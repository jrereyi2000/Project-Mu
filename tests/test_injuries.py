"""Tests for weekly injury pipeline: capture detection, OCR extraction, DB loading, and fantasy projections."""
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from engines import fantasy_projection_engine as FPE
from pipeline import extract_injuries_ocr as EIO
from pipeline import load_capture as LC
from pipeline import weekly_capture as WC


class InjuryOcrHelpersTest(unittest.TestCase):
    def test_parse_duration_and_ir(self):
        # 0 weeks / recovered
        self.assertEqual(EIO.parse_weeks_remaining("0 WEEKS"), 0)
        self.assertEqual(EIO.parse_weeks_remaining("O WEEKS"), 0)
        self.assertEqual(EIO.parse_weeks_remaining("NONE", injury_type="NONE"), 0)

        # Range durations
        self.assertEqual(EIO.parse_weeks_remaining("1-3 WEEKS"), 1)
        self.assertEqual(EIO.parse_weeks_remaining("2- 4 WEEKS"), 2)
        self.assertEqual(EIO.parse_weeks_remaining("3 - 7 WEEKS"), 3)
        self.assertEqual(EIO.parse_weeks_remaining("6 - 10 WEEKS"), 6)

        # Week to week
        self.assertEqual(EIO.parse_weeks_remaining("WEEK TO WEEK"), 1)

    def test_known_positions(self):
        self.assertIn("QB", EIO.KNOWN_POSITIONS)
        self.assertIn("HB", EIO.KNOWN_POSITIONS)
        self.assertIn("LEDG", EIO.KNOWN_POSITIONS)
        self.assertIn("REDG", EIO.KNOWN_POSITIONS)
        self.assertIn("MIKE", EIO.KNOWN_POSITIONS)


class InjuryPipelineModesTest(unittest.TestCase):
    def test_detect_mode_injuries(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "injuries.csv"), "w") as fh:
                fh.write("TEAM,PLAYER_NAME,POSITION,OVR,INJURY_TYPE,DURATION,WEEKS_REMAINING,IS_IR\n")
            self.assertEqual(LC.detect_mode(tmp), "injuries")

    def test_weekly_capture_dirs_contains_injuries(self):
        self.assertIn("injuries", WC.CAPTURE_DIRS)

    def test_extract_done_with_injuries(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Weekly mode without injuries dir
            with open(os.path.join(tmp, "schedule.csv"), "w") as fh:
                fh.write("col\n")
            with open(os.path.join(tmp, "stats_passing.csv"), "w") as fh:
                fh.write("col\n")
            self.assertTrue(WC.extract_done(tmp, mode="weekly"))

            # Weekly mode with injuries dir: requires injuries.csv
            os.makedirs(os.path.join(tmp, "injuries"))
            self.assertFalse(WC.extract_done(tmp, mode="weekly"))
            with open(os.path.join(tmp, "injuries.csv"), "w") as fh:
                fh.write("col\n")
            self.assertTrue(WC.extract_done(tmp, mode="weekly"))


class FantasyProjectionInjuryTest(unittest.TestCase):
    def test_get_player_injury_exact_and_prior(self):
        data = FPE.SeasonData(
            season=2035,
            opponent={(1, 10): 2, (1, 11): 3, (1, 12): 4},
            completed_weeks={1: {1, 2, 3, 4, 5, 6, 7, 8, 9}, 2: set(), 3: set(), 4: set()},
            roster={100: {"name": "Test Star", "team_id": 1, "position": "WR", "group": "WR", "depth": 1}},
            injuries={
                (100, 10): {"weeks_remaining": 3, "is_ir": False},
                (200, 10): {"weeks_remaining": 0, "is_ir": True},
            }
        )
        # Week 10 exact match
        inj10 = FPE.get_player_injury(data, 100, 10)
        self.assertIsNotNone(inj10)
        self.assertEqual(inj10["weeks_remaining"], 3)
        self.assertFalse(inj10["is_ir"])

        # Week 11 prior inference (3 - 1 = 2 weeks remaining)
        inj11 = FPE.get_player_injury(data, 100, 11)
        self.assertIsNotNone(inj11)
        self.assertEqual(inj11["weeks_remaining"], 2)
        self.assertFalse(inj11["is_ir"])

        # Week 13 healed (3 - 3 = 0 weeks remaining)
        inj13 = FPE.get_player_injury(data, 100, 13)
        self.assertIsNone(inj13)

        # Player on IR remains on IR
        ir_rec = FPE.get_player_injury(data, 200, 11)
        self.assertIsNotNone(ir_rec)
        self.assertTrue(ir_rec["is_ir"])

    def test_project_week_zeroes_injured_player(self):
        data = FPE.SeasonData(
            season=2035,
            opponent={(1, 2): 2, (2, 2): 1},
            completed_weeks={1: {1}, 2: {1}},
            roster={
                100: {"name": "Healthy WR", "team_id": 1, "position": "WR", "group": "WR", "depth": 1},
                101: {"name": "Injured WR", "team_id": 1, "position": "WR", "group": "WR", "depth": 2},
            },
            points={100: {1: 15.0}, 101: {1: 12.0}},
            injuries={
                (101, 2): {"weeks_remaining": 2, "is_ir": True},
            }
        )
        rows = FPE.project_week(data, 2)
        by_pid = {r["player_id"]: r for r in rows}

        self.assertGreater(by_pid[100]["projected_points"], 0.0)
        self.assertEqual(by_pid[100]["pos_rank"], "WR1")

        # Injured player projected 0.0 points and no pos_rank
        self.assertEqual(by_pid[101]["projected_points"], 0.0)
        self.assertIsNone(by_pid[101].get("pos_rank"))
        self.assertTrue(by_pid[101]["is_out"])


class LoadInjuriesDbTest(unittest.TestCase):
    def test_load_injuries_matching(self):
        with tempfile.TemporaryDirectory() as tmp:
            csv_path = os.path.join(tmp, "injuries.csv")
            with open(csv_path, "w") as fh:
                fh.write(
                    "TEAM,PLAYER_NAME,POSITION,OVR,INJURY_TYPE,DURATION,WEEKS_REMAINING,IS_IR\n"
                    "CHIEFS,P.Mahomes,QB,99,HIGH ANKLE SPRAIN,WEEK TO WEEK,1,FALSE\n"
                    "CHIEFS,T.Kelce,TE,95,BROKEN COLLARBONE,4 - 8 WEEKS,4,TRUE\n"
                )

            cur = MagicMock()
            # Return roster rows: (player_id, team_id, player_name, full_name, position, overall_rating)
            cur.fetchall.return_value = [
                (10, 1, "P.Mahomes", "Patrick Mahomes", "QB", 99),
                (11, 1, "T.Kelce", "Travis Kelce", "TE", 95),
            ]

            by_name = {"CHIEFS": 1}
            problems = []
            count = LC.load_injuries(cur, tmp, 2035, 10, by_name, problems)

            self.assertEqual(count, 2)
            self.assertEqual(problems, [])
            self.assertEqual(cur.execute.call_count, 3)  # 1 SELECT + 2 INSERTs

