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
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
import psycopg
from psycopg.rows import dict_row
from engines import napper_draft_engine
from engines.napper_trade_engine import fill_empty_starter_slots
from engines.napper_injuries import IR_MIN_WEEKS, get_injury_map, is_ir_eligible
from engines.fantasy_projection_engine import DEF_POINTS_SQL, DEF_STATS_JOIN_SQL, KICKING_POINTS_SQL

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/napper", tags=["napper"])
DATABASE_URL = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")


def get_db():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def get_current_season(cur) -> int:
    cur.execute("SELECT current_season FROM league_state WHERE id = 1")
    row = cur.fetchone()
    if not row or not row["current_season"]:
        raise RuntimeError("The active NFL season is not configured in league_state")
    return int(row["current_season"])


def get_current_season_week(cur) -> tuple[int, int]:
    """Return the active simulation season and week used by analytics and games."""
    cur.execute("SELECT current_season, current_week FROM league_state WHERE id = 1")
    row = cur.fetchone()
    if not row or not row["current_season"]:
        raise RuntimeError("The active NFL season is not configured in league_state")
    return int(row["current_season"]), max(1, int(row["current_week"] or 1))


def require_current_season_league(cur, league_id: int) -> Dict[str, Any]:
    cur.execute("SELECT id, season FROM napper_leagues WHERE id = %s", (league_id,))
    league = cur.fetchone()
    if not league:
        raise HTTPException(status_code=404, detail="League not found")
    season = get_current_season(cur)
    if int(league["season"]) != season:
        raise HTTPException(status_code=409, detail="This league belongs to an old NFL season and must be restarted")
    return league


def _reception_adjustment(scoring_type: Optional[str]) -> float:
    if scoring_type == "HALF_PPR":
        return 0.5
    if scoring_type == "STANDARD":
        return 1.0
    return 0.0


_ACTUAL_PLAYER_POINTS_CTE = f"""
    WITH raw_actual AS (
        SELECT ps.player_id, SUM(ps.fantasy_points) AS points, SUM(ps.receptions) AS receptions
        FROM player_stats ps JOIN games g ON g.id = ps.game_id
        WHERE g.season = %s AND g.week = %s AND g.status = 'COMPLETED' AND ps.player_id IS NOT NULL
          AND ps.position NOT IN ('K', 'DEF')
        GROUP BY ps.player_id
        UNION ALL
        SELECT k.player_id, SUM({KICKING_POINTS_SQL}), 0
        FROM game_stat_kicking k JOIN games g ON g.id = k.game_id
        WHERE g.season = %s AND g.week = %s AND g.status = 'COMPLETED'
        GROUP BY k.player_id
        UNION ALL
        SELECT dst.player_id, SUM({DEF_POINTS_SQL}), 0
        FROM player_seasons dst
        JOIN games g ON g.season = dst.season AND g.week = %s AND g.status = 'COMPLETED'
            AND (g.home_team_id = dst.team_id OR g.away_team_id = dst.team_id)
        {DEF_STATS_JOIN_SQL}
        WHERE dst.season = %s AND dst.position = 'DEF'
        GROUP BY dst.player_id
        UNION ALL
        SELECT NULL::INT, 0::NUMERIC, 0::NUMERIC WHERE FALSE
    ), actual AS (
        SELECT player_id, SUM(points) AS fantasy_points, SUM(receptions) AS receptions
        FROM raw_actual GROUP BY player_id
    )
"""


def _get_weekly_team_totals(cur, league_id: int, season: int, week: int, scoring_type: str):
    """Calculate starter scores and projections from the actual player-week rows."""
    cur.execute(f"""
        {_ACTUAL_PLAYER_POINTS_CTE}
        SELECT nr.team_id,
               COALESCE(SUM(fp.projected_points), 0) AS projection,
               COALESCE(SUM(COALESCE(actual.fantasy_points, 0)
                   - COALESCE(actual.receptions, 0) * %s), 0) AS score,
               COUNT(*) AS starters,
               COUNT(actual.player_id) AS played_players
        FROM napper_rosters nr
        LEFT JOIN fantasy_projections fp
          ON fp.player_id = nr.player_id AND fp.season = %s AND fp.week = %s
        LEFT JOIN actual ON actual.player_id = nr.player_id
        WHERE nr.league_id = %s AND nr.week = %s AND nr.slot NOT IN ('BN', 'IR')
        GROUP BY nr.team_id
    """, (season, week, season, week, week, season,
          _reception_adjustment(scoring_type), season, week, league_id, week))
    return {row["team_id"]: row for row in cur.fetchall()}


def _is_nfl_week_complete(cur, season: int, week: int) -> bool:
    cur.execute("""
        SELECT COUNT(*) AS total,
               COUNT(*) FILTER (WHERE status = 'COMPLETED') AS completed
        FROM games
        WHERE season = %s AND week = %s AND game_type = 'REGULAR_SEASON'
    """, (season, week))
    row = cur.fetchone()
    return bool(row and row["total"] > 0 and row["completed"] == row["total"])


def _get_league_standings(cur, league_id: int, season: int, scoring_type: str):
    """Build season standings from weekly starter scores and scheduled opponents."""
    _, active_week = get_current_season_week(cur)
    cur.execute("""
        SELECT id, team_name, manager_handle, is_ai, persona,
               (user_id = (SELECT id FROM napper_user_profiles ORDER BY id LIMIT 1)) AS is_user
        FROM napper_teams WHERE league_id = %s
    """, (league_id,))
    teams = cur.fetchall()
    stats = {
        team["id"]: {**team, "wins": 0, "losses": 0, "ties": 0,
                     "points_for": 0.0, "points_against": 0.0}
        for team in teams
    }
    cur.execute("""
        SELECT id, week, home_team_id, away_team_id
        FROM napper_matchups
        WHERE league_id = %s AND week <= %s AND week <= 14
        ORDER BY week, id
    """, (league_id, active_week))
    matchups_by_week = defaultdict(list)
    for matchup in cur.fetchall():
        matchups_by_week[matchup["week"]].append(matchup)

    for week, matchups in matchups_by_week.items():
        weekly_totals = _get_weekly_team_totals(cur, league_id, season, week, scoring_type)
        complete = week < active_week or _is_nfl_week_complete(cur, season, week)
        for matchup in matchups:
            home_id, away_id = matchup["home_team_id"], matchup["away_team_id"]
            home_score = float((weekly_totals.get(home_id) or {}).get("score") or 0)
            away_score = float((weekly_totals.get(away_id) or {}).get("score") or 0)
            if home_id in stats:
                stats[home_id]["points_for"] += home_score
                stats[home_id]["points_against"] += away_score
            if away_id in stats:
                stats[away_id]["points_for"] += away_score
                stats[away_id]["points_against"] += home_score
            has_lineups = int((weekly_totals.get(home_id) or {}).get("starters") or 0) > 0 and int((weekly_totals.get(away_id) or {}).get("starters") or 0) > 0
            if complete and has_lineups and home_id in stats and away_id in stats:
                if home_score > away_score:
                    stats[home_id]["wins"] += 1
                    stats[away_id]["losses"] += 1
                elif away_score > home_score:
                    stats[away_id]["wins"] += 1
                    stats[home_id]["losses"] += 1
                else:
                    stats[home_id]["ties"] += 1
                    stats[away_id]["ties"] += 1

    def rank_key(team):
        games = team["wins"] + team["losses"] + team["ties"]
        record_pct = (team["wins"] + 0.5 * team["ties"]) / games if games else 0.0
        return (-record_pct, -team["points_for"], team["id"])

    ordered = sorted(stats.values(), key=rank_key)
    for seed, team in enumerate(ordered, 1):
        team["seed"] = seed
        team["points_for"] = round(team["points_for"], 2)
        team["points_against"] = round(team["points_against"], 2)
    return ordered


# ──────────────────────────────── Models ────────────────────────────────

class LeagueCreateRequest(BaseModel):
    name: str
    teams_count: int = 8
    scoring_type: str = "PPR"
    status: str = "IN_SEASON"
    is_superflex: bool = True
    avatar_url: Optional[str] = None
    archetype_preset: Optional[str] = None
    archetype_composition: Optional[Dict[str, int]] = None


class LineupSwap(BaseModel):
    player_id: int
    new_slot: str


class LineupRequest(BaseModel):
    team_id: int
    swaps: List[LineupSwap]
    week: Optional[int] = None


class TeamNameRequest(BaseModel):
    team_name: str


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
    archetype_preset: Optional[str] = None


class SetDraftOrderRequest(BaseModel):
    slot_order: Optional[List[int]] = None
    teams: Optional[List[Dict[str, Any]]] = None


class ClaimSlotRequest(BaseModel):
    slot: int


class DraftPickRequest(BaseModel):
    player_id: int


class SaveDraftAsLeagueRequest(BaseModel):
    name: Optional[str] = "My Draft League"
    teams_count: Optional[int] = 8
    scoring_type: str = "PPR"
    is_superflex: bool = True
    user_slot: Optional[int] = 1
    draft_id: Optional[int] = None
    teams: Optional[List[Dict[str, Any]]] = None
    picks: Optional[List[Dict[str, Any]]] = None


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
    # Any position, but only an IR-eligible player (engines/napper_injuries.is_ir_eligible); never starts or scores.
    "IR": {"QB", "RB", "WR", "TE", "FB", "HB", "K", "DEF"},
}


def _get_slot_order(roster_fmt: Optional[Dict[str, int]]) -> List[str]:
    """Generates ordered starting slot names from league roster_format."""
    standard_slots = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
    if not roster_fmt:
        return standard_slots

    order: List[str] = []
    for s in ["QB", "RB", "WR", "TE", "FLEX", "SUPERFLEX", "K", "DEF"]:
        count = int(roster_fmt.get(s, 0))
        for _ in range(count):
            order.append(s)

    return order if order else standard_slots


def _ensure_matchup_and_rosters(cur, league_id: int, week: int):
    """Ensures matchups and rosters exist for the given week (supports weeks 1 to 17)."""
    # 1. Ensure rosters for this week
    cur.execute(
        "SELECT COUNT(*) as cnt FROM napper_rosters WHERE league_id = %s AND week = %s",
        (league_id, week)
    )
    if cur.fetchone()["cnt"] == 0:
        # Check if rosters exist for any week in this league
        _, active_week = get_current_season_week(cur)
        src_row = None
        if week == active_week:
            cur.execute(
                "SELECT week FROM napper_rosters WHERE league_id = %s AND week < %s ORDER BY week DESC LIMIT 1",
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
        # If the league has no roster history, leave it empty. A different league's
        # roster must never be presented as this league's team.

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
                h_proj = 0.0
                a_proj = 0.0
                win_prob = 0.5
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
            season, current_week = get_current_season_week(cur)
            cur.execute("""
                SELECT l.id, l.name, l.teams_count, l.scoring_type, l.status, l.avatar_url,
                       t.id as user_team_id, t.team_name as user_team_name, t.wins, t.losses, t.ties,
                       t.points_for, t.points_against
                FROM napper_leagues l
                JOIN napper_teams t ON t.league_id = l.id
                    AND t.user_id = (SELECT id FROM napper_user_profiles ORDER BY id LIMIT 1)
                WHERE l.is_seed IS NOT TRUE AND l.season = %s
                ORDER BY l.id
            """, (season,))
            leagues = cur.fetchall()

            results = []
            for lg in leagues:
                lid = lg["id"]
                cw = current_week
                u_tid = lg["user_team_id"]
                _ensure_matchup_and_rosters(cur, lid, cw)
                conn.commit()

                wins = lg["wins"] if lg["wins"] is not None else 0
                losses = lg["losses"] if lg["losses"] is not None else 0
                ties = lg["ties"] if lg["ties"] is not None else 0
                record = f"{wins}-{losses}" + (f"-{ties}" if ties else "")

                cur.execute("""
                    SELECT m.home_team_id, m.away_team_id,
                           th.team_name AS home_name, ta.team_name AS away_name
                    FROM napper_matchups m
                    JOIN napper_teams th ON th.id = m.home_team_id
                    JOIN napper_teams ta ON ta.id = m.away_team_id
                    WHERE m.league_id = %s AND m.week = %s
                      AND (m.home_team_id = %s OR m.away_team_id = %s)
                    LIMIT 1
                """, (lid, cw, u_tid, u_tid))
                match = cur.fetchone()
                totals = _get_weekly_team_totals(cur, lid, season, cw, lg["scoring_type"])
                user_total = totals.get(u_tid, {})
                opponent_id = None
                if match:
                    is_home = match["home_team_id"] == u_tid
                    opponent_id = match["away_team_id"] if is_home else match["home_team_id"]
                    opp_name = match["away_name"] if is_home else match["home_name"]
                else:
                    opp_name = None
                opponent_total = totals.get(opponent_id, {})

                results.append({
                    "id": lg["id"],
                    "name": lg["name"],
                    "season": season,
                    "teams_count": lg["teams_count"],
                    "scoring_type": lg["scoring_type"],
                    "status": lg["status"],
                    "avatar_url": lg.get("avatar_url"),
                    "current_week": cw,
                    "user_team_id": u_tid,
                    "user_team_name": lg["user_team_name"],
                    "record": record,
                    "wins": wins,
                    "losses": losses,
                    "ties": ties,
                    "user_score": round(float(user_total.get("score") or 0), 2),
                    "user_proj": round(float(user_total.get("projection") or 0), 2),
                    "opponent_team_name": opp_name,
                    "opponent_score": round(float(opponent_total.get("score") or 0), 2),
                    "opponent_proj": round(float(opponent_total.get("projection") or 0), 2),
                    "unplayed_count": max(0, int(user_total.get("starters") or 0) - int(user_total.get("played_players") or 0)),
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

            season, _ = get_current_season_week(cur)
            require_current_season_league(cur, league_id)
            teams = _get_league_standings(cur, league_id, season, league["scoring_type"])

            return {
                "league": league,
                "teams": teams,
            }


@router.delete("/league/{league_id}")
def delete_league(league_id: int):
    """Deletes a league and its dependent teams, rosters, matchups, drafts, trades, and chat."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM napper_leagues WHERE id = %s RETURNING id",
                (league_id,),
            )
            deleted = cur.fetchone()
            if not deleted:
                raise HTTPException(status_code=404, detail="League not found")
            return {"deleted": True, "league_id": deleted["id"]}


def resolve_archetype_preset_composition(preset_name: Optional[str], ai_slots: int) -> Dict[str, int]:
    """Resolves archetype preset name into specific archetype slot counts for AI managers."""
    preset_lower = (preset_name or "").lower()
    if "trader" in preset_lower or "trade" in preset_lower:
        comp = {
            "TRADE_FIEND": max(1, round(ai_slots * 0.45)),
            "ANALYTICS_NERD": max(1, round(ai_slots * 0.25)),
            "WAIVER_HAWK": 1,
            "HERO_RB": 1,
        }
    elif "sleeper" in preset_lower or "underdog" in preset_lower:
        comp = {
            "UNDERDOG": max(1, round(ai_slots * 0.45)),
            "ZERO_RB_ZEALOT": max(1, round(ai_slots * 0.25)),
            "HERO_RB": 1,
            "ANALYTICS_NERD": 1,
        }
    elif "zero" in preset_lower:
        comp = {
            "ZERO_RB_ZEALOT": max(1, round(ai_slots * 0.55)),
            "ANALYTICS_NERD": 1,
            "UNDERDOG": 1,
            "HERO_RB": 1,
        }
    elif "hero" in preset_lower:
        comp = {
            "HERO_RB": max(1, round(ai_slots * 0.55)),
            "ZERO_RB_ZEALOT": 1,
            "ANALYTICS_NERD": 1,
            "TRADE_FIEND": 1,
        }
    elif "chaos" in preset_lower or "trash" in preset_lower:
        comp = {
            "TRASH_TALKER": max(1, round(ai_slots * 0.45)),
            "DIE_HARD": max(1, round(ai_slots * 0.35)),
            "TRADE_FIEND": 1,
        }
    elif "analytics" in preset_lower:
        comp = {
            "ANALYTICS_NERD": max(1, round(ai_slots * 0.55)),
            "ZERO_RB_ZEALOT": 1,
            "TRADE_FIEND": 1,
            "HERO_RB": 1,
        }
    elif "sim" in preset_lower or "fantasy league" in preset_lower:
        comp = {
            "BPA": max(1, round(ai_slots * 0.35)),
            "HERO_RB": max(1, round(ai_slots * 0.15)),
            "ZERO_RB_ZEALOT": max(1, round(ai_slots * 0.15)),
            "RB_HEAVY": max(1, round(ai_slots * 0.15)),
            "EARLY_QB": max(1, round(ai_slots * 0.10)),
            "EARLY_TE": max(1, round(ai_slots * 0.10)),
        }
    else:
        # Default: Balanced Mix across the 8 archetypes
        canonical = [
            "ANALYTICS_NERD", "ZERO_RB_ZEALOT", "HERO_RB", "TRASH_TALKER",
            "WAIVER_HAWK", "DIE_HARD", "TRADE_FIEND", "UNDERDOG"
        ]
        comp = defaultdict(int)
        for i in range(ai_slots):
            comp[canonical[i % len(canonical)]] += 1
        return dict(comp)

    # Balance counts to exact ai_slots
    cur_total = sum(comp.values())
    if cur_total < ai_slots:
        first_key = next(iter(comp))
        comp[first_key] += (ai_slots - cur_total)
    elif cur_total > ai_slots:
        diff = cur_total - ai_slots
        for k in list(comp.keys()):
            if comp[k] > 0 and diff > 0:
                sub = min(comp[k], diff)
                comp[k] -= sub
                diff -= sub
    return comp


@router.post("/leagues/create")
def create_league(req: LeagueCreateRequest):
    """Creates a new fantasy league.

    Newly created in-season leagues start without fabricated rosters or scores.
    """
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="League name cannot be empty")

    teams_count = req.teams_count if req.teams_count in (8, 10, 12) else 8
    scoring_type = req.scoring_type if req.scoring_type in ("PPR", "HALF_PPR", "STANDARD") else "PPR"
    status = "PRE_DRAFT" if req.status == "PRE_DRAFT" else "IN_SEASON"

    if req.is_superflex:
        roster_format = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 1, "K": 1, "DEF": 1, "BN": 6, "IR": 1}
    else:
        roster_format = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 0, "K": 1, "DEF": 1, "BN": 6, "IR": 1}

    avatar = req.avatar_url or "https://images.unsplash.com/photo-1508098682722-e99c43a406b2?auto=format&fit=crop&w=256&h=256&q=80"
    with get_db() as conn:
        with conn.cursor() as cur:
            season, active_week = get_current_season_week(cur)
            current_week = active_week if status == "IN_SEASON" else 1

            # Seed leagues stay stored but are excluded from the active league list.
            cur.execute("""
                INSERT INTO napper_leagues (
                    name, season, teams_count, scoring_type, roster_format,
                    status, avatar_url, current_week, is_seed
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, FALSE)
                RETURNING id, name, season, teams_count, scoring_type, status, current_week, avatar_url
            """, (name, season, teams_count, scoring_type, psycopg.types.json.Jsonb(roster_format), status, avatar, current_week))
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
                0, 0, 0.0, 0.0
            ))
            user_team_id = cur.fetchone()["id"]

            # 5. Create AI Teams using custom picked strategies or preset
            archetype_composition = req.archetype_composition
            if not archetype_composition:
                archetype_composition = resolve_archetype_preset_composition(req.archetype_preset, teams_count - 1)

            ai_managers = napper_draft_engine.build_ai_managers(
                teams_count=teams_count,
                archetype_composition=archetype_composition,
                randomize_order=True,
            )

            team_ids = [user_team_id]
            for idx, cand in enumerate(ai_managers):
                seed_num = idx + 2
                cur.execute("""
                    INSERT INTO napper_teams (
                        league_id, user_id, team_name, manager_handle, avatar_url,
                        is_ai, persona, wins, losses, ties, points_for, points_against, seed
                    ) VALUES (%s, NULL, %s, %s, %s, TRUE, %s, %s, %s, 0, %s, %s, %s)
                    RETURNING id
                """, (
                    league_id, cand["name"], cand["handle"],
                    cand.get("avatar") or f"https://api.dicebear.com/7.x/identicon/svg?seed={cand['name']}",
                    cand["persona"],
                    0, 0, 0.0, 0.0, seed_num
                ))
                team_ids.append(cur.fetchone()["id"])

            # In-season leagues start with empty rosters; only a completed draft or
            # explicit roster transaction can associate players with these teams.
            if status == "IN_SEASON":
                n = len(team_ids)
                t_list = list(team_ids)
                for w in range(1, 15):
                    round_idx = (w - 1) % (n - 1)
                    rotated = [t_list[0]] + t_list[1:][round_idx:] + t_list[1:][:round_idx]
                    for match_i in range(n // 2):
                        home = rotated[match_i]
                        away = rotated[n - 1 - match_i]
                        cur.execute("""
                            INSERT INTO napper_matchups (
                                league_id, week, home_team_id, away_team_id,
                                home_score, away_score, home_proj, away_proj,
                                win_prob_home, is_final
                            ) VALUES (%s, %s, %s, %s, 0, 0, 0, 0, 0.500, FALSE)
                        """, (league_id, w, home, away))

                welcome_card = {
                    "type": "LEAGUE_CREATED",
                    "title": f"Welcome to {name}!",
                    "highlight": f"{teams_count} Teams · {scoring_type} · Superflex" if req.is_superflex else f"{teams_count} Teams · {scoring_type}",
                    "commentary": f"Week {current_week} is underway. Draft or add players to build your roster.",
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


@router.post("/league/{league_id}/set_strategies")
def set_league_strategies(league_id: int, req: SetArchetypesRequest):
    """Updates the manager strategies/personas for all AI teams in a league."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, name, teams_count, status FROM napper_leagues WHERE id = %s", (league_id,))
            league = cur.fetchone()
            if not league:
                raise HTTPException(status_code=404, detail="League not found")
            if league["status"] != "PRE_DRAFT":
                raise HTTPException(status_code=409, detail="League strategies can only be changed before the draft.")

            # Fetch existing AI teams
            cur.execute("""
                SELECT id, persona FROM napper_teams
                WHERE league_id = %s AND is_ai = TRUE
                ORDER BY seed ASC, id ASC
            """, (league_id,))
            ai_teams = cur.fetchall()
            if not ai_teams:
                raise HTTPException(status_code=400, detail="No AI teams found in league to update")

            ai_slots = len(ai_teams)
            comp = req.archetype_composition
            if not comp and req.archetype_preset:
                comp = resolve_archetype_preset_composition(req.archetype_preset, ai_slots)

            new_managers = napper_draft_engine.build_ai_managers(
                teams_count=ai_slots + 1,
                archetype_composition=comp,
                archetypes=req.archetypes,
                randomize_order=True,
            )

            for idx, tm in enumerate(ai_teams):
                m = new_managers[idx % len(new_managers)]
                cur.execute("""
                    UPDATE napper_teams
                    SET persona = %s, team_name = %s, manager_handle = %s
                    WHERE id = %s
                """, (m["persona"], m["name"], m["handle"], tm["id"]))

            # Announce update in league chat
            summary_parts = []
            if comp:
                for k, v in comp.items():
                    if v > 0:
                        clean_name = k.replace("_", " ").title()
                        summary_parts.append(f"{v} {clean_name}")
            summary_str = ", ".join(summary_parts) if summary_parts else (req.archetype_preset or "Custom Strategy Mix")

            cur.execute("""
                INSERT INTO napper_chat (league_id, sender_type, sender_name, sender_handle, avatar_url, message_text, card_payload)
                VALUES (%s, 'SYSTEM_BOT', 'Commissioner', '@CommissionerBot', '/static/players/robot_avatar.webp', %s, %s)
            """, (
                league_id,
                f"⚙ League strategies updated: {summary_str}",
                psycopg.types.json.Jsonb({
                    "type": "STRATEGY_UPDATE",
                    "title": "League Strategies Updated",
                    "highlight": f"{ai_slots} AI Teams Reconfigured",
                    "commentary": f"Active manager strategies: {summary_str}",
                })
            ))

        conn.commit()

    return {"status": "success", "message": "League strategies updated", "league_id": league_id}


@router.get("/league/{league_id}/teams_rosters")
def get_league_teams_rosters(league_id: int, week: Optional[int] = Query(None)):
    """Returns all teams in a league and their active rosters for trading/proposals."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_week, season FROM napper_leagues WHERE id = %s", (league_id,))
            lg = cur.fetchone()
            if not lg:
                raise HTTPException(status_code=404, detail="League not found")
            require_current_season_league(cur, league_id)
            _, active_week = get_current_season_week(cur)
            wk = int(week) if week is not None else active_week
            _ensure_matchup_and_rosters(cur, league_id, wk)
            conn.commit()

            cur.execute("""
                SELECT id, team_name, manager_handle, avatar_url,
                       (user_id = (SELECT id FROM napper_user_profiles ORDER BY id LIMIT 1)) as is_user
                FROM napper_teams
                WHERE league_id = %s
                ORDER BY (user_id = (SELECT id FROM napper_user_profiles ORDER BY id LIMIT 1)) DESC, team_name ASC
            """, (league_id,))
            teams = cur.fetchall()

            season, _ = get_current_season_week(cur)
            teams_data = []
            for tm in teams:
                cur.execute("""
                    SELECT p.id, p.player_name, p.full_name, p.espn_id, ps.position,
                           nr.slot, NULLIF(fp.projected_points, 0.0) as proj_fpts
                    FROM napper_rosters nr
                    JOIN players p ON p.id = nr.player_id
                    JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s AND ps.team_id IS NOT NULL
                    LEFT JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.week = %s AND fp.season = %s
                    WHERE nr.league_id = %s AND nr.team_id = %s AND nr.week = %s
                      AND ps.position IN ('QB', 'HB', 'FB', 'RB', 'WR', 'TE', 'K', 'DEF')
                    ORDER BY CASE nr.slot WHEN 'IR' THEN 2 WHEN 'BN' THEN 1 ELSE 0 END, proj_fpts DESC
                """, (season, wk, season, league_id, tm["id"], wk))
                players = []
                for p in cur.fetchall():
                    players.append({
                        "id": p["id"],
                        "player_id": p["id"],
                        "name": p["player_name"] or p["full_name"],
                        "espn_id": p["espn_id"],
                        "position": "RB" if p["position"] in ("HB", "RB", "FB") else p["position"],
                        "slot": p["slot"],
                        "proj_fpts": float(p["proj_fpts"]) if p["proj_fpts"] is not None else None,
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


@router.get("/league/{league_id}/team")
def get_league_user_team(league_id: int, week: Optional[int] = Query(None)):
    """Returns the signed-in user's team and its saved lineup for one week."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT l.season, l.current_week, l.roster_format, l.scoring_type,
                       t.id AS team_id, t.team_name, t.wins, t.losses, t.ties,
                       t.points_for, t.points_against
                FROM napper_leagues l
                JOIN napper_teams t ON t.league_id = l.id
                    AND t.user_id = (SELECT id FROM napper_user_profiles ORDER BY id LIMIT 1)
                WHERE l.id = %s
                LIMIT 1
            """, (league_id,))
            team = cur.fetchone()
            if not team:
                raise HTTPException(status_code=404, detail="User team not found in this league")

            season, active_week = get_current_season_week(cur)
            require_current_season_league(cur, league_id)
            wk = max(1, min(17, int(week) if week is not None else active_week))
            _ensure_matchup_and_rosters(cur, league_id, wk)
            conn.commit()
            cur.execute(_ACTUAL_PLAYER_POINTS_CTE + """
                SELECT nr.player_id, nr.slot, p.player_name, p.full_name, p.espn_id,
                       ps.position AS position,
                       nfl_team.abbreviation AS nfl_team,
                       opponent.abbreviation AS opponent_abbr,
                       scheduled.game_time, scheduled.status AS nfl_game_status,
                       NULLIF(fp.projected_points, 0.0) AS projected_points,
                       COALESCE(week_stats.fantasy_points, 0) AS fantasy_points,
                       COALESCE(week_stats.receptions, 0) AS receptions,
                       (week_stats.player_id IS NOT NULL) AS has_played
                FROM napper_rosters nr
                JOIN players p ON p.id = nr.player_id
                JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s AND ps.team_id IS NOT NULL
                JOIN teams nfl_team ON nfl_team.id = ps.team_id
                LEFT JOIN games scheduled ON scheduled.season = %s AND scheduled.week = %s
                    AND scheduled.status IN ('SCHEDULED', 'COMPLETED')
                    AND (scheduled.home_team_id = ps.team_id OR scheduled.away_team_id = ps.team_id)
                LEFT JOIN teams opponent ON opponent.id = CASE
                    WHEN scheduled.home_team_id = ps.team_id THEN scheduled.away_team_id
                    WHEN scheduled.away_team_id = ps.team_id THEN scheduled.home_team_id
                    ELSE NULL END
                LEFT JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.week = %s AND fp.season = %s
                LEFT JOIN actual week_stats ON week_stats.player_id = p.id
                WHERE nr.league_id = %s AND nr.team_id = %s AND nr.week = %s
                  AND ps.position IN ('QB', 'HB', 'FB', 'RB', 'WR', 'TE', 'K', 'DEF')
                ORDER BY nr.slot, COALESCE(fp.projected_points, 0) DESC, p.id
            """, (season, wk, season, wk, wk, season,
                  season, season, wk, wk, season, league_id, team["team_id"], wk))

            players = []
            for row in cur.fetchall():
                position = row["position"]
                if position in ("HB", "FB"):
                    position = "RB"
                players.append({
                    "id": row["player_id"],
                    "player_id": row["player_id"],
                    "espn_id": row["espn_id"],
                    "name": row["player_name"] or row["full_name"],
                    "position": position,
                    "team": row["nfl_team"],
                    "opponent": f"vs {row['opponent_abbr']}" if row["opponent_abbr"] else "Bye week / no matchup",
                    "kickoff": row["game_time"] or ("Bye week" if not row["nfl_game_status"] else "Time TBD"),
                    "game_status": "Final" if row["nfl_game_status"] == "COMPLETED" else ("Scheduled" if row["nfl_game_status"] == "SCHEDULED" else "Bye week"),
                    "headshot_url": f"/static/players/{row['player_id']}.webp",
                    "fpts": round(float(row["fantasy_points"] or 0) - float(row["receptions"] or 0) * _reception_adjustment(team["scoring_type"]), 2),
                    "proj_fpts": float(row["projected_points"]) if row["projected_points"] is not None else None,
                    "is_yet_to_play": row["nfl_game_status"] != "COMPLETED",
                    "injury_status": None,
                    "slot": row["slot"],
                })

            roster_format = team["roster_format"] or {}
            slot_order = _get_slot_order(roster_format)
            by_slot = defaultdict(list)
            for player in players:
                by_slot[player["slot"]].append(player)

            roster = []
            for slot in slot_order:
                player = by_slot[slot].pop(0) if by_slot[slot] else None
                roster.append({"id": f"{slot}-{len(roster) + 1}", "slot": slot, "slot_label": slot, "player": player})
            ir_players = by_slot.pop("IR", [])
            for slot in list(by_slot):
                if slot != "BN":
                    by_slot["BN"].extend(by_slot.pop(slot))
            bench_players = by_slot.get("BN", [])
            bench_count = max(int(roster_format.get("BN", 0)), len(bench_players))
            for index in range(bench_count):
                player = bench_players[index] if index < len(bench_players) else None
                roster.append({"id": f"BN-{index + 1}", "slot": "BN", "slot_label": "BN", "player": player})
            for index in range(max(int(roster_format.get("IR", 0)), len(ir_players))):
                player = ir_players[index] if index < len(ir_players) else None
                roster.append({"id": f"IR-{index + 1}", "slot": "IR", "slot_label": "IR", "player": player})

            wins, losses, ties = team["wins"] or 0, team["losses"] or 0, team["ties"] or 0
            return {
                "league_id": league_id,
                "week": wk,
                "team": {
                    "id": team["team_id"],
                    "team_name": team["team_name"],
                    "wins": wins,
                    "losses": losses,
                    "ties": ties,
                    "record": f"{wins}-{losses}" + (f"-{ties}" if ties else ""),
                    "points_for": float(team["points_for"] or 0),
                    "points_against": float(team["points_against"] or 0),
                },
                "roster": roster,
            }


@router.post("/league/{league_id}/team/name")
def update_league_team_name(league_id: int, req: TeamNameRequest):
    """Updates the signed-in user's team name in this league."""
    team_name = req.team_name.strip()
    if not team_name or len(team_name) > 64:
        raise HTTPException(status_code=400, detail="Team name must be between 1 and 64 characters.")
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE napper_teams
                SET team_name = %s
                WHERE league_id = %s
                  AND user_id = (SELECT id FROM napper_user_profiles ORDER BY id LIMIT 1)
                RETURNING id, team_name
            """, (team_name, league_id))
            team = cur.fetchone()
            if not team:
                raise HTTPException(status_code=404, detail="Your team was not found in this league.")
        conn.commit()
    return {"team": team}


@router.get("/league/{league_id}/scores")
def get_league_scores(league_id: int, week: Optional[int] = Query(None)):
    """Returns the league's scheduled fantasy matchups and roster-derived scores."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT season, current_week, scoring_type FROM napper_leagues WHERE id = %s", (league_id,))
            league = cur.fetchone()
            if not league:
                raise HTTPException(status_code=404, detail="League not found")
            season, active_week = get_current_season_week(cur)
            require_current_season_league(cur, league_id)
            wk = max(1, min(17, int(week) if week is not None else active_week))
            _ensure_matchup_and_rosters(cur, league_id, wk)
            conn.commit()
            cur.execute("""
                SELECT m.id, m.home_team_id, m.away_team_id,
                       home.team_name AS home_name, home.manager_handle AS home_manager,
                       home.wins AS home_wins, home.losses AS home_losses,
                       away.team_name AS away_name, away.manager_handle AS away_manager,
                       away.wins AS away_wins, away.losses AS away_losses,
                       (home.user_id = (SELECT id FROM napper_user_profiles ORDER BY id LIMIT 1)) AS home_is_user,
                       (away.user_id = (SELECT id FROM napper_user_profiles ORDER BY id LIMIT 1)) AS away_is_user,
                       m.is_final
                FROM napper_matchups m
                JOIN napper_teams home ON home.id = m.home_team_id
                JOIN napper_teams away ON away.id = m.away_team_id
                WHERE m.league_id = %s AND m.week = %s
                ORDER BY m.id
            """, (league_id, wk))
            matchups = cur.fetchall()

            totals = _get_weekly_team_totals(cur, league_id, season, wk, league["scoring_type"])
            standings = {team["id"]: team for team in _get_league_standings(
                cur, league_id, season, league["scoring_type"]
            )}
            _, active_week = get_current_season_week(cur)
            week_complete = wk < active_week or _is_nfl_week_complete(cur, season, wk)

            results = []
            for row in matchups:
                home = totals.get(row["home_team_id"], {})
                away = totals.get(row["away_team_id"], {})
                final = week_complete
                results.append({
                    "id": row["id"],
                    "home": {"id": row["home_team_id"], "team_name": row["home_name"], "manager_handle": row["home_manager"], **{k: standings.get(row["home_team_id"], {}).get(k, 0) for k in ("wins", "losses", "ties", "seed")}, "is_user": row["home_is_user"]},
                    "away": {"id": row["away_team_id"], "team_name": row["away_name"], "manager_handle": row["away_manager"], **{k: standings.get(row["away_team_id"], {}).get(k, 0) for k in ("wins", "losses", "ties", "seed")}, "is_user": row["away_is_user"]},
                    "home_score": round(float(home.get("score") or 0), 2),
                    "away_score": round(float(away.get("score") or 0), 2),
                    "home_proj": round(float(home.get("projection") or 0), 2),
                    "away_proj": round(float(away.get("projection") or 0), 2),
                    "has_started": int(home.get("played_players") or 0) + int(away.get("played_players") or 0) > 0,
                    "is_final": final,
                })
            return {"league_id": league_id, "week": wk, "current_week": active_week, "matchups": results}


@router.get("/league/{league_id}/matchup")
def get_league_matchup(
    league_id: int,
    week: Optional[int] = Query(None),
    matchup_id: Optional[int] = Query(None),
):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_week, roster_format, season, scoring_type FROM napper_leagues WHERE id = %s", (league_id,))
            lg = cur.fetchone()
            if not lg:
                raise HTTPException(status_code=404, detail="League not found")

            # Support week switching 1 to 17
            season, active_week = get_current_season_week(cur)
            require_current_season_league(cur, league_id)
            wk = int(week) if week is not None else active_week
            wk = max(1, min(17, wk))

            # Ensure matchups and rosters exist for this week
            _ensure_matchup_and_rosters(cur, league_id, wk)
            conn.commit()

            match = None
            if matchup_id is not None:
                cur.execute("""
                    SELECT m.id, m.home_team_id, m.away_team_id, m.home_score, m.away_score,
                           m.home_proj, m.away_proj, m.win_prob_home, m.is_final
                    FROM napper_matchups m
                    WHERE m.league_id = %s AND m.week = %s AND m.id = %s
                """, (league_id, wk, matchup_id))
                match = cur.fetchone()
                if not match:
                    raise HTTPException(status_code=404, detail=f"Matchup {matchup_id} was not found for Week {wk}")
            else:
                # Default to the active user's matchup for the main Matchup tab.
                cur.execute("SELECT id FROM napper_teams WHERE league_id = %s AND user_id = (SELECT id FROM napper_user_profiles ORDER BY id LIMIT 1) LIMIT 1", (league_id,))
                u_row = cur.fetchone()
                user_team_id = u_row["id"] if u_row else None

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

            # Query rosters for both teams in this week
            cur.execute(_ACTUAL_PLAYER_POINTS_CTE + """
                SELECT nr.team_id, nr.slot, nr.player_id, p.player_name, p.full_name, p.espn_id,
                       t.abbreviation as nfl_team,
                       ps.position as position,
                       NULLIF(fp.projected_points, 0.0) as proj_pts,
                       opponent.abbreviation AS opponent_abbr, scheduled.game_time,
                       scheduled.status AS nfl_game_status,
                       COALESCE(actual.fantasy_points, 0) AS fantasy_points,
                       COALESCE(actual.receptions, 0) AS receptions
                FROM napper_rosters nr
                JOIN players p ON p.id = nr.player_id
                JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s AND ps.team_id IS NOT NULL
                JOIN teams t ON t.id = ps.team_id
                LEFT JOIN games scheduled ON scheduled.season = %s AND scheduled.week = %s
                    AND scheduled.status IN ('SCHEDULED', 'COMPLETED')
                    AND (scheduled.home_team_id = ps.team_id OR scheduled.away_team_id = ps.team_id)
                LEFT JOIN teams opponent ON opponent.id = CASE
                    WHEN scheduled.home_team_id = ps.team_id THEN scheduled.away_team_id
                    WHEN scheduled.away_team_id = ps.team_id THEN scheduled.home_team_id
                    ELSE NULL END
                LEFT JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.week = %s AND fp.season = %s
                LEFT JOIN actual ON actual.player_id = p.id
                WHERE nr.league_id = %s AND nr.week = %s AND nr.team_id IN (%s, %s)
                  AND ps.position IN ('QB', 'HB', 'FB', 'RB', 'WR', 'TE', 'K', 'DEF')
            """, (season, wk, season, wk, wk, season,
                  season, season, wk, wk, season, league_id, wk, left_team_id, right_team_id))
            roster_rows = cur.fetchall()

            left_roster = [r for r in roster_rows if r["team_id"] == left_team_id]
            right_roster = [r for r in roster_rows if r["team_id"] == right_team_id]

            def make_player_dict(r):
                if not r:
                    return None
                pid = r["player_id"]
                proj = float(r["proj_pts"]) if r["proj_pts"] is not None else None
                return {
                    "id": pid,
                    "player_id": pid,
                    "espn_id": r["espn_id"],
                    "name": r["player_name"] or r["full_name"],
                    "full_name": r["full_name"] or r["player_name"],
                    "position": "RB" if r["position"] in ("HB", "RB", "FB") else (r["position"] or "FLEX"),
                    "slot": r.get("slot"),
                    "team": r["nfl_team"] or "NFL",
                    "opponent": f"vs {r['opponent_abbr']}" if r["opponent_abbr"] else "Bye week / no matchup",
                    "kickoff": r["game_time"] or ("Bye week" if not r["nfl_game_status"] else "Time TBD"),
                    "game_status": "Final" if r["nfl_game_status"] == "COMPLETED" else ("Scheduled" if r["nfl_game_status"] == "SCHEDULED" else "Bye week"),
                    "headshot_url": f"/static/players/{pid}.webp",
                    "fpts": round(float(r["fantasy_points"] or 0) - float(r["receptions"] or 0) * _reception_adjustment(lg["scoring_type"]), 2),
                    "proj_fpts": round(proj, 2) if proj is not None else None,
                    "is_yet_to_play": r["nfl_game_status"] != "COMPLETED",
                    "injury_status": None,
                }

            roster_format = lg["roster_format"] or {}
            slot_order = _get_slot_order(roster_format)

            def assign_slots_to_players(roster: List[dict]):
                starters_pool = [r for r in roster if r["slot"] not in ("BN", "IR")]
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
                        if not found and bench_pool:
                            for b_idx, bp in enumerate(bench_pool):
                                b_pos = bp.get("position") or ""
                                b_norm = "RB" if b_pos in ("HB", "FB", "RB") else b_pos
                                if s == "FLEX" and b_norm in ("RB", "WR", "TE"):
                                    found = bench_pool.pop(b_idx)
                                    break
                                elif s == "SUPERFLEX" and b_norm in ("QB", "RB", "WR", "TE"):
                                    found = bench_pool.pop(b_idx)
                                    break
                                elif b_norm == s:
                                    found = bench_pool.pop(b_idx)
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
            max_bn = max(len(left_bench), len(right_bench), int(roster_format.get("BN", 0)))
            for i in range(max_bn):
                lp = left_bench[i] if i < len(left_bench) else None
                rp = right_bench[i] if i < len(right_bench) else None
                bench.append({
                    "slot": "BN",
                    "slot_label": "BN",
                    "left_player": make_player_dict(lp),
                    "right_player": make_player_dict(rp),
                })

            totals = _get_weekly_team_totals(cur, league_id, season, wk, lg["scoring_type"])
            standings = {team["id"]: team for team in _get_league_standings(
                cur, league_id, season, lg["scoring_type"]
            )}
            left_total, right_total = totals.get(left_team_id, {}), totals.get(right_team_id, {})
            h_proj = float(left_total.get("projection") or 0.0)
            a_proj = float(right_total.get("projection") or 0.0)
            left_score = float(left_total.get("score") or 0.0)
            right_score = float(right_total.get("score") or 0.0)
            # Display each side's share of available points: projections before kickoff,
            # then actual starter points once any games have begun.
            any_games_started = int(left_total.get("played_players") or 0) + int(right_total.get("played_players") or 0) > 0
            left_basis, right_basis = (left_score, right_score) if any_games_started else (h_proj, a_proj)
            total_basis = left_basis + right_basis
            win_prob_home = left_basis / total_basis if total_basis > 0 else 0.5
            win_prob_left = round(win_prob_home, 3)
            win_prob_right = round(1.0 - win_prob_home, 3)

            left_record = standings.get(left_team_id, {})
            right_record = standings.get(right_team_id, {})

            return {
                "league_id": league_id,
                "week": wk,
                "left_team": {
                    **left_team_meta,
                    **{k: left_record.get(k, left_team_meta.get(k, 0)) for k in ("wins", "losses", "ties", "points_for", "points_against", "seed")},
                    "score": round(left_score, 2),
                    "proj": round(h_proj, 2),
                    "win_prob": win_prob_left,
                    "yet_to_play_summary": f"Yet to play ({max(0, int(left_total.get('starters') or 0) - int(left_total.get('played_players') or 0))}) starters",
                },
                "right_team": {
                    **right_team_meta,
                    **{k: right_record.get(k, right_team_meta.get(k, 0)) for k in ("wins", "losses", "ties", "points_for", "points_against", "seed")},
                    "score": round(right_score, 2),
                    "proj": round(a_proj, 2),
                    "win_prob": win_prob_right,
                    "yet_to_play_summary": f"Yet to play ({max(0, int(right_total.get('starters') or 0) - int(right_total.get('played_players') or 0))}) starters",
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
            cur.execute("SELECT current_week, roster_format, season, scoring_type FROM napper_leagues WHERE id = %s", (league_id,))
            lg = cur.fetchone()
            if not lg:
                raise HTTPException(status_code=404, detail="League not found")
            _, active_week = get_current_season_week(cur)
            wk = req.week or active_week

            # 2. Validate team belongs to league
            cur.execute("SELECT id FROM napper_teams WHERE id = %s AND league_id = %s", (req.team_id, league_id))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Team not found in this league")

            current_season, _ = get_current_season_week(cur)

            # 3. Fetch current team roster for this week
            cur.execute("""
                SELECT nr.player_id, nr.slot, ps.position as position
                FROM napper_rosters nr
                JOIN player_seasons ps ON ps.player_id = nr.player_id AND ps.season = %s AND ps.team_id IS NOT NULL
                JOIN teams nfl_team ON nfl_team.id = ps.team_id
                WHERE nr.league_id = %s AND nr.team_id = %s AND nr.week = %s
                  AND ps.position IN ('QB', 'HB', 'FB', 'RB', 'WR', 'TE', 'K', 'DEF')
            """, (current_season, league_id, req.team_id, wk))
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

            # IR takes only players on injured reserve or out for IR_MIN_WEEKS+ weeks (decision D1).
            to_ir = [swap.player_id for swap in req.swaps if swap.new_slot.upper() == "IR"
                     and roster_by_pid[swap.player_id]["slot"] != "IR"]
            if to_ir:
                injuries = get_injury_map(cur, current_season, wk, to_ir)
                for pid in to_ir:
                    if not is_ir_eligible(injuries.get(pid)):
                        cur.execute("SELECT COALESCE(player_name, full_name) AS name FROM players WHERE id = %s", (pid,))
                        name_row = cur.fetchone()
                        raise HTTPException(
                            status_code=400,
                            detail=f"{name_row['name'] if name_row else pid} can't go on IR: only players on "
                                   f"injured reserve or out {IR_MIN_WEEKS}+ weeks are eligible",
                        )

            # 5. Validate roster slot limits
            roster_format = lg["roster_format"] or {}
            slot_counts = defaultdict(int)
            for s in simulated_slots.values():
                if s != "BN":
                    slot_counts[s] += 1

            for s, count in slot_counts.items():
                max_allowed = int(roster_format.get(s, 0))
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
            # Refresh both matchup sides from their saved starters and current projections.
            cur.execute("""
                SELECT id, home_team_id, away_team_id, home_proj, away_proj
                FROM napper_matchups
                WHERE league_id = %s AND week = %s AND (home_team_id = %s OR away_team_id = %s)
            """, (league_id, wk, req.team_id, req.team_id))
            match_row = cur.fetchone()
            if match_row:
                totals = _get_weekly_team_totals(cur, league_id, current_season, wk, lg["scoring_type"])
                h_p = float(totals.get(match_row["home_team_id"], {}).get("projection") or 0)
                a_p = float(totals.get(match_row["away_team_id"], {}).get("projection") or 0)
                points_total = h_p + a_p
                new_win_prob = round(h_p / points_total, 3) if points_total > 0 else 0.5

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
            cur.execute("SELECT current_week, season FROM napper_leagues WHERE id = %s", (league_id,))
            lg = cur.fetchone()
            if not lg:
                raise HTTPException(status_code=404, detail="League not found")
            current_season, wk = get_current_season_week(cur)
            require_current_season_league(cur, league_id)
            _ensure_matchup_and_rosters(cur, league_id, wk)
            conn.commit()

            # Query all players not currently rostered in this league for this week
            sql = """
                SELECT p.id as player_id, p.player_name, p.full_name, p.espn_id, ps.position,
                       NULLIF(fp.projected_points, 0) as proj_pts,
                       COALESCE(NULLIF(ss.ppg_fantasy_points, 0), fp.avg_points) as avg_pts,
                       fp.ros_rank,
                       fp.matchup_stars as matchup_stars,
                       COALESCE(t.abbreviation, 'NFL') as team_abbr,
                       opponent.abbreviation as opponent_abbr,
                       scheduled.game_time, scheduled.status AS nfl_game_status
                FROM players p
                JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s AND ps.team_id IS NOT NULL
                JOIN teams t ON t.id = ps.team_id
                LEFT JOIN games scheduled ON scheduled.season = %s AND scheduled.week = %s
                    AND scheduled.status IN ('SCHEDULED', 'COMPLETED')
                    AND (scheduled.home_team_id = ps.team_id OR scheduled.away_team_id = ps.team_id)
                LEFT JOIN teams opponent ON opponent.id = CASE
                    WHEN scheduled.home_team_id = ps.team_id THEN scheduled.away_team_id
                    WHEN scheduled.away_team_id = ps.team_id THEN scheduled.home_team_id
                    ELSE NULL END
                LEFT JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.week = %s AND fp.season = %s
                LEFT JOIN season_stats ss ON ss.player_id = p.id AND ss.season = ps.season
                WHERE p.id NOT IN (
                    SELECT player_id FROM napper_rosters WHERE league_id = %s AND week = %s
                )
                AND ps.position IN ('QB', 'HB', 'FB', 'RB', 'WR', 'TE', 'K', 'DEF')
            """
            params = [current_season, current_season, wk, wk, current_season, league_id, wk]
            if position and position != "ALL":
                sql += " AND ps.position = %s"
                params.append(position)
            if q:
                sql += " AND (p.full_name ILIKE %s OR p.player_name ILIKE %s)"
                params.extend([f"%{q}%", f"%{q}%"])

            sql += " ORDER BY proj_pts DESC NULLS LAST LIMIT 60"
            cur.execute(sql, params)
            rows = cur.fetchall()

            players = []
            for r in rows:
                players.append({
                    "player_id": r["player_id"],
                    "espn_id": r["espn_id"],
                    "name": r["player_name"] or r["full_name"],
                    "position": r["position"],
                    "team": r["team_abbr"],
                    "opponent": f"vs {r['opponent_abbr']}" if r["opponent_abbr"] else "Bye week / no matchup",
                    "kickoff": r["game_time"] or ("Bye week" if not r["nfl_game_status"] else "Time TBD"),
                    "matchup_stars": r["matchup_stars"],
                    "proj_fpts": float(r["proj_pts"]) if r["proj_pts"] is not None else None,
                    "avg_fpts": float(r["avg_pts"]) if r["avg_pts"] is not None else None,
                    "ros_rank": r["ros_rank"],
                    "rostered_pct": None,
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
            _, active_week = get_current_season_week(cur)
            wk = req.week or active_week
            _ensure_matchup_and_rosters(cur, league_id, wk)
            conn.commit()

            # Validate team
            if not req.team_id:
                cur.execute("SELECT id, team_name, manager_handle FROM napper_teams WHERE league_id = %s AND user_id = (SELECT id FROM napper_user_profiles ORDER BY id LIMIT 1) LIMIT 1", (league_id,))
                tm = cur.fetchone()
            else:
                cur.execute("SELECT id, team_name, manager_handle FROM napper_teams WHERE id = %s AND league_id = %s", (req.team_id, league_id))
                tm = cur.fetchone()
                if not tm:
                    cur.execute("SELECT id, team_name, manager_handle FROM napper_teams WHERE league_id = %s AND user_id = (SELECT id FROM napper_user_profiles ORDER BY id LIMIT 1) LIMIT 1", (league_id,))
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
                current_season, current_week = get_current_season_week(cur)
                cur.execute("""
                    SELECT p.id, p.player_name, p.full_name, p.espn_id, ps.position,
                           fp.projected_points as proj
                    FROM players p
                    LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                    LEFT JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.week = %s AND fp.season = %s
                    WHERE p.id = ANY(%s)
                """, (current_season, current_week, current_season, list(all_pids)))
                for p in cur.fetchall():
                    players_map[p["id"]] = {
                        "id": p["id"],
                        "player_id": p["id"],
                        "name": p["player_name"] or p["full_name"],
                        "espn_id": p["espn_id"],
                        "position": p["position"] or "",
                        "headshot_url": f"/static/players/{p['id']}.webp",
                        "proj_fpts": float(p["proj"]) if p["proj"] is not None else None,
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


def _reject_ir_trade_players(cur, league_id: int, week: int, player_ids: List[int]) -> None:
    """Players in an IR slot can't be traded (decision D2)."""
    cur.execute("""
        SELECT COALESCE(p.player_name, p.full_name) AS name
        FROM napper_rosters nr JOIN players p ON p.id = nr.player_id
        WHERE nr.league_id = %s AND nr.week = %s AND nr.slot = 'IR' AND nr.player_id = ANY(%s)
    """, (league_id, week, list(player_ids)))
    names = [r["name"] for r in cur.fetchall()]
    if names:
        raise HTTPException(status_code=400, detail=f"Players on IR can't be traded: {', '.join(names)}")


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
                cur.execute("SELECT id, team_name FROM napper_teams WHERE league_id = %s AND user_id = (SELECT id FROM napper_user_profiles ORDER BY id LIMIT 1) LIMIT 1", (league_id,))
                prop_tm = cur.fetchone()
            else:
                cur.execute("SELECT id, team_name FROM napper_teams WHERE id = %s AND league_id = %s", (req.proposer_team_id, league_id))
                prop_tm = cur.fetchone()
                if not prop_tm:
                    cur.execute("SELECT id, team_name FROM napper_teams WHERE league_id = %s AND user_id = (SELECT id FROM napper_user_profiles ORDER BY id LIMIT 1) LIMIT 1", (league_id,))
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
            _reject_ir_trade_players(cur, league_id, wk, req.offered_player_ids + req.requested_player_ids)

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
            if status == "ACCEPTED":
                _reject_ir_trade_players(cur, league_id, tr["week"],
                                         (tr["offered_player_ids"] or []) + (tr["requested_player_ids"] or []))

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

                # Traded players arrive on the bench; AI teams fill any starting slot the trade emptied.
                for team in (tr["proposer_team_id"], tr["target_team_id"]):
                    fill_empty_starter_slots(cur, league_id, team, wk)

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
    """Standalone mocks are client-side and are persisted only when saved as a league."""
    raise HTTPException(
        status_code=410,
        detail="Standalone mock drafts are managed in the client and are not persisted.",
    )


@router.post("/drafts/save_as_league")
def save_draft_as_league(req: SaveDraftAsLeagueRequest):
    """Converts and saves a completed draft into an active, persistent fantasy league in PostgreSQL."""
    try:
        return napper_draft_engine.save_draft_as_league(
            name=req.name,
            teams_count=req.teams_count,
            scoring_type=req.scoring_type,
            is_superflex=req.is_superflex,
            user_slot=req.user_slot,
            draft_id=req.draft_id,
            teams=req.teams,
            picks=req.picks,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error saving draft as league: {e}", exc_info=True)
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


@router.post("/draft/{draft_id}/set_order")
def set_draft_order(draft_id: int, req: SetDraftOrderRequest):
    """Sets custom draft order for all teams in a pending draft."""
    try:
        return napper_draft_engine.set_draft_order(
            draft_id,
            slot_order=req.slot_order,
            teams=req.teams
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error setting draft order {draft_id}: {e}", exc_info=True)
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
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    """Returns active-season offensive players with sourced current-season data only."""
    with get_db() as conn:
        with conn.cursor() as cur:
            current_season, projection_week = get_current_season_week(cur)
            where_clauses = []
            params: Dict[str, Any] = {"limit": limit, "offset": offset, "current_season": current_season, "projection_week": projection_week}

            if draft_id > 0:
                cur.execute("SELECT id, season FROM napper_drafts WHERE id = %s", (draft_id,))
                draft = cur.fetchone()
                if not draft:
                    raise HTTPException(status_code=404, detail=f"Draft {draft_id} not found")
                if int(draft["season"]) != current_season:
                    raise HTTPException(status_code=409, detail="This draft belongs to an old NFL season and must be restarted")
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
            where_clauses.append("ps.team_id IS NOT NULL")

            if search:
                where_clauses.append("(p.full_name ILIKE %(search)s OR p.player_name ILIKE %(search)s)")
                params["search"] = f"%{search.strip()}%"

            where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

            cur.execute(f"""
                SELECT COUNT(DISTINCT p.id) as total
                FROM players p
                JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %(current_season)s
                WHERE {where_sql}
            """, params)
            total = cur.fetchone()["total"]

            cur.execute(f"""
                SELECT p.id, p.player_name, p.full_name, p.espn_id, ps.position, ps.age, ps.team_id,
                       t.abbreviation as team_abbr,
                       fp.adp_rank as adp,
                       fp.ros_total_points,
                       fp.projected_points as weekly_projection,
                       ps.age,
                       ss.rush_attempts as rush_att,
                       ss.rush_yards as rush_yds,
                       ss.rush_tds as rush_tds,
                       ss.receptions as rec,
                       ss.rec_yards as rec_yds,
                       ss.rec_tds as rec_tds,
                       (SELECT MIN(w.week)
                        FROM generate_series((SELECT GREATEST(current_week, 1) FROM league_state WHERE id = 1), 18) AS w(week)
                        WHERE NOT EXISTS (
                            SELECT 1 FROM games g
                            WHERE g.season = %(current_season)s AND g.week = w.week
                              AND (g.home_team_id = ps.team_id OR g.away_team_id = ps.team_id)
                        )) AS bye_week,
                       (SELECT COUNT(*) FROM napper_draft_picks ndp WHERE ndp.player_id = p.id) as draft_count
                FROM players p
                JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %(current_season)s
                JOIN teams t ON t.id = ps.team_id
                LEFT JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.season = %(current_season)s AND fp.week = %(projection_week)s
                LEFT JOIN season_stats ss ON ss.player_id = p.id AND ss.season = %(current_season)s
                WHERE {where_sql}
                ORDER BY fp.adp_rank ASC NULLS LAST, fp.ros_total_points DESC NULLS LAST, p.id ASC
                LIMIT %(limit)s OFFSET %(offset)s
            """, params)
            rows = cur.fetchall()

            players = []
            for r in rows:
                players.append({
                    "id": r["id"],
                    "player_id": r["id"],
                    "espn_id": r["espn_id"],
                    "name": r["full_name"] or r["player_name"],
                    "player_name": r["player_name"],
                    "position": r["position"],
                    "team": r["team_abbr"],
                    "headshot_url": f"/static/players/{r['id']}.webp",
                    "adp": int(r["adp"]) if r["adp"] is not None else None,
                    "bye": r["bye_week"],
                    "age": r["age"],
                    "projections": {
                        "pts": float(r["ros_total_points"]) if r["ros_total_points"] is not None else None,
                        "avg": float(r["weekly_projection"]) if r["weekly_projection"] is not None else None,
                    },
                    "rush": {
                        "rush": float(r["rush_att"]) if r["rush_att"] is not None else None,
                        "yds": float(r["rush_yds"]) if r["rush_yds"] is not None else None,
                        "td": float(r["rush_tds"]) if r["rush_tds"] is not None else None,
                    },
                    "rec": {
                        "rec": float(r["rec"]) if r["rec"] is not None else None,
                        "yds": float(r["rec_yds"]) if r["rec_yds"] is not None else None,
                        "td": float(r["rec_tds"]) if r["rec_tds"] is not None else None,
                    },
                    "draft_count": int(r["draft_count"]),
                })

            return {"players": players, "total": total}


@router.get("/league/{league_id}/draft")
def get_league_draft(league_id: int, rounds: int = Query(15, ge=1, le=25)):
    """Retrieves or creates a draft session for a league in PRE_DRAFT status."""
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id, status, season FROM napper_leagues WHERE id = %s", (league_id,))
            league = cur.fetchone()
            if not league:
                raise HTTPException(status_code=404, detail="League not found")

            active_season = get_current_season(cur)
            if int(league["season"]) != active_season:
                raise HTTPException(status_code=409, detail="This league belongs to an old NFL season and must be recreated")
            cur.execute("SELECT id FROM napper_drafts WHERE league_id = %s AND season = %s ORDER BY id DESC LIMIT 1", (league_id, active_season))
            row = cur.fetchone()

    if row:
        return napper_draft_engine.get_draft_state(row["id"])
    return napper_draft_engine.create_draft_session(league_id=league_id, rounds=rounds)
