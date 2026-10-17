"""Napper Draft Simulation Engine (engines/napper_draft_engine.py).

Powers the Interactive Draft Room and Mock Draft Simulator for Project Mu's Napper fantasy platform:
- create_draft_session: Initializes mock or league snake drafts, pre-populating all picks.
- get_draft_state: Returns full draft state, picks matrix, teams, on-the-clock info, and results.
- claim_slot: Allows users to change their draft position in mock drafts.
- start_draft: Begins draft progression.
- make_draft_pick: Validates and records picks, advancing the draft clock and generating AI banter.
- simulate_ai_pick: Autonomous AI picker balancing ADP, positional need, and manager persona biases.
- simulate_until_user: Simulates all AI picks until the user is back on the clock.
- simulate_entire_draft: Completes all remaining picks.
- finalize_draft: Computes draft grades, identifies steals/reaches, assigns rosters, and sets schedule.
"""

import json
import logging
import math
import os
import random
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)
DATABASE_URL = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


def get_db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def get_previous_season(cur) -> int:
    cur.execute("SELECT current_season FROM league_state WHERE id = 1")
    row = cur.fetchone()
    if row:
        cs = row["current_season"] if isinstance(row, dict) else row[0]
        if cs:
            return int(cs) - 1
    return 2035


ARCHETYPE_MANAGERS = {
    "ZERO_RB_ZEALOT": [
        {"name": "Zero-RB Theory", "handle": "@ZeroHero", "persona": "ZERO_RB_ZEALOT", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "PPR Machines", "handle": "@PPRGod", "persona": "ZERO_RB_ZEALOT", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Air Raid Society", "handle": "@AirRaidHQ", "persona": "ZERO_RB_ZEALOT", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Late Round RB Lab", "handle": "@LateBacks", "persona": "ZERO_RB_ZEALOT", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Target Monster", "handle": "@TargetShare", "persona": "ZERO_RB_ZEALOT", "avatar": "/static/players/robot_avatar.webp"},
    ],
    "HERO_RB": [
        {"name": "Hero RB Squad", "handle": "@AnchorBack", "persona": "HERO_RB", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Robust RB Lab", "handle": "@BellCowBros", "persona": "HERO_RB", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Workhorse Dynasty", "handle": "@Workhorse", "persona": "HERO_RB", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Ground & Pound", "handle": "@HeavyTrench", "persona": "HERO_RB", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Bellcow Believers", "handle": "@BellcowBros", "persona": "HERO_RB", "avatar": "/static/players/robot_avatar.webp"},
    ],
    "ANALYTICS_NERD": [
        {"name": "Data Analytics", "handle": "@DataDog", "persona": "ANALYTICS_NERD", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "VORP Machine", "handle": "@QuantKing", "persona": "ANALYTICS_NERD", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Draft Board Wizards", "handle": "@ScoutMaster", "persona": "ANALYTICS_NERD", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Regression Models", "handle": "@StatSigma", "persona": "ANALYTICS_NERD", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Expected Points HQ", "handle": "@xFP_Metrics", "persona": "ANALYTICS_NERD", "avatar": "/static/players/robot_avatar.webp"},
    ],
    "TRASH_TALKER": [
        {"name": "Gridiron Dominance", "handle": "@Champ2035", "persona": "TRASH_TALKER", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Smashmouth Crew", "handle": "@HeavyHitter", "persona": "TRASH_TALKER", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Trophy Chasers", "handle": "@RingCollector", "persona": "TRASH_TALKER", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "No Fear Fantasy", "handle": "@SwaggerKing", "persona": "TRASH_TALKER", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Scoreboard Watchers", "handle": "@CheckTheBoard", "persona": "TRASH_TALKER", "avatar": "/static/players/robot_avatar.webp"},
    ],
    "WAIVER_HAWK": [
        {"name": "Waiver Wire Kings", "handle": "@WireHawk", "persona": "WAIVER_HAWK", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "FAAB Bidding Lab", "handle": "@FAABMaxer", "persona": "WAIVER_HAWK", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Depth Hoarders", "handle": "@BenchDepth", "persona": "WAIVER_HAWK", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Midnight Churners", "handle": "@WaiverNinja", "persona": "WAIVER_HAWK", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Upside Lottery Co", "handle": "@HandcuffLotto", "persona": "WAIVER_HAWK", "avatar": "/static/players/robot_avatar.webp"},
    ],
    "DIE_HARD": [
        {"name": "Franchise DieHards", "handle": "@DieHardFan", "persona": "DIE_HARD", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Sunday Loyalty", "handle": "@BleedColors", "persona": "DIE_HARD", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Legacy Believers", "handle": "@OldSchoolGrit", "persona": "DIE_HARD", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Eye Test Only", "handle": "@FilmRoomEye", "persona": "DIE_HARD", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Hometown Heroes", "handle": "@CivicPride", "persona": "DIE_HARD", "avatar": "/static/players/robot_avatar.webp"},
    ],
    "TRADE_FIEND": [
        {"name": "Trade Block Titans", "handle": "@BlockBuster", "persona": "TRADE_FIEND", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Buy Low Syndicate", "handle": "@MarketFlip", "persona": "TRADE_FIEND", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Asset Allocators", "handle": "@ConsolidationKing", "persona": "TRADE_FIEND", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Two-For-One Lab", "handle": "@PackageDeal", "persona": "TRADE_FIEND", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Touchdown Todd", "handle": "@TouchdownTodd", "persona": "TRADE_FIEND", "avatar": "/static/players/robot_avatar.webp"},
    ],
    "UNDERDOG": [
        {"name": "Sleeper Syndicate", "handle": "@SleeperSteve", "persona": "UNDERDOG", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Breakout Blueprint", "handle": "@NextGenStars", "persona": "UNDERDOG", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Young Gun Dynasty", "handle": "@YouthMovement", "persona": "UNDERDOG", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Ceiling Chasers", "handle": "@CeilingChasers", "persona": "UNDERDOG", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Cinderella Squad", "handle": "@UnderdogHQ", "persona": "UNDERDOG", "avatar": "/static/players/robot_avatar.webp"},
    ],
    "RB_HEAVY": [
        {"name": "Heavy Trench", "handle": "@HeavyTrench", "persona": "RB_HEAVY", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Ground & Pound", "handle": "@GroundPound", "persona": "RB_HEAVY", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Smashmouth Syndicate", "handle": "@SmashTrench", "persona": "RB_HEAVY", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Bellcow Dynasty", "handle": "@BellcowKings", "persona": "RB_HEAVY", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Trench Bullies", "handle": "@TrenchBullies", "persona": "RB_HEAVY", "avatar": "/static/players/robot_avatar.webp"},
    ],
    "EARLY_QB": [
        {"name": "Gunslinger Dynasty", "handle": "@GunslingerHQ", "persona": "EARLY_QB", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Field Generals", "handle": "@FieldGeneral", "persona": "EARLY_QB", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Elite Arms Lab", "handle": "@EliteArms", "persona": "EARLY_QB", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Pocket Kings", "handle": "@PocketKings", "persona": "EARLY_QB", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Dual-Threat Society", "handle": "@DualThreat", "persona": "EARLY_QB", "avatar": "/static/players/robot_avatar.webp"},
    ],
    "EARLY_TE": [
        {"name": "Middle Seam", "handle": "@MiddleSeam", "persona": "EARLY_TE", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Unicorn Tight Ends", "handle": "@UnicornTE", "persona": "EARLY_TE", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Endzone Enforcers", "handle": "@TE_Enforcers", "persona": "EARLY_TE", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Y-Option Lab", "handle": "@YOption", "persona": "EARLY_TE", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Seam Stretchers", "handle": "@SeamStretcher", "persona": "EARLY_TE", "avatar": "/static/players/robot_avatar.webp"},
    ],
    "BPA": [
        {"name": "Pure Consensus", "handle": "@ConsensusBoard", "persona": "BPA", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Best Board Available", "handle": "@TrueBPA", "persona": "BPA", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Value Over Replacement", "handle": "@VORP_Pure", "persona": "BPA", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Board Discipline", "handle": "@BoardDiscipline", "persona": "BPA", "avatar": "/static/players/robot_avatar.webp"},
        {"name": "Market Consensus", "handle": "@MarketConsensus", "persona": "BPA", "avatar": "/static/players/robot_avatar.webp"},
    ],
}

DEFAULT_MOCK_MANAGERS = [
    {"name": "Zero-RB Theory", "handle": "@ZeroHero", "persona": "ZERO_RB_ZEALOT", "avatar": "/static/players/robot_avatar.webp"},
    {"name": "Data Analytics", "handle": "@DataDog", "persona": "ANALYTICS_NERD", "avatar": "/static/players/robot_avatar.webp"},
    {"name": "Trade Block Titans", "handle": "@BlockBuster", "persona": "TRADE_FIEND", "avatar": "/static/players/robot_avatar.webp"},
    {"name": "Sleeper Syndicate", "handle": "@SleeperSteve", "persona": "UNDERDOG", "avatar": "/static/players/robot_avatar.webp"},
    {"name": "Gridiron Dominance", "handle": "@Champ2035", "persona": "TRASH_TALKER", "avatar": "/static/players/robot_avatar.webp"},
    {"name": "Hero RB Squad", "handle": "@AnchorBack", "persona": "HERO_RB", "avatar": "/static/players/robot_avatar.webp"},
    {"name": "Waiver Wire Kings", "handle": "@WireHawk", "persona": "WAIVER_HAWK", "avatar": "/static/players/robot_avatar.webp"},
    {"name": "Franchise DieHards", "handle": "@DieHardFan", "persona": "DIE_HARD", "avatar": "/static/players/robot_avatar.webp"},
    {"name": "VORP Machine", "handle": "@QuantKing", "persona": "ANALYTICS_NERD", "avatar": "/static/players/robot_avatar.webp"},
    {"name": "Touchdown Todd", "handle": "@TouchdownTodd", "persona": "TRADE_FIEND", "avatar": "/static/players/robot_avatar.webp"},
    {"name": "Ceiling Chasers", "handle": "@CeilingChasers", "persona": "UNDERDOG", "avatar": "/static/players/robot_avatar.webp"},
]


def build_ai_managers(
    teams_count: int,
    archetype_composition: Optional[Dict[str, int]] = None,
    archetypes: Optional[List[str]] = None,
    randomize_order: bool = True,
) -> List[Dict[str, Any]]:
    """Builds a customized list of AI managers based on desired archetype composition."""
    ai_slots = teams_count - 1
    selected_personas: List[str] = []

    if archetypes and len(archetypes) > 0:
        selected_personas = list(archetypes)[:ai_slots]
    elif archetype_composition and sum(archetype_composition.values()) > 0:
        for persona, count in archetype_composition.items():
            if persona in ARCHETYPE_MANAGERS:
                selected_personas.extend([persona] * max(0, int(count)))
        selected_personas = selected_personas[:ai_slots]

    # Fill any remaining slots with a balanced round-robin
    canonical_personas = list(ARCHETYPE_MANAGERS.keys())
    idx = 0
    while len(selected_personas) < ai_slots:
        selected_personas.append(canonical_personas[idx % len(canonical_personas)])
        idx += 1

    if randomize_order and not (archetypes and len(archetypes) > 0):
        random.shuffle(selected_personas)

    # Map each chosen persona to an AI manager template
    managers: List[Dict[str, Any]] = []
    used_indices: Dict[str, int] = defaultdict(int)

    for persona in selected_personas:
        pool = ARCHETYPE_MANAGERS.get(persona, ARCHETYPE_MANAGERS["ANALYTICS_NERD"])
        use_idx = used_indices[persona] % len(pool)
        used_indices[persona] += 1
        m = pool[use_idx]
        managers.append({
            "name": m["name"],
            "handle": m["handle"],
            "persona": m["persona"],
            "avatar": m.get("avatar", "/static/players/robot_avatar.webp"),
        })

    return managers


def ensure_defenses_exist(cur):
    """Ensures all 32 NFL team defenses exist in players and player_seasons for drafting."""
    prev_season = get_previous_season(cur)
    cur.execute("SELECT COUNT(*) as cnt FROM fantasy_projections WHERE season = %s AND position = 'DEF' AND adp_rank IS NOT NULL", (prev_season,))
    row = cur.fetchone()
    if row and (row["cnt"] if isinstance(row, dict) else row[0]) >= 32:
        return

    cur.execute("SELECT id, name, abbreviation FROM teams ORDER BY id")
    nfl_teams = cur.fetchall()
    for tm in nfl_teams:
        p_name = f"{tm['abbreviation']} Defense"
        f_name = f"{tm['name']} D/ST"
        cur.execute("SELECT id FROM players WHERE player_name = %s OR full_name = %s LIMIT 1", (p_name, f_name))
        row = cur.fetchone()
        if not row:
            cur.execute("""
                INSERT INTO players (player_name, full_name, espn_id, player_number)
                VALUES (%s, %s, %s, 0)
                RETURNING id
            """, (p_name, f_name, 99000 + tm["id"]))
            p_id = cur.fetchone()["id"]
        else:
            p_id = row["id"]

        for s in (prev_season, prev_season + 1):
            cur.execute("""
                INSERT INTO player_seasons (player_id, team_id, season, position, age, overall_rating)
                VALUES (%s, %s, %s, 'DEF', 28, 83)
                ON CONFLICT (player_id, season) DO NOTHING
            """, (p_id, tm["id"], s))

            for wk in range(1, 19):
                cur.execute("""
                    INSERT INTO fantasy_projections (season, week, player_id, player_name, team_id, projected_points, position, adp_rank)
                    VALUES (%s, %s, %s, %s, %s, 7.5, 'DEF', %s)
                    ON CONFLICT (season, week, player_id) DO UPDATE SET
                        adp_rank = EXCLUDED.adp_rank,
                        projected_points = EXCLUDED.projected_points,
                        position = EXCLUDED.position
                """, (s, wk, p_id, p_name, tm["id"], 140 + tm["id"]))


def ensure_kickers_exist(cur):
    """Ensures simulation kickers have baseline fantasy projections and ADP ranks for drafting."""
    prev_season = get_previous_season(cur)
    cur.execute("SELECT COUNT(*) as cnt FROM fantasy_projections WHERE season = %s AND position = 'K' AND adp_rank IS NOT NULL", (prev_season,))
    row = cur.fetchone()
    if row and (row["cnt"] if isinstance(row, dict) else row[0]) >= 32:
        return

    for season in (prev_season, prev_season + 1):
        cur.execute("""
            SELECT p.id, p.player_name, ps.team_id, COALESCE(ps.overall_rating, 80) as ovr
            FROM players p
            JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
            WHERE ps.position = 'K'
            ORDER BY ps.overall_rating DESC NULLS LAST, p.id ASC
        """, (season,))
        kickers = cur.fetchall()
        for idx, k in enumerate(kickers, start=1):
            adp = 150 + idx
            avg_pts = round(7.0 + (float(k["ovr"]) / 99.0) * 2.5, 1)
            for wk in range(1, 19):
                ros_total = round(avg_pts * (19 - wk), 1)
                cur.execute("""
                    INSERT INTO fantasy_projections (season, week, player_id, player_name, team_id, projected_points, position, adp_rank, avg_points, ros_total_points)
                    VALUES (%s, %s, %s, %s, %s, %s, 'K', %s, %s, %s)
                    ON CONFLICT (season, week, player_id) DO UPDATE SET
                        player_name = EXCLUDED.player_name,
                        team_id = EXCLUDED.team_id,
                        projected_points = EXCLUDED.projected_points,
                        position = EXCLUDED.position,
                        adp_rank = EXCLUDED.adp_rank,
                        avg_points = EXCLUDED.avg_points,
                        ros_total_points = EXCLUDED.ros_total_points
                """, (season, wk, k["id"], k["player_name"], k["team_id"], avg_pts, adp, avg_pts, ros_total))


def create_draft_session(
    league_id: Optional[int] = None,
    teams_count: int = 10,
    scoring_type: str = "PPR",
    user_slot: Optional[int] = None,
    rounds: int = 15,
    is_mock: bool = True,
    archetype_composition: Optional[Dict[str, int]] = None,
    archetypes: Optional[List[str]] = None,
    randomize_user_slot: bool = True,
    settings: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Initializes a new draft session and pre-populates all snake pick slots."""
    if settings is None:
        settings = {}

    if archetype_composition:
        settings["archetype_composition"] = archetype_composition

    # Randomize user slot by default for mock drafts if not explicitly pinned
    if league_id is None and is_mock:
        if user_slot is None:
            user_slot = random.randint(1, teams_count)
        else:
            user_slot = max(1, min(teams_count, int(user_slot)))
    else:
        user_slot = user_slot or 1

    with get_db() as conn:
        with conn.cursor() as cur:
            ensure_defenses_exist(cur)
            ensure_kickers_exist(cur)

            is_superflex = settings.get("is_superflex", "SUPERFLEX" in scoring_type.upper() or scoring_type.upper() == "PPR")
            settings["is_superflex"] = is_superflex

            teams_info = []

            if league_id is not None:
                is_mock = False
                cur.execute("SELECT * FROM napper_leagues WHERE id = %s", (league_id,))
                league = cur.fetchone()
                if not league:
                    raise ValueError(f"League {league_id} not found")

                teams_count = league["teams_count"] or teams_count
                scoring_type = league["scoring_type"] or scoring_type

                cur.execute("""
                    SELECT id, team_name, manager_handle, avatar_url, is_ai, persona,
                           (user_id IS NOT NULL OR is_ai IS FALSE) as is_user
                    FROM napper_teams
                    WHERE league_id = %s
                    ORDER BY (user_id IS NOT NULL OR is_ai IS FALSE) DESC, id ASC
                """, (league_id,))
                league_teams = cur.fetchall()

                # Place user team at user_slot
                user_team = None
                other_teams = []
                for tm in league_teams:
                    if tm["is_user"] and not user_team:
                        user_team = tm
                    else:
                        other_teams.append(tm)

                slot_cursor = 0
                for s in range(1, teams_count + 1):
                    if s == user_slot and user_team:
                        teams_info.append({
                            "slot_num": s,
                            "team_id": user_team["id"],
                            "team_name": user_team["team_name"],
                            "manager_handle": user_team["manager_handle"],
                            "persona": user_team["persona"] or "USER",
                            "avatar_url": user_team["avatar_url"] or "/static/players/robot_avatar.webp",
                            "is_user": True,
                        })
                    else:
                        tm = other_teams[slot_cursor] if slot_cursor < len(other_teams) else None
                        slot_cursor += 1
                        t_name = tm["team_name"] if tm else f"Team {s}"
                        t_handle = tm["manager_handle"] if tm else f"@Manager{s}"
                        t_persona = (tm["persona"] if tm and tm["persona"] else None) or DEFAULT_MOCK_MANAGERS[(s - 1) % len(DEFAULT_MOCK_MANAGERS)]["persona"]
                        t_id = tm["id"] if tm else None
                        teams_info.append({
                            "slot_num": s,
                            "team_id": t_id,
                            "team_name": t_name,
                            "manager_handle": t_handle,
                            "persona": t_persona,
                            "avatar_url": (tm["avatar_url"] if tm and tm["avatar_url"] else None) or "/static/players/robot_avatar.webp",
                            "is_user": False,
                        })
            else:
                # Standalone mock draft
                # Look up user profile
                cur.execute("SELECT handle, display_name, avatar_url FROM napper_user_profiles ORDER BY id ASC LIMIT 1")
                prof = cur.fetchone()
                u_handle = prof["handle"] if prof else "@jrereyi2000"
                u_name = "The Straw Hat Pirates"
                u_avatar = prof["avatar_url"] if prof else "/static/players/robot_avatar.webp"

                # Build customized AI managers according to chosen archetype composition
                ai_managers = build_ai_managers(
                    teams_count=teams_count,
                    archetype_composition=archetype_composition,
                    archetypes=archetypes
                )
                # Shuffle AI managers so no two drafts have the exact same order
                random.shuffle(ai_managers)

                ai_idx = 0
                for s in range(1, teams_count + 1):
                    if s == user_slot:
                        teams_info.append({
                            "slot_num": s,
                            "team_id": None,
                            "team_name": u_name,
                            "manager_handle": u_handle,
                            "persona": "USER",
                            "avatar_url": u_avatar,
                            "is_user": True,
                        })
                    else:
                        ai_m = ai_managers[ai_idx]
                        ai_idx += 1
                        teams_info.append({
                            "slot_num": s,
                            "team_id": None,
                            "team_name": ai_m["name"],
                            "manager_handle": ai_m["handle"],
                            "persona": ai_m["persona"],
                            "avatar_url": ai_m["avatar"],
                            "is_user": False,
                        })

            settings["teams_info"] = teams_info

            prev_season = get_previous_season(cur)

            # Insert draft record
            cur.execute("""
                INSERT INTO napper_drafts (
                    league_id, season, rounds, teams_count, scoring_type,
                    status, current_round, current_pick, is_mock, user_slot, settings
                ) VALUES (%s, %s, %s, %s, %s, 'PENDING', 1, 1, %s, %s, %s)
                RETURNING id, league_id, season, rounds, teams_count, scoring_type,
                          status, current_round, current_pick, is_mock, user_slot, settings, created_at
            """, (
                league_id,
                prev_season,
                rounds,
                teams_count,
                scoring_type,
                is_mock,
                user_slot,
                psycopg.types.json.Jsonb(settings)
            ))
            draft = cur.fetchone()
            draft_id = draft["id"]

            # Pre-populate all draft picks in snake order
            picks_to_insert = []
            slot_by_num = {t["slot_num"]: t for t in teams_info}

            for r in range(1, rounds + 1):
                is_odd = (r % 2 == 1)
                for p in range(1, teams_count + 1):
                    overall = (r - 1) * teams_count + p
                    slot_num = p if is_odd else (teams_count - p + 1)
                    team_meta = slot_by_num.get(slot_num, {})
                    picked_by = "USER" if slot_num == user_slot else "AI"

                    picks_to_insert.append((
                        draft_id,
                        r,
                        p,
                        overall,
                        team_meta.get("team_id"),
                        team_meta.get("team_name", f"Team {slot_num}"),
                        slot_num,
                        None,  # player_id
                        picked_by,
                        None   # pick_notes
                    ))

            cur.executemany("""
                INSERT INTO napper_draft_picks (
                    draft_id, round_num, pick_num, overall_pick, team_id,
                    team_name, slot_num, player_id, picked_by, pick_notes
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, picks_to_insert)

        conn.commit()

    return get_draft_state(draft_id)


def get_draft_state(draft_id: int) -> Dict[str, Any]:
    """Fetches full state of a draft session including board matrix and active turn."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM napper_drafts WHERE id = %s", (draft_id,))
            draft = cur.fetchone()
            if not draft:
                raise ValueError(f"Draft {draft_id} not found")

            total_picks = draft["rounds"] * draft["teams_count"]
            current_pick = draft["current_pick"]
            current_round = draft["current_round"]
            status = draft["status"]
            user_slot = draft["user_slot"]
            settings = draft["settings"] or {}
            teams_info = settings.get("teams_info", [])

            prev_season = get_previous_season(cur)

            # Fetch all picks with drafted player details
            cur.execute("""
                SELECT dp.id, dp.round_num, dp.pick_num, dp.overall_pick, dp.slot_num,
                       dp.team_id, dp.team_name, dp.player_id, dp.picked_by, dp.pick_notes,
                       p.player_name, p.full_name, COALESCE(ps.position, 'FLEX') as position,
                       COALESCE(t.abbreviation, 'NFL') as team_abbr,
                       COALESCE(NULLIF(ss.total_fantasy_points, 0), NULLIF(fp.ros_total_points, 0), fp.projected_points * 17, 180.0) as projected_points,
                       COALESCE(NULLIF(ss.ppg_fantasy_points, 0), fp.avg_points, 10.5) as ppg,
                       COALESCE(fp.adp_rank, 150) as adp_rank
                FROM napper_draft_picks dp
                LEFT JOIN players p ON p.id = dp.player_id
                LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                LEFT JOIN teams t ON t.id = ps.team_id
                LEFT JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.season = %s AND fp.week = 1
                LEFT JOIN season_stats ss ON ss.player_id = p.id AND ss.season = %s
                WHERE dp.draft_id = %s
                ORDER BY dp.overall_pick ASC
            """, (prev_season, prev_season, prev_season, draft_id))
            raw_picks = cur.fetchall()

            drafted_player_ids = set()
            picks = []
            roster_by_slot = defaultdict(list)

            for rp in raw_picks:
                player_data = None
                if rp["player_id"]:
                    drafted_player_ids.add(rp["player_id"])
                    player_data = {
                        "id": rp["player_id"],
                        "name": rp["full_name"] or rp["player_name"],
                        "player_name": rp["player_name"],
                        "position": rp["position"],
                        "team": rp["team_abbr"],
                        "headshot_url": f"/static/players/{rp['player_id']}.webp",
                        "projected_points": float(rp["projected_points"]),
                        "ppg": float(rp["ppg"]),
                        "adp": int(rp["adp_rank"]),
                    }
                    roster_by_slot[rp["slot_num"]].append(player_data)

                picks.append({
                    "id": rp["id"],
                    "round_num": rp["round_num"],
                    "pick_num": rp["pick_num"],
                    "overall_pick": rp["overall_pick"],
                    "slot_num": rp["slot_num"],
                    "team_id": rp["team_id"],
                    "team_name": rp["team_name"],
                    "picked_by": rp["picked_by"],
                    "pick_notes": rp["pick_notes"],
                    "player": player_data,
                })

            # Check if active pick exists
            on_the_clock = None
            is_user_turn = False
            if status == "IN_PROGRESS" and current_pick <= total_picks:
                for p in picks:
                    if p["overall_pick"] == current_pick:
                        slot = p["slot_num"]
                        t_meta = next((t for t in teams_info if t["slot_num"] == slot), {})
                        is_user_turn = (slot == user_slot)
                        on_the_clock = {
                            "round_num": p["round_num"],
                            "pick_num": p["pick_num"],
                            "overall_pick": p["overall_pick"],
                            "slot_num": slot,
                            "team_name": p["team_name"],
                            "manager_handle": t_meta.get("manager_handle", f"@Team{slot}"),
                            "persona": t_meta.get("persona", "USER" if is_user_turn else "AI"),
                            "is_user": is_user_turn,
                        }
                        break

            # Teams list with updated rosters
            teams = []
            for t in teams_info:
                s_num = t["slot_num"]
                tm_roster = roster_by_slot.get(s_num, [])
                teams.append({
                    "slot_num": s_num,
                    "team_id": t.get("team_id"),
                    "team_name": t["team_name"],
                    "manager_handle": t.get("manager_handle", f"@Team{s_num}"),
                    "persona": t.get("persona", "USER" if s_num == user_slot else "AI"),
                    "avatar_url": t.get("avatar_url", "/static/players/robot_avatar.webp"),
                    "is_user": (s_num == user_slot),
                    "drafted_count": len(tm_roster),
                    "roster": tm_roster,
                })

            return {
                "id": draft["id"],
                "league_id": draft["league_id"],
                "season": draft["season"],
                "rounds": draft["rounds"],
                "teams_count": draft["teams_count"],
                "scoring_type": draft["scoring_type"],
                "status": status,
                "current_round": current_round,
                "current_pick": current_pick,
                "total_picks": total_picks,
                "user_slot": user_slot,
                "is_mock": draft["is_mock"],
                "is_user_turn": is_user_turn,
                "settings": settings,
                "teams": teams,
                "picks": picks,
                "on_the_clock": on_the_clock,
                "drafted_player_ids": sorted(list(drafted_player_ids)),
                "recap": settings.get("recap"),
            }


def claim_slot(draft_id: int, new_slot: int) -> Dict[str, Any]:
    """Claims a new draft slot for the user in a pending draft."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM napper_drafts WHERE id = %s", (draft_id,))
            draft = cur.fetchone()
            if not draft:
                raise ValueError(f"Draft {draft_id} not found")
            if draft["status"] != "PENDING":
                raise ValueError("Cannot claim slot after draft has started")

            teams_count = draft["teams_count"]
            if new_slot < 1 or new_slot > teams_count:
                raise ValueError(f"Invalid slot {new_slot}. Must be between 1 and {teams_count}")

            old_slot = draft["user_slot"]
            if old_slot == new_slot:
                return get_draft_state(draft_id)

            settings = draft["settings"] or {}
            teams_info = settings.get("teams_info", [])

            user_team_meta = next((t for t in teams_info if t["slot_num"] == old_slot), None)
            target_team_meta = next((t for t in teams_info if t["slot_num"] == new_slot), None)

            if user_team_meta and target_team_meta:
                # Swap slots
                user_team_meta["slot_num"] = new_slot
                user_team_meta["is_user"] = True
                target_team_meta["slot_num"] = old_slot
                target_team_meta["is_user"] = False

                teams_info.sort(key=lambda x: x["slot_num"])
                settings["teams_info"] = teams_info

                cur.execute("""
                    UPDATE napper_drafts
                    SET user_slot = %s, settings = %s
                    WHERE id = %s
                """, (new_slot, psycopg.types.json.Jsonb(settings), draft_id))

                # Update picks picked_by and team_name
                cur.execute("""
                    UPDATE napper_draft_picks
                    SET picked_by = 'USER', team_name = %s
                    WHERE draft_id = %s AND slot_num = %s
                """, (user_team_meta["team_name"], draft_id, new_slot))

                cur.execute("""
                    UPDATE napper_draft_picks
                    SET picked_by = 'AI', team_name = %s
                    WHERE draft_id = %s AND slot_num = %s
                """, (target_team_meta["team_name"], draft_id, old_slot))

        conn.commit()

    return get_draft_state(draft_id)


def randomize_draft_order(draft_id: int, keep_user_slot: bool = False) -> Dict[str, Any]:
    """Randomizes the draft slot order and optionally user slot in a pending draft."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM napper_drafts WHERE id = %s", (draft_id,))
            draft = cur.fetchone()
            if not draft:
                raise ValueError(f"Draft {draft_id} not found")
            if draft["status"] != "PENDING":
                raise ValueError("Cannot randomize draft order after draft has started")

            teams_count = draft["teams_count"]
            old_user_slot = draft["user_slot"]
            settings = draft["settings"] or {}
            teams_info = settings.get("teams_info", [])

            user_meta = next((t for t in teams_info if t.get("is_user")), None)
            ai_metas = [t for t in teams_info if not t.get("is_user")]

            if not user_meta:
                user_meta = {
                    "slot_num": old_user_slot,
                    "team_id": None,
                    "team_name": "The Straw Hat Pirates",
                    "manager_handle": "@jrereyi2000",
                    "persona": "USER",
                    "avatar_url": "/static/players/robot_avatar.webp",
                    "is_user": True,
                }

            # Determine new user slot
            if keep_user_slot:
                new_user_slot = old_user_slot
            else:
                available_slots = list(range(1, teams_count + 1))
                if len(available_slots) > 1 and old_user_slot in available_slots:
                    available_slots.remove(old_user_slot)
                new_user_slot = random.choice(available_slots)

            # Shuffle AI managers across the other slots
            random.shuffle(ai_metas)

            new_teams_info = []
            ai_idx = 0
            for s in range(1, teams_count + 1):
                if s == new_user_slot:
                    user_meta["slot_num"] = s
                    user_meta["is_user"] = True
                    new_teams_info.append(user_meta)
                else:
                    tm = ai_metas[ai_idx] if ai_idx < len(ai_metas) else DEFAULT_MOCK_MANAGERS[ai_idx % len(DEFAULT_MOCK_MANAGERS)]
                    ai_idx += 1
                    tm["slot_num"] = s
                    tm["is_user"] = False
                    new_teams_info.append(tm)

            new_teams_info.sort(key=lambda x: x["slot_num"])
            settings["teams_info"] = new_teams_info

            # Update napper_drafts
            cur.execute("""
                UPDATE napper_drafts
                SET user_slot = %s, settings = %s
                WHERE id = %s
            """, (new_user_slot, psycopg.types.json.Jsonb(settings), draft_id))

            # Update napper_draft_picks
            slot_by_num = {t["slot_num"]: t for t in new_teams_info}
            for s_num, t_meta in slot_by_num.items():
                cur.execute("""
                    UPDATE napper_draft_picks
                    SET picked_by = %s, team_name = %s, team_id = %s
                    WHERE draft_id = %s AND slot_num = %s
                """, (
                    "USER" if s_num == new_user_slot else "AI",
                    t_meta.get("team_name"),
                    t_meta.get("team_id"),
                    draft_id,
                    s_num
                ))

        conn.commit()

    return get_draft_state(draft_id)


def set_draft_order(
    draft_id: int,
    slot_order: Optional[List[int]] = None,
    teams: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """Manually sets the draft order for all teams in a pending draft."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM napper_drafts WHERE id = %s", (draft_id,))
            draft = cur.fetchone()
            if not draft:
                raise ValueError(f"Draft {draft_id} not found")
            if draft["status"] != "PENDING":
                raise ValueError("Cannot set draft order after draft has started")

            teams_count = draft["teams_count"]
            settings = draft["settings"] or {}
            old_teams_info = settings.get("teams_info", [])
            old_by_slot = {t["slot_num"]: t for t in old_teams_info}

            new_teams_info = []
            if teams is not None:
                if len(teams) != teams_count:
                    raise ValueError(f"teams list must contain exactly {teams_count} teams, got {len(teams)}")
                for idx, tm in enumerate(teams):
                    s_num = idx + 1
                    tm_copy = dict(tm)
                    tm_copy["slot_num"] = s_num
                    new_teams_info.append(tm_copy)
            elif slot_order is not None:
                if len(slot_order) != teams_count:
                    raise ValueError(f"slot_order must contain {teams_count} slots, got {len(slot_order)}")
                if sorted(slot_order) != list(range(1, teams_count + 1)):
                    raise ValueError(f"slot_order must be a permutation of slots 1 to {teams_count}")
                for new_slot, old_slot in enumerate(slot_order, start=1):
                    tm = dict(old_by_slot.get(old_slot, {}))
                    tm["slot_num"] = new_slot
                    new_teams_info.append(tm)
            else:
                raise ValueError("Either slot_order or teams must be provided")

            # Determine user_slot from the team where is_user is True (or persona is 'USER')
            user_meta = next((t for t in new_teams_info if t.get("is_user") or t.get("persona") == "USER"), None)
            new_user_slot = user_meta["slot_num"] if user_meta else draft["user_slot"]

            # Ensure is_user is properly marked
            for tm in new_teams_info:
                tm["is_user"] = (tm["slot_num"] == new_user_slot)

            new_teams_info.sort(key=lambda x: x["slot_num"])
            settings["teams_info"] = new_teams_info

            # Update napper_drafts
            cur.execute("""
                UPDATE napper_drafts
                SET user_slot = %s, settings = %s
                WHERE id = %s
            """, (new_user_slot, psycopg.types.json.Jsonb(settings), draft_id))

            # Update napper_draft_picks
            slot_by_num = {t["slot_num"]: t for t in new_teams_info}
            for s_num, t_meta in slot_by_num.items():
                cur.execute("""
                    UPDATE napper_draft_picks
                    SET picked_by = %s, team_name = %s, team_id = %s
                    WHERE draft_id = %s AND slot_num = %s
                """, (
                    "USER" if s_num == new_user_slot else "AI",
                    t_meta.get("team_name"),
                    t_meta.get("team_id"),
                    draft_id,
                    s_num
                ))

        conn.commit()

    return get_draft_state(draft_id)


def set_draft_archetypes(
    draft_id: int,
    archetype_composition: Optional[Dict[str, int]] = None,
    archetypes: Optional[List[str]] = None
) -> Dict[str, Any]:
    """Updates AI team archetypes and managers for a pending mock draft."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM napper_drafts WHERE id = %s", (draft_id,))
            draft = cur.fetchone()
            if not draft:
                raise ValueError(f"Draft {draft_id} not found")
            if draft["status"] != "PENDING":
                raise ValueError("Cannot change archetypes after draft has started")

            teams_count = draft["teams_count"]
            user_slot = draft["user_slot"]
            settings = draft["settings"] or {}
            teams_info = settings.get("teams_info", [])

            user_meta = next((t for t in teams_info if t.get("is_user")), None)
            if not user_meta:
                user_meta = {
                    "slot_num": user_slot,
                    "team_id": None,
                    "team_name": "The Straw Hat Pirates",
                    "manager_handle": "@jrereyi2000",
                    "persona": "USER",
                    "avatar_url": "/static/players/robot_avatar.webp",
                    "is_user": True,
                }

            new_ai_managers = build_ai_managers(
                teams_count=teams_count,
                archetype_composition=archetype_composition,
                archetypes=archetypes
            )
            random.shuffle(new_ai_managers)

            new_teams_info = []
            ai_idx = 0
            for s in range(1, teams_count + 1):
                if s == user_slot:
                    user_meta["slot_num"] = s
                    user_meta["is_user"] = True
                    new_teams_info.append(user_meta)
                else:
                    tm = new_ai_managers[ai_idx]
                    ai_idx += 1
                    new_teams_info.append({
                        "slot_num": s,
                        "team_id": None,
                        "team_name": tm["name"],
                        "manager_handle": tm["handle"],
                        "persona": tm["persona"],
                        "avatar_url": tm["avatar"],
                        "is_user": False,
                    })

            new_teams_info.sort(key=lambda x: x["slot_num"])
            settings["teams_info"] = new_teams_info
            if archetype_composition:
                settings["archetype_composition"] = archetype_composition

            cur.execute("""
                UPDATE napper_drafts
                SET settings = %s
                WHERE id = %s
            """, (psycopg.types.json.Jsonb(settings), draft_id))

            for tm in new_teams_info:
                cur.execute("""
                    UPDATE napper_draft_picks
                    SET picked_by = %s, team_name = %s, team_id = %s
                    WHERE draft_id = %s AND slot_num = %s
                """, (
                    "USER" if tm["slot_num"] == user_slot else "AI",
                    tm.get("team_name"),
                    tm.get("team_id"),
                    draft_id,
                    tm["slot_num"]
                ))

        conn.commit()

    return get_draft_state(draft_id)


def start_draft(draft_id: int) -> Dict[str, Any]:
    """Transitions a draft from PENDING to IN_PROGRESS."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM napper_drafts WHERE id = %s", (draft_id,))
            draft = cur.fetchone()
            if not draft:
                raise ValueError(f"Draft {draft_id} not found")

            if draft["status"] == "PENDING":
                cur.execute("""
                    UPDATE napper_drafts
                    SET status = 'IN_PROGRESS'
                    WHERE id = %s
                """, (draft_id,))

                if draft["league_id"]:
                    cur.execute("""
                        INSERT INTO napper_chat (league_id, sender_type, sender_name, sender_handle, avatar_url, message_text)
                        VALUES (%s, 'SYSTEM_BOT', 'Draft Commissioner', '@DraftBot', '/static/players/robot_avatar.webp',
                                '🚀 The Draft is officially underway! Round 1 is on the clock.')
                    """, (draft["league_id"],))

        conn.commit()

    return get_draft_state(draft_id)


def _generate_pick_commentary(
    player_name: str,
    position: str,
    overall_pick: int,
    adp_rank: int,
    proj: float,
    team_name: str,
    persona: str,
    picked_by: str
) -> str:
    """Generates lively AI banter and commentary for a draft selection."""
    diff = overall_pick - adp_rank  # positive = steal, negative = reach
    is_steal = (diff >= 8)
    is_reach = (diff <= -8)

    if picked_by == "USER":
        if is_steal:
            return f"🔥 Huge value at pick #{overall_pick}! You grabbed {player_name} (consensus ADP #{adp_rank}). Outstanding pick."
        elif is_reach:
            return f"👀 Reaching ahead of the board for {player_name} at #{overall_pick}. You clearly know who you want."
        return f"Solid selection with {player_name} ({position}) at pick #{overall_pick}."

    # AI personas
    if persona == "ZERO_RB_ZEALOT":
        if position in ("RB", "HB"):
            return f"Breaking my Zero-RB protocol for {player_name} at #{overall_pick}. The value was simply too great."
        return f"Zero-RB masterclass in progress. Locking down {player_name} ({position}) with projected {proj:.1f} FPTS."

    if persona == "HERO_RB":
        if position in ("RB", "HB"):
            return f"Anchor back secured! {player_name} gives me the bellcow foundation I needed at pick #{overall_pick}."
        return f"Bolstering my pass-catchers with {player_name} after securing the backfield."

    if persona == "ANALYTICS_NERD":
        if is_steal:
            return f"My VORP algorithms flagged {player_name} as a +{diff} pick market inefficiency. Automatic trigger."
        return f"Optimal expected-value pick: {player_name} projects for {proj:.1f} FPTS with an elite target/touch profile."

    if persona == "TRASH_TALKER":
        if is_reach:
            return f"Rankings are for cowards. {player_name} is going to dominate the entire league this year. Good luck stopping us."
        return f"Thanks for letting {player_name} fall to me. You all just handed me the championship."

    if persona == "WAIVER_HAWK":
        return f"Target volume and versatile snaps secured. {player_name} fits my roster depth perfectly at pick #{overall_pick}."

    if persona == "DIE_HARD":
        return f"Pure grit and veteran leadership. {player_name} brings championship DNA to this franchise at pick #{overall_pick}."

    if persona == "TRADE_FIEND":
        return f"Thrilled to lock down {player_name} at #{overall_pick}. Outstanding player, but my DMs are open if anyone wants to send trade offers right now."

    if persona == "UNDERDOG":
        return f"The entire league is sleeping on {player_name} at pick #{overall_pick}. Massive breakout ceiling incoming!"

    if persona == "RB_HEAVY":
        if position in ("RB", "HB"):
            return f"Ground and pound! Locking in another workhorse backfield weapon with {player_name} at #{overall_pick}. We win in the trenches."
        return f"Balanced reinforcement: adding {player_name} ({position}) to complete the roster around my heavy backfield."

    if persona == "EARLY_QB":
        if position == "QB":
            return f"Franchise field general locked! {player_name} gives me the ultimate weekly floor and ceiling at pick #{overall_pick}."
        return f"Surrounding my elite quarterback with {player_name} ({position}) at pick #{overall_pick}."

    if persona == "EARLY_TE":
        if position == "TE":
            return f"Positional cheat code secured! {player_name} gives me an insurmountable weekly advantage at tight end at pick #{overall_pick}."
        return f"Capitalizing on elite TE positional scarcity by adding {player_name} at pick #{overall_pick}."

    if persona == "BPA":
        if is_steal:
            return f"Discipline and consensus board value: {player_name} was the clear best player available at pick #{overall_pick}."
        return f"Pure best player available: selecting {player_name} ({position}) to maximize overall roster talent."

    if is_steal:
        return f"Steal alert! {team_name} snags {player_name} at #{overall_pick} despite ADP #{adp_rank}."
    return f"{team_name} selects {player_name} ({position}) with the #{overall_pick} overall pick."


def make_draft_pick(draft_id: int, player_id: int, picked_by: str = "USER") -> Dict[str, Any]:
    """Records a player selection for the active pick and advances the draft."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM napper_drafts WHERE id = %s", (draft_id,))
            draft = cur.fetchone()
            if not draft:
                raise ValueError(f"Draft {draft_id} not found")
            if draft["status"] != "IN_PROGRESS":
                raise ValueError(f"Draft is not in progress (current status: {draft['status']})")

            current_pick = draft["current_pick"]
            rounds = draft["rounds"]
            teams_count = draft["teams_count"]
            total_picks = rounds * teams_count

            if current_pick > total_picks:
                return finalize_draft(draft_id)

            # Check if player already drafted
            cur.execute("""
                SELECT overall_pick FROM napper_draft_picks
                WHERE draft_id = %s AND player_id = %s
            """, (draft_id, player_id))
            already = cur.fetchone()
            if already:
                raise ValueError(f"Player {player_id} was already selected at pick #{already['overall_pick']}")

            # Get active pick info
            cur.execute("""
                SELECT * FROM napper_draft_picks
                WHERE draft_id = %s AND overall_pick = %s
            """, (draft_id, current_pick))
            active_pick = cur.fetchone()
            if not active_pick:
                raise ValueError(f"No active pick found for overall #{current_pick}")

            prev_season = get_previous_season(cur)
            # Get player info
            cur.execute("""
                SELECT p.id, p.player_name, p.full_name, COALESCE(ps.position, 'FLEX') as position,
                       COALESCE(t.abbreviation, 'NFL') as team_abbr,
                       COALESCE(NULLIF(ss.total_fantasy_points, 0), NULLIF(fp.ros_total_points, 0), fp.projected_points * 17, 180.0) as projected_points,
                       COALESCE(NULLIF(ss.ppg_fantasy_points, 0), fp.avg_points, 10.5) as ppg,
                       COALESCE(fp.adp_rank, 150) as adp_rank
                FROM players p
                LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                LEFT JOIN teams t ON t.id = ps.team_id
                LEFT JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.season = %s AND fp.week = 1
                LEFT JOIN season_stats ss ON ss.player_id = p.id AND ss.season = %s
                WHERE p.id = %s
            """, (prev_season, prev_season, prev_season, player_id))
            player = cur.fetchone()
            if not player:
                raise ValueError(f"Player {player_id} not found")

            settings = draft["settings"] or {}
            teams_info = settings.get("teams_info", [])
            team_meta = next((t for t in teams_info if t["slot_num"] == active_pick["slot_num"]), {})
            persona = team_meta.get("persona", "USER" if picked_by == "USER" else "AI")

            p_name = player["full_name"] or player["player_name"]
            notes = _generate_pick_commentary(
                player_name=p_name,
                position=player["position"],
                overall_pick=current_pick,
                adp_rank=int(player["adp_rank"]),
                proj=float(player["projected_points"]),
                team_name=active_pick["team_name"],
                persona=persona,
                picked_by=picked_by
            )

            # Record pick
            cur.execute("""
                UPDATE napper_draft_picks
                SET player_id = %s, picked_by = %s, pick_notes = %s
                WHERE draft_id = %s AND overall_pick = %s
            """, (player_id, picked_by, notes, draft_id, current_pick))

            # Post chat message if real league draft
            if draft["league_id"]:
                cur.execute("SELECT id FROM napper_leagues WHERE id = %s", (draft["league_id"],))
                if cur.fetchone():
                    chat_text = f"Round {active_pick['round_num']}, Pick {active_pick['pick_num']} (#{current_pick}): {active_pick['team_name']} drafted {p_name} ({player['position']} - {player['team_abbr']})."
                    cur.execute("""
                        INSERT INTO napper_chat (league_id, sender_type, sender_name, sender_handle, avatar_url, message_text)
                        VALUES (%s, 'SYSTEM_BOT', 'Draft Bot', '@DraftBot', '/static/players/robot_avatar.webp', %s)
                    """, (draft["league_id"], chat_text))

            # Advance pick
            next_pick = current_pick + 1
            if next_pick > total_picks:
                cur.execute("""
                    UPDATE napper_drafts
                    SET current_pick = %s, current_round = %s, status = 'COMPLETED'
                    WHERE id = %s
                """, (next_pick, rounds, draft_id))
                conn.commit()
                return finalize_draft(draft_id)
            else:
                next_round = ((next_pick - 1) // teams_count) + 1
                cur.execute("""
                    UPDATE napper_drafts
                    SET current_pick = %s, current_round = %s
                    WHERE id = %s
                """, (next_pick, next_round, draft_id))

        conn.commit()

    return get_draft_state(draft_id)


def simulate_ai_pick(draft_id: int) -> Dict[str, Any]:
    """Selects the best available player for the on-the-clock team based on ADP, need, and persona."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM napper_drafts WHERE id = %s", (draft_id,))
            draft = cur.fetchone()
            if not draft:
                raise ValueError(f"Draft {draft_id} not found")
            if draft["status"] == "PENDING":
                start_draft(draft_id)
                cur.execute("SELECT * FROM napper_drafts WHERE id = %s", (draft_id,))
                draft = cur.fetchone()
            if draft["status"] != "IN_PROGRESS":
                return get_draft_state(draft_id)

            current_pick = draft["current_pick"]
            total_picks = draft["rounds"] * draft["teams_count"]
            if current_pick > total_picks:
                return finalize_draft(draft_id)

            cur.execute("""
                SELECT * FROM napper_draft_picks
                WHERE draft_id = %s AND overall_pick = %s
            """, (draft_id, current_pick))
            active_pick = cur.fetchone()
            if not active_pick:
                return get_draft_state(draft_id)

            slot_num = active_pick["slot_num"]
            settings = draft["settings"] or {}
            teams_info = settings.get("teams_info", [])
            team_meta = next((t for t in teams_info if t["slot_num"] == slot_num), {})
            persona = team_meta.get("persona", "AI")
            is_superflex = settings.get("is_superflex", False)

            prev_season = get_previous_season(cur)

            # Count positions already drafted by this team
            cur.execute("""
                SELECT COALESCE(ps.position, 'FLEX') as pos, COUNT(*) as cnt
                FROM napper_draft_picks dp
                JOIN player_seasons ps ON ps.player_id = dp.player_id AND ps.season = %s
                WHERE dp.draft_id = %s AND dp.slot_num = %s AND dp.player_id IS NOT NULL
                GROUP BY ps.position
            """, (prev_season, draft_id, slot_num))
            pos_counts = {r["pos"]: r["cnt"] for r in cur.fetchall()}

            qb_cnt = pos_counts.get("QB", 0)
            rb_cnt = pos_counts.get("RB", 0) + pos_counts.get("HB", 0) + pos_counts.get("FB", 0)
            wr_cnt = pos_counts.get("WR", 0)
            te_cnt = pos_counts.get("TE", 0)
            k_cnt = pos_counts.get("K", 0)
            def_cnt = pos_counts.get("DEF", 0)

            # Roster caps aligned with fantasy_league_sim DRAFT_CAP
            max_qb = 3 if is_superflex else 2
            max_rb = 7
            max_wr = 7
            max_te = 2
            max_k = 1
            max_def = 1

            rounds = draft.get("rounds", 15) or 15

            # Starter requirements aligned with fantasy_league_sim LINEUP
            starter_targets = {
                "QB": 1 if rounds >= 1 else 0,
                "RB": 2 if rounds >= 8 else (1 if rounds >= 2 else 0),
                "WR": 2 if rounds >= 8 else (1 if rounds >= 3 else 0),
                "TE": 1 if rounds >= 4 else 0,
                "K": 1 if rounds >= 14 else 0,
                "DEF": 1 if rounds >= 14 else 0,
            }

            need_qb = max(0, starter_targets["QB"] - qb_cnt)
            need_rb = max(0, starter_targets["RB"] - rb_cnt)
            need_wr = max(0, starter_targets["WR"] - wr_cnt)
            need_te = max(0, starter_targets["TE"] - te_cnt)
            need_k = max(0, starter_targets["K"] - k_cnt)
            need_def = max(0, starter_targets["DEF"] - def_cnt)
            total_need = need_qb + need_rb + need_wr + need_te + need_k + need_def
            total_rostered = qb_cnt + rb_cnt + wr_cnt + te_cnt + k_cnt + def_cnt
            picks_left = max(1, rounds - total_rostered)
            forced = total_need >= picks_left

            # Fetch top 100 available players by ADP
            cur.execute("""
                SELECT p.id, p.player_name, p.full_name, COALESCE(ps.position, 'FLEX') as position,
                       COALESCE(fp.adp_rank, 200) as adp_rank,
                       COALESCE(NULLIF(ss.total_fantasy_points, 0), NULLIF(fp.ros_total_points, 0), fp.projected_points * 17, 180.0) as projected_points,
                       COALESCE(NULLIF(ss.ppg_fantasy_points, 0), fp.avg_points, 10.5) as ppg
                FROM players p
                JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                LEFT JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.season = %s AND fp.week = 1
                LEFT JOIN season_stats ss ON ss.player_id = p.id AND ss.season = %s
                WHERE p.id NOT IN (
                    SELECT player_id FROM napper_draft_picks
                    WHERE draft_id = %s AND player_id IS NOT NULL
                )
                AND ps.position IN ('QB', 'HB', 'FB', 'RB', 'WR', 'TE', 'K', 'DEF')
                ORDER BY COALESCE(fp.adp_rank, 200) ASC, ss.total_fantasy_points DESC NULLS LAST, projected_points DESC
                LIMIT 100
            """, (prev_season, prev_season, prev_season, draft_id))
            candidates = cur.fetchall()

            if not candidates:
                raise ValueError("No available players left in player pool")

            curr_round = active_pick["round_num"]
            k_def_start_round = max(1, rounds - 2)
            best_candidate = None
            best_score = -999999.0

            for cand in candidates:
                pos = cand["position"]
                norm_pos = "RB" if pos in ("HB", "FB", "RB") else pos

                # Hard constraints
                if norm_pos == "QB" and qb_cnt >= max_qb:
                    continue
                if norm_pos == "RB" and rb_cnt >= max_rb:
                    continue
                if norm_pos == "WR" and wr_cnt >= max_wr:
                    continue
                if norm_pos == "TE" and te_cnt >= max_te:
                    continue
                if norm_pos == "K" and (k_cnt >= max_k or curr_round < k_def_start_round):
                    continue
                if norm_pos == "DEF" and (def_cnt >= max_def or curr_round < k_def_start_round):
                    continue

                # Forced starting lineup rule from fantasy_league_sim
                if forced:
                    if norm_pos == "QB" and need_qb == 0:
                        continue
                    if norm_pos == "RB" and need_rb == 0:
                        continue
                    if norm_pos == "WR" and need_wr == 0:
                        continue
                    if norm_pos == "TE" and need_te == 0:
                        continue
                    if norm_pos == "K" and need_k == 0:
                        continue
                    if norm_pos == "DEF" and need_def == 0:
                        continue

                adp = float(cand["adp_rank"])
                proj = float(cand["projected_points"])

                # Positional Weighting (aligned with fantasy_league_sim POS_WEIGHT: QB 0.4, TE 0.8, RB/WR 1.0)
                pos_wt = 1.0 if is_superflex and norm_pos == "QB" else {"QB": 0.4, "RB": 1.0, "WR": 1.0, "TE": 0.8, "K": 0.2, "DEF": 0.2}.get(norm_pos, 0.7)
                score = 1000.0 - (adp * 2.0) + (proj * pos_wt * 3.5)

                # Backup penalties (aligned with fantasy_league_sim BACKUP_PENALTY)
                if norm_pos == "QB" and qb_cnt >= 1:
                    score -= 350.0 * qb_cnt
                elif norm_pos == "TE" and te_cnt >= 1:
                    score -= 250.0 * te_cnt
                elif norm_pos == "RB" and rb_cnt >= 4:
                    score -= 60.0 * (rb_cnt - 3)
                elif norm_pos == "WR" and wr_cnt >= 4:
                    score -= 60.0 * (wr_cnt - 3)

                # Starter urgency
                if norm_pos == "TE" and te_cnt == 0 and curr_round >= 6:
                    score += 250.0
                if norm_pos == "QB" and qb_cnt == 0 and curr_round >= 5:
                    score += 250.0
                if norm_pos == "WR" and wr_cnt < starter_targets["WR"] and curr_round >= 4:
                    score += 200.0
                if norm_pos == "RB" and rb_cnt < starter_targets["RB"] and curr_round >= 4:
                    score += 200.0

                # Strategy / Persona adjustments (aligned with fantasy_league_sim strategy_adjustment)
                if persona in ("ZERO_RB_ZEALOT", "ZERO_RB"):
                    if curr_round <= 4 and norm_pos == "RB":
                        score -= 400.0
                    elif curr_round in (5, 6, 7) and norm_pos == "RB":
                        score += 200.0
                    elif curr_round <= 4 and norm_pos in ("WR", "TE", "QB"):
                        score += 150.0
                elif persona == "HERO_RB":
                    if curr_round <= 2 and rb_cnt == 0 and norm_pos == "RB":
                        score += 300.0
                    elif curr_round in (3, 4, 5, 6) and rb_cnt >= 1 and norm_pos == "RB":
                        score -= 350.0
                elif persona == "RB_HEAVY":
                    if curr_round <= 4 and norm_pos == "RB":
                        if rb_cnt < 2:
                            score += 280.0
                        elif rb_cnt == 2:
                            score += 140.0
                elif persona == "EARLY_QB":
                    if curr_round <= 3 and norm_pos == "QB" and qb_cnt == 0:
                        score += 350.0
                elif persona == "EARLY_TE":
                    if curr_round <= 3 and norm_pos == "TE" and te_cnt == 0:
                        score += 300.0
                elif persona in ("BPA", "ANALYTICS_NERD"):
                    if adp < current_pick:
                        score += (current_pick - adp) * 5.0
                elif persona == "TRASH_TALKER":
                    score += random.uniform(-10.0, 15.0)
                    if cand.get("projected_points", 0) > 220.0:
                        score *= 1.15
                    if curr_round <= 3 and norm_pos in ("QB", "TE") and ((norm_pos == "QB" and qb_cnt == 0) or (norm_pos == "TE" and te_cnt == 0)):
                        score += 180.0
                elif persona == "WAIVER_HAWK":
                    if norm_pos in ("WR", "RB"):
                        score += float(cand.get("ppg", 10.0)) * 2.0
                    if curr_round >= 9 and norm_pos in ("RB", "WR"):
                        score += 1.25
                elif persona == "DIE_HARD":
                    if curr_round <= 4 and norm_pos == "RB":
                        if rb_cnt < 2:
                            score += 200.0
                    score += random.uniform(-5.0, 10.0)
                    if cand.get("age", 25) >= 27:
                        score *= 1.1
                elif persona == "TRADE_FIEND":
                    if adp < current_pick:
                        score += (current_pick - adp) * 4.5
                    if cand.get("projected_points", 0) > 200.0:
                        score *= 1.12
                elif persona == "UNDERDOG":
                    if cand.get("age", 25) <= 24:
                        score *= 1.25
                    score += random.uniform(-5.0, 15.0)

                if score > best_score:
                    best_score = score
                    best_candidate = cand

            # Fallback if all candidates filtered out (e.g. late rounds)
            if not best_candidate:
                best_candidate = candidates[0]

            chosen_player_id = best_candidate["id"]

    return make_draft_pick(draft_id, chosen_player_id, picked_by="AI")


def simulate_until_user(draft_id: int) -> Dict[str, Any]:
    """Loops simulate_ai_pick until the user is on the clock or draft completes."""
    state = get_draft_state(draft_id)
    if state["status"] == "PENDING":
        state = start_draft(draft_id)

    max_steps = state["total_picks"]
    step = 0
    while step < max_steps:
        if state["status"] == "COMPLETED" or state["is_user_turn"]:
            break
        state = simulate_ai_pick(draft_id)
        step += 1

    return state


def simulate_entire_draft(draft_id: int) -> Dict[str, Any]:
    """Simulates all remaining draft picks to completion."""
    state = get_draft_state(draft_id)
    if state["status"] == "PENDING":
        state = start_draft(draft_id)

    max_steps = state["total_picks"]
    step = 0
    while step < max_steps:
        if state["status"] == "COMPLETED":
            break
        state = simulate_ai_pick(draft_id)
        step += 1

    return state


def finalize_draft(draft_id: int) -> Dict[str, Any]:
    """Completes draft, computes grades, records best value / biggest reach, and sets rosters."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM napper_drafts WHERE id = %s", (draft_id,))
            draft = cur.fetchone()
            if not draft:
                raise ValueError(f"Draft {draft_id} not found")

            # Mark COMPLETED
            cur.execute("UPDATE napper_drafts SET status = 'COMPLETED' WHERE id = %s", (draft_id,))

            prev_season = get_previous_season(cur)

            # Fetch all picks with drafted player info
            cur.execute("""
                SELECT dp.overall_pick, dp.slot_num, dp.team_id, dp.team_name, dp.player_id,
                       p.player_name, p.full_name, COALESCE(ps.position, 'FLEX') as position,
                       COALESCE(NULLIF(ss.total_fantasy_points, 0), NULLIF(fp.ros_total_points, 0), fp.projected_points * 17, 180.0) as projected_points,
                       COALESCE(NULLIF(ss.ppg_fantasy_points, 0), fp.avg_points, 10.5) as ppg,
                       COALESCE(fp.adp_rank, 150) as adp_rank
                FROM napper_draft_picks dp
                JOIN players p ON p.id = dp.player_id
                LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                LEFT JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.season = %s AND fp.week = 1
                LEFT JOIN season_stats ss ON ss.player_id = p.id AND ss.season = %s
                WHERE dp.draft_id = %s AND dp.player_id IS NOT NULL
                ORDER BY dp.overall_pick ASC
            """, (prev_season, prev_season, prev_season, draft_id))
            picks = cur.fetchall()

            # Group by slot
            team_picks = defaultdict(list)
            best_value = None
            max_value_diff = -9999
            biggest_reach = None
            max_reach_diff = -9999

            for p in picks:
                team_picks[p["slot_num"]].append(p)
                p_name = p["full_name"] or p["player_name"]
                adp = int(p["adp_rank"])
                overall = int(p["overall_pick"])

                # Value: fell the most below ADP (overall > adp)
                val_diff = overall - adp
                if val_diff > max_value_diff:
                    max_value_diff = val_diff
                    best_value = {
                        "player_id": p["player_id"],
                        "player_name": p_name,
                        "position": p["position"],
                        "overall_pick": overall,
                        "adp_rank": adp,
                        "team_name": p["team_name"],
                        "diff": val_diff,
                    }

                # Reach: picked furthest ahead of ADP (adp > overall)
                reach_diff = adp - overall
                if reach_diff > max_reach_diff:
                    max_reach_diff = reach_diff
                    biggest_reach = {
                        "player_id": p["player_id"],
                        "player_name": p_name,
                        "position": p["position"],
                        "overall_pick": overall,
                        "adp_rank": adp,
                        "team_name": p["team_name"],
                        "diff": reach_diff,
                    }

            # Calculate team scores & letter grades
            team_scores = []
            for slot_num, p_list in team_picks.items():
                total_proj = sum(float(x["projected_points"]) for x in p_list)
                positions = {x["position"] for x in p_list}
                norm_pos = {"RB" if p in ("HB", "FB", "RB") else p for p in positions}

                # Balance penalty if completely missing core positions
                balance_pen = 0.0
                if "QB" not in norm_pos: balance_pen += 250.0
                if "RB" not in norm_pos: balance_pen += 300.0
                if "WR" not in norm_pos: balance_pen += 250.0
                if "TE" not in norm_pos: balance_pen += 150.0

                comp_score = total_proj - balance_pen
                team_name = p_list[0]["team_name"] if p_list else f"Team {slot_num}"
                team_scores.append({
                    "slot_num": slot_num,
                    "team_name": team_name,
                    "total_proj": round(total_proj, 1),
                    "score": comp_score,
                })

            team_scores.sort(key=lambda x: x["score"], reverse=True)
            n_teams = max(len(team_scores), 1)

            grades_map = {}
            for rank_idx, tm in enumerate(team_scores):
                pct = rank_idx / n_teams
                if pct < 0.15:
                    grade = "A+"
                elif pct < 0.35:
                    grade = "A"
                elif pct < 0.60:
                    grade = "B+"
                elif pct < 0.80:
                    grade = "B"
                elif pct < 0.90:
                    grade = "C+"
                elif pct < 0.95:
                    grade = "C"
                else:
                    grade = "D"

                grades_map[str(tm["slot_num"])] = {
                    "team_name": tm["team_name"],
                    "grade": grade,
                    "projected_points": tm["total_proj"],
                    "rank": rank_idx + 1,
                }

            recap = {
                "grades": grades_map,
                "best_value": best_value,
                "biggest_reach": biggest_reach,
            }

            settings = draft["settings"] or {}
            settings["recap"] = recap
            cur.execute("""
                UPDATE napper_drafts
                SET settings = %s
                WHERE id = %s
            """, (psycopg.types.json.Jsonb(settings), draft_id))

            # If real league draft: populate rosters & schedule
            league_id = draft["league_id"]
            if league_id:
                cur.execute("SELECT * FROM napper_leagues WHERE id = %s", (league_id,))
                league = cur.fetchone()
                roster_fmt = league["roster_format"] or {
                    "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 1, "K": 1, "DEF": 1, "BN": 6
                }

                cur.execute("SELECT id, team_name FROM napper_teams WHERE league_id = %s", (league_id,))
                league_teams = cur.fetchall()
                team_id_by_name = {t["team_name"]: t["id"] for t in league_teams}

                # Clear existing pre-draft rosters
                cur.execute("DELETE FROM napper_rosters WHERE league_id = %s", (league_id,))

                # Slot assignment helper
                starter_slots = []
                for s_key in ["QB", "RB", "WR", "TE", "FLEX", "SUPERFLEX", "K", "DEF"]:
                    count = int(roster_fmt.get(s_key, 1))
                    for _ in range(count):
                        starter_slots.append(s_key)

                for slot_num, p_list in team_picks.items():
                    tm_name = p_list[0]["team_name"]
                    t_id = team_id_by_name.get(tm_name)
                    if not t_id:
                        continue

                    # Sort players by projected points
                    sorted_players = sorted(p_list, key=lambda x: float(x["projected_points"]), reverse=True)

                    assigned_slots = []
                    unassigned = list(sorted_players)

                    # Assign specific positions first
                    for s_key in ["QB", "RB", "WR", "TE", "K", "DEF"]:
                        count = int(roster_fmt.get(s_key, 1))
                        for _ in range(count):
                            match_idx = None
                            for idx, pl in enumerate(unassigned):
                                pos = pl["position"]
                                norm_pos = "RB" if pos in ("HB", "FB", "RB") else pos
                                if norm_pos == s_key:
                                    match_idx = idx
                                    break
                            if match_idx is not None:
                                pl = unassigned.pop(match_idx)
                                assigned_slots.append((pl["player_id"], s_key))

                    # FLEX (RB, WR, TE)
                    flex_count = int(roster_fmt.get("FLEX", 1))
                    for _ in range(flex_count):
                        match_idx = None
                        for idx, pl in enumerate(unassigned):
                            pos = pl["position"]
                            norm_pos = "RB" if pos in ("HB", "FB", "RB") else pos
                            if norm_pos in ("RB", "WR", "TE"):
                                match_idx = idx
                                break
                        if match_idx is not None:
                            pl = unassigned.pop(match_idx)
                            assigned_slots.append((pl["player_id"], "FLEX"))

                    # SUPERFLEX (QB, RB, WR, TE)
                    sf_count = int(roster_fmt.get("SUPERFLEX", 1))
                    for _ in range(sf_count):
                        match_idx = None
                        for idx, pl in enumerate(unassigned):
                            pos = pl["position"]
                            norm_pos = "RB" if pos in ("HB", "FB", "RB") else pos
                            if norm_pos in ("QB", "RB", "WR", "TE"):
                                match_idx = idx
                                break
                        if match_idx is not None:
                            pl = unassigned.pop(match_idx)
                            assigned_slots.append((pl["player_id"], "SUPERFLEX"))

                    # Remaining to Bench (BN)
                    for pl in unassigned:
                        assigned_slots.append((pl["player_id"], "BN"))

                    # Insert into napper_rosters for weeks 1..14
                    for w in range(1, 15):
                        for pid, slot_name in assigned_slots:
                            cur.execute("""
                                INSERT INTO napper_rosters (league_id, team_id, week, player_id, slot)
                                VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT (team_id, week, player_id) DO NOTHING
                            """, (league_id, t_id, w, pid, slot_name))

                # Check if matchups exist; if not, generate 14-week round-robin
                cur.execute("SELECT COUNT(*) FROM napper_matchups WHERE league_id = %s", (league_id,))
                matchup_count = cur.fetchone()["count"]

                if matchup_count == 0:
                    team_ids = [t["id"] for t in league_teams]
                    n = len(team_ids)
                    if n >= 2:
                        for w in range(1, 15):
                            round_idx = (w - 1) % (n - 1)
                            rotated = [team_ids[0]] + team_ids[1:][round_idx:] + team_ids[1:][:round_idx]
                            for match_i in range(n // 2):
                                home = rotated[match_i]
                                away = rotated[n - 1 - match_i]
                                cur.execute("""
                                    INSERT INTO napper_matchups (
                                        league_id, week, home_team_id, away_team_id,
                                        home_score, away_score, home_proj, away_proj,
                                        win_prob_home, is_final
                                    ) VALUES (%s, %s, %s, %s, 0.00, 0.00, 118.5, 114.2, 0.520, FALSE)
                                """, (league_id, w, home, away))

                # Mark league IN_SEASON
                cur.execute("""
                    UPDATE napper_leagues
                    SET status = 'IN_SEASON', current_week = 1
                    WHERE id = %s
                """, (league_id,))

                # Post draft recap card in chat
                recap_card = {
                    "type": "DRAFT_RECAP",
                    "title": "Draft Concluded!",
                    "best_value": best_value,
                    "biggest_reach": biggest_reach,
                    "grades": grades_map,
                }
                cur.execute("""
                    INSERT INTO napper_chat (league_id, sender_type, sender_name, sender_handle, avatar_url, message_text, card_payload)
                    VALUES (%s, 'SYSTEM_BOT', 'Draft Commissioner', '@DraftBot', '/static/players/robot_avatar.webp',
                            '🏆 The Draft has officially concluded! Rosters are set and the season has kicked off.', %s)
                """, (league_id, psycopg.types.json.Jsonb(recap_card)))

        conn.commit()

    return get_draft_state(draft_id)
