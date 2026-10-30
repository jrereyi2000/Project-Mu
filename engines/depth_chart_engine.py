"""
Depth chart engine, "Let Staff Set" (docs/in_progress/V2_CORE_BUILD_PLAN.md §2.4, V2_MASTER_PLAN.md §2.2).

Builds a team's full Madden depth chart, all 35 `Team.DepthChart` positions, each up to 6 players best first
(pipeline/save_reader/release.mjs, depth_chart.mjs), from a roster, the staff's schemes and the injury list.

  staff_depth_chart(roster, scheme, injuries, coach=None) -> {slot: [player_id, ...]}   pure, no DB
  store(cur, team_id, season, week, chart, source)                                       depth_charts rows
  load(cur, team_id, season, week, source) -> {slot: [player_id, ...]}

How a slot is scored for a player:
  * Eligibility: each slot lists position tiers with an out-of-position penalty (LT from LT/OT, then RT, then
    G, then C; slot CB from CB then S; KR/PR from HB/WR/CB; LS from LS then TE/C; ...). Players outside every
    tier never appear, unless the slot would otherwise have no starter (then anyone healthy, penalty 25).
  * OVR at the slot: a player in the slot's first tier keeps his stored `overall` (Madden's OVR), except on the
    archetype slots below.
    Anyone else is graded with engines/ovr_weights.py's fitted Madden 27 formulas for the slot's archetypes
    (PWHB = HB_PowerBack, SLWR = WR_Slot, NT = DT_NoseTackle, SLCB = CB_Slot, SUBLB = coverage linebackers,
    KOS = KP_Power, otherwise every archetype of the slot's position, the best one counts) when his ratings
    cover the formula's terms, else his `overall`. KR, PR and GAD use rating blends; 3DRB (catching and pass
    blocking) and the rush slots RLE/RRE/RDT (pass-rush moves) blend OVR 50/50 with a rating blend.
  * Scheme fit: +SCHEME_FIT_BONUS when the player's archetype suits the offensive or defensive scheme
    (SCHEME_ARCHETYPES; e.g. Wide Zone West Coast likes agile linemen, 3-4 likes nose tackles).
  * Coach trust (youth vs veterans): computed from coach["youth_trust"] / coach["veteran_trust"] but scaled by
    COACH_TRUST_SCALE = 0.0 (plan D4: coach traits ship at 0), so it never changes a chart yet.
  Ties: higher overall, then higher stamina, then lower player_id.

Starting conflicts: the base positions (BASE_SLOTS) take their starters (WR 3, CB 2, DT 2 in a 4-3 or 1 in a
3-4, MLB 1 in a 4-3 or 2 in a 3-4, else 1) in one greedy pass over every (player, base slot) pair, best score
first, so a player starts at one base position only. Backups (depth after the starters) and the situational slots
(3DRB, PWHB, GAD, SLWR, NT, RLE, RRE, RDT, SUBLB, SLCB, KOS, KR, PR) may reuse anyone eligible, so one player can
sit on several slots. Injured players (injuries[pid]["out"]) never appear.
"""

from engines import ovr_weights

# Madden's 35 DepthChart field names, in the order the DepthChart record stores them (read from
# CAREER-GIANTS-AUTOSAVE; LockedEntries, the 36th field, is not a position).
POSITION_SLOTS = [
    "3DRB", "C", "CB", "DT", "FB", "FS", "GAD", "HB", "K", "KOS", "KR", "LE", "LG", "LOLB", "LS", "LT", "MLB",
    "NT", "P", "PR", "PWHB", "QB", "RDT", "RE", "RG", "RLE", "ROLB", "RRE", "RT", "SLCB", "SLWR", "SS", "SUBLB",
    "TE", "WR",
]

DEPTH = 6
SOURCES = ("MADDEN", "MU_COACH", "JR")

BASE_SLOTS = [
    "QB", "HB", "FB", "WR", "TE", "LT", "LG", "C", "RG", "RT",
    "LE", "RE", "DT", "LOLB", "MLB", "ROLB", "CB", "FS", "SS",
    "K", "P", "LS",
]
SITUATIONAL_SLOTS = [s for s in POSITION_SLOTS if s not in BASE_SLOTS]

# Position groups (Madden positions plus generic names a roster may use).
OT = ("LT", "RT", "OT")
G = ("LG", "RG", "G", "OG")
DE = ("LE", "RE", "DE", "EDGE")
OLB = ("LOLB", "ROLB", "OLB")
ILB = ("MLB", "ILB", "LB")
S = ("FS", "SS", "S")

FALLBACK_PENALTY = 25.0

# slot -> [(positions, out-of-position penalty), ...], best tier first.
ELIGIBLE = {
    "QB": [(("QB",), 0)],
    "HB": [(("HB", "RB"), 0), (("FB",), 6), (("WR",), 10)],
    "PWHB": [(("HB", "RB"), 0), (("FB",), 2)],
    "3DRB": [(("HB", "RB"), 0), (("FB",), 6), (("WR",), 8)],
    "FB": [(("FB",), 0), (("HB", "RB"), 6), (("TE",), 6)],
    "GAD": [(("WR", "HB", "RB"), 0), (("QB", "TE", "CB"), 6)],
    "WR": [(("WR",), 0), (("TE",), 8), (("HB", "RB"), 10)],
    "SLWR": [(("WR",), 0), (("HB", "RB", "TE"), 8)],
    "TE": [(("TE",), 0), (("FB",), 6), (OT + G, 12)],
    "LT": [(("LT", "OT"), 0), (("RT",), 1), (G, 5), (("C",), 8)],
    "RT": [(("RT", "OT"), 0), (("LT",), 1), (G, 5), (("C",), 8)],
    "LG": [(("LG", "G", "OG"), 0), (("RG",), 1), (("C",), 3), (OT, 4)],
    "RG": [(("RG", "G", "OG"), 0), (("LG",), 1), (("C",), 3), (OT, 4)],
    "C": [(("C",), 0), (G, 3), (OT, 6)],
    "LE": [(("LE", "DE", "EDGE"), 0), (("RE",), 1), (("DT",) + OLB, 5)],
    "RE": [(("RE", "DE", "EDGE"), 0), (("LE",), 1), (("DT",) + OLB, 5)],
    "RLE": [(DE + OLB, 0), (("DT",), 4)],
    "RRE": [(DE + OLB, 0), (("DT",), 4)],
    "DT": [(("DT", "NT"), 0), (DE, 4)],
    "NT": [(("DT", "NT"), 0), (DE, 8)],
    "RDT": [(("DT", "NT"), 0), (DE, 2)],
    "LOLB": [(("LOLB", "OLB"), 0), (("ROLB",), 1), (ILB, 4), (DE, 6)],
    "ROLB": [(("ROLB", "OLB"), 0), (("LOLB",), 1), (ILB, 4), (DE, 6)],
    "MLB": [(ILB, 0), (OLB, 4), (S, 10)],
    "SUBLB": [(ILB + OLB, 0), (S, 5)],
    "CB": [(("CB",), 0), (S, 6)],
    "SLCB": [(("CB",), 0), (S, 4)],
    "FS": [(("FS", "S"), 0), (("SS",), 1), (("CB",), 5)],
    "SS": [(("SS", "S"), 0), (("FS",), 1), (("CB",), 6), (ILB + OLB, 8)],
    "K": [(("K",), 0), (("P",), 10)],
    "P": [(("P",), 0), (("K",), 10)],
    "KOS": [(("K",), 0), (("P",), 4)],
    "LS": [(("LS",), 0), (("TE", "C"), 10), (G + ("FB",), 14)],
    "KR": [(("HB", "RB", "WR", "CB"), 0), (S + ("FB", "TE"), 8)],
    "PR": [(("WR", "CB", "HB", "RB"), 0), (S, 8)],
}

# slot -> the ovr_weights archetypes it is graded on (the best one counts); slots not listed use every archetype
# of GRADE_POSITION[slot] (default: the slot itself).
SLOT_ARCHETYPES = {
    "PWHB": ["HB_PowerBack"],
    "SLWR": ["WR_Slot"],
    "NT": ["DT_NoseTackle"],
    "SLCB": ["CB_Slot"],
    "SUBLB": ["MLB_PassCoverage", "OLB_PassCoverage"],
    "KOS": ["KP_Power"],
}
GRADE_POSITION = {"3DRB": "HB", "RLE": "LE", "RRE": "RE", "RDT": "DT"}

# Rating blends (weights) for the situational roles; BLEND_SHARE of the score comes from the blend, the rest from OVR.
BLENDS = {
    "KR": {"KickReturn": 3, "Speed": 3, "Acceleration": 2, "Agility": 1, "BCVision": 1, "Carrying": 1},
    "PR": {"KickReturn": 3, "Speed": 2, "Acceleration": 2, "Agility": 2, "ChangeOfDirection": 1, "Carrying": 2},
    "GAD": {"Speed": 3, "Acceleration": 2, "Agility": 2, "ChangeOfDirection": 1, "Catching": 1, "Carrying": 1,
            "JukeMove": 1},
    "3DRB": {"Catching": 3, "PassBlock": 3, "ShortRouteRunning": 2, "Awareness": 1, "ChangeOfDirection": 1},
    "RLE": {"FinesseMoves": 3, "PowerMoves": 3, "BlockShedding": 2, "Acceleration": 2, "Speed": 1, "Strength": 1},
    "RRE": {"FinesseMoves": 3, "PowerMoves": 3, "BlockShedding": 2, "Acceleration": 2, "Speed": 1, "Strength": 1},
    "RDT": {"PowerMoves": 3, "FinesseMoves": 2, "BlockShedding": 2, "Strength": 2, "Acceleration": 1},
}
BLEND_SHARE = {"KR": 1.0, "PR": 1.0, "GAD": 1.0, "3DRB": 0.5, "RLE": 0.5, "RRE": 0.5, "RDT": 0.5}

SCHEME_FIT_BONUS = 1.5
OFFENSE_SLOTS = {"QB", "HB", "PWHB", "3DRB", "FB", "GAD", "WR", "SLWR", "TE", "LT", "LG", "C", "RG", "RT"}
DEFENSE_SLOTS = {"LE", "RE", "RLE", "RRE", "DT", "NT", "RDT", "LOLB", "MLB", "ROLB", "SUBLB", "CB", "SLCB", "FS",
                 "SS"}
# Scheme name (lowercase substring) -> archetypes that fit it. The longest matching key wins.
SCHEME_ARCHETYPES = {
    "offense": {
        "wide zone west coast": {"HB_ElusiveBack", "OT_Agile", "G_Agile", "C_Agile", "TE_Possession",
                                 "WR_Playmaker", "QB_FieldGeneral", "FB_Utility"},
        "west coast": {"QB_FieldGeneral", "WR_Slot", "WR_Playmaker", "HB_ReceivingBack", "TE_Possession",
                       "OT_PassProtector", "G_PassProtector", "C_PassProtector"},
        "spread": {"QB_Scrambler", "QB_Improviser", "WR_DeepThreat", "WR_Slot", "HB_ReceivingBack",
                   "TE_VerticalThreat", "OT_Agile", "G_Agile"},
        "erhardt-perkins": {"QB_FieldGeneral", "WR_Physical", "TE_Possession", "HB_PowerBack",
                            "OT_PassProtector", "G_PassProtector", "C_PassProtector"},
        "air raid": {"QB_StrongArm", "WR_DeepThreat", "WR_Slot", "OT_PassProtector"},
        "vertical": {"QB_StrongArm", "WR_DeepThreat", "TE_VerticalThreat", "OT_PassProtector"},
        "power": {"HB_PowerBack", "FB_Blocking", "TE_Blocking", "OT_Power", "G_Power", "C_Power"},
        "run and shoot": {"WR_Slot", "WR_DeepThreat", "QB_Improviser"},
    },
    "defense": {
        "4-3": {"DE_PowerRusher", "DE_SmallerSpeedRusher", "DT_SpeedRusher", "DT_PowerRusher",
                "MLB_FieldGeneral", "OLB_RunStopper"},
        "3-4": {"DE_RunStopper", "DT_NoseTackle", "MLB_RunStopper", "OLB_PassCoverage"},
        "4-2-5": {"DE_SmallerSpeedRusher", "CB_Slot", "S_Hybrid", "MLB_PassCoverage"},
        "nickel": {"CB_Slot", "S_Hybrid", "MLB_PassCoverage"},
        "cover 2": {"CB_Zone", "S_Zone", "MLB_PassCoverage"},
        "cover 3": {"CB_Zone", "S_Zone", "S_RunSupport"},
    },
}
SCHEME_KEYS = {
    "offense": ("offense", "offensive_scheme", "effective_offense"),
    "defense": ("defense", "defensive_scheme", "defensive_front", "effective_defense"),
}
MAN_COVERAGE_ARCHETYPES = {"High": {"CB_MantoMan"}, "Low": {"CB_Zone", "S_Zone"}}

# Coach trust (plan D4: coach traits ship at 0). trust in [-1, 1]; the term is
# COACH_TRUST_SCALE * (youth_trust * youth(age) + veteran_trust * veteran(age)).
COACH_TRUST_SCALE = 0.0
YOUTH_AGE = 24
VETERAN_AGE = 30

_DEFAULT = object()
_WEIGHTS_CACHE = {}


def _weights(weights):
    if weights is not _DEFAULT:
        return weights
    if "doc" not in _WEIGHTS_CACHE:
        try:
            _WEIGHTS_CACHE["doc"] = ovr_weights.load_weights()
        except (OSError, ValueError):
            _WEIGHTS_CACHE["doc"] = None
    return _WEIGHTS_CACHE["doc"]


# ---------------------------------------------------------------- scoring (pure)

def is_out(player_id, injuries):
    """True when the injury list marks the player out (keys may be ints or strings)."""
    inj = (injuries or {}).get(player_id)
    if inj is None:
        inj = (injuries or {}).get(str(player_id))
    return bool(inj and inj.get("out"))


def eligibility_penalty(slot, position):
    """The out-of-position penalty for this position at this slot, or None when it is not eligible."""
    for positions, penalty in ELIGIBLE[slot]:
        if position in positions:
            return float(penalty)
    return None


def _primary(slot, position):
    return position in ELIGIBLE[slot][0][0]


def _slot_archetypes(slot, doc):
    if slot in SLOT_ARCHETYPES:
        return [a for a in SLOT_ARCHETYPES[slot] if a in doc.get("archetypes", {})]
    pos = GRADE_POSITION.get(slot, slot)
    return [a for a in doc.get("slots", {}).get(pos, []) if a and a in doc.get("archetypes", {})]


def slot_ovr(player, slot, weights=_DEFAULT):
    """The player's OVR at this slot (before penalties): stored overall in the slot's first tier (not on an archetype
    slot), else the
    best fitted ovr_weights grade over the slot's archetypes when his ratings cover the formula, else overall."""
    overall = float(player.get("overall") or 0)
    if slot not in SLOT_ARCHETYPES and _primary(slot, player.get("position")):
        return overall
    doc = _weights(weights)
    ratings = player.get("ratings") or {}
    if not doc or not ratings:
        return overall
    grades = []
    for arch in _slot_archetypes(slot, doc):
        terms = doc["archetypes"][arch]["terms"]
        if all(k in ratings for k, (w, q) in terms.items() if w or q):
            grades.append(ovr_weights.predict_grade(doc, arch, ratings, exact=True))
    if not grades:
        return overall
    best = max(grades)
    if _primary(slot, player.get("position")) and player.get("archetype") in SLOT_ARCHETYPES.get(slot, ()):
        best = max(best, overall)   # his own archetype is the slot's: Madden's stored OVR is that grade
    return max(0.0, min(99.0, best))


def blend_score(player, slot):
    """The slot's rating blend (0-99) over the ratings the player has, or None without any of them."""
    weights = BLENDS.get(slot)
    ratings = player.get("ratings") or {}
    if not weights:
        return None
    have = {k: w for k, w in weights.items() if k in ratings}
    if not have:
        return None
    return sum(w * float(ratings[k]) for k, w in have.items()) / sum(have.values())


def _scheme_name(scheme, side):
    for key in SCHEME_KEYS[side]:
        value = (scheme or {}).get(key)
        if value:
            return str(value).lower()
    return ""


def scheme_fit(player, slot, scheme):
    """+SCHEME_FIT_BONUS when the player's archetype suits the slot side's scheme, else 0."""
    arch = player.get("archetype")
    if not arch or not scheme:
        return 0.0
    side = "offense" if slot in OFFENSE_SLOTS else "defense" if slot in DEFENSE_SLOTS else None
    if side is None:
        return 0.0
    fits = set()
    name = _scheme_name(scheme, side)
    if name:
        match = max((k for k in SCHEME_ARCHETYPES[side] if k in name), key=len, default=None)
        if match:
            fits |= SCHEME_ARCHETYPES[side][match]
    if side == "defense":
        fits |= MAN_COVERAGE_ARCHETYPES.get((scheme or {}).get("man_coverage"), set())
    return SCHEME_FIT_BONUS if arch in fits else 0.0


def coach_trust(player, coach):
    """Youth vs veteran trust. Scaled by COACH_TRUST_SCALE, which is 0 under plan D4."""
    if not coach:
        return 0.0
    age = player.get("age")
    if age is None:
        return 0.0
    youth = max(0.0, (YOUTH_AGE - age) / 3.0)
    veteran = max(0.0, (age - VETERAN_AGE) / 3.0)
    return COACH_TRUST_SCALE * (float(coach.get("youth_trust") or 0) * youth
                                + float(coach.get("veteran_trust") or 0) * veteran)


def slot_score(player, slot, scheme=None, coach=None, weights=_DEFAULT, penalty=None):
    """The player's score at this slot, or None when he is not eligible (penalty overrides eligibility)."""
    if penalty is None:
        penalty = eligibility_penalty(slot, player.get("position"))
        if penalty is None:
            return None
    base = slot_ovr(player, slot, weights)
    blend = blend_score(player, slot)
    if blend is not None:
        share = BLEND_SHARE.get(slot, 0.5)
        base = share * blend + (1 - share) * base
    return base - penalty + scheme_fit(player, slot, scheme) + coach_trust(player, coach)


def _key(score, player):
    return (-score, -(player.get("overall") or 0), -(player.get("stamina") or 0), player["player_id"])


def starter_counts(scheme):
    """Starters per base slot: WR 3, CB 2; DT 2 and MLB 1 in a 4-3 (the default), DT 1 and MLB 2 in a 3-4."""
    counts = {s: 1 for s in BASE_SLOTS}
    counts.update({"WR": 3, "CB": 2, "DT": 2})
    if "3-4" in _scheme_name(scheme, "defense"):
        counts.update({"DT": 1, "MLB": 2})
    return counts


def _ranked(slot, players, scheme, coach, weights):
    out = []
    for p in players:
        s = slot_score(p, slot, scheme, coach, weights)
        if s is not None:
            out.append((s, p))
    out.sort(key=lambda sp: _key(*sp))
    return out


def staff_depth_chart(roster, scheme, injuries, coach=None, *, weights=_DEFAULT):
    """{slot: [player_id, ... up to 6]} best first for all 35 POSITION_SLOTS. Pure: no DB.

    roster: [{player_id, position, archetype, overall, ratings: {Rating: int}, age, stamina?}]
    scheme: {"offense": ..., "defense": ..., "man_coverage": ...} (coach_profile keys also accepted; may be empty)
    injuries: {player_id: {"out": bool, "weeks": int}}
    coach: optional {"youth_trust", "veteran_trust"}; the trust terms are 0 under plan D4.
    weights: an ovr_weights document (default: engines/ovr_weights_m27.json), None to grade by overall only."""
    scheme = scheme or {}
    healthy = [p for p in roster if not is_out(p["player_id"], injuries)]
    ranked = {slot: _ranked(slot, healthy, scheme, coach, weights) for slot in POSITION_SLOTS}

    # Base starters: one greedy pass over every (player, base slot) pair, best first; a player starts once.
    counts = starter_counts(scheme)
    pairs = sorted(((s, p, slot) for slot in BASE_SLOTS for s, p in ranked[slot]),
                   key=lambda t: _key(t[0], t[1]) + (BASE_SLOTS.index(t[2]),))
    starters = {slot: [] for slot in BASE_SLOTS}
    started = set()
    for s, p, slot in pairs:
        pid = p["player_id"]
        if pid in started or len(starters[slot]) >= counts[slot]:
            continue
        starters[slot].append((s, p))
        started.add(pid)
    # A base slot with no eligible starter left takes the best healthy player who starts nowhere.
    for slot in BASE_SLOTS:
        need = counts[slot] - len(starters[slot])
        if need <= 0:
            continue
        spare = [(slot_score(p, slot, scheme, coach, weights, penalty=FALLBACK_PENALTY), p)
                 for p in healthy if p["player_id"] not in started]
        spare.sort(key=lambda sp: _key(*sp))
        for s, p in spare[:need]:
            starters[slot].append((s, p))
            started.add(p["player_id"])

    chart = {}
    for slot in POSITION_SLOTS:
        if slot in starters:
            first = sorted(starters[slot], key=lambda sp: _key(*sp))
            ids = [p["player_id"] for _, p in first]
            ids += [p["player_id"] for _, p in ranked[slot] if p["player_id"] not in ids]
        else:
            ids = [p["player_id"] for _, p in ranked[slot]]
            if not ids and healthy:
                spare = sorted(((slot_score(p, slot, scheme, coach, weights, penalty=FALLBACK_PENALTY), p)
                                for p in healthy), key=lambda sp: _key(*sp))
                ids = [p["player_id"] for _, p in spare[:1]]
        chart[slot] = ids[:DEPTH]
    return chart


def starters_by_base_slot(chart, scheme=None):
    """{base slot: [starter player_ids]} from a chart (the first starter_counts(scheme)[slot] entries)."""
    counts = starter_counts(scheme or {})
    return {slot: list(chart.get(slot, []))[:counts[slot]] for slot in BASE_SLOTS}


# ---------------------------------------------------------------- storage (thin cursor wrappers)

DELETE_SQL = "DELETE FROM depth_charts WHERE team_id = %s AND season = %s AND week = %s AND source = %s"
INSERT_SQL = ("INSERT INTO depth_charts (team_id, season, week, position_slot, depth, player_id, source) "
              "VALUES (%s, %s, %s, %s, %s, %s, %s)")
SELECT_SQL = ("SELECT position_slot, depth, player_id FROM depth_charts "
              "WHERE team_id = %s AND season = %s AND week = %s AND source = %s ORDER BY position_slot, depth")


def _check_source(source):
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}, not {source!r}")


def store(cur, team_id, season, week, chart, source):
    """Replaces this (team, season, week, source)'s depth_charts rows with the chart (depth 1..6 per slot)."""
    _check_source(source)
    rows = []
    for slot, ids in chart.items():
        if slot not in POSITION_SLOTS:
            raise ValueError(f"not a depth chart position: {slot!r}")
        ids = list(ids)
        if len(ids) > DEPTH:
            raise ValueError(f"{slot}: {len(ids)} players, at most {DEPTH}")
        for depth, pid in enumerate(ids, start=1):
            if pid is not None:
                rows.append((team_id, season, week, slot, depth, pid, source))
    cur.execute(DELETE_SQL, (team_id, season, week, source))
    if rows:
        cur.executemany(INSERT_SQL, rows)


def load(cur, team_id, season, week, source):
    """{slot: [player_id, ...]} best first for every POSITION_SLOTS slot, or {} when nothing is stored."""
    _check_source(source)
    cur.execute(SELECT_SQL, (team_id, season, week, source))
    rows = cur.fetchall()
    if not rows:
        return {}
    chart = {slot: [] for slot in POSITION_SLOTS}
    for slot, depth, pid in sorted(rows, key=lambda r: (r[0], r[1])):
        if pid is not None:
            chart.setdefault(slot, []).append(pid)
    return chart
