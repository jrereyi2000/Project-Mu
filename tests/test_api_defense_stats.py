"""Defense, special-teams and team situational stats on the site: /api/stats/player and /api/stats/team return the
columns the Stats page shows (Mu_Network/src/App.tsx unitStatColumns), and /api/player/{id} gives defenders,
kickers and punters their own stat group. Reads the live database (skipped when it isn't reachable).

Run: python3 -m unittest tests.test_api_defense_stats
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def frontend_columns() -> dict:
    with open(os.path.join(ROOT, "Mu_Network", "src", "App.tsx")) as f:
        src = f.read()
    block = src[src.index("const unitStatColumns"):]
    block = block[:block.index("};")]
    return {m.group(1) or m.group(2): re.findall(r'"([^"]+)"', m.group(3))
            for m in re.finditer(r'(?:"([^"]+)"|(\w+)):\s*\[([^\]]*)\]', block)}


try:
    from fastapi.testclient import TestClient
    from server.api import app
    client = TestClient(app)
    client.get("/api/status").raise_for_status()
except Exception as e:   # no database or server dependencies here
    client = None
    SKIP = str(e)


@unittest.skipIf(client is None, "live database not reachable")
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
