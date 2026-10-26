"""
Season-end archive and head-coach records (docs/SEASON_CONTINUITY_PLAN.md phase 1; PROJECT_SPEC §3.3h).

Once a season's Super Bowl is COMPLETED this freezes the season: league_seasons (champion, runner-up), one
team_season_results row per team, one coach_season_lines row per head coach, and the coach's career snapshot in
coach_seasons (the previous snapshot + this season, engines/coach_profile.accumulate; JR 2026-10-26: coach records
start in 2035, the first season with every game on record). Run by the weekly run's Step 7d (weekly, playoff and
postgame runs; it acts only on a season whose Super Bowl is complete and isn't archived yet) or by hand:

  python3 -m engines.season_archive [--season S] [--replace] [--dry-run]

Everything is computed from `games`, never read off a Madden screen (plan D5):
- Standings: wins, losses, ties, points for and against, division / conference / common-game records.
- Division places use the NFL tiebreakers this league can compute: head-to-head, division record, common games,
  conference record, strength of victory, strength of schedule, net points. When a step leaves more than one team
  but fewer than the group, the rest restart from step 1 (the NFL's rule); a tie nothing breaks is UNBROKEN and the
  teams are listed by abbreviation.
- Conference seeds come from the bracket (exact): the Wild Card home teams are seeds 2-4 and their opponents 9 - that
  seed (2v7, 3v6, 4v5); the #1 seed is the conference's Divisional-round team with no Wild Card game. Only the order of
  the three home teams needs the conference tiebreakers.
compute() is pure; archive() reads and writes. A second run of an archived season changes nothing unless --replace.
"""
import argparse
import json
import os

import psycopg

from engines.coach_profile import _mu_era_only, accumulate, fetch_games, fetch_snapshot, fetch_team_coach, fetch_tenures

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
REGULAR = "REGULAR_SEASON"
ROUNDS = ("WILD_CARD", "DIVISIONAL", "CONFERENCE", "SUPER_BOWL")
LOST_IN = {"WILD_CARD": "WILD_CARD", "DIVISIONAL": "DIVISIONAL", "CONFERENCE": "CONFERENCE",
           "SUPER_BOWL": "SUPER_BOWL_LOSS"}
FULL_SEASON_GAMES = 17
MIN_COMMON_GAMES = 4          # the NFL's minimum for the common-games step in a conference tiebreak
UNBROKEN = "UNBROKEN"


# ----------------------------------------------------------------------------------------------- pure ----

def _result(g, team):
    mine, theirs = ((g["home_score"], g["away_score"]) if g["home_team_id"] == team
                    else (g["away_score"], g["home_score"]))
    return "W" if mine > theirs else "L" if mine < theirs else "T"


def _pct(w, l, t):
    n = w + l + t
    return (w + 0.5 * t) / n if n else 0.0


def standings(games, teams):
    """{team_id: {w, l, t, pf, pa, games: [(opponent, 'W'|'L'|'T', points for, points against)]}} over the
    completed regular-season games. teams: {team_id: {abbr, conference, division}}."""
    rec = {tid: {"w": 0, "l": 0, "t": 0, "pf": 0, "pa": 0, "games": []} for tid in teams}
    for g in games:
        if (g.get("game_type") or REGULAR) != REGULAR:
            continue
        for team, opp, pf, pa in ((g["home_team_id"], g["away_team_id"], g["home_score"], g["away_score"]),
                                  (g["away_team_id"], g["home_team_id"], g["away_score"], g["home_score"])):
            if team not in rec:
                continue
            r = _result(g, team)
            rec[team]["wlt"[("W", "L", "T").index(r)]] += 1
            rec[team]["pf"] += pf
            rec[team]["pa"] += pa
            rec[team]["games"].append((opp, r, pf, pa))
    return rec


def _record_vs(rec, team, opponents):
    """(w, l, t) of team against the given opponent set."""
    out = [0, 0, 0]
    for opp, r, _, _ in rec[team]["games"]:
        if opp in opponents:
            out["WLT".index(r)] += 1
    return out


def _team_pct(rec, team):
    r = rec[team]
    return _pct(r["w"], r["l"], r["t"])


def _steps(rec, teams, scope):
    """The tiebreak steps for a division ('division') or conference ('conference') tie, each a (name, value(group,
    team)) with higher = better; None means the step doesn't apply to this group."""
    def h2h(group, team):
        others = set(group) - {team}
        if scope == "conference":
            # A conference head-to-head counts only when every tied team played every other (else it's skipped).
            for a in group:
                for b in group:
                    if a != b and not any(o == b for o, *_ in rec[a]["games"]):
                        return None
        return _pct(*_record_vs(rec, team, others))

    def division(group, team):
        mine = teams[team]["division"]
        return _pct(*_record_vs(rec, team, {t for t in teams if t != team and teams[t]["division"] == mine}))

    def common(group, team):
        sets = [{o for o, *_ in rec[t]["games"]} for t in group]
        shared = set.intersection(*sets) - set(group)
        w, l, t = _record_vs(rec, team, shared)
        if scope == "conference" and w + l + t < MIN_COMMON_GAMES:
            return None
        return _pct(w, l, t) if shared else None

    def conference(group, team):
        mine = teams[team]["conference"]
        return _pct(*_record_vs(rec, team, {t for t in teams if t != team and teams[t]["conference"] == mine}))

    def sov(group, team):
        beaten = [opp for opp, r, *_ in rec[team]["games"] if r == "W"]
        return _combined(rec, beaten)

    def sos(group, team):
        return _combined(rec, [opp for opp, *_ in rec[team]["games"]])

    def net_points(group, team):
        return rec[team]["pf"] - rec[team]["pa"]

    steps = [("head-to-head", h2h)]
    if scope == "division":
        steps.append(("division record", division))
    steps += [("common games", common), ("conference record", conference), ("strength of victory", sov),
              ("strength of schedule", sos), ("net points", net_points)]
    return steps


def _combined(rec, opponents):
    """Combined win % of the listed opponents (each listed game counts once)."""
    w = l = t = 0
    for o in opponents:
        if o in rec:
            w, l, t = w + rec[o]["w"], l + rec[o]["l"], t + rec[o]["t"]
    return _pct(w, l, t)


def break_tie(rec, teams, group, scope):
    """Order a group of teams with the same record: [(team_id, reason)], best first. Each pass finds the single
    best team: a step that leaves one leader ranks it (reason = that step); a step that narrows the group restarts
    from step 1 with the leaders; when no step separates them the group is UNBROKEN (listed by abbreviation)."""
    out, rest = [], list(group)
    steps = _steps(rec, teams, scope)
    while rest:
        if len(rest) == 1:
            out.append((rest[0], None))               # last of the group: no tiebreaker put it above anyone
            break
        pool, reason = list(rest), None
        changed = True
        while changed and len(pool) > 1:
            changed = False
            for name, fn in steps:
                vals = {t: fn(pool, t) for t in pool}
                if any(v is None for v in vals.values()):
                    continue
                best = max(vals.values())
                leaders = [t for t in pool if vals[t] == best]
                if len(leaders) < len(pool):
                    pool, reason = leaders, name
                    changed = True
                    break
        if len(pool) == 1:
            winner = pool[0]
        else:
            winner, reason = sorted(pool, key=lambda t: teams[t]["abbr"])[0], UNBROKEN
        out.append((winner, reason))
        rest.remove(winner)
    return out


def order_by_record(rec, teams, team_ids, scope):
    """[(team_id, tiebreak reason or None)] best first: win % first, ties broken by break_tie()."""
    by_pct = {}
    for t in team_ids:
        by_pct.setdefault(round(_team_pct(rec, t), 6), []).append(t)
    out = []
    for pct in sorted(by_pct, reverse=True):
        group = by_pct[pct]
        out += [(group[0], None)] if len(group) == 1 else break_tie(rec, teams, group, scope)
    return out


def division_places(rec, teams):
    """{team_id: (place 1-4, tiebreak reason or None)}."""
    out = {}
    divisions = {}
    for t, info in teams.items():
        divisions.setdefault(info["division"], []).append(t)
    for members in divisions.values():
        for place, (t, reason) in enumerate(order_by_record(rec, teams, members, "division"), 1):
            out[t] = (place, reason)
    return out


def seeds(rec, teams, playoff_games):
    """{team_id: seed 1-7} read off the bracket, and a list of problems (a conference whose bracket isn't the
    7-team shape gets no seeds)."""
    out, problems = {}, []
    for conf in sorted({i["conference"] for i in teams.values()}):
        wc = [g for g in playoff_games if g["game_type"] == "WILD_CARD"
              and teams.get(g["home_team_id"], {}).get("conference") == conf]
        div = [g for g in playoff_games if g["game_type"] == "DIVISIONAL"
               and teams.get(g["home_team_id"], {}).get("conference") == conf]
        in_wc = {g["home_team_id"] for g in wc} | {g["away_team_id"] for g in wc}
        byes = {t for g in div for t in (g["home_team_id"], g["away_team_id"])} - in_wc
        if len(wc) != 3 or len(byes) != 1:
            problems.append(f"{conf}: {len(wc)} Wild Card games and {len(byes)} bye team(s); no seeds")
            continue
        out[byes.pop()] = 1
        homes = order_by_record(rec, teams, [g["home_team_id"] for g in wc], "conference")
        for seed, (t, _) in enumerate(homes, 2):
            out[t] = seed
            opp = next(g["away_team_id"] for g in wc if g["home_team_id"] == t)
            out[opp] = 9 - seed
    return out, problems


def playoff_line(team, playoff_games):
    """(result, wins, losses) for a team's postseason."""
    mine = sorted((g for g in playoff_games if team in (g["home_team_id"], g["away_team_id"])),
                  key=lambda g: ROUNDS.index(g["game_type"]))
    if not mine:
        return "MISSED", 0, 0
    wins = sum(1 for g in mine if _result(g, team) == "W")
    last = mine[-1]
    if _result(last, team) == "W":
        return ("CHAMPION" if last["game_type"] == "SUPER_BOWL" else LOST_IN[last["game_type"]]), wins, len(mine) - wins
    return LOST_IN[last["game_type"]], wins, len(mine) - wins


def compute(games, teams):
    """Pure. games: the season's completed games; teams: {team_id: {abbr, conference, division}}. Returns
    {league: {...}, teams: {team_id: row}, problems} or raises ValueError when the Super Bowl isn't complete."""
    sb = [g for g in games if g.get("game_type") == "SUPER_BOWL"]
    if len(sb) != 1:
        raise ValueError("the Super Bowl isn't complete")
    sb = sb[0]
    playoff = [g for g in games if g.get("game_type") in ROUNDS]
    rec = standings(games, teams)
    places = division_places(rec, teams)
    seed_map, problems = seeds(rec, teams, playoff)
    champ = sb["home_team_id"] if _result(sb, sb["home_team_id"]) == "W" else sb["away_team_id"]
    rows = {}
    for t in teams:
        result, pw, pl = playoff_line(t, playoff)
        r = rec[t]
        rows[t] = {"wins": r["w"], "losses": r["l"], "ties": r["t"], "points_for": r["pf"], "points_against": r["pa"],
                   "division_place": places[t][0], "division_tiebreak": places[t][1],
                   "conference_seed": seed_map.get(t), "playoff_result": result,
                   "playoff_wins": pw, "playoff_losses": pl}
    short = [teams[t]["abbr"] for t in teams if rec[t]["w"] + rec[t]["l"] + rec[t]["t"] < FULL_SEASON_GAMES]
    if short:
        problems.append(f"fewer than {FULL_SEASON_GAMES} regular-season games on record: {', '.join(sorted(short))}")
    return {"league": {"champion_team_id": champ,
                       "runner_up_team_id": sb["away_team_id"] if champ == sb["home_team_id"] else sb["home_team_id"],
                       "super_bowl_game_id": sb.get("id"),
                       "regular_season_games": sum(1 for g in games if (g.get("game_type") or REGULAR) == REGULAR),
                       "postseason_games": len(playoff)},
            "teams": rows, "problems": problems}


# ------------------------------------------------------------------------------------------- database ----

GAME_COLS = ("id", "season", "week", "game_type", "home_team_id", "away_team_id", "home_score", "away_score")


def installed(cur):
    cur.execute("SELECT to_regclass('league_seasons') IS NOT NULL AND to_regclass('coach_season_lines') IS NOT NULL")
    return bool(cur.fetchone()[0])


def _load(cur, season):
    cur.execute("SELECT id, abbreviation, conference, division FROM teams")
    teams = {r[0]: {"abbr": r[1], "conference": r[2], "division": r[3]} for r in cur.fetchall()}
    cur.execute(f"""SELECT {', '.join(GAME_COLS)} FROM games
                    WHERE season = %s AND status = 'COMPLETED' AND home_score IS NOT NULL AND away_score IS NOT NULL""",
                (season,))
    return teams, [dict(zip(GAME_COLS, r)) for r in cur.fetchall()]


def _gm_id(cur, team_id, season):
    cur.execute("SELECT to_regclass('gm_profiles') IS NOT NULL")
    if not cur.fetchone()[0]:
        return None
    cur.execute("""SELECT id FROM gm_profiles WHERE team_id = %s AND start_season <= %s
                     AND (end_season IS NULL OR end_season >= %s) ORDER BY start_season DESC LIMIT 1""",
                (team_id, season, season))
    r = cur.fetchone()
    return r[0] if r else None


def coach_snapshot(cur, coach_id, season):
    """The coach's career snapshot through `season`: his latest earlier snapshot + every season since (accumulate).
    Returns (row, mu_era_only, problem). A coach with no earlier snapshot starts from zero (plan D6, mu_era_only);
    a snapshot with an unrecorded season in between (a team with fewer than 17 regular-season games) isn't written."""
    snap = fetch_snapshot(cur, coach_id, season)
    mu_only = False
    if snap is None:
        snap = {"season": season - 1, "career_wins": 0, "career_losses": 0, "career_ties": 0, "winning_seasons": 0,
                "playoff_wins": 0, "playoff_losses": 0, "super_bowl_wins": 0, "super_bowl_losses": 0}
        mu_only = True
    else:
        mu_only = _mu_era_only(cur, coach_id, snap["season"])
    tenures = fetch_tenures(cur, coach_id)
    team_ids = {t["team_id"] for t in tenures}
    games = fetch_games(cur, team_ids, snap["season"] + 1, season, lambda s, w: True)
    for s in range(snap["season"] + 1, season):
        held = [t for t in tenures if t["start_season"] <= s and (t["end_season"] is None or s <= t["end_season"])]
        if held and sum(1 for g in games if g["season"] == s and (g["game_type"] or REGULAR) == REGULAR
                        and held[0]["team_id"] in (g["home_team_id"], g["away_team_id"])) < FULL_SEASON_GAMES:
            return None, mu_only, f"season {s} isn't fully on record since his {snap['season']} snapshot"
    return accumulate(snap, tenures, games, season), mu_only, None


def archive(cur, season, replace=False, dry_run=False):
    """Archive one season. Returns a report {season, status, champion, problems, coaches, snapshots_skipped}.
    status: not_installed | not_ready | exists | archived | dry_run."""
    if not installed(cur):
        return {"season": season, "status": "not_installed"}
    cur.execute("SELECT 1 FROM league_seasons WHERE season = %s", (season,))
    if cur.fetchone() and not replace:
        return {"season": season, "status": "exists"}
    teams, games = _load(cur, season)
    try:
        result = compute(games, teams)
    except ValueError as e:
        return {"season": season, "status": "not_ready", "reason": str(e)}
    report = {"season": season, "champion": teams[result["league"]["champion_team_id"]]["abbr"],
              "problems": list(result["problems"]), "coaches": 0, "snapshots_skipped": []}
    coach_rows, snapshot_rows = [], []
    for tid, row in result["teams"].items():
        coach = fetch_team_coach(cur, tid, season)
        row["head_coach_id"] = coach["id"] if coach else None
        row["gm_id"] = _gm_id(cur, tid, season)
        if not coach:
            report["problems"].append(f"{teams[tid]['abbr']}: no head coach on record for {season}")
            continue
        coach_rows.append((coach["id"], tid, season, row["wins"], row["losses"], row["ties"],
                           row["wins"] + row["losses"] + row["ties"], row["playoff_wins"], row["playoff_losses"],
                           row["playoff_result"]))
        snap, mu_only, problem = coach_snapshot(cur, coach["id"], season)
        if problem:
            report["snapshots_skipped"].append(f"{coach['coach_name']}: {problem}")
        else:
            snapshot_rows.append((coach["id"], tid, season, snap, mu_only))
    report["coaches"] = len(coach_rows)
    if dry_run:
        report["status"] = "dry_run"
        report["teams"] = {teams[t]["abbr"]: r for t, r in result["teams"].items()}
        return report

    lg = result["league"]
    cur.execute("""INSERT INTO league_seasons (season, champion_team_id, runner_up_team_id, super_bowl_game_id,
                                               regular_season_games, postseason_games, notes, finalized_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                   ON CONFLICT (season) DO UPDATE SET champion_team_id = EXCLUDED.champion_team_id,
                       runner_up_team_id = EXCLUDED.runner_up_team_id, super_bowl_game_id = EXCLUDED.super_bowl_game_id,
                       regular_season_games = EXCLUDED.regular_season_games,
                       postseason_games = EXCLUDED.postseason_games, notes = EXCLUDED.notes, finalized_at = now()""",
                (season, lg["champion_team_id"], lg["runner_up_team_id"], lg["super_bowl_game_id"],
                 lg["regular_season_games"], lg["postseason_games"], "; ".join(report["problems"]) or None))
    cur.execute("DELETE FROM team_season_results WHERE season = %s", (season,))
    for tid, r in result["teams"].items():
        cur.execute("""INSERT INTO team_season_results (season, team_id, wins, losses, ties, points_for, points_against,
                           division_place, division_tiebreak, conference_seed, playoff_result, playoff_wins,
                           playoff_losses, head_coach_id, gm_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (season, tid, r["wins"], r["losses"], r["ties"], r["points_for"], r["points_against"],
                     r["division_place"], r["division_tiebreak"], r["conference_seed"], r["playoff_result"],
                     r["playoff_wins"], r["playoff_losses"], r["head_coach_id"], r["gm_id"]))
    cur.execute("DELETE FROM coach_season_lines WHERE season = %s", (season,))
    cur.executemany("""INSERT INTO coach_season_lines (coach_id, team_id, season, wins, losses, ties, games,
                           playoff_wins, playoff_losses, playoff_result)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", coach_rows)
    for coach_id, tid, s, snap, mu_only in snapshot_rows:
        cur.execute("""INSERT INTO coach_seasons (coach_id, team_id, season, career_wins, career_losses, career_ties,
                           winning_seasons, playoff_wins, playoff_losses, super_bowl_wins, super_bowl_losses,
                           mu_era_only)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (coach_id, season) DO UPDATE SET team_id = EXCLUDED.team_id,
                           career_wins = EXCLUDED.career_wins, career_losses = EXCLUDED.career_losses,
                           career_ties = EXCLUDED.career_ties, winning_seasons = EXCLUDED.winning_seasons,
                           playoff_wins = EXCLUDED.playoff_wins, playoff_losses = EXCLUDED.playoff_losses,
                           super_bowl_wins = EXCLUDED.super_bowl_wins, super_bowl_losses = EXCLUDED.super_bowl_losses,
                           mu_era_only = EXCLUDED.mu_era_only""",
                    (coach_id, tid, s, snap["career_wins"], snap["career_losses"], snap["career_ties"],
                     snap["winning_seasons"], snap["playoff_wins"], snap["playoff_losses"], snap["super_bowl_wins"],
                     snap["super_bowl_losses"], mu_only))
    report["status"] = "archived"
    return report


def run_ready(cur, season):
    """The weekly run's step: archive `season - 1` and `season` when their Super Bowl is complete and they aren't
    archived yet (the league may already have rolled over). Returns the reports."""
    return [archive(cur, s) for s in (season - 1, season)]


def main():
    ap = argparse.ArgumentParser(description="Archive a finished season (standings, seeds, playoff results, coaches).")
    ap.add_argument("--season", type=int, help="season to archive (default: the latest with a completed Super Bowl)")
    ap.add_argument("--replace", action="store_true", help="rewrite a season that's already archived")
    ap.add_argument("--dry-run", action="store_true", help="compute and print; write nothing")
    args = ap.parse_args()
    with psycopg.connect(CONN_INFO) as conn:
        cur = conn.cursor()
        season = args.season
        if season is None:
            cur.execute("SELECT max(season) FROM games WHERE game_type = 'SUPER_BOWL' AND status = 'COMPLETED'")
            season = cur.fetchone()[0]
        report = archive(cur, season, replace=args.replace, dry_run=args.dry_run)
        if not args.dry_run:
            conn.commit()
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
