"""
Unit and integration tests for Player Progression Engine (engines/progression.py).
Tests cover terms, clamping, reproducible seeding, ratings adjustments, simulation stability, and backtest reporting.
"""

import math
import os
import psycopg
import pytest

from engines import ovr_weights as ow
from engines.progression import (
    seed_player_ceiling,
    compute_player_delta,
    apply_ratings_progression,
    run_backtest,
    run_simulation,
    run_progression,
    OVR_MAX,
    OVR_MIN,
    MAX_SEASON_CHANGE,
)
from engines.trade_value import AGE_BANDS

TEST_DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden_test user=postgres password=postgres")


@pytest.fixture(scope="module")
def weights_doc():
    return ow.load_weights()


@pytest.fixture(scope="module")
def mock_aging_curves():
    # Generic aging curves matching trade_value.py format
    return {
        "generic": [0.86, 0.17, -0.36, -1.53, -2.43, -2.68],
        "CB": [0.94, 0.20, -1.02, -2.16, -2.43, -2.68],
        "QB": [1.01, 0.37, -0.23, -1.78, -2.70, -2.68],
    }


def test_seed_player_ceiling_reproducible():
    """Ceiling seeding is deterministic and respects prospect true_value when provided."""
    c1, src1 = seed_player_ceiling(player_id=123, ovr=80, age=22, dev_trait="Star")
    c2, src2 = seed_player_ceiling(player_id=123, ovr=80, age=22, dev_trait="Star")
    assert c1 == c2
    assert src1 == "SEEDED"
    assert c1 >= 80

    # Draftee with prospect true value
    c_draft, src_draft = seed_player_ceiling(player_id=456, ovr=75, age=21, dev_trait="Normal", prospect_true_value=84.5)
    assert c_draft == 84.5
    assert src_draft == "SCOUT_PROSPECT"


def test_progression_terms(mock_aging_curves):
    """Verifies that individual causal terms behave as specified in §5.2."""
    # 1. Age term: young player positive, vet negative
    _, _, b_young, _ = compute_player_delta(
        player_id=1, position="QB", age=22, ovr=75, dev_trait="Normal", ceiling=85.0,
        games_played=16, downs_played=800, is_starter=True, is_ir=False, injury_weeks=0,
        season=2036, aging_curves=mock_aging_curves
    )
    _, _, b_vet, _ = compute_player_delta(
        player_id=1, position="QB", age=32, ovr=75, dev_trait="Normal", ceiling=75.0,
        games_played=16, downs_played=800, is_starter=True, is_ir=False, injury_weeks=0,
        season=2036, aging_curves=mock_aging_curves
    )
    assert b_young["age"] > 0
    assert b_vet["age"] < 0

    # 2. Dev term: young player strictly orders Normal < Star < Superstar < X-Factor
    dev_terms = []
    for dt in ("Normal", "Star", "Superstar", "Superstar X-Factor"):
        _, _, b, _ = compute_player_delta(
            player_id=1, position="QB", age=23, ovr=75, dev_trait=dt, ceiling=85.0,
            games_played=16, downs_played=800, is_starter=True, is_ir=False, injury_weeks=0,
            season=2036, aging_curves=mock_aging_curves
        )
        dev_terms.append(b["dev"])
    assert dev_terms[0] < dev_terms[1] < dev_terms[2] < dev_terms[3]

    # 3. Ceiling term: room to grow yields positive pull; zero room yields 0.0
    _, _, b_room, _ = compute_player_delta(
        player_id=1, position="QB", age=23, ovr=75, dev_trait="Normal", ceiling=90.0,
        games_played=16, downs_played=800, is_starter=True, is_ir=False, injury_weeks=0,
        season=2036, aging_curves=mock_aging_curves
    )
    _, _, b_capped, _ = compute_player_delta(
        player_id=1, position="QB", age=23, ovr=85, dev_trait="Normal", ceiling=80.0,
        games_played=16, downs_played=800, is_starter=True, is_ir=False, injury_weeks=0,
        season=2036, aging_curves=mock_aging_curves
    )
    assert b_room["ceiling"] > 0
    assert b_capped["ceiling"] == 0.0

    # 4. Play term: full-time starter vs benched
    _, _, b_start, _ = compute_player_delta(
        player_id=1, position="QB", age=25, ovr=80, dev_trait="Normal", ceiling=85.0,
        games_played=16, downs_played=900, is_starter=True, is_ir=False, injury_weeks=0,
        season=2036, aging_curves=mock_aging_curves
    )
    _, _, b_bench, _ = compute_player_delta(
        player_id=1, position="QB", age=25, ovr=80, dev_trait="Normal", ceiling=85.0,
        games_played=2, downs_played=20, is_starter=False, is_ir=False, injury_weeks=0,
        season=2036, aging_curves=mock_aging_curves
    )
    assert b_start["play"] > 0
    assert b_bench["play"] < 0

    # 5. Injury term: IR causes negative penalty
    _, _, b_ir, _ = compute_player_delta(
        player_id=1, position="QB", age=25, ovr=80, dev_trait="Normal", ceiling=85.0,
        games_played=6, downs_played=300, is_starter=False, is_ir=True, injury_weeks=10,
        season=2036, aging_curves=mock_aging_curves
    )
    assert b_ir["injury"] <= -0.75


def test_clamping(mock_aging_curves):
    """Delta OVR is clamped to age and OVR band limits, and 99 cannot grow."""
    # 99 OVR player cannot increase
    d_99, new_99, _, _ = compute_player_delta(
        player_id=99, position="QB", age=24, ovr=99, dev_trait="Superstar X-Factor", ceiling=99.0,
        games_played=17, downs_played=1000, is_starter=True, is_ir=False, injury_weeks=0,
        season=2036, aging_curves=mock_aging_curves
    )
    assert d_99 <= 0
    assert new_99 <= 99

    # Max delta clamped to MAX_SEASON_CHANGE (8)
    d_boost, _, _, _ = compute_player_delta(
        player_id=77, position="QB", age=21, ovr=60, dev_trait="Superstar X-Factor", ceiling=95.0,
        games_played=17, downs_played=1000, is_starter=True, is_ir=False, injury_weeks=0,
        season=2036, aging_curves=mock_aging_curves
    )
    assert d_boost <= MAX_SEASON_CHANGE

    # Age 32+ cannot gain more than 1
    d_old, _, _, _ = compute_player_delta(
        player_id=88, position="QB", age=33, ovr=85, dev_trait="Superstar", ceiling=95.0,
        games_played=17, downs_played=1000, is_starter=True, is_ir=False, injury_weeks=0,
        season=2036, aging_curves=mock_aging_curves
    )
    assert d_old <= 1


def test_reproducible_seeding(mock_aging_curves):
    """Progression noise and deltas are strictly reproducible for same (season, player_id)."""
    d1, o1, b1, _ = compute_player_delta(
        player_id=777, position="WR", age=24, ovr=80, dev_trait="Star", ceiling=88.0,
        games_played=16, downs_played=800, is_starter=True, is_ir=False, injury_weeks=0,
        season=2036, aging_curves=mock_aging_curves
    )
    d2, o2, b2, _ = compute_player_delta(
        player_id=777, position="WR", age=24, ovr=80, dev_trait="Star", ceiling=88.0,
        games_played=16, downs_played=800, is_starter=True, is_ir=False, injury_weeks=0,
        season=2036, aging_curves=mock_aging_curves
    )
    assert d1 == d2
    assert o1 == o2
    assert b1["noise"] == b2["noise"]

    # Different player_id yields different noise
    _, _, b3, _ = compute_player_delta(
        player_id=778, position="WR", age=24, ovr=80, dev_trait="Star", ceiling=88.0,
        games_played=16, downs_played=800, is_starter=True, is_ir=False, injury_weeks=0,
        season=2036, aging_curves=mock_aging_curves
    )
    assert b1["noise"] != b3["noise"]


def test_ratings_adjustment(weights_doc):
    """Ratings layer moves archetype skills to reach target OVR and verifies archetype remains on top."""
    ratings = {
        "Speed": 88, "Acceleration": 90, "Agility": 85, "Awareness": 80,
        "Tackle": 82, "Pursuit": 84, "PlayRecognition": 80, "BlockShedding": 78,
        "HitPower": 75, "ZoneCoverage": 70, "ManCoverage": 65
    }
    pos = "MIKE"
    arch = "MLB_FieldGeneral"
    cur_ovr, _ = ow.predict_ovr(weights_doc, "MLB", ratings, current=arch)
    target_ovr = cur_ovr - 2  # Regressed veteran

    new_ratings, diff, top_arch, settled = apply_ratings_progression(
        doc=weights_doc,
        position=pos,
        archetype=arch,
        ratings=ratings,
        age=30,  # Veteran: triggers physical decline in Speed/Agility
        target_ovr=target_ovr,
        injury_severity=0
    )

    # Physical rating decreased due to age 30
    assert new_ratings["Speed"] < ratings["Speed"]
    # Final OVR predicted matches target OVR
    top_ovr, predicted_arch = ow.predict_ovr(weights_doc, "MLB", new_ratings, current=top_arch)
    assert top_ovr == target_ovr
    assert settled is True
    assert len(diff) > 0


@pytest.mark.integration
def test_simulation_stability():
    """Runs a 5-season simulation and asserts league shape stays within stability limits."""
    conn = psycopg.connect(TEST_DSN)
    with conn:
        sim_res = run_simulation(conn, start_season=2036, num_seasons=5, log=lambda *args: None)
        assert sim_res["is_stable"] is True
        assert len(sim_res["history"]) == 5

        final = sim_res["history"][-1]
        assert 45 <= final["count_90_plus"] <= 180
        assert 80.5 <= final["avg_starter_ovr"] <= 88.0
        assert 74.0 <= final["avg_ovr"] <= 80.5
        assert 25.5 <= final["avg_age"] <= 28.5


@pytest.mark.integration
def test_backtest_reporting():
    """Runs backtest on 2035->2036 and verifies mean predicted ΔOVR aligns with Madden."""
    conn = psycopg.connect(TEST_DSN)
    with conn:
        bt = run_backtest(conn, season=2036, log=lambda *args: None)
        assert bt["sample_size"] > 1000
        # League mean within 0.15 of Madden's actual (-0.147)
        assert abs(bt["mean_predicted"] - bt["mean_actual"]) <= 0.15
        assert bt["within_1_pct"] >= 0.65
        assert len(bt["age_bands"]) > 0
        assert len(bt["ovr_bands"]) > 0
        assert len(bt["position_groups"]) > 0


@pytest.mark.integration
def test_progression_db_execution():
    """Tests progression run with --commit and verifies persistence in player_progression_history."""
    conn = psycopg.connect(TEST_DSN)
    with conn:
        res = run_progression(conn, season=2036, commit=True, dry_run=False, log=lambda *args: None)
        assert res["players_count"] > 1000

        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM player_progression_history WHERE season = 2036")
            count = cur.fetchone()[0]
            assert count == res["players_count"]

            cur.execute("SELECT count(*) FROM player_ceilings")
            c_count = cur.fetchone()[0]
            assert c_count >= count
