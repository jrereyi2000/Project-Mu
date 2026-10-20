"""
Scouting engine (docs/team_management/04_SCOUTING_AND_DRAFT_PLAN.md §2.1-§2.5; PROJECT_SPEC §2.18, §3.3g).

A draft class's scouting rows (scout_prospects) carry each prospect's hidden true value V (engines/scouting_value.py).
Everything keys on prospect_key, because a Draft Board re-scan replaces draft_prospects ids (relink re-points them).

Steps: one scouting run each. In-season step = the league week; offseason step = 23 + the GM offseason stage
(24 post-Super Bowl .. 27 draft). A class's first step is the step it was initialized at (the 2037 class: 4).

CLI:
    python3 -m engines.scouting_engine init --draft-season 2037 [--dry-run]
    python3 -m engines.scouting_engine relink --draft-season 2037
"""
import argparse
import hashlib
import json
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
    a = ap.parse_args(argv)
    with _connect() as conn:
        cur = conn.cursor()
        if a.cmd == "init":
            init(cur, a.draft_season, a.step, a.dry_run)
        elif a.cmd == "relink":
            relink(cur, a.draft_season)
        if getattr(a, "dry_run", False):
            conn.rollback()
        else:
            conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
