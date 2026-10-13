"""Projected Excitement Index for scheduled games (PROJECT_SPEC §2.1).

Projects E on the same raw 0–100 scale as the post-game Excitement Index by taking the expected value of
each component of E = 0.35·C + 0.25·P + 0.20·S + 0.20·U before kickoff:

- C (competitiveness): E[max(0, 100 - 4·|margin|)] with margin ~ Normal(μ, σ). μ = slope × (home OVR −
  away OVR), with the slope fitted from completed games; σ is the spread of the residual margins.
- P (production): the league-average game. Team scoring/yardage barely carries over week to week in this
  league (backtest correlation ≈ 0), so team-specific P only added noise.
- S (star impact): the two teams' average top-3 OVR and average milestone-outlier points per game.
- U (upset/narrative): the OVR favourite's win probability blended over chalk and upset outcomes.

Team per-game averages are shrunk toward the league average (SHRINK_GAMES pseudo-games). Results are stored
raw in games.projected_excitement; readers see engines.gotw_engine.display_excitement() of it.

Backtest on 2035 (weeks 3+, earlier weeks only): correlation with actual E ≈ 0.24, mean error ≈ the
"always guess the league average" baseline. Pre-game excitement is only weakly predictable here; the
projection is mainly useful for ranking games, and is labelled as projected wherever it is shown.

CLI:
    python3 -m engines.excitement_projection --season 2035            # project + store scheduled games
    python3 -m engines.excitement_projection --season 2035 --backtest # accuracy vs. completed games (no writes)
"""

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

import psycopg

from db.db_handler import DatabaseHandler
from engines.gotw_engine import (
    competitiveness_score,
    excitement_index,
    fetch_game_player_rows,
    player_outlier_points,
    production_score,
    star_score,
    upset_score,
)

SHRINK_GAMES = 4
DEFAULT_MARGIN_SD = 14.0
DEFAULT_OVR_MARGIN_SLOPE = 2.3   # points of expected margin per point of team OVR gap
MIN_GAMES_FOR_FIT = 32
DEFAULT_TEAM_OVR = 80
# Used only when no completed games exist for the season or the one before it.
FALLBACK_LEAGUE = {"pf": 24.0, "yds": 340.0, "top3": 80.0, "outliers": 10.0}


@dataclass
class TeamProfile:
    games: int
    pf: float        # points scored per game
    yds_for: float   # total offense per game
    top3: float      # mean top-3 OVR of players with stats, per game
    outliers: float  # milestone outlier points per game


@dataclass
class LeagueModel:
    league: TeamProfile
    margin_sd: float
    ovr_margin_slope: float


# ---------------------------------------------------------------------------
# Pure projection math
# ---------------------------------------------------------------------------

def _normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def expected_competitiveness(mu: float, sd: float, step: float = 0.1) -> float:
    """E[C] for margin ~ N(mu, sd). C is 0 beyond a 25-point margin, so integrate over [-25, 25]."""
    total = 0.0
    x = -25.0
    while x <= 25.0:
        pdf = math.exp(-0.5 * ((x - mu) / sd) ** 2) / (sd * math.sqrt(2 * math.pi))
        total += competitiveness_score(abs(x)) * pdf * step
        x += step
    return total


def expected_upset(home_ovr: float, away_ovr: float, mu_home: float, sd: float) -> float:
    if home_ovr == away_ovr:
        return upset_score(home_ovr, away_ovr, favorite_won=True)
    mu_favorite = mu_home if home_ovr > away_ovr else -mu_home
    p_favorite = _normal_cdf(mu_favorite / sd)
    return (p_favorite * upset_score(home_ovr, away_ovr, favorite_won=True)
            + (1 - p_favorite) * upset_score(home_ovr, away_ovr, favorite_won=False))


def project_matchup(home: TeamProfile, away: TeamProfile, home_ovr: float, away_ovr: float, model: LeagueModel) -> dict:
    mu_home = model.ovr_margin_slope * (home_ovr - away_ovr)
    c = expected_competitiveness(mu_home, model.margin_sd)
    p = production_score(2 * model.league.pf, 2 * model.league.yds_for)
    s = star_score(home.top3, away.top3, home.outliers + away.outliers)
    u = expected_upset(home_ovr, away_ovr, mu_home, model.margin_sd)
    return {"C": c, "P": p, "S": s, "U": u, "E": excitement_index(c, p, s, u), "expected_margin_home": mu_home}


def build_profiles(team_games: list, prior_team_games: Optional[list] = None) -> tuple:
    """team_games: per (team, game) dicts {team_id, pf, yds_for, top3, outliers, ...}.
    Returns ({team_id: TeamProfile}, league TeamProfile). Teams with no games use the league profile."""
    league_rows = team_games or prior_team_games or []
    if league_rows:
        n = len(league_rows)
        league = TeamProfile(
            games=0,
            pf=sum(r["pf"] for r in league_rows) / n,
            yds_for=sum(r["yds_for"] for r in league_rows) / n,
            top3=sum(r["top3"] for r in league_rows) / n,
            outliers=sum(r["outliers"] for r in league_rows) / n,
        )
    else:
        f = FALLBACK_LEAGUE
        league = TeamProfile(0, f["pf"], f["yds"], f["top3"], f["outliers"])

    by_team = defaultdict(list)
    for r in team_games:
        by_team[r["team_id"]].append(r)

    def shrunk(rows, key, prior):
        return (sum(r[key] for r in rows) + SHRINK_GAMES * prior) / (len(rows) + SHRINK_GAMES)

    profiles = {
        team_id: TeamProfile(
            games=len(rows),
            pf=shrunk(rows, "pf", league.pf),
            yds_for=shrunk(rows, "yds_for", league.yds_for),
            top3=shrunk(rows, "top3", league.top3),
            outliers=shrunk(rows, "outliers", league.outliers),
        )
        for team_id, rows in by_team.items()
    }
    return profiles, league


def fit_margin_model(team_games: list, team_ovrs: dict) -> tuple:
    """Returns (margin_sd, ovr_margin_slope) from home-team rows: slope is least squares through the origin
    of home margin on home−away OVR; sd is the spread of the residuals. Defaults below MIN_GAMES_FOR_FIT."""
    pairs = []
    for r in team_games:
        if not r["is_home"]:
            continue
        gap = team_ovrs.get(r["team_id"], DEFAULT_TEAM_OVR) - team_ovrs.get(r["opponent_id"], DEFAULT_TEAM_OVR)
        pairs.append((gap, r["pf"] - r["pa"]))
    if len(pairs) < MIN_GAMES_FOR_FIT:
        return DEFAULT_MARGIN_SD, DEFAULT_OVR_MARGIN_SLOPE
    denom = sum(g * g for g, _ in pairs)
    slope = sum(g * m for g, m in pairs) / denom if denom else DEFAULT_OVR_MARGIN_SLOPE
    residuals = [m - slope * g for g, m in pairs]
    sd = math.sqrt(sum(e * e for e in residuals) / (len(residuals) - 1))
    return sd, slope


def build_model(team_games: list, prior_team_games: list, team_ovrs: dict, prior_team_ovrs: dict) -> tuple:
    rows, ovrs = (team_games, team_ovrs) if team_games else (prior_team_games, prior_team_ovrs)
    profiles, league = build_profiles(team_games, prior_team_games)
    sd, slope = fit_margin_model(rows or [], ovrs)
    return profiles, LeagueModel(league=league, margin_sd=sd, ovr_margin_slope=slope)


# ---------------------------------------------------------------------------
# Data loading (mirrors engines/gotw_engine.py's per-game inputs)
# ---------------------------------------------------------------------------

def load_team_games(cur, season: int, before_week: Optional[int] = None) -> list:
    """One row per (team, completed game) for the season, optionally limited to weeks < before_week."""
    cur.execute("""
        SELECT id, week, home_team_id, away_team_id, home_score, away_score, COALESCE(excitement_index, 0)
        FROM games
        WHERE season = %s AND status = 'COMPLETED' AND (%s::int IS NULL OR week < %s::int)
          AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL
    """, (season, before_week, before_week))
    games = {r[0]: r for r in cur.fetchall()}
    if not games:
        return []
    ids = list(games)

    cur.execute("""
        SELECT ts.game_id, t.id, ts.total_offense
        FROM team_stats ts JOIN teams t ON t.name = ts.team_name
        WHERE ts.game_id = ANY(%s)
    """, (ids,))
    yards = {(gid, tid): yds or 0 for gid, tid, yds in cur.fetchall()}

    ovrs = defaultdict(list)
    outliers = defaultdict(int)
    for gid, tid, ovr, *stats in fetch_game_player_rows(cur, ids):
        ovrs[(gid, tid)].append(ovr or 0)
        outliers[(gid, tid)] += player_outlier_points(*stats)

    rows = []
    for gid, (_, week, home, away, hs, as_, stored_e) in games.items():
        for team, opp, pf, pa in ((home, away, hs, as_), (away, home, as_, hs)):
            top3 = sorted(ovrs[(gid, team)], reverse=True)[:3]
            rows.append({
                "game_id": gid, "week": week, "team_id": team, "opponent_id": opp, "is_home": team == home,
                "pf": pf or 0, "pa": pa or 0,
                "yds_for": yards.get((gid, team), 0), "yds_against": yards.get((gid, opp), 0),
                "top3": sum(top3) / 3 if top3 else DEFAULT_TEAM_OVR,
                "outliers": outliers[(gid, team)],
                "stored_e": float(stored_e),
            })
    return rows


def load_team_ovrs(cur, season: int) -> dict:
    cur.execute("SELECT team_id, team_ovr FROM team_seasons WHERE season = %s", (season,))
    return {tid: ovr for tid, ovr in cur.fetchall() if ovr is not None}


def _project(history, prior, games, team_ovrs, prior_ovrs) -> dict:
    profiles, model = build_model(history, prior, team_ovrs, prior_ovrs)
    out = {}
    for gid, home, away in games:
        out[gid] = project_matchup(
            profiles.get(home, model.league), profiles.get(away, model.league),
            team_ovrs.get(home, DEFAULT_TEAM_OVR), team_ovrs.get(away, DEFAULT_TEAM_OVR), model,
        )
    return out


def project_scheduled_games(season: int, week: Optional[int] = None, persist: bool = True, db_conn_info: str = None) -> dict:
    """Projects (and stores, raw) projected_excitement for every SCHEDULED game of the season (or one week)."""
    db_conn_info = db_conn_info or DatabaseHandler().db_conn_info
    with psycopg.connect(db_conn_info) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, home_team_id, away_team_id FROM games
                WHERE season = %s AND status = 'SCHEDULED' AND (%s::int IS NULL OR week = %s::int)
                  AND home_team_id IS NOT NULL AND away_team_id IS NOT NULL
            """, (season, week, week))
            games = cur.fetchall()
            history = load_team_games(cur, season)
            prior = [] if history else load_team_games(cur, season - 1)
            prior_ovrs = {} if history else load_team_ovrs(cur, season - 1)
            projections = _project(history, prior, games, load_team_ovrs(cur, season), prior_ovrs)
            if persist:
                for gid, proj in projections.items():
                    cur.execute("UPDATE games SET projected_excitement = %s WHERE id = %s", (proj["E"], gid))
        if persist:
            conn.commit()
    print(f"✅ Projected Excitement Index for {len(projections)} scheduled game(s) (season {season}{f', week {week}' if week else ''}).")
    return projections


def backtest(season: int, min_history_rows: int = 64, db_conn_info: str = None) -> dict:
    """Projects each completed game from earlier weeks only and compares with its stored E (no writes)."""
    db_conn_info = db_conn_info or DatabaseHandler().db_conn_info
    with psycopg.connect(db_conn_info) as conn:
        with conn.cursor() as cur:
            all_rows = load_team_games(cur, season)
            team_ovrs = load_team_ovrs(cur, season)

    pairs = []
    for week in sorted({r["week"] for r in all_rows}):
        earlier = [r for r in all_rows if r["week"] < week]
        if len(earlier) < min_history_rows:
            continue
        home_rows = [r for r in all_rows if r["week"] == week and r["is_home"]]
        games = [(r["game_id"], r["team_id"], r["opponent_id"]) for r in home_rows]
        projections = _project(earlier, [], games, team_ovrs, {})
        pairs += [(projections[r["game_id"]]["E"], r["stored_e"]) for r in home_rows if r["stored_e"] > 0]

    proj = [p for p, _ in pairs]
    act = [a for _, a in pairs]
    n = len(pairs)
    mean_act, mean_proj = sum(act) / n, sum(proj) / n
    cov = sum((p - mean_proj) * (a - mean_act) for p, a in pairs)
    corr = cov / math.sqrt(sum((p - mean_proj) ** 2 for p in proj) * sum((a - mean_act) ** 2 for a in act))
    return {
        "games": n,
        "mean_actual": round(mean_act, 2),
        "mean_projected": round(mean_proj, 2),
        "mae": round(sum(abs(p - a) for p, a in pairs) / n, 2),
        "mae_league_mean_baseline": round(sum(abs(mean_act - a) for a in act) / n, 2),
        "correlation": round(corr, 3),
        "projected_range": (round(min(proj), 1), round(max(proj), 1)),
        "actual_range": (round(min(act), 1), round(max(act), 1)),
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Project the Excitement Index (E) for scheduled games.")
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--week", type=int, help="Only project this week's scheduled games")
    parser.add_argument("--backtest", action="store_true", help="Score projection accuracy on completed games (no writes)")
    args = parser.parse_args()
    if args.backtest:
        for k, v in backtest(args.season).items():
            print(f"{k}: {v}")
    else:
        project_scheduled_games(args.season, week=args.week)
