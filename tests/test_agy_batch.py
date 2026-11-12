"""Tests for tools/agy_batch.sh: the print-mode Hydrogen batch launcher.

Offline only: every case runs the launcher against a stub `agy` (MU_AGY_BIN), so no model is called
and nothing is spent. The stub reports its argv and prints the JSON result the case needs, the same
shape `agy --output-format json` really returns (docs/reference/AUDIT_QUEUE_INVENTORY.md §11.1).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = REPO_ROOT / "tools" / "agy_batch.sh"

STUB = """#!/usr/bin/env bash
for arg in "$@"; do printf 'ARG=%s\\n' "$arg"; done >> "$AGY_STUB_LOG"
resume=0
for arg in "$@"; do [ "$arg" = "--conversation" ] && resume=1; done
printf '{"conversation_id": "conv-fixed", "status": "%s", "response": "BATCH-OK", "num_turns": %s, "duration_seconds": 1.25, "usage": {"input_tokens": 120, "output_tokens": 12, "thinking_tokens": 6, "cache_read_tokens": 0, "total_tokens": 132}}\\n' \
  "${AGY_STUB_STATUS:-SUCCESS}" "$([ "$resume" = 1 ] && echo 2 || echo 1)"
"""


@pytest.fixture
def stub(tmp_path: Path) -> Path:
    binary = tmp_path / "agy"
    binary.write_text(STUB, encoding="utf-8")
    binary.chmod(0o755)
    return binary


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def _env(tmp_path: Path, stub: Path, **overrides: str) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "MU_AGY_BIN": str(stub),
        "AGY_STUB_LOG": str(tmp_path / "agy.log"),
        "MU_ANTIGRAVITY_LEDGER": str(tmp_path / "usage.jsonl"),
    }
    env.update(overrides)
    return env


def _run(cwd: Path, env: dict[str, str], *args: str, stdin: str | None = None):
    return subprocess.run(
        [str(LAUNCHER), *args], cwd=str(cwd), env=env, capture_output=True, text=True,
        input=stdin, check=False,
    )


def _args(tmp_path: Path) -> list[str]:
    return [line[len("ARG="):] for line in (tmp_path / "agy.log").read_text().splitlines()]


def test_first_turn_records_the_conversation_id_under_the_label(
    checkout: Path, stub: Path, tmp_path: Path
) -> None:
    prompt = tmp_path / "brief.txt"
    prompt.write_text("Audit R5a and report.\n")

    result = _run(checkout, _env(tmp_path, stub), "--label", "audit-x", str(prompt))

    assert result.returncode == 0, result.stderr
    assert "conversation: conv-fixed" in result.stdout
    assert "status: SUCCESS" in result.stdout
    state = checkout / "data" / "audit_reports" / "audit-x" / "agy_batch.json"
    saved = json.loads(state.read_text())
    assert saved["conversation_id"] == "conv-fixed"
    assert saved["label"] == "audit-x"
    assert saved["model"] == "gemini-4-hydrogen-high"
    args = _args(tmp_path)
    assert args[:2] == ["--model", "gemini-4-hydrogen-high"]
    assert args[args.index("--output-format") + 1] == "json"
    assert args[args.index("--print-timeout") + 1] == "0"
    assert "--conversation" not in args          # first turn opens the conversation
    assert args[-1] == "-p=Audit R5a and report."   # trailing newline stripped by command substitution


def test_second_call_resumes_the_recorded_conversation(
    checkout: Path, stub: Path, tmp_path: Path
) -> None:
    prompt = tmp_path / "brief.txt"
    prompt.write_text("First turn.\n")
    env = _env(tmp_path, stub)
    assert _run(checkout, env, "--label", "audit-x", str(prompt)).returncode == 0

    (tmp_path / "agy.log").unlink()
    prompt.write_text("Second turn.\n")
    result = _run(checkout, env, "--label", "audit-x", str(prompt))

    assert result.returncode == 0, result.stderr
    args = _args(tmp_path)
    assert args[args.index("--conversation") + 1] == "conv-fixed"
    assert "turns: 2" in result.stdout
    assert "resuming audit-x" in result.stderr


def test_label_state_files_are_separate(checkout: Path, stub: Path, tmp_path: Path) -> None:
    prompt = tmp_path / "brief.txt"
    prompt.write_text("Turn.\n")
    env = _env(tmp_path, stub)
    assert _run(checkout, env, "--label", "audit-x", str(prompt)).returncode == 0
    (tmp_path / "agy.log").unlink()

    assert _run(checkout, env, "--label", "audit-y", str(prompt)).returncode == 0

    assert "--conversation" not in _args(tmp_path)   # a new label starts a new conversation
    assert (checkout / "data" / "audit_reports" / "audit-y" / "agy_batch.json").exists()


def test_explicit_state_path_is_used(checkout: Path, stub: Path, tmp_path: Path) -> None:
    prompt = tmp_path / "brief.txt"
    prompt.write_text("Turn.\n")
    state = tmp_path / "custom" / "state.json"

    result = _run(checkout, _env(tmp_path, stub), "--label", "audit-x", "--state", str(state), str(prompt))

    assert result.returncode == 0, result.stderr
    assert json.loads(state.read_text())["conversation_id"] == "conv-fixed"


def test_model_defaults_to_mu_hydrogen_model_and_takes_an_override(
    checkout: Path, stub: Path, tmp_path: Path
) -> None:
    prompt = tmp_path / "brief.txt"
    prompt.write_text("Turn.\n")

    result = _run(checkout, _env(tmp_path, stub, MU_HYDROGEN_MODEL="gemini-4-hydrogen-xhigh"), str(prompt))

    assert result.returncode == 0, result.stderr
    assert _args(tmp_path)[:2] == ["--model", "gemini-4-hydrogen-xhigh"]


def test_non_success_status_exits_one_and_still_records_the_id(
    checkout: Path, stub: Path, tmp_path: Path
) -> None:
    prompt = tmp_path / "brief.txt"
    prompt.write_text("Turn.\n")

    result = _run(checkout, _env(tmp_path, stub, AGY_STUB_STATUS="FAILED"), "--label", "audit-x", str(prompt))

    assert result.returncode == 1
    assert "status: FAILED" in result.stdout
    assert "conversation: conv-fixed" in result.stdout
    assert (checkout / "data" / "audit_reports" / "audit-x" / "agy_batch.json").exists()


def test_no_json_result_exits_three(checkout: Path, tmp_path: Path) -> None:
    quiet = tmp_path / "quiet-agy"
    quiet.write_text("#!/usr/bin/env bash\nprintf 'agy: model overloaded\\n'\n", encoding="utf-8")
    quiet.chmod(0o755)
    prompt = tmp_path / "brief.txt"
    prompt.write_text("Turn.\n")

    result = _run(checkout, _env(tmp_path, quiet), "--label", "audit-x", str(prompt))

    assert result.returncode == 3
    assert "no JSON result" in result.stderr


def test_usage_is_printed_and_ledgered(checkout: Path, stub: Path, tmp_path: Path) -> None:
    prompt = tmp_path / "brief.txt"
    prompt.write_text("Turn.\n")

    result = _run(checkout, _env(tmp_path, stub), "--label", "audit-x", str(prompt))

    assert result.returncode == 0, result.stderr
    assert '"total_tokens": 132' in result.stdout
    assert "usage recorded:" in result.stdout
    record = json.loads((tmp_path / "usage.jsonl").read_text().splitlines()[0])
    assert record["conversation_id"] == "conv-fixed"
    assert record["model"] == "gemini-4-hydrogen-high"
    assert record["total_tokens"] == 132
    assert record["list_cost_usd"] > 0


def test_prompt_is_one_argument_and_board_text_is_not_evaluated(
    checkout: Path, stub: Path, tmp_path: Path
) -> None:
    prompt = tmp_path / "brief.txt"
    prompt.write_text("Handle $(touch nope) as data · not a command.\n")

    result = _run(checkout, _env(tmp_path, stub), "--label", "audit-x", str(prompt))

    assert result.returncode == 0, result.stderr
    assert _args(tmp_path)[-1] == "-p=Handle $(touch nope) as data · not a command."
    assert not (checkout / "nope").exists()


def test_stdin_prompt_and_missing_or_empty_prompt_are_handled(
    checkout: Path, stub: Path, tmp_path: Path
) -> None:
    env = _env(tmp_path, stub)
    assert _run(checkout, env, "--label", "audit-x", "-", stdin="From stdin.\n").returncode == 0
    assert _args(tmp_path)[-1] == "-p=From stdin."

    empty = tmp_path / "empty.txt"
    empty.write_text("   \n")
    assert _run(checkout, env, str(empty)).returncode == 2
    assert _run(checkout, env, str(tmp_path / "absent.txt")).returncode == 2
    assert _run(checkout, env).returncode == 2


def test_unknown_option_and_bad_timeout_are_refused(checkout: Path, stub: Path, tmp_path: Path) -> None:
    env = _env(tmp_path, stub)
    assert _run(checkout, env, "--nope", "x").returncode == 2
    assert _run(checkout, env, "--timeout", "soon", "x").returncode == 2
