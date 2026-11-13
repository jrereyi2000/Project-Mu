"""
Unit tests for tools/go_usage.py — the OpenCode Go allowance watchdog.
(docs/in_progress/OPENCODE_GO_PIPELINE_PLAN.md §2.5, U1)

Fully offline: the HTTP layer is stubbed at urllib.request.urlopen and the ledger is a
temporary JSONL fixture, so no request leaves the machine and no real API key is read.

Verifies:
1. The api source: endpoint derivation, Bearer header, project-mu User-Agent, percentage
   parsing, exit 0 under the thresholds and 2 at or over one.
2. Failure handling: 403/404/URLError falls back to the ledger under `auto`, while
   `--source api` exits 1 without falling back.
3. The ledger source: rolling 5-hour / 7-day / UTC-month sums, dollars without a limit
   (exit 1) and percentages with --monthly-limit (exit 0/2).
4. Staleness, the --json shape, and that --monthly-limit does not touch the api source.
"""
import contextlib
import io
import json
import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from tools import go_usage

NOW = datetime(2026, 11, 12, 12, 0, 0, tzinfo=timezone.utc)


def frozen_datetime(at):
    """A datetime class whose now() is pinned, so a ledger fixture never ages out by wall clock."""
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return at if tz is None else at.astimezone(tz)

    return Frozen
THRESHOLDS = {"rolling": 85.0, "weekly": 99.0, "monthly": 95.0}
API_BODY = {"usage": {
    "rolling": {"status": "ok", "percent": 2, "resetsAt": "2026-11-12T13:04:00Z"},
    "weekly": {"status": "ok", "percent": 1, "resetsAt": "2026-11-16T00:00:00Z"},
    "monthly": {"status": "ok", "percent": 0, "resetsAt": "2026-12-12T00:00:00Z"},
}}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._body = json.dumps(payload).encode("utf-8")
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def write_ledger(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def ledger_row(at, allowance_usd, model="deepseek-v4.1-flash", label="dossier"):
    return {"at": at, "model": model, "label": label, "allowance_usd": allowance_usd,
            "list_cost_usd": allowance_usd * 1.5, "billing_source": "go"}


def iso(at):
    return at.strftime("%Y-%m-%dT%H:%M:%SZ")


class GoUsageTestCase(unittest.TestCase):
    """Runs main() with a clean environment and a temp repo root, so no .env is read."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.ledger = str(self.tmp / "go_allowance.jsonl")

    def run_cli(self, argv, env=None, now=NOW):
        base_env = {"MU_GO_ALLOWANCE_FILE": self.ledger}
        base_env.update(env or {})
        stdout = io.StringIO()
        with mock.patch.dict(os.environ, base_env, clear=True), \
             mock.patch.object(go_usage, "_REPO_ROOT", self.tmp), \
             mock.patch.object(go_usage, "DEFAULT_THRESHOLDS", dict(THRESHOLDS)), \
             mock.patch.object(go_usage, "datetime", frozen_datetime(now)), \
             contextlib.redirect_stdout(stdout):
            code = go_usage.main(argv)
        return code, stdout.getvalue()

    def run_json(self, argv, env=None, now=NOW):
        code, out = self.run_cli(list(argv) + ["--json"], env=env, now=now)
        return code, json.loads(out)

    def check(self, **kwargs):
        kwargs.setdefault("thresholds", dict(THRESHOLDS))
        kwargs.setdefault("now", NOW)
        kwargs.setdefault("ledger_path", self.ledger)
        return go_usage.check(**kwargs)


class TestApiSource(GoUsageTestCase):
    def test_reports_percentages_and_exits_zero(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(API_BODY)):
            code, out = self.run_cli(["--source", "api"],
                                     env={"OPENCODE_GO_API_KEY": "test-key"})
        self.assertEqual(code, 0)
        self.assertIn("go allowance: 5h 2% (resets 13:04Z)", out)
        self.assertIn("weekly 1%", out)
        self.assertIn("month 0%", out)
        self.assertIn("under 85%/99%/95%", out)

    def test_exits_two_at_or_over_a_threshold(self):
        body = json.loads(json.dumps(API_BODY))
        body["usage"]["rolling"]["percent"] = 85
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(body)):
            code, out = self.run_cli(["--source", "api"],
                                     env={"OPENCODE_GO_API_KEY": "test-key"})
        self.assertEqual(code, 2)
        self.assertIn("OVER", out)
        self.assertIn("5h 85% >= 85%", out)

    def test_request_carries_bearer_token_and_project_mu_user_agent(self):
        seen = []

        def fake_urlopen(request, timeout=None):
            seen.append(request)
            return FakeResponse(API_BODY)

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            code, _ = self.run_cli(["--source", "api"],
                                   env={"OPENCODE_GO_API_KEY": "test-key"})
        self.assertEqual(code, 0)
        self.assertEqual(len(seen), 1)
        request = seen[0]
        self.assertEqual(request.full_url, "https://opencode.ai/inference/go/v1/usage")
        headers = {key.lower(): value for key, value in request.headers.items()}
        self.assertEqual(headers["authorization"], "Bearer test-key")
        self.assertIn("project-mu", headers["user-agent"])

    def test_usage_url_is_derived_from_the_base_url(self):
        with mock.patch.object(go_usage, "_REPO_ROOT", self.tmp), \
             mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(go_usage.usage_url(),
                             "https://opencode.ai/inference/go/v1/usage")
            self.assertEqual(go_usage.usage_url("https://opencode.ai/inference/go/openai/v1"),
                             "https://opencode.ai/inference/go/v1/usage")
            self.assertEqual(go_usage.usage_url("http://localhost:9999/openai/v1"),
                             "http://localhost:9999/v1/usage")

    def test_forbidden_falls_back_to_the_ledger_under_auto(self):
        write_ledger(self.ledger, [ledger_row("2026-11-12T11:00:00Z", 0.25)])
        error = urllib.error.HTTPError("https://x", 403, "Forbidden", None, None)
        with mock.patch("urllib.request.urlopen", side_effect=error):
            code, document = self.run_json(["--source", "auto", "--monthly-limit", "100"],
                                           env={"OPENCODE_GO_API_KEY": "test-key"})
        self.assertEqual(code, 0)
        self.assertEqual(document["source"], "ledger")
        self.assertIn("api unavailable", document["message"])
        self.assertIsNotNone(document["windows"]["monthly"]["percent"])

    def test_api_alone_exits_one_instead_of_falling_back(self):
        write_ledger(self.ledger, [ledger_row("2026-11-12T11:00:00Z", 0.25)])
        error = urllib.error.HTTPError("https://x", 403, "Forbidden", None, None)
        with mock.patch("urllib.request.urlopen", side_effect=error):
            code, document = self.run_json(["--source", "api", "--monthly-limit", "100"],
                                           env={"OPENCODE_GO_API_KEY": "test-key"})
        self.assertEqual(code, 1)
        self.assertEqual(document["source"], "api")
        self.assertEqual(document["windows"], {})
        self.assertIn("unknown", document["message"])

    def test_url_error_falls_back_under_auto(self):
        write_ledger(self.ledger, [ledger_row("2026-11-12T11:00:00Z", 0.25)])
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("no route")):
            code, document = self.run_json([], env={"OPENCODE_GO_API_KEY": "test-key"})
        self.assertEqual(document["source"], "ledger")
        self.assertEqual(code, 1)          # ledger without a limit is "limit unknown"

    def test_missing_key_skips_the_api_under_auto(self):
        with mock.patch("urllib.request.urlopen") as urlopen:
            code, document = self.run_json([], env={})
        urlopen.assert_not_called()
        self.assertEqual(code, 1)
        self.assertIn("OPENCODE_GO_API_KEY", document["message"])

    def test_monthly_limit_does_not_change_the_api_source(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(API_BODY)):
            code, out = self.run_cli(["--source", "api", "--monthly-limit", "1"],
                                     env={"OPENCODE_GO_API_KEY": "test-key"})
        self.assertEqual(code, 0)
        self.assertIn("5h 2%", out)
        self.assertNotIn("$", out)

    def test_json_shape_for_the_api_source(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(API_BODY)):
            code, document = self.run_json(["--source", "api"],
                                           env={"OPENCODE_GO_API_KEY": "test-key"})
        self.assertEqual(code, 0)
        self.assertEqual(document["source"], "api")
        self.assertFalse(document["stale"])
        self.assertEqual(sorted(document["windows"]), ["monthly", "rolling", "weekly"])
        rolling = document["windows"]["rolling"]
        self.assertEqual(rolling["percent"], 2.0)
        self.assertEqual(rolling["threshold"], 85.0)
        self.assertFalse(rolling["over"])
        self.assertIsNone(rolling["limit_usd"])
        self.assertIsNone(rolling["usd"])
        self.assertEqual(rolling["resets_at"], "2026-11-12T13:04:00Z")


class TestLedgerSource(GoUsageTestCase):
    def fixture(self, anchor=NOW):
        """Entries a fixed distance from the anchor: only the first lands in every window."""
        month_start = anchor.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        write_ledger(self.ledger, [
            ledger_row(iso(anchor - timedelta(hours=1)), 0.1),      # 1 h ago  -> 5h, week, month
            ledger_row(iso(anchor - timedelta(hours=10)), 0.2),     # 10 h ago -> week, month
            ledger_row(iso(anchor - timedelta(days=4)), 0.4),       # 4 d ago  -> week, month
            ledger_row(iso(anchor - timedelta(days=11)), 0.8),      # 11 d ago -> month
            ledger_row(iso(month_start - timedelta(minutes=1)), 1.6),  # last month -> nothing
            {"at": "not a timestamp", "allowance_usd": 9.9},
            {"at": iso(anchor - timedelta(minutes=30)), "allowance_usd": None},
        ])

    def test_window_sums_without_a_limit_print_dollars_and_exit_one(self):
        self.fixture()
        code, message, document = self.check(source="ledger")
        self.assertEqual(code, 1)
        self.assertEqual(document["source"], "ledger")
        self.assertEqual(document["windows"]["rolling"]["usd"], 0.1)
        self.assertEqual(document["windows"]["weekly"]["usd"], 0.7)
        self.assertEqual(document["windows"]["monthly"]["usd"], 1.5)
        self.assertIn("5h $0.1000", message)
        self.assertIn("limit unknown", message)
        for window in ("rolling", "weekly", "monthly"):
            self.assertIsNone(document["windows"][window]["percent"])
            self.assertIn("entries", document["windows"][window])

    def test_monthly_limit_turns_dollars_into_percentages(self):
        self.fixture()
        code, message, document = self.check(source="ledger", monthly_limit=100.0)
        self.assertEqual(code, 0)
        windows = document["windows"]
        self.assertAlmostEqual(windows["rolling"]["percent"], 0.5)     # 0.1 of $20
        self.assertAlmostEqual(windows["weekly"]["percent"], 1.4)      # 0.7 of $50
        self.assertAlmostEqual(windows["monthly"]["percent"], 1.5)     # 1.5 of $100
        self.assertEqual(windows["rolling"]["limit_usd"], 20.0)
        self.assertEqual(windows["weekly"]["limit_usd"], 50.0)
        self.assertEqual(windows["monthly"]["limit_usd"], 100.0)
        self.assertIn("of $20.00 (0%)", message)
        self.assertIn("under 85%/99%/95%", message)

    def test_monthly_limit_can_push_a_window_over(self):
        self.fixture()
        code, message, document = self.check(
            source="ledger", monthly_limit=1.0,
            thresholds={"rolling": 85.0, "weekly": 99.0, "monthly": 95.0})
        self.assertEqual(code, 2)
        self.assertTrue(document["windows"]["monthly"]["over"])
        self.assertIn("OVER", message)

    def test_stale_ledger_is_reported_but_still_judged(self):
        self.fixture()
        code, message, document = self.check(source="ledger", monthly_limit=100.0,
                                             max_age_hours=0.25)
        self.assertEqual(code, 0)
        self.assertTrue(document["stale"])

    def test_missing_ledger_exits_one(self):
        code, document = self.run_json(["--source", "ledger"])
        self.assertEqual(code, 1)
        self.assertEqual(document["source"], "ledger")
        self.assertTrue(document["stale"])
        self.assertIn("no readable ledger", document["message"])
        self.assertEqual(sorted(document["windows"]), ["monthly", "rolling", "weekly"])

    def test_empty_ledger_under_auto_exits_one(self):
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError("offline")):
            code, document = self.run_json(["--monthly-limit", "100"], env={})
        self.assertEqual(code, 1)
        self.assertEqual(document["source"], "ledger")
        self.assertIn("no readable ledger", document["message"])

    def test_monthly_limit_from_the_environment(self):
        self.fixture()
        code, document = self.run_json(["--source", "ledger"],
                                       env={"MU_GO_MONTHLY_LIMIT": "100"})
        self.assertEqual(code, 0)
        self.assertAlmostEqual(document["windows"]["monthly"]["percent"], 1.5)

    def test_zero_monthly_limit_is_refused_without_a_crash(self):
        self.fixture()
        code, document = self.run_json(["--source", "ledger", "--monthly-limit", "0"])
        self.assertEqual(code, 1)
        self.assertIn("invalid monthly limit: 0", document["message"])
        self.assertIn("5h $0.1000", document["message"])
        self.assertNotIn("%", document["message"])
        for window in ("rolling", "weekly", "monthly"):
            self.assertIsNone(document["windows"][window]["percent"])
            self.assertIsNone(document["windows"][window]["limit_usd"])

    def test_zero_monthly_limit_from_the_environment_is_refused(self):
        self.fixture()
        code, out = self.run_cli(["--source", "ledger"], env={"MU_GO_MONTHLY_LIMIT": "0"})
        self.assertEqual(code, 1)
        self.assertIn("invalid monthly limit: 0", out)

    def test_negative_monthly_limit_never_prints_negative_percentages(self):
        self.fixture()
        code, out = self.run_cli(["--source", "ledger", "--monthly-limit", "-5"])
        self.assertEqual(code, 1)
        self.assertIn("invalid monthly limit: -5", out)
        self.assertNotIn("-25%", out)
        self.assertNotIn("$-", out)

    def test_cli_prints_dollars_for_the_ledger(self):
        self.fixture()
        code, out = self.run_cli(["--source", "ledger"])
        self.assertEqual(code, 1)
        self.assertIn("go allowance: 5h $0.1000", out)
        self.assertIn("month $1.5000", out)
        self.assertNotIn("%", out)

    def test_ledger_windows_follow_the_injected_clock_not_the_wall_clock(self):
        anchor = datetime(2027, 6, 20, 12, 0, tzinfo=timezone.utc)
        self.fixture(anchor)
        code, out = self.run_cli(["--source", "ledger"], now=anchor)
        self.assertEqual(code, 1)
        self.assertIn("go allowance: 5h $0.1000", out)
        self.assertIn("weekly $0.7000", out)
        self.assertIn("month $1.5000", out)

        code, out = self.run_cli(["--source", "ledger", "--monthly-limit", "100"], now=anchor)
        self.assertEqual(code, 0)
        self.assertIn("month $1.5000 of $100.00", out)


class TestUnpricedLedgerRows(GoUsageTestCase):
    """AUDIT R6 F4 Deferral 1: a ledger line that carries tokens but no allowance amount must never be
    priced as $0 (which reads as "this cost nothing"); the report says nothing is priced instead."""

    def token_row(self, at):
        return {"at": iso(at), "model": "glm-5.3-flash", "label": "social_writer",
                "input_tokens": 200, "cached_tokens": 0, "output_tokens": 50}

    def test_a_ledger_of_token_rows_only_says_it_cannot_price_them(self):
        write_ledger(self.ledger, [self.token_row(NOW - timedelta(minutes=5))])
        code, message, document = self.check(source="ledger", monthly_limit=60.0)
        self.assertEqual(code, 1)
        self.assertIn("no priced entries", message)
        self.assertIn("1 entry carries no allowance amount", message)
        self.assertNotIn("$0.0000", message)
        self.assertEqual(document["windows"]["rolling"]["entries"], 0)
        self.assertEqual(document["unpriced"], 1)

    def test_a_mixed_ledger_reports_the_unpriced_rows_beside_the_dollars(self):
        write_ledger(self.ledger, [
            self.token_row(NOW - timedelta(minutes=5)),
            ledger_row(iso(NOW - timedelta(hours=1)), 0.1),
        ])
        code, message, document = self.check(source="ledger")
        self.assertEqual(code, 1)                       # still "limit unknown" without a limit
        self.assertIn("5h $0.1000", message)
        self.assertIn("1 unpriced", message)
        self.assertEqual(document["unpriced"], 1)

    def test_the_priced_row_shape_is_unchanged_when_nothing_is_unpriced(self):
        write_ledger(self.ledger, [ledger_row(iso(NOW - timedelta(hours=1)), 0.1)])
        code, message, document = self.check(source="ledger")
        self.assertEqual(document["unpriced"], 0)
        self.assertNotIn("unpriced", message)

    def test_one_tiny_call_never_prints_as_zero_dollars(self):
        write_ledger(self.ledger, [ledger_row(iso(NOW - timedelta(minutes=5)), 1.2e-05)])
        code, message, document = self.check(source="ledger", monthly_limit=60.0)
        self.assertEqual(code, 0)
        self.assertIn("5h <$0.0001", message)
        self.assertNotIn("$0.0000", message)
        self.assertEqual(document["windows"]["rolling"]["usd"], 1.2e-05)


class TestFrozenClockFormatting(GoUsageTestCase):
    def test_api_reset_labels_follow_the_injected_clock(self):
        with mock.patch("urllib.request.urlopen", return_value=FakeResponse(API_BODY)):
            code, out = self.run_cli(["--source", "api"],
                                     env={"OPENCODE_GO_API_KEY": "test-key"},
                                     now=datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc))
        self.assertEqual(code, 0)
        self.assertIn("(resets Nov 12)", out)


if __name__ == "__main__":
    unittest.main()
