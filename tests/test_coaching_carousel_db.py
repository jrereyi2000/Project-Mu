"""The coaching carousel against the live database, rehearsing the 2035 offseason inside one transaction that is
always rolled back (nothing is ever committed). Skipped when the database or the carousel migrations are missing.

`SameSeasonReturn` (audit F2) follows the same real-PostgreSQL pattern but builds its own `mu_scratch_u9_*` database
from db/init_db.sql and drops it; each test rolls its transaction back.

Run: python3 -m unittest tests.test_coaching_carousel_db
"""
import os
import pathlib
import unittest
import uuid

try:
    import psycopg
    from pipeline.load_capture import DSN
except Exception:          # pragma: no cover
    psycopg = None

import pytest
from engines import coach_save_sync as cs
from engines import coach_hot_seat as chs
from engines import coaching_carousel as cc
from engines import fire_coordinator as fc
from engines.nfl_mirror import staff

SEASON = 2035
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


@pytest.mark.integration
class Rehearse2035(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.conn = psycopg.connect(DSN, connect_timeout=3)
        except Exception as e:
            raise unittest.SkipTest(f"no database: {e!r}")
        cur = cls.conn.cursor()
        if not cc.installed(cur):
            cls.conn.close()
            raise unittest.SkipTest("coaching carousel migrations not applied")
        cur.execute("SELECT count(*) FROM coach_hot_seat WHERE season = %s", (SEASON,))
        if cur.fetchone()[0]:
            cls.conn.close()
            raise unittest.SkipTest(f"{SEASON} already decided in this database")
        # Back to the end of 2035: undo the staff the 2036 offseason put in by hand.
        for t in ("coach_tenures", "coordinator_tenures"):
            cur.execute(f"DELETE FROM {t} WHERE start_season > %s", (SEASON,))
            cur.execute(f"UPDATE {t} SET end_season = NULL WHERE end_season >= %s", (SEASON,))
        cls.rep = cc.run(cur, SEASON, log=lambda m: None)
        cls.again = cc.run(cur, SEASON, log=lambda m: None)
        cls.cur = cur

    @classmethod
    def tearDownClass(cls):
        cls.conn.rollback()
        cls.conn.close()

    def test_every_team_decided_and_hires_made(self):
        self.cur.execute("SELECT count(*) FROM coach_hot_seat WHERE season = %s", (SEASON,))
        self.assertEqual(self.cur.fetchone()[0], 32)
        fired = len(self.rep["hot_seat"]["fired"])
        self.assertEqual(len(self.rep["hires"]["head_coaches"]), fired)

    def test_every_cpu_team_fully_staffed_next_season(self):
        cc.check_staffs(self.cur, SEASON + 1, set(cc.teams(self.cur)) - cc.jr_teams(self.cur))

    def test_jr_teams_never_fire_or_hire_head_coaches(self):
        # JR's coordinators can be poached (then his team promotes from within), but Mu fires and hires nobody there.
        self.cur.execute("""SELECT count(*) FROM coach_hot_seat h JOIN gm_team_settings s ON s.team_id = h.team_id
                            WHERE h.season = %s AND s.user_controlled AND h.fired""", (SEASON,))
        self.assertEqual(self.cur.fetchone()[0], 0)
        self.cur.execute("""SELECT count(*) FROM staff_moves m JOIN gm_team_settings s ON s.team_id = m.team_id
                            WHERE m.season = %s AND s.user_controlled
                              AND NOT (m.action = 'LEFT' OR m.source = 'PROMOTED')""", (SEASON,))
        self.assertEqual(self.cur.fetchone()[0], 0)

    def test_most_attractive_opening_hires_first(self):
        self.cur.execute("""SELECT inputs FROM staff_moves WHERE season = %s AND role = 'HC' AND action = 'HIRED'
                            ORDER BY id""", (SEASON,))
        rows = [r[0] for r in self.cur.fetchall()]
        self.assertEqual([r["opening_rank"] for r in rows], list(range(1, len(rows) + 1)))
        self.assertEqual([r["appeal"] for r in rows], sorted((r["appeal"] for r in rows), reverse=True))

    def test_a_second_run_changes_nothing(self):
        self.assertEqual(self.again["hot_seat"]["fired"], [])
        self.assertEqual(self.again["coordinators_fired"], [])
        self.assertIsNone(self.again["hires"])

    def test_poached_coordinators_leave_and_are_replaced(self):
        self.cur.execute("""SELECT from_team_id, from_role FROM staff_moves
                            WHERE season = %s AND role = 'HC' AND source = 'POACHED'""", (SEASON,))
        for team_id, role in self.cur.fetchall():
            self.cur.execute("""SELECT count(*) FROM staff_moves WHERE season = %s AND team_id = %s AND role = %s
                                AND action = 'LEFT'""", (SEASON, team_id, role))
            self.assertEqual(self.cur.fetchone()[0], 1)


@pytest.mark.integration
class JRFiresCoordinators2035(unittest.TestCase):
    """JR fires Carolina's OC (the hiring cycle fills the job) and Indianapolis's DC (he names the replacement) after
    the 2035 reviews, rehearsed and rolled back like Rehearse2035."""
    NAME = "Testy Replacementson"

    @classmethod
    def setUpClass(cls):
        try:
            cls.conn = psycopg.connect(DSN, connect_timeout=3)
        except Exception as e:
            raise unittest.SkipTest(f"no database: {e!r}")
        cur = cls.conn.cursor()
        if not cc.installed(cur):
            cls.conn.close()
            raise unittest.SkipTest("coaching carousel migrations not applied")
        cur.execute("SELECT count(*) FROM coach_hot_seat WHERE season = %s", (SEASON,))
        if cur.fetchone()[0]:
            cls.conn.close()
            raise unittest.SkipTest(f"{SEASON} already decided in this database")
        for t in ("coach_tenures", "coordinator_tenures"):
            cur.execute(f"DELETE FROM {t} WHERE start_season > %s", (SEASON,))
            cur.execute(f"UPDATE {t} SET end_season = NULL WHERE end_season >= %s", (SEASON,))
        cls.cur, quiet = cur, (lambda m: None)
        # Fired before any review (a new hot-seat row), then taken back.
        cls.before = cc.staff_in(cur, SEASON)
        cls.early = fc.fire(cur, SEASON, "IND", "OC", log=quiet)
        cur.execute("SELECT phase, score FROM coordinator_hot_seat WHERE season = %s AND role = 'OC' AND team_id = "
                    "(SELECT id FROM teams WHERE abbreviation = 'IND')", (SEASON,))
        cls.early_row = cur.fetchone()
        fc.undo(cur, SEASON, "IND", "OC", log=quiet)
        # The reviews first, so JR's firings update the review's rows; then the rest of the carousel.
        chs.run(cur, SEASON, log=quiet)
        cc.review_coordinators(cur, SEASON, log=quiet)
        cls.car = fc.fire(cur, SEASON, "CAR", "OC", log=quiet)
        cls.ind = fc.fire(cur, SEASON, "IND", "DC", replacement=cls.NAME, source="PROMOTED",
                          background="linebackers coach", log=quiet)
        cls.rep = cc.run(cur, SEASON, log=quiet)
        cls.after = cc.staff_in(cur, SEASON + 1)
        cls.ids = {a: tid for tid, a in ((k, v["abbr"]) for k, v in cc.teams(cur).items())}

    @classmethod
    def tearDownClass(cls):
        cls.conn.rollback()
        cls.conn.close()

    def test_undo_restores_the_staff(self):
        self.assertEqual(self.early_row[0], self.early["phase"])
        self.assertEqual(self.after.get((self.ids["IND"], "OC"), {}).get("coordinator_id"),
                         self.before[(self.ids["IND"], "OC")]["coordinator_id"])

    def test_firings_are_recorded_like_mus(self):
        self.cur.execute("""SELECT t.abbreviation, h.role, h.fired, h.inputs->>'fired_by', h.inputs->'review' IS NOT NULL
                            FROM coordinator_hot_seat h JOIN teams t ON t.id = h.team_id
                            WHERE h.season = %s AND h.inputs->>'fired_by' = 'JR' ORDER BY 1""", (SEASON,))
        self.assertEqual(self.cur.fetchall(), [("CAR", "OC", True, "JR", True), ("IND", "DC", True, "JR", True)])

    def test_named_replacement_takes_the_job(self):
        self.assertEqual(self.ind["replacement"]["source"], "PROMOTED")
        self.assertEqual(self.after[(self.ids["IND"], "DC")]["name"], self.NAME)
        self.assertEqual(self.after[(self.ids["IND"], "DC")]["scheme"],
                         cc.staff_in(self.cur, SEASON)[(self.ids["IND"], "DC")]["scheme"])  # keeps the system

    def test_hiring_cycle_fills_the_open_job(self):
        self.assertIsNotNone(self.rep["hires"])                 # JR's named hire doesn't block the cycle
        jr = self.rep["hires"]["jr_hires"]
        self.assertEqual([(x["team"], x["role"]) for x in jr], [("CAR", "OC")])
        new = self.after[(self.ids["CAR"], "OC")]
        self.assertEqual(new["name"], jr[0]["coordinator"])
        self.assertNotEqual(new["name"], self.car["fired"])
        cc.check_staffs(self.cur, SEASON + 1, {self.ids["CAR"], self.ids["IND"]})

    def test_only_jrs_openings_are_filled_on_his_teams(self):
        self.cur.execute("""SELECT count(*) FROM staff_moves m JOIN gm_team_settings s ON s.team_id = m.team_id
                            WHERE m.season = %s AND s.user_controlled AND m.action = 'HIRED' AND m.role = 'HC'""",
                         (SEASON,))
        self.assertEqual(self.cur.fetchone()[0], 0)

    def test_errors(self):
        with self.assertRaises(ValueError):
            fc.fire(self.cur, SEASON, "KC", "OC", log=lambda m: None)          # not JR's team
        with self.assertRaises(ValueError):
            fc.fire(self.cur, SEASON, "CAR", "OC", log=lambda m: None)         # already fired / no one left
        with self.assertRaises(ValueError):
            fc.undo(self.cur, SEASON, "CAR", "OC", log=lambda m: None)         # the cycle already ran


@pytest.mark.integration
class StaffAlreadyNamedForNextSeason(unittest.TestCase):
    """The 2036 Wild Card run (2026-10-27): Step 7d ran the carousel for 2035, whose 2036 staff the offseason
    manifest had already named and whose coordinators the 2036 review had already put on the hot seat. A new head
    coach bringing his own coordinator deleted that coordinator as if the cycle had just promoted him, and the
    coordinator_hot_seat row referencing him raised ForeignKeyViolation. Rehearsed on that state and rolled back."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.conn = psycopg.connect(DSN, connect_timeout=3)
        except Exception as e:
            raise unittest.SkipTest(f"no database: {e!r}")
        cur = cls.conn.cursor()
        if not cc.installed(cur):
            cls.conn.close()
            raise unittest.SkipTest("coaching carousel migrations not applied")
        cur.execute("SELECT count(*) FROM coach_hot_seat WHERE season = %s", (SEASON,))
        if cur.fetchone()[0]:
            cls.conn.close()
            raise unittest.SkipTest(f"{SEASON} already decided in this database")
        # Every 2036 coordinator reviewed (on the hot seat, kept), as on the live database after 2036's Black Monday.
        cls.named = cc.staff_in(cur, SEASON + 1)
        for (tid, role), c in cls.named.items():
            cur.execute("""INSERT INTO coordinator_hot_seat (season, team_id, role, coordinator_id, score, fired, phase,
                                                             inputs) VALUES (%s, %s, %s, %s, 0, false, 'BLACK_MONDAY', '{}')
                           ON CONFLICT DO NOTHING""", (SEASON + 1, tid, role, c["coordinator_id"]))
        cls.cur = cur

    @classmethod
    def tearDownClass(cls):
        cls.conn.rollback()
        cls.conn.close()

    def test_hiring_cycle_keeps_coordinators_it_did_not_create(self):
        # Raised ForeignKeyViolation before. The cycle now gets to its own staff check, which refuses (2036's head
        # coaches are already named), so the weekly run never decides 2035 (coaching_carousel.owns).
        self.assertFalse(cc.owns(self.cur, SEASON))
        with self.assertRaisesRegex(ValueError, "staff check"):
            cc.run(self.cur, SEASON, log=lambda m: None)
        ids = [c["coordinator_id"] for c in self.named.values()]
        self.cur.execute("SELECT count(*) FROM coordinators WHERE id = ANY(%s)", (ids,))
        self.assertEqual(self.cur.fetchone()[0], len(ids))
        # A head coach who brought his own coordinator replaced the one already named, on the record.
        self.cur.execute("SELECT count(*) FROM staff_moves WHERE season = %s AND action = 'REPLACED' "
                         "AND coordinator_id = ANY(%s)", (SEASON, ids))
        self.assertGreater(self.cur.fetchone()[0], 0)


@pytest.mark.integration
class SameSeasonTieBreak(unittest.TestCase):
    """A midseason change leaves two tenures for one team covering the same season (the outgoing one ends in the
    season the incoming one starts in). staff_in / head_coaches must pick the newer tenure, by id."""

    SEASON = 2030

    def setUp(self):
        try:
            self.conn = psycopg.connect(DSN, connect_timeout=3)
        except Exception as e:
            raise unittest.SkipTest(f"no database: {e!r}")
        cur = self.conn.cursor()
        if not cc.installed(cur):
            self.conn.close()
            raise unittest.SkipTest("coaching carousel migrations not applied")
        cur.execute("SELECT id FROM teams WHERE abbreviation = 'KC'")
        row = cur.fetchone()
        if not row:
            self.conn.close()
            raise unittest.SkipTest("no KC team")
        self.team = row[0]
        cur.execute("INSERT INTO coaches (coach_name, slant) VALUES ('Tie-break outgoing', 'OFFENSIVE') RETURNING id")
        self.old_coach = cur.fetchone()[0]
        cur.execute("INSERT INTO coaches (coach_name, slant) VALUES ('Tie-break incoming', 'OFFENSIVE') RETURNING id")
        self.new_coach = cur.fetchone()[0]
        cur.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) VALUES (%s, %s, %s, %s)",
                    (self.old_coach, self.team, self.SEASON, self.SEASON))
        cur.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) VALUES (%s, %s, %s, NULL)",
                    (self.new_coach, self.team, self.SEASON))
        cur.execute("""INSERT INTO coordinators (coordinator_name, side, scheme)
                       VALUES ('Tie-break outgoing OC', 'OFFENSIVE', 'AIR RAID') RETURNING id""")
        self.old_oc = cur.fetchone()[0]
        cur.execute("""INSERT INTO coordinators (coordinator_name, side, scheme)
                       VALUES ('Tie-break incoming OC', 'OFFENSIVE', 'SPREAD') RETURNING id""")
        self.new_oc = cur.fetchone()[0]
        cur.execute("""INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season)
                       VALUES (%s, %s, 'OC', %s, %s)""", (self.old_oc, self.team, self.SEASON, self.SEASON))
        cur.execute("""INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season)
                       VALUES (%s, %s, 'OC', %s, NULL)""", (self.new_oc, self.team, self.SEASON))
        self.cur = cur

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def test_head_coach_picks_the_newer_tenure_of_the_same_season(self):
        self.assertEqual(cc.head_coaches(self.cur, self.SEASON)[self.team]["coach_id"], self.new_coach)

    def test_coordinator_picks_the_newer_tenure_of_the_same_season(self):
        self.assertEqual(cc.staff_in(self.cur, self.SEASON)[(self.team, "OC")]["coordinator_id"], self.new_oc)


@pytest.mark.integration
class SameSeasonReturn(unittest.TestCase):
    """Audit F2 required regression: a coach returning within the same season must beat the interim.

    A→B→A where A started the mirrored season: `_start_tenure` reopens A's older, lower-id tenure, so every reader
    must rank an open same-season tenure above a closed one before `id DESC`. The readers checked are
    `staff._current_hc` / `_current_coord`, `coach_save_sync._wanted`, and
    `coaching_carousel.head_coaches` / `staff_in`; a repeated final change must emit no moves. Controls:
    A(start 2024)→B→A and A→B→C.

    Real PostgreSQL in a `mu_scratch_u9_*` database from db/init_db.sql; each transaction is rolled back.
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

    def _coach(self, name, team_id, start, end=None):
        self.cur.execute("INSERT INTO coaches (coach_name) VALUES (%s) RETURNING id", (name,))
        pid = self.cur.fetchone()[0]
        self.cur.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season, end_season) "
                         "VALUES (%s, %s, %s, %s)", (pid, team_id, start, end))
        return pid

    def _coord(self, name, team_id, role, start, end=None):
        side = "OFFENSIVE" if role == "OC" else "DEFENSIVE"
        self.cur.execute("INSERT INTO coordinators (coordinator_name, side, scheme) VALUES (%s, %s, %s) "
                         "RETURNING id", (name, side, "West Coast" if side == "OFFENSIVE" else "4-3"))
        pid = self.cur.fetchone()[0]
        self.cur.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season, end_season) "
                         "VALUES (%s, %s, %s, %s, %s)", (pid, team_id, role, start, end))
        return pid

    def _staff_a(self, team_id, start):
        return (self._coach("First Coach", team_id, start),
                self._coord("First OC", team_id, "OC", start),
                self._coord("First DC", team_id, "DC", start))

    def _apply(self, start_names, incoming_names, week):
        moves = staff.apply_staff_changes(self.cur, 2026, [
            staff.StaffChange(team="BUF", role="HC", out_name=start_names[0], in_name=incoming_names[0], week=week),
            staff.StaffChange(team="BUF", role="OC", out_name=start_names[1], in_name=incoming_names[1], week=week),
            staff.StaffChange(team="BUF", role="DC", out_name=start_names[2], in_name=incoming_names[2], week=week),
        ])
        return moves

    def _assert_reader_agreement(self, team_id, expected):
        """The four readers must all select `expected` (person ids) for 2026."""
        assert staff._current_hc(self.cur, team_id, 2026)["person_id"] == expected[0]
        assert staff._current_coord(self.cur, team_id, "OC", 2026)["person_id"] == expected[1]
        assert staff._current_coord(self.cur, team_id, "DC", 2026)["person_id"] == expected[2]
        wanted = cs._wanted(self.cur, 2026)
        assert wanted[(team_id, "HC")]["person_id"] == expected[0]
        assert wanted[(team_id, "OC")]["person_id"] == expected[1]
        assert wanted[(team_id, "DC")]["person_id"] == expected[2]
        hcs = cc.head_coaches(self.cur, 2026)
        coords = cc.staff_in(self.cur, 2026)
        assert hcs[team_id]["coach_id"] == expected[0]
        assert coords[(team_id, "OC")]["coordinator_id"] == expected[1]
        assert coords[(team_id, "DC")]["coordinator_id"] == expected[2]

    # -- tests --
    def test_a_to_b_to_a_where_a_started_the_season_keeps_a_in_every_reader(self):
        tid = self._team()
        a = self._staff_a(tid, 2026)
        self._apply(("First Coach", "First OC", "First DC"),
                    ("Second Coach", "Second OC", "Second DC"), week=6)
        self._apply(("Second Coach", "Second OC", "Second DC"),
                    ("First Coach", "First OC", "First DC"), week=8)

        self._assert_reader_agreement(tid, a)
        # The return reopened A's lower-id tenure: repeating the final change must be a no-op.
        assert self._apply(("Second Coach", "Second OC", "Second DC"),
                           ("First Coach", "First OC", "First DC"), week=8) == []

    def test_a_starting_2024_then_b_then_a_keeps_a(self):
        tid = self._team()
        a = self._staff_a(tid, 2024)
        self._apply(("First Coach", "First OC", "First DC"),
                    ("Second Coach", "Second OC", "Second DC"), week=6)
        self._apply(("Second Coach", "Second OC", "Second DC"),
                    ("First Coach", "First OC", "First DC"), week=8)

        self._assert_reader_agreement(tid, a)
        assert self._apply(("Second Coach", "Second OC", "Second DC"),
                           ("First Coach", "First OC", "First DC"), week=8) == []

    def test_a_then_b_then_c_keeps_c(self):
        tid = self._team()
        self._staff_a(tid, 2026)
        self._apply(("First Coach", "First OC", "First DC"),
                    ("Second Coach", "Second OC", "Second DC"), week=6)
        self._apply(("Second Coach", "Second OC", "Second DC"),
                    ("Third Coach", "Third OC", "Third DC"), week=9)

        self.cur.execute("SELECT id FROM coaches WHERE coach_name = 'Third Coach'")
        third_hc = self.cur.fetchone()[0]
        self.cur.execute("SELECT id FROM coordinators WHERE coordinator_name = 'Third OC'")
        third_oc = self.cur.fetchone()[0]
        self.cur.execute("SELECT id FROM coordinators WHERE coordinator_name = 'Third DC'")
        third_dc = self.cur.fetchone()[0]
        self._assert_reader_agreement(tid, (third_hc, third_oc, third_dc))
        assert self._apply(("Second Coach", "Second OC", "Second DC"),
                           ("Third Coach", "Third OC", "Third DC"), week=9) == []


if __name__ == "__main__":
    unittest.main()
