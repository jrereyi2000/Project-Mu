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


def _distribute_metric(target: int | float, weights: list[float], is_half_step: bool = False) -> list[int | float]:
    """Distribute a numeric target across games according to game weights.
    Ensures exact conservation: sum(allocations) == target.
    If is_half_step is True (used for defensive sacks), distributes in 0.5 increments."""
    if not weights or target <= 0:
        return [0.0 if is_half_step else 0] * len(weights)
    
    scale = 2 if is_half_step else 1
    int_target = int(round(target * scale))
    
    weight_sum = sum(weights)
    if weight_sum <= 0.001:
        weight_sum = float(len(weights))
        weights = [1.0] * len(weights)
        
    remaining_target = int_target
    remaining_weight = weight_sum
    allocs = []
    for w in weights:
        if remaining_weight <= 0.001:
            val = remaining_target
        else:
            val = int(round(remaining_target * (w / remaining_weight)))
        val = max(0, min(val, remaining_target))
        allocs.append(val)
        remaining_target -= val
        remaining_weight -= w
        
    if remaining_target > 0:
        best_idx = weights.index(max(weights))
        allocs[best_idx] += remaining_target
        
    if is_half_step:
        return [round(v / 2.0, 1) for v in allocs]
    return allocs


def generate_defense_and_special_teams_stats(season_year: int, games_df: pd.DataFrame):
    """Backfills game_stat_{kicking,defense,punting,blocking,puntreturn,kickreturn}
    from their respective season_stat_* tables for completed games missing game lines.
    In weekly runs with 1 new game per team, the game line equals that week's exact change in season totals."""
    import psycopg

    if games_df.empty:
        return

    # 1. KICKING
    try:
        season_k_df = pd.read_sql(f"SELECT * FROM season_stat_kicking WHERE season = {season_year}", engine)
        if not season_k_df.empty:
            existing_k = pd.read_sql(f"""
                SELECT gs.* FROM game_stat_kicking gs JOIN games g ON g.id = gs.game_id
                WHERE g.season = {season_year} AND COALESCE(g.game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
            """, engine)
            k_metrics = ['fgm_29', 'fga_29', 'fgm_39', 'fga_39', 'fgm_49', 'fga_49', 'fgm_50', 'fga_50',
                         'fg_blocked', 'xpm', 'xpa', 'xp_blocked', 'kickoffs', 'touchbacks']
            k_rows = []
            for team_id in season_k_df['team_id'].unique():
                if pd.isnull(team_id): continue
                team_id = int(team_id)
                team_games = games_df[(games_df['home_team_id'] == team_id) | (games_df['away_team_id'] == team_id)].sort_values('week')
                if not existing_k.empty:
                    recorded_games = existing_k[existing_k['team_id'] == team_id]['game_id'].unique()
                    team_games = team_games[~team_games['id'].isin(recorded_games)]
                if team_games.empty: continue

                game_weights = []
                for _, g in team_games.iterrows():
                    is_home = (g['home_team_id'] == team_id)
                    pts = g['home_score'] if is_home else g['away_score']
                    game_weights.append(max(1.0, pts / 7.0))

                for _, p_row in season_k_df[season_k_df['team_id'] == team_id].iterrows():
                    pid = int(p_row['player_id'])
                    p_existing = existing_k[existing_k['player_id'] == pid] if not existing_k.empty else pd.DataFrame()
                    allocs = {}
                    for m in k_metrics:
                        target = int(p_row[m]) if pd.notnull(p_row[m]) else 0
                        if not p_existing.empty and m in p_existing.columns:
                            target -= int(p_existing[m].fillna(0).sum())
                        allocs[m] = _distribute_metric(max(0, target), game_weights)

                    for idx, (_, g) in enumerate(team_games.iterrows()):
                        fgm = allocs['fgm_29'][idx] + allocs['fgm_39'][idx] + allocs['fgm_49'][idx] + allocs['fgm_50'][idx]
                        fga = max(fgm, allocs['fga_29'][idx] + allocs['fga_39'][idx] + allocs['fga_49'][idx] + allocs['fga_50'][idx])
                        xpm = allocs['xpm'][idx]
                        xpa = max(xpm, allocs['xpa'][idx])
                        fg_pct = round((fgm / fga * 100.0), 1) if fga > 0 else 0.0
                        xp_pct = round((xpm / xpa * 100.0), 1) if xpa > 0 else 0.0

                        s_long = int(p_row['fg_long']) if pd.notnull(p_row.get('fg_long')) else 50
                        if allocs['fgm_50'][idx] > 0: fg_long = min(s_long, 55)
                        elif allocs['fgm_49'][idx] > 0: fg_long = min(s_long, 47)
                        elif allocs['fgm_39'][idx] > 0: fg_long = min(s_long, 37)
                        elif allocs['fgm_29'][idx] > 0: fg_long = min(s_long, 27)
                        elif fgm > 0: fg_long = min(s_long, 30)
                        else: fg_long = 0

                        k_rows.append({
                            'game_id': int(g['id']),
                            'player_id': pid,
                            'team_id': team_id,
                            'position': p_row['position'] or 'K',
                            'fgm': fgm, 'fga': fga, 'fg_pct': fg_pct, 'fg_long': fg_long,
                            'fg_blocked': allocs['fg_blocked'][idx],
                            'xpm': xpm, 'xpa': xpa, 'xp_pct': xp_pct, 'xp_blocked': allocs['xp_blocked'][idx],
                            'fgm_29': allocs['fgm_29'][idx], 'fga_29': allocs['fga_29'][idx],
                            'fgm_39': allocs['fgm_39'][idx], 'fga_39': allocs['fga_39'][idx],
                            'fgm_49': allocs['fgm_49'][idx], 'fga_49': allocs['fga_49'][idx],
                            'fgm_50': allocs['fgm_50'][idx], 'fga_50': allocs['fga_50'][idx],
                            'kickoffs': allocs['kickoffs'][idx], 'touchbacks': allocs['touchbacks'][idx]
                        })

            if k_rows:
                with psycopg.connect(DATABASE_URL) as conn:
                    with conn.cursor() as cur:
                        cols = list(k_rows[0].keys())
                        placeholders = ', '.join(['%s'] * len(cols))
                        query = f"INSERT INTO game_stat_kicking ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT (game_id, player_id) DO NOTHING"
                        cur.executemany(query, [[r[c] for c in cols] for r in k_rows])
                    conn.commit()
                print(f"  [Kicking] Backfilled {len(k_rows)} game lines.")
    except Exception as e:
        print(f"  [WARN] Backfilling kicking failed: {e}")

    # 2. DEFENSE
    try:
        season_def_df = pd.read_sql(f"SELECT * FROM season_stat_defense WHERE season = {season_year}", engine)
        if not season_def_df.empty:
            existing_def = pd.read_sql(f"""
                SELECT gs.* FROM game_stat_defense gs JOIN games g ON g.id = gs.game_id
                WHERE g.season = {season_year} AND COALESCE(g.game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
            """, engine)
            def_metrics = ['solo', 'assists', 'tfl', 'sacks', 'ints', 'int_yds', 'deflections',
                           'forced_fumbles', 'fumble_recoveries', 'fumble_yds', 'blocks', 'safeties', 'tds']
            def_rows = []
            for team_id in season_def_df['team_id'].unique():
                if pd.isnull(team_id): continue
                team_id = int(team_id)
                team_games = games_df[(games_df['home_team_id'] == team_id) | (games_df['away_team_id'] == team_id)].sort_values('week')
                if not existing_def.empty:
                    recorded_games = existing_def[existing_def['team_id'] == team_id]['game_id'].unique()
                    team_games = team_games[~team_games['id'].isin(recorded_games)]
                if team_games.empty: continue

                game_weights = []
                for _, g in team_games.iterrows():
                    is_home = (g['home_team_id'] == team_id)
                    pts_allowed = g['away_score'] if is_home else g['home_score']
                    game_weights.append(max(1.0, (50.0 - pts_allowed) / 10.0))

                for _, p_row in season_def_df[season_def_df['team_id'] == team_id].iterrows():
                    pid = int(p_row['player_id'])
                    p_existing = existing_def[existing_def['player_id'] == pid] if not existing_def.empty else pd.DataFrame()
                    allocs = {}
                    for m in def_metrics:
                        if m == 'sacks':
                            target = float(p_row[m]) if pd.notnull(p_row[m]) else 0.0
                            if not p_existing.empty and m in p_existing.columns:
                                target -= float(p_existing[m].fillna(0).sum())
                            allocs[m] = _distribute_metric(max(0.0, target), game_weights, is_half_step=True)
                        else:
                            target = int(p_row[m]) if pd.notnull(p_row[m]) else 0
                            if not p_existing.empty and m in p_existing.columns:
                                target -= int(p_existing[m].fillna(0).sum())
                            allocs[m] = _distribute_metric(max(0, target), game_weights)

                    for idx, (_, g) in enumerate(team_games.iterrows()):
                        solo = int(allocs['solo'][idx])
                        assists = int(allocs['assists'][idx])
                        tackles = solo + assists
                        sacks = float(allocs['sacks'][idx])
                        ints = int(allocs['ints'][idx])
                        int_yds = int(allocs['int_yds'][idx])
                        int_avg = round((int_yds / ints), 1) if ints > 0 else 0.0
                        int_long = int_yds if ints > 0 else 0

                        def_rows.append({
                            'game_id': int(g['id']),
                            'player_id': pid,
                            'team_id': team_id,
                            'position': p_row['position'] or 'DEF',
                            'solo': solo, 'assists': assists, 'tackles': tackles,
                            'tfl': int(allocs['tfl'][idx]),
                            'sacks': sacks,
                            'ints': ints, 'int_yds': int_yds, 'int_avg': int_avg, 'int_long': int_long,
                            'deflections': int(allocs['deflections'][idx]),
                            'forced_fumbles': int(allocs['forced_fumbles'][idx]),
                            'fumble_recoveries': int(allocs['fumble_recoveries'][idx]),
                            'fumble_yds': int(allocs['fumble_yds'][idx]),
                            'blocks': int(allocs['blocks'][idx]),
                            'safeties': int(allocs['safeties'][idx]),
                            'tds': int(allocs['tds'][idx])
                        })

            if def_rows:
                with psycopg.connect(DATABASE_URL) as conn:
                    with conn.cursor() as cur:
                        cols = list(def_rows[0].keys())
                        placeholders = ', '.join(['%s'] * len(cols))
                        query = f"INSERT INTO game_stat_defense ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT (game_id, player_id) DO NOTHING"
                        cur.executemany(query, [[r[c] for c in cols] for r in def_rows])
                    conn.commit()
                print(f"  [Defense] Backfilled {len(def_rows)} game lines.")
    except Exception as e:
        print(f"  [WARN] Backfilling defense failed: {e}")

    # 3. PUNTING
    try:
        season_punt_df = pd.read_sql(f"SELECT * FROM season_stat_punting WHERE season = {season_year}", engine)
        if not season_punt_df.empty:
            existing_punt = pd.read_sql(f"""
                SELECT gs.* FROM game_stat_punting gs JOIN games g ON g.id = gs.game_id
                WHERE g.season = {season_year} AND COALESCE(g.game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
            """, engine)
            punt_metrics = ['punts', 'yards', 'net_yards', 'blocked', 'inside_20', 'touchbacks']
            punt_rows = []
            for team_id in season_punt_df['team_id'].unique():
                if pd.isnull(team_id): continue
                team_id = int(team_id)
                team_games = games_df[(games_df['home_team_id'] == team_id) | (games_df['away_team_id'] == team_id)].sort_values('week')
                if not existing_punt.empty:
                    recorded_games = existing_punt[existing_punt['team_id'] == team_id]['game_id'].unique()
                    team_games = team_games[~team_games['id'].isin(recorded_games)]
                if team_games.empty: continue

                game_weights = []
                for _, g in team_games.iterrows():
                    is_home = (g['home_team_id'] == team_id)
                    pts = g['home_score'] if is_home else g['away_score']
                    game_weights.append(max(1.0, (40.0 - pts) / 10.0))

                for _, p_row in season_punt_df[season_punt_df['team_id'] == team_id].iterrows():
                    pid = int(p_row['player_id'])
                    p_existing = existing_punt[existing_punt['player_id'] == pid] if not existing_punt.empty else pd.DataFrame()
                    allocs = {}
                    for m in punt_metrics:
                        target = int(p_row[m]) if pd.notnull(p_row[m]) else 0
                        if not p_existing.empty and m in p_existing.columns:
                            target -= int(p_existing[m].fillna(0).sum())
                        allocs[m] = _distribute_metric(max(0, target), game_weights)

                    for idx, (_, g) in enumerate(team_games.iterrows()):
                        punts = int(allocs['punts'][idx])
                        yards = int(allocs['yards'][idx])
                        net_yards = min(yards, int(allocs['net_yards'][idx]))
                        average = round((yards / punts), 1) if punts > 0 else 0.0
                        net_average = round((net_yards / punts), 1) if punts > 0 else 0.0
                        s_long = int(p_row['longest']) if pd.notnull(p_row.get('longest')) else 60
                        longest = min(s_long, int(round(average + 12))) if punts > 0 else 0

                        punt_rows.append({
                            'game_id': int(g['id']),
                            'player_id': pid,
                            'team_id': team_id,
                            'position': p_row['position'] or 'P',
                            'punts': punts, 'yards': yards, 'average': average,
                            'net_yards': net_yards, 'net_average': net_average,
                            'blocked': int(allocs['blocked'][idx]),
                            'inside_20': int(allocs['inside_20'][idx]),
                            'touchbacks': int(allocs['touchbacks'][idx]),
                            'longest': longest
                        })

            if punt_rows:
                with psycopg.connect(DATABASE_URL) as conn:
                    with conn.cursor() as cur:
                        cols = list(punt_rows[0].keys())
                        placeholders = ', '.join(['%s'] * len(cols))
                        query = f"INSERT INTO game_stat_punting ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT (game_id, player_id) DO NOTHING"
                        cur.executemany(query, [[r[c] for c in cols] for r in punt_rows])
                    conn.commit()
                print(f"  [Punting] Backfilled {len(punt_rows)} game lines.")
    except Exception as e:
        print(f"  [WARN] Backfilling punting failed: {e}")

    # 4. BLOCKING
    try:
        season_blk_df = pd.read_sql(f"SELECT * FROM season_stat_blocking WHERE season = {season_year}", engine)
        if not season_blk_df.empty:
            existing_blk = pd.read_sql(f"""
                SELECT gs.* FROM game_stat_blocking gs JOIN games g ON g.id = gs.game_id
                WHERE g.season = {season_year} AND COALESCE(g.game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
            """, engine)
            blk_rows = []
            for team_id in season_blk_df['team_id'].unique():
                if pd.isnull(team_id): continue
                team_id = int(team_id)
                team_games = games_df[(games_df['home_team_id'] == team_id) | (games_df['away_team_id'] == team_id)].sort_values('week')
                if not existing_blk.empty:
                    recorded_games = existing_blk[existing_blk['team_id'] == team_id]['game_id'].unique()
                    team_games = team_games[~team_games['id'].isin(recorded_games)]
                if team_games.empty: continue

                game_weights = [1.0] * len(team_games)
                for _, p_row in season_blk_df[season_blk_df['team_id'] == team_id].iterrows():
                    pid = int(p_row['player_id'])
                    p_existing = existing_blk[existing_blk['player_id'] == pid] if not existing_blk.empty else pd.DataFrame()
                    target = int(p_row['sacks_allowed']) if pd.notnull(p_row.get('sacks_allowed')) else 0
                    if not p_existing.empty and 'sacks_allowed' in p_existing.columns:
                        target -= int(p_existing['sacks_allowed'].fillna(0).sum())
                    alloc = _distribute_metric(max(0, target), game_weights)

                    for idx, (_, g) in enumerate(team_games.iterrows()):
                        blk_rows.append({
                            'game_id': int(g['id']),
                            'player_id': pid,
                            'team_id': team_id,
                            'position': p_row['position'] or 'OL',
                            'sacks_allowed': int(alloc[idx])
                        })

            if blk_rows:
                with psycopg.connect(DATABASE_URL) as conn:
                    with conn.cursor() as cur:
                        cols = list(blk_rows[0].keys())
                        placeholders = ', '.join(['%s'] * len(cols))
                        query = f"INSERT INTO game_stat_blocking ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT (game_id, player_id) DO NOTHING"
                        cur.executemany(query, [[r[c] for c in cols] for r in blk_rows])
                    conn.commit()
                print(f"  [Blocking] Backfilled {len(blk_rows)} game lines.")
    except Exception as e:
        print(f"  [WARN] Backfilling blocking failed: {e}")

    # 5. PUNT RETURN
    try:
        season_pr_df = pd.read_sql(f"SELECT * FROM season_stat_puntreturn WHERE season = {season_year}", engine)
        if not season_pr_df.empty:
            existing_pr = pd.read_sql(f"""
                SELECT gs.* FROM game_stat_puntreturn gs JOIN games g ON g.id = gs.game_id
                WHERE g.season = {season_year} AND COALESCE(g.game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
            """, engine)
            pr_metrics = ['returns', 'yards', 'tds']
            pr_rows = []
            for team_id in season_pr_df['team_id'].unique():
                if pd.isnull(team_id): continue
                team_id = int(team_id)
                team_games = games_df[(games_df['home_team_id'] == team_id) | (games_df['away_team_id'] == team_id)].sort_values('week')
                if not existing_pr.empty:
                    recorded_games = existing_pr[existing_pr['team_id'] == team_id]['game_id'].unique()
                    team_games = team_games[~team_games['id'].isin(recorded_games)]
                if team_games.empty: continue

                game_weights = []
                for _, g in team_games.iterrows():
                    is_home = (g['home_team_id'] == team_id)
                    pts_allowed = g['away_score'] if is_home else g['home_score']
                    game_weights.append(max(1.0, (40.0 - pts_allowed) / 10.0))

                for _, p_row in season_pr_df[season_pr_df['team_id'] == team_id].iterrows():
                    pid = int(p_row['player_id'])
                    p_existing = existing_pr[existing_pr['player_id'] == pid] if not existing_pr.empty else pd.DataFrame()
                    allocs = {}
                    for m in pr_metrics:
                        target = int(p_row[m]) if pd.notnull(p_row[m]) else 0
                        if not p_existing.empty and m in p_existing.columns:
                            target -= int(p_existing[m].fillna(0).sum())
                        allocs[m] = _distribute_metric(max(0, target), game_weights)

                    for idx, (_, g) in enumerate(team_games.iterrows()):
                        ret = int(allocs['returns'][idx])
                        yds = int(allocs['yards'][idx])
                        avg = round((yds / ret), 1) if ret > 0 else 0.0
                        s_long = int(p_row['longest']) if pd.notnull(p_row.get('longest')) else yds
                        longest = min(s_long, yds) if ret > 0 else 0

                        pr_rows.append({
                            'game_id': int(g['id']),
                            'player_id': pid,
                            'team_id': team_id,
                            'position': p_row['position'] or 'WR',
                            'returns': ret, 'yards': yds, 'average': avg,
                            'tds': int(allocs['tds'][idx]),
                            'longest': longest
                        })

            if pr_rows:
                with psycopg.connect(DATABASE_URL) as conn:
                    with conn.cursor() as cur:
                        cols = list(pr_rows[0].keys())
                        placeholders = ', '.join(['%s'] * len(cols))
                        query = f"INSERT INTO game_stat_puntreturn ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT (game_id, player_id) DO NOTHING"
                        cur.executemany(query, [[r[c] for c in cols] for r in pr_rows])
                    conn.commit()
                print(f"  [Punt Return] Backfilled {len(pr_rows)} game lines.")
    except Exception as e:
        print(f"  [WARN] Backfilling punt return failed: {e}")

    # 6. KICK RETURN
    try:
        season_kr_df = pd.read_sql(f"SELECT * FROM season_stat_kickreturn WHERE season = {season_year}", engine)
        if not season_kr_df.empty:
            existing_kr = pd.read_sql(f"""
                SELECT gs.* FROM game_stat_kickreturn gs JOIN games g ON g.id = gs.game_id
                WHERE g.season = {season_year} AND COALESCE(g.game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
            """, engine)
            kr_metrics = ['returns', 'yards', 'tds']
            kr_rows = []
            for team_id in season_kr_df['team_id'].unique():
                if pd.isnull(team_id): continue
                team_id = int(team_id)
                team_games = games_df[(games_df['home_team_id'] == team_id) | (games_df['away_team_id'] == team_id)].sort_values('week')
                if not existing_kr.empty:
                    recorded_games = existing_kr[existing_kr['team_id'] == team_id]['game_id'].unique()
                    team_games = team_games[~team_games['id'].isin(recorded_games)]
                if team_games.empty: continue

                game_weights = []
                for _, g in team_games.iterrows():
                    is_home = (g['home_team_id'] == team_id)
                    pts_allowed = g['away_score'] if is_home else g['home_score']
                    game_weights.append(max(1.0, pts_allowed / 7.0))

                for _, p_row in season_kr_df[season_kr_df['team_id'] == team_id].iterrows():
                    pid = int(p_row['player_id'])
                    p_existing = existing_kr[existing_kr['player_id'] == pid] if not existing_kr.empty else pd.DataFrame()
                    allocs = {}
                    for m in kr_metrics:
                        target = int(p_row[m]) if pd.notnull(p_row[m]) else 0
                        if not p_existing.empty and m in p_existing.columns:
                            target -= int(p_existing[m].fillna(0).sum())
                        allocs[m] = _distribute_metric(max(0, target), game_weights)

                    for idx, (_, g) in enumerate(team_games.iterrows()):
                        ret = int(allocs['returns'][idx])
                        yds = int(allocs['yards'][idx])
                        avg = round((yds / ret), 1) if ret > 0 else 0.0
                        s_long = int(p_row['longest']) if pd.notnull(p_row.get('longest')) else yds
                        longest = min(s_long, yds) if ret > 0 else 0

                        kr_rows.append({
                            'game_id': int(g['id']),
                            'player_id': pid,
                            'team_id': team_id,
                            'position': p_row['position'] or 'WR',
                            'returns': ret, 'yards': yds, 'average': avg,
                            'tds': int(allocs['tds'][idx]),
                            'longest': longest
                        })

            if kr_rows:
                with psycopg.connect(DATABASE_URL) as conn:
                    with conn.cursor() as cur:
                        cols = list(kr_rows[0].keys())
                        placeholders = ', '.join(['%s'] * len(cols))
                        query = f"INSERT INTO game_stat_kickreturn ({','.join(cols)}) VALUES ({placeholders}) ON CONFLICT (game_id, player_id) DO NOTHING"
                        cur.executemany(query, [[r[c] for c in cols] for r in kr_rows])
                    conn.commit()
                print(f"  [Kick Return] Backfilled {len(kr_rows)} game lines.")
    except Exception as e:
        print(f"  [WARN] Backfilling kick return failed: {e}")


def generate_team_and_player_stats(season_year: int | None = None, strict: bool = False, include_dst: bool = True):
    """Backfill the season's missing game lines, then run the analytics engines. strict=True (the weekly
    run, pipeline/weekly_capture.py) re-raises an engine failure so the week stops before any articles;
    otherwise the failure is printed and the run carries on."""
    if season_year is None:
        season_year = current_season()
    print(f"-> Backfill and engines for season {season_year}")
    # 1. Fetch Input Data
    # Regular season only: Season Stats stop at Week 18, so playoff games get lines only from a games capture
    games_df = pd.read_sql(f"SELECT * FROM games WHERE season = {season_year} AND status = 'COMPLETED' "
                           f"AND COALESCE(game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'", engine)
    # Real (or already backfilled) lines for this season's games only
    existing_player_stats = pd.read_sql(f"""
        SELECT ps.* FROM player_stats ps JOIN games g ON g.id = ps.game_id WHERE g.season = {season_year}
          AND COALESCE(g.game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
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
                    # One missing game: the line is that week's exact change in the season totals. Several: the
                    # remainder is spread by score, so the line is synthetic (player splits skip it).
                    'line_source': 'WEEKLY' if num_games == 1 else 'DISTRIBUTED',
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

    # 4. Defense and Special Teams: distribute season totals into game_stat_* for games missing game lines
    if include_dst:
        try:
            print("-> Generating defense and special-teams game stats...")
            generate_defense_and_special_teams_stats(season_year, games_df)
        except Exception as e:
            print(f"❌ Defense and special-teams game stats generation failed: {e}")
            if strict:
                raise

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
        from engines.coty_engine import CotyEngine
        from engines.tendency_engine import TendencyEngine
        from engines.playoff_clout_engine import PlayoffCloutEngine
        from engines.popularity_engine import PopularityEngine
        from engines.player_popularity_engine import PlayerPopularityEngine
        from engines.records_engine import RecordsEngine
        from engines.excitement_projection import project_scheduled_games
        from engines.fantasy_projection_engine import project_upcoming_week
        from engines.fantasy_league_sim import simulate_season

        # 0. Score Game Excitement (E) for games that have none yet plus the games just backfilled above.
        #    Must run before TWI, which only counts games whose excitement_index is set.
        backfilled_game_ids = sorted({int(r['game_id']) for r in player_stats_rows + team_stats_rows})
        print("-> Running Game Excitement Engine (E)...")
        batch_evaluate(season=season_year, only_missing=True, game_ids=backfilled_game_ids)

        # Game scripts (§2.14): each completed game's canonical story, after E (its drama follows E). Games whose
        # recorded facts haven't changed keep their script, so a published story stays the same story.
        print("-> Writing game scripts...")
        from engines.game_script_engine import run as write_game_scripts
        write_game_scripts(season_year, conn_info=DATABASE_URL)

        # 1. Run Mathematical Culture Indices in dependency order:
        #    E -> TWI (watchability_index) -> Franchise POP (reads TWI) -> Player POP (reads Franchise POP)
        print("-> Running Team Watchability Engine...")
        TWIEngine().backfill_twi(season=season_year)

        print("-> Running Opponent-Adjusted Game Credit Engine...")
        GameCreditEngine().calculate(season=season_year)

        print("-> Running Team Efficiency Engine...")
        TeamEfficiencyEngine().calculate(season=season_year)

        print("-> Running Observed Tendency Engine...")
        TendencyEngine().calculate(season=season_year)

        print("-> Running Coach of the Year Engine...")
        CotyEngine().calculate(season=season_year)

        print("-> Running Playoff Clout Engine...")
        PlayoffCloutEngine().calculate(season=season_year)

        print("-> Running Franchise Popularity Engine...")
        PopularityEngine().calculate_popularity(season=season_year)

        print("-> Running Player Popularity Engine...")
        PlayerPopularityEngine().calculate_popularity(season=season_year)

        # Update franchise records and NFL top-10 lists for the season
        print("-> Running Records Engine...")
        try:
            RecordsEngine().calculate(season=season_year)
        except Exception as e:   # the record book is optional: a missing migration must not stop the week's sync
            print(f"   [WARN] Records Engine skipped: {e}")

        # Project E for every still-scheduled game from the season so far (feeds the site, the brief and primer picks)
        print("-> Projecting Excitement Index for scheduled games...")
        project_scheduled_games(season_year)

        # Weekly fantasy projections (fantasy_projections) for the next week with scheduled games
        print("-> Projecting fantasy points for the upcoming week...")
        project_upcoming_week(season_year)

        # Simulated fantasy leagues (rostered %, start %, standings); reads the projections just written
        print("-> Simulating fantasy leagues through the upcoming week...")
        simulate_season(season_year)

        # Napper leagues: AI lineups (IR moves) and AI trades for the same upcoming week. Optional: a failure
        # here never fails the stats sync.
        print("-> Napper: AI lineups and trades for the upcoming week...")
        try:
            from engines.napper_weekly import run_napper_week
            run_napper_week(season_year)
        except Exception as e:
            print(f"   [WARN] Napper weekly step skipped: {e!r}")
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
    ap.add_argument("--no-dst", action="store_true", help="Skip defense and special teams game stat backfill")
    args = ap.parse_args()
    generate_team_and_player_stats(season_year=args.season, include_dst=not args.no_dst)