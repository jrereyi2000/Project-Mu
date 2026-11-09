"""
GM season rollover (docs/done/team_management/03_GM_AGENTS_PLAN.md Phase 7, §7 Q4; PROJECT_SPEC §3.3f).

Run by the Season scan's preseason engines (pipeline/weekly_capture.step_preseason_engines, optional: a failure
only warns) for the new season, or by hand:

  python3 -m engines.gm_rollover [--season NEW] [--dry-run] [--no-llm]

In order: last season's still-open proposals expire (the offseason is over), the automatic hot seat (each GM's score from last season; up to MAX_FIRES CPU-run teams replace their
GM: the tenure closes at the season just finished and a new persona is seeded), the new season's gm_team_seasons
rows with a preseason direction (last season's wins and this season's team_ovr) and needs, fo_quality carried
forward until step 4 writes it, own picks seeded for the new third draft out, and the draft just held marked used.
rollover_actions() is pure and returns only what is still missing, so a second run changes nothing.

Owner patience (switchboard `owner_patience`, engines/owner_policy.patience; OWNERS_SCOUTS_VOTERS_PLAN §2.3, U5): a
hot-seat row may carry `owner` {hot_seat_line_scale, min_tenure_delta}: its line is HOT_SEAT_LINE x the scale and
its minimum tenure MIN_TENURE + the delta (at least 1); rows without it are decided as before. rollover() attaches
the terms to CPU teams with a reviewed owner when the feature is active; in shadow the hot seat is today's and the
report's owner_shadow lists the GMs the owner terms would have decided differently. MAX_FIRES and JR's safety
(his GMs are never fired) never change.
"""
import argparse
import json
import os
import random

from engines.fo_standing import hot_seat_term

EXPECTED_WINS = {"CONTEND": 10.5, "RETOOL": 8.5, "REBUILD": 6.0}
MIN_TENURE = 2                     # a GM gets at least two seasons
HOT_SEAT_LINE = 1.0
MAX_FIRES = 4                      # the NFL changes a handful of GMs a year
FO_WEIGHT = 1.5                    # front-office standing score (about +-0.3) -> about +-0.45 hot-seat points


def hot_seat(rows, season, max_fires=MAX_FIRES):
    """Pure. rows: [{team_id, abbr, start_season, direction (last season), wins, wins_before, user_controlled,
    fo_score, fo_rank}] for the season just finished (`season`). score = (expected wins for the direction - wins)/3,
    +0.5 for two straight seasons of 6 wins or fewer, +0.25 after 4+ seasons in the job, - FO_WEIGHT * fo_score
    (engines/fo_standing: the GM's own drafting and player development; None counts as 0). A GM is fired when his tenure is at
    least 2 seasons, score >= 1.0 and a seeded draw (random.Random(f"hotseat:{season}:{abbr}")) falls under
    min(0.9, 0.35 + 0.3 (score - 1)); the worst scores first, at most max_fires; JR's teams never fire their GM.
    Returns {team_id: {score, inputs, fire}}."""
    out = {}
    for r in rows:
        own = r.get("owner") or None
        line = round(HOT_SEAT_LINE * float(own.get("hot_seat_line_scale", 1.0)), 4) if own else HOT_SEAT_LINE
        min_tenure = max(1, MIN_TENURE + int(own.get("min_tenure_delta", 0))) if own else MIN_TENURE
        exp = EXPECTED_WINS.get(r.get("direction") or "RETOOL", 8.5)
        wins = r.get("wins") or 0
        tenure = season - (r.get("start_season") or season) + 1
        two_bad = wins <= 6 and r.get("wins_before") is not None and r["wins_before"] <= 6
        fo_pts = hot_seat_term(r.get("fo_score"), FO_WEIGHT)
        score = (exp - wins) / 3 + (0.5 if two_bad else 0) + (0.25 if tenure >= 4 else 0) + fo_pts
        p = min(0.9, 0.35 + 0.3 * (score - line))
        draw = random.Random(f"hotseat:{season}:{r['abbr']}").random()
        eligible = tenure >= min_tenure and score >= line and not r.get("user_controlled")
        out[r["team_id"]] = {"score": round(score, 3), "fire": eligible and draw < p,
                             "inputs": {"direction": r.get("direction"), "expected_wins": exp, "wins": wins,
                                        "wins_before": r.get("wins_before"), "tenure": tenure, "p": round(p, 3),
                                        "fo_score": r.get("fo_score"), "fo_rank": r.get("fo_rank"),
                                        "fo_points": fo_pts,
                                        "user_controlled": bool(r.get("user_controlled"))}}
        if own:
            out[r["team_id"]]["inputs"]["owner"] = dict(own, line=line, min_tenure=min_tenure)
    fired = sorted((t for t, v in out.items() if v["fire"]), key=lambda t: -out[t]["score"])
    for t in fired[max_fires:]:
        out[t]["fire"] = False
        out[t]["inputs"]["capped"] = True
    return out


def rollover_actions(state):
    """Pure. state: {season (new), team_ids, hot_seat_decided, hot_seat_rows, team_rows (team ids with a row
    this season), direction_missing (team ids), fo_carry (team ids whose fo_quality can carry), picks_existing,
    unused_past (count of unused picks of drafts already held)}. Returns what's still to do:
    {hot_seat, team_rows, directions, fo_carry, picks, mark_used}."""
    from engines.gm_personas import picks_to_seed
    season = state["season"]
    return {"hot_seat": None if state["hot_seat_decided"] else hot_seat(state["hot_seat_rows"], season - 1),
            "team_rows": sorted(set(state["team_ids"]) - set(state["team_rows"])),
            "directions": sorted(state["direction_missing"] | (set(state["team_ids"]) - set(state["team_rows"]))),
            "fo_carry": sorted(state["fo_carry"]),
            "picks": picks_to_seed(state["picks_existing"], state["team_ids"], season),
            "mark_used": state["unused_past"] > 0}


# -------------------------------------------------------------------- competitive GM hiring search ----
def gm_candidate_pool(abbr, season):
    """Pure. The personnel-director candidates for one GM opening (STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN §1): an
    ELITE architect, an established STANDARD director and a first-time PROMOTION, each at U1's market salary with a
    seeded stature. ELITE always outranks STANDARD always outranks PROMOTION on stature; the owner's budget decides
    which tier the team can afford."""
    from engines import staff_contracts_engine as sce
    rng = random.Random(f"gmsearch:{abbr}:{season}")
    pool = []
    for tier, experience, championships, base in (("ELITE", 6, 1, 0.85), ("STANDARD", 4, 0, 0.55),
                                                  ("PROMOTION", 1, 0, 0.30)):
        pool.append({"key": f"{abbr}:{tier}", "tier": tier, "experience": experience,
                     "championships": championships,
                     "salary": sce.market_salary("GM", experience=experience, championships=championships),
                     "stature": round(base + rng.uniform(-0.08, 0.08), 3)})
    return pool


def gm_search(candidates, *, appeal, budget):
    """Pure. The best candidate the owner can afford: the highest (stature + appeal) whose market salary fits the
    budget; if none fit, the cheapest candidate (a team must have a GM)."""
    affordable = [c for c in candidates if (c.get("salary") or 0) <= budget]
    if affordable:
        return max(affordable, key=lambda c: (round((c.get("stature") or 0) + appeal, 3),
                                              -(c.get("salary") or 0), c["key"]))
    if candidates:
        return min(candidates, key=lambda c: ((c.get("salary") or 0), -round(c.get("stature") or 0, 3), c["key"]))
    return None


def read_state(cur, season):
    prior = season - 1
    cur.execute("SELECT id FROM teams WHERE abbreviation IS NOT NULL")
    team_ids = [r[0] for r in cur.fetchall()]
    cur.execute("SELECT count(*), count(hot_seat_score) FROM gm_team_seasons WHERE season = %s", (prior,))
    n, decided = cur.fetchone()
    cur.execute("""SELECT g.team_id, t.abbreviation, g.start_season, s.direction, ts.wins, tb.wins,
                          COALESCE(st.user_controlled, false)
                   FROM gm_profiles g JOIN teams t ON t.id = g.team_id
                   LEFT JOIN gm_team_seasons s ON s.team_id = g.team_id AND s.season = %s
                   LEFT JOIN team_seasons ts ON ts.team_id = g.team_id AND ts.season = %s
                   LEFT JOIN team_seasons tb ON tb.team_id = g.team_id AND tb.season = %s
                   LEFT JOIN gm_team_settings st ON st.team_id = g.team_id
                   WHERE g.end_season IS NULL AND g.start_season <= %s""", (prior, prior, prior - 1, prior))
    rows = [{"team_id": r[0], "abbr": r[1], "start_season": r[2], "direction": r[3], "wins": r[4],
             "wins_before": r[5], "user_controlled": r[6]} for r in cur.fetchall()]
    if n and not decided:
        for r, f in zip(rows, front_office_scores(cur, season, [r["team_id"] for r in rows])):
            r.update(f)
    cur.execute("SELECT team_id, direction IS NULL, fo_quality_at IS NULL FROM gm_team_seasons WHERE season = %s",
                (season,))
    cur_rows = cur.fetchall()
    cur.execute("SELECT team_id FROM gm_team_seasons WHERE season = %s AND fo_quality_at IS NOT NULL", (prior,))
    prior_fo = {r[0] for r in cur.fetchall()}
    # Only a GM who was already in the job before the prior season inherits his predecessor's q (a GM fired in the
    # post-Super Bowl hot seat, P1-27, must start at the baseline).
    cur.execute("SELECT team_id FROM gm_profiles WHERE end_season IS NULL AND start_season <= %s", (prior,))
    carry_ok = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT draft_season, round, original_team_id FROM draft_picks WHERE draft_season > %s", (season,))
    existing = {tuple(r) for r in cur.fetchall()}
    cur.execute("SELECT count(*) FROM draft_picks WHERE draft_season <= %s AND NOT used", (season,))
    unused = cur.fetchone()[0]
    return {"season": season, "team_ids": team_ids, "hot_seat_decided": n == 0 or decided > 0, "hot_seat_rows": rows,
            "team_rows": {r[0] for r in cur_rows}, "direction_missing": {r[0] for r in cur_rows if r[1]},
            "fo_carry": {r[0] for r in cur_rows if r[2]} & prior_fo & carry_ok, "picks_existing": existing,
            "unused_past": unused}


def front_office_scores(cur, season, team_ids):
    """[{fo_score, fo_rank}] per team id, computed now for the GMs about to be judged (engines/fo_standing.live,
    with this season's roster cards loaded). A failure leaves them unscored (the term counts 0)."""
    try:
        cur.execute("SAVEPOINT fo_standing")
        from engines import fo_standing
        by = {r["team_id"]: r for r in fo_standing.live(cur, season)}
        cur.execute("RELEASE SAVEPOINT fo_standing")
    except Exception as e:
        cur.execute("ROLLBACK TO SAVEPOINT fo_standing")
        print(f"  [WARN] front-office standings for the hot seat skipped: {e!r}")
        by = {}
    return [{"fo_score": by[t]["score"], "fo_rank": by[t]["rank"]} if t in by else {} for t in team_ids]


def _gm_budget(cur, season, team_id, abbr):
    """The owner's cash left for a GM: the annual cash pool (owner_cash_ledgers, else the baseline wealth band)
    minus the non-GM staff payroll already committed in staff_contracts."""
    from engines import report_card_engine, staff_contracts_engine as sce
    pool = None
    try:
        cur.execute("SAVEPOINT gm_budget")
        cur.execute("""SELECT total_cash_budget FROM owner_cash_ledgers
                       WHERE team_id = %s AND season <= %s ORDER BY season DESC LIMIT 1""", (team_id, season))
        row = cur.fetchone()
        cur.execute("RELEASE SAVEPOINT gm_budget")
        if row and row[0] is not None:
            pool = int(row[0])
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT gm_budget")
    if pool is None:
        try:
            card = report_card_engine.load_baseline().get(abbr)
            if card and card.get("wealth_band"):
                pool = report_card_engine.base_budget(card["wealth_band"])
        except Exception:
            pool = 0
    payroll = 0
    try:
        cur.execute("SAVEPOINT gm_payroll")
        cur.execute("""SELECT person_kind, annual_salary FROM staff_contracts
                       WHERE team_id = %s AND remaining_years > 0 AND person_kind <> 'GM'""", (team_id,))
        payroll = sce.staff_payroll([{"person_kind": k, "annual_salary": s} for k, s in cur.fetchall()])
        cur.execute("RELEASE SAVEPOINT gm_payroll")
    except Exception:
        cur.execute("ROLLBACK TO SAVEPOINT gm_payroll")
    return max(0, int(pool) - int(payroll))


def _hire_gm(cur, season, prior, abbr, team_id, llm, *, why, log=print):
    """The competitive GM hiring search (plan S3): close the old tenure, score the personnel-director pool by the
    team's enhanced opening appeal and the owner's staff budget, and seed the winner's persona. Returns the search
    report."""
    from engines import coaching_carousel as cc
    from engines import gm_personas
    appeal = 0.5
    try:
        parts = cc.opening_parts(cur, prior, [team_id])
        appeal = cc.opening_appeal(parts.get(team_id, {}))
    except Exception:
        pass
    budget = _gm_budget(cur, season, team_id, abbr)
    winner = gm_search(gm_candidate_pool(abbr, season), appeal=appeal, budget=budget) or \
        {"tier": "STANDARD", "salary": 0, "stature": 0.5}
    teams = gm_personas.read_teams(cur, season)
    open_ = gm_personas.read_open(cur)
    others = {a: t for a, t in open_.items() if a != abbr}
    traits = gm_personas.replacement_traits(abbr, season, others)
    taken = [g["gm_name"] for g in open_.values() if g.get("gm_name")]
    cur.execute("UPDATE gm_profiles SET end_season = %s, end_reason = %s "
                "WHERE team_id = %s AND end_season IS NULL", (prior, why, team_id))
    p = gm_personas.make_persona(abbr, teams[abbr], traits, season, llm, taken,
                                 seed_key=f"gm:{abbr}:{season}", start=season)
    p = dict(p, first_draft_season=season + 1)      # hired after this season's draft (Season scan / hot seat)
    gid = gm_personas.insert_gm(cur, teams[abbr]["id"], p, traits, f"gm:{abbr}:{season}")
    try:
        cur.execute("""INSERT INTO staff_contracts (team_id, person_id, person_kind, annual_salary, total_years,
                                                    remaining_years, signed_season, full_control, buyout_owed)
                       VALUES (%s, %s, 'GM', %s, 4, 4, %s, false, 0)""",
                    (team_id, gid, winner["salary"], season))
    except Exception:
        pass
    log(f"  gm search: {abbr} hires a {winner['tier']} GM (appeal {appeal:.3f}, budget ${budget:,}, "
        f"${winner['salary']:,})")
    return {"gm": p["gm_name"], "tier": winner["tier"], "salary": winner["salary"],
            "appeal": round(appeal, 3), "budget": budget}


def _owner_state(cur, season, state, rep):
    """The `owner_patience` overlay on the hot-seat rows (active: the owner's terms; shadow: record the comparison;
    off: unchanged)."""
    from engines import owner_context as oc
    mode = oc.feature_mode(cur, "owner_patience")
    if mode == "off":
        return state
    terms = oc.patience_terms(cur)
    owned = [dict(r, owner=terms[r["team_id"]]) if r["team_id"] in terms and not r.get("user_controlled")
             else r for r in state["hot_seat_rows"]]
    rep["owner_mode"] = mode
    if mode == "active":
        return dict(state, hot_seat_rows=owned)
    if not state["hot_seat_decided"]:
        base, alt = hot_seat(state["hot_seat_rows"], season - 1), hot_seat(owned, season - 1)
        rep["owner_shadow"] = [{"team_id": t, "fired": bool(base.get(t, {}).get("fire")),
                                "owner_fired": bool(alt[t]["fire"]), "owner": alt[t]["inputs"].get("owner")}
                               for t in sorted(alt) if bool(base.get(t, {}).get("fire")) != bool(alt[t]["fire"])]
    return state


def _fire_hot_seat(cur, season, decisions, rep, llm, log):
    """Writes the hot seat's scores for season-1 and fires the GMs it names. Returns the fired team ids."""
    prior = season - 1
    if decisions is None:
        return []
    for tid, h in decisions.items():
        cur.execute("UPDATE gm_team_seasons SET hot_seat_score = %s, hot_seat_inputs = %s "
                    "WHERE team_id = %s AND season = %s", (h["score"], json.dumps(h["inputs"]), tid, prior))
    fired_tids = []
    for tid, h in sorted(decisions.items(), key=lambda kv: -kv[1]["score"]):
        if not h["fire"]:
            continue
        cur.execute("""SELECT t.abbreviation, g.coach_id IS NOT NULL FROM teams t
                       LEFT JOIN gm_profiles g ON g.team_id = t.id AND g.end_season IS NULL WHERE t.id = %s""",
                    (tid,))
        abbr, coach_gm = cur.fetchone()
        # A head coach who was also GM loses the GM job only: Madden decides who coaches.
        why = f"hot seat after {prior}: score {h['score']}" + ("; stays head coach" if coach_gm else "")
        hire = _hire_gm(cur, season, prior, abbr, tid, llm, why=why, log=log)
        rep["fired"].append({"team": abbr, "score": h["score"], **hire})
        fired_tids.append(tid)
        log(f"  hot seat: {abbr} replaces its GM (score {h['score']})")
    return fired_tids


def super_bowl_complete(cur, season):
    """True once `season`'s Super Bowl is a completed game (the point the carousel hires and the GM hot seat runs)."""
    cur.execute("SELECT count(*) FROM games WHERE season = %s AND game_type = 'SUPER_BOWL' "
                "AND status = 'COMPLETED'", (season,))
    return bool(cur.fetchone()[0])


def hot_seat_only(cur, season, llm=None, log=print):
    """The GM hot seat for the season just finished, run BEFORE the coaching carousel (P1-27) so a team that fires
    both its head coach and its GM is dual-vacant when the carousel hires (the §2.3 Godfather trigger reads it).
    `season` is the season starting. Skipped until the Super Bowl is complete and once the hot seat is decided; the
    next preseason's `rollover(season)` sees the scores written and does not decide it again.
    Returns {season, fired, skipped}."""
    rep = {"season": season, "fired": [], "skipped": None}
    if not super_bowl_complete(cur, season - 1):
        rep["skipped"] = "the Super Bowl isn't complete"
        return rep
    state = read_state(cur, season)
    if state["hot_seat_decided"]:
        rep["skipped"] = "already decided"
        return rep
    state = _owner_state(cur, season, state, rep)
    fired = _fire_hot_seat(cur, season, rollover_actions(state)["hot_seat"], rep, llm, log)
    rep["fired_team_ids"] = fired
    return rep


def rollover(cur, season, llm=None, log=print):
    """Run the rollover for new `season` inside the caller's transaction. Returns a report."""
    from engines import gm_direction
    from engines.gm_personas import ensure_team_seasons, seed_picks
    prior = season - 1
    state = read_state(cur, season)
    rep = {"season": season, "fired": [], "team_rows": 0, "directions": 0, "fo_carried": 0, "picks": 0, "used": 0}
    state = _owner_state(cur, season, state, rep)
    acts = rollover_actions(state)
    cur.execute("""UPDATE gm_trade_proposals SET status = 'EXPIRED', status_reason = 'the offseason is over'
                   WHERE season = %s AND status IN ('PROPOSED', 'APPROVED')""", (prior,))
    rep["expired"] = cur.rowcount
    for tid in _fire_hot_seat(cur, season, acts["hot_seat"], rep, llm, log):
        if tid in acts["fo_carry"]:
            acts["fo_carry"].remove(tid)          # the new GM starts at the baseline, not his predecessor's q
    rep["team_rows"] = ensure_team_seasons(cur, season) if acts["team_rows"] else 0
    if acts["directions"]:
        league = gm_direction.read_league(cur, season, 1)
        if league["teams"]:
            rows = gm_direction.compute(league, 1, force_direction=True)
            rows = {t: r for t, r in rows.items() if t in set(acts["directions"])}
            gm_direction.write(cur, season, 1, rows)
            rep["directions"] = len(rows)
        else:
            log(f"  no {season} team_seasons rows yet: directions wait for the first engine run")
    for tid in acts["fo_carry"]:
        cur.execute("""UPDATE gm_team_seasons n SET fo_quality = p.fo_quality, fo_quality_inputs = p.fo_quality_inputs,
                              fo_quality_at = p.fo_quality_at
                       FROM gm_team_seasons p WHERE p.team_id = n.team_id AND p.season = %s
                         AND n.team_id = %s AND n.season = %s""", (prior, tid, season))
        rep["fo_carried"] += cur.rowcount
    if acts["picks"]:
        cur.execute("SELECT id FROM teams WHERE abbreviation IS NOT NULL")
        rep["picks"] = seed_picks(cur, [r[0] for r in cur.fetchall()], season)
    if acts["mark_used"]:
        cur.execute("UPDATE draft_picks SET used = true WHERE draft_season <= %s AND NOT used", (season,))
        rep["used"] = cur.rowcount
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, help="the new season (default league_state.current_season)")
    ap.add_argument("--dry-run", action="store_true", help="print the report and roll back")
    ap.add_argument("--no-llm", action="store_true")
    a = ap.parse_args(argv)
    import psycopg
    from engines.gm_llm import flush, make_llm
    llm = None if (a.dry_run or a.no_llm) else make_llm("gm_persona")
    dsn = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        cur.execute("SELECT current_season FROM league_state WHERE id = 1")
        season = a.season or cur.fetchone()[0]
        rep = rollover(cur, season, llm)
        print(json.dumps(rep, indent=1))
        if a.dry_run:
            conn.rollback()
            print("dry run: rolled back")
        else:
            conn.commit()
    flush(llm)


if __name__ == "__main__":
    main()
