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
    sim_season(pilot, save="CAREER-MU<name>", to="week N", managed=True)
    advance(pilot, save="CAREER-TUNE-<name>")
    watch_game(pilot, save="CAREER-TUNE-<name>", to="kickoff" | "halftime" | "supersim")
    catch_up(pilot, save="CAREER-<name>", to_week=None, dsn=None)
    set_setting(pilot, path="A>B>C", value="V")
    verify_mods(pilot, check="first_down_line")
    quit_franchise(pilot, allow_quit=False)

Safety (plan D1, S7, section 2.5): saves are only cloned to, and only simmed as, CAREER-TUNE-*;
the clone never overwrites a file that isn't CAREER-TUNE-*; sim_season refuses while
pipeline.watch_save runs (a clone keeps the original's LeagueID, so the watcher would ingest it
into the real franchise database); quitting a franchise that isn't TUNE-* is refused unless
allow_quit (quitting writes <save>-AUTOSAVE).

The managed-save allowance (CLOSE_OUT_CHECKLIST P7-00e, JR 2026-11-12) adds one explicit second
class and loosens nothing: sim_season also accepts a CAREER-MU* save, but only when the caller
passes managed=True (`--managed`), and CAREER-MUBASE is refused even then because it is the
hash-pinned baseline that must never be simulated (P7-00a). Without --managed a CAREER-MU* save
is refused exactly as before, and every other name is refused with or without it. clone_save,
advance and watch_game stay CAREER-TUNE-* only.

catch_up is the opposite of sim_season (NFL_WEEKLY_MIRROR_PLAN.md section 2.12): the real
CAREER-* franchise, with pipeline.watch_save running. It sims one week, saves, quits to the main
menu so the franchise is closed, waits for the league office to write that week's mirror, and
repeats until the first LIVE week (or --to-week). It refuses a CAREER-TUNE-* clone, a stopped
watcher, a disabled mirror and a next week that is not CATCH_UP.

watch_game is the one job allowed to start a season game (VISUAL_MOD_CHECK_PLAN.md section 2.5):
it presses PLAY SEASON GAME through the pilot's own policy (the catalog's HUB_GAMEDAY still
allows ADVANCE WEEK alone), walks the pregame to kickoff, writes `ready.json` immediately before
its hold, and holds still while a caller records. --to halftime walks JR's D2 supersim recipe to
the last play of the half; --to supersim holds on the positioned SUPERSIM_MENU. It never calls
a play except that half's last snap.
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

from tools.madden_pilot.navigate import ROOT, TITLE, PilotStop, Stuck, Unknown, same_option
from tools.madden_pilot.screens import UNKNOWN, label_match, norm

DEFAULT_SAVES_DIR = "/mnt/c/Users/jrere/Documents/Madden NFL 27/saves/"
TUNE_SAVE_PREFIX = "CAREER-TUNE-"
MANAGED_SAVE_PREFIX = "CAREER-MU"      # the canonical franchise's managed saves (CAREER-MU2026)
PINNED_BASELINE_SAVE = "CAREER-MUBASE"  # hash-pinned; never simulated (P7-00a)
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
# What a launch may land on. The main menu's Franchise panel reads as FRANCHISE_LANDING, and a
# one-mode menu (Mu Franchise Only) has no other panel, so it reads as FRANCHISE_LANDING too
# (live 2026-11-11 on Mu Test: Back on it opens EXIT MADDEN). FRANCHISE_LIST is a warm start.
MENU_SCREENS = ("MAIN_MENU", "FRANCHISE_LANDING", "FRANCHISE_LIST", *HUBS)
# The catalogued screens a cold boot shows before the menu, in order: the MMC warning, the
# EA/MADDEN splash and the title. One Space takes each (the warning's OK is its default).
BOOT_SCREENS = ("BOOT_SPLASH", "TITLE_SCREEN")
MAX_SIM_WEEKS = 30                 # safety cap on NEXT WEEK presses in one run
ADVANCE_STOP_TIMEOUT = 90          # seconds an `advance` waits for a catalogued landing screen
SIM_TIMEOUT = 240                  # seconds for one NEXT WEEK (spike: under 20 s)
LOAD_TIMEOUT = 120                 # seconds for a franchise to load (spike: about 25 s)
LAUNCH_TIMEOUT = 300               # seconds for Madden's window and first menu
CATCH_UP_WRITE_TIMEOUT = 15 * 60   # seconds to wait for the league office's WRITTEN mirror week
CATCH_UP_POLL = 5.0                # seconds between nfl_mirror_weeks reads while waiting
MAX_CATCH_UP_WEEKS = 30            # safety cap on one catch-up run (NFL_WEEKLY_MIRROR_PLAN §2.12)
CATCH_UP_SEASON = 2026             # fallback mirror season when the settings row has none
PRO_BOWL_WEEK = 22                 # Madden's no-game stop between the conference and the Super Bowl
PRO_BOWL = "PRO_BOWL"              # a progression stop with no real NFL week and no mirror row

# watch_game (VISUAL_MOD_CHECK_PLAN.md 2.5; the spike S-1 numbers are in the plan's 2.5a).
WATCH_TARGETS = ("kickoff", "halftime", "supersim")   # what watch-game drives to
# The ready marker's cue anchor per target, the name the mod-check driver waits for.
WATCH_ANCHORS = {"kickoff": "kickoff", "halftime": "halftime_start", "supersim": "supersim_menu"}
READY_FILE = "ready.json"                 # written immediately before watch-game's hold
WATCH_MAX_HOLD = 600                      # --hold-seconds' upper bound (the plan's 1..600)
KICK_HOLD = 1.5                           # the kickoff is a Space hold, not a tap (JR, S-1)
PREGAME_TIMEOUT = 90                      # seconds for one pregame press to land
GAME_INTRO_TIMEOUT = 150                  # the unattended broadcast intro is about 40 s (S-1)
PAUSE_TIMEOUT = 30                        # seconds for Escape to open the pause menu
SUPERSIM_TIMEOUT = 30                     # seconds for the pause menu to open the supersim screen
SUPERSIM_SIM_TIMEOUT = 180                # one CHANGE OF POSSESSION runs about 2 min of clock
SUPERSIM_PLAY_TIMEOUT = 60                # one JUMP TO NEXT PLAY moves about 4 s of clock
HALFTIME_WINDOW = 20                      # under 20 s left in Q2 is the half's last play (D2)
HALFTIME_MIN_CLOCK = 1                    # 0:00 means the half is already over: do not resume
HALFTIME_MAX_PLAY_PRESSES = 320           # a quarter at ~4 s a JUMP TO NEXT PLAY is ~225 presses
HALFTIME_BLIND_READS = 10                 # unreadable clocks in a row before the job stops
LEAVE_TIMEOUT = 60                        # seconds for QUIT GAME to land on its confirmation
# Labels that reject, back out of or destroy something: watch-game never confirms one, whatever
# a quit-game dialog turns out to look like (JR, 2026-11-12).
CANCEL_WORDS = ("CANCEL", "NO", "BACK", "DELETE", "TRADE")

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
# The scorebug's game clock: one or two digits, a separator and two digits ("10:21", "0:18").
# The play clock reads ":40" and never matches (it has no leading digit), and neither do the
# passer numbers "53.3" / "72.2" or a down marker's "35".
_CLOCK_RE = re.compile(r"(\d{1,2})\s*[:;.]\s*(\d{2})(?!\d)")
_QUARTER_RE = re.compile(r"\b(1ST|IST|2ND|3RD|4TH|OT)\b")
_QUARTERS = {"1ST": 1, "IST": 1, "2ND": 2, "3RD": 3, "4TH": 4, "OT": "OT"}


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


TICKER_BAND = 0.94          # the bottom Broadcast Ticker starts near here; it is not this game


def _frame_height(frame):
    """The frame's pixel height, from the look's own size or (once) the saved PNG."""
    try:
        if frame.size and len(frame.size) >= 2:
            return float(frame.size[1])
    except TypeError:
        pass
    if frame.png:
        try:
            from PIL import Image
            with Image.open(frame.png) as opened:
                frame.size = [opened.width, opened.height]
            return float(frame.size[1])
        except Exception:                    # no image/Pillow: no positional rule
            return None
    return None


def read_game_clock(frame):
    """The game clock off the scorebug's OCR: {"quarter": 1-4 | "OT", "seconds": s}, or None.

    A line like "1st 10:21" or "2ND 0:18" is the usual read (spike S-1's kickoff frame shows
    "1st 11:00"). The quarter and the clock can also land on two OCR lines of the same row —
    S-1's pause frame read "2nd" and "2:54" beside it — so a quarter word is also paired with a
    clock word to its right on its own row, nearest first. The play clock (":40") is never a
    time: a clock needs one or two digits before its separator, and a bare ":40" has none, and
    a time is never read without a quarter word beside it. OCR-digits are not guessed at, so a
    clock that reads as "O:18" is a miss, not a wrong 0:18 — the job would rather stop.

    **The bottom Broadcast Ticker is never this game's clock** (F12): a frame with an unreadable
    main scorebug ("ost IO:S6") but a lower ticker line "1ST 14:53" used to return the *other*
    game's 14:53. Rows whose words sit in the bottom `TICKER_BAND` are ignored, so that frame
    reads None and the caller stops instead of resuming a half on another game's clock.
    """
    if frame is None:
        return None
    height = _frame_height(frame)
    cutoff = height * TICKER_BAND if height else None

    def in_ticker(box):
        if cutoff is None or not box:
            return False
        return float(box[1]) + float(box[3]) / 2 > cutoff

    for line, row_y in frame.lines_with_rows():
        if cutoff is not None and row_y is not None and row_y > cutoff:
            continue
        up = str(line).upper()
        quarter = _QUARTER_RE.search(up)
        if not quarter:
            continue
        clock = _CLOCK_RE.search(up, quarter.end())
        if clock:
            return {"quarter": _QUARTERS[quarter.group(1)],
                    "seconds": int(clock.group(1)) * 60 + int(clock.group(2))}
    # A row split across two OCR lines: the quarter word and the clock word keep their boxes.
    words = [(str(w.get("text") or "").upper().strip(), w.get("box")) for w in frame.words
             if not in_ticker(w.get("box"))]
    quarters = [(text, box) for text, box in words if box and text in _QUARTERS]
    clocks = [(text, box) for text, box in words if box and _CLOCK_RE.fullmatch(text)]
    best = None
    for qtext, qbox in quarters:
        qx, qy, qw, qh = (float(v) for v in qbox)
        for ctext, cbox in clocks:
            cx, cy, cw, ch = (float(v) for v in cbox)
            gap = cx - (qx + qw)
            same_row = abs((cy + ch / 2) - (qy + qh / 2)) <= max(qh, ch)
            if not (same_row and 0 <= gap <= 250):
                continue
            if best is None or gap < best[0]:
                m, s = _CLOCK_RE.fullmatch(ctext).groups()
                best = (gap, {"quarter": _QUARTERS[qtext], "seconds": int(m) * 60 + int(s)})
    return best[1] if best else None


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
    for k in ("why", "reason", "save", "season", "weeks_simmed", "end_week", "stopped_because", "pack", "dst",
              "check", "route", "minutes"):
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
            progress = {"started": started}
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


def save_name_refusal(save, managed=False, verb="simmed"):
    """Why `save` may not be driven, or None when it may (CLOSE_OUT_CHECKLIST P7-00e).

    Two save classes only:
      CAREER-TUNE-*  disposable tuning clones, always allowed — the class the pilot exists for;
      CAREER-MU*     the canonical franchise's managed saves, allowed only with `managed`
                     (`sim-season --managed`), because driving the real franchise is a decision
                     the caller has to state.
    Every other name is refused with or without the flag, and CAREER-MUBASE is refused even with
    `managed`: it is the hash-pinned baseline (data/baselines/CAREER-MUBASE.sha256) and must never
    be simulated (P7-00a)."""
    upper = str(save or "").upper()
    if save and _valid_save_name(save):
        if upper.startswith(TUNE_SAVE_PREFIX):
            return None
        if upper.startswith(MANAGED_SAVE_PREFIX):
            if not managed:
                return (f"{save} is a managed save: it is {verb} only with --managed "
                        f"({TUNE_SAVE_PREFIX}* stays for disposable clones)")
            if upper.startswith(PINNED_BASELINE_SAVE):
                return (f"{PINNED_BASELINE_SAVE} is the pinned baseline and is never {verb}, "
                        f"not even with --managed (P7-00a)")
            return None
    allowed = f"{TUNE_SAVE_PREFIX}* or {MANAGED_SAVE_PREFIX}*" if managed else f"{TUNE_SAVE_PREFIX}*"
    return f"only {allowed} saves are {verb}, not {save!r}"


def _allow_tile(pilot, tile):
    """Let CURRENT FRANCHISES confirm exactly `tile` for this run, and nothing else.

    The catalog permits only TUNE-* tiles. Two jobs drive a save outside that class by design: the
    catch-up (the real franchise) and `sim-season --managed` (the canonical one), so each allows its
    own tile here. `_walk_to_tile` still stops on any other tile's border."""
    pilot.policy.setdefault("FRANCHISE_LIST", [])
    if tile not in pilot.policy["FRANCHISE_LIST"]:
        pilot.policy["FRANCHISE_LIST"].append(tile)
    return tile


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
    plain copy and reports itself in the result.

    The managed class (P7-00e) is deliberately not clonable: sim_season's `--managed` drives the
    one canonical CAREER-MU* save in place, and a second CAREER-MU* file would keep the real
    LeagueID while also sitting inside the watcher's CAREER-MU* scope."""
    folder = Path(folder) if folder else saves_dir()
    if dst and str(dst).upper().startswith(MANAGED_SAVE_PREFIX):
        return _refuse(f"{dst} is a managed save: it is the canonical franchise, driven in place by "
                       f"sim-season --managed and under the watcher's {MANAGED_SAVE_PREFIX}* scope; "
                       f"clone to {TUNE_SAVE_PREFIX}<name> instead", src=src, dst=dst)
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


def manager_dir():
    """The Mod Manager's own folder in WSL form: MMC_MOD_MANAGER_EXE's directory when set (a Windows
    form is converted), else MMC_MANAGER_DIR, else the default."""
    exe = os.environ.get("MMC_MOD_MANAGER_EXE")
    if exe:
        m = re.match(r"^([a-zA-Z]):[\\/](.*)$", exe)
        if m:
            converted = "/mnt/" + m.group(1).lower() + "/" + m.group(2).replace("\\", "/")
            return os.path.dirname(converted) or DEFAULT_MANAGER_DIR
        return os.path.dirname(exe) or DEFAULT_MANAGER_DIR
    return os.environ.get("MMC_MANAGER_DIR", DEFAULT_MANAGER_DIR)


def launch_cwd():
    """Where cmd.exe must run for `-launch` to work: the Mod Manager's own folder.

    Frosty resolves the pack against its own directory. Started from anywhere else — this job used
    `cwd="/mnt/c"` — the executor throws "errors occurred while attempting to load the following
    mod: <name>" and no Madden window ever appears, which read as a broken mod and as a launch
    timeout for three attempts on 2026-11-12 before the working directory was found to be the
    difference (docs/reference/MUSE_EMBER_EVALUATION.md §2.2)."""
    return manager_dir()


def _manager_running():
    """True when MMCModManager.exe is open. A machine without tasklist (Linux tests, CI) reads False."""
    try:
        from pipeline.mod_packs import manager_running
        return bool(manager_running())
    except (SystemExit, Exception):        # noqa: B014 - SystemExit is what mod_packs raises unwrapped
        return False


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


def wait_menu(pilot, timeout, poll=2.0):
    """Look until a menu is up, pressing through the catalogued boot on the way.

    A cold boot is not a passive wait: the MMC ACCOUNT ERROR, the EA/MADDEN splash and the
    title each need one Space (pressed through confirm_on, the catalogued labels), and until
    then nothing changes (live 2026-11-11 on Mu Test: BOOT_SPLASH sat for the whole 300 s and
    a launch job stopped Unknown). The menus are MENU_SCREENS: the vanilla mode menu, the
    Franchise panel a one-mode menu opens on (Mu Franchise Only), a warm start straight on
    CURRENT FRANCHISES, or a loaded hub."""
    end = pilot.clock() + timeout
    with patience(pilot, timeout + 10 * poll, poll):
        while True:
            frame, screen = pilot.look()
            if screen in MENU_SCREENS:
                return frame, screen
            if pilot.clock() >= end:
                if screen == UNKNOWN:
                    pilot.stop(f"screen still unrecognised after {timeout}s waiting for a menu", Unknown)
                pilot.stop(f"waited {timeout}s for a menu, still on {screen}", Stuck)
            if screen in BOOT_SCREENS:
                pilot.confirm_on(screen, "PRESS ANY BUTTON", wait=2.0)
                continue
            if screen == "ACCOUNT_ERROR":
                pilot.dismiss("ACCOUNT_ERROR")     # OK is the focused default; the catalog exits on it
                continue
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
def launch(pilot, pack=DEFAULT_PACK, run=None, madden_running=None, timeout=LAUNCH_TIMEOUT, progress=None,
           manager_running=None, process_identity=None, fingerprint=None):
    """Start Madden through the MMC Mod Manager with `pack` (unless Madden already runs), wait
    for its window, then press through the cold boot (ACCOUNT ERROR, splash, title) to a menu:
    the vanilla main menu, the Franchise panel a one-mode menu such as Mu Franchise Only opens
    on (FRANCHISE_LANDING), CURRENT FRANCHISES on a warm start, or a hub. A screen that stays
    unrecognised stops as Unknown with its screenshot.

    The manager runs with its own folder as the working directory (launch_cwd), and the job
    refuses while another MMCModManager.exe is open: `start` would only focus that window, so the
    pack would never be applied and the run would wait 300 s for nothing."""
    run = run or (lambda argv: subprocess.run(argv, check=True, cwd=launch_cwd()))
    running = (madden_running or _madden_running)()
    argv = launch_command(pack)
    progress.update(pack=pack, command=argv, already_running=bool(running), cwd=launch_cwd())
    if not running:
        if (manager_running or _manager_running)():
            return _refuse("MMCModManager.exe is already open: close it and launch again — a second "
                           "`start` only focuses the open window (and two managers race over ModData), "
                           "so the pack is never applied", **progress)
        run(argv)
    _wait_window(pilot, timeout)
    _, screen = wait_menu(pilot, timeout)
    # Provenance for a later same-process fixture (F6): the Windows PID + start time make the
    # instance identifiable, and the applied-pack fingerprint makes its pack state checkable.
    # A machine that cannot read either records it as unverified; that is not a launch failure,
    # it just makes this receipt unusable for attachment.
    try:
        from pipeline.gpu_queue import process_check
        identity = (process_identity or process_check.madden_process_identity)()
    except Exception:                        # noqa: BLE001 - record provenance, never guess it
        identity = None
    try:
        from pipeline import mod_packs
        applied = (fingerprint or mod_packs.pack_fingerprint)(pack)
    except Exception:                        # noqa: BLE001
        applied = None
    identity = identity if isinstance(identity, dict) else None
    applied = applied if isinstance(applied, dict) else None
    return {"status": "ok", "pack": pack, "command": argv, "already_running": bool(running), "screen": screen,
            "cwd": progress.get("cwd"),
            "process": identity, "process_verified": bool(identity),
            "applied": applied, "applied_verified": bool(applied and applied.get("pack") == pack)}


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


class _SaveWeek(int):
    """A save week that also carries the save's calendar year, so the catch-up can refuse a save
    whose season disagrees with nfl_mirror_settings. It is still an int for every other caller."""

    year = None


def _save_week(save_path):
    """The save's current Mu week from the save file (the fallback when the hub shows BYE WEEK).

    `.year` carries the save's calendar year (a bare int otherwise). The Pro Bowl, Madden's
    no-game progression stop, reads as Mu week 22 rather than None so the catch-up can advance
    it by the one step it is; the preseason and offseason still read as None."""
    try:
        head = _save_header(save_path)
    except Exception:  # noqa: BLE001 - a fallback; the hub header remains the first source
        return None
    if head is None:
        return None
    week = head.get("week")
    if week is None:
        if head.get("phase") != "PROBOWL":
            return None
        week = PRO_BOWL_WEEK
    value = _SaveWeek(int(week))
    value.year = head.get("year")
    return value


def _state_from_reader(reader, save_path):
    """Normalise a week reader to {"year", "week", "phase"}.

    _save_week returns an int carrying `.year`; a dict from _save_header is used as is; a plain
    injected int (the older test contract) carries no year, so only the week guard applies."""
    value = reader(save_path)
    if value is None:
        return None
    if isinstance(value, dict):
        week = value.get("week")
        return {"year": value.get("year"), "week": None if week is None else int(week),
                "phase": value.get("phase")}
    return {"year": getattr(value, "year", None), "week": int(value), "phase": "WEEK"}


def _year_mismatch(state, season):
    """True when the save names a calendar year other than the mirror's season."""
    year = (state or {}).get("year")
    if year is None:
        return False
    try:
        return int(year) != int(season)
    except (TypeError, ValueError):
        return True


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
        # The top item, READY TO ADVANCE, often fails OCR; the border reader then credits its
        # border to FORCE ADVANCE and Space takes READY, which a 32-user league cannot run — the
        # menu just closes and the week never moves (2026-11-10, p2g week 3). Position is
        # reliable: three Ups clamp on the top item, one Down lands on FORCE ADVANCE.
        label = "FORCE ADVANCE"
        pilot.press("up", times=3)
        pilot.press("down")
        pilot.look()
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
        # the button OCRs as just START (2026-11-10, p2g), so the catalog anchors it on that word
        pilot.confirm_on("DRAFT_HUB", "START", wait=2.5)
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
    the saves folder is only a bound and the border is the truth. A page shows more than three
    tiles, so it moves three presses at a time and looks for the tile anywhere in the frame —
    one press per look swept 170 saves at about a second a press (JR, 2026-11-10, a p2g run that
    looked hung) — then steps onto the tile's border one at a time. A press that leaves the
    border where it was is an edge of the list, and it then goes the other way; coming back to a
    tile this direction already passed means the list wrapped, so the tile is not on it.
    """
    order = save_tiles(folder)
    bound = max(len(order), 60) + 3
    stride = 3

    def labels():
        # The folder's saves plus whatever the frame reads as a name: a list built before a save
        # was moved or archived shows tiles that are no longer files, and the border has to stay
        # readable all the same (2026-11-10: the border read went blind after 166 saves moved).
        seen = set(order)
        seen.update(w["text"].strip() for w in pilot.frame.words
                    if 3 <= len(w["text"].strip()) <= 40 and any(c.isalpha() for c in w["text"])
                    and "/" not in w["text"] and ":" not in w["text"]
                    and norm(w["text"]) not in LIST_WORDS)
        return sorted(seen)

    def on_target():
        return pilot.highlighted(labels=[tile]) == tile

    def current():
        return pilot.highlighted(labels=labels())

    def approach():
        """From the bordered tile, step toward the visible `tile` until it is bordered."""
        for _ in range(stride + 4):
            if on_target():
                return True
            here, there = current(), pilot.frame.find(tile)
            box = pilot.frame.find(here) if here else None
            if not (there and box):
                return False
            dx = there[0] - box[0]
            if abs(dx) < 20:            # same x: the frame says nothing about the direction
                return False
            pilot.press("right" if dx > 0 else "left")
            pilot.look()
        return on_target()

    if on_target():
        return True
    here, there = current(), pilot.frame.find(tile)
    if there is not None and approach():
        return True                 # on this page already: step onto it, no galloping
    directions = ("left", "right") if here and there and there[0] < pilot.frame.find(here)[0] \
        else ("right", "left")
    for key in directions:
        seen, prev = set(), current()
        for _ in range(bound // stride + 2):
            pilot.press(key, times=stride)
            pilot.look()
            now = current()
            if pilot.frame.find(tile) is not None:
                return approach()
            if now is None:
                continue
            if now == prev:
                break                   # the border can't move this way: an edge of the list
            if now in seen:
                return False            # wrapped the whole list: the tile is not on it
            seen.add(now)
            prev = now
    return on_target()


MAIN_MENU_SCREENS = ("MAIN_MENU", "MAIN_MENU_RAIL", "TITLE_SCREEN")
MAIN_MENU_WORDS = ("AUTHENTIC", "PERSONA ENGINE", "ONLINE LEAGUES", "TEAM PASS")   # the mode rail
LIST_WORDS = frozenset({"CURRENT", "FRANCHISES", "CREATE", "NEW", "LEAGUE", "LEAGUES", "ACCEPT", "DELETE",
                        "NEXT", "SELECT", "TIMES", "AM", "PM"})    # CURRENT FRANCHISES' own words


def _leave_to_main_menu(pilot, max_backs=3):
    """Back out of the franchise screens to the main menu; the last screen looked at is returned.

    CURRENT FRANCHISES only rebuilds on a fresh entry from the main menu: a clone made while the
    list was open stays missing after one Back and a re-entry from the Franchise landing page
    (JR, 2026-11-10). Bounded, and a screen that Back won't leave is returned rather than guessed
    at, so the caller's goto fails with a screenshot instead of pressing blind. One more Back on
    the main menu opens EXIT MADDEN?; that dialog is dismissed (NO is its default), never
    confirmed."""
    for _ in range(max_backs):
        _, screen = pilot.look()
        # the main menu draws the Franchise cards behind its rail, so it reads as the landing
        # page; the rail's own words are the only thing that tells them apart
        if screen in MAIN_MENU_SCREENS or any(pilot.frame.has(w) for w in MAIN_MENU_WORDS):
            return screen
        if screen == "EXIT_MADDEN":
            pilot.press(pilot.key_name("select"), _confirm=True)
            continue
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
    if screen not in MAIN_MENU_SCREENS:
        with patience(pilot, 180, 1.0):
            pilot.goto("FRANCHISE_LIST")
    # Always come in from the main menu: Madden builds CURRENT FRANCHISES when the screen opens
    # from there, and a list that was already up keeps tiles for saves no longer on disk — a
    # clone is missing, an archived save still shows (JR, 2026-11-10). One round trip per load.
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
               franchise=None, managed=False):
    """Load CAREER-TUNE-<name> from the franchise list, sim with NEXT WEEK until `to`, create a
    savepoint, and quit back to the franchise list. Refuses other saves and a running watcher
    before touching the PC. `franchise` (a TUNE-* tile name) says that franchise is already
    loaded: sim it in place instead of quitting to the list and loading it again.

    `managed=True` (`--managed`) is the managed-save allowance (P7-00e): it also accepts a
    CAREER-MU* save, the canonical franchise, and allows that save's own tile on CURRENT
    FRANCHISES for the run. CAREER-MUBASE stays refused (P7-00a). The watcher rule is unchanged:
    a managed run still needs `pipeline.watch_save` stopped and ingests its weeks afterwards."""
    why = save_name_refusal(save, managed=managed, verb="simmed")
    if why:
        return _refuse(why, save=save)
    if franchise:
        wanted = "MU" if str(save).upper().startswith(MANAGED_SAVE_PREFIX) else "TUNE"
        if not norm(tile_name(franchise)).startswith(wanted):
            return _refuse(f"only a {wanted}-* franchise may be simmed in place, not {franchise!r}", save=save)
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
    if managed and str(save).upper().startswith(MANAGED_SAVE_PREFIX):
        # CURRENT FRANCHISES lets only TUNE-* tiles be confirmed; a managed save's own tile is
        # allowed for this run and nothing else (the catch-up does the same for the real save).
        _allow_tile(pilot, tile)
    progress.update(save=save, to=to, weeks_simmed=0, managed=bool(managed))

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
        # A managed save is the real franchise, and quitting writes its autosave — which is exactly
        # how this job makes the named save and closes the franchise for the league office. `--managed`
        # is the operator's explicit permission, so it lifts the TUNE-only quit guard (catalog
        # `quit_guard`) for this route only, through the same switch the `quit` job calls allow_quit.
        pilot.goto("FRANCHISE_LIST", allow_quit=managed)
    after = {"save": _mtime(save_path), "autosave": _mtime(auto_path)}
    return {"status": "ok", "save": save, "to": to, "save_path": str(save_path), "autosave_path": str(auto_path),
            "start_week": progress.get("start_week"), "end_week": head.get("week"), "end_phase": head["phase"],
            "weeks_simmed": weeks, "saved": saved, "quit": quit, "managed": bool(managed),
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
    if screen0 not in DRAFT_SCREENS and pilot.frame.has("DRAFT") and not pilot.frame.has("GAMEDAY"):
        # Draft week replaces the GAMEDAY tab with a DRAFT tab, and the hub loads on whichever
        # tab was last open: walk back to it, because simming the draft is this stop's advance
        # (2026-11-10, p2g: the hub loaded on MANAGE ROSTER and the advance had no route).
        for _ in range(3):
            pilot.press("z")
            frame, screen0 = pilot.look()
            if screen0 in DRAFT_SCREENS:
                break
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


# ------------------------------------------------------------------------------- watch_game
SIM_SCREENS = ("SUPERSIM_MENU", "SUPERSIM_JUMP_FORWARD")


def _start_game(pilot):
    """GAMEDAY's PLAY SEASON GAME → pregame → READY → the intro → kickoff, and how it started.

    Only this job starts a game. HUB_GAMEDAY's `confirm_ok` stays `["ADVANCE WEEK"]`: PLAY
    SEASON GAME is added to the pilot's own policy for this run, exactly as the spike's guard
    did (spike S-1; the catalog is never widened). The pregame pivot's only control is a SELECT
    hint that does not OCR as a label, so its Space is pressed directly; READY is highlighted
    on arrival and confirmed through the catalog. The ~40 s broadcast intro needs no press.

    Returns ``(screen, how)``. ``how`` is ``"kicking"`` when this team kicks (the catalogued
    GAME_KICKOFF, Space held 1.5 s), ``"receiving"`` when the receiving view auto-advanced to
    the play call (F8: the CPU kicks, no agent key), or ``"in-play"`` when the kickoff frame was
    already gone by the first look (a late attach). The receiving view is never sent the
    kicker's Space hold: it carries no kick controls and its measured transition needs none.
    """
    pilot.policy.setdefault("HUB_GAMEDAY", [])
    if "PLAY SEASON GAME" not in pilot.policy["HUB_GAMEDAY"]:
        pilot.policy["HUB_GAMEDAY"].append("PLAY SEASON GAME")
    start_screens = ["GAME_PREGAME_READY", "GAME_KICKOFF", "GAME_KICKOFF_RECEIVE", "GAME_PLAY_CALL"]
    _, screen = wait_known(pilot, HUBS, PREGAME_TIMEOUT, poll=1.0, what="a franchise hub")
    if screen != "HUB_GAMEDAY":
        pilot.goto("HUB_GAMEDAY", timeout=PREGAME_TIMEOUT)
    pilot.highlight("PLAY SEASON GAME")
    pilot.confirm_on("HUB_GAMEDAY", "PLAY SEASON GAME", wait=2.5)
    _, screen = wait_known(pilot, ["GAME_PREGAME_PIVOT", "GAME_PREGAME_READY"], PREGAME_TIMEOUT,
                           poll=1.0, what="the pregame")
    if screen == "GAME_PREGAME_PIVOT":
        pilot.press("select", _confirm=True, wait=2.5)
        _, screen = wait_known(pilot, start_screens, PREGAME_TIMEOUT, poll=1.0,
                               what="the ready screen")
    if screen == "GAME_PREGAME_READY":
        pilot.highlight("READY")
        pilot.confirm_on("GAME_PREGAME_READY", "READY", wait=3.0)
        screen = None
    if screen is None:
        _, screen = wait_known(pilot, start_screens[1:], GAME_INTRO_TIMEOUT, poll=2.0,
                               what="kickoff")
    if screen == "GAME_KICKOFF":
        pilot.press("select", hold=KICK_HOLD, _confirm=True, wait=2.0)   # a hold, not a tap (S-1)
        return screen, "kicking"
    if screen == "GAME_KICKOFF_RECEIVE":
        # Measured: the receiving view needs no key; the CPU kicks and Madden advances to the
        # offensive play call on its own (F8). Require the scorebug clock as context so a
        # KICKOFF ticker alone can never start this branch, then wait for the real transition.
        if read_game_clock(pilot.frame) is None:
            pilot.stop("watch-game: the receiving kickoff frame has no readable scorebug clock; "
                       "not sending any key to a kick state nobody has measured", Stuck)
        _, screen = wait_known(pilot, ["GAME_PLAY_CALL"], GAME_INTRO_TIMEOUT, poll=2.0,
                               what="the automatic receiving kickoff transition to the play call")
        return screen, "receiving"
    return screen, "in-play"


def _write_ready(pilot, save, target, hold, clock, ready_token=None, started=None):
    """Write this invocation's ready-for-recording marker immediately before the hold.

    The mod-check driver accepts only a marker carrying the token it passed, from this process's
    pid, naming the cue's anchor; the remaining hold must still cover the recording. This is the
    job's own "I am holding now" signal (F1/F3), not a historical screen classification. A look
    is deliberately not taken: a look refocuses Madden while the caller is about to record.
    """
    now = time.time()
    stamp = datetime.datetime.now()
    payload = {
        "job": "watch-game", "save": save, "route": "supersim" if target == "halftime" else target,
        "anchor": WATCH_ANCHORS[target], "screen": pilot.screen,
        "clock": clock, "hold_seconds": int(hold),
        "ready_token": ready_token, "pid": os.getpid(), "started": started,
        "ready_at": stamp.isoformat(timespec="seconds"),
        "hold_until_epoch": now + float(hold),
        "hold_until": (stamp + datetime.timedelta(seconds=float(hold))).isoformat(timespec="seconds"),
    }
    if target == "halftime":
        # F1 stays open: nothing in this job has observed the show itself. The marker says so,
        # so a later reader cannot mistake "holding after the last-play route" for the show.
        payload["show_observed"] = False
        payload["show_note"] = ("the halftime show itself is unmeasured; this marker only says the "
                                "route reached its last-play hold")
    Path(pilot.run_dir).mkdir(parents=True, exist_ok=True)
    (Path(pilot.run_dir) / READY_FILE).write_text(json.dumps(payload, indent=1, default=str))


def _hold(pilot, seconds):
    """Press nothing for `seconds` and report the screen it ended on: the job's whole point.

    The caller is recording while this runs, so no look is taken mid-hold either (a look
    refocuses Madden); the one look at the end is what the result reports.
    """
    pilot.sleep(seconds)
    return pilot.look()[1]


def _visible_options(pilot):
    """The menu rows actually on the frame, in screen order, under their catalog names.

    The catalog's option list is what the menu can contain, not what this game state shows
    (F7: the live pause menu has 9 rows, not the catalog's 11). A row is found by its own OCR
    text; ``SUPER`` and its ``SUPER SIM`` tooltip are the same row. A screen's ``aliases`` map
    covers a measured truncated reading of the same row (``JUMP TO NEXT`` for JUMP TO NEXT
    PLAY, F9) without matching a different action.
    """
    spec = pilot.spec()
    options = [str(option) for option in (spec.get("options") or [])]
    aliases = spec.get("aliases") or {}
    found = []
    for index, option in enumerate(options):
        box = pilot.frame.find(option) if pilot.frame else None
        if box is None:
            for reading in aliases.get(option) or []:
                box = pilot.frame.find(reading) if pilot.frame else None
                if box:
                    break
        if box is None and norm(option) == "SUPER":
            box = pilot.frame.find("SUPER SIM") if pilot.frame else None
        if box is None and norm(option) == "SUPER SIM":
            box = pilot.frame.find("SUPER") if pilot.frame else None
        if box:
            found.append((float(box[1]), float(box[0]), index, option))
    found.sort()
    return [option for *_, option in found]


def _pin_highlight(pilot, label):
    """Put the visible selection on `label`, verified from the frame at every step.

    The old pin pressed ``len(catalog options)`` Ups then a catalog-index number of Downs and
    trusted a shadow cursor (F7). That silently activated STATS or COACHING on the measured
    9-row pause menu. This pin uses the rows actually on screen, walks Up to the top only as
    navigation, then steps Down one row at a time and reads the selected row each time; it
    stops before Space when the target never becomes selected. A wrapped, clamped or
    disabled-skipping menu is covered because every visible row is visited at most once.
    """
    if pilot.screen is None:
        pilot.look()
    options = _visible_options(pilot)
    if not any(same_option(label, option) for option in options):
        pilot.stop(f"watch-game: {label!r} isn't visible on {pilot.screen} "
                   f"(visible rows: {options}); not pressing blind", Stuck)
    pilot.press("up", times=max(1, len(options)))     # bounded navigation to the top
    pilot.look()
    seen = []
    for _ in range(len(options) + 1):
        current = pilot.highlighted(labels=options)
        seen.append(current)
        if current is not None and same_option(current, label):
            return
        pilot.press("down", wait=0.6)
        pilot.look()
    pilot.stop(f"watch-game: could not establish {label!r} on {pilot.screen}; selection read "
               f"{seen}; not pressing Space", Stuck)


def _supersim_menu(pilot, timeout=SUPERSIM_TIMEOUT):
    """Stand on SUPERSIM_MENU: Escape pauses, then the pause menu's sixth item opens supersim.

    A frame that is neither a sim screen nor the pause menu is real play (it reads as UNKNOWN, or
    GAME_KICKOFF when a kick is waiting), and Escape pauses it. Nothing here presses a football
    key: JR's control rule (spike S-1) is that an offense drive is simmed, never played. The
    pause menu is pinned from RESUME, because only RESUME's selection border reads back.
    """
    frame, screen = pilot.look()
    if screen == "SUPERSIM_JUMP_FORWARD":
        pilot.press("back", wait=2.0)
        frame, screen = wait_known(pilot, ["SUPERSIM_MENU"], timeout, what="the supersim menu")
    if screen == "SUPERSIM_MENU":
        return frame
    if screen == "PAUSE_MENU":
        pass
    elif screen in (UNKNOWN, "GAME_KICKOFF", "GAME_PLAY_CALL"):
        # Real play-call and the receiving kickoff pause with the same measured Escape (F8/F10);
        # GAME_PLAY_CALL is what a super-sim Back actually returns to (2026-11-12).
        pilot.press("esc", wait=2.5)
        frame, screen = wait_known(pilot, ["PAUSE_MENU"], PAUSE_TIMEOUT, poll=1.0, what="the pause menu")
    else:
        pilot.stop(f"watch-game: {screen} is neither the game, the pause menu nor the supersim screen; "
                   f"no route is catalogued from it", Stuck)
    _pin_highlight(pilot, "SUPER")               # RESUME + 5 Downs (S-1's list)
    pilot.confirm_on("PAUSE_MENU", "SUPER", wait=2.5)
    frame, screen = wait_known(pilot, ["SUPERSIM_MENU", "SUPERSIM_JUMP_FORWARD"], timeout, poll=1.0,
                               what="the supersim screen")
    if screen == "SUPERSIM_JUMP_FORWARD":
        pilot.press("back", wait=2.0)
        frame, screen = wait_known(pilot, ["SUPERSIM_MENU"], timeout, what="the supersim menu")
    return frame


def _wait_sim(pilot, timeout, poll=2.0):
    """Wait out a sim action: (frame, screen) — the sim screen, or where the game came back.

    The sim's own frames read as UNKNOWN, so an unrecognised frame is only reported after
    `timeout`: the caller then reopens the pause menu rather than press a football key.
    """
    end = pilot.clock() + timeout
    with patience(pilot, timeout + 10 * poll, poll):
        while True:
            frame, screen = pilot.look()
            if screen in SIM_SCREENS:
                return frame, screen
            if pilot.clock() >= end:
                return frame, screen
            pilot.sleep(poll)


def _supersim_press(pilot, label, timeout=SUPERSIM_PLAY_TIMEOUT):
    """Confirm one sim control on SUPERSIM_MENU, wait out the sim it runs, and re-open the menu.

    Returns (frame, screen). A sim that hands real play back is not a stop: the pause menu is
    reopened so the recipe keeps simming, and no play is called (JR's control rule).
    """
    _supersim_menu(pilot)
    _pin_highlight(pilot, label)
    pilot.confirm_on("SUPERSIM_MENU", label, wait=3.0)
    frame, screen = _wait_sim(pilot, timeout)
    if screen not in SIM_SCREENS:
        frame = _supersim_menu(pilot)
        screen = pilot.screen
    return frame, screen


def _supersim_jump(pilot, label, timeout=SUPERSIM_SIM_TIMEOUT):
    """JUMP FORWARD → `label` (NEXT QUARTER, CHANGE OF POSSESSION, END OF GAME), waited out."""
    _supersim_menu(pilot)
    _pin_highlight(pilot, "JUMP FORWARD")
    pilot.confirm_on("SUPERSIM_MENU", "JUMP FORWARD", wait=2.0)
    _, screen = wait_known(pilot, ["SUPERSIM_JUMP_FORWARD"], SUPERSIM_TIMEOUT, poll=1.0,
                           what="the jump-forward menu")
    _pin_highlight(pilot, label)
    pilot.confirm_on("SUPERSIM_JUMP_FORWARD", label, wait=3.0)
    frame, screen = _wait_sim(pilot, timeout)
    if screen not in SIM_SCREENS:
        frame = _supersim_menu(pilot)
        screen = pilot.screen
    return frame, screen


def _take_one_snap(pilot):
    """JR's control rule: the one football snap this job may take is the last of a quarter.

    Space takes the default play, about a second passes while the line sets, and Space snaps it
    (spike S-1's rule for a real game). Nothing else in this job presses a football key: a drive
    that would need playing is simmed on the supersim screen instead, never called.
    """
    pilot.press("select", _confirm=True, wait=1.0)     # the default play
    pilot.press("select", _confirm=True, wait=1.0)     # the snap


def _to_halftime(pilot, progress):
    """D2's recipe (JR, spike S-1): supersim Q1's end, sim Q2 by play, resume under 20 s.

    SUPER SIM → JUMP FORWARD → NEXT QUARTER (stops at the start of Q2) → CHANGE OF POSSESSION
    twice (a drive each) → JUMP TO NEXT PLAY until the clock reads between 1 and 19 s in Q2 →
    Backspace → RESUME → the half's last snap. The clock is the resume cue: 0:00 means the half
    is already over, so the job stops as Stuck rather than resume a play that no longer exists;
    an unreadable or out-of-window clock stops the same way (plan R4). The caller writes its
    ready marker and holds after this returns, so recording is synchronized with the hold.
    """
    _supersim_jump(pilot, "NEXT QUARTER")              # Q1's end: stops at the start of Q2
    frame, _ = _supersim_jump(pilot, "CHANGE OF POSSESSION")     # one drive
    frame, _ = _supersim_jump(pilot, "CHANGE OF POSSESSION")
    presses, blind = 0, 0
    while True:
        clock = read_game_clock(frame)
        if clock and clock["quarter"] == 2 and clock["seconds"] == 0:
            pilot.stop("the clock read 0:00 in Q2: the half is already over, so there is no last "
                       "play to resume; not resuming at the wrong moment", Stuck)
        if (clock and clock["quarter"] == 2
                and HALFTIME_MIN_CLOCK <= clock["seconds"] < HALFTIME_WINDOW):
            break
        if clock and clock["quarter"] not in (1, 2):
            pilot.stop(f"the sim reached Q{clock['quarter']} before the clock read under "
                       f"{HALFTIME_WINDOW}s in Q2: not resuming the half at the wrong moment", Stuck)
        blind = blind + 1 if clock is None else 0
        if blind >= HALFTIME_BLIND_READS:
            pilot.stop(f"the game clock didn't read on {blind} supersim frames: not resuming the half "
                       f"at the wrong moment", Stuck)
        presses += 1
        if presses > HALFTIME_MAX_PLAY_PRESSES:
            pilot.stop(f"{presses} JUMP TO NEXT PLAY presses and the clock never read under "
                       f"{HALFTIME_WINDOW}s in Q2: not resuming the half at the wrong moment", Stuck)
        frame, _ = _supersim_press(pilot, "JUMP TO NEXT PLAY", timeout=SUPERSIM_PLAY_TIMEOUT)
        progress.update(halftime_presses=presses)
    progress.update(halftime_presses=presses, halftime_clock=clock)
    # MEASURED 2026-11-12: Back from the sim screen returns to the offensive play call, not the
    # pause menu (live_quit_prepare). Recognise both landings; never wait for Pause forever and
    # never confirm a RESUME that the measured route does not show (F10).
    landing = _return_from_supersim(pilot)
    if landing == "PAUSE_MENU":
        _pin_highlight(pilot, "RESUME")
        pilot.confirm_on("PAUSE_MENU", "RESUME", wait=3.0)
    elif landing != "GAME_PLAY_CALL":
        pilot.stop(f"watch-game: leaving super-sim landed on {landing}, not the measured play call "
                   f"or pause menu; not resuming a half from an unmeasured screen", Stuck)
    # Re-read the real clock immediately before the only allowed last-play snap: the transition
    # burns game clock, so the 1..19 s Q2 window must still hold at this instant (F10/R4).
    clock = read_game_clock(pilot.look()[0])
    if not (clock and clock["quarter"] == 2
            and HALFTIME_MIN_CLOCK <= clock["seconds"] < HALFTIME_WINDOW):
        pilot.stop(f"watch-game: the clock read {clock!r} after leaving super-sim; only a live Q2 "
                   f"clock of {HALFTIME_MIN_CLOCK}-{HALFTIME_WINDOW - 1}s may take the half's last "
                   f"play, so no snap is pressed", Stuck)
    _take_one_snap(pilot)                              # the half's last play
    # One look after the snap so the ready marker names what is actually on screen at the hold
    # (the auditor's F1: the pre-snap screen was reported). The show itself is still not
    # observed here, and the marker says show_observed: false.
    _, screen = pilot.look()
    return screen, clock


def _quit_game(pilot, save=None):
    """Leave a live game: Escape → the pause menu's QUIT GAME → the measured EXIT GAME modal.

    JR (2026-11-12): a `CAREER-TUNE-*` clone may have its game quit, because leaving a mod check
    sitting in a live game was worse. QUIT GAME is added to the pilot's policy for THIS RUN only,
    exactly as PLAY SEASON GAME is: the catalog's `PAUSE_MENU.confirm_ok` still allows RESUME and
    SUPER alone, so no other job can reach the label. The modal that follows is the MEASURED
    EXIT GAME dialog (F11), and its only allowed affirmative is YES - EXIT TO MENU; the landing
    after that choice is still unmeasured, so the job reports where it lands and, when it is
    still a loaded franchise hub, finishes with the existing safe TUNE quit.
    """
    screen = _return_from_supersim(pilot)
    if screen != "PAUSE_MENU":
        _pause(pilot)                                  # measured Escape recovery from play-call
    pilot.policy.setdefault("PAUSE_MENU", [])
    if "QUIT GAME" not in pilot.policy["PAUSE_MENU"]:
        pilot.policy["PAUSE_MENU"].append("QUIT GAME")
    _pin_highlight(pilot, "QUIT GAME")
    pilot.confirm_on("PAUSE_MENU", "QUIT GAME", wait=3.0)
    _, screen = _wait_quit_landing(pilot)
    if screen == "QUIT_GAME_CONFIRM":
        _confirm_quit_game(pilot)
        _, screen = wait_known(pilot, ["FRANCHISE_LIST", *HUBS, "FRANCHISE_LANDING", "MAIN_MENU"],
                               LEAVE_TIMEOUT, poll=1.0,
                               what="the landing after YES - EXIT TO MENU")
        if screen in HUBS:
            # EXIT TO MENU can leave the franchise loaded on a hub; finish with the existing
            # safe TUNE quit rather than assuming it reached CURRENT FRANCHISES (F11).
            _quit_now(pilot, franchise=save, allow_quit=False)
            screen = "FRANCHISE_LIST"
    return screen


def _return_from_supersim(pilot, timeout=SUPERSIM_TIMEOUT):
    """Back out of the super-sim screens and report the measured landing.

    MEASURED 2026-11-12 (live_quit_prepare): Back on SUPERSIM_MENU returns to the offensive
    play call (GAME_PLAY_CALL), not the pause menu. The jump-forward modal still backs out to
    the sim menu first. A pause landing is also accepted for a variant that shows it; anything
    else is returned as-is so the caller can stop rather than press into it.
    """
    for _ in range(2):
        if pilot.screen == "SUPERSIM_JUMP_FORWARD":
            pilot.press("back", wait=2.0)
            _, screen = wait_known(pilot, ["SUPERSIM_MENU", "GAME_PLAY_CALL", "PAUSE_MENU"],
                                   timeout, poll=1.0, what="the screen behind the jump-forward modal")
            if screen != "SUPERSIM_MENU":
                return screen
            continue
        if pilot.screen == "SUPERSIM_MENU":
            pilot.press("back", wait=2.5)
            _, screen = wait_known(pilot, ["GAME_PLAY_CALL", "PAUSE_MENU"], timeout, poll=1.0,
                                   what="the measured return from super-sim")
            return screen
        return pilot.screen
    return pilot.screen


def _pause(pilot, tries=3, wait=PAUSE_TIMEOUT, poll=1.0):
    """Reach PAUSE_MENU with Escape, a bounded number of times.

    Real play does not run itself (spike S-1): a hold that outlasts the play clock ends on a
    delay-of-game penalty dialog whose border does not read, and the first Escape dismisses it,
    so Escape is tried again — but only that many times, and then the run stops rather than press
    into a screen nobody has seen.
    """
    with patience(pilot, tries * (wait + 10 * poll), poll):
        for _ in range(tries):
            _, screen = pilot.look()
            if screen == "PAUSE_MENU":
                return
            pilot.press("esc", wait=2.5)
            end = pilot.clock() + wait
            while True:
                _, screen = pilot.look()
                if screen == "PAUSE_MENU":
                    return
                if screen != UNKNOWN or pilot.clock() >= end:
                    break
                pilot.sleep(poll)
    pilot.stop(f"watch-game: Escape didn't reach the pause menu ({pilot.screen}) in {tries} tries; "
               f"not pressing again", Stuck)


def _wait_quit_landing(pilot, timeout=LEAVE_TIMEOUT, poll=1.0):
    """Where QUIT GAME lands: the measured EXIT GAME modal, or the list when there is no dialog.

    A loading frame (UNKNOWN) is waited out; any other screen is a Stuck — catalogued or not —
    because the game is left exactly once and a screen nobody has seen is a report, not a second
    blind press. The modal is the measured EXIT GAME / ARE YOU SURE / YES - EXIT TO MENU screen
    (F11), not the pause menu's QUIT GAME tooltip, which classifies as PAUSE_MENU.
    """
    end = pilot.clock() + timeout
    with patience(pilot, timeout + 10 * poll, poll):
        while True:
            frame, screen = pilot.look()
            if screen in ("QUIT_GAME_CONFIRM", "FRANCHISE_LIST"):
                return frame, screen
            if screen != UNKNOWN or pilot.clock() >= end:
                pilot.stop(f"watch-game: QUIT GAME landed on {screen}, not the quit-game confirmation "
                           f"or the franchise list: not pressing again (add the screen to the catalog "
                           f"and re-run)", Stuck)
            pilot.sleep(poll)


def _cancel_shaped(label):
    """True for a label that rejects, backs out of or destroys something (CANCEL, NO, ...)."""
    return bool(set(norm(label).replace("-", " ").split()) & set(CANCEL_WORDS))


def _destructive_shape(label):
    """True for a label a game-quit workflow must never confirm (cancel/no/back/delete/trade,
    or EXIT TO DESKTOP: the per-run TUNE quit may only choose YES - EXIT TO MENU, F11)."""
    tokens = set(norm(label).replace("-", " ").split())
    return bool(tokens & set(CANCEL_WORDS)) or "DESKTOP" in tokens


def _confirm_quit_game(pilot):
    """Leave through the measured EXIT GAME modal: its one allowed label, never a destructive one.

    The catalog decides which labels are affirmative (`QUIT_GAME_CONFIRM.confirm_ok` = YES -
    EXIT TO MENU); `forbidden_confirm` means only this job's policy can press it, and the policy
    is given that one label only. A modal whose allowed labels all look like cancel/no/back/
    delete/trade or EXIT TO DESKTOP stops the run rather than pick one. The selection is read by
    containment, so a missing selected NO can never authorize its neighbouring YES - EXIT TO
    MENU row: the guard moves the selection and re-reads it before Space (F9/F11).
    """
    if pilot.screen != "QUIT_GAME_CONFIRM":
        pilot.look()
    allowed = [label for label in (pilot.spec("QUIT_GAME_CONFIRM").get("confirm_ok") or [])
               if not _destructive_shape(label)]
    if not allowed:
        pilot.stop("watch-game: the quit-game confirmation's allowed labels all look like "
                   "cancel/no/back/delete/trade/desktop; not choosing one", Stuck)
    pilot.policy.setdefault("QUIT_GAME_CONFIRM", [])
    for label in allowed:
        if label not in pilot.policy["QUIT_GAME_CONFIRM"]:
            pilot.policy["QUIT_GAME_CONFIRM"].append(label)
    label = allowed[0]
    _pin_highlight(pilot, label)
    pilot.confirm_on("QUIT_GAME_CONFIRM", label, wait=3.0)


def _leave_franchise(pilot, save):
    """Leave the franchise: `advance`'s quit path outside a game, the pause menu's QUIT GAME in one.

    JR (2026-11-12, M-0673/M-0675): `watch-game` may quit a live game on a CAREER-TUNE-* clone.
    Outside a football game the catalogued route to CURRENT FRANCHISES is `advance`'s own quit
    (`_quit_now`, i.e. `goto("FRANCHISE_LIST")`, which the quit guard allows for a TUNE-* tile).
    Inside one the only exit is the pause menu's QUIT GAME (`_quit_game`), confirmed through the
    pilot's policy for this run and followed by its own confirmation dialog.
    """
    if pilot.screen != "FRANCHISE_LIST" and pilot.plan(pilot.screen, "FRANCHISE_LIST") is None:
        screen = _quit_game(pilot, save=save)
        return {"quit": True, "quit_route": "quit-game", "end_screen": screen}
    out = _quit_now(pilot, franchise=save, allow_quit=False)
    return {"quit": bool(out.get("quit")), "quit_route": "route",
            "end_screen": out.get("screen") or pilot.screen}


@job("watch-game")
def watch_game(pilot, save=None, franchise=None, to="kickoff", hold_seconds=60, pack=None, watcher=None,
               folder=None, progress=None, quit=True, ready_token=None):
    """Start a CAREER-TUNE-* season game, then hold still while a caller records the screen.

    The one pilot job allowed to start a game (VISUAL_MOD_CHECK_PLAN.md 2.5). `to="kickoff"`
    presses PLAY SEASON GAME, walks the pregame (an unlabelled SELECT pivot, READY, a ~40 s
    intro), holds Space 1.5 s to kick, then presses NOTHING for `hold_seconds` and reports the
    screen it ended on. `to="halftime"` then walks D2's supersim recipe (`_to_halftime`), takes
    the half's last snap and holds through the halftime show. `to="supersim"` kicks off, opens
    the pause menu's SUPER SIM screen and holds on it. The clock, not a timer, is the resume cue
    (plan R4). Immediately before any hold the job writes `ready.json` (see `_write_ready`) with
    the cue anchor, route, screen, clock, hold deadline, pid and the caller's `--ready-token`.

    It never calls a play of its own: every control between the kickoff and the half's last play
    is a supersim control (`_take_one_snap` is the only football key press). HUB_GAMEDAY's
    confirm_ok still allows ADVANCE WEEK alone — PLAY SEASON GAME goes through the pilot's
    policy for this run, as the spike's guard did. `pack` is recorded for the report; Madden is
    launched by the `launch` job.

    Leaving: `quit` quits the franchise through `_quit_now(..., allow_quit=False)` when a route to
    the franchise list exists, and through the pause menu's own QUIT GAME (`_quit_game`) when the
    segment ended in a live game — a TUNE-* clone may have its game quit (JR, 2026-11-12). The
    result reports which way it left (`quit_route`: "route" or "quit-game"); `written` compares the
    saves folder's mtimes before and after, i.e. whether `<save>-AUTOSAVE` appeared (S-1: quitting
    writes it).
    """
    if not save or not str(save).upper().startswith(TUNE_SAVE_PREFIX) or not _valid_save_name(save):
        return _refuse(f"only {TUNE_SAVE_PREFIX}* saves are watched, not {save!r}", save=save)
    if franchise and not norm(tile_name(franchise)).startswith("TUNE"):
        return _refuse(f"only a TUNE-* franchise may be watched in place, not {franchise!r}", save=save)
    target = " ".join(str(to or "").strip().lower().replace("_", "-").split())
    if target not in WATCH_TARGETS:
        return _refuse(f"--to must be {' or '.join(WATCH_TARGETS)}, not {to!r}", save=save)
    try:
        hold = int(hold_seconds)
        whole = float(hold_seconds) == hold
    except (TypeError, ValueError):
        return _refuse(f"--hold-seconds must be a whole number of seconds, not {hold_seconds!r}", save=save)
    if not whole:
        return _refuse(f"--hold-seconds must be whole seconds, not {hold_seconds!r}", save=save)
    if not 1 <= hold <= WATCH_MAX_HOLD:
        return _refuse(f"--hold-seconds {hold} is outside 1-{WATCH_MAX_HOLD}", save=save)
    if (watcher or watcher_running)():
        return _refuse("pipeline.watch_save is running: stop it first (a clone keeps the real "
                       "franchise's LeagueID, so the watcher would ingest it)", save=save)
    folder = Path(folder) if folder else saves_dir()
    save_path, auto_path = folder / save, folder / f"{save}-AUTOSAVE"
    if folder.is_dir() and not save_path.is_file() and not getattr(pilot, "dry_run", False):
        return _refuse(f"no save {save} in {folder} (clone-save first)", save=save)
    before = {"save": _mtime(save_path), "autosave": _mtime(auto_path)}
    tile = tile_name(save)
    progress.update(save=save, to=target, pack=pack, hold_seconds=hold, route=target)
    if franchise:
        pilot.loaded_franchise = norm(tile_name(franchise))
    else:
        _load_franchise(pilot, tile, save=save, folder=folder)
    _, how = _start_game(pilot)
    route = "kickoff"
    if target == "kickoff":
        if how != "kicking":
            pilot.stop("watch-game: this game started on the receiving side and its kickoff view "
                       "was already gone (the CPU kicks and Madden advances on its own); there is "
                       "no kicking kickoff screen to record, so nothing is recorded", Stuck)
        clock = read_game_clock(pilot.frame)
    elif target == "supersim":
        _supersim_menu(pilot)
        route = "supersim"
        clock = read_game_clock(pilot.frame)
    else:
        _, clock = _to_halftime(pilot, progress)
        route = "supersim"
    _write_ready(pilot, save, target, hold, clock, ready_token=ready_token,
                 started=progress.get("started"))
    screen = _hold(pilot, hold)
    progress.update(route=route, end_screen=screen, clock=clock)
    leaving = {"quit": False, "end_screen": screen}
    if quit:
        leaving = _leave_franchise(pilot, save)
    after = {"save": _mtime(save_path), "autosave": _mtime(auto_path)}
    written = {k: (after[k] is not None and after[k] != before[k]) for k in after}
    return {"status": "ok", "save": save, "franchise": pilot.loaded_franchise, "to": target,
            "route": route, "hold_seconds": hold, "pack": pack, "clock": clock,
            "save_path": str(save_path), "autosave_path": str(auto_path),
            "written": written, "autosave_written": written["autosave"], **leaving}


# ------------------------------------------------------------------------------- catch_up
class _MirrorStore:
    """Reads nfl_mirror_settings and nfl_mirror_weeks for the catch-up job; it never writes.

    One connection with autocommit on, so every SELECT sees the league office's latest commit
    (the watcher writes the mirror rows from another process)."""

    def __init__(self, dsn):
        self.dsn = dsn
        self._conn = None

    def _cur(self):
        if self._conn is None:
            import psycopg
            self._conn = psycopg.connect(self.dsn, autocommit=True)
        return self._conn.cursor()

    def settings(self):
        from engines.nfl_mirror import store
        with self._cur() as cur:
            return store.read_settings(cur)

    def week_status(self, season, week):
        from engines.nfl_mirror import store
        with self._cur() as cur:
            return store.week_status(cur, season, week)

    def close(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _mirror_dsn(dsn=None):
    """--dsn, else DATABASE_URL (what the watcher and league office use), else the active franchise."""
    target = dsn or os.environ.get("DATABASE_URL")
    if not target:
        from db import franchises
        target = franchises.active_dsn()
    if not target:
        return None
    from engines.nfl_history.store import dsn_for
    return dsn_for(target)


def _fetch_games(season):
    from engines.nfl_mirror import fetch
    files = fetch.fetch_week(season=season)
    return list(fetch.read_rows(files["games"]))


def _mode_reader(season, games_rows=None):
    """mode_for(mu_week) -> real_state.CATCH_UP | LIVE | PRO_BOWL, reading games.csv once.

    The Pro Bowl (Mu week 22) has no real NFL week and no mirror row: it is Madden's own
    progression stop, so the catch-up advances through it deliberately instead of stopping as
    if it were a real live week. Any other week with no real game still reads LIVE."""
    cache = {}
    rows_for = games_rows or (lambda: _fetch_games(season))

    def mode_for(mu_week):
        from engines.nfl_mirror import real_state
        if int(mu_week) == PRO_BOWL_WEEK:
            return PRO_BOWL
        if "games" not in cache:
            cache["games"] = list(rows_for())
        try:
            real_week = real_state.real_week_for(mu_week)
        except ValueError:
            return real_state.LIVE
        return real_state.week_mode(cache["games"], season, real_week)

    return mode_for


def _save_copy(save_path, auto_path):
    """Copy Madden's autosave over the named save after the franchise is closed (as sim_season
    does; never Tab). False when there is nothing newer to copy (or a dry run has no files)."""
    if not save_path.is_file() or not auto_path.is_file():
        return False
    if (_mtime(auto_path) or 0) <= (_mtime(save_path) or 0):
        return False
    shutil.copy2(auto_path, save_path)
    return True


def _wait_written(pilot, mirror, season, week, timeout=CATCH_UP_WRITE_TIMEOUT, poll=CATCH_UP_POLL):
    """Wait for nfl_mirror_weeks to read WRITTEN for `week`; FAILED or the timeout is a stop."""
    end = pilot.clock() + timeout
    while True:
        status = mirror.week_status(season, week)
        if status == "WRITTEN":
            return status
        if status == "FAILED":
            pilot.stop(f"the league office's mirror week {season} week {week} FAILED; it is not written", Stuck)
        if pilot.clock() >= end:
            pilot.stop(f"the league office didn't write mirror {season} week {week} within {timeout}s "
                       f"(status {status or 'no row'}); is pipeline.watch_save still running?", Stuck)
        pilot.sleep(poll)


def _quit_to_menu(pilot, franchise=None):
    """Quit the loaded franchise and back out to the main menu, so the league office's save
    replacement can run and the next load starts from the menu (CURRENT FRANCHISES rebuilds
    only on a fresh entry from there)."""
    _quit_now(pilot, franchise=franchise, allow_quit=True)
    return _leave_to_main_menu(pilot)


@job("catch-up")
def catch_up(pilot, save=None, to_week=None, dsn=None, progress=None, *, watcher=None, folder=None,
             store=None, mode_for=None, week_reader=None, load=None, sim=None, quit_to_menu=None):
    """One week at a time on the real franchise, waiting for the NFL mirror between weeks
    (NFL_WEEKLY_MIRROR_PLAN.md section 2.12).

    Before touching the PC it refuses a CAREER-TUNE-* save, a watcher that isn't running, a
    disabled nfl_mirror_settings and a save whose next week is not CATCH_UP for the real NFL.
    Each loop loads the franchise, sims exactly one week, checks that the save's own week moved
    by exactly one (R4c), quits to the main menu, copies the autosave over the named save, and
    waits for nfl_mirror_weeks to read WRITTEN for the save's new week. It stops at the first
    LIVE week or --to-week, and on a FAILED or never-written mirror week."""
    if not save or not str(save).upper().startswith(CAREER_PREFIX) or \
            str(save).upper().startswith(TUNE_SAVE_PREFIX) or not _valid_save_name(save):
        return _refuse(f"catch-up mirrors the real CAREER-* franchise, never a {TUNE_SAVE_PREFIX}* clone, "
                       f"not {save!r}", save=save)
    if to_week is not None:
        try:
            to_week = int(to_week)
        except (TypeError, ValueError):
            return _refuse(f"--to-week must be a week number, not {to_week!r}", save=save)
        if not 1 <= to_week <= 23:
            return _refuse(f"--to-week {to_week} isn't a Mu week (1-23)", save=save)
    if not (watcher or watcher_running)():
        return _refuse("pipeline.watch_save isn't running: the catch-up waits for its league-office write "
                       "after every week, so start the watcher first (the reverse of sim_season)", save=save)
    # The catalog lets CURRENT FRANCHISES confirm only TUNE-* tiles; the catch-up is the one job
    # allowed the real franchise, so it allows exactly this tile's name for this run (and nothing
    # else: _walk_to_tile still stops on any other tile's border).
    tile = _allow_tile(pilot, tile_name(save))
    folder = Path(folder) if folder else saves_dir()
    save_path, auto_path = folder / save, folder / f"{save}-AUTOSAVE"
    if folder.is_dir() and not save_path.is_file() and not getattr(pilot, "dry_run", False):
        return _refuse(f"no save {save} in {folder}", save=save)
    mirror, own = store, False
    if mirror is None:
        target = _mirror_dsn(dsn)
        if not target:
            return _refuse("no database to read nfl_mirror_settings: pass --dsn, set DATABASE_URL, "
                           "or register the active franchise", save=save)
        mirror, own = _MirrorStore(target), True
    try:
        try:
            settings = mirror.settings()
        except Exception as e:  # noqa: BLE001 - a database that can't answer is a refusal, not a crash
            return _refuse(f"couldn't read nfl_mirror_settings: {e}", save=save)
        if not settings or not settings.get("enabled"):
            return _refuse("nfl_mirror_settings.enabled is false: enable the NFL weekly mirror before a catch-up",
                           save=save)
        season = int(settings.get("season") or CATCH_UP_SEASON)
        reader = week_reader or _save_week
        # The production reader must carry a readable calendar year on every header read; an
        # explicitly injected legacy reader may still return only an int (audit-5 wave-5 R1, M-0565).
        production_reader = week_reader is None or week_reader is _save_week
        mode_of = mode_for or _mode_reader(season)
        try:
            state = _state_from_reader(reader, _latest_save(save_path, auto_path))
        except Exception as e:  # noqa: BLE001
            return _refuse(f"couldn't read {save}'s week: {e}", save=save)
        week = None if state is None else state.get("week")
        if week is None:
            return _refuse(f"couldn't read {save}'s week from the save file; the catch-up needs a "
                           f"regular-season or playoff save", save=save)
        # F1: the save's own calendar year, not just its week. A 2027 save under 2026 settings
        # must refuse before the PC, otherwise it advances the wrong season under the mirror's
        # data. An injected reader may carry no year; only the production reader must be complete.
        if production_reader and state.get("year") is None:
            return _refuse(f"couldn't read {save}'s calendar year from the save file; the catch-up needs "
                           f"it to match nfl_mirror_settings.season {season}", save=save, season=season)
        if _year_mismatch(state, season):
            return _refuse(f"{save} is the {state.get('year')} season, but nfl_mirror_settings.season is "
                           f"{season}: the catch-up only mirrors the settings' season", save=save,
                           week=week, season=season)
        week = int(week)
        try:
            mode = mode_of(week)
        except Exception as e:  # noqa: BLE001 - nflverse down: refuse before the PC
            return _refuse(f"couldn't read the real games for {season} week {week}: {e}", save=save)
        if mode not in ("CATCH_UP", PRO_BOWL):
            return _refuse(f"{season} week {week} is {mode or 'unknown'}, not CATCH_UP for the real NFL: the "
                           f"catch-up starts at the first week whose games are all final", save=save, week=week)
        load = load or (lambda: _load_franchise(pilot, tile, save=save, folder=folder))
        sim = sim or (lambda: _sim_one_week(pilot))
        quit_to_menu = quit_to_menu or (lambda: _quit_to_menu(pilot, franchise=save))
        weeks, stopped = 0, None

        def gate_settings(where=""):
            """Re-read nfl_mirror_settings and stop when this week is no longer mirrored.

            Called before the readiness wait and again after it resolves, because `_wait_written`
            can block for minutes: an operator disabling the mirror, moving the season or narrowing
            `last_week` during the wait must stop before the save is loaded or simmed, not one full
            sim later (audit-5 wave-5 R2, M-0565)."""
            try:
                latest = mirror.settings()
            except Exception as e:  # noqa: BLE001
                pilot.stop(f"couldn't re-read nfl_mirror_settings before {season} week {week}{where}: {e}", Stuck)
            if not latest or not latest.get("enabled"):
                pilot.stop(f"nfl_mirror_settings.enabled went false before {season} week {week}{where}: stopping "
                           f"before another unmirrored sim", Stuck)
            latest_season = int(latest.get("season") or CATCH_UP_SEASON)
            if latest_season != season:
                pilot.stop(f"nfl_mirror_settings.season moved to {latest_season} before {season} week {week}{where}: "
                           f"stopping rather than mirror the wrong season", Stuck)
            try:
                last_week = None if latest.get("last_week") is None else int(latest["last_week"])
            except (TypeError, ValueError):
                pilot.stop(f"nfl_mirror_settings.last_week is {latest.get('last_week')!r}, not a week number",
                           Stuck)
            # The week after this sim is the one the league office must write. The Pro Bowl has no
            # row, so only a real week is held to last_week; the next step (to the Super Bowl) is.
            if last_week is not None and week + 1 != PRO_BOWL_WEEK and week + 1 > last_week:
                pilot.stop(f"nfl_mirror_settings last_week is {last_week}: advancing {season} week {week} to "
                           f"week {week + 1} would wait for a mirror_active row it cannot create", Stuck)

        progress.update(save=save, season=season, start_week=week, end_week=week, weeks_simmed=0)
        while True:
            if to_week is not None and week >= to_week:
                stopped = "to-week"
                break
            if weeks >= MAX_CATCH_UP_WEEKS:
                pilot.stop(f"simmed {weeks} weeks without reaching a LIVE week or --to-week", Stuck)
            # F2: re-read the settings and gate this week before touching the PC. An operator's
            # disable between weeks must stop rather than sim an unmirrored roster, and the week
            # about to be simmed must already be WRITTEN (the failure/readiness check the old loop
            # only made for the week after a sim).
            gate_settings()
            if week != PRO_BOWL_WEEK:
                _wait_written(pilot, mirror, season, week, timeout=CATCH_UP_WRITE_TIMEOUT)
            # The wait can be long: the settings that authorised it may have changed while it ran.
            gate_settings(" after the readiness wait")
            load()
            try:
                before_state = _state_from_reader(reader, _latest_save(save_path, auto_path))
            except Exception as e:  # noqa: BLE001
                pilot.stop(f"couldn't read {save}'s week before the sim: {e}", Stuck)
            before = None if before_state is None else before_state.get("week")
            if before != week:
                pilot.stop(f"the save reads week {before}, expected week {week}: refusing to sim the "
                           f"wrong save", Stuck)
            if production_reader and before_state.get("year") is None:
                # R1 (audit-5, M-0565): a header that loses its calendar year on reload must not
                # authorise the sim. The initial read already refuses this; the reload read must too.
                pilot.stop(f"the save reloaded with no readable calendar year, expected {season}: refusing "
                           f"to sim a save whose season cannot be read", Stuck)
            if _year_mismatch(before_state, season):
                pilot.stop(f"the save reloaded as {before_state.get('year')} week {before}, but "
                           f"nfl_mirror_settings.season is {season}: refusing to sim a different season", Stuck)
            sim()
            try:
                after_state = _state_from_reader(reader, _latest_save(save_path, auto_path))
            except Exception as e:  # noqa: BLE001
                pilot.stop(f"couldn't read {save}'s week after the sim: {e}", Stuck)
            after = None if after_state is None else after_state.get("week")
            if after != before + 1:
                pilot.stop(f"one sim moved the save from week {before} to week {after}, not exactly one "
                           f"week; stopping before the league office sees it", Stuck)
            if production_reader and after_state.get("year") is None:
                # R1 (audit-5, M-0565): a post-sim header with no calendar year stops before the
                # quit, the save copy and the acknowledgment, not after them.
                pilot.stop(f"the save carries no readable calendar year after the sim, expected {season}: "
                           f"stopping before the league office sees it", Stuck)
            if _year_mismatch(after_state, season):
                pilot.stop(f"one sim moved the save into the {after_state.get('year')} season, but "
                           f"nfl_mirror_settings.season is {season}: stopping before the league office "
                           f"sees it", Stuck)
            quit_to_menu()
            _save_copy(save_path, auto_path)
            if after == PRO_BOWL_WEEK:
                # F3: Madden's Pro Bowl is a progression stop with no real NFL week and no
                # nfl_mirror_weeks row. Quit/copy above, then advance it deliberately instead of
                # returning None early or waiting out the 15-minute timeout.
                message = (f"catch-up: {season} week {before} simmed, Pro Bowl reached with no mirror "
                           f"row ({weeks + 1} this run)")
            else:
                _wait_written(pilot, mirror, season, after, timeout=CATCH_UP_WRITE_TIMEOUT)
                message = (f"catch-up: {season} week {before} simmed, mirror week {after} written "
                           f"({weeks + 1} this run)")
            weeks += 1
            week = after
            progress.update(weeks_simmed=weeks, end_week=week)
            _send(pilot, message, True)
            mode = mode_of(week)
            if mode not in ("CATCH_UP", PRO_BOWL):
                stopped = "live"
                break
        progress.update(stopped_because=stopped)
        return {"status": "ok", "save": save, "season": season, "start_week": progress.get("start_week"),
                "end_week": week, "weeks_simmed": weeks, "stopped_because": stopped, "to_week": to_week}
    finally:
        if own:
            mirror.close()


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
def _quit_now(pilot, franchise=None, allow_quit=False):
    """The quit itself, without the job decorator: back out to FRANCHISE_LIST (Back > YES)."""
    if franchise:
        pilot.loaded_franchise = norm(tile_name(franchise))
    _, screen = pilot.look()
    if screen == "FRANCHISE_LIST":
        return {"screen": screen, "quit": False, "why": "already on the franchise list"}
    pilot.goto("FRANCHISE_LIST", allow_quit=allow_quit)
    return {"screen": "FRANCHISE_LIST", "quit": True, "franchise": pilot.loaded_franchise}


@job("quit")
def quit_franchise(pilot, franchise=None, allow_quit=False, progress=None):
    """Back out of the loaded franchise to the franchise list (Back > YES). The hub never shows
    the save name, so a fresh pilot doesn't know what is loaded: pass franchise="TUNE-x" when you
    know, or allow_quit=True after JR says yes (quitting writes <save>-AUTOSAVE)."""
    return {"status": "ok", **_quit_now(pilot, franchise=franchise, allow_quit=allow_quit)}


JOBS = {"launch": launch, "clone-save": clone_save, "sim-season": sim_season, "advance": advance,
        "watch-game": watch_game, "catch-up": catch_up, "set-setting": set_setting, "verify-mods": verify_mods,
        "quit": quit_franchise}
