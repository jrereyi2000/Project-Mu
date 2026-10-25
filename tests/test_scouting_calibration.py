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


def test_qb_rule_is_graded_by_starter_security(setup):
    _, _, ctxs = setup
    qb = {"position": "QB", "age": 22, "V": 80}
    wr = {"position": "WR", "age": 22, "V": 80}
    base = dict(next(iter(ctxs.values())), position_bias={}, youth_bias=0.0, direction="RETOOL")
    for s in (0.0, 0.4, 1.0):
        ctx = dict(base, qb_security=s)
        f_qb, b_qb = sb.fit_bias(qb, 80, ctx)
        b_wr = sb.fit_bias(wr, 80, ctx)[1]
        assert f_qb == pytest.approx(sb.QB_PHI * (1 - s))
        assert b_qb == pytest.approx(b_wr - sb.QB_PENALTY * s)
    # a team with no QB1 pushes QBs up more than any non-QB need can
    assert sb.fit_bias(qb, 80, dict(base, qb_security=0.0))[0] > sb.PHI * 1.2


def test_qb_security_factors():
    elite = {"ovr": 95, "age": 26, "pct": 0.95, "years_left": 4}
    assert sb.qb_security(elite) == pytest.approx(1.0)
    assert sb.qb_security(None) == 0.0 and sb.qb_security({}) == 0.0
    assert sb.qb_security(dict(elite, pct=0.2)) == 0.0                     # bottom-quarter starter
    assert sb.qb_security(dict(elite, age=33)) == pytest.approx(0.5)        # aging starter
    assert sb.qb_security(dict(elite, age=36)) == 0.0
    assert sb.qb_security(dict(elite, years_left=0)) == pytest.approx(sb.QB_CONTRACT[0])
    assert sb.qb_security(dict(elite, years_left=None)) == pytest.approx(sb.QB_CONTRACT[0])
    assert sb.qb_security(dict(elite, years_left=1)) == pytest.approx(sb.QB_CONTRACT[1])
    mid = sb.qb_security(dict(elite, pct=0.55))
    assert 0 < mid < 1


def test_team_context_without_qb_facts_falls_back_to_needs():
    needy = sb.team_context({"needs": [{"group": "QB", "score": 5, "slot_gap": 3}]})
    set_ = sb.team_context({"needs": [{"group": "WR", "score": 5, "slot_gap": 3}]})
    assert needy["qb_security"] == 0.0 and set_["qb_security"] == 1.0
    assert sb.team_context({"needs": []}, {})["qb_security"] == 0.0           # no QB on the roster


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
