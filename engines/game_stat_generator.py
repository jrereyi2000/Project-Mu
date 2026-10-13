import pandas as pd
import numpy as np
from sqlalchemy import create_engine

# Database Connection
DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/madden"
engine = create_engine(DATABASE_URL)

def generate_team_and_player_stats(season_year: int):
    # 1. Fetch Input Data
    games_df = pd.read_sql(f"SELECT * FROM games WHERE season = {season_year} AND status = 'COMPLETED'", engine)
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
                
                # If this player already has real game stats in the DB, deduct them so we only distribute the remainder!
                if not existing_player_stats.empty:
                    # Filter to this specific player
                    p_existing = existing_player_stats[
                        (existing_player_stats['player_name'] == p_tup[0]) & 
                        (existing_player_stats['team_id'] == team_id)
                    ]
                    if not p_existing.empty:
                        target -= int(p_existing[m].sum())
                        
                remaining_target = max(0, target)
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
        conn.commit()
    print(f"Database sync complete: Tried to insert {len(team_stats_rows)} team rows and {len(player_stats_rows)} player rows.")
    
    # --- 🏈 PHASE 2: TRIGGER THE AUTOMATED AI SWARM ---
    print("\n[SWARM PIPELINE] Triggering Post-Week Analytics & Swarm...")
    try:
        from engines.gotw_engine import batch_evaluate
        from engines.twi_engine import TWIEngine
        from engines.popularity_engine import PopularityEngine
        from engines.player_popularity_engine import PlayerPopularityEngine
        from newsroom.swarm_dispatcher import SwarmDispatcher, build_weekly_assignment_brief

        # 0. Score Game Excitement (E) for games that have none yet plus the games just backfilled above.
        #    Must run before TWI, which only counts games whose excitement_index is set.
        backfilled_game_ids = sorted({int(r['game_id']) for r in player_stats_rows + team_stats_rows})
        print("-> Running Game Excitement Engine (E)...")
        batch_evaluate(season=season_year, only_missing=True, game_ids=backfilled_game_ids)

        # 1. Run Mathematical Culture Indices in dependency order:
        #    E -> TWI (watchability_index) -> Franchise POP (reads TWI) -> Player POP (reads Franchise POP)
        print("-> Running Team Watchability Engine...")
        TWIEngine().backfill_twi(season=season_year)

        print("-> Running Franchise Popularity Engine...")
        PopularityEngine().calculate_popularity(season=season_year)

        print("-> Running Player Popularity Engine...")
        PlayerPopularityEngine().calculate_popularity(season=season_year)
        
        # 2. Compile the Assignment Brief for the AI
        print("-> Compiling AI Assignment Brief (Weekly Slate Digest)...")
        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                # Find the current week dynamically
                cur.execute("SELECT MAX(week) FROM games WHERE season = %s AND status = 'COMPLETED'", (season_year,))
                current_week = cur.fetchone()[0] or 1

        brief = build_weekly_assignment_brief(DATABASE_URL, season=season_year, week=current_week)
        
        # 3. Wake the Swarm (Production mode saves to DB with concurrency)
        print("-> Dispatching the National AI Swarm (Parallel Workers)...")
        SwarmDispatcher(debug_mode=False).dispatch_weekly_national(
            brief, season=season_year, week=current_week, live=True, save_to_db=True, max_workers=5
        )
        print("\n✅ ALL PIPELINES COMPLETE. Content generated.")
        
    except Exception as e:
        import traceback
        print(f"\n❌ SWARM TRIGGER FAILED: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    # We can pass run_swarm=True when calling it, or just let it run by default if executed directly.
    generate_team_and_player_stats(season_year=2035)