"""
Unit tests for the Gemini-or-local LLM provider switch and llm_usage logging.
(docs/done/PRE_PC_WORK.md §6, docs/in_progress/PC_MIGRATION_PLAN.md §7.3)

Verifies using unittest.mock (no real paid API calls or running Ollama required):
1. Provider and model resolution (prefixes, per-label env, global env, defaults).
2. Ollama base URL handling.
3. Successful Ollama API call and response parsing (/api/generate and /api/chat).
4. Automatic fallback to Gemini on connection error and timeout (never on bad answers).
5. Token usage logging to llm_usage for both Ollama ('ollama/<model>') and Gemini.
6. Integration with short-task callers: agent_memory, gm_llm, and social_writer.
"""
import os
import unittest
from unittest import mock
import requests

from engines.llm_provider import (
    DEFAULT_FALLBACK_MODEL,
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OLLAMA_MODEL,
    ProviderText,
    call_ollama,
    generate_text,
    get_ollama_base_url,
    resolve_fallback_model,
    resolve_model,
    resolve_provider,
)
from db.llm_usage import UsageMeter, log_response, log_usage, normalize_model_name
from engines.gm_llm import make_llm
from newsroom.agent_memory import gemini_text, llm_text
from newsroom.social_writer import cost_usd, write_post


class FakeGeminiResponse:
    def __init__(self, text="Fake Gemini response text"):
        self.text = text
        self.usage_metadata = mock.MagicMock(
            prompt_token_count=120,
            cached_content_token_count=0,
            candidates_token_count=45,
            thoughts_token_count=80,
        )


def make_mock_gemini_client(response_text="Fake Gemini response"):
    client = mock.MagicMock()
    client.models.generate_content.return_value = FakeGeminiResponse(response_text)
    client.models._generate_content = mock.MagicMock(return_value=FakeGeminiResponse(response_text))
    return client


class LLMProviderResolutionTests(unittest.TestCase):
    def test_default_provider_is_gemini(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_provider("gm_rationale"), "gemini")
            self.assertEqual(resolve_model("gemini", "gm_rationale"), DEFAULT_GEMINI_MODEL)

    def test_provider_from_model_prefix(self):
        self.assertEqual(resolve_provider("gm_persona", model="ollama:qwen2.5:14b"), "ollama")
        self.assertEqual(resolve_model("ollama", "gm_persona", model="ollama:qwen2.5:14b"), "qwen2.5:14b")

        self.assertEqual(resolve_provider("agent_wire", model="gemini:gemini-3.8-flash:low"), "gemini")
        self.assertEqual(resolve_model("gemini", "agent_wire", model="gemini:gemini-3.8-flash:low"), "gemini-3.8-flash:low")

    def test_provider_from_per_label_env(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER_AGENT_WIRE": "ollama"}):
            self.assertEqual(resolve_provider("agent_wire"), "ollama")
            self.assertEqual(resolve_provider("agent_memory"), "gemini")

    def test_provider_from_global_env(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "ollama"}):
            self.assertEqual(resolve_provider("agent_memory"), "ollama")
            self.assertEqual(resolve_provider("gm_rationale"), "ollama")

    def test_per_label_env_overrides_global_env(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "gemini", "LLM_PROVIDER_SCOUTING_NOTES": "ollama"}):
            self.assertEqual(resolve_provider("scouting_notes"), "ollama")
            self.assertEqual(resolve_provider("agent_memory"), "gemini")

    def test_explicit_provider_arg_overrides_env(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "gemini"}):
            self.assertEqual(resolve_provider("gm_persona", provider="ollama"), "ollama")

    def test_resolve_fallback_model(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_fallback_model("agent_wire"), DEFAULT_FALLBACK_MODEL)

        with mock.patch.dict(os.environ, {"LLM_FALLBACK_MODEL": "gemini-3.8-flash:minimal"}):
            self.assertEqual(resolve_fallback_model("agent_wire"), "gemini-3.8-flash:minimal")

        with mock.patch.dict(os.environ, {"LLM_FALLBACK_MODEL_AGENT_WIRE": "gemini-2.5-flash"}):
            self.assertEqual(resolve_fallback_model("agent_wire"), "gemini-2.5-flash")

    def test_get_ollama_base_url(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_ollama_base_url(), "http://localhost:11434")

        with mock.patch.dict(os.environ, {"OLLAMA_HOST": "192.168.1.10"}):
            self.assertEqual(get_ollama_base_url(), "http://192.168.1.10:11434")

        with mock.patch.dict(os.environ, {"OLLAMA_HOST": "my-pc:11434"}):
            self.assertEqual(get_ollama_base_url(), "http://my-pc:11434")

        with mock.patch.dict(os.environ, {"OLLAMA_HOST": "http://wsl-host:11434"}):
            self.assertEqual(get_ollama_base_url(), "http://wsl-host:11434")


class OllamaAPICallTests(unittest.TestCase):
    @mock.patch("requests.post")
    def test_call_ollama_generate(self, mock_post):
        mock_resp = mock.MagicMock()
        mock_resp.json.return_value = {
            "model": "qwen2.5:14b",
            "response": "Generated Ollama output text",
            "done": True,
            "prompt_eval_count": 35,
            "eval_count": 50,
            "thinking_tokens": 0,
        }
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        text, usage = call_ollama(
            model="qwen2.5:14b",
            prompt="Hello prompt",
            system="You are a GM",
            temperature=0.5,
        )

        self.assertEqual(text, "Generated Ollama output text")
        self.assertEqual(usage["input_tokens"], 35)
        self.assertEqual(usage["output_tokens"], 50)
        self.assertEqual(usage["cached_tokens"], 0)
        self.assertEqual(usage["thinking_tokens"], 0)

        mock_post.assert_called_once()
        args, kwargs = mock_post.call_args
        self.assertTrue(args[0].endswith("/api/generate"))
        self.assertEqual(kwargs["json"]["model"], "qwen2.5:14b")
        self.assertEqual(kwargs["json"]["prompt"], "Hello prompt")
        self.assertEqual(kwargs["json"]["system"], "You are a GM")
        self.assertEqual(kwargs["json"]["options"]["temperature"], 0.5)

    @mock.patch("requests.post")
    def test_call_ollama_chat_with_schema(self, mock_post):
        mock_resp = mock.MagicMock()
        mock_resp.json.return_value = {
            "model": "qwen2.5:14b",
            "message": {"role": "assistant", "content": '{"name": "John Doe", "age": 45}'},
            "done": True,
            "prompt_eval_count": 60,
            "eval_count": 25,
        }
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        schema = {"type": "object", "properties": {"name": {"type": "string"}, "age": {"type": "integer"}}}
        messages = [{"role": "user", "content": "Generate GM persona"}]

        text, usage = call_ollama(
            model="qwen2.5:14b",
            messages=messages,
            response_schema=schema,
        )

        self.assertEqual(text, '{"name": "John Doe", "age": 45}')
        self.assertEqual(usage["input_tokens"], 60)
        self.assertEqual(usage["output_tokens"], 25)

        args, kwargs = mock_post.call_args
        self.assertTrue(args[0].endswith("/api/chat"))
        self.assertEqual(kwargs["json"]["format"], schema)


class ProviderFallbackTests(unittest.TestCase):
    @mock.patch("requests.post")
    def test_fallback_to_gemini_on_connection_error(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("Connection refused to http://localhost:11434")
        mock_client = make_mock_gemini_client("Gemini fallback text")

        with mock.patch("engines.llm_provider.log_response") as mock_log:
            text = generate_text(
                prompt="Draft rationale prompt",
                source="draft_rationale",
                label="draft_rationale",
                model="ollama:qwen2.5:14b",
                client=mock_client,
            )

        self.assertEqual(text, "Gemini fallback text")
        mock_client.models.generate_content.assert_called_once()
        mock_log.assert_called_once()
        args, kwargs = mock_log.call_args
        self.assertEqual(kwargs.get("source") or args[0], "draft_rationale")
        self.assertEqual(kwargs.get("model") or args[2], DEFAULT_FALLBACK_MODEL)

    @mock.patch("requests.post")
    def test_fallback_to_gemini_on_timeout(self, mock_post):
        mock_post.side_effect = requests.exceptions.Timeout("Read timed out after 60s")
        mock_client = make_mock_gemini_client("Gemini timeout fallback")

        with mock.patch("engines.llm_provider.log_response"):
            text = generate_text(
                prompt="Trade rationale prompt",
                source="gm_rationale",
                label="gm_rationale",
                model="ollama:qwen2.5:14b",
                client=mock_client,
            )

        self.assertEqual(text, "Gemini timeout fallback")
        mock_client.models.generate_content.assert_called_once()

    @mock.patch("requests.post")
    def test_no_fallback_on_bad_answer(self, mock_post):
        # A 200 OK that returns malformed or unexpected text should NOT fallback to Gemini
        mock_resp = mock.MagicMock()
        mock_resp.json.return_value = {
            "model": "qwen2.5:14b",
            "response": "I cannot answer this question properly.",
            "done": True,
            "prompt_eval_count": 20,
            "eval_count": 10,
        }
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp
        mock_client = make_mock_gemini_client("Should not be called")

        with mock.patch("engines.llm_provider.log_usage"):
            text = generate_text(
                prompt="GM prompt",
                source="gm_persona",
                label="gm_persona",
                model="ollama:qwen2.5:14b",
                client=mock_client,
            )

        self.assertEqual(text, "I cannot answer this question properly.")
        mock_client.models.generate_content.assert_not_called()


class LLMUsageLoggingTests(unittest.TestCase):
    def test_normalize_model_name(self):
        self.assertEqual(normalize_model_name("ollama:qwen2.5:14b"), "ollama/qwen2.5:14b")
        self.assertEqual(normalize_model_name("ollama/qwen2.5:14b"), "ollama/qwen2.5:14b")
        self.assertEqual(normalize_model_name("gemini:gemini-3.8-flash:low"), "gemini-3.8-flash")
        self.assertEqual(normalize_model_name("gemini-3.8-flash:low"), "gemini-3.8-flash")
        self.assertEqual(normalize_model_name("gemini-3.8-flash"), "gemini-3.8-flash")

    @mock.patch("psycopg.connect")
    def test_log_usage_ollama(self, mock_connect):
        mock_conn = mock.MagicMock()
        mock_connect.return_value.__enter__.return_value = mock_conn

        log_usage(
            source="agent_wire",
            label="post CAR",
            model="ollama/qwen2.5:14b",
            input_tokens=150,
            cached_tokens=0,
            output_tokens=65,
            thinking_tokens=0,
        )

        mock_conn.execute.assert_called_once()
        args = mock_conn.execute.call_args[0]
        params = args[1]
        self.assertEqual(params[0], "agent_wire")
        self.assertEqual(params[1], "post CAR")
        self.assertEqual(params[2], "ollama/qwen2.5:14b")
        self.assertEqual(params[3], 150)  # input_tokens
        self.assertEqual(params[4], 0)    # cached_tokens
        self.assertEqual(params[5], 65)   # output_tokens
        self.assertEqual(params[6], 0)    # thinking_tokens

    @mock.patch("psycopg.connect")
    def test_log_response_gemini(self, mock_connect):
        mock_conn = mock.MagicMock()
        mock_connect.return_value.__enter__.return_value = mock_conn

        resp = FakeGeminiResponse()
        log_response("agent_memory", "summary @handle", "gemini-3.8-flash:low", resp)

        mock_conn.execute.assert_called_once()
        params = mock_conn.execute.call_args[0][1]
        self.assertEqual(params[0], "agent_memory")
        self.assertEqual(params[2], "gemini-3.8-flash")
        self.assertEqual(params[3], 120)  # input_tokens
        self.assertEqual(params[5], 45)   # output_tokens
        self.assertEqual(params[6], 80)   # thinking_tokens

    def test_usage_meter_add_call(self):
        client = mock.MagicMock()
        meter = UsageMeter(client)
        meter.add_call(
            {"prompt_eval_count": 80, "eval_count": 40},
            model="ollama:qwen2.5:14b",
            label="test_meter_label",
            source="agent_wire",
        )

        totals = meter.totals()
        self.assertEqual(totals["input_tokens"], 80)
        self.assertEqual(totals["output_tokens"], 40)
        self.assertEqual(totals["calls"], 1)


class CallerIntegrationTests(unittest.TestCase):
    # 1. agent_memory tests
    @mock.patch("requests.post")
    def test_agent_memory_llm_text_ollama(self, mock_post):
        mock_resp = mock.MagicMock()
        mock_resp.json.return_value = {
            "model": "qwen2.5:14b",
            "response": "- STANCE: Panthers will make playoffs",
            "prompt_eval_count": 110,
            "eval_count": 25,
        }
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        with mock.patch("engines.llm_provider.log_usage") as mock_log:
            with mock.patch.dict(os.environ, {"LLM_PROVIDER_AGENT_MEMORY": "ollama"}):
                text = llm_text("Extract takes prompt", label="takes #123")

        self.assertEqual(text, "- STANCE: Panthers will make playoffs")
        mock_log.assert_called_once()
        self.assertEqual(mock_log.call_args[1]["source"], "agent_memory")
        self.assertEqual(mock_log.call_args[1]["model"], "ollama/qwen2.5:14b")

    def test_agent_memory_gemini_text_backwards_compatible(self):
        mock_client = make_mock_gemini_client("Gemini takes text")
        with mock.patch("engines.llm_provider.log_response"):
            text = gemini_text(
                api_key="fake_key",
                model="gemini-3.8-flash",
                prompt="Extract takes",
                label="takes #456",
                client=mock_client,
            )
        self.assertEqual(text, "Gemini takes text")
        mock_client.models.generate_content.assert_called_once()

    # 2. gm_llm tests
    @mock.patch("requests.post")
    def test_gm_llm_make_llm_ollama(self, mock_post):
        mock_resp = mock.MagicMock()
        mock_resp.json.return_value = {
            "model": "qwen2.5:14b",
            "response": "GM Rationale for trade",
            "prompt_eval_count": 85,
            "eval_count": 30,
        }
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        with mock.patch("engines.llm_provider.log_usage"):
            llm = make_llm("gm_rationale", model="ollama:qwen2.5:14b")
            self.assertIsInstance(llm, ProviderText)
            result = llm("Trade context prompt")
            self.assertEqual(result, "GM Rationale for trade")
            flush_res = llm.flush()
            self.assertEqual(flush_res["calls"], 1)
            self.assertEqual(flush_res["input_tokens"], 85)
            self.assertEqual(flush_res["output_tokens"], 30)

    @mock.patch("requests.post")
    def test_gm_llm_fallback_on_unreachable(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("Ollama offline")
        mock_client = make_mock_gemini_client("Gemini trade rationale fallback")

        with mock.patch("engines.llm_provider.log_usage"):
            llm = make_llm("gm_rationale", model="ollama:qwen2.5:14b", client=mock_client)
            result = llm("Trade prompt")

        self.assertEqual(result, "Gemini trade rationale fallback")

    # 3. social_writer tests
    @mock.patch("requests.post")
    def test_social_writer_write_post_ollama_success(self, mock_post):
        mock_resp = mock.MagicMock()
        mock_resp.json.return_value = {
            "model": "qwen2.5:14b",
            "message": {"role": "assistant", "content": "What a win by Carolina! #KeepPounding"},
            "prompt_eval_count": 140,
            "eval_count": 22,
        }
        mock_resp.raise_for_status.return_value = None
        mock_post.return_value = mock_resp

        with mock.patch("db.llm_usage.log_usage") as mock_log, mock.patch("engines.llm_provider.log_usage"):
            res = write_post(
                client=None,
                model="ollama:qwen2.5:14b",
                system="You are a fan",
                prompt="Write a post about week 5",
            )

        self.assertEqual(res["calls"], 1)
        self.assertEqual(res["text"], "What a win by Carolina! #KeepPounding")
        self.assertEqual(res["cost_usd"], 0.0)
        self.assertEqual(res["usage"]["input"], 140)
        self.assertEqual(res["usage"]["output"], 22)
        mock_log.assert_called_once()
        self.assertEqual(mock_log.call_args[1]["model"], "ollama/qwen2.5:14b")

    @mock.patch("requests.post")
    def test_social_writer_write_post_ollama_rewrite_on_length(self, mock_post):
        # 1st call returns text > 280 chars, 2nd call returns short text.
        # Must stay on Ollama for rewrite! (Never fallback on validation issue)
        resp1 = mock.MagicMock()
        resp1.json.return_value = {
            "model": "qwen2.5:14b",
            "message": {"role": "assistant", "content": "A" * 290},
            "prompt_eval_count": 100,
            "eval_count": 75,
        }
        resp1.raise_for_status.return_value = None

        resp2 = mock.MagicMock()
        resp2.json.return_value = {
            "model": "qwen2.5:14b",
            "message": {"role": "assistant", "content": "Short rewritten post #KeepPounding"},
            "prompt_eval_count": 180,
            "eval_count": 20,
        }
        resp2.raise_for_status.return_value = None

        mock_post.side_effect = [resp1, resp2]

        with mock.patch("db.llm_usage.log_usage"), mock.patch("engines.llm_provider.log_usage"):
            res = write_post(
                client=None,
                model="ollama:qwen2.5:14b",
                system="You are a fan",
                prompt="Write a post",
            )

        self.assertEqual(res["calls"], 2)
        self.assertEqual(res["text"], "Short rewritten post #KeepPounding")
        self.assertEqual(mock_post.call_count, 2)
        self.assertEqual(res["cost_usd"], 0.0)

    @mock.patch("requests.post")
    def test_social_writer_fallback_to_gemini(self, mock_post):
        mock_post.side_effect = requests.exceptions.ConnectionError("Ollama down")
        mock_client = make_mock_gemini_client("Gemini fallback post #NFL")

        res = write_post(
            client=mock_client,
            model="ollama:qwen2.5:14b",
            system="You are a beat writer",
            prompt="Write a post",
        )

        self.assertEqual(res["text"], "Gemini fallback post #NFL")
        mock_client.models.generate_content.assert_called_once()

    def test_cost_usd_zero_for_ollama(self):
        usage = {"input": 1000, "cached": 0, "output": 500, "thinking": 0}
        self.assertEqual(cost_usd("ollama/qwen2.5:14b", usage), 0.0)
        self.assertEqual(cost_usd("ollama:qwen2.5:14b", usage), 0.0)
