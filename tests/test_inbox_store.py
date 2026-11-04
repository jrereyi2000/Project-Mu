"""Front-office inbox store (FRONT_OFFICE_INBOX_PLAN.md §2.6, §2.8; U1).

Offline: the inbox modules run against an in-memory SQLite database built from the real migration
(db/migrations/2027_01_19_front_office_inbox.sql, translated) plus the few league tables they read. The fake cursor
translates the psycopg dialect (%s, to_regclass, now(), FOR UPDATE, Jsonb) and is shared by the other inbox tests
(`from tests.test_inbox_store import InboxDB`). No PostgreSQL connection is opened.
"""
import json
import pathlib
import re
import sqlite3

import pytest

from engines.inbox import Conflict, actors, store

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "db" / "migrations" / "2027_01_19_front_office_inbox.sql"
INIT_DB = ROOT / "db" / "init_db.sql"

LEAGUE_DDL = """
CREATE TABLE teams (id INTEGER PRIMARY KEY, abbreviation TEXT, nickname TEXT, city TEXT);
CREATE TABLE coaches (id INTEGER PRIMARY KEY, coach_name TEXT);
CREATE TABLE coach_tenures (id INTEGER PRIMARY KEY AUTOINCREMENT, coach_id INT, team_id INT, start_season INT,
                            end_season INT);
CREATE TABLE gm_profiles (id INTEGER PRIMARY KEY, team_id INT, gm_name TEXT, start_season INT, end_season INT,
                          coach_id INT, risk_appetite REAL DEFAULT 0.5);
CREATE TABLE players (id INTEGER PRIMARY KEY, player_name TEXT);
CREATE TABLE player_seasons (player_id INT, team_id INT, season INT);
CREATE TABLE agent_memories (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT UNIQUE, handle TEXT,
                             display_name TEXT, assigned_team_id INT);
CREATE TABLE league_state (id INTEGER PRIMARY KEY, current_season INT, current_week INT);
"""

PERSONA_DDL = """
CREATE TABLE persona_sliders (persona_type TEXT, persona_id TEXT, temperament INT, sociability INT, confidence INT,
                              openness INT, warmth INT, drive INT, PRIMARY KEY (persona_type, persona_id));
CREATE TABLE persona_traits (id INTEGER PRIMARY KEY AUTOINCREMENT, persona_type TEXT, persona_id TEXT,
                             trait_id TEXT, active BOOLEAN DEFAULT 1);
"""


def sqlite_ddl(sql):
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"\b(BEGIN|COMMIT);", "", sql)
    sql = sql.replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
    sql = re.sub(r"\bJSONB\b", "TEXT", sql)
    sql = re.sub(r"\bTIMESTAMPTZ\b", "TEXT", sql)
    return sql.replace("now()", "CURRENT_TIMESTAMP")


def _translate(sql):
    sql = re.sub(r"to_regclass\(([^)]*)\)\s+IS NOT NULL",
                 r"((SELECT count(*) FROM sqlite_master WHERE name = \1) > 0)", sql)
    sql = re.sub(r"\s+FOR UPDATE\b", "", sql)
    sql = re.sub(r"\bnow\(\)", "CURRENT_TIMESTAMP", sql)
    return sql.replace("%s", "?")


def _param(v):
    if hasattr(v, "obj") and type(v).__name__ in ("Jsonb", "Json"):
        return json.dumps(v.obj, default=str)
    if isinstance(v, (dict, list)):
        return json.dumps(v, default=str)
    return v


def _value(v):
    if isinstance(v, str) and v[:1] in "{[":
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


class FakeCursor:
    """psycopg-like cursor over sqlite3."""

    def __init__(self, conn):
        self._conn = conn
        self._cur = conn.cursor()
        self.statements = []
        self.connection = self

    def execute(self, sql, params=()):
        self.statements.append(sql)
        self._cur.execute(_translate(sql), tuple(_param(p) for p in (params or ())))
        return self

    @property
    def rowcount(self):
        return self._cur.rowcount

    def fetchone(self):
        row = self._cur.fetchone()
        return None if row is None else tuple(_value(v) for v in row)

    def fetchall(self):
        return [tuple(_value(v) for v in row) for row in self._cur.fetchall()]

    def commit(self):
        raise AssertionError("inbox modules never commit the caller's transaction")

    rollback = commit


class InboxDB:
    """A league with Carolina (1) and Atlanta (2): CAR's GM Dan Morgan (10), HC Dave Canales (41), players
    Bryce Young (100), Xavier Legette (101); ATL's GM (20) and HC (42); writer car_beat."""

    def __init__(self, user_teams=("CAR",), personas=False, inbox=True):
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.conn.executescript(LEAGUE_DDL)
        if personas:
            self.conn.executescript(PERSONA_DDL)
        if inbox:
            self.conn.executescript(sqlite_ddl(MIGRATION.read_text()))
        self.user_teams = list(user_teams)
        c = self.conn
        c.executemany("INSERT INTO teams VALUES (?, ?, ?, ?)",
                      [(1, "CAR", "Panthers", "Carolina"), (2, "ATL", "Falcons", "Atlanta")])
        c.executemany("INSERT INTO coaches VALUES (?, ?)", [(41, "Dave Canales"), (42, "Raheem Morris")])
        c.executemany("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) VALUES (?, ?, ?, ?)",
                      [(41, 1, 2024, None), (42, 2, 2024, None)])
        c.executemany("INSERT INTO gm_profiles (id, team_id, gm_name, start_season, end_season, coach_id) "
                      "VALUES (?, ?, ?, ?, ?, ?)",
                      [(9, 1, "Scott Fitterer", 2020, 2023, None), (10, 1, "Dan Morgan", 2024, None, None),
                       (20, 2, "Terry Fontenot", 2021, None, None)])
        c.executemany("INSERT INTO players VALUES (?, ?)", [(100, "Bryce Young"), (101, "Xavier Legette"),
                                                             (200, "Bijan Robinson")])
        c.executemany("INSERT INTO player_seasons VALUES (?, ?, ?)",
                      [(100, 1, 2026), (101, 1, 2026), (200, 2, 2026)])
        c.execute("INSERT INTO agent_memories (agent_id, handle, display_name, assigned_team_id) "
                  "VALUES ('car_beat', '@carbeat', 'Joe Person', 1)")
        c.execute("INSERT INTO league_state VALUES (1, 2026, 5)")

    def cursor(self):
        return FakeCursor(self.conn)

    def make_coach_gm(self):
        """Carolina's GM tenure becomes the head coach's (gm_profiles.coach_id = 41)."""
        self.conn.execute("UPDATE gm_profiles SET coach_id = 41 WHERE id = 10")

    def rows(self, sql, *params):
        return self.conn.execute(sql, params).fetchall()


@pytest.fixture
def db(monkeypatch):
    d = InboxDB()
    monkeypatch.setattr(actors, "user_teams", lambda: [t.upper() for t in d.user_teams])
    return d


@pytest.fixture
def cur(db):
    return db.cursor()


def thread_row(team_id=1, kind="CONTRACT", source_ref="ext:100:2026", **kw):
    row = {"team_id": team_id, "kind": kind, "actor_role": "GM", "actor_id": "10", "counterpart_type": "player",
           "counterpart_id": "100", "counterpart_name": "Bryce Young", "subject": "Extension",
           "started_by": "COUNTERPART", "source_ref": source_ref, "season": 2026, "week": 5}
    row.update(kw)
    return row


# --------------------------------------------------------------------------------------------- migration ---

def test_migration_and_init_db_carry_the_same_tables():
    mig = " ".join(MIGRATION.read_text().split())
    init = " ".join(INIT_DB.read_text().split())
    for table in ("controlled_personas", "inbox_threads", "inbox_messages", "inbox_relationship_events",
                  "team_vision"):
        assert f"CREATE TABLE IF NOT EXISTS {table} (" in mig
        block = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\);", mig).group(1)
        assert block in init, table
    assert "CREATE INDEX IF NOT EXISTS inbox_threads_team_status" in init
    assert "persona_relationships" not in re.sub(r"--[^\n]*", "", MIGRATION.read_text())


# ------------------------------------------------------------------------------------------------ threads ---

def test_create_thread_is_idempotent_on_source(cur):
    first = store.create_thread(cur, thread_row())
    assert first is not None
    assert store.create_thread(cur, thread_row()) is None
    t = store.get_thread(cur, first)
    assert t["status"] == "OPEN" and t["state"] == {} and t["kind"] == "CONTRACT"


def test_messages_number_seq_and_set_status(cur):
    tid = store.create_thread(cur, thread_row())
    a = store.add_message(cur, tid, "COUNTERPART", "Let's talk.", "TEMPLATE", options=[{"id": "MAKE_OFFER"}])
    assert a["seq"] == 1
    t = store.get_thread(cur, tid)
    assert t["status"] == "WAITING_JR" and t["unread"]
    store.mark_read(cur, tid)
    assert not store.get_thread(cur, tid)["unread"]
    b = store.add_message(cur, tid, "JR", "3 years.", "JR", option_id="MAKE_OFFER")
    assert b["seq"] == 2
    t = store.get_thread(cur, tid)
    assert t["status"] == "OPEN" and not t["unread"]
    msgs = store.messages(cur, tid)
    assert [m["seq"] for m in msgs] == [1, 2]
    assert msgs[0]["options"] == [{"id": "MAKE_OFFER"}]


def test_caps_per_kind(cur):
    for i in range(store.MAX_OPEN_PER_KIND["REPORTER"]):
        assert store.create_thread(cur, thread_row(kind="REPORTER", source_ref=f"r{i}")) is not None
    assert store.create_thread(cur, thread_row(kind="REPORTER", source_ref="r9")) is None
    assert store.create_thread(cur, thread_row(kind="VISION", source_ref="v1")) is not None
    assert store.create_thread(cur, thread_row(kind="VISION", source_ref="v2")) is None
    # a closed thread frees its slot
    tid = store.find_thread(cur, 1, "VISION", "v1")
    store.close_thread(cur, tid)
    assert store.create_thread(cur, thread_row(kind="VISION", source_ref="v2")) is not None
    assert store.max_open("NEW_KIND") == store.DEFAULT_MAX_OPEN


def test_expiry_after_stale_weeks_and_by_source(cur):
    old = store.create_thread(cur, thread_row(source_ref="old"))
    fresh = store.create_thread(cur, thread_row(source_ref="fresh", week=7))
    dated = store.create_thread(cur, thread_row(source_ref="dated", week=7, expires_season=2026, expires_week=8))
    assert store.expire_stale(cur, 2026, 7) == 0              # 2 weeks after week 5: not yet past
    assert store.expire_stale(cur, 2026, 8) == 1
    assert store.get_thread(cur, old)["status"] == "EXPIRED"
    assert store.get_thread(cur, fresh)["status"] == "OPEN"
    assert store.expire_stale(cur, 2026, 9) == 1
    assert store.get_thread(cur, dated)["status"] == "EXPIRED"


def test_a_reply_resets_the_stale_clock(cur):
    tid = store.create_thread(cur, thread_row())
    store.add_message(cur, tid, "JR", "hi", "JR", at={"season": 2026, "week": 7})
    assert store.expire_stale(cur, 2026, 9) == 0
    assert store.expire_stale(cur, 2026, 10) == 1


def test_released_actor_makes_thread_read_only(db, cur):
    actors.take_control(cur, "CAR", "GM", "10")
    tid = store.create_thread(cur, thread_row())
    t = store.get_thread(cur, tid)
    assert store.writable(cur, t)
    actors.release_control(cur, "CAR", "GM", "10")
    assert not store.writable(cur, t)
    with pytest.raises(Conflict):
        store.require_writable(cur, t)
    assert store.get_thread(cur, tid) is not None             # still readable
    store.close_thread(cur, tid)
    assert not store.writable(cur, store.get_thread(cur, tid))


def test_list_filters_and_unread(cur):
    a = store.create_thread(cur, thread_row(source_ref="a"))
    store.create_thread(cur, thread_row(source_ref="b", kind="REPORTER", actor_role="COACH", actor_id="41"))
    store.add_message(cur, a, "COUNTERPART", "x", "TEMPLATE")
    assert store.unread_count(cur, 1) == 2
    assert [t["id"] for t in store.list_threads(cur, 1, kind="CONTRACT")] == [a]
    assert len(store.list_threads(cur, 1, actor_role="coach")) == 1
    assert len(store.list_threads(cur, 1, status=["WAITING_JR"])) == 1


def test_jr_statements_shape(cur):
    tid = store.create_thread(cur, thread_row(kind="REPORTER", actor_role="COACH", actor_id="41",
                                              counterpart_type="writer", counterpart_id="car_beat",
                                              counterpart_name="Joe Person", subject="The injury",
                                              source_ref="inj:100"))
    store.add_message(cur, tid, "COUNTERPART", "How is Bryce?", "TEMPLATE")
    store.add_message(cur, tid, "JR", "He's day to day.", "JR", option_id="ON_RECORD")
    store.add_message(cur, tid, "COUNTERPART", "Will he start?", "TEMPLATE")
    store.add_message(cur, tid, "JR", "No comment", "JR", option_id="NO_COMMENT")
    store.add_message(cur, tid, "COUNTERPART", "Between us?", "TEMPLATE")
    store.add_message(cur, tid, "JR", "Probably not.", "JR", option_id="OFF_RECORD")
    rows = store.jr_statements(cur, 1, 2026)
    assert len(rows) == 2
    first = rows[0]
    assert set(first) == {"thread_id", "team_id", "season", "week", "writer_agent_id", "writer_handle", "subject",
                          "question", "answer", "on_record", "actor_role", "actor_name", "created_at"}
    assert first["question"] == "How is Bryce?" and first["answer"] == "He's day to day." and first["on_record"]
    assert first["writer_handle"] == "@carbeat" and first["actor_name"] == "Dave Canales"
    assert rows[1]["on_record"] is False
    assert store.jr_statements(cur, 1, 2026, week=6) == []
