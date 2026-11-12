"""Tests for tools/spawn_agent.sh's DeepSeek launch types and hand-off guard (U1).

Offline and side-effect free: every call passes --dry-run and an explicit environment, and a `tmux`
stub on PATH records any attempt to open a window so a test fails loudly if one is made.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "tools" / "spawn_agent.sh"

TMUX_STUB = """#!/usr/bin/env bash
printf 'tmux %s\\n' "$*" >> "${MU_TMUX_MARKER:?}"
exit 1
"""


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    """An explicit environment with nothing of JR's tmux or shell in it."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tmux = bin_dir / "tmux"
    tmux.write_text(TMUX_STUB, encoding="utf-8")
    tmux.chmod(0o755)
    home = tmp_path / "home"
    home.mkdir()
    return {
        "PATH": f"{bin_dir}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "MU_TMUX_SESSION": "mu-test",
        "MU_REPO": str(REPO_ROOT),
        "MU_TMUX_MARKER": str(tmp_path / "tmux-called"),
    }


def _run(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _assert_no_window(env: dict[str, str]) -> None:
    marker = Path(env["MU_TMUX_MARKER"])
    assert not marker.exists(), marker.read_text(encoding="utf-8") if marker.exists() else ""


def test_deepseek_dry_run_launches_the_opencode_wrapper(env: dict[str, str]) -> None:
    result = _run(env, "--dry-run", "b1", "deepseek", "/builder")

    assert result.returncode == 0, result.stderr
    assert "opencode_session.sh --prompt /builder" in result.stdout
    assert "claude-deepseek" not in result.stdout
    _assert_no_window(env)


def test_deepseek_plain_prompt_gets_the_label_prefix(env: dict[str, str]) -> None:
    result = _run(env, "--dry-run", "b1", "deepseek", "fix the flaky test")

    assert result.returncode == 0, result.stderr
    # The run line is printf %q-quoted, so spaces in the prompt come back as `\ `.
    unquoted = result.stdout.replace("\\ ", " ")
    assert "Your session label is b1. fix the flaky test" in unquoted
    assert "opencode_session.sh --prompt" in result.stdout
    _assert_no_window(env)


def test_deepseek_claude_dry_run_launches_the_legacy_wrapper(
    env: dict[str, str], tmp_path: Path
) -> None:
    fake = tmp_path / "fake" / "claude-deepseek"
    env["MU_DEEPSEEK_CMD"] = str(fake)

    result = _run(env, "--dry-run", "b1", "deepseek-claude", "/builder")

    assert result.returncode == 0, result.stderr
    assert str(fake) in result.stdout
    assert "opencode_session.sh" not in result.stdout
    _assert_no_window(env)


def test_guard_refuses_a_deepseek_marked_caller(env: dict[str, str]) -> None:
    env["MU_AGENT_CLASS"] = "deepseek"

    result = _run(env, "--dry-run", "b1", "deepseek", "/builder")

    assert result.returncode == 3
    assert "MU_ALLOW_DEEPSEEK_SPAWN=1" in result.stderr
    assert result.stdout == ""
    _assert_no_window(env)


def test_guard_allows_a_deepseek_caller_that_declares_the_rule(env: dict[str, str]) -> None:
    env["MU_AGENT_CLASS"] = "deepseek"
    env["MU_ALLOW_DEEPSEEK_SPAWN"] = "1"

    result = _run(env, "--dry-run", "b1", "deepseek", "/builder")

    assert result.returncode == 0, result.stderr
    assert "opencode_session.sh --prompt /builder" in result.stdout
    _assert_no_window(env)


def test_guard_covers_the_legacy_claude_wrapper_type(env: dict[str, str]) -> None:
    env["MU_AGENT_CLASS"] = "deepseek"

    result = _run(env, "--dry-run", "b1", "deepseek-claude", "/builder")

    assert result.returncode == 3
    assert "MU_ALLOW_DEEPSEEK_SPAWN=1" in result.stderr
    _assert_no_window(env)


def test_guard_refuses_a_legacy_deepseek_endpoint_caller(env: dict[str, str]) -> None:
    env["ANTHROPIC_BASE_URL"] = "https://api.deepseek.com/anthropic"

    result = _run(env, "--dry-run", "b1", "deepseek", "/builder")

    assert result.returncode == 3
    assert "MU_ALLOW_DEEPSEEK_SPAWN=1" in result.stderr
    _assert_no_window(env)


def test_guard_refuses_a_legacy_deepseek_model_caller(env: dict[str, str]) -> None:
    env["ANTHROPIC_MODEL"] = "deepseek-flash"

    result = _run(env, "--dry-run", "b1", "deepseek", "/builder")

    assert result.returncode == 3
    _assert_no_window(env)


def test_a_claude_caller_may_spawn_a_deepseek_session(env: dict[str, str]) -> None:
    env["MU_AGENT_CLASS"] = "claude"

    result = _run(env, "--dry-run", "b1", "deepseek", "/builder")

    assert result.returncode == 0, result.stderr
    assert "opencode_session.sh --prompt /builder" in result.stdout
    _assert_no_window(env)


def test_unknown_agent_exits_2(env: dict[str, str]) -> None:
    result = _run(env, "--dry-run", "b1", "bogus", "/builder")

    assert result.returncode == 2
    assert "unknown agent 'bogus'" in result.stderr
    _assert_no_window(env)


def test_bad_label_exits_2(env: dict[str, str]) -> None:
    result = _run(env, "--dry-run", "Bad_Label", "deepseek", "/builder")

    assert result.returncode == 2
    assert "kebab-case" in result.stderr
    _assert_no_window(env)
