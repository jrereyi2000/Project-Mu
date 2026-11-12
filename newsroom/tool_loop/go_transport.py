"""Google-compatible transport for OpenCode Go.

OpenCode Go serves every text model through a Google-shaped endpoint
(`/inference/go/google/v1beta/models/<model>:generateContent`), translated to and from the
model's native API. That lets the newsroom's existing `google-genai` AFC writer path run a
Go model — `muse-spark-1.3-contributor`, `glm-5.3-flash`, … — by changing the client and the
model id, with no rewrite of the tool loop.

Facts this module encodes (spike 2026-11-12, docs/reference/MODEL_ROUTING_EVALUATION.md §7.3):

- The base URL is `https://opencode.ai/inference/go/google`.
- `x-opencode-session` is **required**; without it Go answers `400 MissingSessionID`.
- Tool schemas survive the translation: twelve models probed all returned a `functionCall`
  with intact arguments.
- Responses carry `usageMetadata.opencode.{allowance, billing_source, list_cost, tokens}`.

Only the client construction and its per-call accounting live here. Nothing in this module calls
a model, imports the SDK at module load, or knows about articles.
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

GO_GOOGLE_BASE_URL = "https://opencode.ai/inference/go/google"
GO_KEY_ENV = "OPENCODE_GO_API_KEY"
SESSION_ENV = "MU_GO_SESSION"

# Model ids that OpenCode Go serves. A Go id is anything the catalogue lists that is not a
# Gemini-family id billed directly by Google; the comparison and any later switch share this rule.
_NON_GO_PREFIXES = ("gemini-", "gemini/", "gemini:")


def _env(name: str) -> str | None:
    """Read an environment variable, then the repo's .env (pipeline.notify.env does both)."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        from pipeline.notify import env
        return env(name) or None
    except Exception:
        return None


def go_api_key() -> str | None:
    """The Go subscription key, or None. It is the `oc_sk_…` key from Console > Account."""
    return _env(GO_KEY_ENV)


def go_session_id(prefix: str = "mu-compare") -> str:
    """A stable-per-process session id: Go uses it for routing and prompt caching."""
    return f"{prefix}-{os.getpid()}-{int(time.time())}"


def is_go_model(model: str) -> bool:
    """True when `model` is served by OpenCode Go rather than by Google directly."""
    m = (model or "").strip().lower()
    if not m:
        return False
    return not m.startswith(_NON_GO_PREFIXES)


def go_client(api_key: str | None = None, session: str | None = None, label: str = ""):
    """A `google-genai` Client pointed at Go's Google-compatible endpoint.

    Every sync `models.generate_content` the client makes is metered into the Go allowance
    ledger (see `record_go_call`): a real call records the allowance, list cost, billing source
    and tokens Go reported for it, and a call whose response reports no allowance records
    nothing at all. `label` is the ledger line's label (a caller passes its own, e.g.
    `"social_writer"`), so a run's spend is attributable without reading llm_usage.

    Raises ValueError when no key is configured, so a caller fails loudly instead of
    silently falling back to a Gemini key and billing the wrong account.
    """
    key = api_key or go_api_key()
    if not key:
        raise ValueError(f"{GO_KEY_ENV} is not set")

    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=key,
        http_options=types.HttpOptions(
            base_url=GO_GOOGLE_BASE_URL,
            headers={"x-opencode-session": session or _env(SESSION_ENV) or go_session_id()},
        ),
    )
    _account_calls(client, label)
    return client


def _field(obj, name: str):
    """One field of a usage object or dict, including the extras the SDK keeps off the schema.

    `usageMetadata.opencode` is not a Gemini field, so google-genai keeps it in `model_extra`
    (or only in `model_dump()`, depending on the version) — the same three spellings
    `newsroom/tool_loop/openai_loop.py` reads for the OpenAI-shaped path.
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    value = getattr(obj, name, None)
    if value is not None:
        return value
    extra = getattr(obj, "model_extra", None)
    if isinstance(extra, dict) and name in extra:
        return extra[name]
    dump = getattr(obj, "model_dump", None)
    if callable(dump):
        try:
            data = dump()
        except Exception:
            data = None
        if isinstance(data, dict):
            return data.get(name)
    return None


def _number(value):
    """A float, or None when the field was absent or is not a number (`billing_source` is a string)."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _count(value) -> int:
    """A token count; 0 when the response did not report it."""
    number = _number(value)
    return int(number) if number is not None else 0


def response_usage(resp) -> dict:
    """What one `generateContent` response reported: its tokens and Go's allowance block.

    Keys: `input`, `cached`, `output` (token counts, 0 when unreported) and `allowance_usd`,
    `list_cost_usd`, `billing_source` (None when the response carries no `opencode` block).
    """
    meta = None if resp is None else (getattr(resp, "usage_metadata", None)
                                      if not isinstance(resp, dict) else None)
    if isinstance(resp, dict):
        meta = resp.get("usage_metadata") or resp.get("usageMetadata")
    block = _field(meta, "opencode")
    billing = _field(block, "billing_source")
    return {
        "input": _count(_field(meta, "prompt_token_count")),
        "cached": _count(_field(meta, "cached_content_token_count")),
        "output": _count(_field(meta, "candidates_token_count")),
        "allowance_usd": _number(_field(block, "allowance")),
        "list_cost_usd": _number(_field(block, "list_cost")),
        "billing_source": billing if isinstance(billing, str) else None,
    }


def record_go_call(resp, model: str = "", label: str = "") -> dict | None:
    """Append one Go allowance line for one response, or record nothing. Returns the line or None.

    A line is written **only** when the response reports an allowance: that number is the dollars
    the call took out of the Go subscription, priced over the rolling windows by
    `tools/go_usage.py --source ledger`. The tokens ride along (`input_tokens`, `cached_tokens`,
    `output_tokens`) with the model id and a UTC timestamp. A response that reports tokens but no
    allowance writes **nothing**: a ledger line defaulting to `0` would read as "this call cost
    nothing", and an empty ledger makes `tools/go_usage.py` say it has nothing to price
    (AUDIT R6 F4 Deferral 1). A free Go model's own reported `0` is recorded as Go reported it.
    """
    usage = response_usage(resp)
    if usage["allowance_usd"] is None:
        return None
    from newsroom.tool_loop import go_allowance
    tokens = {key: usage[key] for key in ("input", "cached", "output")}
    go_allowance.record(
        at=datetime.now(timezone.utc),
        model=model or "",
        label=label,
        allowance_usd=usage["allowance_usd"],
        list_cost_usd=usage["list_cost_usd"],
        billing_source=usage["billing_source"],
        tokens=tokens,
    )
    return {"model": model or "", "label": label,
            "allowance_usd": usage["allowance_usd"],
            "list_cost_usd": usage["list_cost_usd"],
            "billing_source": usage["billing_source"], **tokens}


def _model_of(args, kwargs) -> str:
    """The model id of one `generate_content` call: the keyword, else a leading string argument."""
    model = kwargs.get("model")
    if model is None and args and isinstance(args[0], str):
        model = args[0]
    return model if isinstance(model, str) else ""


def _account_calls(client, label: str = ""):
    """Meter every sync Go request on `client` into the Go allowance ledger.

    Hooks the per-request method — `models._generate_content`, one HTTP request per call, the
    automatic-function-calling loop included, exactly the seam `db.llm_usage.UsageMeter` counts —
    and falls back to `models.generate_content` for a client that only has the public method. So a
    Go call is accounted wherever it is made (the newsroom writer, the AFC tool loop,
    `engines.llm_provider.call_go`/`GoText`, the comparisons) without changing any caller.
    Accounted once per client: a second call to this function, or a `UsageMeter` around the same
    client, wraps the existing hook and never double-records a line.

    Metering never breaks a call: a failure to read the usage block prints one line and the
    response is returned untouched.
    """
    models = getattr(client, "models", None)
    if models is None or getattr(models, "_go_ledger_hook", False):
        return client
    target = "_generate_content" if callable(getattr(models, "_generate_content", None)) \
        else "generate_content"
    inner = getattr(models, target, None)
    if not callable(inner):
        return client

    def accounted(*args, **kwargs):
        resp = inner(*args, **kwargs)
        try:
            record_go_call(resp, _model_of(args, kwargs), label)
        except Exception as exc:                     # metering never stops an inference
            print(f"  (go allowance not recorded: {exc})", file=sys.stderr)
        return resp

    setattr(models, target, accounted)
    models._go_ledger_hook = True
    return client


def client_for_model(model: str, api_key: str | None = None, go_key: str | None = None,
                     session: str | None = None, label: str = ""):
    """The one client a model id runs on: OpenCode Go's for a Go id, Google's for a Gemini id.

    The shared factory for every `google-genai` site (AUDIT_QUEUE_INVENTORY.md R11): a Go model id
    (`glm-5.3-flash`, `muse-spark-1.3-contributor`, …) can no longer be sent to Google's endpoint, and a
    Gemini-only site no longer has to require `GEMINI_API_KEY` to run a Go model.

    `api_key` is a **Gemini** key and `go_key` an **OpenCode Go** key. They are never interchangeable: a Gemini
    key forwarded by mistake can neither authenticate a Go call nor become the Go client's credential
    (AUDIT R6 F1). Without a Go key this raises `ValueError("OPENCODE_GO_API_KEY is not set")`; without a Gemini
    key, `RuntimeError("GEMINI_API_KEY is not set")`. Either way it fails loudly, never to the other provider.
    `label` names the Go allowance ledger line a Go call writes.
    """
    if is_go_model(model):
        return go_client(api_key=go_key, session=session, label=label)
    key = api_key or _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    from google import genai
    return genai.Client(api_key=key)


def client_for_model(model: str, api_key: str | None = None, go_key: str | None = None,
                     session: str | None = None):
    """The one client a model id runs on: OpenCode Go's for a Go id, Google's for a Gemini id.

    The shared factory for every `google-genai` site (AUDIT_QUEUE_INVENTORY.md R11): a Go model id
    (`glm-5.3-flash`, `muse-spark-1.3-contributor`, …) can no longer be sent to Google's endpoint, and a
    Gemini-only site no longer has to require `GEMINI_API_KEY` to run a Go model.

    `api_key` is a **Gemini** key and `go_key` an **OpenCode Go** key. They are never interchangeable: a Gemini
    key forwarded by mistake can neither authenticate a Go call nor become the Go client's credential
    (AUDIT R6 F1). Without a Go key this raises `ValueError("OPENCODE_GO_API_KEY is not set")`; without a Gemini
    key, `RuntimeError("GEMINI_API_KEY is not set")`. Either way it fails loudly, never to the other provider.
    """
    if is_go_model(model):
        return go_client(api_key=go_key, session=session)
    key = api_key or _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    from google import genai
    return genai.Client(api_key=key)
