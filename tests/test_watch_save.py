"""
tests/test_watch_save.py: Unit tests for pipeline/watch_save.py.
Verifies file debouncing, week classification, single execution locking,
flag handling (--once, --dry-run, --no-articles, --force), and error notifications.
"""
import json
import os
import tempfile
import time
import unittest
from unittest import mock

import pytest

from pipeline import watch_save as ws


class DebouncerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.debouncer = ws.FileDebouncer(debounce_seconds=10)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_file(self, name, size=100, mtime_offset=0):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as f:
            f.write(b"x" * size)
        t = time.time() + mtime_offset
        os.utime(path, (t, t))
        return path

    def test_empty_file_not_settled(self):
        path = self.make_file("empty.save", size=0, mtime_offset=-20)
        self.assertFalse(self.debouncer.is_settled(path))

    def test_recent_file_not_settled(self):
        path = self.make_file("recent.save", size=100, mtime_offset=-2)
        # modified 2 seconds ago, debounce is 10s -> not settled
        self.assertFalse(self.debouncer.is_settled(path))

    def test_old_file_settled_immediately(self):
        # File untouched for 60 seconds
        path = self.make_file("old.save", size=100, mtime_offset=-60)
        self.assertTrue(self.debouncer.is_settled(path))

    def test_write_burst_resets_debounce(self):
        path = self.make_file("burst.save", size=100, mtime_offset=-5)
        now = time.time()

        # Check 1: 5 seconds old -> not settled
        self.assertFalse(self.debouncer.is_settled(path, now=now))

        # Check 2: write happens 3 seconds later
        now += 3
        with open(path, "wb") as f:
            f.write(b"x" * 200)
        os.utime(path, (now, now))
        self.assertFalse(self.debouncer.is_settled(path, now=now))

        # Check 3: 5 seconds after burst -> still not settled (needs 10s)
        now += 5
        self.assertFalse(self.debouncer.is_settled(path, now=now))

        # Check 4: 11 seconds after burst -> settled!
        now += 6
        self.assertTrue(self.debouncer.is_settled(path, now=now))

    def test_already_processed_tracking(self):
        path = self.make_file("proc.save", size=100, mtime_offset=-60)
        self.assertFalse(self.debouncer.is_already_processed(path))
        self.debouncer.mark_processed(path)
        self.assertTrue(self.debouncer.is_already_processed(path))

        # Modifying file makes it not processed anymore
        t = time.time()
        os.utime(path, (t, t))
        self.assertFalse(self.debouncer.is_already_processed(path))


class WeekClassificationTest(unittest.TestCase):
    def test_same_week(self):
        self.assertEqual(ws.classify_week(2036, 5, 2036, 5), "same_week")

    def test_new_week_same_season(self):
        self.assertEqual(ws.classify_week(2036, 6, 2036, 5), "new_week")

    def test_new_week_season_rollover(self):
        self.assertEqual(ws.classify_week(2037, 1, 2036, 23), "new_week")

    def test_older_week_same_season(self):
        self.assertEqual(ws.classify_week(2036, 4, 2036, 5), "older_week")

    def test_older_week_previous_season(self):
        self.assertEqual(ws.classify_week(2035, 10, 2036, 5), "older_week")

    def test_different_franchise_far_future(self):
        self.assertEqual(ws.classify_week(2040, 1, 2036, 5), "different_franchise")

    def test_no_current_week(self):
        self.assertEqual(ws.classify_week(2036, None, 2036, 5), "no_current_week")


class LockTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.lock_path = os.path.join(self.tmp, "test.lock")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_lock_acquire_and_release(self):
        with ws.run_lock(self.lock_path):
            self.assertTrue(os.path.exists(self.lock_path))
            # Second acquire attempt within the with-block must fail
            with self.assertRaises(ws.LockError):
                with ws.run_lock(self.lock_path):
                    pass

        # Once released, can acquire again
        with ws.run_lock(self.lock_path):
            pass


class ProcessSaveTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.save_file = os.path.join(self.tmp, "CAREER-TEST")
        with open(self.save_file, "w") as f:
            f.write("mock save")

        self.fake_snapshot = {
            "meta": {"reader_version": 2, "game_year": 27},
            "season": {
                "calendar_year": 2036,
                "franchise_year": 1,
                "week_type": "RegularSeason",
                "week_index": 5, # Week 6
            },
            "teams": [],
            "players": [],
            "games": [],
        }

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def fake_snapshot_save(self, save_path, run_folder, **kw):
        snap_path = os.path.join(run_folder, "save.json")
        with open(snap_path, "w") as f:
            json.dump(self.fake_snapshot, f)
        return snap_path

    @mock.patch("pipeline.watch_save.send_notify")
    @mock.patch("pipeline.watch_save.step_articles")
    @mock.patch("pipeline.watch_save.run_weekly_engines")
    @mock.patch("pipeline.load_save.load")
    @mock.patch("pipeline.watch_save.get_league_state")
    def test_new_week_full_pipeline(self, mock_state, mock_load, mock_engines, mock_articles, mock_notify):
        mock_state.return_value = (2036, 5) # stored is week 5, save is week 6
        mock_load.return_value = {"league_state": "2036 week 5 -> 2036 week 6", "players": 100, "games_completed": 16}
        mock_articles.return_value = {"status": "published", "week": 6}

        with mock.patch("pipeline.watch_save.snapshot_save", side_effect=self.fake_snapshot_save):
            res = ws.process_save(
                self.save_file, run_folder=self.tmp, dry_run=False, no_articles=False
            )

        self.assertEqual(res["status"], "success")
        self.assertFalse(res["dry_run"])
        self.assertEqual(res["week"], 6)

        # Verified load_save called with commit=True
        mock_load.assert_called_once()
        self.assertTrue(mock_load.call_args[1].get("commit"))

        # Verified engines and articles called
        mock_engines.assert_called_once_with(2036, mode="weekly", log_fn=mock.ANY)
        mock_articles.assert_called_once_with(2036, week=6)
        mock_notify.assert_called_once()

    @mock.patch("pipeline.watch_save.send_notify")
    @mock.patch("pipeline.watch_save.step_articles")
    @mock.patch("pipeline.watch_save.run_weekly_engines")
    @mock.patch("pipeline.load_save.load")
    @mock.patch("pipeline.watch_save.get_league_state")
    def test_dry_run_skips_commit_engines_and_articles(self, mock_state, mock_load, mock_engines, mock_articles, mock_notify):
        mock_state.return_value = (2036, 5)
        mock_load.return_value = {"league_state": "rolled back", "players": 100, "games_completed": 16}

        with mock.patch("pipeline.watch_save.snapshot_save", side_effect=self.fake_snapshot_save):
            res = ws.process_save(
                self.save_file, run_folder=self.tmp, dry_run=True, no_articles=False
            )

        self.assertEqual(res["status"], "success")
        self.assertTrue(res["dry_run"])

        # commit must be False in dry-run
        self.assertFalse(mock_load.call_args[1].get("commit"))
        # engines and articles must not run in dry-run
        mock_engines.assert_not_called()
        mock_articles.assert_not_called()

    @mock.patch("pipeline.watch_save.send_notify")
    @mock.patch("pipeline.watch_save.step_articles")
    @mock.patch("pipeline.watch_save.run_weekly_engines")
    @mock.patch("pipeline.load_save.load")
    @mock.patch("pipeline.watch_save.get_league_state")
    def test_no_articles_flag(self, mock_state, mock_load, mock_engines, mock_articles, mock_notify):
        mock_state.return_value = (2036, 5)
        mock_load.return_value = {"league_state": "committed", "players": 100, "games_completed": 16}

        with mock.patch("pipeline.watch_save.snapshot_save", side_effect=self.fake_snapshot_save):
            res = ws.process_save(
                self.save_file, run_folder=self.tmp, dry_run=False, no_articles=True
            )

        self.assertEqual(res["status"], "success")
        self.assertTrue(mock_load.call_args[1].get("commit"))
        mock_engines.assert_called_once()
        mock_articles.assert_not_called()

    @mock.patch("pipeline.watch_save.send_notify")
    @mock.patch("pipeline.load_save.load")
    @mock.patch("pipeline.watch_save.get_league_state")
    def test_same_week_is_noop(self, mock_state, mock_load, mock_notify):
        mock_state.return_value = (2036, 6) # save is also week 6

        with mock.patch("pipeline.watch_save.snapshot_save", side_effect=self.fake_snapshot_save):
            res = ws.process_save(self.save_file, run_folder=self.tmp)

        self.assertEqual(res["status"], "no_op")
        self.assertEqual(res["reason"], "same_week")
        mock_load.assert_not_called()
        mock_notify.assert_not_called()

    @mock.patch("pipeline.watch_save.send_notify")
    @mock.patch("pipeline.load_save.load")
    @mock.patch("pipeline.watch_save.get_league_state")
    def test_older_week_notifies_and_noops(self, mock_state, mock_load, mock_notify):
        mock_state.return_value = (2036, 7) # stored is week 7, save is week 6

        with mock.patch("pipeline.watch_save.snapshot_save", side_effect=self.fake_snapshot_save):
            res = ws.process_save(self.save_file, run_folder=self.tmp)

        self.assertEqual(res["status"], "no_op")
        self.assertEqual(res["reason"], "older_week")
        mock_load.assert_not_called()
        mock_notify.assert_called_once()
        self.assertIn("older week", mock_notify.call_args[0][0])

    @mock.patch("pipeline.watch_save.send_notify")
    @mock.patch("pipeline.load_save.load")
    @mock.patch("pipeline.watch_save.get_league_state")
    def test_force_flag_bypasses_week_check(self, mock_state, mock_load, mock_notify):
        mock_state.return_value = (2036, 6) # same week
        mock_load.return_value = {"league_state": "forced", "players": 100, "games_completed": 16}

        with mock.patch("pipeline.watch_save.snapshot_save", side_effect=self.fake_snapshot_save):
            with mock.patch("pipeline.watch_save.run_weekly_engines"):
                with mock.patch("pipeline.watch_save.step_articles", return_value={"status": "published", "week": 6}):
                    res = ws.process_save(self.save_file, run_folder=self.tmp, force=True)

        self.assertEqual(res["status"], "success")
        mock_load.assert_called_once()


class FindFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_find_save_files(self):
        f1 = os.path.join(self.tmp, "CAREER-GIANTS")
        f2 = os.path.join(self.tmp, "CAREER-AUTOSAVE")
        f3 = os.path.join(self.tmp, "ROSTER-DATA")
        for f in (f1, f2, f3):
            with open(f, "w") as fh:
                fh.write("x")

        # Set f2 newer than f1
        now = time.time()
        os.utime(f1, (now - 20, now - 20))
        os.utime(f2, (now - 5, now - 5))

        matches = ws.find_save_files(self.tmp, pattern="CAREER-*")
        self.assertEqual(matches, [f2, f1])

        # Passing file path directly
        self.assertEqual(ws.find_save_files(f1), [f1])


@pytest.mark.integration
class WatchSaveIntegrationTest(unittest.TestCase):
    """End-to-end integration test against madden_test using CAREER-GIANTS-AUTOSAVE."""

    def test_sample_save_dry_run_into_madden_test(self):
        sample_save = os.path.join(ws.ROOT, "CAREER-GIANTS-AUTOSAVE")
        if not os.path.exists(sample_save):
            self.skipTest("CAREER-GIANTS-AUTOSAVE not present")

        # Snapshot into a temp folder and run dry-run with --force as season 2099
        tmp_folder = tempfile.mkdtemp(prefix="watch_test_")
        try:
            stored_season, stored_week = ws.get_league_state()
            res = ws.process_save(
                sample_save, season=2099, dry_run=True, force=True, run_folder=tmp_folder
            )
            self.assertEqual(res["status"], "success")
            self.assertTrue(res["dry_run"])
            self.assertEqual(res["season"], 2099)
            self.assertEqual(res["week"], 5)
            self.assertGreater(res["load_report"]["players"], 2000)
            self.assertGreater(res["load_report"]["games_completed"], 70)

            # Confirm league_state in madden_test was not changed (dry-run rolled back)
            after_season, after_week = ws.get_league_state()
            self.assertEqual((after_season, after_week), (stored_season, stored_week))
        finally:
            import shutil
            shutil.rmtree(tmp_folder, ignore_errors=True)

