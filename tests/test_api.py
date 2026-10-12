import psycopg
from psycopg.rows import dict_row

CONN_INFO = "host=localhost dbname=madden user=postgres password=postgres"

def get_player(player_id, season):
    with psycopg.connect(CONN_INFO, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT p.id, p.full_name as name, p.player_number, p.height, p.weight, 
                       ps.position, ps.years_of_experience, ps.overall_rating,
                       t.id as team_id, t.name as team_name, t.abbreviation as team_abbr
                FROM players p
                JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                JOIN teams t ON t.id = ps.team_id
                WHERE p.id = %s
            """, (season, player_id))
            player = cur.fetchone()

            if not player:
                return {"error": "Player not found"}

            # game_log
            cur.execute("""
                SELECT g.week, g.status, g.home_team_id, g.away_team_id, g.home_score, g.away_score,
                       t_home.name as home_name, t_home.abbreviation as home_abbr,
                       t_away.name as away_name, t_away.abbreviation as away_abbr,
                       st.completions, st.pass_attempts, st.pass_yards, st.pass_tds, st.interceptions,
                       st.rush_attempts, st.rush_yards, st.rush_tds,
                       st.receptions, st.rec_yards, st.rec_tds, st.fumb
                FROM games g
                LEFT JOIN player_stats st ON st.game_id = g.id AND (st.player_name = %s OR st.player_name = %s) AND st.team_id = %s
                JOIN teams t_home ON t_home.id = g.home_team_id
                JOIN teams t_away ON t_away.id = g.away_team_id
                WHERE g.season = %s AND (g.home_team_id = %s OR g.away_team_id = %s)
                ORDER BY g.week
            """, (player['name'], player['name'].split(" ")[-1], player['team_id'], season, player['team_id'], player['team_id']))
            games = cur.fetchall()

            # career_stats
            cur.execute("""
                SELECT ps.season, t.name as team_name, t.abbreviation as team_abbr,
                       COUNT(st.id) as gp,
                       SUM(st.completions) as completions, SUM(st.pass_attempts) as pass_attempts,
                       SUM(st.pass_yards) as pass_yards, SUM(st.pass_tds) as pass_tds, SUM(st.interceptions) as interceptions,
                       SUM(st.rush_attempts) as rush_attempts, SUM(st.rush_yards) as rush_yards, SUM(st.rush_tds) as rush_tds,
                       SUM(st.receptions) as receptions, SUM(st.rec_yards) as rec_yards, SUM(st.rec_tds) as rec_tds
                FROM player_seasons ps
                JOIN teams t ON t.id = ps.team_id
                LEFT JOIN games g ON g.season = ps.season AND (g.home_team_id = ps.team_id OR g.away_team_id = ps.team_id)
                LEFT JOIN player_stats st ON st.game_id = g.id AND (st.player_name = %s OR st.player_name = %s) AND st.team_id = ps.team_id
                WHERE ps.player_id = %s
                GROUP BY ps.season, t.name, t.abbreviation
                ORDER BY ps.season DESC
            """, (player['name'], player['name'].split(" ")[-1], player_id))
            career = cur.fetchall()

            return {"player": player, "game_log": games, "career_stats": career}

print(get_player(100, 2035)['player'])
