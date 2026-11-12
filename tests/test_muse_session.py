"""Tests for tools/muse_session.sh (MUSE_EMBER_ROTATION_PLAN §2.2, U1).

Offline only: the wrapper is run against a throwaway git checkout and a stub `opencode`
(MU_OPENCODE_BIN), so nothing starts a real OpenCode session and no network is touched.

The wrapper pins the model on the session through `opencode api` instead of passing `--model`,
because the OpenCode TUI has no `--model` in v2.2.7 (spike S-M1): the stub therefore answers the two
`api post` calls and records them, and only a real `--session` invocation reaches the ARG= lines.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = REPO_ROOT / "tools" / "muse_session.sh"

STUB = """#!/usr/bin/env bash
if [ "${1:-}" = "api" ]; then
  if [ "${STUB_API_FAIL:-}" = "1" ]; then exit 1; fi
  if [ -n "${STUB_API_LOG:-}" ]; then printf '%s\\n' "$*" >> "$STUB_API_LOG"; fi
  exit 0
fi
for arg in "$@"; do printf 'ARG=%s\\n' "$arg"; done
printf 'MU_AGENT_CLASS=%s\\n' "${MU_AGENT_CLASS:-}"
printf 'MU_AGENT_HARNESS=%s\\n' "${MU_AGENT_HARNESS:-}"
printf 'MU_AGENT_MODEL=%s\\n' "${MU_AGENT_MODEL:-}"
printf 'MU_MUSE_MODEL=%s\\n' "${MU_MUSE_MODEL:-}"
printf 'MU_OPENCODE_MODEL=%s\\n' "${MU_OPENCODE_MODEL:-}"
printf 'MU_OPENCODE_SESSION=%s\\n' "${MU_OPENCODE_SESSION:-}"
"""

DEFAULT_MODEL = "opencode-go/muse-spark-1.3-contributor"
EMBER_MODEL = "opencode/muse-ember-1.0"
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
        "STUB_API_LOG": str(tmp_path / "api.log"),
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


def _api_calls(tmp_path: Path) -> list[str]:
    """The stub's record of every `opencode api ...` call the wrapper made."""
    log = tmp_path / "api.log"
    if not log.exists():
        return []
    return [line for line in log.read_text(encoding="utf-8").splitlines() if line]


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
    assert values["MU_AGENT_CLASS"] == "muse"
    assert values["MU_AGENT_HARNESS"] == "opencode"
    assert values["MU_AGENT_MODEL"] == "muse-spark-1.3-contributor"
    assert values["MU_MUSE_MODEL"] == DEFAULT_MODEL
    assert values["MU_OPENCODE_MODEL"] == DEFAULT_MODEL


def test_honours_the_ember_model_override(checkout: Path, bin_dir: Path, tmp_path: Path) -> None:
    env = _env(bin_dir, tmp_path, MU_MUSE_MODEL=EMBER_MODEL)

    result = _run(checkout, env)

    assert result.returncode == 0, result.stderr
    values, _ = _parse(result.stdout)
    api = _api_calls(tmp_path)
    assert values["MU_OPENCODE_MODEL"] == EMBER_MODEL
    assert values["MU_AGENT_MODEL"] == "muse-ember-1.0"
    assert any('"providerID":"opencode"' in call and '"id":"muse-ember-1.0"' in call for call in api)


def test_creates_the_session_with_the_model_and_pins_it(
    checkout: Path, bin_dir: Path, tmp_path: Path
) -> None:
    result = _run(checkout, _env(bin_dir, tmp_path))

    assert result.returncode == 0, result.stderr
    values, _ = _parse(result.stdout)
    api = _api_calls(tmp_path)
    session = values["MU_OPENCODE_SESSION"]
    assert len(api) == 2, api
    create, pin = api
    assert create.startswith("api post /api/session ")
    payload = json.loads(create.split("--data ", 1)[1])
    assert payload["id"] == session
    assert payload["model"] == {"id": "muse-spark-1.3-contributor", "providerID": "opencode-go"}
    assert payload["location"]["directory"] == str(checkout)
    assert pin.startswith(f"api post /api/session/{session}/model ")
    assert json.loads(pin.split("--data ", 1)[1]) == {
        "model": {"id": "muse-spark-1.3-contributor", "providerID": "opencode-go"}
    }


def test_never_passes_model_to_the_tui(checkout: Path, bin_dir: Path, tmp_path: Path) -> None:
    """The TUI rejects `--model` (spike S-M1); the session carries the model instead."""
    result = _run(checkout, _env(bin_dir, tmp_path))

    assert result.returncode == 0, result.stderr
    values, args = _parse(result.stdout)
    session = values["MU_OPENCODE_SESSION"]
    assert SESSION_RE.match(session), session
    assert args == ["--session", session]


def test_session_only_creates_pins_and_prints_the_id(
    checkout: Path, bin_dir: Path, tmp_path: Path
) -> None:
    """JR 2026-11-12: a Spark session belongs to the shared OpenCode window, so the launcher only
    creates and pins it, then prints the id for the tab."""
    result = _run(checkout, _env(bin_dir, tmp_path), "--session-only", "--title", "Mu muse-1 (muse)")

    assert result.returncode == 0, result.stderr
    session = re.search(r"^session: (ses_mu_\d{10}_\d{5})$", result.stdout, re.M)
    assert session, result.stdout
    _, args = _parse(result.stdout)
    assert "--session" not in args                     # no TUI exec
    api = _api_calls(tmp_path)
    assert len(api) == 2, api
    assert json.loads(api[0].split("--data ", 1)[1])["title"] == "Mu muse-1 (muse)"
    assert json.loads(api[1].split("--data ", 1)[1])["model"]["id"] == "muse-spark-1.3-contributor"


def test_run_starts_the_session_headlessly(
    checkout: Path, bin_dir: Path, tmp_path: Path
) -> None:
    """JR 2026-11-12: a spawned Spark session also runs inside OpenCode via `opencode run`."""
    result = _run(checkout, _env(bin_dir, tmp_path), "--run", "/builder muse-1")

    assert result.returncode == 0, result.stderr
    values, args = _parse(result.stdout)
    session = values["MU_OPENCODE_SESSION"]
    assert args == ["run", "--session", session, "--format", "default", "/builder muse-1"]
    assert len(_api_calls(tmp_path)) == 2          # created and pinned first


def test_reuses_a_given_session_instead_of_minting_one(
    checkout: Path, bin_dir: Path, tmp_path: Path
) -> None:
    env = _env(bin_dir, tmp_path, MU_OPENCODE_SESSION="ses_mu_1700000000_04242")

    result = _run(checkout, env)

    assert result.returncode == 0, result.stderr
    values, args = _parse(result.stdout)
    api = _api_calls(tmp_path)
    assert values["MU_OPENCODE_SESSION"] == "ses_mu_1700000000_04242"
    assert args == ["--session", "ses_mu_1700000000_04242"]
    # A create on an existing id leaves that session's model alone, so the model is re-pinned.
    assert any(call.startswith("api post /api/session/ses_mu_1700000000_04242/model ") for call in api)


def test_passes_the_remaining_arguments_through_unchanged(
    checkout: Path, bin_dir: Path, tmp_path: Path
) -> None:
    result = _run(checkout, _env(bin_dir, tmp_path), "--prompt", "/builder", "--extra=1")

    assert result.returncode == 0, result.stderr
    values, args = _parse(result.stdout)
    session = values["MU_OPENCODE_SESSION"]
    assert args == ["--session", session, "--prompt", "/builder", "--extra=1"]


def test_refuses_to_start_when_the_model_cannot_be_pinned(
    checkout: Path, bin_dir: Path, tmp_path: Path
) -> None:
    result = _run(checkout, _env(bin_dir, tmp_path, STUB_API_FAIL="1"))

    assert result.returncode == 3
    assert result.stdout == ""
    assert "refusing to start" in result.stderr
    assert DEFAULT_MODEL in result.stderr


def test_prints_the_route_line(checkout: Path, bin_dir: Path, tmp_path: Path) -> None:
    result = _run(checkout, _env(bin_dir, tmp_path))

    assert result.returncode == 0, result.stderr
    values, _ = _parse(result.stdout)
    session = values["MU_OPENCODE_SESSION"]
    assert (
        "Project Mu Muse route: "
        f"{DEFAULT_MODEL} (harness opencode, model muse-spark-1.3-contributor, session {session})"
        in result.stderr
    )


def test_prints_no_secret(checkout: Path, bin_dir: Path, tmp_path: Path) -> None:
    result = _run(checkout, _env(bin_dir, tmp_path), "--prompt", "/builder")

    combined = result.stdout + result.stderr
    assert not re.search(r"sk-[A-Za-z0-9]", combined)
    assert "AUTH_TOKEN" not in combined
    assert "API_KEY" not in combined
