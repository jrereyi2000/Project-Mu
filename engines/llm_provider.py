"""
LLM Provider Seam for Gemini and Local Ollama Models.
(docs/done/PRE_PC_WORK.md §6, docs/in_progress/PC_MIGRATION_PLAN.md §7.3)

Provides a unified interface for short LLM tasks, allowing execution via either
Google Gemini (via the google-genai SDK) or local Ollama (via HTTP API), with
automatic fallback to Gemini on connection error or timeout.

Usage:
  from engines.llm_provider import generate_text, ProviderText, resolve_provider

  # One-off text generation with automatic provider selection and fallback:
  text = generate_text("Summarize season", source="agent_memory", label="summary @handle")

  # Callable prompt -> text with .flush() for GM agents:
  llm = ProviderText(label="gm_rationale", model="ollama:qwen2.5:14b")
  rationale = llm(prompt)
  llm.flush()
"""
import os
import socket
from typing import Any, Optional, Tuple, Union

import requests

from db.llm_usage import log_response, log_usage, normalize_model_name, UsageMeter
from engines.llm_config import DEFAULT_MODEL, make_content_config, resolve_thinking_level

DEFAULT_GEMINI_MODEL = DEFAULT_MODEL  # "gemini-3.8-flash"
DEFAULT_OLLAMA_MODEL = "qwen2.5:14b"
DEFAULT_FALLBACK_MODEL = "gemini-3.8-flash:low"
DEFAULT_OLLAMA_HOST = "localhost"
DEFAULT_OLLAMA_PORT = 11434
DEFAULT_OLLAMA_TIMEOUT = 60.0

OLLAMA_FALLBACK_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ReadTimeout,
    ConnectionRefusedError,
    socket.timeout,
    TimeoutError,
)


def get_ollama_base_url() -> str:
    """Return the normalized base URL for the Ollama HTTP API (e.g. 'http://localhost:11434')."""
    raw = os.environ.get("OLLAMA_HOST")
    if not raw:
        try:
            from pipeline.notify import env
            raw = env("OLLAMA_HOST")
        except Exception:
            raw = None
    if not raw:
        return f"http://{DEFAULT_OLLAMA_HOST}:{DEFAULT_OLLAMA_PORT}"

    raw = raw.strip()
    if raw.startswith(("http://", "https://")):
        scheme, rest = raw.split("://", 1)
        if ":" not in rest:
            return f"{scheme}://{rest}:{DEFAULT_OLLAMA_PORT}"
        return raw

    if ":" in raw:
        return f"http://{raw}"
    return f"http://{raw}:{DEFAULT_OLLAMA_PORT}"


def resolve_provider(
    label: str = "",
    source: str = "",
    model: Optional[str] = None,
    provider: Optional[str] = None,
) -> str:
    """Resolve whether to use 'gemini' or 'ollama'.

    Priority:
    1. Explicit provider parameter ('gemini' or 'ollama')
    2. Model prefix ('ollama:<model>' / 'ollama/<model>' vs 'gemini:<model>' / 'gemini/<model>')
    3. Per-source env var: LLM_PROVIDER_<SOURCE> (e.g. LLM_PROVIDER_AGENT_MEMORY=ollama)
    4. Per-label env var: LLM_PROVIDER_<LABEL> (e.g. LLM_PROVIDER_AGENT_WIRE=ollama)
    5. Global env var: LLM_PROVIDER (e.g. LLM_PROVIDER=ollama)
    6. Default: 'gemini'
    """
    if provider:
        p = provider.strip().lower()
        if p in ("gemini", "ollama"):
            return p

    if model:
        m = model.strip().lower()
        if m.startswith(("ollama:", "ollama/")):
            return "ollama"
        if m.startswith(("gemini:", "gemini/")):
            return "gemini"

    for candidate in (source, label):
        if candidate:
            cand_clean = candidate.strip().lower().replace(" ", "_")
            key = f"LLM_PROVIDER_{cand_clean.upper()}"
            if key in os.environ:
                val = os.environ[key].strip().lower()
                if val in ("gemini", "ollama"):
                    return val

    if "LLM_PROVIDER" in os.environ:
        val = os.environ["LLM_PROVIDER"].strip().lower()
        if val in ("gemini", "ollama"):
            return val

    return "gemini"


def resolve_model(
    provider: str,
    label: str = "",
    source: str = "",
    model: Optional[str] = None,
) -> str:
    """Resolve the clean model name for the given provider."""
    prov = provider.strip().lower()
    clean = model
    if clean:
        clean = clean.strip()
        if clean.startswith(("ollama:", "ollama/")):
            clean = clean[7:]
        elif clean.startswith(("gemini:", "gemini/")):
            clean = clean[7:]

    if prov == "ollama":
        if clean and clean != DEFAULT_GEMINI_MODEL:
            return clean
        for candidate in (source, label):
            if candidate:
                cand_clean = candidate.strip().lower().replace(" ", "_")
                key = f"OLLAMA_MODEL_{cand_clean.upper()}"
                if key in os.environ:
                    return os.environ[key].strip()
        return os.environ.get("OLLAMA_MODEL") or DEFAULT_OLLAMA_MODEL

    # gemini
    if clean:
        return clean
    return DEFAULT_GEMINI_MODEL


def resolve_fallback_model(
    label: str = "",
    source: str = "",
    fallback_model: Optional[str] = None,
) -> str:
    """Resolve the Gemini fallback model to use when Ollama is unreachable/times out."""
    if fallback_model:
        return fallback_model.strip()
    for candidate in (source, label):
        if candidate:
            cand_clean = candidate.strip().lower().replace(" ", "_")
            key = f"LLM_FALLBACK_MODEL_{cand_clean.upper()}"
            if key in os.environ:
                return os.environ[key].strip()
    return os.environ.get("LLM_FALLBACK_MODEL") or DEFAULT_FALLBACK_MODEL


def call_ollama(
    model: str,
    prompt: Optional[str] = None,
    system: Optional[str] = None,
    messages: Optional[list] = None,
    temperature: Optional[float] = None,
    response_schema: Any = None,
    response_mime_type: Optional[str] = None,
    timeout: Optional[float] = None,
    base_url: Optional[str] = None,
    think: Optional[bool] = None,
    num_ctx: Optional[int] = None,
) -> Tuple[str, dict]:
    """Call Ollama API (/api/generate or /api/chat) without streaming.

    think: True/False turns a thinking model's reasoning on or off (Ollama's `think` field);
    None leaves the model's default (gemma4 thinks by default). num_ctx overrides Ollama's
    4,096-token default context, which silently truncates longer prompts.

    Returns (text, usage_dict) where usage_dict contains:
      input_tokens (prompt_eval_count), output_tokens (eval_count minus thinking),
      cached_tokens (0), thinking_tokens (estimated: Ollama counts reasoning inside
      eval_count and returns it as text only, so eval_count is split by character share).
    Raises requests connection/timeout exceptions on network failure.
    """
    url_base = base_url or get_ollama_base_url()
    t_out = timeout if timeout is not None else float(os.environ.get("OLLAMA_TIMEOUT", DEFAULT_OLLAMA_TIMEOUT))

    options: dict[str, Any] = {}
    if temperature is not None:
        options["temperature"] = temperature
    if num_ctx:
        options["num_ctx"] = int(num_ctx)

    fmt = None
    if response_schema is not None:
        if isinstance(response_schema, dict):
            fmt = response_schema
        elif hasattr(response_schema, "model_json_schema"):
            fmt = response_schema.model_json_schema()
        elif hasattr(response_schema, "schema"):
            fmt = response_schema.schema()
    elif response_mime_type == "application/json":
        fmt = "json"

    if messages is not None:
        endpoint = f"{url_base}/api/chat"
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if options:
            payload["options"] = options
        if fmt is not None:
            payload["format"] = fmt
        if think is not None:
            payload["think"] = bool(think)
        resp = requests.post(endpoint, json=payload, timeout=t_out)
        resp.raise_for_status()
        data = resp.json()
        text = data.get("message", {}).get("content", "")
        thinking_text = data.get("message", {}).get("thinking") or ""
    else:
        endpoint = f"{url_base}/api/generate"
        payload = {
            "model": model,
            "prompt": prompt or "",
            "stream": False,
        }
        if system:
            payload["system"] = system
        if options:
            payload["options"] = options
        if fmt is not None:
            payload["format"] = fmt
        if think is not None:
            payload["think"] = bool(think)
        resp = requests.post(endpoint, json=payload, timeout=t_out)
        resp.raise_for_status()
        data = resp.json()
        text = data.get("response", "")
        thinking_text = data.get("thinking") or ""

    input_tokens = int(data.get("prompt_eval_count") or 0)
    eval_count = int(data.get("eval_count") or 0)
    thinking_tokens = int(data.get("thinking_tokens") or 0)
    if not thinking_tokens and thinking_text:
        # Split eval_count between reasoning and answer by their share of the generated characters
        thinking_tokens = round(eval_count * len(thinking_text) / (len(thinking_text) + len(text or "")))
    output_tokens = max(0, eval_count - thinking_tokens)

    usage = {
        "input_tokens": input_tokens,
        "cached_tokens": 0,
        "output_tokens": output_tokens,
        "thinking_tokens": thinking_tokens,
    }
    return (text or "").strip(), usage


def call_gemini(
    prompt: Union[str, list],
    model: str,
    label: str = "",
    system: Optional[str] = None,
    temperature: Optional[float] = None,
    thinking_level: Optional[str] = None,
    response_schema: Any = None,
    response_mime_type: Optional[str] = None,
    client: Any = None,
    api_key: Optional[str] = None,
) -> Tuple[str, dict, Any]:
    """Call Google Gemini client with make_content_config. Returns (text, usage_dict, resp)."""
    from google import genai
    from db.llm_usage import _usage

    resolved_thinking = resolve_thinking_level(
        label=label,
        model=model,
        thinking_level=thinking_level,
        default="low",
    )
    config, clean_model = make_content_config(
        model=model,
        thinking_level=resolved_thinking,
        temperature=temperature,
        system_instruction=system,
        response_schema=response_schema,
        response_mime_type=response_mime_type,
    )

    c = client
    if c is None:
        key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not key:
            try:
                from pipeline.notify import env
                key = env("GEMINI_API_KEY")
            except Exception:
                key = None
        c = genai.Client(api_key=key)

    resp = c.models.generate_content(model=clean_model, contents=prompt, config=config)
    usage = _usage(resp)
    return (resp.text or "").strip(), usage, resp


def generate_text(
    prompt: Union[str, list],
    source: str = "llm_call",
    label: str = "",
    model: Optional[str] = None,
    provider: Optional[str] = None,
    fallback_model: Optional[str] = None,
    thinking_level: Optional[str] = None,
    temperature: Optional[float] = None,
    system: Optional[str] = None,
    response_schema: Any = None,
    response_mime_type: Optional[str] = None,
    client: Any = None,
    api_key: Optional[str] = None,
    return_usage: bool = False,
    allow_fallback: bool = True,
    log_tokens: bool = True,
    ollama_options: Optional[dict] = None,
) -> Union[str, Tuple[str, dict]]:
    """Provider-switching text generator.

    Routes to Ollama or Gemini, with automatic fallback to Gemini on Ollama connection error
    or timeout. Logs token usage for both providers to llm_usage.
    If return_usage=True, returns (text, usage_dict).
    allow_fallback=False re-raises Ollama errors instead of answering with Gemini, and
    log_tokens=False skips the llm_usage write (tools/llm_compare.py needs both: a quiet
    fallback would score Gemini's answer as the local model's, and the archive DB is read-only).
    ollama_options passes think / num_ctx / timeout through to call_ollama.
    """
    effective_label = label or source
    prov = resolve_provider(label=effective_label, source=source, model=model, provider=provider)

    if prov == "ollama":
        ollama_model = resolve_model("ollama", label=effective_label, source=source, model=model)
        try:
            if isinstance(prompt, list):
                messages = []
                if system:
                    messages.append({"role": "system", "content": system})
                for item in prompt:
                    if isinstance(item, dict):
                        messages.append(item)
                    elif hasattr(item, "role") and hasattr(item, "parts"):
                        # Gemini Content turn
                        text_part = "".join(getattr(p, "text", "") for p in item.parts)
                        role = "assistant" if item.role in ("model", "assistant") else "user"
                        messages.append({"role": role, "content": text_part})
                    else:
                        messages.append({"role": "user", "content": str(item)})
            text, usage = call_ollama(
                model=ollama_model,
                messages=messages if isinstance(prompt, list) else None,
                prompt=prompt if not isinstance(prompt, list) else None,
                system=system if not isinstance(prompt, list) else None,
                temperature=temperature,
                response_schema=response_schema,
                response_mime_type=response_mime_type,
                **(ollama_options or {}),
            )
            # Log Ollama usage
            if log_tokens:
                log_usage(
                    source=source,
                    label=effective_label,
                    model=f"ollama/{ollama_model}",
                    input_tokens=usage["input_tokens"],
                    cached_tokens=0,
                    output_tokens=usage["output_tokens"],
                    thinking_tokens=usage["thinking_tokens"],
                )
            if return_usage:
                return text, usage
            return text
        except OLLAMA_FALLBACK_EXCEPTIONS as e:
            if not allow_fallback:
                raise
            fb = resolve_fallback_model(label=effective_label, source=source, fallback_model=fallback_model)
            print(
                f"[llm_provider] Ollama ({ollama_model}) unreachable or timed out ({type(e).__name__}: {e}). "
                f"Falling back to Gemini ({fb}).",
                flush=True,
            )
            # Fall through to Gemini execution with fallback_model
            gemini_model = fb
    else:
        gemini_model = resolve_model("gemini", label=effective_label, source=source, model=model)

    # Gemini execution
    text, usage, resp = call_gemini(
        prompt=prompt,
        model=gemini_model,
        label=effective_label,
        system=system,
        temperature=temperature,
        thinking_level=thinking_level,
        response_schema=response_schema,
        response_mime_type=response_mime_type,
        client=client,
        api_key=api_key,
    )
    # Log Gemini usage
    if log_tokens:
        log_response(source=source, label=effective_label, model=gemini_model, resp=resp)
    if return_usage:
        return text, usage
    return text


def estimate_cost(model: str, usage: dict) -> float:
    """Calculate estimated USD cost for a model call. Local models (ollama) are $0.00."""
    if not model:
        return 0.0
    norm = normalize_model_name(model).lower()
    if norm.startswith("ollama/") or norm.startswith("ollama:"):
        return 0.0
    prices = {
        "gemini-3.8-flash": (0.15, 0.60),
        "gemini-3.5-flash-lite": (0.075, 0.30),
        "gemini-2.5-pro": (1.25, 5.00),
        "gemini-2.5-flash": (0.15, 0.60),
    }
    pin, pout = (0.15, 0.60)
    for k, v in prices.items():
        if k in norm:
            pin, pout = v
            break
    inp = usage.get("input_tokens") or usage.get("input") or 0
    out = usage.get("output_tokens") or usage.get("output") or 0
    think = usage.get("thinking_tokens") or usage.get("thinking") or 0
    return (inp * pin + (out + think) * pout) / 1_000_000.0


class ProviderText:
    """Callable prompt -> text that supports 'gemini' and 'ollama' with fallback and .flush().

    Drop-in provider-neutral counterpart to GeminiText for GM agents and short tasks.
    """

    def __init__(
        self,
        label: str,
        source: Optional[str] = None,
        model: Optional[str] = None,
        provider: Optional[str] = None,
        fallback_model: Optional[str] = None,
        temperature: float = 0.7,
        thinking_level: Optional[str] = None,
        client: Any = None,
        api_key: Optional[str] = None,
    ):
        self.label = label
        self.source = source or label
        self.provider = resolve_provider(label=label, source=self.source, model=model, provider=provider)
        self.model = resolve_model(self.provider, label=label, source=self.source, model=model)
        self.fallback_model = resolve_fallback_model(label=label, source=self.source, fallback_model=fallback_model)
        self.temperature = temperature
        self.thinking_level = thinking_level
        self.client = client
        self.api_key = api_key
        self.calls: list[dict] = []

        # If pure Gemini, initialize GeminiText / UsageMeter for exact behavior
        self._gemini_text = None
        if self.provider == "gemini":
            from engines.gm_llm import GeminiText
            self._gemini_text = GeminiText(
                key=api_key,
                label=label,
                model=self.model,
                temperature=temperature,
                thinking_level=thinking_level,
                client=client,
            )

    def __call__(self, prompt: str) -> str:
        if self._gemini_text is not None:
            return self._gemini_text(prompt)

        # Ollama execution with fallback
        try:
            text, usage = call_ollama(
                model=self.model,
                prompt=prompt,
                temperature=self.temperature,
            )
            call_row = {
                "source": self.source,
                "label": self.label,
                "model": f"ollama/{self.model}",
                "input_tokens": usage["input_tokens"],
                "cached_tokens": 0,
                "output_tokens": usage["output_tokens"],
                "thinking_tokens": usage["thinking_tokens"],
            }
            self.calls.append(call_row)
            log_usage(
                source=self.source,
                label=self.label,
                model=f"ollama/{self.model}",
                input_tokens=usage["input_tokens"],
                cached_tokens=0,
                output_tokens=usage["output_tokens"],
                thinking_tokens=usage["thinking_tokens"],
            )
            return text
        except OLLAMA_FALLBACK_EXCEPTIONS as e:
            print(
                f"[llm_provider] Ollama ({self.model}) unreachable or timed out ({type(e).__name__}: {e}). "
                f"Falling back to Gemini ({self.fallback_model}).",
                flush=True,
            )
            # Lazily build fallback GeminiText if needed
            from engines.gm_llm import GeminiText
            fallback_gt = GeminiText(
                key=self.api_key,
                label=self.label,
                model=self.fallback_model,
                temperature=self.temperature,
                thinking_level=self.thinking_level,
                client=self.client,
            )
            res = fallback_gt(prompt)
            # Record fallback call from its meter
            calls = fallback_gt.meter.take()
            self.calls.extend(calls)
            for c in calls:
                log_usage(
                    source=self.source,
                    label=self.label,
                    model=c.get("model") or self.fallback_model,
                    input_tokens=c["input_tokens"],
                    cached_tokens=c["cached_tokens"],
                    output_tokens=c["output_tokens"],
                    thinking_tokens=c["thinking_tokens"],
                )
            return res

    def flush(self):
        if self._gemini_text is not None:
            return self._gemini_text.flush()
        calls = list(self.calls)
        self.calls.clear()
        keys = ("input_tokens", "cached_tokens", "output_tokens", "thinking_tokens")
        return {k: sum(c.get(k, 0) for c in calls) for k in keys} | {"calls": len(calls)}
