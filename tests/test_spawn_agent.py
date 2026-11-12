"""Tests for tools/spawn_agent.sh's DeepSeek launch types and hand-off guard (U1).

Offline and side-effect free: every call passes --dry-run and an explicit environment, and a `tmux`
stub on PATH records any attempt to open a window so a test fails loudly if one is made.
"""

from __future__ import annotations

import re
import subprocess
import time
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


def test_deepseek_dry_run_creates_a_session_with_no_window(env: dict[str, str]) -> None:
    result = _run(env, "--dry-run", "b1", "deepseek", "/builder")

    assert result.returncode == 0, result.stderr
    assert "no_window=1" in result.stdout                       # JR 2026-11-12: no tmux window for DeepSeek
    assert "opencode_session.sh --session-only" in result.stdout
    assert "--run /builder" in result.stdout                    # started inside OpenCode, not in a window
    assert "claude-deepseek" not in result.stdout
    _assert_no_window(env)


def test_deepseek_plain_prompt_gets_the_label_prefix(env: dict[str, str]) -> None:
    result = _run(env, "--dry-run", "b1", "deepseek", "fix the flaky test")

    assert result.returncode == 0, result.stderr
    # The run line is printf %q-quoted, so spaces in the prompt come back as `\ `.
    unquoted = result.stdout.replace("\\ ", " ")
    assert "Your session label is b1. fix the flaky test" in unquoted
    assert "opencode_session.sh --session-only" in result.stdout
    assert "--run Your\\ session\\ label\\ is\\ b1.\\ fix\\ the\\ flaky\\ test" in result.stdout
    _assert_no_window(env)


def test_deepseek_start_opens_no_window_and_runs_the_session_in_opencode(
    env: dict[str, str], tmp_path: Path
) -> None:
    """The real path: create the session, then drive it with `opencode run` in the background."""
    calls = tmp_path / "calls.log"
    stub = tmp_path / "stub-bin" / "opencode"
    stub.parent.mkdir()
    stub.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "{calls}"\nexit 0\n', encoding="utf-8")
    stub.chmod(0o755)
    env["MU_OPENCODE_BIN"] = str(stub)
    env["MU_ALLOW_DEEPSEEK_SPAWN"] = "1"
    env["MU_AGENT_LOG_DIR"] = str(tmp_path / "logs")

    result = _run(env, "b1", "deepseek", "/builder")

    assert result.returncode == 0, result.stderr
    started = re.search(r"session (ses_mu_\d{10}_\d{5})$", result.stdout, re.M)
    assert started, result.stdout
    assert f"log: {tmp_path / 'logs' / 'b1.log'}" in result.stdout
    for _ in range(50):                       # the run is backgrounded; wait for the stub to log it
        if calls.exists() and "run --session" in calls.read_text(encoding="utf-8"):
            break
        time.sleep(0.05)
    lines = calls.read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("api post /api/session ") for line in lines), lines
    assert any(line == f"run --session {started.group(1)} --format default /builder" for line in lines), lines
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
    assert "opencode_session.sh --session-only" in result.stdout
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
    assert "opencode_session.sh --session-only" in result.stdout
    _assert_no_window(env)


def test_muse_dry_run_creates_a_session_with_no_window(env: dict[str, str]) -> None:
    result = _run(env, "--dry-run", "muse-lab", "muse", "/builder")

    assert result.returncode == 0, result.stderr
    assert "no_window=1" in result.stdout                       # JR 2026-11-12: no tmux window for Spark
    assert "muse_session.sh --session-only" in result.stdout
    assert "--run /builder" in result.stdout
    assert "opencode_session.sh" not in result.stdout
    _assert_no_window(env)


def test_muse_plain_prompt_gets_the_label_prefix(env: dict[str, str]) -> None:
    result = _run(env, "--dry-run", "muse-lab", "muse", "run the pilot")

    assert result.returncode == 0, result.stderr
    unquoted = result.stdout.replace("\\ ", " ")
    assert "Your session label is muse-lab. run the pilot" in unquoted
    assert "muse_session.sh --session-only" in result.stdout
    _assert_no_window(env)


def test_muse_needs_no_deepseek_spawn_flag(env: dict[str, str]) -> None:
    env["MU_AGENT_CLASS"] = "deepseek"

    result = _run(env, "--dry-run", "muse-lab", "muse", "/builder")

    assert result.returncode == 0, result.stderr
    assert "muse_session.sh --session-only" in result.stdout
    _assert_no_window(env)


def test_usage_lists_the_muse_type(env: dict[str, str]) -> None:
    result = _run(env)

    assert result.returncode == 1
    assert "<claude|opus|deepseek|deepseek-claude|muse|codex|gemini|argon|hydrogen>" in result.stderr
    _assert_no_window(env)


def test_hydrogen_dry_run_launches_antigravity_with_the_route_marker(env: dict[str, str]) -> None:
    result = _run(env, "--dry-run", "hydrogen-audit", "hydrogen", "/audit")

    assert result.returncode == 0, result.stderr
    assert "agy --model gemini-4-hydrogen-high -i /audit" in result.stdout
    assert "MU_AGENT_CLASS=hydrogen" in result.stdout
    assert "MU_AGENT_MODEL=gemini-4-hydrogen-high" in result.stdout
    _assert_no_window(env)


def test_hydrogen_model_override_sets_both_flag_and_marker(env: dict[str, str]) -> None:
    env["MU_HYDROGEN_MODEL"] = "gemini-4-hydrogen-xhigh"

    result = _run(env, "--dry-run", "hydrogen-audit", "hydrogen", "/audit")

    assert result.returncode == 0, result.stderr
    assert "agy --model gemini-4-hydrogen-xhigh" in result.stdout
    assert "MU_AGENT_MODEL=gemini-4-hydrogen-xhigh" in result.stdout
    _assert_no_window(env)


def test_hydrogen_needs_no_deepseek_spawn_flag(env: dict[str, str]) -> None:
    env["MU_AGENT_CLASS"] = "deepseek"

    result = _run(env, "--dry-run", "hydrogen-audit", "hydrogen", "/audit")

    assert result.returncode == 0, result.stderr
    assert "agy --model" in result.stdout
    _assert_no_window(env)


def test_argon_dry_run_launches_antigravity(env: dict[str, str]) -> None:
    result = _run(env, "--dry-run", "argon-audit", "argon", "/audit")

    assert result.returncode == 0, result.stderr
    assert "agy --model gemini-4-argon-medium -i /audit" in result.stdout
    _assert_no_window(env)


def test_hydrogen_plain_prompt_gets_the_label_prefix(env: dict[str, str]) -> None:
    result = _run(env, "--dry-run", "hydrogen-lab", "hydrogen", "read the spec")

    assert result.returncode == 0, result.stderr
    unquoted = result.stdout.replace("\\ ", " ")
    assert "Your session label is hydrogen-lab. read the spec" in unquoted
    _assert_no_window(env)


@pytest.mark.parametrize(("agent", "expected"), [
    ("opus", "claude-opus-5-5"),
    ("codex", "gpt-6.1-sol"),
    ("argon", "gemini-4-argon-medium"),
    ("hydrogen", "gemini-4-hydrogen-high"),
    ("deepseek-claude", "deepseek-v4.1-flash"),
])
def test_each_route_names_its_actor_for_the_switchboard(
    env: dict[str, str], agent: str, expected: str
) -> None:
    """JR 2026-11-12: every route exports MU_AGENT_MODEL, so features._actor records the model, not JR."""
    result = _run(env, "--dry-run", f"actor-{agent}", agent, "/x")

    assert result.returncode == 0, result.stderr
    assert f"MU_AGENT_MODEL={expected}" in result.stdout
    _assert_no_window(env)


def test_a_bare_route_records_unknown_rather_than_jr(env: dict[str, str]) -> None:
    for agent in ("claude", "gemini"):
        result = _run(env, "--dry-run", f"bare-{agent}", agent, "/x")

        assert result.returncode == 0, result.stderr
        assert "MU_AGENT_MODEL=unknown" in result.stdout
    _assert_no_window(env)


def test_route_model_overrides_reach_the_export(env: dict[str, str]) -> None:
    env.update({"MU_CLAUDE_MODEL": "claude-fable-5-1", "MU_CODEX_MODEL": "gpt-6.1-sol-xhigh",
                "MU_GEMINI_MODEL": "gemini-4-argon-high", "MU_OPUS_MODEL": "claude-opus-5-6"})

    assert "MU_AGENT_MODEL=claude-fable-5-1" in _run(env, "--dry-run", "o1", "claude", "/x").stdout
    assert "MU_AGENT_MODEL=gpt-6.1-sol-xhigh" in _run(env, "--dry-run", "o2", "codex", "/x").stdout
    assert "MU_AGENT_MODEL=gemini-4-argon-high" in _run(env, "--dry-run", "o3", "gemini", "/x").stdout
    opus = _run(env, "--dry-run", "o4", "opus", "/x").stdout
    assert "MU_AGENT_MODEL=claude-opus-5-6" in opus and "--model claude-opus-5-6" in opus
    _assert_no_window(env)


def test_the_opencode_wrappers_set_the_actor_themselves(env: dict[str, str]) -> None:
    deepseek = _run(env, "--dry-run", "wrap-ds", "deepseek", "/x").stdout
    assert "MU_AGENT_MODEL=" not in deepseek and "opencode_session.sh --session-only" in deepseek
    muse = _run(env, "--dry-run", "wrap-muse", "muse", "/x").stdout
    assert "MU_AGENT_MODEL=" not in muse and "muse_session.sh --session-only" in muse
    _assert_no_window(env)


@pytest.mark.parametrize("agent", ["claude", "opus", "codex", "gemini", "argon", "hydrogen"])
def test_only_codex_claude_and_agy_routes_get_a_window(env: dict[str, str], agent: str) -> None:
    """JR 2026-11-12: new windows are for codex, claude and agy; DeepSeek/Spark share the OpenCode one."""
    result = _run(env, "--dry-run", f"win-{agent}", agent, "/x")

    assert result.returncode == 0, result.stderr
    assert f"window=win-{agent}" in result.stdout
    assert "no_window" not in result.stdout
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
