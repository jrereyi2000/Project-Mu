"""WSL wrapper for ``win_agent.py`` (MADDEN_PILOT_AND_TUNING_PLAN §2.1).

``call(["look", "--title", "Madden NFL 27", "--out", "/home/.../shot.png", "--ocr"])`` runs
``win_agent.py`` under the Windows venv interpreter (``MU_WIN_PYTHON``, default
``/mnt/c/Madden27Modding/MuPilot/venv/Scripts/python.exe``), converting the script path and the
path arguments (``--out``, ``--small``, ``--dir``, ``--exe`` values, and any ``os.PathLike``) with
``wslpath -w``. It parses the last JSON line on stdout and returns it. It raises ``WinError`` when
the interpreter is missing, the run times out, stdout holds no JSON, the JSON says ``"ok": false``,
or the exit code is nonzero.

Self-contained (same pattern as ``tools/mod_builder/harness.py``).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path, PurePath
from typing import Any, Sequence

DEFAULT_PYTHON = "/mnt/c/Madden27Modding/MuPilot/venv/Scripts/python.exe"
AGENT = Path(__file__).resolve().with_name("win_agent.py")

# Options whose value is a file path (converted to Windows form).
PATH_OPTIONS = {"--out", "--small", "--dir", "--exe"}


class WinError(Exception):
    """win_agent failed: missing interpreter, timeout, nonzero exit, bad output or ``ok: false``."""

    def __init__(self, message: str, result: dict | None = None, stderr: str = ""):
        super().__init__(message)
        self.result = result
        self.stderr = stderr


def python_path() -> str:
    return os.environ.get("MU_WIN_PYTHON") or DEFAULT_PYTHON


def _looks_windows(s: str) -> bool:
    return len(s) >= 2 and (s[1] == ":" or s.startswith("\\\\"))


def to_windows_path(path: str | os.PathLike) -> str:
    """WSL path -> Windows path via ``wslpath -w``. Works for files that don't exist yet (the
    nearest existing parent is converted). Windows paths pass through unchanged."""
    s = os.fspath(path)
    if _looks_windows(s):
        return s
    p = Path(s).expanduser().absolute()
    if shutil.which("wslpath") is None:
        raise WinError("wslpath not found: path conversion needs WSL")
    target, tail = p, []
    while not target.exists() and target != target.parent:
        tail.append(target.name)
        target = target.parent
    out = subprocess.run(["wslpath", "-w", str(target)], capture_output=True, text=True)
    if out.returncode != 0:
        raise WinError(f"wslpath -w {target} failed: {out.stderr.strip()}")
    win = out.stdout.strip()
    for part in reversed(tail):
        win = win.rstrip("\\") + "\\" + part
    return win


def convert_args(cmd: Sequence[Any]) -> list[str]:
    """Stringify a win_agent command line, converting path option values and PathLike args."""
    args = list(cmd)
    if not args:
        raise WinError("empty win_agent command")
    out: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if isinstance(a, PurePath) or (hasattr(a, "__fspath__") and not isinstance(a, str)):
            out.append(to_windows_path(a))
        elif a in PATH_OPTIONS and i + 1 < len(args):
            out += [a, to_windows_path(args[i + 1])]
            i += 1
        elif a == "--args":  # everything after --args goes to the started process untouched
            out += [str(x) for x in args[i:]]
            break
        else:
            out.append(str(a))
        i += 1
    return out


def _parse_result(stdout: str) -> dict | None:
    """The last stdout line holding a JSON object."""
    for line in reversed([ln for ln in stdout.splitlines() if ln.strip()]):
        start = line.find("{")
        if start < 0:
            continue
        try:
            value = json.loads(line[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None


def call(cmd: list[str], timeout: float = 60) -> dict:
    """Run one win_agent command and return its JSON result (``ok`` is true)."""
    py = python_path()
    if not os.path.isfile(py):
        raise WinError(f"Windows Python not found at {py} (set MU_WIN_PYTHON)")
    argv = [py, to_windows_path(AGENT), *convert_args(cmd)]
    name = str(cmd[0]) if cmd else "?"
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              cwd=os.path.dirname(os.path.abspath(py)))
    except subprocess.TimeoutExpired as e:
        stderr = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        raise WinError(f"win_agent {name} timed out after {timeout}s", stderr=stderr) from e
    except OSError as e:
        raise WinError(f"could not run {py}: {e}") from e

    result = _parse_result(proc.stdout or "")
    if result is None:
        raise WinError(f"win_agent {name} printed no JSON (exit {proc.returncode}): "
                       f"{(proc.stdout or '').strip()[-500:]} {(proc.stderr or '').strip()[-500:]}",
                       stderr=proc.stderr)
    if not result.get("ok"):
        raise WinError(f"win_agent {name} failed: {result.get('error', 'unknown error')}",
                       result=result, stderr=proc.stderr)
    if proc.returncode != 0:
        raise WinError(f"win_agent {name} exited {proc.returncode}", result=result, stderr=proc.stderr)
    return result
