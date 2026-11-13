"""Agent Wire posts on OpenCode Go (GLM_5_3_UNIFICATION_PLAN U2, §2.2). No database, no API calls: each client is
a fake shaped like the google-genai one (`.models.generate_content`; a response with `.text` and
`.usage_metadata`).

  pytest -m "not integration" tests/test_social_writer_go.py
"""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from newsroom import social_publish as SP
from newsroom import social_writer as SW
from newsroom.tool_loop import go_transport
from tests.test_social_writer import voice_assignment

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GO_MODEL = "glm-5.3-flash"


class _Response:
    def __init__(self, text):
        self.text = text
        self.usage_metadata = SimpleNamespace(prompt_token_count=100, cached_content_token_count=0,
                                              candidates_token_count=20, thoughts_token_count=5)


class _Models:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return _Response(self.replies.pop(0))


class _FakeClient:
    """A Gemini- or Go-shaped client: `.models.generate_content` returns the queued replies."""

    def __init__(self, replies):
        self.models = _Models(replies)

    @property
    def calls(self):
        return self.models.calls


def _post(**kw):
    return json.dumps({"text": "Bills by a field goal", "about": [], "picks": [], "calls": [], **kw})


def _clean_env(monkeypatch):
    """No provider env, so the model id alone decides the route."""
    for key in ("LLM_PROVIDER", "LLM_PROVIDER_AGENT_WIRE"):
        monkeypatch.delenv(key, raising=False)


def _long(prefix="x"):
    return prefix * 300


# --------------------------------------------------------------------------------------------- client routing ---
def test_a_go_model_id_uses_the_go_client(monkeypatch):
    """A bare Go id builds its client through go_transport.go_client(), never a Gemini one."""
    _clean_env(monkeypatch)
    fake = _FakeClient([_post()])
    monkeypatch.setattr(go_transport, "go_client", lambda *a, **k: fake)
    monkeypatch.setattr(SW, "load_api_key", lambda: None)         # a Go post needs no Gemini key at all

    out = SW.write_post(None, GO_MODEL, "sys", "prompt", assignment=voice_assignment())

    assert out["text"] == "Bills by a field goal"
    assert fake.calls[0]["model"] == GO_MODEL
    assert out["calls"] == 1
    assert out["usage"] == {"input": 100, "cached": 0, "output": 20, "thinking": 5}


def test_a_gemini_model_id_uses_the_gemini_client(monkeypatch):
    """A gemini-* id keeps the caller's client and builds no Go client."""
    _clean_env(monkeypatch)
    fake = _FakeClient([_post()])
    built = []
    monkeypatch.setattr(go_transport, "go_client", lambda *a, **k: built.append(k) or _FakeClient([]))

    out = SW.write_post(fake, "gemini-3.8-flash", "sys", "prompt", assignment=voice_assignment())

    assert out["text"] == "Bills by a field goal"
    assert fake.calls[0]["model"] == "gemini-3.8-flash"
    assert built == []


def test_an_explicit_gemini_provider_wins_over_a_go_id(monkeypatch):
    """provider="gemini" is the rollback lever: the Go-looking id runs on the Gemini client it was given."""
    _clean_env(monkeypatch)
    fake = _FakeClient([_post()])
    monkeypatch.setattr(go_transport, "go_client", lambda *a, **k: pytest.fail("Go client built"))

    SW.write_post(fake, GO_MODEL, "sys", "prompt", provider="gemini", assignment=voice_assignment())

    assert fake.calls[0]["model"] == GO_MODEL


def test_a_go_model_does_not_use_the_gemini_client_it_was_handed(monkeypatch):
    """social_model_compare passes one real Gemini client for every model: a Go id still goes to the Go client."""
    _clean_env(monkeypatch)
    from google import genai
    gemini = genai.Client(api_key="fake-key")                    # a real Google-pointed client, never called here
    go = _FakeClient([_post(text="go wrote this")])
    monkeypatch.setattr(go_transport, "go_client", lambda *a, **k: go)

    out = SW.write_post(gemini, GO_MODEL, "sys", "prompt", assignment=voice_assignment())

    assert out["text"] == "go wrote this"
    assert go.calls[0]["model"] == GO_MODEL


def test_an_injected_go_client_is_used_as_given(monkeypatch):
    """A Go client a caller already holds (a test double, or one from go_client()) is not rebuilt."""
    _clean_env(monkeypatch)
    fake = _FakeClient([_post(text="injected go text")])
    monkeypatch.setattr(go_transport, "go_client", lambda *a, **k: pytest.fail("rebuilt an injected Go client"))

    out = SW.write_post(fake, GO_MODEL, "sys", "prompt")

    assert out["text"] == "injected go text" and fake.calls[0]["model"] == GO_MODEL


def test_a_go_model_never_needs_a_gemini_key(monkeypatch):
    """With no Gemini key the Go path still runs: the Gemini key is never read."""
    _clean_env(monkeypatch)
    fake = _FakeClient([_post(text="go text")])
    monkeypatch.setattr(go_transport, "go_client", lambda *a, **k: fake)
    monkeypatch.setattr(SW, "load_api_key", lambda: pytest.fail("asked for the Gemini key"))

    out = SW.write_post(None, "muse-spark-1.3-contributor", "sys", "p")

    assert (out["text"], out["structured"]) == ("go text", True)
    assert fake.calls[0]["model"] == "muse-spark-1.3-contributor"


def test_a_missing_go_key_raises_instead_of_writing_through_gemini(monkeypatch):
    """No Go key: the transport's ValueError reaches the caller and no Gemini client is used."""
    _clean_env(monkeypatch)
    from google import genai
    gemini = genai.Client(api_key="fake-key")                    # a real Gemini client, never used or called
    monkeypatch.setattr(go_transport, "go_api_key", lambda: None)     # the real go_client(), with no key
    monkeypatch.setattr(go_transport, "_env", lambda name: None)

    with pytest.raises(ValueError, match="OPENCODE_GO_API_KEY"):
        SW.write_post(gemini, GO_MODEL, "sys", "prompt", assignment=voice_assignment())


# ---------------------------------------------------------------------------------------- same contract on Go ---
def test_the_json_contract_parses_a_go_reply(monkeypatch):
    """text/about/picks/calls resolve exactly as they do on a Gemini reply."""
    _clean_env(monkeypatch)
    raw = _post(text="Bills by a field goal", about=[{"type": "team", "name": "Cowboys", "lean": -1}],
                picks=[{"game": "BUF @ KC", "winner": "BUF", "score": None, "confidence": "lean"}],
                calls=[{"call_type": "MVP", "subject": "Rayford", "detail": ""}])
    fake = _FakeClient([raw])
    monkeypatch.setattr(go_transport, "go_client", lambda *a, **k: fake)

    out = SW.write_post(None, GO_MODEL, "sys", "prompt", assignment=voice_assignment())

    assert out["text"] == "Bills by a field goal"
    assert out["structured"] is True
    assert out["picks"][0]["winner_team_id"] == 4 and out["picks"][0]["game_id"] == 501
    assert out["season_calls"][0]["subject_id"] == 77
    assert out["about"] == [{"type": "team", "id": 9, "name": "Cowboys", "lean": -1}]


def test_an_over_280_character_go_reply_is_rewritten_once(monkeypatch):
    """The one-rewrite pass runs on Go exactly as on Gemini, and the second call carries the ask."""
    _clean_env(monkeypatch)
    fake = _FakeClient([_post(text=_long()), _post(text="Chiefs by a field goal")])
    monkeypatch.setattr(go_transport, "go_client", lambda *a, **k: fake)

    out = SW.write_post(None, GO_MODEL, "sys", "prompt", assignment=voice_assignment())

    assert out["calls"] == 2 and len(fake.calls) == 2
    assert out["text"] == "Chiefs by a field goal"
    assert out["problems"][0].startswith("rewritten once for length")
    ask = fake.calls[1]["contents"][-1].parts[-1].text
    assert "Rewrite it in under 260 characters" in ask
    assert "Return the same JSON object" in ask                  # it was a structured reply


def test_a_go_reply_that_still_fails_keeps_its_problems(monkeypatch):
    """A rewrite that is still over 280 is kept and flagged; it is not asked a third time."""
    _clean_env(monkeypatch)
    fake = _FakeClient([_post(text=_long("a")), _post(text=_long("b"))])
    monkeypatch.setattr(go_transport, "go_client", lambda *a, **k: fake)

    out = SW.write_post(None, GO_MODEL, "sys", "prompt", assignment=voice_assignment())

    assert out["calls"] == 2 and len(fake.calls) == 2
    assert any("over 280" in p for p in out["problems"])
    assert out["problems"][0].startswith("rewritten once for length")


def test_a_non_json_go_reply_falls_back_to_plain_text(monkeypatch):
    _clean_env(monkeypatch)
    fake = _FakeClient(["Arrowhead was ROCKING"])
    monkeypatch.setattr(go_transport, "go_client", lambda *a, **k: fake)

    out = SW.write_post(None, GO_MODEL, "sys", "prompt")

    assert (out["text"], out["structured"], out["picks"], out["about"]) == ("Arrowhead was ROCKING", False, [], [])
    assert out["season_calls"] == []


# ----------------------------------------------------------------------------------------- no default change ---
def _weekly_run_social_model(**env):
    clean = {k: v for k, v in os.environ.items() if k != "SOCIAL_MODEL"}
    clean.update(env)
    out = subprocess.run([sys.executable, "-c", "import newsroom.weekly_run as w; print(w.SOCIAL_MODEL)"],
                         cwd=ROOT, env=clean, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_social_model_unset_keeps_gemini():
    assert _weekly_run_social_model() == "gemini-3.8-flash"


def test_social_model_env_selects_a_go_model():
    assert _weekly_run_social_model(SOCIAL_MODEL=GO_MODEL) == GO_MODEL


# ------------------------------------------------------------------------------------------- social_publish ---
def test_social_publish_builds_the_client_from_the_model(monkeypatch):
    """A Go SOCIAL_MODEL gets the Go client; a gemini-* one gets the Gemini client."""
    monkeypatch.setattr(go_transport, "go_client", lambda *a, **k: "go-client")
    monkeypatch.setattr(SP, "_gemini_client", lambda: pytest.fail("Gemini client built for a Go model"))
    assert SP._model_client(GO_MODEL) == "go-client"
    assert SP._model_client("muse-spark-1.3-contributor") == "go-client"

    monkeypatch.setattr(SP, "_gemini_client", lambda: "gemini-client")
    assert SP._model_client("gemini-3.8-flash") == "gemini-client"
    assert SP._model_client("gemini-3.8-flash:low") == "gemini-client"


def test_the_writer_builds_the_go_client_when_none_is_injected(monkeypatch):
    fake = _FakeClient([])
    monkeypatch.setattr(go_transport, "go_client", lambda *a, **k: fake)

    writer = SP._Writer(None, {"teams": {}, "season": 2036, "week": 1}, {}, {}, None, GO_MODEL, 1, lambda *a: None)

    assert writer.client is fake and writer.model == GO_MODEL


# --------------------------------------------------------------------------- billing labels (AUDIT R6 F4) ---
def test_a_go_post_reports_an_allowance_not_a_free_post(monkeypatch):
    """`cost_usd: 0.0` on a Go post is the subscription paying, not a free call."""
    _clean_env(monkeypatch)
    fake = _FakeClient(["Arrowhead was ROCKING"])
    monkeypatch.setattr(go_transport, "go_client", lambda *a, **k: fake)

    out = SW.write_post(None, GO_MODEL, "sys", "prompt")

    assert out["cost_usd"] == 0.0
    assert out["billing"] == "allowance"
    assert out["cost_note"] == SW.ALLOWANCE_NOTE
    assert "not $0" in SW.cost_note(GO_MODEL)


def test_a_priced_gemini_post_carries_no_allowance_label():
    assert SW.billing("gemini-3.8-flash") == "priced"
    assert SW.cost_note("gemini-3.8-flash") == ""


def test_an_unknown_gemini_model_is_unpriced_not_free():
    assert SW.billing("gemini-9-ultra") == "unpriced"
    assert SW.cost_note("gemini-9-ultra") == SW.UNPRICED_NOTE


def test_the_comment_line_names_the_allowance(monkeypatch):
    writer = SP._Writer(None, {"teams": {}, "season": 2036, "week": 1}, {}, {}, _FakeClient([]), GO_MODEL, 1,
                        lambda *a: None)
    writer.cost_notes[SW.ALLOWANCE_NOTE] += 2

    assert "Go allowance (not $0 free): 2 post(s)" in writer.cost_line()


def test_the_model_comparison_labels_a_go_run_as_an_allowance():
    from tools import social_model_compare as SMC
    rows = [{"cost_usd": 0.0, "usage": {"input": 100, "output": 20, "thinking": 0}, "problems": []}] * 3

    assert SMC.cost_text(GO_MODEL, rows) == f"{SW.ALLOWANCE_NOTE}, 3 posts"
    assert SMC.cost_text("gemini-3.8-flash", [dict(rows[0], cost_usd=0.00012)]).startswith("$0.0001 total")
