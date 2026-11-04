"""Relationship events from the inbox (FRONT_OFFICE_INBOX_PLAN.md §2.5, D15–D18; U1).

The relationships plan's store doesn't exist yet: flush is a guarded no-op, tested here against a fake
personality.relationship_store module and a dict resolver. Nothing writes persona_relationships."""
import sys
import types

import pytest

from engines.inbox import appraisal, store
from tests.test_inbox_store import InboxDB, cur, db, thread_row  # noqa: F401  (fixtures)

ENVELOPE_KEYS = {"schema_version", "universe_id", "event_id", "source_event_id", "source_kind", "source_refs", "kind",
                 "occurred_at", "subject", "target", "actor", "basis", "confidence", "reviewer", "evidence_scope",
                 "visibility", "attribution", "payload", "supersedes_source_event_id"}
POLITE = {"outcome": "positive", "significance": "meaningful", "mistreated": False,
          "reason": "he walked me through his thinking"}
RUDE = {"outcome": "negative", "significance": "routine", "mistreated": True,
        "reason": "brushed off my counter without discussing it"}
AT = {"season": 2026, "week": 5, "offseason_stage": None}


def gm_thread(cur, **kw):
    tid = store.create_thread(cur, thread_row(kind="GM_TALK", counterpart_type="gm", counterpart_id="20",
                                              counterpart_name="Terry Fontenot", source_ref="trade:7", **kw))
    return store.get_thread(cur, tid)


def role_thread(cur):
    tid = store.create_thread(cur, thread_row(kind="ROLE_TALK", actor_role="COACH", actor_id="41",
                                              counterpart_id="101", counterpart_name="Xavier Legette",
                                              source_ref="lost:101"))
    return store.get_thread(cur, tid)


# ------------------------------------------------------------------------------------------------- allowed ---

@pytest.mark.parametrize("view, want", [
    ({"status": "legacy", "role": "gm", "origin": "legacy"}, True),                # GM persona
    ({"status": "legacy", "role": "writer", "origin": "legacy"}, True),            # beat writer
    ({"status": "stored", "role": "player", "origin": "generated"}, True),         # generated player profile
    ({"status": "stored", "role": "coach", "origin": "real", "reviewed": False}, False),   # real coach, unreviewed
    ({"status": "stored", "role": "coach", "origin": "real", "reviewed": True}, True),
    ({"status": "missing", "role": "player", "origin": None}, False),              # profile not loaded
    (None, False),
])
def test_allowed(view, want):
    assert appraisal.allowed(view) is want


# -------------------------------------------------------------------------------------------------- events ---

def test_interaction_event_envelope(cur):
    t = gm_thread(cur)
    ev = appraisal.interaction_event(t, {"id": 812}, None, None, POLITE, AT)
    assert ENVELOPE_KEYS <= set(ev)
    assert ev["kind"] == "meaningful_interaction" and ev["source_event_id"] == "inbox:msg:812"
    assert ev["subject"]["_alias"] == {"role": "gm", "namespace": "db-gm", "external_id": "20"}
    assert ev["target"]["_alias"]["external_id"] == "10" and ev["actor"] == ev["target"]
    assert ev["basis"] == "verified_simulation" and ev["confidence"] == "medium" and ev["visibility"] == "private"
    assert ev["occurred_at"] == {"season": 2026, "tick": 5}
    p = ev["payload"]
    assert p["relationship_kind"] == "support" and p["outcome"] == "positive" and p["significance"] == "meaningful"
    assert p["opportunity_ref"] == "inbox_message:812" and p["appraisal_reason"] == POLITE["reason"]
    assert p["channel"] == "inbox" and p["inbox_kind"] == "GM_TALK"


def test_treatment_event_envelope(cur):
    t = gm_thread(cur)
    ev = appraisal.treatment_event(t, {"id": 813}, None, None, RUDE, AT)
    assert ENVELOPE_KEYS <= set(ev) and ev["kind"] == "treatment"
    p = ev["payload"]
    assert p["treatment_kind"] == "disrespect" and p["outcome"] == "negative" and p["team_key"] == "team:1"
    assert p["outcome_refs"] == ["inbox_message:813"] and p["appraisal_reason"] == RUDE["reason"]
    assert ev["attribution"]["responsible"][0]["_alias"]["external_id"] == "10"


def test_one_source_gives_one_event(cur):
    t = gm_thread(cur)
    assert appraisal.exchange_event(t, {"id": 1}, None, None, RUDE, AT)["kind"] == "treatment"
    assert appraisal.exchange_event(t, {"id": 1}, None, None, POLITE, AT)["kind"] == "meaningful_interaction"
    assert appraisal.exchange_event(t, {"id": 1}, None, None, None, AT) is None          # no model, no appraisal
    assert appraisal.exchange_event(t, {"id": 1}, None, None, {"outcome": "great"}, AT) is None
    first = appraisal.record(cur, appraisal.exchange_event(t, {"id": 1}, None, None, RUDE, AT))
    again = appraisal.record(cur, appraisal.exchange_event(t, {"id": 1}, None, None, POLITE, AT))
    assert first == again
    assert cur.execute("SELECT kind FROM inbox_relationship_events").fetchall() == [("treatment",)]


def test_promise_events(cur):
    t = role_thread(cur)
    made = appraisal.promise_made(t, None, None, {"type": "depth_chart_slot", "slot": "WR", "max_depth": 1},
                                  {"season": 2026, "week": 9})
    assert ENVELOPE_KEYS <= set(made) and made["kind"] == "promise_made"
    p = made["payload"]
    assert p["promise_id"] == f"inbox:{t['id']}:1"
    assert p["issuer"]["_alias"] == {"role": "coach", "namespace": "db-coach", "external_id": "41"}
    assert p["beneficiary"]["_alias"] == {"role": "player", "namespace": "db-player", "external_id": "101"}
    assert p["terms"]["criterion"] == {"type": "depth_chart_slot", "slot": "WR", "max_depth": 1}
    assert p["due_at"] == {"season": 2026, "tick": 9} and p["authority_ref"] == f"inbox_thread:{t['id']}"
    assert made["subject"] == p["beneficiary"] and made["target"] == p["issuer"]
    assert made["source_event_id"] == f"inbox:promise:{t['id']}:1:made"
    promise = {"promise_id": p["promise_id"], "slot": "WR", "max_depth": 1, "due_season": 2026, "due_week": 9}
    kept = appraisal.promise_resolved(t, promise, {"depth": 1, "source": "JR", "season": 2026, "week": 9})
    broken = appraisal.promise_resolved(t, promise, {"depth": 3, "source": "JR", "season": 2026, "week": 9})
    unknown = appraisal.promise_resolved(t, promise, None)
    assert [e["payload"]["resolution"] for e in (kept, broken, unknown)] == ["kept", "broken", "unknown"]
    assert kept["payload"]["criterion_result"] == {"slot": "WR", "depth_at_due": 1, "chart_source": "JR",
                                                   "chart_season": 2026, "chart_week": 9}
    assert kept["source_event_id"] == f"inbox:promise:{t['id']}:1:resolved"
    assert kept["payload"]["beneficiary_ack_ref"] is None


def test_promises_are_recorded_without_a_model(cur):
    t = role_thread(cur)
    made = appraisal.promise_made(t, None, None, {"slot": "WR", "max_depth": 2}, {"season": 2026, "week": 8})
    rid = appraisal.record(cur, made, thread_id=t["id"])
    rows = cur.execute("SELECT id, kind, status, sim_time FROM inbox_relationship_events").fetchall()
    assert rows == [(rid, "promise_made", "PENDING", {"season": 2026, "week": 5, "offseason_stage": None})]


def test_unknown_kind_refused(cur):
    with pytest.raises(ValueError):
        appraisal.record(cur, {"kind": "trust_delta", "source_event_id": "x"})


# --------------------------------------------------------------------------------------------------- flush ---

def test_flush_is_a_noop_without_the_relationships_store(cur, monkeypatch):
    monkeypatch.delitem(sys.modules, "personality.relationship_store", raising=False)
    appraisal.record(cur, appraisal.exchange_event(gm_thread(cur), {"id": 5}, None, None, POLITE, AT))
    got = appraisal.flush(cur)
    assert got["status"] == "relationship_store_missing" and got["appended"] == 0
    assert cur.execute("SELECT status FROM inbox_relationship_events").fetchall() == [("PENDING",)]


class FakeStore(types.ModuleType):
    EVENTS_TABLE = "fake_relationship_events"

    def __init__(self, conflict=False):
        super().__init__("personality.relationship_store")
        self.appended, self.heads, self.conflict = [], [], conflict

    def universe_id(self, cur):
        return "u1"

    def read_head(self, cur, universe_id):
        return "head-7"

    def append_events(self, cur, events, *, expected_head, team_registry):
        if self.conflict:
            raise RuntimeError("head moved")
        self.heads.append(expected_head)
        self.appended += events
        return {"head": "head-8"}


class Ident:
    def __init__(self, pid):
        self.persona_id = pid


def resolver(known):
    return lambda cur, universe, role, namespace, external_id: (
        Ident(f"p-{role}-{external_id}") if (namespace, external_id) in known else None)


@pytest.fixture
def fake_table(db):
    db.conn.execute("CREATE TABLE fake_relationship_events (id INTEGER)")


def test_flush_appends_with_expected_head(cur, fake_table):
    t = gm_thread(cur)
    appraisal.record(cur, appraisal.exchange_event(t, {"id": 5}, None, None, POLITE, AT))
    fake = FakeStore()
    got = appraisal.flush(cur, rel_store=fake, resolver=resolver({("db-gm", "20"), ("db-gm", "10")}))
    assert got["appended"] == 1 and fake.heads == ["head-7"]
    ev = fake.appended[0]
    assert ev["universe_id"] == "u1" and ev["subject"]["persona_id"] == "p-gm-20"
    assert "_alias" not in ev["subject"] and ev["target"]["persona_id"] == "p-gm-10"
    assert cur.execute("SELECT status, status_reason FROM inbox_relationship_events").fetchall() == \
        [("APPENDED", None)]


def test_flush_leaves_unresolved_and_conflicts_pending(cur, fake_table):
    t = gm_thread(cur)
    appraisal.record(cur, appraisal.exchange_event(t, {"id": 5}, None, None, POLITE, AT))
    got = appraisal.flush(cur, rel_store=FakeStore(), resolver=resolver({("db-gm", "10")}))
    assert got["appended"] == 0
    status, reason = cur.execute("SELECT status, status_reason FROM inbox_relationship_events").fetchone()
    assert status == "PENDING" and "db-gm:20" in reason
    got = appraisal.flush(cur, rel_store=FakeStore(conflict=True),
                          resolver=resolver({("db-gm", "20"), ("db-gm", "10")}))
    assert got["status"] == "conflict"
    status, reason = cur.execute("SELECT status, status_reason FROM inbox_relationship_events").fetchone()
    assert status == "PENDING" and "head moved" in reason


def test_nothing_writes_persona_relationships(cur, fake_table):
    t = gm_thread(cur)
    appraisal.record(cur, appraisal.exchange_event(t, {"id": 5}, None, None, RUDE, AT))
    appraisal.flush(cur, rel_store=FakeStore(), resolver=resolver({("db-gm", "20"), ("db-gm", "10")}))
    assert not any("persona_relationships" in s for s in cur.statements)
    import inspect
    for mod in ("appraisal", "voice", "scan", "store", "actors"):
        src = inspect.getsource(__import__(f"engines.inbox.{mod}", fromlist=["x"]))
        assert "INSERT INTO persona_relationships" not in src and "UPDATE persona_relationships" not in src
