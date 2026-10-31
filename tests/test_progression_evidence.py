"""Progression evidence is cutoff-bounded, opportunity-aware and reward-free."""
import pytest
from engines.progression_evidence import build_evidence, due_checkpoints


def game(n, **changes):
    return {"game_id": n, "season": 2034, "week": n, "status": "COMPLETED",
            "game_type": "REGULAR_SEASON", "position_group": "RB", "rush_attempts": 12,
            "performance_signal": .8, **changes}


def assess(games, **changes):
    return build_evidence(games, season=2034, as_of_week=23, **changes)


def test_partial_season_review_is_not_an_award():
    result = assess([game(n) for n in range(1, 7)])
    assert result["eligible"] and result["trigger"] == "PARTIAL_SEASON"
    assert result["meaningful_appearances"] == 6
    assert result["performance_signal"] == pytest.approx(.8)
    assert not {"ratings", "band", "award"} & result.keys()


def test_mix_regular_and_playoff_games_and_trade_history():
    games = [game(n, team_id=1 if n < 4 else 2) for n in range(1, 7)]
    games[-1]["game_type"] = "PLAYOFFS"
    result = assess(games)
    assert result["eligible"] and result["playoff_appearances"] == 1


def test_closed_deep_playoff_run_after_bye_can_qualify():
    games = [game(n, week=week, game_type="PLAYOFFS") for n, week in enumerate((20, 21, 23), 1)]
    games.append(game(9, week=19, status="BYE"))
    result = assess(games, playoff_run_closed=True)
    assert result["trigger"] == "PLAYOFF_RUN" and result["eligible"]
    assert result["evidence_ids"] == [1, 2, 3]
    assert result["reliability"] < 1


def test_playoff_run_requires_closure_and_sustained_opportunities():
    games = [game(n, game_type="PLAYOFFS") for n in range(1, 4)]
    assert not assess(games)["eligible"]
    assert not assess(games[:1], playoff_run_closed=True)["eligible"]
    assert not assess([game(n, game_type="PLAYOFFS", rush_attempts=0) for n in range(1, 5)],
                      playoff_run_closed=True)["eligible"]


def test_future_uncompleted_and_preseason_games_cannot_help():
    games = [game(n) for n in range(1, 6)] + [game(6, status="SCHEDULED"), game(7, game_type="PRESEASON")]
    games.append(game(8, week=24))
    assert not assess(games)["eligible"]
    assert assess(games)["evidence_ids"] == [1, 2, 3, 4, 5]


def test_exact_duplicate_ingest_does_not_add_appearances():
    games = [game(n) for n in range(1, 7)]
    one, duplicate = assess(games), assess(games + [dict(games[0])])
    assert one == duplicate
    with pytest.raises(ValueError, match="conflicting duplicate"):
        assess(games + [game(1, rush_attempts=99)])


def test_missing_data_is_unknown_and_wholly_used_windows_cannot_repeat():
    games = [game(n, performance_signal=None) for n in range(1, 7)]
    result = assess(games)
    assert result["eligible"] and result["performance_signal"] is None
    assert not assess(games, accounted_evidence_ids=result["evidence_ids"])["eligible"]
    no_usage = assess([game(n, rush_attempts=None) for n in range(1, 7)])
    assert not no_usage["eligible"] and no_usage["notes"]


def test_baseline_normalization_is_explicit_and_invalid_signal_refused():
    games = [game(n, performance_signal=None, efficiency=6, baseline_efficiency=4, efficiency_scale=4)
             for n in range(1, 7)]
    assert assess(games)["performance_signal"] == .5
    with pytest.raises(ValueError, match="normalized"):
        assess([game(1, performance_signal=95)])


def test_reception_proxy_weakens_but_does_not_disqualify_playoff_review():
    games = [game(n, position_group="REC", receptions=5, game_type="PLAYOFFS") for n in range(1, 4)]
    result = assess(games, playoff_run_closed=True)
    assert result["eligible"] and result["reliability"] < .3


def test_checkpoints_use_completed_games_not_weeks_and_flush_once():
    games = [game(n, week=n if n < 3 else n + 1) for n in range(1, 6)]
    events = due_checkpoints(games, season=2034, as_of_week=6)
    assert [e["checkpoint"] for e in events] == ["MENTAL:4"]
    assert events[0]["evidence_ids"] == [1, 2, 3, 4]
    closed = due_checkpoints(games, season=2034, as_of_week=6,
                            accounted_game_ids={"MENTAL": [1, 2, 3, 4]}, season_closed=True)
    assert closed[0]["checkpoint"] == "MENTAL:5" and closed[0]["evidence_ids"] == [5]
    assert closed[1]["checkpoint"] == "TECHNICAL:5"
    assert due_checkpoints(games, season=2034, as_of_week=6,
                           accounted_game_ids=[1, 2, 3, 4, 5], season_closed=True) == []


def test_midpoint_and_postseason_residual_do_not_repeat_evidence():
    events = due_checkpoints([game(n) for n in range(1, 21)], season=2034, as_of_week=23, season_closed=True)
    for group in ("MENTAL", "TECHNICAL"):
        ids = [gid for event in events if event["group"] == group for gid in event["evidence_ids"]]
        assert sorted(ids) == list(range(1, 21))


def test_team_schedule_mismatch_and_same_week_cutoff():
    result = assess([game(n) for n in range(1, 7)], team_games=[game(1)])
    assert not result["eligible"]
    games = [game(1, week=4), game(2, week=4)]
    result = build_evidence(games, season=2034, as_of_week=4, as_of_game_id=1)
    assert result["evidence_ids"] == [1]


def test_invalid_policy_cannot_make_zero_usage_meaningful():
    with pytest.raises(ValueError, match="positive finite"):
        assess([game(1)], opportunity_thresholds={"RB": 0})


def test_overlapping_windows_need_new_sample_not_one_new_game():
    games = [game(n) for n in range(1, 8)]
    result = assess(games, accounted_evidence_ids=range(1, 7))
    assert not result["eligible"] and result["fresh_evidence_ids"] == [7]
    games = [game(n) for n in range(1, 13)]
    result = assess(games, accounted_evidence_ids=range(1, 7))
    assert result["eligible"] and result["fresh_evidence_ids"] == list(range(7, 13))


def test_closed_playoff_run_is_stable_and_cannot_reroll():
    games = [game(n, game_type="PLAYOFFS") for n in range(1, 4)]
    first = assess(games, playoff_run_closed=True)
    replay = assess(games, playoff_run_closed=True, accounted_evidence_ids=first["fresh_evidence_ids"])
    assert first["window_id"] == replay["window_id"]
    assert not replay["eligible"] and not replay["fresh_evidence_ids"]
