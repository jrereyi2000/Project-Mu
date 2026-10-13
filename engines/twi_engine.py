import psycopg
import math
import numpy as np
from db.db_handler import DatabaseHandler

class TWIEngine:
    def __init__(self):
        self.db = DatabaseHandler()
        
    def backfill_twi(self, season: int = 2035, current_week: int = 18):
        print(f"--- Calculating Team Watchability Index (TWI) for Season {season} ---")
        
        team_games = {}
        
        with psycopg.connect(self.db.db_conn_info) as conn:
            with conn.cursor() as cur:
                # 1. Fetch current league week if not provided or default
                cur.execute("SELECT current_week FROM league_state WHERE current_season = %s", (season,))
                row = cur.fetchone()
                if row and row[0]:
                    current_week = row[0]
                
                # 2. Fetch all teams
                cur.execute("SELECT id, name FROM teams")
                teams = cur.fetchall()
                
                # 3. Fetch all completed games with their E score
                cur.execute("""
                    SELECT g.id, g.week, g.home_team_id, g.away_team_id, g.home_score, g.away_score, g.excitement_index
                    FROM games g
                    WHERE g.season = %s AND g.status = 'COMPLETED' AND g.excitement_index IS NOT NULL
                    ORDER BY g.week ASC
                """, (season,))
                games = cur.fetchall()
                
                # Pre-fetch total yards per team per game
                cur.execute("""
                    SELECT ts.game_id, t.id, ts.total_offense
                    FROM team_stats ts JOIN teams t ON ts.team_name = t.name
                """)
                yard_map = {}
                for gid, tid, yds in cur.fetchall():
                    if gid not in yard_map: yard_map[gid] = {}
                    yard_map[gid][tid] = float(yds or 0)
                
                # Map games to teams
                for g in games:
                    gid, gwk, htid, atid, hpts, apts, e_idx = g
                    
                    if htid not in team_games: team_games[htid] = []
                    if atid not in team_games: team_games[atid] = []
                    
                    hyds = yard_map.get(gid, {}).get(htid, 0)
                    ayds = yard_map.get(gid, {}).get(atid, 0)
                    
                    team_games[htid].append({"week": gwk, "e_score": e_idx, "pts": hpts, "yds": hyds, "opp_pts": apts, "opp_yds": ayds, "opp_id": atid})
                    team_games[atid].append({"week": gwk, "e_score": e_idx, "pts": apts, "yds": ayds, "opp_pts": hpts, "opp_yds": hyds, "opp_id": htid})
                
                # PASS 1: Calculate raw E_adj (without opponent delta)
                raw_twis = {}
                for tid, _ in teams:
                    if tid not in team_games: 
                        raw_twis[tid] = 50.0
                        continue
                        
                    weighted_sum = 0
                    weight_total = 0
                    for gm in team_games[tid]:
                        t_pts = gm["pts"] or 0
                        o_pts = gm["opp_pts"] or 0
                        t_yds = gm["yds"] or 0
                        o_yds = gm["opp_yds"] or 0
                        
                        pts_share = (t_pts / (t_pts + o_pts)) if (t_pts + o_pts) > 0 else 0.5
                        yds_share = (t_yds / (t_yds + o_yds)) if (t_yds + o_yds) > 0 else 0.5
                        alpha = (0.5 * pts_share) + (0.5 * yds_share)
                        
                        e_adj = gm["e_score"] * alpha * 2.0  # x2 to normalize back to 100 scale since alpha averages 0.5
                        
                        w_t = math.exp(-0.15 * (current_week - gm["week"]))
                        weighted_sum += w_t * e_adj
                        weight_total += w_t
                        
                    raw_twis[tid] = weighted_sum / weight_total if weight_total > 0 else 50.0
                
                league_avg_twi = sum(raw_twis.values()) / len(raw_twis) if raw_twis else 50.0
                
                # PASS 2: Add Opponent Baseline Correction & Ceiling Chaos Volatility (sigma_top)
                mu_adj_map = {}
                sigma_top_map = {}
                for tid, _ in teams:
                    if tid not in team_games:
                        mu_adj_map[tid] = 50.0
                        sigma_top_map[tid] = 0.0
                        continue
                    
                    adjusted_e_scores = []
                    weighted_sum = 0
                    weight_total = 0
                    
                    for gm in team_games[tid]:
                        t_pts = gm["pts"] or 0
                        o_pts = gm["opp_pts"] or 0
                        t_yds = gm["yds"] or 0
                        o_yds = gm["opp_yds"] or 0
                        
                        pts_share = (t_pts / (t_pts + o_pts)) if (t_pts + o_pts) > 0 else 0.5
                        yds_share = (t_yds / (t_yds + o_yds)) if (t_yds + o_yds) > 0 else 0.5
                        alpha = (0.5 * pts_share) + (0.5 * yds_share)
                        
                        # Apply Opponent Delta (Gamma = 0.10)
                        opp_twi = raw_twis.get(gm["opp_id"], 50.0)
                        delta_opp = 0.10 * (league_avg_twi - opp_twi)
                        
                        e_adj = (gm["e_score"] * alpha * 2.0) + delta_opp
                        adjusted_e_scores.append(e_adj)
                        
                        w_t = math.exp(-0.15 * (current_week - gm["week"]))
                        weighted_sum += w_t * e_adj
                        weight_total += w_t
                        
                    mu_adj = weighted_sum / weight_total if weight_total > 0 else 50.0
                    mu_adj_map[tid] = mu_adj
                    
                    # Ceiling Chaos Bonus
                    if adjusted_e_scores:
                        median_val = np.median(adjusted_e_scores)
                        top_games = [e for e in adjusted_e_scores if e > median_val]
                        sigma_top_map[tid] = math.sqrt(sum((e - median_val)**2 for e in top_games) / len(top_games)) if top_games else 0.0
                    else:
                        sigma_top_map[tid] = 0.0

                # 4. COMPONENT: Blue-Chip Draft Pedigree Index (P_draft)
                # Formula: P_draft = min(100, sum(V_pick(k) * w_year(Y) * M_pos * SnapShare))
                # Utilizing actual years_of_experience:
                # w_year: Y=1 (exp 0) -> 1.00; Y=2 (exp 1) -> 0.75; Y=3 (exp 2) -> 0.40; Y>=4 -> 0.0
                pos_mults = {
                    'QB': 1.50,
                    'WR': 1.20, 'LEDG': 1.20, 'REDG': 1.20, 'LE': 1.20, 'RE': 1.20, 'LOLB': 1.20, 'ROLB': 1.20,
                    'HB': 1.00, 'FB': 1.00, 'TE': 1.00, 'CB': 1.00, 'LT': 1.00, 'RT': 1.00,
                    'LG': 0.50, 'RG': 0.50, 'C': 0.50, 'DT': 0.50, 'MLB': 0.50, 'MIKE': 0.50, 'WILL': 0.50, 'SAM': 0.50, 'FS': 0.50, 'SS': 0.50, 'K': 0.50, 'P': 0.50
                }
                w_year_map = {0: 1.00, 1: 0.75, 2: 0.40}
                
                cur.execute("""
                    SELECT ps.team_id, ps.position, ps.years_of_experience, ps.overall_rating
                    FROM player_seasons ps
                    WHERE ps.season = %s AND ps.years_of_experience <= 2
                    ORDER BY ps.years_of_experience ASC, ps.overall_rating DESC
                """, (season,))
                all_young = cur.fetchall()
                classes = {0: [], 1: [], 2: []}
                for p in all_young:
                    classes[p[2]].append(p)
                
                p_draft_map = {t[0]: 0.0 for t in teams}
                for yr, plist in classes.items():
                    w_yr = w_year_map[yr]
                    for pick_k, (tid, pos, exp, ovr) in enumerate(plist[:64], start=1):
                        v_pick = 100.0 * math.exp(-0.045 * (pick_k - 1))
                        m_pos = pos_mults.get(pos, 0.75)
                        snap_share = 0.90 if ovr >= 80 else (0.60 if ovr >= 74 else 0.30)
                        p_draft_map[tid] += (v_pick * w_yr * m_pos * snap_share) * 0.35
                
                for tid in p_draft_map:
                    p_draft_map[tid] = min(100.0, p_draft_map[tid])

                # 5. COMPONENT: Under-25 Production Share (Y_prod)
                # Formula: Y_prod = 100 * (Scrimmage Yards by Skill Players Aged <= 24 / Total Scrimmage Yards)
                cur.execute("""
                    WITH player_ages AS (
                        SELECT p.player_name, ps.team_id, ps.age
                        FROM players p
                        JOIN player_seasons ps ON p.id = ps.player_id
                        WHERE ps.season = %s
                    ),
                    team_scrimmage AS (
                        SELECT 
                            t.id AS team_id,
                            COALESCE(SUM(pst.rush_yards + pst.rec_yards), 0) AS total_scrim,
                            COALESCE(SUM(CASE WHEN pa.age <= 24 THEN (pst.rush_yards + pst.rec_yards) ELSE 0 END), 0) AS u25_scrim
                        FROM teams t
                        JOIN games g ON (g.home_team_id = t.id OR g.away_team_id = t.id) AND g.season = %s AND g.status = 'COMPLETED'
                        LEFT JOIN player_stats pst ON pst.game_id = g.id AND pst.team_id = t.id
                        LEFT JOIN player_ages pa ON pst.player_name = pa.player_name AND pa.team_id = t.id
                        GROUP BY t.id
                    )
                    SELECT team_id, 
                           CASE WHEN total_scrim > 0 THEN (u25_scrim::float / total_scrim::float) * 100.0 ELSE 0.0 END
                    FROM team_scrimmage;
                """, (season, season))
                y_prod_map = {r[0]: float(r[1]) for r in cur.fetchall()}

                # 6. COMPONENT: The Purgatory Penalty
                # Formula: Penalty = max(0, (Age_starters - 27.5)) * max(0, (0.500 - Win%)) * 40
                cur.execute("""
                    WITH ranked_players AS (
                        SELECT ps.team_id, ps.age,
                               ROW_NUMBER() OVER (PARTITION BY ps.team_id ORDER BY ps.overall_rating DESC) as rnk
                        FROM player_seasons ps
                        WHERE ps.season = %s
                    )
                    SELECT ts.team_id, ts.wins, ts.losses, 
                           AVG(rp.age) FILTER (WHERE rp.rnk <= 22) as starter_age
                    FROM ranked_players rp
                    JOIN team_seasons ts ON ts.team_id = rp.team_id AND ts.season = %s
                    GROUP BY ts.team_id, ts.wins, ts.losses;
                """, (season, season))
                purgatory_map = {}
                for tid, w, l, s_age in cur.fetchall():
                    win_pct = (w / (w + l)) if (w + l) > 0 else 0.5
                    pen = max(0.0, float(s_age or 27.5) - 27.5) * max(0.0, 0.500 - win_pct) * 40.0
                    purgatory_map[tid] = pen

                # 7. FINAL TWI FORMULATION
                # Spec: TWI = min(100, 0.40(mu_adj) + 0.20(sigma_100) + 0.25(P_draft) + 0.15(Y_prod) - Penalty_purgatory)
                # Normalizing sigma_top to 100 scale (sigma_100 = min(100, sigma_top * 4.0)) and applying standardizing multiplier (1.25)
                # to anchor league average around 50.0 matching the specification tiers.
                final_results = []
                for tid, tname in teams:
                    mu = mu_adj_map.get(tid, 50.0)
                    sig = sigma_top_map.get(tid, 0.0)
                    sig_100 = min(100.0, sig * 4.0)
                    p_dr = p_draft_map.get(tid, 0.0)
                    y_pr = y_prod_map.get(tid, 0.0)
                    pen = purgatory_map.get(tid, 0.0)
                    
                    raw_score = (0.40 * mu) + (0.20 * sig_100) + (0.25 * p_dr) + (0.15 * y_pr) - pen
                    twi = min(100.0, max(0.0, raw_score * 1.25))
                    
                    final_results.append({
                        "team_id": tid,
                        "team_name": tname,
                        "twi": round(twi, 2),
                        "mu": round(mu, 2),
                        "sigma": round(sig, 2),
                        "p_draft": round(p_dr, 2),
                        "y_prod": round(y_pr, 2),
                        "penalty": round(pen, 2)
                    })
                    
                    cur.execute("""
                        UPDATE team_seasons
                        SET watchability_index = %s, watchability_sigma_top = %s, p_draft = %s,
                            y_prod = %s, purgatory_penalty = %s
                        WHERE team_id = %s AND season = %s
                    """, (round(twi, 2), round(sig, 2), round(p_dr, 2), round(y_pr, 2), round(pen, 2), tid, season))
                    
                conn.commit()
                
        # Sort and print
        final_results.sort(key=lambda x: x["twi"], reverse=True)
        print("\n📺 FINAL NFL TEAM WATCHABILITY INDEX (TWI)")
        print("==========================================================================================")
        for i, r in enumerate(final_results):
            tier = "Must-Watch TV" if r["twi"] >= 75 else ("Chaotic Underdog" if r["sigma"] > 12 else ("Methodical Contender" if r["twi"] >= 55 else "RedZone Fade"))
            print(f"{i+1:>2}. {r['team_name']:<18} | TWI: {r['twi']:>5.2f} (μ: {r['mu']:>5.1f} | σ: {r['sigma']:>4.1f} | P_dr: {r['p_draft']:>5.1f} | Y_pr: {r['y_prod']:>5.1f}% | Pen: {r['penalty']:>4.1f}) -> {tier}")

if __name__ == "__main__":
    TWIEngine().backfill_twi()
