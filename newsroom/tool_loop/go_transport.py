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

`opencode` is not a Gemini field, so google-genai 2.34.0 drops it while parsing the response
(its models forbid extra fields). Accounting therefore reads the **raw** JSON body at
`client._api_client.request`, before the SDK parses it, and takes the model id from the request
path (`models/<id>:generateContent`).

Only the client construction and its per-call accounting live here. Nothing in this module calls
a model, imports the SDK at module load, or knows about articles.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from urllib.parse import unquote

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

    Every sync HTTP request the client makes is metered into the Go allowance ledger (see
    `record_go_call`): a real call records the allowance, list cost, billing source and tokens Go
    reported for it, and a call whose response reports no allowance records nothing at all.
    Metering reads the raw response body, because the SDK drops `usageMetadata.opencode` while
    parsing. `label` is the ledger line's label (a caller passes its own, e.g. `"social_writer"`),
    so a run's spend is attributable without reading llm_usage.

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
    """One field of a usage object or dict, including the extras a model may keep off the schema.

    A raw body is a plain dict. A *parsed* `GenerateContentResponse` never carries Go's
    `opencode` block on google-genai 2.34.0 (its models forbid extra fields and the response
    converter drops unknown keys), but the OpenAI-shaped path and older SDKs can keep one in
    `model_extra` or only in `model_dump()`, and those spellings still read here.
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


def _first(obj, names):
    """The first of `names` `obj` carries, so a snake_case and a camelCase spelling both read."""
    for name in names:
        value = _field(obj, name)
        if value is not None:
            return value
    return None


def response_usage(resp) -> dict:
    """What one `generateContent` response reported: its tokens and Go's allowance block.

    `resp` is a parsed SDK response or the raw JSON body (a dict). Keys: `input`, `cached`,
    `output` (token counts, 0 when unreported; thinking tokens are part of the output) and
    `allowance_usd`, `list_cost_usd`, `billing_source` (None when the body carries no `opencode`
    block). Both the SDK's snake_case and Go's camelCase token keys are read.
    """
    if isinstance(resp, dict):
        meta = resp.get("usage_metadata") or resp.get("usageMetadata")
    else:
        meta = None if resp is None else getattr(resp, "usage_metadata", None)
    block = _field(meta, "opencode")
    billing = _field(block, "billing_source")
    output = (_count(_first(meta, ("candidates_token_count", "candidatesTokenCount")))
              + _count(_first(meta, ("thoughts_token_count", "thoughtsTokenCount"))))
    return {
        "input": _count(_first(meta, ("prompt_token_count", "promptTokenCount"))),
        "cached": _count(_first(meta, ("cached_content_token_count",
                                       "cachedContentTokenCount"))),
        "output": output,
        "allowance_usd": _number(_field(block, "allowance")),
        "list_cost_usd": _number(_field(block, "list_cost")),
        "billing_source": billing if isinstance(billing, str) else None,
    }


def record_go_call(resp, model: str = "", label: str = "") -> dict | None:
    """Append one Go allowance line for one response, or record nothing. Returns the line or None.

    `resp` is the raw JSON response body (what `_account_calls` reads) or a parsed SDK response.
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


def _response_body(resp) -> dict | None:
    """The JSON object one SDK `HttpResponse` carried, or None when there is none to read."""
    body = getattr(resp, "body", None)
    if isinstance(body, (bytes, bytearray)):
        body = body.decode("utf-8", "replace")
    if not isinstance(body, str) or not body.strip():
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _model_of_path(path) -> str:
    """The model id of one request path: `models/<id>:generateContent` -> `<id>`."""
    if not isinstance(path, str) or not path:
        return ""
    tail = path.split("models/", 1)[-1]
    tail = tail.split(":", 1)[0].split("?", 1)[0].strip("/")
    return unquote(tail)


def _account_calls(client, label: str = ""):
    """Meter every sync Go request on `client` into the Go allowance ledger.

    Hooks the client's `_api_client.request` — the single point every sync `generate_content`
    (the automatic-function-calling loop and `chats` included) passes through, exactly one HTTP
    request per call — and meters the **raw JSON body** there. `usageMetadata.opencode` is not a
    Gemini field, so google-genai 2.34.0 drops it while parsing the response: hooking the parsed
    response (the previous `models._generate_content` hook) saw no allowance and wrote nothing.

    So a Go call is accounted wherever it is made (the newsroom writer, the AFC tool loop,
    `engines.llm_provider.call_go`/`GoText`, the comparisons) without changing any caller.
    Accounted once per client: a second call to this function, or a `UsageMeter` around the same
    client, wraps the existing hook and never double-records a line. Streaming (`request_streamed`)
    and `client.aio` are not metered; no production code uses them.

    Metering never breaks a call: a failure to read the body prints one line and the response is
    returned untouched. A request that raised records nothing.
    """
    api = getattr(client, "_api_client", None)
    if api is None or getattr(api, "_go_ledger_hook", False):
        return client
    inner = getattr(api, "request", None)
    if not callable(inner):
        return client

    def accounted(*args, **kwargs):
        resp = inner(*args, **kwargs)
        try:
            path = args[1] if len(args) > 1 else kwargs.get("path", "")
            record_go_call(_response_body(resp), _model_of_path(path), label)
        except Exception as exc:                     # metering never stops an inference
            print(f"  (go allowance not recorded: {exc})", file=sys.stderr)
        return resp

    setattr(api, "request", accounted)
    api._go_ledger_hook = True
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

