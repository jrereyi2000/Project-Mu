"""server/front_office_api.py: the NFLPA report-card endpoints (NFLPA_REPORT_CARD_PLAN.md §2.5, U3). The shaping and
validation tests are pure (no database); the endpoint test only reads the integration database (skipped when it's
down) and writes nothing."""
from decimal import Decimal

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from engines import report_card_engine as rce
from server import front_office_api as fo


def card(**kw):
    c = {"season": 2037, "weight_room": "C", "training_room": "C+", "locker_room": "C+", "food_nutrition": "C-",
         "team_travel": "B-", "family_treatment": "D+", "head_coach": "C+", "ownership": "C",
         "overall_score": Decimal("0.585"), "overall_grade": "C", "summary_text": "A league-average facility."}
    c.update(kw)
    return c


# ------------------------------------------------------------------ shape ----
def test_shape_report_card():
    shaped = fo.shape_report_card(card())
    assert set(shaped) == {"season", "weight_room", "training_room", "locker_room", "food_nutrition",
                           "team_travel", "family_treatment", "head_coach", "ownership",
                           "overall_score", "overall_grade", "summary_text"}
    assert shaped["weight_room"] == "C"
    assert shaped["overall_score"] == 0.585          # NUMERIC is normalized to a float
    assert isinstance(shaped["overall_score"], float)
    assert shaped["summary_text"] == "A league-average facility."


# ------------------------------------------------------------- rank / percentile ----
def test_rank_percentile_best_and_worst():
    assert fo.rank_percentile([0.5, 0.8, 0.3], 0.8) == (1, 100.0)
    assert fo.rank_percentile([0.5, 0.8, 0.3], 0.3) == (3, 33.3)


def test_rank_percentile_ties_share_the_top_rank():
    assert fo.rank_percentile([0.8, 0.8, 0.5], 0.8) == (1, 100.0)
    assert fo.rank_percentile([0.8, 0.5, 0.5], 0.5) == (2, 66.7)


def test_rank_percentile_empty():
    assert fo.rank_percentile([], 0.5) == (None, None)


# ------------------------------------------------------------- upgrades ----
def test_validate_upgrade_body():
    fo.validate_upgrade_body("weight_room", "MINOR")          # no exception
    with pytest.raises(HTTPException) as e:
        fo.validate_upgrade_body("not_a_category", "MINOR")
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        fo.validate_upgrade_body("weight_room", "GOLD_PLATED")
    assert e.value.status_code == 400


def test_plan_upgrade_cost_and_target_grade():
    c = card(weight_room="C")
    assert fo.plan_upgrade(c, "weight_room", "MINOR", 2037) == {
        "category": "weight_room", "cost_dollars": 5_000_000, "target_grade": "C+",
        "completion_season": 2038, "status": "IN_PROGRESS"}
    assert fo.plan_upgrade(c, "weight_room", "STANDARD", 2037)["target_grade"] == "B"
    assert fo.plan_upgrade(c, "weight_room", "OVERHAUL", 2037)["target_grade"] == "A+"
    assert fo.plan_upgrade(c, "weight_room", "OVERHAUL", 2037)["cost_dollars"] == 25_000_000


def test_check_cash():
    fo.check_cash(10_000_000, 5_000_000)              # exact funding is fine
    with pytest.raises(HTTPException) as e:
        fo.check_cash(4_999_999, 5_000_000)
    assert e.value.status_code == 409


# ------------------------------------------------------------- policies ----
def test_validate_policy():
    fo.validate_policy("luxury_travel")                # no exception
    with pytest.raises(HTTPException) as e:
        fo.validate_policy("gold_plated_jet")
    assert e.value.status_code == 400


def test_policy_effect_enable_and_disable():
    toggles = {"luxury_travel": False, "family_daycare": False, "private_chef": False}
    next_toggles, cost, updated = fo.policy_effect(toggles, card(team_travel="D"), "luxury_travel", True)
    assert next_toggles == {"luxury_travel": True, "family_daycare": False, "private_chef": False}
    assert cost == 3_500_000
    assert updated["team_travel"] == "A"               # immediate raise to target

    off = {"luxury_travel": True, "family_daycare": False, "private_chef": False}
    next_toggles, cost, updated = fo.policy_effect(off, card(team_travel="A"), "luxury_travel", False)
    assert next_toggles["luxury_travel"] is False
    assert cost == 0
    assert updated["team_travel"] == "A"               # a cut never lowers the current card


def test_policy_effect_sums_all_enabled_policies():
    toggles = {"luxury_travel": True, "family_daycare": False, "private_chef": False}
    next_toggles, cost, updated = fo.policy_effect(toggles, card(food_nutrition="D"), "private_chef", True)
    assert cost == 3_500_000 + 2_000_000
    assert updated["food_nutrition"] == "A"


# ------------------------------------------------------------- endpoints ----
@pytest.mark.integration
class EndpointTest:
    def setup_method(self):
        app = FastAPI()
        app.include_router(fo.router)
        self.client = TestClient(app)

    def test_report_card_payload(self):
        r = self.client.get("/api/team/car/report-card")
        assert r.status_code == 200
        data = r.json()
        assert data["team"]["abbreviation"] == "CAR"
        if data["cards"]:
            assert data["current"] is not None
            for c in data["cards"]:
                assert set(c) == set(fo.shape_report_card(card(season=0)))
                for cat in rce.CATEGORIES:
                    assert c[cat] in rce.GRADES
            if data["rank"]:
                rank = data["rank"]
                assert 1 <= rank["rank"] <= rank["teams"]
                assert 0.0 <= rank["percentile"] <= 100.0
        if data["policies"]:
            assert set(data["policies"]) == {"luxury_travel", "family_daycare", "private_chef", "annual_opex_cost"}
        if data["upgrades"]:
            for u in data["upgrades"]:
                assert u["status"] == "IN_PROGRESS"

    def test_alias_and_unknown_team(self):
        assert self.client.get("/api/team/ARI/report-card").json()["team"]["abbreviation"] == "AZ"
        assert self.client.get("/api/team/XYZ/report-card").status_code == 404
