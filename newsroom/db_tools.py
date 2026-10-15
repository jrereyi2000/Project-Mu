import psycopg
from langchain_core.tools import tool
from db.db_handler import DatabaseHandler
from engines.gotw_engine import display_excitement, production_score, upset_score
from engines.excitement_projection import DEFAULT_MARGIN_SD, DEFAULT_OVR_MARGIN_SLOPE, _normal_cdf as normal_cdf, expected_upset
from engines.market_constants import market
from engines.team_efficiency_engine import compute_team_efficiency, fetch_team_game_rows
from engines.matchup_edges import compute_edges, fetch_ratings, fetch_units
from engines.game_credit_engine import compute_game_credit, fetch_completed_games, trajectory_label
from engines.coty_engine import compute_coty, fetch_inputs as fetch_coty_inputs
from engines.coach_profile import coach_profile, fetch_team_coach, fetch_team_staff, staff_matchup
from engines.player_splits import (BLEND_GAMES, DIMENSIONS, MIN_GAMES, compute_splits, defense_groups,
                                   player_lines, stat_group, STATS as SPLIT_STATS)
import json

from newsroom.run_context import context_season, cutoff_week, is_visible_week

db = DatabaseHandler()


def resolve_season(season=0) -> int:
    """Season a tool reads: the explicit argument, else the article being written (newsroom/run_context.py),
    else league_state.current_season, else the latest season with games."""
    if season:
        return int(season)
    ctx = context_season()
    if ctx:
        return ctx
    with psycopg.connect(db.db_conn_info) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_season FROM league_state LIMIT 1;")
            row = cur.fetchone()
            if row and row[0]:
                return int(row[0])
            cur.execute("SELECT MAX(season) FROM games;")
            row = cur.fetchone()
            return int(row[0]) if row and row[0] else 0


def _article_cutoff_params() -> tuple:
    """SQL params hiding articles from the current article's publication week onward (and the article itself)."""
    season = context_season()
    week = cutoff_week(season) if season else None
    if season is None or week is None:
        return (None, None, None, None)
    return (season, season, season, week)


def _visible_completed_games(cur, season: int) -> list:
    """Completed games of `season` the current article may see (none from its publication week onward)."""
    return [g for g in fetch_completed_games(cur, season) if is_visible_week(season, g["week"])]

SKILL_POSITIONS = ("HB", "FB", "WR", "TE")
DEFENSIVE_POSITIONS = ("LEDG", "REDG", "DT", "MIKE", "WILL", "SAM", "CB", "FS", "SS")

# Madden's Season Stats lists only its leaders in each category (defense is sorted by tackles, roughly the top
# 250 defenders), so a player with no line has unknown numbers, not zeros.
NO_LINE_NOTE = ("Not among the league leaders Madden lists for this category, so these numbers are unknown, "
                "not zero. Don't cite a stat for this player in this category.")


def _num(v):
    """Stat value for prose: whole numbers without a trailing .0 (sacks come in halves)."""
    if v is None:
        return 0
    f = float(v)
    return int(f) if f.is_integer() else round(f, 1)


def defense_line(tackles, sacks, tfl, ints, deflections, forced_fumbles, fumble_recoveries, tds, safeties=0) -> str:
    """One defender's line, e.g. '68 tkl, 14.5 sacks, 9 TFL, 2 INT, 6 PD, 3 FF'; zero categories are left out."""
    parts = [f"{_num(tackles)} tkl"]
    for val, label in ((sacks, "sacks"), (tfl, "TFL"), (ints, "INT"), (deflections, "PD"),
                       (forced_fumbles, "FF"), (fumble_recoveries, "FR"), (tds, "def TD"), (safeties, "safety")):
        if val:
            parts.append(f"{_num(val)} {'sack' if label == 'sacks' and _num(val) == 1 else label}")
    return ", ".join(parts)


def kicking_line(fgm, fga, fg_long, xpm, xpa, fgm_50=None, fga_50=None) -> str:
    line = f"{_num(fgm)}/{_num(fga)} FG"
    if fg_long:
        line += f" (long {_num(fg_long)})"
    if fga_50:
        line += f", {_num(fgm_50)}/{_num(fga_50)} from 50+"
    if xpa:
        line += f", {_num(xpm)}/{_num(xpa)} XP"
    return line


def postseason_line(cur, season: int, player_id: int) -> dict:
    """A player's playoff production, summed from the captured box scores of the season's playoff games the
    current article may see (Season Stats are regular season only). Empty when he has no captured playoff line."""
    cut = cutoff_week(season)
    cur.execute("""
        SELECT COUNT(DISTINCT l.game_id),
               SUM(l.completions), SUM(l.pass_attempts), SUM(l.pass_yards), SUM(l.pass_tds), SUM(l.interceptions),
               SUM(l.rush_attempts), SUM(l.rush_yards), SUM(l.rush_tds), SUM(l.receptions), SUM(l.rec_yards), SUM(l.rec_tds),
               SUM(l.def_tackles), SUM(l.def_sacks), SUM(l.def_tfl), SUM(l.def_ints), SUM(l.def_deflections),
               SUM(l.def_forced_fumbles), SUM(l.def_fumble_recoveries), SUM(l.def_tds), SUM(l.def_safeties),
               SUM(l.kick_fgm), SUM(l.kick_fga), MAX(l.kick_fg_long), SUM(l.kick_xpm), SUM(l.kick_xpa),
               COUNT(l.def_tackles), COUNT(l.kick_fga)
        FROM game_stat_lines l JOIN games g ON g.id = l.game_id
        WHERE l.player_id = %s AND g.season = %s AND COALESCE(g.game_type, 'REGULAR_SEASON') <> 'REGULAR_SEASON'
          AND g.status = 'COMPLETED' AND (%s::int IS NULL OR g.week < %s::int)
    """, (player_id, season, cut, cut))
    r = cur.fetchone()
    if not r or not r[0]:
        return {}
    out = {"games": r[0]}
    if r[3]:
        out["passing"] = f"{r[1] or 0}/{r[2] or 0}, {r[3]} yds, {r[4] or 0} TD, {r[5] or 0} INT"
    if r[7]:
        out["rushing"] = f"{r[6] or 0} car, {r[7]} yds, {r[8] or 0} TD"
    if r[10]:
        out["receiving"] = f"{r[9] or 0} rec, {r[10]} yds, {r[11] or 0} TD"
    if r[26]:
        out["defense"] = defense_line(r[12], r[13], r[14], r[15], r[16], r[17], r[18], r[19], r[20])
    if r[27] and (r[22] or r[25]):
        out["kicking"] = kicking_line(r[21], r[22], r[23], r[24], r[25])
    return out


def defense_and_special_teams(cur, season: int, player_id: int) -> dict:
    """A player's season defense, kicking, punting, return and pass-protection lines from the Season Stats
    capture (season_stat_lines), only the categories Madden lists him in."""
    cur.execute("""
        SELECT games_played, def_tackles, def_sacks, def_tfl, def_ints, def_int_yds, def_deflections,
               def_forced_fumbles, def_fumble_recoveries, def_tds, def_safeties, def_solo, def_assists,
               kick_fgm, kick_fga, kick_fg_long, kick_xpm, kick_xpa, kick_fgm_50, kick_fga_50,
               punt_punts, punt_average, punt_net_average, punt_inside_20, punt_longest,
               kr_returns, kr_yards, kr_average, kr_tds, kr_longest,
               pr_returns, pr_yards, pr_average, pr_tds, pr_longest,
               blk_sacks_allowed
        FROM season_stat_lines WHERE season = %s AND player_id = %s LIMIT 1;
    """, (season, player_id))
    r = cur.fetchone()
    if not r:
        return {}
    out = {}
    if r[0] is not None:
        out["games_played"] = r[0]
    if r[1] is not None:
        out["defense"] = defense_line(r[1], r[2], r[3], r[4], r[6], r[7], r[8], r[9], r[10]) \
            + (f"; {_num(r[5])} INT return yds" if r[4] else "") + f" ({_num(r[11])} solo, {_num(r[12])} assisted)"
    if r[14]:
        out["kicking"] = kicking_line(r[13], r[14], r[15], r[16], r[17], r[18], r[19])
    if r[20]:
        out["punting"] = (f"{_num(r[20])} punts, {_num(r[21])} avg, {_num(r[22])} net avg, "
                          f"{_num(r[23])} inside the 20, long {_num(r[24])}")
    if r[25]:
        out["kick_returns"] = f"{_num(r[25])} ret, {_num(r[26])} yds ({_num(r[27])} avg), {_num(r[28])} TD, long {_num(r[29])}"
    if r[30]:
        out["punt_returns"] = f"{_num(r[30])} ret, {_num(r[31])} yds ({_num(r[32])} avg), {_num(r[33])} TD, long {_num(r[34])}"
    if r[35] is not None:
        out["pass_protection"] = f"{_num(r[35])} sacks allowed"
    return out

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
    """Fetch the full box score, excitement index, and top player performances for a specific game: passing, rushing and receiving, plus defensive playmakers (tackles, sacks, TFL, INT, pass deflections, forced fumbles, defensive TDs) and kickers when the game's player stats were captured, and each team's third/fourth-down and two-point conversions, red zone, penalties, quarter-by-quarter scoring and time of possession where they were recorded for this game."""
    try:
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                # Get Game details
                cur.execute("""
                    SELECT 
                        ht.name AS home_team, g.home_score, 
                        at.name AS away_team, g.away_score, 
                        g.excitement_index, g.week, g.season
                    FROM games g
                    JOIN teams ht ON g.home_team_id = ht.id
                    JOIN teams at ON g.away_team_id = at.id
                    WHERE g.id = %s
                """, (game_id,))
                game_row = cur.fetchone()
                if not game_row:
                    return f"Game ID {game_id} not found."
                if not is_visible_week(game_row[6], game_row[5]):
                    return f"Game ID {game_id} (Week {game_row[5]}) has not been played yet as of this article."

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
                    "excitement_index": display_excitement(game_row[4]) if game_row[4] else None,
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

                # Team situational lines: only values that are real (a captured box score, or an exact weekly fill)
                cur.execute("""
                    SELECT team_name, estimated_fields, exact_fields, third_down_conversions, third_down_attempts,
                           fourth_down_conversions, fourth_down_attempts, two_point_conversions, two_point_attempts,
                           red_zone_tds, red_zone_fgs, red_zone_attempts, penalties, penalty_yards,
                           q1, q2, q3, q4, ot, time_of_possession_s, first_downs
                    FROM team_stats WHERE game_id = %s ORDER BY team_name;
                """, (game_id,))
                team_lines = {}
                for (tname, est, exact, c3, a3, c4, a4, c2, a2, rztd, rzfg, rza, pen, peny,
                     q1, q2, q3, q4, ot, top, fd) in cur.fetchall():
                    est, exact = set(est or []), set(exact or [])
                    real = lambda f, v: v is not None and (f not in est or f in exact)
                    line = {}
                    for label, cf, cv, af, av in (("third_down", "third_down_conversions", c3, "third_down_attempts", a3),
                                                  ("fourth_down", "fourth_down_conversions", c4, "fourth_down_attempts", a4),
                                                  ("two_point", "two_point_conversions", c2, "two_point_attempts", a2)):
                        if real(cf, cv):
                            line[label] = f"{cv}/{av}" if real(af, av) else f"{cv} converted"
                    if real("red_zone_tds", rztd) and real("red_zone_fgs", rzfg):
                        line["red_zone"] = f"{rztd} TD, {rzfg} FG" + (f" on {rza} trips" if real("red_zone_attempts", rza) else "")
                    if real("penalty_yards", peny):
                        line["penalties"] = (f"{pen} for {peny} yds" if real("penalties", pen) else f"{peny} penalty yds")
                    if q1 is not None:
                        line["by_quarter"] = f"{q1}-{q2}-{q3}-{q4}" + (f", OT {ot}" if ot else "")
                        if fd:
                            line["first_downs"] = fd
                    if top:
                        line["time_of_possession"] = f"{top // 60}:{top % 60:02d}"
                    if line:
                        team_lines[tname] = line
                if team_lines:
                    result["team_situational"] = team_lines

                # Defense and kicking from the game's player-stats capture (real Madden box scores; only games
                # the capture covered have them)
                cur.execute("""
                    SELECT COALESCE(l.full_name, l.player_name), t.name, ps.position,
                           l.def_tackles, l.def_sacks, l.def_tfl, l.def_ints, l.def_deflections,
                           l.def_forced_fumbles, l.def_fumble_recoveries, l.def_tds, l.def_safeties,
                           l.kick_fgm, l.kick_fga, l.kick_fg_long, l.kick_xpm, l.kick_xpa
                    FROM game_stat_lines l
                    LEFT JOIN teams t ON t.id = l.team_id
                    LEFT JOIN player_seasons ps ON ps.player_id = l.player_id AND ps.season = l.season
                    WHERE l.game_id = %s AND (l.def_tackles IS NOT NULL OR l.kick_fga IS NOT NULL OR l.kick_xpa IS NOT NULL)
                    ORDER BY COALESCE(l.def_sacks, 0) + COALESCE(l.def_ints, 0) + COALESCE(l.def_forced_fumbles, 0)
                             + COALESCE(l.def_tds, 0) DESC, COALESCE(l.def_tackles, 0) DESC;
                """, (game_id,))
                d_rows = cur.fetchall()
                if d_rows:
                    defense, kicking = [], []
                    for d in d_rows:
                        who = f"{d[0]} ({d[2] or '?'}, {d[1] or 'Unknown'})"
                        if d[3] is not None and (d[4] or d[6] or d[8] or d[9] or d[10] or d[11]
                                                 or (d[3] or 0) >= 7 or (d[5] or 0) >= 2):
                            defense.append(f"{who}: " + defense_line(d[3], d[4], d[5], d[6], d[7], d[8], d[9], d[10], d[11]))
                        if d[13] or d[16]:
                            kicking.append(f"{who}: " + kicking_line(d[12], d[13], d[14], d[15], d[16]))
                    result["defensive_performances"] = defense
                    result["kicking"] = kicking
                else:
                    result["defense_note"] = ("No defensive or special-teams lines were captured for this game; "
                                              "don't cite individual tackles, sacks or interceptions from it.")

                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_player_season_stats(player_identifier: str) -> str:
    """Fetch cumulative regular-season stats (passing, rushing, receiving, and where listed defense, kicking, punting, returns and sacks allowed), a separate postseason line from captured playoff box scores when he has one, Madden OVR, age, experience, awards, popularity, popularity components (SpikeIndex, Baseline Rating, Franchise Platform, Team Success, Awards Equity, Exposure), and Playoff Clout, and season touch/yard share for skill players. Accepts player name (e.g. 'Drake Maye') or player ID."""
    try:
        s_val = resolve_season()
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
                        ps.age, ps.years_of_experience, p.height, p.weight,
                        ps.spike_index, ps.r_rating, ps.f_platform, ps.t_success,
                        ps.a_awards, ps.e_exposure, ps.s_playoff, ss.pass_sacks
                    FROM player_seasons ps
                    JOIN players p ON ps.player_id = p.id
                    LEFT JOIN teams t ON ps.team_id = t.id
                    LEFT JOIN season_stats ss ON p.player_name = ss.player_name AND ss.season = ps.season
                    LEFT JOIN fantasy_projections fp ON p.player_name = fp.player_name AND fp.season = ps.season AND fp.week = 1
                    WHERE p.id = %s AND ps.season = %s
                    LIMIT 1;
                """, (target_id, s_val))

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

                # Season touch share / yard share of the team's carries + catches (season totals, which are real;
                # per-game splits for CPU-simmed games are synthetic, so no game-to-game usage deltas)
                usage = None
                if row[1] in SKILL_POSITIONS:
                    cur.execute("""
                        WITH team AS (
                            SELECT SUM(COALESCE(rush_attempts, 0) + COALESCE(receptions, 0)) AS touches,
                                   SUM(COALESCE(rush_yards, 0) + COALESCE(rec_yards, 0)) AS yards
                            FROM season_stats WHERE season = %s AND team_id = (
                                SELECT team_id FROM player_seasons WHERE player_id = %s AND season = %s)
                        )
                        SELECT COALESCE(ss.rush_attempts, 0) + COALESCE(ss.receptions, 0),
                               COALESCE(ss.rush_yards, 0) + COALESCE(ss.rec_yards, 0), team.touches, team.yards
                        FROM players p
                        JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                        JOIN season_stats ss ON ss.player_name = p.player_name AND ss.season = %s AND ss.team_id = ps.team_id
                        CROSS JOIN team
                        WHERE p.id = %s LIMIT 1;
                    """, (s_val, target_id, s_val, s_val, s_val, target_id))
                    u = cur.fetchone()
                    if u and u[2]:
                        usage = {
                            "touch_share_pct": round(100.0 * u[0] / u[2], 1),
                            "scrimmage_yard_share_pct": round(100.0 * u[1] / u[3], 1) if u[3] else None,
                            "scale_note": "Share of the team's season carries + receptions and scrimmage yards. Receptions, not targets; snaps aren't recorded.",
                        }

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
                    "popularity_components": {
                        "spike_index": row[18],
                        "baseline_rating_and_pedigree": row[19],
                        "franchise_platform": row[20],
                        "team_success": row[21],
                        "awards_equity": row[22],
                        "exposure": row[23],
                        "scale_note": "0-100. Spike index = 25 per big game this season (capped at 100): a 24+ PPR game for offense; for a listed defender, a game with 2+ splash plays (sacks, INTs, forced fumbles, recoveries) or a defensive TD, counted from captured game lines and estimated from season totals for the other games, so a defender's can be fractional. Awards equity weights the last 3 seasons' awards with decay plus 25% of career award points. Exposure = recency-weighted excitement of the games they played in, +25% for each week's top game.",
                    },
                    "playoff_clout": {
                        "score": row[24],
                        "scale_note": "0-100: 40 per Super Bowl MVP, 25 per Super Bowl win played in, 8 per other playoff win played in. Super Bowl rosters before 2035 aren't recorded, so earlier rings only count through SB MVPs.",
                    },
                    "career_awards": career_awards if career_awards else ["None recorded"],
                    "preseason_adp_rank": row[13] if row[13] else "Undrafted",
                    "season_usage": usage or "Not applicable (non-skill position or no season stats)",
                    "season_stats": {
                        "passing": f"{row[5] or 0} yds, {row[6] or 0} TD, {row[7] or 0} INT"
                                   + (f", {row[25]} sacks taken" if row[25] is not None and row[5] else ""),
                        "rushing": f"{row[8] or 0} yds, {row[9] or 0} TD",
                        "receiving": f"{row[10] or 0} rec, {row[11] or 0} yds, {row[12] or 0} TD"
                    }
                }
                extra = defense_and_special_teams(cur, s_val, target_id)
                if row[1] in DEFENSIVE_POSITIONS + ("K", "P") and not any(row[5:13]):
                    result["season_stats"] = {}
                result["season_stats"].update({k: v for k, v in extra.items() if k != "games_played"})
                if "games_played" in extra:
                    result["games_played"] = extra["games_played"]
                if row[1] in DEFENSIVE_POSITIONS and "defense" not in extra:
                    result["season_stats"]["defense"] = NO_LINE_NOTE
                elif (row[1] == "K" and "kicking" not in extra) or (row[1] == "P" and "punting" not in extra):
                    result["season_stats"]["special_teams"] = NO_LINE_NOTE
                post = postseason_line(cur, s_val, target_id)
                if post:
                    result["postseason_stats"] = post
                    result["postseason_note"] = ("season_stats are the regular season only; postseason_stats are this "
                                                 "season's playoff games from their box scores. Keep them separate.")
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
        s_val = resolve_season()
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id:
                    return f"Team '{team_identifier}' not found. Valid teams include Cowboys (ID 5), Patriots (ID 17), Vikings (ID 12), etc."

                cur.execute("""
                    SELECT name, wins, losses, watchability_index, popularity_index, team_ovr, off_ovr, def_ovr
                    FROM teams t
                    JOIN team_seasons ts ON t.id = ts.team_id
                    WHERE t.id = %s AND ts.season = %s
                """, (team_id, s_val))

                team_row = cur.fetchone()
                if not team_row:
                    return f"Season data for Team ID {team_id} not found."

                cur.execute("""
                    SELECT COALESCE(p.full_name, p.player_name), ps.position, ps.overall_rating
                    FROM player_seasons ps
                    JOIN players p ON ps.player_id = p.id
                    WHERE ps.team_id = %s AND ps.season = %s
                    ORDER BY ps.overall_rating DESC
                    LIMIT 3;
                """, (team_id, s_val))

                top_players = [f"{r[0]} ({r[1]} - {r[2]} OVR)" for r in cur.fetchall()]
                coach = fetch_team_coach(cur, team_id, s_val)

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
                    "top_3_players": top_players,
                    "head_coach": (f"{coach['coach_name']} (season {s_val - coach['start_season'] + 1} with the team; "
                                   f"{coach['offensive_scheme']} offense, {coach['defensive_scheme']} front). "
                                   "Call get_coach_profile for records, tendencies and hardware.") if coach else None,
                }
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_coach_profile(team_identifier: str, season: int = 0) -> str:
    """Fetch a team's head coach: tenure (season with the team, who he replaced), offensive scheme and defensive front, coaching tendencies (pass lean and fourth-down aggressiveness measured in this league, man coverage as the staff's real-world philosophy; Low/Medium/High with plain-language meaning), this season's record, record with the team, career record (regular season, winning seasons, playoffs, Super Bowls), Super Bowls with the team and Coach of the Year awards. Accepts team name, abbreviation, or team ID."""
    try:
        s_val = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id:
                    return f"Team '{team_identifier}' not found."
                profile = coach_profile(cur, team_id, s_val, is_visible_week, awards_visible=cutoff_week(s_val) is None)
                if not profile:
                    return f"No head coach recorded for team ID {team_id} in {s_val}."
                cur.execute("SELECT name FROM teams WHERE id = %s", (team_id,))
                return json.dumps({"season": s_val, "team": cur.fetchone()[0], **profile}, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_player_scheme_splits(player_identifier: str, seasons: int = 2) -> str:
    """Fetch how a QB, running back, receiver or tight end produces against different kinds of defenses over the last `seasons` seasons (default 2: this one and last): per-game averages and the change from his own average against Strong / Average / Weak scoring defenses (points allowed), pass rushes and coverages (Madden ranks). Only real game lines count. Accepts player name or player ID."""
    try:
        s_val = resolve_season()
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                ident = str(player_identifier).strip()
                if ident.isdigit():
                    target_id = int(ident)
                else:
                    cur.execute("""
                        SELECT id FROM players WHERE player_name ILIKE %s OR full_name ILIKE %s
                        ORDER BY LENGTH(player_name) ASC LIMIT 1
                    """, (f"%{ident}%", f"%{ident}%"))
                    row = cur.fetchone()
                    target_id = row[0] if row else None
                if not target_id:
                    return f"Player '{player_identifier}' not found in database."
                cur.execute("""
                    SELECT COALESCE(p.full_name, p.player_name), ps.position, t.name
                    FROM players p
                    LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                    LEFT JOIN teams t ON t.id = ps.team_id
                    WHERE p.id = %s
                """, (s_val, target_id))
                name, position, team = cur.fetchone()
                group = stat_group(position)
                if not group:
                    return f"{name} ({position}): splits cover quarterbacks, running backs, receivers and tight ends only."
                span = [s_val - i for i in range(max(1, int(seasons)))]
                lines, skipped = player_lines(cur, target_id, span, is_visible_week)
                opp = {}
                for season in span:
                    for t, groups in defense_groups(cur, season, is_visible_week).items():
                        opp[(season, t)] = groups
                splits = compute_splits(lines, opp, SPLIT_STATS[group])
                return json.dumps({
                    "player": name, "position": position, "team": team, "seasons": sorted(span),
                    "real_games": splits["games"], "synthetic_games_skipped": skipped,
                    "overall_per_game": splits["overall_per_game"],
                    "verdict": (None if any("per_game" in g for d in splits["dimensions"].values() for g in d.values())
                                else f"Not enough real games yet for any split (each group needs {MIN_GAMES}); "
                                     "don't cite splits for this player."),
                    "splits_by_opponent": {f"{dim} ({DIMENSIONS[dim]})": v for dim, v in splits["dimensions"].items()},
                    "note": (f"Only real game lines count (captured box scores and exact weekly lines); synthetic lines "
                             f"spread over several games are skipped. Strong = the best 11 of 32 defenses that season, "
                             f"Average the next 10, Weak the rest. A group needs {MIN_GAMES} real games, and its averages "
                             f"are blended toward his overall average by {BLEND_GAMES} games, so small samples lean to his "
                             f"norm. Cite a split as a tendency with its game count (\"in 5 games against top pass "
                             f"rushes\"); with few games, don't build an argument on it."),
                }, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_team_season_schedule(team_identifier: str, season: int = 0) -> str:
    """Fetch a team's complete season schedule and game-by-game results: scores, opponents, win/loss outcomes, victory margins, one-score game status, and game IDs for every game that season. Accepts team name, abbreviation, or team ID."""
    try:
        s_val = resolve_season(season)
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
                    # Games from the article's publication week onward count as not played yet
                    status = r[9] if (r[9] != "COMPLETED" or is_visible_week(s_val, r[1])) else "SCHEDULED"

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
                        "excitement_index": display_excitement(r[8]) if status == "COMPLETED" and r[8] else None,
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
        s_val = resolve_season()
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                # Top Watchability
                cur.execute("""
                    SELECT t.name, ts.watchability_index 
                    FROM teams t JOIN team_seasons ts ON t.id = ts.team_id 
                    WHERE ts.season = %s ORDER BY ts.watchability_index DESC NULLS LAST LIMIT 5;
                """, (s_val,))
                top_w = [f"{r[0]} ({round(float(r[1] or 0), 1)} Watchability)" for r in cur.fetchall()]

                # Top Popularity
                cur.execute("""
                    SELECT t.name, ts.popularity_index 
                    FROM teams t JOIN team_seasons ts ON t.id = ts.team_id 
                    WHERE ts.season = %s ORDER BY ts.popularity_index DESC NULLS LAST LIMIT 5;
                """, (s_val,))
                top_pop = [f"{r[0]} ({round(float(r[1] or 0), 1)} Popularity)" for r in cur.fetchall()]

                result = {
                    "most_watchable_teams": top_w,
                    "most_popular_teams": top_pop
                }
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_advanced_team_analytics(team_identifier: str, season: int = 0) -> str:
    """Fetch a team's stored index components: Team Watchability Index (TWI) and its parts (ceiling/chaos volatility sigma_top, Blue-Chip Draft Pedigree P_draft, Under-25 Production Share Y_prod, Purgatory Penalty), Franchise Popularity, Base Market Gravity (B) with its local/national breakdown where published, Legacy Floor (historical franchise prestige), Playoff Clout, and starter age. Accepts team name, abbreviation, or team ID."""
    try:
        s_val = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id:
                    return f"Team '{team_identifier}' not found."

                cur.execute("""
                    SELECT t.name, ts.watchability_index, ts.watchability_sigma_top, ts.p_draft, ts.y_prod,
                           ts.purgatory_penalty, ts.popularity_index, ts.s_playoff
                    FROM teams t JOIN team_seasons ts ON ts.team_id = t.id AND ts.season = %s
                    WHERE t.id = %s;
                """, (s_val, team_id))
                row = cur.fetchone()
                if not row:
                    return f"No {s_val} season row for team ID {team_id}."
                name, twi, sigma, p_draft, y_prod, penalty, pop, clout = row

                # Same starter definition as the Purgatory Penalty: the 22 highest-rated players
                cur.execute("""
                    SELECT AVG(age) FROM (
                        SELECT age FROM player_seasons WHERE team_id = %s AND season = %s
                        ORDER BY overall_rating DESC LIMIT 22
                    ) s;
                """, (team_id, s_val))
                starter_age = cur.fetchone()[0]

                def num(v):
                    return round(float(v), 1) if v is not None else None

                m = market(name)
                result = {
                    "team": name,
                    "team_id": team_id,
                    "season": s_val,
                    "watchability_index": num(twi),
                    "watchability_sigma_top": num(sigma),
                    "blue_chip_draft_pedigree": num(p_draft),
                    "under_25_production_share_pct": num(y_prod),
                    "purgatory_penalty": num(penalty),
                    "starter_average_age": num(starter_age),
                    "franchise_popularity": num(pop),
                    "base_market_gravity": m["B"],
                    "market_breakdown": ({"local_scale": m["L"], "local_fervor": m["D"], "national_reach": m["N"]}
                                         if m["N"] is not None else "Not published for this franchise"),
                    "legacy_floor": m["S_all_time"],
                    "playoff_clout": num(clout),
                    "scale_note": "All indices 0-100. Purgatory penalty > 0 only for losing teams whose top-22 players average over 27.5 years old; it is subtracted from TWI. sigma_top above ~12 marks a high-variance, chaotic team. Playoff clout decays 15%/season: 100 per Super Bowl title, 50 per Super Bowl loss, 20 per other playoff win.",
                }
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_advanced_game_analytics(game_id: int) -> str:
    """Fetch advanced metrics for a game: Offensive Pace & Production (P, 0-100, points plus yards), Upset Leverage (U, 0-100; for scheduled games the projected U and the underdog's win probability), Single-Game Excitement, and each team's Opponent-Adjusted Game Credit for that game."""
    try:
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT g.season, g.week, g.status, g.home_team_id, g.away_team_id, g.home_score, g.away_score,
                           g.excitement_index, g.projected_excitement, ht.name, at.name,
                           COALESCE(hts.team_ovr, 80), COALESCE(ats.team_ovr, 80)
                    FROM games g
                    JOIN teams ht ON ht.id = g.home_team_id
                    JOIN teams at ON at.id = g.away_team_id
                    LEFT JOIN team_seasons hts ON hts.team_id = g.home_team_id AND hts.season = g.season
                    LEFT JOIN team_seasons ats ON ats.team_id = g.away_team_id AND ats.season = g.season
                    WHERE g.id = %s;
                """, (game_id,))
                row = cur.fetchone()
                if not row:
                    return f"Game ID {game_id} not found."
                season, week, status, home_id, away_id, hs, as_, e_raw, e_proj, home, away, home_ovr, away_ovr = row
                if status == "COMPLETED" and not is_visible_week(season, week):
                    status = "SCHEDULED"  # not played yet as of the article being written

                if home_ovr == away_ovr:
                    favorite, underdog = "Even matchup (equal OVR)", None
                else:
                    favorite, underdog = (home, away) if home_ovr > away_ovr else (away, home)
                result = {"game_id": game_id, "matchup": f"{away} @ {home}", "week": week, "status": status,
                          "ovr_favorite": favorite}

                if status == "COMPLETED":
                    cur.execute("SELECT COALESCE(SUM(total_offense), 0) FROM team_stats WHERE game_id = %s;", (game_id,))
                    yards = cur.fetchone()[0]
                    favorite_won = (hs > as_ and home_ovr >= away_ovr) or (as_ > hs and away_ovr >= home_ovr)
                    credits, _, _ = compute_game_credit(_visible_completed_games(cur, season))
                    game_credit = {}
                    for tid, tname in ((home_id, home), (away_id, away)):
                        entry = next((g for g in credits[tid].game_log if g["game_id"] == game_id), None) if tid in credits else None
                        game_credit[tname] = round(entry["game_credit"], 1) if entry else None
                    result.update({
                        "final": f"{away} {as_} @ {home} {hs}",
                        "offensive_pace_production": round(production_score(hs + as_, yards), 1),
                        "upset_leverage": round(upset_score(home_ovr, away_ovr, favorite_won), 1),
                        "underdog_won": None if underdog is None else not favorite_won,
                        "single_game_excitement": display_excitement(e_raw) if e_raw else None,
                        "opponent_adjusted_game_credit": game_credit,
                    })
                else:
                    mu_home = DEFAULT_OVR_MARGIN_SLOPE * (home_ovr - away_ovr)
                    p_home = normal_cdf(mu_home / DEFAULT_MARGIN_SD)
                    result.update({
                        "projected_upset_leverage": round(expected_upset(home_ovr, away_ovr, mu_home, DEFAULT_MARGIN_SD), 1),
                        "underdog": underdog,
                        "underdog_win_probability": None if underdog is None else round(min(p_home, 1 - p_home), 3),
                        "projected_excitement": display_excitement(e_proj) if e_proj else None,
                    })
                result["scale_note"] = ("Upset leverage: ~50 = neutral; higher = the underdog won (or is likely to keep it close), "
                                        "lower = chalk; 80+ also marks heavyweight clashes. Pace/production: 70 combined points and "
                                        "850 combined yards = 100.")
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_opponent_adjusted_credit(team_identifier: str = "", season: int = 0) -> str:
    """Fetch Opponent-Adjusted Game Credit: schedule-adjusted points per game vs. a league-average team (margins capped at 24, home field removed). Without a team, returns all 32 teams ranked with season credit, recent-form credit, strength of schedule, expected wins, and win luck (wins minus expected wins; positive = record flatters them, i.e. paper tiger). Also returns the Trajectory Multiplier (last 4 games vs. season level, 0.80-1.20; 1.00 = steady) with a Surging/Rising/Steady/Fading/Sliding label. With a team name, abbreviation, or ID, returns that team's summary plus a game-by-game credit log."""
    try:
        s_val = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                games = _visible_completed_games(cur, s_val)
                if not games:
                    return f"No completed games for Season {s_val}."
                credits, hfa, margin_sd = compute_game_credit(games)
                cur.execute("SELECT id, name FROM teams;")
                names = dict(cur.fetchall())

                ranked = sorted(credits.values(), key=lambda c: c.rating, reverse=True)
                ranks = {c.team_id: i for i, c in enumerate(ranked, start=1)}

                def summary(c):
                    losses = c.games - c.wins
                    return {
                        "credit_rank": ranks[c.team_id],
                        "team": names.get(c.team_id, str(c.team_id)),
                        "team_id": c.team_id,
                        "record": f"{c.wins:g}-{losses:g}",
                        "opp_adj_credit": round(c.rating, 1),
                        "recent_form_credit": round(c.recent, 1),
                        "trajectory_multiplier": round(c.trajectory, 2),
                        "trajectory": trajectory_label(c.trajectory),
                        "strength_of_schedule": round(c.sos, 1),
                        "raw_point_diff_per_game": round(c.point_diff / c.games, 1),
                        "expected_wins": round(c.expected_wins, 1),
                        "win_luck": round(c.wins - c.expected_wins, 1),
                    }

                meta = {
                    "season": s_val,
                    "games_rated": len(games),
                    "home_field_advantage_pts": round(hfa, 2),
                    "scale_note": "Points per game vs. a league-average team (0 = average). Strength of schedule > 0 means tougher opponents.",
                }

                if not str(team_identifier).strip():
                    return json.dumps({**meta, "teams": [summary(c) for c in ranked]}, indent=2)

                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id or team_id not in credits:
                    return f"Team '{team_identifier}' not found or has no completed games."
                c = credits[team_id]
                log = [{
                    "week": g["week"],
                    "game_id": g["game_id"],
                    "opponent": f"{'vs' if g['site'] > 0 else ('@' if g['site'] < 0 else 'neutral')} {names.get(g['opponent_id'], g['opponent_id'])}",
                    "opponent_credit": round(g["opponent_rating"], 1),
                    "result": f"{'W' if g['margin'] > 0 else ('L' if g['margin'] < 0 else 'T')} {g['pf']}-{g['pa']}",
                    "game_credit": round(g["game_credit"], 1),
                } for g in c.game_log]
                return json.dumps({**meta, **summary(c), "game_log": log}, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_coach_of_the_year_race(limit: int = 10, season: int = 0) -> str:
    """Fetch the Coach of the Year race: head coaches ranked by COTY score (50 = league average, about 10 points per standard deviation), built from the regular season only. Each candidate shows record and win%, wins above expectation (wins minus expected wins from opponent-adjusted credit), roster outperformance (team OVR rank minus win% rank; positive = winning more than the roster's rating says), improvement over last season's win% (null unless the team's full 17-game regular season last year is recorded), and whether it is the coach's first season with the team."""
    try:
        s_val = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                race = compute_coty(s_val, _visible_completed_games(cur, s_val), *fetch_coty_inputs(cur, s_val))
                if not race:
                    return f"No completed regular-season games for Season {s_val}."
                cur.execute("SELECT id, name FROM teams;")
                names = dict(cur.fetchall())

        def opt(v, nd):
            return None if v is None else round(v, nd)

        candidates = [{
            "rank": r["coty_rank"],
            "coach": r["coach_name"],
            "team": names.get(r["team_id"], str(r["team_id"])),
            "record": f"{r['wins']:g}-{r['losses']:g}",
            "win_pct": round(r["win_pct"], 3),
            "coty_score": round(r["coty_score"], 1),
            "wins_above_expected": round(r["wins_above_expected"], 1),
            "expected_wins": round(r["expected_wins"], 1),
            "team_ovr": r["team_ovr"],
            "team_ovr_rank": r["ovr_rank"],
            "win_pct_rank": r["win_rank"],
            "roster_outperformance": opt(r["roster_outperformance"], 1),
            "last_season_win_pct": opt(r["prev_win_pct"], 3),
            "win_pct_change": opt(r["win_pct_change"], 3),
            "first_season_with_team": r["first_year_with_team"],
        } for r in race[:max(1, int(limit))]]
        note = ("Score blends win% (30%), wins above expectation (20%), roster outperformance (20%) and improvement "
                "over last season (30%), each relative to the league; a missing input is skipped and the rest "
                "re-weighted. +3 for a coach's first season with the team. Ranks: 1 = best; tied ranks are averaged.")
        if all(r["win_pct_change"] is None for r in race):
            note += f" No team has a full {s_val - 1} regular season recorded, so improvement is left out this year."
        return json.dumps({"season": s_val, "games_through_cutoff": sum(r["games"] for r in race) // 2,
                           "scale_note": note, "candidates": candidates}, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

def _pct(made, att):
    return round(100.0 * made / att, 1) if att else None


def _rank(values: dict, best_high: bool) -> dict:
    """1 = best among teams with a value."""
    order = sorted((v, t) for t, v in values.items() if v is not None)
    if best_high:
        order.reverse()
    return {t: i for i, (_, t) in enumerate(order, 1)}


def situational_profiles(cur, season: int) -> dict:
    """Per team: season third/fourth-down and two-point conversions, red zone on offense and defense, penalties
    (team_seasons, Madden's Team Stats screens), league ranks, and third downs allowed from the games where the
    opponent's row is real (captured or an exact weekly fill in team_stats.exact_fields)."""
    cur.execute("SELECT MAX(week) FROM captures WHERE mode = 'weekly' AND status = 'loaded' AND season = %s", (season,))
    covered = (cur.fetchone() or [None])[0]
    cur.execute("""
        SELECT team_id, third_down_conv, third_down_att, fourth_down_conv, fourth_down_att, two_point_conv, two_point_att,
               red_zone_att, red_zone_td, red_zone_fg, red_zone_def_att, red_zone_def_td, red_zone_def_fg,
               penalties, penalty_yards
        FROM team_seasons WHERE season = %s AND third_down_att IS NOT NULL
    """, (season,))
    rows = cur.fetchall()
    if not rows or not covered:
        return {}
    cur.execute("""
        SELECT t.id, COUNT(*) FROM games g JOIN teams t ON t.id IN (g.home_team_id, g.away_team_id)
        WHERE g.season = %s AND g.status = 'COMPLETED' AND COALESCE(g.game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
          AND g.week <= %s GROUP BY t.id
    """, (season, covered))
    games = dict(cur.fetchall())
    # Third downs allowed: the opponent's offensive row, only where it is real
    cur.execute("""
        SELECT t.id,
               SUM(o.third_down_conversions) FILTER (WHERE NOT ('third_down_conversions' = ANY(o.estimated_fields))
                                                     OR 'third_down_conversions' = ANY(o.exact_fields)),
               COUNT(*) FILTER (WHERE (NOT ('third_down_conversions' = ANY(o.estimated_fields))
                                       OR 'third_down_conversions' = ANY(o.exact_fields))
                                      AND o.third_down_conversions IS NOT NULL),
               SUM(o.third_down_conversions) FILTER (WHERE 'third_down_attempts' = ANY(o.exact_fields)
                                                     AND (NOT ('third_down_conversions' = ANY(o.estimated_fields))
                                                          OR 'third_down_conversions' = ANY(o.exact_fields))),
               SUM(o.third_down_attempts) FILTER (WHERE 'third_down_attempts' = ANY(o.exact_fields)
                                                  AND (NOT ('third_down_conversions' = ANY(o.estimated_fields))
                                                       OR 'third_down_conversions' = ANY(o.exact_fields))),
               COUNT(*) FILTER (WHERE 'third_down_attempts' = ANY(o.exact_fields)
                                AND (NOT ('third_down_conversions' = ANY(o.estimated_fields))
                                     OR 'third_down_conversions' = ANY(o.exact_fields)))
        FROM games g
        JOIN teams t ON t.id IN (g.home_team_id, g.away_team_id)
        JOIN team_stats o ON o.game_id = g.id AND o.team_name <> t.name
        WHERE g.season = %s AND g.status = 'COMPLETED' AND g.week <= %s
        GROUP BY t.id
    """, (season, covered))
    allowed = {r[0]: r[1:] for r in cur.fetchall()}
    out = {}
    for (tid, c3, a3, c4, a4, c2, a2, rza, rztd, rzfg, drza, drztd, drzfg, pen, peny) in rows:
        g = games.get(tid) or 0
        out[tid] = {
            "through_week": covered,
            "games": g,
            "third_down": f"{c3}/{a3}", "third_down_pct": _pct(c3, a3),
            "fourth_down": f"{c4}/{a4}", "fourth_down_pct": _pct(c4, a4),
            "two_point": f"{c2}/{a2}",
            "red_zone_trips": rza, "red_zone_trips_per_game": round(rza / g, 2) if g and rza is not None else None,
            "red_zone_td_pct": _pct(rztd, rza), "red_zone_scoring_pct": _pct((rztd or 0) + (rzfg or 0), rza),
            "red_zone_td_fg": f"{rztd} TD, {rzfg} FG",
            "red_zone_trips_allowed": drza, "red_zone_td_pct_allowed": _pct(drztd, drza),
            "red_zone_td_fg_allowed": f"{drztd} TD, {drzfg} FG",
            "penalties_per_game": round(pen / g, 1) if g and pen is not None else None,
            "penalty_yards_per_game": round(peny / g, 1) if g and peny is not None else None,
        }
        a = allowed.get(tid)
        if a and a[1]:
            out[tid]["third_down_conversions_allowed"] = f"{a[0]} in {a[1]} game(s) with a recorded opponent line"
        if a and a[4]:
            out[tid]["third_down_pct_allowed"] = f"{_pct(a[2], a[3])}% ({a[2]}/{a[3]} in {a[4]} game(s))"
    for key, rank_key, best_high in (("third_down_pct", "third_down_rank", True), ("fourth_down_pct", "fourth_down_rank", True),
                                     ("red_zone_td_pct", "red_zone_rank", True),
                                     ("red_zone_td_pct_allowed", "red_zone_defense_rank", False),
                                     ("penalties_per_game", "discipline_rank", False)):
        for t, r in _rank({t: v[key] for t, v in out.items()}, best_high).items():
            out[t][rank_key] = r
    return out


@tool
def get_team_efficiency_profile(team_identifier: str = "", season: int = 0) -> str:
    """Fetch season efficiency from points and yards: Pythagorean wins (the record points for/against predict; pyth_luck = wins minus that, positive = record flatters them), team pace & production (0-100), yards per game passing and rushing, and giveaways per game; plus situational football: third- and fourth-down conversion rates, two-point tries, red-zone trips and TD rate on offense and allowed on defense, penalties and penalty yards per game, each with a league rank (1 = best). Without a team, returns all 32 teams sorted by pyth_luck; with a team name, abbreviation, or ID, returns that team only."""
    try:
        s_val = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                profiles = compute_team_efficiency(fetch_team_game_rows(cur, s_val, before_week=cutoff_week(s_val)))
                if not profiles:
                    return f"No completed games for Season {s_val}."
                cur.execute("SELECT id, name FROM teams;")
                names = dict(cur.fetchall())
                situational = situational_profiles(cur, s_val)

                def fmt(team_id, p):
                    losses = p["games"] - p["wins"]
                    return {
                        "team": names.get(team_id, str(team_id)),
                        "team_id": team_id,
                        "record": f"{p['wins']:g}-{losses:g}",
                        "points_for": p["points_for"],
                        "points_against": p["points_against"],
                        "pythagorean_wins": round(p["pythagorean_wins"], 1),
                        "pyth_luck": round(p["pyth_luck"], 1),
                        "pace_production": round(p["pace_production"], 1),
                        "pass_yds_per_game": round(p["pass_yds_per_game"], 1),
                        "rush_yds_per_game": round(p["rush_yds_per_game"], 1),
                        "giveaways_per_game": round(p["giveaways_per_game"], 2),
                        "situational": situational.get(team_id, "Not captured for this season"),
                    }

                note = ("Pace & production: 35 points and 425 yards per game = 100. Pythagorean wins use raw points; "
                        "compare with expected_wins from get_opponent_adjusted_credit, which also adjusts for schedule. "
                        "Situational numbers are season totals from the team stats through the week shown; ranks are 1-32, "
                        "1 = best (fewest penalties, lowest red-zone TD rate allowed). Third-down defense isn't recorded as a "
                        "season total: third downs allowed appear only for games where the opponent's line is recorded, so "
                        "don't cite a season third-down defense rate beyond them.")
                if not str(team_identifier).strip():
                    ranked = sorted(profiles.items(), key=lambda kv: kv[1]["pyth_luck"], reverse=True)
                    return json.dumps({"season": s_val, "scale_note": note, "teams": [fmt(t, p) for t, p in ranked]}, indent=2)

                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id or team_id not in profiles:
                    return f"Team '{team_identifier}' not found or has no completed games."
                return json.dumps({"season": s_val, "scale_note": note, **fmt(team_id, profiles[team_id])}, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_matchup_edges(team_identifier: str, opponent_identifier: str = "", season: int = 0) -> str:
    """Fetch Matchup Edge for a game, in both directions: pass edge (passing offense vs. the opponent's coverage rating), rush edge (rushing offense vs. defensive rating), ball-security edge (who gives the ball away more), and Trench Edge (offensive line OVR vs. the opponent's top pass rushers). Edges are in standard deviations (0 = even, +1 = clear advantage). Without an opponent, uses the team's next scheduled game. Accepts team names, abbreviations, or IDs."""
    try:
        s_val = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id:
                    return f"Team '{team_identifier}' not found."
                if str(opponent_identifier).strip():
                    opp_id = _resolve_team_id(cur, opponent_identifier)
                    if not opp_id:
                        return f"Team '{opponent_identifier}' not found."
                    context = "Requested matchup"
                else:
                    cur.execute("""
                        SELECT week, CASE WHEN home_team_id = %s THEN away_team_id ELSE home_team_id END
                        FROM games
                        WHERE season = %s AND (home_team_id = %s OR away_team_id = %s)
                          AND (status = 'SCHEDULED' OR (%s::int IS NOT NULL AND week >= %s::int))
                          AND (%s::int IS NULL OR week >= %s::int)
                        ORDER BY week LIMIT 1;
                    """, (team_id, s_val, team_id, team_id, *([cutoff_week(s_val)] * 4)))
                    nxt = cur.fetchone()
                    if not nxt:
                        return f"No scheduled game for team ID {team_id}; pass an opponent."
                    context, opp_id = f"Week {nxt[0]} matchup", nxt[1]

                profiles = compute_team_efficiency(fetch_team_game_rows(cur, s_val, before_week=cutoff_week(s_val)))
                ratings = fetch_ratings(cur, s_val)
                units = fetch_units(cur, s_val)
                cur.execute("SELECT id, name FROM teams;")
                names = dict(cur.fetchall())

                def side(off, dfn):
                    e = compute_edges(profiles, ratings, units, off, dfn)
                    return {
                        "offense": names.get(off), "defense": names.get(dfn),
                        "pass_edge": round(e["edges"]["pass"], 2),
                        "rush_edge": round(e["edges"]["rush"], 2),
                        "ball_security_edge": round(e["edges"]["ball_security"], 2),
                        "trench_edge_ovr": round(e["trench_edge"], 1) if e["trench_edge"] is not None else None,
                        "defense_pass_rush_rank": ratings.get(dfn, {}).get("pass_rush_rank"),
                        "best_edge": e["best_edge"],
                        "biggest_vulnerability": e["biggest_vulnerability"],
                    }

                coaches = {t: fetch_team_staff(cur, t, s_val) for t in (team_id, opp_id)}
                return json.dumps({
                    "season": s_val,
                    "context": context,
                    f"{names.get(team_id)}_offense": side(team_id, opp_id),
                    f"{names.get(opp_id)}_offense": side(opp_id, team_id),
                    "coaching_matchup": {
                        f"{names.get(team_id)}_offense_vs_{names.get(opp_id)}_defense": staff_matchup(coaches[team_id], coaches[opp_id]),
                        f"{names.get(opp_id)}_offense_vs_{names.get(team_id)}_defense": staff_matchup(coaches[opp_id], coaches[team_id]),
                    },
                    "scale_note": "Pass/rush/ball-security edges are in standard deviations across the league (0 = even). Trench edge is offensive-line OVR minus the defense's top-4 pass-rusher OVR. Defensive strength comes from Madden ratings (coverage rank 1 = best). Coaching tendencies are Low/Medium/High: pass lean and fourth down are measured in this league (allowing for the score), man coverage is the staff's real-world philosophy (there is no blitz data); describe them, never turn them into percentages.",
                }, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_weekly_slate(week: int, season: int = 0) -> str:
    """Fetch the complete scoreboard for all games in a given week, including game IDs, matchups, final scores, victory margins, and excitement ratings."""
    try:
        w_val = int(week)
        s_val = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        g.id,
                        at.name AS away_team, g.away_score,
                        ht.name AS home_team, g.home_score,
                        ABS(g.home_score - g.away_score) AS margin,
                        COALESCE(g.excitement_index, 0) as excitement_index,
                        g.home_team_id, g.away_team_id, g.status, g.projected_excitement
                    FROM games g
                    JOIN teams ht ON g.home_team_id = ht.id
                    JOIN teams at ON g.away_team_id = at.id
                    WHERE g.season = %s AND g.week = %s
                    ORDER BY COALESCE(NULLIF(g.excitement_index, 0), g.projected_excitement) DESC NULLS LAST;
                """, (s_val, w_val))
                games = cur.fetchall()
                if not games:
                    return f"No games found for Season {s_val}, Week {w_val}."

                slate = []
                week_visible = is_visible_week(s_val, w_val)
                for g in games:
                    if g[9] == "COMPLETED" and not week_visible:
                        # Not played yet as of the article being written: show it as a scheduled game
                        g = (g[0], g[1], None, g[3], None, None, g[6], g[7], g[8], "SCHEDULED", g[10])
                    slate.append({
                        "game_id": g[0],
                        "matchup": f"{g[1]} {g[2]} @ {g[3]} {g[4]}" if g[9] == "COMPLETED" else f"{g[1]} @ {g[3]}",
                        "status": g[9],
                        "away_team": f"{g[1]} (ID: {g[8]})",
                        "home_team": f"{g[3]} (ID: {g[7]})",
                        "away_score": g[2],
                        "home_score": g[4],
                        "margin": g[5],
                        # 70–100 display scale; scheduled games carry the pre-game projection instead
                        "excitement_index": display_excitement(g[6]) if g[9] == "COMPLETED" and g[6] else None,
                        "projected_excitement": display_excitement(g[10]) if g[9] != "COMPLETED" else None,
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
def get_season_standings(season: int = 0) -> str:
    """Fetch the complete NFL standings for a season (regular-season games only), grouped by division, including wins, losses, win percentage, points scored (PF), points allowed (PA), and point differential."""
    try:
        # Inside an article run, results from its publication week onward are excluded and the record is
        # counted from games; otherwise the stored team_seasons record is used.
        s_val = resolve_season(season)
        cut = cutoff_week(s_val)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT 
                        t.id, t.name, t.conference, t.division,
                        ts.wins, ts.losses,
                        ROUND((ts.wins::numeric / NULLIF(ts.wins + ts.losses, 0)::numeric), 3) as win_pct,
                        COALESCE(SUM(CASE WHEN g.home_team_id = t.id THEN g.home_score WHEN g.away_team_id = t.id THEN g.away_score ELSE 0 END), 0) as pf,
                        COALESCE(SUM(CASE WHEN g.home_team_id = t.id THEN g.away_score WHEN g.away_team_id = t.id THEN g.home_score ELSE 0 END), 0) as pa,
                        COUNT(*) FILTER (WHERE (g.home_team_id = t.id AND g.home_score > g.away_score) OR (g.away_team_id = t.id AND g.away_score > g.home_score)) AS games_wins,
                        COUNT(*) FILTER (WHERE (g.home_team_id = t.id AND g.home_score < g.away_score) OR (g.away_team_id = t.id AND g.away_score < g.home_score)) AS games_losses
                    FROM teams t
                    JOIN team_seasons ts ON t.id = ts.team_id AND ts.season = %s
                    LEFT JOIN games g ON (g.home_team_id = t.id OR g.away_team_id = t.id) AND g.season = %s AND g.status = 'COMPLETED'
                         AND COALESCE(g.game_type, 'REGULAR_SEASON') = 'REGULAR_SEASON'
                         AND (%s::int IS NULL OR g.week < %s::int)
                    GROUP BY t.id, t.name, t.conference, t.division, ts.wins, ts.losses
                    ORDER BY t.conference, t.division, win_pct DESC, (SUM(CASE WHEN g.home_team_id = t.id THEN g.home_score ELSE g.away_score END) - SUM(CASE WHEN g.home_team_id = t.id THEN g.away_score ELSE g.home_score END)) DESC;
                """, (s_val, s_val, cut, cut))
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
                    if cut is not None:
                        w, l = int(r[9]), int(r[10])
                        record, win_pct = f"{w}-{l}", (round(w / (w + l), 3) if w + l else 0.0)
                    else:
                        record, win_pct = f"{r[4]}-{r[5]}", (float(r[6]) if r[6] is not None else 0.0)
                    standings[div].append({
                        "team": r[1],
                        "team_id": r[0],
                        "record": record,
                        "win_pct": win_pct,
                        "points_for": pf,
                        "points_against": pa,
                        "point_differential": f"+{diff}" if diff > 0 else str(diff)
                    })

                if cut is not None:
                    for teams in standings.values():
                        teams.sort(key=lambda t: (t["win_pct"], t["points_for"] - t["points_against"]), reverse=True)

                result = {
                    "season": s_val,
                    "standings": standings
                }
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_top_rated_players(limit: int = 200, season: int = 0) -> str:
    """Fetch the league's highest-rated players by Madden OVR (ties broken by popularity), every position, one compact line each: rank, player ID, name, position, team, OVR, age, years of experience, and season totals (passing/rushing/receiving; tackles/sacks/INT/forced fumbles for listed defenders; FG for kickers; net average for punters). Use this, not the popularity list, to build league-wide player rankings such as a Top 100. Long snappers are excluded. limit is capped at 300."""
    try:
        lim = max(1, min(int(limit), 300))
        s_val = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT p.id, COALESCE(p.full_name, p.player_name), ps.position, t.city || ' ' || t.name,
                           ps.overall_rating, ps.age, ps.years_of_experience,
                           ss.pass_yards, ss.pass_tds, ss.interceptions, ss.rush_yards, ss.rush_tds,
                           ss.receptions, ss.rec_yards, ss.rec_tds,
                           l.def_tackles, l.def_sacks, l.def_ints, l.def_forced_fumbles,
                           l.kick_fgm, l.kick_fga, l.punt_net_average
                    FROM player_seasons ps
                    JOIN players p ON ps.player_id = p.id
                    JOIN teams t ON ps.team_id = t.id
                    LEFT JOIN season_stats ss ON ss.player_name = p.player_name AND ss.season = ps.season
                                             AND ss.team_id = ps.team_id
                    LEFT JOIN season_stat_lines l ON l.season = ps.season AND l.player_id = ps.player_id
                    WHERE ps.season = %s AND ps.overall_rating IS NOT NULL AND ps.position <> 'LS'
                    ORDER BY ps.overall_rating DESC, ps.popularity_index DESC NULLS LAST, p.id
                    LIMIT %s;
                """, (s_val, lim))
                rows = cur.fetchall()
                if not rows:
                    return f"No player ratings found for Season {s_val}."

                lines = []
                for i, r in enumerate(rows, 1):
                    (pid, name, pos, team, ovr, age, exp, py, ptd, ints, ry, rtd, rec, rcy, rctd,
                     tkl, sacks, d_ints, ff, fgm, fga, net_avg) = r
                    stats = []
                    if py:
                        stats.append(f"{py} pass yds, {ptd or 0} TD, {ints or 0} INT")
                    if ry:
                        stats.append(f"{ry} rush yds, {rtd or 0} TD")
                    if rcy:
                        stats.append(f"{rec or 0} rec, {rcy} yds, {rctd or 0} TD")
                    if tkl is not None:
                        stats.append(f"{_num(tkl)} tkl, {_num(sacks)} sacks, {_num(d_ints)} INT, {_num(ff)} FF")
                    if fga:
                        stats.append(f"{_num(fgm)}/{_num(fga)} FG")
                    if net_avg is not None:
                        stats.append(f"{_num(net_avg)} net punt avg")
                    lines.append(f"{i}. {name} (ID {pid}) | {pos} | {team} | OVR {ovr} | age {age} | exp {exp}"
                                 + (f" | {'; '.join(stats)}" if stats else ""))
                return json.dumps({
                    "season": s_val,
                    "count": len(lines),
                    "note": "Ordered by Madden OVR. Stats are season totals so far: passing, rushing, receiving, and for "
                            "defenders tackles/sacks/INT/forced fumbles, kickers FG, punters net average. Madden lists only "
                            "the leaders in each category, so a defender with no stats shown has unknown numbers, not zeros; "
                            "offensive linemen have none. Use get_player_season_stats for a player's full profile.",
                    "players": lines,
                }, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

LEADER_CATEGORIES = {
    # category: (ORDER BY expression, row filter, label)
    "sacks": ("l.def_sacks", "l.def_tackles IS NOT NULL", "sacks"),
    "interceptions": ("l.def_ints", "l.def_tackles IS NOT NULL", "interceptions"),
    "tackles": ("l.def_tackles", "l.def_tackles IS NOT NULL", "tackles"),
    "tackles_for_loss": ("l.def_tfl", "l.def_tackles IS NOT NULL", "tackles for loss"),
    "pass_deflections": ("l.def_deflections", "l.def_tackles IS NOT NULL", "pass deflections"),
    "forced_fumbles": ("l.def_forced_fumbles", "l.def_tackles IS NOT NULL", "forced fumbles"),
    "takeaways": ("COALESCE(l.def_ints, 0) + COALESCE(l.def_fumble_recoveries, 0)", "l.def_tackles IS NOT NULL",
                  "interceptions + fumble recoveries"),
    "defensive_tds": ("l.def_tds", "l.def_tackles IS NOT NULL", "defensive TDs"),
    "field_goals": ("l.kick_fgm", "l.kick_fga > 0", "field goals made"),
    "punting": ("l.punt_net_average", "l.punt_punts > 0", "net punting average"),
    "kick_returns": ("l.kr_yards", "l.kr_returns > 0", "kick return yards"),
    "punt_returns": ("l.pr_yards", "l.pr_returns > 0", "punt return yards"),
    "sacks_allowed": ("-l.blk_sacks_allowed", "l.blk_sacks_allowed IS NOT NULL", "fewest sacks allowed"),
}


@tool
def get_defense_special_teams_leaders(category: str = "sacks", team_identifier: str = "", limit: int = 15,
                                      season: int = 0, rookies_only: bool = False) -> str:
    """Season leaders in a defensive or special-teams category, league-wide or for one team (team name or abbreviation): each player's full defensive line (tackles, sacks, TFL, INT, pass deflections, forced fumbles, fumble recoveries, defensive TDs) or kicking/punting/return line. category: sacks, interceptions, tackles, tackles_for_loss, pass_deflections, forced_fumbles, takeaways, defensive_tds, field_goals, punting, kick_returns, punt_returns, sacks_allowed (offensive linemen). Each line shows the player's years of experience (rookie = first season). rookies_only=True lists rookies only (for Defensive Rookie of the Year). Use it for a team's defensive playmakers, an opponent's pass rushers or ball hawks, All-Pro defense and special teams, and award races. limit is capped at 50."""
    try:
        cat = str(category or "sacks").strip().lower().replace(" ", "_")
        if cat not in LEADER_CATEGORIES:
            return f"Unknown category '{category}'. Use one of: {', '.join(LEADER_CATEGORIES)}."
        order_expr, row_filter, label = LEADER_CATEGORIES[cat]
        lim = max(1, min(int(limit), 50))
        s_val = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                team_id = None
                if str(team_identifier or "").strip():
                    team_id = _resolve_team_id(cur, team_identifier)
                    if not team_id:
                        return f"Team '{team_identifier}' not found."
                cur.execute(f"""
                    SELECT l.player_id, COALESCE(l.full_name, l.player_name), COALESCE(ps.position, l.position),
                           t.city || ' ' || t.name, l.games_played,
                           l.def_tackles, l.def_sacks, l.def_tfl, l.def_ints, l.def_deflections,
                           l.def_forced_fumbles, l.def_fumble_recoveries, l.def_tds, l.def_safeties,
                           l.kick_fgm, l.kick_fga, l.kick_fg_long, l.kick_xpm, l.kick_xpa, l.kick_fgm_50, l.kick_fga_50,
                           l.punt_punts, l.punt_average, l.punt_net_average, l.punt_inside_20,
                           l.kr_returns, l.kr_yards, l.kr_average, l.kr_tds,
                           l.pr_returns, l.pr_yards, l.pr_average, l.pr_tds, l.blk_sacks_allowed, ps.years_of_experience
                    FROM season_stat_lines l
                    LEFT JOIN player_seasons ps ON ps.player_id = l.player_id AND ps.season = l.season
                    LEFT JOIN teams t ON t.id = COALESCE(ps.team_id, l.team_id)
                    WHERE l.season = %s AND {row_filter} AND (%s::int IS NULL OR COALESCE(ps.team_id, l.team_id) = %s::int)
                      AND (NOT %s OR ps.years_of_experience = 0)
                    ORDER BY {order_expr} DESC NULLS LAST, l.def_tackles DESC NULLS LAST, l.player_id
                    LIMIT %s;
                """, (s_val, team_id, team_id, bool(rookies_only), lim))
                rows = cur.fetchall()
                lines = []
                for i, r in enumerate(rows, 1):
                    head = f"{i}. {r[1]} (ID {r[0]}) | {r[2]} | {r[3] or 'Free Agent'}" + (f" | {r[4]} G" if r[4] else "") \
                        + (" | rookie" if r[34] == 0 else (f" | exp {r[34]}" if r[34] is not None else ""))
                    if cat == "field_goals":
                        body = kicking_line(r[14], r[15], r[16], r[17], r[18], r[19], r[20])
                    elif cat == "punting":
                        body = f"{_num(r[21])} punts, {_num(r[22])} avg, {_num(r[23])} net avg, {_num(r[24])} inside the 20"
                    elif cat == "kick_returns":
                        body = f"{_num(r[25])} ret, {_num(r[26])} yds ({_num(r[27])} avg), {_num(r[28])} TD"
                    elif cat == "punt_returns":
                        body = f"{_num(r[29])} ret, {_num(r[30])} yds ({_num(r[31])} avg), {_num(r[32])} TD"
                    elif cat == "sacks_allowed":
                        body = f"{_num(r[33])} sacks allowed"
                    else:
                        body = defense_line(r[5], r[6], r[7], r[8], r[9], r[10], r[11], r[12], r[13])
                    lines.append(f"{head} | {body}")
                return json.dumps({
                    "season": s_val,
                    "category": label,
                    "team": team_identifier or "League-wide",
                    "rookies_only": bool(rookies_only),
                    "leaders": lines or ["No listed players in this category."],
                    "note": "Season totals from Madden's Season Stats, which lists only the leaders in each category "
                            "(defenders are listed by tackles, roughly the top 250). A player missing here has unknown "
                            "numbers, not zeros, and a team's listed defenders don't add up to its full team total, so "
                            "don't sum them into team sacks or takeaways. Pressures, snaps, targets allowed and "
                            "coverage alignments aren't recorded.",
                }, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"


@tool
def get_top_popular_players(limit: int = 20, season: int = 0) -> str:
    """Fetch the top most popular players across the NFL for a season, ordered by popularity index, including player ID, name, position, team, Madden OVR, age, experience, and popularity rating."""
    try:
        lim = int(limit)
        s_val = resolve_season(season)
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
def get_all_team_popularities(season: int = 0) -> str:
    """Fetch all 32 NFL teams ranked by popularity index, including team ID, record, watchability rating, overall rating, and popularity score."""
    try:
        s_val = resolve_season(season)
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
def get_draft_order(season: int = 0) -> str:
    """Fetch the official Round 1 NFL Draft selection order (Picks 1-32) for a given season, based on reverse regular season record, point differential tiebreakers, and postseason finish."""
    try:
        s_val = resolve_season(season)
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
def get_team_draft_needs(team_identifier: str, season: int = 0) -> str:
    """Fetch a team's primary roster needs ranked by urgency, evaluating starter rating deficits, positional depth, age cliffs, and statistical phase rankings. Accepts team name, abbreviation, or team ID."""
    try:
        s_val = resolve_season(season)
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
def get_team_roster_depth_chart(team_identifier: str, season: int = 0) -> str:
    """Fetch a team's complete depth chart organized by position group (QB, RB, WR, TE, OT, IOL, EDGE, DT, LB, CB, S), showing ratings, age tiers, and roster demographics. Accepts team name, abbreviation, or team ID."""
    try:
        s_val = resolve_season(season)
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
def get_franchise_rebuild_status(team_identifier: str, season: int = 0) -> str:
    """Fetch a franchise's macro rebuild lifecycle stage (Championship Window, Youth Ascent, Roster Purgatory, Ground-Up Rebuild) and recommended draft posture. Accepts team name, abbreviation, or team ID."""
    try:
        s_val = resolve_season(season)
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
                      AND (%s::int IS NULL OR nf.season < %s::int OR (nf.season = %s::int AND nf.week < %s::int))
                    ORDER BY nf.season DESC, nf.week DESC, at.mention_type = 'PRIMARY' DESC, at.mention_count DESC, nf.id DESC
                    LIMIT %s;
                """, (team_id, *_article_cutoff_params(), limit))
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
                      AND (%s::int IS NULL OR nf.season < %s::int OR (nf.season = %s::int AND nf.week < %s::int))
                    ORDER BY nf.season DESC, nf.week DESC, ap.mention_type = 'PRIMARY' DESC, ap.mention_count DESC, nf.id DESC
                    LIMIT %s;
                """, (player_id, *_article_cutoff_params(), limit))
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


@tool
def get_team_injury_report(team_identifier: str, week: int = 0, season: int = 0) -> str:
    """Fetch a team's official weekly injury report, including player name, position, overall rating,
    popularity, injury diagnosis, duration, weeks remaining, IR status, starter status, and key injury flag.
    Accepts team name, city, abbreviation, or team ID."""
    try:
        s_val = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id:
                    return f"Error: Team '{team_identifier}' not found."

                cur.execute("SELECT name, abbreviation, city FROM teams WHERE id = %s;", (team_id,))
                t_row = cur.fetchone()
                team_name = t_row[0] if t_row else str(team_id)
                team_abbr = t_row[1] if t_row else "NFL"
                team_city = t_row[2] if t_row else ""

                w_val = week
                if not w_val or w_val <= 0:
                    cut = cutoff_week(s_val)
                    if cut is not None and context_season() == s_val:
                        w_val = cut
                    else:
                        cur.execute("SELECT current_week FROM league_state WHERE id = 1;")
                        ls_row = cur.fetchone()
                        w_val = ls_row[0] if ls_row and ls_row[0] else 1

                # If no injuries for exact week, check if a recent prior week exists for this team/season
                cur.execute("""
                    SELECT DISTINCT week FROM injuries
                    WHERE team_id = %s AND season = %s AND week <= %s
                    ORDER BY week DESC LIMIT 1;
                """, (team_id, s_val, w_val))
                w_row = cur.fetchone()
                query_week = w_row[0] if w_row else w_val

                # Query starters for this team and season to determine is_starter
                cur.execute("""
                    WITH ranked_roster AS (
                        SELECT 
                            player_id,
                            position,
                            overall_rating,
                            ROW_NUMBER() OVER (PARTITION BY position ORDER BY overall_rating DESC) as pos_rank
                        FROM player_seasons
                        WHERE team_id = %s AND season = %s
                    )
                    SELECT player_id, position, pos_rank FROM ranked_roster;
                """, (team_id, s_val))
                starter_ranks = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

                def check_starter(pid, pos):
                    if pid in starter_ranks:
                        p, rank = starter_ranks[pid]
                    else:
                        return False
                    if p in ("QB", "TE", "LT", "LG", "C", "RG", "RT", "FS", "SS", "K", "P", "FB"):
                        return rank == 1
                    if p in ("HB", "DT", "LEDG", "REDG", "LE", "RE"):
                        return rank <= 2
                    if p in ("WR", "CB", "MIKE", "SAM", "WILL", "MLB", "LOLB", "ROLB"):
                        return rank <= 3
                    return rank == 1

                cur.execute("""
                    SELECT 
                        i.player_id,
                        COALESCE(p.full_name, p.player_name, i.player_name) as player_name,
                        COALESCE(ps.position, i.position) as position,
                        COALESCE(ps.overall_rating, i.ovr) as ovr,
                        COALESCE(ps.popularity_index, 0.0) as pop,
                        i.injury_type,
                        i.duration,
                        i.weeks_remaining,
                        i.is_ir
                    FROM injuries i
                    LEFT JOIN players p ON p.id = i.player_id
                    LEFT JOIN player_seasons ps ON ps.player_id = i.player_id AND ps.season = i.season
                    WHERE i.team_id = %s AND i.season = %s AND i.week = %s
                    ORDER BY COALESCE(ps.overall_rating, i.ovr) DESC, COALESCE(ps.popularity_index, 0.0) DESC;
                """, (team_id, s_val, query_week))
                rows = cur.fetchall()

                if not rows:
                    return json.dumps({
                        "team": f"{team_city} {team_name}".strip(),
                        "team_abbr": team_abbr,
                        "season": s_val,
                        "week": query_week,
                        "total_injured": 0,
                        "key_injuries_count": 0,
                        "injuries": [],
                        "status_note": "No injuries reported on official report. Healthy active roster."
                    }, indent=2)

                injuries = []
                key_count = 0
                for pid, pname, pos, ovr, pop, inj_type, dur, w_rem, is_ir in rows:
                    ovr_val = int(ovr or 0)
                    pop_val = round(float(pop or 0.0), 1)
                    is_starter = check_starter(pid, pos)
                    # National Key Injury: OVR >= 88 OR Pop >= 50 OR (QB and starter)
                    is_key = (ovr_val >= 88) or (pop_val >= 50.0) or (pos == "QB" and is_starter)
                    if is_key:
                        key_count += 1

                    injuries.append({
                        "player_name": pname,
                        "position": pos,
                        "overall_rating": ovr_val,
                        "popularity_index": pop_val,
                        "is_starter": is_starter,
                        "is_key_injury": is_key,
                        "injury_type": inj_type,
                        "duration": dur,
                        "weeks_remaining": int(w_rem or 0),
                        "is_ir": bool(is_ir),
                    })

                return json.dumps({
                    "team": f"{team_city} {team_name}".strip(),
                    "team_abbr": team_abbr,
                    "season": s_val,
                    "week": query_week,
                    "total_injured": len(injuries),
                    "key_injuries_count": key_count,
                    "injuries": injuries,
                    "coverage_guidance": "National writers: only cite key injuries (OVR >= 88, Pop >= 50, or starting QBs). Beat writers: report on any injured starters across offense, defense, and specialists."
                }, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"


