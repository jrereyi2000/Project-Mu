"""engines/owner_policy.py (OWNERS_SCOUTS_VOTERS_PLAN.md §2.3, §4). Pure; no DB."""
import itertools

import pytest

from engines import owner_policy as op
from engines import transactions_engine as te
from engines.agent_loops import base

PAIRS = (("MEDDLER", "HANDS_OFF"), ("DEEP_POCKETS", "BUDGET_CONSCIOUS"), ("HEADLINE_HUNTER", "PATIENT_BUILDER"))


def view(*traits, **sliders):
    return {"traits": list(traits), "sliders": {**{s: 5 for s in ("temperament", "sociability", "confidence",
                                                                  "openness", "warmth", "drive")}, **sliders}}


def all_views():
    for combo in itertools.product(*[(None,) + p for p in PAIRS]):
        for t, d, o in itertools.product((0, 5, 10), repeat=3):
            yield view(*[c for c in combo if c], temperament=t, drive=d, openness=o)


CANDS = [{"key": "coach:1", "kind": "RETREAD"}, {"key": "coord:2", "kind": "POACHED", "age": 40},
         {"key": "coord:3", "kind": "POACHED", "age": 55}, {"key": "x:4", "kind": "OUTSIDER"},
         {"key": "coord:5", "kind": "POACHED"}]

GM_CANDS = [{"key": "gm:E", "tier": "ELITE"}, {"key": "gm:S", "tier": "STANDARD"},
            {"key": "gm:P", "tier": "PROMOTION"}]


def test_neutral_owner_is_todays_constants():
    for v in (None, {}, view()):
        assert op.patience(v) == {"hot_seat_line_scale": 1.0, "min_tenure_delta": 0}
        assert op.appeal_term(v) == 0.0
        assert all(op.hiring_key(v, c) == 0.0 for c in CANDS)
        assert all(op.gm_hiring_key(v, c) == 0.0 for c in GM_CANDS)
        assert op.spending(v) == {"cap_buffer_share": te.CAP_BUFFER_SHARE, "offers_per_wave": te.OFFERS_PER_WAVE,
                                  "need_premium": te.OFFER_NEED_PREMIUM}


def test_every_policy_stays_in_its_bounds():
    for v in all_views():
        p = op.patience(v)
        assert 0.85 <= p["hot_seat_line_scale"] <= 1.15 and -1 <= p["min_tenure_delta"] <= 1
        assert all(abs(op.hiring_key(v, c)) <= 0.15 for c in CANDS)
        assert all(abs(op.gm_hiring_key(v, c)) <= op.GM_HIRING_CAP for c in GM_CANDS)
        assert abs(op.appeal_term(v)) <= 0.05
        s = op.spending(v)
        assert 0.0 <= s["cap_buffer_share"] <= 0.05 and 3 <= s["offers_per_wave"] <= 6
        assert 0.05 <= s["need_premium"] <= 0.15
        for m in MOVES.values():
            w = op.meddle_weights(m, v)
            assert all(x >= 0 for x in w.values()) and abs(sum(w.values()) - 1) < 1e-9


def test_opposing_traits_move_opposite_ways():
    assert op.patience(view("PATIENT_BUILDER"))["hot_seat_line_scale"] == 1.1
    assert op.patience(view("PATIENT_BUILDER"))["min_tenure_delta"] == 1
    assert op.patience(view("HEADLINE_HUNTER"))["hot_seat_line_scale"] == 0.9
    assert op.patience(view(temperament=9))["hot_seat_line_scale"] < 1.0 < op.patience(view(temperament=1))[
        "hot_seat_line_scale"]
    assert op.patience(view(drive=9))["hot_seat_line_scale"] < 1.0
    assert op.spending(view("DEEP_POCKETS")) == {"cap_buffer_share": 0.0, "offers_per_wave": 6, "need_premium": 0.15}
    assert op.spending(view("BUDGET_CONSCIOUS")) == {"cap_buffer_share": 0.05, "offers_per_wave": 3,
                                                     "need_premium": 0.05}
    assert op.p_veto(MOVES["trade"], view("MEDDLER")) > op.p_veto(MOVES["trade"], view()) > op.p_veto(
        MOVES["trade"], view("HANDS_OFF"))


def test_hiring_terms():
    hh, pb = view("HEADLINE_HUNTER"), view("PATIENT_BUILDER")
    assert op.hiring_key(hh, CANDS[0]) == 0.15
    assert op.hiring_key(pb, CANDS[1]) == 0.10
    assert op.hiring_key(pb, CANDS[2]) == 0.0 and op.hiring_key(pb, CANDS[4]) == 0.0   # too old / age unknown
    assert op.hiring_key(view(openness=9), CANDS[1]) == 0.04
    assert op.hiring_key(view(openness=9), CANDS[0]) == 0.0      # openness favours first-timers only
    assert op.hiring_key(view("PATIENT_BUILDER", openness=10), CANDS[1]) == 0.15   # capped
    assert op.appeal_term(view("MEDDLER")) == -0.05 and op.appeal_term(view("HANDS_OFF")) == 0.0


def test_gm_hiring_terms():
    elite, standard, promotion = GM_CANDS
    assert op.gm_hiring_key(None, promotion) == 0.0
    assert op.gm_hiring_key(view(), elite) == 0.0
    # HEADLINE_HUNTER wants the proven architect, not the unknown first-timer
    assert op.gm_hiring_key(view("HEADLINE_HUNTER"), elite) == 0.15
    assert op.gm_hiring_key(view("HEADLINE_HUNTER"), standard) == 0.0
    assert op.gm_hiring_key(view("HEADLINE_HUNTER"), promotion) == -0.05
    # DEEP_POCKETS only sweetens ELITE
    assert op.gm_hiring_key(view("DEEP_POCKETS"), elite) == 0.10
    assert op.gm_hiring_key(view("DEEP_POCKETS"), promotion) == 0.0
    # BUDGET_CONSCIOUS / MEDDLER prefer a controllable first-timer and cool on an elite
    for trait in ("BUDGET_CONSCIOUS", "MEDDLER"):
        assert op.gm_hiring_key(view(trait), promotion) == 0.15
        assert op.gm_hiring_key(view(trait), standard) == 0.0
    assert op.gm_hiring_key(view("BUDGET_CONSCIOUS"), elite) == -0.15
    assert op.gm_hiring_key(view("MEDDLER"), elite) == -0.10
    # openness favors the first-timer only
    assert op.gm_hiring_key(view(openness=9), promotion) == 0.04
    assert op.gm_hiring_key(view(openness=1), promotion) == -0.04
    assert op.gm_hiring_key(view(openness=9), elite) == 0.0
    # bounded to +-GM_HIRING_CAP
    flush = view("BUDGET_CONSCIOUS", "MEDDLER", openness=10)
    assert op.gm_hiring_key(flush, promotion) == 0.25            # 0.15 + 0.15 + 0.05, capped
    assert op.gm_hiring_key(view("BUDGET_CONSCIOUS", "MEDDLER"), elite) == -0.25
    assert op.gm_hiring_key(flush, {"key": "x"}) == 0.0          # no tier, no term


MOVES = {
    "trade": {"kind": "TRADE", "team_id": 7, "ref": "t1", "position_rank": 3},
    "small_trade": {"kind": "TRADE", "team_id": 7, "ref": "t2", "position_rank": 11},
    "signing": {"kind": "SIGNING", "team_id": 7, "ref": "s1", "fa_rank": 12, "alternative": {"player_id": 99}},
    "small_signing": {"kind": "SIGNING", "team_id": 7, "ref": "s2", "fa_rank": 51},
    "hire": {"kind": "HIRE", "team_id": 7, "ref": "h1", "role": "HC", "alternative": {"key": "coach:3"}},
    "coord_hire": {"kind": "HIRE", "team_id": 7, "ref": "h2", "role": "OC"},
}


def test_meddling_only_on_big_cpu_moves_never_on_jrs_team():
    meddler = view("MEDDLER", "HEADLINE_HUNTER")
    for k in ("small_trade", "small_signing", "coord_hire"):
        assert op.meddle_options(MOVES[k], meddler) == []
        assert op.meddle_choice(MOVES[k], meddler, season=2026, tick=5) is None
    for k in ("trade", "signing", "hire"):
        assert op.meddle_options(MOVES[k], meddler)
        jr = dict(MOVES[k], user_controlled=True)
        assert op.meddle_options(jr, meddler) == []
        assert op.meddle_choice(jr, meddler, season=2026, tick=5) is None
        for s in range(200):
            assert op.meddle_choice(jr, meddler, season=2026 + s, tick=5) is None


def test_veto_and_push_options_through_the_loop_base():
    meddler = view("MEDDLER", "HEADLINE_HUNTER")
    opts = op.meddle_options(MOVES["signing"], meddler)
    assert all(isinstance(o, base.Option) for o in opts)
    assert [o.key for o in opts] == ["APPROVE", "VETO", "PUSH"]
    assert opts[2].detail["alternative"] == {"player_id": 99}
    assert [o.key for o in op.meddle_options(MOVES["trade"], meddler)] == ["APPROVE", "VETO"]   # trades: no push
    assert [o.key for o in op.meddle_options(MOVES["signing"], view("MEDDLER"))] == ["APPROVE", "VETO"]
    no_alt = dict(MOVES["signing"])
    no_alt.pop("alternative")
    assert "PUSH" not in [o.key for o in op.meddle_options(no_alt, meddler)]
    w = op.meddle_weights(MOVES["signing"], meddler)
    assert w["VETO"] == 0.25 and w["PUSH"] == 0.15 and w["APPROVE"] == 0.6
    fav = dict(MOVES["trade"], popularity_rank=5)
    assert op.p_veto(fav, view()) == 0.10
    keys = {op.meddle_choice(dict(MOVES["signing"], ref=f"s{i}"), meddler, season=2026, tick=3) for i in range(300)}
    assert keys == {"APPROVE", "VETO", "PUSH"}
    a = op.meddle_choice(MOVES["signing"], meddler, season=2026, tick=3)
    assert a == op.meddle_choice(MOVES["signing"], meddler, season=2026, tick=3)     # seeded


def test_veto_cap():
    meddler = view("MEDDLER")
    assert "VETO" not in [o.key for o in op.meddle_options(MOVES["trade"], meddler, vetoes_used=op.VETO_CAP)]
    for i in range(100):
        m = dict(MOVES["trade"], ref=f"t{i}")
        assert op.meddle_choice(m, meddler, season=2026, tick=4, vetoes_used=1) == "APPROVE"


def test_veto_rates_are_roughly_the_policy():
    hands_off = sum(op.meddle_choice(dict(MOVES["trade"], ref=f"r{i}"), view("HANDS_OFF"), season=2026, tick=2)
                    == "VETO" for i in range(2000)) / 2000
    meddler = sum(op.meddle_choice(dict(MOVES["trade"], ref=f"r{i}"), view("MEDDLER"), season=2026, tick=2)
                  == "VETO" for i in range(2000)) / 2000
    assert hands_off == pytest.approx(0.01, abs=0.01) and meddler == pytest.approx(0.25, abs=0.03)
