"""engines/season_ledger.py: opening teams, the transition statuses (plan D1: a departure with no retirement on record
is a free agent), the free-agent pool and career paths. The pure tests need no database; the integration tests run on
madden_test (tests/conftest.py) inside a transaction that is rolled back."""
import unittest

import psycopg
import pytest

from engines import season_ledger as sl

CAR, ATL, NYJ = 11, 25, 18


def move(t, week, frm=None, to=None, flags=None):
    return {"type": t, "week": week, "from_team_id": frm, "to_team_id": to, "flags": flags}


class OpeningTeamTest(unittest.TestCase):
    def test_no_moves_keeps_current_team(self):
        self.assertEqual(sl.opening_team(CAR, []), CAR)

    def test_released_or_traded_started_with_the_team_he_left(self):
        self.assertEqual(sl.opening_team(CAR, [move("RELEASED", 6, frm=ATL)]), ATL)
        self.assertEqual(sl.opening_team(NYJ, [move("TRADED", 5, frm=CAR, to=NYJ)]), CAR)

    def test_signed_during_the_season_started_as_a_free_agent(self):
        self.assertIsNone(sl.opening_team(CAR, [move("SIGNED", 10, to=CAR)]))
        # signed off his own practice squad: he was with the team
        self.assertEqual(sl.opening_team(CAR, [move("SIGNED", 8, to=CAR, flags="practice_squad")]), CAR)

    def test_re_signing_is_his_team_and_offseason_rows_are_ignored(self):
        self.assertEqual(sl.opening_team(CAR, [move("RE_SIGNED", 9, to=CAR)]), CAR)
        self.assertEqual(sl.opening_team(CAR, [move("SIGNED", None, to=CAR)]), CAR)


class ClassifyTest(unittest.TestCase):
    def test_retained_and_moved(self):
        self.assertEqual(sl.classify({"team_id": CAR}, {"team_id": CAR})[0], "RETAINED")
        status, source, frm, to, _ = sl.classify({"team_id": CAR}, {"team_id": ATL}, offseason=("TRADED",))
        self.assertEqual((status, source, frm, to), ("MOVED", "TRADE", CAR, ATL))
        self.assertEqual(sl.classify({"team_id": CAR}, {"team_id": ATL}, offseason=("SIGNED",))[1], "TRANSACTION")
        self.assertEqual(sl.classify({"team_id": CAR}, {"team_id": ATL})[1], "ROSTER_SCAN")

    def test_departure_is_a_free_agent_unless_retired(self):
        status, _, frm, to, flags = sl.classify({"team_id": CAR}, None)
        self.assertEqual((status, frm, to), ("FREE_AGENT", CAR, None))
        self.assertIn("retirement_scan_missing", flags)                     # D1, no scan for that season
        self.assertNotIn("retirement_scan_missing", sl.classify({"team_id": CAR}, None, scan_ran=True)[4])
        self.assertEqual(sl.classify({"team_id": CAR}, None, retired=True)[0], "RETIRED")

    def test_signed_mid_season_was_a_free_agent_at_the_start(self):
        status, _, _, to, flags = sl.classify({"team_id": CAR}, {"team_id": None, "signed_week": 10})
        self.assertEqual((status, to), ("FREE_AGENT", None))
        self.assertIn("signed_in_season:week 10", flags)

    def test_released_in_season_is_flagged(self):
        self.assertIn("released_in_season", sl.classify({"team_id": CAR, "released": True}, None)[4])

    def test_arrivals(self):
        self.assertEqual(sl.classify(None, {"team_id": CAR, "exp": 0}, drafted=True)[:2], ("DRAFTED", "DRAFT"))
        status, source, _, _, flags = sl.classify(None, {"team_id": CAR, "exp": 0})
        self.assertEqual((status, source), ("NEW", "ROSTER_SCAN"))
        self.assertIn("undrafted_rookie", flags)
        self.assertIn("returned", sl.classify(None, {"team_id": CAR, "exp": 6}, returned=True)[4])
        self.assertIn("no_prior_capture", sl.classify(None, {"team_id": CAR, "exp": 2})[4])
        status, source, _, to, flags = sl.classify(None, {"team_id": None, "signed_week": 7, "first_team": ATL, "exp": 3})
        self.assertEqual((status, source, to), ("NEW", "TRANSACTION", ATL))


@pytest.mark.integration
class LedgerIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.conn = psycopg.connect(sl.CONN_INFO)
        self.cur = self.conn.cursor()
        if not sl.installed(self.cur):
            self.skipTest("ledger migration not in this database")

    def tearDown(self):
        self.conn.rollback()
        self.conn.close()

    def test_2036_boundary_accounts_for_everyone(self):
        rep = sl.build(self.cur, 2036)
        self.assertEqual(rep["status"], "built")
        self.cur.execute("SELECT count(DISTINCT player_id) FROM player_seasons WHERE season IN (2035, 2036) "
                         "AND team_id IS NOT NULL AND COALESCE(source, 'CAPTURE') <> 'SYNTH'")
        self.assertEqual(sum(rep["counts"].values()), self.cur.fetchone()[0])
        # D1: no retirement scan exists for 2035, so every departure is a free agent, flagged
        self.assertNotIn("RETIRED", rep["counts"])
        self.assertEqual(rep["flags"]["retirement_scan_missing"], rep["counts"]["FREE_AGENT"])
        again = sl.build(self.cur, 2036)
        self.assertEqual(again["counts"], rep["counts"])                       # idempotent

    def test_history_seasons_are_not_a_boundary(self):
        self.assertEqual(sl.build(self.cur, 2035)["status"], "not_ready")      # 2034 is generated history (D10)

    def test_free_agent_pool_has_no_history_players(self):
        pool = sl.free_agent_pool(self.cur, 2036)
        ids = [p["player_id"] for p in pool]
        if ids:
            self.cur.execute("SELECT count(*) FROM players WHERE id = ANY(%s) AND origin IS NOT NULL", (ids,))
            self.assertEqual(self.cur.fetchone()[0], 0)

    def test_career_path_of_a_history_player_ends_retired(self):
        self.cur.execute("SELECT id FROM players WHERE origin = 'HISTORY' LIMIT 1")
        row = self.cur.fetchone()
        if not row:
            self.skipTest("no history players here")
        path = sl.career_path(self.cur, row[0])
        self.assertEqual(path["now"]["status"], "RETIRED")
        self.assertEqual(path["now"]["basis"], "history")
        self.assertTrue(all(s["history"] for s in path["seasons"]))


if __name__ == "__main__":
    unittest.main()
