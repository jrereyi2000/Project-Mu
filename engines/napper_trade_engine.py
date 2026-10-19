"""Napper Proactive Trade Engine.

Analyzes team rosters in `napper_rosters` against `fantasy_projections` (`ros_total_points`)
incorporating positional scarcity weights and bench depth penalties to detect genuine roster surplus
and deficits. Autonomous AI managers proactively generate fair, reciprocal trade proposals to both
other AI teams and the user's team, and evaluate/execute AI-to-AI pending trades stochastically,
mutating rosters and announcing updates in `napper_chat`.

CLI:
    python3 -m engines.napper_trade_engine --league 1 --week 6
    python3 -m engines.napper_trade_engine --league 1 --week 6 --process-ai
"""

import argparse
import json
import logging
import os
import random
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

import psycopg

from engines.napper_lineup_engine import fill_empty_starter_slots

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres"
)


def get_previous_season(cur) -> int:
    cur.execute("SELECT current_season FROM league_state WHERE id = 1")
    row = cur.fetchone()
    if row:
        cs = row["current_season"] if isinstance(row, dict) else row[0]
        if cs:
            return int(cs) - 1
    return 2035

# Standard baseline positional starters (can be overridden by league roster_format)
DEFAULT_STARTER_REQ = {
    "QB": 1,
    "RB": 2,
    "WR": 2,
    "TE": 1,
}

# Positional scarcity weights:
# In Superflex / 2QB formats, QBs carry a massive scarcity premium.
# In standard formats, starting RBs carry the highest scarcity due to injury attrition and volume.
POSITIONAL_SCARCITY_WEIGHTS = {
    "standard": {
        "QB": 1.10,
        "RB": 1.30,
        "WR": 1.05,
        "TE": 1.15,
        "K": 0.50,
        "DEF": 0.60,
    },
    "superflex": {
        "QB": 1.45,
        "RB": 1.25,
        "WR": 1.05,
        "TE": 1.10,
        "K": 0.50,
        "DEF": 0.60,
    },
}

# Recommended minimum bench depth per position before severe penalties apply
BENCH_DEPTH_TARGETS = {
    "standard": {
        "QB": 1,
        "RB": 2,
        "WR": 2,
        "TE": 1,
    },
    "superflex": {
        "QB": 2,
        "RB": 2,
        "WR": 2,
        "TE": 1,
    },
}

POS_NORMALIZATION = {
    "QB": "QB",
    "HB": "RB",
    "RB": "RB",
    "FB": "RB",
    "WR": "WR",
    "TE": "TE",
    "K": "K",
    "DEF": "DEF",
}


def _safe_commit(conn) -> None:
    """Commits connection if not inside an external transaction block or marked as uncommitted."""
    if getattr(conn, "_no_commit", False):
        return
    try:
        conn.commit()
    except psycopg.ProgrammingError:
        pass


def ensure_napper_tables_exist(conn) -> None:
    """Ensures all necessary Napper tables exist in the database."""
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS napper_user_profiles (
                id SERIAL PRIMARY KEY,
                handle VARCHAR(32) UNIQUE NOT NULL,
                display_name VARCHAR(64) NOT NULL,
                avatar_url TEXT,
                trophies INT DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS napper_leagues (
                id SERIAL PRIMARY KEY,
                name VARCHAR(64) NOT NULL,
                season INT DEFAULT 2035,
                teams_count SMALLINT DEFAULT 8,
                scoring_type VARCHAR(16) DEFAULT 'PPR',
                roster_format JSONB DEFAULT '{"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 0, "K": 1, "DEF": 1, "BN": 6, "IR": 1}'::jsonb,
                current_week INT DEFAULT 1,
                status VARCHAR(16) DEFAULT 'IN_SEASON',
                avatar_url TEXT
            );

            CREATE TABLE IF NOT EXISTS napper_teams (
                id SERIAL PRIMARY KEY,
                league_id INT REFERENCES napper_leagues(id) ON DELETE CASCADE,
                user_id INT REFERENCES napper_user_profiles(id) ON DELETE SET NULL,
                team_name VARCHAR(64) NOT NULL,
                manager_handle VARCHAR(32) NOT NULL,
                avatar_url TEXT,
                is_ai BOOLEAN DEFAULT FALSE,
                persona VARCHAR(32),
                wins INT DEFAULT 0,
                losses INT DEFAULT 0,
                ties INT DEFAULT 0,
                points_for NUMERIC(8,2) DEFAULT 0,
                points_against NUMERIC(8,2) DEFAULT 0,
                seed SMALLINT
            );

            CREATE TABLE IF NOT EXISTS napper_rosters (
                id SERIAL PRIMARY KEY,
                league_id INT NOT NULL,
                team_id INT NOT NULL REFERENCES napper_teams(id) ON DELETE CASCADE,
                week INT NOT NULL,
                player_id INT REFERENCES players(id) ON DELETE CASCADE,
                slot VARCHAR(16) NOT NULL,
                CONSTRAINT uq_napper_rosters UNIQUE (team_id, week, player_id)
            );

            CREATE TABLE IF NOT EXISTS napper_matchups (
                id SERIAL PRIMARY KEY,
                league_id INT NOT NULL REFERENCES napper_leagues(id) ON DELETE CASCADE,
                week INT NOT NULL,
                home_team_id INT NOT NULL REFERENCES napper_teams(id) ON DELETE CASCADE,
                away_team_id INT NOT NULL REFERENCES napper_teams(id) ON DELETE CASCADE,
                home_score NUMERIC(6,2) DEFAULT 0,
                away_score NUMERIC(6,2) DEFAULT 0,
                home_proj NUMERIC(6,2) DEFAULT 0,
                away_proj NUMERIC(6,2) DEFAULT 0,
                win_prob_home NUMERIC(4,3) DEFAULT 0.5,
                is_final BOOLEAN DEFAULT FALSE
            );

            CREATE TABLE IF NOT EXISTS napper_trades (
                id SERIAL PRIMARY KEY,
                league_id INT NOT NULL REFERENCES napper_leagues(id) ON DELETE CASCADE,
                week INT NOT NULL,
                proposer_team_id INT NOT NULL REFERENCES napper_teams(id) ON DELETE CASCADE,
                target_team_id INT NOT NULL REFERENCES napper_teams(id) ON DELETE CASCADE,
                offered_player_ids INT[] NOT NULL,
                requested_player_ids INT[] NOT NULL,
                status VARCHAR(16) DEFAULT 'PENDING',
                initiated_by_ai BOOLEAN DEFAULT FALSE,
                ai_rationale TEXT,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS napper_chat (
                id SERIAL PRIMARY KEY,
                league_id INT NOT NULL REFERENCES napper_leagues(id) ON DELETE CASCADE,
                sender_type VARCHAR(16) NOT NULL,
                sender_name VARCHAR(64) NOT NULL,
                sender_handle VARCHAR(32) NOT NULL,
                avatar_url TEXT,
                message_text TEXT NOT NULL,
                card_payload JSONB,
                created_at TIMESTAMPTZ DEFAULT NOW()
            );
            """
        )
        _safe_commit(conn)


def get_connection(conn_info: Optional[str] = None, conn: Optional[Any] = None):
    """Context-friendly connection helper."""
    if conn is not None:
        class DummyContext:
            def __enter__(self):
                return conn
            def __exit__(self, exc_type, exc_val, exc_tb):
                pass
        return DummyContext()
    dsn = conn_info or DATABASE_URL
    return psycopg.connect(dsn)


def _get_league_roster_requirements(cur, league_id: int) -> Tuple[int, Dict[str, int], bool]:
    """Fetches league season, starter position requirements, and superflex indicator."""
    cur.execute(
        "SELECT season, roster_format FROM napper_leagues WHERE id = %s",
        (league_id,),
    )
    row = cur.fetchone()
    prev_season = get_previous_season(cur)
    season = prev_season
    roster_req = dict(DEFAULT_STARTER_REQ)
    is_superflex = False

    if row:
        season = row[0] or prev_season
        fmt = row[1]
        if isinstance(fmt, str):
            try:
                fmt = json.loads(fmt)
            except Exception:
                fmt = {}
        if isinstance(fmt, dict):
            for pos in ("QB", "RB", "WR", "TE"):
                if pos in fmt:
                    roster_req[pos] = int(fmt[pos])
            if fmt.get("SUPERFLEX", 0) > 0 or fmt.get("QB", 1) >= 2:
                is_superflex = True
                roster_req["QB"] = max(roster_req["QB"], 2)

    return season, roster_req, is_superflex


def get_scarcity_weights(is_superflex: bool) -> Dict[str, float]:
    """Returns positional scarcity weights based on league format."""
    return POSITIONAL_SCARCITY_WEIGHTS["superflex" if is_superflex else "standard"]


def get_bench_depth_targets(is_superflex: bool) -> Dict[str, int]:
    """Returns minimum safe bench depth targets per position."""
    return BENCH_DEPTH_TARGETS["superflex" if is_superflex else "standard"]


def analyze_team_rosters(
    cur,
    league_id: int,
    week: int,
    season: int,
    roster_req: Dict[str, int],
    is_superflex: bool = False,
) -> Dict[int, Dict[str, Any]]:
    """Analyzes all teams in the league for surplus and deficit positions.

    Incorporate positional scarcity weights and bench depth penalties:
    - Starter quality is evaluated based on Rest-of-Season projections (`ros_total_points`).
    - Positional scarcity weights scale both starter upgrade needs and surplus trade value.
    - Bench depth penalties penalize rosters that have inadequate reserves at key positions,
      blocking teams from offering away players when doing so leaves them vulnerable.
    """
    scarcity_weights = get_scarcity_weights(is_superflex)
    bench_targets = get_bench_depth_targets(is_superflex)

    # 1. Fetch teams
    cur.execute(
        """
        SELECT id, team_name, manager_handle, avatar_url, is_ai, persona
        FROM napper_teams
        WHERE league_id = %s
        """,
        (league_id,),
    )
    teams: Dict[int, Dict[str, Any]] = {}
    for tid, tname, handle, avatar, is_ai, persona in cur.fetchall():
        teams[tid] = {
            "team_id": tid,
            "team_name": tname,
            "manager_handle": handle,
            "avatar_url": avatar or "/static/avatars/default.png",
            "is_ai": bool(is_ai),
            "persona": persona or "ANALYTICS_NERD",
            "roster": [],
            "positions": {"QB": [], "RB": [], "WR": [], "TE": []},
            "bench_depth": {"QB": 0, "RB": 0, "WR": 0, "TE": 0},
            "surplus": {"QB": [], "RB": [], "WR": [], "TE": []},
            "deficits": [],  # list of (position, severity_score, rationale)
        }

    if not teams:
        return {}

    # 2. Fetch rosters and projections for this week with intelligent fallbacks
    cur.execute(
        """
        SELECT 
            r.team_id,
            r.player_id,
            r.slot,
            COALESCE(p.full_name, p.player_name, 'Unknown Player') AS player_name,
            COALESCE(
                CASE 
                    WHEN fp.position IN ('QB', 'HB', 'RB', 'FB', 'WR', 'TE', 'K', 'DEF') THEN fp.position
                    WHEN r.slot IN ('QB', 'RB', 'WR', 'TE', 'K', 'DEF') THEN r.slot
                    WHEN r.slot = 'SUPERFLEX' THEN 'QB'
                    WHEN r.slot = 'FLEX' THEN 'RB'
                    WHEN (SELECT position FROM player_seasons WHERE player_id = r.player_id AND position IN ('QB', 'HB', 'RB', 'WR', 'TE') LIMIT 1) IS NOT NULL 
                        THEN (SELECT position FROM player_seasons WHERE player_id = r.player_id AND position IN ('QB', 'HB', 'RB', 'WR', 'TE') LIMIT 1)
                    ELSE 'WR'
                END,
                'WR'
            ) AS raw_pos,
            COALESCE(
                fp.ros_total_points,
                (SELECT ros_total_points FROM fantasy_projections WHERE player_id = r.player_id AND ros_total_points > 0 LIMIT 1),
                fp.projected_points * 12.0,
                CASE WHEN UPPER(r.slot) LIKE 'BN%%' THEN 30.0 ELSE 50.0 END
            ) AS ros_value,
            COALESCE(
                fp.projected_points,
                (SELECT projected_points FROM fantasy_projections WHERE player_id = r.player_id AND projected_points > 0 LIMIT 1),
                CASE WHEN UPPER(r.slot) LIKE 'BN%%' THEN 6.0 ELSE 12.0 END
            ) AS weekly_proj,
            COALESCE(t.abbreviation, 'FA') AS nfl_team
        FROM napper_rosters r
        JOIN players p ON r.player_id = p.id
        LEFT JOIN fantasy_projections fp ON (
            fp.player_id = r.player_id 
            AND fp.season = %s 
            AND fp.week = %s
        )
        LEFT JOIN teams t ON fp.team_id = t.id
        WHERE r.league_id = %s AND r.week = %s
        ORDER BY ros_value DESC
        """,
        (season, week, league_id, week),
    )

    rows = cur.fetchall()
    # Fallback if rosters were empty for this specific week: query week 1 or latest week
    if not rows:
        cur.execute(
            """
            SELECT 
                r.team_id,
                r.player_id,
                r.slot,
                COALESCE(p.full_name, p.player_name, 'Unknown Player') AS player_name,
                COALESCE(
                    CASE 
                        WHEN fp.position IN ('QB', 'HB', 'RB', 'FB', 'WR', 'TE', 'K', 'DEF') THEN fp.position
                        WHEN r.slot IN ('QB', 'RB', 'WR', 'TE', 'K', 'DEF') THEN r.slot
                        WHEN r.slot = 'SUPERFLEX' THEN 'QB'
                        WHEN r.slot = 'FLEX' THEN 'RB'
                        ELSE 'WR'
                    END,
                    'WR'
                ) AS raw_pos,
                COALESCE(
                    fp.ros_total_points,
                    CASE WHEN UPPER(r.slot) LIKE 'BN%%' THEN 30.0 ELSE 50.0 END
                ) AS ros_value,
                COALESCE(fp.projected_points, 8.0) AS weekly_proj,
                COALESCE(t.abbreviation, 'FA') AS nfl_team
            FROM napper_rosters r
            JOIN players p ON r.player_id = p.id
            LEFT JOIN fantasy_projections fp ON fp.player_id = r.player_id AND fp.season = %s
            LEFT JOIN teams t ON fp.team_id = t.id
            WHERE r.league_id = %s
            ORDER BY ros_value DESC
            """,
            (season, league_id),
        )
        rows = cur.fetchall()

    for tid, pid, slot, pname, raw_pos, ros_val, wk_proj, nfl_team in rows:
        if tid not in teams or slot.upper() == "IR":
            continue  # players on IR are never traded and don't count toward depth (decision D2)
        norm_pos = POS_NORMALIZATION.get(raw_pos.upper(), "WR")
        is_bench = slot.upper().startswith("BN") or slot.upper() == "BENCH"
        player_obj = {
            "player_id": pid,
            "player_name": pname,
            "position": norm_pos,
            "raw_position": raw_pos,
            "slot": slot,
            "is_bench": is_bench,
            "ros_value": float(ros_val),
            "weekly_proj": float(wk_proj),
            "nfl_team": nfl_team,
            "scarcity_weight": scarcity_weights.get(norm_pos, 1.0),
            "effective_ros": float(ros_val) * scarcity_weights.get(norm_pos, 1.0),
        }
        teams[tid]["roster"].append(player_obj)
        if norm_pos in teams[tid]["positions"]:
            teams[tid]["positions"][norm_pos].append(player_obj)
            if is_bench:
                teams[tid]["bench_depth"][norm_pos] += 1

    # 3. Determine surplus and deficits per team incorporating scarcity & bench depth
    for tid, tdata in teams.items():
        for pos, players in tdata["positions"].items():
            # Sort players descending by raw ROS value
            players.sort(key=lambda p: p["ros_value"], reverse=True)
            required = roster_req.get(pos, 1)
            target_bench = bench_targets.get(pos, 1)
            scarcity = scarcity_weights.get(pos, 1.0)
            total_at_pos = len(players)
            bench_count = max(0, total_at_pos - required)

            # Baseline quality threshold for an acceptable starter
            starter_threshold = 40.0 if pos in ("RB", "WR") else (55.0 if pos == "QB" else 30.0)
            quality_players = [p for p in players if p["ros_value"] >= starter_threshold]

            # Deficit evaluation:
            pos_deficit_score = 0.0
            deficit_reasons = []

            # 3A. Direct Starter Shortage
            if len(quality_players) < required:
                missing_starters = required - len(quality_players)
                # Severe penalty for unable to field starter
                shortage_penalty = missing_starters * 50.0 * scarcity
                pos_deficit_score += shortage_penalty
                deficit_reasons.append(f"missing {missing_starters} quality starter(s)")
            elif quality_players and quality_players[required - 1]["ros_value"] < starter_threshold * 1.25:
                # Weak starter upgrade opportunity
                upgrade_gap = (starter_threshold * 1.25 - quality_players[required - 1]["ros_value"]) * scarcity
                pos_deficit_score += upgrade_gap
                deficit_reasons.append("weak starter tier")

            # 3B. Bench Depth Penalty
            if bench_count < target_bench:
                missing_bench = target_bench - bench_count
                # Bench depth penalty: Roster is dangerously thin, susceptible to byes/injuries
                bench_penalty = missing_bench * 25.0 * scarcity
                pos_deficit_score += bench_penalty
                deficit_reasons.append(f"bench depth penalty ({bench_count}/{target_bench} backups)")

            if pos_deficit_score > 0:
                tdata["deficits"].append((pos, pos_deficit_score, "; ".join(deficit_reasons)))

            # Surplus evaluation:
            # A team ONLY has surplus if:
            # 1. Total players at position strictly exceeds starter requirements
            # 2. Bench depth penalty is NOT severe (i.e. bench_count >= 1)
            # 3. The surplus player has real trade value (>= 25.0 ROS)
            if total_at_pos > required and bench_count >= 1:
                # Candidates can be excess quality players or startable bench pieces
                excess_candidates = [
                    p for p in players[required:]
                    if p["ros_value"] >= 25.0
                ]
                # If team has deep quality bench, they are prime surplus
                if excess_candidates:
                    tdata["surplus"][pos].extend(excess_candidates)

        # Sort deficits descending by severity score
        tdata["deficits"].sort(key=lambda d: d[1], reverse=True)

    return teams


def generate_trade_proposals(
    league_id: int,
    week: int,
    conn_info: Optional[str] = None,
    conn: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """Analyzes team rosters and generates fair trade proposals initiated by AI managers.

    Incorporates positional scarcity weights, bench depth penalties, and persona-driven
    natural language rationales. Saves proposals to `napper_trades` and announces them in `napper_chat`.
    """
    proposals_created: List[Dict[str, Any]] = []

    with get_connection(conn_info=conn_info, conn=conn) as active_conn:
        ensure_napper_tables_exist(active_conn)
        with active_conn.cursor() as cur:
            season, roster_req, is_superflex = _get_league_roster_requirements(cur, league_id)
            scarcity_weights = get_scarcity_weights(is_superflex)
            teams = analyze_team_rosters(cur, league_id, week, season, roster_req, is_superflex)

            if not teams or len(teams) < 2:
                return []

            # Check existing pending trades to avoid duplicate proposals
            cur.execute(
                """
                SELECT proposer_team_id, target_team_id, offered_player_ids, requested_player_ids
                FROM napper_trades
                WHERE league_id = %s AND status = 'PENDING'
                """,
                (league_id,),
            )
            existing_trades = cur.fetchall()
            existing_pairs: Set[Tuple[int, int]] = {
                (r[0], r[1]) for r in existing_trades
            }
            existing_player_trades: Set[Tuple[int, int]] = {
                (pid, r[0]) for r in existing_trades for pid in (r[2] or [])
            }

            ai_team_ids = [tid for tid, t in teams.items() if t["is_ai"]]
            all_team_ids = list(teams.keys())

            # Attempt trade pairings: for each AI team, search for a complementary partner
            for proposer_id in ai_team_ids:
                proposer = teams[proposer_id]
                proposer_surplus_pos = [
                    pos for pos, plist in proposer["surplus"].items() if plist
                ]
                proposer_deficit_pos = [pos for pos, _, _ in proposer["deficits"]]

                # If no strict surplus exists, find any position where team has 2+ bench pieces
                if not proposer_surplus_pos:
                    for pos, pcount in proposer["bench_depth"].items():
                        if pcount >= 2 and proposer["positions"][pos]:
                            bench_candidates = [
                                p for p in proposer["positions"][pos]
                                if p["is_bench"] and p["ros_value"] >= 20.0
                            ]
                            if bench_candidates:
                                proposer_surplus_pos.append(pos)
                                proposer["surplus"][pos].extend(bench_candidates)

                if not proposer_surplus_pos or not proposer_deficit_pos:
                    continue

                # Shuffle target candidates to ensure variety
                target_candidates = [tid for tid in all_team_ids if tid != proposer_id]
                random.shuffle(target_candidates)

                for target_id in target_candidates:
                    if (proposer_id, target_id) in existing_pairs:
                        continue

                    target = teams[target_id]
                    target_deficit_pos = [pos for pos, _, _ in target["deficits"]]
                    target_surplus_pos = [
                        pos for pos, plist in target["surplus"].items() if plist
                    ]

                    # Match reciprocal needs
                    matching_offer_pos = None
                    matching_request_pos = None

                    # 1. Direct reciprocal: Proposer has surplus in Target's deficit, and vice-versa
                    for pos_a in proposer_surplus_pos:
                        for pos_b in target_surplus_pos:
                            if pos_a != pos_b and pos_b in proposer_deficit_pos and pos_a in target_deficit_pos:
                                matching_offer_pos = pos_a
                                matching_request_pos = pos_b
                                break
                        if matching_offer_pos:
                            break

                    # 2. Relaxed match: Proposer offers surplus, target has available talent at proposer's deficit
                    if not matching_offer_pos:
                        for pos_a in proposer_surplus_pos:
                            for pos_b in proposer_deficit_pos:
                                if pos_a != pos_b and len(target["positions"].get(pos_b, [])) > roster_req.get(pos_b, 1):
                                    matching_offer_pos = pos_a
                                    matching_request_pos = pos_b
                                    break
                            if matching_offer_pos:
                                break

                    if not matching_offer_pos or not matching_request_pos:
                        continue

                    offered_candidates = proposer["surplus"].get(matching_offer_pos, [])
                    requested_candidates = target["surplus"].get(matching_request_pos) or [
                        p for p in target["positions"].get(matching_request_pos, [])
                        if p["is_bench"] or p["ros_value"] >= 25.0
                    ]

                    if not offered_candidates or not requested_candidates:
                        continue

                    # Search for balanced trade package
                    best_trade = None
                    min_diff = 99999.0

                    # Evaluate 1-for-1 trades incorporating positional scarcity weights
                    for p_off in offered_candidates[:4]:
                        if (p_off["player_id"], proposer_id) in existing_player_trades:
                            continue
                        for p_req in requested_candidates[:4]:
                            val_off = p_off["ros_value"] * scarcity_weights.get(p_off["position"], 1.0)
                            val_req = p_req["ros_value"] * scarcity_weights.get(p_req["position"], 1.0)
                            diff = abs(val_off - val_req)
                            max_val = max(val_off, val_req, 1.0)

                            # Realistic value balance: scarcity-weighted values within 25% of each other
                            if (diff / max_val <= 0.25 or diff <= 20.0) and diff < min_diff:
                                min_diff = diff
                                best_trade = {
                                    "type": "1-for-1",
                                    "offered": [p_off],
                                    "requested": [p_req],
                                    "val_off": val_off,
                                    "val_req": val_req,
                                }

                    # If no 1-for-1 found, attempt 2-for-1 consolidation package
                    if not best_trade and len(offered_candidates) >= 2 and requested_candidates:
                        p_off1, p_off2 = offered_candidates[0], offered_candidates[1]
                        for p_req in requested_candidates[:3]:
                            val_off = (
                                p_off1["ros_value"] * scarcity_weights.get(p_off1["position"], 1.0)
                                + p_off2["ros_value"] * scarcity_weights.get(p_off2["position"], 1.0)
                            )
                            val_req = p_req["ros_value"] * scarcity_weights.get(p_req["position"], 1.0)
                            # In 2-for-1, proposer gives up 10-20% surplus value for the consolidation
                            ratio = val_off / max(val_req, 1.0)
                            if 1.05 <= ratio <= 1.35:
                                best_trade = {
                                    "type": "2-for-1",
                                    "offered": [p_off1, p_off2],
                                    "requested": [p_req],
                                    "val_off": val_off,
                                    "val_req": val_req,
                                }
                                break

                    if not best_trade:
                        continue

                    offered_players = best_trade["offered"]
                    requested_players = best_trade["requested"]
                    offered_ids = [p["player_id"] for p in offered_players]
                    requested_ids = [p["player_id"] for p in requested_players]
                    offered_names = ", ".join(p["player_name"] for p in offered_players)
                    requested_names = ", ".join(p["player_name"] for p in requested_players)

                    # Generate persona-tailored natural language explanation
                    persona = proposer.get("persona", "ANALYTICS_NERD")
                    off_pos = matching_offer_pos
                    req_pos = matching_request_pos

                    if persona == "TRASH_TALKER":
                        rationale = (
                            f"You're getting exposed at {off_pos} while my bench is overflowing with talent. "
                            f"Take {offered_names} for {requested_names} before you regret your lineup this weekend."
                        )
                        chat_msg = (
                            f"Sent you a trade proposal @{target['manager_handle']}! "
                            f"I'm offering {offered_names} to fix your atrocious {off_pos} situation. "
                            f"All I need in return is {requested_names}. Stop overthinking it and accept."
                        )
                    elif persona == "ANALYTICS_NERD":
                        rationale = (
                            f"Predictive models show a mutual surplus: {offered_names} yields positive ROS equity "
                            f"at {off_pos} (+{round(best_trade['val_off'], 1)} pts), addressing your depth deficit, "
                            f"while {requested_names} shores up our {req_pos} expected points distribution."
                        )
                        chat_msg = (
                            f"Proposed a trade to @{target['manager_handle']}. Our quantitative model identifies "
                            f"a reciprocal positional fit: sending {offered_names} for {requested_names}. "
                            f"Positive ROS delta for both franchises."
                        )
                    elif persona == "WAIVER_HAWK":
                        rationale = (
                            f"Consolidating roster depth for starting upside. Offering {offered_names} ({off_pos}) "
                            f"for {requested_names} ({req_pos}) gives you immediate high-floor volume while "
                            f"streamlining our weekly rotation."
                        )
                        chat_msg = (
                            f"Offer in your inbox @{target['manager_handle']}! Roster churn is the key to a championship. "
                            f"Giving up {offered_names} to land {requested_names}. Let's make it happen."
                        )
                    elif persona == "TRADE_FIEND":
                        rationale = (
                            f"Proactive market move: acquiring {requested_names} ({req_pos}) gives our roster "
                            f"high-leverage starting capital, while sending {offered_names} ({off_pos}) gives you "
                            f"the immediate depth your squad needs to compete."
                        )
                        chat_msg = (
                            f"Trade offer sent to @{target['manager_handle']}! Sending {offered_names} for {requested_names}. "
                            f"Win-win deal to upgrade both of our rosters. Let's make it official."
                        )
                    elif persona == "UNDERDOG":
                        rationale = (
                            f"Targeting breakout ceiling and explosive talent. Offering {offered_names} ({off_pos}) "
                            f"for {requested_names} ({req_pos}) brings elite youth upside into our starting lineup."
                        )
                        chat_msg = (
                            f"Sent a proposal to @{target['manager_handle']}. Offering {offered_names} to acquire "
                            f"{requested_names}. Huge believer in this breakout upside!"
                        )
                    elif persona == "ZERO_RB_ZEALOT":
                        rationale = (
                            f"Zero-RB roster balancing: transferring backfield touches ({offered_names}) "
                            f"in exchange for high-volume pass-catching equity ({requested_names})."
                        )
                        chat_msg = (
                            f"Proposal submitted @{target['manager_handle']}! Trading running back depth ({offered_names}) "
                            f"for {requested_names}. Perfect positional exchange."
                        )
                    elif persona == "HERO_RB":
                        rationale = (
                            f"Consolidating around our anchor bellcow: sending {offered_names} to secure {requested_names} "
                            f"gives us the foundational piece needed for a deep playoff run."
                        )
                        chat_msg = (
                            f"Trade offer sent to @{target['manager_handle']}! Packaging {offered_names} for {requested_names}. "
                            f"Take a look and let's get this deal across the goal line."
                        )
                    else:  # DIE_HARD
                        rationale = (
                            f"Looking for hard-nosed football players who play with grit. Offering {offered_names} "
                            f"to bring {requested_names} into our {req_pos} room for the playoff push."
                        )
                        chat_msg = (
                            f"Sent an offer over to @{target['manager_handle']}. Offering {offered_names} for "
                            f"{requested_names}. Real football guys who can lead our locker room down the stretch."
                        )

                    # Insert into napper_trades
                    cur.execute(
                        """
                        INSERT INTO napper_trades (
                            league_id, week, proposer_team_id, target_team_id,
                            offered_player_ids, requested_player_ids,
                            status, initiated_by_ai, ai_rationale
                        ) VALUES (%s, %s, %s, %s, %s, %s, 'PENDING', TRUE, %s)
                        RETURNING id
                        """,
                        (
                            league_id,
                            week,
                            proposer_id,
                            target_id,
                            offered_ids,
                            requested_ids,
                            rationale,
                        ),
                    )
                    trade_id = cur.fetchone()[0]

                    # Insert announcement card into napper_chat
                    card_payload = {
                        "type": "TRADE_ALERT",
                        "award_type": "TRADE_ALERT",
                        "title": "TRADE PROPOSAL",
                        "subtitle": f"{proposer['team_name']} proposed a trade to {target['team_name']}",
                        "details": {
                            "trade_id": trade_id,
                            "offered": offered_names,
                            "requested": requested_names,
                            "offered_positions": [p["position"] for p in offered_players],
                            "requested_positions": [p["position"] for p in requested_players],
                            "rationale": rationale,
                            "proposer_persona": persona,
                        },
                    }

                    cur.execute(
                        """
                        INSERT INTO napper_chat (
                            league_id, sender_type, sender_name, sender_handle,
                            avatar_url, message_text, card_payload
                        ) VALUES (%s, 'AI_MANAGER', %s, %s, %s, %s, %s)
                        """,
                        (
                            league_id,
                            proposer["team_name"],
                            proposer["manager_handle"],
                            proposer["avatar_url"],
                            chat_msg,
                            json.dumps(card_payload),
                        ),
                    )

                    existing_pairs.add((proposer_id, target_id))
                    for pid in offered_ids:
                        existing_player_trades.add((pid, proposer_id))

                    proposals_created.append({
                        "trade_id": trade_id,
                        "proposer_id": proposer_id,
                        "target_id": target_id,
                        "offered_player_ids": offered_ids,
                        "requested_player_ids": requested_ids,
                        "rationale": rationale,
                        "chat_msg": chat_msg,
                    })
                    break

            _safe_commit(active_conn)

    return proposals_created


def process_ai_to_ai_trades(
    league_id: int,
    week: int,
    conn_info: Optional[str] = None,
    conn: Optional[Any] = None,
    random_seed: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Evaluates pending trades between AI teams, executes roster mutations upon acceptance, and logs trade announcements to chat.

    Uses positional scarcity weights and bench depth safety checks to accept or reject deals.
    """
    if random_seed is not None:
        random.seed(random_seed)

    processed_trades: List[Dict[str, Any]] = []

    with get_connection(conn_info=conn_info, conn=conn) as active_conn:
        ensure_napper_tables_exist(active_conn)
        with active_conn.cursor() as cur:
            league_season, roster_req, is_superflex = _get_league_roster_requirements(cur, league_id)
            scarcity_weights = get_scarcity_weights(is_superflex)

            # Fetch pending trades whose target is an AI team
            cur.execute(
                """
                SELECT 
                    t.id, t.week, t.proposer_team_id, t.target_team_id,
                    t.offered_player_ids, t.requested_player_ids,
                    tp.team_name AS prop_name, tp.manager_handle AS prop_handle, tp.avatar_url AS prop_avatar, tp.persona AS prop_persona,
                    tt.team_name AS targ_name, tt.manager_handle AS targ_handle, tt.avatar_url AS targ_avatar, tt.persona AS targ_persona
                FROM napper_trades t
                JOIN napper_teams tp ON t.proposer_team_id = tp.id
                JOIN napper_teams tt ON t.target_team_id = tt.id
                WHERE t.league_id = %s AND t.status = 'PENDING' AND tt.is_ai = TRUE
                """,
                (league_id,),
            )
            pending_trades = cur.fetchall()

            for (
                trade_id,
                trade_week,
                prop_id,
                targ_id,
                off_ids,
                req_ids,
                prop_name,
                prop_handle,
                prop_avatar,
                prop_persona,
                targ_name,
                targ_handle,
                targ_avatar,
                targ_persona,
            ) in pending_trades:
                off_ids = off_ids or []
                req_ids = req_ids or []

                # Fetch player details and values
                cur.execute(
                    """
                    SELECT p.id, COALESCE(p.full_name, p.player_name), COALESCE(ps.position, fp.position, 'WR'), COALESCE(fp.ros_total_points, 15.0)
                    FROM players p
                    LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                    LEFT JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.season = %s AND fp.week = %s
                    WHERE p.id = ANY(%s)
                    """,
                    (league_season, league_season, trade_week, off_ids),
                )
                off_players = cur.fetchall()

                cur.execute(
                    """
                    SELECT p.id, COALESCE(p.full_name, p.player_name), COALESCE(ps.position, fp.position, 'WR'), COALESCE(fp.ros_total_points, 15.0)
                    FROM players p
                    LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                    LEFT JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.season = %s AND fp.week = %s
                    WHERE p.id = ANY(%s)
                    """,
                    (league_season, league_season, trade_week, req_ids),
                )
                req_players = cur.fetchall()

                off_names = ", ".join(r[1] for r in off_players) if off_players else "Unknown Player"
                req_names = ", ".join(r[1] for r in req_players) if req_players else "Unknown Player"

                # Calculate scarcity-weighted values
                off_val = sum(
                    float(r[3]) * scarcity_weights.get(POS_NORMALIZATION.get(r[2], "WR"), 1.0)
                    for r in off_players
                ) if off_players else 10.0

                req_val = sum(
                    float(r[3]) * scarcity_weights.get(POS_NORMALIZATION.get(r[2], "WR"), 1.0)
                    for r in req_players
                ) if req_players else 10.0

                # Check bench depth safety for target:
                # Does giving up requested players leave target with fewer than starter requirement?
                cur.execute(
                    """
                    SELECT COALESCE(ps.position, fp.position, 'WR'), COUNT(*)
                    FROM napper_rosters r
                    JOIN players p ON r.player_id = p.id
                    LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                    LEFT JOIN fantasy_projections fp ON fp.player_id = p.id AND fp.season = %s AND fp.week = %s
                    WHERE r.league_id = %s AND r.team_id = %s AND r.week = %s AND r.slot <> 'IR'
                    GROUP BY COALESCE(ps.position, fp.position, 'WR')
                    """,
                    (league_season, league_season, week, league_id, targ_id, week),
                )
                current_counts = {POS_NORMALIZATION.get(row[0], "WR"): row[1] for row in cur.fetchall()}

                # Tally positions being sent away and received
                outgoing_by_pos: Dict[str, int] = defaultdict(int)
                for r in req_players:
                    outgoing_by_pos[POS_NORMALIZATION.get(r[2], "WR")] += 1

                incoming_by_pos: Dict[str, int] = defaultdict(int)
                for r in off_players:
                    incoming_by_pos[POS_NORMALIZATION.get(r[2], "WR")] += 1

                leaves_orphan_starter = False
                for pos, out_cnt in outgoing_by_pos.items():
                    req_start = roster_req.get(pos, 1)
                    in_cnt = incoming_by_pos.get(pos, 0)
                    net_remaining = current_counts.get(pos, 0) - out_cnt + in_cnt
                    if net_remaining < req_start:
                        leaves_orphan_starter = True
                        break

                val_ratio = off_val / max(req_val, 1.0)

                # A player moved to IR since the offer can't be traded (decision D2).
                cur.execute(
                    """
                    SELECT 1 FROM napper_rosters
                    WHERE league_id = %s AND week = %s AND slot = 'IR' AND player_id = ANY(%s)
                    LIMIT 1
                    """,
                    (league_id, week, list(off_ids) + list(req_ids)),
                )
                involves_ir = cur.fetchone() is not None

                # Stochastic acceptance probability based on value balance and bench safety
                if leaves_orphan_starter or involves_ir:
                    accept_prob = 0.0  # Automatic reject: cannot forfeit required starters or trade IR players
                elif 0.88 <= val_ratio <= 1.25:
                    accept_prob = 0.82  # Fair deal
                elif 0.72 <= val_ratio < 0.88:
                    accept_prob = 0.30  # Slight discount
                elif 1.25 < val_ratio <= 1.55:
                    accept_prob = 0.92  # High value acquisition for target
                else:
                    accept_prob = 0.04  # Lopsided offer

                accepted = random.random() < accept_prob

                if accepted:
                    # 1. Update trade status to ACCEPTED
                    cur.execute(
                        "UPDATE napper_trades SET status = 'ACCEPTED' WHERE id = %s",
                        (trade_id,),
                    )

                    # 2. Execute roster mutations: Swap players for all current and future weeks
                    cur.execute(
                        """
                        UPDATE napper_rosters
                        SET team_id = %s, slot = 'BN'
                        WHERE league_id = %s AND team_id = %s AND player_id = ANY(%s) AND week >= %s
                        """,
                        (targ_id, league_id, prop_id, off_ids, week),
                    )
                    cur.execute(
                        """
                        UPDATE napper_rosters
                        SET team_id = %s, slot = 'BN'
                        WHERE league_id = %s AND team_id = %s AND player_id = ANY(%s) AND week >= %s
                        """,
                        (prop_id, league_id, targ_id, req_ids, week),
                    )
                    # Traded players arrive on the bench; AI teams fill any starting slot the trade emptied.
                    for team in (prop_id, targ_id):
                        fill_empty_starter_slots(cur, league_id, team, week)

                    # 3. Post system alert in napper_chat
                    announcement_text = (
                        f"🚨 TRADE COMPLETED! {prop_name} has traded {off_names} "
                        f"to {targ_name} for {req_names}!"
                    )
                    card_payload = {
                        "type": "TRADE_ALERT",
                        "award_type": "TRADE_ALERT",
                        "title": "TRADE COMPLETED",
                        "subtitle": f"{prop_name} ⇄ {targ_name}",
                        "details": {
                            "trade_id": trade_id,
                            "offered": off_names,
                            "requested": req_names,
                            "proposer": prop_name,
                            "target": targ_name,
                        },
                    }
                    cur.execute(
                        """
                        INSERT INTO napper_chat (
                            league_id, sender_type, sender_name, sender_handle,
                            avatar_url, message_text, card_payload
                        ) VALUES (%s, 'SYSTEM_BOT', 'Napper Trade Bot', 'tradebot', 'https://api.dicebear.com/7.x/bottts/svg?seed=TradeBot', %s, %s)
                        """,
                        (league_id, announcement_text, json.dumps(card_payload)),
                    )

                    # 4. Contextual manager banter reaction
                    targ_persona_str = targ_persona or "ANALYTICS_NERD"
                    if targ_persona_str == "TRASH_TALKER":
                        reaction_text = (
                            f"Appreciate the donation @{prop_handle}! {off_names} goes straight into our starting lineup. "
                            f"Championship ring is getting sized."
                        )
                    elif targ_persona_str == "ANALYTICS_NERD":
                        reaction_text = (
                            f"Transaction verified. Our updated roster model shows an immediate expected value uptick. "
                            f"Good dealing @{prop_handle}."
                        )
                    elif targ_persona_str == "WAIVER_HAWK":
                        reaction_text = (
                            f"Locked and loaded! Love bringing in {off_names} before the playoff push. "
                            f"Pleasure doing business @{prop_handle}."
                        )
                    else:  # DIE_HARD
                        reaction_text = (
                            f"Thrilled to welcome {off_names} to our franchise! Real ballers who bring grit to this roster @{prop_handle}."
                        )

                    cur.execute(
                        """
                        INSERT INTO napper_chat (
                            league_id, sender_type, sender_name, sender_handle,
                            avatar_url, message_text
                        ) VALUES (%s, 'AI_MANAGER', %s, %s, %s, %s)
                        """,
                        (league_id, targ_name, targ_handle, targ_avatar, reaction_text),
                    )

                    processed_trades.append({
                        "trade_id": trade_id,
                        "status": "ACCEPTED",
                        "proposer_id": prop_id,
                        "target_id": targ_id,
                        "offered_player_ids": off_ids,
                        "requested_player_ids": req_ids,
                    })

                else:
                    # 1. Mark REJECTED
                    cur.execute(
                        "UPDATE napper_trades SET status = 'REJECTED' WHERE id = %s",
                        (trade_id,),
                    )

                    # 2. Contextual rejection banter
                    targ_persona_str = targ_persona or "ANALYTICS_NERD"
                    if leaves_orphan_starter:
                        decline_msg = (
                            f"Can't do it @{prop_handle}. Moving {req_names} leaves us with zero depth at that slot. "
                            f"Front office won't leave a starting position vacated."
                        )
                    elif targ_persona_str == "TRASH_TALKER":
                        decline_msg = (
                            f"Nice try @{prop_handle}, but you're not getting {req_names} for that package. "
                            f"Stop sending amateur hour proposals to my inbox."
                        )
                    elif targ_persona_str == "ANALYTICS_NERD":
                        decline_msg = (
                            f"Declined @{prop_handle}. Our algorithm projects negative expected value (-{round(req_val - off_val, 1)} pts). "
                            f"Value threshold not met."
                        )
                    elif targ_persona_str == "WAIVER_HAWK":
                        decline_msg = (
                            f"Passing on this one @{prop_handle}. Rather work the waiver wire tonight than part with {req_names}."
                        )
                    else:  # DIE_HARD
                        decline_msg = (
                            f"No deal @{prop_handle}. {req_names} is an untouchable cornerstone of this football team."
                        )

                    cur.execute(
                        """
                        INSERT INTO napper_chat (
                            league_id, sender_type, sender_name, sender_handle,
                            avatar_url, message_text
                        ) VALUES (%s, 'AI_MANAGER', %s, %s, %s, %s)
                        """,
                        (league_id, targ_name, targ_handle, targ_avatar, decline_msg),
                    )

                    processed_trades.append({
                        "trade_id": trade_id,
                        "status": "REJECTED",
                        "proposer_id": prop_id,
                        "target_id": targ_id,
                        "offered_player_ids": off_ids,
                        "requested_player_ids": req_ids,
                    })

            _safe_commit(active_conn)

    return processed_trades


def main():
    parser = argparse.ArgumentParser(description="Napper Proactive Trade Engine")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--league", type=int, help="League ID")
    target.add_argument("--all-active", action="store_true", help="Every IN_SEASON league of the current season")
    parser.add_argument("--week", type=int, help="Week number (default: league_state current week)")
    parser.add_argument(
        "--process-ai",
        action="store_true",
        help="Process pending offers to AI teams instead of generating proposals",
    )
    args = parser.parse_args()

    from engines.napper_lineup_engine import active_league_ids
    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            week = args.week
            if week is None:
                cur.execute("SELECT current_week FROM league_state WHERE id = 1")
                week = int(cur.fetchone()[0] or 1)
            leagues = active_league_ids(cur) if args.all_active else [args.league]

    for league in leagues:
        if args.process_ai:
            print(f"Processing offers to AI teams for League {league}, Week {week}...")
            results = process_ai_to_ai_trades(league, week)
            print(f"Processed {len(results)} pending trades: {results}")
        else:
            print(f"Generating trade proposals for League {league}, Week {week}...")
            proposals = generate_trade_proposals(league, week)
            print(f"Generated {len(proposals)} proposals: {proposals}")


if __name__ == "__main__":
    main()
