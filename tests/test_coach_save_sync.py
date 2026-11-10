"""engines/coach_save_sync.py: the `"staff"` block for Mu's staff for the target season (COACH_SAVE_WRITES_PLAN §4, U5).

Offline: an in-memory SQLite league behind the shared psycopg-like FakeCursor, plus synthetic reader_version-8
snapshots. No PostgreSQL connection is opened.
"""
import sqlite3

from engines import coach_save_sync as cs
from tests.test_inbox_store import FakeCursor

FINISHED = 2026          # the season just played
TARGET = 2027            # the season the save is being prepared for

DDL = """
CREATE TABLE teams (id INTEGER PRIMARY KEY, abbreviation TEXT);
CREATE TABLE coaches (id INTEGER PRIMARY KEY, coach_name TEXT);
CREATE TABLE coach_tenures (id INTEGER PRIMARY KEY AUTOINCREMENT, coach_id INT, team_id INT, start_season INT,
                            end_season INT);
CREATE TABLE coordinators (id INTEGER PRIMARY KEY, coordinator_name TEXT);
CREATE TABLE coordinator_tenures (id INTEGER PRIMARY KEY AUTOINCREMENT, coordinator_id INT, team_id INT, role TEXT,
                                  start_season INT, end_season INT);
CREATE TABLE coach_madden_profiles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    person_kind TEXT, person_id INT, source TEXT, presentation_id INT,
    first_name TEXT, last_name TEXT, age INT, position TEXT, archetype TEXT, specialty TEXT,
    level INT DEFAULT 1, years_coaching INT,
    offensive_playbook TEXT, defensive_playbook TEXT, offensive_scheme TEXT, defensive_scheme TEXT,
    team_philosophy TEXT, personality TEXT, demeanor TEXT, body_type TEXT, height INT, weight INT,
    face_shape TEXT, generic_head TEXT,
    talents TEXT DEFAULT '[]', ratings TEXT DEFAULT '{}', tendencies TEXT DEFAULT '{}',
    visuals_template INT, portrait_id INT, portrait_status TEXT DEFAULT 'NONE', headshot_path TEXT,
    seed TEXT, written_at TEXT,
    UNIQUE (person_kind, person_id)
);
CREATE TABLE staff_contracts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, team_id INT, person_kind TEXT, person_id INT,
    annual_salary BIGINT, total_years INT, remaining_years INT, signed_season INT
);
"""


# ------------------------------------------------------------------------------------------------- builders ---
def _db(teams):
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(DDL)
    conn.executemany("INSERT INTO teams (id, abbreviation) VALUES (?, ?)", [(tid, abbr) for tid, abbr, _ in teams])
    return conn


def _snapshot(teams):
    return {"meta": {"game_year": 27, "reader_version": 8},
            "teams": [{"team_index": idx, "row": idx, "short_name": abbr} for _tid, abbr, idx in teams],
            "staff": [{"team_index": idx, "hc": None, "oc": None, "dc": None} for _tid, _abbr, idx in teams],
            "coaches": []}


def _seat(snap, team_index, key, pgid):
    for seat in snap["staff"]:
        if seat["team_index"] == team_index:
            seat[key] = pgid


def _save_coach(snap, pgid, first, last, team_index, status="Signed"):
    snap["coaches"].append({"pgid": pgid, "first_name": first, "last_name": last, "team_index": team_index,
                            "contract_status": status, "position": "HeadCoach"})


def _hc(conn, coach_id, name, team_id, start, end=None):
    conn.execute("INSERT OR IGNORE INTO coaches (id, coach_name) VALUES (?, ?)", (coach_id, name))
    conn.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) VALUES (?, ?, ?, ?)",
                 (coach_id, team_id, start, end))


def _coord(conn, coord_id, name, team_id, role, start, end=None):
    conn.execute("INSERT OR IGNORE INTO coordinators (id, coordinator_name) VALUES (?, ?)", (coord_id, name))
    conn.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season) "
                 "VALUES (?, ?, ?, ?, ?)", (coord_id, team_id, role, start, end))


def _profile(conn, kind, person_id, presentation_id=None, position="HeadCoach", source="CREATED",
             visuals_template=749):
    conn.execute("""INSERT INTO coach_madden_profiles
                    (person_kind, person_id, source, presentation_id, first_name, last_name, age, position, archetype,
                     level, talents, ratings, tendencies, visuals_template, portrait_id, seed)
                    VALUES (?, ?, ?, ?, 'A', 'B', 50, ?, 'OffensiveGuru', 5, '[]', '{}', '{}', ?, 0, ?)""",
                 (kind, person_id, source, presentation_id, position, visuals_template,
                  f"madden-coach:{'coach' if kind == 'COACH' else 'coord'}-{person_id}"))


def _contract(conn, team_id, person_kind, person_id, salary, total, remaining):
    conn.execute("INSERT INTO staff_contracts (team_id, person_kind, person_id, annual_salary, total_years, "
                 "remaining_years, signed_season) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (team_id, person_kind, person_id, salary, total, remaining, TARGET))


def _actions(result):
    return [a["action"] for a in result["staff"]]


# ---------------------------------------------------------------------------------------------------- tests ---
def test_no_change_returns_an_empty_list():
    teams = [(1, "CAR", 0)]
    conn, snap = _db(teams), _snapshot(teams)
    _hc(conn, 41, "Dave Canales", 1, FINISHED - 2)
    _coord(conn, 300, "Brad Idzik", 1, "OC", FINISHED - 2)
    _coord(conn, 301, "Ejiro Evero", 1, "DC", FINISHED - 2)
    _profile(conn, "COACH", 41, 100)
    _profile(conn, "COORD", 300, 101, "OffensiveCoordinator")
    _profile(conn, "COORD", 301, 102, "DefensiveCoordinator")
    _seat(snap, 0, "hc", 100)
    _seat(snap, 0, "oc", 101)
    _seat(snap, 0, "dc", 102)
    result = cs.plan_staff_edits(FakeCursor(conn), snap, TARGET)
    assert result == {"staff": [], "skipped": [], "report": []}


def test_a_black_monday_firing_plus_an_outside_hire_is_a_release_then_a_create():
    teams = [(1, "CAR", 0)]
    conn, snap = _db(teams), _snapshot(teams)
    _hc(conn, 41, "Old Coach", 1, FINISHED - 3, FINISHED)       # fired after the season
    _hc(conn, 50, "New Coach", 1, TARGET)                        # the outside hire
    _coord(conn, 300, "Brad Idzik", 1, "OC", FINISHED - 2)
    _coord(conn, 301, "Ejiro Evero", 1, "DC", FINISHED - 2)
    _profile(conn, "COACH", 50)                                  # invented: no PresentationId yet
    _profile(conn, "COORD", 300, 101, "OffensiveCoordinator")
    _profile(conn, "COORD", 301, 102, "DefensiveCoordinator")
    _seat(snap, 0, "hc", 100)                                    # the fired man still sits in the save
    _seat(snap, 0, "oc", 101)
    _seat(snap, 0, "dc", 102)
    _save_coach(snap, 100, "Old", "Coach", 0)
    result = cs.plan_staff_edits(FakeCursor(conn), snap, TARGET)
    assert result["skipped"] == []
    assert _actions(result) == ["release", "create"]
    release, create = result["staff"]
    assert release == {"action": "release", "pgid": 100}
    assert create["key"] == "coach-50" and create["team_index"] == 0 and create["position"] == "HeadCoach"
    assert create["visuals_template"] == 749 and create["profile"]["archetype"] == "OffensiveGuru"


def test_a_poached_coordinator_is_an_assign_plus_a_promotion_at_his_old_team():
    teams = [(1, "CAR", 0), (2, "ATL", 1)]
    conn, snap = _db(teams), _snapshot(teams)
    # CAR: HC and DC unchanged; the OC leaves and the team promotes from within.
    _hc(conn, 41, "Dave Canales", 1, FINISHED - 2)
    _coord(conn, 300, "Brad Idzik", 1, "OC", FINISHED - 2, FINISHED)
    _coord(conn, 400, "Promoted Guy", 1, "OC", TARGET)
    _coord(conn, 301, "Ejiro Evero", 1, "DC", FINISHED - 2)
    # ATL: hires the poached man as its OC next season.
    _hc(conn, 42, "Raheem Morris", 2, FINISHED - 2)
    _coord(conn, 300, "Brad Idzik", 2, "OC", TARGET)
    _coord(conn, 303, "Jeff Ulbrich", 2, "DC", FINISHED - 2)
    _profile(conn, "COACH", 41, 100)
    _profile(conn, "COORD", 400, None, "OffensiveCoordinator")       # the promotion, invented
    _profile(conn, "COORD", 301, 102, "DefensiveCoordinator")
    _profile(conn, "COACH", 42, 200)
    _profile(conn, "COORD", 300, 101, "OffensiveCoordinator")        # the poached man, already in the save
    _profile(conn, "COORD", 303, 202, "DefensiveCoordinator")
    _seat(snap, 0, "hc", 100)
    _seat(snap, 0, "oc", 101)                                        # Idzik still at CAR in the save
    _seat(snap, 0, "dc", 102)
    _seat(snap, 1, "hc", 200)
    _seat(snap, 1, "oc", None)                                       # ATL has an opening
    _seat(snap, 1, "dc", 202)
    result = cs.plan_staff_edits(FakeCursor(conn), snap, TARGET)
    assert result["skipped"] == []
    assert _actions(result) == ["assign", "create"]                  # assign before create: CAR's seat frees first
    assign, create = result["staff"]
    assert assign == {"action": "assign", "pgid": 101, "team_index": 1, "position": "OffensiveCoordinator"}
    assert create["key"] == "coord-400" and create["team_index"] == 0


def test_a_retread_is_an_assign_from_free_agency_with_his_contract():
    teams = [(1, "CAR", 0)]
    conn, snap = _db(teams), _snapshot(teams)
    _hc(conn, 60, "Retread Rick", 1, TARGET)                     # hired from the pool
    _coord(conn, 300, "Brad Idzik", 1, "OC", FINISHED - 2)
    _coord(conn, 301, "Ejiro Evero", 1, "DC", FINISHED - 2)
    _profile(conn, "COACH", 60, 150, source="SAVE")             # he is already in the save, a free agent
    _profile(conn, "COORD", 300, 101, "OffensiveCoordinator")
    _profile(conn, "COORD", 301, 102, "DefensiveCoordinator")
    _contract(conn, 1, "HC", 60, 4_500_000, 3, 2)
    _seat(snap, 0, "hc", None)
    _seat(snap, 0, "oc", 101)
    _seat(snap, 0, "dc", 102)
    _save_coach(snap, 150, "Retread", "Rick", 32, status="FreeAgent")
    result = cs.plan_staff_edits(FakeCursor(conn), snap, TARGET)
    assert _actions(result) == ["assign"]
    assign = result["staff"][0]
    assert assign["pgid"] == 150 and assign["team_index"] == 0 and assign["position"] == "HeadCoach"
    assert assign["contract"] == {"ContractLength": 3, "ContractSalary": 450, "ContractYearsRemaining": 2}


def test_a_godfather_coach_gm_is_a_plain_head_coach_assign():
    teams = [(1, "CAR", 0)]
    conn, snap = _db(teams), _snapshot(teams)
    _hc(conn, 41, "Old Coach", 1, FINISHED - 3, FINISHED)
    _hc(conn, 60, "Coach Gm", 1, TARGET)                         # also the GM in Mu; the GM seat is not in the save
    _coord(conn, 300, "Brad Idzik", 1, "OC", FINISHED - 2)
    _coord(conn, 301, "Ejiro Evero", 1, "DC", FINISHED - 2)
    _profile(conn, "COACH", 60, 150, source="SAVE")
    _profile(conn, "COORD", 300, 101, "OffensiveCoordinator")
    _profile(conn, "COORD", 301, 102, "DefensiveCoordinator")
    _seat(snap, 0, "hc", 100)
    _seat(snap, 0, "oc", 101)
    _seat(snap, 0, "dc", 102)
    _save_coach(snap, 100, "Old", "Coach", 0)
    result = cs.plan_staff_edits(FakeCursor(conn), snap, TARGET)
    assert _actions(result) == ["release", "assign"]
    assign = result["staff"][1]
    assert assign == {"action": "assign", "pgid": 150, "team_index": 0, "position": "HeadCoach"}
    assert all("gm" not in {k.lower() for k in action} for action in result["staff"])       # no GM action at all


def test_an_unfinished_team_is_skipped():
    teams = [(1, "CAR", 0)]
    conn, snap = _db(teams), _snapshot(teams)
    _hc(conn, 50, "New Coach", 1, TARGET)                        # only the head-coach seat is decided
    _profile(conn, "COACH", 50)
    _seat(snap, 0, "hc", 100)                                    # a firing waiting to be written
    result = cs.plan_staff_edits(FakeCursor(conn), snap, TARGET)
    assert result["staff"] == []
    assert len(result["skipped"]) == 1
    skip = result["skipped"][0]
    assert skip["team"] == "CAR" and skip["reason"] == "hiring cycle not finished"
    assert skip["roles"] == ["OC", "DC"]


def test_the_order_is_releases_then_assigns_then_creates():
    teams = [(1, "CAR", 0)]
    conn, snap = _db(teams), _snapshot(teams)
    # HC: release + create; OC: assign from free agency; DC: release + create.
    _hc(conn, 41, "Old Coach", 1, FINISHED - 3, FINISHED)
    _hc(conn, 50, "New Coach", 1, TARGET)
    _coord(conn, 60, "Retread Rick", 1, "OC", TARGET)
    _coord(conn, 301, "Old Dc", 1, "DC", FINISHED - 3, FINISHED)
    _coord(conn, 51, "New Dc", 1, "DC", TARGET)
    _profile(conn, "COACH", 50)                                   # create
    _profile(conn, "COORD", 60, 150, source="SAVE")              # assign
    _profile(conn, "COORD", 51)                                   # create
    _seat(snap, 0, "hc", 100)
    _seat(snap, 0, "oc", None)
    _seat(snap, 0, "dc", 102)
    _save_coach(snap, 150, "Retread", "Rick", 32, status="FreeAgent")
    result = cs.plan_staff_edits(FakeCursor(conn), snap, TARGET)
    assert _actions(result) == ["release", "release", "assign", "create", "create"]
    assert [a.get("pgid") for a in result["staff"][:2]] == [100, 102]


def test_jr_own_coordinator_firing_is_written():
    teams = [(1, "CAR", 0)]                                       # CAR is a JR team
    conn, snap = _db(teams), _snapshot(teams)
    _hc(conn, 41, "Dave Canales", 1, FINISHED - 2)
    _coord(conn, 300, "Brad Idzik", 1, "OC", FINISHED - 2, FINISHED)   # JR fired him
    _coord(conn, 400, "Replacement Guy", 1, "OC", TARGET)             # invented replacement
    _coord(conn, 301, "Ejiro Evero", 1, "DC", FINISHED - 2)
    _profile(conn, "COACH", 41, 100)
    _profile(conn, "COORD", 400, None, "OffensiveCoordinator")
    _profile(conn, "COORD", 301, 102, "DefensiveCoordinator")
    _seat(snap, 0, "hc", 100)
    _seat(snap, 0, "oc", 101)
    _seat(snap, 0, "dc", 102)
    result = cs.plan_staff_edits(FakeCursor(conn), snap, TARGET)
    assert _actions(result) == ["release", "create"]
    assert result["staff"][0] == {"action": "release", "pgid": 101}
    assert result["staff"][1]["key"] == "coord-400"


def test_a_free_agent_unhired_for_three_seasons_retires_and_not_before():
    teams = [(1, "CAR", 0)]
    conn, snap = _db(teams), _snapshot(teams)
    _hc(conn, 41, "Dave Canales", 1, FINISHED - 2)
    _coord(conn, 300, "Brad Idzik", 1, "OC", FINISHED - 2)
    _coord(conn, 301, "Ejiro Evero", 1, "DC", FINISHED - 2)
    _profile(conn, "COACH", 41, 100)
    _profile(conn, "COORD", 300, 101, "OffensiveCoordinator")
    _profile(conn, "COORD", 301, 102, "DefensiveCoordinator")
    # A coach Mu released after 2031, still a free agent in the save, with no job anywhere.
    _hc(conn, 70, "Forgotten Man", 1, 2028, 2031)
    _profile(conn, "COACH", 70, 150, source="SAVE")
    _seat(snap, 0, "hc", 100)
    _seat(snap, 0, "oc", 101)
    _seat(snap, 0, "dc", 102)
    _save_coach(snap, 150, "Forgotten", "Man", 32, status="FreeAgent")
    # 2034: 2034 - 2031 - 1 = 2 complete unhired seasons -> not yet.
    assert cs.plan_staff_edits(FakeCursor(conn), snap, 2034)["staff"] == []
    # 2035: 3 complete unhired seasons -> retired.
    result = cs.plan_staff_edits(FakeCursor(conn), snap, 2035)
    assert result["staff"] == [{"action": "release", "pgid": 150, "retire": True}]
