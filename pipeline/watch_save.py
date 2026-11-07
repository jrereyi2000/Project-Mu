"""
pipeline/watch_save.py: Debounced save watcher daemon for Madden NFL PC franchise saves
(docs/done/PC_MIGRATION_PLAN.md §5.2, docs/done/PRE_PC_WORK.md §4).

Watches a directory for CAREER-* save files, debounces write bursts from Madden,
snapshots the save via pc_save_reader.mjs, routes it to its franchise's database, and runs
the weekly ingestion pipeline there:
  load_save (--commit) -> engines -> league office -> newsroom articles -> next week's mod pack

Madden autosaves after Advance Week, so no manual save is needed. After the load and the weekly engines, the league
office (pipeline.league_office.run, stage auto) runs in the same child process on the save just ingested, so the
articles can report its moves. It replaces the save (a verified write); that write keeps the season and week, so it is
never ingested again as a new week (classify_week: same_week; the daemon also remembers the league office's output
sha256 and skips its own write without reading it). Offseason stops have no current week: with the league office on,
an offseason save is ingested (load, league office; no engines, no articles) when league_office_runs has no WRITTEN or
PLANNED run for its stage yet. A league office failure is logged and notified and the articles still run; a run that
needs recovery names `python3 -m pipeline.league_office --recover <run_id>`. While Madden27.exe runs the league office
refuses to replace the save unless LEAGUE_OFFICE_FRANCHISE_CLOSED=1 is set (JR sets it when he works at Madden's main
menu with the franchise closed); without it the refusal is logged, not fatal, and the daemon retries the league office
on the same snapshot once Madden27.exe has closed (dropped if the save changed in the meantime). The league office is
not run on the load that first seeds a franchise (run `python3 -m pipeline.league_office setup` first) or on a forced
re-load of a week already loaded.

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
  --dry-run                Load without commit, skip engines, league office and articles; never creates or seeds a
                           database
  --no-articles            Run loader and engines, skip newsroom articles
  --no-league-office       Don't run the league office after the weekly engines
  --league-office-dry-run  Run the league office with dry_run=True (plan only: no save write, nothing acknowledged)
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
  --no-freeze              Don't freeze the database + save after the ingest (pipeline/weekly_freeze.py)

After every committed ingest (a week, an offseason stage or the seeding load) the database and the save are frozen
together into MU_FREEZE_DIR/<db>/<label>/ (pipeline/weekly_freeze.py); a league office retry re-freezes the same
label once it has replaced the save, so the pair stays in sync. A freeze failure is logged and notified, never fatal.
"""
import argparse
import datetime
import fcntl
import glob
import hashlib
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
from pipeline import weekly_freeze
from pipeline.weekly_capture import run_weekly_engines, step_articles, step_preseason_engines, step_rookie_headshots, WeekStopped

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAPTURES = os.path.join(ROOT, "captures")
READER_SCRIPT = os.path.join(ROOT, "pipeline", "save_reader", "pc_save_reader.mjs")
DEFAULT_LOCK_PATH = os.path.join(CAPTURES, ".weekly_run.lock")
DEFAULT_DEBOUNCE = 10
DEFAULT_POLL_INTERVAL = 2
# Set by JR when Madden27.exe sits at its main menu with the franchise closed (pipeline.league_office reads it too).
FRANCHISE_CLOSED_ENV = "LEAGUE_OFFICE_FRANCHISE_CLOSED"
LO_RESULT = "league_office_result.json"


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

    def mark_processed(self, path, mtime=None):
        """Records that the file's mtime (the current one, or `mtime`, e.g. the one seen before processing started)
        has been processed."""
        if mtime is None:
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                return
        if path not in self.states:
            self.states[path] = {}
        self.states[path]["last_processed_mtime"] = mtime

    def note_own_write(self, path, sha256):
        """Remembers the sha256 of a save the league office wrote, so the watcher doesn't ingest its own write."""
        if sha256:
            self.states.setdefault(path, {})["own_write_sha256"] = sha256

    def is_own_write(self, path):
        """True when the file's content is exactly the league office's last write to it (then forgets the note
        only once a different content is seen)."""
        sha = (self.states.get(path) or {}).get("own_write_sha256")
        if not sha:
            return False
        try:
            current = file_sha256(path)
        except OSError:
            return False
        if current == sha:
            return True
        self.states[path].pop("own_write_sha256", None)
        return False


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


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
    # Only reports the queue. The queue is run by the nightly runner, never inline here: the ingest
    # happens while Madden is usually still open, when the runner waits indefinitely, and no job
    # handlers are registered in this process, so running it here would block the week or fail the jobs.
    try:
        from pipeline.gpu_queue.queue import GPUQueue

        pending = GPUQueue().list_jobs(status="pending")
        log_fn(f"GPU queue hook: checked queue, {len(pending)} pending jobs found "
               f"(left for the nightly runner).")
    except Exception as e:
        log_fn(f"Warning: GPU queue hook check encountered an error: {e}")


def prepare_next_week_mod_pack(dsn, season, next_week, log_fn=log):
    """Writes the mod pack for each user team's next game (`next_week`: the save's current week, Madden's next
    unplayed week, games.week numbering) into the Mod Manager's config with mod_packs.write_pack, selecting the first
    one. Skips writing (with a log line) while MMCModManager.exe is open (it saves its own copy of the packs) or when
    its config or the `Mu Base` pack is missing. Returns the names of the packs written."""
    written = []
    if not next_week or not season:
        return written
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
            return written

        planned = []      # (team, game, choice)
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
                    planned.append((team, g, c))
        if not planned:
            return written

        # mod_packs' config / manager checks raise SystemExit; they mean "skip writing", never a failed week.
        try:
            path = mod_packs.config_path()
        except SystemExit as e:
            log_fn(f"Next-week mod pack: not written, no Mod Manager config ({e}).")
            return written
        if not os.path.exists(path):
            log_fn(f"Next-week mod pack: not written, Mod Manager config {path} is missing.")
            return written
        try:
            running = mod_packs.manager_running()
        except SystemExit as e:
            log_fn(f"Next-week mod pack: not written, can't tell whether MMCModManager.exe is open ({e}).")
            return written
        if running:
            log_fn("Next-week mod pack: not written, MMCModManager.exe is open (it saves its own copy of the packs); "
                   "close it and run `python3 -m pipeline.mod_packs write --team <TEAM>`.")
            return written
        try:
            base = mod_packs.base_pack(path)
        except SystemExit as e:
            log_fn(f"Next-week mod pack: not written, {e}.")
            return written
        for team, g, c in planned:
            if c.pack_name in written:
                continue
            mod_packs.write_pack(path, c.pack_name, mod_packs.build_pack(base, c), select=not written)
            log_fn(f"Next-week mod pack: wrote '{c.pack_name}' for {team} ({g.away} @ {g.home}) to {path}"
                   + ("" if written else " and selected it"))
            written.append(c.pack_name)
    except Exception as e:
        log_fn(f"Warning: next-week mod pack preparation skipped: {e}")
    return written


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


# ------------------------------------------------------------------------------ personality switchboard ---
# The personality feature switchboard (personality/features.py, PERSONALITY_ACTIVATION_PLAN.md §2.4) decides the
# personality steps below; it is read once per ingest (personality_modes) and handed to each step. Each step's
# environment variable stays as a kill switch: "off" turns the step off whatever the switchboard says, and "on" is
# an operator override that runs it as active (the variables' behaviour before the switchboard existed).
PRODUCERS_ENV = "MU_RELATIONSHIP_PRODUCERS"
TRADE_REQUESTS_ENV = "MU_TRADE_REQUESTS"


def env_switch(name):
    """'on', 'off' or None (unset / anything else) for a personality step's environment variable."""
    v = os.environ.get(name, "").strip().lower()
    return v if v in ("on", "off") else None


def personality_modes(dsn=None, connect=None, log_fn=log):
    """The switchboard snapshot for this database's league, read once per ingest; None when it can't be read
    (no database, no league yet). Never raises: every personality step then reads 'off'."""
    if not (dsn or os.environ.get("DATABASE_URL")) and connect is None:
        return None
    conn = None
    try:
        from personality import features as pf
        if connect is None:
            import psycopg
            connect = psycopg.connect
        conn = connect(dsn or os.environ.get("DATABASE_URL"))
        with conn.cursor() as cur:
            cur.execute("SELECT madden_league_id FROM league_state WHERE id = 1")
            row = cur.fetchone()
            snap = None if not row or row[0] is None else pf.snapshot(cur, int(row[0]))
        conn.rollback()
        return snap
    except Exception as e:  # noqa: BLE001 - personality steps then stay off
        log_fn(f"Personality switchboard not read ({e}); personality steps stay off.")
        return None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


def feature_gate(feature, env_name, modes):
    """(mode, why) for a watcher step: the environment kill switch wins, else the switchboard snapshot."""
    switch = env_switch(env_name)
    if switch == "off":
        return "off", f"{env_name}=off (kill switch)"
    if switch == "on":
        return "active", f"{env_name}=on (operator override)"
    if modes is None:
        return "off", "switchboard not read"
    from personality import features as pf
    mode = pf.snapshot_mode(modes, feature)
    return mode, f"switchboard {feature} is {mode}"


def relationship_producers_on(modes=None):
    """True when the producers step runs: MU_RELATIONSHIP_PRODUCERS=on, or the switchboard's producers feature in
    shadow (observe) or active, unless MU_RELATIONSHIP_PRODUCERS=off."""
    return feature_gate("producers", PRODUCERS_ENV, modes)[0] != "off"


def step_relationship_producers(snapshot_path, save_season, dsn=None, log_fn=log, connect=None, modes=None):
    """Relationship event producers for this save (RELATIONSHIP_EVENT_PRODUCERS_PLAN.md §2.4), right after the
    load, on weekly ingests and offseason stops alike. Its own connection and transaction; runs when the switchboard
    `modes` has producers in shadow (observe: events recorded, consumers neutral) or active (feature_gate: the
    MU_RELATIONSHIP_PRODUCERS kill switch wins), skipped (with a logged reason) when the producer or relationships
    tables aren't migrated. Never fails the ingest: an error is logged, notified and recorded as a FAILED run."""
    mode, why = feature_gate("producers", PRODUCERS_ENV, modes)
    if mode == "off":
        log_fn(f"Relationship producers off ({why}).")
        return {"status": "off"}
    season = tick = league = None
    conn = None
    try:
        from engines.relationship_producers import calendar as rcal, run as runner, sources
        from pipeline import league_office as lo
        with open(snapshot_path) as fh:
            snapshot = json.load(fh)
        stage = lo.detect_stage(snapshot)
        season = lo.league_season(snapshot, stage)
        tick = rcal.tick_for(snapshot, lo.league_week(snapshot, stage) if stage in lo.IN_SEASON else None)
        if connect is None:
            import psycopg
            connect = psycopg.connect
        conn = connect(dsn or os.environ.get("DATABASE_URL"))
        with conn.cursor() as cur:
            ready = sources.tables_ready(cur)
            missing = [k for k in ("producers", "relationships") if not ready[k]]
            if missing:
                conn.rollback()
                log_fn(f"Relationship producers skipped: {', '.join(missing)} tables not migrated.")
                return {"status": "skipped", "reason": "not_migrated", "missing": missing}
            league = sources.league_id(cur)
            roster = sources.roster_from_snapshot(cur, snapshot)
            result = runner.run_tick(cur, season, tick, source="SAVE", league_id=league, roster=roster,
                                     roster_season=save_season)
        conn.commit()
        counts = result.get("counts") or {}
        log_fn(f"Relationship producers {season}:{tick}: {result['status']} ({counts.get('appended', 0)} appended, "
               f"{counts.get('held', 0)} held, {counts.get('rejected', 0)} rejected)")
        return {"status": result["status"], "season": season, "tick": tick, "counts": counts}
    except Exception as e:  # noqa: BLE001 - never fatal to the ingest
        msg = f"Relationship producers failed for season {season} tick {tick}: {e}"
        log_fn(msg)
        traceback.print_exc()
        if conn is not None:
            try:
                conn.rollback()
                if league is not None and season is not None and tick is not None:
                    from engines.relationship_producers import run as runner
                    with conn.cursor() as cur:
                        runner.record_failure(cur, league, season, tick, "SAVE", e)
                    conn.commit()
            except Exception as rec_err:  # noqa: BLE001
                log_fn(f"Could not record the FAILED producer run: {rec_err}")
        try:
            send_notify(msg, title="Mu: relationship producers failed", priority="default", tags=("warning",))
        except Exception:  # noqa: BLE001
            pass
        return {"status": "failed", "error": str(e)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# ------------------------------------------------------------------------------------- personality evolution ---
EVOLUTION_ENV = "MU_PERSONALITY_EVOLUTION"
EVOLUTION_TICK = 24   # league_office.STAGE_WEEKS["OFFSEASON_START"]


def personality_evolution_on(modes=None):
    """True when the evolution step runs: MU_PERSONALITY_EVOLUTION=on, or the switchboard's evolution feature in
    shadow (dry run) or active, unless MU_PERSONALITY_EVOLUTION=off (the kill switch)."""
    return feature_gate("evolution", EVOLUTION_ENV, modes)[0] != "off"


def step_personality_evolution(snapshot_path, save_season, dsn=None, log_fn=log, connect=None, modes=None):
    """Personality evolution for the season just ended (PERSONALITY_EVOLUTION_PLAN.md §2.5), on the OFFSEASON_START
    ingest (tick 24) only, after the relationship producers and before the league office. Its own connection and
    transaction; runs by the switchboard `modes` (feature_gate: the MU_PERSONALITY_EVOLUTION kill switch wins):
    shadow runs the season as a dry run (report only, no profile changes), active applies it. Skipped (with a
    logged reason) when the evolution tables aren't migrated or the league has no lifecycle universe. Writes the
    season report under verify_output/ and, when applied, notifies "Mu: N personality changes in <season>". Never
    fails the ingest: an error is logged, notified and recorded as a FAILED run."""
    mode, why = feature_gate("evolution", EVOLUTION_ENV, modes)
    if mode == "off":
        log_fn(f"Personality evolution off ({why}).")
        return {"status": "off"}
    season = universe = None
    conn = None
    try:
        from pipeline import league_office as lo
        from personality import evolution_run as er
        with open(snapshot_path) as fh:
            snapshot = json.load(fh)
        stage = lo.detect_stage(snapshot)
        if stage in lo.IN_SEASON or lo.STAGE_WEEKS.get(stage) != EVOLUTION_TICK:
            return {"status": "skipped", "reason": "not_offseason_start", "stage": stage}
        season = lo.league_season(snapshot, stage)
        if connect is None:
            import psycopg
            connect = psycopg.connect
        conn = connect(dsn or os.environ.get("DATABASE_URL"))
        with conn.cursor() as cur:
            if not er.tables_ready(cur):
                conn.rollback()
                log_fn("Personality evolution skipped: persona_evolution tables not migrated.")
                return {"status": "skipped", "reason": "not_migrated"}
            try:
                universe = er.resolve_universe(cur)
            except er.EvolutionRunError as e:
                conn.rollback()
                log_fn(f"Personality evolution skipped: {e}")
                return {"status": "skipped", "reason": "no_universe"}
            report = er.run_season(cur, universe, season, dry_run=mode == "shadow")
        if mode == "shadow":
            conn.rollback()
        else:
            conn.commit()
        path = er.write_report(report, er.report_path(universe, season))
        changes = report["counts"].get("applied", 0) if report["status"] == "applied" else 0
        log_fn(f"Personality evolution {season} ({mode}): {report['status']} ({changes} changes); report {path}")
        if report["status"] == "applied":
            send_notify(f"Mu: {changes} personality changes in {season}. Report: {path}",
                        title="Mu: personality evolution", tags=("busts_in_silhouette",))
        return {"status": report["status"], "season": season, "universe_id": universe, "changes": changes,
                "report": path}
    except Exception as e:  # noqa: BLE001 - never fatal to the ingest
        msg = f"Personality evolution failed for season {season}: {e}"
        log_fn(msg)
        traceback.print_exc()
        if conn is not None:
            try:
                conn.rollback()
                if universe is not None and season is not None:
                    from personality import evolution_run as er
                    with conn.cursor() as cur:
                        er.record_failure(cur, universe, season, e)
                    conn.commit()
            except Exception as rec_err:  # noqa: BLE001
                log_fn(f"Could not record the FAILED evolution run: {rec_err}")
        try:
            send_notify(msg, title="Mu: personality evolution failed", priority="default", tags=("warning",))
        except Exception:  # noqa: BLE001
            pass
        return {"status": "failed", "error": str(e)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# ----------------------------------------------------------------------------------------- focus refresh ---
FOCUS_ENV = "MU_FOCUS_REFRESH"


def focus_refresh_on(modes=None):
    """True when the focus refresh runs: MU_FOCUS_REFRESH=on, or the switchboard's focus_refresh feature in shadow
    (dry run) or active, unless MU_FOCUS_REFRESH=off (the kill switch)."""
    return feature_gate("focus_refresh", FOCUS_ENV, modes)[0] != "off"


def step_focus_refresh(snapshot_path, save_season, dsn=None, log_fn=log, connect=None, modes=None):
    """Refresh the current focus of real coaches and owners for the season just ended
    (INFERRED_SLIDERS_AND_FOCUS_PLAN.md §2.7), on the OFFSEASON_START ingest (tick 24) only, right after personality
    evolution and before the league office and trade-request steps. Its own connection and transaction; runs by the
    switchboard `modes` (feature_gate: the MU_FOCUS_REFRESH kill switch wins): shadow runs the season as a dry run
    (report only), active applies it. Skipped (with a logged reason) when the persona_focus tables aren't migrated
    or the league has no lifecycle universe. Writes the season report under verify_output/ and, when applied,
    notifies "Mu: N focus refreshes in <season>". Never fails the ingest: an error is logged, notified and recorded
    as a FAILED run."""
    mode, why = feature_gate("focus_refresh", FOCUS_ENV, modes)
    if mode == "off":
        log_fn(f"Focus refresh off ({why}).")
        return {"status": "off"}
    season = universe = None
    conn = None
    try:
        from pipeline import league_office as lo
        from personality import focus_run as fr
        with open(snapshot_path) as fh:
            snapshot = json.load(fh)
        stage = lo.detect_stage(snapshot)
        if stage in lo.IN_SEASON or lo.STAGE_WEEKS.get(stage) != EVOLUTION_TICK:
            return {"status": "skipped", "reason": "not_offseason_start", "stage": stage}
        season = lo.league_season(snapshot, stage)
        if connect is None:
            import psycopg
            connect = psycopg.connect
        conn = connect(dsn or os.environ.get("DATABASE_URL"))
        with conn.cursor() as cur:
            if not fr.tables_ready(cur):
                conn.rollback()
                log_fn("Focus refresh skipped: persona_focus tables not migrated.")
                return {"status": "skipped", "reason": "not_migrated"}
            try:
                universe = fr.resolve_universe(cur)
            except fr.FocusRunError as e:
                conn.rollback()
                log_fn(f"Focus refresh skipped: {e}")
                return {"status": "skipped", "reason": "no_universe"}
            report = fr.run_season(cur, universe, season, dry_run=mode == "shadow")
        if mode == "shadow":
            conn.rollback()
        else:
            conn.commit()
        path = fr.write_report(report, fr.report_path(universe, season))
        changes = report["counts"].get("replaced", 0) if report["status"] == "applied" else 0
        log_fn(f"Focus refresh {season} ({mode}): {report['status']} ({changes} refreshed); report {path}")
        if report["status"] == "applied":
            send_notify(f"Mu: {changes} focus refreshes in {season}. Report: {path}",
                        title="Mu: focus refresh", tags=("busts_in_silhouette",))
        return {"status": report["status"], "season": season, "universe_id": universe, "changes": changes,
                "report": path}
    except Exception as e:  # noqa: BLE001 - never fatal to the ingest
        msg = f"Focus refresh failed for season {season}: {e}"
        log_fn(msg)
        traceback.print_exc()
        if conn is not None:
            try:
                conn.rollback()
                if universe is not None and season is not None:
                    from personality import focus_run as fr
                    with conn.cursor() as cur:
                        fr.record_failure(cur, universe, season, e)
                    conn.commit()
            except Exception as rec_err:  # noqa: BLE001
                log_fn(f"Could not record the FAILED focus run: {rec_err}")
        try:
            send_notify(msg, title="Mu: focus refresh failed", priority="default", tags=("warning",))
        except Exception:  # noqa: BLE001
            pass
        return {"status": "failed", "error": str(e)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# ------------------------------------------------------------------------------------------ trade requests ---
TRADE_REQUEST_REPORTS = os.path.join("verify_output", "personality_trade_requests")


def trade_request_clock(universe_id, season, tick):
    """The trade-requests plan's RequestClock for an ingest: the 32-tick relationships calendar and the GM window
    (gm_trade_engine.window_for; ticks 24-32 are the offseason window)."""
    from engines import gm_trade_engine as gm
    from engines import player_trade_requests as tr
    window = gm.window_for(tick) or "CLOSED"
    return tr.RequestClock(universe_id=universe_id, season=season, checkpoint_id=f"tick:{tick}",
                           ordinal=tr.clock_ordinal(season, tick), window_id=f"{season}:{window}",
                           window_kind=window, trades_open=window != "CLOSED",
                           source_event_id=f"ingest:{universe_id}:{season}:{tick}")


def trade_request_policy(modes, mode):
    """The request policy from the switchboard's trade_requests config ('policy'), or the offline illustration
    policy in shadow; None when active without a reviewed policy (the store refuses the offline one)."""
    from engines import player_trade_requests as tr
    from personality import features as pf
    row = ((modes or {}).get("features") or {}).get("trade_requests") or {}
    cfg = (row.get("config") or {}).get("policy")
    if cfg is not None:
        return tr.policy_from_dict(cfg)
    return tr.offline_illustration_policy() if mode == "shadow" else None


def trade_request_contexts(cur, universe, season, tick, clock, modes, user_team_ids):
    """[(RequestContext, current team_id)] for every player persona on a team: the relationships plan's incidents
    (only while its policy is in the switchboard's relationships config; otherwise none, so nobody qualifies) and
    the core's trade_requests view. Players without a stored persona are skipped."""
    from engines import player_trade_requests as tr
    from personality import core, lifecycle as plc, store
    from personality import reactions as rx, relationships as rel, relationship_store as rs
    uid = universe["universe_id"]
    rel_row = ((modes or {}).get("features") or {}).get("relationships") or {}
    rel_policy = (rel_row.get("config") or {}).get("policy")
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (rs.EVENTS_TABLE,))
    have_events = bool(cur.fetchone()[0])
    cur.execute("SELECT p.id, p.madden_pgid, p.team_id FROM players p WHERE p.team_id IS NOT NULL "
                "AND p.madden_pgid IS NOT NULL ORDER BY p.id")
    out = []
    as_of = {"season": season, "tick": tick}
    for player_id, pgid, team_id in cur.fetchall():
        ident = store.resolve_alias(cur, uid, "player", plc.pgid_namespace(universe), str(pgid))
        if ident is None:
            continue
        profile = store.read(cur, ident)
        if profile is None:
            continue
        view = core.feature_view(profile, "trade_requests")
        subject = {"kind": "person", "universe_id": uid, "role": "player", "persona_id": profile["persona_id"]}
        incidents, rel_rev, rx_rev = (), "none", "none"
        if rel_policy is not None and have_events:
            events = rs.read_events(cur, subject=subject, as_of=as_of)
            seasons = {ev["occurred_at"]["season"] for ev in events} | {season}
            cal = {y: 32 for y in range(min(seasons), max(seasons) + 1)}
            state = rel.project_relationships(events, subject=subject, as_of=as_of, known_through=None,
                                              policy=rel_policy, calendar=cal)
            reactions = rx.project_reactions(events, state, feature_view=core.feature_view(profile, "reactions"),
                                             as_of=as_of, known_through=None, policy=rel_policy, calendar=cal)
            incidents = tuple(tr.incidents_from_shared(events, state, reactions, team_id=int(team_id),
                                                       team_key=f"team:{team_id}"))
            rel_rev, rx_rev = state["input_hash"], reactions["input_hash"]
        control = tr.control_context(int(team_id), user_team_ids=user_team_ids, suggestions_on=True)
        ctx = tr.RequestContext(schema_version=tr.SCHEMA_VERSION, persona_id=profile["persona_id"],
                                team_id=int(team_id), profile_view=view, relationship_revision=rel_rev,
                                reaction_revision=rx_rev, assessment_id=f"{season}:{tick}",
                                as_of_ordinal=clock.ordinal, complete_through_ordinal=clock.ordinal,
                                incidents=incidents, control=control)
        out.append((ctx, int(team_id)))
    return out


def registry_user_team_ids(cur):
    """JR's teams from the franchise registry's user_teams (ids), or None when the registry can't say."""
    try:
        _, entry = franchises.active_entry()
        abbrs = [a.upper() for a in ((entry or {}).get("user_teams") or [])]
    except Exception:  # noqa: BLE001 - unknown authority
        return None
    if not abbrs:
        return set()
    cur.execute("SELECT id FROM teams WHERE upper(abbreviation) = ANY(%s)", (abbrs,))
    return {r[0] for r in cur.fetchall()}


def review_trade_requests(cur, universe, season, tick, mode, modes, *, contexts=None, user_team_ids=None):
    """One checkpoint review for every player context (pure orchestration over the trade-requests plan's engine and
    store). shadow: evaluate_request in SHADOW mode, nothing persisted, decisions returned for the report; active:
    record_decision through the store and, for an ACTIVE stored episode, REVIEW_REPAIR (still on the requesting
    team) or a deferred CLOSE_EMPLOYMENT (he left it; the departure is not verified here). One player's error is
    recorded and the others carry on."""
    from engines import player_trade_request_store as trs
    from engines import player_trade_requests as tr
    clock = trade_request_clock(universe["universe_id"], season, tick)
    policy = trade_request_policy(modes, mode)
    if policy is None:
        return {"status": "skipped", "reason": "no_policy", "decisions": []}
    if contexts is None:
        contexts = trade_request_contexts(cur, universe, season, tick, clock, modes, user_team_ids)
    decisions, counts = [], {}
    for ctx, team_id in contexts:
        try:
            if mode == "shadow":
                d = tr.evaluate_request(ctx, clock, None, None, policy, mode="SHADOW")
                row = {"persona_id": ctx.persona_id, "team_id": team_id, "outcome": d.outcome,
                       "decision": tr.to_dict(d)}
            else:
                episode = trs.read_active(cur, clock.universe_id, ctx.persona_id)
                if episode is not None:
                    left = team_id != episode.team_id
                    action = tr.RequestAction(
                        event_id=f"{'close' if left else 'repair'}:{clock.season}:{clock.checkpoint_id}",
                        source_event_id=clock.source_event_id, expected_revision=episode.revision,
                        kind="CLOSE_EMPLOYMENT" if left else "REVIEW_REPAIR", actor="SYSTEM",
                        payload={"reason": "UNKNOWN", "from_team_id": episode.team_id} if left else {})
                    if left:
                        ctx = tr.RequestContext(**dict(tr.to_dict(ctx) | {"team_id": episode.team_id,
                                                "control": tr.control_context(episode.team_id,
                                                user_team_ids=user_team_ids, suggestions_on=True),
                                                "incidents": ctx.incidents}))
                    r = trs.apply_action(cur, episode.request_id, action, ctx, clock, policy)
                    row = {"persona_id": ctx.persona_id, "team_id": team_id, "outcome": action.kind,
                           "receipt": r}
                else:
                    r = trs.record_decision(cur, ctx, clock, policy, mode="ENABLED")
                    row = {"persona_id": ctx.persona_id, "team_id": team_id,
                           "outcome": r["decision"]["outcome"], "receipt": r}
        except Exception as e:  # noqa: BLE001 - one player never stops the review
            row = {"persona_id": ctx.persona_id, "team_id": team_id, "outcome": "ERROR",
                   "error": f"{type(e).__name__}: {e}"}
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
        decisions.append(row)
    return {"status": mode, "counts": counts, "decisions": decisions, "clock": tr.to_dict(clock)}


def step_trade_requests(snapshot_path, save_season, dsn=None, log_fn=log, connect=None, modes=None):
    """Trade-request reviews (PERSONALITY_ACTIVATION_PLAN.md §2.4) on every ingest, after the producers and
    evolution steps, when the switchboard's trade_requests feature is not off (MU_TRADE_REQUESTS=off is the kill
    switch). Its own connection and transaction. shadow writes the decisions to a report under
    verify_output/personality_trade_requests/ and persists nothing; active persists through the trade-requests
    store. Skipped (with a logged reason) when the tables aren't migrated or the league has no lifecycle universe.
    Never fails the ingest."""
    mode, why = feature_gate("trade_requests", TRADE_REQUESTS_ENV, modes)
    if mode == "off":
        log_fn(f"Trade requests off ({why}).")
        return {"status": "off"}
    conn = None
    try:
        from engines import player_trade_request_store as trs
        from engines.relationship_producers import calendar as rcal, sources
        from pipeline import league_office as lo
        with open(snapshot_path) as fh:
            snapshot = json.load(fh)
        stage = lo.detect_stage(snapshot)
        season = lo.league_season(snapshot, stage)
        tick = rcal.tick_for(snapshot, lo.league_week(snapshot, stage) if stage in lo.IN_SEASON else None)
        if connect is None:
            import psycopg
            connect = psycopg.connect
        conn = connect(dsn or os.environ.get("DATABASE_URL"))
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass(%s) IS NOT NULL, to_regclass('persona_lifecycle_universes') IS NOT NULL",
                        (trs.REQUESTS_TABLE,))
            have_requests, have_lifecycle = cur.fetchone()
            if not (have_requests and have_lifecycle):
                conn.rollback()
                log_fn("Trade requests skipped: trade-request or lifecycle tables not migrated.")
                return {"status": "skipped", "reason": "not_migrated"}
            from personality import lifecycle_store
            universe = lifecycle_store.universe_for_league(cur, sources.league_id(cur))
            if universe is None:
                conn.rollback()
                log_fn("Trade requests skipped: no lifecycle universe for this league.")
                return {"status": "skipped", "reason": "no_universe"}
            result = review_trade_requests(cur, universe, season, tick, mode, modes,
                                           user_team_ids=registry_user_team_ids(cur))
        if mode == "active":
            conn.commit()
        else:
            conn.rollback()
        out = {"status": result["status"], "season": season, "tick": tick, "counts": result.get("counts", {})}
        if mode == "shadow" and result.get("decisions") is not None:
            os.makedirs(TRADE_REQUEST_REPORTS, exist_ok=True)
            path = os.path.join(TRADE_REQUEST_REPORTS,
                                f"shadow_{universe['universe_id'].replace(':', '_')}_{season}_{tick:02d}.json")
            with open(path, "w") as fh:
                json.dump(result, fh, indent=1, sort_keys=True, default=str)
            out["report"] = path
        log_fn(f"Trade requests {season}:{tick} ({mode}): {out['counts']}")
        return out
    except Exception as e:  # noqa: BLE001 - never fatal to the ingest
        msg = f"Trade requests failed: {e}"
        log_fn(msg)
        traceback.print_exc()
        if conn is not None:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
        try:
            send_notify(msg, title="Mu: trade requests failed", priority="default", tags=("warning",))
        except Exception:  # noqa: BLE001
            pass
        return {"status": "failed", "error": str(e)}
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


# ----------------------------------------------------------------------------------------- league office ---
def franchise_closed_env():
    """True when JR has set LEAGUE_OFFICE_FRANCHISE_CLOSED (Madden at its main menu, franchise closed)."""
    return os.environ.get(FRANCHISE_CLOSED_ENV, "").strip().lower() in ("1", "true", "yes")


def league_office_stage(snapshot, log_fn=log):
    """{"stage", "wave", "season", "week"} as the league office will see the snapshot (league_state numbering), or
    None when it isn't one of the league office's stages."""
    try:
        from pipeline import league_office as lo
        stage, wave = lo.stage_and_wave(snapshot)
        return {"stage": stage, "wave": wave, "season": lo.league_season(snapshot, stage),
                "week": lo.league_week(snapshot, stage)}
    except Exception as e:      # noqa: BLE001 - StageError or a snapshot without a season block
        log_fn(f"League office stage not recognised for this save: {e}")
        return None


def league_office_stage_done(dsn, info):
    """True when league_office_runs already has a WRITTEN (or PLANNED: waiting for --recover) run for this stage,
    week and free-agency wave. A database without the table, or one that can't be read, counts as done (no-op)."""
    import psycopg
    try:
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.league_office_runs')")
            reg = cur.fetchone()
            if not reg or not reg[0]:
                return True
            cur.execute("""SELECT 1 FROM league_office_runs WHERE season = %s AND stage = %s
                           AND week IS NOT DISTINCT FROM %s AND status IN ('WRITTEN', 'PLANNED')
                           AND COALESCE(summary->>'wave', '') = %s LIMIT 1""",
                        (info["season"], info["stage"], info["week"],
                         "" if info.get("wave") is None else str(info["wave"])))
            return cur.fetchone() is not None
    except Exception:
        return True


def league_office_command(snapshot_path, save_path, dsn):
    """The manual command that runs the league office on this snapshot later."""
    return (f"{FRANCHISE_CLOSED_ENV}=1 python3 -m pipeline.league_office --snapshot {snapshot_path} "
            f"--save '{save_path}' --dsn '{dsn}'")


def run_league_office(snapshot_path, save_path, dsn, dry_run=False, expected_sha256=None, log_fn=log):
    """Runs pipeline.league_office.run(cur, snapshot, save_path, stage="auto") in this process on the franchise's
    database. Never raises: returns {"status": "written" | "dry_run" | "failed" | "franchise_open" |
    "needs_recovery" | "skipped", "run_id", "error", "output_sha256", "stage", "replaced"}."""
    from pipeline import league_office as lo

    res = {"status": None, "run_id": None, "error": None, "output_sha256": None, "stage": None, "replaced": False,
           "dry_run": bool(dry_run)}
    if not save_path or not os.path.exists(save_path):
        return dict(res, status="skipped", error=f"save {save_path!r} not found")
    if expected_sha256 and file_sha256(save_path) != expected_sha256:
        return dict(res, status="skipped",
                    error="the save changed since it was read (a newer save will be ingested on its own)")
    with open(snapshot_path) as fh:
        snapshot = json.load(fh)
    reports = []

    def out(text):
        reports.append(text)

    def last_report():
        for text in reversed(reports):
            try:
                return json.loads(text)
            except (TypeError, ValueError):
                continue
        return {}

    log_fn(f"League office: {os.path.basename(save_path)} (stage auto, dry_run={bool(dry_run)})")
    try:
        import psycopg
        with psycopg.connect(dsn) as conn:
            run_id = lo.run(conn.cursor(), snapshot, save_path, stage="auto", dry_run=dry_run,
                            confirm_franchise_closed=franchise_closed_env(), out=out)
    except lo.FranchiseOpenError as e:
        return dict(res, status="franchise_open",
                    error=f"{e}. Left for a later retry (the watcher retries once Madden27.exe has closed; JR sets "
                          f"{FRANCHISE_CLOSED_ENV}=1 when he works at Madden's main menu).")
    except lo.RecoveryError as e:
        rep = last_report()
        run_id = rep.get("run_id")
        msg = str(e)
        if "--recover" not in msg:
            msg += f"; run `python3 -m pipeline.league_office --recover {run_id if run_id else '<run_id>'}`"
        return dict(res, status="needs_recovery", run_id=run_id, stage=rep.get("stage"), error=msg)
    except Exception as e:      # noqa: BLE001 - StageError, ProgressionIntegrationError, CompositionError, ...
        rep = last_report()
        return dict(res, status="failed", run_id=rep.get("run_id"), stage=rep.get("stage"),
                    error=f"{type(e).__name__}: {e}")
    rep = last_report()
    res.update(run_id=run_id, stage=rep.get("stage"))
    status = rep.get("status")
    if dry_run:
        return dict(res, status="dry_run")
    if status == "WRITTEN":
        return dict(res, status="written", replaced=bool(rep.get("replaced")), output_sha256=rep.get("output_sha256"),
                    retirement_window=(rep.get("summary") or {}).get("retirement_window"))
    return dict(res, status="failed", error=rep.get("error") or f"league office run {run_id} ended {status}")


def retirement_window_notice(result):
    """(line, priority) for the offseason notification from a written league office run's
    summary["retirement_window"] (RETIREMENT_WINDOW_PLAN §2.5), or None without one. High priority when a rostered
    player vanished or a restore hit a conflict."""
    from pipeline import retirement_window as rw

    window = (result or {}).get("retirement_window")
    if not isinstance(window, dict) or window.get("role") not in ("decision", "window"):
        return None          # in-season runs store only the roster image
    return rw.notice(window)


def report_league_office(result, save_name, log_fn=log):
    """Logs the league office result and notifies on a failure; returns a short line for the week's notification."""
    status = result.get("status")
    run = f" (run {result['run_id']})" if result.get("run_id") else ""
    if status == "written":
        line = (f"League office wrote {save_name}{run}" if result.get("replaced")
                else f"League office: nothing to write{run}")
        log_fn(line)
        return line
    if status == "dry_run":
        line = f"League office dry run{run} (nothing written)"
        log_fn(line)
        return line
    if status == "franchise_open":
        line = "League office waiting: Madden27.exe is running"
        log_fn(f"[WARN] League office not run on {save_name}: {result.get('error')}")
        return line
    if status == "skipped":
        line = f"League office skipped: {result.get('error')}"
        log_fn(line)
        return line
    title = "Mu: league office needs recovery" if status == "needs_recovery" else "Mu: league office failed"
    msg = f"League office on {save_name}{run} {status.replace('_', ' ')}: {result.get('error')}"
    log_fn(f"[ERROR] {msg}")
    send_notify(msg, title=title, priority="high", tags=("warning", "x"))
    return f"League office {status.replace('_', ' ')}{run}"


def ingest_snapshot(snapshot_path, save_season, save_week, dry_run=False, no_articles=False, no_engines=False,
                    save_name="", log_fn=log, save_path=None, league_office=False, league_office_dry_run=False,
                    save_sha256=None, stage_only=False):
    """load_save -> engines -> league office -> articles -> next week's mod pack against DATABASE_URL. Runs in the
    child process run_ingest starts. `league_office` runs the league office on `save_path` (checked against
    `save_sha256`); `stage_only` (an offseason stop) loads and runs the league office only."""
    from pipeline import load_save

    load_report = load_save.load(snapshot_path, season=save_season, commit=(not dry_run))
    log_fn(f"Load report: {load_report.get('league_state')}, players: {load_report.get('players')}, "
           f"completed games: {load_report.get('games_completed')}")

    if dry_run:
        log_fn("Dry run mode: database changes rolled back, skipping engines and articles.")
        return {"status": "success", "dry_run": True, "season": save_season, "week": save_week, "load_report": load_report}
    dsn = os.environ.get("DATABASE_URL")
    lo_result = None

    # The personality switchboard, read once for this ingest (every feature off by default)
    modes = personality_modes(dsn, log_fn=log_fn)
    # Step: relationship producers (after the load, before the stage_only branch; off by default; never fatal)
    producers = step_relationship_producers(snapshot_path, save_season, dsn=dsn, log_fn=log_fn, modes=modes)
    # Step: personality evolution (OFFSEASON_START only, after the producers, before the league office; off by default)
    evolution = step_personality_evolution(snapshot_path, save_season, dsn=dsn, log_fn=log_fn, modes=modes)
    # Step: focus refresh (OFFSEASON_START only, right after evolution, before the league office; off by default)
    focus_refresh = step_focus_refresh(snapshot_path, save_season, dsn=dsn, log_fn=log_fn, modes=modes)
    # Step: trade-request reviews (after producers and evolution; off by default; never fatal)
    trade_requests = step_trade_requests(snapshot_path, save_season, dsn=dsn, log_fn=log_fn, modes=modes)

    def step_league_office():
        nonlocal lo_result
        if not league_office:
            return None
        log_fn("Step 7e: league office")
        lo_result = run_league_office(snapshot_path, save_path, dsn, dry_run=league_office_dry_run,
                                      expected_sha256=save_sha256, log_fn=log_fn)
        if lo_result["status"] == "franchise_open":
            lo_result["retry_command"] = league_office_command(snapshot_path, save_path, dsn)
            log_fn(f"League office retry, by hand: {lo_result['retry_command']}")
        return report_league_office(lo_result, save_name, log_fn=log_fn)

    def result():
        return {"status": "success", "dry_run": False, "season": save_season, "week": save_week,
                "load_report": load_report, "league_office": lo_result, "relationship_producers": producers,
                "personality_evolution": evolution, "focus_refresh": focus_refresh,
                "trade_requests": trade_requests}

    if stage_only:
        # An offseason stop: no current week, so no weekly engines, articles or mod pack.
        lo_line = step_league_office()
        rw_notice = retirement_window_notice(lo_result)
        rw_line, rw_priority = rw_notice if rw_notice else ("", "default")
        if rw_line:
            log_fn(rw_line)
        send_notify(f"Ingested season {save_season} offseason stage from {save_name}. {lo_line or ''} "
                    f"{rw_line}".strip(),
                    title="Mu: offseason stage done", priority=rw_priority,
                    tags=("white_check_mark",) if rw_priority == "default" else ("warning",))
        return result()

    if no_engines:
        log_fn("--no-engines: loaded only, skipping engines, league office and articles.")
        return result()

    # Step: engines
    if save_week is None:
        log_fn(f"Step 6b: rookie headshots (season {save_season})")
        step_rookie_headshots(save_season)
        log_fn(f"Step 7: start-of-season calibration engines (season {save_season})")
        step_preseason_engines(save_season)
    else:
        run_weekly_engines(save_season, mode="weekly", log_fn=log_fn)
        queue_nightly_jobs(save_season, save_week, log_fn=log_fn)

    # Step: league office (after the engines, before the articles, so they can report its moves; never fatal)
    lo_line = step_league_office()

    # Step: articles
    published = None
    if not no_articles and save_week is not None:
        log_fn("Step 8: newsroom articles")
        res = step_articles(save_season, week=save_week)
        published = f"slate for Week {res.get('week')}: {res.get('status')}"
        if res.get("status") not in ("published", "skipped_exists", "season_complete"):
            raise WeekStopped(f"Articles did not publish ({res})")

    # Step: the mod pack for the user teams' next game (the save's current week is Madden's next unplayed week)
    if save_week is not None:
        prepare_next_week_mod_pack(dsn, save_season, save_week, log_fn=log_fn)

    # PRESEASON / CUTDOWN runs are in the retirement window too (RETIREMENT_WINDOW_PLAN §2.1)
    rw_notice = retirement_window_notice(lo_result)
    rw_line, rw_priority = rw_notice if rw_notice else ("", "default")
    if rw_line:
        log_fn(rw_line)
    send_notify(
        f"Ingested season {save_season} week {save_week or 'preseason'} from {save_name}. "
        f"{published or 'Articles skipped.'}" + (f" {lo_line}." if lo_line else "") + (f" {rw_line}" if rw_line else ""),
        title="Mu: week done", priority=rw_priority,
        tags=("white_check_mark",) if rw_priority == "default" else ("warning",)
    )

    return result()


def run_ingest(snapshot_path, dsn, save_season, save_week, dry_run=False, no_articles=False, no_engines=False,
               save_name="", log_fn=log, save_path=None, league_office=False, league_office_dry_run=False,
               save_sha256=None, stage_only=False):
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
    if league_office:
        cmd += ["--run-league-office", "--save-path", save_path]
        if save_sha256:
            cmd += ["--save-sha256", save_sha256]
        if league_office_dry_run:
            cmd.append("--league-office-dry-run")
    if stage_only:
        cmd.append("--stage-only")
    p = subprocess.run(cmd, cwd=ROOT, env={**os.environ, "DATABASE_URL": dsn})
    if p.returncode != 0 or not os.path.exists(result_path):
        raise WatcherError(f"Ingest of {save_name} into {db_name_of(dsn)} failed (exit {p.returncode}); see the log above.")
    with open(result_path) as fh:
        return json.load(fh)


def run_league_office_child(snapshot_path, dsn, save_path, save_sha256=None, dry_run=False):
    """A retry of the league office alone (after a franchise-open refusal), in a child process with
    DATABASE_URL=dsn; returns run_league_office's result."""
    result_path = os.path.join(os.path.dirname(snapshot_path), LO_RESULT)
    if os.path.exists(result_path):
        os.remove(result_path)
    cmd = [sys.executable, "-m", "pipeline.watch_save", "--league-office-only", snapshot_path,
           "--save-path", save_path]
    if save_sha256:
        cmd += ["--save-sha256", save_sha256]
    if dry_run:
        cmd.append("--league-office-dry-run")
    p = subprocess.run(cmd, cwd=ROOT, env={**os.environ, "DATABASE_URL": dsn})
    if p.returncode != 0 or not os.path.exists(result_path):
        return {"status": "failed", "error": f"league office child failed (exit {p.returncode}); see the log above"}
    with open(result_path) as fh:
        return json.load(fh)


def freeze_week(db, dsn, save_path, label, season, week, stage=None, extra=None, log_fn=log):
    """The paired database + save freeze after an ingest (pipeline/weekly_freeze.py). Never fatal: a failure is
    logged and notified and returns None."""
    try:
        m = weekly_freeze.freeze(db, dsn, save_path, label, season=season, week=week, stage=stage, extra=extra)
        log_fn(f"Freeze {db}/{label}: dump {m['dump_bytes'] / 1e6:.1f} MB, save {m.get('save_name')}")
        return m
    except Exception as e:      # noqa: BLE001 - a freeze never stops the week
        msg = f"Freeze {db}/{label} failed: {e}"
        log_fn(f"[ERROR] {msg}")
        send_notify(msg, title="Mu: weekly freeze failed", priority="high", tags=("warning",))
        return None


def process_save(save_path, season=None, dry_run=False, no_articles=False, no_engines=False, force=False,
                 auto_init=False, reseed=False, confirm_reseed=None, user_teams=None, franchise_name=None,
                 run_folder=None, dsn=None, reader_script=READER_SCRIPT, log_fn=log, no_league_office=False,
                 league_office_dry_run=False, no_freeze=False):
    """
    Core processing for a settled save file:
    1. Snapshots into captures/<run>/save.json
    2. Routes it to its franchise's database (route_franchise), creating and seeding it with --auto-init;
       an explicit `dsn` skips routing and loads into that database
    3. Compares save season & week against that database's league_state; with the league office on, an
       offseason stop is a 'new_stage' until league_office_runs has a WRITTEN / PLANNED run for it
    4. If new week / stage (or force or just seeded): runs load_save -> engines -> league office -> articles ->
       mod pack there (run_ingest). The league office runs only on a new week or stage (not right after seeding,
       not on a forced re-load), never with --dry-run or --no-league-office.
    5. Freezes the database and the save together (freeze_week), unless --dry-run or --no-freeze.
    """
    if run_folder is None:
        run_name = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        run_folder = os.path.join(CAPTURES, run_name)

    lo_enabled = not no_league_office and not dry_run
    # The save's fingerprint as read: the league office (and a later retry) only runs on that exact save.
    save_sha256 = file_sha256(save_path) if lo_enabled else None
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

    classification = None
    stage_info = None
    if not just_seeded:
        classification = classify_week(save_season, save_week, stored_season, stored_week)
        if classification == "no_current_week" and lo_enabled:
            stage = league_office_stage(snapshot, log_fn=log_fn)
            if stage is not None:
                stage_info = stage
                done = league_office_stage_done(dsn, stage)
                classification = "same_stage" if done else "new_stage"
                log_fn(f"Offseason stop {stage['stage']} (season {stage['season']}, week {stage['week']}"
                       + (f", wave {stage['wave']}" if stage.get("wave") else "") + "): "
                       + ("league office already ran for it" if done else "league office not run yet"))

    if not force and not just_seeded:
        if classification == "same_stage":
            log_fn(f"Save {save_name} is at an offseason stop the league office already handled for {db}. No-op.")
            return no_op("same_stage")
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

    run_lo = lo_enabled and not no_engines and classification in ("new_week", "new_stage")
    if lo_enabled and not run_lo:
        if just_seeded:
            log_fn("League office not run on the load that seeds the franchise: run "
                   "`python3 -m pipeline.league_office setup` before its first league office run.")
        elif no_engines:
            log_fn("League office not run with --no-engines.")
        else:
            log_fn(f"League office not run on a forced re-load ({classification}).")
    elif not lo_enabled:
        log_fn("League office not run (" + ("--dry-run" if dry_run else "--no-league-office") + ").")
    stage_only = classification == "new_stage"

    log_fn(f"Ingesting {'offseason stage' if stage_only else 'week'}: season {save_season} week {save_week} into {db} "
           f"(dry_run={dry_run}, no_articles={no_articles}, no_engines={no_engines}, league_office={run_lo}"
           f"{', league office dry run' if run_lo and league_office_dry_run else ''})")
    res = run_ingest(snapshot_path, dsn, save_season, save_week, dry_run=dry_run, no_articles=no_articles,
                     no_engines=no_engines, save_name=save_name, log_fn=log_fn, save_path=save_path,
                     league_office=run_lo, league_office_dry_run=league_office_dry_run,
                     save_sha256=save_sha256, stage_only=stage_only)
    freeze = None
    if not dry_run and not no_freeze:
        label = weekly_freeze.label_for(save_season, save_week, stage_info if stage_only else None)
        lo = res.get("league_office") or {}
        freeze = freeze_week(db, dsn, save_path, label, save_season, save_week,
                             stage=stage_info if stage_only else None,
                             extra={"classification": classification, "articles": not no_articles and not stage_only,
                                    "league_office_status": lo.get("status")}, log_fn=log_fn)
        freeze = {"label": label, "season": save_season, "week": save_week,
                  "stage": stage_info if stage_only else None, "ok": freeze is not None}
    return {**res, "db": db, "dsn": dsn, "franchise": fid, "snapshot_path": snapshot_path, "save_path": save_path,
            "save_sha256": save_sha256, "classification": classification, "freeze": freeze}


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
    pending = {}      # save path -> league office retry (refused while Madden27.exe ran)

    while running:
        poll_once(watch_path, pattern, debouncer, pending, lock_path, log_fn=log_fn, **process_kw)
        time.sleep(poll_interval)


def note_league_office(res, save_file, debouncer, pending, league_office_dry_run=False, log_fn=log):
    """After process_save: remember the league office's own write, or queue a retry after a franchise-open
    refusal."""
    lo_res = (res or {}).get("league_office") or {}
    if lo_res.get("replaced") and lo_res.get("output_sha256"):
        debouncer.note_own_write(save_file, lo_res["output_sha256"])
    if lo_res.get("status") == "franchise_open" and res.get("snapshot_path") and res.get("dsn"):
        pending[save_file] = {"snapshot_path": res["snapshot_path"], "dsn": res["dsn"],
                              "save_sha256": res.get("save_sha256"), "dry_run": league_office_dry_run,
                              "db": res.get("db"), "freeze": res.get("freeze")}
        log_fn(f"League office for {os.path.basename(save_file)} queued: retried once Madden27.exe has closed.")


def retry_league_office(pending, debouncer, lock_path, madden_running=None, log_fn=log):
    """Runs queued league office retries once Madden27.exe has closed (each on its own snapshot, only while the save
    is unchanged)."""
    if not pending:
        return
    if madden_running is None:
        from pipeline.gpu_queue.process_check import is_madden_running as madden_running
    if madden_running():
        return
    for save_file, job in list(pending.items()):
        try:
            with run_lock(lock_path):
                pending.pop(save_file, None)
                log_fn(f"Madden27.exe has closed: retrying the league office on {os.path.basename(save_file)}")
                lo_res = run_league_office_child(job["snapshot_path"], job["dsn"], save_file,
                                                 save_sha256=job.get("save_sha256"), dry_run=job.get("dry_run"))
                if lo_res.get("replaced") and lo_res.get("output_sha256"):
                    debouncer.note_own_write(save_file, lo_res["output_sha256"])
                line = report_league_office(lo_res, os.path.basename(save_file), log_fn=log_fn)
                fz = job.get("freeze")
                if fz and lo_res.get("replaced") and job.get("db"):
                    # The save changed after the week's freeze: freeze the pair again under the same label.
                    freeze_week(job["db"], job["dsn"], save_file, fz["label"], fz.get("season"), fz.get("week"),
                                stage=fz.get("stage"), extra={"league_office_status": lo_res.get("status"),
                                                              "refrozen_after_retry": True}, log_fn=log_fn)
                if lo_res.get("status") in ("written", "dry_run"):
                    send_notify(f"{line} (retried after Madden closed).", title="Mu: league office done",
                                tags=("white_check_mark",))
        except LockError as e:
            pending.setdefault(save_file, job)
            log_fn(f"Lock active: {e}. League office retry waits for the next check.")
            return
        except Exception as e:      # noqa: BLE001 - a retry never stops the daemon
            traceback.print_exc()
            msg = f"League office retry on {os.path.basename(save_file)} failed: {e}"
            log_fn(f"[ERROR] {msg}")
            send_notify(msg, title="Mu: league office failed", priority="high", tags=("warning", "x"))


def poll_once(watch_path, pattern, debouncer, pending, lock_path, log_fn=log, **process_kw):
    """One pass of the daemon: league office retries, then every settled save not processed yet."""
    retry_league_office(pending, debouncer, lock_path, log_fn=log_fn)
    save_files = find_save_files(watch_path, pattern=pattern)
    for save_file in save_files:
        if debouncer.is_already_processed(save_file):
            continue

        if debouncer.is_settled(save_file):
            if debouncer.is_own_write(save_file):
                log_fn(f"{os.path.basename(save_file)} is the league office's own write; not ingested again.")
                debouncer.mark_processed(save_file)
                continue
            log_fn(f"Settled save file detected: {save_file}")
            # A newer save replaces any queued league office retry for the older one.
            if pending.pop(save_file, None):
                log_fn(f"Dropped the queued league office retry for {os.path.basename(save_file)}: the save "
                       "changed.")
            try:
                before = os.path.getmtime(save_file)
            except OSError:
                continue
            try:
                with run_lock(lock_path):
                    res = process_save(save_file, log_fn=log_fn, **process_kw)
                    # The mtime seen before processing: a save written meanwhile (Madden, or the league office,
                    # recognised by its sha256) is looked at again.
                    debouncer.mark_processed(save_file, mtime=before)
                    note_league_office(res, save_file, debouncer, pending,
                                       league_office_dry_run=process_kw.get("league_office_dry_run", False),
                                       log_fn=log_fn)
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


def ingest_main(args):
    """The child side of run_ingest: DATABASE_URL already names the franchise's database."""
    res = ingest_snapshot(args.ingest, args.season, args.week, dry_run=args.dry_run,
                          no_articles=args.no_articles, no_engines=args.no_engines, save_name=args.save_name,
                          save_path=args.save_path, league_office=args.run_league_office,
                          league_office_dry_run=args.league_office_dry_run, save_sha256=args.save_sha256,
                          stage_only=args.stage_only)
    with open(os.path.join(os.path.dirname(args.ingest), "ingest_result.json"), "w") as fh:
        json.dump(res, fh, default=str)
    return 0


def league_office_main(args):
    """The child side of run_league_office_child (a retry): DATABASE_URL names the franchise's database."""
    res = run_league_office(args.league_office_only, args.save_path, os.environ.get("DATABASE_URL"),
                            dry_run=args.league_office_dry_run, expected_sha256=args.save_sha256)
    with open(os.path.join(os.path.dirname(args.league_office_only), LO_RESULT), "w") as fh:
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
    ap.add_argument("--dry-run", action="store_true",
                    help="Load without commit, no engines, league office, articles or database setup")
    ap.add_argument("--no-articles", action="store_true", help="Run loader and engines, skip newsroom articles")
    ap.add_argument("--no-engines", action="store_true", help="Run the loader only (no engines, no articles)")
    ap.add_argument("--no-league-office", action="store_true",
                    help="Don't run the league office (pipeline.league_office) after the weekly engines")
    ap.add_argument("--league-office-dry-run", action="store_true",
                    help="Run the league office with dry_run=True: plan only, no save write, nothing acknowledged "
                         f"(the real run needs {FRANCHISE_CLOSED_ENV}=1 while Madden27.exe is open)")
    ap.add_argument("--season", type=int, help="Override season year (default: save's calendar year)")
    ap.add_argument("--force", action="store_true", help="Run even if week is same as or older than league_state")
    ap.add_argument("--auto-init", action="store_true", help="Create and seed the database of a franchise seen for the first time")
    ap.add_argument("--franchise-name", help="Name for a new franchise (its database becomes madden_<name>)")
    ap.add_argument("--user-team", default="", help="Comma-separated user-controlled teams for a new franchise (e.g. IND or IND,CAR)")
    ap.add_argument("--reseed", action="store_true", help="Drop and recreate this franchise's database, then seed it (--once only)")
    ap.add_argument("--confirm-reseed", metavar="DB", help="Required with --reseed: the database name being dropped")
    ap.add_argument("--dsn", help="Load into this database instead of routing by franchise")
    ap.add_argument("--lock-file", default=DEFAULT_LOCK_PATH, help=f"Lock file path (default: {DEFAULT_LOCK_PATH})")
    ap.add_argument("--no-freeze", action="store_true",
                    help="Don't freeze the database + save after each ingest (pipeline/weekly_freeze.py)")
    # Internal: the child process run_ingest starts.
    ap.add_argument("--ingest", help=argparse.SUPPRESS)
    ap.add_argument("--week", type=int, help=argparse.SUPPRESS)
    ap.add_argument("--save-name", default="", help=argparse.SUPPRESS)
    ap.add_argument("--save-path", help=argparse.SUPPRESS)
    ap.add_argument("--save-sha256", help=argparse.SUPPRESS)
    ap.add_argument("--run-league-office", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--stage-only", action="store_true", help=argparse.SUPPRESS)
    # Internal: the child process run_league_office_child starts (a league office retry).
    ap.add_argument("--league-office-only", help=argparse.SUPPRESS)
    args = ap.parse_args()

    if args.ingest:
        sys.exit(ingest_main(args))
    if args.league_office_only:
        sys.exit(league_office_main(args))

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
                      dsn=args.dsn, no_league_office=args.no_league_office,
                      league_office_dry_run=args.league_office_dry_run, no_freeze=args.no_freeze)

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
