"""Retirements at the season boundary from the save (SEASON_CONTINUITY_PLAN phase 2b; CLOSE_OUT_CHECKLIST P1-26).

Offline: the load step runs on an in-memory SQLite fake with only the columns it reads and writes.
"""
import sqlite3

from pipeline import load_save as ls
from tests.test_inbox_store import FakeCursor


def _db():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript("""
        CREATE TABLE players (id INTEGER PRIMARY KEY AUTOINCREMENT, player_name TEXT, full_name TEXT, height TEXT,
                              weight INT, player_number INT, origin TEXT, madden_pgid BIGINT,
                              retired_season INT);
        CREATE TABLE player_retirements (id INTEGER PRIMARY KEY AUTOINCREMENT, season INT, player_id INT,
                                         player_name TEXT, position TEXT, team_id INT, transaction_id INT,
                                         capture_id INT, flags TEXT);
        CREATE UNIQUE INDEX player_retirements_one ON player_retirements (season, player_id)
            WHERE player_id IS NOT NULL;
    """)
    conn.execute("INSERT INTO players (player_name, full_name, origin, madden_pgid) "
                 "VALUES ('J. Retiree', 'John Retiree', 'SAVE', 900001)")
    conn.execute("INSERT INTO players (player_name, full_name, origin, madden_pgid) "
                 "VALUES ('T. Passer', 'Tom Passer', 'SAVE', 900002)")
    return conn


SNAP = {"season": {"week_type": "OffSeason", "week_index": 1, "stage": 1}, "players": [
    {"pgid": 900001, "first_name": "John", "last_name": "Retiree", "status": "Retired", "position": "QB",
     "team_index": 0, "height_in": 74, "weight_lbs": 220, "jersey": 9},
    {"pgid": 900002, "first_name": "Tom", "last_name": "Passer", "status": "Signed", "position": "QB",
     "team_index": 0, "height_in": 74, "weight_lbs": 220, "jersey": 10},
    {"pgid": 900003, "first_name": "New", "last_name": "Rookie", "status": "Retired", "position": "WR",
     "team_index": 32, "height_in": 72, "weight_lbs": 200, "jersey": 11},
]}


def test_retirement_season_offseason_and_preseason():
    assert ls.retirement_season({"season": {"week_type": "OffSeason"}}, 2026) == 2026
    assert ls.retirement_season({"season": {"week_type": "PreSeason"}}, 2027) == 2026
    assert ls.retirement_season({"season": {"week_type": "RegularSeason"}}, 2026) == 2026


def test_retired_player_gets_a_row_and_retired_season():
    conn, cur = _db(), None
    cur = FakeCursor(conn)
    report = {}
    n = ls.load_retirements(cur, SNAP, 2026, {0: 100}, report, capture_id=7)
    assert n == 2 and report["retirements"] == 2
    cur.execute("SELECT season, player_id, player_name, position, team_id, capture_id FROM player_retirements "
                "ORDER BY player_id")
    rows = cur.fetchall()
    assert rows[0] == (2026, 1, "J. Retiree", "QB", 100, 7)
    assert rows[1][1] is not None and rows[1][2] == "N. Rookie" and rows[1][4] is None   # created, no team
    cur.execute("SELECT id, retired_season FROM players ORDER BY id")
    assert cur.fetchall() == [(1, 2026), (2, None), (3, 2026)]


def test_retirements_are_idempotent():
    cur = FakeCursor(_db())
    ls.load_retirements(cur, SNAP, 2026, {0: 100}, {})
    ls.load_retirements(cur, SNAP, 2026, {0: 100}, {})
    cur.execute("SELECT count(*) FROM player_retirements")
    assert cur.fetchone()[0] == 2


def test_no_retirements_reports_zero_and_writes_nothing():
    cur = FakeCursor(_db())
    report = {}
    snap = {"season": {"week_type": "OffSeason"}, "players": [SNAP["players"][1]]}
    assert ls.load_retirements(cur, snap, 2026, {0: 100}, report) == 0
    assert report["retirements"] == 0
    cur.execute("SELECT count(*) FROM player_retirements")
    assert cur.fetchone()[0] == 0
