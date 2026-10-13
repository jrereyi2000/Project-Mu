import psycopg
from langchain_core.tools import tool
from db.db_handler import DatabaseHandler
import json

db = DatabaseHandler()

def _resolve_team_id(cur, identifier: str) -> int:
    """Helper to resolve a team ID from an int or name/abbreviation string."""
    clean_id = str(identifier).strip()
    if clean_id.isdigit():
        cur.execute("SELECT id FROM teams WHERE id = %s;", (int(clean_id),))
        row = cur.fetchone()
        if row:
            return row[0]

    # Search by abbreviation or name
    cur.execute("""
        SELECT id FROM teams 
        WHERE abbreviation ILIKE %s OR name ILIKE %s 
        ORDER BY LENGTH(name) ASC LIMIT 1;
    """, (clean_id, f"%{clean_id}%"))
    row = cur.fetchone()
    if row:
        return row[0]
    return None

@tool
def get_game_box_score(game_id: int) -> str:
    """Fetch the full box score, excitement index, and top player performances for a specific game."""
    try:
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                # Get Game details
                cur.execute("""
                    SELECT 
                        ht.name AS home_team, g.home_score, 
                        at.name AS away_team, g.away_score, 
                        g.excitement_index, g.week
                    FROM games g
                    JOIN teams ht ON g.home_team_id = ht.id
                    JOIN teams at ON g.away_team_id = at.id
                    WHERE g.id = %s
                """, (game_id,))
                game_row = cur.fetchone()
                if not game_row:
                    return f"Game ID {game_id} not found."

                # Get Top Player Stats for the game (joining teams table for team_name)
                cur.execute("""
                    SELECT 
                        ps.player_name, t.name AS team_name, ps.position,
                        ps.pass_yards, ps.pass_tds, ps.interceptions,
                        ps.rush_yards, ps.rush_tds,
                        ps.receptions, ps.rec_yards, ps.rec_tds
                    FROM player_stats ps
                    LEFT JOIN teams t ON ps.team_id = t.id
                    WHERE ps.game_id = %s
                """, (game_id,))

                stats = cur.fetchall()

                result = {
                    "matchup": f"{game_row[2]} {game_row[3]} @ {game_row[0]} {game_row[1]}",
                    "week": game_row[5],
                    "excitement_index": float(game_row[4]) if game_row[4] else 0.0,
                    "key_performances": []
                }

                for s in stats:
                    # Include notable stat contributions
                    if (s[3] and s[3] > 0) or (s[6] and s[6] > 0) or (s[9] and s[9] > 0):
                        perf = f"{s[0]} ({s[2]}, {s[1] or 'Unknown'}): "
                        if s[3]: perf += f"{s[3]} pass yds, {s[4]} TD, {s[5]} INT. "
                        if s[6]: perf += f"{s[6]} rush yds, {s[7]} TD. "
                        if s[9]: perf += f"{s[8]} rec, {s[9]} rec yds, {s[10]} TD. "
                        result["key_performances"].append(perf)

                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_player_season_stats(player_identifier: str) -> str:
    """Fetch cumulative season stats, Madden OVR, age, experience, awards, and popularity for a specific player. Accepts player name (e.g. 'Drake Maye') or player ID."""
    try:
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                target_id = None
                clean_ident = str(player_identifier).strip()
                if clean_ident.isdigit():
                    target_id = int(clean_ident)
                else:
                    cur.execute("""
                        SELECT id FROM players 
                        WHERE player_name ILIKE %s OR full_name ILIKE %s
                        ORDER BY LENGTH(player_name) ASC
                        LIMIT 1;
                    """, (f"%{clean_ident}%", f"%{clean_ident}%"))
                    row = cur.fetchone()
                    if row:
                        target_id = row[0]

                if not target_id:
                    return f"Player '{player_identifier}' not found in database."

                cur.execute("""
                    SELECT 
                        COALESCE(p.full_name, p.player_name) as full_name, 
                        ps.position, t.name, ps.overall_rating, ps.popularity_index,
                        ss.pass_yards, ss.pass_tds, ss.interceptions,
                        ss.rush_yards, ss.rush_tds,
                        ss.receptions, ss.rec_yards, ss.rec_tds,
                        fp.adp_rank,
                        ps.age, ps.years_of_experience, p.height, p.weight
                    FROM player_seasons ps
                    JOIN players p ON ps.player_id = p.id
                    LEFT JOIN teams t ON ps.team_id = t.id
                    LEFT JOIN season_stats ss ON p.player_name = ss.player_name AND ss.season = ps.season
                    LEFT JOIN fantasy_projections fp ON p.player_name = fp.player_name AND fp.season = ps.season AND fp.week = 1
                    WHERE p.id = %s AND ps.season = 2035
                    LIMIT 1;
                """, (target_id,))

                row = cur.fetchone()
                if not row:
                    return f"Season stats for Player ID {target_id} not found."

                # Fetch career awards/accolades
                cur.execute("""
                    SELECT season, award_type 
                    FROM awards 
                    WHERE player_id = %s OR player_name ILIKE %s
                    ORDER BY season ASC;
                """, (target_id, f"%{row[0]}%"))
                career_awards = [f"{a[0]} {a[1]}" for a in cur.fetchall()]

                result = {
                    "player_id": target_id,
                    "name": row[0],
                    "position": row[1],
                    "team": row[2] or "Free Agent",
                    "age": row[14],
                    "years_of_experience": row[15],
                    "height": row[16] or "N/A",
                    "weight": f"{row[17]} lbs" if row[17] else "N/A",
                    "madden_ovr": row[3],
                    "popularity_index": float(row[4]) if row[4] else 50.0,
                    "career_awards": career_awards if career_awards else ["None recorded"],
                    "preseason_adp_rank": row[13] if row[13] else "Undrafted",
                    "season_stats": {
                        "passing": f"{row[5] or 0} yds, {row[6] or 0} TD, {row[7] or 0} INT",
                        "rushing": f"{row[8] or 0} yds, {row[9] or 0} TD",
                        "receiving": f"{row[10] or 0} rec, {row[11] or 0} yds, {row[12] or 0} TD"
                    }
                }
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_player_context(player_identifier: str) -> str:
    """Fetch complete player profile including age, years of experience, career awards, Madden OVR, physical attributes, and season stats. Accepts player name or player ID."""
    try:
        fn = getattr(get_player_season_stats, "func", get_player_season_stats)
        return fn(player_identifier)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_team_context(team_identifier: str) -> str:
    """Fetch a team's current record, watchability index, popularity, and top 3 players. Accepts team name, abbreviation, or team ID (e.g. 'Patriots', 'NE', or 17)."""
    try:
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id:
                    return f"Team '{team_identifier}' not found. Valid teams include Cowboys (ID 5), Patriots (ID 17), Vikings (ID 12), etc."

                cur.execute("""
                    SELECT name, wins, losses, watchability_index, popularity_index, team_ovr, off_ovr, def_ovr
                    FROM teams t
                    JOIN team_seasons ts ON t.id = ts.team_id
                    WHERE t.id = %s AND ts.season = 2035
                """, (team_id,))

                team_row = cur.fetchone()
                if not team_row:
                    return f"Season data for Team ID {team_id} not found."

                cur.execute("""
                    SELECT COALESCE(p.full_name, p.player_name), ps.position, ps.overall_rating
                    FROM player_seasons ps
                    JOIN players p ON ps.player_id = p.id
                    WHERE ps.team_id = %s AND ps.season = 2035
                    ORDER BY ps.overall_rating DESC
                    LIMIT 3;
                """, (team_id,))

                top_players = [f"{r[0]} ({r[1]} - {r[2]} OVR)" for r in cur.fetchall()]

                result = {
                    "team": team_row[0],
                    "team_id": team_id,
                    "record": f"{team_row[1]}-{team_row[2]}",
                    "culture_metrics": {
                        "watchability_index": float(team_row[3]) if team_row[3] else 50.0,
                        "popularity_index": float(team_row[4]) if team_row[4] else 50.0
                    },
                    "madden_ratings": {
                        "team_ovr": team_row[5],
                        "off_ovr": team_row[6],
                        "def_ovr": team_row[7]
                    },
                    "top_3_players": top_players
                }
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_team_season_schedule(team_identifier: str, season: int = 2035) -> str:
    """Fetch a team's complete season schedule and game-by-game results: scores, opponents, win/loss outcomes, victory margins, one-score game status, and game IDs for every game that season. Accepts team name, abbreviation, or team ID."""
    try:
        s_val = int(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id:
                    return f"Team '{team_identifier}' not found."

                cur.execute("SELECT name FROM teams WHERE id = %s;", (team_id,))
                team_name_row = cur.fetchone()
                team_name = team_name_row[0] if team_name_row else str(team_id)

                cur.execute("""
                    SELECT 
                        g.id, g.week,
                        CASE WHEN g.home_team_id = %s THEN 'vs' ELSE '@' END AS location,
                        CASE WHEN g.home_team_id = %s THEN at.name ELSE ht.name END AS opponent,
                        CASE WHEN g.home_team_id = %s THEN g.away_team_id ELSE g.home_team_id END AS opponent_id,
                        CASE WHEN g.home_team_id = %s THEN g.home_score ELSE g.away_score END AS team_score,
                        CASE WHEN g.home_team_id = %s THEN g.away_score ELSE g.home_score END AS opponent_score,
                        ABS(COALESCE(g.home_score, 0) - COALESCE(g.away_score, 0)) AS margin,
                        COALESCE(g.excitement_index, 0) AS excitement_index,
                        g.status
                    FROM games g
                    JOIN teams ht ON g.home_team_id = ht.id
                    JOIN teams at ON g.away_team_id = at.id
                    WHERE (g.home_team_id = %s OR g.away_team_id = %s)
                      AND g.season = %s
                    ORDER BY g.week ASC;
                """, (team_id, team_id, team_id, team_id, team_id, team_id, team_id, s_val))
                rows = cur.fetchall()

                wins = 0
                losses = 0
                ties = 0
                one_score_wins = 0
                one_score_losses = 0
                pf = 0
                pa = 0
                game_list = []

                for r in rows:
                    t_score = r[5] or 0
                    o_score = r[6] or 0
                    margin = r[7]
                    status = r[9]

                    if status == "COMPLETED":
                        pf += t_score
                        pa += o_score
                        if t_score > o_score:
                            result = "W"
                            wins += 1
                            if margin <= 8:
                                one_score_wins += 1
                        elif t_score < o_score:
                            result = "L"
                            losses += 1
                            if margin <= 8:
                                one_score_losses += 1
                        else:
                            result = "T"
                            ties += 1
                        outcome_str = f"{result} {t_score}-{o_score}"
                    else:
                        outcome_str = "UPCOMING"

                    game_list.append({
                        "week": r[1],
                        "game_id": r[0],
                        "matchup": f"{r[2]} {r[3]} (ID: {r[4]})",
                        "outcome": outcome_str,
                        "margin": margin if status == "COMPLETED" else None,
                        "one_score_game": (margin <= 8) if status == "COMPLETED" else None,
                        "excitement_index": round(float(r[8]), 2) if status == "COMPLETED" else None,
                        "status": status
                    })

                diff = pf - pa
                summary = {
                    "team": team_name,
                    "team_id": team_id,
                    "season": s_val,
                    "record": f"{wins}-{losses}" + (f"-{ties}" if ties else ""),
                    "one_score_record": f"{one_score_wins}-{one_score_losses}",
                    "points_for": pf,
                    "points_against": pa,
                    "point_differential": f"+{diff}" if diff > 0 else str(diff),
                    "completed_games": wins + losses + ties,
                    "season_schedule": game_list
                }
                return json.dumps(summary, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_league_leaderboards() -> str:
    """Fetch the current top 5 teams by Watchability (W) and Popularity (POP)."""
    try:
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                # Top Watchability
                cur.execute("""
                    SELECT t.name, ts.watchability_index 
                    FROM teams t JOIN team_seasons ts ON t.id = ts.team_id 
                    WHERE ts.season = 2035 ORDER BY ts.watchability_index DESC NULLS LAST LIMIT 5;
                """)
                top_w = [f"{r[0]} ({round(float(r[1] or 0), 1)} Watchability)" for r in cur.fetchall()]

                # Top Popularity
                cur.execute("""
                    SELECT t.name, ts.popularity_index 
                    FROM teams t JOIN team_seasons ts ON t.id = ts.team_id 
                    WHERE ts.season = 2035 ORDER BY ts.popularity_index DESC NULLS LAST LIMIT 5;
                """)
                top_pop = [f"{r[0]} ({round(float(r[1] or 0), 1)} Popularity)" for r in cur.fetchall()]

                result = {
                    "most_watchable_teams": top_w,
                    "most_popular_teams": top_pop
                }
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_advanced_team_analytics(team_identifier: str) -> str:
    """Fetch advanced tactical metrics for a team: Blue-Chip Pedigree (P_draft), Under-25 Production (Y_prod), and Purgatory Status. Accepts team name or team ID."""
    try:
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id:
                    return f"Team '{team_identifier}' not found."

                # Calculate Y_prod (Under 25 production share)
                cur.execute("""
                    SELECT 
                        SUM(CASE WHEN ps.age <= 24 THEN ss.rush_yards + ss.rec_yards ELSE 0 END) as youth_yards,
                        SUM(ss.rush_yards + ss.rec_yards) as total_yards
                    FROM player_seasons ps
                    JOIN players p ON ps.player_id = p.id
                    JOIN season_stats ss ON p.player_name = ss.player_name AND ss.season = ps.season
                    WHERE ps.team_id = %s AND ps.season = 2035;
                """, (team_id,))
                row = cur.fetchone()
                y_prod = 0.0
                if row and row[1] and row[1] > 0:
                    y_prod = round(float(row[0] or 0) / float(row[1]) * 100, 1)

                # Get average age for Purgatory Penalty
                cur.execute("SELECT AVG(age) FROM player_seasons WHERE team_id = %s AND season = 2035;", (team_id,))
                avg_age_row = cur.fetchone()
                avg_age = round(float(avg_age_row[0] or 26.0), 1)

                result = {
                    "team_id": team_id,
                    "youth_production_share": f"{y_prod}%",
                    "roster_average_age": avg_age,
                    "draft_pedigree_assessment": "High" if y_prod > 35 else "Moderate",
                    "purgatory_risk": "High (Aging Roster)" if avg_age > 27.5 else "Low"
                }
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_advanced_game_analytics(game_id: int) -> str:
    """Fetch advanced schematic metrics for a game: Offensive Pace, Upset Leverage, and Single-Game Excitement."""
    try:
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT home_score, away_score, excitement_index 
                    FROM games WHERE id = %s;
                """, (game_id,))
                row = cur.fetchone()
                if not row:
                    return f"Game ID {game_id} not found."

                total_points = (row[0] or 0) + (row[1] or 0)
                pace = round((total_points / 70.0) * 100, 1)

                result = {
                    "game_id": game_id,
                    "offensive_pace_rating": pace,
                    "single_game_excitement": float(row[2]) if row[2] else 50.0,
                    "upset_narrative_leverage": "High Variance" if (row[2] and row[2] > 80) else "Standard"
                }
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_weekly_slate(week: int, season: int = 2035) -> str:
    """Fetch the complete scoreboard for all games in a given week, including game IDs, matchups, final scores, victory margins, and excitement ratings."""
    try:
        w_val = int(week)
        s_val = int(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        g.id,
                        at.name AS away_team, g.away_score,
                        ht.name AS home_team, g.home_score,
                        ABS(g.home_score - g.away_score) AS margin,
                        COALESCE(g.excitement_index, 0) as excitement_index,
                        g.home_team_id, g.away_team_id
                    FROM games g
                    JOIN teams ht ON g.home_team_id = ht.id
                    JOIN teams at ON g.away_team_id = at.id
                    WHERE g.season = %s AND g.week = %s
                    ORDER BY g.excitement_index DESC NULLS LAST;
                """, (s_val, w_val))
                games = cur.fetchall()
                if not games:
                    return f"No games found for Season {s_val}, Week {w_val}."

                slate = []
                for g in games:
                    slate.append({
                        "game_id": g[0],
                        "matchup": f"{g[1]} {g[2]} @ {g[3]} {g[4]}",
                        "away_team": f"{g[1]} (ID: {g[8]})",
                        "home_team": f"{g[3]} (ID: {g[7]})",
                        "away_score": g[2],
                        "home_score": g[4],
                        "margin": g[5],
                        "excitement_index": round(float(g[6]), 2)
                    })

                result = {
                    "season": s_val,
                    "week": w_val,
                    "total_games": len(slate),
                    "games": slate
                }
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_season_standings(season: int = 2035) -> str:
    """Fetch the complete NFL standings for a season, grouped by division, including wins, losses, win percentage, points scored (PF), points allowed (PA), and point differential."""
    try:
        s_val = int(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        t.id, t.name, t.conference, t.division,
                        ts.wins, ts.losses,
                        ROUND((ts.wins::numeric / NULLIF(ts.wins + ts.losses, 0)::numeric), 3) as win_pct,
                        COALESCE(SUM(CASE WHEN g.home_team_id = t.id THEN g.home_score WHEN g.away_team_id = t.id THEN g.away_score ELSE 0 END), 0) as pf,
                        COALESCE(SUM(CASE WHEN g.home_team_id = t.id THEN g.away_score WHEN g.away_team_id = t.id THEN g.home_score ELSE 0 END), 0) as pa
                    FROM teams t
                    JOIN team_seasons ts ON t.id = ts.team_id AND ts.season = %s
                    LEFT JOIN games g ON (g.home_team_id = t.id OR g.away_team_id = t.id) AND g.season = %s AND g.status = 'COMPLETED'
                    GROUP BY t.id, t.name, t.conference, t.division, ts.wins, ts.losses
                    ORDER BY t.conference, t.division, win_pct DESC, (SUM(CASE WHEN g.home_team_id = t.id THEN g.home_score ELSE g.away_score END) - SUM(CASE WHEN g.home_team_id = t.id THEN g.away_score ELSE g.home_score END)) DESC;
                """, (s_val, s_val))
                rows = cur.fetchall()
                if not rows:
                    return f"No standings found for Season {s_val}."

                standings = {}
                for r in rows:
                    div = f"{r[2]} {r[3]}" if not r[3].startswith(r[2]) else r[3]
                    if div not in standings:
                        standings[div] = []
                    pf = int(r[7])
                    pa = int(r[8])
                    diff = pf - pa
                    standings[div].append({
                        "team": r[1],
                        "team_id": r[0],
                        "record": f"{r[4]}-{r[5]}",
                        "win_pct": float(r[6]) if r[6] is not None else 0.0,
                        "points_for": pf,
                        "points_against": pa,
                        "point_differential": f"+{diff}" if diff > 0 else str(diff)
                    })

                result = {
                    "season": s_val,
                    "standings": standings
                }
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_top_popular_players(limit: int = 20, season: int = 2035) -> str:
    """Fetch the top most popular players across the NFL for a season, ordered by popularity index, including player ID, name, position, team, Madden OVR, age, experience, and popularity rating."""
    try:
        lim = int(limit)
        s_val = int(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        p.id, COALESCE(p.full_name, p.player_name) as name,
                        ps.position, t.name as team, t.id as team_id,
                        ps.overall_rating,
                        ROUND(ps.popularity_index::numeric, 2) as pop,
                        ps.age, ps.years_of_experience
                    FROM player_seasons ps
                    JOIN players p ON ps.player_id = p.id
                    JOIN teams t ON ps.team_id = t.id
                    WHERE ps.season = %s
                    ORDER BY ps.popularity_index DESC NULLS LAST
                    LIMIT %s;
                """, (s_val, lim))
                rows = cur.fetchall()
                if not rows:
                    return f"No player popularity data found for Season {s_val}."

                players = []
                for r in rows:
                    players.append({
                        "player_id": r[0],
                        "name": r[1],
                        "position": r[2],
                        "team": r[3],
                        "team_id": r[4],
                        "overall_rating": r[5],
                        "popularity_index": float(r[6]) if r[6] is not None else 0.0,
                        "age": r[7],
                        "years_of_experience": r[8]
                    })

                result = {
                    "season": s_val,
                    "count": len(players),
                    "top_players": players
                }
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_all_team_popularities(season: int = 2035) -> str:
    """Fetch all 32 NFL teams ranked by popularity index, including team ID, record, watchability rating, overall rating, and popularity score."""
    try:
        s_val = int(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        t.id, t.name, t.conference, t.division,
                        ts.wins, ts.losses,
                        ROUND(ts.popularity_index::numeric, 2) as pop,
                        ROUND(ts.watchability_index::numeric, 2) as watchability,
                        ts.team_ovr
                    FROM teams t
                    JOIN team_seasons ts ON t.id = ts.team_id
                    WHERE ts.season = %s
                    ORDER BY ts.popularity_index DESC NULLS LAST;
                """, (s_val,))
                rows = cur.fetchall()
                if not rows:
                    return f"No team popularity data found for Season {s_val}."

                teams = []
                for r in rows:
                    teams.append({
                        "team_id": r[0],
                        "team": r[1],
                        "conference": r[2],
                        "division": r[3],
                        "record": f"{r[4]}-{r[5]}",
                        "popularity_index": float(r[6]) if r[6] is not None else 0.0,
                        "watchability_index": float(r[7]) if r[7] is not None else 0.0,
                        "team_ovr": r[8]
                    })

                result = {
                    "season": s_val,
                    "total_teams": len(teams),
                    "ranked_teams": teams
                }
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

POSITION_GROUP_MAP = {
    "QB": "QB",
    "HB": "RB", "FB": "RB",
    "WR": "WR",
    "TE": "TE",
    "LT": "OT", "RT": "OT",
    "LG": "IOL", "C": "IOL", "RG": "IOL",
    "LEDG": "EDGE", "REDG": "EDGE",
    "DT": "DT",
    "MIKE": "LB", "WILL": "LB", "SAM": "LB",
    "CB": "CB",
    "FS": "S", "SS": "S",
    "K": "SPECIALISTS", "P": "SPECIALISTS", "LS": "SPECIALISTS",
}

JIMMY_JOHNSON_CHART = {
    1: 3000, 2: 2600, 3: 2200, 4: 1800, 5: 1700, 6: 1600, 7: 1500, 8: 1400,
    9: 1350, 10: 1300, 11: 1250, 12: 1200, 13: 1150, 14: 1100, 15: 1050, 16: 1000,
    17: 950, 18: 900, 19: 875, 20: 850, 21: 800, 22: 780, 23: 760, 24: 740,
    25: 720, 26: 700, 27: 680, 28: 660, 29: 640, 30: 620, 31: 600, 32: 590,
    33: 580, 34: 560, 35: 550, 36: 540, 37: 530, 38: 520, 39: 510, 40: 500,
    41: 490, 42: 480, 43: 470, 44: 460, 45: 450, 46: 440, 47: 430, 48: 420,
    49: 410, 50: 400, 51: 390, 52: 380, 53: 370, 54: 360, 55: 350, 56: 340,
    57: 330, 58: 320, 59: 310, 60: 300, 61: 292, 62: 284, 63: 276, 64: 270,
}

@tool
def get_draft_order(season: int = 2035) -> str:
    """Fetch the official Round 1 NFL Draft selection order (Picks 1-32) for a given season, based on reverse regular season record, point differential tiebreakers, and postseason finish."""
    try:
        s_val = int(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        CASE WHEN home_score > away_score THEN home_team_id ELSE away_team_id END as winner_id,
                        CASE WHEN home_score > away_score THEN away_team_id ELSE home_team_id END as runner_up_id
                    FROM games
                    WHERE season = %s AND (game_type = 'SUPER_BOWL' OR week = 23) AND status = 'COMPLETED'
                    LIMIT 1;
                """, (s_val,))
                sb_row = cur.fetchone()
                sb_winner = sb_row[0] if sb_row else None
                sb_runner = sb_row[1] if sb_row else None

                cur.execute("""
                    SELECT 
                        t.id, t.name, t.abbreviation,
                        ts.wins, ts.losses,
                        ROUND((ts.wins::numeric / NULLIF(ts.wins + ts.losses, 0)::numeric), 3) as win_pct,
                        COALESCE(SUM(CASE WHEN g.home_team_id = t.id THEN g.home_score WHEN g.away_team_id = t.id THEN g.away_score ELSE 0 END), 0) as pf,
                        COALESCE(SUM(CASE WHEN g.home_team_id = t.id THEN g.away_score WHEN g.away_team_id = t.id THEN g.home_score ELSE 0 END), 0) as pa
                    FROM teams t
                    JOIN team_seasons ts ON t.id = ts.team_id AND ts.season = %s
                    LEFT JOIN games g ON (g.home_team_id = t.id OR g.away_team_id = t.id) AND g.season = %s AND g.status = 'COMPLETED'
                    GROUP BY t.id, t.name, t.abbreviation, ts.wins, ts.losses
                    ORDER BY win_pct ASC, (SUM(CASE WHEN g.home_team_id = t.id THEN g.home_score ELSE g.away_score END) - SUM(CASE WHEN g.home_team_id = t.id THEN g.away_score ELSE g.home_score END)) ASC;
                """, (s_val, s_val))
                rows = cur.fetchall()
                if not rows:
                    return f"No draft order data found for Season {s_val}."

                board = []
                non_sb_rows = [r for r in rows if r[0] not in (sb_winner, sb_runner)]
                sb_runner_row = [r for r in rows if r[0] == sb_runner]
                sb_winner_row = [r for r in rows if r[0] == sb_winner]

                pick = 1
                for r in non_sb_rows:
                    pf = int(r[6])
                    pa = int(r[7])
                    diff = pf - pa
                    board.append({
                        "pick": pick,
                        "team_id": r[0],
                        "team": r[1],
                        "abbreviation": r[2],
                        "record": f"{r[3]}-{r[4]}",
                        "win_pct": float(r[5]) if r[5] is not None else 0.0,
                        "point_differential": f"+{diff}" if diff > 0 else str(diff),
                        "selection_status": "Regular Season Finish"
                    })
                    pick += 1

                if sb_runner_row:
                    r = sb_runner_row[0]
                    diff = int(r[6]) - int(r[7])
                    board.append({
                        "pick": 31,
                        "team_id": r[0],
                        "team": r[1],
                        "abbreviation": r[2],
                        "record": f"{r[3]}-{r[4]}",
                        "win_pct": float(r[5]) if r[5] is not None else 0.0,
                        "point_differential": f"+{diff}" if diff > 0 else str(diff),
                        "selection_status": "Super Bowl Runner-up"
                    })
                if sb_winner_row:
                    r = sb_winner_row[0]
                    diff = int(r[6]) - int(r[7])
                    board.append({
                        "pick": 32,
                        "team_id": r[0],
                        "team": r[1],
                        "abbreviation": r[2],
                        "record": f"{r[3]}-{r[4]}",
                        "win_pct": float(r[5]) if r[5] is not None else 0.0,
                        "point_differential": f"+{diff}" if diff > 0 else str(diff),
                        "selection_status": "Super Bowl Champion"
                    })

                result = {
                    "season": s_val,
                    "total_picks": len(board),
                    "round_1_order": board
                }
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_team_draft_needs(team_identifier: str, season: int = 2035) -> str:
    """Fetch a team's primary roster needs ranked by urgency, evaluating starter rating deficits, positional depth, age cliffs, and statistical phase rankings. Accepts team name, abbreviation, or team ID."""
    try:
        s_val = int(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id:
                    return f"Team '{team_identifier}' not found."

                cur.execute("SELECT name, abbreviation FROM teams WHERE id = %s;", (team_id,))
                t_row = cur.fetchone()
                team_name = t_row[0] if t_row else str(team_id)
                team_abbr = t_row[1] if t_row else "NFL"

                cur.execute("""
                    SELECT pass_off_rank, rush_off_rank, pass_rush_rank, coverage_rank, off_ovr, def_ovr
                    FROM team_seasons WHERE team_id = %s AND season = %s;
                """, (team_id, s_val))
                ts = cur.fetchone()

                cur.execute("""
                    SELECT 
                        ps.position,
                        COALESCE(p.full_name, p.player_name),
                        ps.overall_rating,
                        ps.age,
                        ps.years_of_experience
                    FROM player_seasons ps
                    JOIN players p ON ps.player_id = p.id
                    WHERE ps.team_id = %s AND ps.season = %s;
                """, (team_id, s_val))
                rows = cur.fetchall()
                if not rows:
                    return f"No roster data found for {team_name} in Season {s_val}."

                groups = {}
                for pos, name, ovr, age, exp in rows:
                    g = POSITION_GROUP_MAP.get(pos, pos)
                    if g not in groups:
                        groups[g] = []
                    groups[g].append({"name": name, "position": pos, "ovr": ovr, "age": age, "exp": exp})

                needs = []
                for g, players in groups.items():
                    if g in ("SPECIALISTS", "K", "P", "LS"):
                        continue
                    players_sorted = sorted(players, key=lambda x: x["ovr"], reverse=True)
                    starter = players_sorted[0]
                    avg_ovr = round(sum(p["ovr"] for p in players) / len(players), 1)
                    starter_ovr = starter["ovr"]
                    starter_age = starter["age"]
                    depth_count = len(players)

                    score = 0
                    reasons = []

                    if starter_ovr < 75:
                        score += 40
                        reasons.append(f"Starter {starter['name']} ({starter_ovr} OVR) is below starter caliber")
                    elif starter_ovr < 80:
                        score += 25
                        reasons.append(f"Starter {starter['name']} ({starter_ovr} OVR) is bridge-level starter")
                    elif starter_ovr < 85:
                        score += 10

                    if starter_age >= 31:
                        score += 15
                        young_backups = [p for p in players if p["age"] <= 24]
                        if not young_backups:
                            score += 10
                            reasons.append(f"Starter is {starter_age} with no under-25 developmental successor")

                    if depth_count <= 2 and g in ("CB", "OT", "WR", "EDGE", "IOL", "DT"):
                        score += 15
                        reasons.append(f"Dangerous lack of positional depth (only {depth_count} rostered)")

                    if ts:
                        if g in ("CB", "S") and ts[3] and ts[3] >= 24:
                            score += 15
                            reasons.append(f"Team pass coverage ranked {ts[3]}th in NFL")
                        if g in ("EDGE", "DT") and ts[2] and ts[2] >= 24:
                            score += 15
                            reasons.append(f"Team pass rush ranked {ts[2]}th in NFL")
                        if g in ("QB", "WR", "OT") and ts[0] and ts[0] >= 24:
                            score += 15
                            reasons.append(f"Passing offense ranked {ts[0]}th in NFL")
                        if g in ("RB", "IOL", "OT") and ts[1] and ts[1] >= 24:
                            score += 15
                            reasons.append(f"Rushing offense ranked {ts[1]}th in NFL")

                    urgency = "CRITICAL" if score >= 45 else ("HIGH" if score >= 30 else ("MODERATE" if score >= 15 else "LOW"))

                    needs.append({
                        "position_group": g,
                        "urgency": urgency,
                        "need_score": score,
                        "current_starter": f"{starter['name']} ({starter['position']}, {starter_ovr} OVR, age {starter_age})",
                        "positional_avg_ovr": avg_ovr,
                        "roster_depth_count": depth_count,
                        "analytical_reasons": reasons
                    })

                needs_sorted = sorted(needs, key=lambda x: x["need_score"], reverse=True)

                result = {
                    "team": team_name,
                    "team_abbr": team_abbr,
                    "season": s_val,
                    "top_ranked_draft_needs": needs_sorted[:5],
                    "secondary_draft_needs": needs_sorted[5:8]
                }
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_team_roster_depth_chart(team_identifier: str, season: int = 2035) -> str:
    """Fetch a team's complete depth chart organized by position group (QB, RB, WR, TE, OT, IOL, EDGE, DT, LB, CB, S), showing ratings, age tiers, and roster demographics. Accepts team name, abbreviation, or team ID."""
    try:
        s_val = int(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id:
                    return f"Team '{team_identifier}' not found."

                cur.execute("SELECT name, abbreviation FROM teams WHERE id = %s;", (team_id,))
                t_row = cur.fetchone()
                team_name = t_row[0] if t_row else str(team_id)
                team_abbr = t_row[1] if t_row else "NFL"

                cur.execute("""
                    SELECT 
                        COALESCE(p.full_name, p.player_name) as name,
                        ps.position,
                        ps.overall_rating,
                        ps.age,
                        ps.years_of_experience
                    FROM player_seasons ps
                    JOIN players p ON ps.player_id = p.id
                    WHERE ps.team_id = %s AND ps.season = %s
                    ORDER BY ps.overall_rating DESC;
                """, (team_id, s_val))
                players = cur.fetchall()
                if not players:
                    return f"No roster data found for {team_name} in Season {s_val}."

                pos_groups = {
                    "QB": [], "RB": [], "WR": [], "TE": [], "OT": [], "IOL": [],
                    "EDGE": [], "DT": [], "LB": [], "CB": [], "S": [], "SPECIALISTS": []
                }

                blue_chips = 0
                youth_count = 0
                tot_age = 0

                for name, pos, ovr, age, exp in players:
                    tot_age += age
                    if ovr >= 85:
                        blue_chips += 1
                    if age <= 24:
                        youth_count += 1

                    tier = "Elite Blue-Chip" if ovr >= 85 else ("Solid Starter" if ovr >= 78 else ("Bridge/Vulnerability" if ovr >= 72 else "Roster Depth"))
                    age_flag = "Youth Core" if age <= 24 else ("Prime" if age <= 29 else "Veteran Cliff")

                    grp = POSITION_GROUP_MAP.get(pos, "SPECIALISTS")
                    pos_groups[grp].append({
                        "name": name,
                        "position": pos,
                        "overall_rating": ovr,
                        "age": age,
                        "experience": exp,
                        "tier": tier,
                        "age_bracket": age_flag
                    })

                avg_age = round(tot_age / len(players), 1)

                result = {
                    "team": team_name,
                    "team_abbr": team_abbr,
                    "season": s_val,
                    "roster_size": len(players),
                    "average_age": avg_age,
                    "blue_chip_count_85_plus": blue_chips,
                    "under_25_player_count": youth_count,
                    "depth_chart_by_position_group": {k: v for k, v in pos_groups.items() if v}
                }
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_draft_trade_value(pick_number: int) -> str:
    """Fetch the standardized draft trade point value (Jimmy Johnson Chart) and typical trade package scenarios for any pick number (1-260)."""
    try:
        p = int(pick_number)
        if p < 1 or p > 260:
            return "Pick number must be between 1 and 260."

        rnd = ((p - 1) // 32) + 1
        val = JIMMY_JOHNSON_CHART.get(p)
        if not val:
            val = max(1, int(round(270 * (64 / p) ** 1.8)))

        if p <= 3:
            scenario = "Mega-Trade Tier: Moving into this slot requires a top-10 first-rounder + future 1st-round pick + multiple Day 2 assets (approx 2,200-3,000 pts)."
        elif p <= 10:
            scenario = "Blue-Chip Leap: Moving up into this slot typically requires a mid-first round pick plus a high 2nd-rounder (~1,300-1,800 pts)."
        elif p <= 20:
            scenario = "Mid-Round Target: Moving up typically costs a late 1st-round pick plus a 3rd-round pick (~850-1,250 pts)."
        elif p <= 32:
            scenario = "First-Round Gateway (5th-Year Option): Moving into the back of Round 1 typically costs a high 2nd-rounder plus a 3rd or 4th-rounder (~590-800 pts)."
        else:
            scenario = f"Day {2 if rnd in (2, 3) else 3} Selection: Standard trade package involves swapping mid-round selections or future Day 3 picks (~{val} pts)."

        result = {
            "pick_number": p,
            "round": rnd,
            "draft_trade_value_points": val,
            "valuation_model": "Jimmy Johnson Draft Value Chart",
            "trade_scenario_context": scenario
        }
        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_franchise_rebuild_status(team_identifier: str, season: int = 2035) -> str:
    """Fetch a franchise's macro rebuild lifecycle stage (Championship Window, Youth Ascent, Roster Purgatory, Ground-Up Rebuild) and recommended draft posture. Accepts team name, abbreviation, or team ID."""
    try:
        s_val = int(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id:
                    return f"Team '{team_identifier}' not found."

                cur.execute("SELECT name, abbreviation FROM teams WHERE id = %s;", (team_id,))
                t_row = cur.fetchone()
                team_name = t_row[0] if t_row else str(team_id)
                team_abbr = t_row[1] if t_row else "NFL"

                cur.execute("""
                    SELECT ts.wins, ts.losses, ts.team_ovr, ts.off_ovr, ts.def_ovr,
                           ts.pass_off_rank, ts.rush_off_rank, ts.pass_rush_rank, ts.coverage_rank
                    FROM team_seasons ts
                    WHERE ts.team_id = %s AND ts.season = %s;
                """, (team_id, s_val))
                ts = cur.fetchone()
                if not ts:
                    return f"No season data for {team_name} in Season {s_val}."
                wins, losses, team_ovr, off_ovr, def_ovr, po_rk, ro_rk, pr_rk, cov_rk = ts
                total_games = wins + losses
                win_pct = round(wins / total_games, 3) if total_games > 0 else 0.0

                cur.execute("""
                    SELECT 
                        ROUND(AVG(age), 1) as avg_age,
                        COUNT(CASE WHEN overall_rating >= 85 THEN 1 END) as blue_chips,
                        COUNT(CASE WHEN age <= 24 THEN 1 END) as youth_count,
                        COUNT(*) as total_roster
                    FROM player_seasons
                    WHERE team_id = %s AND season = %s;
                """, (team_id, s_val))
                roster_stats = cur.fetchone()
                avg_age = float(roster_stats[0] or 26.5)
                blue_chips = int(roster_stats[1] or 0)
                youth_count = int(roster_stats[2] or 0)
                total_roster = int(roster_stats[3] or 53)

                cur.execute("""
                    SELECT 
                        SUM(CASE WHEN ps.age <= 24 THEN ss.rush_yards + ss.rec_yards ELSE 0 END) as youth_yds,
                        SUM(ss.rush_yards + ss.rec_yards) as tot_yds
                    FROM player_seasons ps
                    JOIN players p ON ps.player_id = p.id
                    JOIN season_stats ss ON p.player_name = ss.player_name AND ss.season = ps.season
                    WHERE ps.team_id = %s AND ps.season = %s;
                """, (team_id, s_val))
                y_row = cur.fetchone()
                y_share = round(float(y_row[0] or 0) / float(y_row[1] or 1) * 100, 1) if y_row and y_row[1] else 0.0

                if team_ovr >= 85 and win_pct >= 0.600:
                    posture_category = "CHAMPIONSHIP_WINDOW"
                    draft_philosophy = "WIN_NOW_IMPACT: Target immediate Day-1 starter, pass rush depth, or luxury offensive weapon to maximize Lombardi window."
                elif avg_age <= 26.0 and y_share >= 25.0:
                    posture_category = "YOUTH_ASCENT"
                    draft_philosophy = "FOUNDATIONAL_HIGH_CEILING: Draft high-upside athletic traits at premium positions (QB/OT/EDGE/CB) to elevate ascending core."
                elif win_pct < 0.350 or (wins <= 5 and team_ovr <= 83):
                    posture_category = "GROUND_UP_REBUILD"
                    draft_philosophy = "FRANCHISE_CORNERSTONE: Secure premier foundational talent (franchise signal-caller or blindside trench anchor)."
                else:
                    posture_category = "ROSTER_PURGATORY"
                    draft_philosophy = "ACCUMULATE_CAPITAL: Trade down for pick volume, clear aging cap commitments, avoid reaching for short-term fixes."

                result = {
                    "team": team_name,
                    "team_abbr": team_abbr,
                    "season": s_val,
                    "record": f"{wins}-{losses}",
                    "win_pct": win_pct,
                    "ratings": {
                        "team_ovr": team_ovr,
                        "off_ovr": off_ovr,
                        "def_ovr": def_ovr
                    },
                    "roster_demographics": {
                        "average_age": avg_age,
                        "blue_chip_count_85_plus": blue_chips,
                        "under_25_roster_count": youth_count,
                        "youth_production_share_pct": f"{y_share}%"
                    },
                    "rebuild_lifecycle_stage": posture_category,
                    "recommended_draft_posture": draft_philosophy
                }
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_team_articles(team_identifier: str, limit: int = 5) -> str:
    """Fetch recent editorial articles, breakdowns, and news referencing a specific team.
    
    Args:
        team_identifier: Team name, city, abbreviation (e.g. 'KC', 'Chiefs', 'Kansas City'), or team ID.
        limit: Maximum number of recent articles to return (default: 5).
    """
    try:
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id:
                    return f"Error: Could not resolve team identifier '{team_identifier}'."

                cur.execute("SELECT name, city, abbreviation FROM teams WHERE id = %s;", (team_id,))
                team_info = cur.fetchone()
                team_str = f"{team_info[1]} {team_info[0]} ({team_info[2]})" if team_info else f"Team {team_id}"

                cur.execute("""
                    SELECT 
                        nf.id,
                        nf.season,
                        nf.week,
                        nf.persona,
                        nf.headline,
                        LEFT(nf.content, 400) AS excerpt,
                        at.mention_type,
                        at.mention_count,
                        nf.created_at
                    FROM article_teams at
                    JOIN news_feed nf ON at.article_id = nf.id
                    WHERE at.team_id = %s
                    ORDER BY nf.season DESC, nf.week DESC, at.mention_type = 'PRIMARY' DESC, at.mention_count DESC, nf.id DESC
                    LIMIT %s;
                """, (team_id, limit))
                rows = cur.fetchall()

                if not rows:
                    return f"No articles currently cataloged for {team_str}."

                results = []
                for r in rows:
                    results.append({
                        "article_id": r[0],
                        "season": r[1],
                        "week": r[2],
                        "author_persona": r[3],
                        "headline": r[4],
                        "mention_type": r[6],
                        "mention_count": r[7],
                        "excerpt": r[5].strip() + ("..." if len(r[5]) >= 400 else ""),
                        "published_at": str(r[8]) if r[8] else None
                    })

                return json.dumps({
                    "team": team_str,
                    "team_id": team_id,
                    "articles_found": len(results),
                    "articles": results
                }, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_player_articles(player_identifier: str, limit: int = 5) -> str:
    """Fetch recent editorial articles, breakdowns, and news referencing a specific player.
    
    Args:
        player_identifier: Player full name (e.g. 'Patrick Mahomes') or player ID.
        limit: Maximum number of recent articles to return (default: 5).
    """
    try:
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                clean_id = str(player_identifier).strip()
                player_id = None
                if clean_id.isdigit():
                    cur.execute("SELECT id FROM players WHERE id = %s;", (int(clean_id),))
                    row = cur.fetchone()
                    if row:
                        player_id = row[0]

                if not player_id:
                    cur.execute("""
                        SELECT p.id, p.full_name 
                        FROM players p
                        LEFT JOIN player_seasons ps ON p.id = ps.player_id
                        WHERE p.full_name ILIKE %s OR p.player_name ILIKE %s 
                        ORDER BY LENGTH(p.full_name) ASC, ps.overall_rating DESC NULLS LAST, p.id ASC LIMIT 1;
                    """, (f"%{clean_id}%", f"%{clean_id}%"))
                    row = cur.fetchone()
                    if row:
                        player_id = row[0]

                if not player_id:
                    return f"Error: Could not resolve player identifier '{player_identifier}'."

                cur.execute("""
                    SELECT p.full_name, t.name, t.city, t.abbreviation
                    FROM players p
                    LEFT JOIN player_seasons ps ON p.id = ps.player_id
                    LEFT JOIN teams t ON ps.team_id = t.id
                    WHERE p.id = %s
                    ORDER BY ps.season DESC NULLS LAST LIMIT 1;
                """, (player_id,))
                p_info = cur.fetchone()
                player_name = p_info[0] if p_info else f"Player {player_id}"
                team_desc = f"{p_info[2]} {p_info[1]}" if p_info and p_info[1] else "Unassigned"

                cur.execute("""
                    SELECT 
                        nf.id,
                        nf.season,
                        nf.week,
                        nf.persona,
                        nf.headline,
                        LEFT(nf.content, 400) AS excerpt,
                        ap.mention_type,
                        ap.mention_count,
                        nf.created_at
                    FROM article_players ap
                    JOIN news_feed nf ON ap.article_id = nf.id
                    WHERE ap.player_id = %s
                    ORDER BY nf.season DESC, nf.week DESC, ap.mention_type = 'PRIMARY' DESC, ap.mention_count DESC, nf.id DESC
                    LIMIT %s;
                """, (player_id, limit))
                rows = cur.fetchall()

                if not rows:
                    return f"No articles currently cataloged for {player_name} ({team_desc})."

                results = []
                for r in rows:
                    results.append({
                        "article_id": r[0],
                        "season": r[1],
                        "week": r[2],
                        "author_persona": r[3],
                        "headline": r[4],
                        "mention_type": r[6],
                        "mention_count": r[7],
                        "excerpt": r[5].strip() + ("..." if len(r[5]) >= 400 else ""),
                        "published_at": str(r[8]) if r[8] else None
                    })

                return json.dumps({
                    "player_name": player_name,
                    "player_id": player_id,
                    "current_team": team_desc,
                    "articles_found": len(results),
                    "articles": results
                }, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"


