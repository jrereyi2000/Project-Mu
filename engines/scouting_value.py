"""
Scouting: each prospect's hidden true value V and the public grade scale (docs/team_management/04_SCOUTING_AND_DRAFT_PLAN.md
§2.1; PROJECT_SPEC §2.18). Pure: no DB.

    V = OVR + PV[position] + AGE[age]          (OVR points; a blank OVR counts as UNRATED_OVR)

PV is the additive form of the mock draft's POSITIONAL_PREMIUM, with LT/RT and the edge rushers split out. V has
no dev term (JR, K2: the prospect screens don't show dev) and no Madden board prior (Q1: kappa = 0; Madden's PICK
order seeds the week-4 consensus instead, engines/scouting_engine.py). EDIT DRAFT CLASS shows the true rating and
it never changes during the season (K1), so V is fixed for the class.

Sorting by V gives the true rank. V and the true rank are stored but never shown: not to writers, not on #debug.
The grade scale maps V (and the noisy consensus C = V + e) to 0-100 through the class's anchors: its #1 ~ 92,
#32 ~ 80, #64 ~ 74, #224 ~ 60, piecewise linear between them.
"""
import json
import os
import random
import re

UNRATED_OVR = 50.0

PV = {"QB": 5.0, "LEDG": 3.0, "REDG": 3.0, "LT": 3.0, "RT": 2.0, "CB": 2.5, "WR": 2.0, "DT": 1.0, "TE": 0.0,
      "LG": -1.0, "RG": -1.0, "C": -1.0, "MIKE": -2.0, "WILL": -2.0, "SAM": -2.0, "FS": -2.0, "SS": -2.0,
      "HB": -3.0, "FB": -5.0, "K": -12.0, "P": -12.0, "LS": -14.0}
AGE = {21: 1.5, 22: 0.5, 23: -0.5, 24: -1.5}
AGE_YOUNG, AGE_OLD = 2.0, -2.5                      # outside the board's 21-24

GRADE_ANCHORS = ((1, 92.0), (32, 80.0), (64, 74.0), (224, 60.0))

# Writers name the group in words.
POSITION_WORDS = {"QB": "quarterback", "HB": "running back", "FB": "fullback", "WR": "wide receiver",
                  "TE": "tight end", "LT": "left tackle", "RT": "right tackle", "LG": "guard", "RG": "guard",
                  "C": "center", "LEDG": "edge rusher", "REDG": "edge rusher", "DT": "defensive tackle",
                  "MIKE": "linebacker", "WILL": "linebacker", "SAM": "linebacker", "CB": "cornerback",
                  "FS": "safety", "SS": "safety", "K": "kicker", "P": "punter", "LS": "long snapper"}

CLASS_YEARS = {21: ("junior", "redshirt sophomore"), 22: ("senior", "redshirt junior"),
               23: ("senior", "redshirt senior"), 24: ("redshirt senior", "sixth-year senior")}

COLLEGES_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "scouting",
                             "colleges.json")


def name_key(name):
    return re.sub(r"[^A-Z]", "", (name or "").upper())


def group_of(position):
    """The mock draft's position group (OT for LT/RT, EDGE for LEDG/REDG, SPECIALISTS for K/P/LS)."""
    from engines.mock_draft_engine import POSITION_GROUP_MAP
    return POSITION_GROUP_MAP.get((position or "").upper(), (position or "").upper() or "UNK")


def prospect_key(draft_season, full_name, position):
    """Stable across Draft Board re-scans (draft_prospects ids change on every load): season, name, group."""
    return f"{int(draft_season)}:{name_key(full_name)}:{group_of(position)}"


def age_adj(age):
    if age is None:
        return 0.0
    a = int(age)
    return AGE.get(a, AGE_YOUNG if a < 21 else AGE_OLD)


def true_value(ovr, position, age):
    """(V, inputs). Inputs are stored in scout_prospects.value_inputs (hidden)."""
    o = float(ovr) if ovr is not None else UNRATED_OVR
    pv = PV.get((position or "").upper(), 0.0)
    aa = age_adj(age)
    return round(o + pv + aa, 2), {"ovr": ovr, "position": position, "group": group_of(position), "age": age,
                                   "pv": pv, "age_adj": aa, "unrated": ovr is None}


def true_ranks(values):
    """{key: rank} by V, best first; ties by key so the order is stable."""
    order = sorted(values, key=lambda k: (-values[k], k))
    return {k: i + 1 for i, k in enumerate(order)}


def grade_map(values):
    """The class's V -> grade map: [(v, grade)] knots, V descending, through GRADE_ANCHORS (a rank beyond the class
    takes its last V). Monotone; grade() interpolates and extrapolates with the end slopes."""
    vs = sorted(values, reverse=True)
    if not vs:
        return [(0.0, 0.0)]
    knots = []
    for rank, g in GRADE_ANCHORS:
        v = vs[min(rank, len(vs)) - 1]
        if knots and v >= knots[-1][0]:                 # ties (or a short class): keep the knots strictly falling
            v = knots[-1][0] - 0.01
        knots.append((v, g))
    return knots


def grade(v, knots):
    """0-100 grade for a value on the class's scale."""
    if len(knots) == 1:
        return max(0.0, min(100.0, knots[0][1]))
    if v >= knots[0][0]:
        (v0, g0), (v1, g1) = knots[0], knots[1]
    elif v <= knots[-1][0]:
        (v0, g0), (v1, g1) = knots[-2], knots[-1]
    else:
        i = next(i for i in range(len(knots) - 1) if knots[i][0] >= v >= knots[i + 1][0])
        (v0, g0), (v1, g1) = knots[i], knots[i + 1]
    g = g1 + (v - v1) * (g0 - g1) / (v0 - v1)
    return round(max(0.0, min(100.0, g)), 1)


def round_label(rank):
    if rank <= 224:
        n = (rank - 1) // 32 + 1
        return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n, 'th') } round"
    return "UDFA"


def round_tags(rank):
    tags = []
    if rank <= 10:
        tags.append("Top 10")
    if 97 <= rank <= 224:
        tags.append("Day 3")
    return tags


def class_year(age, seed):
    """A class year that fits the age (never a true freshman), seeded."""
    a = int(age) if age is not None else 22
    a = min(max(a, 21), 24)
    return random.Random(f"class:{seed}").choice(CLASS_YEARS[a])


_COLLEGES = None


def colleges():
    global _COLLEGES
    if _COLLEGES is None:
        with open(COLLEGES_PATH) as f:
            _COLLEGES = json.load(f)
    return _COLLEGES


def school(college):
    """{key, display, nickname, conference, level} for a board college (OCR aliases folded); None if unmapped."""
    c = colleges()
    k = (college or "").strip().upper()
    k = c["aliases"].get(k, k)
    s = c["schools"].get(k)
    return dict(s, key=k) if s else None


def conference_members(conference):
    return sorted(k for k, s in colleges()["schools"].items() if s["conference"] == conference)
