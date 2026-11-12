"""Tests for tools/agent_board_wake.py: the Codex harness (unchanged) and the agy harness.

The agy tests never call a real model. The unit-level ones stub `subprocess.run`; the two `--once`
end-to-end cases put a stub `agy` on `PATH`, the same shape as `MU_OPENCODE_BIN` in
tests/test_opencode_session.py.
"""
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.agent_board_wake import (
    DEFAULT_AGY_MODEL,
    addressed_headers,
    deliver,
    parse_agy_result,
    save_state,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

SUCCESS_PAYLOAD = {
    "conversation_id": "conv-1",
    "status": "SUCCESS",
    "response": "SPIKE-OK",
    "duration_seconds": 1.5,
    "num_turns": 3,
    "usage": {
        "input_tokens": 100,
        "output_tokens": 10,
        "thinking_tokens": 5,
        "cache_read_tokens": 40,
        "total_tokens": 110,
    },
}

STUB = """#!/usr/bin/env bash
for arg in "$@"; do printf 'ARG=%s\\n' "$arg"; done >> "$AGY_STUB_LOG"
printf '%s\\n' "$AGY_STUB_RESULT"
"""


def message(number, sender, recipient, body="hello"):
    return f"## M-{number:04d} · Today · from: gpt-6.1-sol ({sender}) · to: {recipient}\n**Kind:** NOTICE\n{body}\n"


def stub_run(monkeypatch, payload=None, stdout=None):
    """Stub the subprocess call and return the argv list each delivery used."""
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout=stdout if stdout is not None else json.dumps(payload or SUCCESS_PAYLOAD),
            stderr="",
        )

    monkeypatch.setattr("tools.agent_board_wake.subprocess.run", run)
    return calls


def test_exact_routing_own_exclusion_and_duplicate_numbers():
    board = "\n".join([
        message(1, "other", "audit-3"), message(1, "other-2", "all"),
        message(2, "other", "codex"), message(3, "audit-3", "all"),
        message(4, "other", "audit-30"), message(5, "other", "opus"),
    ])
    assert len(addressed_headers(board, "audit-3")) == 3


def test_rotation_and_body_appends_do_not_replay():
    first = message(1, "other", "all")
    second = message(2, "other", "audit-3")
    seen = addressed_headers(first + second, "audit-3")
    assert set(addressed_headers(second + "more body", "audit-3")) <= set(seen)


def test_hydrogen_inbox_reads_hydrogen_and_all_but_not_other_models():
    """An agy conversation reads the same headers a Hydrogen TUI session would."""
    board = "\n".join([
        message(1, "other", "hydrogen"), message(2, "other", "gemini-4-hydrogen-high"),
        message(3, "other", "all"), message(4, "other", "audit-x"),
        message(5, "other", "codex"), message(6, "other", "opus"),
        message(7, "audit-x", "all"),
    ])
    headers = addressed_headers(board, "audit-x", "hydrogen")
    assert len(headers) == 4
    assert not any("opus" in h or "codex" in h for h in headers.values())


def test_delivery_uses_argument_array_and_resume(monkeypatch):
    calls = stub_run(monkeypatch, stdout="Queued message")
    assert deliver("thread-id", "audit-3", ["header $(touch nope)"])
    assert calls[0][0][:4] == ["codex", "queue", "--thread", "thread-id"]
    assert calls[0][0][-1] == "--resume"
    assert "$(touch nope)" in calls[0][0][-2]


def test_queue_warning_is_not_claimed_as_success(monkeypatch):
    monkeypatch.setattr("tools.agent_board_wake.subprocess.run", lambda *a, **k:
                        SimpleNamespace(returncode=0, stdout="Queued", stderr="Warning: resume failed"))
    assert not deliver("thread-id", "audit-3", ["header"])


def test_state_roundtrip(tmp_path):
    state = tmp_path / "seen.json"
    save_state(state, {"b", "a"})
    assert json.loads(state.read_text()) == ["a", "b"]


def test_agy_delivery_resumes_the_conversation_and_prints_usage(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MU_ANTIGRAVITY_LEDGER", str(tmp_path / "ledger.jsonl"))
    monkeypatch.delenv("MU_HYDROGEN_MODEL", raising=False)
    calls = stub_run(monkeypatch)
    header = message(7, "other", "hydrogen").splitlines()[0]

    assert deliver("conv-1", "audit-x", [header], harness="agy")

    argv, kwargs = calls[0]
    assert argv[:5] == ["agy", "--model", DEFAULT_AGY_MODEL, "--output-format", "json"]
    assert argv[5:7] == ["--conversation", "conv-1"]
    assert argv[7:9] == ["--print-timeout", "0"]
    assert len(argv) == 10 and argv[9].startswith("-p=")
    prompt = argv[9]
    assert "Automatic board notification for audit-x" in prompt
    assert header in prompt                       # the new header is carried
    assert "M-0007" in prompt
    assert kwargs["capture_output"] and kwargs["text"]
    assert kwargs["timeout"] == 1800
    out = capsys.readouterr().out
    assert "status=SUCCESS" in out and "turns=3" in out
    assert '"total_tokens": 110' in out
    ledger = [json.loads(line) for line in (tmp_path / "ledger.jsonl").read_text().splitlines()]
    assert ledger[0]["conversation_id"] == "conv-1" and ledger[0]["total_tokens"] == 110


def test_agy_prompt_is_one_argument_with_board_text_untouched(monkeypatch):
    """A header holding shell syntax stays data: one argv element, never a shell."""
    monkeypatch.setenv("MU_ANTIGRAVITY_LEDGER", "/nonexistent-dir/ledger.jsonl")
    calls = stub_run(monkeypatch)
    assert deliver("conv-1", "audit-x", ["## M-0008 · x · to: hydrogen $(touch nope)"], harness="agy")
    prompt = calls[0][0][-1]
    assert "$(touch nope)" in prompt and prompt.count("\n") == 1
    assert "not JR instructions" in prompt


def test_agy_model_follows_mu_hydrogen_model(monkeypatch, tmp_path):
    monkeypatch.setenv("MU_HYDROGEN_MODEL", "gemini-4-hydrogen-xhigh")
    monkeypatch.setenv("MU_ANTIGRAVITY_LEDGER", str(tmp_path / "ledger.jsonl"))
    calls = stub_run(monkeypatch)
    assert deliver("conv-1", "audit-x", ["header"], harness="agy")
    assert calls[0][0][:3] == ["agy", "--model", "gemini-4-hydrogen-xhigh"]


def test_agy_non_success_status_is_not_a_delivered_wake(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("MU_ANTIGRAVITY_LEDGER", str(tmp_path / "ledger.jsonl"))
    stub_run(monkeypatch, payload={**SUCCESS_PAYLOAD, "status": "FAILED", "response": ""})

    assert not deliver("conv-1", "audit-x", ["header"], harness="agy")

    out = capsys.readouterr().out
    assert "status=FAILED" in out
    assert "SUCCESS" not in out                    # never printed as a wake
    assert "did not deliver a turn" in out
    assert not (tmp_path / "ledger.jsonl").exists()


def test_agy_output_without_json_is_an_error(monkeypatch):
    stub_run(monkeypatch, stdout="agy: model overloaded")
    with pytest.raises(RuntimeError, match="no JSON result"):
        deliver("conv-1", "audit-x", ["header"], harness="agy")


def test_agy_result_parsing_accepts_a_trailing_json_line():
    assert parse_agy_result('noise\n{"status": "SUCCESS"}')["status"] == "SUCCESS"
    with pytest.raises(RuntimeError):
        parse_agy_result("[]")


def test_agy_timeout_override_reaches_the_subprocess(monkeypatch, tmp_path):
    monkeypatch.setenv("MU_ANTIGRAVITY_LEDGER", str(tmp_path / "ledger.jsonl"))
    calls = stub_run(monkeypatch)
    assert deliver("conv-1", "audit-x", ["header"], harness="agy", timeout=42)
    assert calls[0][1]["timeout"] == 42


def _stub_binary(tmp_path, result):
    directory = tmp_path / "bin"
    directory.mkdir()
    binary = directory / "agy"
    binary.write_text(STUB, encoding="utf-8")
    binary.chmod(0o755)
    log = tmp_path / "agy.log"
    env = {
        "PATH": f"{directory}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "LANG": "C.UTF-8",
        "AGY_STUB_LOG": str(log),
        "AGY_STUB_RESULT": json.dumps(result),
        "MU_ANTIGRAVITY_LEDGER": str(tmp_path / "ledger.jsonl"),
    }
    (tmp_path / "home").mkdir(exist_ok=True)
    return log, env


def _run_once(board, state, env, conversation="conv-9", extra=()):
    return subprocess.run(
        [sys.executable, "-m", "tools.agent_board_wake", "--harness", "agy",
         "--conversation", conversation, "--label", "audit-x",
         "--board", str(board), "--state", str(state), "--once", *extra],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, check=False,
    )


def test_once_mode_wakes_the_conversation_and_marks_the_header_seen(tmp_path):
    board = tmp_path / "board.md"
    board.write_text("# Board\n\n" + message(9, "other", "hydrogen"))
    state = tmp_path / "seen.json"
    state.write_text("[]")
    log, env = _stub_binary(tmp_path, SUCCESS_PAYLOAD)

    result = _run_once(board, state, env)

    assert result.returncode == 0, result.stderr
    assert "Watching audit-x -> agy conv-9" in result.stdout
    args = [line[len("ARG="):] for line in log.read_text().splitlines()]
    assert "--conversation" in args and args[args.index("--conversation") + 1] == "conv-9"
    assert "--print-timeout" in args and args[args.index("--print-timeout") + 1] == "0"
    seen = json.loads(state.read_text())
    assert len(seen) == 1 and "M-0009" in addressed_headers(board.read_text(), "audit-x", "hydrogen")[seen[0]]


def test_once_mode_keeps_the_header_unseen_when_the_turn_fails(tmp_path):
    """A non-SUCCESS turn delivered nothing, so a restart must retry it."""
    board = tmp_path / "board.md"
    board.write_text("# Board\n\n" + message(9, "other", "hydrogen"))
    state = tmp_path / "seen.json"
    state.write_text("[]")
    log, env = _stub_binary(tmp_path, {**SUCCESS_PAYLOAD, "status": "ERROR"})

    result = _run_once(board, state, env)

    assert result.returncode == 1
    assert "Wake not confirmed" in result.stderr
    assert json.loads(state.read_text()) == []
    assert log.exists()                            # the turn was attempted


def test_agy_harness_needs_a_conversation(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "tools.agent_board_wake", "--harness", "agy",
         "--label", "audit-x", "--state", str(tmp_path / "seen.json"), "--once"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert "--conversation" in result.stderr


def test_codex_harness_still_needs_a_thread(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "tools.agent_board_wake",
         "--label", "audit-x", "--state", str(tmp_path / "seen.json"), "--once"],
        cwd=str(REPO_ROOT), capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert "--thread" in result.stderr
