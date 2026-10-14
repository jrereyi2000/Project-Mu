"""Formatting of the defense and special-teams lines the writer tools return. Imports newsroom.db_tools, but
these helpers never touch the database.

Run: python3 -m unittest tests.test_defense_tools
"""
import unittest
from decimal import Decimal

from newsroom.db_tools import LEADER_CATEGORIES, defense_line, kicking_line


class DefenseLineTests(unittest.TestCase):
    def test_half_sacks_and_zero_categories(self):
        self.assertEqual(defense_line(68, Decimal("14.5"), 11, 0, 0, 0, 0, 0),
                         "68 tkl, 14.5 sacks, 11 TFL")
        self.assertEqual(defense_line(86, Decimal("1.0"), 4, 2, 3, 1, 0, 0),
                         "86 tkl, 1 sack, 4 TFL, 2 INT, 3 PD, 1 FF")

    def test_kicking_line(self):
        self.assertEqual(kicking_line(33, 35, 56, 21, 22, 8, 8), "33/35 FG (long 56), 8/8 from 50+, 21/22 XP")
        self.assertEqual(kicking_line(1, 1, 38, 3, 3), "1/1 FG (long 38), 3/3 XP")

    def test_leader_categories_cover_all_pro_slots(self):
        for cat in ("sacks", "interceptions", "tackles", "tackles_for_loss", "takeaways",
                    "field_goals", "punting", "kick_returns", "punt_returns"):
            self.assertIn(cat, LEADER_CATEGORIES)


if __name__ == "__main__":
    unittest.main()
