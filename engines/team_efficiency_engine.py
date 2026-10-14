"""Team efficiency profile (PROJECT_SPEC §2.6): raw-points metrics that complement Opponent-Adjusted Game Credit.

- Pythagorean wins: G · PF^2.37 / (PF^2.37 + PA^2.37) (standard NFL exponent). `pyth_luck` = wins − Pythagorean wins.
  Unlike expected_wins (§2.4), this ignores schedule, so the two disagree when the schedule was unusually easy or hard.
- Team pace/production P_team: min(100, (PF/G)/35·60 + (yds/G)/425·40). These are the game-level production_score
  benchmarks halved to one team. True tempo (plays per game) needs team_stats.total_plays, which is not captured.
- Giveaways and pass/rush yards per game (the team's own offense) are also returned, for Matchup Edge.

Only season totals are trustworthy here. For CPU-simmed games, engines/game_stat_generator.py splits each player's
season totals across games in proportion to each game's combined score, so per-game box scores are synthetic. A
team's own season totals (points, yards, giveaways) are real, but anything attributed to its opponents game by game
(takeaways, yards allowed, TDs vs. points in a given game) is an artifact of that split and is deliberately not used.

CLI:
    python3 -m engines.team_efficiency_engine --season 2035
"""

import argparse
from collections import defaultdict

import psycopg

from db.db_handler import DatabaseHandler

PYTH_EXPONENT = 2.37
TEAM_POINTS_BENCHMARK = 35.0
TEAM_YARDS_BENCHMARK = 425.0


def pythagorean_wins(pf: float, pa: float, games: int) -> float:
    if pf + pa <= 0:
        return games / 2
    return games * pf ** PYTH_EXPONENT / (pf ** PYTH_EXPONENT + pa ** PYTH_EXPONENT)


def team_pace(points_per_game: float, yards_per_game: float) -> float:
    return min(100.0, points_per_game / TEAM_POINTS_BENCHMARK * 60 + yards_per_game / TEAM_YARDS_BENCHMARK * 40)


def compute_team_efficiency(rows: list) -> dict:
    """rows: one dict per (team, completed game) with keys team_id, won (1/0.5/0), pf, pa, yds, pass_yds, rush_yds,
    giveaways. Returns {team_id: profile dict}."""
    by_team = defaultdict(list)
    for r in rows:
        by_team[r["team_id"]].append(r)

    profiles = {}
    for team_id, rs in by_team.items():
        g = len(rs)

        def total(key):
            return sum(r[key] or 0 for r in rs)

        pf, pa, wins = total("pf"), total("pa"), sum(r["won"] for r in rs)
        pyth = pythagorean_wins(pf, pa, g)
        profiles[team_id] = {
            "games": g,
            "wins": wins,
            "points_for": pf,
            "points_against": pa,
            "pythagorean_wins": pyth,
            "pyth_luck": wins - pyth,
            "giveaways_per_game": total("giveaways") / g,
            "pace_production": team_pace(pf / g, total("yds") / g),
            "pass_yds_per_game": total("pass_yds") / g,
            "rush_yds_per_game": total("rush_yds") / g,
        }
    return profiles


def fetch_team_game_rows(cur, season: int, before_week: int = None) -> list:
    """One row per team per completed game. before_week (optional) keeps only games from earlier weeks."""
    cur.execute("""
        WITH tg AS (
            SELECT g.id AS game_id, g.home_team_id AS team_id, g.home_score AS pf, g.away_score AS pa
            FROM games g WHERE g.season = %(s)s AND g.status = 'COMPLETED' AND (%(w)s::int IS NULL OR g.week < %(w)s::int)
            UNION ALL
            SELECT g.id, g.away_team_id, g.away_score, g.home_score
            FROM games g WHERE g.season = %(s)s AND g.status = 'COMPLETED' AND (%(w)s::int IS NULL OR g.week < %(w)s::int)
        )
        SELECT tg.team_id, tg.pf, tg.pa, ts.total_offense, ts.passing_yards, ts.rushing_yards, ts.turnovers
        FROM tg
        LEFT JOIN teams t ON t.id = tg.team_id
        LEFT JOIN team_stats ts ON ts.game_id = tg.game_id AND ts.team_name = t.name
    """, {"s": season, "w": before_week})
    rows = []
    for team_id, pf, pa, yds, pass_yds, rush_yds, giveaways in cur.fetchall():
        rows.append({
            "team_id": team_id, "pf": pf or 0, "pa": pa or 0,
            "won": 1.0 if (pf or 0) > (pa or 0) else (0.5 if pf == pa else 0.0),
            "yds": yds, "pass_yds": pass_yds, "rush_yds": rush_yds, "giveaways": giveaways,
        })
    return rows


class TeamEfficiencyEngine:
    def __init__(self):
        self.db = DatabaseHandler()

    def calculate(self, season: int | None = None) -> dict:
        if season is None:
            season = self.db.get_current_league_state()[0]
        print(f"--- Calculating Team Efficiency Profile for Season {season} ---")
        with psycopg.connect(self.db.db_conn_info) as conn:
            with conn.cursor() as cur:
                profiles = compute_team_efficiency(fetch_team_game_rows(cur, season))
                cur.executemany("""
                    UPDATE team_seasons
                    SET pythagorean_wins = %s, pace_production = %s
                    WHERE team_id = %s AND season = %s
                """, [(round(p["pythagorean_wins"], 2), round(p["pace_production"], 2), t, season)
                      for t, p in profiles.items()])
                cur.execute("SELECT id, name FROM teams")
                names = dict(cur.fetchall())
            conn.commit()

        print("\n📐 TEAM EFFICIENCY PROFILE")
        print("=" * 78)
        for t, p in sorted(profiles.items(), key=lambda kv: kv[1]["pyth_luck"], reverse=True):
            print(f"{names.get(t, t):<14} | W {p['wins']:>4.1f} PythW {p['pythagorean_wins']:>4.1f} ({p['pyth_luck']:+.1f}) "
                  f"| Pace {p['pace_production']:5.1f} | Giveaways/g {p['giveaways_per_game']:.2f}")
        return profiles


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--season", type=int, help="default: league_state.current_season")
    TeamEfficiencyEngine().calculate(season=parser.parse_args().season)
