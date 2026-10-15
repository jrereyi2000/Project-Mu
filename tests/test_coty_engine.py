"""Unit tests for the Coach of the Year race. No database required.

Run: python3 -m unittest tests.test_coty_engine
"""
import itertools
import unittest

from engines.coty_engine import (FIRST_YEAR_BONUS, FULL_SEASON_GAMES, WEIGHTS, average_ranks, compute_coty,
                                 full_season_win_pcts, win_pcts, zscores)


def game(gid, week, home, away, hs, as_, game_type="REGULAR_SEASON"):
    return {"id": gid, "week": week, "home_team_id": home, "away_team_id": away,
            "home_score": hs, "away_score": as_, "game_type": game_type}


def round_robin(strength: dict, rounds: int = 2) -> list:
    """Every pair plays `rounds` times; the stronger team wins by the strength gap (ties go to the home team)."""
    games, gid = [], 0
    for r in range(rounds):
        for a, b in itertools.combinations(sorted(strength), 2):
            home, away = (a, b) if r % 2 == 0 else (b, a)
            gap = strength[home] - strength[away]
            gid += 1
            games.append(game(gid, gid, home, away, 20 + max(gap, 1), 20 + max(-gap, 0)))
    return games


def coaches(teams, start=2026):
    return {t: {"coach_id": 100 + t, "coach_name": f"Coach {t}", "start_season": start} for t in teams}


class RankAndZTests(unittest.TestCase):
    def test_average_ranks_share_ties(self):
        self.assertEqual(average_ranks({"a": 90, "b": 85, "c": 85, "d": 80}), {"a": 1, "b": 2.5, "c": 2.5, "d": 4})

    def test_zscores_skip_missing_and_flat(self):
        z = zscores({"a": 1.0, "b": 3.0, "c": None})
        self.assertEqual(set(z), {"a", "b"})
        self.assertAlmostEqual(z["a"], -1.0)
        self.assertEqual(zscores({"a": 2.0, "b": 2.0}), {})
        self.assertEqual(zscores({"a": 2.0}), {})

    def test_win_pcts_regular_season_only_ties_half(self):
        pct = win_pcts([game(1, 1, 1, 2, 20, 20), game(2, 2, 1, 2, 30, 10),
                        game(3, 23, 2, 1, 40, 0, game_type="SUPER_BOWL")])
        self.assertEqual(pct, {1: 0.75, 2: 0.25})


    def test_previous_season_counts_only_when_full(self):
        # Team 1 plays 17 regular-season games (and a Super Bowl), team 3 plays 14, team 2 only 3.
        games = [game(i, i, 1, 3, 24, 10) for i in range(1, 15)]
        games += [game(100 + i, 14 + i, 1, 2, 10, 24) for i in range(1, 4)]
        games.append(game(200, 23, 1, 3, 30, 0, game_type="SUPER_BOWL"))
        self.assertEqual(FULL_SEASON_GAMES, 17)
        full = full_season_win_pcts(games)
        self.assertEqual(set(full), {1})
        self.assertAlmostEqual(full[1], 14 / 17)
        self.assertEqual(set(full_season_win_pcts(games, min_games=14)), {1, 3})

class CotyScoreTests(unittest.TestCase):
    def setUp(self):
        self.strength = {1: 10, 2: 7, 3: 4, 4: 1}
        self.games = round_robin(self.strength)

    def test_score_is_centred_and_ranked(self):
        race = compute_coty(2035, self.games, coaches(self.strength), {1: 90, 2: 85, 3: 80, 4: 75}, {})
        self.assertEqual([r["team_id"] for r in race], [1, 2, 3, 4])
        self.assertEqual([r["coty_rank"] for r in race], [1, 2, 3, 4])
        self.assertAlmostEqual(sum(r["coty_score"] for r in race) / 4, 50.0)

    def test_outperforming_the_roster_wins_the_race(self):
        # Team 3 wins exactly as often as team 2 would have, but with the league's worst roster.
        ovr = {1: 90, 2: 85, 3: 70, 4: 80}
        race = {r["team_id"]: r for r in compute_coty(2035, self.games, coaches(self.strength), ovr, {})}
        self.assertEqual(race[3]["ovr_rank"], 4)
        self.assertEqual(race[3]["win_rank"], 3)
        self.assertEqual(race[3]["roster_outperformance"], 1)
        self.assertLess(race[1]["roster_outperformance"], 1)  # 1st in both: 0
        flat = {r["team_id"]: r for r in compute_coty(2035, self.games, coaches(self.strength),
                                                        {1: 90, 2: 85, 3: 80, 4: 75}, {})}
        self.assertGreater(race[3]["coty_score"], flat[3]["coty_score"])

    def test_improvement_counts_when_last_season_is_recorded(self):
        ovr = {1: 90, 2: 85, 3: 80, 4: 75}
        prev = {1: 1.0, 2: 0.9, 3: 0.1, 4: 0.5}  # team 3 jumped, team 1 stood still
        race = {r["team_id"]: r for r in compute_coty(2035, self.games, coaches(self.strength), ovr, prev)}
        base = {r["team_id"]: r for r in compute_coty(2035, self.games, coaches(self.strength), ovr, {})}
        self.assertIn("win_pct_change", race[3]["inputs_used"])
        self.assertAlmostEqual(race[3]["win_pct_change"], race[3]["win_pct"] - 0.1)
        self.assertGreater(race[3]["coty_score"], base[3]["coty_score"])
        self.assertLess(race[1]["coty_score"], base[1]["coty_score"])

    def test_missing_input_is_reweighted_not_zeroed(self):
        # Only teams 1 and 2 have a previous season; 3 and 4 are scored on the other inputs alone.
        ovr = {1: 90, 2: 85, 3: 80, 4: 75}
        race = {r["team_id"]: r for r in compute_coty(2035, self.games, coaches(self.strength), ovr, {1: 0.2, 2: 0.9})}
        base = {r["team_id"]: r for r in compute_coty(2035, self.games, coaches(self.strength), ovr, {})}
        self.assertNotIn("win_pct_change", race[3]["inputs_used"])
        self.assertIsNone(race[3]["win_pct_change"])
        self.assertAlmostEqual(race[3]["coty_score"], base[3]["coty_score"])
        self.assertAlmostEqual(race[4]["coty_score"], base[4]["coty_score"])

    def test_team_without_ovr_skips_roster_input(self):
        race = {r["team_id"]: r for r in compute_coty(2035, self.games, coaches(self.strength), {1: 90, 2: 85, 3: 80}, {})}
        self.assertIsNone(race[4]["roster_outperformance"])
        self.assertEqual(race[4]["inputs_used"], ["win_pct", "wins_above_expected"])

    def test_first_year_bonus(self):
        c = coaches(self.strength)
        c[4]["start_season"] = 2035
        ovr = {1: 90, 2: 85, 3: 80, 4: 75}
        race = {r["team_id"]: r for r in compute_coty(2035, self.games, c, ovr, {})}
        base = {r["team_id"]: r for r in compute_coty(2035, self.games, coaches(self.strength), ovr, {})}
        self.assertTrue(race[4]["first_year_with_team"])
        self.assertAlmostEqual(race[4]["coty_score"] - base[4]["coty_score"], FIRST_YEAR_BONUS)

    def test_playoffs_ignored_and_coachless_teams_skipped(self):
        extra = self.games + [game(999, 23, 4, 1, 50, 0, game_type="SUPER_BOWL")]
        a = compute_coty(2035, extra, coaches([1, 2, 3]), {1: 90, 2: 85, 3: 80, 4: 75}, {})
        b = compute_coty(2035, self.games, coaches([1, 2, 3]), {1: 90, 2: 85, 3: 80, 4: 75}, {})
        self.assertEqual([r["team_id"] for r in a], [1, 2, 3])
        self.assertEqual([round(r["coty_score"], 6) for r in a], [round(r["coty_score"], 6) for r in b])
        self.assertEqual(compute_coty(2035, [], coaches([1]), {}, {}), [])

    def test_weights_sum_to_one(self):
        self.assertAlmostEqual(sum(WEIGHTS.values()), 1.0)


if __name__ == "__main__":
    unittest.main()
