"""pipeline/colleges.py: Madden 27's college list (Pipeline's lookup) and its cross-check against saves. Pure: no
database.

Run: python3 -m unittest tests.test_colleges
"""
import collections
import json
import os
import tempfile
import unittest

from pipeline import colleges as cl


class ListTest(unittest.TestCase):
    def test_committed_list_names_the_anchor_schools(self):
        # real players in CAREER-WEEK1: Burrow, Mahomes, Herbert, Purdy, Love; prospects' rows the vote couldn't name
        for row, name in ((1886, "LSU"), (2034, "TEXAS TECH"), (1960, "OREGON"), (1865, "IOWA STATE"),
                          (2054, "UTAH STATE"), (1959, "OLE MISS"), (2028, "TENNESSEE STATE"), (1882, "LEHIGH")):
            self.assertEqual(cl.college_name({"table": 16384, "row": row}), name)

    def test_list_is_pipelines_with_credit(self):
        doc = cl.load_list()
        self.assertEqual(len(doc["colleges"]), 488)
        self.assertIn("ChanceDaWrapper", doc["_license"])
        self.assertIn("Pipeline 0.5.0", doc["_source"])

    def test_unknown_or_unset_reference_is_none(self):
        self.assertIsNone(cl.college_name(None))
        self.assertIsNone(cl.college_name({"table": 16384, "row": 999999}))

    def test_overrides_win(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "list.json")
            with open(path, "w") as fh:
                json.dump({"colleges": [{"name": "Tennessee Tech", "table": 16384, "row": 2028}],
                           "overrides": {"16384:2028": "Tennessee State", "16473:71035": "Albany State"}}, fh)
            self.assertEqual(cl.college_name({"table": 16384, "row": 2028}, path), "TENNESSEE STATE")
            self.assertEqual(cl.college_name({"table": 16473, "row": 71035}, path), "ALBANY STATE")


class SpellingTest(unittest.TestCase):
    def test_nflverse_to_madden(self):
        self.assertEqual(cl.madden_name("Mississippi"), "OLE MISS")
        self.assertEqual(cl.madden_name("Louisiana Tech"), "L.A. TECH")
        self.assertEqual(cl.madden_name("Western Kentucky"), "W. KENTUCKY")
        self.assertEqual(cl.madden_name("Delta State University"), "DELTA STATE")
        self.assertEqual(cl.madden_name("Indiana PA,  University of"), "IUP")
        self.assertEqual(cl.madden_name("Boston College"), "BOSTON COLLEGE")

    def test_same_school(self):
        self.assertTrue(cl.same_school("ELON", "Elon University"))
        self.assertTrue(cl.same_school("FORT VALLEY STATE COLLEGE", "Fort Valley State"))
        self.assertTrue(cl.same_school("ST. JOHN'S (MN)", "St. John's"))
        self.assertTrue(cl.same_school("SAM HOUSTON STATE", "Sam Houston"))
        self.assertFalse(cl.same_school("TEXAS A&M", "Texas"))


class CheckTest(unittest.TestCase):
    def test_votes_from_unique_name_matches_only(self):
        snap = {"players": [
            {"first_name": "Joe", "last_name": "Burrow", "status": "Signed", "college": {"table": 16384, "row": 1886}},
            {"first_name": "Josh", "last_name": "Allen", "status": "Signed", "college": {"table": 16384, "row": 2095}},
            {"first_name": "Draft", "last_name": "Pick", "status": "Draft", "college": {"table": 16384, "row": 1886}},
            {"first_name": "No", "last_name": "College", "status": "Signed", "college": None}]}
        nfl = {"JOEBURROW": {"LSU"}, "JOSHALLEN": {"WYOMING", "KENTUCKY"}, "DRAFTPICK": {"TCU"}}
        self.assertEqual(cl.votes(snap, nfl), {(16384, 1886): collections.Counter({"LSU": 1})})

    def test_check_counts_only_confident_references(self):
        names = {(16384, 1886): "LSU", (16384, 1960): "Oregon"}
        rep = cl.check({(16384, 1886): collections.Counter({"LSU": 3, "TULANE": 1}),
                        (16384, 1960): collections.Counter({"USC": 2}),
                        (16473, 9): collections.Counter({"ALBANY STATE": 2}),
                        (16384, 2034): collections.Counter({"TEXAS TECH": 1})}, names)
        self.assertEqual((rep["checked"], rep["agree"]), (3, 1))
        self.assertEqual(rep["differ"], [((16384, 1960), "USC", "Oregon")])
        self.assertEqual(rep["missing"], [((16473, 9), "ALBANY STATE")])


if __name__ == "__main__":
    unittest.main()
