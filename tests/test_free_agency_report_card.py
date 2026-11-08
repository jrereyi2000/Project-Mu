"""Tests for U2: the free-agency facility modifier and the NFLPA report card article (NFLPA_REPORT_CARD_PLAN.md
§2.2/§5 U2). Synthetic dicts only, no database."""
import math

import pytest

from engines.free_agency_engine import W_DEFAULT, contention_score, offer_utility
from newsroom.report_card_article import ARTICLE_TYPE, CATEGORY_LABELS, build_article

MARKET = {"b": 0.09, "a_league": math.log(3_000_000), "a": {"QB": math.log(6_000_000)}, "n": {}}
NEUTRAL_TEAM = {"team_id": 1, "win_pct": 0.5, "playoff_odds": 0.5, "popularity_index": 50}


def player(pid=1, group="WR", ovr=85, age=27, accrued=5):
    return {"id": pid, "group": group, "ovr": ovr, "age": age, "accrued_seasons": accrued}


def offer(p, team_id=1, mult=1.0, years=2, guar_share=0.4, depth=1):
    from engines.trade_value import worth
    total = int(round(worth(p["ovr"], p["group"], MARKET) * mult * years))
    return {"team_id": team_id, "player_id": p["id"], "years": years, "total_value": total,
            "bonus": int(total * 0.1), "guaranteed": int(total * guar_share), "projected_depth": depth}


# ------------------------------------------------- facility modifier (S_org) ----
def test_absent_facility_score_is_byte_for_byte_unchanged():
    p = player()
    o = offer(p)
    u = offer_utility(o, p, NEUTRAL_TEAM, MARKET)
    assert set(u) == {"utility", "financial", "opportunity", "contention", "scheme", "persona"}
    assert "facility" not in u
    expected = (W_DEFAULT["money"] * u["financial"] + W_DEFAULT["role"] * u["opportunity"]
                + W_DEFAULT["team"] * u["contention"] + W_DEFAULT["scheme"] * u["scheme"])
    assert u["utility"] == pytest.approx(expected, abs=1e-3)


def test_facility_score_blends_into_the_team_term_and_adds_the_key():
    p = player()
    o = offer(p)
    team = dict(NEUTRAL_TEAM, facility_score=1.0)
    u = offer_utility(o, p, team, MARKET)
    con = contention_score(NEUTRAL_TEAM)
    s_org = 0.70 * con + 0.30 * 1.0
    expected = (W_DEFAULT["money"] * u["financial"] + W_DEFAULT["role"] * u["opportunity"]
                + W_DEFAULT["team"] * s_org + W_DEFAULT["scheme"] * u["scheme"])
    assert u["utility"] == pytest.approx(expected, abs=1e-3)
    assert u["facility"] == pytest.approx(1.0)
    assert u["contention"] == pytest.approx(con, abs=1e-4)   # contention stays the raw S_contention


def test_top_facility_discount_and_bottom_facility_tax():
    p = player()
    o = offer(p)
    base = offer_utility(o, p, NEUTRAL_TEAM, MARKET)
    top = offer_utility(o, p, dict(NEUTRAL_TEAM, facility_score=1.0), MARKET)
    bottom = offer_utility(o, p, dict(NEUTRAL_TEAM, facility_score=0.0), MARKET)
    assert top["utility"] > base["utility"] > bottom["utility"]


def test_facility_delta_matches_the_blend_weight():
    p = player()
    o = offer(p)
    con = contention_score(NEUTRAL_TEAM)
    base = offer_utility(o, p, NEUTRAL_TEAM, MARKET)["utility"]
    for fac in (1.0, 0.0, 0.8):
        with_fac = offer_utility(o, p, dict(NEUTRAL_TEAM, facility_score=fac), MARKET)["utility"]
        assert with_fac - base == pytest.approx(W_DEFAULT["team"] * 0.30 * (fac - con), abs=1e-3)


# -------------------------------------------------------------------- article ----
def card(team="MIN", team_name="Minnesota Vikings", overall_score=0.915, overall_grade="A", summary=""):
    return {"season": 2036, "team_id": 1, "team": team, "team_name": team_name,
            "weight_room": "B", "training_room": "B", "locker_room": "B", "food_nutrition": "B",
            "team_travel": "B", "family_treatment": "B", "head_coach": "B", "ownership": "B",
            "overall_score": overall_score, "overall_grade": overall_grade,
            "summary_text": summary, "last_renovated_season": None}


def test_build_article_empty_is_none():
    assert build_article(2036, 26, []) is None


def test_build_article_is_deterministic_and_ranks_descending():
    rows = [card("AZ", "Arizona Cardinals", 0.312, "F"),
            card("MIN", "Minnesota Vikings", 0.915, "A"),
            card("CAR", "Carolina Panthers", 0.500, "C")]
    a = build_article(2036, 26, rows)
    b = build_article(2036, 26, list(reversed(rows)))
    assert a is not None and a == b
    assert a["article_type"] == "NFLPA_REPORT_CARD"

    ranking = a["content"].split("## The Full Ranking")[1].split("## The Best and the Worst")[0]
    order = [ranking.index(name) for name in ("Minnesota Vikings", "Carolina Panthers", "Arizona Cardinals")]
    assert order == sorted(order)
    for i in (1, 2, 3):
        assert f"**{i}. " in ranking


def test_build_article_uses_only_db_figures():
    rows = [card("MIN", "Minnesota Vikings", 0.915, "A", summary="Top of the class.")]
    a = build_article(2036, 26, rows)
    content = a["content"]
    assert "Minnesota Vikings" in content
    assert "**A**" in content          # overall grade
    assert "0.915" in content          # overall score
    assert "Top of the class." in content
    for col, label in CATEGORY_LABELS:
        assert f"{label} {rows[0][col]}" in content
    assert a["headline"].startswith("The 2036 NFLPA Team Report Cards")


def test_build_article_ties_break_on_abbreviation():
    rows = [card("B", "Team Bravo", 0.500, "C"),
            card("A", "Team Alpha", 0.500, "C")]
    a = build_article(2036, 26, rows)
    ranking = a["content"].split("## The Full Ranking")[1].split("## The Best and the Worst")[0]
    assert ranking.index("Team Alpha") < ranking.index("Team Bravo")
