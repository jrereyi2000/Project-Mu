import pandas as pd
import numpy as np
from sqlalchemy import create_engine

# Database Connection
DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/madden"
engine = create_engine(DATABASE_URL)

def generate_team_and_player_stats(season_year: int):
    # 1. Fetch Input Data
    games_df = pd.read_sql(f"SELECT * FROM games WHERE season = {season_year}", engine)
    existing_player_stats = pd.read_sql("SELECT * FROM player_stats", engine)
    
    season_p_df = pd.read_sql(f"""
        SELECT player_name, team_id, position,
               SUM(completions) as completions, SUM(pass_attempts) as pass_attempts,
               SUM(pass_yards) as pass_yards, SUM(pass_tds) as pass_tds,
               SUM(interceptions) as interceptions, SUM(rush_attempts) as rush_attempts,
               SUM(rush_yards) as rush_yards, SUM(rush_tds) as rush_tds,
               SUM(receptions) as receptions, SUM(rec_yards) as rec_yards,
               SUM(rec_tds) as rec_tds, SUM(fumb) as fumb
        FROM season_stats
        WHERE season = {season_year}
        GROUP BY player_name, team_id, position
    """, engine)

    team_stats_rows = []
    player_stats_rows = []

    teams = season_p_df['team_id'].unique()

    for team_id in teams:
        team_games = games_df[(games_df['home_team_id'] == team_id) | (games_df['away_team_id'] == team_id)].sort_values('week')
        
        if not existing_player_stats.empty:
            real_games_for_team = existing_player_stats[existing_player_stats['team_id'] == team_id]['game_id'].unique()
            team_games = team_games[~team_games['id'].isin(real_games_for_team)]
            
        num_games = len(team_games)
        if num_games == 0:
            continue

        team_players = season_p_df[season_p_df['team_id'] == team_id]

        metrics = ['completions', 'pass_attempts', 'pass_yards', 'pass_tds', 'interceptions',
                   'rush_attempts', 'rush_yards', 'rush_tds', 'receptions', 'rec_yards', 'rec_tds', 'fumb']
        
        game_weights = []
        for _, game in team_games.iterrows():
            is_home = (game['home_team_id'] == team_id)
            pts_scored = game['home_score'] if is_home else game['away_score']
            pts_allowed = game['away_score'] if is_home else game['home_score']
            w = max(1.0, (pts_scored + pts_allowed) / 40.0)
            game_weights.append(w)
            
        if not game_weights: continue
        weight_sum = sum(game_weights)
        
        players_list = list(zip(team_players['player_name'], team_players['position']))
        player_game_stats = {}
        
        # 1. Distribute stats for each player proportionally
        for _, p_row in team_players.iterrows():
            p_tup = (p_row['player_name'], p_row['position'])
            
            allocs = {m: [] for m in metrics}
            for m in metrics:
                target = int(p_row[m]) if pd.notnull(p_row[m]) else 0
                remaining_target = target
                remaining_weight = weight_sum
                
                for w in game_weights:
                    if remaining_weight <= 0.001:
                        val = remaining_target
                    else:
                        val = int(round(remaining_target * (w / remaining_weight)))
                    val = max(0, min(val, remaining_target))
                    allocs[m].append(val)
                    remaining_target -= val
                    remaining_weight -= w
                    
                if remaining_target > 0:
                    best_idx = game_weights.index(max(game_weights))
                    allocs[m][best_idx] += remaining_target
                    
            for idx, (_, game) in enumerate(team_games.iterrows()):
                g_id = game['id']
                player_game_stats[(p_tup, g_id)] = {m: allocs[m][idx] for m in metrics}
                
        # 2. Append to player_stats_rows
        for idx, (_, game) in enumerate(team_games.iterrows()):
            g_id = game['id']
            for p_tup in players_list:
                p_stats = player_game_stats[(p_tup, g_id)]
                player_stats_rows.append({
                    'game_id': g_id,
                    'player_name': p_tup[0],
                    'team_id': team_id,
                    'position': p_tup[1],
                    **p_stats
                })
                
        # 3. Aggregate into team_stats_rows
        team_name = pd.read_sql(f"SELECT name FROM teams WHERE id = {team_id}", engine).iloc[0]['name']
        for idx, (_, game) in enumerate(team_games.iterrows()):
            g_id = game['id']
            is_home = (game['home_team_id'] == team_id)
            pts_scored = game['home_score'] if is_home else game['away_score']
            
            t_stats = {m: sum(player_game_stats[(p_tup, g_id)][m] for p_tup in players_list) for m in metrics}
            tot_yds = t_stats['pass_yards'] + t_stats['rush_yards']
            team_to = t_stats['interceptions'] + t_stats['fumb']
            
            team_stats_rows.append({
                'game_id': g_id,
                'team_name': team_name,
                'score': pts_scored,
                'total_offense': tot_yds,
                'passing_yards': t_stats['pass_yards'],
                'rushing_yards': t_stats['rush_yards'],
                'turnovers': team_to,
                'passing_completions': t_stats['completions'],
                'passing_attempts': t_stats['pass_attempts'],
                'passing_tds': t_stats['pass_tds'],
                'rushing_attempts': t_stats['rush_attempts'],
                'rushing_tds': t_stats['rush_tds'],
                'first_downs': 0,
                'total_plays': 0,
                'third_down_attempts': 0,
                'third_down_conversions': 0,
                'fourth_down_attempts': 0,
                'fourth_down_conversions': 0
            })

    import psycopg
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            if team_stats_rows:
                ts_cols = list(team_stats_rows[0].keys())
                placeholders = ', '.join(['%s'] * len(ts_cols))
                ts_query = f"INSERT INTO team_stats ({','.join(ts_cols)}) VALUES ({placeholders}) ON CONFLICT (game_id, team_name) DO NOTHING"
                ts_vals = [[row[c] for c in ts_cols] for row in team_stats_rows]
                cur.executemany(ts_query, ts_vals)
                
            if player_stats_rows:
                ps_cols = list(player_stats_rows[0].keys())
                placeholders = ', '.join(['%s'] * len(ps_cols))
                ps_query = f"INSERT INTO player_stats ({','.join(ps_cols)}) VALUES ({placeholders}) ON CONFLICT (game_id, player_name, position) DO NOTHING"
                ps_vals = [[row[c] for c in ps_cols] for row in player_stats_rows]
                cur.executemany(ps_query, ps_vals)
                print("Found T. Davis rows:", len([row for row in player_stats_rows if row["player_name"] == "T. Davis"]))
        conn.commit()
    print(f"Database sync complete: Tried to insert {len(team_stats_rows)} team rows and {len(player_stats_rows)} player rows.")

if __name__ == "__main__":
    generate_team_and_player_stats(season_year=2035)