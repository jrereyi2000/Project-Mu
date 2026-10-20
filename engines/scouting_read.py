"""
Scouting read helpers for the newsroom (team management step 5 Phase 9) and #debug (docs/team_management/
04_SCOUTING_AND_DRAFT_PLAN.md §2.11; PROJECT_SPEC §2.18). Read-only, plain tuple cursors.

Only public fields leave this module: consensus grade, rank, round label and their changes, the prospect's board
fields (no OVR), stored events (payload keys starting "_" removed), track-record labels. Never V, true rank, e,
impact, q or team boards.

Visibility (the injury-report rule): a step stored in the run for Week N is visible to Week N articles (week <=
cutoff). Offseason steps (23 + stage) are visible once the article's offseason event has reached that stage
(newsroom.run_context.OFFSEASON_EVENT_ORDER: super_bowl 1 .. draft 4).
"""
import json

BOARD_COLS = ("draft_season", "step", "season", "week", "stage", "prospect_key", "grade", "rank", "round_label",
              "delta_grade", "delta_rank", "full_name", "position", "college", "height_in", "weight", "age",
              "class_year", "conference", "status", "top_headline")


def installed(cur):
    cur.execute("SELECT to_regclass('scout_consensus') IS NOT NULL")
    return bool(cur.fetchone()[0])


def visible_step(steps, season, cutoff_week=None, offseason_stage=None):
    """The latest visible step. steps: [(step, season, week, stage)]. cutoff_week None = no cutoff (everything);
    offseason_stage = the article's offseason event order (None = every stored offseason step)."""
    best = None
    for step, s, w, st in steps:
        if s != season:
            continue
        if st:                                            # an offseason step
            if cutoff_week is not None and cutoff_week < 24:
                continue
            if offseason_stage is not None and st > offseason_stage:
                continue
        elif cutoff_week is not None and w > cutoff_week:
            continue
        best = step if best is None or step > best else best
    return best


def public_payload(payload):
    if isinstance(payload, str):
        payload = json.loads(payload)
    return {k: v for k, v in (payload or {}).items() if not str(k).startswith("_")}


def _steps(cur, draft_season):
    cur.execute("SELECT step, season, week, stage FROM scout_runs WHERE draft_season = %s ORDER BY step",
                (draft_season,))
    return cur.fetchall()


def _context(season, week=None):
    """(cutoff_week, offseason_stage) from the newsroom run context, or the explicit week."""
    stage = None
    try:
        from newsroom.run_context import OFFSEASON_EVENT_ORDER, context_offseason_event, cutoff_week
        cut = cutoff_week(season) if week is None else week
        ev = context_offseason_event()
        stage = OFFSEASON_EVENT_ORDER.get(ev) if ev else None
    except Exception:
        cut = week
    return cut, stage


def consensus_board(cur, draft_season, step=None, limit=64, position=None):
    """The public board at `step` (default: the latest stored), best first: [{BOARD_COLS}]."""
    if step is None:
        steps = _steps(cur, draft_season)
        if not steps:
            return []
        step = steps[-1][0]
    sql = f"SELECT {', '.join(BOARD_COLS)} FROM vw_scout_board_public WHERE draft_season = %s AND step = %s"
    args = [draft_season, step]
    if position:
        want = position.upper()
        sql += " AND (upper(position) = %s OR upper(position) = ANY(%s))"
        args += [want, sorted(_positions_in(want))]
    sql += " ORDER BY rank LIMIT %s"
    args.append(limit)
    cur.execute(sql, args)
    return [_row(r) for r in cur.fetchall()]


def _positions_in(group):
    from engines.mock_draft_engine import POSITION_GROUP_MAP
    return {p for p, g in POSITION_GROUP_MAP.items() if g == group}


def _row(r):
    d = dict(zip(BOARD_COLS, r))
    for k in ("grade", "delta_grade"):
        d[k] = float(d[k]) if d[k] is not None else None
    return d


def events_for(cur, draft_season, keys, step=None, through_step=None):
    """{key: [{step, kind, team, headline, blurb, payload}]} (VISITs only name the team)."""
    if not keys:
        return {}
    sql = """SELECT e.prospect_key, e.step, e.kind, t.abbreviation, e.headline, e.blurb, e.payload
             FROM scout_events e LEFT JOIN teams t ON t.id = e.team_id
             WHERE e.draft_season = %s AND e.prospect_key = ANY(%s)"""
    args = [draft_season, list(keys)]
    if step is not None:
        sql += " AND e.step = %s"
        args.append(step)
    if through_step is not None:
        sql += " AND e.step <= %s"
        args.append(through_step)
    cur.execute(sql + " ORDER BY e.step, abs(e.impact) DESC NULLS LAST, e.id", args)
    out = {}
    for k, st, kind, team, head, blurb, payload in cur.fetchall():
        out.setdefault(k, []).append({"step": st, "kind": kind, "team": team, "headline": head, "blurb": blurb,
                                      "payload": public_payload(payload)})
    return out


def risers_fallers(cur, season, week=None, limit=10, within=150):
    """The latest step visible at the article's cutoff: {draft_season, step, week, stage, risers, fallers}, each
    mover a public board row plus the events that drove it. None when nothing is visible."""
    if not installed(cur):
        return None
    draft_season = season + 1
    cut, stage = _context(season, week)
    step = visible_step(_steps(cur, draft_season), season, cut, stage)
    if step is None:
        return None
    cur.execute(f"""SELECT {', '.join(BOARD_COLS)} FROM vw_scout_board_public
                    WHERE draft_season = %s AND step = %s AND delta_rank IS NOT NULL
                      AND LEAST(rank, rank + delta_rank) <= %s""", (draft_season, step, within))
    rows = [_row(r) for r in cur.fetchall()]
    up = sorted((r for r in rows if r["delta_rank"] > 0), key=lambda r: (-r["delta_rank"], r["rank"]))[:limit]
    down = sorted((r for r in rows if r["delta_rank"] < 0), key=lambda r: (r["delta_rank"], r["rank"]))[:limit]
    evs = events_for(cur, draft_season, [r["prospect_key"] for r in up + down], step=step)
    for r in up + down:
        r["events"] = [e for e in evs.get(r["prospect_key"], []) if e["kind"] != "VISIT"]
    meta = next(s for s in _steps(cur, draft_season) if s[0] == step)
    return {"draft_season": draft_season, "step": step, "week": meta[2], "stage": meta[3], "risers": up,
            "fallers": down}


def prospect_story(cur, prospect_key, through_step=None):
    """One prospect's public timeline: board rows by step and every stored event through `through_step`."""
    draft_season = int(prospect_key.split(":")[0])
    sql = f"SELECT {', '.join(BOARD_COLS)} FROM vw_scout_board_public WHERE prospect_key = %s"
    args = [prospect_key]
    if through_step is not None:
        sql += " AND step <= %s"
        args.append(through_step)
    cur.execute(sql + " ORDER BY step", args)
    board = [_row(r) for r in cur.fetchall()]
    return {"prospect_key": prospect_key, "board": board,
            "events": events_for(cur, draft_season, [prospect_key], through_step=through_step).get(prospect_key, [])}
