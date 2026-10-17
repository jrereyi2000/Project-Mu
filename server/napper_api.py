"""Napper API Router (server/napper_api.py).

Provides endpoints for the Napper interactive fantasy platform:
- Profiles & Leagues list (3 reference leagues with accurate scores and records)
- Matchups supporting week switching (weeks 1 to 17) with ordered starter and bench slots
- Atomic Lineup validation & starter swaps with matchup projection recalculation
- Waivers with claim/drop execution and chat announcements
- Proactive Trades Hub with accept/reject responses, roster swaps, and chat announcements
- League Chat & Automated Bot Awards stream
"""

import json
import logging
import math
import os
import random
from collections import defaultdict
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
import psycopg
from psycopg.rows import dict_row
from engines import napper_draft_engine

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/napper", tags=["napper"])
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


# ──────────────────────────────── Models ────────────────────────────────

class LeagueCreateRequest(BaseModel):
    name: str
    teams_count: int = 8
    scoring_type: str = "PPR"
    status: str = "IN_SEASON"
    is_superflex: bool = True
    avatar_url: Optional[str] = None


class LineupSwap(BaseModel):
    player_id: int
    new_slot: str


class LineupRequest(BaseModel):
    team_id: int
    swaps: List[LineupSwap]
    week: Optional[int] = None


class WaiverClaimRequest(BaseModel):
    team_id: int
    add_player_id: int
    drop_player_id: Optional[int] = None
    week: Optional[int] = None


class TradeProposeRequest(BaseModel):
    proposer_team_id: int
    target_team_id: int
    offered_player_ids: List[int]
    requested_player_ids: List[int]
    ai_rationale: Optional[str] = None


class TradeRespondRequest(BaseModel):
    action: str  # ACCEPT or REJECT


class ChatMessageRequest(BaseModel):
    message_text: str
    sender_type: Optional[str] = "USER"
    sender_name: Optional[str] = "The Straw Hat Pirates"
    sender_handle: Optional[str] = "@jrereyi2000"
    card_payload: Optional[Dict[str, Any]] = None


class MockDraftCreateRequest(BaseModel):
    teams_count: int = 10
    scoring_type: str = "PPR"
    user_slot: Optional[int] = None
    rounds: int = 15
    is_superflex: bool = True
    randomize_user_slot: bool = True
    archetype_composition: Optional[Dict[str, int]] = None
    archetypes: Optional[List[str]] = None


class RandomizeOrderRequest(BaseModel):
    keep_user_slot: bool = False


class SetArchetypesRequest(BaseModel):
    archetype_composition: Optional[Dict[str, int]] = None
    archetypes: Optional[List[str]] = None


class ClaimSlotRequest(BaseModel):
    slot: int


class DraftPickRequest(BaseModel):
    player_id: int


# Positional eligibility map
ELIGIBLE_POSITIONS: Dict[str, set] = {
    "QB": {"QB"},
    "RB": {"RB", "FB", "HB"},
    "WR": {"WR"},
    "TE": {"TE"},
    "FLEX": {"RB", "WR", "TE", "FB", "HB"},
    "SUPERFLEX": {"QB", "RB", "WR", "TE", "FB", "HB"},
    "K": {"K"},
    "DEF": {"DEF"},
    "BN": {"QB", "RB", "WR", "TE", "FB", "HB", "K", "DEF"},
}


def _get_slot_order(roster_fmt: Optional[Dict[str, int]]) -> List[str]:
    """Generates ordered starting slot names from league roster_format."""
    if not roster_fmt:
        return ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPERFLEX", "K", "DEF"]

    order: List[str] = []
    for s in ["QB", "RB", "WR", "TE", "FLEX", "SUPERFLEX", "K", "DEF"]:
        count = int(roster_fmt.get(s, 0))
        for _ in range(count):
            order.append(s)

    return order if order else ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPERFLEX", "K", "DEF"]


def _ensure_matchup_and_rosters(cur, league_id: int, week: int):
    """Ensures matchups and rosters exist for the given week (supports weeks 1 to 17)."""
    # 1. Ensure rosters for this week
    cur.execute(
        "SELECT COUNT(*) as cnt FROM napper_rosters WHERE league_id = %s AND week = %s",
        (league_id, week)
    )
    if cur.fetchone()["cnt"] == 0:
        # Check if rosters exist for any week in this league
        cur.execute(
            "SELECT week FROM napper_rosters WHERE league_id = %s ORDER BY ABS(week - %s) LIMIT 1",
            (league_id, week)
        )
        src_row = cur.fetchone()
        if src_row:
            src_week = src_row["week"]
            cur.execute("""
                INSERT INTO napper_rosters (league_id, team_id, week, player_id, slot)
                SELECT league_id, team_id, %s, player_id, slot
                FROM napper_rosters
                WHERE league_id = %s AND week = %s
                ON CONFLICT (team_id, week, player_id) DO NOTHING
            """, (week, league_id, src_week))
        else:
            # If this league has NO rosters at all (e.g. League 2 or 3), copy baseline rosters from League 1 or populate
            cur.execute("SELECT id FROM napper_teams WHERE league_id = %s ORDER BY id", (league_id,))
            target_teams = [t["id"] for t in cur.fetchall()]

            # Fetch a sample template of rosters from League 1
            cur.execute("""
                SELECT team_id, player_id, slot
                FROM napper_rosters
                WHERE league_id = 1 AND week = 6
                ORDER BY team_id, id
            """)
            template_rows = cur.fetchall()
            if template_rows and target_teams:
                # Group template by source team
                tmpl_by_team = defaultdict(list)
                for r in template_rows:
                    tmpl_by_team[r["team_id"]].append((r["player_id"], r["slot"]))
                tmpl_teams = list(tmpl_by_team.keys())

                for idx, t_id in enumerate(target_teams):
                    src_tid = tmpl_teams[idx % len(tmpl_teams)]
                    for pid, slot in tmpl_by_team[src_tid]:
                        cur.execute("""
                            INSERT INTO napper_rosters (league_id, team_id, week, player_id, slot)
                            VALUES (%s, %s, %s, %s, %s)
                            ON CONFLICT (team_id, week, player_id) DO NOTHING
                        """, (league_id, t_id, week, pid, slot))

    # 2. Ensure matchups for this week
    cur.execute(
        "SELECT COUNT(*) as cnt FROM napper_matchups WHERE league_id = %s AND week = %s",
        (league_id, week)
    )
    if cur.fetchone()["cnt"] == 0:
        cur.execute("""
            SELECT id FROM napper_teams
            WHERE league_id = %s
            ORDER BY wins DESC, points_for DESC, id ASC
        """, (league_id,))
        teams = [r["id"] for r in cur.fetchall()]
        if len(teams) >= 2:
            n = len(teams)
            for i in range(n // 2):
                h_id = teams[i]
                a_id = teams[n - 1 - i]
                # Default projected points
                h_proj = 135.50 - i * 4.0
                a_proj = 125.00 - i * 3.5
                win_prob = 0.55 if h_proj >= a_proj else 0.45
                cur.execute("""
                    INSERT INTO napper_matchups (
                        league_id, week, home_team_id, away_team_id,
                        home_score, away_score, home_proj, away_proj,
                        win_prob_home, is_final
                    ) VALUES (%s, %s, %s, %s, 0.00, 0.00, %s, %s, %s, FALSE)
                """, (league_id, week, h_id, a_id, h_proj, a_proj, win_prob))


# ──────────────────────────────── Endpoints ────────────────────────────────

@router.get("/profile")
def get_user_profile():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, handle, display_name, avatar_url, trophies, created_at
                FROM napper_user_profiles
                ORDER BY id LIMIT 1
            """)
            prof = cur.fetchone()
            if not prof:
                prof = {
                    "id": 1,
                    "handle": "jrereyi2000",
                    "display_name": "The Straw Hat Pirates",
                    "avatar_url": "/static/players/robot_avatar.webp",
                    "trophies": 3,
                }

            # Get pending incoming trade count
            cur.execute("""
                SELECT COUNT(*) as cnt FROM napper_trades nt
                JOIN napper_teams tm ON tm.id = nt.target_team_id
                WHERE tm.user_id = %s AND nt.status = 'PENDING'
            """, (prof["id"],))
            pending_trades = cur.fetchone()["cnt"]

            return {
                "profile": prof,
                "pending_trades_count": pending_trades,
            }


@router.get("/leagues")
def get_user_leagues():
    with get_db() as conn:
        with conn.cursor() as cur:
            # Query all 3 reference leagues with user's team joined
            cur.execute("""
                SELECT l.id, l.name, l.season, l.teams_count, l.scoring_type, l.status, l.current_week, l.avatar_url,
                       t.id as user_team_id, t.team_name as user_team_name, t.wins, t.losses, t.ties,
                       t.points_for, t.points_against
                FROM napper_leagues l
                LEFT JOIN napper_teams t ON t.league_id = l.id AND t.user_id IS NOT NULL
                ORDER BY l.id
            """)
            leagues = cur.fetchall()

            results = []
            for lg in leagues:
                lid = lg["id"]
                cw = lg["current_week"] or 1
                u_tid = lg["user_team_id"]

                wins = lg["wins"] if lg["wins"] is not None else 0
                losses = lg["losses"] if lg["losses"] is not None else 0
                ties = lg["ties"] if lg["ties"] is not None else 0
                record = f"{wins}-{losses}" + (f"-{ties}" if ties else "")

                # Query user matchup for current week
                match = None
                if u_tid:
                    cur.execute("""
                        SELECT m.home_team_id, m.away_team_id, m.home_score, m.away_score, m.home_proj, m.away_proj,
                               th.team_name as home_name, ta.team_name as away_name
                        FROM napper_matchups m
                        JOIN napper_teams th ON th.id = m.home_team_id
                        JOIN napper_teams ta ON ta.id = m.away_team_id
                        WHERE m.league_id = %s AND m.week = %s AND (m.home_team_id = %s OR m.away_team_id = %s)
                        LIMIT 1
                    """, (lid, cw, u_tid, u_tid))
                    match = cur.fetchone()

                if match and u_tid:
                    is_home = (match["home_team_id"] == u_tid)
                    u_score = float(match["home_score"] if is_home else match["away_score"])
                    o_score = float(match["away_score"] if is_home else match["home_score"])
                    u_proj = float(match["home_proj"] if is_home else match["away_proj"])
                    o_proj = float(match["away_proj"] if is_home else match["home_proj"])
                    opp_name = match["away_name"] if is_home else match["home_name"]
                else:
                    # Find any opponent team in this league
                    cur.execute("SELECT team_name FROM napper_teams WHERE league_id = %s AND id != %s LIMIT 1", (lid, u_tid or 0))
                    opp_row = cur.fetchone()
                    opp_name = opp_row["team_name"] if opp_row else "Team 2"
                    u_score, o_score = 0.0, 0.0
                    u_proj, o_proj = 0.0, 0.0

                results.append({
                    "id": lg["id"],
                    "name": lg["name"],
                    "season": lg["season"],
                    "teams_count": lg["teams_count"],
                    "scoring_type": lg["scoring_type"],
                    "status": lg["status"],
                    "avatar_url": lg.get("avatar_url"),
                    "current_week": cw,
                    "user_team_id": u_tid or 1,
                    "user_team_name": lg["user_team_name"] or "The Straw Hat Pirates",
                    "record": record,
                    "wins": wins,
                    "losses": losses,
                    "ties": ties,
                    "user_score": u_score,
                    "user_proj": u_proj,
                    "opponent_team_name": opp_name,
                    "opponent_score": o_score,
                    "opponent_proj": o_proj,
                    "unplayed_count": 10 if lg["status"] == "IN_SEASON" else 0,
                })

            return {"leagues": results}


@router.get("/league/{league_id}")
def get_league_detail(league_id: int):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, name, season, teams_count, scoring_type, roster_format, status, current_week, avatar_url
                FROM napper_leagues WHERE id = %s
            """, (league_id,))
            league = cur.fetchone()
            if not league:
                raise HTTPException(status_code=404, detail="League not found")

            # Standings
            cur.execute("""
                SELECT id, team_name, manager_handle, is_ai, persona, wins, losses, ties,
                       points_for, points_against, seed, (user_id IS NOT NULL) as is_user
                FROM napper_teams
                WHERE league_id = %s
                ORDER BY wins DESC, points_for DESC
            """, (league_id,))
            teams = cur.fetchall()

            return {
                "league": league,
                "teams": teams,
            }


@router.post("/leagues/create")
def create_league(req: LeagueCreateRequest):
    """Creates a new fantasy league.

    User Rule: When a new league is created, any existing seed leagues (is_seed = TRUE)
    are permanently removed from the system.
    """
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="League name cannot be empty")

    teams_count = req.teams_count if req.teams_count in (8, 10, 12) else 8
    scoring_type = req.scoring_type if req.scoring_type in ("PPR", "HALF_PPR", "STANDARD") else "PPR"
    status = "PRE_DRAFT" if req.status == "PRE_DRAFT" else "IN_SEASON"

    if req.is_superflex:
        roster_format = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 1, "K": 1, "DEF": 1, "BN": 6}
    else:
        roster_format = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 0, "K": 1, "DEF": 1, "BN": 6}

    avatar = req.avatar_url or "https://images.unsplash.com/photo-1508098682722-e99c43a406b2?auto=format&fit=crop&w=256&h=256&q=80"
    current_week = 6 if status == "IN_SEASON" else 1

    with get_db() as conn:
        with conn.cursor() as cur:
            prev_season = get_previous_season(cur)

            # 1. Remove seed leagues if any exist
            cur.execute("DELETE FROM napper_leagues WHERE is_seed = TRUE")

            # 2. Insert new user league
            cur.execute("""
                INSERT INTO napper_leagues (
                    name, season, teams_count, scoring_type, roster_format,
                    status, avatar_url, current_week, is_seed
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE)
                RETURNING id, name, season, teams_count, scoring_type, status, current_week, avatar_url
            """, (name, prev_season, teams_count, scoring_type, psycopg.types.json.Jsonb(roster_format), status, avatar, current_week))
            new_league = cur.fetchone()
            league_id = new_league["id"]

            # 3. Fetch or create user profile
            cur.execute("SELECT id, handle, display_name, avatar_url FROM napper_user_profiles LIMIT 1")
            user_prof = cur.fetchone()
            if not user_prof:
                cur.execute("""
                    INSERT INTO napper_user_profiles (handle, display_name, avatar_url, trophies)
                    VALUES ('jrereyi2000', 'The Straw Hat Pirates',
                            'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=256&h=256&q=80', 3)
                    RETURNING id, handle, display_name, avatar_url
                """)
                user_prof = cur.fetchone()
            user_id = user_prof["id"]

            # 4. Create User's Team
            cur.execute("""
                INSERT INTO napper_teams (
                    league_id, user_id, team_name, manager_handle, avatar_url,
                    is_ai, persona, wins, losses, ties, points_for, points_against, seed
                ) VALUES (%s, %s, %s, %s, %s, FALSE, 'USER', %s, %s, 0, %s, %s, 1)
                RETURNING id
            """, (
                league_id, user_id, user_prof["display_name"], user_prof["handle"], user_prof["avatar_url"],
                5 if status == "IN_SEASON" else 0,
                0 if status == "IN_SEASON" else 0,
                785.4 if status == "IN_SEASON" else 0.0,
                610.2 if status == "IN_SEASON" else 0.0
            ))
            user_team_id = cur.fetchone()["id"]

            # 5. Create AI Teams
            AI_CANDIDATES = [
                {"name": "Team Rocket", "handle": "jesse_james", "persona": "TRASH_TALKER", "w": 4, "l": 1, "pf": 712.5, "pa": 645.0, "seed": 2},
                {"name": "Gotham Rogues", "handle": "bruce_w", "persona": "ANALYTICS_NERD", "w": 3, "l": 2, "pf": 654.2, "pa": 620.1, "seed": 3},
                {"name": "Metropolis Titans", "handle": "clark_k", "persona": "DIE_HARD", "w": 3, "l": 2, "pf": 641.8, "pa": 632.5, "seed": 4},
                {"name": "Asgard Hammers", "handle": "thor_o", "persona": "WAIVER_HAWK", "w": 2, "l": 3, "pf": 592.1, "pa": 651.4, "seed": 5},
                {"name": "Central City Streaks", "handle": "barry_a", "persona": "ZERO_RB_ZEALOT", "w": 2, "l": 3, "pf": 583.4, "pa": 662.0, "seed": 6},
                {"name": "Starling Archers", "handle": "oliver_q", "persona": "TRASH_TALKER", "w": 1, "l": 4, "pf": 542.0, "pa": 681.3, "seed": 7},
                {"name": "Wakanda Panthers", "handle": "tchalla", "persona": "DIE_HARD", "w": 0, "l": 5, "pf": 511.9, "pa": 720.5, "seed": 8},
                {"name": "Atlantis Tridents", "handle": "arthur_c", "persona": "WAIVER_HAWK", "w": 3, "l": 2, "pf": 660.0, "pa": 640.0, "seed": 9},
                {"name": "The Daily Planet", "handle": "lois_l", "persona": "ANALYTICS_NERD", "w": 2, "l": 3, "pf": 640.0, "pa": 660.0, "seed": 10},
                {"name": "Stark Industries", "handle": "tony_s", "persona": "TRASH_TALKER", "w": 4, "l": 1, "pf": 720.0, "pa": 630.0, "seed": 11},
                {"name": "X-Men First Class", "handle": "charles_x", "persona": "DIE_HARD", "w": 1, "l": 4, "pf": 595.0, "pa": 700.0, "seed": 12},
            ]

            team_ids = [user_team_id]
            for i in range(teams_count - 1):
                cand = AI_CANDIDATES[i % len(AI_CANDIDATES)]
                cur.execute("""
                    INSERT INTO napper_teams (
                        league_id, user_id, team_name, manager_handle, avatar_url,
                        is_ai, persona, wins, losses, ties, points_for, points_against, seed
                    ) VALUES (%s, NULL, %s, %s, %s, TRUE, %s, %s, %s, 0, %s, %s, %s)
                    RETURNING id
                """, (
                    league_id, cand["name"], cand["handle"],
                    f"https://api.dicebear.com/7.x/identicon/svg?seed={cand['name']}",
                    cand["persona"],
                    cand["w"] if status == "IN_SEASON" else 0,
                    cand["l"] if status == "IN_SEASON" else 0,
                    cand["pf"] if status == "IN_SEASON" else 0.0,
                    cand["pa"] if status == "IN_SEASON" else 0.0,
                    cand["seed"]
                ))
                team_ids.append(cur.fetchone()["id"])

            # 6. If IN_SEASON, populate reference rosters & schedule
            if status == "IN_SEASON":
                cur.execute("""
                    SELECT p.id, p.player_name, ps.position
                    FROM players p
                    JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                    ORDER BY p.id ASC
                    LIMIT 300
                """, (prev_season,))
                avail = cur.fetchall()

                pos_pools = defaultdict(list)
                for p in avail:
                    pos_pools[p["position"]].append(p["id"])

                slots_needed = _get_slot_order(roster_format) + ["BN"] * int(roster_format.get("BN", 6))

                used_pids = set()
                for tid in team_ids:
                    team_assigned = []
                    for slot in slots_needed:
                        eligible_poses = ELIGIBLE_POSITIONS.get(slot, {"WR", "RB"})
                        picked_pid = None
                        for ep in eligible_poses:
                            for candidate in pos_pools.get(ep, []):
                                if candidate not in used_pids:
                                    picked_pid = candidate
                                    break
                            if picked_pid:
                                break
                        if not picked_pid and avail:
                            for cand in avail:
                                if cand["id"] not in used_pids:
                                    picked_pid = cand["id"]
                                    break
                        if picked_pid:
                            used_pids.add(picked_pid)
                            team_assigned.append((picked_pid, slot))

                    for w in range(1, 15):
                        for pid, slot in team_assigned:
                            cur.execute("""
                                INSERT INTO napper_rosters (league_id, team_id, week, player_id, slot)
                                VALUES (%s, %s, %s, %s, %s)
                                ON CONFLICT (team_id, week, player_id) DO NOTHING
                            """, (league_id, tid, w, pid, slot))

                n = len(team_ids)
                t_list = list(team_ids)
                for w in range(1, 15):
                    round_idx = (w - 1) % (n - 1)
                    rotated = [t_list[0]] + t_list[1:][round_idx:] + t_list[1:][:round_idx]
                    for match_i in range(n // 2):
                        home = rotated[match_i]
                        away = rotated[n - 1 - match_i]
                        is_final = (w < current_week)
                        h_score = round(random.uniform(90.0, 140.0), 2) if is_final else 0.00
                        a_score = round(random.uniform(85.0, 135.0), 2) if is_final else 0.00
                        cur.execute("""
                            INSERT INTO napper_matchups (
                                league_id, week, home_team_id, away_team_id,
                                home_score, away_score, home_proj, away_proj,
                                win_prob_home, is_final
                            ) VALUES (%s, %s, %s, %s, %s, %s, 118.5, 114.2, 0.520, %s)
                        """, (league_id, w, home, away, h_score, a_score, is_final))

                welcome_card = {
                    "type": "LEAGUE_CREATED",
                    "title": f"Welcome to {name}!",
                    "highlight": f"{teams_count} Teams · {scoring_type} · Superflex" if req.is_superflex else f"{teams_count} Teams · {scoring_type}",
                    "commentary": "All rosters and matchups are locked in for Week 6. Make your waiver moves and set your lineups!",
                }
                cur.execute("""
                    INSERT INTO napper_chat (league_id, sender_type, sender_name, sender_handle, avatar_url, message_text, card_payload)
                    VALUES (%s, 'SYSTEM_BOT', 'Napper Commissioner', '@CommissionerBot', '/static/players/robot_avatar.webp', %s, %s)
                """, (league_id, f"🏆 {name} has officially kicked off!", psycopg.types.json.Jsonb(welcome_card)))
            else:
                welcome_card = {
                    "type": "PRE_DRAFT_READY",
                    "title": f"{name} Draft Lobby Open",
                    "highlight": f"{teams_count} Teams · Snake Draft Order",
                    "commentary": "Draft order has been randomized. Join the draft room when you're ready to make your selections!",
                }
                cur.execute("""
                    INSERT INTO napper_chat (league_id, sender_type, sender_name, sender_handle, avatar_url, message_text, card_payload)
                    VALUES (%s, 'SYSTEM_BOT', 'Draft Commissioner', '@DraftBot', '/static/players/robot_avatar.webp', %s, %s)
                """, (league_id, f"⚡ {name} draft room is open in PRE_DRAFT mode!", psycopg.types.json.Jsonb(welcome_card)))

        conn.commit()

    return {
        "status": "success",
        "league": new_league,
        "user_team_id": user_team_id,
    }


@router.get("/league/{league_id}/teams_rosters")
def get_league_teams_rosters(league_id: int, week: Optional[int] = Query(None)):
    """Returns all teams in a league and their active rosters for trading/proposals."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_week FROM napper_leagues WHERE id = %s", (league_id,))
            lg = cur.fetchone()
            if not lg:
                raise HTTPException(status_code=404, detail="League not found")
            wk = int(week) if week is not None else (lg["current_week"] or 6)

            cur.execute("""
                SELECT id, team_name, manager_handle, avatar_url, (user_id IS NOT NULL) as is_user
                FROM napper_teams
                WHERE league_id = %s
                ORDER BY (user_id IS NOT NULL) DESC, team_name ASC
            """, (league_id,))
            teams = cur.fetchall()

            prev_season = get_previous_season(cur)
            teams_data = []
            for tm in teams:
                cur.execute("""
                    SELECT p.id, p.player_name, p.full_name, COALESCE(ps.position, 'FLEX') as position,
                           nr.slot, COALESCE(fp.projected_points, 10.0) as proj_fpts
                    FROM napper_rosters nr
                    JOIN players p ON p.id = nr.player_id
                    LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                    LEFT JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.week = %s AND fp.season = %s
                    WHERE nr.league_id = %s AND nr.team_id = %s AND nr.week = %s
                    ORDER BY nr.slot != 'BN' DESC, proj_fpts DESC
                """, (prev_season, wk, prev_season, league_id, tm["id"], wk))
                players = []
                for p in cur.fetchall():
                    players.append({
                        "id": p["id"],
                        "name": p["player_name"] or p["full_name"],
                        "position": p["position"],
                        "slot": p["slot"],
                        "proj_fpts": float(p["proj_fpts"]),
                        "headshot_url": f"/static/players/{p['id']}.webp",
                    })
                teams_data.append({
                    "id": tm["id"],
                    "team_name": tm["team_name"],
                    "manager_handle": tm["manager_handle"],
                    "is_user": tm["is_user"],
                    "players": players,
                })

            return {"teams": teams_data}


@router.get("/league/{league_id}/matchup")
def get_league_matchup(league_id: int, week: Optional[int] = Query(None)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_week, roster_format FROM napper_leagues WHERE id = %s", (league_id,))
            lg = cur.fetchone()
            if not lg:
                raise HTTPException(status_code=404, detail="League not found")

            # Support week switching 1 to 17
            wk = int(week) if week is not None else (lg["current_week"] or 6)
            wk = max(1, min(17, wk))

            # Ensure matchups and rosters exist for this week
            _ensure_matchup_and_rosters(cur, league_id, wk)
            conn.commit()

            # Find user team
            cur.execute("SELECT id FROM napper_teams WHERE league_id = %s AND user_id IS NOT NULL LIMIT 1", (league_id,))
            u_row = cur.fetchone()
            user_team_id = u_row["id"] if u_row else None

            # Find matchup for this week (prioritizing user's team matchup)
            match = None
            if user_team_id:
                cur.execute("""
                    SELECT m.id, m.home_team_id, m.away_team_id, m.home_score, m.away_score,
                           m.home_proj, m.away_proj, m.win_prob_home, m.is_final
                    FROM napper_matchups m
                    WHERE m.league_id = %s AND m.week = %s AND (m.home_team_id = %s OR m.away_team_id = %s)
                    LIMIT 1
                """, (league_id, wk, user_team_id, user_team_id))
                match = cur.fetchone()

            if not match:
                cur.execute("""
                    SELECT m.id, m.home_team_id, m.away_team_id, m.home_score, m.away_score,
                           m.home_proj, m.away_proj, m.win_prob_home, m.is_final
                    FROM napper_matchups m WHERE m.league_id = %s AND m.week = %s LIMIT 1
                """, (league_id, wk))
                match = cur.fetchone()

            if not match:
                raise HTTPException(status_code=404, detail=f"No matchup found for Week {wk}")

            left_team_id = match["home_team_id"]
            right_team_id = match["away_team_id"]

            # Query team details
            cur.execute("""
                SELECT id, team_name, manager_handle, avatar_url, wins, losses, ties, points_for, points_against, seed
                FROM napper_teams WHERE id IN (%s, %s)
            """, (left_team_id, right_team_id))
            teams_map = {t["id"]: t for t in cur.fetchall()}
            left_team_meta = teams_map.get(left_team_id, {})
            right_team_meta = teams_map.get(right_team_id, {})

            prev_season = get_previous_season(cur)

            # Query rosters for both teams in this week
            cur.execute("""
                SELECT nr.team_id, nr.slot, nr.player_id, p.player_name, p.full_name, p.espn_id,
                       COALESCE(t.abbreviation, 'NFL') as nfl_team,
                       COALESCE(ps.position, 'FLEX') as position,
                       COALESCE(fp.projected_points, 12.0) as proj_pts
                FROM napper_rosters nr
                JOIN players p ON p.id = nr.player_id
                LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                LEFT JOIN teams t ON t.id = ps.team_id
                LEFT JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.week = %s AND fp.season = %s
                WHERE nr.league_id = %s AND nr.week = %s AND nr.team_id IN (%s, %s)
            """, (prev_season, wk, prev_season, league_id, wk, left_team_id, right_team_id))
            roster_rows = cur.fetchall()

            left_roster = [r for r in roster_rows if r["team_id"] == left_team_id]
            right_roster = [r for r in roster_rows if r["team_id"] == right_team_id]

            def make_player_dict(r):
                if not r:
                    return None
                pid = r["player_id"]
                proj = float(r["proj_pts"] or 0.0)
                return {
                    "id": pid,
                    "player_id": pid,
                    "name": r["player_name"] or r["full_name"],
                    "full_name": r["full_name"] or r["player_name"],
                    "position": r["position"] or "FLEX",
                    "slot": r.get("slot"),
                    "team": r["nfl_team"] or "NFL",
                    "opponent": "vs OPP",
                    "kickoff": "Sun 1:00 PM",
                    "game_status": "Final" if wk < 6 else "Yet to play",
                    "headshot_url": f"/static/players/{pid}.webp",
                    "fpts": 0.0 if wk >= 6 else round(proj * 0.95, 2),
                    "proj_fpts": round(proj, 2),
                    "is_yet_to_play": (wk >= 6),
                    "injury_status": None,
                }

            roster_format = lg["roster_format"] or {}
            slot_order = _get_slot_order(roster_format)

            def assign_slots_to_players(roster: List[dict]):
                starters_pool = [r for r in roster if r["slot"] != "BN"]
                bench_pool = [r for r in roster if r["slot"] == "BN"]

                by_slot = defaultdict(list)
                for r in starters_pool:
                    by_slot[r["slot"]].append(r)

                slot_assigned = []
                for s in slot_order:
                    if by_slot[s]:
                        slot_assigned.append(by_slot[s].pop(0))
                    else:
                        # Find any remaining starter whose position matches or any starter
                        found = None
                        for rem_slot, rem_list in by_slot.items():
                            if rem_list:
                                found = rem_list.pop(0)
                                break
                        slot_assigned.append(found)

                # Any leftovers in by_slot go to bench
                for rem_list in by_slot.values():
                    bench_pool.extend(rem_list)

                return slot_assigned, bench_pool

            left_assigned, left_bench = assign_slots_to_players(left_roster)
            right_assigned, right_bench = assign_slots_to_players(right_roster)

            starters = []
            for i, slot_name in enumerate(slot_order):
                lp = left_assigned[i] if i < len(left_assigned) else None
                rp = right_assigned[i] if i < len(right_assigned) else None
                starters.append({
                    "slot": slot_name,
                    "slot_label": slot_name,
                    "left_player": make_player_dict(lp),
                    "right_player": make_player_dict(rp),
                })

            bench = []
            max_bn = max(len(left_bench), len(right_bench), 6)
            for i in range(max_bn):
                lp = left_bench[i] if i < len(left_bench) else None
                rp = right_bench[i] if i < len(right_bench) else None
                bench.append({
                    "slot": "BN",
                    "slot_label": "BN",
                    "left_player": make_player_dict(lp),
                    "right_player": make_player_dict(rp),
                })

            # Calculate total projected and live score from starters
            left_proj_sum = sum((s["left_player"]["proj_fpts"] for s in starters if s["left_player"]), 0.0)
            right_proj_sum = sum((s["right_player"]["proj_fpts"] for s in starters if s["right_player"]), 0.0)

            h_proj = float(match["home_proj"] or left_proj_sum or 130.0)
            a_proj = float(match["away_proj"] or right_proj_sum or 120.0)

            win_prob_home = float(match["win_prob_home"] or 0.50)
            win_prob_left = round(win_prob_home * 100)
            win_prob_right = 100 - win_prob_left

            return {
                "league_id": league_id,
                "week": wk,
                "left_team": {
                    **left_team_meta,
                    "score": float(match["home_score"] or 0.0),
                    "proj": round(h_proj, 2),
                    "win_prob": win_prob_left,
                    "yet_to_play_summary": f"Yet to play ({len(slot_order)}) starters",
                },
                "right_team": {
                    **right_team_meta,
                    "score": float(match["away_score"] or 0.0),
                    "proj": round(a_proj, 2),
                    "win_prob": win_prob_right,
                    "yet_to_play_summary": f"Yet to play ({len(slot_order)}) starters",
                },
                "starters": starters,
                "bench": bench,
            }


@router.post("/league/{league_id}/lineup")
def update_lineup(league_id: int, req: LineupRequest):
    """Atomically validates and updates starter and bench slots."""
    if not req.swaps:
        raise HTTPException(status_code=400, detail="No swaps provided")

    with get_db() as conn:
        with conn.cursor() as cur:
            # 1. Validate league
            cur.execute("SELECT current_week, roster_format FROM napper_leagues WHERE id = %s", (league_id,))
            lg = cur.fetchone()
            if not lg:
                raise HTTPException(status_code=404, detail="League not found")
            wk = req.week or lg["current_week"] or 6

            # 2. Validate team belongs to league
            cur.execute("SELECT id FROM napper_teams WHERE id = %s AND league_id = %s", (req.team_id, league_id))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Team not found in this league")

            prev_season = get_previous_season(cur)

            # 3. Fetch current team roster for this week
            cur.execute("""
                SELECT nr.player_id, nr.slot, COALESCE(ps.position, 'FLEX') as position
                FROM napper_rosters nr
                LEFT JOIN player_seasons ps ON ps.player_id = nr.player_id AND ps.season = %s
                WHERE nr.league_id = %s AND nr.team_id = %s AND nr.week = %s
            """, (prev_season, league_id, req.team_id, wk))
            current_roster = cur.fetchall()
            if not current_roster:
                raise HTTPException(status_code=400, detail=f"No roster found for team {req.team_id} in week {wk}")

            roster_by_pid = {r["player_id"]: r for r in current_roster}

            # 4. Validate each swap
            valid_slots = set(ELIGIBLE_POSITIONS.keys())
            simulated_slots = {pid: r["slot"] for pid, r in roster_by_pid.items()}

            for swap in req.swaps:
                if swap.player_id not in roster_by_pid:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Player {swap.player_id} is not on team {req.team_id}'s roster"
                    )

                new_slot = swap.new_slot.upper()
                if new_slot not in valid_slots:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Invalid slot '{swap.new_slot}'. Allowed: {sorted(valid_slots)}"
                    )

                # Validate positional eligibility (except bench which accepts any position)
                player_pos = roster_by_pid[swap.player_id]["position"]
                allowed_positions = ELIGIBLE_POSITIONS.get(new_slot, set())
                if new_slot != "BN" and player_pos not in allowed_positions:
                    # Allow flex/superflex or standard position checks
                    raise HTTPException(
                        status_code=400,
                        detail=f"Player position {player_pos} is not eligible for slot {new_slot}"
                    )

                simulated_slots[swap.player_id] = new_slot

            # 5. Validate roster slot limits
            roster_format = lg["roster_format"] or {}
            slot_counts = defaultdict(int)
            for s in simulated_slots.values():
                if s != "BN":
                    slot_counts[s] += 1

            for s, count in slot_counts.items():
                max_allowed = int(roster_format.get(s, 1))
                if count > max_allowed:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Slot limit exceeded for {s}: {count} assigned, maximum allowed is {max_allowed}"
                    )

            # 6. Atomically apply all swaps
            for swap in req.swaps:
                cur.execute("""
                    UPDATE napper_rosters
                    SET slot = %s
                    WHERE league_id = %s AND team_id = %s AND week = %s AND player_id = %s
                """, (swap.new_slot.upper(), league_id, req.team_id, wk, swap.player_id))

            # 7. Recalculate team projected score for the matchup
            cur.execute("""
                SELECT COALESCE(SUM(fp.projected_points), 0.0) as new_proj
                FROM napper_rosters nr
                JOIN fantasy_projections fp ON fp.player_id = nr.player_id AND fp.week = %s AND fp.season = %s
                WHERE nr.league_id = %s AND nr.team_id = %s AND nr.week = %s AND nr.slot != 'BN'
            """, (wk, prev_season, league_id, req.team_id, wk))
            new_proj_val = float(cur.fetchone()["new_proj"] or 0.0)

            # Update matchup projections if available
            cur.execute("""
                SELECT id, home_team_id, away_team_id, home_proj, away_proj
                FROM napper_matchups
                WHERE league_id = %s AND week = %s AND (home_team_id = %s OR away_team_id = %s)
            """, (league_id, wk, req.team_id, req.team_id))
            match_row = cur.fetchone()
            if match_row and new_proj_val > 0:
                is_home = (match_row["home_team_id"] == req.team_id)
                h_p = new_proj_val if is_home else float(match_row["home_proj"] or 120.0)
                a_p = float(match_row["away_proj"] or 120.0) if is_home else new_proj_val
                new_win_prob = round(1.0 / (1.0 + math.exp(-0.05 * (h_p - a_p))), 3)

                cur.execute("""
                    UPDATE napper_matchups
                    SET home_proj = %s, away_proj = %s, win_prob_home = %s
                    WHERE id = %s
                """, (h_p, a_p, new_win_prob, match_row["id"]))

        conn.commit()

    return {
        "status": "ok",
        "updated": len(req.swaps),
        "team_id": req.team_id,
        "week": wk,
    }


@router.get("/league/{league_id}/waivers")
def get_waivers(league_id: int, position: Optional[str] = None, q: Optional[str] = None):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_week FROM napper_leagues WHERE id = %s", (league_id,))
            lg = cur.fetchone()
            wk = lg["current_week"] if lg else 6

            prev_season = get_previous_season(cur)

            # Query all players not currently rostered in this league for this week
            sql = """
                SELECT p.id as player_id, p.player_name, p.full_name, ps.position,
                       COALESCE(fp.projected_points, 10.0) as proj_pts,
                       COALESCE(fp.avg_points, 11.2) as avg_pts,
                       fp.ros_rank,
                       COALESCE(fp.matchup_stars, 3) as matchup_stars,
                       COALESCE(t.abbreviation, 'NFL') as team_abbr
                FROM players p
                JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                LEFT JOIN teams t ON t.id = ps.team_id
                LEFT JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.week = %s AND fp.season = %s
                WHERE p.id NOT IN (
                    SELECT player_id FROM napper_rosters WHERE league_id = %s AND week = %s
                )
            """
            params = [prev_season, wk, prev_season, league_id, wk]
            if position and position != "ALL":
                sql += " AND ps.position = %s"
                params.append(position)
            if q:
                sql += " AND (p.full_name ILIKE %s OR p.player_name ILIKE %s)"
                params.extend([f"%{q}%", f"%{q}%"])

            sql += " ORDER BY proj_pts DESC LIMIT 60"
            cur.execute(sql, params)
            rows = cur.fetchall()

            players = []
            for r in rows:
                players.append({
                    "player_id": r["player_id"],
                    "name": r["player_name"] or r["full_name"],
                    "position": r["position"] or "FLEX",
                    "team": r["team_abbr"] or "NFL",
                    "opponent": "vs OPP",
                    "matchup_stars": r["matchup_stars"],
                    "proj_fpts": float(r["proj_pts"]),
                    "avg_fpts": float(r["avg_pts"]),
                    "ros_rank": r["ros_rank"] or "FLEX24",
                    "rostered_pct": 32.5,
                    "headshot_url": f"/static/players/{r['player_id']}.webp",
                })

            return {"players": players}


@router.post("/league/{league_id}/waivers/claim")
def claim_waiver_player(league_id: int, req: WaiverClaimRequest):
    """Submits an add/drop transaction and announces in napper_chat."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_week FROM napper_leagues WHERE id = %s", (league_id,))
            lg = cur.fetchone()
            if not lg:
                raise HTTPException(status_code=404, detail="League not found")
            wk = req.week or lg["current_week"] or 6

            # Validate team
            if not req.team_id:
                cur.execute("SELECT id, team_name, manager_handle FROM napper_teams WHERE league_id = %s AND user_id IS NOT NULL LIMIT 1", (league_id,))
                tm = cur.fetchone()
            else:
                cur.execute("SELECT id, team_name, manager_handle FROM napper_teams WHERE id = %s AND league_id = %s", (req.team_id, league_id))
                tm = cur.fetchone()
                if not tm:
                    cur.execute("SELECT id, team_name, manager_handle FROM napper_teams WHERE league_id = %s AND user_id IS NOT NULL LIMIT 1", (league_id,))
                    tm = cur.fetchone()
            if not tm:
                raise HTTPException(status_code=404, detail="Team not found in this league")
            actual_team_id = tm["id"]

            # Validate add player
            cur.execute("SELECT id, player_name, full_name FROM players WHERE id = %s", (req.add_player_id,))
            p_add = cur.fetchone()
            if not p_add:
                raise HTTPException(status_code=404, detail="Player to add not found")

            # Check if add_player_id is already rostered in this league for this week
            cur.execute("""
                SELECT team_id FROM napper_rosters
                WHERE league_id = %s AND week = %s AND player_id = %s
            """, (league_id, wk, req.add_player_id))
            if cur.fetchone():
                raise HTTPException(status_code=400, detail="Player is already rostered by a team in this league")

            p_drop = None
            if req.drop_player_id:
                # Validate drop player is on team's roster
                cur.execute("""
                    SELECT id FROM napper_rosters
                    WHERE league_id = %s AND team_id = %s AND week = %s AND player_id = %s
                """, (league_id, actual_team_id, wk, req.drop_player_id))
                if not cur.fetchone():
                    raise HTTPException(status_code=400, detail="Player to drop is not on team roster")

                cur.execute("SELECT id, player_name, full_name FROM players WHERE id = %s", (req.drop_player_id,))
                p_drop = cur.fetchone()

                # Drop player for this and subsequent weeks
                cur.execute("""
                    DELETE FROM napper_rosters
                    WHERE league_id = %s AND team_id = %s AND week >= %s AND player_id = %s
                """, (league_id, actual_team_id, wk, req.drop_player_id))

            # Add player to bench for this and subsequent weeks
            cur.execute("SELECT DISTINCT week FROM napper_rosters WHERE league_id = %s AND week >= %s", (league_id, wk))
            weeks_to_add = [r["week"] for r in cur.fetchall()]
            if not weeks_to_add:
                weeks_to_add = [wk]

            for w in weeks_to_add:
                cur.execute("""
                    INSERT INTO napper_rosters (league_id, team_id, week, player_id, slot)
                    VALUES (%s, %s, %s, %s, 'BN')
                    ON CONFLICT (team_id, week, player_id) DO UPDATE SET slot = 'BN'
                """, (league_id, actual_team_id, w, req.add_player_id))

            # Construct announcement message
            team_title = tm["team_name"]
            add_name = p_add["player_name"] or p_add["full_name"]
            if p_drop:
                drop_name = p_drop["player_name"] or p_drop["full_name"]
                chat_msg = f"⚡ Waivers: {team_title} claimed {add_name} from free agency and dropped {drop_name}."
            else:
                chat_msg = f"⚡ Waivers: {team_title} claimed {add_name} from free agency."

            card_payload = {
                "type": "WAIVER_CLAIM",
                "team": team_title,
                "added_player": add_name,
                "dropped_player": (p_drop["player_name"] or p_drop["full_name"]) if p_drop else None,
            }

            cur.execute("""
                INSERT INTO napper_chat (league_id, sender_type, sender_name, sender_handle, avatar_url, message_text, card_payload)
                VALUES (%s, 'SYSTEM_BOT', 'Waiver Bot', '@NapperWaivers', '/static/players/robot_avatar.webp', %s, %s)
            """, (league_id, chat_msg, json.dumps(card_payload)))

        conn.commit()

    return {
        "status": "success",
        "added_player_id": req.add_player_id,
        "dropped_player_id": req.drop_player_id,
        "message": chat_msg,
    }


@router.get("/league/{league_id}/trades")
def get_trades(league_id: int):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT t.id, t.league_id, t.week, t.proposer_team_id, t.target_team_id,
                       t.offered_player_ids, t.requested_player_ids, t.status, t.initiated_by_ai,
                       t.ai_rationale, t.created_at,
                       tp.team_name as proposer_name, tp.manager_handle as proposer_handle,
                       tt.team_name as target_name, tt.manager_handle as target_handle
                FROM napper_trades t
                JOIN napper_teams tp ON tp.id = t.proposer_team_id
                JOIN napper_teams tt ON tt.id = t.target_team_id
                WHERE t.league_id = %s
                ORDER BY t.created_at DESC
            """, (league_id,))
            rows = cur.fetchall()

            # Batch load players
            all_pids = set()
            for r in rows:
                all_pids.update(r["offered_player_ids"] or [])
                all_pids.update(r["requested_player_ids"] or [])

            players_map = {}
            if all_pids:
                prev_season = get_previous_season(cur)
                cur.execute("""
                    SELECT p.id, p.player_name, p.full_name, COALESCE(ps.position, 'FLEX') as position,
                           COALESCE(fp.projected_points, 12.0) as proj
                    FROM players p
                    LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                    LEFT JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.week = 6 AND fp.season = %s
                    WHERE p.id = ANY(%s)
                """, (prev_season, prev_season, list(all_pids)))
                for p in cur.fetchall():
                    players_map[p["id"]] = {
                        "id": p["id"],
                        "name": p["player_name"] or p["full_name"],
                        "position": p["position"],
                        "headshot_url": f"/static/players/{p['id']}.webp",
                        "proj_fpts": float(p["proj"]),
                    }

            trades = []
            for r in rows:
                trades.append({
                    "id": r["id"],
                    "week": r["week"],
                    "proposer": {
                        "id": r["proposer_team_id"],
                        "team_name": r["proposer_name"],
                        "manager_handle": r["proposer_handle"],
                    },
                    "target": {
                        "id": r["target_team_id"],
                        "team_name": r["target_name"],
                        "manager_handle": r["target_handle"],
                    },
                    "offered_players": [players_map.get(pid, {"id": pid, "name": f"Player {pid}"}) for pid in r["offered_player_ids"]],
                    "requested_players": [players_map.get(pid, {"id": pid, "name": f"Player {pid}"}) for pid in r["requested_player_ids"]],
                    "status": r["status"],
                    "initiated_by_ai": r["initiated_by_ai"],
                    "ai_rationale": r["ai_rationale"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                })

            return {"trades": trades}


@router.post("/league/{league_id}/trades/propose")
def propose_trade(league_id: int, req: TradeProposeRequest):
    """Proposes a new trade between two teams."""
    if not req.offered_player_ids or not req.requested_player_ids:
        raise HTTPException(status_code=400, detail="Must offer and request at least one player")

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_week FROM napper_leagues WHERE id = %s", (league_id,))
            lg = cur.fetchone()
            wk = lg["current_week"] if lg else 6

            # Verify proposer and target teams belong to this league
            if not req.proposer_team_id:
                cur.execute("SELECT id, team_name FROM napper_teams WHERE league_id = %s AND user_id IS NOT NULL LIMIT 1", (league_id,))
                prop_tm = cur.fetchone()
            else:
                cur.execute("SELECT id, team_name FROM napper_teams WHERE id = %s AND league_id = %s", (req.proposer_team_id, league_id))
                prop_tm = cur.fetchone()
                if not prop_tm:
                    cur.execute("SELECT id, team_name FROM napper_teams WHERE league_id = %s AND user_id IS NOT NULL LIMIT 1", (league_id,))
                    prop_tm = cur.fetchone()
            cur.execute("SELECT id, team_name FROM napper_teams WHERE id = %s AND league_id = %s", (req.target_team_id, league_id))
            target_tm = cur.fetchone()
            if not prop_tm or not target_tm:
                raise HTTPException(status_code=404, detail="Proposer or target team not found in league")
            actual_proposer_id = prop_tm["id"]

            # Verify offered players are on proposer roster
            cur.execute("""
                SELECT player_id FROM napper_rosters
                WHERE league_id = %s AND team_id = %s AND week = %s AND player_id = ANY(%s)
            """, (league_id, actual_proposer_id, wk, req.offered_player_ids))
            found_offered = {r["player_id"] for r in cur.fetchall()}
            if len(found_offered) != len(req.offered_player_ids):
                raise HTTPException(status_code=400, detail="One or more offered players are not on proposer's roster")

            # Verify requested players are on target roster
            cur.execute("""
                SELECT player_id FROM napper_rosters
                WHERE league_id = %s AND team_id = %s AND week = %s AND player_id = ANY(%s)
            """, (league_id, req.target_team_id, wk, req.requested_player_ids))
            found_requested = {r["player_id"] for r in cur.fetchall()}
            if len(found_requested) != len(req.requested_player_ids):
                raise HTTPException(status_code=400, detail="One or more requested players are not on target's roster")

            cur.execute("""
                INSERT INTO napper_trades (
                    league_id, week, proposer_team_id, target_team_id,
                    offered_player_ids, requested_player_ids, status,
                    initiated_by_ai, ai_rationale
                ) VALUES (%s, %s, %s, %s, %s, %s, 'PENDING', FALSE, %s)
                RETURNING id
            """, (league_id, wk, actual_proposer_id, req.target_team_id, req.offered_player_ids, req.requested_player_ids, req.ai_rationale))
            tid = cur.fetchone()["id"]

        conn.commit()

    return {"status": "ok", "trade_id": tid}


@router.post("/league/{league_id}/trades/{trade_id}/respond")
def respond_trade(league_id: int, trade_id: int, req: TradeRespondRequest):
    """Accepts or rejects a trade proposal, swapping players and posting chat announcement."""
    action = req.action.upper()
    if action not in ("ACCEPT", "REJECT"):
        raise HTTPException(status_code=400, detail="Action must be ACCEPT or REJECT")

    status = "ACCEPTED" if action == "ACCEPT" else "REJECTED"
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, league_id, week, proposer_team_id, target_team_id,
                       offered_player_ids, requested_player_ids, status
                FROM napper_trades
                WHERE id = %s AND league_id = %s
            """, (trade_id, league_id))
            tr = cur.fetchone()
            if not tr:
                raise HTTPException(status_code=404, detail="Trade not found")

            if tr["status"] != "PENDING":
                raise HTTPException(status_code=400, detail=f"Trade is already {tr['status']}")

            cur.execute("SELECT team_name FROM napper_teams WHERE id = %s", (tr["proposer_team_id"],))
            p_tm = cur.fetchone()
            proposer_name = p_tm["team_name"] if p_tm else f"Team {tr['proposer_team_id']}"

            cur.execute("SELECT team_name FROM napper_teams WHERE id = %s", (tr["target_team_id"],))
            t_tm = cur.fetchone()
            target_name = t_tm["team_name"] if t_tm else f"Team {tr['target_team_id']}"

            # Update trade status
            cur.execute("""
                UPDATE napper_trades
                SET status = %s
                WHERE id = %s
            """, (status, trade_id))

            if status == "ACCEPTED":
                wk = tr["week"]

                # 1. Swap offered players to target team for this and subsequent weeks
                for pid in tr["offered_player_ids"]:
                    cur.execute("""
                        UPDATE napper_rosters
                        SET team_id = %s, slot = 'BN'
                        WHERE league_id = %s AND team_id = %s AND week >= %s AND player_id = %s
                    """, (tr["target_team_id"], league_id, tr["proposer_team_id"], wk, pid))

                # 2. Swap requested players to proposer team for this and subsequent weeks
                for pid in tr["requested_player_ids"]:
                    cur.execute("""
                        UPDATE napper_rosters
                        SET team_id = %s, slot = 'BN'
                        WHERE league_id = %s AND team_id = %s AND week >= %s AND player_id = %s
                    """, (tr["proposer_team_id"], league_id, tr["target_team_id"], wk, pid))

                # Query player names for chat message
                all_pids = (tr["offered_player_ids"] or []) + (tr["requested_player_ids"] or [])
                cur.execute("SELECT id, player_name FROM players WHERE id = ANY(%s)", (all_pids,))
                names_map = {r["id"]: r["player_name"] for r in cur.fetchall()}

                offered_str = ", ".join([names_map.get(pid, f"Player {pid}") for pid in tr["offered_player_ids"]])
                requested_str = ", ".join([names_map.get(pid, f"Player {pid}") for pid in tr["requested_player_ids"]])

                chat_msg = (
                    f"🤝 Blockbuster Trade Completed! {proposer_name} sent {offered_str} "
                    f"to {target_name} for {requested_str}."
                )

                card_payload = {
                    "type": "TRADE_ALERT",
                    "trade_id": trade_id,
                    "proposer_team": proposer_name,
                    "target_team": target_name,
                    "offered_players": [names_map.get(pid, f"Player {pid}") for pid in tr["offered_player_ids"]],
                    "requested_players": [names_map.get(pid, f"Player {pid}") for pid in tr["requested_player_ids"]],
                }

                cur.execute("""
                    INSERT INTO napper_chat (league_id, sender_type, sender_name, sender_handle, avatar_url, message_text, card_payload)
                    VALUES (%s, 'SYSTEM_BOT', 'Napper Trade Bot', '@NapperTrades', '/static/players/robot_avatar.webp', %s, %s)
                """, (league_id, chat_msg, json.dumps(card_payload)))

            else:
                chat_msg = f"❌ Trade Declined: {target_name} declined trade offer #{trade_id} from {proposer_name}."
                cur.execute("""
                    INSERT INTO napper_chat (league_id, sender_type, sender_name, sender_handle, avatar_url, message_text)
                    VALUES (%s, 'SYSTEM_BOT', 'Napper Trade Bot', '@NapperTrades', '/static/players/robot_avatar.webp', %s)
                """, (league_id, chat_msg))

        conn.commit()

    return {"status": "ok", "trade_status": status, "message": chat_msg}


@router.get("/league/{league_id}/chat")
def get_league_chat(league_id: int, limit: int = 50):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT * FROM (
                    SELECT id, league_id, sender_type, sender_name, sender_handle, avatar_url,
                           message_text, card_payload, created_at
                    FROM napper_chat
                    WHERE league_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                ) sub
                ORDER BY created_at ASC, id ASC
            """, (league_id, limit))
            messages = cur.fetchall()

            results = []
            for m in messages:
                results.append({
                    "id": m["id"],
                    "league_id": m["league_id"],
                    "sender_type": m["sender_type"],
                    "sender_name": m["sender_name"],
                    "sender_handle": m["sender_handle"],
                    "avatar_url": m["avatar_url"],
                    "message_text": m["message_text"],
                    "card_payload": m["card_payload"],
                    "created_at": m["created_at"].isoformat() if m["created_at"] else None,
                })

            return {"messages": results}


@router.post("/league/{league_id}/chat/send")
def send_chat_message(league_id: int, req: ChatMessageRequest):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO napper_chat (league_id, sender_type, sender_name, sender_handle, avatar_url, message_text, card_payload)
                VALUES (%s, %s, %s, %s, '/static/players/robot_avatar.webp', %s, %s)
                RETURNING id, created_at
            """, (
                league_id,
                req.sender_type,
                req.sender_name,
                req.sender_handle,
                req.message_text,
                json.dumps(req.card_payload) if req.card_payload else None
            ))
            row = cur.fetchone()

        conn.commit()

    return {"status": "ok", "id": row["id"], "created_at": row["created_at"].isoformat()}


# ────────────────────────── Draft & Mock Draft Endpoints ──────────────────────────

@router.post("/drafts/create_mock")
def create_mock_draft(req: MockDraftCreateRequest):
    """Creates a new mock draft session with pre-populated snake pick order."""
    try:
        return napper_draft_engine.create_draft_session(
            league_id=None,
            teams_count=req.teams_count,
            scoring_type=req.scoring_type,
            user_slot=req.user_slot,
            rounds=req.rounds,
            is_mock=True,
            archetype_composition=req.archetype_composition,
            archetypes=req.archetypes,
            randomize_user_slot=req.randomize_user_slot,
            settings={"is_superflex": req.is_superflex}
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error creating mock draft: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/draft/{draft_id}/randomize_order")
def randomize_draft_order(draft_id: int, req: Optional[RandomizeOrderRequest] = None):
    """Randomizes the draft slot order and optionally user slot in a pending draft."""
    try:
        keep_user = req.keep_user_slot if req else False
        return napper_draft_engine.randomize_draft_order(draft_id, keep_user_slot=keep_user)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error randomizing draft order {draft_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/draft/{draft_id}/set_archetypes")
def set_draft_archetypes(draft_id: int, req: SetArchetypesRequest):
    """Updates the AI archetype composition for a pending draft."""
    try:
        return napper_draft_engine.set_draft_archetypes(
            draft_id,
            archetype_composition=req.archetype_composition,
            archetypes=req.archetypes
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error updating archetypes in draft {draft_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/draft/{draft_id}")
def get_draft(draft_id: int):
    """Returns real-time draft state, board matrix, and on-the-clock timer."""
    try:
        return napper_draft_engine.get_draft_state(draft_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error fetching draft {draft_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/draft/{draft_id}/claim_slot")
def claim_draft_slot(draft_id: int, req: ClaimSlotRequest):
    """Changes the user's draft slot in a pending draft."""
    try:
        return napper_draft_engine.claim_slot(draft_id, req.slot)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error claiming slot in draft {draft_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/draft/{draft_id}/start")
def start_draft(draft_id: int):
    """Transitions a pending draft into progress."""
    try:
        return napper_draft_engine.start_draft(draft_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error starting draft {draft_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/draft/{draft_id}/pick")
def make_draft_pick(draft_id: int, req: DraftPickRequest):
    """Submits a user selection for the current on-the-clock pick."""
    try:
        return napper_draft_engine.make_draft_pick(draft_id, req.player_id, picked_by="USER")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error making pick in draft {draft_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/draft/{draft_id}/sim_step")
def simulate_draft_step(draft_id: int):
    """Simulates a single AI draft turn based on ADP, team need, and manager persona."""
    try:
        return napper_draft_engine.simulate_ai_pick(draft_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error simulating step in draft {draft_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/draft/{draft_id}/sim_to_user")
def simulate_draft_to_user(draft_id: int):
    """Simulates all AI picks until the user is back on the clock or draft completes."""
    try:
        return napper_draft_engine.simulate_until_user(draft_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error simulating to user in draft {draft_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/draft/{draft_id}/sim_all")
def simulate_entire_draft(draft_id: int):
    """Fast-forwards all remaining draft picks to completion."""
    try:
        return napper_draft_engine.simulate_entire_draft(draft_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error simulating entire draft {draft_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/draft/{draft_id}/finalize")
def finalize_draft(draft_id: int):
    """Completes draft, generates report cards and recap, and assigns rosters/matchups."""
    try:
        return napper_draft_engine.finalize_draft(draft_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error finalizing draft {draft_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/draft/{draft_id}/available_players")
def get_available_players(
    draft_id: int,
    search: Optional[str] = Query(None),
    position: Optional[str] = Query("ALL"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0)
):
    """Returns available players ordered by ADP, with bye, age, ros_pct, projections, rush/rec stats, and draft count."""
    with get_db() as conn:
        with conn.cursor() as cur:
            prev_season = get_previous_season(cur)
            where_clauses = []
            params: Dict[str, Any] = {"limit": limit, "offset": offset, "prev_season": prev_season}

            if draft_id > 0:
                cur.execute("SELECT id FROM napper_drafts WHERE id = %s", (draft_id,))
                if not cur.fetchone():
                    raise HTTPException(status_code=404, detail=f"Draft {draft_id} not found")
                where_clauses.append("""p.id NOT IN (
                    SELECT player_id FROM napper_draft_picks
                    WHERE draft_id = %(draft_id)s AND player_id IS NOT NULL
                )""")
                params["draft_id"] = draft_id

            if position and position.upper() != "ALL":
                pos_upper = position.upper()
                if pos_upper in ("RB", "HB"):
                    where_clauses.append("ps.position IN ('RB', 'HB', 'FB')")
                else:
                    where_clauses.append("ps.position = %(position)s")
                    params["position"] = pos_upper
            else:
                where_clauses.append("ps.position IN ('QB', 'HB', 'FB', 'RB', 'WR', 'TE', 'K', 'DEF')")

            if search:
                where_clauses.append("(p.full_name ILIKE %(search)s OR p.player_name ILIKE %(search)s)")
                params["search"] = f"%{search.strip()}%"

            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

            cur.execute(f"""
                SELECT COUNT(DISTINCT p.id) as total
                FROM players p
                JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %(prev_season)s
                WHERE {where_sql}
            """, params)
            total = cur.fetchone()["total"]

            cur.execute(f"""
                SELECT p.id, p.player_name, p.full_name, ps.position, ps.age, ps.team_id,
                       COALESCE(t.abbreviation, 'NFL') as team_abbr,
                       COALESCE(fp.adp_rank, 999) as adp,
                       ss.total_fantasy_points as ss_total_pts,
                       ss.ppg_fantasy_points as ss_ppg_pts,
                       fp.ros_total_points,
                       fp.avg_points as fp_avg_pts,
                       NULLIF(fp.projected_points, 0) as fp_proj_pts,
                       COALESCE(ss.rush_attempts, 0) as rush_att,
                       COALESCE(ss.rush_yards, 0) as rush_yds,
                       COALESCE(ss.rush_tds, 0) as rush_tds,
                       COALESCE(ss.receptions, 0) as rec,
                       COALESCE(ss.rec_yards, 0) as rec_yds,
                       COALESCE(ss.rec_tds, 0) as rec_tds,
                       (SELECT COUNT(*) FROM napper_draft_picks ndp WHERE ndp.player_id = p.id) as draft_count
                FROM players p
                JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %(prev_season)s
                LEFT JOIN teams t ON t.id = ps.team_id
                LEFT JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.season = %(prev_season)s AND fp.week = 1
                LEFT JOIN season_stats ss ON ss.player_id = p.id AND ss.season = %(prev_season)s
                WHERE {where_sql}
                ORDER BY COALESCE(fp.adp_rank, 999) ASC, ss.total_fantasy_points DESC NULLS LAST, p.id ASC
                LIMIT %(limit)s OFFSET %(offset)s
            """, params)
            rows = cur.fetchall()

            players = []
            for r in rows:
                adp_val = int(r["adp"])
                team_id = r["team_id"] or 1
                bye_wk = (team_id % 10) + 5
                ros_pct = round(max(5.0, min(100.0, 100.0 - (float(adp_val) * 0.45))), 1) if adp_val <= 200 else 5.0

                ss_total = r["ss_total_pts"]
                ss_ppg = r["ss_ppg_pts"]
                ros_total = r["ros_total_points"]
                fp_avg = r["fp_avg_pts"]
                fp_proj = r["fp_proj_pts"]

                # PTS must be season total points, and AVG must be ppg
                if ss_total is not None and float(ss_total) > 10.0:
                    tot_pts = round(float(ss_total), 1)
                    avg_ppg = round(float(ss_ppg) if ss_ppg and float(ss_ppg) > 0 else (tot_pts / 17.0), 1)
                elif ros_total is not None and float(ros_total) > 10.0:
                    tot_pts = round(float(ros_total), 1)
                    avg_ppg = round(tot_pts / 17.0, 1)
                elif fp_avg is not None and float(fp_avg) > 0:
                    avg_ppg = round(float(fp_avg), 1)
                    tot_pts = round(avg_ppg * 17.0, 1)
                elif fp_proj is not None and float(fp_proj) > 0:
                    avg_ppg = round(float(fp_proj), 1)
                    tot_pts = round(avg_ppg * 17.0, 1)
                else:
                    # Consensus projection scaled by ADP
                    if adp_val <= 12:
                        avg_ppg = round(18.0 - (adp_val * 0.3), 1)
                    elif adp_val <= 36:
                        avg_ppg = round(14.5 - ((adp_val - 12) * 0.15), 1)
                    elif adp_val <= 75:
                        avg_ppg = round(11.0 - ((adp_val - 36) * 0.08), 1)
                    elif adp_val <= 150:
                        avg_ppg = round(8.0 - ((adp_val - 75) * 0.03), 1)
                    else:
                        avg_ppg = round(max(3.5, 6.0 - ((adp_val - 150) * 0.01)), 1)
                    tot_pts = round(avg_ppg * 17.0, 1)

                players.append({
                    "id": r["id"],
                    "name": r["full_name"] or r["player_name"],
                    "player_name": r["player_name"],
                    "position": r["position"],
                    "team": r["team_abbr"],
                    "headshot_url": f"/static/players/{r['id']}.webp",
                    "adp": adp_val,
                    "bye": bye_wk,
                    "age": r["age"] or 25,
                    "ros_pct": ros_pct,
                    "projections": {
                        "pts": tot_pts,
                        "avg": avg_ppg,
                    },
                    "rush": {
                        "rush": int(r["rush_att"]),
                        "yds": int(r["rush_yds"]),
                        "td": int(r["rush_tds"]),
                    },
                    "rec": {
                        "rec": int(r["rec"]),
                        "yds": int(r["rec_yds"]),
                        "td": int(r["rec_tds"]),
                    },
                    "draft_count": int(r["draft_count"]),
                })

            return {"players": players, "total": total}


@router.get("/league/{league_id}/draft")
def get_league_draft(league_id: int, rounds: int = Query(15, ge=1, le=25)):
    """Retrieves or creates a draft session for a league in PRE_DRAFT status."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, status FROM napper_leagues WHERE id = %s", (league_id,))
            league = cur.fetchone()
            if not league:
                raise HTTPException(status_code=404, detail="League not found")

            cur.execute("SELECT id FROM napper_drafts WHERE league_id = %s ORDER BY id DESC LIMIT 1", (league_id,))
            row = cur.fetchone()

    if row:
        return napper_draft_engine.get_draft_state(row["id"])
    return napper_draft_engine.create_draft_session(league_id=league_id, rounds=rounds)
