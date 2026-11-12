"""Segment plans and check maps for a visual mod check (VISUAL_MOD_CHECK_PLAN section 2.2/2.3).

A mod lives in ``data/mod_checks/<slug>/``:

``segments.json``
    ``{"segments": [{"name": "game_start", "after": "kickoff", "record_s": 8, "fps": 10,
    "width": 1280, "checks": ["fox_scorebug"]}, ...]}``

    An optional top-level ``"mod"`` gives the report a display name; without it the directory
    name is used. Each segment's keys are ``name``, ``after``, ``cue``, ``record_s``, ``fps``,
    ``width``, ``checks`` and ``preroll_s``; an unknown key is a refusal, so a typo cannot be
    ignored.

    ``after`` is a cue from the fixed vocabulary the runner knows how to see on screen
    (``kickoff``, ``halftime_start``, ``supersim_menu``); anything else is refused rather than
    guessed. A segment may omit ``after`` only for the static ``supersim_menu`` screen, and then
    it must say so with ``"cue": "supersim_menu"``: a fixed wait is allowed there, nowhere else,
    so a timing-based check cannot silently score the wrong moment. ``preroll_s`` (plan R2) is an
    optional fixed wait, ``0``..``10`` seconds, before the recording starts; the report carries it
    so a reader knows a segment was timed rather than cued.

``checks.json``
    One spec per asset in ``tools.madden_pilot.assets.check_frame``'s own shape. ``reference``
    paths are relative to the mod's directory and are resolved here, so ``check_frame`` (which
    resolves relative paths against the repository root) receives absolute ones.

Every refusal raises ``PlanError`` with a message naming the file, the segment or check, and
what was expected. The CLI prints it and exits 2 (the pilot's unknown/refused exit code).

Nothing here measures pixels: the verdicts come from ``tools.madden_pilot.assets.check_frame``
and this module only says what to score.
"""
from __future__ import annotations

import ast
import copy
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

# The driver's record caps come from win_agent.py, which runs under Windows Python and needs
# pywin32, so WSL code cannot import it. They are read from the module's own source here rather
# than copied, so there is still one place that decides them (plan section 2.2).
CAPS_FILE = Path(__file__).resolve().parents[1] / "madden_pilot" / "win_agent.py"
CAP_NAMES = ("SMALL_WIDTH", "MAX_RECORD_S", "MAX_RECORD_FPS")


def _driver_caps() -> tuple[int, int, int]:
    try:
        tree = ast.parse(CAPS_FILE.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RuntimeError(f"cannot read the driver's record caps from {CAPS_FILE}: {exc}") from exc
    found: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in CAP_NAMES:
                    try:
                        found[target.id] = int(ast.literal_eval(node.value))
                    except (ValueError, TypeError):
                        raise RuntimeError(f"{CAPS_FILE}: {target.id} is no longer a plain "
                                           f"number") from None
    missing = [name for name in CAP_NAMES if name not in found]
    if missing:
        raise RuntimeError(f"{CAPS_FILE} no longer defines {missing}; the mod check needs the "
                           f"driver's own record caps")
    return found["SMALL_WIDTH"], found["MAX_RECORD_S"], found["MAX_RECORD_FPS"]


SMALL_WIDTH, MAX_RECORD_S, MAX_RECORD_FPS = _driver_caps()

# The cues the runner (and U2's watch-game job) know how to wait for. An unknown cue is a
# refusal, never a guess (plan section 2.2).
CUES = ("kickoff", "halftime_start", "supersim_menu")
# The one static screen a segment may wait a fixed time on instead of a cue.
STATIC_CUE = "supersim_menu"

DEFAULT_FPS = 5
MIN_WIDTH = 320
MAX_PREROLL_S = 10.0
SEGMENT_KEYS = ("name", "after", "cue", "record_s", "fps", "width", "checks", "preroll_s")

ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT / "data" / "mod_checks"
ENV_DATA_DIR = "MU_MOD_CHECKS_DIR"
SEGMENTS_FILE = "segments.json"
CHECKS_FILE = "checks.json"


class PlanError(ValueError):
    """A segment plan or check map that cannot be scored: the message says what and why."""


@dataclass(frozen=True)
class Segment:
    """One recorded stretch of a game: the cue it follows and what to score on it."""

    name: str
    cue: str | None
    record_s: int
    fps: int
    width: int
    checks: tuple[str, ...]
    preroll_s: float = 0.0
    cued: bool = True          # False for a fixed wait on the static supersim_menu screen

    def as_dict(self) -> dict:
        """The plan fields, for ``--dry-run`` output and logs."""
        return {"name": self.name, "cue": self.cue, "after": self.cue if self.cued else None,
                "record_s": self.record_s, "fps": self.fps, "width": self.width,
                "checks": list(self.checks), "preroll_s": self.preroll_s}


@dataclass(frozen=True)
class Plan:
    """A validated ``segments.json`` plus the ``checks.json`` its segments refer to."""

    mod: str
    mod_dir: Path
    segments: tuple[Segment, ...]
    checks: Mapping[str, dict] = field(default_factory=dict)

    def names(self) -> list[str]:
        return [segment.name for segment in self.segments]

    def segment(self, name: str) -> Segment:
        for segment in self.segments:
            if segment.name == name:
                return segment
        raise PlanError(f"{self.mod}: no segment named {name!r} (has: {', '.join(self.names()) or 'none'})")

    def select(self, names: Iterable[str] | str | None = None) -> list[Segment]:
        """The requested segments in plan order; ``None`` or empty means all of them."""
        if isinstance(names, str):
            names = names.split(",")
        wanted = [str(name).strip() for name in (names or []) if str(name).strip()]
        if not wanted:
            return list(self.segments)
        missing = [name for name in wanted if name not in self.names()]
        if missing:
            raise PlanError(f"{self.mod}: --segments names unknown segment(s) {missing} "
                            f"(has: {', '.join(self.names())})")
        chosen = set(wanted)
        return [segment for segment in self.segments if segment.name in chosen]


def data_root(data_dir: str | Path | None = None) -> Path:
    """The git-ignored root: ``--data-dir``, else ``MU_MOD_CHECKS_DIR``, else ``data/mod_checks``."""
    return Path(data_dir or os.environ.get(ENV_DATA_DIR) or DATA_ROOT)


def mod_directory(mod: str, data_dir: str | Path | None = None) -> Path:
    """``<data root>/<slug>``. A slug is one path component; ``..`` is a refusal."""
    text = str(mod or "").strip()
    if not text or Path(text).name != text or text in (".", ".."):
        raise PlanError(f"mod must be one slug (a directory name), not {mod!r}")
    return data_root(data_dir) / text


# --------------------------------------------------------------------------- reading
def _read_json(path: Path, what: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise PlanError(f"no {what} at {path}") from None
    except OSError as exc:
        raise PlanError(f"cannot read {what} at {path}: {exc}") from exc
    except ValueError as exc:
        raise PlanError(f"{path} is not JSON: {exc}") from exc


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool))


def _resolve_reference(value: Any, key: str, mod_dir: Path, check: str, require: bool) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PlanError(f"checks.json: {check}: reference {key} must be a file path, not {value!r}")
    target = Path(value)
    path = target if target.is_absolute() else (mod_dir / target)
    if require and not path.is_file():
        raise PlanError(f"checks.json: {check}: reference {key} file is missing: {value} "
                        f"(looked for {path})")
    return str(path)


def load_checks(mod_dir: str | Path, *, require_refs: bool = True) -> dict[str, dict]:
    """Read and validate ``checks.json``; reference paths become absolute under ``mod_dir``.

    ``require_refs=True`` (the run) refuses a missing ``reference.pass`` / ``reference.fail``
    file. ``capture`` passes ``False`` because it is creating that pair.
    """
    folder = Path(mod_dir)
    path = folder / CHECKS_FILE
    raw = _read_json(path, "check map")
    if not isinstance(raw, dict) or not raw:
        raise PlanError(f"{path} must hold one check spec per asset (a non-empty JSON object)")
    checks: dict[str, dict] = {}
    for name, spec in raw.items():
        if not isinstance(name, str) or not name.strip():
            raise PlanError(f"{path}: every check needs a name (a non-empty string key)")
        if not isinstance(spec, dict):
            raise PlanError(f"checks.json: {name}: the spec must be an object, not {type(spec).__name__}")
        if not spec.get("colour") and not spec.get("reference"):
            raise PlanError(f"checks.json: {name}: the spec has no measures "
                            f"(it needs colour, reference, or both)")
        if "region" in spec:
            region = spec["region"]
            if (not isinstance(region, (list, tuple)) or len(region) != 4
                    or any(not _number(value) for value in region)):
                raise PlanError(f"checks.json: {name}: region must be [x, y, w, h] as fractions, "
                                f"not {region!r}")
        copy_spec = copy.deepcopy(spec)
        reference = copy_spec.get("reference")
        if reference is not None:
            if not isinstance(reference, dict):
                raise PlanError(f"checks.json: {name}: reference must be an object")
            for key in ("pass", "fail"):
                if key not in reference:
                    raise PlanError(f"checks.json: {name}: reference needs both pass and fail "
                                    f"(missing {key!r})")
                reference[key] = _resolve_reference(reference[key], key, folder, name, require_refs)
            if "margin" in reference and (not _number(reference["margin"]) or reference["margin"] < 0):
                raise PlanError(f"checks.json: {name}: reference margin must be a number >= 0, "
                                f"not {reference['margin']!r}")
        checks[name] = copy_spec
    return checks


def _parse_segment(raw: Any, index: int, path: Path, check_names: set[str]) -> Segment:
    if not isinstance(raw, dict):
        raise PlanError(f"{path}: segment {index} must be an object, not {type(raw).__name__}")
    unknown = sorted(set(raw) - set(SEGMENT_KEYS))
    if unknown:
        raise PlanError(f"{path}: segment {index} has unknown field(s) {unknown} "
                        f"(allowed: {', '.join(SEGMENT_KEYS)})")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        raise PlanError(f"{path}: segment {index} has no name")
    name = name.strip()
    if Path(name).name != name or name in (".", ".."):
        raise PlanError(f"{path}: segment name {name!r} must be one plain name "
                        f"(it becomes a folder under the run directory)")
    where = f"segment {name}"

    cue = raw.get("cue")
    after = raw.get("after")
    if after is not None and after not in CUES:
        raise PlanError(f"{path}: {where}: after must be one of {', '.join(CUES)}, not {after!r}")
    if cue is not None and cue not in CUES:
        raise PlanError(f"{path}: {where}: cue must be one of {', '.join(CUES)}, not {cue!r}")
    if after is not None and cue is not None and after != cue:
        raise PlanError(f"{path}: {where}: after {after!r} and cue {cue!r} disagree; "
                        f"a segment waits for one cue")
    cued = after is not None
    if not cued:
        if cue != STATIC_CUE:
            raise PlanError(
                f"{path}: {where}: no after cue. Only the static {STATIC_CUE} screen may be a "
                f"fixed wait (write \"cue\": \"{STATIC_CUE}\"); every other segment must name "
                f"one of {', '.join(CUES)} so it cannot score the wrong moment")
        # A static screen is reached, not cued: the run waits a fixed time on it.
    selected_cue = after if cued else cue

    record_s = raw.get("record_s")
    if not _is_int(record_s) or not 1 <= record_s <= MAX_RECORD_S:
        raise PlanError(f"{path}: {where}: record_s must be an int 1..{MAX_RECORD_S}, not {record_s!r}")
    fps = raw.get("fps", DEFAULT_FPS)
    if not _is_int(fps) or not 1 <= fps <= MAX_RECORD_FPS:
        raise PlanError(f"{path}: {where}: fps must be an int 1..{MAX_RECORD_FPS}, not {fps!r}")
    width = raw.get("width", SMALL_WIDTH)
    if not _is_int(width) or width < MIN_WIDTH:
        raise PlanError(f"{path}: {where}: width must be an int >= {MIN_WIDTH} px, not {width!r}")
    preroll = raw.get("preroll_s", 0.0)
    if not _number(preroll) or not 0 <= float(preroll) <= MAX_PREROLL_S:
        raise PlanError(f"{path}: {where}: preroll_s must be a number 0..{MAX_PREROLL_S:g}, "
                        f"not {preroll!r}")

    checks = raw.get("checks", [])
    if not isinstance(checks, list) or any(not isinstance(item, str) for item in checks):
        raise PlanError(f"{path}: {where}: checks must be a list of asset names")
    missing = [item for item in checks if item not in check_names]
    if missing:
        raise PlanError(f"{path}: {where}: unknown check(s) {missing} "
                        f"(checks.json has: {', '.join(sorted(check_names)) or 'none'})")

    return Segment(name=name, cue=selected_cue, record_s=int(record_s), fps=int(fps),
                   width=int(width), checks=tuple(checks), preroll_s=float(preroll), cued=cued)


def load_plan(mod_dir: str | Path, *, require_refs: bool = True) -> Plan:
    """Read and validate ``segments.json`` (and the ``checks.json`` it names).

    ``require_refs=False`` is for ``capture``, which is about to write the reference pair.
    """
    folder = Path(mod_dir)
    path = folder / SEGMENTS_FILE
    raw = _read_json(path, "segment plan")
    if not isinstance(raw, dict) or not isinstance(raw.get("segments"), list):
        raise PlanError(f"{path} must be an object with a \"segments\" list")
    entries = raw["segments"]
    if not entries:
        raise PlanError(f"{path}: \"segments\" is empty; a run needs at least one segment")
    checks = load_checks(folder, require_refs=require_refs)
    seen: set[str] = set()
    segments: list[Segment] = []
    for index, entry in enumerate(entries):
        segment = _parse_segment(entry, index, path, set(checks))
        if segment.name in seen:
            raise PlanError(f"{path}: segment {segment.name!r} appears twice")
        seen.add(segment.name)
        segments.append(segment)
    display = raw.get("mod") or folder.name
    if not isinstance(display, str) or Path(display).name != display or display in (".", ".."):
        raise PlanError(f"{path}: mod must be a plain display name or omitted, not {raw.get('mod')!r}")
    return Plan(mod=display, mod_dir=folder, segments=tuple(segments), checks=checks)
