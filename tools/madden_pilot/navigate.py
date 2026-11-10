"""The pilot: look, press, wait and move between Madden screens (plan section 2.2).

    pilot = Pilot(load_catalog(), run_dir)        # driver defaults to tools.madden_pilot.win
    pilot.goto("HUB_GAMEDAY")
    pilot.confirm_on("HUB_GAMEDAY", "ADVANCE WEEK")

The driver is any object with `call(cmd: list[str]) -> dict` speaking win_agent's commands
(`look --title T --out PNG --ocr`, `key --name K --hold S --times N`). Every look is saved as
run_dir/steps/NNNN_<screen>.png (when the driver wrote it) and .json.

Safety rules:
- Space (keys.select) is only pressed by confirm_on, and only for a label in that screen's
  `confirm_ok` (or a policy given for it), with the highlight on that label when it can be read.
- `forbidden_keys` are never pressed on their screen; a `forbidden` screen stops the run on sight.
- Quitting a franchise (a screen's `quit_guard` label) writes <save>-AUTOSAVE, so it is refused
  unless the loaded franchise starts with TUNE- or allow_quit=True is passed.
Stops (Stuck, Unknown, Forbidden) write run_dir/stop.json and send an ntfy notice first.
"""
import datetime
import json
import os
import shutil
import sys
import time
from collections import deque
from pathlib import Path

from tools.madden_pilot.screens import UNKNOWN, Frame, border_highlight, classify, label_match, norm

ROOT = Path(__file__).resolve().parents[2]
TITLE = "Madden NFL 27"
TUNE_PREFIX = "TUNE-"


class PilotStop(Exception):
    """The run stopped; `info` is what was written to stop.json."""
    kind = "Stop"

    def __init__(self, reason, info=None):
        super().__init__(reason)
        self.reason = reason
        self.info = info or {}


class Stuck(PilotStop):
    kind = "Stuck"


class Unknown(PilotStop):
    kind = "Unknown"


class Forbidden(PilotStop):
    kind = "Forbidden"


def send_notice(message, title="Madden pilot"):
    """ntfy through pipeline.notify (as tools/commit_audit.py does); never raises."""
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from pipeline.notify import notify
        notify(message, title=title, priority="high", tags=("warning",))
    except Exception as e:  # a notice never hides the stop itself
        print(f"[pilot] notice not sent: {e}", flush=True)


class Pilot:
    def __init__(self, catalog, run_dir, driver=None, *, title=TITLE, policy=None, unknown_limit=20,
                 stuck_limit=3, highlight_reader=None, sleep=time.sleep, clock=time.monotonic):
        """policy: {screen: [labels]} extra confirm_ok labels; naming a `forbidden_confirm`
        screen here allows exactly those labels on it. highlight_reader(frame, labels, spec)
        overrides the screenshot reader for 'border' screens (tests)."""
        if driver is None:
            from tools.madden_pilot import win as driver
        self.catalog = catalog
        self.screens = catalog["screens"]
        self.keys = catalog.get("keys", {})
        self.run_dir = Path(run_dir)
        self.driver = driver
        self.title = title
        self.policy = policy or {}
        self.unknown_limit = unknown_limit
        self.stuck_limit = stuck_limit
        self.highlight_reader = highlight_reader
        self.sleep = sleep
        self.clock = clock
        self.step = 0
        self.frame = None
        self.screen = None
        self.last_known = None
        self.last_png = None
        self.unknown_run = 0
        self.cursor = None                 # tracked option index on 'ocr_order' screens
        self.loaded_franchise = None
        self.allow_quit = False
        (self.run_dir / "steps").mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ basics
    def spec(self, screen=None):
        return self.screens.get(screen or self.screen) or {}

    def key_name(self, key):
        """A logical key (select, back, tab_next...) to the driver's key name."""
        return self.keys.get(key, key)

    def _call(self, cmd):
        try:
            out = self.driver.call(cmd)
        except PilotStop:
            raise
        except Exception as e:
            self.stop(f"driver failed on {cmd[0]}: {e}", Stuck)
        if not isinstance(out, dict) or not out.get("ok", False):
            self.stop(f"driver refused {cmd[0]}: {out}", Stuck)
        return out

    def look(self):
        """Capture, OCR and classify one frame; saves the step. Returns (frame, screen).
        Stops on a forbidden screen, or after more than unknown_limit UNKNOWN looks in a row
        (unless the last known screen is a video that is being backed through)."""
        self.step += 1
        steps = self.run_dir / "steps"
        out_png = steps / f"{self.step:04d}_look.png"
        out = self._call(["look", "--title", self.title, "--out", str(out_png), "--ocr"])
        # the driver echoes the path it wrote, possibly in Windows form; prefer the WSL path asked for
        png_path = str(out_png) if out_png.exists() else (out.get("png") or str(out_png))
        frame = Frame(png_path, out.get("words", []), out.get("lines"), out.get("size"))
        screen = classify(frame, self.catalog)
        png = Path(frame.png)
        dst = steps / f"{self.step:04d}_{screen}.png"
        if png.exists():
            try:
                if png.resolve().parent == steps.resolve():
                    png.replace(dst)
                else:
                    shutil.copyfile(png, dst)
                frame.png = str(dst)
            except OSError:
                pass
        (steps / f"{self.step:04d}_{screen}.json").write_text(json.dumps(
            {"step": self.step, "screen": screen, "png": frame.png, "lines": frame.lines,
             "words": frame.words, "loaded_franchise": self.loaded_franchise}, indent=1))
        if screen != self.screen:
            opts = self.spec(screen).get("options") or []
            default = self.spec(screen).get("default")
            self.cursor = opts.index(default) if default in opts else (0 if opts else None)
        self.frame, self.screen, self.last_png = frame, screen, frame.png
        if screen == UNKNOWN:
            self.unknown_run += 1
            if not self.spec(self.last_known).get("wait_or_back") and self.unknown_run > self.unknown_limit:
                self.stop(f"unrecognised screen for {self.unknown_run} looks", Unknown)
        else:
            self.unknown_run = 0
            self.last_known = screen
            if self.spec(screen).get("forbidden"):
                self.stop(f"forbidden screen {screen}", Forbidden)
        return frame, screen

    def press(self, key, hold=0.08, wait=0.6, times=1, _confirm=False):
        """Press a logical or raw key. Refuses Space outside confirm_on and the current
        screen's forbidden_keys (a Forbidden stop)."""
        if self.screen is None:
            self.look()
        name = self.key_name(key)
        spec = self.spec()
        banned = {self.key_name(k).lower() for k in spec.get("forbidden_keys", [])}
        if name.lower() in banned or key.lower() in banned:
            self.stop(f"key {key!r} is forbidden on {self.screen}", Forbidden)
        if name == self.key_name("select") and not _confirm:
            self.stop(f"Space on {self.screen} outside confirm_on", Forbidden)
        if os.environ.get("MU_PILOT_TRACE"):
            print(f"[trace] press {name} hold={hold} times={times} on {self.screen}", flush=True)
        self._call(["key", "--name", name, "--hold", str(hold), "--times", str(times)])
        if self.cursor is not None and spec.get("highlight") == "ocr_order":
            fwd, back = self._nav_keys(spec)
            n = len(spec.get("options") or [])
            if name == fwd:
                self.cursor = min(n - 1, self.cursor + times)
            elif name == back:
                self.cursor = max(0, self.cursor - times)
        self.sleep(wait)

    def wait_for(self, screens, timeout=10, poll=1.0):
        """Look until one of `screens` shows; a Stuck stop after `timeout` seconds."""
        screens = [screens] if isinstance(screens, str) else list(screens)
        end = self.clock() + timeout
        while True:
            frame, screen = self.look()
            if screen in screens:
                return frame, screen
            if self.clock() >= end:
                self.stop(f"waited {timeout}s for {screens}, still on {screen}", Stuck)
            self.sleep(poll)

    # -------------------------------------------------------------- highlight
    @staticmethod
    def _nav_keys(spec):
        return ("down", "up") if spec.get("nav", "vertical") == "vertical" else ("right", "left")

    def highlighted(self, frame=None, screen=None, labels=None):
        """The highlighted option on this screen, or None when it can't be told."""
        frame, screen = frame or self.frame, screen or self.screen
        spec = self.spec(screen)
        mode = spec.get("highlight")
        labels = labels or spec.get("options") or []
        if mode == "ocr_order":
            opts = spec.get("options") or []
            return opts[self.cursor] if self.cursor is not None and self.cursor < len(opts) else None
        if mode == "border":
            if self.highlight_reader:
                return self.highlight_reader(frame, labels, spec)
            return border_highlight(frame, labels, reach=spec.get("border_reach", 0.06))
        return None

    def highlight(self, label, limit=12):
        """Move the highlight onto `label` with the arrow keys along the screen's `nav` axis."""
        screen = self.screen
        spec = self.spec()
        fwd, back = self._nav_keys(spec)
        opts = list(spec.get("options") or [])
        for _ in range(limit):
            labels = opts if any(label_match(label, o) for o in opts) else opts + [label]
            cur = self.highlighted(labels=labels)
            if cur is not None and label_match(cur, label):
                return
            key = fwd
            a, b = (self.frame.find(cur) if cur else None), self.frame.find(label)
            i = 1 if spec.get("nav", "vertical") == "vertical" else 0
            if a and b and a[i] != b[i]:
                key = fwd if b[i] > a[i] else back
            elif cur in opts and label in opts:
                key = fwd if opts.index(label) > opts.index(cur) else back
            self.press(key)
            self.look()
            if self.screen != screen:
                self.stop(f"moving the highlight to {label} left {screen} for {self.screen}", Stuck)
        self.stop(f"couldn't highlight {label} on {screen}", Stuck)

    # ---------------------------------------------------------------- confirm
    def confirm_ok(self, screen):
        spec = self.spec(screen)
        extra = self.policy.get(screen, [])
        if spec.get("never_select"):
            return []
        if spec.get("forbidden_confirm"):
            return list(extra)
        return list(spec.get("confirm_ok", [])) + list(extra)

    def _quit_refused(self, screen, label):
        guard = self.spec(screen).get("quit_guard")
        if not guard or not label_match(label, guard):
            return None
        if self.allow_quit or norm(self.loaded_franchise or "").startswith(TUNE_PREFIX):
            return None
        return (f"refusing to quit franchise {self.loaded_franchise or '(unknown)'}: quitting writes "
                f"<save>-AUTOSAVE; only TUNE-* franchises quit without allow_quit=True")

    def confirm_on(self, screen, label, wait=1.0):
        """Press Space on `screen` only if `label` is allowed there (confirm_ok or policy) and,
        where the highlight can be read, is the highlighted option. Anything else stops."""
        if self.screen != screen:
            self.look()
        if self.screen != screen:
            self.stop(f"confirm_on expected {screen}, on {self.screen}", Stuck)
        if not any(label_match(label, ok) for ok in self.confirm_ok(screen)):
            self.stop(f"{label!r} isn't an allowed confirm on {screen}", Forbidden)
        refused = self._quit_refused(screen, label)
        if refused:
            self.stop(refused, Forbidden)
        spec = self.spec(screen)
        if spec.get("highlight") and (spec.get("options") or spec.get("loads_franchise") or self.policy.get(screen)):
            labels = list(spec.get("options") or [])
            if not any(label_match(label, o) for o in labels):
                labels.append(label)
            cur = self.highlighted(labels=labels)
            if cur is None or not label_match(cur, label):
                self.stop(f"highlight on {screen} is {cur!r}, not {label!r}; not confirming", Stuck)
        self.press("select", wait=wait, _confirm=True)
        if spec.get("loads_franchise"):
            self.loaded_franchise = norm(label)
        return True

    # ------------------------------------------------------------------- goto
    def plan(self, start, target):
        """Shortest list of (screen, next_screen, steps) over the catalog's exits, or None."""
        if start == target:
            return []
        if self.spec(target).get("no_route"):
            return None
        prev = {start: None}
        q = deque([start])
        while q:
            s = q.popleft()
            for nxt, steps in (self.spec(s).get("exits") or {}).items():
                if nxt in prev or nxt not in self.screens or self.spec(nxt).get("no_route"):
                    continue
                prev[nxt] = (s, steps)
                if nxt == target:
                    path, cur = [], nxt
                    while prev[cur]:
                        s0, st = prev[cur]
                        path.append((s0, cur, st))
                        cur = s0
                    return path[::-1]
                q.append(nxt)
        return None

    def _run_steps(self, screen, steps):
        for step in steps:
            op, arg = step[0], step[1]
            if op == "highlight":
                self.highlight(arg)
            elif op == "confirm":
                self.confirm_on(screen, arg)
            elif op == "hold":
                self.press(arg, hold=2.0, wait=1.5)
            else:
                self.press(op, times=int(arg))

    def goto(self, target, timeout=120, allow_quit=False, hop_timeout=30, poll=1.0):
        """Walk the exits to `target`, re-planning from wherever each hop lands (interrupts
        included). Stuck when a screen won't let go or no route exists."""
        self.allow_quit = allow_quit
        try:
            end = self.clock() + timeout
            frame, screen = self.look()
            same, backs, prev, unknown_run = 0, 0, None, 0
            while screen != target:
                if self.clock() >= end:
                    self.stop(f"goto {target} timed out on {screen}", Stuck)
                if screen == UNKNOWN:
                    video = self.spec(self.last_known)
                    if video.get("wait_or_back"):
                        # A video's frames are UNKNOWN every look; a screen whose OCR merely
                        # jittered is UNKNOWN for a frame or two. Back out only after four in a
                        # row, or a jittering hub spins into the quit dialog (P2, 2026-11-10).
                        unknown_run += 1
                        if unknown_run >= 4:
                            backs += 1
                            if backs > video.get("max_backs", 15):
                                self.stop(f"{self.last_known} didn't end after {backs - 1} backs", Stuck)
                            self.press("back", wait=poll)
                            unknown_run = 0
                    else:
                        self.sleep(poll)
                    frame, screen = self.look()
                    continue
                unknown_run = 0
                same = same + 1 if screen == prev else 0
                prev = screen
                if same > self.spec(screen).get("repeat", 0) + self.stuck_limit:
                    self.stop(f"still on {screen} after {same} tries toward {target}", Stuck)
                path = self.plan(screen, target)
                if path is None:
                    self.stop(f"no route from {screen} to {target}", Stuck)
                for s, _, steps in path:
                    for st in steps:
                        if st[0] == "confirm":
                            refused = self._quit_refused(s, st[1])
                            if refused:
                                self.stop(refused, Forbidden)
                _, nxt, steps = path[0]
                if self.spec(screen).get("wait_or_back"):
                    backs += 1
                self._run_steps(screen, steps)
                frame, screen = self._settle(screen, hop_timeout, poll)
            return frame
        finally:
            self.allow_quit = False

    def _settle(self, left, timeout, poll):
        """Look until the screen isn't `left` (or a video's UNKNOWN frames), up to timeout."""
        end = self.clock() + timeout
        looks = 0
        while True:
            frame, screen = self.look()
            looks += 1
            if screen == left and self.spec(left).get("repeat") and looks >= 2:
                return frame, screen           # a multi-page screen: the next page looks the same
            if screen != left and (screen != UNKNOWN or self.spec(self.last_known).get("wait_or_back")):
                return frame, screen
            if self.clock() >= end:
                return frame, screen
            self.sleep(poll)

    # ------------------------------------------------------------------- stop
    def stop(self, reason, kind=Stuck):
        """Write run_dir/stop.json, send an ntfy notice, and raise `kind` (Stuck, Unknown,
        Forbidden)."""
        info = {"kind": kind.kind, "reason": reason, "screen": self.screen, "last_known": self.last_known,
                "step": self.step, "screenshot": self.last_png, "loaded_franchise": self.loaded_franchise,
                "time": datetime.datetime.now().isoformat(timespec="seconds")}
        (self.run_dir / "stop.json").write_text(json.dumps(info, indent=1))
        send_notice(f"{kind.kind}: {reason}\nscreen {self.screen}, step {self.step}\n{self.last_png or ''}")
        raise kind(reason, info)
