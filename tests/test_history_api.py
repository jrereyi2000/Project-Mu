"""server/history_api.py: the #records Record Book and #awards endpoints, and the player page's awards list. The ranking, badge and awards shaping tests
need no database; the endpoint tests only read the live one (skipped when Postgres is down) and write nothing.

Run this file only (the Napper tests write to the live database): python3 -m unittest tests.test_history_api"""
import unittest

import psycopg
from psycopg.rows import dict_row
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server import history_api as H


def mark(value, holder="A Player", season=2010, source="NFL", **kw):
    m = {"value": value, "holder": holder, "player_id": None, "team": "CAR", "season": season, "game_date": None,
         "source": source, "mu": H.is_mu(source, season), "in_progress": False}
    m.update(kw)
    return m


def cat(**kw):
    c = {"category": "season_pass_tds", "label": "Passing TDs in a season", "scope": "SEASON", "subject": "PLAYER",
         "better": "HIGH", "sort_order": 2, "coverage": "FULL", "season_type": "REGULAR"}
    c.update(kw)
    c["group"] = H.category_group(c)
    return c


class RankingTest(unittest.TestCase):
    def test_ties_share_a_rank(self):
        top = H.ranked([mark(7, "A"), mark(6, "B"), mark(7, "C"), mark(5, "D")], low=False)
        self.assertEqual([(m["holder"], m["rank"]) for m in top], [("A", 1), ("C", 1), ("B", 3), ("D", 4)])

    def test_low_is_better_for_fewest_points(self):
        top = H.ranked([mark(200), mark(165), mark(180)], low=True)
        self.assertEqual([m["value"] for m in top], [165, 180, 200])

    def test_cut_at_ten_keeps_ties_at_ten(self):
        marks = [mark(v) for v in range(20, 11, -1)] + [mark(11, "X"), mark(11, "Y"), mark(10)]
        top = H.ranked(marks, low=False)
        self.assertEqual(len(top), 11)
        self.assertTrue(all(m["rank"] <= 10 for m in top))

    def test_dedupe_same_season_on_both_lists(self):
        self.assertEqual(len(H.dedupe([mark(67, "G Crawford", 2035, "BOOK"), mark(67, "g crawford", 2035, "LEAGUE")])), 1)


class BadgeTest(unittest.TestCase):
    def test_mu_marks(self):
        self.assertTrue(H.is_mu("BOOK", 2029))
        self.assertTrue(H.is_mu("LEAGUE", 2036))
        self.assertFalse(H.is_mu("NFL", 2025))
        self.assertFalse(H.is_mu("BOOK", 2025))

    def test_in_progress_only_for_current_league_season(self):
        live = {"season": 2036, "week": 18, "in_progress": True}
        self.assertTrue(H.mark_in_progress("LEAGUE", 2036, live))
        self.assertFalse(H.mark_in_progress("LEAGUE", 2035, live))
        self.assertFalse(H.mark_in_progress("BOOK", 2036, live))
        self.assertFalse(H.mark_in_progress("LEAGUE", 2036, dict(live, in_progress=False)))

    def test_groups(self):
        self.assertEqual(cat()["group"], "SEASON")
        self.assertEqual(cat(category="rookie_game_pass_tds", scope="GAME")["group"], "ROOKIE")
        self.assertEqual(cat(category="super_bowl_pass_tds", scope="GAME", season_type="POSTSEASON")["group"], "SUPER_BOWL")
        self.assertEqual(cat(category="career_super_bowl_wins_coach", scope="CAREER", season_type="POSTSEASON")["group"],
                         "SUPER_BOWL")
        self.assertEqual(cat(category="career_postseason_pass_tds", scope="CAREER", season_type="POSTSEASON")["group"],
                         "CAREER")


class CardTest(unittest.TestCase):
    def test_previous_nfl_record_when_broken(self):
        top = H.ranked([mark(67, "Crawford", 2035, "BOOK"), mark(55, "Manning", 2013), mark(50, "Brady", 2007)], False)
        prev = H.previous_nfl_record(top, low=False)
        self.assertEqual((prev["holder"], prev["value"]), ("Manning", 55))
        self.assertEqual(H.record_result([top[0]], top, low=False), "broke")

    def test_tie_is_not_a_previous_record(self):
        top = H.ranked([mark(7, "Crawford", 2035, "BOOK"), mark(7, "Brees", 2015)], False)
        self.assertIsNone(H.previous_nfl_record(top, low=False))
        self.assertEqual(H.record_result([m for m in top if m["rank"] == 1], top, low=False), "tied")

    def test_nfl_record_has_no_result(self):
        top = H.ranked([mark(55, "Manning", 2013), mark(67, "Crawford", 2035, "BOOK")], False)
        self.assertIsNone(H.record_result([mark(55, "Manning", 2013)], top, low=False))

    def test_franchise_basis_without_a_list(self):
        c = H.build_category(cat(category="season_team_wins", subject="TEAM"), [],
                             [mark(17, "2035 Panthers", 2035, "LEAGUE"), mark(16, "2007 Patriots", 2007)], [], "all")
        self.assertEqual(c["basis"], "franchise")
        self.assertEqual(c["record"][0]["holder"], "2035 Panthers")
        self.assertEqual(c["result"], "broke")

    def test_mu_era_ranks_league_marks_from_both_lists(self):
        c = H.build_category(cat(), [mark(55, "Manning", 2013), mark(67, "Crawford", 2035, "BOOK")],
                             [mark(48, "Young", 2031, "BOOK"), mark(67, "Crawford", 2035, "LEAGUE")], [], "mu")
        self.assertEqual([(m["holder"], m["rank"]) for m in c["top10"]], [("Crawford", 1), ("Young", 2)])
        self.assertIsNone(c["previous_nfl_record"])

    def test_empty_category(self):
        self.assertIsNone(H.build_category(cat(), [], [], [], "mu"))
        self.assertEqual(H.build_category(cat(), [], [], [], "all")["record"], [])

    def test_num(self):
        self.assertEqual(H.num(30.5), 30.5)
        self.assertIsInstance(H.num(7.0), int)


def award(award_type, season, name, player_id=None, team="CAR", coach_id=None):
    return {"award_type": award_type, "season": season, "name": name, "player_id": player_id, "coach_id": coach_id,
            "position": None, "team": team}


class AwardsTest(unittest.TestCase):
    def test_super_bowl_from_the_winners_side(self):
        g = {"id": 9, "home_team_id": 31, "away_team_id": 11, "home_score": 6, "away_score": 59}
        sb = H.super_bowl(g, {31: "DEN", 11: "CAR"})
        self.assertEqual((sb["team"], sb["score"], sb["opponent"], sb["opponent_score"]), ("CAR", 59, "DEN", 6))
        self.assertIsNone(H.super_bowl(dict(g, home_score=None), {}))

    def test_coty_row_is_a_coach(self):
        a = H.shape_award({"award_type": "COTY", "season": 2035, "player_id": None, "player_name": None,
                           "position": None, "team_id": 11, "coach_id": 2, "coach_name": "Marcus Whitfield"}, {11: "CAR"})
        self.assertEqual((a["name"], a["coach_id"], a["player_id"], a["team"]), ("Marcus Whitfield", 2, None, "CAR"))

    def test_leaders_count_repeat_winners(self):
        rows = [award("MVP", 2034, "George Crawford", 325), award("MVP", 2035, "George Crawford", 325),
                award("OPOY", 2026, "CeeDee Lamb", team="DAL"), award("OPOY", 2027, "CeeDee Lamb", team="DAL"),
                award("DPOY", 2028, "Arvell Reese", 121, "NYG"), award("DPOY", 2029, "Arvell Reese", 121, "NYG"),
                award("DPOY", 2030, "Arvell Reese", 121, "NYG"), award("MVP", 2033, "Drake Maye", 906)]
        leaders = H.award_leaders(rows)
        self.assertEqual([(l["name"], l["award_type"], l["count"]) for l in leaders],
                         [("Arvell Reese", "DPOY", 3), ("George Crawford", "MVP", 2), ("CeeDee Lamb", "OPOY", 2)])
        self.assertEqual(leaders[1]["seasons"], [2034, 2035])

    def test_franchise_counts(self):
        seasons = [{"champion": {"team": "CAR"}, "awards": {"MVP": award("MVP", 2035, "G"), "COTY": None}},
                   {"champion": {"team": "BAL"}, "awards": {"MVP": award("MVP", 2033, "D", team="NE")}},
                   {"champion": None, "awards": {"MVP": award("MVP", 2036, "G")}}]
        self.assertEqual(H.franchise_counts(seasons), [{"team": "CAR", "titles": 1, "awards": 2},
                                                       {"team": "BAL", "titles": 1, "awards": 0},
                                                       {"team": "NE", "titles": 0, "awards": 1}])

    def test_all_pro_slot_order(self):
        rows = [{"id": 3, "position": "K", "player_name": "K", "player_id": 1, "team": "DAL"},
                {"id": 2, "position": "WR", "player_name": "W2", "player_id": 2, "team": "CAR"},
                {"id": 1, "position": "WR", "player_name": "W1", "player_id": 3, "team": "CAR"},
                {"id": 4, "position": "EDGE", "player_name": "E", "player_id": 4, "team": "MIA"},
                {"id": 5, "position": "QB", "player_name": "Q", "player_id": 5, "team": "CAR"}]
        team = H.all_pro_team(rows)
        self.assertEqual([(r["slot"], r["name"]) for r in team], [("QB", "Q"), ("WR", "W1"), ("WR", "W2"), ("EDGE", "E"), ("K", "K")])
        self.assertEqual([r["unit"] for r in team], ["OFFENSE", "OFFENSE", "OFFENSE", "DEFENSE", "SPECIAL_TEAMS"])


import pytest


class WatchTest(unittest.TestCase):
    CAT = {"category": "season_pass_yards", "label": "Passing yards in a season", "better": "HIGH"}
    BOOK = [(5477.0, "Peyton Manning", 2013), (5497.0, "George Crawford", 2035), (5497.0, "Matthew Stafford", 2021)]

    def test_leader_against_the_record(self):
        row = H.watch_category(self.CAT, self.BOOK, [(1, 10, "A", 3000.0), (2, 11, "B", 2500.0)], {1: 9, 2: 9}, {1: "CAR", 2: "DEN"})
        self.assertEqual(row["value"], 3000)
        self.assertEqual(row["leaders"], [{"holder": "A", "player_id": 10, "team": "CAR", "games": 9}])
        self.assertEqual(row["record"]["value"], 5497)
        self.assertEqual([h["holder"] for h in row["record"]["holders"]], ["George Crawford", "Matthew Stafford"])
        self.assertEqual(row["pct"], 54.6)
        self.assertEqual(row["pace_17"], 5667)
        self.assertTrue(row["on_pace"])
        self.assertEqual(row["rank"], 4)

    def test_tied_leaders_and_off_pace(self):
        row = H.watch_category(self.CAT, self.BOOK, [(2, 11, "B", 2000.0), (1, 10, "A", 2000.0)], {1: 9, 2: 10}, {1: "CAR", 2: "DEN"})
        self.assertEqual([l["holder"] for l in row["leaders"]], ["A", "B"])
        self.assertEqual(row["pace_17"], 3400)       # the leader with the most games sets the pace
        self.assertFalse(row["on_pace"])

    def test_longest_kick_has_no_pace(self):
        cat = {"category": "season_fg_long", "label": "Longest field goal in a season", "better": "HIGH"}
        row = H.watch_category(cat, [(68.0, "Cam Little", 2025)], [(1, 10, "K", 60.0)], {1: 5}, {1: "IND"})
        self.assertIsNone(row["pace_17"])
        self.assertFalse(row["on_pace"])

    def test_nothing_to_watch(self):
        self.assertIsNone(H.watch_category(self.CAT, self.BOOK, [], {}, {}))
        self.assertIsNone(H.watch_category(self.CAT, [], [(1, 10, "A", 300.0)], {1: 1}, {1: "CAR"}))


@pytest.mark.integration
class EndpointTest(unittest.TestCase):
    TABLES = ("record_categories", "league_record_entries", "franchise_records", "awards")

    def setUp(self):
        app = FastAPI()
        app.include_router(H.router)
        self.client = TestClient(app)
        self.conn = psycopg.connect(H.CONN_INFO)

    def tearDown(self):
        self.conn.close()

    def counts(self):
        with self.conn.cursor() as cur:
            out = {}
            for t in self.TABLES:
                cur.execute(f"SELECT count(*) FROM {t};")
                out[t] = cur.fetchone()[0]
            return out

    def query(self, sql, params=()):
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def test_every_category_returns(self):
        before = self.counts()
        reg = self.client.get("/api/records").json()["categories"]
        post = self.client.get("/api/records?season_type=POSTSEASON").json()["categories"]
        total = self.query("SELECT count(*) FROM record_categories;")[0][0]
        self.assertEqual(len(reg) + len(post), total)
        self.assertEqual(self.counts(), before)          # read-only

    def test_ties_show_every_holder(self):
        cards = {c["category"]: c for c in self.client.get("/api/records").json()["categories"]}
        for category, n in self.query("SELECT category, count(*) FROM league_records GROUP BY 1;"):
            if category in cards:
                self.assertEqual(len(cards[category]["record"]), n, category)

    def test_franchise_matches_current_view(self):
        data = self.client.get("/api/records/franchise/car").json()
        self.assertEqual(data["team"]["abbreviation"], "CAR")
        want = self.query("SELECT count(*) FROM franchise_records_current fr JOIN teams t ON t.id = fr.team_id "
                          "WHERE t.abbreviation = 'CAR' AND fr.season_type = 'REGULAR';")[0][0]
        self.assertEqual(sum(len(r["record"]) for r in data["records"]), want)

    def test_unknown_team_and_category(self):
        self.assertEqual(self.client.get("/api/records/franchise/zzz").status_code, 404)
        self.assertEqual(self.client.get("/api/records/category/nope").status_code, 404)

    def test_category_detail(self):
        data = self.client.get("/api/records/category/season_pass_yards").json()
        self.assertTrue(all(m["rank"] <= 10 for m in data["top10"]))
        self.assertTrue(all(m["mu"] and m["rank"] > 10 for m in data["mu_outside_top10"]))
        self.assertGreater(len(data["franchise_records"]), 0)

    def test_awards(self):
        before = self.counts()
        data = self.client.get("/api/awards").json()
        sbs = self.query("SELECT count(*) FROM games WHERE game_type = 'SUPER_BOWL' AND status = 'COMPLETED';")[0][0]
        self.assertEqual(sum(1 for s in data["seasons"] if s["champion"]), sbs)
        stored = self.query("SELECT count(*) FROM awards WHERE award_type = ANY(%s);", (list(H.AWARD_TYPES),))[0][0]
        self.assertEqual(sum(1 for s in data["seasons"] for a in s["awards"].values() if a), stored)
        self.assertEqual([s["season"] for s in data["seasons"]], sorted((s["season"] for s in data["seasons"]), reverse=True))
        self.assertEqual(self.counts(), before)

    def test_all_pro(self):
        data = self.client.get("/api/awards/all-pro").json()
        if not data["seasons_available"]:
            self.skipTest("no All-Pro teams stored")
        stored = self.query("SELECT count(*) FROM awards WHERE season = %s AND award_type LIKE '%%All-Pro';",
                            (data["season"],))[0][0]
        self.assertEqual(len(data["first_team"]) + len(data["second_team"]), stored)
        slots = [H.SLOT_ORDER.index(r["slot"]) for r in data["first_team"] if r["slot"] in H.SLOT_ORDER]
        self.assertEqual(slots, sorted(slots))
        empty = self.client.get("/api/awards/all-pro?season=1999").json()
        self.assertEqual((empty["first_team"], empty["second_team"]), ([], []))

    def test_franchise_titles(self):
        titles = self.client.get("/api/records/franchise/car").json()["titles"]
        want = self.query("SELECT count(*) FROM games g JOIN teams t ON t.abbreviation = 'CAR' "
                          "WHERE g.game_type = 'SUPER_BOWL' AND g.status = 'COMPLETED' AND ("
                          "(g.home_team_id = t.id AND g.home_score > g.away_score) OR "
                          "(g.away_team_id = t.id AND g.away_score > g.home_score));")[0][0]
        self.assertEqual(len(titles), want)
        self.assertTrue(all(t["score"] > t["opponent_score"] for t in titles))

    def test_player_awards_on_both_cursor_kinds(self):
        row = self.query("SELECT player_id, count(*) FROM awards WHERE player_id IS NOT NULL "
                         "GROUP BY 1 ORDER BY 2 DESC LIMIT 1;")
        if not row:
            self.skipTest("no player awards stored")
        pid, n = row[0]
        with self.conn.cursor() as cur:
            plain = H.fetch_player_awards(cur, pid)
        with self.conn.cursor(row_factory=dict_row) as cur:
            as_dicts = H.fetch_player_awards(cur, pid)
        self.assertEqual(len(plain), n)
        self.assertEqual(plain, as_dicts)
        self.assertEqual([a["season"] for a in plain], sorted((a["season"] for a in plain), reverse=True))

    def test_record_watch_only_in_season(self):
        before = self.counts()
        body = self.client.get("/api/records/watch").json()
        season = body["season"]
        live = bool(self.query("SELECT 1 FROM games WHERE season = %s AND game_type = 'REGULAR_SEASON' "
                               "AND status IS DISTINCT FROM 'COMPLETED' LIMIT 1;", (season,)))
        self.assertEqual(body["in_progress"], live)
        if not live:
            self.assertEqual(body["categories"], [])
        for row in body["categories"]:
            self.assertTrue(row["leaders"])
            self.assertGreater(row["record"]["value"], 0)
        self.assertEqual(self.client.get("/api/records/watch?season=2035").json()["categories"], [])
        self.assertEqual(self.counts(), before)


if __name__ == "__main__":
    unittest.main()
