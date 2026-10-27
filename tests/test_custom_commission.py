import unittest
from unittest.mock import MagicMock, patch
from newsroom.custom_commission import (
    load_article_catalog,
    load_teams,
    commission_custom_article,
)
from fastapi.testclient import TestClient
from server.api import app
from pipeline.notify import env


class TestCustomCommission(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.token = env("DEBUG_TOKEN") or "test_token"

    def test_catalog_loading(self):
        article_types, fantasy_writers, personas = load_article_catalog()
        self.assertIn("MACRO_SLATE_RECAP", article_types)
        self.assertIn("FANTASY_WAIVER_WIRE", article_types)
        self.assertIn("BERRY", fantasy_writers)
        self.assertIn("BARNWELL", personas)

    def test_load_teams(self):
        fake_rows = [
            {"id": i, "city": "Carolina" if i == 11 else f"City {i}", "name": "Panthers" if i == 11 else f"Team {i}", "abbreviation": "CAR" if i == 11 else f"T{i:02d}"}
            for i in range(1, 33)
        ]
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        mock_conn.execute.return_value.fetchall.return_value = fake_rows
        with patch("newsroom.custom_commission.psycopg.connect", return_value=mock_conn):
            teams = load_teams()
        self.assertEqual(len(teams), 32)
        car = next((t for t in teams if t["abbreviation"] == "CAR"), None)
        self.assertIsNotNone(car)

    def test_dry_run_national_commission(self):
        fake_teams = [{"id": 11, "city": "Carolina", "name": "Panthers", "abbreviation": "CAR"}]
        with patch("newsroom.custom_commission.get_league_state", return_value=(2035, 18)), \
             patch("newsroom.custom_commission.load_teams", return_value=fake_teams), \
             patch("newsroom.custom_commission.build_weekly_assignment_brief", return_value={"publication_week": 18}), \
             patch("newsroom.swarm_dispatcher.SwarmDispatcher.get_agent_record", return_value=("writer_joyner", "JOYNER", "", "@KCJoyner", "KC Joyner")), \
             patch("newsroom.swarm_dispatcher.psycopg.connect", side_effect=RuntimeError("offline")):
            res = commission_custom_article(
                prompt="Break down the Panthers defensive front and pass rush sustainability",
                season=2035,
                week=18,
                target_desk="NATIONAL",
                article_type_id="SINGLE_GAME_SCHEMATIC_AUTOPSY",
                writer_archetype="JOYNER",
                team_id=11,
                desk_note="Analyze Carolina pass rush against top pass protection",
                live=False,
                save_to_db=False,
            )
        self.assertEqual(res["target_desk"], "NATIONAL")
        self.assertEqual(res["slot"], "Special Commission")
        self.assertEqual(res["article_type"], "SINGLE_GAME_SCHEMATIC_AUTOPSY")

    def test_dry_run_fantasy_commission(self):
        fake_teams = [{"id": 11, "city": "Carolina", "name": "Panthers", "abbreviation": "CAR"}]
        with patch("newsroom.custom_commission.get_league_state", return_value=(2035, 18)), \
             patch("newsroom.custom_commission.load_teams", return_value=fake_teams), \
             patch("newsroom.custom_commission.build_weekly_assignment_brief", return_value={"publication_week": 18}), \
             patch("newsroom.swarm_dispatcher.SwarmDispatcher.get_agent_record", return_value=("writer_yates", "YATES", "", "@FieldYates", "Field Yates")), \
             patch("newsroom.swarm_dispatcher.psycopg.connect", side_effect=RuntimeError("offline")):
            res = commission_custom_article(
                prompt="Waiver wire priority adds for running backs",
                season=2035,
                week=18,
                target_desk="FANTASY",
                article_type_id="FANTASY_WAIVER_WIRE",
                writer_archetype="YATES",
                team_id=None,
                desk_note="Rank top waiver wire RBs",
                live=False,
                save_to_db=False,
            )
        self.assertEqual(res["target_desk"], "FANTASY")
        self.assertEqual(res["slot"], "Fantasy Spotlight")
        self.assertEqual(res["article_type"], "FANTASY_WAIVER_WIRE")

    def test_classify_article_endpoint_validation(self):
        # Empty prompt should return 400
        res = self.client.post(
            "/api/debug/classify_article",
            json={"prompt": ""},
            headers={"X-Debug-Token": self.token},
        )
        self.assertEqual(res.status_code, 400)


if __name__ == "__main__":
    unittest.main()
