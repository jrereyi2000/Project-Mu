"""
pipeline/madden_player_edit.py: Shared helpers for Madden 27 player editing, position mapping,
archetype resolution, and save_writer edit construction.

Extracted from pipeline/teleport.py (AUDIT_LEDGER.md §3b item 1) so that kept components
(engines/progression.py, pipeline/teleport_draft.py) do not depend on the obsolete teleporter.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from engines import ovr_weights as ow
from pipeline import commentary_names as cn
from pipeline.load_save import POSITIONS, archetype as mu_archetype_name

WEIGHT_OFFSET = 160  # Madden stores weight as pounds - 160 (pipeline/save_reader/lib.mjs)

TO_MADDEN_POSITION = {mu: madden for madden, mu in POSITIONS.items()}  # LEDG -> LE, MIKE -> MLB, ...
NEIGHBOURS = [
    {"LT", "RT"},
    {"LG", "RG", "C"},
    {"LE", "RE"},
    {"LOLB", "ROLB", "MLB"},
    {"FS", "SS"},
    {"K", "P"},
    {"HB", "FB"},
    {"WR", "TE"},
    {"DT", "LE", "RE"},
    {"CB", "FS", "SS"},
]
DEV_TO_MADDEN = {
    "Normal": "Normal",
    "Star": "Star",
    "Superstar": "Superstar",
    "Superstar X-Factor": "XFactor",
    "X-Factor": "XFactor",
    "Hidden": "Hidden",
}
DONOR_STATUSES = ("Signed", "PracticeSquad")
SKIP_POSITIONS = ("DEF",)  # Project Mu's team-defense rows, not players
SAME_ARCHETYPE, OTHER_ARCHETYPE, NEIGHBOUR, ANYWHERE = 0, 15, 40, 120


def _key(text: Optional[str]) -> str:
    return re.sub(r"[^a-z]", "", (text or "").lower())


def madden_archetype(doc_or_pos, madden_position=None, mu_archetype=None):
    """Project Mu's archetype text ('Power Back', '• Power') -> the Madden PlayerType of that position, or None.

    Supports both signatures:
      madden_archetype(doc, madden_position, mu_archetype)
      madden_archetype(madden_position, mu_archetype)
    """
    if isinstance(doc_or_pos, dict):
        doc = doc_or_pos
        pos = madden_position
        want_text = mu_archetype
    else:
        doc = ow.load_weights()
        pos = doc_or_pos
        want_text = madden_position

    want = _key(want_text)
    if not want or not doc or "slots" not in doc:
        return None
    for arch in doc["slots"].get(pos, []):
        if arch and _key(mu_archetype_name(arch)) == want:
            return arch
    return None


def split_name(full_name: Optional[str], player_name: Optional[str] = None) -> Tuple[str, str]:
    """'KC Concepcion Jr' -> ('KC', 'Concepcion Jr'); falls back to player_name 'G. Crawford'."""
    name = (full_name or player_name or "").strip()
    first, _, last = name.partition(" ")
    return first.rstrip("."), last


def height_inches(text: Optional[str]) -> Optional[int]:
    m = re.match(r"\s*(\d+)'\s*(\d+)", text or "")
    return int(m.group(1)) * 12 + int(m.group(2)) if m else None


def surname_key(last: Optional[str]) -> str:
    return _key(re.sub(r"\s+(Jr\.?|Sr\.?|II|III|IV|V)$", "", last or "", flags=re.I))


def settle_archetype(doc, position, arch, ratings, target, max_rounds=200):
    """Ratings where `arch` is the player's top archetype at `target`. After adjust_ratings another archetype can end
    up higher (Madden would then show that archetype and a higher OVR), so each round lowers the rating that costs the
    highest rival more than it costs `arch`, then brings `arch` back to the target using only ratings that help `arch`
    more than that rival. A tie is fine: the player keeps their archetype. Returns (ratings, settled?)."""
    ratings = dict(ratings)
    own = doc["archetypes"][arch]
    for _ in range(max_rounds):
        grades = ow.predict_grades(doc, position, ratings)
        rivals = {a: g for a, g in grades.items() if a != arch and g > target}
        if not rivals:
            return ratings, True
        rival = doc["archetypes"][max(rivals, key=rivals.get)]
        options = []
        for k in set(rival.get("skills", rival["terms"])) | set(own.get("skills", own["terms"])):
            x = ratings.get(k, 0)
            if x <= 0:
                continue
            rival_drop = -ow._gain(rival["terms"], k, x, -1)
            own_drop = -ow._gain(own["terms"], k, x, -1)
            if rival_drop > own_drop:
                options.append((rival_drop - own_drop, k))
        if not options:
            return ratings, False
        ratings[max(options)[1]] -= 1
        # Back up to the target only on ratings that help `arch` more than the rival, or the step is undone.
        favour = [
            k
            for k in own.get("skills", own["terms"])
            if ow._gain(own["terms"], k, ratings.get(k, 0), 1) > ow._gain(rival["terms"], k, ratings.get(k, 0), 1)
        ]
        try:
            ratings, _ = ow.adjust_ratings(doc, arch, ratings, target, skills=favour)
        except ValueError:
            return ratings, False
    grades = ow.predict_grades(doc, position, ratings)
    return ratings, all(g <= target for a, g in grades.items() if a != arch)


def scanned_ratings(mu, donor, doc, arch):
    """(ratings, target OVR, notes) for a player with a PS5 scan (§2.3): the scanned ratings over the donor's (a rating
    the list doesn't show keeps the donor's value). Madden computed the scan's OVR from those very ratings, so they are
    written as read and the scan's OVR is the target, even where Project Mu's OVR for the same season differs (an
    older roster read; noted as a scan gap). Only when Project Mu has moved a season past the scan (progression) are
    they moved by Project Mu's OVR change, measured on the predicted grade so the formula's own error cancels out."""
    scan = mu["scan"]
    ratings = dict(donor["ratings"])
    ratings.update({k: v for k, v in scan["ratings"].items() if k in ratings})
    target, shown, notes = mu["overall"], scan["overall"], []
    if shown is None or target is None or shown == target:
        return ratings, shown if shown is not None else target, notes
    if mu.get("season") is None or scan["season"] >= mu["season"]:
        notes.append(f"scan gap: scan OVR {shown}, Project Mu {target}")
        return ratings, shown, notes
    notes.append(f"scan moved: {shown} -> {target} since {scan['season']}")
    if arch in doc["archetypes"]:
        try:
            ratings, _ = ow.adjust_ratings(doc, arch, ratings, ow.predict_grade(doc, arch, ratings) + target - shown)
        except ValueError as e:
            notes.append(f"unreachable: {e}")
    return ratings, target, notes


def player_edit(mu_or_donor, donor_or_target, doc=None, age_add=0, write_ovr=False, new_name=None, commentary_id=None):
    """(save_writer edit, notes) for one player pairing.

    Supports both:
      player_edit(mu, donor, doc, age_add=0, write_ovr=False)
      player_edit(donor_row, target, new_name=None, age_add=0, commentary_id=None)
    """
    if doc is None or not isinstance(doc, dict) or "archetypes" not in doc:
        doc = ow.load_weights()

    # Detect if args are swapped: donor first, target/mu second
    if isinstance(mu_or_donor, dict) and "pgid" in mu_or_donor and "madden_position" not in mu_or_donor and (
        "madden_position" in donor_or_target or "overall" in donor_or_target or "full_name" in donor_or_target
    ):
        donor = mu_or_donor
        mu = dict(donor_or_target)
    else:
        mu = dict(mu_or_donor)
        donor = donor_or_target

    if new_name is not None:
        mu["full_name"] = new_name

    first, last = split_name(mu.get("full_name"), mu.get("player_name"))
    position = mu.get("madden_position") or mu.get("position")
    mu_arch = mu.get("madden_archetype") or mu.get("archetype")
    arch = mu_arch if mu_arch in doc["archetypes"] else donor["archetype"]
    if donor["position"] != position and arch not in (doc["slots"].get(position) or []):
        arch = next((a for a in doc["slots"].get(position, []) if a), arch)

    notes = []
    set_ = {"FirstName": first, "LastName": last}
    if mu.get("age") is not None:
        set_["Age"] = mu["age"] + age_add
    if mu.get("years_pro") is not None:
        set_["YearsPro"] = mu["years_pro"]
    if mu.get("jersey") is not None:
        set_["JerseyNum"] = mu["jersey"]
    h = height_inches(mu.get("height"))
    if h:
        set_["Height"] = h
    if mu.get("weight"):
        set_["Weight"] = mu["weight"] - WEIGHT_OFFSET
    dev = DEV_TO_MADDEN.get(mu.get("dev_trait") or "")
    if dev:
        set_["TraitDevelopment"] = dev
    if donor["position"] != position:
        set_["Position"] = position
        notes.append(f"position {donor['position']} -> {position}")
    if arch != donor["archetype"]:
        set_["PlayerType"] = arch
        notes.append(f"archetype {donor['archetype']} -> {arch}")

    if commentary_id is not None:
        cid = commentary_id
    else:
        name = mu.get("full_name") or mu.get("player_name")
        cid = cn.commentary_id(name)
    set_["PLYR_COMMENT"] = cid if cid is not None else 0

    ratings = dict(donor["ratings"])
    if mu.get("scan"):
        ratings, target, scan_notes = scanned_ratings(mu, donor, doc, arch)
        notes += scan_notes
    elif mu.get("overall") is not None and arch in doc["archetypes"]:
        target = mu["overall"]
        try:
            ratings, _ = ow.adjust_ratings(doc, arch, ratings, target)
        except ValueError as e:
            notes.append(f"unreachable: {e}")
        if position in doc["slots"] and arch in doc["slots"][position]:
            ratings, settled = settle_archetype(doc, position, arch, ratings, target)
            if not settled:
                notes.append("rival archetype: another archetype stays above the target")
    else:
        notes.append("no Project Mu OVR: ratings kept")
        return {"pgid": donor["pgid"], "set": set_}, notes

    for k, v in ratings.items():
        if v != donor["ratings"].get(k):
            set_[f"{k}Rating"] = v

    if write_ovr and position in doc["slots"]:
        grades = [ow.predict_grade(doc, a, ratings) if a in doc["archetypes"] else 0 for a in doc["slots"][position]]
        if mu.get("scan") and target is not None and arch in doc["slots"][position]:
            # Scanned ratings carry Madden's own OVR; the fit's error (K, LS, FB) mustn't change it or the archetype.
            grades = [target if a == arch else min(g, target) for a, g in zip(doc["slots"][position], grades)]
            top, top_arch = target, arch
        else:
            top, top_arch = ow.predict_ovr(doc, position, ratings, current=arch)
        grades += [0] * (5 - len(grades))
        for i, g in enumerate(grades[:5]):
            set_[f"OverallGrade{i}"] = max(0, min(g, 99))
        set_["OverallRating"] = max(0, min(top, 99))
        set_["PlayerType"] = top_arch

    return {"pgid": donor["pgid"], "set": set_}, notes
