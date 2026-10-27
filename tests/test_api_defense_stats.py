"""Defense, special-teams and team situational stats on the site: /api/stats/player and /api/stats/team return the
columns the Stats page shows (Mu_Network/src/App.tsx unitStatColumns), and /api/player/{id} gives defenders,
kickers and punters their own stat group. Reads the live database (skipped when it isn't reachable).

The game page (/api/game/{id}) returns only real team_stats values (estimated ones blanked, exact ones kept) and
the game's defenders, kickers and punters where they were captured.

Run: python3 -m unittest tests.test_api_defense_stats
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def frontend_columns(name: str = "unitStatColumns") -> dict:
    with open(os.path.join(ROOT, "Mu_Network", "src", "components", "league", "StatsPage.tsx")) as f:
        src = f.read()
    block = src[src.index(f"const {name}"):]
    block = block[:block.index("};")]
    return {m.group(1) or m.group(2): re.findall(r'"([^"]+)"', m.group(3))
            for m in re.finditer(r'(?:"([^"]+)"|(\w+)):\s*\[([^\]]*)\]', block)}


import pytest
from fastapi.testclient import TestClient
from server.api import app

client = TestClient(app)


class RealTeamStatsPureTests(unittest.TestCase):
    def test_real_team_stats_blanks_estimates(self):
        from server.api import real_team_stats
        row = real_team_stats({"third_down_conversions": 5, "third_down_attempts": 13, "red_zone_tds": 2,
                               "first_downs": 0, "total_plays": 0,
                               "estimated_fields": ["third_down_conversions", "third_down_attempts", "red_zone_tds"],
                               "exact_fields": ["third_down_attempts"]})
        self.assertIsNone(row["third_down_conversions"])
        self.assertEqual(row["third_down_attempts"], 13)        # exact weekly fill: real
        self.assertIsNone(row["red_zone_tds"])
        self.assertFalse(row["box_score_captured"])
        self.assertIsNone(row["first_downs"])                    # backfill placeholder, not a value
        self.assertNotIn("estimated_fields", row)
        captured = real_team_stats({"red_zone_tds": 1, "first_downs": 14, "third_down_conversions": 4,
                                    "third_down_attempts": 12, "estimated_fields": ["third_down_attempts"],
                                    "exact_fields": []})
        self.assertTrue(captured["box_score_captured"])
        self.assertEqual((captured["first_downs"], captured["third_down_conversions"]), (14, 4))
        self.assertIsNone(captured["third_down_attempts"])


@pytest.mark.integration
class DefenseStatsApiTests(unittest.TestCase):
    def test_player_categories_match_the_stats_page(self):
        cols = frontend_columns()
        for cat in ("Tackles", "Sacks", "Interceptions", "Kicking", "Punting", "Kick Returns", "Punt Returns"):
            rows = client.get("/api/stats/player", params={"category": cat, "season": 2035}).json()
            self.assertTrue(rows, cat)
            self.assertEqual(list(rows[0]["stats"]), cols[cat], cat)
        sacks = client.get("/api/stats/player", params={"category": "Sacks", "season": 2035}).json()
        self.assertGreaterEqual(sacks[0]["stats"]["SCK"], sacks[-1]["stats"]["SCK"])

    def test_team_categories_match_the_stats_page(self):
        cols = frontend_columns()
        for cat in ("Downs", "Red Zone Defense"):
            rows = client.get("/api/stats/team", params={"category": cat, "season": 2035}).json()
            self.assertEqual(len(rows), 32, cat)
            self.assertEqual(list(rows[0]["stats"]), cols[cat], cat)

    def test_game_page_lines(self):
        import psycopg
        from server.api import CONN_INFO
        with psycopg.connect(CONN_INFO) as conn:
            cur = conn.cursor()
            cur.execute("""SELECT g.id FROM games g WHERE g.season = 2035 AND g.status = 'COMPLETED' AND EXISTS
                           (SELECT 1 FROM game_stat_defense d WHERE d.game_id = g.id) ORDER BY g.id LIMIT 1""")
            captured_id = cur.fetchone()[0]
            cur.execute("""SELECT g.id FROM games g WHERE g.season = 2035 AND g.status = 'COMPLETED' AND NOT EXISTS
                           (SELECT 1 FROM game_stat_defense d WHERE d.game_id = g.id) ORDER BY g.id LIMIT 1""")
            plain_id = cur.fetchone()[0]
        captured = client.get(f"/api/game/{captured_id}").json()
        self.assertTrue(captured["defense"])
        self.assertTrue(any(t["box_score_captured"] for t in captured["team_stats"]))
        plain = client.get(f"/api/game/{plain_id}").json()
        self.assertEqual((plain["defense"], plain["kicking"], plain["punting"]), ([], [], []))
        for t in plain["team_stats"]:
            self.assertNotIn("estimated_fields", t)
            if not t["box_score_captured"]:
                self.assertIsNone(t["first_downs"])

    def test_season_type_filters_offense_and_blanks_capture_tables(self):
        regular = client.get("/api/stats/player", params={"category": "Passing", "season": 2035}).json()
        self.assertTrue(regular)
        post = client.get("/api/stats/player", params={"category": "Passing", "season": 2035,
                                                        "season_type": "postseason"}).json()
        self.assertLessEqual(len(post), len(regular))

    def test_postseason_tables_from_captured_box_scores(self):
        """Postseason defense/special teams and team tables sum the playoff box scores. Week 17's captured games
        stand in for playoff games, which 2035 doesn't have yet."""
        import psycopg
        from psycopg.rows import dict_row
        from server import api
        cols = frontend_columns()
        with psycopg.connect(api.CONN_INFO, row_factory=dict_row) as conn:
            cur = conn.cursor()
            for cat in ("tackles", "sacks", "kicking", "punting"):
                rows = api.player_st_leaders(cur, 2035, cat, postseason=True, games="g.week = 17")
                self.assertTrue(rows, cat)
                self.assertEqual(list(rows[0]["stats"]), cols[cat.title()], cat)
            downs = api.team_situational_postseason(cur, 2035, "downs", games="g.week = 17")
            self.assertEqual(len(downs), 32)
            # box scores have conversions but no attempts: attempt columns are left out, the rest are real
            self.assertEqual(list(downs[0]["stats"]), frontend_columns("postseasonStatColumns")["Downs"])
            self.assertTrue(set(downs[0]["stats"]) <= set(cols["Downs"]))
            self.assertIsNotNone(downs[0]["stats"]["3D CONV"])
            rzd = api.team_situational_postseason(cur, 2035, "red zone defense", games="g.week = 17")
            self.assertEqual(list(rzd[0]["stats"]), cols["Red Zone Defense"])

    def test_status_reports_last_load(self):
        self.assertIn("last_updated", client.get("/api/status").json())

    def test_player_page_articles_are_tagged_mentions(self):
        import psycopg
        from server.api import CONN_INFO
        with psycopg.connect(CONN_INFO) as conn:
            cur = conn.cursor()
            cur.execute("SELECT player_id, COUNT(*) FROM article_players GROUP BY 1 ORDER BY 2 DESC LIMIT 1")
            row = cur.fetchone()
        if not row:
            self.skipTest("no tagged articles")
        data = client.get(f"/api/player/{row[0]}").json()
        self.assertTrue(data["articles"])
        self.assertLessEqual(len(data["articles"]), 6)
        for a in data["articles"]:
            self.assertTrue(a["headline"])
            self.assertNotIn("(", a["label"])

    def test_player_page_stat_groups(self):
        leaders = {cat: client.get("/api/stats/player", params={"category": cat, "season": 2035}).json()[0]["id"]
                   for cat in ("Sacks", "Kicking", "Punting")}
        for cat, group in (("Sacks", "defense"), ("Kicking", "kicking"), ("Punting", "punting")):
            data = client.get(f"/api/player/{leaders[cat]}", params={"season": 2035}).json()
            self.assertEqual(data["stat_group"], group)
            season = next(c for c in data["career_stats"] if c["season"] == 2035)
            self.assertTrue(season["has_capture_line"])
        defender = client.get(f"/api/player/{leaders['Sacks']}", params={"season": 2035}).json()
        self.assertIsNotNone(next(c for c in defender["career_stats"] if c["season"] == 2035)["def_sacks"])


if __name__ == "__main__":
    unittest.main()
