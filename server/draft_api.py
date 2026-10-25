"""
Draft Central backend (/api/draft/*; docs/DRAFT_CENTRAL_PLAN.md, PROJECT_SPEC §2.10, §2.18, §4.6).

Serves the Consensus Big Board, Prospect Scouting Dossiers (cumulative college season stats, weekly game logs, combine
athletic percentiles, storyline timelines), College Honors & Awards, 32-Team Draft Needs & Capital Matrix, Media Mock
Draft, and the Live Broadcast Draft Day War Room.

Public endpoints never expose hidden scouting values (`true_value`, `true_rank`, `err`, `impact`, `_production_pct`)
or raw Madden `ovr`. War Room write actions (`lock-order`, `pick`, `undo`) require `X-Debug-Token`.
"""
import os
from typing import Any, Dict, List, Optional

import psycopg
from fastapi import APIRouter, Header, HTTPException
from psycopg.rows import tuple_row
from pydantic import BaseModel

from engines import college_stats as cs
from engines import draft_order as do
from engines import draft_room as dr
from engines import scouting_events as sev
from engines import scouting_read as sr
from engines import scouting_value as sv
from pipeline.ingest_prospects import inches_height
from server.debug_api import LockIn, PickIn, require_token

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")

router = APIRouter(prefix="/api/draft")

NEED_GROUP_LABELS = {
    "QB": "Quarterback",
    "RB": "Running Back",
    "WR": "Wide Receiver",
    "TE": "Tight End",
    "OT": "Offensive Tackle",
    "IOL": "Interior OL",
    "EDGE": "Edge Rusher",
    "DT": "Defensive Tackle",
    "IDL": "Interior DL",
    "LB": "Linebacker",
    "CB": "Cornerback",
    "S": "Safety",
    "K": "Kicker",
    "P": "Punter",
}


def _connect():
    return psycopg.connect(CONN_INFO, row_factory=tuple_row)


def _league_state(cur) -> tuple[int, int]:
    cur.execute("SELECT current_season, current_week FROM league_state WHERE id = 1")
    row = cur.fetchone()
    return (int(row[0]), int(row[1])) if row else (2036, 1)


def _pick_chart_value(overall_pick: int) -> int:
    """Standard draft trade value points (1st overall = 3000, smooth decay through pick 224)."""
    p = max(1, min(260, int(overall_pick)))
    if p == 1:
        return 3000
    if p == 2:
        return 2600
    if p == 3:
        return 2200
    if p == 4:
        return 1800
    if p <= 32:
        return int(round(1700 * (0.945 ** (p - 5))))
    if p <= 100:
        return int(round(580 * (0.968 ** (p - 33))))
    return max(2, int(round(64 * (0.975 ** (p - 101)))))


@router.get("/state")
def get_draft_state(season: Optional[int] = None):
    """High-level state for the Draft Central header, step selector, and headline ticker."""
    with _connect() as conn:
        cur = conn.cursor()
        cur_season, cur_week = _league_state(cur)
        s_val = season if season is not None else cur_season
        draft_season = s_val + 1
        inst = sr.installed(cur)
        if not inst:
            return {
                "season": s_val,
                "week": cur_week,
                "draft_season": draft_season,
                "installed": False,
                "current_step": None,
                "stored_steps": [],
                "recent_headlines": [],
            }

        steps_rows = sr._steps(cur, draft_season)
        stored_steps = [
            {
                "step": st,
                "season": s,
                "week": w,
                "stage": stg,
                "phase": sev.phase(st),
                "phase_label": cs.STEP_PHASE_LABELS.get(sev.phase(st), "Scouting"),
                "label": cs.step_label(st),
            }
            for st, s, w, stg in steps_rows
        ]
        current_step = stored_steps[-1]["step"] if stored_steps else None

        cur.execute("SELECT count(*) FROM scout_prospects WHERE draft_season = %s AND status != 'WITHDRAWN'", (draft_season,))
        total_prospects = int(cur.fetchone()[0] or 0)

        cur.execute("SELECT count(*) FROM draft_picks WHERE draft_season = %s AND round = 1 AND overall_pick IS NOT NULL", (draft_season,))
        war_room_locked = int(cur.fetchone()[0] or 0) == 32

        cur.execute("SELECT count(*) FROM scout_draft_picks WHERE draft_season = %s", (draft_season,))
        picks_recorded = int(cur.fetchone()[0] or 0)

        recent_headlines = []
        if current_step is not None:
            cur.execute("""
                SELECT e.prospect_key, b.full_name, b.position, b.college, b.rank, b.grade,
                       e.kind, e.headline, e.blurb, e.step
                FROM scout_events e
                JOIN vw_scout_board_public b
                  ON b.draft_season = e.draft_season AND b.step = e.step AND b.prospect_key = e.prospect_key
                WHERE e.draft_season = %s AND e.step = %s AND e.kind != 'VISIT' AND e.headline IS NOT NULL
                ORDER BY b.rank ASC, e.id ASC
                LIMIT 12
            """, (draft_season, current_step))
            for pk, name, pos, col, rk, gr, kind, head, blurb, st in cur.fetchall():
                recent_headlines.append({
                    "prospect_key": pk,
                    "full_name": name,
                    "position": pos,
                    "college": col,
                    "rank": rk,
                    "grade": float(gr) if gr is not None else None,
                    "kind": kind,
                    "headline": head,
                    "blurb": blurb,
                    "step": st,
                })

        return {
            "season": s_val,
            "week": cur_week,
            "draft_season": draft_season,
            "installed": True,
            "current_step": current_step,
            "step_label": cs.step_label(current_step) if current_step else None,
            "phase": sev.phase(current_step) if current_step else None,
            "phase_label": cs.STEP_PHASE_LABELS.get(sev.phase(current_step), "Scouting") if current_step else None,
            "stored_steps": stored_steps,
            "total_prospects": total_prospects,
            "war_room_locked": war_room_locked,
            "picks_recorded": picks_recorded,
            "recent_headlines": recent_headlines,
        }


@router.get("/board")
def get_draft_board(
    draft_season: Optional[int] = None,
    step: Optional[int] = None,
    position: Optional[str] = None,
    conference: Optional[str] = None,
    round_filter: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 250,
):
    """Consensus Big Board with mover deltas, school metadata, and cumulative college season stats."""
    with _connect() as conn:
        cur = conn.cursor()
        cur_season, _ = _league_state(cur)
        ds = draft_season if draft_season is not None else (cur_season + 1)
        if not sr.installed(cur):
            return {"draft_season": ds, "step": None, "prospects": [], "conferences": [], "position_groups": []}

        steps_rows = sr._steps(cur, ds)
        if not steps_rows:
            return {"draft_season": ds, "step": None, "prospects": [], "conferences": [], "position_groups": []}
        active_step = step if step is not None else steps_rows[-1][0]

        raw_board = sr.consensus_board(cur, ds, step=active_step, limit=400, position=position)
        keys = [r["prospect_key"] for r in raw_board]
        stat_map = cs.board_stat_summaries(cur, ds, keys, through_step=active_step)

        conferences_set = set()
        pos_groups_set = set()
        prospects = []

        q_conf = (conference or "").strip().upper()
        q_rnd = (round_filter or "").strip().upper()
        q_search = (search or "").strip().lower()

        for r in raw_board:
            pkey = r["prospect_key"]
            sch = sv.school(r.get("college"))
            conf = r.get("conference") or (sch["conference"] if sch else "Independent")
            p_grp = sv.group_of(r.get("position"))
            if conf:
                conferences_set.add(conf)
            if p_grp:
                pos_groups_set.add(p_grp)

            if q_conf and q_conf != "ALL":
                if q_conf in ("P4", "FBS", "FCS"):
                    if (sch or {}).get("level", "").upper() != q_conf:
                        continue
                elif (conf or "").upper() != q_conf:
                    continue

            rk = int(r.get("rank") or 999)
            if q_rnd and q_rnd != "ALL":
                if q_rnd == "R1" and rk > 32:
                    continue
                if q_rnd == "DAY2" and not (33 <= rk <= 100):
                    continue
                if q_rnd == "DAY3" and not (101 <= rk <= 224):
                    continue
                if q_rnd == "UDFA" and rk <= 224:
                    continue

            if q_search:
                hay = f"{r.get('full_name', '')} {r.get('college', '')} {r.get('position', '')} {p_grp} {conf}".lower()
                if q_search not in hay:
                    continue

            st_info = stat_map.get(pkey) or {"stat_group": p_grp, "games": 0, "totals": {}, "summary_line": "No charted games"}
            prospects.append({
                **r,
                "position_group": p_grp,
                "height": inches_height(r["height_in"]) if r.get("height_in") else None,
                "school_display": sch["display"] if sch else (r.get("college") or "").title(),
                "nickname": sch["nickname"] if sch else None,
                "level": sch["level"] if sch else None,
                "conference": conf,
                "stat_group": st_info["stat_group"],
                "games": st_info["games"],
                "stat_summary": st_info["summary_line"],
                "season_totals": st_info["totals"],
            })

        return {
            "draft_season": ds,
            "step": active_step,
            "step_label": cs.step_label(active_step),
            "total_matching": len(prospects),
            "prospects": prospects[:max(1, min(limit, 400))],
            "conferences": sorted(conferences_set),
            "position_groups": ["QB", "RB", "WR", "TE", "OT", "IOL", "EDGE", "DT", "LB", "CB", "S", "SPECIALISTS"],
        }


@router.get("/prospect/{prospect_key:path}")
def get_prospect_dossier(prospect_key: str, step: Optional[int] = None):
    """Full Scouting Dossier for one prospect: bio, cumulative college stats, game log, combine card, and timeline."""
    with _connect() as conn:
        cur = conn.cursor()
        dossier = cs.prospect_dossier(cur, prospect_key, through_step=step)
        if not dossier:
            raise HTTPException(404, f"Prospect '{prospect_key}' not found")
        return dossier


@router.get("/movers")
def get_draft_movers(season: Optional[int] = None, step: Optional[int] = None, limit: int = 12):
    """Top risers and fallers with their college storylines and cumulative stat summaries."""
    with _connect() as conn:
        cur = conn.cursor()
        cur_season, _ = _league_state(cur)
        s_val = season if season is not None else cur_season
        draft_season = s_val + 1
        if not sr.installed(cur):
            return {"draft_season": draft_season, "step": None, "risers": [], "fallers": []}

        steps_rows = sr._steps(cur, draft_season)
        if not steps_rows:
            return {"draft_season": draft_season, "step": None, "risers": [], "fallers": []}
        active_step = step if step is not None else steps_rows[-1][0]

        cur.execute(f"""
            SELECT {', '.join(sr.BOARD_COLS)}
            FROM vw_scout_board_public
            WHERE draft_season = %s AND step = %s AND delta_rank IS NOT NULL
              AND LEAST(rank, rank + delta_rank) <= 160
        """, (draft_season, active_step))
        rows = [sr._row(r) for r in cur.fetchall()]
        lim = max(1, min(limit, 25))
        up = sorted((r for r in rows if (r["delta_rank"] or 0) > 0), key=lambda r: (-r["delta_rank"], r["rank"]))[:lim]
        down = sorted((r for r in rows if (r["delta_rank"] or 0) < 0), key=lambda r: (r["delta_rank"], r["rank"]))[:lim]

        keys = [r["prospect_key"] for r in up + down]
        evs = sr.events_for(cur, draft_season, keys, step=active_step)
        stat_map = cs.board_stat_summaries(cur, draft_season, keys, through_step=active_step)

        def _enrich(r: dict) -> dict:
            pkey = r["prospect_key"]
            sch = sv.school(r.get("college"))
            st_info = stat_map.get(pkey) or {}
            return {
                **r,
                "position_group": sv.group_of(r.get("position")),
                "height": inches_height(r["height_in"]) if r.get("height_in") else None,
                "school_display": sch["display"] if sch else (r.get("college") or "").title(),
                "stat_summary": st_info.get("summary_line", ""),
                "events": [e for e in evs.get(pkey, []) if e["kind"] != "VISIT"],
            }

        return {
            "draft_season": draft_season,
            "step": active_step,
            "step_label": cs.step_label(active_step),
            "risers": [_enrich(r) for r in up],
            "fallers": [_enrich(r) for r in down],
        }


@router.get("/awards")
def get_college_awards(draft_season: Optional[int] = None, step: Optional[int] = None):
    """Heisman / National Player of the Year Watch and positional college award finalists & watchlists."""
    with _connect() as conn:
        cur = conn.cursor()
        cur_season, _ = _league_state(cur)
        ds = draft_season if draft_season is not None else (cur_season + 1)
        return cs.compile_awards_and_honors(cur, ds, step=step)


@router.get("/team-needs")
def get_team_needs_matrix(season: Optional[int] = None):
    """32-Team Draft Capital & Positional Needs Matrix across all 7 rounds of the upcoming draft."""
    with _connect() as conn:
        cur = conn.cursor()
        cur_season, _ = _league_state(cur)
        s_val = season if season is not None else cur_season
        draft_season = s_val + 1

        cur.execute("""
            SELECT t.id, t.abbreviation, t.city, t.name, t.conference, t.division, t.team_color,
                   ts.wins, ts.losses, ts.team_ovr
            FROM teams t
            LEFT JOIN team_seasons ts ON ts.team_id = t.id AND ts.season = %s
            ORDER BY t.id
        """, (s_val,))
        teams_map: Dict[int, Dict[str, Any]] = {}
        for tid, abbr, city, name, conf, div, color, w, l, ovr in cur.fetchall():
            teams_map[tid] = {
                "team_id": tid,
                "abbreviation": abbr,
                "city": city,
                "name": name,
                "full_name": f"{city} {name}",
                "conference": conf,
                "division": div,
                "team_color": color,
                "record": f"{w or 0}-{l or 0}",
                "team_ovr": ovr,
                "direction": "RETOOL",
                "gm_name": None,
                "needs": [],
                "picks": [],
                "round_1_picks": [],
                "total_picks": 0,
                "draft_capital_points": 0,
            }

        # Enrich with GM direction and positional needs from gm_team_seasons
        cur.execute("SELECT to_regclass('gm_team_seasons') IS NOT NULL")
        if cur.fetchone()[0]:
            cur.execute("""
                SELECT gts.team_id, gts.direction, gts.needs, gp.gm_name
                FROM gm_team_seasons gts
                LEFT JOIN gm_profiles gp ON gp.id = gts.gm_id
                WHERE gts.season = %s
            """, (s_val,))
            for tid, direction, needs_json, gm_name in cur.fetchall():
                if tid not in teams_map:
                    continue
                if direction:
                    teams_map[tid]["direction"] = direction
                teams_map[tid]["gm_name"] = gm_name
                raw_needs = needs_json if isinstance(needs_json, list) else []
                formatted_needs = []
                for idx, n in enumerate(raw_needs[:6], 1):
                    grp = n.get("group")
                    score = float(n.get("score") or 0.0)
                    urgency = "CRITICAL" if score >= 18.0 else ("HIGH" if score >= 10.0 else ("MODERATE" if score >= 5.0 else "DEPTH"))
                    reasons = []
                    if float(n.get("slot_gap") or 0) > 0:
                        reasons.append("Starter upgrade needed")
                    if float(n.get("expiring") or 0) > 0:
                        reasons.append("Expiring contracts")
                    if float(n.get("injury") or 0) > 0:
                        reasons.append("Injury depth")
                    formatted_needs.append({
                        "rank": idx,
                        "group": grp,
                        "label": NEED_GROUP_LABELS.get(grp, grp),
                        "score": round(score, 1),
                        "urgency": urgency,
                        "reasons": reasons,
                    })
                teams_map[tid]["needs"] = formatted_needs

        # Compute 7-round draft order & ownership
        slots = do.draft_order_with_owners(cur, draft_season, rounds=range(1, 8))
        r1_base_order = {s["original_team_id"]: s["round_pick"] for s in slots if s["round"] == 1}

        for s in slots:
            owner_id = s["owner_team_id"]
            orig_id = s["original_team_id"]
            overall = s["pick"]
            rnd = s["round"]
            pts = _pick_chart_value(overall)
            orig_abbr = teams_map.get(orig_id, {}).get("abbreviation", "NFL")
            owner_abbr = teams_map.get(owner_id, {}).get("abbreviation", "NFL")

            if owner_id in teams_map:
                pick_obj = {
                    "round": rnd,
                    "round_pick": s["round_pick"],
                    "overall_pick": overall,
                    "original_team_id": orig_id,
                    "original_team": orig_abbr,
                    "owner_team_id": owner_id,
                    "owner_team": owner_abbr,
                    "status": "OWN" if owner_id == orig_id else "ACQUIRED",
                    "value_points": pts,
                    "locked": s.get("locked", False),
                }
                teams_map[owner_id]["picks"].append(pick_obj)
                teams_map[owner_id]["total_picks"] += 1
                teams_map[owner_id]["draft_capital_points"] += pts
                if rnd == 1:
                    teams_map[owner_id]["round_1_picks"].append(overall)

        teams_list = list(teams_map.values())
        by_capital = sorted(teams_list, key=lambda t: (-t["draft_capital_points"], r1_base_order.get(t["team_id"], 32)))
        for rk, t in enumerate(by_capital, 1):
            t["capital_rank"] = rk
            t["base_draft_slot"] = r1_base_order.get(t["team_id"], 32)

        # Default sort by base Round 1 draft order (1 to 32)
        teams_list.sort(key=lambda t: t["base_draft_slot"])

        return {
            "season": s_val,
            "draft_season": draft_season,
            "teams": teams_list,
        }


@router.get("/mock")
def get_media_mock_draft(season: Optional[int] = None, week: Optional[int] = None):
    """Round 1 Media Mock Draft (Mel Kiper & Analytics Desk), stripped of hidden OVR."""
    from engines.mock_draft_engine import MockDraftEngine, POSITION_GROUP_MAP
    from pipeline.draft_board import read_mock

    with _connect() as conn:
        cur = conn.cursor()
        cur_season, cur_week = _league_state(cur)
        s_val = season if season is not None else cur_season
        w_val = week if week is not None else cur_week
        draft_season = s_val + 1

        selections = read_mock(cur, s_val, w_val)
        if not selections:
            try:
                eng = MockDraftEngine(season=s_val)
                selections = eng.run_mock()
            except Exception as e:
                return {"season": s_val, "week": w_val, "draft_season": draft_season, "picks": [], "error": str(e)}

        # Enrich with consensus grade, prospect_key, school info, and college stat summary (and strip raw OVR)
        board_rows = sr.consensus_board(cur, draft_season, limit=400) if sr.installed(cur) else []
        by_name_pos = {(r["full_name"].upper(), r["position"].upper()): r for r in board_rows if r.get("full_name") and r.get("position")}
        stat_map = cs.board_stat_summaries(cur, draft_season, [r["prospect_key"] for r in board_rows]) if board_rows else {}

        # Also map through draft_picks Round 1 ownership so traded slots show via-team context
        r1_slots = do.draft_order_with_owners(cur, draft_season, rounds=(1,))
        slot_by_pick = {s["pick"]: s for s in r1_slots}
        teams_lookup = do.fetch_teams(cur)

        out_picks = []
        for sel in selections:
            p = dict(sel.get("prospect") or {})
            p.pop("overall_rating", None)
            p.pop("ovr", None)

            name_u = (p.get("full_name") or "").upper()
            pos_u = (p.get("position") or "").upper()
            b_row = by_name_pos.get((name_u, pos_u))
            pkey = b_row["prospect_key"] if b_row else sv.prospect_key(draft_season, name_u, pos_u)
            sch = sv.school(p.get("college"))
            st_info = stat_map.get(pkey) or {}

            pk_num = int(sel["pick"])
            slot_info = slot_by_pick.get(pk_num)
            orig_abbr = sel.get("team_abbr")
            owner_abbr = orig_abbr
            owner_name = sel.get("team")
            if slot_info:
                orig_t = teams_lookup.get(slot_info["original_team_id"], {})
                owner_t = teams_lookup.get(slot_info["owner_team_id"], {})
                orig_abbr = orig_t.get("abbreviation") or orig_abbr
                owner_abbr = owner_t.get("abbreviation") or owner_abbr
                owner_name = owner_t.get("name") or owner_name

            out_picks.append({
                "pick": pk_num,
                "team_id": slot_info["owner_team_id"] if slot_info else sel.get("team_id"),
                "team": owner_name,
                "team_abbr": owner_abbr,
                "original_team_abbr": orig_abbr,
                "traded": owner_abbr != orig_abbr,
                "record": sel.get("record", ""),
                "locked": bool(sel.get("locked")),
                "selection_status": sel.get("selection_status"),
                "fit_score": sel.get("fit_score"),
                "rationale": sel.get("rationale"),
                "prospect": {
                    "prospect_key": pkey,
                    "full_name": p.get("full_name"),
                    "position": p.get("position"),
                    "position_group": POSITION_GROUP_MAP.get(pos_u, pos_u),
                    "college": p.get("college"),
                    "school_display": sch["display"] if sch else (p.get("college") or "").title(),
                    "conference": b_row.get("conference") if b_row else (sch["conference"] if sch else None),
                    "height": p.get("height"),
                    "weight": p.get("weight"),
                    "age": p.get("age"),
                    "class_year": b_row.get("class_year") if b_row else None,
                    "consensus_rank": b_row.get("rank") if b_row else p.get("projected_pick"),
                    "grade": b_row.get("grade") if b_row else None,
                    "round_label": b_row.get("round_label") if b_row else "1st round",
                    "delta_rank": b_row.get("delta_rank") if b_row else 0,
                    "stat_summary": st_info.get("summary_line", ""),
                },
            })

        return {
            "season": s_val,
            "week": w_val,
            "draft_season": draft_season,
            "analyst": "Mel Kiper Jr. & Gridiron Draft Lab",
            "picks": out_picks,
        }


def _evaluate_pick_grade(pick_num: int, consensus_rank: Optional[int], grade: Optional[float], choice_source: Optional[str]) -> Dict[str, str]:
    """Compute an instant broadcast grade and verdict tag for a completed Round 1 selection."""
    rk = consensus_rank or pick_num
    delta = pick_num - rk   # positive = drafted later than consensus rank (value/steal), negative = drafted earlier (reach)
    g = float(grade or 80.0)

    if pk_is_top := (pick_num <= 5 and rk <= 5):
        return {"letter": "A+", "verdict": "FRANCHISE CORNERSTONE", "tone": "elite"}
    if delta >= 10 or (delta >= 5 and g >= 85.0):
        return {"letter": "A+", "verdict": "GRAND THEFT STEAL", "tone": "elite"}
    if delta >= 4:
        return {"letter": "A", "verdict": "HIGH-VALUE SELECTION", "tone": "good"}
    if delta >= -4:
        return {"letter": "A-" if g >= 83.0 else "B+", "verdict": "CLEAN BOARD FIT", "tone": "good"}
    if delta >= -12:
        return {"letter": "B" if choice_source == "GM" else "B-", "verdict": "SCHEME TARGET", "tone": "neutral"}
    if delta >= -22:
        return {"letter": "C+", "verdict": "HIGH-CONVICTION REACH", "tone": "warn"}
    return {"letter": "C-", "verdict": "MAJOR BOARD REACH", "tone": "reach"}


@router.get("/war-room")
def get_war_room(team: Optional[str] = None, draft_season: Optional[int] = None):
    """Broadcast Draft Day War Room state: slots with instant grades, on-the-clock console, team boards, and trade offers."""
    with _connect() as conn:
        cur = conn.cursor()
        state = dr.state(cur, draft_season, team)
        if not state.get("installed") or state.get("error"):
            return state

        ds = state["draft_season"]
        step = state.get("step")

        # Enrich slots' selections and available pool with college stat summaries & instant pick grades
        all_keys = set()
        for s in state.get("slots") or []:
            if s.get("selection") and s["selection"].get("prospect_key"):
                all_keys.add(s["selection"]["prospect_key"])
        for c in state.get("consensus") or []:
            if c.get("prospect_key"):
                all_keys.add(c["prospect_key"])
        oc = state.get("on_clock")
        if oc:
            if oc.get("recommendation") and oc["recommendation"].get("prospect_key"):
                all_keys.add(oc["recommendation"]["prospect_key"])
            for alt in oc.get("alternatives") or []:
                if alt.get("prospect_key"):
                    all_keys.add(alt["prospect_key"])

        stat_map = cs.board_stat_summaries(cur, ds, list(all_keys), through_step=step) if all_keys else {}

        # Fetch recorded rationales for completed picks
        cur.execute("SELECT overall_pick, rationale FROM scout_draft_picks WHERE draft_season = %s", (ds,))
        rat_by_pick = {int(p): r for p, r in cur.fetchall()}

        for s in state.get("slots") or []:
            sel = s.get("selection")
            if sel:
                pkey = sel.get("prospect_key")
                st_info = stat_map.get(pkey) or {}
                sel["stat_summary"] = st_info.get("summary_line", "")
                sel["rationale"] = rat_by_pick.get(s["pick"])
                sel["pick_grade"] = _evaluate_pick_grade(
                    s["pick"], sel.get("consensus_rank"), sel.get("grade"), sel.get("choice_source")
                )

        if oc:
            rec = oc.get("recommendation")
            if rec:
                rec["stat_summary"] = (stat_map.get(rec.get("prospect_key")) or {}).get("summary_line", "")
            for alt in oc.get("alternatives") or []:
                alt["stat_summary"] = (stat_map.get(alt.get("prospect_key")) or {}).get("summary_line", "")

        for c in state.get("consensus") or []:
            c["stat_summary"] = (stat_map.get(c.get("prospect_key")) or {}).get("summary_line", "")

        # Provide list of all 32 team abbreviations for the War Room board switcher
        cur.execute("SELECT abbreviation, city || ' ' || name FROM teams ORDER BY abbreviation")
        state["all_teams"] = [{"abbr": a, "name": n} for a, n in cur.fetchall()]
        return state


def _run_war_room_action(action: str, *args):
    with _connect() as conn:
        cur = conn.cursor()
        if not dr.installed(cur):
            raise HTTPException(409, "the scouting migration (2026_12_12_scouting.sql) isn't applied")
        try:
            getattr(dr, action)(cur, *args)
        except dr.DraftRoomError as e:
            conn.rollback()
            raise HTTPException(e.status, e.message)
        conn.commit()
    return get_war_room()


@router.post("/war-room/lock-order")
def war_room_lock_order(body: Optional[LockIn] = None, x_debug_token: Optional[str] = Header(None)):
    """Lock the 224-pick draft order with optional Round 1 owner fixes."""
    require_token(x_debug_token)
    return _run_war_room_action("lock_order", None, (body.fixes if body else None))


@router.post("/war-room/picks/{n}")
def war_room_record_pick(n: int, body: PickIn, x_debug_token: Optional[str] = Header(None)):
    """Record pick n in the Live Draft War Room."""
    require_token(x_debug_token)
    from engines.gm_llm import flush, make_llm
    llm = make_llm("draft_rationale")
    try:
        return _run_war_room_action("record", None, n, body.prospect_key, llm)
    finally:
        flush(llm)


@router.post("/war-room/picks/{n}/undo")
def war_room_undo_pick(n: int, x_debug_token: Optional[str] = Header(None)):
    """Undo the last recorded pick n."""
    require_token(x_debug_token)
    return _run_war_room_action("undo_last", None, n)
