"""engines/nfl_mirror/staff.py: real coaching changes into Mu's tenure tables (NFL_WEEKLY_MIRROR_PLAN §2.7, §4).

Offline: an in-memory SQLite league behind the shared psycopg-like FakeCursor. The tenure reads are checked with
engines/coach_save_sync._wanted, the reader the league office's staff write uses.
"""
import csv
import sqlite3

from engines import coach_save_sync as cs
from engines.nfl_mirror import staff
from engines.nfl_mirror.staff import StaffChange
from tests.test_inbox_store import FakeCursor

SEASON = 2026

DDL = """
CREATE TABLE teams (id INTEGER PRIMARY KEY, abbreviation TEXT);
CREATE TABLE coaches (id INTEGER PRIMARY KEY AUTOINCREMENT, coach_name TEXT UNIQUE NOT NULL,
                      slant TEXT NOT NULL DEFAULT 'OFFENSIVE', offensive_scheme TEXT, defensive_scheme TEXT,
                      pass_lean TEXT, fourth_down_aggressiveness TEXT, blitz_rate TEXT, man_coverage TEXT);
CREATE TABLE coach_tenures (id INTEGER PRIMARY KEY AUTOINCREMENT, coach_id INT NOT NULL, team_id INT NOT NULL,
                            start_season INT NOT NULL, end_season INT, UNIQUE (coach_id, team_id, start_season));
CREATE TABLE coordinators (id INTEGER PRIMARY KEY AUTOINCREMENT, coordinator_name TEXT NOT NULL, side TEXT NOT NULL,
                           scheme TEXT NOT NULL, playbook TEXT, pass_lean TEXT, blitz_rate TEXT, man_coverage TEXT,
                           origin_notes TEXT);
CREATE TABLE coordinator_tenures (id INTEGER PRIMARY KEY AUTOINCREMENT, coordinator_id INT NOT NULL,
                                  team_id INT NOT NULL, role TEXT NOT NULL, start_season INT NOT NULL,
                                  end_season INT, UNIQUE (coordinator_id, team_id, role, start_season));
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
"""


def _league():
    """Kansas City (1) and Buffalo (2), each with a head coach, OC and DC since 2024, all with SAVE profiles."""
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.executescript(DDL)
    conn.executemany("INSERT INTO teams (id, abbreviation) VALUES (?, ?)", [(1, "KC"), (2, "BUF")])
    conn.executemany("INSERT INTO coaches (id, coach_name, offensive_scheme, defensive_scheme) VALUES (?, ?, ?, ?)",
                     [(10, "Andy Reid", "West Coast", "4-3"), (20, "Sean McDermott", "Spread", "4-3")])
    conn.executemany("INSERT INTO coach_tenures (coach_id, team_id, start_season) VALUES (?, ?, 2024)",
                     [(10, 1), (20, 2)])
    conn.executemany("INSERT INTO coordinators (id, coordinator_name, side, scheme, playbook) VALUES (?, ?, ?, ?, ?)",
                     [(100, "Matt Nagy", "OFFENSIVE", "West Coast", "Kansas City Offense"),
                      (101, "Steve Spagnuolo", "DEFENSIVE", "4-3", "Kansas City Defense"),
                      (200, "Joe Brady", "OFFENSIVE", "Spread", "Buffalo Offense"),
                      (201, "Bobby Babich", "DEFENSIVE", "4-3", "Buffalo Defense")])
    conn.executemany("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season) "
                     "VALUES (?, ?, ?, 2024)", [(100, 1, "OC"), (101, 1, "DC"), (200, 2, "OC"), (201, 2, "DC")])
    for kind, pid, pos, pgid in (("COACH", 10, "HeadCoach", 1), ("COACH", 20, "HeadCoach", 2),
                                 ("COORD", 100, "OffensiveCoordinator", 3), ("COORD", 101, "DefensiveCoordinator", 4),
                                 ("COORD", 200, "OffensiveCoordinator", 5), ("COORD", 201, "DefensiveCoordinator", 6)):
        conn.execute("INSERT INTO coach_madden_profiles (person_kind, person_id, source, presentation_id, first_name, "
                     "last_name, age, position, archetype) VALUES (?, ?, 'SAVE', ?, 'A', 'B', 50, ?, 'OffensiveGuru')",
                     (kind, pid, pgid, pos))
    return conn, FakeCursor(conn)


def _count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def _snapshot(conn):
    return {table: conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()
            for table in ("coaches", "coach_tenures", "coordinators", "coordinator_tenures", "coach_madden_profiles")}


def _hc(team, out_name, in_name, **kw):
    return StaffChange(team=team, role="HC", out_name=out_name, in_name=in_name, week=kw.pop("week", 6), **kw)


# ---------- real_staff_changes (pure) ----------

def test_hc_change_from_per_week_coaches_across_a_bye():
    by_week = {4: {"KC": "Andy Reid", "BUF": "Sean McDermott"},
               5: {"KC": "Andy Reid"},                                # Buffalo on a bye
               6: {"KC": "Andy Reid", "BUF": "Joe Brady"}}
    changes = staff.real_staff_changes(by_week, 6, [])
    assert changes == [StaffChange(team="BUF", role="HC", out_name="Sean McDermott", in_name="Joe Brady", week=6)]
    assert staff.real_staff_changes(by_week, 5, []) == []
    assert staff.real_staff_changes(by_week, 4, []) == []            # no earlier week: nothing to compare


def test_name_spelling_differences_are_not_changes():
    by_week = {1: {"MIA": "Mike McDaniel"}, 2: {"MIA": "mike mcdaniel Jr."}}
    assert staff.real_staff_changes(by_week, 2, []) == []


def test_coordinators_from_csv_rows_for_the_week_only():
    rows = [{"date": "2026-10-20", "week": "8", "team": "kc", "role": "oc", "out_name": "Matt Nagy",
             "in_name": "Mike Kafka", "interim": "", "note": "Nagy fired"},
            {"date": "2026-10-27", "week": "9", "team": "BUF", "role": "DC", "out_name": "Bobby Babich",
             "in_name": "Leslie Frazier", "interim": "yes", "note": ""},
            {"date": "", "week": "", "team": "BUF", "role": "DC", "out_name": "", "in_name": "X Y"},
            {"date": "", "week": "8", "team": "BUF", "role": "ST", "out_name": "", "in_name": "X Y"}]
    changes = staff.real_staff_changes({}, 8, rows)
    assert changes == [StaffChange(team="KC", role="OC", out_name="Matt Nagy", in_name="Mike Kafka", week=8,
                                   interim=False, source="CSV", date="2026-10-20", note="Nagy fired")]
    (dc,) = staff.real_staff_changes({}, 9, rows)
    assert (dc.role, dc.in_name, dc.interim) == ("DC", "Leslie Frazier", True)


def test_csv_hc_row_adds_interim_to_the_game_change():
    by_week = {5: {"BUF": "Sean McDermott"}, 6: {"BUF": "Joe Brady"}}
    rows = [{"date": "2026-10-07", "week": "6", "team": "BUF", "role": "HC", "out_name": "",
             "in_name": "Joe Brady", "interim": "true", "note": "McDermott fired after week 5"}]
    (change,) = staff.real_staff_changes(by_week, 6, rows)
    assert change == StaffChange(team="BUF", role="HC", out_name="Sean McDermott", in_name="Joe Brady", week=6,
                                 interim=True, source="GAMES", date="2026-10-07",
                                 note="McDermott fired after week 5")


def test_read_staff_csv_header_only_file(tmp_path):
    with open(staff.DEFAULT_PATH, newline="") as fh:
        assert next(csv.reader(fh)) == list(staff.CSV_COLUMNS)
    assert staff.read_staff_csv() == []
    assert staff.read_staff_csv(str(tmp_path / "missing.csv")) == []
    path = tmp_path / "s.csv"
    path.write_text("date,week,team,role,out_name,in_name,interim,note\n2026-10-01,5,KC,DC, A B ,C D,,\n")
    assert staff.read_staff_csv(str(path)) == [{"date": "2026-10-01", "week": "5", "team": "KC", "role": "DC",
                                                "out_name": "A B", "in_name": "C D", "interim": "", "note": ""}]


# ---------- apply_staff_changes ----------

def test_hc_change_ends_old_tenure_and_starts_new_one():
    conn, cur = _league()
    (move,) = staff.apply_staff_changes(cur, SEASON, [_hc("BUF", "Sean McDermott", "Brian Daboll", interim=True)])
    assert conn.execute("SELECT end_season FROM coach_tenures WHERE coach_id = 20").fetchone() == (SEASON,)
    coach_id = conn.execute("SELECT id FROM coaches WHERE coach_name = 'Brian Daboll'").fetchone()[0]
    assert conn.execute("SELECT team_id, start_season, end_season FROM coach_tenures WHERE coach_id = ?",
                        (coach_id,)).fetchall() == [(2, SEASON, None)]
    # The league office's staff reader now wants the new man in Buffalo's seat for this season.
    assert cs._wanted(cur, SEASON)[(2, "HC")]["person_id"] == coach_id
    assert move["kind"] == "STAFF" and move["status"] == "PLANNED" and move["to_team_id"] == 2
    detail = move["detail"]
    assert (detail["role"], detail["created"], detail["profile"], detail["interim"]) == ("HC", True, "CREATED", True)
    assert detail["ended_person_id"] == 20 and detail["person_kind"] == "COACH"


def test_name_only_coach_gets_a_created_profile_once():
    conn, cur = _league()
    change = _hc("BUF", "Sean McDermott", "Brian Daboll")
    staff.apply_staff_changes(cur, SEASON, [change])
    before = _snapshot(conn)
    profile = conn.execute("SELECT source, first_name, last_name, position, seed, offensive_scheme "
                           "FROM coach_madden_profiles WHERE person_kind = 'COACH' AND person_id = "
                           "(SELECT id FROM coaches WHERE coach_name = 'Brian Daboll')").fetchone()
    assert profile[:4] == ("CREATED", "Brian", "Daboll", "HeadCoach")
    assert profile[4].startswith("madden-coach:coach-")
    assert profile[5] == "Spread"                    # McDermott's offensive scheme, mapped to Madden's enum
    assert staff.apply_staff_changes(cur, SEASON, [change]) == []
    assert _snapshot(conn) == before                 # idempotent: nothing written the second time


def test_existing_coach_matched_by_name_not_duplicated():
    conn, cur = _league()
    conn.execute("INSERT INTO coaches (id, coach_name) VALUES (30, 'Mike McDaniel')")
    conn.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) VALUES (30, 1, 2022, 2025)")
    (move,) = staff.apply_staff_changes(cur, SEASON, [_hc("BUF", "Sean McDermott", "mike mcdaniel jr.")])
    assert _count(conn, "coaches") == 3
    assert move["detail"]["person_id"] == 30 and move["detail"]["created"] is False
    assert move["detail"]["profile"] is None         # no save record known: left for review, nothing invented
    assert cs._wanted(cur, SEASON)[(2, "HC")]["person_id"] == 30


def test_coordinator_promoted_to_head_coach_keeps_his_madden_record():
    conn, cur = _league()
    staff.apply_staff_changes(cur, SEASON, [_hc("KC", "Andy Reid", "Steve Spagnuolo")])
    coach_id = conn.execute("SELECT id FROM coaches WHERE coach_name = 'Steve Spagnuolo'").fetchone()[0]
    assert conn.execute("SELECT person_kind, person_id, presentation_id, position FROM coach_madden_profiles "
                        "WHERE presentation_id = 4").fetchone() == ("COACH", coach_id, 4, "HeadCoach")
    assert _count(conn, "coach_madden_profiles") == 6   # his record moved, no second person invented


def test_oc_and_dc_changes_from_csv():
    conn, cur = _league()
    changes = [StaffChange(team="KC", role="OC", out_name="Matt Nagy", in_name="Mike Kafka", week=8, source="CSV"),
               StaffChange(team="BUF", role="DC", out_name="Bobby Babich", in_name="Leslie Frazier", week=8,
                           interim=True, source="CSV")]
    moves = staff.apply_staff_changes(cur, SEASON, changes)
    assert [m["detail"]["role"] for m in moves] == ["OC", "DC"]
    assert conn.execute("SELECT coordinator_id, end_season FROM coordinator_tenures WHERE coordinator_id IN (100, 201)"
                        " ORDER BY coordinator_id").fetchall() == [(100, SEASON), (201, SEASON)]
    kafka = conn.execute("SELECT id, side, scheme, playbook FROM coordinators WHERE coordinator_name = 'Mike Kafka'"
                         ).fetchone()
    assert kafka[1:] == ("OFFENSIVE", "West Coast", "Kansas City Offense")   # keeps Nagy's system
    assert conn.execute("SELECT source, position, offensive_playbook FROM coach_madden_profiles "
                        "WHERE person_kind = 'COORD' AND person_id = ?", (kafka[0],)).fetchone() == (
        "CREATED", "OffensiveCoordinator", "Chiefs")
    wanted = cs._wanted(cur, SEASON)
    assert wanted[(1, "OC")]["name"] == "Mike Kafka"
    assert wanted[(2, "DC")]["name"] == "Leslie Frazier"
    assert wanted[(1, "DC")]["name"] == "Steve Spagnuolo"   # untouched seats stay
    assert staff.apply_staff_changes(cur, SEASON, changes) == []


def test_user_team_is_never_touched():
    conn, cur = _league()
    before = _snapshot(conn)
    (move,) = staff.apply_staff_changes(cur, SEASON, [_hc("BUF", "Sean McDermott", "Brian Daboll")],
                                        user_team_ids={2})
    assert move["status"] == "SKIPPED" and "user team" in move["reason"]
    assert _snapshot(conn) == before


def test_unknown_team_and_missing_name_are_skipped():
    conn, cur = _league()
    before = _snapshot(conn)
    moves = staff.apply_staff_changes(cur, SEASON, [_hc("XXX", "A B", "C D"), _hc("KC", "Andy Reid", None)])
    assert [m["status"] for m in moves] == ["SKIPPED", "SKIPPED"]
    assert _snapshot(conn) == before


def test_nflverse_team_codes_and_a_second_change_in_one_season():
    conn, cur = _league()
    conn.execute("INSERT INTO teams (id, abbreviation) VALUES (3, 'AZ')")
    conn.execute("INSERT INTO coaches (id, coach_name) VALUES (40, 'Jonathan Gannon')")
    conn.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season) VALUES (40, 3, 2023)")
    staff.apply_staff_changes(cur, SEASON, [_hc("ARI", "Jonathan Gannon", "Interim One", week=5)])
    staff.apply_staff_changes(cur, SEASON, [_hc("ARI", "Interim One", "Interim Two", week=9)])
    assert cs._wanted(cur, SEASON)[(3, "HC")]["name"] == "Interim Two"
    assert conn.execute("SELECT count(*) FROM coach_tenures WHERE team_id = 3 AND end_season = ?",
                        (SEASON,)).fetchone() == (2,)


def test_out_name_mismatch_is_reported_but_real_life_wins():
    _conn, cur = _league()
    (move,) = staff.apply_staff_changes(cur, SEASON, [_hc("KC", "Someone Else", "Eric Bieniemy")])
    assert move["detail"]["mu_out_name"] == "Andy Reid"
    assert cs._wanted(cur, SEASON)[(1, "HC")]["name"] == "Eric Bieniemy"
