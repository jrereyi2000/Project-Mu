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


class MergeWideTest(unittest.TestCase):
    def test_half_visible_column_does_not_shift_values(self):
        # Defense, Oct 14: FMBREC is cut off at the left page's edge ("FMB"), listed on page 0 only
        extract._cache.update({
            "w_000.png": {"title": "", "columns": ["TEAM", "NAME", "TAK", "FMB"], "rows": [["NO", "J.Campbell", "14", ""]]},
            "w_right_000.png": {"title": "", "columns": ["TAK", "FMBREC", "TD"], "rows": [["14", "1", "0"]]},
            "w_001.png": {"title": "", "columns": ["TEAM", "NAME", "TAK"], "rows": [["DET", "O.Aragon", "6"]]},
            "w_right_001.png": {"title": "", "columns": ["TAK", "FMBREC", "TD", "FR"], "rows": [["6", "0", "1", ""]]},
        })
        try:
            cols, rows = extract.merge_wide(["w_000.png", "w_001.png"], ["w_right_000.png", "w_right_001.png"], "t")
        finally:
            for k in ("w_000.png", "w_right_000.png", "w_001.png", "w_right_001.png"):
                extract._cache.pop(k)
        self.assertEqual(cols, ["TEAM", "NAME", "TAK", "FMBREC", "TD"])
        self.assertEqual(rows, [("NO", "J.Campbell", "14", "1", "0"), ("DET", "O.Aragon", "6", "0", "1")])


class MergeWidePrefixTest(unittest.TestCase):
    def test_full_columns_are_not_renamed(self):
        # Season Stats Kicking: FGM / FGA are whole columns, not cut-off FGM29 / FGA29
        extract._cache.update({
            "k_000.png": {"title": "", "columns": ["NAME", "POS", "FGM", "FGA", "XPB"], "rows": [["J.Sierra", "K", "30", "33", "0"]]},
            "k_right_000.png": {"title": "", "columns": ["XPB", "FGM29", "FGA29", "DP"], "rows": [["0", "5", "5", "0"]]},
        })
        try:
            cols, rows = extract.merge_wide(["k_000.png"], ["k_right_000.png"], "t")
        finally:
            for k in ("k_000.png", "k_right_000.png"):
                extract._cache.pop(k)
        self.assertEqual(cols, ["NAME", "POS", "FGM", "FGA", "XPB", "FGM29", "FGA29", "DP"])
        self.assertEqual(rows, [("J.Sierra", "K", "30", "33", "0", "5", "5", "0")])


if __name__ == "__main__":
    unittest.main()
