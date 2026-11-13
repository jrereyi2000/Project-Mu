"""Tests for tools/opencode_session.sh (OPENCODE_DEEPSEEK_ROUTE_PLAN §2.3, U1).

Offline only: the wrapper is run against a throwaway git checkout and a stub `opencode`
(MU_OPENCODE_BIN), so nothing starts a real OpenCode session and no network is touched.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "tools" / "opencode_session.sh"

STUB = """#!/usr/bin/env bash
for arg in "$@"; do printf 'ARG=%s\\n' "$arg"; done
printf 'MU_AGENT_CLASS=%s\\n' "${MU_AGENT_CLASS:-}"
printf 'MU_AGENT_HARNESS=%s\\n' "${MU_AGENT_HARNESS:-}"
printf 'MU_AGENT_MODEL=%s\\n' "${MU_AGENT_MODEL:-}"
printf 'MU_OPENCODE_MODEL=%s\\n' "${MU_OPENCODE_MODEL:-}"
printf 'MU_OPENCODE_SESSION=%s\\n' "${MU_OPENCODE_SESSION:-}"
"""

DEFAULT_MODEL = "opencode-go/deepseek-v4.1-flash"
SESSION_RE = re.compile(r"^ses_mu_\d{10}_\d{5}$")


@pytest.fixture
def bin_dir(tmp_path: Path) -> Path:
    """A PATH directory holding a stub `opencode` that reports its environment and argv."""
    directory = tmp_path / "bin"
    directory.mkdir()
    stub = directory / "opencode"
    stub.write_text(STUB, encoding="utf-8")
    stub.chmod(0o755)
    return directory


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    """A temporary git checkout carrying a project OpenCode config."""
    repo = tmp_path / "repo"
    (repo / ".opencode").mkdir(parents=True)
    (repo / ".opencode" / "opencode.json").write_text('{"model": "x"}\n', encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def _env(bin_dir: Path, tmp_path: Path, **overrides: str) -> dict[str, str]:
    """A minimal explicit environment, so the caller's own MU_* / ANTHROPIC_* never leak in."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {
        "PATH": f"{bin_dir}:/usr/local/bin:/usr/bin:/bin",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "MU_OPENCODE_BIN": str(bin_dir / "opencode"),
    }
    env.update(overrides)
    return env


def _run(cwd: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(WRAPPER), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _parse(stdout: str) -> tuple[dict[str, str], list[str]]:
    values: dict[str, str] = {}
    args: list[str] = []
    for line in stdout.splitlines():
        if line.startswith("ARG="):
            args.append(line[len("ARG=") :])
        elif "=" in line:
            key, _, value = line.partition("=")
            values[key] = value
    return values, args


def test_refuses_outside_a_git_checkout(tmp_path: Path, bin_dir: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()

    result = _run(plain, _env(bin_dir, tmp_path))

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert "not inside a git checkout" in result.stderr


def test_refuses_a_checkout_without_the_opencode_config(tmp_path: Path, bin_dir: Path) -> None:
    repo = tmp_path / "bare-repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    result = _run(repo, _env(bin_dir, tmp_path))

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.count("\n") == 1
    assert ".opencode/opencode.json" in result.stderr


def test_exports_the_route_markers_and_the_default_model(
    checkout: Path, bin_dir: Path, tmp_path: Path
) -> None:
    result = _run(checkout, _env(bin_dir, tmp_path))

    assert result.returncode == 0, result.stderr
    values, _ = _parse(result.stdout)
    assert values["MU_AGENT_CLASS"] == "deepseek"
    assert values["MU_AGENT_HARNESS"] == "opencode"
    assert values["MU_AGENT_MODEL"] == "deepseek-v4.1-flash"
    assert values["MU_OPENCODE_MODEL"] == DEFAULT_MODEL


def test_honours_a_model_override(checkout: Path, bin_dir: Path, tmp_path: Path) -> None:
    env = _env(bin_dir, tmp_path, MU_OPENCODE_MODEL="other-provider/other-model")

    result = _run(checkout, env)

    assert result.returncode == 0, result.stderr
    values, _ = _parse(result.stdout)
    assert values["MU_OPENCODE_MODEL"] == "other-provider/other-model"


def test_session_only_creates_the_session_with_its_title_and_prints_the_id(
    checkout: Path, tmp_path: Path
) -> None:
    """JR 2026-11-12: a DeepSeek session belongs to the shared OpenCode window, so the launcher only
    creates it (title from the spawn label) and prints the id instead of opening a TUI."""
    log = tmp_path / "api.log"
    stub = tmp_path / "logging-bin" / "opencode"
    stub.parent.mkdir()
    stub.write_text(f'#!/usr/bin/env bash\nprintf "%s\\n" "$*" >> "{log}"\nexit 0\n', encoding="utf-8")
    stub.chmod(0o755)
    env = _env(stub.parent, tmp_path, MU_OPENCODE_BIN=str(stub))

    result = _run(checkout, env, "--session-only", "--title", "Mu builder-12 (deepseek)")

    assert result.returncode == 0, result.stderr
    session = re.search(r"^session: (ses_mu_\d{10}_\d{5})$", result.stdout, re.M)
    assert session, result.stdout
    assert "--session" not in result.stdout               # no TUI exec
    call = log.read_text(encoding="utf-8")
    assert call.startswith("api post /api/session ")
    assert '"title":"Mu builder-12 (deepseek)"' in call
    assert f'"id":"{session.group(1)}"' in call


def test_run_starts_the_session_headlessly(
    checkout: Path, bin_dir: Path, tmp_path: Path
) -> None:
    """JR 2026-11-12: a spawned DeepSeek session runs inside OpenCode via `opencode run`."""
    result = _run(checkout, _env(bin_dir, tmp_path), "--run", "/builder b1")

    assert result.returncode == 0, result.stderr
    values, args = _parse(result.stdout)
    session = values["MU_OPENCODE_SESSION"]
    assert args == ["run", "--session", session, "--format", "default", "/builder b1"]


def test_session_only_still_prints_the_route_line(
    checkout: Path, bin_dir: Path, tmp_path: Path
) -> None:
    result = _run(checkout, _env(bin_dir, tmp_path), "--session-only")

    assert result.returncode == 0, result.stderr
    assert "Project Mu DeepSeek route:" in result.stderr
    assert re.search(r"^session: ses_mu_\d{10}_\d{5}$", result.stdout, re.M)


def test_mints_a_session_id_and_passes_it_as_session(
    checkout: Path, bin_dir: Path, tmp_path: Path
) -> None:
    result = _run(checkout, _env(bin_dir, tmp_path))

    assert result.returncode == 0, result.stderr
    values, args = _parse(result.stdout)
    session = values["MU_OPENCODE_SESSION"]
    assert SESSION_RE.match(session), session
    assert args[:2] == ["--session", session]


def test_reuses_a_given_session_instead_of_minting_one(
    checkout: Path, bin_dir: Path, tmp_path: Path
) -> None:
    env = _env(bin_dir, tmp_path, MU_OPENCODE_SESSION="ses_mu_1700000000_04242")

    result = _run(checkout, env)

    assert result.returncode == 0, result.stderr
    values, args = _parse(result.stdout)
    assert values["MU_OPENCODE_SESSION"] == "ses_mu_1700000000_04242"
    assert args[:2] == ["--session", "ses_mu_1700000000_04242"]


def test_passes_the_remaining_arguments_through_unchanged(
    checkout: Path, bin_dir: Path, tmp_path: Path
) -> None:
    result = _run(checkout, _env(bin_dir, tmp_path), "--prompt", "/builder", "--extra=1")

    assert result.returncode == 0, result.stderr
    values, args = _parse(result.stdout)
    session = values["MU_OPENCODE_SESSION"]
    assert args == ["--session", session, "--prompt", "/builder", "--extra=1"]


def test_prints_the_route_line(checkout: Path, bin_dir: Path, tmp_path: Path) -> None:
    result = _run(checkout, _env(bin_dir, tmp_path))

    assert result.returncode == 0, result.stderr
    values, _ = _parse(result.stdout)
    session = values["MU_OPENCODE_SESSION"]
    assert (
        f"Project Mu DeepSeek route: {DEFAULT_MODEL} (harness opencode, session {session})"
        in result.stderr
    )


def test_prints_no_secret(checkout: Path, bin_dir: Path, tmp_path: Path) -> None:
    result = _run(checkout, _env(bin_dir, tmp_path), "--prompt", "/builder")

    combined = result.stdout + result.stderr
    assert not re.search(r"sk-[A-Za-z0-9]", combined)
    assert "AUTH_TOKEN" not in combined
    assert "DEEPSEEK_API_KEY" not in combined
    assert "api.deepseek.com" not in combined
