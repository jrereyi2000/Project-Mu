"""db/llm_usage.py: the meter counts every model call on a client, labelled."""
import types
import unittest

from db.llm_usage import UsageMeter


class FakeModels:
    def __init__(self):
        self.n = 0

    def _generate_content(self, model=None, contents=None, config=None):
        self.n += 1
        u = types.SimpleNamespace(prompt_token_count=100 * self.n, cached_content_token_count=10,
                                  candidates_token_count=20, thoughts_token_count=5)
        return types.SimpleNamespace(usage_metadata=u)

    def generate_content(self, model=None, contents=None, config=None):
        # like the SDK's automatic function calling: several requests behind one call
        for _ in range(3):
            resp = self._generate_content(model=model, contents=contents, config=config)
        return resp


class MeterTest(unittest.TestCase):
    def test_counts_the_tool_loop_and_labels(self):
        client = types.SimpleNamespace(models=FakeModels())
        meter = UsageMeter(client)
        with meter.labelled("01_passing_000.png"):
            client.models.generate_content(model="gemini-3.8-flash")
        t = meter.totals()
        self.assertEqual(t, {"input_tokens": 600, "cached_tokens": 30, "output_tokens": 60, "thinking_tokens": 15,
                             "calls": 3})
        self.assertEqual({c["label"] for c in meter.calls}, {"01_passing_000.png"})
        self.assertEqual({c["model"] for c in meter.calls}, {"gemini-3.8-flash"})


if __name__ == "__main__":
    unittest.main()
