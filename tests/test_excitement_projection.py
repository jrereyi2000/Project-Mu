"""Unit tests for the projected Excitement Index and its display scale. No database required.

Run: python3 -m unittest tests.test_excitement_projection
"""
import unittest

from engines.excitement_projection import (
    DEFAULT_MARGIN_SD,
    DEFAULT_OVR_MARGIN_SLOPE,
    MIN_GAMES_FOR_FIT,
    SHRINK_GAMES,
    LeagueModel,
    TeamProfile,
    build_profiles,
    expected_competitiveness,
    expected_upset,
    fit_margin_model,
    project_matchup,
)
from engines.gotw_engine import defensive_outlier_points, display_excitement, player_outlier_points, star_score, upset_score


def row(team_id, opponent_id, pf, pa, is_home=True, yds=350, top3=85.0, outliers=10):
    return {"team_id": team_id, "opponent_id": opponent_id, "is_home": is_home, "pf": pf, "pa": pa,
            "yds_for": yds, "top3": top3, "outliers": outliers}


class DisplayScaleTests(unittest.TestCase):
    def test_anchor_points(self):
        self.assertEqual(display_excitement(40), 70.0)
        self.assertEqual(display_excitement(100), 100.0)
        self.assertEqual(display_excitement(70), 85.0)

    def test_floor_and_none(self):
        self.assertEqual(display_excitement(10), 70.0)
        self.assertIsNone(display_excitement(None))

    def test_order_preserved_above_floor(self):
        values = [display_excitement(r) for r in (41, 55, 58.2, 72.5, 83.65)]
        self.assertEqual(values, sorted(values))
        self.assertEqual(display_excitement(58.2), 79.1)


class DefensiveOutlierTests(unittest.TestCase):
    """Defensive and special-teams milestones (captured game lines only), on the offensive outlier scale."""

    def test_quiet_line_scores_nothing(self):
        self.assertEqual(defensive_outlier_points(2.5, 1, 0, 0, 1, 0, 54), 0)
        self.assertEqual(defensive_outlier_points(None, None, None, None, None, 0, None), 0)

    def test_milestones(self):
        self.assertEqual(defensive_outlier_points(3, 0, 0, 0, 0, 0, None), 12)          # 3-sack game
        self.assertEqual(defensive_outlier_points(0, 2, 1, 0, 0, 0, None), 22)          # 2 INT, one returned
        self.assertEqual(defensive_outlier_points(0, 0, 0, 0, 0, 2, None), 20)          # two return TDs
        self.assertEqual(defensive_outlier_points(0, 0, 0, 1, 2, 0, 56), 18)            # safety, 2 FF, 56-yd FG

    def test_same_scale_as_offense(self):
        # a 3-sack game is worth a 150-yard day; a defensive TD more than a 2-TD day
        self.assertEqual(defensive_outlier_points(3, 0, 0, 0, 0, 0, None), player_outlier_points(0, 0, 150, 0, 0, 0))
        self.assertGreater(defensive_outlier_points(0, 0, 1, 0, 0, 0, None), player_outlier_points(0, 0, 0, 2, 0, 0))
        self.assertGreater(star_score(85, 85, 12), star_score(85, 85, 0))


class ProjectionMathTests(unittest.TestCase):
    def test_even_matchup_more_competitive_than_mismatch(self):
        self.assertGreater(expected_competitiveness(0, 14), expected_competitiveness(14, 14))

    def test_expected_competitiveness_matches_closed_form_for_tiny_spread(self):
        # With almost no variance, E[C] ≈ C(|mu|) = 100 - 4·|mu|
        self.assertAlmostEqual(expected_competitiveness(5, 0.2), 80, delta=0.5)

    def test_expected_upset_between_chalk_and_upset(self):
        u = expected_upset(90, 80, mu_home=10, sd=14)
        self.assertGreater(u, upset_score(90, 80, favorite_won=True))
        self.assertLess(u, upset_score(90, 80, favorite_won=False))

    def test_equal_ovr_is_chalk(self):
        self.assertEqual(expected_upset(82, 82, 0, 14), upset_score(82, 82, favorite_won=True))

    def test_projection_on_raw_scale(self):
        league = TeamProfile(0, 24.0, 340.0, 85.0, 10.0)
        model = LeagueModel(league, DEFAULT_MARGIN_SD, DEFAULT_OVR_MARGIN_SLOPE)
        proj = project_matchup(league, league, 85, 80, model)
        self.assertTrue(30 < proj["E"] < 80)
        self.assertAlmostEqual(proj["expected_margin_home"], 5 * DEFAULT_OVR_MARGIN_SLOPE)

    def test_star_power_raises_projection(self):
        league = TeamProfile(0, 24.0, 340.0, 85.0, 10.0)
        stars = TeamProfile(8, 24.0, 340.0, 95.0, 40.0)
        model = LeagueModel(league, DEFAULT_MARGIN_SD, DEFAULT_OVR_MARGIN_SLOPE)
        self.assertGreater(project_matchup(stars, stars, 85, 85, model)["E"], project_matchup(league, league, 85, 85, model)["E"])


class FittingTests(unittest.TestCase):
    def test_profiles_shrink_toward_league(self):
        rows = [row(1, 2, 40, 10, top3=95.0)] + [row(t, t + 1, 20, 20, top3=85.0) for t in range(3, 20)]
        profiles, league = build_profiles(rows)
        expected = (95.0 + SHRINK_GAMES * league.top3) / (1 + SHRINK_GAMES)
        self.assertAlmostEqual(profiles[1].top3, expected)
        self.assertLess(profiles[1].top3, 95.0)

    def test_missing_history_falls_back_to_prior_then_constants(self):
        _, league = build_profiles([], [row(1, 2, 30, 20, yds=400)])
        self.assertEqual(league.yds_for, 400)
        _, league = build_profiles([], [])
        self.assertEqual(league.top3, 80.0)

    def test_fit_recovers_slope(self):
        ovrs = {t: 80 + (t % 10) for t in range(1, 41)}
        rows = []
        for t in range(1, 41):
            opp = (t % 40) + 1
            margin = 3.0 * (ovrs[t] - ovrs[opp])
            rows.append(row(t, opp, 20 + max(margin, 0), 20 - min(margin, 0)))
        sd, slope = fit_margin_model(rows, ovrs)
        self.assertAlmostEqual(slope, 3.0, places=6)

    def test_fit_defaults_with_few_games(self):
        rows = [row(1, 2, 21, 20)] * (MIN_GAMES_FOR_FIT - 1)
        self.assertEqual(fit_margin_model(rows, {}), (DEFAULT_MARGIN_SD, DEFAULT_OVR_MARGIN_SLOPE))


if __name__ == "__main__":
    unittest.main()
