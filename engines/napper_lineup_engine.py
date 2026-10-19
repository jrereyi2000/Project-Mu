"""Weekly lineups for AI Napper managers (docs/NAPPER_WEEKLY_IMPLEMENTATION_PLAN.md, Phase 4).

For each AI team and week, before that week's games:

1. IR pass. Players who are IR-eligible (engines/napper_injuries.is_ir_eligible: on injured reserve or
   out IR_MIN_WEEKS+ weeks) fill the league's IR slots, best rest-of-season value first. Players in IR who
   are no longer eligible come back to the bench.
2. Roster limit. If the active roster (everyone not in IR) is larger than the starting slots plus BN,
   the lowest-ROS bench players are dropped (decision D3), never one the starting lineup needs.
3. Optional free-agent pickup (--fill-ir-spots): each player moved to IR is replaced by the best free agent
   at his position by ROS value, while the active roster has room.
4. Starter pass. QB, RB, WR, TE, K, DEF, then FLEX, then SUPERFLEX are filled greedily with the highest
   projected_points among players who can play (not out, not on bye); a slot no one available can fill
   takes the best unavailable player rather than staying empty. Everyone else is benched.

The lineup is written to the target week and every later saved week (the weekly run re-optimizes each week
before it is played), matchup projections for the week are refreshed, and IR moves, drops and pickups are
posted to the league chat. Human teams are never changed (decision D5).

CLI:
    python3 -m engines.napper_lineup_engine --league 15                 # current week from league_state
    python3 -m engines.napper_lineup_engine --all-active --week 5
    python3 -m engines.napper_lineup_engine --all-active --fill-ir-spots
    python3 -m engines.napper_lineup_engine --league 15 --dry-run       # print moves, write nothing
"""

import argparse
import json
import os
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import psycopg
from psycopg.rows import tuple_row

from engines.napper_injuries import get_injury_map, is_ir_eligible

DATABASE_URL = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")

# Starting slots in fill order (fixed positions first, then the wider flex slots) and who may fill each.
STARTER_SLOT_ELIGIBILITY = {
    "QB": {"QB"}, "RB": {"RB"}, "WR": {"WR"}, "TE": {"TE"}, "K": {"K"}, "DEF": {"DEF"},
    "FLEX": {"RB", "WR", "TE"}, "SUPERFLEX": {"QB", "RB", "WR", "TE"},
}
DEFAULT_ROSTER_FORMAT = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "SUPERFLEX": 0, "K": 1, "DEF": 1,
                         "BN": 6, "IR": 1}
POS_GROUP = {"QB": "QB", "HB": "RB", "FB": "RB", "RB": "RB", "WR": "WR", "TE": "TE", "K": "K", "DEF": "DEF"}


def _roster_format(fmt: Any) -> Dict[str, int]:
    if isinstance(fmt, str):
        fmt = json.loads(fmt)
    return {k: int(v) for k, v in (fmt or DEFAULT_ROSTER_FORMAT).items()}


def _starter_slots(fmt: Dict[str, int]) -> List[str]:
    return [slot for slot in STARTER_SLOT_ELIGIBILITY for _ in range(fmt.get(slot, 0))]


def _can_play(p: dict) -> bool:
    return not p.get("bye") and not (p.get("injury") or {}).get("status")


# ─────────────────────────────── pure planning (no database) ───────────────────────────────

def plan_ir(players: List[dict], fmt: Dict[str, int]) -> Tuple[List[int], List[int], List[int]]:
    """IR pass and roster limit for one team-week. players: dicts with pid, pos (QB/RB/WR/TE/K/DEF), slot,
    proj, ros, bye, injury (get_injury_map entry or None). Returns (to_ir, off_ir, drops) player ids."""
    ir_cap = fmt.get("IR", 0)
    in_ir = [p for p in players if p["slot"] == "IR"]
    off_ir = [p["pid"] for p in in_ir if not is_ir_eligible(p.get("injury"))]
    staying = len(in_ir) - len(off_ir)
    candidates = sorted((p for p in players if p["slot"] != "IR" and is_ir_eligible(p.get("injury"))),
                        key=lambda p: (-p["ros"], p["pid"]))
    to_ir = [p["pid"] for p in candidates[:max(0, ir_cap - staying)]]

    in_ir_after = ({p["pid"] for p in in_ir} - set(off_ir)) | set(to_ir)
    active = [p for p in players if p["pid"] not in in_ir_after]
    capacity = len(_starter_slots(fmt)) + fmt.get("BN", 0)
    drops: List[int] = []
    if len(active) > capacity:
        needed = {p["pid"] for p in _pick_starters(active, fmt).values() if p}
        for p in sorted((p for p in active if p["pid"] not in needed), key=lambda p: (p["ros"], -p["pid"])):
            if len(active) - len(drops) <= capacity:
                break
            drops.append(p["pid"])
    return to_ir, off_ir, drops


def _pick_starters(players: List[dict], fmt: Dict[str, int]) -> Dict[int, Optional[dict]]:
    """{slot index: player} for the starting slots, greedy by availability then projection."""
    pool = sorted(players, key=lambda p: (not _can_play(p), -p["proj"], -p["ros"], p["pid"]))
    picks: Dict[int, Optional[dict]] = {}
    for i, slot in enumerate(_starter_slots(fmt)):
        eligible = STARTER_SLOT_ELIGIBILITY[slot]
        pick = next((p for p in pool if p["pos"] in eligible), None)
        if pick is not None:
            pool.remove(pick)
        picks[i] = pick
    return picks


def plan_starters(players: List[dict], fmt: Dict[str, int]) -> Dict[int, str]:
    """{pid: slot} for every non-IR player: starting slots filled greedily, everyone else BN."""
    slots = _starter_slots(fmt)
    out = {p["pid"]: "BN" for p in players}
    for i, p in _pick_starters(players, fmt).items():
        if p is not None:
            out[p["pid"]] = slots[i]
    return out


def plan_lineup(players: List[dict], fmt: Dict[str, int]) -> dict:
    """Full plan without free-agent pickups: {slots: {pid: slot}, to_ir, off_ir, drops}."""
    to_ir, off_ir, drops = plan_ir(players, fmt)
    ir_now = ({p["pid"] for p in players if p["slot"] == "IR"} - set(off_ir)) | set(to_ir)
    active = [p for p in players if p["pid"] not in ir_now and p["pid"] not in drops]
    slots = plan_starters(active, fmt)
    slots.update({pid: "IR" for pid in ir_now})
    return {"slots": slots, "to_ir": to_ir, "off_ir": off_ir, "drops": drops}


# ─────────────────────────────── database layer ───────────────────────────────

def _load_team_week(c, league_id: int, team_id: int, season: int, week: int) -> List[dict]:
    c.execute("""
        SELECT nr.player_id, nr.slot, COALESCE(p.player_name, p.full_name), pos.position, pos.team_id,
               COALESCE(fp.projected_points, 0)::float, COALESCE(fp.ros_total_points, 0)::float,
               EXISTS (SELECT 1 FROM games g WHERE g.season = %s AND g.week = %s
                       AND pos.team_id IN (g.home_team_id, g.away_team_id)) AS has_game
        FROM napper_rosters nr
        JOIN players p ON p.id = nr.player_id
        LEFT JOIN LATERAL (
            SELECT ps.position, ps.team_id FROM player_seasons ps
            WHERE ps.player_id = nr.player_id AND ps.season = %s
            ORDER BY ps.team_id IS NULL LIMIT 1
        ) pos ON TRUE
        LEFT JOIN fantasy_projections fp ON fp.player_id = nr.player_id AND fp.season = %s AND fp.week = %s
        WHERE nr.league_id = %s AND nr.team_id = %s AND nr.week = %s
    """, (season, week, season, season, week, league_id, team_id, week))
    rows = c.fetchall()
    injuries = get_injury_map(c, season, week, [r[0] for r in rows])
    return [{
        "pid": pid, "slot": slot, "name": name, "pos": POS_GROUP.get(pos or ""), "proj": proj, "ros": ros,
        "bye": not has_game, "injury": injuries.get(pid),
    } for pid, slot, name, pos, _team, proj, ros, has_game in rows]


def _best_free_agent(c, league_id: int, season: int, week: int, group: str) -> Optional[Tuple[int, str]]:
    positions = [p for p, g in POS_GROUP.items() if g == group]
    c.execute("""
        SELECT ps.player_id, COALESCE(p.player_name, p.full_name)
        FROM player_seasons ps
        JOIN players p ON p.id = ps.player_id
        JOIN fantasy_projections fp ON fp.player_id = ps.player_id AND fp.season = ps.season AND fp.week = %s
        WHERE ps.season = %s AND ps.team_id IS NOT NULL AND ps.position = ANY(%s)
          AND NOT EXISTS (SELECT 1 FROM napper_rosters nr
                          WHERE nr.league_id = %s AND nr.week = %s AND nr.player_id = ps.player_id)
          AND fp.projected_points > 0
        ORDER BY fp.ros_total_points DESC NULLS LAST, ps.player_id
        LIMIT 1
    """, (week, season, positions, league_id, week))
    row = c.fetchone()
    return (row[0], row[1]) if row else None


def _post_chat(c, league_id: int, text: str) -> None:
    c.execute("""
        INSERT INTO napper_chat (league_id, sender_type, sender_name, sender_handle, avatar_url, message_text)
        VALUES (%s, 'SYSTEM_BOT', 'Napper Lineup Bot', '@NapperLineups', '/static/players/robot_avatar.webp', %s)
    """, (league_id, text))


def refresh_matchup_projections(cur, league_id: int, season: int, week: int) -> None:
    """Re-sums each team's starters' projected_points (not BN/IR) into the week's matchups and win odds."""
    with cur.connection.cursor(row_factory=tuple_row) as c:
        c.execute("""
            SELECT nr.team_id, COALESCE(SUM(fp.projected_points), 0)::float
            FROM napper_rosters nr
            LEFT JOIN fantasy_projections fp ON fp.player_id = nr.player_id AND fp.season = %s AND fp.week = %s
            WHERE nr.league_id = %s AND nr.week = %s AND nr.slot NOT IN ('BN', 'IR')
            GROUP BY nr.team_id
        """, (season, week, league_id, week))
        proj = dict(c.fetchall())
        c.execute("SELECT id, home_team_id, away_team_id FROM napper_matchups WHERE league_id = %s AND week = %s",
                  (league_id, week))
        for mid, home, away in c.fetchall():
            h, a = proj.get(home, 0.0), proj.get(away, 0.0)
            c.execute("UPDATE napper_matchups SET home_proj = %s, away_proj = %s, win_prob_home = %s WHERE id = %s",
                      (h, a, round(h / (h + a), 3) if h + a > 0 else 0.5, mid))


def optimize_team_lineup(cur, league_id: int, team_id: int, week: int, fill_ir_spots: bool = False,
                         post_chat: bool = True) -> dict:
    """Plans and writes one AI team's lineup for `week` (and later saved weeks). Returns what changed:
    {team, moves: [(pid, old, new)], to_ir, off_ir, drops, adds} (names for IR/drop/add lists)."""
    with cur.connection.cursor(row_factory=tuple_row) as c:
        c.execute("""
            SELECT t.is_ai, t.team_name, l.season, l.roster_format
            FROM napper_teams t JOIN napper_leagues l ON l.id = t.league_id
            WHERE t.id = %s AND t.league_id = %s
        """, (team_id, league_id))
        row = c.fetchone()
        if not row or not row[0]:
            return {}
        _, team_name, season, fmt = row
        fmt = _roster_format(fmt)
        players = _load_team_week(c, league_id, team_id, season, week)
        if not players:
            return {}
        by_pid = {p["pid"]: p for p in players}
        names = {p["pid"]: p["name"] for p in players}

        to_ir, off_ir, drops = plan_ir(players, fmt)
        for pid in drops:
            c.execute("DELETE FROM napper_rosters WHERE league_id = %s AND team_id = %s AND week >= %s AND player_id = %s",
                      (league_id, team_id, week, pid))
        adds: List[str] = []
        if fill_ir_spots and to_ir:
            ir_now = ({p["pid"] for p in players if p["slot"] == "IR"} - set(off_ir)) | set(to_ir)
            capacity = len(_starter_slots(fmt)) + fmt.get("BN", 0)
            active = [p for p in players if p["pid"] not in ir_now and p["pid"] not in drops]
            for pid in to_ir:
                if len(active) >= capacity:
                    break
                fa = _best_free_agent(c, league_id, season, week, by_pid[pid]["pos"])
                if not fa:
                    continue
                c.execute("SELECT DISTINCT week FROM napper_rosters WHERE league_id = %s AND team_id = %s AND week >= %s",
                          (league_id, team_id, week))
                for (w,) in c.fetchall():
                    c.execute("""
                        INSERT INTO napper_rosters (league_id, team_id, week, player_id, slot)
                        VALUES (%s, %s, %s, %s, 'BN') ON CONFLICT (team_id, week, player_id) DO NOTHING
                    """, (league_id, team_id, w, fa[0]))
                adds.append(fa[1])
                players = _load_team_week(c, league_id, team_id, season, week)
                by_pid = {p["pid"]: p for p in players}
                active = [p for p in players if p["pid"] not in ir_now]
            players = [p for p in players if p["pid"] not in drops]

        plan = plan_lineup([dict(p, slot="IR" if p["pid"] in to_ir else p["slot"]) for p in players
                            if p["pid"] not in drops], fmt)
        moves = []
        for pid, new_slot in plan["slots"].items():
            old = by_pid[pid]["slot"] if pid in by_pid else None
            if old != new_slot:
                moves.append((pid, old, new_slot))
            c.execute("UPDATE napper_rosters SET slot = %s WHERE league_id = %s AND team_id = %s AND week >= %s AND player_id = %s",
                      (new_slot, league_id, team_id, week, pid))

        name = lambda pid: names.get(pid, str(pid))
        dropped_names = [name(pid) for pid in drops]
        result = {
            "team": team_name, "moves": moves, "to_ir": [name(p) for p in to_ir],
            "off_ir": [name(p) for p in off_ir], "drops": dropped_names, "adds": adds,
        }
        if post_chat:
            notes = []
            if result["to_ir"]:
                notes.append(f"moves {', '.join(result['to_ir'])} to IR")
            if result["off_ir"]:
                notes.append(f"activates {', '.join(result['off_ir'])} from IR")
            if adds:
                notes.append(f"adds {', '.join(adds)}")
            if dropped_names:
                notes.append(f"drops {', '.join(dropped_names)}")
            if notes:
                _post_chat(c, league_id, f"📋 {team_name} {'; '.join(notes)} (week {week}).")
    return result


def _ensure_week_rosters(c, league_id: int, week: int) -> None:
    """Carry the latest earlier week's rosters forward when `week` has none yet (as the API does)."""
    c.execute("SELECT 1 FROM napper_rosters WHERE league_id = %s AND week = %s LIMIT 1", (league_id, week))
    if c.fetchone():
        return
    c.execute("SELECT MAX(week) FROM napper_rosters WHERE league_id = %s AND week < %s", (league_id, week))
    src = c.fetchone()[0]
    if src is not None:
        c.execute("""
            INSERT INTO napper_rosters (league_id, team_id, week, player_id, slot)
            SELECT league_id, team_id, %s, player_id, slot FROM napper_rosters
            WHERE league_id = %s AND week = %s
            ON CONFLICT (team_id, week, player_id) DO NOTHING
        """, (week, league_id, src))


def optimize_league_lineups(cur, league_id: int, week: int, fill_ir_spots: bool = False,
                            post_chat: bool = True) -> List[dict]:
    """Optimizes every AI team in the league for `week`; returns each team's result."""
    with cur.connection.cursor(row_factory=tuple_row) as c:
        _ensure_week_rosters(c, league_id, week)
        c.execute("SELECT id FROM napper_teams WHERE league_id = %s AND is_ai ORDER BY id", (league_id,))
        team_ids = [r[0] for r in c.fetchall()]
        c.execute("SELECT season FROM napper_leagues WHERE id = %s", (league_id,))
        season = c.fetchone()[0]
    results = [r for r in (optimize_team_lineup(cur, league_id, t, week, fill_ir_spots, post_chat)
                           for t in team_ids) if r]
    refresh_matchup_projections(cur, league_id, season, week)
    return results


def fill_empty_starter_slots(cur, league_id: int, team_id: int, from_week: int) -> List[Tuple[int, int, str]]:
    """Moves an AI team's best eligible bench players into starting slots left empty (e.g. by a trade, whose
    players arrive on the bench) for every saved week >= from_week. Best = that week's projected_points, then
    ros_total_points. Human teams are never changed. Returns the (week, player_id, slot) moves made."""
    moves: List[Tuple[int, int, str]] = []
    with cur.connection.cursor(row_factory=tuple_row) as c:
        c.execute("""
            SELECT t.is_ai, l.season, l.roster_format
            FROM napper_teams t JOIN napper_leagues l ON l.id = t.league_id
            WHERE t.id = %s AND t.league_id = %s
        """, (team_id, league_id))
        row = c.fetchone()
        if not row or not row[0]:
            return moves
        season, fmt = row[1], _roster_format(row[2])

        c.execute("""
            SELECT nr.week, nr.player_id, nr.slot, pos.position,
                   COALESCE(fp.projected_points, 0)::float, COALESCE(fp.ros_total_points, 0)::float
            FROM napper_rosters nr
            LEFT JOIN LATERAL (
                SELECT ps.position FROM player_seasons ps
                WHERE ps.player_id = nr.player_id AND ps.season = %s
                ORDER BY ps.team_id IS NULL LIMIT 1
            ) pos ON TRUE
            LEFT JOIN fantasy_projections fp
              ON fp.player_id = nr.player_id AND fp.season = %s AND fp.week = nr.week
            WHERE nr.league_id = %s AND nr.team_id = %s AND nr.week >= %s
        """, (season, season, league_id, team_id, from_week))
        by_week: Dict[int, list] = defaultdict(list)
        for week, pid, slot, pos, proj, ros in c.fetchall():
            by_week[week].append((pid, slot, POS_GROUP.get(pos or ""), proj, ros))

        for week in sorted(by_week):
            rows = by_week[week]
            filled: Dict[str, int] = defaultdict(int)
            for _, slot, _, _, _ in rows:
                filled[slot] += 1
            bench = sorted((r for r in rows if r[1] == "BN"), key=lambda r: (-r[3], -r[4], r[0]))
            for slot, eligible in STARTER_SLOT_ELIGIBILITY.items():
                for _ in range(fmt.get(slot, 0) - filled[slot]):
                    pick = next((r for r in bench if r[2] in eligible), None)
                    if pick is None:
                        break
                    bench.remove(pick)
                    filled[slot] += 1
                    c.execute("""
                        UPDATE napper_rosters SET slot = %s
                        WHERE league_id = %s AND team_id = %s AND week = %s AND player_id = %s
                    """, (slot, league_id, team_id, week, pick[0]))
                    moves.append((week, pick[0], slot))
    return moves


def active_league_ids(cur) -> List[int]:
    with cur.connection.cursor(row_factory=tuple_row) as c:
        c.execute("""
            SELECT l.id FROM napper_leagues l
            WHERE l.status = 'IN_SEASON' AND l.season = (SELECT current_season FROM league_state WHERE id = 1)
            ORDER BY l.id
        """)
        return [r[0] for r in c.fetchall()]


def main():
    parser = argparse.ArgumentParser(description="Weekly AI lineups for Napper leagues")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--league", type=int, help="League id")
    target.add_argument("--all-active", action="store_true", help="Every IN_SEASON league of the current season")
    parser.add_argument("--week", type=int, help="Week to set (default: league_state current week)")
    parser.add_argument("--fill-ir-spots", action="store_true", help="Replace players moved to IR with free agents")
    parser.add_argument("--dry-run", action="store_true", help="Print the moves and roll back")
    args = parser.parse_args()

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            week = args.week
            if week is None:
                cur.execute("SELECT current_week FROM league_state WHERE id = 1")
                week = int(cur.fetchone()[0] or 1)
            leagues = active_league_ids(cur) if args.all_active else [args.league]
            for league_id in leagues:
                results = optimize_league_lineups(cur, league_id, week, args.fill_ir_spots,
                                                  post_chat=not args.dry_run)
                print(f"League {league_id}, week {week}: {len(results)} AI teams")
                for r in results:
                    extra = "; ".join(f"{k}: {', '.join(r[k])}" for k in ("to_ir", "off_ir", "adds", "drops") if r[k])
                    print(f"   {r['team']}: {len(r['moves'])} slot changes" + (f" ({extra})" if extra else ""))
        if args.dry_run:
            conn.rollback()
            print("Dry run: rolled back.")
        else:
            conn.commit()


if __name__ == "__main__":
    main()
