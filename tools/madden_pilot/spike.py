"""Guarded Madden spike agent (MADDEN_SPIKE_AGENT_PLAN sections 2.2-2.5 and 2.9).

A spike is one fresh session driving Madden past the catalog for a short, written brief. This
module holds the parts that must not be left to an agent's judgement:

* ``Brief`` / ``AllowConfirm`` and ``load_brief`` - the brief format and its validation.
* ``SpikeGuard`` - the code guard over a ``navigate.Pilot``: step and time caps, hard-stop words
  and screens (with the News Center ticker exception), the key and confirm allow-lists, the
  franchise rule and catalog routes. The state lives in ``data/spikes/<id>/state.json`` and is
  reloaded by every CLI call (each call is a new process).
* ``build_clip`` - the GIF and contact sheet built from a driver ``record`` burst.
* ``check_report`` / ``report_markdown`` - the report check and the requester's rendering.
* ``start`` / ``end`` / ``analyze`` - the pipeline around the guard, with the PC-touching
  functions injectable so the dry run and the tests never touch the PC.

The CLI in ``__main__.py`` only parses arguments, builds a Pilot (``ScriptedMadden`` for
``--dry-run``) and prints one JSON object per call.
"""
from __future__ import annotations

import datetime
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from tools.madden_pilot import jobs, vision
from tools.madden_pilot.navigate import Forbidden, PilotStop
from tools.madden_pilot.screens import UNKNOWN, border_highlight, label_match, load_catalog, norm

ROOT = Path(__file__).resolve().parents[2]

MAX_STEPS_CAP = 200                 # section 2.2/2.3
MAX_MINUTES_CAP = 45
MAX_KEY_TIMES = 10
MAX_RECORD_S = 30                   # the driver's own record caps (win_agent.MAX_RECORD_*)
MAX_RECORD_FPS = 10
TICKER_BAND = 0.08                  # the bottom 8% of the frame is the News Center ticker

ID_RE = re.compile(r"^[0-9]{8}-[0-9]{4}-[a-z0-9-]+$")
SAVE_RE = re.compile(r"^CAREER-TUNE-[A-Za-z0-9][A-Za-z0-9_\-]*$")
SCREEN_NAME_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*$")
REPORT_STATUSES = ("answered", "partial", "stopped")

HARD_STOP_WORDS = (
    "DELETE", "PERMANENTLY", "PURCHASE", "BUY", "STORE", "MADDEN POINTS", "ULTIMATE TEAM",
    "ONLINE", "SIGN IN", "SIGN OUT", "EA ACCOUNT", "PLACE ON INJURED RESERVE",
    "ACTIVATE FROM IR", "RELEASE PLAYER", "TRADE", "RE-SIGN PLAYER", "NEGOTIATE",
)

# Always allowed for spike-key (arrows, tab-switch and Back); Space and Enter never are.
ALWAYS_KEYS = frozenset({"up", "down", "left", "right", "z", "c", "backspace", "pgup", "pgdn"})

# Mirrors win_agent.KEYS without importing win_agent (it imports win32api, which WSL lacks).
# The names are validated again at load time against this set.
KNOWN_KEYS = frozenset(
    {"up", "down", "left", "right", "pgup", "pgdn", "space", "backspace", "enter", "esc", "tab",
     "lshift", "lctrl"}
    | set("abcdefghijklmnopqrstuvwxyz")
    | {str(d) for d in range(10)}
    | {f"f{n}" for n in range(1, 13)}
)


class BriefError(ValueError):
    """A brief that must not run; the message names the offending field."""

    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field


@dataclass(frozen=True)
class AllowConfirm:
    """One allow-listed label (allow_confirm) or key name (allow_keys)."""

    label: str
    on_words: tuple[str, ...] = ()
    on_kind: str | None = None


@dataclass(frozen=True)
class Brief:
    id: str
    from_: str
    goal: str
    questions: tuple[str, ...]
    pack: str
    save: str | None
    start: str
    allow_confirm: tuple[AllowConfirm, ...]
    allow_keys: tuple[AllowConfirm, ...]      # label = key name
    max_steps: int
    max_minutes: int
    record: bool
    launch: str
    checks: tuple[dict, ...]
    checkpoints: tuple[dict, ...]


# --------------------------------------------------------------------------- brief
def _text_has_hard_word(text) -> str | None:
    """The first HARD_STOP_WORDS entry present as whole words in `text`, else None."""
    body = f" {norm(text)} "
    for word in HARD_STOP_WORDS:
        if f" {norm(word)} " in body:
            return word
    return None


def _require_string(raw: dict, field: str, *, default=None) -> str:
    value = raw.get(field, default)
    if not isinstance(value, str) or not value.strip():
        raise BriefError(field, "must be a non-empty string")
    return value.strip()


def _require_int(raw: dict, field: str, low: int, high: int, *, default=None) -> int:
    value = raw.get(field, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise BriefError(field, f"must be an integer in {low}..{high}")
    if not low <= value <= high:
        raise BriefError(field, f"must be in {low}..{high}, not {value}")
    return value


def _parse_entries(value, field: str) -> tuple[AllowConfirm, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise BriefError(field, "must be a list")
    entries = []
    for index, item in enumerate(value):
        if isinstance(item, str):
            label, words, kind = item, (), None
        elif isinstance(item, dict):
            label, words, kind = item.get("label"), item.get("on_words"), item.get("on_kind")
        else:
            raise BriefError(field, f"entry {index} must be a label string or an object")
        if not isinstance(label, str) or not label.strip():
            raise BriefError(field, f"entry {index} has no label")
        if kind is not None and kind not in vision.SCREEN_KINDS:
            raise BriefError(field, f"entry {index} has an unknown on_kind {kind!r} "
                                    f"(known: {list(vision.SCREEN_KINDS)})")
        if words is None:
            words = ()
        if isinstance(words, str):
            words = (words,)
        if not isinstance(words, (list, tuple)) or any(not isinstance(w, str) for w in words):
            raise BriefError(field, f"entry {index} on_words must be a list of strings")
        entries.append(AllowConfirm(str(label).strip(),
                                    tuple(str(w).strip() for w in words if str(w).strip()),
                                    kind))
    return tuple(entries)


def _check_specs(value) -> tuple[dict, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise BriefError("checks", "must be a list")
    specs = []
    for index, spec in enumerate(value):
        if not isinstance(spec, dict):
            raise BriefError("checks", f"entry {index} must be an object")
        name = spec.get("name")
        if not isinstance(name, str) or not name.strip():
            raise BriefError("checks", f"entry {index} has no name")
        reference = spec.get("reference")
        if isinstance(reference, dict):
            for key in ("pass", "fail"):
                rel = reference.get(key)
                if rel:
                    target = Path(str(rel))
                    path = target if target.is_absolute() else ROOT / target
                    if not path.is_file():
                        raise BriefError("checks", f"{name}: reference {key} file is missing: {rel}")
        specs.append(spec)
    return tuple(specs)


def _checkpoints(value, check_names: set[str]) -> tuple[dict, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise BriefError("checkpoints", "must be a list")
    points = []
    for index, point in enumerate(value):
        if not isinstance(point, dict):
            raise BriefError("checkpoints", f"entry {index} must be an object")
        name = point.get("name")
        if not isinstance(name, str) or not name.strip():
            raise BriefError("checkpoints", f"entry {index} has no name")
        names = point.get("checks") or []
        if not isinstance(names, list) or any(not isinstance(n, str) for n in names):
            raise BriefError("checkpoints", f"{name}: checks must be a list of names")
        unknown = [n for n in names if n not in check_names]
        if unknown:
            raise BriefError("checkpoints", f"{name}: unknown check(s) {unknown}")
        if "record_s" in point:
            seconds = point["record_s"]
            if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= MAX_RECORD_S:
                raise BriefError("checkpoints", f"{name}: record_s must be 1..{MAX_RECORD_S}")
        points.append(point)
    return tuple(points)


def load_brief(path) -> Brief:
    """Read and validate one brief; raises ``BriefError`` naming the field (section 2.2)."""
    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except OSError as exc:
        raise BriefError("brief", f"cannot read {target}: {exc}") from exc
    except ValueError as exc:
        raise BriefError("brief", f"{target} is not JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise BriefError("brief", "must be a JSON object")
    catalog = load_catalog()

    ident = raw.get("id")
    if not isinstance(ident, str) or not ID_RE.fullmatch(ident):
        raise BriefError("id", "must match YYYYMMDD-HHMM-<slug> (^[0-9]{8}-[0-9]{4}-[a-z0-9-]+$)")

    save = raw.get("save")
    if save is not None:
        if not isinstance(save, str) or not SAVE_RE.fullmatch(save):
            raise BriefError("save", "must be null or CAREER-TUNE-<name>")

    start = raw.get("start")
    if not isinstance(start, str) or start not in catalog["screens"]:
        raise BriefError("start", f"must be a catalogued screen, not {start!r}")
    if catalog["screens"][start].get("no_route"):
        raise BriefError("start", f"{start} is marked no_route and cannot be a start screen")

    allow_confirm = _parse_entries(raw.get("allow_confirm"), "allow_confirm")
    for entry in allow_confirm:
        word = _text_has_hard_word(entry.label)
        if word:
            raise BriefError("allow_confirm", f"{entry.label!r} names the hard-stop word {word!r}")

    allow_keys = _parse_entries(raw.get("allow_keys"), "allow_keys")
    for entry in allow_keys:
        key = norm(entry.label).lower()
        if key not in KNOWN_KEYS:
            raise BriefError("allow_keys", f"{entry.label!r} is not a driver key")
        if key in ("q", "e") and not entry.on_words:
            raise BriefError("allow_keys", f"{entry.label!r} needs on_words (q/e are destructive "
                                           f"elsewhere)")

    max_steps = _require_int(raw, "max_steps", 1, MAX_STEPS_CAP)
    max_minutes = _require_int(raw, "max_minutes", 1, MAX_MINUTES_CAP)

    record = raw.get("record", False)
    if not isinstance(record, bool):
        raise BriefError("record", "must be true or false")
    launch = raw.get("launch", "pilot")
    if launch not in ("pilot", "jr"):
        raise BriefError("launch", 'must be "pilot" or "jr"')
    pack = raw.get("pack", jobs.DEFAULT_PACK)
    if not isinstance(pack, str) or not pack.strip():
        raise BriefError("pack", "must be a non-empty string")
    from_ = _require_string(raw, "from")
    goal = _require_string(raw, "goal")
    questions = raw.get("questions", [])
    if not isinstance(questions, list) or any(not isinstance(q, str) or not q.strip() for q in questions):
        raise BriefError("questions", "must be a list of non-empty strings")

    checks = _check_specs(raw.get("checks"))
    checkpoints = _checkpoints(raw.get("checkpoints"), {spec["name"] for spec in checks})

    return Brief(
        id=ident, from_=from_, goal=goal, questions=tuple(q.strip() for q in questions),
        pack=pack.strip(), save=save, start=start, allow_confirm=allow_confirm,
        allow_keys=allow_keys, max_steps=max_steps, max_minutes=max_minutes, record=record,
        launch=launch, checks=checks, checkpoints=checkpoints,
    )


# --------------------------------------------------------------------------- guard
class SpikeGuard:
    """The code guard over a Pilot (section 2.3). Every public action saves the state file.

    Rules are checked in the plan's order: caps, hard stops, keys, confirms, franchise, routes.
    A refusal never stops the run; a hard stop sets ``state["stopped"]`` and every later call is
    refused.
    """

    def __init__(self, brief: Brief, pilot, state_path, clock=time.monotonic, describe_fn=None):
        self.brief = brief
        self.pilot = pilot
        self.state_path = Path(state_path)
        self.clock = clock
        self.describe_fn = describe_fn or vision.describe
        self.description = None
        self.describe_error = None
        self.pilot.unknown_limit = brief.max_steps
        self.pilot.policy = {}
        self.state = self._read_state() or self._new_state()
        self._save()

    # ---------------------------------------------------------------- state
    def _read_state(self):
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def _new_state(self) -> dict:
        wall = datetime.datetime.now()
        now = self.clock()
        return {
            "steps": 0,
            "started": wall.isoformat(timespec="seconds"),
            "deadline": (wall + datetime.timedelta(minutes=self.brief.max_minutes)).isoformat(timespec="seconds"),
            "started_mono": now,
            "deadline_mono": now + self.brief.max_minutes * 60.0,
            "loaded_franchise": None,
            "last_screen": None,
            "stopped": None,
            "clips": [],
            "checkpoints": [],
        }

    def reset(self):
        """Start a fresh spike state (spike-start after a previous run has stopped)."""
        self.state = self._new_state()
        self.description = None
        self.describe_error = None
        self._save()

    def _save(self):
        self.state["last_screen"] = self.pilot.screen
        if self.pilot.loaded_franchise:
            self.state["loaded_franchise"] = self.pilot.loaded_franchise
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(json.dumps(self.state, indent=1, default=str), encoding="utf-8")

    @property
    def root(self) -> Path:
        return self.state_path.parent

    @property
    def steps(self) -> int:
        return int(self.state.get("steps") or 0)

    @property
    def steps_left(self) -> int:
        return max(0, self.brief.max_steps - self.steps)

    @property
    def minutes_left(self) -> float:
        deadline = float(self.state.get("deadline_mono") or 0.0)
        return max(0.0, (deadline - self.clock()) / 60.0)

    def _set_stopped(self, reason: str):
        if not self.state.get("stopped"):
            self.state["stopped"] = reason
        self._save()

    # ---------------------------------------------------------------- results
    def _highlight(self):
        frame = self.pilot.frame
        if frame is None:
            return None
        labels = list(frame.lines)
        reader = getattr(self.pilot, "highlight_reader", None)
        try:
            if reader:
                return reader(frame, labels, {})
            return border_highlight(frame, labels)
        except Exception:                       # a bad image must never hide the answer
            return None

    def _result(self, *, ok: bool, refused: str | None = None, **extra) -> dict:
        frame = self.pilot.frame
        data = {
            "ok": bool(ok),
            "step": self.steps,
            "steps_left": self.steps_left,
            "minutes_left": round(self.minutes_left, 2),
            "screen": self.pilot.screen,
            "lines": list(frame.lines) if frame is not None else [],
            "highlight": self._highlight(),
            "png": str(frame.png) if frame is not None and frame.png else None,
            "refused": refused,
            "stopped": self.state.get("stopped"),
        }
        data.update(extra)
        return data

    def _blocked(self):
        stopped = self.state.get("stopped")
        if stopped:
            return self._result(ok=False, refused=f"the spike is stopped ({stopped})")
        if self.steps >= self.brief.max_steps:
            self._set_stopped("cap")
            return self._result(ok=False, refused=f"step cap reached ({self.brief.max_steps})")
        if self.clock() >= float(self.state.get("deadline_mono") or 0.0):
            self._set_stopped("cap")
            return self._result(ok=False, refused=f"time cap reached ({self.brief.max_minutes} min)")
        return None

    def _pilot_stop(self, exc: PilotStop) -> dict:
        reason = str(exc.reason)
        if isinstance(exc, Forbidden) and reason.startswith("forbidden screen "):
            self._set_stopped(f"hard:{reason.rsplit(' ', 1)[-1]}")
        else:
            self._set_stopped(f"{exc.kind}: {reason}")
        return self._result(ok=False)

    # ---------------------------------------------------------------- hard stops
    @staticmethod
    def _frame_height(frame):
        if frame.size and len(frame.size) >= 2:
            try:
                return int(frame.size[1])
            except (TypeError, ValueError):
                return None
        if frame.png and Path(frame.png).is_file():
            try:
                from PIL import Image
                with Image.open(frame.png) as img:
                    return img.height
            except (OSError, ValueError):
                return None
        return None

    def _ticker_lines(self, frame) -> set:
        """Indexes of OCR lines inside the bottom TICKER_BAND of the frame."""
        height = self._frame_height(frame)
        if not height:
            return set()
        groups = {}
        for word in frame.words:
            groups.setdefault(word.get("line"), []).append(word)
        floor = height * (1.0 - TICKER_BAND)
        out = set()
        for index, _line in enumerate(frame.lines):
            words = groups.get(index)
            if not words:
                continue
            bottoms = [w["box"][1] + w["box"][3] for w in words if w.get("box")]
            if bottoms and max(bottoms) >= floor:
                out.add(index)
        return out

    def _hard_word(self, frame):
        ticker = self._ticker_lines(frame)
        for index, line in enumerate(frame.lines):
            if index in ticker:
                continue
            word = _text_has_hard_word(line)
            if word:
                return word
        return None

    def _back_out_and_look(self):
        try:
            self.pilot.press("back")
        except PilotStop:
            pass
        try:
            self.pilot.look()
        except PilotStop:
            pass

    def _hard_stop(self, reason: str, *, back: bool = True) -> dict:
        if back:
            self._back_out_and_look()
        self._set_stopped(reason)
        return self._result(ok=False)

    # ---------------------------------------------------------------- vision
    def describe(self):
        """Describe the current frame; a backend that is down is recorded, never fatal."""
        png = self.pilot.last_png or getattr(self.pilot.frame, "png", None)
        self.description = None
        self.describe_error = None
        try:
            self.description = self.describe_fn(png)
        except vision.VisionUnavailable as exc:
            self.describe_error = str(exc)
            return None
        except Exception as exc:                # noqa: BLE001 - a describe never stops the spike
            self.describe_error = f"{type(exc).__name__}: {exc}"
            return None
        warning = self.description.warning if self.description else None
        word = _text_has_hard_word(warning) if warning else None
        if word:
            return self._hard_stop(f"hard:{word}")
        return None

    # ---------------------------------------------------------------- look
    def look(self, describe: bool = False) -> dict:
        blocked = self._blocked()
        if blocked:
            return blocked
        return self._look_once(describe=describe)

    def _look_once(self, describe: bool = False) -> dict:
        try:
            frame, screen = self.pilot.look()
        except PilotStop as exc:
            return self._pilot_stop(exc)
        spec = self.pilot.spec(screen)
        # never_select screens that are safe to stand on (the hubs, where a spike starts and
        # returns) are not hard stops; the decision prompts (no_route) are. A literal reading
        # would stop a spike on HUB_WEEKLY, which the plan's own example brief starts on.
        if spec.get("forbidden") or (spec.get("never_select") and screen not in jobs.HUBS):
            return self._hard_stop(f"hard:{screen}")
        word = self._hard_word(frame)
        if word:
            return self._hard_stop(f"hard:{word}")
        if describe:
            stopped = self.describe()
            if stopped is not None:
                return stopped
        self._save()
        return self._result(ok=True)

    # ---------------------------------------------------------------- keys
    def key(self, name, times=1, hold=0.08, describe: bool = False) -> dict:
        blocked = self._blocked()
        if blocked:
            return blocked
        if isinstance(times, bool) or not isinstance(times, int) or not 1 <= times <= MAX_KEY_TIMES:
            return self._result(ok=False, refused=f"--times must be 1..{MAX_KEY_TIMES}")
        key = str(name or "").strip().lower()
        if key not in KNOWN_KEYS:
            return self._result(ok=False, refused=f"{name!r} is not a driver key")
        if key in ("space", "enter"):
            return self._result(ok=False, refused="Space and Enter are pressed only by spike-confirm")
        if self.pilot.screen is None:
            looked = self._look_once(describe=describe)
            if not looked["ok"]:
                return looked
        spec = self.pilot.spec()
        banned = {str(k).lower() for k in spec.get("forbidden_keys", [])}
        if key in banned:
            return self._result(ok=False, refused=f"{key!r} is forbidden on {self.pilot.screen}")
        if key not in ALWAYS_KEYS:
            entry = next((e for e in self.brief.allow_keys if norm(e.label).lower() == key), None)
            if entry is None:
                return self._result(ok=False, refused=f"{key!r} is not in the brief's allow_keys")
            if not entry.on_words and not entry.on_kind:
                return self._result(ok=False, refused=f"{key!r} is allowed only with on_words or "
                                                       f"on_kind in the brief")
            if entry.on_words and not all(self.pilot.frame.has(w) for w in entry.on_words):
                return self._result(ok=False, refused=f"{key!r} is allowed only on a frame holding "
                                                       f"{list(entry.on_words)}")
            if entry.on_kind:
                if self.description is None and self.describe_error is None:
                    stopped = self.describe()
                    if stopped is not None:
                        return stopped
                if not self.description or self.description.kind != entry.on_kind:
                    detail = self.describe_error or (self.description.kind if self.description else "no description")
                    return self._result(ok=False, refused=f"{key!r} is allowed only on "
                                                           f"{entry.on_kind!r} frames (got {detail})")
        if self.steps + times > self.brief.max_steps:
            self._set_stopped("cap")
            return self._result(ok=False, refused=f"the step cap ({self.brief.max_steps}) would be passed")
        try:
            self.pilot.press(key, times=times, hold=hold)
        except PilotStop as exc:
            return self._pilot_stop(exc)
        self.state["steps"] = self.steps + times
        if self.steps >= self.brief.max_steps:
            self._set_stopped("cap")
        return self._finish()

    # ---------------------------------------------------------------- confirms
    def _allow_entry(self, label):
        for entry in self.brief.allow_confirm:
            if label_match(entry.label, label):
                return entry
        return None

    def _entry_frame_ok(self, entry, frame) -> str | None:
        if entry.on_words and not all(frame.has(word) for word in entry.on_words):
            return f"{entry.label!r} is allowed only on a frame holding {list(entry.on_words)}"
        if entry.on_kind and (not self.description or self.description.kind != entry.on_kind):
            detail = self.describe_error or (self.description.kind if self.description else "no description")
            return f"{entry.label!r} is allowed only on {entry.on_kind!r} frames (got {detail})"
        return None

    def confirm(self, label, describe: bool = False) -> dict:
        blocked = self._blocked()
        if blocked:
            return blocked
        label = str(label or "").strip()
        if not label:
            return self._result(ok=False, refused="spike-confirm needs --label")
        if self.pilot.screen is None or describe:
            looked = self._look_once(describe=describe)
            if not looked["ok"]:
                return looked
        screen, frame = self.pilot.screen, self.pilot.frame
        entry = self._allow_entry(label)
        if entry is None:
            return self._result(ok=False, refused=f"{label!r} is not in the brief's allow_confirm")
        if entry.on_kind and self.description is None and self.describe_error is None:
            stopped = self.describe()
            if stopped is not None:
                return stopped
        reason = self._entry_frame_ok(entry, frame)
        if reason:
            return self._result(ok=False, refused=reason)
        if self.steps + 1 > self.brief.max_steps:
            self._set_stopped("cap")
            return self._result(ok=False, refused=f"the step cap ({self.brief.max_steps}) would be passed")

        if screen == UNKNOWN:
            if frame.find(label) is None:
                return self._result(ok=False, refused=f"{label!r} is not an OCR line on the frame")
            others = [line for line in frame.lines if norm(line) != norm(label)]
            border = border_highlight(frame, [label, *others])
            if self.description is not None and self.description.selected and border \
                    and norm(border) != norm(self.description.selected):
                return self._result(ok=False, refused="vision and border disagree")
            if border is None or not label_match(border, label):
                got = f"{border!r}" if border else "unreadable"
                return self._result(ok=False, refused=f"the selection border is not on {label!r} "
                                                       f"(read {got})")
            try:
                self.pilot.press("select", _confirm=True)
            except PilotStop as exc:
                return self._pilot_stop(exc)
        else:
            spec = self.pilot.spec(screen)
            if spec.get("never_select"):
                return self._result(ok=False, refused=f"Space is never pressed on {screen}")
            if screen == "FRANCHISE_LIST":
                expected = jobs.tile_name(self.brief.save) if self.brief.save else None
                if not expected or not label_match(expected, label):
                    return self._result(ok=False, refused=f"only the brief's franchise tile "
                                                           f"({expected or 'none'}) may be confirmed here")
            self.pilot.policy.setdefault(screen, [])
            if not any(label_match(label, allowed) for allowed in self.pilot.policy[screen]):
                self.pilot.policy[screen].append(label)
            try:
                self.pilot.confirm_on(screen, label)
            except PilotStop as exc:
                return self._pilot_stop(exc)
        self.state["steps"] = self.steps + 1
        if self.steps >= self.brief.max_steps:
            self._set_stopped("cap")
        return self._finish()

    # ---------------------------------------------------------------- goto
    def goto(self, target) -> dict:
        blocked = self._blocked()
        if blocked:
            return blocked
        target = str(target or "").strip()
        catalog = self.pilot.screens
        if target not in catalog:
            return self._result(ok=False, refused=f"{target!r} is not a catalogued screen")
        if catalog[target].get("no_route"):
            return self._result(ok=False, refused=f"{target} is marked no_route")
        if self.pilot.screen is None:
            looked = self._look_once()
            if not looked["ok"]:
                return looked
        path = self.pilot.plan(self.pilot.screen, target)
        if path is None:
            return self._result(ok=False, refused=f"no route from {self.pilot.screen} to {target}")
        hops = len(path)
        if self.steps + hops > self.brief.max_steps:
            self._set_stopped("cap")
            return self._result(ok=False, refused=f"the step cap ({self.brief.max_steps}) would be passed")
        try:
            self.pilot.goto(target)
        except PilotStop as exc:
            return self._pilot_stop(exc)
        self.state["steps"] = self.steps + hops
        if self.steps >= self.brief.max_steps:
            self._set_stopped("cap")
        return self._finish()

    # ---------------------------------------------------------------- record
    def record(self, seconds, fps, note=None, force=False) -> dict:
        blocked = self._blocked()
        if blocked:
            return blocked
        if not self.brief.record and not force:
            return self._result(ok=False, refused="the brief does not allow recording")
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= MAX_RECORD_S:
            return self._result(ok=False, refused=f"--seconds must be 1..{MAX_RECORD_S}")
        if isinstance(fps, bool) or not isinstance(fps, int) or not 1 <= fps <= MAX_RECORD_FPS:
            return self._result(ok=False, refused=f"--fps must be 1..{MAX_RECORD_FPS}")
        if self.steps + 1 > self.brief.max_steps:
            self._set_stopped("cap")
            return self._result(ok=False, refused=f"the step cap ({self.brief.max_steps}) would be passed")
        number = len(self.state.setdefault("clips", [])) + 1
        clip_dir = self.root / "clips" / f"{number:03d}"
        clip_dir.mkdir(parents=True, exist_ok=True)
        try:
            self.pilot._call(["record", "--title", self.pilot.title, "--dir", str(clip_dir),
                              "--seconds", str(seconds), "--fps", str(fps)])
        except PilotStop as exc:
            return self._pilot_stop(exc)
        gif, sheet = clip_dir / "clip.gif", clip_dir / "sheet.png"
        try:
            info = build_clip(clip_dir, gif, sheet, fps)
        except (OSError, ValueError) as exc:
            return self._result(ok=False, refused=f"could not build the clip: {exc}")
        self.state["clips"].append({
            "n": number, "dir": str(clip_dir.relative_to(self.root)), "gif": str(gif.relative_to(self.root)),
            "sheet": str(sheet.relative_to(self.root)), "seconds": seconds, "fps": fps,
            "note": note or "", "frames": info["frames"],
        })
        self.state["steps"] = self.steps + 1
        if self.steps >= self.brief.max_steps:
            self._set_stopped("cap")
        return self._finish(clip=info, clip_number=number)

    # ---------------------------------------------------------------- checks
    def check(self, checkpoint=None, checks=None) -> dict:
        blocked = self._blocked()
        if blocked:
            return blocked
        if not checkpoint and not checks:
            return self._result(ok=False, refused="give --check NAME or --checkpoint NAME")
        point = None
        if checkpoint:
            point = next((p for p in self.brief.checkpoints if p.get("name") == checkpoint), None)
            if point is None:
                return self._result(ok=False, refused=f"no checkpoint {checkpoint!r} in the brief")
            names = list(point.get("checks") or [])
        else:
            names = [str(name) for name in (checks or [])]
        specs = {spec["name"]: spec for spec in self.brief.checks}
        missing = [name for name in names if name not in specs]
        if missing:
            return self._result(ok=False, refused=f"unknown check(s) {missing}")
        looked = self._look_once()
        if not looked["ok"]:
            return looked
        png = self.pilot.last_png
        results = {name: run_check(png, specs[name]) for name in names}
        record = {"name": checkpoint or "checks", "step": self.steps, "png": png,
                  "at": datetime.datetime.now().isoformat(timespec="seconds"), "checks": results}
        if point is not None:
            self.state.setdefault("checkpoints", []).append(record)
            self._save()
        clip = None
        if point is not None and point.get("record_s"):
            clip = self.record(int(point["record_s"]), 5, note=f"checkpoint {point.get('name')}",
                               force=True)
        return self._finish(checks=results, checkpoint=record if point is not None else None,
                            clip=clip.get("clip") if isinstance(clip, dict) else None)

    # ---------------------------------------------------------------- finish
    def _finish(self, **extra) -> dict:
        looked = self._look_once()
        if not looked["ok"]:
            looked.update(extra)
            return looked
        return self._result(ok=True, **extra)


# --------------------------------------------------------------------------- clips
def _clip_frames(frames_dir):
    paths = sorted(p for p in Path(frames_dir).iterdir()
                   if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    return paths


def build_clip(frames_dir, out_gif, out_sheet, fps=5) -> dict:
    """Write an animated GIF (every frame, 480 px wide) and a labelled contact sheet.

    The sheet holds up to 24 evenly spaced frames in a 6-column grid, each labelled with its
    timestamp (frame index / fps). Returns counts and paths; raises ValueError with no frames.
    """
    from PIL import Image, ImageDraw

    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS")
    paths = _clip_frames(frames_dir)
    if not paths:
        raise ValueError(f"no frames in {frames_dir}")
    images = []
    for path in paths:
        with Image.open(path) as opened:
            image = opened.convert("RGB")
        if image.width != 480:
            image = image.resize((480, max(1, round(image.height * 480 / image.width))), resample)
        images.append(image)

    out_gif, out_sheet = Path(out_gif), Path(out_sheet)
    out_gif.parent.mkdir(parents=True, exist_ok=True)
    out_sheet.parent.mkdir(parents=True, exist_ok=True)
    duration = max(1, round(1000 / max(float(fps), 0.1)))
    palette = [image.convert("P", palette=Image.ADAPTIVE) for image in images]
    palette[0].save(out_gif, format="GIF", save_all=True, append_images=palette[1:],
                    duration=duration, loop=0, optimize=False)

    count = len(images)
    if count <= 24:
        picks = list(range(count))
    else:
        picks = sorted({round(i * (count - 1) / 23) for i in range(24)})
    thumb_w = 320
    thumb_h = max(1, round(images[0].height * thumb_w / images[0].width))
    label_h, pad, cols = 18, 6, 6
    cell_w, cell_h = thumb_w + 2 * pad, thumb_h + label_h + 2 * pad
    rows = max(1, (len(picks) + cols - 1) // cols)
    sheet = Image.new("RGB", (cols * cell_w, rows * cell_h), (24, 24, 28))
    draw = ImageDraw.Draw(sheet)
    for slot, index in enumerate(picks):
        x, y = (slot % cols) * cell_w, (slot // cols) * cell_h
        sheet.paste(images[index].resize((thumb_w, thumb_h), resample), (x + pad, y + pad))
        stamp = index / max(float(fps), 0.1)
        draw.text((x + pad, y + pad + thumb_h + 2), f"{index:04d}  {stamp:.2f}s", fill=(220, 220, 220))
    sheet.save(out_sheet, format="PNG")
    return {"gif": str(out_gif), "sheet": str(out_sheet), "frames": count,
            "sheet_frames": len(picks)}


# --------------------------------------------------------------------------- checks
def run_check(png, spec: dict) -> dict:
    """Run one asset-check spec on a saved frame (unknown when there is no frame)."""
    if not png or not Path(png).is_file():
        return {"status": "unknown", "measures": {}, "why": "no frame was captured to check"}
    from tools.madden_pilot.checks import asset_probe
    try:
        return asset_probe.run_frame(png, spec)
    except Exception as exc:                    # noqa: BLE001 - a bad frame is an unknown check
        return {"status": "unknown", "measures": {}, "why": f"check failed: {exc}"}


# --------------------------------------------------------------------------- report
def _report_path(run_dir: Path, rel: str):
    """Evidence resolves under the run dir, then under its parent (the spike root, where clips
    and the report live). A path that escapes either is rejected."""
    if not isinstance(rel, str) or not rel.strip():
        return None
    target = Path(rel)
    if target.is_absolute():
        return None
    for base in (Path(run_dir), Path(run_dir).parent):
        try:
            candidate = (base / target).resolve()
            candidate.relative_to(base.resolve())
        except (OSError, ValueError):
            continue
        if candidate.is_file():
            return candidate
    return None


def _state_for(run_dir: Path) -> dict:
    path = Path(run_dir).parent / "state.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def check_report(path, brief: Brief, run_dir) -> list[str]:
    """The report problems (empty means it passes), section 2.4."""
    problems: list[str] = []
    try:
        report = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        return [f"cannot read the report: {exc}"]
    except ValueError as exc:
        return [f"the report is not JSON: {exc}"]
    if not isinstance(report, dict):
        return ["the report must be a JSON object"]

    if report.get("id") != brief.id:
        problems.append(f"id is {report.get('id')!r}, expected {brief.id!r}")
    status = report.get("status")
    if status not in REPORT_STATUSES:
        problems.append(f"status must be one of {list(REPORT_STATUSES)}, not {status!r}")

    answers = report.get("answers")
    if not isinstance(answers, list):
        problems.append("answers must be a list")
        answers = []
    answered = set()
    for index, answer in enumerate(answers):
        if not isinstance(answer, dict):
            problems.append(f"answers[{index}] must be an object")
            continue
        question = answer.get("question")
        if question not in brief.questions:
            problems.append(f"answers[{index}] answers a question not in the brief: {question!r}")
        elif question in answered:
            problems.append(f"two answers for the question {question!r}")
        answered.add(question)
        if not isinstance(answer.get("answer"), str) or not answer.get("answer", "").strip():
            problems.append(f"answers[{index}] has no answer text (use 'not found')")
        evidence = answer.get("evidence", [])
        if not isinstance(evidence, list):
            problems.append(f"answers[{index}].evidence must be a list")
            evidence = []
        for rel in evidence:
            if _report_path(run_dir, rel) is None:
                problems.append(f"evidence does not exist under the run dir: {rel!r}")
    for question in brief.questions:
        if question not in answered:
            problems.append(f"missing an answer for the question {question!r}")

    catalog = load_catalog()
    proposed = report.get("proposed_screens", [])
    if not isinstance(proposed, list):
        problems.append("proposed_screens must be a list")
        proposed = []
    seen_names = set()
    for index, screen in enumerate(proposed):
        if not isinstance(screen, dict):
            problems.append(f"proposed_screens[{index}] must be an object")
            continue
        name = screen.get("name")
        if not isinstance(name, str) or not SCREEN_NAME_RE.fullmatch(name):
            problems.append(f"proposed screen {name!r} is not upper snake case")
        elif name in catalog["screens"]:
            problems.append(f"proposed screen {name} is already in the catalog")
        elif name in seen_names:
            problems.append(f"proposed screen {name} appears twice")
        seen_names.add(name)
        if screen.get("seen") and _report_path(run_dir, screen["seen"]) is None:
            problems.append(f"proposed screen {name!r} seen path does not exist: {screen['seen']!r}")

    state = _state_for(run_dir)
    stopped = state.get("stopped")
    hard_or_cap = isinstance(stopped, str) and (stopped.startswith("hard:") or stopped == "cap")
    if hard_or_cap and status != "stopped":
        problems.append(f"the run stopped ({stopped}) but the report status is {status!r}, not 'stopped'")

    return problems


def report_markdown(report: dict, brief: Brief, run_dir) -> str:
    """The plain rendering of a report for the requesting agent (section 2.4)."""
    run_dir = Path(run_dir)

    def link(rel):
        if not isinstance(rel, str) or not rel:
            return ""
        return f"[{rel}]({rel})"

    lines = [f"# Spike {report.get('id') or brief.id}: {brief.goal}", ""]
    lines.append(f"Status: **{report.get('status')}**")
    lines.append(f"From: {brief.from_}")
    lines.append(f"Pack: {brief.pack}  Save: {brief.save or '(none)'}")
    lines.append("")
    lines.append("## Answers")
    for answer in report.get("answers", []) or []:
        lines.append("")
        lines.append(f"### {answer.get('question')}")
        lines.append("")
        lines.append(str(answer.get("answer") or ""))
        evidence = answer.get("evidence") or []
        if evidence:
            lines.append("")
            lines.append("Evidence: " + ", ".join(link(rel) for rel in evidence))
    lines.append("")
    lines.append("## Route")
    for hop in report.get("route", []) or []:
        keys = hop.get("keys") or []
        rendered = []
        for key in keys:
            if isinstance(key, (list, tuple)):
                rendered.append(" ".join(str(part) for part in key))
            else:
                rendered.append(str(key))
        lines.append(f"- {hop.get('from')} -> {', '.join(rendered) or '(none)'} -> {hop.get('to')}")
    lines.append("")
    lines.append("## Proposed screens")
    proposed = report.get("proposed_screens") or []
    if not proposed:
        lines.append("(none)")
    for screen in proposed:
        lines.append("")
        lines.append(f"### {screen.get('name')}")
        for field in ("all", "any", "none", "options"):
            if screen.get(field):
                lines.append(f"- {field}: {screen[field]}")
        if screen.get("seen"):
            lines.append(f"- seen: {link(screen['seen'])}")
        if screen.get("note"):
            lines.append(f"- note: {screen['note']}")
    lines.append("")
    lines.append("## Stops")
    stops = report.get("stops") or []
    lines.extend(f"- {stop}" for stop in stops) if stops else lines.append("(none)")
    if report.get("notes"):
        lines.append("")
        lines.append("## Notes")
        lines.append(str(report["notes"]))
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- start / end
def state_refusal(brief: Brief, state_path) -> str | None:
    """The state-file refusal: a spike with no stopped reason is still running."""
    path = Path(state_path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and data and not data.get("stopped"):
        return ("a spike is already running: state.json has no stopped reason (finish it with "
                "spike-end or remove the state file)")
    return None


def _bare_result(*, ok: bool, refused: str | None = None, **extra) -> dict:
    """A section 2.5 result before a guard exists (spike-start refusals)."""
    data = {"ok": bool(ok), "step": 0, "steps_left": 0, "minutes_left": 0.0, "screen": None,
            "lines": [], "highlight": None, "png": None, "refused": refused, "stopped": None}
    data.update(extra)
    return data


def start(brief: Brief, pilot, state_path, *, dry_run=False, watcher=None, saves_folder=None,
          describe_fn=None, clock=time.monotonic, launch=None, wait_window=None, wait_menu=None,
          load=None, goto=None):
    """spike-start: refuse the unsafe starts, launch (or wait for) Madden, load the save, then
    route to the brief's start. Returns ``(guard, result)``; the guard is None on a refusal, so
    no state file is left behind for a start that never happened."""
    reason = state_refusal(brief, state_path)
    if reason:
        return None, _bare_result(ok=False, refused=reason)
    if watcher is None:
        watcher = jobs.watcher_running
    try:
        running = bool(watcher())
    except Exception:                       # noqa: BLE001 - an unavailable check is not a yes
        running = False
    if running and not dry_run:
        return None, _bare_result(ok=False, refused="pipeline.watch_save is running: stop it before "
                                                    "a spike loads a franchise clone")
    if brief.save and not dry_run:
        folder = Path(saves_folder) if saves_folder else jobs.saves_dir()
        if not (folder / brief.save).is_file():
            return None, _bare_result(ok=False, refused=f"no save {brief.save} in {folder}")

    guard = SpikeGuard(brief, pilot, state_path, clock=clock, describe_fn=describe_fn)
    guard.reset()
    looked = guard.look()
    if not looked["ok"]:
        return guard, looked
    tile = jobs.tile_name(brief.save) if brief.save else None
    if pilot.screen in jobs.HUBS and not (tile and pilot.loaded_franchise
                                          and norm(pilot.loaded_franchise) == norm(tile)):
        result = guard._result(ok=False, refused="a franchise is open and is not the brief's "
                                                 "TUNE-* save (the hub doesn't show the name); "
                                                 "quit it first")
        try:
            guard.state_path.unlink()       # a start that never happened leaves no running state
        except OSError:
            pass
        return None, result
    try:
        if not dry_run:
            if brief.launch == "jr":
                (wait_window or (lambda: jobs._wait_window(pilot, 600)))()
                (wait_menu or (lambda: jobs.wait_menu(pilot, 600)))()
            else:
                (launch or (lambda: jobs.launch(pilot, pack=brief.pack)))()
        if brief.save:
            (load or (lambda: jobs._load_franchise(pilot, tile, save=brief.save)))()
        (goto or (lambda: pilot.goto(brief.start)))()
    except PilotStop as exc:
        return guard, guard._pilot_stop(exc)
    guard.state["loaded_franchise"] = pilot.loaded_franchise
    guard.description = None
    guard.describe_error = None
    looked = guard.look()
    if not looked["ok"]:
        return guard, looked
    guard._save()
    return guard, guard._result(ok=True)


def end(brief: Brief, guard: SpikeGuard, report_path, run_dir, *, quit_to_list=None,
        leave=None) -> dict:
    """spike-end: check the report, back out to a safe screen, write report.md and stop."""
    report_path = Path(report_path)
    problems = check_report(report_path, brief, run_dir)
    if problems:
        return guard._result(ok=False, refused="the report has problems: " + "; ".join(problems),
                             report_problems=problems)
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return guard._result(ok=False, refused=f"cannot read the report: {exc}")
    loaded = guard.state.get("loaded_franchise") or guard.pilot.loaded_franchise
    try:
        if loaded and str(loaded).upper().startswith("TUNE-"):
            (quit_to_list or (lambda: jobs._quit_now(guard.pilot, franchise=loaded,
                                                     allow_quit=False)))()
        else:
            (leave or jobs._leave_to_main_menu)(guard.pilot)
    except PilotStop as exc:
        return guard._pilot_stop(exc)
    markdown = report_markdown(report, brief, run_dir)
    markdown_path = report_path.with_suffix(".md")
    markdown_path.write_text(markdown, encoding="utf-8")
    try:
        guard.pilot.look()                  # every action job ends with a look (section 2.5)
    except PilotStop:
        pass
    guard.state["stopped"] = "ended"
    guard._save()
    return guard._result(ok=True, report=str(report_path), report_md=str(markdown_path),
                         status=report.get("status"))


# --------------------------------------------------------------------------- analyze
def madden_running() -> bool:
    from pipeline.gpu_queue import process_check
    return process_check.is_madden_running()


def analyze(root, brief: Brief, run_dir, *, describe_fn=None, running=None) -> dict:
    """spike-analyze: the heavy pass after Madden closes (section 2.9)."""
    checker = running or madden_running
    if checker():
        return {"ok": False, "refused": "Madden27.exe is running: close it before spike-analyze "
                                        "(the heavy model and the checks want the full GPU)"}
    root, run_dir = Path(root), Path(run_dir)
    describe_fn = describe_fn or (lambda png: vision.describe(png, backend=vision.heavy_backend()))
    step_pngs = sorted((run_dir / "steps").glob("*.png")) if (run_dir / "steps").is_dir() else []
    state = _state_for(run_dir)
    checkpoint_pngs = []
    for point in state.get("checkpoints") or []:
        png = point.get("png") if isinstance(point, dict) else None
        if png and Path(png).is_file():
            checkpoint_pngs.append(Path(png))
    descriptions = []
    seen = set()
    for png in [*step_pngs, *checkpoint_pngs]:
        if png in seen:
            continue
        seen.add(png)
        try:
            described = describe_fn(png)
        except vision.VisionUnavailable as exc:
            descriptions.append({"png": str(png), "vision": None, "error": str(exc)})
            continue
        except Exception as exc:            # noqa: BLE001
            descriptions.append({"png": str(png), "vision": None, "error": f"{type(exc).__name__}: {exc}"})
            continue
        descriptions.append({"png": str(png), "vision": {
            "kind": described.kind, "title": described.title, "options": list(described.options),
            "selected": described.selected, "overlays": list(described.overlays),
            "warning": described.warning, "summary": described.summary,
            "model": described.model, "seconds": described.seconds,
        }})
    check_frames = checkpoint_pngs or (step_pngs[-1:] if step_pngs else [])
    checks = {}
    for spec in brief.checks:
        results = {str(png): run_check(png, spec) for png in check_frames}
        checks[spec["name"]] = results
    analysis = {"id": brief.id, "at": datetime.datetime.now().isoformat(timespec="seconds"),
                "frames": len(descriptions), "steps": descriptions, "checks": checks}
    root.mkdir(parents=True, exist_ok=True)
    (root / "analysis.json").write_text(json.dumps(analysis, indent=1, default=str), encoding="utf-8")
    markdown_path = root / "report.md"
    if markdown_path.is_file():
        text = markdown_path.read_text(encoding="utf-8")
        marker = "## After-run analysis"
        head = text.split(marker)[0].rstrip()
        lines = [head, "", marker, "",
                 f"Analysed {len(descriptions)} frame(s) at {analysis['at']}."]
        for name, results in checks.items():
            for png, result in results.items():
                lines.append(f"- {name} on `{Path(png).name}`: {result.get('status')} "
                             f"({result.get('why')})")
        markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"ok": True, "analysis": str(root / "analysis.json"), "frames": len(descriptions),
            "checks": checks}
