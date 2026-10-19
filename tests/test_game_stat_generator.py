import pandas as pd
import pytest
from engines.game_stat_generator import _distribute_metric, generate_defense_and_special_teams_stats


def test_distribute_metric_exact_conservation():
    """Verify that integer metric distribution exactly conserves the target sum across games."""
    target = 17
    weights = [1.0, 2.5, 0.5, 4.0, 2.0]
    allocs = _distribute_metric(target, weights)
    assert len(allocs) == len(weights)
    assert sum(allocs) == target
    assert all(isinstance(v, int) for v in allocs)
    assert all(v >= 0 for v in allocs)


def test_distribute_metric_single_game():
    """Verify that when only 1 game is present (standard weekly cadence), 100% of the target is allocated."""
    target = 5
    weights = [2.0]
    allocs = _distribute_metric(target, weights)
    assert allocs == [5]


def test_distribute_metric_half_steps():
    """Verify that defensive sacks distribute in 0.5 increments while conserving total sacks."""
    target = 3.5
    weights = [1.0, 3.0, 1.0]
    allocs = _distribute_metric(target, weights, is_half_step=True)
    assert len(allocs) == len(weights)
    assert sum(allocs) == pytest.approx(target)
    assert all(v >= 0.0 for v in allocs)
    # Check that each allocation is a multiple of 0.5
    for v in allocs:
        assert (v * 2).is_integer()


def test_distribute_metric_zero_target():
    """Verify that 0 target yields zeros."""
    allocs = _distribute_metric(0, [1.0, 2.0])
    assert allocs == [0, 0]


def test_generate_defense_and_special_teams_stats_empty_games():
    """Verify that empty games dataframe is handled gracefully without errors."""
    empty_df = pd.DataFrame()
    # Should not raise any exceptions
    generate_defense_and_special_teams_stats(2036, empty_df)
