"""SwarmDispatcher._execute_agent must only save real model output to news_feed. No database required
(psycopg.connect is mocked). Needs the project's Python deps (psycopg, langchain_core) to import the dispatcher.

Run: python3 -m unittest tests.test_article_saving
"""
import importlib.util
import unittest
from unittest import mock

DEPS_AVAILABLE = all(importlib.util.find_spec(m) for m in ("psycopg", "langchain_core"))


def compiled_prompt():
    return {
        "display_name": "Bill Barnwell", "handle": "@billbarnwell", "content_type": "ARTICLE",
        "archetype": "BARNWELL", "article_type": "MACRO_SLATE_RECAP", "article_type_name": "The Macro Slate Recap",
        "playoff_override": None, "temporal_mode": "RETROSPECTIVE", "recap_week": 6,
        "system_instruction": "sys", "task_instruction": "task",
    }


@unittest.skipUnless(DEPS_AVAILABLE, "project dependencies (psycopg, langchain_core) not installed")
class ArticleSavingTests(unittest.TestCase):
    def setUp(self):
        from newsroom import swarm_dispatcher
        self.module = swarm_dispatcher
        self.sd = swarm_dispatcher.SwarmDispatcher.__new__(swarm_dispatcher.SwarmDispatcher)
        self.sd.debug_mode = False
        self.sd.db = mock.Mock(db_conn_info="stub")
        self.sd.entity_tagger = mock.Mock()

    def run_agent(self, **kwargs):
        with mock.patch.object(self.module.psycopg, "connect") as connect:
            res = self.sd._execute_agent(compiled_prompt(), 2035, 7, None, slot="Hero Slot", **kwargs)
        return res, connect

    def test_mock_output_is_never_saved(self):
        self.sd.api_key = "key"
        res, connect = self.run_agent(live=False, save_to_db=True)
        connect.assert_not_called()
        self.assertIsNone(res["article_id"])

    def test_missing_api_key_is_not_saved(self):
        self.sd.api_key = None
        res, connect = self.run_agent(live=True, save_to_db=True)
        connect.assert_not_called()
        self.assertEqual(res["error"], "missing GEMINI_API_KEY")

    def test_gemini_exception_is_not_saved(self):
        self.sd.api_key = "key"
        self.sd.tools = []
        genai = mock.Mock(Client=mock.Mock(side_effect=RuntimeError("quota")))
        with mock.patch.dict("sys.modules", {"google": mock.Mock(genai=genai), "google.genai": genai}):
            res, connect = self.run_agent(live=True, save_to_db=True)
        connect.assert_not_called()
        self.assertIn("quota", res["error"])


if __name__ == "__main__":
    unittest.main()
