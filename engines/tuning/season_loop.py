"""
One simulated season on the tuning league (CLOSE_OUT_CHECKLIST P2-05, P2-10; docs/reference/TUNING_LEAGUE.md).

  python3 -m engines.tuning.season_loop --run p2c [--from CAREER-MU2026] [--resume] [--no-offseason]
                                        [--fast-season] [--max-weeks 28] [--max-stops 14]
                                        [--targets PATH] [--dry-run]

One command per simulated season. It drives the same CLIs the live franchise uses, every Madden-touching step
through a subprocess (`runner` replaces subprocess.run in tests):

  1. tools.madden_pilot clone-save --from <base> --to CAREER-TUNE-<run>   (fresh copy, capacity-pruned)
  2. the season walk (P2-10), one stop at a time, per stop: tools.madden_pilot advance (exactly one stop,
     then quit) -> pipeline.watch_save --once into madden_tune_<run>, which runs the weekly engines and the
     league office and writes Mu's decisions into the save with the franchise closed -> the next week.
     The stops are the 18 regular-season weeks and the playoff rounds, so the weekly league office plans a
     REGULAR_WEEK run per week and a PLAYOFF_WEEK run per round, the way the live league does. The walk
     ends when an advance lands on the offseason's first stop (STAFF); the caller snapshots the season
     and then ingests that stop, which is the league office's OFFSEASON_START write.
     --fast-season replaces steps 2-3 with the one-shot `sim-season` (regular season, then the postseason)
     for stat-only work: no weekly league office, no in-season trades, depth charts or checkpoints.
  3. the stage loop (P2-03), per stop: advance one stop, ingest it with the league office writing, and repeat
     through the eight offseason stops, the preseason weeks and the cut day. Madden never sims through the
     offseason (TUNING_LEAGUE.md §6).
  4. a season report: a pc_save_reader snapshot of the save at the season's end scored against the real-NFL
     targets (engines/tuning/compare.py), written to data/tuning/seasons/<run>/season_<year>.md and .json,
     then the decision comparison (engines/tuning/decision_compare.py, P3-07) beside it as
     decisions_<year>.md and .json, read-only on madden_tune_<run> (`--no-decisions`,
     `--decision-targets PATH`).

The tuning database is created on the first ingest (--auto-init) and is disposable; a run name is all the state.
`data/tuning/seasons/<run>/progress.json` records how far the run got after every step, so `--resume` restarts
mid-season at the week the last run stopped at.
Madden must be open for steps 1-3: this module asks for the PC through the pilot's own rules (CAREER-TUNE-* only,
a running watcher refuses, quitting writes the autosave).
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
SEASONS_DIR = os.path.join(ROOT, "data", "tuning", "seasons")
BASE_SAVE = "CAREER-MU2026"
TUNE_PREFIX = "CAREER-TUNE-"
DB_PREFIX = "madden_tune_"
SAVES_DIR = os.environ.get("MADDEN_SAVES_DIR", "/mnt/c/Users/jrere/Documents/Madden NFL 27/saves")
READER = os.path.join(ROOT, "pipeline", "save_reader", "pc_save_reader.mjs")
DEFAULT_DSN = "host=localhost dbname={db} user=postgres password=postgres"
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
# The phases a week-to-week season is still running in: a regular-season week or a playoff round. The
# advance reports the stop it landed on; anything else is the offseason (STAFF first).
IN_SEASON_PHASES = ("WEEK", "WILDCARD", "DIVISIONAL", "CONFERENCE", "SUPER BOWL", "PRO BOWL")
MAX_SEASON_STOPS = 28          # 18 regular weeks + up to 5 playoff stops, with slack
# Madden27.exe may still run between pilots; the league office writes the save with the franchise closed.
INGEST_ENV = {"LEAGUE_OFFICE_FRANCHISE_CLOSED": "1"}


class Refused(RuntimeError):
    """The loop won't start (bad run name, bad base save, a missing pilot)."""


class StepFailed(RuntimeError):
    """A CLI step of the season failed."""


def db_for(run):
    return DB_PREFIX + re.sub(r"[^a-z0-9_]+", "_", run.lower())


def db_exists(db):
    """True/False when the server can be asked, None when it can't (the ingest then reports)."""
    try:
        import psycopg
    except ImportError:                     # pragma: no cover - the ingest would fail first
        return None
    try:
        with psycopg.connect(DEFAULT_DSN.format(db="postgres")) as conn:
            return conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,)).fetchone() is not None
    except Exception:                       # noqa: BLE001 - a resume check is best effort
        return None


def dsn_for(run):
    return DEFAULT_DSN.format(db=db_for(run))


def save_for(run):
    return TUNE_PREFIX + run.upper()          # the saves folder is upper-case: CAREER-TUNE-P2C


def in_season(phase):
    """True for a regular-season week or a playoff round — the stops the week walk ingests itself."""
    return str(phase or "").upper() in IN_SEASON_PHASES


def _run_cmd(runner, cmd, step, env=None, cwd=ROOT):
    proc = runner(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        tail = ((proc.stdout or "") + (proc.stderr or ""))[-1500:]
        raise StepFailed(f"{step} failed (exit {proc.returncode}): {' '.join(map(str, cmd))}\n{tail}")
    return proc


def _ingest_env(environ=None):
    env = dict(environ if environ is not None else os.environ)
    env.update(INGEST_ENV)
    return env


# ---------- the run's record ----------

class Progress:
    """How far the run got, written to <run_dir>/progress.json after every step.

    `season_complete` is set only once the season's last stop has been ingested, and a `--resume` reads it to
    know whether the week walk still has stops to run: the database cannot say, because the league's week
    bookkeeping is games-week only and an offseason ingest keeps the stored week (JR, 2026-11-10)."""

    FIELDS = ("run", "save", "db", "steps", "stops", "season_complete")

    def __init__(self, run_dir, name, save, db, *, resume=False):
        self.path = os.path.join(run_dir, "progress.json")
        fresh = {"run": name, "save": save, "db": db, "steps": [], "stops": [],
                 "season_complete": False, "started": _now(), "updated": None}
        loaded = self.read() if resume else None
        self.found = loaded is not None
        self.data = loaded or fresh
        for field in self.FIELDS:
            self.data.setdefault(field, fresh[field])
        if self.found or not resume:
            self.flush()

    def read(self):
        try:
            with open(self.path) as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def add(self, step):
        self.data["steps"].append(step)
        self.flush()

    def stop(self, n, res):
        self.data["stops"].append({"n": n, "status": res.get("status"), "from": res.get("from"),
                                   "to": res.get("to"), "minutes": res.get("minutes"),
                                   "advance_s": res.get("advance_s"), "ingest_s": res.get("ingest_s")})
        self.flush()

    def season_done(self):
        self.data["season_complete"] = True
        self.flush()

    def flush(self):
        self.data["updated"] = _now()
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w") as fh:
            json.dump(self.data, fh, indent=1, default=str)


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


# ---------- the steps ----------

def clone(run, base_save=BASE_SAVE, *, runner=None, python=None):
    """A fresh pruned CAREER-TUNE-<run> from the baseline (re-cloning is the 'fresh save' reset)."""
    runner, python = runner or subprocess.run, python or sys.executable
    return _run_cmd(runner, [python, "-m", "tools.madden_pilot", "clone-save", "--from", base_save,
                             "--to", save_for(run)], "clone save")


def release(run, *, runner=None, python=None, run_dir=None):
    """Leave whatever franchise the game is inside, so the next step starts on CURRENT FRANCHISES.

    A pilot run that stops (an unknown screen, a timeout) leaves Madden inside the franchise, and
    the next `sim-season` cannot load from there: it is already in one and refuses to quit a
    franchise it did not load. Best effort — a stopped quit is ignored here and the step that
    follows reports whatever is really wrong. No-op (one look) when already on the list."""
    runner, python = runner or subprocess.run, python or sys.executable
    cmd = [python, "-m", "tools.madden_pilot", "quit", "--franchise", "TUNE-" + run]
    if run_dir:
        cmd += ["--run-dir", str(run_dir)]
    proc = runner(cmd, cwd=ROOT, capture_output=True, text=True, env=None)
    try:
        res = json.loads((proc.stdout or "")[proc.stdout.index("{"):])
    except ValueError:
        return {"status": "stopped", "why": "the game is not on a screen the pilot can leave from"}
    return res


def sim(run, to, *, in_place=False, quit=True, runner=None, python=None, run_dir=None):
    """Sim forward with the pilot until `to` ('regular-season-end' or 'playoffs-end').

    The regular season is simmed with `quit=False` so the postseason run can continue in the
    same loaded franchise (`in_place`), which saves a full load."""
    runner, python = runner or subprocess.run, python or sys.executable
    cmd = [python, "-m", "tools.madden_pilot", "sim-season", "--save", save_for(run), "--to", to]
    if in_place:
        cmd += ["--franchise", "TUNE-" + run]
    if not quit:
        cmd.append("--no-quit")
    if run_dir:
        cmd += ["--run-dir", str(run_dir)]
    return _run_cmd(runner, cmd, f"sim {to}")


def advance(run, *, runner=None, python=None, run_dir=None):
    """Advance exactly one stop and quit to the franchise list; returns the pilot's result dict.

    The pilot exits 1 on a stop (an uncatalogued screen) and 2 on an unknown; those still carry
    a result and a stop.json with the screenshot, so the loop records them and ends instead of
    failing — an unknown screen is exactly what the catalog has to be extended with next."""
    runner, python = runner or subprocess.run, python or sys.executable
    cmd = [python, "-m", "tools.madden_pilot", "advance", "--save", save_for(run)]
    if run_dir:
        cmd += ["--run-dir", str(run_dir)]
    proc = runner(cmd, cwd=ROOT, capture_output=True, text=True, env=None)
    text = proc.stdout or ""
    try:
        res = json.loads(text[text.index("{"):])
    except ValueError:
        tail = (text + (proc.stderr or ""))[-1500:]
        raise StepFailed(f"advance failed (exit {proc.returncode}): {' '.join(map(str, cmd))}\n{tail}")
    res["returncode"] = proc.returncode
    return res


def ingest(run, *, runner=None, python=None, environ=None, auto_init=True, saves_dir=None):
    """Ingest the clone into madden_tune_<run>: weekly engines, league office, no articles, no freeze."""
    runner, python = runner or subprocess.run, python or sys.executable
    save_path = os.path.join(saves_dir or SAVES_DIR, save_for(run))
    cmd = [python, "-m", "pipeline.watch_save", save_path, "--once", "--dsn", dsn_for(run),
           "--no-articles", "--no-freeze"]
    if auto_init:
        cmd.append("--auto-init")
    return _run_cmd(runner, cmd, f"ingest into {db_for(run)}", env=_ingest_env(environ))


def snapshot(run, out_path, *, runner=None, saves_dir=None):
    """pc_save_reader.mjs on the clone, for the report."""
    runner = runner or subprocess.run
    save_path = os.path.join(saves_dir or SAVES_DIR, save_for(run))
    return _run_cmd(runner, ["node", READER, save_path, "--out", str(out_path)], "save reader")


# ---------- the walks ----------

def walk_season(run, *, runner=None, python=None, max_weeks=MAX_SEASON_STOPS, work_dir=None, environ=None,
                saves_dir=None, progress=None):
    """The week-by-week season (P2-10): one advance and one ingest per stop, regular season AND playoffs.

    Each stop is one `tools.madden_pilot advance` (it quits to the franchise list) followed by one ingest of
    that save, which runs the weekly engines and the league office and writes Mu's decisions into the save
    with the franchise closed. The stop is the advance's own `to` header: `WEEK` + a week number for the
    regular season, a round name for the playoffs.

    The walk ends after the advance that leaves the season (it lands on the offseason's first stop, STAFF).
    That last stop is NOT ingested here: the caller snapshots the season first and ingests it afterwards, so
    the report is taken while the save still holds the season's games (Madden rolls the per-game tables over
    at the new league year).

    Returns the stop records, each with its own step times (`advance_s`, `ingest_s`). The last one says
    whether the season reached its end — `status` "ok" and a `to` outside the season — or the pilot stopped
    on an uncatalogued screen, whose screenshot is in that stop's run dir. `max_weeks` is a safety cap and a
    deliberate partial run (`--max-weeks 3` for a live proof): it ends the walk like a stop, not an error,
    and `--resume` carries on from the record."""
    runner, python = runner or subprocess.run, python or sys.executable
    stops = []
    for n in range(1, max_weeks + 1):
        run_dir = os.path.join(work_dir, f"week_{n:02d}") if work_dir else None
        release(run, runner=runner, python=python,
                run_dir=os.path.join(run_dir, "release") if run_dir else None)
        t0 = time.time()
        res = advance(run, runner=runner, python=python, run_dir=run_dir)
        res["advance_s"] = round(time.time() - t0, 1)
        stops.append(res)
        if res.get("status") != "ok":
            if progress is not None:
                progress.stop(n, res)
            break
        if not in_season((res.get("to") or {}).get("phase")):
            if progress is not None:
                progress.stop(n, res)                    # where the season ended, for the next resume
            break                                        # the season is over: the caller ingests this stop
        t1 = time.time()
        ingest(run, runner=runner, python=python, auto_init=False, environ=environ, saves_dir=saves_dir)
        res["ingest_s"] = round(time.time() - t1, 1)
        if progress is not None:
            progress.stop(n, res)
    return stops


def season_finished(stops):
    """True when the last advance landed outside the season (the caller may snapshot and let Mu write)."""
    last = stops[-1] if stops else None
    return bool(last and last.get("status") == "ok"
                and not in_season((last.get("to") or {}).get("phase")))


def walk_offseason(run, *, runner=None, python=None, max_stops=14, work_dir=None, environ=None,
                   saves_dir=None):
    """The P2-03 stage loop: advance one stop, ingest it, repeat until the next regular season.

    The stops are the eight offseason stages, the preseason weeks and the cut day; the loop ends
    when an advance lands on a regular-season week (2027 week 1). The preseason weeks carry no
    phase word of their own on the hub — it reads "2027. WEEK 1", the same as a regular-season
    week — so the stop comes from the save (`PRESEASON_<n>`)."""
    stops = []
    for n in range(1, max_stops + 1):
        run_dir = os.path.join(work_dir, f"stop_{n:02d}") if work_dir else None
        release(run, runner=runner, python=python, run_dir=os.path.join(run_dir, "release") if run_dir else None)
        res = advance(run, runner=runner, python=python, run_dir=run_dir)
        stops.append(res)
        if res.get("status") != "ok":
            break
        ingest(run, runner=runner, python=python, auto_init=n == 1, environ=environ, saves_dir=saves_dir)
        after = res.get("to") or {}
        if str(after.get("phase", "")).upper() == "WEEK":       # the next regular season has begun
            break
    return stops


def _default_targets():
    from engines.tuning import targets as T
    return T.default_path()


def report(snapshot_path, out_dir, *, targets_path=None):
    """Score the season against the real-NFL targets and write <out_dir>/season_<year>.md and .json."""
    from engines.tuning import compare as C
    from engines.tuning import targets as T
    targets_path = targets_path or T.default_path()
    with open(snapshot_path) as fh:
        snap = json.load(fh)
    with open(targets_path) as fh:
        targets = json.load(fh)
    rep = C.compare(C.season_from_snapshot(snap), targets)
    season = rep.meta.get("season")
    os.makedirs(out_dir, exist_ok=True)
    md_path = os.path.join(out_dir, f"season_{season}.md")
    if rep.no_games and os.path.exists(md_path):
        # a resumed run that starts after the season rolled over cannot see its games; keep the
        # report the run that actually simmed the season wrote rather than replacing it with an
        # empty one (2026-11-10)
        return {"season": season, "score": None, "report": md_path,
                "json": os.path.join(out_dir, f"season_{season}.json"), "inside": 0, "stats": 0,
                "kept_existing": True}
    with open(md_path, "w") as fh:
        fh.write(C.format_report(rep))
    json_path = os.path.join(out_dir, f"season_{season}.json")
    with open(json_path, "w") as fh:
        json.dump({"season": season, "score": rep.score, "meta": rep.meta,
                   "stats": [s.__dict__ for s in rep.stats], "missing": rep.missing}, fh, indent=1)
    return {"season": season, "score": rep.score, "report": md_path, "json": json_path,
            "inside": sum(1 for s in rep.stats if s.score == 0), "stats": len(rep.stats)}


def decision_report(run, season, out_dir, *, targets_path=None, connect=None):
    """Write the P3-07 decision comparison beside the stat report (decision_compare.py).

    Read-only on madden_tune_<run>. A missing, empty, partial or malformed decision-targets file, or a
    database the report cannot read, is reported as skipped: the season and its stat report are already
    written, so this step never turns a finished season into a failure."""
    from engines.tuning import decision_compare as DC
    try:
        out = DC.run(dsn_for(run), season, targets_path=targets_path, out_dir=out_dir, connect=connect)
    except FileNotFoundError as e:
        return {"skipped": str(e)}
    except (ValueError, TypeError, KeyError) as e:
        # JSONDecodeError is a ValueError; an empty or partial targets file raises TypeError/KeyError
        return {"skipped": f"the decision targets at {targets_path or 'the default path'} are unreadable: {e}"}
    except Exception as e:                                  # noqa: BLE001 - see the docstring
        try:
            import psycopg
        except ImportError:
            raise
        if isinstance(e, psycopg.Error):
            return {"skipped": f"cannot read {db_for(run)}: {e}"}
        raise
    rep = out["report"]
    return {"season": rep.season, "verdict": rep.overall, "counts": rep.counts(),
            "report": out["paths"]["md"], "json": out["paths"]["json"]}


# ---------- the loop ----------

def run(name, *, base_save=BASE_SAVE, resume=False, offseason=True, max_stops=14, max_weeks=MAX_SEASON_STOPS,
        fast_season=False, targets_path=None, runner=None, python=None, report_fn=print, seasons_dir=None,
        saves_dir=None, do_report=True, environ=None, decision_fn=None, decision_targets=None):
    """Clone, walk the season, walk the offseason and report one season. Returns
    {"run_dir", "steps", "weeks", "stops", "season_complete", "report"} and, when `decision_fn` is given,
    "decisions"."""
    if not NAME_RE.match(name or ""):
        raise Refused(f"bad run name {name!r} (letters, digits, - and _)")
    if base_save.startswith(TUNE_PREFIX) or not base_save.startswith("CAREER-"):
        raise Refused(f"base save must be a franchise save like {BASE_SAVE}, not {base_save!r}")
    if do_report and not os.path.exists(targets_path or _default_targets()):
        raise Refused(f"no real-NFL targets at {targets_path or _default_targets()}: build them with "
                      "python3 -m engines.tuning.targets (refused up front rather than after a whole season)")
    if resume and db_exists(db_for(name)) is False:
        raise Refused(f"there is no {db_for(name)} to resume: a resumed season continues the database its "
                      "clone seeded at week 1 (that is where the neutral progression bootstrap is taken); "
                      "start a fresh run instead")
    t0 = time.time()
    run_dir = os.path.join(seasons_dir or SEASONS_DIR, name)
    os.makedirs(run_dir, exist_ok=True)
    progress = Progress(run_dir, name, save_for(name), db_for(name), resume=resume)
    if resume and not progress.found:
        raise Refused(f"no progress record at {progress.path}: a resumed run restarts the week walk where the "
                      "last one stopped, and without the record there is no telling whether the season is still "
                      "running (an offseason ingest keeps the league's stored week); start a fresh run instead")
    steps = progress.data["steps"]
    season_done = bool(resume and progress.data.get("season_complete"))
    # whatever a previous run left open, start from CURRENT FRANCHISES (a no-op when already there)
    release(name, runner=runner, python=python)
    if not resume:
        clone(name, base_save, runner=runner, python=python)
        progress.add("clone")
        # Seed the database from the week-1 clone, while every ConfidenceRating is still 50. The
        # neutral progression bootstrap is refused on a played season's save (confidence drifts
        # with results), and without state the league office stops OFFSEASON_START with
        # "explicit neutral bootstrap is required" (2026-11-10, madden_tune_p2f and p2g).
        ingest(name, runner=runner, python=python, saves_dir=saves_dir, environ=environ)
        progress.add("ingest-clone")
    weeks = []
    if season_done:
        season_ok = True
    elif fast_season:
        if not resume:
            sim(name, "regular-season-end", quit=False, runner=runner, python=python,
                run_dir=os.path.join(run_dir, "pilot_regular"))
            progress.add("regular-season")
        # a resumed season has no franchise open, so the postseason run loads the clone itself
        sim(name, "playoffs-end", in_place=not resume, runner=runner, python=python,
            run_dir=os.path.join(run_dir, "pilot_playoffs"))
        progress.add("playoffs")
        season_ok = True
    else:
        weeks = walk_season(name, runner=runner, python=python, max_weeks=max_weeks, work_dir=run_dir,
                            environ=environ, saves_dir=saves_dir, progress=progress)
        progress.add("season")
        season_ok = season_finished(weeks)
        if not season_ok:
            last = weeks[-1] if weeks else {}
            report_fn(f"[WARN] the season stopped before its end: {last.get('reason') or last.get('kind') or last}"
                      f" (screenshot in its week_* run dir); no report, no offseason walk")
    snap = os.path.join(run_dir, "snapshot.json")
    if season_ok and do_report and not season_done:
        # taken at the season's end: Madden rolls the per-game tables over at the new league year,
        # so an offseason snapshot has the next season's unplayed schedule and no games
        snapshot(name, snap, runner=runner, saves_dir=saves_dir)
        progress.add("snapshot")
    if season_ok and not season_done:
        # the walk left the save on the season's last stop (STAFF): Mu's OFFSEASON_START write
        ingest(name, runner=runner, python=python, auto_init=False, saves_dir=saves_dir, environ=environ)
        progress.add("ingest-playoffs")
        progress.season_done()
    stops = []
    if season_ok and offseason:
        stops = walk_offseason(name, runner=runner, python=python, max_stops=max_stops, work_dir=run_dir,
                               environ=environ, saves_dir=saves_dir)
        progress.add("offseason")
    out = {"run_dir": run_dir, "steps": steps, "weeks": weeks, "stops": stops,
           "season_complete": season_ok, "db": db_for(name), "save": save_for(name)}
    if do_report and season_ok:
        out["report"] = report(snap, run_dir, targets_path=targets_path)
        report_fn(json.dumps(out["report"], indent=1))
        season = out["report"].get("season")
        if decision_fn is not None and season:
            out["decisions"] = decision_fn(name, season, run_dir, targets_path=decision_targets)
            report_fn(json.dumps(out["decisions"], indent=1))
    out["minutes"] = round((time.time() - t0) / 60, 1)
    with open(os.path.join(run_dir, "run.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run one simulated tuning season (needs Madden on the PC)")
    ap.add_argument("--run", required=True, help="run name; the save is CAREER-TUNE-<run>, the DB madden_tune_<run>")
    ap.add_argument("--from", dest="base_save", default=BASE_SAVE)
    ap.add_argument("--resume", action="store_true", help="continue the run from where its progress record stopped")
    ap.add_argument("--no-offseason", action="store_true", help="stop after the postseason (no stage loop)")
    ap.add_argument("--fast-season", action="store_true",
                    help="sim the regular season and the postseason in one one-shot sim instead of a week at a "
                         "time (stat-only supersim work: no weekly league office, no in-season decisions)")
    ap.add_argument("--max-weeks", type=int, default=MAX_SEASON_STOPS,
                    help="safety cap on in-season advances (18 regular weeks + the playoff rounds)")
    ap.add_argument("--max-stops", type=int, default=14)
    ap.add_argument("--targets", help="targets JSON (default data/tuning/targets_2015_2025.json)")
    ap.add_argument("--decision-targets",
                    help="decision targets JSON (default data/tuning/decision_targets.json)")
    ap.add_argument("--no-decisions", action="store_true",
                    help="skip the P3-07 decision comparison report")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = ap.parse_args(argv)
    if args.dry_run:
        print(json.dumps({"run": args.run, "save": save_for(args.run), "db": db_for(args.run),
                          "dsn": dsn_for(args.run), "resume": args.resume, "offseason": not args.no_offseason,
                          "season": "one-shot sim" if args.fast_season else "week by week",
                          "max_weeks": args.max_weeks,
                          "from": args.base_save, "pilot": "tools.madden_pilot",
                          "decisions": not (args.no_decisions or args.no_report),
                          "steps": ["clone",
                                    "sim regular-season-end" if args.fast_season else "advance + ingest per week",
                                    "sim playoffs-end" if args.fast_season else "advance + ingest per round",
                                    "ingest the season's last stop",
                                    "offseason stage loop" if not args.no_offseason else "no offseason",
                                    "season report", "decision comparison"]}, indent=1))
        return 0
    try:
        out = run(args.run, base_save=args.base_save, resume=args.resume, offseason=not args.no_offseason,
                  max_stops=args.max_stops, max_weeks=args.max_weeks, fast_season=args.fast_season,
                  targets_path=args.targets,
                  report_fn=lambda s: print(s), do_report=not args.no_report,
                  decision_fn=None if args.no_decisions else decision_report,
                  decision_targets=args.decision_targets)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 3
    except StepFailed as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 3
    print(f"season run {args.run}: {out.get('minutes')} min, {len(out.get('weeks') or [])} in-season stops, "
          f"{len(out.get('stops') or [])} offseason stops, complete={out.get('season_complete')}, "
          f"report {out.get('report', {}).get('report')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
