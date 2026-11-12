"""
Unit tests for the OpenAI-Compatible Tool Loop and Schema Converter.
(docs/done/PC_MIGRATION_PLAN.md §7.3, §7.4; PRE_PC_WORK_2.md Item A6)

Verifies:
1. Tool schema conversion from Python callables, docstrings, type annotations, LangChain tools, and Pydantic models.
2. Multi-turn OpenAI tool calling execution, callback invocation, argument parsing, and tool response tracking.
3. Tool error handling (exceptions raised by tools safely handled without loop crash).
4. Malformed JSON argument handling.
5. Max steps termination limit.
6. Token usage accumulation (prompt, completion, cached, reasoning).
7. Fallback synthesis prompt when model returns empty text after research.
8. Encapsulated Gemini tool loop execution and fallback synthesis.
9. Router dispatching between Gemini and OpenAI-compatible providers.
10. OpenCode Go provider: endpoints, the `deepseek` alias, model aliases, session header,
    usage cost capture and the allowance ledger (OPENCODE_GO_PIPELINE_PLAN.md U1).
"""
import collections
import contextlib
import io
import json
import os
import tempfile
import unittest
from typing import Any, Dict, List, Literal, Optional, Union
from unittest import mock

import pydantic
from langchain_core.tools import tool

from newsroom.tool_loop import (
    ToolLoopResult,
    execute_agent_tool_loop,
    python_func_to_openai_tool,
    resolve_tool_loop_provider,
    run_gemini_tool_loop,
    run_openai_tool_loop,
)
from newsroom.tool_loop.schema import parse_docstring, python_type_to_json_schema


# ---------------------------------------------------------------------------
# Mock OpenAI Structures
# ---------------------------------------------------------------------------

class MockFunction:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class MockToolCall:
    def __init__(self, id: str, name: str, arguments: str):
        self.id = id
        self.type = "function"
        self.function = MockFunction(name, arguments)


class MockMessage:
    def __init__(self, content: Optional[str] = None, tool_calls: Optional[List[MockToolCall]] = None,
                 reasoning_content: Optional[str] = None):
        self.content = content
        self.tool_calls = tool_calls or []
        if reasoning_content is not None:
            self.reasoning_content = reasoning_content


class MockChoice:
    def __init__(self, message: MockMessage, finish_reason: str = "stop"):
        self.message = message
        self.finish_reason = finish_reason


class MockUsageDetails:
    def __init__(self, cached_tokens: int = 0, reasoning_tokens: int = 0):
        self.cached_tokens = cached_tokens
        self.reasoning_tokens = reasoning_tokens


class MockUsage:
    def __init__(self, prompt_tokens: int = 15, completion_tokens: int = 25, cached_tokens: int = 5, reasoning_tokens: int = 0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens
        self.prompt_tokens_details = MockUsageDetails(cached_tokens=cached_tokens)
        self.completion_tokens_details = MockUsageDetails(reasoning_tokens=reasoning_tokens)


class MockGoUsage(MockUsage):
    """OpenCode Go's usage: the token counts plus ``cost`` and ``usage.opencode``."""

    def __init__(self, *args, cost: float = 0.0, allowance: Optional[float] = 1.2e-05,
                 list_cost: Optional[float] = 2.0e-05, billing_source: str = "go", **kwargs):
        super().__init__(*args, **kwargs)
        self.cost = cost
        self.opencode = {"allowance": allowance, "list_cost": list_cost,
                         "billing_source": billing_source,
                         "tokens": {"input": 1000, "output": 20}}


class MockResponse:
    def __init__(self, message: MockMessage, usage: Optional[MockUsage] = None,
                 finish_reason: str = "stop"):
        self.choices = [MockChoice(message, finish_reason=finish_reason)]
        self.usage = usage or MockUsage()


class MockOpenAIClient:
    def __init__(self, responses: List[MockResponse]):
        self.responses = list(responses)
        self.call_history: List[Dict[str, Any]] = []
        self.chat = mock.MagicMock()
        self.chat.completions.create = self._create

    def _create(self, **kwargs):
        self.call_history.append(kwargs)
        if not self.responses:
            raise RuntimeError("MockOpenAIClient: No more mocked responses remaining.")
        return self.responses.pop(0)


# ---------------------------------------------------------------------------
# Tests: Tool Schema Converter
# ---------------------------------------------------------------------------

class TestToolSchemaConverter(unittest.TestCase):
    def test_basic_function_schema(self):
        def sample_tool(name: str, age: int, score: float, active: bool = True) -> str:
            """Sample tool description."""
            return f"{name}:{age}:{score}:{active}"

        schema = python_func_to_openai_tool(sample_tool)
        self.assertEqual(schema["type"], "function")
        fn = schema["function"]
        self.assertEqual(fn["name"], "sample_tool")
        self.assertEqual(fn["description"], "Sample tool description.")
        params = fn["parameters"]
        self.assertEqual(params["type"], "object")

        props = params["properties"]
        self.assertEqual(props["name"]["type"], "string")
        self.assertEqual(props["age"]["type"], "integer")
        self.assertEqual(props["score"]["type"], "number")
        self.assertEqual(props["active"]["type"], "boolean")

        # Required check: name, age, score are required; active has default
        self.assertEqual(sorted(params["required"]), ["age", "name", "score"])

    def test_google_docstring_parsing(self):
        def get_team_stats(team_abbr: str, season: int = 2026) -> str:
            """Fetch comprehensive efficiency ratings for an NFL franchise.

            Args:
                team_abbr (str): Three-letter team abbreviation (e.g. 'KC', 'BAL').
                season: Season calendar year.
            """
            return f"{team_abbr}:{season}"

        schema = python_func_to_openai_tool(get_team_stats)
        fn = schema["function"]
        self.assertEqual(fn["name"], "get_team_stats")
        self.assertEqual(fn["description"], "Fetch comprehensive efficiency ratings for an NFL franchise.")

        props = fn["parameters"]["properties"]
        self.assertIn("Three-letter team abbreviation", props["team_abbr"]["description"])
        self.assertIn("Season calendar year", props["season"]["description"])
        self.assertEqual(fn["parameters"]["required"], ["team_abbr"])

    def test_sphinx_docstring_parsing(self):
        def compute_margin(game_id: int, include_overtime: bool = False) -> str:
            """Calculate final score margin.

            :param game_id: Canonical game identifier.
            :param include_overtime: Include overtime period points.
            """
            return f"{game_id}:{include_overtime}"

        schema = python_func_to_openai_tool(compute_margin)
        props = schema["function"]["parameters"]["properties"]
        self.assertEqual(props["game_id"]["description"], "Canonical game identifier.")
        self.assertEqual(props["include_overtime"]["description"], "Include overtime period points.")

    def test_complex_types_and_literals(self):
        def advanced_tool(
            tags: List[str],
            metadata: Dict[str, Any],
            filter_mode: Literal["STRICT", "LENIENT"] = "STRICT",
            limit: Optional[int] = 10,
        ) -> str:
            """Test complex type mapping."""
            return "ok"

        schema = python_func_to_openai_tool(advanced_tool)
        props = schema["function"]["parameters"]["properties"]
        self.assertEqual(props["tags"]["type"], "array")
        self.assertEqual(props["tags"]["items"]["type"], "string")
        self.assertEqual(props["metadata"]["type"], "object")
        self.assertEqual(props["filter_mode"]["type"], "string")
        self.assertEqual(props["filter_mode"]["enum"], ["STRICT", "LENIENT"])
        self.assertEqual(props["limit"]["type"], "integer")

    def test_varargs_and_kwargs_excluded(self):
        def func_with_args(player_id: int, *args, **kwargs) -> str:
            """Tool with varargs."""
            return str(player_id)

        schema = python_func_to_openai_tool(func_with_args)
        props = schema["function"]["parameters"]["properties"]
        self.assertIn("player_id", props)
        self.assertNotIn("args", props)
        self.assertNotIn("kwargs", props)
        self.assertEqual(schema["function"]["parameters"]["required"], ["player_id"])

    def test_pydantic_model_class(self):
        class PlayerFilter(pydantic.BaseModel):
            """Filter criteria for player database lookup."""
            position: str
            min_ovr: int = 80
            conference: Optional[str] = None

        schema = python_func_to_openai_tool(PlayerFilter)
        fn = schema["function"]
        self.assertEqual(fn["name"], "PlayerFilter")
        self.assertIn("Filter criteria", fn["description"])
        props = fn["parameters"]["properties"]
        self.assertEqual(props["position"]["type"], "string")
        self.assertEqual(props["min_ovr"]["type"], "integer")
        self.assertIn("position", fn["parameters"]["required"])

    def test_langchain_tool_conversion(self):
        @tool
        def query_injuries(team: str, week: int = 1) -> str:
            """Lookup player injury report for a team.

            Args:
                team: Team code.
                week: NFL schedule week.
            """
            return f"Injuries for {team} in week {week}"

        schema = python_func_to_openai_tool(query_injuries)
        fn = schema["function"]
        self.assertEqual(fn["name"], "query_injuries")
        self.assertTrue("Lookup player injury report" in fn["description"])
        props = fn["parameters"]["properties"]
        self.assertEqual(props["team"]["type"], "string")
        self.assertEqual(props["week"]["type"], "integer")

    def test_preformatted_dict_passthrough(self):
        raw_dict = {
            "type": "function",
            "function": {
                "name": "custom_raw_tool",
                "description": "Pre-formatted tool",
                "parameters": {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]},
            },
        }
        res = python_func_to_openai_tool(raw_dict)
        self.assertEqual(res, raw_dict)


# ---------------------------------------------------------------------------
# Tests: OpenAI Multi-Turn Tool Loop
# ---------------------------------------------------------------------------

class TestOpenAIToolLoop(unittest.TestCase):
    def test_direct_response_no_tools_invoked(self):
        client = MockOpenAIClient([
            MockResponse(MockMessage(content="No tools needed, here is the article."))
        ])

        def dummy_tool(x: int) -> str:
            return str(x)

        result = run_openai_tool_loop(
            client=client,
            model="deepseek-chat",
            system_instruction="System prompt",
            prompt="Write article",
            tools=[dummy_tool],
        )

        self.assertEqual(result.text, "No tools needed, here is the article.")
        self.assertEqual(result.iterations, 1)
        self.assertEqual(result.total_tool_calls, 0)
        self.assertIsNone(result.error)
        self.assertEqual(len(client.call_history), 1)

    def test_multiturn_tool_calling(self):
        # Turn 1: Model requests tool call
        resp1 = MockResponse(MockMessage(
            content=None,
            tool_calls=[MockToolCall("call_001", "get_stats", json.dumps({"player": "Mahomes", "season": 2026}))]
        ))
        # Turn 2: Model outputs final article with data
        resp2 = MockResponse(MockMessage(
            content="Mahomes completed a stellar 2026 campaign with 4,200 yards."
        ))

        client = MockOpenAIClient([resp1, resp2])

        tool_called_args = []
        def get_stats(player: str, season: int = 2026) -> str:
            """Fetch player stats."""
            tool_called_args.append((player, season))
            return f"{player} recorded 4,200 pass yards in {season}."

        result = run_openai_tool_loop(
            client=client,
            model="deepseek-chat",
            system_instruction="You are a beat writer.",
            prompt="Analyze Mahomes in 2026.",
            tools=[get_stats],
        )

        self.assertEqual(result.text, "Mahomes completed a stellar 2026 campaign with 4,200 yards.")
        self.assertEqual(result.iterations, 2)
        self.assertEqual(result.tool_calls_count, {"get_stats": 1})
        self.assertEqual(result.total_tool_calls, 1)
        self.assertEqual(tool_called_args, [("Mahomes", 2026)])
        self.assertEqual(len(client.call_history), 2)

        # Inspect messages in second call
        second_call_messages = client.call_history[1]["messages"]
        # system + user + assistant (with tool_calls) + tool (output)
        self.assertEqual(len(second_call_messages), 4)
        self.assertEqual(second_call_messages[2]["role"], "assistant")
        self.assertEqual(second_call_messages[3]["role"], "tool")
        self.assertEqual(second_call_messages[3]["tool_call_id"], "call_001")
        self.assertIn("Mahomes recorded 4,200", second_call_messages[3]["content"])

    def test_parallel_tool_calls_in_single_turn(self):
        resp1 = MockResponse(MockMessage(
            content="Checking both teams...",
            tool_calls=[
                MockToolCall("call_1", "get_box", json.dumps({"game_id": 101})),
                MockToolCall("call_2", "get_box", json.dumps({"game_id": 102})),
            ]
        ))
        resp2 = MockResponse(MockMessage(content="Comparison of Game 101 and 102 complete."))

        client = MockOpenAIClient([resp1, resp2])

        invocations = []
        def get_box(game_id: int) -> str:
            invocations.append(game_id)
            return f"Box score for game {game_id}"

        result = run_openai_tool_loop(
            client=client,
            model="deepseek-chat",
            system_instruction="Analyst",
            prompt="Compare games 101 and 102",
            tools=[get_box],
        )

        self.assertEqual(result.text, "Comparison of Game 101 and 102 complete.")
        self.assertEqual(result.tool_calls_count, {"get_box": 2})
        self.assertEqual(invocations, [101, 102])
        self.assertEqual(result.iterations, 2)

    def test_tool_raising_exception_handled_gracefully(self):
        # Tool raises an exception, model receives error in tool message and concludes anyway
        resp1 = MockResponse(MockMessage(
            content=None,
            tool_calls=[MockToolCall("call_err", "failing_tool", json.dumps({"key": "val"}))]
        ))
        resp2 = MockResponse(MockMessage(
            content="Tool failed with error, but fallback narrative generated."
        ))

        client = MockOpenAIClient([resp1, resp2])

        def failing_tool(key: str) -> str:
            raise ConnectionError("Database cluster unreachable")

        result = run_openai_tool_loop(
            client=client,
            model="deepseek-chat",
            system_instruction="Writer",
            prompt="Investigate",
            tools=[failing_tool],
        )

        self.assertEqual(result.text, "Tool failed with error, but fallback narrative generated.")
        self.assertEqual(result.tool_calls_count, {"failing_tool": 1})
        self.assertIsNone(result.error)

        # Check tool message content
        second_call_messages = client.call_history[1]["messages"]
        tool_msg = second_call_messages[-1]
        self.assertEqual(tool_msg["role"], "tool")
        self.assertIn("Tool Error: Database cluster unreachable", tool_msg["content"])

    def test_malformed_json_arguments(self):
        resp1 = MockResponse(MockMessage(
            content=None,
            tool_calls=[MockToolCall("call_bad_json", "test_tool", "{malformed_json: missing_quotes")]
        ))
        resp2 = MockResponse(MockMessage(content="Handled malformed arguments."))

        client = MockOpenAIClient([resp1, resp2])

        def test_tool(x: int = 1) -> str:
            return "ok"

        result = run_openai_tool_loop(
            client=client,
            model="deepseek-chat",
            system_instruction="",
            prompt="Run",
            tools=[test_tool],
        )

        self.assertEqual(result.text, "Handled malhandled arguments." if False else "Handled malformed arguments.")
        self.assertEqual(result.tool_calls_count, {})  # Tool was not called due to bad JSON
        tool_msg = client.call_history[1]["messages"][-1]
        self.assertIn("Failed to parse arguments as JSON", tool_msg["content"])

    def test_unrecognized_tool_name(self):
        resp1 = MockResponse(MockMessage(
            content=None,
            tool_calls=[MockToolCall("call_ghost", "non_existent_tool", "{}")]
        ))
        resp2 = MockResponse(MockMessage(content="Handled missing tool."))

        client = MockOpenAIClient([resp1, resp2])

        def existing_tool() -> str:
            return "ok"

        result = run_openai_tool_loop(
            client=client,
            model="deepseek-chat",
            system_instruction="",
            prompt="Run",
            tools=[existing_tool],
        )

        self.assertEqual(result.text, "Handled missing tool.")
        tool_msg = client.call_history[1]["messages"][-1]
        self.assertIn("is not recognized", tool_msg["content"])

    def test_max_steps_termination(self):
        # Client always responds with a tool call
        looping_responses = [
            MockResponse(MockMessage(
                content=None,
                tool_calls=[MockToolCall(f"call_{i}", "ping", json.dumps({"i": i}))]
            ))
            for i in range(10)
        ]
        client = MockOpenAIClient(looping_responses)

        def ping(i: int) -> str:
            return f"pong {i}"

        result = run_openai_tool_loop(
            client=client,
            model="deepseek-chat",
            system_instruction="",
            prompt="Infinite tool call test",
            tools=[ping],
            max_steps=4,
        )

        self.assertEqual(result.iterations, 4)
        self.assertEqual(result.total_tool_calls, 4)
        self.assertIn("Maximum steps (4) reached", result.error)

    def test_fallback_synthesis_when_content_empty(self):
        # Turn 1: tool call
        resp1 = MockResponse(MockMessage(
            content=None,
            tool_calls=[MockToolCall("call_1", "lookup", "{}")]
        ))
        # Turn 2: tool loop completed without content (empty string)
        resp2 = MockResponse(MockMessage(content=""))
        # Turn 3: fallback synthesis call prompt answered
        resp3 = MockResponse(MockMessage(content="Synthesized final article from collected research."))

        client = MockOpenAIClient([resp1, resp2, resp3])

        def lookup() -> str:
            return "Secret research data"

        result = run_openai_tool_loop(
            client=client,
            model="deepseek-chat",
            system_instruction="",
            prompt="Write something",
            tools=[lookup],
        )

        self.assertEqual(result.text, "Synthesized final article from collected research.")
        self.assertEqual(len(client.call_history), 3)
        # Check that fallback message asked for final article
        synthesis_prompt = client.call_history[2]["messages"][-1]["content"]
        self.assertIn("write your complete final article", synthesis_prompt)

    def test_usage_accumulation(self):
        resp1 = MockResponse(
            MockMessage(tool_calls=[MockToolCall("c1", "calc", "{}")]),
            MockUsage(prompt_tokens=100, completion_tokens=20, cached_tokens=50, reasoning_tokens=10)
        )
        resp2 = MockResponse(
            MockMessage(content="Done"),
            MockUsage(prompt_tokens=150, completion_tokens=80, cached_tokens=60, reasoning_tokens=30)
        )
        client = MockOpenAIClient([resp1, resp2])

        def calc() -> str:
            return "42"

        result = run_openai_tool_loop(
            client=client,
            model="deepseek-chat",
            system_instruction="",
            prompt="Calculate",
            tools=[calc],
        )

        self.assertEqual(result.usage["input_tokens"], 250)
        self.assertEqual(result.usage["output_tokens"], 100)
        self.assertEqual(result.usage["cached_tokens"], 110)
        self.assertEqual(result.usage["thinking_tokens"], 40)

    def test_deepseek_cache_hit_miss_and_reasoning_usage(self):
        from newsroom.tool_loop.openai_loop import extract_openai_usage
        # DeepSeek's example responses put the cache counters at the top level.
        usage = extract_openai_usage({
            "prompt_tokens": 1000,
            "prompt_cache_hit_tokens": 600,
            "prompt_cache_miss_tokens": 400,
            "completion_tokens": 300,
            "completion_tokens_details": {"reasoning_tokens": 200},
        })
        self.assertEqual(usage, {"input_tokens": 1000, "cached_tokens": 600,
                                 "uncached_tokens": 400, "output_tokens": 300,
                                 "thinking_tokens": 200,
                                 # Go's four optional fields are None when unreported
                                 "cost_usd": None, "list_cost_usd": None,
                                 "allowance_usd": None, "billing_source": None})
        # Its schema also nests them under prompt_tokens_details.
        nested = extract_openai_usage({
            "prompt_tokens": 100,
            "prompt_tokens_details": {"prompt_cache_hit_tokens": 25,
                                      "prompt_cache_miss_tokens": 75},
            "completion_tokens": 10,
            "completion_tokens_details": {"reasoning_tokens": 4},
        })
        self.assertEqual((nested["cached_tokens"], nested["uncached_tokens"]), (25, 75))
        # Without an explicit miss count, uncached is prompt minus cached.
        derived = extract_openai_usage({"prompt_tokens": 100,
                                        "prompt_tokens_details": {"cached_tokens": 40},
                                        "completion_tokens": 5})
        self.assertEqual(derived["uncached_tokens"], 60)

    def test_usage_calls_expose_each_request(self):
        resp1 = MockResponse(
            MockMessage(tool_calls=[MockToolCall("c1", "calc", "{}")]),
            MockUsage(prompt_tokens=100, completion_tokens=20, cached_tokens=50, reasoning_tokens=10)
        )
        resp2 = MockResponse(
            MockMessage(content="Done"),
            MockUsage(prompt_tokens=150, completion_tokens=80, cached_tokens=60, reasoning_tokens=30)
        )
        client = MockOpenAIClient([resp1, resp2])

        def calc() -> str:
            """Compute."""
            return "42"

        result = run_openai_tool_loop(client=client, model="deepseek-flash",
                                      system_instruction="", prompt="Calculate", tools=[calc])
        self.assertEqual(len(result.usage_calls), 2)
        self.assertEqual(result.usage_calls[0]["input_tokens"], 100)
        self.assertEqual(result.usage_calls[0]["uncached_tokens"], 50)
        self.assertEqual(result.usage_calls[0]["thinking_tokens"], 10)
        self.assertEqual(result.usage_calls[1]["output_tokens"], 80)
        self.assertTrue(all("latency_s" in call for call in result.usage_calls))
        self.assertEqual(result.usage["uncached_tokens"], 140)

    def test_usage_calls_carry_finish_reason(self):
        resp1 = MockResponse(MockMessage(tool_calls=[MockToolCall("c1", "calc", "{}")]),
                             finish_reason="tool_calls")
        resp2 = MockResponse(MockMessage(content="Cut off mid-senten"),
                             finish_reason="length")
        client = MockOpenAIClient([resp1, resp2])

        def calc() -> str:
            """Compute."""
            return "42"

        result = run_openai_tool_loop(client=client, model="deepseek-flash",
                                      system_instruction="", prompt="Calculate", tools=[calc])
        self.assertEqual([c["finish_reason"] for c in result.usage_calls], ["tool_calls", "length"])
        self.assertEqual(result.text, "Cut off mid-senten")

    def test_reasoning_content_replayed_on_tool_turns(self):
        resp1 = MockResponse(MockMessage(
            content=None,
            tool_calls=[MockToolCall("call_1", "lookup", "{}")],
            reasoning_content="I should look up the record first.",
        ))
        resp2 = MockResponse(MockMessage(content="Article."))
        client = MockOpenAIClient([resp1, resp2])

        def lookup() -> str:
            """Look up."""
            return "evidence"

        result = run_openai_tool_loop(client=client, model="deepseek-flash",
                                      system_instruction="", prompt="Write", tools=[lookup])
        self.assertEqual(result.text, "Article.")
        assistant = [m for m in client.call_history[1]["messages"] if m["role"] == "assistant"][0]
        self.assertEqual(assistant["reasoning_content"],
                         "I should look up the record first.")

    def test_no_reasoning_content_key_when_provider_omits_it(self):
        resp1 = MockResponse(MockMessage(
            content=None, tool_calls=[MockToolCall("call_1", "lookup", "{}")]))
        resp2 = MockResponse(MockMessage(content="Article."))
        client = MockOpenAIClient([resp1, resp2])

        def lookup() -> str:
            """Look up."""
            return "evidence"

        run_openai_tool_loop(client=client, model="gpt", system_instruction="",
                             prompt="Write", tools=[lookup])
        assistant = [m for m in client.call_history[1]["messages"] if m["role"] == "assistant"][0]
        self.assertNotIn("reasoning_content", assistant)

    def test_max_tokens_forwarded_to_every_request(self):
        resp1 = MockResponse(MockMessage(
            tool_calls=[MockToolCall("call_1", "lookup", "{}")]))
        resp2 = MockResponse(MockMessage(content="Article."))
        client = MockOpenAIClient([resp1, resp2])

        def lookup() -> str:
            """Look up."""
            return "evidence"

        run_openai_tool_loop(client=client, model="deepseek-flash", system_instruction="",
                             prompt="Write", tools=[lookup], max_tokens=8192)
        self.assertEqual([c.get("max_tokens") for c in client.call_history], [8192, 8192])
        self.assertEqual([c["temperature"] for c in client.call_history], [0.7, 0.7])

    def test_max_tokens_omitted_when_not_requested(self):
        client = MockOpenAIClient([MockResponse(MockMessage(content="Article."))])
        run_openai_tool_loop(client=client, model="gpt", system_instruction="",
                             prompt="Write", tools=[])
        self.assertNotIn("max_tokens", client.call_history[0])

    def test_api_network_failure(self):
        client = mock.MagicMock()
        client.chat.completions.create.side_effect = RuntimeError("Connection reset by peer")

        result = run_openai_tool_loop(
            client=client,
            model="deepseek-chat",
            system_instruction="",
            prompt="Fail test",
            tools=[],
        )

        self.assertIn("Connection reset by peer", result.error)
        self.assertIn("Error calling OpenAI API", result.text)


# ---------------------------------------------------------------------------
# Tests: Gemini Tool Loop
# ---------------------------------------------------------------------------

class TestGeminiToolLoop(unittest.TestCase):
    def test_gemini_missing_api_key_returns_error(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("pipeline.notify.env", return_value=None):
            result = run_gemini_tool_loop(
                client=None,
                model="gemini-3.8-flash",
                system_instruction="System",
                prompt="Prompt",
                tools=[],
            )
            self.assertEqual(result.error, "missing GEMINI_API_KEY")
            self.assertIn("Missing GEMINI_API_KEY", result.text)

    def test_gemini_execution_success(self):
        fake_response = mock.MagicMock()
        fake_response.text = "Gemini generated article."
        fake_response.usage_metadata = mock.MagicMock(
            prompt_token_count=120,
            candidates_token_count=60,
            cached_content_token_count=20,
            thoughts_token_count=10,
        )

        fake_chat = mock.MagicMock()
        fake_chat.send_message.return_value = fake_response

        client = mock.MagicMock()
        client.chats.create.return_value = fake_chat

        def sample_tool(team: str) -> str:
            return f"Stats for {team}"

        result = run_gemini_tool_loop(
            client=client,
            model="gemini-3.8-flash",
            system_instruction="You are a reporter.",
            prompt="Cover the game.",
            tools=[sample_tool],
        )

        self.assertEqual(result.text, "Gemini generated article.")
        self.assertIsNone(result.error)
        self.assertEqual(result.usage["input_tokens"], 120)
        self.assertEqual(result.usage["output_tokens"], 60)
        self.assertEqual(result.usage["cached_tokens"], 20)
        self.assertEqual(result.usage["thinking_tokens"], 10)
        client.chats.create.assert_called_once()
        fake_chat.send_message.assert_called_once_with("Cover the game.")

    def test_gemini_fallback_synthesis(self):
        # Initial call returns empty text
        initial_resp = mock.MagicMock()
        initial_resp.text = ""
        initial_resp.candidates = []
        initial_resp.usage_metadata = mock.MagicMock(
            prompt_token_count=1000,
            candidates_token_count=20,
            cached_content_token_count=0,
            thoughts_token_count=5,
        )

        # Followup call returns synthesized text
        followup_resp = mock.MagicMock()
        followup_resp.text = "Synthesized markdown from Gemini."
        followup_resp.usage_metadata = mock.MagicMock(
            prompt_token_count=200,
            candidates_token_count=80,
            cached_content_token_count=0,
            thoughts_token_count=0,
        )

        fake_chat = mock.MagicMock()
        fake_chat.send_message.side_effect = [initial_resp, followup_resp]

        client = mock.MagicMock()
        client.chats.create.return_value = fake_chat

        result = run_gemini_tool_loop(
            client=client,
            model="gemini-3.8-flash",
            system_instruction="System",
            prompt="Prompt",
            tools=[],
        )

        self.assertEqual(result.text, "Synthesized markdown from Gemini.")
        self.assertEqual(fake_chat.send_message.call_count, 2)
        self.assertEqual(result.iterations, 2)
        # Usage covers both the AFC turn and the synthesis follow-up
        self.assertEqual(result.usage["input_tokens"], 1200)
        self.assertEqual(result.usage["output_tokens"], 100)
        self.assertEqual(result.usage["thinking_tokens"], 5)


# ---------------------------------------------------------------------------
# Tests: Router
# ---------------------------------------------------------------------------

class TestToolLoopRouter(unittest.TestCase):
    def test_provider_resolution(self):
        self.assertEqual(resolve_tool_loop_provider("gemini"), "gemini")
        self.assertEqual(resolve_tool_loop_provider("openai"), "openai")
        self.assertEqual(resolve_tool_loop_provider("deepseek"), "deepseek")
        self.assertEqual(resolve_tool_loop_provider("glm"), "glm")
        self.assertEqual(resolve_tool_loop_provider("ollama"), "ollama")
        self.assertEqual(resolve_tool_loop_provider("vllm"), "vllm")

        # Inferred from model
        self.assertEqual(resolve_tool_loop_provider(model="deepseek-chat"), "deepseek")
        self.assertEqual(resolve_tool_loop_provider(model="ollama:qwen2.5:14b"), "ollama")
        self.assertEqual(resolve_tool_loop_provider(model="gemini-3.8-flash"), "gemini")
        self.assertEqual(resolve_tool_loop_provider(model="glm-4-plus"), "glm")
        # A bare id OpenCode Go serves is Go, never Gemini (AUDIT_QUEUE_INVENTORY.md R11):
        # the `glm`/`deepseek` substring whitelist missed every other Go family.
        self.assertEqual(resolve_tool_loop_provider(model="muse-spark-1.3-contributor"), "opencode-go")
        self.assertEqual(resolve_tool_loop_provider(model="glm-5.3-flash"), "glm")
        self.assertEqual(resolve_tool_loop_provider(model="qwen2.5:14b"), "ollama")   # an Ollama tag stays Ollama

        # Unsupported
        with self.assertRaises(ValueError):
            resolve_tool_loop_provider("anthropic_bedrock")

    def test_the_gemini_loop_routes_a_go_model_to_the_go_client(self):
        """AUDIT_QUEUE_INVENTORY.md R11: a Go id on the Gemini AFC loop never reaches Google's endpoint."""

        with mock.patch("newsroom.tool_loop.go_transport.go_client",
                        side_effect=ValueError("OPENCODE_GO_API_KEY is not set")) as go_client, \
                mock.patch("google.genai.Client", side_effect=AssertionError("a Gemini client was built")) as gemini:
            result = run_gemini_tool_loop(None, "muse-spark-1.3-contributor", "sys", "prompt", [])

        go_client.assert_called_once()
        gemini.assert_not_called()
        self.assertIn("OPENCODE_GO_API_KEY", result.error)

    def test_the_gemini_loop_still_needs_its_gemini_key(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("pipeline.notify.env", lambda name: None), \
                mock.patch("newsroom.tool_loop.go_transport.go_client") as go_client:
            result = run_gemini_tool_loop(None, "gemini-3.8-flash", "sys", "prompt", [])

        go_client.assert_not_called()
        self.assertEqual(result.error, "missing GEMINI_API_KEY")

    @mock.patch("newsroom.tool_loop.router.run_gemini_tool_loop")
    @mock.patch("newsroom.tool_loop.router.run_openai_tool_loop")
    def test_router_dispatches_correctly(self, mock_openai, mock_gemini):
        mock_gemini.return_value = ToolLoopResult(text="From Gemini")
        mock_openai.return_value = ToolLoopResult(text="From OpenAI")

        # Gemini dispatch
        res1 = execute_agent_tool_loop(
            provider="gemini",
            client="fake_gemini_client",
            model="gemini-3.8-flash",
            system_instruction="sys",
            prompt="prompt",
            tools=[],
            temperature=0.8,
        )
        self.assertEqual(res1.text, "From Gemini")
        mock_gemini.assert_called_once_with(
            client="fake_gemini_client",
            model="gemini-3.8-flash",
            system_instruction="sys",
            prompt="prompt",
            tools=[],
            temperature=0.8,
        )

        # DeepSeek dispatch
        res2 = execute_agent_tool_loop(
            provider="deepseek",
            client="fake_openai_client",
            model="deepseek-chat",
            system_instruction="sys",
            prompt="prompt",
            tools=[],
            max_steps=25,
        )
        self.assertEqual(res2.text, "From OpenAI")
        mock_openai.assert_called_once_with(
            client="fake_openai_client",
            model="deepseek-chat",
            system_instruction="sys",
            prompt="prompt",
            tools=[],
            provider="deepseek",
            max_steps=25,
        )

        # Ollama dispatch
        mock_openai.reset_mock()
        res3 = execute_agent_tool_loop(
            provider="ollama",
            client="fake_ollama_client",
            model="qwen2.5:14b",
            system_instruction="sys",
            prompt="prompt",
            tools=[],
        )
        self.assertEqual(res3.text, "From OpenAI")
        mock_openai.assert_called_once()

    @mock.patch("newsroom.tool_loop.router.run_openai_tool_loop")
    def test_router_strips_provider_prefix(self, mock_openai):
        mock_openai.return_value = ToolLoopResult(text="ok")
        execute_agent_tool_loop(None, "c", "ollama:qwen2.5:14b", "sys", "p", [])
        kwargs = mock_openai.call_args.kwargs
        self.assertEqual(kwargs["model"], "qwen2.5:14b")
        self.assertEqual(kwargs["provider"], "ollama")

        # A slash in an HF-style id that isn't the provider prefix is kept
        execute_agent_tool_loop("vllm", "c", "deepseek-ai/DeepSeek-V3", "sys", "p", [])
        self.assertEqual(mock_openai.call_args.kwargs["model"], "deepseek-ai/DeepSeek-V3")

    def test_unprefixed_ollama_tag_routes_to_ollama(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_tool_loop_provider(model="qwen2.5:14b"), "ollama")
            self.assertEqual(resolve_tool_loop_provider(model="gemma4:12b"), "ollama")
            self.assertEqual(resolve_tool_loop_provider(model="gemini-3.8-flash"), "gemini")

    def test_default_endpoint_per_provider(self):
        from newsroom.tool_loop.openai_loop import resolve_endpoint
        with mock.patch.dict(os.environ, {"OLLAMA_HOST": "localhost:11434"}, clear=True), \
             mock.patch("pipeline.notify.env", return_value=None):
            self.assertEqual(resolve_endpoint("ollama"), ("http://localhost:11434/v1", "local"))
            self.assertEqual(resolve_endpoint("vllm")[0], "http://localhost:8000/v1")
            # Hosted provider with no key is an error, not a dummy key
            with self.assertRaises(ValueError):
                resolve_endpoint("deepseek")
        # The direct DeepSeek API is the escape hatch: setting DEEPSEEK_BASE_URL by hand
        with mock.patch.dict(os.environ, {"DEEPSEEK_BASE_URL": "https://api.deepseek.com",
                                          "DEEPSEEK_API_KEY": "k"}, clear=True), \
             mock.patch("pipeline.notify.env", return_value=None):
            self.assertEqual(resolve_endpoint("deepseek"), ("https://api.deepseek.com", "k"))

    def test_openai_loop_missing_key_returns_error(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("pipeline.notify.env", return_value=None):
            # `deepseek` is a Go alias, so the missing key is Go's
            result = run_openai_tool_loop(None, "deepseek-chat", "s", "p", [], provider="deepseek")
        self.assertIn("OPENCODE_GO_API_KEY", result.error)
        with mock.patch.dict(os.environ, {"DEEPSEEK_BASE_URL": "https://api.deepseek.com"},
                             clear=True), \
             mock.patch("pipeline.notify.env", return_value=None):
            escaped = run_openai_tool_loop(None, "deepseek-chat", "s", "p", [], provider="deepseek")
        self.assertIn("DEEPSEEK_API_KEY", escaped.error)

    def test_provider_from_env_variable(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "vllm"}):
            self.assertEqual(resolve_tool_loop_provider(), "vllm")


class TestEdgeCasesAndRobustness(unittest.TestCase):
    def test_unannotated_function_schema(self):
        def raw_func(a, b=10):
            return a + b

        schema = python_func_to_openai_tool(raw_func)
        props = schema["function"]["parameters"]["properties"]
        self.assertEqual(props["a"]["type"], "string")
        self.assertEqual(props["b"]["type"], "string")
        self.assertEqual(schema["function"]["parameters"]["required"], ["a"])

    def test_function_with_no_docstring(self):
        def no_doc_func(val: int) -> int:
            return val * 2

        schema = python_func_to_openai_tool(no_doc_func)
        self.assertIn("Execute no_doc_func", schema["function"]["description"])

    def test_langchain_tool_in_openai_loop(self):
        @tool
        def add_numbers(x: int, y: int = 5) -> int:
            """Add two numbers together."""
            return x + y

        resp1 = MockResponse(MockMessage(
            tool_calls=[MockToolCall("c_add", "add_numbers", json.dumps({"x": 10, "y": 7}))]
        ))
        resp2 = MockResponse(MockMessage(content="The sum is 17."))

        client = MockOpenAIClient([resp1, resp2])
        result = run_openai_tool_loop(
            client=client,
            model="gpt-oss-20b",
            system_instruction="",
            prompt="Compute 10 + 7",
            tools=[add_numbers],
        )

        self.assertEqual(result.text, "The sum is 17.")
        self.assertEqual(result.tool_calls_count, {"add_numbers": 1})
        self.assertEqual(client.call_history[1]["messages"][-1]["content"], "17")

    def test_gemini_with_usage_meter_calls(self):
        client = mock.MagicMock()
        mock_meter = mock.MagicMock()
        mock_meter.calls = [
            {"input_tokens": 50, "output_tokens": 20, "cached_tokens": 10, "thinking_tokens": 5},
            {"input_tokens": 70, "output_tokens": 30, "cached_tokens": 15, "thinking_tokens": 10},
        ]
        client._usage_meter = mock_meter

        fake_chat = mock.MagicMock()
        fake_response = mock.MagicMock()
        fake_response.text = "Generated by Gemini with meter."
        fake_chat.send_message.return_value = fake_response
        client.chats.create.return_value = fake_chat

        result = run_gemini_tool_loop(
            client=client,
            model="gemini-3.8-flash",
            system_instruction="",
            prompt="Prompt",
            tools=[],
        )

        self.assertEqual(result.text, "Generated by Gemini with meter.")
        self.assertEqual(result.usage["input_tokens"], 120)
        self.assertEqual(result.usage["output_tokens"], 50)
        self.assertEqual(result.usage["cached_tokens"], 25)
        self.assertEqual(result.usage["thinking_tokens"], 15)


# ---------------------------------------------------------------------------
# Tests: OpenCode Go provider (OPENCODE_GO_PIPELINE_PLAN.md U1)
# ---------------------------------------------------------------------------

class MockDumpUsage:
    """A usage object whose extras only appear in ``model_dump()`` (an SDK version detail)."""

    def __init__(self, prompt_tokens: int = 10, completion_tokens: int = 5, data=None):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self._data = data or {}

    def model_dump(self):
        return dict(self._data, prompt_tokens=self.prompt_tokens,
                    completion_tokens=self.completion_tokens)


class MockExtraUsage:
    """A usage object whose extras only appear in ``model_extra``."""

    def __init__(self, prompt_tokens: int = 10, completion_tokens: int = 5, data=None):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.model_extra = data or {}


GO_ENV = {"OPENCODE_GO_API_KEY": "go-key"}


class TestOpenCodeGoProvider(unittest.TestCase):
    def test_provider_endpoint_entry(self):
        from newsroom.tool_loop.openai_loop import PROVIDER_ENDPOINTS
        self.assertEqual(PROVIDER_ENDPOINTS["opencode-go"],
                         ("OPENCODE_GO_BASE_URL",
                          "https://opencode.ai/inference/go/openai/v1",
                          "OPENCODE_GO_API_KEY"))

    def test_resolve_opencode_go_from_env(self):
        from newsroom.tool_loop.openai_loop import resolve_endpoint
        env = dict(GO_ENV, OPENCODE_GO_BASE_URL="https://example.test/inference/go/openai/v1")
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("pipeline.notify.env", return_value=None):
            self.assertEqual(resolve_endpoint("opencode-go"),
                             ("https://example.test/inference/go/openai/v1", "go-key"))
        with mock.patch.dict(os.environ, GO_ENV, clear=True), \
             mock.patch("pipeline.notify.env", return_value=None):
            self.assertEqual(resolve_endpoint("opencode-go"),
                             ("https://opencode.ai/inference/go/openai/v1", "go-key"))

    def test_resolve_opencode_go_without_key_raises(self):
        from newsroom.tool_loop.openai_loop import resolve_endpoint
        with mock.patch.dict(os.environ, {}, clear=True), \
             mock.patch("pipeline.notify.env", return_value=None), \
             self.assertRaises(ValueError) as raised:
            resolve_endpoint("opencode-go")
        self.assertIn("OPENCODE_GO_API_KEY", str(raised.exception))

    def test_deepseek_alias_resolves_to_go_by_default(self):
        from newsroom.tool_loop.openai_loop import resolve_endpoint
        with mock.patch.dict(os.environ, GO_ENV, clear=True), \
             mock.patch("pipeline.notify.env", return_value=None):
            self.assertEqual(resolve_endpoint("deepseek"),
                             ("https://opencode.ai/inference/go/openai/v1", "go-key"))

    def test_deepseek_escape_hatch_when_base_url_is_set(self):
        from newsroom.tool_loop.openai_loop import resolve_endpoint
        env = {"DEEPSEEK_BASE_URL": "https://api.deepseek.com", "DEEPSEEK_API_KEY": "dk",
               **GO_ENV}
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("pipeline.notify.env", return_value=None):
            self.assertEqual(resolve_endpoint("deepseek"), ("https://api.deepseek.com", "dk"))

    def _sent_model(self, model: str, provider: str) -> str:
        client = MockOpenAIClient([MockResponse(MockMessage(content="done"))])
        run_openai_tool_loop(client=client, model=model, system_instruction="",
                             prompt="p", tools=[], provider=provider)
        return client.call_history[0]["model"]

    def test_model_alias_for_both_go_providers(self):
        self.assertEqual(self._sent_model("deepseek-flash", "opencode-go"),
                         "deepseek-v4.1-flash")
        self.assertEqual(self._sent_model("deepseek-flash", "deepseek"),
                         "deepseek-v4.1-flash")

    def test_model_alias_passes_other_ids_and_providers_through(self):
        self.assertEqual(self._sent_model("deepseek-flash", "openai"), "deepseek-flash")
        self.assertEqual(self._sent_model("glm-4", "opencode-go"), "glm-4")
        self.assertEqual(self._sent_model("deepseek-v4.1-flash", "deepseek"),
                         "deepseek-v4.1-flash")

    def test_build_default_client_sends_session_header(self):
        import newsroom.tool_loop.openai_loop as openai_loop
        with mock.patch.dict(os.environ, GO_ENV, clear=True), \
             mock.patch("pipeline.notify.env", return_value=None), \
             mock.patch("openai.OpenAI") as fake_openai:
            openai_loop.build_default_client("opencode-go", max_retries=0)
        kwargs = fake_openai.call_args.kwargs
        self.assertEqual(kwargs["base_url"], "https://opencode.ai/inference/go/openai/v1")
        self.assertEqual(kwargs["api_key"], "go-key")
        session = kwargs["default_headers"]["x-opencode-session"]
        self.assertTrue(session.startswith("mu-pipeline-"))
        self.assertIn(str(os.getpid()), session)
        self.assertEqual(kwargs["max_retries"], 0)

    def test_build_default_client_honours_mu_go_session(self):
        import newsroom.tool_loop.openai_loop as openai_loop
        env = dict(GO_ENV, MU_GO_SESSION="mu-pipeline-test")
        with mock.patch.dict(os.environ, env, clear=True), \
             mock.patch("pipeline.notify.env", return_value=None), \
             mock.patch("openai.OpenAI") as fake_openai:
            openai_loop.build_default_client("deepseek")
        self.assertEqual(fake_openai.call_args.kwargs["default_headers"],
                         {"x-opencode-session": "mu-pipeline-test"})

    def test_build_default_client_leaves_local_providers_unchanged(self):
        import newsroom.tool_loop.openai_loop as openai_loop
        with mock.patch.dict(os.environ, {"OLLAMA_HOST": "localhost:11434"}, clear=True), \
             mock.patch("pipeline.notify.env", return_value=None), \
             mock.patch("openai.OpenAI") as fake_openai:
            openai_loop.build_default_client("ollama")
            self.assertNotIn("default_headers", fake_openai.call_args.kwargs)
            openai_loop.build_default_client("vllm")
            self.assertNotIn("default_headers", fake_openai.call_args.kwargs)


class TestGoUsageCapture(unittest.TestCase):
    def test_cost_fields_from_dict(self):
        from newsroom.tool_loop.openai_loop import extract_openai_usage
        usage = extract_openai_usage({
            "prompt_tokens": 1000,
            "completion_tokens": 20,
            "cost": 0,
            "opencode": {"allowance": 1.206e-05, "billing_source": "go",
                         "list_cost": 2.01e-05, "tokens": {"input": 1000}},
        })
        self.assertEqual(usage["cost_usd"], 0.0)
        self.assertAlmostEqual(usage["allowance_usd"], 1.206e-05)
        self.assertAlmostEqual(usage["list_cost_usd"], 2.01e-05)
        self.assertEqual(usage["billing_source"], "go")
        # Token contract is untouched
        self.assertEqual((usage["input_tokens"], usage["output_tokens"]), (1000, 20))

    def test_cost_fields_from_model_dump(self):
        from newsroom.tool_loop.openai_loop import extract_openai_usage
        usage = extract_openai_usage(MockDumpUsage(data={
            "cost": 0.0,
            "opencode": {"allowance": 0.5, "list_cost": 0.75, "billing_source": "go"},
        }))
        self.assertEqual(usage["cost_usd"], 0.0)
        self.assertEqual(usage["allowance_usd"], 0.5)
        self.assertEqual(usage["list_cost_usd"], 0.75)
        self.assertEqual(usage["billing_source"], "go")

    def test_cost_fields_from_model_extra(self):
        from newsroom.tool_loop.openai_loop import extract_openai_usage
        usage = extract_openai_usage(MockExtraUsage(data={
            "cost": 0.0,
            "opencode": {"allowance": 0.25, "list_cost": 0.5, "billing_source": "go"},
        }))
        self.assertEqual(usage["allowance_usd"], 0.25)
        self.assertEqual(usage["list_cost_usd"], 0.5)
        self.assertEqual(usage["billing_source"], "go")

    def test_cost_fields_absent_are_none(self):
        from newsroom.tool_loop.openai_loop import extract_openai_usage
        usage = extract_openai_usage({"prompt_tokens": 10, "completion_tokens": 5})
        for key in ("cost_usd", "list_cost_usd", "allowance_usd", "billing_source"):
            self.assertIsNone(usage[key], key)
        empty = extract_openai_usage(None)
        for key in ("cost_usd", "list_cost_usd", "allowance_usd", "billing_source"):
            self.assertIsNone(empty[key], key)


class TestGoAllowanceLedger(unittest.TestCase):
    def _two_request_client(self, **usage_kwargs):
        return MockOpenAIClient([
            MockResponse(MockMessage(tool_calls=[MockToolCall("c1", "calc", "{}")]),
                         MockGoUsage(**usage_kwargs)),
            MockResponse(MockMessage(content="Done"), MockGoUsage(**usage_kwargs)),
        ])

    def _run(self, client, **kwargs):
        def calc() -> str:
            """Compute."""
            return "42"
        return run_openai_tool_loop(client=client, model="deepseek-flash",
                                    system_instruction="", prompt="Calculate",
                                    tools=[calc], **kwargs)

    def _ledger_lines(self, path):
        with open(path) as f:
            return [json.loads(line) for line in f if line.strip()]

    def test_loop_writes_one_line_per_request(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "go_allowance.jsonl")
            with mock.patch.dict(os.environ, {"MU_GO_ALLOWANCE_FILE": path}):
                result = self._run(self._two_request_client(), provider="opencode-go")
            self.assertEqual(result.text, "Done")
            lines = self._ledger_lines(path)
            self.assertEqual(len(lines), 2)
            for line in lines:
                self.assertEqual(line["model"], "deepseek-v4.1-flash")
                self.assertEqual(line["label"], "opencode-go")
                self.assertEqual(line["allowance_usd"], 1.2e-05)
                self.assertEqual(line["list_cost_usd"], 2.0e-05)
                self.assertEqual(line["billing_source"], "go")
                self.assertIn("at", line)
            # Totals carry the summed money fields; usage_calls keep the per-request ones
            self.assertAlmostEqual(result.usage["allowance_usd"], 2.4e-05)
            self.assertAlmostEqual(result.usage["list_cost_usd"], 4.0e-05)
            self.assertEqual(result.usage_calls[0]["billing_source"], "go")

    def test_loop_writes_nothing_without_an_allowance(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "go_allowance.jsonl")
            client = MockOpenAIClient([MockResponse(MockMessage(content="Done"))])
            with mock.patch.dict(os.environ, {"MU_GO_ALLOWANCE_FILE": path}):
                result = self._run(client, provider="opencode-go")
            self.assertEqual(result.text, "Done")
            self.assertFalse(os.path.exists(path))

    def test_ledger_write_failure_does_not_break_the_run(self):
        with tempfile.TemporaryDirectory() as td:
            blocker = os.path.join(td, "blocker")
            with open(blocker, "w") as f:
                f.write("not a directory")
            path = os.path.join(blocker, "go_allowance.jsonl")
            stderr = io.StringIO()
            with mock.patch.dict(os.environ, {"MU_GO_ALLOWANCE_FILE": path}), \
                 contextlib.redirect_stderr(stderr):
                result = self._run(self._two_request_client(), provider="opencode-go")
            self.assertEqual(result.text, "Done")
            self.assertIsNone(result.error)
            self.assertAlmostEqual(result.usage["allowance_usd"], 2.4e-05)
            self.assertIn("go allowance not recorded", stderr.getvalue())
            self.assertFalse(os.path.exists(path))

    def test_record_creates_the_directory_and_entries_read_back(self):
        from newsroom.tool_loop import go_allowance
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "nested", "usage", "go_allowance.jsonl")
            go_allowance.record(path, at="2026-11-12T04:05:00Z", model="deepseek-v4.1-flash",
                                label="dossier", allowance_usd=1.5e-05,
                                list_cost_usd=2.0e-05, billing_source="go")
            rows = go_allowance.entries(path)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["at"], "2026-11-12T04:05:00Z")
            self.assertEqual(rows[0]["label"], "dossier")

    def test_entries_skip_malformed_lines_and_filter_by_since(self):
        from newsroom.tool_loop import go_allowance
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "go_allowance.jsonl")
            with open(path, "w") as f:
                f.write('{"at": "2026-11-12T01:00:00Z", "allowance_usd": 0.1}\n')
                f.write("not json\n")
                f.write("[1, 2, 3]\n")
                f.write('{"at": "2026-11-12T05:00:00Z", "allowance_usd": 0.2}\n')
            self.assertEqual(len(go_allowance.entries(path)), 2)
            recent = go_allowance.entries(path, since="2026-11-12T03:00:00Z")
            self.assertEqual([r["allowance_usd"] for r in recent], [0.2])
            self.assertEqual(go_allowance.entries(os.path.join(td, "missing.jsonl")), [])


class TestOpenCodeGoRouting(unittest.TestCase):
    def test_provider_names(self):
        self.assertEqual(resolve_tool_loop_provider("opencode-go"), "opencode-go")
        self.assertEqual(resolve_tool_loop_provider("opencode"), "opencode-go")
        self.assertEqual(resolve_tool_loop_provider("deepseek"), "deepseek")

    def test_model_prefixes(self):
        self.assertEqual(resolve_tool_loop_provider(model="opencode-go:deepseek-v4.1-flash"),
                         "opencode-go")
        self.assertEqual(resolve_tool_loop_provider(model="opencode:glm-5.3-flash"),
                         "opencode-go")
        self.assertEqual(resolve_tool_loop_provider(model="deepseek:deepseek-v4.1-flash"),
                         "deepseek")

    @mock.patch("newsroom.tool_loop.router.run_openai_tool_loop")
    def test_router_strips_opencode_prefix(self, mock_openai):
        mock_openai.return_value = ToolLoopResult(text="ok")
        execute_agent_tool_loop(None, "c", "opencode:deepseek-v4.1-flash", "sys", "p", [])
        kwargs = mock_openai.call_args.kwargs
        self.assertEqual(kwargs["model"], "deepseek-v4.1-flash")
        self.assertEqual(kwargs["provider"], "opencode-go")

        execute_agent_tool_loop("opencode-go", "c", "opencode-go:deepseek-v4.1-flash",
                                "sys", "p", [])
        self.assertEqual(mock_openai.call_args.kwargs["model"], "deepseek-v4.1-flash")

        # `deepseek:` still resolves to its own provider, which is the Go alias
        execute_agent_tool_loop(None, "c", "deepseek:deepseek-flash", "sys", "p", [])
        self.assertEqual(mock_openai.call_args.kwargs["provider"], "deepseek")
        self.assertEqual(mock_openai.call_args.kwargs["model"], "deepseek-flash")


if __name__ == "__main__":
    unittest.main()
