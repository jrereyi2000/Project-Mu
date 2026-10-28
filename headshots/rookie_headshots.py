#!/usr/bin/env python3
"""Automated rookie player headshot generation for seasonal pipeline (docs/done/HEADSHOT_PIPELINE.md).

Produces photorealistic broadcast-grade media-day portraits for incoming draft class rookies,
removes backgrounds via rembg (silueta model), and saves transparent WebPs to static/players/{player_id}.webp.
"""

import argparse
import concurrent.futures
import io
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image
import psycopg
from google import genai
from google.genai import types

from headshots.headshot_appearance_generator import appearance, parse_height
from headshots.make_headshot import (
    IMAGE_MODEL,
    IMAGE_PROMPT,
    parse_line,
    team_colors,
)
from pipeline.notify import env

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_PLAYERS_DIR = os.path.join(ROOT, "static", "players")
BACKUP_PLAYERS_DIR = os.path.join(ROOT, "data", "headshots", "players_backup")

_tl = threading.local()


def ensure_api_key():
    """Ensure GEMINI_API_KEY is available in os.environ."""
    if not os.environ.get("GEMINI_API_KEY"):
        key = env("GEMINI_API_KEY")
        if key:
            os.environ["GEMINI_API_KEY"] = key
        else:
            raise RuntimeError("GEMINI_API_KEY not set in environment or root .env")


def get_thread_client() -> genai.Client:
    """Thread-local genai Client to avoid connection contention."""
    ensure_api_key()
    if not hasattr(_tl, "client"):
        _tl.client = genai.Client()
    return _tl.client


def get_thread_rembg_session():
    """Thread-local rembg session with ONNX silueta model."""
    if not hasattr(_tl, "session"):
        import rembg
        _tl.session = rembg.new_session(model_name="silueta")
    return _tl.session


def get_current_season(dsn: str = DSN) -> int:
    """Get current season from league_state."""
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_season FROM league_state WHERE id = 1")
            row = cur.fetchone()
            if row and row[0]:
                return int(row[0])
    return 2036


def get_rookies_needing_headshots(
    season: int,
    drafted_only: bool = False,
    static_dir: str = STATIC_PLAYERS_DIR,
    dsn: str = DSN,
) -> Tuple[List[Dict[str, Any]], int]:
    """Query rookies for a season and identify those missing static/players/{id}.webp.

    Returns (rookies_needing_generation, total_rookies_found).
    """
    query = """
        SELECT p.id,
               COALESCE(NULLIF(TRIM(p.full_name), ''), p.player_name) AS full_name,
               t.name AS team,
               ps.position,
               p.height,
               p.weight,
               ps.age,
               dp.actual_pick,
               dp.id AS prospect_id
        FROM players p
        JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
        JOIN teams t ON t.id = ps.team_id
        LEFT JOIN draft_prospects dp ON dp.player_id = p.id AND dp.draft_season = %s
        WHERE ps.years_of_experience = 0
    """
    params: List[Any] = [season, season]
    if drafted_only:
        query += " AND dp.actual_pick IS NOT NULL"
    query += " ORDER BY dp.actual_pick NULLS LAST, p.id"

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

    rookies = []
    for r in rows:
        rookies.append({
            "id": r[0],
            "full_name": r[1],
            "team": r[2],
            "position": r[3],
            "height": r[4],
            "weight": r[5],
            "age": r[6],
            "actual_pick": r[7],
            "prospect_id": r[8],
        })

    needing = []
    for r in rookies:
        target_path = os.path.join(static_dir, f"{r['id']}.webp")
        if not os.path.exists(target_path):
            needing.append(r)

    return needing, len(rookies)


def generate_player_headshot(
    player: Dict[str, Any],
    static_dir: str = STATIC_PLAYERS_DIR,
    backup_dir: str = BACKUP_PLAYERS_DIR,
    max_retries: int = 3,
    ref_image_bytes: Optional[bytes] = None,
) -> str:
    """Generate, segment, and persist a single player headshot.

    Returns the absolute path to static/players/{player_id}.webp.
    """
    pid = player["id"]
    out_path = os.path.join(static_dir, f"{pid}.webp")
    if os.path.exists(out_path):
        return out_path

    ensure_api_key()
    name = player["full_name"]
    team = player["team"]
    pos = player["position"]
    height = player.get("height") or "6'1\""
    weight = player.get("weight") or 220
    age = player.get("age") or 22

    line, _ = appearance(name, team, pos, parse_height(height), float(weight), int(age))
    v = parse_line(line)
    c = team_colors(team)
    v.update(
        PRIMARY=c["primary"]["name"],
        PRIMARY_HEX=c["primary"]["hex"],
        SECONDARY=c["secondary"]["name"],
        SECONDARY_HEX=c["secondary"]["hex"],
    )

    prompt = IMAGE_PROMPT
    for k, val in v.items():
        prompt = prompt.replace("{" + k + "}", val)

    contents: List[Any] = [prompt]
    if ref_image_bytes:
        from headshots.make_headshot import STYLE_REF
        contents = [
            prompt + "\n\n" + STYLE_REF,
            types.Part.from_bytes(data=ref_image_bytes, mime_type="image/png"),
        ]

    client = get_thread_client()
    last_err = None
    img_bytes = None

    for attempt in range(max_retries):
        try:
            r = client.models.generate_content(
                model=IMAGE_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                    image_config=types.ImageConfig(aspect_ratio="4:3"),
                ),
            )
            if r.candidates and r.candidates[0].content and r.candidates[0].content.parts:
                for part in r.candidates[0].content.parts:
                    if part.inline_data and part.inline_data.data:
                        img_bytes = part.inline_data.data
                        break
            if img_bytes:
                break
        except Exception as e:
            last_err = e
            time.sleep(2 ** attempt * 1.5)

    if not img_bytes:
        raise RuntimeError(f"Failed to generate headshot for {name} (ID {pid}): {last_err}")

    raw_img = Image.open(io.BytesIO(img_bytes))
    import rembg
    nobg = rembg.remove(raw_img, session=get_thread_rembg_session())

    if backup_dir:
        os.makedirs(backup_dir, exist_ok=True)
        backup_path = os.path.join(backup_dir, f"{pid}.webp")
        nobg.save(backup_path, format="WEBP", quality=90, method=4)

    os.makedirs(static_dir, exist_ok=True)
    final_img = nobg.resize((700, 508), Image.Resampling.LANCZOS)
    final_img.save(out_path, format="WEBP", quality=90, method=4)
    return out_path


def generate_rookie_headshots(
    season: Optional[int] = None,
    drafted_only: bool = False,
    max_workers: int = 5,
    dry_run: bool = False,
    static_dir: str = STATIC_PLAYERS_DIR,
    backup_dir: str = BACKUP_PLAYERS_DIR,
    dsn: str = DSN,
    log_fn=print,
) -> Dict[str, Any]:
    """Generate broadcast media-day headshots for rookies drafted/entering in season.

    Saves directly to static/players/{player_id}.webp and backups to data/headshots/players_backup/.
    """
    if season is None:
        season = get_current_season(dsn)

    needing, total = get_rookies_needing_headshots(
        season=season,
        drafted_only=drafted_only,
        static_dir=static_dir,
        dsn=dsn,
    )
    already_had = total - len(needing)

    log_fn(f"[rookie_headshots] Season {season}: {total} rookies found on rosters ({already_had} already exist, {len(needing)} need generation)")

    if dry_run or not needing:
        return {
            "season": season,
            "total_rookies": total,
            "already_had": already_had,
            "generated": 0,
            "failed": 0,
            "errors": [],
        }

    generated = 0
    failed = 0
    errors = []
    completed_count = 0
    total_to_generate = len(needing)
    lock = threading.Lock()

    def process_item(item: Dict[str, Any]):
        nonlocal completed_count, generated, failed
        pid = item["id"]
        name = item["full_name"]
        team = item["team"]
        pos = item["position"]
        try:
            out = generate_player_headshot(
                player=item,
                static_dir=static_dir,
                backup_dir=backup_dir,
            )
            with lock:
                completed_count += 1
                generated += 1
                log_fn(f"[rookie_headshots] [{completed_count}/{total_to_generate}] Generated {name} ({team} {pos}, pick {item.get('actual_pick') or 'UDFA'}) -> {os.path.basename(out)}")
            return True, None
        except Exception as e:
            with lock:
                completed_count += 1
                failed += 1
                err_msg = f"{name} (ID {pid}): {e}"
                errors.append(err_msg)
                log_fn(f"[rookie_headshots] [{completed_count}/{total_to_generate}] ERROR for {name} ({team}): {e}")
            return False, err_msg

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(process_item, item) for item in needing]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    log_fn(f"[rookie_headshots] Finished Season {season}: {generated} generated, {failed} failed, {already_had} pre-existing.")
    return {
        "season": season,
        "total_rookies": total,
        "already_had": already_had,
        "generated": generated,
        "failed": failed,
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description="Generate headshots for rookies entering a season.")
    parser.add_argument("--season", type=int, default=None, help="Target season (default: current_season from league_state)")
    parser.add_argument("--workers", type=int, default=5, help="Number of concurrent generation workers (default: 5)")
    parser.add_argument("--drafted-only", action="store_true", help="Only generate for prospects with actual_pick in draft_prospects")
    parser.add_argument("--dry-run", action="store_true", help="Count candidates without calling Gemini API")
    args = parser.parse_args()

    report = generate_rookie_headshots(
        season=args.season,
        drafted_only=args.drafted_only,
        max_workers=args.workers,
        dry_run=args.dry_run,
        log_fn=print,
    )
    if report.get("failed", 0) > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
