"""Pure helpers of engines/tuning/decision_targets.py (CLOSE_OUT_CHECKLIST P3-06).

No nflverse download: every case is a synthetic list of rows.
"""
from engines.tuning import decision_targets as D


def test_group_of_maps_madden_families():
    assert D.group_of("QB") == "QB"
    assert D.group_of("FB") == "RB"
    assert D.group_of("OT") == "OL"
    assert D.group_of("DE") == "DL"
    assert D.group_of("OLB") == "LB"
    assert D.group_of("FS") == "DB"
    assert D.group_of("K") == "K"
    assert D.group_of("LS") is None
    assert D.group_of(None) is None


def test_band_quartiles():
    b = D.band(range(1, 101))
    assert b["n"] == 100
    assert b["median"] == 51
    assert b["p25"] < b["median"] < b["p75"] < b["p90"]


def test_band_ignores_none_and_reports_small_cohorts():
    assert D.band([None, 3, None])["n"] == 1
    assert "median" not in D.band([4])


def test_share_at_least():
    assert D.share_at_least([1, 2, 3, 10], 3) == 0.5
    assert D.share_at_least([], 3) is None


def test_season_band_reports_lo_median_hi():
    b = D.season_band({2015: 7, 2016: 4, 2017: 10})
    assert (b["lo"], b["median"], b["hi"]) == (4, 7, 10)
    assert b["by_season"]["2017"] == 10


def test_coach_changes_between_and_midseason():
    rows = [
        # team A: coach X both seasons -> no change
        (2015, 1, "AAA", "X"), (2015, 2, "AAA", "X"),
        (2016, 1, "AAA", "X"),
        # team B: X last season, Y this season -> one change, counted at 2016
        (2015, 1, "BBB", "X"),
        (2016, 1, "BBB", "Y"),
        # team C: two coaches inside 2015 -> one mid-season change
        (2015, 1, "CCC", "X"), (2015, 2, "CCC", "Z"),
        (2016, 1, "CCC", "Z"),
    ]
    between, mid = D.coach_changes(rows, range(2015, 2017))
    assert between["by_season"] == {"2015": 0, "2016": 1}
    assert mid["by_season"] == {"2015": 1, "2016": 0}


def test_trade_counts_groups_assets_by_trade_id():
    rows = [
        {"season": "2016", "trade_id": "1", "pfr_id": "PlayA"},
        {"season": "2016", "trade_id": "1", "pfr_id": None},
        {"season": "2016", "trade_id": "2", "pfr_id": None},   # pick-only
        {"season": "2017", "trade_id": "3", "pfr_id": "PlayB"},
    ]
    t = D.trade_counts(rows, [2016, 2017])
    assert t["all"]["by_season"] == {"2016": 2, "2017": 1}
    assert t["with_player"]["by_season"] == {"2016": 1, "2017": 1}
    assert "in_season" not in t          # no windows given


def test_season_windows_from_game_dates():
    w = D.season_windows([("2016", "2016-09-08"), ("2016", "2017-02-05"),
                          ("2017", "2017-09-07"), (None, "2017-01-01")])
    assert w[2016] == (D.dt.date(2016, 9, 8), D.dt.date(2017, 2, 5))
    assert w[2017] == (D.dt.date(2017, 9, 7), D.dt.date(2017, 9, 7))


def test_player_careers_counts_active_not_cut():
    rows = [
        {"gsis_id": "a", "season": "2016", "status": "ACT", "position": "QB"},
        {"gsis_id": "a", "season": "2017", "status": "RES", "position": "QB"},
        {"gsis_id": "b", "season": "2016", "status": "CUT", "position": "RB"},
        {"gsis_id": "c", "season": "2016", "status": "ACT", "position": "WR"},
        {"gsis_id": "c", "season": "2016", "status": "CUT", "position": "WR"},
    ]
    c = D.player_careers(rows)
    assert c["a"]["active"] == {2016, 2017}
    assert c["b"]["active"] == set() and c["b"]["any"] == {2016}
    assert c["c"]["active"] == {2016}          # one active appearance is enough; CUT repeats don't add seasons


def test_career_tables_split_populations():
    careers = {f"d{i}": {"active": {2016, 2017}, "any": {2016, 2017}, "pos": "QB"} for i in range(20)}
    careers.update({f"u{i}": {"active": {2016}, "any": {2016}, "pos": "QB"} for i in range(20)})
    drafted = {f"d{i}" for i in range(20)}
    tables = D.career_tables(careers, drafted, range(2015, 2026))
    assert tables["drafted"]["QB"]["n"] == 20 and tables["drafted"]["QB"]["mean"] == 2.0
    assert tables["undrafted"]["QB"]["n"] == 20 and tables["undrafted"]["QB"]["mean"] == 1.0
    assert tables["all_players"]["QB"]["n"] == 40


def test_career_tables_drops_cohorts_below_the_minimum():
    careers = {f"d{i}": {"active": {2016}, "any": {2016}, "pos": "QB"} for i in range(3)}
    tables = D.career_tables(careers, set(careers), range(2015, 2026))
    assert tables["drafted"] == {}


def test_cut_only_summary_matches_the_quoted_average():
    careers = {
        "a": {"active": {2015, 2016, 2017, 2018}, "any": {2015, 2016, 2017, 2018}, "pos": "QB"},
        "b": {"active": set(), "any": {2016}, "pos": "QB"},
    }
    s = D.cut_only_summary(careers, range(2015, 2026))
    assert s["n_active"] == 1 and s["cut_only"] == 1
    assert s["mean_active_only"] == 4.0
    assert s["mean_including_cut_only"] == 2.0


def test_trade_counts_split_in_season_and_offseason():
    windows = {2016: (D.dt.date(2016, 9, 8), D.dt.date(2017, 2, 5))}
    rows = [
        {"season": "2016", "trade_id": "1", "pfr_id": "A", "trade_date": "2016-10-25"},   # in season
        {"season": "2016", "trade_id": "2", "pfr_id": None, "trade_date": "2016-04-30"},  # draft-day
        {"season": "2016", "trade_id": "3", "pfr_id": None, "trade_date": "2017-03-10"},  # March
    ]
    t = D.trade_counts(rows, [2016], windows)
    assert t["in_season"]["by_season"] == {"2016": 1}
    assert t["offseason"]["by_season"] == {"2016": 2}
    assert t["all"]["by_season"] == {"2016": 3}
