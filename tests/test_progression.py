"""
Unit and integration tests for Player Progression Engine (engines/progression.py).
Tests cover terms, clamping, reproducible seeding, ratings adjustments, simulation stability, and backtest reporting.
"""

import math
import os
import random

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
def test_simulation_holds_the_league_shape():
    """With the league terms (JR, 2026-10-28) the league keeps its 2036 shape every season (§5.5, R6);
    following Madden's own turnover it shrinks."""
    conn = psycopg.connect(TEST_DSN)
    with conn:
        held = run_simulation(conn, start_season=2036, num_seasons=3, hold_shape=True, log=lambda *args: None)
        madden = run_simulation(conn, start_season=2036, num_seasons=3, hold_shape=False, log=lambda *args: None)
    base = held["baseline"]
    assert held["is_stable"] is True
    for s in held["history"]:
        assert s["players_count"] == base["players"]
        assert abs(s["count_90_plus"] - base["count_90_plus"]) <= max(10, base["count_90_plus"] * 0.10)
        assert abs(s["avg_ovr"] - base["avg_ovr"]) <= 0.5
    assert all(s["league_term"] == 0 and s["star_term"] == 0 for s in madden["history"])
    assert madden["history"][-1]["avg_ovr"] < held["history"][-1]["avg_ovr"]


def test_calibrate_league_term_hits_both_targets():
    from engines.progression import calibrate_league_term
    players = [{"ovr": o} for o in [70] * 80 + [88] * 20]
    # dev moves the 70s, star moves the 88s; 90+ needs star >= 2 for the 88s
    progress = lambda p, dev, star: p["ovr"] + (int(dev) if p["ovr"] < 85 else int(star))
    (dev, star), shape = calibrate_league_term(players, [], {"count_90_plus": 20, "avg_ovr": 76.0}, progress)
    assert star == 2.0 and shape["count_90_plus"] == 20
    assert abs(shape["avg_ovr"] - 76.0) <= 0.5


def test_rookie_class_follows_the_real_class():
    from engines.progression import rookie_class
    real = [{"ovr": o, "position": "WR", "age": 22} for o in (83, 76, 70, 67, 64, 62)]
    turnover = {"rookies": real, "devs": {"Normal": 9, "Star": 1}}
    out = rookie_class(turnover, 12, random.Random(1))
    assert len(out) == 12
    assert max(r["ovr"] for r in out) <= 84 and min(r["ovr"] for r in out) >= 61
    assert out[0]["ovr"] >= out[-1]["ovr"]


def test_gone_rate_borrows_the_age_band_for_small_groups():
    from engines.progression import gone_rate
    t = {"gone": {("30-32", False): (60, 200), ("30-32", True): (1, 5), ("36+", False): (2, 2)}}
    assert gone_rate(t, 31, 70) == 0.3
    assert gone_rate(t, 31, 90) == 61 / 205        # 5 players at 85+: too few, whole band used
    assert gone_rate(t, 37, 70) == 0.9             # 36+ too small: assume most leave


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


def test_production_ranks_within_the_position_group():
    from engines.progression import production
    good = {"games_played": 16, "pass_yards": 4800, "pass_tds": 40, "interceptions": 8}
    bad = {"games_played": 16, "pass_yards": 2900, "pass_tds": 12, "interceptions": 18}
    assert production("QB", good) > production("QB", bad)
    assert production("QB", {"games_played": 3, "pass_yards": 900}) is None          # too few games
    assert production("K", {"kick_fga": 5, "kick_fg_pct": 100}) is None              # too few attempts
    assert production("OL", {"games_played": 17, "blk_sacks_allowed": 1}) > production(
        "OL", {"games_played": 17, "blk_sacks_allowed": 9})


def test_performance_term_is_rank_plus_awards_clamped():
    from engines.progression import performance_term
    assert performance_term(None) == 0.0
    assert performance_term({"score": 1.0, "awards": 0.0}) == 1.0
    assert performance_term({"score": -1.0, "awards": 0.0}) == -1.0
    assert performance_term({"score": 1.0, "awards": 1.5}) == 2.0                  # clamped
    assert performance_term({"score": -1.0, "awards": 0.5}) == -0.5


def test_performance_moves_the_delta(mock_aging_curves):
    from engines.progression import compute_player_delta
    kw = dict(player_id=7, position="WR", age=25, ovr=80, dev_trait="Normal", ceiling=88.0, games_played=16,
              downs_played=None, is_starter=True, is_ir=False, injury_weeks=0, season=2037,
              aging_curves=mock_aging_curves)
    up = compute_player_delta(**kw, performance=2.0)
    down = compute_player_delta(**kw, performance=-1.0)
    assert up[1] - down[1] == 3
    assert up[2]["performance"] == 2.0 and up[3] == "STANDOUT_SEASON"


def test_rating_groups():
    from engines.progression import rating_group
    assert rating_group("Speed") == rating_group("ThrowPower") == "physical"
    assert rating_group("Awareness") == rating_group("PlayRecognition") == "mental"
    assert rating_group("ManCoverage") == "technical"
    assert rating_group("Injury") == "fixed"


def test_mental_decline_follows_the_peak():
    """D10: nothing before 30; then a share of the draft-day-to-peak gain, never below draft day."""
    from engines.progression import mental_decline
    cur, peak, entry = {"Awareness": 90}, {"Awareness": 95}, {"Awareness": 55}
    assert mental_decline(cur, peak, entry, 28) == {"Awareness": 90}
    a31 = mental_decline(cur, peak, entry, 31)["Awareness"]
    a34 = mental_decline(cur, peak, entry, 34)["Awareness"]
    assert a34 < a31 < 90
    assert a31 == 90 - round(0.04 * 40)
    # a smaller peak gain loses less
    assert mental_decline(cur, {"Awareness": 90}, {"Awareness": 80}, 34)["Awareness"] == 89
    assert mental_decline({"Awareness": 56}, peak, entry, 34)["Awareness"] == 55       # floor: draft day


def test_estimate_entry_mental_uses_years_pro():
    from engines.progression import estimate_entry_mental
    r = {"Awareness": 80, "PlayRecognition": 30}
    assert estimate_entry_mental(r, 0) == {"Awareness": 80, "PlayRecognition": 30}
    assert estimate_entry_mental(r, 5) == {"Awareness": 47, "PlayRecognition": 20}    # floor 20


def test_ratings_layer_keeps_groups_apart(weights_doc):
    """Growth goes to technical and mental skills (mental first), never physical outside the strength program;
    a young player's decline leaves mental ratings alone; delta_ovr aims at the ratings' own grade."""
    ratings = {
        "Speed": 88, "Acceleration": 90, "Agility": 85, "Awareness": 70, "Strength": 70, "Stamina": 80,
        "Tackle": 78, "Pursuit": 80, "PlayRecognition": 70, "BlockShedding": 74,
        "HitPower": 75, "ZoneCoverage": 70, "ManCoverage": 65
    }
    arch = "MLB_FieldGeneral"
    grade = ow.predict_grade(weights_doc, arch, ratings)
    grown, diff, _, _ = apply_ratings_progression(weights_doc, "MIKE", arch, ratings, age=24, target_ovr=0,
                                                  delta_ovr=3, years_pro=3)
    assert ow.predict_grade(weights_doc, arch, grown) == grade + 3
    assert all(grown[k] <= ratings[k] for k in ("Speed", "Acceleration", "Agility", "Strength", "Stamina"))
    assert grown["Awareness"] + grown["PlayRecognition"] > ratings["Awareness"] + ratings["PlayRecognition"]

    rookie, _, _, _ = apply_ratings_progression(weights_doc, "MIKE", arch, ratings, age=22, target_ovr=0,
                                                delta_ovr=0, years_pro=0)
    assert rookie["Strength"] == 71 and rookie["Stamina"] == 81                      # strength program

    slump, _, _, _ = apply_ratings_progression(weights_doc, "MIKE", arch, ratings, age=25, target_ovr=0,
                                               delta_ovr=-2, years_pro=3)
    assert slump["Awareness"] == ratings["Awareness"] and slump["PlayRecognition"] == ratings["PlayRecognition"]
    assert ow.predict_grade(weights_doc, arch, slump) == grade - 2


def test_db_tackles_do_not_count_and_downs_set_the_rate():
    from engines.progression import production
    quiet = {"games_played": 16, "def_tackles": 30, "def_ints": 4, "def_deflections": 12}
    busy = {"games_played": 16, "def_tackles": 110, "def_ints": 1, "def_deflections": 6}
    assert production("DB", quiet) > production("DB", busy)
    # per down, scaled to the group's downs per game: the same counts over fewer downs rank higher
    a = {"games_played": 16, "downs_played": 600, "def_ints": 4}
    b = {"games_played": 16, "downs_played": 1100, "def_ints": 4}
    assert production("DB", a, 60.0) > production("DB", b, 60.0)
    assert production("DB", {"games_played": 16, "def_ints": 4}, 60.0) == 32 / 16     # no downs: per game


@pytest.mark.integration
def test_performance_reliability_weights():
    """Each group's weight is its year-to-year correlation scaled so the most repeatable group is 1 (§5.15)."""
    from engines.progression import performance_reliability, performance_scores
    conn = psycopg.connect(TEST_DSN)
    with conn, conn.cursor() as cur:
        rel = performance_reliability(cur, 2036)
        perf = performance_scores(cur, 2036)
    assert max(rel["weights"].values()) == 1.0
    assert all(0.0 <= w <= 1.0 for w in rel["weights"].values())
    assert all(abs(v["score"]) <= rel["weights"].get(v["group"], 1.0) + 1e-9 for v in perf.values() if v["group"])


def test_simulated_performance_scales_by_reliability():
    from engines.progression import simulated_performance
    roster = [{"player_id": i, "position": "WR" if i % 2 else "CB", "ovr": 60 + i % 40} for i in range(400)]
    perf = simulated_performance(roster, 2037, {"DB": 0.5, "REC": 1.0})
    assert max(abs(perf[i]["score"]) for i in range(0, 400, 2)) <= 0.5
    assert max(abs(perf[i]["score"]) for i in range(1, 400, 2)) == 1.0


def test_simulated_performance_gives_scores_and_all_pros():
    from engines.progression import simulated_performance
    roster = [{"player_id": i, "position": "WR" if i % 2 else "CB", "ovr": 60 + i % 40} for i in range(400)]
    perf = simulated_performance(roster, 2037)
    assert len(perf) == 400
    assert -1.0 <= min(v["score"] for v in perf.values()) and max(v["score"] for v in perf.values()) <= 1.0
    assert sum(1 for v in perf.values() if v["awards"]) == 62


# ------------------------------------------------------------ phase C2 (§5.8-§5.13) ----
from engines import progression as pg


def test_durability_seeding_is_stable_and_follows_ratings():
    a = pg.seed_durability(501, 90, 88)
    assert a == pg.seed_durability(501, 90, 88)
    assert a[1] == "RATINGS" and 0.0 <= a[0] <= 1.0
    assert pg.seed_durability(501)[1] == "DEFAULT"
    # same seeded draw, better InjuryRating / ToughnessRating -> more durable
    assert pg.seed_durability(501, 95, 95)[0] > pg.seed_durability(501, 65, 65)[0]
    # the draw differs by player and stays within ±DURABILITY_SPREAD / 2 of the start
    ds = [pg.seed_durability(pid, 80, 80)[0] for pid in range(200)]
    assert len(set(ds)) > 50
    assert all(abs(d - 0.5) <= pg.DURABILITY_SPREAD / 2 + 1e-9 for d in ds)


def test_season_mileage_weights_by_position():
    rb = pg.season_mileage("RB", {"games_played": 17, "downs_played": 600, "rush_attempts": 300, "receptions": 50})
    assert rb["touches"] == 350 and rb["hits"] == 350 and rb["snaps"] == 600
    backup = pg.season_mileage("RB", {"games_played": 17, "downs_played": 200, "rush_attempts": 60, "receptions": 10})
    assert rb["wear"] > 2 * backup["wear"]
    ol = pg.season_mileage("OL", {"games_played": 17, "downs_played": 1050})
    assert ol["touches"] == 0 and 8 <= ol["wear"] <= 12
    # no downs recorded: games x the group's snaps per game
    assert pg.season_mileage("OL", {"games_played": 10})["snaps"] == 10 * pg.MILEAGE_SNAPS_PER_GAME["OL"]
    assert pg.mileage_excess(20.0, 2, 10.0) == 0.0
    assert pg.mileage_excess(40.0, 2, 10.0) == 1.0
    assert pg.mileage_excess(5.0, 0, 10.0) == 0.0


def test_wear_grows_with_mileage_and_shrinks_with_durability():
    heavy = pg.wear_term(29, "HB", 1.0, 0.3)
    medium = pg.wear_term(29, "HB", 0.4, 0.3)
    assert heavy < medium < 0
    assert abs(pg.wear_term(29, "HB", 1.0, 0.9)) < abs(heavy)          # durable body wears slower
    assert pg.wear_term(29, "HB", -0.5, 0.5) > 0                        # light mileage slows the decline a little
    assert pg.wear_term(23, "HB", 1.5, 0.0) == 0.0                      # before the physical decline
    assert pg.wear_term(26, "HB", 1.0, 0.3) < pg.wear_term(26, "QB", 1.0, 0.3) == 0.0   # RB declines a year earlier
    lo, hi = pg.WEAR_TERM_RANGE
    for e in (-5, -0.5, 0, 1, 10):
        for d in (0.0, 0.5, 1.0):
            assert lo <= pg.wear_term(34, "WR", e, d) <= hi


def test_lasting_injury_damage_rolls_for_major_injuries_only():
    assert pg.lasting_injury_damage(1, 2037, ["HIGH ANKLE SPRAIN"], 0.2) == {}
    assert pg.lasting_injury_damage(1, 2037, ["PARTIAL ACL TEAR"], 0.2) == {}
    assert pg.is_major_structural("ACL Tear") and pg.is_major_structural("TORN ACHILLES")
    rolls = [pg.lasting_injury_damage(pid, 2037, ["ACL TEAR"], 0.2) for pid in range(300)]
    hits = [r for r in rolls if r]
    assert 0 < len(hits) < 300
    for r in hits:
        (k, v), = r.items()
        assert k in pg.LASTING_DAMAGE_RATINGS and -3 <= v <= -1
    assert rolls[7] == pg.lasting_injury_damage(7, 2037, ["ACL TEAR"], 0.2)      # seeded
    durable = sum(1 for pid in range(300) if pg.lasting_injury_damage(pid, 2037, ["ACL TEAR"], 0.95))
    assert durable < len(hits)


def test_lasting_damage_lowers_physical_ratings_and_ovr(weights_doc, mock_aging_curves):
    kw = dict(player_id=3, position="HB", age=27, ovr=82, dev_trait="Normal", ceiling=84.0, games_played=8,
              downs_played=None, is_starter=True, is_ir=False, injury_weeks=0, season=2037,
              aging_curves=mock_aging_curves)
    assert pg.compute_player_delta(**kw, lasting_damage=2)[2]["injury"] == round(-2 * pg.LASTING_DAMAGE_OVR, 2)
    ratings = {"Speed": 90, "Acceleration": 90, "Agility": 88, "Awareness": 75, "Carrying": 85, "BCVision": 80,
               "BreakTackle": 80, "Trucking": 70, "JukeMove": 85, "Strength": 70, "Catching": 65}
    new, _, _, _ = apply_ratings_progression(weights_doc, "HB", None, ratings, age=27, target_ovr=0, delta_ovr=-1,
                                             years_pro=5, lasting_damage={"Speed": -3}, wear=-1.0)
    assert new["Speed"] <= ratings["Speed"] - 4                          # 3 lasting + 1 from wear


def test_ceiling_band_growth_replaces_the_dev_table(mock_aging_curves):
    band = pg.seed_ceiling_band(42, 78, "Star", 88.0, years_pro=1)
    assert band == pg.seed_ceiling_band(42, 78, "Star", 88.0, years_pro=1)
    gc = band["group_ceilings"]
    assert gc["technical"] == 88.0 and gc["mental"] == 89.0 and gc["physical"] == 79.0
    assert band["spread"] == 1.0 and band["floor"] < 78
    # no room, no growth (28 and under); no spread, no growth; more spread grows faster
    assert pg.band_growth(1, 2037, 23, 80, 2.0, 80.0) == 0.0
    assert pg.band_growth(1, 2037, 23, 75, 0.0, 85.0) == 0.0
    assert pg.band_growth(1, 2037, 23, 75, 1.0, 85.0) < pg.band_growth(1, 2037, 23, 75, 2.0, 85.0)
    assert pg.band_growth(1, 2037, 23, 75, 2.0, 76.0) < pg.band_growth(1, 2037, 23, 75, 2.0, 85.0)
    # with room, the band reproduces the old dev table (Normal 0, Star +0.5 ... X-Factor +1.5 at 24 and under)
    old = {24: (0.0, 0.5, 1.0, 1.5), 26: (0.0, 0.3, 0.65, 1.0), 28: (0.0, 0.15, 0.35, 0.6), 33: (0.0, 0.2, 0.4, 0.7)}
    for age, vals in old.items():
        for dr, v in enumerate(vals):
            g = pg.band_growth(9, 2037, age, 70, pg.BAND_SPREAD_BY_DEV[dr], 90.0)
            assert abs(g - v) <= 0.06 + pg.BAND_DRAW_SCALE * pg.BAND_SPREAD_BY_DEV[dr]
    # a stored spread overrides the dev trait
    kw = dict(player_id=3, position="WR", age=23, ovr=75, dev_trait="Normal", ceiling=88.0, games_played=16,
              downs_played=None, is_starter=True, is_ir=False, injury_weeks=0, season=2037,
              aging_curves=mock_aging_curves)
    assert pg.compute_player_delta(**kw)[2]["dev"] == 0.0
    assert pg.compute_player_delta(**kw, spread=2.0)[2]["dev"] > 0.5


def test_breakthrough_rule():
    assert pg.breakthrough(5, 2037, 29, True, True) == 0                  # too old
    assert pg.breakthrough(5, 2037, 24, True, False) == 0                 # one season isn't enough
    pts = [pg.breakthrough(pid, 2037, 24, True, True) for pid in range(200)]
    assert all(p == 0 or 1 <= p <= 4 for p in pts)
    assert 0 < sum(1 for p in pts if p) < 200
    assert pts[11] == pg.breakthrough(11, 2037, 24, True, True)
    band = pg.seed_ceiling_band(5, 80, "Normal", 85.0)
    raised, ceiling = pg.apply_breakthrough(band, 85.0, 3)
    assert raised["group_ceilings"]["technical"] == 88.0 and raised["group_ceilings"]["mental"] == 89.0
    assert raised["group_ceilings"]["physical"] == band["group_ceilings"]["physical"] and ceiling == 88.0
    perf = {i: {"score": i / 10, "group": "REC"} for i in range(8)}
    perf.update({100 + i: {"score": 0.0, "group": "QB"} for i in range(8)})   # all tied: nobody stands out
    assert pg.top_quarter(perf) == {6, 7}


def test_breakthrough_tags_the_cause(mock_aging_curves):
    kw = dict(player_id=3, position="WR", age=24, ovr=75, dev_trait="Star", ceiling=88.0, games_played=16,
              downs_played=None, is_starter=True, is_ir=False, injury_weeks=0, season=2037,
              aging_curves=mock_aging_curves)
    assert pg.compute_player_delta(**kw, breakthrough=True)[3] == "BREAKTHROUGH"


def test_upkeep_only_above_90():
    assert pg.upkeep_term(90, -1.0, 10, 1.0) == 0.0
    assert pg.upkeep_term(85, -1.0) == 0.0
    middling = pg.upkeep_term(98, 0.0)
    assert -2.5 <= middling <= -1.5                                       # a 98 with a middling season loses ~2
    assert pg.upkeep_term(98, 0.0, games_missed=6) < middling             # missed games cost more
    assert pg.upkeep_term(98, 0.0, wear_excess=1.0) < middling
    assert pg.upkeep_term(98, 1.0) == 0.0                                 # top producer keeps it
    assert pg.upkeep_term(99, -1.0, 17, 5.0) >= pg.UPKEEP_TERM_MIN


def test_upkeep_sits_inside_the_league_term(mock_aging_curves):
    kw = dict(player_id=8, position="QB", age=28, ovr=94, dev_trait="Superstar", ceiling=97.0, games_played=17,
              downs_played=None, is_starter=True, is_ir=False, injury_weeks=0, season=2037,
              aging_curves=mock_aging_curves)
    base = pg.compute_player_delta(**kw)
    taxed = pg.compute_player_delta(**kw, upkeep=-2.0)
    assert taxed[2]["upkeep"] == -2.0 and taxed[2]["league"] == base[2]["league"] - 2.0


def test_continuity_and_mentor_are_small_and_bounded():
    assert pg.continuity_term(23, True, False) == -pg.CONTINUITY_PENALTY
    assert pg.continuity_term(23, False, True) == -pg.CONTINUITY_PENALTY
    assert pg.continuity_term(23, False, False) == pg.CONTINUITY_BONUS
    assert pg.continuity_term(32, True, True) == round(-pg.CONTINUITY_PENALTY * 0.25, 3)
    assert pg.continuity_term(23, True, True, known=False) == 0.0
    for age in range(20, 40):
        for a in (True, False):
            for b in (True, False):
                assert -0.3 <= pg.continuity_term(age, a, b) <= 0.1
        assert 0.0 <= pg.mentor_term(age, True) <= pg.MENTOR_BONUS
        assert pg.mentor_term(age, False) == 0.0
    assert pg.mentor_term(22, True) == pg.MENTOR_BONUS and pg.mentor_term(25, True) == pg.MENTOR_BONUS / 2
    assert pg.mentor_term(26, True) == 0.0
    me = {"player_id": 1, "position": "WR", "age": 22}
    mates = [me, {"player_id": 2, "position": "TE", "age": 31}, {"player_id": 3, "position": "WR", "age": 30},
             {"player_id": 4, "position": "CB", "age": 33}, {"player_id": 5, "position": "WR", "age": 26}]
    aw = {2: 90, 3: 86, 4: 95, 5: 92}
    assert pg.find_mentor(me, mates, aw) == 2                             # same group (REC), old enough, most aware
    assert pg.find_mentor(me, mates, {2: 80, 3: 84, 4: 95, 5: 92}) is None


def test_behaviour_changes_are_stored_outcomes():
    import json as _json
    sacked = {"pass_attempts": 400, "pass_sacks": 55, "rush_attempts": 30}
    outs = [pg.behaviour_changes(pid, 2037, "QB", 1, sacked) for pid in range(200)]
    gained = [o for o in outs if o]
    assert 0 < len(gained) < 200
    for o in gained:
        assert len(o["gained"]) == 1 and o["lost"] == []                  # no known flags: nothing to lose
        assert o["gained"][0]["flag"] in pg.PT_BAD_QB and o["gained"][0]["cause"] == "SACKS"
        _json.dumps(o)
    assert outs[3] == pg.behaviour_changes(3, 2037, "QB", 1, sacked)       # seeded
    # a known flag can go once the situation improves; a flag already set isn't gained again
    clean = {"pass_attempts": 500, "pass_sacks": 15}
    lost = [pg.behaviour_changes(pid, 2037, "QB", 2, clean, {"PT_HAPPYFEET": True}) for pid in range(200)]
    assert any(o and o["lost"] == [{"flag": "PT_HAPPYFEET", "cause": "BETTER_SITUATION"}] for o in lost)
    assert all(o is None or o["gained"] == [] for o in lost)
    veteran = pg.behaviour_changes(1, 2037, "QB", 6, sacked)              # past the young-QB window
    assert veteran is None or not veteran["gained"]
    assert pg.behaviour_changes(1, 2037, "CB", 1, sacked) is None
    assert pg.behaviour_changes(1, 2037, "QB", 1, None) is None


def test_persona_and_coach_terms_are_zero(mock_aging_curves):
    _, _, b, _ = pg.compute_player_delta(
        player_id=1, position="QB", age=24, ovr=80, dev_trait="Star", ceiling=88.0, games_played=16,
        downs_played=None, is_starter=True, is_ir=False, injury_weeks=0, season=2037, aging_curves=mock_aging_curves)
    assert b["persona"] == 0.0 and b["coach"] == 0.0
    row = {c: 0 for c in ("season", "player_id", "old_ovr", "new_ovr", "delta_ovr", "age_term", "dev_term",
                          "ceiling_term", "play_term", "injury_term", "noise_term", "league_term", "performance_term",
                          "wear_term", "upkeep_term", "continuity_term", "mentor_term")}
    row.update(ratings_diff={"Speed": -1}, cause="AGE", checkpoint="OFFSEASON", dev_before="Star", dev_after="Star",
               group_changes=pg.group_changes({"Speed": -1, "Awareness": 2, "Catching": 1}),
               persona_term=pg.PERSONA_TERM, coach_term=pg.COACH_TERM,
               behaviour_changes={"gained": [{"flag": "PT_HAPPYFEET", "cause": "SACKS"}], "lost": []})
    cols, vals = pg.history_rows([row])
    v = dict(zip(cols, vals[0]))
    assert v["persona_term"] == 0 and v["coach_term"] == 0 and v["checkpoint"] == "OFFSEASON"
    assert '"PT_HAPPYFEET"' in v["behaviour_changes"]
    assert row["group_changes"] == {"physical": -1, "technical": 1, "mental": 2}
    assert "wear_term" not in pg.history_rows([row], with_c2=False)[0]
    # dev_before / dev_after fit VARCHAR(16)
    assert pg.dev_label("Superstar X-Factor") == "X-Factor" and pg.dev_label("Star") == "Star"
    assert pg.dev_label(None) is None


def _ctx(**over):
    ctx = {"season": 2037, "stats": {}, "normal_wear": {"RB": 6.0}, "injuries": {}, "coord_start": {},
           "team_last": {}, "team_prev": {}, "roster_by_team": {}, "durability": {}}
    ctx.update(over)
    return ctx


def test_c2_player_inputs_from_synthetic_context():
    heavy = {s: {"position": "HB", "games_played": 17, "downs_played": 650, "rush_attempts": 320, "receptions": 45}
             for s in (2033, 2034, 2035, 2036)}
    ctx = _ctx(stats={10: heavy}, durability={10: (0.3, "RATINGS")}, team_last={10: 4, 11: 4},
               team_prev={10: 4, 11: 9}, coord_start={(4, "OC"): 2030},
               roster_by_team={4: [{"player_id": 10, "position": "HB", "age": 27},
                                   {"player_id": 12, "position": "HB", "age": 31}]},
               injuries={10: ["TORN ACHILLES"]})
    x = pg.c2_player_inputs({"player_id": 10, "position": "HB"}, ctx, None, {12: 90}, age=28)
    assert x["durability"] == 0.3 and x["mileage_excess"] > 1.0 and x["wear"] < -0.5
    assert set(x["mileage"]) == {2033, 2034, 2035, 2036}
    assert x["continuity"] == pg.CONTINUITY_BONUS * 0.5                   # same team, same OC, age 28
    assert x["mentor"] == 0.0 and x["mentor_id"] is None                  # too old to be mentored
    assert x["last_stats"] == heavy[2036]
    rookie = pg.c2_player_inputs({"player_id": 11, "position": "HB"}, ctx, {"Injury": 90, "Toughness": 90},
                                 {12: 90}, age=22)
    assert rookie["wear"] == 0.0 and rookie["durability_source"] == "RATINGS"
    assert rookie["continuity"] == -pg.CONTINUITY_PENALTY               # changed teams
    assert rookie["mentor_id"] == 12 and rookie["mentor"] == pg.MENTOR_BONUS
    new_oc = pg.c2_player_inputs({"player_id": 10, "position": "HB"},
                                 _ctx(team_last={10: 4}, coord_start={(4, "OC"): 2036}), None, {}, age=24)
    assert new_oc["continuity"] == -pg.CONTINUITY_PENALTY


def _synthetic_league(n=900):
    rng = random.Random("synthetic-league")
    positions = ["QB", "HB", "WR", "TE", "LT", "LG", "C", "DT", "LEDG", "MIKE", "CB", "FS", "K"]
    devs = ["Normal"] * 70 + ["Star"] * 20 + ["Superstar"] * 8 + ["Superstar X-Factor"] * 2
    out = []
    for pid in range(1, n + 1):
        age = rng.randint(21, 35)
        ovr = max(55, min(99, int(round(rng.gauss(76, 7)))))
        out.append({"player_id": pid, "position": rng.choice(positions), "age": age, "ovr": ovr,
                    "dev": rng.choice(devs), "excess": rng.uniform(-0.6, 1.2), "durability": rng.random(),
                    "new_coord": rng.random() < 0.15, "changed": rng.random() < 0.15, "mentor": rng.random() < 0.3,
                    "score": rng.uniform(-1, 1), "injury": ["ACL TEAR"] if rng.random() < 0.01 else [],
                    "games": rng.choice([17] * 8 + [10, 4])})
    return out


def test_c2_terms_hold_the_synthetic_league_shape(mock_aging_curves):
    """Regression bound next to the real backtest: on a synthetic league the C2 terms move the mean yearly change by
    at most 0.1 OVR, and with the league term refitted (upkeep inside it) the projected 90+ count by at most 5 and
    the mean OVR by at most 0.1."""
    league = _synthetic_league()

    def kwargs(p, c2):
        ceiling, _ = seed_player_ceiling(p["player_id"], p["ovr"], p["age"], p["dev"])
        kw = dict(player_id=p["player_id"], position=p["position"], age=p["age"], ovr=p["ovr"], dev_trait=p["dev"],
                  ceiling=ceiling, games_played=p["games"], downs_played=None, is_starter=True, is_ir=False,
                  injury_weeks=0, season=2037, aging_curves=mock_aging_curves,
                  performance=pg.performance_term({"score": p["score"], "awards": 0.0}))
        if c2:
            dmg = pg.lasting_injury_damage(p["player_id"], 2037, p["injury"], p["durability"])
            kw.update(wear=pg.wear_term(p["age"], p["position"], p["excess"], p["durability"]),
                      lasting_damage=-sum(dmg.values()),
                      continuity=pg.continuity_term(p["age"], p["new_coord"], p["changed"]),
                      mentor=pg.mentor_term(p["age"], p["mentor"]),
                      upkeep=pg.upkeep_term(p["ovr"], p["score"], 17 - p["games"], p["excess"]))
        return kw

    off = [kwargs(p, False) for p in league]
    on = [kwargs(p, True) for p in league]
    no_upkeep = [{**kw, "upkeep": 0.0} for kw in on]
    mean = lambda rows: sum(pg.compute_player_delta(**kw)[0] for kw in rows) / len(rows)
    assert abs(mean(no_upkeep) - mean(off)) <= 0.1

    target = {"count_90_plus": sum(1 for p in league if p["ovr"] >= 90),
              "avg_ovr": sum(p["ovr"] for p in league) / len(league)}
    progress = lambda kw, dev, star: compute_player_delta(**kw, league_adj=dev, star_adj=star)[1]
    _, shape_off = pg.calibrate_league_term(off, [], target, progress)
    _, shape_on = pg.calibrate_league_term(on, [], target, progress)
    assert abs(shape_on["count_90_plus"] - shape_off["count_90_plus"]) <= 5
    assert abs(shape_on["avg_ovr"] - shape_off["avg_ovr"]) <= 0.1


@pytest.mark.integration
def test_c2_backtest_and_simulation_bounds():
    """The real bounds on madden_test (U7): backtest MAE within 0.05 of 1.134, five simulated seasons' 90+ count
    within 143-146 ± 5."""
    conn = psycopg.connect(TEST_DSN)
    with conn:
        bt = run_backtest(conn, season=2036, log=lambda *args: None)
        sim = run_simulation(conn, start_season=2036, num_seasons=5, log=lambda *args: None)
    assert abs(bt["mae"] - 1.134) <= 0.05
    assert all(138 <= s["count_90_plus"] <= 151 for s in sim["history"])
