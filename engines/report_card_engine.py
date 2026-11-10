"""
NFLPA team report cards (docs/done/NFLPA_REPORT_CARD_PLAN.md §2.1-2.4, U1).

Pure functions over plain dicts, money in whole dollars, no DB access and no randomness; the baseline grades are
a committed JSON config (engines/report_card_baseline.json). The engine owns the data layer and arithmetic only:
grade translation, the composite facility score, the upgrade/policy effects, wear-and-tear degradation and the
owner cash pool. Free-agency integration (§2.2) and CPU owner auto-upgrades (§2.3e, engines/owner_policy.py) are
other units' work.

Grades (§2.1). A 14-grade ordered scale, high to low, each mapped to a representative float inside its §2.1 band
(A 0.90-1.00, B 0.70-0.89, C 0.50-0.69, D 0.30-0.49, F 0.00-0.29). The concrete table is:

    A+ 1.00   A 0.95   A- 0.90
    B+ 0.85   B 0.80   B- 0.73
    C+ 0.65   C 0.58   C- 0.51
    D+ 0.45   D 0.38   D- 0.31
    F  0.18   F- 0.00

grade_to_score is monotonic (strictly decreasing down the list). score_to_grade returns the grade whose
representative score is nearest (ties go to the higher grade), so the round trip score_to_grade(grade_to_score(g))
is g.

Facility score (§2.2). S_facility = sum over the eight categories of w_k * grade_to_score(g_k), weights
weight_room .15, training_room .15, locker_room .15, food_nutrition .10, team_travel .15, family_treatment .10,
head_coach .10, ownership .10 (they sum to 1.0). The free-agency S_org blend (0.70 contention + 0.30 facility)
and the personality sensitivities live in engines/free_agency_engine.py (U2).

Upgrades (§2.3a/b). CapEx tiers (1-season construction delay; a "sub-tier" is one notch in the 14-grade list, a
"full tier" three notches):

    MINOR      $5M   +1 sub-tier     (C- -> C)
    STANDARD  $12M   +1 full tier    (C -> B)
    OVERHAUL  $25M   -> A+

OpEx annual policies, immediate grade effect (the category is raised to the target, never lowered):

    luxury_travel   $3.5M/yr   team_travel -> A
    family_daycare  $1.0M/yr   family_treatment -> A
    private_chef    $2.0M/yr   food_nutrition -> A

Degradation (D1, §2.3d). Physical facilities drop 1 sub-tier every 3 unmaintained seasons: degrade(card, seasons)
lowers each CapEx category (weight_room, training_room, locker_room, food_nutrition) by seasons // 3 notches,
floored at F-.

Owner cash pool (§2.3). base_budget(wealth_band) returns one concrete annual value per band (chosen as the band
midpoint, documented here):

    TIER_1  $45,000,000   (band $40-50M)
    TIER_2  $33,000,000   (band $28-38M)
    TIER_3  $22,000,000   (band $18-26M)
    TIER_4  $13,000,000   (band $10-16M)

success_dividend adds +$300k per win above 8, +$2.5M per home playoff game, +$2.5M for a Super Bowl appearance
(or +$5M for the championship, which subsumes the appearance), and +$2M for a top-5 popularity franchise, hard
capped at +$10M. annual_cash_pool = base_budget(wealth_band) + success_dividend(...).

Every constant here is an estimate except the §2.2 weights and the §2.1 band edges, which the plan fixes.
"""
from __future__ import annotations

import json
from pathlib import Path

# ------------------------------------------------------------- grade scale ----
GRADES = ("A+", "A", "A-", "B+", "B", "B-", "C+", "C", "C-", "D+", "D", "D-", "F", "F-")

GRADE_SCORES = {
    "A+": 1.00, "A": 0.95, "A-": 0.90,
    "B+": 0.85, "B": 0.80, "B-": 0.73,
    "C+": 0.65, "C": 0.58, "C-": 0.51,
    "D+": 0.45, "D": 0.38, "D-": 0.31,
    "F": 0.18, "F-": 0.00,
}

# §2.1 bands: {grade-prefix: (low, high)} inclusive.
GRADE_BANDS = {"A": (0.90, 1.00), "B": (0.70, 0.89), "C": (0.50, 0.69),
               "D": (0.30, 0.49), "F": (0.00, 0.29)}

# ------------------------------------------------------------- categories ---
CATEGORIES = ("weight_room", "training_room", "locker_room", "food_nutrition",
              "team_travel", "family_treatment", "head_coach", "ownership")

# §2.2 baseline weights (sum to 1.0).
FACILITY_WEIGHTS = {
    "weight_room": 0.15,
    "training_room": 0.15,
    "locker_room": 0.15,
    "food_nutrition": 0.10,
    "team_travel": 0.15,
    "family_treatment": 0.10,
    "head_coach": 0.10,
    "ownership": 0.10,
}

# §2.3d: physical facilities wear down without capital maintenance.
CAPEX_CATEGORIES = ("weight_room", "training_room", "locker_room", "food_nutrition")

# ------------------------------------------------------------- upgrades -----
CONSTRUCTION_DELAY_SEASONS = 1

CAPEX_TIERS = ("MINOR", "STANDARD", "OVERHAUL")
CAPEX_COSTS = {"MINOR": 5_000_000, "STANDARD": 12_000_000, "OVERHAUL": 25_000_000}
# Grade effect per tier, in notches up the GRADES list (a sub-tier = 1, a full tier = 3); OVERHAUL is special.
CAPEX_NOTCHES = {"MINOR": 1, "STANDARD": 3, "OVERHAUL": None}
OVERHAUL_GRADE = "A+"

# §2.3b OpEx annual policies and their immediate grade effects (raised to target, never lowered).
OPEX_POLICIES = {
    "luxury_travel": {"cost": 3_500_000, "category": "team_travel", "target_grade": "A"},
    "family_daycare": {"cost": 1_000_000, "category": "family_treatment", "target_grade": "A"},
    "private_chef": {"cost": 2_000_000, "category": "food_nutrition", "target_grade": "A"},
}

# ------------------------------------------------------------- cash pool ----
BASE_BUDGETS = {"TIER_1": 45_000_000, "TIER_2": 33_000_000, "TIER_3": 22_000_000, "TIER_4": 13_000_000}

WINS_DIVIDEND = 300_000            # per win above 8
WINS_DIVIDEND_BASE = 8
HOME_PLAYOFF_GAME_DIVIDEND = 2_500_000
SB_APPEARANCE_DIVIDEND = 2_500_000
SB_CHAMPIONSHIP_DIVIDEND = 5_000_000   # subsumes the appearance (a champion passes sb_championship, not appearance)
TOP5_POPULARITY_DIVIDEND = 2_000_000
SUCCESS_DIVIDEND_CAP = 10_000_000


def _band_of(grade):
    """The §2.1 band prefix ('A'..'F') for a grade letter."""
    return grade[0]


def grade_to_score(grade):
    """The representative float in [0, 1] for a letter grade (KeyError on an unknown grade)."""
    return GRADE_SCORES[grade]


def score_to_grade(score):
    """The grade whose representative score is nearest to `score` (ties go to the higher grade)."""
    score = max(0.0, min(1.0, score))
    return min(GRADES, key=lambda g: (abs(GRADE_SCORES[g] - score), -GRADE_SCORES[g]))


def facility_score(card):
    """S_facility (§2.2): the weighted composite of a card's eight category grades, rounded to 4 decimals."""
    return round(sum(FACILITY_WEIGHTS[k] * grade_to_score(card[k]) for k in CATEGORIES), 4)


def load_baseline():
    """The 32-team baseline report cards from engines/report_card_baseline.json, keyed by team abbreviation."""
    path = Path(__file__).with_name("report_card_baseline.json")
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


# ------------------------------------------------------------- upgrades -----
def _bump(grade, notches):
    """Move a grade `notches` steps up the GRADES list (positive = toward A+, negative = toward F-), clamped."""
    idx = GRADES.index(grade)
    return GRADES[max(0, min(len(GRADES) - 1, idx - notches))]


def upgrade_cost(tier):
    """The whole-dollar cost of a CapEx tier (KeyError on an unknown tier)."""
    return CAPEX_COSTS[tier]


def upgrade_target_grade(current_grade, tier):
    """The grade a category reaches once a CapEx project completes (MINOR +1 sub-tier, STANDARD +1 full tier,
    OVERHAUL -> A+)."""
    if tier == "OVERHAUL":
        return OVERHAUL_GRADE
    return _bump(current_grade, CAPEX_NOTCHES[tier])


def apply_upgrade(card, category, tier):
    """A copy of `card` with `category` set to the tier's completed grade (the grade effect, applied on
    construction completion)."""
    out = dict(card)
    out[category] = upgrade_target_grade(card.get(category), tier)
    return out


def order_upgrade(card, category, tier, season, team_abbr=None):
    """The facility_upgrades row for a CapEx project ordered in `season`: cost, target grade, a 1-season
    construction delay (completion_season = season + 1) and status IN_PROGRESS."""
    return {
        "team_abbr": team_abbr,
        "category": category,
        "tier": tier,
        "cost_dollars": upgrade_cost(tier),
        "target_grade": upgrade_target_grade(card.get(category), tier),
        "season_ordered": season,
        "completion_season": season + CONSTRUCTION_DELAY_SEASONS,
        "status": "IN_PROGRESS",
    }


def advance_upgrade(upgrade, current_season):
    """A copy of the upgrade dict with status COMPLETED once current_season reaches completion_season
    (IN_PROGRESS -> COMPLETED across season rollovers); otherwise unchanged. CANCELLED is left alone."""
    out = dict(upgrade)
    if out["status"] == "IN_PROGRESS" and current_season >= out["completion_season"]:
        out["status"] = "COMPLETED"
    return out


def opex_cost(policy):
    """The annual whole-dollar cost of an OpEx policy (KeyError on an unknown policy)."""
    return OPEX_POLICIES[policy]["cost"]


def apply_policy(card, policy):
    """A copy of `card` with the policy's immediate grade effect applied (§2.3b): the target category is raised
    to its target grade, never lowered."""
    spec = OPEX_POLICIES[policy]
    out = dict(card)
    current = out.get(spec["category"])
    if grade_to_score(current) < grade_to_score(spec["target_grade"]):
        out[spec["category"]] = spec["target_grade"]
    return out


def degrade(card, seasons, categories=CAPEX_CATEGORIES):
    """A copy of `card` with each physical category dropped 1 sub-tier per 3 unmaintained seasons (D1/§2.3d),
    floored at F-. `seasons` below 3 changes nothing."""
    notches = seasons // 3
    if notches <= 0:
        return dict(card)
    out = dict(card)
    for cat in categories:
        if cat in out:
            out[cat] = _bump(out[cat], -notches)
    return out


# ------------------------------------------------------------- cash pool ----
def base_budget(wealth_band):
    """The concrete annual base cash pool for a wealth band (module docstring). KeyError on an unknown band."""
    return BASE_BUDGETS[wealth_band]


def success_dividend(wins=0, home_playoff_games=0, sb_appearance=False, sb_championship=False,
                     top5_popularity=False):
    """The annual success dividend, hard-capped at SUCCESS_DIVIDEND_CAP: +$300k per win above 8, +$2.5M per home
    playoff game, +$2.5M for a Super Bowl appearance (or +$5M for the championship, which subsumes it), +$2M for a
    top-5 popularity franchise."""
    div = WINS_DIVIDEND * max(0, wins - WINS_DIVIDEND_BASE)
    div += HOME_PLAYOFF_GAME_DIVIDEND * home_playoff_games
    if sb_championship:
        div += SB_CHAMPIONSHIP_DIVIDEND
    elif sb_appearance:
        div += SB_APPEARANCE_DIVIDEND
    if top5_popularity:
        div += TOP5_POPULARITY_DIVIDEND
    return min(SUCCESS_DIVIDEND_CAP, div)


def annual_cash_pool(wealth_band, wins=0, home_playoff_games=0, sb_appearance=False, sb_championship=False,
                     top5_popularity=False):
    """B_base + success dividend for one season."""
    return base_budget(wealth_band) + success_dividend(wins, home_playoff_games, sb_appearance, sb_championship,
                                                       top5_popularity)
