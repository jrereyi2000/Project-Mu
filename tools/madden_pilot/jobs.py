"""Pilot jobs (docs/done/MADDEN_PILOT_AND_TUNING_PLAN.md section 2.3).

Each job takes a navigate.Pilot (clone_save only uses its run_dir), writes run_dir/result.json,
sends one ntfy summary through pipeline.notify (on finish, refusal and stop) and returns the
result dict. `status` is one of:
  ok        the job did what it was asked
  refused   a safety rule said no before anything was pressed or written
  stopped   the pilot stopped (Stuck / Unknown / Forbidden; stop.json and the screenshot say where)
  pass / fail / unknown   verify_mods' check result

    launch(pilot, pack="Mu Base")
    clone_save(pilot, src="CAREER-MU2026", dst="CAREER-TUNE-<name>")
    sim_season(pilot, save="CAREER-TUNE-<name>", to="week N" | "regular-season-end" | "playoffs-end")
    set_setting(pilot, path="A>B>C", value="V")
    verify_mods(pilot, check="first_down_line")
    quit_franchise(pilot, allow_quit=False)

Safety (plan D1, S7, section 2.5): saves are only cloned to, and only simmed as, CAREER-TUNE-*;
the clone never overwrites a file that isn't CAREER-TUNE-*; sim_season refuses while
pipeline.watch_save runs (a clone keeps the original's LeagueID, so the watcher would ingest it
into the real franchise database); quitting a franchise that isn't TUNE-* is refused unless
allow_quit (quitting writes <save>-AUTOSAVE).
"""
import contextlib
import datetime
import functools
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from tools.madden_pilot.navigate import ROOT, TITLE, PilotStop, Stuck, Unknown
from tools.madden_pilot.screens import UNKNOWN, label_match, norm

DEFAULT_SAVES_DIR = "/mnt/c/Users/jrere/Documents/Madden NFL 27/saves/"
TUNE_SAVE_PREFIX = "CAREER-TUNE-"
CAREER_PREFIX = "CAREER-"
BASELINE_SAVE = "CAREER-MU2026"
DEFAULT_PACK = "Mu Base"
MANAGER_EXE = "MMCModManager.exe"
DEFAULT_MANAGER_DIR = "/mnt/c/Madden27Modding/MMC_Modding_Tools_v1.1.0.7/MMC_ModManager_v1.1.0.7"
WATCHER_CHECK = 'ps -eo args | grep "[p]ipeline[.]watch_save"'

HUBS = ("HUB_WEEKLY", "HUB_GAMEDAY", "HUB_OTHER")
MAX_SIM_WEEKS = 30                 # safety cap on NEXT WEEK presses in one run
SIM_TIMEOUT = 240                  # seconds for one NEXT WEEK (spike: under 20 s)
LOAD_TIMEOUT = 120                 # seconds for a franchise to load (spike: about 25 s)
LAUNCH_TIMEOUT = 300               # seconds for Madden's window and first menu
# Header words that mean the season moved past the playoffs (todo: not seen yet; guesses).
OFFSEASON_WORDS = ("OFFSEASON", "STAFF", "RE-SIGN", "RESIGN", "FREE AGENCY", "COMBINE", "PRO DAY",
                   "DRAFT", "UDFA", "CUTDOWN", "PRESEASON")

# Standalone phase lines, highest priority first: an offseason stop must win over a card that
# happens to say "SUPER BOWL THRILLER" (P2-02, 2026-11-10).
PHASE_WORDS = ("STAFF WEEK", "STAFF", "RE-SIGN", "RESIGN", "FREE AGENCY", "COMBINE", "PRO DAY", "DRAFT",
               "UDFA", "CUTDOWN", "PRESEASON", "OFFSEASON", "PRO BOWL", "SUPER BOWL",
               "CONFERENCE CHAMPIONSHIP", "DIVISIONAL", "WILDCARD")

# "2026. WEEK 1" (OCR reads 1 as I or l, and may drop the space: "WEEKI")
_WEEK_RE = re.compile(r"\b(20\d\d)\s*[.:,]?\s*WEEK\s*([0-9IL|!O]{1,2})\b")
_PHASE_RE = re.compile(r"\b(20\d\d)\s*[.:,]\s*([A-Z][A-Z &\-]{2,30})")


# ------------------------------------------------------------------------------- helpers
def env(key, default=None):
    """A setting from the environment, else the project-root .env (pipeline.notify.env)."""
    v = os.environ.get(key)
    if v:
        return v
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from pipeline.notify import env as _env
        return _env(key, default)
    except Exception:
        return default


def saves_dir():
    return Path(env("MADDEN_SAVES_DIR", DEFAULT_SAVES_DIR))


def watcher_running():
    """True when a pipeline.watch_save process runs in WSL. `pgrep -f` would match the
    calling shell's own command line, so the [p] trick keeps grep from matching itself."""
    out = subprocess.run(WATCHER_CHECK, shell=True, capture_output=True, text=True)
    return bool(out.stdout.strip())


def tile_name(save):
    """CAREER-TUNE-SPIKE -> TUNE-SPIKE (the franchise list drops the CAREER- prefix)."""
    return save[len(CAREER_PREFIX):] if save.upper().startswith(CAREER_PREFIX) else save


def _valid_save_name(name):
    return bool(name) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_\-]*", name) is not None


def read_header(frame):
    """The hub header as {"year", "phase", "week"}: phase "WEEK" with week N for the regular
    season ("2026. WEEK 1"), else the header's words (e.g. "WILD CARD") and week None.
    None when no header is on screen. Never trust other numbers from OCR."""
    if frame is None:
        return None
    for line in frame.lines:
        up = str(line).upper()
        m = _WEEK_RE.search(up)
        if m:
            digits = m.group(2).translate(str.maketrans({"I": "1", "L": "1", "|": "1", "!": "1", "O": "0"}))
            if digits.isdigit():
                return {"year": int(m.group(1)), "phase": "WEEK", "week": int(digits)}
    for line in frame.lines:
        m = _PHASE_RE.search(str(line).upper())
        if m:
            phase = norm(m.group(2))
            if phase == "WEEK":
                continue          # a regular-season line whose number OCR dropped: use the save
            return {"year": int(m.group(1)), "phase": phase, "week": None}
    # The postseason/offseason header may carry no year ("WILDCARD WEEK", "STAFF WEEK"). Match
    # whole tokens at the start of a short line, so a "RE-SIGNING?" card is not read as RE-SIGN.
    for word in PHASE_WORDS:
        wanted = word.replace("-", " ").split()
        for line in frame.lines:
            u = norm(line)
            if len(u.split()) > 4:
                continue
            tokens = u.replace("-", " ").split()
            if len(tokens) >= len(wanted) and tokens[:len(wanted)] == wanted:
                phase = "STAFF" if word == "STAFF WEEK" else word
                return {"year": None, "phase": phase, "week": None}
    return None


def parse_target(to):
    """'week N' -> ("week", N); 'regular-season-end' / 'playoffs-end' -> (that, None)."""
    t = " ".join(str(to or "").strip().lower().replace("_", "-").split())
    m = re.fullmatch(r"(?:week|wk)\s*-?\s*(\d{1,2})", t)
    if m:
        if not 1 <= int(m.group(1)) <= 18:
            raise ValueError(f"week {m.group(1)} isn't a regular-season week (1-18)")
        return ("week", int(m.group(1)))
    if t in ("regular-season-end", "regular-season", "season-end"):
        return ("regular-season-end", None)
    if t in ("playoffs-end", "playoffs", "postseason-end"):
        return ("playoffs-end", None)
    raise ValueError(f"--to must be 'week N', 'regular-season-end' or 'playoffs-end', not {to!r}")


@contextlib.contextmanager
def patience(pilot, seconds, poll):
    """Let UNKNOWN frames (loading, sim progress) go on for `seconds` before an Unknown stop."""
    old = pilot.unknown_limit
    pilot.unknown_limit = max(old, int(seconds / max(poll, 0.1)) + 2)
    try:
        yield
    finally:
        pilot.unknown_limit = old


def _send(pilot, message, ok):
    if getattr(pilot, "dry_run", False):
        print(f"[dry-run] notice (not sent): {message}", flush=True)
        return
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        import pipeline.notify as pn
        pn.notify(message, title="Madden pilot", priority="default" if ok else "high",
                  tags=("white_check_mark",) if ok else ("warning",))
    except Exception as e:  # a notice never hides the result
        print(f"[pilot] notice not sent: {e}", flush=True)


def _summary(name, result):
    st = result.get("status")
    bits = [f"{name}: {st}"]
    for k in ("why", "reason", "save", "weeks_simmed", "end_week", "pack", "dst", "check", "minutes"):
        if result.get(k) not in (None, "", []):
            bits.append(f"{k} {result[k]}")
    if result.get("screenshot"):
        bits.append(str(result["screenshot"]))
    return "\n".join(bits)


def job(name):
    """Wraps a job: times it, turns a PilotStop into status 'stopped', writes result.json
    and sends the ntfy summary."""
    def deco(fn):
        @functools.wraps(fn)
        def run(pilot, *args, **kwargs):
            t0 = time.monotonic()
            started = datetime.datetime.now().isoformat(timespec="seconds")
            progress = {}
            try:
                result = fn(pilot, *args, progress=progress, **kwargs)
            except PilotStop as e:
                result = {**progress, "status": "stopped", "kind": e.kind, "reason": e.reason,
                          "screen": e.info.get("screen"), "screenshot": e.info.get("screenshot")}
            result = {"job": name, **result, "started": started,
                      "minutes": round((time.monotonic() - t0) / 60, 2),
                      "run_dir": str(pilot.run_dir)}
            if getattr(pilot, "dry_run", False):
                result["dry_run"] = True
            Path(pilot.run_dir).mkdir(parents=True, exist_ok=True)
            (Path(pilot.run_dir) / "result.json").write_text(json.dumps(result, indent=1, default=str))
            _send(pilot, _summary(name, result), result["status"] in ("ok", "pass"))
            return result
        return run
    return deco


def _refuse(why, **extra):
    return {"status": "refused", "why": why, **extra}


# ------------------------------------------------------------------------------- clone_save
@job("clone-save")
def clone_save(pilot, src=BASELINE_SAVE, dst=None, folder=None, dry_run=False, progress=None):
    """Copy save `src` to `dst` inside MADDEN_SAVES_DIR. `dst` must be CAREER-TUNE-<name>; an
    existing CAREER-TUNE-* file is replaced, anything else never is. The copy gets a fresh
    mtime, so Madden lists it first."""
    folder = Path(folder) if folder else saves_dir()
    if not dst or not str(dst).upper().startswith(TUNE_SAVE_PREFIX) or len(dst) <= len(TUNE_SAVE_PREFIX):
        return _refuse(f"destination must be {TUNE_SAVE_PREFIX}<name>, not {dst!r}", src=src, dst=dst)
    if not _valid_save_name(dst) or not _valid_save_name(src):
        return _refuse("save names are plain file names (letters, digits, - and _)", src=src, dst=dst)
    if src.upper() == dst.upper():
        return _refuse("source and destination are the same save", src=src, dst=dst)
    src_path, dst_path = folder / src, folder / dst
    if not src_path.is_file():
        return _refuse(f"no save {src} in {folder}", src=src, dst=dst)
    # a case-insensitive Windows folder: an existing file under any spelling counts
    existing = [p for p in folder.iterdir() if p.name.upper() == dst.upper()]
    for p in existing:
        if not p.name.upper().startswith(TUNE_SAVE_PREFIX):
            return _refuse(f"{p.name} exists and isn't {TUNE_SAVE_PREFIX}*; not overwriting", src=src, dst=dst)
    out = {"status": "ok", "src": str(src_path), "dst": str(dst_path), "overwrote": bool(existing),
           "bytes": src_path.stat().st_size}
    if dry_run:
        print(f"[dry-run] would copy {src_path} -> {dst_path}", flush=True)
        return {**out, "copied": False}
    tmp = folder / f".mu-pilot-clone-{os.getpid()}.tmp"      # not CAREER-*: no watcher picks it up
    try:
        shutil.copyfile(src_path, tmp)
        os.replace(tmp, dst_path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return {**out, "copied": True}


# ------------------------------------------------------------------------------- launch
def launch_command(pack):
    """cmd.exe /c start "" "<MMCModManager.exe>" -launch <pack>, through pipeline.game_launch's
    helpers when they import (its own launch() picks a per-game pack from the database, so the
    pilot only borrows the command), else built here the same way."""
    try:
        from pipeline import game_launch
        return game_launch.command(game_launch.manager_exe(), pack)
    except Exception:
        exe = os.environ.get("MMC_MOD_MANAGER_EXE") or os.path.join(
            os.environ.get("MMC_MANAGER_DIR", DEFAULT_MANAGER_DIR), MANAGER_EXE)
        m = re.match(r"^/mnt/([a-zA-Z])(/.*)?$", exe)
        if m:
            exe = f"{m.group(1).upper()}:" + (m.group(2) or "/").replace("/", "\\")
        return ["cmd.exe", "/c", "start", "", exe, "-launch", pack]


def _madden_running():
    from pipeline.gpu_queue import process_check
    return process_check.is_madden_running()


def wait_known(pilot, screens, timeout, poll=2.0, what=None):
    """Look until one of `screens` shows. UNKNOWN frames (loading, intros, sim progress) are
    waited out; at `timeout` the stop is Unknown when the screen is still unrecognised, else
    Stuck. Forbidden screens stop on sight (Pilot.look)."""
    end = pilot.clock() + timeout
    what = what or str(list(screens))
    with patience(pilot, timeout + 10 * poll, poll):
        while True:
            frame, screen = pilot.look()
            if screen in screens:
                return frame, screen
            if pilot.clock() >= end:
                if screen == UNKNOWN:
                    pilot.stop(f"screen still unrecognised after {timeout}s waiting for {what}", Unknown)
                pilot.stop(f"waited {timeout}s for {what}, still on {screen}", Stuck)
            pilot.sleep(poll)


def _wait_window(pilot, timeout, poll=3.0):
    end = pilot.clock() + timeout
    while True:
        out = pilot._call(["windows", "--title", pilot.title])
        wins = [w for w in out.get("windows", []) if not w.get("minimized")] or out.get("windows", [])
        if wins:
            return wins[0]
        if pilot.clock() >= end:
            pilot.stop(f"no {pilot.title!r} window after {timeout}s", Stuck)
        pilot.sleep(poll)


@job("launch")
def launch(pilot, pack=DEFAULT_PACK, run=None, madden_running=None, timeout=LAUNCH_TIMEOUT, progress=None):
    """Start Madden through the MMC Mod Manager with `pack` (unless Madden already runs), wait
    for its window, then for MAIN_MENU or FRANCHISE_LIST. Intro and loading frames are UNKNOWN;
    a screen that stays unrecognised stops as Unknown with its screenshot (MAIN_MENU is still
    todo in the catalog)."""
    run = run or (lambda argv: subprocess.run(argv, check=True, cwd="/mnt/c"))
    running = (madden_running or _madden_running)()
    argv = launch_command(pack)
    progress.update(pack=pack, command=argv, already_running=bool(running))
    if not running:
        run(argv)
    _wait_window(pilot, timeout)
    _, screen = wait_known(pilot, ["MAIN_MENU", "FRANCHISE_LIST", *HUBS], timeout)
    return {"status": "ok", "pack": pack, "command": argv, "already_running": bool(running), "screen": screen}


# ------------------------------------------------------------------------------- sim_season
def _week_now(pilot, allow_unreadable=False):
    """Read the week/phase header from the current hub, then the Gameday tab, then the Weekly Hub.

    Gameday carries the header too (including the year-less postseason phase, "WILDCARD WEEK").
    Stuck when every header is unreadable (a bye week shows only BYE WEEK; the season loop passes
    allow_unreadable and counts instead)."""
    frame, _ = pilot.look()
    head = read_header(frame)
    if head is None:
        with patience(pilot, 120, 1.0):
            frame = pilot.goto("HUB_GAMEDAY")
        head = read_header(frame)
    if head is None:
        with patience(pilot, 20, 1.0):
            frame = pilot.goto("HUB_WEEKLY")
        head = read_header(frame)
    if head is None and not allow_unreadable:
        pilot.stop("can't read the week header (2026. WEEK N) on the hub", Stuck)
    return head


def _snapshot(save_path):
    import tempfile
    from pipeline.watch_save import snapshot_save
    folder = tempfile.mkdtemp(prefix="pilot_week_")
    try:
        snap = snapshot_save(str(save_path), folder)
        with open(snap) as fh:
            return json.load(fh)
    finally:
        shutil.rmtree(folder, ignore_errors=True)


def _save_week(save_path):
    """The save's current week from the save file (the fallback when the hub shows BYE WEEK)."""
    from pipeline.load_save import current_week
    try:
        return current_week(_snapshot(save_path))
    except Exception:  # noqa: BLE001 - a fallback; the hub header remains the first source
        return None


PLAYOFF_PHASES = {"wildcardplayoff": "WILDCARD", "divisionalplayoff": "DIVISIONAL",
                  "conferenceplayoff": "CONFERENCE", "conferencechampionship": "CONFERENCE",
                  "superbowl": "SUPER BOWL", "preseason": "PRESEASON"}


def _save_header(save_path):
    """The save's own week/phase (the fallback when the postseason hub shows no header at all)."""
    from pipeline.load_save import current_week
    try:
        snap = _snapshot(save_path)
    except Exception:  # noqa: BLE001
        return None
    s = snap.get("season") or {}
    year = s.get("calendar_year")
    if str(s.get("stage") or "").lower() == "offseason":
        return {"year": year, "phase": "OFFSEASON", "week": None}
    week = current_week(snap)
    if week is not None:
        return {"year": year, "phase": "WEEK", "week": week}
    wt = str(s.get("week_type") or "")
    if wt:
        return {"year": year, "phase": PLAYOFF_PHASES.get(wt.lower(), wt.upper()), "week": None}
    return None


def _latest_save(save_path, auto_path):
    return auto_path if (auto_path.is_file() and (_mtime(auto_path) or 0) > (_mtime(save_path) or 0)) else save_path


def _settled_highlight(pilot, options, tries=8, wait=0.5):
    """The highlighted option once two looks in a row agree, or the last read."""
    last = None
    for _ in range(tries):
        cur = pilot.highlighted(labels=options)
        if cur is not None and cur == last:
            return cur
        last = cur
        pilot.sleep(wait)
        pilot.look()
    return last


def _move_highlight(pilot, label, options, presses=4):
    """Move onto `label` one verified press at a time.

    A single arrow press is sometimes lost (focus switch) and a second press on a stale frame
    overshoots (READY TO ADVANCE -> FORCE ADVANCE -> SIM TO MIDSEASON, JR 2026-11-10), so each
    press is followed by a settled re-read before another is allowed.
    """
    for _ in range(presses):
        cur = _settled_highlight(pilot, options)
        if cur == label:
            return True
        if cur not in options:
            return False
        pilot.press("down" if options.index(label) > options.index(cur) else "up")
        pilot.sleep(0.8)
        pilot.look()
    return _settled_highlight(pilot, options) == label


def _sim_one_week(pilot, poll=3.0):
    """GAMEDAY > ADVANCE WEEK > FORCE ADVANCE (READY TO ADVANCE can't run an all-user league),
    then wait out the sim."""
    pilot.goto("ADVANCE_MENU")
    if pilot.frame.has("FORCE ADVANCE"):
        label = "FORCE ADVANCE"
        if not _move_highlight(pilot, label, ["READY TO ADVANCE", "FORCE ADVANCE", "SIM TO MIDSEASON"]):
            pilot.stop("couldn't settle the highlight on FORCE ADVANCE", Stuck)
    elif pilot.frame.has("NEXT WEEK"):
        label = "NEXT WEEK"
        pilot.highlight(label)
    else:
        label = "READY TO ADVANCE"
        pilot.highlight(label)
    pilot.confirm_on("ADVANCE_MENU", label)
    others = [n for n in pilot.screens if n != "ADVANCE_MENU"]
    _, screen = wait_known(pilot, others + ["FORCE_ADVANCE_CONFIRM"], SIM_TIMEOUT, poll, what=f"the end of {label}")
    if screen == "FORCE_ADVANCE_CONFIRM":
        if not _move_highlight(pilot, "SIM AHEAD", ["SIM AHEAD", "CANCEL"]):
            pilot.stop("couldn't settle the highlight on SIM AHEAD", Stuck)
        pilot.confirm_on("FORCE_ADVANCE_CONFIRM", "SIM AHEAD")
        rest = [n for n in pilot.screens if n != "FORCE_ADVANCE_CONFIRM"]
        _, screen = wait_known(pilot, rest, SIM_TIMEOUT, poll, what="the end of SIM AHEAD")
    return screen


def _done(kind, n, head):
    if kind == "week":
        return head["phase"] == "WEEK" and head["week"] >= n
    if kind == "regular-season-end":
        return head["phase"] != "WEEK"
    # playoffs-end: past the post-season phases (todo: those headers aren't catalogued yet)
    return head["phase"] != "WEEK" and any(w in head["phase"] for w in OFFSEASON_WORDS)


def save_tiles(folder=None):
    """The franchise list's tiles in Madden's order: save files newest first, name minus CAREER-.

    Autosaves are tiles of their own. JR, 2026-11-10: the list is ordered by save recency, so the
    position of a wanted tile is computable from the saves folder."""
    folder = Path(folder) if folder else saves_dir()
    files = [p for p in folder.glob(CAREER_PREFIX + "*") if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [tile_name(p.name) for p in files]


def tile_index(save, folder=None):
    """The 0-based position of `save`'s tile in the franchise list, or None when it isn't there."""
    try:
        return save_tiles(folder).index(tile_name(save))
    except ValueError:
        return None


def _load_franchise(pilot, tile, save=None, folder=None):
    # Title -> main menu and the franchise load are long UNKNOWN splashes offline; goto's default
    # 20-look UNKNOWN limit (about 40 s) is too short for them.
    frame, screen = pilot.look()
    for _ in range(8):                       # the mode rail may be on Ultimate Team or another mode
        if screen != "MAIN_MENU_RAIL":
            break
        pilot.press("down")
        frame, screen = pilot.look()
    with patience(pilot, 180, 1.0):
        pilot.goto("FRANCHISE_LIST")
    # The list opens wherever it was last left (and wraps). Walk one tile at a time: one lap left,
    # then right if the target was on the other side (the dry-run's fake list does not wrap).
    lap = len(save_tiles(folder)) + 1
    found = False
    for direction, steps in (("left", lap), ("right", 2 * lap)):
        for _ in range(steps):
            if pilot.highlighted(labels=[tile]) == tile:
                found = True
                break
            pilot.press(direction)
            pilot.look()
        if found:
            break
    if not found:
        pilot.stop(f"couldn't find the {tile} tile in CURRENT FRANCHISES", Stuck)
    pilot.confirm_on("FRANCHISE_LIST", tile)
    # the dialog may take a moment; CURRENT FRANCHISES stays behind it and behind the loading frames
    _, screen = wait_known(pilot, ["PROGRESSION_WARNING", *HUBS], 60)
    if screen == "PROGRESSION_WARNING":
        pilot.highlight("PROCEED WITHOUT PROGRESSION")
        pilot.confirm_on("PROGRESSION_WARNING", "PROCEED WITHOUT PROGRESSION")
        wait_known(pilot, list(HUBS), LOAD_TIMEOUT)


def _savepoint(pilot, name=None):
    """No savepoint step: Madden writes <save>-AUTOSAVE after every advance, and Tab opens the
    Save-As dialog (which can overwrite the wrong file) — JR, 2026-11-10: never press Tab."""
    return "autosave"


def _mtime(path):
    try:
        return path.stat().st_mtime
    except OSError:
        return None


@job("sim-season")
def sim_season(pilot, save=None, to="regular-season-end", watcher=None, folder=None, quit=True, progress=None,
               franchise=None):
    """Load CAREER-TUNE-<name> from the franchise list, sim with NEXT WEEK until `to`, create a
    savepoint, and quit back to the franchise list. Refuses other saves and a running watcher
    before touching the PC. `franchise` (a TUNE-* tile name) says that franchise is already
    loaded: sim it in place instead of quitting to the list and loading it again."""
    if not save or not str(save).upper().startswith(TUNE_SAVE_PREFIX) or not _valid_save_name(save):
        return _refuse(f"only {TUNE_SAVE_PREFIX}* saves are simmed, not {save!r}", save=save)
    if franchise and not norm(tile_name(franchise)).startswith("TUNE"):
        return _refuse(f"only a TUNE-* franchise may be simmed in place, not {franchise!r}", save=save)
    try:
        kind, n = parse_target(to)
    except ValueError as e:
        return _refuse(str(e), save=save)
    if (watcher or watcher_running)():
        return _refuse("pipeline.watch_save is running: stop it first (a clone keeps the real "
                       "franchise's LeagueID, so the watcher would ingest it)", save=save)
    folder = Path(folder) if folder else saves_dir()
    save_path, auto_path = folder / save, folder / f"{save}-AUTOSAVE"
    if folder.is_dir() and not save_path.is_file() and not getattr(pilot, "dry_run", False):
        return _refuse(f"no save {save} in {folder} (clone-save first)", save=save)
    before = {"save": _mtime(save_path), "autosave": _mtime(auto_path)}
    tile = tile_name(save)
    progress.update(save=save, to=to, weeks_simmed=0)

    if franchise:
        # The operator says this franchise is already loaded: go straight to its hub. Checking
        # the screen here is wrong — the Advance menu is inside a franchise but is not a hub.
        pilot.loaded_franchise = norm(tile_name(franchise))
    else:
        _load_franchise(pilot, tile, save=save, folder=folder)
    head = _week_now(pilot, allow_unreadable=True)
    if head is None:
        head = _save_header(_latest_save(save_path, auto_path))
    if head is None:
        week = _save_week(_latest_save(save_path, auto_path))
        if week is None:
            pilot.stop("can't read the week from the hub or the save", Stuck)
        head = {"year": None, "phase": "WEEK", "week": week}
    progress.update(start_week=head.get("week"), start_phase=head["phase"], end_week=head.get("week"))
    weeks = 0
    while not _done(kind, n, head):
        if weeks >= MAX_SIM_WEEKS:
            pilot.stop(f"simmed {weeks} weeks without reaching {to}", Stuck)
        _sim_one_week(pilot)
        weeks += 1
        new = _week_now(pilot, allow_unreadable=True)
        if new is None:
            new = _save_header(_latest_save(save_path, auto_path))
        if new is None:
            # A bye week's hub shows only BYE WEEK: the advance moved exactly one week.
            if head.get("week") is None:
                pilot.stop("the hub has no week header and the week is unknown", Stuck)
            new = {"year": head.get("year"), "phase": "WEEK", "week": head["week"] + 1}
        if new["phase"] == "WEEK" and new.get("week") and new["week"] > 18:
            new = {"year": new.get("year"), "phase": "POSTSEASON", "week": None}
        if new == head:
            pilot.stop(f"the header didn't move the week off {head}", Stuck)
        head = new
        progress.update(weeks_simmed=weeks, end_week=head.get("week"), end_phase=head["phase"])
    saved = _savepoint(pilot, tile_name(save).removesuffix("-AUTOSAVE"))
    if not save.upper().endswith("-AUTOSAVE") and auto_path.is_file() and \
            (_mtime(auto_path) or 0) > (_mtime(save_path) or 0):
        # JR: to make a real save, copy the autosave over the named save (no Tab).
        shutil.copy2(auto_path, save_path)
        saved = "autosave (copied to the named save)"
    progress.update(saved=saved)
    if quit:
        pilot.goto("FRANCHISE_LIST")
    after = {"save": _mtime(save_path), "autosave": _mtime(auto_path)}
    return {"status": "ok", "save": save, "to": to, "save_path": str(save_path), "autosave_path": str(auto_path),
            "start_week": progress.get("start_week"), "end_week": head.get("week"), "end_phase": head["phase"],
            "weeks_simmed": weeks, "saved": saved, "quit": quit,
            "written": {k: (after[k] is not None and after[k] != before[k]) for k in after}}


# ------------------------------------------------------------------------------- set_setting
def _row_value(frame, label, value):
    """True when `value` reads on the same row as `label` (one OCR line, or a box whose
    vertical centre is within the label's height)."""
    if any(label_match(ln, f"*{norm(label)}*{norm(value)}*") for ln in frame.lines):
        return True
    a, b = frame.find(label), frame.find(value)
    if not a or not b:
        return False
    return abs((a[1] + a[3] / 2) - (b[1] + b[3] / 2)) <= max(a[3], b[3]) and b[0] > a[0]


def _known(pilot, label):
    frame, screen = pilot.look()
    if screen == UNKNOWN:
        pilot.stop(f"set-setting: this screen isn't in the catalog (looking for {label!r}); "
                   f"add it, then retry", Unknown)
    if not frame.has(label):
        pilot.stop(f"set-setting: {label!r} isn't on {screen}", Unknown)
    return frame, screen


@job("set-setting")
def set_setting(pilot, path=None, value=None, max_steps=20, progress=None):
    """Walk a '>'-separated menu path by OCR labels (each hop: highlight the label, confirm it
    on a catalogued screen whose confirm_ok allows it), then press Right on the last label until
    its row reads `value`, and back out to where it started. Stops as Unknown on screens that
    aren't catalogued (no settings screen is yet)."""
    parts = [p.strip() for p in str(path or "").split(">") if p.strip()]
    if not parts or value in (None, ""):
        return _refuse("set-setting needs --path 'A>B>Setting' and --value V", path=path)
    progress.update(path=path, value=value, walked=[])
    _, start = pilot.look()
    if start == UNKNOWN:
        pilot.stop("set-setting: the starting screen isn't in the catalog", Unknown)
    for label in parts[:-1]:
        _, screen = _known(pilot, label)
        spec = pilot.spec(screen)
        if spec.get("highlight") and (spec.get("options") or spec.get("nav")):
            pilot.highlight(label)
        pilot.confirm_on(screen, label)
        pilot._settle(screen, 30, 1.0)
        progress["walked"].append(label)
    final = parts[-1]
    frame, screen = _known(pilot, final)
    if pilot.spec(screen).get("highlight"):
        pilot.highlight(final)
        frame = pilot.frame
    presses = 0
    while not _row_value(frame, final, value):
        if presses >= max_steps:
            pilot.stop(f"{final} never read {value!r} after {presses} presses", Stuck)
        pilot.press("right")
        presses += 1
        frame, now = pilot.look()
        if now != screen:
            pilot.stop(f"changing {final} left {screen} for {now}", Stuck)
    progress.update(presses=presses)
    if start != screen:
        pilot.goto(start)
    return {"status": "ok", "path": path, "value": value, "presses": presses, "screen": screen,
            "back_on": pilot.screen}


# ------------------------------------------------------------------------------- verify_mods
def load_check(name):
    """tools/madden_pilot/checks/<name>.py; the name is a plain module name."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name or ""):
        raise ValueError(f"bad check name {name!r}")
    mod = importlib.import_module(f"tools.madden_pilot.checks.{name}")
    if not callable(getattr(mod, "run", None)):
        raise ValueError(f"check {name} has no run(pilot)")
    return mod


@job("verify-mods")
def verify_mods(pilot, check=None, progress=None):
    """Run checks/<check>.py's run(pilot) -> {"status": pass|fail|unknown, ...}."""
    try:
        mod = load_check(check)
    except (ValueError, ImportError) as e:
        return _refuse(str(e), check=check)
    progress.update(check=check)
    out = dict(mod.run(pilot) or {})
    status = out.get("status") if out.get("status") in ("pass", "fail", "unknown") else "unknown"
    return {**out, "status": status, "check": check}


# ------------------------------------------------------------------------------- quit
@job("quit")
def quit_franchise(pilot, franchise=None, allow_quit=False, progress=None):
    """Back out of the loaded franchise to the franchise list (Back > YES). The hub never shows
    the save name, so a fresh pilot doesn't know what is loaded: pass franchise="TUNE-x" when you
    know, or allow_quit=True after JR says yes (quitting writes <save>-AUTOSAVE)."""
    if franchise:
        pilot.loaded_franchise = norm(tile_name(franchise))
    _, screen = pilot.look()
    if screen == "FRANCHISE_LIST":
        return {"status": "ok", "screen": screen, "quit": False, "why": "already on the franchise list"}
    pilot.goto("FRANCHISE_LIST", allow_quit=allow_quit)
    return {"status": "ok", "screen": "FRANCHISE_LIST", "quit": True, "franchise": pilot.loaded_franchise}


JOBS = {"launch": launch, "clone-save": clone_save, "sim-season": sim_season, "set-setting": set_setting,
        "verify-mods": verify_mods, "quit": quit_franchise}
