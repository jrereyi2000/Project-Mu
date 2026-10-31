"""Input provenance/cutoff, trade calendars and conservative postseason closure."""
from unittest.mock import MagicMock
import copy

from engines import progression_inputs as pi
from engines.progression_evidence import build_evidence

SEASON = 2037


def roster(pid=1, team=10, position="QB", **extra):
    return dict(player_id=pid, pgid=1000 + pid, team_id=team, position=position, age=25,
                released_week=None, **extra)


def game(gid, week, home=10, away=20, kind="REGULAR_SEASON", status="COMPLETED", hs=20, aws=10):
    return dict(game_id=gid, season=SEASON, week=week, home_team_id=home, away_team_id=away,
                game_type=kind, status=status, home_score=hs, away_score=aws)


def qb_line(pid, gid, week, team=10, yards=200, **extra):
    return dict(player_id=pid, game_id=gid, season=SEASON, week=week, team_id=team,
                position="QB", downs_played=50, pass_attempts=25, pass_yards=yards,
                pass_tds=1, interceptions=0, **extra)


def test_cutoff_preseason_scheduled_and_future_stats_excluded():
    games = [game(1, 1), game(2, 2, status="SCHEDULED"), game(3, 3), game(4, 0, kind="PRESEASON")]
    lines = [qb_line(1, n, wk, yards=900) for n, wk in ((1, 1), (2, 2), (3, 3), (4, 0))]
    out = pi.build_inputs([roster()], games, lines, season=SEASON, week=2)[1]
    assert [row["game_id"] for row in out["player_games"]] == [1]
    assert [row["game_id"] for row in out["team_games"]] == [1]
    assert out["season_closed"] is False


def test_trade_keeps_old_game_ids_and_never_counts_both_teams_same_week():
    games = [game(wk, wk, home=10) for wk in range(1, 7)]
    games += [game(100 + wk, wk, home=30, away=40) for wk in range(1, 7)]
    lines = [qb_line(1, wk, wk, team=10) for wk in (1, 2, 3, 5)]
    lines += [qb_line(1, 106, 6, team=30)]
    out = pi.build_inputs([roster(team=30)], games, lines, season=SEASON, week=6)[1]
    assert [g["game_id"] for g in out["team_games"]] == [1, 2, 3, 5, 106]
    assert len({g["week"] for g in out["team_games"]}) == len(out["team_games"])
    assert any("trade dates are unknown" in n for n in out["notes"])


def test_bye_has_no_calendar_appearance_and_missing_playoff_schedule_is_not_closure():
    games = [game(1, 1), game(2, 2, kind="BYE", status="BYE"), game(3, 18)]
    out = pi.build_inputs([roster()], games, [qb_line(1, 1, 1)], season=SEASON, week=20)[1]
    assert len(out["team_games"]) == 2
    assert out["season_closed"] is False
    assert out["playoff_run_closed"] is False


def test_actual_postseason_loss_closes_team_run():
    games = [game(19, 19, kind="WILD_CARD"), game(20, 20, kind="DIVISIONAL"),
             game(21, 21, kind="CONFERENCE", hs=10, aws=20)]
    lines = [qb_line(1, wk, wk) for wk in (19, 20, 21)]
    out = pi.build_inputs([roster()], games, lines, season=SEASON, week=21)[1]
    assert out["season_closed"] and out["playoff_run_closed"]
    assert len(out["player_games"]) == 3


def test_league_superbowl_closes_regular_only_team_but_future_result_cannot():
    games = [game(1, 18), game(23, 23, home=30, away=40, kind="SUPER_BOWL")]
    early = pi.build_inputs([roster()], games, [], season=SEASON, week=22)[1]
    late = pi.build_inputs([roster()], games, [], season=SEASON, week=23)[1]
    assert early["season_closed"] is False
    assert late["season_closed"] is True and late["playoff_run_closed"] is False


def test_released_player_does_not_collect_current_team_schedule_after_release():
    row = roster()
    row["released_week"] = 3
    out = pi.build_inputs([row], [game(wk, wk) for wk in range(1, 6)],
                          [qb_line(1, 1, 1)], season=SEASON, week=5)[1]
    assert [g["game_id"] for g in out["team_games"]] == [1]


def cohort():
    rosters = [roster(pid=pid, team=pid) for pid in range(1, 10)]
    games, lines = [], []
    for pid in range(1, 10):
        for wk in range(1, 5):
            gid = pid * 100 + wk
            games.append(game(gid, wk, home=pid, away=pid + 100))
            lines.append(qb_line(pid, gid, wk, team=pid, yards=100 + pid * 30))
    return rosters, games, lines


def test_cohort_signal_is_cutoff_bounded_normalized_and_leave_player_out():
    rosters, games, lines = cohort()
    out = pi.build_inputs(rosters, games, lines, season=SEASON, week=4)
    high, low = out[9]["player_games"][0], out[1]["player_games"][0]
    assert 0 < high["performance_signal"] <= 1
    assert -1 <= low["performance_signal"] < 0
    assert high["baseline_efficiency"] < high["efficiency"]
    assert high["source"] == "game_stat_lines"
    original = copy.deepcopy(out)
    games.append(game(9999, 9, home=9, away=109))
    lines.append(qb_line(9, 9999, 9, team=9, yards=9000))
    assert pi.build_inputs(rosters, games, lines, season=SEASON, week=4) == original


def test_insufficient_cohort_is_unknown_performance_but_reps_can_support_mental_learning():
    out = pi.build_inputs([roster()], [game(1, 1)], [qb_line(1, 1, 1)], season=SEASON, week=1)[1]
    row = out["player_games"][0]
    assert row["performance_signal"] is None and row["signal_reliability"] > 0
    ev = build_evidence(out["player_games"], season=SEASON, as_of_week=1)
    assert ev["meaningful_appearances"] == 1 and ev["reliability"] > 0
    assert ev["performance_signal"] is None


def test_actual_game_position_overrides_current_position_and_no_route_metric_invented():
    line = dict(player_id=1, game_id=1, team_id=10, position="WR", receptions=4, rec_yards=100)
    out = pi.build_inputs([roster(position="QB")], [game(1, 1)], [line], season=SEASON, week=1)[1]
    row = out["player_games"][0]
    assert row["position_group"] == "REC"
    assert row["efficiency_metric"] == "receiving_production_per_reception_proxy"
    assert out["supported_skills"] == []
    assert not any(k in row for k in ("routes_run", "targets", "epa", "separation"))


def test_only_direct_broad_skill_mappings_are_exposed():
    row = dict(player_id=1, game_id=1, position="K", team_id=10, kick_fgm=3, kick_fga=3)
    result = pi.build_inputs([roster(position="K")], [game(1, 1)], [row], season=SEASON, week=1)[1]
    assert result["supported_skills"] == ["KickAccuracy"]


def test_fetch_inputs_uses_explicit_cutoff_and_no_authoritative_rating_reads():
    cur = MagicMock()
    batches = [(["player_id", "pgid", "team_id", "position", "age", "released_week"],
                [(1, 1001, 10, "QB", 25, None)]),
               (["game_id", "season", "week", "home_team_id", "away_team_id", "home_score", "away_score", "status", "game_type"],
                [(1, SEASON, 1, 10, 20, 20, 10, "COMPLETED", "REGULAR_SEASON")]),
               (["player_id", "game_id", "team_id", "position", "pass_attempts", "pass_yards"],
                [(1, 1, 10, "QB", 25, 200)])]
    def execute(sql, params):
        cols, rows = batches.pop(0)
        cur.description = [(name,) for name in cols]
        cur.fetchall.return_value = rows
    cur.execute.side_effect = execute
    result = pi.fetch_inputs(cur, SEASON, 4)
    assert result[1]["pgid"] == 1001
    assert [call.args[1] for call in cur.execute.call_args_list] == [(SEASON,), (SEASON, 4), (SEASON, 4)]
    for call in cur.execute.call_args_list:
        assert "player_attributes" not in call.args[0]
        assert "player_ceilings" not in call.args[0]


def test_later_peer_outcomes_cannot_rewrite_earlier_game_comparisons():
    rosters, games, lines = cohort()
    earlier = pi.build_inputs(rosters, games, lines, season=SEASON, week=4)[1]["player_games"][0]
    for pid in range(2, 10):
        gid = pid * 1000 + 8
        games.append(game(gid, 8, home=pid, away=pid + 100))
        lines.append(qb_line(pid, gid, 8, team=pid, yards=9000 * pid))
    later = pi.build_inputs(rosters, games, lines, season=SEASON, week=8)[1]["player_games"][0]
    for field in ("performance_signal", "baseline_efficiency", "efficiency_scale", "signal_reliability"):
        assert earlier[field] == later[field]
    assert earlier["game_id"] == later["game_id"] == 101


def test_later_cohort_peers_do_not_fill_earlier_missing_comparison():
    rosters = [roster(pid=pid, team=pid) for pid in range(1, 6)]
    games = [game(1, 1, home=1, away=100)]
    lines = [qb_line(1, 1, 1, team=1)]
    for pid in range(2, 6):
        games.append(game(pid, 2, home=pid, away=100 + pid))
        lines.append(qb_line(pid, pid, 2, team=pid, yards=pid * 50))
    result = pi.build_inputs(rosters, games, lines, season=SEASON, week=2)[1]["player_games"][0]
    assert result["performance_signal"] is None
    assert "insufficient position cohort" in result["signal_note"]
