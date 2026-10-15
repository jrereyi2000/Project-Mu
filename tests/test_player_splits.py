"""Player splits by defense (engines/player_splits.py). No database required.

Run: python3 -m unittest tests.test_player_splits
"""
import unittest

from engines.player_splits import BLEND_GAMES, MIN_GAMES, STATS, compute_splits, rank_groups, stat_group


def line(season, opp, rec_yards, receptions=5, rec_tds=0):
    return {"season": season, "opponent_id": opp, "receptions": receptions, "rec_yards": rec_yards,
            "rec_tds": rec_tds, "fantasy_points": receptions + rec_yards / 10 + 6 * rec_tds}


STRONG, WEAK = {"scoring_defense": "Strong", "pass_rush": "Strong", "coverage": "Strong"}, \
               {"scoring_defense": "Weak", "pass_rush": "Weak", "coverage": "Weak"}


class PlayerSplitsTests(unittest.TestCase):
    def test_groups_split_like_the_tiers(self):
        g = rank_groups({t: t for t in range(1, 33)})          # ranks: 1 = best
        self.assertEqual([list(g.values()).count(x) for x in ("Strong", "Average", "Weak")], [11, 10, 11])
        self.assertEqual((g[1], g[32]), ("Strong", "Weak"))
        self.assertEqual(rank_groups({"a": 14.0, "b": 30.0, "c": 21.0}, best_low=True)["a"], "Strong")

    def test_stat_groups(self):
        self.assertEqual([stat_group(p) for p in ("QB", "HB", "FB", "WR", "TE", "CB", None)],
                         ["QB", "RB", "RB", "REC", "REC", None, None])

    def test_split_against_strong_and_weak_defenses(self):
        opp = {(2035, 1): STRONG, (2035, 2): WEAK}
        lines = [line(2035, 1, 40) for _ in range(4)] + [line(2035, 2, 120) for _ in range(4)]
        out = compute_splits(lines, opp, STATS["REC"])
        self.assertEqual(out["games"], 8)
        self.assertEqual(out["overall_per_game"]["rec_yards"], 80.0)
        strong = out["dimensions"]["pass_rush"]["Strong"]
        # 4 games at 40 blended with 3 pseudo-games at the 80 average: (160 + 240) / 7
        self.assertAlmostEqual(strong["per_game"]["rec_yards"], round((4 * 40 + BLEND_GAMES * 80) / 7, 1))
        self.assertTrue(strong["vs_his_average"]["rec_yards"].startswith("-"))
        self.assertTrue(out["dimensions"]["coverage"]["Weak"]["vs_his_average"]["rec_yards"].startswith("+"))
        self.assertIn("note", out["dimensions"]["coverage"]["Average"])   # no games there

    def test_too_few_games_and_unknown_opponents(self):
        opp = {(2035, 1): STRONG}
        lines = [line(2035, 1, 50) for _ in range(MIN_GAMES - 1)] + [line(2035, 9, 90)]   # team 9 ungrouped
        out = compute_splits(lines, opp, STATS["REC"])
        self.assertEqual(out["dimensions"]["pass_rush"]["Strong"], {"games": MIN_GAMES - 1,
                                                                    "note": f"fewer than {MIN_GAMES} real games"})
        self.assertEqual(compute_splits([], opp, STATS["REC"])["games"], 0)

    def test_quarterback_rates(self):
        qb = [{"season": 2035, "opponent_id": 1, "completions": 20, "pass_attempts": 30, "pass_yards": 240,
               "pass_tds": 2, "interceptions": 0, "rush_yards": 10, "fantasy_points": 18.6}] * 3
        out = compute_splits(qb, {(2035, 1): STRONG}, STATS["QB"])
        self.assertAlmostEqual(out["overall_per_game"]["comp_pct"], 66.7)
        self.assertEqual(out["overall_per_game"]["yards_per_attempt"], 8.0)
        vs = out["dimensions"]["scoring_defense"]["Strong"]["vs_his_average"]
        self.assertNotIn("completions", vs)                     # counts behind the rates aren't compared
        self.assertEqual(vs["pass_yards"], "+0%")


if __name__ == "__main__":
    unittest.main()
