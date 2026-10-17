import os
import shutil
import tempfile
import unittest
from unittest.mock import patch

from pipeline import extract_draft_results as E


class TestExtractDraftResults(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_close_matching(self):
        self.assertTrue(E._close("JDENNEV", "JDENNEY"))
        self.assertTrue(E._close("IWAVNE", "IWAYNE"))
        self.assertTrue(E._close("HHODAINS", "HHODGINS"))
        self.assertTrue(E._close("D.VOGEL", "DVOGEL"))
        self.assertFalse(E._close("VOGEL", "CAPPS"))

    def test_stitch_pages_clean_overlap(self):
        page0 = [
            {"name": "D.VOGEL", "position": "LEDG", "round": "1", "pick": "1", "ovr": "75"},
            {"name": "Z.CAPPS", "position": "REDG", "round": "1", "pick": "2", "ovr": "73"},
            {"name": "C.REYNOLDS", "position": "REDG", "round": "1", "pick": "3", "ovr": "75"},
        ]
        page1 = [
            {"name": "Z.CAPPS", "position": "REDG", "round": "1", "pick": "2", "ovr": "73"},
            {"name": "C.REYNOLDS", "position": "REDG", "round": "1", "pick": "3", "ovr": "75"},
            {"name": "C.BUCHANAN", "position": "SAM", "round": "1", "pick": "4", "ovr": "78"},
        ]
        stitched = E.stitch_pages([page0, page1])
        self.assertEqual(len(stitched), 4)
        self.assertEqual([r["name"] for r in stitched], ["D.VOGEL", "Z.CAPPS", "C.REYNOLDS", "C.BUCHANAN"])

    def test_stitch_pages_ocr_slip_overlap(self):
        page0 = [
            {"name": "B.LONG", "position": "CB"},
            {"name": "M.HIGGINS", "position": "WR"},
            {"name": "J.DENNEV", "position": "QB"},
        ]
        page1 = [
            {"name": "J.DENNEY", "position": "QB"},
            {"name": "J.PEARSON", "position": "HB"},
        ]
        stitched = E.stitch_pages([page0, page1])
        self.assertEqual(len(stitched), 4)
        self.assertEqual([r["name"] for r in stitched], ["B.LONG", "M.HIGGINS", "J.DENNEV", "J.PEARSON"])

    def test_extract_draft_results_already_exists(self):
        csv_file = os.path.join(self.tmp, "draft_results.csv")
        json_file = os.path.join(self.tmp, "draft_results.json")
        with open(csv_file, "w") as fh:
            fh.write("overall_pick,player_name\n1,D.VOGEL\n")
        with open(json_file, "w") as fh:
            fh.write("[]")

        res = E.extract_draft_results(self.tmp)
        self.assertEqual(res, csv_file)

    def test_find_frames_empty(self):
        self.assertEqual(E.find_draft_recap_frames(self.tmp), [])
