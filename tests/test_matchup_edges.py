"""Unit tests for Matchup Edge and Trench Edge. No database required.

Run: python3 -m unittest tests.test_matchup_edges
"""
import unittest

from engines.matchup_edges import compute_edges, unit_average, z_scores


def profile(pass_yds, rush_yds, giveaways):
    return {"pass_yds_per_game": pass_yds, "rush_yds_per_game": rush_yds, "giveaways_per_game": giveaways}


PROFILES = {1: profile(300, 150, 0.5), 2: profile(200, 100, 2.0), 3: profile(250, 125, 1.25)}
RATINGS = {1: {"coverage_rank": 1, "def_ovr": 90, "pass_rush_rank": 1},
           2: {"coverage_rank": 32, "def_ovr": 70, "pass_rush_rank": 32},
           3: {"coverage_rank": 16, "def_ovr": 80, "pass_rush_rank": 16}}
UNITS = {1: {"ol": 85.0, "pass_rush": 88.0}, 2: {"ol": 75.0, "pass_rush": 72.0}}


class MatchupEdgeTests(unittest.TestCase):
    def test_z_scores(self):
        z = z_scores({1: 10, 2: 20, 3: 30})
        self.assertAlmostEqual(z[2], 0.0)
        self.assertAlmostEqual(z[1], -z[3])
        self.assertEqual(z_scores({1: 5, 2: 5}), {1: 0.0, 2: 0.0})

    def test_strong_offense_vs_weak_defense(self):
        e = compute_edges(PROFILES, RATINGS, UNITS, off_team=1, def_team=2)
        self.assertGreater(e["edges"]["pass"], 0)
        self.assertGreater(e["edges"]["rush"], 0)
        self.assertGreater(e["edges"]["ball_security"], 0)    # team 2 gives it away more
        self.assertAlmostEqual(e["trench_edge"], 85.0 - 72.0)
        self.assertIsNone(e["biggest_vulnerability"])

    def test_reverse_direction_is_disadvantaged(self):
        e = compute_edges(PROFILES, RATINGS, UNITS, off_team=2, def_team=1)
        self.assertLess(e["edges"]["pass"], 0)
        self.assertIsNone(e["best_edge"])
        self.assertAlmostEqual(e["trench_edge"], 75.0 - 88.0)

    def test_missing_units(self):
        self.assertIsNone(compute_edges(PROFILES, RATINGS, UNITS, off_team=3, def_team=1)["trench_edge"])
        self.assertEqual(unit_average([80, 90, 70], 2), 85.0)
        self.assertIsNone(unit_average([], 4))


if __name__ == "__main__":
    unittest.main()
