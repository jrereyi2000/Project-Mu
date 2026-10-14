"""Unit tests for the fantasy writer tools (newsroom/fantasy_tools.py). No database required.

Run: python3 -m unittest tests.test_fantasy_tools
"""
import unittest
from types import SimpleNamespace

from newsroom import fantasy_tools as ft
from newsroom.run_context import article_run_context
from newsroom.swarm_dispatcher import SwarmDispatcher


def line(**kw):
    base = {"pass_yds": 0, "pass_td": 0, "int": 0, "rush_att": 0, "rush_yds": 0, "rush_td": 0, "rec": 0,
            "rec_yds": 0, "rec_td": 0, "fumb": 0}
    return {**base, **kw}


class HelperTests(unittest.TestCase):
    def test_position_filter(self):
        self.assertIsNone(ft._group_filter("ALL"))
        self.assertIsNone(ft._group_filter(""))
        self.assertEqual(ft._group_filter("hb"), "RB")
        self.assertEqual(ft._group_filter("FB"), "RB")
        self.assertEqual(ft._group_filter("TE"), "TE")
        with self.assertRaises(ValueError):
            ft._group_filter("K")

    def test_stat_line(self):
        self.assertEqual(ft._stat_line(line(rush_att=22, rush_yds=26, rec=1, rec_yds=6)), "22-26-0 rush, 1-6-0 rec")
        self.assertEqual(ft._stat_line(line(pass_yds=382, pass_td=4)), "382 pass yds, 4 TD, 0 INT")
        self.assertEqual(ft._stat_line(line()), "no stats")


class WeekRuleTests(unittest.TestCase):
    def test_projection_weeks_after_publication_are_hidden(self):
        with article_run_context(2035, 10):
            self.assertIsNone(ft._check_forward_week(None, 2035, 10))
            self.assertIn("after this article's publication week", ft._check_forward_week(None, 2035, 11))
            self.assertEqual(ft._publication_week(None, 2035), 10)
        self.assertIsNone(ft._check_forward_week(None, 2035, 18))     # no article: no cutoff

    def test_tools_registered(self):
        names = {t.name for t in ft.FANTASY_TOOLS}
        self.assertEqual(len(names), 8)
        self.assertTrue(all(n.startswith("get_fantasy_") for n in names))


class DispatcherToolSetTests(unittest.TestCase):
    def test_fantasy_tools_only_for_fantasy_articles(self):
        fake = SimpleNamespace(tools=["base"])
        self.assertEqual(SwarmDispatcher.tools_for(fake, "MACRO_SLATE_RECAP"), ["base"])
        self.assertEqual(SwarmDispatcher.tools_for(fake, None), ["base"])
        self.assertEqual(SwarmDispatcher.tools_for(fake, "FANTASY_START_SIT"), ["base"] + ft.FANTASY_TOOLS)


if __name__ == "__main__":
    unittest.main()
