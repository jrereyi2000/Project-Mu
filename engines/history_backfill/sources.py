"""
Phase 0 inputs for the 2026-2034 history backfill (docs/HISTORY_BACKFILL_PLAN.md §3.1-§3.2; PROJECT_SPEC §1.2).

Three files under data/history/, each rebuilt by one command and read by later phases (never by the live site):

  python3 -m engines.history_backfill.sources start-2026    # start_2026.csv: projected end of the real 2026-27 season
  python3 -m engines.history_backfill.sources rosters-2026  # rosters_2026.csv: real 2026 rosters, matched to `players`
  python3 -m engines.history_backfill.sources nfl-careers   # nfl_careers_through_2025.csv: real careers through 2025

- start-2026 (point A, plan B3): ESPN's standings and every team's 2026 schedule. Each team's rating is its point
  differential per game shrunk toward zero (PD / (G + SHRINK_GAMES)); each unplayed game is won with probability
  Phi((r_team - r_opp +/- HOME_EDGE) / GAME_SD), so projected wins = wins so far + the sum of those probabilities.
  Points for/against add the remaining games at the team's scoring rate shrunk toward the league's.
- rosters-2026: ESPN's current rosters (the real 2026 league). Each player is matched to `players` by espn_id, else by
  a unique full name; in_pool_2035 / in_pool_2036 say whether the matched player has a player_seasons row then. Real
  veterans on these rosters who are in neither pool retired during 2026-2034 (plan §3.2).
- nfl-careers: nflverse's regular-season player stats (the K5 subtraction: Madden's career totals include real NFL
  stats through 2025 and nothing from the real 2026 season), summed per player over 2016-2025 and renamed to the
  season_stat_lines columns. Covers every rosters-2026 player and every 2035/2036 player with an espn_id.

The pure parts (projection(), sum_career()) take parsed rows; fetch_*() do the HTTP. Downloads are cached in
data/history/.cache/ (git-ignored); --refresh downloads again.
"""
import argparse
import csv
import datetime as dt
import json
import math
import os
import urllib.request

import psycopg

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "history")
CACHE_DIR = os.path.join(OUT_DIR, ".cache")

ESPN = "https://site.api.espn.com/apis"
NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
REAL_SEASON = 2026
CAREER_SEASONS = range(2016, 2026)      # nobody active in 2036 played before 2016 (max 14 years of experience)
ESPN_TO_MU = {"ARI": "AZ", "WSH": "WAS"}  # ESPN abbreviations that differ from teams.abbreviation
GAMES = 17

SHRINK_GAMES = 4      # a team's point differential per game counts as if it had played this many more 0-margin games
HOME_EDGE = 1.5       # points
GAME_SD = 13.5        # standard deviation of an NFL game margin around the rating difference
PPG_SHRINK_GAMES = 4

# nflverse stats_player column -> season_stat_lines column (sums only; longs, averages and rates aren't summable)
CAREER_COLUMNS = {
    "games": "games_played",
    "completions": "completions", "attempts": "pass_attempts", "passing_yards": "pass_yards",
    "passing_tds": "pass_tds", "passing_interceptions": "interceptions", "sacks_suffered": "pass_sacks",
    "carries": "rush_attempts", "rushing_yards": "rush_yards", "rushing_tds": "rush_tds",
    "fumbles_total": "fumb",
    "receptions": "receptions", "receiving_yards": "rec_yards", "receiving_tds": "rec_tds",
    "def_tackles_solo": "def_solo", "def_tackle_assists": "def_assists", "def_tackles_for_loss": "def_tfl",
    "def_sacks": "def_sacks", "def_interceptions": "def_ints", "def_interception_yards": "def_int_yds",
    "def_pass_defended": "def_deflections", "def_fumbles_forced": "def_forced_fumbles",
    "fumble_recovery_opp": "def_fumble_recoveries", "fumble_recovery_yards_opp": "def_fumble_yds",
    "def_safeties": "def_safeties", "def_tds": "def_tds",
    "fg_made": "kick_fgm", "fg_att": "kick_fga", "fg_blocked": "kick_fg_blocked",
    "pat_made": "kick_xpm", "pat_att": "kick_xpa", "pat_blocked": "kick_xp_blocked",
    "pt_att": "punt_punts", "pt_yards": "punt_yards", "pt_net_yards": "punt_net_yards",
    "pt_inside_20": "punt_inside_20", "pt_touchback": "punt_touchbacks", "pt_blocked": "punt_blocked",
    "kickoff_returns": "kr_returns", "kickoff_return_yards": "kr_yards",
    "punt_returns": "pr_returns", "punt_return_yards": "pr_yards",
}
# Madden's field-goal range columns: (made columns, missed columns) per range
FG_RANGES = {
    "29": (("fg_made_0_19", "fg_made_20_29"), ("fg_missed_0_19", "fg_missed_20_29")),
    "39": (("fg_made_30_39",), ("fg_missed_30_39",)),
    "49": (("fg_made_40_49",), ("fg_missed_40_49",)),
    "50": (("fg_made_50_59", "fg_made_60_"), ("fg_missed_50_59", "fg_missed_60_")),
}
CAREER_FIELDS = (["espn_id", "gsis_id", "player_id", "full_name", "first_season", "last_season", "seasons"]
                 + list(CAREER_COLUMNS.values()) + ["def_tackles"]
                 + [f"kick_{k}_{r}" for r in FG_RANGES for k in ("fgm", "fga")])


# ---------- HTTP ----------

def _get(url, cache_name, refresh=False):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, cache_name)
    if refresh or not os.path.exists(path):
        req = urllib.request.Request(url, headers={"User-Agent": "project-mu-history-backfill"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        with open(path, "wb") as f:
            f.write(data)
    return path


def _json(url, cache_name, refresh=False):
    with open(_get(url, cache_name, refresh)) as f:
        return json.load(f)


def _mu(abbr):
    return ESPN_TO_MU.get(abbr, abbr)


def fetch_standings(refresh=False):
    """{espn_team_id: {abbr, wins, losses, ties, points_for, points_against}} for the real 2026 season."""
    d = _json(f"{ESPN}/v2/sports/football/nfl/standings?season={REAL_SEASON}&seasontype=2",
              "espn_standings_2026.json", refresh)
    teams = {}
    for conf in d["children"]:
        for e in conf["standings"]["entries"]:
            s = {x["name"]: x.get("value") for x in e["stats"]}
            teams[e["team"]["id"]] = {
                "abbr": _mu(e["team"]["abbreviation"]),
                "wins": int(s.get("wins") or 0), "losses": int(s.get("losses") or 0), "ties": int(s.get("ties") or 0),
                "points_for": int(s.get("pointsFor") or 0), "points_against": int(s.get("pointsAgainst") or 0)}
    return teams


def fetch_schedule(espn_team_id, refresh=False):
    """[{week, home, away, final}] for one team's 2026 regular season (abbreviations as in teams.abbreviation)."""
    d = _json(f"{ESPN}/site/v2/sports/football/nfl/teams/{espn_team_id}/schedule?season={REAL_SEASON}&seasontype=2",
              f"espn_schedule_2026_{espn_team_id}.json", refresh)
    games = []
    for e in d.get("events", []):
        c = e["competitions"][0]
        side = {t["homeAway"]: _mu(t["team"]["abbreviation"]) for t in c["competitors"]}
        games.append({"week": e["week"]["number"], "home": side["home"], "away": side["away"],
                      "final": c["status"]["type"].get("completed", False)})
    return games


def fetch_roster(espn_team_id, refresh=False):
    d = _json(f"{ESPN}/site/v2/sports/football/nfl/teams/{espn_team_id}/roster",
              f"espn_roster_{espn_team_id}.json", refresh)
    abbr = _mu(d["team"]["abbreviation"])
    out = []
    for group in d.get("athletes", []):
        for a in group.get("items", []):
            out.append({"espn_id": a["id"], "full_name": a.get("fullName"), "team": abbr,
                        "position": (a.get("position") or {}).get("abbreviation"),
                        "age": a.get("age"), "experience_years": (a.get("experience") or {}).get("years"),
                        "birth_date": (a.get("dateOfBirth") or "")[:10]})
    return out


# ---------- start-2026 ----------

def _phi(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def projection(standings, schedules):
    """standings: {abbr: {wins, losses, ties, points_for, points_against}}; schedules: {abbr: [game]}.
    Returns one row per team with its rating and projected final record and points (pure)."""
    played = {a: s["wins"] + s["losses"] + s["ties"] for a, s in standings.items()}
    rating = {a: (s["points_for"] - s["points_against"]) / (played[a] + SHRINK_GAMES) for a, s in standings.items()}
    total_g = sum(played.values()) or 1
    league_ppg = sum(s["points_for"] for s in standings.values()) / total_g
    rows = []
    for a, s in standings.items():
        g = played[a]
        remaining = [x for x in schedules.get(a, []) if not x["final"]]
        exp_w = 0.0
        for x in remaining:
            home = x["home"] == a
            opp = x["away"] if home else x["home"]
            exp_w += _phi((rating[a] - rating.get(opp, 0.0) + (HOME_EDGE if home else -HOME_EDGE)) / GAME_SD)
        n = len(remaining)
        pf_rate = (s["points_for"] + PPG_SHRINK_GAMES * league_ppg) / (g + PPG_SHRINK_GAMES)
        pa_rate = (s["points_against"] + PPG_SHRINK_GAMES * league_ppg) / (g + PPG_SHRINK_GAMES)
        rows.append({
            "team": a, "games_played": g, "wins": s["wins"], "losses": s["losses"], "ties": s["ties"],
            "points_for": s["points_for"], "points_against": s["points_against"],
            "rating": round(rating[a], 3), "games_left": n,
            "proj_wins": round(s["wins"] + exp_w, 2), "proj_losses": round(s["losses"] + n - exp_w, 2),
            "proj_points_for": round(s["points_for"] + n * pf_rate), "proj_points_against": round(s["points_against"] + n * pa_rate),
        })
    rows.sort(key=lambda r: -r["proj_wins"])
    return rows


def build_start_2026(refresh=False):
    raw = fetch_standings(refresh)
    standings = {v["abbr"]: v for v in raw.values()}
    schedules = {raw[tid]["abbr"]: fetch_schedule(tid, refresh) for tid in raw}
    rows = projection(standings, schedules)
    as_of = dt.date.today().isoformat()
    for r in rows:
        r["as_of"] = as_of
    _write("start_2026.csv", rows)
    return rows


# ---------- rosters-2026 ----------

def _pool(cur):
    """players (id, full_name, espn_id), {player_id: {2035, 2036}} and {player_id: years_of_experience in 2036 terms}."""
    cur.execute("SELECT id, full_name, espn_id FROM players")
    players = cur.fetchall()
    cur.execute("SELECT player_id, season, years_of_experience FROM player_seasons WHERE season IN (2035, 2036)")
    seasons, exp36 = {}, {}
    for pid, season, exp in cur.fetchall():
        seasons.setdefault(pid, set()).add(season)
        if exp is not None:
            exp36[pid] = max(exp36.get(pid, 0), exp + (2036 - season))
    return players, seasons, exp36


def plausible_veteran(exp_2036, exp_2026):
    """A real 2026 player has at least 10 more years of experience by 2036 (one missed season allowed). A name match
    that fails this is a fictional namesake, not the same player."""
    if exp_2036 is None:
        return True
    return exp_2036 >= (exp_2026 or 0) + 9


def build_rosters_2026(refresh=False):
    raw = fetch_standings(refresh)
    with psycopg.connect(CONN_INFO) as conn, conn.cursor() as cur:
        players, seasons, exp36 = _pool(cur)
    by_espn = {str(e): pid for pid, _, e in players if e}
    by_name = {}
    for pid, name, _ in players:
        by_name.setdefault((name or "").lower(), []).append(pid)
    rows = []
    for tid in raw:
        for a in fetch_roster(tid, refresh):
            pid, how = by_espn.get(a["espn_id"]), "espn_id"
            if pid is not None and not plausible_veteran(exp36.get(pid), a["experience_years"]):
                pid, how = None, "espn_id_conflict"   # a fictional player whose stored espn_id is a real namesake's
            if pid is None and how == "espn_id":
                cands = [p for p in by_name.get((a["full_name"] or "").lower(), [])
                         if plausible_veteran(exp36.get(p), a["experience_years"])]
                pid, how = (cands[0], "name") if len(cands) == 1 else (None, "ambiguous" if cands else "none")
            s = seasons.get(pid, set())
            rows.append({**a, "player_id": pid, "match": how,
                         "in_pool_2035": 2035 in s, "in_pool_2036": 2036 in s})
    rows.sort(key=lambda r: (r["team"], r["full_name"] or ""))
    _write("rosters_2026.csv", rows)
    return rows


# ---------- nfl-careers ----------

def _num(v):
    try:
        return float(v) if v not in ("", "NA", None) else 0.0
    except ValueError:
        return 0.0


def sum_career(stat_rows):
    """stat_rows: nflverse stats_player rows for one player, any seasons. Returns the summed season_stat_lines
    columns plus first/last season and the number of seasons (pure)."""
    out = {col: 0.0 for col in CAREER_COLUMNS.values()}
    for r in (f"kick_{k}_{rng}" for rng in FG_RANGES for k in ("fgm", "fga")):
        out[r] = 0.0
    seasons = set()
    for row in stat_rows:
        seasons.add(int(row["season"]))
        for src, col in CAREER_COLUMNS.items():
            out[col] += _num(row.get(src))
        for rng, (made, missed) in FG_RANGES.items():
            m = sum(_num(row.get(c)) for c in made)
            out[f"kick_fgm_{rng}"] += m
            out[f"kick_fga_{rng}"] += m + sum(_num(row.get(c)) for c in missed)
    out["def_tackles"] = out["def_solo"] + out["def_assists"]
    out = {k: (int(v) if float(v).is_integer() else round(v, 1)) for k, v in out.items()}
    out["first_season"] = min(seasons) if seasons else None
    out["last_season"] = max(seasons) if seasons else None
    out["seasons"] = len(seasons)
    return out


def build_nfl_careers(refresh=False):
    roster_path = os.path.join(OUT_DIR, "rosters_2026.csv")
    if not os.path.exists(roster_path):
        raise SystemExit("rosters_2026.csv is missing: run rosters-2026 first")
    wanted = {}   # espn_id -> (player_id, full_name)
    with open(roster_path) as f:
        for r in csv.DictReader(f):
            wanted[r["espn_id"]] = (r["player_id"] or None, r["full_name"])
    with psycopg.connect(CONN_INFO) as conn, conn.cursor() as cur:
        players, seasons, exp36 = _pool(cur)
    for pid, name, espn in players:
        # under 10 years of experience in 2036 = entered the league after 2026, so no real NFL career
        if espn and pid in seasons and exp36.get(pid, 10) >= 10:
            prev = wanted.get(str(espn))
            wanted[str(espn)] = (pid, prev[1] if prev else name)
    with open(_get(f"{NFLVERSE}/players/players.csv", "nflverse_players.csv", refresh)) as f:
        nfl_players = list(csv.DictReader(f))
    gsis_by_espn = {r["espn_id"]: r["gsis_id"] for r in nfl_players if r["espn_id"] and r["gsis_id"]}
    # A 2036 veteran (10+ years) with no espn_id and no 2026 roster row: match nflverse by a unique display name
    # among players whose rookie season is 2025 or earlier.
    covered = {str(p) for p, _ in wanted.values() if p}
    by_name = {}
    for r in nfl_players:
        if r["gsis_id"] and r["rookie_season"] and int(r["rookie_season"]) <= 2025:
            by_name.setdefault(r["display_name"].lower(), []).append(r["gsis_id"])
    extra = {}   # gsis_id -> (player_id, full_name)
    for pid, name, _ in players:
        if str(pid) in covered or exp36.get(pid, 0) < 10:
            continue
        cands = by_name.get((name or "").lower(), [])
        if len(cands) == 1:
            extra[cands[0]] = (pid, name)
    stats = {}
    for season in CAREER_SEASONS:
        name = f"stats_player_reg_{season}.csv"
        with open(_get(f"{NFLVERSE}/stats_player/{name}", f"nflverse_{name}", refresh)) as f:
            for r in csv.DictReader(f):
                stats.setdefault(r["player_id"], []).append(r)
    rows = []
    for espn, (pid, name) in wanted.items():
        gsis = gsis_by_espn.get(espn)
        lines = stats.get(gsis, []) if gsis else []
        if not lines:
            continue
        rows.append({"espn_id": espn, "gsis_id": gsis, "player_id": pid, "full_name": name, **sum_career(lines)})
    for gsis, (pid, name) in extra.items():
        if stats.get(gsis):
            rows.append({"espn_id": None, "gsis_id": gsis, "player_id": pid, "full_name": name, **sum_career(stats[gsis])})
    rows.sort(key=lambda r: (r["full_name"] or ""))
    _write("nfl_careers_through_2025.csv", rows, CAREER_FIELDS)
    return rows


# ---------- CLI ----------

def _write(name, rows, fields=None):
    os.makedirs(OUT_DIR, exist_ok=True)
    fields = fields or (list(rows[0].keys()) if rows else [])
    with open(os.path.join(OUT_DIR, name), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote data/history/{name}: {len(rows)} rows")


def main():
    ap = argparse.ArgumentParser(description="Build the history backfill's phase 0 input files (data/history/).")
    ap.add_argument("what", choices=["start-2026", "rosters-2026", "nfl-careers", "all"])
    ap.add_argument("--refresh", action="store_true", help="download again instead of using data/history/.cache/")
    a = ap.parse_args()
    if a.what in ("start-2026", "all"):
        build_start_2026(a.refresh)
    if a.what in ("rosters-2026", "all"):
        build_rosters_2026(a.refresh)
    if a.what in ("nfl-careers", "all"):
        build_nfl_careers(a.refresh)


if __name__ == "__main__":
    main()
