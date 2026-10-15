"""Unit tests for Awards Equity, Exposure, defensive Performance and Playoff Clout. No database required.

Run: python3 -m unittest tests.test_player_and_playoff_metrics
"""
import math
import unittest

from engines.player_popularity_engine import (DEF_SCALE, EXPOSURE_DEFAULT, awards_equity, defensive_performance,
                                              exposure, performance)
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


class DefensivePerformanceTests(unittest.TestCase):
    # Reshard Gregory, 2035: 15 games, 63 tackles, 16 TFL, 14.5 sacks, 1 PD, 1 FF, 2 FR
    EDGE = {"games_played": 15, "def_tackles": 63, "def_tfl": 16, "def_sacks": 14.5, "def_ints": 0,
            "def_deflections": 1, "def_forced_fumbles": 1, "def_fumble_recoveries": 2, "def_tds": 0, "def_safeties": 0}

    def test_elite_edge_rusher_lands_with_elite_skill_players(self):
        production, spikes = defensive_performance(self.EDGE, [])
        self.assertAlmostEqual(production, DEF_SCALE * (0.25 * 63 + 16 + 4 * 14.5 + 1.5 + 3 + 4) / 15)
        self.assertGreater(production, 74)
        self.assertGreater(spikes, 4)       # about as many big games as a top-10 skill player (5)

    def test_game_lines_count_and_the_rest_is_estimated(self):
        two_sacks = {"def_sacks": 2, "def_tackles": 4}
        quiet = {"def_tackles": 3}
        _, with_spike = defensive_performance(self.EDGE, [two_sacks])
        _, without = defensive_performance(self.EDGE, [quiet])
        self.assertGreater(with_spike, without)
        # every game captured: spikes are exact counts
        lines = [two_sacks] + [quiet] * 14
        self.assertEqual(defensive_performance(self.EDGE, lines)[1], 1)

    def test_defensive_td_is_a_spike(self):
        line = {"games_played": 1, "def_tackles": 2, "def_ints": 1, "def_tds": 1}
        self.assertEqual(defensive_performance(line, [dict(line)])[1], 1)
        self.assertGreaterEqual(defensive_performance(line, [])[1], 1)   # estimated: the TD plus the chance of 2+ splash plays

    def test_performance_formula(self):
        self.assertEqual(performance(80.0, 2, 0.6), (0.6 * 80 + 0.4 * 50, 50.0))
        self.assertAlmostEqual(performance(80.0, 2, 0.3)[0], (0.6 * 80 + 0.4 * 50) * 0.85)
        self.assertEqual(performance(50.0, 9, 0.6)[1], 100.0)


if __name__ == "__main__":
    unittest.main()
