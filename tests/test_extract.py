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


if __name__ == "__main__":
    unittest.main()
