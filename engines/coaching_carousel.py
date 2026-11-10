"""
Coaching carousel (PROJECT_SPEC §2.19): head-coach firings (engines/coach_hot_seat.py), coordinator firings, and the
hiring cycle after the Super Bowl, with poaching. Mu writes its own head coaches and coordinators into the Madden save
(COACH_SAVE_WRITES_PLAN): every invented hire gets a CREATED coach_madden_profiles row here, and the league office's
offseason write applies the staff. Nothing is mirrored by hand.

Run by the weekly run's Step 7d (pipeline/weekly_capture.step_coach_hot_seat; weekly, playoff and postgame runs) or by
hand:

  python3 -m engines.coaching_carousel [--season S] [--dry-run]

Each run does what is ready, once: the head-coach hot seat (Black Monday, then each playoff team once it is out), the
coordinator review of every team whose head coach kept his job (decided with him), and, once the Super Bowl is
complete and every team is decided, the hiring cycle for season + 1, most attractive opening first (opening_appeal).
Mu never fires anyone on JR's teams (gm_team_settings.user_controlled), but other teams can poach their
coordinators; JR's team then promotes from within. coordinator_seat(), opening_appeal() and pick() are pure; a second
run changes nothing (seeded draws).
"""
import argparse
import json
import math
import os
import random

from engines import coach_hot_seat as chs
from engines import coach_madden_profile as cmp
from engines import coaching_tree_hiring as th
from engines import owner_policy as op
from engines.staff_names import background, new_name, taken_names
from personality import coaching_trees as ct

APPEAL_WEIGHTS = {"qb": 0.30, "roster": 0.25, "draft": 0.15, "cap": 0.10, "market": 0.10, "facilities": 0.10}
CHURN_PENALTY = 0.05               # per extra head coach the team went through in the last 5 seasons
MAX_COORD_FIRES = 8                # coordinators turn over far more than head coaches
COORD_LINE = 1.0
FIRST_YEAR_LINE = 1.5              # a coordinator in his first season needs a worse year
NOISE = 0.6                        # Gumbel scale on candidate scores: hiring is never a sure thing
OUTSIDE_SCORE = {"HC": 0.5, "OC": 0.2, "DC": 0.2}
PROMOTION_SCORE = 0.4
RETREAD_PENALTY = 0.3              # owners prefer a fresh face
OUTSIDE_SLANTS = (("OFFENSIVE", 0.45), ("DEFENSIVE", 0.35), ("CEO", 0.2))
SIDE = {"OC": "OFFENSIVE", "DC": "DEFENSIVE"}
FIRST_SEASON = 2026               # a fresh franchise's first season: the carousel decides every season from it on
HIRE_TICK = 23                     # the hiring cycle runs after the Super Bowl (week 22): tick 23 of the 32-tick year

# Resolution JC-2A data path (COMPENSATORY_PICKS_PLAN follow-up U6). Program eligibility is reviewed by JR in
# staff_development_eligibility; qualifying hire-aways are journalled in staff_development_departures for the comp
# producer. Neither table's absence changes today's behaviour, and nobody's status is ever inferred: absent from the
# registry means not eligible. The role sets mirror engines.compensatory_picks_engine.JC2A_FROM_ROLES / _TO_ROLES.
STAFF_DEV_ELIGIBILITY = "staff_development_eligibility"
STAFF_DEV_DEPARTURES = "staff_development_departures"
STAFF_DEV_TENURE_YEARS = 2
STAFF_DEV_FROM_ROLES = frozenset(("OC", "DC", "ASSISTANT_GM"))
STAFF_DEV_TO_ROLES = frozenset(("HC", "GM"))


# ----------------------------------------------------------------------------------------------- data helpers ----
def unit_ranks(cur, season):
    """{team_id: {pf_rank, pa_rank, off_talent_rank, def_talent_rank}}: points scored (1 = most) and allowed
    (1 = fewest) in `season`'s completed regular season, and team_seasons off_ovr / def_ovr (1 = best)."""
    cur.execute("""SELECT t.id, COALESCE(sum(CASE WHEN g.home_team_id = t.id THEN g.home_score ELSE g.away_score END), 0),
                          COALESCE(sum(CASE WHEN g.home_team_id = t.id THEN g.away_score ELSE g.home_score END), 0),
                          ts.off_ovr, ts.def_ovr
                   FROM teams t
                   LEFT JOIN games g ON g.season = %s AND g.game_type = 'REGULAR_SEASON' AND g.status = 'COMPLETED'
                                    AND t.id IN (g.home_team_id, g.away_team_id)
                   LEFT JOIN team_seasons ts ON ts.team_id = t.id AND ts.season = %s
                   WHERE t.abbreviation IS NOT NULL GROUP BY t.id, ts.off_ovr, ts.def_ovr""", (season, season))
    rows = cur.fetchall()
    if not rows or not any(r[1] for r in rows):
        return {}

    def rank(key, reverse):
        order = sorted(rows, key=lambda r: ((key(r) is None), -(key(r) or 0) if reverse else (key(r) or 0), r[0]))
        return {r[0]: i + 1 for i, r in enumerate(order)}
    pf, pa = rank(lambda r: r[1], True), rank(lambda r: r[2], False)
    off, de = rank(lambda r: r[3], True), rank(lambda r: r[4], True)
    return {r[0]: {"pf_rank": pf[r[0]], "pa_rank": pa[r[0]], "off_talent_rank": off[r[0]],
                   "def_talent_rank": de[r[0]]} for r in rows}


def unit_of(ranks, team_id, role):
    """(unit rank, talent rank) of a team's offense (OC) or defense (DC), or (None, None)."""
    u = ranks.get(team_id)
    if not u:
        return None, None
    return (u["pf_rank"], u["off_talent_rank"]) if role == "OC" else (u["pa_rank"], u["def_talent_rank"])


def jr_teams(cur):
    cur.execute("SELECT team_id FROM gm_team_settings WHERE user_controlled")
    return {r[0] for r in cur.fetchall()}


def staff_in(cur, season):
    """{(team_id, role): {coordinator_id, name, start_season, scheme, playbook, pass_lean, blitz_rate,
    man_coverage}} for the coordinators whose tenure covers `season` (the latest-starting per team and role)."""
    cur.execute("""SELECT DISTINCT ON (ct.team_id, ct.role) ct.team_id, ct.role, c.id, c.coordinator_name,
                          ct.start_season, c.scheme, c.playbook, c.pass_lean, c.blitz_rate, c.man_coverage
                   FROM coordinator_tenures ct JOIN coordinators c ON c.id = ct.coordinator_id
                   WHERE ct.start_season <= %s AND (ct.end_season IS NULL OR ct.end_season >= %s)
                   ORDER BY ct.team_id, ct.role, ct.start_season DESC""", (season, season))
    keys = ("coordinator_id", "name", "start_season", "scheme", "playbook", "pass_lean", "blitz_rate", "man_coverage")
    return {(r[0], r[1]): dict(zip(keys, r[2:])) for r in cur.fetchall()}


def head_coaches(cur, season):
    """{team_id: {coach_id, name, slant, start_season, offensive_scheme, defensive_scheme, pass_lean, blitz_rate,
    man_coverage}} in `season`."""
    cur.execute("""SELECT DISTINCT ON (ct.team_id) ct.team_id, c.id, c.coach_name, c.slant, ct.start_season,
                          c.offensive_scheme, c.defensive_scheme, c.pass_lean, c.blitz_rate, c.man_coverage
                   FROM coach_tenures ct JOIN coaches c ON c.id = ct.coach_id
                   WHERE ct.start_season <= %s AND (ct.end_season IS NULL OR ct.end_season >= %s)
                   ORDER BY ct.team_id, ct.start_season DESC""", (season, season))
    keys = ("coach_id", "name", "slant", "start_season", "offensive_scheme", "defensive_scheme", "pass_lean",
            "blitz_rate", "man_coverage")
    return {r[0]: dict(zip(keys, r[1:])) for r in cur.fetchall()}


def teams(cur):
    cur.execute("SELECT id, abbreviation, city, name FROM teams WHERE abbreviation IS NOT NULL")
    return {r[0]: {"abbr": r[1], "city": r[2], "name": r[3]} for r in cur.fetchall()}


# ------------------------------------------------------------------------------------------ coordinator review ----
def coordinator_seat(rows, season, max_fires=MAX_COORD_FIRES):
    """Pure. rows: [{team_id, abbr, role, coordinator_id, name, start_season, unit_rank, talent_rank,
    unit_rank_before, hc_slant, hc_score, playoff_wins, user_controlled}] for coordinators whose head coach kept his
    job. score = (unit rank - talent rank)/10 (halved when the head coach's slant is that side: he runs it),
    +0.5 for a bottom-7 unit (rank 26+), +0.25 when it was bottom-10 last season too under him, +0.5 when the head
    coach survived a hot seat score of 1.0 or more (a scapegoat), -0.5 after a playoff win. Fired when JR doesn't run
    the team, score >= 1.0 (1.5 in his first season), and the seeded draw (random.Random(
    f"coordseat:{season}:{abbr}:{role}")) falls under min(0.85, 0.4 + 0.3 (score - 1)); worst first, at most
    max_fires. Returns {(team_id, role): {coordinator_id, score, fire, inputs}}."""
    out = {}
    for r in rows:
        tenure = season - (r.get("start_season") or season) + 1
        ur, tr = r.get("unit_rank"), r.get("talent_rank")
        own_side = r.get("hc_slant") == SIDE[r["role"]]
        gap = (ur - tr) / 10 * (0.5 if own_side else 1.0) if ur is not None and tr is not None else 0.0
        terms = {"gap": round(gap, 3),
                 "bottom_unit": 0.5 if ur is not None and ur >= 26 else 0.0,
                 "bottom_again": 0.25 if (tenure >= 2 and r.get("unit_rank_before") is not None
                                          and r["unit_rank_before"] >= 23) else 0.0,
                 "scapegoat": 0.5 if (r.get("hc_score") or 0) >= chs.HOT_SEAT_LINE else 0.0,
                 "playoff_win": -0.5 if r.get("playoff_wins") else 0.0}
        score = sum(terms.values())
        line = FIRST_YEAR_LINE if tenure < 2 else COORD_LINE
        p = min(0.85, max(0.0, 0.4 + 0.3 * (score - COORD_LINE)))
        draw = random.Random(f"coordseat:{season}:{r['abbr']}:{r['role']}").random()
        why_not = "JR's team" if r.get("user_controlled") else "below the line" if score < line else None
        out[(r["team_id"], r["role"])] = {
            "coordinator_id": r["coordinator_id"], "score": round(score, 3), "fire": why_not is None and draw < p,
            "inputs": {"coordinator": r.get("name"), "unit_rank": ur, "talent_rank": tr, "tenure": tenure,
                       "terms": terms, "p": round(p, 3), "draw": round(draw, 3), "safe": why_not}}
    fired = sorted((k for k, v in out.items() if v["fire"]), key=lambda k: -out[k]["score"])
    for k in fired[max(0, max_fires):]:
        out[k]["fire"] = False
        out[k]["inputs"]["capped"] = True
    return out


def review_coordinators(cur, season, log=print):
    """Review the coordinators of every team whose head coach is decided and kept his job, once. Returns the
    coordinators fired this run [{team, role, coordinator, score}]."""
    cur.execute("""SELECT h.team_id, h.score, h.phase FROM coach_hot_seat h
                   WHERE h.season = %s AND NOT h.fired
                     AND NOT EXISTS (SELECT 1 FROM coordinator_hot_seat c WHERE c.season = h.season
                                                                           AND c.team_id = h.team_id)""", (season,))
    pending = {r[0]: {"hc_score": float(r[1]), "phase": r[2]} for r in cur.fetchall()}
    if not pending:
        return []
    ranks, before = unit_ranks(cur, season), unit_ranks(cur, season - 1)
    staff, hcs, tm, mine = staff_in(cur, season), head_coaches(cur, season), teams(cur), jr_teams(cur)
    res = chs.playoff_results(cur, season)
    rows = []
    for tid, info in pending.items():
        for role in ("OC", "DC"):
            c = staff.get((tid, role))
            if not c:
                continue
            ur, tr = unit_of(ranks, tid, role)
            rows.append({"team_id": tid, "abbr": tm[tid]["abbr"], "role": role, **c, "unit_rank": ur,
                         "talent_rank": tr, "unit_rank_before": unit_of(before, tid, role)[0],
                         "hc_slant": (hcs.get(tid) or {}).get("slant"), "hc_score": info["hc_score"],
                         "playoff_wins": res.get(tid, {}).get("wins", 0), "user_controlled": tid in mine})
    cur.execute("SELECT count(*) FROM coordinator_hot_seat WHERE season = %s AND fired AND NOT inputs ? 'fired_by'",
                (season,))
    decisions = coordinator_seat(rows, season, MAX_COORD_FIRES - cur.fetchone()[0])
    by_key = {(r["team_id"], r["role"]): r for r in rows}
    out = []
    for (tid, role), d in sorted(decisions.items(), key=lambda kv: -kv[1]["score"]):
        cur.execute("""INSERT INTO coordinator_hot_seat (season, team_id, role, coordinator_id, score, fired, phase,
                                                         inputs) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (season, tid, role, d["coordinator_id"], d["score"], d["fire"], pending[tid]["phase"],
                     json.dumps(d["inputs"])))
        if d["fire"]:
            cur.execute("""UPDATE coordinator_tenures SET end_season = %s
                           WHERE coordinator_id = %s AND team_id = %s AND role = %s
                             AND (end_season IS NULL OR end_season > %s)""",
                        (season, d["coordinator_id"], tid, role, season))
            r = by_key[(tid, role)]
            out.append({"team": r["abbr"], "role": role, "coordinator": r["name"], "score": d["score"]})
            log(f"  coordinator hot seat: {r['abbr']} fires {role} {r['name']} (score {d['score']})")
    return out


# --------------------------------------------------------------------------------------------- hiring cycle ----
def percentiles(values):
    """{key: 0..1 percentile (0 lowest, 1 highest, ties shared)} of {key: value}; None values get 0.5."""
    known = {k: v for k, v in values.items() if v is not None}
    n = len(known)
    out = {k: 0.5 for k in values}
    for k, v in known.items():
        below = sum(o < v for o in known.values()) + 0.5 * (sum(o == v for o in known.values()) - 1)
        out[k] = round(below / max(n - 1, 1), 3)
    return out


def opening_appeal(parts):
    """Pure. parts: {qb, roster, draft, cap, market, facilities} each 0..1 (missing = 0.5) plus churn (extra head
    coaches in the last 5 seasons). appeal = sum(APPEAL_WEIGHTS[k] * part) - CHURN_PENALTY * churn (§2.1): the QB
    first (a franchise quarterback sells a job), then the roster and its under-25 core, draft capital over the next
    two drafts, cap room, market prestige and facilities; an owner who keeps firing coaches scares candidates off."""
    base = sum(w * (parts.get(k) if parts.get(k) is not None else 0.5) for k, w in APPEAL_WEIGHTS.items())
    return round(base - CHURN_PENALTY * (parts.get("churn") or 0), 3)


# ----------------------------------------------------------------------- §2.1 appeal components (pure) ----
STARTERS = 22          # the 22 non-specialist starters (QB through S)
STARTER_SLOTS = {"QB": 1, "RB": 1, "WR": 3, "TE": 1, "OT": 2, "IOL": 3, "EDGE": 2, "DT": 2, "LB": 2, "CB": 3, "S": 2}


def qb_appeal(qb):
    """S_qb (§2.1 five-tier table): a team's QB1 destination score. qb: {ovr, age, dev_trait, cap_hit} (or
    {ovr, age, cap}); None or {} = no starter (Tier 5, 0.10). Tier 1 elite (OVR >= 88, age <= 31) 1.00; Tier 2
    promising young (age <= 25, Star/Superstar/X-Factor dev, OVR >= 76) 0.90; Tier 3 average starter (78-84, 26-31)
    0.60; Tier 4 aging bridge (age >= 34, OVR <= 78, cap >= $30M) 0.25; anything else 0.10."""
    if not qb:
        return 0.10
    ovr, age = qb.get("ovr"), qb.get("age")
    dev = (qb.get("dev_trait") or "").lower()
    cap = qb.get("cap_hit") if qb.get("cap_hit") is not None else qb.get("cap")
    if ovr is not None and age is not None and int(ovr) >= 88 and int(age) <= 31:
        return 1.00
    if age is not None and int(age) <= 25 and ovr is not None and int(ovr) >= 76 \
            and ("star" in dev or "x-factor" in dev):
        return 0.90
    # A very good starter short of Tier 1 used to fall through to 0.10, BELOW a 78-OVR average starter: the scale
    # has to be monotonic in OVR or the opening appeal ranks a worse quarterback higher.
    if ovr is not None and age is not None and int(ovr) >= 85 and int(age) <= 33:
        return 0.75
    if ovr is not None and age is not None and 78 <= int(ovr) <= 84 and 26 <= int(age) <= 31:
        return 0.60
    if age is not None and int(age) >= 34 and ovr is not None and int(ovr) <= 78 and int(cap or 0) >= 30_000_000:
        return 0.25
    return 0.10


def youth_core(under25_starters, under25_xfactor):
    """S_youth_core (§2.1) = min(under-25 starters / 22 + 0.05 * under-25 X-factor, 1.0)."""
    return round(min(1.0, (int(under25_starters or 0) / STARTERS) + 0.05 * int(under25_xfactor or 0)), 3)


def roster_appeal(team_ovr_pct, under25_starters, under25_xfactor):
    """S_roster_appeal (§2.1) = 0.50 * team_ovr percentile + 0.50 * S_youth_core."""
    return round(0.50 * team_ovr_pct + 0.50 * youth_core(under25_starters, under25_xfactor), 3)


def opening_parts(cur, season, team_ids):
    """{team_id: opening_appeal parts} for openings after `season`. qb = the §2.1 S_qb tier of the QB1 (OVR, age,
    dev trait, cap hit); roster = 0.50 * team_ovr percentile + 0.50 * S_youth_core (under-25 starters / 22 + 0.05 *
    under-25 X-factor); draft = Round 1-2 pick capital over the next TWO drafts (the upcoming draft weighted by slot,
    (33 - round pick)/32, Round 2 half; the draft after counted by round only since its order isn't set yet); cap =
    cap space percentile (team_cap_seasons, season + 1 else season); market = market_constants B percentile;
    facilities = report_card_engine.facility_score of the team's latest report card (or the 2026 baseline). churn =
    head coaches who started in the last 5 seasons, minus one."""
    from engines.draft_order import draft_order_with_owners
    from engines.market_constants import TEAM_MARKET
    allt = teams(cur)
    qbs = _qb1_facts(cur, season)
    qb = {t: qb_appeal(qbs.get(t)) for t in allt}
    cur.execute("SELECT team_id, team_ovr FROM team_seasons WHERE season = %s", (season,))
    team_ovr = percentiles({**{t: None for t in allt}, **{t: v for t, v in cur.fetchall() if t in allt}})
    youth = _roster_youth(cur, season)
    roster = {t: roster_appeal(team_ovr[t], youth.get(t, {}).get("under25_starters", 0),
                               youth.get(t, {}).get("under25_xfactor", 0)) for t in allt}
    value = {t: 0.0 for t in allt}
    try:
        cur.execute("SAVEPOINT appeal_draft")
        for ds, slot_weighted in ((season + 1, True), (season + 2, False)):
            for slot in draft_order_with_owners(cur, ds, rounds=(1, 2)):
                if slot["owner_team_id"] in value:
                    w = (33 - slot["round_pick"]) / 32 if slot_weighted else 1.0
                    value[slot["owner_team_id"]] += w * (1.0 if slot["round"] == 1 else 0.5)
        cur.execute("RELEASE SAVEPOINT appeal_draft")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT appeal_draft")
        value = {t: None for t in allt}
    draft = percentiles(value)
    market = percentiles({t: TEAM_MARKET.get(allt[t]["name"], {}).get("B") for t in allt})
    cur.execute("""SELECT DISTINCT ON (team_id) team_id, cap_space FROM team_cap_seasons
                   WHERE season IN (%s, %s) ORDER BY team_id, season DESC""", (season, season + 1))
    cap = percentiles({**{t: None for t in allt}, **{t: float(c) if c is not None else None
                                                     for t, c in cur.fetchall() if t in allt}})
    facilities = _facility_scores(cur, season, allt)
    cur.execute("""SELECT team_id, count(*) FROM coach_tenures WHERE start_season > %s AND start_season <= %s
                   GROUP BY team_id""", (season - 5, season))
    churn = {t: max(n - 1, 0) for t, n in cur.fetchall()}
    return {t: {"qb": qb[t], "roster": roster[t], "draft": draft[t], "cap": cap[t], "market": market[t],
                "facilities": facilities.get(t, 0.5), "churn": churn.get(t, 0)} for t in team_ids}


def _qb1_facts(cur, season):
    """{team_id: {ovr, age, dev_trait, cap_hit}} for each team's top-OVR rostered QB1 (scouting_engine.qb_starters
    plus the dev trait and current-season cap hit the §2.1 S_qb tiers need). Falls back to qb_starters (no dev/cap)
    when the richer read fails."""
    from engines.scouting_engine import qb_starters
    try:
        cur.execute("SAVEPOINT appeal_qb")
        cur.execute("""SELECT DISTINCT ON (ps.team_id) ps.team_id, ps.overall_rating, ps.age, ps.dev_trait,
                              pcy.cap_hit
                       FROM player_seasons ps
                       LEFT JOIN player_contracts pc ON pc.player_id = ps.player_id AND pc.season = ps.season
                            AND pc.status = 'ACTIVE'
                       LEFT JOIN player_contract_years pcy ON pcy.contract_id = pc.id AND pcy.year = ps.season
                       WHERE ps.season = %s AND ps.position = 'QB' AND ps.released_week IS NULL
                         AND ps.team_id IS NOT NULL AND ps.overall_rating IS NOT NULL
                       ORDER BY ps.team_id, ps.overall_rating DESC, ps.player_id""", (season,))
        rows = cur.fetchall()
        cur.execute("RELEASE SAVEPOINT appeal_qb")
        return {r[0]: {"ovr": r[1], "age": r[2], "dev_trait": r[3], "cap_hit": r[4]} for r in rows}
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT appeal_qb")
        return {t: {"ovr": q.get("ovr"), "age": q.get("age"), "dev_trait": None, "cap_hit": None}
                for t, q in qb_starters(cur, season).items()}


def _roster_youth(cur, season):
    """{team_id: {under25_starters, under25_xfactor}}: the under-25 count among the 22 starters (top OVR per
    STARTER_SLOTS group) and the roster's under-25 blue-chip (Superstar/X-Factor) dev traits."""
    from engines.gm_direction import position_group
    cur.execute("""SELECT team_id, player_id, position, age, overall_rating, dev_trait
                   FROM player_seasons WHERE season = %s AND released_week IS NULL
                     AND team_id IS NOT NULL AND position IS NOT NULL""", (season,))
    by_team = {}
    for tid, pid, pos, age, ovr, dev in cur.fetchall():
        g = position_group(pos)
        if g not in STARTER_SLOTS:
            continue
        by_team.setdefault(tid, []).append({"pid": pid, "group": g, "age": age, "ovr": ovr or 0, "dev": dev or ""})
    out = {}
    for tid, players in by_team.items():
        starters = set()
        for g, n in STARTER_SLOTS.items():
            pool = sorted((p for p in players if p["group"] == g), key=lambda p: -p["ovr"])[:n]
            starters.update(p["pid"] for p in pool)
        under25_starters = sum(1 for p in players if p["pid"] in starters and p["age"] is not None
                               and int(p["age"]) < 25)
        under25_xfactor = sum(1 for p in players if p["age"] is not None and int(p["age"]) < 25
                              and ("x-factor" in p["dev"].lower() or "superstar" in p["dev"].lower()))
        out[tid] = {"under25_starters": under25_starters, "under25_xfactor": under25_xfactor}
    return out


def _facility_scores(cur, season, tm):
    """{team_id: facility_score} from the team's latest team_report_cards row (season <= season), else the 2026
    baseline (engines/report_card_baseline.json, keyed by teams.abbreviation)."""
    from engines import report_card_engine
    cols = ("weight_room", "training_room", "locker_room", "food_nutrition", "team_travel",
            "family_treatment", "head_coach", "ownership")
    baseline = report_card_engine.load_baseline()
    out = {tid: report_card_engine.facility_score(baseline[t["abbr"]])
           for tid, t in tm.items() if t["abbr"] in baseline}
    try:
        cur.execute("SAVEPOINT appeal_facility")
        cur.execute(f"""SELECT DISTINCT ON (team_id) team_id, {', '.join(cols)}
                        FROM team_report_cards WHERE season <= %s ORDER BY team_id, season DESC""", (season,))
        for r in cur.fetchall():
            out[r[0]] = report_card_engine.facility_score(dict(zip(cols, r[1:])))
        cur.execute("RELEASE SAVEPOINT appeal_facility")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT appeal_facility")
    return out


# ----------------------------------------------------------------------- §2.3 Godfather offer (pure) ----
GODFATHER_CASH = 35_000_000        # owner cash pool floor for a Full Control offer (§2.3)
GODFATHER_SALARY = 19_000_000      # the top-of-market $16-22M/yr band's midpoint
GODFATHER_YEARS = 5                # 5-6 years, fully guaranteed


def godfather_owner_ok(owner_view, cash_pool):
    """True when the owner has DEEP_POCKETS and HEADLINE_HUNTER traits and a cash pool >= GODFATHER_CASH (§2.3)."""
    if not owner_view:
        return False
    traits = set(owner_view.get("traits") or ())
    return {"DEEP_POCKETS", "HEADLINE_HUNTER"} <= traits and int(cash_pool or 0) >= GODFATHER_CASH


def godfather_candidate_ok(candidate):
    """True when the candidate has the §2.3 stature: a Super Bowl win as head coach or coordinator **and** a drive or
    ego of 8 or more (JR, 2026-11-10 — the plan's two readings disagreed; this is the AND reading). It applies to any
    head-coach hire the carousel makes: a poached coordinator, an unemployed former head coach or an outside hire.
    A sitting head coach under contract elsewhere is never a candidate in the first place."""
    if not candidate:
        return False
    won = (candidate.get("super_bowl_wins") or 0) >= 1 or bool(candidate.get("champion"))
    drive, ego = candidate.get("drive"), candidate.get("ego")
    driven = (drive is not None and int(drive) >= 8) or (ego is not None and int(ego) >= 8)
    return won and driven


def candidate_sliders(cur):
    """{(namespace, external_id): {"drive", "ego"}} for the carousel's coaching candidates (P1-27): the person
    lifecycle's `db-coach` / `db-coordinator` profiles when this league's universe is registered, else the legacy
    coach `persona_sliders` rows (external_id = the coach/coordinator id). `ego` is the `confidence` slider ("brash
    and sure of yourself"). {} on any error or without the tables; the carousel never fails on this read."""
    out = {}

    def add(ns, ext, drive, conf):
        if drive is None and conf is None:
            return
        out[(ns, str(ext))] = {k: int(v) for k, v in (("drive", drive), ("ego", conf)) if v is not None}

    cur.execute("SAVEPOINT candidate_sliders")
    try:
        cur.execute("SELECT to_regclass('persona_sliders') IS NOT NULL")
        if cur.fetchone()[0]:
            cur.execute("SELECT persona_id, drive, confidence FROM persona_sliders WHERE persona_type = 'coach'")
            for pid, drive, conf in cur.fetchall():
                add("db-coach", pid, drive, conf)
        cur.execute("SELECT to_regclass('persona_core_aliases') IS NOT NULL")
        if cur.fetchone()[0]:
            cur.execute("SELECT madden_league_id FROM league_state WHERE id = 1")
            row = cur.fetchone()
            if row and row[0] is not None:
                from personality import lifecycle_store as LS
                universe = LS.universe_for_league(cur, int(row[0]))
                if universe:
                    cur.execute("""SELECT a.namespace, a.external_id, s.drive, s.confidence
                                   FROM persona_core_aliases a
                                   JOIN persona_core_profiles p ON p.persona_type = a.persona_type
                                        AND p.persona_id = a.persona_id
                                   JOIN persona_sliders s ON s.persona_type = p.persona_type
                                        AND s.persona_id = p.persona_id
                                   WHERE a.persona_type = 'coach' AND a.universe_id = %s
                                     AND a.namespace IN ('db-coach', 'db-coordinator')""",
                                (universe["universe_id"],))
                    for ns, ext, drive, conf in cur.fetchall():
                        add(ns, ext, drive, conf)
        cur.execute("RELEASE SAVEPOINT candidate_sliders")
    except Exception:  # noqa: BLE001 - an optional enrichment
        cur.execute("ROLLBACK TO SAVEPOINT candidate_sliders")
        cur.execute("RELEASE SAVEPOINT candidate_sliders")
        return {}
    return out


def _sliders_for(sliders, namespace, person_id):
    """A candidate's {drive, ego} from candidate_sliders, trying his own namespace then the other seat's."""
    for ns in (namespace, "db-coach" if namespace == "db-coordinator" else "db-coordinator"):
        got = sliders.get((ns, str(person_id)))
        if got:
            return dict(got)
    return {}


def godfather_offer(*, gate, owner_view, cash_pool, hc_vacant, gm_vacant, candidate):
    """Pure §2.3 gate. gate: staff_contracts_engine.godfather_gate's result (or godfather_eligible's). Returns
    {'offer': bool, 'blockers': [...], 'salary': GODFATHER_SALARY, 'years': GODFATHER_YEARS}."""
    blockers = []
    if not (gate or {}).get("eligible"):
        blockers.append("rarity gate")
    if not godfather_owner_ok(owner_view, cash_pool):
        blockers.append("owner")
    if not (hc_vacant and gm_vacant):
        blockers.append("dual vacancy")
    if not godfather_candidate_ok(candidate):
        blockers.append("candidate stature")
    return {"offer": not blockers, "blockers": blockers, "salary": GODFATHER_SALARY, "years": GODFATHER_YEARS}


def gm_slant_bias(slant):
    """The front-office slant sync (§2.3): a coach-GM's position_bias leans toward the coach's side of the ball."""
    if slant == "OFFENSIVE":
        return {"QB": 1.10, "WR": 1.08, "OT": 1.08, "RB": 1.05, "TE": 1.05, "IOL": 1.05}
    if slant == "DEFENSIVE":
        return {"EDGE": 1.10, "DT": 1.08, "CB": 1.08, "LB": 1.05, "S": 1.05}
    return {}


def godfather_acceptance(season, team_id, coach_id, coach_name, *, salary, years, slant, trigger_reason):
    """Pure: the writes a Full Control acceptance makes, as plain dicts — the staff_contracts row (full_control
    true), the gm_profiles coach_id assignment plus the slant-synced position_bias, and the full_control_history
    row."""
    return {
        "staff_contract": {"team_id": team_id, "person_id": coach_id, "person_kind": "HC",
                           "annual_salary": salary, "total_years": years, "remaining_years": years,
                           "signed_season": season + 1, "full_control": True, "buyout_owed": 0},
        "gm_profile": {"team_id": team_id, "coach_id": coach_id, "gm_name": coach_name,
                       "position_bias": gm_slant_bias(slant), "start_season": season + 1},
        "full_control_history": {"season": season, "team_id": team_id, "coach_id": coach_id,
                                 "annual_salary": salary, "contract_years": years,
                                 "trigger_reason": trigger_reason},
    }


def _gm_vacant(cur, team_id):
    """True when the team has no open GM tenure (a dual HC+GM vacancy needs the GM seat empty too). A failed read
    must not abort the caller's transaction — without the savepoint the next statement raised
    InFailedSqlTransaction and killed the whole hiring cycle."""
    try:
        cur.execute("SAVEPOINT gm_vacant")
        cur.execute("SELECT count(*) FROM gm_profiles WHERE team_id = %s AND end_season IS NULL", (team_id,))
        count = cur.fetchone()[0]
        cur.execute("RELEASE SAVEPOINT gm_vacant")
        return count == 0
    except Exception:
        try:
            cur.execute("ROLLBACK TO SAVEPOINT gm_vacant")
        except Exception:
            pass
        return False


def _godfather_cash_pool(cur, season, team_id, abbr):
    """The owner's annual cash pool for the Godfather cash floor: owner_cash_ledgers.total_cash_budget (latest
    season <= season + 1), else the baseline wealth band's base budget."""
    from engines import report_card_engine
    try:
        cur.execute("SAVEPOINT godfather_cash")
        cur.execute("""SELECT total_cash_budget FROM owner_cash_ledgers
                       WHERE team_id = %s AND season <= %s ORDER BY season DESC LIMIT 1""", (team_id, season + 1))
        row = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT godfather_cash")
        if row and row[0] is not None:
            return int(row[0])
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT godfather_cash")
    try:
        card = report_card_engine.load_baseline().get(abbr)
        if card and card.get("wealth_band"):
            return report_card_engine.base_budget(card["wealth_band"])
    except Exception:
        pass
    return 0


def _extend_full_control(cur, acc):
    """The §2.3 writes when a Godfather offer is accepted: the full-control staff_contracts row, the gm_profiles
    coach_id assignment (plus the slant-synced position_bias; a new coach-GM tenure when the GM seat is empty), and
    the full_control_history row."""
    sc = acc["staff_contract"]
    cur.execute("""INSERT INTO staff_contracts (team_id, person_id, person_kind, annual_salary, total_years,
                                                remaining_years, signed_season, full_control, buyout_owed)
                   VALUES (%s, %s, 'HC', %s, %s, %s, %s, true, 0)""",
                (sc["team_id"], sc["person_id"], sc["annual_salary"], sc["total_years"], sc["remaining_years"],
                 sc["signed_season"]))
    h = acc["full_control_history"]
    cur.execute("""INSERT INTO full_control_history (season, team_id, coach_id, annual_salary, contract_years,
                                                    trigger_reason)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (h["season"], h["team_id"], h["coach_id"], h["annual_salary"], h["contract_years"],
                 h["trigger_reason"]))
    gm = acc["gm_profile"]
    cur.execute("UPDATE gm_profiles SET coach_id = %s, position_bias = %s "
                "WHERE team_id = %s AND end_season IS NULL",
                (gm["coach_id"], json.dumps(gm["position_bias"]), gm["team_id"]))
    if cur.rowcount == 0:
        # a dual vacancy: the GM seat is empty, so the new head coach becomes the GM (a new coach-GM tenure)
        from engines import gm_voices
        rng = random.Random(f"gm:{gm['team_id']}:{gm['start_season']}:godfather")
        # The draw order is part of the seed: pre-plan sent the first draw (0.35-0.75) to risk_appetite and the
        # second (0.30-0.70) to aggressiveness, and 8ec3ec6 swapped them rather than restoring them.
        risk = round(rng.uniform(0.35, 0.75), 2)
        agg = round(rng.uniform(0.30, 0.70), 2)
        picks = round(rng.uniform(0.85, 1.20), 2)
        youth = round(rng.uniform(-0.5, 0.5), 2)
        traits = {"aggressiveness": agg, "risk_appetite": risk, "pick_affinity": picks, "youth_bias": youth}
        card = gm_voices.template_card(traits, role="COACH_GM")
        cur.execute("""INSERT INTO gm_profiles (team_id, gm_name, bio, voice, voice_card, voice_card_source,
                                                bio_source, start_season, first_draft_season, coach_id,
                                                risk_appetite, aggressiveness, pick_affinity, youth_bias,
                                                position_bias, seed)
                       VALUES (%s, %s, %s, %s, %s, 'TEMPLATE', 'TEMPLATE', %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (gm["team_id"], gm["gm_name"],
                     f"{gm['gm_name']} is the head coach and general manager with full control of football "
                     "operations.", gm_voices.one_line(card), json.dumps(card), gm["start_season"],
                     gm["start_season"], gm["coach_id"], traits["risk_appetite"], traits["aggressiveness"],
                     traits["pick_affinity"], traits["youth_bias"],
                     json.dumps(gm["position_bias"]),
                     f"gm:{gm['team_id']}:{gm['start_season']}:godfather"))


def _maybe_godfather(cur, season, tid, abbr, coach_id, coach_name, slant, candidate, owner_view, log=print):
    """§2.3: extend a Full Control (Godfather) offer for a head-coach opening when every gate passes — the rarity
    gate (staff_contracts_engine.godfather_gate), the owner archetype + cash, the dual HC+GM vacancy and the
    candidate stature. Returns the acceptance dict (already written) or None. The tables exist since 2027_02_02, but
    the owner features stay off until the activation plan clears them (the `owner_hiring` switchboard), so this
    normally no-ops."""
    from engines import staff_contracts_engine as sce
    try:
        cur.execute("SAVEPOINT godfather_gate")
        gate = sce.godfather_gate(cur, season)
        cur.execute("RELEASE SAVEPOINT godfather_gate")
    except Exception:
        try:
            cur.execute("ROLLBACK TO SAVEPOINT godfather_gate")
        except Exception:
            pass
        return None
    cash = _godfather_cash_pool(cur, season, tid, abbr)
    decision = godfather_offer(gate=gate, owner_view=owner_view, cash_pool=cash,
                               hc_vacant=True, gm_vacant=_gm_vacant(cur, tid), candidate=candidate)
    if not decision["offer"]:
        return None
    acc = godfather_acceptance(season, tid, coach_id, coach_name, salary=decision["salary"],
                               years=decision["years"], slant=slant,
                               trigger_reason="dual HC+GM vacancy; deep-pockets headline-hunter owner; "
                                             "Super Bowl-winning candidate")
    _extend_full_control(cur, acc)
    log(f"  godfather: {abbr} grants {coach_name} full control (head coach + GM)")
    return acc


def gumbel(key):
    u = random.Random(f"hire:{key}").random()
    return -NOISE * math.log(-math.log(min(max(u, 1e-12), 1 - 1e-12)))


def owner_adjusted(pool, owner_view):
    """Pure. Copies of the pool entries with the owner's hiring term (owner_policy.hiring_key, |x| <= 0.15) added
    to their scores."""
    return [dict(c, score=round(c["score"] + op.hiring_key(owner_view, c), 4)) for c in pool]


def owner_alternative(pool, chosen, owner_view):
    """Pure. The candidate the owner would rather have than `chosen` (a PUSH): the one with the best positive
    hiring term, then score, then key; None when no other candidate has a positive term."""
    best = None
    for c in pool:
        if c["key"] == chosen["key"]:
            continue
        k = op.hiring_key(owner_view, c)
        if k > 0 and (best is None or (k, c["score"], c["key"]) > best[0]):
            best = ((k, c["score"], c["key"]), c)
    return None if best is None else best[1]


def pick(candidates, key):
    """Pure. The candidate with the best score plus seeded Gumbel noise (random.Random(f"hire:{key}:{cand key}")).
    candidates: [{key, score, ...}]."""
    return max(candidates, key=lambda c: (c["score"] + gumbel(f"{key}:{c['key']}"), c["key"]))


def coordinator_candidacy(unit_rank, talent_rank, playoff_wins=0, champion=False, years=1):
    """How attractive a coordinator is: his unit against its talent, how good the unit was, a playoff run,
    experience. About -1 .. +2."""
    if unit_rank is None:
        return 0.0
    over = (talent_rank - unit_rank) / 10 if talent_rank is not None else 0.0
    return round(over + (16.5 - unit_rank) / 16 + (0.3 if playoff_wins else 0) + (0.3 if champion else 0)
                 + 0.1 * min(years, 4), 3)


def _coordinator_years(cur, season):
    """{coordinator_id: seasons as a coordinator through `season`}."""
    cur.execute("""SELECT coordinator_id, sum(LEAST(COALESCE(end_season, %s), %s) - start_season + 1)
                   FROM coordinator_tenures WHERE start_season <= %s GROUP BY coordinator_id""",
                (season, season, season))
    return {r[0]: int(r[1]) for r in cur.fetchall()}


def ready_to_hire(cur, season):
    """The Super Bowl is complete, every team's head coach is decided, and the cycle hasn't run."""
    cur.execute("SELECT count(*) FROM games WHERE season = %s AND game_type = 'SUPER_BOWL' AND status = 'COMPLETED'",
                (season,))
    if not cur.fetchone()[0]:
        return False
    cur.execute("SELECT count(*) FROM teams WHERE abbreviation IS NOT NULL AND id NOT IN "
                "(SELECT team_id FROM coach_hot_seat WHERE season = %s)", (season,))
    if cur.fetchone()[0]:
        return False
    cur.execute("SELECT count(*) FROM staff_moves WHERE season = %s AND COALESCE(inputs->>'by', '') <> 'JR'",
                (season,))
    return cur.fetchone()[0] == 0                   # JR's own named replacements (engines/fire_coordinator) don't count


def _insert_coordinator(cur, name, role, scheme, playbook, tendencies, notes):
    cur.execute("""INSERT INTO coordinators (coordinator_name, side, scheme, playbook, pass_lean, blitz_rate,
                                             man_coverage, origin_notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (name, SIDE[role], scheme, playbook,
                 tendencies.get("pass_lean") if role == "OC" else None,
                 tendencies.get("blitz_rate") if role == "DC" else None,
                 tendencies.get("man_coverage") if role == "DC" else None, notes))
    return cur.fetchone()[0]


def _jsonb(value):
    from psycopg.types.json import Jsonb
    return Jsonb(value)


def _mapped_playbook(value):
    """`value` when build_profile's `PLAYBOOK_MAP` carries it, else None.

    The carousel's own HC_STAFF coordinator rows store the nickname form ('Panthers Offense'), which the map does not
    carry (it has the `<City> Offense/Defense` forms plus six nickname aliases); passing it to the builder would raise
    and abort the hiring cycle, so the profile leaves that playbook NULL rather than guessing a Madden enum.
    """
    return value if value in cmp.PLAYBOOK_MAP else None


def _store_created_profile(cur, person_id, name, role, *, slant=None, scheme=None, playbook=None, tendencies=None,
                           sliders=None, background=None, promote_from=None):
    """Store the CREATED `coach_madden_profiles` row for one invented coach/coordinator (§2.4, U5).

    One call and one insert per invented person, inside the caller's transaction. The seed is
    `madden-coach:<coach_key>`, so a CREATED row and a later SAVE link for the same person draw the same profile.
    `background` is None: `staff_names.background` returns narrative text, not the `{age, years}` mapping the builder
    accepts, and the carousel has no better numeric values for an invented person.
    """
    if not _table_exists(cur, "coach_madden_profiles"):
        return None
    # D2: a poached coordinator promoted to head coach is the same person, and must keep his own record. Move his
    # existing profile onto the coach identity instead of inventing a second person — otherwise the staff write creates
    # a new Madden coach, releases his real record, and D8 later retires it (audit B3, 2026-11-10).
    if promote_from is not None:
        cur.execute("""UPDATE coach_madden_profiles
                          SET person_kind = 'COACH', person_id = %s, position = %s
                        WHERE person_kind = 'COORD' AND person_id = %s
                    RETURNING id""", (person_id, "HeadCoach", promote_from))
        if cur.fetchone():
            return None
    coach_key = f"{'coach' if role == 'HC' else 'coord'}-{person_id}"
    row = cmp.build_profile({"name": name, "id": person_id}, role, slant=slant, scheme=scheme, playbook=playbook,
                            tendencies=tendencies or {}, sliders=sliders or {}, background=background,
                            contract=None, seed=f"madden-coach:{coach_key}")
    columns = [c for c in (
        "person_kind", "person_id", "source", "presentation_id", "first_name", "last_name", "age", "position",
        "archetype", "specialty", "level", "years_coaching", "offensive_playbook", "defensive_playbook",
        "offensive_scheme", "defensive_scheme", "team_philosophy", "personality", "demeanor", "body_type", "height",
        "weight", "face_shape", "generic_head", "talents", "ratings", "tendencies", "visuals_template", "portrait_id",
        "portrait_status", "headshot_path", "seed", "written_at") if c in row]
    values = tuple(_jsonb(row[c]) if c in ("talents", "ratings", "tendencies") else row[c] for c in columns)
    cur.execute(f"INSERT INTO coach_madden_profiles ({', '.join(columns)}) "
                f"VALUES ({', '.join(['%s'] * len(columns))})", values)
    return row


def _move(cur, season, team_id, role, action, person, **kw):
    cols = ["season", "team_id", "role", "action", "person"] + list(kw)
    cur.execute(f"INSERT INTO staff_moves ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                (season, team_id, role, action, person, *kw.values()))


# ---------------------------------------------------------------------- staff development award data path ----
# Resolution JC-2A's "minority" leg is a reviewed *program eligibility* fact, not anything this engine derives. A
# person is either in the registry or absent (never guessed). Every read is guarded with to_regclass, so a database
# without db/migrations/2027_02_07_staff_development_award.sql records nothing and behaves exactly as before.
def _table_exists(cur, table):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
    row = cur.fetchone()
    return bool(row and row[0])


def development_eligible(cur, person_kind, person_key):
    """True when the reviewed staff_development_eligibility registry marks this person eligible for the development
    award. person_kind is 'COACH', 'COORDINATOR' or 'EXECUTIVE'; person_key is matched as text (a db row id for
    coaches/coordinators, or JR's reviewed key for an executive). An absent registry means not eligible, never an
    error."""
    if not _table_exists(cur, STAFF_DEV_ELIGIBILITY):
        return False
    cur.execute("SELECT eligible FROM staff_development_eligibility WHERE person_kind = %s AND person_key = %s",
                (person_kind, str(person_key)))
    row = cur.fetchone()
    return bool(row and row[0])


def record_staff_departure(cur, season, person_kind, person_key, person_name, from_team_id, to_team_id,
                           from_role, to_role, tenure_years, source="coaching_carousel", hire_coach_id=None):
    """Journal one hire-away in staff_development_departures when the reviewed registry marks the person eligible
    and the move qualifies: OC / DC / assistant-GM to another club as HC / GM after >= STAFF_DEV_TENURE_YEARS.
    person_kind/person_key identify the reviewed registry row (a moved coordinator is 'COORDINATOR', his
    coordinator_id); hire_coach_id is the coaches row the hire itself created, which is what a JC-2A award stores as
    its source coach (2027_02_15). Returns True when a row was written. A missing registry or journal writes nothing
    and never raises, so the carousel behaves exactly as today without the migration. The journal's eligible flag is
    always true here: ineligible and absent people are simply never recorded."""
    if from_team_id is None or to_team_id is None or from_team_id == to_team_id:
        return False
    if from_role not in STAFF_DEV_FROM_ROLES or to_role not in STAFF_DEV_TO_ROLES:
        return False
    if (tenure_years or 0) < STAFF_DEV_TENURE_YEARS:
        return False
    if not _table_exists(cur, STAFF_DEV_DEPARTURES):
        return False
    if not development_eligible(cur, person_kind, person_key):
        return False
    cur.execute("""INSERT INTO staff_development_departures
                   (season, from_team_id, to_team_id, person_name, from_role, to_role, tenure_years, eligible, source,
                    person_kind, person_key, hire_coach_id)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, true, %s, %s, %s, %s)""",
                (season, from_team_id, to_team_id, person_name, from_role, to_role, int(tenure_years), source,
                 person_kind, str(person_key), hire_coach_id))
    return True


def eligible_departures(cur, season):
    """The season's reviewed development-award hire-aways in the shape
    engines.compensatory_picks_engine.jc2a_awards expects (team_id = the club that lost the person). The reviewed
    `eligible` flag stands in for the engine's `is_minority` input and is never inferred. An absent journal means no
    departures."""
    if not _table_exists(cur, STAFF_DEV_DEPARTURES):
        return []
    cur.execute("""SELECT from_team_id, person_name, from_role, to_role, tenure_years, source, person_kind,
                          person_key, hire_coach_id
                   FROM staff_development_departures WHERE season = %s AND eligible""", (season,))
    out = []
    for from_team, name, from_role, to_role, tenure, source, kind, key, hire_coach_id in cur.fetchall():
        out.append({"team_id": from_team, "person_name": name, "is_minority": True,
                    "hire_from_role": from_role, "hire_to_role": to_role, "tenure_years": tenure,
                    "hired_away": True, "source": source, "season": season, "person_kind": kind,
                    "person_key": key, "hire_coach_id": hire_coach_id})
    return out


# ------------------------------------------------------------------------------------------- person lifecycle ----
# docs/done/PERSONALITY_DRAFT_INTEGRATION_PLAN.md §2.5: every fictional coach the carousel (or JR's
# fire_coordinator replacement) creates gets one durable creation event and a source UUID from it, never from his
# name, team or job; a promotion adds an alias to the same identity; retreads and rehires keep theirs; a same-cycle
# stand-in whose row is deleted is archived. The carousel's draws and writes are the same with or without this.
STAFF_FEATURE = "staff_lifecycle"


def staff_person_id(universe_id, creation_event):
    """A created coach's source_person_id: uuid5 of the universe and his durable creation event."""
    import uuid
    from personality import lifecycle as L
    return str(uuid.uuid5(L.LIFECYCLE_NS, f"{universe_id}|staff-create|{creation_event}"))


def staff_lifecycle(cur, events, context=None):
    """Applies the cycle's staff events to the person lifecycle. context None = observe: nothing is read or written,
    the report counts the events. context {"universe_id", "cutoff", "receipt"} (receipt: JR's reviewed `staff:coach`
    generated classification) = initialize each created coach once (alias db-coordinator / db-coach), add the db-coach
    alias to a promoted coordinator's identity, check retreads/rehires resolve, archive deleted stand-ins. A coach whose
    identity can't be resolved (a real coach still waiting for his reviewed profile, an unregistered person) stays
    pending; a conflict (e.g. an invented name matching an existing coach row) is quarantined, never merged."""
    info = {"mode": "enforce" if context else "observe", "events": len(events),
            "created": sum(1 for e in events if e["kind"] == "created"), "pending": [], "quarantined": []}
    if not context:
        return info
    from personality import lifecycle as L, lifecycle_store as LS
    receipt = L.validate_receipt(context["receipt"])
    if receipt["scope"] != "staff:coach" or receipt["origin"] != "generated":
        raise L.LifecycleError("fictional coaches need a reviewed generated staff:coach receipt")
    uid, cutoff = context["universe_id"], L.validate_cutoff(context["cutoff"])

    def resolve(namespace, external_id):
        r = LS.resolve_person(cur, uid, "coach", namespace, str(external_id), cutoff=cutoff, feature=STAFF_FEATURE,
                              current=True)
        return r["identity"]["source_person_id"] if r["status"] == "ready" else None
    for e in events:
        alias = {"namespace": e["namespace"], "external_id": str(e["external_id"])}
        try:
            if e["kind"] == "created":
                if e.get("reused_row"):
                    info["quarantined"].append(dict(e, reason="invented name matched an existing coach row"))
                    continue
                spid = staff_person_id(uid, e["event"])
                LS.initialize_person(cur, uid, "coach", spid, "generated", cutoff["season"], cutoff=cutoff,
                                     receipt=dict(receipt, creation_event=e["event"], creator=e["creator"]),
                                     aliases=[alias])
            elif e["kind"] == "promoted":
                spid = resolve("db-coordinator", e["coordinator_id"])
                if spid is None:
                    info["pending"].append(dict(e, reason="coordinator identity unresolved"))
                else:
                    LS.bind_person(cur, uid, "coach", spid, [alias], cutoff=cutoff,
                                   receipt={"event": e["event"], "evidence": "promotion"})
            elif e["kind"] == "retained":
                if resolve(e["namespace"], e["external_id"]) is None:
                    info["pending"].append(dict(e, reason="identity unresolved"))
            elif e["kind"] == "deleted":
                spid = resolve("db-coordinator", e["external_id"])
                if spid is not None:
                    LS.archive_person(cur, uid, "coach", spid, cutoff=cutoff, reason=e["event"])
                    LS.close_binding(cur, uid, "coach", "db-coordinator", str(e["external_id"]), cutoff=cutoff)
        except L.LifecycleError as ex:
            info["quarantined"].append(dict(e, reason=str(ex)))
    return info


def hiring_cycle(cur, season, log=print, personality=None):
    """Fill every opening for season + 1 inside the caller's transaction: head coaches first (most attractive
    opening first, opening_appeal),
    each poach's vacancy by an internal promotion, the new head coach's own coordinator on his side (OFFENSIVE: the
    OC, DEFENSIVE: the DC, CEO: none), then the coordinator openings left (most attractive team first).
    JR's teams fill only the coordinator jobs JR left open himself (engines/fire_coordinator.py).
    Returns {head_coaches: [...], coordinators: [...], jr_losses: [...] (JR's coordinators poached),
    jr_hires: [...] (the jobs JR left open), personality (staff_lifecycle: observe without `personality`)}.

    Coaching-tree hiring (switchboard `coaching_tree_hiring`, engines/coaching_tree_hiring.py; COACHING_TREES_PLAN
    §2.8): off -> exactly the above; active -> tree terms added before each pick and a new head coach may bring an
    out-of-work protégé (source REHIRED) instead of the invented stand-in; shadow -> picks as off, plus
    report['tree_shadow'] listing each opening where the tree terms would have picked someone else.

    Owners (OWNERS_SCOUTS_VOTERS_PLAN §2.3, U5; CPU head-coach openings only, never JR's teams): `owner_hiring`
    active adds owner_policy.appeal_term to the opening's appeal and hiring_key to each candidate's score before
    the pick; `owner_meddling` active lets the owner review the pick (owner_policy.meddle_choice): VETO picks again
    without that candidate, PUSH hires owner_alternative instead, and either is stored in owner_decisions when the
    table exists. Shadow (either feature): picks as off, report['owner_shadow'] lists what the owner would have
    changed. Off: unchanged."""
    nxt = season + 1
    tm, mine = teams(cur), jr_teams(cur)
    ranks, res = unit_ranks(cur, season), chs.playoff_results(cur, season)
    staff, hcs = staff_in(cur, season), head_coaches(cur, season)
    years, taken = _coordinator_years(cur, season), taken_names(cur)
    cur.execute("""SELECT h.team_id, h.coach_id, c.coach_name FROM coach_hot_seat h JOIN coaches c ON c.id = h.coach_id
                   WHERE h.season = %s AND h.fired""", (season,))
    fired_hc = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    cur.execute("SELECT team_id, role, coordinator_id FROM coordinator_hot_seat WHERE season = %s AND fired", (season,))
    fired_coord = {(r[0], r[1]): r[2] for r in cur.fetchall()}
    report = {"head_coaches": [], "coordinators": [], "jr_losses": [], "jr_hires": []}
    created = set()                                 # coordinators this cycle created: only these may be undone
    events = []                                     # person lifecycle events (staff_lifecycle), in order
    tmode, tree, followed_ids = th.tree_mode(cur), None, set()
    from engines import owner_context as octx      # not `oc`: the hiring loop names the OC `oc`
    hmode, mmode = octx.feature_mode(cur, "owner_hiring"), octx.feature_mode(cur, "owner_meddling")
    owners = octx.team_owner_views(cur, exclude=mine) if (hmode, mmode) != ("off", "off") else {}
    try:
        godfather_owners = owners or octx.team_owner_views(cur, exclude=mine)   # §2.3 reads the same owner views
    except Exception:
        godfather_owners = {}
    if "shadow" in (hmode, mmode):
        report["owner_shadow"] = []
    if (hmode, mmode) != ("off", "off"):
        report["owner_modes"] = {"owner_hiring": hmode, "owner_meddling": mmode}
    if tmode != "off":
        from engines import coaching_tree_facts
        tree = {"graph": coaching_tree_facts.tree_graph(cur, through=season), "records": th.mentor_records(cur)}
        if tmode == "shadow":
            report["tree_shadow"] = []

    def tpick(pool, seed, opening, hiring_hc_pk=None):
        """pick(), with the coaching-tree terms when the feature is active (shadow: noted, not applied)."""
        base = pick(pool, seed)
        if tree is None:
            return base
        alt = pick(th.adjusted(pool, graph=tree["graph"], hiring_hc_pk=hiring_hc_pk, records=tree["records"]), seed)
        chosen = next(c for c in pool if c["key"] == alt["key"])
        if tmode == "active":
            return chosen
        if chosen["key"] != base["key"]:
            report["tree_shadow"].append({"opening": opening, "picked": base.get("name") or base["key"],
                                          "tree_pick": chosen.get("name") or chosen["key"]})
        return base

    def promote(team_id, role, prev, why):
        """An internal promotion who keeps the system (prev: the coordinator he replaces)."""
        seed = f"{season}:{team_id}:{role}:promo"
        name = new_name(seed, taken)
        bg = background(seed, "PROMOTED", role)
        notes = f"{tm[team_id]['name']} {role} from {nxt}; promoted from {tm[team_id]['name']} {bg} {why}"
        cid = _insert_coordinator(cur, name, role, prev.get("scheme"), prev.get("playbook"), prev, notes)
        _store_created_profile(cur, cid, name, role, scheme=prev.get("scheme"),
                               playbook=_mapped_playbook(prev.get("playbook")), tendencies=prev)
        created.add(cid)
        events.append({"kind": "created", "event": f"carousel:{seed}", "creator": "coaching_carousel:PROMOTED",
                       "namespace": "db-coordinator", "external_id": cid})
        cur.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season) VALUES (%s, %s, %s, %s)",
                    (cid, team_id, role, nxt))
        _move(cur, season, team_id, role, "HIRED", name, coordinator_id=cid, source="PROMOTED",
              score=PROMOTION_SCORE,
              notes=f"promoted from {bg}; keeps " + (f"{prev['name']}'s system" if prev.get("name") else "the system"))
        return name

    # Head-coach candidates: other teams' coordinators (poach), former head coaches (retread), one invented outsider.
    cands = []
    sliders = candidate_sliders(cur)                # drive/ego for the §2.3 Godfather stature (P1-27)
    for (tid, role), c in staff.items():
        if (tid, role) in fired_coord:              # JR's coordinators can be poached too
            continue
        ur, tr = unit_of(ranks, tid, role)
        cands.append({"key": f"coord:{c['coordinator_id']}", "kind": "POACHED", "team_id": tid, "role": role, **c,
                      **_sliders_for(sliders, "db-coordinator", c["coordinator_id"]),
                      "champion": res.get(tid, {}).get("champion", False),
                      "score": coordinator_candidacy(ur, tr, res.get(tid, {}).get("wins", 0),
                                                     res.get(tid, {}).get("champion", False),
                                                     years.get(c["coordinator_id"], 1))})
    cur.execute("""SELECT c.id, c.coach_name, c.slant, c.offensive_scheme, c.defensive_scheme, c.pass_lean,
                          c.blitz_rate, c.man_coverage, r.career_wins, r.career_losses, r.playoff_wins,
                          r.super_bowl_wins
                   FROM coaches c LEFT JOIN coach_records r ON r.coach_id = c.id
                   WHERE EXISTS (SELECT 1 FROM coach_tenures t WHERE t.coach_id = c.id AND t.end_season BETWEEN %s AND %s)
                     AND NOT EXISTS (SELECT 1 FROM coach_tenures t WHERE t.coach_id = c.id
                                       AND (t.end_season IS NULL OR t.end_season >= %s))
                     AND NOT EXISTS (SELECT 1 FROM gm_profiles g WHERE g.coach_id = c.id AND g.end_season IS NULL)""",
                (season - 2, season, nxt))
    for cid, name, slant, off, de, pl, br, mc, w, l, pw, sbw in cur.fetchall():
        pct = w / (w + l) if w is not None and (w + l) else 0.45
        cands.append({"key": f"coach:{cid}", "kind": "RETREAD", "coach_id": cid, "name": name, "slant": slant,
                      "offensive_scheme": off, "defensive_scheme": de, "pass_lean": pl, "blitz_rate": br,
                      "man_coverage": mc, "super_bowl_wins": sbw,
                      **_sliders_for(sliders, "db-coach", cid),
                      "score": round((pct - 0.5) * 4 + (0.3 if pw else 0) - RETREAD_PENALTY, 3)})

    def opick(pool, seed, opening, tid):
        """tpick() for a head-coach opening, with the owner's hiring term and meddling by their modes."""
        view = owners.get(tid)
        if view is None:
            return tpick(pool, seed, opening)
        if hmode == "active":
            alt = tpick(owner_adjusted(pool, view), seed, opening)
            c = next(x for x in pool if x["key"] == alt["key"])
        else:
            c = tpick(pool, seed, opening)
            if hmode == "shadow":
                adj = owner_adjusted(pool, view)
                if tree is not None and tmode == "active":
                    adj = th.adjusted(adj, graph=tree["graph"], records=tree["records"])
                alt = pick(adj, seed)
                if alt["key"] != c["key"]:
                    report["owner_shadow"].append({"opening": opening, "feature": "owner_hiring",
                                                   "picked": c.get("name") or c["key"],
                                                   "owner_pick": alt.get("name") or alt["key"]})
        if mmode == "off":
            return c
        want = owner_alternative(pool, c, view)
        move = {"kind": "HIRE", "role": "HC", "team_id": tid, "ref": c["key"], "user_controlled": False,
                "alternative": want["key"] if want else None}
        used = octx.vetoes_used(cur, season, tid)
        decision = op.meddle_choice(move, view, season=season, tick=HIRE_TICK, vetoes_used=used)
        if decision in (None, "APPROVE"):
            return c
        if decision == "VETO":
            rest = [x for x in pool if x["key"] != c["key"]]
            new = tpick(rest, f"{seed}:veto", opening) if rest else c
        else:
            new = want
        if mmode == "shadow":
            report["owner_shadow"].append({"opening": opening, "feature": "owner_meddling", "decision": decision,
                                           "picked": c.get("name") or c["key"],
                                           "owner_pick": new.get("name") or new["key"]})
            return c
        octx.record_decision(cur, season=season, tick=HIRE_TICK, team_id=tid, move_kind="HIRE",
                           move_ref={"ref": c["key"], "name": c.get("name")}, decision=decision,
                           alternative={"ref": new["key"], "name": new.get("name")})
        report.setdefault("owner_decisions", []).append({"opening": opening, "decision": decision,
                                                         "vetoed": c.get("name") or c["key"],
                                                         "hired": new.get("name") or new["key"]})
        return new

    parts = opening_parts(cur, season, list(fired_hc))
    appeal = {t: opening_appeal(p) for t, p in parts.items()}
    if hmode != "off":
        owner_appeal = {t: round(a + op.appeal_term(owners[t]), 3) if t in owners else a for t, a in appeal.items()}
        if hmode == "active":
            appeal = owner_appeal
        elif sorted(fired_hc, key=lambda t: (-owner_appeal[t], t)) != sorted(fired_hc, key=lambda t: (-appeal[t], t)):
            report["owner_shadow"].append({"feature": "owner_hiring", "what": "opening order",
                                           "owner_appeal": {tm[t]["abbr"]: owner_appeal[t] for t in fired_hc}})
    ranked = sorted(fired_hc, key=lambda t: (-appeal[t], t))           # the best job gets first pick
    for rank, tid in enumerate(ranked, 1):
        old_id, old_name = fired_hc[tid]
        abbr = tm[tid]["abbr"]
        seed = f"{season}:{abbr}:HC"
        outsider = {"key": f"outside:{abbr}", "kind": "OUTSIDE", "score": OUTSIDE_SCORE["HC"]}
        pool = [c for c in cands if not (c["kind"] == "RETREAD" and c["coach_id"] == old_id)] + [outsider]
        c = opick(pool, seed, f"{abbr} HC", tid)
        if c is not outsider:
            cands.remove(c)
        oc, dc = staff.get((tid, "OC"), {}), staff.get((tid, "DC"), {})
        # Who he is, his slant, and the schemes he brings (the other side stays with the retained coordinator).
        if c["kind"] == "POACHED":
            slant, name = SIDE[c["role"]], c["name"]
            origin = f"{tm[c['team_id']]['name']} {c['role']}"
            mine_side = c
        elif c["kind"] == "RETREAD":
            slant, name = c["slant"] or "CEO", c["name"]
            origin = "former head coach"
            mine_side = {"scheme": c["offensive_scheme"] if slant == "OFFENSIVE" else c["defensive_scheme"],
                         "pass_lean": c["pass_lean"], "blitz_rate": c["blitz_rate"], "man_coverage": c["man_coverage"]}
        else:
            rng = random.Random(f"hcslant:{seed}")
            slant = rng.choices([s for s, _ in OUTSIDE_SLANTS], [w for _, w in OUTSIDE_SLANTS])[0]
            name = new_name(seed, taken)
            origin = background(seed, "OUTSIDE", "HC")
            base = oc if slant == "OFFENSIVE" else dc
            mine_side = {"scheme": base.get("scheme"), "pass_lean": base.get("pass_lean"),
                         "blitz_rate": base.get("blitz_rate"), "man_coverage": base.get("man_coverage")}
        off = mine_side["scheme"] if slant == "OFFENSIVE" else oc.get("scheme")
        de = mine_side["scheme"] if slant == "DEFENSIVE" else dc.get("scheme")
        tend = {"pass_lean": (mine_side if slant == "OFFENSIVE" else oc).get("pass_lean") or "Medium",
                "blitz_rate": (mine_side if slant == "DEFENSIVE" else dc).get("blitz_rate") or "Medium",
                "man_coverage": (mine_side if slant == "DEFENSIVE" else dc).get("man_coverage") or "Medium"}
        cur.execute("SELECT id FROM coaches WHERE lower(coach_name) = lower(%s)", (name,))
        row = cur.fetchone()
        reused_row = bool(row)
        if row:
            coach_id = row[0]
            cur.execute("""UPDATE coaches SET slant = %s, offensive_scheme = %s, defensive_scheme = %s, pass_lean = %s,
                                              blitz_rate = %s, man_coverage = %s WHERE id = %s""",
                        (slant, off, de, tend["pass_lean"], tend["blitz_rate"], tend["man_coverage"], coach_id))
        else:
            cur.execute("""INSERT INTO coaches (coach_name, slant, offensive_scheme, defensive_scheme, pass_lean,
                                                fourth_down_aggressiveness, blitz_rate, man_coverage)
                           VALUES (%s, %s, %s, %s, %s, 'Medium', %s, %s) RETURNING id""",
                        (name, slant, off, de, tend["pass_lean"], tend["blitz_rate"], tend["man_coverage"]))
            coach_id = cur.fetchone()[0]
            _store_created_profile(cur, coach_id, name, "HC", slant=slant,
                                   scheme={"offensive": off, "defensive": de},
                                   playbook={"offensive": _mapped_playbook(oc.get("playbook")),
                                             "defensive": _mapped_playbook(dc.get("playbook"))},
                                   tendencies=tend,
                                   promote_from=c.get("coordinator_id") if c.get("kind") == "POACHED" else None)
        cur.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season) VALUES (%s, %s, %s)",
                    (coach_id, tid, nxt))
        src = c["kind"]
        if src == "POACHED":           # the same person, now also a head coach: one identity, one more alias
            events.append({"kind": "promoted", "event": f"carousel:{seed}", "coordinator_id": c["coordinator_id"],
                           "namespace": "db-coach", "external_id": coach_id})
        elif src == "RETREAD":
            events.append({"kind": "retained", "event": f"carousel:{seed}", "namespace": "db-coach",
                           "external_id": coach_id})
        else:
            events.append({"kind": "created", "event": f"carousel:{seed}", "creator": "coaching_carousel:OUTSIDE",
                           "namespace": "db-coach", "external_id": coach_id, "reused_row": reused_row})
        _move(cur, season, tid, "HC", "HIRED", name, coach_id=coach_id, source=src,
              from_team_id=c.get("team_id"), from_role=c.get("role"), slant=slant, score=c["score"],
              notes=f"replaces {old_name}; {origin}",
              inputs=json.dumps({"opening_rank": rank, "openings": len(ranked), "appeal": appeal[tid],
                                 "parts": parts[tid]}))
        report["head_coaches"].append({"team": abbr, "coach": name, "slant": slant, "source": src, "from": origin,
                                       "replaces": old_name, "opening_rank": rank, "appeal": appeal[tid]})
        log(f"  hiring: {abbr} (opening #{rank}, appeal {appeal[tid]}) hires {name} ({slant}, {src.lower()}: "
            f"{origin}) to replace {old_name}")

        # A rare §2.3 Godfather offer: when every hard gate passes, the new head coach also becomes the GM.
        godfather = _maybe_godfather(cur, season, tid, abbr, coach_id, name, slant, c,
                                     godfather_owners.get(tid), log=log)
        if godfather:
            report["head_coaches"][-1]["full_control"] = True

        # A poached coordinator leaves his job; his team promotes from within (unless it is the hiring team).
        if src == "POACHED":
            ft, fr = c["team_id"], c["role"]
            cur.execute("""UPDATE coordinator_tenures SET end_season = %s
                           WHERE coordinator_id = %s AND team_id = %s AND role = %s
                             AND (end_season IS NULL OR end_season > %s)""",
                        (season, c["coordinator_id"], ft, fr, season))
            _move(cur, season, ft, fr, "LEFT", name, coordinator_id=c["coordinator_id"],
                  notes=f"hired as {tm[tid]['city']} {tm[tid]['name']} head coach")
            # Resolution JC-2A: a reviewed-eligible coordinator leaving for a head-coach job is journalled for the
            # compensatory-picks producer. Additive only: the hiring decisions above are untouched.
            record_staff_departure(cur, season, "COORDINATOR", c["coordinator_id"], name, ft, tid, fr, "HC",
                                   years.get(c["coordinator_id"], 1), source="coaching_carousel:POACHED",
                                   hire_coach_id=coach_id)
            if ft != tid:
                promoted = promote(ft, fr, c, f"after {name} left to coach the {tm[tid]['name']}")
                if ft in mine:
                    report["jr_losses"].append({"team": tm[ft]["abbr"], "role": fr, "lost": name, "to": abbr,
                                                "promoted": promoted})
        # The new head coach brings his own coordinator on his side of the ball.
        if slant in ("OFFENSIVE", "DEFENSIVE"):
            role = "OC" if slant == "OFFENSIVE" else "DC"
            cur_c = staff_in(cur, nxt).get((tid, role))           # still in the job for next season, if anyone
            if cur_c and cur_c["coordinator_id"] in created:
                # Named earlier in this cycle (a promotion after a poach): the new head coach's pick takes the job
                # instead, so the stand-in is undone rather than fired.
                cur.execute("DELETE FROM staff_moves WHERE season = %s AND coordinator_id = %s",
                            (season, cur_c["coordinator_id"]))
                cur.execute("DELETE FROM coordinator_tenures WHERE coordinator_id = %s", (cur_c["coordinator_id"],))
                cur.execute("DELETE FROM coordinators WHERE id = %s", (cur_c["coordinator_id"],))
                events.append({"kind": "deleted", "event": f"carousel:{season}:{abbr}:{role}:undone",
                               "namespace": "db-coordinator", "external_id": cur_c["coordinator_id"]})
                report["coordinators"] = [x for x in report["coordinators"] if x["coordinator"] != cur_c["name"]]
            elif cur_c:
                # Anyone else keeps his record (hot-seat rows, history): he is replaced. One already named for next
                # season by someone else (a migration, the manifest) never starts there, so that tenure goes.
                if cur_c["start_season"] == nxt:
                    cur.execute("DELETE FROM coordinator_tenures WHERE coordinator_id = %s AND team_id = %s "
                                "AND role = %s AND start_season = %s", (cur_c["coordinator_id"], tid, role, nxt))
                cur.execute("""UPDATE coordinator_tenures SET end_season = %s
                               WHERE coordinator_id = %s AND team_id = %s AND role = %s
                                 AND (end_season IS NULL OR end_season > %s)""",
                            (season, cur_c["coordinator_id"], tid, role, season))
                _move(cur, season, tid, role, "REPLACED", cur_c["name"], coordinator_id=cur_c["coordinator_id"],
                      notes=f"{name} brought in his own {role}")
                fired_coord[(tid, role)] = cur_c["coordinator_id"]      # out of a job: a rehire candidate
                cands[:] = [x for x in cands if x.get("coordinator_id") != cur_c["coordinator_id"]]
            follow = None
            if tree is not None:
                where_now = {v: k for k, v in fired_coord.items()}
                ids = sorted(set(fired_coord.values()) - followed_ids)
                cur.execute("SELECT id, coordinator_name, scheme FROM coordinators WHERE id = ANY(%s)", (ids,))
                ow = [{"coordinator_id": r[0], "name": r[1], "scheme": r[2], "team_id": where_now[r[0]][0],
                       "role": where_now[r[0]][1]} for r in cur.fetchall()]
                fpool = th.follow_candidates(ct.person_key(name), ow, graph=tree["graph"], role=role)
                if fpool:
                    fpick = pick([{"key": "hc_staff", "kind": "HC_STAFF", "score": 0.0}] + fpool,
                                 f"{season}:{abbr}:{role}:follow")
                    if fpick["kind"] == "FOLLOWED" and tmode == "active":
                        follow = fpick
                    elif fpick["kind"] == "FOLLOWED":
                        report["tree_shadow"].append({"opening": f"{abbr} {role}", "picked": "HC_STAFF stand-in",
                                                      "tree_pick": fpick["name"]})
            if follow is not None:
                fid = follow["coordinator_id"]
                followed_ids.add(fid)
                events.append({"kind": "retained", "event": f"carousel:{season}:{abbr}:{role}:follow",
                               "namespace": "db-coordinator", "external_id": fid})
                cur.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season) "
                            "VALUES (%s, %s, %s, %s)", (fid, tid, role, nxt))
                _move(cur, season, tid, role, "HIRED", follow["name"], coordinator_id=fid, source="REHIRED",
                      from_team_id=follow["team_id"], from_role=follow["role"], score=th.FOLLOW_SCORE,
                      notes=f"follows his mentor {name}")
                report["coordinators"].append({"team": abbr, "role": role, "coordinator": follow["name"],
                                               "source": "REHIRED"})
            else:
                lseed = f"{season}:{abbr}:{role}:hcstaff"
                lname = new_name(lseed, taken)
                bg = background(lseed, "PROMOTED", role)
                notes = f"{tm[tid]['name']} {role} under {name} from {nxt}; {name}'s {bg} before following him"
                cid = _insert_coordinator(cur, lname, role, mine_side["scheme"], f"{tm[tid]['name']} "
                                          f"{'Offense' if role == 'OC' else 'Defense'}", tend, notes)
                _store_created_profile(cur, cid, lname, role, scheme=mine_side["scheme"], playbook=None,
                                       tendencies=tend)
                created.add(cid)
                events.append({"kind": "created", "event": f"carousel:{lseed}", "creator": "coaching_carousel:HC_STAFF",
                               "namespace": "db-coordinator", "external_id": cid})
                cur.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season) "
                            "VALUES (%s, %s, %s, %s)", (cid, tid, role, nxt))
                _move(cur, season, tid, role, "HIRED", lname, coordinator_id=cid, source="HC_STAFF",
                      notes=f"{name}'s {bg}; runs his {mine_side['scheme'] or 'system'}")
                report["coordinators"].append({"team": abbr, "role": role, "coordinator": lname, "source": "HC_STAFF"})

    # Coordinator openings left: fired coordinators' jobs (JR's teams: only the ones he fired) and a poached team's
    # own vacancy.
    nstaff = staff_in(cur, nxt)
    cur.execute("""SELECT c.id, c.coordinator_name, c.scheme, c.playbook, c.pass_lean, c.blitz_rate, c.man_coverage
                   FROM coordinators c WHERE c.id = ANY(%s)""", (list(set(fired_coord.values()) - followed_ids),))
    out_of_work = {r[0]: dict(zip(("coordinator_id", "name", "scheme", "playbook", "pass_lean", "blitz_rate",
                                   "man_coverage"), r)) for r in cur.fetchall()}
    where = {v: k for k, v in fired_coord.items()}
    cpu = [t for t in tm if t not in mine]
    hiring = cpu + sorted({t for t, _ in fired_coord if t in mine})
    cparts = opening_parts(cur, season, hiring)
    next_hcs = head_coaches(cur, nxt) if tree is not None else {}
    for tid in sorted(hiring, key=lambda t: (-opening_appeal(cparts[t]), t)):     # best job first
        for role in ("OC", "DC"):
            if (tid, role) in nstaff or (tid in mine and (tid, role) not in fired_coord):
                continue
            abbr = tm[tid]["abbr"]
            prev = staff.get((tid, role), {})
            seed = f"{season}:{abbr}:{role}"
            pool = [{"key": "promote", "kind": "PROMOTED", "score": PROMOTION_SCORE},
                    {"key": f"outside:{abbr}:{role}", "kind": "OUTSIDE", "score": OUTSIDE_SCORE[role]}]
            for cid, c in out_of_work.items():
                ft, fr = where[cid]
                if fr != role or ft == tid:
                    continue
                ur, trk = unit_of(ranks, ft, fr)
                pool.append({"key": f"coord:{cid}", "kind": "REHIRED", **c, "team_id": ft, "role": fr,
                             "score": round(coordinator_candidacy(ur, trk, years=years.get(cid, 1)) - 0.3, 3)})
            hc_pk = ct.person_key(next_hcs[tid]["name"]) if tid in next_hcs else "p:"
            c = tpick(pool, seed, f"{abbr} {role}", hiring_hc_pk=hc_pk)
            if c["kind"] == "PROMOTED":
                name = promote(tid, role, prev, f"to replace {prev.get('name') or 'the departed coordinator'}")
            elif c["kind"] == "REHIRED":
                name = c["name"]
                out_of_work.pop(c["coordinator_id"])
                events.append({"kind": "retained", "event": f"carousel:{seed}", "namespace": "db-coordinator",
                               "external_id": c["coordinator_id"]})
                cur.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season) "
                            "VALUES (%s, %s, %s, %s)", (c["coordinator_id"], tid, role, nxt))
                _move(cur, season, tid, role, "HIRED", name, coordinator_id=c["coordinator_id"], source="REHIRED",
                      from_team_id=c["team_id"], from_role=role, score=c["score"],
                      notes=f"former {tm[c['team_id']]['name']} {role}; brings his {c['scheme'] or 'system'}")
            else:
                name = new_name(seed, taken)
                bg = background(seed, "OUTSIDE", role)
                notes = f"{tm[tid]['name']} {role} from {nxt}; previously {bg}; keeps the {prev.get('scheme') or 'existing'} system"
                cid = _insert_coordinator(cur, name, role, prev.get("scheme"), prev.get("playbook"), prev, notes)
                _store_created_profile(cur, cid, name, role, scheme=prev.get("scheme"),
                                       playbook=_mapped_playbook(prev.get("playbook")), tendencies=prev)
                events.append({"kind": "created", "event": f"carousel:{seed}", "creator": "coaching_carousel:OUTSIDE",
                               "namespace": "db-coordinator", "external_id": cid})
                cur.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season) "
                            "VALUES (%s, %s, %s, %s)", (cid, tid, role, nxt))
                _move(cur, season, tid, role, "HIRED", name, coordinator_id=cid, source="OUTSIDE",
                      score=OUTSIDE_SCORE[role], notes=f"previously {bg}; keeps the system")
            report["coordinators"].append({"team": abbr, "role": role, "coordinator": name, "source": c["kind"]})
            if tid in mine:
                report["jr_hires"].append({"team": abbr, "role": role, "coordinator": name, "source": c["kind"],
                                           "replaces": prev.get("name")})
            log(f"  hiring: {abbr} {role} {name} ({c['kind'].lower()})")

    cur.execute("SELECT DISTINCT team_id FROM staff_moves WHERE season = %s", (season,))
    check_staffs(cur, nxt, (set(tm) - mine) | {r[0] for r in cur.fetchall()})
    # P1-27 runs the GM hot seat BEFORE this cycle, so the seats it vacated are still empty; a Godfather coach-GM
    # fills his own, every other one is filled here, after the head-coach hires.
    from engines import gm_rollover
    # `season` here is the season just finished; the seat is being filled for `nxt`, the season about to start
    # (that is what hot_seat_only(cur, season + 1) works with), so the hire's start, first draft and signed
    # season are all `nxt`.
    report["gm_fills"] = gm_rollover.fill_vacant_gms(cur, nxt, log=log)
    report["personality"] = staff_lifecycle(cur, events, personality)
    return report


def check_staffs(cur, season, team_ids):
    """Every team in team_ids has exactly one head coach, OC and DC in `season`; raises otherwise."""
    bad = []
    for tid in team_ids:
        cur.execute("""SELECT (SELECT count(*) FROM coach_tenures WHERE team_id = %(t)s AND start_season <= %(s)s
                                 AND (end_season IS NULL OR end_season >= %(s)s)),
                              (SELECT count(*) FROM coordinator_tenures WHERE team_id = %(t)s AND role = 'OC'
                                 AND start_season <= %(s)s AND (end_season IS NULL OR end_season >= %(s)s)),
                              (SELECT count(*) FROM coordinator_tenures WHERE team_id = %(t)s AND role = 'DC'
                                 AND start_season <= %(s)s AND (end_season IS NULL OR end_season >= %(s)s))""",
                    {"t": tid, "s": season})
        counts = cur.fetchone()
        if counts != (1, 1, 1):
            bad.append(f"team {tid}: HC/OC/DC {counts}")
    if bad:
        raise ValueError(f"staff check for {season} failed: " + "; ".join(bad))


# ------------------------------------------------------------------------------------------------ orchestration ----
def installed(cur):
    cur.execute("SELECT to_regclass('staff_moves') IS NOT NULL AND to_regclass('coordinator_hot_seat') IS NOT NULL")
    return cur.fetchone()[0]


def owns(cur, season):
    """True for the seasons the carousel decides: every season from FIRST_SEASON on, except one the league finished
    before the carousel ever ran for it (a season before league_state's with no coach_hot_seat, coordinator_hot_seat or
    staff_moves rows). That is madden_2036's 2035: the offseason manifest set it and 2036's staff is already named, so
    the weekly run must never decide it."""
    if season < FIRST_SEASON:
        return False
    cur.execute("SELECT current_season FROM league_state WHERE id = 1")
    if season >= cur.fetchone()[0]:
        return True
    cur.execute("""SELECT EXISTS (SELECT 1 FROM coach_hot_seat WHERE season = %(s)s)
                       OR EXISTS (SELECT 1 FROM coordinator_hot_seat WHERE season = %(s)s)
                       OR EXISTS (SELECT 1 FROM staff_moves WHERE season = %(s)s)""", {"s": season})
    return cur.fetchone()[0]


def run(cur, season, log=print, personality=None):
    """Everything ready for `season`, inside the caller's transaction. Returns {season, hot_seat (the coach hot seat
    report), coordinators_fired, hires (None until the cycle runs)}. personality: staff_lifecycle's context."""
    rep = {"season": season, "hot_seat": chs.run(cur, season, log=log)}
    rep["coordinators_fired"] = review_coordinators(cur, season, log=log)
    rep["hires"] = hiring_cycle(cur, season, log=log, personality=personality) if ready_to_hire(cur, season) else None
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, help="the season just played (default league_state.current_season)")
    ap.add_argument("--dry-run", action="store_true", help="run everything ready, print the report, roll back")
    a = ap.parse_args(argv)
    import psycopg
    dsn = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT current_season FROM league_state WHERE id = 1")
        season = a.season or cur.fetchone()[0]
        rep = run(cur, season)
        print(json.dumps(rep, indent=1, default=str))
        if a.dry_run:
            conn.rollback()
            print("dry run: rolled back")
        else:
            conn.commit()


if __name__ == "__main__":
    main()
