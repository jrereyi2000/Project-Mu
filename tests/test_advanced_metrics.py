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
    """Serves the prepared results in order: a list for fetchall(), anything else for fetchone()."""

    def __init__(self, results):
        self.results = list(results)
        self.calls = []
        self._current = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        self._current = self.results.pop(0)

    def fetchone(self):
        assert not isinstance(self._current, list), "unexpected fetchone on a row list"
        return self._current

    def fetchall(self):
        assert isinstance(self._current, list), "unexpected fetchall on a single row"
        return list(self._current)


def _blank(columns):
    return {name: None for name in columns}


def _tuple_row(columns, values):
    return tuple(values.get(name) for name in columns)


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
    # Audit M1 moved five metrics whose counters contradict their premise to BLOCKED.
    assert counts == {am.OK: 15, am.BLOCKED: 8, am.IMPOSSIBLE: 14}


def test_m1_blocked_metrics_carry_the_counter_evidence():
    for key in ("explosive_rate", "coverage_rating_lite", "burn_pct", "sack_conversion_rate", "snap_share"):
        metric = am.METRICS[key]
        assert metric.status == am.BLOCKED, key
        assert "audit M1" in metric.reason, key


def test_every_ok_metric_computes_on_a_full_row():
    result = am.compute(FULL_ROW)
    assert set(result) == OK_KEYS
    for key, value in result.items():
        assert value is not None, f"{key} did not compute on a full row"

    # The plan's named known answers.
    assert result["catch_rate"] == pytest.approx(109 / 136)
    assert result["qb_adot"] == pytest.approx(720 / 240)
    assert result["p2s"] == pytest.approx(10 / 50)
    assert result["stop_rate"] == pytest.approx(27 / 900)

    # Spot checks for the other computed counters.
    assert result["drop_rate"] == pytest.approx(7 / (109 + 7))
    assert result["target_share"] == pytest.approx(136 / 544)
    assert result["pressure_rate"] == pytest.approx(50 / (240 + 10))
    assert result["yac_per_att"] == pytest.approx(250 / 100)
    assert result["btk_rate"] == pytest.approx(15 / 100)
    assert result["loaded_box_rate"] == pytest.approx(20 / 100)
    assert result["light_box_rate"] == pytest.approx(30 / 100)
    assert result["stuff_rate_all_snaps"] == pytest.approx(9 / 900)
    assert result["tb_pct"] == pytest.approx(25 / 40)

    # Pocket disparity is the inside rating minus the outside rating.
    assert result["pocket_split"] == pytest.approx(108.8 - 89.6)


def test_zero_denominator_gives_none():
    row = dict(FULL_ROW, targets=0, team_pass_attempts=0, pressures_faced=0, downs_played=0,
               def_targets=0, kick_kickoffs=0, team_total_plays=0)
    result = am.compute(row)
    for key in ("catch_rate", "target_share", "p2s", "stop_rate", "stuff_rate_all_snaps", "tb_pct"):
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


def _career(week=4, prev_week=0, prev_season=2026, status="OK", games=4, **delta):
    row = {"season": 2026, "week": week, "prev_season": prev_season, "prev_week": prev_week,
           "status": status, "delta": delta or None, "games_covered": games}
    return row


def _tracking(pass_att, pocket_att, week=4, prev_week=0, prev_season=2026):
    return [{"week": week, "prev_week": prev_week, "prev_season": prev_season,
             "pass_att": pass_att, "pocket_att": pocket_att}]


def _games(weeks, has_box=True, first_id=100):
    """Team games (id, week, has_box) for the `_team_games` query."""
    return [{"game_id": first_id + i, "week": week, "has_box": has_box}
            for i, week in enumerate(weeks)]


def test_player_metrics_sums_career_deltas_and_uses_the_stat_line_only_for_missing_counters():
    # H3: receptions, targets, drops and attempts all come from the Career delta, which carries the full
    # box counters. The stat line's own receptions lose; only kicking (absent from the Career row) is taken
    # from the line.
    line = _blank(am.STAT_COLUMNS)
    line.update({"team_id": 7, "games_played": 4, "receptions": 100, "kick_kickoffs": 40,
                 "kick_touchbacks": 25})
    career = _career(
        TARGETS=136, RECEIVECATCHES=109, RECEIVEDROPS=7, PASSATTEMPTS=240, PASSSACKED=10,
        PRESSURES=50, AIRYARDS=720, RUSHATTEMPTS=100, RUSHSUCCESS=45, RUSHYARDSAFTER1STHIT=250,
        RUSHBROKENTACKLES=15, LIGHTBOX=30, LOADEDBOX=20, DOWNSPLAYED=900, STOPS=27, STUFFS=9,
        DEFTARGETS=90, CTHALLOWED=54, DSECINTS=3, DLINESACKS=4, DLINEHALFSACK=2, DEFPRESSURES=25,
        INSIDEPOCKETTHROW_ATTEMPTS=200, INSIDEPOCKETTHROW_COMPLETIONS=140, INSIDEPOCKETTHROW_YARDS=1600,
        INSIDEPOCKETTHROW_TD=14, INSIDEPOCKETTHROW_INTERCEPTIONS=4,
        OUTSIDEPOCKETTHROW_ATTEMPTS=40, OUTSIDEPOCKETTHROW_COMPLETIONS=24, OUTSIDEPOCKETTHROW_YARDS=300,
        OUTSIDEPOCKETTHROW_TD=2, OUTSIDEPOCKETTHROW_INTERCEPTIONS=1)
    team = {"team_pass_attempts": 544, "team_total_plays": 1000}
    cur = StubCursor([line, [career], _games([1, 2, 3, 4]), _tracking(240, 240), team])

    result = am.player_metrics(cur, 2026, 42)

    assert result["inputs"]["receptions"] == 109          # Career wins over the line's 100
    assert result["inputs"]["targets"] == 136
    assert result["inputs"]["rec_drops"] == 7
    assert result["inputs"]["kick_kickoffs"] == 40         # only the line carries kicking
    assert result["inputs"]["def_sacks"] == 5.0            # 4 full + 0.5 * 2 half
    assert result["inputs"]["team_pass_attempts"] == 544
    assert result["weeks_unknown"] == 0
    assert result["coverage"] == {"games_covered": 4, "stat_line_games": 4, "same_games": True,
                                  "untracked_weeks": 0, "games_lost": 0, "mixed_sources": False,
                                  "week": None, "invalid_inputs": [], "team_denominator": None,
                                  "reasons": []}
    assert result["metrics"]["catch_rate"] == pytest.approx(109 / 136)
    assert result["metrics"]["drop_rate"] == pytest.approx(7 / (109 + 7))
    assert result["metrics"]["target_share"] == pytest.approx(136 / 544)
    assert result["metrics"]["stop_rate"] == pytest.approx(27 / 900)
    assert result["metrics"]["tb_pct"] == pytest.approx(25 / 40)
    assert "coverage_rating_lite" not in result["metrics"]    # BLOCKED, never computed
    assert "snap_share" not in result["metrics"]              # BLOCKED, never computed

    assert "season_stat_lines" in cur.calls[0][0]
    assert "player_week_counters" in cur.calls[1][0]
    assert "games" in cur.calls[2][0]                         # the team's stored games
    assert "player_seasons" in cur.calls[3][0]
    assert "team_stats" in cur.calls[4][0]
    assert cur.calls[4][1]["team_id"] == 7
    assert cur.calls[4][1]["weeks"] == [1, 2, 3, 4]      # the Career rows' game weeks


def test_player_metrics_excludes_an_untracked_career_week():
    # H3: the team's tracked pocket attempts (100) do not account for its 138 pass attempts, so week 1 was
    # not tracked. The whole blob is untracked (a single multi-week delta cannot be split): no catch rate,
    # and the week is reported like an unknown week.
    line = _blank(am.STAT_COLUMNS)
    line.update({"team_id": 7, "games_played": 4, "receptions": 27, "kick_kickoffs": 40,
                 "kick_touchbacks": 25})
    career = _career(TARGETS=20, RECEIVECATCHES=27, PASSATTEMPTS=138,
                     INSIDEPOCKETTHROW_ATTEMPTS=60, OUTSIDEPOCKETTHROW_ATTEMPTS=40)
    cur = StubCursor([line, [career], _games([1, 2, 3, 4]), _tracking(138, 100),
                      {"team_pass_attempts": 138, "team_total_plays": 500}])

    result = am.player_metrics(cur, 2026, 2090)

    assert result["inputs"].get("targets") is None        # the untracked delta is not used
    assert result["inputs"]["receptions"] == 27            # the stat line still reports the box score
    assert result["inputs"]["team_pass_attempts"] is None  # no denominator without a Career game
    assert result["metrics"]["catch_rate"] is None
    assert result["weeks_unknown"] == 1
    assert result["coverage"]["untracked_weeks"] == 1
    assert result["coverage"]["games_covered"] == 0
    assert result["coverage"]["stat_line_games"] == 4
    assert result["coverage"]["same_games"] is False
    assert result["coverage"]["team_denominator"] == "no Career game covered"


def test_player_metrics_suppresses_a_metric_that_mixes_sources_over_different_game_counts():
    # A Career row with 4 games and a stat line with 5: drop rate would mix a Career reception count with a
    # line drop count, so it returns None and the coverage flag names the mix. Career-only and line-only
    # metrics still compute.
    line = _blank(am.STAT_COLUMNS)
    line.update({"team_id": 7, "games_played": 5, "rec_drops": 5, "kick_kickoffs": 40,
                 "kick_touchbacks": 25})
    career = _career(TARGETS=100, RECEIVECATCHES=60)
    cur = StubCursor([line, [career], _games([1, 2, 3, 4]), _tracking(240, 240),
                      {"team_pass_attempts": 300, "team_total_plays": 900}])

    result = am.player_metrics(cur, 2026, 42)

    assert result["metrics"]["catch_rate"] == pytest.approx(0.6)   # both sides Career
    assert result["metrics"]["drop_rate"] is None                  # Career receptions + line drops
    assert result["metrics"]["tb_pct"] == pytest.approx(25 / 40)   # line only
    assert result["coverage"]["mixed_sources"] is True
    assert result["coverage"]["same_games"] is False
    assert result["coverage"]["games_covered"] == 4
    assert result["coverage"]["stat_line_games"] == 5


def test_a_rollover_row_fills_the_team_denominator_of_the_season_it_is_credited_to():
    # audit-4 stats suspicion 1: a load taken before the next season's week 1 (2027 preseason week 0) carries
    # the 2026 playoffs and Super Bowl in its delta and is credited to 2026. The team denominators must cover
    # 2026 weeks 19-23, not an empty range.
    line = _blank(am.STAT_COLUMNS)
    line.update({"team_id": 7, "games_played": 5})
    career = _career(week=0, prev_week=18, games=5, TARGETS=20)
    cur = StubCursor([line, [career], _games([19, 20, 21, 22, 23]), [],
                      {"team_pass_attempts": 75, "team_total_plays": 180}])

    result = am.player_metrics(cur, 2026, 42)

    assert cur.calls[4][1]["weeks"] == [19, 20, 21, 22, 23]
    assert result["coverage"]["team_denominator"] is None
    assert result["inputs"]["team_pass_attempts"] == 75
    assert result["metrics"]["target_share"] == pytest.approx(20 / 75)


def test_the_season_team_supplies_the_denominator_when_the_stat_line_has_none():
    # audit-4 stats suspicion 1: the 2026 line of a player first seen in the next save may carry no team, so
    # the denominators read the player's season team instead of going None.
    line = _blank(am.STAT_COLUMNS)
    line.update({"team_id": None, "games_played": 3})
    career = _career(week=3, prev_week=0, games=3, TARGETS=15)
    cur = StubCursor([line, {"team_id": 9}, [career], _games([1, 2, 3]), [],
                      {"team_pass_attempts": 120, "team_total_plays": 300}])

    result = am.player_metrics(cur, 2026, 42)

    assert "player_seasons" in cur.calls[1][0]
    assert cur.calls[5][1]["team_id"] == 9
    assert result["metrics"]["target_share"] == pytest.approx(15 / 120)


def test_player_metrics_accepts_positional_rows():
    """Ordinary psycopg cursors return tuples, not dicts."""
    line = _tuple_row(("team_id", "games_played") + am.STAT_COLUMNS,
                      {"team_id": 7, "games_played": 4, "receptions": 109})
    career = _tuple_row(("season", "week", "prev_season", "prev_week", "status", "delta", "games_covered"),
                        {"season": 2026, "week": 4, "prev_season": 2026, "prev_week": 0, "status": "OK",
                         "delta": {"TARGETS": 136, "RECEIVECATCHES": 109}, "games_covered": 4})
    tracking = (4, 0, 2026, 240, 240)
    games = [(101, 1, True), (102, 2, True), (103, 3, True), (104, 4, True)]
    team = (544, 1000)
    cur = StubCursor([line, [career], games, [tracking], team])

    result = am.player_metrics(cur, 2026, 42)

    assert result["inputs"]["targets"] == 136
    assert result["inputs"]["team_pass_attempts"] == 544
    assert result["weeks_unknown"] == 0
    assert result["metrics"]["catch_rate"] == pytest.approx(109 / 136)
    assert result["metrics"]["target_share"] == pytest.approx(136 / 544)


def test_player_metrics_week_reads_only_its_row_and_marks_unknown_weeks():
    # H1/H3: the week reads only a delta that covers exactly its game. A TABLE_CHANGED row is not a usable
    # delta, so its counters are not borrowed and the week counts as unknown; the line still reports the
    # box score.
    line = _blank(am.STAT_COLUMNS)
    line.update({"team_id": 7, "receptions": 6, "pass_attempts": 30, "downs_played": 55})
    career = _career(week=3, prev_week=2, status="TABLE_CHANGED", games=1,
                     TARGETS=10, RECEIVECATCHES=6)
    career["delta"] = None
    team = {"team_pass_attempts": 35, "team_total_plays": 70}
    cur = StubCursor([line, [career], _games([3]), [], team])

    result = am.player_metrics(cur, 2026, 42, week=3)

    assert "game_stat_lines" in cur.calls[0][0]
    assert "player_week_counters" in cur.calls[1][0]
    assert "credited_season" in cur.calls[1][0]          # the whole credited season, not one label
    assert result["weeks_unknown"] == 1
    assert result["inputs"]["receptions"] == 6
    assert result["inputs"].get("targets") is None
    assert result["metrics"]["catch_rate"] is None
    assert cur.calls[4][1]["weeks"] == [3]

    # An OK, tracked week is not counted as unknown.
    career_ok = _career(week=3, prev_week=2, status="OK", games=1, TARGETS=10, RECEIVECATCHES=6)
    cur = StubCursor([line, [career_ok], _games([3]), _tracking(30, 30, week=3, prev_week=2), team])
    result = am.player_metrics(cur, 2026, 42, week=3)
    assert result["weeks_unknown"] == 0
    assert result["coverage"]["games_covered"] == 1
    assert result["coverage"]["same_games"] is True
    assert result["metrics"]["catch_rate"] == pytest.approx(6 / 10)


def test_a_bye_week_never_borrows_the_delta_that_spans_it():
    # audit-5 S3: loads at weeks 8 and 10 leave one delta labelled 10 (prev_week 8). The team played only
    # week 10, so it is week 10's game; asking for the bye week 9 must not report it or claim same games.
    career = _career(week=10, prev_week=8, games=1, TARGETS=5, RECEIVECATCHES=2)
    games = _games([10])
    tracking = _tracking(30, 30, week=10, prev_week=8)
    team = {"team_pass_attempts": 30, "team_total_plays": 70}

    bye = am.player_metrics(StubCursor([None, {"team_id": 7}, [career], games, tracking, team]),
                            2026, 42, week=9)
    assert bye["inputs"].get("receptions") is None
    assert bye["inputs"].get("targets") is None
    assert bye["coverage"]["games_covered"] == 0
    assert bye["coverage"]["same_games"] is False
    assert any("no game" in reason for reason in bye["coverage"]["reasons"])
    assert bye["coverage"]["team_denominator"] == "week 9: no game in the team's stored schedule"

    played = am.player_metrics(StubCursor([None, {"team_id": 7}, [career], games, tracking, team]),
                               2026, 42, week=10)
    assert played["inputs"]["targets"] == 5
    assert played["inputs"]["receptions"] == 2
    assert played["coverage"]["games_covered"] == 1
    assert played["coverage"]["same_games"] is True
    assert played["metrics"]["catch_rate"] == pytest.approx(2 / 5)


def test_an_unsplittable_multi_week_delta_leaves_the_week_to_the_stat_line():
    # audit-5 S3: one delta covers weeks 11 and 12. Asking for week 11 cannot split the 26 targets over two
    # games, so the week's counters come from the box score and the tracked inputs stay None with a reason.
    line = _blank(am.STAT_COLUMNS)
    line.update({"team_id": 7, "receptions": 8, "rec_yards": 73})
    career = _career(week=12, prev_week=10, games=2, TARGETS=26, RECEIVECATCHES=15, RECEIVEYARDS=143)
    cur = StubCursor([line, [career], _games([11, 12]), [],
                      {"team_pass_attempts": 45, "team_total_plays": 110}])

    result = am.player_metrics(cur, 2026, 42, week=11)

    assert result["inputs"]["receptions"] == 8
    assert result["inputs"]["rec_yards"] == 73
    assert result["inputs"].get("targets") is None
    assert result["inputs"].get("rec_drops") is None
    assert result["metrics"]["catch_rate"] is None
    assert result["metrics"]["target_share"] is None
    assert result["coverage"]["games_covered"] == 0
    assert result["coverage"]["same_games"] is False
    assert any("cannot be split" in reason for reason in result["coverage"]["reasons"])


def test_a_missing_team_box_withholds_the_week_denominator():
    # audit-5 S1/S3: a stored game without its team box cannot divide any share.
    line = _blank(am.STAT_COLUMNS)
    line.update({"team_id": 7, "receptions": 6})
    career = _career(week=5, prev_week=4, games=1, TARGETS=9, RECEIVECATCHES=6)
    cur = StubCursor([line, [career], _games([5], has_box=False), _tracking(35, 35, week=5, prev_week=4),
                      {"team_pass_attempts": 35, "team_total_plays": 80}])

    result = am.player_metrics(cur, 2026, 42, week=5)

    assert result["metrics"]["catch_rate"] == pytest.approx(6 / 9)
    assert result["metrics"]["target_share"] is None
    assert result["inputs"]["team_pass_attempts"] is None
    assert result["coverage"]["team_denominator"] == "week 5: missing team box"
    assert result["coverage"]["same_games"] is False


def test_a_week_without_a_stored_schedule_or_team_publishes_no_share():
    # A one-week delta can still describe its game, but with no stored schedule its identity cannot be
    # confirmed and no team denominator exists: the share is None and coverage says why.
    line = _blank(am.STAT_COLUMNS)
    line.update({"team_id": None, "receptions": 6})
    career = _career(week=5, prev_week=4, games=1, TARGETS=9, RECEIVECATCHES=6)
    cur = StubCursor([line, {"team_id": None}, [career]])   # no team: no games/tracking/totals query

    result = am.player_metrics(cur, 2026, 42, week=5)

    assert result["metrics"]["catch_rate"] == pytest.approx(6 / 9)
    assert result["metrics"]["target_share"] is None
    assert result["coverage"]["team_denominator"] == "no stored team schedule"
    assert result["coverage"]["same_games"] is False
    assert any("schedule" in reason for reason in result["coverage"]["reasons"])


def test_more_receptions_than_targets_publishes_no_catch_rate_or_share():
    # audit-5 S4: 11 receptions on 10 targets gave catch rate 1.1. The bad counter is reported, the rates
    # are None (never clamped), and a valid fully tracked receiver still computes.
    line = _blank(am.STAT_COLUMNS)
    line.update({"team_id": 7, "games_played": 4})
    career = _career(week=4, prev_week=0, games=4, TARGETS=10, RECEIVECATCHES=11)
    cur = StubCursor([line, [career], _games([1, 2, 3, 4]), _tracking(140, 140),
                      {"team_pass_attempts": 140, "team_total_plays": 300}])

    result = am.player_metrics(cur, 2026, 42)

    assert result["inputs"]["targets"] == 10
    assert result["inputs"]["receptions"] == 11
    assert result["metrics"]["catch_rate"] is None
    assert result["metrics"]["target_share"] is None
    assert result["coverage"]["invalid_inputs"]
    assert any("exceed" in item for item in result["coverage"]["invalid_inputs"])

    valid = _career(week=4, prev_week=0, games=4, TARGETS=10, RECEIVECATCHES=7)
    cur = StubCursor([line, [valid], _games([1, 2, 3, 4]), _tracking(140, 140),
                      {"team_pass_attempts": 140, "team_total_plays": 300}])
    result = am.player_metrics(cur, 2026, 42)
    assert result["metrics"]["catch_rate"] == pytest.approx(0.7)
    assert result["metrics"]["target_share"] == pytest.approx(10 / 140)
    assert result["coverage"]["invalid_inputs"] == []


def test_an_invalid_receiver_week_is_named_in_coverage():
    # audit-5 S4's week 11: a single-game delta with 11 receptions on 10 targets must not publish 1.1.
    line = _blank(am.STAT_COLUMNS)
    line.update({"team_id": 7})
    career = _career(week=11, prev_week=10, games=1, TARGETS=10, RECEIVECATCHES=11)
    cur = StubCursor([line, [career], _games([11]), _tracking(40, 40, week=11, prev_week=10),
                      {"team_pass_attempts": 40, "team_total_plays": 90}])

    result = am.player_metrics(cur, 2026, 42, week=11)

    assert result["metrics"]["catch_rate"] is None
    assert result["metrics"]["target_share"] is None
    assert any(item.startswith("week 11") for item in result["coverage"]["invalid_inputs"])


@pytest.mark.parametrize("terminal_week", [-1, -2])
def test_a_same_season_offseason_or_probowl_terminal_row_fills_the_tail(terminal_week):
    # A terminal row loaded in the same credited season (OFFSEASON -1, PROBOWL -2) covers the tail weeks.
    line = _blank(am.STAT_COLUMNS)
    line.update({"team_id": 7, "games_played": 5})
    career = _career(week=terminal_week, prev_week=18, games=5, TARGETS=20)
    cur = StubCursor([line, [career], _games([19, 20, 21, 22, 23]), [],
                      {"team_pass_attempts": 75, "team_total_plays": 180}])

    result = am.player_metrics(cur, 2026, 42)

    assert cur.calls[4][1]["weeks"] == [19, 20, 21, 22, 23]
    assert result["coverage"]["team_denominator"] is None
    assert result["metrics"]["target_share"] == pytest.approx(20 / 75)


def test_a_partial_season_tail_does_not_pull_earlier_weeks_into_the_denominator():
    # Only weeks 15-18 are covered by the Career rows, even though the team's whole season is stored: the
    # denominator is those four games, and completeness compares against them alone.
    line = _blank(am.STAT_COLUMNS)
    line.update({"team_id": 7, "games_played": 4})
    career = _career(week=18, prev_week=14, games=4, TARGETS=20)
    cur = StubCursor([line, [career], _games(range(1, 19)), [],
                      {"team_pass_attempts": 120, "team_total_plays": 300}])

    result = am.player_metrics(cur, 2026, 42)

    assert cur.calls[4][1]["weeks"] == [15, 16, 17, 18]
    assert result["coverage"]["team_denominator"] is None
    assert result["metrics"]["target_share"] == pytest.approx(20 / 120)


def test_an_incomplete_team_history_publishes_no_share():
    # audit-5 S1: the Career rows cover a full season but only the first nine games have team boxes, so the
    # denominator would be partial history. The share is None and coverage names the gap.
    line = _blank(am.STAT_COLUMNS)
    line.update({"team_id": 7, "games_played": 17})
    career = _career(week=18, prev_week=0, games=17, TARGETS=130)
    cur = StubCursor([line, [career], _games(range(1, 10)), [],
                      {"team_pass_attempts": 330, "team_total_plays": 800}])

    result = am.player_metrics(cur, 2026, 42)

    assert result["metrics"]["target_share"] is None
    assert result["inputs"]["team_pass_attempts"] is None
    assert "Career rows cover 17 games" in result["coverage"]["team_denominator"]


def test_a_player_inactive_for_a_team_game_keeps_the_share():
    # The player's Career rows cover three games, his club played four in the covered weeks: a DNP is not
    # partial team history, so the denominator over those weeks is used.
    line = _blank(am.STAT_COLUMNS)
    line.update({"team_id": 7, "games_played": 3})
    career = _career(week=4, prev_week=0, games=3, TARGETS=15)
    cur = StubCursor([line, [career], _games([1, 2, 3, 4]), [],
                      {"team_pass_attempts": 120, "team_total_plays": 300}])

    result = am.player_metrics(cur, 2026, 42)

    assert result["coverage"]["team_denominator"] is None
    assert result["metrics"]["target_share"] == pytest.approx(15 / 120)
