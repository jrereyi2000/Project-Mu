"""
Team vision (FRONT_OFFICE_INBOX_PLAN.md §2.9; PROJECT_SPEC §2.16, §2.21b, §3.3s).

A vision is the coach and the GM agreeing on three settings: direction (CONTEND / RETOOL / REBUILD, minus
gm_direction's overrides), emphasis (OFFENSE / DEFENSE / BALANCED) and playing time (YOUTH / VETERANS / MERIT,
stored only). The engine decides whether a proposal is allowed and whether the counterpart agrees; the kind
(`engines/inbox/kinds/vision.py`) carries the conversation and stores an AGREED vision in `team_vision`.

  allowed(scores_row, cap_space) -> {'direction': [...], 'emphasis': [...], 'playing_time': [...]}
  respond(cur, team_id, season, proposal, counterpart) -> {'decision', 'vision', 'reasons'}
  context(cur, team_id, season, week=None) -> dict      the league data the decision reads (pure helpers below)
  decide(context, proposal, counterpart, risk_appetite=None) -> same shape as respond
  current(cur, team_id, season) -> dict | None
  upsert(cur, team_id, season, vision, thread_id)

Direction: a direction the overrides forbid REFUSEs with the override as the reason. A direction equal to the
engine's band agrees. Otherwise the D-score must be within VISION_MARGIN x lean of the proposed band's nearest
edge: lean = 0.5 + risk_appetite for a GM counterpart, 1.0 for a coach. Emphasis agrees unless the team's top 3
needs are all on the other side, when the engine counters BALANCED. Playing time always agrees.
Nothing here commits.
"""
from engines import gm_direction

VISION_MARGIN = 0.5                    # D-score units; an estimate (plan §2.9)
DIRECTIONS = ("CONTEND", "RETOOL", "REBUILD")
EMPHASES = ("OFFENSE", "DEFENSE", "BALANCED")
PLAYING_TIMES = ("YOUTH", "VETERANS", "MERIT")
OFFENSE_GROUPS = frozenset(gm_direction.OFFENSE_GROUPS)
DEFENSE_GROUPS = frozenset(gm_direction.DEFENSE_GROUPS)


def _regclass(cur, table):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (table,))
    row = cur.fetchone()
    return bool(row and row[0])


def _current_week(cur):
    try:
        cur.execute("SELECT current_week FROM league_state WHERE id = 1")
        row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


def _normalize(proposal):
    p = dict(proposal or {})
    direction = str(p.get("direction") or "").strip().upper() or None
    emphasis = str(p.get("emphasis") or "BALANCED").strip().upper()
    playing_time = str(p.get("playing_time") or "MERIT").strip().upper()
    return {"direction": direction, "emphasis": emphasis, "playing_time": playing_time}


def allowed(scores_row, cap_space):
    """The permitted settings (plan §2.9). Direction is the three bands minus assign_directions' overrides:
    projected wins >= 10.5 allows only CONTEND; a team over the cap (cap_space < 0) can't REBUILD."""
    directions = list(DIRECTIONS)
    proj_wins = (scores_row or {}).get("proj_wins")
    if proj_wins is not None and proj_wins >= gm_direction.ALWAYS_CONTEND_WINS:
        directions = ["CONTEND"]
    elif cap_space is not None and cap_space < 0:
        directions.remove("REBUILD")
    return {"direction": directions, "emphasis": list(EMPHASES), "playing_time": list(PLAYING_TIMES)}


def _override_reason(direction, scores_row, cap_space):
    proj_wins = (scores_row or {}).get("proj_wins")
    if proj_wins is not None and proj_wins >= gm_direction.ALWAYS_CONTEND_WINS:
        return "proj_wins >= 10.5"
    if direction == "REBUILD" and cap_space is not None and cap_space < 0:
        return "over the cap"
    return "not allowed"


def _edge_distance(d, direction, edges):
    """How far D is from the proposed band's nearest edge (0 when D is inside the band)."""
    hi, lo = edges
    if direction == "CONTEND":
        return abs(d - hi)
    if direction == "REBUILD":
        return abs(d - lo)
    return min(abs(d - hi), abs(d - lo))


def _lean(counterpart, risk_appetite=None):
    c = counterpart or {}
    role = str(c.get("type") or c.get("role") or "").lower()
    if role != "gm":
        return 1.0                                      # a coach (no profile) or an unknown counterpart
    risk = risk_appetite if risk_appetite is not None else c.get("risk_appetite")
    return 0.5 + (float(risk) if risk is not None else 0.5)


def _all_other_side(needs, emphasis):
    """True when the team's top 3 needs are all on the side the emphasis doesn't favour."""
    if emphasis == "OFFENSE":
        other = DEFENSE_GROUPS
    elif emphasis == "DEFENSE":
        other = OFFENSE_GROUPS
    else:
        return False
    top = [n.get("group") for n in list(needs or [])[:3]]
    return bool(top) and all(g in other for g in top)


def decide(context, proposal, counterpart=None, risk_appetite=None):
    """The engine's decision over a `context(...)` dict (see respond). `counterpart`:
    {'type': 'gm'|'coach', 'id': ..., 'risk_appetite': float | None}."""
    p = _normalize(proposal)
    scores_row = context["scores_row"]
    cap_space = context.get("cap_space")
    band = context["engine_band"]
    needs = context.get("needs") or []
    reasons = []
    if p["direction"] not in DIRECTIONS:
        return {"decision": "REFUSE", "vision": dict(p), "reasons": ["a direction must be CONTEND, RETOOL or REBUILD"]}
    if p["direction"] not in allowed(scores_row, cap_space)["direction"]:
        return {"decision": "REFUSE", "vision": dict(p),
                "reasons": [_override_reason(p["direction"], scores_row, cap_space)]}
    if p["direction"] == band:
        direction_ok = True
        reasons.append(f"direction {band} is the engine's band")
    else:
        margin = VISION_MARGIN * _lean(counterpart, risk_appetite)
        distance = _edge_distance(scores_row["D"], p["direction"], context["edges"])
        direction_ok = distance <= margin
        reasons.append(f"direction {p['direction']} is {distance:.2f} from its band edge (margin {margin:.2f})")
    emphasis = p["emphasis"] if p["emphasis"] in EMPHASES else "BALANCED"
    if _all_other_side(needs, emphasis):
        emphasis_ok = False
        side = "defensive" if emphasis == "OFFENSE" else "offensive"
        reasons.append(f"the top 3 needs are all {side}; BALANCED keeps the emphasis honest")
    else:
        emphasis_ok = True
        reasons.append(f"emphasis {emphasis} fits the needs")
    reasons.append(f"playing time {p['playing_time']} is stored only")
    if direction_ok and emphasis_ok:
        return {"decision": "AGREE", "vision": {"direction": p["direction"], "emphasis": emphasis,
                                                "playing_time": p["playing_time"]}, "reasons": reasons}
    vision = {"direction": p["direction"] if direction_ok else band,
              "emphasis": emphasis if emphasis_ok else "BALANCED",
              "playing_time": p["playing_time"]}
    return {"decision": "COUNTER", "vision": vision, "reasons": reasons}


def context(cur, team_id, season, week=None):
    """The league data a vision decision reads: the team's score row, the band edges, the engine's own band
    (band_edges + overrides, ignoring any pin), its ranked needs and cap space."""
    if week is None:
        week = _current_week(cur)
    league = gm_direction.read_league(cur, season, week)
    teams, roster = league["teams"], league["roster"]
    team = next((t for t in teams if t["team_id"] == team_id), None)
    if team is None:
        raise ValueError(f"team {team_id} is not in season {season}")
    scores = gm_direction.direction_scores(teams)
    cap_space = {t["team_id"]: t["cap_space"] for t in teams}
    band, band_reason = gm_direction.assign_directions(scores, None, cap_space)[team_id]
    injured = gm_direction.out_ids(league["injuries"])
    slots = {t["team_id"]: gm_direction.starter_slots(roster.get(t["team_id"], []), injured) for t in teams}
    lines = gm_direction.league_lines({tid: gm_direction.slot_ovrs(s) for tid, s in slots.items()})
    needs, surplus = gm_direction.positional_needs(roster.get(team_id, []), injured, league["expiring"], lines,
                                                   band)
    return {"team_id": team_id, "scores_row": scores[team_id], "edges": gm_direction.band_edges(scores),
            "engine_band": band, "engine_reason": band_reason, "needs": needs, "surplus": surplus,
            "cap_space": team["cap_space"]}


def _risk_appetite(cur, counterpart):
    c = counterpart or {}
    if str(c.get("type") or c.get("role") or "").lower() != "gm":
        return None
    if c.get("risk_appetite") is not None:
        return float(c["risk_appetite"])
    try:
        pid = int(str(c.get("id")))
    except (TypeError, ValueError):
        return None
    cur.execute("SELECT risk_appetite FROM gm_profiles WHERE id = %s", (pid,))
    row = cur.fetchone()
    return float(row[0]) if row and row[0] is not None else None


def respond(cur, team_id, season, proposal, counterpart=None):
    """The vision decision (plan §2.9). Returns {'decision': 'AGREE'|'COUNTER'|'REFUSE', 'vision',
    'reasons'}. `counterpart` is {'type': 'gm'|'coach', 'id': ..., 'risk_appetite': optional}; a GM's lean
    uses its risk_appetite (read from gm_profiles when not given), a coach's is 1.0."""
    ctx = context(cur, team_id, season)
    return decide(ctx, proposal, counterpart, _risk_appetite(cur, counterpart))


def current(cur, team_id, season):
    """The season's agreed vision, or None (also None without the team_vision table)."""
    if not _regclass(cur, "team_vision"):
        return None
    cur.execute("SELECT direction, emphasis, playing_time, thread_id FROM team_vision "
                "WHERE team_id = %s AND season = %s", (team_id, season))
    row = cur.fetchone()
    if not row:
        return None
    return {"direction": row[0], "emphasis": row[1], "playing_time": row[2], "thread_id": row[3]}


def upsert(cur, team_id, season, vision, thread_id=None):
    """Store an agreed vision for (team, season), replacing any earlier one. Returns the normalized vision."""
    v = _normalize(vision)
    cur.execute("""INSERT INTO team_vision (team_id, season, direction, emphasis, playing_time, thread_id)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON CONFLICT (team_id, season) DO UPDATE SET direction = EXCLUDED.direction,
                       emphasis = EXCLUDED.emphasis, playing_time = EXCLUDED.playing_time,
                       thread_id = EXCLUDED.thread_id, agreed_at = now()""",
                (team_id, season, v["direction"], v["emphasis"], v["playing_time"], thread_id))
    return v
