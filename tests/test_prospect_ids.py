"""Permanent prospect IDs (engines/prospect_ids.py; SCOUTING_REVAMP_PLAN.md §2.1b, §4; U1).

Offline: an in-memory SQLite database built from the real migration (db/migrations/2027_01_21_scouting_reports.sql,
translated by the inbox tests' helpers). No PostgreSQL connection is opened.
"""
import pathlib
import re
import sqlite3
import uuid

import pytest

from engines import prospect_ids as pids
from tests.test_inbox_store import FakeCursor, sqlite_ddl

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "db" / "migrations" / "2027_01_21_scouting_reports.sql"
INIT_DB = ROOT / "db" / "init_db.sql"
TABLES = ("scout_prospect_ids", "scout_prospect_ratings", "scout_reports", "scout_interviews")


def scouting_db():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(sqlite_ddl(MIGRATION.read_text()))
    return conn


@pytest.fixture
def cur():
    return FakeCursor(scouting_db())


def rows_of(cur):
    cur.execute("SELECT prospect_uid, birth_key, pgid, prospect_key, key_history, status, first_seen "
                "FROM scout_prospect_ids ORDER BY birth_key")
    return cur.fetchall()


# ------------------------------------------------------------------------------------------- migration ---

def test_migration_has_four_idempotent_tables_mirrored_in_init_db():
    sql = MIGRATION.read_text()
    assert sql.count("CREATE TABLE IF NOT EXISTS") == 4
    assert "BEGIN;" in sql and "COMMIT;" in sql
    mig = " ".join(sql.split())
    init = " ".join(INIT_DB.read_text().split())
    for table in TABLES:
        block = re.search(rf"CREATE TABLE IF NOT EXISTS {table} \((.*?)\);", mig).group(1)
        assert block in init, table
    assert "CREATE INDEX IF NOT EXISTS scout_prospect_ids_key" in init
    # scout_interviews references inbox_threads, so init_db must define the inbox first
    assert init.index("CREATE TABLE IF NOT EXISTS inbox_threads") < init.index("CREATE TABLE IF NOT EXISTS scout_interviews")
    assert init.index("CREATE TABLE IF NOT EXISTS scout_runs") < init.index("CREATE TABLE IF NOT EXISTS scout_reports")


def test_migration_runs_twice():
    conn = scouting_db()
    conn.executescript(sqlite_ddl(MIGRATION.read_text()))


def test_hidden_ratings_table_is_in_no_view():
    sql = re.sub(r"--[^\n]*", "", INIT_DB.read_text())
    for view in re.findall(r"CREATE (?:OR REPLACE )?VIEW.*?;", sql, flags=re.S):
        assert "scout_prospect_ratings" not in view


# -------------------------------------------------------------------------------------------- birth key ---

def test_same_birth_key_same_uid():
    a = pids.uid_from_birth_key(pids.birth_key(2027, 555))
    assert a == pids.uid_from_birth_key("save-pgid:2027:555")
    assert a == uuid.uuid5(pids.MU_PROSPECT_NS, "save-pgid:2027:555")
    assert pids.birth_key(2027, None, "2027:SMITH:QB") == "board:2027:2027:SMITH:QB"
    assert a != pids.uid_from_birth_key(pids.birth_key(2028, 555))


def test_allocates_once_and_rerun_changes_nothing(cur):
    rows = [{"prospect_key": "2027:JOHNSMITH:QB", "pgid": 10}, {"prospect_key": "2027:BOBJONES:WR", "pgid": 11}]
    out = pids.ensure_prospect_ids(cur, 2027, rows, "week1")
    assert out == {"allocated": 2, "renamed": 0, "collisions": 0}
    before = rows_of(cur)
    assert {r[0] for r in before} == {str(pids.uid_from_birth_key("save-pgid:2027:10")),
                                      str(pids.uid_from_birth_key("save-pgid:2027:11"))}
    assert pids.ensure_prospect_ids(cur, 2027, rows, "week2") == {"allocated": 0, "renamed": 0, "collisions": 0}
    assert rows_of(cur) == before                    # first_seen stays week1


def test_rename_keeps_uid_and_appends_history(cur):
    pids.ensure_prospect_ids(cur, 2027, [{"prospect_key": "2027:JOHNSMITH:QB", "pgid": 10}], "week1")
    uid = pids.uid_for(cur, 2027, "2027:JOHNSMITH:QB")
    out = pids.ensure_prospect_ids(cur, 2027, [{"prospect_key": "2027:JONSMITH:QB", "pgid": 10}], "week5")
    assert out["renamed"] == 1 and out["allocated"] == 0
    assert pids.uid_for(cur, 2027, "2027:JONSMITH:QB") == uid
    assert pids.uid_for(cur, 2027, "2027:JOHNSMITH:QB") is None
    (row,) = rows_of(cur)
    assert row[4] == [{"prospect_key": "2027:JOHNSMITH:QB", "until_capture": "week5"}]


def test_position_group_change_keeps_uid(cur):
    pids.ensure_prospect_ids(cur, 2027, [{"prospect_key": "2027:JOHNSMITH:LB", "pgid": 10}], "week1")
    uid = pids.uid_for(cur, 2027, "2027:JOHNSMITH:LB")
    pids.ensure_prospect_ids(cur, 2027, [{"prospect_key": "2027:JOHNSMITH:EDGE", "pgid": 10}], "week2")
    assert pids.uid_for(cur, 2027, "2027:JOHNSMITH:EDGE") == uid
    assert len(rows_of(cur)) == 1


def test_shared_key_marks_both_collisions(cur):
    rows = [{"prospect_key": "2027:JOHNSMITH:QB", "pgid": 10}, {"prospect_key": "2027:JOHNSMITH:QB", "pgid": 12},
            {"prospect_key": "2027:BOBJONES:WR", "pgid": 11}]
    out = pids.ensure_prospect_ids(cur, 2027, rows, "week1")
    assert out == {"allocated": 3, "renamed": 0, "collisions": 2}
    status = {r[2]: r[5] for r in rows_of(cur)}
    assert status == {10: "KEY_COLLISION", 12: "KEY_COLLISION", 11: "OK"}
    assert pids.uid_for(cur, 2027, "2027:JOHNSMITH:QB") is None
    # once one is renamed, both are OK again
    rows[1] = {"prospect_key": "2027:JOHNSMITHJR:QB", "pgid": 12}
    pids.ensure_prospect_ids(cur, 2027, rows, "week2")
    assert {r[2]: r[5] for r in rows_of(cur)} == {10: "OK", 12: "OK", 11: "OK"}


def test_scanned_class_matches_by_key(cur):
    rows = [{"prospect_key": "2027:JOHNSMITH:QB", "pgid": None}]
    assert pids.ensure_prospect_ids(cur, 2027, rows, "scan1")["allocated"] == 1
    assert pids.ensure_prospect_ids(cur, 2027, rows, "scan2")["allocated"] == 0
    (row,) = rows_of(cur)
    assert row[1] == "board:2027:2027:JOHNSMITH:QB" and row[2] is None
    assert pids.uid_for(cur, 2027, "2027:JOHNSMITH:QB") == pids.uid_from_birth_key(row[1])


def test_classes_are_separate(cur):
    pids.ensure_prospect_ids(cur, 2027, [{"prospect_key": "2027:JOHNSMITH:QB", "pgid": 10}], "w")
    out = pids.ensure_prospect_ids(cur, 2028, [{"prospect_key": "2028:JOHNSMITH:QB", "pgid": 10}], "w")
    assert out["allocated"] == 1 and len(rows_of(cur)) == 2


def test_person_ref_prefers_source_person_id(cur):
    pids.ensure_prospect_ids(cur, 2027, [{"prospect_key": "2027:JOHNSMITH:QB", "pgid": 10}], "w")
    uid = pids.uid_for(cur, 2027, "2027:JOHNSMITH:QB")
    assert pids.person_ref(cur, uid) == str(uid)
    other = uuid.uuid4()
    cur.execute("UPDATE scout_prospect_ids SET source_person_id = %s WHERE prospect_uid = %s", (str(other), str(uid)))
    assert pids.person_ref(cur, uid) == str(other)


def test_missing_table_is_a_no_op():
    cur = FakeCursor(sqlite3.connect(":memory:", isolation_level=None))
    assert pids.ensure_prospect_ids(cur, 2027, [{"prospect_key": "k", "pgid": 1}], "w") == \
        {"allocated": 0, "renamed": 0, "collisions": 0}
    assert pids.uid_for(cur, 2027, "k") is None
    assert pids.class_ids(cur, 2027) == {}
