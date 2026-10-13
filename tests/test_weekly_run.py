"""Unit tests for picking the newsroom publication week (newsroom/weekly_run.py). No database required.

Run: python3 -m unittest tests.test_weekly_run
"""
import unittest

from newsroom.weekly_run import FINAL_WEEK, publication_week


def weeks(*rows):
    """rows: (week, non_bye_games, completed_games)"""
    return list(rows)


class PublicationWeekTests(unittest.TestCase):
    def test_no_games_played_publishes_week_1(self):
        self.assertEqual(publication_week(weeks((1, 16, 0), (2, 16, 0))), 1)

    def test_empty_schedule_publishes_week_1(self):
        self.assertEqual(publication_week([]), 1)

    def test_after_week_5_finishes_publish_week_6(self):
        rows = weeks(*[(w, 16, 16) for w in range(1, 6)], (6, 14, 0), (7, 15, 0))
        self.assertEqual(publication_week(rows), 6)

    def test_partially_played_week_does_not_count(self):
        # User played their Week 6 game; CPU games not simmed yet -> still the Week 6 slate.
        rows = weeks(*[(w, 16, 16) for w in range(1, 6)], (6, 14, 1))
        self.assertEqual(publication_week(rows), 6)

    def test_bye_rows_are_ignored(self):
        # 14 real games + bye rows; non_bye_games already excludes byes.
        rows = weeks((1, 16, 16), (2, 14, 14))
        self.assertEqual(publication_week(rows), 3)

    def test_week_18_rolls_into_wild_card_slate(self):
        rows = weeks(*[(w, 16, 16) for w in range(1, 19)], (19, 6, 0))
        self.assertEqual(publication_week(rows), 19)

    def test_conference_championships_publish_super_bowl_bye_slate(self):
        rows = weeks(*[(w, 16, 16) for w in range(1, 19)], (19, 6, 6), (20, 4, 4), (21, 2, 2))
        self.assertEqual(publication_week(rows), 22)

    def test_nothing_to_publish_after_super_bowl(self):
        rows = weeks((21, 2, 2), (FINAL_WEEK, 1, 1))
        self.assertIsNone(publication_week(rows))

    def test_week_with_only_byes_is_not_finished(self):
        rows = weeks((1, 16, 16), (2, 0, 0))
        self.assertEqual(publication_week(rows), 2)


if __name__ == "__main__":
    unittest.main()
