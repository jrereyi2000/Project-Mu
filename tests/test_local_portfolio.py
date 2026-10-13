"""Unit tests for the local beat portfolio plan (newsroom/local_portfolio.py). No database required.

Run: python3 -m unittest tests.test_local_portfolio
"""
import unittest

from newsroom.local_portfolio import POSTGAME, PRIMER, SEASON_PROGRESS, SPOTLIGHT, plan_local_portfolio


def types(plan):
    return [t for _, t in plan]


class LocalPortfolioTests(unittest.TestCase):
    def test_single_reporter_writes_the_whole_portfolio(self):
        plan = plan_local_portfolio(1, 8, True, True)
        self.assertEqual(types(plan), [POSTGAME, PRIMER, SPOTLIGHT, SEASON_PROGRESS])
        self.assertEqual({r for r, _ in plan}, {0})

    def test_season_progress_report_starts_week_6(self):
        self.assertNotIn(SEASON_PROGRESS, types(plan_local_portfolio(2, 5, True, True)))
        self.assertIn(SEASON_PROGRESS, types(plan_local_portfolio(2, 6, True, True)))

    def test_no_postgame_without_a_game_last_week(self):
        self.assertNotIn(POSTGAME, types(plan_local_portfolio(2, 1, False, True)))

    def test_no_primer_on_a_bye(self):
        self.assertNotIn(PRIMER, types(plan_local_portfolio(2, 9, True, False)))

    def test_existing_types_are_skipped(self):
        plan = plan_local_portfolio(3, 9, True, True, existing_types={PRIMER})
        self.assertEqual(types(plan), [POSTGAME, SPOTLIGHT, SEASON_PROGRESS])

    def test_postgame_reporter_rotates_week_to_week(self):
        writers = {plan_local_portfolio(3, w, True, True)[0][0] for w in (7, 8, 9)}
        self.assertEqual(writers, {0, 1, 2})

    def test_work_is_spread_across_reporters(self):
        plan = plan_local_portfolio(2, 10, True, True)
        self.assertEqual(sorted(r for r, _ in plan), [0, 0, 1, 1])

    def test_no_reporters_no_articles(self):
        self.assertEqual(plan_local_portfolio(0, 8, True, True), [])


if __name__ == "__main__":
    unittest.main()
