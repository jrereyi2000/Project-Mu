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


# Apple Vision output for real card crops (captures/2026-10-13_explore, Oct 13)
CRAWFORD = [("GEORGE", (46, 54, 132, 82)), ("CRAWFORD", (42, 86, 362, 156)),
            ("#10 @B I SCRAMBLER I FRANCHISE QB", (48, 166, 485, 187)),
            ("SUPERSTAR X-FACTOR | OMAHA", (222, 238, 563, 268)),
            ("AGE", (42, 312, 90, 342)), ("22", (44, 346, 86, 386)), ("HT", (178, 312, 212, 344)),
            ("6'3\"", (178, 344, 242, 386)), ("WT", (312, 314, 350, 344)), ("229", (310, 346, 371, 386)),
            ("EXP", (443, 312, 489, 342)), ("MORALE", (579, 312, 659, 342)), ("99%", (577, 344, 647, 386))]
HERRERA = [("DIEGO", (44, 54, 112, 86)), ("HERRERA", (44, 83, 294, 157)),
           ("#51C POWER | RISING STAR", (44, 168, 385, 194)), ("STAR", (118, 242, 172, 264)),
           ("AGE", (42, 312, 90, 344)), ("23", (44, 346, 88, 386)), ("HT", (178, 312, 212, 344)),
           ("6'6\"", (178, 340, 248, 386)), ("WT", (310, 312, 352, 344)), ("342", (310, 346, 369, 386)),
           ("EXP", (443, 312, 489, 344)), ("ROOKIE", (441, 346, 527, 386)), ("MORALE", (579, 312, 659, 342))]
# HT label not read; value still found by its column
DAVIS = [("TREY'MANE", (44, 53, 168, 87)), ("DAVIS", (43, 84, 217, 158)),
         ("#6 WR | PHYSICAL | ELITE PLAYER", (44, 168, 423, 194)),
         ("SUPERSTAR X-FACTOR | YAC 'EM UP", (222, 238, 623, 268)), ("AGE", (42, 312, 90, 342)),
         ("24", (44, 346, 86, 386)), ("6'4\"", (178, 344, 242, 386)), ("WT", (312, 314, 350, 344)),
         ("223", (310, 346, 371, 386)), ("EXP", (443, 312, 489, 342)), ("3", (445, 348, 467, 384))]


class CardParseTest(unittest.TestCase):
    def test_crawford(self):
        c = mc.parse_card(CRAWFORD)
        self.assertEqual((c["first"], c["last"], c["number"], c["position"]), ("George", "Crawford", 10, "QB"))
        self.assertEqual((c["archetype"], c["dev"], c["age"], c["height"], c["weight"]),
                         ("Scrambler", "Superstar X-Factor", 22, "6'3\"", 229))
        self.assertIsNone(c["experience"])
        self.assertEqual(mc.card_flags(c), ["missing_experience"])

    def test_rookie_and_glued_number(self):
        c = mc.parse_card(HERRERA)
        self.assertEqual((c["number"], c["position"], c["archetype"], c["dev"]), (51, "C", "Power", "Star"))
        self.assertEqual(c["experience"], 0)
        self.assertEqual(mc.card_flags(c), [])

    def test_value_without_its_label(self):
        c = mc.parse_card(DAVIS)
        self.assertEqual((c["height"], c["experience"], c["role"]), ("6'4\"", 3, "Elite Player"))

    def test_number_zero_read_as_letter_o(self):
        items = [("CHUCK", (44, 54, 120, 84)), ("BYRNE", (45, 84, 225, 158)),
                 ("#O LS | POWER | BORDERLINE STARTER", (42, 162, 482, 194)), ("NORMAL", (118, 241, 210, 265)),
                 ("AGE", (42, 312, 90, 342))]
        c = mc.parse_card(items)
        self.assertEqual((c["first"], c["last"], c["number"], c["position"], c["archetype"]),
                         ("Chuck", "Byrne", 0, "LS", "Power"))

    def test_height_checks(self):
        base = {k: 1 for k in mc.CARD_FIELDS}
        base.update(age=24, weight=200, experience=2)
        for ht, flag in (("6']\"", "bad_height"), ("5'71\"", "bad_height"), ("6'7\"", "suspect_height")):
            self.assertIn(flag, mc.card_flags(dict(base, height=ht)), ht)
        self.assertEqual(mc.card_flags(dict(base, height="6'7\"", weight=320)), [])


if __name__ == "__main__":
    unittest.main()
