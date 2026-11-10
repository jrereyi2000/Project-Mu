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

# Every screen a loaded franchise can open on. A live event takes the focus on load: at the Free
# agency frenzy Madden opens the LIVE EVENT tab, not GAMEDAY (2026-11-10).
HUBS = ("HUB_WEEKLY", "HUB_GAMEDAY", "HUB_OTHER", "HUB_WEEKLY_DRAFT_CARD", "HUB_BYE", "LIVE_EVENT_FRENZY",
        "LIVE_EVENT_FRENZY_MODAL", "FREE_AGENCY_ENDED", "DRAFT_HUB", "DRAFT_LAUNCH")
MAX_SIM_WEEKS = 30                 # safety cap on NEXT WEEK presses in one run
ADVANCE_STOP_TIMEOUT = 90          # seconds an `advance` waits for a catalogued landing screen
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
               "CONFERENCE CHAMPIONSHIP", "CHAMPIONSHIP SUNDAY", "DIVISIONAL", "WILDCARD")

# Madden's eight offseason stops by the save's week_index (JR's walkthrough, MADDEN_OFFSEASON_WALKTHROUGH.md);
# the stage numbers in between are internal and never shown as a stop.
OFFSEASON_STOPS = {0: "STAFF", 1: "RE-SIGN", 2: "FA_PREP", 3: "FREE_AGENCY", 5: "COMBINE", 7: "PRO_DAY",
                   11: "DRAFT", 15: "RECAP_UDFA"}

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
PRUNER = ROOT / "pipeline" / "save_reader" / "prune_capacity.mjs"


def prune_capacity(save, out, timeout=180):
    """Shrink a cloned save's empty table capacity: Highlight 3,200 rows down to 512 frees
    582.8 KB of the 20 MiB unpacked limit the tuning season used to truncate at, and was
    proved in game through the 2026 and 2027 seasons (docs/in_progress/SAVE_CAPACITY_PRUNE_PLAN.md
    §9). Returns (pruned, detail) and never raises: a save that is not an FBCHUNKS file, a
    missing node or a refused rebuild leaves the caller's copy untouched."""
    node = shutil.which("node")
    if node is None:
        return False, "node not on PATH"
    try:
        with open(save, "rb") as fh:
            if fh.read(8) != b"FBCHUNKS":
                return False, "not an FBCHUNKS save"
    except OSError as exc:
        return False, f"unreadable: {exc}"
    report = out.with_suffix(".report.json")
    try:
        proc = subprocess.run(
            [node, str(PRUNER), str(save), "--out", str(out), "--report", str(report)],
            capture_output=True, text=True, timeout=timeout, cwd=str(ROOT),
        )
        if proc.returncode != 0 or not out.is_file():
            lines = (proc.stderr or proc.stdout or "no output").strip().splitlines()
            return False, f"pruner failed: {lines[-1] if lines else 'no output'}"
        data = json.loads(report.read_text()) if report.is_file() else {}
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return False, f"pruner not run: {exc}"
    finally:
        if report.is_file():
            report.unlink()
    saved = data.get("saved", 0)
    return True, f"{len(data.get('tables', []))} tables, {saved} bytes freed"


@job("clone-save")
def clone_save(pilot, src=BASELINE_SAVE, dst=None, folder=None, dry_run=False, progress=None):
    """Copy save `src` to `dst` inside MADDEN_SAVES_DIR. `dst` must be CAREER-TUNE-<name>; an
    existing CAREER-TUNE-* file is replaced, anything else never is. The copy gets a fresh
    mtime, so Madden lists it first. The copy is then capacity-pruned (prune_capacity) so
    every tuning run starts with the headroom the season needs; a refused prune leaves the
    plain copy and reports itself in the result."""
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
    pruned_tmp = folder / f".mu-pilot-clone-{os.getpid()}.pruned.tmp"
    try:
        shutil.copyfile(src_path, tmp)
        pruned, prune_detail = prune_capacity(tmp, pruned_tmp)
        if not pruned:
            print(f"WARNING: capacity prune skipped ({prune_detail}); {dst} keeps its full table capacity",
                  flush=True)
        os.replace(pruned_tmp if pruned else tmp, dst_path)
    finally:
        for leftover in (tmp, pruned_tmp):
            if leftover.exists():
                leftover.unlink()
    return {**out, "copied": True, "pruned": pruned, "prune": prune_detail}


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
    frame, screen = pilot.look()
    if screen == "HUB_BYE":
        return None                  # a bye week's hub carries no week number at all
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
        idx = s.get("week_index")
        return {"year": year, "phase": "OFFSEASON", "week": None, "stage": idx,
                "stop": OFFSEASON_STOPS.get(idx, f"OFFSEASON_WEEK_{idx}")}
    if str(s.get("week_type") or "") == "PreSeason":
        # Madden's preseason hub header reads "2027. WEEK 1", the same shape as a regular-season
        # week, so the save is the only thing that can tell them apart; the stage is the week.
        idx = s.get("week_index")
        return {"year": year, "phase": "PRESEASON", "week": None, "stage": idx,
                "stop": f"PRESEASON_{idx}"}
    week = current_week(snap)
    if week is not None:
        return {"year": year, "phase": "WEEK", "week": week}
    wt = str(s.get("week_type") or "")
    if wt:
        return {"year": year, "phase": PLAYOFF_PHASES.get(wt.lower(), wt.upper()), "week": None}
    return None


def _stop_key(head):
    """What a stop is, for "did the advance move?" — the header, the week, or the offseason stage."""
    if not head:
        return None
    return (head.get("year"), head.get("phase"), head.get("week"), head.get("stage"))


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


def _sim_one_week(pilot, poll=3.0, timeout=SIM_TIMEOUT):
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
    _, screen = wait_known(pilot, others + ["FORCE_ADVANCE_CONFIRM"], timeout, poll, what=f"the end of {label}")
    if screen == "FORCE_ADVANCE_CONFIRM":
        if not _move_highlight(pilot, "SIM AHEAD", ["SIM AHEAD", "CANCEL"]):
            pilot.stop("couldn't settle the highlight on SIM AHEAD", Stuck)
        pilot.confirm_on("FORCE_ADVANCE_CONFIRM", "SIM AHEAD")
        rest = [n for n in pilot.screens if n != "FORCE_ADVANCE_CONFIRM"]
        _, screen = wait_known(pilot, rest, timeout, poll, what="the end of SIM AHEAD")
    return screen


DRAFT_SCREENS = ("DRAFT_HUB", "DRAFT_LAUNCH")
DRAFT_SIM_TIMEOUT = 600            # seconds the draft sim runs (JR: the draft only advances by simming it)


def _simulate_draft(pilot, timeout=DRAFT_SIM_TIMEOUT, poll=4.0):
    """Draft week: open the DRAFT tab, start the draft, take SIMULATE DRAFT, and wait for the next
    hub. Simming the draft IS the advance — Madden moves to the recap stop itself, so no ADVANCE
    WEEK is pressed. Project Mu has already written its picks into the save (TUNING_LEAGUE.md §6)."""
    frame, screen = pilot.look()
    if screen == "DRAFT_HUB":
        pilot.confirm_on("DRAFT_HUB", "START THE NFL DRAFT", wait=2.5)
        frame, screen = pilot.look()
    if screen == "DRAFT_LAUNCH":
        # Windows OCR reads the button as SIMULATEDRAFT / SIMULATEDUFT, so it cannot be found by
        # name; it is one Down from the selected START DRAFTING. The border sits between the two
        # stacked options and cannot prove which one owns it, so the label check is skipped and
        # the press is one verified Down (DRAFT_LAUNCH has highlight null for that reason).
        pilot.press("down")
        pilot.confirm_on("DRAFT_LAUNCH", "SIMULATE DRAFT", wait=5.0)
    # the draft runs a minute or two behind a Simulating frame; then the recap hub appears
    others = [n for n in pilot.screens if n not in DRAFT_SCREENS]
    return wait_known(pilot, others, timeout, poll, what="the end of the draft")


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


def _verify_loaded(pilot, save, folder):
    """Stop unless the franchise now on screen is the save we meant to load.

    A lost arrow press, a stale border read or a hand on the keyboard can confirm the wrong
    tile: on 2026-11-10 a list walk loaded the previous save (2028 week 1) while the pilot
    believed it had opened CAREER-TUNE-P2C (2026 week 1) and would have simmed that season.
    The hub header carries the year and, in season, the week; the save file carries both, so
    they must agree. Screens without a header (a postseason phase, a loading frame) are left to
    the caller's own header read.
    """
    if getattr(pilot, "dry_run", False):
        return None                     # the fake Madden has no save file behind it
    head = read_header(pilot.frame)
    disk = _save_header(_latest_save(Path(folder) / save, Path(folder) / f"{save}-AUTOSAVE")) if folder else None
    if not head or not disk:
        return None
    same_week = head.get("phase") == disk.get("phase") == "WEEK" and head.get("week") == disk.get("week")
    same_year = head.get("year") is None or disk.get("year") is None or head["year"] == disk["year"]
    if same_year and (same_week or head.get("phase") != "WEEK" or disk.get("phase") != "WEEK"):
        return None
    pilot.stop(f"the loaded franchise is {head.get('year')} {head.get('phase')} "
               f"{head.get('week') if head.get('phase') == 'WEEK' else ''}, but {save} holds "
               f"{disk.get('year')} {disk.get('phase')} "
               f"{disk.get('week') if disk.get('phase') == 'WEEK' else ''}: refusing to sim the wrong save",
               Stuck)


def _walk_to_tile(pilot, tile, folder):
    """Put the CURRENT FRANCHISES border on `tile`; True when it is there.

    The list opens wherever it was last left. Madden orders the tiles by the save's own date, so
    the saves folder is only a bound and the border is the truth: press one arrow, look, and stop
    the moment the target is bordered. The first direction comes from the two labels' x positions
    when both are on screen; a press that leaves the border where it was means an edge of the
    list, and then it goes the other way (2026-11-10: a wrong-way jump swept 60 saves twice and
    looked like a hung run).
    """
    order = save_tiles(folder)
    bound = len(order) + 3

    def on_target():
        return pilot.highlighted(labels=[tile]) == tile

    def current():
        return pilot.highlighted(labels=order)

    if on_target():
        return True
    a, b = pilot.frame.find(current()) if current() else None, pilot.frame.find(tile)
    directions = ("left", "right") if a and b and b[0] < a[0] else ("right", "left")
    for key in directions:
        still = current()
        for _ in range(bound):
            pilot.press(key)
            pilot.look()
            if on_target():
                return True
            now = current()
            if now is not None and still is not None and now == still:
                break                   # the border can't move this way: an edge of the list
            if now is not None:
                still = now
    return on_target()


MAIN_MENU_SCREENS = ("MAIN_MENU", "MAIN_MENU_RAIL", "TITLE_SCREEN")


def _leave_to_main_menu(pilot, max_backs=3):
    """Back out of the franchise screens to the main menu; the last screen looked at is returned.

    CURRENT FRANCHISES only rebuilds on a fresh entry from the main menu: a clone made while the
    list was open stays missing after one Back and a re-entry from the Franchise landing page
    (JR, 2026-11-10). Bounded, and a screen that Back won't leave is returned rather than guessed
    at, so the caller's goto fails with a screenshot instead of pressing blind."""
    for _ in range(max_backs):
        _, screen = pilot.look()
        if screen in MAIN_MENU_SCREENS:
            return screen
        pilot.press("back")
    return pilot.look()[1]


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
    found = _walk_to_tile(pilot, tile, folder)
    if not found:
        # CURRENT FRANCHISES is built when the screen opens and a clone made while it was already
        # up does not appear on re-entry from the landing page either: the list must be left for
        # the main menu and entered again (JR, 2026-11-10: the p2g run walked the list and never
        # saw TUNE-P2G).
        _leave_to_main_menu(pilot)
        with patience(pilot, 180, 1.0):
            pilot.goto("FRANCHISE_LIST", timeout=180)
        found = _walk_to_tile(pilot, tile, folder)
    if not found:
        pilot.stop(f"couldn't find the {tile} tile in CURRENT FRANCHISES", Stuck)
    pilot.confirm_on("FRANCHISE_LIST", tile)
    # the dialog may take a moment; CURRENT FRANCHISES stays behind it and behind the loading frames
    _, screen = wait_known(pilot, ["PROGRESSION_WARNING", *HUBS], 60)
    if screen == "PROGRESSION_WARNING":
        pilot.highlight("PROCEED WITHOUT PROGRESSION")
        pilot.confirm_on("PROGRESSION_WARNING", "PROCEED WITHOUT PROGRESSION")
        wait_known(pilot, list(HUBS), LOAD_TIMEOUT)
    if save:
        _verify_loaded(pilot, save, folder or saves_dir())


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
        pre = _mtime(_latest_save(save_path, auto_path))
        _sim_one_week(pilot)
        weeks += 1
        new = _week_now(pilot, allow_unreadable=True)
        if new is None and head.get("week") is not None and (head.get("phase") == "WEEK"):
            # A bye week's hub shows nothing but "BYE WEEK", so the week can't be read from it.
            # The advance still moved exactly one week, and Madden wrote the autosave: the file's
            # mtime answers "did it move?" for free, where a reader snapshot costs a minute
            # (2026-11-10, ~1.2 min/week on a bye week vs ~25 s otherwise).
            post = _mtime(_latest_save(save_path, auto_path))
            if post is not None and pre is not None and post > pre:
                new = {"year": head.get("year"), "phase": "WEEK", "week": head["week"] + 1}
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


# ------------------------------------------------------------------------------- advance
@job("advance")
def advance(pilot, save=None, franchise=None, quit=True, watcher=None, folder=None, progress=None):
    """Advance a CAREER-TUNE-* franchise exactly ONE stop: a regular-season week, a playoff round or
    one offseason stage (P2-03). It reads the stop before and after, copies Madden's autosave over
    the named save, and quits to the franchise list so the league office can write the file.

    One advance is one press of the advance action: the stage loop never sims through the
    offseason (TUNING_LEAGUE.md §6). `franchise` says the TUNE-* franchise is already loaded."""
    if not save or not str(save).upper().startswith(TUNE_SAVE_PREFIX) or not _valid_save_name(save):
        return _refuse(f"only {TUNE_SAVE_PREFIX}* saves are advanced, not {save!r}", save=save)
    if franchise and not norm(tile_name(franchise)).startswith("TUNE"):
        return _refuse(f"only a TUNE-* franchise may be advanced in place, not {franchise!r}", save=save)
    if (watcher or watcher_running)():
        return _refuse("pipeline.watch_save is running: stop it first (a clone keeps the real "
                       "franchise's LeagueID, so the watcher would ingest it)", save=save)
    folder = Path(folder) if folder else saves_dir()
    save_path, auto_path = folder / save, folder / f"{save}-AUTOSAVE"
    if folder.is_dir() and not save_path.is_file() and not getattr(pilot, "dry_run", False):
        return _refuse(f"no save {save} in {folder} (clone-save first)", save=save)
    if franchise:
        pilot.loaded_franchise = norm(tile_name(franchise))
    else:
        _load_franchise(pilot, tile_name(save), save=save, folder=folder)
    frame, screen0 = pilot.look()
    before_save = _save_header(_latest_save(save_path, auto_path))
    # in the offseason the hub usually carries no phase word and its cards say things like "STAFF
    # SHAKE", so the save's own stage is the stop; the hub header is only used in season.
    before = before_save if (before_save or {}).get("phase") in ("OFFSEASON", "PRESEASON") \
        else (read_header(frame) or before_save)
    if screen0 not in HUBS:
        pilot.stop(f"advance: {screen0} is not a hub; no advance route is catalogued for it", Unknown)
    progress.update(save=save, from_screen=screen0, from_stop=before)
    if screen0 in DRAFT_SCREENS:
        _simulate_draft(pilot)
    else:
        # a new offseason screen is the point of P2-02, so stop after a minute rather than four
        _sim_one_week(pilot, timeout=ADVANCE_STOP_TIMEOUT)
        if pilot.screen in DRAFT_SCREENS:
            # the advance from Pro day lands ON draft week: simming the draft is the advance
            # (2026-11-10: quitting off DRAFT_HUB stopped the season run with "no route")
            _simulate_draft(pilot)
    # the sim lands on the coach-progress tabs first; read the header off the hub behind them.
    # Madden writes the new stop to the autosave, so the stop is read from the newest file: the
    # named save is only overwritten below.
    after_save = _save_header(_latest_save(save_path, auto_path))
    if (after_save or {}).get("phase") in ("OFFSEASON", "PRESEASON"):
        after = after_save
        pilot.look()
    else:
        after = _week_now(pilot, allow_unreadable=True) or after_save
    landing = pilot.screen
    if _stop_key(before_save) is not None and _stop_key(after_save) == _stop_key(before_save):
        pilot.stop("the advance left the stop where it was "
                   f"({(before_save or before).get('stop') or (before_save or before).get('phase')})", Stuck)
    written = {}
    if not save.upper().endswith("-AUTOSAVE") and auto_path.is_file() and \
            (_mtime(auto_path) or 0) > (_mtime(save_path) or 0):
        shutil.copy2(auto_path, save_path)          # Madden's autosave is the save (never Tab)
        written = {"save": True}
    if quit:
        pilot.goto("FRANCHISE_LIST")
    return {"status": "ok", "save": save, "franchise": pilot.loaded_franchise, "from": before,
            "to": after, "from_screen": screen0, "to_screen": landing, "quit": quit,
            "written": written, "saved": "autosave"}


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


JOBS = {"launch": launch, "clone-save": clone_save, "sim-season": sim_season, "advance": advance,
        "set-setting": set_setting, "verify-mods": verify_mods, "quit": quit_franchise}
