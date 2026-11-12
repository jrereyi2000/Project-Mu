"""
pipeline/gpu_queue/process_check.py
Process checking for Madden NFL 27 and GPU resource contention.

Checks if Madden 27 is running via tasklist.exe (WSL2 / Windows) or pgrep (Mac / Linux).
Provides mockable hooks for unit tests and local simulation.

Reference:
- docs/done/PC_MIGRATION_PLAN.md §7.5
- docs/not_started/PRE_PC_WORK_2.md Item A7
"""
from contextlib import contextmanager
import os
import shutil
import subprocess
from typing import Callable, Generator, List, Mapping, Optional, Sequence, Union

DEFAULT_MADDEN_PROCESS_NAMES = ["Madden27.exe", "Madden27", "madden27.exe"]

# Global mock hook for testing
_process_check_mock: Optional[Callable[[], bool]] = None


def set_process_check_mock(mock_fn: Optional[Callable[[], bool]]) -> None:
    """Set or clear a global process checking mock."""
    global _process_check_mock
    _process_check_mock = mock_fn


@contextmanager
def mock_process_running(
    is_running: Union[bool, Callable[[], bool]]
) -> Generator[None, None, None]:
    """Context manager for mocking process detection."""
    global _process_check_mock
    old_mock = _process_check_mock
    try:
        if callable(is_running):
            _process_check_mock = is_running
        else:
            _process_check_mock = lambda: bool(is_running)
        yield
    finally:
        _process_check_mock = old_mock


def _find_tasklist_exe() -> Optional[str]:
    """Locate tasklist.exe on Windows or WSL2."""
    which_path = shutil.which("tasklist.exe")
    if which_path:
        return which_path
    wsl_path = "/mnt/c/Windows/System32/tasklist.exe"
    if os.path.exists(wsl_path):
        return wsl_path
    return None


def is_process_running(process_names: Union[str, Sequence[str]]) -> bool:
    """
    Check if any process matching the given names is running on the host system.
    Returns True if any match is found, False otherwise.
    """
    global _process_check_mock
    if _process_check_mock is not None:
        return bool(_process_check_mock())

    if isinstance(process_names, str):
        target_names = [process_names]
    else:
        target_names = list(process_names)

    target_lowers = [name.strip().lower() for name in target_names if name.strip()]
    if not target_lowers:
        return False

    tasklist_bin = _find_tasklist_exe()
    if tasklist_bin:
        try:
            # /FO CSV formats output as comma-separated values, /NH omits column headers
            res = subprocess.run(
                [tasklist_bin, "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            if res.returncode == 0:
                output_lower = res.stdout.lower()
                for target in target_lowers:
                    if target in output_lower:
                        return True
                return False
        except Exception:
            # Fall through to unix tools if tasklist failed unexpectedly
            pass

    # Linux / macOS pgrep check
    pgrep_bin = shutil.which("pgrep")
    if pgrep_bin:
        for target in target_lowers:
            try:
                # -x matches the exact process name (not the full command line,
                # which would also match any shell whose arguments mention Madden);
                # -i is case insensitive
                res = subprocess.run(
                    [pgrep_bin, "-i", "-x", target],
                    capture_output=True,
                    timeout=5.0,
                    check=False,
                )
                if res.returncode == 0:
                    return True
            except Exception:
                continue
        return False

    # Generic ps fallback
    ps_bin = shutil.which("ps")
    if ps_bin:
        try:
            res = subprocess.run(
                [ps_bin, "-eo", "comm="],
                capture_output=True,
                text=True,
                timeout=5.0,
                check=False,
            )
            if res.returncode == 0:
                # Compare process names exactly, not full command lines
                running_names = {line.strip().lower() for line in res.stdout.splitlines()}
                if any(target in running_names for target in target_lowers):
                    return True
        except Exception:
            pass

    return False


def is_madden_running(process_names: Optional[Sequence[str]] = None) -> bool:
    """
    Check whether Madden NFL 27 is currently active on the system.
    """
    if process_names is not None:
        names = list(process_names)
    else:
        env_names = os.environ.get("MADDEN_PROCESS_NAME")
        if env_names:
            names = [n.strip() for n in env_names.split(",") if n.strip()]
        else:
            names = list(DEFAULT_MADDEN_PROCESS_NAMES)
    return is_process_running(names)


# A process's start time is what makes its PID an *identity*: Windows reuses PIDs, so a saved
# PID alone cannot prove the same game instance is still the one a launch verified (F6).
_IDENTITY_SCRIPT = (
    "$names = {names}; "
    "$p = Get-Process -Name $names -ErrorAction SilentlyContinue | "
    "Sort-Object StartTime | Select-Object -First 1; "
    "if ($p) {{ "
    "$u = $p.StartTime.ToUniversalTime(); "
    "$e = [int64]($u.Subtract([datetime]::new(1970,1,1,0,0,0,[DateTimeKind]::Utc)).TotalSeconds); "
    "Write-Output ('{{0}}|{{1}}|{{2}}' -f $p.Id, $e, $p.ProcessName) }}"
)


def madden_process_identity(process_names: Optional[Sequence[str]] = None,
                            run: Optional[Callable] = None) -> Optional[dict]:
    """The running Madden's {pid, started (UTC epoch seconds), name}, or None when unknown.

    The mod check's trusted attach (F6) binds a launch receipt to this identity: a PID alone is
    reused by Windows, so the start time travels with it. Uses PowerShell's Get-Process (the
    StartTime is readable for a normal user process). Any failure — no PowerShell, no process,
    an unreadable start time — returns None, which callers must treat as "provenance unknown"
    and refuse to attach. `run` is injectable for tests and never touches Windows there.
    """
    if process_names is None:
        env_names = os.environ.get("MADDEN_PROCESS_NAME")
        names = ([n.strip() for n in env_names.split(",") if n.strip()] if env_names
                 else list(DEFAULT_MADDEN_PROCESS_NAMES))
    elif isinstance(process_names, str):
        names = [process_names]
    else:
        names = list(process_names)
    stems = []
    for name in names:
        stem = name.strip()
        if stem.lower().endswith(".exe"):
            stem = stem[:-4]
        if stem:
            stems.append(stem)
    if not stems:
        return None
    quoted = ",".join("'" + stem.replace("'", "''") + "'" for stem in stems)
    script = _IDENTITY_SCRIPT.format(names=f"@({quoted})")
    runner = run or subprocess.run
    try:
        result = runner(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                        capture_output=True, text=True, timeout=15.0, check=False)
    except Exception:                     # no PowerShell, timeout, anything: unknown
        return None
    if getattr(result, "returncode", 1) != 0:
        return None
    for line in (getattr(result, "stdout", "") or "").splitlines():
        parts = [part.strip() for part in line.strip().split("|")]
        if len(parts) != 3:
            continue
        try:
            pid, started = int(parts[0]), float(parts[1])
        except ValueError:
            continue
        if pid > 0:
            return {"pid": pid, "started": started, "name": parts[2]}
    return None


def same_process_identity(saved: Optional[Mapping], current: Optional[Mapping],
                          tolerance: float = 2.0) -> bool:
    """True when two identities name the same process instance (PID and start time agree)."""
    if not saved or not current:
        return False
    try:
        same_pid = int(saved["pid"]) == int(current["pid"])
        drift = abs(float(saved["started"]) - float(current["started"]))
    except (KeyError, TypeError, ValueError):
        return False
    return same_pid and drift <= float(tolerance)
