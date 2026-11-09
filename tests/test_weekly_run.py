"""Unit tests for picking the newsroom publication week (newsroom/weekly_run.py). No database required.

Run: python3 -m unittest tests.test_weekly_run
"""
import unittest

from unittest import mock

from newsroom.weekly_run import FINAL_WEEK, publication_week, update_writer_relationships


def weeks(*rows):
    """rows: (week, non_bye_games, completed_games)"""
    return list(rows)


class _Cursor:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self):
        self.committed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return _Cursor()

    def commit(self):
        self.committed = True


class WriterRelationshipUpkeepTests(unittest.TestCase):
    """The weekly run grades season calls before applying last week's graded picks/calls."""

    def _run(self, week, season, graded=1, changed=2):
        conn = _Conn()
        order = []
        with mock.patch("psycopg.connect", return_value=conn), \
                mock.patch("newsroom.agent_memory.grade_season_calls",
                           side_effect=lambda cur, s: order.append(("grade", s)) or graded), \
                mock.patch("newsroom.relationships.apply_graded",
                           side_effect=lambda cur, s, w: order.append(("apply", s, w)) or changed), \
                mock.patch("newsroom.relationships.decay",
                           side_effect=lambda cur, s: order.append(("decay", s)) or changed):
            result = update_writer_relationships("dsn", season, week)
        return result, order, conn

    def test_regular_week_grades_then_applies_last_week(self):
        result, order, conn = self._run(week=3, season=2026)
        self.assertEqual(result, {"graded": 1, "changed": 2})
        self.assertEqual(order, [("grade", 2026), ("apply", 2026, 2)])
        self.assertTrue(conn.committed)

    def test_week_one_grades_last_season_and_decays(self):
        result, order, conn = self._run(week=1, season=2026)
        self.assertEqual(order, [("grade", 2025), ("decay", 2026)])
        self.assertTrue(conn.committed)

    def test_a_failure_is_reported_and_never_raised(self):
        with mock.patch("psycopg.connect", side_effect=RuntimeError("no database")):
            result = update_writer_relationships("dsn", 2026, 3)
        self.assertIn("no database", result["error"])


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
