"""Staff-contract producer (STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN U1 follow-up; CLOSE_OUT_CHECKLIST P1-22).

Offline: the producer runs on an in-memory SQLite fake with the tenures and staff_contracts schemas.
"""
import sqlite3

from engines import staff_contracts_run as scr
from tests.test_inbox_store import FakeCursor


def _db():
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript("""
        CREATE TABLE coaches (id INTEGER PRIMARY KEY, coach_name TEXT);
        CREATE TABLE coach_records (coach_id INT, super_bowl_wins INT);
        CREATE TABLE coach_tenures (id INTEGER PRIMARY KEY AUTOINCREMENT, coach_id INT, team_id INT,
                                    start_season INT, end_season INT);
        CREATE TABLE coordinators (id INTEGER PRIMARY KEY, coordinator_name TEXT);
        CREATE TABLE coordinator_tenures (id INTEGER PRIMARY KEY AUTOINCREMENT, coordinator_id INT, team_id INT,
                                          role TEXT, start_season INT, end_season INT);
        CREATE TABLE staff_contracts (id INTEGER PRIMARY KEY AUTOINCREMENT, team_id INT, person_id INT,
                                      person_kind TEXT, annual_salary INT, total_years INT, remaining_years INT,
                                      signed_season INT, full_control INT DEFAULT 0, buyout_owed INT DEFAULT 0);
    """)
    conn.execute("INSERT INTO coaches VALUES (10, 'Old Coach'), (11, 'New Coach')")
    conn.execute("INSERT INTO coach_records VALUES (10, 1), (11, 0)")
    conn.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) VALUES "
                 "(10, 1, 2030, NULL), (11, 2, 2035, NULL)")
    conn.execute("INSERT INTO coordinators VALUES (20, 'OC One'), (21, 'DC One'), (22, 'OC Two')")
    conn.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season) VALUES "
                 "(20, 1, 'OC', 2035, NULL), (21, 1, 'DC', 2036, NULL), (22, 2, 'OC', 2035, NULL)")
    return conn


def _rows(cur):
    cur.execute("SELECT team_id, person_kind, person_id, annual_salary, total_years, remaining_years, "
                "signed_season, buyout_owed FROM staff_contracts ORDER BY team_id, person_kind, person_id")
    return cur.fetchall()


def test_files_every_current_hc_oc_dc_once_and_is_idempotent():
    conn = _db()
    cur = FakeCursor(conn)
    first = scr.annual(cur, 2036)
    assert first["filed"] == 5 and first["payroll"] > 0
    rows = _rows(cur)
    assert len(rows) == 5
    hc = next(r for r in rows if r[1] == "HC" and r[0] == 1)
    assert hc[3] > 0 and hc[4] == 4
    # a champion head coach earns the LEGENDARY band
    assert hc[3] >= 16_000_000
    second = scr.annual(cur, 2036)
    assert second["filed"] == 0 and second["fired"] == 0 and len(_rows(cur)) == 5


def test_remaining_years_rolls_deterministically():
    conn = _db()
    cur = FakeCursor(conn)
    scr.annual(cur, 2036)
    scr.annual(cur, 2037)
    hc = next(r for r in _rows(cur) if r[1] == "HC" and r[0] == 1)
    # his tenure predates the ledger, so 2036 bootstrapped a 4-year deal; 2037 leaves 3
    assert hc[5] == 3 and hc[6] == 2036


def test_a_firing_moves_remaining_guaranteed_into_buyout():
    conn = _db()
    cur = FakeCursor(conn)
    scr.annual(cur, 2036)
    before = next(r for r in _rows(cur) if r[1] == "HC" and r[0] == 2)
    conn.execute("UPDATE coach_tenures SET end_season = 2036 WHERE coach_id = 11")
    rep = scr.annual(cur, 2037)
    assert rep["fired"] == 1
    after = next(r for r in _rows(cur) if r[1] == "HC" and r[0] == 2)
    assert after[5] == 0 and after[7] == before[3] * before[5]      # salary x remaining years
    # a fired coach's replacement is filed fresh
    conn.execute("INSERT INTO coaches VALUES (12, 'Replacement'), (23, 'Replacement OC')")
    conn.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) VALUES (12, 2, 2037, NULL)")
    rep = scr.annual(cur, 2037)
    assert any(r[1] == "HC" and r[0] == 2 and r[5] > 0 for r in _rows(cur))


def test_dry_run_writes_nothing():
    conn = _db()
    rep = scr.annual(FakeCursor(conn), 2036, dry_run=True)
    assert rep["status"] == "dry_run" and rep["filed"] == 5
    cur = FakeCursor(conn)
    cur.execute("SELECT count(*) FROM staff_contracts")
    assert cur.fetchone()[0] == 0


def test_without_the_tables_it_reports_not_installed():
    cur = FakeCursor(sqlite3.connect(":memory:"))
    assert scr.annual(cur, 2036)["status"] == "not_installed"
