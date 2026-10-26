"""
The weekly run (docs/PIPELINE_MIGRATION.md, phase 5: target weekly flow, steps 2-9), started on demand
from the debug page (#debug, server/debug_api.py: Scan week / Backfill previous week / Game scan) or by hand:

  python3 -m pipeline.weekly_capture                  # connect, capture, extract, load, backfill + engines, articles, shrink
  python3 -m pipeline.weekly_capture --no-articles    # the same without articles (backfill previous week)
  python3 -m pipeline.weekly_capture --mode games     # every game of last week: box scores + player stats, loaded
                                                      # with --replace over the backfill's lines; never articles
  python3 -m pipeline.weekly_capture --resume <run>   # continue captures/<run> from the step it stopped on
  python3 -m pipeline.weekly_capture --mode contracts [--teams CAR,NO]
                                                      # Team Salary for all 32 teams (or a re-scan of these),
                                                      # extracted and loaded; never engines or articles
  python3 -m pipeline.weekly_capture --mode transactions
                                                      # League Transactions only: capture (from the newest loaded
                                                      # heading, or SEASON - WEEK 1 on the first run), extract,
                                                      # load; never engines or articles

League Transactions (team management step 2): the weekly and playoff captures also read League > LEAGUE
TRANSACTIONS from the newest heading already loaded (--since); the load applies the moves before Season Stats
and keeps contracts and team cap rows current from them. Nothing re-scans Team Salary automatically (JR,
2026-10-19): --mode contracts --teams stays a by-hand tool.

Week 22 (league_state.current_week 22, Madden on Pro Bowl week): no games of ours, so Scan week is an
articles-only run: engines, the Week 23 (Super Bowl week) slate, then current_week moves to 23. No console.
Week 23: the Super Bowl capture assumes Madden is on the postgame screen after playing the game;
captures the Super Bowl box score and player stats directly without advancing to the offseason.

Postseason (league_state.current_week 19+: the week a run captures is the one the last load left current,
so from the Wild Card round on it is a playoff round): the run is a playoff run on its own (or --mode playoff). Season Stats and Team Stats are regular season only and don't change, so it
captures last round's schedule (and the coming round's matchups) and every game's box score and lines
(madden_capture.py --only playoff), loads them in one transaction, then runs the engines and articles.
The Week 18 run is a normal weekly run; it also records the Wild Card matchups (schedule_next/).

Steps (each one's failure stops the week: nothing after it runs, and a notice goes to JR's phone):
  2. connect   madden_capture.py --connect: start or restart the chiaki-ng stream and wait up to 2 minutes
               for a franchise screen (chiaki stream lands on it directly). Presses nothing.
  3. capture   madden_capture.py --out <run>: Season Stats, Team Stats and last week's schedule
               (games mode: --only games, ~7 min a game, resuming per game; playoff mode: --only playoff,
               the schedule then every game, resuming the games with --only games --resume;
               Super Bowl / Week 23: --only postgame directly from the postgame screen).
  4. hand back notice: turn the controller back on and restart the broadcast (the script doesn't disconnect).
  5. extract   extract.py captures/<run>.
  6. load      pipeline.load_capture, one transaction; a failed check writes nothing.
  7. engines   the backfill and the analytics engines for league_state.current_season (strict: an
               engine error stops the week).
  8. articles  newsroom.weekly_run (skips a week already published).
  9. shrink    screenshots -> WebP q90 at full resolution, debug folders deleted, CSVs kept.

Resuming picks the first step not done: capture is done when the run wrote .capture_done (a weekly run
from before that marker: week.json), extract when the mode's CSVs exist (weekly: schedule.csv + stats_*.csv;
games/postgame: game_scores.csv), load when the captures row is 'loaded'.
The backfill only fills missing games and the articles skip a published week, so both are safe to rerun.
Only one weekly run at a time (lock file captures/.weekly_run.lock).
"""
import argparse, datetime, fcntl, glob, os, shutil, subprocess, sys, time, traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CAPTURES = os.path.join(ROOT, "captures")
LOCK_PATH = os.path.join(CAPTURES, ".weekly_run.lock")
CAPTURE_DIRS = ("stats", "team_stats", "schedule", "injuries", "stop_debug", "scope_debug", "week_debug")
DEBUG_DIRS = ("stop_debug", "scope_debug", "week_debug", "team_debug", "probe", "explore")
CONNECT_TIMEOUT = 6 * 60       # madden_capture --connect: up to 2 min, twice when it has to relaunch
CAPTURE_TIMEOUT = 75 * 60      # the weekly capture takes ~20 min
CONTRACTS_TIMEOUT = 150 * 60   # Team Salary: ~3 min a team (scroll to top, settle, pair checks), ~100 min for 32
CONTINUE_WINDOW = 24 * 60 * 60 # a contracts run without --resume continues an unfinished one this recent
GAMES_TIMEOUT = 4 * 60 * 60    # --only games: ~7 min a game, 16 games
MODES = ("weekly", "games", "playoff", "roster", "prospects", "season", "postgame", "draft-results", "team-overalls",
         "contracts", "transactions")
FIRST_PLAYOFF_WEEK = 19    # league_state.current_week is the week the next run captures (Wild Card = 19)
PRO_BOWL_WEEK, FINAL_WEEK = 22, 23
SUPER_BOWL_LABEL = "POST SEASON SUPER BOWL"   # named for the Super Bowl postgame capture


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
def folder_mode(folder):
    """A run folder's mode from what it holds."""
    if os.path.exists(os.path.join(folder, "draft_results.csv")) or os.path.exists(os.path.join(folder, "draft_results.json")) or os.path.isdir(os.path.join(folder, "draft_results")):
        return "draft-results"
    if os.path.isdir(os.path.join(folder, "team_overalls")):
        return "team-overalls"
    if os.path.isdir(os.path.join(folder, "season_schedule")) and (os.path.isdir(os.path.join(folder, "roster")) or os.path.isdir(os.path.join(folder, "prospects"))):
        return "season"
    if os.path.isdir(os.path.join(folder, "contracts")) and not any(
            os.path.isdir(os.path.join(folder, d)) for d in ("roster", "season_schedule", "prospects")):
        return "contracts"
    if os.path.isdir(os.path.join(folder, "prospects")):
        return "prospects"
    if os.path.isdir(os.path.join(folder, "roster")):
        return "roster"
    if os.path.isdir(os.path.join(folder, "season_schedule")):
        return "season_schedule"
    if os.path.isdir(os.path.join(folder, "games")):
        return "playoff" if os.path.isdir(os.path.join(folder, "schedule")) else "games"
    if os.path.isdir(os.path.join(folder, "transactions")) and not any(
            os.path.isdir(os.path.join(folder, d)) for d in ("stats", "schedule", "team_stats", "injuries")):
        return "transactions"
    return "weekly"


def tx_pages(folder):
    """League Transactions screenshots (PNG, or WebP after shrink)."""
    return glob.glob(os.path.join(folder, "transactions", "page_*.png")) + \
        glob.glob(os.path.join(folder, "transactions", "page_*.webp"))


def tx_extracted(folder):
    """No transactions pages, or transactions.csv written."""
    return not tx_pages(folder) or os.path.exists(os.path.join(folder, "transactions.csv"))


def capture_done(folder, mode="weekly"):
    if os.path.exists(os.path.join(folder, ".capture_done")):
        return True
    if mode in ("draft-results", "draft_results"):
        return (os.path.exists(os.path.join(folder, "draft_results.csv"))
                or os.path.exists(os.path.join(folder, "draft_results.json"))
                or os.path.isdir(os.path.join(folder, "draft_results")))
    if mode in ("team-overalls", "team_overalls"):
        return (os.path.exists(os.path.join(folder, "team_overalls.csv"))
                or os.path.isdir(os.path.join(folder, "team_overalls")))
    return mode == "weekly" and os.path.exists(os.path.join(folder, "week.json"))


def extract_done(folder, mode="weekly"):
    if mode in ("draft-results", "draft_results"):
        return os.path.exists(os.path.join(folder, "draft_results.csv")) or os.path.exists(os.path.join(folder, "draft_results.json"))
    if mode in ("team-overalls", "team_overalls"):
        return os.path.exists(os.path.join(folder, "team_overalls.csv"))
    if mode in ("games", "postgame"):
        return os.path.exists(os.path.join(folder, "game_scores.csv"))
    if mode == "playoff":
        if os.path.isdir(os.path.join(folder, "games", "01_super_bowl")) or not os.path.isdir(os.path.join(folder, "schedule")):
            return os.path.exists(os.path.join(folder, "game_scores.csv"))
        ok = all(os.path.exists(os.path.join(folder, f)) for f in ("game_scores.csv", "schedule.csv"))
        if os.path.isdir(os.path.join(folder, "injuries")):
            ok = ok and os.path.exists(os.path.join(folder, "injuries.csv"))
        return ok and tx_extracted(folder)
    if mode == "transactions":
        return os.path.exists(os.path.join(folder, "transactions.csv"))
    if mode == "contracts":
        return all(os.path.exists(os.path.join(folder, f)) for f in ("contracts.csv", "team_cap.csv"))
    if mode == "roster":
        return all(os.path.exists(os.path.join(folder, f)) for f in ("roster_cards.csv", "roster_list.csv"))
    if mode == "prospects":
        return os.path.exists(os.path.join(folder, "prospects.csv"))
    if mode == "season":
        ok = (os.path.exists(os.path.join(folder, "season_schedule.csv"))
                and all(os.path.exists(os.path.join(folder, f)) for f in ("roster_cards.csv", "roster_list.csv"))
                and os.path.exists(os.path.join(folder, "prospects.csv")))
        if os.path.isdir(os.path.join(folder, "injuries")):
            ok = ok and os.path.exists(os.path.join(folder, "injuries.csv"))
        if os.path.isdir(os.path.join(folder, "team_overalls")):
            ok = ok and os.path.exists(os.path.join(folder, "team_overalls.csv"))
        if os.path.isdir(os.path.join(folder, "contracts")):
            ok = ok and all(os.path.exists(os.path.join(folder, f)) for f in ("contracts.csv", "team_cap.csv"))
        return ok
    weekly_ok = (os.path.exists(os.path.join(folder, "schedule.csv"))
                 and bool(glob.glob(os.path.join(folder, "stats_*.csv"))))
    if os.path.isdir(os.path.join(folder, "injuries")):
        weekly_ok = weekly_ok and os.path.exists(os.path.join(folder, "injuries.csv"))
    if os.path.isdir(os.path.join(folder, "team_overalls")):
        weekly_ok = weekly_ok and os.path.exists(os.path.join(folder, "team_overalls.csv"))
    return weekly_ok and tx_extracted(folder)


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


def playoff_or_weekly(resume=None):
    """'playoff' when the week this run captures is a playoff round (league_state.current_week 19+, which the
    last load set to the week Madden then had current) or a resumed folder is a playoff run, else 'weekly'."""
    if resume and os.path.isdir(os.path.join(CAPTURES, resume)):
        if folder_mode(os.path.join(CAPTURES, resume)) == "playoff":
            return "playoff"
    try:
        _, current = league_state()
    except Exception:
        return "weekly"
    return "playoff" if current and current >= FIRST_PLAYOFF_WEEK else "weekly"


# --------------------------------------------------------------- steps ----
def step_connect(log_path):
    code, last = run_cmd([sys.executable, "madden_capture.py", "--connect"], log_path, CONNECT_TIMEOUT)
    if code != 0:
        raise WeekStopped(f"Couldn't reach Madden's franchise menus over chiaki-ng: {last}. Nothing was pressed.")


def step_capture(folder, log_path, mode="weekly", week_label=None, teams=None, since=None):
    """teams: --mode contracts only, team nicknames (PANTHERS) for madden_capture --team-names.
    since: League Transactions cutoff heading for the weekly, playoff and transactions captures."""
    name = os.path.basename(folder)
    named = ["--week", week_label] if week_label else []
    since_args = ["--since", since] if since else []
    if mode == "games":                        # resumes per game (games/<NN>/DONE)
        where = ["--resume", name] if os.path.isdir(os.path.join(folder, "games")) else ["--out", name]
        args, timeout = [sys.executable, "madden_capture.py", "--only", "games", *where], GAMES_TIMEOUT
    elif mode in ("playoff", "postgame"):      # schedule (+ next round), then the games; Super Bowl is postgame
        if mode == "postgame" or week_label == SUPER_BOWL_LABEL:
            where = ["--resume", name] if os.path.isdir(os.path.join(folder, "games")) else ["--out", name]
            args, timeout = [sys.executable, "madden_capture.py", "--only", "postgame", *where], GAMES_TIMEOUT
        elif os.path.isdir(os.path.join(folder, "games")) and os.path.exists(os.path.join(folder, "week.json")):
            args = [sys.executable, "madden_capture.py", "--only", "games", "--resume", name, *named]
        else:
            args = [sys.executable, "madden_capture.py", "--only", "playoff", "--out", name, *named, *since_args]
        timeout = GAMES_TIMEOUT
    elif mode == "roster":
        where = ["--resume", name] if os.path.isdir(os.path.join(folder, "roster")) else ["--out", name]
        args, timeout = [sys.executable, "madden_capture.py", "--only", "roster", *where], CAPTURE_TIMEOUT
    elif mode == "contracts":                  # ~40 min for 32 teams, resumes per team (contracts/done.txt)
        where = ["--resume", name] if os.path.isdir(os.path.join(folder, "contracts")) else ["--out", name]
        named_teams = ["--team-names", ",".join(teams)] if teams else []
        args, timeout = [sys.executable, "madden_capture.py", "--only", "contracts", *where, *named_teams], CONTRACTS_TIMEOUT
    elif mode == "transactions":
        where = ["--resume", name] if os.path.isdir(os.path.join(folder, "transactions")) else ["--out", name]
        args, timeout = [sys.executable, "madden_capture.py", "--only", "transactions", *where, *since_args], CAPTURE_TIMEOUT
    elif mode == "prospects":
        args, timeout = [sys.executable, "madden_capture.py", "--only", "prospects", "--all-prospects", "--out", name], CAPTURE_TIMEOUT
    elif mode in ("team-overalls", "team_overalls"):
        where = ["--resume", name] if os.path.isdir(os.path.join(folder, "team_overalls")) else ["--out", name]
        args, timeout = [sys.executable, "madden_capture.py", "--only", "team-overalls", *where], CAPTURE_TIMEOUT
    elif mode == "season":
        where = ["--resume", name] if (os.path.isdir(os.path.join(folder, "roster")) or os.path.isdir(os.path.join(folder, "season_schedule")) or os.path.isdir(os.path.join(folder, "prospects")) or os.path.isdir(os.path.join(folder, "injuries")) or os.path.isdir(os.path.join(folder, "team_overalls")) or os.path.isdir(os.path.join(folder, "contracts"))) else ["--out", name]
        # schedule, rosters, contracts (~40 min), team overalls, injuries, prospects
        args, timeout = ([sys.executable, "madden_capture.py", "--only", "season", "--all-prospects", *where],
                         CAPTURE_TIMEOUT * 2 + CONTRACTS_TIMEOUT)
    elif mode in ("draft-results", "draft_results"):
        where = ["--resume", name] if os.path.isdir(os.path.join(folder, "draft_results")) else ["--out", name]
        args, timeout = [sys.executable, "madden_capture.py", "--only", "draft-results", *where], CAPTURE_TIMEOUT
    else:
        for d in DEBUG_DIRS:                   # clear old debug frames, keep partial captures
            shutil.rmtree(os.path.join(folder, d), ignore_errors=True)
        if os.path.exists(os.path.join(folder, "week.json")):
            os.remove(os.path.join(folder, "week.json"))
        has_partial = any(os.path.isdir(os.path.join(folder, d)) for d in ("stats", "injuries", "team_stats", "schedule"))
        where = ["--resume", name] if has_partial else ["--out", name]
        args, timeout = [sys.executable, "madden_capture.py", *where, *named, *since_args], CAPTURE_TIMEOUT
    code, last = run_cmd(args, log_path, timeout)
    if code != 0 or (mode in ("weekly", "playoff", "postgame") and not os.path.exists(os.path.join(folder, "week.json"))):
        raise WeekStopped(f"Capture stopped: {last}. The console is left where it stopped; frames in "
                          f"{os.path.relpath(folder, ROOT)}/stop_debug.")
    open(os.path.join(folder, ".capture_done"), "w").close()


def step_extract(folder, log_path, mode="weekly"):
    if mode in ("draft-results", "draft_results") and extract_done(folder, mode):
        return
    code, last = run_cmd([sys.executable, "extract.py", folder], log_path, 60 * 60)
    if code != 0 or not extract_done(folder, mode):
        raise WeekStopped(f"extract.py failed: {last}")


def step_load(folder, replace=False, mode="weekly"):
    if mode in ("draft-results", "draft_results"):
        from pipeline.draft_results import load_draft_results_csv, load_draft_results_json
        import psycopg
        from pipeline.load_capture import DSN
        csv_path = os.path.join(folder, "draft_results.csv")
        json_path = os.path.join(folder, "draft_results.json")
        season, _ = league_state()
        if os.path.exists(csv_path):
            report = load_draft_results_csv(csv_path, season)
        elif os.path.exists(json_path):
            report = load_draft_results_json(json_path, season)
        else:
            raise WeekStopped(f"No draft results found in {folder} (expected draft_results.csv or draft_results.json)")
        name = os.path.basename(os.path.normpath(folder))
        with psycopg.connect(DSN) as conn:
            conn.execute(
                "INSERT INTO captures (folder, mode, season, week, status, loaded_at) "
                "VALUES (%s, %s, %s, %s, 'loaded', NOW()) "
                "ON CONFLICT (folder) DO UPDATE SET status = 'loaded', loaded_at = NOW()",
                (name, "draft-results", season, 24),
            )
        return {"mode": "draft-results", "week": 24, "report": report}
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


def step_gm(season, next_stage=False):
    """GM trade proposals (team management step 3, engines/gm_trade_engine.py) for league_state's week, after the
    engines and before articles. After the Super Bowl scan the league is in the offseason: stage 1 (post-Super
    Bowl) runs here once; next_stage=True (the Next offseason stage run, right after its transactions load) runs
    the stage after the last one. Optional: a failure only logs a warning and the run goes on. Backfills Gemini
    bios for GMs still on the template first. Returns the number of new proposals."""
    try:
        import psycopg
        from pipeline.load_capture import DSN
        from engines.gm_llm import flush, make_llm
        from engines.gm_personas import backfill_bios
        from engines.gm_trade_engine import run_week
        llm = make_llm("gm_rationale")
        with psycopg.connect(DSN) as conn:
            cur = conn.cursor()
            cur.execute("SELECT to_regclass('gm_trade_proposals') IS NOT NULL")
            if not cur.fetchone()[0]:
                log("  GM trade proposals skipped: the GM agents migration isn't applied")
                return 0
            from engines.gm_trade_engine import OFFSEASON_STAGES, league_window, next_stage as following, read_stage
            _, week, window = league_window(cur)
            stage = None
            if next_stage:
                if window != "OFFSEASON":
                    log(f"  GM offseason stage skipped: the league isn't in the offseason (week {week})")
                    return 0
                stage = following(read_stage(cur, season))
                if stage is None:
                    log(f"  GM offseason stage skipped: all {len(OFFSEASON_STAGES)} stages have run")
                    return 0
                log(f"  GM offseason stage {stage}: {OFFSEASON_STAGES[stage - 1]}")
            elif window == "OFFSEASON":
                done = read_stage(cur, season)
                if done:
                    log(f"  GM trade proposals: offseason stage {done} already ran; run the next stage from #debug")
                    return 0
                stage = 1                                     # right after the Super Bowl scan: post-Super Bowl
            bio_llm = make_llm("gm_persona")
            if bio_llm is not None:
                n = backfill_bios(cur, season, bio_llm, log=log)
                if n:
                    log(f"  GM bios: {n} written by Gemini")
                flush(bio_llm)
            rep = run_week(cur, season, week, llm=llm, log=log, stage=stage)
            conn.commit()
        flush(llm)
        n = len(rep["proposals"])
        log(f"  GM trade proposals: {n} new, {len(rep['expired'])} expired, {len(rep['rechecked'])} no longer legal")
        if n:
            when = f"offseason stage {stage}" if stage else f"week {week}"
            notify(f"{n} new trade proposal{'s' if n != 1 else ''} for JR ({when}). Review them on #debug > "
                   "Front office.", title="Mu: trade proposals", tags=("handshake",))
        return n
    except Exception as e:
        traceback.print_exc()
        log(f"  [WARN] GM trade proposals skipped: {e!r}")
        return 0


def step_scouting(season):
    """Scouting (team management step 4, engines/scouting_engine.py), Step 7c: the draft class's consensus board,
    events and team boards for league_state's step (the week about to be played, or 23 + the GM offseason stage
    just run), catching up any missing steps in order. Optional: skipped without the migration or before the class
    is initialized, and a failure only logs a warning. Returns the steps stored."""
    try:
        import psycopg
        from pipeline.load_capture import DSN
        from engines import scouting_engine as se
        from engines.gm_llm import flush, make_llm
        llm = make_llm("scouting_notes")
        with psycopg.connect(DSN) as conn:
            cur = conn.cursor()
            if not se.installed(cur):
                log("  Scouting skipped: the scouting migration isn't applied")
                return []
            s, step = se.current_step(cur)
            if step is None:
                log("  Scouting skipped: the draft has been held")
                return []
            done = se.catch_up(cur, s + 1, step, log=log, extras=se.default_extras, llm=llm)
            conn.commit()
        flush(llm)
        log(f"  Scouting {s + 1}: steps stored {done or 'none (up to date or class not initialized)'}")
        return done
    except Exception as e:
        traceback.print_exc()
        log(f"  [WARN] Scouting skipped: {e!r}")
        return []


def step_coach_hot_seat(season):
    """engines/coaching_carousel.py (Step 7d): whatever of the coaching carousel is ready, once each: Black Monday
    head-coach firings (the Week 18 run, before the Week 19 articles), a playoff team's coach once it is out, the
    coordinator review of every team whose coach kept his job, and the hiring cycle once the Super Bowl is complete.
    Runs for league_state's season and the one before it (the league may already have rolled over). JR mirrors
    head-coach changes in Madden: one notice lists the coaches to fire, another the openings to fill (hire any coach;
    Mu's hire stands for him). Skipped without the migrations; a failure only logs a warning."""
    try:
        import psycopg
        from pipeline.load_capture import DSN
        from engines import coaching_carousel as cc
        reports = []
        with psycopg.connect(DSN) as conn:
            cur = conn.cursor()
            if not cc.installed(cur):
                log("  Coaching carousel skipped: the coach hot seat / coaching carousel migrations aren't applied")
                return None
            for s in (season - 1, season):
                reports.append(cc.run(cur, s, log=log))
            conn.commit()
        for rep in reports:
            s, hs = rep["season"], rep["hot_seat"]
            hires = rep["hires"]
            log(f"  Coaching carousel {s}: hot seat {hs['status']}, {len(hs['fired'])} head coach(es) and "
                f"{len(rep['coordinators_fired'])} coordinator(s) fired"
                + (f", {len(hires['head_coaches'])} head coach(es) hired" if hires else ""))
            if hs["fired"]:
                who = "; ".join(f"{f['team']}: {f['coach']}" for f in hs["fired"])
                notify(f"Fire these head coaches in Madden: {who}. Mu names their replacements after the Super Bowl.",
                       title="Mu: coaches fired", tags=("door",))
            if hires and hires["head_coaches"]:
                who = "; ".join(f"{h['team']}: {h['coach']} ({h['slant'].lower()}, {h['from']})"
                                for h in hires["head_coaches"])
                notify(f"Hire a head coach in Madden for each opening (any coach; Mu's hire stands for him): {who}.",
                       title="Mu: coaches hired", tags=("handshake",))
            if hires and hires.get("jr_losses"):
                who = "; ".join(f"{x['team']} {x['role']} {x['lost']} left to coach {x['to']}, {x['promoted']} promoted"
                                for x in hires["jr_losses"])
                notify(f"Your coordinators were poached: {who}. Rename or replace the promotion by migration if you "
                       "want someone else.", title="Mu: coordinator poached", tags=("warning",))
        return reports
    except Exception as e:
        traceback.print_exc()
        log(f"  [WARN] Coaching carousel skipped: {e!r}")
        return None


def step_preseason_engines(season):
    """Preseason engines for start-of-season calibration (schedule, rosters, injuries loaded):
    1. Preseason Fantasy ADP (runs 100 simulated drafts, writes Week 1 ADP board).
    2. Scheduled Game Excitement Projections (pre-game E for all scheduled games).
    3. Week 1 Fantasy Projections (points, matchup stars, pos ranks).
    Then (optional, never stops the run) Napper week 1 AI lineups (engines/napper_weekly.py, no trades)."""
    try:
        from engines.fantasy_adp_engine import generate_preseason_adp
        from engines.excitement_projection import project_scheduled_games
        from engines.fantasy_projection_engine import project_weeks

        log("  Preseason engine 1/3: Simulated fantasy drafts & ADP board...")
        generate_preseason_adp(season=season, redraft=True)

        log("  Preseason engine 2/3: Projected game excitement (E)...")
        project_scheduled_games(season)

        log("  Preseason engine 3/3: Week 1 fantasy projections & matchup stars...")
        project_weeks(season, [1])
    except Exception as e:
        traceback.print_exc()
        raise WeekStopped(f"Preseason engines failed after the load: {e!r}")
    # Napper: week 1 AI lineups from the new projections (no trades before week 1). Optional, never stops the run.
    try:
        from engines.napper_weekly import run_napper_week
        log("  Napper: week 1 AI lineups...")
        run_napper_week(season, 1, trades=False, log=log)
    except Exception as e:
        log(f"  [WARN] Napper week 1 lineups skipped: {e!r}")
    # GM agents (team management step 3): the season rollover. Optional, never stops the run.
    step_gm_rollover(season)
    # Scouting (team management step 4): fo_quality from the drafts with real rookie ratings, the new class's rows.
    step_scouting_season(season)


def step_scouting_season(season):
    """Season scan, after the GM rollover (team management step 4): engines/fo_quality.recompute for the new season
    (this year's rookies scored at year 0, earlier classes a year on; JR's teams keep their q), then
    engines/dev_quality.recompute (player development from last season's card to this one; skipped without its
    columns), then
    scouting_engine init for the incoming class (season + 1) at the league's current step. Each part is optional:
    skipped without its migration, a failure only logs a warning."""
    out = {}
    try:
        import psycopg
        from pipeline.load_capture import DSN
        from engines import fo_quality, scouting_engine as se
        with psycopg.connect(DSN) as conn:
            cur = conn.cursor()
            cur.execute("SELECT to_regclass('gm_team_seasons') IS NOT NULL")
            if cur.fetchone()[0]:
                res = fo_quality.recompute(cur, season, log=lambda *a: None)
                out["fo_quality"] = {r["abbr"]: r["q"] for r in res.values() if not r["held"]}
                log(f"  fo_quality {season}: {len(out['fo_quality'])} teams rescored from their draft records")
                cur.execute("""SELECT 1 FROM information_schema.columns
                               WHERE table_name = 'gm_team_seasons' AND column_name = 'dev_quality'""")
                if cur.fetchone():
                    from engines import dev_quality
                    try:
                        with conn.transaction():        # a savepoint: a failure here never blocks scouting init
                            res = dev_quality.recompute(cur, season, log=lambda *a: None)
                        out["dev_quality"] = {r["abbr"]: r["D_star"] for r in res.values()}
                        log(f"  dev_quality {season}: {len(out['dev_quality'])} teams scored on player development")
                    except Exception as e:
                        log(f"  [WARN] dev_quality {season} skipped: {e!r}")
            if se.installed(cur):
                cur.execute("SELECT count(*) FROM draft_prospects WHERE draft_season = %s", (season + 1,))
                if cur.fetchone()[0]:
                    out["scouting_init"] = se.init(cur, season + 1, log=log)
                else:
                    log(f"  Scouting init skipped: no {season + 1} draft board loaded")
            conn.commit()
    except SystemExit as e:
        log(f"  [WARN] Season scouting step skipped: {e}")
    except Exception as e:
        traceback.print_exc()
        log(f"  [WARN] Season scouting step skipped: {e!r}")
    return out


def step_gm_rollover(season):
    """engines/gm_rollover.py for the new season: the automatic hot seat, gm_team_seasons rows with a preseason
    direction, fo_quality carried forward, picks seeded for the new third draft, the draft just held marked
    used. Skipped when the GM migration isn't applied; a failure only logs a warning."""
    try:
        import psycopg
        from pipeline.load_capture import DSN
        from engines.gm_llm import flush, make_llm
        from engines.gm_rollover import rollover
        with psycopg.connect(DSN) as conn:
            cur = conn.cursor()
            cur.execute("SELECT to_regclass('gm_team_seasons') IS NOT NULL")
            if not cur.fetchone()[0]:
                log("  GM rollover skipped: the GM agents migration isn't applied")
                return None
            llm = make_llm("gm_persona")
            rep = rollover(cur, season, llm, log=log)
            conn.commit()
        flush(llm)
        log(f"  GM rollover {season}: {len(rep['fired'])} GM(s) replaced, {rep['directions']} directions, "
            f"{rep['picks']} picks seeded, {rep['used']} picks marked used")
        return rep
    except Exception as e:
        traceback.print_exc()
        log(f"  [WARN] GM rollover skipped: {e!r}")
        return None


def step_rookie_headshots(season):
    """Generate broadcast media-day headshots for rookies drafted/entering in season."""
    from headshots.rookie_headshots import generate_rookie_headshots
    try:
        log("  Rookie headshots: generating media-day headshots for incoming rookie class...")
        report = generate_rookie_headshots(season=season, log_fn=log)
        log(f"  Rookie headshots done: {report.get('generated', 0)} generated, {report.get('already_had', 0)} pre-existing ({report.get('failed', 0)} failed)")
        if report.get("failed", 0) > 0 and report.get("generated", 0) == 0:
            raise RuntimeError(f"All {report.get('failed')} rookie headshot attempts failed: {report.get('errors')[:3]}")
    except Exception as e:
        traceback.print_exc()
        raise WeekStopped(f"Rookie headshots failed after the roster load: {e!r}")


def step_articles(season, week=None):
    from newsroom.weekly_run import run_weekly_swarm
    try:
        res = run_weekly_swarm(season=season, week=week)
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


def team_nicknames(abbrs):
    """['CAR', 'NO'] -> ['PANTHERS', 'SAINTS'] (teams.abbreviation -> upper(teams.name)). An unknown
    abbreviation raises WeekStopped before anything touches the console."""
    import psycopg
    from pipeline.load_capture import DSN
    with psycopg.connect(DSN) as conn:
        by_abbr = {a.upper(): n.upper() for a, n in conn.execute("SELECT abbreviation, name FROM teams").fetchall()}
    want = [a.strip().upper() for a in abbrs if a.strip()]
    bad = [a for a in want if a not in by_abbr]
    if bad:
        raise WeekStopped(f"Unknown team abbreviations {bad}; nothing was captured")
    return [by_abbr[a] for a in want]


def unfinished_contracts_run(targets=None, now=None):
    """The run folder a contracts run without --resume should continue: one whose capture was cut off (no
    .capture_done, so not a finished capture whose extract or load failed) from the last CONTINUE_WINDOW whose contracts/targets.txt is the same team set (targets: nicknames, None = all 32).
    The one with the most finished teams wins, then the newest. None when there is nothing to continue."""
    now = now or time.time()
    best = None
    for d in sorted(glob.glob(os.path.join(CAPTURES, "*", "contracts", "targets.txt"))):
        folder = os.path.dirname(os.path.dirname(d))
        touched = max(os.path.getmtime(p) for p in (folder, d, os.path.join(folder, "contracts", "done.txt"),
                                                    os.path.join(folder, "weekly_run.log")) if os.path.exists(p))
        if (folder_mode(folder) != "contracts" or now - touched > CONTINUE_WINDOW
                or capture_done(folder, "contracts")):
            continue
        saved = {t.strip().upper() for t in open(d) if t.strip()}
        if (targets is None and len(saved) != 32) or (targets is not None and saved != {t.upper() for t in targets}):
            continue
        try:
            if load_status(folder) == "loaded":
                continue
        except Exception:
            continue
        done_path = os.path.join(folder, "contracts", "done.txt")
        n_done = sum(1 for line in open(done_path) if line.strip()) if os.path.exists(done_path) else 0
        key = (n_done, touched)
        if best is None or key > best[0]:
            best = (key, os.path.basename(folder))
    return best[1] if best else None


def contracts_phase(folder, log_path, teams=None):
    """Capture -> extract -> load of a Team Salary run in its own folder (team management step 1), each step
    skipped when already done. teams: abbreviations for a re-scan (None = all 32). Takes no lock and doesn't
    connect: run(mode='contracts') holds the lock. Nothing calls it automatically for League Transactions
    (JR, 2026-10-19: no automatic re-scans). Returns the load report (None when the folder was already loaded)."""
    os.makedirs(folder, exist_ok=True)
    report = None
    if not capture_done(folder, "contracts"):
        nicknames = team_nicknames(teams) if teams else None
        log(f"Contracts capture ({', '.join(nicknames) if nicknames else 'all 32 teams'})")
        step_capture(folder, log_path, "contracts", teams=nicknames)
    if not extract_done(folder, "contracts"):
        log("Contracts extract")
        step_extract(folder, log_path, "contracts")
    if load_status(folder) != "loaded":
        log("Contracts load")
        report = step_load(folder, mode="contracts")
        log(f"Loaded {report.get('contracts')} contracts for {report.get('teams')} teams "
            f"({report.get('superseded')} superseded; cap warnings: {len(report.get('cap_warnings') or [])})")
    return report


# ---------------------------------------------------------- transactions ----
def since_heading(season, weekly=True):
    """The newest League Transactions heading loaded for the season (by heading order), for --since. A season
    with nothing loaded yet gives '<season> SEASON - WEEK 1' (JR, 2026-10-19: the Week 1 roster already has every
    preseason move, and the preseason cut-downs to 53 are over half the list, so neither the weekly run nor
    --mode transactions reads them). `weekly` is kept for callers; both modes behave the same. None when the
    transactions table doesn't exist."""
    import psycopg
    from pipeline.load_capture import DSN
    from pipeline.transactions_parse import heading_ordinal
    with psycopg.connect(DSN) as conn:
        if not conn.execute("SELECT to_regclass('transactions') IS NOT NULL").fetchone()[0]:
            return None
        heads = [r[0] for r in conn.execute("SELECT DISTINCT txn_date FROM transactions WHERE season = %s", (season,))]
    heads = [h for h in heads if heading_ordinal(h)]
    if heads:
        return max(heads, key=heading_ordinal)
    return f"{season} SEASON - WEEK 1"


# ----------------------------------------------------------------- run ----
def set_league_state(season, week):
    import psycopg
    from pipeline.load_capture import DSN
    with psycopg.connect(DSN) as conn:
        conn.execute("UPDATE league_state SET current_season = %s, current_week = %s WHERE id = 1", (season, week))


def set_current_week(week):
    import psycopg
    from pipeline.load_capture import DSN
    with psycopg.connect(DSN) as conn:
        conn.execute("UPDATE league_state SET current_week = %s WHERE id = 1", (week,))


def bye_run(articles=True):
    """League week 22 (Pro Bowl week): nothing to capture. Engines, the Week 23 slate, then current_week 23.
    Returns 0 when it went through, 1 when it stopped (notice sent)."""
    try:
        season, _ = league_state()
        log(f"Week 22 has no games: articles-only run (season {season})")
        log("Step 7: engines")
        step_engines(season)
        if not articles:
            log("Articles not requested; league stays at week 22")
            return 0
        log("Step 8: articles (Week 23 slate)")
        res = step_articles(season, week=FINAL_WEEK)
        if res.get("status") not in ("published", "skipped_exists"):
            raise WeekStopped(f"Articles didn't publish ({res})")
        set_current_week(FINAL_WEEK)
        notify(f"Week 23 slate: {res.get('status')}. League now at week 23: play the Super Bowl, then Scan week.",
               title="Mu: week done", tags=("white_check_mark",))
        return 0
    except WeekStopped as e:
        log(f"WEEK STOPPED: {e}")
        notify(f"{e}\nRun Scan week again once it's fixed (nothing to capture this week).",
               title="Mu: week stopped", priority="urgent", tags=("warning",))
        return 1


def run(resume=None, articles=True, connect=True, mode="weekly", season=None, teams=None, gm_stage=False):
    """One weekly run (mode 'weekly'; 'playoff' for a postseason week, chosen automatically; or 'games' for
    every game of last week, never with articles; 'season' for start-of-season calibration; 'contracts' for
    Team Salary, teams = abbreviations for a re-scan). Returns 0 when the week went through, 1 when it stopped."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if teams and mode != "contracts":
        raise ValueError("teams is only for mode 'contracts'")
    if mode == "weekly" and resume and os.path.isdir(os.path.join(CAPTURES, resume)):
        detected = folder_mode(os.path.join(CAPTURES, resume))
        if detected != "weekly":
            mode = detected
    if mode == "season":
        try:
            curr_s, curr_w = league_state()
            target_season = season or (curr_s + 1 if curr_w != 1 else curr_s)
            if target_season != curr_s or curr_w != 1:
                set_league_state(target_season, 1)
                log(f"Season scan: rolled over league_state from Season {curr_s} Week {curr_w} to Season {target_season}, Week 1")
        except Exception as e:
            log(f"Warning: could not inspect/update league_state: {e}")
    current = None
    if mode in ("weekly", "playoff", "postgame"):
        try:
            current = league_state()[1]
        except Exception:
            current = None
    if mode == "weekly":
        mode = playoff_or_weekly(resume)
    articles = articles and mode in ("weekly", "playoff", "postgame", "season")
    week_label = SUPER_BOWL_LABEL if mode in ("playoff", "postgame") and current is not None and current >= FINAL_WEEK else None
    if week_label == SUPER_BOWL_LABEL or mode == "postgame":
        what = "Super Bowl postgame capture"
    else:
        what = {"games": "Game scan", "playoff": "Playoff capture", "postgame": "Super Bowl postgame capture", "roster": "Roster scan", "prospects": "Draft prospect scan", "season": "Season scan", "draft-results": "Draft results scan", "team-overalls": "Team overalls scan", "contracts": "Contract scan", "transactions": "League transactions scan"}.get(mode, "Weekly capture")
    os.makedirs(CAPTURES, exist_ok=True)
    lock = open(LOCK_PATH, "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log("Another weekly run is in progress; not starting a second one")
        return 1
    if current == PRO_BOWL_WEEK and mode == "playoff" and not resume:
        try:
            return bye_run(articles)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
    if mode == "contracts" and not resume:
        # a Contract scan started again (by hand or from #debug) continues the unfinished one instead of
        # starting over (Oct 20: a run killed after 24 teams was restarted from team 1)
        try:
            resume = unfinished_contracts_run(team_nicknames(teams) if teams else None)
        except Exception:
            resume = None
        if resume:
            log(f"Continuing the unfinished contract scan in captures/{resume}")
    name = resume or datetime.datetime.now().strftime("%Y-%m-%d_%H%M")
    folder = os.path.join(CAPTURES, name)
    if resume and not os.path.isdir(folder):
        log(f"No capture folder {folder}")
        return 1
    os.makedirs(folder, exist_ok=True)
    log_path = os.path.join(folder, "weekly_run.log")
    started = time.time()
    try:
        if mode == "contracts":
            if teams:
                team_nicknames(teams)            # an unknown abbreviation stops before the console is touched
            if not capture_done(folder, mode):
                notify(f"{what} starting ({name}). Remote Play is taking over the console.",
                       title="Mu: capture starting", tags=("video_game",))
                if connect:
                    log("Step 2: connect")
                    step_connect(log_path)
            report = contracts_phase(folder, log_path, teams)
            log("Contracts loaded. Skipping weekly engines and articles.")
            log("Step 9: shrink")
            before, after = shrink(folder)
            log(f"Capture folder {before / 1e6:.0f} MB -> {after / 1e6:.0f} MB")
            mins = (time.time() - started) / 60
            n = f"{report.get('contracts')} contracts, {report.get('teams')} teams" if report else "already loaded"
            notify(f"{what} done in {mins:.0f} min ({n}). Turn your controller back on and restart the broadcast.",
                   title="Mu: contracts scan done", priority="high", tags=("white_check_mark",))
            return 0
        if not capture_done(folder, mode):
            notify(f"{what} starting ({name}). Remote Play is taking over the console.",
                   title="Mu: capture starting", tags=("video_game",))
            if connect:
                log("Step 2: connect")
                step_connect(log_path)
            since = None
            if mode in ("weekly", "playoff", "transactions") and week_label != SUPER_BOWL_LABEL:
                try:
                    since = since_heading(league_state()[0], weekly=(mode != "transactions"))
                except Exception as e:
                    log(f"[WARN] No League Transactions cutoff ({e}); the scan reads the whole list")
            log("Step 3: capture" + (f" (League Transactions since {since})" if since else ""))
            step_capture(folder, log_path, mode, week_label, **({"since": since} if since else {}))
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
            report = step_load(folder, replace=(mode == "games"), mode=mode)
            log(f"Loaded: {report.get('mode')} week {report.get('week')}")
        else:
            log("Already loaded; skipping the load")
        if mode == "transactions":
            log("League transactions loaded. Skipping weekly engines and articles.")
            if gm_stage:                                   # Next offseason stage: the moves are in, now the GMs
                log("Step 7b: GM trade proposals (next offseason stage)")
                step_gm(league_state()[0], next_stage=True)
                log("Step 7c: scouting")
                step_scouting(league_state()[0])
            log("Step 9: shrink")
            before, after = shrink(folder)
            log(f"Capture folder {before / 1e6:.0f} MB -> {after / 1e6:.0f} MB")
            notify(f"{what} loaded.", title="Mu: transactions scan done", tags=("white_check_mark",))
            return 0
        if mode == "season":
            season, _ = league_state()
            log(f"Step 6b: rookie headshots (season {season})")
            step_rookie_headshots(season)
            log(f"Step 7: preseason engines (season {season})")
            step_preseason_engines(season)
            published = None
            if articles:
                log("Step 8: articles (Week 1 preseason slate)")
                res = step_articles(season, week=1)
                published = f"slate for Week 1: {res.get('status')}"
                if res.get("status") not in ("published", "skipped_exists", "season_complete"):
                    raise WeekStopped(f"Preseason articles didn't publish ({res})")
            log("Step 9: shrink")
            before, after = shrink(folder)
            log(f"Capture folder {before / 1e6:.0f} MB -> {after / 1e6:.0f} MB")
            notify(f"Season setup, preseason engines, and Week 1 projections loaded for Season {season}. "
                   f"{published or 'Articles not run.'}",
                   title="Mu: season scan done", tags=("white_check_mark",))
            return 0
        if mode in ("roster", "prospects", "draft-results"):
            season, _ = league_state()
            if mode == "roster":
                log(f"Step 6b: rookie headshots (season {season})")
                step_rookie_headshots(season)
            what_done = "Rosters" if mode == "roster" else (
                "Draft prospects" if mode == "prospects" else "Draft results"
            )
            log(f"{what_done} loaded. Skipping weekly engines and articles.")
            log("Step 9: shrink")
            before, after = shrink(folder)
            log(f"Capture folder {before / 1e6:.0f} MB -> {after / 1e6:.0f} MB")
            notify(f"{what_done} loaded for Season {season}.",
                   title=f"Mu: {mode} scan done", tags=("white_check_mark",))
            return 0
        season, current_week = league_state()
        log(f"Step 7: backfill + engines (season {season})")
        step_engines(season)
        if mode in ("weekly", "playoff"):
            log("Step 7b: GM trade proposals")
            step_gm(season)
            log("Step 7c: scouting")
            step_scouting(season)
        if mode in ("weekly", "playoff", "postgame"):
            log("Step 7d: coaching carousel")
            step_coach_hot_seat(season)
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
        mode_arg = f" --mode {mode}" if mode != "weekly" else ""
        notify(f"{e}\nResume by hand: python3 -m pipeline.weekly_capture --resume {name}{mode_arg}",
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
                    help="games: every game of last week (box scores + player stats), loaded with --replace; "
                         "playoff: a postseason week (chosen automatically from league current week 19 on; week 23 is postgame); "
                         "postgame: capture Super Bowl directly from the in-game postgame screen; "
                         "season: start-of-season cadence scan (schedule, rosters, then draft board); "
                         "roster: season cadence scan of all 32 team rosters; "
                         "prospects: draft board scan of college prospects; "
                         "draft-results: capture and load actual draft results into draft_prospects; "
                         "contracts: Team Salary for all 32 teams (or --teams), extracted and loaded; "
                         "transactions: League Transactions alone, extracted and loaded")
    ap.add_argument("--teams", metavar="ABBRS", help="--mode contracts: re-scan only these teams, e.g. CAR,NO")
    ap.add_argument("--no-connect", action="store_true",
                    help="skip the connect step (chiaki-ng is already streaming the franchise menus)")
    ap.add_argument("--season", type=int, help="explicitly target season for the run")
    ap.add_argument("--gm-next-stage", action="store_true",
                    help="--mode transactions in the offseason: then run the GM agents' next offseason stage")
    a = ap.parse_args()
    teams = [t for t in (a.teams or "").split(",") if t.strip()] or None
    if teams and a.mode != "contracts":
        ap.error("--teams needs --mode contracts")
    if a.gm_next_stage and a.mode != "transactions":
        ap.error("--gm-next-stage needs --mode transactions")
    sys.exit(run(a.resume, articles=not a.no_articles, connect=not a.no_connect, mode=a.mode, season=a.season,
                 teams=teams, gm_stage=a.gm_next_stage))


if __name__ == "__main__":
    main()
