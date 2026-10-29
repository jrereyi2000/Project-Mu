"""
pipeline/watch_save.py: Debounced save watcher daemon for Madden NFL PC franchise saves
(docs/in_progress/PC_MIGRATION_PLAN.md §5.2, docs/done/PRE_PC_WORK.md §4).

Watches a directory for CAREER-* save files, debounces write bursts from Madden,
snapshots the save via pc_save_reader.mjs, and runs the weekly ingestion pipeline:
  load_save (--commit) -> engines -> newsroom articles

Usage:
  python3 -m pipeline.watch_save [PATH] [OPTIONS]

Options:
  PATH                     Save file or directory to watch (default: MADDEN_SAVES_DIR or ".")
  --saves-dir DIR          Directory to watch for CAREER-* files
  --pattern GLOB           Pattern for save files (default: "CAREER-*")
  --debounce SECONDS       Debounce wait time in seconds (default: 10)
  --poll-interval SECONDS  Polling interval for daemon loop (default: 2)
  --once                   Check once and exit
  --dry-run                Load without commit, skip engines and articles
  --no-articles            Run loader and engines, skip newsroom articles
  --season N               Override season year (default: save's calendar year)
  --force                  Proceed even if week is same as or older than league_state
  --auto-init              Auto-seed clean database if unseeded or new franchise detected
  --user-team TEAMS        Comma-separated user-controlled team abbreviations (e.g. IND)
  --reseed                 Force clean re-seeding of database on save detection
  --lock-file PATH         Custom lock file path (default: captures/.weekly_run.lock)
"""
import argparse
import datetime
import fcntl
import glob
import json
import os
import signal
import subprocess
import sys
import shutil
import time
import traceback
from contextlib import contextmanager

from pipeline.load_save import current_week, DSN
from pipeline.notify import env, notify as send_notify
from pipeline.weekly_capture import run_weekly_engines, step_articles, step_preseason_engines, WeekStopped

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAPTURES = os.path.join(ROOT, "captures")
READER_SCRIPT = os.path.join(ROOT, "pipeline", "save_reader", "pc_save_reader.mjs")
DEFAULT_LOCK_PATH = os.path.join(CAPTURES, ".weekly_run.lock")
DEFAULT_DEBOUNCE = 10
DEFAULT_POLL_INTERVAL = 2


class WatcherError(Exception):
    """Base error for watch_save daemon."""
    pass


class LockError(WatcherError):
    """Raised when unable to acquire the run lock."""
    pass


def log(msg):
    print(f"[{datetime.datetime.now():%H:%M:%S}] [watch_save] {msg}", flush=True)


@contextmanager
def run_lock(lock_path):
    """File lock using fcntl to prevent concurrent pipeline/weekly runs."""
    os.makedirs(os.path.dirname(os.path.abspath(lock_path)), exist_ok=True)
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError) as e:
        lock_file.close()
        raise LockError(f"Another pipeline run is in progress ({lock_path} locked): {e}")
    try:
        yield
    finally:
        try:
            fcntl.flock(lock_file, fcntl.LOCK_UN)
        except OSError:
            pass
        lock_file.close()


def find_save_files(watch_path, pattern="CAREER-*"):
    """Finds save files to watch: either a single file or matching pattern in directory.
    Returns files sorted by modification time (most recently modified first)."""
    if not os.path.exists(watch_path):
        return []
    if os.path.isfile(watch_path):
        return [os.path.abspath(watch_path)]
    matches = glob.glob(os.path.join(watch_path, pattern))
    # Exclude directories or lock files
    files = [os.path.abspath(f) for f in matches if os.path.isfile(f) and not f.endswith(".lock")]
    files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return files


class FileDebouncer:
    """Tracks modification times and sizes to debounce write bursts."""

    def __init__(self, debounce_seconds=DEFAULT_DEBOUNCE):
        self.debounce_seconds = debounce_seconds
        self.states = {}  # path -> dict

    def is_settled(self, path, now=None):
        """Returns True if the file size > 0, hasn't changed, and mtime is >= debounce_seconds old."""
        now = now if now is not None else time.time()
        try:
            st = os.stat(path)
            mtime = st.st_mtime
            size = st.st_size
        except OSError:
            return False

        if size == 0:
            return False

        if path not in self.states:
            # First time observing this file
            age = now - mtime
            if age >= self.debounce_seconds:
                # File hasn't been touched in a long time; already settled
                self.states[path] = {
                    "last_mtime": mtime,
                    "last_size": size,
                    "stable_since": mtime,
                    "last_processed_mtime": None,
                }
                return True
            else:
                self.states[path] = {
                    "last_mtime": mtime,
                    "last_size": size,
                    "stable_since": now,
                    "last_processed_mtime": None,
                }
                return False

        state = self.states[path]
        if mtime != state["last_mtime"] or size != state["last_size"]:
            # File is actively changing
            state["last_mtime"] = mtime
            state["last_size"] = size
            state["stable_since"] = now
            return False

        # File unchanged since last check
        if (now - state["stable_since"] >= self.debounce_seconds) and (now - mtime >= self.debounce_seconds):
            return True

        return False

    def is_already_processed(self, path):
        """Returns True if this exact file modification was already processed."""
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return False
        state = self.states.get(path)
        return state is not None and state.get("last_processed_mtime") == mtime

    def mark_processed(self, path):
        """Records that the current mtime of the file has been processed."""
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return
        if path not in self.states:
            self.states[path] = {}
        self.states[path]["last_processed_mtime"] = mtime


def wait_for_settle(path, debounce_seconds=DEFAULT_DEBOUNCE, timeout=60, poll_interval=1):
    """Synchronously wait for a file to settle. Useful for --once mode."""
    debouncer = FileDebouncer(debounce_seconds=debounce_seconds)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if debouncer.is_settled(path):
            return True
        time.sleep(poll_interval)
    return False


def get_league_state(dsn=None):
    """Returns (current_season, current_week) from league_state table, or (None, None) if unseeded/empty."""
    import psycopg
    conn_dsn = dsn or DSN
    try:
        with psycopg.connect(conn_dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT to_regclass('public.league_state')")
                reg = cur.fetchone()
                if not reg or not reg[0]:
                    return None, None
                cur.execute("SELECT current_season, current_week FROM league_state WHERE id = 1")
                row = cur.fetchone()
                if not row:
                    return None, None
                return int(row[0]), int(row[1])
    except Exception:
        return None, None


def classify_week(save_season, save_week, stored_season, stored_week):
    """
    Classifies a save snapshot against stored league_state:
      - 'new_week': save advances current week or rolls over season
      - 'same_week': save matches current season & week
      - 'older_week': save is behind stored season & week
      - 'different_franchise': save season is from another franchise (> 1 year jump)
      - 'no_current_week': save is not in a regular season or playoff week
    """
    if save_week is None:
        return "no_current_week"
    if save_season == stored_season:
        if save_week > stored_week:
            return "new_week"
        elif save_week == stored_week:
            return "same_week"
        else:
            return "older_week"
    elif save_season == stored_season + 1:
        return "new_week"
    elif save_season < stored_season:
        return "older_week"
    else:
        return "different_franchise"


def snapshot_save(save_path, run_folder, reader_script=READER_SCRIPT, node_bin="node"):
    """Runs pc_save_reader.mjs on save_path and writes save.json into run_folder."""
    os.makedirs(run_folder, exist_ok=True)
    snapshot_path = os.path.join(run_folder, "save.json")
    cmd = [node_bin, reader_script, save_path, "--out", snapshot_path]
    log(f"Reading save snapshot: {' '.join(cmd)}")
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        err = p.stderr.strip() or p.stdout.strip()
        raise WatcherError(f"Save reader failed (exit {p.returncode}): {err}")
    if not os.path.exists(snapshot_path):
        raise WatcherError(f"Save reader did not write {snapshot_path}")
    return snapshot_path


def queue_nightly_jobs(season, week, log_fn=log):
    """Hook point for Plan §7.5 local-model jobs going onto the nightly queue."""
    log_fn(f"Nightly queue hook: season {season} week {week} (local-model jobs queued when queue is active)")


def process_save(save_path, season=None, dry_run=False, no_articles=False, force=False,
                 auto_init=False, reseed=False, user_teams=None,
                 run_folder=None, dsn=None, reader_script=READER_SCRIPT, log_fn=log):
    """
    Core processing for a settled save file:
    1. Snapshots into captures/<run>/save.json
    2. Auto-initializes clean DB if unseeded or --auto-init / --reseed is set
    3. Compares save season & week against league_state
    4. If new week (or force or just_seeded): runs load_save -> engines -> articles
    """
    from pipeline import load_save

    if run_folder is None:
        run_name = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        run_folder = os.path.join(CAPTURES, run_name)

    snapshot_path = snapshot_save(save_path, run_folder, reader_script=reader_script)
    with open(snapshot_path) as fh:
        snapshot = json.load(fh)

    save_season = season or snapshot.get("season", {}).get("calendar_year")
    save_week = current_week(snapshot)
    stored_season, stored_week = get_league_state(dsn)

    just_seeded = False
    needs_seed = (stored_season is None) or (reseed and stored_season is not None)

    if auto_init and not needs_seed and stored_season is not None:
        cls_check = classify_week(save_season, save_week, stored_season, stored_week)
        if cls_check in ("different_franchise", "older_week"):
            log_fn(f"Auto-init: detected {cls_check} (save: {save_season} vs stored: {stored_season}). Reseeding DB...")
            needs_seed = True

    if needs_seed:
        if not auto_init and stored_season is None:
            raise WatcherError(
                "Database is not initialized (no league_state found). "
                "Run with --auto-init to automatically seed the clean DB from the save."
            )
        initial_week = save_week if save_week is not None else 1
        log_fn(f"Auto-seeding clean database for Season {save_season}, Week {initial_week}...")
        from db.seed_clean_db import run_clean_seed
        run_clean_seed(
            dsn=dsn or DSN,
            season=save_season,
            week=initial_week,
            user_teams=user_teams or [],
            dry_run=dry_run,
            log=log_fn,
        )
        stored_season, stored_week = save_season, initial_week
        just_seeded = True

    log_fn(f"Save: season {save_season}, week {save_week} | League state: season {stored_season}, week {stored_week}")

    if not force and not just_seeded:
        classification = classify_week(save_season, save_week, stored_season, stored_week)
        if classification == "same_week":
            log_fn(f"Save {os.path.basename(save_path)} is at season {save_season} week {save_week}, same as league_state. No-op.")
            shutil.rmtree(run_folder, ignore_errors=True)
            return {"status": "no_op", "reason": "same_week", "season": save_season, "week": save_week}

        if classification in ("older_week", "different_franchise"):
            msg = (f"Watched save {os.path.basename(save_path)} is at season {save_season} week {save_week}, "
                   f"which is {classification.replace('_', ' ')} (league_state: {stored_season} week {stored_week}). No-op.")
            log_fn(f"[WARN] {msg}")
            send_notify(msg, title="Mu save watcher: older save or different franchise", priority="default", tags=("warning",))
            shutil.rmtree(run_folder, ignore_errors=True)
            return {"status": "no_op", "reason": classification, "season": save_season, "week": save_week}

        if classification == "no_current_week":
            log_fn(f"Save {os.path.basename(save_path)} is not at a regular season or playoff week (season {save_season}). No-op.")
            shutil.rmtree(run_folder, ignore_errors=True)
            return {"status": "no_op", "reason": "no_current_week", "season": save_season, "week": save_week}

    log_fn(f"Ingesting week: season {save_season} week {save_week} (dry_run={dry_run}, no_articles={no_articles})")

    # Step: load_save
    load_report = load_save.load(snapshot_path, season=save_season, commit=(not dry_run))
    log_fn(f"Load report: {load_report.get('league_state')}, players: {load_report.get('players')}, "
           f"completed games: {load_report.get('games_completed')}")

    if dry_run:
        log_fn("Dry run mode: database changes rolled back, skipping engines and articles.")
        return {"status": "success", "dry_run": True, "season": save_season, "week": save_week, "load_report": load_report}

    # Step: engines
    if save_week is None:
        log_fn(f"Step 7: start-of-season calibration engines (season {save_season})")
        step_preseason_engines(save_season)
    else:
        run_weekly_engines(save_season, mode="weekly", log_fn=log_fn)
        queue_nightly_jobs(save_season, save_week, log_fn=log_fn)

    # Step: articles
    published = None
    if not no_articles and save_week is not None:
        log_fn("Step 8: newsroom articles")
        res = step_articles(save_season, week=save_week)
        published = f"slate for Week {res.get('week')}: {res.get('status')}"
        if res.get("status") not in ("published", "skipped_exists", "season_complete"):
            raise WeekStopped(f"Articles did not publish ({res})")

    send_notify(
        f"Ingested season {save_season} week {save_week or 'preseason'} from {os.path.basename(save_path)}. "
        f"{published or 'Articles skipped.'}",
        title="Mu: week done",
        tags=("white_check_mark",)
    )

    return {"status": "success", "dry_run": False, "season": save_season, "week": save_week, "load_report": load_report}


def watch_loop(watch_path, pattern="CAREER-*", debounce_seconds=DEFAULT_DEBOUNCE,
               poll_interval=DEFAULT_POLL_INTERVAL, dry_run=False, no_articles=False,
               season=None, force=False, auto_init=False, reseed=False, user_teams=None,
               lock_path=DEFAULT_LOCK_PATH, log_fn=log):
    """Continuous polling daemon loop."""
    debouncer = FileDebouncer(debounce_seconds=debounce_seconds)
    running = True

    def _sig_handler(sig, frame):
        nonlocal running
        log_fn("Received termination signal, shutting down watcher...")
        running = False

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    log_fn(f"Watching '{watch_path}' (pattern: {pattern}, debounce: {debounce_seconds}s, poll: {poll_interval}s)")

    while running:
        save_files = find_save_files(watch_path, pattern=pattern)
        for save_file in save_files:
            if debouncer.is_already_processed(save_file):
                continue

            if debouncer.is_settled(save_file):
                log_fn(f"Settled save file detected: {save_file}")
                try:
                    with run_lock(lock_path):
                        res = process_save(
                            save_file, season=season, dry_run=dry_run, no_articles=no_articles,
                            force=force, auto_init=auto_init, reseed=reseed, user_teams=user_teams,
                            log_fn=log_fn
                        )
                        debouncer.mark_processed(save_file)
                        log_fn(f"Result for {os.path.basename(save_file)}: {res.get('status')}")
                except LockError as e:
                    log_fn(f"Lock active: {e}. Will retry on next check.")
                except Exception as e:
                    traceback.print_exc()
                    err_msg = f"Save watcher error on {os.path.basename(save_file)}: {e}"
                    log_fn(f"[ERROR] {err_msg}")
                    send_notify(err_msg, title="Mu: save watcher error", priority="high", tags=("warning", "x"))
                    # Mark processed so we don't immediately crash in an infinite loop on the same failing save
                    debouncer.mark_processed(save_file)

        time.sleep(poll_interval)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", help="Save file or folder to watch (default: MADDEN_SAVES_DIR or .)")
    ap.add_argument("--saves-dir", help="Directory to watch for CAREER-* files")
    ap.add_argument("--pattern", default="CAREER-*", help="Save filename pattern (default: CAREER-*)")
    ap.add_argument("--debounce", type=int, default=DEFAULT_DEBOUNCE, help=f"Debounce wait in seconds (default: {DEFAULT_DEBOUNCE})")
    ap.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL, help=f"Poll interval in seconds (default: {DEFAULT_POLL_INTERVAL})")
    ap.add_argument("--once", action="store_true", help="Check once and exit")
    ap.add_argument("--dry-run", action="store_true", help="Load without commit, no articles or engines")
    ap.add_argument("--no-articles", action="store_true", help="Run loader and engines, skip newsroom articles")
    ap.add_argument("--season", type=int, help="Override season year (default: save's calendar year)")
    ap.add_argument("--force", action="store_true", help="Run even if week is same as or older than league_state")
    ap.add_argument("--auto-init", action="store_true", help="Auto-seed clean database if unseeded or new franchise detected")
    ap.add_argument("--user-team", default="", help="Comma-separated abbreviations of user-controlled teams (e.g. IND or IND,CAR)")
    ap.add_argument("--reseed", action="store_true", help="Force clean re-seeding of database on save detection")
    ap.add_argument("--lock-file", default=DEFAULT_LOCK_PATH, help=f"Lock file path (default: {DEFAULT_LOCK_PATH})")
    args = ap.parse_args()

    watch_path = args.path or args.saves_dir or env("MADDEN_SAVES_DIR", ".")
    watch_path = os.path.abspath(watch_path)
    user_teams = [t.strip().upper() for t in args.user_team.split(",") if t.strip()]

    if not os.path.exists(watch_path):
        log(f"Error: watch path '{watch_path}' does not exist.")
        sys.exit(1)

    if args.once:
        save_files = find_save_files(watch_path, pattern=args.pattern)
        if not save_files:
            log(f"No matching save files found in '{watch_path}' (pattern: {args.pattern}).")
            sys.exit(0)

        target_save = save_files[0]
        log(f"--once mode: targeting latest save {target_save}")
        if not wait_for_settle(target_save, debounce_seconds=args.debounce, timeout=30):
            log(f"Save file {target_save} is actively changing (did not settle within timeout).")
            sys.exit(1)

        try:
            with run_lock(args.lock_file):
                res = process_save(
                    target_save, season=args.season, dry_run=args.dry_run,
                    no_articles=args.no_articles, force=args.force,
                    auto_init=args.auto_init, reseed=args.reseed, user_teams=user_teams,
                    log_fn=log
                )
                log(f"Execution completed: {res.get('status')}")
                sys.exit(0)
        except LockError as e:
            log(f"Cannot run: {e}")
            sys.exit(1)
        except Exception as e:
            traceback.print_exc()
            err_msg = f"Save watcher error on {os.path.basename(target_save)}: {e}"
            log(f"[ERROR] {err_msg}")
            send_notify(err_msg, title="Mu: save watcher error", priority="high", tags=("warning", "x"))
            sys.exit(1)
    else:
        watch_loop(
            watch_path, pattern=args.pattern, debounce_seconds=args.debounce,
            poll_interval=args.poll_interval, dry_run=args.dry_run,
            no_articles=args.no_articles, season=args.season, force=args.force,
            auto_init=args.auto_init, reseed=args.reseed, user_teams=user_teams,
            lock_path=args.lock_file, log_fn=log
        )


if __name__ == "__main__":
    main()
