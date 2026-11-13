"""The Go Google-compatible transport (newsroom/tool_loop/go_transport.py). No network, no key.

Run: pytest -m "not integration" tests/test_go_transport.py
"""
import importlib
import json
import os
from datetime import datetime, timezone

import pytest


def _reload(monkeypatch, key=None, session=None):
    for name in ("OPENCODE_GO_API_KEY", "MU_GO_SESSION"):
        monkeypatch.delenv(name, raising=False)
    if key is not None:
        monkeypatch.setenv("OPENCODE_GO_API_KEY", key)
    if session is not None:
        monkeypatch.setenv("MU_GO_SESSION", session)
    import newsroom.tool_loop.go_transport as transport
    return importlib.reload(transport)


def test_client_refuses_without_a_key(monkeypatch):
    transport = _reload(monkeypatch, key=None)
    monkeypatch.setattr(transport, "_env", lambda name: None)
    with pytest.raises(ValueError, match="OPENCODE_GO_API_KEY"):
        transport.go_client()


def test_client_points_at_the_go_google_path_with_a_session_header(monkeypatch):
    transport = _reload(monkeypatch, key="oc_sk_test", session="mu-test-1")
    client = transport.go_client()
    options = client._api_client._http_options
    assert options.base_url == "https://opencode.ai/inference/go/google"
    assert options.headers["x-opencode-session"] == "mu-test-1"


def test_session_id_is_unique_per_process_and_time(monkeypatch):
    transport = _reload(monkeypatch, key="oc_sk_test")
    monkeypatch.setattr(transport.os, "getpid", lambda: 1234)
    monkeypatch.setattr(transport.time, "time", lambda: 1000.0)
    assert transport.go_session_id("mu") == "mu-1234-1000"
    monkeypatch.setattr(transport.time, "time", lambda: 1001.0)
    assert transport.go_session_id("mu") == "mu-1234-1001"


def test_is_go_model_splits_gemini_from_go_ids(monkeypatch):
    transport = _reload(monkeypatch, key="oc_sk_test")
    for model in ("muse-spark-1.3-contributor", "glm-5.3-flash", "deepseek-v4.1-flash", "kimi-k3"):
        assert transport.is_go_model(model) is True, model
    for model in ("gemini-3.8-flash", "gemini:3.8", "gemini/4-argon", ""):
        assert transport.is_go_model(model) is False, model


def test_module_imports_without_the_sdk(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith("google"):
            raise ImportError("no SDK here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    import importlib as _importlib
    import newsroom.tool_loop.go_transport as transport
    _importlib.reload(transport)                      # module load must not need google.genai


# --- The Go allowance ledger, written by the calls a go_client() makes ----------------------
#
# AUDIT R6 F4 Deferral 1 and the D1 re-check: go_client() used to write nothing at all to
# data/usage/go_allowance.jsonl, so every Go text/social call was invisible to
# `tools/go_usage.py --source ledger`. The first fix hooked the parsed `models._generate_content`
# response — but google-genai 2.34.0 drops `usageMetadata.opencode` while parsing (its models
# forbid extra fields), so production still wrote 0 lines. These tests therefore use a REAL
# `go_client()` and a REAL `genai.Client`, stubbing only `BaseApiClient.request` (the one HTTP
# call) to return Go's raw JSON body; metering reads that body before the SDK parses it.
#
# A call records the dollars Go says it took out of the allowance plus the tokens the response
# reported, and records NOTHING when the response reports no allowance: `tools/go_usage.py`
# prices allowance_usd, so a defaulted 0 would read as "this call cost nothing".


def _go_body(opencode=True, allowance=1.2e-05, list_cost=2e-05, billing_source="go",
             prompt=200, cached=10, candidates=50, thoughts=None):
    """One raw `generateContent` body in Go's JSON shape (camelCase, `opencode` beside the tokens)."""
    meta = {"promptTokenCount": prompt, "cachedContentTokenCount": cached,
            "candidatesTokenCount": candidates,
            "totalTokenCount": prompt + cached + candidates}
    if thoughts is not None:
        meta["thoughtsTokenCount"] = thoughts
    if opencode:
        meta["opencode"] = {"allowance": allowance, "list_cost": list_cost,
                            "billing_source": billing_source}
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": "hi"}]},
                            "finishReason": "STOP"}],
            "usageMetadata": meta}


def _stub_request(monkeypatch, *bodies):
    """Stub only `BaseApiClient.request`, returning `bodies` in order (the last one repeats).

    A body that is an exception is raised, so a failed request can be exercised. Returns the list
    of request paths the SDK asked for.
    """
    from google.genai import _api_client, types

    paths = []

    def request(self, http_method, path, request_dict, http_options=None):
        paths.append(path)
        body = bodies[min(len(paths) - 1, len(bodies) - 1)]
        if isinstance(body, BaseException):
            raise body
        text = body if isinstance(body, str) else json.dumps(body)
        return types.HttpResponse(headers={}, body=text)

    monkeypatch.setattr(_api_client.BaseApiClient, "request", request)
    return paths


def _client(monkeypatch, tmp_path, label="t"):
    """A real go_client()/genai.Client with the ledger in a temp file. Stub the request first."""
    transport = _reload(monkeypatch, key="oc_sk_test")
    ledger = tmp_path / "go_allowance.jsonl"
    monkeypatch.setenv("MU_GO_ALLOWANCE_FILE", str(ledger))
    return transport.go_client(label=label), ledger


def _rows(ledger):
    if not ledger.exists():
        return []
    return [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]


def test_a_real_go_call_records_the_allowance_and_tokens_go_reported(monkeypatch, tmp_path):
    """The D1 regression: a real client with only the HTTP request stubbed writes exactly one line.

    On caafc6e9 this test writes 0 lines, because the SDK had already dropped `opencode`.
    """
    _stub_request(monkeypatch, _go_body())
    client, ledger = _client(monkeypatch, tmp_path, label="t")

    client.models.generate_content(model="glm-5.3-flash", contents="write a post")

    rows = _rows(ledger)
    assert len(rows) == 1
    row = rows[0]
    assert row["model"] == "glm-5.3-flash"
    assert row["label"] == "t"
    assert row["allowance_usd"] == pytest.approx(1.2e-05)
    assert row["list_cost_usd"] == pytest.approx(2e-05)
    assert row["billing_source"] == "go"
    assert (row["input_tokens"], row["cached_tokens"], row["output_tokens"]) == (200, 10, 50)
    at = datetime.fromisoformat(row["at"].replace("Z", "+00:00"))
    assert at.tzinfo is not None
    assert abs((at - datetime.now(timezone.utc)).total_seconds()) < 60


def test_a_chat_send_message_records_one_line(monkeypatch, tmp_path):
    _stub_request(monkeypatch, _go_body())
    client, ledger = _client(monkeypatch, tmp_path)

    client.chats.create(model="glm-5.3-flash").send_message("x")

    assert len(_rows(ledger)) == 1


def test_an_automatic_tool_calling_turn_records_one_line_per_request(monkeypatch, tmp_path):
    """An article's AFC turn is several HTTP requests; Go bills each, so each gets one line."""
    from google.genai import types

    function_call = {"candidates": [{"content": {"role": "model", "parts": [
        {"functionCall": {"name": "get_score", "args": {"team": "SEA"}}}]},
        "finishReason": "STOP"}],
        "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 10,
                          "opencode": {"allowance": 1e-05, "list_cost": 2e-05,
                                       "billing_source": "go"}}}
    _stub_request(monkeypatch, function_call, _go_body(prompt=150, candidates=5))
    client, ledger = _client(monkeypatch, tmp_path, label="articles")

    def get_score(team: str) -> dict:
        """Get one team's score.

        Args:
            team: Team abbreviation.
        """
        return {"score": 21}

    chat = client.chats.create(
        model="glm-5.3-flash",
        config=types.GenerateContentConfig(tools=[get_score]))
    chat.send_message("score?")

    rows = _rows(ledger)
    assert len(rows) == 2                         # one per request, not one per turn
    assert [row["input_tokens"] for row in rows] == [100, 150]


def test_a_response_with_no_allowance_records_nothing_and_writes_no_file(monkeypatch, tmp_path):
    from tools import go_usage

    _stub_request(monkeypatch, _go_body(opencode=False))     # Go reported tokens, no allowance
    client, ledger = _client(monkeypatch, tmp_path)

    client.models.generate_content(model="glm-5.3-flash", contents="write a post")

    assert not ledger.exists()                               # never a 0 row that reads as free
    code, message, document = go_usage.check(source="ledger", monthly_limit=60.0,
                                             ledger_path=ledger)
    assert code == 1
    assert document["windows"]["rolling"]["entries"] == 0
    assert "no readable ledger" in message


def test_a_free_model_reporting_zero_allowance_is_recorded_as_go_reported_it(monkeypatch, tmp_path):
    _stub_request(monkeypatch, _go_body(allowance=0.0, list_cost=0.0))
    client, ledger = _client(monkeypatch, tmp_path)

    client.models.generate_content(model="beam-free", contents="hi")

    rows = _rows(ledger)
    assert len(rows) == 1
    assert rows[0]["allowance_usd"] == 0.0                   # Go's own number, not a default
    assert rows[0]["billing_source"] == "go"
    assert rows[0]["input_tokens"] == 200


def test_a_failing_request_writes_nothing_and_still_raises(monkeypatch, tmp_path):
    _stub_request(monkeypatch, RuntimeError("Go is down"))
    client, ledger = _client(monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="Go is down"):
        client.models.generate_content(model="glm-5.3-flash", contents="hi")
    assert not ledger.exists()


def test_a_ledger_write_error_never_breaks_the_call(monkeypatch, tmp_path, capsys):
    _stub_request(monkeypatch, _go_body())
    client, ledger = _client(monkeypatch, tmp_path)

    def boom(**kwargs):
        raise OSError("disk full")

    import newsroom.tool_loop.go_allowance as allowance
    monkeypatch.setattr(allowance, "record", boom)

    resp = client.models.generate_content(model="glm-5.3-flash", contents="hi")

    assert resp.text == "hi"                                 # the response is returned untouched
    assert not ledger.exists()
    assert "go allowance not recorded" in capsys.readouterr().err


def test_the_recorded_row_is_what_go_usage_prices(monkeypatch, tmp_path):
    from tools import go_usage

    _stub_request(monkeypatch, _go_body())
    client, ledger = _client(monkeypatch, tmp_path)
    client.models.generate_content(model="glm-5.3-flash", contents="write a post")

    code, message, document = go_usage.check(source="ledger", monthly_limit=60.0,
                                             ledger_path=ledger)
    assert document["windows"]["rolling"]["entries"] == 1
    assert document["windows"]["rolling"]["usd"] == pytest.approx(1.2e-05)
    assert document["windows"]["monthly"]["usd"] == pytest.approx(1.2e-05)
    assert code == 0
    assert "$0.0000" not in message


def test_a_usage_meter_around_the_client_does_not_double_record(monkeypatch, tmp_path):
    from db.llm_usage import UsageMeter

    _stub_request(monkeypatch, _go_body())
    client, ledger = _client(monkeypatch, tmp_path)
    meter = UsageMeter(client)                               # what GoText and the dispatcher do

    client.models.generate_content(model="glm-5.3-flash", contents="hi")

    assert len(_rows(ledger)) == 1
    assert meter.totals()["calls"] == 1


def test_one_client_accounts_each_call_once(monkeypatch, tmp_path):
    _stub_request(monkeypatch, _go_body())
    client, ledger = _client(monkeypatch, tmp_path)

    client.models.generate_content(model="glm-5.3-flash", contents="one")
    client.models.generate_content(model="glm-5.3-flash", contents="two")

    assert len(_rows(ledger)) == 2
    # and a second hook on the same client never double-records
    import newsroom.tool_loop.go_transport as transport
    transport._account_calls(client, "again")
    client.models.generate_content(model="glm-5.3-flash", contents="three")
    assert len(_rows(ledger)) == 3


def test_client_for_model_passes_the_label_through_to_the_ledger(monkeypatch, tmp_path):
    """N1: the duplicate factory that dropped `label=` was deleted; the line carries it."""
    _stub_request(monkeypatch, _go_body())
    transport = _reload(monkeypatch, key=None)                # no env key: pass the Go key explicitly
    ledger = tmp_path / "go_allowance.jsonl"
    monkeypatch.setenv("MU_GO_ALLOWANCE_FILE", str(ledger))

    client = transport.client_for_model("glm-5.3-flash", go_key="oc_sk_x", label="x")
    client.models.generate_content(model="glm-5.3-flash", contents="hi")

    rows = _rows(ledger)
    assert len(rows) == 1
    assert rows[0]["label"] == "x"


def test_the_model_id_comes_from_the_request_path(monkeypatch, tmp_path):
    paths = _stub_request(monkeypatch, _go_body())
    client, ledger = _client(monkeypatch, tmp_path)

    client.models.generate_content(model="muse-spark-1.3-contributor", contents="hi")

    assert paths == ["models/muse-spark-1.3-contributor:generateContent"]
    assert _rows(ledger)[0]["model"] == "muse-spark-1.3-contributor"


def test_response_usage_reads_camel_case_tokens_and_counts_thinking_as_output():
    """N3: a raw body is camelCase, and its thoughtsTokenCount is part of the output."""
    transport = importlib.import_module("newsroom.tool_loop.go_transport")
    usage = transport.response_usage(_go_body(prompt=200, cached=10, candidates=40, thoughts=10))
    assert (usage["input"], usage["cached"], usage["output"]) == (200, 10, 50)
    assert usage["allowance_usd"] == pytest.approx(1.2e-05)
    assert usage["list_cost_usd"] == pytest.approx(2e-05)
    assert usage["billing_source"] == "go"


def test_response_usage_still_reads_a_parsed_response_and_its_model_extra():
    """The parsed shape stays supported (supplement): older SDKs and the OpenAI-shaped path."""
    transport = importlib.import_module("newsroom.tool_loop.go_transport")

    class Parsed:
        prompt_token_count = 200
        cached_content_token_count = 0
        candidates_token_count = 50
        thoughts_token_count = 0
        model_extra = {"opencode": {"allowance": 3e-05, "list_cost": 6e-05,
                                    "billing_source": "go"}}

    class Resp:
        usage_metadata = Parsed()

    usage = transport.response_usage(Resp())
    assert (usage["input"], usage["cached"], usage["output"]) == (200, 0, 50)
    assert usage["allowance_usd"] == pytest.approx(3e-05)

