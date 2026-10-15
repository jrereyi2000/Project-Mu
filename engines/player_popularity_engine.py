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

# Performance (P) for defenders (JR, Oct 14): fantasy points only cover offense, so defenders listed in Madden's
# Season Stats are scored on splash-weighted defensive points per game instead. DEF_SCALE puts the 10th-best
# defender at about the 10th-best skill player's 74 (2035: 25th 66 vs 62, 50th 56 vs 56).
DEFENSIVE_POSITIONS = {"LEDG", "REDG", "DT", "MIKE", "WILL", "SAM", "CB", "FS", "SS"}
DEF_WEIGHTS = {"def_tackles": 0.25, "def_tfl": 1.0, "def_sacks": 4.0, "def_ints": 6.0, "def_deflections": 1.5,
               "def_forced_fumbles": 3.0, "def_fumble_recoveries": 2.0, "def_tds": 6.0, "def_safeties": 2.0}
DEF_SCALE = 12.5
OFF_SCALE = 3.5          # avg PPR per game × 3.5
SPIKE_PPR = 24.0         # an offensive spike game: 24+ PPR
SPLASH_FIELDS = ("def_sacks", "def_ints", "def_forced_fumbles", "def_fumble_recoveries")
SPIKE_SPLASH = 2         # a defensive spike game: 2+ splash plays (sacks, INT, forced fumbles, recoveries) or a defensive TD


def _v(line: dict, key: str) -> float:
    return float(line.get(key) or 0)


def defensive_points(line: dict) -> float:
    return sum(w * _v(line, k) for k, w in DEF_WEIGHTS.items())


def splash_plays(line: dict) -> float:
    return sum(_v(line, k) for k in SPLASH_FIELDS)


def _poisson_at_least(lam: float, k: int) -> float:
    if lam <= 0:
        return 0.0
    return 1.0 - sum(math.exp(-lam) * lam ** i / math.factorial(i) for i in range(k))


def defensive_performance(season_line: dict, game_lines: list) -> tuple:
    """(production 0-100, spike games) from a defender's season line and whatever game lines exist.
    Spike games: counted from game lines where they exist; for the season's other games, estimated from the
    remaining season totals (Poisson chance of 2+ splash plays per game, plus each remaining defensive TD)."""
    games = max(1, int(_v(season_line, "games_played")) or 1)
    production = min(100.0, DEF_SCALE * defensive_points(season_line) / games)
    spikes = sum(1 for g in game_lines if splash_plays(g) >= SPIKE_SPLASH or _v(g, "def_tds") > 0)
    rest = games - len(game_lines)
    if rest > 0:
        splash_left = max(0.0, splash_plays(season_line) - sum(splash_plays(g) for g in game_lines))
        tds_left = max(0.0, _v(season_line, "def_tds") - sum(_v(g, "def_tds") for g in game_lines))
        spikes += tds_left + rest * _poisson_at_least(splash_left / rest, SPIKE_SPLASH)
    return production, spikes


def performance(production: float, spikes: float, win_pct: float) -> tuple:
    """P = (0.60 production + 0.40 SpikeIndex) × 0.85 on a sub-.350 team; SpikeIndex = min(100, 25 per spike game)."""
    spike_idx = round(min(100.0, 25 * spikes), 1)
    discount = 1.0 if win_pct > 0.350 else 0.85
    return (0.60 * production + 0.40 * spike_idx) * discount, spike_idx


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
            'LEDG': 0.90, 'REDG': 0.90, 'SAM': 0.90, 'WILL': 0.90,   # the roster's codes for LE/RE/LOLB/ROLB
            'TE': 0.75, 'MLB': 0.75, 'MIKE': 0.75, 'FS': 0.75, 'SS': 0.75,
            'LT': 0.50, 'RT': 0.50, 'LG': 0.50, 'RG': 0.50, 'C': 0.50, 'DT': 0.50, 'K': 0.50, 'P': 0.50, 'LS': 0.50
        }
        
    def calculate_popularity(self, season: int | None = None):
        if season is None:
            season = self.db.get_current_league_state()[0]
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


                # 3. Performance inputs (P). Offense: each game's PPR by player_id (matching on the abbreviated
                #    name gave defenders the fantasy games of offensive players who share it, e.g. "T. Davis").
                cur.execute("""
                    SELECT st.player_id, st.fantasy_points
                    FROM player_stats st JOIN games g ON g.id = st.game_id
                    WHERE g.season = %s AND st.player_id IS NOT NULL AND st.fantasy_points IS NOT NULL
                """, (season,))
                f_scores = defaultdict(list)
                for player_id, score in cur.fetchall():
                    f_scores[player_id].append(float(score))
                #    Defense: the season line Madden lists, plus the game lines captured so far
                def_cols = ["games_played"] + list(DEF_WEIGHTS)
                cur.execute(f"""
                    SELECT player_id, {', '.join(def_cols)} FROM season_stat_lines
                    WHERE season = %s AND def_tackles IS NOT NULL
                """, (season,))
                def_season = {r[0]: dict(zip(def_cols, r[1:])) for r in cur.fetchall()}
                cur.execute(f"""
                    SELECT player_id, {', '.join(DEF_WEIGHTS)} FROM game_stat_lines
                    WHERE season = %s AND def_tackles IS NOT NULL
                """, (season,))
                def_games = defaultdict(list)
                for r in cur.fetchall():
                    def_games[r[0]].append(dict(zip(DEF_WEIGHTS, r[1:])))

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
                    # Offense: average PPR (× 3.5) and 24+ PPR spike games. Listed defenders: splash-weighted defensive
                    # points per game and 2+ splash-play games. A two-way player keeps the better of the two. No line
                    # at all (an unlisted defender, a lineman): the OVR baseline, no spikes.
                    win_pct = t_wins / (t_wins + t_losses) if (t_wins + t_losses) > 0 else 0.5
                    my_scores = f_scores.get(player_id, [])
                    if my_scores:
                        off_prod = min(100.0, OFF_SCALE * sum(my_scores) / len(my_scores))
                        P, spike_idx = performance(off_prod, len([x for x in my_scores if x >= SPIKE_PPR]), win_pct)
                    else:
                        P, spike_idx = performance(min(100.0, OFF_SCALE * ovr / 100.0 * 15.0), 0, win_pct)
                    if pos in DEFENSIVE_POSITIONS and player_id in def_season:
                        P_def, spike_def = performance(*defensive_performance(def_season[player_id],
                                                                              def_games.get(player_id, [])), win_pct)
                        if not my_scores or P_def > P:
                            P, spike_idx = P_def, spike_def

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
