"""Injury status for Napper rosters (docs/done/NAPPER_WEEKLY_IMPLEMENTATION_PLAN.md, Phases 2–4).

A player's status in week w comes from his latest `injuries` row in weeks <= w (the weekly Madden injury
report), with weeks_remaining counted down by the weeks since that report. This matches
`engines/fantasy_projection_engine.get_player_injury`, so a player shown as out is also projected at 0:

- IR: the report flags him on injured reserve (`is_ir`); he stays IR until a later report says otherwise.
- O (out): not on IR but weeks_remaining > 0 after the countdown.
- healthy: neither (no entry in the map).

IR-slot eligibility (decision D1): on IR, or out for at least IR_MIN_WEEKS more weeks. A one-week
injury is benched, not IR'd.
"""

from typing import Dict, Iterable, Optional

IR_MIN_WEEKS = 2


def injury_status(info: Optional[dict]) -> Optional[str]:
    """'IR', 'O' or None for an entry of get_injury_map (or None)."""
    if not info:
        return None
    if info["is_ir"]:
        return "IR"
    return "O" if info["weeks_remaining"] > 0 else None


def is_ir_eligible(info: Optional[dict]) -> bool:
    return bool(info) and (info["is_ir"] or info["weeks_remaining"] >= IR_MIN_WEEKS)


def get_injury_map(cur, season: int, week: int, player_ids: Optional[Iterable[int]] = None) -> Dict[int, dict]:
    """{player_id: {status, injury_type, duration, weeks_remaining, is_ir, reported_week}} for every player
    injured in `week` (optionally only `player_ids`). Healthy players are absent."""
    from psycopg.rows import tuple_row

    ids = list(player_ids) if player_ids is not None else None
    if ids is not None and not ids:
        return {}
    with cur.connection.cursor(row_factory=tuple_row) as c:
        c.execute(f"""
            SELECT DISTINCT ON (player_id) player_id, week, injury_type, duration, weeks_remaining, is_ir
            FROM injuries
            WHERE season = %s AND week <= %s AND player_id IS NOT NULL
              {"AND player_id = ANY(%s)" if ids is not None else ""}
            ORDER BY player_id, week DESC
        """, (season, week, ids) if ids is not None else (season, week))
        rows = c.fetchall()

    out = {}
    for pid, reported_week, injury_type, duration, weeks_remaining, is_ir in rows:
        remaining = max(0, int(weeks_remaining or 0) - (week - reported_week))
        info = {
            "injury_type": injury_type,
            "duration": duration,
            "weeks_remaining": remaining,
            "is_ir": bool(is_ir),
            "reported_week": reported_week,
        }
        info["status"] = injury_status(info)
        if info["status"]:
            out[pid] = info
    return out
