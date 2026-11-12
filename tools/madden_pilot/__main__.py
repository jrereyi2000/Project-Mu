"""Madden pilot CLI (docs/done/MADDEN_PILOT_AND_TUNING_PLAN.md section 2.3).

    python3 -m tools.madden_pilot launch      [--pack "Mu Base"]
    python3 -m tools.madden_pilot clone-save  [--from CAREER-MU2026] --to CAREER-TUNE-<name>
    python3 -m tools.madden_pilot sim-season  --save CAREER-TUNE-<name> [--to "week N"|regular-season-end|playoffs-end]
    python3 -m tools.madden_pilot advance     --save CAREER-TUNE-<name> [--no-quit]
    python3 -m tools.madden_pilot watch-game  --save CAREER-TUNE-<name> [--to kickoff|halftime] [--hold-seconds N]
    python3 -m tools.madden_pilot catch-up    --save CAREER-<name> [--to-week N] [--dsn ...]
    python3 -m tools.madden_pilot set-setting --path "Settings>Gameplay>Accel Clamp" --value Off
    python3 -m tools.madden_pilot verify-mods --check first_down_line
    python3 -m tools.madden_pilot quit        [--franchise TUNE-<name>] [--allow-quit]
  a guarded spike (MADDEN_SPIKE_AGENT_PLAN):
    python3 -m tools.madden_pilot spike-start --brief BRIEF.json
    python3 -m tools.madden_pilot spike-look|spike-key|spike-confirm|spike-goto|spike-record|
        spike-check|spike-end|spike-analyze --brief BRIEF.json ...
  common: [--run-dir DIR] [--dry-run]

Every run writes data/pilot_runs/<YYYY-MM-DD_HHMMSS>_<job>/ (steps/NNNN_<screen>.png/.json,
result.json, stop.json on a stop) and prints result.json. Exit code 0 for ok/pass, 2 for
unknown, 1 otherwise.

--dry-run swaps the Windows driver for ScriptedMadden: a fake Madden built from catalog.json
that walks the planned route and prints every look and key. It touches no PC: nothing is
launched, copied, pressed or sent to ntfy.
"""
import argparse
import datetime
import json
import sys
import time
from pathlib import Path

from tools.madden_pilot import jobs, navigate, spike, vision
from tools.madden_pilot.navigate import ROOT, TITLE, Pilot
from tools.madden_pilot.screens import UNKNOWN, Frame, classify, load_catalog, norm

LOADING = "LOADING"            # a frame with no catalogued words (classifies as UNKNOWN)
POSTSEASON = "POSTSEASON"      # stands in for the post-season screens, which aren't catalogued yet
REGULAR_WEEKS = 18
# The in-game screens (watch-game, VISUAL_MOD_CHECK_PLAN 2.5): the fake Madden shows a scorebug
# on them and sims the clock the way the supersim controls do.
GAME_SCREENS = ("GAME_KICKOFF", "GAME_PLAY", "PAUSE_MENU", "SUPERSIM_MENU", "SUPERSIM_JUMP_FORWARD")
FAKE_SIM_STEP_SECONDS = 25     # a JUMP TO NEXT PLAY press. Madden moves ~4 s (S-1); the fake
                              # moves a quarter in a couple of dozen presses so a dry run is short
FAKE_POSSESSION_SECONDS = 120  # CHANGE OF POSSESSION: one drive, about two minutes (S-1)
FAKE_SNAP_SECONDS = 6          # a snap burns a few seconds of clock
QUARTER_WORDS = {1: "1st", 2: "2ND", 3: "3RD", 4: "4TH"}   # mixed case, like the scorebug's OCR


class ScriptedMadden:
    """A fake Madden for --dry-run and tests. Screens and moves come from catalog.json: each
    screen shows its `all` words, its first `any` word and its options; each exit's last step
    (a key, or a confirm on a label) leads to its target. On top of that it scripts what the
    catalog can't say: confirming a franchise tile opens PROGRESSION_WARNING, Proceed loads the
    Weekly Hub, NEXT WEEK sims (a few loading frames), adds a week and shows COACH_PROGRESS for
    three Space presses, and Tab (savepoint) shows a loading frame. After week 18 the header
    reads WILD CARD, and NEXT WEEK from there opens an uncatalogued screen (UNKNOWN), as the
    real post-season will until its screens are catalogued.

    A season game is scripted too (spike S-1's route): PLAY SEASON GAME > the pregame pivot >
    READY > a loading intro > GAME_KICKOFF > the kick (a Space hold) > GAME_PLAY, with Escape to
    the pause menu, SUPER SIM to the supersim screen, JUMP FORWARD to its three choices, and a
    scorebug clock that NEXT QUARTER, CHANGE OF POSSESSION and JUMP TO NEXT PLAY move.
    """

    def __init__(self, catalog, start="FRANCHISE_LIST", tiles=("TUNE-SPIKE",), year=2026, week=1,
                 intro=0, out=print):
        self.catalog = catalog
        self.screens = catalog["screens"]
        self.keymap = catalog.get("keys", {})
        self.tiles = list(tiles)
        self.year, self.week = year, week
        self.out = out
        self.t = 0.0
        self.keys = []
        self.looks = 0
        self.game_quarter = 1
        self.game_seconds = 660       # "1st 11:00": the kickoff scorebug spike S-1 read
        self.pending = [LOADING] * intro      # frames shown before the current screen
        self.progress_tabs = 0
        self.moves = {}
        for name, spec in self.screens.items():
            for target, steps in (spec.get("exits") or {}).items():
                op, arg = steps[-1][0], steps[-1][1]
                if op == "confirm":
                    self.moves[(name, "space", norm(arg))] = target
                    if not spec.get("options"):     # a lone Space screen: BOOT_SPLASH, TITLE_SCREEN
                        self.moves[(name, "space", None)] = target
                elif op != "highlight":
                    # 'dismiss' (ACCOUNT_ERROR, EXIT_MADDEN) is Space on the focused button, so it
                    # fires whatever the highlight reads; other keys land on the screen as a whole
                    key = "space" if op == "dismiss" else self.keymap.get(op, op)
                    labels = [None, *(spec.get("options") or [])] if op == "dismiss" else [None]
                    for lab in labels:
                        self.moves[(name, key, norm(lab) if lab else None)] = target
        self.screen, self.idx = None, 0
        self.go(start)

    # ------------------------------------------------------------ state
    def clock(self):
        return self.t

    def sleep(self, s):
        self.t += max(float(s), 0.01)

    def go(self, screen):
        self.screen = screen
        opts = self.options
        default = self.screens.get(screen, {}).get("default")
        self.idx = opts.index(default) if default in opts else 0

    @property
    def options(self):
        if self.screen == "FRANCHISE_LIST":
            return self.tiles
        opts = list(self.screens.get(self.screen, {}).get("options") or [])
        if self.screen == "HUB_GAMEDAY" and self.week <= 1:
            opts = [o for o in opts if o != "WEEKLY RECAP"]     # no recap tile before a played week
        return opts

    @property
    def highlight(self):
        return self.options[self.idx] if self.options else None

    @property
    def game_clock(self):
        """The scorebug's game clock, in the shape the pilot reads ("1st 11:00", "2ND 0:18")."""
        return f"{QUARTER_WORDS[self.game_quarter]} {self.game_seconds // 60}:{self.game_seconds % 60:02d}"

    def header(self):
        if self.week > REGULAR_WEEKS:
            return f"{self.year}. WILD CARD"
        return f"{self.year}. WEEK {self.week}"

    def lines(self, screen):
        if screen not in self.screens:
            base = [] if screen == LOADING else \
                ["PLAYOFF PICTURE", "WILD CARD ROUND"] if screen == POSTSEASON else [screen]
        else:
            spec = self.screens[screen]
            words = list(spec.get("all", [])) + list(spec.get("any", [])[:1])
            if screen == "FRANCHISE_LIST":
                base = words + self.tiles + ["CREATE NEW LEAGUE"]
            elif self.options:
                # Lay the option rows out first, where the screen really draws them: a menu's
                # `all` word is often also one of its options (PAUSE_MENU's RESUME), and the
                # pilot reads a highlight's direction from the option boxes' own row.
                base = list(self.options) + [w for w in words if w not in self.options]
            else:
                base = words
            if screen in ("HUB_WEEKLY", "QUIT_CONFIRM"):
                base.append(self.header())
        if screen in GAME_SCREENS:
            base = base + [self.game_clock, ":40"]      # the play clock is not the game clock
        out = []
        for ln in base:
            if ln not in out:
                out.append(ln)
        return out

    def words(self, lines):
        # CURRENT FRANCHISES lays its tiles out along one row, so their x positions order the
        # list; every other screen is a column (the pilot's highlight logic compares x).
        if self.screen == "FRANCHISE_LIST":
            row = [w for ln in lines if ln in self.tiles for w in ln.split()]
            return [{"text": w, "box": [100 + 200 * (row.index(w) if w in row else 0), 600, 150, 30],
                     "line": len(row)} for w in row] + \
                   [{"text": w, "box": [100 + 120 * j, 40 * i, 100, 30], "line": i}
                    for i, ln in enumerate(lines) if ln not in self.tiles
                    for j, w in enumerate(ln.split())]
        return [{"text": w, "box": [100 + 120 * j, 40 * i, 100, 30], "line": i}
                for i, ln in enumerate(lines) for j, w in enumerate(ln.split())]

    def check(self):
        """Screens whose generated words don't classify back to themselves (should be none)."""
        bad = []
        for name in self.screens:
            if not self.screens[name].get("all") and not self.screens[name].get("any"):
                continue
            got = classify(Frame(None, self.words(self.lines(name))), self.catalog)
            if got != name:
                bad.append((name, got))
        return bad

    # ------------------------------------------------------------ driver
    def call(self, cmd):
        op = cmd[0]
        if op == "windows":
            return {"ok": True, "windows": [{"title": TITLE, "pid": 1, "minimized": False, "exe": "Madden27.exe"}]}
        if op == "focus":
            return {"ok": True, "hwnd": 1}
        if op == "look":
            self.looks += 1
            show = self.pending.pop(0) if self.pending else self.screen
            lines = self.lines(show)
            extra = f"  ({self.header()})" if self.header() in lines else ""
            self.out(f"  look  -> {show if show in self.screens else UNKNOWN + ' (' + show.lower() + ')'}{extra}")
            return {"ok": True, "png": None, "size": [2560, 1440], "words": self.words(lines)}
        if op == "key":
            name = cmd[cmd.index("--name") + 1]
            times = int(cmd[cmd.index("--times") + 1]) if "--times" in cmd else 1
            for _ in range(times):
                before, hl = self.screen, self.highlight
                self.keys.append(name)
                self.on_key(name)
                on = f" on {hl}" if hl and name == self.keymap.get("select", "space") else ""
                self.out(f"  key   {name:<9} {before}{on} -> {self.screen}")
            return {"ok": True, "name": name, "sent": times}
        if op == "record":
            out_dir = Path(cmd[cmd.index("--dir") + 1])
            out_dir.mkdir(parents=True, exist_ok=True)
            from PIL import Image
            for index in range(3):          # three blank frames stand in for the burst
                Image.new("RGB", (64, 36), (18, 18, 24)).save(out_dir / f"frame_{index:04d}.jpg", "JPEG")
            self.out(f"  record -> 3 blank frames in {out_dir.name} (dry run)")
            return {"ok": True, "dir": str(out_dir), "frames": 3, "fps_actual": 5.0,
                    "size": [2560, 1440]}
        if op in ("run", "kill"):
            self.out(f"  {op}  {' '.join(map(str, cmd[1:]))} (dry run: not done)")
            return {"ok": True}
        return {"ok": False, "error": f"unknown command {op}"}

    def on_key(self, name):
        spec = self.screens.get(self.screen, {})
        select = self.keymap.get("select", "space")
        if self.screen == "COACH_PROGRESS" and name == select:
            self.progress_tabs -= 1
            if self.progress_tabs <= 0:
                self.go("HUB_WEEKLY")
            return
        fwd, back = ("down", "up") if spec.get("nav", "vertical") == "vertical" else ("right", "left")
        if name in (fwd, back) and self.options:
            self.idx = max(0, min(len(self.options) - 1, self.idx + (1 if name == fwd else -1)))
            return
        label = self.highlight
        if name == "esc" and self.screen == "GAME_PLAY":
            self.go("PAUSE_MENU")
            return
        if name == "backspace" and self.screen == "SUPERSIM_MENU":
            self.go("PAUSE_MENU")
            return
        if self.game_key(name, label, select):
            return
        if name == select:
            if spec.get("loads_franchise") and label:
                self.pending = [LOADING]
                self.go("PROGRESSION_WARNING")
                return
            if self.screen == "PROGRESSION_WARNING" and label == "PROCEED WITHOUT PROGRESSION":
                self.pending = [LOADING] * 3
                self.go("HUB_WEEKLY")
                return
            if self.screen == "ADVANCE_MENU" and label in ("NEXT WEEK", "FORCE ADVANCE") and self.week > REGULAR_WEEKS:
                self.go(POSTSEASON)
                return
            if self.screen == "ADVANCE_MENU" and label in ("NEXT WEEK", "FORCE ADVANCE"):
                self.pending = [LOADING] * 3
                self.week += 1
                self.progress_tabs = 3
                self.go("COACH_PROGRESS")
                return
        if name == self.keymap.get("savepoint"):
            self.pending = [LOADING]
            return
        target = self.moves.get((self.screen, name, norm(label) if name == select and label else None))
        if target:
            self.go(target)

    def game_key(self, name, label, select):
        """The in-game route the catalog can't script: the game-start chain and the sim controls.

        Scripted rather than an exit because the pivot's only control has no OCR label and the
        intro between READY and kickoff is unattended, and because the sim controls move the fake
        clock rather than a screen. True when it handled the key.
        """
        if name != select:
            return False
        if self.screen == "HUB_GAMEDAY" and label == "PLAY SEASON GAME":
            self.pending = [LOADING]
            self.go("GAME_PREGAME_PIVOT")
        elif self.screen == "GAME_PREGAME_PIVOT":
            self.pending = [LOADING]
            self.go("GAME_PREGAME_READY")
        elif self.screen == "GAME_PREGAME_READY" and label == "READY":
            self.pending = [LOADING] * 3          # the ~40 s broadcast intro, no press needed
            self.go("GAME_KICKOFF")
        elif self.screen == "GAME_KICKOFF":
            self.go("GAME_PLAY")
        elif self.screen == "PAUSE_MENU" and label == "SUPER":
            self.pending = [LOADING]
            self.go("SUPERSIM_MENU")
        elif self.screen == "PAUSE_MENU" and label == "RESUME":
            self.go("GAME_PLAY")
        elif self.screen == "PAUSE_MENU" and label == "QUIT GAME":
            self.pending = [LOADING]
            self.go("QUIT_GAME_CONFIRM")
        elif self.screen == "SUPERSIM_MENU" and label == "JUMP FORWARD":
            self.go("SUPERSIM_JUMP_FORWARD")
        elif self.screen == "SUPERSIM_MENU" and label == "JUMP TO NEXT PLAY":
            self.pending = [LOADING]
            self.game_seconds = max(0, self.game_seconds - FAKE_SIM_STEP_SECONDS)
            self.go("SUPERSIM_MENU")      # the sim screen comes back with the top item selected
        elif self.screen == "SUPERSIM_JUMP_FORWARD" and label == "NEXT QUARTER":
            self.pending = [LOADING]
            self.game_quarter = min(4, self.game_quarter + 1)
            self.game_seconds = 900
            self.go("SUPERSIM_MENU")
        elif self.screen == "SUPERSIM_JUMP_FORWARD" and label == "CHANGE OF POSSESSION":
            self.pending = [LOADING]
            self.game_seconds = max(0, self.game_seconds - FAKE_POSSESSION_SECONDS)
            self.go("SUPERSIM_MENU")
        elif self.screen == "GAME_PLAY":
            self.game_seconds = max(0, self.game_seconds - FAKE_SNAP_SECONDS)
        else:
            return False
        return True


class DryMirrorStore:
    """Stand-in for nfl_mirror_settings/nfl_mirror_weeks in --dry-run: the mirror is on and every
    week the job asks about reads WRITTEN, so the fake loop never waits."""

    def __init__(self, season=jobs.CATCH_UP_SEASON):
        self.season = season

    def settings(self):
        return {"enabled": True, "season": self.season, "last_week": None}

    def week_status(self, season, week):
        return "WRITTEN"

    def close(self):
        pass


ROUTES = {
    "sim-season": ("FRANCHISE_LIST > highlight {tile} > Space > PROGRESSION_WARNING (Proceed) > HUB_WEEKLY "
                   "(read 2026. WEEK N) > [Z > HUB_GAMEDAY > highlight ADVANCE WEEK > Space > ADVANCE_MENU > "
                   "FORCE ADVANCE > sim > COACH_PROGRESS x3 > HUB_WEEKLY (read week)] until {to} > Tab (savepoint) > "
                   "Backspace > QUIT_CONFIRM (YES) > FRANCHISE_LIST"),
    "advance": "FRANCHISE_LIST > highlight {tile} > Space > PROGRESSION_WARNING (Proceed) > hub > GAMEDAY > "
               "ADVANCE WEEK > FORCE ADVANCE > SIM AHEAD > (read the stop) > Backspace > QUIT_CONFIRM (YES) > "
               "FRANCHISE_LIST",
    "catch-up": "FRANCHISE_LIST > highlight {tile} > Space > PROGRESSION_WARNING (Proceed) > hub > GAMEDAY > "
                "ADVANCE WEEK > FORCE ADVANCE > sim > Backspace > QUIT_CONFIRM (YES) > main menu > wait for "
                "nfl_mirror_weeks WRITTEN > reload > ... until the first LIVE week (or --to-week)",
    "launch": "MMCModManager.exe -launch {pack} > wait for the Madden window > press through ACCOUNT ERROR, "
              "splash and title > a menu (MAIN_MENU, FRANCHISE_LANDING or FRANCHISE_LIST)",
    "quit": "hub > Backspace > QUIT_CONFIRM (YES, TUNE-* or --allow-quit only) > FRANCHISE_LIST",
    "set-setting": "walk {path} by OCR labels on catalogued screens > Right until the row reads {value} > back",
    "verify-mods": "run checks/{check}.py",
    "clone-save": "copy {src} to {dst} in MADDEN_SAVES_DIR",
    "watch-game": "FRANCHISE_LIST > highlight {tile} > Space > PROGRESSION_WARNING (Proceed) > hub > GAMEDAY > "
                  "highlight PLAY SEASON GAME > Space (this job's policy; the catalog still allows ADVANCE WEEK "
                  "only) > GAME_PREGAME_PIVOT > Space > GAME_PREGAME_READY (READY) > ~40 s intro > GAME_KICKOFF > "
                  "Space held 1.5 s > {to} > hold {hold_seconds}s pressing nothing [halftime also: Esc > PAUSE_MENU > "
                  "SUPER > SUPERSIM_MENU > JUMP FORWARD > NEXT QUARTER > CHANGE OF POSSESSION x2 > JUMP TO NEXT PLAY "
                  "until the clock reads under 20 s in Q2 > Backspace > RESUME > the half's last snap] > leave: "
                  "PAUSE_MENU > highlight QUIT GAME (this job's policy; the catalog does not allow it) > Space > "
                  "QUIT_GAME_CONFIRM > highlight QUIT GAME > Space > FRANCHISE_LIST",
}


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--run-dir", help="where steps/, result.json and stop.json go "
                                          "(default data/pilot_runs/<YYYY-MM-DD_HHMMSS>_<job>/); "
                                          "spike jobs use it as the spike root "
                                          "(default data/spikes/<id>/)")
    common.add_argument("--dry-run", action="store_true", help="walk the route on a scripted fake Madden; touch no PC")
    ap = argparse.ArgumentParser(prog="python3 -m tools.madden_pilot", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="job", required=True)
    s = sub.add_parser("launch", parents=[common], help="start Madden through the MMC Mod Manager with a pack")
    s.add_argument("--pack", default=jobs.DEFAULT_PACK)
    s = sub.add_parser("clone-save", parents=[common], help="copy a save to CAREER-TUNE-<name>")
    s.add_argument("--from", dest="src", default=jobs.BASELINE_SAVE)
    s.add_argument("--to", dest="dst", required=True)
    s = sub.add_parser("sim-season", parents=[common], help="sim a CAREER-TUNE-* franchise forward")
    s.add_argument("--save", required=True)
    s.add_argument("--to", default="regular-season-end", help="'week N', regular-season-end or playoffs-end")
    s.add_argument("--no-quit", action="store_true", help="stay in the franchise after the savepoint")
    s.add_argument("--franchise", help="the franchise already loaded, e.g. TUNE-P2A: sim it in place "
                   "instead of going back to CURRENT FRANCHISES (the hub doesn't show the save name)")
    s = sub.add_parser("advance", parents=[common], help="advance a CAREER-TUNE-* franchise exactly one stop")
    s.add_argument("--save", required=True)
    s.add_argument("--no-quit", action="store_true", help="stay in the franchise after the advance")
    s.add_argument("--franchise", help="the TUNE-* franchise already loaded: advance it in place")
    s = sub.add_parser("watch-game", parents=[common],
                       help="start a CAREER-TUNE-* season game and hold still while it is recorded")
    s.add_argument("--save", required=True)
    s.add_argument("--to", default="kickoff", help="kickoff, or halftime for the supersim recipe")
    s.add_argument("--hold-seconds", type=int, default=60,
                   help="how long to press nothing once the segment starts (1-600)")
    s.add_argument("--pack", help="the pack the game was launched with; recorded in the result")
    s.add_argument("--franchise", help="the TUNE-* franchise already loaded: watch it in place")
    s.add_argument("--no-quit", action="store_true", help="leave the game/franchise open afterwards")
    s = sub.add_parser("catch-up", parents=[common],
                       help="sim the real CAREER-* franchise one week at a time, waiting for the NFL mirror")
    s.add_argument("--save", required=True)
    s.add_argument("--to-week", type=int, help="stop once the save reaches this Mu week")
    s.add_argument("--dsn", help="the franchise database (default: DATABASE_URL, else the active franchise)")
    s = sub.add_parser("set-setting", parents=[common], help="walk a menu path and set a value")
    s.add_argument("--path", required=True)
    s.add_argument("--value", required=True)
    s.epilog = ("Needs a pack whose main menu has a Settings route: vanilla, or Mu Base with Mu Franchise "
                "Menu 1.1. Mu Test (Mu Franchise Only) has no main-menu Settings (audit-2 L2, M-0447). "
                "The job walks the game that is already running; launch that pack first.")
    s = sub.add_parser("verify-mods", parents=[common], help="run a check from checks/")
    s.add_argument("--check", required=True)
    s = sub.add_parser("quit", parents=[common], help="quit the loaded franchise to the franchise list")
    s.add_argument("--franchise", help="the loaded franchise, e.g. TUNE-SPIKE (the hub doesn't show it)")
    s.add_argument("--allow-quit", action="store_true", help="quit a non-TUNE franchise (ask JR first)")

    sp = sub.add_parser("spike-start", parents=[common], help="start a guarded spike from a brief")
    sp.add_argument("--brief", required=True, help="the brief JSON (MADDEN_SPIKE_AGENT_PLAN section 2.2)")
    sp.add_argument("--id", help="the spike id (default: the brief's id)")
    sp = sub.add_parser("spike-look", parents=[common], help="capture and classify one frame")
    sp.add_argument("--brief", required=True)
    sp.add_argument("--id")
    sp.add_argument("--describe", action="store_true", help="also describe the frame with the vision model")
    sp = sub.add_parser("spike-key", parents=[common], help="press a key through the guard")
    sp.add_argument("--brief", required=True)
    sp.add_argument("--id")
    sp.add_argument("--name", required=True, help="a driver key name (arrows, z, c, backspace, ...)")
    sp.add_argument("--times", type=int, default=1)
    sp.add_argument("--hold", type=float, default=0.08)
    sp.add_argument("--describe", action="store_true")
    sp = sub.add_parser("spike-confirm", parents=[common], help="press Space on an allow-listed label")
    sp.add_argument("--brief", required=True)
    sp.add_argument("--id")
    sp.add_argument("--label", required=True)
    sp.add_argument("--describe", action="store_true")
    sp = sub.add_parser("spike-goto", parents=[common], help="walk the catalog routes to a screen")
    sp.add_argument("--brief", required=True)
    sp.add_argument("--id")
    sp.add_argument("--to", "--target", dest="target", required=True, help="the catalogued screen")
    sp = sub.add_parser("spike-record", parents=[common], help="record a clip and build its GIF and sheet")
    sp.add_argument("--brief", required=True)
    sp.add_argument("--id")
    sp.add_argument("--seconds", type=int, default=5)
    sp.add_argument("--fps", type=int, default=5)
    sp.add_argument("--note")
    sp = sub.add_parser("spike-check", parents=[common], help="run asset checks on a captured frame")
    sp.add_argument("--brief", required=True)
    sp.add_argument("--id")
    sp.add_argument("--check", action="append", help="a named check from the brief (repeatable)")
    sp.add_argument("--checkpoint", help="a named checkpoint from the brief")
    sp = sub.add_parser("spike-end", parents=[common], help="check and render the report, then back out")
    sp.add_argument("--brief", required=True)
    sp.add_argument("--id")
    sp.add_argument("--report", required=True, help="the report JSON the agent wrote")
    sp = sub.add_parser("spike-analyze", parents=[common],
                        help="the heavy after-run pass once Madden is closed")
    sp.add_argument("--brief", required=True)
    sp.add_argument("--id", help="the spike id (default: the brief's id)")
    return ap


def run_dir_for(job, given=None, dry_run=False):
    """One run directory per invocation. A second run started in the same second gets a suffix:
    the pilot numbers step PNGs from 1 in each process, so two runs sharing `steps/` would
    overwrite each other's frames (spike S-1 lost most of its in-game frames that way)."""
    if given:
        return Path(given)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    base = ROOT / "data" / "pilot_runs" / f"{stamp}_{job}{'_dry-run' if dry_run else ''}"
    out, n = base, 1
    while out.exists():
        n += 1
        out = base.with_name(f"{base.name}-{n}")
    return out


def dry_pilot(catalog, run_dir, args, out=print):
    start, intro = {"launch": ("MAIN_MENU", 2), "sim-season": ("FRANCHISE_LIST", 0), "quit": ("HUB_WEEKLY", 0),
                    "advance": ("FRANCHISE_LIST", 0), "catch-up": ("FRANCHISE_LIST", 0),
                    "watch-game": ("FRANCHISE_LIST", 0),
                    "set-setting": ("HUB_WEEKLY", 0),
                    "verify-mods": ("MAIN_MENU", 0)}.get(args.job, ("MAIN_MENU", 0))
    if args.job in ("sim-season", "advance", "watch-game") and getattr(args, "franchise", None):
        start = "HUB_WEEKLY"
    tiles = ["TUNE-SPIKE"]
    if args.job in ("sim-season", "advance", "catch-up", "watch-game"):
        tile = jobs.tile_name(args.save).upper()
        tiles = ["ALLUSER-AUTOSAVE", tile] + (["TUNE-SPIKE"] if tile != "TUNE-SPIKE" else [])
    fake = ScriptedMadden(catalog, start=start, tiles=tiles, intro=intro, out=out)
    pilot = Pilot(catalog, run_dir, driver=fake, sleep=fake.sleep, clock=fake.clock,
                  highlight_reader=lambda frame, labels, spec: fake.highlight)
    pilot.dry_run = True
    navigate.send_notice = lambda message, title="Madden pilot": out(f"[dry-run] stop notice (not sent): {message}")
    return pilot, fake


def main(argv=None, out=print):
    args = build_parser().parse_args(argv)
    if args.job.startswith("spike"):
        return _spike_main(args, out)
    run_dir = run_dir_for(args.job, args.run_dir, args.dry_run)
    catalog = load_catalog()
    fmt = {k: v for k, v in vars(args).items() if v is not None}
    if args.job in ("sim-season", "advance", "catch-up", "watch-game"):
        fmt["tile"] = jobs.tile_name(args.save)
    real_notice = navigate.send_notice
    try:
        return _run(args, run_dir, catalog, fmt, out)
    finally:
        navigate.send_notice = real_notice          # dry runs swap it for a print


def _run(args, run_dir, catalog, fmt, out):
    if args.dry_run:
        out(f"[dry-run] {args.job}: {ROUTES[args.job].format_map(_Missing(fmt))}")
        out(f"[dry-run] run dir {run_dir}")
        pilot, fake = dry_pilot(catalog, run_dir, args, out=out)
    else:
        out(f"[pilot] {args.job}: run dir {run_dir}. Madden minimizes when it loses focus: keep Claude minimized "
            f"or on another monitor. Mouse to the top-left corner aborts.")
        pilot, fake = Pilot(catalog, run_dir), None

    if args.job == "launch":
        kw = {}
        if args.dry_run:
            kw = {"run": lambda argv: out(f"  run   {' '.join(argv)} (dry run: not started)"),
                  "madden_running": lambda: False}
        result = jobs.launch(pilot, pack=args.pack, **kw)
    elif args.job == "clone-save":
        result = jobs.clone_save(pilot, src=args.src, dst=args.dst, dry_run=args.dry_run)
    elif args.job == "sim-season":
        watcher = jobs.watcher_running
        if args.dry_run:
            if jobs.watcher_running():
                out("[dry-run] WARNING: pipeline.watch_save is running; a live run would refuse")
            watcher = lambda: False  # noqa: E731
        result = jobs.sim_season(pilot, save=args.save, to=args.to, watcher=watcher, quit=not args.no_quit,
                                 franchise=args.franchise)
    elif args.job == "advance":
        watcher = jobs.watcher_running
        if args.dry_run:
            watcher = lambda: False  # noqa: E731
        result = jobs.advance(pilot, save=args.save, watcher=watcher, quit=not args.no_quit,
                              franchise=args.franchise)
    elif args.job == "watch-game":
        watcher = jobs.watcher_running
        if args.dry_run:
            if jobs.watcher_running():
                out("[dry-run] WARNING: pipeline.watch_save is running; a live run would refuse")
            watcher = lambda: False  # noqa: E731
        result = jobs.watch_game(pilot, save=args.save, franchise=args.franchise, to=args.to,
                                 hold_seconds=args.hold_seconds, pack=args.pack, watcher=watcher,
                                 quit=not args.no_quit)
    elif args.job == "catch-up":
        kw = {}
        if args.dry_run:
            try:
                running = jobs.watcher_running()
            except Exception:  # noqa: BLE001 - the dry run only reports the real check
                running = False
            if not running:
                out("[dry-run] note: pipeline.watch_save isn't running; a live catch-up would refuse")
            kw = {"watcher": lambda: True, "store": DryMirrorStore(),
                  "mode_for": lambda week: "CATCH_UP" if week <= REGULAR_WEEKS else "LIVE",
                  "week_reader": lambda path: fake.week}
        result = jobs.catch_up(pilot, save=args.save, to_week=args.to_week, dsn=args.dsn, **kw)
    elif args.job == "set-setting":
        result = jobs.set_setting(pilot, path=args.path, value=args.value)
    elif args.job == "verify-mods":
        result = jobs.verify_mods(pilot, check=args.check)
    else:
        result = jobs.quit_franchise(pilot, franchise=args.franchise, allow_quit=args.allow_quit)
    out(json.dumps(result, indent=1, default=str))
    status = result.get("status")
    return 0 if status in ("ok", "pass") else 2 if status == "unknown" else 1


class _Missing(dict):
    def __missing__(self, key):
        return "{" + key + "}"


# --------------------------------------------------------------------------- spike jobs
def _stderr(message):
    print(message, file=sys.stderr)


def _spike_contract(job, **over):
    """The section 2.5 JSON shape: every key always present, extras merged in."""
    data = {"ok": True, "job": job, "step": 0, "steps_left": 0, "minutes_left": 0.0,
            "screen": None, "lines": [], "highlight": None, "png": None, "refused": None,
            "stopped": None}
    data.update(over)
    return data


def _vision_payload(described):
    return {"kind": described.kind, "title": described.title, "options": list(described.options),
            "selected": described.selected, "overlays": list(described.overlays),
            "warning": described.warning, "summary": described.summary,
            "model": described.model, "seconds": described.seconds}


def _with_guard(job, guard, result):
    data = _spike_contract(job)
    data.update(result)
    data["job"] = job
    if guard.description is not None:
        data["vision"] = _vision_payload(guard.description)
    elif guard.describe_error:
        data["vision"] = None
        data["vision_error"] = guard.describe_error
    return data


def _spike_exit(data):
    if not data.get("ok"):
        return 1
    stopped = data.get("stopped")
    if stopped and stopped != "ended":
        return 1                            # a successful last action that hit a cap
    if data.get("screen") == UNKNOWN and not data.get("refused"):
        return 2                            # a look that landed on an uncatalogued screen
    return 0


def _emit(data, out):
    out(json.dumps(data, indent=1, default=str))
    return _spike_exit(data)


def _fake_describe(png):
    return vision.ScreenDescription(kind="other", title="scripted frame", options=(), selected=None,
                                    overlays=(), warning=None, summary="dry run: no vision model",
                                    model="scripted", seconds=0.0)


def _spike_pilot(args, brief, catalog, run_dir):
    """A live Pilot, or a ScriptedMadden one for --dry-run (whose narration goes to stderr)."""
    if not args.dry_run:
        return Pilot(catalog, run_dir), None
    tiles = ["TUNE-SPIKE"]
    if brief.save:
        tile = jobs.tile_name(brief.save).upper()
        if tile not in tiles:
            tiles.insert(0, tile)
    fake = ScriptedMadden(catalog, start="FRANCHISE_LIST", tiles=tiles, out=_stderr)
    pilot = Pilot(catalog, run_dir, driver=fake, sleep=fake.sleep, clock=fake.clock,
                  highlight_reader=lambda frame, labels, spec: fake.highlight)
    pilot.dry_run = True
    navigate.send_notice = lambda message, title="Madden pilot": _stderr(
        f"[dry-run] stop notice (not sent): {message}")
    return pilot, fake


def _spike_main(args, out):
    """Run one spike-* job and print exactly one JSON object (section 2.5)."""
    real_notice = navigate.send_notice
    try:
        return _spike_job(args, out)
    finally:
        navigate.send_notice = real_notice


def _spike_job(args, out):
    job = args.job
    try:
        brief = spike.load_brief(args.brief)
    except spike.BriefError as exc:
        return _emit(_spike_contract(job, ok=False, refused=str(exc)), out)
    if args.id and args.id != brief.id:
        return _emit(_spike_contract(job, ok=False,
                                     refused=f"--id {args.id} does not match the brief ({brief.id})"), out)
    root = Path(args.run_dir) if args.run_dir else ROOT / "data" / "spikes" / brief.id
    run_dir = root / "run"

    if job == "spike-analyze":
        describe_fn = _fake_describe if args.dry_run else (
            lambda png: vision.describe(png, backend=vision.heavy_backend()))
        result = spike.analyze(root, brief, run_dir, describe_fn=describe_fn,
                               running=(lambda: False) if args.dry_run else None)
        data = _spike_contract(job)
        data.update(result)
        return _emit(data, out)

    state_path = root / "state.json"
    if job != "spike-start" and not state_path.is_file():
        return _emit(_spike_contract(job, ok=False, refused="no spike state: run spike-start first"), out)
    catalog = load_catalog()
    pilot, fake = _spike_pilot(args, brief, catalog, run_dir)
    clock = fake.clock if fake is not None else time.monotonic
    describe_fn = _fake_describe if args.dry_run else vision.describe

    if job == "spike-start":
        guard, result = spike.start(brief, pilot, state_path, dry_run=args.dry_run,
                                    describe_fn=describe_fn, clock=clock,
                                    watcher=(lambda: False) if args.dry_run else None)
        if guard is None:
            return _emit(_spike_contract(job, **result), out)
    else:
        guard = spike.SpikeGuard(brief, pilot, state_path, clock=clock, describe_fn=describe_fn)
        if job == "spike-look":
            result = guard.look(describe=args.describe)
        elif job == "spike-key":
            result = guard.key(args.name, args.times, args.hold, describe=args.describe)
        elif job == "spike-confirm":
            result = guard.confirm(args.label, describe=args.describe)
        elif job == "spike-goto":
            result = guard.goto(args.target)
        elif job == "spike-record":
            result = guard.record(args.seconds, args.fps, note=args.note)
        elif job == "spike-check":
            result = guard.check(checkpoint=args.checkpoint, checks=args.check)
        else:
            result = spike.end(brief, guard, args.report, run_dir)
            (root / "result.json").write_text(json.dumps(result, indent=1, default=str),
                                              encoding="utf-8")
    return _emit(_with_guard(job, guard, result), out)


if __name__ == "__main__":
    sys.exit(main())
