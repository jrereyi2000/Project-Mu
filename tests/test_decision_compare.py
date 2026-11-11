"""Decision comparison report (engines/tuning/decision_compare.py, CLOSE_OUT_CHECKLIST P3-07).

Pure extractors and the gate scoring are tested on synthetic rows; `extract` and `run` are tested against a
fake cursor, so no database is needed.
"""
import json

import pytest

from engines.tuning import decision_compare as C
from engines.tuning import decision_targets as D

GROUPS = D.GROUP_ORDER


def targets():
    return {
        "meta": {"seasons": [2015, 2025]},
        "career_length": {"drafted": {g: {"median": 5, "n": 20} for g in GROUPS}},
        "retirement_age": {"drafted": {g: {"median": 28, "n": 20} for g in GROUPS}},
        "coach_changes": {"between_seasons": {"lo": 4, "hi": 10},
                          "midseason": {"lo": 0, "hi": 3}},
        "trades": {"in_season": {"lo": 5, "hi": 21}, "offseason": {"lo": 45, "hi": 84}},
        "team_movement": {"rate": {"median": 0.249, "lo": 0.179, "hi": 0.265}},
        "draft_by_round": {str(r): {"share_started_2plus": v} for r, v in
                           zip(range(1, 8), (0.85, 0.65, 0.48, 0.34, 0.31, 0.15, 0.11))},
        "rookie_usage": {"median_games": 9.0, "share_8plus": 0.56, "share_12plus": 0.36},
    }


def observed(**over):
    obs = {
        "season": 2026,
        "meta": {"drafted_cohort": 200, "target_seasons": [2015, 2025]},
        "coach_changes": {"between_seasons": 7, "midseason": 2, "teams": 32},
        "trades": {"in_season": 13, "offseason": 70, "all": 83, "proposed": 100,
                   "by_status": {"APPROVED": 83, "EXPIRED": 17}},
        "career_length": {"drafted": {g: {"n": 20, "median": 5.0} for g in GROUPS},
                          "all_players": {g: {"n": 30, "median": 4.0} for g in GROUPS},
                          "careers": 200},
        "retirement_age": {"drafted": {g: {"n": 20, "median": 28.0} for g in GROUPS},
                           "all_players": {g: {"n": 30, "median": 27.0} for g in GROUPS},
                           "n": 200},
        "team_movement": {"from_season": 2025, "to_season": 2026, "eligible": 720, "moved": 180,
                          "rate": 0.25},
        "draft_by_round": {str(r): {"n": 30, "games_median": 50, "share_started_2plus": v}
                           for r, v in zip(range(1, 8), (0.85, 0.65, 0.48, 0.34, 0.31, 0.15, 0.11))},
        "rookie_usage": {"n": 200, "median_games": 9.0, "share_8plus": 0.56, "share_12plus": 0.36},
        "data": {"regular_weeks_run": 18, "completed_games": 272},
    }
    obs.update(over)
    return obs


# ------------------------------------------------------------------ position groups

def test_mu_group_of_maps_madden_positions():
    assert C.mu_group_of("LEDG") == "DL" and C.mu_group_of("REDG") == "DL"
    assert C.mu_group_of("MIKE") == "LB" and C.mu_group_of("WILL") == "LB" and C.mu_group_of("SAM") == "LB"
    assert C.mu_group_of("LT") == "OL" and C.mu_group_of("RG") == "OL"
    assert C.mu_group_of("HB") == "RB" and C.mu_group_of("FB") == "RB"
    assert C.mu_group_of("CB") == "DB" and C.mu_group_of("FS") == "DB"
    assert C.mu_group_of("LS") is None and C.mu_group_of(None) is None


# ------------------------------------------------------------------ pure extractors

def test_coach_changes_counts_hires_and_midseason():
    tenures = [
        (1, 10, 2024, None),          # team 1 keeps its coach
        (2, 20, 2022, 2026),          # team 2 fires: 21 takes over in 2027
        (2, 21, 2027, None),
        (3, 30, 2020, 2026),          # team 3's second coach starts inside 2026 -> mid-season
        (3, 31, 2026, None),
    ]
    out = C.coach_changes(tenures, 2026)
    assert out["between_seasons"] == 1
    assert out["midseason"] == 1
    assert out["teams"] == 3


def test_coach_changes_matches_the_p2g_shape():
    # five teams end a tenure in 2026 and hire a new head coach for 2027; 27 keep theirs
    tenures = [(t, 100 + t, 2020, (2026 if t < 5 else None)) for t in range(32)]
    tenures += [(t, 200 + t, 2027, None) for t in range(5)]
    out = C.coach_changes(tenures, 2026)
    assert out["between_seasons"] == 5 and out["midseason"] == 0 and out["teams"] == 32


def test_trade_counts_splits_in_season_and_offseason():
    rows = [
        (2026, 1, "APPROVED", "IN_SEASON"),
        (2026, 18, "EXECUTED", "IN_SEASON"),
        (2026, 23, "APPROVED", "IN_SEASON"),     # a playoff-week deal is still in-season
        (2026, 1, "PROPOSED", "IN_SEASON"),      # not a deal
        (2026, 1, "STALLED", "IN_SEASON"),
        (2026, 8, "APPROVED", "OFFSEASON"),
        (2026, 8, "APPROVED", "DRAFT_DAY"),
        (2025, 5, "APPROVED", "IN_SEASON"),      # another season
    ]
    out = C.trade_counts(rows, 2026, last_week=23)
    assert out["in_season"] == 3
    assert out["offseason"] == 2
    assert out["all"] == 5
    assert out["proposed"] == 7
    assert out["by_status"]["STALLED"] == 1


def test_career_lengths_use_years_of_experience_else_distinct_seasons():
    rows = [
        ("a", 2026, 2026, "QB", 8),      # retires with 8 years pro -> 9 seasons
        ("a", 2026, 2025, "QB", 7),
        ("b", 2026, 2026, "HB", None),    # no YoE -> two distinct seasons in the database
        ("b", 2026, 2025, "HB", None),
        ("c", 2026, 2026, "LT", 3),
        ("d", 2026, 2026, "QB", 2),       # a second QB so the group has a median
        ("e", 2026, 2026, "QB", 5),
    ]
    out = C.career_lengths(rows, drafted_ids={"a", "c", "d", "e"})
    assert out["careers"] == 5
    assert out["all_players"]["QB"]["n"] == 3 and out["all_players"]["QB"]["median"] == 6
    assert out["all_players"]["RB"].get("median") is None and out["all_players"]["RB"]["n"] == 1
    assert out["drafted"]["QB"]["n"] == 3
    assert "LB" not in out["all_players"]


def test_retirement_ages_split_the_cohort():
    rows = [("a", 2026, "QB", 33), ("b", 2026, "HB", 27), ("c", 2026, "LT", 31),
            ("d", 2026, "WR", None), ("e", 2026, "LS", 40), ("f", 2026, "QB", 31)]
    out = C.retirement_ages(rows, drafted_ids={"a", "b", "f"})
    assert out["n"] == 4                       # the age-less and the unknown position are skipped
    assert out["all_players"]["QB"]["n"] == 2 and out["all_players"]["QB"]["median"] == 31
    assert set(out["drafted"]) == {"QB", "RB"}


def test_movement_counts_needs_eight_games_both_seasons():
    rows = [
        ("a", 2025, 1, 17), ("a", 2026, 2, 16),   # eligible, moved
        ("b", 2025, 1, 17), ("b", 2026, 1, 15),   # eligible, stayed
        ("c", 2025, 1, 5), ("c", 2026, 2, 17),    # too few games last season
        ("d", 2025, 1, 17), ("d", 2026, None, 0),  # no team this season
        ("e", 2025, 1, 17),                       # only one season
    ]
    out = C.movement_counts(rows)
    assert out["eligible"] == 2 and out["moved"] == 1 and out["rate"] == 0.5
    assert out["from_season"] == 2025 and out["to_season"] == 2026
    assert C.movement_counts(rows[:1]) is None


def test_draft_outcomes_by_round_and_started_seasons():
    rows = [
        (1, 2025, "1", "QB", 17), (2, 2025, "1", "WR", 14), (3, 2025, "1", "TE", 3),
        (4, 2025, "2", "HB", 16),
        (5, 2026, "1", "QB", 10),                 # too recent for a 2+ started-season share
        (6, 2025, "UDFA", "WR", 9),               # not a drafted round
    ]
    started = {1: {2025, 2026}, 2: {2025, 2026}, 3: {2025}, 4: {2025}}
    out = C.draft_outcomes(rows, started, through_season=2026)
    assert out["1"]["n"] == 3 and out["1"]["share_started_2plus"] == 0.667
    assert out["2"]["share_started_2plus"] == 0.0
    assert "1" in out and out["1"]["games_median"] == 14


def test_rookie_usage_excludes_linemen():
    rows = [(1, 2026, "1", "QB", 17), (2, 2026, "1", "HB", 10), (3, 2026, "1", "LT", 17),
            (4, 2026, "2", "CB", 6), (5, 2026, "3", "LS", 9)]
    out = C.rookie_usage(rows)
    assert out["n"] == 3                       # QB, HB, CB
    assert out["median_games"] == 10
    assert out["share_8plus"] == 0.667


# ------------------------------------------------------------------ scoring

def _gate(report, code):
    return next(g for g in report.gates if g.code == code)


def test_all_pass():
    rep = C.compare_decisions(observed(), targets())
    assert rep.overall == C.PASS
    assert all(g.verdict == C.PASS for g in rep.gates if g.verdict != C.REPORT_ONLY)
    assert rep.counts()[C.PASS] == len(rep.gates) - 1          # the offseason reference row


def test_a_failing_gate_fails_the_report():
    obs = observed(trades={"in_season": 61, "offseason": 70, "all": 131, "proposed": 200,
                           "by_status": {}})
    rep = C.compare_decisions(obs, targets())
    assert _gate(rep, "A4.in_season").verdict == C.FAIL
    assert rep.overall == C.FAIL


def test_missing_data_is_not_a_silent_pass():
    obs = observed(career_length={"drafted": {}, "all_players": {}, "careers": 0})
    obs["meta"]["drafted_cohort"] = 0
    rep = C.compare_decisions(obs, targets())
    g = _gate(rep, "A1.QB")
    assert g.verdict == C.NOT_MEASURED and "drafted cohort is empty" in g.detail
    assert rep.overall == C.INCOMPLETE


def test_small_cohorts_are_not_measured():
    cl = {g: {"n": 20, "median": 5.0} for g in GROUPS}
    cl["QB"] = {"n": 3, "median": 5.0}
    rep = C.compare_decisions(observed(career_length={"drafted": cl, "all_players": {}, "careers": 3}),
                              targets())
    assert _gate(rep, "A1.QB").verdict == C.NOT_MEASURED
    assert _gate(rep, "A1.RB").verdict == C.PASS


def test_in_season_trades_need_the_weekly_engine():
    obs = observed(data={"regular_weeks_run": 0})
    rep = C.compare_decisions(obs, targets())
    g = _gate(rep, "A4.in_season")
    assert g.verdict == C.NOT_MEASURED and "weekly league office" in g.detail
    assert _gate(rep, "A4.offseason").verdict == C.REPORT_ONLY


def test_median_tolerance_is_wider_for_qb_k_p():
    cl = {g: {"n": 20, "median": 5.0} for g in GROUPS}
    cl["QB"] = {"n": 20, "median": 6.4}        # 1.4 out: inside 1.5 for QB
    cl["RB"] = {"n": 20, "median": 6.4}        # 1.4 out: outside 1.0 for RB
    rep = C.compare_decisions(observed(career_length={"drafted": cl, "all_players": {}, "careers": 3}),
                              targets())
    assert _gate(rep, "A1.QB").verdict == C.PASS
    assert _gate(rep, "A1.RB").verdict == C.FAIL


def test_draft_monotonic_gate():
    dbr = {str(r): {"n": 30, "games_median": 50, "share_started_2plus": v}
           for r, v in zip(range(1, 8), (0.85, 0.65, 0.48, 0.34, 0.31, 0.15, 0.11))}
    rep = C.compare_decisions(observed(draft_by_round=dbr), targets())
    assert _gate(rep, "A6.monotonic").verdict == C.PASS
    dbr["7"] = {"n": 30, "games_median": 50, "share_started_2plus": 0.9}
    rep = C.compare_decisions(observed(draft_by_round=dbr), targets())
    assert _gate(rep, "A6.monotonic").verdict == C.FAIL


def test_rookie_shares_and_median():
    obs = observed(rookie_usage={"n": 200, "median_games": 11.5, "share_8plus": 0.45,
                                 "share_12plus": 0.36})
    rep = C.compare_decisions(obs, targets())
    assert _gate(rep, "A7.median_games").verdict == C.FAIL      # 2.5 out > 2
    assert _gate(rep, "A7.share_8plus").verdict == C.FAIL        # 11 points out > 10
    assert _gate(rep, "A7.share_12plus").verdict == C.PASS


def test_report_is_json_serializable_and_prints_reasons():
    rep = C.compare_decisions(observed(career_length={"drafted": {}, "all_players": {}, "careers": 0}),
                              targets())
    json.dumps(rep.as_dict())
    text = C.format_report(rep)
    assert "Verdict: INCOMPLETE" in text
    assert "A3.between_seasons" in text and "✓ PASS" in text
    assert "Not measured, and why" in text
    assert "Not yet computable" in text and "P3-06a" in text
    assert "Reported, not gated" in text


# ------------------------------------------------------------------ extract over a fake database

TABLES = {"draft_prospects", "player_season_transitions", "draft_picks"}
TENURES = [(t, 100 + t, 2020, None) for t in range(32)]
TENURES += [(t, 200 + t, 2027, None) for t in range(5)]
TENURES = [(t, c, s, (2026 if c < 200 and t < 5 else e)) for t, c, s, e in TENURES]
PROPOSALS = [(2026, 1, "APPROVED", "IN_SEASON"), (2026, 9, "APPROVED", "OFFSEASON"),
             (2026, 9, "EXPIRED", "IN_SEASON")]
MOVEMENT_ROWS = [("p1", 2026, 2, 16), ("p1", 2027, 3, 16), ("p2", 2026, 1, 17), ("p2", 2027, 1, 17)]
DRAFT_ROWS = [(10, 2025, "1", "WR", 15), (11, 2026, "1", "QB", 17), (12, 2027, "2", "HB", 11)]
CAREER_ROWS = [(21, 2027, 2026, "QB", 8), (21, 2027, 2027, "QB", 9),
               (22, 2027, 2026, "QB", 4), (22, 2027, 2027, "QB", 5)]
AGE_ROWS = [(21, 2027, "QB", 32), (22, 2027, "QB", 30)]


class FakeCursor:
    """Answers the queries extract() makes, from the constants above."""

    def __init__(self):
        self._rows = []

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        self._rows = []
        if "to_regclass" in s:
            self._rows = [(params[0] in TABLES,)]
        elif "FROM coach_tenures" in s:
            self._rows = TENURES
        elif "FROM gm_trade_proposals" in s:
            self._rows = PROPOSALS
        elif "count(DISTINCT week) FROM league_office_runs" in s:
            self._rows = [(18,)]
        elif "FROM player_retirements r JOIN player_seasons ps" in s:
            self._rows = CAREER_ROWS
        elif "FROM player_retirements r LEFT JOIN" in s:
            self._rows = AGE_ROWS
        elif "FROM games WHERE status = 'COMPLETED' AND season IN" in s:
            self._rows = [(2026, 285), (2027, 40)]
        elif "SELECT max(week) FROM games" in s:
            self._rows = [(23,)]
        elif "FROM player_seasons ps LEFT JOIN season_stat_general" in s:
            self._rows = MOVEMENT_ROWS
        elif "FROM draft_prospects dp" in s:
            self._rows = [r for r in DRAFT_ROWS if r[1] <= params[0]]
        elif "FROM depth_charts" in s:
            self._rows = [(11, 2026), (11, 2027), (12, 2027)]
        elif "count(*) FROM games WHERE season" in s:
            self._rows = [(285,)]
        elif "count(*) FROM player_seasons WHERE season" in s:
            self._rows = [(3236,)]
        elif "count(*) FROM player_retirements WHERE season <=" in s:
            self._rows = [(44,)]
        elif "count(*) FROM player_season_transitions WHERE to_season" in s:
            self._rows = [(4,)]
        elif "count(*) FROM gm_trade_proposals WHERE season" in s:
            self._rows = [(3,)]
        elif "count(*) FROM draft_picks" in s:
            self._rows = [(10,)]
        elif "SELECT DISTINCT player_id FROM draft_prospects" in s:
            self._rows = [(21,), (22,)]
        elif "FROM draft_prospects WHERE player_id IS NOT NULL" in s:
            self._rows = [(1,)]
        elif "SELECT DISTINCT player_id FROM player_season_transitions" in s:
            self._rows = []
        else:                                  # pragma: no cover - a new query needs a branch here
            raise AssertionError(f"unexpected query: {s}")

    def fetchall(self):
        return self._rows


def test_extract_reads_every_gate():
    obs = C.extract(FakeCursor(), 2026, league_current_season=2027)
    assert obs["coach_changes"] == {"between_seasons": 5, "midseason": 0, "teams": 32}
    assert obs["trades"]["in_season"] == 1 and obs["trades"]["offseason"] == 1
    assert obs["meta"]["drafted_cohort"] == 2
    assert obs["career_length"]["drafted"]["QB"]["n"] == 2             # max YoE 9 + 1 and 5 + 1
    assert obs["retirement_age"]["drafted"]["QB"]["n"] == 2
    assert obs["team_movement"]["rate"] == 0.5                        # 2026 -> 2027, one of two moved
    assert obs["draft_by_round"]["1"]["n"] == 1                       # the 2026 and 2027 classes are too recent
    assert obs["rookie_usage"]["n"] == 2                              # the 2025 WR and the 2026 QB
    assert obs["data"]["regular_weeks_run"] == 18


def test_extract_marks_a_season_without_weekly_runs():
    class NoWeekly(FakeCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if "count(DISTINCT week) FROM league_office_runs" in " ".join(sql.split()):
                self._rows = [(0,)]

    obs = C.extract(NoWeekly(), 2026, league_current_season=2027)
    rep = C.compare_decisions(obs, targets())
    assert _gate(rep, "A4.in_season").verdict == C.NOT_MEASURED


class FakeConn:
    def __init__(self, cur):
        self._cur = cur

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def cursor(self):
        return self._cur


def _league_cursor(cur):
    class WithLeague:
        def __init__(self):
            self.inner = cur
            self._rows = []

        def execute(self, sql, params=None):
            if "SELECT current_season FROM league_state" in " ".join(sql.split()):
                self._rows = [(2027,)]
            else:
                self.inner.execute(sql, params)
                self._rows = self.inner.fetchall()

        def fetchall(self):
            return self._rows

    return WithLeague()


def test_run_writes_both_reports(tmp_path):
    from pathlib import Path
    targets_path = Path(tmp_path) / "targets.json"
    targets_path.write_text(json.dumps(targets()))
    cur = _league_cursor(FakeCursor())
    out = C.run("host=localhost dbname=madden_tune_x user=postgres password=postgres", 2026,
                targets_path=str(targets_path), out_dir=str(tmp_path),
                connect=lambda dsn: FakeConn(cur))
    assert out["paths"]["md"].endswith("decisions_2026.md")
    assert out["paths"]["json"].endswith("decisions_2026.json")
    assert "Decision comparison: season 2026" in open(out["paths"]["md"]).read()
    doc = json.load(open(out["paths"]["json"]))
    assert doc["report"]["season"] == 2026 and "gates" in doc["report"]


def test_run_refuses_without_targets(tmp_path):
    with pytest.raises(FileNotFoundError):
        C.run("host=localhost dbname=x user=postgres password=postgres", 2026,
              targets_path=str(tmp_path / "missing.json"), out_dir=str(tmp_path),
              connect=lambda dsn: FakeConn(FakeCursor()))
