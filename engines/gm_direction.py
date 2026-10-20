"""
Team direction and positional needs (docs/team_management/03_GM_AGENTS_PLAN.md §2.2, Phase 2; PROJECT_SPEC §2.16).

Pure parts: direction_scores (the league's D values), assign_directions (bands, overrides, hysteresis),
starter_slots and positional_needs. The loader reads team_seasons, rosters, injuries and contracts and writes
gm_team_seasons: the direction only at checkpoint weeks (or when a team has none yet), the needs every run.

  python3 -m engines.gm_direction [--season 2036] [--week W] [--dry-run]
"""
import argparse
import json
import math
import os
from statistics import mean, pstdev

TRADE_DEADLINE_WEEK = 8                  # NFL deadline (sub-plan §1.1 C1); not Napper's fantasy deadline
CHECKPOINT_WEEKS = {1, 6, TRADE_DEADLINE_WEEK - 1, 24}   # 24 = the offseason (post-season re-check)
SEASON_GAMES = 17
PRIOR_GAMES = 6                          # p = g/(g+6) form + 6/(g+6) prior
CONTEND_N, REBUILD_N = 10, 8
HYSTERESIS = 0.3
ALWAYS_CONTEND_WINS = 10.5
STARTER_AGE_PIVOT = 27.5
INJURY_OUT_WEEKS = 3                     # a starter out this long (or on IR) doesn't count toward slots
EXPIRING_WEIGHT = 0.5
EXPIRING_REBUILD_MULT = 1.5
SLOT_PERCENTILE = 0.60
SURPLUS_WITHIN = 3
EMPTY_SLOT_OVR = 40

# Starter slots per group (S10's group map, with K and P apart).
SLOTS = {"QB": 1, "RB": 1, "WR": 3, "TE": 1, "OT": 2, "IOL": 3, "EDGE": 2, "DT": 2, "LB": 2, "CB": 3, "S": 2,
         "K": 1, "P": 1}
# Veteran trade weight per group: starts from the mock draft's POSITIONAL_PREMIUM, with its own constant because
# veteran value isn't draft value.
TRADE_POS_WEIGHT = {"QB": 1.25, "OT": 1.12, "EDGE": 1.12, "CB": 1.10, "WR": 1.02, "DT": 1.00, "TE": 0.90,
                    "IOL": 0.88, "LB": 0.85, "S": 0.82, "RB": 0.80, "K": 0.30, "P": 0.25}


def position_group(pos):
    from engines.mock_draft_engine import POSITION_GROUP_MAP
    if pos in ("K", "P"):
        return pos
    g = POSITION_GROUP_MAP.get((pos or "").upper())
    return None if g in (None, "SPECIALISTS") else g


def is_checkpoint(week):
    return week in CHECKPOINT_WEEKS


# --------------------------------------------------------------- direction ----
def _z(values):
    m = mean(values)
    sd = pstdev(values)
    return [0.0 if sd == 0 else (v - m) / sd for v in values]


def _pct_rank(values):
    """Each value's rank fraction in [0, 1] (ties share the average rank)."""
    n = len(values)
    out = []
    for v in values:
        below = sum(x < v for x in values)
        eq = sum(x == v for x in values)
        out.append((below + (eq - 1) / 2) / (n - 1) if n > 1 else 0.5)
    return out


def direction_scores(teams):
    """teams: [{team_id, wins, losses, pythagorean_wins, expected_wins, prior_wins, team_ovr, opp_adj_credit_recent,
    trajectory_multiplier, starter_age, aggressiveness}] -> {team_id: {D, proj_wins, p, form, prior, ...}}.

    g = wins + losses; form = (pyth + expected)/(2g); prior = 0.5 prior_wins/17 + 0.5 pct_rank(team_ovr);
    p = g/(g+6) form + 6/(g+6) prior; proj_wins = wins + p (17 - g);
    D = z(proj_wins) + 0.5 z(team_ovr) + 0.25 z(oacr) + 4 (T - 1) - 0.3 max(0, starter_age - 27.5)
        + 0.2 (aggressiveness - 0.5)  (the persona tilt)."""
    ovr = [t.get("team_ovr") or 0 for t in teams]
    ranks = _pct_rank(ovr)
    rows = []
    for t, r in zip(teams, ranks):
        g = (t.get("wins") or 0) + (t.get("losses") or 0)
        prior_w = t.get("prior_wins")
        prior = 0.5 * ((prior_w if prior_w is not None else SEASON_GAMES / 2) / SEASON_GAMES) + 0.5 * r
        form = ((t.get("pythagorean_wins") or 0) + (t.get("expected_wins") or 0)) / (2 * g) if g else prior
        p = (g / (g + PRIOR_GAMES)) * form + (PRIOR_GAMES / (g + PRIOR_GAMES)) * prior
        proj = (t.get("wins") or 0) + p * max(0, SEASON_GAMES - g)
        rows.append({"team_id": t["team_id"], "g": g, "form": round(form, 4), "prior": round(prior, 4),
                     "p": round(p, 4), "proj_wins": round(proj, 3)})
    zp = _z([r["proj_wins"] for r in rows])
    zo = _z(ovr)
    zc = _z([t.get("opp_adj_credit_recent") or 0 for t in teams])
    out = {}
    for t, r, a, b, c in zip(teams, rows, zp, zo, zc):
        traj = t.get("trajectory_multiplier") or 1.0
        age = t.get("starter_age") or STARTER_AGE_PIVOT
        tilt = 0.2 * ((t.get("aggressiveness") if t.get("aggressiveness") is not None else 0.5) - 0.5)
        d = a + 0.5 * b + 0.25 * c + 4 * (traj - 1) - 0.3 * max(0.0, age - STARTER_AGE_PIVOT) + tilt
        out[t["team_id"]] = dict(r, D=round(d, 4), z_proj=round(a, 3), z_ovr=round(b, 3), z_oacr=round(c, 3),
                                 trajectory=traj, starter_age=round(age, 2), tilt=round(tilt, 3))
    return out


def band_edges(scores):
    """(hi, lo): the D edge between CONTEND and RETOOL (between the 10th and 11th team) and between RETOOL and
    REBUILD (between the 8th-lowest and the next)."""
    ds = sorted((s["D"] for s in scores.values()), reverse=True)
    n = len(ds)
    hi = (ds[CONTEND_N - 1] + ds[CONTEND_N]) / 2 if n > CONTEND_N else -math.inf
    k = n - REBUILD_N
    lo = (ds[k - 1] + ds[k]) / 2 if 0 < k < n else math.inf
    return hi, lo


def _band(d, hi, lo):
    return "CONTEND" if d > hi else "REBUILD" if d < lo else "RETOOL"


def assign_directions(scores, previous=None, cap_space=None, edges=None):
    """{team_id: (direction, reason)}. Bands by D (edges from band_edges); with a previous direction a team only
    changes when D crosses an edge by more than HYSTERESIS. Overrides last: proj_wins >= 10.5 is always CONTEND,
    and a team over the cap (cap_space < 0) can't be REBUILD (it becomes RETOOL)."""
    previous = previous or {}
    cap_space = cap_space or {}
    hi, lo = edges or band_edges(scores)
    out = {}
    for tid, s in scores.items():
        prev = previous.get(tid)
        d = s["D"]
        if prev == "CONTEND":
            band = _band(d, hi - HYSTERESIS, lo - HYSTERESIS)
        elif prev == "REBUILD":
            band = _band(d, hi + HYSTERESIS, lo + HYSTERESIS)
        elif prev == "RETOOL":
            band = _band(d, hi + HYSTERESIS, lo - HYSTERESIS)
        else:
            band = _band(d, hi, lo)
        reason = "band" if prev is None else ("held" if band == prev else "changed")
        if s["proj_wins"] >= ALWAYS_CONTEND_WINS and band != "CONTEND":
            band, reason = "CONTEND", "proj_wins >= 10.5"
        if band == "REBUILD" and (cap_space.get(tid) is not None and cap_space[tid] < 0):
            band, reason = "RETOOL", "over the cap"
        out[tid] = (band, reason)
    return out


# ------------------------------------------------------------------ needs ----
def starter_slots(players, injured=()):
    """players: [{id, group, ovr, age}] of one team -> {group: [starters, best first]} (healthy only), plus the
    depth beyond the slots under '_depth'. injured: player ids that don't count."""
    by = {}
    for p in players:
        if p.get("group") in SLOTS:
            by.setdefault(p["group"], []).append(p)
    out, depth = {}, {}
    for g, n in SLOTS.items():
        pool = sorted((p for p in by.get(g, []) if p["id"] not in injured), key=lambda p: -(p.get("ovr") or 0))
        out[g], depth[g] = pool[:n], pool[n:]
    out["_depth"] = depth
    return out


def slot_ovrs(slots):
    return {g: [p.get("ovr") or 0 for p in slots[g]] + [EMPTY_SLOT_OVR] * (n - len(slots[g]))
            for g, n in SLOTS.items()}


def league_lines(team_slot_ovrs, pct=SLOT_PERCENTILE):
    """{group: [60th-percentile OVR of slot 1, slot 2, ...]} across teams."""
    out = {}
    for g, n in SLOTS.items():
        line = []
        for i in range(n):
            vals = sorted(t[g][i] for t in team_slot_ovrs.values())
            k = pct * (len(vals) - 1)
            f, c = math.floor(k), math.ceil(k)
            line.append(vals[f] + (vals[c] - vals[f]) * (k - f))
        out[g] = line
    return out


def positional_needs(players, injured, expiring, lines, direction=None):
    """One team's ranked needs and surplus.

    For each group: slot_gap = sum over slots of max(0, league line - team slot OVR) with injured starters left
    out (out >= 3 weeks or IR); injury = the part of that gap the injuries cause; expiring = for each starter on
    an expiring deal (years_remaining 1, no extension), 0.5 x the gap his slot would have if he left (the next
    healthy player steps up), x1.5 for REBUILD and RETOOL. score = (slot_gap + expiring) x TRADE_POS_WEIGHT.
    Surplus: depth players within 3 OVR of the group's weakest starter, tradeable without opening a need.
    Returns (needs [{group, slot_gap, injury, expiring, score}] score-ranked and > 0, surplus [{group, player_ids}])."""
    healthy = starter_slots(players, injured)
    full = starter_slots(players, ())
    ovr_h, ovr_f = slot_ovrs(healthy), slot_ovrs(full)
    mult = EXPIRING_REBUILD_MULT if direction in ("REBUILD", "RETOOL") else 1.0
    needs, surplus = [], []
    for g, n in SLOTS.items():
        line = lines[g]
        gap = sum(max(0.0, line[i] - ovr_h[g][i]) for i in range(n))
        gap_full = sum(max(0.0, line[i] - ovr_f[g][i]) for i in range(n))
        exp = 0.0
        pool = healthy[g] + healthy["_depth"][g]
        for i, p in enumerate(healthy[g]):
            if p["id"] in expiring:
                rest = [q.get("ovr") or 0 for q in pool if q["id"] != p["id"]]
                rest = sorted(rest, reverse=True) + [EMPTY_SLOT_OVR] * n
                gap_without = sum(max(0.0, line[j] - rest[j]) for j in range(n))
                exp += EXPIRING_WEIGHT * max(0.0, gap_without - gap) * mult
        w = TRADE_POS_WEIGHT[g]
        score = (gap + exp) * w
        if score > 0:
            needs.append({"group": g, "slot_gap": round(gap, 2), "injury": round(max(0.0, gap - gap_full), 2),
                          "expiring": round(exp, 2), "score": round(score, 2)})
        if healthy[g] and len(healthy[g]) == n:
            floor = min(p.get("ovr") or 0 for p in healthy[g])
            extra = [p["id"] for p in healthy["_depth"][g] if (p.get("ovr") or 0) >= floor - SURPLUS_WITHIN]
            if extra:
                surplus.append({"group": g, "player_ids": extra})
    needs.sort(key=lambda x: -x["score"])
    return needs, surplus


# --------------------------------------------------------------------- DB ----
def read_league(cur, season, week):
    """Everything the direction and needs need, from the DB (read-only)."""
    cur.execute("""SELECT s.team_id, t.abbreviation, s.wins, s.losses, s.pythagorean_wins, s.expected_wins, s.team_ovr,
                          s.opp_adj_credit_recent, s.trajectory_multiplier,
                          (SELECT p.wins FROM team_seasons p WHERE p.team_id = s.team_id AND p.season = s.season - 1),
                          (SELECT c.cap_space FROM team_cap_seasons c WHERE c.team_id = s.team_id AND c.season = s.season),
                          (SELECT g.aggressiveness FROM gm_profiles g WHERE g.team_id = s.team_id AND g.end_season IS NULL),
                          (SELECT x.direction FROM gm_team_seasons x WHERE x.team_id = s.team_id AND x.season = s.season)
                   FROM team_seasons s JOIN teams t ON t.id = s.team_id WHERE s.season = %s""", (season,))
    teams = [{"team_id": r[0], "abbr": r[1], "wins": r[2], "losses": r[3], "pythagorean_wins": r[4],
              "expected_wins": r[5], "team_ovr": r[6], "opp_adj_credit_recent": r[7], "trajectory_multiplier": r[8],
              "prior_wins": r[9], "cap_space": r[10],
              "aggressiveness": float(r[11]) if r[11] is not None else None, "previous": r[12]}
             for r in cur.fetchall()]
    cur.execute("""SELECT player_id, team_id, position, overall_rating, age, dev_trait, years_of_experience
                   FROM player_seasons WHERE season = %s AND released_week IS NULL AND position <> 'DEF'
                     AND team_id IS NOT NULL""", (season,))
    roster = {}
    for pid, tid, pos, ovr, age, dev, yoe in cur.fetchall():
        roster.setdefault(tid, []).append({"id": pid, "team_id": tid, "pos": pos, "group": position_group(pos),
                                           "ovr": ovr, "age": age, "dev": dev, "yoe": yoe})
    injuries = read_injuries(cur, season, week)
    cur.execute("""SELECT player_id FROM player_contracts WHERE season = %s AND status = 'ACTIVE'
                     AND years_remaining = 1 AND extension_years IS NULL""", (season,))
    expiring = {r[0] for r in cur.fetchall()}
    return {"teams": teams, "roster": roster, "injuries": injuries, "expiring": expiring}


def read_injuries(cur, season, week):
    """{player_id: {weeks_remaining, is_ir, week}} from the latest injuries week at or before `week`."""
    cur.execute("""SELECT player_id, weeks_remaining, is_ir, week, team_id FROM injuries
                   WHERE season = %s AND player_id IS NOT NULL
                     AND week = (SELECT max(week) FROM injuries WHERE season = %s AND week <= %s)""",
                (season, season, week))
    return {r[0]: {"weeks_remaining": r[1] or 0, "is_ir": bool(r[2]), "week": r[3], "team_id": r[4]}
            for r in cur.fetchall()}


def out_ids(injuries, weeks=INJURY_OUT_WEEKS):
    return {pid for pid, i in injuries.items() if i["is_ir"] or i["weeks_remaining"] >= weeks}


def compute(league, week, force_direction=False):
    """The pure pipeline over read_league's data -> {team_id: row for gm_team_seasons}."""
    teams, roster = league["teams"], league["roster"]
    injured = out_ids(league["injuries"])
    slots = {t["team_id"]: starter_slots(roster.get(t["team_id"], []), injured) for t in teams}
    for t in teams:
        ages = [p["age"] for g in SLOTS for p in slots[t["team_id"]][g] if p.get("age")]
        t["starter_age"] = mean(ages) if ages else None
    scores = direction_scores(teams)
    previous = {t["team_id"]: t["previous"] for t in teams if t["previous"]}
    dirs = assign_directions(scores, previous, {t["team_id"]: t["cap_space"] for t in teams})
    lines = league_lines({tid: slot_ovrs(s) for tid, s in slots.items()})
    out = {}
    for t in teams:
        tid = t["team_id"]
        new_dir, reason = dirs[tid]
        keep = t["previous"] and not (force_direction or is_checkpoint(week))
        direction = t["previous"] if keep else new_dir
        needs, surplus = positional_needs(roster.get(tid, []), injured, league["expiring"], lines, direction)
        out[tid] = {"abbr": t["abbr"], "direction": direction, "set_direction": not keep,
                    "reason": "kept (not a checkpoint)" if keep else reason,
                    "D": scores[tid]["D"], "inputs": dict(scores[tid], cap_space=t["cap_space"]),
                    "needs": needs, "surplus": surplus}
    return out


def write(cur, season, week, rows):
    for tid, r in rows.items():
        cur.execute("""INSERT INTO gm_team_seasons (team_id, season, gm_id, direction, direction_score, direction_week,
                                                    direction_inputs, needs, needs_week, surplus, updated_at)
                       VALUES (%s, %s, (SELECT id FROM gm_profiles WHERE team_id = %s AND end_season IS NULL),
                               %s, %s, %s, %s, %s, %s, %s, now())
                       ON CONFLICT (team_id, season) DO UPDATE SET
                           needs = EXCLUDED.needs, needs_week = EXCLUDED.needs_week, surplus = EXCLUDED.surplus,
                           updated_at = now(),
                           direction = CASE WHEN %s THEN EXCLUDED.direction ELSE gm_team_seasons.direction END,
                           direction_score = CASE WHEN %s THEN EXCLUDED.direction_score ELSE gm_team_seasons.direction_score END,
                           direction_week = CASE WHEN %s THEN EXCLUDED.direction_week ELSE gm_team_seasons.direction_week END,
                           direction_inputs = CASE WHEN %s THEN EXCLUDED.direction_inputs ELSE gm_team_seasons.direction_inputs END""",
                    (tid, season, tid, r["direction"], r["D"], week, json.dumps(r["inputs"]), json.dumps(r["needs"]),
                     week, json.dumps(r["surplus"]), r["set_direction"], r["set_direction"], r["set_direction"],
                     r["set_direction"]))


def run(cur, season, week, force_direction=False):
    rows = compute(read_league(cur, season, week), week, force_direction)
    write(cur, season, week, rows)
    return rows


def print_rows(rows, log=print):
    for tid, r in sorted(rows.items(), key=lambda kv: -kv[1]["D"]):
        top = ", ".join(f"{n['group']} {n['score']:.1f}" for n in r["needs"][:3]) or "-"
        i = r["inputs"]
        log(f"  {r['abbr']:4} D {r['D']:+.2f}  {r['direction']:8} ({r['reason']})  proj {i['proj_wins']:.1f}  "
            f"needs: {top}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int)
    ap.add_argument("--week", type=int)
    ap.add_argument("--force-direction", action="store_true", help="re-band even off a checkpoint week")
    ap.add_argument("--dry-run", action="store_true", help="print, write nothing")
    a = ap.parse_args(argv)
    import psycopg
    dsn = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT current_season, current_week FROM league_state WHERE id = 1")
        s, w = cur.fetchone()
        season, week = a.season or s, a.week or w
        rows = compute(read_league(cur, season, week), week, a.force_direction)
        print(f"season {season} week {week}{' (checkpoint)' if is_checkpoint(week) else ''}")
        print_rows(rows)
        if a.dry_run:
            conn.rollback()
            print("dry run: nothing written")
        else:
            write(cur, season, week, rows)
            conn.commit()


if __name__ == "__main__":
    main()
