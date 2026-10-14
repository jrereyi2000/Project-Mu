"""pipeline/trigger.py: hub label parsing, the fire timing, and when a hub week is due."""
import unittest

from pipeline.trigger import Trigger, due, parse_hub_label


class LabelTest(unittest.TestCase):
    def test_regular_and_postseason(self):
        self.assertEqual(parse_hub_label("2026: WEEK 2"), (2026, 2))
        self.assertEqual(parse_hub_label("2035:WEEK18 @ BUCCANEERS (0-1-0)"), (2035, 18))
        self.assertEqual(parse_hub_label("2035: wild card"), (2035, 19))
        self.assertEqual(parse_hub_label("2035: SUPER BOWL"), (2035, 23))

    def test_unreadable_preseason_and_pro_bowl(self):
        for text in ("", "WEEK 2", "2035: PRESEASON WEEK 1", "2035: PRO BOWL", "@ BUCCANEERS"):
            self.assertIsNone(parse_hub_label(text), text)


class TimingTest(unittest.TestCase):
    def test_fires_after_advance_week_and_30s_on_the_hub(self):
        t = Trigger(fire_after=30)
        self.assertFalse(t.update("MAIN_MENU", 0))              # not armed
        self.assertFalse(t.update("ADVANCE_WEEK", 5))
        self.assertFalse(t.update("MAIN_MENU", 10))
        self.assertFalse(t.update("MAIN_MENU", 39))
        self.assertTrue(t.update("MAIN_MENU", 40))
        t.fired()
        self.assertFalse(t.update("MAIN_MENU", 100))             # fires once

    def test_leaving_the_hub_restarts_the_count(self):
        t = Trigger(fire_after=30, miss_frames=3)
        t.update("ADVANCE_WEEK", 0)
        t.update("MAIN_MENU", 1)
        t.update("UNKNOWN", 2)                                   # a flicker or two doesn't reset
        t.update("UNKNOWN", 3)
        self.assertTrue(t.update("MAIN_MENU", 31))
        t = Trigger(fire_after=30, miss_frames=3)
        t.update("ADVANCE_WEEK", 0)
        t.update("MAIN_MENU", 1)
        for i in range(3):
            t.update("UNKNOWN", 2 + i)                           # JR went into a menu
        self.assertFalse(t.update("MAIN_MENU", 31))
        self.assertTrue(t.update("MAIN_MENU", 61))

    def test_fallback_fires_without_advance_week(self):
        t = Trigger(fire_after=30)
        t.update("MAIN_MENU", 0)
        t.fallback = True
        self.assertTrue(t.update("MAIN_MENU", 30))


class DueTest(unittest.TestCase):
    def test_due_only_for_a_new_week_in_the_current_season(self):
        league = (2035, 18)
        self.assertTrue(due((2035, 19), league, {})[0])
        self.assertFalse(due((2035, 18), league, {})[0])        # already loaded
        self.assertFalse(due((2036, 1), league, {})[0])         # new season: set by hand
        self.assertFalse(due(None, league, {})[0])
        self.assertFalse(due((2035, 19), league, {"attempted": {"season": 2035, "week": 19}})[0])
        self.assertTrue(due((2035, 20), league, {"attempted": {"season": 2035, "week": 19}})[0])


if __name__ == "__main__":
    unittest.main()
