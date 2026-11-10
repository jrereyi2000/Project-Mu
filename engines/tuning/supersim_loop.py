"""
Supersim tuning loop (docs/done/MADDEN_PILOT_AND_TUNING_PLAN.md §2.4, R4, R6).

  python3 -m engines.tuning.supersim_loop --run <name> --space <space.json> [--max-iters 20] [--from CAREER-MU2026]
                                          [--to regular-season-end] [--repeats 1] [--targets PATH] [--dry-run]

Tunes Madden's own supersim data (ebx under football/attribsys/data/supersim_*, S5) with tuning mods: one asset field
at a time, each iteration a mod, a simulated season on a fresh clone of the baseline save, and a score against the
real-NFL targets (engines/tuning/compare.py). Lower scores are better.

The search space is a JSON file written after `MmcHarness ebx-dump` on the supersim assets (R4):
  {"fields": [{"asset": "football/attribsys/data/supersim_...", "path": "<field path from ebx-dump>",
               "start": <the game's current value>, "step": <first step>, "min": <lo>, "max": <hi>}, ...]}

iteration(run_dir, change) runs one iteration, every Madden-touching step through a CLI (subprocess; `runner`
replaces subprocess.run in tests):
  1. state.json + change -> data/mod_builds/<slug>/recipe.json: one `ebx-set` op per field at its current value
     (the baseline sets every field to its start value), slug mu_tune_<run>, title "Mu Tune <run>"
  2. python3 -m tools.mod_builder build <slug>; python3 -m tools.mod_builder install <slug> --pack "Mu Tune"
  3. per repeat (R6: average 2-3 seasons when one is too noisy):
       python3 -m tools.madden_pilot clone-save --from <base> --to CAREER-TUNE-<run>   (a fresh week-1 copy)
       python3 -m tools.madden_pilot launch --pack "Mu Tune"
       python3 -m tools.madden_pilot sim-season --save CAREER-TUNE-<run> --to <to>
       node pipeline/save_reader/pc_save_reader.mjs <saves>/CAREER-TUNE-<run> --out <iter dir>/snapshot_<r>.json
       engines.tuning.compare: season_from_snapshot + compare against the targets
  4. appends {iter, change, values, score, scores, worst, accepted, ...} to data/tuning/runs/<run>/log.jsonl
     and writes state.json (the best values so far)

next_change(log, fields) picks the next change from the log alone:
  - one field at a time, in the space's order; the first try on a field goes up by its step;
  - a try that lowers the best score by at least GAIN (2%) is kept, and the next try goes on in the same direction;
  - a try that is worse than the best (an overshoot) is undone: the step halves and the direction turns round;
    a try that is better but by under 2% is kept, without counting as a gain;
  - after FIELD_TRIES tries without a gain on a field, the next field;
  - stop (None) after STOP_TRIES (3) tries in a row without a 2% gain, or when every field's step is under its
    min_step (step / 16 by default).
run() refuses to start unless `MmcHarness.exe info` says ebx_readable: true (the Madden 27 key must be entered once
in MMC Editor; MOD_BUILDING.md §3), then runs the baseline (if the log is empty) and iterations until the stop rule,
--max-iters, or a STOP file in the run folder (JR says stop).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))
RUNS_DIR = os.path.join(ROOT, "data", "tuning", "runs")
PACK = "Mu Tune"
BASE_SAVE = "CAREER-MU2026"
TUNE_PREFIX = "CAREER-TUNE-"
DEFAULT_TO = "regular-season-end"
SAVES_DIR = os.environ.get("MADDEN_SAVES_DIR", "/mnt/c/Users/jrere/Documents/Madden NFL 27/saves")
READER = os.path.join(ROOT, "pipeline", "save_reader", "pc_save_reader.mjs")
GAIN = 0.02
STOP_TRIES = 3
FIELD_TRIES = 2
MIN_STEP_FRAC = 1 / 16
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class Refused(RuntimeError):
    """The loop won't start (ebx not readable, bad run name or base save)."""


class StepFailed(RuntimeError):
    """A CLI step of an iteration failed."""


# ---------- the search ----------

def _key(f):
    return f"{f['asset']}::{f['path']}"


def _clamp(f, v):
    lo, hi = f.get("min"), f.get("max")
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return round(v, 6)


def search_state(log, fields):
    """Replays the log into the search state: {values, best, field, step, direction, stall, field_tries, done}."""
    values = {_key(f): f["start"] for f in fields}
    steps = {_key(f): f["step"] for f in fields}
    dirs = {_key(f): 1 for f in fields}
    st = {"values": values, "steps": steps, "dirs": dirs, "best": None, "field": 0, "stall": 0, "field_tries": 0}
    for entry in log:
        s = entry.get("score")
        ch = entry.get("change")
        if ch is None:                      # the baseline
            if s is not None:
                st["best"] = s
            continue
        k = ch["key"]
        best = st["best"]
        if s is None or best is None:
            st["stall"] += 1
            continue
        if s <= best * (1 - GAIN):          # a real gain: keep, same direction
            values[k] = ch["value"]
            st["best"], st["stall"], st["field_tries"] = s, 0, 0
        else:
            if s < best:                    # better by under 2%: keep the value, no gain counted
                values[k] = ch["value"]
                st["best"] = s
            else:                           # overshoot: undo, halve, turn round
                steps[k] /= 2
                dirs[k] = -dirs[k]
            st["stall"] += 1
            st["field_tries"] += 1
            if st["field_tries"] >= FIELD_TRIES:
                st["field"] = (st["field"] + 1) % len(fields)
                st["field_tries"] = 0
    return st


def next_change(log, fields):
    """The next change {key, asset, path, value, old, step, direction}, or None when the stop rule holds."""
    if not fields:
        return None
    st = search_state(log, fields)
    if st["best"] is None:
        return None if any(e.get("change") is None for e in log) else {"baseline": True}
    if st["stall"] >= STOP_TRIES:
        return None
    for i in range(len(fields)):
        f = fields[(st["field"] + i) % len(fields)]
        k = _key(f)
        min_step = f.get("min_step", f["step"] * MIN_STEP_FRAC)
        step = st["steps"][k]
        if step < min_step:
            continue
        old = st["values"][k]
        value = _clamp(f, old + st["dirs"][k] * step)
        if value == old:                    # at a bound: try the other way once
            value = _clamp(f, old - st["dirs"][k] * step)
            if value == old:
                continue
        return {"key": k, "asset": f["asset"], "path": f["path"], "value": value, "old": old, "step": step,
                "direction": 1 if value > old else -1}
    return None


# ---------- one iteration ----------

def _run_cmd(runner, cmd, step, cwd=ROOT):
    proc = runner(cmd, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = ((proc.stdout or "") + (proc.stderr or ""))[-1500:]
        raise StepFailed(f"{step} failed (exit {proc.returncode}): {' '.join(map(str, cmd))}\n{tail}")
    return proc


def slug_for(run):
    return "mu_tune_" + re.sub(r"[^a-z0-9]+", "_", run.lower()).strip("_")


def _builds_root():
    from tools.mod_builder import builds_dir       # lazy: tools/ isn't needed to import this module
    return builds_dir()


def write_recipe(run, values, fields, builds_root=None):
    slug = slug_for(run)
    folder = os.path.join(builds_root or _builds_root(), slug)
    os.makedirs(folder, exist_ok=True)
    recipe = {"slug": slug, "title": f"Mu Tune {run}", "author": "JR (Project Mu)", "version": "1.0",
              "category": "Gameplay", "route": "harness",
              "description": f"Supersim tuning run {run} (engines/tuning/supersim_loop.py)",
              "ops": [{"op": "ebx-set", "asset": f["asset"], "path": f["path"], "value": values[_key(f)]}
                      for f in fields]}
    path = os.path.join(folder, "recipe.json")
    with open(path, "w") as fh:
        json.dump(recipe, fh, indent=2)
    return slug, path


def read_log(run_dir):
    path = os.path.join(run_dir, "log.jsonl")
    if not os.path.exists(path):
        return []
    with open(path) as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _append(run_dir, entry):
    with open(os.path.join(run_dir, "log.jsonl"), "a") as fh:
        fh.write(json.dumps(entry) + "\n")


def _score_snapshot(snapshot_path, targets):
    from engines.tuning import compare as C
    with open(snapshot_path) as fh:
        snap = json.load(fh)
    report = C.compare(C.season_from_snapshot(snap), targets)
    return report


def iteration(run_dir, change, *, fields=None, targets=None, runner=None, base_save=BASE_SAVE, to=DEFAULT_TO,
              repeats=1, python=None, builds_root=None, saves_dir=None):
    """Runs one iteration (change None = the baseline) and appends it to <run_dir>/log.jsonl. Returns the entry."""
    runner = runner or subprocess.run
    python = python or sys.executable
    saves_dir = saves_dir or SAVES_DIR
    run = os.path.basename(os.path.normpath(run_dir))
    cfg = _load_json(os.path.join(run_dir, "config.json"), {})
    fields = fields if fields is not None else cfg.get("fields", [])
    if targets is None:
        targets = _load_json(cfg.get("targets") or _default_targets(), None)
        if targets is None:
            raise Refused("no real-NFL targets: build them with python3 -m engines.tuning.targets")
    log = read_log(run_dir)
    st = search_state(log, fields)
    values = dict(st["values"])
    if change and not change.get("baseline"):
        values[change["key"]] = change["value"]
    n = len(log)
    it_dir = os.path.join(run_dir, f"iter_{n:03d}")
    os.makedirs(it_dir, exist_ok=True)
    t0 = time.time()
    slug, recipe = write_recipe(run, values, fields, builds_root)
    save = TUNE_PREFIX + run
    _run_cmd(runner, [python, "-m", "tools.mod_builder", "build", slug], "mod build")
    _run_cmd(runner, [python, "-m", "tools.mod_builder", "install", slug, "--pack", PACK], "mod install")
    scores, worst = [], []
    for r in range(max(1, repeats)):
        _run_cmd(runner, [python, "-m", "tools.madden_pilot", "clone-save", "--from", base_save, "--to", save],
                 "clone save")
        _run_cmd(runner, [python, "-m", "tools.madden_pilot", "launch", "--pack", PACK], "launch")
        # sim-season makes a savepoint and quits to the franchise list itself
        _run_cmd(runner, [python, "-m", "tools.madden_pilot", "sim-season", "--save", save, "--to", to], "sim season")
        snap = os.path.join(it_dir, f"snapshot_{r}.json")
        _run_cmd(runner, ["node", READER, os.path.join(saves_dir, save), "--out", snap], "save reader")
        report = _score_snapshot(snap, targets)
        if report.score is None:
            raise StepFailed(f"no scored stats in {snap} (no games simmed?)")
        scores.append(report.score)
        worst = [{"key": s.key, "sim": s.sim, "lo": s.lo, "hi": s.hi, "score": s.score} for s in report.worst(5)]
    score = round(sum(scores) / len(scores), 4)
    best = st["best"]
    entry = {"iter": n, "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
             "change": None if not change or change.get("baseline") else change,
             "values": values, "score": score, "scores": scores, "best_before": best,
             "gain": None if best is None else round((best - score) / best, 4) if best else None,
             "accepted": best is None or score < best, "worst": worst, "recipe": recipe, "save": save,
             "minutes": round((time.time() - t0) / 60, 1)}
    _append(run_dir, entry)
    st_after = search_state(read_log(run_dir), fields)
    with open(os.path.join(run_dir, "state.json"), "w") as fh:
        json.dump({"best": st_after["best"], "values": st_after["values"]}, fh, indent=1)
    return entry


# ---------- the loop ----------

def ebx_readable(info=None):
    """True when MmcHarness.exe info says ebx_readable (about 30 s, read-only)."""
    if info is None:
        from tools.mod_builder import harness     # lazy: only the live loop needs the harness
        info = harness.run(["info"])
    return bool(info.get("ebx_readable"))


def _load_json(path, default):
    if not path or not os.path.exists(path):
        return default
    with open(path) as fh:
        return json.load(fh)


def _default_targets():
    from engines.tuning import targets as T
    return T.default_path()


def run(name, fields, *, max_iters=20, base_save=BASE_SAVE, to=DEFAULT_TO, repeats=1, targets_path=None,
        runner=None, check_ebx=None, report=print, runs_dir=None, **kw):
    """Checks ebx, then runs the baseline and iterations until the stop rule, max_iters, or a STOP file."""
    if not NAME_RE.match(name or ""):
        raise Refused(f"bad run name {name!r} (letters, digits, - and _)")
    if base_save.startswith(TUNE_PREFIX) or not base_save.startswith("CAREER-"):
        raise Refused(f"base save must be a franchise save like {BASE_SAVE}, not {base_save!r}")
    if not (check_ebx or ebx_readable)():
        raise Refused("MmcHarness info says ebx_readable: false. Supersim tuning needs the Madden 27 key entered once "
                      "in MMC Editor's prompt (docs/reference/MOD_BUILDING.md §3); JR does that himself.")
    targets_path = targets_path or _default_targets()
    targets = _load_json(targets_path, None)
    if targets is None:
        raise Refused(f"no targets at {targets_path}: build them with python3 -m engines.tuning.targets")
    run_dir = os.path.join(runs_dir or RUNS_DIR, name)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "config.json"), "w") as fh:
        json.dump({"fields": fields, "base_save": base_save, "to": to, "repeats": repeats,
                   "targets": targets_path}, fh, indent=1)
    done = []
    for _ in range(max_iters):
        if os.path.exists(os.path.join(run_dir, "STOP")):
            report("STOP file found: stopping")
            break
        log = read_log(run_dir)
        change = next_change(log, fields)
        if change is None:
            report("stop rule: no 2% gain over the last 3 tries")
            break
        entry = iteration(run_dir, change, fields=fields, targets=targets, runner=runner, base_save=base_save, to=to,
                          repeats=repeats, **kw)
        done.append(entry)
        report(format_entry(entry))
    return {"run_dir": run_dir, "iterations": done, "state": search_state(read_log(run_dir), fields)}


def format_entry(e):
    ch = e.get("change")
    what = "baseline" if not ch else f"{ch['path']} ({ch['asset'].rsplit('/', 1)[-1]}) {ch['old']} -> {ch['value']}"
    gain = "" if e.get("gain") is None else f", gain {e['gain'] * 100:+.1f}%"
    worst = ", ".join(f"{w['key']} {w['sim']}" for w in e.get("worst", [])[:3])
    return f"iter {e['iter']}: {what}: score {e['score']}{gain} ({'kept' if e['accepted'] else 'undone'}); " \
           f"worst: {worst}; {e['minutes']} min"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Supersim tuning loop (needs the Madden 27 key, the pilot and JR's yes)")
    ap.add_argument("--run", required=True, help="run name; the save is CAREER-TUNE-<run>")
    ap.add_argument("--space", required=True, help='JSON {"fields": [{asset, path, start, step, min, max}]}')
    ap.add_argument("--max-iters", type=int, default=20)
    ap.add_argument("--from", dest="base_save", default=BASE_SAVE)
    ap.add_argument("--to", default=DEFAULT_TO)
    ap.add_argument("--repeats", type=int, default=1, help="seasons simmed per iteration (R6)")
    ap.add_argument("--targets", help="targets JSON (default data/tuning/targets_2015_2025.json)")
    ap.add_argument("--dry-run", action="store_true", help="print the next change only")
    args = ap.parse_args(argv)
    fields = _load_json(args.space, {}).get("fields", [])
    if not fields:
        print(f"no fields in {args.space}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(json.dumps(next_change(read_log(os.path.join(RUNS_DIR, args.run)), fields)))
        return 0
    try:
        out = run(args.run, fields, max_iters=args.max_iters, base_save=args.base_save, to=args.to,
                  repeats=args.repeats, targets_path=args.targets)
    except (Refused, StepFailed) as e:
        print(f"refused: {e}" if isinstance(e, Refused) else f"stopped: {e}", file=sys.stderr)
        return 3
    print(f"best score {out['state']['best']}; log {os.path.join(out['run_dir'], 'log.jsonl')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
