import psycopg
from db.db_handler import DatabaseHandler
import math

class PlayerPopularityEngine:
    def __init__(self):
        self.db = DatabaseHandler()
        self.pos_mults = {
            'QB': 1.15,
            'WR': 1.05, 'HB': 1.05,
            'LE': 0.90, 'RE': 0.90, 'LOLB': 0.90, 'ROLB': 0.90, 'CB': 0.90,
            'TE': 0.75, 'MLB': 0.75, 'FS': 0.75, 'SS': 0.75,
            'LT': 0.50, 'RT': 0.50, 'LG': 0.50, 'RG': 0.50, 'C': 0.50, 'DT': 0.50, 'K': 0.50, 'P': 0.50
        }
        
    def calculate_popularity(self, season: int = 2035):
        print(f"--- Calculating Player Popularity Index for Season {season} ---")
        
        with psycopg.connect(self.db.db_conn_info) as conn:
            with conn.cursor() as cur:
                # 1. Fetch Team Context (F and T)
                cur.execute("""
                    SELECT t.id, t.name, ts.popularity_index, ts.wins, ts.losses
                    FROM teams t
                    JOIN team_seasons ts ON t.id = ts.team_id
                    WHERE ts.season = %s
                """, (season,))
                teams = {r[0]: {"name": r[1], "pop": r[2] or 50.0, "wins": r[3], "losses": r[4]} for r in cur.fetchall()}
                
                # We need B from previous file, let's just approximate or hardcode top ones if missing
                # Actually, we can fetch B directly if we had it, but we can just use team POP as a proxy for F to save time,
                # or rebuild the B dict:
                TEAM_B = {
                    "Cowboys": 85.85, "Eagles": 71.0, "49ers": 70.9, "Packers": 70.7, "Steelers": 70.65,
                    "Bears": 70.5, "Giants": 69.9, "Chiefs": 67.7, "Patriots": 66.3, "Broncos": 63.0,
                    "Commanders": 59.65, "Bills": 56.6, "Raiders": 53.85, "Seahawks": 53.8, "Jets": 53.55,
                    "Vikings": 53.3, "Lions": 52.65, "Browns": 51.35, "Ravens": 51.1, "Dolphins": 47.8,
                    "Saints": 47.2, "Rams": 44.45, "Falcons": 43.7, "Texans": 43.05, "Buccaneers": 39.3,
                    "Colts": 39.0, "Bengals": 38.15, "Chargers": 36.35, "Cardinals": 33.0, "Titans": 32.2,
                    "Panthers": 31.9, "Jaguars": 31.9
                }
                
                # 2. Fetch Awards (A)
                cur.execute("SELECT player_name, award_type FROM awards WHERE season = %s", (season-1,))
                awards = {}
                for pname, atype in cur.fetchall():
                    if pname not in awards: awards[pname] = []
                    awards[pname].append(atype)
                    
                # 3. Fetch Spike Games (P)
                cur.execute("""
                    SELECT player_name, ppr_score
                    FROM vw_fantasy_scores
                    WHERE season = %s
                """, (season,))
                f_scores = {}
                for pname, score in cur.fetchall():
                    if pname not in f_scores: f_scores[pname] = []
                    f_scores[pname].append(float(score))
                    
                # 4. Fetch All Players
                cur.execute("""
                    SELECT ps.id, p.full_name, ps.position, ps.overall_rating, ps.age, ps.team_id, p.player_name, ps.years_of_experience
                    FROM player_seasons ps
                    JOIN players p ON ps.player_id = p.id
                    WHERE ps.season = %s
                """, (season,))
                
                players = cur.fetchall()
                final_results = []
                
                for pid, full_name, pos, ovr, age, tid, pname, exp in players:
                    team_ctx = teams.get(tid, {"name": "Unknown", "pop": 50.0, "wins": 0, "losses": 0})
                    t_name = team_ctx["name"]
                    t_pop = team_ctx["pop"]
                    t_wins = team_ctx["wins"]
                    t_losses = team_ctx["losses"]
                    
                    # --- COMPONENT F: Franchise Platform (15%) ---
                    B = TEAM_B.get(t_name, 50.0)
                    F = (0.65 * t_pop) + (0.35 * B)
                    
                    # --- COMPONENT E: Exposure (20%) ---
                    # Approximate E as team's average game excitability, or just 50 baseline for now
                    E = 60.0 # Standard baseline unless we want to query games
                    
                    # --- COMPONENT P: Performance (20%) ---
                    # We look at their fantasy scores this season
                    my_scores = f_scores.get(pname, [])
                    avg_prod = sum(my_scores)/len(my_scores) if my_scores else (ovr / 100.0 * 15.0) # Baseline projection
                    avg_prod = min(100, avg_prod * 3.5) # Scale to 100
                    
                    spikes = len([s for s in my_scores if s >= 24.0]) # A 24+ pt game is a spike
                    spike_idx = min(100, 25 * spikes)
                    
                    win_pct = t_wins / (t_wins + t_losses) if (t_wins + t_losses) > 0 else 0.5
                    discount = 1.0 if win_pct > 0.350 else 0.85
                    
                    P = ((0.60 * avg_prod) + (0.40 * spike_idx)) * discount
                    
                    # --- COMPONENT R: Rating & Pedigree (15%) ---
                    # Spec Section 2.4: R = max(0, 100 * (ovr - 65) / 34.0) + Delta_rookie
                    # Delta_rookie = 0.15 * V_pick(k) * w_year(Y)
                    R_base = max(0, 100 * (ovr - 65) / 34.0)
                    delta_rookie = 0.0
                    if exp is not None and exp <= 2:
                        w_yr = 1.00 if exp == 0 else (0.75 if exp == 1 else 0.40)
                        # Top-tier rookies/sophomores carry strong anticipation curiosity
                        if ovr >= 78:
                            v_pick_proxy = min(100.0, max(20.0, (ovr - 70) * 4.5))
                            delta_rookie = 0.15 * v_pick_proxy * w_yr
                    R = min(100.0, R_base + delta_rookie)
                    
                    # --- COMPONENT A: Awards (15%) ---
                    my_awards = awards.get(pname, [])
                    a_pts = 0
                    if 'MVP' in my_awards: a_pts += 50
                    if 'OPOY' in my_awards or 'DPOY' in my_awards: a_pts += 35
                    if 'OROY' in my_awards or 'DROY' in my_awards: a_pts += 18
                    A = min(100, a_pts * 1.5) # Slight boost to emulate career points
                    
                    # --- COMPONENT T: Team Success (15%) ---
                    s_curr = min(100, win_pct * 100)
                    # Add SB lore
                    s_post = 0
                    if t_name in ['Panthers']: s_post += 30 # 2034 SB Win
                    if t_name in ['Jets']: s_post += 15 # 2034 SB Runner Up
                    T = (0.60 * s_curr) + (0.40 * s_post)
                    
                    # --- FINAL MULTIPLIER (M_pos) ---
                    M_pos = self.pos_mults.get(pos, 0.75)
                    
                    # --- FINAL POPULATION ---
                    Pop = (0.15*F + 0.20*E + 0.20*P + 0.15*R + 0.15*A + 0.15*T) * M_pos
                    Pop = min(100.0, max(0.0, Pop))
                    
                    final_results.append({
                        "pid": pid, "name": full_name, "pos": pos, "ovr": ovr, 
                        "team": t_name, "pop": round(Pop, 2), "M": M_pos
                    })

                # Batch update all player popularity indices in a single round-trip
                if final_results:
                    update_payload = [(r["pop"], r["pid"]) for r in final_results]
                    cur.executemany("UPDATE player_seasons SET popularity_index = %s WHERE id = %s;", update_payload)
                conn.commit()
                
        # Sort and display
        final_results.sort(key=lambda x: x["pop"], reverse=True)
        print("\n🌟 NFL PLAYER POPULARITY INDEX (TOP 20)")
        print("================================================================")
        for i, r in enumerate(final_results[:20]):
            print(f"{i+1:>2}. {r['name']:<20} | {r['pos']:<3} | {r['team']:<10} | OVR: {r['ovr']} | POP: {r['pop']:>5.2f} (M={r['M']})")

if __name__ == "__main__":
    PlayerPopularityEngine().calculate_popularity()
