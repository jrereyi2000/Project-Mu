"""engines/nfl_mirror/store.py and its migration (docs/done/NFL_WEEKLY_MIRROR_PLAN.md §2.1, §2.4, §4).

Offline: an in-memory SQLite database built from the migration behind the shared psycopg-like FakeCursor. The
integration test applies the migration twice on a throwaway mu_scratch_* PostgreSQL database.
"""
import os
import re
import sqlite3

import pytest

from engines.nfl_mirror import store
from tests.test_inbox_store import FakeCursor, sqlite_ddl

ROOT = os.path.join(os.path.dirname(__file__), "..")
MIGRATION = os.path.join(ROOT, "db", "migrations", "2027_02_27_nfl_mirror.sql")
INIT_DB = os.path.join(ROOT, "db", "init_db.sql")
TABLES = ("nfl_mirror_settings", "nfl_mirror_weeks", "nfl_mirror_moves")

BASE_DDL = """
CREATE TABLE teams (id INTEGER PRIMARY KEY, abbreviation TEXT);
CREATE TABLE players (id INTEGER PRIMARY KEY, name TEXT, madden_pgid INT);
CREATE TABLE league_office_runs (id INTEGER PRIMARY KEY);
CREATE TABLE nfl_players (gsis_id TEXT PRIMARY KEY, display_name TEXT NOT NULL, position TEXT, birth_date TEXT,
                          college TEXT);
CREATE TABLE nfl_player_links (
    player_id INT PRIMARY KEY, gsis_id TEXT NOT NULL UNIQUE,
    method TEXT NOT NULL CHECK (method IN ('EXACT', 'ESPN', 'ALIAS', 'FUZZY', 'OVERRIDE', 'CREATED')),
    score NUMERIC, evidence TEXT NOT NULL DEFAULT '{}', linked_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def _mirror_sqlite():
    """The migration's tables in SQLite (its ALTER of nfl_player_links is PostgreSQL-only; BASE_DDL has the result)."""
    sql = open(MIGRATION).read()
    sql = sql[:sql.index("-- A player the mirror created")] + "COMMIT;"
    return sqlite_ddl(sql)


def _db(installed=True):
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(BASE_DDL)
    if installed:
        conn.executescript(_mirror_sqlite())
    conn.executemany("INSERT INTO teams (id, abbreviation) VALUES (?, ?)", [(1, "KC"), (2, "BUF")])
    return conn, FakeCursor(conn)


# ---------- settings and mirror_active ----------

def test_missing_tables_read_as_mirror_off():
    _conn, cur = _db(installed=False)
    assert store.read_settings(cur) is None
    assert store.mirror_active(cur, 2026, "REGULAR_WEEK", 5) is False
    assert store.week_status(cur, 2026, 5) is None


def test_migration_seeds_one_disabled_row():
    _conn, cur = _db()
    settings = store.read_settings(cur)
    assert settings["enabled"] is False
    assert settings["season"] == 2026 and settings["last_week"] is None
    assert settings["injuries_state"] == "ON" and settings["madden_injuries_field"] is None
    assert store.mirror_active(cur, 2026, "REGULAR_WEEK", 1) is False


@pytest.mark.parametrize("season,stage,week,expected", [
    (2026, "REGULAR_WEEK", 1, True),
    (2026, "REGULAR_WEEK", 10, True),            # == last_week
    (2026, "PLAYOFF_WEEK", 10, True),
    (2026, "REGULAR_WEEK", 11, False),           # past last_week
    (2027, "REGULAR_WEEK", 3, False),            # wrong season
    (2026, "OFFSEASON_START", 3, False),         # offseason stage
    (2026, "PRESEASON", 3, False),
    (2026, "REGULAR_WEEK", None, False),
])
def test_mirror_active_cases(season, stage, week, expected):
    _conn, cur = _db()
    store.set_enabled(cur, True, season=2026, last_week=10)
    assert store.mirror_active(cur, season, stage, week) is expected


def test_mirror_active_through_super_bowl_and_off_again():
    _conn, cur = _db()
    store.set_enabled(cur, True)
    assert store.mirror_active(cur, 2026, "PLAYOFF_WEEK", 23) is True
    store.set_enabled(cur, False)
    assert store.mirror_active(cur, 2026, "PLAYOFF_WEEK", 23) is False


def test_set_enabled_rejects_bad_last_week():
    _conn, cur = _db()
    with pytest.raises(ValueError):
        store.set_enabled(cur, True, last_week=0)


def test_injuries_state_and_field():
    _conn, cur = _db()
    field = {"table": "LeagueSetting", "tableId": 7, "row": 0, "field": "Field_51", "off": 0, "on": 1}
    store.set_injuries_state(cur, "off")
    store.set_injuries_field(cur, field)
    settings = store.read_settings(cur)
    assert settings["injuries_state"] == "OFF"
    assert settings["madden_injuries_field"] == field
    with pytest.raises(ValueError):
        store.set_injuries_state(cur, "MAYBE")
    store.set_injuries_field(cur, None)
    assert store.read_settings(cur)["madden_injuries_field"] is None


def test_settings_row_recreated_when_missing():
    conn, cur = _db()
    conn.execute("DELETE FROM nfl_mirror_settings")
    assert store.read_settings(cur) is None
    store.set_enabled(cur, True, season=2026, last_week=4)
    assert store.mirror_active(cur, 2026, "REGULAR_WEEK", 4) is True


# ---------- weeks and moves ----------

def test_record_week_upserts_only_given_fields():
    conn, cur = _db()
    store.record_week(cur, 2026, 3, real_week=3, mode="CATCH_UP", sources={"roster_weekly": "abc"},
                      counts={"SIGN": 2})
    assert store.week_status(cur, 2026, 3) == "PLANNED"
    store.record_week(cur, 2026, 3, report="two signings")
    row = conn.execute("SELECT real_week, mode, sources, counts, report FROM nfl_mirror_weeks").fetchone()
    assert row == (3, "CATCH_UP", '{"roster_weekly": "abc"}', '{"SIGN": 2}', "two signings")
    with pytest.raises(ValueError):
        store.record_week(cur, 2026, 3, mode="SOMETIMES")
    with pytest.raises(ValueError):
        store.record_week(cur, 2026, 3, colour="red")


def test_record_moves_and_mark_written():
    conn, cur = _db()
    conn.execute("INSERT INTO league_office_runs (id) VALUES (77)")
    store.record_week(cur, 2026, 4, real_week=4, mode="CATCH_UP")
    ids = store.record_moves(cur, 2026, 4, [
        {"kind": "SIGN", "gsis_id": "00-1", "pgid": 501, "to_team_id": 1, "detail": {"years": 1}},
        {"kind": "STAFF", "to_team_id": 2, "status": "SKIPPED", "reason": "user team"},
    ])
    assert len(ids) == 2
    store.mark_written(cur, 2026, 4, 77)
    assert store.week_status(cur, 2026, 4) == "WRITTEN"
    assert conn.execute("SELECT run_id FROM nfl_mirror_weeks").fetchone() == (77,)
    rows = conn.execute("SELECT kind, gsis_id, pgid, to_team_id, detail, status, reason FROM nfl_mirror_moves "
                        "ORDER BY id").fetchall()
    assert rows == [("SIGN", "00-1", 501, 1, '{"years": 1}', "WRITTEN", None),
                    ("STAFF", None, None, 2, "{}", "SKIPPED", "user team")]


def test_record_moves_replace_keeps_written_moves():
    conn, cur = _db()
    store.record_moves(cur, 2026, 5, [{"kind": "RELEASE", "gsis_id": "a", "status": "WRITTEN"},
                                      {"kind": "SIGN", "gsis_id": "b"}])
    store.record_moves(cur, 2026, 5, [{"kind": "SIGN", "gsis_id": "c"}], replace=True)
    assert conn.execute("SELECT gsis_id FROM nfl_mirror_moves ORDER BY id").fetchall() == [("a",), ("c",)]


@pytest.mark.parametrize("move", [{"kind": "DANCE"}, {"kind": "SIGN", "status": "MAYBE"},
                                  {"kind": "SIGN", "colour": "red"}])
def test_record_moves_rejects_bad_moves(move):
    _conn, cur = _db()
    with pytest.raises(ValueError):
        store.record_moves(cur, 2026, 1, [move])


# ---------- links ----------

def test_links_maps_gsis_to_pgid():
    conn, cur = _db()
    conn.executemany("INSERT INTO players (id, name, madden_pgid) VALUES (?, ?, ?)",
                     [(1, "A", 9001), (2, "B", None)])
    conn.executemany("INSERT INTO nfl_players (gsis_id, display_name) VALUES (?, ?)", [("g1", "A"), ("g2", "B")])
    conn.executemany("INSERT INTO nfl_player_links (player_id, gsis_id, method) VALUES (?, ?, 'EXACT')",
                     [(1, "g1"), (2, "g2")])
    assert store.links(cur) == {"g1": 9001}


def test_add_created_link_adds_person_once_and_refuses_conflicts():
    conn, cur = _db()
    conn.executemany("INSERT INTO players (id, name, madden_pgid) VALUES (?, ?, ?)", [(5, "R", 7005), (6, "S", 7006)])
    evidence = {"name": "Rookie Guy", "position": "WR", "source": "EA", "donor_pgid": 6100}
    assert store.add_created_link(cur, 5, "00-0099", evidence) is True
    assert store.add_created_link(cur, 5, "00-0099", evidence) is False
    assert conn.execute("SELECT method, evidence FROM nfl_player_links").fetchall() == [
        ("CREATED", '{"name": "Rookie Guy", "position": "WR", "source": "EA", "donor_pgid": 6100}')]
    assert conn.execute("SELECT display_name, position FROM nfl_players").fetchall() == [("Rookie Guy", "WR")]
    assert store.links(cur) == {"00-0099": 7005}
    with pytest.raises(ValueError):
        store.add_created_link(cur, 6, "00-0099", evidence)       # that person is someone else's
    with pytest.raises(ValueError):
        store.add_created_link(cur, 5, "00-0100", evidence)       # that player is linked already


# ---------- migration text ----------

def test_migration_and_init_db_define_the_tables_and_created_method():
    mig = open(MIGRATION).read()
    init = open(INIT_DB).read()
    assert "BEGIN;" in mig and "COMMIT;" in mig
    for text in (mig, init):
        assert set(re.findall(r"CREATE TABLE IF NOT EXISTS (nfl_mirror_\w+)", text)) == set(TABLES)
        assert "DROP CONSTRAINT IF EXISTS nfl_player_links_method_check" in text
        assert "'OVERRIDE', 'CREATED')" in text
    # init_db.sql creates nfl_player_links before it re-creates the CHECK, and the ledger lists the migration.
    assert init.index("CREATE TABLE IF NOT EXISTS nfl_player_links") < init.index(
        "ADD CONSTRAINT nfl_player_links_method_check")
    assert "('2027_02_27_nfl_mirror', '2027_02_27_nfl_mirror.sql'" in init


def test_store_vocabularies_match_the_migration():
    mig = open(MIGRATION).read()
    for kind in store.MOVE_KINDS:
        assert f"'{kind}'" in mig
    for name in store.WEEK_FIELDS + store.MOVE_FIELDS:
        assert re.search(rf"\b{name}\b", mig), name


# ---------- scratch database (integration) ----------

SCRATCH = "mu_scratch_nfl_mirror_store"
ADMIN = "host=localhost dbname=postgres user=postgres password=postgres connect_timeout=3"


@pytest.mark.integration
def test_migration_applies_twice_on_init_db_and_store_round_trips():
    import psycopg
    try:
        admin = psycopg.connect(ADMIN, autocommit=True)
    except Exception:
        pytest.skip("no Postgres")
    admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {SCRATCH}")
    try:
        with psycopg.connect(f"host=localhost dbname={SCRATCH} user=postgres password=postgres") as conn:
            conn.execute(open(INIT_DB).read())
            sql = open(MIGRATION).read().replace("BEGIN;", "").replace("COMMIT;", "")
            conn.execute(sql)
            conn.execute(sql)
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM nfl_mirror_settings")
                assert cur.fetchone() == (1,)
                cur.execute("""SELECT pg_get_constraintdef(oid) FROM pg_constraint
                               WHERE conname = 'nfl_player_links_method_check'""")
                assert "CREATED" in cur.fetchone()[0]
                store.set_enabled(cur, True, season=2026, last_week=9)
                assert store.mirror_active(cur, 2026, "REGULAR_WEEK", 9)
                assert not store.mirror_active(cur, 2026, "REGULAR_WEEK", 10)
                store.set_injuries_field(cur, {"table": "LeagueSetting", "field": "Field_1", "off": 0, "on": 1})
                assert store.read_settings(cur)["madden_injuries_field"]["field"] == "Field_1"
                store.record_week(cur, 2026, 1, real_week=1, mode="CATCH_UP", sources={"x": "y"})
                cur.execute("INSERT INTO teams (abbreviation, name, city) VALUES ('KC', 'Chiefs', 'Kansas City') "
                            "RETURNING id")
                team_id = cur.fetchone()[0]
                store.record_moves(cur, 2026, 1, [{"kind": "STAFF", "to_team_id": team_id, "detail": {"a": 1}}])
                cur.execute("INSERT INTO players (player_name, madden_pgid) VALUES ('Rookie Guy', 4242) "
                            "RETURNING id")
                player_id = cur.fetchone()[0]
                assert store.add_created_link(cur, player_id, "00-0099", {"name": "Rookie Guy", "position": "WR"})
                assert store.links(cur) == {"00-0099": 4242}
                cur.execute("INSERT INTO league_office_runs (season, week, stage, save_name, status, edits) "
                            "VALUES (2026, 1, 'REGULAR_WEEK', 'CAREER-X', 'WRITTEN', '{}') RETURNING id")
                store.mark_written(cur, 2026, 1, cur.fetchone()[0])
                assert store.week_status(cur, 2026, 1) == "WRITTEN"
            conn.execute(sql)                                  # a third run over real rows changes nothing
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM nfl_mirror_moves")
                assert cur.fetchone() == (1,)
    finally:
        admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)")
        admin.close()
