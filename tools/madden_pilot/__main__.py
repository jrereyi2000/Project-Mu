"""Madden pilot CLI (docs/in_progress/MADDEN_PILOT_AND_TUNING_PLAN.md section 2.3).

    python3 -m tools.madden_pilot launch      [--pack "Mu Base"]
    python3 -m tools.madden_pilot clone-save  [--from CAREER-WEEK1] --to CAREER-TUNE-<name>
    python3 -m tools.madden_pilot sim-season  --save CAREER-TUNE-<name> [--to "week N"|regular-season-end|playoffs-end]
    python3 -m tools.madden_pilot set-setting --path "Settings>Gameplay>Accel Clamp" --value Off
    python3 -m tools.madden_pilot verify-mods --check first_down_line
    python3 -m tools.madden_pilot quit        [--franchise TUNE-<name>] [--allow-quit]
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
from pathlib import Path

from tools.madden_pilot import jobs, navigate
from tools.madden_pilot.navigate import ROOT, TITLE, Pilot
from tools.madden_pilot.screens import UNKNOWN, Frame, classify, load_catalog, norm

LOADING = "LOADING"            # a frame with no catalogued words (classifies as UNKNOWN)
POSTSEASON = "POSTSEASON"      # stands in for the post-season screens, which aren't catalogued yet
REGULAR_WEEKS = 18


class ScriptedMadden:
    """A fake Madden for --dry-run and tests. Screens and moves come from catalog.json: each
    screen shows its `all` words, its first `any` word and its options; each exit's last step
    (a key, or a confirm on a label) leads to its target. On top of that it scripts what the
    catalog can't say: confirming a franchise tile opens PROGRESSION_WARNING, Proceed loads the
    Weekly Hub, NEXT WEEK sims (a few loading frames), adds a week and shows COACH_PROGRESS for
    three Space presses, and Tab (savepoint) shows a loading frame. After week 18 the header
    reads WILD CARD, and NEXT WEEK from there opens an uncatalogued screen (UNKNOWN), as the
    real post-season will until its screens are catalogued."""

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
        self.pending = [LOADING] * intro      # frames shown before the current screen
        self.progress_tabs = 0
        self.moves = {}
        for name, spec in self.screens.items():
            for target, steps in (spec.get("exits") or {}).items():
                op, arg = steps[-1][0], steps[-1][1]
                if op == "confirm":
                    self.moves[(name, "space", norm(arg))] = target
                elif op != "highlight":
                    self.moves[(name, self.keymap.get(op, op), None)] = target
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

    def header(self):
        if self.week > REGULAR_WEEKS:
            return f"{self.year}. WILD CARD"
        return f"{self.year}. WEEK {self.week}"

    def lines(self, screen):
        if screen not in self.screens:
            return [] if screen == LOADING else ["PLAYOFF PICTURE", "WILD CARD ROUND"] if screen == POSTSEASON \
                else [screen]
        spec = self.screens[screen]
        lines = list(spec.get("all", [])) + list(spec.get("any", [])[:1])
        if screen == "FRANCHISE_LIST":
            lines += self.tiles + ["CREATE NEW LEAGUE"]
        else:
            lines += self.options
        if screen in ("HUB_WEEKLY", "QUIT_CONFIRM"):
            lines.append(self.header())
        out = []
        for ln in lines:
            if ln not in out:
                out.append(ln)
        return out

    def words(self, lines):
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
        if name == select:
            if spec.get("loads_franchise") and label:
                self.pending = [LOADING]
                self.go("PROGRESSION_WARNING")
                return
            if self.screen == "PROGRESSION_WARNING" and label == "PROCEED WITHOUT PROGRESSION":
                self.pending = [LOADING] * 3
                self.go("HUB_WEEKLY")
                return
            if self.screen == "ADVANCE_MENU" and label == "NEXT WEEK" and self.week > REGULAR_WEEKS:
                self.go(POSTSEASON)
                return
            if self.screen == "ADVANCE_MENU" and label == "NEXT WEEK":
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


ROUTES = {
    "sim-season": ("FRANCHISE_LIST > highlight {tile} > Space > PROGRESSION_WARNING (Proceed) > HUB_WEEKLY "
                   "(read 2026. WEEK N) > [Z > HUB_GAMEDAY > highlight ADVANCE WEEK > Space > ADVANCE_MENU > "
                   "NEXT WEEK > sim > COACH_PROGRESS x3 > HUB_WEEKLY (read week)] until {to} > Tab (savepoint) > "
                   "Backspace > QUIT_CONFIRM (YES) > FRANCHISE_LIST"),
    "launch": "MMCModManager.exe -launch {pack} > wait for the Madden window > MAIN_MENU or FRANCHISE_LIST",
    "quit": "hub > Backspace > QUIT_CONFIRM (YES, TUNE-* or --allow-quit only) > FRANCHISE_LIST",
    "set-setting": "walk {path} by OCR labels on catalogued screens > Right until the row reads {value} > back",
    "verify-mods": "run checks/{check}.py",
    "clone-save": "copy {src} to {dst} in MADDEN_SAVES_DIR",
}


def build_parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--run-dir", help="where steps/, result.json and stop.json go "
                                          "(default data/pilot_runs/<YYYY-MM-DD_HHMMSS>_<job>/)")
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
    s = sub.add_parser("set-setting", parents=[common], help="walk a menu path and set a value")
    s.add_argument("--path", required=True)
    s.add_argument("--value", required=True)
    s = sub.add_parser("verify-mods", parents=[common], help="run a check from checks/")
    s.add_argument("--check", required=True)
    s = sub.add_parser("quit", parents=[common], help="quit the loaded franchise to the franchise list")
    s.add_argument("--franchise", help="the loaded franchise, e.g. TUNE-SPIKE (the hub doesn't show it)")
    s.add_argument("--allow-quit", action="store_true", help="quit a non-TUNE franchise (ask JR first)")
    return ap


def run_dir_for(job, given=None, dry_run=False):
    if given:
        return Path(given)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return ROOT / "data" / "pilot_runs" / f"{stamp}_{job}{'_dry-run' if dry_run else ''}"


def dry_pilot(catalog, run_dir, args, out=print):
    start, intro = {"launch": ("MAIN_MENU", 2), "sim-season": ("FRANCHISE_LIST", 0), "quit": ("HUB_WEEKLY", 0),
                    "set-setting": ("HUB_WEEKLY", 0), "verify-mods": ("MAIN_MENU", 0)}.get(args.job, ("MAIN_MENU", 0))
    tiles = ["TUNE-SPIKE"]
    if args.job == "sim-season":
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
    run_dir = run_dir_for(args.job, args.run_dir, args.dry_run)
    catalog = load_catalog()
    fmt = {k: v for k, v in vars(args).items() if v is not None}
    if args.job == "sim-season":
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
        result = jobs.sim_season(pilot, save=args.save, to=args.to, watcher=watcher, quit=not args.no_quit)
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


if __name__ == "__main__":
    sys.exit(main())
