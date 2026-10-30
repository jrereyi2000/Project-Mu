"""Audit and baseline portrait generator for active rosters (e.g. 2026 initial season).

Audits all active players in a given season, identifies who has official broadcast
face scans vs who is missing them, fetches official media-day portraits from ESPN,
generates local 5-bracket inpainting portraits for players without photos, and
batch-exports 512x512 BC3 DDS textures + manifest for Madden 27 modding.

Usage:
    python -m headshots.audit_roster_portraits [--season 2026] [--fetch-espn] [--export-dds] [--dry-run]
"""

from __future__ import annotations

import argparse
import io
import logging
import os
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image
import psycopg

from headshots.local.donors import get_donor_plate, normalize_team
from headshots.portrait_converter.converter import to_portrait

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("audit_roster_portraits")

ROOT = Path(__file__).resolve().parent.parent
STATIC_PLAYERS_DIR = ROOT / "static" / "players"
BACKUP_PLAYERS_DIR = ROOT / "data" / "headshots" / "players_backup"
DEFAULT_DDS_DIR = ROOT / "data" / "headshots" / "dds" / "2026_baseline"

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
ESPN_HEADSHOT_URL = "https://a.espncdn.com/combiner/i?img=/i/headshots/nfl/players/full/{espn_id}.png&w=1000&h=1000"


def get_current_season(dsn: str = DSN) -> int:
    try:
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT current_season FROM league_state WHERE id = 1")
            row = cur.fetchone()
            if row and row[0]:
                return int(row[0])
    except Exception:
        pass
    return 2026


def fetch_season_roster(season: int, dsn: str = DSN) -> List[Dict[str, Any]]:
    query = """
        SELECT p.id,
               COALESCE(NULLIF(TRIM(p.full_name), ''), p.player_name) AS full_name,
               t.abbreviation AS team_abbr,
               t.name AS team_name,
               ps.position,
               p.height,
               p.weight,
               ps.age,
               ps.years_of_experience,
               p.espn_id
        FROM players p
        JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
        JOIN teams t ON t.id = ps.team_id
        ORDER BY t.abbreviation, ps.position, p.id
    """
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(query, (season,))
        rows = cur.fetchall()

    roster = []
    for r in rows:
        roster.append({
            "id": r[0],
            "full_name": r[1],
            "team_abbr": r[2],
            "team_name": r[3],
            "position": r[4],
            "height": r[5] or "6'1\"",
            "weight": r[6] or 220,
            "age": r[7] or 24,
            "years_of_experience": r[8],
            "espn_id": r[9],
        })
    return roster


def download_espn_cutout(espn_id: int) -> Optional[Image.Image]:
    """Download official ESPN portrait and segment background via rembg."""
    url = ESPN_HEADSHOT_URL.format(espn_id=espn_id)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = resp.read()
            # Default ESPN silhouettes are usually < 15KB or contain default placeholder
            if len(data) < 20000:
                return None
            im = Image.open(io.BytesIO(data)).convert("RGBA")
            import rembg
            nobg = rembg.remove(im)
            return nobg
    except Exception:
        return None


def run_audit(
    season: int = 2026,
    fetch_espn: bool = False,
    generate_missing: bool = False,
    export_dds: bool = False,
    dds_dir: Path = DEFAULT_DDS_DIR,
    max_workers: int = 6,
    dsn: str = DSN,
) -> Dict[str, Any]:
    """Audit roster face scans and portraits for a starting franchise season."""
    roster = fetch_season_roster(season, dsn)
    total_players = len(roster)
    logger.info(f"Loaded {total_players} rostered players for Season {season}.")

    STATIC_PLAYERS_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_PLAYERS_DIR.mkdir(parents=True, exist_ok=True)

    has_webp = []
    missing_webp_with_espn = []
    missing_webp_no_espn = []

    for p in roster:
        webp_path = STATIC_PLAYERS_DIR / f"{p['id']}.webp"
        if webp_path.exists() and webp_path.stat().st_size > 0:
            has_webp.append(p)
        elif p.get("espn_id"):
            missing_webp_with_espn.append(p)
        else:
            missing_webp_no_espn.append(p)

    logger.info(f"--- Season {season} Face Scan Audit ---")
    logger.info(f"  Total Roster Players: {total_players}")
    logger.info(f"  Already have WebP Cutout: {len(has_webp)} ({len(has_webp)/total_players*100:.1f}%)")
    logger.info(f"  Missing WebP (have ESPN ID): {len(missing_webp_with_espn)}")
    logger.info(f"  Missing WebP (no ESPN ID): {len(missing_webp_no_espn)}")

    fetched_espn_count = 0
    if fetch_espn and missing_webp_with_espn:
        logger.info(f"Fetching official ESPN headshots for {len(missing_webp_with_espn)} players...")

        def _fetch_worker(player):
            pid = player["id"]
            eid = player["espn_id"]
            target_webp = STATIC_PLAYERS_DIR / f"{pid}.webp"
            backup_webp = BACKUP_PLAYERS_DIR / f"{pid}.webp"
            cutout = download_espn_cutout(eid)
            if cutout:
                cutout.save(backup_webp, format="WEBP", quality=90, method=4)
                final_img = cutout.resize((700, 508), Image.Resampling.LANCZOS)
                final_img.save(target_webp, format="WEBP", quality=90, method=4)
                return True
            return False

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(_fetch_worker, p) for p in missing_webp_with_espn]
            for f in as_completed(futures):
                if f.result():
                    fetched_espn_count += 1

        logger.info(f"Successfully fetched and segmented {fetched_espn_count} ESPN broadcast headshots.")

    # Check remaining players needing synthetic face scans
    still_needing = []
    for p in roster:
        webp_path = STATIC_PLAYERS_DIR / f"{p['id']}.webp"
        if not webp_path.exists() or webp_path.stat().st_size == 0:
            still_needing.append(p)

    logger.info(f"Players lacking portraits after fetch: {len(still_needing)}")

    generated_count = 0
    if generate_missing and still_needing:
        logger.info(f"Generating local 5-bracket inpainting portraits for {len(still_needing)} players...")
        from headshots.rookie_headshots import generate_player_headshot
        from headshots.local.adapter import patch_headshot_generator

        patch_headshot_generator(force=True)

        for p in still_needing:
            try:
                generate_player_headshot(
                    player={
                        "id": p["id"],
                        "full_name": p["full_name"],
                        "team": p["team_name"],
                        "position": p["position"],
                        "height": p["height"],
                        "weight": p["weight"],
                        "age": p["age"],
                    },
                    static_dir=str(STATIC_PLAYERS_DIR),
                    backup_dir=str(BACKUP_PLAYERS_DIR),
                )
                generated_count += 1
            except Exception as e:
                logger.error(f"Failed to generate headshot for {p['full_name']}: {e}")

    # Export DDS portrait textures if requested
    exported_dds_count = 0
    if export_dds:
        dds_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = dds_dir / "portrait_manifest.csv"
        logger.info(f"Exporting 512x512 BC3 DDS textures to {dds_dir}...")

        manifest_lines = ["player_id,full_name,team,position,years_exp,dds_filename,status\n"]

        for p in roster:
            pid = p["id"]
            webp_path = STATIC_PLAYERS_DIR / f"{pid}.webp"
            dds_file = f"{pid}.dds"
            dds_path = dds_dir / dds_file

            if webp_path.exists():
                try:
                    with open(webp_path, "rb") as f:
                        webp_bytes = f.read()
                    dds_bytes = to_portrait(webp_bytes, width=512, height=512, fmt="DDS_BC3")
                    with open(dds_path, "wb") as f:
                        f.write(dds_bytes)
                    exported_dds_count += 1
                    manifest_lines.append(f"{pid},\"{p['full_name']}\",{p['team_abbr']},{p['position']},{p['years_of_experience']},{dds_file},OK\n")
                except Exception as e:
                    manifest_lines.append(f"{pid},\"{p['full_name']}\",{p['team_abbr']},{p['position']},{p['years_of_experience']},{dds_file},CONVERT_ERROR\n")
            else:
                manifest_lines.append(f"{pid},\"{p['full_name']}\",{p['team_abbr']},{p['position']},{p['years_of_experience']},MISSING,NO_PORTRAIT\n")

        with open(manifest_path, "w", encoding="utf-8") as f:
            f.writelines(manifest_lines)

        logger.info(f"Exported {exported_dds_count}/{total_players} DDS portrait textures to {dds_dir}.")
        logger.info(f"Wrote mod manifest to {manifest_path}")

    return {
        "season": season,
        "total_roster": total_players,
        "initial_webp_count": len(has_webp),
        "fetched_espn": fetched_espn_count,
        "generated_missing": generated_count,
        "exported_dds": exported_dds_count,
        "uncovered_count": len(still_needing) - generated_count,
    }


def main():
    parser = argparse.ArgumentParser(description="Audit and package starting roster portraits for Madden 27 modding.")
    parser.add_argument("--season", type=int, default=2026, help="Season to audit (default: 2026).")
    parser.add_argument("--fetch-espn", action="store_true", help="Download official ESPN headshots for real players.")
    parser.add_argument("--generate-missing", action="store_true", help="Generate 5-bracket portraits for players lacking photos.")
    parser.add_argument("--export-dds", action="store_true", help="Batch export 512x512 BC3 DDS textures and manifest.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_DDS_DIR, help="Destination directory for DDS textures.")
    parser.add_argument("--workers", type=int, default=6, help="Parallel workers for ESPN fetching.")
    args = parser.parse_args()

    run_audit(
        season=args.season,
        fetch_espn=args.fetch_espn,
        generate_missing=args.generate_missing,
        export_dds=args.export_dds,
        dds_dir=args.out_dir,
        max_workers=args.workers,
    )


if __name__ == "__main__":
    main()
