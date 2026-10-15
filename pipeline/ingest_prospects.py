"""Prospect ingestion and validation module.

Parses, validates, and normalizes draft prospect boards from CSV files without requiring
database schema modifications.

Each prospect contains:
    - projected_pick: Beginning-of-season projected pick rank (1, 2, ..., 32+)
    - full_name: Player's full legal name
    - position: Canonical position (QB, HB, WR, TE, OT, IOL, EDGE, DT, LB, CB, S, etc.)
    - height: Formatted height (e.g. 6'3" or 6-3)
    - weight: Weight in pounds (integer)
    - age: Age at draft time (integer)
    - college: College/university program
    - overall_rating: Scouted pre-draft baseline OVR (integer, 50-99)
    - archetype: Optional scouted archetype / trait
    - notes: Optional scouting summary
"""

import csv
import os
import re
from dataclasses import asdict, dataclass
from typing import List, Optional


CANONICAL_POSITIONS = {
    # Offense
    "QB", "HB", "RB", "FB", "WR", "TE",
    "OT", "LT", "RT",
    "IOL", "OG", "LG", "RG", "C",
    # Defense
    "EDGE", "LEDG", "REDG", "DE", "LE", "RE",
    "DT", "NT",
    "LB", "MLB", "ILB", "OLB", "MIKE", "WILL", "SAM",
    "CB", "S", "FS", "SS",
    # Specialists
    "K", "P", "LS"
}


@dataclass
class Prospect:
    projected_pick: int
    full_name: str
    position: str
    college: str
    height: str
    weight: int
    age: int
    overall_rating: int
    archetype: Optional[str] = None
    notes: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_height(val: str) -> str:
    """Normalize heights into standard 6'2\" notation."""
    if not val:
        raise ValueError("Height cannot be empty")
    s = str(val).strip().replace("\"", "").replace("'", "-")
    # Matches formats like 6-2, 6-02, 6 2
    m = re.match(r"^(\d)[\s\-\']+(\d{1,2})$", s)
    if m:
        feet, inches = m.groups()
        return f"{feet}'{int(inches)}\""
    # Fallback if already like 6'2"
    if re.match(r"^\d'\d{1,2}\"?$", str(val).strip()):
        cleaned = str(val).strip().rstrip("\"")
        return f"{cleaned}\""
    return str(val).strip()


def normalize_position(pos: str) -> str:
    """Normalize position abbreviations."""
    clean = str(pos).strip().upper()
    if clean == "RB":
        return "HB"
    if clean in ("OG", "G"):
        return "IOL"
    if clean == "DE":
        return "EDGE"
    if clean == "NT":
        return "DT"
    if clean in ("ILB", "OLB"):
        return "LB"
    return clean


def parse_prospect_row(row: dict, line_num: int) -> Prospect:
    """Parse and validate a single CSV row with case-insensitive header mapping."""
    clean_row = {k.strip().lower().replace(" ", "_"): (v.strip() if v else "") for k, v in row.items() if k}

    def get_field(keys: list[str], default: str = "") -> str:
        for k in keys:
            if k in clean_row and clean_row[k]:
                return clean_row[k]
        return default

    # Projected Pick
    pick_str = get_field(["projected_pick", "pick", "rank", "proj_pick", "projected"])
    if not pick_str:
        raise ValueError(f"Line {line_num}: missing projected_pick")
    try:
        projected_pick = int(pick_str)
        if projected_pick <= 0:
            raise ValueError()
    except ValueError:
        raise ValueError(f"Line {line_num}: invalid projected_pick '{pick_str}', must be positive integer")

    # Full Name
    full_name = get_field(["full_name", "name", "player", "player_name"])
    if not full_name:
        raise ValueError(f"Line {line_num}: missing full_name")

    # Position
    pos_raw = get_field(["position", "pos"])
    if not pos_raw:
        raise ValueError(f"Line {line_num}: missing position for {full_name}")
    position = normalize_position(pos_raw)
    if position not in CANONICAL_POSITIONS:
        raise ValueError(f"Line {line_num}: unknown position '{pos_raw}' for {full_name}")

    # College
    college = get_field(["college", "school", "university"])
    if not college:
        raise ValueError(f"Line {line_num}: missing college for {full_name}")

    # Height
    height_raw = get_field(["height", "ht"])
    if not height_raw:
        raise ValueError(f"Line {line_num}: missing height for {full_name}")
    height = normalize_height(height_raw)

    # Weight
    weight_str = get_field(["weight", "wt"])
    if not weight_str:
        raise ValueError(f"Line {line_num}: missing weight for {full_name}")
    try:
        weight = int(weight_str)
        if weight < 140 or weight > 420:
            raise ValueError()
    except ValueError:
        raise ValueError(f"Line {line_num}: invalid weight '{weight_str}' for {full_name} (expected 140-420)")

    # Age
    age_str = get_field(["age"])
    if not age_str:
        raise ValueError(f"Line {line_num}: missing age for {full_name}")
    try:
        age = int(age_str)
        if age < 18 or age > 30:
            raise ValueError()
    except ValueError:
        raise ValueError(f"Line {line_num}: invalid age '{age_str}' for {full_name} (expected 18-30)")

    # Overall Rating (OVR)
    ovr_str = get_field(["overall_rating", "ovr", "rating", "grade"])
    if not ovr_str:
        raise ValueError(f"Line {line_num}: missing overall_rating for {full_name}")
    try:
        ovr = int(ovr_str)
        if ovr < 40 or ovr > 99:
            raise ValueError()
    except ValueError:
        raise ValueError(f"Line {line_num}: invalid overall_rating '{ovr_str}' for {full_name} (expected 40-99)")

    archetype = get_field(["archetype", "trait", "style"], default=None)
    notes = get_field(["notes", "scouting_report", "summary"], default=None)

    return Prospect(
        projected_pick=projected_pick,
        full_name=full_name,
        position=position,
        college=college,
        height=height,
        weight=weight,
        age=age,
        overall_rating=ovr,
        archetype=archetype,
        notes=notes
    )


def load_prospects_from_csv(csv_path: str) -> List[Prospect]:
    """Load, validate, and sort prospects from a CSV file."""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Prospects CSV not found: {csv_path}")

    prospects = []
    seen_picks = set()
    with open(csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"CSV file is empty: {csv_path}")

        for idx, row in enumerate(reader, start=2):
            if not any(row.values()):
                continue
            prospect = parse_prospect_row(row, idx)
            if prospect.projected_pick in seen_picks:
                raise ValueError(f"Duplicate projected_pick {prospect.projected_pick} found for {prospect.full_name}")
            seen_picks.add(prospect.projected_pick)
            prospects.append(prospect)

    prospects.sort(key=lambda p: p.projected_pick)
    return prospects
