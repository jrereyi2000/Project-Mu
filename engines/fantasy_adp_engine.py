import psycopg
import pandas as pd
from db.db_handler import DatabaseHandler

def generate_preseason_adp(season: int = 2035):
    print(f"🏈 Generating Preseason ADP Board for {season}...")
    db = DatabaseHandler()
    
    with psycopg.connect(db.db_conn_info) as conn:
        # 1. Fetch Players and Team Context
        query = """
        SELECT 
            p.id AS player_id, p.player_name, p.full_name, ps.position, ps.overall_rating, ps.popularity_index,
            t.id AS team_id, t.name AS team_name, 
            ts.off_ovr, ts.pass_off_rank, ts.rush_off_rank
        FROM player_seasons ps
        JOIN players p ON ps.player_id = p.id
        JOIN teams t ON ps.team_id = t.id
        JOIN team_seasons ts ON t.id = ts.team_id AND ts.season = ps.season
        WHERE ps.season = %s AND ps.position IN ('QB', 'HB', 'WR', 'TE')
        """
        df = pd.read_sql(query, conn, params=(season,))
        
        # 2. Fetch Previous Year Awards
        awards_query = """
        SELECT award_type, player_name 
        FROM awards 
        WHERE season = %s AND award_type IN ('MVP', 'OPOY', 'OROY', 'SBMVP')
        """
        awards_df = pd.read_sql(awards_query, conn, params=(season - 1,))
        
    # Map awards to player names
    award_winners = {}
    for _, row in awards_df.iterrows():
        if row['player_name'] not in award_winners:
            award_winners[row['player_name']] = []
        award_winners[row['player_name']].append(row['award_type'])
        
    adp_list = []
    
    for _, row in df.iterrows():
        base_value = float(row['overall_rating'])
        
        # --- 1. POSITIONAL SCARCITY & ELITE QB PREMIUM ---
        if row['position'] == 'HB':
            base_value *= 1.35
        elif row['position'] == 'WR':
            base_value *= 1.30
        elif row['position'] == 'TE':
            base_value *= 1.15
        elif row['position'] == 'QB':
            if row['overall_rating'] >= 98:
                base_value *= 1.10  # Transcendent Tier
            elif row['overall_rating'] >= 90:
                base_value *= 1.00  # Franchise Tier
            else:
                base_value *= 0.90  # Streamer Tier
                
        # --- 2. SITUATIONAL ECOSYSTEM BUMP ---
        if row['position'] in ['WR', 'TE', 'QB'] and row['pass_off_rank'] and row['pass_off_rank'] <= 10:
            base_value += (11 - row['pass_off_rank']) * 0.5
        
        if row['position'] == 'HB' and row['rush_off_rank'] and row['rush_off_rank'] <= 10:
            base_value += (11 - row['rush_off_rank']) * 0.5
            
        # --- 3. PRESTIGE BUMP (Previous Year Awards) ---
        player_awards = award_winners.get(row['full_name'], [])
        if 'MVP' in player_awards:
            base_value += 8.0
        if 'OPOY' in player_awards:
            base_value += 6.0
        if 'SBMVP' in player_awards:
            base_value += 4.0
        if 'OROY' in player_awards:
            base_value += 5.0 # High breakout hype!
            
        # --- 4. CULTURAL RELEVANCE (POPULARITY TIEBREAKER) ---
        pop_idx = float(row.get('popularity_index') or 50.0)
        # Scales 0-100 popularity into a +0 to +3.0 point draft bump
        pop_bonus = (pop_idx / 100.0) * 3.0
        base_value += pop_bonus
        
        # --- 5. ROSTER CLOGGING PENALTY ---
        if row['overall_rating'] < 80:
            base_value *= 0.7
            
        adp_list.append({
            "player_id": row['player_id'],
            "player_name": row['player_name'],
            "full_name": row['full_name'],
            "team_id": row['team_id'],
            "position": row['position'],
            "overall_rating": row['overall_rating'],
            "draft_score": round(base_value, 2)
        })
        
    # Rank them
    final_df = pd.DataFrame(adp_list).sort_values(by="draft_score", ascending=False).reset_index(drop=True)
    final_df['adp_rank'] = final_df.index + 1
    
    print("\n🏆 GENERATED 2035 PRESEASON ADP (TOP 25)")
    print("="*70)
    print(final_df[['adp_rank', 'full_name', 'position', 'overall_rating', 'draft_score']].head(20).to_string(index=False))
    
    # Save to Database for Week 1
    print("\n💾 Saving Preseason ADP to fantasy_projections (Week 1)...")
    with psycopg.connect(db.db_conn_info) as conn:
        with conn.cursor() as cur:
            for _, row in final_df.head(300).iterrows():
                cur.execute("""
                    INSERT INTO fantasy_projections (season, week, player_name, team_id, adp_rank, position)
                    VALUES (%s, 1, %s, %s, %s, %s)
                    ON CONFLICT (season, week, player_name, team_id) DO UPDATE SET
                        adp_rank = EXCLUDED.adp_rank,
                        position = EXCLUDED.position;
                """, (season, row['player_name'], row['team_id'], row['adp_rank'], row['position']))
        conn.commit()
    print("✅ Successfully locked in Preseason Draft Board!")

if __name__ == "__main__":
    generate_preseason_adp()
