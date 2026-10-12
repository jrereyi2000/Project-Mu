#!/usr/bin/env python3
"""
Test Harness for NFL Editorial Workflow & Media Swarm
======================================================
Usage Examples:
  # 1. Dry run prompt & commission inspection for Week 18 (no API calls, no DB writes)
  python3 test_editorial_flow.py --season 2035 --week 18 --dry-run

  # 2. Mock generation with DB save & entity verification (instant, zero LLM cost)
  python3 test_editorial_flow.py --season 2035 --week 18 --save-to-db --count 3

  # 3. Live LLM execution for a single article type
  python3 test_editorial_flow.py --season 2035 --week 18 --article-type MACRO_SLATE_RECAP --live

  # 4. View existing database records for a given season and week
  python3 test_editorial_flow.py --season 2035 --week 18 --view-db
"""

import argparse
import sys
import json
import psycopg
from psycopg.rows import dict_row

from newsroom.swarm_dispatcher import SwarmDispatcher, build_weekly_assignment_brief
from db.db_handler import DatabaseHandler

DATABASE_URL = "host=localhost dbname=madden user=postgres password=postgres"

def print_banner(text: str, char: str = "=", width: int = 80):
    print("\n" + char * width)
    print(f" {text}")
    print(char * width)

def view_db_records(season: int, week: int):
    """Inspects and pretty-prints all articles and relational tags stored in the DB for season/week."""
    print_banner(f"DATABASE RECORDS: Season {season} | Week {week}", "#")
    with psycopg.connect(DATABASE_URL, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, season, week, content_type, persona, article_type, slot, headline, excerpt, 
                       LENGTH(content) AS content_chars, created_at
                FROM news_feed
                WHERE season = %s AND week = %s AND content_type = 'ARTICLE'
                ORDER BY id ASC;
            """, (season, week))
            articles = cur.fetchall()

            if not articles:
                print(f"⚠️  No articles found in news_feed for Season {season}, Week {week}.")
                return

            print(f"✅ Found {len(articles)} articles in news_feed:\n")
            for idx, a in enumerate(articles, 1):
                art_id = a["id"]
                print(f"[{idx}] ARTICLE ID #{art_id}")
                print(f"    • Slot:         {a.get('slot') or 'Unslotted'}")
                print(f"    • Article Type: {a.get('article_type') or 'N/A'}")
                print(f"    • Persona:      {a['persona']}")
                print(f"    • Headline:     {a['headline']}")
                print(f"    • Excerpt:      {a['excerpt']}")
                print(f"    • Length:       {a['content_chars']} characters")
                print(f"    • Created:      {a['created_at']}")

                # Fetch tagged teams
                cur.execute("""
                    SELECT t.name, t.city, t.abbreviation, at.mention_type, at.mention_count
                    FROM article_teams at
                    JOIN teams t ON at.team_id = t.id
                    WHERE at.article_id = %s
                    ORDER BY at.mention_type = 'PRIMARY' DESC, at.mention_count DESC;
                """, (art_id,))
                teams = cur.fetchall()
                if teams:
                    team_str = ", ".join([f"{t['city']} {t['name']} ({t['mention_type']}, {t['mention_count']}x)" for t in teams])
                    print(f"    • Teams:     {team_str}")

                # Fetch tagged players
                cur.execute("""
                    SELECT p.full_name, ap.mention_type, ap.mention_count, t.abbreviation
                    FROM article_players ap
                    JOIN players p ON ap.player_id = p.id
                    LEFT JOIN teams t ON ap.team_id = t.id
                    WHERE ap.article_id = %s
                    ORDER BY ap.mention_type = 'PRIMARY' DESC, ap.mention_count DESC;
                """, (art_id,))
                players = cur.fetchall()
                if players:
                    player_str = ", ".join([f"{p['full_name']} [{p['abbreviation'] or 'FA'}] ({p['mention_type']}, {p['mention_count']}x)" for p in players])
                    print(f"    • Players:   {player_str}")
                print("-" * 80)

def run_test_harness(
    season: int,
    week: int,
    article_type: str = None,
    writer_archetype: str = None,
    count: int = 4,
    live: bool = False,
    save_to_db: bool = False,
    dry_run: bool = False,
    clean_after: bool = False,
    workers: int = 5
):
    print_banner(f"EDITORIAL TEST HARNESS: Season {season} | Week {week}")
    print(f"• Mode:          {'DRY RUN (Inspect Prompts)' if dry_run else ('LIVE LLM (Gemini)' if live else 'MOCK GENERATION')}")
    print(f"• Target Count:  {count if not article_type else 1}")
    print(f"• Concurrency:   {workers} workers ({'Parallel' if workers > 1 else 'Sequential'})")
    print(f"• Save to DB:    {save_to_db and not dry_run}")
    print(f"• Clean After:   {clean_after}")

    # 1. Build Dual-Horizon Assignment Brief
    print("\n[Step 1] Building Dual-Horizon Weekly Assignment Brief...")
    brief = build_weekly_assignment_brief(DATABASE_URL, season, week)
    print(f" -> Calendar Week:        {brief.get('calendar_week')}")
    print(f" -> Recap Week Target:    {brief.get('recap_week')} (Concluded Games: {brief['recap_slate']['total_games']})")
    print(f" -> Preview Week Target:  {brief.get('calendar_week')} (Scheduled Games: {brief['preview_slate']['total_games']})")
    print(f" -> Standout Stars:       {len(brief.get('standout_performances', []))} standout statlines loaded")

    dispatcher = SwarmDispatcher(debug_mode=True)
    created_article_ids = []

    # 2. Determine Slate of Articles to Run
    if article_type:
        at_id = article_type.upper().strip()
        at_info = dispatcher.article_types_data.get(at_id)
        if not at_info:
            print(f"❌ Error: Article type '{at_id}' not found in taxonomy.")
            sys.exit(1)
        allowed = at_info.get("allowed_writers", ["BARNWELL"])
        arch = writer_archetype.upper().strip() if writer_archetype else allowed[0]
        agent_rec = dispatcher.get_agent_record(arch)
        items_to_run = [{
            "slot": "Targeted Slot",
            "article_type_id": at_id,
            "article_type_name": at_info.get("name", at_id),
            "archetype": arch,
            "agent_record": agent_rec
        }]
    else:
        print("\n[Step 2] Commissioning Slate via Editorial Matrix...")
        items_to_run = dispatcher.commission_weekly_editorial_slate(season, week, target_articles=count)

    print_banner(f"PLANNED EDITORIAL SLATE ({len(items_to_run)} Commissioned Articles)", "-")
    for idx, item in enumerate(items_to_run, 1):
        slot = item.get("slot", f"Slot {idx}")
        rec = item["agent_record"]
        print(f" [{idx}] [{slot}] {item['article_type_name']} ({item['article_type_id']})")
        print(f"     -> Writer: {rec[4] or rec[0]} ({rec[1]}) | Handle: {rec[3]}")

    # 3. Process Each Commissioned Article
    compiled_tasks = []
    for idx, item in enumerate(items_to_run, 1):
        rec = item["agent_record"]
        at_id = item["article_type_id"]
        display = rec[4] or rec[0]

        compiled_prompt = dispatcher.compile_agent_prompt(
            agent_id=rec[0],
            archetype=rec[1],
            persistent_memory=rec[2],
            handle=rec[3],
            display_name=rec[4],
            assigned_team_id=rec[5],
            brief=brief,
            season=season,
            week=week,
            content_type="ARTICLE",
            article_type=at_id
        )
        compiled_tasks.append({
            "idx": idx,
            "item": item,
            "prompt": compiled_prompt,
            "rec": rec,
            "display": display,
            "at_id": at_id,
        })

    # Print summary / dry run preview
    for task in compiled_tasks:
        idx = task["idx"]
        item = task["item"]
        display = task["display"]
        compiled_prompt = task["prompt"]

        print_banner(f"[{idx}/{len(compiled_tasks)}] Commissioned: {item['article_type_name']} ({display})", "=")
        print(f"• Slot:             {item.get('slot', f'Slot {idx}')}")
        print(f"• Temporal Horizon: {compiled_prompt.get('temporal_mode')}")
        print(f"• Recap Target:     Week {compiled_prompt.get('recap_week')}")
        print("\n--- COMPILED TASK PROMPT PREVIEW ---")
        preview_prompt = compiled_prompt["task_instruction"]
        for l in preview_prompt.splitlines()[:20]:
            print("  |", l)
        print("  | ... [Prompt truncated for display] ...\n")

    if dry_run:
        print("⚡ [DRY RUN] Skipping generation and database persistence.")
        return

    # Worker function for parallel or sequential execution
    def _execute_single_task(task):
        t_item = task["item"]
        t_display = task["display"]
        t_prompt = task["prompt"]
        t_rec = task["rec"]
        t_idx = task["idx"]
        slot_name = t_item.get("slot", f"Slot {t_idx}")
        at_id = task["at_id"]

        print(f"🚀 [WORKER START {t_idx}/{len(compiled_tasks)}] [{slot_name}] {t_item['article_type_name']} ({t_display})...")
        res = dispatcher._execute_agent(
            compiled_prompt=t_prompt,
            season=season,
            week=week,
            team_id=t_rec[5],
            live=live,
            save_to_db=save_to_db,
            slot=slot_name,
            article_type=at_id
        )
        headline = res.get("headline") if isinstance(res, dict) else None
        excerpt = res.get("excerpt") if isinstance(res, dict) else None
        art_id = res.get("article_id") if isinstance(res, dict) else None
        print(f"✅ [WORKER FINISHED {t_idx}/{len(compiled_tasks)}] [{slot_name}] {t_item['article_type_name']} ({t_display})")
        if art_id:
            print(f"   💾 Saved to news_feed ID: #{art_id}")
            print(f"   • Slot:     {slot_name}")
            print(f"   • Type:     {at_id}")
            print(f"   • Headline: {headline}")
            print(f"   • Excerpt:  {excerpt}")
        return art_id

    # Execute tasks (Parallel if workers > 1)
    if workers > 1 and len(compiled_tasks) > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        print_banner(f"EXECUTING {len(compiled_tasks)} ARTICLES IN PARALLEL ({workers} WORKERS)", "*")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_task = {executor.submit(_execute_single_task, t): t for t in compiled_tasks}
            for fut in as_completed(future_to_task):
                try:
                    art_id = fut.result()
                    if art_id:
                        created_article_ids.append(art_id)
                except Exception as exc:
                    print(f"❌ Article task raised an exception: {exc}")
    else:
        print_banner(f"EXECUTING {len(compiled_tasks)} ARTICLES SEQUENTIALLY", "*")
        for task in compiled_tasks:
            art_id = _execute_single_task(task)
            if art_id:
                created_article_ids.append(art_id)

    # 4. Show DB Summary if Saved
    if save_to_db and not dry_run:
        view_db_records(season, week)

        if clean_after and created_article_ids:
            print(f"\n🧹 Cleaning up {len(created_article_ids)} test article rows from database...")
            with psycopg.connect(DATABASE_URL) as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM news_feed WHERE id = ANY(%s);", (created_article_ids,))
                conn.commit()
            print("✅ Test cleanup complete.")

def main():
    parser = argparse.ArgumentParser(description="Project Mu Editorial Workflow Testing Harness")
    parser.add_argument("--season", type=int, default=2035, help="NFL season year (default: 2035)")
    parser.add_argument("--week", type=int, default=18, help="Editorial slate calendar week (default: 18)")
    parser.add_argument("--article-type", type=str, default=None, help="Target a single specific article type (e.g. MACRO_SLATE_RECAP, POWER_RANKINGS)")
    parser.add_argument("--writer", type=str, default=None, help="Specific writer archetype for targeted test (e.g. BARNWELL, SANDO)")
    parser.add_argument("--count", type=int, default=None, help="Number of articles to commission from matrix (default: full matrix slate of 10 articles)")
    parser.add_argument("--live", action="store_true", help="Trigger live Gemini 3.8 Flash API execution with autonomous ReAct DB tools")
    parser.add_argument("--save-to-db", action="store_true", help="Save outputs and entity tags to PostgreSQL news_feed")
    parser.add_argument("--dry-run", action="store_true", help="Inspect prompts and commissioning without executing model or writing to DB")
    parser.add_argument("--clean-after", action="store_true", help="Delete created database records after test run verification")
    parser.add_argument("--view-db", action="store_true", help="Only view existing database records for this season and week")
    parser.add_argument("--workers", type=int, default=5, help="Number of concurrent worker threads (default: 5, set 1 for sequential)")

    args = parser.parse_args()

    if args.view_db:
        view_db_records(args.season, args.week)
    else:
        run_test_harness(
            season=args.season,
            week=args.week,
            article_type=args.article_type,
            writer_archetype=args.writer,
            count=args.count,
            live=args.live,
            save_to_db=args.save_to_db,
            dry_run=args.dry_run,
            clean_after=args.clean_after,
            workers=args.workers
        )

if __name__ == "__main__":
    main()
