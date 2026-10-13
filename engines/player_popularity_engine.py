import psycopg
from db.db_handler import DatabaseHandler
from engines.market_constants import market, super_bowl_results
import math
from collections import defaultdict

# Awards Equity (nfl_player_popularity_specification.md §2.5). All-Pro teams aren't recorded.
AWARD_POINTS = {"MVP": 50, "OPOY": 35, "DPOY": 35, "SBMVP": 25, "OROY": 18, "DROY": 18}
AWARD_DECAY = 0.35
AWARD_WINDOW = 3
CAREER_WEIGHT = 0.25

# Exposure (spec §2.2); the appearance flag stands in for SnapShare, which isn't recorded.
EXPOSURE_DECAY = 0.12
GOTW_BONUS = 0.25
EXPOSURE_DEFAULT = 50.0


def awards_equity(player_awards: list, season: int) -> float:
    """player_awards: [(award_season, award_type)]. Counts seasons before `season`:
    A = min(100, Σ_{y=1..3} e^{-0.35(y-1)} · points(season - y) + 0.25 · career points)."""
    by_season = defaultdict(int)
    for y, award in player_awards:
        if y < season:
            by_season[y] += AWARD_POINTS.get(award, 0)
    recent = sum(math.exp(-AWARD_DECAY * (k - 1)) * by_season.get(season - k, 0) for k in range(1, AWARD_WINDOW + 1))
    return min(100.0, recent + CAREER_WEIGHT * sum(by_season.values()))


def exposure(appearances: list, current_week: int) -> float:
    """appearances: [(week, raw_E, is_gotw)] for games the player has a stat line in.
    E = Σ w·E·(1 + 0.25·GOTW) / Σ w with w = e^{-0.12 (current_week - week)}."""
    if not appearances:
        return EXPOSURE_DEFAULT
    num = den = 0.0
    for week, e, gotw in appearances:
        w = math.exp(-EXPOSURE_DECAY * (current_week - week))
        num += w * e * (1 + GOTW_BONUS * gotw)
        den += w
    return min(100.0, num / den)


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
                
                # 2. Fetch Awards (A), matched on player_id (award names are full names; players.player_name is abbreviated)
                cur.execute("SELECT player_id, season, award_type FROM awards WHERE season < %s AND player_id IS NOT NULL", (season,))
                awards = defaultdict(list)
                for player_id, a_season, atype in cur.fetchall():
                    awards[player_id].append((a_season, atype))

                # 2b. Exposure (E): every game the player has a stat line in, with its raw E and Game-of-the-Week flag
                cur.execute("""
                    WITH g AS (
                        SELECT id, week, excitement_index,
                               excitement_index = MAX(excitement_index) OVER (PARTITION BY week) AS is_gotw
                        FROM games
                        WHERE season = %s AND status = 'COMPLETED' AND excitement_index IS NOT NULL
                    )
                    SELECT st.player_name, st.team_id, g.week, g.excitement_index, g.is_gotw
                    FROM player_stats st JOIN g ON g.id = st.game_id
                """, (season,))
                appearances = defaultdict(list)
                current_week = 1
                for pname, tid, week, e, gotw in cur.fetchall():
                    appearances[(pname, tid)].append((week, float(e), 1 if gotw else 0))
                    current_week = max(current_week, week)


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
                    SELECT ps.id, p.full_name, ps.position, ps.overall_rating, ps.age, ps.team_id, p.player_name, ps.years_of_experience, p.id
                    FROM player_seasons ps
                    JOIN players p ON ps.player_id = p.id
                    WHERE ps.season = %s
                """, (season,))
                
                players = cur.fetchall()
                last_sb = {}
                for sb_season, winner, loser in super_bowl_results(cur, season - 1):
                    if sb_season == season - 1:
                        last_sb = {winner: 30, loser: 15}
                final_results = []
                
                for pid, full_name, pos, ovr, age, tid, pname, exp, player_id in players:
                    team_ctx = teams.get(tid, {"name": "Unknown", "pop": 50.0, "wins": 0, "losses": 0})
                    t_name = team_ctx["name"]
                    t_pop = team_ctx["pop"]
                    t_wins = team_ctx["wins"]
                    t_losses = team_ctx["losses"]
                    
                    # --- COMPONENT F: Franchise Platform (15%) ---
                    B = market(t_name)["B"]
                    F = (0.65 * t_pop) + (0.35 * B)
                    
                    # --- COMPONENT E: Exposure (20%) ---
                    E = exposure(appearances.get((pname, tid), []), current_week)
                    
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
                    A = awards_equity(awards.get(player_id, []), season)
                    
                    # --- COMPONENT T: Team Success (15%) ---
                    s_curr = min(100, win_pct * 100)
                    # Last season's Super Bowl: winner 30, runner-up 15
                    s_post = last_sb.get(tid, 0)
                    T = (0.60 * s_curr) + (0.40 * s_post)
                    
                    # --- FINAL MULTIPLIER (M_pos) ---
                    M_pos = self.pos_mults.get(pos, 0.75)
                    
                    # --- FINAL POPULATION ---
                    Pop = (0.15*F + 0.20*E + 0.20*P + 0.15*R + 0.15*A + 0.15*T) * M_pos
                    Pop = min(100.0, max(0.0, Pop))
                    
                    final_results.append({
                        "pid": pid, "name": full_name, "pos": pos, "ovr": ovr, 
                        "team": t_name, "pop": round(Pop, 2), "M": M_pos,
                        "spike": spike_idx, "R": round(R, 2), "F": round(F, 2), "T": round(T, 2),
                        "A": round(A, 2), "E": round(E, 2)
                    })

                # Batch update all player popularity indices in a single round-trip
                if final_results:
                    update_payload = [(r["pop"], r["spike"], r["R"], r["F"], r["T"], r["A"], r["E"], r["pid"]) for r in final_results]
                    cur.executemany("""
                        UPDATE player_seasons
                        SET popularity_index = %s, spike_index = %s, r_rating = %s, f_platform = %s, t_success = %s,
                            a_awards = %s, e_exposure = %s
                        WHERE id = %s;
                    """, update_payload)
                conn.commit()
                
        # Sort and display
        final_results.sort(key=lambda x: x["pop"], reverse=True)
        print("\n🌟 NFL PLAYER POPULARITY INDEX (TOP 20)")
        print("================================================================")
        for i, r in enumerate(final_results[:20]):
            print(f"{i+1:>2}. {r['name']:<20} | {r['pos']:<3} | {r['team']:<10} | OVR: {r['ovr']} | POP: {r['pop']:>5.2f} (M={r['M']})")

if __name__ == "__main__":
    PlayerPopularityEngine().calculate_popularity()
