"""Trade value model (docs/team_management/03_GM_AGENTS_PLAN.md Phase 3). Fixture rows only: no database."""
import math
import random

from engines.trade_value import (GENERIC_AGING, build_fits, fit_aging, fit_market, league_value, pick_value,
                                 player_value, tradeable, worth)


def market_rows():
    rng = random.Random(1)
    rows = []
    for g, shift in (("QB", 0.5), ("WR", 0.2), ("CB", 0.1), ("RB", -0.3), ("K", -0.8)):
        n = 40 if g != "K" else 8                              # K: too few rows -> league intercept
        for _ in range(n):
            o = rng.randint(65, 97)
            rows.append((g, o, math.exp(15 + shift + 0.09 * (o - 75) + rng.gauss(0, 0.2))))
    return rows


def draft_rows():
    rows = []
    for pick in range(1, 225):
        ovr = 78 - 4 * math.log(pick) + (1 if pick % 7 == 0 else 0)
        hit = 13_000_000 * pick ** -0.55 + 600_000
        rows.append((pick, ovr, hit))
    return rows


def aging_pairs():
    rng = random.Random(2)
    return [("WR", a, 80, 80 + GENERIC_AGING[min(5, max(0, (a - 21) // 3))] + rng.gauss(0, 1))
            for a in range(21, 36) for _ in range(20)]


FITS = build_fits(market_rows(), aging_pairs(), draft_rows())


def player(pid=1, group="WR", ovr=85, age=27, end=2038, hits=None, projected=(), team=1, dev="Normal", ntc=False):
    hits = hits or {y: 5_000_000 for y in range(2036, end + 1)}
    years = {y: (h, y in projected) for y, h in hits.items()}
    return {"id": pid, "team_id": team, "group": group, "ovr": ovr, "age": age, "dev": dev,
            "contract": {"end_season": end, "years": years, "no_trade_clause": ntc}}


def ctx(direction, team=2, needs=(), lines=None, youth=0.0):
    return {"team_id": team, "direction": direction, "needs": list(needs), "lines": lines or {"WR": 70},
            "starter_ids": set(), "position_bias": {}, "youth_bias": youth, "pick_affinity": 1.0, "slots": {}}


def test_worth_monotone_in_ovr():
    m = FITS["market"]
    vals = [worth(o, "WR", m) for o in range(60, 100)]
    assert all(b > a for a, b in zip(vals, vals[1:]))
    assert "K" not in m["a"] and worth(80, "K", m) == worth(80, None, m)     # small group -> league intercept


def test_aging_shrinks_toward_generic_on_small_samples():
    small = fit_aging([("TE", 30, 80, 90)])                                    # one +10 pair at age 30
    assert abs(small["TE"][3] - small["generic"][3]) < 1.0
    big = fit_aging([("TE", 30, 80, 90)] * 400)
    assert big["TE"][3] > 8


def test_surplus_ends_at_contract_end():
    short = player_value(player(end=2036), ctx("RETOOL"), FITS, 2036)
    long = player_value(player(end=2038), ctx("RETOOL"), FITS, 2036)
    assert [y[0] for y in short["years"]] == [2036]
    assert [y[0] for y in long["years"]] == [2036, 2037, 2038]


def test_projected_years_counted_and_flagged():
    p = player(end=2038, hits={2036: 5e6, 2037: 5e6, 2038: 7e6}, projected={2038})
    v = player_value(p, ctx("RETOOL"), FITS, 2036)
    assert v["projected"] and v["years"][-1][3] is True
    assert not player_value(player(end=2038), ctx("RETOOL"), FITS, 2036)["projected"]


def test_rebuild_values_veteran_below_contend():
    vet = player(age=31, ovr=88, end=2038)
    assert player_value(vet, ctx("REBUILD"), FITS, 2036)["value"] < player_value(vet, ctx("CONTEND"), FITS, 2036)["value"]


def test_need_raises_utility_and_backup_lowers_it():
    p = player(ovr=85)
    base = player_value(p, ctx("RETOOL"), FITS, 2036)["value"]
    need = player_value(p, ctx("RETOOL", needs=[{"group": "WR", "score": 10}]), FITS, 2036)["value"]
    backup = player_value(p, ctx("RETOOL", lines={"WR": 90}), FITS, 2036)["value"]
    assert need > base > backup


def test_pick_curve_monotone_and_r1_above_r2():
    v = FITS["pick"]["value"]
    assert all(b <= a + 1e-6 for a, b in zip(v, v[1:]))
    assert sum(v[:32]) / 32 > sum(v[32:64]) / 32 > 0
    r1 = pick_value({"draft_season": 2037, "round": 1, "original_team_id": 5}, None, FITS, 2036)
    r2 = pick_value({"draft_season": 2037, "round": 2, "original_team_id": 5}, None, FITS, 2036)
    later = pick_value({"draft_season": 2039, "round": 1, "original_team_id": 5}, None, FITS, 2036)
    assert r1 > r2 and r1 > 0 and later < r1


def test_pick_direction_multipliers():
    pk = {"draft_season": 2037, "round": 1, "original_team_id": 5}
    reb = pick_value(pk, ctx("REBUILD"), FITS, 2036)
    con = pick_value(pk, ctx("CONTEND"), FITS, 2036)
    assert reb > 0 and con > 0 and reb != con


def test_ntc_def_injured_and_flipped_excluded():
    assert tradeable(player(ntc=True)) == (False, "no-trade clause")
    assert tradeable(dict(player(), group=None))[0] is False                   # the DEF row has no group
    assert tradeable(dict(player(), contract=None)) == (False, "no active contract")
    assert tradeable(player(pid=7), injured={7}) == (False, "injured")
    assert tradeable(player(pid=7), acquired={7}) == (False, "acquired by trade this season")
    assert tradeable(player()) == (True, None)


def test_league_value_is_neutral():
    p = player(ovr=85)
    assert league_value(p, FITS, 2036) == player_value(p, None, FITS, 2036)["value"]
    assert fit_market([])["b"] > 0


def test_rookie_year_block():
    rookie = dict(player(), yoe=0)
    assert tradeable(rookie, rookie_year=True) == (False, "rookie season")
    assert tradeable(rookie) == (True, None)
    assert tradeable(dict(player(), yoe=1), rookie_year=True) == (True, None)
