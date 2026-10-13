"""Matchup Edge and Trench Edge (PROJECT_SPEC §2.8), computed live for writer tools (nothing is stored).

For team A's offense against team B's defense (z = z-score across the 32 teams):
- Pass edge          = z(A pass yds/g) + z(B coverage_rank)      (Madden ranks run 1 = best, so a high rank is a weak secondary)
- Rush edge          = z(A rush yds/g) − z(B def_ovr)
- Ball-security edge = z(B giveaways/g) − z(A giveaways/g)       (positive: B is the more careless team with the ball)
- Trench edge        = mean OVR of A's top 5 offensive linemen − mean OVR of B's top 4 pass rushers (OVR points),
                       reported with B's pass_rush_rank.

The defensive side uses Madden ratings rather than "yards allowed" or takeaways, because per-game box scores for
CPU-simmed games are synthetic splits of season totals (§2.6), which makes anything attributed to an opponent unreliable.
"""

import statistics

OL_POSITIONS = ("LT", "LG", "C", "RG", "RT")
PASS_RUSH_POSITIONS = ("LEDG", "REDG", "LE", "RE", "DT")
OL_COUNT = 5
PASS_RUSH_COUNT = 4


def z_scores(values: dict) -> dict:
    """{team_id: value} → {team_id: z}. All zeros when there's no spread."""
    vals = [v for v in values.values() if v is not None]
    if len(vals) < 2:
        return {k: 0.0 for k in values}
    mean, sd = statistics.mean(vals), statistics.pstdev(vals)
    return {k: ((v - mean) / sd if sd and v is not None else 0.0) for k, v in values.items()}


def unit_average(ovrs: list, count: int):
    top = sorted(ovrs, reverse=True)[:count]
    return sum(top) / len(top) if top else None


def compute_edges(profiles: dict, ratings: dict, units: dict, off_team: int, def_team: int) -> dict:
    """profiles: {team_id: {pass_yds_per_game, rush_yds_per_game, giveaways_per_game}} (team_efficiency_engine).
    ratings: {team_id: {coverage_rank, def_ovr, pass_rush_rank}}. units: {team_id: {"ol": avg OVR, "pass_rush": avg OVR}}.
    Returns off_team's offensive edges against def_team's defense."""
    z_pass = z_scores({t: p["pass_yds_per_game"] for t, p in profiles.items()})
    z_rush = z_scores({t: p["rush_yds_per_game"] for t, p in profiles.items()})
    z_give = z_scores({t: p["giveaways_per_game"] for t, p in profiles.items()})
    z_cov = z_scores({t: r["coverage_rank"] for t, r in ratings.items()})
    z_def = z_scores({t: r["def_ovr"] for t, r in ratings.items()})

    edges = {
        "pass": z_pass.get(off_team, 0.0) + z_cov.get(def_team, 0.0),
        "rush": z_rush.get(off_team, 0.0) - z_def.get(def_team, 0.0),
        "ball_security": z_give.get(def_team, 0.0) - z_give.get(off_team, 0.0),
    }
    ol = units.get(off_team, {}).get("ol")
    rush = units.get(def_team, {}).get("pass_rush")
    trench = ol - rush if ol is not None and rush is not None else None
    best = max(edges, key=edges.get)
    worst = min(edges, key=edges.get)
    return {
        "edges": edges,
        "trench_edge": trench,
        "best_edge": best if edges[best] > 0 else None,
        "biggest_vulnerability": worst if edges[worst] < 0 else None,
    }


def fetch_ratings(cur, season: int) -> dict:
    cur.execute("""
        SELECT team_id, coverage_rank, def_ovr, pass_rush_rank FROM team_seasons WHERE season = %s
    """, (season,))
    return {t: {"coverage_rank": c, "def_ovr": d, "pass_rush_rank": p} for t, c, d, p in cur.fetchall()}


def fetch_units(cur, season: int) -> dict:
    cur.execute("""
        SELECT team_id, position, overall_rating FROM player_seasons
        WHERE season = %s AND position = ANY(%s) AND overall_rating IS NOT NULL
    """, (season, list(OL_POSITIONS + PASS_RUSH_POSITIONS)))
    ol, rush = {}, {}
    for team_id, pos, ovr in cur.fetchall():
        (ol if pos in OL_POSITIONS else rush).setdefault(team_id, []).append(ovr)
    teams = set(ol) | set(rush)
    return {t: {"ol": unit_average(ol.get(t, []), OL_COUNT), "pass_rush": unit_average(rush.get(t, []), PASS_RUSH_COUNT)}
            for t in teams}
