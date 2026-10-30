"""Audit and baseline portrait generator for active rosters and Madden 27 saves.

Audits players directly in Madden 27 PC franchise saves (or the database) to identify
who actually lacks in-game broadcast portraits (where Player.PLYR_PORTRAIT == 0,
which renders as a blank grey silhouette in Madden).

Generates photorealistic 5-bracket inpainting portraits for those missing players,
converts them to 512x512 BC3 DDS textures, and creates a mod manifest ready for
MMC Modding Tools / MyFranchise Tool in-game injection.

Usage:
    # Audit real Madden franchise save directly:
    python -m headshots.audit_roster_portraits --save CAREER-GIANTS-AUTOSAVE

    # Generate portraits for players missing Madden headshots and export DDS textures:
    python -m headshots.audit_roster_portraits --save CAREER-GIANTS-AUTOSAVE --generate-missing --export-dds

    # Or audit via database season:
    python -m headshots.audit_roster_portraits --season 2026
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import os
import subprocess
import sys
import tempfile
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
READER_SCRIPT = ROOT / "pipeline" / "save_reader" / "pc_save_reader.mjs"

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


def audit_madden_save(
    save_or_snapshot_path: Path,
    include_practice_squad: bool = True,
    generate_missing: bool = False,
    export_dds: bool = False,
    dds_dir: Path = DEFAULT_DDS_DIR,
) -> Dict[str, Any]:
    """Audit players directly from a Madden 27 PC save file or snapshot JSON."""
    logger.info(f"Loading Madden franchise save from {save_or_snapshot_path}...")

    if save_or_snapshot_path.suffix == ".json":
        with open(save_or_snapshot_path, "r", encoding="utf-8") as f:
            snapshot = json.load(f)
    else:
        # Run pc_save_reader.mjs to generate temporary snapshot
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            cmd = ["node", str(READER_SCRIPT), str(save_or_snapshot_path), "--out", str(tmp_path)]
            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"Save reader failed: {res.stderr.strip() or res.stdout.strip()}")
            with open(tmp_path, "r", encoding="utf-8") as f:
                snapshot = json.load(f)
        finally:
            if tmp_path.exists():
                tmp_path.unlink()

    players = snapshot.get("players", [])
    teams_dict = snapshot.get("teams", {})
    # Map team_index -> team abbreviation
    team_map = {}
    for t in teams_dict:
        if isinstance(t, dict):
            t_idx = t.get("team_index")
            t_abbr = t.get("short_name") or t.get("abbr") or t.get("nick_name")
            if t_idx is not None and t_abbr:
                team_map[t_idx] = normalize_team(t_abbr)

    signed_players = [p for p in players if p.get("status") == "Signed"]
    ps_players = [p for p in players if p.get("status") == "PracticeSquad"]
    fa_players = [p for p in players if p.get("status") == "FreeAgent"]

    def is_missing_portrait(p):
        return (p.get("appearance", {}).get("portrait_id") or 0) == 0

    signed_missing = [p for p in signed_players if is_missing_portrait(p)]
    signed_have = [p for p in signed_players if not is_missing_portrait(p)]

    ps_missing = [p for p in ps_players if is_missing_portrait(p)]
    ps_have = [p for p in ps_players if not is_missing_portrait(p)]

    logger.info("=================================================================")
    logger.info(f"   MADDEN 27 IN-GAME HEADSHOT AUDIT ({save_or_snapshot_path.name})")
    logger.info("=================================================================")
    logger.info(f"  Signed Roster Players (53-man active): {len(signed_players)}")
    logger.info(f"    - HAVE Authentic Madden Headshots: {len(signed_have)} ({len(signed_have)/len(signed_players)*100:.1f}%)")
    logger.info(f"    - MISSING Headshot (portrait_id = 0, blank silhouette): {len(signed_missing)} ({len(signed_missing)/len(signed_players)*100:.1f}%)")
    logger.info(f"  Practice Squad Players: {len(ps_players)}")
    logger.info(f"    - HAVE Authentic Madden Headshots: {len(ps_have)}")
    logger.info(f"    - MISSING Headshot: {len(ps_missing)}")
    logger.info("-----------------------------------------------------------------")

    targets = list(signed_missing)
    if include_practice_squad:
        targets.extend(ps_missing)

    logger.info(f"Total players lacking in-game portraits targeted for generation: {len(targets)}")
    for p in targets[:15]:
        t_abbr = team_map.get(p.get("team_index"), "FA")
        logger.info(f"   * {p.get('first_name')} {p.get('last_name')} ({p.get('position')}, {t_abbr}, OVR {p.get('overall')})")
    if len(targets) > 15:
        logger.info(f"   ... and {len(targets) - 15} more players.")

    generated_count = 0
    if generate_missing and targets:
        logger.info(f"Generating 5-bracket inpainting portraits for {len(targets)} players...")
        from headshots.local.adapter import patch_headshot_generator
        from headshots.rookie_headshots import generate_player_headshot

        patch_headshot_generator(force=True)

        for p in targets:
            full_name = f"{p.get('first_name')} {p.get('last_name')}".strip()
            team_abbr = team_map.get(p.get("team_index"), "CAR")
            height_in = p.get("height_in") or 73
            height_str = f"{height_in // 12}'{height_in % 12}\""
            weight = p.get("weight_lbs") or 220
            age = p.get("age") or 23
            pos = p.get("position") or "WR"
            pgid = p.get("pgid") or p.get("row")

            try:
                generate_player_headshot(
                    player={
                        "id": pgid,
                        "full_name": full_name,
                        "team": team_abbr,
                        "position": pos,
                        "height": height_str,
                        "weight": weight,
                        "age": age,
                    },
                    static_dir=str(STATIC_PLAYERS_DIR),
                    backup_dir=str(BACKUP_PLAYERS_DIR),
                )
                generated_count += 1
            except Exception as e:
                logger.error(f"Failed to generate headshot for {full_name}: {e}")

        logger.info(f"Successfully generated {generated_count}/{len(targets)} headshots.")

    exported_dds_count = 0
    if export_dds and targets:
        dds_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = dds_dir / "madden_portrait_gapfill_manifest.csv"
        logger.info(f"Exporting 512x512 BC3 DDS textures to {dds_dir}...")

        lines = ["pgid,full_name,team,position,status,dds_filename\n"]
        for p in targets:
            pgid = p.get("pgid") or p.get("row")
            full_name = f"{p.get('first_name')} {p.get('last_name')}".strip()
            team_abbr = team_map.get(p.get("team_index"), "FA")
            pos = p.get("position") or "WR"
            status = p.get("status") or "Signed"

            webp_path = STATIC_PLAYERS_DIR / f"{pgid}.webp"
            dds_filename = f"{pgid}.dds"
            dds_path = dds_dir / dds_filename

            if webp_path.exists():
                try:
                    with open(webp_path, "rb") as f:
                        webp_bytes = f.read()
                    dds_bytes = to_portrait(webp_bytes, width=512, height=512, fmt="DDS_BC3")
                    with open(dds_path, "wb") as f:
                        f.write(dds_bytes)
                    exported_dds_count += 1
                    lines.append(f"{pgid},\"{full_name}\",{team_abbr},{pos},{status},{dds_filename}\n")
                except Exception as e:
                    logger.warning(f"Could not convert {full_name} to DDS: {e}")

        with open(manifest_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
        logger.info(f"Exported {exported_dds_count} gap-fill DDS portraits to {dds_dir}.")
        logger.info(f"Wrote mod manifest to {manifest_path}")

    return {
        "signed_total": len(signed_players),
        "signed_missing_portrait": len(signed_missing),
        "ps_total": len(ps_players),
        "ps_missing_portrait": len(ps_missing),
        "generated": generated_count,
        "exported_dds": exported_dds_count,
    }


def fetch_database_roster(season: int, dsn: str = DSN) -> List[Dict[str, Any]]:
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


def main():
    parser = argparse.ArgumentParser(description="Audit and package portraits for Madden 27 franchise modding.")
    parser.add_argument("--save", type=Path, help="Path to Madden CAREER-* franchise save file to audit in-game portraits directly.")
    parser.add_argument("--snapshot", type=Path, help="Path to save.json snapshot.")
    parser.add_argument("--season", type=int, default=2026, help="Season to audit in database (default: 2026).")
    parser.add_argument("--no-ps", action="store_true", help="Exclude practice squad players from save audit.")
    parser.add_argument("--generate-missing", action="store_true", help="Generate 5-bracket inpainting portraits for players missing portraits.")
    parser.add_argument("--export-dds", action="store_true", help="Batch export 512x512 BC3 DDS textures and manifest.")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_DDS_DIR, help="Destination directory for DDS textures.")
    args = parser.parse_args()

    save_path = args.save or args.snapshot
    if save_path:
        audit_madden_save(
            save_or_snapshot_path=save_path,
            include_practice_squad=not args.no_ps,
            generate_missing=args.generate_missing,
            export_dds=args.export_dds,
            dds_dir=args.out_dir,
        )
    else:
        # Fall back to database audit
        roster = fetch_database_roster(args.season)
        logger.info(f"Database Audit: {len(roster)} players loaded for season {args.season}.")
        has_webp = sum(1 for p in roster if (STATIC_PLAYERS_DIR / f"{p['id']}.webp").exists())
        logger.info(f"Players with WebP cutout: {has_webp}/{len(roster)} ({has_webp/len(roster)*100:.1f}%)")


if __name__ == "__main__":
    main()
