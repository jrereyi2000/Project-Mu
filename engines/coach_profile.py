"""Head-coach profiles for the writer tools (newsroom/db_tools.py: get_coach_profile, get_team_context,
get_matchup_edges). PROJECT_SPEC §3.3a.

A coach's career totals are the latest `coach_seasons` snapshot before the article's season (totals at the end of
that season) plus every completed game his team played afterwards while he held the job (`coach_tenures`; one head
coach per team per season). This mirrors the `coach_records` view, but only counts games the article may see
(`visible(season, week)`), so a rerun of an old week never sees later results.
"""

TIERS = ("Low", "Medium", "High")

TENDENCY_WORDS = {
    "pass_lean": {"High": "pass-heavy: throws more than the situation calls for",
                  "Medium": "balanced run/pass mix",
                  "Low": "run-heavy: runs more than the situation calls for"},
    "fourth_down_aggressiveness": {"High": "aggressive: goes for it on fourth-and-short more than most",
                                   "Medium": "middle of the pack on fourth-and-short",
                                   "Low": "conservative: kicks on fourth-and-short more than most"},
    "blitz_rate": {"High": "blitz-heavy: sends five or more rushers more than most",
                   "Medium": "blitzes about as often as the league",
                   "Low": "rarely blitzes: wins with a four-man rush"},
    "man_coverage": {"High": "man-heavy coverage",
                     "Medium": "mixes man and zone",
                     "Low": "zone-first coverage"},
}

TENDENCY_NOTE = ("Tendencies are the staff's real-world 2026 tendencies (the playbooks haven't changed since), ranked "
                 "against the league as Low / Medium / High. Describe them as tendencies (\"a blitz-heavy defense\"); "
                 "never turn them into percentages or claim how often anything happened in a specific game.")

REGULAR = "REGULAR_SEASON"
FULL_SEASON_GAMES = 17


def describe_tendencies(coach: dict) -> dict:
    """{'pass_lean': 'High (pass-heavy: ...)', ...}; None where the coach has no rating."""
    return {k: (f"{coach[k]} ({TENDENCY_WORDS[k][coach[k]]})" if coach.get(k) in TIERS else None)
            for k in TENDENCY_WORDS}


def _result(game: dict, team_id: int):
    """'W' / 'L' / 'T' for team_id in a completed game."""
    mine, theirs = ((game["home_score"], game["away_score"]) if game["home_team_id"] == team_id
                    else (game["away_score"], game["home_score"]))
    return "W" if mine > theirs else "L" if mine < theirs else "T"


def team_for_season(tenures: list, season: int):
    """The coach's team in `season`, from his tenures [{team_id, start_season, end_season}], or None."""
    for t in tenures:
        if t["start_season"] <= season and (t["end_season"] is None or season <= t["end_season"]):
            return t["team_id"]
    return None


def accumulate(snapshot: dict | None, tenures: list, games: list, through_season: int) -> dict | None:
    """Career totals: `snapshot` (a coach_seasons row as a dict, or None) plus the coach's games in later seasons up
    to `through_season`. `games` are completed, already-visible games (any teams) with season, week, game_type,
    home/away team ids and scores. A season adds a winning season once its 17-game regular season is complete."""
    if snapshot is None:
        return None
    tot = {k: snapshot[k] or 0 for k in ("career_wins", "career_losses", "career_ties", "winning_seasons",
                                         "playoff_wins", "playoff_losses", "super_bowl_wins", "super_bowl_losses")}
    by_season = {}
    for g in games:
        s = g["season"]
        if s <= snapshot["season"] or s > through_season:
            continue
        team = team_for_season(tenures, s)
        if team is None or team not in (g["home_team_id"], g["away_team_id"]):
            continue
        r = _result(g, team)
        if (g.get("game_type") or REGULAR) == REGULAR:
            tot[{"W": "career_wins", "L": "career_losses", "T": "career_ties"}[r]] += 1
            rec = by_season.setdefault(s, [0, 0, 0])
            rec["WLT".index(r)] += 1
        else:
            if r == "W":
                tot["playoff_wins"] += 1
            elif r == "L":
                tot["playoff_losses"] += 1
            if g.get("game_type") == "SUPER_BOWL" and r != "T":
                tot["super_bowl_wins" if r == "W" else "super_bowl_losses"] += 1
    tot["winning_seasons"] += sum(1 for w, l, t in by_season.values() if w + l + t >= FULL_SEASON_GAMES and w > l)
    return tot


def season_record(games: list, team_id: int, season: int) -> dict:
    """The team's regular-season and playoff record in `season` from the given (visible) games."""
    reg, post = [0, 0, 0], [0, 0, 0]
    for g in games:
        if g["season"] != season or team_id not in (g["home_team_id"], g["away_team_id"]):
            continue
        rec = reg if (g.get("game_type") or REGULAR) == REGULAR else post
        rec["WLT".index(_result(g, team_id))] += 1
    return {"regular_season": "-".join(map(str, reg if reg[2] else reg[:2])),
            "playoffs": "-".join(map(str, post[:2])) if any(post) else None}


def record_line(t: dict | None) -> dict | None:
    if t is None:
        return None
    w, l, ties = t["career_wins"], t["career_losses"], t["career_ties"]
    games = w + l + ties
    return {
        "regular_season": f"{w}-{l}" + (f"-{ties}" if ties else ""),
        "win_pct": round((w + 0.5 * ties) / games, 3) if games else None,
        "winning_seasons": t["winning_seasons"],
        "playoffs": f"{t['playoff_wins']}-{t['playoff_losses']}",
        "super_bowls": f"{t['super_bowl_wins']}-{t['super_bowl_losses']}",
    }


# --- database --------------------------------------------------------------------------------------------------

COACH_COLS = ("id", "coach_name", "offensive_scheme", "defensive_scheme", "pass_lean",
              "fourth_down_aggressiveness", "blitz_rate", "man_coverage")


def fetch_team_coach(cur, team_id: int, season: int) -> dict | None:
    """The team's head coach in `season` with his tenure start there."""
    cur.execute(f"""
        SELECT {', '.join('c.' + c for c in COACH_COLS)}, ct.start_season
        FROM coach_tenures ct JOIN coaches c ON c.id = ct.coach_id
        WHERE ct.team_id = %s AND ct.start_season <= %s AND COALESCE(ct.end_season, 9999) >= %s
        ORDER BY ct.start_season DESC LIMIT 1
    """, (team_id, season, season))
    row = cur.fetchone()
    return dict(zip(COACH_COLS + ("start_season",), row)) if row else None


def fetch_previous_coach(cur, team_id: int, start_season: int):
    cur.execute("""
        SELECT c.coach_name, ct.start_season, ct.end_season
        FROM coach_tenures ct JOIN coaches c ON c.id = ct.coach_id
        WHERE ct.team_id = %s AND ct.end_season = %s
    """, (team_id, start_season - 1))
    row = cur.fetchone()
    return {"coach": row[0], "seasons": f"{row[1]}-{row[2]}"} if row else None


def fetch_tenures(cur, coach_id: int) -> list:
    cur.execute("SELECT team_id, start_season, end_season FROM coach_tenures WHERE coach_id = %s", (coach_id,))
    return [dict(zip(("team_id", "start_season", "end_season"), r)) for r in cur.fetchall()]


def fetch_snapshot(cur, coach_id: int, before_season: int) -> dict | None:
    """The coach's latest coach_seasons row from before `before_season`."""
    cols = ("season", "team_id", "career_wins", "career_losses", "career_ties", "winning_seasons",
            "playoff_wins", "playoff_losses", "super_bowl_wins", "super_bowl_losses")
    cur.execute(f"""
        SELECT {', '.join(cols)} FROM coach_seasons
        WHERE coach_id = %s AND season < %s ORDER BY season DESC LIMIT 1
    """, (coach_id, before_season))
    row = cur.fetchone()
    return dict(zip(cols, row)) if row else None


def fetch_games(cur, team_ids, first_season: int, last_season: int, visible) -> list:
    """Completed games involving any of team_ids in [first_season, last_season] that visible(season, week) allows."""
    cur.execute("""
        SELECT season, week, game_type, home_team_id, away_team_id, home_score, away_score
        FROM games
        WHERE status = 'COMPLETED' AND home_score IS NOT NULL AND away_score IS NOT NULL
          AND season BETWEEN %s AND %s AND (home_team_id = ANY(%s) OR away_team_id = ANY(%s))
    """, (first_season, last_season, list(team_ids), list(team_ids)))
    cols = ("season", "week", "game_type", "home_team_id", "away_team_id", "home_score", "away_score")
    return [g for g in (dict(zip(cols, r)) for r in cur.fetchall()) if visible(g["season"], g["week"])]


def coach_profile(cur, team_id: int, season: int, visible, awards_visible: bool) -> dict | None:
    """Everything a writer needs about the team's head coach in `season`. `awards_visible`: whether this season's
    awards (decided after the season) may be shown."""
    coach = fetch_team_coach(cur, team_id, season)
    if not coach:
        return None
    tenures = fetch_tenures(cur, coach["id"])
    snapshot = fetch_snapshot(cur, coach["id"], season)
    first = min([snapshot["season"] + 1 if snapshot else season] + [t["start_season"] for t in tenures])
    games = fetch_games(cur, {t["team_id"] for t in tenures}, first, season, visible)
    career = accumulate(snapshot, tenures, games, season)

    # With this team: every season of this tenure, when each finished season's regular season is on record.
    start = coach["start_season"]
    tenure_games = [g for g in games if start <= g["season"] <= season and team_id in (g["home_team_id"], g["away_team_id"])]
    reg = {s: sum(1 for g in tenure_games if g["season"] == s and (g["game_type"] or REGULAR) == REGULAR)
           for s in range(start, season)}
    with_team = None
    if all(n >= FULL_SEASON_GAMES for n in reg.values()):
        tally = [0, 0, 0]
        for g in tenure_games:
            if (g["game_type"] or REGULAR) == REGULAR:
                tally["WLT".index(_result(g, team_id))] += 1
        with_team = "-".join(map(str, tally if tally[2] else tally[:2]))
    sb = [g for g in tenure_games if g["game_type"] == "SUPER_BOWL"]

    cur.execute("""
        SELECT season FROM awards WHERE award_type = 'COTY' AND coach_id = %s AND (season < %s OR %s)
        ORDER BY season
    """, (coach["id"], season, awards_visible))
    coty = [r[0] for r in cur.fetchall()]

    return {
        "coach": coach["coach_name"],
        "season_with_team": season - start + 1,
        "with_team_since": start,
        "replaced": fetch_previous_coach(cur, team_id, start),
        "schemes": {"offense": coach["offensive_scheme"], "defensive_front": coach["defensive_scheme"]},
        "tendencies": describe_tendencies(coach),
        "this_season": season_record(games, team_id, season),
        "record_with_team": with_team,
        "career": record_line(career),
        "super_bowls_with_team": [f"{g['season']} {'won' if _result(g, team_id) == 'W' else 'lost'}" for g in
                                  sorted(sb, key=lambda g: g["season"])],
        "coach_of_the_year": coty,
        "note": TENDENCY_NOTE + (" record_with_team is null when an earlier season of this tenure isn't on record."
                                 if with_team is None else ""),
    }


def staff_matchup(off_coach: dict | None, def_coach: dict | None) -> dict | None:
    """One offense-vs-defense coaching line for get_matchup_edges."""
    if not off_coach or not def_coach:
        return None
    o, d = describe_tendencies(off_coach), describe_tendencies(def_coach)
    return {
        "offense": f"{off_coach['coach_name']}: {off_coach['offensive_scheme']}, pass lean {o['pass_lean']}, "
                   f"fourth down {o['fourth_down_aggressiveness']}",
        "defense": f"{def_coach['coach_name']}: {def_coach['defensive_scheme']} front, blitz {d['blitz_rate']}, "
                   f"coverage {d['man_coverage']}",
    }
