"""Playoff Clout (PROJECT_SPEC §2.7) for teams and players.

Team:   S = min(100, Σ_y e^{-0.15 (Y - y)} · [100·SB win + 50·SB loss + 20·playoff wins]) over seasons y ≤ Y.
Player: S = min(100, 40·SB MVPs + 25·SB wins played in + 8·other playoff wins played in).

Super Bowls come from games.game_type = 'SUPER_BOWL'; "other playoff wins" are completed wins in any other
non-REGULAR_SEASON game (none exist before 2035). "Played in" means the player has a player_stats row for that game,
which is only knowable from 2035 on: earlier Super Bowl rosters aren't stored, so before 2035 a player only gets
credit for SB MVPs (matched on awards.player_id). Veterans' clout is understated accordingly.

CLI:
    python3 -m engines.playoff_clout_engine --season 2035
"""

import argparse
import math
from collections import defaultdict

import psycopg

from db.db_handler import DatabaseHandler

TEAM_DECAY = 0.15
TEAM_POINTS = {"sb_win": 100, "sb_loss": 50, "playoff_win": 20}
PLAYER_POINTS = {"sbmvp": 40, "sb_win": 25, "playoff_win": 8}


def team_playoff_clout(events: list, season: int) -> dict:
    """events: [(season, team_id, kind)] with kind in TEAM_POINTS. Returns {team_id: clout}."""
    totals = defaultdict(float)
    for y, team_id, kind in events:
        if y <= season:
            totals[team_id] += math.exp(-TEAM_DECAY * (season - y)) * TEAM_POINTS[kind]
    return {t: min(100.0, v) for t, v in totals.items()}


def player_playoff_clout(counts: dict) -> float:
    """counts: {'sbmvp': n, 'sb_win': n, 'playoff_win': n}."""
    return min(100.0, sum(PLAYER_POINTS[k] * counts.get(k, 0) for k in PLAYER_POINTS))


def fetch_team_events(cur, season: int) -> list:
    cur.execute("""
        SELECT season, game_type,
               CASE WHEN home_score > away_score THEN home_team_id ELSE away_team_id END,
               CASE WHEN home_score > away_score THEN away_team_id ELSE home_team_id END
        FROM games
        WHERE status = 'COMPLETED' AND game_type <> 'REGULAR_SEASON' AND season <= %s AND home_score <> away_score
    """, (season,))
    events = []
    for y, game_type, winner, loser in cur.fetchall():
        if game_type == "SUPER_BOWL":
            events += [(y, winner, "sb_win"), (y, loser, "sb_loss")]
        else:
            events.append((y, winner, "playoff_win"))
    return events


def fetch_player_counts(cur, season: int) -> dict:
    """{player_id: {'sbmvp': n, 'sb_win': n, 'playoff_win': n}}"""
    counts = defaultdict(lambda: defaultdict(int))
    cur.execute("SELECT player_id FROM awards WHERE award_type = 'SBMVP' AND player_id IS NOT NULL AND season <= %s", (season,))
    for (pid,) in cur.fetchall():
        counts[pid]["sbmvp"] += 1
    # Wins played in: the player's stat row is for the winning team
    cur.execute("""
        SELECT ps.player_id, g.game_type
        FROM games g
        JOIN player_stats st ON st.game_id = g.id
            AND st.team_id = CASE WHEN g.home_score > g.away_score THEN g.home_team_id ELSE g.away_team_id END
        JOIN players p ON p.player_name = st.player_name
        JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = g.season AND ps.team_id = st.team_id
        WHERE g.status = 'COMPLETED' AND g.game_type <> 'REGULAR_SEASON' AND g.season <= %s AND g.home_score <> g.away_score
    """, (season,))
    for pid, game_type in cur.fetchall():
        counts[pid]["sb_win" if game_type == "SUPER_BOWL" else "playoff_win"] += 1
    return counts


class PlayoffCloutEngine:
    def __init__(self):
        self.db = DatabaseHandler()

    def calculate(self, season: int = 2035):
        print(f"--- Calculating Playoff Clout for Season {season} ---")
        with psycopg.connect(self.db.db_conn_info) as conn:
            with conn.cursor() as cur:
                team_clout = team_playoff_clout(fetch_team_events(cur, season), season)
                cur.execute("SELECT team_id FROM team_seasons WHERE season = %s", (season,))
                cur.executemany("UPDATE team_seasons SET s_playoff = %s WHERE team_id = %s AND season = %s",
                                [(round(team_clout.get(t, 0.0), 2), t, season) for (t,) in cur.fetchall()])

                player_counts = fetch_player_counts(cur, season)
                cur.execute("SELECT player_id FROM player_seasons WHERE season = %s", (season,))
                cur.executemany("UPDATE player_seasons SET s_playoff = %s WHERE player_id = %s AND season = %s",
                                [(round(player_playoff_clout(player_counts.get(p, {})), 2), p, season)
                                 for (p,) in cur.fetchall()])
                cur.execute("SELECT id, name FROM teams")
                names = dict(cur.fetchall())
            conn.commit()

        print("\n🏆 TEAM PLAYOFF CLOUT")
        for t, v in sorted(team_clout.items(), key=lambda kv: kv[1], reverse=True):
            print(f"  {names.get(t, t):<14} {v:5.1f}")
        return team_clout


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--season", type=int, default=2035)
    PlayoffCloutEngine().calculate(season=parser.parse_args().season)
