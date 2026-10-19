"""pipeline/weekly_capture.py and League Transactions (team management step 2, Phase 6): the D5 re-scan pick,
command building with --since, folder states and the transactions run. No console, no database."""
import datetime, json, os, tempfile, unittest
from unittest import mock

import pipeline.weekly_capture as wc

T0 = datetime.datetime(2036, 10, 1)


def at(days):
    return T0 + datetime.timedelta(days=days)


class PickRescanTest(unittest.TestCase):
    def test_traded_always_rest_oldest_first_capped(self):
        pending = [(5, at(3), False), (6, at(1), False), (7, at(2), True)]
        teams, left = wc.pick_rescan([1, 2], [3, 4], pending, cap=2)
        self.assertEqual(teams, [1, 2, 7, 6, 5])          # traded (new + pending traded), then oldest 2
        self.assertEqual(left, [3, 4])                     # new non-trade teams wait behind older requests

    def test_traded_dont_count_toward_cap(self):
        teams, left = wc.pick_rescan(list(range(1, 11)), [11, 12], [], cap=8)
        self.assertEqual(teams, list(range(1, 13)))
        self.assertEqual(left, [])

    def test_no_duplicates(self):
        teams, _ = wc.pick_rescan([1], [1, 2], [(2, at(0), False), (1, at(1), False)])
        self.assertEqual(teams, [1, 2])


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


class RescanTest(unittest.TestCase):
    def test_capture_writes_plan_and_captures_sibling(self):
        root = tempfile.mkdtemp()
        folder = os.path.join(root, "run")
        os.makedirs(folder)
        captured = []
        with mock.patch.multiple(wc, rescan_teams=lambda f, s: ([(3, "CAR"), (9, "NO")], [12]),
                                 team_nicknames=lambda abbrs: [a + "!" for a in abbrs],
                                 step_capture=lambda f, lp, mode, teams=None, **kw: captured.append((f, mode, teams))):
            got = wc.rescan_capture(folder, os.path.join(folder, "log"), 2036)
        self.assertEqual(got, [3, 9])
        self.assertEqual(captured, [(folder + "_contracts", "contracts", ["CAR!", "NO!"])])
        plan = json.load(open(os.path.join(folder + "_contracts", "rescan.json")))
        self.assertEqual(plan, {"teams": [3, 9], "left_pending": [12]})

    def test_capture_failure_never_stops_the_week(self):
        folder = tempfile.mkdtemp()
        with mock.patch.object(wc, "rescan_teams", side_effect=RuntimeError("db down")):
            self.assertEqual(wc.rescan_capture(folder, os.path.join(folder, "log"), 2036), [])

    def test_load_marks_requests(self):
        root = tempfile.mkdtemp()
        folder = os.path.join(root, "run")
        sib = folder + "_contracts"
        os.makedirs(sib)
        json.dump({"teams": [3, 9], "left_pending": []}, open(os.path.join(sib, "rescan.json"), "w"))
        open(os.path.join(sib, ".capture_done"), "w").close()
        marked = []
        with mock.patch.multiple(wc, contracts_phase=lambda f, lp, teams=None: {"contracts": 100},
                                 mark_rescans_satisfied=lambda s, t, f: marked.append((s, t, os.path.basename(f))) or 2,
                                 shrink=lambda f: (0, 0)):
            wc.rescan_load(folder, os.path.join(root, "log"), 2036)
        self.assertEqual(marked, [(2036, [3, 9], "run_contracts")])


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
                league_state=lambda: (2036, 4), since_heading=lambda s, weekly=True: None if not weekly else "x",
                rescan_capture=lambda f, lp, s: calls.append(("rescan_capture",)) or [],
                rescan_load=lambda f, lp, s: calls.append(("rescan_load",)),
                step_engines=lambda s: calls.append(("engines",)), step_articles=lambda s, week=None: calls.append(("articles",))):
            os.makedirs(os.path.join(tmp, "tx1"))
            self.assertEqual(wc.run("tx1", mode="transactions"), 0)
        self.assertEqual([c[0] for c in calls], ["notify", "connect", "capture", "rescan_capture", "notify", "extract",
                                                 "load", "rescan_load", "notify"])
        self.assertEqual(calls[2], ("capture", "transactions", None))       # first run: whole list
        self.assertEqual(calls[6], ("load", "transactions"))


if __name__ == "__main__":
    unittest.main()
