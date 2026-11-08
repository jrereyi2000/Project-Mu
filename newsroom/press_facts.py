"""Press conference fact sheet and line checker (PRESS_CONFERENCE_PLAN §2.1, §2.3; PROJECT_SPEC §2.14, §3, §7.2).

`fact_sheet(cur, game_id)` builds the one frozen set of facts both pressers of a game share. Every number in it
comes from the engines or the database; a value the data does not record is ``None`` (never 0). `check_line`
guards a reporter's or coach's line against that sheet: scores, ``N <stat>`` phrases, records and streaks, playoff
standing, person names and event attributions. Ages, contract figures and historical claims are out of scope, and
opinions always pass.

Everything is read through the cursor the caller owns, except two readers that already own their connection
(`db_tools.get_game_box_score`, a LangChain tool) and `records_engine.find_records_broken_in_week`, which reads the
record book with many queries. Both are module-level names so callers and tests can replace them.
"""
from __future__ import annotations

import re

from engines import game_script_engine
from engines.coach_profile import fetch_team_coach
from engines.records_engine import find_records_broken_in_week
from newsroom import db_tools
from newsroom import roster_moves as rm
from pipeline.mod_packs import broadcast_window

# db_tools' box-score reader is a LangChain StructuredTool; keep the plain function so `fact_sheet` can call it
# directly (tests patch this module-level name).
get_game_box_score = getattr(db_tools.get_game_box_score, "func", db_tools.get_game_box_score)

PLAYOFF_SEEDS = 7

# The real people in the press pool. A reporter may name himself (or a colleague) without the NAME check firing;
# these are the display names in newsroom/config/agent_accounts.json plus the two pool archetypes without accounts.
PUBLIC_FIGURES = (
    "Adam Schefter", "Emmanuel Acho", "Bill Barnwell", "Matthew Berry", "Jeffri Chadiha", "Ryan Clark",
    "Colin Cowherd", "Rich Eisen", "Kirk Herbstreit", "J.J. Watt", "KC Joyner", "Mina Kimes", "Pat McAfee",
    "Chad Johnson", "Dan Orlovsky", "Ian Rapoport", "Robert Griffin III", "Gregg Rosenthal", "Mike Sando",
    "Shannon Sharpe", "Bill Simmons", "Skip Bayless", "Stephen A. Smith", "The Athletic Staff", "Trey Wingo",
    "Field Yates", "Mel Kiper",
)

# Place names and quoted/unquoted company and broadcast names that are not people. Team names and cities are added
# from the sheet at check time.
PLACES = (
    "Kansas City", "New York", "Los Angeles", "San Francisco", "Tampa Bay", "Green Bay", "Las Vegas",
    "New Orleans", "New England", "Mexico City", "Rio de Janeiro", "Sao Paulo", "United States",
    "Great Britain", "East Rutherford", "Foxborough", "Jersey City", "St. Louis", "San Diego", "Oakland",
)
COMPANIES = (
    "Pro Football Focus", "Pro Football Talk", "The Athletic", "Sports Illustrated", "CBS Sports", "NFL Network",
    "FOX Sports", "Fox Sports", "The Ringer", "Bleacher Report", "USA Today", "Associated Press",
    "The Associated Press", "NFL Films", "Madden NFL", "Amazon Prime", "Paramount Plus", "Monday Night Football",
    "Sunday Night Football", "Thursday Night Football", "Super Bowl", "Pro Bowl", "Hall of Fame", "All-Pro",
    "Wild Card", "Divisional Round", "Conference Championship", "First Team", "Second Team",
)
# Capitalised football phrases that are not people.
FOOTBALL_PHRASES = (
    "West Coast", "Air Raid", "Run and Shoot", "Pro Style", "Zone Read", "Play Action", "Play-Action",
    "Two-Minute Drill", "Two Minute Drill", "Screen Pass", "Deep Ball", "Hail Mary", "Lambeau Leap",
    "Gatorade Bath", "Black Monday", "Monday Morning", "Time of Possession", "Locker Room", "Front Office",
    "Turnover Margin", "Special Teams", "Offensive Line", "Defensive Line", "Practice Squad", "Injury Report",
    "Depth Chart", "Game Plan", "Pass Rush", "Run Game", "Red Zone", "Goal Line", "First-Team", "Second-Team",
)
# Every club's full name, so a presser can mention another team without the NAME check firing.
NFL_TEAMS = (
    "Arizona Cardinals", "Atlanta Falcons", "Baltimore Ravens", "Buffalo Bills", "Carolina Panthers",
    "Chicago Bears", "Cincinnati Bengals", "Cleveland Browns", "Dallas Cowboys", "Denver Broncos", "Detroit Lions",
    "Green Bay Packers", "Houston Texans", "Indianapolis Colts", "Jacksonville Jaguars", "Kansas City Chiefs",
    "Las Vegas Raiders", "Los Angeles Chargers", "Los Angeles Rams", "Miami Dolphins", "Minnesota Vikings",
    "New England Patriots", "New Orleans Saints", "New York Giants", "New York Jets", "Philadelphia Eagles",
    "Pittsburgh Steelers", "San Francisco 49ers", "Seattle Seahawks", "Tampa Bay Buccaneers", "Tennessee Titans",
    "Washington Commanders",
)

# Capitalised words that open a phrase but are not a name.
NAME_STOPWORDS = {
    "the", "a", "an", "this", "that", "these", "those", "there", "here", "where", "when", "why", "how", "what",
    "who", "which", "whose", "we", "you", "they", "he", "she", "it", "i", "our", "your", "their", "his", "her",
    "its", "my", "and", "but", "so", "or", "if", "because", "as", "at", "by", "for", "from", "in", "into", "of",
    "on", "to", "with", "without", "about", "after", "before", "during", "over", "under", "again", "also",
    "then", "now", "still", "just", "only", "even", "very", "really", "much", "more", "most", "some", "any",
    "all", "every", "each", "both", "either", "neither", "another", "other", "same", "different", "first",
    "second", "third", "fourth", "last", "next", "previous", "one", "two", "three", "four", "five", "six",
    "seven", "eight", "nine", "ten", "good", "great", "bad", "tough", "right", "sure", "yeah", "yep", "nope",
    "okay", "ok", "alright", "thanks", "thank", "sorry", "please", "absolutely", "exactly", "definitely",
    "probably", "maybe", "obviously", "honestly", "unfortunately", "fortunately", "basically", "actually",
    "look", "listen", "well", "no", "yes", "god", "coach", "head", "coordinator", "assistant", "offensive",
    "defensive", "special", "teams", "team", "general", "manager", "owner", "president", "mr", "mrs", "ms", "dr",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february",
    "march", "april", "may", "june", "july", "august", "september", "october", "november", "december",
    "christmas", "thanksgiving", "eve", "year", "years", "day", "days", "week", "weeks", "month", "months",
    "new", "last", "this", "next",
    "offense", "defense", "quarter", "half", "overtime", "red", "zone", "goal", "line", "field", "two-point",
    "two", "point", "points", "play", "game", "season", "week", "injury", "report", "depth", "chart",
}

STANDARD_PLAYER_KEYS = (
    "pass_yards", "pass_tds", "interceptions", "rush_attempts", "rush_yards", "rush_tds", "receptions",
    "rec_yards", "rec_tds", "fumbles", "def_tackles", "sacks", "def_ints", "def_tds", "def_forced_fumbles",
    "def_fumble_recoveries", "kick_fgm", "kick_fga", "kick_xpm", "kick_xpa", "yards", "touchdowns",
)

# ------------------------------------------------------------------------------------------------ fact sheet ---
def fact_sheet(cur, game_id: int) -> dict:
    """The frozen fact sheet for one game (plan §2.3). Returns ``{}`` when the game is not found."""
    cur.execute("""
        SELECT g.id, g.season, g.week, COALESCE(g.game_type, 'REGULAR_SEASON'), g.game_time, g.status,
               g.home_team_id, ht.name, ht.city, ht.abbreviation, ht.conference, ht.division, g.home_score,
               g.away_team_id, at.name, at.city, at.abbreviation, at.conference, at.division, g.away_score
        FROM games g
        JOIN teams ht ON ht.id = g.home_team_id
        JOIN teams at ON at.id = g.away_team_id
        WHERE g.id = %s
    """, (game_id,))
    row = cur.fetchone()
    if not row:
        return {}

    (gid, season, week, game_type, game_time, status,
     home_id, home_name, home_city, home_abbr, home_conf, home_div, home_score,
     away_id, away_name, away_city, away_abbr, away_conf, away_div, away_score) = row

    home = {"id": home_id, "name": home_name, "city": home_city, "abbr": home_abbr,
            "conference": home_conf, "division": home_div, "home": True, "score": home_score}
    away = {"id": away_id, "name": away_name, "city": away_city, "abbr": away_abbr,
            "conference": away_conf, "division": away_div, "home": False, "score": away_score}
    team_by_id = {home["id"]: home, away["id"]: away}

    # Every completed game up to this week: records, streaks, standings and the league blocks all come from it.
    cur.execute("""
        SELECT id, week, game_type, home_team_id, away_team_id, home_score, away_score
        FROM games
        WHERE season = %s AND status = 'COMPLETED' AND week <= %s
        ORDER BY week, id
    """, (season, week))
    season_games = [dict(zip(("id", "week", "game_type", "home_team_id", "away_team_id",
                              "home_score", "away_score"), r)) for r in cur.fetchall()]

    cur.execute("SELECT id, name, city, abbreviation, conference, division FROM teams ORDER BY division, name")
    teams = [dict(zip(("id", "name", "city", "abbr", "conference", "division"), r)) for r in cur.fetchall()]

    team_stats = _team_stats(cur, game_id)
    ts_away = _stats_for(team_stats, away["name"])
    ts_home = _stats_for(team_stats, home["name"])
    quarter_scores = {away["abbr"]: _quarters(ts_away), home["abbr"]: _quarters(ts_home)}
    broadcast = broadcast_window(game_time, away.get("conference"))

    script = _load_script(cur, game_id)
    box = _box_score(game_id)

    records_before, records_after = _team_records(season_games, gid, (away["id"], home["id"]))
    division_place, conference_seed = _standings(teams, records_after)
    record_events = _records_for_week(cur, season, week)

    player_lines, team_aggs = _player_lines(cur, game_id, season, week, team_by_id)
    team_stats_list = []
    for team, ts, opp_ts in ((away, ts_away, ts_home), (home, ts_home, ts_away)):
        team_stats_list.append(_team_facts(
            cur, team, season, week, season_games, ts, opp_ts, player_lines, team_aggs,
            records_before, records_after, division_place, conference_seed, record_events))

    league = _league_block(season_games, teams, gid, week, records_after)

    game_block = {
        "id": gid, "season": season, "week": week, "game_type": game_type, "status": status,
        "game_time": game_time, "broadcast_window": broadcast,
        "home": _team_ref(home), "away": _team_ref(away),
        "final": {"home": home_score, "away": away_score},
        "quarter_scores": quarter_scores,
    }
    names = _collect_names(team_stats_list, player_lines, (away, home))
    numbers = _numbers_index(game_block, team_stats_list, player_lines)

    return {
        "game": game_block,
        "box": box,
        "script": _script_block(script),
        "teams": team_stats_list,
        "league": league,
        "names": names,
        "numbers": numbers,
    }


def _team_ref(team):
    return {"id": team["id"], "name": team["name"], "city": team.get("city"), "abbr": team["abbr"]}


def _box_score(game_id):
    try:
        box = get_game_box_score(game_id)
    except Exception:
        return None
    if not box or (isinstance(box, str) and box.startswith("Error:")):
        return None
    return box


def _load_script(cur, game_id):
    try:
        cur.execute("SELECT script FROM game_scripts WHERE game_id = %s", (game_id,))
        row = cur.fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    try:
        script, _ = game_script_engine.ensure_script(cur, game_id)
        return script
    except Exception:
        return None


def _script_block(script):
    summary = game_script_engine.script_summary(script) if script else {}
    script = script or {}
    return {
        "shape": script.get("shape"),
        "storyline": summary.get("storyline") or script.get("storyline"),
        "lead_changes": script.get("lead_changes"),
        "largest_lead": script.get("largest_lead"),
        "winner_deficit": script.get("winner_largest_deficit"),
        "key_moments": summary.get("key_moments") or [],
        "halftime": summary.get("halftime"),
        "winning_score": summary.get("winning_score"),
        "overtime": bool(summary.get("overtime") or script.get("overtime")),
    }


def _team_stats(cur, game_id):
    """{team_name: {...}} from team_stats. A missing table/rows reads as {}."""
    try:
        cur.execute("""
            SELECT team_name, total_offense, total_yards_gained, passing_yards, rushing_yards, turnovers,
                   q1, q2, q3, q4, ot
            FROM team_stats
            WHERE game_id = %s
            ORDER BY team_name
        """, (game_id,))
        rows = cur.fetchall()
    except Exception:
        return {}
    out = {}
    for r in rows:
        out[r[0]] = dict(zip(("team_name", "total_offense", "total_yards_gained", "passing_yards",
                              "rushing_yards", "turnovers", "q1", "q2", "q3", "q4", "ot"), r))
    return out


def _stats_for(team_stats, name):
    if name in team_stats:
        return team_stats[name]
    lower = (name or "").lower()
    for key, row in team_stats.items():
        if (key or "").lower() == lower:
            return row
    return None


def _quarters(ts):
    if not ts:
        return None
    vals = [ts.get("q1"), ts.get("q2"), ts.get("q3"), ts.get("q4")]
    if any(v is None for v in vals):
        return None
    out = list(vals)
    if ts.get("ot") is not None:
        out.append(ts["ot"])
    return out


def _team_records(season_games, game_id, team_ids):
    """({team_id: (w, l, t)}, {team_id: (w, l, t)}) before and after this game (regular season only).

    Every team that appears in a game gets a row, so division and conference standings see the whole league."""
    before = {t: [0, 0, 0] for t in team_ids}
    after = {t: [0, 0, 0] for t in team_ids}
    for g in season_games:
        if (g["game_type"] or "REGULAR_SEASON") != "REGULAR_SEASON":
            continue
        hs, as_ = g["home_score"], g["away_score"]
        if hs is None or as_ is None:
            continue
        for tid, pf, pa in ((g["home_team_id"], hs, as_), (g["away_team_id"], as_, hs)):
            before.setdefault(tid, [0, 0, 0])
            after.setdefault(tid, [0, 0, 0])
            idx = 0 if pf > pa else 1 if pf < pa else 2
            after[tid][idx] += 1
            if g["id"] != game_id:
                before[tid][idx] += 1
    return ({k: tuple(v) for k, v in before.items()}, {k: tuple(v) for k, v in after.items()})


def _record_text(rec):
    if rec is None:
        return None
    w, l, t = rec
    return f"{w}-{l}-{t}" if t else f"{w}-{l}"


def _standings(teams, records):
    """({team_id: division_place}, {team_id: conference_seed}) from the after-game regular-season records."""
    def pct(tid):
        w, l, t = records.get(tid, (0, 0, 0))
        played = w + l + t
        return (w + 0.5 * t) / played if played else 0.0

    by_div, by_conf = {}, {}
    for t in teams:
        by_div.setdefault(t["division"], []).append(t)
        by_conf.setdefault(t["conference"], []).append(t)
    place = {}
    for rows in by_div.values():
        rows = sorted(rows, key=lambda t: (pct(t["id"]), -t["id"]), reverse=True)
        for i, t in enumerate(rows, 1):
            place[t["id"]] = i
    seed = {}
    for rows in by_conf.values():
        rows = sorted(rows, key=lambda t: (pct(t["id"]), -t["id"]), reverse=True)
        for i, t in enumerate(rows, 1):
            seed[t["id"]] = i
    return place, seed


def _streak(season_games, team_id):
    games = [g for g in season_games if team_id in (g["home_team_id"], g["away_team_id"])
             and g["home_score"] is not None and g["away_score"] is not None]
    games.sort(key=lambda g: (g["week"], g["id"]))
    if not games:
        return {"type": None, "count": 0}
    last = games[-1]
    home = last["home_team_id"] == team_id
    win = (last["home_score"] > last["away_score"]) if home else (last["away_score"] > last["home_score"])
    tie = last["home_score"] == last["away_score"]
    kind = "T" if tie else ("W" if win else "L")
    count = 0
    for g in reversed(games):
        h = g["home_team_id"] == team_id
        if g["home_score"] == g["away_score"]:
            this = "T"
        else:
            this = "W" if ((g["home_score"] > g["away_score"]) == h) else "L"
        if this != kind:
            break
        count += 1
    return {"type": kind, "count": count}


def _team_facts(cur, team, season, week, season_games, ts, opponent_ts, player_lines, team_aggs,
                records_before, records_after, division_place, conference_seed, record_events):
    abbr = team["abbr"]
    players = [p for p in player_lines if p["team"] == abbr]
    agg = team_aggs.get(abbr, {})

    yards = None
    if ts:
        if ts.get("total_yards_gained") is not None:
            yards = ts["total_yards_gained"]
        elif ts.get("total_offense") is not None:
            yards = ts["total_offense"]
        elif ts.get("passing_yards") is not None and ts.get("rushing_yards") is not None:
            yards = ts["passing_yards"] + ts["rushing_yards"]
    line = {
        "yards": yards,
        "pass_yards": ts.get("passing_yards") if ts else None,
        "rush_yards": ts.get("rushing_yards") if ts else None,
        "turnovers": ts.get("turnovers") if ts else None,
        "takeaways": opponent_ts.get("turnovers") if opponent_ts else None,
        "sacks": agg.get("sacks"),
    }
    transactions, transaction_players = _transactions(cur, team["id"], season, week)

    return {
        "team_id": team["id"],
        "name": team["name"],
        "city": team.get("city"),
        "abbr": abbr,
        "conference": team.get("conference"),
        "division": team.get("division"),
        "home": bool(team.get("home")),
        "record_before": _record_text(records_before.get(team["id"])),
        "record_after": _record_text(records_after.get(team["id"])),
        "streak": _streak(season_games, team["id"]),
        "division_place": division_place.get(team["id"]),
        "conference_seed": conference_seed.get(team["id"]),
        "coach": _coach_facts(cur, team["id"], season),
        "line": line,
        "players": players,
        "injuries": _injuries(cur, team["id"], season, week),
        "transactions": transactions,
        "transaction_players": transaction_players,
        "records": [e for e in record_events if e.get("team_id") == team["id"]],
    }


def _coach_facts(cur, team_id, season):
    try:
        coach = fetch_team_coach(cur, team_id, season)
    except Exception:
        coach = None
    if not coach:
        return None
    start = coach.get("start_season")
    out = {
        "id": coach.get("id"),
        "name": coach.get("coach_name"),
        "tenure": (season - start + 1) if start else None,
        "with_team_since": start,
        "slant": coach.get("slant"),
        "schemes": {
            "offense": coach.get("offensive_scheme"),
            "defense": coach.get("defensive_scheme"),
        },
        "tendencies": {
            "pass_lean": coach.get("pass_lean"),
            "fourth_down_aggressiveness": coach.get("fourth_down_aggressiveness"),
            "man_coverage": coach.get("man_coverage"),
        },
        "hot_seat": None,
    }
    try:
        cur.execute("""
            SELECT score, fired, phase, inputs
            FROM coach_hot_seat
            WHERE season <= %s AND team_id = %s AND coach_id = %s
            ORDER BY season DESC, phase
            LIMIT 1
        """, (season, team_id, coach.get("id")))
        row = cur.fetchone()
    except Exception:
        row = None
    if row:
        score, fired, phase = row[0], bool(row[1]), row[2]
        verdict = "fired" if fired else "retained"
        line = f"Hot seat: score {float(score):.2f}, {verdict}" + (f" ({phase})" if phase else "")
        out["hot_seat"] = {"score": float(score) if score is not None else None,
                           "fired": fired, "phase": phase, "line": line}
    return out


def _injuries(cur, team_id, season, week):
    """Injuries new this week: this week's report rows whose player was not on last week's report."""
    try:
        cur.execute("""
            SELECT week, player_name, position, injury_type, duration, weeks_remaining, is_ir
            FROM injuries
            WHERE season = %s AND team_id = %s AND week IN (%s, %s)
        """, (season, team_id, week, week - 1))
        rows = cur.fetchall()
    except Exception:
        return []
    current, before = [], set()
    for r in rows:
        _, name, position, injury_type, duration, weeks_remaining, is_ir = r
        if r[0] == week - 1:
            before.add(name)
        elif r[0] == week:
            current.append({"player_name": name, "position": position, "injury_type": injury_type,
                            "duration": duration, "weeks_remaining": weeks_remaining, "is_ir": bool(is_ir)})
    return [i for i in current
            if i["player_name"] not in before and ((i["injury_type"] or "").upper() != "NONE" or i["is_ir"])]


def _transactions(cur, team_id, season, week):
    """(this week's moves as roster_moves.move_line strings, the player names in them)."""
    try:
        cur.execute("""
            SELECT t.id, t.season, t.week, c.week, t.type, t.player_id, t.player_name, p.full_name, t.position,
                   t.from_team_id, t.to_team_id, ft.name, tt.name, t.contract_years, t.contract_value, t.flags,
                   t.raw_text
            FROM transactions t
            LEFT JOIN captures c ON c.id = t.capture_id
            LEFT JOIN players p ON p.id = t.player_id
            LEFT JOIN teams ft ON ft.id = t.from_team_id
            LEFT JOIN teams tt ON tt.id = t.to_team_id
            WHERE t.season = %s AND (t.from_team_id = %s OR t.to_team_id = %s)
            ORDER BY t.week DESC NULLS FIRST, t.id DESC
        """, (season, team_id, team_id))
        rows = [dict(zip(("id", "season", "week", "capture_week", "type", "player_id", "player_name",
                          "full_name", "position", "from_team_id", "to_team_id", "from_team", "to_team",
                          "contract_years", "contract_value", "flags", "raw_text"), r))
                for r in cur.fetchall()]
    except Exception:
        return [], []
    for r in rows:
        r["team"] = r.get("from_team") if r.get("type") == "RELEASED" else (r.get("to_team") or r.get("from_team"))
    try:
        rows = rm.new_this_week(rows, season, week)
    except Exception:
        rows = []
    out, names = [], []
    for r in rows:
        name = rm.display_name(r)
        if name and name != "Unknown player":
            names.append(name)
        try:
            out.append(rm.move_line(r, with_notable=False))
        except Exception:
            out.append(r.get("raw_text") or " ".join(x for x in (r.get("team"), r.get("type"), name) if x))
    return out, names


def _records_for_week(cur, season, week):
    try:
        return find_records_broken_in_week(cur, season, week) or []
    except Exception:
        return []


def _player_lines(cur, game_id, season, week, team_by_id):
    """({player entries}, {team abbr: aggregate line}) for everyone with a recorded line in this game."""
    entries = {}

    cur.execute("""
        SELECT ps.player_id, COALESCE(p.full_name, ps.player_name), ps.team_id, ps.position,
               ps.pass_yards, ps.pass_tds, ps.interceptions, ps.rush_attempts, ps.rush_yards, ps.rush_tds,
               ps.receptions, ps.rec_yards, ps.rec_tds, ps.fumb
        FROM player_stats ps
        LEFT JOIN players p ON p.id = ps.player_id
        WHERE ps.game_id = %s
        ORDER BY ps.team_id, ps.id
    """, (game_id,))
    offense_rows = cur.fetchall()
    for r in offense_rows:
        pid, name, tid, pos, py, ptd, ints, ra, ry, rtd, rec, recy, rectd, fumb = r
        team = team_by_id.get(tid)
        if not team or not name:
            continue
        key = pid if pid is not None else (tid, name)
        e = entries.setdefault(key, _empty_player(name, team["abbr"], pos, pid))
        e["line"].update({
            "pass_yards": py, "pass_tds": ptd, "interceptions": ints, "rush_attempts": ra,
            "rush_yards": ry, "rush_tds": rtd, "receptions": rec, "rec_yards": recy, "rec_tds": rectd,
            "fumbles": fumb,
        })

    cur.execute("""
        SELECT gl.team_id, gl.player_id, COALESCE(gl.full_name, gl.player_name), pse.position,
               gl.def_tackles, gl.def_sacks, gl.def_tfl, gl.def_ints, gl.def_forced_fumbles,
               gl.def_fumble_recoveries, gl.def_tds, gl.kick_fgm, gl.kick_fga, gl.kick_xpm, gl.kick_xpa
        FROM game_stat_lines gl
        LEFT JOIN player_seasons pse ON pse.player_id = gl.player_id AND pse.season = %s
        WHERE gl.game_id = %s AND gl.player_id IS NOT NULL
          AND (gl.def_tackles IS NOT NULL OR gl.kick_fga IS NOT NULL OR gl.kick_xpa IS NOT NULL)
        ORDER BY gl.team_id, gl.player_id
    """, (season, game_id))
    defense_rows = cur.fetchall()
    for r in defense_rows:
        tid, pid, name, pos, tackles, sacks, tfl, ints, ff, fr, dtd, fgm, fga, xpm, xpa = r
        team = team_by_id.get(tid)
        if not team or not name:
            continue
        e = entries.setdefault(pid if pid is not None else (tid, name),
                               _empty_player(name, team["abbr"], pos, pid))
        e["line"].update({
            "def_tackles": tackles, "sacks": _num(sacks), "def_tfl": tfl, "def_ints": ints,
            "def_forced_fumbles": ff, "def_fumble_recoveries": fr, "def_tds": dtd,
            "kick_fgm": fgm, "kick_fga": fga, "kick_xpm": xpm, "kick_xpa": xpa,
        })

    pids = [e["player_id"] for e in entries.values() if e["player_id"] is not None]
    averages = _averages(cur, season, week, pids)

    for key, e in entries.items():
        line = e["line"]
        line["yards"] = _sum_present(line.get("pass_yards"), line.get("rush_yards"), line.get("rec_yards"))
        line["touchdowns"] = _sum_present(line.get("pass_tds"), line.get("rush_tds"), line.get("rec_tds"),
                                          line.get("def_tds"))
        e["season_averages"] = averages.get(e["player_id"])

    out = [e for e in entries.values() if any(v is not None for v in e["line"].values())]

    aggs = {}
    for e in out:
        a = aggs.setdefault(e["team"], {})
        line = e["line"]
        for stat in ("pass_yards", "pass_tds", "interceptions", "rush_attempts", "rush_yards", "rush_tds",
                     "receptions", "rec_yards", "rec_tds", "def_tds", "def_ints", "kick_fgm"):
            _accumulate(a, stat, line.get(stat))
        _accumulate(a, "sacks", line.get("sacks"))
    for abbr, a in aggs.items():
        a["touchdowns"] = _sum_present(a.get("pass_tds"), a.get("rush_tds"), a.get("def_tds"))
        a["yards"] = _sum_present(a.get("pass_yards"), a.get("rush_yards"), a.get("rec_yards"))
        a["field_goals"] = a.get("kick_fgm")
        a["carries"] = a.get("rush_attempts")
        a["receptions"] = a.get("receptions")
        a["interceptions"] = a.get("interceptions")
    return out, aggs


def _empty_player(name, team, position, player_id):
    return {"player_id": player_id, "name": name, "position": position, "team": team,
            "line": {k: None for k in STANDARD_PLAYER_KEYS}, "season_averages": None}


def _averages(cur, season, week, pids):
    out = {}
    if not pids:
        return out
    cur.execute("""
        SELECT ps.player_id, COUNT(*),
               AVG(ps.pass_yards), AVG(ps.pass_tds), AVG(ps.interceptions), AVG(ps.rush_attempts),
               AVG(ps.rush_yards), AVG(ps.rush_tds), AVG(ps.receptions), AVG(ps.rec_yards), AVG(ps.rec_tds),
               AVG(ps.fumb)
        FROM player_stats ps
        JOIN games g ON g.id = ps.game_id
        WHERE g.season = %s AND g.week < %s AND g.status = 'COMPLETED'
          AND ps.player_id = ANY(%s)
        GROUP BY ps.player_id
    """, (season, week, pids))
    for r in cur.fetchall():
        pid, games = r[0], r[1]
        vals = r[2:]
        keys = ("pass_yards", "pass_tds", "interceptions", "rush_attempts", "rush_yards", "rush_tds",
                "receptions", "rec_yards", "rec_tds", "fumbles")
        out[pid] = {"games": games, **{k: _round(v) for k, v in zip(keys, vals)}}

    cur.execute("""
        SELECT gl.player_id, COUNT(*),
               AVG(gl.def_tackles), AVG(gl.def_sacks), AVG(gl.def_tfl), AVG(gl.def_ints),
               AVG(gl.def_forced_fumbles), AVG(gl.def_fumble_recoveries), AVG(gl.def_tds),
               AVG(gl.kick_fgm), AVG(gl.kick_fga), AVG(gl.kick_xpm), AVG(gl.kick_xpa)
        FROM game_stat_lines gl
        JOIN games g ON g.id = gl.game_id
        WHERE g.season = %s AND g.week < %s AND g.status = 'COMPLETED'
          AND gl.player_id = ANY(%s)
        GROUP BY gl.player_id
    """, (season, week, pids))
    keys = ("def_tackles", "sacks", "def_tfl", "def_ints", "def_forced_fumbles", "def_fumble_recoveries",
            "def_tds", "kick_fgm", "kick_fga", "kick_xpm", "kick_xpa")
    for r in cur.fetchall():
        pid, games = r[0], r[1]
        d = out.setdefault(pid, {"games": games})
        d.update({k: _round(v) for k, v in zip(keys, r[2:])})
        d["games"] = max(games, d.get("games") or 0)
    for d in out.values():
        d["yards"] = _sum_present(d.get("pass_yards"), d.get("rush_yards"), d.get("rec_yards"))
        d["touchdowns"] = _sum_present(d.get("pass_tds"), d.get("rush_tds"), d.get("rec_tds"), d.get("def_tds"))
        d["carries"] = d.get("rush_attempts")
        d["field_goals"] = d.get("kick_fgm")
    return out


def _league_block(season_games, teams, game_id, week, records_after):
    by_id = {t["id"]: t for t in teams}

    def pct(tid):
        w, l, t = records_after.get(tid, (0, 0, 0))
        played = w + l + t
        return (w + 0.5 * t) / played if played else 0.0

    other = []
    for g in season_games:
        if g["week"] != week or g["id"] == game_id:
            continue
        ht, at = by_id.get(g["home_team_id"]), by_id.get(g["away_team_id"])
        if not ht or not at or g["home_score"] is None or g["away_score"] is None:
            continue
        other.append(f"{at['abbr']} {g['away_score']}, {ht['abbr']} {g['home_score']}")

    ranked = sorted((t for t in teams if t["id"] in records_after),
                    key=lambda t: (pct(t["id"]), -t["id"]), reverse=True)
    top = []
    for t in ranked[:5]:
        top.append(f"{t['abbr']} {_record_text(records_after.get(t['id']))}")

    picture = None
    if week >= 10:
        picture = {}
        for conf in sorted({t["conference"] for t in teams if t.get("conference")}):
            rows = [t for t in ranked if t.get("conference") == conf]
            picture[conf] = [{"seed": i, "team": t["abbr"], "record": _record_text(records_after.get(t["id"]))}
                             for i, t in enumerate(rows[:PLAYOFF_SEEDS], 1)]

    return {"other_results": other, "top_standings": top, "playoff_picture": picture}


def _collect_names(team_facts, player_lines, game_teams):
    names = []
    seen = set()

    def add(name):
        if not name or name in seen:
            return
        seen.add(name)
        names.append(name)

    for tf in team_facts:
        coach = tf.get("coach") or {}
        add(coach.get("name"))
        for p in tf.get("players", []):
            add(p.get("name"))
        for inj in tf.get("injuries", []):
            add(inj.get("player_name"))
        for name in tf.get("transaction_players", []):
            add(name)
        for rec in tf.get("records", []):
            add(rec.get("holder"))
    for p in player_lines:
        add(p.get("name"))
    for fig in PUBLIC_FIGURES:
        add(fig)
    return names


def _numbers_index(game, team_facts, player_lines):
    numbers = []

    def add(stat, subject, value):
        if subject is None or value is None:
            return
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return
        numbers.append([stat, subject, value])

    final = game.get("final") or {}
    for side, score in (("home", final.get("home")), ("away", final.get("away"))):
        ref = game.get(side) or {}
        add("score", ref.get("abbr"), score)
        add(f"{side}_score", ref.get("abbr"), score)
    if final.get("home") is not None and final.get("away") is not None:
        margin = abs(final["home"] - final["away"])
        home_abbr = (game.get("home") or {}).get("abbr")
        away_abbr = (game.get("away") or {}).get("abbr")
        add("margin", home_abbr, margin)
        add("margin", away_abbr, margin)
        add("points_allowed", home_abbr, final["away"])
        add("points_allowed", away_abbr, final["home"])

    for tf in team_facts:
        abbr = tf["abbr"]
        line = tf.get("line") or {}
        for stat in ("yards", "pass_yards", "rush_yards", "turnovers", "takeaways", "sacks"):
            add(stat, abbr, line.get(stat))
        add("points", abbr, final.get("home") if tf.get("home") else final.get("away"))
        add("wins", abbr, _wins(tf.get("record_after")))
        add("losses", abbr, _losses(tf.get("record_after")))
        streak = tf.get("streak") or {}
        if streak.get("type") == "W":
            add("win_streak", abbr, streak.get("count"))
        if streak.get("type") == "L":
            add("loss_streak", abbr, streak.get("count"))
        add("division_place", abbr, tf.get("division_place"))
        add("conference_seed", abbr, tf.get("conference_seed"))
        for stat in ("interceptions", "touchdowns", "receptions", "carries", "field_goals"):
            add(stat, abbr, _team_agg(abbr, stat, player_lines))

    for p in player_lines:
        name = p.get("name")
        for stat, value in (p.get("line") or {}).items():
            add(stat, name, value)
        avg = p.get("season_averages") or {}
        for stat, value in avg.items():
            if stat != "games":
                add(f"season_{stat}_pg", name, value)
    return numbers


def _team_agg(abbr, stat, player_lines):
    """The team's aggregate for a checker stat, summed over its recorded player lines (None when none recorded)."""
    keys = {
        "interceptions": ("interceptions",), "touchdowns": ("pass_tds", "rush_tds", "def_tds"),
        "receptions": ("receptions",), "carries": ("rush_attempts",), "field_goals": ("kick_fgm",),
    }[stat]
    vals = []
    for p in player_lines:
        if p.get("team") != abbr:
            continue
        for k in keys:
            v = (p.get("line") or {}).get(k)
            if v is not None:
                vals.append(v)
    return sum(vals) if vals else None


def _wins(record):
    try:
        return int(str(record).split("-")[0])
    except (TypeError, ValueError):
        return None


def _losses(record):
    try:
        return int(str(record).split("-")[1])
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------------------------------------ checker ---
def check_line(text: str, sheet: dict, speaker: str) -> list[dict]:
    """Every NUMBER, NAME and EVENT issue in `text` against `sheet` (plan §2.3). Opinions always pass."""
    if not text or not sheet:
        return []
    ctx = _CheckContext(sheet, speaker or "")
    issues = []
    issues += ctx.check_scores(text)
    issues += ctx.check_stat_phrases(text)
    issues += ctx.check_records(text)
    issues += ctx.check_standing(text)
    issues += ctx.check_names(text)
    issues += ctx.check_events(text)

    issues.sort(key=lambda i: (i["span"][0], i["span"][1], i["kind"]))
    out, seen = [], set()
    for issue in issues:
        key = (issue["kind"], tuple(issue["span"]), issue["claim"])
        if key not in seen:
            seen.add(key)
            out.append(issue)
    return out


def _norm(value):
    if not value:
        return ""
    value = re.sub(r"['’]s$", "", value.strip())
    value = re.sub(r"\b(?:Jr|Sr|II|III|IV)\.?$", "", value, flags=re.I).strip()
    value = value.replace(".", "")
    return re.sub(r"\s+", " ", value).lower()


def _last(value):
    norm = _norm(value)
    return norm.split()[-1] if norm else ""


def _words_between(text):
    return re.findall(r"[A-Za-z0-9'’]+", text)


def _issue(kind, start, end, claim, recorded):
    return {"kind": kind, "span": [start, end], "claim": claim, "recorded": recorded}


class _CheckContext:
    def __init__(self, sheet, speaker):
        self.sheet = sheet
        self.speaker = speaker
        self.teams = sheet.get("teams") or []
        self.game = sheet.get("game") or {}
        self.numbers = {}
        for row in sheet.get("numbers") or []:
            try:
                stat, subject, value = row
            except (TypeError, ValueError):
                continue
            if value is not None:
                self.numbers[(stat, subject)] = value

        self.person_by_norm = {}
        self.person_by_last = {}
        for t in self.teams:
            coach = t.get("coach") or {}
            if coach.get("name"):
                self._register_person(coach["name"], {"kind": "coach", "team": t, "name": coach["name"]})
            for p in t.get("players") or []:
                self._register_person(p.get("name"), {"kind": "player", "team": t, "player": p,
                                                      "name": p.get("name")})

        self.known_names = set()
        for name in sheet.get("names") or []:
            self.known_names.add(_norm(name))
        for fig in PUBLIC_FIGURES:
            self.known_names.add(_norm(fig))
        if speaker:
            self.known_names.add(_norm(speaker))

        self.team_terms = {}
        for t in self.teams:
            for value in (t.get("abbr"), t.get("name"), t.get("city"), _city_name(t)):
                if value:
                    self.team_terms[_norm(value)] = t

        self.place_terms = {_norm(p) for p in PLACES} | {_norm(c) for c in COMPANIES}
        self.place_terms |= {_norm(t) for t in NFL_TEAMS} | {_norm(t) for t in FOOTBALL_PHRASES}
        self.place_terms |= set(self.team_terms)

        self.person_pattern, self.person_map = self._person_matcher()
        self.team_pattern, self.team_map = self._team_matcher()

    def _register_person(self, name, subject):
        if not name:
            return
        self.person_by_norm.setdefault(_norm(name), subject)
        last = _last(name)
        if len(last) >= 3:
            self.person_by_last.setdefault(last, subject)

    def _person_matcher(self):
        names = {}
        for norm, subject in self.person_by_norm.items():
            if len(norm.split()) >= 2:
                names[norm] = subject
        for last, subject in self.person_by_last.items():
            names.setdefault(last, subject)
        if not names:
            return None, {}
        pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
                             + r")\b", re.I)
        return pattern, {_norm(k): v for k, v in names.items()}

    def _team_matcher(self):
        if not self.team_terms:
            return None, {}
        pattern = re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(self.team_terms, key=len, reverse=True))
                             + r")\b", re.I)
        return pattern, self.team_terms

    # -- subject resolution ----------------------------------------------------------------------------------
    def _subject_before(self, text, pos, window=6):
        best = None
        if self.person_pattern:
            for m in self.person_pattern.finditer(text, 0, pos):
                if len(_words_between(text[m.end():pos])) > window:
                    continue
                if best is None or m.end() > best[0]:
                    best = (m.end(), self.person_map.get(_norm(m.group(0))))
        team_hit = None
        if self.team_pattern:
            for m in self.team_pattern.finditer(text, 0, pos):
                if len(_words_between(text[m.end():pos])) > window:
                    continue
                if team_hit is None or m.end() > team_hit[0]:
                    team_hit = (m.end(), self.team_map.get(_norm(m.group(0))))
        if team_hit and (best is None or team_hit[0] >= best[0]):
            return {"kind": "team", "team": team_hit[1]} if team_hit[1] else None
        return best[1] if best else None

    def _speaker_team(self):
        norm = _norm(self.speaker)
        last = _last(self.speaker)
        for t in self.teams:
            coach = t.get("coach") or {}
            if coach.get("name") and (norm == _norm(coach["name"]) or (last and last == _last(coach["name"]))):
                return t
        return None

    def _default_team(self):
        team = self._speaker_team()
        if team:
            return team
        for t in self.teams:
            if t.get("home"):
                return t
        return self.teams[0] if self.teams else None

    def _resolve_team(self, text, pos):
        subject = self._subject_before(text, pos)
        if not subject:
            return self._default_team()
        if subject.get("kind") == "team":
            return subject["team"]
        return subject.get("team") or self._default_team()

    def _value(self, subject, aliases):
        if not subject:
            return None
        if subject.get("kind") == "team":
            key = subject["team"].get("abbr")
        else:
            key = subject.get("name")
        for stat in aliases:
            value = self.numbers.get((stat, key))
            if value is not None:
                return value
        return None

    # -- scores ----------------------------------------------------------------------------------------------
    def check_scores(self, text):
        out = []
        allowed = self._recorded_scores()
        for m in re.finditer(r"\b(\d{1,3})\s*(?:-|–|—|to)\s*(\d{1,3})\b", text):
            a, b = int(m.group(1)), int(m.group(2))
            if a > 100 or b > 100:
                continue
            before = text[max(0, m.start() - 40):m.start()]
            after = text[m.end():m.end() + 30]
            if re.search(r"\b(record|start(?:ed)?|starting)\b", before, re.I) or \
                    re.search(r"^\s+(?:record|start(?:ed)?)\b", after, re.I):
                continue                      # the record check owns this span
            if re.search(r"^\s+(?:weeks?|week|days?|day|years?|year|round|pick|half|quarter|minutes?|seconds?|"
                         r"games?|wins?|losses?|points?|defense|front|scheme|"
                         r"in the (?:division|conference|playoffs)|at home|on the road|against)\b", after, re.I):
                continue
            if m.group(0).replace(" ", "").replace("to", "-") in ("3-4", "4-3") and \
                    re.search(r"^\s+(?:defense|front|scheme)\b", after, re.I):
                continue
            record_like = bool(re.search(r"\b(we(?:'re| are)|now|stand|standing|our record|their record)\b",
                                         before, re.I))
            if record_like and a <= 20 and b <= 20:
                team = self._resolve_team(text, m.start())
                recorded = {team.get("record_before"), team.get("record_after")} if team else set()
                recorded.discard(None)
                if f"{a}-{b}" not in recorded:
                    out.append(_issue("NUMBER", m.start(), m.end(), m.group(0),
                                      team.get("record_after") if team else None))
                continue
            if not allowed:
                continue
            if (a, b) not in allowed and (b, a) not in allowed:
                out.append(_issue("NUMBER", m.start(), m.end(), m.group(0), self._score_text()))
        return out

    def _recorded_scores(self):
        scores = set()
        home = (self.game.get("home") or {}).get("abbr")
        away = (self.game.get("away") or {}).get("abbr")
        final = self.game.get("final") or {}
        hs, as_ = final.get("home"), final.get("away")
        if hs is not None and as_ is not None:
            scores.add((hs, as_))
            scores.add((as_, hs))
        qs = self.game.get("quarter_scores") or {}
        series = [qs.get(home) or [], qs.get(away) or []]
        if all(len(s) >= 4 for s in series):
            sums = [0, 0]
            for i in range(4):
                for j, s in enumerate(series):
                    sums[j] += s[i]
                scores.add((sums[0], sums[1]))
                scores.add((sums[1], sums[0]))
        return scores

    def _score_text(self):
        final = self.game.get("final") or {}
        home = (self.game.get("home") or {}).get("abbr")
        away = (self.game.get("away") or {}).get("abbr")
        if final.get("home") is None or final.get("away") is None:
            return None
        return f"{away} {final['away']}-{final['home']} {home}"

    # -- N <stat> --------------------------------------------------------------------------------------------
    STAT_RE = re.compile(
        r"\b(\d{1,3})\s+(?:(?:straight|consecutive)\s+)?"
        r"(?:(?:receiving|rushing|passing|total|offensive|scrimmage)\s+)?"
        r"(yards?|touchdowns?|tds?|interceptions?|ints?|picks?|sacks?|turnovers?|giveaways?|takeaways?|"
        r"catches?|receptions?|carries?|carry|field\s+goals?|fgs?|points?|wins?|losses?)\b", re.I)
    STREAK_RE = re.compile(
        r"\b(\d{1,2})\s+(?:straight|consecutive)\s+(wins|losses)\b|"
        r"\b(\d{1,2})\s+(wins|losses)\s+in\s+a\s+row\b|"
        r"\b(?:won|lost)\s+(\d{1,2})\s+straight\b|"
        r"\b(\d{1,2})[\s-]game\s+(winning|losing)\s+streak\b", re.I)

    def check_stat_phrases(self, text):
        out = []
        streak_spans = []
        for m in self.STREAK_RE.finditer(text):
            streak_spans.append((m.start(), m.end()))
            n = next(int(g) for g in m.groups() if g and g.isdigit())
            if "losing" in m.group(0).lower() or "loss" in m.group(0).lower() or "lost" in m.group(0).lower():
                team_aliases = ["loss_streak"]
            else:
                team_aliases = ["win_streak"]
            team = self._resolve_team(text, m.start())
            value = self._value({"kind": "team", "team": team}, team_aliases) if team else None
            if value is None:
                continue
            if int(value) != n:
                out.append(_issue("NUMBER", m.start(), m.end(), m.group(0), value))

        for m in self.STAT_RE.finditer(text):
            if any(s <= m.start() < e for s, e in streak_spans):
                continue
            n = int(m.group(1))
            stat = _stat_key(m.group(2))
            if not stat:
                continue
            subject = self._subject_before(text, m.start())
            if subject and subject.get("kind") == "coach":
                subject = {"kind": "team", "team": subject.get("team")}
            if not subject:
                team = self._default_team()
                subject = {"kind": "team", "team": team} if team else None
            if not subject:
                continue
            context = text[max(0, m.start() - 40):m.start()].lower()
            around = text[max(0, m.start() - 25):min(len(text), m.end() + 25)].lower()
            per_game = bool(re.search(r"\b(averag\w*|per game|a game|per contest|a contest)\b", around))
            aliases = _aliases(stat, context, subject.get("kind"), per_game=per_game)
            value = self._value(subject, aliases)
            if value is None:
                continue
            if int(value) != n:
                out.append(_issue("NUMBER", m.start(), m.end(), m.group(0), value))
        return out

    # -- records and standing -------------------------------------------------------------------------------
    def check_records(self, text):
        out = []
        for m in re.finditer(r"\b(\d{1,2})\s*-\s*(\d{1,2})\b", text):
            a, b = int(m.group(1)), int(m.group(2))
            if a > 20 or b > 20:
                continue
            before = text[max(0, m.start() - 40):m.start()]
            after = text[m.end():m.end() + 25]
            if not (re.search(r"\b(record|start(?:ed)?|starting|start)\b", before, re.I)
                    or re.search(r"^\s+(?:record|start)\b", after, re.I)):
                continue
            team = self._resolve_team(text, m.start())
            if not team:
                continue
            recorded = {team.get("record_before"), team.get("record_after")}
            recorded.discard(None)
            if f"{a}-{b}" not in recorded:
                out.append(_issue("NUMBER", m.start(), m.end(), m.group(0),
                                  team.get("record_after") or team.get("record_before")))
        return out

    def check_standing(self, text):
        out = []
        for m in re.finditer(r"\bfirst place\b", text, re.I):
            team = self._resolve_team(text, m.start())
            if not team or team.get("division_place") is None:
                continue
            if team["division_place"] != 1:
                out.append(_issue("EVENT", m.start(), m.end(), m.group(0),
                                  f"division place {team['division_place']}"))
        for m in re.finditer(r"\bin the playoffs\b", text, re.I):
            team = self._resolve_team(text, m.start())
            if not team or team.get("conference_seed") is None:
                continue
            if team["conference_seed"] > PLAYOFF_SEEDS:
                out.append(_issue("EVENT", m.start(), m.end(), m.group(0),
                                  f"conference seed {team['conference_seed']}"))
        return out

    # -- names -----------------------------------------------------------------------------------------------
    def check_names(self, text):
        out = []
        for candidate in _capitalised_candidates(text):
            words, start, end = candidate
            words = _strip_titles(words)
            if len(words) < 2:
                continue
            norm = _norm(" ".join(w for w, _, _ in words))
            if not norm:
                continue
            start = words[0][1]
            end = words[-1][2]
            if _inside_quotes(text, start):
                continue
            if any(_norm(w) in NAME_STOPWORDS for w, _, _ in words):
                continue
            if norm in self.known_names or norm in self.place_terms:
                continue
            out.append(_issue("NAME", start, end, text[start:end], None))
        return out

    # -- events ----------------------------------------------------------------------------------------------
    EVENT_RE = re.compile(
        r"(?<![\w'’])([A-Z][\w'’.-]*(?:\s+[A-Z][\w'’.-]*){0,2})['’]s\s+"
        r"(touchdowns?|interceptions?|picks?|fumbles?|sacks?)\b")

    def check_events(self, text):
        out = []
        for m in self.EVENT_RE.finditer(text):
            name, event = m.group(1), m.group(2).lower()
            subject = self.person_by_norm.get(_norm(name)) or self.person_by_last.get(_last(name))
            if not subject or subject.get("kind") != "player":
                continue
            line = subject["player"].get("line") or {}
            if event.startswith("touchdown"):
                keys = ("touchdowns", "pass_tds", "rush_tds", "rec_tds", "def_tds")
            elif event.startswith("interception") or event.startswith("pick"):
                keys = ("interceptions", "def_ints")
            elif event.startswith("fumble"):
                keys = ("fumbles", "def_forced_fumbles", "def_fumble_recoveries")
            else:
                keys = ("sacks",)
            values = [line.get(k) for k in keys if line.get(k) is not None]
            if not values:
                continue
            if max(values) <= 0:
                out.append(_issue("EVENT", m.start(), m.end(), m.group(0), max(values)))
        return out


def _capitalised_candidates(text):
    """Runs of 2-3 capitalised words (whitespace-separated), each as [(word, start, end), ...]."""
    tokens = [(m.group(0), m.start(), m.end()) for m in re.finditer(r"[A-Za-z][A-Za-z'’.-]*", text)]
    runs, current = [], []
    for token in tokens:
        if current:
            gap = text[current[-1][2]:token[1]]
            if gap and not re.fullmatch(r"\s+", gap):
                runs.append(current)
                current = []
        if _is_capitalised(token[0]):
            current.append(token)
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)

    out = []
    for run in runs:
        for i in range(len(run)):
            for n in (2, 3):
                if i + n <= len(run):
                    words = run[i:i + n]
                    out.append((words, words[0][1], words[-1][2]))
    return out


def _is_capitalised(word):
    if not word:
        return False
    if word[0].isupper() and any(c.islower() for c in word):
        return True
    # initials: A. / J.J.
    return bool(re.fullmatch(r"(?:[A-Z]\.){1,3}", word))


def _strip_titles(words):
    while words:
        first = _norm(words[0][0])
        if first in ("head", "coach", "coordinator", "assistant", "offensive", "defensive", "special",
                     "general", "manager", "owner", "president", "mr", "mrs", "ms", "dr"):
            words = words[1:]
            # "Head Coach" is two titles
            continue
        break
    return words


def _inside_quotes(text, pos):
    """True when `pos` sits inside a double-quote pair (straight or curly). Single apostrophes are possessives."""
    for open_q, close_q in (('"', '"'), ("“", "”")):
        prefix = text[:pos]
        if open_q == close_q:
            if prefix.count(open_q) % 2:
                return True
        elif prefix.count(open_q) > prefix.count(close_q):
            return True
    return False


def _stat_key(word):
    w = re.sub(r"\s+", " ", word.lower().strip())
    if w.startswith("yard"):
        return "yards"
    if w in ("touchdown", "touchdowns", "td", "tds"):
        return "touchdowns"
    if w in ("interception", "interceptions", "int", "ints", "pick", "picks"):
        return "interceptions"
    if w in ("sack", "sacks"):
        return "sacks"
    if w in ("turnover", "turnovers", "giveaway", "giveaways"):
        return "turnovers"
    if w in ("takeaway", "takeaways"):
        return "takeaways"
    if w in ("catch", "catches", "reception", "receptions"):
        return "receptions"
    if w in ("carry", "carries"):
        return "carries"
    if w in ("field goal", "field goals", "fg", "fgs"):
        return "field_goals"
    if w in ("point", "points"):
        return "points"
    if w in ("win", "wins"):
        return "wins"
    if w in ("loss", "losses"):
        return "losses"
    return None


def _aliases(stat, context, kind, per_game=False):
    if stat == "yards":
        if re.search(r"\b(threw|throw|throwing|pass|passing)\b", context):
            aliases = ["pass_yards", "yards"]
        elif re.search(r"\b(ran|run|running|rush|rushing)\b", context):
            aliases = ["rush_yards", "yards"]
        elif re.search(r"\b(caught|catch|catches|reception|receptions|rec|receiving)\b", context):
            aliases = ["rec_yards", "yards"]
        elif kind == "team":
            aliases = ["yards", "pass_yards", "rush_yards", "rec_yards"]
        else:
            aliases = ["pass_yards", "rush_yards", "rec_yards", "yards"]
    elif stat == "touchdowns":
        if re.search(r"\b(threw|throw|throwing|pass|passing)\b", context):
            aliases = ["pass_tds", "touchdowns"]
        elif re.search(r"\b(ran|run|running|rush|rushing)\b", context):
            aliases = ["rush_tds", "touchdowns"]
        elif re.search(r"\b(caught|catch|catches|reception|receptions|rec|receiving)\b", context):
            aliases = ["rec_tds", "touchdowns"]
        else:
            aliases = ["touchdowns", "pass_tds", "rush_tds", "rec_tds", "def_tds"]
    elif stat == "interceptions":
        aliases = ["interceptions", "def_ints"]
    elif stat == "sacks":
        aliases = ["sacks"]
    elif stat == "turnovers":
        aliases = ["turnovers"]
    elif stat == "takeaways":
        aliases = ["takeaways"]
    elif stat == "receptions":
        aliases = ["receptions"]
    elif stat == "carries":
        aliases = ["carries", "rush_attempts"]
    elif stat == "field_goals":
        aliases = ["field_goals", "kick_fgm"]
    elif stat == "points":
        if re.search(r"\b(held|holding|allow|allowed|gave up|give up|giving up)\b", context):
            return ["points_allowed", "points"]
        if re.search(r"\b(?:won|lost|win|lose|winning|losing)\s+by\b|\bby\s*$", context):
            return ["margin", "points"]
        aliases = ["points"]
    elif stat == "wins":
        aliases = ["wins"]
    elif stat == "losses":
        aliases = ["losses"]
    else:
        return []
    if per_game:
        return [f"season_{a}_pg" for a in aliases]
    return aliases


def _city_name(team):
    if team.get("city") and team.get("name"):
        return f"{team['city']} {team['name']}"
    return None


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


def _round(value):
    if value is None:
        return None
    return round(float(value), 1)


def _sum_present(*values):
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def _accumulate(target, key, value):
    if value is None:
        return
    target[key] = (target.get(key) or 0) + value
