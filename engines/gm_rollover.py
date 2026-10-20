"""
GM season rollover (docs/team_management/03_GM_AGENTS_PLAN.md Phase 7, §7 Q4; PROJECT_SPEC §3.3f).

Run by the Season scan's preseason engines (pipeline/weekly_capture.step_preseason_engines, optional: a failure
only warns) for the new season, or by hand:

  python3 -m engines.gm_rollover [--season NEW] [--dry-run] [--no-llm]

In order: the automatic hot seat (each GM's score from last season; up to MAX_FIRES CPU-run teams replace their
GM: the tenure closes at the season just finished and a new persona is seeded), the new season's gm_team_seasons
rows with a preseason direction (last season's wins and this season's team_ovr) and needs, fo_quality carried
forward until step 4 writes it, own picks seeded for the new third draft out, and the draft just held marked used.
rollover_actions() is pure and returns only what is still missing, so a second run changes nothing.
"""
import argparse
import json
import os
import random

EXPECTED_WINS = {"CONTEND": 10.5, "RETOOL": 8.5, "REBUILD": 6.0}
MIN_TENURE = 2                     # a GM gets at least two seasons
HOT_SEAT_LINE = 1.0
MAX_FIRES = 4                      # the NFL changes a handful of GMs a year


def hot_seat(rows, season, max_fires=MAX_FIRES):
    """Pure. rows: [{team_id, abbr, start_season, direction (last season), wins, wins_before, user_controlled}] for
    the season just finished (`season`). score = (expected wins for the direction - wins)/3, +0.5 for two
    straight seasons of 6 wins or fewer, +0.25 after 4+ seasons in the job. A GM is fired when his tenure is at
    least 2 seasons, score >= 1.0 and a seeded draw (random.Random(f"hotseat:{season}:{abbr}")) falls under
    min(0.9, 0.35 + 0.3 (score - 1)); the worst scores first, at most max_fires; JR's teams never fire their GM.
    Returns {team_id: {score, inputs, fire}}."""
    out = {}
    for r in rows:
        exp = EXPECTED_WINS.get(r.get("direction") or "RETOOL", 8.5)
        wins = r.get("wins") or 0
        tenure = season - (r.get("start_season") or season) + 1
        two_bad = wins <= 6 and r.get("wins_before") is not None and r["wins_before"] <= 6
        score = (exp - wins) / 3 + (0.5 if two_bad else 0) + (0.25 if tenure >= 4 else 0)
        p = min(0.9, 0.35 + 0.3 * (score - HOT_SEAT_LINE))
        draw = random.Random(f"hotseat:{season}:{r['abbr']}").random()
        eligible = tenure >= MIN_TENURE and score >= HOT_SEAT_LINE and not r.get("user_controlled")
        out[r["team_id"]] = {"score": round(score, 3), "fire": eligible and draw < p,
                             "inputs": {"direction": r.get("direction"), "expected_wins": exp, "wins": wins,
                                        "wins_before": r.get("wins_before"), "tenure": tenure, "p": round(p, 3),
                                        "user_controlled": bool(r.get("user_controlled"))}}
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
    cur.execute("SELECT team_id, direction IS NULL, fo_quality_at IS NULL FROM gm_team_seasons WHERE season = %s",
                (season,))
    cur_rows = cur.fetchall()
    cur.execute("SELECT team_id FROM gm_team_seasons WHERE season = %s AND fo_quality_at IS NOT NULL", (prior,))
    prior_fo = {r[0] for r in cur.fetchall()}
    cur.execute("SELECT draft_season, round, original_team_id FROM draft_picks WHERE draft_season > %s", (season,))
    existing = {tuple(r) for r in cur.fetchall()}
    cur.execute("SELECT count(*) FROM draft_picks WHERE draft_season <= %s AND NOT used", (season,))
    unused = cur.fetchone()[0]
    return {"season": season, "team_ids": team_ids, "hot_seat_decided": n == 0 or decided > 0, "hot_seat_rows": rows,
            "team_rows": {r[0] for r in cur_rows}, "direction_missing": {r[0] for r in cur_rows if r[1]},
            "fo_carry": {r[0] for r in cur_rows if r[2]} & prior_fo, "picks_existing": existing,
            "unused_past": unused}


def rollover(cur, season, llm=None, log=print):
    """Run the rollover for new `season` inside the caller's transaction. Returns a report."""
    from engines import gm_direction
    from engines.gm_personas import ensure_team_seasons, seed, seed_picks
    prior = season - 1
    acts = rollover_actions(read_state(cur, season))
    rep = {"season": season, "fired": [], "team_rows": 0, "directions": 0, "fo_carried": 0, "picks": 0, "used": 0}
    if acts["hot_seat"] is not None:
        for tid, h in acts["hot_seat"].items():
            cur.execute("UPDATE gm_team_seasons SET hot_seat_score = %s, hot_seat_inputs = %s "
                        "WHERE team_id = %s AND season = %s", (h["score"], json.dumps(h["inputs"]), tid, prior))
        for tid, h in sorted(acts["hot_seat"].items(), key=lambda kv: -kv[1]["score"]):
            if not h["fire"]:
                continue
            cur.execute("SELECT abbreviation FROM teams WHERE id = %s", (tid,))
            abbr = cur.fetchone()[0]
            seed(cur, season, llm, replace=abbr, log=log)
            cur.execute("""UPDATE gm_profiles SET end_season = %s, end_reason = %s
                           WHERE id = (SELECT id FROM gm_profiles WHERE team_id = %s AND end_season IS NOT NULL
                                       ORDER BY id DESC LIMIT 1)""",
                        (prior, f"hot seat after {prior}: score {h['score']}", tid))
            rep["fired"].append({"team": abbr, "score": h["score"]})
            log(f"  hot seat: {abbr} replaces its GM (score {h['score']})")
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
