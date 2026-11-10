"""
One simulated season on the tuning league (CLOSE_OUT_CHECKLIST P2-05, docs/reference/TUNING_LEAGUE.md).

  python3 -m engines.tuning.season_loop --run p2c [--from CAREER-MU2026] [--resume] [--no-offseason]
                                        [--max-stops 10] [--targets PATH] [--dry-run]

One command per simulated season. It drives the same CLIs the live franchise uses, every Madden-touching step
through a subprocess (`runner` replaces subprocess.run in tests):

  1. tools.madden_pilot clone-save --from <base> --to CAREER-TUNE-<run>   (fresh copy, capacity-pruned)
  2. tools.madden_pilot sim-season --save CAREER-TUNE-<run> --to regular-season-end
  3. tools.madden_pilot sim-season --save CAREER-TUNE-<run> --franchise TUNE-<run> --to playoffs-end
  4. the stage loop (P2-03), per stop: tools.madden_pilot advance (exactly one stop, then quit) ->
     pipeline.watch_save --once into madden_tune_<run>, which runs the weekly engines and the league office and
     writes Mu's decisions into the save with the franchise closed -> the pilot reloads the written save.
     Madden never sims through the offseason (TUNING_LEAGUE.md §6); the loop runs through the preseason
     weeks and the cut day and ends when an advance lands on the next regular-season week.
  5. a season report: a pc_save_reader snapshot of the final save scored against the real-NFL targets
     (engines/tuning/compare.py), written to data/tuning/seasons/<run>/season_<year>.md and .json.

The tuning database is created on the first ingest (--auto-init) and is disposable; a run name is all the state.
Madden must be open for steps 2-4: this module asks for the PC through the pilot's own rules (CAREER-TUNE-* only,
a running watcher refuses, quitting writes the autosave).
"""
import argparse
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
# Madden27.exe may still run between pilots; the league office writes the save with the franchise closed.
INGEST_ENV = {"LEAGUE_OFFICE_FRANCHISE_CLOSED": "1"}


class Refused(RuntimeError):
    """The loop won't start (bad run name, bad base save, a missing pilot)."""


class StepFailed(RuntimeError):
    """A CLI step of the season failed."""


def db_for(run):
    return DB_PREFIX + re.sub(r"[^a-z0-9_]+", "_", run.lower())


def dsn_for(run):
    return DEFAULT_DSN.format(db=db_for(run))


def save_for(run):
    return TUNE_PREFIX + run.upper()          # the saves folder is upper-case: CAREER-TUNE-P2C


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
    failing — an unknown offseason screen is exactly what P2-02 has to catalogue next."""
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


def walk_offseason(run, *, runner=None, python=None, max_stops=14, work_dir=None, environ=None):
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
        ingest(run, runner=runner, python=python, auto_init=n == 1, environ=environ)
        after = res.get("to") or {}
        if str(after.get("phase", "")).upper() == "WEEK":       # the next regular season has begun
            break
    return stops


def snapshot(run, out_path, *, runner=None, saves_dir=None):
    """pc_save_reader.mjs on the clone, for the report."""
    runner = runner or subprocess.run
    save_path = os.path.join(saves_dir or SAVES_DIR, save_for(run))
    return _run_cmd(runner, ["node", READER, save_path, "--out", str(out_path)], "save reader")


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
    with open(md_path, "w") as fh:
        fh.write(C.format_report(rep))
    json_path = os.path.join(out_dir, f"season_{season}.json")
    with open(json_path, "w") as fh:
        json.dump({"season": season, "score": rep.score, "meta": rep.meta,
                   "stats": [s.__dict__ for s in rep.stats], "missing": rep.missing}, fh, indent=1)
    return {"season": season, "score": rep.score, "report": md_path, "json": json_path,
            "inside": sum(1 for s in rep.stats if s.score == 0), "stats": len(rep.stats)}


# ---------- the loop ----------

def run(name, *, base_save=BASE_SAVE, resume=False, offseason=True, max_stops=14, targets_path=None,
        runner=None, python=None, report_fn=print, seasons_dir=None, saves_dir=None, do_report=True,
        environ=None):
    """Clone, sim, walk the offseason and report one season. Returns
    {"run_dir", "steps", "stops", "report"}."""
    if not NAME_RE.match(name or ""):
        raise Refused(f"bad run name {name!r} (letters, digits, - and _)")
    if base_save.startswith(TUNE_PREFIX) or not base_save.startswith("CAREER-"):
        raise Refused(f"base save must be a franchise save like {BASE_SAVE}, not {base_save!r}")
    if do_report and not os.path.exists(targets_path or _default_targets()):
        raise Refused(f"no real-NFL targets at {targets_path or _default_targets()}: build them with "
                      "python3 -m engines.tuning.targets (refused up front rather than after a whole season)")
    t0 = time.time()
    run_dir = os.path.join(seasons_dir or SEASONS_DIR, name)
    os.makedirs(run_dir, exist_ok=True)
    steps = []
    if not resume:
        release(name, runner=runner, python=python)
        clone(name, base_save, runner=runner, python=python)
        steps.append("clone")
        sim(name, "regular-season-end", quit=False, runner=runner, python=python,
            run_dir=os.path.join(run_dir, "pilot_regular"))
        steps.append("regular-season")
    # a resumed season has no franchise open, so the postseason run loads the clone itself
    sim(name, "playoffs-end", in_place=not resume, runner=runner, python=python,
        run_dir=os.path.join(run_dir, "pilot_playoffs"))
    steps.append("playoffs")
    snap = os.path.join(run_dir, "snapshot.json")
    if do_report:
        # taken at the season's end: Madden rolls the per-game tables over at the new league year,
        # so an offseason snapshot has the next season's unplayed schedule and no 2026 games
        snapshot(name, snap, runner=runner, saves_dir=saves_dir)
        steps.append("snapshot")
    ingest(name, runner=runner, python=python, saves_dir=saves_dir, environ=environ)
    steps.append("ingest-playoffs")
    stops = []
    if offseason:
        stops = walk_offseason(name, runner=runner, python=python, max_stops=max_stops, work_dir=run_dir,
                               environ=environ)
        steps.append("offseason")
    out = {"run_dir": run_dir, "steps": steps, "stops": stops, "db": db_for(name), "save": save_for(name)}
    if do_report:
        out["report"] = report(snap, run_dir, targets_path=targets_path)
        report_fn(json.dumps(out["report"], indent=1))
    out["minutes"] = round((time.time() - t0) / 60, 1)
    with open(os.path.join(run_dir, "run.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=str)
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Run one simulated tuning season (needs Madden on the PC)")
    ap.add_argument("--run", required=True, help="run name; the save is CAREER-TUNE-<run>, the DB madden_tune_<run>")
    ap.add_argument("--from", dest="base_save", default=BASE_SAVE)
    ap.add_argument("--resume", action="store_true", help="don't clone or sim the regular season again")
    ap.add_argument("--no-offseason", action="store_true", help="stop after the postseason (no stage loop)")
    ap.add_argument("--max-stops", type=int, default=14)
    ap.add_argument("--targets", help="targets JSON (default data/tuning/targets_2015_2025.json)")
    ap.add_argument("--no-report", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the plan and exit")
    args = ap.parse_args(argv)
    if args.dry_run:
        print(json.dumps({"run": args.run, "save": save_for(args.run), "db": db_for(args.run),
                          "dsn": dsn_for(args.run), "resume": args.resume, "offseason": not args.no_offseason,
                          "from": args.base_save, "pilot": "tools.madden_pilot",
                          "steps": ["clone", "sim regular-season-end", "sim playoffs-end", "ingest",
                                    "offseason stage loop" if not args.no_offseason else "no offseason"]}, indent=1))
        return 0
    try:
        out = run(args.run, base_save=args.base_save, resume=args.resume, offseason=not args.no_offseason,
                  max_stops=args.max_stops, targets_path=args.targets,
                  report_fn=lambda s: print(s), do_report=not args.no_report)
    except Refused as e:
        print(f"refused: {e}", file=sys.stderr)
        return 3
    except StepFailed as e:
        print(f"stopped: {e}", file=sys.stderr)
        return 3
    print(f"season run {args.run}: {out.get('minutes')} min, {len(out.get('stops') or [])} stops, "
          f"report {out.get('report', {}).get('report')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
