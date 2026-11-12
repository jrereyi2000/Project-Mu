"""The `_execute_agent` comparison seam (`client` / `capture`). Offline: a fake google-genai client.

Run: pytest -m "not integration" tests/test_swarm_capture.py
"""
from types import SimpleNamespace
from unittest import mock

import pytest


class _Response:
    def __init__(self, text, prompt=100, cached=20, out=10, thoughts=5):
        self.text = text
        self.candidates = []
        self.usage_metadata = SimpleNamespace(
            prompt_token_count=prompt, cached_content_token_count=cached,
            candidates_token_count=out, thoughts_token_count=thoughts,
        )


class _Models:
    def __init__(self, responses):
        self._queue = list(responses)
        self.calls = 0

    def _generate_content(self, *args, **kwargs):
        self.calls += 1
        return self._queue.pop(0)


class _Chat:
    def __init__(self, client, config):
        self.client = client
        self.config = config

    def send_message(self, prompt):
        # Emulate the SDK's AFC: run any tool the article path supplied, then answer.
        for tool in getattr(self.config, "tools", None) or []:
            try:
                tool(city="Boston")
            except TypeError:
                tool("Boston")
        return self.client.models._generate_content(model="fake", contents=prompt)


class _Chats:
    def __init__(self, client):
        self.client = client
        self.configs = []

    def create(self, **kwargs):
        self.configs.append(kwargs.get("config"))
        return _Chat(self.client, kwargs.get("config"))


class _Client:
    def __init__(self, texts):
        self.models = _Models([_Response(t) for t in texts])
        self.chats = _Chats(self)


def _dispatcher(tools=None):
    from newsroom.swarm_dispatcher import SwarmDispatcher
    dispatcher = SwarmDispatcher(api_key="fake-key", debug_mode=False)
    dispatcher.tools_for = lambda article_type=None: list(tools or [])
    dispatcher._ballot_brief = lambda *a, **k: None
    return dispatcher


def _prompt(**extra):
    base = {
        "display_name": "Tester", "handle": "TESTER", "content_type": "ARTICLE",
        "article_type": "MACRO_SLATE_RECAP", "article_type_name": "Slate Recap", "slot": "Hero Slot",
        "system_instruction": "Write like a columnist.", "task_instruction": "Write the recap.",
        "archetype": "TESTER",
    }
    base.update(extra)
    return base


def test_capture_gets_text_tokens_and_tool_trace():
    def lookup(city: str) -> str:
        """Look up a city."""
        return "18C and clear in " + city

    dispatcher = _dispatcher(tools=[lookup])
    client = _Client(["# Headline\n\nBody text."])
    capture = {}

    with mock.patch("db.llm_usage.UsageMeter.record") as record:
        dispatcher._execute_agent(_prompt(), 2036, 18, None, live=True, model="muse-spark-1.3-contributor",
                                  save_to_db=False, client=client, capture=capture)
    record.assert_not_called()                       # a capture run never writes llm_usage

    assert capture["text"].startswith("# Headline")
    assert capture["failure_reason"] is None
    assert capture["tool_calls"][0]["name"] == "lookup"
    assert capture["tool_calls"][0]["args"] == {"city": "Boston"}
    assert "18C" in capture["tool_calls"][0]["result"]
    assert capture["usage_calls"][0]["input_tokens"] == 100
    assert capture["usage_calls"][0]["cached_tokens"] == 20
    assert capture["usage_calls"][0]["thinking_tokens"] == 5
    assert capture["rewrites"] == []


def test_a_tool_error_is_recorded_and_still_returned_to_the_model():
    def broken(city: str) -> str:
        """Always fails."""
        raise RuntimeError("no database")

    dispatcher = _dispatcher(tools=[broken])
    capture = {}
    dispatcher._execute_agent(_prompt(), 2036, 18, None, live=True, model="glm-5.3-flash",
                              save_to_db=False, client=_Client(["text"]), capture=capture)
    assert capture["tool_calls"][0]["error"] == "no database"
    assert capture["tool_calls"][0]["result"] is None


def test_no_capture_behaves_exactly_as_before():
    dispatcher = _dispatcher(tools=[])
    client = _Client(["plain text"])
    with mock.patch("db.llm_usage.UsageMeter.record") as record:
        dispatcher._execute_agent(_prompt(), 2036, 18, None, live=True, model="gemini-3.8-flash",
                                  save_to_db=False, client=client)
    assert client.models.calls == 1
    record.assert_called_once()                      # the production path still meters to llm_usage


def test_a_validation_rewrite_is_recorded_and_replaces_the_text():
    dispatcher = _dispatcher(tools=[])
    dispatcher._power_rankings_team_index = lambda: {}
    capture = {}
    with mock.patch("newsroom.swarm_dispatcher.parse_power_rankings", return_value=([], ["missing team"])), \
         mock.patch("newsroom.swarm_dispatcher.power_rankings_fix_prompt", return_value="fix it"):
        dispatcher._execute_agent(_prompt(article_type="POWER_RANKINGS", article_type_name="Power Rankings"),
                                  2036, 18, None, live=True, model="muse-spark-1.3-contributor",
                                  save_to_db=False, client=_Client(["first draft", "second draft"]),
                                  capture=capture)
    assert capture["rewrites"] == ["POWER_RANKINGS"]
    assert capture["text"] == "second draft"


def test_a_model_without_a_key_is_skipped_but_a_supplied_client_works():
    from newsroom.swarm_dispatcher import SwarmDispatcher
    dispatcher = SwarmDispatcher(api_key=None, debug_mode=False)
    dispatcher.tools_for = lambda article_type=None: []
    dispatcher._ballot_brief = lambda *a, **k: None
    capture = {}
    dispatcher._execute_agent(_prompt(), 2036, 18, None, live=True, model="muse-spark-1.3-contributor",
                              save_to_db=False, client=_Client(["ok"]), capture=capture)
    assert capture["text"] == "ok"


def test_a_go_model_builds_a_go_client_and_needs_no_gemini_key():
    from newsroom.swarm_dispatcher import SwarmDispatcher
    dispatcher = SwarmDispatcher(api_key=None, debug_mode=False)
    dispatcher.tools_for = lambda article_type=None: []
    dispatcher._ballot_brief = lambda *a, **k: None
    capture = {}
    fake = _Client(["go text"])
    with mock.patch("newsroom.tool_loop.go_transport.go_client", return_value=fake) as go_client:
        dispatcher._execute_agent(_prompt(), 2036, 18, None, live=True, model="glm-5.3-flash",
                                  save_to_db=False, capture=capture)
    go_client.assert_called_once()
    assert capture["text"] == "go text"


def test_the_article_writer_default_is_the_go_model():
    from newsroom.model_default import DEFAULT_WRITER_MODEL
    from newsroom import weekly_run
    assert DEFAULT_WRITER_MODEL == "glm-5.3-flash"
    assert weekly_run.DEFAULT_MODEL == DEFAULT_WRITER_MODEL
    assert weekly_run.SOCIAL_MODEL.startswith("gemini-")
    assert weekly_run.MEMORY_MODEL.startswith("gemini-")
