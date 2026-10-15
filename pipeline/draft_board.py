"""The scouted draft board and the weekly mock draft in the database (draft_prospects, mock_draft_picks).

Writers: pipeline/load_capture.py (the Draft Board scan, and the roster load's rookie link) and
engines/mock_draft_engine.py (each week's Round 1 mock). Readers: the engine and the writer tools in
newsroom/db_tools.py. Every function takes an open cursor; the caller owns the transaction.
"""

import re
from typing import Dict, List, Optional

from pipeline.ingest_prospects import Prospect, height_inches, inches_height


def position_group(position: Optional[str]) -> Optional[str]:
    from engines.mock_draft_engine import POSITION_GROUP_MAP
    return POSITION_GROUP_MAP.get(position, position) if position else None


def _name_key(name: str) -> str:
    return re.sub(r"[^A-Z]", "", (name or "").upper())


def write_board(cur, draft_season: int, prospects: List[Prospect], source_capture: Optional[str] = None) -> int:
    """Replace a draft class's board with `prospects`. Its saved mock drafts go with it (they were drawn
    from the old board). Returns the rows written."""
    cur.execute("DELETE FROM draft_prospects WHERE draft_season = %s", (draft_season,))
    cur.executemany(
        """INSERT INTO draft_prospects (draft_season, projected_pick, projected_round, round_pick, full_name,
               position, college, height_in, weight, age, ovr, archetype, notes, source_capture)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        [(draft_season, p.projected_pick, p.projected_round, p.round_pick, p.full_name, p.position, p.college,
          height_inches(p.height), p.weight, p.age, p.overall_rating, p.archetype, p.notes, source_capture)
         for p in prospects])
    return len(prospects)


def read_board(cur, draft_season: int, position: str = "", limit: Optional[int] = None) -> List[Prospect]:
    """The class's board in pick order. `position` keeps one position or position group (EDGE, OT, LB...)."""
    cur.execute("""SELECT id, projected_pick, projected_round, round_pick, full_name, position, college,
                          height_in, weight, age, ovr, archetype, notes
                   FROM draft_prospects WHERE draft_season = %s ORDER BY projected_pick""", (draft_season,))
    board = [Prospect(projected_pick=pk, full_name=name, position=pos, college=col, height=inches_height(h),
                      weight=wt, age=age, overall_rating=ovr, archetype=arch, notes=notes,
                      projected_round=rnd, round_pick=rp, prospect_id=pid)
             for pid, pk, rnd, rp, name, pos, col, h, wt, age, ovr, arch, notes in cur.fetchall()]
    want = (position or "").strip().upper()
    if want:
        board = [p for p in board if p.position and (p.position.upper() == want or
                                                     (position_group(p.position) or "").upper() == want)]
    return board[:limit] if limit else board


def link_rookies(cur, season: int) -> Dict[str, int]:
    """Point each prospect of draft class `season` at the rookie he became (player_seasons with
    years_of_experience 0 in `season`), matched by full name and position group. A name that fits more
    than one rookie, or more than one prospect, is left unlinked."""
    cur.execute("SELECT id, full_name, position FROM draft_prospects WHERE draft_season = %s AND player_id IS NULL",
                (season,))
    prospects = cur.fetchall()
    if not prospects:
        return {"linked": 0, "unlinked": 0}
    cur.execute("""SELECT p.id, COALESCE(p.full_name, p.player_name), s.position
                   FROM players p JOIN player_seasons s ON s.player_id = p.id
                   WHERE s.season = %s AND s.years_of_experience = 0""", (season,))
    rookies: Dict[tuple, List[int]] = {}
    for pid, name, pos in cur.fetchall():
        rookies.setdefault((_name_key(name), position_group(pos)), []).append(pid)
    claimed: Dict[tuple, int] = {}
    for _, name, pos in prospects:
        key = (_name_key(name), position_group(pos))
        claimed[key] = claimed.get(key, 0) + 1
    linked = 0
    for draft_id, name, pos in prospects:
        key = (_name_key(name), position_group(pos))
        hits = rookies.get(key, [])
        if len(hits) == 1 and claimed[key] == 1:
            cur.execute("UPDATE draft_prospects SET player_id = %s WHERE id = %s", (hits[0], draft_id))
            linked += 1
    return {"linked": linked, "unlinked": len(prospects) - linked}


def save_mock(cur, season: int, week: int, selections: List[Dict]) -> int:
    """Store a Round 1 mock draft for (season, week), replacing that week's earlier run."""
    cur.execute("DELETE FROM mock_draft_picks WHERE season = %s AND week = %s", (season, week))
    cur.executemany(
        """INSERT INTO mock_draft_picks (season, week, pick, team_id, prospect_id, locked, selection_status,
               fit_score, rationale) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        [(season, week, s["pick"], s.get("team_id"), s["prospect"]["prospect_id"], bool(s.get("locked")),
          s.get("selection_status"), s.get("fit_score"), s.get("rationale")) for s in selections])
    return len(selections)


def read_mock(cur, season: int, week: int) -> List[Dict]:
    """A saved mock draft, in the shape MockDraftEngine.run_mock returns; [] when none is saved."""
    cur.execute("""SELECT m.pick, m.team_id, t.name, t.abbreviation, m.locked, m.selection_status, m.fit_score,
                          m.rationale, d.id, d.projected_pick, d.projected_round, d.round_pick, d.full_name,
                          d.position, d.college, d.height_in, d.weight, d.age, d.ovr, d.archetype, d.notes
                   FROM mock_draft_picks m
                   JOIN draft_prospects d ON d.id = m.prospect_id
                   LEFT JOIN teams t ON t.id = m.team_id
                   WHERE m.season = %s AND m.week = %s ORDER BY m.pick""", (season, week))
    out = []
    for (pick, team_id, team, abbr, locked, status, fit, rationale, pid, pk, rnd, rp, name, pos, col, h, wt, age,
         ovr, arch, notes) in cur.fetchall():
        prospect = Prospect(projected_pick=pk, full_name=name, position=pos, college=col, height=inches_height(h),
                            weight=wt, age=age, overall_rating=ovr, archetype=arch, notes=notes,
                            projected_round=rnd, round_pick=rp, prospect_id=pid)
        out.append({"pick": pick, "team_id": team_id, "team": team, "team_abbr": abbr, "locked": locked,
                    "selection_status": status, "prospect": prospect.to_dict(),
                    "fit_score": float(fit) if fit is not None else None, "rationale": rationale})
    return out
