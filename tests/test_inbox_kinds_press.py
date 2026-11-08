"""The PRESS inbox notice (docs/in_progress/PRESS_CONFERENCE_PLAN.md §2.13; U8).

Offline: the inbox SQLite fake shared by the other `tests/test_inbox_*.py` files, plus the real press migration
(dialect-translated: uppercase NOW(), the JSONB cast). No PostgreSQL connection is opened and no live database is
touched.
"""
import pathlib

import pytest

from engines.inbox import InboxError, actors, registry, store
from engines.inbox import scan as scan_mod
from engines.inbox.kinds import press
from tests.test_inbox_store import InboxDB, sqlite_ddl

SEASON = 2026
WEEK = 5
ROOT = pathlib.Path(__file__).resolve().parent.parent
PRESS_MIGRATION = ROOT / "db" / "migrations" / "2027_01_20_press_conferences.sql"


def load_press(db):
    """Apply the real press migration's tables to the in-memory SQLite database."""
    sql = PRESS_MIGRATION.read_text().replace("NOW()", "CURRENT_TIMESTAMP").replace("::jsonb", "")
    db.conn.executescript(sqlite_ddl(sql))


def add_conference(db, cid, *, team_id=1, season=SEASON, week=WEEK, controller="USER", status="SCHEDULED",
                   subject_type="coach", subject_id=41, subject_name="Dave Canales"):
    db.conn.execute(
        "INSERT INTO press_conferences (id, season, week, game_id, team_id, subject_type, subject_id, "
        "subject_name, controller, status, facts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, season, week, 1000 + cid, team_id, subject_type, subject_id, subject_name, controller, status, "{}"))


@pytest.fixture
def db(monkeypatch):
    d = InboxDB()
    monkeypatch.setattr(actors, "user_teams", lambda: [t.upper() for t in d.user_teams])
    return d


@pytest.fixture
def cur(db):
    return db.cursor()


def team(cur, week=WEEK):
    acts = actors.controlled(cur, "CAR")
    return {"team_id": 1, "abbr": "CAR", "season": SEASON, "week": week, "offseason_stage": None,
            "actors": acts, "actor": acts[0] if acts else None}


def open_notice(db, cur, cid):
    """Scan, open the notice thread, and return (trigger, actor, thread_id)."""
    actors.take_control(cur, "CAR", "COACH", "41")
    trigger = press.scan(cur, SEASON, WEEK, team(cur))[0]
    actor = actors.controlled(cur, "CAR")[0]
    tid = scan_mod.open_thread(cur, press, trigger, actor, SEASON, WEEK)
    return trigger, actor, tid


# ---------------------------------------------------------------------------------------------------- scan ---

def test_the_kind_is_discovered():
    assert registry.kinds(refresh=True).get("PRESS") is press
    assert press.KIND == "PRESS" and press.COUNTERPART_TYPE == "system"
    assert press.CAN_START is False and press.ACTOR_ROLES == ("COACH",)


def test_scan_opens_one_notice_per_open_user_presser(db, cur):
    load_press(db)
    add_conference(db, 11, status="SCHEDULED")
    add_conference(db, 12, status="LIVE")
    add_conference(db, 13, status="REVIEW")
    add_conference(db, 14, status="CLOSED")
    add_conference(db, 15, status="DECLINED")
    add_conference(db, 16, status="SCHEDULED", controller="AGENT")     # an AI presser: never a notice
    add_conference(db, 17, status="SCHEDULED", team_id=2)              # another team's presser
    actors.take_control(cur, "CAR", "COACH", "41")

    triggers = press.scan(cur, SEASON, WEEK, team(cur))

    assert [t.source_ref for t in triggers] == ["press:11", "press:12", "press:13"]
    for trig in triggers:
        assert trig.kind == "PRESS" and trig.counterpart_type == "system"
        assert trig.counterpart_id == "press" and trig.counterpart_name == "Press Room"
        assert trig.link_table == "press_conferences" and trig.link_id in (11, 12, 13)
        assert trig.actor_role == "COACH" and trig.actor_id == "41"
        assert trig.subject == "Week 5 postgame press conference"


def test_ai_pressers_never_produce_a_notice(db, cur):
    load_press(db)
    add_conference(db, 21, controller="AGENT", status="LIVE")
    actors.take_control(cur, "CAR", "COACH", "41")
    assert press.scan(cur, SEASON, WEEK, team(cur)) == []


def test_scan_is_empty_without_the_press_tables(cur):
    # InboxDB has no press_conferences table: an unmigrated database scans to nothing, it never raises.
    actors.take_control(cur, "CAR", "COACH", "41")
    assert press.scan(cur, SEASON, WEEK, team(cur)) == []


def test_scan_is_idempotent_once_the_notice_is_open(db, cur):
    load_press(db)
    add_conference(db, 31)
    trigger, actor, tid = open_notice(db, cur, 31)
    assert tid is not None
    # The same trigger again creates nothing (the store is unique on team, kind, source_ref).
    assert scan_mod.open_thread(cur, press, trigger, actor, SEASON, WEEK) is None
    assert [t.source_ref for t in press.scan(cur, SEASON, WEEK, team(cur))] == ["press:31"]


# ---------------------------------------------------------------------------------------------- the thread ---

def test_open_thread_stores_the_notice_and_links_the_conference(db, cur):
    load_press(db)
    add_conference(db, 41)
    trigger, actor, tid = open_notice(db, cur, 41)

    thread = store.get_thread(cur, tid)
    assert thread["kind"] == "PRESS" and thread["counterpart_type"] == "system"
    assert thread["link_table"] == "press_conferences" and thread["link_id"] == 41
    assert thread["source_ref"] == "press:41"
    assert store.messages(cur, tid)[0]["body"]


@pytest.mark.parametrize("final", ["CLOSED", "DECLINED"])
def test_the_notice_closes_with_the_presser(db, cur, final):
    load_press(db)
    add_conference(db, 51)
    _, _, tid = open_notice(db, cur, 51)
    assert store.get_thread(cur, tid)["status"] in store.OPEN_STATUSES

    db.conn.execute("UPDATE press_conferences SET status = ? WHERE id = 51", (final,))
    assert press.scan(cur, SEASON, WEEK + 1, team(cur, WEEK + 1)) == []
    assert store.get_thread(cur, tid)["status"] == "CLOSED"


def test_the_notice_closes_when_the_conference_is_gone(db, cur):
    load_press(db)
    add_conference(db, 61)
    _, _, tid = open_notice(db, cur, 61)

    db.conn.execute("DELETE FROM press_conferences WHERE id = 61")
    assert press.scan(cur, SEASON, WEEK + 1, team(cur, WEEK + 1)) == []
    assert store.get_thread(cur, tid)["status"] == "CLOSED"


# ----------------------------------------------------------------------------------------------- options ---

def test_the_only_option_is_open_presser(db, cur):
    load_press(db)
    add_conference(db, 71)
    _, _, tid = open_notice(db, cur, 71)
    thread = store.get_thread(cur, tid)
    assert [o["id"] for o in press.options(cur, thread, None)] == ["OPEN_PRESSER"]


def test_act_accepts_open_presser_only(db, cur):
    load_press(db)
    add_conference(db, 81)
    _, _, tid = open_notice(db, cur, 81)
    thread = store.get_thread(cur, tid)

    result = press.act(cur, thread, "OPEN_PRESSER", {}, None)
    assert result.close is False and result.judge is False
    assert result.engine_ref == {"press_conference": 81}
    with pytest.raises(InboxError) as err:
        press.act(cur, thread, "TAKE_PODIUM", {}, None)
    assert err.value.status == 400


def test_exchange_records_the_open_and_keeps_the_notice_open(db, cur):
    from engines.inbox import voice

    load_press(db)
    add_conference(db, 91)
    _, _, tid = open_notice(db, cur, 91)

    out = voice.exchange(cur, press, tid, "OPEN_PRESSER", note=None, llm=None)

    assert out["status"] == "WAITING_JR"                       # the presser still owns the podium
    bodies = [m["body"] for m in store.messages(cur, tid)]
    assert any("Game Center" in b for b in bodies)


# ----------------------------------------------------------------------------------------------- start ---

def test_start_raises_400(cur):
    actors.take_control(cur, "CAR", "COACH", "41")
    with pytest.raises(InboxError) as err:
        press.start(cur, team(cur), {})
    assert err.value.status == 400


def test_template_points_to_the_game_center():
    assert "Week 5" in press.template("PRESS_READY", {"week": 5})
    assert "Game Center" in press.template("PRESS_READY", {"week": 5})
    assert "Game Center" in press.template("OPENED", {})
