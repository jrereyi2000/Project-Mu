"""The visual mod check run and its reference capture (VISUAL_MOD_CHECK_PLAN 2.1, 2.3, R1-R3).

``run(save, mod, ...)``
    1. refuses anything but ``CAREER-TUNE-*`` (the same rule as ``sim-season``) and refuses while
       ``pipeline.watch_save`` runs, before touching anything;
    2. reads ``data/mod_checks/<mod>/segments.json`` and ``checks.json`` (``segments.load_plan``);
    3. clones the save it was given into a fresh ``CAREER-TUNE-CHK<n>`` and scores *that* clone.
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

``PilotDriver`` is the real driver. It shells out to ``python3 -m tools.madden_pilot
watch-game --save <clone> --to <cue> [--pack P]`` for each segment (that job is unit U2's and
may not exist yet), records through ``win_agent record`` and quits through the ``quit`` job. The
class imports and constructs without U2 merged; only ``wait_for`` needs the new job.
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
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from tools.madden_pilot import assets, jobs
from tools.mod_check import report as report_mod
from tools.mod_check.segments import (
    CUES, PlanError, Segment, data_root, load_plan, mod_directory,
)

CHK_PREFIX = "CAREER-TUNE-CHK"
MAX_CHK = 99
FRAME_SUFFIXES = (".png", ".jpg", ".jpeg")
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
    """Copy ``save`` to the first free ``<prefix><n>`` (n from 1) and return that save's name.

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
        candidate = f"{prefix}{number}"
        if candidate.upper() not in existing:
            name = candidate
            break
    if name is None:
        raise ValueError(f"no free {prefix}<n> name in {saves} (tried 1..{MAX_CHK})")
    destination = saves / name
    temp = saves / f".mu-mod-check-clone-{os.getpid()}.tmp"
    try:
        shutil.copyfile(source, temp)
        os.replace(temp, destination)
    finally:
        if temp.exists():
            temp.unlink()
    return name


def _frame_paths(returned: Iterable[str | Path] | None, folder: Path) -> list[Path]:
    """The driver's frames that exist (or the folder's images when it returned none), sorted."""
    paths: list[Path] = []
    for item in returned or []:
        path = Path(item)
        if not path.is_absolute() and not path.exists():
            path = folder / path
        if path.is_file():
            paths.append(path)
    if not paths and folder.is_dir():
        paths = [path for path in folder.iterdir()
                 if path.is_file() and path.suffix.lower() in FRAME_SUFFIXES]
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


def _finish_quietly(driver: Any) -> dict:
    try:
        return driver.finish() or {}
    except Exception:
        return {}


def _autosave_flag(finished: Mapping[str, Any]) -> bool | None:
    value = finished.get("autosave_written", finished.get("autosave"))
    return None if value is None else bool(value)


# --------------------------------------------------------------------------- run
def run(save: str, mod: str, segments: Sequence[str] | None = None, run_dir: str | Path | None = None,
        dry_run: bool = False, driver: Any = None, describe: Callable | None = None,
        data_dir: str | Path | None = None, watcher: Callable | None = None,
        clone: Callable | None = None, progress: Callable | None = None, *,
        pack: str | None = None, check: Callable | None = None,
        notice: Callable | None = None, sleep: Callable | None = None) -> dict:
    """Run one visual mod check and return the report (``status`` is the verdict).

    Extra keyword injections beyond the plan's signature, all optional: ``pack`` (the pack to
    launch; a launch that reports its own pack wins), ``check`` (default
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

    if dry_run:
        return {"status": "dry_run", "dry_run": True, "save": save, "mod": plan.mod,
                "mod_dir": str(folder),
                "segments": [segment.as_dict() for segment in chosen],
                "checks": plan.checks, "verdict": None, "minutes": 0.0,
                "pack": pack, "autosave_written": None,
                "would_clone": f"{CHK_PREFIX}<first free n>", "clone_from": save}

    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    target = _inside(Path(run_dir) if run_dir else root / "runs" / f"{stamp}_{plan.mod}",
                     root, "the run directory")

    maker = clone or default_clone
    try:
        scored = str(maker(save, CHK_PREFIX) or "").strip()
    except (OSError, ValueError, PlanError) as exc:
        return _refused(f"could not clone {save}: {exc}", save=save, mod=plan.mod)
    if not valid_tune_save(scored) or scored.upper() == save.upper():
        return _refused(f"the clone callable returned {scored!r}; a run scores a fresh "
                        f"{CHK_PREFIX}<n> save, never the one it was given", save=save, mod=plan.mod)

    engine = check or assets.check_frame
    describer = describe or describe_segment
    sleeper = sleep or time.sleep
    pilot = driver or PilotDriver()
    target.mkdir(parents=True, exist_ok=True)
    try:
        started = pilot.start(scored, pack=pack) or {}
    except DriverError as exc:
        return {"status": "stopped", "stop": "driver", "why": str(exc), "save": scored,
                "source_save": save, "mod": plan.mod, "run_dir": str(target)}

    entries: list[dict] = []
    try:
        for index, segment in enumerate(chosen, 1):
            segment_dir = target / "segments" / segment.name
            frames_dir = segment_dir / "frames"
            frames_dir.mkdir(parents=True, exist_ok=True)
            if segment.cued:
                pilot.wait_for(segment.cue, timeout_s=CUE_TIMEOUT_S)
            if segment.preroll_s:
                sleeper(segment.preroll_s)
            returned = pilot.record(frames_dir, segment.record_s, segment.fps, segment.width)
            frames = _frame_paths(returned, frames_dir)
            checks = {name: _score_asset(engine, plan.checks[name], frames, target)
                      for name in segment.checks}
            summary = _summarise(describer, segment, frames, segment_dir)
            entries.append(report_mod.segment_result(
                segment.name, segment.cue, segment.record_s,
                [_rel(path, target) for path in frames], checks, summary))
            if callable(progress):
                progress({"segment": segment.name, "done": index, "of": len(chosen)})
    except CueMissed as exc:
        _finish_quietly(pilot)
        return {"status": "stopped", "stop": "cue_missed", "why": str(exc), "save": scored,
                "source_save": save, "mod": plan.mod, "run_dir": str(target)}
    except DriverError as exc:
        _finish_quietly(pilot)
        return {"status": "stopped", "stop": "driver", "why": str(exc), "save": scored,
                "source_save": save, "mod": plan.mod, "run_dir": str(target)}

    finished = _finish_quietly(pilot)
    minutes = (time.monotonic() - started_at) / 60.0
    result = report_mod.build_report(
        scored, plan.mod, entries, minutes,
        pack=finished.get("pack") or started.get("pack") or pack,
        autosave_written=_autosave_flag(finished))
    json_path, md_path = report_mod.write_report(result, target)
    line = report_mod.notice_line(result)
    if callable(notice):
        notice(line)
    elif notice is None:
        _notify(line)
    return {"status": result["verdict"], **result, "run_dir": str(target),
            "report_json": str(json_path), "report_md": str(md_path),
            "source_save": save, "clone": scored}


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


def _newest_run(root: Path, mod: str) -> Path | None:
    runs = root / "runs"
    if not runs.is_dir():
        return None
    candidates = sorted((path for path in runs.iterdir()
                         if path.is_dir() and path.name.endswith(f"_{mod}")),
                        key=lambda path: path.name)
    return candidates[-1] if candidates else None


def capture(mod: str, segment: str, pass_at: str, fail_at: str, *, asset: str | None = None,
            yes: bool = False, frames: Sequence[str | Path] | None = None,
            save: str | None = None, run_dir: str | Path | None = None, driver: Any = None,
            data_dir: str | Path | None = None, clone: Callable | None = None,
            watcher: Callable | None = None, notice: Callable | None = None,
            sleep: Callable | None = None, progress: Callable | None = None) -> dict:
    """Cut one segment's two named moments into ``refs/`` after a human ``yes`` (plan 2.3/R3).

    Returns ``{status, asset, segment, pass_at, fail_at, pass_seconds, fail_seconds, frames,
    similarities, crossed, verdicts, margin, preview, paths, saved, refused}``. ``similarities``
    holds the two numbers ``check_frame`` measured on each frame; ``crossed`` holds the four
    direct ``assets.crop_similarity`` calls (each frame against each crop). ``status`` is ``ok``
    (saved or previewed), ``refused`` (the pair is undecidable, a moment is past the clip, ...)
    or ``stopped`` (the recording driver failed).
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
              else _inside(root / "captures" / f"{datetime.datetime.now():%Y-%m-%d_%H%M%S}_{plan.mod}",
                           root, "the capture directory"))

    paths: list[Path]
    if frames:
        paths = [Path(item) for item in frames if Path(item).is_file()]
        if len(paths) != len(list(frames)):
            missing = [str(item) for item in frames if not Path(item).is_file()]
            raise PlanError(f"frame(s) not found: {missing}")
    else:
        source = Path(run_dir) if run_dir else _newest_run(root, plan.mod)
        paths = _find_segment_frames(source, segment) if source else []
        if not paths and save:
            if not valid_tune_save(save):
                return _refused(f"only {jobs.TUNE_SAVE_PREFIX}* saves are checked, not {save!r}",
                                mod=plan.mod)
            if (watcher or jobs.watcher_running)():
                return _refused("pipeline.watch_save is running: stop it first", mod=plan.mod)
            maker = clone or default_clone
            try:
                scored = str(maker(save, CHK_PREFIX) or "").strip()
            except (OSError, ValueError) as exc:
                return _refused(f"could not clone {save}: {exc}", mod=plan.mod)
            live = driver or PilotDriver()
            try:
                live.start(scored, pack=None)
                if chosen.cued:
                    live.wait_for(chosen.cue, timeout_s=CUE_TIMEOUT_S)
                if chosen.preroll_s:
                    (sleep or time.sleep)(chosen.preroll_s)
                frames_dir = target / "segments" / segment / "frames"
                frames_dir.mkdir(parents=True, exist_ok=True)
                paths = _frame_paths(live.record(frames_dir, chosen.record_s, chosen.fps,
                                                 chosen.width), frames_dir)
            except CueMissed as exc:
                return {"status": "stopped", "stop": "cue_missed", "why": str(exc), "mod": plan.mod}
            except DriverError as exc:
                return {"status": "stopped", "stop": "driver", "why": str(exc), "mod": plan.mod}
            if not paths:
                return _refused(f"the driver recorded no frames for {segment}", mod=plan.mod)
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
                "paths": {}, "saved": False, "refused": refused}

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
                "why": "preview only: re-run with --yes to write the reference pair"}
    for key, path in destination.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(preview[key], path)
    return {"status": "ok", "mod": plan.mod, "segment": segment, "asset": name,
            "pass_at": pass_at, "fail_at": fail_at, "pass_seconds": pass_seconds,
            "fail_seconds": fail_seconds, "similarities": similarities, "crossed": crossed,
            "verdicts": verdicts, "margin": margin, "preview": preview,
            "paths": {key: str(path) for key, path in destination.items()},
            "saved": True, "refused": None}


# --------------------------------------------------------------------------- the real driver
def _last_json(text: str) -> dict:
    for line in reversed((text or "").splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        return value if isinstance(value, dict) else {}
    return {}


class PilotDriver:
    """The real driver. Needs unit U2's ``watch-game`` job merged for ``wait_for``.

    ``start`` remembers the clone and pack; each ``wait_for`` runs
    ``python3 -m tools.madden_pilot watch-game --save <clone> --to <cue> [--pack P]``; frames
    come from ``win_agent record`` and ``finish`` quits through the ``quit`` job and reports
    whether the autosave appeared (plan R1/D1).
    """

    def __init__(self, python: str | None = None, cwd: str | Path | None = None,
                 timeout_s: float = 900.0):
        self.python = python or os.environ.get("MU_PILOT_PYTHON") or "python3"
        self.cwd = Path(cwd) if cwd else Path(__file__).resolve().parents[2]
        self.timeout_s = timeout_s
        self.save: str | None = None
        self.pack: str | None = None
        self.saves = jobs.saves_dir()
        self._before: dict[str, float | None] = {}

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

    def wait_for(self, cue: str, timeout_s: float = CUE_TIMEOUT_S) -> None:
        if cue not in CUES:
            raise CueMissed(cue, f"allowed cues are {', '.join(CUES)}")
        if not self.save:
            raise CueMissed(cue, "start() was never called")
        args = ["watch-game", "--save", self.save, "--to", cue]
        if self.pack:
            args += ["--pack", self.pack]
        try:
            self._call(*args, timeout=max(self.timeout_s, timeout_s))
        except DriverError as exc:
            raise CueMissed(cue, str(exc)) from exc

    def record(self, out_dir: Path, seconds: int, fps: int, width: int) -> list[Path]:
        from tools.madden_pilot import win
        from tools.madden_pilot.navigate import TITLE
        folder = Path(out_dir)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            win.call(["record", "--title", TITLE, "--dir", str(folder),
                      "--seconds", str(int(seconds)), "--fps", str(int(fps)),
                      "--width", str(int(width))], timeout=max(60, int(seconds) + 30))
        except Exception as exc:                # WinError and anything else from the Windows side
            raise DriverError(f"could not record {seconds}s at {fps} fps: {exc}") from exc
        # win_agent reports a frame *count*; the files themselves are what the checks read.
        return _frame_paths(None, folder)

    def finish(self) -> dict:
        result: dict[str, Any] = {}
        if self.save:
            try:
                result = self._call("quit", "--franchise", jobs.tile_name(self.save))
            except DriverError as exc:
                result = {"quit": False, "why": str(exc)}
            autosave = self.saves / f"{self.save}-AUTOSAVE"
            after = _mtime(autosave)
            before = self._before.get("autosave")
            result["autosave_written"] = bool(after is not None and (before is None or after > before))
        result["pack"] = self.pack
        return result
