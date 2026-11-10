"""Staff-contract producer (STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN U1 follow-up; CLOSE_OUT_CHECKLIST P1-22).

Offline: the producer runs on an in-memory SQLite fake with the tenures and staff_contracts schemas.
"""
import sqlite3

import pytest

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
                                      signed_season INT, full_control INT DEFAULT 0, buyout_owed INT DEFAULT 0,
                                      buyout_total INT, buyout_fired_season INT, buyout_years INT);
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


def test_a_poached_coordinator_is_not_fired_when_the_annual_run_sees_his_closed_tenure():
    """M-0125 High: filtering tenures on `end_season > season` dropped every mover at annual(S) and fired him with a
    buyout, against JR's 2026-11-09 rule that a poached coordinator owes nothing."""
    conn = _db()
    # team 2's OC (id 22) took a job elsewhere: his tenure ends at 2036, and he is employed elsewhere by then
    conn.execute("UPDATE coordinator_tenures SET end_season = 2036 WHERE coordinator_id = 22 AND team_id = 2")
    cur = FakeCursor(conn)
    scr.annual(cur, 2036)                                  # the league year he moved in
    scr.annual(cur, 2036)
    after = next(r for r in _rows(cur) if r[1] == "OC" and r[0] == 2)
    assert after[7] == 0                                   # no buyout for a mover


def test_a_hire_for_the_coming_league_year_is_not_fired():
    """M-0150 N1 HIGH: the carousel hires for season+1 before FREE_AGENCY's annual run, so reading a next-season hire
    as gone filed and fired him (on an unclamped 5-season buyout, out of owner cash) the day he was hired."""
    conn = _db_with_gms()
    conn.execute("INSERT INTO gm_profiles VALUES (103, 3, 'Next GM', 2037, NULL, NULL)")
    conn.execute("INSERT INTO staff_contracts (team_id, person_id, person_kind, annual_salary, total_years, "
                 "remaining_years, signed_season) VALUES (3, 103, 'GM', 3000000, 5, 5, 2037)")
    conn.execute("INSERT INTO coaches VALUES (12, 'Godfather')")
    conn.execute("INSERT INTO coach_records VALUES (12, 0)")
    conn.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) "
                 "VALUES (12, 3, 2037, NULL)")
    conn.execute("INSERT INTO staff_contracts (team_id, person_id, person_kind, annual_salary, total_years, "
                 "remaining_years, signed_season, full_control) VALUES (3, 12, 'HC', 20000000, 5, 5, 2037, 1)")
    cur = FakeCursor(conn)
    rep = scr.annual(cur, 2036)
    assert rep["fired"] == 0
    for kind, person_id in (("GM", 103), ("HC", 12)):
        row = next(r for r in _rows(cur) if r[0] == 3 and r[1] == kind and r[2] == person_id)
        assert (row[5], row[7]) == (5, 0)              # untouched: full deal, nothing owed


def test_a_hot_seat_settled_coach_is_not_filed_again():
    """The settled-row skip stays in force: a coach the hot seat already fired is not re-filed by the annual run."""
    conn = _db()
    cur = FakeCursor(conn)
    scr.annual(cur, 2036)
    conn.execute("UPDATE coach_tenures SET end_season = 2036 WHERE coach_id = 11")
    conn.execute("UPDATE staff_contracts SET remaining_years = 0, buyout_owed = 12000000 "
                 "WHERE team_id = 2 AND person_kind = 'HC'")
    rep = scr.annual(cur, 2036)
    assert rep["filed"] == 0 and rep["fired"] == 0


def test_a_coach_rehired_into_the_same_seat_is_filed_again():
    """M-0150 L1: the settled-row skip had no season bound, so a fired coach later re-hired by the same team was
    never filed a contract again."""
    conn = _db()
    cur = FakeCursor(conn)
    scr.annual(cur, 2035)
    conn.execute("UPDATE staff_contracts SET remaining_years = 0, buyout_owed = 12000000 "
                 "WHERE team_id = 2 AND person_kind = 'HC'")
    conn.execute("UPDATE coach_tenures SET end_season = 2036 WHERE coach_id = 11")
    conn.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) "
                 "VALUES (11, 2, 2038, NULL)")
    scr.annual(cur, 2036)
    scr.annual(cur, 2037)                    # his coming-season deal is filed with the hire (M-0196 B1)
    scr.annual(cur, 2038)
    cur.execute("SELECT remaining_years, signed_season, buyout_owed FROM staff_contracts "
                "WHERE team_id = 2 AND person_kind = 'HC' AND person_id = 11 ORDER BY id")
    rows = cur.fetchall()
    assert rows[0] == (0, 2035, 12000000)                      # the old deal stays settled
    assert rows[-1][:2] == (4, 2038)                           # a fresh 4-year deal from the rehire season


def test_a_fired_coach_is_bought_out_for_the_years_he_had_left():
    """M-0125 Medium: the buyout is the deal's remaining seasons as of the season he stopped, not one more."""
    conn = _db()
    cur = FakeCursor(conn)
    scr.annual(cur, 2035)
    scr.annual(cur, 2036)
    before = next(r for r in _rows(cur) if r[1] == "HC" and r[0] == 2)
    conn.execute("UPDATE coach_tenures SET end_season = 2036 WHERE coach_id = 11")
    scr.annual(cur, 2037)                                  # the run after the fired season
    after = next(r for r in _rows(cur) if r[1] == "HC" and r[0] == 2)
    assert after[5] == 0 and after[7] == before[3] * 2


# ------------------------------------------------------------- one invariant (M-0158) ----
# Every contract year is paid exactly once: as salary if he held the job that season, or as a buyout instalment if
# he was fired before it — never both, never neither, never a year past the deal. The table runs the real producer
# (annual at FREE_AGENCY, with the season just played) and both hot-seat settles over a one-person league.
_INVARIANT_DDL = """
    CREATE TABLE coaches (id INTEGER PRIMARY KEY, coach_name TEXT);
    CREATE TABLE coach_records (coach_id INT, super_bowl_wins INT);
    CREATE TABLE coach_tenures (id INTEGER PRIMARY KEY AUTOINCREMENT, coach_id INT, team_id INT,
                                start_season INT, end_season INT);
    CREATE TABLE coordinators (id INTEGER PRIMARY KEY, coordinator_name TEXT);
    CREATE TABLE coordinator_tenures (id INTEGER PRIMARY KEY AUTOINCREMENT, coordinator_id INT, team_id INT,
                                      role TEXT, start_season INT, end_season INT);
    CREATE TABLE gm_profiles (id INTEGER PRIMARY KEY, team_id INT, gm_name TEXT, start_season INT, end_season INT,
                              coach_id INT);
    CREATE TABLE staff_contracts (id INTEGER PRIMARY KEY AUTOINCREMENT, team_id INT, person_id INT,
                                  person_kind TEXT, annual_salary INT, total_years INT, remaining_years INT,
                                  signed_season INT, full_control INT DEFAULT 0, buyout_owed INT DEFAULT 0,
                                  buyout_total INT, buyout_fired_season INT, buyout_years INT);
"""

DEALS = [
    dict(name="hc-mid-replaced", kind="HC", salary=6_000_000, years=4, signed=2026, last=2027, path="annual",
         successor=2028, expect=["S S B B"]),
    dict(name="hc-mid-vacant", kind="HC", salary=6_000_000, years=4, signed=2026, last=2027, path="annual",
         expect=["S S B B"]),
    dict(name="hc-first-year", kind="HC", salary=6_000_000, years=4, signed=2026, last=2026, path="annual",
         expect=["S B B B"]),
    dict(name="hc-last-year", kind="HC", salary=6_000_000, years=4, signed=2026, last=2029, path="annual",
         expect=["S S S S"]),
    dict(name="hc-kept-past-his-deal", kind="HC", salary=6_000_000, years=4, signed=2026, last=2032, path="annual",
         kept=True, expect=["S S S S", "S S S S"]),
    dict(name="hc-kept-then-leaves", kind="HC", salary=6_000_000, years=4, signed=2026, last=2030, path="annual",
         leaving_past_his_deal=True, expect=["S S S S", "S"]),   # the worked season paid, nothing after
    dict(name="full-control-kept-then-leaves", kind="HC", salary=20_000_000, years=3, signed=2031, last=2034,
         path="fc_settle", full_control=True, leaving_past_his_deal=True, expect=["S S S", "S"]),
    dict(name="gm-kept-then-fired", kind="GM", salary=3_000_000, years=4, signed=2033, last=2037, path="gm_settle",
         expect=["S S S S", "S"]),                 # the hot seat settles him with nothing owed; that season is paid
    dict(name="oc-lateral", kind="OC", salary=2_000_000, years=3, signed=2033, last=2034, path="annual",
         moved_to=2, voided=True, expect=["S S -"]),
    dict(name="oc-poached-to-a-head-coach-job", kind="OC", salary=2_000_000, years=3, signed=2033, last=2034,
         path="annual", poached_to_hc=True, voided=True, expect=["S S -"]),
    dict(name="gm-mid-deal", kind="GM", salary=3_000_000, years=4, signed=2033, last=2035, path="gm_settle",
         expect=["S S B B"]),
    dict(name="gm-first-year", kind="GM", salary=3_000_000, years=4, signed=2033, last=2033, path="gm_settle",
         expect=["B B B B"]),
    dict(name="gm-last-year", kind="GM", salary=3_000_000, years=4, signed=2033, last=2036, path="gm_settle",
         expect=["S S S B"]),
    dict(name="gm-replaced", kind="GM", salary=3_000_000, years=4, signed=2033, last=2035, path="gm_settle",
         gm_successor=2036, expect=["S S B B"]),
    dict(name="gm-league-start-fired-first", kind="GM", salary=3_000_000, years=4, signed=2033, last=2033,
         path="gm_settle", no_initial_run=True, expect=["S B B B"]),   # no contract row when the hot seat fired him
    dict(name="full-control-100m", kind="HC", salary=20_000_000, years=5, signed=2033, last=2035, path="fc_settle",
         full_control=True, expect=["S S B B B"]),
    dict(name="full-control-first-year", kind="HC", salary=20_000_000, years=5, signed=2031, last=2031,
         path="fc_settle", full_control=True, expect=["B B B B B"]),
    dict(name="full-control-last-year", kind="HC", salary=4_000_000, years=5, signed=2031, last=2035,
         path="fc_settle", full_control=True, expect=["S S S S B"]),
    dict(name="full-control-kept-past-his-deal", kind="HC", salary=20_000_000, years=3, signed=2033, last=2036,
         path="annual", full_control=True, kept=True, through=2039, expect=["S S S", "S S S S"]),
    dict(name="coach-gm", kind="HC", salary=20_000_000, years=5, signed=2030, last=2032, path="fc_settle",
         full_control=True, coach_gm=True, expect=["S S B B B"]),
]


def _scenario(case):
    """One team, one staffer on the case's deal; returns (conn, cur, person_id)."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(_INVARIANT_DDL)
    kind, signed, last = case["kind"], case["signed"], case["last"]
    end = None if case.get("kept") else last
    person_id = {"GM": 101, "HC": 10}.get(kind, 20)
    if kind == "GM":
        conn.execute("INSERT INTO gm_profiles VALUES (101, 1, 'GM', ?, NULL, NULL)", (signed,))
        if case.get("gm_successor"):
            conn.execute("INSERT INTO gm_profiles VALUES (102, 1, 'Next GM', ?, NULL, NULL)",
                         (case["gm_successor"],))
    elif kind == "HC":
        conn.execute("INSERT INTO coaches VALUES (10, 'Coach')")
        conn.execute("INSERT INTO coach_records VALUES (10, 0)")
        conn.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) "
                     "VALUES (10, 1, ?, ?)", (signed, end))
        if case.get("coach_gm"):
            conn.execute("INSERT INTO gm_profiles VALUES (101, 1, 'Coach-GM', ?, NULL, 10)", (signed,))
        if case.get("successor"):
            conn.execute("INSERT INTO coaches VALUES (11, 'Successor')")
            conn.execute("INSERT INTO coach_records VALUES (11, 0)")
            conn.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) "
                         "VALUES (11, 1, ?, NULL)", (case["successor"],))
    else:
        conn.execute("INSERT INTO coordinators VALUES (20, 'Coord')")
        conn.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season) "
                     "VALUES (20, 1, ?, ?, ?)", (kind, signed, end))
        if case.get("moved_to"):
            conn.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season) "
                         "VALUES (20, ?, ?, ?, NULL)", (case["moved_to"], kind, last + 1))
        if case.get("poached_to_hc"):
            conn.execute("INSERT INTO coaches VALUES (12, 'Coord')")
            conn.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) "
                         "VALUES (12, 2, ?, NULL)", (last + 1,))
    cur = FakeCursor(conn)
    if not case.get("no_initial_run"):
        scr.annual(cur, signed)                        # the producer files the deal
        conn.execute("UPDATE staff_contracts SET annual_salary = ?, total_years = ?, remaining_years = ?, "
                     "full_control = ?", (case["salary"], case["years"], case["years"],
                                          1 if case.get("full_control") else 0))
    return conn, cur, person_id


@pytest.mark.parametrize("case", DEALS, ids=[d["name"] for d in DEALS])
def test_every_contract_year_is_paid_exactly_once(case):
    """S = paid as salary that season, B = that season's buyout instalment, - = he moved on. Every year of every
    deal is exactly one of them, and salary seasons + buyout total = annual salary x length."""
    from engines import coach_hot_seat as chs, gm_rollover as gr, staff_contracts_engine as sce

    conn, cur, person_id = _scenario(case)
    through = case.get("through") or max(case["last"] + 1, case["signed"] + case["years"])
    active = {}
    for season in range(case["signed"], through + 1):
        if season == case["last"] and case["path"] != "annual":
            if case["kind"] == "GM":
                conn.execute("UPDATE gm_profiles SET end_season = ? WHERE id = 101", (season,))
                gr._settle_gm_contract(cur, person_id, log=lambda *a: None, person_season=season)
            else:
                if case.get("coach_gm"):
                    conn.execute("UPDATE gm_profiles SET end_season = ? WHERE id = 101", (season,))
                chs._settle_full_control(cur, person_id, season)
        scr.annual(cur, season)
        cur.execute("""SELECT id, remaining_years, signed_season FROM staff_contracts
                       WHERE person_id = %s AND person_kind = %s AND team_id = 1""", (person_id, case["kind"]))
        for rid, remaining, signed_at in cur.fetchall():
            active.setdefault(rid, {})[season] = bool(remaining and remaining > 0 and signed_at <= season)
    cur.execute("""SELECT id, annual_salary, total_years, signed_season, buyout_total, buyout_fired_season,
                          buyout_years
                   FROM staff_contracts WHERE person_id = %s AND person_kind = %s AND team_id = 1 ORDER BY id""",
                (person_id, case["kind"]))
    rows = cur.fetchall()
    assert len(rows) == len(case["expect"]), rows
    for (rid, salary, years, signed_at, total, fired, n), want in zip(rows, case["expect"]):
        status = []
        for season in range(signed_at, signed_at + years):
            instalment = sce.buyout_instalment({"buyout_total": total, "buyout_fired_season": fired,
                                                "buyout_years": n}, season)
            on_payroll = active.get(rid, {}).get(season, False)
            assert not (on_payroll and instalment)                # never twice
            status.append("S" if on_payroll else ("B" if instalment else "-"))
        assert " ".join(status) == want, (rid, status, want)
        if not case.get("voided"):                    # a poach or lateral move voids the rest of the deal
            assert status.count("S") * int(salary) + int(total or 0) == int(salary) * int(years)
        if total:
            assert int(n) == status.count("B")
            assert int(fired) == int(signed_at) + status.index("B")
    if case.get("coach_gm"):
        cur.execute("SELECT count(*) FROM staff_contracts WHERE person_kind = 'GM'")
        assert cur.fetchone()[0] == 0                        # one man, one deal
    if case.get("leaving_past_his_deal"):
        cur.execute("SELECT annual_salary, full_control FROM staff_contracts WHERE person_id = %s "
                    "ORDER BY id DESC LIMIT 1", (person_id,))
        pay, flagged = cur.fetchone()
        assert int(pay) == case["salary"]                    # exactly his own salary, not a market reprice
        if case.get("full_control"):
            assert not flagged                               # his full-control seat is released with him
            cur.execute("SELECT count(*) FROM staff_contracts WHERE full_control AND remaining_years > 0")
            assert cur.fetchone()[0] == 0
    if case.get("full_control") and case.get("kept"):
        # JR: a full-control HC's re-sign keeps full control and its market, so he still counts toward the ceiling
        cur.execute("SELECT full_control, annual_salary FROM staff_contracts WHERE person_id = %s "
                    "ORDER BY id DESC LIMIT 1", (person_id,))
        flagged, pay = cur.fetchone()
        assert flagged in (1, True) and int(pay) >= case["salary"]

def test_experience_is_the_seasons_in_the_role_across_his_tenures():
    """M-0190: an HC on his fourth team keeps his career experience; the current tenure alone priced him as a
    first-timer."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(_INVARIANT_DDL)
    conn.execute("INSERT INTO coaches VALUES (10, 'Mover')")
    conn.execute("INSERT INTO coach_records VALUES (10, 0)")
    conn.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) VALUES "
                 "(10, 2, 2019, 2021), (10, 3, 2022, 2024), (10, 1, 2025, NULL)")
    staff = scr.current_staff(FakeCursor(conn), 2030)
    assert next(s for s in staff if s["person_kind"] == "HC")["experience"] == 12


def test_a_hire_for_the_coming_season_gets_his_contract_now():
    """M-0196 B1: the save writer needs a live staff_contracts row at the stage the carousel hires, not a year
    later, and the coming season's deal must not be charged to this one."""
    conn = _db()
    conn.execute("INSERT INTO coaches VALUES (12, 'Next Coach')")
    conn.execute("INSERT INTO coach_records VALUES (12, 0)")
    conn.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) "
                 "VALUES (12, 3, 2037, NULL)")
    cur = FakeCursor(conn)
    first = scr.annual(cur, 2036)
    cur.execute("SELECT annual_salary, total_years, remaining_years, signed_season FROM staff_contracts "
                "WHERE person_id = 12 AND person_kind = 'HC'")
    salary, years, remaining, signed = cur.fetchone()
    assert (years, remaining, signed) == (4, 4, 2037) and salary > 0
    cur.execute("SELECT sum(annual_salary) FROM staff_contracts WHERE signed_season <= 2036 AND remaining_years > 0")
    assert first["payroll"] == cur.fetchone()[0]               # the 2037 deal is not in 2036's payroll
    assert scr.annual(cur, 2037)["filed"] == 0                 # and he is not filed twice
