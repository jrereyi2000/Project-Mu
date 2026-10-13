import psycopg
from typing import List, Optional, Tuple
from db.db_handler import DatabaseHandler

# --- Excitement Index (E) components (PROJECT_SPEC §2.1) ---

WEIGHTS = {"C": 0.35, "P": 0.25, "S": 0.20, "U": 0.20}
HEAVYWEIGHT_OVR = 84


def competitiveness_score(margin: float) -> float:
    """C: 100 minus 4 points per point of final margin, floored at 0 (no OT bonus yet)."""
    return min(100, max(0, 100 - (4 * margin)))


def production_score(total_points: float, combined_yards: float) -> float:
    """P: combined points (benchmark 70 → 60 pts) plus combined yards (benchmark 850 → 40 pts)."""
    return min(100, (total_points / 70.0) * 60 + (combined_yards / 850.0) * 40)


def player_outlier_points(pass_yards, pass_tds, rush_yards, rush_tds, rec_yards, rec_tds) -> int:
    """Milestone box-score bonuses for one player (summed and capped at 100 per game)."""
    pts = 0
    if pass_yards and pass_yards >= 400: pts += 15
    if pass_tds and pass_tds >= 4: pts += 15
    if (rush_yards or 0) + (rec_yards or 0) >= 150: pts += 12
    if (rush_tds or 0) + (rec_tds or 0) >= 2: pts += 8
    return pts


def star_score(mean_top3_home: float, mean_top3_away: float, outlier_points: float) -> float:
    """S: half star power (mean top-3 OVR of both teams), half milestone outliers (capped at 100)."""
    s_stars = (mean_top3_home + mean_top3_away) / 2
    return (0.5 * s_stars) + (0.5 * min(100, outlier_points))


def upset_score(home_ovr: float, away_ovr: float, favorite_won: bool) -> float:
    """U: heavyweight clashes floor at 80; underdog wins scale up and chalk wins scale down with the OVR gap."""
    delta_ovr = max(home_ovr, away_ovr) - min(home_ovr, away_ovr)
    u = 80 if (home_ovr >= HEAVYWEIGHT_OVR and away_ovr >= HEAVYWEIGHT_OVR) else 0
    if not favorite_won:
        return max(u, min(100, 50 + (5 * delta_ovr)))
    return max(u, max(10, 50 - (3 * delta_ovr)))


def excitement_index(c: float, p: float, s: float, u: float) -> float:
    return round(WEIGHTS["C"] * c + WEIGHTS["P"] * p + WEIGHTS["S"] * s + WEIGHTS["U"] * u, 2)


def fetch_game_player_rows(cur, game_ids: List[int]) -> list:
    """One row per player_stats line: (game_id, team_id, overall_rating, pass_yards, pass_tds,
    rush_yards, rush_tds, rec_yards, rec_tds).

    player_stats only stores abbreviated names (e.g. 'A. Wright'), which collide across players, so a
    line is matched to the player with that name on the same team in the game's season (position
    breaks the rare same-team tie). Lines with no such player are skipped.
    """
    cur.execute("""
        SELECT pstat.game_id, pstat.team_id, rated.overall_rating,
               pstat.pass_yards, pstat.pass_tds, pstat.rush_yards, pstat.rush_tds, pstat.rec_yards, pstat.rec_tds
        FROM player_stats pstat
        JOIN games g ON g.id = pstat.game_id
        JOIN LATERAL (
            SELECT ps.overall_rating
            FROM players p
            JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = g.season AND ps.team_id = pstat.team_id
            WHERE p.player_name = pstat.player_name
            ORDER BY (ps.position = pstat.position) DESC, p.id
            LIMIT 1
        ) rated ON TRUE
        WHERE pstat.game_id = ANY(%s)
    """, (list(game_ids),))
    return cur.fetchall()


class GotWEngine:
    def __init__(self):
        self.db = DatabaseHandler()

    def evaluate_game(self, game_id: int) -> float:
        print(f"--- Evaluating Game {game_id} ---")
        with psycopg.connect(self.db.db_conn_info) as conn:
            with conn.cursor() as cur:
                # 1. Fetch Game Score & Overtime Status
                cur.execute("""
                    SELECT home_score, away_score, status, season
                    FROM games WHERE id = %s
                """, (game_id,))
                row = cur.fetchone()
                if not row: return 0.0
                home_score, away_score, status, season = row
                
                # 2. Fetch Team Stats (Production)
                cur.execute("""
                    SELECT total_offense FROM team_stats WHERE game_id = %s
                """, (game_id,))
                team_stats = cur.fetchall()
                combined_yards = sum(r[0] for r in team_stats) if team_stats else 0
                
                # 3. Fetch Player OVRs (Star Impact & Outliers)
                # Rows: (game_id, team_id, ovr, pass_yds, pass_tds, rush_yds, rush_tds, rec_yds, rec_tds)
                players = fetch_game_player_rows(cur, [game_id])

                # 4. Fetch Roster Means (Narrative/Upset)
                # We need to know who the home and away teams are
                cur.execute("""
                    SELECT home_team_id, away_team_id FROM games WHERE id = %s
                """, (game_id,))
                teams_row = cur.fetchone()
                if not teams_row: return 0.0
                home_team_id, away_team_id = teams_row
                
                cur.execute("""
                    SELECT team_id, team_ovr FROM team_seasons WHERE season = %s AND team_id IN (%s, %s)
                """, (season, home_team_id, away_team_id))
                team_ovrs = {r[0]: r[1] for r in cur.fetchall()}
                home_ovr = team_ovrs.get(home_team_id, 80)
                away_ovr = team_ovrs.get(away_team_id, 80)
                
                # --- ALGORITHM CALCULATIONS ---
                c_score = competitiveness_score(abs(home_score - away_score))
                p_score = production_score(home_score + away_score, combined_yards)

                top3_home_ovr = sorted([p[2] for p in players if p[1] == home_team_id], reverse=True)[:3]
                top3_away_ovr = sorted([p[2] for p in players if p[1] == away_team_id], reverse=True)[:3]
                mean_top3_home = sum(top3_home_ovr)/3 if top3_home_ovr else 80
                mean_top3_away = sum(top3_away_ovr)/3 if top3_away_ovr else 80
                s_outliers = sum(player_outlier_points(*p[3:9]) for p in players)
                s_score = star_score(mean_top3_home, mean_top3_away, s_outliers)

                favorite_won = (home_score > away_score and home_ovr >= away_ovr) or (away_score > home_score and away_ovr >= home_ovr)
                u_score = upset_score(home_ovr, away_ovr, favorite_won)

                excitement_index_value = excitement_index(c_score, p_score, s_score, u_score)

                print(f"Calculated Excitement Index (E) = {excitement_index_value}")
                print(f"  [C]: {c_score:.1f}, [P]: {p_score:.1f}, [S]: {s_score:.1f}, [U]: {u_score:.1f}")
                
                cur.execute("UPDATE games SET excitement_index = %s WHERE id = %s", (excitement_index_value, game_id))
                return excitement_index_value

def batch_evaluate(season: Optional[int] = None, only_missing: bool = False, game_ids: Optional[List[int]] = None):
    """Score completed games and persist games.excitement_index.

    By default every completed game (optionally limited to one season) is re-scored. With
    only_missing=True, only games that have no excitement_index yet are scored, plus any
    explicitly listed game_ids (e.g. games whose box scores were just inserted or corrected).
    Already-scored games are otherwise left alone so their E doesn't drift as ratings change.
    """
    engine = GotWEngine()
    db = DatabaseHandler()
    ids = [int(g) for g in (game_ids or [])]
    with psycopg.connect(db.db_conn_info) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM games
                WHERE status = 'COMPLETED'
                  AND (%(season)s::int IS NULL OR season = %(season)s)
                  AND (NOT %(only_missing)s OR excitement_index IS NULL OR id = ANY(%(ids)s))
                ORDER BY id
            """, {"season": season, "only_missing": only_missing, "ids": ids})
            games = cur.fetchall()
            for g in games:
                engine.evaluate_game(g[0])
        conn.commit()
    print(f"✅ Evaluated {len(games)} completed game(s) and logged their Excitement Index.")
    return len(games)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Score the Game Excitement Index (E) for completed games.")
    parser.add_argument("--season", type=int, help="Only consider games from this season")
    parser.add_argument("--missing-only", action="store_true", help="Only score games without an excitement_index (plus --game-ids)")
    parser.add_argument("--game-ids", type=int, nargs="+", help="Specific game ids to (re)score, e.g. after a box-score correction")
    args = parser.parse_args()
    batch_evaluate(season=args.season, only_missing=args.missing_only or bool(args.game_ids), game_ids=args.game_ids)
