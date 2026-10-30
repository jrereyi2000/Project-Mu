"""
Real NFL targets for tuning (docs/in_progress/MADDEN_PILOT_AND_TUNING_PLAN.md §2.4; JR's D2: 2015-2025).

  python3 -m engines.tuning.targets [--seasons 2015-2025] [--refresh] [--out PATH]

Sources (nflverse releases, downloaded through engines.history_backfill.sources._get and its cache,
data/history/.cache/; --refresh downloads again):
  stats_player/stats_player_reg_<season>.csv   player season lines, regular season
  stats_team/stats_team_reg_<season>.csv       team season lines: yards, sacks, turnovers, games
  schedules/games.csv                          regular-season scores: team points for and against

Each season becomes a flat {key: value} summary (season_summary, shared with engines/tuning/compare.py so a simulated
season is measured the same way):
  <GROUP>.<stat>.rank1|rank5|rank10|rank25   the group's leaders (counting stats: every player in the group;
                                             rate stats: qualifying players only)
  <GROUP>.<stat>.p50|p75|p90                 percentiles over qualifying players (QUALIFY)
  league.<stat>_per_team_game                the league's total over every player / team-games; league.<rate> from
                                             the league's summed lines
  league.points|total_yards|turnovers|team_sacks_per_team_game   from the team lines
  team.<stat>.max|p90|p50|p10|min            the 32 teams' season totals
Counting values in the leader, percentile and team keys are scaled to a 17-game season (x 17 / games per team), so
the 16-game seasons (2015-2020) and partial simulated seasons compare with 17-game ones; qualifying thresholds are on
the scaled values. Per-team-game values and rates aren't scaled.

Position groups (nflverse labels many edge rushers LB and Madden 27 lists them LE/RE, so DL and LB are one FRONT
group): QB, RB (RB, FB), WR, TE, FRONT (DE, DT, NT, DL, LB, OLB, ILB, MLB), DB (CB, S, SAF, FS, SS, DB), DEF (FRONT
and DB), K, P. Total yards are gross passing + rushing yards (Madden's OFFYARDS is the same sum; sack yards aren't
taken off).

The per-season summaries are averaged; the band is the min and max across seasons:
  data/tuning/targets_2015_2025.json  {"meta": {...}, "stats": {key: {mean, lo, hi, n, by_season}}}
"""
import argparse
import csv
import datetime as dt
import json
import math
import os

from engines.history_backfill.sources import NFLVERSE, _get, sum_career
from pipeline.load_save import passer_rating

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data", "tuning")
DEFAULT_SEASONS = range(2015, 2026)
SEASON_GAMES = 17

# group -> the player groups it pools, its stats, and who qualifies for its percentiles (column, minimum at 17 games)
GROUPS = {
    "QB": {"members": ("QB",), "qualify": ("pass_attempts", 224),
           "stats": ("pass_attempts", "completions", "pass_yards", "pass_tds", "interceptions", "pass_sacks",
                     "pass_comp_pct", "pass_ypa", "pass_rating", "rush_yards")},
    "RB": {"members": ("RB",), "qualify": ("rush_attempts", 100),
           "stats": ("rush_attempts", "rush_yards", "rush_tds", "rush_avg", "receptions", "rec_yards")},
    "WR": {"members": ("WR",), "qualify": ("receptions", 30),
           "stats": ("receptions", "rec_yards", "rec_tds", "rec_avg")},
    "TE": {"members": ("TE",), "qualify": ("receptions", 20),
           "stats": ("receptions", "rec_yards", "rec_tds", "rec_avg")},
    "FRONT": {"members": ("FRONT",), "qualify": ("def_tackles", 20),
              "stats": ("def_sacks", "def_tackles", "def_tfl", "def_forced_fumbles")},
    "DB": {"members": ("DB",), "qualify": ("def_tackles", 20),
           "stats": ("def_ints", "def_deflections", "def_tackles")},
    "DEF": {"members": ("FRONT", "DB"), "qualify": ("def_tackles", 20),
            "stats": ("def_tackles", "def_sacks", "def_ints", "def_tfl", "def_forced_fumbles", "def_deflections")},
    "K": {"members": ("K",), "qualify": ("kick_fga", 15),
          "stats": ("kick_fgm", "kick_fga", "kick_fg_pct", "kick_xpm", "kick_xp_pct")},
    "P": {"members": ("P",), "qualify": ("punt_punts", 40),
          "stats": ("punt_punts", "punt_yards", "punt_average", "punt_net_average")},
}
LEADER_RANKS = (1, 5, 10, 25)
PERCENTILES = (50, 75, 90)
TEAM_PERCENTILES = (("max", 100), ("p90", 90), ("p50", 50), ("p10", 10), ("min", 0))

# counting columns (engines.history_backfill.sources CAREER_COLUMNS names, as season_stat_lines uses)
COUNTING = ("completions", "pass_attempts", "pass_yards", "pass_tds", "interceptions", "pass_sacks",
            "rush_attempts", "rush_yards", "rush_tds", "receptions", "rec_yards", "rec_tds",
            "def_solo", "def_assists", "def_tackles", "def_tfl", "def_sacks", "def_ints", "def_deflections",
            "def_forced_fumbles", "def_fumble_recoveries",
            "kick_fgm", "kick_fga", "kick_xpm", "kick_xpa", "punt_punts", "punt_yards", "punt_net_yards",
            "kr_returns", "kr_yards", "pr_returns", "pr_yards")
LEAGUE_COUNTING = ("completions", "pass_attempts", "pass_yards", "pass_tds", "interceptions", "pass_sacks",
                   "rush_attempts", "rush_yards", "rush_tds", "receptions", "rec_yards", "rec_tds",
                   "def_tackles", "def_sacks", "def_ints", "def_tfl", "def_forced_fumbles", "def_deflections",
                   "kick_fgm", "kick_fga", "kick_xpm", "kick_xpa", "punt_punts", "punt_yards",
                   "kr_returns", "kr_yards", "pr_returns", "pr_yards")
TEAM_STATS = ("points", "points_allowed", "total_yards", "pass_yards", "rush_yards", "sacks", "turnovers")
TEAM_PER_GAME = {"points": "points", "total_yards": "total_yards", "turnovers": "turnovers", "sacks": "team_sacks"}


def _ratio(a, b, scale=1.0):
    return scale * a / b if b else None


RATES = {
    "pass_comp_pct": lambda v: _ratio(v["completions"], v["pass_attempts"], 100),
    "pass_ypa": lambda v: _ratio(v["pass_yards"], v["pass_attempts"]),
    "pass_rating": lambda v: passer_rating(v["completions"], v["pass_attempts"], v["pass_yards"], v["pass_tds"],
                                           v["interceptions"]),
    "rush_avg": lambda v: _ratio(v["rush_yards"], v["rush_attempts"]),
    "rec_avg": lambda v: _ratio(v["rec_yards"], v["receptions"]),
    "kick_fg_pct": lambda v: _ratio(v["kick_fgm"], v["kick_fga"], 100),
    "kick_xp_pct": lambda v: _ratio(v["kick_xpm"], v["kick_xpa"], 100),
    "punt_average": lambda v: _ratio(v["punt_yards"], v["punt_punts"]),
    "punt_net_average": lambda v: _ratio(v["punt_net_yards"], v["punt_punts"]),
}

NFLVERSE_GROUPS = {"QB": "QB", "RB": "RB", "FB": "RB", "HB": "RB", "WR": "WR", "TE": "TE",
                   "DE": "FRONT", "DT": "FRONT", "NT": "FRONT", "DL": "FRONT", "EDGE": "FRONT",
                   "LB": "FRONT", "OLB": "FRONT", "ILB": "FRONT", "MLB": "FRONT",
                   "CB": "DB", "S": "DB", "SAF": "DB", "FS": "DB", "SS": "DB", "DB": "DB", "K": "K", "P": "P"}
# schedules/games.csv keeps a relocated team's old abbreviation for its old seasons; stats_team uses today's
NFLVERSE_TEAM_ALIASES = {"STL": "LA", "LAR": "LA", "SD": "LAC", "OAK": "LV"}
NFLVERSE_POSITION_GROUPS = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "DL": "FRONT", "LB": "FRONT",
                            "DB": "DB"}


# ---------- pure: one season's summary ----------

def percentile(values, q):
    """Linear-interpolated percentile (numpy's default) of a list; None when empty."""
    s = sorted(values)
    if not s:
        return None
    k = (len(s) - 1) * q / 100
    lo = math.floor(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _with_rates(v):
    out = dict(v)
    for name, fn in RATES.items():
        out[name] = fn(v)
    return out


def _round(x):
    return None if x is None else round(float(x), 3)


def season_summary(players, teams):
    """players: [{"group": QB|RB|WR|TE|FRONT|DB|K|P|None, <COUNTING columns>}] (one line per
    player-season; group None still counts in the league totals). teams: [{"games": n, <TEAM_STATS>}].
    Returns the flat {key: value} described in the module docstring."""
    stats = {}
    team_games = sum(t["games"] for t in teams)
    games_per_team = team_games / len(teams) if teams else 0
    scale = SEASON_GAMES / games_per_team if games_per_team else 1.0

    pools = {}
    totals = {c: 0.0 for c in COUNTING}
    for p in players:
        for c in COUNTING:
            totals[c] += p.get(c) or 0
        if p.get("group") is None:
            continue
        scaled = _with_rates({c: (p.get(c) or 0) * scale for c in COUNTING})
        pools.setdefault(p["group"], []).append(scaled)

    for name, spec in GROUPS.items():
        rows = [r for m in spec["members"] for r in pools.get(m, [])]
        col, minimum = spec["qualify"]
        qualified = [r for r in rows if r[col] >= minimum]
        for stat in spec["stats"]:
            pool = qualified if stat in RATES else rows
            vals = sorted((r[stat] for r in pool if r[stat] is not None), reverse=True)
            for rank in LEADER_RANKS:
                if len(vals) >= rank:
                    stats[f"{name}.{stat}.rank{rank}"] = _round(vals[rank - 1])
            qvals = [r[stat] for r in qualified if r[stat] is not None]
            if qvals:
                for q in PERCENTILES:
                    stats[f"{name}.{stat}.p{q}"] = _round(percentile(qvals, q))

    if team_games and players:      # no player lines (Madden links a week's lines late): no player league totals
        for c in LEAGUE_COUNTING:
            stats[f"league.{c}_per_team_game"] = _round(totals[c] / team_games)
        for name, fn in RATES.items():
            v = fn(totals)
            if v is not None:
                stats[f"league.{name}"] = _round(v)
    if team_games:
        for col, key in TEAM_PER_GAME.items():
            vals = [t[col] for t in teams if t.get(col) is not None]
            if vals:
                stats[f"league.{key}_per_team_game"] = _round(sum(vals) / sum(t["games"] for t in teams
                                                                                 if t.get(col) is not None))
    for col in TEAM_STATS:
        vals = [t[col] * SEASON_GAMES / t["games"] for t in teams if t.get(col) is not None and t["games"]]
        if vals:
            for label, q in TEAM_PERCENTILES:
                stats[f"team.{col}.{label}"] = _round(percentile(vals, q))
    return stats


def average_seasons(per_season):
    """{season: {key: value}} -> {key: {mean, lo, hi, n, by_season}} over the seasons that have the key."""
    keys = sorted({k for s in per_season.values() for k in s})
    out = {}
    for k in keys:
        by = {str(y): s[k] for y, s in sorted(per_season.items()) if s.get(k) is not None}
        if not by:
            continue
        vals = list(by.values())
        out[k] = {"mean": _round(sum(vals) / len(vals)), "lo": min(vals), "hi": max(vals), "n": len(vals),
                  "by_season": by}
    return out


# ---------- nflverse rows -> season_summary inputs ----------

def _num(x):
    try:
        return float(x) if x not in (None, "") else 0.0
    except ValueError:
        return 0.0


def nflverse_group(row):
    pos = (row.get("position") or "").upper()
    return NFLVERSE_GROUPS.get(pos) or NFLVERSE_POSITION_GROUPS.get((row.get("position_group") or "").upper())


def nflverse_players(rows):
    """stats_player rows (one season, regular season) -> season_summary players."""
    out = []
    for r in rows:
        if r.get("season_type", "REG") != "REG":
            continue
        line = sum_career([r])
        line = {c: float(line.get(c) or 0) for c in COUNTING}
        line["group"] = nflverse_group(r)
        out.append(line)
    return out


def nflverse_teams(team_rows, schedule_rows, season):
    """stats_team rows + schedules/games.csv rows -> season_summary teams (points from the season's REG scores)."""
    pts, allowed, games = {}, {}, {}
    for g in schedule_rows:
        if str(g.get("season")) != str(season) or g.get("game_type") != "REG":
            continue
        if g.get("home_score") in (None, "") or g.get("away_score") in (None, ""):
            continue
        for side, us, them in (("home_team", "home_score", "away_score"), ("away_team", "away_score", "home_score")):
            team = NFLVERSE_TEAM_ALIASES.get(g[side], g[side])
            pts[team] = pts.get(team, 0) + _num(g[us])
            allowed[team] = allowed.get(team, 0) + _num(g[them])
            games[team] = games.get(team, 0) + 1
    out = []
    for r in team_rows:
        if r.get("season_type", "REG") != "REG" or str(r.get("season", season)) != str(season):
            continue
        team = NFLVERSE_TEAM_ALIASES.get(r["team"], r["team"])
        out.append({
            "team": team, "games": _num(r.get("games")) or games.get(team, 0),
            "points": pts.get(team), "points_allowed": allowed.get(team),
            "pass_yards": _num(r.get("passing_yards")), "rush_yards": _num(r.get("rushing_yards")),
            "total_yards": _num(r.get("passing_yards")) + _num(r.get("rushing_yards")),
            "sacks": _num(r.get("def_sacks")),
            "turnovers": _num(r.get("passing_interceptions")) + _num(r.get("fumbles_lost_total")),
        })
    return out


# ---------- download + build ----------

def _urls(season):
    return {"players": f"{NFLVERSE}/stats_player/stats_player_reg_{season}.csv",
            "teams": f"{NFLVERSE}/stats_team/stats_team_reg_{season}.csv"}


SCHEDULE_URL = f"{NFLVERSE}/schedules/games.csv"


def _csv(url, refresh):
    with open(_get(url, "nflverse_" + url.rsplit("/", 1)[1], refresh), newline="") as f:
        return list(csv.DictReader(f))


def build_targets(seasons=DEFAULT_SEASONS, refresh=False):
    seasons = list(seasons)
    schedule = _csv(SCHEDULE_URL, refresh)
    per_season, season_meta = {}, {}
    for y in seasons:
        urls = _urls(y)
        players = nflverse_players(_csv(urls["players"], refresh))
        teams = nflverse_teams(_csv(urls["teams"], refresh), schedule, y)
        per_season[y] = season_summary(players, teams)
        season_meta[str(y)] = {"players": len(players), "teams": len(teams),
                               "games_per_team": _round(sum(t["games"] for t in teams) / len(teams)) if teams else 0,
                               "teams_without_points": sorted(t["team"] for t in teams if t["points"] is None)}
    return {
        "meta": {
            "seasons": seasons,
            "built_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "sources": {"players": _urls("<season>")["players"], "teams": _urls("<season>")["teams"],
                        "points": SCHEDULE_URL},
            "team_source": "nflverse stats_team (yards, sacks, turnovers = interceptions + fumbles_lost_total) and "
                           "schedules/games.csv REG scores (points)",
            "season_games": SEASON_GAMES,
            "scaling": "counting values in leader, percentile and team keys scaled to 17 games per team",
            "thresholds": {g: {"column": s["qualify"][0], "min": s["qualify"][1]} for g, s in GROUPS.items()},
            "groups": {g: list(s["members"]) for g, s in GROUPS.items()},
            "band": "lo/hi = min/max of the per-season values; mean = their average",
            "per_season": season_meta,
        },
        "stats": average_seasons(per_season),
    }


def default_path(seasons=DEFAULT_SEASONS):
    seasons = list(seasons)
    return os.path.normpath(os.path.join(OUT_DIR, f"targets_{seasons[0]}_{seasons[-1]}.json"))


def parse_seasons(text):
    """'2015-2025' or '2019,2021,2023'."""
    if "-" in text:
        a, b = text.split("-", 1)
        return range(int(a), int(b) + 1)
    return [int(x) for x in text.split(",") if x.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build real-NFL tuning targets from nflverse.")
    ap.add_argument("--seasons", default="2015-2025", help="e.g. 2015-2025 or 2019,2021")
    ap.add_argument("--refresh", action="store_true", help="download the nflverse files again")
    ap.add_argument("--out", help="output JSON (default data/tuning/targets_<first>_<last>.json)")
    args = ap.parse_args(argv)
    seasons = list(parse_seasons(args.seasons))
    targets = build_targets(seasons, refresh=args.refresh)
    out = args.out or default_path(seasons)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as f:
        json.dump(targets, f, indent=1)
    print(f"{len(targets['stats'])} target stats over {len(seasons)} seasons -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
