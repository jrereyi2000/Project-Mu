"""ROLE_TALK, the coach-player kind (FRONT_OFFICE_INBOX_PLAN.md §2.2–§2.8, §4; U3).

Offline: the real InboxDB SQLite fake from tests/test_inbox_store.py, extended with depth_charts and the roster
columns the staff chart needs. No chart is ever written; every test checks that too where it matters.
"""
import json

import pytest

from engines.inbox import InboxError, actors, registry, store
from engines.inbox.kinds import role_talk
from engines.inbox.registry import ActResult
from tests.test_inbox_store import InboxDB

SEASON, WEEK, CAR = 2026, 5, 1

EXTRA_DDL = """
CREATE TABLE depth_charts (
    team_id INT, season INT, week INT, position_slot TEXT, depth INT, player_id INT, source TEXT,
    PRIMARY KEY (team_id, season, week, source, position_slot, depth));
CREATE TABLE franchise_pending_moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT, team_id INT, kind TEXT, status TEXT, payload TEXT, reason TEXT,
    written_run INT);
ALTER TABLE players ADD COLUMN full_name TEXT;
ALTER TABLE player_seasons ADD COLUMN position TEXT;
ALTER TABLE player_seasons ADD COLUMN age INT;
ALTER TABLE player_seasons ADD COLUMN overall_rating INT;
ALTER TABLE player_seasons ADD COLUMN years_of_experience INT;
ALTER TABLE player_seasons ADD COLUMN archetype TEXT;
ALTER TABLE player_seasons ADD COLUMN dev_trait TEXT;
ALTER TABLE player_seasons ADD COLUMN released_week INT;
"""
ROSTER = """
UPDATE players SET full_name = player_name;
UPDATE player_seasons SET position = 'QB', age = 25, overall_rating = 90 WHERE player_id = 100;
UPDATE player_seasons SET position = 'WR', age = 25, overall_rating = 80 WHERE player_id = 101;
UPDATE player_seasons SET position = 'RB', age = 24, overall_rating = 85 WHERE player_id = 200;
INSERT INTO players (id, player_name, full_name) VALUES (102, 'Andy Dalton', 'Andy Dalton');
INSERT INTO player_seasons (player_id, team_id, season, position, age, overall_rating)
    VALUES (102, 1, 2026, 'QB', 38, 70);
"""


@pytest.fixture
def db(monkeypatch):
    d = InboxDB()
    monkeypatch.setattr(actors, "user_teams", lambda: [t.upper() for t in d.user_teams])
    d.conn.executescript(EXTRA_DDL)
    d.conn.executescript(ROSTER)
    return d


@pytest.fixture
def cur(db):
    return db.cursor()


def team(cur, role="COACH", pid="41", week=WEEK):
    actors.take_control(cur, "CAR", role, pid)
    a = actors.actor_for(cur, "CAR", role)
    return {"team_id": CAR, "abbr": "CAR", "season": SEASON, "week": week, "offseason_stage": None,
            "actors": actors.controlled(cur, "CAR"), "actor": a}


def put_chart(db, week, source, chart, slot=None):
    rows = []
    for s, ids in chart.items():
        for depth, pid in enumerate(ids, start=1):
            rows.append((CAR, SEASON, week, s, depth, pid, source))
    db.conn.executemany("INSERT INTO depth_charts VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def make_thread(cur, source_ref="lost:100", week=WEEK, state=None, player="100", name="Bryce Young"):
    tid = store.create_thread(cur, {
        "team_id": CAR, "kind": "ROLE_TALK", "actor_role": "COACH", "actor_id": "41",
        "counterpart_type": "player", "counterpart_id": player, "counterpart_name": name,
        "subject": f"{name}'s role", "started_by": "COUNTERPART", "source_ref": source_ref,
        "season": SEASON, "week": week, "state": state or {"topic": "LOST_STARTING_JOB", "slot": "QB"},
    })
    return store.get_thread(cur, tid)


def chart_count(db):
    return db.rows("SELECT count(*) FROM depth_charts")[0][0]


# --------------------------------------------------------------------------------------------------- scan ---

def test_only_coach_actors():
    assert role_talk.ACTOR_ROLES == ("COACH",)
    assert not registry.serves(role_talk, "GM")
    assert registry.serves(role_talk, "COACH")


def test_lost_starting_job_trigger(db, cur):
    put_chart(db, 4, "MU_COACH", {"QB": [100]})
    put_chart(db, 5, "MU_COACH", {"QB": [102]})
    triggers = role_talk.scan(cur, SEASON, WEEK, team(cur))
    lost = [t for t in triggers if t.intent == "LOST_STARTING_JOB"]
    assert len(lost) == 1
    t = lost[0]
    assert t.kind == "ROLE_TALK" and t.counterpart_id == "100" and t.counterpart_name == "Bryce Young"
    assert t.facts["slot"] == "QB" and t.facts["last_week"] == 4 and t.facts["player_id"] == 100
    assert t.source_ref == "role:lost:100:2026:5" and t.actor_role == "COACH" and t.actor_id == "41"
    assert t.state["topic"] == "LOST_STARTING_JOB" and t.state["slot"] == "QB"


def test_no_lost_job_while_he_is_still_starting(db, cur):
    put_chart(db, 4, "MU_COACH", {"QB": [100]})
    put_chart(db, 5, "MU_COACH", {"QB": [100, 102]})
    assert [t for t in role_talk.scan(cur, SEASON, WEEK, team(cur)) if t.intent == "LOST_STARTING_JOB"] == []


def test_wants_more_needs_two_weeks_of_staff_start_and_jr_not(db, cur):
    put_chart(db, 4, "MU_COACH", {"QB": [100]})       # the staff's chart last week
    put_chart(db, 4, "JR", {"QB": [102]})              # JR's chart last week: not him
    put_chart(db, 5, "JR", {"QB": [102]})              # JR's chart this week: not him
    triggers = role_talk.scan(cur, SEASON, WEEK, team(cur))
    wants = [t for t in triggers if t.intent == "WANTS_MORE"]
    assert len(wants) == 1 and wants[0].facts["slot"] == "QB" and wants[0].facts["jr_depth"] is None
    assert wants[0].source_ref == "role:wants:100:QB:2026:5"
    assert not [t for t in triggers if t.intent == "LOST_STARTING_JOB"]


def test_no_wants_more_when_jr_also_starts_him(db, cur):
    put_chart(db, 4, "MU_COACH", {"QB": [100]})
    put_chart(db, 4, "JR", {"QB": [100]})
    put_chart(db, 5, "JR", {"QB": [100]})
    assert [t for t in role_talk.scan(cur, SEASON, WEEK, team(cur)) if t.intent == "WANTS_MORE"] == []


def test_no_triggers_without_a_controlled_coach(cur):
    out = role_talk.scan(cur, SEASON, WEEK, {"team_id": CAR, "abbr": "CAR", "season": SEASON, "week": WEEK,
                                             "actors": [], "actor": None})
    assert out == []


# ------------------------------------------------------------------------------------------------ options ---

def test_options_and_default_review_week(cur):
    t = make_thread(cur, week=6, state={"topic": "WANTS_MORE", "slot": "WR"})
    opts = {o["id"]: o for o in role_talk.options(cur, t, None)}
    assert set(opts) == {"PROMISE", "EXPLAIN", "NO_CHANGE"}
    assert opts["PROMISE"]["form"] == "promise"
    assert opts["PROMISE"]["default"] == {"slot": "WR", "max_depth": 1, "due_week": 8}
    assert opts["EXPLAIN"]["needs_note"] is True
    assert opts["NO_CHANGE"]["needs_note"] is False


# ---------------------------------------------------------------------------------------------------- act ---

def test_promise_records_promise_made_and_never_writes_a_chart(db, cur):
    put_chart(db, 5, "JR", {"WR": [101, 100]})
    t = make_thread(cur, source_ref="wants:101", state={"topic": "WANTS_MORE", "slot": "WR"},
                    player="101", name="Xavier Legette")
    depth_before, moves_before = chart_count(db), db.rows("SELECT count(*) FROM franchise_pending_moves")[0][0]
    res = role_talk.act(cur, t, "PROMISE", {"slot": "WR", "max_depth": 1, "due_week": 7}, None)
    assert isinstance(res, ActResult) and res.close and res.intent == "PROMISE"
    p = res.state["promises"][0]
    assert p == {"promise_id": f"inbox:{t['id']}:1", "slot": "WR", "max_depth": 1, "due_season": SEASON,
                 "due_week": 7, "made_at_depth": 1}
    assert res.engine_ref == {"promise_id": p["promise_id"]}
    rows = cur.execute("SELECT kind, event FROM inbox_relationship_events").fetchall()
    assert len(rows) == 1 and rows[0][0] == "promise_made"
    ev = rows[0][1]
    assert ev["payload"]["promise_id"] == p["promise_id"]
    assert ev["payload"]["issuer"]["_alias"] == {"role": "coach", "namespace": "db-coach", "external_id": "41"}
    assert ev["payload"]["beneficiary"]["_alias"] == {"role": "player", "namespace": "db-player",
                                                       "external_id": "101"}
    assert ev["payload"]["terms"]["criterion"] == {"type": "depth_chart_slot", "slot": "WR", "max_depth": 1}
    assert ev["payload"]["due_at"] == {"season": SEASON, "tick": 7}
    assert ev["payload"]["authority_ref"] == f"inbox_thread:{t['id']}"
    assert chart_count(db) == depth_before                      # no depth chart write (D11)
    assert db.rows("SELECT count(*) FROM franchise_pending_moves")[0][0] == moves_before


def test_a_promise_made_in_a_later_week_uses_the_league_week(db, cur):
    """The thread's creation week is not "this week": JR can answer a talk opened two weeks ago."""
    t = make_thread(cur, source_ref="wants:101", state={"topic": "WANTS_MORE", "slot": "WR"},
                    player="101", name="Xavier Legette")
    db.conn.execute("UPDATE league_state SET current_week = 7")
    with pytest.raises(InboxError):
        role_talk.act(cur, t, "PROMISE", {"slot": "WR", "max_depth": 1, "due_week": 7}, None)
    res = role_talk.act(cur, t, "PROMISE", {"slot": "WR", "max_depth": 1, "due_week": 8}, None)
    assert res.state["promises"][0]["due_week"] == 8
    ev = cur.execute("SELECT event FROM inbox_relationship_events").fetchone()[0]
    assert ev["payload"]["due_at"] == {"season": SEASON, "tick": 8}


def test_promise_validation(cur):
    t = make_thread(cur)
    with pytest.raises(InboxError):
        role_talk.act(cur, t, "PROMISE", {"slot": "WR", "max_depth": 1, "due_week": 5}, None)   # not after this week
    with pytest.raises(InboxError):
        role_talk.act(cur, t, "PROMISE", {"slot": "NOT_A_SLOT", "max_depth": 1, "due_week": 7}, None)
    with pytest.raises(InboxError):
        role_talk.act(cur, t, "PROMISE", {"slot": "WR", "max_depth": 0, "due_week": 7}, None)
    with pytest.raises(InboxError):
        role_talk.act(cur, t, "EXPLAIN", {}, None)              # a note is required
    with pytest.raises(InboxError):
        role_talk.act(cur, t, "SHOUT", {}, None)


def test_explain_and_no_change_close_the_thread(cur):
    t = make_thread(cur)
    out = role_talk.act(cur, t, "EXPLAIN", {}, "He's behind two veterans right now.")
    assert out.intent == "EXPLAIN" and out.close and out.facts["explanation"].startswith("He's behind")
    out = role_talk.act(cur, t, "NO_CHANGE", {}, None)
    assert out.intent == "NO_CHANGE" and out.close


# ----------------------------------------------------------------------------------------- promise review ---

def _promised_thread(cur, source_ref="promise-kept", slot="WR", max_depth=1):
    t = make_thread(cur, source_ref=source_ref, state={"topic": "WANTS_MORE", "slot": slot},
                    player="101", name="Xavier Legette")
    res = role_talk.act(cur, t, "PROMISE", {"slot": slot, "max_depth": max_depth, "due_week": 7}, None)
    store.update_state(cur, t["id"], res.state)
    store.close_thread(cur, t["id"])
    return store.get_thread(cur, t["id"])


def test_due_promise_kept_and_review_runs_once(db, cur):
    t = _promised_thread(cur)
    put_chart(db, 7, "MU_COACH", {"WR": [101]})
    assert role_talk.review_promises(cur, SEASON, 7, {"team_id": CAR, "abbr": "CAR"}) == 1
    events = cur.execute("SELECT kind, event FROM inbox_relationship_events WHERE kind = 'promise_resolved'").fetchall()
    assert len(events) == 1
    ev = events[0][1]
    assert ev["payload"]["resolution"] == "kept"
    assert ev["payload"]["criterion_result"] == {"slot": "WR", "depth_at_due": 1, "chart_source": "MU_COACH",
                                                 "chart_season": SEASON, "chart_week": 7}
    system = [m for m in store.messages(cur, t["id"]) if m["author"] == "SYSTEM"]
    assert len(system) == 1 and "was kept" in system[0]["body"]
    assert store.get_thread(cur, t["id"])["state"]["promises"][0]["resolution"] == "kept"
    # exactly once
    assert role_talk.review_promises(cur, SEASON, 8, {"team_id": CAR, "abbr": "CAR"}) == 0
    assert len(cur.execute("SELECT id FROM inbox_relationship_events WHERE kind = 'promise_resolved'").fetchall()) == 1
    assert len([m for m in store.messages(cur, t["id"]) if m["author"] == "SYSTEM"]) == 1


def test_due_promise_broken(db, cur):
    t = _promised_thread(cur)
    put_chart(db, 7, "JR", {"WR": [100, 101]})
    assert role_talk.review_promises(cur, SEASON, 7, {"team_id": CAR, "abbr": "CAR"}) == 1
    ev = cur.execute("SELECT event FROM inbox_relationship_events WHERE kind = 'promise_resolved'").fetchone()[0]
    assert ev["payload"]["resolution"] == "broken"
    assert ev["payload"]["criterion_result"]["depth_at_due"] == 2
    assert any("not kept" in m["body"] for m in store.messages(cur, t["id"]) if m["author"] == "SYSTEM")


def test_an_overdue_promise_is_judged_on_the_chart_at_its_due_week(db, cur):
    """A promotion made after the deadline must not turn a broken promise into a kept one."""
    t = _promised_thread(cur)                                    # due week 7
    put_chart(db, 7, "MU_COACH", {"WR": [100, 101]})             # still second on the chart at the deadline
    put_chart(db, 8, "MU_COACH", {"WR": [101]})                  # promoted a week too late
    assert role_talk.review_promises(cur, SEASON, 8, {"team_id": CAR, "abbr": "CAR"}) == 1
    ev = cur.execute("SELECT event FROM inbox_relationship_events WHERE kind = 'promise_resolved'").fetchone()[0]
    assert ev["payload"]["resolution"] == "broken"
    assert ev["payload"]["criterion_result"]["chart_week"] == 7


def test_due_promise_unknown_without_a_chart(db, cur):
    t = _promised_thread(cur)                     # the depth_charts table exists but has no rows
    assert role_talk.review_promises(cur, SEASON, 7, {"team_id": CAR, "abbr": "CAR"}) == 1
    ev = cur.execute("SELECT event FROM inbox_relationship_events WHERE kind = 'promise_resolved'").fetchone()[0]
    assert ev["payload"]["resolution"] == "unknown"
    assert ev["payload"]["criterion_result"]["depth_at_due"] is None
    assert any("Couldn't check" in m["body"] for m in store.messages(cur, t["id"]) if m["author"] == "SYSTEM")


def test_future_promise_is_not_reviewed_early(db, cur):
    t = _promised_thread(cur)
    put_chart(db, 6, "MU_COACH", {"WR": [101]})
    assert role_talk.review_promises(cur, SEASON, 6, {"team_id": CAR, "abbr": "CAR"}) == 0
    assert cur.execute("SELECT id FROM inbox_relationship_events WHERE kind = 'promise_resolved'").fetchall() == []


# --------------------------------------------------------------------------------------------------- start ---

def test_start_names_the_player(cur):
    t = role_talk.start(cur, dict(team(cur), week=6), {"player_id": 101, "topic": "Snaps"})
    assert t.counterpart_id == "101" and t.subject == "Snaps"
    assert t.source_ref == "role:jr:101:2026:6" and t.state["slot"] is None


def test_start_refuses_a_player_the_picker_would_not_offer(db, cur):
    """The picker lists ACTIVE players who aren't on IR; a typed id has to be held to the same rule."""
    db.conn.executescript("""
        CREATE TABLE player_contracts (id INTEGER PRIMARY KEY, player_id INT, team_id INT, season INT, status TEXT);
        CREATE TABLE injuries (id INTEGER PRIMARY KEY, player_id INT, team_id INT, season INT, week INT, is_ir INT);
        INSERT INTO player_contracts (player_id, team_id, season, status) VALUES (100, 1, 2026, 'ACTIVE');
        INSERT INTO player_contracts (player_id, team_id, season, status) VALUES (101, 1, 2026, 'PRACTICE_SQUAD');
        INSERT INTO player_contracts (player_id, team_id, season, status) VALUES (102, 1, 2026, 'ACTIVE');
        INSERT INTO injuries (player_id, team_id, season, week, is_ir) VALUES (102, 1, 2026, 5, 1);
    """)
    assert role_talk.start(cur, dict(team(cur), week=6), {"player_id": 100}).counterpart_id == "100"
    for pid in (101, 102):                                        # practice squad, then on IR
        with pytest.raises(InboxError) as e:
            role_talk.start(cur, dict(team(cur), week=6), {"player_id": pid})
        assert e.value.status == 409 and "active roster" in e.value.message


def test_start_declares_the_player_pick_form(cur):
    assert role_talk.START_FORM == "player_pick"


def test_start_uses_the_typed_note_as_the_topic(cur):
    t = role_talk.start(cur, dict(team(cur), week=6), {"player_id": 101, "note": "More targets"})
    assert t.subject == "More targets" and t.facts["topic"] == "More targets" and t.state["topic"] == "JR_STARTED"
