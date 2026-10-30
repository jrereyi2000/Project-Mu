"""Tool to build and ingest the canonical 32-team, 5-tier donor library for Project Mu.

Fetches active rosters from ESPN API for all 32 NFL franchises, classifies players
into the 5 physical weight tiers, downloads high-resolution 1000x1000 media-day headshots,
pads them onto 1024x1024 canvases, and constructs the donor registry with alternative candidates.

Usage:
    python -m headshots.local.build_donor_library [--teams NO,CAR,KC] [--concurrency 5] [--download]
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from headshots.local.donors import (
    DEFAULT_DONOR_DIR,
    REGISTRY_PATH,
    TIER_EDGE,
    TIER_LINEMAN,
    TIER_METADATA,
    TIER_POWER,
    TIER_SKILL,
    TIER_SPEED,
    TIERS,
    get_weight_tier,
    load_donor_registry,
    normalize_team,
    save_donor_registry,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_donor_library")

ESPN_TEAMS_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams"
ESPN_ROSTER_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams/{team_id}/roster"
ESPN_HEADSHOT_URL = "https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/{espn_id}.png&w=1000&h=1000"


def fetch_all_teams() -> List[Dict[str, str]]:
    """Fetch all 32 NFL teams and their ESPN identifiers."""
    req = urllib.request.Request(ESPN_TEAMS_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    teams = []
    leagues = data.get("sports", [{}])[0].get("leagues", [{}])[0].get("teams", [])
    for entry in leagues:
        t = entry.get("team", {})
        teams.append({
            "id": str(t.get("id")),
            "abbr": normalize_team(t.get("abbreviation")),
            "name": t.get("displayName"),
            "slug": t.get("slug") or t.get("abbreviation", "").lower(),
        })
    return teams


def fetch_team_roster(team_id: str) -> List[Dict[str, Any]]:
    """Fetch roster for a given team ID from ESPN API."""
    url = ESPN_ROSTER_URL.format(team_id=team_id)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        logger.error(f"Failed to fetch roster for team ID {team_id}: {e}")
        return []

    athletes = []
    for group in data.get("athletes", []):
        for item in group.get("items", []):
            espn_id = item.get("id")
            # Only include players with valid headshot assets
            headshot_data = item.get("headshot", {})
            has_headshot = bool(headshot_data.get("href"))
            wt = int(item.get("weight", 0) or 0)
            if not espn_id or not has_headshot or wt <= 0:
                continue

            athletes.append({
                "id": str(espn_id),
                "name": item.get("displayName"),
                "position": item.get("position", {}).get("abbreviation", "ATH"),
                "height": item.get("displayHeight", ""),
                "weight": wt,
                "jersey": item.get("jersey", ""),
                "headshot_url": ESPN_HEADSHOT_URL.format(espn_id=espn_id),
            })
    return athletes


# Pre-calibrated/approved donors for teams
PREFERRED_DONORS: Dict[str, Dict[str, str]] = {
    "NO": {
        TIER_SPEED: "4361370",    # Chris Olave (187 lbs, WR)
        TIER_SKILL: "4599739",    # Kendre Miller (220 lbs, RB)
        TIER_POWER: "3929645",    # Juwan Johnson (231 lbs, TE)
        TIER_EDGE:  "3918310",    # Carl Granderson (261 lbs, DE)
        TIER_LINEMAN: "3917331",  # Erik McCoy (303 lbs, C)
    }
}


def select_best_candidate(candidates: List[Dict[str, Any]], tier: str, preferred_id: Optional[str] = None) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Sort candidates by positional fit and return (primary_donor, alternative_candidates)."""
    if preferred_id and any(c["id"] == str(preferred_id) for c in candidates):
        primary = next(c for c in candidates if c["id"] == str(preferred_id))
        alternatives = [c for c in candidates if c["id"] != str(preferred_id)][:4]
        return primary, alternatives

    target_pos = TIER_METADATA[tier]["target_positions"]

    def score_candidate(c: Dict[str, Any]) -> Tuple[int, int]:
        pos = c.get("position", "")
        is_target = 1 if pos in target_pos else 0
        # Target middle of weight range
        mid = (TIER_METADATA[tier]["min_weight"] + min(TIER_METADATA[tier]["max_weight"], 330)) / 2
        weight_dist = -abs(c["weight"] - mid)
        return (is_target, weight_dist)

    sorted_candidates = sorted(candidates, key=score_candidate, reverse=True)
    primary = sorted_candidates[0]
    alternatives = sorted_candidates[1:5]  # Keep up to 4 alternatives
    return primary, alternatives


def pad_headshot_to_1024(img_bytes: bytes) -> Image.Image:
    """Pad a 1000x1000 RGBA ESPN media-day headshot onto a 1024x1024 white canvas."""
    im = Image.open(io.BytesIO(img_bytes)).convert("RGBA")
    canvas = Image.new("RGB", (1024, 1024), (255, 255, 255))
    canvas.paste(im, (12, 24), im)
    return canvas


def build_library(
    target_teams: Optional[List[str]] = None,
    download_images: bool = True,
    out_dir: Path = DEFAULT_DONOR_DIR,
    concurrency: int = 4,
) -> Dict[str, Any]:
    """Build the complete 32-team 5-tier donor registry and image library."""
    all_teams = fetch_all_teams()
    if target_teams:
        norm_targets = {normalize_team(t) for t in target_teams}
        teams_to_process = [t for t in all_teams if t["abbr"] in norm_targets]
    else:
        teams_to_process = all_teams

    logger.info(f"Processing donor templates for {len(teams_to_process)} NFL franchises...")

    registry = load_donor_registry(out_dir / "registry.json")

    for team in teams_to_process:
        abbr = team["abbr"]
        name = team["name"]
        slug = team["slug"]
        logger.info(f"--- Sourcing {name} ({abbr}) ---")

        athletes = fetch_team_roster(team["id"])
        if not athletes:
            logger.warning(f"No athletes found for {abbr}, skipping.")
            continue

        # Group athletes by tier
        tier_groups: Dict[str, List[Dict[str, Any]]] = {t: [] for t in TIERS}
        for a in athletes:
            tier = get_weight_tier(a["weight"])
            tier_groups[tier].append(a)

        team_registry_entry: Dict[str, Any] = {
            "name": name,
            "abbr": abbr,
            "slug": slug,
            "espn_id": team["id"],
            "tiers": {},
        }

        # Check if team already exists in registry to preserve existing active/disqualified selections
        existing_tiers = registry.get(abbr, {}).get("tiers", {})

        for tier in TIERS:
            candidates = tier_groups[tier]
            if not candidates:
                logger.warning(f"No candidates for {abbr} {tier}!")
                continue

            existing_tier_entry = existing_tiers.get(tier, {})
            current_espn_id = existing_tier_entry.get("espn_id")

            # If user already selected a donor, keep that donor active
            if current_espn_id and any(c["id"] == str(current_espn_id) for c in candidates):
                primary = next(c for c in candidates if c["id"] == str(current_espn_id))
                alternatives = [c for c in candidates if c["id"] != str(current_espn_id)][:4]
            else:
                primary, alternatives = select_best_candidate(
                    candidates, tier, preferred_id=PREFERRED_DONORS.get(abbr, {}).get(tier)
                )

            tier_info = {
                "player_name": primary["name"],
                "espn_id": primary["id"],
                "position": primary["position"],
                "weight": primary["weight"],
                "height": primary["height"],
                "headshot_url": primary["headshot_url"],
                "candidates": alternatives,
                "disqualified": existing_tier_entry.get("disqualified", []),
            }
            team_registry_entry["tiers"][tier] = tier_info

            # Download and save 1024x1024 base image if requested
            if download_images:
                team_dir = out_dir / abbr
                team_dir.mkdir(parents=True, exist_ok=True)
                base_path = team_dir / f"{tier}_base.png"

                # Check if base_path already exists
                if not base_path.exists():
                    try:
                        req = urllib.request.Request(primary["headshot_url"], headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req, timeout=15) as r:
                            img_data = r.read()
                        padded = pad_headshot_to_1024(img_data)
                        padded.save(base_path)
                        logger.info(f"Saved {abbr} {tier}: {primary['name']} ({primary['weight']} lbs)")
                    except Exception as e:
                        logger.error(f"Failed to download {primary['name']} for {abbr} {tier}: {e}")

        registry[abbr] = team_registry_entry

    # Persist updated registry
    save_donor_registry(registry, out_dir / "registry.json")
    logger.info(f"Donor registry saved to {out_dir / 'registry.json'}")
    return registry


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build 32-team 5-tier donor library.")
    parser.add_argument("--teams", help="Comma-separated team abbreviations (e.g. NO,CAR,KC).")
    parser.add_argument("--no-download", action="store_true", help="Build registry without downloading images.")
    args = parser.parse_args()

    teams_arg = [t.strip() for t in args.teams.split(",")] if args.teams else None
    build_library(target_teams=teams_arg, download_images=not args.no_download)
