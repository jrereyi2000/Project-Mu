"""Free agency engine (docs/in_progress/V2_CORE_BUILD_PLAN.md §2.4, U5). Synthetic dicts only: no database."""
import math

import pytest

from engines.contracts_engine import minimum_salary
from engines.free_agency_engine import (W_DEFAULT, acceptance, offer_utility, role_score, run_wave, street_market,
                                        udfa_market)
from engines.trade_value import worth

MARKET = {"b": 0.09, "a_league": math.log(3_000_000), "a": {"QB": math.log(6_000_000)}, "n": {}}
NEUTRAL_TEAM = {"team_id": 1, "win_pct": 0.5, "playoff_odds": 0.5, "popularity_index": 50}
CONTENDER = {"team_id": 2, "win_pct": 0.8, "playoff_odds": 0.9, "popularity_index": 80, "direction": "CONTEND"}
REBUILDER = {"team_id": 3, "win_pct": 0.25, "playoff_odds": 0.1, "popularity_index": 20, "direction": "REBUILD"}


def player(pid=1, group="WR", ovr=85, age=27, accrued=5):
    return {"id": pid, "group": group, "ovr": ovr, "age": age, "accrued_seasons": accrued}


def offer(p, team_id=1, mult=1.0, years=2, guar_share=0.4, depth=1, bonus_share=0.1):
    total = int(round(worth(p["ovr"], p["group"], MARKET) * mult * years))
    return {"team_id": team_id, "player_id": p["id"], "years": years, "total_value": total,
            "bonus": int(total * bonus_share), "guaranteed": int(total * guar_share), "projected_depth": depth}


def decide(o, p, team, in_season=False):
    return acceptance({"offer": o, "team": team, "market": MARKET}, p, in_season)


# ---------------------------------------------------------------- utility ----
def test_utility_parts_and_weights_sum():
    assert sum(W_DEFAULT.values()) == pytest.approx(1.0)
    p = player()
    u = offer_utility(offer(p), p, dict(NEUTRAL_TEAM, scheme_fit={1: 0.9}), MARKET)
    assert set(u) == {"utility", "financial", "opportunity", "contention", "scheme", "persona"}
    expected = (W_DEFAULT["money"] * u["financial"] + W_DEFAULT["role"] * u["opportunity"]
                + W_DEFAULT["team"] * u["contention"] + W_DEFAULT["scheme"] * u["scheme"])
    assert u["utility"] == pytest.approx(expected, abs=1e-3)
    assert u["scheme"] == 0.9 and u["opportunity"] == 1.0
    assert offer_utility(offer(p), p, NEUTRAL_TEAM, MARKET)["scheme"] == 0.5     # no fit given -> neutral


def test_more_money_scores_higher():
    p = player()
    lo = offer_utility(offer(p, mult=0.8), p, NEUTRAL_TEAM, MARKET)
    hi = offer_utility(offer(p, mult=1.2), p, NEUTRAL_TEAM, MARKET)
    assert hi["financial"] > lo["financial"] and hi["utility"] > lo["utility"]


def test_role_score_values():
    assert role_score(1, 70) == 1.0
    assert role_score(2, 70) == 0.65
    assert role_score(3, 70) == 0.3
    assert role_score(4, 85) < 0.3                                                 # 80+ treats backup as severe


def test_projected_depth_from_team_overrides_offer():
    p = player()
    team = dict(NEUTRAL_TEAM, projected_depth={1: 3})
    assert offer_utility(offer(p, depth=1), p, team, MARKET)["opportunity"] < 0.3


# ------------------------------------------------------- starter vs backup ----
def test_star_rejects_backup_without_premium_accepts_with_one():
    p = player(ovr=85)
    assert decide(offer(p, depth=1), p, NEUTRAL_TEAM) == ("ACCEPT", None)
    assert decide(offer(p, depth=3), p, NEUTRAL_TEAM) == ("REJECT", None)
    assert decide(offer(p, mult=1.6, depth=3), p, NEUTRAL_TEAM)[0] == "ACCEPT"


def test_non_star_backup_is_not_auto_rejected():
    p = player(ovr=68)
    status, _ = decide(offer(p, mult=1.3, depth=3), p, NEUTRAL_TEAM)
    assert status in ("ACCEPT", "COUNTER")


# --------------------------------------------------------------- contention ----
def test_contender_discount_and_rebuilder_tax():
    p = player(ovr=85)
    o = offer(p, mult=0.8)
    uc = offer_utility(dict(o, team_id=2), p, CONTENDER, MARKET)
    ur = offer_utility(dict(o, team_id=3), p, REBUILDER, MARKET)
    assert uc["contention"] > ur["contention"] and uc["utility"] > ur["utility"]
    assert decide(dict(o, team_id=2), p, CONTENDER)[0] == "ACCEPT"
    status, counter = decide(dict(o, team_id=3), p, REBUILDER)
    assert status == "COUNTER"
    assert counter["total_value"] / counter["years"] > o["total_value"] / o["years"]   # the rebuilder pays more
    assert set(counter) == {"years", "total_value", "bonus", "guaranteed"}


def test_lowball_is_rejected():
    p = player(ovr=85)
    assert decide(offer(p, mult=0.3), p, REBUILDER) == ("REJECT", None)


# --------------------------------------------------------------- guarantees ----
def test_guaranteed_ratio_matters_more_for_veterans():
    vet, young = player(ovr=80, age=32), player(ovr=80, age=25)

    def delta(p):
        lo = offer_utility(offer(p, years=3, guar_share=0.05, bonus_share=0.0), p, NEUTRAL_TEAM, MARKET)
        hi = offer_utility(offer(p, years=3, guar_share=0.6, bonus_share=0.0), p, NEUTRAL_TEAM, MARKET)
        return hi["financial"] - lo["financial"]

    assert delta(vet) > delta(young) > 0


def test_veteran_penalises_backloaded_non_guaranteed():
    vet = player(ovr=80, age=32)
    flat = offer(vet, years=4, guar_share=0.1, bonus_share=0.0)
    back = dict(flat, salary_by_year=[1, 1, 4, 6])
    flat = dict(flat, salary_by_year=[3, 3, 3, 3])
    assert (offer_utility(back, vet, NEUTRAL_TEAM, MARKET)["financial"]
            < offer_utility(flat, vet, NEUTRAL_TEAM, MARKET)["financial"])


def test_term_fit_by_age():
    vet, prime = player(age=32), player(age=25)
    v1 = offer_utility(offer(vet, years=1), vet, NEUTRAL_TEAM, MARKET)["financial"]
    v4 = offer_utility(offer(vet, years=4), vet, NEUTRAL_TEAM, MARKET)["financial"]
    p2 = offer_utility(offer(prime, years=2), prime, NEUTRAL_TEAM, MARKET)["financial"]
    p5 = offer_utility(offer(prime, years=5), prime, NEUTRAL_TEAM, MARKET)["financial"]
    assert v4 > v1 and p2 > p5


# ------------------------------------------------------------------ persona ----
def test_persona_term_zero_and_argument_ignored():
    p = player()
    o = offer(p)
    base = offer_utility(o, p, NEUTRAL_TEAM, MARKET)
    with_persona = offer_utility(o, p, NEUTRAL_TEAM, MARKET, persona={"traits": ["Ring Chaser", "Ego"]})
    assert base["persona"] == 0.0 and with_persona == base


# -------------------------------------------------------------------- waves ----
def wave_fixture(n=40):
    players = [player(pid=i, ovr=60 + (i % 35), age=24 + i % 10) for i in range(1, n + 1)]
    offers = []
    for p in players:
        for t in (10, 11, 12):                     # identical bids: the seed breaks the ties
            o = offer(p, team_id=t, mult=1.1, years=3, guar_share=0.5)
            o["team"] = {"team_id": t, "win_pct": 0.5, "playoff_odds": 0.5, "popularity_index": 50}
            o["market"] = MARKET
            offers.append(o)
    return players, offers


def test_wave_reproducible_and_seed_sensitive():
    players, offers = wave_fixture()
    a = run_wave(players, offers, 3, "fa:2037:w3")
    b = run_wave(players, offers, 3, "fa:2037:w3")
    c = run_wave(players, offers, 3, "fa:2037:other")
    assert a == b
    assert [(d["player_id"], d["team_id"]) for d in a] != [(d["player_id"], d["team_id"]) for d in c]


def test_wave_one_decision_per_player_and_tiers():
    players, offers = wave_fixture()
    for wave in (1, 2, 3):
        out = run_wave(players, offers, wave, "s")
        ids = [d["player_id"] for d in out]
        assert len(ids) == len(set(ids))
        for d in out:
            assert offers[d["offer_index"]] is d["offer"] and d["offer"]["player_id"] == d["player_id"]
            assert d["status"] in ("ACCEPT", "REJECT", "COUNTER")
    ovr = {p["id"]: p["ovr"] for p in players}
    assert all(ovr[d["player_id"]] >= 85 for d in run_wave(players, offers, 1, "s"))
    assert all(ovr[d["player_id"]] >= 75 for d in run_wave(players, offers, 2, "s"))
    assert len(run_wave(players, offers, 3, "s")) == len(players)


def test_wave_star_takes_starter_role_over_richer_backup():
    p = player(pid=7, ovr=88)
    backup = dict(offer(p, team_id=20, mult=1.3, depth=3), team=NEUTRAL_TEAM, market=MARKET)
    starter = dict(offer(p, team_id=21, mult=1.0, depth=1), team=NEUTRAL_TEAM, market=MARKET)
    out = run_wave([p], [backup, starter], 1, "x")
    assert out[0]["team_id"] == 21 and out[0]["status"] == "ACCEPT"


# ---------------------------------------------------------- street and UDFA ----
NEEDS = {
    1: {"needs": {"WR": 5.0}, "starter_ovr": {"WR": 70}, "win_pct": 0.6},
    2: {"needs": [{"group": "CB", "score": 8.0}, {"group": "WR", "score": 2.0}], "starter_ovr": {"WR": 78, "CB": 66}},
    3: {"needs": {"LB": 3.0}, "starter_ovr": {"LB": 60}, "roster_spots": 0},
}


def test_street_market_sane_signings():
    fas = [player(pid=100 + i, group=g, ovr=o, age=29, accrued=a)
           for i, (g, o, a) in enumerate([("WR", 72, 6), ("WR", 64, 2), ("CB", 67, 4), ("LB", 75, 7),
                                          ("TE", 70, 3)])]
    out = street_market(fas, NEEDS, "street:2037:5")
    assert out == street_market(fas, NEEDS, "street:2037:5")
    signed = [d for d in out if d["status"] == "ACCEPT"]
    assert signed
    groups = {p["id"]: p for p in fas}
    per_team = {}
    for d in out:
        p = groups[d["player_id"]]
        assert d["offer"]["years"] == 1 and d["offer"]["total_value"] == minimum_salary(p["accrued_seasons"])
        assert p["group"] in ("WR", "CB")               # LB team has no spots, nobody needs a TE
    for d in signed:
        per_team[d["team_id"]] = per_team.get(d["team_id"], 0) + 1
    assert all(n <= 1 for n in per_team.values())
    assert len({d["player_id"] for d in out}) == len(out)
    top_wr = next(d for d in signed if d["player_id"] == 100)
    assert top_wr["team_id"] == 1                       # he starts on team 1 (72 >= 70), backs up on team 2


def test_udfa_market_rookie_minimum_and_role():
    prospects = [player(pid=200 + i, group=g, ovr=o, age=22, accrued=0)
                 for i, (g, o) in enumerate([("CB", 68), ("WR", 71), ("WR", 58), ("CB", 55), ("CB", 54),
                                             ("CB", 53), ("TE", 60)])]
    out = udfa_market(prospects, NEEDS, "udfa:2037")
    assert out == udfa_market(prospects, NEEDS, "udfa:2037")
    assert len({d["player_id"] for d in out}) == len(out)
    per_team = {}
    for d in out:
        o = d["offer"]
        assert o["years"] == 3 and o["total_value"] >= 3 * minimum_salary(0)
        assert o["guaranteed"] == o["bonus"] <= 100_000
        if d["status"] == "ACCEPT":
            per_team[d["team_id"]] = per_team.get(d["team_id"], 0) + 1
    assert all(n <= 3 for n in per_team.values()) and per_team
    assert all(d["player_id"] != 206 for d in out)      # nobody needs a TE
    cb = next(d for d in out if d["player_id"] == 200)
    assert cb["status"] == "ACCEPT" and cb["team_id"] == 2   # starts at CB on team 2
