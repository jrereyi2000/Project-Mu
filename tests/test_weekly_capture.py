"""pipeline/weekly_capture.py: step order, stopping on a failed step, resuming, shrinking. No console,
no database: every step is replaced."""
import json, os, tempfile, unittest
from unittest import mock

from PIL import Image

import pipeline.weekly_capture as wc

ORIG_STEP_CAPTURE = wc.step_capture


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
            league_state=lambda: (2035, 18),   # the run captures Week 18 (regular season)
            step_engines=lambda season: self.calls.append(f"engines {season}"),
            step_articles=lambda season, week=None: (self.calls.append(f"articles {week}" if week else "articles"),
                                                   {"status": "published", "week": week or 19})[1],
            set_current_week=lambda week: self.calls.append(f"current {week}"),
        )
        p.start()
        self.addCleanup(p.stop)
        self.status = None
        self.load_error = None

    def start(self, name):
        """A new run named `name` (run() takes a name only to resume, so the folder is made first)."""
        os.makedirs(os.path.join(self.tmp, name))
        return wc.run(name)

    def fake_capture(self, folder, log_path, mode="weekly", week_label=None):
        self.calls.append("capture")
        self.capture_mode = mode
        self.week_label = week_label
        os.makedirs(os.path.join(folder, "stats"), exist_ok=True)
        Image.new("RGB", (40, 20), "red").save(os.path.join(folder, "stats", "01_passing_000.png"))
        os.makedirs(os.path.join(folder, "stop_debug"), exist_ok=True)
        json.dump({"current": "SEASON WEEK 19", "captured": "SEASON WEEK 18"}, open(os.path.join(folder, "week.json"), "w"))
        open(os.path.join(folder, ".capture_done"), "w").close()

    def fake_extract(self, folder, log_path, mode="weekly"):
        self.calls.append("extract")
        if mode in ("games", "playoff"):
            open(os.path.join(folder, "game_scores.csv"), "w").write("x\n")
        elif mode == "roster":
            open(os.path.join(folder, "roster_cards.csv"), "w").write("x\n")
            open(os.path.join(folder, "roster_list.csv"), "w").write("x\n")
        elif mode == "prospects":
            open(os.path.join(folder, "prospects.csv"), "w").write("x\n")
        elif mode in ("draft-results", "draft_results"):
            open(os.path.join(folder, "draft_results.csv"), "w").write("x\n")
        for f in ("schedule.csv", "stats_passing.csv"):
            open(os.path.join(folder, f), "w").write("x\n")

    def fake_load(self, folder, replace=False, mode="weekly", **kwargs):
        self.replace = replace
        self.load_mode = mode
        self.calls.append("load")
        if self.load_error:
            raise wc.WeekStopped(self.load_error)
        return {"mode": mode, "week": 18}

    def test_full_run_in_order_and_shrunk(self):
        self.assertEqual(self.start("r1"), 0)
        self.assertEqual(self.capture_mode, "weekly")
        self.assertEqual(self.calls, ["connect", "capture", "extract", "load", "engines 2035", "articles"])
        folder = os.path.join(self.tmp, "r1")
        self.assertTrue(os.path.exists(os.path.join(folder, "stats", "01_passing_000.webp")))
        self.assertFalse(os.path.exists(os.path.join(folder, "stats", "01_passing_000.png")))
        self.assertFalse(os.path.exists(os.path.join(folder, "stop_debug")))
        self.assertTrue(os.path.exists(os.path.join(folder, "schedule.csv")))
        titles = [t for t, _ in self.notices]
        self.assertEqual(titles, ["Mu: capture starting", "Mu: your turn", "Mu: week done"])

    def test_roster_scan_runs_capture_extract_load_and_skips_articles(self):
        os.makedirs(os.path.join(self.tmp, "ros1"))
        self.assertEqual(wc.run("ros1", articles=True, mode="roster"), 0)
        self.assertEqual(self.calls, ["connect", "capture", "extract", "load"])
        self.assertEqual(self.capture_mode, "roster")
        self.assertEqual(self.notices[0][0], "Mu: capture starting")
        self.assertIn("Roster scan", self.notices[0][1])
        self.assertEqual(self.notices[-1][0], "Mu: roster scan done")
        self.assertIn("Rosters loaded for Season 2035", self.notices[-1][1])

    def test_prospects_scan_runs_capture_extract_load_and_skips_articles(self):
        os.makedirs(os.path.join(self.tmp, "prosp1"))
        self.assertEqual(wc.run("prosp1", articles=True, mode="prospects"), 0)
        self.assertEqual(self.calls, ["connect", "capture", "extract", "load"])
        self.assertEqual(self.capture_mode, "prospects")
        self.assertEqual(self.notices[0][0], "Mu: capture starting")
        self.assertIn("Draft prospect scan", self.notices[0][1])
        self.assertEqual(self.notices[-1][0], "Mu: prospects scan done")
        self.assertIn("Draft prospects loaded for Season 2035", self.notices[-1][1])

    def test_draft_results_scan_runs_capture_extract_load_and_skips_articles(self):
        os.makedirs(os.path.join(self.tmp, "dr1"))
        self.assertEqual(wc.run("dr1", articles=True, mode="draft-results"), 0)
        self.assertEqual(self.calls, ["connect", "capture", "extract", "load"])
        self.assertEqual(self.capture_mode, "draft-results")
        self.assertEqual(self.notices[0][0], "Mu: capture starting")
        self.assertIn("Draft results scan", self.notices[0][1])
        self.assertEqual(self.notices[-1][0], "Mu: draft-results scan done")
        self.assertIn("Draft results loaded for Season 2035", self.notices[-1][1])

    def test_season_scan_runs_capture_extract_load_and_skips_articles(self):
        os.makedirs(os.path.join(self.tmp, "seas1"))
        self.assertEqual(wc.run("seas1", articles=True, mode="season"), 0)
        self.assertEqual(self.calls, ["connect", "capture", "extract", "load"])
        self.assertEqual(self.capture_mode, "season")
        self.assertEqual(self.notices[0][0], "Mu: capture starting")
        self.assertIn("Season scan", self.notices[0][1])
        self.assertEqual(self.notices[-1][0], "Mu: season scan done")
        self.assertIn("Season setup (schedule, rosters, draft prospects) loaded for Season 2035", self.notices[-1][1])

    def test_game_scan_replaces_and_never_writes_articles(self):
        os.makedirs(os.path.join(self.tmp, "g1"))
        self.assertEqual(wc.run("g1", articles=True, mode="games"), 0)
        self.assertEqual(self.calls, ["connect", "capture", "extract", "load", "engines 2035"])
        self.assertTrue(self.replace)
        self.assertEqual(self.notices[0][0], "Mu: capture starting")
        self.assertIn("Game scan", self.notices[0][1])

    def test_postseason_week_is_a_playoff_run_with_articles(self):
        """League current week 19+: the round this run captures is a playoff round, so no Season Stats."""
        with mock.patch.object(wc, "league_state", lambda: (2035, 19)):
            self.assertEqual(self.start("p1"), 0)
        self.assertEqual(self.capture_mode, "playoff")
        self.assertEqual(self.calls, ["connect", "capture", "extract", "load", "engines 2035", "articles"])
        self.assertFalse(self.replace)
        self.assertIn("Playoff capture", self.notices[0][1])

    def test_week_18_results_run_is_a_weekly_run(self):
        with mock.patch.object(wc, "league_state", lambda: (2035, 18)):   # the run captures Week 18
            self.start("w18")
        self.assertEqual(self.capture_mode, "weekly")

    def test_week_22_is_articles_only(self):
        """Pro Bowl week: no capture or folder; engines, the Week 23 slate, then the league moves to week 23."""
        with mock.patch.object(wc, "league_state", lambda: (2035, 22)):
            self.assertEqual(wc.run(), 0)
        self.assertEqual(self.calls, ["engines 2035", "articles 23", "current 23"])
        self.assertEqual(os.listdir(self.tmp), [".lock"])

    def test_week_22_without_articles_stays_at_22(self):
        with mock.patch.object(wc, "league_state", lambda: (2035, 22)):
            self.assertEqual(wc.run(articles=False), 0)
        self.assertEqual(self.calls, ["engines 2035"])

    def test_super_bowl_capture_names_the_week(self):
        """Madden is in the offseason after the Super Bowl: the last capture names the Super Bowl week."""
        with mock.patch.object(wc, "league_state", lambda: (2035, 23)):
            self.assertEqual(self.start("sb"), 0)
        self.assertEqual((self.capture_mode, self.week_label), ("playoff", "POST SEASON SUPER BOWL"))

    def test_wild_card_capture_reads_the_week_itself(self):
        with mock.patch.object(wc, "league_state", lambda: (2035, 19)):
            self.start("wc")
        self.assertEqual((self.capture_mode, self.week_label), ("playoff", None))

    def test_super_bowl_step_capture_calls_postgame(self):
        with mock.patch.object(wc, "run_cmd", return_value=(0, "ok")) as mock_cmd:
            f = os.path.join(self.tmp, "sb_step")
            os.makedirs(f, exist_ok=True)
            with open(os.path.join(f, "week.json"), "w") as fh:
                fh.write("{}")
            ORIG_STEP_CAPTURE(f, "log.txt", mode="playoff", week_label=wc.SUPER_BOWL_LABEL)
            cmd = mock_cmd.call_args[0][0]
            self.assertIn("--only", cmd)
            self.assertIn("postgame", cmd)

    def test_folder_modes(self):
        f = os.path.join(self.tmp, "m")
        os.makedirs(os.path.join(f, "games"))
        self.assertEqual(wc.folder_mode(f), "games")
        os.makedirs(os.path.join(f, "schedule"))
        self.assertEqual(wc.folder_mode(f), "playoff")
        for name in ("game_scores.csv", "schedule.csv"):
            self.assertFalse(wc.extract_done(f, "playoff"))
            open(os.path.join(f, name), "w").close()
        self.assertTrue(wc.extract_done(f, "playoff"))

    def test_resumed_playoff_folder_stays_a_playoff_run(self):
        f = os.path.join(self.tmp, "p2")
        os.makedirs(os.path.join(f, "games"))
        os.makedirs(os.path.join(f, "schedule"))
        self.assertEqual(wc.playoff_or_weekly("p2"), "playoff")

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

    def test_resume_auto_detects_draft_results_mode(self):
        f = os.path.join(self.tmp, "dr_resume")
        os.makedirs(os.path.join(f, "draft_results"))
        self.assertEqual(wc.run("dr_resume"), 0)
        self.assertEqual(self.load_mode, "draft-results")
        self.assertEqual(self.notices[-1][0], "Mu: draft-results scan done")

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
