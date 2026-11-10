"""
VISION: the coach and the GM agree on the team's direction (FRONT_OFFICE_INBOX_PLAN.md §2.8-§2.9; U6).

The conversation is COACH -> the team's GM, or GM -> the team's head coach, depending on which of the two JR
controls (`ACTOR_ROLES` is not declared, so the registry scans the kind once per team). It is skipped when JR
controls both, and when the coach is the GM. The engine behind it is `engines/team_vision.py`; an AGREED vision is
stored in `team_vision` (the direction pins gm_direction for the season, the emphasis weights that side's needs;
playing time is stored only).

Triggers, each once per source_ref:
  SEASON_VISION   the first scan of a season with no team_vision row (source vision:season:<season>)
  DIRECTION_SHIFT at a checkpoint (gm_direction.is_checkpoint), when gm_direction's own band has moved away from the
                  pinned direction (source vision:shift:<season>:<week>)

JR answers PROPOSE (form `vision`: direction, emphasis, playing time), ACCEPT_COUNTER (after a COUNTER) or
KEEP_CURRENT. AGREED upserts team_vision and closes the thread. A typed line drafts the PROPOSE (`draft`). JR-started
any time (source vision:jr:<season>:<week>); one open per team (the store's VISION cap is 1).
"""
from engines import gm_direction, team_vision
from engines.inbox import InboxError
from engines.inbox import actors as actors_mod
from engines.inbox import helpers
from engines.inbox.registry import ActResult, Trigger


def _abbr(cur, team_id):
    """The team's abbreviation; a missing row falls back to the id, which is what a fresh franchise shows."""
    return helpers.abbr(cur, team_id, default=str(team_id))


KIND = "VISION"
COUNTERPART_TYPE = "gm"                # the coach -> GM case; a GM actor talks to the head coach (coach)
VOICE = "a front-office professional who cares about where this franchise is headed"
SEASON_SOURCE = "vision:season:{season}"
SHIFT_SOURCE = "vision:shift:{season}:{week}"
JR_SOURCE = "vision:jr:{season}:{week}"


def _actor(team):
    """The persona JR controls on this team that the vision talks run through, or None. Both controlled (or
    neither) means no conversation: JR has nobody to talk to across the table."""
    acts = list(team.get("actors") or [])
    gm = next((a for a in acts if a is not None and a.role == "GM"), None)
    coach = next((a for a in acts if a is not None and a.role == "COACH"), None)
    if gm is not None and coach is not None:
        return None
    return coach or gm


def _counterpart(cur, team_id, role):
    """(type, id, name) on the other side of a coach or GM actor, or (None, None, None) when there is no separate
    other side (no open tenure, or the coach is the GM)."""
    gm, hc = actors_mod.current_gm(cur, team_id), actors_mod.head_coach(cur, team_id)
    if gm and hc and gm.get("coach_id") is not None and gm["coach_id"] == hc["id"]:
        return None, None, None
    if str(role or "").upper() == "COACH":
        return ("gm", str(gm["id"]), gm["name"]) if gm else (None, None, None)
    if str(role or "").upper() == "GM":
        return ("coach", str(hc["id"]), hc["name"]) if hc else (None, None, None)
    return None, None, None


def _trigger(team, ctype, cid, cname, source, facts, intent, season, actor):
    return Trigger(KIND, team["team_id"], ctype, cid, cname, f"{season} team vision", source, facts, intent,
                   expires_season=season, actor_role=actor.role, actor_id=actor.persona_id)


def scan(cur, season, week, team):
    actor = _actor(team)
    if actor is None:
        return []
    team_id = team["team_id"]
    ctype, cid, cname = _counterpart(cur, team_id, actor.role)
    if ctype is None:
        return []
    vision = team_vision.current(cur, team_id, season)
    base = {"team": team.get("abbr"), "counterpart": cname}
    if vision is None:
        ctx = team_vision.context(cur, team_id, season, week)
        facts = dict(base, engine_direction=ctx["engine_band"],
                     allowed=team_vision.allowed(ctx["scores_row"], ctx.get("cap_space")), pinned=None)
        return [_trigger(team, ctype, cid, cname, SEASON_SOURCE.format(season=season), facts, "SEASON_VISION",
                         season, actor)]
    if gm_direction.is_checkpoint(week) and vision.get("direction"):
        ctx = team_vision.context(cur, team_id, season, week)
        if ctx["engine_band"] != vision["direction"]:
            facts = dict(base, engine_direction=ctx["engine_band"], pinned=vision["direction"],
                         emphasis=vision.get("emphasis"), playing_time=vision.get("playing_time"))
            return [_trigger(team, ctype, cid, cname, SHIFT_SOURCE.format(season=season, week=week), facts,
                             "DIRECTION_SHIFT", season, actor)]
    return []


def options(cur, thread, last):
    state = (thread or {}).get("state") or {}
    current = team_vision.current(cur, thread["team_id"], thread["season"]) or {}
    seed = {"direction": current.get("direction"), "emphasis": current.get("emphasis") or "BALANCED",
            "playing_time": current.get("playing_time") or "MERIT"}
    out = [{"id": "PROPOSE", "label": "Propose a vision", "form": "vision", "needs_note": False, "default": seed}]
    if state.get("counter"):
        out.append({"id": "ACCEPT_COUNTER", "label": "Accept the counter", "form": None, "needs_note": False,
                    "default": None})
    out.append({"id": "KEEP_CURRENT", "label": "Keep the current direction", "form": None, "needs_note": False,
                "default": None})
    return out


def _proposal(payload):
    p = dict(payload or {})
    direction = str(p.get("direction") or "").strip().upper()
    emphasis = str(p.get("emphasis") or "BALANCED").strip().upper()
    playing_time = str(p.get("playing_time") or "MERIT").strip().upper()
    if direction not in team_vision.DIRECTIONS:
        raise InboxError(400, "direction must be one of " + ", ".join(team_vision.DIRECTIONS))
    if emphasis not in team_vision.EMPHASES:
        raise InboxError(400, "emphasis must be one of " + ", ".join(team_vision.EMPHASES))
    if playing_time not in team_vision.PLAYING_TIMES:
        raise InboxError(400, "playing time must be one of " + ", ".join(team_vision.PLAYING_TIMES))
    return {"direction": direction, "emphasis": emphasis, "playing_time": playing_time}


def _facts(cur, thread, decision, vision, reasons, **extra):
    facts = {"team": _abbr(cur, thread["team_id"]), "counterpart": thread.get("counterpart_name"),
             "decision": decision, "direction": vision.get("direction"), "emphasis": vision.get("emphasis"),
             "playing_time": vision.get("playing_time"), "reasons": list(reasons)}
    facts.update(extra)
    return facts


def act(cur, thread, option_id, payload, note):
    option_id = str(option_id or "").strip().upper()
    state = dict(thread.get("state") or {})
    team_id, season = thread["team_id"], thread["season"]
    if option_id == "PROPOSE":
        proposal = _proposal(payload)
        state.pop("counter", None)                       # a new proposal replaces any old counter
        state.pop("counter_reasons", None)
        result = team_vision.respond(cur, team_id, season, proposal,
                                     {"type": thread.get("counterpart_type"), "id": thread.get("counterpart_id")})
        decision, vision, reasons = result["decision"], result["vision"], result["reasons"]
        ref = {"vision": vision, "decision": decision, "reasons": list(reasons)}
        if decision == "AGREE":
            team_vision.upsert(cur, team_id, season, vision, thread["id"])
            return ActResult(facts=_facts(cur, thread, "AGREE", vision, reasons, proposal=proposal),
                             intent="AGREED", engine_ref=ref, close=True)
        if decision == "COUNTER":
            state.update(counter=vision, counter_reasons=list(reasons))
            return ActResult(facts=_facts(cur, thread, "COUNTER", vision, reasons, proposal=proposal),
                             intent="COUNTERED", engine_ref=ref, state=state)
        return ActResult(facts=_facts(cur, thread, "REFUSE", proposal, reasons, proposal=proposal),
                         intent="REFUSED", engine_ref=ref, state=state)
    if option_id == "ACCEPT_COUNTER":
        vision = state.get("counter")
        if not vision:
            raise InboxError(400, "there is no counter to accept")
        team_vision.upsert(cur, team_id, season, vision, thread["id"])
        reasons = list(state.get("counter_reasons") or []) + ["JR accepted the counter"]
        return ActResult(facts=_facts(cur, thread, "AGREE", vision, reasons),
                         intent="AGREED", engine_ref={"vision": vision, "decision": "AGREE"}, close=True)
    if option_id == "KEEP_CURRENT":
        current = team_vision.current(cur, team_id, season)
        facts = {"team": _abbr(cur, thread["team_id"]), "counterpart": thread.get("counterpart_name"),
                 "direction": (current or {}).get("direction")}
        return ActResult(facts=facts, intent="KEPT", engine_ref={"kept": True, "vision": current}, close=True)
    raise InboxError(400, f"unknown vision option {option_id}")


DRAFT_WORDS = (
    ("direction", (("CONTEND", ("contend", "win now", "go for it", "all in", "compete now")),
                   ("RETOOL", ("retool", "reset", "stay competitive")),
                   ("REBUILD", ("rebuild", "blow it up", "tear it down", "start over", "youth movement")))),
    ("emphasis", (("OFFENSE", ("offense", "offence", "attack", "passing game")),
                  ("DEFENSE", ("defense", "defence", "stop the run", "secondary")),
                  ("BALANCED", ("balanced", "both sides")))),
    ("playing_time", (("YOUTH", ("youth", "young", "kids", "rookies")),
                      ("VETERANS", ("veteran", "vets")),
                      ("MERIT", ("merit", "earn it", "best players")))),
)


def draft(cur, thread, text, llm=None):
    """Typed direction text -> a PROPOSE draft JR confirms (FRONT_OFFICE_INBOX_PLAN D10): "let's rebuild and lean
    on defense with young players" -> REBUILD / DEFENSE / YOUTH. No direction in the text means no draft; the
    emphasis and playing time fall back to the pinned vision, then to the engine defaults."""
    low = str(text or "").lower()
    if not low.strip():
        return None
    payload = {key: next((value for value, words in choices if any(w in low for w in words)), None)
               for key, choices in DRAFT_WORDS}
    if payload["direction"] is None:
        return None
    current = team_vision.current(cur, thread["team_id"], thread["season"]) or {}
    payload["emphasis"] = payload["emphasis"] or current.get("emphasis") or "BALANCED"
    payload["playing_time"] = payload["playing_time"] or current.get("playing_time") or "MERIT"
    return {"option_id": "PROPOSE", "payload": payload,
            "summary": f"Propose a vision: {payload['direction']}, lean {payload['emphasis'].lower()}, "
                       f"{payload['playing_time'].lower()} players."}


def template(intent, facts):
    team = facts.get("team") or "this team"
    if intent == "SEASON_VISION":
        allowed = (facts.get("allowed") or {}).get("direction") or []
        options = ", ".join(allowed) if allowed else "CONTEND, RETOOL or REBUILD"
        return (f"Before the season gets away from us: where is {team} headed? The numbers point to "
                f"{facts.get('engine_direction') or 'RETOOL'}. I can sign off on {options}.")
    if intent == "DIRECTION_SHIFT":
        return (f"The numbers have moved: {team} looks like {facts.get('engine_direction')} now, but we agreed "
                f"to {facts.get('pinned')}. Let's talk about where we're headed.")
    if intent == "OPEN":
        return f"Let's settle the vision for {team}: direction, where we lean, and who plays."
    if intent == "AGREED":
        return (f"Then we're agreed: {facts.get('direction')}, lean {str(facts.get('emphasis') or '').lower()}, "
                f"and {str(facts.get('playing_time') or '').lower()} players. I'll work to that.")
    if intent == "COUNTERED":
        reason = (facts.get("reasons") or ["it's further than I can go"])[0]
        return (f"I can't go that far: {reason}. I could do {facts.get('direction')}, "
                f"{str(facts.get('emphasis') or '').lower()}, {str(facts.get('playing_time') or '').lower()}.")
    if intent == "REFUSED":
        reason = (facts.get("reasons") or ["it isn't allowed"])[0]
        return f"I can't sign off on that direction: {reason}. Let's keep it where it is."
    if intent == "KEPT":
        return "Understood. We'll stay the course."
    return f"{team} needs a vision. Let's talk."


def start(cur, team, body):
    actor = team.get("actor")
    if actor is None:
        raise InboxError(400, "start a vision talk as your coach or GM")
    if _actor(team) is None:                 # the scan skips this case; a JR start must refuse it too
        raise InboxError(400, "you control both the coach and the GM, so there is no vision talk to have")
    ctype, cid, cname = _counterpart(cur, team["team_id"], actor.role)
    if ctype is None:
        raise InboxError(400, "there is no separate coach and GM to agree with")
    season, week = team["season"], team["week"]
    ctx = team_vision.context(cur, team["team_id"], season, week)
    vision = team_vision.current(cur, team["team_id"], season)
    facts = {"team": team.get("abbr"), "counterpart": cname, "engine_direction": ctx["engine_band"],
             "allowed": team_vision.allowed(ctx["scores_row"], ctx.get("cap_space")),
             "pinned": (vision or {}).get("direction")}
    return _trigger(team, ctype, cid, cname, JR_SOURCE.format(season=season, week=week), facts, "OPEN", season,
                    actor)
