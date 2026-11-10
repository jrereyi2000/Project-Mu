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


def _db_with_gms():
    """The same league plus one veteran GM (team 1, hired 2030) and one first-year GM (team 2, hired 2036)."""
    conn = _db()
    conn.execute("CREATE TABLE gm_profiles (id INTEGER PRIMARY KEY, team_id INT, gm_name TEXT, "
                 "start_season INT, end_season INT, coach_id INT)")
    conn.execute("INSERT INTO gm_profiles VALUES (101, 1, 'Veteran GM', 2030, NULL, NULL), "
                 "(102, 2, 'Rookie GM', 2036, NULL, NULL)")
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
    # B3 (chunk 9): recomputed with the roll's own rule, the deal owes 2 seasons here (the stored 3 was last rolled
    # for the previous league year, so buying it out as stored overpaid by one).
    assert after[5] == 0 and after[7] == before[3] * 2
    # a fired coach's replacement is filed fresh
    conn.execute("INSERT INTO coaches VALUES (12, 'Replacement'), (23, 'Replacement OC')")
    conn.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) VALUES (12, 2, 2037, NULL)")
    rep = scr.annual(cur, 2037)
    assert any(r[1] == "HC" and r[0] == 2 and r[5] > 0 for r in _rows(cur))


def test_gm_experience_comes_from_his_start_season():
    """A GM's experience is his seasons on the job, not the count of his gm_profiles row (always 1)."""
    conn = _db_with_gms()
    cur = FakeCursor(conn)
    staff = scr.current_staff(cur, 2036)
    gm_by_team = {s["team_id"]: s for s in staff if s["person_kind"] == "GM"}
    assert gm_by_team[1]["experience"] == 7                  # hired 2030: 2030..2036
    assert gm_by_team[2]["experience"] == 1                  # hired 2036
    scr.annual(cur, 2036)
    rows = {(r[0], r[1]): r for r in _rows(cur)}
    assert rows[(1, "GM")][3] == 5_750_000                   # the veteran stays STANDARD
    assert rows[(2, "GM")][3] == 2_750_000                   # a first-year GM is PROMOTION


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


def test_a_poached_coordinator_is_not_fired_and_owes_no_buyout():
    """JR, 2026-11-09: a coordinator who left for a promotion or another team is a departure, not a firing."""
    conn = _db()
    cur = FakeCursor(conn)
    # OC One (20) leaves team 1 and takes team 2's OC job in the same season.
    conn.execute("UPDATE coordinator_tenures SET end_season = 2035 WHERE coordinator_id = 20")
    conn.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season) "
                 "VALUES (20, 2, 'OC', 2036, NULL)")
    conn.execute("INSERT INTO staff_contracts (team_id, person_id, person_kind, annual_salary, total_years, "
                 "remaining_years, signed_season) VALUES (1, 20, 'OC', 3000000, 3, 2, 2035)")
    rep = scr.annual(cur, 2036)
    assert rep["fired"] == 0 and rep["status"] == "built"
    cur.execute("SELECT remaining_years, buyout_owed FROM staff_contracts WHERE team_id = 1 AND person_id = 20")
    assert cur.fetchone() == (0, 0)                   # closed, but no buyout: he moved, he wasn't fired


def test_a_coach_gm_does_not_get_a_second_gm_contract():
    """M-0105 B3: a Godfather coach-GM holds a full-control HC contract; filing a GM deal on top paid the same man
    twice (and counted both in staff_payroll)."""
    conn = _db_with_gms()
    conn.execute("INSERT INTO gm_profiles VALUES (103, 3, 'Coach-GM', 2030, NULL, 41)")
    cur = FakeCursor(conn)
    staff = scr.current_staff(cur, 2036)
    assert 3 not in {s["team_id"] for s in staff if s["person_kind"] == "GM"}
    scr.annual(cur, 2036)
    assert not [r for r in _rows(cur) if r[0] == 3 and r[1] == "GM"]


def test_a_refiled_gm_contract_keeps_his_own_pay():
    """M-0105 B5: championships are stored for coaches, not GMs, so a re-file priced an ELITE GM as STANDARD
    ($5.75M). His own deal's salary is carried forward instead."""
    conn = _db_with_gms()
    conn.execute("INSERT INTO staff_contracts (team_id, person_id, person_kind, annual_salary, total_years, "
                 "remaining_years, signed_season) VALUES (1, 101, 'GM', 10000000, 4, 0, 2032)")
    cur = FakeCursor(conn)
    scr.annual(cur, 2036)
    filed = [r for r in _rows(cur) if r[0] == 1 and r[1] == "GM"]
    assert filed and filed[-1][3] == 10_000_000


def test_a_promotion_gm_moves_up_once_he_has_served_the_years():
    """M-0117 N2: carrying the salary forward froze a PROMOTION GM at $2.75M for ever. The old salary is a floor,
    not a freeze."""
    conn = _db_with_gms()
    conn.execute("INSERT INTO staff_contracts (team_id, person_id, person_kind, annual_salary, total_years, "
                 "remaining_years, signed_season) VALUES (1, 101, 'GM', 2750000, 4, 0, 2034)")
    cur = FakeCursor(conn)
    scr.annual(cur, 2036)                                            # the veteran now has 7 seasons
    filed = [r for r in _rows(cur) if r[0] == 1 and r[1] == "GM"]
    assert filed[-1][3] == 5_750_000                                 # STANDARD, not the frozen $2.75M


def test_a_stray_gm_contract_for_a_coach_gm_closes_without_a_buyout():
    """M-0117 N3: the old producer filed a GM deal for a coach-GM; closing it as a firing charged an $11M buyout for
    a contract that should never have existed."""
    conn = _db_with_gms()
    conn.execute("INSERT INTO gm_profiles VALUES (103, 3, 'Coach-GM', 2030, NULL, 41)")
    conn.execute("INSERT INTO staff_contracts (team_id, person_id, person_kind, annual_salary, total_years, "
                 "remaining_years, signed_season) VALUES (3, 103, 'GM', 2750000, 4, 4, 2030)")
    cur = FakeCursor(conn)
    out = scr.annual(cur, 2036)
    row = [r for r in _rows(cur) if r[0] == 3 and r[1] == "GM"][-1]
    assert row[5] == 0 and out["fired"] == 0
