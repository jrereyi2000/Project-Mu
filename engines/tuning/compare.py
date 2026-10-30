"""
Scores a simulated season against the real-NFL targets (docs/in_progress/MADDEN_PILOT_AND_TUNING_PLAN.md §2.4).

  python3 -m engines.tuning.compare <snapshot.json> [--season N] [--targets PATH] [--out report.md]

season_from_snapshot() reads one season of a pc_save_reader snapshot (pipeline/save_reader/pc_save_reader.mjs) into
the same flat summary engines/tuning/targets.py builds from nflverse (targets.season_summary):
  players  every player's season_stats rows for that franchise year (SEAS_YEAR), grouped and renamed by
           pipeline.load_save.group_lines (the columns load_save writes), prefixed like season_stat_lines
           (def_, kick_, punt_, kr_, pr_); position group from the player's Madden position
  teams    the season's completed regular-season games: points from the scores, yards / sacks / giveaways from each
           side's team box score (OFFPASSYARDS, OFFRUSHYARDS, SACKS, GIVEAWAYS)
--season is a calendar year and defaults to the snapshot's season.calendar_year.

compare() scores each target stat the simulated season has: 0 inside the real band [lo, hi], else the distance
outside it divided by the band width. The width is floored at max(5% of |mean|, the stat's resolution) so a zero-width
band doesn't divide by 0: the resolution is 1 for a counting stat's leader, percentile or team-total key (a whole
number in the save: a 0-0 band for forced fumbles' p50 scores a 1 as one width outside, not 100) and 0.01 otherwise
(rates, per-team-game averages). The overall score is the mean over the scored stats.
"""
import argparse
import json
import os
import sys
from dataclasses import dataclass, field

from engines.tuning import targets as T
from pipeline.load_save import FINISHED, group_lines

WIDTH_FLOOR_FRAC = 0.05
WIDTH_FLOOR = 0.01          # rates and per-team-game averages
COUNT_FLOOR = 1.0           # whole-number keys: a counting stat's leaders / percentiles, team season totals

# Madden 27 positions -> targets' position groups (OL and LS have no stat group)
MADDEN_GROUPS = {"QB": "QB", "HB": "RB", "FB": "RB", "WR": "WR", "TE": "TE",
                 "LE": "FRONT", "RE": "FRONT", "DT": "FRONT", "LOLB": "FRONT", "MLB": "FRONT", "ROLB": "FRONT",
                 "CB": "DB", "FS": "DB", "SS": "DB", "K": "K", "P": "P"}
# load_save.GROUPS name -> the prefix that gives the season_stat_lines column names targets.py uses
PREFIXES = {"offense": "", "defense": "def_", "kicking": "kick_", "punting": "punt_", "kickreturn": "kr_",
            "puntreturn": "pr_"}


def _player_line(rows):
    line = {c: 0 for c in T.COUNTING}
    for group, vals in group_lines(rows).items():
        prefix = PREFIXES.get(group)
        if prefix is None:
            continue
        for k, v in vals.items():
            col = prefix + k
            if col in line and v is not None:
                line[col] += v
    return line


def season_from_snapshot(snapshot, season=None):
    """{"meta": {...}, "stats": {key: value}} for one season of a pc_save_reader snapshot. A season with no completed
    regular-season games gives meta.no_games = True and empty stats."""
    s = snapshot["season"]
    base = s["calendar_year"] - s["franchise_year"]
    season = season or s["calendar_year"]
    year = season - base
    teams, box_missing = {}, 0
    for g in snapshot.get("games", []):
        if g.get("franchise_year") != year or g.get("week_type") != "RegularSeason" or g.get("status") not in FINISHED:
            continue
        for side, other in (("home", "away"), ("away", "home")):
            idx = g.get(f"{side}_team_index")
            if idx is None:
                continue
            t = teams.setdefault(idx, {"team": idx, "games": 0, "points": 0, "points_allowed": 0, "pass_yards": 0,
                                       "rush_yards": 0, "total_yards": 0, "sacks": 0, "turnovers": 0})
            t["games"] += 1
            t["points"] += g.get(f"{side}_score") or 0
            t["points_allowed"] += g.get(f"{other}_score") or 0
            box = g.get(f"{side}_team_stats")
            if not box:
                box_missing += 1
                continue
            t["pass_yards"] += box.get("OFFPASSYARDS", 0)
            t["rush_yards"] += box.get("OFFRUSHYARDS", 0)
            t["total_yards"] += box.get("OFFPASSYARDS", 0) + box.get("OFFRUSHYARDS", 0)
            t["sacks"] += box.get("SACKS", 0)
            t["turnovers"] += box.get("GIVEAWAYS", 0)
    team_games = sum(t["games"] for t in teams.values())
    meta = {"season": season, "franchise_year": year, "games": team_games // 2, "teams": len(teams),
            "games_per_team": round(team_games / len(teams), 3) if teams else 0, "team_boxes_missing": box_missing,
            "no_games": not team_games}
    if not team_games:
        meta["players"] = 0
        return {"meta": meta, "stats": {}}
    players = []
    for p in snapshot.get("players", []):
        rows = [r for r in p.get("season_stats") or [] if r.get("stats", {}).get("SEAS_YEAR") == year]
        if not rows:
            continue
        line = _player_line(rows)
        line["group"] = MADDEN_GROUPS.get(p.get("position"))
        players.append(line)
    meta["players"] = len(players)
    return {"meta": meta, "stats": T.season_summary(players, list(teams.values()))}


@dataclass
class StatScore:
    key: str
    sim: float
    lo: float
    mean: float
    hi: float
    score: float


@dataclass
class Report:
    score: float | None
    stats: list = field(default_factory=list)       # [StatScore], in key order
    missing: list = field(default_factory=list)     # target keys the simulated season doesn't have
    meta: dict = field(default_factory=dict)        # the sim's meta plus the targets' seasons

    @property
    def no_games(self):
        return bool(self.meta.get("no_games"))

    def worst(self, n=10):
        return sorted(self.stats, key=lambda s: (-s.score, s.key))[:n]


def resolution(key):
    """The smallest meaningful width for a key: 1 for a counting stat's leader / percentile / team-total key."""
    section, rest = key.split(".", 1)
    if section == "team":
        return COUNT_FLOOR
    if section != "league" and rest.split(".", 1)[0] in T.COUNTING:
        return COUNT_FLOOR
    return WIDTH_FLOOR


def band_score(value, lo, mean, hi, floor=WIDTH_FLOOR):
    """0 inside [lo, hi]; else the distance outside over the band width (floored)."""
    if lo <= value <= hi:
        return 0.0
    width = max(hi - lo, WIDTH_FLOOR_FRAC * abs(mean), floor)
    return (lo - value if value < lo else value - hi) / width


def compare(sim, targets):
    meta = dict(sim.get("meta", {}))
    meta["target_seasons"] = targets.get("meta", {}).get("seasons")
    stats, missing = [], []
    for key, band in sorted(targets["stats"].items()):
        v = sim.get("stats", {}).get(key)
        if v is None:
            missing.append(key)
            continue
        stats.append(StatScore(key, v, band["lo"], band["mean"], band["hi"],
                               round(band_score(v, band["lo"], band["mean"], band["hi"], resolution(key)), 3)))
    score = round(sum(s.score for s in stats) / len(stats), 3) if stats else None
    return Report(score=score, stats=stats, missing=missing if stats else [], meta=meta)


def _fmt(x):
    if x is None:
        return "-"
    return f"{x:,.0f}" if abs(x) >= 1000 else f"{x:.3g}" if abs(x) < 10 else f"{x:.1f}"


def _section(key):
    return key.split(".", 1)[0]


def format_report(report, n=10):
    m = report.meta
    seasons = m.get("target_seasons") or []
    band = f"{seasons[0]}–{seasons[-1]}" if seasons else "target"
    title = f"# Tuning comparison: season {m.get('season')}"
    if report.no_games:
        return (f"{title}\n\nNo games played in season {m.get('season')} (franchise year {m.get('franchise_year')}): "
                "nothing to compare.\n")
    row = lambda s: f"| `{s.key}` | {_fmt(s.sim)} | {_fmt(s.lo)} | {_fmt(s.mean)} | {_fmt(s.hi)} | {s.score:.2f} |"
    head = "| stat | sim | lo | mean | hi | score |\n|---|---|---|---|---|---|"
    inside = sum(1 for s in report.stats if s.score == 0)
    lines = [title, "",
             f"- Games: {m.get('games')} ({m.get('games_per_team')} per team, {m.get('teams')} teams); "
             f"players with lines: {m.get('players')}",
             f"- **Score: {report.score:.3f}** (mean over {len(report.stats)} stats; 0 = inside the real {band} band; "
             f"1 = one band width outside). {inside} of {len(report.stats)} stats are inside the band.",
             ""]
    if not m.get("players"):
        lines += ["- No player has a season line for this season yet (Madden links a week's player lines late), so "
                  "only the team stats are scored.", ""]
    if m.get("team_boxes_missing"):
        lines += [f"- Team box scores missing for {m['team_boxes_missing']} team-games (their yards, sacks and "
                  "turnovers count as 0).", ""]
    lines += [f"## Worst {n}", "", head] + [row(s) for s in report.worst(n)]
    sections = {}
    for s in report.stats:
        sections.setdefault(_section(s.key), []).append(s.score)
    lines += ["", "## By section", "", "| section | stats | inside | mean score |", "|---|---|---|---|"]
    for name, scores in sections.items():
        lines.append(f"| {name} | {len(scores)} | {sum(1 for x in scores if x == 0)} | "
                     f"{sum(scores) / len(scores):.3f} |")
    lines += ["", "## All stats", "", head] + [row(s) for s in report.stats]
    if report.missing:
        lines += ["", f"Not in the simulated season ({len(report.missing)}): "
                  + ", ".join(f"`{k}`" for k in report.missing)]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description="Score a simulated season (pc_save_reader snapshot) against real-NFL "
                                             "targets.")
    ap.add_argument("snapshot", help="pc_save_reader.mjs output JSON")
    ap.add_argument("--season", type=int, help="calendar year (default: the snapshot's season.calendar_year)")
    ap.add_argument("--targets", default=T.default_path(), help="targets JSON (engines.tuning.targets)")
    ap.add_argument("--out", help="also write the markdown report here")
    args = ap.parse_args(argv)
    if not os.path.exists(args.targets):
        print(f"no targets at {args.targets}: build them with python3 -m engines.tuning.targets", file=sys.stderr)
        return 2
    with open(args.snapshot) as f:
        snapshot = json.load(f)
    with open(args.targets) as f:
        targets = json.load(f)
    text = format_report(compare(season_from_snapshot(snapshot, args.season), targets))
    print(text, end="")
    if args.out:
        with open(args.out, "w") as f:
            f.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
