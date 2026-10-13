import psycopg
from typing import List, Optional, Tuple
from db.db_handler import DatabaseHandler

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
                # We need the top 3 players from both teams, and their box score stats!
                cur.execute("""
                    SELECT ps.overall_rating, 
                           pstat.pass_yards, pstat.pass_tds, 
                           pstat.rush_yards, pstat.rush_tds, 
                           pstat.rec_yards, pstat.rec_tds,
                           t.id as team_id
                    FROM player_stats pstat
                    JOIN players p ON pstat.player_name = p.player_name
                    JOIN player_seasons ps ON p.id = ps.player_id
                    JOIN teams t ON pstat.team_id = t.id
                    WHERE pstat.game_id = %s
                """, (game_id,))
                
                players = cur.fetchall()
                
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
                
                # C (Competitiveness) - 35%
                margin = abs(home_score - away_score)
                c_score = max(0, 100 - (4 * margin))
                
                # Add OT Bonus if the game went to OT (assuming we detect this, let's say status == 'COMPLETED_OT' or just score logic)
                # Without OT flag, we just cap it. Let's cap at 100.
                c_score = min(100, c_score)
                
                # P (Production & Pace) - 25%
                total_points = home_score + away_score
                p_pts = (total_points / 70.0) * 60
                p_yds = (combined_yards / 850.0) * 40
                p_score = min(100, p_pts + p_yds)
                
                # S (Star Impact) - 20%
                home_players = [p for p in players if p[7] == home_team_id]
                away_players = [p for p in players if p[7] == away_team_id]
                
                top3_home_ovr = sorted([p[0] for p in home_players], reverse=True)[:3]
                top3_away_ovr = sorted([p[0] for p in away_players], reverse=True)[:3]
                
                mean_top3_home = sum(top3_home_ovr)/3 if top3_home_ovr else 80
                mean_top3_away = sum(top3_away_ovr)/3 if top3_away_ovr else 80
                s_stars = (mean_top3_home + mean_top3_away) / 2
                
                s_outliers = 0
                for p in players:
                    ovr, py, ptd, ry, rtd, recy, rectd, tid = p
                    if py and py >= 400: s_outliers += 15
                    if ptd and ptd >= 4: s_outliers += 15
                    if (ry or 0) + (recy or 0) >= 150: s_outliers += 12
                    if (rtd or 0) + (rectd or 0) >= 2: s_outliers += 8
                
                s_outliers = min(100, s_outliers)
                s_score = (0.5 * s_stars) + (0.5 * s_outliers)
                
                # U (Upset Narrative) - 20%
                favorite_ovr = max(home_ovr, away_ovr)
                underdog_ovr = min(home_ovr, away_ovr)
                delta_ovr = favorite_ovr - underdog_ovr
                
                favorite_won = (home_score > away_score and home_ovr >= away_ovr) or (away_score > home_score and away_ovr >= home_ovr)
                is_heavyweight = home_ovr >= 84 and away_ovr >= 84
                
                u_score = 0
                if is_heavyweight:
                    u_score = max(80, u_score)
                    
                if not favorite_won:
                    # Underdog win!
                    u_score = max(u_score, min(100, 50 + (5 * delta_ovr)))
                else:
                    # Chalk victory
                    u_score = max(u_score, max(10, 50 - (3 * delta_ovr)))
                    
                # FINAL INDEX E
                excitement_index = (0.35 * c_score) + (0.25 * p_score) + (0.20 * s_score) + (0.20 * u_score)
                excitement_index = round(excitement_index, 2)
                
                print(f"Calculated Excitement Index (E) = {excitement_index}")
                print(f"  [C]: {c_score:.1f}, [P]: {p_score:.1f}, [S]: {s_score:.1f}, [U]: {u_score:.1f}")
                
                cur.execute("UPDATE games SET excitement_index = %s WHERE id = %s", (excitement_index, game_id))
                return excitement_index

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
