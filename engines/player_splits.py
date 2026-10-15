"""Player splits by the kind of defense faced (PROJECT_SPEC §2.13), for the writer tool get_player_scheme_splits.

Each opponent defense is grouped three ways for the season of the game, each as Strong / Average / Weak (the best
11 of 32, the next 10, the rest):
- scoring defense   points allowed per regular-season game (games the article may see)
- pass rush         Madden's pass-rush rank (team_seasons.pass_rush_rank, 1 = best)
- coverage          Madden's coverage rank (team_seasons.coverage_rank, 1 = best)

Only real game lines count: captured box scores and exact weekly lines (`player_stats.line_source` CAPTURED or
WEEKLY). DISTRIBUTED lines spread a season remainder over several games by score, so they say nothing about the
opponent and are skipped. Each group's per-game averages are blended toward the player's overall average with
BLEND_GAMES pseudo-games, and a group needs MIN_GAMES real games to be reported.
"""

REAL_SOURCES = ("CAPTURED", "WEEKLY")
MIN_GAMES = 3
BLEND_GAMES = 3
GROUPS = ("Strong", "Average", "Weak")
STRONG_SHARE, AVERAGE_SHARE = 11 / 32, 21 / 32

STATS = {
    "QB": ("completions", "pass_attempts", "pass_yards", "pass_tds", "interceptions", "rush_yards", "fantasy_points"),
    "RB": ("rush_attempts", "rush_yards", "rush_tds", "receptions", "rec_yards", "fantasy_points"),
    "REC": ("receptions", "rec_yards", "rec_tds", "fantasy_points"),
}
DIMENSIONS = {
    "scoring_defense": "points allowed per game",
    "pass_rush": "Madden pass-rush rank",
    "coverage": "Madden coverage rank",
}


def stat_group(position: str) -> str | None:
    p = (position or "").upper()
    if p == "QB":
        return "QB"
    if p in ("HB", "RB", "FB"):
        return "RB"
    if p in ("WR", "TE"):
        return "REC"
    return None


def rank_groups(values: dict, best_low: bool = True) -> dict:
    """{team: 'Strong'|'Average'|'Weak'}: the best 11/32 Strong, the next 10/32 Average, the rest Weak.
    best_low: a lower value is better (points allowed, a rank)."""
    n = len(values)
    order = sorted(values, key=lambda k: values[k], reverse=not best_low)
    strong, average = round(n * STRONG_SHARE), round(n * AVERAGE_SHARE)
    return {k: "Strong" if i < strong else "Average" if i < average else "Weak" for i, k in enumerate(order)}


def _per_game(lines: list, stats: tuple) -> dict:
    n = len(lines)
    out = {s: sum(float(l.get(s) or 0) for l in lines) / n for s in stats}
    if "pass_attempts" in out:
        att = sum(float(l.get("pass_attempts") or 0) for l in lines)
        out["comp_pct"] = 100 * sum(float(l.get("completions") or 0) for l in lines) / att if att else None
        out["yards_per_attempt"] = sum(float(l.get("pass_yards") or 0) for l in lines) / att if att else None
    return out


def compute_splits(lines: list, opponent_groups: dict, stats: tuple) -> dict:
    """lines: real game lines, each with 'dimension groups' looked up by opponent_groups[(season, opp_team_id)] →
    {dimension: group}. Returns {overall, dimensions: {dim: {group: {...}}}}."""
    if not lines:
        return {"games": 0, "overall_per_game": None, "dimensions": {}}
    overall = _per_game(lines, stats)
    dims = {}
    for dim in DIMENSIONS:
        by_group = {g: [] for g in GROUPS}
        for l in lines:
            grp = opponent_groups.get((l["season"], l["opponent_id"]), {}).get(dim)
            if grp:
                by_group[grp].append(l)
        out = {}
        for grp, gl in by_group.items():
            n = len(gl)
            if n < MIN_GAMES:
                out[grp] = {"games": n, "note": f"fewer than {MIN_GAMES} real games"}
                continue
            raw = _per_game(gl, stats)
            blended = {s: (n * raw[s] + BLEND_GAMES * overall[s]) / (n + BLEND_GAMES)
                       if raw[s] is not None and overall[s] is not None else None for s in raw}
            vs = {s: f"{(blended[s] / overall[s] - 1) * 100:+.0f}%" for s in blended
                  if blended[s] is not None and overall[s] and s not in ("completions", "pass_attempts")}
            out[grp] = {"games": n,
                        "per_game": {s: round(v, 1) for s, v in blended.items() if v is not None},
                        "vs_his_average": vs}
        dims[dim] = out
    return {"games": len(lines), "overall_per_game": {s: round(v, 1) for s, v in overall.items() if v is not None},
            "dimensions": dims}


# --- database --------------------------------------------------------------------------------------------------

def defense_groups(cur, season: int, visible) -> dict:
    """{team_id: {dimension: group}} for `season`."""
    cur.execute("""
        SELECT season, week, home_team_id, away_team_id, home_score, away_score FROM games
        WHERE season = %s AND status = 'COMPLETED' AND game_type = 'REGULAR_SEASON'
    """, (season,))
    allowed, games = {}, {}
    for s, wk, h, a, hs, as_ in cur.fetchall():
        if not visible(s, wk):
            continue
        for team, opp_pts in ((h, as_), (a, hs)):
            allowed[team] = allowed.get(team, 0) + opp_pts
            games[team] = games.get(team, 0) + 1
    out = {}
    if allowed:
        for t, g in rank_groups({t: allowed[t] / games[t] for t in allowed}).items():
            out.setdefault(t, {})["scoring_defense"] = g
    cur.execute("SELECT team_id, pass_rush_rank, coverage_rank FROM team_seasons WHERE season = %s", (season,))
    rows = cur.fetchall()
    for dim, idx in (("pass_rush", 1), ("coverage", 2)):
        ranks = {r[0]: r[idx] for r in rows if r[idx] is not None}
        for t, g in rank_groups(ranks).items():
            out.setdefault(t, {})[dim] = g
    return out


def player_lines(cur, player_id: int, seasons: list, visible) -> tuple:
    """(real lines with season and opponent_id, count of skipped synthetic lines)."""
    cur.execute("""
        SELECT g.season, g.week, ps.team_id, g.home_team_id, g.away_team_id, ps.line_source,
               ps.completions, ps.pass_attempts, ps.pass_yards, ps.pass_tds, ps.interceptions,
               ps.rush_attempts, ps.rush_yards, ps.rush_tds, ps.receptions, ps.rec_yards, ps.rec_tds,
               ps.fantasy_points
        FROM player_stats ps JOIN games g ON g.id = ps.game_id
        WHERE ps.player_id = %s AND g.season = ANY(%s) AND g.status = 'COMPLETED'
        ORDER BY g.season, g.week
    """, (player_id, list(seasons)))
    cols = ("season", "week", "team_id", "home", "away", "line_source", "completions", "pass_attempts", "pass_yards",
            "pass_tds", "interceptions", "rush_attempts", "rush_yards", "rush_tds", "receptions", "rec_yards",
            "rec_tds", "fantasy_points")
    real, skipped = [], 0
    for r in cur.fetchall():
        line = dict(zip(cols, r))
        if not visible(line["season"], line["week"]):
            continue
        if line["line_source"] not in REAL_SOURCES:
            skipped += 1
            continue
        line["opponent_id"] = line["away"] if line["team_id"] == line["home"] else line["home"]
        real.append(line)
    return real, skipped
