"""
Unit tests for the `go` provider in `engines/llm_provider.py`.
(GLM_5_3_UNIFICATION_PLAN §2.1 and §5 U1)

Offline only: every client is a fake and no test needs `OPENCODE_GO_API_KEY`, a network call or
a database. They pin the contract the rest of the plan builds on:

1. `resolve_provider` returns `go` for an explicit provider, a `go:`/`go/` model prefix, a
   per-label/per-source env value and the global env, and every existing case is unchanged.
2. `resolve_model("go", ...)` defaults to `glm-5.3-flash` (or `MU_GO_TEXT_MODEL`) and strips the
   `go:`/`go/` prefix.
3. `generate_text(..., provider="go")` calls the Go model through the injected client and logs
   under the same source/label with the Go model id.
4. A missing Go key raises the transport's `ValueError` and never answers with Gemini.
5. `GoText` exposes `.model`/`.label`/`__call__`/`.flush()`/`.meter`, shares one cached client per
   process, and `make_llm(label, provider="go")` builds it.
"""
import os
import unittest
from unittest import mock

from engines import llm_provider
from engines.llm_provider import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_GO_MODEL,
    DEFAULT_OLLAMA_MODEL,
    GoText,
    ProviderText,
    generate_text,
    resolve_model,
    resolve_provider,
)
from engines.gm_llm import make_llm


class FakeGoResponse:
    def __init__(self, text="Fake Go response text"):
        self.text = text
        self.usage_metadata = mock.MagicMock(
            prompt_token_count=90,
            cached_content_token_count=0,
            candidates_token_count=35,
            thoughts_token_count=12,
        )


class FakeGoModels:
    """A google-genai-shaped `models` object: `generate_content` runs through `_generate_content`,
    so a UsageMeter wrapped around the client counts the call just as it does in production."""

    def __init__(self, text="Fake Go response text"):
        self.text = text
        self.calls = []

    def generate_content(self, model=None, contents=None, config=None):
        return self._generate_content(model=model, contents=contents, config=config)

    def _generate_content(self, model=None, contents=None, config=None):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return FakeGoResponse(self.text)


class FakeGoClient:
    def __init__(self, text="Fake Go response text"):
        self.models = FakeGoModels(text)


def clear_go_client():
    """Forget the cached process client so a test can build or refuse one deterministically."""
    llm_provider._GO_CLIENT = None


class GoProviderResolutionTests(unittest.TestCase):
    def setUp(self):
        clear_go_client()

    def tearDown(self):
        clear_go_client()

    def test_explicit_provider_go(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_provider("gm_rationale", provider="go"), "go")

    def test_provider_from_go_model_prefix(self):
        for model in ("go:glm-5.3-flash", "go/glm-5.3-flash"):
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertEqual(resolve_provider("press", model=model), "go")

    def test_provider_from_per_label_env(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER_PRESS": "go"}, clear=True):
            self.assertEqual(resolve_provider("press"), "go")
            self.assertEqual(resolve_provider("press", source="press"), "go")
            self.assertEqual(resolve_provider("agent_memory"), "gemini")

    def test_provider_from_per_source_env(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER_MEMORY": "go"}, clear=True):
            self.assertEqual(resolve_provider("summary @handle", source="memory"), "go")
            self.assertEqual(resolve_provider("summary @handle", source="inbox"), "gemini")

    def test_provider_from_global_env(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "go"}, clear=True):
            self.assertEqual(resolve_provider("gm_rationale"), "go")

    def test_explicit_provider_beats_env_and_prefix(self):
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "go"}, clear=True):
            self.assertEqual(resolve_provider("press", model="gemini:gemini-3.8-flash",
                                              provider="gemini"), "gemini")
            self.assertEqual(resolve_provider("press", model="ollama:qwen2.5:14b",
                                              provider="ollama"), "ollama")

    def test_existing_cases_unchanged(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_provider("gm_rationale"), "gemini")
            self.assertEqual(resolve_provider("gm_rationale", provider="ollama"), "ollama")
            self.assertEqual(resolve_provider("gm_rationale", provider="gemini"), "gemini")
            self.assertEqual(resolve_provider("gm_persona", model="ollama:qwen2.5:14b"), "ollama")
            self.assertEqual(resolve_provider("gm_persona", model="ollama/qwen2.5:14b"), "ollama")
            self.assertEqual(resolve_provider("agent_wire", model="gemini:gemini-3.8-flash:low"), "gemini")
            self.assertEqual(resolve_provider("agent_wire", model="gemini/gemini-3.8-flash"), "gemini")

        with mock.patch.dict(os.environ, {"LLM_PROVIDER_AGENT_WIRE": "ollama"}):
            self.assertEqual(resolve_provider("agent_wire"), "ollama")
            self.assertEqual(resolve_provider("agent_memory"), "gemini")

        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "ollama"}):
            self.assertEqual(resolve_provider("agent_memory"), "ollama")

    def test_unknown_provider_value_is_ignored(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_provider("press", provider="claude"), "gemini")
            self.assertEqual(resolve_provider("press", provider="go-go"), "gemini")
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": "claude"}, clear=True):
            self.assertEqual(resolve_provider("press"), "gemini")
        with mock.patch.dict(os.environ, {"LLM_PROVIDER_PRESS": "claude"}, clear=True):
            self.assertEqual(resolve_provider("press"), "gemini")


class GoModelResolutionTests(unittest.TestCase):
    def test_default_go_model(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_model("go", "press"), "glm-5.3-flash")
            self.assertEqual(DEFAULT_GO_MODEL, "glm-5.3-flash")

    def test_mu_go_text_model_overrides_the_default(self):
        with mock.patch.dict(os.environ, {"MU_GO_TEXT_MODEL": "gpt-6-luna"}, clear=True):
            self.assertEqual(resolve_model("go", "press"), "gpt-6-luna")
            self.assertEqual(resolve_model("go", "press", model="go:glm-5.3-flash"), "glm-5.3-flash")

    def test_go_prefix_is_stripped(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_model("go", "press", model="go:glm-5.3-flash"), "glm-5.3-flash")
            self.assertEqual(resolve_model("go", "press", model="go/glm-5.3-flash"), "glm-5.3-flash")
            self.assertEqual(resolve_model("go", "press", model="  go:muse-spark-1.3  "), "muse-spark-1.3")

    def test_gemini_default_is_not_a_go_model(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_model("go", "press", model=DEFAULT_GEMINI_MODEL), "glm-5.3-flash")

    def test_existing_model_resolution_unchanged(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_model("gemini", "press"), DEFAULT_GEMINI_MODEL)
            self.assertEqual(resolve_model("gemini", "press", model="gemini:gemini-3.8-flash:low"),
                             "gemini-3.8-flash:low")
            self.assertEqual(resolve_model("ollama", "press"), DEFAULT_OLLAMA_MODEL)
            self.assertEqual(resolve_model("ollama", "press", model="ollama:qwen2.5:14b"), "qwen2.5:14b")


class GenerateTextGoTests(unittest.TestCase):
    def setUp(self):
        clear_go_client()

    def tearDown(self):
        clear_go_client()

    def test_provider_go_calls_the_go_model(self):
        client = FakeGoClient("The line holds up.")
        with mock.patch.dict(os.environ, {}, clear=True):
            text = generate_text("Grade this line", source="press", label="press answer",
                                 provider="go", client=client, log_tokens=False)

        self.assertEqual(text, "The line holds up.")
        self.assertEqual(len(client.models.calls), 1)
        call = client.models.calls[0]
        self.assertEqual(call["model"], "glm-5.3-flash")
        self.assertEqual(call["contents"], "Grade this line")
        self.assertIsNotNone(call["config"].thinking_config)     # the resolved level (low) is applied

    def test_go_model_prefix_calls_that_model(self):
        client = FakeGoClient("ok")
        with mock.patch.dict(os.environ, {}, clear=True):
            text = generate_text("p", source="llm_compare", model="go:glm-5.3-flash:low",
                                 client=client, log_tokens=False)

        self.assertEqual(text, "ok")
        self.assertEqual(client.models.calls[0]["model"], "glm-5.3-flash")

    def test_return_usage_shape(self):
        client = FakeGoClient("ok")
        with mock.patch.dict(os.environ, {}, clear=True):
            result = generate_text("p", source="press", provider="go", client=client,
                                   return_usage=True, log_tokens=False)

        text, usage = result
        self.assertEqual(text, "ok")
        self.assertEqual(usage["input_tokens"], 90)
        self.assertEqual(usage["output_tokens"], 35)
        self.assertEqual(usage["cached_tokens"], 0)
        self.assertEqual(usage["thinking_tokens"], 12)

    def test_usage_is_logged_under_the_same_source_and_label(self):
        client = FakeGoClient("ok")
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("engines.llm_provider.log_usage") as mock_log:
            generate_text("p", source="press", label="press answer", provider="go", client=client)

        mock_log.assert_called_once()
        kwargs = mock_log.call_args[1]
        self.assertEqual(kwargs["source"], "press")
        self.assertEqual(kwargs["label"], "press answer")
        self.assertEqual(kwargs["model"], "glm-5.3-flash")
        self.assertEqual(kwargs["input_tokens"], 90)
        self.assertEqual(kwargs["output_tokens"], 35)
        self.assertEqual(kwargs["thinking_tokens"], 12)

    def test_label_env_routes_to_go(self):
        client = FakeGoClient("from the label route")
        with mock.patch.dict(os.environ, {"LLM_PROVIDER_PRESS": "go"}, clear=True):
            text = generate_text("p", source="press", client=client, log_tokens=False)

        self.assertEqual(text, "from the label route")
        self.assertEqual(client.models.calls[0]["model"], "glm-5.3-flash")

    def test_missing_key_raises_and_never_answers_with_gemini(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("newsroom.tool_loop.go_transport.go_api_key", return_value=None), \
                mock.patch("engines.llm_provider.call_gemini") as mock_gemini:
            with self.assertRaises(ValueError) as ctx:
                generate_text("p", source="press", provider="go", allow_fallback=True)

        self.assertEqual(str(ctx.exception), "OPENCODE_GO_API_KEY is not set")
        mock_gemini.assert_not_called()

    def test_missing_key_raises_through_a_go_model_prefix(self):
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("newsroom.tool_loop.go_transport.go_api_key", return_value=None), \
                mock.patch("engines.llm_provider.call_gemini") as mock_gemini:
            with self.assertRaises(ValueError):
                generate_text("p", source="llm_compare", model="go:glm-5.3-flash", log_tokens=False)

        mock_gemini.assert_not_called()

    def test_usage_meter_call_totals(self):
        # ProviderText shares the same call path; the meter counts the Go call and flush() returns
        # its totals (tests/conftest.py replaces UsageMeter.record, so no database is touched)
        client = FakeGoClient("metered")
        with mock.patch.dict(os.environ, {}, clear=True):
            llm = ProviderText(label="press answer", source="press", provider="go", client=client)
            self.assertEqual(llm("p"), "metered")
            totals = llm.flush()

        self.assertEqual(totals["calls"], 1)
        self.assertEqual(totals["input_tokens"], 90)
        self.assertEqual(totals["output_tokens"], 35)


class GoTextTests(unittest.TestCase):
    def setUp(self):
        clear_go_client()

    def tearDown(self):
        clear_go_client()

    def test_exposes_model_label_and_a_meter(self):
        client = FakeGoClient("text from go")
        with mock.patch.dict(os.environ, {}, clear=True):
            go = GoText(label="inbox", model="glm-5.3-flash", client=client)
        self.assertEqual(go.model, "glm-5.3-flash")
        self.assertEqual(go.model_name, "glm-5.3-flash")
        self.assertEqual(go.label, "inbox")
        self.assertIsNotNone(go.meter)
        self.assertEqual(go("p"), "text from go")

    def test_default_model_when_none(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            go = GoText(label="inbox", client=FakeGoClient())
        self.assertEqual(go.model, "glm-5.3-flash")

    def test_two_constructions_share_one_cached_client(self):
        shared = FakeGoClient("shared")
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("newsroom.tool_loop.go_transport.go_client",
                           return_value=shared) as mock_build:
            first = GoText(label="gm_rationale")
            second = GoText(label="gm_persona")

        self.assertIs(first.client, shared)
        self.assertIs(second.client, shared)
        self.assertEqual(mock_build.call_count, 1)

    def test_one_cached_client_across_generate_text_calls(self):
        shared = FakeGoClient("shared")
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("newsroom.tool_loop.go_transport.go_client",
                           return_value=shared) as mock_build:
            generate_text("p", source="press", provider="go", log_tokens=False)
            generate_text("p", source="press", provider="go", log_tokens=False)

        self.assertEqual(mock_build.call_count, 1)
        self.assertEqual(len(shared.models.calls), 2)

    def test_flush_totals(self):
        client = FakeGoClient("ok")
        with mock.patch.dict(os.environ, {}, clear=True):
            go = GoText(label="gm_rationale", client=client)
            go("p")
            self.assertEqual(go.meter.totals()["calls"], 1)
            totals = go.flush()

        self.assertEqual(totals["calls"], 1)
        self.assertEqual(totals["input_tokens"], 90)

    def test_flush_without_calls_still_returns_totals(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            go = GoText(label="gm_rationale", client=FakeGoClient())
            totals = go.flush()
        self.assertEqual(totals["calls"], 0)


class MakeLlmGoTests(unittest.TestCase):
    def setUp(self):
        clear_go_client()

    def tearDown(self):
        clear_go_client()

    def test_make_llm_builds_gotext_for_provider_go(self):
        client = FakeGoClient("gm line")
        with mock.patch.dict(os.environ, {}, clear=True):
            llm = make_llm("gm_rationale", provider="go", client=client)

        self.assertIsInstance(llm, GoText)
        self.assertEqual(llm.label, "gm_rationale")
        self.assertEqual(llm.model, "glm-5.3-flash")
        self.assertEqual(llm("Trade prompt"), "gm line")
        self.assertEqual(client.models.calls[0]["model"], "glm-5.3-flash")

    def test_make_llm_builds_gotext_for_a_go_model_prefix(self):
        client = FakeGoClient("gm line")
        with mock.patch.dict(os.environ, {}, clear=True):
            llm = make_llm("scout_report", model="go:glm-5.3-flash", client=client)

        self.assertIsInstance(llm, GoText)
        self.assertEqual(llm.model, "glm-5.3-flash")

    def test_make_llm_builds_gotext_from_a_label_env(self):
        client = FakeGoClient("gm line")
        with mock.patch.dict(os.environ, {"LLM_PROVIDER_GM_PERSONA": "go"}, clear=True):
            llm = make_llm("gm_persona", client=client)

        self.assertIsInstance(llm, GoText)
        self.assertEqual(llm.model, "glm-5.3-flash")

    def test_make_llm_returns_none_without_a_go_key(self):
        # None is make_llm's template contract; the important part is that no Gemini callable is built
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch("newsroom.tool_loop.go_transport.go_api_key", return_value=None), \
                mock.patch("engines.gm_llm.GeminiText") as gemini:
            self.assertIsNone(make_llm("gm_rationale", provider="go"))
        gemini.assert_not_called()

    def test_make_llm_still_builds_gemini_by_default(self):
        client = mock.MagicMock()
        with mock.patch.dict(os.environ, {}, clear=True):
            llm = make_llm("gm_rationale", client=client)
        self.assertNotIsInstance(llm, GoText)
        self.assertEqual(llm.model, DEFAULT_GEMINI_MODEL)


class GoDefaultsUnchangedTests(unittest.TestCase):
    def test_no_default_route_names_go(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            for label in ("press", "agent_memory", "inbox", "scouting_notes", "agent_wire", "gm_rationale"):
                self.assertEqual(resolve_provider(label), "gemini", label)
                self.assertEqual(resolve_model("gemini", label), DEFAULT_GEMINI_MODEL, label)


class EstimateCostGoTests(unittest.TestCase):
    """AUDIT R6 F4 Deferral 2: `estimate_cost` priced every unlisted model at Gemini's (0.15, 0.60), so a
    Go model's cost was silently wrong instead of priced at its own list rate or reported unpriced."""

    # 2M input of which 1M was cached, 1M output. GLM-5.3-Flash's published rates are
    # 0.15 in / 0.03 cached read / 0.50 out per million (tools/article_model_compare.py PRICES):
    #   1M uncached * 0.15 + 1M cached * 0.03 + 1M out * 0.50 = 0.68
    # Gemini's fallback would answer 2M * 0.15 + 1M * 0.60 = 0.90.
    USAGE = {"input_tokens": 2_000_000, "cached_tokens": 1_000_000,
             "output_tokens": 1_000_000, "thinking_tokens": 0}

    def test_glm_5_3_flash_is_priced_at_its_published_rates(self):
        self.assertAlmostEqual(llm_provider.estimate_cost("glm-5.3-flash", dict(self.USAGE)),
                               0.68, places=9)
        self.assertNotAlmostEqual(llm_provider.estimate_cost("glm-5.3-flash", dict(self.USAGE)),
                                  0.90, places=9)          # never Gemini's numbers

    def test_the_cached_rate_is_used_for_cached_tokens(self):
        # 1M cached, nothing else: 0.03 at the cached-read rate, not 0.15 at the input rate.
        self.assertAlmostEqual(
            llm_provider.estimate_cost("glm-5.3-flash",
                                       {"input_tokens": 1_000_000, "cached_tokens": 1_000_000,
                                        "output_tokens": 0}),
            0.03, places=9)

    def test_the_repro_usage_no_longer_answers_geminis_number(self):
        # newsroom/social_writer.write_post's usage shape, 200 in / 0 cached / 50 out:
        # GLM lists 200 * 0.15 + 50 * 0.50 = 0.000055; the old Gemini fallback said 0.00006.
        usage = {"input": 200, "cached": 0, "output": 50, "thinking": 0}
        self.assertAlmostEqual(llm_provider.estimate_cost("glm-5.3-flash", usage), 0.000055, places=12)

    def test_a_go_call_says_its_dollars_are_an_allowance_not_cash(self):
        detail = llm_provider.cost_estimate("glm-5.3-flash", dict(self.USAGE))
        self.assertEqual(detail["basis"], "allowance")
        self.assertAlmostEqual(detail["usd"], 0.68, places=9)
        self.assertEqual(detail["cash_usd"], 0.0)          # the allowance pays; no card is charged
        self.assertIn("allowance", detail["note"].lower())
        self.assertIn("not $0", detail["note"])

    def test_an_unpriced_model_is_none_with_a_reason_not_gemini_numbers(self):
        self.assertIsNone(llm_provider.estimate_cost("some-unlisted-model-9", dict(self.USAGE)))
        detail = llm_provider.cost_estimate("some-unlisted-model-9", dict(self.USAGE))
        self.assertEqual(detail["basis"], "unpriced")
        self.assertIsNone(detail["usd"])
        self.assertIn("unpriced", detail["note"].lower())

    def test_a_gemini_call_is_still_cash_and_unchanged(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "thinking_tokens": 0}
        self.assertAlmostEqual(llm_provider.estimate_cost("gemini-3.8-flash", usage), 0.75, places=9)
        detail = llm_provider.cost_estimate("gemini-3.8-flash", usage)
        self.assertEqual(detail["basis"], "cash")
        self.assertAlmostEqual(detail["cash_usd"], 0.75, places=9)
        self.assertEqual(detail["note"], "")

    def test_a_local_model_is_free_by_construction(self):
        self.assertEqual(llm_provider.estimate_cost("ollama/qwen2.5:14b", dict(self.USAGE)), 0.0)
        self.assertEqual(llm_provider.cost_estimate("ollama/qwen2.5:14b", dict(self.USAGE))["basis"],
                         "local")

    def test_a_longer_model_id_wins_over_its_prefix(self):
        # "gemini-2.5-flash" is a prefix of nothing here, but a Go id with a suffix must not be
        # priced by a shorter key that happens to be inside it.
        self.assertEqual(llm_provider._price_for("gemini-3.8-flash-preview")[0], "gemini-3.8-flash")


class GoKeyIsolationTests(unittest.TestCase):
    """AUDIT R6 F1 and AUDIT_QUEUE_INVENTORY.md R11: a caller's Gemini key can never authenticate, or become
    the cached credential of, the process's OpenCode Go client, and a bare Go id never resolves to Gemini."""

    def setUp(self):
        clear_go_client()

    def tearDown(self):
        clear_go_client()

    def test_resolve_provider_sends_a_bare_go_id_to_go(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_provider(model="glm-5.3-flash"), "go")
            self.assertEqual(resolve_provider(model="muse-spark-1.3-contributor"), "go")
            self.assertEqual(resolve_provider(model="gemini-3.8-flash"), "gemini")
            self.assertEqual(resolve_provider(model="qwen2.5:14b"), "gemini")   # an Ollama tag is not a Go id

    def test_a_caller_key_never_reaches_go_client(self):
        client = FakeGoClient("go text")
        with mock.patch("newsroom.tool_loop.go_transport.go_client", return_value=client) as built, \
                mock.patch("newsroom.tool_loop.go_transport.go_api_key", return_value="oc_sk_env"):
            text = generate_text("p", source="agent_memory", model="glm-5.3-flash",
                                 api_key="GEMINI_KEY", log_tokens=False)

        self.assertEqual(text, "go text")
        built.assert_called_once_with()          # no key argument: the transport reads OPENCODE_GO_API_KEY itself

    def test_a_gemini_key_cannot_stand_in_for_the_go_key(self):
        with mock.patch("google.genai.Client", side_effect=AssertionError("a client was built")) as built, \
                mock.patch("newsroom.tool_loop.go_transport.go_api_key", return_value=None):
            with self.assertRaises(ValueError) as ctx:
                generate_text("p", source="agent_memory", model="glm-5.3-flash",
                              api_key="GEMINI_KEY", log_tokens=False)

        self.assertEqual(str(ctx.exception), "OPENCODE_GO_API_KEY is not set")
        built.assert_not_called()

