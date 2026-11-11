"""Vision description for the Madden spike agent (MADDEN_SPIKE_AGENT_PLAN section 2.7).

Turns one screenshot into a small text description so a spike session with no image input of
its own can reason about the screen. Two backends are supported:

* ``ollama:<tag>`` - a local Ollama model, asked through ``/api/generate`` with one ``images``
  entry. This is the live default (small model, runs beside Madden).
* ``deepseek:<model>`` - DeepSeek's OpenAI-compatible chat endpoint, sent an ``image_url``
  data-URI part. It is the fallback, and S-A confirmed the API accepts images.

Choices and overrides, in order:

* ``MU_SPIKE_VISION`` picks the backend (``ollama:qwen3.5:4b``).
* otherwise the ``live`` pick in ``data/spikes/vision_choice.json`` (written by
  ``tools.madden_pilot.vision_bench``).
* if that file is missing or unreadable the module falls back to ``ollama:qwen3.5:4b``, the
  provisional S-A live pick; a machine without that model simply gets ``VisionUnavailable``.
* ``MU_SPIKE_VISION_HEAVY`` is read by ``heavy_backend()`` for ``spike-analyze`` (U2), which
  runs a bigger model after Madden closes.
* ``MU_SPIKE_VISION_FALLBACK`` defaults to ``deepseek:deepseek-flash`` when
  ``vision_choice.json`` records ``deepseek_images: true`` and to ``none`` otherwise. It is
  tried once when the primary raises ``VisionUnavailable`` or runs past its timeout.

S-A findings this file honours: the long JSON-key prompt (and Ollama's ``format: "json"``)
makes ``qwen3.5:4b`` answer with empty strings, so the prompt here is compact and tolerant
parsing is used instead of a JSON grammar. Vision never authorises a key: the guard's
``border_highlight`` reader remains the deciding reader.

``describe`` returns a ``ScreenDescription`` for any answer it can read; a malformed answer
becomes ``kind="other"`` with the raw text in ``summary`` and never raises. It raises
``VisionUnavailable`` only when the backend cannot be reached (or the image cannot be read).
"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCREEN_KINDS = ("menu", "dialog", "hub", "gameplay", "replay_or_cutscene", "loading",
                "pause_menu", "postgame", "other")

ENV_VISION = "MU_SPIKE_VISION"
ENV_VISION_HEAVY = "MU_SPIKE_VISION_HEAVY"
ENV_VISION_FALLBACK = "MU_SPIKE_VISION_FALLBACK"
ENV_VISION_CHOICE = "MU_SPIKE_VISION_CHOICE"       # test/ops override for the choice file

PROVIDERS = ("ollama", "deepseek")
MAX_WIDTH = 1280                                    # section 2.7: enough for menu text, fast
DEFAULT_LIVE = "ollama:qwen3.5:4b"                  # S-A provisional pick, used only if no file
DEFAULT_FALLBACK = "deepseek:deepseek-flash"

ROOT = Path(__file__).resolve().parents[2]
VISION_CHOICE = ROOT / "data" / "spikes" / "vision_choice.json"

# Compact on purpose: S-A found the long JSON-key prompt (and Ollama format:"json") made
# qwen3.5:4b return empty strings on many frames.
PROMPT = (
    "Describe this Madden NFL screen. Reply with one compact JSON object and no markdown:\n"
    '{"kind":"menu|dialog|hub|gameplay|replay_or_cutscene|loading|pause_menu|postgame|other",'
    '"title":string|null,"options":[string],"selected":string|null,'
    '"overlays":[string],"warning":string|null,"summary":string}'
)

class VisionUnavailable(RuntimeError):
    """The vision backend could not be reached, refused the image, or timed out."""


@dataclass(frozen=True)
class ScreenDescription:
    kind: str            # one of SCREEN_KINDS
    title: str | None    # the heading as the model reads it
    options: tuple[str, ...]   # selectable items, in on-screen order
    selected: str | None       # the item the model sees as highlighted
    overlays: tuple[str, ...]  # e.g. "scorebug bottom-center", "network watermark top-right"
    warning: str | None        # anything about delete/online/purchase/sign-in on screen
    summary: str               # one sentence
    model: str
    seconds: float


# --------------------------------------------------------------------------- configuration
def choice_path(path: str | Path | None = None) -> Path:
    """Where the model pick lives: the override env var, else ``data/spikes/vision_choice.json``."""
    return Path(path or os.environ.get(ENV_VISION_CHOICE) or VISION_CHOICE)


def read_vision_choice(path: str | Path | None = None) -> dict:
    """The parsed choice file (``{}`` when it is missing, unreadable or not an object)."""
    target = choice_path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def default_backend() -> str:
    """The live backend: ``MU_SPIKE_VISION``, else the choice file's ``live``, else qwen3.5:4b."""
    env = os.environ.get(ENV_VISION, "").strip()
    if env:
        return env
    live = str(read_vision_choice().get("live") or "").strip()
    return live or DEFAULT_LIVE


def heavy_backend() -> str:
    """The heavier backend ``spike-analyze`` uses after Madden closes."""
    env = os.environ.get(ENV_VISION_HEAVY, "").strip()
    if env:
        return env
    heavy = str(read_vision_choice().get("heavy") or "").strip()
    return heavy or default_backend()


def fallback_backend() -> str:
    """The one-shot fallback: env override, else DeepSeek only when the file says images work."""
    env = os.environ.get(ENV_VISION_FALLBACK, "").strip()
    if env:
        return env
    choice = read_vision_choice()
    if choice.get("deepseek_images") is True:
        return str(choice.get("fallback") or DEFAULT_FALLBACK).strip() or DEFAULT_FALLBACK
    return "none"


def parse_backend(spec: str | None) -> tuple[str, str]:
    """Split ``ollama:<tag>`` / ``deepseek:<model>`` into (provider, model).

    Ollama tags carry their own colon (``qwen3.5:4b``), so only the first colon separates
    the provider from the model.
    """
    text = (spec or "").strip()
    if ":" not in text:
        raise ValueError(f"vision backend must be 'ollama:<tag>' or 'deepseek:<model>', got {spec!r}")
    provider, model = text.split(":", 1)
    provider = provider.strip().lower()
    model = model.strip()
    if provider not in PROVIDERS:
        raise ValueError(f"unknown vision backend {provider!r} (expected one of {PROVIDERS})")
    if not model:
        raise ValueError(f"vision backend {spec!r} names no model")
    return provider, model


def resolve_backend(backend: str | None = None, model: str | None = None) -> str:
    """A full backend spec, applying a ``model`` override to the resolved provider."""
    spec = (backend or default_backend()).strip()
    if not spec:
        raise ValueError("no vision backend configured")
    if model:
        provider = spec.split(":", 1)[0].strip().lower()
        spec = f"{provider}:{model.strip()}"
    parse_backend(spec)                            # validate early
    return spec


# --------------------------------------------------------------------------- image input
def image_bytes(png: str | Path, width: int = MAX_WIDTH) -> bytes:
    """PNG bytes of the frame, downscaled to at most ``width`` px wide (section 2.7)."""
    from PIL import Image

    try:
        with Image.open(png) as opened:
            img = opened.convert("RGB")
    except (OSError, ValueError) as exc:
        raise VisionUnavailable(f"cannot read frame {png!s}: {exc}") from exc
    if img.width > width:
        height = max(1, round(img.height * width / img.width))
        resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
        img = img.resize((width, height), resample)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def image_data_uri(png: str | Path, width: int = MAX_WIDTH) -> str:
    """The frame as a ``data:image/png;base64,...`` URI (used by the DeepSeek request)."""
    return "data:image/png;base64," + base64.b64encode(image_bytes(png, width)).decode("ascii")


# --------------------------------------------------------------------------- backend calls
def _ollama_text(client: Any, model: str, prompt: str, b64: str, timeout: float) -> str:
    if client is not None:
        generate = getattr(client, "generate", None)
        if generate is not None:
            return generate(model=model, prompt=prompt, images=[b64], timeout=timeout)
        if callable(client):
            return client(model, prompt, b64)
        raise TypeError("ollama client has no generate()")
    import requests

    from engines.llm_provider import get_ollama_base_url

    url = get_ollama_base_url().rstrip("/") + "/api/generate"
    # No format:"json": S-A showed it breaks qwen3.5:4b's answers.
    resp = requests.post(url, json={"model": model, "prompt": prompt, "images": [b64],
                                    "stream": False, "options": {"temperature": 0}},
                         timeout=timeout)
    resp.raise_for_status()
    return str(resp.json().get("response") or "")


def _deepseek_content(message: Any) -> str:
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", None)
    if isinstance(content, list):
        return " ".join(str(part.get("text", "") if isinstance(part, dict) else part)
                        for part in content).strip()
    return str(content or "")


def _deepseek_text(client: Any, model: str, prompt: str, b64: str, timeout: float) -> str:
    if client is None:
        from newsroom.tool_loop.openai_loop import build_default_client
        client = build_default_client("deepseek", max_retries=0)
    chat = getattr(client, "chat", None)
    if chat is None:
        raise TypeError("deepseek client has no chat.completions")
    response = chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url",
             "image_url": {"url": "data:image/png;base64," + b64}},
        ]}],
        temperature=0,
        timeout=timeout,
    )
    choices = response.get("choices") if isinstance(response, dict) else getattr(response, "choices", None)
    if not choices:
        return ""
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else getattr(first, "message", None)
    return _deepseek_content(message)


def _answer(spec: str, prompt: str, b64: str, timeout: float, client: Any) -> tuple[str, float]:
    """Call one backend once. Any transport/client failure becomes ``VisionUnavailable``."""
    provider, model = parse_backend(spec)
    start = time.monotonic()
    try:
        if provider == "ollama":
            text = _ollama_text(client, model, prompt, b64, timeout)
        else:
            text = _deepseek_text(client, model, prompt, b64, timeout)
    except VisionUnavailable:
        raise
    except Exception as exc:  # requests/httpx/openai/OS errors all mean "could not reach it"
        raise VisionUnavailable(f"{spec} unavailable: {type(exc).__name__}: {exc}") from exc
    seconds = time.monotonic() - start
    if seconds > timeout:
        raise VisionUnavailable(f"{spec} timed out after {seconds:.1f}s (limit {timeout:g}s)")
    return str(text or ""), seconds


def request_text(png: str | Path, *, backend: str | None = None, model: str | None = None,
                 prompt: str = PROMPT, timeout: float = 30.0,
                 client: Any = None) -> tuple[str, float, str]:
    """Ask one backend one prompt about a frame.

    Returns (answer, seconds, backend spec). Used by ``describe`` and by ``vision_bench``'s
    targeted scorebug-layout prompt. Raises ``VisionUnavailable`` when the backend fails.
    ``client`` is a test seam: an object with ``generate`` (Ollama) and/or ``chat.completions``
    (DeepSeek); production passes None and the module builds the real call.
    """
    spec = resolve_backend(backend, model)
    b64 = base64.b64encode(image_bytes(png)).decode("ascii")
    text, seconds = _answer(spec, prompt, b64, timeout, client)
    return text, seconds, spec


# --------------------------------------------------------------------------- answer parsing
def extract_json(text: str) -> Any:
    """The first JSON object/array in an answer, tolerating code fences and prose; else None."""
    raw = (text or "").strip()
    if not raw:
        return None
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.IGNORECASE | re.DOTALL)
    if fence:
        raw = fence.group(1).strip()
    try:
        return json.loads(raw)
    except ValueError:
        pass
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = raw.find(opener), raw.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(raw[start:end + 1])
            except ValueError:
                continue
    return None


def _string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(text for text in (str(item).strip() for item in value) if text)
    text = str(value).strip()
    return (text,) if text else ()


def _coerce_kind(value: Any) -> str:
    kind = _string(value)
    kind = kind.lower() if kind else ""
    return kind if kind in SCREEN_KINDS else "other"


def parse_answer(text: str, model: str, seconds: float) -> ScreenDescription:
    """Build a ``ScreenDescription``; a malformed answer goes to kind "other", never raises."""
    obj = extract_json(text)
    if not isinstance(obj, dict):
        raw = (text or "").strip() or "(empty answer)"
        return ScreenDescription("other", None, (), None, (), None, raw, model, seconds)
    return ScreenDescription(
        kind=_coerce_kind(obj.get("kind")),
        title=_string(obj.get("title")),
        options=_strings(obj.get("options")),
        selected=_string(obj.get("selected")),
        overlays=_strings(obj.get("overlays")),
        warning=_string(obj.get("warning")),
        summary=_string(obj.get("summary")) or "",
        model=model,
        seconds=seconds,
    )


# --------------------------------------------------------------------------- public entry
def describe(png: str | Path, *, backend: str | None = None, model: str | None = None,
             timeout: float = 30.0, client: Any = None) -> ScreenDescription:
    """Describe one frame with the live backend, falling back once as configured (section 2.7).

    ``model`` overrides the model part of the chosen backend. The returned ``model`` field
    names the backend that answered (for example ``deepseek:deepseek-flash``).
    """
    spec = resolve_backend(backend, model)
    b64 = base64.b64encode(image_bytes(png)).decode("ascii")
    try:
        text, seconds = _answer(spec, PROMPT, b64, timeout, client)
    except VisionUnavailable:
        fallback = fallback_backend()
        if not fallback or fallback.strip().lower() == "none" or fallback.strip() == spec:
            raise
        fb_spec = resolve_backend(fallback)
        text, seconds = _answer(fb_spec, PROMPT, b64, timeout, client)
        spec = fb_spec
    return parse_answer(text, spec, seconds)
