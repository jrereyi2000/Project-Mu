"""
Loads a save-reader snapshot (pipeline/save_reader/pc_save_reader.mjs) into the database in one transaction
(docs/in_progress/PC_MIGRATION_PLAN.md §5.1). The PC replacement for load_capture.py: players are matched on
players.madden_pgid (Madden's Player.PresentationId), so there is no name matching and no OCR check.

  python3 -m pipeline.load_save captures/<run>/save.json [--season N] [--commit]

Without --commit the load runs and reports, then rolls back. --season defaults to the save's calendar year; the
save's own franchise year picks which games and season rows belong to it (older years are history Madden kept).

Writes, for that season:
  players / player_seasons   every Signed, PracticeSquad or FreeAgent player (a pgid not yet stored gets a new
                             players row); team, position, age, OVR, experience, dev trait, archetype
  player_attributes          every player's 54 native ratings (Speed, Acceleration, etc.) as JSONB
  games                      regular-season and playoff games, scheduled and completed (preseason skipped)
  team_stats                 quarter and OT scores plus Madden's team box score (SeasonGame team stat cache:
                             yards, first downs, 3rd/4th downs, turnovers, red zone, penalties, possession) for
                             completed games; completions from the player lines once Madden links them (a save
                             without a box score falls back to line totals)
  player_stats, game_stat_*  per-game lines: a game the save has lines for has its stored lines replaced
                             (Madden purges old weeks, so a game with no lines in this save keeps what it has)
  season_stats, season_stat_* season totals (a player traded mid-season is summed across teams)
  player_contracts (+ years)  every rostered player's contract: the season's ACTIVE rows are superseded first
  injuries                   the save's injured players for the league's current week (that week's rows replaced)
  game_highlights            scoring plays from SeasonGame.ScoringSummaries (Madden keeps only recent weeks);
                             a game whose highlights came from a capture keeps them
  league_state               current week from the save (never moves back; only for the league's season or the
                             next one) and, with --commit, a `captures` row (mode `save`) for the run
Not yet: team_cap_seasons (the save's team cap fields aren't in a confirmed unit).

Save lines are stored as line_source CAPTURED, season rows as source CAPTURE and contracts as source SCAN: all are
real data read from Madden, which is what readers treat those values as.
"""
import argparse
import json
import os
import sys

import psycopg

from pipeline.capture_matching import db_player_name
from pipeline.contracts import insert_contract, supersede_active
from pipeline.load_capture import team_line_totals

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


class LoadError(Exception):
    pass


# ------------------------------------------------------------------ mapping ----
FREE_AGENT_TEAM_INDEX = 32
LOADED_STATUSES = ("Signed", "PracticeSquad", "FreeAgent")
TEAM_ABBR_ALIASES = {"ARI": "AZ"}          # Madden's short name -> teams.abbreviation where they differ

# Madden 27 positions -> Project Mu's. Madden's depth chart shows ROLB as WILL (checked on the PC, 2026-10-29), so
# LOLB is SAM.
POSITIONS = {"LE": "LEDG", "RE": "REDG", "MLB": "MIKE", "LOLB": "SAM", "ROLB": "WILL"}

# Madden 27's TraitDevelopment enum gives two names to each stored value: College_Impact = Star (1), College_Star =
# Superstar (2), College_Elite = XFactor (3) (schema M27_620_0; the library reads back the first name). So the
# College_ names are the NFL tiers, not a separate scale.
DEV_TRAITS = {"Normal": "Normal", "Star": "Star", "Superstar": "Superstar", "XFactor": "X-Factor", "Hidden": "Hidden",
              "College_Impact": "Star", "College_Star": "Superstar", "College_Elite": "X-Factor"}

ARCHETYPE_WORDS = {"MantoMan": "Man To Man", "SmallerSpeedRusher": "Speed Rusher"}

PLAYOFF_WEEKS = {"WildcardPlayoff": (19, "WILD_CARD"), "DivisionalPlayoff": (20, "DIVISIONAL"),
                 "ConferencePlayoff": (21, "CONFERENCE"), "SuperBowl": (23, "SUPER_BOWL")}
FINISHED = ("HomeWon", "AwayWon", "Tie")


def position(madden_pos):
    return POSITIONS.get(madden_pos, madden_pos)


def dev_trait(madden_dev):
    if not madden_dev:
        return None
    return DEV_TRAITS.get(madden_dev, madden_dev)


def archetype(player_type):
    """'HB_PowerBack' -> 'Power Back', 'CB_MantoMan' -> 'Man To Man'."""
    if not player_type or "_" not in player_type:
        return player_type
    word = player_type.split("_", 1)[1]
    if word in ARCHETYPE_WORDS:
        return ARCHETYPE_WORDS[word]
    out = ""
    for i, ch in enumerate(word):
        if ch.isupper() and i and not word[i - 1].isupper():
            out += " "
        out += ch
    return out


def height_text(inches):
    return f"{inches // 12}'{inches % 12}\"" if inches else None


def game_week(week_type, week_index):
    """(week, game_type) for a game Project Mu stores, or None (preseason, Pro Bowl)."""
    if week_type == "RegularSeason":
        return week_index + 1, "REGULAR_SEASON"
    return PLAYOFF_WEEKS.get(week_type)


# ---------------------------------------------------------------- stat lines ----
def _ratio(a, b, digits=1, scale=1):
    return round(scale * a / b, digits) if b else None


def passer_rating(comp, att, yds, td, ints):
    if not att:
        return None
    clamp = lambda x: max(0.0, min(2.375, x))
    a = clamp((comp / att - 0.3) * 5)
    b = clamp((yds / att - 3) * 0.25)
    c = clamp(td / att * 20)
    d = clamp(2.375 - ints / att * 25)
    return round((a + b + c + d) / 6 * 100, 1)


def offense_cols(s):
    v = {
        "completions": s.get("PASSCOMPLETED", 0), "pass_attempts": s.get("PASSATTEMPTS", 0),
        "pass_yards": s.get("PASSYARDS", 0), "pass_tds": s.get("PASSTDS", 0), "interceptions": s.get("PASSINTS", 0),
        "pass_long": s.get("PASSLONGEST", 0), "pass_sacks": s.get("PASSSACKED", 0),
        "rush_attempts": s.get("RUSHATTEMPTS", 0), "rush_yards": s.get("RUSHYARDS", 0), "rush_tds": s.get("RUSHTDS", 0),
        "fumb": s.get("RUSHFUMBLES", 0), "rush_btk": s.get("RUSHBROKENTACKLES", 0),
        "rush_yac": s.get("RUSHYARDSAFTER1STHIT", 0), "rush_long": s.get("RUSHLONGEST", 0),
        "receptions": s.get("RECEIVECATCHES", 0), "rec_yards": s.get("RECEIVEYARDS", 0), "rec_tds": s.get("RECEIVETDS", 0),
        "rec_rac": s.get("RECEIVEYARDSAFTER", 0), "rec_long": s.get("RECEIVELONGEST", 0),
    }
    return v


def offense_rates(v):
    v["pass_comp_pct"] = _ratio(v["completions"], v["pass_attempts"], scale=100)
    v["pass_ypa"] = _ratio(v["pass_yards"], v["pass_attempts"])
    v["pass_rating"] = passer_rating(v["completions"], v["pass_attempts"], v["pass_yards"], v["pass_tds"], v["interceptions"])
    v["rush_avg"] = _ratio(v["rush_yards"], v["rush_attempts"])
    v["rec_avg"] = _ratio(v["rec_yards"], v["receptions"])
    v["rec_rac_avg"] = _ratio(v["rec_rac"], v["receptions"])
    return v


# Madden's DEFTACKLES is solo tackles and ASSDEFTACKLES assists; the in-game box score's tackle column is their sum
# (checked on the PC, 2026-10-29). A sack is DLINESACKS plus half a sack per DLINEHALFSACK.
def defense_cols(s):
    solo, ast = s.get("DEFTACKLES", 0), s.get("ASSDEFTACKLES", 0)
    v = {
        "solo": solo, "assists": ast, "tackles": solo + ast, "tfl": s.get("DEFTACKLESFORLOSS", 0),
        "sacks": s.get("DLINESACKS", 0) + 0.5 * s.get("DLINEHALFSACK", 0), "ints": s.get("DSECINTS", 0),
        "int_yds": s.get("DSECINTRETURNYARDS", 0), "int_long": s.get("DSECINTLONGESTRETURN", 0),
        "deflections": s.get("DEFPASSDEFLECTIONS", 0), "forced_fumbles": s.get("DLINEFORCEDFUMBLES", 0),
        "fumble_recoveries": s.get("DLINEFUMBLERECOVERIES", 0), "fumble_yds": s.get("DLINEFUMBLERECOVERYYARDS", 0),
        "blocks": s.get("DLINEBLOCKS", 0), "safeties": s.get("DLINESAFETIES", 0),
        "tds": s.get("DSECINTTDS", 0) + s.get("DLINEFUMBLETDS", 0),
    }
    return v


def defense_rates(v):
    v["int_avg"] = _ratio(v["int_yds"], v["ints"])
    return v


def kicking_cols(s):
    return {
        "fgm": s.get("KICKFGMADE", 0), "fga": s.get("KICKFGATTEMPTS", 0), "fg_long": s.get("KICKFGLONGEST", 0),
        "fg_blocked": s.get("KICKFGBLOCKED", 0), "xpm": s.get("KICKEPMADE", 0), "xpa": s.get("KICKEPATTEMPTS", 0),
        "xp_blocked": s.get("KICKEPBLOCKED", 0),
        "fgm_29": s.get("KICKFGMADE29ORLESS", 0), "fga_29": s.get("KICKFGATTEMPTS29ORLESS", 0),
        "fgm_39": s.get("KICKFGMADE30TO39", 0), "fga_39": s.get("KICKFGATTEMPTS30TO39", 0),
        "fgm_49": s.get("KICKFGMADE40TO49", 0), "fga_49": s.get("KICKFGATTEMPTS40TO49", 0),
        "fgm_50": s.get("KICKFGMADE50ORMORE", 0), "fga_50": s.get("KICKFGATTEMPTS50ORMORE", 0),
        "kickoffs": s.get("KICKNUMKICKOFFS", 0), "touchbacks": s.get("KICKTOUCHBACKS", 0),
    }


def kicking_rates(v):
    v["fg_pct"] = _ratio(v["fgm"], v["fga"], scale=100)
    v["xp_pct"] = _ratio(v["xpm"], v["xpa"], scale=100)
    return v


def punting_cols(s):
    return {
        "punts": s.get("PUNTATTEMPTS", 0), "yards": s.get("PUNTYARDS", 0), "net_yards": s.get("PUNTNETYARDS", 0),
        "blocked": s.get("PUNTBLOCKED", 0), "inside_20": s.get("PUNTIN20", 0), "touchbacks": s.get("PUNTTOUCHBACKS", 0),
        "longest": s.get("PUNTLONGEST", 0),
    }


def punting_rates(v):
    v["average"] = _ratio(v["yards"], v["punts"])
    v["net_average"] = _ratio(v["net_yards"], v["punts"])
    return v


def return_cols(prefix):
    def cols(s):
        return {"returns": s.get(f"{prefix}ATTEMPTS", 0), "yards": s.get(f"{prefix}YARDS", 0),
                "tds": s.get(f"{prefix}TDS", 0), "longest": s.get(f"{prefix}LONGEST", 0)}
    return cols


def return_rates(v):
    v["average"] = _ratio(v["yards"], v["returns"])
    return v


# (game table, season table, Madden fields -> columns, rates from the summed columns). Every Madden stat row is
# run through every group; a group whose columns are all zero for that row adds nothing.
GROUPS = {
    "offense": ("player_stats", "season_stats", offense_cols, offense_rates),
    "defense": ("game_stat_defense", "season_stat_defense", defense_cols, defense_rates),
    "kicking": ("game_stat_kicking", "season_stat_kicking", kicking_cols, kicking_rates),
    "punting": ("game_stat_punting", "season_stat_punting", punting_cols, punting_rates),
    "kickreturn": ("game_stat_kickreturn", "season_stat_kickreturn", return_cols("KRET"), return_rates),
    "puntreturn": ("game_stat_puntreturn", "season_stat_puntreturn", return_cols("PRET"), return_rates),
    "blocking": ("game_stat_blocking", "season_stat_blocking", lambda s: {"sacks_allowed": s.get("OLINESACKSALLOWED", 0)}, None),
}
LONGS = ("pass_long", "rush_long", "rec_long", "int_long", "fg_long", "longest")


def merge_into(acc, vals):
    """Sums counters and keeps the max of the longs (two Madden rows for one game or one season)."""
    for k, v in vals.items():
        if k in LONGS:
            acc[k] = max(acc.get(k, 0), v)
        else:
            acc[k] = acc.get(k, 0) + v
    return acc


def group_lines(rows):
    """Madden stat rows (one player, one game or one season) -> {group: columns} for the non-zero groups,
    plus downs_played / games_played / games_started taken as the max over the rows."""
    out, general = {}, {}
    for r in rows:
        s = r["stats"]
        for name, (_, _, cols, _) in GROUPS.items():
            vals = cols(s)
            if any(vals.values()):
                merge_into(out.setdefault(name, {}), vals)
        for src, col in (("DOWNSPLAYED", "downs_played"), ("GAMESPLAYED", "games_played")):
            if s.get(src):
                general[col] = max(general.get(col, 0), s[src])
    for name, vals in out.items():
        rates = GROUPS[name][3]
        if rates:
            rates(vals)
    if general:
        out["general"] = general
    return out


# --------------------------------------------------------------- contracts ----
# Madden money is in $10,000 units. A contract year's cap hit is SalaryYear[i] + BonusYear[i] (the bonus is
# already prorated per year), and CurrentYear is the index of this season: De'Von Achane's year 1 is 1050 + 327 =
# 1377 = his PLYR_CAPSALARY, $13.77M.
MONEY_UNIT = 10_000


def contract_values(c, season):
    """A snapshot contract -> insert_contract's keyword values (whole dollars). Cut penalty = the bonus still to be
    charged (it all accelerates on release); cut savings = this year's cap hit minus that penalty. Both are
    computed with that rule, not read from Madden."""
    length, cur_year = c["length"], c["current_year"]
    sal, bon = c["salary_by_year"], c["bonus_by_year"]
    hit = lambda i: (sal[i] if i < len(sal) else 0) + (bon[i] if i < len(bon) else 0)
    remaining = range(cur_year, length)
    penalty = sum(bon[i] for i in remaining if i < len(bon))
    return {
        "length": length,
        "years_remaining": max(length - cur_year, 0),
        "total_value": sum(hit(i) for i in range(length)) * MONEY_UNIT,
        "bonus": sum(bon[i] for i in range(length) if i < len(bon)) * MONEY_UNIT,
        "cut_penalty": penalty * MONEY_UNIT,
        "cut_savings": (hit(cur_year) - penalty) * MONEY_UNIT if cur_year < length else 0,
        "no_trade_clause": c.get("no_trade_clause", False),
        "incentive": None,
        "years": {season + i - cur_year: hit(i) * MONEY_UNIT for i in remaining},
    }


# ----------------------------------------------------------------- injuries ----
def words(enum_value):
    """'KneeACLSprain' -> 'KNEE ACL SPRAIN' (the injury report's upper-case style)."""
    out = ""
    for i, ch in enumerate(enum_value or ""):
        prev = enum_value[i - 1] if i else ""
        nxt = enum_value[i + 1] if i + 1 < len(enum_value) else ""
        if i and ch.isupper() and (prev.islower() or (nxt.islower() and prev.isupper())):
            out += " "
        out += ch
    return out.upper()


def injury_values(inj):
    """(injury_type, duration, weeks_remaining, is_ir) in the injury report's style ('WEEK TO WEEK', '2 - 4 WEEKS').
    A healed player Madden still has on IR has type Invalid_: no injury type."""
    lo, hi = max(inj.get("min_weeks") or 0, 0), max(inj.get("max_weeks") or 0, 0)
    if hi <= 1:
        duration, weeks = "WEEK TO WEEK", 1
    elif lo == hi:
        duration, weeks = f"{hi} WEEKS", hi
    else:
        duration, weeks = f"{lo} - {hi} WEEKS", hi
    kind = inj.get("type")
    return (words(kind) if kind and kind != "Invalid_" else None), duration, weeks, bool(inj.get("injured_reserve"))


# --------------------------------------------------------------- highlights ----
QUARTERS = {1: "Q1", 2: "Q2", 3: "Q3", 4: "Q4", 5: "OT"}
SCORE_TYPES = {6: "TOUCHDOWN", 7: "TOUCHDOWN", 8: "TOUCHDOWN", 3: "FIELD GOAL", 2: "SAFETY"}
CONVERSION_TYPES = {1: "EXTRA POINT", 2: "TWO-POINT CONVERSION"}


def scoring_highlights(game, home_abbr, away_abbr):
    """SeasonGame scoring summaries -> game_highlights plays (the shape engines/game_script_engine reads: quarter,
    clock, play_type, play, points, team, scoring). Madden sometimes counts a touchdown's conversion in the
    touchdown's row (0 -> 7) and sometimes only in the next row's score before, so the walk keeps a running score:
    any gap between it and a row's score before (or the final) is that side's conversion, at the previous play's
    time. The plays always add up to the final score."""
    plays = sorted(game.get("scoring_plays") or [], key=lambda p: (p["quarter"], -p["seconds_left"]))
    sides = (("home", home_abbr), ("away", away_abbr))
    running = {"home": 0, "away": 0}
    out, last = [], None

    def add(points, kind, at, abbr, conversion=False):
        quarter = QUARTERS.get(at["quarter"], "OT")
        clock = f"{at['seconds_left'] // 60}:{at['seconds_left'] % 60:02d}"
        out.append({"quarter": quarter, "clock": clock, "play_type": kind,
                    "play": f"{abbr} {kind}{' GOOD' if conversion else ''}", "points": points, "team": abbr,
                    "scoring": True, "source": "SAVE"})

    def catch_up(target, at):
        for side, abbr in sides:
            gap = target[side] - running[side]
            if gap > 0 and at is not None:
                add(gap, CONVERSION_TYPES.get(gap, "SCORE"), at, abbr, conversion=gap in CONVERSION_TYPES)
                running[side] = target[side]

    for p in plays:
        catch_up({"home": p["home_before"], "away": p["away_before"]}, last)
        for side, abbr in sides:
            pts = p[f"{side}_after"] - max(p[f"{side}_before"], running[side])
            if pts > 0:
                add(pts, SCORE_TYPES.get(pts, "SCORE"), p, abbr)
                running[side] += pts
        last = p
    catch_up({"home": game["home_score"], "away": game["away_score"]}, last)
    return out


# -------------------------------------------------------------------- load ----
def team_ids(cur, snapshot):
    cur.execute("SELECT abbreviation, id FROM teams")
    by_abbr = {a: i for a, i in cur.fetchall() if a}
    ids, missing = {}, []
    for t in snapshot["teams"]:
        if t["team_index"] >= FREE_AGENT_TEAM_INDEX:
            continue
        abbr = TEAM_ABBR_ALIASES.get(t["short_name"], t["short_name"])
        if abbr in by_abbr:
            ids[t["team_index"]] = by_abbr[abbr]
        else:
            missing.append(t["short_name"])
    if missing:
        raise LoadError(f"teams not in the teams table: {', '.join(missing)}")
    return ids


def load_players(cur, snapshot, season, teams, report):
    cur.execute("SELECT madden_pgid, id FROM players WHERE madden_pgid IS NOT NULL")
    by_pgid = dict(cur.fetchall())
    ids, created = {}, 0
    for p in snapshot["players"]:
        if p["status"] not in LOADED_STATUSES:
            continue
        pid = by_pgid.get(p["pgid"])
        full = f"{p['first_name']} {p['last_name']}".strip()
        if pid is None:
            cur.execute("INSERT INTO players (player_name, full_name, height, weight, player_number, origin, madden_pgid) "
                        "VALUES (%s, %s, %s, %s, %s, 'SAVE', %s) RETURNING id",
                        (db_player_name(p["first_name"], p["last_name"]), full, height_text(p["height_in"]),
                         p["weight_lbs"], p["jersey"], p["pgid"]))
            pid = cur.fetchone()[0]
            created += 1
        else:
            cur.execute("UPDATE players SET height = %s, weight = %s, player_number = %s WHERE id = %s",
                        (height_text(p["height_in"]), p["weight_lbs"], p["jersey"], pid))
        team = teams.get(p["team_index"]) if p["status"] != "FreeAgent" else None
        cur.execute("INSERT INTO player_seasons (player_id, team_id, season, position, age, overall_rating, "
                    "years_of_experience, dev_trait, archetype) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (player_id, season) DO UPDATE SET team_id = EXCLUDED.team_id, "
                    "position = EXCLUDED.position, age = EXCLUDED.age, overall_rating = EXCLUDED.overall_rating, "
                    "years_of_experience = EXCLUDED.years_of_experience, dev_trait = EXCLUDED.dev_trait, "
                    "archetype = EXCLUDED.archetype",
                    (pid, team, season, position(p["position"]), p["age"], p["overall"], p["years_pro"],
                     dev_trait(p["dev_trait"]), archetype(p["archetype"])))
        ids[p["pgid"]] = (pid, team, position(p["position"]), db_player_name(p["first_name"], p["last_name"]))
    report["players"] = len(ids)
    report["players_created"] = created
    return ids


def load_player_attributes(cur, snapshot, season, players, report, capture_folder=None):
    """Upserts each player's 54 Madden ratings into player_attributes for the season."""
    folder = capture_folder or f"save_{season}"
    rows = []
    for p in snapshot["players"]:
        who = players.get(p["pgid"])
        ratings = p.get("ratings")
        if who is None or not ratings:
            continue
        pid, team, pos, _ = who
        rows.append((
            pid, season, team, folder, pos, p.get("age"),
            p.get("overall"), p.get("overall"), "ok", len(ratings),
            json.dumps(ratings), "native_save"
        ))
    if not rows:
        report["player_attributes"] = 0
        return
    cur.executemany("""
        INSERT INTO player_attributes (
            player_id, season, team_id, capture_folder, position, age,
            overall_rating, predicted_ovr, ovr_check, confirmed, ratings, flags
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (player_id, capture_folder) DO UPDATE SET
            season = EXCLUDED.season,
            team_id = EXCLUDED.team_id,
            position = EXCLUDED.position,
            age = EXCLUDED.age,
            overall_rating = EXCLUDED.overall_rating,
            predicted_ovr = EXCLUDED.predicted_ovr,
            ovr_check = EXCLUDED.ovr_check,
            confirmed = EXCLUDED.confirmed,
            ratings = EXCLUDED.ratings,
            flags = EXCLUDED.flags
    """, rows)
    report["player_attributes"] = len(rows)


def load_games(cur, snapshot, season, teams, report):
    """Upserts this franchise year's games; returns {snapshot game row: (game id, week, home id, away id)}."""
    year = snapshot["season"]["franchise_year"]
    games, completed = {}, 0
    for g in snapshot["games"]:
        wk = game_week(g["week_type"], g["week_index"])
        if g["franchise_year"] != year or wk is None:
            continue
        week, game_type = wk
        home, away = teams.get(g["home_team_index"]), teams.get(g["away_team_index"])
        if home is None or away is None:
            continue
        done = g["status"] in FINISHED
        cur.execute("SELECT id, status, home_score, away_score FROM games WHERE season = %s AND week = %s "
                    "AND home_team_id = %s AND away_team_id = %s", (season, week, home, away))
        row = cur.fetchone()
        if row is None:
            cur.execute("INSERT INTO games (season, week, home_team_id, away_team_id, home_score, away_score, status, "
                        "game_type, excitement_index) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL) RETURNING id",
                        (season, week, home, away, g["home_score"] if done else 0, g["away_score"] if done else 0,
                         "COMPLETED" if done else "SCHEDULED", game_type))
            gid = cur.fetchone()[0]
        else:
            gid = row[0]
            changed = done and (row[1] != "COMPLETED" or (row[2], row[3]) != (g["home_score"], g["away_score"]))
            if changed:   # excitement_index NULL: the engines' only_missing pass re-scores the game
                cur.execute("UPDATE games SET home_score = %s, away_score = %s, status = 'COMPLETED', "
                            "excitement_index = NULL WHERE id = %s", (g["home_score"], g["away_score"], gid))
        completed += done
        games[g["row"]] = (gid, week, home, away, done, g)
    report["games"] = len(games)
    report["games_completed"] = completed
    return games


def line_team(rows, snap_game, home, away, fallback):
    """The team a player's line counts for in a game: the side opposite the line's OpposingTeam (so a player traded
    since keeps the team they played for), else their current team."""
    opp = next((r["opponent_team_index"] for r in rows if r.get("opponent_team_index") is not None), None)
    if opp == snap_game["home_team_index"]:
        return away
    if opp == snap_game["away_team_index"]:
        return home
    return fallback


def load_game_lines(cur, snapshot, games, players, report):
    lines = {}            # (game id, player id) -> [madden rows]
    meta = {}
    for p in snapshot["players"]:
        who = players.get(p["pgid"])
        if who is None:
            continue
        for r in p["game_stats"]:
            g = games.get(r.get("game_row"))
            if g is None or not g[4]:
                continue
            lines.setdefault((g[0], who[0]), []).append(r)
            meta[(g[0], who[0])] = (who, g)
    game_ids = sorted({gid for gid, _ in lines})
    if game_ids:      # the save is authoritative for a game it has lines for
        for table in [v[0] for v in GROUPS.values()] + ["game_stat_general"]:
            cur.execute(f"DELETE FROM {table} WHERE game_id = ANY(%s)", (game_ids,))
    n = 0
    for key, rows in lines.items():
        (pid, season_team, pos, name), g = meta[key]
        gid, _, home, away, _, snap_game = g
        team = line_team(rows, snap_game, home, away, season_team)
        for group, vals in group_lines(rows).items():
            table = "game_stat_general" if group == "general" else GROUPS[group][0]
            if table == "game_stat_general":
                vals = {"downs_played": vals["downs_played"]} if vals.get("downs_played") else None
                if vals is None:
                    continue
            cols = list(vals)
            if table == "player_stats":
                cur.execute(f"INSERT INTO player_stats (game_id, player_id, player_name, team_id, position, line_source, "
                            f"{', '.join(cols)}) VALUES (%s, %s, %s, %s, %s, 'CAPTURED', {', '.join(['%s'] * len(cols))})",
                            (gid, pid, name, team, pos, *[vals[c] for c in cols]))
            else:
                cur.execute(f"INSERT INTO {table} (game_id, player_id, team_id, position, {', '.join(cols)}) "
                            f"VALUES (%s, %s, %s, %s, {', '.join(['%s'] * len(cols))})",
                            (gid, pid, team, pos, *[vals[c] for c in cols]))
            n += 1
    report["game_line_rows"] = n
    report["games_with_lines"] = len(game_ids)
    return game_ids


def team_box(ts):
    """A SeasonGame team stat cache (Madden's TeamStats row) -> team_stats columns. Passing yards are the passers'
    gross yards (OFFPASSYARDS equals the summed lines, sacks not taken off) and total_offense = passing + rushing;
    total_yards_gained adds return yards; red_zone_pct is trips ending in a TD or FG, as the captured box score had it."""
    rz_att, rz_td, rz_fg = ts.get("OFFREDZONES", 0), ts.get("OFFREDZONETDS", 0), ts.get("OFFREDZONEFGS", 0)
    return {
        "first_downs": ts.get("FIRSTDOWNS", 0), "total_offense": ts.get("OFFYARDS", 0),
        "total_yards_gained": ts.get("TOTALYARDS", 0),
        "total_plays": ts.get("RUSHATTEMPTS", 0) + ts.get("PASSATTEMPTS", 0) + ts.get("SACKSALLOWED", 0),
        "rushing_attempts": ts.get("RUSHATTEMPTS", 0), "rushing_yards": ts.get("OFFRUSHYARDS", 0),
        "rushing_tds": ts.get("RUSHTDS", 0), "passing_attempts": ts.get("PASSATTEMPTS", 0),
        "passing_yards": ts.get("OFFPASSYARDS", 0), "passing_tds": ts.get("PASSTDS", 0),
        "third_down_attempts": ts.get("THIRDDOWNS", 0), "third_down_conversions": ts.get("THIRDDOWNCONV", 0),
        "fourth_down_attempts": ts.get("FOURTHDOWNS", 0), "fourth_down_conversions": ts.get("FOURTHDOWNCONV", 0),
        "turnovers": ts.get("GIVEAWAYS", 0), "punt_return_yards": ts.get("PUNTRETURNYARDS", 0),
        "kick_return_yards": ts.get("KICKRETURNYARDS", 0),
        "two_point_conversions": ts.get("TWOPOINTCONVMADE", 0), "two_point_attempts": ts.get("TWOPOINTCONVATTEMPTS", 0),
        "red_zone_attempts": rz_att, "red_zone_tds": rz_td, "red_zone_fgs": rz_fg,
        "red_zone_pct": round(100 * (rz_td + rz_fg) / rz_att, 1) if rz_att else None,
        "penalties": ts.get("PENALTIES", 0), "penalty_yards": ts.get("PENALTYYARDS", 0),
        "time_of_possession_s": ts.get("POSSESSIONTIME", 0),
    }


def load_team_scores(cur, games, report):
    cur.execute("SELECT id, name FROM teams")
    names = dict(cur.fetchall())
    n = 0
    for gid, _, home, away, done, g in games.values():
        if not done:
            continue
        for tid, side in ((home, "home"), (away, "away")):
            q = g[f"{side}_quarters"]
            vals = {"score": g[f"{side}_score"], "q1": q[0], "q2": q[1], "q3": q[2], "q4": q[3], "ot": g[f"{side}_ot"]}
            lines = team_line_totals(cur, gid, tid)
            box = g.get(f"{side}_team_stats")
            if box:      # Madden's team box score; completions only come from the player lines, once Madden links them
                vals.update(team_box(box))
                if lines["passing_attempts"]:
                    vals["passing_completions"] = lines["passing_completions"]
            else:
                vals.update(lines)
            cols = list(vals)
            cur.execute(f"INSERT INTO team_stats (game_id, team_name, {', '.join(cols)}) VALUES (%s, %s, "
                        f"{', '.join(['%s'] * len(cols))}) ON CONFLICT (game_id, team_name) DO UPDATE SET "
                        + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols), (gid, names[tid], *[vals[c] for c in cols]))
            n += 1
    report["team_stat_rows"] = n


def load_season_lines(cur, snapshot, season, players, report):
    year = snapshot["season"]["franchise_year"]
    n = 0
    for p in snapshot["players"]:
        who = players.get(p["pgid"])
        if who is None:
            continue
        rows = [r for r in p["season_stats"] if r["stats"].get("SEAS_YEAR") == year]
        if not rows:
            continue
        pid, team, pos, name = who
        for group, vals in group_lines(rows).items():
            if group == "general":
                table, vals = "season_stat_general", {k: vals[k] for k in ("games_played", "downs_played") if k in vals}
            else:
                table = GROUPS[group][1]
            if table == "season_stats":
                cols = list(vals)
                cur.execute(f"INSERT INTO season_stats (season, player_id, player_name, team_id, position, source, "
                            f"{', '.join(cols)}) VALUES (%s, %s, %s, %s, %s, 'CAPTURE', {', '.join(['%s'] * len(cols))}) "
                            f"ON CONFLICT (season, player_id) WHERE player_id IS NOT NULL DO UPDATE SET "
                            f"team_id = EXCLUDED.team_id, position = EXCLUDED.position, "
                            + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols),
                            (season, pid, name, team, pos, *[vals[c] for c in cols]))
            else:
                cols = list(vals)
                cur.execute(f"INSERT INTO {table} (season, player_id, team_id, position, source, {', '.join(cols)}) "
                            f"VALUES (%s, %s, %s, %s, 'CAPTURE', {', '.join(['%s'] * len(cols))}) "
                            f"ON CONFLICT (season, player_id) DO UPDATE SET team_id = EXCLUDED.team_id, "
                            f"position = EXCLUDED.position, "
                            + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols),
                            (season, pid, team, pos, *[vals[c] for c in cols]))
            n += 1
    report["season_line_rows"] = n


def load_contracts(cur, snapshot, season, players, report, capture_id=None):
    rows, differs = [], 0
    for p in snapshot["players"]:
        who = players.get(p["pgid"])
        if who is None or who[1] is None or not p.get("contract") or not p["contract"].get("length"):
            continue
        vals = contract_values(p["contract"], season)
        if vals["years"].get(season, 0) != (p.get("cap_salary") or 0) * MONEY_UNIT:
            differs += 1
        rows.append((who, vals))
    # Contracts in their first year (new signings, rookies) often disagree with Player.PLYR_CAPSALARY (0 or a
    # prorated amount); later years match it ~97% of the time. Madden's Team Salary screen shows the contract's own
    # number (checked on the PC, 2026-10-29), so that is what is stored; the disagreements are only counted.
    report["contracts_cap_hit_differs"] = differs
    team_ids_ = {who[1] for who, _ in rows}
    report["contracts_superseded"] = supersede_active(cur, season, team_ids_, [who[0] for who, _ in rows])
    for (pid, team, _, _), vals in rows:
        insert_contract(cur, player_id=pid, team_id=team, season=season, source="SCAN", capture_id=capture_id, **vals)
    report["contracts"] = len(rows)


def load_injuries(cur, snapshot, season, week, players, report):
    if week is None:
        report["injuries"] = "skipped: the save isn't in a regular-season or playoff week"
        return
    cur.execute("DELETE FROM injuries WHERE season = %s AND week = %s", (season, week))
    n = 0
    for p in snapshot["players"]:
        who = players.get(p["pgid"])
        inj = p.get("injury") or {}
        if who is None or who[1] is None or (inj.get("status") == "Uninjured" and not inj.get("injured_reserve")):
            continue
        pid, team, pos, name = who
        injury_type, duration, weeks, is_ir = injury_values(inj)
        cur.execute("INSERT INTO injuries (season, week, team_id, player_id, player_name, position, ovr, injury_type, "
                    "duration, weeks_remaining, is_ir) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (season, week, team_id, player_name) DO UPDATE SET player_id = EXCLUDED.player_id, "
                    "position = EXCLUDED.position, ovr = EXCLUDED.ovr, injury_type = EXCLUDED.injury_type, "
                    "duration = EXCLUDED.duration, weeks_remaining = EXCLUDED.weeks_remaining, is_ir = EXCLUDED.is_ir, "
                    "captured_at = now()",
                    (season, week, team, pid, name, pos, p["overall"], injury_type, duration, weeks, is_ir))
        n += 1
    report["injuries"] = n


def load_highlights(cur, games, season, report):
    cur.execute("SELECT id, abbreviation FROM teams")
    abbr = dict(cur.fetchall())
    n = 0
    for gid, week, home, away, done, g in games.values():
        if not done or not g.get("scoring_plays"):
            continue
        cur.execute("SELECT highlights FROM game_highlights WHERE game_id = %s", (gid,))
        row = cur.fetchone()
        if row and row[0] and any(h.get("source") != "SAVE" for h in row[0]):
            continue              # captured highlights have real play text: keep them
        plays = scoring_highlights(g, abbr[home], abbr[away])
        cur.execute("INSERT INTO game_highlights (game_id, season, week, highlights) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (game_id) DO UPDATE SET highlights = EXCLUDED.highlights, season = EXCLUDED.season, "
                    "week = EXCLUDED.week, created_at = now()", (gid, season, week, json.dumps(plays)))
        n += 1
    report["games_with_scoring_plays"] = n


def current_week(snapshot):
    """The save's current week in games.week numbering, or None outside the regular season and playoffs."""
    wk = game_week(snapshot["season"]["week_type"], snapshot["season"]["week_index"])
    return wk[0] if wk else None


def update_league_state(cur, season, week, report):
    """Moves league_state to the save's week: never back, and only for the league's season or the next one (a test
    load as a far-off season leaves it alone)."""
    cur.execute("SELECT current_season, current_week FROM league_state WHERE id = 1")
    stored_season, stored_week = cur.fetchone()
    if week is None:
        report["league_state"] = f"kept {stored_season} week {stored_week}: no current week in the save"
    elif season == stored_season + 1 or (season == stored_season and week > stored_week):
        cur.execute("UPDATE league_state SET current_season = %s, current_week = %s WHERE id = 1", (season, week))
        report["league_state"] = f"{stored_season} week {stored_week} -> {season} week {week}"
    else:
        report["league_state"] = f"kept {stored_season} week {stored_week} (save: {season} week {week})"


def load_snapshot(cur, snapshot, season=None, capture_id=None, capture_folder=None):
    """The whole load on an open cursor (the caller commits or rolls back). Returns the report."""
    if snapshot.get("meta", {}).get("game_year") != 27:
        raise LoadError(f"not a Madden 27 snapshot: game_year {snapshot.get('meta', {}).get('game_year')}")
    season = season or snapshot["season"]["calendar_year"]
    report = {"season": season, "franchise_year": snapshot["season"]["franchise_year"],
              "save_week": {k: snapshot["season"][k] for k in ("week_type", "week_index", "stage")}}
    teams = team_ids(cur, snapshot)
    players = load_players(cur, snapshot, season, teams, report)
    load_player_attributes(cur, snapshot, season, players, report, capture_folder=capture_folder)
    games = load_games(cur, snapshot, season, teams, report)
    load_game_lines(cur, snapshot, games, players, report)
    load_team_scores(cur, games, report)
    load_season_lines(cur, snapshot, season, players, report)
    load_contracts(cur, snapshot, season, players, report, capture_id)
    week = current_week(snapshot)
    report["current_week"] = week
    load_injuries(cur, snapshot, season, week, players, report)
    load_highlights(cur, games, season, report)
    update_league_state(cur, season, week, report)
    return report


REPORT_COUNTS = ("players", "players_created", "player_attributes", "games", "games_completed", "game_line_rows", "team_stat_rows",
                 "season_line_rows", "contracts", "injuries", "games_with_scoring_plays")


def capture_row(cur, path, season, week):
    """The run's `captures` row (mode `save`, folder = the snapshot's directory name); a run already loaded is
    refused."""
    folder = os.path.basename(os.path.dirname(os.path.abspath(path)))
    cur.execute("SELECT id, status FROM captures WHERE folder = %s", (folder,))
    row = cur.fetchone()
    if row and row[1] == "loaded":
        raise LoadError(f"{folder} is already loaded")
    if row:
        cur.execute("UPDATE captures SET mode = 'save', season = %s, week = %s, error = NULL WHERE id = %s",
                    (season, week, row[0]))
        return row[0]
    cur.execute("INSERT INTO captures (folder, mode, season, week, status, extracted_at) "
                "VALUES (%s, 'save', %s, %s, 'extracted', now()) RETURNING id", (folder, season, week))
    return cur.fetchone()[0]


def load(path, season=None, commit=False):
    with open(path) as fh:
        snapshot = json.load(fh)
    folder = os.path.basename(os.path.dirname(os.path.abspath(path)))
    with psycopg.connect(DSN) as conn:
        cur = conn.cursor()
        cid = None
        if commit:
            cid = capture_row(cur, path, season or snapshot["season"]["calendar_year"], current_week(snapshot))
        report = load_snapshot(cur, snapshot, season, capture_id=cid, capture_folder=folder)
        if commit:
            cur.execute("UPDATE captures SET status = 'loaded', loaded_at = now(), row_counts = %s WHERE id = %s",
                        (json.dumps({k: report[k] for k in REPORT_COUNTS if isinstance(report.get(k), int)}), cid))
            conn.commit()
        else:
            conn.rollback()
        report["committed"] = commit
        report["capture_id"] = cid
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("snapshot", help="JSON written by pipeline/save_reader/pc_save_reader.mjs")
    ap.add_argument("--season", type=int, help="season to load as (default: the save's calendar year)")
    ap.add_argument("--commit", action="store_true", help="write the load (default: run, report, roll back)")
    a = ap.parse_args()
    try:
        report = load(a.snapshot, a.season, a.commit)
    except LoadError as e:
        print(f"LOAD STOPPED, nothing written: {e}")
        sys.exit(1)
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
