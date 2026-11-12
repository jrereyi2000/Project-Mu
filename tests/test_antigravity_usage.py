"""Tests for the Antigravity usage ledger (tools/antigravity_usage.py).

Offline: every case uses a tmp ledger, and the recorded payload is the exact shape `agy -p
--output-format json` returned on 2026-11-12 (a 9,657-token Hydrogen probe).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools import antigravity_usage as au

REPO_ROOT = Path(__file__).resolve().parents[1]
PROBE = {
    "conversation_id": "c802f7bb-22a4-4c4f-8aba-f1a4117bdeff",
    "status": "SUCCESS",
    "response": "ok\n",
    "duration_seconds": 2.11993504,
    "num_turns": 1,
    "usage": {"input_tokens": 9630, "output_tokens": 27, "thinking_tokens": 22,
              "cache_read_tokens": 0, "total_tokens": 9657},
}


def test_parse_result_reads_the_cli_usage_block():
    record = au.parse_result(PROBE, model="gemini-4-hydrogen-high")

    assert record["input_tokens"] == 9630
    assert record["output_tokens"] == 27
    assert record["thinking_tokens"] == 22
    assert record["total_tokens"] == 9657
    assert record["conversation_id"] == PROBE["conversation_id"]
    assert record["model"] == "gemini-4-hydrogen-high"
    # 9,630 x $1.50/M + 27 x $7.50/M = $0.014445 + $0.0002025
    assert record["list_cost_usd"] == pytest.approx(0.014647, abs=1e-6)


def test_parse_result_keeps_thinking_inside_output():
    """agy's own probe totals input + output; thinking is a subset, so it is never added again."""
    record = au.parse_result(PROBE)
    assert record["total_tokens"] == record["input_tokens"] + record["output_tokens"]


def test_parse_result_accepts_a_bare_usage_block_and_rejects_a_body_without_one():
    assert au.parse_result(PROBE["usage"])["total_tokens"] == 9657
    with pytest.raises(au.BadRecord):
        au.parse_result({"status": "SUCCESS", "response": "no usage here"})
    with pytest.raises(au.BadRecord):
        au.parse_result({"usage": {"input_tokens": 0, "output_tokens": 0}})


def test_cache_reads_are_a_subset_of_input_tokens_for_pricing():
    record = {"model": "gemini-4-hydrogen-high", "input_tokens": 1_000_000,
              "cache_read_tokens": 1_000_000, "output_tokens": 0}
    assert au.list_cost(record) == pytest.approx(0.075)


def test_model_family_matches_the_variants_and_nothing_else():
    assert au.model_family("gemini-4-hydrogen-xhigh") == "hydrogen"
    assert au.model_family("gemini-4-argon-medium") == "argon"
    assert au.model_family("claude-opus-5-5") is None
    assert au.list_cost({"model": "unknown", "input_tokens": 10, "output_tokens": 10}) == 0.0


def test_record_then_summarise_round_trip(tmp_path: Path):
    ledger = tmp_path / "usage.jsonl"
    au.append_record(au.parse_result(PROBE, model="gemini-4-hydrogen-high"), ledger)
    au.append_record(au.parse_result(PROBE, model="gemini-4-hydrogen-high"), ledger)

    rows, skipped = au.entries(ledger)
    summary = au.summarise(rows, since_hours=24)

    assert skipped == 0
    assert len(rows) == 2
    assert summary["runs"] == 2
    assert summary["tokens"] == 2 * 9657
    assert summary["models"] == {"gemini-4-hydrogen-high": 2}


def test_check_exits_1_with_nothing_measured_and_2_over_budget(tmp_path: Path):
    ledger = tmp_path / "usage.jsonl"
    code, message, _ = au.check(path=ledger)
    assert code == 1 and "nothing measured" in message

    au.append_record(au.parse_result(PROBE, model="gemini-4-hydrogen-high"), ledger)
    code, message, document = au.check(path=ledger, max_tokens=100_000)
    assert code == 0 and document["plan_quota"].startswith("unknown")

    code, message, _ = au.check(path=ledger, max_tokens=9_000)
    assert code == 2 and "OVER" in message


def test_check_never_invents_a_plan_percentage(tmp_path: Path):
    ledger = tmp_path / "usage.jsonl"
    au.append_record(au.parse_result(PROBE, model="gemini-4-hydrogen-high"), ledger)
    code, message, document = au.check(path=ledger, max_usd=1.0)
    assert code == 0
    assert "%" not in message
    assert document["plan_quota"].startswith("unknown")


def test_entries_skips_a_malformed_line(tmp_path: Path):
    ledger = tmp_path / "usage.jsonl"
    ledger.write_text("{not json}\n" + json.dumps(au.parse_result(PROBE)) + "\n", encoding="utf-8")
    rows, skipped = au.entries(ledger)
    assert len(rows) == 1 and skipped == 1


def test_cli_record_appends_and_reports(tmp_path: Path):
    ledger = tmp_path / "usage.jsonl"
    result = json.loads(json.dumps(PROBE))
    payload = tmp_path / "probe.json"
    payload.write_text(json.dumps(result), encoding="utf-8")

    recorded = subprocess.run(
        [sys.executable, "-m", "tools.antigravity_usage", "record", str(payload),
         "--model", "gemini-4-hydrogen-high", "--ledger", str(ledger)],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert recorded.returncode == 0, recorded.stderr
    assert "9,657 tokens" in recorded.stdout

    checked = subprocess.run(
        [sys.executable, "-m", "tools.antigravity_usage", "--ledger", str(ledger),
         "--max-tokens", "20000", "--json"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert checked.returncode == 0, checked.stderr
    document = json.loads(checked.stdout)
    assert document["runs"] == 1
    assert document["tokens"] == 9657


def test_cli_record_rejects_a_body_without_usage(tmp_path: Path):
    payload = tmp_path / "bad.json"
    payload.write_text('{"status": "SUCCESS"}', encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "tools.antigravity_usage", "record", str(payload),
         "--ledger", str(tmp_path / "usage.jsonl")],
        cwd=REPO_ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 3
    assert "cannot record" in result.stderr
