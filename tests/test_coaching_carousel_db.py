"""The coaching carousel against the live database, rehearsing the 2035 offseason inside one transaction that is
always rolled back (nothing is ever committed). Skipped when the database or the carousel migrations are missing.

Run: python3 -m unittest tests.test_coaching_carousel_db
"""
import unittest

try:
    import psycopg
    from pipeline.load_capture import DSN
except Exception:          # pragma: no cover
    psycopg = None

import pytest
from engines import coach_hot_seat as chs
from engines import coaching_carousel as cc
from engines import fire_coordinator as fc

SEASON = 2035


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


if __name__ == "__main__":
    unittest.main()
