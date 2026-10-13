"""Console-free checks of madden_capture.py (key map, week labels)."""
import sys
import unittest

if sys.platform != "darwin":
    raise unittest.SkipTest("madden_capture.py needs macOS (Quartz, Vision)")

import madden_capture as mc


class KeyMapTest(unittest.TestCase):
    def test_chiaki_keyboard_map(self):
        # chiaki-ng "Keyboard as controller": Cross=S Circle=D L1=2 R1=3 L2=1 R2=4, Esc
        self.assertEqual(mc.KEYCODES["cross"], 1)
        self.assertEqual(mc.KEYCODES["circle"], 2)
        self.assertEqual(mc.KEYCODES["escape"], 53)


class WeekLabelTest(unittest.TestCase):
    def test_clean_week_fixes_ocr_digits(self):
        self.assertEqual(mc.clean_week("SEASON WEEKT"), "SEASON WEEK 1")
        self.assertEqual(mc.clean_week("season week 1O"), "SEASON WEEK 10")
        self.assertEqual(mc.clean_week("POST SEASON WILDCARD"), "POST SEASON WILD CARD")

    def test_week_order(self):
        self.assertLess(mc.week_index("PRESEASON WEEK 3"), mc.week_index("SEASON WEEK 1"))
        self.assertEqual(mc.week_index("SEASON WEEK 18"), 18)
        self.assertEqual(mc.week_index("POST SEASON SUPER BOWL"), 22)

    def test_previous_week(self):
        self.assertEqual(mc.previous_week("SEASON WEEK 9"), "SEASON WEEK 8")
        self.assertEqual(mc.previous_week("POST SEASON WILD CARD"), "SEASON WEEK 18")
        with self.assertRaises(mc.Abort):
            mc.previous_week("SEASON WEEK 1")


class _Frame:
    """classify() only needs .text and .find(); boxes are irrelevant here."""
    def __init__(self, *labels):
        self.items = [(t, (0, 0, 1, 1)) for t in labels]
        self.text = " | ".join(labels)

    find = mc.Frame.find


class ClassifyTest(unittest.TestCase):
    def test_roster_list_is_not_player_stats(self):
        # The roster list also shows PLAYER NAME (sort chip) and VIEW PLAYER CARD.
        f = _Frame("PANTHERS", "ROSTER", "NAME", "PLAYER NAME", "MOR", "SAVINGS", "PENALTY", "VIEW PLAYER CARD")
        self.assertEqual(mc.classify(f), "ROSTER_LIST")

    def test_roster_list_while_button_bar_redraws(self):
        f = _Frame("PANTHERS", "ROSTER", "NAME", "PLAYER NAME", "MOR", "SAVINGS", "PENALTY")
        self.assertEqual(mc.classify(f), "ROSTER_LIST")

    def test_player_stats_still_recognised(self):
        f = _Frame("NFL", "R2", "PASSING", "PLAYER NAME", "VIEW PLAYER CARD", "YARDS")
        self.assertEqual(mc.classify(f), "PLAYER_STATS")

    def test_manage_roster_hub(self):
        f = _Frame("GAMEDAY", "WEEKLY HUB", "MANAGE ROSTER", "LEAGUE", "COACH CENTRAL", "UPGRADE PLAYERS",
                   "ADJUST LINEUP", "ROSTER", "DEPTH CHART")
        self.assertEqual(mc.classify(f), "HUB_ROSTER")


if __name__ == "__main__":
    unittest.main()
