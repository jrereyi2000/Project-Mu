import psycopg
from db.db_handler import DatabaseHandler

# Data sourced directly from base_market_gravity.md and legacy_floor.md
TEAM_MODELS = {
    "Cowboys": {"B": 85.85, "S_all_time": 82.3},
    "Eagles": {"B": 71.00, "S_all_time": 55.1},
    "49ers": {"B": 70.90, "S_all_time": 78.1},
    "Packers": {"B": 70.70, "S_all_time": 88.1},
    "Steelers": {"B": 70.65, "S_all_time": 89.8},
    "Bears": {"B": 70.50, "S_all_time": 58.4},
    "Giants": {"B": 69.90, "S_all_time": 74.1},
    "Chiefs": {"B": 67.70, "S_all_time": 70.0},
    "Patriots": {"B": 66.30, "S_all_time": 82.3},
    "Broncos": {"B": 63.00, "S_all_time": 60.6},
    "Commanders": {"B": 59.65, "S_all_time": 56.6},
    "Bills": {"B": 56.60, "S_all_time": 34.1},
    "Raiders": {"B": 53.85, "S_all_time": 60.9},
    "Seahawks": {"B": 53.80, "S_all_time": 40.3},
    "Jets": {"B": 53.55, "S_all_time": 24.8},
    "Vikings": {"B": 53.30, "S_all_time": 45.2},
    "Lions": {"B": 52.65, "S_all_time": 35.0},
    "Browns": {"B": 51.35, "S_all_time": 42.4},
    "Ravens": {"B": 51.10, "S_all_time": 71.5},
    "Dolphins": {"B": 47.80, "S_all_time": 59.6},
    "Saints": {"B": 47.20, "S_all_time": 26.2},
    "Rams": {"B": 44.45, "S_all_time": 63.1},
    "Falcons": {"B": 43.70, "S_all_time": 17.5},
    "Texans": {"B": 43.05, "S_all_time": 14.7},
    "Buccaneers": {"B": 39.30, "S_all_time": 25.9},
    "Colts": {"B": 39.00, "S_all_time": 52.8},
    "Bengals": {"B": 38.15, "S_all_time": 23.4},
    "Chargers": {"B": 36.35, "S_all_time": 31.7},
    "Cardinals": {"B": 33.00, "S_all_time": 23.6},
    "Titans": {"B": 32.20, "S_all_time": 34.6},
    "Panthers": {"B": 31.90, "S_all_time": 25.3},
    "Jaguars": {"B": 31.90, "S_all_time": 13.9},
}

# Bonuses based on 5-year trailing (2030-2034) hardware from legacy_floor.md
RECENT_SUCCESS_BUMPS = {
    "Ravens": 50,    # 2 SB Wins (2030, 2033)
    "Titans": 25,    # 1 SB Win (2031)
    "Cardinals": 25, # 1 SB Win (2032)
    "Panthers": 25,  # 1 SB Win (2034)
    "Giants": 20,    # 4 SB Runner-Ups (2027, 2030, 2031, 2033)
    "Dolphins": 10,  # 2 SB Runner-Ups (2028, 2032)
    "Jets": 10       # 1 SB Runner-Up (2034)
}

class PopularityEngine:
    def __init__(self):
        self.db = DatabaseHandler()
        
    def calculate_popularity(self, season: int = 2035):
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
                
                for tid, name, twi, wins, losses in teams:
                    if not twi: twi = 50.0
                    
                    # 1. Base Market Gravity (B)
                    model = TEAM_MODELS.get(name, {"B": 50, "S_all_time": 50})
                    B = model["B"]
                    
                    # 2. Historical Equity (H)
                    S_all_time = model["S_all_time"]
                    
                    # Calculate S_recent (Trailing 5 years)
                    win_pct = (wins / (wins + losses)) if (wins + losses) > 0 else 0.500
                    S_recent_base = win_pct * 100
                    
                    # Apply Hardware Bumps
                    hardware_bump = RECENT_SUCCESS_BUMPS.get(name, 0)
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
