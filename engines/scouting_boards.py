"""
Team boards and the draft simulation behind --calibrate (docs/team_management/04_SCOUTING_AND_DRAFT_PLAN.md §2.3, §2.5;
PROJECT_SPEC §2.18). Pure: no DB.

Each front office sees T = V + e_team, plus fit and bias:
    e_team = SIGMA_BASE * q * m * (RHO * zC + sqrt(1 - RHO^2) * z_team)   (+ SIGMA_RES * q * m * r on draft day)
  zC = the consensus error now over its sd across the class (front offices read the same tape and media, so the
  consensus's mistakes, Madden's board included, bleed into theirs); z_team = the team's own AR(1) misread, seeded
  per (team, prospect); m = VISIT_M after a top-30 visit; q = fo_quality (sharper front offices err less).
  fit = PHI * the team's normalized need for the group (x1.2 for a CONTEND team with a starter gap there; a REBUILD
  team leans young); bias = position_bias scaling (T - 60), youth_bias * (22 - age) * 0.75, risk_appetite on the
  prospect's volatility. QBs instead get the QB rule, graded by how secure the starter is (qb_security, s in 0-1:
  his rank among the league's QB1s x an age factor x a contract factor): fit = QB_PHI * (1 - s) and
  bias -= QB_PENALTY * s, so a team with a weak, old or expiring starter pushes QBs up and only a team with a
  young, top-tier, signed starter marks them all the way down.
A Round 1 pick is argmax(T + fit + bias) over the prospects still available inside the consensus top REACH_LIMIT
(a prospect Madden and the media project far later doesn't go in Round 1, K1).
"""
import math
import random

from engines import scouting_value as sv

# Frozen by --calibrate (2026-10-20, 1000 drafts on the 2037 class at 2036 week 4; see CALIBRATION). The graded QB
# rule (QB_PHI, QB_PENALTY, qb_security) was re-checked against the same band on 2026-10-24 (spec §2.18).
SIGMA_BASE = 5.5
RHO = 0.5
SIGMA_RES = 2.0
PHI = 3.0
LAMBDA_TEAM = 0.15
VISIT_M = 0.75
QB_PENALTY = 8.0                    # marked down this much at s = 1 (a young, top-tier, signed starter)
QB_PHI = 4.0                        # QB fit at s = 0 (no starter, or a bottom-quarter one)
QB_RANK_LO, QB_RANK_HI = 0.25, 0.85 # QB1 league percentile: at or under LO counts 0, at or over HI counts 1
QB_AGE_FULL, QB_AGE_ZERO = 30, 36   # age factor 1 through 30, 0 from 36
QB_CONTRACT = {0: 0.6, 1: 0.9}      # contract years left after this season (no row counts as 0); 2+ = 1.0
RISK_W = 1.5
REACH_LIMIT = 80
STORED = 100
POOR_GAP = 32                       # a poor pick: true rank more than 32 spots worse than the slot
CALIBRATION = {"date": "2026-10-20", "class": 2037, "drafts": 1000, "target": (0.20, 0.30)}   # results: spec §2.18


def needs_group(position):
    from engines.gm_direction import position_group
    return position_group(position)


def qb_security(qb):
    """How secure a team's QB1 makes it, 0 (no starter, or a bottom-quarter, old or unsigned one) to 1 (young,
    top-tier, signed for 2+ more seasons). qb: {ovr, age, pct (his percentile among the 32 QB1s, 0-1),
    years_left (contract seasons after this one, extension included; None = no contract row, counted as 0)};
    None or {} = no QB1."""
    if not qb:
        return 0.0
    quality = min(1.0, max(0.0, (float(qb.get("pct") or 0) - QB_RANK_LO) / (QB_RANK_HI - QB_RANK_LO)))
    age = qb.get("age")
    age_f = 1.0 if age is None else min(1.0, max(0.0, (QB_AGE_ZERO - int(age)) / (QB_AGE_ZERO - QB_AGE_FULL)))
    yl = qb.get("years_left")
    contract = QB_CONTRACT[0] if yl is None else QB_CONTRACT.get(max(int(yl), 0), 1.0)
    return round(quality * age_f * contract, 3)


def team_context(gm, qb=None):
    """gm: front_office.get_gm (or the fixture's subset); qb: the team's QB1 (qb_security), falling back to
    gm["qb"]. -> the board's view of the team. Without QB1 facts, s is 1 unless QB is a top-4 need with a
    starter gap (then 0)."""
    needs = gm.get("needs") or []
    top = max((float(n.get("score") or 0) for n in needs), default=0.0) or 1.0
    norm = {n["group"]: (max(float(n.get("score") or 0), 0.0) / top, float(n.get("slot_gap") or 0)) for n in needs}
    ranked = [n["group"] for n in needs]
    qb = qb if qb is not None else gm.get("qb")
    if qb is not None:
        sec = qb_security(qb)
    else:
        n = norm.get("QB")
        sec = 0.0 if n and n[1] > 0 and "QB" in ranked[:4] else 1.0
    return {"team_id": gm.get("team_id"), "abbr": gm.get("abbreviation"), "q": float(gm.get("fo_quality") or 1.0),
            "direction": gm.get("direction") or "RETOOL", "needs": norm, "need_groups": ranked, "qb": qb,
            "qb_security": sec,
            "position_bias": {k: float(v) for k, v in (gm.get("position_bias") or {}).items()},
            "youth_bias": float(gm.get("youth_bias") or 0.0), "risk": float(gm.get("risk_appetite") or 0.5),
            "user_controlled": bool(gm.get("user_controlled"))}


def fit_bias(p, T, ctx, vol=0.0, vol_ref=0.0):
    """(fit, bias) in OVR points for prospect p (needs V, position, age) on team ctx at team value T."""
    g = needs_group(p.get("position"))
    age = int(p.get("age") or 22)
    need, gap = ctx["needs"].get(g, (0.0, 0.0))
    sec = ctx.get("qb_security", 1.0)
    if g == "QB":
        fit = QB_PHI * (1.0 - sec)
    else:
        fit = PHI * need * (1.2 if ctx["direction"] == "CONTEND" and gap > 0 else 1.0)
    if ctx["direction"] == "REBUILD":
        fit += 0.5 * (22 - age)
    pb = ctx["position_bias"].get(g, ctx["position_bias"].get(sv.group_of(p.get("position")), 1.0))
    bias = (T - 60.0) * (pb - 1.0) + ctx["youth_bias"] * (22 - age) * 0.75 + (ctx["risk"] - 0.5) * RISK_W * (vol - vol_ref)
    if g == "QB":
        bias -= QB_PENALTY * sec
    return fit, bias


def team_error(q, zc, z_team, resid=0.0, visited=False):
    m = VISIT_M if visited else 1.0
    return SIGMA_BASE * q * m * (RHO * zc + math.sqrt(1 - RHO * RHO) * z_team) + SIGMA_RES * q * m * resid


def team_z(draft_season, team_id, key, first_step, step, salt=""):
    """The team's own AR(1) misread of a prospect at `step` (unit variance), seeded per (team, prospect)."""
    rng = random.Random(f"team:{draft_season}:{team_id}:{key}:{salt}")
    a = 1 - LAMBDA_TEAM
    z = rng.gauss(0, 1)
    for _ in range(first_step + 1, step + 1):
        z = a * z + math.sqrt(1 - a * a) * rng.gauss(0, 1)
    return z


def residual(draft_season, team_id, key, salt=""):
    return random.Random(f"resid:{draft_season}:{team_id}:{key}:{salt}").gauss(0, 1)


def board(cls, cons, ctx, step, draft_season, first_step, visited=(), vols=None, limit=None, draft_day=False):
    """One team's board at `step`: [(key, total, err, fit, bias)] best first, over the consensus top `limit`
    (default all). cons: the step's consensus {key: {err, rank}}."""
    errs = [r["err"] for r in cons.values()]
    m = sum(errs) / len(errs)
    sd = math.sqrt(sum((e - m) ** 2 for e in errs) / max(len(errs) - 1, 1)) or 1.0
    vols = vols or {}
    vref = sorted(vols.values())[len(vols) // 2] if vols else 0.0
    out = []
    for p in cls:
        r = cons.get(p["key"])
        if r is None or (limit and r["rank"] > limit):
            continue
        z = team_z(draft_season, ctx["team_id"], p["key"], first_step, step)
        res = residual(draft_season, ctx["team_id"], p["key"]) if draft_day else 0.0
        e = team_error(ctx["q"], (r["err"] - m) / sd, z, res, p["key"] in visited)
        T = p["V"] + e
        fit, bias = fit_bias(p, T, ctx, vols.get(p["key"], vref), vref)
        out.append((p["key"], T + fit + bias, e, fit, bias))
    out.sort(key=lambda x: (-x[1], x[0]))
    return out


def recommend(rows, cons, available, n_alt=3):
    """(best, alternatives) from a team's board rows: the best available inside the consensus top REACH_LIMIT
    (if none is, the best available at all)."""
    pool = [r for r in rows if r[0] in available and cons[r[0]]["rank"] <= REACH_LIMIT] or \
           [r for r in rows if r[0] in available]
    return (pool[0], pool[1:1 + n_alt]) if pool else (None, [])


# ------------------------------------------------------------ calibration ----
def draft_day_errors(cls, gaps, rng, sigma_draft, decay, alpha):
    """A fresh draft-day consensus error per prospect: the decayed Madden seed plus the step-27 noise, which is
    exactly sd sigma_draft by the schedule (scouting_engine.sigma_at). Draws in class order from rng."""
    return {p["key"]: decay * alpha * gaps[p["key"]] + sigma_draft * rng.gauss(0, 1) for p in cls}


def simulate_draft(cls, gaps, order, ctxs, rng, sigma_draft, decay, alpha, visits=None, top=150):
    """One simulated Round 1. order: [{pick, owner_team_id}]; ctxs: {team_id: team_context}. Returns
    ([{pick, team_id, key, true_rank, cons_rank, q}], the draft-day consensus {key: {err, rank}})."""
    err = draft_day_errors(cls, gaps, rng, sigma_draft, decay, alpha)
    c = {p["key"]: p["V"] + err[p["key"]] for p in cls}
    ranked = sorted(c, key=lambda k: (-c[k], k))
    cons = {k: {"err": err[k], "rank": i + 1} for i, k in enumerate(ranked)}
    m = sum(err.values()) / len(err)
    sd = math.sqrt(sum((e - m) ** 2 for e in err.values()) / max(len(err) - 1, 1)) or 1.0
    pool = [p for p in cls if cons[p["key"]]["rank"] <= top]
    by_key = {p["key"]: p for p in cls}
    visits = visits or {}
    boards = {}
    for tid, ctx in ctxs.items():
        rows = []
        vset = visits.get(tid, ())
        for p in pool:
            e = team_error(ctx["q"], (err[p["key"]] - m) / sd, rng.gauss(0, 1), rng.gauss(0, 1), p["key"] in vset)
            T = p["V"] + e
            fit, bias = fit_bias(p, T, ctx)
            rows.append((p["key"], T + fit + bias, e, fit, bias))
        rows.sort(key=lambda x: (-x[1], x[0]))
        boards[tid] = rows
    available = {p["key"] for p in pool}
    picks = []
    for slot in order:
        tid = slot["owner_team_id"]
        best, _ = recommend(boards[tid], cons, available)
        available.discard(best[0])
        picks.append({"pick": slot["pick"], "team_id": tid, "key": best[0], "true_rank": by_key[best[0]]["true_rank"],
                      "cons_rank": cons[best[0]]["rank"], "q": ctxs[tid]["q"]})
    return picks, cons


def planned_visits(cls, ctxs, draft_season):
    """{team_id: {key}}: the visits each team would log at steps 25-26 (scouting_events.visits), drawn from
    Madden's top 100 (calibration's stand-in for the consensus top 100)."""
    from engines import scouting_events as se_ev
    cands = sorted(cls, key=lambda p: p.get("projected_pick") or 9999)[:100]
    cands = [{"key": p["key"], "group": needs_group(p.get("position")), "full_name": p["full_name"]} for p in cands]
    teams = {tid: {"need_groups": c["need_groups"]} for tid, c in ctxs.items()}
    out = {}
    for step in (25, 26):
        for tid, key in se_ev.visits(step, teams, cands, draft_season):
            out.setdefault(tid, set()).add(key)
    return out


def is_poor(pick):
    return pick["true_rank"] > pick["pick"] + POOR_GAP


def q_band(q):
    return "<=0.85" if q <= 0.85 else ">=1.15" if q >= 1.15 else "0.85-1.15"


def spread_q(ctxs):
    """q spread evenly 0.7-1.4 over the teams (by team id), for --q spread."""
    ids = sorted(ctxs)
    n = len(ids)
    return {tid: dict(ctxs[tid], q=round(0.7 + 0.7 * i / max(n - 1, 1), 3)) for i, tid in enumerate(ids)}


def calibrate(cls, order, ctxs, drafts=1000, seed=0, sigma_draft=None, decay=None, alpha=None, visits=None):
    """Poor-pick rate overall and by q band, mean |true rank - slot|, consensus/true top-32 overlap."""
    from engines import scouting_engine as se
    sigma_draft = se.SIGMA_DRAFT if sigma_draft is None else sigma_draft
    alpha = se.SEED_ALPHA if alpha is None else alpha
    if decay is None:
        first = min(p["first_step"] for p in cls)
        decay = (1 - se.LAMBDA) ** (se.STEP_DRAFT - first)
    gaps = se.board_gaps(cls)
    if visits is None:
        visits = planned_visits(cls, ctxs, cls[0]["key"].split(":")[0] if cls else 0)
    true_top = {p["key"] for p in cls if p["true_rank"] <= 32}
    rng = random.Random(f"calibrate:{seed}")
    poor = total = 0
    bands = {}
    gap_sum = 0.0
    overlap = cons_overlap = 0
    for _ in range(drafts):
        picks, cons = simulate_draft(cls, gaps, order, ctxs, rng, sigma_draft, decay, alpha, visits)
        for pk in picks:
            bad = is_poor(pk)
            poor += bad
            total += 1
            gap_sum += abs(pk["true_rank"] - pk["pick"])
            b = bands.setdefault(q_band(pk["q"]), [0, 0])
            b[0] += bad
            b[1] += 1
        overlap += len(true_top & {pk["key"] for pk in picks})
        cons_overlap += sum(1 for k in true_top if cons[k]["rank"] <= 32)
    return {"drafts": drafts, "poor_rate": poor / total, "mean_abs_gap": gap_sum / total,
            "r1_overlap_true_top32": overlap / drafts, "consensus_overlap_true_top32": cons_overlap / drafts,
            "bands": {k: {"poor_rate": v[0] / v[1], "picks": v[1]} for k, v in sorted(bands.items())}}
