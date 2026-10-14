"""Console- and network-free checks of extract.py's roster helpers."""
import os
import unittest

import extract


class CardValueTest(unittest.TestCase):
    def test_rookie_is_zero_experience(self):
        self.assertEqual(extract._card_value("experience", "ROOKIE"), "0")
        self.assertEqual(extract._card_value("experience", " 9 "), "9")

    def test_curly_quotes_in_heights(self):
        self.assertEqual(extract._card_value("height", "6’1”"), "6'1\"")
        self.assertEqual(extract._card_value("height", "5'11''"), "5'11\"")


class RosterPathTest(unittest.TestCase):
    def test_roster_list_pages_use_the_roster_crop(self):
        self.assertTrue(extract._is_roster(os.path.join("captures", "x", "roster", "01_panthers", "list_01.png")))
        self.assertFalse(extract._is_roster(os.path.join("captures", "x", "stats", "01_passing_000.png")))


class MergeTest(unittest.TestCase):
    def test_roster_rows_deduplicated_on_name_and_pos(self):
        cols = ["NAME", "POS", "AGE", "OVR"]
        extract._cache.update({
            "p1.png": {"title": "", "columns": cols, "rows": [["B.McGrath", "QB", "24", "80"], ["T.Ramos", "LEDG", "26", "79"]]},
            "p2.png": {"title": "", "columns": cols, "rows": [["T.Ramos", "LEDG", "26", "78"], ["V.Amaya", "FS", "25", "77"]]},
        })
        try:
            columns, rows = extract.merge(["p1.png", "p2.png"], "test", key_cols=["NAME", "POS"])
        finally:
            for k in ("p1.png", "p2.png"):
                extract._cache.pop(k)
        self.assertEqual([r[0] for r in rows], ["B.McGrath", "T.Ramos", "V.Amaya"])
        self.assertEqual(rows[1][3], "79")          # first copy kept


if __name__ == "__main__":
    unittest.main()
