"""Unit tests for shared market constants and Super Bowl hardware bumps. No database required.

Run: python3 -m unittest tests.test_market_constants
"""
import unittest

from engines.market_constants import SB_RUNNER_UP_BUMP, SB_WIN_BUMP, market, recent_hardware_bumps


class FakeCursor:
    """Answers super_bowl_results() with (season, winner, loser) rows filtered by the season bound."""
    def __init__(self, results):
        self.results, self.bound = results, None

    def execute(self, _sql, params):
        self.bound = params[0]

    def fetchall(self):
        return [r for r in self.results if r[0] <= self.bound]


class HardwareBumpTests(unittest.TestCase):
    def test_trailing_five_season_window(self):
        cur = FakeCursor([(2029, 1, 2), (2030, 3, 4), (2033, 3, 5), (2034, 6, 4), (2035, 7, 8)])
        bumps = recent_hardware_bumps(cur, 2035)
        self.assertNotIn(1, bumps)                        # 2029 is outside 2030–2034
        self.assertEqual(bumps[3], 2 * SB_WIN_BUMP)
        self.assertEqual(bumps[4], 2 * SB_RUNNER_UP_BUMP)
        self.assertNotIn(7, bumps)                        # the current season's SB doesn't count yet

    def test_market_lookup(self):
        self.assertEqual(market("Cowboys")["N"], 100)
        self.assertIsNone(market("Ravens")["N"])
        self.assertEqual(market("Unknown")["B"], 50.0)


if __name__ == "__main__":
    unittest.main()
