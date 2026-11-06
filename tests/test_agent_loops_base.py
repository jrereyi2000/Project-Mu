"""Agent loop base (COACH_PLAYER_AGENT_LOOPS_PLAN.md §2.1, §2.4, §4). Synthetic: no database, no model."""
from pathlib import Path
import json

import pytest

from engines.agent_loops import base

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "db" / "migrations" / "2027_02_01_agent_loops.sql"

OPTS = [base.Option("A", "KEEP", {}, "Keep it"), base.Option("B", "SWAP", {"x": 1}, "Swap"),
        base.Option("C", "SWAP", {"x": 2}, "Other swap")]
FACTS = {"team": "Carolina", "players": ["Bryce Young", "Andy Dalton"], "slot": "QB", "offer": 12500000}


class FakeLLM:
    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, prompt):
        self.calls.append(prompt)
        return self.answer


def _decide(store, **kw):
    args = dict(league_id=1, season=2026, tick=5, kind="CHART", subject="team:5:chart", options=OPTS,
                persona={"sliders": {"warmth": 5}, "traits": [], "persona_type": "coach", "persona_id": "c1"},
                weights={"A": 1, "B": 1, "C": 1}, facts=FACTS, seen=False, llm=None, store=store)
    args.update(kw)
    return base.decide(None, **args)


def test_seeded_policy_is_deterministic():
    picks = {base.policy_choice(OPTS, {}, {"A": 1, "B": 1, "C": 1}, base.rng_for(2026, 5, "CHART", "s"))
             for _ in range(20)}
    assert len(picks) == 1
    spread = {base.policy_choice(OPTS, {}, {"A": 1, "B": 1, "C": 1}, base.rng_for(2026, t, "CHART", "s"))
              for t in range(1, 40)}
    assert spread == {"A", "B", "C"}
    assert base.seed_for(2026, 5, "CHART", "s") == "loop:2026:5:CHART:s"


def test_policy_weights_zero_and_callable():
    assert base.policy_choice(OPTS, {}, {}, base.rng_for(1, 1, "CHART", "x")) == "A"
    assert base.policy_choice(OPTS, {}, {"C": 2.0}, base.rng_for(1, 1, "CHART", "x")) == "C"
    assert base.policy_choice(OPTS, {}, lambda o, p: 1.0 if o["key"] == "B" else 0.0,
                              base.rng_for(1, 1, "CHART", "x")) == "B"
    with pytest.raises(base.AgentLoopError):
        base.policy_choice(OPTS, {}, {"A": -1}, base.rng_for(1, 1, "CHART", "x"))
    with pytest.raises(base.AgentLoopError):
        base.policy_choice([OPTS[0], OPTS[0]], {}, {}, base.rng_for(1, 1, "CHART", "x"))


def test_unseen_never_calls_the_model():
    llm = FakeLLM('{"choice": "B", "message": "Bryce Young starts."}')
    row = _decide(base.MemoryStore(), llm=llm, seen=False)
    assert llm.calls == [] and row["choice_source"] == "POLICY" and row["text"] is None
    assert row["chosen_key"] == row["policy_key"]


def test_seen_uses_the_model_and_keeps_grounded_text():
    llm = FakeLLM('```json\n{"choice": "B", "message": "Bryce Young is my QB. Carolina moves on."}\n```')
    row = _decide(base.MemoryStore(), llm=llm, seen=True)
    assert len(llm.calls) == 1 and "B: Swap" in llm.calls[0]
    assert row["chosen_key"] == "B" and row["choice_source"] == "MODEL"
    assert row["text"] == "Bryce Young is my QB. Carolina moves on."
    assert row["option"]["detail"] == {"x": 1}


def test_model_key_outside_options_falls_back():
    llm = FakeLLM('{"choice": "Z", "message": "Bryce Young starts."}')
    row = _decide(base.MemoryStore(), llm=llm, seen=True)
    assert row["choice_source"] == "MODEL_REJECTED" and row["chosen_key"] == row["policy_key"]
    assert row["text"] is None
    garbled = _decide(base.MemoryStore(), llm=FakeLLM("no json here"), seen=True)
    assert garbled["choice_source"] == "MODEL_REJECTED"


def test_failed_call_keeps_the_policy():
    def boom(prompt):
        raise RuntimeError("down")
    row = _decide(base.MemoryStore(), llm=boom, seen=True)
    assert row["choice_source"] == "POLICY" and row["chosen_key"] == row["policy_key"]


def test_ungrounded_text_is_dropped():
    for msg in ("Tom Brady starts at QB.", "Bryce Young wants 40 million.", "Bryce Young moves to WR."):
        row = _decide(base.MemoryStore(), llm=FakeLLM(json.dumps({"choice": "B", "message": msg})), seen=True)
        assert row["chosen_key"] == "B" and row["text"] is None, msg
    ok = _decide(base.MemoryStore(), llm=FakeLLM(json.dumps({"choice": "B",
                                                              "message": "I asked for $12.5M. I'm worth it."})),
                 seen=True)
    assert ok["text"] == "I asked for $12.5M. I'm worth it."


def test_grounded_helper():
    assert base.grounded("Andy Dalton backs up.", FACTS)
    assert not base.grounded("", FACTS)
    assert not base.grounded("Andy Dalton backs up Cam Newton.", FACTS)
    assert base.grounded("We offered 12,500,000 dollars.", FACTS)
    assert not base.grounded("We offered 13,000,000 dollars.", FACTS)


def test_replay_returns_the_stored_decision_without_a_model_call():
    store = base.MemoryStore()
    first = _decide(store, llm=FakeLLM('{"choice": "C", "message": "Andy Dalton starts."}'), seen=True)
    llm = FakeLLM('{"choice": "A", "message": "x"}')
    again = _decide(store, llm=llm, seen=True, weights={"A": 5})
    assert llm.calls == [] and again["replayed"] and not first["replayed"]
    assert again["chosen_key"] == "C" and again["text"] == first["text"]


def test_marquee_budget_stops_at_the_weekly_cap():
    store = base.MemoryStore()
    llm = FakeLLM('{"choice": "B", "message": "Bryce Young starts."}')
    rows = [_decide(store, subject=f"team:{i}:chart", llm=llm, marquee=True) for i in range(base.MARQUEE_PER_WEEK + 2)]
    assert len(llm.calls) == base.MARQUEE_PER_WEEK
    assert [r["choice_source"] for r in rows].count("MODEL") == base.MARQUEE_PER_WEEK
    assert rows[-1]["choice_source"] == "POLICY"
    # a new week has a fresh budget; seen cases (JR's threads, leaks) never count against it
    assert _decide(store, subject="team:0:chart", tick=6, llm=llm, marquee=True)["choice_source"] == "MODEL"
    assert _decide(store, kind="INBOX_REPLY", subject="thread:1:msg:1", llm=llm, seen=True)["choice_source"] == "MODEL"
    assert base.marquee_available(store, 1, 2026, 5) is False


def test_decide_validates():
    with pytest.raises(base.AgentLoopError):
        _decide(base.MemoryStore(), kind="NOPE")
    with pytest.raises(base.AgentLoopError):
        _decide(base.MemoryStore(), options=[])
    with pytest.raises(base.AgentLoopError):
        _decide(base.MemoryStore(), visibility="secret")


def test_row_shape_matches_the_table():
    row = _decide(base.MemoryStore(), cause_refs=["x:1"], visibility="public")
    assert set(base.ROW_FIELDS) <= set(row)
    assert row["persona_type"] == "coach" and row["persona_id"] == "c1" and row["visibility"] == "public"
    assert row["options"][1] == {"key": "B", "action": "SWAP", "detail": {"x": 1}, "label": "Swap"}


class FakeCursor:
    """Just enough of psycopg for SqlStore: one table keyed on the unique columns."""

    def __init__(self):
        self.rows, self.result = {}, None

    def execute(self, sql, params):
        if sql.startswith("INSERT"):
            key = tuple(params[:5])
            new = key not in self.rows
            if new:
                self.rows[key] = [p.obj if hasattr(p, "obj") else p for p in params]
            self.result = [(1,)] if new else []
        elif "count(*)" in sql:
            lid, season, tick, kinds = params
            self.result = [(sum(1 for k, r in self.rows.items() if k[:3] == (lid, season, tick)
                                and k[3] in kinds and r[10] != "POLICY"),)]
        elif "subject_key = %s" in sql:
            self.result = [tuple(self.rows[tuple(params)])] if tuple(params) in self.rows else []
        else:
            lid, season, kind = params
            self.result = [tuple(r) for k, r in sorted(self.rows.items()) if (k[0], k[1], k[3]) == (lid, season, kind)]

    def fetchone(self):
        return self.result[0] if self.result else None

    def fetchall(self):
        return list(self.result)


def test_sql_store_round_trip():
    cur = FakeCursor()
    first = base.decide(cur, league_id=1, season=2026, tick=5, kind="CHART", subject="team:5:chart", options=OPTS,
                        persona={}, weights={"A": 1}, facts=FACTS, seen=False, llm=None)
    again = base.decide(cur, league_id=1, season=2026, tick=5, kind="CHART", subject="team:5:chart", options=OPTS,
                        persona={}, weights={"B": 1}, facts=FACTS, seen=False, llm=None)
    assert first["chosen_key"] == again["chosen_key"] == "A" and again["replayed"]
    assert base.SqlStore(cur).find(1, 2026, "CHART")[0]["subject_key"] == "team:5:chart"
    assert base.SqlStore(cur).count_model(1, 2026, 5) == 0


def test_migration_text_matches_the_definition():
    sql = MIGRATION.read_text()
    assert sql.count("BEGIN;") == 1 and sql.rstrip().endswith("COMMIT;")
    for needle in ("CREATE TABLE IF NOT EXISTS agent_loop_decisions",
                   "id BIGSERIAL PRIMARY KEY", "league_id BIGINT NOT NULL", "tick SMALLINT NOT NULL",
                   "CHECK (kind IN ('CHART','PROMISE','PROMISE_REVIEW','CONTRACT','INBOX_REPLY','LEAK'))",
                   "CHECK (choice_source IN ('POLICY','MODEL','MODEL_REJECTED'))",
                   "visibility TEXT NOT NULL DEFAULT 'private' CHECK (visibility IN ('private','public'))",
                   "cause_refs JSONB NOT NULL DEFAULT '[]'",
                   "UNIQUE (league_id, season, tick, kind, subject_key)",
                   "CREATE INDEX IF NOT EXISTS agent_loop_decisions_public",
                   "ON agent_loop_decisions (league_id, season, tick) WHERE visibility = 'public'",
                   "incompatible definition"):
        assert needle in sql, needle
    assert "INSERT INTO" not in sql
    for kind in base.KINDS:
        assert f"'{kind}'" in sql
    for col in base.ROW_FIELDS:
        assert f"{col} " in sql, col
