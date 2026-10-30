"""
pipeline/watch_save.py: Debounced save watcher daemon for Madden NFL PC franchise saves
(docs/in_progress/PC_MIGRATION_PLAN.md §5.2, docs/done/PRE_PC_WORK.md §4).

Watches a directory for CAREER-* save files, debounces write bursts from Madden,
snapshots the save via pc_save_reader.mjs, routes it to its franchise's database, and runs
the weekly ingestion pipeline there:
  load_save (--commit) -> engines -> newsroom articles

Each franchise has its own database (db/franchises.py), found by the save's Franchise.LeagueID, so
saves from several franchises can share the folder and every save slot of one franchise lands in the
same database. A new franchise is only set up with --auto-init: its database is created, built from
db/init_db.sql, seeded by db/seed_clean_db.py with the save's season and week, and stamped with the
league id. A database that already has data is never seeded again; --reseed drops and recreates one
franchise's database and needs --confirm-reseed <its database name>. The load, engines and articles
run in a child process with DATABASE_URL set to the franchise's database, since most modules read
DATABASE_URL when they are imported.

Usage:
  python3 -m pipeline.watch_save [PATH] [OPTIONS]

Options:
  PATH                     Save file or directory to watch (default: MADDEN_SAVES_DIR or ".")
  --saves-dir DIR          Directory to watch for CAREER-* files
  --pattern GLOB           Pattern for save files (default: "CAREER-*")
  --debounce SECONDS       Debounce wait time in seconds (default: 10)
  --poll-interval SECONDS  Polling interval for daemon loop (default: 2)
  --once                   Process the newest save once and exit
  --dry-run                Load without commit, skip engines and articles; never creates or seeds a database
  --no-articles            Run loader and engines, skip newsroom articles
  --no-engines             Run the loader only (no engines, no articles)
  --season N               Override season year (default: save's calendar year)
  --force                  Proceed even if week is same as or older than league_state
  --auto-init              Create and seed the database of a franchise seen for the first time
  --franchise-name NAME    Name for a new franchise (its database becomes madden_<name>)
  --user-team TEAMS        Comma-separated user-controlled team abbreviations for a new franchise (e.g. IND)
  --reseed                 Drop and recreate this franchise's database, then seed it (--once only)
  --confirm-reseed DB      Required with --reseed: the database name being dropped
  --dsn DSN                Load into this database instead of routing by franchise (tests, one-off loads)
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

from db import franchises
from pipeline.load_save import current_week
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
    Returns files oldest-modified first, so a backlog of weekly saves loads in week order."""
    if not os.path.exists(watch_path):
        return []
    if os.path.isfile(watch_path):
        return [os.path.abspath(watch_path)]
    matches = glob.glob(os.path.join(watch_path, pattern))
    # Exclude directories or lock files
    files = [os.path.abspath(f) for f in matches if os.path.isfile(f) and not f.endswith(".lock")]
    files.sort(key=lambda p: (os.path.getmtime(p), p))
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




def get_league_state(dsn):
    """Returns (current_season, current_week) from league_state table, or (None, None) if unseeded/empty."""
    import psycopg
    try:
        with psycopg.connect(dsn) as conn:
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
    Classifies a save snapshot against its own franchise's league_state (franchises are told apart by
    league id, db/franchises.py, never by season):
      - 'new_week': save advances current week or rolls over season
      - 'same_week': save matches current season & week
      - 'older_week': save is behind stored season & week
      - 'season_gap': save is more than one season ahead (whole seasons were never read)
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
        return "season_gap"


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
    try:
        from pipeline.gpu_queue.queue import GPUQueue
        from pipeline.gpu_queue.runner import GPUQueueRunner

        queue = GPUQueue()
        pending = queue.list_jobs(status="pending")
        log_fn(f"GPU queue hook: checked queue, {len(pending)} pending jobs found.")
        if pending:
            runner = GPUQueueRunner(queue=queue, log_fn=log_fn)
            processed = runner.run_loop(stop_on_empty=True)
            log_fn(f"GPU queue hook: processed {processed} pending jobs.")
    except Exception as e:
        log_fn(f"Warning: GPU queue hook check encountered an error: {e}")


def prepare_next_week_mod_pack(dsn, season, next_week, log_fn=log):
    """Prepares and logs next-week mod pack plan for user-controlled teams."""
    if not next_week or not season:
        return
    try:
        from db import franchises
        from pipeline import mod_packs

        reg = franchises.load_registry()
        db = db_name_of(dsn) if dsn else None
        _, entry = franchises.find(reg, db) if db else (None, None)
        if not entry:
            _, entry = franchises.active_entry(reg)
        user_teams = (entry or {}).get("user_teams") or []
        if not user_teams:
            log_fn("Next-week mod pack: no user teams registered in franchises.json; skipping.")
            return

        import psycopg
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            for team in user_teams:
                games = mod_packs.load_games(cur, season, week=next_week, team=team)
                if not games:
                    log_fn(f"Next-week mod pack: no scheduled game found for user team {team} in season {season} week {next_week}.")
                    continue
                for g in games:
                    c = mod_packs.choose(g)
                    pres = c.window
                    music = c.window if c.window in ("CBS", "FOX") else "None (Primetime)"
                    crowd = f"Tier {c.tier}" if c.tier != "FULL" else "FULL"
                    ball = "BCA Pink" if c.bca else "Mudded"
                    log_fn(
                        f"Next-week mod pack plan for {team} (Week {next_week}, {g.away} @ {g.home}, {g.game_time}): "
                        f"{c.pack_name} | Presentation: {pres}, Music: {music}, Crowd: {crowd} (score: {c.score}), Ball: {ball}"
                    )
    except Exception as e:
        log_fn(f"Warning: next-week mod pack preparation skipped: {e}")


def franchise_id(snapshot, save_path):
    """The save's Franchise.LeagueID (reader v3+), else the save's file name for older snapshots."""
    league_id = (snapshot.get("franchise") or {}).get("league_id")
    if league_id is not None:
        return str(int(league_id))
    return "file_" + franchises.slugify(os.path.basename(save_path))


def db_name_of(dsn):
    from psycopg.conninfo import conninfo_to_dict
    return conninfo_to_dict(dsn).get("dbname") or dsn


def route_franchise(fid, save_name, save_season, save_week, auto_init=False, reseed=False, confirm_reseed=None,
                    user_teams=None, franchise_name=None, dry_run=False, log_fn=log):
    """
    Finds (or, with --auto-init, sets up) the franchise's database. Returns
    {"status": "ready", "db", "dsn", "seeded"}, or {"status": "no_op", "reason", "db"} when a dry run would
    have had to create or seed it. Never seeds a database that already has data.
    """
    with franchises.editing_registry() as reg:
        _, entry = franchises.find(reg, fid)
        if entry is None:
            if not auto_init:
                raise WatcherError(
                    f"{save_name} is from a franchise not seen before (league id {fid}). "
                    "Run with --auto-init (and --franchise-name, --user-team) to create its database."
                )
            if dry_run:
                db = franchises.db_name_for(fid, franchise_name, {e["db"] for e in reg["franchises"].values()})
                log_fn(f"Dry run: would register new franchise {fid} as {db}, then create and seed it.")
                return {"status": "no_op", "reason": "new_franchise", "db": db}
            entry = franchises.register(reg, fid, name=franchise_name, user_teams=user_teams, save_name=save_name)
            log_fn(f"New franchise {fid} registered as {entry['db']}"
                   + (" (now the active franchise)" if reg["active"] == fid else ""))
        else:
            franchises.note_save(entry, save_name)
        entry = dict(entry)

    db = entry["db"]
    dsn = franchises.dsn_for_db(db)

    if reseed:
        if confirm_reseed != db:
            raise WatcherError(f"--reseed drops {db}; pass --confirm-reseed {db} to confirm.")
        if dry_run:
            raise WatcherError("--reseed can't be combined with --dry-run.")
        log_fn(f"Reseed: dropping {db}...")
        franchises.drop_db(db)

    setup = auto_init or reseed
    if not franchises.db_exists(db):
        if not setup:
            raise WatcherError(f"Database {db} for franchise {fid} does not exist. Run with --auto-init to create it.")
        if dry_run:
            log_fn(f"Dry run: would create and seed {db}.")
            return {"status": "no_op", "reason": "database_missing", "db": db}
        log_fn(f"Creating database {db}...")
        franchises.create_db(db)

    if franchises.db_state(dsn) == "empty":
        if not setup:
            raise WatcherError(f"Database {db} is empty. Run with --auto-init to seed it from the save.")
        if dry_run:
            log_fn(f"Dry run: would seed {db}.")
            return {"status": "no_op", "reason": "database_empty", "db": db}
        initial_week = save_week if save_week is not None else 1
        log_fn(f"Seeding {db} for season {save_season}, week {initial_week}...")
        from db.seed_clean_db import run_clean_seed
        run_clean_seed(dsn=dsn, season=save_season, week=initial_week,
                       user_teams=entry.get("user_teams") or [], log=log_fn)
        if fid.lstrip("-").isdigit():
            franchises.stamp_league_id(dsn, int(fid))
        return {"status": "ready", "db": db, "dsn": dsn, "seeded": True}

    stamped = franchises.stamped_league_id(dsn)
    if stamped is not None and fid.lstrip("-").isdigit() and stamped != int(fid):
        raise WatcherError(f"Database {db} belongs to franchise {stamped}, not {fid} ({save_name}). "
                           f"Check {franchises.registry_path()}.")
    return {"status": "ready", "db": db, "dsn": dsn, "seeded": False}


def ingest_snapshot(snapshot_path, save_season, save_week, dry_run=False, no_articles=False, no_engines=False,
                    save_name="", log_fn=log):
    """load_save -> engines -> articles against DATABASE_URL. Runs in the child process run_ingest starts."""
    from pipeline import load_save

    load_report = load_save.load(snapshot_path, season=save_season, commit=(not dry_run))
    log_fn(f"Load report: {load_report.get('league_state')}, players: {load_report.get('players')}, "
           f"completed games: {load_report.get('games_completed')}")

    if dry_run:
        log_fn("Dry run mode: database changes rolled back, skipping engines and articles.")
        return {"status": "success", "dry_run": True, "season": save_season, "week": save_week, "load_report": load_report}
    if no_engines:
        log_fn("--no-engines: loaded only, skipping engines and articles.")
        return {"status": "success", "dry_run": False, "season": save_season, "week": save_week, "load_report": load_report}

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

    # Step: next-week mod pack preparation for user teams
    if save_week is not None:
        dsn = os.environ.get("DATABASE_URL")
        next_week = save_week + 1
        prepare_next_week_mod_pack(dsn, save_season, next_week, log_fn=log_fn)

    send_notify(
        f"Ingested season {save_season} week {save_week or 'preseason'} from {save_name}. "
        f"{published or 'Articles skipped.'}",
        title="Mu: week done",
        tags=("white_check_mark",)
    )

    return {"status": "success", "dry_run": False, "season": save_season, "week": save_week, "load_report": load_report}


def run_ingest(snapshot_path, dsn, save_season, save_week, dry_run=False, no_articles=False, no_engines=False,
               save_name="", log_fn=log):
    """Runs ingest_snapshot in a child process with DATABASE_URL=dsn (most modules read it at import time);
    returns the child's result dict."""
    result_path = os.path.join(os.path.dirname(snapshot_path), "ingest_result.json")
    cmd = [sys.executable, "-m", "pipeline.watch_save", "--ingest", snapshot_path,
           "--season", str(save_season), "--save-name", save_name]
    if save_week is not None:
        cmd += ["--week", str(save_week)]
    if dry_run:
        cmd.append("--dry-run")
    if no_articles:
        cmd.append("--no-articles")
    if no_engines:
        cmd.append("--no-engines")
    p = subprocess.run(cmd, cwd=ROOT, env={**os.environ, "DATABASE_URL": dsn})
    if p.returncode != 0 or not os.path.exists(result_path):
        raise WatcherError(f"Ingest of {save_name} into {db_name_of(dsn)} failed (exit {p.returncode}); see the log above.")
    with open(result_path) as fh:
        return json.load(fh)


def process_save(save_path, season=None, dry_run=False, no_articles=False, no_engines=False, force=False,
                 auto_init=False, reseed=False, confirm_reseed=None, user_teams=None, franchise_name=None,
                 run_folder=None, dsn=None, reader_script=READER_SCRIPT, log_fn=log):
    """
    Core processing for a settled save file:
    1. Snapshots into captures/<run>/save.json
    2. Routes it to its franchise's database (route_franchise), creating and seeding it with --auto-init;
       an explicit `dsn` skips routing and loads into that database
    3. Compares save season & week against that database's league_state
    4. If new week (or force or just seeded): runs load_save -> engines -> articles there (run_ingest)
    """
    if run_folder is None:
        run_name = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        run_folder = os.path.join(CAPTURES, run_name)

    snapshot_path = snapshot_save(save_path, run_folder, reader_script=reader_script)
    with open(snapshot_path) as fh:
        snapshot = json.load(fh)

    save_name = os.path.basename(save_path)
    save_season = season or snapshot.get("season", {}).get("calendar_year")
    save_week = current_week(snapshot)
    fid = franchise_id(snapshot, save_path)

    if dsn:
        db, just_seeded = db_name_of(dsn), False
    else:
        route = route_franchise(fid, save_name, save_season, save_week, auto_init=auto_init, reseed=reseed,
                                confirm_reseed=confirm_reseed, user_teams=user_teams,
                                franchise_name=franchise_name, dry_run=dry_run, log_fn=log_fn)
        if route["status"] == "no_op":
            shutil.rmtree(run_folder, ignore_errors=True)
            return {"status": "no_op", "reason": route["reason"], "db": route["db"], "franchise": fid,
                    "season": save_season, "week": save_week}
        db, dsn, just_seeded = route["db"], route["dsn"], route["seeded"]

    stored_season, stored_week = get_league_state(dsn)
    if stored_season is None and not just_seeded:
        raise WatcherError(f"Database {db} has no league_state. Run with --auto-init to seed it from the save.")

    log_fn(f"{save_name} -> {db} (franchise {fid}) | Save: season {save_season}, week {save_week} | "
           f"League state: season {stored_season}, week {stored_week}")

    def no_op(reason):
        shutil.rmtree(run_folder, ignore_errors=True)
        return {"status": "no_op", "reason": reason, "db": db, "franchise": fid, "season": save_season, "week": save_week}

    if not force and not just_seeded:
        classification = classify_week(save_season, save_week, stored_season, stored_week)
        if classification == "same_week":
            log_fn(f"Save {save_name} is at season {save_season} week {save_week}, same as {db}'s league_state. No-op.")
            return no_op("same_week")

        if classification in ("older_week", "season_gap"):
            msg = (f"Watched save {save_name} is at season {save_season} week {save_week}, which is "
                   f"{classification.replace('_', ' ')} for {db} (league_state: {stored_season} week {stored_week}). "
                   "No-op; --force loads it anyway.")
            log_fn(f"[WARN] {msg}")
            send_notify(msg, title="Mu save watcher: save out of order", priority="default", tags=("warning",))
            return no_op(classification)

        if classification == "no_current_week":
            log_fn(f"Save {save_name} is not at a regular season or playoff week (season {save_season}). No-op.")
            return no_op("no_current_week")

    log_fn(f"Ingesting week: season {save_season} week {save_week} into {db} "
           f"(dry_run={dry_run}, no_articles={no_articles}, no_engines={no_engines})")
    res = run_ingest(snapshot_path, dsn, save_season, save_week, dry_run=dry_run, no_articles=no_articles,
                     no_engines=no_engines, save_name=save_name, log_fn=log_fn)
    return {**res, "db": db, "franchise": fid}


def watch_loop(watch_path, pattern="CAREER-*", debounce_seconds=DEFAULT_DEBOUNCE,
               poll_interval=DEFAULT_POLL_INTERVAL, lock_path=DEFAULT_LOCK_PATH, log_fn=log, **process_kw):
    """Continuous polling daemon loop; process_kw goes to process_save for every settled save. Debounce state is
    per file, and each save routes to its own franchise, so saves from several franchises can share the folder."""
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
                        res = process_save(save_file, log_fn=log_fn, **process_kw)
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


def ingest_main(args):
    """The child side of run_ingest: DATABASE_URL already names the franchise's database."""
    res = ingest_snapshot(args.ingest, args.season, args.week, dry_run=args.dry_run,
                          no_articles=args.no_articles, no_engines=args.no_engines, save_name=args.save_name)
    with open(os.path.join(os.path.dirname(args.ingest), "ingest_result.json"), "w") as fh:
        json.dump(res, fh, default=str)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", help="Save file or folder to watch (default: MADDEN_SAVES_DIR or .)")
    ap.add_argument("--saves-dir", help="Directory to watch for CAREER-* files")
    ap.add_argument("--pattern", default="CAREER-*", help="Save filename pattern (default: CAREER-*)")
    ap.add_argument("--debounce", type=int, default=DEFAULT_DEBOUNCE, help=f"Debounce wait in seconds (default: {DEFAULT_DEBOUNCE})")
    ap.add_argument("--poll-interval", type=int, default=DEFAULT_POLL_INTERVAL, help=f"Poll interval in seconds (default: {DEFAULT_POLL_INTERVAL})")
    ap.add_argument("--once", action="store_true", help="Process the newest save once and exit")
    ap.add_argument("--dry-run", action="store_true", help="Load without commit, no engines, articles or database setup")
    ap.add_argument("--no-articles", action="store_true", help="Run loader and engines, skip newsroom articles")
    ap.add_argument("--no-engines", action="store_true", help="Run the loader only (no engines, no articles)")
    ap.add_argument("--season", type=int, help="Override season year (default: save's calendar year)")
    ap.add_argument("--force", action="store_true", help="Run even if week is same as or older than league_state")
    ap.add_argument("--auto-init", action="store_true", help="Create and seed the database of a franchise seen for the first time")
    ap.add_argument("--franchise-name", help="Name for a new franchise (its database becomes madden_<name>)")
    ap.add_argument("--user-team", default="", help="Comma-separated user-controlled teams for a new franchise (e.g. IND or IND,CAR)")
    ap.add_argument("--reseed", action="store_true", help="Drop and recreate this franchise's database, then seed it (--once only)")
    ap.add_argument("--confirm-reseed", metavar="DB", help="Required with --reseed: the database name being dropped")
    ap.add_argument("--dsn", help="Load into this database instead of routing by franchise")
    ap.add_argument("--lock-file", default=DEFAULT_LOCK_PATH, help=f"Lock file path (default: {DEFAULT_LOCK_PATH})")
    # Internal: the child process run_ingest starts.
    ap.add_argument("--ingest", help=argparse.SUPPRESS)
    ap.add_argument("--week", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--save-name", default="", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.ingest:
        sys.exit(ingest_main(args))

    watch_path = args.path or args.saves_dir or env("MADDEN_SAVES_DIR", ".")
    watch_path = os.path.abspath(watch_path)
    user_teams = [t.strip().upper() for t in args.user_team.split(",") if t.strip()]

    if not os.path.exists(watch_path):
        log(f"Error: watch path '{watch_path}' does not exist.")
        sys.exit(1)
    if args.reseed and not args.once:
        log("Error: --reseed only works with --once.")
        sys.exit(1)

    process_kw = dict(season=args.season, dry_run=args.dry_run, no_articles=args.no_articles,
                      no_engines=args.no_engines, force=args.force,
                      auto_init=args.auto_init, user_teams=user_teams, franchise_name=args.franchise_name,
                      dsn=args.dsn)

    if args.once:
        save_files = find_save_files(watch_path, pattern=args.pattern)
        if not save_files:
            log(f"No matching save files found in '{watch_path}' (pattern: {args.pattern}).")
            sys.exit(0)

        target_save = save_files[-1]
        log(f"--once mode: targeting latest save {target_save}")
        if not wait_for_settle(target_save, debounce_seconds=args.debounce, timeout=30):
            log(f"Save file {target_save} is actively changing (did not settle within timeout).")
            sys.exit(1)

        try:
            with run_lock(args.lock_file):
                res = process_save(target_save, reseed=args.reseed, confirm_reseed=args.confirm_reseed,
                                   log_fn=log, **process_kw)
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
        watch_loop(watch_path, pattern=args.pattern, debounce_seconds=args.debounce,
                   poll_interval=args.poll_interval, lock_path=args.lock_file, log_fn=log, **process_kw)


if __name__ == "__main__":
    main()
