"""The visual mod check run and its reference capture (VISUAL_MOD_CHECK_PLAN 2.1, 2.3, R1-R3).

``run(save, mod, ...)``
    1. refuses anything but ``CAREER-TUNE-*`` (the same rule as ``sim-season``) and refuses while
       ``pipeline.watch_save`` runs, before touching anything;
    2. reads ``data/mod_checks/<mod>/segments.json`` and ``checks.json`` (``segments.load_plan``);
    3. clones the save it was given into a fresh ``CAREER-TUNE-CHK<letter>`` and scores *that* clone.
       There is no ``--no-clone``: plan R1/D1 confirmed that quitting a game writes
       ``-AUTOSAVE``, so a check must never score the save JR is using. The clone callable is
       injectable; the default is ``default_clone``, the same file copy ``jobs.clone_save``
       makes (without the capacity prune, which a short visual check does not need);
    4. drives the injected ``driver`` (``start`` / ``wait_for`` / ``record`` / ``finish``) and
       scores every recorded frame with ``tools.madden_pilot.assets.check_frame``. A segment's
       asset passes on its **latest decisive** frame (pass or fail, not unknown) and the report
       names that frame;
    5. asks the describe callable once per segment, about the contact sheet
       (``spike.build_clip``) or the middle frame. The model only describes: it never decides a
       verdict and never presses a key. A missing model gives ``summary: "model unavailable"``
       and the run still finishes;
    6. writes ``report.json`` and ``report.md`` under ``data/mod_checks/runs/<stamp>_<slug>/``
       (or ``run_dir``), sends one ntfy line and returns the report dict with ``status`` set to
       the verdict.

``dry_run=True`` validates and returns the plan with no clone, driver, frame, write or notice.

``capture(...)``
    The human step that makes a mod's reference pair: it takes one segment's frames (or records
    them through a driver), cuts the two named moments, measures them both ways with
    ``assets.crop_similarity``, shows the numbers and the crop paths, and writes ``refs/`` only
    with an explicit ``yes``. A pair whose pass/fail similarity is inside the spec's margin is
    refused: that pair would decide nothing, and a wrong pair is the one way a check can lie
    (plan R3).

``PilotDriver`` is the real driver. It starts ``python3 -m tools.madden_pilot watch-game`` as a
*background* process for each cued segment, so the job holds the game while this process records
(``watch-game`` only leaves when its hold ends). The job writes a per-invocation ``ready.json``
immediately before its hold: the cue, route, screen, clock, hold deadline, its pid and the
driver's own ready token. The driver accepts that marker only from the process it started, only
while that process is alive, and only when the remaining hold covers the recording. It then
records through ``win_agent record`` and collects the job's ``result.json`` as its ``finish``.
A halftime run uses the job's ``--to halftime`` route; the static super-sim segment uses
``--to supersim`` and is accepted only when the ready screen is ``SUPERSIM_MENU``.

A run launches Madden itself through the pilot's approved ``launch`` job with the plan's own
pack, and refuses when Madden is already running: an attached game's loaded pack cannot be
verified (plan R6). The report keeps the pixel ``verdict`` separate from a ``operational``
status, so a passing picture with a failed quit is not reported as a clean run.
"""
from __future__ import annotations

import datetime
import json
import math
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from tools.madden_pilot import assets, jobs
from tools.mod_check import report as report_mod
from tools.mod_check.segments import (
    CUES, PlanError, Segment, data_root, load_plan, mod_directory,
)

# cue -> (watch-game stop, the ready marker's anchor, the screen it must report or None).
# `watch-game` writes `ready.json` immediately before its hold; a historical `steps/*.json`
# classification is never readiness.
# Every cue now has a route: the job positions the screen and signals its own hold.
CUE_ROUTES = {
    "kickoff": ("kickoff", "kickoff", None),
    "halftime_start": ("halftime", "halftime_start", None),
    "supersim_menu": ("supersim", "supersim_menu", "SUPERSIM_MENU"),
}
READY_FILE = "ready.json"            # watch-game's current-process ready-for-recording signal
READY_MARGIN_S = 5.0                 # IPC/record margin the remaining hold must still cover
JOB_QUIT_GRACE_S = 180.0            # how long after its hold a job may take to quit
CUE_POLL_S = 0.5                    # how often the job's ready marker is read while it holds
LAUNCH_RECEIPT = "launch.json"      # the mod folder's last verified launch, reused by --reuse-launch
CHK_PREFIX = "CAREER-TUNE-CHK"
MAX_CHK = 99
LAUNCH_TIMEOUT_S = 900.0


def clone_suffix(number: int) -> str:
    """1 -> A, 26 -> Z, 27 -> AA.

    Clone names carry letters, never digits: the pilot finds a franchise tile by reading its
    name, and Windows OCR reads the digit 1 as I, so a ``CAREER-TUNE-CHK1`` tile was unfindable
    on the PC (spike 20261112-1410, plan §2.5a).
    """
    if number < 1:
        raise ValueError(f"a clone number starts at 1, not {number}")
    letters = ""
    while number:
        number, rest = divmod(number - 1, 26)
        letters = chr(ord("A") + rest) + letters
    return letters
FRAME_SUFFIXES = (".png", ".jpg", ".jpeg")
# The kickoff is a short pregame; the halftime route may need the bounded 320 JUMP TO NEXT
# PLAY presses plus two possessions, so its cue timeout is much longer than kickoff's.
CUE_TIMEOUTS = {"kickoff": 180.0, "halftime_start": 1800.0, "supersim_menu": 300.0}
CUE_TIMEOUT_S = 180.0
DEFAULT_MARGIN = 0.05
_TUNE_SAVE = re.compile(r"^CAREER-TUNE-[A-Za-z0-9][A-Za-z0-9_\-]*$", re.IGNORECASE)


class DriverError(RuntimeError):
    """The driver could not do what the run asked (a missing job, a refused launch, ...)."""


class CueMissed(DriverError):
    """The driver never saw the cue a segment waits for."""

    def __init__(self, cue: str, detail: str = ""):
        self.cue = cue
        super().__init__(f"cue {cue!r} was not seen" + (f": {detail}" if detail else ""))


class LaunchError(DriverError):
    """The launch workflow could not establish which test pack Madden is running."""


# --------------------------------------------------------------------------- helpers
def valid_tune_save(save: Any) -> bool:
    """``CAREER-TUNE-*`` and a plain file name: the same rule ``jobs.sim_season`` applies."""
    return isinstance(save, str) and _TUNE_SAVE.fullmatch(save) is not None


def _refused(why: str, **extra) -> dict:
    return {"status": "refused", "why": why, **extra}


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def _inside(path: Path, root: Path, what: str) -> Path:
    """Resolve ``path`` and refuse it unless it lives under ``root`` (plan S9: git-ignored data)."""
    target = path.expanduser().resolve()
    try:
        target.relative_to(root.expanduser().resolve())
    except ValueError:
        raise PlanError(f"{what} must be inside {root} (the git-ignored mod-check folder), "
                        f"not {target}") from None
    return target


def _rel(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except (OSError, ValueError):
        return str(path)


def default_clone(save: str, prefix: str = CHK_PREFIX, folder: str | Path | None = None) -> str:
    """Copy ``save`` to the first free ``<prefix><letter>`` (A, B, ...) and return that save's name.

    This is ``jobs.clone_save``'s file copy without its capacity prune: a visual check records a
    few seconds of a game, it never advances a season, so the rows the prune frees are not
    needed. It writes only ``CAREER-TUNE-*`` names and never touches a save that is not one.
    """
    saves = Path(folder) if folder is not None else jobs.saves_dir()
    if not valid_tune_save(save):
        raise ValueError(f"only CAREER-TUNE-* saves are cloned, not {save!r}")
    if not prefix.upper().startswith(jobs.TUNE_SAVE_PREFIX):
        raise ValueError(f"the clone prefix must be {jobs.TUNE_SAVE_PREFIX}*, not {prefix!r}")
    source = saves / save
    if not source.is_file():
        raise ValueError(f"no save {save} in {saves}")
    existing = {p.name.upper() for p in saves.iterdir()} if saves.is_dir() else set()
    name = None
    for number in range(1, MAX_CHK + 1):
        candidate = f"{prefix}{clone_suffix(number)}"
        if candidate.upper() not in existing:
            name = candidate
            break
    if name is None:
        raise ValueError(f"no free {prefix}<letter> name in {saves} (tried A..{clone_suffix(MAX_CHK)})")
    destination = saves / name
    temp = saves / f".mu-mod-check-clone-{os.getpid()}.tmp"
    try:
        shutil.copyfile(source, temp)
        os.replace(temp, destination)
    finally:
        if temp.exists():
            temp.unlink()
    return name


def _frame_paths(returned: Iterable[str | Path] | None, folder: Path,
                 since: float | None = None) -> list[Path]:
    """The frames this recording wrote, sorted.

    ``returned`` is the driver's own list. ``None`` means the driver reported no list, so the
    folder is read instead — but only files at or after ``since`` (the moment recording began),
    never an older run's leftovers. An empty list is an explicit "nothing was written" and is
    not replaced by folder contents (F3: stale evidence must never satisfy a segment).
    """
    paths: list[Path] = []
    for item in returned or []:
        path = Path(item)
        if not path.is_absolute() and not path.exists():
            path = folder / path
        if path.is_file():
            paths.append(path)
    if returned is None and folder.is_dir():
        for path in folder.iterdir():
            if not path.is_file() or path.suffix.lower() not in FRAME_SUFFIXES:
                continue
            if since is not None and (_mtime(path) or 0) < since:
                continue
            paths.append(path)
    return sorted(paths)


def _score_asset(check: Callable[[Path, dict], dict], spec: Mapping[str, Any],
                 frames: Sequence[Path], base: Path) -> dict:
    """One asset over a segment's frames: the latest decisive frame wins (plan section 2.1)."""
    if not frames:
        return report_mod.check_result("unknown", {}, None,
                                       "no frames were recorded for this segment")
    decisive: tuple[Path, dict] | None = None
    last: tuple[Path, dict] = (frames[-1], {})
    for path in frames:
        try:
            result = check(path, dict(spec)) or {}
        except Exception as exc:                       # a bad frame is an unknown, not a crash
            result = {"status": "unknown", "measures": {}, "why": f"check failed: {exc}"}
        last = (path, result)
        if result.get("status") in ("pass", "fail"):
            decisive = (path, result)
    if decisive is not None:
        frame, result = decisive
        status = str(result.get("status"))
    else:
        frame, result = last
        status = "unknown"
    return report_mod.check_result(status, result.get("measures"),
                                   _rel(frame, base), str(result.get("why") or ""))


def _summary_text(answer: Any) -> str | None:
    """The describe callable's answer as text: a str, a ScreenDescription, or a dict."""
    if answer is None:
        return None
    if isinstance(answer, str):
        return answer.strip() or None
    summary = getattr(answer, "summary", None)
    if summary is None and isinstance(answer, dict):
        summary = answer.get("summary")
    text = str(summary or "").strip()
    return text or None


def describe_segment(png: str | Path) -> str:
    """Default describer: ``tools.madden_pilot.vision.describe``, keeping its summary text."""
    from tools.madden_pilot import vision
    return str(vision.describe(png).summary or "").strip()


def _middle_frame(frames: Sequence[Path]) -> Path:
    return frames[len(frames) // 2]


def _summarise(describe: Callable[[Path], Any], segment: Segment, frames: Sequence[Path],
               segment_dir: Path) -> str:
    """One description per segment; a model problem never fails the run (plan R5)."""
    if not frames:
        return "no frames to describe"
    source = _middle_frame(frames)
    try:                                        # the sheet is nicer; a plain middle frame is enough
        from tools.madden_pilot.spike import build_clip
    except Exception:                           # pragma: no cover - spike is in-tree
        build_clip = None
    if build_clip is not None:
        try:
            info = build_clip(segment_dir / "frames", segment_dir / "clip.gif",
                              segment_dir / "sheet.png", segment.fps)
            source = Path(info["sheet"])
        except Exception:                       # a bad clip is no reason to lose the run
            source = _middle_frame(frames)
    try:
        text = _summary_text(describe(source))
    except Exception:                           # includes VisionUnavailable: the table stands alone
        return report_mod.MODEL_UNAVAILABLE
    return text or report_mod.MODEL_UNAVAILABLE


def _notify(message: str) -> bool:
    """One ntfy line through ``pipeline.notify``, in the pilot's style."""
    try:
        from pipeline import notify as notify_mod
        return bool(notify_mod.notify(message))
    except Exception:                           # a notice failure must not lose a finished report
        return False


def _check_hold(pilot: Any, seconds: int) -> None:
    """Ask the real driver whether its job is still alive and holding (F3/R1).

    Fake drivers used by the offline tests need no seam; the real PilotDriver re-checks the
    live process, its own accepted ready marker and the remaining hold.
    """
    check = getattr(pilot, "check_hold", None)
    if callable(check):
        check(seconds)


def _finish(driver: Any) -> tuple[dict, str | None]:
    """Best-effort finish that never hides its own failure: ``(state, error)``."""
    try:
        finished = driver.finish()
    except Exception as exc:                       # cleanup is best-effort; the error is data
        return {}, f"finish failed: {exc}"
    if finished is None:
        return {}, "finish returned nothing"
    if not isinstance(finished, dict):
        return {}, f"finish returned {type(finished).__name__}, not a state"
    return dict(finished), None


def _operational(finished: Mapping[str, Any], finish_error: str | None) -> dict:
    """Separate "the pixels were scored" from "the game was left and the PC is free".

    A missing or non-zero job exit, a stopped job, a missing ``result.json``, a finish timeout
    and an unconfirmed quit all mean the run is incomplete even when its pixel verdict passed.
    """
    problems: list[str] = []
    if finish_error:
        problems.append(finish_error)
    code = finished.get("exit_code")
    if code is not None and int(code) != 0:
        problems.append(f"watch-game exited {code}")
    for extra in finished.get("exit_codes") or []:
        if extra is not None and int(extra) != 0 and extra != code:
            problems.append(f"a watch-game invocation exited {extra}")
    job_status = finished.get("job_status", finished.get("status"))
    if job_status not in (None, "ok"):
        why = finished.get("why") or finished.get("reason") or ""
        problems.append(f"watch-game reported {job_status}" + (f": {why}" if why else ""))
    stopped = finished.get("stopped")
    if stopped:
        problems.append(f"watch-game was stopped ({', '.join(map(str, stopped))})")
    if finished.get("quit") is not True:
        why = finished.get("why") or finished.get("reason")
        problems.append("the game was not confirmed quit"
                        + (f": {why}" if why else " (finish did not report a quit)"))
    if problems:
        why = "; ".join(problems)
        return {"status": "cleanup_failed", "why": why,
                "recovery": ("the game may still be open: leave it by hand before any other PC "
                             "action, then check the frames in the run report"),
                "job_status": job_status, "exit_code": code}
    return {"status": "complete", "why": "", "recovery": None,
            "job_status": job_status, "exit_code": code}


def _autosave_flag(finished: Mapping[str, Any]) -> bool | None:
    value = finished.get("autosave_written", finished.get("autosave"))
    return None if value is None else bool(value)


def _unique_run_dir(root: Path, mod: str) -> Path:
    """A fresh run directory even when two runs start in the same second."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    base = root / "runs" / f"{stamp}_{mod}"
    out, number = base, 1
    while out.exists():
        number += 1
        out = base.with_name(f"{base.name}-{number}")
    return out


def _unique_capture_dir(root: Path, mod: str) -> Path:
    """A fresh capture directory even when two captures start in the same second."""
    base = root / "captures" / f"{datetime.datetime.now():%Y-%m-%d_%H%M%S}_{mod}"
    out, number = base, 1
    while out.exists():
        number += 1
        out = base.with_name(f"{base.name}-{number}")
    return out


def _occupant(folder: Path) -> str | None:
    """Why an explicit run directory cannot be reused, or None when it is empty/absent."""
    if not folder.exists():
        return None
    if not folder.is_dir():
        return "it is a file, not a directory"
    try:
        entries = list(folder.iterdir())
    except OSError as exc:
        return f"it cannot be read: {exc}"
    if entries:
        return f"it already holds {len(entries)} item(s), including {entries[0].name!r}"
    return None


def default_launch(pack: str, run_dir: str | Path, python: str | None = None,
                   cwd: str | Path | None = None) -> dict:
    """Launch Madden through the pilot's own ``launch`` job with ``pack`` and return its receipt.

    This is the only trustworthy pack provenance available: the run starts the plan's test pack
    itself, so the pack it asked Frosty for is the pack in the report. An already-running Madden
    is refused — its loaded pack cannot be verified — and so is a launch that reports a different
    pack or a non-ok status. A caller can inject its own launch callable in tests. The receipt
    also carries the launch job's process identity and applied-pack fingerprint (F6), which
    ``attach_launch`` re-verifies before a later fixture may reuse this same process.
    """
    try:
        from pipeline.gpu_queue import process_check
        already = bool(process_check.is_madden_running())
    except Exception as exc:
        raise LaunchError(f"cannot tell whether Madden is already running: {exc}") from exc
    if already:
        raise LaunchError("Madden 27 is already running: close it so the check can launch the "
                          "plan's test pack itself (an attached game's loaded pack cannot be "
                          "verified, plan R6)")
    command = [python or os.environ.get("MU_PILOT_PYTHON") or "python3", "-m",
               "tools.madden_pilot", "launch", "--pack", str(pack), "--run-dir", str(run_dir)]
    try:
        proc = subprocess.run(command, cwd=str(cwd or Path(__file__).resolve().parents[2]),
                              capture_output=True, text=True, timeout=LAUNCH_TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LaunchError(f"the launch job could not run: {exc}") from exc
    receipt = _last_json(proc.stdout)
    if proc.returncode != 0:
        why = (receipt.get("why") or receipt.get("reason") or receipt.get("refused")
               or proc.stderr.strip() or f"exit {proc.returncode}")
        raise LaunchError(f"launch failed: {why}")
    if receipt.get("status") != "ok":
        raise LaunchError(f"launch did not report ok: {receipt or 'no JSON result'}")
    if receipt.get("pack") != pack:
        raise LaunchError(f"launch reported pack {receipt.get('pack')!r}, not the plan's {pack!r}")
    if receipt.get("already_running") is not False:
        raise LaunchError("launch attached to an already-running Madden; its loaded pack is "
                          "unknown (plan R6)")
    return receipt


def _receipt_path(folder: str | Path) -> Path:
    return Path(folder) / LAUNCH_RECEIPT


def read_launch_receipt(folder: str | Path) -> dict | None:
    """The last launch receipt a run stored in the mod folder, or None."""
    try:
        data = json.loads(_receipt_path(folder).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_launch_receipt(folder: str | Path, receipt: Mapping[str, Any]) -> Path:
    """Store the cold launch's receipt so a later fixture can attach to the same process (F6)."""
    path = _receipt_path(folder)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(receipt), indent=1, default=str), encoding="utf-8")
    return path


def attach_launch(receipt: Mapping[str, Any] | None, pack: str,
                  identity: Callable[[], Mapping[str, Any] | None] | None = None,
                  fingerprint: Callable[[str], Mapping[str, Any] | None] | None = None) -> dict:
    """Verify that the running Madden is the *same verified instance* a cold launch started.

    The trusted attach of F6, for consecutive fixtures that must not relaunch or change pack:
    the stored receipt must name the plan's pack, report a cold launch, carry a verified Windows
    process identity (pid + start time) and a verified applied-pack fingerprint; the *current*
    Madden must still have that identity (Windows reuses PIDs, so the start time is the check),
    and the applied pack's fingerprint must be unchanged. Unknown, stale, wrong-pack and changed
    applied state all raise LaunchError before anything is cloned, recorded or scored. A caller
    string or a selected-pack name alone is never enough.
    """
    if not isinstance(receipt, Mapping) or receipt.get("status") != "ok":
        raise LaunchError("no successful launch receipt to reuse: run once without --reuse-launch "
                          "on this mod so the launch's provenance is recorded (plan R6/F6)")
    if receipt.get("already_running") is not False:
        raise LaunchError("the receipt says its launch attached to an already-running Madden; "
                          "that process's pack is unknown, so it cannot be reused (plan R6/F6)")
    if receipt.get("pack") != pack:
        raise LaunchError(f"the stored launch ran pack {receipt.get('pack')!r}, not the plan's "
                          f"{pack!r}; refusing to attach to a different pack")
    saved = receipt.get("process")
    applied = receipt.get("applied")
    if not receipt.get("process_verified") or not isinstance(saved, Mapping):
        raise LaunchError("the stored launch has no verified Windows process identity (pid and "
                          "start time); an attached game's instance cannot be proved (F6)")
    if not receipt.get("applied_verified") or not isinstance(applied, Mapping):
        raise LaunchError("the stored launch has no verified applied-pack fingerprint; the pack "
                          "state of an attached game cannot be proved (F6)")
    from pipeline.gpu_queue import process_check
    current = (identity or process_check.madden_process_identity)()
    if current is None:
        raise LaunchError("cannot read the running Madden process; its identity is unknown, so "
                          "the verified launch cannot be matched (F6)")
    if not process_check.same_process_identity(saved, current):
        raise LaunchError(f"the running Madden is not the launched instance (saved pid/start "
                          f"{dict(saved)!r}, current {dict(current)!r}); relaunch instead of "
                          f"attaching (F6)")
    from pipeline import mod_packs
    now = (fingerprint or mod_packs.pack_fingerprint)(pack)
    if not isinstance(now, Mapping):
        raise LaunchError(f"cannot read the applied pack {pack!r} from the Mod Manager config; "
                          f"its state is unknown, so no attach (F6)")
    if now.get("fingerprint") != applied.get("fingerprint"):
        raise LaunchError(f"the applied pack {pack!r} changed since the launch (fingerprint "
                          f"{applied.get('fingerprint')!r} -> {now.get('fingerprint')!r}); relaunch "
                          f"instead of attaching (F6)")
    return dict(receipt)


# --------------------------------------------------------------------------- run
def run(save: str, mod: str, segments: Sequence[str] | None = None, run_dir: str | Path | None = None,
        dry_run: bool = False, driver: Any = None, describe: Callable | None = None,
        data_dir: str | Path | None = None, watcher: Callable | None = None,
        clone: Callable | None = None, progress: Callable | None = None, *,
        pack: str | None = None, check: Callable | None = None,
        notice: Callable | None = None, sleep: Callable | None = None,
        launch: Callable | None = None, reuse: bool = False,
        identity: Callable[[], Mapping[str, Any] | None] | None = None,
        fingerprint: Callable[[str], Mapping[str, Any] | None] | None = None) -> dict:
    """Run one visual mod check and return the report.

    ``status`` is the pixel verdict only when the run also finished cleanly; a cleanup problem
    returns ``status: "incomplete"`` with the pixel verdict kept in ``verdict`` and the reason in
    ``operational``. Extra keyword injections beyond the plan's signature, all optional:
    ``pack`` (a cross-check of the plan's own pack; it is never treated as proof), ``launch``
    (default ``default_launch``: the pilot's own launch job), ``check`` (default
    ``assets.check_frame``), ``notice`` (a callable receiving the ntfy line; default sends it),
    ``sleep`` (default ``time.sleep``; the preroll's wait).
    """
    started_at = time.monotonic()
    if not valid_tune_save(save):
        return _refused(f"only {jobs.TUNE_SAVE_PREFIX}* saves are checked, not {save!r} "
                        f"(clone one with `python3 -m tools.madden_pilot clone-save`)",
                        save=save, mod=mod)
    if (watcher or jobs.watcher_running)():
        return _refused("pipeline.watch_save is running: stop it first (a clone keeps the real "
                        "franchise's LeagueID, so the watcher would ingest it)", save=save, mod=mod)
    root = data_root(data_dir)
    folder = mod_directory(mod, data_dir)
    plan = load_plan(folder)
    chosen = plan.select(segments)
    if not plan.pack:
        return _refused("segments.json must name the plan's expected test pack so the launch can "
                        "be verified (add \"pack\": \"Mu Test\" at the top level; plan R6)",
                        save=save, mod=plan.mod, pack=None)
    if pack is not None and pack != plan.pack:
        return _refused(f"--pack {pack!r} disagrees with the plan's expected pack "
                        f"{plan.pack!r}; the launch is bound to the plan (plan R6)",
                        save=save, mod=plan.mod, pack=plan.pack)

    if dry_run:
        return {"status": "dry_run", "dry_run": True, "save": save, "mod": plan.mod,
                "mod_dir": str(folder),
                "segments": [segment.as_dict() for segment in chosen],
                "checks": plan.checks, "verdict": None, "minutes": 0.0,
                "pack": plan.pack, "pack_required": plan.pack, "autosave_written": None,
                "would_clone": f"{CHK_PREFIX}<first free n>", "clone_from": save}

    if run_dir:
        target = _inside(Path(run_dir), root, "the run directory")
        occupied = _occupant(target)
        if occupied:
            return _refused(f"the run directory {target} is not fresh ({occupied}); every run "
                            f"needs its own directory so old frames cannot satisfy a segment",
                            save=save, mod=plan.mod)
    else:
        target = _inside(_unique_run_dir(root, plan.mod), root, "the run directory")

    attached_receipt: dict | None = None
    if reuse:
        # A refused attach must not clone a save or touch the PC at all (F6).
        try:
            attached_receipt = attach_launch(read_launch_receipt(folder), plan.pack,
                                            identity=identity, fingerprint=fingerprint)
        except (LaunchError, DriverError, OSError) as exc:
            return {"status": "stopped", "stop": "attach", "why": str(exc), "save": save,
                    "source_save": save, "mod": plan.mod, "run_dir": None,
                    "pack_required": plan.pack, "pack_actual": None, "pack_verified": False,
                    "attached": False}

    maker = clone or default_clone
    try:
        scored = str(maker(save, CHK_PREFIX) or "").strip()
    except (OSError, ValueError, PlanError) as exc:
        return _refused(f"could not clone {save}: {exc}", save=save, mod=plan.mod)
    if not valid_tune_save(scored) or scored.upper() == save.upper():
        return _refused(f"the clone callable returned {scored!r}; a run scores a fresh "
                        f"{CHK_PREFIX}<letter> save, never the one it was given", save=save, mod=plan.mod)

    target.mkdir(parents=True, exist_ok=True)
    if reuse:
        # Consecutive fixtures on one verified launch (F6): no relaunch and no new process.
        receipt = attached_receipt
    else:
        launcher = launch or (lambda wanted: default_launch(wanted, target / "launch"))
        try:
            receipt = launcher(plan.pack) or {}
        except (DriverError, OSError) as exc:
            return {"status": "stopped", "stop": "launch", "why": str(exc), "save": scored,
                    "source_save": save, "mod": plan.mod, "run_dir": str(target),
                    "pack_required": plan.pack, "pack_actual": None, "pack_verified": False,
                    "attached": False}
        if (not isinstance(receipt, dict) or receipt.get("status") != "ok"
                or receipt.get("pack") != plan.pack or receipt.get("already_running") is not False):
            return {"status": "stopped", "stop": "launch",
                    "why": f"the launch receipt does not verify the plan's pack {plan.pack!r}: "
                           f"{receipt!r}", "save": scored, "source_save": save, "mod": plan.mod,
                    "run_dir": str(target), "pack_required": plan.pack,
                    "pack_actual": (receipt or {}).get("pack"), "pack_verified": False,
                    "attached": False}
        receipt = dict(receipt)
        try:
            write_launch_receipt(folder, receipt)
        except OSError:
            pass                                   # the report still carries the receipt
    actual_pack = str(receipt["pack"])
    provenance = {
        "reused": bool(reuse),
        "process": receipt.get("process"),
        "process_verified": bool(receipt.get("process_verified")),
        "applied": receipt.get("applied"),
        "applied_verified": bool(receipt.get("applied_verified")),
        "receipt": str(_receipt_path(folder)),
    }

    engine = check or assets.check_frame
    describer = describe or describe_segment
    sleeper = sleep or time.sleep
    pilot = driver or PilotDriver(run_dir=target)
    entries: list[dict] = []
    try:
        started = pilot.start(scored, pack=actual_pack) or {}
    except DriverError as exc:
        finished, finish_error = _finish(pilot)
        return _stopped_run("driver", str(exc), finished, finish_error, scored, save,
                            plan, target, actual_pack, entries)

    try:
        for index, segment in enumerate(chosen, 1):
            segment_dir = target / "segments" / segment.name
            frames_dir = segment_dir / "frames"
            frames_dir.mkdir(parents=True, exist_ok=True)
            if segment.cued:
                hold_s = int(segment.record_s + math.ceil(segment.preroll_s) + 20)
                pilot.wait_for(segment.cue,
                               timeout_s=CUE_TIMEOUTS.get(segment.cue, CUE_TIMEOUT_S),
                               hold_s=hold_s,
                               record_s=int(math.ceil(segment.record_s + segment.preroll_s)))
                _check_hold(pilot, segment.record_s)       # still this process and still holding
            if segment.preroll_s:
                sleeper(segment.preroll_s)
                _check_hold(pilot, segment.record_s)       # the preroll can outlast the hold
            record_started = time.time()
            returned = pilot.record(frames_dir, segment.record_s, segment.fps, segment.width)
            frames = _frame_paths(returned, frames_dir, since=record_started)
            checks = {name: _score_asset(engine, plan.checks[name], frames, target)
                      for name in segment.checks}
            summary = _summarise(describer, segment, frames, segment_dir)
            entries.append(report_mod.segment_result(
                segment.name, segment.cue, segment.record_s,
                [_rel(path, target) for path in frames], checks, summary,
                preroll_s=segment.preroll_s))
            if callable(progress):
                progress({"segment": segment.name, "done": index, "of": len(chosen)})
    except CueMissed as exc:
        finished, finish_error = _finish(pilot)
        return _stopped_run("cue_missed", str(exc), finished, finish_error, scored, save,
                            plan, target, actual_pack, entries)
    except DriverError as exc:
        finished, finish_error = _finish(pilot)
        return _stopped_run("driver", str(exc), finished, finish_error, scored, save,
                            plan, target, actual_pack, entries)

    finished, finish_error = _finish(pilot)
    operational = _operational(finished, finish_error)
    minutes = (time.monotonic() - started_at) / 60.0
    result = report_mod.build_report(
        scored, plan.mod, entries, minutes,
        pack=actual_pack, autosave_written=_autosave_flag(finished),
        pack_required=plan.pack, pack_actual=actual_pack, pack_verified=True,
        launch=receipt, provenance=provenance, quit=finished.get("quit"),
        quit_route=finished.get("quit_route"), end_screen=finished.get("end_screen"),
        operational=operational)
    json_path, md_path = report_mod.write_report(result, target)
    line = report_mod.notice_line(result)
    if callable(notice):
        notice(line)
    elif notice is None:
        _notify(line)
    status = result["verdict"] if operational["status"] == "complete" else "incomplete"
    return {"status": status, "verdict": result["verdict"], **result, "run_dir": str(target),
            "report_json": str(json_path), "report_md": str(md_path),
            "source_save": save, "clone": scored, "attached": bool(reuse),
            "provenance": provenance}


def _stopped_run(stop: str, why: str, finished: Mapping[str, Any], finish_error: str | None,
                 scored: str, source: str, plan: Any, target: Path,
                 actual_pack: str, entries: Sequence[Mapping[str, Any]] = ()) -> dict:
    """A stopped run keeps its cleanup state and every earlier pixel row it already scored."""
    operational = _operational(finished, finish_error)
    return {"status": "stopped", "stop": stop, "why": why, "save": scored,
            "source_save": source, "mod": plan.mod, "run_dir": str(target),
            "pack_required": plan.pack, "pack_actual": actual_pack, "pack_verified": True,
            "segments": list(entries),
            "quit": finished.get("quit"), "quit_route": finished.get("quit_route"),
            "end_screen": finished.get("end_screen"), "operational": operational}


# --------------------------------------------------------------------------- capture
def parse_timestamp(value: str) -> float:
    """``M:SS`` (or plain seconds) as seconds; anything else is refused."""
    text = str(value or "").strip()
    if text.startswith("-"):
        raise PlanError(f"a moment must be a time at or after 0:00, not {value!r}")
    parts = text.split(":")
    try:
        if len(parts) == 1:
            seconds = float(parts[0])
        elif len(parts) == 2:
            minutes, rest = int(parts[0]), float(parts[1])
            if rest < 0 or minutes < 0:
                raise ValueError
            seconds = minutes * 60.0 + rest
        else:
            raise ValueError
    except ValueError:
        raise PlanError(f"a moment must be M:SS (for example 0:03), not {value!r}") from None
    if not math.isfinite(seconds) or seconds < 0:
        raise PlanError(f"a moment must be a time at or after 0:00, not {value!r}")
    return seconds


def frame_at(frames: Sequence[Path], fps: int, seconds: float, *, clip: str = "the clip") -> Path:
    """The frame that covers ``seconds`` at ``fps`` (frame N is at N/fps)."""
    index = int(seconds * fps)
    if index < 0 or index >= len(frames):
        duration = len(frames) / max(fps, 1)
        raise PlanError(f"{seconds:g}s is past the end of {clip} "
                        f"({len(frames)} frame(s) at {fps} fps, {duration:.2f}s)")
    return frames[index]


def _crop_box(size: tuple[int, int], region: Sequence[float]) -> tuple[int, int, int, int] | None:
    """Pixel box for a fractional region, clamped the same way ``assets`` clamps its own."""
    width, height = size
    x, y, w, h = (float(value) for value in region)
    left = max(0, min(width, round(x * width)))
    top = max(0, min(height, round(y * height)))
    right = max(left, min(width, round((x + w) * width)))
    bottom = max(top, min(height, round((y + h) * height)))
    if right <= left or bottom <= top:
        return None
    return left, top, right, bottom


def _write_crop(png: Path, region: Sequence[float] | None, out: Path, *, whole: bool = False) -> Path:
    """Write ``region`` of ``png`` (or the whole frame) to ``out`` as PNG."""
    from PIL import Image
    out.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(png) as opened:
        image = opened.convert("RGB")
        if whole or region is None:
            image.save(out, format="PNG")
            return out
        box = _crop_box(image.size, region)
        if box is None:
            raise PlanError(f"the region {list(region)} is empty on {png}")
        image.crop(box).save(out, format="PNG")
        return out


def _find_segment_frames(run_dir: Path, segment: str) -> list[Path]:
    """Frames a previous ``run`` recorded for one segment, in recording order."""
    folder = Path(run_dir) / "segments" / segment / "frames"
    if folder.is_dir():
        return sorted(path for path in folder.iterdir()
                      if path.is_file() and path.suffix.lower() in FRAME_SUFFIXES)
    return []


def _run_allocation(name: str, mod: str) -> tuple[datetime.datetime, int] | None:
    """(started at, allocation number) for a run directory this mod owns, else None.

    ``_unique_run_dir`` names runs ``<stamp>_<mod>`` and, for a same-second collision,
    ``<stamp>_<mod>-2``, ``-3`` ... ``-10``. The unsuffixed name is allocation 1, and the
    number is compared as an integer, so ``-10`` is newer than ``-9``. Exactly this mod's
    names match: another mod whose slug merely ends with the same words is not a candidate.
    """
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2}_\d{6})_" + re.escape(mod) + r"(?:-(\d+))?",
                         name)
    if not match:
        return None
    try:
        started = datetime.datetime.strptime(match.group(1), "%Y-%m-%d_%H%M%S")
    except ValueError:
        return None
    return started, int(match.group(2) or 1)


def _newest_run(root: Path, mod: str) -> Path | None:
    """The newest run this mod owns, by allocation (timestamp then same-second number).

    A run's report may name the mod it scored; a directory whose report names another mod is
    skipped (a renamed/replaced check must not calibrate on it). An incomplete run with no
    report is still the newest allocation: capture then finds no frames and refuses, rather
    than silently cutting an older run's frames (plan R3).
    """
    runs = root / "runs"
    if not runs.is_dir():
        return None
    candidates: list[tuple[tuple[datetime.datetime, int], Path]] = []
    for path in runs.iterdir():
        if not path.is_dir():
            continue
        key = _run_allocation(path.name, mod)
        if key is None:
            continue
        try:
            report = json.loads((path / "report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = None
        if isinstance(report, dict) and report.get("mod") not in (None, mod):
            continue
        candidates.append((key, path))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def capture(mod: str, segment: str, pass_at: str, fail_at: str, *, asset: str | None = None,
            yes: bool = False, frames: Sequence[str | Path] | None = None,
            save: str | None = None, run_dir: str | Path | None = None, driver: Any = None,
            data_dir: str | Path | None = None, clone: Callable | None = None,
            watcher: Callable | None = None, notice: Callable | None = None,
            sleep: Callable | None = None, progress: Callable | None = None,
            launch: Callable | None = None, reuse: bool = False,
            identity: Callable[[], Mapping[str, Any] | None] | None = None,
            fingerprint: Callable[[str], Mapping[str, Any] | None] | None = None) -> dict:
    """Cut one segment's two named moments into ``refs/`` after a human ``yes`` (plan 2.3/R3).

    Returns ``{status, asset, segment, pass_at, fail_at, pass_seconds, fail_seconds, frames,
    similarities, crossed, verdicts, margin, preview, paths, saved, refused}`` plus, after a
    live recording, ``operational``/``quit``/``quit_route``/``end_screen``/``pack``.
    ``status`` is ``ok`` (saved or previewed), ``refused`` (the pair is undecidable, a moment is
    past the clip, ...) or ``stopped`` (the recording driver failed). A live recording is owned
    by one try/finally: the driver is finished exactly once after start on every path, and an
    unclean exit is surfaced in ``operational``. Offline capture never creates a driver.
    """
    root = data_root(data_dir)
    folder = mod_directory(mod, data_dir)
    plan = load_plan(folder, require_refs=False)
    chosen = plan.segment(segment)
    name = asset or (chosen.checks[0] if chosen.checks else None)
    if not name:
        raise PlanError(f"{segment}: no asset to capture (the segment lists no checks)")
    if name not in plan.checks:
        raise PlanError(f"{segment}: no check named {name!r} (has: {', '.join(sorted(plan.checks))})")
    spec = plan.checks[name]
    reference = spec.get("reference") if isinstance(spec.get("reference"), dict) else {}
    margin = float(reference.get("margin", DEFAULT_MARGIN))
    ref_region = tuple(reference["region"]) if reference.get("region") else None
    region = tuple(spec.get("region") or (0.0, 0.0, 1.0, 1.0))
    whole = ref_region is not None          # keep the spec's reference.region meaningful
    pass_seconds = parse_timestamp(pass_at)
    fail_seconds = parse_timestamp(fail_at)

    target = (_inside(Path(run_dir), root, "the capture directory") if run_dir
              else _inside(_unique_capture_dir(root, plan.mod), root, "the capture directory"))
    if run_dir and save:
        occupied = _occupant(target)
        if occupied:
            return _refused(f"the live capture directory {target} is not fresh ({occupied}); "
                            f"an old run's frames could be mistaken for this recording",
                            mod=plan.mod)

    paths: list[Path]
    live_info: dict[str, Any] = {}
    if frames:
        paths = [Path(item) for item in frames if Path(item).is_file()]
        if len(paths) != len(list(frames)):
            missing = [str(item) for item in frames if not Path(item).is_file()]
            raise PlanError(f"frame(s) not found: {missing}")
    else:
        source = Path(run_dir) if run_dir else _newest_run(root, plan.mod)
        # --save means record live: an old run's frames must not stand in for the recording.
        paths = _find_segment_frames(source, segment) if source and not save else []
        if not paths and save:
            if not valid_tune_save(save):
                return _refused(f"only {jobs.TUNE_SAVE_PREFIX}* saves are checked, not {save!r}",
                                mod=plan.mod)
            if (watcher or jobs.watcher_running)():
                return _refused("pipeline.watch_save is running: stop it first", mod=plan.mod)
            if not plan.pack:
                return _refused("segments.json must name the plan's expected test pack for a "
                                "live capture (add \"pack\": \"Mu Test\"; plan R6)", mod=plan.mod)
            if reuse:
                # Verify the attach before cloning (F15, matching run()): a refused
                # --reuse-launch must not leave a stray CAREER-TUNE-CHK* save behind.
                try:
                    attached_receipt = attach_launch(read_launch_receipt(folder), plan.pack,
                                                     identity=identity, fingerprint=fingerprint)
                except (LaunchError, DriverError, OSError) as exc:
                    return {"status": "stopped", "stop": "attach", "why": str(exc),
                            "mod": plan.mod, "pack_required": plan.pack, "attached": False}
            maker = clone or default_clone
            try:
                scored = str(maker(save, CHK_PREFIX) or "").strip()
            except (OSError, ValueError) as exc:
                return _refused(f"could not clone {save}: {exc}", mod=plan.mod)
            if not valid_tune_save(scored) or scored.upper() == save.upper():
                return _refused(f"the clone callable returned {scored!r}; a live capture scores "
                                f"a fresh {CHK_PREFIX}<letter> save, never the one it was given",
                                mod=plan.mod)
            target.mkdir(parents=True, exist_ok=True)
            if reuse:
                receipt = attached_receipt
            else:
                launcher = launch or (lambda wanted: default_launch(wanted, target / "launch"))
                try:
                    receipt = launcher(plan.pack) or {}
                except (DriverError, OSError) as exc:
                    return {"status": "stopped", "stop": "launch", "why": str(exc), "mod": plan.mod,
                            "pack_required": plan.pack, "attached": False}
                if (not isinstance(receipt, dict) or receipt.get("status") != "ok"
                        or receipt.get("pack") != plan.pack
                        or receipt.get("already_running") is not False):
                    return {"status": "stopped", "stop": "launch",
                            "why": f"the launch receipt does not verify the plan's pack "
                                   f"{plan.pack!r}: {receipt!r}", "mod": plan.mod,
                            "pack_required": plan.pack, "attached": False}
                receipt = dict(receipt)
                try:
                    write_launch_receipt(folder, receipt)
                except OSError:
                    pass
            actual_pack = str(receipt["pack"])
            live = driver or PilotDriver(run_dir=target)
            started = False
            stop: dict | None = None
            finished: Mapping[str, Any] = {}
            finish_error: str | None = None
            try:
                live.start(scored, pack=actual_pack)
                started = True
                if chosen.cued:
                    hold_s = int(chosen.record_s + math.ceil(chosen.preroll_s) + 20)
                    live.wait_for(chosen.cue,
                                  timeout_s=CUE_TIMEOUTS.get(chosen.cue, CUE_TIMEOUT_S),
                                  hold_s=hold_s,
                                  record_s=int(math.ceil(chosen.record_s + chosen.preroll_s)))
                    _check_hold(live, chosen.record_s)
                if chosen.preroll_s:
                    (sleep or time.sleep)(chosen.preroll_s)
                    _check_hold(live, chosen.record_s)
                frames_dir = target / "segments" / segment / "frames"
                frames_dir.mkdir(parents=True, exist_ok=True)
                record_started = time.time()
                paths = _frame_paths(live.record(frames_dir, chosen.record_s, chosen.fps,
                                                 chosen.width), frames_dir, since=record_started)
                if not paths:
                    stop = _refused(f"the driver recorded no frames for {segment}", mod=plan.mod)
            except CueMissed as exc:
                stop = {"status": "stopped", "stop": "cue_missed", "why": str(exc), "mod": plan.mod}
            except DriverError as exc:
                stop = {"status": "stopped", "stop": "driver", "why": str(exc), "mod": plan.mod}
            finally:
                if started:
                    finished, finish_error = _finish(live)
            operational = _operational(finished, finish_error)
            live_info = {"operational": operational, "quit": finished.get("quit"),
                         "quit_route": finished.get("quit_route"),
                         "end_screen": finished.get("end_screen"),
                         "pack": actual_pack, "pack_required": plan.pack,
                         "pack_actual": actual_pack, "pack_verified": True,
                         "launch": receipt, "attached": bool(reuse),
                         "provenance": {"reused": bool(reuse),
                                        "process": receipt.get("process"),
                                        "process_verified": bool(receipt.get("process_verified")),
                                        "applied": receipt.get("applied"),
                                        "applied_verified": bool(receipt.get("applied_verified"))}}
            if stop:
                return {**stop, **live_info}
        elif not paths:
            where = f" in {source}" if source else ""
            return _refused(f"no frames for segment {segment!r}{where}; run the check first, "
                            f"or pass --save to record one segment", mod=plan.mod)

    frames_sorted = sorted(paths)
    pass_frame = frame_at(frames_sorted, chosen.fps, pass_seconds, clip=f"segment {segment!r}")
    fail_frame = frame_at(frames_sorted, chosen.fps, fail_seconds, clip=f"segment {segment!r}")

    preview_dir = target / "previews"
    pass_preview = preview_dir / f"{name}_pass.png"
    fail_preview = preview_dir / f"{name}_native.png"
    _write_crop(pass_frame, region, pass_preview, whole=whole)
    _write_crop(fail_frame, region, fail_preview, whole=whole)

    provisional = json.loads(json.dumps(spec))
    provisional["reference"] = {
        "pass": str(pass_preview), "fail": str(fail_preview), "margin": margin}
    if ref_region is not None:
        provisional["reference"]["region"] = list(ref_region)

    pass_result = assets.check_frame(pass_frame, provisional)
    fail_result = assets.check_frame(fail_frame, provisional)
    ref_box = ref_region if whole else None
    crossed = {
        "pass_frame_vs_pass": assets.crop_similarity(pass_frame, region, pass_preview, ref_box),
        "pass_frame_vs_fail": assets.crop_similarity(pass_frame, region, fail_preview, ref_box),
        "fail_frame_vs_pass": assets.crop_similarity(fail_frame, region, pass_preview, ref_box),
        "fail_frame_vs_fail": assets.crop_similarity(fail_frame, region, fail_preview, ref_box),
    }
    similarities = {
        "pass": pass_result.get("measures", {}),
        "fail": fail_result.get("measures", {}),
    }
    verdicts = {"pass": pass_result.get("status"), "fail": fail_result.get("status")}

    refused = None
    if pass_result.get("status") != "pass" or fail_result.get("status") != "fail":
        numbers = (f"pass frame: similarity_pass={similarities['pass'].get('similarity_pass', 0):.3f} "
                   f"similarity_fail={similarities['pass'].get('similarity_fail', 0):.3f}; "
                   f"fail frame: similarity_pass={similarities['fail'].get('similarity_pass', 0):.3f} "
                   f"similarity_fail={similarities['fail'].get('similarity_fail', 0):.3f}")
        refused = (f"the pair is inside the margin ({margin:g}) and would decide nothing "
                   f"[{numbers}]")
        return {"status": "refused", "why": refused, "mod": plan.mod, "segment": segment,
                "asset": name, "pass_at": pass_at, "fail_at": fail_at,
                "pass_seconds": pass_seconds, "fail_seconds": fail_seconds,
                "similarities": similarities, "crossed": crossed,
                "verdicts": verdicts, "margin": margin,
                "preview": {"pass": str(pass_preview), "native": str(fail_preview)},
                "paths": {}, "saved": False, "refused": refused, **live_info}

    destination = {
        "pass": Path(reference.get("pass") or folder / "refs" / f"{name}_pass.png"),
        "native": Path(reference.get("fail") or folder / "refs" / f"{name}_native.png"),
    }
    for key, path in destination.items():
        destination[key] = _inside(path, root, f"the {key} reference")
    preview = {"pass": str(pass_preview), "native": str(fail_preview)}
    if not yes:
        return {"status": "ok", "mod": plan.mod, "segment": segment, "asset": name,
                "pass_at": pass_at, "fail_at": fail_at, "pass_seconds": pass_seconds,
                "fail_seconds": fail_seconds, "similarities": similarities,
                "crossed": crossed, "verdicts": verdicts, "margin": margin,
                "preview": preview,
                "paths": {key: str(path) for key, path in destination.items()},
                "saved": False, "refused": None,
                "why": "preview only: re-run with --yes to write the reference pair",
                **live_info}
    for key, path in destination.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(preview[key], path)
    return {"status": "ok", "mod": plan.mod, "segment": segment, "asset": name,
            "pass_at": pass_at, "fail_at": fail_at, "pass_seconds": pass_seconds,
            "fail_seconds": fail_seconds, "similarities": similarities, "crossed": crossed,
            "verdicts": verdicts, "margin": margin, "preview": preview,
            "paths": {key: str(path) for key, path in destination.items()},
            "saved": True, "refused": None, **live_info}


# --------------------------------------------------------------------------- the real driver
def _last_json(text: str) -> dict:
    """The last JSON object in a process's output, even when it is pretty-printed.

    The pilot CLI prints its one JSON result with ``json.dumps(..., indent=1)``, so it spans
    many lines and a line-by-line parse saw only the closing brace (F13: every real
    ``default_launch`` failed with "no JSON result"). Scan the *last* object start backwards
    and let ``raw_decode`` consume the whole object; earlier log lines may contain braces of
    their own, so only a complete dict counts.
    """
    data = text or ""
    decoder = json.JSONDecoder()
    best: dict | None = None
    best_end = -1
    index = data.find("{")
    while index >= 0:
        try:
            value, end = decoder.raw_decode(data[index:])
        except ValueError:
            index = data.find("{", index + 1)
            continue
        if isinstance(value, dict) and index + end > best_end:
            best, best_end = value, index + end
        index = data.find("{", index + 1)
    return best or {}


class PilotDriver:
    """The real driver: ``watch-game`` holds the game while this driver records it.

    ``start`` remembers the clone and pack. Each cued segment starts
    ``python3 -m tools.madden_pilot watch-game --save <clone> --to <stop>
    --hold-seconds <record_s + preroll + 20> --run-dir <this run's native dir> --ready-token T``
    as a background process. The job writes ``<native>/ready.json`` immediately before its hold;
    the marker carries the cue's anchor, route, screen, clock, hold deadline, pid and token T.
    The driver accepts it only from the process it started, only while that process is alive, and
    only when the remaining hold covers the recording (F1/F3). It then records during the hold;
    when the hold ends the job quits the franchise on its own and ``finish`` collects its
    ``result.json`` (``quit``, ``quit_route``, ``autosave_written``, ``end_screen``) rather than
    starting a second quit.

    ``kickoff`` records after the kick; ``halftime_start`` waits for the job's D2 supersim recipe
    to reach the half's last snap; ``supersim_menu`` is accepted only when the job's ready screen
    is ``SUPERSIM_MENU``. A process that exits before its marker, one whose marker names another
    cue or screen, one still short at ``timeout_s``, and a marker whose hold is too short all
    stop the segment as ``CueMissed`` before a frame is recorded. The halftime show itself has
    not yet been measured live (plan 2.5a/R4); the route and its ready anchor are what ships.
    """

    def __init__(self, python: str | None = None, cwd: str | Path | None = None,
                 timeout_s: float = 900.0, run_dir: str | Path | None = None):
        self.python = python or os.environ.get("MU_PILOT_PYTHON") or "python3"
        self.cwd = Path(cwd) if cwd else Path(__file__).resolve().parents[2]
        self.timeout_s = timeout_s
        self.run_dir = Path(run_dir) if run_dir else None
        self.save: str | None = None
        self.pack: str | None = None
        self.saves = jobs.saves_dir()
        self._before: dict[str, float | None] = {}
        self._jobs: list[dict] = []
        self._job_number = 0
        self._temp: Path | None = None

    def _call(self, *args: str, timeout: float | None = None) -> dict:
        command = [self.python, "-m", "tools.madden_pilot", *args]
        try:
            proc = subprocess.run(command, cwd=str(self.cwd), capture_output=True, text=True,
                                  timeout=timeout or self.timeout_s)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise DriverError(f"{' '.join(args)} could not run: {exc}") from exc
        payload = _last_json(proc.stdout)
        if proc.returncode != 0:
            why = (payload.get("why") or payload.get("reason") or payload.get("refused")
                   or proc.stderr.strip() or f"exit {proc.returncode}")
            raise DriverError(f"{' '.join(args)} failed: {why}")
        return payload

    def start(self, save: str, pack: str | None = None) -> dict:
        self.save, self.pack = save, pack
        self.saves = jobs.saves_dir()
        self._before = {"save": _mtime(self.saves / save),
                        "autosave": _mtime(self.saves / f"{save}-AUTOSAVE")}
        return {"status": "ok", "save": save, "pack": pack}

    # ---------------------------------------------------------------- the background job
    def _work_root(self) -> Path:
        """Where the job's own steps/ and result.json go: the run's dir, else a temp dir."""
        if self.run_dir is not None:
            return self.run_dir
        if self._temp is None:
            import tempfile
            self._temp = Path(tempfile.mkdtemp(prefix="mu-mod-check-watch-"))
        return self._temp

    @staticmethod
    def _step_screens(native: Path) -> list[str]:
        """Every screen the job has looked at, newest first, from its own ``steps/*.json``."""
        steps_dir = Path(native) / "steps"
        steps = sorted(steps_dir.glob("*.json")) if steps_dir.is_dir() else []
        screens: list[str] = []
        for path in reversed(steps):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(data, dict) and data.get("screen"):
                screens.append(str(data["screen"]))
        return screens

    @staticmethod
    def _read_ready(native: Path, token: str, process: Any) -> dict | None:
        """This job's own ready marker, or None (wrong/absent token, pid or file)."""
        try:
            data = json.loads((Path(native) / READY_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(data, dict) or data.get("ready_token") != token:
            return None
        pid, proc_pid = data.get("pid"), getattr(process, "pid", None)
        if pid is not None and proc_pid is not None:
            try:
                if int(pid) != int(proc_pid):
                    return None
            except (TypeError, ValueError):
                return None
        return data

    @staticmethod
    def _read_result(native: Path) -> dict | None:
        try:
            data = json.loads((Path(native) / "result.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    @staticmethod
    def _tail(process: Any, limit: int = 600) -> str:
        """The last of the job's stdout/stderr, read once it has exited."""
        try:
            if process.poll() is None:
                return "(still running)"
        except Exception:
            return ""
        parts = []
        for name in ("stdout", "stderr"):
            stream = getattr(process, name, None)
            if stream is None:
                continue
            try:
                text = stream.read()
            except Exception:
                text = ""
            if text:
                parts.append(f"{name}: {str(text).strip()}")
        return " | ".join(parts)[-limit:]

    @staticmethod
    def _stop(process: Any, grace: float = 5.0) -> None:
        """Terminate, then kill: never leave a job behind (its own hold could be long)."""
        for action in ("terminate", "kill"):
            try:
                getattr(process, action)()
            except Exception:
                pass
            try:
                process.wait(timeout=grace)
                return
            except subprocess.TimeoutExpired:
                continue
            except Exception:
                return

    def _collect_job(self, job: dict) -> dict:
        """Wait (bounded) for one job, then take its own result.json and exit status."""
        process = job.get("process")
        if process is None:
            return {}
        if process.poll() is None:
            try:
                process.wait(timeout=float(job.get("exit_timeout") or 60.0))
            except subprocess.TimeoutExpired:
                self._stop(process)
                job["stopped"] = "finish-timeout"
        code = process.poll()
        result = self._read_result(Path(job["dir"]))
        if result is None:
            return {"quit": False, "quit_route": None, "autosave_written": None,
                    "end_screen": None, "pack": self.pack, "exit_code": code,
                    "job_status": None,
                    "why": f"watch-game wrote no result.json ({self._tail(process)})"}
        result = dict(result)
        result["exit_code"] = code
        result["job_status"] = result.get("status")
        return result

    @staticmethod
    def _job_problem(job: dict, result: Mapping[str, Any]) -> str | None:
        """Why a previous job's cleanup is not confirmed, or None when it is (F4/R2).

        One shared rule for the live-hold gate and the final report: a missing result, a
        non-zero exit, a stopped/timeout job or an unconfirmed quit all mean the game is not
        known to be released, so no later PC action may start.
        """
        if not result:
            return "wrote no result.json"
        code = result.get("exit_code")
        if code not in (None, 0):
            return f"exited {code}"
        status = result.get("job_status")
        if status not in (None, "ok"):
            return f"reported {status}"
        if result.get("quit") is not True:
            why = result.get("why") or result.get("reason")
            return "did not confirm quitting the game" + (f": {why}" if why else "")
        stopped = job.get("stopped")
        if stopped:
            return f"was stopped ({stopped})"
        return None

    def _collect_running(self) -> None:
        """Every previous job is collected once — exited ones included — before the next action.

        A previous segment that did not confirm its quit (failed, no result, non-zero exit,
        stopped, timed out) must stop the run *before* the next job loads the franchise or
        drives Windows, rather than being aggregated after a dangerous overlap (R2). The
        collected result is kept on the job so ``finish`` never asks for it twice.
        """
        problems: list[str] = []
        for job in self._jobs:
            if "collected" not in job:
                job["collected"] = self._collect_job(job)
            problem = self._job_problem(job, job["collected"])
            if problem:
                job["failure"] = problem
                problems.append(f"{job.get('cue') or 'watch-game'}: {problem}")
        if problems:
            raise DriverError("the previous segment's cleanup is not confirmed ("
                              + "; ".join(problems)
                              + "); not starting another PC action — leave the game by hand and "
                                "check the run report")

    def _autosave_seen(self) -> bool | None:
        if not self.save:
            return None
        after = _mtime(self.saves / f"{self.save}-AUTOSAVE")
        before = self._before.get("autosave")
        return bool(after is not None and (before is None or after > before))

    def wait_for(self, cue: str, timeout_s: float = CUE_TIMEOUT_S,
                 hold_s: int = 60, record_s: int = 0) -> None:
        """Start ``watch-game`` and return once *its own* ready marker says it is holding.

        No historical ``steps/*.json`` classification is readiness (F3). The marker must carry
        this invocation's token, come from this process's pid, name the cue's anchor and, for
        the static super-sim screen, report ``SUPERSIM_MENU``. The remaining hold must still
        cover ``record_s`` plus a margin, so a late marker stops instead of recording past it.
        """
        route = CUE_ROUTES.get(cue)
        if route is None:
            raise CueMissed(cue, f"allowed cues are {', '.join(CUES)}")
        target, anchor, required_screen = route
        if not self.save:
            raise CueMissed(cue, "start() was never called")
        try:
            hold = int(hold_s)
        except (TypeError, ValueError):
            raise CueMissed(cue, f"hold_s must be whole seconds, not {hold_s!r}") from None
        self._collect_running()
        self._job_number += 1
        native = self._work_root() / "watch-game" / f"{self._job_number:02d}_{target}"
        native.mkdir(parents=True, exist_ok=True)
        token = uuid.uuid4().hex
        command = [self.python, "-m", "tools.madden_pilot", "watch-game",
                   "--save", self.save, "--to", target,
                   "--hold-seconds", str(hold), "--run-dir", str(native),
                   "--ready-token", token]
        if self.pack:
            command += ["--pack", self.pack]
        try:
            process = subprocess.Popen(command, cwd=str(self.cwd), stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True)
        except OSError as exc:
            raise DriverError(f"could not start watch-game: {exc}") from exc
        job = {"process": process, "dir": native, "hold": hold, "cue": cue, "token": token,
               "exit_timeout": max(60.0, hold + JOB_QUIT_GRACE_S), "stopped": None,
               "ready": None}
        self._jobs.append(job)

        deadline = time.monotonic() + max(0.0, float(timeout_s))
        last: str | None = None
        needed = float(record_s) + READY_MARGIN_S
        while True:
            screens = self._step_screens(native)
            if screens:
                last = screens[0]
            code = process.poll()
            ready = self._read_ready(native, token, process)
            if ready is not None and code is None:
                if ready.get("anchor") != anchor:
                    raise CueMissed(cue, f"watch-game signalled {ready.get('anchor')!r}, "
                                         f"not {anchor!r}")
                if required_screen and ready.get("screen") != required_screen:
                    raise CueMissed(cue, f"watch-game's ready screen is {ready.get('screen')!r}, "
                                         f"not {required_screen!r}")
                try:
                    remaining = float(ready.get("hold_until_epoch")) - time.time()
                except (TypeError, ValueError):
                    remaining = None
                if remaining is not None and remaining < needed:
                    self._stop(process)
                    job["stopped"] = "hold-too-short"
                    raise CueMissed(cue, f"the job's hold has {remaining:.1f}s left; recording "
                                         f"{record_s:g}s plus a {READY_MARGIN_S:g}s margin needs "
                                         f"{needed:g}s")
                job["ready"] = ready
                return
            if code is not None:
                detail = f"last screen {last or 'none'}"
                tail = self._tail(process)
                when = "after signalling ready" if ready is not None else "before its ready marker"
                raise CueMissed(cue, f"watch-game exited ({code}) {when}: {detail}"
                                     + (f"; {tail}" if tail else ""))
            if time.monotonic() >= deadline:
                self._stop(process)
                job["stopped"] = "timeout"
                tail = self._tail(process)
                raise CueMissed(cue, f"watch-game did not signal ready in {timeout_s:g}s "
                                     f"(last screen {last or 'none'}); stopped it"
                                     + (f"; {tail}" if tail else ""))
            time.sleep(min(CUE_POLL_S, max(0.01, deadline - time.monotonic())))

    def check_hold(self, seconds: int) -> dict:
        """Refuse to record unless the job just waited for is alive and still holds (F3/R1).

        Readiness is checked when the ready marker arrives; a preroll of up to 10 s then runs
        between that check and the recorder, so the process can die or the hold can run out in
        the gap. This is the same check again, at the moment the recorder is about to take the
        screen. There is no unmanaged path: a raw ``record`` with no watch-game job is refused
        because there is nothing that says the screen is this game and this invocation.
        """
        job = None
        for candidate in reversed(self._jobs):
            if candidate.get("ready") is not None:
                job = candidate
                break
        if job is None:
            raise DriverError("record() has no accepted watch-game job to verify; the driver "
                              "records only the job it waited for (call wait_for first)")
        process = job.get("process")
        if process is None:
            raise DriverError("the accepted watch-game job has no process to verify")
        try:
            code = process.poll()
        except Exception as exc:
            raise DriverError(f"cannot read the watch-game process state: {exc}") from exc
        if code is not None:
            job["stopped"] = job.get("stopped") or "exited-before-record"
            raise DriverError(f"watch-game exited ({code}) before the recording started; its "
                              f"screen is no longer the game, so nothing is recorded")
        try:
            remaining = float((job.get("ready") or {}).get("hold_until_epoch")) - time.time()
        except (TypeError, ValueError):
            remaining = None
        needed = float(seconds) + READY_MARGIN_S
        if remaining is None:
            raise DriverError("the watch-game ready marker has no hold deadline; not recording "
                              "past a hold nobody can bound")
        if remaining < needed:
            job["stopped"] = job.get("stopped") or "hold-too-short-before-record"
            raise DriverError(f"the job's hold has {remaining:.1f}s left but recording "
                              f"{seconds:g}s plus a {READY_MARGIN_S:g}s margin needs {needed:g}s; "
                              f"not recording past its hold")
        return job

    def record(self, out_dir: Path, seconds: int, fps: int, width: int) -> list[Path]:
        """Record through ``win_agent``. Only an accepted, live watch-game job may be recorded."""
        self.check_hold(int(seconds))
        from tools.madden_pilot import win
        from tools.madden_pilot.navigate import TITLE
        folder = Path(out_dir)
        folder.mkdir(parents=True, exist_ok=True)
        started = time.time()
        try:
            win.call(["record", "--title", TITLE, "--dir", str(folder),
                      "--seconds", str(int(seconds)), "--fps", str(int(fps)),
                      "--width", str(int(width))], timeout=max(60, int(seconds) + 30))
        except Exception as exc:                # WinError and anything else from the Windows side
            raise DriverError(f"could not record {seconds}s at {fps} fps: {exc}") from exc
        # win_agent reports a frame *count*; the files themselves are what the checks read.
        return _frame_paths(None, folder, since=started)

    def finish(self) -> dict:
        """Collect the background job(s): their own quit, not a second one."""
        if self._jobs:
            collected = [job["collected"] if "collected" in job else self._collect_job(job)
                         for job in self._jobs]
            last = collected[-1] if collected else {}
            statuses = [entry.get("job_status") for entry in collected]
            bad = [status for status in statuses if status not in (None, "ok")]
            result: dict[str, Any] = {
                "quit": bool(last.get("quit")),
                "quit_route": last.get("quit_route"),
                "autosave_written": last.get("autosave_written"),
                "end_screen": last.get("end_screen"),
                "pack": last.get("pack") or self.pack,
                "job_status": (bad[0] if bad else
                               (None if all(status is None for status in statuses) else "ok")),
                "exit_code": last.get("exit_code"),
                "exit_codes": [entry.get("exit_code") for entry in collected],
            }
            written = [entry.get("autosave_written") for entry in collected
                       if entry.get("autosave_written") is not None]
            result["autosave_written"] = any(written) if written else self._autosave_seen()
            whys = [str(entry.get("why") or entry.get("reason")) for entry in collected
                    if entry.get("why") or entry.get("reason")]
            whys += [f"{job.get('cue') or 'watch-game'}: {job['failure']}"
                     for job in self._jobs if job.get("failure")]
            if whys:
                result["why"] = "; ".join(dict.fromkeys(whys))
            stopped = [job["stopped"] for job in self._jobs if job.get("stopped")]
            if stopped:
                result["stopped"] = stopped
            return result
        # No background job was started: the old fallback quits through the pilot's own job.
        result = {}
        if self.save:
            try:
                result = self._call("quit", "--franchise", jobs.tile_name(self.save))
            except DriverError as exc:
                result = {"quit": False, "why": str(exc)}
            result["autosave_written"] = self._autosave_seen()
            result.setdefault("job_status", result.get("status"))
            result.setdefault("exit_code", None)
        result["pack"] = self.pack
        return result
