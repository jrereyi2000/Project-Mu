"""pipeline/weekly_capture.py --mode contracts (team management step 1, Phase 5). Temp folders only: run_cmd,
league_state, the teams query and every step after capture are replaced; no console, no database."""
import os
import sys
import tempfile
import unittest
from unittest import mock

import pipeline.weekly_capture as wc

ABBRS = {"CAR": "PANTHERS", "NO": "SAINTS", "TB": "BUCCANEERS"}


def nicknames(abbrs):
    bad = [a for a in abbrs if a.upper() not in ABBRS]
    if bad:
        raise wc.WeekStopped(f"Unknown team abbreviations {bad}; nothing was captured")
    return [ABBRS[a.upper()] for a in abbrs]


class FolderStateTest(unittest.TestCase):
    def folder(self, *dirs, files=()):
        d = tempfile.mkdtemp()
        for x in dirs:
            os.makedirs(os.path.join(d, x))
        for f in files:
            open(os.path.join(d, f), "w").close()
        return d

    def test_contracts_folder(self):
        d = self.folder("contracts")
        self.assertEqual(wc.folder_mode(d), "contracts")
        self.assertFalse(wc.capture_done(d, "contracts"))
        open(os.path.join(d, ".capture_done"), "w").close()
        self.assertTrue(wc.capture_done(d, "contracts"))
        self.assertFalse(wc.extract_done(d, "contracts"))
        open(os.path.join(d, "contracts.csv"), "w").close()
        self.assertFalse(wc.extract_done(d, "contracts"))
        open(os.path.join(d, "team_cap.csv"), "w").close()
        self.assertTrue(wc.extract_done(d, "contracts"))

    def test_season_with_contracts(self):
        season_csvs = ("season_schedule.csv", "roster_cards.csv", "roster_list.csv", "prospects.csv")
        d = self.folder("season_schedule", "roster", "contracts", files=season_csvs)
        self.assertEqual(wc.folder_mode(d), "season")
        self.assertFalse(wc.extract_done(d, "season"))          # contracts/ captured: its CSVs are required
        for f in ("contracts.csv", "team_cap.csv"):
            open(os.path.join(d, f), "w").close()
        self.assertTrue(wc.extract_done(d, "season"))
        self.assertTrue(wc.extract_done(self.folder("season_schedule", "roster", files=season_csvs), "season"))


class StepCaptureTest(unittest.TestCase):
    def capture(self, folder, teams=None):
        calls = []

        def run_cmd(args, log_path, timeout):
            calls.append((args, timeout))
            return 0, "Done"
        with mock.patch.object(wc, "run_cmd", side_effect=run_cmd):
            wc.step_capture(folder, os.path.join(folder, "log"), "contracts", teams=teams)
        return calls

    def test_resume_with_team_names(self):
        root = tempfile.mkdtemp()
        folder = os.path.join(root, "2036-10-19_1200")
        os.makedirs(os.path.join(folder, "contracts"))
        (args, timeout), = self.capture(folder, ["PANTHERS", "SAINTS"])
        self.assertEqual(args[1:], ["madden_capture.py", "--only", "contracts", "--resume", "2036-10-19_1200",
                                    "--team-names", "PANTHERS,SAINTS"])
        self.assertEqual(timeout, wc.CAPTURE_TIMEOUT)
        self.assertTrue(os.path.exists(os.path.join(folder, ".capture_done")))

    def test_new_full_run(self):
        folder = tempfile.mkdtemp()
        (args, _), = self.capture(folder)
        self.assertEqual(args[1:], ["madden_capture.py", "--only", "contracts", "--out", os.path.basename(folder)])

    def test_season_resume_counts_contracts_and_timeout(self):
        folder = tempfile.mkdtemp()
        os.makedirs(os.path.join(folder, "contracts"))
        calls = []
        with mock.patch.object(wc, "run_cmd", side_effect=lambda a, l, t: (calls.append((a, t)), (0, ""))[1]):
            wc.step_capture(folder, os.path.join(folder, "log"), "season")
        args, timeout = calls[0]
        self.assertIn("--resume", args)
        self.assertEqual(timeout, wc.CAPTURE_TIMEOUT * 3)


class RunContractsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.calls, self.notices = [], []
        self.status = None

        def fake_capture(folder, log_path, mode="weekly", week_label=None, teams=None):
            self.calls.append(("capture", mode, teams))
            open(os.path.join(folder, ".capture_done"), "w").close()

        def fake_extract(folder, log_path, mode="weekly"):
            self.calls.append(("extract", mode))
            for f in ("contracts.csv", "team_cap.csv"):
                open(os.path.join(folder, f), "w").close()

        def fake_load(folder, replace=False, mode="weekly"):
            self.calls.append(("load", mode))
            return {"mode": "contracts", "contracts": 53, "teams": 1, "superseded": 0, "cap_warnings": []}
        p = mock.patch.multiple(
            wc, CAPTURES=self.tmp, LOCK_PATH=os.path.join(self.tmp, ".lock"),
            notify=lambda msg, **kw: self.notices.append((kw.get("title"), msg)),
            step_connect=lambda log_path: self.calls.append(("connect",)),
            step_capture=fake_capture, step_extract=fake_extract, step_load=fake_load,
            load_status=lambda folder: self.status, team_nicknames=nicknames,
            league_state=lambda: (2036, 4),
            step_engines=lambda season: self.calls.append(("engines",)),
            step_articles=lambda season, week=None: self.calls.append(("articles",)),
            run_cmd=lambda *a, **k: self.calls.append(("run_cmd",)) or (0, ""),
        )
        p.start()
        self.addCleanup(p.stop)

    def test_rescan_runs_capture_extract_load_only(self):
        self.assertEqual(wc.run(mode="contracts", teams=["CAR", "NO"]), 0)
        self.assertEqual(self.calls, [("connect",), ("capture", "contracts", ["PANTHERS", "SAINTS"]),
                                      ("extract", "contracts"), ("load", "contracts")])
        self.assertEqual(self.notices[-1][0], "Mu: contracts scan done")

    def test_unknown_abbreviation_stops_before_the_console(self):
        self.assertEqual(wc.run(mode="contracts", teams=["CAR", "XYZ"]), 1)
        self.assertEqual(self.calls, [])
        self.assertIn("XYZ", self.notices[-1][1])
        self.assertIn("--mode contracts", self.notices[-1][1])

    def test_teams_only_for_contracts(self):
        with self.assertRaises(ValueError):
            wc.run(mode="roster", teams=["CAR"])

    def test_resume_detects_contracts_folder(self):
        os.makedirs(os.path.join(self.tmp, "run1", "contracts"))
        open(os.path.join(self.tmp, "run1", ".capture_done"), "w").close()
        self.assertEqual(wc.run(resume="run1"), 0)
        self.assertEqual(self.calls, [("extract", "contracts"), ("load", "contracts")])

    def test_contracts_phase_takes_no_lock_and_skips_done_steps(self):
        folder = os.path.join(self.tmp, "weekly_contracts")
        with mock.patch.object(wc.fcntl, "flock", side_effect=AssertionError("no lock")):
            wc.contracts_phase(folder, os.path.join(self.tmp, "log"), ["TB"])
        self.assertEqual(self.calls, [("capture", "contracts", ["BUCCANEERS"]), ("extract", "contracts"),
                                      ("load", "contracts")])
        self.calls.clear()
        self.status = "loaded"
        self.assertIsNone(wc.contracts_phase(folder, os.path.join(self.tmp, "log"), ["TB"]))
        self.assertEqual(self.calls, [])


class CliTest(unittest.TestCase):
    def test_teams_needs_contracts_mode(self):
        with mock.patch.object(sys, "argv", ["weekly_capture", "--teams", "CAR"]), \
                mock.patch.object(wc, "run") as run:
            with self.assertRaises(SystemExit):
                wc.main()
        run.assert_not_called()

    def test_contracts_cli(self):
        with mock.patch.object(sys, "argv", ["weekly_capture", "--mode", "contracts", "--teams", "CAR,NO"]), \
                mock.patch.object(wc, "run", return_value=0) as run:
            with self.assertRaises(SystemExit):
                wc.main()
        self.assertEqual(run.call_args.kwargs["teams"], ["CAR", "NO"])
        self.assertEqual(run.call_args.kwargs["mode"], "contracts")


if __name__ == "__main__":
    unittest.main()
