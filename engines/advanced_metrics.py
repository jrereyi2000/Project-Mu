"""Advanced player metrics over Madden's stored counters (MADDEN_STATS_INGEST_PLAN U3).

Every derived metric in `docs/reference/MADDEN_STATS_AND_ADVANCED_METRICS.md` §4 is one `Metric` in
`METRICS`, with a status copied from the plan's §2.5:

- OK: computable now, and `compute()` returns it.
- BLOCKED: a counter's meaning is unresolved (`ROUTESRUN` is not routes run; spike S22), so the doc metric
  stays uncomputed until the question is answered.
- IMPOSSIBLE: Madden does not record the inputs (no per-receiver air yards, no pass-rush/run-block snap
  splits, counters that never fill). BLOCKED and IMPOSSIBLE metrics are never computed and carry a reason.

The module holds no numbers of its own. The counters come from `player_season_counters` /
`player_week_counters` (typed Career deltas, plan §2.2) merged with `season_stat_lines` / `game_stat_lines`
(box-score counters). Team denominators come from `team_stats` summed over the player's team's games. Rates
are fractions (0-1), not percentages.

`player_metrics(cur, season, player_id, week=None)` merges those sources for one player and returns
`{"inputs", "metrics", "weeks_unknown"}`. The cursor is ordinary psycopg; tests use a stub. A missing or
NULL input, or a zero denominator, gives None.

Note on the merge: `def_catches_allowed` and `downs_played` exist on both sides (the Career delta and the
stat line). A non-NULL Career delta wins, because it is the same counter measured over the same tracked
weeks; stat-line values fill in when the Career row has no value for that field.

CLI (read-only; uses DATABASE_URL):

    python3 -m engines.advanced_metrics --list
    python3 -m engines.advanced_metrics --season 2027 --player 1234 [--week 5]

`passer_rating` mirrors `pipeline/load_save.passer_rating` (same clamps and one-decimal rounding); it is
copied rather than imported so this module stays usable offline without the loader's DB stack.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

OK = "OK"
BLOCKED = "BLOCKED"
IMPOSSIBLE = "IMPOSSIBLE"
STATUSES = (OK, BLOCKED, IMPOSSIBLE)

DEFAULT_DSN = "host=localhost dbname=madden user=postgres password=postgres"


@dataclass(frozen=True)
class Metric:
    key: str
    label: str
    group: str
    inputs: tuple[str, ...]
    formula: Callable[[dict], float | None]
    status: str
    reason: str = ""


# ---------------------------------------------------------------- helpers ----
def _num(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratio(numerator, denominator) -> float | None:
    n, d = _num(numerator), _num(denominator)
    if n is None or d is None or d == 0:
        return None
    return n / d


def _sum(*values) -> float | None:
    """Sum, or None if any part is missing (a partial sum would look like a real answer)."""
    nums = [_num(v) for v in values]
    if any(v is None for v in nums):
        return None
    return sum(nums)


def _passer_rating(comp, att, yds, td, ints) -> float | None:
    """NFL passer rating, as pipeline.load_save.passer_rating (rounded to one decimal)."""
    att = _num(att)
    if not att:
        return None
    comp, yds, td, ints = (_num(comp) or 0.0, _num(yds) or 0.0, _num(td) or 0.0, _num(ints) or 0.0)

    def clamp(x: float) -> float:
        return max(0.0, min(2.375, x))

    a = clamp((comp / att - 0.3) * 5)
    b = clamp((yds / att - 3) * 0.25)
    c = clamp(td / att * 20)
    d = clamp(2.375 - ints / att * 25)
    return round((a + b + c + d) / 6 * 100, 1)


def _passer_rating_lite(catches_allowed, targets, ints) -> float | None:
    """Catch-and-INT passer rating allowed: catches allowed are completions, targets are attempts, and
    yards and TDs are treated as zero because Madden does not record coverage yards or TDs."""
    return _passer_rating(catches_allowed, targets, 0, 0, ints)


# ---------------------------------------------------------------- formulas ----
# Receiving (doc §4.1).
def _tprr(r):
    return _ratio(r.get("targets"), r.get("madden_routes"))


def _yprr(r):
    return _ratio(r.get("rec_yards"), r.get("madden_routes"))


def _target_share(r):
    return _ratio(r.get("targets"), r.get("team_pass_attempts"))


def _route_participation(r):
    return _ratio(r.get("madden_routes"), r.get("team_pass_attempts"))


def _qb_adot(r):
    return _ratio(r.get("air_yards"), r.get("pass_attempts"))


def _receiver_adot(r):
    return _ratio(r.get("air_yards"), r.get("targets"))


def _racr(r):
    return _ratio(r.get("rec_yards"), r.get("air_yards"))


def _catch_rate(r):
    return _ratio(r.get("receptions"), r.get("targets"))


def _drop_rate(r):
    return _ratio(r.get("rec_drops"), _sum(r.get("receptions"), r.get("rec_drops")))


def _separation_index(r):
    return _ratio(_sum(r.get("sep_open"), r.get("sep_wide_open")), r.get("targets"))


# Passing (doc §4.2).
def _p2s(r):
    return _ratio(r.get("pass_sacks"), r.get("pressures_faced"))


def _pressure_rate(r):
    return _ratio(r.get("pressures_faced"), _sum(r.get("pass_attempts"), r.get("pass_sacks")))


def _deep_rate(r):
    return _ratio(r.get("deep_att"), r.get("pass_attempts"))


def _depth_split_efficiency(r):
    return _passer_rating(r.get("deep_cmp"), r.get("deep_att"), r.get("deep_yds"),
                          r.get("deep_td"), r.get("deep_int"))


def _pocket_split(r):
    inside = _passer_rating(r.get("pocket_in_cmp"), r.get("pocket_in_att"), r.get("pocket_in_yds"),
                            r.get("pocket_in_td"), r.get("pocket_in_int"))
    outside = _passer_rating(r.get("pocket_out_cmp"), r.get("pocket_out_att"), r.get("pocket_out_yds"),
                             r.get("pocket_out_td"), r.get("pocket_out_int"))
    if inside is None or outside is None:
        return None
    return inside - outside


def _blitz_rating(r):
    return _passer_rating(r.get("blitz_cmp"), r.get("blitz_plays"), r.get("blitz_yds"),
                          r.get("blitz_td"), r.get("blitz_int"))


def _scramble_rate(r):
    return _ratio(r.get("scramble_att"), _sum(r.get("pass_attempts"), r.get("pass_sacks")))


# Rushing (doc §4.3).
def _rush_success_rate(r):
    return _ratio(r.get("rush_success"), r.get("rush_attempts"))


def _yac_per_att(r):
    return _ratio(r.get("rush_yac"), r.get("rush_attempts"))


def _btk_rate(r):
    return _ratio(r.get("rush_btk"), r.get("rush_attempts"))


def _explosive_rate(r):
    return _ratio(_sum(r.get("rush_10plus"), r.get("rush_20plus")), r.get("rush_attempts"))


def _loaded_box_rate(r):
    return _ratio(r.get("loaded_box"), r.get("rush_attempts"))


def _light_box_rate(r):
    return _ratio(r.get("light_box"), r.get("rush_attempts"))


def _concept_split(r):
    return _ratio(r.get("outside_run_yards"), r.get("outside_run_carries"))


# Defense (doc §4.4).
def _coverage_rating(r):
    return _passer_rating(r.get("def_catches_allowed"), r.get("def_targets"),
                          r.get("coverage_yards"), r.get("coverage_tds"), r.get("def_ints"))


def _coverage_rating_lite(r):
    return _passer_rating_lite(r.get("def_catches_allowed"), r.get("def_targets"), r.get("def_ints"))


def _burn_pct(r):
    return _ratio(r.get("def_catches_allowed"), r.get("def_targets"))


def _prate(r):
    return _ratio(r.get("def_pressures"), r.get("pass_rush_snaps"))


def _sack_conversion_rate(r):
    return _ratio(r.get("def_sacks"), r.get("def_pressures"))


def _stop_rate(r):
    return _ratio(r.get("stops"), r.get("downs_played"))


def _stuff_rate(r):
    return _ratio(r.get("stuffs"), r.get("run_defense_snaps"))


def _stuff_rate_all_snaps(r):
    return _ratio(r.get("stuffs"), r.get("downs_played"))


def _press_frequency(r):
    return _ratio(r.get("coverage_depth_press"), r.get("coverage_snaps"))


# Offensive line & playing time (doc §4.5).
def _pass_block_sack_rate(r):
    return _ratio(r.get("blk_sacks_allowed"), r.get("pass_block_snaps"))


def _pancake_rate(r):
    return _ratio(r.get("blk_pancakes"), r.get("run_block_snaps"))


def _snap_share(r):
    return _ratio(r.get("downs_played"), r.get("team_total_plays"))


def _tb_pct(r):
    return _ratio(r.get("kick_touchbacks"), r.get("kick_kickoffs"))


BLOCKED_ROUTES = (
    "Needs real routes run. Madden's ROUTESRUN grows about 0.72 per target and about 5% of DOWNSPLAYED; "
    "it is not routes run (spike S22 / plan R2)."
)
IMPOSSIBLE_DEPTH = "SHORT/MEDIUM/DEEPPASS_* never grow; they hold real-NFL legacy totals (spike S19)."
IMPOSSIBLE_NO_SNAP_SPLIT = "Madden does not split snaps by type, so there is no such snap count."

METRICS: dict[str, Metric] = {
    # ---- 4.1 Wide receiver & tight end
    "tprr": Metric("tprr", "Targets per route run (TPRR)", "receiving",
                   ("targets", "madden_routes"), _tprr, BLOCKED, BLOCKED_ROUTES),
    "yprr": Metric("yprr", "Yards per route run (YPRR)", "receiving",
                   ("rec_yards", "madden_routes"), _yprr, BLOCKED, BLOCKED_ROUTES),
    "target_share": Metric("target_share", "Target share", "receiving",
                           ("targets", "team_pass_attempts"), _target_share, OK),
    "route_participation": Metric("route_participation", "Route participation", "receiving",
                                  ("madden_routes", "team_pass_attempts"), _route_participation,
                                  BLOCKED, BLOCKED_ROUTES),
    "rec_adot": Metric("rec_adot", "Receiver average depth of target (aDOT)", "receiving",
                       ("air_yards", "targets"), _receiver_adot, IMPOSSIBLE,
                       "Air yards are recorded only on the passer's Career row; there is no per-receiver "
                       "air-yards counter (spike S9). QB aDOT replaces it."),
    "racr": Metric("racr", "Receiver air conversion ratio (RACR)", "receiving",
                   ("rec_yards", "air_yards"), _racr, IMPOSSIBLE,
                   "Air yards are a passer counter, so a receiver's RACR cannot be built (spike S9)."),
    "catch_rate": Metric("catch_rate", "Catch rate", "receiving",
                         ("receptions", "targets"), _catch_rate, OK),
    "drop_rate": Metric("drop_rate", "Drop rate", "receiving",
                        ("rec_drops", "receptions"), _drop_rate, OK),
    "separation_index": Metric("separation_index", "Separation index (open / contested rate)", "receiving",
                               ("sep_open", "sep_wide_open", "sep_tight", "targets"),
                               _separation_index, IMPOSSIBLE,
                               "Separation is a passer-side count, not a per-receiver one, and the "
                               "TIGHT/NORMAL/OPEN buckets are aliased in the save (spike S9, S21)."),

    # ---- 4.2 Quarterback
    "p2s": Metric("p2s", "Pressure-to-sack rate (P2S)", "passing",
                  ("pass_sacks", "pressures_faced"), _p2s, OK),
    "pressure_rate": Metric("pressure_rate", "Pressure rate faced", "passing",
                            ("pressures_faced", "pass_attempts", "pass_sacks"), _pressure_rate, OK),
    "deep_rate": Metric("deep_rate", "Deep ball attempt rate", "passing",
                        ("deep_att", "pass_attempts"), _deep_rate, IMPOSSIBLE, IMPOSSIBLE_DEPTH),
    "depth_split_efficiency": Metric("depth_split_efficiency", "Depth-stratified efficiency", "passing",
                                     ("short_att", "short_cmp", "short_yds", "short_td", "short_int",
                                      "medium_att", "medium_cmp", "medium_yds", "medium_td", "medium_int",
                                      "deep_att", "deep_cmp", "deep_yds", "deep_td", "deep_int"),
                                     _depth_split_efficiency, IMPOSSIBLE, IMPOSSIBLE_DEPTH),
    "pocket_split": Metric("pocket_split", "Pocket vs. off-platform passer rating (in - out)", "passing",
                           ("pocket_in_att", "pocket_in_cmp", "pocket_in_yds", "pocket_in_td",
                            "pocket_in_int", "pocket_out_att", "pocket_out_cmp", "pocket_out_yds",
                            "pocket_out_td", "pocket_out_int"), _pocket_split, OK),
    "blitz_rating": Metric("blitz_rating", "Blitz passer rating", "passing",
                           ("blitz_plays", "blitz_cmp", "blitz_yds", "blitz_td", "blitz_int"),
                           _blitz_rating, IMPOSSIBLE,
                           "BLITZ_* never grow; they hold real-NFL legacy totals (spike S19)."),
    "scramble_rate": Metric("scramble_rate", "Emergency scramble rate", "passing",
                            ("scramble_att", "pass_attempts", "pass_sacks"), _scramble_rate, IMPOSSIBLE,
                            "QBSCRAMBLE_* never grow; they hold real-NFL legacy totals (spike S19)."),
    "qb_adot": Metric("qb_adot", "Quarterback average depth of target (aDOT)", "passing",
                      ("air_yards", "pass_attempts"), _qb_adot, OK),

    # ---- 4.3 Running back
    "rush_success_rate": Metric("rush_success_rate", "Rushing success rate", "rushing",
                                ("rush_success", "rush_attempts"), _rush_success_rate, OK),
    "yac_per_att": Metric("yac_per_att", "Yards after contact per attempt (YAC/Att)", "rushing",
                          ("rush_yac", "rush_attempts"), _yac_per_att, OK),
    "btk_rate": Metric("btk_rate", "Broken tackle rate (BTK rate)", "rushing",
                       ("rush_btk", "rush_attempts"), _btk_rate, OK),
    "explosive_rate": Metric("explosive_rate", "Explosive run rate", "rushing",
                             ("rush_10plus", "rush_20plus", "rush_attempts"), _explosive_rate, OK),
    "loaded_box_rate": Metric("loaded_box_rate", "Loaded box rate", "rushing",
                              ("loaded_box", "rush_attempts"), _loaded_box_rate, OK),
    "light_box_rate": Metric("light_box_rate", "Light box rate", "rushing",
                             ("light_box", "rush_attempts"), _light_box_rate, OK),
    "concept_split": Metric("concept_split", "Inside vs. outside run efficiency", "rushing",
                            ("outside_run_carries", "outside_run_yards"), _concept_split, IMPOSSIBLE,
                            "INSIDE/OUTSIDERUN_* never fill (spike S10)."),

    # ---- 4.4 Defense & secondary
    "coverage_rating": Metric("coverage_rating", "Passer rating allowed in coverage", "defense",
                              ("def_targets", "def_catches_allowed", "def_ints", "coverage_yards",
                               "coverage_tds"), _coverage_rating, IMPOSSIBLE,
                              "Coverage yards and touchdowns are not recorded, so the full rating cannot "
                              "be built; coverage_rating_lite uses catches and INTs only."),
    "coverage_rating_lite": Metric("coverage_rating_lite", "Passer rating allowed, catch-and-INT (lite)",
                                   "defense", ("def_catches_allowed", "def_targets", "def_ints"),
                                   _coverage_rating_lite, OK),
    "burn_pct": Metric("burn_pct", "Catch rate allowed (burn %)", "defense",
                       ("def_catches_allowed", "def_targets"), _burn_pct, OK),
    "prate": Metric("prate", "Pass rush pressure rate (PRate)", "defense",
                    ("def_pressures", "pass_rush_snaps"), _prate, IMPOSSIBLE,
                    IMPOSSIBLE_NO_SNAP_SPLIT + " There is no pass-rush snap count."),
    "sack_conversion_rate": Metric("sack_conversion_rate", "Sack conversion rate", "defense",
                                   ("def_sacks", "def_pressures"), _sack_conversion_rate, OK),
    "stop_rate": Metric("stop_rate", "Defensive stop rate", "defense",
                        ("stops", "downs_played"), _stop_rate, OK),
    "stuff_rate": Metric("stuff_rate", "Stuff rate per run-defense snap", "defense",
                         ("stuffs", "run_defense_snaps"), _stuff_rate, IMPOSSIBLE,
                         IMPOSSIBLE_NO_SNAP_SPLIT + " stuff_rate_all_snaps divides by all downs played "
                         "instead."),
    "stuff_rate_all_snaps": Metric("stuff_rate_all_snaps", "Stuff rate per snap (all downs)", "defense",
                                   ("stuffs", "downs_played"), _stuff_rate_all_snaps, OK),
    "press_frequency": Metric("press_frequency", "Press technique frequency", "defense",
                              ("coverage_depth_press", "coverage_snaps"), _press_frequency, IMPOSSIBLE,
                              "COVERAGEDEPTH_* never fill (spike S10), and there is no coverage-snap count."),

    # ---- 4.5 Offensive line & playing time
    "pass_block_sack_rate": Metric("pass_block_sack_rate", "Pass blocking sack rate", "blocking",
                                   ("blk_sacks_allowed", "pass_block_snaps"), _pass_block_sack_rate,
                                   IMPOSSIBLE, IMPOSSIBLE_NO_SNAP_SPLIT + " There is no pass-block snap "
                                   "count."),
    "pancake_rate": Metric("pancake_rate", "Pancake block rate", "blocking",
                           ("blk_pancakes", "run_block_snaps"), _pancake_rate, IMPOSSIBLE,
                           IMPOSSIBLE_NO_SNAP_SPLIT + " There is no run-block snap count."),
    "snap_share": Metric("snap_share", "Snap share", "blocking",
                         ("downs_played", "team_total_plays"), _snap_share, OK),
    "tb_pct": Metric("tb_pct", "Touchback percentage (TB %)", "special",
                     ("kick_touchbacks", "kick_kickoffs"), _tb_pct, OK),
}


def metric_status() -> list[dict]:
    """One row per catalog metric, in catalog order: {key, label, status, reason}."""
    return [{"key": m.key, "label": m.label, "status": m.status, "reason": m.reason}
            for m in METRICS.values()]


def compute(row: dict) -> dict[str, float | None]:
    """Every OK metric over one merged row. A missing/NULL declared input gives None, as does a zero
    denominator inside the formula. BLOCKED and IMPOSSIBLE metrics are never computed."""
    out: dict[str, float | None] = {}
    for metric in METRICS.values():
        if metric.status != OK:
            continue
        if any(row.get(name) is None for name in metric.inputs):
            out[metric.key] = None
            continue
        out[metric.key] = metric.formula(row)
    return out


# ---------------------------------------------------------------- read side ----
# Typed columns on player_week_counters / player_season_counters (plan §2.2).
COUNTER_COLUMNS = (
    "targets", "madden_routes",
    "air_yards", "pressures_faced",
    "sep_tight", "sep_normal", "sep_open", "sep_wide_open",
    "short_att", "short_cmp", "short_yds", "short_td", "short_int",
    "medium_att", "medium_cmp", "medium_yds", "medium_td", "medium_int",
    "deep_att", "deep_cmp", "deep_yds", "deep_td", "deep_int",
    "pocket_in_att", "pocket_in_cmp", "pocket_in_yds", "pocket_in_td", "pocket_in_int",
    "pocket_out_att", "pocket_out_cmp", "pocket_out_yds", "pocket_out_td", "pocket_out_int",
    "blitz_plays", "blitz_cmp", "blitz_yds", "blitz_td", "blitz_int",
    "scramble_att", "scramble_yds", "scramble_td",
    "throw_left", "throw_middle", "throw_right",
    "rush_success", "rush_10plus", "light_box", "loaded_box",
    "def_targets", "def_catches_allowed", "def_pressures", "stops", "stuffs", "downs_played",
)

# Box-score columns on season_stat_lines / game_stat_lines (the six new ones are appended by the
# 2027_02_25 migration; plan §2.2).
STAT_COLUMNS = (
    "completions", "pass_attempts", "pass_yards", "pass_tds", "interceptions", "pass_sacks",
    "rush_attempts", "rush_yards", "rush_tds", "rush_btk", "rush_yac", "rush_20plus",
    "receptions", "rec_yards", "rec_tds", "rec_rac", "rec_drops",
    "def_sacks", "def_ints", "def_catches_allowed", "def_big_hits",
    "blk_sacks_allowed", "blk_pancakes",
    "downs_played", "games_started",
    "kick_kickoffs", "kick_touchbacks",
)

TEAM_COLUMNS = ("team_pass_attempts", "team_total_plays")
WEEKS_UNKNOWN_STATUSES = ("TABLE_CHANGED", "RESET", "CROSS_SEASON")


def _fetch_row(cur, columns: tuple[str, ...], sql: str, params: dict) -> dict:
    """One row as a dict. Accepts a mapping row or a positional tuple (ordinary psycopg)."""
    cur.execute(sql, params)
    row = cur.fetchone()
    if row is None:
        return {}
    if isinstance(row, Mapping):
        return {name: row.get(name) for name in columns}
    return dict(zip(columns, row))


def _counter_row(cur, season: int, player_id: int, week: int | None) -> dict:
    if week is None:
        columns = ("weeks_unknown",) + COUNTER_COLUMNS
        sql = (f"SELECT {', '.join(columns)} FROM player_season_counters "
               "WHERE season = %(season)s AND player_id = %(player_id)s")
        params = {"season": season, "player_id": player_id}
    else:
        columns = ("status",) + COUNTER_COLUMNS
        sql = (f"SELECT {', '.join(columns)} FROM player_week_counters "
               "WHERE season = %(season)s AND week = %(week)s AND player_id = %(player_id)s "
               "AND stage = 'WEEK'")
        params = {"season": season, "week": week, "player_id": player_id}
    return _fetch_row(cur, columns, sql, params)


def _line_row(cur, season: int, player_id: int, week: int | None) -> dict:
    columns = ("team_id",) + STAT_COLUMNS
    if week is None:
        sql = (f"SELECT {', '.join(columns)} FROM season_stat_lines "
               "WHERE season = %(season)s AND player_id = %(player_id)s")
        params = {"season": season, "player_id": player_id}
    else:
        sql = (f"SELECT {', '.join(columns)} FROM game_stat_lines "
               "WHERE season = %(season)s AND week = %(week)s AND player_id = %(player_id)s")
        params = {"season": season, "week": week, "player_id": player_id}
    return _fetch_row(cur, columns, sql, params)


def _team_totals(cur, team_id, season: int, week: int | None) -> dict:
    """Team denominators: the player's team's pass attempts and total plays for the season or week."""
    if team_id is None:
        return {name: None for name in TEAM_COLUMNS}
    sql = """
        SELECT COALESCE(SUM(ts.passing_attempts), 0) AS team_pass_attempts,
               COALESCE(SUM(ts.total_plays), 0) AS team_total_plays
        FROM team_stats ts
        JOIN games g ON g.id = ts.game_id
        JOIN teams t ON t.name = ts.team_name
        WHERE t.id = %(team_id)s AND g.season = %(season)s"""
    params = {"team_id": team_id, "season": season}
    if week is not None:
        sql += " AND g.week = %(week)s"
        params["week"] = week
    return _fetch_row(cur, TEAM_COLUMNS, sql, params)


def player_metrics(cur, season: int, player_id: int, week: int | None = None) -> dict:
    """One player's inputs and OK metric values.

    `week` is None: the whole season (`player_season_counters` + `season_stat_lines`). Otherwise one week
    (`player_week_counters` + `game_stat_lines`). `weeks_unknown` is the season view's count, or 1 for a
    week whose delta is unknown (the Career table changed or the counters reset).
    """
    counters = _counter_row(cur, season, player_id, week)
    line = _line_row(cur, season, player_id, week)

    merged = dict(line)
    for name, value in counters.items():
        if value is not None and name not in ("status", "weeks_unknown"):
            merged[name] = value
    merged.update(_team_totals(cur, merged.get("team_id"), season, week))

    if week is None:
        weeks_unknown = int(counters.get("weeks_unknown") or 0)
    else:
        weeks_unknown = 1 if counters.get("status") in WEEKS_UNKNOWN_STATUSES else 0

    return {"inputs": merged, "metrics": compute(merged), "weeks_unknown": weeks_unknown}


# ---------------------------------------------------------------- CLI ----
def _print_list() -> None:
    rows = metric_status()
    print(f"{'KEY':<24} {'STATUS':<11} {'GROUP':<10} LABEL")
    print("-" * 100)
    for row in rows:
        metric = METRICS[row["key"]]
        print(f"{row['key']:<24} {row['status']:<11} {metric.group:<10} {row['label']}")
        if row["reason"]:
            print(f"{'':<24} {'':<11} {'':<10} {row['reason']}")
    counts = {status: sum(1 for r in rows if r["status"] == status) for status in STATUSES}
    print("-" * 100)
    print(f"{len(rows)} metrics: " + ", ".join(f"{counts[s]} {s}" for s in STATUSES))


def _print_player(season: int, player_id: int, week: int | None) -> None:
    import psycopg  # local: --list must work without a database

    dsn = os.environ.get("DATABASE_URL", DEFAULT_DSN)
    with psycopg.connect(dsn) as conn:
        conn.read_only = True
        with conn.cursor() as cur:
            result = player_metrics(cur, season, player_id, week)

    where = f"season {season}" + (f", week {week}" if week is not None else "")
    print(f"Advanced metrics for player {player_id}, {where} "
          f"(weeks unknown: {result['weeks_unknown']})")
    print("\nINPUTS")
    for name in sorted(result["inputs"]):
        print(f"  {name:<28} {result['inputs'][name]}")
    print("\nMETRICS")
    for metric in METRICS.values():
        if metric.status == OK:
            value = result["metrics"].get(metric.key)
            print(f"  {metric.key:<24} {'None' if value is None else f'{value:.4f}':>10}  {metric.label}")
        else:
            print(f"  {metric.key:<24} {'-':>10}  {metric.status}: {metric.reason}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Advanced Madden player metrics (read-only).")
    parser.add_argument("--list", action="store_true", help="list every metric with its status and reason")
    parser.add_argument("--season", type=int, help="season to read")
    parser.add_argument("--player", type=int, help="players.id to read")
    parser.add_argument("--week", type=int, help="one week (default: the whole season)")
    args = parser.parse_args(argv)

    if args.list:
        _print_list()
        return 0
    if args.season is None or args.player is None:
        parser.error("use --list, or --season N with --player ID (and optional --week N)")
    if args.week is not None and args.week < 1:
        parser.error("--week must be 1 or later")
    _print_player(args.season, args.player, args.week)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
