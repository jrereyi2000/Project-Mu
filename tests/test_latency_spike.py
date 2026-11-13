"""The `tools/latency_spike.py` inbox diagnostic (AUDIT R6 F5).

When `make_llm("inbox")` returns None, the message must name the key the *resolved* provider needs:
`OPENCODE_GO_API_KEY` for a Go route, `GEMINI_API_KEY` for the Gemini default. Offline: no client, no key,
no call.
"""
import os
import unittest
from unittest import mock

import tools.latency_spike as LS


class InboxModelMessageTests(unittest.TestCase):
    def _message(self, env, model=None):
        task = mock.Mock(prompt="fill the contract")
        with mock.patch("tools.llm_compare.build_inbox_appraisal_inputs", return_value=[task]), \
                mock.patch("engines.gm_llm.make_llm", return_value=None), \
                mock.patch.dict(os.environ, env, clear=True):
            with self.assertRaises(SystemExit) as caught:
                LS.run_inbox(calls=1, model=model)
        return str(caught.exception)

    def test_a_go_route_names_the_go_key(self):
        message = self._message({"LLM_PROVIDER_INBOX": "go"})
        self.assertIn("OPENCODE_GO_API_KEY", message)
        self.assertNotIn("GEMINI_API_KEY", message)

    def test_a_go_model_argument_names_the_go_key(self):
        message = self._message({}, model="glm-5.3-flash")
        self.assertIn("OPENCODE_GO_API_KEY", message)
        self.assertNotIn("GEMINI_API_KEY", message)

    def test_the_gemini_default_still_names_the_gemini_key(self):
        message = self._message({})
        self.assertIn("GEMINI_API_KEY", message)


class UnpricedModelTests(unittest.TestCase):
    """N2: `estimate_cost` returns None for an id with no known rate; the payload carries null."""

    def test_an_unpriced_inbox_model_is_a_null_cost_not_a_crash(self):
        written = {}

        class FakeLLM:
            model_name = "some-unknown-model"

            def __call__(self, prompt):
                return "OK"

        task = mock.Mock(prompt="fill the contract")
        with mock.patch("tools.llm_compare.build_inbox_appraisal_inputs", return_value=[task]), \
                mock.patch("engines.gm_llm.make_llm", return_value=FakeLLM()), \
                mock.patch.object(LS, "_write",
                                  side_effect=lambda name, payload: written.setdefault("p", payload)):
            payload = LS.run_inbox(calls=1, model="some-unknown-model")

        self.assertIsNone(payload["cost_usd"])
        self.assertIsNone(written["p"]["cost_usd"])

    def test_an_unpriced_press_model_is_a_null_cost_not_a_crash(self):
        with mock.patch("engines.llm_provider.generate_text",
                        return_value=("text", {"input_tokens": 5, "output_tokens": 2})), \
                mock.patch("newsroom.press_room.run_week", return_value={}), \
                mock.patch.object(LS, "_write", return_value="report.json"):
            payload = LS.run_press(dsn="postgresql://scratch/x", season=2026, week=1, team="SEA",
                                   model="some-unknown-model", allow_active=True)

        self.assertIsNone(payload["cost_usd"])


if __name__ == "__main__":
    unittest.main()
