"""
LLM Provider Seam for Gemini, OpenCode Go and Local Ollama Models.
(docs/done/PRE_PC_WORK.md §6, docs/done/PC_MIGRATION_PLAN.md §7.3, GLM_5_3_UNIFICATION_PLAN §2.1)

Provides a unified interface for short LLM tasks, allowing execution via
Google Gemini (via the google-genai SDK), OpenCode Go (the same SDK pointed at Go's
Google-compatible endpoint, `newsroom/tool_loop/go_transport.py`) or local Ollama (via HTTP
API), with automatic fallback to Gemini on an Ollama connection error or timeout. Go never
falls back to Gemini: without `OPENCODE_GO_API_KEY` it raises.

Usage:
  from engines.llm_provider import generate_text, ProviderText, resolve_provider

  # One-off text generation with automatic provider selection and fallback:
  text = generate_text("Summarize season", source="agent_memory", label="summary @handle")

  # A Go model, by provider or by model prefix (no default changes: Gemini stays the default):
  text = generate_text("Grade this line", source="press", provider="go")
  text = generate_text("Grade this line", source="press", model="go:glm-5.3-flash")

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

# The text model an OpenCode Go call uses when nobody names one. MU_GO_TEXT_MODEL overrides it
# (read at call time too, so a run or a test can set it after import).
GO_MODEL_FALLBACK = "glm-5.3-flash"


def default_go_model() -> str:
    """The Go text model to use when no model is named."""
    return os.environ.get("MU_GO_TEXT_MODEL") or GO_MODEL_FALLBACK


DEFAULT_GO_MODEL = default_go_model()

OLLAMA_FALLBACK_EXCEPTIONS = (
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    requests.exceptions.ConnectTimeout,
    requests.exceptions.ReadTimeout,
    ConnectionRefusedError,
    socket.timeout,
    TimeoutError,
)

# A tuning ingest must never spend API budget (CLOSE_OUT_CHECKLIST P2-07). watch_save sets this for a
# madden_tune_* destination; an operator can also set it by hand for any local run.
NO_LLM_ENV = "MU_NO_LLM"


class LLMDisabled(RuntimeError):
    """MU_NO_LLM is on: the run must not reach any model, local or paid."""


def llm_disabled() -> bool:
    """True when MU_NO_LLM asks every LLM path to fall back to templates / refuse the call."""
    return os.environ.get(NO_LLM_ENV, "").strip().lower() in ("1", "true", "yes", "on")


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
    """Resolve whether to use 'gemini', 'ollama' or 'go'.

    Priority:
    1. Explicit provider parameter ('gemini', 'ollama' or 'go')
    2. Model prefix ('ollama:<model>' / 'ollama/<model>', 'gemini:<model>' / 'gemini/<model>',
       'go:<model>' / 'go/<model>')
    3. Per-source env var: LLM_PROVIDER_<SOURCE> (e.g. LLM_PROVIDER_AGENT_MEMORY=ollama)
    4. Per-label env var: LLM_PROVIDER_<LABEL> (e.g. LLM_PROVIDER_AGENT_WIRE=ollama)
    5. Global env var: LLM_PROVIDER (e.g. LLM_PROVIDER=ollama)
    6. Default: 'gemini'

    An unknown provider name or env value is ignored, as it always was.
    """
    if provider:
        p = provider.strip().lower()
        if p in ("gemini", "ollama", "go"):
            return p

    if model:
        m = model.strip().lower()
        if m.startswith(("ollama:", "ollama/")):
            return "ollama"
        if m.startswith(("gemini:", "gemini/")):
            return "gemini"
        if m.startswith(("go:", "go/")):
            return "go"

    for candidate in (source, label):
        if candidate:
            cand_clean = candidate.strip().lower().replace(" ", "_")
            key = f"LLM_PROVIDER_{cand_clean.upper()}"
            if key in os.environ:
                val = os.environ[key].strip().lower()
                if val in ("gemini", "ollama", "go"):
                    return val

    if "LLM_PROVIDER" in os.environ:
        val = os.environ["LLM_PROVIDER"].strip().lower()
        if val in ("gemini", "ollama", "go"):
            return val

    # A bare id OpenCode Go serves (`glm-5.3-flash`, `muse-spark-1.3-contributor`, …) is a Go call, never a
    # Gemini one: sending it to Google's endpoint answers 404 (AUDIT_QUEUE_INVENTORY.md R11). Non-Go ids keep
    # their old answer below, and an Ollama tag (`qwen2.5:14b`) is left to its own provider.
    if model and ":" not in model.strip():
        from newsroom.tool_loop.go_transport import is_go_model
        if is_go_model(model):
            return "go"

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
        elif clean.startswith(("go:", "go/")):
            clean = clean[3:]

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

    if prov == "go":
        # A Gemini id left over from the caller's default is not a Go model: use the Go default.
        if clean and clean != DEFAULT_GEMINI_MODEL:
            return clean
        return default_go_model()

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


# One Go client per process: the transport is keyed by OPENCODE_GO_API_KEY, so every GoText and
# every generate_text(provider="go") can share it and its prompt-cache routing.
_GO_CLIENT: Any = None


def get_go_client() -> Any:
    """The process's OpenCode Go client, built once by `go_transport.go_client()`.

    The client is authenticated by `OPENCODE_GO_API_KEY` alone (AUDIT R6 F1): a caller's `api_key` is never
    forwarded here, so a Gemini key can neither authenticate a Go call nor be cached as the Go client's
    credential for the rest of the process. Raises the transport's `ValueError`
    (`OPENCODE_GO_API_KEY is not set`) before any client is built or any network call is made: a Go call never
    falls back to a Gemini key or a Gemini answer.
    """
    global _GO_CLIENT
    if _GO_CLIENT is None:
        from newsroom.tool_loop.go_transport import go_client
        _GO_CLIENT = go_client()
    return _GO_CLIENT


def call_go(
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
    """Call an OpenCode Go model through the Google-compatible transport.

    Same shape as `call_gemini`: returns (text, usage_dict, resp). The client is the one
    `go_transport.go_client()` builds, unless a caller injects one (tests, comparisons).
    `api_key` is accepted for signature compatibility and ignored: a Go call is authenticated by
    `OPENCODE_GO_API_KEY` only, so a forwarded Gemini key can never reach Go (AUDIT R6 F1).
    """
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

    c = client if client is not None else get_go_client()
    resp = c.models.generate_content(model=clean_model, contents=prompt, config=config)
    usage = _usage(resp)
    return (resp.text or "").strip(), usage, resp


class GoText:
    """prompt -> text through one OpenCode Go client, every call counted by a UsageMeter.

    The Go sibling of `engines.gm_llm.GeminiText`, with the same `.model`, `.label`, callable,
    `.flush()` and `.meter` interface, so `make_llm`, press and the inbox need no signature change.
    The client is the shared per-process one from `newsroom/tool_loop/go_transport.go_client()`, authenticated by
    `OPENCODE_GO_API_KEY` only (AUDIT R6 F1: the `key` argument is kept for signature compatibility and ignored,
    so a caller's Gemini key never becomes the Go client's credential); a missing key raises its `ValueError`
    instead of falling back to Gemini.
    """

    def __init__(self, key=None, label="", model=None, temperature=0.7, thinking_level=None, client=None):
        self.label = label
        self.model = model or default_go_model()
        self.temperature = temperature
        self.thinking_level = resolve_thinking_level(
            label=label,
            model=self.model,
            thinking_level=thinking_level,
            default="low",
        )
        self.config, self.model_name = make_content_config(
            model=self.model,
            thinking_level=self.thinking_level,
            temperature=temperature,
        )
        self.client = client if client is not None else get_go_client()
        self.meter = UsageMeter(self.client)

    def __call__(self, prompt):
        with self.meter.labelled(self.label):
            resp = self.client.models.generate_content(
                model=self.model_name, contents=prompt,
                config=self.config)
        return (resp.text or "").strip()

    def flush(self):
        try:
            return self.meter.record(self.label, label=self.label, model=self.model_name)
        except Exception as e:                                   # logging never breaks a run
            print(f"[llm_provider] usage not recorded: {e}", flush=True)
            return None


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

    Routes to Ollama, OpenCode Go or Gemini, with automatic fallback to Gemini on an Ollama
    connection error or timeout. Logs token usage for every provider to llm_usage.
    If return_usage=True, returns (text, usage_dict).
    allow_fallback=False re-raises Ollama errors instead of answering with Gemini, and
    log_tokens=False skips the llm_usage write (tools/llm_compare.py needs both: a quiet
    fallback would score Gemini's answer as the local model's, and the archive DB is read-only).
    ollama_options passes think / num_ctx / timeout through to call_ollama.
    A Go call is never answered by Gemini: without OPENCODE_GO_API_KEY it raises the transport's
    ValueError, whatever allow_fallback says.
    """
    if llm_disabled():
        raise LLMDisabled(f"model calls are disabled by {NO_LLM_ENV}")
    effective_label = label or source
    prov = resolve_provider(label=effective_label, source=source, model=model, provider=provider)

    if prov == "go":
        go_model = resolve_model("go", label=effective_label, source=source, model=model)
        text, usage, _resp = call_go(
            prompt=prompt,
            model=go_model,
            label=effective_label,
            system=system,
            temperature=temperature,
            thinking_level=thinking_level,
            response_schema=response_schema,
            response_mime_type=response_mime_type,
            client=client,
            api_key=api_key,
        )
        if log_tokens:
            log_usage(
                source=source,
                label=effective_label,
                model=go_model,
                input_tokens=usage["input_tokens"],
                cached_tokens=usage["cached_tokens"],
                output_tokens=usage["output_tokens"],
                thinking_tokens=usage["thinking_tokens"],
            )
        if return_usage:
            return text, usage
        return text

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


# List prices in $ per 1M tokens: (input, cached read, output+thinking). One table for every model
# Mu prices, so a Go model is never silently priced at another provider's rate (AUDIT R6 F4 Deferral 2).
# The Go ids and their rates are the same values tools/article_model_compare.py's PRICES carries; that
# tool holds the full 13-model list, this one the models Mu itself calls. A model missing from here is
# UNPRICED — estimate_cost returns None rather than a guess. The four gemini entries are unchanged.
MODEL_PRICES: dict[str, tuple[float, float, float]] = {
    "gemini-3.8-flash": (0.15, 0.15, 0.60),
    "gemini-3.5-flash-lite": (0.075, 0.075, 0.30),
    "gemini-2.5-pro": (1.25, 1.25, 5.00),
    "gemini-2.5-flash": (0.15, 0.15, 0.60),
    "glm-5.3-flash": (0.15, 0.03, 0.50),
    "muse-spark-1.3-contributor": (0.10, 0.002, 0.20),
    "deepseek-v4.1-flash": (0.15, 0.003, 0.60),
}

# The two ways a call can be "not a card charge", and the one way it can be unknown.
ALLOWANCE_NOTE = "Go allowance (not $0 free): the list price of these tokens, not cash"
UNPRICED_NOTE = "unpriced model (no per-token price known)"


def _price_for(norm: str):
    """The (model id, (input, cached, output)) a normalized model id is priced at, longest key first.

    Longest-match, so a longer id is never priced by a shorter key inside it
    (`gemini-3.8-flash-preview` is priced as `gemini-3.8-flash`, never as something else).
    """
    best = None
    for key, price in MODEL_PRICES.items():
        if key in norm and (best is None or len(key) > len(best[0])):
            best = (key, price)
    return best


def cost_estimate(model: str, usage: dict | None) -> dict:
    """The full answer for one call: the dollars, whether they are cash, and why.

    `estimate_cost` returns only the number, which a caller then prints. That number is a **list
    price**: for a Go model the allowance pays and no card is charged, so this dict also carries
    `cash_usd`, `basis` and a plain-language `note` (AUDIT R6 F4):

    - `basis: "cash"`      — a provider bills these tokens at list (Gemini). `cash_usd == usd`.
    - `basis: "allowance"` — a Go model: `usd` is what the tokens would list at, `cash_usd` is 0.0.
    - `basis: "local"`     — ollama; no API cost and no listing.
    - `basis: "unpriced"`  — no known rate: `usd` is None and `note` says so, never another
                             provider's numbers.
    """
    usage = usage or {}
    if not model:
        return {"usd": 0.0, "cash_usd": 0.0, "basis": "unknown", "note": "no model id"}
    norm = normalize_model_name(model).lower()
    if norm.startswith("ollama/") or norm.startswith("ollama:"):
        return {"usd": 0.0, "cash_usd": 0.0, "basis": "local", "note": ""}
    priced = _price_for(norm)
    if priced is None:
        return {"usd": None, "cash_usd": None, "basis": "unpriced", "note": UNPRICED_NOTE}
    _, (pin, pcached, pout) = priced
    inp = int(usage.get("input_tokens") or usage.get("input") or 0)
    cached = min(int(usage.get("cached_tokens") or usage.get("cached") or 0), inp)
    out = int(usage.get("output_tokens") or usage.get("output") or 0)
    think = int(usage.get("thinking_tokens") or usage.get("thinking") or 0)
    usd = ((inp - cached) * pin + cached * pcached + (out + think) * pout) / 1_000_000.0
    from newsroom.tool_loop.go_transport import is_go_model
    if is_go_model(model):
        return {"usd": usd, "cash_usd": 0.0, "basis": "allowance", "note": ALLOWANCE_NOTE}
    return {"usd": usd, "cash_usd": usd, "basis": "cash", "note": ""}


def estimate_cost(model: str, usage: dict) -> float | None:
    """The list price in USD of one call's tokens, or None when no rate is known for the model.

    Local models (ollama) are $0.00. A Go model is priced at its own published rate, never at
    Gemini's fallback (AUDIT R6 F4 Deferral 2); because the Go allowance pays for it, that number is a
    list-price reference and `cash_usd` in `cost_estimate` is the 0.0 a card is actually charged. A
    caller that must tell cash from allowance uses `cost_estimate`; one that prints a cost may print
    None as "unpriced".
    """
    return cost_estimate(model, usage)["usd"]


class ProviderText:
    """Callable prompt -> text that supports 'gemini', 'ollama' and 'go' with fallback and .flush().

    Drop-in provider-neutral counterpart to GeminiText for GM agents and short tasks. A Go call
    goes through `GoText` (OpenCode Go's Google-compatible transport) and never falls back.
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

        # A Go call is a GoText, with the same meter/flush contract (and never a Gemini answer)
        self._go_text = None
        if self.provider == "go":
            self._go_text = GoText(
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

        if self._go_text is not None:
            return self._go_text(prompt)

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
        if self._go_text is not None:
            return self._go_text.flush()
        calls = list(self.calls)
        self.calls.clear()
        keys = ("input_tokens", "cached_tokens", "output_tokens", "thinking_tokens")
        return {k: sum(c.get(k, 0) for c in calls) for k in keys} | {"calls": len(calls)}
