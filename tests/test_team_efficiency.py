"""Unit tests for the team efficiency profile. No database required.

Run: python3 -m unittest tests.test_team_efficiency
"""
import unittest

from engines.team_efficiency_engine import compute_team_efficiency, pythagorean_wins, team_pace


def row(team_id, pf, pa, yds=350, pass_yds=250, rush_yds=100, giveaways=1):
    return {"team_id": team_id, "pf": pf, "pa": pa, "won": 1.0 if pf > pa else (0.5 if pf == pa else 0.0),
            "yds": yds, "pass_yds": pass_yds, "rush_yds": rush_yds, "giveaways": giveaways}


class TeamEfficiencyTests(unittest.TestCase):
    def test_pythagorean(self):
        self.assertAlmostEqual(pythagorean_wins(300, 300, 16), 8.0)
        self.assertGreater(pythagorean_wins(400, 300, 16), 8.0)
        self.assertEqual(pythagorean_wins(0, 0, 4), 2.0)

    def test_pace_benchmark_and_cap(self):
        self.assertAlmostEqual(team_pace(35, 425), 100.0)
        self.assertEqual(team_pace(70, 850), 100.0)

    def test_luck_from_close_wins(self):
        # Two one-point wins and a 30-point loss: 2-1 record, points say well under 2 wins.
        p = compute_team_efficiency([row(1, 21, 20), row(1, 21, 20), row(1, 10, 40)])[1]
        self.assertEqual(p["wins"], 2.0)
        self.assertGreater(p["pyth_luck"], 1.0)
        self.assertAlmostEqual(p["giveaways_per_game"], 1.0)


if __name__ == "__main__":
    unittest.main()
