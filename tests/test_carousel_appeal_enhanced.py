"""Synthetic (no live DB) tests for the enhanced opening appeal and the Godfather Full Control governor
(STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN §2.1, §2.3, U2).

Pure functions only: `engines.coaching_carousel.opening_appeal` and its §2.1 components (qb_appeal, youth_core,
roster_appeal), the §2.3 Godfather decision and acceptance, and the GM hiring search in `engines.gm_rollover`.
"""
from engines.coaching_carousel import (APPEAL_WEIGHTS, gm_slant_bias, godfather_acceptance, godfather_candidate_ok,
                                       godfather_offer, godfather_owner_ok, opening_appeal, qb_appeal, roster_appeal,
                                       youth_core)
from engines.gm_rollover import gm_candidate_pool, gm_search
from engines.staff_contracts_engine import godfather_eligible

SEASON = 2036


# --------------------------------------------------------------- §2.1 QB security tiers ----
def test_weights_sum_to_one_and_missing_parts_are_middling():
    assert sum(APPEAL_WEIGHTS.values()) == 1.0
    assert opening_appeal({}) == 0.5
    assert opening_appeal({k: 1.0 for k in APPEAL_WEIGHTS}) == 1.0
    assert opening_appeal({"churn": 2}) == 0.4


def test_qb_tiers():
    assert qb_appeal({"ovr": 90, "age": 28}) == 1.00                       # Tier 1 elite
    assert qb_appeal({"ovr": 88, "age": 31}) == 1.00                       # Tier 1 edges
    assert qb_appeal({"ovr": 80, "age": 23, "dev_trait": "Star"}) == 0.90  # Tier 2 promising young
    assert qb_appeal({"ovr": 76, "age": 25, "dev_trait": "Superstar"}) == 0.90
    assert qb_appeal({"ovr": 77, "age": 25, "dev_trait": "Superstar X-Factor"}) == 0.90
    assert qb_appeal({"ovr": 80, "age": 29}) == 0.60                       # Tier 3 average starter
    assert qb_appeal({"ovr": 76, "age": 35, "cap_hit": 30_000_000}) == 0.25  # Tier 4 aging bridge
    assert qb_appeal({}) == 0.10                                           # Tier 5 wasteland
    assert qb_appeal({"ovr": 70, "age": 24, "dev_trait": "Normal"}) == 0.10


def test_qb_dev_trait_matters_for_young_qb():
    # A 24-year-old, 80-OVR starter is a promising young QB only with a Star/Superstar/X-Factor dev trait.
    assert qb_appeal({"ovr": 80, "age": 24, "dev_trait": "Star"}) == 0.90
    assert qb_appeal({"ovr": 80, "age": 24, "dev_trait": "Normal"}) == 0.10
    assert qb_appeal({"ovr": 80, "age": 24, "dev_trait": "Hidden"}) == 0.10


def test_opening_appeal_qb_outweighs_cap():
    assert opening_appeal({"qb": 1.0, "cap": 0.0}) > opening_appeal({"qb": 0.0, "cap": 1.0})


# --------------------------------------------------------------- §2.1 youth core ----
def test_youth_core_formula_and_clamp():
    assert youth_core(0, 0) == 0.0
    assert youth_core(11, 0) == 0.5
    assert youth_core(11, 2) == 0.6                    # +0.05 per under-25 X-factor
    assert youth_core(22, 0) == 1.0
    assert youth_core(22, 10) == 1.0                   # clamped at 1.0


def test_under25_roster_outscores_older_roster_of_same_ovr():
    # Same team OVR percentile, but one roster is young and blue-chip, the other old.
    young = roster_appeal(0.5, 20, 3)
    old = roster_appeal(0.5, 0, 0)
    assert young > old
    parts = {"qb": 0.6, "draft": 0.5, "cap": 0.5, "market": 0.5, "facilities": 0.5, "churn": 0}
    assert opening_appeal({**parts, "roster": young}) > opening_appeal({**parts, "roster": old})


# --------------------------------------------------------------- §2.3 Godfather governor ----
def test_godfather_rarity_gate_rejects_active_cap_and_cooldown():
    assert godfather_eligible(SEASON, active_full_control=0, last_godfather_season=None)
    assert godfather_eligible(SEASON, active_full_control=1, last_godfather_season=None)
    assert not godfather_eligible(SEASON, active_full_control=2, last_godfather_season=None)   # max 2 active
    assert godfather_eligible(SEASON, active_full_control=0, last_godfather_season=SEASON - 5)
    assert not godfather_eligible(SEASON, active_full_control=0, last_godfather_season=SEASON - 4)  # cooldown < 5


def test_godfather_offer_requires_every_condition():
    owner = {"traits": ["DEEP_POCKETS", "HEADLINE_HUNTER"]}
    candidate = {"super_bowl_wins": 1, "drive": 9}      # JR 2026-11-10: a ring AND drive/ego >= 8
    gate = {"eligible": True}
    kwargs = dict(gate=gate, owner_view=owner, cash_pool=45_000_000, hc_vacant=True, gm_vacant=True,
                  candidate=candidate)
    assert godfather_offer(**kwargs)["offer"]

    assert not godfather_offer(**dict(kwargs, gate={"eligible": False}))["offer"]           # rarity gate
    assert not godfather_offer(**dict(kwargs, owner_view={"traits": ["DEEP_POCKETS"]}))["offer"]
    assert not godfather_offer(**dict(kwargs, cash_pool=30_000_000))["offer"]              # under $35M
    assert not godfather_offer(**dict(kwargs, gm_vacant=False))["offer"]                   # not a dual vacancy
    assert not godfather_offer(**dict(kwargs, candidate={"super_bowl_wins": 0}))["offer"]  # no stature
    assert not godfather_offer(**dict(kwargs, candidate={"super_bowl_wins": 1}))["offer"]  # a ring alone is not it


def test_godfather_owner_and_candidate_checks():
    assert godfather_owner_ok({"traits": ["DEEP_POCKETS", "HEADLINE_HUNTER"]}, 35_000_000)
    assert not godfather_owner_ok({"traits": ["DEEP_POCKETS"]}, 50_000_000)
    assert not godfather_owner_ok({"traits": ["DEEP_POCKETS", "HEADLINE_HUNTER"]}, 34_000_000)
    assert not godfather_owner_ok(None, 50_000_000)

    # JR, 2026-11-10: a Super Bowl win AND (drive >= 8 or ego >= 8). The plan's other reading (either alone) is out.
    assert godfather_candidate_ok({"super_bowl_wins": 1, "drive": 9})
    assert godfather_candidate_ok({"champion": True, "ego": 9})
    assert not godfather_candidate_ok({"super_bowl_wins": 1})              # a ring alone
    assert not godfather_candidate_ok({"drive": 9})                        # drive alone
    assert not godfather_candidate_ok({"ego": 9})
    assert godfather_candidate_ok({"super_bowl_wins": 1, "drive": 8})     # 8 counts (JR: 8 or above)
    assert not godfather_candidate_ok({"super_bowl_wins": 0, "drive": 7, "ego": 7})
    assert not godfather_candidate_ok(None)


def test_full_control_acceptance_populates_coach_id_and_syncs_slants():
    acc = godfather_acceptance(SEASON, team_id=5, coach_id=99, coach_name="Marcus Whitfield",
                               salary=19_000_000, years=5, slant="OFFENSIVE",
                               trigger_reason="dual HC+GM vacancy")
    # The full-control staff contract, the coach_id link and the history row are all present.
    assert acc["staff_contract"]["person_kind"] == "HC"
    assert acc["staff_contract"]["person_id"] == 99
    assert acc["staff_contract"]["full_control"] is True
    assert acc["staff_contract"]["remaining_years"] == 5
    assert acc["gm_profile"]["coach_id"] == 99
    assert acc["gm_profile"]["gm_name"] == "Marcus Whitfield"
    assert acc["full_control_history"]["coach_id"] == 99

    # The front-office slant sync: an OFFENSIVE coach-GM leans offensive groups, a DEFENSIVE one defensive.
    assert set(gm_slant_bias("OFFENSIVE")) == {"QB", "WR", "OT", "RB", "TE", "IOL"}
    assert set(gm_slant_bias("DEFENSIVE")) == {"EDGE", "DT", "CB", "LB", "S"}
    assert gm_slant_bias("CEO") == {}
    assert acc["gm_profile"]["position_bias"] == gm_slant_bias("OFFENSIVE")


# --------------------------------------------------------------- GM hiring search (gm_rollover) ----
def test_gm_search_picks_the_best_affordable_candidate():
    pool = gm_candidate_pool("CAR", SEASON)
    assert {c["tier"] for c in pool} == {"ELITE", "STANDARD", "PROMOTION"}
    assert [c["tier"] for c in sorted(pool, key=lambda c: -c["stature"])] == ["ELITE", "STANDARD", "PROMOTION"]

    rich = gm_search(pool, appeal=0.6, budget=50_000_000)     # can afford anyone and the job appeals: ELITE wins
    assert rich["tier"] == "ELITE"

    middling = gm_search(pool, appeal=0.4, budget=50_000_000)  # ELITE refuses the opening; STANDARD and PROMOTION accept
    assert middling["tier"] == "STANDARD"
    assert any(d["tier"] == "ELITE" and d["reason"] == "appeal" for d in middling["declined"])

    poor = gm_search(pool, appeal=0.3, budget=1_000_000)      # nobody affordable: the cheapest willing candidate wins
    assert poor["tier"] == "PROMOTION"
    assert poor["salary"] == 2_750_000
    assert poor["salary"] == min(c["salary"] for c in pool)


def test_the_stature_boundary_is_eight():
    """M-0119 N5: drive 7 with a ring fails, drive 8 passes."""
    assert not godfather_candidate_ok({"super_bowl_wins": 1, "drive": 7, "ego": 7})
    assert godfather_candidate_ok({"super_bowl_wins": 1, "drive": 8})
    assert godfather_candidate_ok({"champion": True, "ego": 8})
