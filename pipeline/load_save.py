"""
Loads a save-reader snapshot (pipeline/save_reader/pc_save_reader.mjs) into the database in one transaction
(docs/done/PC_MIGRATION_PLAN.md §5.1). The PC replacement for load_capture.py: players are matched on
players.madden_pgid (Madden's Player.PresentationId), so there is no name matching and no OCR check.

  python3 -m pipeline.load_save captures/<run>/save.json [--season N] [--commit]
                               [--personality-manifest PATH --personality-review PATH]

Without --commit the load runs and reports, then rolls back. --season defaults to the save's calendar year; the
save's own franchise year picks which games and season rows belong to it (older years are history Madden kept).

Writes, for that season:
  players / player_seasons   every Signed, PracticeSquad or FreeAgent player (a pgid not yet stored gets a new
                             players row); team, position, age, OVR, experience, dev trait, archetype
  player_attributes          each player's 54 native ratings (Speed, Acceleration, etc.) as JSONB, only when they
                             changed: a season baseline row, then a row per in-season change (none expected)
  games                      regular-season and playoff games, scheduled and completed (preseason skipped)
  team_stats                 quarter and OT scores plus Madden's team box score (SeasonGame team stat cache:
                             yards, first downs, 3rd/4th downs, turnovers, red zone, penalties, possession) for
                             completed games; completions from the player lines once Madden links them (a save
                             without a box score falls back to line totals)
  player_stats, game_stat_*  per-game lines: a game the save has lines for has its stored lines replaced
                             (Madden purges old weeks, so a game with no lines in this save keeps what it has)
  season_stats, season_stat_* season totals (a player traded mid-season is summed across teams)
  player_contracts (+ years)  every rostered player's contract: the season's ACTIVE rows are superseded first
  team_cap_seasons           reader_version >= 5: each real team's Team Salary strip from the save's cap fields (checked
                             against the Panthers' Team Salary screen in CAREER-WEEK1, 2026-11-02; team_cap_values)
  injuries                   the save's injured players for the league's current week (that week's rows replaced)
  game_highlights            scoring plays from SeasonGame.ScoringSummaries (Madden keeps only recent weeks);
                             a game whose highlights came from a capture keeps them
  team_seasons               reader_version >= 5: each real team's Madden team ratings (Team.TEAM_RATINGOVR / OFF /
                             DEF) as team_ovr / off_ovr / def_ovr (the row is created when the season has none; wins,
                             losses and the OCR-only rank columns are left alone)
  draft_prospects            reader_version >= 5: the coming class (season + 1) from the save's prospects (status Draft,
                             in DraftPlayer.InitialDraftRank order: projected pick, round 1-7 then UDFA, position,
                             archetype, age, height, weight, the save's OVR and the college through
                             pipeline/colleges.py (Madden's college list), NULL when unknown), only when that class has
                             no board yet: a scanned or already loaded board is kept, so its ids, mock drafts and
                             scouting links never move
  scout_prospect_ids /       every load (once 2027_01_21_scouting_reports.sql is applied): the coming class's permanent
  scout_prospect_ratings     prospect IDs (engines/prospect_ids.py; report `prospect_ids`: allocated / renamed /
                             collisions) and each Draft-status player's native ratings by prospect_uid (hidden;
                             pipeline/prospect_ratings.py)
  persona_lifecycle_*        with --personality-manifest/--personality-review (docs/in_progress/
                             PERSONALITY_DRAFT_INTEGRATION_PLAN.md §2.4, personality/lifecycle_store.py): the reviewed
                             lifecycle manifest previewed from this exact snapshot is applied in the same transaction,
                             then verified aliases are bound (db-player by pgid, the board rows written by this load as
                             draft:<class>:<capture>, unique scout:<class> keys) and reviewed import handoffs. Without
                             them the load observes only: report `personality` counts the people still unbound
                             (`pending`); nothing personality-related is written
  depth_charts               reader_version >= 4: every real team's Madden depth chart (35 positions x 6, best
                             first) as source MADDEN for the save's current week (pgid -> player id through the
                             players loaded; empty slots and players not loaded are skipped)
  league_state               current week from the save (never moves back; only for the league's season or the
                             next one) and, with --commit, a `captures` row (mode `save`) for the run

The report also carries, for reader_version >= 4, `league_settings` (the four progression / regression slider records'
values and the dev-trait regression flag, null while unknown) and `settings_warnings` (every slider value that isn't 0:
Project Mu runs progression itself and expects all four sliders at 0 for every position and age, LS included).

Save lines are stored as line_source CAPTURED, season rows as source CAPTURE and contracts as source SCAN: all are
real data read from Madden, which is what readers treat those values as.
"""
import argparse
import json
import os
import sys

import psycopg

from pipeline.capture_matching import db_player_name
from pipeline.colleges import college_name
from pipeline.contracts import insert_contract, supersede_active, upsert_team_cap
from pipeline import draft_board
from pipeline.draft_board import write_board
from pipeline.ingest_prospects import Prospect
from pipeline.load_capture import team_line_totals
from pipeline.prospect_ratings import store_prospect_ratings
from engines import prospect_ids
from engines import depth_chart_engine

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
            # A roster row clears a captured retirement (SEASON_CONTINUITY_PLAN D8): he is playing again.
            cur.execute("UPDATE players SET height = %s, weight = %s, player_number = %s, retired_season = NULL "
                        "WHERE id = %s", (height_text(p["height_in"]), p["weight_lbs"], p["jersey"], pid))
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


def retirement_season(snapshot, season):
    """The season a `Retired` player retired after. Madden marks retirement at the offseason start while the save
    still reports the season just finished, so that snapshot's own season is the retiring season; a player still
    `Retired` once the new preseason has started retired after the previous one."""
    wt = (snapshot.get("season") or {}).get("week_type") or (snapshot.get("season_info") or {}).get("week_type")
    return season - 1 if wt == "PreSeason" else season


def load_retirements(cur, snapshot, season, teams, report, capture_id=None):
    """Madden's native retirements from the save (SEASON_CONTINUITY_PLAN phase 2b): every Player row whose
    `ContractStatus` is `Retired` gets a `player_retirements` row for the season he retired after and
    `players.retired_season` set, so `engines/season_ledger.py` can label the boundary RETIRED. Idempotent: one row
    per (season, player); a later roster scan or signing clears `players.retired_season` (D8). Returns the count."""
    rows = [p for p in snapshot.get("players") or [] if p.get("status") == "Retired"]
    if not rows:
        report["retirements"] = 0
        return 0
    cur.execute("SELECT madden_pgid, id FROM players WHERE madden_pgid IS NOT NULL")
    by_pgid = dict(cur.fetchall())
    retired_after = retirement_season(snapshot, season)
    # A player Madden keeps `Retired` past the purge (Kelce, Rodgers in the G4 run) is still on the list every load;
    # without this he got a fresh row for each new season and the ledger labelled him RETIRED year after year. A
    # restored player has `players.retired_season` cleared (D8), so a genuine second retirement still counts.
    cur.execute("SELECT id FROM players WHERE retired_season IS NOT NULL")
    already_retired = {r[0] for r in cur.fetchall()}
    count = 0
    for p in rows:
        pid = by_pgid.get(p["pgid"])
        full = f"{p['first_name']} {p['last_name']}".strip()
        if pid is None:
            cur.execute("INSERT INTO players (player_name, full_name, height, weight, player_number, origin, "
                        "madden_pgid) VALUES (%s, %s, %s, %s, %s, 'SAVE', %s) RETURNING id",
                        (db_player_name(p["first_name"], p["last_name"]), full, height_text(p["height_in"]),
                         p["weight_lbs"], p["jersey"], p["pgid"]))
            pid = cur.fetchone()[0]
            by_pgid[p["pgid"]] = pid
        if pid in already_retired:
            continue
        cur.execute("INSERT INTO player_retirements (season, player_id, player_name, position, team_id, capture_id) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                    (retired_after, pid, db_player_name(p["first_name"], p["last_name"]), position(p["position"]),
                     teams.get(p.get("team_index")), capture_id))
        cur.execute("UPDATE players SET retired_season = %s "
                    "WHERE id = %s AND (retired_season IS NULL OR retired_season = %s)",
                    (retired_after, pid, retired_after))
        count += 1
    report["retirements"] = count
    return count


def load_player_attributes(cur, snapshot, season, players, report, capture_folder=None):
    """Stores observed native ratings in player_attributes, but only when they changed.

    Observations can include form or Madden drift. They never update the independent
    progression_player_state permanent base; only a verified Mu write receipt can do that.

    Project Mu owns progression, so ratings should move once a year (the offseason progression write) plus
    for new players. A row is written when the player has no row for this season yet (flag native_save,
    the season baseline) or their ratings differ from their newest stored row (flag in_season_change);
    otherwise nothing is written. In-season changes are counted in the report: with Madden's XP sliders
    at 0 there should be none (PROGRESSION_PLAN.md phase A check 1). Comparison is against rows from other
    capture folders, so reloading a capture already stored rewrites its own row in place (same flag)."""
    folder = capture_folder or f"save_{season}"
    candidates = {}
    for p in snapshot["players"]:
        who = players.get(p["pgid"])
        ratings = p.get("ratings")
        if who is None or not ratings:
            continue
        candidates[who[0]] = (who, p, ratings)

    latest = {}
    if candidates:
        cur.execute("""
            SELECT DISTINCT ON (pa.player_id) pa.player_id, pa.season, pa.ratings FROM player_attributes pa
            LEFT JOIN player_attributes own ON own.player_id = pa.player_id AND own.capture_folder = %s
            WHERE pa.player_id = ANY(%s) AND pa.season <= %s AND pa.capture_folder <> %s
              AND (own.id IS NULL OR pa.id < own.id)      -- a reloaded capture compares with what came before it
            ORDER BY pa.player_id, pa.season DESC, pa.id DESC
        """, (folder, list(candidates), season, folder))
        latest = {pid: (s, r) for pid, s, r in cur.fetchall()}

    rows, unchanged, changed = [], 0, 0
    for pid, (who, p, ratings) in candidates.items():
        _, team, pos, _ = who
        prev = latest.get(pid)          # newest row from another capture: (season, ratings)
        if prev is None or prev[0] < season:
            flag = "native_save"
        elif prev[1] == ratings:
            unchanged += 1
            continue
        else:
            flag = "in_season_change"
            changed += 1
        rows.append((
            pid, season, team, folder, pos, p.get("age"),
            p.get("overall"), p.get("overall"), "ok", len(ratings),
            json.dumps(ratings), flag
        ))
    report["player_attributes"] = len(rows)
    report["player_attributes_unchanged"] = unchanged
    report["player_attributes_in_season_changes"] = changed
    if not rows:
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


DRAFTED_PICKS = 224                # 7 rounds x 32; later board slots are UDFA


def draft_board_prospects(snapshot):
    """The save's coming class (reader_version >= 5) as draft board Prospects in Madden's board order
    (DraftPlayer.InitialDraftRank, then OVR). Empty for older snapshots."""
    players = {p["pgid"]: p for p in snapshot["players"] if p["status"] == "Draft"}
    rows = [(d, players[d["pgid"]]) for d in snapshot.get("draft_class") or [] if d["pgid"] in players]
    rows.sort(key=lambda dp: (dp[0]["initial_draft_rank"] is None, dp[0]["initial_draft_rank"] or 0, -(dp[1]["overall"] or 0)))
    out = []
    for pick, (_, p) in enumerate(rows, start=1):
        drafted = pick <= DRAFTED_PICKS
        out.append(Prospect(projected_pick=pick, full_name=f"{p['first_name']} {p['last_name']}".strip(),
                            position=position(p["position"]), college=college_name(p.get("college")),
                            height=height_text(p["height_in"]),
                            weight=p["weight_lbs"], age=p["age"], overall_rating=p["overall"],
                            archetype=archetype(p["archetype"]),
                            projected_round=str((pick - 1) // 32 + 1) if drafted else "UDFA",
                            round_pick=(pick - 1) % 32 + 1 if drafted else None, source_id=str(p["pgid"])))
    return out


def load_draft_class(cur, snapshot, season, report, capture_folder=None):
    """The coming class (season + 1) as its draft_prospects board, only when it has none: on PC there is no Draft
    Board scan, and a board already there (scanned, or loaded from an earlier save) is kept. Returns the Prospects
    written (each carrying its pgid as source_id, for the personality source mapping), or None when kept/empty."""
    prospects = draft_board_prospects(snapshot)
    if not prospects:
        return None
    draft_season = season + 1
    cur.execute("SELECT count(*) FROM draft_prospects WHERE draft_season = %s", (draft_season,))
    have = cur.fetchone()[0]
    if have:
        report["draft_class"] = f"kept the {draft_season} board ({have} prospects)"
        return None
    report["draft_prospects"] = write_board(cur, draft_season, prospects, source_capture=capture_folder or "save")
    report["draft_class"] = f"{draft_season} board loaded from the save"
    return prospects


def _pgid_bindings(cur, universe_id, namespace):
    """{pgid text: source_person_id} for the universe's open save-pgid bindings."""
    cur.execute("SELECT external_id, source_person_id FROM persona_lifecycle_bindings WHERE universe_id = %s "
                "AND role = 'player' AND namespace = %s AND valid_to IS NULL", (universe_id, namespace))
    return {ext: str(spid) for ext, spid in cur.fetchall()}


def load_personality(cur, snapshot, season, players, report, context=None, board=None, capture_folder=None):
    """Person lifecycle step (docs/done/PERSONALITY_DRAFT_INTEGRATION_PLAN.md §2.4), inside the load's
    transaction. context None = observe: reads only and reports report["personality"]["pending"] (loaded roster
    and class pgids with no lifecycle binding). context {"manifest", "review", "handoffs"?} = enforce: applies the
    reviewed manifest (it must have been previewed from this exact snapshot), binds db-player for every loaded
    player whose pgid is bound (same pgid lifetime: the players row was matched on madden_pgid), board IDs for a
    board written by this load (draft:<class>:<capture>, lifecycle-only), unique scout:<class> keys, and each
    handoff validate_handoff verifies. Any lifecycle conflict raises LoadError, which aborts the whole load. A name
    match never binds; a kept board is not mapped."""
    from personality import lifecycle as L, lifecycle_store as LS
    info = {"mode": "enforce" if context else "observe"}
    report["personality"] = info
    if not LS.available(cur):
        if context:
            raise LoadError("personality manifest given, but 2027_01_24_personality_lifecycle.sql isn't applied")
        info["status"] = "not installed"
        return info
    league = (snapshot.get("franchise") or {}).get("league_id")
    try:
        if context:
            manifest = context["manifest"]
            if manifest.get("input_hashes", {}).get("snapshot") != L.snapshot_sha256(snapshot):
                raise LoadError("personality manifest was previewed from a different snapshot: preview and review again")
            info["batch"] = LS.apply_lifecycle(cur, manifest, expected_hash=manifest.get("manifest_sha256"),
                                               review_receipt=context["review"])
            universe = manifest["universe"]
        else:
            universe = LS.universe_for_league(cur, league) if league is not None else None
        if universe is None:
            info["status"] = "no universe registered for this league"
            return info
        uid = universe["universe_id"]
        info["universe_id"] = uid
        bound = _pgid_bindings(cur, uid, L.pgid_namespace(universe))
        draft_season = season + 1
        class_pgids = [p["pgid"] for p in snapshot.get("players") or [] if p.get("status") == "Draft"
                       and p.get("pgid") is not None]
        info["pending"] = sum(1 for pgid in list(players) + class_pgids if str(pgid) not in bound)
        if not context:
            return info
        cutoff = context["manifest"]["cutoff"]
        stamp = {"batch_id": info["batch"]["batch_id"], "evidence": "same_pgid_lifetime"}
        counts = {"db_player": 0, "board": 0, "scout": 0}
        for pgid, (pid, *_rest) in sorted(players.items()):
            spid = bound.get(str(pgid))
            if spid:
                counts["db_player"] += len(LS.bind_person(cur, uid, "player", spid,
                                                          [{"namespace": "db-player", "external_id": str(pid)}],
                                                          cutoff=cutoff, receipt=stamp))
        if board:
            ids = draft_board.board_ids(cur, draft_season)
            capture = capture_folder or L.snapshot_sha256(snapshot)[:12]
            for p in board:
                spid, row = bound.get(p.source_id or ""), ids.get(p.projected_pick)
                if spid and row is not None:
                    counts["board"] += len(LS.bind_person(
                        cur, uid, "player", spid, [{"namespace": f"draft:{draft_season}:{capture}",
                                                     "external_id": str(row)}], cutoff=cutoff, receipt=stamp))
        else:
            info["board_aliases"] = "board kept or absent: board rows not mapped"
        for key, (prospect_uid, prospect_key, status) in sorted(prospect_ids.class_ids(cur, draft_season).items(),
                                                                key=lambda kv: str(kv[0])):
            spid = bound.get(str(key)) if isinstance(key, int) else None
            if spid and status == prospect_ids.OK:
                counts["scout"] += len(LS.bind_person(cur, uid, "player", spid,
                                                      [{"namespace": f"scout:{draft_season}", "external_id": prospect_key}],
                                                      cutoff=cutoff, receipt=stamp))
        handoffs = {"verified": 0, "pending": 0}
        if context.get("handoffs"):
            existing = LS.export_state(cur, uid)["bindings"]
            for candidate in context["handoffs"]:
                result = L.validate_handoff(candidate, existing)
                if result["status"] == "conflict":
                    raise LoadError(f"personality handoff conflict: {result['reason']}")
                if result["status"] == "verified":
                    LS.bind_person(cur, uid, candidate.get("role", "player"), result["source_person_id"],
                                   [candidate["to"]], cutoff=cutoff,
                                   receipt={"batch_id": info["batch"]["batch_id"],
                                            "evidence": candidate["evidence"], "receipt": candidate.get("receipt")})
                    handoffs["verified"] += 1
                else:
                    handoffs["pending"] += 1
        info.update(bound=counts, handoffs=handoffs)
        return info
    except L.LifecycleError as e:
        raise LoadError(f"personality: {e}")


CAP_ROOM_OFFSET = 160_000           # Team.SalCapCapRoom = cap space + 160000 (every team, CAREER-WEEK1)
ROOKIE_SCALE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "data", "scouting", "madden27_rookie_scale.json")
_ROOKIE_SCALE = None


def rookie_scale():
    """data/scouting/madden27_rookie_scale.json: {first_year_cap_hit: {slot: dollars}, reserve_factor,
    reference_league_cap}."""
    global _ROOKIE_SCALE
    if _ROOKIE_SCALE is None:
        with open(ROOKIE_SCALE_PATH) as fh:
            _ROOKIE_SCALE = json.load(fh)
    return _ROOKIE_SCALE


def rookie_reserve_estimate(slots, league_cap):
    """Madden's in-season estimate of next season's rookie reserve for a team holding these overall slots (1-224) in
    the coming draft: the slots' first-year cap hits x 1.016, scaled by this season's league cap against the 2026 cap
    the scale reproduces (CAREER-WEEK1, CAR, slots 19/51/83/114/115/179/194/211: $14.2M, its Team Salary screen),
    rounded to $10,000. A slot past 224 counts as 224."""
    sc = rookie_scale()
    hits = sum(sc["first_year_cap_hit"][str(min(max(int(s), 1), 224))] for s in slots)
    est = hits * sc["reserve_factor"] * league_cap / sc["reference_league_cap"]
    return int(round(est / MONEY_UNIT)) * MONEY_UNIT


def cap_season(snapshot: dict, season: int) -> int:
    """The league year the save's cap sheet belongs to: season + 1 during OffSeason, else season."""
    week_type = (snapshot.get("season_info") or {}).get("week_type") or (snapshot.get("season") or {}).get("week_type")
    return season + 1 if week_type == "OffSeason" else season


def team_cap_dollars(league_cap_units: int, team_cap: dict) -> dict:
    """Pure function converting $10k-unit snapshot fields to whole-dollar team_cap_seasons columns."""
    cap = league_cap_units * MONEY_UNIT
    rollover = int(team_cap["rollover"]) * MONEY_UNIT
    adjusted_cap = (league_cap_units + int(team_cap["rollover"]) + int(team_cap.get("cap_credits") or 0)) * MONEY_UNIT
    cap_space = int(team_cap["cap_room"]) * MONEY_UNIT
    cap_penalty = int(team_cap["cap_penalties"]) * MONEY_UNIT
    offers = int(team_cap.get("offers_reserve") or 0) * MONEY_UNIT
    total_spend = adjusted_cap - cap_space
    salaries = max(0, total_spend - cap_penalty - offers)
    out = {
        "cap": cap,
        "rollover": rollover,
        "adjusted_cap": adjusted_cap,
        "cap_space": cap_space,
        "salaries": salaries,
        "cap_penalty": cap_penalty,
        "total_spend": total_spend,
    }
    if int(team_cap.get("rookie_reserve") or 0) > 0:
        out["rookie_reserve_dollars"] = int(team_cap["rookie_reserve"]) * MONEY_UNIT
    return out


def team_cap_values(cap, slots=None):
    """A snapshot team's raw cap fields -> upsert_team_cap's values (whole dollars), as the Team Salary screen shows
    them (CAR, CAREER-WEEK1: cap $301M, 25 rollover $13.8M, adjusted cap $315M, cap space $11.7M, 26 salaries $279M,
    26 cap penalty $24.6M, 26 total spend $303M): salaries = SalCapRosterReserve, cap penalty = ThisYearCapPenalties,
    rollover = RolloverCap, cap space = SalCapCapRoom - 160000; adjusted cap = space + salaries + penalty and cap =
    adjusted cap - rollover - ThisYearCapCredits (credits add to a team's cap like rollover: the league cap comes out
    the same for all 32 teams, $301.2M in CAREER-WEEK1 and $331.3M in CAREER-GIANTS-AUTOSAVE's 2027, where five teams
    have credits). Next season's rookie reserve: Madden's own SalCapRookieReserve once it sets one (the offseason, from
    the Super Bowl to the draft: SalCapRookieCount > 0), else, in season, rookie_reserve_estimate over `slots` (the
    team's overall slots in the coming draft), else NULL."""
    salaries, penalty, rollover = cap["SalCapRosterReserve"], cap["ThisYearCapPenalties"], cap["RolloverCap"]
    space = cap["SalCapCapRoom"] - CAP_ROOM_OFFSET
    adjusted = space + salaries + penalty
    out = {k: v * MONEY_UNIT for k, v in {
        "cap": adjusted - rollover - cap.get("ThisYearCapCredits", 0), "rollover": rollover, "adjusted_cap": adjusted,
        "cap_space": space,
        "salaries": salaries, "cap_penalty": penalty, "total_spend": salaries + penalty}.items()}
    if cap.get("SalCapRookieCount"):
        out["next_rookie_reserve"] = cap.get("SalCapRookieReserve", 0) * MONEY_UNIT
    elif slots:
        out["next_rookie_reserve"] = rookie_reserve_estimate(slots, out["cap"])
    return out


def load_team_caps(cur, snapshot: dict, season: int, teams: dict, report: dict, capture_id=None) -> None:
    """Upserts 32 team_cap_seasons rows for cap_season(snapshot, season) on reader_version >= 6 snapshots.
    In season, the coming draft's slots (draft_picks with year_offset 0, pick_number + 1) feed the in-season
    rookie reserve estimate. In the offseason, Madden's own SalCapRookieReserve is saved."""
    salary_cap = snapshot.get("salary_cap")
    if not salary_cap or salary_cap.get("team_salary_cap") is None:
        return
    league_cap_units = int(salary_cap["team_salary_cap"])
    target_cap_season = cap_season(snapshot, season)
    is_offseason = (
        (snapshot.get("season_info") or {}).get("week_type") == "OffSeason"
        or (snapshot.get("season") or {}).get("week_type") == "OffSeason"
    )
    slots = {}
    if not is_offseason:
        for p in snapshot.get("draft_picks") or []:
            if p.get("year_offset") == 0 and p.get("current_team_index") is not None:
                slots.setdefault(p["current_team_index"], []).append(p["pick_number"] + 1)

    count = 0
    for team in snapshot.get("teams", []):
        tidx = team.get("team_index")
        tid = teams.get(tidx)
        cap_block = team.get("cap")
        if tid is None or not cap_block:
            continue
        vals = team_cap_dollars(league_cap_units, cap_block)
        if not is_offseason and slots.get(tidx):
            vals["next_rookie_reserve"] = rookie_reserve_estimate(slots[tidx], vals["cap"])
        upsert_team_cap(cur, tid, target_cap_season, vals, capture_id)
        if is_offseason and "rookie_reserve_dollars" in vals:
            cur.execute(
                "UPDATE team_cap_seasons SET next_rookie_reserve = %s WHERE team_id = %s AND season = %s",
                (vals["rookie_reserve_dollars"], tid, season),
            )
        count += 1
    if count:
        report["team_caps"] = count


load_team_cap = load_team_caps


TEAM_RATING_COLUMNS = {"ovr": "team_ovr", "off": "off_ovr", "def": "def_ovr"}


def load_team_ratings(cur, snapshot, season, teams, report):
    """Madden's team ratings (reader_version >= 5, snapshot teams[].ratings) into team_seasons for the season. A PC
    franchise never has the PS5 Team Overalls OCR capture, so this is where team_ovr / off_ovr / def_ovr come from;
    older snapshots add nothing."""
    n = 0
    for t in snapshot["teams"]:
        tid, ratings = teams.get(t["team_index"]), t.get("ratings")
        if tid is None or not ratings:
            continue
        vals = {col: ratings.get(key) for key, col in TEAM_RATING_COLUMNS.items()}
        cols = list(vals)
        cur.execute(f"INSERT INTO team_seasons (team_id, season, {', '.join(cols)}) VALUES (%s, %s, "
                    f"{', '.join(['%s'] * len(cols))}) ON CONFLICT (team_id, season) DO UPDATE SET "
                    + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols), (tid, season, *[vals[c] for c in cols]))
        n += 1
    if n:
        report["team_ratings"] = n


def load_depth_charts(cur, snapshot, season, week, teams, players, report):
    """The save's depth charts (reader_version >= 4) as depth_charts rows, source MADDEN, for (season, week). A team's
    MADDEN rows for that week are replaced (depth_chart_engine.store). A slot holding a pgid that wasn't loaded (a
    draft prospect, a deleted player) is skipped like an empty slot; positions outside POSITION_SLOTS are ignored."""
    charts = snapshot.get("depth_charts")
    if charts is None:
        return
    if week is None:
        report["depth_charts"] = "skipped: the save isn't in a regular-season or playoff week"
        return
    n_teams = n_rows = unknown = 0
    for entry in charts:
        team = teams.get(entry.get("team_index"))
        if team is None:
            continue
        chart = {}
        for slot, pgids in (entry.get("slots") or {}).items():
            if slot not in depth_chart_engine.POSITION_SLOTS:
                continue
            ids = []
            for pgid in list(pgids or [])[:depth_chart_engine.DEPTH]:
                who = players.get(pgid) if pgid is not None else None
                if pgid is not None and who is None:
                    unknown += 1
                ids.append(who[0] if who else None)
            chart[slot] = ids
            n_rows += sum(1 for i in ids if i is not None)
        depth_chart_engine.store(cur, team, season, week, chart, "MADDEN")
        n_teams += 1
    report["depth_charts"] = n_teams
    report["depth_chart_rows"] = n_rows
    report["depth_chart_unknown_players"] = unknown


SLIDER_KEYS = ("progression_by_age", "progression_by_position", "regression_by_age", "regression_by_position")


def slider_values(snapshot):
    """{slider key: {field: value} or None when the record wasn't found}, or None for a snapshot without
    league_settings (reader_version < 4)."""
    settings = snapshot.get("league_settings")
    if settings is None:
        return None
    sliders = settings.get("sliders") or {}
    return {k: (dict(sliders[k].get("values") or {}) if sliders.get(k) else None) for k in SLIDER_KEYS}


def nonzero_sliders(snapshot):
    """[(slider key, field, value)] for every slider value that isn't 0, and (key, None, None) for a slider record
    the reader didn't find. Empty for a snapshot without league_settings."""
    values = slider_values(snapshot)
    out = []
    for key in SLIDER_KEYS if values is not None else ():
        vals = values[key]
        if vals is None:
            out.append((key, None, None))
            continue
        out.extend((key, f, v) for f, v in vals.items() if isinstance(v, (int, float)) and not isinstance(v, bool) and v != 0)
    return out


def league_settings_report(snapshot, report):
    """Adds `league_settings` (slider values, dev-trait regression flag) and `settings_warnings` to the report for a
    reader_version >= 4 snapshot; older snapshots add nothing."""
    values = slider_values(snapshot)
    if values is None:
        return
    settings = snapshot["league_settings"]
    report["league_settings"] = {"sliders": values,
                                 "dev_trait_regression_field": settings.get("dev_trait_regression_field"),
                                 "dev_trait_regression_enabled": settings.get("dev_trait_regression_enabled")}
    report["settings_warnings"] = [
        f"{key}: slider record not found in the save" if field is None else
        f"{key}.{field} = {value} (Project Mu expects 0)" for key, field, value in nonzero_sliders(snapshot)]


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


def load_snapshot(cur, snapshot, season=None, capture_id=None, capture_folder=None, personality_context=None):
    """The whole load on an open cursor (the caller commits or rolls back). Returns the report. personality_context
    ({"manifest", "review", "handoffs"?}) applies a reviewed lifecycle manifest in the same transaction; without it
    the personality step only observes (load_personality)."""
    if snapshot.get("meta", {}).get("game_year") != 27:
        raise LoadError(f"not a Madden 27 snapshot: game_year {snapshot.get('meta', {}).get('game_year')}")
    season = season or snapshot["season"]["calendar_year"]
    report = {"season": season, "franchise_year": snapshot["season"]["franchise_year"],
              "save_week": {k: snapshot["season"][k] for k in ("week_type", "week_index", "stage")}}
    teams = team_ids(cur, snapshot)
    load_team_ratings(cur, snapshot, season, teams, report)
    players = load_players(cur, snapshot, season, teams, report)
    load_retirements(cur, snapshot, season, teams, report, capture_id)
    load_player_attributes(cur, snapshot, season, players, report, capture_folder=capture_folder)
    games = load_games(cur, snapshot, season, teams, report)
    load_game_lines(cur, snapshot, games, players, report)
    load_team_scores(cur, games, report)
    load_season_lines(cur, snapshot, season, players, report)
    load_contracts(cur, snapshot, season, players, report, capture_id)
    load_team_caps(cur, snapshot, season, teams, report, capture_id)
    week = current_week(snapshot)
    report["current_week"] = week
    load_injuries(cur, snapshot, season, week, players, report)
    load_highlights(cur, games, season, report)
    load_depth_charts(cur, snapshot, season, week, teams, players, report)
    board = load_draft_class(cur, snapshot, season, report, capture_folder)
    report["prospect_ratings"] = store_prospect_ratings(cur, snapshot, season + 1, capture_folder or f"save_{season}",
                                                        report)
    load_personality(cur, snapshot, season, players, report, personality_context, board, capture_folder)
    league_settings_report(snapshot, report)
    update_league_state(cur, season, week, report)
    return report


REPORT_COUNTS = ("players", "players_created", "retirements", "player_attributes", "player_attributes_unchanged",
                 "player_attributes_in_season_changes", "games", "games_completed", "game_line_rows", "team_stat_rows",
                 "season_line_rows", "contracts", "injuries", "games_with_scoring_plays", "depth_charts",
                 "depth_chart_rows", "team_ratings", "draft_prospects", "team_caps", "prospect_ratings")


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


def personality_context(manifest_path=None, review_path=None):
    """--personality-manifest / --personality-review as a load_personality context (both or neither)."""
    if not manifest_path and not review_path:
        return None
    if not (manifest_path and review_path):
        raise LoadError("--personality-manifest and --personality-review go together")
    with open(manifest_path) as fh:
        manifest = json.load(fh)
    with open(review_path) as fh:
        review = json.load(fh)
    return {"manifest": manifest, "review": review, "handoffs": manifest.get("handoffs") or []}


def load(path, season=None, commit=False, personality=None):
    with open(path) as fh:
        snapshot = json.load(fh)
    folder = os.path.basename(os.path.dirname(os.path.abspath(path)))
    with psycopg.connect(DSN) as conn:
        cur = conn.cursor()
        cid = None
        if commit:
            cid = capture_row(cur, path, season or snapshot["season"]["calendar_year"], current_week(snapshot))
        report = load_snapshot(cur, snapshot, season, capture_id=cid, capture_folder=folder,
                               personality_context=personality)
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
    ap.add_argument("--personality-manifest", metavar="PATH",
                    help="reviewed lifecycle manifest previewed from this snapshot (tools/personality_inventory.py)")
    ap.add_argument("--personality-review", metavar="PATH", help="JR's review receipt naming the manifest's hash")
    a = ap.parse_args()
    try:
        report = load(a.snapshot, a.season, a.commit,
                      personality_context(a.personality_manifest, a.personality_review))
    except LoadError as e:
        print(f"LOAD STOPPED, nothing written: {e}")
        sys.exit(1)
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
