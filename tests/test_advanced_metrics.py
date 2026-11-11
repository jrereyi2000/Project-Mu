"""Unit tests for engines/advanced_metrics.py (MADDEN_STATS_INGEST_PLAN U3).

Offline only: `player_metrics` is exercised against a stub cursor, never a database.
"""

import pytest

from engines import advanced_metrics as am

# Every doc §4 metric, by its doc number, mapped to the module key(s) that encode it. 4.1.5's original
# receiver aDOT is impossible and stays in the catalog as `rec_adot`; the tracked version is QB aDOT.
DOC_METRICS = {
    "4.1.1": {"tprr"},
    "4.1.2": {"yprr"},
    "4.1.3": {"target_share"},
    "4.1.4": {"route_participation"},
    "4.1.5": {"qb_adot", "rec_adot"},
    "4.1.6": {"racr"},
    "4.1.7": {"catch_rate", "drop_rate"},
    "4.1.8": {"separation_index"},
    "4.2.1": {"p2s"},
    "4.2.2": {"pressure_rate"},
    "4.2.3": {"deep_rate"},
    "4.2.4": {"depth_split_efficiency"},
    "4.2.5": {"pocket_split"},
    "4.2.6": {"blitz_rating"},
    "4.2.7": {"scramble_rate"},
    "4.3.1": {"rush_success_rate"},
    "4.3.2": {"yac_per_att"},
    "4.3.3": {"btk_rate"},
    "4.3.4": {"explosive_rate"},
    "4.3.5": {"loaded_box_rate", "light_box_rate"},
    "4.3.6": {"concept_split"},
    "4.4.1": {"coverage_rating", "coverage_rating_lite"},
    "4.4.2": {"burn_pct"},
    "4.4.3": {"prate"},
    "4.4.4": {"sack_conversion_rate"},
    "4.4.5": {"stop_rate", "stuff_rate", "stuff_rate_all_snaps"},
    "4.4.6": {"press_frequency"},
    "4.5.1": {"pass_block_sack_rate"},
    "4.5.2": {"pancake_rate"},
    "4.5.3": {"snap_share"},
    "4.5.4": {"tb_pct"},
}

DOC_KEYS = {key for keys in DOC_METRICS.values() for key in keys}
OK_KEYS = {m.key for m in am.METRICS.values() if m.status == am.OK}

# One row with a non-NULL value for every input of every OK metric, and non-zero denominators.
FULL_ROW = {
    "targets": 136,
    "team_pass_attempts": 544,
    "receptions": 109,
    "rec_drops": 7,
    "pass_sacks": 10,
    "pressures_faced": 50,
    "pass_attempts": 240,
    "air_yards": 720,
    "pocket_in_att": 200, "pocket_in_cmp": 140, "pocket_in_yds": 1600,
    "pocket_in_td": 14, "pocket_in_int": 4,
    "pocket_out_att": 40, "pocket_out_cmp": 24, "pocket_out_yds": 300,
    "pocket_out_td": 2, "pocket_out_int": 1,
    "rush_attempts": 100, "rush_success": 45, "rush_yac": 250, "rush_btk": 15,
    "rush_10plus": 12, "rush_20plus": 4,
    "loaded_box": 20, "light_box": 30,
    "def_targets": 90, "def_catches_allowed": 54, "def_ints": 3,
    "def_sacks": 5, "def_pressures": 25,
    "stops": 27, "stuffs": 9, "downs_played": 900,
    "team_total_plays": 1000,
    "kick_kickoffs": 40, "kick_touchbacks": 25,
}


class StubCursor:
    """Returns the prepared rows in order and records each execute()."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.calls = []

    def execute(self, sql, params=None):
        self.calls.append((sql, params))

    def fetchone(self):
        assert self.rows, "unexpected fetchone"
        return self.rows.pop(0)


def _blank(columns):
    return {name: None for name in columns}


def test_every_doc_metric_has_a_metric_entry():
    assert DOC_KEYS == set(am.METRICS), "doc §4 and METRICS disagree"
    for number, keys in DOC_METRICS.items():
        for key in keys:
            assert key in am.METRICS, f"doc §4.{number} has no METRICS entry ({key})"
    assert 30 <= len(am.METRICS) <= 40, "the catalog should hold about 35 metrics"


def test_metric_status_shape_and_statuses():
    rows = am.metric_status()
    assert [row["key"] for row in rows] == list(am.METRICS)
    for row in rows:
        assert set(row) == {"key", "label", "status", "reason"}
        assert row["status"] in am.STATUSES
        assert row["label"].strip()
    counts = {status: sum(1 for row in rows if row["status"] == status) for status in am.STATUSES}
    assert counts[am.OK] > 0 and counts[am.BLOCKED] > 0 and counts[am.IMPOSSIBLE] > 0


def test_every_ok_metric_computes_on_a_full_row():
    result = am.compute(FULL_ROW)
    assert set(result) == OK_KEYS
    for key, value in result.items():
        assert value is not None, f"{key} did not compute on a full row"

    # The plan's named known answers.
    assert result["catch_rate"] == pytest.approx(109 / 136)
    assert result["qb_adot"] == pytest.approx(720 / 240)
    assert result["p2s"] == pytest.approx(10 / 50)
    assert result["burn_pct"] == pytest.approx(54 / 90)
    assert result["stop_rate"] == pytest.approx(27 / 900)

    # Spot checks for the other computed counters.
    assert result["drop_rate"] == pytest.approx(7 / (109 + 7))
    assert result["target_share"] == pytest.approx(136 / 544)
    assert result["pressure_rate"] == pytest.approx(50 / (240 + 10))
    assert result["yac_per_att"] == pytest.approx(250 / 100)
    assert result["btk_rate"] == pytest.approx(15 / 100)
    assert result["explosive_rate"] == pytest.approx((12 + 4) / 100)
    assert result["loaded_box_rate"] == pytest.approx(20 / 100)
    assert result["light_box_rate"] == pytest.approx(30 / 100)
    assert result["sack_conversion_rate"] == pytest.approx(5 / 25)
    assert result["stuff_rate_all_snaps"] == pytest.approx(9 / 900)
    assert result["snap_share"] == pytest.approx(900 / 1000)
    assert result["tb_pct"] == pytest.approx(25 / 40)

    # Catch-and-INT rating: (a + 0 + 0 + d) / 6 * 100 with comp% 0.60 and 3 INTs in 90 targets.
    assert result["coverage_rating_lite"] == pytest.approx(50.7)
    # Pocket disparity is the inside rating minus the outside rating.
    assert result["pocket_split"] == pytest.approx(108.8 - 89.6)


def test_zero_denominator_gives_none():
    row = dict(FULL_ROW, targets=0, team_pass_attempts=0, pressures_faced=0, downs_played=0,
               def_targets=0, kick_kickoffs=0, team_total_plays=0)
    result = am.compute(row)
    for key in ("catch_rate", "target_share", "p2s", "burn_pct", "stop_rate", "stuff_rate_all_snaps",
                "snap_share", "tb_pct"):
        assert result[key] is None, f"{key} should be None on a zero denominator"


def test_missing_input_gives_none():
    assert am.compute({})["catch_rate"] is None
    row = dict(FULL_ROW)
    row["air_yards"] = None
    assert am.compute(row)["qb_adot"] is None
    row = dict(FULL_ROW)
    row.pop("receptions")
    result = am.compute(row)
    assert result["catch_rate"] is None
    assert result["drop_rate"] is None
    # A metric whose inputs are intact still computes.
    assert result["p2s"] == pytest.approx(0.2)


def test_blocked_and_impossible_are_never_computed_and_explain_why():
    result = am.compute(FULL_ROW)
    for metric in am.METRICS.values():
        if metric.status == am.OK:
            assert metric.key in result
            continue
        assert metric.key not in result, f"{metric.key} ({metric.status}) must not be computed"
        assert metric.reason.strip(), f"{metric.key} ({metric.status}) needs a reason"


def test_player_metrics_merges_counters_stat_line_and_team_totals():
    counters = _blank(am.COUNTER_COLUMNS)
    counters.update({"targets": 136, "air_yards": 720, "pressures_faced": 50,
                     "downs_played": 900, "def_targets": 90, "def_pressures": 25,
                     "stops": 27, "stuffs": 9, "weeks_unknown": 2})
    line = _blank(am.STAT_COLUMNS)
    line.update({"team_id": 7, "receptions": 109, "rec_drops": 7, "pass_attempts": 240,
                 "pass_sacks": 10, "rush_attempts": 100, "rush_success": 45, "rush_yac": 250,
                 "rush_btk": 15, "rush_10plus": 12, "rush_20plus": 4, "loaded_box": 20,
                 "def_catches_allowed": 54, "def_ints": 3, "def_sacks": 5,
                 "downs_played": 600, "kick_kickoffs": 40, "kick_touchbacks": 25})
    team = {"team_pass_attempts": 544, "team_total_plays": 1000}
    cur = StubCursor([counters, line, team])

    result = am.player_metrics(cur, 2027, 42)

    # The Career delta wins where both sides carry the same column; the stat line fills the rest.
    assert result["inputs"]["downs_played"] == 900
    assert result["inputs"]["targets"] == 136
    assert result["inputs"]["receptions"] == 109
    assert result["inputs"]["team_pass_attempts"] == 544
    assert result["inputs"]["team_total_plays"] == 1000
    assert result["weeks_unknown"] == 2
    assert result["metrics"]["catch_rate"] == pytest.approx(109 / 136)
    assert result["metrics"]["snap_share"] == pytest.approx(900 / 1000)
    assert result["metrics"]["stop_rate"] == pytest.approx(27 / 900)

    assert "player_season_counters" in cur.calls[0][0]
    assert "season_stat_lines" in cur.calls[1][0]
    assert "team_stats" in cur.calls[2][0]
    assert cur.calls[2][1]["team_id"] == 7


def _tuple_row(columns, values):
    return tuple(values.get(name) for name in columns)


def test_player_metrics_accepts_positional_rows():
    """Ordinary psycopg cursors return tuples, not dicts."""
    counters = _tuple_row(("weeks_unknown",) + am.COUNTER_COLUMNS,
                          {"weeks_unknown": 1, "targets": 136})
    line = _tuple_row(("team_id",) + am.STAT_COLUMNS, {"team_id": 7, "receptions": 109})
    team = (544, 1000)
    cur = StubCursor([counters, line, team])

    result = am.player_metrics(cur, 2027, 42)

    assert result["inputs"]["targets"] == 136
    assert result["inputs"]["team_pass_attempts"] == 544
    assert result["weeks_unknown"] == 1
    assert result["metrics"]["catch_rate"] == pytest.approx(109 / 136)
    assert result["metrics"]["target_share"] == pytest.approx(136 / 544)


def test_player_metrics_week_uses_week_views_and_marks_unknown_weeks():
    counters = _blank(am.COUNTER_COLUMNS)
    counters.update({"status": "TABLE_CHANGED", "targets": 10, "downs_played": 60})
    line = _blank(am.STAT_COLUMNS)
    line.update({"team_id": 7, "receptions": 6, "pass_attempts": 30, "downs_played": 55})
    team = {"team_pass_attempts": 35, "team_total_plays": 70}
    cur = StubCursor([counters, line, team])

    result = am.player_metrics(cur, 2027, 42, week=3)

    assert "player_week_counters" in cur.calls[0][0]
    assert "game_stat_lines" in cur.calls[1][0]
    assert cur.calls[0][1]["week"] == 3
    assert result["weeks_unknown"] == 1
    assert result["inputs"]["downs_played"] == 60
    assert result["metrics"]["catch_rate"] == pytest.approx(6 / 10)

    # An OK week is not counted as unknown.
    counters_ok = dict(counters, status="OK")
    cur = StubCursor([counters_ok, line, team])
    assert am.player_metrics(cur, 2027, 42, week=3)["weeks_unknown"] == 0
