"""engines/nfl_mirror/staff.py: real coaching changes into Mu's tenure tables (NFL_WEEKLY_MIRROR_PLAN §2.7, §4).

Offline: an in-memory SQLite league behind the shared psycopg-like FakeCursor. The tenure reads are checked with
engines/coach_save_sync._wanted, the reader the league office's staff write uses.

Real PostgreSQL: `ExistingIdentityWithoutProfile` (audit F1) builds `mu_scratch_u9_*` from db/init_db.sql, runs
apply_staff_changes then coach_save_sync.plan_staff_edits, and drops the database; every test rolls its transaction
back. It skips when PostgreSQL is unavailable.
"""
import csv
import os
import pathlib
import sqlite3
import unittest
import uuid

import pytest

from engines import coach_save_sync as cs
from engines.nfl_mirror import staff
from engines.nfl_mirror.staff import StaffChange
from tests.test_inbox_store import FakeCursor

SEASON = 2026
ROOT = pathlib.Path(__file__).resolve().parent.parent
ADMIN_DSN = os.environ.get("ADMIN_DATABASE_URL",
                           "host=localhost dbname=postgres user=postgres password=postgres")


def _scratch_dsn(name):
    return f"host=localhost dbname={name} user=postgres password=postgres"


def _drop_scratch(name):
    import psycopg
    with psycopg.connect(ADMIN_DSN, autocommit=True, connect_timeout=3) as admin:
        admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _make_scratch():
    """A fresh `mu_scratch_u9_*` database built from db/init_db.sql; dropped by the caller."""
    import psycopg
    name = f"mu_scratch_u9_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(ADMIN_DSN, autocommit=True, connect_timeout=3) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        with psycopg.connect(_scratch_dsn(name), autocommit=True) as conn:
            conn.execute((ROOT / "db" / "init_db.sql").read_text())
    except Exception:
        _drop_scratch(name)
        raise
    return name

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


def test_existing_coach_matched_by_name_gets_a_profile_not_a_second_row():
    """Audit F1: an existing identity with no profile gets a CREATED one, so the staff write can use him."""
    conn, cur = _league()
    conn.execute("INSERT INTO coaches (id, coach_name) VALUES (30, 'Mike McDaniel')")
    conn.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) VALUES (30, 1, 2022, 2025)")
    change = _hc("BUF", "Sean McDermott", "mike mcdaniel jr.")
    (move,) = staff.apply_staff_changes(cur, SEASON, [change])
    assert _count(conn, "coaches") == 3
    assert move["detail"]["person_id"] == 30 and move["detail"]["created"] is False
    assert move["detail"]["profile"] == "CREATED"
    assert conn.execute("SELECT source, position FROM coach_madden_profiles "
                        "WHERE person_kind = 'COACH' AND person_id = 30").fetchone() == ("CREATED", "HeadCoach")
    assert cs._wanted(cur, SEASON)[(2, "HC")]["person_id"] == 30
    assert staff.apply_staff_changes(cur, SEASON, [change]) == []      # idempotent


def test_existing_coordinator_matched_by_name_gets_a_profile_not_a_second_row():
    """Audit F1 for a coordinator: no duplicate `coordinators` row, one CREATED profile for the matched id."""
    conn, cur = _league()
    conn.execute("INSERT INTO coordinators (id, coordinator_name, side, scheme, playbook) "
                 "VALUES (210, 'Leslie Frazier', 'DEFENSIVE', '4-3', 'Buffalo Defense')")
    change = StaffChange(team="BUF", role="DC", out_name="Bobby Babich", in_name="Leslie Frazier", week=9)
    (move,) = staff.apply_staff_changes(cur, SEASON, [change])
    assert _count(conn, "coordinators") == 5
    assert move["detail"]["person_id"] == 210 and move["detail"]["created"] is False
    assert move["detail"]["profile"] == "CREATED"
    assert conn.execute("SELECT source, position FROM coach_madden_profiles WHERE person_kind = 'COORD' "
                        "AND person_id = 210").fetchone() == ("CREATED", "DefensiveCoordinator")
    assert cs._wanted(cur, SEASON)[(2, "DC")]["person_id"] == 210
    assert staff.apply_staff_changes(cur, SEASON, [change]) == []


def test_coordinator_promoted_to_head_coach_keeps_his_madden_record():
    conn, cur = _league()
    staff.apply_staff_changes(cur, SEASON, [_hc("KC", "Andy Reid", "Steve Spagnuolo")])
    coach_id = conn.execute("SELECT id FROM coaches WHERE coach_name = 'Steve Spagnuolo'").fetchone()[0]
    assert conn.execute("SELECT person_kind, person_id, presentation_id, position FROM coach_madden_profiles "
                        "WHERE presentation_id = 4").fetchone() == ("COACH", coach_id, 4, "HeadCoach")
    assert _count(conn, "coach_madden_profiles") == 6   # his record moved, no second person invented


# ---------- wave-1 audit F3: a promotion's source coordinator seat (plan §8 Q2) ----------

def _promotion(replacement=None, team="KC", out_name="Andy Reid", source_team=None):
    """The HC change promoting KC's DC Steve Spagnuolo, optionally with the named replacement for his old seat."""
    changes = [StaffChange(team=team, role="HC", out_name=out_name, in_name="Steve Spagnuolo", week=8)]
    if replacement:
        changes.append(StaffChange(team=source_team or team, role="DC", out_name="Steve Spagnuolo",
                                   in_name=replacement, week=8, source="CSV"))
    return changes


def test_promotion_with_a_named_replacement_resolves_both_seats():
    """F3 mechanical half: the named OC/DC change ends the promoted man's tenure and installs its own person, so
    `_wanted` asks for each man once, every seat has a distinct profile, and a repeat apply writes nothing."""
    conn, cur = _league()
    changes = _promotion("Dave Aranda")
    moves = staff.apply_staff_changes(cur, SEASON, changes)
    assert [m["status"] for m in moves] == ["PLANNED", "PLANNED"]

    # the promotion ended the promoted man's coordinator tenure; the named man holds the seat
    assert conn.execute("SELECT end_season FROM coordinator_tenures WHERE coordinator_id = 101").fetchone() == (SEASON,)
    aranda = conn.execute("SELECT id FROM coordinators WHERE coordinator_name = 'Dave Aranda'").fetchone()[0]
    wanted = cs._wanted(cur, SEASON)
    assert (wanted[(1, "HC")]["name"], wanted[(1, "HC")]["person_kind"]) == ("Steve Spagnuolo", "COACH")
    assert (wanted[(1, "DC")]["person_id"], wanted[(1, "DC")]["person_kind"]) == (aranda, "COORD")

    # no man is wanted in two seats, and every wanted seat resolves to its own profile: nothing points at the
    # COORD identity whose profile the promotion moved to COACH (the "promotion skipped … no row" defect).
    persons = [(v["person_kind"], v["person_id"]) for v in wanted.values()]
    assert len(persons) == len(set(persons))
    profiles = set(conn.execute("SELECT person_kind, person_id FROM coach_madden_profiles").fetchall())
    assert set(persons) <= profiles
    assert ("COORD", 101) not in profiles

    before = _snapshot(conn)
    assert staff.apply_staff_changes(cur, SEASON, changes) == []
    assert _snapshot(conn) == before


def test_promotion_with_the_named_replacement_change_first_still_resolves():
    """The look-ahead is by change, not by position: a replacement that already wrote its seat leaves nothing to
    refuse when the promotion is handled afterwards."""
    conn, cur = _league()
    changes = list(reversed(_promotion("Dave Aranda")))
    moves = staff.apply_staff_changes(cur, SEASON, changes)
    assert [m["status"] for m in moves] == ["PLANNED", "PLANNED"]
    assert not any(m["status"] == "REFUSED" for m in moves)
    wanted = cs._wanted(cur, SEASON)
    assert wanted[(1, "HC")]["name"] == "Steve Spagnuolo" and wanted[(1, "DC")]["name"] == "Dave Aranda"
    assert conn.execute("SELECT end_season FROM coordinator_tenures WHERE coordinator_id = 101").fetchone() == (SEASON,)


def test_promotion_without_a_named_replacement_is_refused():
    """F3, Q2: with no named replacement the source seat is a REFUSED move with its reason, its tenure rows are
    untouched, and a re-plan keeps refusing rather than letting the week look cleanly written."""
    conn, cur = _league()
    change = _promotion()[0]
    moves = staff.apply_staff_changes(cur, SEASON, [change])
    assert [m["status"] for m in moves] == ["PLANNED", "REFUSED"]
    refused = moves[1]
    assert refused["kind"] == "STAFF" and refused["to_team_id"] == 1
    assert refused["reason"] == "promoted to HC with no named DC replacement"
    assert refused["detail"]["team"] == "KC" and refused["detail"]["role"] == "DC"
    assert refused["detail"]["out_name"] == "Steve Spagnuolo" and refused["detail"]["in_name"] is None
    assert refused["detail"]["person_kind"] == "COORD" and refused["detail"]["person_id"] == 101

    # the source seat's tenure tables are untouched: Mu still has him as Kansas City's current DC
    assert conn.execute("SELECT end_season FROM coordinator_tenures WHERE coordinator_id = 101").fetchone() == (None,)
    wanted = cs._wanted(cur, SEASON)
    assert wanted[(1, "HC")]["name"] == "Steve Spagnuolo"
    assert wanted[(1, "DC")]["name"] == "Steve Spagnuolo"
    # his one profile moved to the coach identity (the carousel's D2 rule), which is why the seat needs an answer
    coach_id = conn.execute("SELECT id FROM coaches WHERE coach_name = 'Steve Spagnuolo'").fetchone()[0]
    assert conn.execute("SELECT person_kind, person_id FROM coach_madden_profiles WHERE presentation_id = 4"
                        ).fetchone() == ("COACH", coach_id)

    before = _snapshot(conn)
    again = staff.apply_staff_changes(cur, SEASON, [change])
    assert [m["status"] for m in again] == ["REFUSED"]
    assert again[0]["reason"] == "promoted to HC with no named DC replacement"
    assert _snapshot(conn) == before


def test_cross_team_promotion_resolves_the_source_team_seat():
    """A coordinator promoted to another team's HC leaves his old seat on the team he came from; a named change
    for that team + role takes the same path."""
    conn, cur = _league()
    changes = _promotion("Dave Aranda", team="BUF", out_name="Sean McDermott", source_team="KC")
    moves = staff.apply_staff_changes(cur, SEASON, changes)
    assert [m["status"] for m in moves] == ["PLANNED", "PLANNED"]
    assert conn.execute("SELECT end_season FROM coordinator_tenures WHERE coordinator_id = 101").fetchone() == (SEASON,)
    wanted = cs._wanted(cur, SEASON)
    assert (wanted[(2, "HC")]["name"], wanted[(2, "HC")]["person_kind"]) == ("Steve Spagnuolo", "COACH")
    assert (wanted[(1, "DC")]["name"], wanted[(1, "DC")]["person_kind"]) == ("Dave Aranda", "COORD")
    persons = [(v["person_kind"], v["person_id"]) for v in wanted.values()]
    assert len(persons) == len(set(persons))
    profiles = set(conn.execute("SELECT person_kind, person_id FROM coach_madden_profiles").fetchall())
    assert set(persons) <= profiles


def test_cross_team_promotion_without_a_replacement_refuses_the_source_team():
    """The refusal names the seat that is actually open (KC's DC), not the team he was promoted to (BUF)."""
    _conn, cur = _league()
    moves = staff.apply_staff_changes(cur, SEASON, [
        StaffChange(team="BUF", role="HC", out_name="Sean McDermott", in_name="Steve Spagnuolo", week=9)])
    (refused,) = [m for m in moves if m["status"] == "REFUSED"]
    assert refused["to_team_id"] == 1
    assert refused["detail"]["team"] == "KC" and refused["detail"]["role"] == "DC"
    assert refused["reason"] == "promoted to HC with no named DC replacement"


def test_normal_head_coach_and_coordinator_changes_are_unchanged():
    """A non-promotion change is untouched: no source-seat work, no REFUSED move."""
    conn, cur = _league()
    moves = staff.apply_staff_changes(cur, SEASON, [
        _hc("BUF", "Sean McDermott", "Brian Daboll"),
        StaffChange(team="KC", role="OC", out_name="Matt Nagy", in_name="Mike Kafka", week=8, source="CSV")])
    assert [m["status"] for m in moves] == ["PLANNED", "PLANNED"]
    assert not any(m["status"] == "REFUSED" for m in moves)
    wanted = cs._wanted(cur, SEASON)
    assert wanted[(2, "HC")]["name"] == "Brian Daboll" and wanted[(1, "OC")]["name"] == "Mike Kafka"


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


# ---------- audit F1 against real PostgreSQL ----------

@pytest.mark.integration
class ExistingIdentityWithoutProfile(unittest.TestCase):
    """Audit F1 required regression: an existing HC and an existing OC/DC with no `coach_madden_profiles` row are
    mirrored in (one CREATED profile for the same person id, no second person row), then apply + the real downstream
    `coach_save_sync.plan_staff_edits` produce one assign/create per changed seat and no skipped profile. A second
    apply emits no moves, and an existing SAVE profile keeps its presentation id and source.

    Real PostgreSQL in a `mu_scratch_u9_*` database built from db/init_db.sql; each test's transaction is rolled back
    and the database is dropped in tearDownClass.
    """

    @classmethod
    def setUpClass(cls):
        cls.db_name = None
        try:
            import psycopg
        except Exception as exc:                                  # pragma: no cover
            raise unittest.SkipTest(f"psycopg unavailable: {exc!r}")
        try:
            cls.db_name = _make_scratch()
            cls.conn = psycopg.connect(_scratch_dsn(cls.db_name))
            cls.cur = cls.conn.cursor()
        except Exception as exc:                                  # pragma: no cover
            if cls.db_name:
                _drop_scratch(cls.db_name)
            raise unittest.SkipTest(f"no PostgreSQL: {exc!r}")

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "conn", None) is not None:
            cls.conn.rollback()
            cls.conn.close()
        if cls.db_name:
            _drop_scratch(cls.db_name)

    def tearDown(self):
        self.conn.rollback()

    # -- fixtures --
    def _team(self, abbr="BUF"):
        self.cur.execute("INSERT INTO teams (abbreviation, name, city) VALUES (%s, %s, %s) RETURNING id",
                         (abbr, f"{abbr} Team", "City"))
        return self.cur.fetchone()[0]

    def _coach_row(self, name):
        self.cur.execute("INSERT INTO coaches (coach_name) VALUES (%s) RETURNING id", (name,))
        return self.cur.fetchone()[0]

    def _coach(self, name, team_id, start, end=None):
        pid = self._coach_row(name)
        self.cur.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) "
                         "VALUES (%s, %s, %s, %s)", (pid, team_id, start, end))
        return pid

    def _coord_row(self, name, role):
        side = "OFFENSIVE" if role == "OC" else "DEFENSIVE"
        scheme = "West Coast" if side == "OFFENSIVE" else "4-3"
        self.cur.execute("INSERT INTO coordinators (coordinator_name, side, scheme) VALUES (%s, %s, %s) "
                         "RETURNING id", (name, side, scheme))
        return self.cur.fetchone()[0]

    def _coord(self, name, team_id, role, start, end=None):
        pid = self._coord_row(name, role)
        self.cur.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season) "
                         "VALUES (%s, %s, %s, %s, %s)", (pid, team_id, role, start, end))
        return pid

    def _profile_row(self, kind, person_id, position, source="CREATED", presentation_id=None):
        self.cur.execute("""INSERT INTO coach_madden_profiles
                            (person_kind, person_id, source, presentation_id, first_name, last_name, age, position,
                             archetype, seed)
                            VALUES (%s, %s, %s, %s, 'A', 'B', 50, %s, 'OffensiveGuru', %s)""",
                         (kind, person_id, source, presentation_id, position, f"seed-{kind}-{person_id}"))

    def _count(self, table):
        self.cur.execute(f"SELECT count(*) FROM {table}")
        return self.cur.fetchone()[0]

    def _profile(self, kind, person_id):
        self.cur.execute("SELECT source, position, presentation_id FROM coach_madden_profiles "
                         "WHERE person_kind = %s AND person_id = %s", (kind, person_id))
        return self.cur.fetchone()

    def _snapshot(self, abbr, hc=None, oc=None, dc=None):
        return {"meta": {"reader_version": 8},
                "teams": [{"team_index": 0, "short_name": abbr}],
                "staff": [{"team_index": 0, "hc": hc, "oc": oc, "dc": dc}],
                "coaches": [{"pgid": pgid, "first_name": "Old", "last_name": "Coach", "team_index": 0,
                             "contract_status": "Signed", "position": "HeadCoach"}
                            for pgid in (hc, oc, dc) if pgid is not None]}

    def _changes(self, team, out, incoming, week=9):
        return [StaffChange(team=team, role="HC", out_name=out[0], in_name=incoming[0], week=week),
                StaffChange(team=team, role="OC", out_name=out[1], in_name=incoming[1], week=week),
                StaffChange(team=team, role="DC", out_name=out[2], in_name=incoming[2], week=week)]

    # -- tests --
    def test_existing_hc_and_coordinators_without_profiles_are_mirrored_and_planned(self):
        tid = self._team("BUF")
        self._coach("Old Head Coach", tid, 2025)
        self._coord("Old OC", tid, "OC", 2025)
        self._coord("Old DC", tid, "DC", 2025)
        historical = (self._coach_row("Historical Coach"), self._coord_row("Historical OC", "OC"),
                      self._coord_row("Historical DC", "DC"))
        identity_counts = (self._count("coaches"), self._count("coordinators"))
        profiles_before = self._count("coach_madden_profiles")
        changes = self._changes("BUF", ("Old Head Coach", "Old OC", "Old DC"),
                                ("Historical Coach", "Historical OC", "Historical DC"))
        moves = staff.apply_staff_changes(self.cur, SEASON, changes)

        assert [m["detail"]["person_id"] for m in moves] == list(historical)
        assert all(m["detail"]["created"] is False and m["detail"]["profile"] == "CREATED" for m in moves)
        # No second person row: the same identities now carry one CREATED profile each.
        assert (self._count("coaches"), self._count("coordinators")) == identity_counts
        assert self._count("coach_madden_profiles") == profiles_before + 3
        assert self._profile("COACH", historical[0]) == ("CREATED", "HeadCoach", None)
        assert self._profile("COORD", historical[1]) == ("CREATED", "OffensiveCoordinator", None)
        assert self._profile("COORD", historical[2]) == ("CREATED", "DefensiveCoordinator", None)

        plan = cs.plan_staff_edits(self.cur, self._snapshot("BUF", hc=100, oc=101, dc=102), SEASON)
        assert plan["skipped"] == []
        creates = [a for a in plan["staff"] if a["action"] == "create"]
        assert sorted(a["position"] for a in creates) == ["DefensiveCoordinator", "HeadCoach",
                                                          "OffensiveCoordinator"]
        assert sorted(a["key"] for a in creates) == sorted(
            f"{'coach' if kind == 'COACH' else 'coord'}-{pid}"
            for kind, pid in zip(("COACH", "COORD", "COORD"), historical))
        # exactly one create per changed seat (no duplicate action and no release of a wanted man)
        assert staff.apply_staff_changes(self.cur, SEASON, changes) == []

    def test_existing_save_profile_keeps_its_presentation_id_and_source(self):
        tid = self._team("KC")
        self._coach("Old Head Coach", tid, 2025)
        oc = self._coord("Kept OC", tid, "OC", 2025)
        dc = self._coord("Kept DC", tid, "DC", 2025)
        self._profile_row("COORD", oc, "OffensiveCoordinator", source="SAVE", presentation_id=501)
        self._profile_row("COORD", dc, "DefensiveCoordinator", source="SAVE", presentation_id=502)
        save_coach = self._coach_row("Save Coach")
        self._profile_row("COACH", save_coach, "HeadCoach", source="SAVE", presentation_id=777)

        (move,) = staff.apply_staff_changes(
            self.cur, SEASON, [StaffChange(team="KC", role="HC", out_name="Old Head Coach",
                                           in_name="Save Coach", week=9)])
        assert move["detail"]["profile"] == "SAVE" and move["detail"]["created"] is False
        assert self._profile("COACH", save_coach) == ("SAVE", "HeadCoach", 777)

        plan = cs.plan_staff_edits(self.cur, self._snapshot("KC", hc=100, oc=501, dc=502), SEASON)
        assert plan["skipped"] == []
        assigns = [a for a in plan["staff"] if a["action"] == "assign"]
        assert assigns == [{"action": "assign", "pgid": 777, "team_index": 0, "position": "HeadCoach"}]
        assert [a for a in plan["staff"] if a["action"] == "create"] == []
