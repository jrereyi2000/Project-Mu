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
WEEKS_UNKNOWN_STATUSES = ("TABLE_CHANGED", "RESET", "CROSS_SEASON")
# A tracked Madden week's inside+outside-pocket attempts account for its PASSATTEMPTS. The audit measured
# 1.000 for fully tracked teams and 0.49-0.84 for teams missing week 1's pocket data, so 0.9 separates them
# (audit H3).
TRACKED_POCKET_RATIO = 0.9


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


def _career_rows(cur, season: int, player_id: int, week: int | None) -> list[dict]:
    """Every Career-counter row for the player in the season (crediting the season) or the one week.

    Every status is returned so `player_metrics` can count unknown weeks and lost games; only OK and
    NO_GAMES deltas are ever used.
    """
    columns = ("season", "week", "prev_season", "prev_week", "status", "delta", "games_covered")
    if week is None:
        sql = (f"SELECT {', '.join(columns)} FROM player_week_counters "
               "WHERE credited_season = %(season)s AND player_id = %(player_id)s ORDER BY load_seq")
        params = {"season": season, "player_id": player_id}
    else:
        sql = (f"SELECT {', '.join(columns)} FROM player_week_counters "
               "WHERE season = %(season)s AND week = %(week)s AND player_id = %(player_id)s "
               "AND stage = 'WEEK' ORDER BY load_seq")
        params = {"season": season, "week": week, "player_id": player_id}
    return _fetch_rows(cur, columns, sql, params)


def _team_tracking(cur, team_id, season: int) -> dict:
    """The team's tracked pocket attempts per career-row range: {(week, prev_week, prev_season): (att, pocket)}.

    Summed over every usable Career delta of the team's players, so a receiver's row is checked against the
    team's pass attempts (a receiver's own `PASSATTEMPTS` is zero).
    """
    if team_id is None:
        return {}
    sql = """
        SELECT w.week, w.prev_week, w.prev_season,
               COALESCE(SUM((w.delta ->> 'PASSATTEMPTS')::int), 0) AS pass_att,
               COALESCE(SUM((w.delta ->> 'INSIDEPOCKETTHROW_ATTEMPTS')::int), 0)
             + COALESCE(SUM((w.delta ->> 'OUTSIDEPOCKETTHROW_ATTEMPTS')::int), 0) AS pocket_att
        FROM player_week_counters w
        JOIN player_seasons ps ON ps.player_id = w.player_id AND ps.season = w.credited_season
        WHERE ps.team_id = %(team_id)s AND w.credited_season = %(season)s
          AND w.status IN ('OK', 'NO_GAMES') AND w.delta IS NOT NULL
        GROUP BY w.week, w.prev_week, w.prev_season"""
    params = {"team_id": team_id, "season": season}
    columns = ("week", "prev_week", "prev_season", "pass_att", "pocket_att")
    return {(row["week"], row["prev_week"], row["prev_season"]): (row["pass_att"], row["pocket_att"])
            for row in _fetch_rows(cur, columns, sql, params)}


def _is_untracked(row: dict, team_tracking: dict) -> bool:
    """True when Madden did not track this row's games: the team's tracked pocket attempts do not account
    for its pass attempts."""
    key = (row.get("week"), row.get("prev_week"), row.get("prev_season"))
    pass_att, pocket_att = team_tracking.get(key, (0, 0))
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


def _team_totals(cur, team_id, season: int, weeks: list[int]) -> dict:
    """Team denominators over the same game weeks as the numerator (`weeks`; empty means no games)."""
    if team_id is None:
        return {name: None for name in TEAM_COLUMNS}
    sql = """
        SELECT COALESCE(SUM(ts.passing_attempts), 0) AS team_pass_attempts,
               COALESCE(SUM(ts.total_plays), 0) AS team_total_plays
        FROM team_stats ts
        JOIN games g ON g.id = ts.game_id
        JOIN teams t ON t.name = ts.team_name
        WHERE t.id = %(team_id)s AND g.season = %(season)s
          AND g.week = ANY(%(weeks)s)"""
    params = {"team_id": team_id, "season": season, "weeks": list(weeks)}
    return _fetch_row(cur, TEAM_COLUMNS, sql, params)


def player_metrics(cur, season: int, player_id: int, week: int | None = None) -> dict:
    """One player's inputs, OK metric values and coverage.

    Every value comes from the Career deltas of the games it is meant to describe: the season sums the
    player's usable `player_week_counters` rows credited to `season`, a week reads its WEEK-stage row. A
    BASELINE, PARTIAL, TABLE_CHANGED, RESET, CROSS_SEASON or Madden-untracked row is not used; unknown and
    untracked rows count in `weeks_unknown` the way the season view counts unknown weeks. The stat line
    supplies only a counter the Career row does not carry, and a metric that would mix the two sources over
    different game counts is None. `coverage` reports games covered, stat-line games, untracked weeks and
    lost games, plus `mixed_sources` when such a metric was suppressed.
    """
    line = _line_row(cur, season, player_id, week)
    team_id = line.get("team_id")
    rows = _career_rows(cur, season, player_id, week)
    team_tracking = _team_tracking(cur, team_id, season)

    used: list[dict] = []
    weeks_unknown = 0
    untracked = 0
    games_lost = 0
    for row in rows:
        status = row.get("status")
        if status in WEEKS_UNKNOWN_STATUSES:
            weeks_unknown += 1
        elif status == "PARTIAL":
            games_lost += int(row.get("games_covered") or 0)
        if status in USABLE_STATUSES and row.get("delta"):
            if _is_untracked(row, team_tracking):
                untracked += 1
                weeks_unknown += 1
            else:
                used.append(row)

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

    if week is None:
        game_weeks = sorted({w for row in used
                             for w in range(max(int(row.get("prev_week") or 0), 0) + 1,
                                            int(row["week"]) + 1) if w >= 1})
    else:
        game_weeks = [week]
    merged.update(_team_totals(cur, team_id, season, game_weeks))

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

    coverage = {
        "games_covered": games_covered,
        "stat_line_games": line_games,
        "same_games": line_games is None or games_covered == line_games,
        "untracked_weeks": untracked,
        "games_lost": games_lost,
        "mixed_sources": mixed,
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
