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


def season_facts(completed_games=285, regular_weeks_run=18):
    return {"completed_games": completed_games, "regular_weeks_run": regular_weeks_run}


def cc_entry(between=5, midseason=0, teams=32, vacancies=0, hot_seat_rows=32, hc_hires=5, **facts):
    entry = {"between_seasons": between, "midseason": midseason, "teams": teams, "vacancies": vacancies,
             "hot_seat_rows": hot_seat_rows, "hc_hires": hc_hires}
    entry.update(season_facts(**facts))
    return entry


def trade_entry(in_season=13, offseason=70, proposed=84, **facts):
    entry = {"in_season": in_season, "offseason": offseason, "all": in_season + offseason,
             "proposed": proposed, "by_status": {"APPROVED": in_season + offseason}}
    entry.update(season_facts(**facts))
    return entry


def movement_pair(lo=2025, hi=2026, eligible=720, moved=180):
    return {"from_season": lo, "to_season": hi, "eligible": eligible, "moved": moved,
            "rate": round(moved / eligible, 4) if eligible else None}


def observed(**over):
    cc = {2024: cc_entry(between=5), 2025: cc_entry(between=4), 2026: cc_entry(between=5)}
    trades = {s: trade_entry() for s in (2024, 2025, 2026)}
    pairs = [movement_pair(2023, 2024), movement_pair(2024, 2025), movement_pair(2025, 2026)]
    obs = {
        "season": 2026,
        "meta": {"drafted_cohort": 200, "target_seasons": [2015, 2025]},
        "coach_changes": dict(cc[2026], by_season=cc),
        "trades": dict(trades[2026], by_season=trades),
        "career_length": {"drafted": {g: {"n": 20, "median": 5.0} for g in GROUPS},
                          "all_players": {g: {"n": 30, "median": 4.0} for g in GROUPS},
                          "careers": 200},
        "retirement_age": {"drafted": {g: {"n": 20, "median": 28.0} for g in GROUPS},
                           "all_players": {g: {"n": 30, "median": 27.0} for g in GROUPS},
                           "n": 200},
        "team_movement": pairs[-1],
        "movement_pairs": pairs,
        "draft_by_round": {str(r): {"n": 30, "games_median": 50, "share_started_2plus": v}
                           for r, v in zip(range(1, 8), (0.85, 0.65, 0.48, 0.34, 0.31, 0.15, 0.11))},
        "rookie_usage": {"n": 200, "median_games": 9.0, "share_8plus": 0.56, "share_12plus": 0.36},
        "data": {"regular_weeks_run": 18, "completed_games": 285},
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
    assert out["vacancies"] == 0


def test_an_unfilled_vacancy_is_still_a_change():
    # F8: the coach's tenure ends in 2026 and the walk stopped before a successor was hired
    out = C.coach_changes([(1, 10, 2020, 2026)], 2026)
    assert out["between_seasons"] == 1 and out["vacancies"] == 1


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


def test_a_started_season_is_eight_weeks_at_the_players_own_position():
    rows = []
    for week in range(1, 9):                      # 8 weeks at QB: a started season
        rows.append((1, 2026, week, "QB", "QB", "MU_COACH"))
    for week in range(1, 8):                      # 7 weeks: not enough
        rows.append((2, 2026, week, "QB", "QB", "MU_COACH"))
    for week in range(1, 19):                     # depth 1 only at KR: a returner, never a start
        rows.append((3, 2026, week, "KR", "WR", "MU_COACH"))
    for week in range(1, 19):                     # depth 1 in another position's slot
        rows.append((4, 2026, week, "HB", "WR", "MU_COACH"))
    for week in range(1, 19):                     # MADDEN is the read-back fallback and counts (R2b)
        rows.append((5, 2026, week, "QB", "QB", "MADDEN"))
    for week in range(1, 9):                      # JR's chart counts
        rows.append((6, 2026, week, "WR", "WR", "JR"))
    assert C.started_seasons(rows) == {1: {2026}, 5: {2026}, 6: {2026}}


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
    assert rep.counts()[C.PASS] == len(rep.gates) - 2          # the offseason and mid-season reference rows


def test_a_failing_gate_fails_the_report():
    obs = observed()
    obs["trades"]["by_season"][2026]["in_season"] = 61         # far outside the 3-25 per-season band
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
    for entry in obs["trades"]["by_season"].values():
        entry["regular_weeks_run"] = 0
    rep = C.compare_decisions(obs, targets())
    g = _gate(rep, "A4.in_season")
    assert g.verdict == C.NOT_MEASURED and "weekly league office" in g.detail
    assert _gate(rep, "A4.offseason").verdict == C.REPORT_ONLY


# ------------------------------------------------------------------ the three-season rule (F1)

def test_one_season_is_not_the_gate():
    """A single in-band season is INCOMPLETE: the gate is a three-consecutive-season mean (§1, §7.4)."""
    obs = observed()
    obs["coach_changes"]["by_season"] = {2026: cc_entry(between=5)}
    rep = C.compare_decisions(obs, targets())
    g = _gate(rep, "A3.between_seasons")
    assert g.verdict == C.INCOMPLETE
    assert g.observed == "5 (1 of 3 seasons)"
    assert "1 of 3 seasons" in g.detail


def test_each_season_must_sit_inside_the_band():
    """{3, 9, 9} has a mean of 7 (inside 4-10) but 3 is below the per-season band, so the gate fails."""
    obs = observed()
    obs["coach_changes"]["by_season"] = {2024: cc_entry(between=3), 2025: cc_entry(between=9),
                                        2026: cc_entry(between=9)}
    obs["coach_changes"]["between_seasons"] = 7
    g = _gate(C.compare_decisions(obs, targets()), "A3.between_seasons")
    assert g.verdict == C.FAIL and "2024 3" in g.detail


def test_three_seasons_in_band_pass_on_the_mean():
    obs = observed()
    obs["coach_changes"]["by_season"] = {2024: cc_entry(between=5), 2025: cc_entry(between=6),
                                        2026: cc_entry(between=7)}
    obs["coach_changes"]["between_seasons"] = 6
    g = _gate(C.compare_decisions(obs, targets()), "A3.between_seasons")
    assert g.verdict == C.PASS
    assert g.observed == "6 (mean of 2024 5, 2025 6, 2026 7)"


def test_a4_mean_is_in_5_21_but_each_season_must_be_in_3_25():
    """{4, 30, 10} has a mean of 14.7 in 5-21, but 30 is above the per-season band of 3-25."""
    obs = observed()
    obs["trades"]["by_season"] = {2024: trade_entry(in_season=4), 2025: trade_entry(in_season=30),
                                  2026: trade_entry(in_season=10)}
    obs["trades"]["in_season"] = 14
    g = _gate(C.compare_decisions(obs, targets()), "A4.in_season")
    assert g.verdict == C.FAIL and "2025 30" in g.detail


def test_a5_scores_the_three_pairs_on_the_mean_and_each_band():
    obs = observed()
    obs["movement_pairs"] = [movement_pair(2023, 2024, 720, 180),
                             movement_pair(2024, 2025, 720, 180),
                             movement_pair(2025, 2026, 720, 180)]
    g = _gate(C.compare_decisions(obs, targets()), "A5")
    assert g.verdict == C.PASS and "mean of 2023→2024" in g.observed
    obs["movement_pairs"][2] = movement_pair(2025, 2026, 720, 260)   # 36.1%, outside 17.9-26.5%
    g = _gate(C.compare_decisions(obs, targets()), "A5")
    assert g.verdict == C.FAIL


# ------------------------------------------------------------------ data floors (F3)

def test_a_season_with_no_games_is_not_measured():
    obs = observed()
    obs["coach_changes"]["by_season"] = {2024: cc_entry(completed_games=0),
                                        2025: cc_entry(completed_games=0),
                                        2026: cc_entry(completed_games=0)}
    obs["trades"]["by_season"] = {2024: trade_entry(completed_games=0),
                                  2025: trade_entry(completed_games=0),
                                  2026: trade_entry(completed_games=0)}
    obs["movement_pairs"] = []
    rep = C.compare_decisions(obs, targets())
    for code in ("A3.between_seasons", "A4.in_season", "A5"):
        assert _gate(rep, code).verdict == C.NOT_MEASURED, code
    assert _gate(rep, "A3.midseason").verdict == C.REPORT_ONLY
    assert rep.overall == C.INCOMPLETE


def test_a_hiring_cycle_that_did_not_run_is_not_measured():
    obs = observed()
    for entry in obs["coach_changes"]["by_season"].values():
        entry["hot_seat_rows"] = 0
    g = _gate(C.compare_decisions(obs, targets()), "A3.between_seasons")
    assert g.verdict == C.NOT_MEASURED and "hiring cycle" in g.detail


def test_midseason_is_report_only():
    g = _gate(C.compare_decisions(observed(), targets()), "A3.midseason")
    assert g.verdict == C.REPORT_ONLY and "0–3" in g.target


def test_a5_needs_a_minimum_cohort():
    rows = [(p, s, 1 if p != 1 else 2, 9) for p in (1, 2, 3, 4) for s in (2025, 2026)]
    tiny = C.movement_counts(rows)
    assert tiny["eligible"] == 4
    obs = observed(movement_pairs=[tiny])
    g = _gate(C.compare_decisions(obs, targets()), "A5")
    assert g.verdict == C.NOT_MEASURED and "below 300" in g.detail


def test_a5_does_not_claim_the_moved_ledger():
    """F6: the code reads player_seasons team ids, not player_season_transitions MOVED."""
    g = _gate(C.compare_decisions(observed(movement_pairs=[]), targets()), "A5")
    assert "MOVED" not in g.detail and "player_seasons" in g.detail


def test_offseason_reference_says_which_offseason_it_is():
    """F9: season S's OFFSEASON rows are the trades after S, not the real offseason before S."""
    g = _gate(C.compare_decisions(observed(), targets()), "A4.offseason")
    assert g.verdict == C.REPORT_ONLY and "after 2026" in g.detail


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

TABLES = {"draft_prospects", "player_season_transitions", "draft_picks",
          "coach_hot_seat", "staff_moves"}
GAMES = {2023: 285, 2024: 285, 2025: 285, 2026: 285, 2027: 40}
WEEKS = {2023: 18, 2024: 18, 2025: 18, 2026: 18, 2027: 0}
LAST_WEEK = {s: 23 for s in GAMES}
HOT_SEAT = {2023: 32, 2024: 32, 2025: 32, 2026: 32, 2027: 0}
HC_HIRES = {2023: 5, 2024: 5, 2025: 4, 2026: 5, 2027: 0}
# five changes after 2026, four after 2025, five after 2024; every other team keeps one coach
TENURE_ENDS = {**{t: 2026 for t in range(5)}, **{t: 2025 for t in range(5, 9)},
               **{t: 2024 for t in range(9, 14)}}
TENURES = [(t, 1000 + t, 2019, TENURE_ENDS.get(t)) for t in range(32)]
TENURES += [(t, 2000 + t, 2027, None) for t in range(5)]
TENURES += [(t, 2100 + t, 2026, None) for t in range(5, 9)]
TENURES += [(t, 2200 + t, 2025, None) for t in range(9, 14)]
PROPOSALS = []
for _season in (2024, 2025, 2026):
    PROPOSALS += [(_season, week, "APPROVED", "IN_SEASON") for week in range(1, 14)]
    PROPOSALS += [(_season, 9, "APPROVED", "OFFSEASON") for _ in range(70)]
PROPOSALS += [(2026, 9, "EXPIRED", "IN_SEASON")]


def _movement_rows():
    """400 players in four seasons; exactly 100 change team on each consecutive pair (a 25% rate)."""
    team = {pid: {s: 1 for s in (2023, 2024, 2025, 2026)} for pid in range(1, 401)}
    for pid in range(1, 101):
        team[pid][2024] = team[pid][2025] = team[pid][2026] = 2
    for pid in range(101, 201):
        team[pid][2025] = team[pid][2026] = 3
    for pid in range(201, 301):
        team[pid][2026] = 4
    return [(pid, s, team[pid][s], 17) for pid in team for s in (2023, 2024, 2025, 2026)]


MOVEMENT_ROWS = _movement_rows()
DRAFT_ROWS = [(10, 2025, "1", "WR", 15), (11, 2026, "1", "QB", 17), (12, 2027, "2", "HB", 11)]
# (player_id, season, position, age, years_of_experience): the roster rows the ended-career readers see
PLAYER_SEASONS = [(21, 2025, "QB", 31, 8), (21, 2026, "QB", 32, 9),
                  (22, 2025, "QB", 29, 4), (22, 2026, "QB", 30, 5),
                  (23, 2027, "QB", 27, 3)]        # a 2027 retirement must not leak into a 2026 report
RETIREMENTS = [(21, 2026), (22, 2026), (23, 2027)]
DEPTH_ROWS = [(11, 2025, week, "WR", "WR", "MU_COACH") for week in range(1, 9)]
DEPTH_ROWS += [(11, 2026, week, "WR", "WR", "MU_COACH") for week in range(1, 9)]
DEPTH_ROWS += [(12, 2026, week, "WR", "WR", "MU_COACH") for week in range(1, 8)]    # 7 weeks: not a start
DEPTH_ROWS += [(13, 2026, week, "KR", "WR", "MU_COACH") for week in range(1, 19)]   # returner only
DEPTH_ROWS += [(14, 2026, week, "QB", "QB", "MU_COACH") for week in range(1, 10)]   # 9 weeks, one a bye
DEPTH_ROWS += [(15, 2026, week, "QB", "QB", "MU_COACH") for week in range(1, 9)]    # 8 weeks, one a bye
DEPTH_TEAMS = {11: 1, 12: 1, 13: 1, 14: 2, 15: 3}                                   # each player's team
DEPTH_GAMES = {(2025, week, team) for week in range(1, 19) for team in (1, 2, 3)}
DEPTH_GAMES |= {(2026, week, team) for week in range(1, 19) for team in (1, 2, 3)}
DEPTH_GAMES -= {(2026, 5, 2), (2026, 3, 3)}        # team 2's and team 3's bye weeks in 2026


class FakeCursor:
    """Answers the queries extract() makes, from the constants above, and honours the season params.

    The query params are applied the way Postgres would apply them (a season filter on `= ANY(%s)` or
    `<= %s`), so a fixture row outside the requested season is not silently counted (audit F7).
    """

    def __init__(self):
        self._rows = []

    @staticmethod
    def _seasons(params):
        return params[0] if params and isinstance(params[0], (list, tuple, set)) else ()

    @staticmethod
    def _ended_career_rows(params, career):
        """The _ENDED_CAREERS CTE over PLAYER_SEASONS / RETIREMENTS: a retirement up to params[0] counts while
        no roster row follows it, and a last roster row at or before params[1] is two unsigned seasons."""
        season, unsigned_cutoff = params
        last_active = {}
        for pid, s, *_ in PLAYER_SEASONS:
            last_active[pid] = max(last_active.get(pid, s), s)
        retired = {}
        for pid, s in RETIREMENTS:
            if s <= season:
                retired[pid] = max(retired.get(pid, s), s)
        ended = {pid: end for pid, end in retired.items() if last_active.get(pid, 0) <= end}
        for pid, last in last_active.items():
            if last <= unsigned_cutoff:
                ended.setdefault(pid, last)
        if career:
            rows = [(pid, end, r[1], r[2], r[4]) for pid, end in ended.items()
                    for r in PLAYER_SEASONS if r[0] == pid and r[1] <= end]
            return sorted(rows, key=lambda r: (r[0], r[2]))
        rows = []
        for pid, end in ended.items():
            row = next((r for r in PLAYER_SEASONS if r[0] == pid and r[1] == end), None)
            rows.append((pid, end, row[2] if row else None, row[3] if row else None))
        return rows

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        p = params or ()
        self._rows = []
        if "to_regclass" in s:
            self._rows = [(p[0] in TABLES,)]
        elif "FROM coach_tenures" in s:
            self._rows = TENURES
        elif "FROM coach_hot_seat" in s:
            self._rows = [(season, HOT_SEAT[season]) for season in self._seasons(p) if HOT_SEAT.get(season)]
        elif "FROM staff_moves" in s:
            self._rows = [(season, HC_HIRES[season]) for season in self._seasons(p) if HC_HIRES.get(season)]
        elif "count(*) FROM gm_trade_proposals" in s:
            self._rows = [(sum(1 for r in PROPOSALS if r[0] == p[0]),)]
        elif "FROM gm_trade_proposals" in s:
            self._rows = [r for r in PROPOSALS if r[0] in self._seasons(p)]
        elif "count(DISTINCT week) FROM league_office_runs" in s:
            if "ANY" in s:
                self._rows = [(season, WEEKS[season]) for season in self._seasons(p) if WEEKS.get(season)]
            else:
                self._rows = [(WEEKS.get(p[0], 0),)]
        elif "WITH signed_seasons AS" in s:
            self._rows = self._ended_career_rows(p, career="ps.years_of_experience" in s)
        elif "FROM games WHERE status = 'COMPLETED' AND season = ANY" in s:
            self._rows = [(season, GAMES[season]) for season in self._seasons(p) if season in GAMES]
        elif "max(week) FROM games" in s:
            self._rows = [(season, LAST_WEEK[season]) for season in self._seasons(p) if season in LAST_WEEK]
        elif "count(*) FROM games WHERE season" in s:
            self._rows = [(GAMES.get(p[0], 0),)]
        elif "FROM player_seasons ps LEFT JOIN season_stat_general" in s:
            self._rows = [r for r in MOVEMENT_ROWS if r[1] in self._seasons(p)]
        elif "FROM draft_prospects dp" in s:
            self._rows = [r for r in DRAFT_ROWS if r[1] <= p[0]]
        elif "FROM depth_charts" in s:
            self._rows = [r for r in DEPTH_ROWS
                          if r[1] <= p[0] and (r[1], r[2], DEPTH_TEAMS.get(r[0])) in DEPTH_GAMES]
        elif "count(*) FROM player_seasons WHERE season" in s:
            self._rows = [(3236,)]
        elif "count(*) FROM player_retirements WHERE season <=" in s:
            self._rows = [(44,)]
        elif "count(*) FROM player_season_transitions WHERE to_season" in s:
            self._rows = [(4,)]
        elif "count(*) FROM draft_picks" in s:
            self._rows = [(10,)]
        elif "SELECT id FROM players WHERE draft_round BETWEEN 1 AND 7" in s:
            self._rows = [(21,), (22,)]
        elif "SELECT DISTINCT player_id FROM draft_prospects" in s:
            self._rows = [(21,), (22,), (23,)]
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
    assert obs["coach_changes"]["between_seasons"] == 5
    assert obs["coach_changes"]["midseason"] == 0 and obs["coach_changes"]["teams"] == 32
    assert {s: e["between_seasons"] for s, e in obs["coach_changes"]["by_season"].items()} == \
        {2024: 5, 2025: 4, 2026: 5}
    assert obs["coach_changes"]["by_season"][2026]["hot_seat_rows"] == 32
    assert obs["trades"]["in_season"] == 13 and obs["trades"]["offseason"] == 70
    assert obs["meta"]["drafted_cohort"] == 2        # players.draft_round 1-7: the fake answers 21 and 22
    assert obs["career_length"]["drafted"]["QB"]["n"] == 2             # the 2027 retirement is filtered out
    assert obs["retirement_age"]["drafted"]["QB"]["n"] == 2
    assert obs["team_movement"]["from_season"] == 2025
    assert [p["rate"] for p in obs["movement_pairs"]] == [0.25, 0.25, 0.25]
    assert obs["draft_by_round"]["1"]["n"] == 1                       # the 2026 and 2027 classes are too recent
    assert obs["rookie_usage"]["n"] == 2                              # the 2025 WR and the 2026 QB
    assert obs["data"]["regular_weeks_run"] == 18


def test_extract_runs_the_three_season_gate_from_the_real_readers():
    obs = C.extract(FakeCursor(), 2026, league_current_season=2027)
    rep = C.compare_decisions(obs, targets())
    assert _gate(rep, "A3.between_seasons").verdict == C.PASS       # 5, 4, 5 -> mean 4.7, each in 4-10
    assert _gate(rep, "A4.in_season").verdict == C.PASS             # 13 each, mean 13
    assert _gate(rep, "A5").verdict == C.PASS                       # 25.0% each


def test_extract_counts_a_started_season_only_at_his_own_position():
    """F4: player 11 has 8 own-slot weeks in 2025 and 2026; 12 has 7 weeks and 13 is a returner only.

    Player 14 has 9 own-slot weeks with one bye (8 games) and counts; 15 has 8 with one bye (7 games) and
    does not — a start is a game (THRESHOLDS §7 decision 12), so a bye week's chart is not one.
    """
    assert C.read_started_seasons(FakeCursor(), 2026) == {11: {2025, 2026}, 14: {2026}}

class CohortCursor:
    """Answers the three cohort queries with different answers, so a test can tell which one the code used:
    Madden's own draft round (players 21, 23), linked Mu picks (21, 99) and ledger DRAFTED (98)."""

    def __init__(self):
        self._rows = []

    def execute(self, sql, params=None):
        s = " ".join(sql.split())
        if "SELECT id FROM players WHERE draft_round BETWEEN 1 AND 7" in s:
            self._rows = [(21,), (23,)]
        elif "SELECT DISTINCT player_id FROM draft_prospects" in s:
            self._rows = [(21,), (99,)]
        elif "SELECT DISTINCT player_id FROM player_season_transitions" in s:
            self._rows = [(98,)]
        else:                                  # pragma: no cover - a new query needs a branch here
            raise AssertionError(f"unexpected query: {s}")

    def fetchall(self):
        return self._rows


def test_drafted_cohort_is_maddens_draft_rounds_not_mu_picks():
    # §7 decision 10: PLYR_DRAFTROUND on every player is the cohort. The old route (draft_prospects actual picks /
    # player_season_transitions DRAFTED) would return {21, 99, 98} here.
    assert C.read_drafted_ids(CohortCursor()) == {21, 23}


@pytest.mark.integration
def test_drafted_cohort_is_players_draft_round_in_postgres():
    """Real Postgres: the cohort is players.draft_round 1-7, and an undrafted player stays out."""
    import os

    import psycopg

    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        cur = conn.cursor()
        try:
            cur.execute("INSERT INTO players (player_name, draft_round, draft_pick) "
                        "VALUES ('Cohort Drafted', 4, 100) RETURNING id")
            drafted = cur.fetchone()[0]
            cur.execute("INSERT INTO players (player_name) VALUES ('Cohort Undrafted') RETURNING id")
            undrafted = cur.fetchone()[0]
            ids = C.read_drafted_ids(cur)
            assert drafted in ids
            assert undrafted not in ids
        finally:
            conn.rollback()


def test_extract_marks_a_season_without_weekly_runs():
    class NoWeekly(FakeCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if "count(DISTINCT week) FROM league_office_runs" in " ".join(sql.split()):
                if "ANY" in " ".join(sql.split()):
                    self._rows = [(season, 0) for season in self._seasons(params or ())]
                else:
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


# ------------------------------------------------------------------ real Postgres (F7)

@pytest.mark.integration
def test_extract_on_real_postgres_reads_the_p2g_season():
    """Every reader runs on real Postgres, not a fake. The scratch database is a copy of `madden_tune_p2g`,
    where 2026 has 285 completed games, five head-coach changes between seasons and no mid-season change.

    The fake cursor cannot catch a typo'd column or a renamed status; this test does. It skips on any database
    that is not the p2g 2026 shape, so it never asserts another database's numbers.
    """
    import os
    import psycopg
    dsn = os.environ.get("MU_PROGRESSION_STATE_TEST_DSN")
    if not dsn:
        pytest.skip("explicit scratch DSN not supplied")
    database = psycopg.conninfo.conninfo_to_dict(dsn).get("dbname", "")
    if "test" not in database or database == "madden_2036":
        pytest.fail("decision_compare integration tests require an explicitly named test database")
    if not os.path.exists(C.TARGETS_DEFAULT):
        pytest.skip("the decision targets file is not present")
    with C.connect_read_only(dsn) as conn:
        cur = conn.cursor()
        if not cur.execute("SELECT to_regclass('public.coach_tenures') IS NOT NULL "
                           "AND to_regclass('public.games') IS NOT NULL").fetchone()[0]:
            pytest.skip("the scratch database does not have the tuning schema")
        cur.execute("SHOW default_transaction_read_only")
        assert cur.fetchone()[0] == "on"
        obs = C.extract(cur, 2026, league_current_season=2027)
    if obs["data"]["completed_games"] != 285:
        pytest.skip("this test asserts the madden_tune_p2g 2026 shape (285 completed games)")
    assert obs["coach_changes"]["between_seasons"] == 5
    assert obs["coach_changes"]["midseason"] == 0
    assert obs["coach_changes"]["by_season"][2026]["hot_seat_rows"] > 0
    with open(C.TARGETS_DEFAULT, encoding="utf-8") as fh:
        rep = C.compare_decisions(obs, json.load(fh))
    gate = _gate(rep, "A3.between_seasons")
    assert gate.observed == "5 (1 of 3 seasons)"
    assert gate.verdict == C.INCOMPLETE          # one simulated season is not a 3-season mean


def _scratch_dsn():
    """The explicitly named scratch test database, or a skip; a real database is refused."""
    import os

    import psycopg
    dsn = os.environ.get("MU_PROGRESSION_STATE_TEST_DSN")
    if not dsn:
        pytest.skip("explicit scratch DSN not supplied")
    database = psycopg.conninfo.conninfo_to_dict(dsn).get("dbname", "")
    if "test" not in database or database == "madden_2036":
        pytest.fail("decision_compare integration tests require an explicitly named test database")
    return dsn


@pytest.mark.integration
def test_a_started_season_is_eight_weeks_at_his_own_position_in_postgres():
    """F4/§7 decision 12 on real Postgres, rolled back: a start is depth 1 in the player's own slot in 8+
    games of the chart in force. A returner, another position's slot and a bye week's chart never make a
    started season; MADDEN's read-back chart is the fallback and does (R2b)."""
    import psycopg
    dsn = _scratch_dsn()
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM teams ORDER BY id LIMIT 8")
            teams = [r[0] for r in cur.fetchall()]
            if len(teams) < 8:
                pytest.skip("the scratch database has fewer than eight teams")
            # one team per player: depth_charts' key holds one player per team/week/source/slot/depth

            def player(name):
                cur.execute("INSERT INTO players (player_name) VALUES (%s) RETURNING id", (name,))
                return cur.fetchone()[0]

            def roster(pid, position):
                cur.execute("INSERT INTO player_seasons (player_id, season, position) VALUES (%s, 2030, %s)",
                            (pid, position))

            def chart(team_id, pid, week, slot, source):
                cur.execute("INSERT INTO depth_charts "
                            "(team_id, season, week, position_slot, depth, player_id, source) "
                            "VALUES (%s, 2030, %s, %s, 1, %s, %s)",
                            (team_id, week, slot, pid, source))

            def game(home, away, week):
                cur.execute("INSERT INTO games (season, week, home_team_id, away_team_id) "
                            "VALUES (2030, %s, %s, %s)", (week, home, away))

            for week in range(1, 19):                    # every team plays every week...
                game(teams[0], teams[1], week)
                game(teams[2], teams[3], week)
                game(teams[4], teams[5], week)
            for week in range(1, 9):                     # ...except teams 6 and 7's shared week-4 bye
                if week != 4:
                    game(teams[6], teams[7], week)

            started = player("Started Eight Weeks")
            short = player("Started Seven Weeks")
            bye = player("Eight Weeks One Bye")
            returner = player("Returner Only")
            read_back = player("Madden Chart")
            jr = player("JR Chart")
            for pid in (started, short, bye, returner, read_back, jr):
                roster(pid, "WR")
            for week in range(1, 9):
                chart(teams[0], started, week, "WR", "MU_COACH")
                chart(teams[4], jr, week, "WR", "JR")
            for week in range(1, 8):
                chart(teams[6], short, week, "WR", "MU_COACH")     # 7 games (teams 6/7: weeks 1-8, week 4 bye)
            for week in range(1, 9):
                chart(teams[7], bye, week, "WR", "MU_COACH")       # 8 charts, but week 4 is a bye: 7 games
            for week in range(1, 19):
                chart(teams[2], returner, week, "KR", "MU_COACH")   # his own position is WR, the slot is KR
                chart(teams[3], read_back, week, "WR", "MADDEN")    # the read-back fallback (R2b)
            got = C.read_started_seasons(cur, 2030)
            assert got.get(started) == {2030}
            assert got.get(jr) == {2030}
            assert short not in got
            assert bye not in got
            assert returner not in got
            assert got.get(read_back) == {2030}
        finally:
            conn.rollback()


@pytest.mark.integration
def test_an_unsigned_free_agent_ends_a_career_after_two_empty_seasons_in_postgres():
    """F5/§7 decision 11 on real Postgres, rolled back: a career ends at a retirement or after two
    consecutive seasons with no signed roster row; one empty season is not enough and a later signing
    reopens it. A1 length and A2 age both use this population."""
    import psycopg
    dsn = _scratch_dsn()
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM teams ORDER BY id LIMIT 1")
            pair = cur.fetchone()
            if not pair:
                pytest.skip("the scratch database has no teams")
            team = pair[0]

            def player(name):
                cur.execute("INSERT INTO players (player_name) VALUES (%s) RETURNING id", (name,))
                return cur.fetchone()[0]

            def season(pid, season, position, age, yoe):
                # a signed season: load_players writes a FreeAgent year with team_id NULL instead
                cur.execute("INSERT INTO player_seasons "
                            "(player_id, season, team_id, position, age, years_of_experience) "
                            "VALUES (%s, %s, %s, %s, %s, %s)",
                            (pid, season, team, position, age, yoe))

            unsigned = player("Unsigned Free Agent")
            season(unsigned, 2027, "WR", 24, 0)
            season(unsigned, 2028, "WR", 25, 1)              # then 2029 and 2030 empty -> ended at 2028
            one_empty = player("One Empty Season")
            season(one_empty, 2029, "QB", 30, 5)             # only 2030 is empty by the 2030 report
            comeback = player("Signed Again")
            season(comeback, 2027, "CB", 24, 0)
            season(comeback, 2028, "CB", 25, 1)
            season(comeback, 2031, "CB", 28, 4)              # the gap did not end the career
            retired = player("Announced Retirement")
            season(retired, 2028, "HB", 28, 6)
            cur.execute("INSERT INTO player_retirements (season, player_id, player_name, position) "
                        "VALUES (2028, %s, 'Announced Retirement', 'HB')", (retired,))

            careers = C.read_career_rows(cur, 2030)
            ages = C.read_retirement_rows(cur, 2030)
            by_pid = {}
            for pid, end_season, row_season, position, yoe in careers:
                by_pid.setdefault(pid, {"end": end_season, "rows": []})["rows"].append(
                    (row_season, position, yoe))
            assert by_pid[unsigned]["end"] == 2028
            assert [r[0] for r in by_pid[unsigned]["rows"]] == [2027, 2028]
            assert one_empty not in by_pid                          # one empty season is not two
            assert comeback not in by_pid                           # a later signing reopens the career
            assert by_pid[retired]["end"] == 2028
            assert [r[0] for r in by_pid[retired]["rows"]] == [2028]

            both = [r for r in careers if r[0] in (unsigned, retired)]
            lengths = C.career_lengths(both, {unsigned, retired})
            assert lengths["drafted"]["WR"]["n"] == 1        # the unsigned free agent is in the cohort
            assert lengths["drafted"]["RB"]["n"] == 1        # and so is the announced retirement
            assert max(r[4] for r in both if r[0] == unsigned) + 1 == 2   # 2027-2028, two seasons
            ages_by_pid = {r[0]: (r[1], r[2], r[3]) for r in ages}
            assert ages_by_pid[unsigned] == (2028, "WR", 25)
            assert ages_by_pid[retired] == (2028, "HB", 28)
            assert one_empty not in ages_by_pid and comeback not in ages_by_pid
        finally:
            conn.rollback()


# ------------------------------------------------------------------ P3-07 audit R1/R2/R3

@pytest.mark.integration
def test_free_agent_rows_do_not_keep_an_unsigned_career_alive_in_postgres():
    """R1: load_players writes a FreeAgent player_seasons row with NULL team_id every season, so a season
    with no team is not a signing. A signed 2037/2038 career with FreeAgent rows in 2039/2040 ends in 2038
    (A1 length 2, A2 age from the 2038 signed snapshot); one unsigned year is not yet ended; a later
    Signed or PracticeSquad row reopens it; a retirement followed only by FreeAgent rows stays ended; and
    NULL-team years of experience never lengthen a career. Every row rolled back."""
    import psycopg

    from pipeline import load_save

    dsn = _scratch_dsn()
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM teams ORDER BY id LIMIT 1")
            pair = cur.fetchone()
            if not pair:
                pytest.skip("the scratch database has no teams")
            team = pair[0]

            def snapshot(pgid, first, last, status, age, yoe):
                return {"players": [{
                    "pgid": pgid, "status": status, "first_name": first, "last_name": last,
                    "team_index": 0 if status != "FreeAgent" else load_save.FREE_AGENT_TEAM_INDEX,
                    "position": "WR", "age": age, "overall": 70, "years_pro": yoe,
                    "height_in": 72, "weight_lbs": 205, "jersey": 11,
                    "dev_trait": "Normal", "archetype": "WR_DeepThreat"}]}

            def load(pgid, first, last, status, season, age, yoe):
                load_save.load_players(cur, snapshot(pgid, first, last, status, age, yoe), season,
                                       {0: team}, {})

            def pid_of(pgid):
                cur.execute("SELECT id FROM players WHERE madden_pgid = %s", (pgid,))
                return cur.fetchone()[0]

            # the ingestion shape: two Signed seasons, then two FreeAgent rows with no team
            load(9_700_001, "R1", "Gap", "Signed", 2037, 24, 0)
            load(9_700_001, "R1", "Gap", "Signed", 2038, 25, 1)
            load(9_700_001, "R1", "Gap", "FreeAgent", 2039, 26, 2)
            load(9_700_001, "R1", "Gap", "FreeAgent", 2040, 27, 3)
            gap = pid_of(9_700_001)

            # the same shape, then a later genuine roster row: Signed, and PracticeSquad
            for pgid, status, first in ((9_700_002, "Signed", "R1"), (9_700_003, "PracticeSquad", "R1")):
                load(pgid, first, " Comeback", "Signed", 2037, 24, 0)
                load(pgid, first, " Comeback", "Signed", 2038, 25, 1)
                load(pgid, first, " Comeback", "FreeAgent", 2039, 26, 2)
                load(pgid, first, " Comeback", "FreeAgent", 2040, 27, 3)
                load(pgid, first, " Comeback", status, 2041, 28, 4)
            signed_back = pid_of(9_700_002)
            practice_back = pid_of(9_700_003)

            # an announced retirement followed only by FreeAgent rows
            load(9_700_004, "R1", "Retired With FAs", "Signed", 2037, 24, 0)
            load(9_700_004, "R1", "Retired With FAs", "Signed", 2038, 25, 1)
            retired = pid_of(9_700_004)
            cur.execute("INSERT INTO player_retirements (season, player_id, player_name, position) "
                        "VALUES (2039, %s, 'R1 Retired With FAs', 'WR')", (retired,))
            load(9_700_004, "R1", "Retired With FAs", "FreeAgent", 2039, 26, 2)
            load(9_700_004, "R1", "Retired With FAs", "FreeAgent", 2040, 27, 3)

            assert gap not in {r[0] for r in C.read_career_rows(cur, 2039)}   # one unsigned year is not two

            careers = [r for r in C.read_career_rows(cur, 2041) if r[0] == gap]
            assert [(r[1], r[2], r[3], r[4]) for r in careers] == \
                [(2038, 2037, "WR", 0), (2038, 2038, "WR", 1)]
            ages = [r for r in C.read_retirement_rows(cur, 2041) if r[0] == gap]
            assert ages == [(gap, 2038, "WR", 25)]                            # the signed snapshot, not a FA year
            lengths = C.career_lengths(careers, {gap})
            assert lengths["drafted"]["WR"]["n"] == 1
            assert max(r[4] for r in careers) + 1 == 2                        # max signed YoE 1 + 1, never 3 + 1
            age_bands = C.retirement_ages(ages, {gap})
            assert age_bands["drafted"]["WR"]["n"] == 1

            # the later NULL-team rows did not reopen him (the endpoint above is still 2038), but a later
            # Signed or PracticeSquad row does reopen
            assert signed_back not in {r[0] for r in C.read_career_rows(cur, 2041)}
            assert practice_back not in {r[0] for r in C.read_career_rows(cur, 2041)}
            assert signed_back not in {r[0] for r in C.read_retirement_rows(cur, 2041)}
            assert practice_back not in {r[0] for r in C.read_retirement_rows(cur, 2041)}

            # a retirement is preferred over the unsigned inference and survives the FA rows after it
            retired_careers = [r for r in C.read_career_rows(cur, 2041) if r[0] == retired]
            assert [(r[1], r[2]) for r in retired_careers] == [(2039, 2037), (2039, 2038)]
            retired_ages = [r for r in C.read_retirement_rows(cur, 2041) if r[0] == retired]
            assert retired_ages == [(retired, 2039, "WR", 25)]
        finally:
            conn.rollback()


@pytest.mark.integration
def test_the_chart_in_force_decides_one_starter_per_slot_in_postgres():
    """R2/§7 decision 12 on real Postgres, rolled back: the chart in force is chosen first (JR's chart for a
    team/week beats MU_COACH's), then depth 1 in the player's own slot counts. A JR override of Mu's starter
    gives only the JR man the start; switching JR's starter away after seven weeks gives nobody an eighth; a
    mapped defensive slot behaves the same; and a slot JR leaves empty falls through to MU_COACH's man (R2b's
    per-slot precedence), so the slot still has a starter."""
    import psycopg
    dsn = _scratch_dsn()
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM teams ORDER BY id LIMIT 4")
            teams = [r[0] for r in cur.fetchall()]
            if len(teams) < 4:
                pytest.skip("the scratch database has fewer than four teams")

            def player(name):
                cur.execute("INSERT INTO players (player_name) VALUES (%s) RETURNING id", (name,))
                return cur.fetchone()[0]

            def roster(pid, team_id, position):
                cur.execute("INSERT INTO player_seasons (player_id, season, team_id, position) "
                            "VALUES (%s, 2033, %s, %s)", (pid, team_id, position))

            def chart(team_id, pid, week, slot, source):
                cur.execute("INSERT INTO depth_charts "
                            "(team_id, season, week, position_slot, depth, player_id, source) "
                            "VALUES (%s, 2033, %s, %s, 1, %s, %s)", (team_id, week, slot, pid, source))

            def game(home, away, week):
                cur.execute("INSERT INTO games (season, week, home_team_id, away_team_id) "
                            "VALUES (2033, %s, %s, %s)", (week, home, away))

            for week in range(1, 9):
                game(teams[0], teams[1], week)
                game(teams[2], teams[3], week)

            # team 0, WR: JR overrides Mu's starter for all eight weeks
            mu_override = player("R2 MU WR Starter")
            jr_override = player("R2 JR WR Starter")
            for pid in (mu_override, jr_override):
                roster(pid, teams[0], "WR")
            for week in range(1, 9):
                chart(teams[0], mu_override, week, "WR", "MU_COACH")
                chart(teams[0], jr_override, week, "WR", "JR")

            # team 1, WR: JR starts his man for seven weeks, then switches away in week 8
            mu_switch = player("R2 MU WR Still There")
            jr_seven = player("R2 JR WR Seven Weeks")
            jr_switch = player("R2 JR WR Week Eight")
            for pid in (mu_switch, jr_seven, jr_switch):
                roster(pid, teams[1], "WR")
            for week in range(1, 8):
                chart(teams[1], mu_switch, week, "WR", "MU_COACH")
                chart(teams[1], jr_seven, week, "WR", "JR")
            chart(teams[1], mu_switch, 8, "WR", "MU_COACH")
            chart(teams[1], jr_switch, 8, "WR", "JR")

            # team 0, a mapped defensive slot: roster LEDG -> depth-chart slot LE
            mu_edge = player("R2 MU Edge Starter")
            jr_edge = player("R2 JR Edge Starter")
            for pid in (mu_edge, jr_edge):
                roster(pid, teams[0], "LEDG")
            for week in range(1, 9):
                chart(teams[0], mu_edge, week, "LE", "MU_COACH")
                chart(teams[0], jr_edge, week, "LE", "JR")

            # team 2: JR's chart is in force but has no WR depth 1; Mu's chart names one (R2b fallback)
            mu_cleared = player("R2 MU WR Cleared Slot")
            jr_te = player("R2 JR TE In Force")
            roster(mu_cleared, teams[2], "WR")
            roster(jr_te, teams[2], "TE")
            for week in range(1, 9):
                chart(teams[2], mu_cleared, week, "WR", "MU_COACH")
                chart(teams[2], jr_te, week, "TE", "JR")

            got = C.read_started_seasons(cur, 2033)
            assert got.get(jr_override) == {2033}       # the JR man, not Mu's overridden starter
            assert mu_override not in got
            assert jr_seven not in got                  # seven starts are not a started season
            assert jr_switch not in got and mu_switch not in got   # switching away gives Mu no eighth
            assert got.get(jr_edge) == {2033}           # the mapped defensive slot
            assert mu_edge not in got
            assert got.get(jr_te) == {2033}             # JR's chart is in force for team 2
            assert got.get(mu_cleared) == {2033}        # ... and the WR slot JR left empty falls through to Mu
        finally:
            conn.rollback()


@pytest.mark.integration
def test_one_ended_row_per_player_when_retirement_follows_the_last_signed_season_in_postgres():
    """R3 on real Postgres, rolled back: a retirement recorded after the last signed season (2039 after
    2038) gives exactly one ended row per player whose position and age come from that last signed season,
    and A1/A2 agree at reports 2039, 2040 and 2041. A retirement-year FreeAgent row does not replace the
    snapshot, and a genuine later comeback moves the endpoint instead of leaving the old retirement."""
    import psycopg
    dsn = _scratch_dsn()
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM teams ORDER BY id LIMIT 1")
            pair = cur.fetchone()
            if not pair:
                pytest.skip("the scratch database has no teams")
            team = pair[0]

            def player(name):
                cur.execute("INSERT INTO players (player_name) VALUES (%s) RETURNING id", (name,))
                return cur.fetchone()[0]

            def season(pid, year, position, age, yoe, team_id=team):
                cur.execute("INSERT INTO player_seasons "
                            "(player_id, season, team_id, position, age, years_of_experience) "
                            "VALUES (%s, %s, %s, %s, %s, %s)", (pid, year, team_id, position, age, yoe))

            def retire(pid, year, name, position="WR"):
                cur.execute("INSERT INTO player_retirements (season, player_id, player_name, position) "
                            "VALUES (%s, %s, %s, %s)", (year, pid, name, position))

            late = player("R3 Retired After Last Season")
            season(late, 2037, "WR", 26, 4)
            season(late, 2038, "WR", 27, 5)
            retire(late, 2039, "R3 Retired After Last Season")

            for report in (2039, 2040, 2041):
                careers = [r for r in C.read_career_rows(cur, report) if r[0] == late]
                ages = [r for r in C.read_retirement_rows(cur, report) if r[0] == late]
                assert careers == [(late, 2039, 2037, "WR", 4), (late, 2039, 2038, "WR", 5)], report
                assert ages == [(late, 2039, "WR", 27)], report
                assert C.career_lengths(careers, {late})["drafted"]["WR"]["n"] == 1
                assert max(r[4] for r in careers) + 1 == 6
                assert C.retirement_ages(ages, {late})["drafted"]["WR"]["n"] == 1

            # the retirement year's own player_seasons row is a FreeAgent row: no team, no snapshot
            fa = player("R3 Retired With FreeAgent Year")
            season(fa, 2038, "WR", 27, 5)
            retire(fa, 2039, "R3 Retired With FreeAgent Year")
            season(fa, 2039, "WR", 28, 6, team_id=None)
            for report in (2039, 2041):
                assert [r for r in C.read_career_rows(cur, report) if r[0] == fa] == \
                    [(fa, 2039, 2038, "WR", 5)], report
                assert [r for r in C.read_retirement_rows(cur, report) if r[0] == fa] == \
                    [(fa, 2039, "WR", 27)], report

            # a genuine later signing reopens the career: the endpoint moves to the last signed season
            back = player("R3 Retired Then Came Back")
            season(back, 2038, "WR", 27, 5)
            retire(back, 2039, "R3 Retired Then Came Back")
            season(back, 2041, "WR", 30, 7)
            assert back not in {r[0] for r in C.read_career_rows(cur, 2041)}
            assert back not in {r[0] for r in C.read_retirement_rows(cur, 2041)}
            assert [r for r in C.read_career_rows(cur, 2043) if r[0] == back] == \
                [(back, 2041, 2038, "WR", 5), (back, 2041, 2041, "WR", 7)]
            assert [r for r in C.read_retirement_rows(cur, 2043) if r[0] == back] == \
                [(back, 2041, "WR", 30)]
        finally:
            conn.rollback()


# ------------------------------------------------------------------ P3-07 audit R1b/R2b/R4

@pytest.mark.integration
def test_a_played_season_survives_a_free_agent_overwrite_in_postgres():
    """R1b on real Postgres, rolled back: a season counts as signed when its player_seasons row has a team OR
    the player has games that season (season_stat_general, else player_stats). The offseason ingests carry the
    same season number, so a FreeAgent capture overwrites a played season's team with NULL; that season must
    stay in the career. Written through load_players, as the ingest writes it."""
    import psycopg

    from pipeline import load_save

    dsn = _scratch_dsn()
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM teams ORDER BY id LIMIT 2")
            teams = [r[0] for r in cur.fetchall()]
            if len(teams) < 2:
                pytest.skip("the scratch database has fewer than two teams")

            def snapshot(pgid, first, last, status, age, yoe):
                return {"players": [{
                    "pgid": pgid, "status": status, "first_name": first, "last_name": last,
                    "team_index": 0 if status != "FreeAgent" else load_save.FREE_AGENT_TEAM_INDEX,
                    "position": "WR", "age": age, "overall": 70, "years_pro": yoe,
                    "height_in": 72, "weight_lbs": 205, "jersey": 11,
                    "dev_trait": "Normal", "archetype": "WR_DeepThreat"}]}

            def load(pgid, first, last, status, season, age, yoe):
                load_save.load_players(cur, snapshot(pgid, first, last, status, age, yoe), season,
                                       {0: teams[0]}, {})

            def pid_of(pgid):
                cur.execute("SELECT id FROM players WHERE madden_pgid = %s", (pgid,))
                return cur.fetchone()[0]

            def played(pid, season, games=16):
                cur.execute("INSERT INTO season_stat_general "
                            "(season, player_id, team_id, position, games_played) "
                            "VALUES (%s, %s, %s, 'WR', %s)", (season, pid, teams[0], games))

            def career(pid, report):
                return [tuple(r) for r in C.read_career_rows(cur, report) if r[0] == pid]

            def ages(pid, report):
                return [tuple(r) for r in C.read_retirement_rows(cur, report) if r[0] == pid]

            # case 1: Signed 2035, Signed 2036, a later FreeAgent capture of the same 2036 (team NULL), and
            # 16 games in 2036. Only 2037 is unsigned, so report 2037 does not end him; report 2038 does, at
            # the 2036 played season (career rows include it; the age comes from its row).
            load(9_720_001, "R1b", "Played", "Signed", 2035, 24, 0)
            load(9_720_001, "R1b", "Played", "Signed", 2036, 25, 1)
            load(9_720_001, "R1b", "Played", "FreeAgent", 2036, 25, 1)
            one = pid_of(9_720_001)
            played(one, 2036)
            load(9_720_001, "R1b", "Played", "FreeAgent", 2037, 26, 2)
            assert career(one, 2037) == [] and ages(one, 2037) == []
            load(9_720_001, "R1b", "Played", "FreeAgent", 2038, 27, 3)
            assert career(one, 2038) == [(one, 2036, 2035, "WR", 0), (one, 2036, 2036, "WR", 1)]
            assert ages(one, 2038) == [(one, 2036, "WR", 25)]

            # case 2: the same shape with Signed 2037 instead of FreeAgent: the career never ends by 2038
            load(9_720_002, "R1b", "Signed On", "Signed", 2035, 24, 0)
            load(9_720_002, "R1b", "Signed On", "Signed", 2036, 25, 1)
            load(9_720_002, "R1b", "Signed On", "FreeAgent", 2036, 25, 1)
            two = pid_of(9_720_002)
            played(two, 2036)
            load(9_720_002, "R1b", "Signed On", "Signed", 2037, 26, 2)
            assert career(two, 2038) == [] and ages(two, 2038) == []

            # case 3: R1's original case still ends at the last signed season: 2037/2038 are NULL-team rows
            # with no games, so they are not signings.
            load(9_720_003, "R1b", "No Games", "Signed", 2035, 24, 0)
            load(9_720_003, "R1b", "No Games", "Signed", 2036, 25, 1)
            load(9_720_003, "R1b", "No Games", "FreeAgent", 2037, 26, 2)
            load(9_720_003, "R1b", "No Games", "FreeAgent", 2038, 27, 3)
            three = pid_of(9_720_003)
            assert career(three, 2038) == [(three, 2036, 2035, "WR", 0), (three, 2036, 2036, "WR", 1)]
            assert ages(three, 2038) == [(three, 2036, "WR", 25)]

            # case 4: a FreeAgent row with no games does not count as signed: the career still ends at 2035
            load(9_720_004, "R1b", "FA No Games", "Signed", 2035, 24, 0)
            load(9_720_004, "R1b", "FA No Games", "FreeAgent", 2036, 25, 1)
            four = pid_of(9_720_004)
            assert career(four, 2037) == [(four, 2035, 2035, "WR", 0)]
            assert ages(four, 2037) == [(four, 2035, "WR", 24)]

            # ...and the player_stats fallback: a NULL-team season with a stat line counts as played
            load(9_720_005, "R1b", "Stats Fallback", "Signed", 2040, 24, 0)
            load(9_720_005, "R1b", "Stats Fallback", "FreeAgent", 2041, 25, 1)
            five = pid_of(9_720_005)
            cur.execute("INSERT INTO games (season, week, home_team_id, away_team_id) "
                        "VALUES (2041, 1, %s, %s) RETURNING id", (teams[0], teams[1]))
            game_id = cur.fetchone()[0]
            cur.execute("INSERT INTO player_stats (game_id, player_name, player_id, team_id, position) "
                        "VALUES (%s, 'R1b Stats Fallback', %s, %s, 'WR')", (game_id, five, teams[0]))
            assert career(five, 2042) == [] and ages(five, 2042) == []
            assert career(five, 2043) == [(five, 2041, 2040, "WR", 0), (five, 2041, 2041, "WR", 1)]
            assert ages(five, 2043) == [(five, 2041, "WR", 25)]
        finally:
            conn.rollback()


@pytest.mark.integration
def test_the_chart_in_force_falls_back_per_slot_in_postgres():
    """R2b on real Postgres, rolled back: the chart in force resolves each slot by JR > MU_COACH > MADDEN.

    A MADDEN-only team's starter counts; a cleared JR slot (NULL player row, and separately no row at all)
    falls through to MU_COACH, and a slot Mu did not set falls through to MADDEN; a JR slot still beats Mu's
    man in the same slot; and KR/PR/SLWR never count even through the MADDEN fallback."""
    import psycopg
    dsn = _scratch_dsn()
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM teams ORDER BY id LIMIT 12")
            teams = [r[0] for r in cur.fetchall()]
            if len(teams) < 12:
                pytest.skip("the scratch database has fewer than twelve teams")

            def player(name):
                cur.execute("INSERT INTO players (player_name) VALUES (%s) RETURNING id", (name,))
                return cur.fetchone()[0]

            def roster(pid, team_id, position, season):
                cur.execute("INSERT INTO player_seasons (player_id, season, team_id, position) "
                            "VALUES (%s, %s, %s, %s)", (pid, season, team_id, position))

            def chart(team_id, pid, week, slot, source, season):
                cur.execute("INSERT INTO depth_charts "
                            "(team_id, season, week, position_slot, depth, player_id, source) "
                            "VALUES (%s, %s, %s, %s, 1, %s, %s)",
                            (team_id, season, week, slot, pid, source))

            def games(season, home, away, weeks):
                for week in weeks:
                    cur.execute("INSERT INTO games (season, week, home_team_id, away_team_id) "
                                "VALUES (%s, %s, %s, %s)", (season, week, home, away))

            # case 1: a MADDEN-only team, WR1 = M for weeks 1-8 -> M has a started season
            games(2034, teams[0], teams[1], range(1, 9))
            madden_wr = player("R2b MADDEN WR")
            roster(madden_wr, teams[0], "WR", 2034)
            for week in range(1, 9):
                chart(teams[0], madden_wr, week, "WR", "MADDEN", 2034)

            # case 2a: JR's chart sets QB1 and leaves WR1 with a NULL player; MU_COACH's WR1 = W.
            games(2035, teams[2], teams[3], range(1, 9))
            jr_qb = player("R2b JR QB")
            mu_wr = player("R2b MU WR Behind NULL JR")
            roster(jr_qb, teams[2], "QB", 2035)
            roster(mu_wr, teams[2], "WR", 2035)
            for week in range(1, 9):
                chart(teams[2], jr_qb, week, "QB", "JR", 2035)
                chart(teams[2], None, week, "WR", "JR", 2035)
                chart(teams[2], mu_wr, week, "WR", "MU_COACH", 2035)

            # case 2b: JR's chart has no WR row at all; MU_COACH's WR1 = W.
            games(2036, teams[4], teams[5], range(1, 9))
            jr_qb2 = player("R2b JR QB Absent Slot")
            mu_wr2 = player("R2b MU WR Behind Absent JR")
            roster(jr_qb2, teams[4], "QB", 2036)
            roster(mu_wr2, teams[4], "WR", 2036)
            for week in range(1, 9):
                chart(teams[4], jr_qb2, week, "QB", "JR", 2036)
                chart(teams[4], mu_wr2, week, "WR", "MU_COACH", 2036)

            # case 2c: no MU row either; MADDEN's WR1 = X is the last fallback.
            games(2037, teams[6], teams[7], range(1, 9))
            jr_qb3 = player("R2b JR QB MADDEN WR")
            madden_wr2 = player("R2b MADDEN WR Fallback")
            roster(jr_qb3, teams[6], "QB", 2037)
            roster(madden_wr2, teams[6], "WR", 2037)
            for week in range(1, 9):
                chart(teams[6], jr_qb3, week, "QB", "JR", 2037)
                chart(teams[6], madden_wr2, week, "WR", "MADDEN", 2037)

            # case 3: MU WR1 = A, JR WR1 = B -> only B
            games(2038, teams[8], teams[9], range(1, 9))
            mu_a = player("R2b MU WR A")
            jr_b = player("R2b JR WR B")
            for pid in (mu_a, jr_b):
                roster(pid, teams[8], "WR", 2038)
            for week in range(1, 9):
                chart(teams[8], mu_a, week, "WR", "MU_COACH", 2038)
                chart(teams[8], jr_b, week, "WR", "JR", 2038)

            # case 7: a returner/personal-protector slot never counts, even as MADDEN's fallback
            games(2043, teams[10], teams[11], range(1, 9))
            kr = player("R2b MADDEN KR")
            pr = player("R2b MADDEN PR")
            slwr = player("R2b MADDEN SLWR")
            slot_wr = player("R2b MADDEN Slot WR")
            for pid in (kr, pr, slwr, slot_wr):
                roster(pid, teams[10], "WR", 2043)
            for week in range(1, 9):
                chart(teams[10], kr, week, "KR", "MADDEN", 2043)
                chart(teams[10], pr, week, "PR", "MADDEN", 2043)
                chart(teams[10], slwr, week, "SLWR", "MADDEN", 2043)
                chart(teams[10], slot_wr, week, "WR", "MADDEN", 2043)

            # case 8: a second team credited from MU_COACH in the same weeks another team has a JR chart
            games(2044, teams[2], teams[3], range(1, 9))
            games(2044, teams[4], teams[5], range(1, 9))
            jr8 = player("R2b JR Chart Team")
            mu8 = player("R2b MU Chart Team")
            roster(jr8, teams[2], "WR", 2044)
            roster(mu8, teams[4], "WR", 2044)
            for week in range(1, 9):
                chart(teams[2], jr8, week, "WR", "JR", 2044)
                chart(teams[4], mu8, week, "WR", "MU_COACH", 2044)

            got = {}
            for season in (2034, 2035, 2036, 2037, 2038, 2043, 2044):
                got.update(C.read_started_seasons(cur, season))

            assert got.get(madden_wr) == {2034}          # case 1: MADDEN is the fallback chart
            assert got.get(mu_wr) == {2035}              # case 2a: NULL JR player falls through
            assert got.get(jr_qb) == {2035}
            assert got.get(mu_wr2) == {2036}             # case 2b: absent JR slot falls through
            assert got.get(jr_qb2) == {2036}
            assert got.get(madden_wr2) == {2037}         # case 2c: no MU row, MADDEN's man starts
            assert got.get(jr_qb3) == {2037}
            assert got.get(jr_b) == {2038}               # case 3: JR beats MU in the same slot
            assert mu_a not in got
            assert got.get(slot_wr) == {2043}            # case 7: a real WR slot still counts
            assert kr not in got and pr not in got and slwr not in got
            assert got.get(jr8) == {2044}                # case 8: both teams, same weeks
            assert got.get(mu8) == {2044}
        finally:
            conn.rollback()


@pytest.mark.integration
def test_the_chart_in_force_carries_forward_in_postgres():
    """R2b on real Postgres, rolled back: the newest week with any stored chart carries forward.

    A week-1 chart runs to the next stored chart (newest week wins); a bye week's chart is not a game; 8
    weeks is the threshold; a chart stored under the season with week 31 or 32 (that year's cutdown and
    preseason) sorts before week 1, while week 23 (postseason) never reaches a regular-season week and a
    week-26 (offseason) chart belongs to the next season; and a MADDEN read-back at week 5 beats JR's week 3
    for the weeks after it."""
    import psycopg
    dsn = _scratch_dsn()
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM teams ORDER BY id LIMIT 8")
            teams = [r[0] for r in cur.fetchall()]
            if len(teams) < 8:
                pytest.skip("the scratch database has fewer than eight teams")

            def player(name):
                cur.execute("INSERT INTO players (player_name) VALUES (%s) RETURNING id", (name,))
                return cur.fetchone()[0]

            def roster(pid, team_id, position, season):
                cur.execute("INSERT INTO player_seasons (player_id, season, team_id, position) "
                            "VALUES (%s, %s, %s, %s)", (pid, season, team_id, position))

            def chart(team_id, pid, week, slot, source, season):
                cur.execute("INSERT INTO depth_charts "
                            "(team_id, season, week, position_slot, depth, player_id, source) "
                            "VALUES (%s, %s, %s, %s, 1, %s, %s)",
                            (team_id, season, week, slot, pid, source))

            def games(season, home, away, weeks):
                for week in weeks:
                    cur.execute("INSERT INTO games (season, week, home_team_id, away_team_id) "
                                "VALUES (%s, %s, %s, %s)", (season, week, home, away))

            # case 4: MU chart week 1 (C), then week 5 (D) over 16 games: C 4 weeks, D 12 -> only D
            games(2039, teams[0], teams[1], range(1, 17))
            c = player("R2b Carried C")
            d = player("R2b Carry Newest D")
            for pid in (c, d):
                roster(pid, teams[0], "WR", 2039)
            chart(teams[0], c, 1, "WR", "MU_COACH", 2039)
            chart(teams[0], d, 5, "WR", "MU_COACH", 2039)

            # case 5: a week-1 chart carried over 7 games and a bye is not a started season
            games(2040, teams[2], teams[3], range(1, 9))
            games(2040, teams[0], teams[1], range(1, 8))       # teams[0]'s week 8 is a bye
            bye = player("R2b Bye Seven Games")
            control = player("R2b Bye Control Eight")
            roster(bye, teams[0], "WR", 2040)
            roster(control, teams[2], "WR", 2040)
            chart(teams[0], bye, 1, "WR", "MU_COACH", 2040)
            chart(teams[2], control, 1, "WR", "MU_COACH", 2040)

            # case 6: 7 weeks is not a started season, 8 is (separate teams: a later chart replaces the slot)
            games(2041, teams[4], teams[5], range(1, 9))
            games(2041, teams[6], teams[7], range(1, 9))
            seven = player("R2b Seven From Week Two")
            eight = player("R2b Eight From Week One")
            roster(seven, teams[6], "WR", 2041)
            roster(eight, teams[4], "WR", 2041)
            chart(teams[6], seven, 2, "WR", "MU_COACH", 2041)
            chart(teams[4], eight, 1, "WR", "MU_COACH", 2041)

            # case 9: a (season, 32) chart sorts before week 1; a (season, 23) chart is irrelevant
            games(2045, teams[6], teams[7], range(1, 9))
            games(2045, teams[0], teams[1], range(1, 9))
            preseason = player("R2b Preseason Chart")
            postseason = player("R2b Postseason Chart")
            roster(preseason, teams[6], "WR", 2045)
            roster(postseason, teams[0], "WR", 2045)
            chart(teams[6], preseason, 32, "WR", "MU_COACH", 2045)
            chart(teams[0], postseason, 23, "WR", "MU_COACH", 2045)

            # case 9b (F2): a (season, 26) chart is the offseason, not before week 1 -> not in force
            games(2045, teams[4], teams[5], range(1, 9))
            offseason = player("R2b Offseason Week Twenty Six")
            roster(offseason, teams[4], "WR", 2045)
            chart(teams[4], offseason, 26, "WR", "MU_COACH", 2045)

            # case 10: JR week 3, MADDEN week 5 only -> weeks 5-16 use MADDEN's chart
            games(2046, teams[2], teams[3], range(1, 17))
            jr3 = player("R2b JR Week Three")
            madden5 = player("R2b MADDEN Week Five")
            roster(jr3, teams[2], "WR", 2046)
            roster(madden5, teams[2], "WR", 2046)
            chart(teams[2], jr3, 3, "WR", "JR", 2046)
            chart(teams[2], madden5, 5, "WR", "MADDEN", 2046)

            got = {}
            for season in (2039, 2040, 2041, 2045, 2046):
                got.update(C.read_started_seasons(cur, season))

            assert got.get(d) == {2039} and c not in got        # case 4: newest week wins
            assert got.get(control) == {2040} and bye not in got  # case 5: a bye is not a game
            assert got.get(eight) == {2041} and seven not in got  # case 6: the 8-week threshold
            assert got.get(preseason) == {2045}                  # case 9: (season, 32) before week 1
            assert postseason not in got                         # ... (season, 23) is not
            assert offseason not in got                          # case 9b: (season, 26) is not either
            assert got.get(madden5) == {2046} and jr3 not in got  # case 10: newest week wins
        finally:
            conn.rollback()


def _read_started_with_rows(cur, season):
    """read_started_seasons() plus the raw rows it resolved, so a test can assert the credited weeks."""
    rows = []
    orig = C.started_seasons
    C.started_seasons = lambda resolved: (rows.extend(resolved), orig(resolved))[1]
    try:
        return C.read_started_seasons(cur, season), rows
    finally:
        C.started_seasons = orig


@pytest.mark.integration
def test_the_chart_falls_back_per_slot_across_weeks_in_postgres():
    """JR's per-slot fallback across weeks, on real Postgres, rolled back (repro j flipped).

    A full MU chart at week 3 followed by a JR chart at week 5 that sets only QB1: the WR slot is empty in
    the newest chart week, so it keeps week 3's starter for weeks 3-10 (8 games, a started season), while QB1
    is JR's man from week 5."""
    import psycopg
    dsn = _scratch_dsn()
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM teams ORDER BY id LIMIT 2")
            pair = [r[0] for r in cur.fetchall()]
            if len(pair) < 2:
                pytest.skip("the scratch database has fewer than two teams")
            team, other = pair

            def player(name):
                cur.execute("INSERT INTO players (player_name) VALUES (%s) RETURNING id", (name,))
                return cur.fetchone()[0]

            def roster(pid, position, year=2050):
                cur.execute("INSERT INTO player_seasons (player_id, season, team_id, position) "
                            "VALUES (%s, %s, %s, %s)", (pid, year, team, position))

            def chart(pid, week, slot, source, year=2050):
                cur.execute("INSERT INTO depth_charts "
                            "(team_id, season, week, position_slot, depth, player_id, source) "
                            "VALUES (%s, %s, %s, %s, 1, %s, %s)",
                            (team, year, week, slot, pid, source))

            for week in range(1, 11):
                cur.execute("INSERT INTO games (season, week, home_team_id, away_team_id) "
                            "VALUES (2050, %s, %s, %s)", (week, team, other))

            wr = player("A6j MU WR Week Three")
            qb = player("A6j JR QB Week Five")
            roster(wr, "WR")
            roster(qb, "QB")
            chart(wr, 3, "WR", "MU_COACH")       # a full chart, then a week-5 JR chart that sets only QB
            chart(qb, 5, "QB", "JR")

            got, rows = _read_started_with_rows(cur, 2050)
            assert sorted(r[2] for r in rows if r[0] == wr) == list(range(3, 11))
            assert sorted(r[2] for r in rows if r[0] == qb) == list(range(5, 11))
            assert got.get(wr) == {2050}         # nine weeks of the ten? no: weeks 3-10 are eight games
            assert qb not in got                 # six starts are below the eight-week threshold
        finally:
            conn.rollback()


@pytest.mark.integration
def test_the_chart_fallback_uses_the_newest_filler_week_in_postgres():
    """JR's newest-week-wins fallback, on real Postgres, rolled back: WR1 set by MU at week 1 (A) and by
    MADDEN at week 4 (B), then a JR chart at week 6 that sets only QB. The WR slot keeps the newest week
    that filled it: A for weeks 1-3, B from week 4 on (a newer MADDEN-only week beats the older MU week)."""
    import psycopg
    dsn = _scratch_dsn()
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM teams ORDER BY id LIMIT 2")
            pair = [r[0] for r in cur.fetchall()]
            if len(pair) < 2:
                pytest.skip("the scratch database has fewer than two teams")
            team, other = pair

            def player(name):
                cur.execute("INSERT INTO players (player_name) VALUES (%s) RETURNING id", (name,))
                return cur.fetchone()[0]

            def roster(pid, position):
                cur.execute("INSERT INTO player_seasons (player_id, season, team_id, position) "
                            "VALUES (%s, 2051, %s, %s)", (pid, team, position))

            def chart(pid, week, slot, source):
                cur.execute("INSERT INTO depth_charts "
                            "(team_id, season, week, position_slot, depth, player_id, source) "
                            "VALUES (%s, 2051, %s, %s, 1, %s, %s)", (team, week, slot, pid, source))

            for week in range(1, 11):
                cur.execute("INSERT INTO games (season, week, home_team_id, away_team_id) "
                            "VALUES (2051, %s, %s, %s)", (week, team, other))

            old_mu = player("A6k MU WR Week One")
            newest_madden = player("A6k MADDEN WR Week Four")
            qb = player("A6k JR QB Week Six")
            roster(old_mu, "WR")
            roster(newest_madden, "WR")
            roster(qb, "QB")
            chart(old_mu, 1, "WR", "MU_COACH")
            chart(newest_madden, 4, "WR", "MADDEN")
            chart(qb, 6, "QB", "JR")

            _got, rows = _read_started_with_rows(cur, 2051)
            assert sorted(r[2] for r in rows if r[0] == old_mu) == [1, 2, 3]
            assert sorted(r[2] for r in rows if r[0] == newest_madden) == list(range(4, 11))
            assert sorted(r[2] for r in rows if r[0] == qb) == list(range(6, 11))
        finally:
            conn.rollback()


@pytest.mark.integration
def test_an_offseason_chart_counts_for_the_next_season_in_postgres():
    """JR's offseason ruling, on real Postgres, rolled back: a chart stored as (2052, 26) is part of 2053's
    pre-week-1 history. JR's WR1 = C is in force for 2053 weeks 1-2; MU's week-3 chart (D) takes over from
    week 3. The same chart credits no 2052 week, and C's 2053 weeks are not a started season (2 games)."""
    import psycopg
    dsn = _scratch_dsn()
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM teams ORDER BY id LIMIT 2")
            pair = [r[0] for r in cur.fetchall()]
            if len(pair) < 2:
                pytest.skip("the scratch database has fewer than two teams")
            team, other = pair

            def player(name):
                cur.execute("INSERT INTO players (player_name) VALUES (%s) RETURNING id", (name,))
                return cur.fetchone()[0]

            def roster(pid, year):
                cur.execute("INSERT INTO player_seasons (player_id, season, team_id, position) "
                            "VALUES (%s, %s, %s, 'WR')", (pid, year, team))

            def chart(pid, year, week, source):
                cur.execute("INSERT INTO depth_charts "
                            "(team_id, season, week, position_slot, depth, player_id, source) "
                            "VALUES (%s, %s, %s, 'WR', 1, %s, %s)", (team, year, week, pid, source))

            def games(year):
                for week in range(1, 11):
                    cur.execute("INSERT INTO games (season, week, home_team_id, away_team_id) "
                                "VALUES (%s, %s, %s, %s)", (year, week, team, other))

            offseason = player("A6l Offseason WR C")
            week_three = player("A6l MU WR D Week Three")
            roster(offseason, 2052)               # rostered in both seasons, so a leak into 2052 would show
            roster(offseason, 2053)
            roster(week_three, 2053)
            games(2052)
            games(2053)
            chart(offseason, 2052, 26, "JR")      # (2052, 26) -> 2053's pre-week-1 chart
            chart(week_three, 2053, 3, "MU_COACH")

            got, rows = _read_started_with_rows(cur, 2053)
            assert [r[1] for r in rows if r[0] == offseason] == [2053] * 2
            assert sorted(r[2] for r in rows if r[0] == offseason) == [1, 2]
            assert sorted(r[2] for r in rows if r[0] == week_three) == list(range(3, 11))
            assert got.get(week_three) == {2053}  # weeks 3-10: eight games
            assert offseason not in got          # only two games
        finally:
            conn.rollback()


@pytest.mark.integration
def test_offseason_and_cutdown_charts_sort_before_week_one_in_postgres():
    """JR's pre-week-1 ordering, on real Postgres, rolled back: (Y-1, 26) comes before (Y, 31), so with both
    in force at week 1 the cutdown chart's man E starts. A team with only the offseason chart (F) still has
    him in force from week 1: the offseason chart counts, it is not ignored."""
    import psycopg
    dsn = _scratch_dsn()
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM teams ORDER BY id LIMIT 4")
            teams = [r[0] for r in cur.fetchall()]
            if len(teams) < 4:
                pytest.skip("the scratch database has fewer than four teams")
            team, other, second, other2 = teams

            def player(name):
                cur.execute("INSERT INTO players (player_name) VALUES (%s) RETURNING id", (name,))
                return cur.fetchone()[0]

            def roster(pid, team_id):
                cur.execute("INSERT INTO player_seasons (player_id, season, team_id, position) "
                            "VALUES (%s, 2055, %s, 'WR')", (pid, team_id))

            def chart(team_id, pid, year, week, source):
                cur.execute("INSERT INTO depth_charts "
                            "(team_id, season, week, position_slot, depth, player_id, source) "
                            "VALUES (%s, %s, %s, 'WR', 1, %s, %s)", (team_id, year, week, pid, source))

            for week in range(1, 11):
                cur.execute("INSERT INTO games (season, week, home_team_id, away_team_id) "
                            "VALUES (2055, %s, %s, %s)", (week, team, other))
                cur.execute("INSERT INTO games (season, week, home_team_id, away_team_id) "
                            "VALUES (2055, %s, %s, %s)", (week, second, other2))

            offseason_c = player("A6m Offseason WR C")
            cutdown_e = player("A6m Cutdown WR E")
            offseason_f = player("A6m Offseason WR F Only")
            roster(offseason_c, team)
            roster(cutdown_e, team)
            roster(offseason_f, second)
            chart(team, offseason_c, 2054, 26, "MU_COACH")    # (Y-1, 26) is older than (Y, 31)
            chart(team, cutdown_e, 2055, 31, "MU_COACH")
            chart(second, offseason_f, 2054, 26, "MU_COACH")

            got, rows = _read_started_with_rows(cur, 2055)
            week_one = [r[0] for r in rows if r[2] == 1]
            assert cutdown_e in week_one                # (Y, 31) is newer than (Y-1, 26)
            assert offseason_c not in week_one
            assert not [r for r in rows if r[0] == offseason_c]                        # never in force
            assert sorted(r[2] for r in rows if r[0] == offseason_f) == list(range(1, 11))
            assert got.get(cutdown_e) == {2055} and got.get(offseason_f) == {2055}
            assert offseason_c not in got
        finally:
            conn.rollback()


@pytest.mark.integration
def test_an_offseason_chart_does_not_cross_two_seasons_in_postgres():
    """JR's one-boundary ruling, on real Postgres, rolled back: (2055, 26) belongs to 2056, not 2057, so a
    2057 read takes the newer (2056, 26) chart's man H from week 1 and never the 2055 chart's man G. A second
    (2055, 26) chart with no newer chart on its team (a lone Y-2 chart) is never credited either, so a leak
    cannot hide behind the newer chart's row."""
    import psycopg
    dsn = _scratch_dsn()
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM teams ORDER BY id LIMIT 2")
            pair = [r[0] for r in cur.fetchall()]
            if len(pair) < 2:
                pytest.skip("the scratch database has fewer than two teams")
            team, other = pair

            def player(name):
                cur.execute("INSERT INTO players (player_name) VALUES (%s) RETURNING id", (name,))
                return cur.fetchone()[0]

            def roster(pid, team_id=None):
                cur.execute("INSERT INTO player_seasons (player_id, season, team_id, position) "
                            "VALUES (%s, 2057, %s, 'WR')", (pid, team_id or team))

            def chart(pid, year, team_id=None):
                cur.execute("INSERT INTO depth_charts "
                            "(team_id, season, week, position_slot, depth, player_id, source) "
                            "VALUES (%s, %s, 26, 'WR', 1, %s, 'MU_COACH')", (team_id or team, year, pid))

            for week in range(1, 11):
                cur.execute("INSERT INTO games (season, week, home_team_id, away_team_id) "
                            "VALUES (2057, %s, %s, %s)", (week, team, other))

            two_back = player("A6n Offseason WR G Two Back")
            one_back = player("A6n Offseason WR H One Back")
            lone_two_back = player("A6n Offseason WR I Lone Two Back")
            roster(two_back)
            roster(one_back)
            roster(lone_two_back, other)
            chart(two_back, 2055)                 # (Y-2, 26): maps to 2056 only, never 2057
            chart(one_back, 2056)                 # (Y-1, 26): the only chart 2057 can see
            chart(lone_two_back, 2055, other)     # (Y-2, 26) alone: no newer chart can mask a leak

            got, rows = _read_started_with_rows(cur, 2057)
            assert not [r for r in rows if r[0] == two_back]
            assert not [r for r in rows if r[0] == lone_two_back]
            assert sorted(r[2] for r in rows if r[0] == one_back) == list(range(1, 11))
            assert got.get(one_back) == {2057}
            assert two_back not in got
            assert lone_two_back not in got
        finally:
            conn.rollback()


@pytest.mark.integration
def test_one_call_reads_every_season_through_the_report_season_in_postgres():
    """F1 on real Postgres, rolled back: one read_started_seasons call covers every season through the
    report season, which is what A6's "2+ started seasons" share needs.

    A player who started 8+ weeks in both 2035 and 2036 is read by a single call for 2036, so
    draft_outcomes counts him as a 2+ started-season career; a 2037 season of his stays out of that read."""
    import psycopg
    dsn = _scratch_dsn()
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM teams ORDER BY id LIMIT 2")
            pair = [r[0] for r in cur.fetchall()]
            if len(pair) < 2:
                pytest.skip("the scratch database has fewer than two teams")
            team, other = pair

            def player(name):
                cur.execute("INSERT INTO players (player_name) VALUES (%s) RETURNING id", (name,))
                return cur.fetchone()[0]

            pid = player("F1 Multi Season Starter")
            for season in (2035, 2036, 2037):
                cur.execute("INSERT INTO player_seasons (player_id, season, team_id, position) "
                            "VALUES (%s, %s, %s, 'WR')", (pid, season, team))
                cur.execute("INSERT INTO depth_charts "
                            "(team_id, season, week, position_slot, depth, player_id, source) "
                            "VALUES (%s, %s, 1, 'WR', 1, %s, 'MU_COACH')", (team, season, pid))
                for week in range(1, 9):
                    cur.execute("INSERT INTO games (season, week, home_team_id, away_team_id) "
                                "VALUES (%s, %s, %s, %s)", (season, week, team, other))

            got = C.read_started_seasons(cur, 2036)
            assert got.get(pid) == {2035, 2036}                  # one call, every season through 2036

            outcomes = C.draft_outcomes([(pid, 2034, "1", "WR", 16)], got, 2036)
            assert outcomes["1"]["share_started_2plus"] == 1.0
            assert outcomes["1"]["n"] == 1
        finally:
            conn.rollback()


@pytest.mark.integration
def test_free_agent_retirees_are_counted_in_postgres():
    """R4 on real Postgres, rolled back: a retiree with no signed-or-played season is still an ended career.

    The endpoint stays the retirement season, and the row it reports comes from the retirement-season
    player_seasons row; a retiree with a signed 2035 and a FreeAgent 2036 with no games reports the 2035
    snapshot; and a later comeback still supersedes the old retirement."""
    import psycopg
    dsn = _scratch_dsn()
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        try:
            cur.execute("SELECT id FROM teams ORDER BY id LIMIT 1")
            pair = cur.fetchone()
            if not pair:
                pytest.skip("the scratch database has no teams")
            team = pair[0]

            def player(name):
                cur.execute("INSERT INTO players (player_name) VALUES (%s) RETURNING id", (name,))
                return cur.fetchone()[0]

            def season(pid, year, position, age, yoe, team_id=team):
                cur.execute("INSERT INTO player_seasons "
                            "(player_id, season, team_id, position, age, years_of_experience) "
                            "VALUES (%s, %s, %s, %s, %s, %s)", (pid, year, team_id, position, age, yoe))

            def retire(pid, year, name, position="WR"):
                cur.execute("INSERT INTO player_retirements (season, player_id, player_name, position) "
                            "VALUES (%s, %s, %s, %s)", (year, pid, name, position))

            def career(pid, report):
                return [tuple(r) for r in C.read_career_rows(cur, report) if r[0] == pid]

            def ages(pid, report):
                return [tuple(r) for r in C.read_retirement_rows(cur, report) if r[0] == pid]

            # case (g): only Mu rows are FreeAgent, then he retires: one ended row, snapshot from that row
            fa_only = player("R4 FA All Season")
            season(fa_only, 2036, "WR", 33, 10, team_id=None)
            retire(fa_only, 2036, "R4 FA All Season")
            assert career(fa_only, 2036) == [(fa_only, 2036, 2036, "WR", 10)]
            assert ages(fa_only, 2036) == [(fa_only, 2036, "WR", 33)]
            lengths = C.career_lengths(career(fa_only, 2036), {fa_only})
            assert lengths["drafted"]["WR"]["n"] == 1
            assert C.retirement_ages(ages(fa_only, 2036), {fa_only})["drafted"]["WR"]["n"] == 1

            # a signed 2035 and a FreeAgent 2036 with no games: the snapshot is the 2035 row
            signed = player("R4 Signed Then FA")
            season(signed, 2035, "WR", 27, 5)
            season(signed, 2036, "WR", 28, 6, team_id=None)
            retire(signed, 2036, "R4 Signed Then FA")
            assert career(signed, 2036) == [(signed, 2036, 2035, "WR", 5)]
            assert ages(signed, 2036) == [(signed, 2036, "WR", 27)]

            # the R3 comeback still supersedes the old retirement: endpoint and snapshot move to 2038
            back = player("R4 Retired Then Came Back")
            season(back, 2035, "WR", 26, 4)
            retire(back, 2036, "R4 Retired Then Came Back")
            season(back, 2038, "WR", 29, 7)
            retire(back, 2039, "R4 Retired Then Came Back")
            assert career(back, 2041) == [(back, 2039, 2035, "WR", 4), (back, 2039, 2038, "WR", 7)]
            assert ages(back, 2041) == [(back, 2039, "WR", 29)]
        finally:
            conn.rollback()


# ------------------------------------------------------------------ cohort gates, end to end (F4/F5)

COHORT_TABLES = ("player_stats, season_stat_general, player_seasons, depth_charts, draft_prospects, "
                 "player_retirements, players, games, coach_tenures, coach_hot_seat, staff_moves, "
                 "gm_trade_proposals, league_office_runs")


def _empty_the_league(cur):
    """Empty the tables a cohort test seeds, inside its own transaction.

    The scratch database may be a copy of a real tuning league (the p2g-shape test needs exactly that), so a
    gate-level test starts from an empty league rather than depending on every row already in it. TRUNCATE is
    transactional: the test's `conn.rollback()` puts the database back exactly as it was.
    """
    cur.execute(f"TRUNCATE TABLE {COHORT_TABLES} RESTART IDENTITY CASCADE")


@pytest.mark.integration
def test_a6_gates_a_drafted_classes_started_seasons_in_postgres():
    """F4 end to end on real Postgres, rolled back: the started-season cohort reaches the A6 gates.

    A drafted class of 16 (players.draft_round 1-7 plus linked actual picks): 15 hold their own slot in 8+
    games of both 2028 and 2029, and one holds it for 7 weeks in 2029 only. `read_started_seasons` is the only
    route by which a season counts as started, so this proves the cohort A6 scores is the one F4 builds: a
    measured A6.round1 share of 15/16 (0.9375), never NOT MEASURED.
    """
    import psycopg
    dsn = _scratch_dsn()
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        try:
            _empty_the_league(cur)
            cur.execute("SELECT id FROM teams ORDER BY id LIMIT 2")
            teams = [r[0] for r in cur.fetchall()]
            if len(teams) < 2:
                pytest.skip("the scratch database has fewer than two teams")
            home, away = teams
            for season in (2028, 2029):
                for week in range(1, 19):
                    cur.execute("INSERT INTO games "
                                "(season, week, home_team_id, away_team_id, status, game_type) "
                                "VALUES (%s, %s, %s, %s, 'COMPLETED', 'REGULAR_SEASON')",
                                (season, week, home, away))
            positions = ["QB", "HB", "WR", "TE", "LT", "LG", "C", "RG", "RT",
                         "LE", "RE", "DT", "MLB", "LOLB", "ROLB", "CB"]
            for i, position in enumerate(positions):
                cur.execute("INSERT INTO players (player_name, draft_round) VALUES (%s, 1) RETURNING id",
                            (f"A6 Cohort {position}",))
                pid = cur.fetchone()[0]
                for season in (2028, 2029):
                    cur.execute("INSERT INTO player_seasons (player_id, season, team_id, position) "
                                "VALUES (%s, %s, %s, %s)", (pid, season, home, position))
                charted = (2028, 2029) if i < 15 else (2029,)   # the 16th: 7 weeks is not a started season
                for season in charted:
                    for week in range(1, 8 if i >= 15 else 9):
                        cur.execute("INSERT INTO depth_charts "
                                    "(team_id, season, week, position_slot, depth, player_id, source) "
                                    "VALUES (%s, %s, %s, %s, 1, %s, 'MU_COACH')",
                                    (home, season, week, position, pid))
                cur.execute("INSERT INTO draft_prospects "
                            "(draft_season, full_name, position, player_id, actual_round, actual_pick) "
                            "VALUES (2028, %s, %s, %s, '1', %s)",
                            (f"A6 Cohort {position}", position, pid, i + 1))
            obs = C.extract(cur, 2030, league_current_season=2030)
            rep = C.compare_decisions(obs, targets())
            assert obs["draft_by_round"]["1"]["n"] == 16
            assert obs["draft_by_round"]["1"]["share_started_2plus"] == round(15 / 16, 3)
            assert _gate(rep, "A6.round1").verdict in (C.PASS, C.FAIL)
        finally:
            conn.rollback()


@pytest.mark.integration
def test_a1_a2_gate_the_ended_career_cohort_in_postgres():
    """F5 end to end on real Postgres, rolled back: the ended-career population reaches the A1/A2 gates.

    Fifteen drafted QBs retire at 29 after six seasons (`years_of_experience` 5); a sixteenth is an unsigned
    free agent whose last signed-or-played season is 2027, two empty seasons before the 2030 report. All
    sixteen are the A1/A2 cohort, so both gates are measured (n=16, median length 6, median age 29) rather
    than NOT MEASURED, and the unsigned free agent is inside them.
    """
    import psycopg
    dsn = _scratch_dsn()
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        try:
            _empty_the_league(cur)
            cur.execute("SELECT id FROM teams ORDER BY id LIMIT 1")
            row = cur.fetchone()
            if not row:
                pytest.skip("the scratch database has no teams")
            team = row[0]
            for i in range(15):
                name = f"A1 Cohort Retired {i}"
                cur.execute("INSERT INTO players (player_name, draft_round) VALUES (%s, 1) RETURNING id",
                            (name,))
                pid = cur.fetchone()[0]
                cur.execute("INSERT INTO player_seasons "
                            "(player_id, season, team_id, position, age, years_of_experience) "
                            "VALUES (%s, 2029, %s, 'QB', 29, 5)", (pid, team))
                cur.execute("INSERT INTO player_retirements (season, player_id, player_name, position) "
                            "VALUES (2029, %s, %s, 'QB')", (pid, name))
            cur.execute("INSERT INTO players (player_name, draft_round) "
                        "VALUES ('A1 Cohort Unsigned', 1) RETURNING id")
            free_agent = cur.fetchone()[0]
            cur.execute("INSERT INTO player_seasons "
                        "(player_id, season, team_id, position, age, years_of_experience) "
                        "VALUES (%s, 2027, %s, 'QB', 28, 4)", (free_agent, team))
            obs = C.extract(cur, 2030, league_current_season=2030)
            rep = C.compare_decisions(obs, targets())
            assert obs["career_length"]["drafted"]["QB"]["n"] == 16
            assert obs["career_length"]["drafted"]["QB"]["median"] == 6
            assert obs["retirement_age"]["drafted"]["QB"]["n"] == 16
            assert obs["retirement_age"]["drafted"]["QB"]["median"] == 29
            assert _gate(rep, "A1.QB").verdict == C.PASS
            assert _gate(rep, "A2.QB").verdict == C.PASS
        finally:
            conn.rollback()


@pytest.mark.integration
def test_a_lone_offseason_chart_two_seasons_back_credits_only_its_own_next_season_in_postgres():
    """A6/JR's no-double-boundary rule on real Postgres, rolled back, in its literal case.

    The audit of `76a22fa` noted the existing no-double-boundary test pairs a (Y-1, 26) chart with the
    (Y-2, 26) one, so a multi-boundary bug would only tie on `ord` and the test would depend on Postgres's
    tie order. Here a single (2060, 26) chart is the only chart in the database: it credits 2061 and never
    2062, with nothing else to break a tie.
    """
    import psycopg
    dsn = _scratch_dsn()
    with psycopg.connect(dsn) as conn:
        cur = conn.cursor()
        try:
            _empty_the_league(cur)
            cur.execute("SELECT id FROM teams ORDER BY id LIMIT 2")
            teams = [r[0] for r in cur.fetchall()]
            if len(teams) < 2:
                pytest.skip("the scratch database has fewer than two teams")
            home, away = teams
            for season in (2061, 2062):
                for week in range(1, 11):
                    cur.execute("INSERT INTO games "
                                "(season, week, home_team_id, away_team_id, status, game_type) "
                                "VALUES (%s, %s, %s, %s, 'COMPLETED', 'REGULAR_SEASON')",
                                (season, week, home, away))
            cur.execute("INSERT INTO players (player_name, draft_round) VALUES ('Lone Chart WR', 1) "
                        "RETURNING id")
            pid = cur.fetchone()[0]
            for season in (2061, 2062):
                cur.execute("INSERT INTO player_seasons (player_id, season, team_id, position) "
                            "VALUES (%s, %s, %s, 'WR')", (pid, season, home))
            cur.execute("INSERT INTO depth_charts "
                        "(team_id, season, week, position_slot, depth, player_id, source) "
                        "VALUES (%s, 2060, 26, 'WR', 1, %s, 'JR')", (home, pid))
            assert C.read_started_seasons(cur, 2061).get(pid) == {2061}
            later = C.read_started_seasons(cur, 2062)
            assert later.get(pid) == {2061}          # 2061 is a real start; 2062 must not be credited
            assert 2062 not in later.get(pid, set())
        finally:
            conn.rollback()
