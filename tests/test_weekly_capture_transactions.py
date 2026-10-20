"""pipeline/weekly_capture.py and League Transactions (team management step 2, Phase 6): command building
with --since, folder states and the transactions run. No console, no database."""
import os, tempfile, unittest
from unittest import mock

import pipeline.weekly_capture as wc


class CommandTest(unittest.TestCase):
    def capture(self, mode, since, week_label=None, dirs=()):
        folder = tempfile.mkdtemp()
        for d in dirs:
            os.makedirs(os.path.join(folder, d))
        calls = []

        def run_cmd(args, log_path, timeout):
            calls.append(args)
            open(os.path.join(folder, "week.json"), "w").write("{}")
            return 0, "Done"
        with mock.patch.object(wc, "run_cmd", side_effect=run_cmd):
            wc.step_capture(folder, os.path.join(folder, "log"), mode, week_label, since=since)
        return calls[0]

    def test_weekly_passes_since(self):
        cmd = self.capture("weekly", "2036 SEASON - WEEK 4")
        self.assertEqual(cmd[cmd.index("--since") + 1], "2036 SEASON - WEEK 4")

    def test_playoff_passes_since(self):
        cmd = self.capture("playoff", "2036 SEASON - WEEK 18")
        self.assertIn("playoff", cmd)
        self.assertIn("--since", cmd)

    def test_transactions_mode(self):
        cmd = self.capture("transactions", None)
        self.assertEqual(cmd[cmd.index("--only") + 1], "transactions")
        self.assertNotIn("--since", cmd)
        cmd = self.capture("transactions", "2036 SEASON - WEEK 4", dirs=("transactions",))
        self.assertIn("--resume", cmd)


class FolderTest(unittest.TestCase):
    def test_transactions_folder_and_extract_gate(self):
        f = tempfile.mkdtemp()
        os.makedirs(os.path.join(f, "transactions"))
        self.assertEqual(wc.folder_mode(f), "transactions")
        self.assertFalse(wc.extract_done(f, "transactions"))
        os.makedirs(os.path.join(f, "stats"))
        self.assertEqual(wc.folder_mode(f), "weekly")
        for name in ("schedule.csv", "stats_passing.csv"):
            open(os.path.join(f, name), "w").close()
        self.assertTrue(wc.extract_done(f, "weekly"))          # no transactions pages: nothing to wait for
        open(os.path.join(f, "transactions", "page_000.png"), "w").close()
        self.assertFalse(wc.extract_done(f, "weekly"))
        open(os.path.join(f, "transactions.csv"), "w").close()
        self.assertTrue(wc.extract_done(f, "weekly"))


class TransactionsRunTest(unittest.TestCase):
    def test_run_order_no_engines(self):
        tmp = tempfile.mkdtemp()
        calls = []

        def capture(folder, log_path, mode="weekly", week_label=None, **kw):
            calls.append(("capture", mode, kw.get("since")))
            os.makedirs(os.path.join(folder, "transactions"), exist_ok=True)
            open(os.path.join(folder, ".capture_done"), "w").close()

        def extract(folder, log_path, mode="weekly"):
            calls.append(("extract", mode))
            open(os.path.join(folder, "transactions.csv"), "w").close()

        with mock.patch.multiple(
                wc, CAPTURES=tmp, LOCK_PATH=os.path.join(tmp, ".lock"), notify=lambda m, **kw: calls.append(("notify", kw.get("title"))),
                step_connect=lambda lp: calls.append(("connect",)), step_capture=capture, step_extract=extract,
                load_status=lambda f: None, step_load=lambda f, replace=False, mode="weekly": calls.append(("load", mode)) or {},
                league_state=lambda: (2036, 4), since_heading=lambda s, weekly=True: "2036 SEASON - WEEK 1",
                step_engines=lambda s: calls.append(("engines",)), step_gm=lambda s, next_stage=False: calls.append(("gm", next_stage)),
                step_scouting=lambda s: calls.append(("scouting",)), step_articles=lambda s, week=None: calls.append(("articles",))):
            os.makedirs(os.path.join(tmp, "tx1"))
            self.assertEqual(wc.run("tx1", mode="transactions"), 0)
        self.assertEqual([c[0] for c in calls], ["notify", "connect", "capture", "notify", "extract", "load", "notify"])
        self.assertEqual(calls[2], ("capture", "transactions", "2036 SEASON - WEEK 1"))   # first run: from Week 1
        self.assertEqual(calls[5], ("load", "transactions"))

    def test_next_offseason_stage_runs_the_gm_step_after_the_load(self):
        """--gm-next-stage (#debug Next offseason stage): the transactions scan, then the GM agents' next stage."""
        tmp = tempfile.mkdtemp()
        calls = []

        def capture(folder, log_path, mode="weekly", week_label=None, **kw):
            os.makedirs(os.path.join(folder, "transactions"), exist_ok=True)
            open(os.path.join(folder, ".capture_done"), "w").close()

        def extract(folder, log_path, mode="weekly"):
            open(os.path.join(folder, "transactions.csv"), "w").close()

        def load(f, replace=False, mode="weekly"):
            calls.append(("load",))
            if self.fail_load:
                raise wc.WeekStopped("load failed")
            return {}

        for fail in (False, True):
            self.fail_load = fail
            calls.clear()
            name = f"tx_gm_{fail}"
            with mock.patch.multiple(
                    wc, CAPTURES=tmp, LOCK_PATH=os.path.join(tmp, ".lock"), notify=lambda m, **kw: None,
                    step_connect=lambda lp: None, step_capture=capture, step_extract=extract,
                    load_status=lambda f: None, step_load=load, league_state=lambda: (2036, 23),
                    since_heading=lambda s, weekly=True: "2036 SEASON - WEEK 1",
                    step_gm=lambda s, next_stage=False: calls.append(("gm", s, next_stage)),
                    step_scouting=lambda s: calls.append(("scouting", s))):
                os.makedirs(os.path.join(tmp, name))
                code = wc.run(name, mode="transactions", gm_stage=True)
            if fail:                                       # a failed load stops before the GMs see stale books
                self.assertEqual((code, calls), (1, [("load",)]))
            else:
                self.assertEqual((code, calls), (0, [("load",), ("gm", 2036, True), ("scouting", 2036)]))


if __name__ == "__main__":
    unittest.main()
