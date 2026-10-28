"""
Unit tests for LLM thinking config helpers and short Gemini call thinking caps.
(docs/PRE_PC_WORK.md §5, docs/PC_MIGRATION_PLAN.md §7.1, §7.9)

Verifies with fake/mock clients (no real API calls) that ThinkingConfig is passed
with the expected levels across llm_config, agent_memory, gm_llm, and social_writer.
"""
import os
import unittest
from unittest import mock
from google.genai import types

from engines.llm_config import (
    DEFAULT_MODEL,
    LABEL_DEFAULT_THINKING,
    get_thinking_config,
    make_content_config,
    parse_model_thinking,
    resolve_thinking_level,
)
from engines.gm_llm import GeminiText, make_llm, call
from newsroom.agent_memory import gemini_text
from newsroom.social_writer import write_post


class FakeResponse:
    def __init__(self, text="Fake response text"):
        self.text = text
        self.usage_metadata = mock.MagicMock(
            prompt_token_count=100,
            cached_content_token_count=0,
            candidates_token_count=50,
            thoughts_token_count=120,
        )


def make_mock_client(response_text="Fake response"):
    client = mock.MagicMock()
    client.models.generate_content.return_value = FakeResponse(response_text)
    client.models._generate_content = mock.MagicMock(return_value=FakeResponse(response_text))
    return client


class LLMConfigUnitTests(unittest.TestCase):
    def test_parse_model_thinking_explicit(self):
        self.assertEqual(parse_model_thinking("gemini-3.8-flash:low"), ("gemini-3.8-flash", "low"))
        self.assertEqual(parse_model_thinking("gemini-3.8-flash:HIGH"), ("gemini-3.8-flash", "high"))
        self.assertEqual(parse_model_thinking("gemini-2.5-flash:medium"), ("gemini-2.5-flash", "medium"))
        self.assertEqual(parse_model_thinking("custom-model:minimal"), ("custom-model", "minimal"))

    def test_parse_model_thinking_disabled_levels(self):
        for lvl in ("default", "none", "off", "unspecified", ""):
            model, think = parse_model_thinking(f"gemini-3.8-flash:{lvl}")
            self.assertEqual(model, "gemini-3.8-flash")
            self.assertIsNone(think)

    def test_parse_model_thinking_defaults(self):
        self.assertEqual(parse_model_thinking("gemini-3.8-flash", default_thinking="low"), ("gemini-3.8-flash", "low"))
        self.assertEqual(parse_model_thinking("gemini-3.8-flash", default_thinking=None), ("gemini-3.8-flash", None))
        self.assertEqual(parse_model_thinking(None, default_thinking="low"), (DEFAULT_MODEL, "low"))

    def test_resolve_thinking_level_priority(self):
        # 1. Explicit model:level wins over everything
        with mock.patch.dict(os.environ, {"GM_LLM_THINKING": "high", "GM_THINKING_SCOUTING_NOTES": "high"}):
            self.assertEqual(resolve_thinking_level("scouting_notes", model="gemini-3.8-flash:low", thinking_level="medium"), "low")

        # 2. Explicit thinking_level parameter wins over env vars and defaults
        with mock.patch.dict(os.environ, {"GM_LLM_THINKING": "high"}):
            self.assertEqual(resolve_thinking_level("gm_persona", model="gemini-3.8-flash", thinking_level="minimal"), "minimal")

        # 3. Label-specific env var wins over general env var
        with mock.patch.dict(os.environ, {"GM_THINKING_GM_NEGOTIATION": "medium", "GM_LLM_THINKING": "high"}):
            self.assertEqual(resolve_thinking_level("gm_negotiation", model="gemini-3.8-flash"), "medium")

        # 4. Agent memory env var
        with mock.patch.dict(os.environ, {"AGENT_MEMORY_THINKING": "medium"}):
            self.assertEqual(resolve_thinking_level("agent_memory", model="gemini-3.8-flash"), "medium")

        # 5. General GM_LLM_THINKING env var
        with mock.patch.dict(os.environ, {"GM_LLM_THINKING": "high"}):
            self.assertEqual(resolve_thinking_level("gm_rationale", model="gemini-3.8-flash"), "high")

        # 6. Fallback defaults for short calls
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_thinking_level("agent_memory", model="gemini-3.8-flash"), "low")
            self.assertEqual(resolve_thinking_level("scouting_notes", model="gemini-3.8-flash"), "low")
            self.assertEqual(resolve_thinking_level("gm_persona", model="gemini-3.8-flash"), "low")
            self.assertEqual(resolve_thinking_level("gm_rationale", model="gemini-3.8-flash"), "low")
            self.assertEqual(resolve_thinking_level("gm_negotiation", model="gemini-3.8-flash"), "low")
            self.assertEqual(resolve_thinking_level("draft_rationale", model="gemini-3.8-flash"), "low")

    def test_make_content_config_thinking(self):
        config, clean_model = make_content_config(model="gemini-3.8-flash:low", temperature=0.3)
        self.assertEqual(clean_model, "gemini-3.8-flash")
        self.assertEqual(config.temperature, 0.3)
        self.assertIsNotNone(config.thinking_config)
        self.assertEqual(config.thinking_config.thinking_level, types.ThinkingLevel.LOW)

    def test_make_content_config_unconstrained(self):
        config, clean_model = make_content_config(model="gemini-3.8-flash:default")
        self.assertEqual(clean_model, "gemini-3.8-flash")
        self.assertIsNone(config.thinking_config)


class AgentMemoryThinkingTests(unittest.TestCase):
    def test_gemini_text_defaults_to_low_thinking(self):
        mock_client = make_mock_client("take extraction response")
        with mock.patch("db.llm_usage.log_response"):
            text = gemini_text(
                api_key="fake-key",
                model="gemini-3.8-flash",
                prompt="Extract takes",
                label="takes #1",
                client=mock_client,
            )
        self.assertEqual(text, "take extraction response")
        mock_client.models.generate_content.assert_called_once()
        _, kwargs = mock_client.models.generate_content.call_args
        self.assertEqual(kwargs["model"], "gemini-3.8-flash")
        config = kwargs["config"]
        self.assertIsNotNone(config.thinking_config)
        self.assertEqual(config.thinking_config.thinking_level, types.ThinkingLevel.LOW)

    def test_gemini_text_model_override(self):
        mock_client = make_mock_client("summary text")
        with mock.patch("db.llm_usage.log_response"):
            gemini_text(
                api_key="fake-key",
                model="gemini-3.8-flash:high",
                prompt="Summarize season",
                label="summary agent",
                client=mock_client,
            )
        _, kwargs = mock_client.models.generate_content.call_args
        self.assertEqual(kwargs["model"], "gemini-3.8-flash")
        config = kwargs["config"]
        self.assertIsNotNone(config.thinking_config)
        self.assertEqual(config.thinking_config.thinking_level, types.ThinkingLevel.HIGH)

    def test_gemini_text_unconstrained_thinking(self):
        mock_client = make_mock_client("summary text")
        with mock.patch("db.llm_usage.log_response"):
            gemini_text(
                api_key="fake-key",
                model="gemini-3.8-flash:default",
                prompt="Summarize season",
                label="summary agent",
                client=mock_client,
            )
        _, kwargs = mock_client.models.generate_content.call_args
        config = kwargs["config"]
        self.assertIsNone(config.thinking_config)


class GMLLMThinkingTests(unittest.TestCase):
    def test_gm_llm_defaults_to_low_thinking(self):
        for label in ("gm_persona", "gm_rationale", "gm_negotiation", "scouting_notes", "draft_rationale"):
            mock_client = make_mock_client(f"{label} response")
            llm = make_llm(label, client=mock_client)
            self.assertIsNotNone(llm)
            self.assertEqual(llm.thinking_level, "low")
            self.assertEqual(llm.model_name, "gemini-3.8-flash")
            self.assertIsNotNone(llm.config.thinking_config)
            self.assertEqual(llm.config.thinking_config.thinking_level, types.ThinkingLevel.LOW)

            out = call(llm, "test prompt")
            self.assertEqual(out, f"{label} response")

    def test_gm_llm_model_colon_override(self):
        mock_client = make_mock_client("high thinking response")
        llm = make_llm("gm_negotiation", model="gemini-3.8-flash:high", client=mock_client)
        self.assertEqual(llm.thinking_level, "high")
        self.assertEqual(llm.model_name, "gemini-3.8-flash")
        self.assertEqual(llm.config.thinking_config.thinking_level, types.ThinkingLevel.HIGH)

    def test_gm_llm_unconstrained_default(self):
        mock_client = make_mock_client("unconstrained response")
        llm = make_llm("gm_negotiation", model="gemini-3.8-flash:default", client=mock_client)
        self.assertIsNone(llm.thinking_level)
        self.assertEqual(llm.model_name, "gemini-3.8-flash")
        self.assertIsNone(llm.config.thinking_config)

    def test_gm_llm_env_var_override(self):
        mock_client = make_mock_client("env var response")
        with mock.patch.dict(os.environ, {"GM_THINKING_GM_NEGOTIATION": "medium"}):
            llm = make_llm("gm_negotiation", client=mock_client)
            self.assertEqual(llm.thinking_level, "medium")
            self.assertEqual(llm.config.thinking_config.thinking_level, types.ThinkingLevel.MEDIUM)

    def test_gm_llm_global_env_var_override(self):
        mock_client = make_mock_client("global env response")
        with mock.patch.dict(os.environ, {"GM_LLM_THINKING": "minimal"}):
            llm = make_llm("gm_persona", client=mock_client)
            self.assertEqual(llm.thinking_level, "minimal")
            self.assertEqual(llm.config.thinking_config.thinking_level, types.ThinkingLevel.MINIMAL)


class SocialWriterThinkingTests(unittest.TestCase):
    def test_write_post_with_thinking_cap(self):
        mock_client = make_mock_client("A great game tonight! #NFL")
        result = write_post(
            client=mock_client,
            model="gemini-3.8-flash:low",
            system="System instruction",
            prompt="Write a reaction",
        )
        self.assertEqual(result["text"], "A great game tonight! #NFL")
        self.assertEqual(result["hashtags"], ["NFL"])
        _, kwargs = mock_client.models.generate_content.call_args
        self.assertEqual(kwargs["model"], "gemini-3.8-flash")
        config = kwargs["config"]
        self.assertIsNotNone(config.thinking_config)
        self.assertEqual(config.thinking_config.thinking_level, types.ThinkingLevel.LOW)

    def test_write_post_without_thinking_cap(self):
        mock_client = make_mock_client("A great game tonight! #NFL")
        write_post(
            client=mock_client,
            model="gemini-3.8-flash",
            system="System instruction",
            prompt="Write a reaction",
        )
        _, kwargs = mock_client.models.generate_content.call_args
        self.assertEqual(kwargs["model"], "gemini-3.8-flash")
        config = kwargs["config"]
        self.assertIsNone(config.thinking_config)


if __name__ == "__main__":
    unittest.main()
