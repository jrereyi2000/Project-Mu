"""
Scouting engine (docs/team_management/04_SCOUTING_AND_DRAFT_PLAN.md §2.1-§2.5; PROJECT_SPEC §2.18, §3.3g).

A draft class's scouting rows (scout_prospects) carry each prospect's hidden true value V (engines/scouting_value.py).
Everything keys on prospect_key, because a Draft Board re-scan replaces draft_prospects ids (relink re-points them).

Steps: one scouting run each. In-season step = the league week; offseason step = 23 + the GM offseason stage
(24 post-Super Bowl .. 27 draft). A class's first step is the step it was initialized at (the 2037 class: 4).

CLI:
    python3 -m engines.scouting_engine init --draft-season 2037 [--dry-run]
    python3 -m engines.scouting_engine relink --draft-season 2037
    python3 -m engines.scouting_engine run [--draft-season 2037] [--week W | --stage S | --step N] [--dry-run] [--replace]
"""
import argparse
import hashlib
import json
import math
import random
import sys

from engines import scouting_value as sv

STEP_DRAFT = 27                       # 23 + the draft stage (gm_trade_engine.DRAFT_STAGE)
OFFSEASON_BASE = 23
CODE_VERSION = "scouting-1"


# ------------------------------------------------------------------ steps ----
def step_for(week, stage=None):
    """The scouting step for a league week (in-season) or an offseason stage 1-4; None after the draft."""
    if stage:
        s = OFFSEASON_BASE + int(stage)
        return s if s <= STEP_DRAFT else None
    return min(max(int(week), 1), OFFSEASON_BASE)


def step_when(step, season):
    """(season, week, stage) a step is stored under."""
    if step > OFFSEASON_BASE:
        return season, 24, step - OFFSEASON_BASE
    return season, step, None


def current_step(cur):
    """(season, step) for league_state: the week about to be played, or 23 + the last GM offseason stage run."""
    from engines.gm_trade_engine import league_window, read_stage
    season, week, window = league_window(cur)
    if window == "OFFSEASON":
        stage = read_stage(cur, season) or 0
        return season, (step_for(week, stage) if stage else OFFSEASON_BASE)
    return season, step_for(week)


# ------------------------------------------------------------------ class ----
def seed_of(key):
    return hashlib.sha256(f"scout:{key}".encode()).hexdigest()[:16]


def build_class(rows, draft_season, first_step):
    """rows: draft_prospects dicts {id, full_name, position, college, age, ovr, projected_pick, height_in, weight}.
    Returns the class's scouting rows (pure): {key, prospect_id, V, true_rank, inputs, class_year, conference,
    school, seed, first_step, projected_pick, ...}. A key that repeats (same name and group) gets ':2', ':3'."""
    out, seen = [], {}
    for r in sorted(rows, key=lambda r: (r.get("projected_pick") or 9999, r["id"])):
        key = sv.prospect_key(draft_season, r["full_name"], r.get("position"))
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 1:
            key = f"{key}:{seen[key]}"
        v, inputs = sv.true_value(r.get("ovr"), r.get("position"), r.get("age"))
        seed = seed_of(key)
        sch = sv.school(r.get("college"))
        out.append({"key": key, "prospect_id": r["id"], "V": v, "inputs": inputs, "seed": seed,
                    "class_year": sv.class_year(r.get("age"), seed), "conference": sch["conference"] if sch else None,
                    "school": sch, "first_step": first_step, "full_name": r["full_name"],
                    "position": r.get("position"), "group": inputs["group"], "college": r.get("college"),
                    "age": r.get("age"), "height_in": r.get("height_in"), "weight": r.get("weight"),
                    "projected_pick": r.get("projected_pick"), "status": "ACTIVE"})
    ranks = sv.true_ranks({p["key"]: p["V"] for p in out})
    for p in out:
        p["true_rank"] = ranks[p["key"]]
    return out


def relink_actions(existing, board):
    """existing: {key: status} stored for the class; board: build_class rows from the current draft_prospects.
    Returns {link: [(key, prospect_id)], withdraw: [key], new: [row]} (pure). A WITHDRAWN prospect who comes back
    is re-linked and made ACTIVE again; DRAFTED / UDFA rows only re-link."""
    on_board = {p["key"]: p for p in board}
    link = [(k, on_board[k]["prospect_id"]) for k in existing if k in on_board]
    withdraw = sorted(k for k, st in existing.items() if k not in on_board and st == "ACTIVE")
    new = [p for k, p in on_board.items() if k not in existing]
    return {"link": link, "withdraw": withdraw, "new": new}


def grade_summary(values):
    """Counts by grade band (for --dry-run printouts: never V by name)."""
    knots = sv.grade_map(values)
    bands = {"90+": 0, "80-89": 0, "70-79": 0, "60-69": 0, "<60": 0}
    for v in values:
        g = sv.grade(v, knots)
        b = "90+" if g >= 90 else "80-89" if g >= 80 else "70-79" if g >= 70 else "60-69" if g >= 60 else "<60"
        bands[b] += 1
    return bands


# -------------------------------------------------------------- consensus ----
# C = V + e. The first error is Madden's board gap plus noise (JR, Q1: V stays ratings-based, Madden's PICK order
# seeds the first consensus): e_first = SEED_ALPHA * gap + sigma_first * z. Then e_{s+1} = (1 - LAMBDA) e_s + tau_s z:
# the seed decays by (1 - LAMBDA) a step, and the noise part's sd falls linearly from SIGMA_0 at the class's first
# step to SIGMA_DRAFT at step 27, never below it. Frozen by --calibrate (Phase 4).
SIGMA_0 = 3.0
SIGMA_DRAFT = 1.5
LAMBDA = 0.05
SEED_ALPHA = 0.75                     # share of Madden's board gap in the first consensus error


def sigma_at(step, first_step):
    span = max(STEP_DRAFT - first_step, 1)
    f = min(max((step - first_step) / span, 0.0), 1.0)
    return SIGMA_0 + (SIGMA_DRAFT - SIGMA_0) * f


def tau_at(step, first_step):
    """The innovation sd that carries sd(e) from step-1's sigma to step's."""
    a = 1 - LAMBDA
    return math.sqrt(max(sigma_at(step, first_step) ** 2 - (a * sigma_at(step - 1, first_step)) ** 2, 0.0))


def _rng(*parts):
    return random.Random(":".join(str(p) for p in parts))


def board_gaps(cls, values=None):
    """{key: gap}: the V at the prospect's Madden projected_pick rank minus his own V (OVR points). values: the whole
    class's V (default: cls's own), so a prospect added late is placed on the full class's scale."""
    vs = sorted(values if values is not None else (p["V"] for p in cls), reverse=True)
    n = len(vs)
    return {p["key"]: vs[min(max((p.get("projected_pick") or n), 1), n) - 1] - p["V"] for p in cls}


def initial_errors(cls, step, first_step, draft_season, salt="", values=None):
    """e at a prospect's first step: SEED_ALPHA * Madden gap + sigma_step * z."""
    gaps = board_gaps(cls, values)
    sig = sigma_at(step, first_step)
    return {p["key"]: SEED_ALPHA * gaps[p["key"]] + sig * _rng("init", draft_season, p["key"], salt).gauss(0, 1)
            for p in cls}


def next_errors(prev_err, step, first_step, draft_season, salt=""):
    a, tau = 1 - LAMBDA, tau_at(step, first_step)
    return {k: a * e + tau * _rng("step", draft_season, step, k, salt).gauss(0, 1) for k, e in prev_err.items()}


def consensus(cls, err, prev=None):
    """{key: {err, C, grade, rank, round_label, delta_grade, delta_rank, move}} for the ACTIVE prospects.
    prev: the previous step's {key: {err, grade, rank}}. move = the change in e (OVR points)."""
    active = [p for p in cls if p.get("status", "ACTIVE") == "ACTIVE"]
    knots = sv.grade_map([p["V"] for p in cls if p.get("status") != "WITHDRAWN"])
    c = {p["key"]: p["V"] + err[p["key"]] for p in active}
    order = sorted(c, key=lambda k: (-c[k], k))
    out = {}
    for i, k in enumerate(order, start=1):
        g = sv.grade(c[k], knots)
        pr = (prev or {}).get(k)
        out[k] = {"err": round(err[k], 3), "C": c[k], "grade": g, "rank": i, "round_label": sv.round_label(i),
                  "delta_grade": round(g - float(pr["grade"]), 1) if pr else None,
                  "delta_rank": (pr["rank"] - i) if pr else None,
                  "move": round(err[k] - float(pr["err"]), 3) if pr else 0.0}
    return out


def chain(cls, first_step, to_step, draft_season, start=None, salt=""):
    """Simulate the consensus from start (a stored step's {key: err}, at start[0]) or the class's first step,
    through to_step. Returns {step: consensus(...)}. Prospects new since the stored step start fresh."""
    out = {}
    if start:
        s0, err = start
        prev = None
    else:
        s0, err, prev = first_step, initial_errors(cls, first_step, first_step, draft_season, salt), None
        out[s0] = prev = consensus(cls, err)
    for step in range(s0 + 1, to_step + 1):
        err = next_errors({k: e for k, e in err.items()}, step, first_step, draft_season, salt)
        missing = [p for p in cls if p["key"] not in err]
        if missing:
            err.update(initial_errors(missing, step, first_step, draft_season, salt, [p["V"] for p in cls]))
        out[step] = prev = consensus(cls, err, prev)
    return out


def risers_fallers(cons, limit=10, within=150):
    """(risers, fallers): the biggest rank moves among prospects ranked within `within` now or before."""
    rows = [(k, r) for k, r in cons.items() if r["delta_rank"] is not None and
            min(r["rank"], r["rank"] + r["delta_rank"]) <= within]
    up = sorted((x for x in rows if x[1]["delta_rank"] > 0), key=lambda x: (-x[1]["delta_rank"], x[1]["rank"]))
    down = sorted((x for x in rows if x[1]["delta_rank"] < 0), key=lambda x: (x[1]["delta_rank"], x[1]["rank"]))
    return up[:limit], down[:limit]


def step_events(cls, cons, step, draft_season):
    """{key: [events]} for the step (scouting_events.generate per prospect; VISITs are added by the caller)."""
    from engines import scouting_events as se
    n = max(len(cls), 1)
    out = {}
    for p in cls:
        if p["key"] not in cons:
            continue
        q = dict(p, v_pct=100.0 * (1 - (p["true_rank"] - 1) / n))
        evs = se.generate(step, q, cons[p["key"]]["move"], cons[p["key"]]["rank"], draft_season)
        if evs:
            out[p["key"]] = evs
    return out


# --------------------------------------------------------------------- db ----
def installed(cur):
    cur.execute("SELECT to_regclass('scout_prospects') IS NOT NULL")
    return bool(cur.fetchone()[0])


def board_rows(cur, draft_season):
    cur.execute("""SELECT id, full_name, position, college, age, ovr, projected_pick, height_in, weight
                   FROM draft_prospects WHERE draft_season = %s""", (draft_season,))
    cols = ("id", "full_name", "position", "college", "age", "ovr", "projected_pick", "height_in", "weight")
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _insert_prospects(cur, draft_season, rows):
    cur.executemany(
        """INSERT INTO scout_prospects (draft_season, prospect_key, prospect_id, true_value, true_rank, value_inputs,
               class_year, conference, first_step, status, seed)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'ACTIVE', %s)
           ON CONFLICT (draft_season, prospect_key) DO NOTHING""",
        [(draft_season, p["key"], p["prospect_id"], p["V"], p["true_rank"], json.dumps(p["inputs"]),
          p["class_year"], p["conference"], p["first_step"], p["seed"]) for p in rows])


def _rerank(cur, draft_season):
    cur.execute("""UPDATE scout_prospects s SET true_rank = r.rk
                   FROM (SELECT id, row_number() OVER (ORDER BY true_value DESC, prospect_key) AS rk
                         FROM scout_prospects WHERE draft_season = %s AND status <> 'WITHDRAWN') r
                   WHERE s.id = r.id""", (draft_season,))


def init(cur, draft_season, step=None, dry_run=False, log=print):
    """Create the class's scout_prospects rows from draft_prospects (a class already stored only gains new
    prospects; use relink after a re-scan). Prints counts and the grade distribution, never V by name."""
    rows = board_rows(cur, draft_season)
    if not rows:
        raise SystemExit(f"no draft_prospects rows for the {draft_season} class")
    if step is None:
        step = current_step(cur)[1]
    built = build_class(rows, draft_season, step)
    existing = 0
    if installed(cur):
        cur.execute("SELECT count(*) FROM scout_prospects WHERE draft_season = %s", (draft_season,))
        existing = cur.fetchone()[0]
    elif not dry_run:
        raise SystemExit("the scouting migration (2026_12_12_scouting.sql) isn't applied")
    unrated = sum(1 for p in built if p["inputs"]["unrated"])
    unmapped = sorted({p["college"] for p in built if p["school"] is None and p["college"]})
    rep = {"draft_season": draft_season, "first_step": step, "prospects": len(built), "already_stored": existing,
           "unrated": unrated, "unmapped_colleges": unmapped, "grades": grade_summary([p["V"] for p in built])}
    log(f"scouting init {draft_season}: {len(built)} prospects (first step {step}), {existing} already stored, "
        f"{unrated} unrated (valued at OVR {sv.UNRATED_OVR:g}), unmapped colleges {unmapped or 'none'}")
    log(f"  grade distribution: {rep['grades']}")
    if not dry_run:
        _insert_prospects(cur, draft_season, built)
        _rerank(cur, draft_season)
    else:
        log("  dry run: nothing written")
    return rep


def relink(cur, draft_season, step=None, log=print):
    """After a Draft Board load: re-point prospect_id by prospect_key, mark prospects no longer on the board
    WITHDRAWN, add new ones at the current step. No-op when the class was never initialized."""
    if not installed(cur):
        return None
    cur.execute("SELECT prospect_key, status FROM scout_prospects WHERE draft_season = %s", (draft_season,))
    existing = dict(cur.fetchall())
    if not existing:
        return None
    if step is None:
        step = current_step(cur)[1]
    acts = relink_actions(existing, build_class(board_rows(cur, draft_season), draft_season, step))
    cur.executemany("""UPDATE scout_prospects SET prospect_id = %s,
                           status = CASE WHEN status = 'WITHDRAWN' THEN 'ACTIVE' ELSE status END
                       WHERE draft_season = %s AND prospect_key = %s""",
                    [(pid, draft_season, k) for k, pid in acts["link"]])
    if acts["withdraw"]:
        cur.execute("""UPDATE scout_prospects SET status = 'WITHDRAWN', prospect_id = NULL
                       WHERE draft_season = %s AND prospect_key = ANY(%s)""", (draft_season, acts["withdraw"]))
    _insert_prospects(cur, draft_season, acts["new"])
    _rerank(cur, draft_season)
    rep = {"linked": len(acts["link"]), "withdrawn": len(acts["withdraw"]), "new": len(acts["new"])}
    log(f"scouting relink {draft_season}: {rep}")
    return rep


def load_class(cur, draft_season, dry_run=False):
    """The class's scouting rows joined to the board's public fields. In a dry run against a DB without the
    migration (or an uninitialized class) the class is built in memory from draft_prospects."""
    if installed(cur):
        cur.execute("""SELECT sp.prospect_key, sp.prospect_id, sp.true_value, sp.true_rank, sp.class_year,
                              sp.conference, sp.first_step, sp.status, sp.seed, d.full_name, d.position, d.college,
                              d.age, d.height_in, d.weight, d.projected_pick
                       FROM scout_prospects sp LEFT JOIN draft_prospects d ON d.id = sp.prospect_id
                       WHERE sp.draft_season = %s ORDER BY sp.true_rank""", (draft_season,))
        rows = cur.fetchall()
        if rows:
            out = []
            for (k, pid, v, tr, cy, conf, fs, st, seed, name, pos, col, age, h, w, pp) in rows:
                if name is None:                       # withdrawn (no board row): name from the key
                    name, pos = k.split(":")[1], k.split(":")[2]
                out.append({"key": k, "prospect_id": pid, "V": float(v), "true_rank": tr, "class_year": cy,
                            "conference": conf, "first_step": fs, "status": st, "seed": seed, "full_name": name,
                            "position": pos, "group": sv.group_of(pos), "college": col, "school": sv.school(col),
                            "age": age, "height_in": h, "weight": w, "projected_pick": pp})
            return out
    if not dry_run:
        raise SystemExit(f"no scouting rows for the {draft_season} class: run init first")
    return build_class(board_rows(cur, draft_season), draft_season, current_step(cur)[1])


def stored_steps(cur, draft_season):
    if not installed(cur):
        return []
    cur.execute("SELECT step FROM scout_runs WHERE draft_season = %s ORDER BY step", (draft_season,))
    return [r[0] for r in cur.fetchall()]


def stored_consensus(cur, draft_season, step):
    cur.execute("""SELECT prospect_key, err, grade, rank FROM scout_consensus WHERE draft_season = %s AND step = %s""",
                (draft_season, step))
    return {k: {"err": float(e), "grade": float(g), "rank": r} for k, e, g, r in cur.fetchall()}


def params():
    return {"code": CODE_VERSION, "sigma_0": SIGMA_0, "sigma_draft": SIGMA_DRAFT, "lambda": LAMBDA,
            "seed_alpha": SEED_ALPHA}


def compute(cur, draft_season, step, dry_run=False):
    """Everything one step stores, computed (no writes): {cls, first_step, cons, events, prev_step}."""
    cls = load_class(cur, draft_season, dry_run)
    first = min(p["first_step"] for p in cls)
    if step < first:
        raise SystemExit(f"step {step} is before the class's first step {first}")
    steps = stored_steps(cur, draft_season)
    prev_step = max((x for x in steps if x < step), default=None)
    if prev_step is not None:
        start = (prev_step, {k: r["err"] for k, r in stored_consensus(cur, draft_season, prev_step).items()})
        prev = stored_consensus(cur, draft_season, prev_step)
    else:
        start, prev = None, None
    if prev_step is not None and prev_step != step - 1 and not dry_run:
        raise SystemExit(f"step {step - 1} isn't stored: run the missing steps in order first")
    if prev_step is None and step != first and not dry_run:
        raise SystemExit(f"the class starts at step {first}: run it first")
    sim = chain(cls, first, step, draft_season, start=start)
    cons = sim[step]
    if prev is not None and step - 1 == prev_step:     # deltas against the stored step, exactly
        cons = consensus(cls, {k: r["err"] for k, r in cons.items()}, prev)
    elif step - 1 in sim:
        cons = consensus(cls, {k: r["err"] for k, r in cons.items()}, sim[step - 1])
    return {"cls": cls, "first_step": first, "cons": cons, "events": step_events(cls, cons, step, draft_season),
            "prev_step": prev_step}


def _write_step(cur, draft_season, step, season, out, keep_llm=None):
    s_season, week, stage = step_when(step, season)
    n_events = sum(len(v) for v in out["events"].values())
    cur.execute("""INSERT INTO scout_runs (draft_season, step, season, week, stage, params, n_events)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (draft_season, step, s_season, week, stage, json.dumps(out.get("params") or params()), n_events))
    ids = {}
    for key, evs in out["events"].items():
        for e in evs:
            head, blurb, src = e["headline"], e["blurb"], e.get("text_source", "TEMPLATE")
            kept = (keep_llm or {}).get((key, e["kind"], e["seq"]))
            if kept:
                head, blurb, src = kept
            cur.execute("""INSERT INTO scout_events (draft_season, step, prospect_key, kind, team_id, payload, impact,
                               headline, blurb, text_source, seq)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (draft_season, step, key, e["kind"], e.get("team_id"), json.dumps(e["payload"]),
                         e.get("impact"), head, blurb, src, e["seq"]))
            ids.setdefault(key, []).append(cur.fetchone()[0])
    cur.executemany(
        """INSERT INTO scout_consensus (draft_season, step, prospect_key, season, week, stage, grade, rank, round_label,
               delta_grade, delta_rank, err, event_ids)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        [(draft_season, step, k, s_season, week, stage, r["grade"], r["rank"], r["round_label"], r["delta_grade"],
          r["delta_rank"], r["err"], ids.get(k, [])) for k, r in out["cons"].items()])
    for row in out.get("boards") or []:
        cur.execute("""INSERT INTO scout_team_boards (draft_season, step, team_id, prospect_key, team_grade, team_rank,
                           fit, bias, err) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""", row)
    return n_events


def run(cur, draft_season, step, dry_run=False, replace=False, log=print, extras=None, llm=None):
    """Store one scouting step (idempotent: a stored step is skipped unless replace, which redraws it with the same
    seeds and keeps any LLM text). extras(cur, out, step, season) adds VISIT events and team boards; llm (gm_llm,
    label scouting_notes) rewrites up to 8 movers' notes (scouting_narrative; never in a dry run).
    Returns the computed step."""
    season = current_step(cur)[0]
    stored = step in stored_steps(cur, draft_season)
    if stored and not replace and not dry_run:
        log(f"scouting {draft_season} step {step}: already stored (use --replace to redraw)")
        return None
    keep = {}
    if stored and replace and not dry_run:
        cur.execute("""SELECT prospect_key, kind, seq, headline, blurb, text_source FROM scout_events
                       WHERE draft_season = %s AND step = %s AND text_source = 'LLM'""", (draft_season, step))
        keep = {(k, kind, seq): (h, b, src) for k, kind, seq, h, b, src in cur.fetchall()}
        cur.execute("DELETE FROM scout_runs WHERE draft_season = %s AND step = %s", (draft_season, step))
    out = compute(cur, draft_season, step, dry_run)
    out["params"] = params()
    if extras:
        extras(cur, out, step, season)
    if llm is not None and not dry_run:
        from engines.scouting_narrative import write_notes
        n = write_notes(out, llm, log=log)
        if n:
            log(f"  scouting notes: {n} written by Gemini")
    if dry_run:
        report(out, step, log)
        log("  dry run: nothing written")
        return out
    n = _write_step(cur, draft_season, step, season, out, keep)
    log(f"scouting {draft_season} step {step}: {len(out['cons'])} prospects, {n} events stored")
    return out


def team_contexts(cur, season):
    """{team_id: scouting_boards.team_context} from the GM agents (step 3); {} without them."""
    from engines import scouting_boards as sb
    cur.execute("SELECT to_regclass('gm_profiles') IS NOT NULL")
    if not cur.fetchone()[0]:
        return {}
    from engines.front_office import get_gm
    cur.execute("SELECT id FROM teams ORDER BY id")
    out = {}
    for (tid,) in cur.fetchall():
        g = get_gm(cur, tid, season)
        if g:
            out[tid] = sb.team_context(g)
    return out


def volatility(cur, draft_season, step, cons):
    """{key: sd of the prospect's consensus moves} through `step` (stored history plus this step's move)."""
    hist = {}
    if installed(cur):
        cur.execute("""SELECT prospect_key, step, err FROM scout_consensus WHERE draft_season = %s AND step < %s
                       ORDER BY step""", (draft_season, step))
        for k, _, e in cur.fetchall():
            hist.setdefault(k, []).append(float(e))
    out = {}
    for k, r in cons.items():
        errs = hist.get(k, []) + [r["err"]]
        moves = [b - a for a, b in zip(errs, errs[1:])]
        out[k] = math.sqrt(sum(m * m for m in moves) / len(moves)) if moves else 0.0
    return out


def stored_visits(cur, draft_season, step):
    """{team_id: {key}} of VISIT events stored before `step`."""
    out = {}
    if installed(cur):
        cur.execute("""SELECT team_id, prospect_key FROM scout_events
                       WHERE draft_season = %s AND kind = 'VISIT' AND step < %s""", (draft_season, step))
        for tid, k in cur.fetchall():
            out.setdefault(tid, set()).add(k)
    return out


BOARD_POOL = 200                      # team boards look at the consensus top 200 and store their top 100


def default_extras(cur, out, step, season):
    """Top-30 VISITs (steps 25-26) and the 32 team boards for the step (scouting_boards)."""
    from engines import scouting_boards as sb
    from engines import scouting_events as ev
    cls, cons = out["cls"], out["cons"]
    if not cls:
        return out
    draft_season = int(cls[0]["key"].split(":")[0])
    ctxs = team_contexts(cur, season)
    if not ctxs:
        return out
    by_key = {p["key"]: p for p in cls}
    visited = stored_visits(cur, draft_season, step)
    if step in (25, 26):
        cands = [{"key": k, "group": sb.needs_group(by_key[k].get("position")), "full_name": by_key[k]["full_name"]}
                 for k, r in sorted(cons.items(), key=lambda x: x[1]["rank"])[:100]]
        cur.execute("SELECT id, abbreviation, name FROM teams")
        names = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
        for tid, key in ev.visits(step, {t: {"need_groups": c["need_groups"]} for t, c in ctxs.items()}, cands,
                                  draft_season):
            evs = out["events"].setdefault(key, [])
            e = dict(ev.visit_event(by_key[key], *names[tid]), team_id=tid)
            e["seq"] = sum(1 for x in evs if x["kind"] == "VISIT")
            evs.append(e)
            visited.setdefault(tid, set()).add(key)
    vols = volatility(cur, draft_season, step, cons)
    knots = sv.grade_map([p["V"] for p in cls if p.get("status") != "WITHDRAWN"])
    first = out["first_step"]
    rows = []
    for tid, ctx in ctxs.items():
        b = sb.board(cls, cons, ctx, step, draft_season, first, visited.get(tid, ()), vols, limit=BOARD_POOL,
                     draft_day=(step == STEP_DRAFT))
        for rank, (key, total, err, fit, bias) in enumerate(b[:sb.STORED], start=1):
            rows.append((draft_season, step, tid, key, sv.grade(total, knots), rank, round(fit, 2), round(bias, 2),
                         round(err, 3)))
    out["boards"] = rows
    out["params"] = dict(out.get("params") or params(), boards={"sigma_base": sb.SIGMA_BASE, "rho": sb.RHO,
                                                                "sigma_res": sb.SIGMA_RES, "phi": sb.PHI,
                                                                "reach_limit": sb.REACH_LIMIT})
    return out


def report(out, step, log=print, top=32):
    """The dry-run printout: consensus top N, risers and fallers, sample events. Public fields only."""
    by_key = {p["key"]: p for p in out["cls"]}
    cons = out["cons"]
    log(f"step {step} consensus top {top}:")
    for k, r in sorted(cons.items(), key=lambda x: x[1]["rank"])[:top]:
        p = by_key[k]
        d = f"{r['delta_rank']:+d}" if r["delta_rank"] is not None else "new"
        log(f"  {r['rank']:>3} {r['grade']:5.1f} {d:>4}  {p['full_name']:<24}{p['position'] or '?':<5}{p['college'] or ''}")
    up, down = risers_fallers(cons)
    for title, rows in (("risers", up), ("fallers", down)):
        log(f"{title}:")
        for k, r in rows:
            head = next((e["headline"] for e in out["events"].get(k, [])), "quiet week")
            log(f"  {r['delta_rank']:+4d} to {r['rank']:>3}  {by_key[k]['full_name']:<24} {head}")
    evs = [e for v in out["events"].values() for e in v]
    kinds = {}
    for e in evs:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    log(f"events: {len(evs)} {kinds}")
    for e in evs[:6]:
        log(f"  [{e['kind']}] {e['headline']} | {e['blurb']}")


def catch_up(cur, draft_season, to_step, log=print, extras=None, dry_run=False, llm=None):
    """Run every missing step from the class's first (or the last stored + 1) through to_step, in order."""
    steps = stored_steps(cur, draft_season)
    cls_first = None
    if not steps:
        cur.execute("SELECT min(first_step) FROM scout_prospects WHERE draft_season = %s", (draft_season,))
        cls_first = cur.fetchone()[0]
        if cls_first is None:
            return []
    start = (max(steps) + 1) if steps else cls_first
    done = []
    for s in range(start, to_step + 1):
        run(cur, draft_season, s, dry_run=dry_run, log=log, extras=extras, llm=llm)
        done.append(s)
    return done


def chain_stats(cls, first_step, draft_season, sims=20):
    """sd(e) by step and the mean count of prospects moving >= 5 ranks a step, over `sims` fresh chains."""
    sd_by, moves_by = {}, {}
    for i in range(sims):
        sim = chain(cls, first_step, STEP_DRAFT, draft_season, salt=f"cal{i}")
        for step, cons in sim.items():
            errs = [r["err"] for r in cons.values()]
            m = sum(errs) / len(errs)
            sd_by.setdefault(step, []).append(math.sqrt(sum((e - m) ** 2 for e in errs) / len(errs)))
            moves_by.setdefault(step, []).append(sum(1 for r in cons.values()
                                                     if r["delta_rank"] is not None and abs(r["delta_rank"]) >= 5))
    return ({s: sum(v) / len(v) for s, v in sd_by.items()}, {s: sum(v) / len(v) for s, v in moves_by.items()})


def calibrate_cli(cur, draft_season=None, drafts=1000, q_mode="live", seed=0, log=print):
    """--calibrate: the class from the DB (scouting rows, or built from draft_prospects), today's Round 1 owners
    (draft_order_with_owners) and the 32 GMs' needs, personas and q. Prints aggregates only."""
    from engines import scouting_boards as sb
    from engines.draft_order import draft_order_with_owners
    season = current_step(cur)[0]
    draft_season = draft_season or season + 1
    cls = load_class(cur, draft_season, dry_run=True)
    ctxs = team_contexts(cur, season)
    if q_mode == "spread":
        ctxs = sb.spread_q(ctxs)
    order = draft_order_with_owners(cur, draft_season)
    r = sb.calibrate(cls, order, ctxs, drafts=drafts, seed=seed)
    log(f"calibrate {draft_season}: {drafts} drafts, q {q_mode}")
    log(f"  poor picks (true rank > slot + {sb.POOR_GAP}): {r['poor_rate']:.1%} "
        f"(target {sb.CALIBRATION['target'][0]:.0%}-{sb.CALIBRATION['target'][1]:.0%})")
    for band, v in r["bands"].items():
        log(f"    q {band:<10} {v['poor_rate']:.1%} of {v['picks']} picks")
    log(f"  mean |true rank - slot|: {r['mean_abs_gap']:.1f}")
    log(f"  true top 32 drafted in Round 1: {r['r1_overlap_true_top32']:.1f}; in the draft-day consensus top 32: "
        f"{r['consensus_overlap_true_top32']:.1f}")
    sds, moves = chain_stats(cls, min(p["first_step"] for p in cls), draft_season)
    log("  sd(e) by step: " + ", ".join(f"{s}:{v:.2f}" for s, v in sorted(sds.items())))
    log("  prospects moving >= 5 ranks: " + ", ".join(f"{s}:{v:.0f}" for s, v in sorted(moves.items()) if s > min(sds)))
    return r


# -------------------------------------------------------------------- cli ----
def _connect():
    import psycopg
    from pipeline.load_capture import DSN
    return psycopg.connect(DSN)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Scouting engine (team management step 4)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="create a draft class's scouting rows")
    p.add_argument("--draft-season", type=int, required=True)
    p.add_argument("--step", type=int, help="first step (default: the league's current step)")
    p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("relink", help="re-link a class to draft_prospects after a board load")
    p.add_argument("--draft-season", type=int, required=True)
    p = sub.add_parser("calibrate", help="simulate drafts and print the poor-pick rate (writes nothing)")
    p.add_argument("--drafts", type=int, default=1000)
    p.add_argument("--class", dest="klass", type=int)
    p.add_argument("--q", choices=("live", "spread"), default="live")
    p.add_argument("--seed", type=int, default=0)
    p = sub.add_parser("run", help="store one scouting step (default: the league's current step)")
    p.add_argument("--draft-season", type=int, help="default: league season + 1")
    p.add_argument("--week", type=int)
    p.add_argument("--stage", type=int, help="offseason stage 1-4 (step 23 + stage)")
    p.add_argument("--step", type=int)
    p.add_argument("--dry-run", action="store_true", help="compute and print, write nothing")
    p.add_argument("--replace", action="store_true", help="redraw a stored step (same seeds)")
    ap.add_argument("--calibrate", action="store_true", help="simulate drafts: the poor-pick rate (no writes)")
    ap.add_argument("--drafts", type=int, default=1000)
    ap.add_argument("--class", dest="klass", type=int, help="draft class (default: league season + 1)")
    ap.add_argument("--q", choices=("live", "spread"), default="live")
    ap.add_argument("--seed", type=int, default=0)
    if argv is None:
        argv = sys.argv[1:]
    if "--calibrate" in argv:                      # python3 -m engines.scouting_engine --calibrate [...]
        argv = ["calibrate"] + [x for x in argv if x != "--calibrate"]
    a = ap.parse_args(argv)
    if a.cmd == "calibrate":
        with _connect() as conn:
            calibrate_cli(conn.cursor(), a.klass, a.drafts, a.q, a.seed)
            conn.rollback()
        return 0
    with _connect() as conn:
        cur = conn.cursor()
        if a.cmd == "init":
            init(cur, a.draft_season, a.step, a.dry_run)
        elif a.cmd == "relink":
            relink(cur, a.draft_season)
        elif a.cmd == "run":
            season, cur_step = current_step(cur)
            step = a.step or (step_for(a.week or 24, a.stage) if (a.week or a.stage) else cur_step)
            run(cur, a.draft_season or season + 1, step, a.dry_run, a.replace, extras=default_extras)
        if getattr(a, "dry_run", False):
            conn.rollback()
        else:
            conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
