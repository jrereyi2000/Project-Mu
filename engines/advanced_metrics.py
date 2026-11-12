"""Advanced player metrics over Madden's stored counters (MADDEN_STATS_INGEST_PLAN U3).

Every derived metric in `docs/reference/MADDEN_STATS_AND_ADVANCED_METRICS.md` §4 is one `Metric` in
`METRICS`, with a status copied from the plan's §2.5:

- OK: computable now, and `compute()` returns it.
- BLOCKED: a counter's meaning is unresolved (`ROUTESRUN` is not routes run; spike S22) or the measured
  counters contradict the metric's premise (audit M1: sacks exceed pressures, catches allowed exceed the
  league's targets, 10+ runs are rarer than 20+ runs, downs played exceed the team's plays), so the doc
  metric stays uncomputed until the question is answered.
- IMPOSSIBLE: Madden does not record the inputs (no per-receiver air yards, no pass-rush/run-block snap
  splits, counters that never fill). BLOCKED and IMPOSSIBLE metrics are never computed and carry a reason.

The module holds no numbers of its own. Both sides of every metric come from the same games (audit H3): the
career counters are the response's `delta` JSONB on `player_week_counters` (plan §2.2), summed week by week
for the season, so the full box counters (`RECEIVECATCHES`, `PASSATTEMPTS`, `PASSSACKED`, `RUSHATTEMPTS`,
`DLINESACKS`, `DSECINTS`, `RECEIVEDROPS`, ...) stay on the Career side. A week Madden did not track is
detected per team (the tracked inside/outside-pocket pass attempts must account for the team's
`PASSATTEMPTS` growth) and excluded like an unknown week. `season_stat_lines` / `game_stat_lines` fill in
only a field the Career row does not carry (kicking and special teams). When a metric would mix the two
sources over different game counts it returns None and `coverage` says why. Team denominators come from
`team_stats` summed over the same game weeks as the numerator. Rates are fractions (0-1), not percentages.

`player_metrics(cur, season, player_id, week=None)` returns
`{"inputs", "metrics", "weeks_unknown", "coverage"}`. The cursor is ordinary psycopg; tests use a stub. A
missing or NULL input, or a zero denominator, gives None.

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
BLOCKED_SACKS = (
    "The counters contradict the premise: DLINESACKS (1,305) exceeds DEFPRESSURES (732) over the 2026 "
    "season, so the pair cannot be a sack-per-pressure rate (audit M1)."
)
BLOCKED_COVERAGE = (
    "The counters contradict the premise: DEFTARGETS (48,001) is more than triple the league's targets "
    "(14,117) and receptions (10,124) while CTHALLOWED is 10,003, so their ratio is not a catch rate "
    "(audit M1)."
)
BLOCKED_EXPLOSIVE = (
    "The counters contradict the premise: RUSH10YARDRUNS (179) is lower than RUSH20YARDRUNS (306), and 34 "
    "of the 90 backs with a 20+ run have no 10+ run, so the buckets do not nest (audit M1)."
)
BLOCKED_SNAP_SHARE = (
    "The counters contradict the premise: DOWNSPLAYED over the team's total plays exceeds 1 for real "
    "players (J. Horn 193/172 = 1.12), so they do not share a denominator (audit M1)."
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
                             ("rush_10plus", "rush_20plus", "rush_attempts"), _explosive_rate,
                             BLOCKED, BLOCKED_EXPLOSIVE),
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
                                   _coverage_rating_lite, BLOCKED, BLOCKED_COVERAGE),
    "burn_pct": Metric("burn_pct", "Catch rate allowed (burn %)", "defense",
                       ("def_catches_allowed", "def_targets"), _burn_pct, BLOCKED, BLOCKED_COVERAGE),
    "prate": Metric("prate", "Pass rush pressure rate (PRate)", "defense",
                    ("def_pressures", "pass_rush_snaps"), _prate, IMPOSSIBLE,
                    IMPOSSIBLE_NO_SNAP_SPLIT + " There is no pass-rush snap count."),
    "sack_conversion_rate": Metric("sack_conversion_rate", "Sack conversion rate", "defense",
                                   ("def_sacks", "def_pressures"), _sack_conversion_rate,
                                   BLOCKED, BLOCKED_SACKS),
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
                         ("downs_played", "team_total_plays"), _snap_share,
                         BLOCKED, BLOCKED_SNAP_SHARE),
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
# Madden key names on the career row's `delta` JSONB -> the input names the formulas use (plan §2.2). The
# views expose only a typed subset, and the full box counters (receptions, attempts, sacks, drops) live in
# the delta alone, so the engine sums the delta itself instead of reading the typed season view.
CAREER_KEYS = {
    "targets": "TARGETS",
    "receptions": "RECEIVECATCHES",
    "rec_drops": "RECEIVEDROPS",
    "rec_yards": "RECEIVEYARDS",
    "madden_routes": "ROUTESRUN",
    "air_yards": "AIRYARDS",
    "pass_attempts": "PASSATTEMPTS",
    "pass_sacks": "PASSSACKED",
    "pressures_faced": "PRESSURES",
    "sep_tight": "SEPARATION_TIGHT",
    "sep_normal": "SEPARATION_NORMAL",
    "sep_open": "SEPARATION_OPEN",
    "sep_wide_open": "SEPARATION_WIDEOPEN",
    "short_att": "SHORTPASS_ATTEMPTS",
    "short_cmp": "SHORTPASS_COMPLETIONS",
    "short_yds": "SHORTPASS_YARDS",
    "short_td": "SHORTPASS_TD",
    "short_int": "SHORTPASS_INTERCEPTIONS",
    "medium_att": "MEDIUMPASS_ATTEMPTS",
    "medium_cmp": "MEDIUMPASS_COMPLETIONS",
    "medium_yds": "MEDIUMPASS_YARDS",
    "medium_td": "MEDIUMPASS_TD",
    "medium_int": "MEDIUMPASS_INTERCEPTIONS",
    "deep_att": "DEEPPASS_ATTEMPTS",
    "deep_cmp": "DEEPPASS_COMPLETIONS",
    "deep_yds": "DEEPPASS_YARDS",
    "deep_td": "DEEPPASS_TD",
    "deep_int": "DEEPPASS_INTERCEPTIONS",
    "pocket_in_att": "INSIDEPOCKETTHROW_ATTEMPTS",
    "pocket_in_cmp": "INSIDEPOCKETTHROW_COMPLETIONS",
    "pocket_in_yds": "INSIDEPOCKETTHROW_YARDS",
    "pocket_in_td": "INSIDEPOCKETTHROW_TD",
    "pocket_in_int": "INSIDEPOCKETTHROW_INTERCEPTIONS",
    "pocket_out_att": "OUTSIDEPOCKETTHROW_ATTEMPTS",
    "pocket_out_cmp": "OUTSIDEPOCKETTHROW_COMPLETIONS",
    "pocket_out_yds": "OUTSIDEPOCKETTHROW_YARDS",
    "pocket_out_td": "OUTSIDEPOCKETTHROW_TD",
    "pocket_out_int": "OUTSIDEPOCKETTHROW_INTERCEPTIONS",
    "blitz_plays": "BLITZ_PLAYS",
    "blitz_cmp": "BLITZ_COMPLETIONS",
    "blitz_yds": "BLITZ_YARDS",
    "blitz_td": "BLITZ_TD",
    "blitz_int": "BLITZ_INTERCEPTIONS",
    "scramble_att": "QBSCRAMBLE_CARRIES",
    "scramble_yds": "QBSCRAMBLE_YARDS",
    "scramble_td": "QBSCRAMBLE_TD",
    "throw_left": "THROWLOCATION_LEFT",
    "throw_middle": "THROWLOCATION_MIDDLE",
    "throw_right": "THROWLOCATION_RIGHT",
    "rush_attempts": "RUSHATTEMPTS",
    "rush_success": "RUSHSUCCESS",
    "rush_10plus": "RUSH10YARDRUNS",
    "rush_20plus": "RUSH20YARDRUNS",
    "rush_yac": "RUSHYARDSAFTER1STHIT",
    "rush_btk": "RUSHBROKENTACKLES",
    "light_box": "LIGHTBOX",
    "loaded_box": "LOADEDBOX",
    "outside_run_carries": "OUTSIDERUN_CARRIES",
    "outside_run_yards": "OUTSIDERUN_YARDS",
    "def_targets": "DEFTARGETS",
    "def_catches_allowed": "CTHALLOWED",
    "def_pressures": "DEFPRESSURES",
    "def_ints": "DSECINTS",
    "def_big_hits": "BIGHITS",
    "stops": "STOPS",
    "stuffs": "STUFFS",
    "downs_played": "DOWNSPLAYED",
    "kick_kickoffs": "KICKNUMKICKOFFS",
    "kick_touchbacks": "KICKTOUCHBACKS",
    "blk_sacks_allowed": "OLINESACKSALLOWED",
    "blk_pancakes": "OLINEPANCAKES",
    "coverage_depth_press": "COVERAGEDEPTH_PRESS",
}

# Box-score columns on season_stat_lines / game_stat_lines (the six new ones are appended by the
# 2027_02_25 migration; plan §2.2). Used only where the Career delta has no such counter.
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
USABLE_STATUSES = ("OK", "NO_GAMES")
WEEKS_UNKNOWN_STATUSES = ("TABLE_CHANGED", "RESET", "CROSS_SEASON", "UNKNOWN_HISTORY")
# A tracked Madden week's inside+outside-pocket attempts account for its PASSATTEMPTS. The audit measured
# 1.000 for fully tracked teams and 0.49-0.84 for teams missing week 1's pocket data, so 0.9 separates them
# (audit H3).
TRACKED_POCKET_RATIO = 0.9
# The last week a season's games are played (the Super Bowl). A career row loaded outside a playable week
# (week <= 0: preseason/off-season keys) covers the tail of the previous season through this week.
LAST_SEASON_WEEK = 23


def _fetch_row(cur, columns: tuple[str, ...], sql: str, params: dict) -> dict:
    """One row as a dict. Accepts a mapping row or a positional tuple (ordinary psycopg)."""
    cur.execute(sql, params)
    row = cur.fetchone()
    if row is None:
        return {}
    if isinstance(row, Mapping):
        return {name: row.get(name) for name in columns}
    return dict(zip(columns, row))


def _fetch_rows(cur, columns: tuple[str, ...], sql: str, params: dict) -> list[dict]:
    """Every row as a dict. Accepts mapping rows or positional tuples (ordinary psycopg).

    `delta` is JSONB: psycopg decodes it to a dict; a stub may hand in a dict directly.
    """
    cur.execute(sql, params)
    rows = cur.fetchall()
    if rows and isinstance(rows[0], Mapping):
        return [{name: row.get(name) for name in columns} for row in rows]
    return [dict(zip(columns, row)) for row in rows]


def _career_rows(cur, season: int, player_id: int) -> list[dict]:
    """Every Career-counter row credited to the season.

    A week query gets the whole credited season too, not only the row labelled with that week: a row loaded
    after a bye can be labelled with the bye week while its difference covers the game before it, and a
    sentinel row (PRESEASON/OFFSEASON/PROBOWL) covers the tail of the season it is credited to. The caller
    establishes which games a difference actually covers (audit-5 S3). Every status is returned so
    `player_metrics` can count unknown weeks and lost games; only OK and NO_GAMES deltas are ever used. A
    first row from before the load-time record is UNKNOWN_HISTORY and carries its upper bound separately
    (`season_games_bound`), never as a lost-game count (audit-5 stats2b R1).
    """
    columns = ("season", "week", "prev_season", "prev_week", "status", "delta", "games_covered",
               "season_games_bound")
    sql = (f"SELECT {', '.join(columns)} FROM player_week_counters "
           "WHERE credited_season = %(season)s AND player_id = %(player_id)s ORDER BY load_seq")
    params = {"season": season, "player_id": player_id}
    return _fetch_rows(cur, columns, sql, params)


def _career_game_teams(cur, season: int, player_id: int) -> dict[int, int]:
    """The club the player's own game line names for each week: {week: team_id}.

    A season line names one club, so after a trade it cannot say which club a historical game belonged to.
    The player's per-game lines can, and they are the only stored per-game identity (audit-5 stats3 R2).
    """
    sql = ("SELECT week, team_id FROM game_stat_lines "
           "WHERE season = %(season)s AND player_id = %(player_id)s AND team_id IS NOT NULL "
           "ORDER BY week")
    params = {"season": season, "player_id": player_id}
    teams: dict[int, int] = {}
    for row in _fetch_rows(cur, ("week", "team_id"), sql, params):
        teams.setdefault(int(row["week"]), row["team_id"])
    return teams


def _team_games(cur, team_ids, season: int) -> list[dict]:
    """The clubs' stored games in a season: club, game id, week and whether the club's box exists.

    A bye leaves no game row, so this is what establishes which weeks a Career difference actually covers
    (a week label alone does not), and whether a team denominator has every game it needs. A game without a
    box, or with a NULL pass-attempt value, is not a usable denominator (audit-5 S1/S3). Several clubs are
    read at once because a player's season can span a trade (audit-5 stats3 R2).
    """
    ids = sorted({int(team_id) for team_id in team_ids if team_id is not None})
    if not ids:
        return []
    sql = """
        SELECT t.id AS team_id, g.id AS game_id, g.week,
               (ts.game_id IS NOT NULL AND ts.passing_attempts IS NOT NULL) AS has_box
        FROM games g
        JOIN teams t ON (g.home_team_id = t.id OR g.away_team_id = t.id)
        LEFT JOIN team_stats ts ON ts.game_id = g.id AND ts.team_name = t.name
        WHERE t.id = ANY(%(team_ids)s) AND g.season = %(season)s
          AND COALESCE(g.status, 'COMPLETED') <> 'BYE'
        ORDER BY g.week, g.id, t.id"""
    params = {"team_ids": ids, "season": season}
    return _fetch_rows(cur, ("team_id", "game_id", "week", "has_box"), sql, params)


def _team_tracking(cur, team_ids, season: int) -> dict:
    """Tracked pocket attempts per club and career-row range: {(team_id, week, prev_week, prev_season)}.

    Summed over every usable Career delta of each club's players, so a receiver's row is checked against the
    club it played for (a receiver's own `PASSATTEMPTS` is zero). The club is carried in the key so a traded
    player's row is checked against its own historical clubs, not one substituted season team (audit-5
    stats3 R2).
    """
    ids = sorted({int(team_id) for team_id in team_ids if team_id is not None})
    if not ids:
        return {}
    # The clubs' player ids are an array constant, so the planner can apply it inside the view before its
    # per-player window functions; the view then reads only those clubs' rows instead of every player's.
    sql = """
        WITH team_rows AS MATERIALIZED (
            SELECT w.week, w.prev_week, w.prev_season, w.delta,
                   (SELECT MIN(ps.team_id) FROM player_seasons ps
                    WHERE ps.player_id = w.player_id AND ps.season = %(season)s
                      AND ps.team_id = ANY(%(team_ids)s)) AS team_id
            FROM player_week_counters w
            WHERE w.player_id = ANY(ARRAY(
                      SELECT ps.player_id FROM player_seasons ps
                      WHERE ps.team_id = ANY(%(team_ids)s) AND ps.season = %(season)s))
              AND w.credited_season = %(season)s
              AND w.status IN ('OK', 'NO_GAMES') AND w.delta IS NOT NULL
        )
        SELECT team_id, week, prev_week, prev_season,
               COALESCE(SUM((delta ->> 'PASSATTEMPTS')::int), 0) AS pass_att,
               COALESCE(SUM((delta ->> 'INSIDEPOCKETTHROW_ATTEMPTS')::int), 0)
             + COALESCE(SUM((delta ->> 'OUTSIDEPOCKETTHROW_ATTEMPTS')::int), 0) AS pocket_att
        FROM team_rows
        GROUP BY team_id, week, prev_week, prev_season"""
    params = {"team_ids": ids, "season": season}
    columns = ("team_id", "week", "prev_week", "prev_season", "pass_att", "pocket_att")
    return {(row["team_id"], row["week"], row["prev_week"], row["prev_season"]):
            (row["pass_att"], row["pocket_att"])
            for row in _fetch_rows(cur, columns, sql, params)}


def _row_team_ids(row: dict, game_teams: dict[int, int], candidate_ids) -> list[int]:
    """The clubs a Career row's covered weeks belong to (the row's own games), or every candidate club when
    the weeks carry no per-game identity."""
    teams = {game_teams[week] for week in _row_weeks(row) if week in game_teams}
    return sorted(teams) if teams else sorted(candidate_ids)


def _is_untracked(row: dict, team_tracking: dict, team_ids) -> bool:
    """True when Madden did not track this row's games: the clubs' tracked pocket attempts do not account
    for their pass attempts over the row's range."""
    key = (row.get("week"), row.get("prev_week"), row.get("prev_season"))
    pass_att = pocket_att = 0
    for team_id in team_ids:
        att, pocket = team_tracking.get((team_id,) + key, (0, 0))
        pass_att += att
        pocket_att += pocket
    return pass_att > 0 and pocket_att < TRACKED_POCKET_RATIO * pass_att


def _career_inputs(rows: list[dict]) -> dict:
    """The used Career deltas summed key by key, mapped to the engine's input names.

    A name is present only when at least one Career row carries its Madden key; an absent name falls back
    to the stat line. `def_sacks` combines Madden's full and half sacks.
    """
    totals: dict[str, float] = {}
    for row in rows:
        for key, value in (row.get("delta") or {}).items():
            if value is None:
                continue
            totals[key] = totals.get(key, 0) + int(value)
    values = {name: totals[key] for name, key in CAREER_KEYS.items() if key in totals}
    if "DLINESACKS" in totals or "DLINEHALFSACK" in totals:
        values["def_sacks"] = totals.get("DLINESACKS", 0) + 0.5 * totals.get("DLINEHALFSACK", 0)
    return values


def _row_weeks(row: dict) -> range:
    """The season weeks a usable Career row's delta covers: after the previous row's last played week, through
    this row's. A row loaded outside a playable week (week <= 0, a preseason or off-season key) covers the tail
    of the previous season through the Super Bowl rather than an empty range, so a delta credited to the season
    just ended still fills that season's team denominators (audit-4 stats suspicion 1).
    """
    prev = max(int(row.get("prev_week") or 0), 0)
    week = int(row.get("week") or 0)
    return range(prev + 1, (LAST_SEASON_WEEK if week <= 0 else week) + 1)


def _line_row(cur, season: int, player_id: int, week: int | None) -> dict:
    if week is None:
        columns = ("team_id", "games_played") + STAT_COLUMNS
        sql = (f"SELECT {', '.join(columns)} FROM season_stat_lines "
               "WHERE season = %(season)s AND player_id = %(player_id)s")
        params = {"season": season, "player_id": player_id}
    else:
        columns = ("team_id",) + STAT_COLUMNS
        sql = (f"SELECT {', '.join(columns)} FROM game_stat_lines "
               "WHERE season = %(season)s AND week = %(week)s AND player_id = %(player_id)s")
        params = {"season": season, "week": week, "player_id": player_id}
    return _fetch_row(cur, columns, sql, params)


def _team_totals(cur, game_pairs) -> dict:
    """Team denominators summed over exactly the (club, game) pairs the numerator covers.

    Pairing the club with the game id is what keeps a traded player's historical games apart: a club's
    unrelated game in the same week is never substituted for the player's game (audit-5 stats3 R2).
    """
    pairs = sorted({(int(team_id), int(game_id)) for team_id, game_id in game_pairs})
    if not pairs:
        return {name: None for name in TEAM_COLUMNS}
    sql = """
        SELECT COALESCE(SUM(ts.passing_attempts), 0) AS team_pass_attempts,
               COALESCE(SUM(ts.total_plays), 0) AS team_total_plays
        FROM unnest(%(team_ids)s::int[], %(game_ids)s::int[]) AS pair(team_id, game_id)
        JOIN teams t ON t.id = pair.team_id
        JOIN team_stats ts ON ts.game_id = pair.game_id AND ts.team_name = t.name"""
    params = {"team_ids": [pair[0] for pair in pairs], "game_ids": [pair[1] for pair in pairs]}
    return _fetch_row(cur, TEAM_COLUMNS, sql, params)


def _weeks_text(weeks) -> str:
    """`1-3,7,9-10` for a set of week numbers (coverage messages)."""
    weeks = sorted({int(w) for w in weeks if w is not None})
    if not weeks:
        return "none"
    spans: list[str] = []
    start = prev = weeks[0]
    for value in weeks[1:]:
        if value == prev + 1:
            prev = value
            continue
        spans.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = value
    spans.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(spans)


def _add_once(items: list[str], text: str) -> None:
    if text not in items:
        items.append(text)


def _game_weeks(row: dict, team_weeks: set[int], schedule_known: bool) -> set[int]:
    """The weeks a usable Career row's delta actually covers.

    The row's label range is `_row_weeks`; a team's bye leaves no game, so the range is cut to the team's
    stored games when the schedule is known. Without a schedule only a one-week delta can be placed.
    """
    weeks = set(_row_weeks(row))
    if schedule_known:
        return weeks & team_weeks
    return weeks


def _denominator_games(weeks, game_teams: dict[int, int], games_by_team: dict):
    """The (club, game) pairs whose boxes are the season denominator, or why a game cannot be bound.

    A covered week the player's own game line names takes that line's club. A covered week with no line
    takes the one candidate club that played it; two candidate clubs playing means the historical club
    cannot be told, so the denominator is refused rather than the current club substituted (audit-5
    stats3 R2). A week no candidate club played is a bye and contributes no game. The games are those of
    the covered weeks, so a player inactive for a club game still counts it (the DNP question is separate).
    """
    playing: dict[int, list[tuple[int, dict]]] = {}
    for team_id, games in games_by_team.items():
        for game in games:
            playing.setdefault(int(game["week"]), []).append((team_id, game))
    bound: list[tuple[int, dict]] = []
    for week in weeks:
        club = game_teams.get(week)
        if club is not None:
            game = next((game for team_id, game in playing.get(week, []) if team_id == club), None)
            if game is None:
                return [], f"week {week}: no stored game for the club the player's line names"
            bound.append((club, game))
            continue
        candidates = playing.get(week, [])
        if not candidates:
            continue
        if len(candidates) > 1:
            return [], (f"week {week}: the player has no game line and more than one club played; "
                        "the historical club cannot be established")
        bound.append(candidates[0])
    return bound, None


def _denominator_reason(games_covered: int, played: list[dict]) -> str | None:
    """Why a team denominator is not complete over the games the numerator covers, or None when it is.

    Every stored team game in the numerator's weeks must have a box (`team_stats`), and the numerator cannot
    claim more games than the team has stored with boxes. Fewer is fine (a player inactive for a team game);
    more means the team history is partial (audit-5 S1).
    """
    if not played:
        return "no stored team game in the Career covered weeks"
    missing = [g["week"] for g in played if not g.get("has_box")]
    if missing:
        return f"missing team box for weeks {_weeks_text(missing)}"
    if games_covered > len(played):
        return (f"Career rows cover {games_covered} games but the team has only {len(played)} stored "
                f"games with boxes")
    return None


def _record_invalid(row: dict, invalid: list[str]) -> bool:
    """Name a Career delta whose receptions exceed its targets; True when it contradicts itself."""
    delta = row.get("delta") or {}
    targets, receptions = delta.get("TARGETS"), delta.get("RECEIVECATCHES")
    if targets is None or receptions is None or int(receptions) <= int(targets):
        return False
    _add_once(invalid, f"Career row week {row.get('week')}: {int(receptions)} receptions exceed "
                       f"{int(targets)} targets")
    return True


def player_metrics(cur, season: int, player_id: int, week: int | None = None) -> dict:
    """One player's inputs, OK metric values and coverage.

    Every value comes from the Career deltas of the games it is meant to describe. The season sums the
    player's usable `player_week_counters` rows credited to `season`; a week query reads the whole credited
    season and then keeps only a delta that covers exactly one game, the requested one: its label range cut
    to the team's stored schedule must be that week alone and its own `games_covered` must be one, because a
    delta loaded after a bye or a skipped save can cover several games and a truncated schedule cannot prove
    otherwise (audit-5 S3/R1). When no delta is isolated to the requested week the box score
    (`game_stat_lines`) supplies the week's counters and the Career-only inputs stay None with a coverage
    reason. A BASELINE, PARTIAL, TABLE_CHANGED, RESET, CROSS_SEASON, UNKNOWN_HISTORY or Madden-untracked row is
    not used; unknown and untracked rows count in `weeks_unknown` the way the season view counts unknown weeks,
    and a first row from before the load-time record (UNKNOWN_HISTORY) is named in `reasons` with its labelled
    upper bound, never as lost games (audit-5 stats2b R1). The
    stat line supplies only a counter the Career row does not carry, and a metric that would mix the two
    sources over different game counts is None.

    Team denominators are bound per historical game: the player's own game lines say which club each covered
    week belonged to, so a traded player's games are summed club by club rather than through one season team
    (audit-5 stats3 R2). They are used only when every bound game and box covers the games the numerator
    covers (audit-5 S1); otherwise `team_denominator` names the gap and the denominator inputs are None.
    `coverage` reports games covered, stat-line games, untracked weeks and lost games, plus `mixed_sources`
    when a metric was suppressed for mixing sources, `invalid_inputs` when a counter contradicts another
    (receptions above targets would publish a catch rate over one, audit-5 S4), and `reasons` for a week that
    could not be isolated.
    """
    line = _line_row(cur, season, player_id, week)
    season_team = line.get("team_id")
    if season_team is None:
        # A stat line without a team (a season row written before the team was known, or a week with no line)
        # still has the player's season team; the denominators need one (audit-4 stats suspicion 1).
        season_team = _fetch_row(cur, ("team_id",),
                                 "SELECT team_id FROM player_seasons "
                                 "WHERE season = %(season)s AND player_id = %(player_id)s",
                                 {"season": season, "player_id": player_id}).get("team_id")
    rows = _career_rows(cur, season, player_id)
    if week is None:
        # A season can span a trade, so the clubs come from the player's own game lines (audit-5 stats3 R2).
        game_teams = _career_game_teams(cur, season, player_id)
        candidate_ids = {team for team in (season_team, *game_teams.values()) if team is not None}
    else:
        # The requested week's own game line names the club the player played for that week.
        game_teams = {}
        club = line.get("team_id") if line.get("team_id") is not None else season_team
        candidate_ids = {club} if club is not None else set()
    team_games = _team_games(cur, candidate_ids, season)
    schedule_known = bool(team_games)
    team_weeks = {int(game["week"]) for game in team_games if game.get("week") is not None}
    games_by_team: dict[int, list[dict]] = {}
    for game in team_games:
        games_by_team.setdefault(game["team_id"], []).append(game)
    team_tracking = _team_tracking(cur, candidate_ids, season)

    reasons: list[str] = []
    invalid: list[str] = []
    if week is None:
        scoped = rows
        selectable = rows
    else:
        # Only a delta that covers exactly one game, the requested one, can describe it (audit-5 S3). A row
        # labelled with the week is not proof: a bye or a skipped save makes its range cover other games too.
        # Nor is a truncated schedule proof: the delta's own game count must agree with the game set the
        # stored schedule can place it in, or the missing history would turn a two-game delta into one game
        # (audit-5 stats3 R1).
        scoped = [row for row in rows if week in _row_weeks(row)]
        selectable = []
        if not schedule_known:
            _add_once(reasons, "no stored team schedule, so a week cannot be placed")
        elif week not in team_weeks:
            _add_once(reasons, f"week {week}: no game in the team's stored schedule")
        for row in scoped:
            claimed = int(row.get("games_covered") or 0)
            covered = _game_weeks(row, team_weeks, schedule_known)
            if not schedule_known:
                # Without a schedule only a one-week, one-game delta can still be trusted.
                if claimed == 1 and len(_row_weeks(row)) == 1:
                    selectable.append(row)
                continue
            if week not in covered:
                continue
            if covered != {week}:
                _add_once(reasons,
                          f"week {week}: a Career delta covers weeks {_weeks_text(covered)} and cannot "
                          "be split")
                continue
            if claimed != 1:
                _add_once(reasons,
                          f"week {week}: a Career delta covers {claimed} games but the stored team schedule "
                          f"places it in week {week} alone; the delta cannot be isolated to one game")
                continue
            selectable.append(row)
        if not schedule_known and not selectable and scoped:
            _add_once(reasons, f"week {week}: no one-week Career delta and no schedule to place one")

    used: list[dict] = []
    weeks_unknown = 0
    untracked = 0
    games_lost = 0
    bad_targets = False
    # A first row from before the load-time record makes its earlier games unknown, not zero: it is counted
    # as an unknown week and its upper bound is named as a maximum, never as lost games (audit-5 stats2b R1).
    # Counted over the scoped rows, because such a row can never be selected for a delta.
    for row in scoped:
        if row.get("status") != "UNKNOWN_HISTORY":
            continue
        weeks_unknown += 1
        bound = row.get("season_games_bound")
        _add_once(reasons,
                  "the first Career row predates the load-time record of season games, so the games before "
                  "it are unknown" + (f" (at most {int(bound)})" if bound is not None else ""))
    for row in selectable:
        status = row.get("status")
        if status == "UNKNOWN_HISTORY":
            continue                      # counted above, with its labelled bound
        if status in WEEKS_UNKNOWN_STATUSES:
            weeks_unknown += 1
        elif status == "PARTIAL":
            games_lost += int(row.get("games_covered") or 0)
        if status in USABLE_STATUSES and row.get("delta"):
            if _is_untracked(row, team_tracking, _row_team_ids(row, game_teams, candidate_ids)):
                untracked += 1
                weeks_unknown += 1
            else:
                used.append(row)
                bad_targets = _record_invalid(row, invalid) or bad_targets

    # A delta that could not be used for this week can still contradict itself; report it so the coverage
    # names the bad period even though no rate was published from it (audit-5 S4).
    for row in scoped:
        if row not in used:
            _record_invalid(row, invalid)

    career = _career_inputs(used)
    games_covered = sum(int(row.get("games_covered") or 0) for row in used)

    merged = dict(line)
    line_games = merged.pop("games_played", None)
    if week is not None:
        line_games = 1 if line else None
    sources = {name: "line" for name, value in merged.items() if name != "team_id" and value is not None}
    for name, value in career.items():
        if value is not None:
            merged[name] = value
            sources[name] = "career"

    targets, receptions = merged.get("targets"), merged.get("receptions")
    if targets is not None and receptions is not None and receptions > targets:
        periods = f"week {week}" if week is not None else "season"
        _add_once(invalid, f"{periods}: {receptions:g} receptions exceed {targets:g} targets")
        bad_targets = True

    if week is None:
        game_weeks = sorted({w for row in used for w in _row_weeks(row)})
        if game_weeks:
            bound, denominator_reason = _denominator_games(game_weeks, game_teams, games_by_team)
            if denominator_reason is None:
                denominator_reason = _denominator_reason(games_covered, [game for _, game in bound])
        else:
            bound, denominator_reason = [], "no Career game covered"
        pairs = [(team_id, game["game_id"]) for team_id, game in bound]
    else:
        played = [game for game in team_games if int(game.get("week") or 0) == week]
        pairs = [(game["team_id"], game["game_id"]) for game in played]
        if not schedule_known:
            denominator_reason = "no stored team schedule"
        elif not played:
            denominator_reason = f"week {week}: no game in the team's stored schedule"
        elif not all(game.get("has_box") for game in played):
            denominator_reason = f"week {week}: missing team box"
        elif games_covered > len(played):
            # The same completeness invariant as the season: the numerator cannot claim more games than the
            # week's stored team game (audit-5 stats3 R1).
            denominator_reason = (f"week {week}: Career rows cover {games_covered} games but the week has "
                                  f"{len(played)} stored team game")
        else:
            denominator_reason = None
    merged.update(_team_totals(cur, pairs))
    if denominator_reason is not None:
        for name in TEAM_COLUMNS:
            merged[name] = None

    metrics = compute(merged)
    mixed = False
    if line_games is not None and games_covered != line_games:
        for metric in METRICS.values():
            if metric.status != OK:
                continue
            metric_sources = {sources.get(name) for name in metric.inputs}
            if "career" in metric_sources and "line" in metric_sources:
                metrics[metric.key] = None
                mixed = True
    if bad_targets:
        # A target counter contradicted by the receptions is not published as a rate (never clamp to 1).
        for metric in METRICS.values():
            if metric.status == OK and "targets" in metric.inputs:
                metrics[metric.key] = None

    if week is None:
        same_games = line_games is None or games_covered == line_games
    else:
        same_games = denominator_reason is None and games_covered == 1
    coverage = {
        "games_covered": games_covered,
        "stat_line_games": line_games,
        "same_games": same_games,
        "untracked_weeks": untracked,
        "games_lost": games_lost,
        "mixed_sources": mixed,
        "week": week,
        "invalid_inputs": invalid,
        "team_denominator": denominator_reason,
        "reasons": reasons,
    }
    return {"inputs": merged, "metrics": metrics, "weeks_unknown": weeks_unknown, "coverage": coverage}


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
    cov = result["coverage"]
    line_note = "" if cov["stat_line_games"] is None else f", stat-line games: {cov['stat_line_games']}"
    mixed_note = ", MIXED SOURCES" if cov["mixed_sources"] else ""
    print(f"Advanced metrics for player {player_id}, {where} "
          f"(weeks unknown: {result['weeks_unknown']}, games covered: {cov['games_covered']}"
          f"{line_note}{mixed_note})")
    if cov["reasons"]:
        print("\nCOVERAGE")
        for reason in cov["reasons"]:
            print(f"  - {reason}")
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
