"""Offseason Editorial Slate Runner for Project Mu.

Dispatches the 6-article Week 24 Offseason Editorial Package:
1. Hero: SUPER_BOWL_GAME_RECAP
2. Secondary 1: SUPER_BOWL_CHAMPION_RETROSPECTIVE
3. Secondary 2: SUPER_BOWL_RUNNER_UP_POSTMORTEM
4. Feature 3: COACHING_CAROUSEL_AUDIT
5. Feature 4: FREE_AGENCY_SIGNING_GRADES
6. Feature 5: NFL_DRAFT_CLASS_RECAP

Usage:
  python3 -m newsroom.offseason_run --season 2035 --input captures/offseason/2035/manifest.json [--dry-run] [--only <event>] [--force]
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import psycopg
from psycopg.rows import dict_row

from db.db_handler import DatabaseHandler
from newsroom.offseason_service import (
    apply_coaching_moves,
    build_event_facts_packet,
    load_manifest,
    preview_manifest,
)
from newsroom.run_context import article_run_context
from newsroom.swarm_dispatcher import SwarmDispatcher

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parent.parent
CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")

OFFSEASON_SLATE_SPEC = [
    {
        "slot": "Hero Slot",
        "article_type": "SUPER_BOWL_GAME_RECAP",
        "event_type": "super_bowl",
        "writer": "BARNWELL",
        "title_hint": "The Super Bowl: How the Game Was Won",
    },
    {
        "slot": "Slot 1",
        "article_type": "SUPER_BOWL_CHAMPION_RETROSPECTIVE",
        "event_type": "super_bowl",
        "writer": "CHADIHA",
        "title_hint": "The Champion's Road to the Lombardi",
    },
    {
        "slot": "Slot 2",
        "article_type": "SUPER_BOWL_RUNNER_UP_POSTMORTEM",
        "event_type": "super_bowl",
        "writer": "SANDO",
        "title_hint": "The Super Bowl Loss: What Comes Next",
    },
    {
        "slot": "Slot 3",
        "article_type": "COACHING_CAROUSEL_AUDIT",
        "event_type": "coaching",
        "writer": "SANDO",
        "title_hint": "The Coaching Carousel: Hires, Firings, and Grades",
    },
    {
        "slot": "Slot 4",
        "article_type": "FREE_AGENCY_SIGNING_GRADES",
        "event_type": "signings",
        "writer": "YATES",
        "title_hint": "The Biggest Signings: Fit, Value, and Risk",
    },
    {
        "slot": "Slot 5",
        "article_type": "NFL_DRAFT_CLASS_RECAP",
        "event_type": "draft",
        "writer": "MEL_KIPER",
        "title_hint": "The Draft Is In: First-Round Classes and Biggest Bets",
    },
]


def resolve_sub_event_slate(only: Optional[str]) -> List[Dict[str, Any]]:
    """Filters the 6-article slate if --only is specified."""
    if not only or only.lower() in ("all", "full"):
        return OFFSEASON_SLATE_SPEC
    key = only.lower().strip()
    filtered = [item for item in OFFSEASON_SLATE_SPEC if item["event_type"] == key]
    if not filtered:
        raise ValueError(f"Unknown --only event filter '{only}'. Must be one of: super_bowl, coaching, signings, draft")
    return filtered


def run_offseason_package(
    season: int,
    manifest_path_or_dict: Union[str, Path, Dict[str, Any]],
    dry_run: bool = False,
    only: Optional[str] = None,
    force: bool = False,
    live: Optional[bool] = None,
    model: str = "gemini-3.8-flash",
    conn_info: Optional[str] = None,
) -> Dict[str, Any]:
    """Coordinates validation, DB state updates, and dispatching of the offseason package."""
    conn_str = conn_info or CONN_INFO
    manifest = load_manifest(manifest_path_or_dict)
    manifest_season = manifest["season"]

    if int(season) != int(manifest_season):
        raise ValueError(f"Season mismatch: CLI/caller requested {season}, but manifest declares {manifest_season}")

    print("======================================================================")
    print(f"🏈 PROJECT MU: OFFSEASON EDITORIAL PACKAGE RUNNER (SEASON {season})")
    print(f"Mode: {'DRY RUN (Preview Only)' if dry_run else 'LIVE DISPATCH'}")
    if only:
        print(f"Sub-Event Scope: {only}")
    print("======================================================================\n")

    # 1. Preview and validate manifest
    preview = preview_manifest(manifest, conn_info=conn_str)
    if not preview["valid"]:
        raise ValueError(f"Manifest validation failed: {preview.get('error')}")

    print(f"📋 Manifest Summary: {preview['summary']}")
    for w in preview.get("warnings", []):
        print(f"⚠️  Warning: {w}")

    # 2. Apply coaching moves
    should_apply_coaching = not only or only.lower() in ("all", "coaching")
    if should_apply_coaching and manifest.get("coaching_moves"):
        print(f"\n👔 Applying coaching moves ({'DRY RUN' if dry_run else 'COMMITTING'})...")
        coach_res = apply_coaching_moves(manifest, dry_run=dry_run, conn_info=conn_str)
        print(f"   -> Touched {len(coach_res['applied_moves'])} teams: {', '.join(m['team'] for m in coach_res['applied_moves'])}")

    # 3. Load draft results if needed
    should_load_draft = not only or only.lower() in ("all", "draft")
    if should_load_draft and manifest.get("draft_results") and not dry_run:
        dr = manifest["draft_results"]
        source_file = dr.get("source_file") if isinstance(dr, dict) else None
        if source_file:
            sf_path = Path(source_file)
            if not sf_path.is_absolute():
                sf_path = ROOT_DIR / sf_path
            if sf_path.exists():
                print(f"\n🎓 Ingesting actual draft results from {sf_path}...")
                try:
                    from pipeline.draft_results import parse_raw_picks, resolve_and_load_picks
                    if sf_path.suffix.lower() == ".csv":
                        import csv
                        with open(sf_path, "r", encoding="utf-8") as fh:
                            raw_rows = list(csv.DictReader(fh))
                    else:
                        with open(sf_path, "r", encoding="utf-8") as fh:
                            raw_rows = json.load(fh)
                    load_res = resolve_and_load_picks(raw_rows, season, dry_run=False)
                    print(f"   -> Draft results loaded: {load_res.get('updated_prospects_count', 32)} selections recorded in draft_prospects.")
                except Exception as ex:
                    print(f"   ⚠️ Could not auto-load draft results: {ex}")

    # 4. Resolve slate of articles to dispatch
    target_articles = resolve_sub_event_slate(only)
    is_live = (not dry_run) if live is None else live
    dispatcher = SwarmDispatcher(debug_mode=dry_run)
    if conn_str:
        dispatcher.db = DatabaseHandler(db_conn_info=conn_str)

    results: Dict[str, Any] = {
        "season": season,
        "week": 24,
        "dry_run": dry_run,
        "scope": only or "all",
        "articles": [],
    }

    # 5. Dispatch each article under strict event cutoff context
    for item in target_articles:
        slot = item["slot"]
        at_id = item["article_type"]
        evt_type = item["event_type"]
        writer = item["writer"]

        print(f"\n----------------------------------------------------------------------")
        print(f"📰 DISPATCHING: [{slot}] {at_id}")
        print(f"   Writer: {writer} | Event Cutoff: {evt_type}")
        print(f"----------------------------------------------------------------------")

        # Check if already present in news_feed
        with psycopg.connect(conn_str, row_factory=dict_row) as conn:
            existing = conn.execute("""
                SELECT id, headline FROM news_feed
                WHERE season = %s AND week = 24 AND slot = %s AND article_type = %s
            """, (season, slot, at_id)).fetchone()

            if existing and not force:
                print(f"⏭️  [SKIPPED] Article #{existing['id']} already exists in news_feed for {slot}. Use --force to overwrite.")
                results["articles"].append({
                    "slot": slot,
                    "article_type": at_id,
                    "status": "SKIPPED_EXISTING",
                    "article_id": existing["id"],
                    "headline": existing["headline"],
                })
                continue
            elif existing and force:
                print(f"🗑️  [FORCE] Deleting existing article #{existing['id']} to regenerate cleanly...")
                conn.execute("DELETE FROM news_feed WHERE id = %s", (existing["id"],))

        # Build facts packet for anti-leakage grounding
        facts = build_event_facts_packet(evt_type, manifest, conn_info=conn_str)

        # Build prompt assignment brief
        brief = {
            "season": season,
            "calendar_week": 24,
            "recap_week": 23,
            "edition": "OFFSEASON",
            "narrative_anchor_event": evt_type,
            "facts_packet": facts,
        }

        desk_note = (
            f"OFFSEASON ASSIGNMENT: Write '{item['title_hint']}' for the Week 24 Offseason edition. "
            f"Adhere strictly to your anti-leakage event cutoff ({evt_type}). "
            f"Facts packet:\n{json.dumps(facts, indent=1)}"
        )

        agent_rec = dispatcher.get_agent_record(writer)
        compiled_prompt = dispatcher.compile_agent_prompt(
            agent_id=agent_rec[0],
            archetype=agent_rec[1],
            persistent_memory=agent_rec[2],
            handle=agent_rec[3],
            display_name=agent_rec[4],
            assigned_team_id=None,
            brief=brief,
            season=season,
            week=24,
            content_type="ARTICLE",
            article_type=at_id,
            desk_note=desk_note,
        )

        # Execute inside event-relative cutoff context
        with article_run_context(season, 24, offseason_event=evt_type):
            exec_res = dispatcher._execute_agent(
                compiled_prompt=compiled_prompt,
                season=season,
                week=24,
                team_id=None,
                live=is_live,
                model=model,
                save_to_db=is_live,
                slot=slot,
                article_type=at_id,
            )

        results["articles"].append({
            "slot": slot,
            "article_type": at_id,
            "status": "DISPATCHED" if is_live else "PREVIEWED",
            "article_id": exec_res.get("article_id"),
            "headline": exec_res.get("headline"),
            "writer": writer,
        })

    print("\n======================================================================")
    print("✅ OFFSEASON EDITORIAL SLATE RUN COMPLETE")
    print(f"Total Articles Processed: {len(results['articles'])}")
    for a in results["articles"]:
        print(f" • [{a['slot']}] {a['article_type']} -> {a.get('headline') or a['status']}")
    print("======================================================================\n")

    return results


def main():
    parser = argparse.ArgumentParser(description="Run the Project Mu Offseason Editorial Slate")
    parser.add_argument("--season", type=int, required=True, help="Concluded season year (e.g. 2035)")
    parser.add_argument("--input", required=True, help="Path to manifest.json")
    parser.add_argument("--dry-run", action="store_true", help="Preview manifest & compilation without DB mutations or live LLM calls")
    parser.add_argument("--only", choices=["super_bowl", "coaching", "signings", "draft"], help="Run only a specific sub-event")
    parser.add_argument("--force", action="store_true", help="Overwrite existing Week 24 articles in news_feed")
    parser.add_argument("--model", default="gemini-3.8-flash", help="Gemini model to use for live execution")

    args = parser.parse_args()

    try:
        run_offseason_package(
            season=args.season,
            manifest_path_or_dict=args.input,
            dry_run=args.dry_run,
            only=args.only,
            force=args.force,
            model=args.model,
        )
    except Exception as e:
        logger.exception("Offseason run failed")
        print(f"\n❌ Offseason run failed: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
