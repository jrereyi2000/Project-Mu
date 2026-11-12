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


def _real_tmux(env: dict[str, str], tmp_path: Path) -> str:
    """Point the script at a real tmux server on its own socket, so a window can be inspected."""
    sock = f"mu-spawn-test-{tmp_path.name}"
    wrapper = Path(env["PATH"].split(":")[0]) / "tmux"
    wrapper.write_text(f'#!/usr/bin/env bash\nexec /usr/bin/tmux -L {sock} "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    return sock


def test_deepseek_dry_run_creates_a_session_with_no_window(env: dict[str, str]) -> None:
    result = _run(env, "--dry-run", "b1", "deepseek", "/builder")

    assert result.returncode == 0, result.stderr
    assert "no_window=1" in result.stdout                       # JR 2026-11-12: no tmux window for DeepSeek
    assert "opencode_session.sh --session-only" in result.stdout
    assert "prompt: /builder" in result.stdout
    assert "claude-deepseek" not in result.stdout
    _assert_no_window(env)


def test_deepseek_plain_prompt_gets_the_label_prefix(env: dict[str, str]) -> None:
    result = _run(env, "--dry-run", "b1", "deepseek", "fix the flaky test")

    assert result.returncode == 0, result.stderr
    # The run line is printf %q-quoted, so spaces in the prompt come back as `\ `.
    unquoted = result.stdout.replace("\\ ", " ")
    assert "Your session label is b1. fix the flaky test" in unquoted
    assert "opencode_session.sh --session-only" in result.stdout
    _assert_no_window(env)


def test_deepseek_start_opens_no_window_and_prints_the_session(
    env: dict[str, str], tmp_path: Path
) -> None:
    """The real path: the wrapper runs with a stub binary, so no session is started and no tab opened."""
    stub = tmp_path / "stub-bin" / "opencode"
    stub.parent.mkdir()
    stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    env["MU_OPENCODE_BIN"] = str(stub)
    env["MU_ALLOW_DEEPSEEK_SPAWN"] = "1"

    result = _run(env, "b1", "deepseek", "/builder")

    assert result.returncode == 0, result.stderr
    assert re.search(r"^session: ses_mu_\d{10}_\d{5}$", result.stdout, re.M)
    assert "/builder" in result.stdout and "tab" in result.stdout
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
    assert "prompt: /builder" in result.stdout
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


# --- the spawn guard's marker coverage (R3 F3) -------------------------------------------------


@pytest.mark.parametrize(("name", "value"), [
    ("MU_AGENT_MODEL", "deepseek-v4.1-flash"),
    ("MU_OPENCODE_MODEL", "opencode-go/deepseek-v4.1-flash"),
    ("MU_AGENT_CLASS", "DeepSeek"),
    ("MU_AGENT_CLASS", "  DeepSeek  "),
    ("OPENCODE", "1"),
    ("OPENCODE_SESSION_ID", "ses_mu_1794506156_15570"),
])
def test_guard_refuses_every_deepseek_route_marker(
    env: dict[str, str], name: str, value: str
) -> None:
    """R3 F3: the guard covered only MU_AGENT_CLASS=v deepseek, so other markers slipped through."""
    env[name] = value

    result = _run(env, "--dry-run", "b1", "deepseek", "/builder")

    assert result.returncode == 3, result.stdout
    assert "MU_ALLOW_DEEPSEEK_SPAWN=1" in result.stderr
    _assert_no_window(env)


def test_a_windowed_route_is_an_unknown_caller_to_the_guard(env: dict[str, str]) -> None:
    """The guard covers the DeepSeek types only; a claude/opus/codex/agy spawn stays allowed."""
    env.update({"MU_AGENT_CLASS": "deepseek", "OPENCODE": "1"})

    result = _run(env, "--dry-run", "child-opus", "opus", "/builder")

    assert result.returncode == 0, result.stderr
    _assert_no_window(env)


# --- a child must not inherit the caller's route markers (R3 F2) -------------------------------


ROUTE_MARKERS = (
    "MU_AGENT_CLASS", "MU_AGENT_HARNESS", "MU_AGENT_MODEL", "MU_OPENCODE_MODEL",
    "MU_OPENCODE_SESSION", "MU_ALLOW_DEEPSEEK_SPAWN",
    "OPENCODE", "OPENCODE_SESSION_ID", "OPENCODE_TERMINAL",
)


def test_a_windowed_child_command_unsets_the_callers_route_markers(env: dict[str, str]) -> None:
    env.update({"MU_AGENT_CLASS": "deepseek", "MU_AGENT_HARNESS": "opencode",
                "MU_AGENT_MODEL": "deepseek-v4.1-flash",
                "MU_OPENCODE_MODEL": "opencode-go/deepseek-v4.1-flash",
                "MU_OPENCODE_SESSION": "ses_mu_PARENT_99999",
                "MU_ALLOW_DEEPSEEK_SPAWN": "1", "OPENCODE": "1",
                "OPENCODE_SESSION_ID": "ses_mu_PARENT_99999"})

    result = _run(env, "--dry-run", "child-opus", "opus", "/builder")

    assert result.returncode == 0, result.stderr
    runs = result.stdout.split("runs (in bash -lic): ", 1)[1]
    tokens = runs.replace("\\", "").split()
    unset = {tokens[i + 1] for i, token in enumerate(tokens) if token == "-u"}
    for var in ROUTE_MARKERS:
        assert var in unset, (var, runs)
    assert "MU_AGENT_MODEL=claude-opus-5-5" in runs       # the child's own route still sets its actor
    _assert_no_window(env)


def test_a_deepseek_child_mints_its_own_session_not_the_parents(
    env: dict[str, str], tmp_path: Path
) -> None:
    stub = tmp_path / "stub-bin" / "opencode"
    stub.parent.mkdir()
    stub.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    stub.chmod(0o755)
    env.update({"MU_OPENCODE_BIN": str(stub), "MU_ALLOW_DEEPSEEK_SPAWN": "1",
                "MU_OPENCODE_SESSION": "ses_mu_PARENT_99999"})

    result = _run(env, "child-ds", "deepseek", "/builder")

    assert result.returncode == 0, result.stderr
    match = re.search(r"^session: (ses_mu_\d+_\d+)$", result.stdout, re.M)
    assert match, result.stdout
    assert match.group(1) != "ses_mu_PARENT_99999"
    _assert_no_window(env)


def test_a_spawned_window_does_not_inherit_the_callers_markers(
    env: dict[str, str], tmp_path: Path
) -> None:
    """The real path on an isolated tmux server: the child's own environment is inspected."""
    sock = _real_tmux(env, tmp_path)
    fake = tmp_path / "fake" / "claude-deepseek"
    fake.parent.mkdir()
    fake.write_text('#!/usr/bin/env bash\nenv > "${MU_CHILD_ENV:?}"\n', encoding="utf-8")
    fake.chmod(0o755)
    child_env = tmp_path / "child.env"
    env.update({
        "MU_DEEPSEEK_CMD": str(fake), "MU_CHILD_ENV": str(child_env),
        "MU_AGENT_CLASS": "deepseek", "MU_AGENT_HARNESS": "opencode",
        "MU_AGENT_MODEL": "deepseek-v4.1-flash",
        "MU_OPENCODE_MODEL": "opencode-go/deepseek-v4.1-flash",
        "MU_OPENCODE_SESSION": "ses_mu_PARENT_99999",
        "MU_ALLOW_DEEPSEEK_SPAWN": "1", "OPENCODE": "1",
        "OPENCODE_SESSION_ID": "ses_mu_PARENT_99999",
    })

    try:
        result = _run(env, "child-window", "deepseek-claude", "/builder")
        assert result.returncode == 0, result.stderr + result.stdout
        deadline = time.monotonic() + 10
        while not child_env.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert child_env.exists(), "the spawned window never ran the agent"
        child = dict(
            line.split("=", 1) for line in child_env.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
        for var in ROUTE_MARKERS:
            if var == "MU_AGENT_MODEL":
                continue        # the route re-sets its own actor after the scrub
            assert var not in child, (var, child.get(var))
        assert child["MU_AGENT_MODEL"] == "deepseek-v4.1-flash"

        shown = subprocess.run(
            ["/usr/bin/tmux", "-L", sock, "show-environment", "-g"],
            capture_output=True, text=True, check=False,
        ).stdout
        global_env = {line.split("=", 1)[0].lstrip("-") for line in shown.splitlines() if "=" in line}
        for var in ROUTE_MARKERS:
            assert var not in global_env, (var, shown)
    finally:
        subprocess.run(["/usr/bin/tmux", "-L", sock, "kill-server"],
                       capture_output=True, text=True, check=False)
