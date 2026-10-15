"""Unit tests for the game script engine (PROJECT_SPEC §2.14). No database required.

Run: python3 -m unittest tests.test_game_script_engine
"""
import copy
import re
import unittest

from engines.game_script_engine import (build_script, excitement_quantile, facts_hash, render_script,
                                        script_summary, solve_points)


def player(name, pos="WR", **stats):
    base = {"name": name, "pos": pos, "pass_yards": 0, "pass_tds": 0, "ints": 0, "pass_long": None,
            "rush_yards": 0, "rush_tds": 0, "rush_long": None, "receptions": 0, "rec_yards": 0, "rec_tds": 0,
            "rec_long": None, "fumbles": 0}
    base.update(stats)
    return base


def team(tid, name, abbr, score, offense, quarters=None, defense=None, kicking=None, two_pt=None):
    return {"id": tid, "name": name, "abbr": abbr, "score": score, "quarters": quarters, "two_pt_made": two_pt,
            "fourth_down": None, "offense": offense, "defense": defense, "kicking": kicking, "returns": [],
            "fumbles_lost": None, "total_yards": None, "kicker": f"{abbr} Kicker", "standouts": [], "coach": None}


def captured_ot_game():
    """Colts 15 @ Ravens 21 in overtime (2035 Week 17), with its captured quarters, kicking and defense."""
    colts = team(1, "Colts", "IND", 15, [
        player("Drew Allar", "QB", pass_yards=240, pass_tds=1, pass_long=91),
        player("Cam Cowan", receptions=6, rec_yards=178, rec_tds=1, rec_long=91),
    ], quarters=[3, 0, 0, 12, 0], defense=[{"name": "Mark Kincade", "pos": "REDG", "tackles": 4, "sacks": 1.0}],
        kicking={"name": "Cam Little", "fgm": 3, "fga": 3, "fg_long": 49, "xpm": 0, "xpa": 0})
    ravens = team(2, "Ravens", "BAL", 21, [
        player("Cade Klubnik", "QB", pass_yards=230, pass_tds=2, pass_long=76, rush_yards=41),
        player("Jeremiah Gettis", "HB", rush_yards=75, rush_tds=1, rush_long=15),
        player("Ja'Kobi Lane", receptions=3, rec_yards=44, rec_tds=1, rec_long=26),
        player("Shane Friedman", receptions=1, rec_yards=76, rec_tds=1, rec_long=76),
    ], quarters=[0, 7, 0, 8, 6], defense=[{"name": "Kirk Childress", "pos": "LEDG", "tackles": 11, "sacks": 2.5}],
        kicking={"name": "Tyler Loop", "fgm": 0, "fga": 0, "fg_long": None, "xpm": 1, "xpa": 1})
    return {"game_id": 831, "season": 2035, "week": 17, "game_type": "REGULAR_SEASON", "excitement_index": 66.0,
            "away": colts, "home": ravens, "overtime_known": True, "tie_possible_ot": False}


def weekly_game(gid=900, excitement=60.0):
    """A weekly-capture game: offense lines only, no quarters, kicking or defense."""
    away = team(3, "Bills", "BUF", 24, [
        player("Josh Allen", "QB", pass_yards=280, pass_tds=2, rush_yards=30, rush_tds=1),
        player("Khalil Shakir", receptions=7, rec_yards=90, rec_tds=1),
        player("Dalton Kincaid", "TE", receptions=5, rec_yards=60, rec_tds=1),
    ])
    home = team(4, "Jets", "NYJ", 20, [
        player("Garrett Wilson", receptions=8, rec_yards=110, rec_tds=1),
        player("Justin Fields", "QB", pass_yards=210, pass_tds=1, ints=1, rush_yards=45),
        player("Breece Hall", "HB", rush_yards=80, rush_tds=1),
    ])
    return {"game_id": gid, "season": 2035, "week": 18, "game_type": "REGULAR_SEASON", "excitement_index": excitement,
            "away": away, "home": home, "overtime_known": False, "tie_possible_ot": False}


def points(script, abbr):
    return sum(script["quarter_scores"][abbr])


class SolvePointsTest(unittest.TestCase):
    def test_all_kicks_made_is_cheapest(self):
        self.assertEqual(solve_points(21, 3)[0], {"tds": 3, "xp": 3, "two": 0, "safeties": 0, "fgs": 0,
                                                  "extra_td": 0, "dropped_td": 0})

    def test_recorded_kicking_is_honoured(self):
        plan = solve_points(15, 1, fgm=3, xpm=0)[0]
        self.assertEqual((plan["tds"], plan["xp"], plan["fgs"]), (1, 0, 3))

    def test_field_goals_fill_the_rest(self):
        plan = solve_points(17, 2)[0]
        self.assertEqual((plan["tds"], plan["xp"], plan["fgs"]), (2, 2, 1))


class CapturedGameTest(unittest.TestCase):
    def setUp(self):
        self.game = captured_ot_game()
        self.script = build_script(copy.deepcopy(self.game))

    def test_uses_captured_quarters_and_overtime(self):
        self.assertEqual(self.script["quarter_scores"], {"IND": [3, 0, 0, 12, 0], "BAL": [0, 7, 0, 8, 6]})
        self.assertEqual(self.script["quarter_scores_source"], "captured box score")
        self.assertTrue(self.script["overtime"])
        self.assertEqual(self.script["shape"], "overtime")
        self.assertEqual(self.script["end_of_regulation"], "Tied 15-15")

    def test_walk_off_touchdown_ends_the_game(self):
        last = self.script["scoring"][-1]
        self.assertEqual((last["period"], last["team"]), ("OT", "BAL"))
        self.assertIn("game over, no try", last["play"])
        self.assertIn("lead for good", last["tags"])

    def test_recorded_kicking_and_scorers(self):
        plays = [s["play"] for s in self.script["scoring"]]
        self.assertEqual(sum("Cam Little" in p and "field goal" in p for p in plays), 3)
        self.assertTrue(any("49-yard field goal" in p for p in plays))      # the recorded long
        self.assertEqual(sum("Tyler Loop kick)" in p for p in plays), 1)    # 1/1 extra points
        self.assertEqual(sum("touchdown run" in p and "Jeremiah Gettis" in p for p in plays), 1)
        self.assertTrue(any("Shane Friedman" in p for p in plays))
        self.assertTrue(any("Ja'Kobi Lane" in p for p in plays))

    def test_defense_standouts_use_recorded_lines(self):
        self.assertTrue(self.script["defensive_standouts"]["BAL"]["recorded"])
        self.assertIn("2.5 sacks", self.script["defensive_standouts"]["BAL"]["players"][0])


class WeeklyGameTest(unittest.TestCase):
    def test_scores_add_up_and_every_touchdown_is_credited(self):
        game = weekly_game()
        script = build_script(copy.deepcopy(game))
        self.assertEqual(points(script, "BUF"), 24)
        self.assertEqual(points(script, "NYJ"), 20)
        plays = " | ".join(s["play"] for s in script["scoring"])
        for name, n in (("Khalil Shakir", 1), ("Dalton Kincaid", 1), ("Garrett Wilson", 1), ("Breece Hall", 1)):
            self.assertEqual(plays.count(name), n, name)
        self.assertEqual(len(re.findall(r"Josh Allen \d+-yard touchdown run", plays)), 1)
        self.assertEqual(script["quarter_scores_source"], "generated")
        self.assertFalse(script["defensive_standouts"])                    # no lines and no standouts given

    def test_touchdown_plays_fit_the_players_yards(self):
        for gid in range(900, 930):
            script = build_script(weekly_game(gid))
            for s in script["scoring"]:
                m = re.search(r"(\d+)-yard touchdown pass to Khalil Shakir", s["play"])
                if m:
                    self.assertLessEqual(int(m.group(1)), 90)

    def test_same_game_same_script(self):
        self.assertEqual(build_script(weekly_game()), build_script(weekly_game()))

    def test_clock_runs_down_and_scores_are_spaced(self):
        for gid in range(900, 930):
            script = build_script(weekly_game(gid))
            seen = []
            for s in script["scoring"]:
                m, sec = s["clock"].split(":")
                seen.append((["Q1", "Q2", "Q3", "Q4", "OT"].index(s["period"]), -(int(m) * 60 + int(sec))))
            self.assertEqual(seen, sorted(seen))
            for (p1, c1), (p2, c2) in zip(seen, seen[1:]):
                if p1 == p2:
                    self.assertGreaterEqual(c2 - c1, 40)

    def test_excitement_drives_drama(self):
        calm = [build_script(weekly_game(gid, excitement=36))["lead_changes"] for gid in range(900, 940)]
        wild = [build_script(weekly_game(gid, excitement=84))["lead_changes"] for gid in range(900, 940)]
        self.assertGreater(sum(wild), sum(calm))
        self.assertEqual(excitement_quantile(None), 0.5)


class RenderTest(unittest.TestCase):
    def test_render_and_summary(self):
        script = build_script(captured_ot_game())
        text = render_script(script)
        self.assertIn("Colts 15 @ Ravens 21 (OT)", text)
        self.assertIn("Scoring plays:", text)
        summary = script_summary(script)
        self.assertTrue(summary["overtime"])
        self.assertIn("OT", summary["winning_score"])

    def test_facts_hash_tracks_recorded_facts(self):
        a = weekly_game()
        self.assertEqual(facts_hash(a), facts_hash(weekly_game()))
        c = weekly_game()
        c["home"]["score"] = 21
        self.assertNotEqual(facts_hash(a), facts_hash(c))


if __name__ == "__main__":
    unittest.main()
