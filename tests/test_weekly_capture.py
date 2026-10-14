"""pipeline/weekly_capture.py: step order, stopping on a failed step, resuming, shrinking. No console,
no database: every step is replaced."""
import json, os, tempfile, unittest
from unittest import mock

from PIL import Image

import pipeline.weekly_capture as wc


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.calls = []
        self.notices = []
        p = mock.patch.multiple(
            wc, CAPTURES=self.tmp, LOCK_PATH=os.path.join(self.tmp, ".lock"),
            notify=lambda msg, **kw: self.notices.append((kw.get("title"), msg)),
            step_connect=lambda log_path: self.calls.append("connect"),
            step_capture=self.fake_capture,
            step_extract=self.fake_extract,
            load_status=lambda folder: self.status,
            step_load=self.fake_load,
            league_state=lambda: (2035, 19),
            step_engines=lambda season: self.calls.append(f"engines {season}"),
            step_articles=lambda season: (self.calls.append("articles"), {"status": "published", "week": 19})[1],
        )
        p.start()
        self.addCleanup(p.stop)
        self.status = None
        self.load_error = None

    def start(self, name):
        """A new run named `name` (run() takes a name only to resume, so the folder is made first)."""
        os.makedirs(os.path.join(self.tmp, name))
        return wc.run(name)

    def fake_capture(self, folder, log_path):
        self.calls.append("capture")
        os.makedirs(os.path.join(folder, "stats"), exist_ok=True)
        Image.new("RGB", (40, 20), "red").save(os.path.join(folder, "stats", "01_passing_000.png"))
        os.makedirs(os.path.join(folder, "stop_debug"), exist_ok=True)
        json.dump({"current": "SEASON WEEK 19", "captured": "SEASON WEEK 18"}, open(os.path.join(folder, "week.json"), "w"))

    def fake_extract(self, folder, log_path):
        self.calls.append("extract")
        for f in ("schedule.csv", "stats_passing.csv"):
            open(os.path.join(folder, f), "w").write("x\n")

    def fake_load(self, folder):
        self.calls.append("load")
        if self.load_error:
            raise wc.WeekStopped(self.load_error)
        return {"mode": "weekly", "week": 18}

    def test_full_run_in_order_and_shrunk(self):
        self.assertEqual(self.start("r1"), 0)
        self.assertEqual(self.calls, ["connect", "capture", "extract", "load", "engines 2035", "articles"])
        folder = os.path.join(self.tmp, "r1")
        self.assertTrue(os.path.exists(os.path.join(folder, "stats", "01_passing_000.webp")))
        self.assertFalse(os.path.exists(os.path.join(folder, "stats", "01_passing_000.png")))
        self.assertFalse(os.path.exists(os.path.join(folder, "stop_debug")))
        self.assertTrue(os.path.exists(os.path.join(folder, "schedule.csv")))
        titles = [t for t, _ in self.notices]
        self.assertEqual(titles, ["Mu: capture starting", "Mu: your turn", "Mu: week done"])

    def test_failed_load_stops_the_week(self):
        self.load_error = "Load stopped, nothing written: bad CSV"
        self.assertEqual(self.start("r2"), 1)
        self.assertEqual(self.calls, ["connect", "capture", "extract", "load"])     # no engines, no articles
        title, msg = self.notices[-1]
        self.assertEqual(title, "Mu: week stopped")
        self.assertIn("bad CSV", msg)
        self.assertIn("--resume r2", msg)
        folder = os.path.join(self.tmp, "r2")
        self.assertTrue(os.path.exists(os.path.join(folder, "stats", "01_passing_000.png")))  # kept for a retry

    def test_resume_skips_finished_steps(self):
        self.start("r3")
        self.calls.clear()
        self.status = "loaded"
        self.assertEqual(wc.run("r3"), 0)
        self.assertEqual(self.calls, ["engines 2035", "articles"])

    def test_resume_of_a_missing_folder_is_refused(self):
        self.assertEqual(wc.run("nope"), 1)
        self.assertEqual(self.calls, [])

    def test_engine_failure_stops_before_articles(self):
        def boom(season):
            self.calls.append("engines")
            raise wc.WeekStopped("Backfill / engines failed")
        with mock.patch.object(wc, "step_engines", boom):
            self.assertEqual(self.start("r4"), 1)
        self.assertNotIn("articles", self.calls)

    def test_one_run_at_a_time(self):
        import fcntl
        lock = open(wc.LOCK_PATH, "w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            self.assertEqual(self.start("r5"), 1)
            self.assertEqual(self.calls, [])
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()


if __name__ == "__main__":
    unittest.main()
