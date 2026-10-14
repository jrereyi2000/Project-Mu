import psycopg
from db.db_handler import DatabaseHandler
from engines.market_constants import market, recent_hardware_bumps

class PopularityEngine:
    def __init__(self):
        self.db = DatabaseHandler()
        
    def calculate_popularity(self, season: int | None = None):
        if season is None:
            season = self.db.get_current_league_state()[0]
        print(f"--- Calculating Franchise Popularity Index (POP) for Season {season} ---")
        
        final_results = []
        
        with psycopg.connect(self.db.db_conn_info) as conn:
            with conn.cursor() as cur:
                # Fetch Current TWI and Win/Loss Record
                cur.execute("""
                    SELECT t.id, t.name, ts.watchability_index, ts.wins, ts.losses
                    FROM teams t
                    JOIN team_seasons ts ON t.id = ts.team_id
                    WHERE ts.season = %s
                """, (season,))
                
                teams = cur.fetchall()
                bumps = recent_hardware_bumps(cur, season)
                
                for tid, name, twi, wins, losses in teams:
                    if not twi: twi = 50.0
                    
                    # 1. Base Market Gravity (B)
                    model = market(name)
                    B = model["B"]
                    
                    # 2. Historical Equity (H)
                    S_all_time = model["S_all_time"]
                    
                    # Calculate S_recent (Trailing 5 years)
                    win_pct = (wins / (wins + losses)) if (wins + losses) > 0 else 0.500
                    S_recent_base = win_pct * 100
                    
                    # Apply Hardware Bumps
                    hardware_bump = bumps.get(tid, 0)
                    S_recent = min(100, S_recent_base + hardware_bump)
                    
                    # H Formula
                    H = (0.85 * S_recent) + (0.15 * S_all_time)
                    
                    # 3. Final POP Calculation
                    POP = (0.35 * B) + (0.30 * H) + (0.35 * twi)
                    POP = min(100.0, max(0.0, POP))
                    
                    final_results.append({
                        "team_id": tid,
                        "name": name,
                        "POP": round(POP, 2),
                        "B": B,
                        "H": round(H, 2),
                        "TWI": round(twi, 2)
                    })
                    
                    # Update Database
                    cur.execute("""
                        UPDATE team_seasons 
                        SET popularity_index = %s 
                        WHERE team_id = %s AND season = %s
                    """, (round(POP, 2), tid, season))
                    
                conn.commit()
                
        # Sort and Display
        final_results.sort(key=lambda x: x["POP"], reverse=True)
        print("\n📈 FINAL NFL FRANCHISE POPULARITY INDEX (POP)")
        print("============================================================")
        for i, r in enumerate(final_results):
            tier = "Apex National Draw" if r["POP"] >= 80 else ("Heritage Anchor/Bandwagon" if r["POP"] >= 70 else ("Ascending Sandbox" if r["POP"] >= 55 else ("Purgatory Franchise" if r["POP"] >= 35 else "Regional Invisible")))
            print(f"{i+1:>2}. {r['name']:<18} | POP: {r['POP']:>5.2f} (B: {r['B']:>5.2f} | H: {r['H']:>5.2f} | TWI: {r['TWI']:>5.2f}) -> {tier}")
            
if __name__ == "__main__":
    PopularityEngine().calculate_popularity()
