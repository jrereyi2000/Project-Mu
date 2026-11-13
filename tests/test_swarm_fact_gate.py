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
    # capture=None is the production write path — the only one the gate runs on (a capture run is the
    # model-comparison seam and is deliberately not gated).
    with mock.patch("newsroom.swarm_dispatcher.psycopg.connect", return_value=conn):
        return dispatcher._execute_agent(
            _prompt(), 2036, 18, None, live=True, model="muse-spark-1.3-contributor",
            save_to_db=True, client=client, capture=capture, **kwargs)


# ------------------------------------------------------------------------------------------------ the gate ---

def test_a_contradiction_triggers_one_rewrite_one_recheck_and_records_it():
    def lookup(city: str) -> str:
        """Look up a city."""
        return "18C and clear in " + city

    dispatcher = _dispatcher(tools=[lookup])
    client = _Client(["# Headline\n\nIsaiah Carson threw for 4,102 yards.", "# Headline\n\nHollis threw for 3,900."])
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check",
                    side_effect=[_gate(1), _gate(0)]) as check, \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.rewrite_prompt", return_value="fix it") as prompt:
        result = dispatcher._execute_agent(_prompt(), 2036, 18, None, live=True,
                                           model="muse-spark-1.3-contributor", save_to_db=False,
                                           client=client)

    assert check.call_count == 2                     # one check, one re-check, never a second rewrite
    assert prompt.call_count == 1
    assert client.models.calls == 2                  # one writer call, one correction call
    assert result["text"].startswith("# Headline\n\nHollis")   # the corrected version is published
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
    assert check.call_args_list[1][0][0] == result["text"]
    assert check.call_args_list[1][0][1] == sent_evidence
    assert payload["claims"] == []                  # the kept, corrected version contradicts nothing


def test_a_still_contradicted_article_saves_flagged_with_its_claims():
    dispatcher, conn = _saving_dispatcher(columns=True)
    client = _Client(["first draft", "second draft"])
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check", side_effect=[_gate(1), _gate(1)]), \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.rewrite_prompt", return_value="fix it"):
        result = _save(dispatcher, conn, client)

    assert result["fact_gate_status"] == "flagged"
    # The rewrite was tried and discarded (it contradicted just as much), so the published text is the
    # writer's and the payload says so; only the attempt is recorded (R5a F5).
    assert result["text"] == "first draft"
    assert result["fact_gate"]["rewritten"] is False
    assert result["fact_gate"]["rewrite_attempted"] is True
    (sql, params), = conn.cur.inserts()
    assert "fact_gate_status" in sql and "fact_gate" in sql
    assert params[-2] == "flagged"
    assert params[9] == "first draft"
    stored = json.loads(params[-1])
    assert stored["counts"]["CONTRADICTED"] == 1
    assert stored["claims"] == [{"claim": CONTRADICTED_CLAIM["claim"],
                                 "evidence": CONTRADICTED_CLAIM["evidence"]}]
    assert stored["contradicted_kept"] == 1
    assert stored["rewritten"] is False
    assert stored["rewrite_attempted"] is True
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
    capture = {}                                     # a capture run with the gate off: today's path exactly
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


def test_fact_gate_rewrite_zero_flags_without_a_correction_pass(monkeypatch, capsys):
    """R4's switch: judge, store the verdict and the claims, never send the correction message."""
    dispatcher, conn = _saving_dispatcher(columns=True)
    client = _Client(["a draft that contradicts the evidence"])
    monkeypatch.setenv("FACT_GATE_REWRITE", "0")
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check", return_value=_gate(2)) as check, \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.rewrite_prompt") as prompt:
        result = _save(dispatcher, conn, client)

    check.assert_called_once()                       # one check, no re-check
    prompt.assert_not_called()
    assert client.models.calls == 1                  # one writer call, no correction call
    assert result["fact_gate_status"] == "flagged"
    payload = result["fact_gate"]
    assert payload["rewritten"] is False
    assert payload["contradicted_kept"] == 2
    assert len(payload["claims"]) == 2
    assert [a["counts"]["CONTRADICTED"] for a in payload["attempts"]] == [2]
    (sql, params), = conn.cur.inserts()
    assert params[-2] == "flagged"
    stored = json.loads(params[-1])
    assert stored["claims"] and stored["rewritten"] is False
    assert stored["counts"]["CONTRADICTED"] == 2
    assert "rewrite disabled" in capsys.readouterr().out.lower()


@pytest.mark.parametrize("value", ["off", "false", "OFF"])
def test_fact_gate_rewrite_also_accepts_off_and_false(monkeypatch, value):
    dispatcher, conn = _saving_dispatcher(columns=True)
    client = _Client(["a draft"])
    monkeypatch.setenv("FACT_GATE_REWRITE", value)
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check", return_value=_gate(1)) as check, \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.rewrite_prompt") as prompt:
        result = _save(dispatcher, conn, client)

    check.assert_called_once()
    prompt.assert_not_called()
    assert client.models.calls == 1
    assert result["fact_gate_status"] == "flagged"


def test_fact_gate_rewrite_defaults_on(monkeypatch):
    """Unset means the correction pass runs, as every other gate test assumes."""
    monkeypatch.delenv("FACT_GATE_REWRITE", raising=False)
    dispatcher, conn = _saving_dispatcher(columns=True)
    client = _Client(["first draft", "second draft"])
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check", side_effect=[_gate(1), _gate(0)]), \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.rewrite_prompt", return_value="fix it"):
        result = _save(dispatcher, conn, client)

    assert result["fact_gate_status"] == "rewritten"
    assert client.models.calls == 2


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

    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check", return_value=_gate(1)), \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.rewrite_prompt", return_value="fix it"):
        result = dispatcher._execute_agent(_prompt(), 2036, 18, None, live=True,
                                           model="muse-spark-1.3-contributor", save_to_db=False,
                                           client=client)

    assert result["fact_gate_status"] == "flagged"
    assert result["text"] == "first draft"            # the contradicting text stands, visibly flagged
    assert result["fact_gate"]["rewritten"] is False   # nothing was adopted (R5a F5)
    assert result["fact_gate"]["rewrite_attempted"] is True


def test_a_comparison_capture_run_is_not_gated():
    """The capture seam records the writer's raw output (tools/article_model_compare.py): no checker call,
    no correction pass, no evidence list, whatever the environment says."""
    dispatcher = _dispatcher(tools=[])
    client = _Client(["raw text"])
    capture = {}
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check") as check:
        result = dispatcher._execute_agent(_prompt(), 2036, 18, None, live=True,
                                           model="muse-spark-1.3-contributor", save_to_db=False,
                                           client=client, capture=capture)

    check.assert_not_called()
    assert client.models.calls == 1
    assert result["fact_gate"] is None                # no verdict on a run that is never saved
    assert result["fact_gate_status"] in (None, "skipped")
    assert capture["text"] == "raw text"
    assert capture["rewrites"] == []


def test_an_offline_mock_run_is_not_gated():
    dispatcher = _dispatcher(tools=[])
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check") as check:
        result = dispatcher._execute_agent(_prompt(), 2036, 18, None, live=False, save_to_db=False)
    check.assert_not_called()
    assert result["fact_gate_status"] is None
    assert result["fact_gate"] is None


def test_an_unmigrated_database_is_gated_log_only_and_never_rewritten(capsys):
    """R5a F2: with no fact_gate_status column nothing may be rewritten — a rewrite saved with no
    record of it is silent alteration, which is worse than a flagged original."""
    dispatcher, conn = _saving_dispatcher(columns=False)
    client = _Client(["wrong draft", "corrected draft"])
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check", return_value=_gate(1)) as check, \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.rewrite_prompt") as prompt:
        result = _save(dispatcher, conn, client)

    check.assert_called_once()                        # judged, never re-checked
    prompt.assert_not_called()                        # no correction message at all
    assert client.models.calls == 1                   # the writer's call only
    assert result["text"] == "wrong draft"            # the writer's own text is what ships
    assert result["fact_gate_status"] == "flagged"
    assert result["fact_gate"]["rewritten"] is False
    assert result["fact_gate"]["rewrite_attempted"] is False
    (sql, params), = conn.cur.inserts()
    assert "fact_gate_status" not in sql              # the old INSERT
    assert params[9] == "wrong draft"
    probes = [s for s, _ in conn.cur.executed if "INFORMATION_SCHEMA.COLUMNS" in s.upper()]
    assert len(probes) == 1 and "table_schema = current_schema()" in probes[0]   # R5a F6
    out = capsys.readouterr().out
    assert "[FACT GATE COLUMN MISSING]" in out and "log-only" in out


def test_the_checker_is_never_handed_the_writers_client_or_model():
    """R5a F1, as landed in 45e58d1: the checker builds its own client for FACT_GATE_MODEL, so the
    writer's client is not billed for it and a Gemini writer is not silently 'skipped'."""
    dispatcher, conn = _saving_dispatcher(columns=True)
    client = _Client(["a draft"])
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check", return_value=_gate(0)) as check:
        _save(dispatcher, conn, client)

    args, kwargs = check.call_args
    assert kwargs == {}                               # no client=, no model= (the 546b9fa defect)
    assert len(args) == 2                             # (text, evidence) only
    assert args[0] == "a draft"
    assert client.models.calls == 1                   # one writer call; the checker's is its own


def test_the_checker_sees_the_whole_tool_result_past_twelve_thousand_characters():
    """R5a F3: a 12,000-character slice cut facts the writer saw out of the checker's view while the
    writer still had them, so real claims came back 'not in evidence'."""
    tail = "TAIL_FACT: Sam Hollis threw for 3,900 yards."

    def long_lookup(city: str) -> str:
        """A tool result longer than the old 12,000-character evidence cap."""
        return "x" * 13_000 + tail

    dispatcher = _dispatcher(tools=[long_lookup])
    client = _Client(["a draft"])
    with mock.patch("newsroom.swarm_dispatcher.fact_gate.check", return_value=_gate(0)) as check:
        dispatcher._execute_agent(_prompt(), 2036, 18, None, live=True,
                                  model="muse-spark-1.3-contributor", save_to_db=False,
                                  client=client)

    evidence = check.call_args[0][1]
    assert tail in evidence
    assert len(evidence) > 13_000


def test_a_rewrite_that_breaks_a_structured_article_is_rejected(capsys):
    """R5a F5: a fact-gate correction that drops a team from the Power Rankings is discarded — the
    pre-rewrite validated text ships — instead of publishing a page no table can be stored from."""
    dispatcher = _dispatcher(tools=[])
    dispatcher._power_rankings_team_index = lambda: []
    first = "# Power Rankings\n\n1. Bears\n2. Bills\n...\n32. Cardinals"
    broken = "# Power Rankings\n\n1. Bears"
    client = _Client([first, broken])

    with mock.patch("newsroom.swarm_dispatcher.parse_power_rankings",
                    side_effect=lambda text, index: ([], ["Missing ranks: [32]"] if text == broken else [])), \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.check", side_effect=[_gate(1), _gate(0)]), \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.rewrite_prompt", return_value="fix it"):
        result = dispatcher._execute_agent(_prompt(article_type="POWER_RANKINGS"), 2036, 18, None,
                                          live=True, model="muse-spark-1.3-contributor",
                                          save_to_db=False, client=client)

    assert client.models.calls == 2                   # writer + correction
    assert result["text"] == first                    # the validated original stands
    assert result["fact_gate_status"] == "flagged"
    assert result["fact_gate"]["rewritten"] is False
    assert result["fact_gate"]["rewrite_attempted"] is True
    assert "rewrite rejected" in capsys.readouterr().out


def test_an_all_pro_rewrite_off_the_stored_ballot_is_rejected(capsys):
    """audit-fact-gate-2 N1: `_structured_article_errors` returned on the type's own parser first,
    so the stored-ballot branches could never run for the only two types a ballot exists for. With
    the media panel's ballot stored, a rewrite that names another player must be rejected — the
    original ships labelled `flagged` — not adopted and published as `rewritten`."""
    dispatcher = _dispatcher(tools=[])
    dispatcher._all_pro_roster_index = lambda season: {}
    dispatcher._ballot_brief = lambda season, week, at_id: {
        "kind": "all_pro", "stored": [{"award_type": "First Team All-Pro", "slot": "QB", "player_id": 1}],
        "text": "STORED BALLOT RESULTS"}
    first = "# All-Pro Roster\n\n- **QB**: Stored Guy, Carolina Panthers — voted in by the panel"
    off_ballot = "# All-Pro Roster\n\n- **QB**: Other Guy, Chicago Bears — my own pick"
    client = _Client([first, off_ballot])

    def stored_check(text, stored, index):
        return [] if text == first else [
            "First Team All-Pro: Other Guy at QB was not voted in by the media panel"]

    with mock.patch("newsroom.swarm_dispatcher.parse_all_pro", return_value=([], [])), \
         mock.patch("newsroom.swarm_dispatcher.check_all_pro_against_stored", side_effect=stored_check), \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.check", side_effect=[_gate(1), _gate(0)]), \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.rewrite_prompt", return_value="fix it"):
        result = dispatcher._execute_agent(_prompt(article_type="OFFICIAL_AP_ALL_PRO_ROSTER"), 2036, 19,
                                           None, live=True, model="muse-spark-1.3-contributor",
                                           save_to_db=False, client=client)

    assert client.models.calls == 2                   # writer + one correction pass
    assert result["text"] == first                    # the on-ballot original stands
    assert result["fact_gate_status"] == "flagged"
    assert result["fact_gate"]["rewritten"] is False   # off-ballot is never a rewrite
    assert result["fact_gate"]["rewrite_attempted"] is True
    out = capsys.readouterr().out
    assert "rewrite rejected" in out and "not voted in by the media panel" in out


def test_a_week_22_awards_rewrite_off_the_stored_ballot_is_rejected(capsys):
    """audit-fact-gate-2 N1, the awards half: the Week 22 AWARDS_TRACKER rewrite is checked against
    the stored winners, so naming anyone but the panel's winner is rejected."""
    dispatcher = _dispatcher(tools=[])
    dispatcher._all_pro_roster_index = lambda season: {}
    dispatcher._coach_index = lambda season: {}
    dispatcher._ballot_brief = lambda season, week, at_id: {
        "kind": "awards", "stored": {"MVP": {"player_id": 1, "name": "Stored Guy", "team_id": 1}},
        "text": "STORED BALLOT RESULTS"}
    first = "# Awards\n\n- **MVP**: Stored Guy, Carolina Panthers — the panel's winner"
    off_ballot = "# Awards\n\n- **MVP**: Other Guy, Chicago Bears — my own pick"
    client = _Client([first, off_ballot])

    def stored_check(text, stored, roster_index, coach_index):
        return [] if text == first else [
            "MVP: the article names Other Guy, but the media panel's ballots chose Stored Guy"]

    with mock.patch("newsroom.swarm_dispatcher.parse_award_winners", return_value=([], [])), \
         mock.patch("newsroom.swarm_dispatcher.check_awards_against_stored", side_effect=stored_check), \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.check", side_effect=[_gate(1), _gate(0)]), \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.rewrite_prompt", return_value="fix it"):
        result = dispatcher._execute_agent(_prompt(article_type="AWARDS_TRACKER"), 2036, 22, None,
                                           live=True, model="muse-spark-1.3-contributor",
                                           save_to_db=False, client=client)

    assert result["text"] == first
    assert result["fact_gate_status"] == "flagged"
    assert result["fact_gate"]["rewritten"] is False
    assert result["fact_gate"]["rewrite_attempted"] is True
    out = capsys.readouterr().out
    assert "rewrite rejected" in out and "media panel's ballots chose Stored Guy" in out


def test_a_validator_that_raises_rejects_the_rewrite(capsys):
    """audit-fact-gate-2 N3: the structural check caught every exception and returned [], so a
    validator that could not read the rewrite let it through. It now fails closed."""
    dispatcher = _dispatcher(tools=[])
    dispatcher._power_rankings_team_index = lambda: []
    first = "# Power Rankings\n\n1. Bears\n2. Bills\n...\n32. Cardinals"
    broken = "# Power Rankings\n\n1. Bears"
    client = _Client([first, broken])

    def parse(text, index):
        if text == broken:
            raise RuntimeError("parser blew up on the rewrite")
        return [], []

    with mock.patch("newsroom.swarm_dispatcher.parse_power_rankings", side_effect=parse), \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.check", side_effect=[_gate(1), _gate(0)]), \
         mock.patch("newsroom.swarm_dispatcher.fact_gate.rewrite_prompt", return_value="fix it"):
        result = dispatcher._execute_agent(_prompt(article_type="POWER_RANKINGS"), 2036, 18, None,
                                           live=True, model="muse-spark-1.3-contributor",
                                           save_to_db=False, client=client)

    assert result["text"] == first                    # a rewrite nothing could read is not published
    assert result["fact_gate_status"] == "flagged"
    assert result["fact_gate"]["rewritten"] is False
    assert result["fact_gate"]["rewrite_attempted"] is True
    out = capsys.readouterr().out
    assert "rewrite rejected" in out and "could not be validated" in out




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
