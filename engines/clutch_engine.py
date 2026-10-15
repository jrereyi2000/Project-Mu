"""Clutch profile (PROJECT_SPEC §2.15): late-game records for teams and quarterbacks, read from the game scripts.

Counts fourth-quarter comebacks, game-winning drives, blown leads and records by game state from each game's
stored script (engines/game_script_engine.py, §2.14), the league's official account of how its games unfolded.
In captured games the quarter scores behind these counts are real; elsewhere the order of scoring is the
script's, so the counts are canon (every writer shares them, and no recorded data contradicts them) rather than
measured. Computed live (nothing stored); a quarterback is credited with the games he started (the team's most
pass attempts in that game).

Definitions (Pro Football Reference style):
- Fourth-quarter comeback: a win in which the team trailed by one score (1-8 points) at some point in the fourth
  quarter or overtime.
- Game-winning drive: a win whose lead-for-good score was the team's, in the fourth quarter or overtime.
- Blown double-digit lead: a loss after leading by 10 or more. Blown fourth-quarter lead: a loss after leading at
  some point in the fourth quarter or overtime.
"""
from collections import Counter

PERIODS = ("Q1", "Q2", "Q3", "Q4", "OT")
PERIOD_WORDS = ("first quarter", "second quarter", "third quarter", "fourth quarter", "overtime")


def _record(results) -> str:
    c = Counter(results)
    return f"{c['W']}-{c['L']}" + (f"-{c['T']}" if c["T"] else "")


def team_situations(script: dict) -> dict:
    """{abbr: situation} for both teams of one game script."""
    abbrs = [script["teams"]["away"], script["teams"]["home"]]
    qs = script["quarter_scores"]
    final = {a: sum(qs[a]) for a in abbrs}
    out = {}
    for team in abbrs:
        opp = abbrs[1] if team == abbrs[0] else abbrs[0]
        margin = final[team] - final[opp]
        result = "W" if margin > 0 else "L" if margin < 0 else "T"
        thru = lambda n: sum(qs[team][:n]) - sum(qs[opp][:n])
        # margins at every point of the fourth quarter and overtime: its start, then after each score in it
        late = [thru(3)]
        decisive = None
        for s in script["scoring"]:
            p = PERIODS.index(s["period"])
            after = s.get("score_after")
            if p >= 3 and after:
                late.append(after[team] - after[opp])
            if "lead for good" in s["tags"]:
                decisive = s
        late_deficit = max((-m for m in late if -8 <= m <= -1), default=0)
        out[team] = {
            "opponent": opp,
            "home": team == abbrs[1],
            "result": result,
            "final": f"{final[team]}-{final[opp]}",
            "halftime_margin": thru(2),
            "margin_after_three": thru(3),
            "overtime": bool(script.get("overtime")),
            "fourth_quarter_comeback": result == "W" and late_deficit > 0,
            "late_deficit": max([-m for m in late if m < 0], default=0),   # biggest fourth-quarter/OT deficit
            "game_winning_drive": (result == "W" and decisive is not None and decisive["team"] == team
                                   and PERIODS.index(decisive["period"]) >= 3),
            "decisive": decisive,
            "largest_lead": script["largest_lead"].get(team, 0),
            "largest_deficit": script["largest_lead"].get(opp, 0),
            "blew_double_digit_lead": result == "L" and script["largest_lead"].get(team, 0) >= 10,
            "blew_fourth_quarter_lead": result == "L" and any(m > 0 for m in late),
            "points_by_quarter": qs[team][:4],
        }
    return out


def _game_label(g: dict) -> str:
    return f"Week {g['week']} {'vs' if g['home'] else '@'} {g['opponent']} ({g['result']} {g['final']})"


def _winning_score(g: dict) -> str:
    d = g.get("decisive")
    return f"{d['play']} ({d['period']} {d['clock']})" if d else ""


def profile(games: list) -> dict:
    """Aggregate of situation dicts (each with `week`); empty when there are no games."""
    if not games:
        return {}
    by_half = {"leading": [], "trailing": [], "tied": []}
    for g in games:
        m = g["halftime_margin"]
        by_half["leading" if m > 0 else "trailing" if m < 0 else "tied"].append(g["result"])
    comebacks = [g for g in games if g["fourth_quarter_comeback"]]
    gwds = [g for g in games if g["game_winning_drive"]]
    blown10 = [g for g in games if g["blew_double_digit_lead"]]
    blown4q = [g for g in games if g["blew_fourth_quarter_lead"]]
    wins = [g for g in games if g["result"] == "W"]
    losses = [g for g in games if g["result"] == "L"]
    best = max(wins, key=lambda g: g["largest_deficit"], default=None)
    worst = max(losses, key=lambda g: g["largest_lead"], default=None)
    n = len(games)
    out = {
        "games": n,
        "record": _record(g["result"] for g in games),
        "fourth_quarter_comebacks": len(comebacks),
        "game_winning_drives": len(gwds),
        "blown_double_digit_leads": len(blown10),
        "blown_fourth_quarter_leads": len(blown4q),
        "record_leading_at_half": _record(by_half["leading"]),
        "record_trailing_at_half": _record(by_half["trailing"]),
        "record_tied_at_half": _record(by_half["tied"]),
        "record_trailing_after_three_quarters": _record(g["result"] for g in games if g["margin_after_three"] < 0),
        "record_leading_after_three_quarters": _record(g["result"] for g in games if g["margin_after_three"] > 0),
        "overtime_record": _record(g["result"] for g in games if g["overtime"]),
        "points_per_game_by_quarter": [round(sum(g["points_by_quarter"][q] for g in games) / n, 1) for q in range(4)],
    }
    if best and best["largest_deficit"] > 0:
        out["largest_comeback_win"] = f"{best['largest_deficit']} points: {_game_label(best)}"
    if worst and worst["largest_lead"] > 0:
        out["largest_lead_lost"] = f"{worst['largest_lead']} points: {_game_label(worst)}"
    out["comeback_games"] = [f"{_game_label(g)}: trailed by as many as {g['late_deficit']} in the fourth quarter; "
                             f"won on {_winning_score(g)}" for g in comebacks]
    out["game_winning_drive_games"] = [f"{_game_label(g)}: {_winning_score(g)}" for g in gwds]
    blown = [g for g in games if g["blew_double_digit_lead"] or g["blew_fourth_quarter_lead"]]
    out["blown_lead_games"] = [f"{_game_label(g)}: led by {g['largest_lead']}"
                               + (", and led in the fourth quarter" if g["blew_fourth_quarter_lead"] else "")
                               for g in blown]
    return out


def fetch_rows(cur, season: int) -> list:
    """Every scripted game of the season: week, game type, script, team ids by abbreviation, starting QBs."""
    cur.execute("""
        SELECT gs.game_id, g.week, COALESCE(g.game_type, 'REGULAR_SEASON'), gs.script,
               g.away_team_id, g.home_team_id
        FROM game_scripts gs JOIN games g ON g.id = gs.game_id
        WHERE gs.season = %s AND g.status = 'COMPLETED'
        ORDER BY g.week, gs.game_id
    """, (season,))
    rows = [{"game_id": r[0], "week": r[1], "game_type": r[2], "script": r[3],
             "team_ids": {r[3]["teams"]["away"]: r[4], r[3]["teams"]["home"]: r[5]}} for r in cur.fetchall()]
    cur.execute("""
        SELECT DISTINCT ON (ps.game_id, ps.team_id) ps.game_id, ps.team_id, ps.player_id,
               COALESCE(p.full_name, ps.player_name)
        FROM player_stats ps JOIN games g ON g.id = ps.game_id LEFT JOIN players p ON p.id = ps.player_id
        WHERE g.season = %s AND ps.position = 'QB'
        ORDER BY ps.game_id, ps.team_id, ps.pass_attempts DESC NULLS LAST, ps.pass_yards DESC NULLS LAST
    """, (season,))
    starters = {(gid, tid): (pid, name) for gid, tid, pid, name in cur.fetchall()}
    for r in rows:
        r["starters"] = {abbr: starters.get((r["game_id"], tid)) for abbr, tid in r["team_ids"].items()}
    return rows


def situations(rows: list, before_week=None, postseason: bool = False):
    """(row, abbr, situation) for every team in every visible game of the chosen part of the season."""
    for r in rows:
        if before_week is not None and r["week"] >= before_week:
            continue
        if (r["game_type"] != "REGULAR_SEASON") != postseason:
            continue
        for abbr, s in team_situations(r["script"]).items():
            yield r, abbr, {**s, "week": r["week"]}


def team_profile(rows, team_id, before_week=None) -> dict:
    out = {}
    for label, post in (("regular_season", False), ("postseason", True)):
        games = [s for r, abbr, s in situations(rows, before_week, post) if r["team_ids"][abbr] == team_id]
        if games:
            out[label] = profile(games)
    return out


def qb_profile(rows, player_id, before_week=None) -> dict:
    out = {}
    for label, post in (("regular_season", False), ("postseason", True)):
        games = [s for r, abbr, s in situations(rows, before_week, post)
                 if r["starters"].get(abbr) and r["starters"][abbr][0] == player_id]
        if games:
            out[label] = profile(games)
    return out


def league_leaders(rows, before_week=None, limit: int = 8) -> dict:
    """Regular-season leaders: QBs by comebacks and game-winning drives, teams by blown leads and late records."""
    qbs, teams = {}, {}
    for r, abbr, s in situations(rows, before_week):
        teams.setdefault(abbr, []).append(s)
        st = r["starters"].get(abbr)
        if st and st[0] is not None:
            qbs.setdefault((st[0], st[1], abbr), []).append(s)

    def top(groups, key, label_fn, unit):
        ranked = sorted(((sum(g[key] for g in gs), label_fn(k), len(gs)) for k, gs in groups.items()),
                        key=lambda t: (-t[0], t[2]))
        return [f"{name}: {n} in {games} {unit}" for n, name, games in ranked[:limit] if n > 0]

    qb_name = lambda k: f"{k[1]} ({k[2]})"
    return {
        "fourth_quarter_comebacks_qb": top(qbs, "fourth_quarter_comeback", qb_name, "starts"),
        "game_winning_drives_qb": top(qbs, "game_winning_drive", qb_name, "starts"),
        "blown_double_digit_leads_team": top(teams, "blew_double_digit_lead", str, "games"),
        "blown_fourth_quarter_leads_team": top(teams, "blew_fourth_quarter_lead", str, "games"),
        "record_trailing_at_half_team": sorted(
            (f"{abbr}: {_record(g['result'] for g in gs if g['halftime_margin'] < 0)}" for abbr, gs in teams.items()
             if any(g["halftime_margin"] < 0 and g["result"] == "W" for g in gs)),
            key=lambda t: -int(t.split(": ")[1].split("-")[0]))[:limit],
    }
