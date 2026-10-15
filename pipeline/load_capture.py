"""
Load an extracted capture folder into the database (docs/PIPELINE_MIGRATION.md, phase 3).

  python3 -m pipeline.load_capture captures/<run> [--dry-run]

The mode is read from the folder: roster/ (roster), season_schedule/ (season schedule), games/
(games, recovery only), else stats/ + team_stats/ + schedule/ (weekly). One transaction per run:
any failed check writes nothing, records the run as failed in `captures` and exits non-zero.
--dry-run runs every check and prints what would change, then rolls back.
Uses DATABASE_URL (default: the local madden database); point it at a restored test copy
to try a load before running it live.
"""
import argparse, csv, json, os, re, sys
from datetime import datetime

import psycopg

from pipeline.capture_matching import all_caps, db_player_name, db_week, dec, full_key, match_roster, name_case, name_key, num

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
        # a postseason run: last round's schedule (+ next round) and its box scores, no Season Stats
        return "playoff" if os.path.exists(os.path.join(folder, "schedule.csv")) else "games"
    if os.path.exists(os.path.join(folder, "stats_passing.csv")):
        return "weekly"
    if os.path.exists(os.path.join(folder, "injuries.csv")):
        return "injuries"
    if os.path.exists(os.path.join(folder, "prospects.csv")):
        return "prospects"
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
    prospects_csv = os.path.join(folder, "prospects.csv")
    prospects = None
    if os.path.exists(prospects_csv):
        try:
            from pipeline.ingest_prospects import load_prospects_from_csv
            prospects = load_prospects_from_csv(prospects_csv)
        except Exception as e:
            problems.append(f"prospects validation failed: {e}")
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

    stored = {e["id"]: e for e in existing}
    cur.execute("SELECT id, player_name, full_name FROM players")
    stored_names = {i: (pn, fn) for i, pn, fn in cur.fetchall()}

    def same_text(a, b):
        return re.sub(r"[^A-Z]", "", (a or "").upper()) == re.sub(r"[^A-Z]", "", (b or "").upper())

    def write(pid, p):
        # keep a stored spelling that names the same person ("S. LaPorta" beats the card's OCR "Laporta");
        # the stats tables and the backfill match players by this text. An all-caps stored spelling (the old
        # video scanner's "JERRY RAYFORD") loses to the card's casing.
        pname, fname = db_player_name(p["first"], p["last"]), f"{p['first']} {p['last']}"
        old_pn, old_fn = stored_names.get(pid, (None, None))
        if old_pn and same_text(old_pn, pname) and not all_caps(old_pn):
            pname = old_pn
        if old_fn and same_text(old_fn, fname) and not all_caps(old_fn):
            fname = old_fn
        cur.execute("""UPDATE players SET player_name = %s, full_name = %s, height = %s, weight = %s, player_number = %s
                       WHERE id = %s""",
                    (pname, fname, p["height"], p["weight"], p["number"], pid))
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
    # A full 32-team capture is the whole league. Anyone it didn't find who holds no real data (no
    # stats, awards, article tags or agent memories; on Oct 14 these were the old video scanner's
    # misreads and duplicates) is deleted with their generated fantasy rows. Anyone with real data
    # keeps their team and is listed (JR, Oct 14: clearing the team broke the engines, which
    # assume every player_seasons row has one).
    if missing and len(per_team) == 32:
        ids = [e["id"] for e in missing]
        cur.execute("""SELECT p.id FROM unnest(%s::int[]) AS p(id) WHERE
                       EXISTS (SELECT 1 FROM season_stats x WHERE x.player_id = p.id) OR
                       EXISTS (SELECT 1 FROM player_stats x WHERE x.player_id = p.id) OR
                       EXISTS (SELECT 1 FROM awards x WHERE x.player_id = p.id) OR
                       EXISTS (SELECT 1 FROM article_players x WHERE x.player_id = p.id) OR
                       EXISTS (SELECT 1 FROM agent_memory_entries x WHERE x.player_id = p.id)""", (ids,))
        keep = {r[0] for r in cur.fetchall()}
        drop = [i for i in ids if i not in keep]
        if drop:
            for t in ("fantasy_projections", "fantasy_sim_adp", "fantasy_sim_picks", "fantasy_sim_player_weeks"):
                cur.execute(f"DELETE FROM {t} WHERE player_id = ANY(%s)", (drop,))
            cur.execute("DELETE FROM players WHERE id = ANY(%s)", (drop,))      # player_seasons cascade
        report["deleted_not_on_any_roster"] = len(drop)
        report["kept_not_on_any_roster"] = sorted(keep)
    if prospects is not None:
        draft_season = season + 1
        dest = os.path.join("data", "draft", f"{draft_season}_prospects.csv")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        if not report.get("dry_run") and os.path.abspath(prospects_csv) != os.path.abspath(dest):
            import shutil
            shutil.copyfile(prospects_csv, dest)
        report["prospects_loaded"] = len(prospects)
        report["draft_season"] = draft_season
        report["prospects_path"] = dest
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
# Postseason weeks (capture_matching.db_week) -> games.game_type. Playoff games aren't in the season schedule:
# the weekly load creates them from the captured schedule. Week 22 (Pro Bowl) has no games.
PLAYOFF_GAME_TYPES = {19: "WILD_CARD", 20: "DIVISIONAL", 21: "CONFERENCE", 23: "SUPER_BOWL"}
PRO_BOWL_WEEK, FINAL_WEEK = 22, 23
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


def load_weekly(cur, folder, season, report, season_stats=True):
    """Last week's schedule (scores) and, with season_stats, Season Stats and Team Stats. A postseason run
    passes season_stats=False: those screens are regular season only and don't change in the playoffs."""
    SCOPE_SETTLED.clear()
    by_name, by_abbr = team_index(cur)
    problems, unmatched = [], []

    # 1. the schedule: one week, scores -> games
    sched = read_csv(os.path.join(folder, "schedule.csv"))
    weeks = {r["WEEK"] for r in sched}
    if len(weeks) != 1:
        raise LoadError(f"schedule.csv covers {sorted(weeks)}, expected one week")
    label = weeks.pop()
    week = db_week(label)
    if week is None:
        raise LoadError(f"schedule.csv: can't read the week {label!r}")
    report["week"] = week
    cur.execute("SELECT current_week FROM league_state WHERE id = 1")
    current, source = next_current_week(folder, week, cur.fetchone()[0])
    cur.execute("SELECT max(week) FROM captures WHERE mode IN ('weekly', 'playoff') AND season = %s AND status = 'loaded'",
                (season,))
    last = cur.fetchone()[0]
    if last is not None and week <= last:
        raise LoadError(f"week {week} is already loaded (last loaded week {last})")
    if last is not None and week != last + 1:
        report["gap"] = f"week {week} follows week {last}"
    if week in PLAYOFF_GAME_TYPES:
        report["playoff_games_added"] = add_playoff_games(cur, sched, season, week, by_name)
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

    # 1b. postseason: the coming round's games (schedule_next.csv), created SCHEDULED
    report["next_round"] = load_next_round(cur, folder, season, current, by_name, problems)

    # 2. team tabs -> team_seasons
    team_rows = {}
    for f, cmap in (TEAM_TABS.items() if season_stats else ()):
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
    if season_stats and len(team_rows) != 32:
        problems.append(f"team tabs cover {len(team_rows)} teams, not 32")

    # 3. Season Stats -> season_stats (offense) and the category tables
    players = roster(cur, season)
    scoped = scoped_rows(folder, by_name)
    cur.execute("SELECT id, name FROM teams")
    team_names = dict(cur.fetchall())
    general, writes = {}, []
    for cat in (list(SEASON_OFFENSE) + list(CATEGORY) if season_stats else ()):
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
    if season_stats and prev and prev[0]:
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
                        f"position = EXCLUDED.position, player_name = EXCLUDED.player_name, "
                        + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols),
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
    report["injuries"] = load_injuries(cur, folder, season, current or week, by_name, problems)
    cur.execute("UPDATE league_state SET current_week = %s WHERE id = 1", (current,))
    report["current_week"] = f"{current} ({source})"

    if not season_stats:
        return len(scores)

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


def load_injuries(cur, folder, season, week, by_name, problems):
    """Load captures/<run>/injuries.csv into the injuries table.
    Matches players by (team_id, name_key) against player_seasons and players."""
    path = os.path.join(folder, "injuries.csv")
    if not os.path.exists(path):
        return 0
    rows = read_csv(path)
    if not rows:
        return 0

    # Look up players in this season by (team_id, name_key)
    cur.execute("""
        SELECT ps.player_id, ps.team_id, p.player_name, p.full_name, ps.position, ps.overall_rating
        FROM player_seasons ps
        JOIN players p ON p.id = ps.player_id
        WHERE ps.season = %s
    """, (season,))
    roster_rows = cur.fetchall()

    by_team_name = {}
    for pid, tid, pname, fname, pos, ovr in roster_rows:
        by_team_name.setdefault((tid, name_key(pname)), []).append((pid, pos, ovr, pname))
        if fname:
            by_team_name.setdefault((tid, name_key(fname)), []).append((pid, pos, ovr, pname))

    loaded = 0
    for r in rows:
        team_str = r.get("TEAM", "").strip().upper()
        tid = by_name.get(team_str)
        if tid is None:
            problems.append(f"injuries: unknown team {team_str!r}")
            continue

        raw_name = r.get("PLAYER_NAME", "").strip()
        nk = name_key(raw_name)
        pos = r.get("POSITION", "").strip()
        raw_ovr = r.get("OVR", "").strip()
        ovr = int(raw_ovr) if raw_ovr.isdigit() else 0
        injury_type = r.get("INJURY_TYPE", "").strip()
        duration = r.get("DURATION", "").strip()
        weeks_rem = int(r.get("WEEKS_REMAINING", 0)) if str(r.get("WEEKS_REMAINING", "")).isdigit() else 0
        is_ir = str(r.get("IS_IR", "")).lower() in ("true", "1", "yes")

        candidates = by_team_name.get((tid, nk), [])
        pid = None
        if len(candidates) == 1:
            pid = candidates[0][0]
            if not pos:
                pos = candidates[0][1]
            if not ovr:
                ovr = candidates[0][2]
        elif len(candidates) > 1:
            same_pos = [c for c in candidates if c[1] == pos]
            if len(same_pos) == 1:
                pid = same_pos[0][0]
                if not ovr:
                    ovr = same_pos[0][2]
            else:
                pid = candidates[0][0]

        cur.execute("""
            INSERT INTO injuries (
                season, week, team_id, player_id, player_name,
                position, ovr, injury_type, duration, weeks_remaining, is_ir
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (season, week, team_id, player_name) DO UPDATE SET
                player_id = EXCLUDED.player_id,
                position = EXCLUDED.position,
                ovr = EXCLUDED.ovr,
                injury_type = EXCLUDED.injury_type,
                duration = EXCLUDED.duration,
                weeks_remaining = EXCLUDED.weeks_remaining,
                is_ir = EXCLUDED.is_ir,
                captured_at = NOW()
        """, (season, week, tid, pid, raw_name, pos, ovr, injury_type, duration, weeks_rem, is_ir))
        loaded += 1

    return loaded


def load_injuries_standalone(cur, folder, season, report):
    """Standalone loader for --only injuries runs."""
    cur.execute("SELECT current_week FROM league_state WHERE id = 1")
    week = cur.fetchone()[0]
    report["week"] = week
    cur.execute("SELECT id, name, abbreviation FROM teams")
    by_name = {n.upper(): tid for tid, n, _ in cur.fetchall()}
    problems = []
    loaded = load_injuries(cur, folder, season, week, by_name, problems)
    if problems:
        raise LoadError("injuries checks failed:\n  " + "\n  ".join(problems))
    report["injuries"] = loaded
    return loaded


def add_playoff_games(cur, sched, season, week, by_name):
    """Create the playoff games a schedule capture lists (matchup and kickoff time; the load's score update
    completes them). Games already stored are left alone; unknown teams are reported by the schedule check."""
    added = 0
    for r in sched:
        try:
            away, home = [by_name[x.strip().upper()] for x in r["MATCHUP"].split("@")]
        except (KeyError, ValueError):
            continue
        cur.execute("INSERT INTO games (season, week, home_team_id, away_team_id, status, game_time, game_type) "
                    "VALUES (%s, %s, %s, %s, 'SCHEDULED', %s, %s) ON CONFLICT (season, week, home_team_id, away_team_id) "
                    "DO NOTHING RETURNING id",
                    (season, week, home, away, (r.get("GAME TIME") or "").strip() or None, PLAYOFF_GAME_TYPES[week]))
        added += cur.fetchone() is not None
    return added


def load_next_round(cur, folder, season, current, by_name, problems):
    """schedule_next.csv (postseason: the round the League Schedule opened on) -> its games, SCHEDULED.
    The round must be the week league_state moves to. Returns a short report, or None without the file."""
    path = os.path.join(folder, "schedule_next.csv")
    if not os.path.exists(path):
        return None
    rows = read_csv(path)
    weeks = {db_week(r["WEEK"]) for r in rows}
    if len(weeks) != 1:
        problems.append(f"schedule_next.csv: weeks {sorted(map(str, weeks))}, expected one")
        return None
    week = weeks.pop()
    coming = FINAL_WEEK if current == PRO_BOWL_WEEK else current     # Pro Bowl week: the Super Bowl is next
    if week not in PLAYOFF_GAME_TYPES or week != coming:
        problems.append(f"schedule_next.csv: week {week} isn't the coming playoff round (current week {current})")
        return None
    for r in rows:
        if any(x.strip().upper() not in by_name for x in r["MATCHUP"].split("@")):
            problems.append(f"schedule_next.csv: unknown team in {r['MATCHUP']}")
    added = add_playoff_games(cur, rows, season, week, by_name)
    cur.execute("SELECT count(*) FROM games WHERE season = %s AND week = %s", (season, week))
    return {"week": week, "games": cur.fetchone()[0], "added": added}


def load_playoff(cur, folder, season, report):
    """A postseason weekly run: last round's schedule (scores, next round) and every game's box score and lines,
    in one transaction. Season Stats and Team Stats aren't captured (regular season only)."""
    load_weekly(cur, folder, season, report, season_stats=False)
    week = report["week"]
    n = load_games(cur, folder, season, report)
    if report.get("week") != week:
        raise LoadError(f"box scores are from week {report.get('week')}, the schedule from week {week}")
    return n


def next_current_week(folder, week, stored):
    """(league_state.current_week after loading `week`, where it came from). The capture writes
    week.json with the week the League Schedule opened on ('current', Madden's current week) and
    the week it captured; captures made before phase 4 have none and get the loaded week + 1.
    The current week never moves back."""
    path = os.path.join(folder, "week.json")
    if not os.path.exists(path):
        current, source = week + 1, "loaded week + 1, no week.json"
    else:
        with open(path) as fh:
            labels = json.load(fh)
        if db_week(labels.get("captured")) != week:
            raise LoadError(f"week.json says {labels.get('captured')!r} was captured, schedule.csv has week {week}")
        if week == FINAL_WEEK:     # after the Super Bowl Madden is in the offseason: the season ends at 23
            return FINAL_WEEK, f"season over (schedule opened on {labels.get('current')!r})"
        current = db_week(labels.get("current"))
        if current is None or current <= week:
            raise LoadError(f"week.json: current week {labels.get('current')!r} doesn't follow the loaded week {week}")
        source = f"captured label {labels['current']!r}"
    if stored is not None and current < stored:
        return stored, f"kept: {source} gives {current}, league_state already has {stored}"
    return current, source


# ------------------------------------------------------------------- games ----
# team_stats columns the box score screen doesn't show, summed from the team's player lines loaded for the game
TEAM_LINE_SUMS = {"passing_completions": "completions", "passing_attempts": "pass_attempts",
                  "passing_tds": "pass_tds", "rushing_attempts": "rush_attempts", "rushing_tds": "rush_tds"}


def team_line_totals(cur, game_id, team_id):
    """Attempts, completions and TDs for a captured game's team row, from its player_stats lines."""
    cur.execute(f"SELECT {', '.join(f'COALESCE(SUM({src}), 0)' for src in TEAM_LINE_SUMS.values())} "
                f"FROM player_stats WHERE game_id = %s AND team_id = %s", (game_id, team_id))
    return dict(zip(TEAM_LINE_SUMS, cur.fetchone()))


def load_games(cur, folder, season, report, replace=False):
    """A recovery capture of one week's games: box scores -> team_stats, player lines -> player_stats
    (offense) and game_stat_* (other categories), scores -> games."""
    by_name, by_abbr = team_index(cur)
    players = roster(cur, season)
    problems = []
    round_week = report.get("week")          # set when a playoff run loaded the round's schedule first
    scores = read_csv(os.path.join(folder, "game_scores.csv"))
    games = {}
    for r in scores:
        away, home = by_abbr.get(r["AWAY"].upper()), by_abbr.get(r["HOME"].upper())
        cur.execute("SELECT id, week FROM games WHERE season = %s AND away_team_id = %s AND home_team_id = %s",
                    (season, away, home))
        hits = cur.fetchall()
        if len(hits) > 1 and round_week is not None:   # a playoff run knows its round
            hits = [h for h in hits if h[1] == round_week]
        if len(hits) > 1:      # a playoff rematch with the same home team: the game whose stored score matches
            cur.execute("SELECT id, week FROM games WHERE id = ANY(%s) AND status = 'COMPLETED' AND away_score = %s "
                        "AND home_score = %s", ([h[0] for h in hits], num(r["ATOTAL"]), num(r["HTOTAL"])))
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
            scored = (vals.get("red_zone_tds") or 0) + (vals.get("red_zone_fgs") or 0)
            if vals.get("red_zone_pct") and scored:   # trips follow from the percentage (no trips column)
                vals["red_zone_attempts"] = round(scored * 100 / float(vals["red_zone_pct"]))
            vals.update(team_line_totals(cur, g["id"], tid))
            cols = list(vals)
            cur.execute(f"INSERT INTO team_stats (game_id, team_name, {', '.join(cols)}) VALUES (%s, %s, "
                        f"{', '.join(['%s'] * len(cols))})", (g["id"], tname[tid], *[vals[c] for c in cols]))
        # excitement_index NULL: the engines' only_missing pass re-scores the game from the captured box score
        # (a playoff game was scored at its result load, before it had any lines)
        cur.execute("UPDATE games SET away_score = %s, home_score = %s, status = 'COMPLETED', excitement_index = NULL "
                    "WHERE id = %s", (num(r["ATOTAL"]), num(r["HTOTAL"]), g["id"]))
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


def load_prospects(cur, folder, season, report):
    """Validate extracted prospects.csv and sync to data/draft/{season+1}_prospects.csv."""
    import shutil
    from pipeline.ingest_prospects import load_prospects_from_csv

    prospects_path = os.path.join(folder, "prospects.csv")
    if not os.path.exists(prospects_path):
        raise LoadError(f"{folder}: prospects.csv not found")

    try:
        prospects = load_prospects_from_csv(prospects_path)
    except Exception as e:
        raise LoadError(f"prospects validation failed: {e}")

    draft_season = season + 1
    dest = os.path.join("data", "draft", f"{draft_season}_prospects.csv")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if not report.get("dry_run") and os.path.abspath(prospects_path) != os.path.abspath(dest):
        shutil.copyfile(prospects_path, dest)

    report["prospects_loaded"] = len(prospects)
    report["draft_season"] = draft_season
    report["canonical_path"] = dest
    return len(prospects)


LOADERS = {"roster": load_roster, "season_schedule": load_season_schedule, "weekly": load_weekly, "playoff": load_playoff,
           "games": load_games, "injuries": load_injuries_standalone, "prospects": load_prospects}


def load(folder, dry_run=False, replace=False):
    folder = folder.rstrip("/")
    mode = detect_mode(folder)
    if mode not in LOADERS:
        raise LoadError(f"mode {mode} isn't built yet")
    report = {"folder": folder, "mode": mode, "dry_run": dry_run}
    with psycopg.connect(DSN) as conn:
        cur = conn.cursor()
        cur.execute("SELECT current_season, current_week FROM league_state WHERE id = 1")
        season, week = cur.fetchone()
        report["season"] = season
        cid = capture_row(cur, folder, mode, season, None if mode in ("roster", "prospects", "season_schedule") else week, dry_run)
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
