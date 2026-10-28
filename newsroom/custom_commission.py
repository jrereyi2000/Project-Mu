"""
newsroom/custom_commission.py

Custom article commissioning engine for Project Mu.
Turns a user-provided prompt into an article type, assigns an appropriate writer,
and commissions the article into the weekly slate (routed to National Newsroom
or Mu Fantasy Desk).
"""

import argparse
import json
import os
import re
import sys
import psycopg
from psycopg.rows import dict_row

from db.db_handler import DatabaseHandler
from newsroom.swarm_dispatcher import (
    SwarmDispatcher,
    build_weekly_assignment_brief,
    canonical_writer_archetype,
)
from newsroom.fantasy_desk import (
    fantasy_phase,
    fantasy_week_label,
)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_DIR = os.path.join(ROOT, "newsroom", "config")


def get_league_state(conn_info: str = None) -> tuple[int, int]:
    """Returns (current_season, current_week) from league_state."""
    conn_info = conn_info or os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
    with psycopg.connect(conn_info, row_factory=dict_row) as conn:
        row = conn.execute("SELECT current_season, current_week FROM league_state WHERE id = 1").fetchone()
        if row:
            return int(row["current_season"]), int(row["current_week"])
    return (2035, 18)


def load_teams(conn_info: str = None) -> list[dict]:
    """Returns all 32 teams [{id, name, city, abbreviation}]."""
    conn_info = conn_info or os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
    try:
        with psycopg.connect(conn_info, row_factory=dict_row) as conn:
            return conn.execute("SELECT id, city, name, abbreviation FROM teams ORDER BY city, name").fetchall()
    except Exception as e:
        print(f"⚠️ Could not load teams: {e}", file=sys.stderr)
        return []


def load_article_catalog() -> tuple[dict, dict, dict]:
    """Loads article_types.json, fantasy_writers.json, and agent_personas.json."""
    with open(os.path.join(CONFIG_DIR, "article_types.json")) as f:
        article_types = json.load(f).get("article_types", {})

    with open(os.path.join(CONFIG_DIR, "fantasy_writers.json")) as f:
        fantasy_writers = json.load(f).get("writers", {})

    with open(os.path.join(CONFIG_DIR, "agent_personas.json")) as f:
        personas = json.load(f)
        archetypes = personas.get("archetypes", {})

    return article_types, fantasy_writers, archetypes


def classify_prompt(
    prompt: str,
    season: int = None,
    week: int = None,
    api_key: str = None,
    conn_info: str = None,
    model: str = "gemini-3.8-flash",
) -> dict:
    """Uses Gemini Flash to classify a user article prompt into:
    - target_desk: 'NATIONAL' or 'FANTASY'
    - article_type_id: key in article_types.json
    - writer_archetype: qualified writer
    - team_id: target team ID if applicable, else None
    - desk_note: distilled specific story angle
    - rationale: brief explanation of choice
    """
    from google import genai
    from google.genai import types

    if season is None or week is None:
        curr_s, curr_w = get_league_state(conn_info)
        season = season or curr_s
        week = week or curr_w

    api_key = api_key or os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        env_file = os.path.join(ROOT, ".env")
        if os.path.exists(env_file):
            with open(env_file) as ef:
                for line in ef:
                    line = line.strip()
                    if line.startswith("GEMINI_API_KEY=") or line.startswith("GOOGLE_API_KEY="):
                        api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break

    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for prompt classification.")

    client = genai.Client(api_key=api_key)
    article_types, fantasy_writers, national_personas = load_article_catalog()
    teams = load_teams(conn_info)

    # Build concise taxonomies for the system prompt
    national_types_summary = []
    fantasy_types_summary = []
    for k, v in article_types.items():
        entry = f"- `{k}` ({v.get('name', k)}): {v.get('focus', '')} [Allowed: {', '.join(v.get('allowed_writers', []))}]"
        if k.startswith("FANTASY_"):
            fantasy_types_summary.append(entry)
        else:
            national_types_summary.append(entry)

    teams_summary = [f"{t['id']}: {t['city']} {t['name']} ({t['abbreviation']})" for t in teams]

    national_writers_list = [
        "BARNWELL (Analytics, regression, unexpected efficiency)",
        "SANDO (Front office, coaching staff, contract/roster architecture)",
        "JOYNER (Scientific tape breakdown, efficiency splits, film study)",
        "ROSENTHAL (Aesthetics, vibes, watchability, unexpected chaos)",
        "CHADIHA (Narrative, veteran legacy, championship pedigree)",
        "LOCAL_BEAT_REPORTER (Daily franchise beat, locker room, local reaction - requires team_id)",
        "SIMMONS (Pop-culture, historical parallels, opinion column)",
        "KIMES (Schematic dissection, secondary play, modern offense)",
        "ORLOVSKY (Quarterback play, protection calls, red zone execution)",
        "MEL_KIPER (Draft prospects, mock draft, draft order, team needs)",
    ]

    fantasy_writers_list = [
        "BERRY (Love/Hate, emotional anecdotes, gut calls, waivers)",
        "YATES (Field Notes, lists, rankings, start/sit tiers, facts)",
        "FANTASY_GURU (Algorithmic projections, matchup exploits, defense vs position)",
    ]

    system_instruction = f"""You are the Editorial Director of Project Mu, an NFL media simulation network.
We are in Season {season}, Week {week}.
Your task is to analyze an article assignment prompt and assign:
1. `target_desk`: Exactly "NATIONAL" or "FANTASY".
   - Use "FANTASY" if the prompt asks about fantasy football, waiver wire, start/sit, fantasy trade targets, draft tiers, ADP, PPR scoring, sleepers, or busts.
   - Use "NATIONAL" for general NFL news, game recaps, previews, player deep dives, power rankings, coach hot seats, team state of the unions, all-pro awards, draft boards, or beat reporting.
2. `article_type_id`: Must be one of the known article type IDs from the provided list.
3. `writer_archetype`: The most appropriate writer archetype matching the tone and allowed writers.
4. `team_id`: Integer ID of the target team if the prompt focuses on a single franchise, or null if league-wide.
5. `desk_note`: Distill the user's prompt into a punchy, mandatory assignment directive for the writer agent, specifying the exact angle, players, or games to investigate.
6. `rationale`: 1 short sentence on why this writer and format fit the prompt.

--- AVAILABLE NATIONAL ARTICLE TYPES ---
{chr(10).join(national_types_summary)}

--- AVAILABLE FANTASY ARTICLE TYPES ---
{chr(10).join(fantasy_types_summary)}

--- NATIONAL WRITERS ---
{chr(10).join(national_writers_list)}

--- FANTASY WRITERS ---
{chr(10).join(fantasy_writers_list)}

--- NFL TEAMS (ID: Name) ---
{chr(10).join(teams_summary)}
"""

    user_query = f"User article prompt: {prompt}\n\nPlease classify this article prompt and output valid JSON."

    response_schema = {
        "type": "OBJECT",
        "properties": {
            "target_desk": {"type": "STRING", "enum": ["NATIONAL", "FANTASY"]},
            "article_type_id": {"type": "STRING"},
            "writer_archetype": {"type": "STRING"},
            "team_id": {"type": "INTEGER", "nullable": True},
            "desk_note": {"type": "STRING"},
            "rationale": {"type": "STRING"},
        },
        "required": ["target_desk", "article_type_id", "writer_archetype", "desk_note", "rationale"],
    }

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        temperature=0.2,
        response_mime_type="application/json",
        response_schema=response_schema,
    )

    resp = client.models.generate_content(
        model=model,
        contents=user_query,
        config=config,
    )
    from db.llm_usage import log_response
    log_response("custom_commission", "classify_prompt", model, resp)

    raw_text = resp.text or "{}"
    data = json.loads(raw_text)

    # Sanity checks & fallbacks
    desk = (data.get("target_desk") or "NATIONAL").upper()
    if desk not in ("NATIONAL", "FANTASY"):
        desk = "NATIONAL"

    at_id = data.get("article_type_id")
    if not at_id or at_id not in article_types:
        if desk == "FANTASY":
            at_id = "FANTASY_WEEKLY_REACTIONS"
        else:
            at_id = "NATIONAL_OPINION_COLUMN"

    at_info = article_types.get(at_id, {})
    allowed = at_info.get("allowed_writers", [])

    writer = canonical_writer_archetype(data.get("writer_archetype") or "")
    if desk == "FANTASY":
        if writer not in fantasy_writers:
            writer = allowed[0] if allowed else "BERRY"
    else:
        if allowed and writer not in allowed and writer != "LOCAL_BEAT_REPORTER":
            # Keep columnist if valid
            if at_id != "NATIONAL_OPINION_COLUMN":
                writer = allowed[0]

    return {
        "target_desk": desk,
        "article_type_id": at_id,
        "article_type_name": at_info.get("name", at_id),
        "writer_archetype": writer,
        "team_id": data.get("team_id"),
        "desk_note": data.get("desk_note") or prompt,
        "rationale": data.get("rationale") or "Classified based on story angle.",
    }


def commission_custom_article(
    prompt: str,
    season: int = None,
    week: int = None,
    target_desk: str = "NATIONAL",
    article_type_id: str = None,
    writer_archetype: str = None,
    team_id: int = None,
    desk_note: str = None,
    slot: str = None,
    live: bool = True,
    model: str = "gemini-3.8-flash",
    save_to_db: bool = True,
    api_key: str = None,
) -> dict:
    """Executes the commissioning and writing of a custom article."""
    curr_s, curr_w = get_league_state()
    season = season or curr_s
    week = week or curr_w

    dispatcher = SwarmDispatcher(api_key=api_key)
    article_types, fantasy_writers, national_personas = load_article_catalog()

    desk = (target_desk or "NATIONAL").upper()
    at_id = article_type_id
    if not at_id or at_id not in article_types:
        at_id = "FANTASY_WEEKLY_REACTIONS" if desk == "FANTASY" else "SINGLE_GAME_SCHEMATIC_AUTOPSY"

    at_info = article_types.get(at_id, {})
    writer = canonical_writer_archetype(writer_archetype or "")

    print(f"\n=======================================================")
    print(f"🎯 COMMISSIONING CUSTOM ARTICLE (Season {season}, Week {week})")
    print(f"   Desk:         {desk}")
    print(f"   Article Type: {at_info.get('name', at_id)} ({at_id})")
    print(f"   Writer:       {writer}")
    print(f"   Team ID:      {team_id}")
    print(f"   Prompt:       {prompt}")
    print(f"   Desk Note:    {desk_note}")
    print(f"=======================================================\n")

    if desk == "FANTASY":
        fantasy_week = max(1, min(week, 18))
        phase = fantasy_phase(fantasy_week) or "season_review"
        assigned_slot = slot or "Fantasy Spotlight"
        if not writer or writer not in fantasy_writers:
            allowed = at_info.get("allowed_writers", [])
            writer = allowed[0] if allowed else "BERRY"

        agent_rec = dispatcher.get_agent_record(writer)
        brief = {
            "desk": "Mu Fantasy",
            "season": season,
            "publication_week": fantasy_week,
            "fantasy_phase": phase,
            "this_week": fantasy_week_label(fantasy_week) if fantasy_week > 1 else "preseason draft prep",
            "results_available_through_week": max(0, fantasy_week - 1),
            "scoring": "full PPR",
            "custom_prompt": prompt,
        }

        compiled_prompt = dispatcher.compile_agent_prompt(
            agent_id=agent_rec[0],
            archetype=agent_rec[1],
            persistent_memory=agent_rec[2],
            handle=agent_rec[3],
            display_name=agent_rec[4],
            assigned_team_id=None,
            brief=brief,
            season=season,
            week=fantasy_week,
            content_type="ARTICLE",
            article_type=at_id,
            desk_note=desk_note or prompt,
        )

        exec_res = dispatcher._execute_agent(
            compiled_prompt=compiled_prompt,
            season=season,
            week=fantasy_week,
            team_id=None,
            live=live,
            model=model,
            save_to_db=save_to_db,
            slot=assigned_slot,
            article_type=at_id,
        )

        art_id = exec_res.get("article_id")
        return {
            **exec_res,
            "target_desk": "FANTASY",
            "url_hash": f"#article-fantasy-{art_id}" if art_id else "#fantasy",
        }

    else:
        # National Newsroom
        assigned_slot = slot or "Special Commission"
        brief = build_weekly_assignment_brief(dispatcher.db.db_conn_info, season, week)
        brief["custom_prompt"] = prompt

        target_team_id = int(team_id) if team_id else None
        if writer == "LOCAL_BEAT_REPORTER":
            agent_rec = dispatcher._get_distinct_beat_reporter(set(), season, team_id=target_team_id)
            if not agent_rec:
                agent_rec = dispatcher.get_agent_record("CHADIHA")
        else:
            agent_rec = dispatcher.get_agent_record(writer or "BARNWELL")

        compiled_prompt = dispatcher.compile_agent_prompt(
            agent_id=agent_rec[0],
            archetype=agent_rec[1],
            persistent_memory=agent_rec[2],
            handle=agent_rec[3],
            display_name=agent_rec[4],
            assigned_team_id=target_team_id if writer == "LOCAL_BEAT_REPORTER" else None,
            brief=brief,
            season=season,
            week=week,
            content_type="ARTICLE",
            article_type=at_id,
            desk_note=desk_note or prompt,
        )

        exec_res = dispatcher._execute_agent(
            compiled_prompt=compiled_prompt,
            season=season,
            week=week,
            team_id=target_team_id if writer == "LOCAL_BEAT_REPORTER" else None,
            live=live,
            model=model,
            save_to_db=save_to_db,
            slot=assigned_slot,
            article_type=at_id,
        )

        art_id = exec_res.get("article_id")
        return {
            **exec_res,
            "target_desk": "NATIONAL",
            "url_hash": f"#article-{art_id}" if art_id else "",
        }


def main():
    parser = argparse.ArgumentParser(description="Commission a custom article for Project Mu")
    parser.add_argument("--prompt", required=True, help="Story prompt or assignment angle")
    parser.add_argument("--season", type=int, help="Season year (defaults to current)")
    parser.add_argument("--week", type=int, help="Publication week (defaults to current)")
    parser.add_argument("--desk", choices=["NATIONAL", "FANTASY"], help="Target desk (auto-classified if omitted)")
    parser.add_argument("--type", help="Article type ID from article_types.json")
    parser.add_argument("--writer", help="Writer archetype")
    parser.add_argument("--team", type=int, help="Team ID if team-specific")
    parser.add_argument("--desk-note", help="Mandatory directive for the writer")
    parser.add_argument("--slot", help="Slot name override")
    parser.add_argument("--classify-only", action="store_true", help="Only classify and print JSON, do not write")
    parser.add_argument("--dry-run", action="store_true", help="Run without live model call or DB write")
    parser.add_argument("--model", default="gemini-3.8-flash", help="Gemini model")

    args = parser.parse_args()

    season = args.season
    week = args.week
    if not season or not week:
        s, w = get_league_state()
        season = season or s
        week = week or w

    if args.classify_only or not (args.desk and args.type and args.writer):
        print(f"🔍 Classifying prompt for Season {season}, Week {week}...")
        classification = classify_prompt(args.prompt, season, week, model=args.model)
        if args.classify_only:
            print(json.dumps(classification, indent=2))
            return

        desk = args.desk or classification["target_desk"]
        at_id = args.type or classification["article_type_id"]
        writer = args.writer or classification["writer_archetype"]
        team_id = args.team if args.team is not None else classification["team_id"]
        desk_note = args.desk_note or classification["desk_note"]
    else:
        desk = args.desk
        at_id = args.type
        writer = args.writer
        team_id = args.team
        desk_note = args.desk_note or args.prompt

    res = commission_custom_article(
        prompt=args.prompt,
        season=season,
        week=week,
        target_desk=desk,
        article_type_id=at_id,
        writer_archetype=writer,
        team_id=team_id,
        desk_note=desk_note,
        slot=args.slot,
        live=not args.dry_run,
        model=args.model,
        save_to_db=not args.dry_run,
    )

    print("\n=======================================================")
    print("✅ COMMISSION COMPLETE")
    print(f"Headline:   {res.get('headline')}")
    print(f"Article ID: {res.get('article_id')}")
    print(f"Slot:       {res.get('slot')}")
    print(f"URL Route:  {res.get('url_hash')}")
    print("=======================================================\n")


if __name__ == "__main__":
    main()
