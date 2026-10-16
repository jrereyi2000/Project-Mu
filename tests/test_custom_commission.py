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
        teams = load_teams()
        self.assertEqual(len(teams), 32)
        car = next((t for t in teams if t["abbreviation"] == "CAR"), None)
        self.assertIsNotNone(car)

    def test_dry_run_national_commission(self):
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
