"""Unit tests for the records engine (engines/records_engine.py) and the record book seed. No database required.

Run: python3 -m unittest tests.test_records_engine
"""
import collections
import os
import re
import unittest
from unittest import mock

from engines import records_engine as R

CATS = {
    "season_rec_tds": {"label": "Receiving TDs in a season", "scope": "SEASON", "subject": "PLAYER", "better": "HIGH"},
    "season_team_wins": {"label": "Wins in a season", "scope": "SEASON", "subject": "TEAM", "better": "HIGH"},
    "season_team_points": {"label": "Points scored in a season", "scope": "SEASON", "subject": "TEAM", "better": "HIGH"},
    "season_team_fewest_points_allowed": {"label": "Fewest points allowed", "scope": "SEASON", "subject": "TEAM", "better": "LOW"},
    "game_receptions": {"label": "Receptions in a game", "scope": "GAME", "subject": "PLAYER", "better": "HIGH"},
}


def book(book_rows=(), book_categories=("season_rec_tds",)):
    franchise = collections.defaultdict(list)
    franchise[(11, "season_rec_tds")] = [(16.0, "Muhsin Muhammad", 2004)]
    franchise[(1, "season_rec_tds")] = [(23.0, "Randy Moss", 2007)]
    franchise[(11, "season_team_wins")] = [(15.0, "2015 Carolina Panthers", 2015)]
    franchise[(1, "season_team_wins")] = [(16.0, "2007 New England Patriots", 2007)]
    franchise[(11, "season_team_points")] = [(500.0, "2015 Carolina Panthers", 2015)]
    franchise[(1, "season_team_points")] = [(589.0, "2007 New England Patriots", 2007)]
    franchise[(11, "season_team_fewest_points_allowed")] = [(218.0, "1996 Carolina Panthers", 1996)]
    franchise[(1, "season_team_fewest_points_allowed")] = [(225.0, "2019 New England Patriots", 2019)]
    entries = {
        "season_rec_tds": [(23.0, "Randy Moss", 2007), (22.0, "Jerry Rice", 1987)] + [(18.0, f"P{i}", 2000 + i) for i in range(3)]
                          + [(17.0, f"Q{i}", 1990 + i) for i in range(10)],
        "game_receptions": [(21.0, "Brandon Marshall", 2009), (20.0, "Terrell Owens", 2000)] + [(16.0, f"R{i}", 2010 + i) for i in range(9)],
    }
    return {"categories": CATS, "franchise": franchise, "entries": entries,
            "teams": {11: {"abbr": "CAR", "name": "Carolina Panthers"}, 1: {"abbr": "NE", "name": "New England Patriots"}},
            "book_rows": list(book_rows), "book_entries": [], "book_categories": set(book_categories)}


def run(players=(), teams=None, games=(), book_rows=()):
    with mock.patch.object(R, "load_book", return_value=book(book_rows)), \
            mock.patch.object(R, "season_player_values", return_value=list(players)), \
            mock.patch.object(R, "team_seasons", return_value=teams or {}), \
            mock.patch.object(R, "game_player_values", return_value=list(games)):
        return R.evaluate(None, 2035)


def find(result, **kw):
    return [e for e in result["events"] if all(e.get(k) == v for k, v in kw.items())]


class EvaluateTests(unittest.TestCase):
    def test_season_records_franchise_and_nfl(self):
        r = run(players=[("season_rec_tds", 11, 7, "Trey'Mane Davis", 27.0), ("season_rec_tds", 11, 8, "Isaiah Carson", 18.0)])
        fr = find(r, type="franchise_record")
        self.assertEqual([(e["holder"], e["result"]) for e in fr], [("Trey'Mane Davis", "broke")])   # only the season's best
        self.assertEqual(fr[0]["old_holders"], ["Muhsin Muhammad (2004)"])
        davis = find(r, type="top10", holder="Trey'Mane Davis")[0]
        self.assertEqual((davis["rank"], davis["result"]), (1, "broke"))
        carson = find(r, type="top10", holder="Isaiah Carson")[0]
        self.assertEqual((carson["rank"], carson["result"]), (4, "ranks"))             # behind Davis, Moss and Rice

    def test_ties_and_outside_the_top_10(self):
        r = run(players=[("season_rec_tds", 11, 7, "A", 16.0)])
        self.assertEqual(find(r, type="franchise_record")[0]["result"], "tied")
        self.assertFalse(find(r, type="top10"))                                        # 16 is 16th: not top 10

    def test_team_records_and_full_season_rule(self):
        teams = {11: {"games": 16, "wins": 16, "losses": 0, "ties": 0, "pf": 654, "pa": 216}}
        r = run(teams=teams)
        nfl = {e["category"]: e["result"] for e in find(r, type="nfl_record")}
        self.assertEqual(nfl, {"season_team_wins": "tied", "season_team_points": "broke"})
        self.assertFalse(r["complete"])
        self.assertFalse(find(r, category="season_team_fewest_points_allowed"))       # needs all 17 games
        teams[11]["games"] = 17
        r = run(teams=teams)
        self.assertTrue(r["complete"])
        self.assertEqual(find(r, type="franchise_record", category="season_team_fewest_points_allowed")[0]["result"], "broke")
        self.assertEqual(find(r, type="nfl_record", category="season_team_wins")[0]["holder"], "2035 Carolina Panthers")

    def test_single_games_rank_on_the_all_time_list(self):
        r = run(games=[("game_receptions", 11, 8, "Isaiah Carson", 16.0, 900, 14)])
        e = find(r, type="top10")[0]
        self.assertEqual((e["rank"], e["week"], e["game_id"]), (3, 14, 900))
        self.assertIn("3rd all time", R.format_report(r))

    def test_report(self):
        r = run(players=[("season_rec_tds", 11, 7, "Trey'Mane Davis", 27.0)],
                teams={11: {"games": 16, "wins": 16, "losses": 0, "ties": 0, "pf": 654, "pa": 216}})
        text = R.format_report(r)
        self.assertIn("regular season not finished", text)
        self.assertIn("Receiving TDs in a season: Trey'Mane Davis (Carolina Panthers), 27: broke Randy Moss (2007) (23)", text)
        self.assertIn("Wins in a season: Carolina Panthers (16-0), 16: tied 2007 New England Patriots (16)", text)

    def test_helpers(self):
        self.assertEqual(R.tidy_name("OMAR COOPER JR."), "Omar Cooper Jr.")
        self.assertEqual(R.ordinal(22), "22nd")


class RecordBookTests(unittest.TestCase):
    DAVIS = {"team_id": 11, "category": "season_rec_tds", "value": 27.0, "holder": "Trey'Mane Davis"}

    def test_book_confirms_the_stats(self):
        r = run(players=[("season_rec_tds", 11, 7, "Trey'Mane Davis", 27.0)], book_rows=[self.DAVIS])
        self.assertTrue(find(r, type="franchise_record")[0]["in_book"])
        self.assertEqual((r["book_only"], r["stats_only"]), ([], []))
        self.assertIn("entries and the stats agree", R.format_report(r))

    def test_disagreements(self):
        r = run(players=[("season_rec_tds", 11, 7, "Trey'Mane Davis", 15.0)], book_rows=[self.DAVIS])   # stats fall short
        self.assertEqual([b["holder"] for b in r["book_only"]], ["Trey'Mane Davis"])
        other = {"team_id": 1, "category": "season_team_wins", "value": 16.0, "holder": "x"}
        r = run(players=[("season_rec_tds", 11, 7, "A", 20.0)],
                teams={11: {"games": 17, "wins": 16, "losses": 1, "ties": 0, "pf": 400, "pa": 300}}, book_rows=[other])
        self.assertEqual([e["holder"] for e in r["stats_only"]], ["A"])          # the book tracks receiving TDs, not team wins
        self.assertIn("In the stats, not in the record book", R.format_report(r))

    def test_apply_leaves_book_rows_alone(self):
        r = run(players=[("season_rec_tds", 11, 7, "Trey'Mane Davis", 27.0)], book_rows=[self.DAVIS])
        cur = mock.MagicMock()
        stored = R.apply(cur, 2035, r)
        self.assertEqual(stored["franchise_rows"], 0)
        self.assertEqual(stored["top10_rows"], 1)


class SeedTests(unittest.TestCase):
    PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "db", "migrations", "2026_10_26_nfl_records.sql")

    def test_every_team_has_every_season_record(self):
        sql = open(self.PATH).read()
        rows = re.findall(r"^\s+\('([A-Z]{2,3})', '(season_[a-z_]+)', ", sql, re.M)
        by_team = collections.defaultdict(set)
        for abbr, cat in rows:
            by_team[abbr].add(cat)
        self.assertEqual(len(by_team), 32)
        self.assertTrue(all(len(c) == 11 for c in by_team.values()), {t: len(c) for t, c in by_team.items()})

    def test_league_lists_cover_every_player_category(self):
        sql = open(self.PATH).read()
        cats = set(re.findall(r"^\s+\('((?:season|game)_[a-z_]+)', \d+, '", sql, re.M))
        self.assertEqual(len(cats), 14)


if __name__ == "__main__":
    unittest.main()
