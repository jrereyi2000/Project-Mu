"""
Unit tests for the OpenAI-Compatible Tool Loop and Schema Converter.
(docs/in_progress/PC_MIGRATION_PLAN.md §7.3, §7.4; PRE_PC_WORK_2.md Item A6)

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
"""
import collections
import json
import os
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
    def __init__(self, content: Optional[str] = None, tool_calls: Optional[List[MockToolCall]] = None):
        self.content = content
        self.tool_calls = tool_calls or []


class MockChoice:
    def __init__(self, message: MockMessage):
        self.message = message


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


class MockResponse:
    def __init__(self, message: MockMessage, usage: Optional[MockUsage] = None):
        self.choices = [MockChoice(message)]
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
        with mock.patch.dict(os.environ, {}, clear=True):
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

        # Unsupported
        with self.assertRaises(ValueError):
            resolve_tool_loop_provider("anthropic_bedrock")

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


if __name__ == "__main__":
    unittest.main()
