"""Opponent-Adjusted Game Credit (PROJECT_SPEC §2.4).

Measures how good a team has been once schedule strength is removed, in points per game relative to a
league-average team. It is a Simple Rating System (SRS) fitted on completed games:

- Each game's margin is capped at ±MARGIN_CAP so garbage-time blowouts don't dominate.
- Home-field advantage H is the league's average home margin this season (clamped to [0, HFA_MAX]);
  Super Bowl games are neutral. A team's site-adjusted margin is margin − H at home, margin + H away.
- Game credit = site-adjusted margin + opponent rating: the margin an average team would need against
  that opponent at that site to match this result. Beating a +8 team by 3 on the road earns ≈ 3 + H + 8.
- A team's rating (`opp_adj_credit`) is its mean game credit, shrunk toward 0 by SHRINK_GAMES pseudo-games
  so early-season ratings stay sane. Ratings depend on each other and are solved iteratively, centred on 0.
- `opp_adj_credit_recent` is the same game credit with TWI's recency decay (λ = 0.15 per week).
- `sos_rating` is the mean opponent rating faced (positive = harder schedule).
- `trajectory_multiplier` (T) compares the last TRAJECTORY_GAMES games' mean credit with the season rating:
  T = clamp(1 + 0.5·Δ / 20, 0.80, 1.20). The 0.5 shrink damps a noisy 4-game mean; T = 1.00 before
  TRAJECTORY_MIN_GAMES games. Labels: Surging ≥ 1.08, Rising ≥ 1.03, Steady, Fading ≤ 0.97, Sliding < 0.92.
- `expected_wins` sums each game's win probability Φ((rating − opp rating ± H) / σ), where σ is the spread of
  margins around the fitted ratings. `wins − expected_wins` flags paper tigers (lucky records) and
  unlucky teams.

Stored on team_seasons; writer agents read it live through newsroom.db_tools.get_opponent_adjusted_credit.

CLI:
    python3 -m engines.game_credit_engine --season 2035
"""

import argparse
import math
from collections import defaultdict
from dataclasses import dataclass, field

import psycopg

from db.db_handler import DatabaseHandler

MARGIN_CAP = 24
HFA_MAX = 4.0
SHRINK_GAMES = 2
RECENCY_LAMBDA = 0.15
DEFAULT_MARGIN_SD = 14.0
MIN_GAMES_FOR_SD = 32
NEUTRAL_GAME_TYPES = {"SUPER_BOWL"}
TRAJECTORY_GAMES = 4
TRAJECTORY_MIN_GAMES = 6
TRAJECTORY_SHRINK = TRAJECTORY_GAMES / (TRAJECTORY_GAMES + 4)
TRAJECTORY_SCALE = 20.0
TRAJECTORY_BOUNDS = (0.80, 1.20)
MAX_ITERATIONS = 500
TOLERANCE = 1e-6


@dataclass
class TeamCredit:
    team_id: int
    games: int = 0
    wins: float = 0.0           # ties count 0.5
    point_diff: int = 0         # raw, uncapped
    rating: float = 0.0         # opp_adj_credit
    recent: float = 0.0         # opp_adj_credit_recent
    sos: float = 0.0
    expected_wins: float = 0.0
    trajectory: float = 1.0     # trajectory_multiplier (T)
    game_log: list = field(default_factory=list)


def _normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def trajectory_multiplier(game_credits: list, season_rating: float) -> float:
    """game_credits in week order. 1.0 until TRAJECTORY_MIN_GAMES games."""
    if len(game_credits) < TRAJECTORY_MIN_GAMES:
        return 1.0
    recent = game_credits[-TRAJECTORY_GAMES:]
    delta = sum(recent) / len(recent) - season_rating
    lo, hi = TRAJECTORY_BOUNDS
    return max(lo, min(hi, 1 + TRAJECTORY_SHRINK * delta / TRAJECTORY_SCALE))


def trajectory_label(t: float) -> str:
    if t >= 1.08:
        return "Surging"
    if t >= 1.03:
        return "Rising"
    if t > 0.97:
        return "Steady"
    if t >= 0.92:
        return "Fading"
    return "Sliding"


def _site_sign(game: dict, team_id: int) -> int:
    """+1 home, −1 away, 0 neutral."""
    if game.get("game_type") in NEUTRAL_GAME_TYPES:
        return 0
    return 1 if game["home_team_id"] == team_id else -1


def _team_rows(games: list) -> dict:
    """{team_id: [per-game rows from that team's perspective]}"""
    rows = defaultdict(list)
    for g in games:
        margin_home = g["home_score"] - g["away_score"]
        for team_id, opp_id, margin in ((g["home_team_id"], g["away_team_id"], margin_home),
                                        (g["away_team_id"], g["home_team_id"], -margin_home)):
            rows[team_id].append({
                "game_id": g.get("id"), "week": g["week"], "opponent_id": opp_id,
                "site": _site_sign(g, team_id), "margin": margin,
                "capped": max(-MARGIN_CAP, min(MARGIN_CAP, margin)),
                "pf": g["home_score"] if team_id == g["home_team_id"] else g["away_score"],
                "pa": g["away_score"] if team_id == g["home_team_id"] else g["home_score"],
            })
    return rows


def fit_home_field(games: list) -> float:
    non_neutral = [g for g in games if g.get("game_type") not in NEUTRAL_GAME_TYPES]
    if not non_neutral:
        return 0.0
    avg = sum(max(-MARGIN_CAP, min(MARGIN_CAP, g["home_score"] - g["away_score"])) for g in non_neutral) / len(non_neutral)
    return max(0.0, min(HFA_MAX, avg))


def compute_game_credit(games: list, current_week: int = None) -> tuple:
    """games: completed games as dicts {id, week, home_team_id, away_team_id, home_score, away_score, game_type}.
    Returns ({team_id: TeamCredit}, home_field_advantage, margin_sd)."""
    hfa = fit_home_field(games)
    rows = _team_rows(games)
    if current_week is None:
        current_week = max((g["week"] for g in games), default=1)

    def adj(r):
        return r["capped"] - r["site"] * hfa

    ratings = {t: 0.0 for t in rows}
    for _ in range(MAX_ITERATIONS):
        new = {t: sum(adj(r) + ratings[r["opponent_id"]] for r in rs) / (len(rs) + SHRINK_GAMES)
               for t, rs in rows.items()}
        mean = sum(new.values()) / len(new) if new else 0.0
        new = {t: v - mean for t, v in new.items()}
        delta = max((abs(new[t] - ratings[t]) for t in new), default=0.0)
        ratings = new
        if delta < TOLERANCE:
            break

    residuals = [r["capped"] - (ratings[t] - ratings[r["opponent_id"]] + r["site"] * hfa)
                 for t, rs in rows.items() for r in rs]
    if len(residuals) >= 2 * MIN_GAMES_FOR_SD:
        margin_sd = math.sqrt(sum(x * x for x in residuals) / len(residuals))
    else:
        margin_sd = DEFAULT_MARGIN_SD

    results = {}
    for t, rs in rows.items():
        tc = TeamCredit(team_id=t, games=len(rs), rating=ratings[t])
        weighted, weight_total = 0.0, 0.0
        for r in sorted(rs, key=lambda r: r["week"]):
            opp_rating = ratings[r["opponent_id"]]
            credit = adj(r) + opp_rating
            w = math.exp(-RECENCY_LAMBDA * (current_week - r["week"]))
            weighted += w * credit
            weight_total += w
            tc.wins += 1.0 if r["margin"] > 0 else (0.5 if r["margin"] == 0 else 0.0)
            tc.point_diff += r["margin"]
            tc.sos += opp_rating
            tc.expected_wins += _normal_cdf((ratings[t] - opp_rating + r["site"] * hfa) / margin_sd)
            tc.game_log.append({**r, "opponent_rating": opp_rating, "game_credit": credit})
        tc.recent = weighted / weight_total if weight_total else 0.0
        tc.trajectory = trajectory_multiplier([g["game_credit"] for g in tc.game_log], tc.rating)
        tc.sos /= len(rs)
        results[t] = tc
    return results, hfa, margin_sd


def fetch_completed_games(cur, season: int) -> list:
    """The season's completed regular-season games: credit, expected wins and luck sit next to the regular-season
    record in team_seasons, so playoff games stay out (a playoff game shows no game credit)."""
    cur.execute("""
        SELECT id, week, home_team_id, away_team_id, home_score, away_score, game_type
        FROM games
        WHERE season = %s AND status = 'COMPLETED' AND home_score IS NOT NULL AND away_score IS NOT NULL
          AND COALESCE(game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
        ORDER BY week, id
    """, (season,))
    cols = ["id", "week", "home_team_id", "away_team_id", "home_score", "away_score", "game_type"]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


class GameCreditEngine:
    def __init__(self):
        self.db = DatabaseHandler()

    def calculate(self, season: int | None = None):
        if season is None:
            season = self.db.get_current_league_state()[0]
        print(f"--- Calculating Opponent-Adjusted Game Credit for Season {season} ---")
        with psycopg.connect(self.db.db_conn_info) as conn:
            with conn.cursor() as cur:
                games = fetch_completed_games(cur, season)
                if not games:
                    print("No completed games; nothing to rate.")
                    return {}
                credits, hfa, sd = compute_game_credit(games)
                cur.executemany("""
                    UPDATE team_seasons
                    SET opp_adj_credit = %s, opp_adj_credit_recent = %s, sos_rating = %s, expected_wins = %s,
                        trajectory_multiplier = %s
                    WHERE team_id = %s AND season = %s
                """, [(round(c.rating, 2), round(c.recent, 2), round(c.sos, 2), round(c.expected_wins, 2),
                       round(c.trajectory, 3), t, season)
                      for t, c in credits.items()])
                cur.execute("SELECT id, name FROM teams")
                names = dict(cur.fetchall())
            conn.commit()

        print(f"Home-field advantage: {hfa:.2f} pts | margin σ: {sd:.1f} pts")
        print("\n📊 OPPONENT-ADJUSTED GAME CREDIT (points per game vs. an average team)")
        print("=" * 92)
        for i, c in enumerate(sorted(credits.values(), key=lambda c: c.rating, reverse=True), start=1):
            luck = c.wins - c.expected_wins
            print(f"{i:>2}. {names.get(c.team_id, c.team_id):<14} | Credit: {c.rating:+6.1f} | Recent: {c.recent:+6.1f} "
                  f"| T: {c.trajectory:.2f} | SOS: {c.sos:+5.1f} | Raw diff/g: {c.point_diff / c.games:+6.1f} | W: {c.wins:>4.1f} xW: {c.expected_wins:>4.1f} ({luck:+.1f})")
        return credits


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--season", type=int, help="default: league_state.current_season")
    GameCreditEngine().calculate(season=parser.parse_args().season)
