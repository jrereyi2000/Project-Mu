"""server/history_api.py: the #records Record Book endpoints. The ranking and badge tests need no database; the
endpoint tests only read the live one (skipped when Postgres is down) and write nothing.

Run this file only (the Napper tests write to the live database): python3 -m unittest tests.test_history_api"""
import unittest

import psycopg
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


def db_up():
    try:
        psycopg.connect(H.CONN_INFO, connect_timeout=2).close()
        return True
    except psycopg.OperationalError:
        return False


@unittest.skipUnless(db_up(), "Postgres not reachable")
class EndpointTest(unittest.TestCase):
    TABLES = ("record_categories", "league_record_entries", "franchise_records")

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


if __name__ == "__main__":
    unittest.main()
