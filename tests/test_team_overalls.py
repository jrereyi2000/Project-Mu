"""Tests for Madden Team Overalls and Rankings capture, extraction, and loading."""
import csv
import os
import shutil
import tempfile
import unittest
from unittest import mock

import madden_capture as mc
from pipeline import extract_team_overalls_ocr as ocr_mod
from pipeline import load_capture as lc
from pipeline import weekly_capture as wc


class TestKeycodesAndGrid(unittest.TestCase):
    def test_keycodes_added(self):
        self.assertEqual(mc.KEYCODES["triangle"], 13)
        self.assertEqual(mc.KEYCODES["square"], 0)
        self.assertEqual(mc.KEYCODES["cross"], 1)
        self.assertEqual(mc.KEYCODES["circle"], 2)

    def test_team_grid_dimensions(self):
        self.assertEqual(len(mc.TEAM_GRID), 8)
        for row in mc.TEAM_GRID:
            self.assertEqual(len(row), 4)
        all_teams = [tm for row in mc.TEAM_GRID for tm in row]
        self.assertEqual(len(all_teams), 32)
        self.assertEqual(len(set(all_teams)), 32)
        # Check that all teams in mc.TEAM_GRID are in mc.TEAMS
        for tm in all_teams:
            self.assertIn(tm, mc.TEAMS)

    def test_team_positions_mapping(self):
        self.assertEqual(mc.TEAM_POSITIONS["BEARS"], (0, 0))
        self.assertEqual(mc.TEAM_POSITIONS["BENGALS"], (0, 1))
        self.assertEqual(mc.TEAM_POSITIONS["PANTHERS"], (5, 1))
        self.assertEqual(mc.TEAM_POSITIONS["VIKINGS"], (7, 3))


class TestClassifyScreens(unittest.TestCase):
    def test_classify_hub_options(self):
        class MockFrame:
            text = "GAMEDAY | WEEKLY HUB | OPTIONS | USER SETTINGS | LEAGUE SETTINGS | USER TEAMS"
            items = [("GAMEDAY", (0, 0, 1, 1)), ("OPTIONS", (0, 0, 1, 1))]
            def find(self, s):
                return s in self.text
        f = MockFrame()
        self.assertEqual(mc.classify(f), "HUB_OPTIONS")

    def test_classify_user_teams(self):
        class MockFrame:
            text = "NEW CHARACTER | COMMISSIONER | LEGACY SCORE | ACTIONS | SORT"
            items = [("NEW CHARACTER", (0, 0, 1, 1)), ("COMMISSIONER", (0, 0, 1, 1))]
            def find(self, s):
                return s in self.text
        f = MockFrame()
        self.assertEqual(mc.classify(f), "USER_TEAMS")

    def test_classify_team_overalls(self):
        class MockFrame:
            text = "OFFLINE > NFL PRESEASON ROSTER > CHOOSE TEAM > CUSTOMIZE | 2036 NFL RANKING | PASS RUSH | RANDOM TEAM"
            items = [("RANDOM TEAM", (0, 0, 1, 1)), ("2036 NFL RANKING", (0, 0, 1, 1))]
            def find(self, s):
                return s in self.text
        f = MockFrame()
        self.assertEqual(mc.classify(f), "TEAM_OVERALLS")


class TestCrossOptions(unittest.TestCase):
    def test_cross_hub_options_permitted_on_user_teams(self):
        class MockFrame:
            def highlighted(self, menu):
                return "USER TEAMS"
        with mock.patch.object(mc, "press") as mock_press:
            mc.cross("HUB_OPTIONS", MockFrame())
            mock_press.assert_called_once_with("cross", wait=1.5)

    def test_cross_hub_options_refused_on_other_items(self):
        class MockFrame:
            def highlighted(self, menu):
                return "USER SETTINGS"
        with self.assertRaises(mc.Abort):
            mc.cross("HUB_OPTIONS", MockFrame())


class TestNavigationLogic(unittest.TestCase):
    def test_team_overalls_team_detected(self):
        class MockFrame:
            size = (3024, 1888)
            items = [
                ("CAROLINA", (1621, 355, 1753, 400)),
                ("PANTHERS", (1612, 389, 2091, 529)),
                ("93", (2448, 386, 2522, 452)),
            ]
        f = MockFrame()
        self.assertEqual(mc.team_overalls_team(f), "PANTHERS")

    def test_navigate_to_bears_from_panthers(self):
        class MockFrame:
            size = (3024, 1888)
            items = [("PANTHERS", (1612, 389, 2091, 529))]
        class MockBearsFrame:
            size = (3024, 1888)
            items = [("BEARS", (1612, 389, 2091, 529))]

        frames = [MockFrame(), MockBearsFrame(), MockBearsFrame()]
        presses = []
        with mock.patch.object(mc, "look", side_effect=lambda: (frames.pop(0) if frames else MockBearsFrame(), "TEAM_OVERALLS")), \
             mock.patch.object(mc, "press", side_effect=lambda key, **kw: presses.append(key)), \
             mock.patch.object(mc, "log"):
            mc.navigate_to_bears()

        # Panthers is row 5, col 1:
        # Col 1 -> 1 Left press
        # Row 5 -> 5 Up presses
        self.assertEqual(presses.count("left"), 1)
        self.assertEqual(presses.count("up"), 5)


class TestExtraction(unittest.TestCase):
    def test_extract_panthers_sample(self):
        sample_path = "captures/explore_overalls/04_settled.png"
        if not os.path.exists(sample_path):
            self.skipTest(f"{sample_path} not found")

        data = ocr_mod.extract_team_overalls_frame(sample_path)
        self.assertEqual(data["team"], "PANTHERS")
        self.assertEqual(data["team_ovr"], 93)
        self.assertEqual(data["off_ovr"], 96)
        self.assertEqual(data["def_ovr"], 91)
        self.assertEqual(data["pass_off_rank"], 1)
        self.assertEqual(data["rush_off_rank"], 1)
        self.assertEqual(data["pass_rush_rank"], 1)
        self.assertEqual(data["coverage_rank"], 1)
        self.assertEqual(data["blocking_rank"], 1)
        self.assertEqual(data["team_speed_rank"], 1)

    def test_extract_team_overalls_ocr_writes_csv(self):
        sample_path = "captures/explore_overalls/04_settled.png"
        if not os.path.exists(sample_path):
            self.skipTest(f"{sample_path} not found")

        tmp = tempfile.mkdtemp()
        try:
            folder = os.path.join(tmp, "team_overalls")
            os.makedirs(folder, exist_ok=True)
            shutil.copy(sample_path, os.path.join(folder, "22_panthers.png"))

            rows = ocr_mod.extract_team_overalls_ocr(tmp)
            self.assertEqual(len(rows), 1)
            csv_path = os.path.join(tmp, "team_overalls.csv")
            self.assertTrue(os.path.exists(csv_path))

            with open(csv_path, newline="") as fh:
                reader = csv.DictReader(fh)
                r = next(reader)
                self.assertEqual(r["team"], "PANTHERS")
                self.assertEqual(r["team_ovr"], "93")
                self.assertEqual(r["off_ovr"], "96")
                self.assertEqual(r["def_ovr"], "91")
                self.assertEqual(r["pass_off_rank"], "1")
                self.assertEqual(r["rush_off_rank"], "1")
        finally:
            shutil.rmtree(tmp)


class TestLoadCapture(unittest.TestCase):
    def test_detect_mode_team_overalls(self):
        tmp = tempfile.mkdtemp()
        try:
            open(os.path.join(tmp, "team_overalls.csv"), "w").close()
            self.assertEqual(lc.detect_mode(tmp), "team_overalls")
        finally:
            shutil.rmtree(tmp)

    def test_load_team_overalls(self):
        tmp = tempfile.mkdtemp()
        try:
            csv_path = os.path.join(tmp, "team_overalls.csv")
            with open(csv_path, "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=ocr_mod.OUT_COLS)
                writer.writeheader()
                writer.writerow({
                    "team": "PANTHERS",
                    "team_ovr": "93",
                    "off_ovr": "96",
                    "def_ovr": "91",
                    "pass_off_rank": "1",
                    "rush_off_rank": "1",
                    "pass_rush_rank": "1",
                    "coverage_rank": "1",
                    "blocking_rank": "1",
                    "team_speed_rank": "1",
                    "division": "NFC South",
                    "status": "Championship Contender",
                    "salary_cap": "$161M",
                })

            class MockCur:
                def __init__(self):
                    self.calls = []
                def execute(self, sql, params=None):
                    self.calls.append((" ".join(sql.split()), params))
                def fetchall(self):
                    last_sql = self.calls[-1][0] if self.calls else ""
                    if "id, name, abbreviation FROM teams" in last_sql:
                        return [(22, "Panthers", "CAR")]
                    return []

            cur = MockCur()
            report = {}
            loaded = lc.load_team_overalls(cur, tmp, 2036, report)
            self.assertEqual(loaded, 1)
            self.assertEqual(report.get("team_overalls"), 1)

            insert_calls = [c for c in cur.calls if "INSERT INTO team_seasons" in c[0]]
            self.assertEqual(len(insert_calls), 1)
            sql, params = insert_calls[0]
            # (tid, season, team_ovr, off_ovr, def_ovr, pass_off_rank, rush_off_rank, pass_rush_rank, coverage_rank)
            self.assertEqual(params, (22, 2036, 93, 96, 91, 1, 1, 1, 1))
        finally:
            shutil.rmtree(tmp)


class TestWeeklyCaptureWiring(unittest.TestCase):
    def test_modes_contains_team_overalls(self):
        self.assertIn("team-overalls", wc.MODES)

    def test_folder_mode_team_overalls(self):
        tmp = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(tmp, "team_overalls"))
            self.assertEqual(wc.folder_mode(tmp), "team-overalls")
        finally:
            shutil.rmtree(tmp)

    def test_capture_done_team_overalls(self):
        tmp = tempfile.mkdtemp()
        try:
            self.assertFalse(wc.capture_done(tmp, mode="team-overalls"))
            os.makedirs(os.path.join(tmp, "team_overalls"))
            self.assertTrue(wc.capture_done(tmp, mode="team-overalls"))
        finally:
            shutil.rmtree(tmp)

    def test_extract_done_team_overalls(self):
        tmp = tempfile.mkdtemp()
        try:
            self.assertFalse(wc.extract_done(tmp, mode="team-overalls"))
            open(os.path.join(tmp, "team_overalls.csv"), "w").close()
            self.assertTrue(wc.extract_done(tmp, mode="team-overalls"))
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
