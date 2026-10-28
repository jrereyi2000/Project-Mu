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
        self.assertEqual(ft._group_filter("K"), "K")
        self.assertEqual(ft._group_filter("DEF"), "DEF")
        self.assertEqual(ft._group_filter("DST"), "DEF")
        with self.assertRaises(ValueError):
            ft._group_filter("CB")

    def test_stat_line(self):
        self.assertEqual(ft._stat_line(line(rush_att=22, rush_yds=26, rec=1, rec_yds=6)), "22-26-0 rush, 1-6-0 rec")
        self.assertEqual(ft._stat_line(line(pass_yds=382, pass_td=4)), "382 pass yds, 4 TD, 0 INT")
        self.assertEqual(ft._stat_line(line()), "no stats")


class NflMoveTests(unittest.TestCase):
    """Fantasy context for real NFL moves (team management step 5, phase 6a)."""

    def test_nfl_move_lines(self):
        roster = {1: {"name": "New Guy", "position": "WR", "team": "MIA", "depth": "WR2"},
                  2: {"name": "Old Guy", "position": "HB", "team": "DAL", "depth": "WR9"},
                  3: {"name": "Cut Guy", "position": "WR", "team": "HOU", "depth": "released", "free_agent": True,
                      "released_week": 3},
                  4: {"name": "Cut Nobody", "position": "TE", "team": "HOU", "depth": "released", "free_agent": True,
                      "released_week": 4}}
        moves = [{"type": "SIGNED", "player_id": 1, "position": "WR", "week": 5, "team": "Dolphins"},
                 {"type": "SIGNED", "player_id": 2, "position": "HB", "week": 2, "team": "Cowboys"},      # too old
                 {"type": "SIGNED", "player_id": 9, "position": "LT", "week": 5, "team": "Bears"},        # not skill
                 {"type": "RE_SIGNED", "player_id": 2, "position": "HB", "week": 5, "team": "Cowboys"}]
        out = ft.nfl_move_lines(moves, roster, {1: 12.0, 3: 40.0, 4: 0.0}, 5)
        self.assertEqual(out, [
            "SIGNED · New Guy (ID 1, WR) signed with Dolphins (Week 5): now WR2, rostered 12%",
            "RELEASED · Cut Guy (ID 3, WR) released by HOU (Week 3): no team, projects 0 until he signs; "
            "rostered 40% (drop candidate)"])

    def test_guideline_and_waiver_directive(self):
        from newsroom.fantasy_desk import FANTASY_DESK_GUIDELINES
        self.assertIn("A released player has no game and projects 0 until he signs", FANTASY_DESK_GUIDELINES)
        self.assertIn("never invent a signing", FANTASY_DESK_GUIDELINES)
        import json, os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "newsroom", "config", "article_types.json")) as f:
            cur = json.load(f)["article_types"]["FANTASY_WAIVER_WIRE"]["curatorial_autonomy"]
        self.assertIn("NFL MOVES:", cur)
        self.assertIn("nfl_moves", cur)


class WeekRuleTests(unittest.TestCase):
    def test_projection_weeks_after_publication_are_hidden(self):
        with article_run_context(2035, 10):
            self.assertIsNone(ft._check_forward_week(None, 2035, 10))
            self.assertIn("after this article's publication week", ft._check_forward_week(None, 2035, 11))
            self.assertEqual(ft._publication_week(None, 2035), 10)
        self.assertIsNone(ft._check_forward_week(None, 2035, 18))     # no article: no cutoff

    def test_tools_registered(self):
        names = {t.name for t in ft.FANTASY_TOOLS}
        self.assertEqual(len(names), 11)
        self.assertIn("get_fantasy_injuries", names)
        self.assertTrue(all(n.startswith("get_fantasy_") for n in names))


class DispatcherToolSetTests(unittest.TestCase):
    def test_fantasy_tools_only_for_fantasy_articles(self):
        fake = SimpleNamespace(tools=["base"])
        self.assertEqual(SwarmDispatcher.tools_for(fake, "MACRO_SLATE_RECAP"), ["base"])
        self.assertEqual(SwarmDispatcher.tools_for(fake, None), ["base"])
        self.assertEqual(SwarmDispatcher.tools_for(fake, "FANTASY_START_SIT"), ["base"] + ft.FANTASY_TOOLS)


if __name__ == "__main__":
    unittest.main()
