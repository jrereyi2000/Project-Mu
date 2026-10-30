"""Python wrapper for MmcHarness.exe (the console route, MOD_BUILDER_PLAN §2.4).

``run(["export-texture", asset, "/home/.../out.png"])`` converts path arguments to Windows form
with ``wslpath -w``, runs the harness (``/mnt/c/Madden27Modding/MuHarness/MmcHarness.exe``, env
``MU_MMC_HARNESS`` overrides), parses the one JSON object it prints on stdout and returns it.
It raises ``HarnessError`` when the exe is missing, the run times out, the exit code is nonzero,
stdout isn't JSON, or the JSON says ``"ok": false``.

Self-contained: imports nothing else from ``tools.mod_builder``.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path, PurePath
from typing import Any, Sequence

DEFAULT_EXE = "/mnt/c/Madden27Modding/MuHarness/MmcHarness.exe"

# Positional arguments (0 = the command itself) that are file paths, per command.
PATH_ARGS: dict[str, tuple[int, ...]] = {
    "info": (),
    "find": (),
    "export-texture": (2,),
    "import-texture": (1, 3),
    "ebx-dump": (2,),
    "ebx-set": (1,),
    "export-mod": (1, 2),
}
# Options that take a value (so their value isn't counted as a positional argument).
VALUE_OPTIONS = {"--kind", "--limit", "--title", "--author", "--version", "--category", "--description"}
# Options whose value is a path.
PATH_OPTIONS = {"--project"}


class HarnessError(Exception):
    """MmcHarness failed: missing exe, timeout, nonzero exit, bad output or ``ok: false``."""

    def __init__(self, message: str, result: dict | None = None, stderr: str = ""):
        super().__init__(message)
        self.result = result
        self.stderr = stderr


def harness_path() -> str:
    return os.environ.get("MU_MMC_HARNESS") or DEFAULT_EXE


def _looks_windows(s: str) -> bool:
    return len(s) >= 2 and (s[1] == ":" or s.startswith("\\\\"))


def to_windows_path(path: str | os.PathLike) -> str:
    """WSL path -> Windows path via ``wslpath -w``. Works for files that don't exist yet
    (their parent folder is converted). Windows paths are passed through unchanged."""
    s = os.fspath(path)
    if _looks_windows(s):
        return s
    p = Path(s).expanduser().absolute()
    if shutil.which("wslpath") is None:
        raise HarnessError("wslpath not found: path conversion needs WSL")
    target, tail = p, []
    while not target.exists() and target != target.parent:
        tail.append(target.name)
        target = target.parent
    out = subprocess.run(["wslpath", "-w", str(target)], capture_output=True, text=True)
    if out.returncode != 0:
        raise HarnessError(f"wslpath -w {target} failed: {out.stderr.strip()}")
    win = out.stdout.strip()
    for part in reversed(tail):
        win = win.rstrip("\\") + "\\" + part
    return win


def convert_args(cmd: Sequence[Any]) -> list[str]:
    """Convert the path arguments of a harness command line to Windows form.

    Path positions come from ``PATH_ARGS``; any ``os.PathLike`` argument is converted too."""
    args = list(cmd)
    if not args:
        raise HarnessError("empty harness command")
    command = str(args[0])
    path_slots = set(PATH_ARGS.get(command, ()))
    out: list[str] = [command]
    pos = 0
    i = 1
    while i < len(args):
        a = args[i]
        if isinstance(a, PurePath) or (hasattr(a, "__fspath__") and not isinstance(a, str)):
            out.append(to_windows_path(a))
            pos += 1
        elif isinstance(a, str) and (a in VALUE_OPTIONS or a in PATH_OPTIONS):
            out.append(a)
            if i + 1 < len(args):
                value = args[i + 1]
                out.append(to_windows_path(value) if a in PATH_OPTIONS else str(value))
            i += 1
        elif isinstance(a, str) and a.startswith("--"):
            out.append(a)
        else:
            pos += 1
            out.append(to_windows_path(a) if pos in path_slots else str(a))
        i += 1
    return out


def _parse_result(stdout: str) -> dict | None:
    """The harness prints one JSON object; take the last line that holds one (tolerating any
    stray text a native library may have put in front of it)."""
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


def run(cmd: list[str], timeout: float = 600) -> dict:
    """Run one harness command and return its JSON result (``ok`` is true)."""
    exe = harness_path()
    if not os.path.isfile(exe):
        raise HarnessError(f"MmcHarness not found at {exe} (build it with tools/mmc_harness/build.sh, "
                           f"or set MU_MMC_HARNESS)")
    argv = [exe, *convert_args(cmd)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout,
                              cwd=os.path.dirname(os.path.abspath(exe)))
    except subprocess.TimeoutExpired as e:
        stderr = e.stderr.decode(errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        raise HarnessError(f"MmcHarness {cmd[0]} timed out after {timeout}s", stderr=stderr) from e
    except OSError as e:
        raise HarnessError(f"could not run {exe}: {e}") from e

    result = _parse_result(proc.stdout or "")
    if not isinstance(result, dict):
        raise HarnessError(f"MmcHarness {cmd[0]} printed no JSON (exit {proc.returncode}): "
                           f"{(proc.stdout or '').strip()[-500:]}", stderr=proc.stderr)
    if not result.get("ok"):
        raise HarnessError(f"MmcHarness {cmd[0]} failed: {result.get('error', 'unknown error')}",
                           result=result, stderr=proc.stderr)
    if proc.returncode != 0:
        raise HarnessError(f"MmcHarness {cmd[0]} exited {proc.returncode}", result=result, stderr=proc.stderr)
    return result
