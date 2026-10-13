"""Unit tests for Awards Equity, Exposure and Playoff Clout. No database required.

Run: python3 -m unittest tests.test_player_and_playoff_metrics
"""
import math
import unittest

from engines.player_popularity_engine import EXPOSURE_DEFAULT, awards_equity, exposure
from engines.playoff_clout_engine import player_playoff_clout, team_playoff_clout


class AwardsEquityTests(unittest.TestCase):
    def test_decay_and_career(self):
        # MVP + OPOY in 2032, evaluated for 2035: e^{-0.7}·85 + 0.25·85
        expected = math.exp(-0.7) * 85 + 0.25 * 85
        self.assertAlmostEqual(awards_equity([(2032, "MVP"), (2032, "OPOY")], 2035), expected)

    def test_old_awards_only_count_through_career(self):
        self.assertAlmostEqual(awards_equity([(2026, "MVP")], 2035), 12.5)

    def test_current_season_ignored_and_capped(self):
        self.assertEqual(awards_equity([(2035, "MVP")], 2035), 0.0)
        many = [(y, a) for y in (2032, 2033, 2034) for a in ("MVP", "OPOY", "SBMVP")]
        self.assertEqual(awards_equity(many, 2035), 100.0)

    def test_coach_award_scores_nothing(self):
        self.assertEqual(awards_equity([(2034, "COTY")], 2035), 0.0)


class ExposureTests(unittest.TestCase):
    def test_default_without_games(self):
        self.assertEqual(exposure([], 10), EXPOSURE_DEFAULT)

    def test_gotw_bonus_and_recency(self):
        self.assertAlmostEqual(exposure([(10, 60.0, 0)], 10), 60.0)
        self.assertAlmostEqual(exposure([(10, 60.0, 1)], 10), 75.0)
        # The recent game dominates
        self.assertGreater(exposure([(1, 40.0, 0), (10, 80.0, 0)], 10), 60.0)


class PlayoffCloutTests(unittest.TestCase):
    def test_team_decay(self):
        clout = team_playoff_clout([(2034, 1, "sb_win"), (2034, 2, "sb_loss"), (2030, 3, "sb_loss")], 2035)
        self.assertAlmostEqual(clout[1], 100 * math.exp(-0.15))
        self.assertAlmostEqual(clout[2], 50 * math.exp(-0.15))
        self.assertAlmostEqual(clout[3], 50 * math.exp(-0.75))

    def test_team_cap_and_future_ignored(self):
        clout = team_playoff_clout([(2033, 1, "sb_win"), (2034, 1, "sb_win"), (2036, 2, "sb_win")], 2035)
        self.assertEqual(clout[1], 100.0)
        self.assertNotIn(2, clout)

    def test_player(self):
        self.assertEqual(player_playoff_clout({"sbmvp": 1}), 40.0)
        self.assertEqual(player_playoff_clout({"sbmvp": 1, "sb_win": 1, "playoff_win": 2}), 81.0)
        self.assertEqual(player_playoff_clout({}), 0.0)


if __name__ == "__main__":
    unittest.main()
