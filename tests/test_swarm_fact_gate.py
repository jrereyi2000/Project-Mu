"""The write-time fact gate wired into `_execute_agent`, its storage and the weekly notice line.

Offline: the writer runs on the fake google-genai client from tests/test_swarm_capture.py, the checker is a
patched `newsroom.swarm_dispatcher.fact_gate.check`, and the save path talks to a FakeConn/FakeCursor.

Run: pytest -m "not integration" tests/test_swarm_fact_gate.py
"""
import json
from types import SimpleNamespace
from unittest import mock

import pytest

from tests.test_swarm_capture import _Client, _dispatcher, _prompt


@pytest.fixture(autouse=True)
def _gate_on(monkeypatch):
    """tests/conftest.py switches the gate off for the unit tier; these tests are the gate."""
    monkeypatch.setenv("FACT_GATE_ARTICLES", "1")


CONTRADICTED_CLAIM = {
    "claim": "Isaiah Carson threw for 4,102 yards",
    "verdict": "CONTRADICTED",
    "evidence": "not in evidence",
    "derived": False,
}
SUPPORTED_CLAIM = {
    "claim": "Sam Hollis threw for 3,900 yards",
    "verdict": "SUPPORTED",
    "evidence": "Hollis 3900 passing yards",
    "derived": False,
}


def _gate(contradicted=0, model="deepseek-v4.1-flash"):
    """The shape newsroom/fact_gate.py documents: claims, counts, ok, model."""
    claims = list([CONTRADICTED_CLAIM] * contradicted) + [SUPPORTED_CLAIM]
    return {
        "claims": claims,
        "counts": {"SUPPORTED": 1, "CONTRADICTED": contradicted, "UNSUPPORTED": 0},
        "ok": contradicted == 0,
        "model": model,
    }


class _FakeCursor:
    """Records every statement; answers the column probe, the INSERT's RETURNING id and a count query."""

    def __init__(self, columns=True, count_row=(3, 2, 1), count_raises=False):
        self.columns = columns
        self.count_row = count_row
        self.count_raises = count_raises
        self.executed = []
        self._next = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        sql = " ".join(str(sql).split())
        self.executed.append((sql, params))
        upper = sql.upper()
        if "INFORMATION_SCHEMA.COLUMNS" in upper:
            self._next = [(1,)] if self.columns else []
        elif "INSERT INTO NEWS_FEED" in upper:
            self._next = [(42,)]
        elif "COUNT(*)" in upper:
            if self.count_raises:
                raise RuntimeError("column fact_gate_status does not exist")
            self._next = [self.count_row]
        else:
            self._next = []

    def fetchone(self):
        return self._next.pop(0) if self._next else None

    def inserts(self):
        return [(sql, params) for sql, params in self.executed if "INSERT INTO NEWS_FEED" in sql.upper()]


class _FakeConn:
    def __init__(self, columns=True, count_row=(3, 2, 1), count_raises=False):
        self.cur = _FakeCursor(columns=columns, count_row=count_row, count_raises=count_raises)
        self.commits = 0

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1


def _saving_dispatcher(tools=None, columns=True):
    dispatcher = _dispatcher(tools=tools)
    dispatcher.entity_tagger = SimpleNamespace(
        extract_and_store_entities=lambda **kwargs: {"teams": [], "players": []})
    dispatcher._record_agent_memory = lambda *a, **k: None
    conn = _FakeConn(columns=columns)
    return dispatcher, conn


def _save(dispatcher, conn, client, capture=None, **kwargs):
    with mock.patch("newsroom.swarm_dispatcher.psycopg.connect", return_value=conn):
        return dispatcher._execute_agent(
            _prompt(), 2036, 18, None, live=True, model="muse-spark-1.3-contributor",
            save_to_db=True, client=client, capture={} if capture is None else capture, **kwargs)


# ------------------------------------------------------------------------------------------------ the gate ---

def test_a_contradiction_triggers_one_rewrite_one_recheck_and_records_it():
    def lookup(city: str) -> str:
        """Look up a city."""
        return "18C and clear in " + city

    dispatcher = _dispatcher(tools=[lookup])
    client = _Client(["# Headline\n\nIsaiah Carson threw for 4,102 yards.", "# Headline\n\nHollis threw for 3,900."])
    capture = {}
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check",
                    side_effect=[_gate(1), _gate(0)]) as check, \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.rewrite_prompt", return_value="fix it") as prompt:
        result = dispatcher._execute_agent(_prompt(), 2036, 18, None, live=True,
                                           model="muse-spark-1.3-contributor", save_to_db=False,
                                           client=client, capture=capture)

    assert check.call_count == 2                     # one check, one re-check, never a second rewrite
    assert prompt.call_count == 1
    assert client.models.calls == 2                  # one writer call, one correction call
    assert capture["rewrites"] == ["FACT_GATE"]
    assert capture["text"].startswith("# Headline\n\nHollis")   # the corrected version is published
    assert result["fact_gate_status"] == "rewritten"
    payload = result["fact_gate"]
    assert payload["rewritten"] is True
    assert payload["contradicted_kept"] == 0
    assert payload["contradicted_discarded"] == 1
    assert [a["counts"]["CONTRADICTED"] for a in payload["attempts"]] == [1, 0]
    assert {a["model"] for a in payload["attempts"]} == {"deepseek-v4.1-flash"}
    # the evidence is the assignment plus what the writer's own tool actually returned
    sent_evidence = check.call_args_list[0][0][1]
    assert sent_evidence.startswith("Write the recap.")
    assert "TOOL lookup(" in sent_evidence and "18C" in sent_evidence
    # the re-check judges the rewrite against the same evidence
    assert check.call_args_list[1][0][0] == capture["text"]
    assert check.call_args_list[1][0][1] == sent_evidence
    assert payload["claims"] == []                  # the kept, corrected version contradicts nothing


def test_a_still_contradicted_article_saves_flagged_with_its_claims():
    dispatcher, conn = _saving_dispatcher(columns=True)
    client = _Client(["first draft", "second draft"])
    capture = {}
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check", side_effect=[_gate(1), _gate(1)]), \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.rewrite_prompt", return_value="fix it"):
        result = _save(dispatcher, conn, client, capture=capture)

    assert result["fact_gate_status"] == "flagged"
    assert capture["rewrites"] == ["FACT_GATE"]
    (sql, params), = conn.cur.inserts()
    assert "fact_gate_status" in sql and "fact_gate" in sql
    assert params[-2] == "flagged"
    stored = json.loads(params[-1])
    assert stored["counts"]["CONTRADICTED"] == 1
    assert stored["claims"] == [{"claim": CONTRADICTED_CLAIM["claim"],
                                 "evidence": CONTRADICTED_CLAIM["evidence"]}]
    assert stored["contradicted_kept"] == 1
    assert stored["rewritten"] is True
    assert "first draft" not in params[-1] and "second draft" not in params[-1]   # never the article


def test_a_successful_rewrite_saves_rewritten():
    dispatcher, conn = _saving_dispatcher(columns=True)
    client = _Client(["wrong draft", "corrected draft"])
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check", side_effect=[_gate(1), _gate(0)]), \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.rewrite_prompt", return_value="fix it"):
        result = _save(dispatcher, conn, client)

    assert result["fact_gate_status"] == "rewritten"
    (sql, params), = conn.cur.inserts()
    assert params[-2] == "rewritten"
    assert json.loads(params[-1])["rewritten"] is True
    assert "corrected draft" in params[9]            # the kept text is what is stored


def test_a_clean_first_check_saves_clean_and_never_rewrites():
    dispatcher, conn = _saving_dispatcher(columns=True)
    client = _Client(["a true article"])
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check", return_value=_gate(0)) as check, \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.rewrite_prompt") as prompt:
        result = _save(dispatcher, conn, client)

    check.assert_called_once()
    prompt.assert_not_called()
    assert client.models.calls == 1
    assert result["fact_gate_status"] == "clean"
    (sql, params), = conn.cur.inserts()
    assert params[-2] == "clean"
    assert json.loads(params[-1])["claims"] == []
    assert json.loads(params[-1])["rewritten"] is False


def test_mu_no_llm_skips_the_gate_without_calling_the_checker(monkeypatch):
    dispatcher, conn = _saving_dispatcher(columns=True)
    client = _Client(["a mocked article"])
    monkeypatch.setenv("MU_NO_LLM", "1")
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check") as check:
        result = _save(dispatcher, conn, client)

    check.assert_not_called()
    assert client.models.calls == 1                  # exactly one model call, as today
    assert result["fact_gate_status"] == "skipped"
    assert result["fact_gate"] is None
    (sql, params), = conn.cur.inserts()
    assert params[-2] == "skipped" and params[-1] is None


def test_fact_gate_articles_zero_leaves_the_writer_path_as_today(monkeypatch):
    def lookup(city: str) -> str:
        """Look up a city."""
        return "18C and clear in " + city

    dispatcher, conn = _saving_dispatcher(tools=[lookup], columns=False)
    client = _Client(["a true article"])
    capture = {}
    monkeypatch.setenv("FACT_GATE_ARTICLES", "0")
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check") as check:
        result = _save(dispatcher, conn, client, capture=capture)

    check.assert_not_called()
    assert client.models.calls == 1                  # one writer call, no correction call
    assert capture["rewrites"] == []
    assert len(capture["tool_calls"]) == 1           # the tool trace is what it was: no helper evidence
    assert set(capture["tool_calls"][0]) == {"name", "args", "result", "error"}
    assert result["fact_gate_status"] == "skipped"
    assert result["fact_gate"] is None
    (sql, params), = conn.cur.inserts()
    assert "fact_gate_status" not in sql             # the old INSERT, unchanged
    assert len(params) == 11


def test_a_database_without_the_columns_still_saves_the_article(capsys):
    dispatcher, conn = _saving_dispatcher(columns=False)
    client = _Client(["a true article", "a rewrite"])
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check", return_value=_gate(0)):
        result = _save(dispatcher, conn, client)

    assert result["article_id"] == 42
    assert result["fact_gate_status"] == "clean"      # still judged and logged
    (sql, params), = conn.cur.inserts()
    assert "fact_gate_status" not in sql
    assert len(params) == 11
    probes = [s for s, _ in conn.cur.executed if "INFORMATION_SCHEMA.COLUMNS" in s.upper()]
    assert len(probes) == 1                           # probed once, then the old INSERT
    assert "[FACT GATE COLUMN MISSING]" in capsys.readouterr().out


def test_a_checker_that_raises_is_caught_and_the_article_still_saves():
    dispatcher, conn = _saving_dispatcher(columns=True)
    client = _Client(["a true article"])
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check", side_effect=RuntimeError("checker down")):
        result = _save(dispatcher, conn, client)

    assert result["fact_gate_status"] == "skipped"
    assert result["fact_gate"] is None
    assert result["article_id"] == 42
    (sql, params), = conn.cur.inserts()
    assert params[-2] == "skipped" and params[-1] is None


def test_a_rewrite_that_returns_nothing_keeps_the_flagged_article():
    dispatcher = _dispatcher(tools=[])
    client = _Client(["first draft"])
    original_create = client.chats.create

    def create(**kwargs):
        chat = original_create(**kwargs)
        calls = {"n": 0}
        send = chat.send_message

        def send_message(prompt):
            calls["n"] += 1
            if calls["n"] > 1:                        # the correction call returns no text
                return SimpleNamespace(text=None, candidates=[])
            return send(prompt)
        chat.send_message = send_message
        return chat
    client.chats.create = create

    capture = {}
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check", return_value=_gate(1)), \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.rewrite_prompt", return_value="fix it"):
        result = dispatcher._execute_agent(_prompt(), 2036, 18, None, live=True,
                                           model="muse-spark-1.3-contributor", save_to_db=False,
                                           client=client, capture=capture)

    assert result["fact_gate_status"] == "flagged"
    assert result["text"] == "first draft"            # the contradicting text stands, visibly flagged
    assert capture["rewrites"] == ["FACT_GATE"]


def test_an_offline_mock_run_is_not_gated():
    dispatcher = _dispatcher(tools=[])
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check") as check:
        result = dispatcher._execute_agent(_prompt(), 2036, 18, None, live=False, save_to_db=False)
    check.assert_not_called()
    assert result["fact_gate_status"] is None
    assert result["fact_gate"] is None


# ----------------------------------------------------------------------------------------------- the notice ---

def test_the_notice_counts_the_weeks_articles():
    from newsroom.weekly_run import fact_gate_summary
    cur = _FakeCursor(count_row=(12, 10, 1))
    line = fact_gate_summary(cur, 2036, 18)
    assert line == "12 articles written, 10 fact-gated (1 flagged)"
    assert "content_type = 'ARTICLE'" in cur.executed[0][0]   # TWEET rows are never articles
    assert cur.executed[0][1] == (2036, 18)


def test_the_notice_is_silent_without_the_column():
    from newsroom.weekly_run import fact_gate_summary
    assert fact_gate_summary(_FakeCursor(count_raises=True), 2036, 18) is None
