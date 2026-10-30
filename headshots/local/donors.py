"""Five-bracket donor library and weight routing for Project Mu headshot inpainting.

Provides:
1. `get_weight_tier(weight_lbs: int) -> str`: Classifies player weight into the 5 tiers.
2. `get_donor_plate(team: str, weight_lbs: int) -> Optional[dict]`: Locates donor template and mask.
3. `load_donor_registry() -> dict`: Loads the 32-team donor registry with candidates.
4. `save_donor_registry(registry: dict)`: Persists registry updates.
5. `disqualify_donor(team: str, tier: str, replacement_id: str, reason: str)`: Swaps an oddly-shaped donor.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("headshots.local.donors")

TIER_SPEED = "tier1_speed"
TIER_SKILL = "tier2_skill"
TIER_POWER = "tier3_power"
TIER_EDGE = "tier4_edge"
TIER_LINEMAN = "tier5_lineman"

TIERS: List[str] = [
    TIER_SPEED,
    TIER_SKILL,
    TIER_POWER,
    TIER_EDGE,
    TIER_LINEMAN,
]

TIER_METADATA: Dict[str, Dict[str, Any]] = {
    TIER_SPEED: {
        "label": "Speed / Featherweight",
        "weight_range": "< 200 lbs",
        "min_weight": 0,
        "max_weight": 199,
        "target_positions": ["CB", "WR", "K", "P", "DB"],
        "description": "Slender neck (14-15 in), lean shoulders, narrower collar opening",
    },
    TIER_SKILL: {
        "label": "Prototypical Skill",
        "weight_range": "200 - 224 lbs",
        "min_weight": 200,
        "max_weight": 224,
        "target_positions": ["QB", "HB", "RB", "WR", "FS", "SS", "S"],
        "description": "Athletic muscular neck (16-17 in), broad chest and shoulder pads",
    },
    TIER_POWER: {
        "label": "Power / Hybrid",
        "weight_range": "225 - 254 lbs",
        "min_weight": 225,
        "max_weight": 254,
        "target_positions": ["LB", "MLB", "OLB", "TE", "FB"],
        "description": "Thick muscular neck (17-18 in), prominent trapezius, heavy shoulder pads",
    },
    TIER_EDGE: {
        "label": "Edge / Heavy",
        "weight_range": "255 - 289 lbs",
        "min_weight": 255,
        "max_weight": 289,
        "target_positions": ["DE", "EDGE", "LEDG", "REDG", "TE"],
        "description": "Imposing V-taper, thick neck (18-19 in), wide shoulder span",
    },
    TIER_LINEMAN: {
        "label": "Anchor / Lineman",
        "weight_range": ">= 290 lbs",
        "min_weight": 290,
        "max_weight": 999,
        "target_positions": ["OT", "OG", "C", "LT", "LG", "RG", "RT", "DT", "NT"],
        "description": "Massive 20-22 inch neck, wide collar opening, broad heavyweight jaw",
    },
}

# Default directory for donor templates
DEFAULT_DONOR_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "headshots" / "donors"
REGISTRY_PATH = DEFAULT_DONOR_DIR / "registry.json"


def get_weight_tier(weight_lbs: int) -> str:
    """Classify player weight into one of the 5 canonical weight tiers.

    Args:
        weight_lbs: Weight in pounds.

    Returns:
        One of 'tier1_speed', 'tier2_skill', 'tier3_power', 'tier4_edge', 'tier5_lineman'.
    """
    if weight_lbs < 200:
        return TIER_SPEED
    elif weight_lbs < 225:
        return TIER_SKILL
    elif weight_lbs < 255:
        return TIER_POWER
    elif weight_lbs < 290:
        return TIER_EDGE
    else:
        return TIER_LINEMAN


def normalize_team(team: str) -> str:
    """Normalize team names, city names, or abbreviations to 2-3 letter NFL abbreviation."""
    t = str(team).strip().upper()
    mapping = {
        "CARDINALS": "ARI", "ARIZONA": "ARI",
        "FALCONS": "ATL", "ATLANTA": "ATL",
        "RAVENS": "BAL", "BALTIMORE": "BAL",
        "BILLS": "BUF", "BUFFALO": "BUF",
        "PANTHERS": "CAR", "CAROLINA": "CAR",
        "BEARS": "CHI", "CHICAGO": "CHI",
        "BENGALS": "CIN", "CINCINNATI": "CIN",
        "BROWNS": "CLE", "CLEVELAND": "CLE",
        "COWBOYS": "DAL", "DALLAS": "DAL",
        "BRONCOS": "DEN", "DENVER": "DEN",
        "LIONS": "DET", "DETROIT": "DET",
        "PACKERS": "GB", "GREEN BAY": "GB",
        "TEXANS": "HOU", "HOUSTON": "HOU",
        "COLTS": "IND", "INDIANAPOLIS": "IND",
        "JAGUARS": "JAX", "JACKSONVILLE": "JAX", "JAC": "JAX",
        "CHIEFS": "KC", "KANSAS CITY": "KC",
        "RAIDERS": "LV", "LAS VEGAS": "LV",
        "CHARGERS": "LAC", "LOS ANGELES CHARGERS": "LAC",
        "RAMS": "LAR", "LOS ANGELES RAMS": "LAR", "LA": "LAR",
        "DOLPHINS": "MIA", "MIAMI": "MIA",
        "VIKINGS": "MIN", "MINNESOTA": "MIN",
        "PATRIOTS": "NE", "NEW ENGLAND": "NE",
        "SAINTS": "NO", "NEW ORLEANS": "NO",
        "GIANTS": "NYG", "NEW YORK GIANTS": "NYG",
        "JETS": "NYJ", "NEW YORK JETS": "NYJ",
        "EAGLES": "PHI", "PHILADELPHIA": "PHI",
        "STEELERS": "PIT", "PITTSBURGH": "PIT",
        "49ERS": "SF", "SAN FRANCISCO": "SF", "NINERS": "SF",
        "SEAHAWKS": "SEA", "SEATTLE": "SEA",
        "BUCCANEERS": "TB", "TAMPA BAY": "TB", "BUCS": "TB",
        "TITANS": "TEN", "TENNESSEE": "TEN",
        "COMMANDERS": "WAS", "WASHINGTON": "WAS", "WSH": "WAS",
    }
    if t in mapping:
        return mapping[t]
    if t in mapping.values():
        return t

    # Try last word (e.g. 'Saints' from 'New Orleans Saints')
    parts = t.split()
    if len(parts) > 1 and parts[-1] in mapping:
        return mapping[parts[-1]]

    return t


def load_donor_registry(registry_path: Optional[Path] = None) -> Dict[str, Any]:
    """Load the JSON donor registry containing all 32 teams and candidate rosters."""
    path = registry_path or REGISTRY_PATH
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_donor_registry(registry: Dict[str, Any], registry_path: Optional[Path] = None) -> None:
    """Save the JSON donor registry."""
    path = registry_path or REGISTRY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(registry, f, indent=2, ensure_ascii=False)


def get_donor_plate(
    team: str,
    weight_lbs: int,
    donor_dir: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Locate the active donor template and aperture mask for a player.

    Args:
        team: Team abbreviation or city (e.g. 'NO', 'CAR', 'Saints').
        weight_lbs: Player weight in pounds.
        donor_dir: Directory containing donor templates.

    Returns:
        Dict with keys:
            - 'team': Team abbreviation
            - 'tier': Weight tier key
            - 'player_name': Selected donor player name
            - 'espn_id': Donor ESPN athlete ID
            - 'weight': Donor weight in lbs
            - 'base_path': Path to 1024x1024 base template PNG
            - 'mask_path': Path to 1024x1024 aperture mask PNG
        Or None if no donor is configured for the team/tier.
    """
    norm_team = normalize_team(team)
    tier = get_weight_tier(weight_lbs)
    base_dir = donor_dir or DEFAULT_DONOR_DIR
    team_dir = base_dir / norm_team

    registry = load_donor_registry(base_dir / "registry.json")
    team_reg = registry.get(norm_team, {}).get("tiers", {}).get(tier, {})

    base_path = team_dir / f"{tier}_base.png"
    mask_path = team_dir / f"{tier}_mask.png"

    if not base_path.exists():
        return None

    return {
        "team": norm_team,
        "tier": tier,
        "player_name": team_reg.get("player_name", "Unknown Donor"),
        "espn_id": team_reg.get("espn_id"),
        "weight": team_reg.get("weight"),
        "base_path": str(base_path),
        "mask_path": str(mask_path) if mask_path.exists() else None,
    }


def disqualify_donor(
    team: str,
    tier: str,
    replacement_id: str,
    reason: str = "",
    registry_path: Optional[Path] = None,
) -> bool:
    """Disqualify an oddly-shaped donor and promote an alternative candidate from the team roster.

    Args:
        team: Team abbreviation (e.g. 'NO').
        tier: Weight tier (e.g. 'tier1_speed').
        replacement_id: ESPN ID of the replacement player from the candidate list.
        reason: Text reason for disqualification (e.g. 'neck shaped weird', 'deep V-neck').
        registry_path: Optional registry file path.

    Returns:
        True if successfully swapped, False otherwise.
    """
    path = registry_path or REGISTRY_PATH
    registry = load_donor_registry(path)
    norm_team = normalize_team(team)

    if norm_team not in registry or tier not in registry[norm_team].get("tiers", {}):
        logger.error(f"Team {norm_team} or tier {tier} not found in registry.")
        return False

    tier_data = registry[norm_team]["tiers"][tier]
    old_player = tier_data["player_name"]
    old_id = tier_data["espn_id"]

    # Search candidates for the replacement
    candidates = tier_data.get("candidates", [])
    replacement = next((c for c in candidates if str(c.get("id")) == str(replacement_id)), None)

    if not replacement:
        logger.error(f"Candidate ID {replacement_id} not found in alternatives for {norm_team} {tier}.")
        return False

    # Move current to disqualified list
    if "disqualified" not in tier_data:
        tier_data["disqualified"] = []

    tier_data["disqualified"].append({
        "espn_id": old_id,
        "player_name": old_player,
        "weight": tier_data.get("weight"),
        "position": tier_data.get("position"),
        "reason": reason,
    })

    # Promote replacement
    tier_data["espn_id"] = replacement["id"]
    tier_data["player_name"] = replacement["name"]
    tier_data["weight"] = replacement["weight"]
    tier_data["position"] = replacement["position"]
    tier_data["headshot_url"] = replacement["headshot_url"]

    save_donor_registry(registry, path)
    logger.info(f"Swapped {norm_team} {tier}: {old_player} -> {replacement['name']} (Reason: {reason})")
    return True
