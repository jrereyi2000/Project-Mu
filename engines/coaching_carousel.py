"""
Coaching carousel (PROJECT_SPEC §2.19): head-coach firings (engines/coach_hot_seat.py), coordinator firings, and the
hiring cycle after the Super Bowl, with poaching. JR mirrors head-coach changes in Madden: he fires the coaches Mu
fires and hires any coach for each opening, and Mu's hire stands for that coach. Coordinators are Mu's own.

Run by the weekly run's Step 7d (pipeline/weekly_capture.step_coach_hot_seat; weekly, playoff and postgame runs) or by
hand:

  python3 -m engines.coaching_carousel [--season S] [--dry-run]

Each run does what is ready, once: the head-coach hot seat (Black Monday, then each playoff team once it is out), the
coordinator review of every team whose head coach kept his job (decided with him), and, once the Super Bowl is
complete and every team is decided, the hiring cycle for season + 1. JR's teams (gm_team_settings.user_controlled)
are never touched: no firings, no poaching from them. coordinator_seat() and pick() are pure; a second run changes
nothing (seeded draws).
"""
import argparse
import json
import math
import os
import random

from engines import coach_hot_seat as chs
from engines.staff_names import background, new_name, taken_names

MAX_COORD_FIRES = 8                # coordinators turn over far more than head coaches
COORD_LINE = 1.0
FIRST_YEAR_LINE = 1.5              # a coordinator in his first season needs a worse year
NOISE = 0.6                        # Gumbel scale on candidate scores: hiring is never a sure thing
OUTSIDE_SCORE = {"HC": 0.5, "OC": 0.2, "DC": 0.2}
PROMOTION_SCORE = 0.4
RETREAD_PENALTY = 0.3              # owners prefer a fresh face
OUTSIDE_SLANTS = (("OFFENSIVE", 0.45), ("DEFENSIVE", 0.35), ("CEO", 0.2))
SIDE = {"OC": "OFFENSIVE", "DC": "DEFENSIVE"}


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
    cur.execute("SELECT count(*) FROM coordinator_hot_seat WHERE season = %s AND fired", (season,))
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
def gumbel(key):
    u = random.Random(f"hire:{key}").random()
    return -NOISE * math.log(-math.log(min(max(u, 1e-12), 1 - 1e-12)))


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
    cur.execute("SELECT count(*) FROM staff_moves WHERE season = %s", (season,))
    return cur.fetchone()[0] == 0


def _insert_coordinator(cur, name, role, scheme, playbook, tendencies, notes):
    cur.execute("""INSERT INTO coordinators (coordinator_name, side, scheme, playbook, pass_lean, blitz_rate,
                                             man_coverage, origin_notes)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                (name, SIDE[role], scheme, playbook,
                 tendencies.get("pass_lean") if role == "OC" else None,
                 tendencies.get("blitz_rate") if role == "DC" else None,
                 tendencies.get("man_coverage") if role == "DC" else None, notes))
    return cur.fetchone()[0]


def _move(cur, season, team_id, role, action, person, **kw):
    cols = ["season", "team_id", "role", "action", "person"] + list(kw)
    cur.execute(f"INSERT INTO staff_moves ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                (season, team_id, role, action, person, *kw.values()))


def hiring_cycle(cur, season, log=print):
    """Fill every opening for season + 1 inside the caller's transaction: head coaches first (worst record first),
    each poach's vacancy by an internal promotion, the new head coach's own coordinator on his side (OFFENSIVE: the
    OC, DEFENSIVE: the DC, CEO: none), then the coordinator openings left (worst record first). Returns
    {head_coaches: [...], coordinators: [...]}."""
    nxt = season + 1
    tm, mine = teams(cur), jr_teams(cur)
    ranks, res = unit_ranks(cur, season), chs.playoff_results(cur, season)
    staff, hcs = staff_in(cur, season), head_coaches(cur, season)
    years, taken = _coordinator_years(cur, season), taken_names(cur)
    cur.execute("SELECT team_id, wins, expected_wins FROM team_seasons WHERE season = %s", (season,))
    record = {r[0]: (r[1] or 0, -(r[2] or 0)) for r in cur.fetchall()}
    order = lambda tids: sorted(tids, key=lambda t: (record.get(t, (0, 0)), t))
    cur.execute("""SELECT h.team_id, h.coach_id, c.coach_name FROM coach_hot_seat h JOIN coaches c ON c.id = h.coach_id
                   WHERE h.season = %s AND h.fired""", (season,))
    fired_hc = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    cur.execute("SELECT team_id, role, coordinator_id FROM coordinator_hot_seat WHERE season = %s AND fired", (season,))
    fired_coord = {(r[0], r[1]): r[2] for r in cur.fetchall()}
    report = {"head_coaches": [], "coordinators": []}

    def promote(team_id, role, prev, why):
        """An internal promotion who keeps the system (prev: the coordinator he replaces)."""
        seed = f"{season}:{team_id}:{role}:promo"
        name = new_name(seed, taken)
        bg = background(seed, "PROMOTED", role)
        notes = f"{tm[team_id]['name']} {role} from {nxt}; promoted from {tm[team_id]['name']} {bg} {why}"
        cid = _insert_coordinator(cur, name, role, prev.get("scheme"), prev.get("playbook"), prev, notes)
        cur.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season) VALUES (%s, %s, %s, %s)",
                    (cid, team_id, role, nxt))
        _move(cur, season, team_id, role, "HIRED", name, coordinator_id=cid, source="PROMOTED",
              score=PROMOTION_SCORE,
              notes=f"promoted from {bg}; keeps " + (f"{prev['name']}'s system" if prev.get("name") else "the system"))
        return name

    # Head-coach candidates: other teams' coordinators (poach), former head coaches (retread), one invented outsider.
    cands = []
    for (tid, role), c in staff.items():
        if tid in mine or (tid, role) in fired_coord:
            continue
        ur, tr = unit_of(ranks, tid, role)
        cands.append({"key": f"coord:{c['coordinator_id']}", "kind": "POACHED", "team_id": tid, "role": role, **c,
                      "score": coordinator_candidacy(ur, tr, res.get(tid, {}).get("wins", 0),
                                                     res.get(tid, {}).get("champion", False),
                                                     years.get(c["coordinator_id"], 1))})
    cur.execute("""SELECT c.id, c.coach_name, c.slant, c.offensive_scheme, c.defensive_scheme, c.pass_lean,
                          c.blitz_rate, c.man_coverage, r.career_wins, r.career_losses, r.playoff_wins
                   FROM coaches c LEFT JOIN coach_records r ON r.coach_id = c.id
                   WHERE EXISTS (SELECT 1 FROM coach_tenures t WHERE t.coach_id = c.id AND t.end_season BETWEEN %s AND %s)
                     AND NOT EXISTS (SELECT 1 FROM coach_tenures t WHERE t.coach_id = c.id
                                       AND (t.end_season IS NULL OR t.end_season >= %s))
                     AND NOT EXISTS (SELECT 1 FROM gm_profiles g WHERE g.coach_id = c.id AND g.end_season IS NULL)""",
                (season - 2, season, nxt))
    for cid, name, slant, off, de, pl, br, mc, w, l, pw in cur.fetchall():
        pct = w / (w + l) if w is not None and (w + l) else 0.45
        cands.append({"key": f"coach:{cid}", "kind": "RETREAD", "coach_id": cid, "name": name, "slant": slant,
                      "offensive_scheme": off, "defensive_scheme": de, "pass_lean": pl, "blitz_rate": br,
                      "man_coverage": mc, "score": round((pct - 0.5) * 4 + (0.3 if pw else 0) - RETREAD_PENALTY, 3)})

    for tid in order(fired_hc):
        old_id, old_name = fired_hc[tid]
        abbr = tm[tid]["abbr"]
        seed = f"{season}:{abbr}:HC"
        outsider = {"key": f"outside:{abbr}", "kind": "OUTSIDE", "score": OUTSIDE_SCORE["HC"]}
        pool = [c for c in cands if not (c["kind"] == "RETREAD" and c["coach_id"] == old_id)] + [outsider]
        c = pick(pool, seed)
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
        cur.execute("INSERT INTO coach_tenures (coach_id, team_id, start_season) VALUES (%s, %s, %s)",
                    (coach_id, tid, nxt))
        src = c["kind"]
        _move(cur, season, tid, "HC", "HIRED", name, coach_id=coach_id, source=src,
              from_team_id=c.get("team_id"), from_role=c.get("role"), slant=slant, score=c["score"],
              notes=f"replaces {old_name}; {origin}")
        report["head_coaches"].append({"team": abbr, "coach": name, "slant": slant, "source": src, "from": origin,
                                       "replaces": old_name})
        log(f"  hiring: {abbr} hires {name} ({slant}, {src.lower()}: {origin}) to replace {old_name}")

        # A poached coordinator leaves his job; his team promotes from within (unless it is the hiring team).
        if src == "POACHED":
            ft, fr = c["team_id"], c["role"]
            cur.execute("""UPDATE coordinator_tenures SET end_season = %s
                           WHERE coordinator_id = %s AND team_id = %s AND role = %s
                             AND (end_season IS NULL OR end_season > %s)""",
                        (season, c["coordinator_id"], ft, fr, season))
            _move(cur, season, ft, fr, "LEFT", name, coordinator_id=c["coordinator_id"],
                  notes=f"hired as {tm[tid]['city']} {tm[tid]['name']} head coach")
            if ft != tid:
                promote(ft, fr, c, f"after {name} left to coach the {tm[tid]['name']}")
        # The new head coach brings his own coordinator on his side of the ball.
        if slant in ("OFFENSIVE", "DEFENSIVE"):
            role = "OC" if slant == "OFFENSIVE" else "DC"
            cur_c = staff_in(cur, nxt).get((tid, role))           # still in the job for next season, if anyone
            if cur_c and cur_c["start_season"] == nxt:
                # Named earlier in this cycle (a promotion after a poach): the new head coach's pick takes the job
                # instead, so the stand-in is undone rather than fired.
                cur.execute("DELETE FROM staff_moves WHERE season = %s AND coordinator_id = %s",
                            (season, cur_c["coordinator_id"]))
                cur.execute("DELETE FROM coordinator_tenures WHERE coordinator_id = %s", (cur_c["coordinator_id"],))
                cur.execute("DELETE FROM coordinators WHERE id = %s", (cur_c["coordinator_id"],))
                report["coordinators"] = [x for x in report["coordinators"] if x["coordinator"] != cur_c["name"]]
            elif cur_c:
                cur.execute("""UPDATE coordinator_tenures SET end_season = %s
                               WHERE coordinator_id = %s AND team_id = %s AND role = %s
                                 AND (end_season IS NULL OR end_season > %s)""",
                            (season, cur_c["coordinator_id"], tid, role, season))
                _move(cur, season, tid, role, "REPLACED", cur_c["name"], coordinator_id=cur_c["coordinator_id"],
                      notes=f"{name} brought in his own {role}")
                fired_coord[(tid, role)] = cur_c["coordinator_id"]      # out of a job: a rehire candidate
                cands[:] = [x for x in cands if x.get("coordinator_id") != cur_c["coordinator_id"]]
            lseed = f"{season}:{abbr}:{role}:hcstaff"
            lname = new_name(lseed, taken)
            bg = background(lseed, "PROMOTED", role)
            notes = f"{tm[tid]['name']} {role} under {name} from {nxt}; {name}'s {bg} before following him"
            cid = _insert_coordinator(cur, lname, role, mine_side["scheme"], f"{tm[tid]['name']} "
                                      f"{'Offense' if role == 'OC' else 'Defense'}", tend, notes)
            cur.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season) "
                        "VALUES (%s, %s, %s, %s)", (cid, tid, role, nxt))
            _move(cur, season, tid, role, "HIRED", lname, coordinator_id=cid, source="HC_STAFF",
                  notes=f"{name}'s {bg}; runs his {mine_side['scheme'] or 'system'}")
            report["coordinators"].append({"team": abbr, "role": role, "coordinator": lname, "source": "HC_STAFF"})

    # Coordinator openings left: fired coordinators' jobs and a poached team's own vacancy.
    nstaff = staff_in(cur, nxt)
    cur.execute("""SELECT c.id, c.coordinator_name, c.scheme, c.playbook, c.pass_lean, c.blitz_rate, c.man_coverage
                   FROM coordinators c WHERE c.id = ANY(%s)""", (list(set(fired_coord.values())),))
    out_of_work = {r[0]: dict(zip(("coordinator_id", "name", "scheme", "playbook", "pass_lean", "blitz_rate",
                                   "man_coverage"), r)) for r in cur.fetchall()}
    where = {v: k for k, v in fired_coord.items()}
    for tid in order(t for t in tm if t not in mine):
        for role in ("OC", "DC"):
            if (tid, role) in nstaff:
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
            c = pick(pool, seed)
            if c["kind"] == "PROMOTED":
                name = promote(tid, role, prev, f"to replace {prev.get('name') or 'the departed coordinator'}")
            elif c["kind"] == "REHIRED":
                name = c["name"]
                out_of_work.pop(c["coordinator_id"])
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
                cur.execute("INSERT INTO coordinator_tenures (coordinator_id, team_id, role, start_season) "
                            "VALUES (%s, %s, %s, %s)", (cid, tid, role, nxt))
                _move(cur, season, tid, role, "HIRED", name, coordinator_id=cid, source="OUTSIDE",
                      score=OUTSIDE_SCORE[role], notes=f"previously {bg}; keeps the system")
            report["coordinators"].append({"team": abbr, "role": role, "coordinator": name, "source": c["kind"]})
            log(f"  hiring: {abbr} {role} {name} ({c['kind'].lower()})")

    check_staffs(cur, nxt, set(tm) - mine)
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


def run(cur, season, log=print):
    """Everything ready for `season`, inside the caller's transaction. Returns {season, hot_seat (the coach hot seat
    report), coordinators_fired, hires (None until the cycle runs)}."""
    rep = {"season": season, "hot_seat": chs.run(cur, season, log=log)}
    rep["coordinators_fired"] = review_coordinators(cur, season, log=log)
    rep["hires"] = hiring_cycle(cur, season, log=log) if ready_to_hire(cur, season) else None
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
