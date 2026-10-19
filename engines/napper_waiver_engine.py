"""Weekly waiver run for Napper leagues (db/migrations/2026_12_05_napper_waivers.sql).

Free agency and waivers:
- A player dropped by any team goes on waivers (napper_waiver_wire) until the next run: he can only be
  claimed, not added instantly. Every other unrostered player is a free agent the user can add at once
  (POST /api/napper/league/{id}/waivers/claim). AI managers act only in the run.
- The user's claims on players on waivers wait as PENDING (napper_waiver_claims, in the user's rank order).

The run, once per league and week (the weekly step, engines/napper_weekly.py, before that week's games):
1. Priority: reverse standings (worst record first, then fewest points for), the same order every round.
2. Rounds. In each round every team in priority order gets one add:
   - the user's team: its next pending claim that can still go through; a claim whose player was taken is
     LOST, one whose drop is gone or that would put the active roster over the limit is INVALID;
   - an AI team: its next pickup, decided on its turn from what is left (ai_next_claim):
     NEED: a starting slot no rostered player can fill (e.g. the only QB is on IR) gets the best healthy free
           agent at that position by ROS value; always made, even past the weekly cap;
     STREAM: a starting slot whose only candidates can't play this week (bye or out) gets the free agent with
           the best projection this week who plays, if the player dropped for him has lower ROS value;
     UPGRADE: an open roster spot gets the best healthy free agent by ROS value, or the lowest-ROS spare is
           swapped for a free agent worth more than ros × SWAP_MARGIN[0] + SWAP_MARGIN[1]; only positions
           under their cap (upgrade_cap: QB = QB slots + 2 × SUPERFLEX slots, RB/WR 7, TE 2, K/DEF 1);
     STREAM and UPGRADE count against AI_ADDS_PER_WEEK (WAIVER_HAWK managers: WAIVER_HAWK_ADDS).
   Rounds repeat until no team adds anyone.
   A full active roster (starters + BN, IR not counted) drops its lowest-ROS spare: a player outside this
   week's best lineup and not added in this run (decision D3).
3. Adds land on the bench for this week and every later saved week. Players dropped in the run go on waivers
   until the next run, so they can't be claimed back in the same one. Unclaimed players whose waivers clear
   this week become free agents. Each add is posted to the league chat as a WAIVER_CLAIM card.

CLI:
    python3 -m engines.napper_waiver_engine --league 15 --week 4
    python3 -m engines.napper_waiver_engine --all-active --dry-run
"""

import argparse
import json
from collections import Counter, defaultdict
from typing import Dict, List, Optional

import psycopg
from psycopg.rows import dict_row, tuple_row

from engines.napper_injuries import get_injury_map
from engines.napper_lineup_engine import (
    DATABASE_URL, POS_GROUP, STARTER_SLOT_ELIGIBILITY, _can_play, _load_team_week, _pick_starters,
    _roster_format, _starter_slots, active_league_ids, add_player, release_player, roster_capacity,
    unfillable_slot_groups,
)

AI_ADDS_PER_WEEK = 2          # STREAM + UPGRADE adds per AI team per run (as fantasy_league_sim's 2 add/drops)
WAIVER_HAWK_ADDS = 3
SWAP_MARGIN = (1.15, 1.0)     # upgrade when the free agent's ROS > the drop's ROS × 1.15 + 1.0
# Most players of a position an AI roster collects through upgrades (streaming and NEED may go past it).
# QB: one per QB slot plus two per SUPERFLEX slot (upgrade_cap), so 1-QB teams don't hoard backups whose
# raw ROS points are high but who never start; a QB on bye or IR is covered by STREAM and NEED.
UPGRADE_CAP = {"RB": 7, "WR": 7, "TE": 2, "K": 1, "DEF": 1}


def upgrade_cap(pos: str, fmt: Dict[str, int]) -> int:
    if pos == "QB":
        return fmt.get("QB", 0) + 2 * fmt.get("SUPERFLEX", 0)
    return UPGRADE_CAP.get(pos, 0)
FANTASY_POSITIONS = ["QB", "HB", "FB", "RB", "WR", "TE", "K", "DEF"]


# ─────────────────────────────── pure planning (no database) ───────────────────────────────

def _healthy(p: dict) -> bool:
    return not (p.get("injury") or {}).get("status")


def lowest_spare(roster: List[dict], fmt: Dict[str, int], keep=()) -> Optional[dict]:
    """The player a full roster drops: lowest ROS among those outside this week's best lineup and not in keep."""
    needed = {p["pid"] for p in _pick_starters(roster, fmt).values() if p}
    spare = [p for p in roster if p["pid"] not in needed and p["pid"] not in keep]
    return min(spare, key=lambda p: (p["ros"], -p["pid"])) if spare else None


def ai_next_claim(roster: List[dict], pool: List[dict], fmt: Dict[str, int], adds_left: int,
                  keep=()) -> Optional[dict]:
    """An AI team's next pickup. roster: its active (non-IR) players; pool: claimable players (dicts with pid,
    pos, proj, ros, bye, injury). Returns {add, drop (pid or None), reason} or None."""
    full = len(roster) >= roster_capacity(fmt)

    def with_drop(add: dict, reason: str, must_beat: Optional[float] = None) -> Optional[dict]:
        drop = lowest_spare(roster, fmt, keep) if full else None
        if full and drop is None:
            return None
        if drop is not None and must_beat is not None and not must_beat > drop["ros"]:
            return None
        return {"add": add["pid"], "drop": drop["pid"] if drop else None, "reason": reason}

    # NEED: a starting slot nobody on the roster can fill.
    for group in unfillable_slot_groups(roster, fmt):
        cands = [p for p in pool if p["pos"] == group and _healthy(p)]
        if cands:
            best = min(cands, key=lambda p: (not _can_play(p), -p["ros"], p["pid"]))
            claim = with_drop(best, "NEED")
            if claim:
                return claim
    if adds_left <= 0:
        return None

    # STREAM: a starting slot whose only candidates can't play this week.
    slots = _starter_slots(fmt)
    for i, pick in _pick_starters(roster, fmt).items():
        if pick is None or _can_play(pick):
            continue
        cands = [p for p in pool if p["pos"] in STARTER_SLOT_ELIGIBILITY[slots[i]] and _can_play(p) and p["proj"] > 0]
        if cands:
            best = max(cands, key=lambda p: (p["proj"], p["ros"], -p["pid"]))
            claim = with_drop(best, "STREAM", must_beat=best["ros"])
            if claim:
                return claim

    # UPGRADE: an open spot, or a clearly better player than the lowest-ROS spare.
    counts = Counter(p["pos"] for p in roster)
    drop = lowest_spare(roster, fmt, keep) if full else None
    if full and drop is None:
        return None
    room = lambda pos: counts[pos] - (1 if drop is not None and drop["pos"] == pos else 0) < upgrade_cap(pos, fmt)
    cands = [p for p in pool if _healthy(p) and room(p["pos"]) and p["ros"] > 0]
    if not cands:
        return None
    best = max(cands, key=lambda p: (p["ros"], p["proj"], -p["pid"]))
    if drop is not None and best["ros"] <= drop["ros"] * SWAP_MARGIN[0] + SWAP_MARGIN[1]:
        return None
    return {"add": best["pid"], "drop": drop["pid"] if drop else None, "reason": "UPGRADE"}


def resolve_waivers(order: List[int], rosters: Dict[int, List[dict]], pool: Dict[int, dict],
                    fmt: Dict[str, int], ai_teams: Dict[int, int], human_claims: Dict[int, List[dict]],
                    max_rounds: int = 20) -> dict:
    """Runs the claim rounds in memory. order: team ids in priority order; rosters: {team: active players};
    pool: {pid: claimable player}; ai_teams: {team: STREAM/UPGRADE adds allowed}; human_claims: {team:
    [{id, add, drop}] in rank order}. Mutates rosters/pool. Returns {transactions: [{team, add, drop, reason,
    claim_id}], outcomes: {claim_id: (status, note)}}."""
    cap = roster_capacity(fmt)
    transactions: List[dict] = []
    outcomes: Dict[int, tuple] = {}
    taken_by: Dict[int, int] = {}
    added = defaultdict(set)
    adds_used = Counter()
    pending = {t: list(cs) for t, cs in human_claims.items()}

    def apply(team: int, add: int, drop: Optional[int], reason: str, claim_id=None):
        roster = rosters[team]
        if drop is not None:           # dropped players stay out of the pool until the next run
            roster.remove(next(p for p in roster if p["pid"] == drop))
        roster.append(dict(pool.pop(add), slot="BN"))
        taken_by[add] = team
        added[team].add(add)
        transactions.append({"team": team, "add": add, "drop": drop, "reason": reason, "claim_id": claim_id})

    for _ in range(max_rounds):
        progress = False
        for team in order:
            roster = rosters.get(team)
            if roster is None:
                continue
            if team in ai_teams:
                claim = ai_next_claim(roster, list(pool.values()), fmt, ai_teams[team] - adds_used[team],
                                      keep=added[team])
                if claim:
                    if claim["reason"] != "NEED":
                        adds_used[team] += 1
                    apply(team, claim["add"], claim["drop"], claim["reason"])
                    progress = True
                continue
            while pending.get(team):
                c = pending[team].pop(0)
                on_roster = {p["pid"] for p in roster}
                if c["add"] not in pool:
                    outcomes[c["id"]] = ("LOST", "claimed by another team" if c["add"] in taken_by
                                         else "no longer available")
                elif c.get("drop") is not None and c["drop"] not in on_roster:
                    outcomes[c["id"]] = ("INVALID", "the player to drop is no longer on the roster")
                elif len(roster) - (1 if c.get("drop") is not None else 0) + 1 > cap:
                    outcomes[c["id"]] = ("INVALID", f"roster full ({cap} active players): choose a player to drop")
                else:
                    outcomes[c["id"]] = ("WON", None)
                    apply(team, c["add"], c.get("drop"), "CLAIM", c["id"])
                    progress = True
                    break
        if not progress:
            break
    return {"transactions": transactions, "outcomes": outcomes}


# ─────────────────────────────── database layer ───────────────────────────────

def priority_order(cur, league_id: int, season: int, scoring_type: str) -> List[int]:
    """Team ids in waiver order: reverse standings (server/napper_api._get_league_standings)."""
    from server.napper_api import _get_league_standings
    with cur.connection.cursor(row_factory=dict_row) as dc:
        standings = _get_league_standings(dc, league_id, season, scoring_type)
    return [t["id"] for t in reversed(standings)]


def next_run_week(c, league_id: int) -> int:
    """The week of the league's next waiver run: one after the last run, else league_state's current week."""
    c.execute("SELECT MAX(week) FROM napper_waiver_runs WHERE league_id = %s", (league_id,))
    last = c.fetchone()[0]
    if last is not None:
        return int(last) + 1
    c.execute("SELECT current_week FROM league_state WHERE id = 1")
    row = c.fetchone()
    return max(1, int((row and row[0]) or 1))


def _load_pool(c, league_id: int, season: int, week: int) -> Dict[int, dict]:
    """Claimable players for the run: unrostered, on an NFL team, at a fantasy position, and not on waivers
    past this week."""
    c.execute("""
        SELECT ps.player_id, COALESCE(p.player_name, p.full_name), ps.position,
               COALESCE(fp.projected_points, 0)::float, COALESCE(fp.ros_total_points, 0)::float,
               EXISTS (SELECT 1 FROM games g WHERE g.season = %s AND g.week = %s
                       AND ps.team_id IN (g.home_team_id, g.away_team_id)) AS has_game
        FROM player_seasons ps
        JOIN players p ON p.id = ps.player_id
        LEFT JOIN fantasy_projections fp ON fp.player_id = ps.player_id AND fp.season = %s AND fp.week = %s
        WHERE ps.season = %s AND ps.team_id IS NOT NULL AND ps.position = ANY(%s)
          AND NOT EXISTS (SELECT 1 FROM napper_rosters nr
                          WHERE nr.league_id = %s AND nr.week = %s AND nr.player_id = ps.player_id)
          AND NOT EXISTS (SELECT 1 FROM napper_waiver_wire ww
                          WHERE ww.league_id = %s AND ww.player_id = ps.player_id AND ww.clears_week > %s)
    """, (season, week, season, week, season, FANTASY_POSITIONS, league_id, week, league_id, week))
    rows = c.fetchall()
    injuries = get_injury_map(c, season, week, [r[0] for r in rows])
    pool = {}
    for pid, name, pos, proj, ros, has_game in rows:
        if pid not in pool:
            pool[pid] = {"pid": pid, "name": name, "pos": POS_GROUP.get(pos), "proj": proj, "ros": ros,
                         "bye": not has_game, "injury": injuries.get(pid)}
    return pool


def _post_claim(c, league_id: int, team_name: str, add_name: str, drop_name: Optional[str], reason: str) -> None:
    how = "claimed" if reason == "CLAIM" else "picked up"
    text = f"⚡ Waivers: {team_name} {how} {add_name}" + (f" and dropped {drop_name}." if drop_name else ".")
    card = {"type": "WAIVER_CLAIM", "team": team_name, "added_player": add_name, "dropped_player": drop_name,
            "reason": reason}
    c.execute("""
        INSERT INTO napper_chat (league_id, sender_type, sender_name, sender_handle, avatar_url, message_text, card_payload)
        VALUES (%s, 'SYSTEM_BOT', 'Waiver Bot', '@NapperWaivers', '/static/players/robot_avatar.webp', %s, %s)
    """, (league_id, text, json.dumps(card)))


def run_waivers(cur, league_id: int, week: int, post_chat: bool = True) -> dict:
    """The league's waiver run for `week` (once; a re-run returns {skipped: True}). Writes the adds and drops,
    the claim outcomes, the waiver wire and the run row; the caller commits."""
    with cur.connection.cursor(row_factory=tuple_row) as c:
        c.execute("SELECT 1 FROM napper_waiver_runs WHERE league_id = %s AND week = %s", (league_id, week))
        if c.fetchone():
            return {"league": league_id, "week": week, "skipped": True, "transactions": [], "outcomes": {}}
        c.execute("SELECT season, roster_format, scoring_type FROM napper_leagues WHERE id = %s", (league_id,))
        season, fmt, scoring_type = c.fetchone()
        fmt = _roster_format(fmt)
        c.execute("SELECT id, team_name, is_ai, persona FROM napper_teams WHERE league_id = %s", (league_id,))
        teams = {tid: (name, is_ai, persona) for tid, name, is_ai, persona in c.fetchall()}

        order = priority_order(cur, league_id, season, scoring_type)
        rosters = {t: [p for p in _load_team_week(c, league_id, t, season, week) if p["slot"] != "IR"]
                   for t in teams}
        pool = _load_pool(c, league_id, season, week)
        names = {p["pid"]: p["name"] for r in rosters.values() for p in r}
        names.update({pid: p["name"] for pid, p in pool.items()})

        c.execute("""
            SELECT id, team_id, add_player_id, drop_player_id FROM napper_waiver_claims
            WHERE league_id = %s AND status = 'PENDING' ORDER BY team_id, rank, id
        """, (league_id,))
        human_claims: Dict[int, List[dict]] = defaultdict(list)
        for cid, tid, add, drop in c.fetchall():
            human_claims[tid].append({"id": cid, "add": add, "drop": drop})
        ai_teams = {t: (WAIVER_HAWK_ADDS if persona == "WAIVER_HAWK" else AI_ADDS_PER_WEEK)
                    for t, (_, is_ai, persona) in teams.items() if is_ai}

        result = resolve_waivers(order, rosters, pool, fmt, ai_teams, human_claims)

        for t in result["transactions"]:
            if t["drop"] is not None:
                release_player(c, league_id, t["team"], t["drop"], week, week + 1)
            add_player(c, league_id, t["team"], t["add"], week)
            if t["claim_id"] is None:
                c.execute("""
                    INSERT INTO napper_waiver_claims (league_id, team_id, add_player_id, drop_player_id, status,
                                                      reason, week, processed_at)
                    VALUES (%s, %s, %s, %s, 'WON', %s, %s, NOW())
                """, (league_id, t["team"], t["add"], t["drop"], t["reason"], week))
            if post_chat:
                _post_claim(c, league_id, teams[t["team"]][0], names.get(t["add"], str(t["add"])),
                            names.get(t["drop"]) if t["drop"] is not None else None, t["reason"])
        for cid, (status, note) in result["outcomes"].items():
            c.execute("""
                UPDATE napper_waiver_claims SET status = %s, note = %s, reason = 'CLAIM', week = %s,
                       processed_at = NOW()
                WHERE id = %s
            """, (status, note, week, cid))
        c.execute("DELETE FROM napper_waiver_wire WHERE league_id = %s AND clears_week <= %s", (league_id, week))
        c.execute("INSERT INTO napper_waiver_runs (league_id, week, priority) VALUES (%s, %s, %s)",
                  (league_id, week, order))

    result.update({"league": league_id, "week": week, "skipped": False, "order": order,
                   "names": names, "team_names": {t: v[0] for t, v in teams.items()}})
    return result


def main():
    parser = argparse.ArgumentParser(description="Napper waiver run")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--league", type=int, action="append", help="League id (repeatable)")
    target.add_argument("--all-active", action="store_true", help="Every IN_SEASON league of the current season")
    parser.add_argument("--week", type=int, help="Default: league_state current week")
    parser.add_argument("--dry-run", action="store_true", help="Print the claims and roll back")
    args = parser.parse_args()

    with psycopg.connect(DATABASE_URL) as conn:
        with conn.cursor() as cur:
            week = args.week
            if week is None:
                cur.execute("SELECT current_week FROM league_state WHERE id = 1")
                week = int(cur.fetchone()[0] or 1)
            for league_id in (active_league_ids(cur) if args.all_active else args.league):
                r = run_waivers(cur, league_id, week, post_chat=not args.dry_run)
                if r["skipped"]:
                    print(f"League {league_id}, week {week}: waivers already run")
                    continue
                print(f"League {league_id}, week {week}: {len(r['transactions'])} adds")
                for t in r["transactions"]:
                    drop = f", drops {r['names'].get(t['drop'])}" if t["drop"] is not None else ""
                    print(f"   {r['team_names'][t['team']]}: {t['reason']} {r['names'].get(t['add'])}{drop}")
        if args.dry_run:
            conn.rollback()
            print("Dry run: rolled back.")
        else:
            conn.commit()


if __name__ == "__main__":
    main()
