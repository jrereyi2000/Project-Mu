import os

import pandas as pd
import numpy as np
import psycopg2
from sqlalchemy import create_engine

# Database Connection: DATABASE_URL (a libpq URL or key=value string), else the local live database
DATABASE_URL = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
engine = create_engine("postgresql+psycopg2://", creator=lambda: psycopg2.connect(DATABASE_URL))

def current_season() -> int:
    """league_state.current_season: the season the backfill and the engines run for."""
    return int(pd.read_sql("SELECT current_season FROM league_state WHERE id = 1", engine).iloc[0, 0])


def generate_team_and_player_stats(season_year: int | None = None, strict: bool = False):
    """Backfill the season's missing game lines, then run the analytics engines. strict=True (the weekly
    run, pipeline/weekly_capture.py) re-raises an engine failure so the week stops before any articles;
    otherwise the failure is printed and the run carries on."""
    if season_year is None:
        season_year = current_season()
    print(f"-> Backfill and engines for season {season_year}")
    # 1. Fetch Input Data
    games_df = pd.read_sql(f"SELECT * FROM games WHERE season = {season_year} AND status = 'COMPLETED'", engine)
    # Real (or already backfilled) lines for this season's games only
    existing_player_stats = pd.read_sql(f"""
        SELECT ps.* FROM player_stats ps JOIN games g ON g.id = ps.game_id WHERE g.season = {season_year}
    """, engine)

    season_p_df = pd.read_sql(f"""
        SELECT player_id, player_name, team_id, position,
               SUM(completions) as completions, SUM(pass_attempts) as pass_attempts,
               SUM(pass_yards) as pass_yards, SUM(pass_tds) as pass_tds,
               SUM(interceptions) as interceptions, SUM(rush_attempts) as rush_attempts,
               SUM(rush_yards) as rush_yards, SUM(rush_tds) as rush_tds,
               SUM(receptions) as receptions, SUM(rec_yards) as rec_yards,
               SUM(rec_tds) as rec_tds, SUM(fumb) as fumb
        FROM season_stats
        WHERE season = {season_year}
        GROUP BY player_id, player_name, team_id, position
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
        player_ids = {(r['player_name'], r['position']): (int(r['player_id']) if pd.notnull(r['player_id']) else None)
                      for _, r in team_players.iterrows()}
        player_game_stats = {}
        
        # 1. Distribute stats for each player proportionally
        for _, p_row in team_players.iterrows():
            p_tup = (p_row['player_name'], p_row['position'])

            # The player's lines already stored this season (any team, so a traded player's old-team games
            # count), matched by player_id; teammates who share a list name (T. Davis WR / CB) stay apart.
            # A season row without a player_id falls back to name + team + position.
            if existing_player_stats.empty:
                p_existing = existing_player_stats
            elif player_ids[p_tup] is not None:
                p_existing = existing_player_stats[existing_player_stats['player_id'] == player_ids[p_tup]]
            else:
                p_existing = existing_player_stats[
                    (existing_player_stats['player_name'] == p_tup[0]) &
                    (existing_player_stats['team_id'] == team_id) &
                    (existing_player_stats['position'] == p_tup[1])
                ]

            allocs = {m: [] for m in metrics}
            for m in metrics:
                target = int(p_row[m]) if pd.notnull(p_row[m]) else 0
                
                # Deduct the lines already stored so only the remainder is distributed
                if not p_existing.empty:
                    target -= int(p_existing[m].fillna(0).sum())
                        
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
                    'player_id': player_ids[p_tup],
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
                # Unknown until engines/team_box_estimator.py splits the season totals (below)
                'third_down_attempts': None,
                'third_down_conversions': None,
                'fourth_down_attempts': None,
                'fourth_down_conversions': None
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
                ps_query = f"INSERT INTO player_stats ({','.join(ps_cols)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"  # either unique key: (game_id, player_name, position) or (game_id, player_id)
                ps_vals = [[row[c] for c in ps_cols] for row in player_stats_rows]
                cur.executemany(ps_query, ps_vals)
        conn.commit()
    print(f"Database sync complete: Tried to insert {len(team_stats_rows)} team rows and {len(player_stats_rows)} player rows.")

    # Per-game conversions, red zone and penalties: the season totals' remainder into every still-empty
    # team_stats cell (one new game per team in a weekly run, so exactly that week's change)
    try:
        from engines.team_box_estimator import run as estimate_team_box
        print("-> Estimating per-game team conversions, red zone and penalties...")
        estimate_team_box(season_year, conn_info=DATABASE_URL)
    except Exception as e:
        print(f"❌ Team box estimate failed: {e}")
        if strict:
            raise
    
    # --- 🏈 PHASE 2: POST-WEEK ANALYTICS ---
    # The newsroom swarm is not run here: it is a separate step (newsroom/weekly_run.py) that the
    # pipeline calls after this sync, so stats edits and repeat captures don't publish a new slate.
    print("\n[ANALYTICS PIPELINE] Running post-week analytics engines...")
    try:
        from engines.gotw_engine import batch_evaluate
        from engines.twi_engine import TWIEngine
        from engines.game_credit_engine import GameCreditEngine
        from engines.team_efficiency_engine import TeamEfficiencyEngine
        from engines.playoff_clout_engine import PlayoffCloutEngine
        from engines.popularity_engine import PopularityEngine
        from engines.player_popularity_engine import PlayerPopularityEngine
        from engines.excitement_projection import project_scheduled_games
        from engines.fantasy_projection_engine import project_upcoming_week
        from engines.fantasy_league_sim import simulate_season

        # 0. Score Game Excitement (E) for games that have none yet plus the games just backfilled above.
        #    Must run before TWI, which only counts games whose excitement_index is set.
        backfilled_game_ids = sorted({int(r['game_id']) for r in player_stats_rows + team_stats_rows})
        print("-> Running Game Excitement Engine (E)...")
        batch_evaluate(season=season_year, only_missing=True, game_ids=backfilled_game_ids)

        # 1. Run Mathematical Culture Indices in dependency order:
        #    E -> TWI (watchability_index) -> Franchise POP (reads TWI) -> Player POP (reads Franchise POP)
        print("-> Running Team Watchability Engine...")
        TWIEngine().backfill_twi(season=season_year)

        print("-> Running Opponent-Adjusted Game Credit Engine...")
        GameCreditEngine().calculate(season=season_year)

        print("-> Running Team Efficiency Engine...")
        TeamEfficiencyEngine().calculate(season=season_year)

        print("-> Running Playoff Clout Engine...")
        PlayoffCloutEngine().calculate(season=season_year)

        print("-> Running Franchise Popularity Engine...")
        PopularityEngine().calculate_popularity(season=season_year)

        print("-> Running Player Popularity Engine...")
        PlayerPopularityEngine().calculate_popularity(season=season_year)

        # Project E for every still-scheduled game from the season so far (feeds the site, the brief and primer picks)
        print("-> Projecting Excitement Index for scheduled games...")
        project_scheduled_games(season_year)

        # Weekly fantasy projections (fantasy_projections) for the next week with scheduled games
        print("-> Projecting fantasy points for the upcoming week...")
        project_upcoming_week(season_year)

        # Simulated fantasy leagues (rostered %, start %, standings); reads the projections just written
        print("-> Simulating fantasy leagues through the upcoming week...")
        simulate_season(season_year)
        print("\n✅ Stats sync and analytics engines complete.")

    except Exception as e:
        import traceback
        print(f"\n❌ ANALYTICS PIPELINE FAILED: {e}")
        traceback.print_exc()
        if strict:
            raise

if __name__ == "__main__":
    # Stats sync + analytics engines only. Publish the newsroom slate with: python3 -m newsroom.weekly_run
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, help="default: league_state.current_season")
    generate_team_and_player_stats(season_year=ap.parse_args().season)