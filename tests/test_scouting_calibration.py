"""Team boards and the poor-pick calibration band (docs/team_management/04_SCOUTING_AND_DRAFT_PLAN.md Phase 4, master
§2.5: 20-30% poor Round 1 picks, clustered among noisy front offices). Pure: the frozen 2037 class and a frozen
export of the 2037 Round 1 owners and the 32 GMs (tests/fixtures/scouting_teams_2036.json)."""
import json
import os
import statistics

import pytest

from engines import scouting_boards as sb
from tests.test_scouting_engine import load_fixture

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "scouting_teams_2036.json")


@pytest.fixture(scope="module")
def setup():
    fx = json.load(open(FIX))
    ctxs = {int(t): sb.team_context(dict(g, team_id=int(t))) for t, g in fx["teams"].items()}
    return load_fixture(), fx["order"], ctxs


@pytest.fixture(scope="module")
def spread(setup):
    cls, order, ctxs = setup
    return sb.calibrate(cls, order, sb.spread_q(ctxs), drafts=300, seed=7)


def test_poor_pick_rate_in_band_under_spread(spread):
    assert 0.20 <= spread["poor_rate"] <= 0.30


def test_poor_picks_cluster_in_noisy_front_offices(spread):
    b = spread["bands"]
    assert b[">=1.15"]["poor_rate"] >= 1.5 * b["<=0.85"]["poor_rate"]


def test_live_with_neutral_q_in_band(setup):
    cls, order, ctxs = setup
    neutral = {t: dict(c, q=1.0) for t, c in ctxs.items()}
    r = sb.calibrate(cls, order, neutral, drafts=300, seed=11)
    assert 0.20 <= r["poor_rate"] <= 0.30


def test_visit_lowers_team_error():
    plain = [abs(sb.team_error(1.0, z, z2)) for z, z2 in ((0.5, -1.0), (1.2, 0.3), (-2.0, 0.8))]
    seen = [abs(sb.team_error(1.0, z, z2, visited=True)) for z, z2 in ((0.5, -1.0), (1.2, 0.3), (-2.0, 0.8))]
    assert all(s == pytest.approx(p * sb.VISIT_M) for s, p in zip(seen, plain))


def test_qb_rule(setup):
    _, _, ctxs = setup
    ctx = next(c for c in ctxs.values() if not c["qb_need"])
    qb = {"position": "QB", "age": 22, "V": 80}
    wr = {"position": "WR", "age": 22, "V": 80}
    f_qb, b_qb = sb.fit_bias(qb, 80, dict(ctx, position_bias={}, youth_bias=0.0))
    f_wr, b_wr = sb.fit_bias(wr, 80, dict(ctx, position_bias={}, youth_bias=0.0))
    assert b_qb == pytest.approx(b_wr - sb.QB_PENALTY)
    needy = dict(ctx, qb_need=True, position_bias={}, youth_bias=0.0)
    assert sb.fit_bias(qb, 80, needy)[1] == pytest.approx(0.0)


def test_team_z_is_seeded_unit_ar1():
    a = sb.team_z(2037, 5, "k", 4, 27)
    assert a == sb.team_z(2037, 5, "k", 4, 27) and a != sb.team_z(2037, 6, "k", 4, 27)
    zs = [sb.team_z(2037, t, f"k{i}", 4, 27) for t in range(5) for i in range(200)]
    assert abs(statistics.pstdev(zs) - 1.0) < 0.1


def test_recommend_stays_inside_reach(setup):
    cls, _, _ = setup
    cons = {p["key"]: {"rank": i + 1, "err": 0.0} for i, p in enumerate(cls)}
    far = cls[-1]["key"]
    rows = [(far, 99.0, 0, 0, 0), (cls[0]["key"], 50.0, 0, 0, 0)]
    best, alts = sb.recommend(rows, cons, {far, cls[0]["key"]})
    assert best[0] == cls[0]["key"]
