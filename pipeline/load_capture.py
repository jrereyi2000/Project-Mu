"""
Load an extracted capture folder into the database (docs/PIPELINE_MIGRATION.md, phase 3).

  python3 -m pipeline.load_capture captures/<run> [--dry-run]

The mode is read from the folder: roster/ (roster), season_schedule/ (season schedule), games/
(games, recovery only), else stats/ + team_stats/ + schedule/ (weekly). One transaction per run:
any failed check writes nothing, records the run as failed in `captures` and exits non-zero.
--dry-run runs every check and prints what would change, then rolls back.
Uses DATABASE_URL (default: the local madden database); point it at the restored test copy
(dbname=madden_p3test) until a mode has passed there.
"""
import argparse, csv, json, os, sys
from datetime import datetime

import psycopg

from pipeline.capture_matching import db_player_name, dec, full_key, match_roster, name_case, name_key, num

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


class LoadError(Exception):
    """A check failed: nothing is written."""


def _header(h):
    """Column name without Madden's sort mark: the sorted column reads '▼YARDS' (or '*YARDS',
    'Y YARDS' through OCR) on the page Gemini read; without this a sorted column's values went
    missing (receiving yards, Oct 14)."""
    h = (h or "").strip()
    return h.lstrip("▼▲*").strip() if h[:1] in "▼▲*" else h


def read_csv(path):
    with open(path, newline="") as fh:
        return [{_header(k): v for k, v in row.items()} for row in csv.DictReader(fh)]


def detect_mode(folder):
    if os.path.exists(os.path.join(folder, "roster_cards.csv")):
        return "roster"
    if os.path.exists(os.path.join(folder, "season_schedule.csv")):
        return "season_schedule"
    if os.path.exists(os.path.join(folder, "game_scores.csv")):
        return "games"
    if os.path.exists(os.path.join(folder, "stats_passing.csv")):
        return "weekly"
    raise LoadError(f"{folder}: no extracted CSVs found (run extract.py first)")


# ------------------------------------------------------------------ roster ----
ROSTER_TEAMS_MIN, ROSTER_TEAMS_MAX = 45, 56     # players per team seen on Oct 13 (52-56)


def load_roster(cur, folder, season, report):
    """roster_cards.csv (+ roster_list.csv for the list OVR) -> players, player_seasons."""
    cards = read_csv(os.path.join(folder, "roster_cards.csv"))
    lists = read_csv(os.path.join(folder, "roster_list.csv"))
    cur.execute("SELECT id, upper(name) FROM teams")
    team_ids = {n: i for i, n in cur.fetchall()}

    # list rows by (team, list name key, position); a repeated key keeps its rows in order
    by_list = {}
    for r in lists:
        by_list.setdefault((r["TEAM"], name_key(r["NAME"]), r["POS"]), []).append(r)

    problems, per_team = [], {}
    parsed = []
    for c in cards:
        team = c["team"]
        if team not in team_ids:
            problems.append(f"unknown team {team!r}")
            continue
        per_team[team] = per_team.get(team, 0) + 1
        first, last = name_case(c["first"].strip()), name_case(c["last"].strip())
        if not last:
            problems.append(f"{team} row {c['row']}: card has no last name ({c['card_png']})")
            continue
        try:
            number, age, weight, exp = num(c["number"]), num(c["age"]), num(c["weight"]), num(c["experience"])
        except ValueError as e:
            problems.append(f"{team} row {c['row']} {first} {last}: {e}")
            continue
        pos = c["position"].strip()
        key = name_key(f"{first[:1]}.{last}") if first else None
        rows = by_list.get((team, key, pos), []) if key and pos else []
        if not rows and key:                 # card position unread: the initial + last name on the team
            rows = [r for (t, k, p), rs in by_list.items() for r in rs if t == team and k == key]
        if not rows:                         # first name unread too: the last name on the team
            rows = [r for (t, k, p), rs in by_list.items() for r in rs
                    if t == team and k[1:] == full_key(last) and (not pos or p == pos)]
        if len(rows) != 1:
            problems.append(f"{team} row {c['row']} {first} {last} {pos}: {len(rows)} list rows match")
            continue
        lr = rows[0]
        pos = pos or lr["POS"]
        try:
            ovr = num(lr["OVR"])
        except ValueError as e:
            problems.append(f"{team} {lr['NAME']}: OVR {e}")
            continue
        if not first:
            problems.append(f"{team} row {c['row']} {last}: card has no first name ({c['card_png']})")
            continue
        parsed.append(dict(team=team, team_id=team_ids[team], first=first, last=last, number=number, position=pos,
                           age=age, height=c["height"].strip() or None, weight=weight, experience=exp,
                           dev=c["dev"] or None, archetype=c["archetype"] or None, role=c["role"] or None, ovr=ovr,
                           full_key=full_key(f"{first} {last}"), name_key=name_key(f"{first[:1]}.{last}")))
    for team, n in sorted(per_team.items()):
        if not ROSTER_TEAMS_MIN <= n <= ROSTER_TEAMS_MAX:
            problems.append(f"{team}: {n} players (expected {ROSTER_TEAMS_MIN}-{ROSTER_TEAMS_MAX})")
    if len(per_team) != 32:
        problems.append(f"{len(per_team)} teams in the capture, not 32")
    if problems:
        raise LoadError("roster checks failed:\n  " + "\n  ".join(problems))

    # existing players and their roster entry for the season
    cur.execute("""SELECT p.id, p.player_name, p.full_name, p.player_number, s.team_id, s.position
                   FROM players p LEFT JOIN player_seasons s ON s.player_id = p.id AND s.season = %s""", (season,))
    existing = [dict(id=i, name_key=name_key(pn), full_key=full_key(fn), number=no, team_id=t, position=po)
                for i, pn, fn, no, t, po in cur.fetchall()]
    by_full, by_name = {}, {}
    for e in existing:
        by_full.setdefault(e["full_key"], []).append(e)
        by_name.setdefault(e["name_key"], []).append(e)

    m, new_idx, ambiguous = match_roster(parsed, existing)
    if ambiguous:
        raise LoadError("roster: players that can't be told apart (nothing written):\n  " + "\n  ".join(
            f"{a} ({parsed[int(a.split()[1].rstrip(':'))]['team']} {parsed[int(a.split()[1].rstrip(':'))]['first']} "
            f"{parsed[int(a.split()[1].rstrip(':'))]['last']})" for a in ambiguous))
    matched = [(pid, how, parsed[i]) for i, (pid, how) in m.items()]
    new = [parsed[i] for i in new_idx]
    used = {pid: f"{p['team']} {p['first']} {p['last']}" for pid, _, p in matched}

    hows = {}
    for _, how, _ in matched:
        hows[how] = hows.get(how, 0) + 1
    report["matched"] = hows
    report["new_players"] = len(new)

    def write(pid, p):
        cur.execute("""UPDATE players SET player_name = %s, full_name = %s, height = %s, weight = %s, player_number = %s
                       WHERE id = %s""",
                    (db_player_name(p["first"], p["last"]), f"{p['first']} {p['last']}", p["height"], p["weight"],
                     p["number"], pid))
        cur.execute("""INSERT INTO player_seasons (player_id, season, team_id, position, age, overall_rating,
                           years_of_experience, dev_trait, archetype, role)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (player_id, season) DO UPDATE SET team_id = EXCLUDED.team_id,
                           position = EXCLUDED.position, age = EXCLUDED.age, overall_rating = EXCLUDED.overall_rating,
                           years_of_experience = EXCLUDED.years_of_experience, dev_trait = EXCLUDED.dev_trait,
                           archetype = EXCLUDED.archetype, role = EXCLUDED.role""",
                    (pid, season, p["team_id"], p["position"], p["age"], p["ovr"], p["experience"], p["dev"],
                     p["archetype"], p["role"]))

    changed_team = 0
    for pid, how, p in matched:
        old = next(e for e in existing if e["id"] == pid)
        if old["team_id"] is not None and old["team_id"] != p["team_id"]:
            changed_team += 1
        write(pid, p)
    for p in new:
        cur.execute("INSERT INTO players (player_name) VALUES (%s) RETURNING id", (db_player_name(p["first"], p["last"]),))
        write(cur.fetchone()[0], p)
    report["team_changes"] = changed_team

    seen = set(used)
    missing = [e for e in existing if e["team_id"] is not None and e["id"] not in seen]
    report["not_on_any_roster"] = len(missing)
    report["not_on_any_roster_sample"] = [e["id"] for e in missing[:20]]
    # a full 32-team capture is the whole league: anyone it didn't find is on no team this season
    # (JR, Oct 14: clear the team, delete nothing; stats and history stay)
    if missing and len(per_team) == 32:
        cur.execute("UPDATE player_seasons SET team_id = NULL WHERE season = %s AND player_id = ANY(%s)",
                    (season, [e["id"] for e in missing]))
        report["team_cleared"] = len(missing)
    return len(parsed)


# ----------------------------------------------------------- column maps ----
# captured header -> (table, column, parser). Offense goes on season_stats / player_stats; every
# other category on its own table (docs/PIPELINE_MIGRATION.md, hybrid layout). GP/DP repeat in
# every Season Stats category and go to the general table once.
I, D = num, dec
SEASON_OFFENSE = {
    "passing": {"YARDS": ("pass_yards", I), "COMP": ("completions", I), "ATT": ("pass_attempts", I),
                "TD": ("pass_tds", I), "INT": ("interceptions", I), "COMP%": ("pass_comp_pct", D),
                "YPG": ("pass_ypg", D), "RATING": ("pass_rating", D), "YPA": ("pass_ypa", D),
                "LONG": ("pass_long", I), "SACKS": ("pass_sacks", I)},
    "rushing": {"ATT": ("rush_attempts", I), "YARDS": ("rush_yards", I), "AVG": ("rush_avg", D),
                "TD": ("rush_tds", I), "AVG G": ("rush_ypg", D), "FUMB": ("fumb", I), "BTK": ("rush_btk", I),
                "YAC": ("rush_yac", I), "LONG": ("rush_long", I)},
    "receiving": {"REC": ("receptions", I), "YARDS": ("rec_yards", I), "AVG": ("rec_avg", D),
                  "AVG G": ("rec_ypg", D), "TD": ("rec_tds", I), "RAC": ("rec_rac", I),
                  "RAC AVG": ("rec_rac_avg", D), "LONG": ("rec_long", I)},
}
GAME_OFFENSE = {
    "passing": {"RATING": ("pass_rating", D), "YARDS": ("pass_yards", I), "TD": ("pass_tds", I),
                "INT": ("interceptions", I), "LONG": ("pass_long", I), "SACKS": ("pass_sacks", I),
                "COMP": ("completions", I), "ATT": ("pass_attempts", I), "COMP%": ("pass_comp_pct", D),
                "YPA": ("pass_ypa", D)},
    "rushing": {"ATT": ("rush_attempts", I), "YARDS": ("rush_yards", I), "AVG": ("rush_avg", D),
                "TD": ("rush_tds", I), "FUMB": ("fumb", I), "BTK": ("rush_btk", I), "YAC": ("rush_yac", I),
                "LONG": ("rush_long", I)},
    "receiving": {"REC": ("receptions", I), "YARDS": ("rec_yards", I), "AVG": ("rec_avg", D), "TD": ("rec_tds", I),
                  "RAC": ("rec_rac", I), "RAC AVG": ("rec_rac_avg", D), "LONG": ("rec_long", I)},
}
CATEGORY = {   # same headers at season and game level (GP/DP aside)
    "defense": {"SOLO": ("solo", I), "ASSISTS": ("assists", I), "TAK": ("tackles", I), "TFL": ("tfl", I),
                "SACK": ("sacks", D), "INT": ("ints", I), "INT YDS": ("int_yds", I), "INT AVG": ("int_avg", D),
                "INT LG": ("int_long", I), "DEFL": ("deflections", I), "FFUMB": ("forced_fumbles", I),
                "FMBREC": ("fumble_recoveries", I), "FMBYDS": ("fumble_yds", I), "BLOCK": ("blocks", I),
                "SFTY": ("safeties", I), "TD": ("tds", I)},
    "kicking": {"FGM": ("fgm", I), "FGA": ("fga", I), "FG%": ("fg_pct", D), "FG LNG": ("fg_long", I),
                "FG BLK": ("fg_blocked", I), "XPM": ("xpm", I), "XPA": ("xpa", I), "XP%": ("xp_pct", D),
                "XPB": ("xp_blocked", I), "FGM29": ("fgm_29", I), "FGA29": ("fga_29", I), "FGM39": ("fgm_39", I),
                "FGA39": ("fga_39", I), "FGM49": ("fgm_49", I), "FGA49": ("fga_49", I), "FGM50": ("fgm_50", I),
                "FGA50": ("fga_50", I), "KO": ("kickoffs", I), "TB": ("touchbacks", I)},
    "punting": {"PUNTS": ("punts", I), "YARDS": ("yards", I), "AVG": ("average", D), "NET YDS": ("net_yards", I),
                "NET AVG": ("net_average", D), "BLOCK": ("blocked", I), "IN20": ("inside_20", I),
                "TB": ("touchbacks", I), "LONG": ("longest", I)},
    "kickreturn": {"KR": ("returns", I), "YARDS": ("yards", I), "AVG": ("average", D), "TD": ("tds", I),
                   "LONG": ("longest", I)},
    "puntreturn": {"PR": ("returns", I), "YARDS": ("yards", I), "AVG": ("average", D), "TD": ("tds", I),
                   "LONG": ("longest", I)},
    "blocking": {"SACK": ("sacks_allowed", I)},
}
# Counting stats only go up over a season ("no season total goes down"); yards don't (negative
# rushing yards: a QB's kneel-downs took one total from 0 to -2), nor averages, rates or longs.
NOT_COUNTERS = ("pct", "avg", "average", "ypg", "ypa", "rating", "long", "longest", "yards", "yds", "yac",
                "rac", "net_yards")


def counters_of(cols):
    return [c for c in cols if not c.endswith(NOT_COUNTERS) and "comp_pct" not in c]


def misread_copies(rows, cmap):
    """Rows sharing NAME + POS that differ in at most 2 cells are one row read twice (Gemini on
    overlapping pages: C.Matias GP 16 / 15, B.Favors TD 1 / 7), not two players."""
    if len(rows) < 2:
        return None
    a, b = rows[0], rows[1]
    diff = [h for h in cmap if a.get(h) != b.get(h)] + [h for h in ("GP", "DP") if a.get(h) != b.get(h)]
    return diff if len(diff) <= 2 else None
OFFENSE_MUST_MATCH = ("passing", "rushing", "receiving")
TEAM_TABS = {
    "team_conversions": {"3DC": "third_down_conv", "3DA": "third_down_att", "3D%": "third_down_pct",
                         "4DC": "fourth_down_conv", "4DA": "fourth_down_att", "4D%": "fourth_down_pct",
                         "2PC": "two_point_conv", "2PA": "two_point_att", "2P%": "two_point_pct"},
    "team_redzoneefficiency": {"ATT": "red_zone_att", "TD": "red_zone_td", "FG": "red_zone_fg", "OFF%": "red_zone_pct",
                               "DATT": "red_zone_def_att", "DTD": "red_zone_def_td", "DFG": "red_zone_def_fg",
                               "DEF%": "red_zone_def_pct"},
    "team_penalties": {"PEN": "penalties", "YARDS": "penalty_yards"},
}
BOX_SCORE = {"OFF YARDS GAINED": "total_offense", "OFF RUSH YARDS": "rushing_yards", "OFF PASS YARDS": "passing_yards",
             "OFF FIRST DOWN": "first_downs", "PUNT RETURN YARDS": "punt_return_yards",
             "KICK RETURN YARDS": "kick_return_yards", "TOTAL YARDS GAINED": "total_yards_gained",
             "TURNOVERS": "turnovers", "THIRD DOWN CONVERSIONS": "third_down_conversions",
             "FOURTH DOWN CONVERSIONS": "fourth_down_conversions", "TWO POINT CONVERSIONS": "two_point_conversions",
             "RED ZONE OFF PERCENTAGE": "red_zone_pct", "RED ZONE OFF TD": "red_zone_tds", "RED ZONE OFF FG": "red_zone_fgs",
             "PENALTY YARDS": "penalty_yards"}
# positions that can appear in a game category; settles teammates who share a list name (S.Lewis LT / HB)
CATEGORY_POSITIONS = {
    "passing": {"QB", "HB", "FB", "WR", "TE", "P", "K"}, "rushing": {"QB", "HB", "FB", "WR", "TE"},
    "receiving": {"HB", "FB", "WR", "TE"}, "blocking": {"LT", "LG", "C", "RG", "RT", "TE", "FB", "HB"},
    "defense": {"LEDG", "REDG", "DT", "MIKE", "WILL", "SAM", "CB", "FS", "SS"}, "kicking": {"K", "P"},
    "punting": {"P", "K"},
}


def parse_row(row, cmap, where, required=True):
    """Captured cells -> {column: value}. Every header the map names must be in the CSV (a missing
    one is a renamed or misread header, which would otherwise leave that stat out silently)."""
    out = {}
    missing = [h for h in cmap if h not in row]
    if missing and required:
        raise LoadError(f"{where}: columns {missing} not in the CSV (headers: {sorted(row)})")
    for header, (col, parse) in cmap.items():
        if header in row:
            try:
                out[col] = parse(row[header])
            except ValueError as e:
                raise LoadError(f"{where}: {header} {e}")
    return out


def team_index(cur):
    cur.execute("SELECT id, name, abbreviation FROM teams")
    by_name, by_abbr = {}, {}
    for i, n, a in cur.fetchall():
        by_name[n.upper()] = i
        by_abbr[a.upper()] = i
    return by_name, by_abbr


def roster(cur, season):
    """[(player_id, name key, team_id, position, player_name)] of everyone on a team this season."""
    cur.execute("""SELECT p.id, p.player_name, s.team_id, s.position FROM players p
                   JOIN player_seasons s ON s.player_id = p.id AND s.season = %s WHERE s.team_id IS NOT NULL""",
                (season,))
    return [(pid, name_key(pn), t, pos, pn) for pid, pn, t, pos in cur.fetchall()]


# --------------------------------------------------------- season schedule ----
def load_season_schedule(cur, folder, season, report):
    rows = read_csv(os.path.join(folder, "season_schedule.csv"))
    by_name, by_abbr = team_index(cur)
    games, per_team, problems = [], {}, []
    for r in rows:
        try:
            week = int(r["WEEK"].split()[-1])
            away, home = [by_name[x.strip().upper()] for x in r["MATCHUP"].split("@")]
        except (KeyError, ValueError):
            problems.append(f"can't read {r}")
            continue
        games.append((week, away, home, r["GAME TIME"]))
        for t in (away, home):
            per_team[t] = per_team.get(t, 0) + 1
    if len(games) != 272:
        problems.append(f"{len(games)} games, not 272")
    bad = {t: n for t, n in per_team.items() if n != 17}
    if bad or len(per_team) != 32:
        problems.append(f"teams without 17 games: {bad} ({len(per_team)} teams)")
    if problems:
        raise LoadError("season schedule checks failed:\n  " + "\n  ".join(problems))
    added = kept = 0
    for week, away, home, gtime in games:
        cur.execute("SELECT status FROM games WHERE season = %s AND week = %s AND home_team_id = %s AND away_team_id = %s",
                    (season, week, home, away))
        if cur.fetchone():
            kept += 1                          # never touch an existing game (completed ones above all)
            continue
        cur.execute("INSERT INTO games (season, week, home_team_id, away_team_id, status, game_time, home_score, "
                    "away_score) VALUES (%s, %s, %s, %s, 'SCHEDULED', %s, 0, 0)", (season, week, home, away, gtime))
        added += 1
    report["games_added"], report["games_already_there"] = added, kept
    return len(games)


# ------------------------------------------------------------------ weekly ----
def scoped_rows(folder, team_ids):
    """stats_scope_<team>_<category>.csv (a team-scoped Season Stats capture) -> {category: {team_id: [row]}}."""
    import glob as _glob
    out = {}
    for path in _glob.glob(os.path.join(folder, "stats_scope_*.csv")):
        team, cat = os.path.basename(path)[len("stats_scope_"):-4].split("_", 1)
        tid = team_ids.get(team.upper())
        if tid is not None:
            out.setdefault(cat, {})[tid] = read_csv(path)
    return out


def assign_by_scope(rs, cands, scoped, cmap):
    """Settle same-name rows with team-scoped captures. A row whose numbers appear under the same
    name and position on one candidate team's own list is that team's player; a captured team
    whose list lacks the row is ruled out for it. Every row must end with exactly one player, each
    a different one; else None. (So one team's list settles a pair.)"""
    def on_list(t, r):
        return any(x["NAME"] == r["NAME"] and x["POS"] == r["POS"] and all(x.get(h) == r.get(h) for h in cmap)
                   for x in scoped.get(t, []))
    options = []
    for r in rs:
        sure = [pid for pid, t in cands if t in scoped and on_list(t, r)]
        options.append(sure if sure else [pid for pid, t in cands if t not in scoped])
    got, used = {}, set()
    for _ in range(len(rs)):                 # settle rows with one option, then what that leaves
        for i, opts in enumerate(options):
            left = [o for o in opts if o not in used]
            if i not in got and len(left) == 1:
                got[i] = left[0]
                used.add(left[0])
    return got if len(got) == len(rs) else None


def resolve_season_rows(cur, season, cat, rows, cmap, table, players, problems, unmatched, scoped=None,
                        team_names=None, folder=""):
    """Season Stats rows (NAME, POS, no team) -> {player_id: (row, values)}. Shared names are
    settled by history (totals only go up); without history the load stops (JR, Oct 14)."""
    from pipeline.capture_matching import assign_by_history
    scoped, team_names = scoped or {}, team_names or {}
    by_key = {}
    for pid, key, team, pos, pn in players:
        by_key.setdefault((key, pos), []).append((pid, team))
    groups = {}
    for r in rows:
        groups.setdefault((name_key(r["NAME"]), r["POS"]), []).append(r)
    out = {}
    for (key, pos), rs in groups.items():
        cands = by_key.get((key, pos), [])
        values = [parse_row(r, cmap, f"{cat} {r['NAME']}") for r in rs]
        diff = misread_copies(rs, cmap)
        if diff:
            problems.append(f"{cat}: {rs[0]['NAME']} {pos} appears {len(rs)} times, differing only in {diff}: one row "
                            "read differently on overlapping pages; re-extract")
            continue
        if not cands:
            twin = [r2 for r2 in rows if r2 is not rs[0] and r2["POS"] == pos and r2["NAME"] != rs[0]["NAME"]
                    and all(r2.get(h) == rs[0].get(h) for h in cmap)]
            unmatched.append(f"{cat}: {rs[0]['NAME']} {pos}" + (f" (same numbers as {twin[0]['NAME']}: a misread name?)"
                                                               if twin else ""))
            continue
        if len(cands) == 1 and len(rs) == 1:
            out[cands[0][0]] = (rs[0], values[0], cands[0][1])
            continue
        counters = counters_of(values[0])
        ids = [pid for pid, _ in cands]
        if table == "season_stats":
            cur.execute(f"SELECT player_id, {', '.join(counters)} FROM season_stats WHERE season = %s AND player_id = ANY(%s)",
                        (season, ids))
        else:
            cur.execute(f"SELECT player_id, {', '.join(counters)} FROM {table} WHERE season = %s AND player_id = ANY(%s)",
                        (season, ids))
        hist = {pid: {} for pid in ids}
        for row in cur.fetchall():
            hist[row[0]] = dict(zip(counters, row[1:]))
        got = assign_by_history(values, hist, counters)
        by_scope = False
        if got is None:
            got = assign_by_scope(rs, cands, scoped.get(cat, {}), cmap)
            by_scope = got is not None
        if got is None:
            teams = sorted({team_names[t] for _, t in cands})
            need = teams[:len(cands) - 1]             # n-1 teams' own lists settle n players
            problems.append(f"{cat}: {len(rs)} row(s) {rs[0]['NAME']} {pos} fit {len(cands)} players "
                            f"({', '.join(teams)}) and their earlier totals can't tell them apart. Capture: "
                            + "; ".join(f"madden_capture.py --only stats --scope {t.upper()} --category {cat.upper()} "
                                        f"--resume {os.path.basename(os.path.normpath(folder))}" for t in need))
            continue
        team_of = dict(cands)
        for i, pid in got.items():
            out[pid] = (rs[i], values[i], team_of[pid])
            if by_scope:
                SCOPE_SETTLED.add((table, pid))
    return out


# (table, player id) whose row a team-scoped capture settled: the team's own list is direct
# evidence, so a total going down there corrects earlier data (the old ingestion had given Billy
# Thompson's 86 catches to Brenen Thompson) and is reported, not refused
SCOPE_SETTLED = set()


def load_weekly(cur, folder, season, report):
    SCOPE_SETTLED.clear()
    by_name, by_abbr = team_index(cur)
    problems, unmatched = [], []

    # 1. the schedule: one week, scores -> games
    sched = read_csv(os.path.join(folder, "schedule.csv"))
    weeks = {r["WEEK"] for r in sched}
    if len(weeks) != 1:
        raise LoadError(f"schedule.csv covers {sorted(weeks)}, expected one week")
    week = int(weeks.pop().split()[-1])
    report["week"] = week
    cur.execute("SELECT max(week) FROM captures WHERE mode = 'weekly' AND season = %s AND status = 'loaded'", (season,))
    last = cur.fetchone()[0]
    if last is not None and week <= last:
        raise LoadError(f"week {week} is already loaded (last loaded week {last})")
    if last is not None and week != last + 1:
        report["gap"] = f"week {week} follows week {last}"
    cur.execute("SELECT id, home_team_id, away_team_id, status FROM games WHERE season = %s AND week = %s", (season, week))
    db_games = {(h, a): (gid, st) for gid, h, a, st in cur.fetchall()}
    scores = []
    for r in sched:
        try:
            away, home = [by_name[x.strip().upper()] for x in r["MATCHUP"].split("@")]
        except KeyError:
            problems.append(f"schedule: unknown team in {r['MATCHUP']}")
            continue
        from pipeline.capture_matching import RESULT_RE
        m = RESULT_RE.match(r["RESULT"].strip())
        if not m:
            problems.append(f"schedule: no final score for {r['MATCHUP']} ({r['RESULT']!r})")
            continue
        t1, s1, s2, t2 = m.groups()
        pts = {by_abbr.get(t1.upper()): int(s1), by_abbr.get(t2.upper()): int(s2)}
        if (home, away) not in db_games or set(pts) != {home, away}:
            problems.append(f"schedule: {r['MATCHUP']} week {week} isn't in games, or its score names other teams")
            continue
        scores.append((db_games[(home, away)][0], pts[home], pts[away]))
    if len(scores) != len(db_games):
        problems.append(f"schedule: {len(scores)} games captured, the games table has {len(db_games)} for week {week}")

    # 2. team tabs -> team_seasons
    team_rows = {}
    for f, cmap in TEAM_TABS.items():
        for r in read_csv(os.path.join(folder, f + ".csv")):
            tid = by_name.get(r["TEAM"].strip().upper())
            if tid is None:
                problems.append(f"{f}: unknown team {r['TEAM']!r}")
                continue
            vals = {}
            for h, col in cmap.items():
                try:
                    vals[col] = (dec if col.endswith("pct") else num)(r[h])
                except ValueError as e:
                    problems.append(f"{f} {r['TEAM']}: {h} {e}")
            team_rows.setdefault(tid, {}).update(vals)
    if len(team_rows) != 32:
        problems.append(f"team tabs cover {len(team_rows)} teams, not 32")

    # 3. Season Stats -> season_stats (offense) and the category tables
    players = roster(cur, season)
    scoped = scoped_rows(folder, by_name)
    cur.execute("SELECT id, name FROM teams")
    team_names = dict(cur.fetchall())
    general, writes = {}, []
    for cat in list(SEASON_OFFENSE) + list(CATEGORY):
        path = os.path.join(folder, f"stats_{cat}.csv")
        if not os.path.exists(path):
            problems.append(f"stats_{cat}.csv is missing")
            continue
        rows = read_csv(path)
        cmap = SEASON_OFFENSE.get(cat) or CATEGORY[cat]
        table = "season_stats" if cat in SEASON_OFFENSE else f"season_stat_{cat}"
        before = len(unmatched)
        try:
            got = resolve_season_rows(cur, season, cat, rows, cmap, table, players, problems, unmatched, scoped,
                                      team_names, folder)
        except LoadError as e:
            problems.append(str(e))
            continue
        if cat in OFFENSE_MUST_MATCH and len(unmatched) > before:
            problems.append(f"{cat}: {len(unmatched) - before} rows match no rostered player (offense must match 100%): "
                            + "; ".join(unmatched[before:before + 10]))
        for pid, (r, vals, team) in got.items():
            writes.append((table, pid, team, r["POS"], vals))
            g = general.setdefault(pid, {"team": team, "pos": r["POS"], "games_played": None, "downs_played": None})
            for h, col in (("GP", "games_played"), ("DP", "downs_played")):
                if r.get(h, "") != "":
                    try:
                        v = num(r[h])
                    except ValueError as e:
                        problems.append(f"{cat} {r['NAME']}: {h} {e}")
                        continue
                    g[col] = max(g[col] or 0, v)
        report.setdefault("rows_matched", {})[cat] = len(got)

    # no season total goes down
    for table, pid, team, pos, vals in writes:
        counters = counters_of(vals)
        if not counters:
            continue
        cur.execute(f"SELECT {', '.join(counters)} FROM {table} WHERE season = %s AND player_id = %s", (season, pid))
        old = cur.fetchone()
        if old:
            down = [f"{c} {o} -> {vals[c]}" for c, o in zip(counters, old) if o is not None and vals[c] is not None
                    and vals[c] < o]
            if down and (table, pid) in SCOPE_SETTLED:
                report.setdefault("corrected_by_scope", []).append(f"{table} player {pid}: {', '.join(down)}")
            elif down:
                problems.append(f"{table} player {pid}: totals went down ({', '.join(down)})")

    # row counts within 10% of the last weekly capture
    cur.execute("SELECT row_counts FROM captures WHERE mode = 'weekly' AND season = %s AND status = 'loaded' "
                "ORDER BY week DESC LIMIT 1", (season,))
    prev = cur.fetchone()
    if prev and prev[0]:
        now = row_counts(folder)
        for f, n in prev[0].items():
            if f.startswith("stats_") and f in now and n and abs(now[f] - n) > 0.1 * n:
                problems.append(f"{f}: {now[f]} rows, last week {n} (more than 10% apart)")

    if problems:
        raise LoadError("weekly checks failed:\n  " + "\n  ".join(problems))
    report["unmatched_other_categories"] = unmatched

    # writes
    for gid, hs, as_ in scores:
        cur.execute("UPDATE games SET home_score = %s, away_score = %s, status = 'COMPLETED' WHERE id = %s", (hs, as_, gid))
    for tid, vals in team_rows.items():
        cols = list(vals)
        cur.execute(f"INSERT INTO team_seasons (team_id, season, {', '.join(cols)}) VALUES (%s, %s, {', '.join(['%s'] * len(cols))}) "
                    f"ON CONFLICT (team_id, season) DO UPDATE SET " + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols),
                    (tid, season, *[vals[c] for c in cols]))
    names = {pid: pn for pid, _, _, _, pn in players}
    for table, pid, team, pos, vals in writes:
        cols = list(vals)
        if table == "season_stats":
            cur.execute(f"INSERT INTO season_stats (season, player_id, player_name, team_id, position, {', '.join(cols)}) "
                        f"VALUES (%s, %s, %s, %s, %s, {', '.join(['%s'] * len(cols))}) "
                        f"ON CONFLICT (season, player_id) WHERE player_id IS NOT NULL DO UPDATE SET team_id = EXCLUDED.team_id, "
                        f"position = EXCLUDED.position, " + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols),
                        (season, pid, names[pid], team, pos, *[vals[c] for c in cols]))
        else:
            cur.execute(f"INSERT INTO {table} (season, player_id, team_id, position, {', '.join(cols)}) "
                        f"VALUES (%s, %s, %s, %s, {', '.join(['%s'] * len(cols))}) ON CONFLICT (season, player_id) DO UPDATE SET "
                        f"team_id = EXCLUDED.team_id, position = EXCLUDED.position, "
                        + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols),
                        (season, pid, team, pos, *[vals[c] for c in cols]))
    for pid, g in general.items():
        cur.execute("INSERT INTO season_stat_general (season, player_id, team_id, position, games_played, downs_played) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (season, player_id) DO UPDATE SET team_id = EXCLUDED.team_id, "
                    "position = EXCLUDED.position, games_played = EXCLUDED.games_played, downs_played = EXCLUDED.downs_played",
                    (season, pid, g["team"], g["pos"], g["games_played"], g["downs_played"]))
    report["games_completed"], report["season_rows"] = len(scores), len(writes)

    # stale copies: the old ingestion stored some lines under the wrong player (J. Cabrera WR on the
    # Chiefs, now a linebacker; the line is the Rams' J. Cabrera's). A season_stats row this load
    # didn't write that has the same list name, position and offense numbers as one it did write,
    # under another player, is that copy: remove it so the line isn't counted twice (JR, Oct 14)
    written = {pid for table, pid, *_ in writes if table == "season_stats"}
    offense = ["completions", "pass_attempts", "pass_yards", "pass_tds", "interceptions", "rush_attempts",
               "rush_yards", "rush_tds", "receptions", "rec_yards", "rec_tds", "fumb"]
    cur.execute(f"SELECT id, player_id, player_name, position, {', '.join(offense)} FROM season_stats WHERE season = %s",
                (season,))
    rows = cur.fetchall()
    fresh = {}
    for r in rows:
        if r[1] in written:
            fresh.setdefault((name_key(r[2]), r[3], tuple(r[4:])), []).append(r[1])
    stale = [r for r in rows if r[1] not in written and any(
        p != r[1] for p in fresh.get((name_key(r[2]), r[3], tuple(r[4:])), []))]
    if stale:
        cur.execute("DELETE FROM season_stats WHERE id = ANY(%s)", ([r[0] for r in stale],))
    report["stale_copies_removed"] = [f"{r[2]} {r[3]} (player {r[1]})" for r in stale]
    return len(writes)


# ------------------------------------------------------------------- games ----
def load_games(cur, folder, season, report, replace=False):
    """A recovery capture of one week's games: box scores -> team_stats, player lines -> player_stats
    (offense) and game_stat_* (other categories), scores -> games."""
    by_name, by_abbr = team_index(cur)
    players = roster(cur, season)
    problems = []
    scores = read_csv(os.path.join(folder, "game_scores.csv"))
    games = {}
    for r in scores:
        away, home = by_abbr.get(r["AWAY"].upper()), by_abbr.get(r["HOME"].upper())
        cur.execute("SELECT id, week FROM games WHERE season = %s AND away_team_id = %s AND home_team_id = %s",
                    (season, away, home))
        hits = cur.fetchall()
        if len(hits) != 1:
            problems.append(f"{r['GAME']}: {len(hits)} games {r['AWAY']} @ {r['HOME']} in {season}")
            continue
        games[r["GAME"]] = dict(id=hits[0][0], week=hits[0][1], away=away, home=home, score=r)
    weeks = {g["week"] for g in games.values()}
    if len(weeks) > 1:
        problems.append(f"games from several weeks: {sorted(weeks)}")
    if games:
        ids = [g["id"] for g in games.values()]
        cur.execute("SELECT count(*) FROM player_stats WHERE game_id = ANY(%s)", (ids,))
        existing = cur.fetchone()[0]
        if existing and not replace:
            problems.append(f"those games already have {existing} player_stats rows (the backfill's); "
                            "load with --replace to swap them for the captured lines")
        report["week"] = weeks.pop() if len(weeks) == 1 else None
        cur.execute("SELECT count(*) FROM games WHERE season = %s AND week = %s AND status <> 'BYE'", (season, report["week"]))
        if cur.fetchone()[0] != len(games):
            problems.append(f"{len(games)} games captured for week {report['week']}, the games table has more")

    by_team = {}
    for pid, key, team, pos, pn in players:
        by_team.setdefault((team, key), []).append((pid, pos, pn))

    def who(cat, team, name):
        hits = by_team.get((team, name_key(name)), [])
        if len(hits) > 1 and cat in CATEGORY_POSITIONS:
            hits = [h for h in hits if h[1] in CATEGORY_POSITIONS[cat]] or hits
        return hits

    lines = {}            # (game id, player id) -> {"team", "pos", "name", table: {col: v}}
    unmatched, later = [], []
    for path in sorted(os.listdir(folder)):
        if not (path.startswith("game_stats_") and path.endswith(".csv")):
            continue
        cat = path[len("game_stats_"):-4]
        cmap = GAME_OFFENSE.get(cat) or CATEGORY.get(cat) or ({"DP": ("downs_played", I)} if cat == "general" else None)
        if cmap is None:
            continue
        table = "player_stats" if cat in GAME_OFFENSE else f"game_stat_{cat}"
        for r in read_csv(os.path.join(folder, path)):
            g = games.get(r["GAME"])
            if g is None:
                continue
            team = by_abbr.get(r["TEAM"].upper())
            if team not in (g["away"], g["home"]):
                problems.append(f"{r['GAME']} {cat}: {r['NAME']} on {r['TEAM']}, not in this game")
                continue
            hits = who(cat, team, r["NAME"])
            if len(hits) > 1 and cat == "general":
                later.append((g, team, r, hits))   # settled below by who has other lines in that game
                continue
            if len(hits) != 1:
                (problems if cat in OFFENSE_MUST_MATCH and hits else unmatched).append(
                    f"{r['GAME']} {cat}: {r['TEAM']} {r['NAME']} ({len(hits)} players)")
                if cat in OFFENSE_MUST_MATCH and not hits:
                    problems.append(f"{r['GAME']} {cat}: {r['TEAM']} {r['NAME']} matches no rostered player")
                continue
            pid, pos, pn = hits[0]
            vals = parse_row(r, cmap, f"{r['GAME']} {cat} {r['NAME']}")
            if not any(v for v in vals.values()):
                continue                       # an all-zero line: nothing to store
            line = lines.setdefault((g["id"], pid), {"team": team, "pos": pos, "name": pn})
            line.setdefault(table, {}).update(vals)
    # downs played (General) has no category to tell same-name teammates apart: the one with other
    # lines in that game is the one who played (two TB D.Davis, the Seahawks' S.Lewis pair)
    for g, team, r, hits in later:
        played = [h for h in hits if (g["id"], h[0]) in lines]
        if len(played) != 1:
            unmatched.append(f"{r['GAME']} general: {r['TEAM']} {r['NAME']} ({len(hits)} players)")
            continue
        pid, pos, pn = played[0]
        vals = parse_row(r, {"DP": ("downs_played", I)}, f"{r['GAME']} general {r['NAME']}")
        if any(vals.values()):
            lines[(g["id"], pid)].setdefault("game_stat_general", {}).update(vals)
    if problems:
        raise LoadError("games checks failed:\n  " + "\n  ".join(problems))
    report["unmatched_lines"] = unmatched

    ids = [g["id"] for g in games.values()]
    if replace:
        cur.execute("DELETE FROM player_stats WHERE game_id = ANY(%s)", (ids,))
        cur.execute("DELETE FROM team_stats WHERE game_id = ANY(%s)", (ids,))
        for cat in CATEGORY_POSITIONS.keys() | {"general", "kickreturn", "puntreturn"}:
            if cat not in GAME_OFFENSE:
                cur.execute(f"DELETE FROM game_stat_{cat} WHERE game_id = ANY(%s)", (ids,))
    n = 0
    for (gid, pid), line in lines.items():
        for table, vals in line.items():
            if table in ("team", "pos", "name"):
                continue
            cols = list(vals)
            if table == "player_stats":
                cur.execute(f"INSERT INTO player_stats (game_id, player_id, player_name, team_id, position, {', '.join(cols)}) "
                            f"VALUES (%s, %s, %s, %s, %s, {', '.join(['%s'] * len(cols))})",
                            (gid, pid, line["name"], line["team"], line["pos"], *[vals[c] for c in cols]))
            else:
                cur.execute(f"INSERT INTO {table} (game_id, player_id, team_id, position, {', '.join(cols)}) "
                            f"VALUES (%s, %s, %s, %s, {', '.join(['%s'] * len(cols))})",
                            (gid, pid, line["team"], line["pos"], *[vals[c] for c in cols]))
            n += 1
    # box scores -> team_stats, scores -> games
    cur.execute("SELECT id, name FROM teams")
    tname = dict(cur.fetchall())
    for gname, g in games.items():
        r = g["score"]
        box = {b["LABEL"]: b for b in read_csv(os.path.join(folder, "games", gname, "box_score.csv"))}
        ok = r.get("QUARTERS_OK", "y") == "y"
        for side, tid, pre in (("away", g["away"], "A"), ("home", g["home"], "H")):
            abbr = r["AWAY" if side == "away" else "HOME"]
            vals = {col: (dec if col.endswith("pct") else num)(box[label][abbr]) for label, col in BOX_SCORE.items()
                    if label in box}
            top = box.get("TIME OF POSSESSION", {}).get(abbr, "")
            if ":" in top:
                mm, ss = top.split(":")
                vals["time_of_possession_s"] = int(mm) * 60 + int(ss)
            for q, h in (("q1", "1"), ("q2", "2"), ("q3", "3"), ("q4", "4"), ("ot", "OT")):
                vals[q] = num(r[pre + h]) if ok else None
            vals["score"] = num(r[pre + "TOTAL"])
            cols = list(vals)
            cur.execute(f"INSERT INTO team_stats (game_id, team_name, {', '.join(cols)}) VALUES (%s, %s, "
                        f"{', '.join(['%s'] * len(cols))})", (g["id"], tname[tid], *[vals[c] for c in cols]))
        cur.execute("UPDATE games SET away_score = %s, home_score = %s, status = 'COMPLETED' WHERE id = %s",
                    (num(r["ATOTAL"]), num(r["HTOTAL"]), g["id"]))
    report["games"], report["lines"] = len(games), n
    return n


# -------------------------------------------------------------------- runs ----
def capture_row(cur, folder, mode, season, week, dry_run=False):
    """The run's `captures` row (created or reset to extracted); a run already loaded is refused.
    A dry run only checks and returns None."""
    name = os.path.basename(os.path.normpath(folder))
    cur.execute("SELECT id, status FROM captures WHERE folder = %s", (name,))
    row = cur.fetchone()
    if row and row[1] == "loaded":
        raise LoadError(f"{name} is already loaded")
    if dry_run:
        return None
    if row:
        cur.execute("UPDATE captures SET mode = %s, season = %s, week = %s, status = 'extracted', error = NULL "
                    "WHERE id = %s", (mode, season, week, row[0]))
        return row[0]
    cur.execute("INSERT INTO captures (folder, mode, season, week, status, extracted_at) "
                "VALUES (%s, %s, %s, %s, 'extracted', now()) RETURNING id", (name, mode, season, week))
    return cur.fetchone()[0]


def row_counts(folder):
    out = {}
    for f in sorted(os.listdir(folder)):
        if f.endswith(".csv"):
            with open(os.path.join(folder, f)) as fh:
                out[f] = max(0, sum(1 for _ in fh) - 1)
    return out


LOADERS = {"roster": load_roster, "season_schedule": load_season_schedule, "weekly": load_weekly,
           "games": load_games}


def load(folder, dry_run=False, replace=False):
    folder = folder.rstrip("/")
    mode = detect_mode(folder)
    if mode not in LOADERS:
        raise LoadError(f"mode {mode} isn't built yet")
    report = {"folder": folder, "mode": mode}
    with psycopg.connect(DSN) as conn:
        cur = conn.cursor()
        cur.execute("SELECT current_season, current_week FROM league_state WHERE id = 1")
        season, week = cur.fetchone()
        report["season"] = season
        cid = capture_row(cur, folder, mode, season, None if mode == "roster" else week, dry_run)
        conn.commit()                        # the run is recorded even if the load fails
        try:
            if mode == "games":
                report["rows"] = load_games(cur, folder, season, report, replace=replace)
            else:
                report["rows"] = LOADERS[mode](cur, folder, season, report)
        except Exception as e:
            conn.rollback()
            if cid is not None:
                cur.execute("UPDATE captures SET status = 'failed', error = %s WHERE id = %s", (str(e)[:4000], cid))
                conn.commit()
            raise
        if dry_run:
            conn.rollback()
            report["dry_run"] = True
            return report
        cur.execute("UPDATE captures SET status = 'loaded', loaded_at = now(), row_counts = %s, error = NULL, "
                    "week = COALESCE(%s, week) WHERE id = %s", (json.dumps(row_counts(folder)), report.get("week"), cid))
        conn.commit()
    return report


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder")
    ap.add_argument("--dry-run", action="store_true", help="run the checks and report, write nothing")
    ap.add_argument("--replace", action="store_true",
                    help="games: replace the backfill's rows for those games with the captured lines")
    a = ap.parse_args()
    try:
        report = load(a.folder, a.dry_run, a.replace)
    except LoadError as e:
        print(f"LOAD STOPPED, nothing written: {e}")
        sys.exit(1)
    print(json.dumps(report, indent=1, default=str))


if __name__ == "__main__":
    main()
