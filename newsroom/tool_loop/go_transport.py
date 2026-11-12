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

Only the client construction lives here. Nothing in this module calls a model, imports the SDK
at module load, or knows about articles.
"""
from __future__ import annotations

import os
import time

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


def go_client(api_key: str | None = None, session: str | None = None):
    """A `google-genai` Client pointed at Go's Google-compatible endpoint.

    Raises ValueError when no key is configured, so a caller fails loudly instead of
    silently falling back to a Gemini key and billing the wrong account.
    """
    key = api_key or go_api_key()
    if not key:
        raise ValueError(f"{GO_KEY_ENV} is not set")

    from google import genai
    from google.genai import types

    return genai.Client(
        api_key=key,
        http_options=types.HttpOptions(
            base_url=GO_GOOGLE_BASE_URL,
            headers={"x-opencode-session": session or _env(SESSION_ENV) or go_session_id()},
        ),
    )


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
