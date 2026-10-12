import argparse
import json
import psycopg
import sys
import signal
try:
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
except Exception:
    pass
from newsroom.swarm_dispatcher import SwarmDispatcher, build_weekly_assignment_brief

def get_default_season_and_week(db_conn_info: str):
    """Finds the latest season and completed week from the database."""
    with psycopg.connect(db_conn_info) as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT season, week 
                FROM games 
                WHERE status = 'COMPLETED' 
                ORDER BY season DESC, week DESC 
                LIMIT 1;
            """)
            row = cur.fetchone()
            if row:
                return row[0], row[1]
            return 2035, 1

def resolve_team_id(db_conn_info: str, team_input: str) -> tuple:
    """Resolves team name/abbreviation/id to (team_id, team_name)."""
    with psycopg.connect(db_conn_info) as conn:
        with conn.cursor() as cur:
            if team_input.isdigit():
                cur.execute("SELECT id, name FROM teams WHERE id = %s;", (int(team_input),))
            else:
                cur.execute("""
                    SELECT id, name FROM teams 
                    WHERE abbreviation ILIKE %s OR name ILIKE %s 
                    LIMIT 1;
                """, (team_input, f"%{team_input}%"))
            row = cur.fetchone()
            if row:
                return row[0], row[1]
    return 12, "Dallas Cowboys"

def main():
    parser = argparse.ArgumentParser(description="Test and inspect Agent Swarm wakeups for any season and week.")
    parser.add_argument("--season", type=int, default=None, help="NFL season year (e.g. 2035). Defaults to latest in DB.")
    parser.add_argument("--week", type=int, default=None, help="NFL week (e.g. 17). Defaults to latest completed week in DB.")
    parser.add_argument("--mode", choices=["all", "national", "local"], default="all", help="Which wakeup pipeline to test.")
    parser.add_argument("--team", type=str, default="DAL", help="Team name, abbreviation, or ID for local pipeline test (default: DAL).")
    parser.add_argument("--archetype", type=str, default=None, help="Target a specific agent archetype (e.g. BARNWELL, SANDO, JOYNER, ROSENTHAL, CHADIHA, MCAFEE, KIMES).")
    parser.add_argument("--article-type", type=str, default=None, help="Optionally target/override the Article Type (e.g. MACRO_SLATE_RECAP, PLAYER_DEEP_PROFILE, FRANCHISE_STATE_OF_THE_UNION, SINGLE_GAME_SCHEMATIC_AUTOPSY).")
    parser.add_argument("--live", action="store_true", help="Send the compiled prompt to Gemini API and print the generated article/tweet.")
    parser.add_argument("--model", type=str, default="gemini-3.8-flash", help="Gemini model name to use for live generation (default: gemini-3.8-flash).")
    parser.add_argument("--article-count", type=int, default=6, help="Number of national articles the orchestrator should commission (default: 6).")
    parser.add_argument("--api-key", type=str, default=None, help="Optional Gemini API key override (defaults to GEMINI_API_KEY env var).")
    args = parser.parse_args()

    sd = SwarmDispatcher(api_key=args.api_key, debug_mode=True)
    default_season, default_week = get_default_season_and_week(sd.db.db_conn_info)

    season = args.season or default_season
    week = args.week or default_week

    print("=" * 70)
    print(f"🏈 SWARM DEBUG HARNESS: Season {season}, Week {week}" + (" [LIVE GEMINI CALL]" if args.live else " [PROMPT INSPECTION]"))
    print("=" * 70)

    brief = build_weekly_assignment_brief(sd.db.db_conn_info, season, week)
    print(f"-> Generated Dynamic Assignment Brief from completed DB games:")
    print(json.dumps(brief, indent=2))
    print("=" * 70)

    # 1. Targeted Single Archetype Mode (Testing: Never save to news_feed)
    if args.archetype:
        sd.dispatch_single_archetype(
            args.archetype, brief, season, week, live=args.live, model=args.model, save_to_db=False, article_type=args.article_type
        )
        return

    # 2. National Pipeline (Testing: Never save to news_feed)
    if args.mode in ["all", "national"]:
        print("\n" + "*" * 60)
        print(f"TEST 1: NATIONAL PIPELINE (Season {season}, Week {week})")
        print("*" * 60)
        sd.dispatch_weekly_national(brief, season, week, article_count=args.article_count, live=args.live, model=args.model, save_to_db=False)

    # 3. Local Team Pipeline (Testing: Never save to news_feed)
    if args.mode in ["all", "local"]:
        team_id, team_name = resolve_team_id(sd.db.db_conn_info, args.team)
        print("\n" + "*" * 60)
        print(f"TEST 2: LOCAL TEAM PIPELINE: {team_name} (ID: {team_id}, Season {season}, Week {week})")
        print("*" * 60)
        sd.dispatch_team_local(team_id, brief, season, week, live=args.live, model=args.model, save_to_db=False)

if __name__ == "__main__":
    main()
