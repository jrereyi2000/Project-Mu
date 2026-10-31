"""pipeline/colleges.py: Madden 27's college rows fitted from real players' nflverse colleges. Pure: no database.

Run: python3 -m unittest tests.test_colleges
"""
import collections
import json
import os
import tempfile
import unittest

from pipeline import colleges as cl


def votes(**rows):
    return {int(r[1:]): collections.Counter(v) for r, v in rows.items()}


class SpellingTest(unittest.TestCase):
    def test_madden_board_spellings(self):
        self.assertEqual(cl.madden_name("Mississippi"), "OLE MISS")
        self.assertEqual(cl.madden_name("Louisiana Tech"), "LA. TECH")
        self.assertEqual(cl.madden_name("Western Kentucky"), "W. KENTUCKY")
        self.assertEqual(cl.madden_name("Miami (Ohio)"), "MIAMI OF OHIO")
        self.assertEqual(cl.madden_name("LSU"), "LSU")

    def test_university_noise_dropped(self):
        self.assertEqual(cl.madden_name("Delta State University"), "DELTA STATE")
        self.assertEqual(cl.madden_name("University of South Florida"), "USF")
        self.assertEqual(cl.madden_name("Indiana PA,  University of"), "INDIANA (PA)")
        self.assertEqual(cl.madden_name("Boston College"), "BOSTON COLLEGE")


class FitTest(unittest.TestCase):
    def test_majority_needs_two_votes_and_sixty_percent(self):
        rows = cl.fit_rows(votes(r1886={"LSU": 3, "TULANE": 1}, r1960={"OREGON": 1, "USC": 1},
                                 r1886000={"X": 1}))
        self.assertEqual(rows[1886]["name"], "LSU")
        self.assertEqual((rows[1886]["votes"], rows[1886]["of"]), (3, 4))
        self.assertNotIn(1960, rows)                     # a tie isn't a majority

    def test_single_vote_only_in_alphabetical_order(self):
        base = dict(r1746={"ALABAMA": 2}, r1750={"ALCORN STATE": 2})
        ok = cl.fit_rows(votes(**base, r1747={"ALABAMA A&M": 1}))
        self.assertEqual(ok[1747]["rule"], "single vote in alphabetical order")
        self.assertNotIn(1747, cl.fit_rows(votes(**base, r1747={"ZZ TOP STATE": 1})))
        self.assertNotIn(59084, cl.fit_rows(votes(**base, r59084={"SHEPHERD": 1})))   # past the alphabetical list

    def test_neighbour_rule_needs_both_neighbours_and_one_board_school(self):
        base = votes(r1744={"AIR FORCE": 2}, r1746={"ALABAMA": 2})
        board = ["AIR FORCE", "AKRON", "ALABAMA", "ARMY"]
        rows = cl.fit_rows(base, wanted=[1745], board_schools=board)
        self.assertEqual((rows[1745]["name"], rows[1745]["rule"]), ("AKRON", "only board school between its neighbours"))
        self.assertNotIn(1745, cl.fit_rows(base, wanted=[1745], board_schools=board + ["AKRON STATE"]))
        self.assertNotIn(1745, cl.fit_rows(base, wanted=[], board_schools=board))
        self.assertNotIn(1747, cl.fit_rows(base, wanted=[1747], board_schools=board))

    def test_votes_from_unique_name_matches_only(self):
        snap = {"players": [
            {"first_name": "Joe", "last_name": "Burrow", "status": "Signed", "college": {"table": 16384, "row": 1886}},
            {"first_name": "Josh", "last_name": "Allen", "status": "Signed", "college": {"table": 16384, "row": 2095}},
            {"first_name": "Draft", "last_name": "Pick", "status": "Draft", "college": {"table": 16384, "row": 1886}},
            {"first_name": "No", "last_name": "College", "status": "Signed", "college": None}]}
        nfl = {"JOEBURROW": {"LSU"}, "JOSHALLEN": {"WYOMING", "KENTUCKY"}, "DRAFTPICK": {"TCU"}}
        self.assertEqual(cl.votes(snap, nfl), {1886: collections.Counter({"LSU": 1})})


class LookupTest(unittest.TestCase):
    def test_overrides_win_and_other_tables_are_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "map.json")
            cl.write_map({1886: {"name": "LSU", "votes": 3, "of": 3, "rule": "majority"},
                          2028: {"name": "TENNESSEE TECH", "votes": 1, "of": 1, "rule": "single"}},
                         {"2028": "TENNESSEE STATE"}, path)
            self.assertEqual(cl.college_name({"table": 16384, "row": 1886}, path), "LSU")
            self.assertEqual(cl.college_name({"table": 16384, "row": 2028}, path), "TENNESSEE STATE")
            self.assertIsNone(cl.college_name({"table": 16384, "row": 9}, path))
            self.assertIsNone(cl.college_name({"table": 16436, "row": 1886}, path))
            self.assertIsNone(cl.college_name(None, path))

    def test_committed_map_has_the_anchor_schools(self):
        with open(cl.MAP_PATH) as fh:
            rows = json.load(fh)["rows"]
        for row, name in ((1886, "LSU"), (2034, "TEXAS TECH"), (1960, "OREGON"), (1865, "IOWA STATE"),
                          (2054, "UTAH STATE"), (1959, "OLE MISS")):
            self.assertEqual(rows[str(row)]["name"], name)


if __name__ == "__main__":
    unittest.main()
