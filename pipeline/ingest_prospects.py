"""Prospect ingestion and validation module.

Parses, validates, and normalizes draft prospect boards from CSV files. The board is stored in the
database by pipeline/draft_board.py (draft_prospects); this module only reads and checks the CSV.

Each prospect contains:
    - projected_pick: Overall projected pick (1-224 drafted, then undrafted in board order)
    - projected_round / round_pick: The board's PICK cell ("4", "19"; "UDFA" and None when undrafted)
    - full_name: Player's full legal name
    - position: Canonical position (QB, HB, WR, TE, OT, IOL, EDGE, DT, LB, CB, S, etc.)
    - height: Formatted height (e.g. 6'3")
    - weight: Weight in pounds (integer)
    - age: Age at draft time (integer)
    - college: College/university program
    - overall_rating: Scouted pre-draft baseline OVR (integer, 40-99)
    - archetype: Optional scouted archetype / trait
    - notes: Optional scouting summary

Two modes. strict (the default) raises on the first missing or implausible field. lenient (JR, Oct 15:
a board that isn't perfect still loads; next season's roster scan has the real values once the
prospect is a rookie) turns a bad field into None and records a warning, skips a row with no name or
no projected pick, and keeps the first of two rows claiming the same pick.
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
    position: Optional[str]
    college: Optional[str]
    height: Optional[str]
    weight: Optional[int]
    age: Optional[int]
    overall_rating: Optional[int]
    archetype: Optional[str] = None
    notes: Optional[str] = None
    projected_round: Optional[str] = None     # "1".."7" or "UDFA"
    round_pick: Optional[int] = None
    prospect_id: Optional[int] = None         # draft_prospects.id when read from the database

    def to_dict(self) -> dict:
        return asdict(self)


class SkipRow(ValueError):
    """A row lenient mode leaves out (no name or no projected pick)."""


def height_inches(height: Optional[str]) -> Optional[int]:
    """6'2" -> 74; None when the height isn't in feet'inches" form."""
    m = re.match(r"^([4-7])'(\d{1,2})\"?$", str(height or "").strip())
    return int(m.group(1)) * 12 + int(m.group(2)) if m and int(m.group(2)) <= 11 else None


def inches_height(inches: Optional[int]) -> Optional[str]:
    """74 -> 6'2\"."""
    return f"{inches // 12}'{inches % 12}\"" if inches else None


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


def parse_prospect_row(row: dict, line_num: int, strict: bool = True,
                       warnings: Optional[List[str]] = None) -> Prospect:
    """Parse and validate a single CSV row with case-insensitive header mapping.

    strict: raise ValueError on the first missing or implausible field. Lenient (strict=False): the
    field becomes None and a warning is appended to `warnings`; a row with no name or no projected pick
    raises SkipRow."""
    clean_row = {k.strip().lower().replace(" ", "_"): (v.strip() if v else "") for k, v in row.items() if k}
    warns = warnings if warnings is not None else []

    def get_field(keys: list[str], default: str = "") -> str:
        for k in keys:
            if k in clean_row and clean_row[k]:
                return clean_row[k]
        return default

    who = get_field(["full_name", "name", "player", "player_name"]) or f"line {line_num}"

    def bad(message: str):
        if strict:
            raise ValueError(f"Line {line_num}: {message}")
        warns.append(f"Line {line_num}: {message} (left blank)")
        return None

    def number(keys, label, lo, hi):
        raw = get_field(keys)
        if not raw:
            return bad(f"missing {label} for {who}")
        try:
            val = int(raw)
            if not lo <= val <= hi:
                raise ValueError()
            return val
        except ValueError:
            return bad(f"invalid {label} '{raw}' for {who} (expected {lo}-{hi})")

    # Projected Pick
    pick_str = get_field(["projected_pick", "pick", "rank", "proj_pick", "projected"])
    try:
        projected_pick = int(pick_str)
        if projected_pick <= 0:
            raise ValueError()
    except ValueError:
        message = (f"Line {line_num}: missing projected_pick" if not pick_str else
                   f"Line {line_num}: invalid projected_pick '{pick_str}', must be positive integer")
        raise (ValueError if strict else SkipRow)(message)

    # Full Name
    full_name = get_field(["full_name", "name", "player", "player_name"])
    if not full_name:
        raise (ValueError if strict else SkipRow)(f"Line {line_num}: missing full_name")

    # Position
    position = None
    pos_raw = get_field(["position", "pos"])
    if not pos_raw:
        bad(f"missing position for {full_name}")
    elif normalize_position(pos_raw) not in CANONICAL_POSITIONS:
        bad(f"unknown position '{pos_raw}' for {full_name}")
    else:
        position = normalize_position(pos_raw)

    college = get_field(["college", "school", "university"]) or bad(f"missing college for {full_name}")
    height_raw = get_field(["height", "ht"])
    height = normalize_height(height_raw) if height_raw else bad(f"missing height for {full_name}")
    if height and not strict and height_inches(height) is None:
        height = bad(f"invalid height '{height_raw}' for {full_name}")
    weight = number(["weight", "wt"], "weight", 140, 420)
    age = number(["age"], "age", 18, 30)
    ovr = number(["overall_rating", "ovr", "rating", "grade"], "overall_rating", 40, 99)

    projected_round = get_field(["projected_round", "round"]) or None
    if projected_round and projected_round.upper() not in {"1", "2", "3", "4", "5", "6", "7", "UDFA"}:
        projected_round = bad(f"invalid projected_round '{projected_round}' for {full_name}")
    rp = get_field(["round_pick"])
    round_pick = int(rp) if rp.isdigit() else None

    return Prospect(
        projected_pick=projected_pick,
        full_name=full_name,
        position=position,
        college=college,
        height=height,
        weight=weight,
        age=age,
        overall_rating=ovr,
        archetype=get_field(["archetype", "trait", "style"], default=None),
        notes=get_field(["notes", "scouting_report", "summary"], default=None),
        projected_round=projected_round.upper() if projected_round else None,
        round_pick=round_pick,
    )


def load_prospects_from_csv(csv_path: str, strict: bool = True,
                            warnings: Optional[List[str]] = None) -> List[Prospect]:
    """Load, validate, and sort prospects from a CSV file.

    strict: any bad row raises. Lenient (strict=False): see the module docstring; problems are
    appended to `warnings`. Either way an empty board raises ValueError."""
    if not os.path.exists(csv_path):
        raise FileNotFoundError(f"Prospects CSV not found: {csv_path}")
    warns = warnings if warnings is not None else []

    prospects = []
    seen_picks = {}
    with open(csv_path, mode="r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError(f"CSV file is empty: {csv_path}")

        for idx, row in enumerate(reader, start=2):
            if not any(row.values()):
                continue
            try:
                prospect = parse_prospect_row(row, idx, strict=strict, warnings=warns)
            except SkipRow as e:
                warns.append(f"{e} (row skipped)")
                continue
            if prospect.projected_pick in seen_picks:
                message = (f"Duplicate projected_pick {prospect.projected_pick} found for {prospect.full_name}")
                if strict:
                    raise ValueError(message)
                warns.append(f"Line {idx}: {message}; kept {seen_picks[prospect.projected_pick]} (row skipped)")
                continue
            seen_picks[prospect.projected_pick] = prospect.full_name
            prospects.append(prospect)

    if not prospects:
        raise ValueError(f"No usable prospects in {csv_path}")
    prospects.sort(key=lambda p: p.projected_pick)
    return prospects
