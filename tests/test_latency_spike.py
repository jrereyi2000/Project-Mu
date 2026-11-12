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


if __name__ == "__main__":
    unittest.main()
