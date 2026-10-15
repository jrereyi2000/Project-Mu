"""Observed offensive tendencies (engines/tendency_engine.py). No database required.

Run: python3 -m unittest tests.test_tendency_engine
"""
import unittest

from engines.tendency_engine import MIN_GAMES, compute_observed, ols_residuals, tiers


def team(games=16, pd_pg=0.0, pass_att=560, rush_att=420, fourth_att=24):
    return {"games": games, "pd_pg": pd_pg, "pass_att": pass_att, "rush_att": rush_att, "fourth_att": fourth_att}


class TendencyEngineTests(unittest.TestCase):
    def test_residuals_remove_the_line(self):
        self.assertEqual([round(r, 9) for r in ols_residuals([1, 2, 3], [2, 4, 6])], [0, 0, 0])
        self.assertEqual(ols_residuals([5, 5], [1, 3]), [-1.0, 1.0])   # no spread in x: just the mean

    def test_tiers_split_like_the_real_world_tiers(self):
        t = tiers({i: i for i in range(32)})
        self.assertEqual([list(t.values()).count(x) for x in ("High", "Medium", "Low")], [11, 10, 11])
        self.assertEqual(t[31], "High")
        self.assertEqual(t[0], "Low")

    def test_passing_because_of_the_score_is_not_a_lean(self):
        # Pass share rises exactly with the deficit, except team 9, which passes more than its score explains.
        rows = {i: team(pd_pg=-i, pass_att=500 + 10 * i, rush_att=500 - 10 * i) for i in range(9)}
        rows[9] = team(pd_pg=-4, pass_att=700, rush_att=300)   # at the middle of the score range: no pull on the line
        out = compute_observed(rows)
        self.assertEqual(out[9]["observed_pass_lean"], "High")
        self.assertGreater(out[9]["pass_rate_oe"], 0.1)
        spread = [out[i]["pass_rate_oe"] for i in range(9)]
        self.assertLess(max(spread) - min(spread), 1e-9)   # the rest lean the same once the score is allowed for

    def test_fourth_down_per_game(self):
        rows = {i: team(fourth_att=16) for i in range(5)}
        rows[5] = team(fourth_att=40)
        rows[6] = team(games=8, fourth_att=20)   # 2.5 a game: more than the field's 1.0
        out = compute_observed(rows)
        self.assertEqual(out[5]["observed_fourth_down"], "High")
        self.assertGreater(out[6]["fourth_down_oe"], 0)

    def test_early_season_gets_values_but_no_tier(self):
        rows = {i: team(games=MIN_GAMES - 1) for i in range(6)}
        out = compute_observed(rows)
        self.assertTrue(all(o["observed_pass_lean"] is None and o["pass_rate_oe"] is not None for o in out.values()))

    def test_missing_inputs(self):
        rows = {i: team() for i in range(4)}
        rows[4] = team(pass_att=None, fourth_att=None)
        rows[5] = team(pd_pg=None)
        out = compute_observed(rows)
        for t in (4, 5):
            self.assertIsNone(out[t]["observed_pass_lean"])
        self.assertIsNone(out[4]["fourth_down_oe"])
        self.assertIsNone(compute_observed({1: team(), 2: team()})[1]["observed_pass_lean"])   # too few teams


if __name__ == "__main__":
    unittest.main()
