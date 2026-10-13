"""Unit tests for the writer tools' season/week context (newsroom/run_context.py). No database required.

Run: python3 -m unittest tests.test_run_context
"""
import threading
import unittest

from newsroom.run_context import article_run_context, context_season, cutoff_week, is_visible_week


class RunContextTests(unittest.TestCase):
    def test_no_context_means_no_cutoff(self):
        self.assertIsNone(context_season())
        self.assertIsNone(cutoff_week(2035))
        self.assertTrue(is_visible_week(2035, 18))

    def test_publication_week_and_later_are_hidden(self):
        with article_run_context(2035, 7):
            self.assertEqual(context_season(), 2035)
            self.assertTrue(is_visible_week(2035, 6))
            self.assertFalse(is_visible_week(2035, 7))
            self.assertFalse(is_visible_week(2035, 12))

    def test_earlier_seasons_are_visible_later_seasons_hidden(self):
        with article_run_context(2036, 3):
            self.assertTrue(is_visible_week(2035, 23))
            self.assertFalse(is_visible_week(2037, 1))

    def test_context_resets_after_article(self):
        with article_run_context(2035, 7):
            pass
        self.assertIsNone(context_season())

    def test_threads_have_their_own_context(self):
        seen = {}

        def worker(week):
            with article_run_context(2035, week):
                barrier.wait()
                seen[week] = cutoff_week(2035)

        barrier = threading.Barrier(2)
        threads = [threading.Thread(target=worker, args=(w,)) for w in (5, 9)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(seen, {5: 5, 9: 9})


if __name__ == "__main__":
    unittest.main()
