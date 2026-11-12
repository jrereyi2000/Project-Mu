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
# AUDIT R6 F4 Deferral 1: go_client() used to write nothing at all to data/usage/go_allowance.jsonl,
# so every Go text/social call was invisible to `tools/go_usage.py --source ledger`. A call now
# records the dollars Go says it took out of the allowance plus the tokens the response reported,
# and records NOTHING when the response reports no allowance: `tools/go_usage.py` prices
# allowance_usd, so a defaulted 0 would read as "this call cost nothing".


class FakeUsage:
    """One response's usageMetadata, with Go's `opencode` block beside Gemini's token counts."""

    def __init__(self, allowance=1.2e-05, list_cost=2.4e-05, billing_source="go",
                 prompt=200, cached=50, candidates=40, thoughts=0):
        self.prompt_token_count = prompt
        self.cached_content_token_count = cached
        self.candidates_token_count = candidates
        self.thoughts_token_count = thoughts
        self.opencode = {"allowance": allowance, "list_cost": list_cost,
                         "billing_source": billing_source, "tokens": prompt + candidates}


class FakeUsageInModelExtra:
    """The SDK keeps an unknown field in `model_extra`; that spelling must read the same."""

    def __init__(self, block):
        self.prompt_token_count = 200
        self.cached_content_token_count = 0
        self.candidates_token_count = 50
        self.thoughts_token_count = 0
        self.model_extra = {"opencode": block}


class FakeResponse:
    def __init__(self, usage):
        self.text = "a fake Go answer"
        self.usage_metadata = usage


class FakeModels:
    """A google-genai-shaped `models`: `generate_content` runs through the per-request
    `_generate_content` the real SDK (and the AFC loop) uses, so one row per request is visible."""

    def __init__(self, response, requests=1):
        self.response = response
        self.requests = requests
        self.calls = []

    def generate_content(self, model=None, contents=None, config=None):
        return self._generate_content(model=model, contents=contents, config=config)

    def _generate_content(self, model=None, contents=None, config=None):
        self.calls.append({"model": model, "contents": contents})
        return self.response


class FakeClient:
    def __init__(self, response, requests=1):
        self.models = FakeModels(response, requests)


def _client_against(monkeypatch, response, ledger):
    """A real go_client() built over a fake google-genai Client, with the ledger in a temp file."""
    transport = _reload(monkeypatch, key="oc_sk_test")
    monkeypatch.setenv("MU_GO_ALLOWANCE_FILE", str(ledger))
    import google.genai
    monkeypatch.setattr(google.genai, "Client", lambda **kwargs: FakeClient(response))
    return transport.go_client(label="social_writer")


def _rows(ledger):
    if not ledger.exists():
        return []
    return [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]


def test_a_go_call_records_its_allowance_and_tokens_in_the_ledger(monkeypatch, tmp_path):
    ledger = tmp_path / "go_allowance.jsonl"
    client = _client_against(monkeypatch, FakeResponse(FakeUsage()), ledger)

    client.models.generate_content(model="glm-5.3-flash", contents="write a post")

    rows = _rows(ledger)
    assert len(rows) == 1
    row = rows[0]
    assert row["model"] == "glm-5.3-flash"
    assert row["label"] == "social_writer"
    assert row["allowance_usd"] == pytest.approx(1.2e-05)
    assert row["list_cost_usd"] == pytest.approx(2.4e-05)
    assert row["billing_source"] == "go"
    assert (row["input_tokens"], row["cached_tokens"], row["output_tokens"]) == (200, 50, 40)
    at = datetime.fromisoformat(row["at"].replace("Z", "+00:00"))
    assert at.tzinfo is not None
    assert abs((at - datetime.now(timezone.utc)).total_seconds()) < 60


def test_the_recorded_row_is_what_go_usage_prices(monkeypatch, tmp_path):
    from tools import go_usage

    ledger = tmp_path / "go_allowance.jsonl"
    client = _client_against(monkeypatch, FakeResponse(FakeUsage()), ledger)
    client.models.generate_content(model="glm-5.3-flash", contents="write a post")

    code, message, document = go_usage.check(source="ledger", monthly_limit=60.0,
                                             ledger_path=ledger)
    assert document["windows"]["rolling"]["entries"] == 1
    assert document["windows"]["rolling"]["usd"] == pytest.approx(1.2e-05)
    assert document["windows"]["monthly"]["usd"] == pytest.approx(1.2e-05)
    assert code == 0
    assert "$0.0000" not in message


def test_a_usage_block_kept_in_model_extra_reads_the_same(monkeypatch, tmp_path):
    ledger = tmp_path / "go_allowance.jsonl"
    usage = FakeUsageInModelExtra({"allowance": 3e-05, "list_cost": 6e-05, "billing_source": "go"})
    client = _client_against(monkeypatch, FakeResponse(usage), ledger)

    client.models.generate_content(model="muse-spark-1.3-contributor", contents="hi")

    rows = _rows(ledger)
    assert len(rows) == 1
    assert rows[0]["allowance_usd"] == pytest.approx(3e-05)
    assert rows[0]["list_cost_usd"] == pytest.approx(6e-05)
    assert (rows[0]["input_tokens"], rows[0]["cached_tokens"], rows[0]["output_tokens"]) == (200, 0, 50)


def test_a_response_with_no_allowance_records_nothing_and_the_tool_says_so(monkeypatch, tmp_path):
    from tools import go_usage

    ledger = tmp_path / "go_allowance.jsonl"
    usage = FakeUsage()
    del usage.opencode                                    # Go reported tokens but no allowance
    client = _client_against(monkeypatch, FakeResponse(usage), ledger)

    client.models.generate_content(model="glm-5.3-flash", contents="write a post")

    assert _rows(ledger) == []                             # never a 0 row that reads as free
    code, message, document = go_usage.check(source="ledger", monthly_limit=60.0,
                                             ledger_path=ledger)
    assert code == 1
    assert document["windows"]["rolling"]["entries"] == 0
    assert "no readable ledger" in message


def test_a_free_model_reporting_zero_allowance_is_recorded_as_go_reported_it(monkeypatch, tmp_path):
    ledger = tmp_path / "go_allowance.jsonl"
    client = _client_against(
        monkeypatch, FakeResponse(FakeUsage(allowance=0.0, list_cost=0.0)), ledger)

    client.models.generate_content(model="beam-free", contents="hi")

    rows = _rows(ledger)
    assert len(rows) == 1
    assert rows[0]["allowance_usd"] == 0.0                 # Go's own number, not a default
    assert rows[0]["billing_source"] == "go"
    assert rows[0]["input_tokens"] == 200


def test_a_response_without_usage_metadata_writes_nothing(monkeypatch, tmp_path):
    ledger = tmp_path / "go_allowance.jsonl"
    client = _client_against(monkeypatch, FakeResponse(None), ledger)

    client.models.generate_content(model="glm-5.3-flash", contents="hi")

    assert _rows(ledger) == []


def test_a_failing_call_writes_nothing_and_still_raises(monkeypatch, tmp_path):
    ledger = tmp_path / "go_allowance.jsonl"
    transport = _reload(monkeypatch, key="oc_sk_test")
    monkeypatch.setenv("MU_GO_ALLOWANCE_FILE", str(ledger))

    class Boom:
        def generate_content(self, model=None, contents=None, config=None):
            raise RuntimeError("Go is down")

    class BoomClient:
        models = Boom()

    import google.genai
    monkeypatch.setattr(google.genai, "Client", lambda **kwargs: BoomClient())
    client = transport.go_client()

    with pytest.raises(RuntimeError, match="Go is down"):
        client.models.generate_content(model="glm-5.3-flash", contents="hi")
    assert _rows(ledger) == []


def test_each_request_a_tool_loop_makes_is_recorded_once(monkeypatch, tmp_path):
    """An article's AFC turn is several HTTP requests; Go bills each, so each gets one line."""
    ledger = tmp_path / "go_allowance.jsonl"
    transport = _reload(monkeypatch, key="oc_sk_test")
    monkeypatch.setenv("MU_GO_ALLOWANCE_FILE", str(ledger))

    class TwoRequestModels(FakeModels):
        def generate_content(self, model=None, contents=None, config=None):
            self._generate_content(model=model, contents=contents, config=config)   # the AFC calls
            return self._generate_content(model=model, contents=contents, config=config)

    class TwoRequestClient:
        def __init__(self):
            self.models = TwoRequestModels(FakeResponse(FakeUsage()))

    import google.genai
    monkeypatch.setattr(google.genai, "Client", lambda **kwargs: TwoRequestClient())
    client = transport.go_client(label="articles")

    client.models.generate_content(model="glm-5.3-flash", contents="write the article")

    assert len(_rows(ledger)) == 2                         # one per request, not one per turn


def test_a_usage_meter_around_the_client_does_not_double_record(monkeypatch, tmp_path):
    from db.llm_usage import UsageMeter

    ledger = tmp_path / "go_allowance.jsonl"
    client = _client_against(monkeypatch, FakeResponse(FakeUsage()), ledger)
    meter = UsageMeter(client)                             # what GoText and the dispatcher do

    client.models.generate_content(model="glm-5.3-flash", contents="hi")

    assert len(_rows(ledger)) == 1
    assert meter.totals()["calls"] == 1


def test_one_client_accounts_each_call_once(monkeypatch, tmp_path):
    ledger = tmp_path / "go_allowance.jsonl"
    client = _client_against(monkeypatch, FakeResponse(FakeUsage()), ledger)

    client.models.generate_content(model="glm-5.3-flash", contents="one")
    client.models.generate_content(model="glm-5.3-flash", contents="two")

    assert len(_rows(ledger)) == 2
    # and a second hook on the same client never double-records
    import newsroom.tool_loop.go_transport as transport
    transport._account_calls(client, "again")
    client.models.generate_content(model="glm-5.3-flash", contents="three")
    assert len(_rows(ledger)) == 3
