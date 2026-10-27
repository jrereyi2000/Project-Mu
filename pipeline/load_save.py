"""
Loads a save-reader snapshot (pipeline/save_reader/pc_save_reader.mjs) into the database in one transaction
(docs/PC_MIGRATION_PLAN.md §5.1). The PC replacement for load_capture.py: players are matched on
players.madden_pgid (Madden's Player.PresentationId), so there is no name matching and no OCR check.

  python3 -m pipeline.load_save captures/<run>/save.json [--season N] [--commit]

Without --commit the load runs and reports, then rolls back. --season defaults to the save's calendar year; the
save's own franchise year picks which games and season rows belong to it (older years are history Madden kept).

Writes, for that season:
  players / player_seasons   every Signed, PracticeSquad or FreeAgent player (a pgid not yet stored gets a new
                             players row); team, position, age, OVR, experience, dev trait, archetype
  games                      regular-season and playoff games, scheduled and completed (preseason skipped)
  team_stats                 quarter and OT scores plus line totals for completed games
  player_stats, game_stat_*  per-game lines: a game the save has lines for has its stored lines replaced
                             (Madden purges old weeks, so a game with no lines in this save keeps what it has)
  season_stats, season_stat_* season totals (a player traded mid-season is summed across teams)
Not yet: contracts, injuries, scoring plays, league_state, captures rows.

Save lines are stored as line_source CAPTURED and season rows as source CAPTURE: both are real box-score data,
which is what readers treat those values as.
"""
import argparse
import json
import os
import sys

import psycopg

from pipeline.capture_matching import db_player_name
from pipeline.load_capture import team_line_totals

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


class LoadError(Exception):
    pass


# ------------------------------------------------------------------ mapping ----
FREE_AGENT_TEAM_INDEX = 32
LOADED_STATUSES = ("Signed", "PracticeSquad", "FreeAgent")
TEAM_ABBR_ALIASES = {"ARI": "AZ"}          # Madden's short name -> teams.abbreviation where they differ

# Madden 27 positions -> Project Mu's. LOLB/ROLB -> WILL/SAM is unconfirmed: check against Madden's roster
# screen on the PC.
POSITIONS = {"LE": "LEDG", "RE": "REDG", "MLB": "MIKE", "LOLB": "WILL", "ROLB": "SAM"}

# Madden 27 dev trait names (College_Impact / College_Star / College_Elite, also without the prefix) mapped by
# rank onto Project Mu's Normal / Star / Superstar / X-Factor. Unconfirmed: check the in-game labels on the PC.
DEV_TRAITS = {"Normal": "Normal", "Impact": "Star", "Star": "Superstar", "Elite": "X-Factor",
              "Superstar": "Superstar", "XFactor": "X-Factor"}

ARCHETYPE_WORDS = {"MantoMan": "Man To Man", "SmallerSpeedRusher": "Speed Rusher"}

PLAYOFF_WEEKS = {"WildcardPlayoff": (19, "WILD_CARD"), "DivisionalPlayoff": (20, "DIVISIONAL"),
                 "ConferencePlayoff": (21, "CONFERENCE"), "SuperBowl": (23, "SUPER_BOWL")}
FINISHED = ("HomeWon", "AwayWon", "Tie")


def position(madden_pos):
    return POSITIONS.get(madden_pos, madden_pos)


def dev_trait(madden_dev):
    if not madden_dev:
        return None
    return DEV_TRAITS.get(madden_dev.split("_")[-1], madden_dev)


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


# Madden's DEFTACKLES is taken as solo tackles (assists are separate) and a sack as DLINESACKS plus half a sack per
# DLINEHALFSACK. Both unconfirmed: check one game against the in-game box score on the PC.
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
            vals.update(team_line_totals(cur, gid, tid))
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


def load_snapshot(cur, snapshot, season=None):
    """The whole load on an open cursor (the caller commits or rolls back). Returns the report."""
    if snapshot.get("meta", {}).get("game_year") != 27:
        raise LoadError(f"not a Madden 27 snapshot: game_year {snapshot.get('meta', {}).get('game_year')}")
    season = season or snapshot["season"]["calendar_year"]
    report = {"season": season, "franchise_year": snapshot["season"]["franchise_year"],
              "save_week": {k: snapshot["season"][k] for k in ("week_type", "week_index", "stage")}}
    teams = team_ids(cur, snapshot)
    players = load_players(cur, snapshot, season, teams, report)
    games = load_games(cur, snapshot, season, teams, report)
    load_game_lines(cur, snapshot, games, players, report)
    load_team_scores(cur, games, report)
    load_season_lines(cur, snapshot, season, players, report)
    return report


def load(path, season=None, commit=False):
    with open(path) as fh:
        snapshot = json.load(fh)
    with psycopg.connect(DSN) as conn:
        cur = conn.cursor()
        report = load_snapshot(cur, snapshot, season)
        if commit:
            conn.commit()
        else:
            conn.rollback()
        report["committed"] = commit
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
