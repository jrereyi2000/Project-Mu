"""
The weekly run (docs/PIPELINE_MIGRATION.md, phase 5: target weekly flow, steps 2-9), started on demand
from the debug page (#debug, server/debug_api.py: Scan week / Backfill previous week / Game scan) or by hand:

  python3 -m pipeline.weekly_capture                  # connect, capture, extract, load, backfill + engines, articles, shrink
  python3 -m pipeline.weekly_capture --no-articles    # the same without articles (backfill previous week)
  python3 -m pipeline.weekly_capture --mode games     # every game of last week: box scores + player stats, loaded
                                                      # with --replace over the backfill's lines; never articles
  python3 -m pipeline.weekly_capture --resume <run>   # continue captures/<run> from the step it stopped on

Steps (each one's failure stops the week: nothing after it runs, and a notice goes to JR's phone):
  2. connect   madden_capture.py --connect: start or restart the chiaki-ng stream and wait up to 2 minutes
               for a franchise screen (chiaki stream lands on it directly). Presses nothing.
  3. capture   madden_capture.py --out <run>: Season Stats, Team Stats and last week's schedule
               (games mode: --only games, ~7 min a game, resuming per game).
  4. hand back notice: turn the controller back on and restart the broadcast (the script doesn't disconnect).
  5. extract   extract.py captures/<run>.
  6. load      pipeline.load_capture, one transaction; a failed check writes nothing.
  7. engines   the backfill and the analytics engines for league_state.current_season (strict: an
               engine error stops the week).
  8. articles  newsroom.weekly_run (skips a week already published).
  9. shrink    screenshots -> WebP q90 at full resolution, debug folders deleted, CSVs kept.

Resuming picks the first step not done: capture is done when the run wrote .capture_done (a weekly run
from before that marker: week.json), extract when the mode's CSVs exist (weekly: schedule.csv + stats_*.csv;
games: game_scores.csv), load when the captures row is 'loaded'.
The backfill only fills missing games and the articles skip a published week, so both are safe to rerun.
Only one weekly run at a time (lock file captures/.weekly_run.lock).
"""
import argparse, datetime, fcntl, glob, os, shutil, subprocess, sys, time, traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAPTURES = os.path.join(ROOT, "captures")
LOCK_PATH = os.path.join(CAPTURES, ".weekly_run.lock")
CAPTURE_DIRS = ("stats", "team_stats", "schedule", "stop_debug", "scope_debug", "week_debug")
DEBUG_DIRS = ("stop_debug", "scope_debug", "week_debug", "team_debug", "probe", "explore")
CONNECT_TIMEOUT = 6 * 60       # madden_capture --connect: up to 2 min, twice when it has to relaunch
CAPTURE_TIMEOUT = 75 * 60      # the weekly capture takes ~20 min
GAMES_TIMEOUT = 4 * 60 * 60    # --only games: ~7 min a game, 16 games
MODES = ("weekly", "games")


class WeekStopped(Exception):
    """A step failed: the week stops here."""


def notify(message, **kw):
    from pipeline.notify import notify as send
    return send(message, **kw)


def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] {msg}", flush=True)


def run_cmd(args, log_path, timeout):
    """Run a command from the project root, echoing its output and appending it to log_path.
    Returns (exit code, last non-empty output line)."""
    last = ""
    with open(log_path, "a") as fh:
        fh.write(f"\n$ {' '.join(args)}\n")
        p = subprocess.Popen(args, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        end = time.time() + timeout
        for line in p.stdout:
            sys.stdout.write(line)
            fh.write(line)
            if line.strip():
                last = line.strip()
            if time.time() > end:
                p.kill()
                return -9, f"timed out after {timeout // 60} minutes"
        return p.wait(), last


# --------------------------------------------------------------- state ----
def capture_done(folder, mode="weekly"):
    if os.path.exists(os.path.join(folder, ".capture_done")):
        return True
    return mode == "weekly" and os.path.exists(os.path.join(folder, "week.json"))


def extract_done(folder, mode="weekly"):
    if mode == "games":
        return os.path.exists(os.path.join(folder, "game_scores.csv"))
    return (os.path.exists(os.path.join(folder, "schedule.csv"))
            and bool(glob.glob(os.path.join(folder, "stats_*.csv"))))


def load_status(folder):
    """The run's captures row status, or None."""
    import psycopg
    from pipeline.load_capture import DSN
    with psycopg.connect(DSN) as conn:
        row = conn.execute("SELECT status FROM captures WHERE folder = %s",
                           (os.path.basename(os.path.normpath(folder)),)).fetchone()
    return row[0] if row else None


def league_state():
    import psycopg
    from pipeline.load_capture import DSN
    with psycopg.connect(DSN) as conn:
        return conn.execute("SELECT current_season, current_week FROM league_state WHERE id = 1").fetchone()


def captured_week(folder):
    """The schedule week the capture took ('SEASON WEEK 17'), from week.json."""
    import json
    try:
        return json.load(open(os.path.join(folder, "week.json"))).get("captured")
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------- steps ----
def step_connect(log_path):
    code, last = run_cmd([sys.executable, "madden_capture.py", "--connect"], log_path, CONNECT_TIMEOUT)
    if code != 0:
        raise WeekStopped(f"Couldn't reach Madden's franchise menus over chiaki-ng: {last}. Nothing was pressed.")


def step_capture(folder, log_path, mode="weekly"):
    name = os.path.basename(folder)
    if mode == "games":                        # resumes per game (games/<NN>/DONE)
        where = ["--resume", name] if os.path.isdir(os.path.join(folder, "games")) else ["--out", name]
        args, timeout = [sys.executable, "madden_capture.py", "--only", "games", *where], GAMES_TIMEOUT
    else:
        for d in CAPTURE_DIRS:                 # a re-capture starts the three modes clean
            shutil.rmtree(os.path.join(folder, d), ignore_errors=True)
        if os.path.exists(os.path.join(folder, "week.json")):
            os.remove(os.path.join(folder, "week.json"))
        args, timeout = [sys.executable, "madden_capture.py", "--out", name], CAPTURE_TIMEOUT
    code, last = run_cmd(args, log_path, timeout)
    if code != 0 or (mode == "weekly" and not os.path.exists(os.path.join(folder, "week.json"))):
        raise WeekStopped(f"Capture stopped: {last}. The console is left where it stopped; frames in "
                          f"{os.path.relpath(folder, ROOT)}/stop_debug.")
    open(os.path.join(folder, ".capture_done"), "w").close()


def step_extract(folder, log_path, mode="weekly"):
    code, last = run_cmd([sys.executable, "extract.py", folder], log_path, 60 * 60)
    if code != 0 or not extract_done(folder, mode):
        raise WeekStopped(f"extract.py failed: {last}")


def step_load(folder, replace=False):
    from pipeline.load_capture import LoadError, load
    try:
        report = load(folder, replace=replace)
    except LoadError as e:
        raise WeekStopped(f"Load stopped, nothing written: {e}")
    except Exception as e:
        raise WeekStopped(f"Load failed, nothing written: {e!r}")
    return report


def step_engines(season):
    from engines.game_stat_generator import generate_team_and_player_stats
    try:
        generate_team_and_player_stats(season_year=season, strict=True)
    except Exception as e:
        traceback.print_exc()
        raise WeekStopped(f"Backfill / engines failed after the load (the week's stats are stored; no "
                          f"articles): {e!r}")


def step_articles(season):
    from newsroom.weekly_run import run_weekly_swarm
    try:
        res = run_weekly_swarm(season=season)
    except Exception as e:
        traceback.print_exc()
        raise WeekStopped(f"Articles failed: {e!r}")
    return res


def shrink(folder):
    """Screenshots -> WebP quality 90 at full resolution; debug folders deleted; CSVs and logs kept.
    Returns (bytes before, bytes after)."""
    from PIL import Image

    def size():
        return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(folder) for f in fs)

    before = size()
    for d in DEBUG_DIRS:
        shutil.rmtree(os.path.join(folder, d), ignore_errors=True)
    for png in glob.glob(os.path.join(folder, "**", "*.png"), recursive=True):
        with Image.open(png) as im:
            im.save(png[:-4] + ".webp", "WEBP", quality=90)
        os.remove(png)
    return before, size()


# ----------------------------------------------------------------- run ----
def run(resume=None, articles=True, connect=True, mode="weekly"):
    """One weekly run (mode 'weekly', or 'games' for every game of last week, never with articles).
    Returns 0 when the week went through, 1 when it stopped (notice sent)."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    articles = articles and mode == "weekly"
    what = "Game scan" if mode == "games" else "Weekly capture"
    os.makedirs(CAPTURES, exist_ok=True)
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("Another weekly run is in progress; not starting a second one")
        return 1
    name = resume or datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    folder = os.path.join(CAPTURES, name)
    if resume and not os.path.isdir(folder):
        log(f"No capture folder {folder}")
        return 1
    os.makedirs(folder, exist_ok=True)
    log_path = os.path.join(folder, "weekly_run.log")
    started = time.time()
    try:
        if not capture_done(folder, mode):
            notify(f"{what} starting ({name}). Remote Play is taking over the console.",
                   title="Mu: capture starting", tags=("video_game",))
            if connect:
                log("Step 2: connect")
                step_connect(log_path)
            log("Step 3: capture")
            step_capture(folder, log_path, mode)
            mins = (time.time() - started) / 60
            notify(f"{what} done in {mins:.0f} min ({captured_week(folder) or 'last week'}). Turn your controller "
                   "back on and restart the broadcast.", title="Mu: your turn", priority="high",
                   tags=("joystick",))
        else:
            log("Capture already done; skipping connect and capture")
        if not extract_done(folder, mode):
            log("Step 5: extract")
            step_extract(folder, log_path, mode)
        status = load_status(folder)
        if status != "loaded":
            log("Step 6: load")
            report = step_load(folder, replace=(mode == "games"))
            log(f"Loaded: {report.get('mode')} week {report.get('week')}")
        else:
            log("Already loaded; skipping the load")
        season, current_week = league_state()
        log(f"Step 7: backfill + engines (season {season})")
        step_engines(season)
        published = None
        if articles:
            log("Step 8: articles")
            res = step_articles(season)
            published = f"slate for Week {res.get('week')}: {res.get('status')}"
            if res.get("status") not in ("published", "skipped_exists", "season_complete"):
                raise WeekStopped(f"Articles didn't publish ({res})")
        log("Step 9: shrink")
        before, after = shrink(folder)
        log(f"Capture folder {before / 1e6:.0f} MB -> {after / 1e6:.0f} MB")
        notify(f"{captured_week(folder) or 'Week'} loaded; league now at week {current_week}. "
               f"{published or 'Articles not run.'}", title="Mu: week done", tags=("white_check_mark",))
        return 0
    except WeekStopped as e:
        log(f"WEEK STOPPED: {e}")
        notify(f"{e}\nResume by hand: python3 -m pipeline.weekly_capture --resume {name}",
               title="Mu: week stopped", priority="urgent", tags=("warning",))
        return 1
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--resume", metavar="RUN", help="continue captures/RUN from the step it stopped on")
    ap.add_argument("--no-articles", action="store_true", help="stop after the backfill and engines")
    ap.add_argument("--mode", choices=MODES, default="weekly",
                    help="games: every game of last week (box scores + player stats), loaded with --replace")
    ap.add_argument("--no-connect", action="store_true",
                    help="skip the connect step (chiaki-ng is already streaming the franchise menus)")
    a = ap.parse_args()
    sys.exit(run(a.resume, articles=not a.no_articles, connect=not a.no_connect, mode=a.mode))


if __name__ == "__main__":
    main()
