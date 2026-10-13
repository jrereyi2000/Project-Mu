"""Unit tests for Opponent-Adjusted Game Credit. No database required.

Run: python3 -m unittest tests.test_game_credit
"""
import unittest

from engines.game_credit_engine import MARGIN_CAP, compute_game_credit, fit_home_field


def game(gid, week, home, away, hs, as_, game_type="REGULAR_SEASON"):
    return {"id": gid, "week": week, "home_team_id": home, "away_team_id": away,
            "home_score": hs, "away_score": as_, "game_type": game_type}


def round_robin(results):
    """results: {(home, away): (home_score, away_score)} played one per week."""
    return [game(i, i + 1, h, a, hs, as_) for i, ((h, a), (hs, as_)) in enumerate(results.items())]


class GameCreditTests(unittest.TestCase):
    def test_ratings_centred_and_ordered(self):
        games = round_robin({(1, 2): (30, 10), (3, 4): (20, 17), (1, 3): (24, 21), (2, 4): (14, 13),
                             (4, 1): (10, 27), (2, 3): (17, 20), (2, 1): (7, 28), (4, 3): (21, 24)})
        credits, _, _ = compute_game_credit(games)
        self.assertAlmostEqual(sum(c.rating for c in credits.values()), 0.0, places=6)
        self.assertEqual(max(credits.values(), key=lambda c: c.rating).team_id, 1)

    def test_schedule_strength_separates_identical_records(self):
        # Teams 2 and 3 each beat only the weak team 4 by 7, but team 3 also played elite team 1 closely.
        games = [
            game(1, 1, 1, 4, 35, 0), game(2, 2, 1, 4, 35, 0), game(3, 3, 1, 4, 35, 0),
            game(4, 4, 2, 4, 14, 7), game(5, 5, 3, 4, 14, 7),
            game(6, 6, 2, 4, 10, 17), game(7, 7, 3, 1, 17, 20),
        ]
        credits, _, _ = compute_game_credit(games)
        self.assertGreater(credits[3].sos, credits[2].sos)
        self.assertGreater(credits[3].rating, credits[2].rating)

    def test_margin_cap(self):
        credits_a, _, _ = compute_game_credit([game(1, 1, 1, 2, 60, 0)])
        credits_b, _, _ = compute_game_credit([game(1, 1, 1, 2, 10 + MARGIN_CAP, 10)])
        self.assertAlmostEqual(credits_a[1].rating, credits_b[1].rating)

    def test_home_field_and_neutral(self):
        games = [game(i, i, 1 + (i % 2), 2 - (i % 2), 23, 20) for i in range(1, 9)]
        self.assertAlmostEqual(fit_home_field(games), 3.0)
        self.assertEqual(fit_home_field([game(1, 1, 1, 2, 30, 0, "SUPER_BOWL")]), 0.0)
        # Home teams always win by exactly HFA, so neither team is better than average.
        credits, hfa, _ = compute_game_credit(games)
        self.assertAlmostEqual(credits[1].rating, 0.0, places=6)

    def test_expected_wins_and_luck(self):
        games = round_robin({(1, 2): (21, 20), (2, 1): (20, 21), (1, 3): (21, 20), (3, 1): (20, 21),
                             (2, 3): (10, 30), (3, 2): (30, 10)})
        credits, _, _ = compute_game_credit(games)
        # Team 1 went 4-0 on one-point wins; team 3 dominated team 2. Team 1's record outruns its credit.
        self.assertEqual(credits[1].wins, 4)
        self.assertGreater(credits[1].wins - credits[1].expected_wins, credits[3].wins - credits[3].expected_wins)

    def test_ties_count_half(self):
        credits, _, _ = compute_game_credit([game(1, 1, 1, 2, 17, 17)])
        self.assertEqual(credits[1].wins, 0.5)


if __name__ == "__main__":
    unittest.main()
