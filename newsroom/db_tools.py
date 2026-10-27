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
from engines.game_script_engine import get_script, render_script
from engines import clutch_engine as clutch
from engines.player_splits import (BLEND_GAMES, DIMENSIONS, MIN_GAMES, compute_splits, defense_groups,
                                   player_lines, stat_group, STATS as SPLIT_STATS)
import decimal
import json
import re

from newsroom.run_context import context_season, context_week, cutoff_week, is_offseason_event_visible, is_visible_week
from newsroom import roster_moves as rm

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

def on_roster_n(season: int) -> int:
    """N in `(ps.released_week IS NULL OR ps.released_week > N)`, the SQL form of roster_moves.on_roster: a player
    is on his team for the article unless released by its cutoff week (any release with no article context)."""
    return rm.roster_week(cutoff_week(season))


def team_label(team_name, released_week, season: int):
    """A player's team for the article, or 'free agent (released by the X, Week N)'."""
    if released_week is not None and not rm.on_roster(released_week, cutoff_week(season)):
        return rm.free_agent_label(team_name, released_week)
    return team_name


@tool
def get_game_box_score(game_id: int) -> str:
    """Fetch the full box score, excitement index, and top player performances for a specific game: passing, rushing and receiving, plus defensive playmakers (tackles, sacks, TFL, INT, pass deflections, forced fumbles, defensive TDs) and kickers when the game's player stats were captured, and each team's third/fourth-down and two-point conversions, red zone, penalties, quarter-by-quarter scoring and time of possession where they were recorded for this game. It ends with the game_script: the game's official story shared by every writer (quarter scores, every scoring play in order with scorer, yardage, drive and clock, lead changes, the winning score, key moments, adjustments and defensive standouts), which you must not contradict."""
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

            # The game's canonical story (§2.14), shared by every writer
            script = get_script(db.db_conn_info, game_id)
            if script:
                result["game_script"] = render_script(script).split("\n")
            return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_clutch_profile(team_identifier: str = "", quarterback: str = "", season: int = 0) -> str:
    """Late-game records from the league's game scripts (the official account of how each game unfolded): fourth-quarter comebacks, game-winning drives, blown double-digit and fourth-quarter leads, records when leading or trailing at halftime and after three quarters, overtime record, points per game by quarter, the biggest comeback win and lead lost, and the games behind each count. Give a team (name or abbreviation) or a quarterback (credited with the games he started); give neither for the league leaders (quarterbacks by comebacks and game-winning drives, teams by blown leads and wins after trailing at halftime). Regular season and postseason are separate."""
    try:
        s_val = resolve_season(season)
        before = cutoff_week(s_val)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                rows = clutch.fetch_rows(cur, s_val)
                if not rows:
                    return f"No game scripts for season {s_val}."
                note = ("From the league's game scripts: cite these as fact; they are the same for every writer. "
                        "A quarterback is credited with the games he started. Regular season and postseason are "
                        "separate; never add them together or compare them with real-NFL career totals.")
                if quarterback:
                    cur.execute("""
                        SELECT p.id, COALESCE(p.full_name, p.player_name) FROM players p
                        JOIN player_seasons pse ON pse.player_id = p.id AND pse.season = %s AND pse.position = 'QB'
                        WHERE p.full_name ILIKE %s OR p.player_name ILIKE %s
                        ORDER BY pse.overall_rating DESC NULLS LAST LIMIT 1
                    """, (s_val, f"%{quarterback.strip()}%", f"%{quarterback.strip()}%"))
                    row = cur.fetchone()
                    if not row:
                        return f"Quarterback '{quarterback}' not found for season {s_val}."
                    prof = clutch.qb_profile(rows, row[0], before)
                    return json.dumps({"quarterback": row[1], "season": s_val, **prof, "note": note}, indent=2)
                if team_identifier:
                    tid = _resolve_team_id(cur, team_identifier)
                    if not tid:
                        return f"Team '{team_identifier}' not found."
                    cur.execute("SELECT name FROM teams WHERE id = %s", (tid,))
                    prof = clutch.team_profile(rows, tid, before)
                    return json.dumps({"team": cur.fetchone()[0], "season": s_val, **prof, "note": note}, indent=2)
                return json.dumps({"season": s_val, "regular_season_leaders": clutch.league_leaders(rows, before),
                                   "note": note}, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"

def _format_playoff_result(result: str | None) -> str:
    mapping = {
        "CHAMPION": "Won Super Bowl",
        "SUPER_BOWL_LOSS": "Super Bowl Runner-Up",
        "CONFERENCE": "Conf Championship",
        "DIVISIONAL": "Divisional Round",
        "WILD_CARD": "Wild Card",
        "MISSED": "Missed Playoffs",
    }
    return mapping.get(result, result or "Missed Playoffs")


def _ordinal_suffix(n: int | None) -> str:
    if not n:
        return "N/A"
    return f"{n}th" if 11 <= (n % 100) <= 13 else f"{n}{['th', 'st', 'nd', 'rd', 'th'][min(n % 10, 4)]}"


def _classify_franchise_state(rows: list) -> str:
    if not rows:
        return "Unknown"
    total_w = sum(r[1] for r in rows)
    total_l = sum(r[2] for r in rows)
    win_pct = total_w / max(1, (total_w + total_l))
    playoff_seasons = sum(1 for r in rows if r[5] and r[5] != "MISSED")
    sb_wins = sum(1 for r in rows if r[5] == "CHAMPION")
    conf_or_better = sum(1 for r in rows if r[5] in ("CHAMPION", "SUPER_BOWL_LOSS", "CONFERENCE"))
    recent_2 = rows[-2:] if len(rows) >= 2 else rows
    recent_sb = any(r[5] in ("CHAMPION", "SUPER_BOWL_LOSS") for r in recent_2)
    recent_wins = [r[1] for r in rows]
    ascending = len(recent_wins) >= 3 and (recent_wins[-1] > recent_wins[-2] >= recent_wins[-3] or (recent_wins[-1] >= 10 and recent_wins[-2] < 9))

    if recent_sb or (sb_wins >= 1 and win_pct >= 0.50) or (conf_or_better >= 2 and win_pct >= 0.58):
        return "Championship Window"
    elif playoff_seasons >= 3 and win_pct >= 0.55:
        return "Perennial Contender"
    elif ascending or (win_pct >= 0.50 and recent_wins[-1] >= 10):
        return "Ascending Core"
    elif win_pct < 0.40 or (recent_wins[-1] <= 6 and win_pct < 0.45):
        return "Rebuilding"
    else:
        return "Roster Purgatory"


def _build_five_year_team_context(cur, team_id: int, season: int) -> dict:
    cur.execute("""
        SELECT season, wins, losses, ties, division_place, playoff_result
        FROM team_season_results
        WHERE team_id = %s AND season < %s AND season >= %s
        ORDER BY season ASC;
    """, (team_id, season, season - 5))
    rows = cur.fetchall()
    if not rows:
        return {
            "five_year_record": "None recorded",
            "five_year_arc": [],
            "franchise_state": "Unknown",
        }
    tot_w = sum(r[1] for r in rows)
    tot_l = sum(r[2] for r in rows)
    tot_t = sum(r[3] for r in rows)
    rec_str = f"{tot_w}-{tot_l}" + (f"-{tot_t}" if tot_t > 0 else "")
    arc = [
        f"{r[0]}: {r[1]}-{r[2]}{f'-{r[3]}' if r[3] else ''} ({_ordinal_suffix(r[4])} in div, {_format_playoff_result(r[5])})"
        for r in rows
    ]
    return {
        "five_year_record": rec_str,
        "five_year_arc": arc,
        "franchise_state": _classify_franchise_state(rows),
    }


def _build_five_year_player_context(cur, player_id: int, player_name: str, position: str, experience: int,
                                    season: int, current_stats: dict) -> dict:
    cur.execute("""
        SELECT ssl.season, t.abbreviation, ssl.games_played,
               ssl.pass_yards, ssl.pass_tds, ssl.interceptions,
               ssl.rush_attempts, ssl.rush_yards, ssl.rush_tds,
               ssl.receptions, ssl.rec_yards, ssl.rec_tds,
               ssl.def_tackles, ssl.def_sacks, ssl.def_ints,
               ssl.def_tfl
        FROM season_stat_lines ssl
        LEFT JOIN teams t ON ssl.team_id = t.id
        WHERE ssl.player_id = %s AND ssl.season < %s AND ssl.season >= %s
        ORDER BY ssl.season ASC;
    """, (player_id, season, season - 5))
    rows = cur.fetchall()

    cur.execute("""
        SELECT season, award_type FROM awards
        WHERE (player_id = %s OR player_name ILIKE %s) AND season < %s AND season >= %s
        ORDER BY season ASC;
    """, (player_id, f"%{player_name}%", season, season - 5))
    awards_by_season = {}
    for sy, aw in cur.fetchall():
        awards_by_season.setdefault(sy, []).append(aw)

    if not rows:
        return {
            "five_year_statline_summary": ["No prior recorded seasons (rookie or first year in database)"],
            "five_year_per_season_averages": {},
            "career_arc_assessment": "Rookie Baseline Season" if experience <= 1 else "No Prior Baseline Recorded",
        }

    summary = []
    tot_games = 0
    tot_pass_yds = 0
    tot_pass_tds = 0
    tot_rush_yds = 0
    tot_rush_tds = 0
    tot_rec = 0
    tot_rec_yds = 0
    tot_rec_tds = 0
    tot_tackles = 0
    tot_sacks = 0.0
    tot_ints = 0

    for r in rows:
        sy, tabbr, gp = r[0], r[1] or "FA", r[2] or 0
        tot_games += gp
        pyds, ptds, pints = r[3] or 0, r[4] or 0, r[5] or 0
        tot_pass_yds += pyds
        tot_pass_tds += ptds
        ryds, rtds = r[7] or 0, r[8] or 0
        tot_rush_yds += ryds
        tot_rush_tds += rtds
        recs, recyds, rectds = r[9] or 0, r[10] or 0, r[11] or 0
        tot_rec += recs
        tot_rec_yds += recyds
        tot_rec_tds += rectds
        tkl, sck, ints = r[12] or 0, float(r[13] or 0.0), r[14] or 0
        tot_tackles += tkl
        tot_sacks += sck
        tot_ints += ints
        tfl = r[15] or 0

        parts = [f"{sy} ({tabbr}): {gp} G"]
        if position == "QB" or pyds > 500:
            parts.append(f"{pyds:,} pass yds, {ptds} TD, {pints} INT")
            if ryds > 50:
                parts.append(f"{ryds} rush yds, {rtds} TD")
        elif position in ("HB", "RB"):
            parts.append(f"{ryds:,} rush yds, {rtds} TD")
            if recs > 10:
                parts.append(f"{recs} rec, {recyds} rec yds")
        elif position in ("WR", "TE"):
            parts.append(f"{recs} rec, {recyds:,} yds, {rectds} TD")
        elif position in ("DT", "DE", "MLB", "OLB", "CB", "FS", "SS"):
            def_str = f"{tkl} tkl"
            if sck > 0:
                def_str += f", {sck:g} sck"
            if tfl > 0 and position in ("DT", "DE", "MLB", "OLB"):
                def_str += f", {tfl} TFL"
            if ints > 0:
                def_str += f", {ints} INT"
            parts.append(def_str)
        elif position in ("K", "P"):
            parts.append(f"{gp} games active")
        else:
            parts.append(f"{gp} games started/played")

        line = ", ".join(parts)
        if sy in awards_by_season:
            line += f" | {', '.join(awards_by_season[sy])}"
        summary.append(line)

    n_seasons = len(rows)
    avg_gp = round(tot_games / n_seasons, 1)
    averages = {"seasons_counted": n_seasons, "avg_games": avg_gp}
    primary_avg = 0.0
    primary_curr = 0.0

    if position == "QB" or tot_pass_yds > 1000:
        averages["avg_pass_yds"] = round(tot_pass_yds / n_seasons)
        averages["avg_pass_tds"] = round(tot_pass_tds / n_seasons, 1)
        primary_avg = tot_pass_yds / n_seasons
        primary_curr = current_stats.get("pass_yards", 0)
    elif position in ("HB", "RB"):
        averages["avg_rush_yds"] = round(tot_rush_yds / n_seasons)
        averages["avg_rush_tds"] = round(tot_rush_tds / n_seasons, 1)
        primary_avg = tot_rush_yds / n_seasons
        primary_curr = current_stats.get("rush_yards", 0)
    elif position in ("WR", "TE"):
        averages["avg_receptions"] = round(tot_rec / n_seasons, 1)
        averages["avg_rec_yds"] = round(tot_rec_yds / n_seasons)
        averages["avg_rec_tds"] = round(tot_rec_tds / n_seasons, 1)
        primary_avg = tot_rec_yds / n_seasons
        primary_curr = current_stats.get("rec_yards", 0)
    elif position in ("DT", "DE", "MLB", "OLB", "CB", "FS", "SS"):
        averages["avg_tackles"] = round(tot_tackles / n_seasons, 1)
        averages["avg_sacks"] = round(tot_sacks / n_seasons, 1)
        averages["avg_ints"] = round(tot_ints / n_seasons, 1)
        primary_avg = (tot_sacks * 25.0 + tot_tackles) / n_seasons
        primary_curr = current_stats.get("sacks", 0.0) * 25.0 + current_stats.get("tackles", 0)

    # Arc assessment
    curr_gp = current_stats.get("games_played", 0)
    if experience <= 1 or not primary_avg or primary_avg <= 0:
        arc = "Rookie Baseline Season"
    elif not curr_gp or curr_gp == 0:
        arc = "Awaiting Regular-Season Snaps"
    else:
        pace = (primary_curr / curr_gp) * 17.0
        ratio = pace / primary_avg
        if ratio >= 1.25:
            arc = "Career-Best Breakout Campaign" if experience <= 4 else "Late-Career Resurgence"
        elif ratio >= 1.10:
            arc = "Surging Above Career Baseline"
        elif 0.85 <= ratio < 1.10:
            arc = "In Prime / Consistent with Career Norms"
        elif ratio < 0.75 and experience >= 7:
            arc = "Age-Related Decline / Slower Pace"
        elif ratio < 0.85:
            arc = "Pacing Below Established Baseline"
        else:
            arc = "Consistent with Career Norms"

    return {
        "five_year_statline_summary": summary,
        "five_year_per_season_averages": averages,
        "career_arc_assessment": arc,
    }


@tool
def get_player_season_stats(player_identifier: str) -> str:
    """Fetch cumulative regular-season stats (passing, rushing, receiving, defense, kicking, punting), a 5-year historical statline summary and trailing per-season averages from league canon, career arc assessment (breakout, prime, decline), a separate postseason line from captured playoff box scores when he has one, Madden OVR, age, experience, awards, popularity, popularity components (SpikeIndex, Baseline Rating, Franchise Platform, Team Success, Awards Equity, Exposure), Playoff Clout, and season touch/yard share (including 3-game rolling usage trends for skill players). Accepts player name (e.g. 'Drake Maye') or player ID."""
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
                        ORDER BY (origin IS NOT NULL), LENGTH(player_name) ASC   -- today's player before a namesake
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
                        ps.a_awards, ps.e_exposure, ps.s_playoff, ss.pass_sacks, ps.released_week
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
                    SELECT season, award_type, position 
                    FROM awards 
                    WHERE player_id = %s OR player_name ILIKE %s
                    ORDER BY season ASC;
                """, (target_id, f"%{row[0]}%"))
                career_awards = [
                    f"{a[0]} {a[1]}" + (f" ({a[2]})" if a[2] and "All-Pro" in a[1] else "")
                    for a in cur.fetchall()
                ]

                # Season touch share / yard share of the team's carries + catches
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
                               COALESCE(ss.rush_yards, 0) + COALESCE(ss.rec_yards, 0), team.touches, team.yards,
                               ps.team_id
                        FROM players p
                        JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                        JOIN season_stats ss ON ss.player_name = p.player_name AND ss.season = %s AND ss.team_id = ps.team_id
                        CROSS JOIN team
                        WHERE p.id = %s LIMIT 1;
                    """, (s_val, target_id, s_val, s_val, s_val, target_id))
                    u = cur.fetchone()
                    if u and u[2]:
                        p_team_id = u[4]
                        touch_share = round(100.0 * u[0] / u[2], 1)
                        usage = {
                            "touch_share_pct": touch_share,
                            "scrimmage_yard_share_pct": round(100.0 * u[1] / u[3], 1) if u[3] else None,
                            "scale_note": "Share of the team's season carries + receptions and scrimmage yards. Receptions, not targets; snaps aren't recorded.",
                        }
                        if p_team_id:
                            cur.execute("""
                                SELECT id FROM games
                                WHERE season = %s AND (home_team_id = %s OR away_team_id = %s)
                                  AND status = 'COMPLETED' AND game_type = 'REGULAR_SEASON'
                                ORDER BY week DESC LIMIT 3;
                            """, (s_val, p_team_id, p_team_id))
                            recent_game_ids = [rg[0] for rg in cur.fetchall()]
                            if len(recent_game_ids) >= 3:
                                cur.execute("""
                                    SELECT COALESCE(SUM(rush_attempts + receptions), 0)
                                    FROM player_stats
                                    WHERE game_id = ANY(%s) AND team_id = %s;
                                """, (recent_game_ids, p_team_id))
                                tm_recent = cur.fetchone()[0]
                                if tm_recent > 0:
                                    cur.execute("""
                                        SELECT COALESCE(SUM(rush_attempts + receptions), 0)
                                        FROM player_stats
                                        WHERE game_id = ANY(%s) AND player_id = %s;
                                    """, (recent_game_ids, target_id))
                                    pl_recent = cur.fetchone()[0]
                                    recent_share = round(100.0 * pl_recent / tm_recent, 1)
                                    usage["recent_3_game_touch_share_pct"] = recent_share
                                    usage["usage_delta_pct"] = round(recent_share - touch_share, 1)
                                    usage["scale_note"] = ("Share of the team's season carries + receptions and scrimmage yards. "
                                                           "recent_3_game_touch_share_pct is over the team's last 3 games, and "
                                                           "usage_delta_pct is the difference from season touch share (+ is surging).")

                # Five-year historical context and baseline
                cur.execute("""
                    SELECT games_played, pass_yards, rush_yards, rec_yards, def_tackles, def_sacks, def_ints
                    FROM season_stat_lines
                    WHERE player_id = %s AND season = %s;
                """, (target_id, s_val))
                s_row = cur.fetchone()
                extra = defense_and_special_teams(cur, s_val, target_id)
                curr_stats = {
                    "games_played": s_row[0] if s_row else (extra.get("games_played") or 0),
                    "pass_yards": (s_row[1] if s_row else (row[5] or 0)) or 0,
                    "rush_yards": (s_row[2] if s_row else (row[8] or 0)) or 0,
                    "rec_yards": (s_row[3] if s_row else (row[11] or 0)) or 0,
                    "tackles": (s_row[4] if s_row else 0) or 0,
                    "sacks": float((s_row[5] if s_row else 0.0) or 0.0),
                    "ints": (s_row[6] if s_row else 0) or 0,
                }
                five_yr = _build_five_year_player_context(cur, target_id, row[0], row[1], row[15] or 0, s_val, curr_stats)

                result = {
                    "player_id": target_id,
                    "name": row[0],
                    "position": row[1],
                    "team": team_label(row[2], row[26], s_val) or "Free Agent",
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
                        "scale_note": "0-100: 40 per Super Bowl MVP, 25 per Super Bowl win played in, 8 per other playoff win played in. Credited across full 10-year league history.",
                    },
                    "career_awards": career_awards if career_awards else ["None recorded"],
                    "preseason_adp_rank": row[13] if row[13] else "Undrafted",
                    "season_usage": usage or "Not applicable (non-skill position or no season stats)",
                    "season_stats": {
                        "passing": f"{row[5] or 0} yds, {row[6] or 0} TD, {row[7] or 0} INT"
                                   + (f", {row[25]} sacks taken" if row[25] is not None and row[5] else ""),
                        "rushing": f"{row[8] or 0} yds, {row[9] or 0} TD",
                        "receiving": f"{row[10] or 0} rec, {row[11] or 0} yds, {row[12] or 0} TD"
                    },
                    "career_arc_assessment": five_yr["career_arc_assessment"],
                    "five_year_statline_summary": five_yr["five_year_statline_summary"],
                    "five_year_per_season_averages": five_yr["five_year_per_season_averages"],
                }
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
    """Fetch complete player profile including 5-year historical statline summary and averages, career arc assessment, age, years of experience, career awards, Madden OVR, physical attributes, season stats, and rolling usage trends. Accepts player name or player ID."""
    try:
        fn = getattr(get_player_season_stats, "func", get_player_season_stats)
        return fn(player_identifier)
    except Exception as e:
        return f"Error: {str(e)}"

@tool
def get_team_context(team_identifier: str) -> str:
    """Fetch a team's current record, 5-year historical record and arc (regular season records, division finishes, playoff exits), franchise state (Championship Window, Perennial Contender, Ascending Core, Rebuilding, Roster Purgatory), watchability index, popularity, Madden ratings, and top 3 players. Accepts team name, abbreviation, or team ID (e.g. 'Patriots', 'NE', or 17)."""
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
                    WHERE ps.team_id = %s AND ps.season = %s AND (ps.released_week IS NULL OR ps.released_week > %s)
                    ORDER BY ps.overall_rating DESC
                    LIMIT 3;
                """, (team_id, s_val, on_roster_n(s_val)))

                top_players = [f"{r[0]} ({r[1]} - {r[2]} OVR)" for r in cur.fetchall()]
                coach = fetch_team_coach(cur, team_id, s_val)
                five_yr = _build_five_year_team_context(cur, team_id, s_val)

                result = {
                    "team": team_row[0],
                    "team_id": team_id,
                    "record": f"{team_row[1]}-{team_row[2]}",
                    "franchise_state": five_yr["franchise_state"],
                    "five_year_record": five_yr["five_year_record"],
                    "five_year_arc": five_yr["five_year_arc"],
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
    """Fetch a team's head coach: tenure (season with the team, who he replaced), offensive scheme and defensive front, coaching tendencies (pass lean and fourth-down aggressiveness measured in this league, man coverage as the staff's real-world philosophy; Low/Medium/High with plain-language meaning), this season's record, regular-season and playoff record with the team, career record (regular season, winning seasons, playoffs, Super Bowls), Super Bowls with the team and Coach of the Year awards. Accepts team name, abbreviation, or team ID."""
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

CAREER_STATS = ("games_played", "completions", "pass_attempts", "pass_yards", "pass_tds", "interceptions",
                "rush_attempts", "rush_yards", "rush_tds", "receptions", "rec_yards", "rec_tds", "def_tackles",
                "def_sacks", "def_tfl", "def_ints", "def_deflections", "def_forced_fumbles", "kick_fgm", "kick_fga",
                "punt_punts", "blk_sacks_allowed")
HISTORY_CAPTURED_FROM = 2035      # seasons before this are reconstructed league history (canon, docs/HISTORY_BACKFILL_PLAN.md)


@tool
def get_player_career(player_identifier: str) -> str:
    """Fetch a player's whole career in this league, season by season since 2026 (the first season the league
    records): team, games and every stat group he has, with career totals. 2026-2034 seasons are the league's
    reconstructed history: canon, so write about them as fact, but there are no game logs or box scores for them.
    Includes his awards, his moves between captured seasons (moved, drafted, joined, free agent, retired; from 2035 on)
    and where his career stands now (active, a free agent, or retired). Accepts a player name or ID."""
    try:
        s_val = resolve_season(0)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                pid = _resolve_player_id(cur, player_identifier)
                if not pid:
                    return f"Player '{player_identifier}' not found."
                cur.execute("SELECT COALESCE(full_name, player_name), origin FROM players WHERE id = %s", (pid,))
                name, origin = cur.fetchone()
                cols = ", ".join(f"l.{c}" for c in CAREER_STATS)
                cur.execute(f"""SELECT l.season, t.abbreviation, l.position, {cols}
                                FROM season_stat_lines l JOIN teams t ON t.id = l.team_id
                                WHERE l.player_id = %s AND l.season <= %s ORDER BY l.season""", (pid, s_val))
                seasons, totals = [], dict.fromkeys(CAREER_STATS, 0)
                for r in cur.fetchall():
                    stats = {c: (float(v) if isinstance(v, decimal.Decimal) else v)
                             for c, v in zip(CAREER_STATS, r[3:]) if v}
                    for c, v in stats.items():
                        totals[c] += v
                    seasons.append({"season": r[0], "team": r[1], "position": r[2],
                                    "history": r[0] < HISTORY_CAPTURED_FROM, **stats})
                cur.execute("SELECT season, award_type FROM awards WHERE player_id = %s AND season <= %s "
                            "ORDER BY season", (pid, s_val))
                awards = [f"{y} {a}" for y, a in cur.fetchall()]
                from engines import season_ledger
                path = season_ledger.career_path(cur, pid) if season_ledger.installed(cur) else None
                return json.dumps({
                    "player": name, "since": seasons[0]["season"] if seasons else None,
                    "moves_between_seasons": [m for m in path["moves"] if m["season"] <= s_val] if path else [],
                    "status_now": path["now"] if path else None,
                    "note": ("Retired before 2035: his whole career is reconstructed league history."
                             if origin == "HISTORY" else
                             "A fill-in from the league's reconstructed history (not a current player)."
                             if origin == "HISTORY_FILLIN" else None),
                    "seasons": seasons, "career_totals": {c: v for c, v in totals.items() if v},
                    "awards": awards}, indent=2, default=str)
    except Exception as e:
        return f"Error: {str(e)}"


@tool
def get_offseason_movement(team_identifier: str = "", season: int = 0) -> str:
    """Fetch the player movement into a season (the player season ledger: last season's rosters against this
    season's opening rosters): retirements, the best players who left and are free agents, players who changed teams,
    and the draft class and other arrivals, league-wide or for one team. A free agent is a player who left and isn't
    recorded as retiring (if the retirement list for that offseason wasn't captured, say he's unsigned, never that he
    retired). Ratings only rank players here: never print an overall. Accepts team name, abbreviation or ID."""
    try:
        s_val = int(season) if season else resolve_season(0)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                from engines import season_ledger
                if not season_ledger.installed(cur):
                    return json.dumps({"season": s_val, "note": "No player ledger recorded yet. Don't describe offseason movement."})
                team_id = None
                if str(team_identifier or "").strip():
                    team_id = _resolve_team_id(cur, team_identifier)
                    if not team_id:
                        return f"Team '{team_identifier}' not found."
                cur.execute("""
                    SELECT x.status, x.source, x.flags, COALESCE(p.full_name, p.player_name),
                           COALESCE(x.exp_after, x.exp_before), f.abbreviation, t.abbreviation,
                           (SELECT position FROM player_seasons ps WHERE ps.player_id = x.player_id
                              AND ps.season IN (x.to_season, x.from_season) ORDER BY ps.season DESC LIMIT 1),
                           (SELECT nt.abbreviation FROM player_seasons ps JOIN teams nt ON nt.id = ps.team_id
                            WHERE ps.player_id = x.player_id AND ps.season = x.to_season AND ps.released_week IS NULL)
                    FROM player_season_transitions x JOIN players p ON p.id = x.player_id
                    LEFT JOIN teams f ON f.id = x.from_team_id LEFT JOIN teams t ON t.id = x.to_team_id
                    WHERE x.to_season = %s AND x.status <> 'RETAINED'
                      AND (%s::int IS NULL OR x.from_team_id = %s OR x.to_team_id = %s)
                    ORDER BY GREATEST(COALESCE(x.ovr_before, 0), COALESCE(x.ovr_after, 0)) DESC, p.id
                """, (s_val, team_id, team_id, team_id))
                rows = cur.fetchall()
                if not rows:
                    return json.dumps({"season": s_val, "note": f"No offseason movement recorded into {s_val}."})
                lists = {"retired": [], "free_agents": [], "changed_teams": [], "drafted": [], "other_arrivals": []}
                scan_missing = False
                for status, source, flags, name, exp, frm, to, pos, now_team in rows:
                    flags = flags or ""
                    who = f"{name} ({pos or '?'})"
                    if status == "RETIRED":
                        lists["retired"].append(f"{who}, last with {frm}")
                    elif status == "FREE_AGENT":
                        scan_missing = scan_missing or "retirement_scan_missing" in flags
                        signed = re.search(r"signed_in_season:week (\d+)", flags)
                        lists["free_agents"].append(f"{who}, left {frm}" + (
                            f"; signed with {now_team or 'a team'} during the season (Week {signed.group(1)})" if signed else ""))
                    elif status == "MOVED":
                        lists["changed_teams"].append(f"{who}: {frm} -> {to}" + (" (trade)" if source == "TRADE" else ""))
                    elif status == "DRAFTED":
                        lists["drafted"].append(f"{who}, {to}")
                    else:
                        lists["other_arrivals"].append(f"{who}, {to}" + (" (undrafted rookie)" if "undrafted_rookie" in flags else ""))
                out = {"season": s_val, "from_season": s_val - 1,
                       "counts": {k: len(v) for k, v in lists.items()},
                       **{k: v[:25] for k, v in lists.items()},
                       "note": "Lists are best players first and capped at 25. "
                               + ("No retirement list was captured for that offseason: call the free agents unsigned "
                                  "or available, never retired. " if scan_missing else "")
                               + "Teams named are where he ended last season and where he opened this one."}
                return json.dumps(out, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"


@tool
def get_team_history(team_identifier: str) -> str:
    """Fetch a franchise's season-by-season history in this league since 2026: record, points, division place,
    playoff seed and result, head coach, GM and the team's award winners, plus totals (playoff trips, Super Bowl trips,
    titles). 2026-2034 are reconstructed league history: canon, write about them as fact. Accepts team name,
    abbreviation, or team ID."""
    try:
        s_val = resolve_season(0)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id:
                    return f"Team '{team_identifier}' not found."
                cur.execute("""SELECT r.season, r.wins, r.losses, r.ties, r.points_for, r.points_against,
                                      r.division_place, r.conference_seed, r.playoff_result, c.coach_name, g.gm_name
                               FROM team_season_results r
                               LEFT JOIN coaches c ON c.id = r.head_coach_id
                               LEFT JOIN gm_profiles g ON g.id = r.gm_id
                               WHERE r.team_id = %s AND r.season < %s ORDER BY r.season""", (team_id, s_val))
                keys = ("season", "wins", "losses", "ties", "points_for", "points_against", "division_place",
                        "conference_seed", "playoff_result", "head_coach", "gm")
                seasons = [dict(zip(keys, r)) for r in cur.fetchall()]
                cur.execute("SELECT season, award_type, COALESCE(player_name, coach_name) FROM awards "
                            "WHERE team_id = %s AND season < %s ORDER BY season", (team_id, s_val))
                awards = {}
                for y, a, n in cur.fetchall():
                    awards.setdefault(y, []).append(f"{a}: {n}")
                for s in seasons:
                    s["history"] = s["season"] < HISTORY_CAPTURED_FROM
                    s["awards"] = awards.get(s["season"], [])
                cur.execute("SELECT name FROM teams WHERE id = %s", (team_id,))
                return json.dumps({
                    "team": cur.fetchone()[0], "seasons": seasons,
                    "totals": {"wins": sum(s["wins"] for s in seasons), "losses": sum(s["losses"] for s in seasons),
                               "playoff_trips": sum(1 for s in seasons if s["playoff_result"] != "MISSED"),
                               "super_bowl_trips": [s["season"] for s in seasons
                                                    if s["playoff_result"] in ("CHAMPION", "SUPER_BOWL_LOSS")],
                               "titles": [s["season"] for s in seasons if s["playoff_result"] == "CHAMPION"]}},
                    indent=2)
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
                        ORDER BY (origin IS NOT NULL), LENGTH(player_name) ASC LIMIT 1
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
                        SELECT age FROM player_seasons ps WHERE team_id = %s AND season = %s AND (ps.released_week IS NULL OR ps.released_week > %s)
                        ORDER BY overall_rating DESC LIMIT 22
                    ) s;
                """, (team_id, s_val, on_roster_n(s_val)))
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
    """Fetch the Coach of the Year race: head coaches ranked by regular-season coaching achievement index (internal COTY score; do not quote the score or bonus numbers in articles, describe the real-world records and turnaround instead). Built from the regular season only. Each candidate shows record and win%, wins above expectation (wins minus expected wins from opponent-adjusted credit), roster outperformance (team OVR rank minus win% rank; positive = winning more than the roster's rating says), improvement over last season's win% (null unless the team's full 17-game regular season last year is recorded), and whether it is the coach's first season with the team."""
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
            "historical_bonus": round(r.get("historical_bonus", 0.0), 1),
            "historical_notes": r.get("historical_notes", []),
        } for r in race[:max(1, int(limit))]]
        note = ("INTERNAL RANKING INDEX: Score blends win% (30%), wins above expectation (20%), roster outperformance (20%) and improvement "
                "over last season (30%), plus bonuses for historical rarity (undefeated season, records broken, 9+ win turnaround). "
                "DO NOT cite 'COTY score', 'coach scores', 'Coach of the Year metric', or 'historical bonus' in published copy. "
                "Instead, describe the coach's real-world football achievements: the record, undefeated season, franchise/NFL records broken, "
                "turnaround, scoring margin, and roster outperformance. Ranks: 1 = best; tied ranks are averaged.")
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
    """Fetch season efficiency from points, yards, and turnovers: Pythagorean wins (the record points for/against predict; pyth_luck = wins minus that, positive = record flatters them), team pace & production (0-100), yards per game passing and rushing, giveaways per game, takeaways per game, and turnover margin; plus situational football: third- and fourth-down conversion rates, two-point tries, red-zone trips and TD rate on offense and allowed on defense, penalties and penalty yards per game, each with a league rank (1 = best). Without a team, returns all 32 teams sorted by pyth_luck; with a team name, abbreviation, or ID, returns that team only."""
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
                        "takeaways_per_game": round(p.get("takeaways_per_game", 0.0), 2),
                        "turnover_margin": int(p.get("turnover_margin", 0)),
                        "situational": situational.get(team_id, "Not captured for this season"),
                    }

                note = ("Pace & production: 35 points and 425 yards per game = 100. Turnover margin is takeaways minus "
                        "giveaways over completed games. Pythagorean wins use raw points; compare with expected_wins from "
                        "get_opponent_adjusted_credit, which also adjusts for schedule. Situational numbers are season totals "
                        "from the team stats through the week shown; ranks are 1-32, 1 = best (fewest penalties, lowest red-zone TD rate allowed). "
                        "Third-down defense isn't recorded as a season total: third downs allowed appear only for games where the opponent's "
                        "line is recorded, so don't cite a season third-down defense rate beyond them.")
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
                           l.kick_fgm, l.kick_fga, l.punt_net_average, ps.released_week
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
                     tkl, sacks, d_ints, ff, fgm, fga, net_avg, rel) = r
                    team = team_label(team, rel, s_val)
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
                        ps.age, ps.years_of_experience, ps.released_week
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
                        "team": team_label(r[3], r[9], s_val),
                        "team_id": r[4] if team_label(r[3], r[9], s_val) == r[3] else None,
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
    """Fetch the Round 1 NFL Draft order (Picks 1-32) for a season: reverse regular-season record (point differential
    breaks ties), with playoff exits in their brackets (missed playoffs 1-18, Wild Card losers 19-24, Divisional
    25-28, Conference 29-30, Super Bowl loser 31, winner 32). Each pick says whether it is locked (that team's
    season is over) or provisional (still alive, or the playoff field isn't set yet)."""
    try:
        from engines.draft_order import compute_draft_order, fetch_games, fetch_teams
        s_val = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                result = compute_draft_order(fetch_teams(cur), fetch_games(cur, s_val, before_week=cutoff_week(s_val)))
        board = result["round_1_order"]
        if not board:
            return f"No draft order data found for Season {s_val}."
        return json.dumps({"season": s_val, "total_picks": len(board),
                           "locked_picks": sum(1 for b in board if b["locked"]),
                           "playoff_field_known": result["playoff_field_known"],
                           "rounds_complete": result["rounds_complete"],
                           "round_1_order": board}, indent=2)
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
                    WHERE ps.team_id = %s AND ps.season = %s AND (ps.released_week IS NULL OR ps.released_week > %s)
                      AND ps.overall_rating IS NOT NULL AND ps.age IS NOT NULL;  -- CPU signings are unrated until a roster scan
                """, (team_id, s_val, on_roster_n(s_val)))
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
                    WHERE ps.team_id = %s AND ps.season = %s AND (ps.released_week IS NULL OR ps.released_week > %s)
                    ORDER BY ps.overall_rating DESC;
                """, (team_id, s_val, on_roster_n(s_val)))
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

                aged = 0
                for name, pos, ovr, age, exp in players:
                    # a player created by a CPU signing (pipeline/load_transactions) has no rating or age until the
                    # next roster scan
                    ovr = ovr or 0
                    if age is not None:
                        tot_age += age
                        aged += 1
                    else:
                        age = 26
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

                avg_age = round(tot_age / aged, 1) if aged else None

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
                    FROM player_seasons ps
                    WHERE team_id = %s AND season = %s AND (ps.released_week IS NULL OR ps.released_week > %s);
                """, (team_id, s_val, on_roster_n(s_val)))
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
                    WHERE ps.team_id = %s AND ps.season = %s AND (ps.released_week IS NULL OR ps.released_week > %s);
                """, (team_id, s_val, on_roster_n(s_val)))
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

def _draft_week(s_val: int) -> int:
    """The week a mock draft is saved under: the run's week when one is set, else the league's current week."""
    week = cutoff_week(s_val)
    if week is not None:
        return week
    return db.get_current_league_state()[1]


def _public_prospect(prospect: dict, grades: dict) -> dict:
    """A mock pick's prospect for writers: the scouting consensus (grade, rank, round label) instead of Madden's
    OVR, which writers never see (team management step 4, Q6)."""
    out = {k: v for k, v in prospect.items() if k != "overall_rating"}
    g = grades.get(prospect.get("prospect_id")) or {}
    out.update(consensus_grade=g.get("grade"), consensus_rank=g.get("consensus_rank"),
               round_label=g.get("round_label"))
    return out


def _scouting_grades(cur, s_val):
    try:
        from engines.scouting_read import public_grades
        return public_grades(cur, s_val)
    except Exception:
        return {}


@tool
def get_mock_draft(season: int = 0, limit: int = 32) -> str:
    """Fetch this week's Round 1 NFL Mock Draft (the media's mock, built from the public scouting consensus):
    pick-by-pick selections, each with the team, the prospect's scouting profile (consensus grade, rank and round
    label; blank fields are null), team-need fit and rationale, and whether the pick is locked (that team's season
    is over) or provisional. Every article this week sees the same mock. The real draft can differ.

    Args:
        season: Evaluation season for draft order and team needs (default: current season).
        limit: Only the first N picks (e.g. 24 while picks 25-32 belong to teams still in the playoffs).
    """
    try:
        from engines.mock_draft_engine import MockDraftEngine
        from pipeline.draft_board import read_mock
        s_val = resolve_season(season)
        week = _draft_week(s_val)
        with psycopg.connect(db.db_conn_info) as conn:
            cur = conn.cursor()
            selections = read_mock(cur, s_val, week)
            source = "saved"
            grades = _scouting_grades(cur, s_val)
            if not selections:
                engine = MockDraftEngine(season=s_val, grades=grades)
                selections = engine.run_mock()
                engine.save(cur, week, selections)
                conn.commit()
                source = "generated"
        limit_val = max(1, int(limit)) if limit else 32
        picks = [dict(p, prospect=_public_prospect(p.get("prospect") or {}, grades)) for p in selections[:limit_val]]
        return json.dumps({
            "evaluation_season": s_val,
            "draft_class_season": s_val + 1,
            "week": week,
            "source": source,
            "total_picks": len(picks),
            "locked_picks": sum(1 for p in picks if p.get("locked")),
            "round_1_mock_draft": picks,
        }, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"


@tool
def get_scouted_draft_board(season: int = 0, limit: int = 32, position: str = "") -> str:
    """Fetch the scouted draft prospect board for the upcoming draft class (season + 1): the public scouting
    consensus (grade 0-100, consensus rank, round label, rank change this week) with Madden's projected pick and
    round, name, position, college, height, weight, age, archetype and notes. In consensus order once the scouting
    board exists, else projected pick order. A field the scan couldn't read is null; never fill it in. For risers,
    fallers and the college storylines behind them use get_draft_risers_fallers.

    Args:
        season: League season (default: current); the class is season + 1.
        limit: Number of prospects to return (default 32).
        position: Optional position or position group filter (QB, OT, EDGE, CB, LB, IOL, WR, S...).
    """
    try:
        from pipeline.draft_board import read_board
        s_val = resolve_season(season)
        draft_season = s_val + 1
        with psycopg.connect(db.db_conn_info) as conn:
            cur = conn.cursor()
            board = read_board(cur, draft_season)
            matching = read_board(cur, draft_season, position=position) if position else board
            grades = _scouting_grades(cur, s_val)
        if not board:
            return f"No scouted draft board for the {draft_season} draft class yet."
        limit_val = max(1, int(limit)) if limit else 32
        if grades:
            matching = sorted(matching, key=lambda p: (grades.get(p.prospect_id, {}).get("consensus_rank") or 9999,
                                                       p.projected_pick))
        prospects = []
        for p in matching[:limit_val]:
            g = grades.get(p.prospect_id) or {}
            prospects.append({
                "consensus_rank": g.get("consensus_rank"), "grade": g.get("grade"), "round_label": g.get("round_label"),
                "rank_change": g.get("delta_rank"),
                "projected_pick": p.projected_pick, "projected_round": p.projected_round, "round_pick": p.round_pick,
                "name": p.full_name, "pos": p.position, "college": p.college, "ht": p.height, "wt": p.weight,
                "age": p.age, "archetype": p.archetype, "notes": p.notes,
            })
        return json.dumps({
            "season": s_val,
            "draft_season": draft_season,
            "filter_position": position.upper() if position else "ALL",
            "total_prospects": len(board),
            "total_matching": len(matching),
            "returned_count": len(prospects),
            "board": "scouting consensus" if grades else "Madden projected order (scouting board not built yet)",
            "prospects": prospects,
        }, indent=2)
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
                        LEFT JOIN player_seasons ps ON p.id = ps.player_id AND ps.source <> 'SYNTH'
                        WHERE p.full_name ILIKE %s OR p.player_name ILIKE %s
                        ORDER BY (p.origin IS NOT NULL), LENGTH(p.full_name) ASC, ps.overall_rating DESC NULLS LAST, p.id ASC LIMIT 1;
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


@tool
def get_record_book(category: str = None, team_identifier: str = None, season: int = 0) -> str:
    """Query the all-time NFL record book and franchise records.
    Provides canonical historical marks, all-time record holders, previous records, and franchise leaderboards.
    - category: optional specific stat category (e.g. 'season_pass_tds', 'Passing TDs in a season', 'season_pass_yards', 'season_rec_tds', 'game_pass_tds', 'season_team_points', 'season_team_wins'). Matches category names or labels flexibly.
    - team_identifier: optional team name, city, abbreviation, or ID to look up that franchise's records.
    - season: season year (default: current season).
    Returns all-time NFL record holders, previous record holders, top-10 leaderboard entries for the category, franchise records, and any records broken or tied this season.
    """
    try:
        s_val = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                # Load teams lookup
                cur.execute("SELECT id, abbreviation, city, name FROM teams;")
                team_map = {r[0]: {"abbr": r[1], "name": f"{r[2]} {r[3]}"} for r in cur.fetchall()}

                # Load categories lookup
                cur.execute("SELECT category, label, scope, subject, better FROM record_categories ORDER BY sort_order;")
                cat_rows = cur.fetchall()
                cat_lookup = {r[0]: {"label": r[1], "scope": r[2], "subject": r[3], "better": r[4]} for r in cat_rows}

                matched_cats = []
                if category:
                    clean_c = category.strip().lower().replace(" ", "_")
                    for c_key, c_info in cat_lookup.items():
                        if clean_c in c_key.lower() or clean_c in c_info["label"].lower().replace(" ", "_") or c_key.lower() in clean_c:
                            matched_cats.append(c_key)
                    if not matched_cats:
                        # Fallback: substring match on label
                        for c_key, c_info in cat_lookup.items():
                            if any(part in c_info["label"].lower() for part in clean_c.split("_") if len(part) > 2):
                                matched_cats.append(c_key)

                resolved_team_id = None
                if team_identifier:
                    resolved_team_id = _resolve_team_id(cur, team_identifier)

                def fmt_num(v):
                    if v is None:
                        return None
                    f = float(v)
                    return int(f) if f.is_integer() else round(f, 2)

                output = {"season": s_val}

                # 1. If specific category or categories matched
                if matched_cats:
                    categories_data = []
                    for c_key in matched_cats[:5]:
                        c_info = cat_lookup[c_key]
                        low = c_info["better"] == "LOW"

                        # Current NFL record holder(s)
                        cur.execute("""
                            SELECT value, holder, season, team_id, source
                            FROM league_records
                            WHERE category = %s;
                        """, (c_key,))
                        nfl_recs = cur.fetchall()
                        nfl_records_list = [{
                            "holder": r[1],
                            "value": fmt_num(r[0]),
                            "season": r[2],
                            "team": team_map.get(r[3], {}).get("name") if r[3] else None,
                            "source": r[4]
                        } for r in nfl_recs]

                        # All-time top 10 entries
                        order_dir = "ASC" if low else "DESC"
                        cur.execute(f"""
                            SELECT value, holder, season, team_id, source
                            FROM league_record_entries
                            WHERE category = %s
                            ORDER BY value {order_dir}
                            LIMIT 10;
                        """, (c_key,))
                        top10_rows = cur.fetchall()
                        top10_list = [{
                            "rank": idx + 1,
                            "holder": r[1],
                            "value": fmt_num(r[0]),
                            "season": r[2],
                            "team": team_map.get(r[3], {}).get("name") if r[3] else None
                        } for idx, r in enumerate(top10_rows)]

                        # Franchise record if team requested
                        team_rec = None
                        if resolved_team_id:
                            cur.execute("""
                                SELECT value, holder, season, source
                                FROM franchise_records_current
                                WHERE team_id = %s AND category = %s;
                            """, (resolved_team_id, c_key))
                            frow = cur.fetchone()
                            if frow:
                                team_rec = {
                                    "team": team_map.get(resolved_team_id, {}).get("name"),
                                    "holder": frow[1],
                                    "value": fmt_num(frow[0]),
                                    "season": frow[2],
                                }

                        categories_data.append({
                            "category": c_key,
                            "label": c_info["label"],
                            "scope": c_info["scope"],
                            "nfl_all_time_record": nfl_records_list,
                            "nfl_all_time_top_10": top10_list,
                            "franchise_record": team_rec
                        })
                    output["categories"] = categories_data

                # 2. If team requested (and no single category or in addition)
                elif resolved_team_id:
                    cur.execute("""
                        SELECT category, label, value, holder, season
                        FROM franchise_records_current
                        WHERE team_id = %s
                        ORDER BY category;
                    """, (resolved_team_id,))
                    f_records = []
                    for r in cur.fetchall():
                        cat_key = r[0]
                        f_records.append({
                            "category": cat_key,
                            "label": r[1],
                            "holder": r[3],
                            "value": fmt_num(r[2]),
                            "season": r[4],
                        })
                    output["team"] = team_map.get(resolved_team_id, {}).get("name")
                    output["franchise_records"] = f_records

                # 3. Overview of all NFL all-time records
                else:
                    cur.execute("""
                        SELECT lr.category, lr.label, lr.value, lr.holder, lr.season, lr.team_id, lr.source
                        FROM league_records lr
                        JOIN record_categories rc ON lr.category = rc.category
                        ORDER BY rc.sort_order;
                    """)
                    all_nfl = []
                    for r in cur.fetchall():
                        all_nfl.append({
                            "category": r[0],
                            "label": r[1],
                            "value": fmt_num(r[2]),
                            "holder": r[3],
                            "season": r[4],
                            "team": team_map.get(r[5], {}).get("name") if r[5] else None,
                        })
                    output["nfl_all_time_records"] = all_nfl

                # 4. Check for any records broken or tied this season
                from engines.records_engine import evaluate
                eval_res = evaluate(cur, s_val)
                season_records = [
                    {
                        "type": e["type"],
                        "result": e["result"],
                        "category": e["category"],
                        "label": e["label"],
                        "holder": e["holder"],
                        "team": e.get("team"),
                        "value": fmt_num(e["value"]),
                        "previous_record": fmt_num(e.get("old_value") or e.get("nfl_record")),
                        "previous_holders": e.get("old_holders") or e.get("nfl_record_holders"),
                        "week": e.get("week")
                    }
                    for e in eval_res.get("events", [])
                    if e.get("type") == "nfl_record" or (e.get("type") == "top10" and e.get("result") in ("broke", "tied"))
                ]
                output["records_set_this_season"] = season_records

                return json.dumps(output, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"






# ================================================================================================================
# Roster moves, contracts and the cap (docs/team_management/05_NEWSROOM_INTEGRATION_PLAN.md §2.4). Each tool is a
# fetch_*(cur, ...) plus the pure formatters in newsroom/roster_moves.py. The contract and cap tables have no
# per-week history, so like rosters they show the current books; transactions follow the week cutoff (§2.2).
# ================================================================================================================

ACTING_TEAM = {"RELEASED": "from"}       # every other type acts as its to_team (signing, re-sign, restructure, trade)


def _resolve_player_id(cur, identifier: str):
    ident = str(identifier).strip()
    if ident.isdigit():
        return int(ident)
    cur.execute("""
        SELECT id FROM players WHERE player_name ILIKE %s OR full_name ILIKE %s
        ORDER BY (origin IS NOT NULL), LENGTH(player_name) ASC LIMIT 1;
    """, (f"%{ident}%", f"%{ident}%"))
    row = cur.fetchone()
    return row[0] if row else None


def _txn_context():
    """(article season, article week, offseason signings visible) for txn_visible."""
    return context_season(), context_week(), is_offseason_event_visible("signings")


def fetch_depth_ranks(cur, season: int, on_roster_week) -> tuple:
    """({player_id: rank among on-roster players at his position on his team}, {player_id: rank counting released
    players on their old team}). Rank 1 = best OVR."""
    cur.execute("""
        SELECT player_id, team_id, position, overall_rating, released_week
        FROM player_seasons WHERE season = %s AND team_id IS NOT NULL;
    """, (season,))
    rows = cur.fetchall()
    def ranks(keep):
        groups = {}
        for pid, tid, pos, ovr, rel in rows:
            if keep(rel):
                groups.setdefault((tid, pos), []).append((-(ovr or 0), pid))
        out = {}
        for members in groups.values():
            for i, (_, pid) in enumerate(sorted(members), 1):
                out[pid] = i
        return out
    return ranks(lambda rel: rm.on_roster(rel, on_roster_week)), ranks(lambda rel: True)


def fetch_moves(cur, season: int, team_id=None, visible_only: bool = True, ctx=None) -> list:
    """This season's transactions as dicts for roster_moves (newest first), with the facts notability and move
    lines need, filtered to what the current article may see. ctx = (season, week, offseason signings visible)
    overrides the thread's article context (the briefs are built outside it)."""
    ctx = ctx or _txn_context()
    cur.execute("""
        SELECT t.id, t.season, t.week, c.week, t.type, t.player_id, t.player_name, p.full_name, t.position,
               t.from_team_id, t.to_team_id, ft.name, tt.name, t.contract_years, t.contract_value, t.flags,
               t.raw_text, ps.popularity_index, ps.overall_rating, ps.team_id, ps.released_week
        FROM transactions t
        LEFT JOIN captures c ON c.id = t.capture_id
        LEFT JOIN players p ON p.id = t.player_id
        LEFT JOIN player_seasons ps ON ps.player_id = t.player_id AND ps.season = t.season
        LEFT JOIN teams ft ON ft.id = t.from_team_id
        LEFT JOIN teams tt ON tt.id = t.to_team_id
        WHERE t.season = %s AND (%s::int IS NULL OR t.from_team_id = %s OR t.to_team_id = %s)
        ORDER BY t.week DESC NULLS FIRST, t.id DESC;
    """, (season, team_id, team_id, team_id))
    keys = ("id", "season", "week", "capture_week", "type", "player_id", "player_name", "full_name", "position",
            "from_team_id", "to_team_id", "from_team", "to_team", "contract_years", "contract_value", "flags",
            "raw_text", "popularity", "ovr", "current_team_id", "released_week")
    rows = [dict(zip(keys, r)) for r in cur.fetchall()]
    if visible_only:
        rows = rm.visible_rows(rows, *ctx)
    if not rows:
        return rows

    pids = [r["player_id"] for r in rows if r["player_id"]]
    cur.execute("""
        SELECT DISTINCT ON (player_id) player_id, cut_penalty FROM player_contracts
        WHERE season = %s AND status = 'RELEASED' AND player_id = ANY(%s) ORDER BY player_id, id DESC;
    """, (season, pids))
    dead = dict(cur.fetchall())
    cur.execute("""
        SELECT extended_transaction_id, season + years_remaining FROM player_contracts
        WHERE extended_transaction_id = ANY(%s) AND status = 'ACTIVE';
    """, ([r["id"] for r in rows],))
    starts = dict(cur.fetchall())
    cut = ctx[1] if ctx[0] == season else cutoff_week(season)
    current, with_released = fetch_depth_ranks(cur, season, rm.roster_week(cut))

    signed_after = {}
    for r in sorted(rows, key=lambda r: (r["week"] if r["week"] is not None else 99, r["id"])):
        if r["type"] == "SIGNED" and r["player_id"]:
            signed_after.setdefault(r["player_id"], []).append(r)
    for r in rows:
        r["team"] = r["from_team"] if ACTING_TEAM.get(r["type"]) == "from" else (r["to_team"] or r["from_team"])
        r["from_team"] = r["from_team"] if r["type"] in ("SIGNED", "TRADED") else None
        r["practice_squad"] = "practice_squad" in (r["flags"] or "")
        pid = r["player_id"]
        if r["type"] == "RELEASED":
            r["dead_money"] = dead.get(pid)
            rank = with_released.get(pid)
            later = [s for s in signed_after.get(pid, [])
                     if (s["week"] if s["week"] is not None else 99) >= (r["week"] if r["week"] is not None else 99)
                     and s["id"] > r["id"]]
            if later:
                r["later_team"], r["later_week"] = later[0]["to_team"], later[0]["week"]
        else:
            rank = current.get(pid) if r["current_team_id"] == r["to_team_id"] else None
            if r["type"] == "SIGNED" and rank:
                r["depth_slot"] = rank
        r["is_starter"] = rm.is_starter_rank(r["position"], rank)
        if r["type"] in ("RE_SIGNED", "EXTENDED") and r["id"] in starts:
            r["new_deal_start"] = starts[r["id"]]
    return rows


def fetch_cap_rows(cur, season: int) -> list:
    """Every team's team_cap_seasons row for the season with its basis label."""
    cur.execute("""
        SELECT t.id, t.name, t.abbreviation, c.cap, c.adjusted_cap, c.cap_space, c.salaries, c.cap_penalty,
               c.total_spend, c.next_rookie_reserve, c.scanned_at, c.derived_at, cap.week,
               (SELECT COUNT(*) FROM transactions x
                 WHERE x.season = c.season AND (x.from_team_id = t.id OR x.to_team_id = t.id)
                   AND c.scanned_at IS NOT NULL AND x.created_at > c.scanned_at)
        FROM team_cap_seasons c JOIN teams t ON t.id = c.team_id
        LEFT JOIN captures cap ON cap.id = c.capture_id
        WHERE c.season = %s ORDER BY c.cap_space DESC NULLS LAST;
    """, (season,))
    keys = ("team_id", "team", "abbreviation", "cap", "adjusted_cap", "cap_space", "salaries", "cap_penalty",
            "total_spend", "next_rookie_reserve", "scanned_at", "derived_at", "capture_week", "moves_since")
    out = []
    for i, r in enumerate(cur.fetchall(), 1):
        row = dict(zip(keys, r))
        row["cap_space_rank"] = i
        row["derived"] = rm.cap_is_derived(row["scanned_at"], row["derived_at"])
        row["basis"] = rm.cap_basis(rm.scan_label(row["capture_week"], row["scanned_at"]), row["derived"],
                                    row["moves_since"])
        out.append(row)
    return out


def fetch_team_contracts(cur, season: int, team_id=None, contract_id=None) -> list:
    """A team's ACTIVE contracts (or one contract by id) with every year's hit and projected flag."""
    cur.execute("""
        SELECT pc.id, pc.player_id, COALESCE(p.full_name, p.player_name), ps.position, pc.length, pc.years_remaining,
               pc.total_value, pc.bonus, pc.cut_savings, pc.cut_penalty, pc.no_trade_clause, pc.incentive, pc.source,
               pc.scanned_at, cap.week, pc.extension_years, pc.extension_value, pc.extended_transaction_id
        FROM player_contracts pc
        JOIN players p ON p.id = pc.player_id
        LEFT JOIN player_seasons ps ON ps.player_id = pc.player_id AND ps.season = pc.season
        LEFT JOIN captures cap ON cap.id = pc.capture_id
        WHERE pc.season = %s AND ((%s::int IS NOT NULL AND pc.id = %s)
                                  OR (%s::int IS NULL AND pc.team_id = %s AND pc.status = 'ACTIVE'));
    """, (season, contract_id, contract_id, contract_id, team_id))
    keys = ("contract_id", "player_id", "name", "position", "length", "years_remaining", "total_value", "bonus",
            "cut_savings", "cut_penalty", "no_trade_clause", "incentive", "source", "scanned_at", "capture_week",
            "extension_years", "extension_value", "extended_transaction_id")
    contracts = [dict(zip(keys, r)) for r in cur.fetchall()]
    if contracts:
        cur.execute("SELECT contract_id, year, cap_hit, projected FROM player_contract_years WHERE contract_id = ANY(%s);",
                    ([c["contract_id"] for c in contracts],))
        years = {}
        for cid, yr, hit, proj in cur.fetchall():
            years.setdefault(cid, []).append((yr, hit, bool(proj)))
        for c in contracts:
            c["years"] = years.get(c["contract_id"], [])
    return contracts


def contract_summary(c, season: int) -> dict:
    """One contract as writer facts (§2.1): scanned or signing basis, NULLs as 'not known', projected years `est.`."""
    this_year = next(((h, p) for y, h, p in c.get("years", []) if y == season), (None, False))
    out = {
        "player": f"{c['name']} ({c.get('position') or '?'})",
        "cap_hit_this_season": rm.est_text(this_year[0]) if this_year[1] else rm.money_text(this_year[0]),
        "length": c["length"],
        "years_remaining": c["years_remaining"],
        "through": season + (c["years_remaining"] or 1) - 1,
        "total_value": rm.money_text(c["total_value"]),
        "bonus": rm.money_text(c["bonus"]),
        "cut_savings": rm.money_text(c["cut_savings"]),
        "cut_penalty": rm.money_text(c["cut_penalty"]),
        "no_trade_clause": bool(c["no_trade_clause"]),
        "incentive": "yes (details not known)" if c.get("incentive") else "none",
        "basis": rm.contract_basis(c["source"], rm.scan_label(c.get("capture_week"), c.get("scanned_at"))),
    }
    yh = rm.year_hits([y for y in c.get("years", []) if y[0] > season])
    if yh:
        out["future_cap_hits"] = yh
    return out


def _extension_visible(cur, txn_id) -> bool:
    if not txn_id:
        return False
    cur.execute("""
        SELECT t.season, t.week, c.week FROM transactions t LEFT JOIN captures c ON c.id = t.capture_id
        WHERE t.id = %s;
    """, (txn_id,))
    r = cur.fetchone()
    return bool(r) and rm.txn_visible(r[0], r[1], r[2], *_txn_context())


@tool
def get_team_cap_sheet(team_identifier: str = "", season: int = 0, limit: int = 20) -> str:
    """Fetch a team's salary cap sheet: cap space and its league rank, adjusted cap, salaries, dead money, total spend and next year's rookie reserve; its largest cap hits with contract terms (through year, total, bonus, cut savings, dead money if cut, no-trade clause); its contract-year players (expiring deals); committed cap by future year; and a count of its moves this season. No team = all 32 teams by cap space. Call it only when the cap or a contract genuinely bears on your story; most articles need no cap numbers. Every figure carries its basis: 'est.' and 'estimated' figures are approximate, 'not known' is never filled in."""
    try:
        s_val = resolve_season(season)
        lim = max(1, min(int(limit or 20), 60))
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                caps = fetch_cap_rows(cur, s_val)
                if not str(team_identifier or "").strip():
                    if not caps:
                        return json.dumps({"season": s_val, "note": "No cap data recorded for this season. Don't cite cap figures."})
                    return json.dumps({
                        "season": s_val,
                        "teams_by_cap_space": [
                            f"{c['cap_space_rank']}. {c['team']}: {rm.money_text(c['cap_space'])} in space, "
                            f"{rm.money_text(c['total_spend'])} total spend, {rm.money_text(c['cap_penalty'])} dead money "
                            f"({c['basis']})" for c in caps],
                        "note": rm.MONEY_NOTE,
                    }, indent=2)
                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id:
                    return f"Team '{team_identifier}' not found."
                cap = next((c for c in caps if c["team_id"] == team_id), None)
                if not cap:
                    return json.dumps({"season": s_val, "note": "No cap data recorded for this team. Don't cite cap figures."})
                contracts = fetch_team_contracts(cur, s_val, team_id)
                def hit(c):
                    return next((h for y, h, p in c.get("years", []) if y == s_val), 0) or 0
                top = sorted(contracts, key=hit, reverse=True)[:lim]
                moves = fetch_moves(cur, s_val, team_id)
                return json.dumps({
                    "team": cap["team"],
                    "season": s_val,
                    "cap_basis": cap["basis"],
                    "cap": rm.money_text(cap["cap"]),
                    "adjusted_cap": rm.money_text(cap["adjusted_cap"]),
                    "cap_space": rm.money_text(cap["cap_space"]),
                    "cap_space_rank": f"{cap['cap_space_rank']} of {len(caps)} (1 = most space)",
                    "salaries": rm.money_text(cap["salaries"]),
                    "dead_money": rm.money_text(cap["cap_penalty"]),
                    "total_spend": rm.money_text(cap["total_spend"]),
                    "next_rookie_reserve": rm.money_text(cap["next_rookie_reserve"]),
                    "top_contracts": [contract_summary(c, s_val) for c in top],
                    "expiring": [f"{c['name']} ({c.get('position') or '?'}): {rm.money_text(hit(c))} this season"
                                 for c in sorted(contracts, key=hit, reverse=True) if c["years_remaining"] == 1],
                    "committed_by_year": rm.committed_by_year(
                        [y for c in contracts for y in c.get("years", [])], s_val),
                    "recent_moves": f"{len(moves)} moves this season; call get_league_transactions with this team for them.",
                    "note": rm.MONEY_NOTE + " 'expiring' players are in a contract year.",
                }, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"


@tool
def get_player_contract(player_identifier: str, season: int = 0) -> str:
    """Fetch a player's contract: team, length, years remaining, the year it runs through, total value, bonus, cut savings, dead money if cut, no-trade clause, incentive flag, this season's and future cap hits, any new deal he signed (re-signing or extension terms), how his contract changed this season (released, traded, replaced) and his moves this season. Accepts player name or ID. Call it only when his contract bears on the story. Figures marked 'est.' are projected; 'not known' is never filled in."""
    try:
        s_val = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                pid = _resolve_player_id(cur, player_identifier)
                if not pid:
                    return f"Player '{player_identifier}' not found in database."
                cur.execute("""
                    SELECT COALESCE(p.full_name, p.player_name), ps.position, t.name, ps.released_week
                    FROM players p LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                    LEFT JOIN teams t ON t.id = ps.team_id WHERE p.id = %s;
                """, (s_val, pid))
                name, pos, team, released_week = cur.fetchone() or (str(pid), None, None, None)
                moves = [r for r in fetch_moves(cur, s_val) if r["player_id"] == pid]
                cur.execute("""
                    SELECT pc.id, pc.status, t.name, pc.total_value, pc.source FROM player_contracts pc
                    JOIN teams t ON t.id = pc.team_id
                    WHERE pc.player_id = %s AND pc.season = %s ORDER BY pc.id;
                """, (pid, s_val))
                history = cur.fetchall()
                result = {"player": name, "position": pos, "season": s_val}
                if not rm.on_roster(released_week, rm.roster_week(cutoff_week(s_val))):
                    result["status"] = rm.free_agent_label(team, released_week)
                else:
                    result["team"] = team or "free agent"
                active = next((h for h in history if h[1] == "ACTIVE"), None)
                if active:
                    c = fetch_team_contracts(cur, s_val, contract_id=active[0])[0]
                    ext_ok = _extension_visible(cur, c["extended_transaction_id"])
                    if c["extended_transaction_id"] and not ext_ok:
                        # an old week being regenerated must not see a later re-signing's projected years
                        c["years"] = [y for y in c["years"] if not y[2]]
                    result["contract"] = contract_summary(c, s_val)
                    result["contract"]["team"] = active[2]
                    if c["extension_years"] and ext_ok:
                        start = s_val + (c["years_remaining"] or 0)
                        result["new_deal"] = rm.extension_text(c["extension_years"], c["extension_value"], start)
                else:
                    result["contract"] = "No contract on file. Don't invent one."
                labels = {"SUPERSEDED": "replaced by a newer record", "RELEASED": "ended by his release",
                          "TRADED": "moved in a trade"}
                changes = [f"{tname} deal worth {rm.money_text(total)} ({'from the signing' if src == 'TRANSACTION' else 'scanned'}): "
                           f"{labels.get(status, status.lower())}"
                           for _, status, tname, total, src in history if status != "ACTIVE"]
                if changes:
                    result["contract_changes_this_season"] = changes
                result["moves_this_season"] = [rm.move_line(r, with_notable=False) for r in moves]
                result["note"] = rm.MONEY_NOTE
                return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error: {str(e)}"


@tool
def get_league_transactions(team_identifier: str = "", week: int = 0, types: str = "", position: str = "",
                            notable_only: bool = False, limit: int = 40, season: int = 0) -> str:
    """Fetch the league's roster moves this season (signings, releases, re-signings, extensions, restructures) and its executed trades, newest week first, one line each with the terms, dead money for releases, the new depth-chart slot for signings and a notable flag; each executed trade is one line with the players and picks that moved, their cap hits and each team's cap change. Filter by team, a single week, types (comma list: SIGNED,RELEASED,RE_SIGNED,EXTENDED,RESTRUCTURED,TRADED), position, or notable_only. Call it only when a move bears on your story; most articles mention none."""
    try:
        s_val = resolve_season(season)
        lim = max(1, min(int(limit or 40), 200))
        ctx_season, ctx_week, _ = _txn_context()
        if week and ctx_season == s_val and ctx_week is not None and int(week) > ctx_week:
            return f"Week {week} moves aren't visible yet: this article is published before Week {ctx_week}'s games."
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                team_id = None
                if str(team_identifier or "").strip():
                    team_id = _resolve_team_id(cur, team_identifier)
                    if not team_id:
                        return f"Team '{team_identifier}' not found."
                rows = [r for r in fetch_moves(cur, s_val, team_id) if r["type"] != "TRADED"]
                trades, names = fetch_trades(cur, s_val), _team_names(cur)
                if team_id:
                    cur.execute("SELECT abbreviation FROM teams WHERE id = %s;", (team_id,))
                    abbr = cur.fetchone()[0]
                    trades = [t for t in trades if abbr in (t["team_a"], t["team_b"])]
    except Exception as e:
        return f"Error: {str(e)}"
    if week:
        rows = [r for r in rows if r["week"] == int(week)]
        trades = [t for t in trades if t["week"] == int(week)]
    wanted = {t.strip().upper().replace("-", "_") for t in str(types or "").split(",") if t.strip()}
    if wanted:
        rows = [r for r in rows if r["type"] in wanted]
        if "TRADED" not in wanted:
            trades = []
    if position:
        rows = [r for r in rows if (r["position"] or "").upper() == position.strip().upper()]
        trades = [t for t in trades if any((l.get("position") or "").upper() == position.strip().upper()
                                           for l in t["legs"])]
    rm.annotate(rows)
    if notable_only:
        rows = [r for r in rows if r["notable"]]
    rows.sort(key=lambda r: (-(r["week"] if r["week"] is not None else 99), -int(r["notable"]), -r["rank_score"]))
    unmatched = [f"{rm.week_label(r)} · {r['type']} · {rm.display_name(r)} ({r['position'] or '?'})"
                 for r in rows if not r["player_id"]]
    counts = rm.counts_by_type(rows)
    if trades:
        counts["TRADED"] = len(trades)
    return json.dumps({
        "season": s_val,
        "count": len(rows) + len(trades),
        "counts": counts,
        "trades": [rm.trade_line(t, names) for t in sorted(trades, key=lambda t: -(t["week"] or 99))][:lim],
        "moves": [rm.move_line(r) for r in rows if r["player_id"]][:lim],
        "rows_with_no_player": unmatched,
        "note": ("Moves headed Week N happened before Week N's games. `trades` lists executed trades only: each is "
                 "fact. Talks that never became a trade are rumors (get_trade_rumors), never reported as done. "
                 + rm.TRADE_NOTE + " " + rm.MONEY_NOTE),
    }, indent=2)


# ----------------------------------------------------------------------------------------------------------------
# Trades and rumors (05_NEWSROOM_INTEGRATION_PLAN.md Phase 8): step 3's read helpers (engines/front_office.py),
# cut off for the article's week and formatted by roster_moves. Nothing here writes.
# ----------------------------------------------------------------------------------------------------------------

def _team_names(cur) -> dict:
    cur.execute("SELECT abbreviation, name FROM teams WHERE abbreviation IS NOT NULL;")
    return dict(cur.fetchall())


def fetch_trades(cur, season: int, ctx=None) -> list:
    """Executed GM trades of the season the article may see (front_office.executed_trades, oldest first). [] before
    step 3's tables exist."""
    from engines import front_office as fo
    ctx = ctx or _txn_context()
    if not fo.installed(cur):
        return []
    return [t for t in fo.executed_trades(cur, season) if rm.trade_visible(season, t["week"], *ctx)]


def fetch_rumors(cur, season: int, ctx=None) -> list:
    """Rumor material the article may see: rumor_ok proposals that were never executed (front_office.trade_rumors),
    made before the article's week, only through the deadline, each with its `outcome` ('stalled' or 'open').
    Another season's article gets none (rumors are live news)."""
    from engines import front_office as fo
    ctx_season, ctx_week, _ = ctx or _txn_context()
    if not fo.installed(cur):
        return []
    if ctx_season is not None and ctx_week is not None:
        if ctx_season != season or not rm.rumor_visible(ctx_week - 1, ctx_week):
            return []
        upto = ctx_week - 1
    else:
        ctx_week, upto = None, rm.REGULAR_SEASON_LAST_WEEK
    rows = fo.trade_rumors(cur, season, upto)
    if not rows:
        return []
    cur.execute("SELECT id, status, expires_week FROM gm_trade_proposals WHERE id = ANY(%s);",
                ([r["rumor_id"] for r in rows],))
    info = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    out = []
    for r in rows:
        status, expires = info.get(r["rumor_id"], ("EXECUTED", None))
        if status == "EXECUTED" or not rm.rumor_visible(r["week"], ctx_week):
            continue
        out.append({**r, "outcome": rm.rumor_outcome(status, expires, ctx_week)})
    return out


def new_week_trades(cur, season: int, week: int, team_abbr: str = None) -> tuple:
    """(executed trades, rumors) new for the Week `week` edition: last week's executed trades and the rumors from
    last week's proposals (roster_moves.new_trades / rumor_visible), optionally only those of one team."""
    ctx = (season, week, True)
    trades = rm.new_trades(fetch_trades(cur, season, ctx=ctx), week)
    rumors = [r for r in fetch_rumors(cur, season, ctx=ctx) if r["week"] == week - 1]
    if team_abbr:
        trades = [t for t in trades if team_abbr in (t["team_a"], t["team_b"])]
        rumors = [r for r in rumors if team_abbr in r["teams"]]
    return trades, rumors


@tool
def get_trade_rumors(team_identifier: str = "", season: int = 0) -> str:
    """Fetch this season's trade rumors: real talks between two front offices that did not become a trade, through the Week 8 trade deadline. Each gives the two teams, who made the call, the kinds of assets (positions or draft picks, never names or figures) and whether talks stalled. A rumor is never a done deal: report it only as talks. Optional team filter. Call it only when trade talk bears on your story; executed trades are in get_league_transactions."""
    try:
        s_val = resolve_season(season)
        ctx_season, ctx_week, _ = _txn_context()
        if ctx_season == s_val and ctx_week is not None and ctx_week > rm.RUMOR_LAST_EDITION_WEEK:
            return json.dumps({"season": s_val, "rumors": [], "note": (
                f"The Week {rm.TRADE_DEADLINE_WEEK} trade deadline has passed: rumors run only through it. Executed "
                "trades are in get_league_transactions.")}, indent=2)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                abbr = None
                if str(team_identifier or "").strip():
                    team_id = _resolve_team_id(cur, team_identifier)
                    if not team_id:
                        return f"Team '{team_identifier}' not found."
                    cur.execute("SELECT abbreviation FROM teams WHERE id = %s;", (team_id,))
                    abbr = cur.fetchone()[0]
                rumors, names = fetch_rumors(cur, s_val), _team_names(cur)
    except Exception as e:
        return f"Error: {str(e)}"
    if abbr:
        rumors = [r for r in rumors if abbr in r["teams"]]
    return json.dumps({
        "season": s_val,
        "count": len(rumors),
        "rumors": [rm.rumor_line(r, names) for r in sorted(rumors, key=lambda r: -r["week"])],
        "note": rm.RUMOR_NOTE,
    }, indent=2)


# ------------------------------------------------- draft scouting (team management step 4 / step 5 Phase 9) ----
_STREAK_WORDS = {2: "two", 3: "three", 4: "four", 5: "five"}


def draft_mover_line(r: dict) -> dict:
    """One riser or faller for writers: public consensus fields and the stored events behind the move (pure)."""
    return {"name": (r.get("full_name") or "").title(), "pos": r.get("position"), "college": r.get("college"),
            "class_year": r.get("class_year"), "consensus_rank": r.get("rank"), "grade": r.get("grade"),
            "round_label": r.get("round_label"), "rank_change": r.get("delta_rank"),
            "grade_change": r.get("delta_grade"),
            "why": [e["blurb"] or e["headline"] for e in r.get("events") or []][:3]}


def draft_record_facts(rec: dict) -> dict:
    """A team's draft track record for writers: each scored pick's label and the streak sentences (pure). Never a
    quality score."""
    picks = [{"draft": p["draft_season"], "round": p["round"], "pick": p["pick"], "player": p["name"],
              "pos": p["position"], "verdict": p["label"], "seasons_since_draft": p["years"]} for p in rec["picks"]]
    lines = []
    for key, what in (("first_round_misses", "first-round misses"), ("first_round_hits", "first-round hits")):
        n = (rec.get("streaks") or {}).get(key) or 0
        if n >= 2:
            lines.append(f"{_STREAK_WORDS.get(n, n)} straight {what}")
        elif n == 1:
            lines.append(f"last year's first-round pick: {'a miss' if 'misses' in key else 'a hit'}")
    return {"picks": picks, "streaks": lines}


@tool
def get_draft_risers_fallers(season: int = 0, limit: int = 10) -> str:
    """Fetch this week's draft-board risers and fallers for the incoming class (season + 1): each prospect's
    consensus rank and grade (0-100), round label, how far he moved since the last board, and the college
    storylines behind the move (games, injuries that never cost him time, all-star games, the combine, pro days,
    visits). The college storylines are Mu's own scouting record: cite them as written, never invent stats beyond
    them. Respects the article's week. No Madden ratings."""
    try:
        from engines.scouting_read import risers_fallers
        s_val = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                rf = risers_fallers(cur, s_val, limit=max(1, min(int(limit or 10), 25)))
    except Exception as e:
        return f"Error: {str(e)}"
    if not rf:
        return f"No scouting board for the {resolve_season(season) + 1} draft class is visible yet."
    return json.dumps({"draft_class": rf["draft_season"], "board_week": rf["week"], "offseason_stage": rf["stage"],
                       "risers": [draft_mover_line(r) for r in rf["risers"]],
                       "fallers": [draft_mover_line(r) for r in rf["fallers"]]}, indent=2)


@tool
def get_team_draft_record(team_identifier: str, season: int = 0) -> str:
    """Fetch a team's recent draft track record: its Round 1-2 picks of the last three drafts, each judged HIT, OK,
    MISS or BUST against what that draft slot usually produces (a rookie's hidden dev trait isn't judged until it's
    revealed), and streaks such as "three straight first-round misses". Use the verdicts as written."""
    try:
        from engines.fo_quality import team_draft_record
        s_val = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id:
                    return f"Team '{team_identifier}' not found."
                cur.execute("SELECT to_regclass('gm_team_seasons') IS NOT NULL")
                rec = team_draft_record(cur, team_id, s_val) if cur.fetchone()[0] else None
    except Exception as e:
        return f"Error: {str(e)}"
    if not rec:
        return f"No draft track record scored for '{team_identifier}' yet."
    return json.dumps(dict(team=str(team_identifier).upper(), as_of_season=rec["season"], **draft_record_facts(rec)),
                      indent=2)


def development_record_facts(rec: dict) -> dict:
    """A team's player-development record for writers (pure): risers and fallers with their ratings and verdicts.
    Never a score."""
    def line(p):
        return {"player": p["name"], "pos": p["position"], "age": p["age"],
                "ovr": f"{p['ovr_start']} -> {p['ovr_end']}", "verdict": p["label"], "rookie_year": bool(p.get("rookie"))}
    return {"window": rec.get("window"), "risers": [line(p) for p in rec.get("risers", [])],
            "fallers": [line(p) for p in rec.get("fallers", [])]}


def _front_office_rows(cur, season):
    cur.execute("SELECT to_regclass('gm_team_seasons') IS NOT NULL")
    if not cur.fetchone()[0]:
        return []
    from engines.fo_standing import stored
    return stored(cur, season)


@tool
def get_front_office_standing(team_identifier: str, season: int = 0) -> str:
    """Fetch how a team's front office is regarded around the league: its GM, years in the job, its front-office
    rank of 32 and reputation, how its drafting and player development compare with the league (judged only on the
    current GM's own tenure, so a new GM is "not judged yet"), its recent draft verdicts, the players who developed
    or stalled under it, and how hot the GM's seat is after the last season. early_read means the standing rests on a
    couple of picks: call it a first impression, not a verdict. Use the ranks and verdicts as written; there is no
    numeric grade to quote."""
    try:
        from engines.dev_quality import team_development_record
        from engines.fo_quality import team_draft_record
        from engines.fo_standing import standing_facts
        s_val = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                team_id = _resolve_team_id(cur, team_identifier)
                if not team_id:
                    return f"Team '{team_identifier}' not found."
                row = next((r for r in _front_office_rows(cur, s_val) if r["team_id"] == team_id), None)
                draft = team_draft_record(cur, team_id, s_val) if row else None
                dev = team_development_record(cur, team_id, s_val) if row else None
    except Exception as e:
        return f"Error: {str(e)}"
    if not row:
        return f"No front-office standing for '{team_identifier}' yet."
    out = standing_facts(row, s_val)
    if draft:
        out["draft_record"] = draft_record_facts(draft)
    if dev:
        out["development_record"] = development_record_facts(dev)
    return json.dumps(out, indent=2)


@tool
def get_front_office_rankings(season: int = 0) -> str:
    """Fetch the league's front offices ranked 1-32: each team's GM, years in the job, reputation, how its drafting
    and player development compare with the league, and the GM's hot-seat temperature. The general consensus on
    which front offices are best and worst run. Use the ranks and words as written; there is no numeric grade."""
    try:
        from engines.fo_standing import standing_facts
        s_val = resolve_season(season)
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                rows = _front_office_rows(cur, s_val)
    except Exception as e:
        return f"Error: {str(e)}"
    if not rows:
        return "No front-office standings yet."
    return json.dumps({"season": s_val, "front_offices": [standing_facts(r, s_val) for r in rows]}, indent=2)


_PICK_TEXT = re.compile(r"^\s*(20\d\d)\s+(?:r(?:ound)?\s*([1-7])|([1-7])(?:st|nd|rd|th)(?:\s*-?\s*round)?)\b(?:\s*pick)?\s*\(?([^)]*)\)?\s*$",
                        re.I)


def parse_package_item(text: str):
    """One package item: ('PICK', draft_season, round, original team text or '') for '2037 R2', '2037 2nd-round pick
    (Bills)'; else ('PLAYER', name)."""
    m = _PICK_TEXT.match(text or "")
    if m:
        return ("PICK", int(m.group(1)), int(m.group(2) or m.group(3)), (m.group(4) or "").strip())
    return ("PLAYER", (text or "").strip())


def package_check_week(ctx_week):
    """The league week a hypothetical package is checked at: the article's week through the deadline; after it
    (Weeks 9-23) the coming offseason (week 24), the first window the deal could happen in."""
    if ctx_week is None:
        return None
    if rm.TRADE_DEADLINE_WEEK < ctx_week < rm.OFFSEASON_WEEK:
        return rm.OFFSEASON_WEEK
    return ctx_week


PACKAGE_CHECK_LABELS = {
    "cap": "fits under both teams' caps", "ntc": "no no-trade clause", "roster": "roster limits",
    "slots": "each team keeps a QB, K and P", "deadline": "trade window open", "picks": "each pick is owned by its sender",
    "booking": "every asset is available", "tradeable": "every player can be traded now",
    "fairness": "balanced enough for both GMs to sign off", "players": "every player is on a roster",
}


def package_check_lines(checks: dict) -> list:
    """legality_check's checks as writer lines, without the numbers behind them (league values, cap margins) and
    without naming what another proposal has booked (that would leak pending talks)."""
    out = []
    for key, c in checks.items():
        label = PACKAGE_CHECK_LABELS.get(key, key)
        if c["ok"]:
            out.append(f"pass · {label}")
        elif key == "cap":
            teams = sorted({part.split(":")[0].strip() for part in str(c["detail"]).split(";") if ":" in part})
            out.append(f"FAIL · {label}: {', '.join(teams) or 'a team'} would be over the cap or too close to it")
        elif key == "booking":
            out.append(f"FAIL · {label}: an asset in it isn't available for a hypothetical package right now")
        elif key == "fairness":
            out.append(f"FAIL · {label}: too lopsided")
        elif key == "deadline":
            out.append(f"FAIL · {label}: the trade window is closed")
        else:
            out.append(f"FAIL · {label}: {c['detail']}")
    return out


@tool
def check_trade_package(team_a: str, team_b: str, team_a_sends: str, team_b_sends: str, season: int = 0) -> str:
    """Check a hypothetical trade package you want to propose as your own idea (only in article types whose directive allows one): the same legality checks the league's GMs use (cap, no-trade clauses, roster limits, the trade deadline, pick ownership, and whether it is balanced enough for both GMs). team_a_sends / team_b_sends are comma lists of players (names, on that team now) and picks ('2037 R2', or '2037 R2 Bills' for a pick the team holds from another team). Shows each player's real cap hit and contract term. Only a package that passes may appear in an article, framed as your idea, never as reported talks."""
    from engines import front_office as fo
    try:
        s_val = resolve_season(season)
        ctx_season, ctx_week, _ = _txn_context()
        with psycopg.connect(db.db_conn_info) as conn:
            with conn.cursor() as cur:
                if not fo.installed(cur):
                    return "Trade checks aren't available yet."
                a_id, b_id = _resolve_team_id(cur, team_a), _resolve_team_id(cur, team_b)
                if not a_id or not b_id or a_id == b_id:
                    return f"Need two different teams (got '{team_a}', '{team_b}')."
                if ctx_season == s_val and ctx_week is not None:
                    week = package_check_week(ctx_week)
                else:
                    cur.execute("SELECT current_week FROM league_state WHERE id = 1;")
                    week = package_check_week(cur.fetchone()[0])
                assets, package, unresolved = [], [], []
                for frm, to, text in ((a_id, b_id, team_a_sends), (b_id, a_id, team_b_sends)):
                    for item in [x for x in str(text or "").split(",") if x.strip()]:
                        parsed = parse_package_item(item)
                        if parsed[0] == "PICK":
                            orig = _resolve_team_id(cur, parsed[3]) if parsed[3] else frm
                            if not orig:
                                unresolved.append(item.strip())
                                continue
                            assets.append({"type": "PICK", "pick": (parsed[1], parsed[2], orig), "from": frm, "to": to})
                            cur.execute("SELECT abbreviation FROM teams WHERE id = %s;", (orig,))
                            package.append(f"{parsed[1]} round {parsed[2]} pick ({cur.fetchone()[0]} original)")
                            continue
                        cur.execute("""
                            SELECT p.id, COALESCE(p.full_name, p.player_name), ps.position,
                                   pc.total_value, pc.length, pc.season + pc.years_remaining - 1, pcy.cap_hit,
                                   pc.no_trade_clause
                            FROM player_seasons ps JOIN players p ON p.id = ps.player_id
                            LEFT JOIN player_contracts pc ON pc.player_id = p.id AND pc.status = 'ACTIVE' AND pc.season = ps.season
                            LEFT JOIN player_contract_years pcy ON pcy.contract_id = pc.id AND pcy.year = %s
                            WHERE ps.season = %s AND ps.team_id = %s AND ps.released_week IS NULL
                              AND (p.full_name ILIKE %s OR p.player_name ILIKE %s)
                            ORDER BY ps.overall_rating DESC NULLS LAST LIMIT 1;
                        """, (s_val, s_val, frm, f"%{parsed[1]}%", f"%{parsed[1]}%"))
                        r = cur.fetchone()
                        if not r:
                            unresolved.append(item.strip())
                            continue
                        assets.append({"type": "PLAYER", "player_id": r[0], "from": frm, "to": to})
                        terms = (f"{rm.money_text(r[6])} cap hit this season, {r[4]}-year deal worth "
                                 f"{rm.money_text(r[3])} through {r[5]}" if r[3] is not None else "no contract on file")
                        package.append(f"{r[2]} {rm.display_name({'full_name': r[1]})}: {terms}"
                                       + (", no-trade clause" if r[7] else ""))
                if unresolved:
                    return json.dumps({"legal": False, "not_found": unresolved, "note": (
                        "Each player must be on the sending team's roster now (name as listed); each pick as "
                        "'2037 R2' or '2037 R2 Bills'. Fix these and check again.")}, indent=2)
                if not assets or not any(x["from"] == a_id for x in assets) or not any(x["from"] == b_id for x in assets):
                    return "Both teams must send at least one player or pick."
                res = fo.legality_check(cur, s_val, week, a_id, b_id, assets)
    except Exception as e:
        return f"Error: {str(e)}"
    legal = bool(res["legal"])
    return json.dumps({
        "legal": legal,
        "checked_for": (f"Week {week}" if week and week <= rm.REGULAR_SEASON_LAST_WEEK else "the coming offseason"),
        "package": package,
        "checks": package_check_lines(res["checks"]),
        "note": ("Legal. You may pitch it only as your own idea ('a deal they could chase', 'a trade I'd make'), never "
                 "as talks anyone reported or a rumor, and cite only the terms shown here." if legal else
                 "Not legal: don't pitch this package. Change what failed and check again, or leave it out."),
    }, indent=2)
