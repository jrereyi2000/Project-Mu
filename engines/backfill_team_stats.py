import os

import psycopg

def backfill_missing_team_stats():
    with psycopg.connect(os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT DISTINCT ps.game_id, ps.team_id, t.name
                FROM player_stats ps
                JOIN teams t ON ps.team_id = t.id
                WHERE NOT EXISTS (
                    SELECT 1 FROM team_stats ts 
                    WHERE ts.game_id = ps.game_id AND ts.team_name = t.name
                )
            """)
            missing_teams = cur.fetchall()
            
            if not missing_teams:
                print("No missing team_stats found! Everything is up to date.")
                return
                
            print(f"Found {len(missing_teams)} games missing team_stats. Generating from player_stats...")
            
            for game_id, team_id, team_name in missing_teams:
                cur.execute("""
                    SELECT 
                        SUM(pass_yards), SUM(rush_yards),
                        SUM(interceptions + fumb),
                        SUM(completions), SUM(pass_attempts), SUM(pass_tds),
                        SUM(rush_attempts), SUM(rush_tds)
                    FROM player_stats
                    WHERE game_id = %s AND team_id = %s
                """, (game_id, team_id))
                
                stats = cur.fetchone()
                if not stats or stats[0] is None:
                    continue
                    
                pass_yards, rush_yards, turnovers, completions, pass_attempts, pass_tds, rush_attempts, rush_tds = stats
                
                tot_yds = (pass_yards or 0) + (rush_yards or 0)
                turnovers = turnovers or 0
                completions = completions or 0
                pass_attempts = pass_attempts or 0
                pass_tds = pass_tds or 0
                rush_attempts = rush_attempts or 0
                rush_tds = rush_tds or 0
                
                cur.execute("SELECT home_team_id, away_team_id, home_score, away_score FROM games WHERE id = %s", (game_id,))
                g_row = cur.fetchone()
                score = 0
                if g_row:
                    if g_row[0] == team_id: score = g_row[2] or 0
                    elif g_row[1] == team_id: score = g_row[3] or 0
                
                print(f"Generating Team Stats for {team_name} in Game {game_id} (Score: {score}, Total Offense: {tot_yds})")
                
                cur.execute("""
                    INSERT INTO team_stats (
                        game_id, team_name, score,
                        total_offense, passing_yards, rushing_yards, turnovers,
                        passing_completions, passing_attempts, passing_tds,
                        rushing_attempts, rushing_tds,
                        first_downs, total_plays, third_down_attempts, third_down_conversions, fourth_down_attempts, fourth_down_conversions
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        0, 0, 0, 0, 0, 0
                    ) ON CONFLICT (game_id, team_name) DO UPDATE SET
                        total_offense = GREATEST(team_stats.total_offense, EXCLUDED.total_offense),
                        passing_yards = GREATEST(team_stats.passing_yards, EXCLUDED.passing_yards),
                        rushing_yards = GREATEST(team_stats.rushing_yards, EXCLUDED.rushing_yards),
                        turnovers = GREATEST(team_stats.turnovers, EXCLUDED.turnovers);
                """, (
                    game_id, team_name, score,
                    tot_yds, pass_yards, rush_yards, turnovers,
                    completions, pass_attempts, pass_tds,
                    rush_attempts, rush_tds
                ))
        conn.commit()
    print("Done!")

if __name__ == "__main__":
    backfill_missing_team_stats()
