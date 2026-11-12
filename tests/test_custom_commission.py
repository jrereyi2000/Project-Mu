import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from newsroom.custom_commission import (
    classify_prompt,
    load_article_catalog,
    load_teams,
    commission_custom_article,
)
from newsroom.model_default import DEFAULT_WRITER_MODEL
from fastapi.testclient import TestClient
from server.api import app
from pipeline.notify import env


CLASSIFICATION = {"target_desk": "NATIONAL", "article_type_id": "MACRO_SLATE_RECAP",
                  "writer_archetype": "BARNWELL", "team_id": None,
                  "desk_note": "Third-down defence", "rationale": "Film breakdown"}

CATALOG = ({"MACRO_SLATE_RECAP": {"name": "Slate recap", "focus": "weekly", "allowed_writers": ["BARNWELL"]}},
           {"BERRY": {"name": "Berry"}},
           {"BARNWELL": {"name": "Barnwell"}})


class _FakeResponse:
    def __init__(self, payload):
        self.text = json.dumps(payload)


class _FakeClient:
    """A `google-genai`-shaped client that records the model each call asked for."""

    def __init__(self, payload=CLASSIFICATION):
        self.payload = payload
        self.calls = []
        self.models = self

    def generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeResponse(self.payload)


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


class TestClassifyPromptRoutesByModel(unittest.TestCase):
    """AUDIT R6 F2 / AUDIT_QUEUE_INVENTORY.md R11: the classification client follows the model id. A Go model
    (`glm-5.3-flash`, the writer default) runs on OpenCode Go and needs no GEMINI_API_KEY; a `gemini-*` id keeps
    Google's endpoint and key. Nothing here makes a network call."""

    def test_a_go_model_routes_to_go_without_a_gemini_key(self):
        fake = _FakeClient()
        import newsroom.custom_commission as cc
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"OPENCODE_GO_API_KEY": "test-go"}, clear=True), \
                patch.object(cc, "ROOT", tmp), \
                patch.object(cc, "load_teams", return_value=[]), \
                patch.object(cc, "load_article_catalog", return_value=CATALOG), \
                patch("db.llm_usage.log_response"), \
                patch("newsroom.tool_loop.go_transport.go_client", return_value=fake) as go_client, \
                patch("google.genai.Client", side_effect=AssertionError("a Gemini client was built")):
            out = cc.classify_prompt("Bills third downs", season=2028, week=10)

        self.assertEqual(out["article_type_id"], "MACRO_SLATE_RECAP")
        # The model default is unchanged (no default flip); only the client moved to the one that serves it.
        self.assertEqual(fake.calls[0]["model"], DEFAULT_WRITER_MODEL)
        self.assertEqual(go_client.call_args, ((), {"api_key": None, "session": None}))

    def test_a_gemini_model_routes_to_google_with_its_own_key(self):
        fake = _FakeClient()
        import newsroom.custom_commission as cc
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"OPENCODE_GO_API_KEY": "test-go", "GEMINI_API_KEY": "test-gem"}, clear=True), \
                patch.object(cc, "ROOT", tmp), \
                patch.object(cc, "load_teams", return_value=[]), \
                patch.object(cc, "load_article_catalog", return_value=CATALOG), \
                patch("db.llm_usage.log_response"), \
                patch("newsroom.tool_loop.go_transport.go_client",
                      side_effect=AssertionError("a Go client was built for a Gemini model")), \
                patch("google.genai.Client", return_value=fake) as gemini:
            out = cc.classify_prompt("Bills third downs", season=2028, week=10, model="gemini-3.8-flash")

        self.assertEqual(out["article_type_id"], "MACRO_SLATE_RECAP")
        gemini.assert_called_once_with(api_key="test-gem")
        self.assertEqual(fake.calls[0]["model"], "gemini-3.8-flash")

    def test_a_go_model_without_the_go_key_fails_loudly_and_never_calls_google(self):
        import newsroom.custom_commission as cc
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(cc, "ROOT", tmp), \
                patch("newsroom.tool_loop.go_transport.go_api_key", lambda: None), \
                patch("google.genai.Client", side_effect=AssertionError("a client was built")) as built:
            with self.assertRaises(ValueError) as ctx:
                cc.classify_prompt("Bills third downs", season=2028, week=10)
        self.assertIn("OPENCODE_GO_API_KEY", str(ctx.exception))
        built.assert_not_called()

    def test_a_gemini_model_without_a_gemini_key_keeps_its_own_error(self):
        import newsroom.custom_commission as cc
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {}, clear=True), \
                patch.object(cc, "ROOT", tmp):
            with self.assertRaises(RuntimeError) as ctx:
                cc.classify_prompt("Bills third downs", season=2028, week=10, model="gemini-3.8-flash")
        self.assertIn("GEMINI_API_KEY", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
