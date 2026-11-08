"""
CONTRACT: JR's GM <-> one of his own players, extensions and re-signings (FRONT_OFFICE_INBOX_PLAN.md §2.8).

Inbound EXTENSION_WINDOW: a starter on the active roster becomes extension-eligible with no thread for him this
season. JR's options are MAKE_OFFER (a `contract_terms` form), ACCEPT_COUNTER (only while the player's counter is on
the table) and WALK_AWAY. The engine is free_agency_engine.offer_utility / acceptance, reached through
server/franchise_api.py's `_context`, `_player`, `_eligibility`, `_score` and `insert_offer` (the plan's R4: a lazy
import, that module is untouched). The player's words are the inbox voice's; the engine's ACCEPT / COUNTER / REJECT
decision is in the fact sheet.

An ACCEPTED offer is stored in contract_offers and closes the thread. The league office
(pipeline/league_office.py step_accepted_offers) writes the contract; this kind never writes one.
"""
from engines.inbox import InboxError
from engines.inbox.registry import ActResult, Trigger

KIND = "CONTRACT"
COUNTERPART_TYPE = "player"
ACTOR_ROLES = ("GM",)
CAN_START = True
START_FORM = "player_pick"          # wave 4: the new-conversation picker's form
VOICE = "a professional football player talking privately with the general manager about your own contract"
MAX_YEARS = 5                       # franchise_api.MAX_OFFER_YEARS
OUT_WEEKS = 3                       # a starter out this long (or on IR) doesn't count, as gm_direction does

INTENTS = {"ACCEPT": "ACCEPTED", "COUNTER": "COUNTER", "REJECT": "REJECTED"}


# ----------------------------------------------------------------------------------------------- helpers ---

def _fa():
    """server.franchise_api, imported lazily (R4): importing the API module is not free and it owns the helpers."""
    from server import franchise_api
    return franchise_api


def _call(fn, *args, **kwargs):
    """Call a franchise_api helper, mapping its HTTP refusal to the inbox's error."""
    from fastapi import HTTPException
    try:
        return fn(*args, **kwargs)
    except HTTPException as e:
        raise InboxError(e.status_code, str(e.detail)) from None


def _abbr(cur, team_id):
    cur.execute("SELECT abbreviation FROM teams WHERE id = %s", (team_id,))
    row = cur.fetchone()
    return str(row[0]).upper() if row and row[0] else None


def _terms(payload):
    """The `contract_terms` form payload -> (years, total, bonus, guaranteed), whole dollars."""
    def whole(key, default=None):
        value = payload.get(key, default)
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            raise InboxError(400, f"{key} must be a whole number of dollars") from None

    years = whole("years")
    total = whole("total_value", whole("total"))
    bonus = whole("bonus", 0)
    guaranteed = whole("guaranteed", 0)
    if years is None or total is None:
        raise InboxError(400, "an offer needs years and a total value")
    if not 1 <= years <= MAX_YEARS:
        raise InboxError(400, f"years must be 1-{MAX_YEARS}")
    if total <= 0 or bonus < 0 or guaranteed < 0:
        raise InboxError(400, "total_value must be positive; bonus and guaranteed can't be negative")
    if bonus > total or guaranteed > total:
        raise InboxError(400, "bonus and guaranteed can't exceed total_value")
    return years, total, bonus, guaranteed


def _active(p):
    """An active-roster player: a contract, not on IR."""
    return bool(p.get("contract")) and not bool((p.get("injury") or {}).get("on_ir"))


def _injured(p):
    inj = p.get("injury") or {}
    return bool(inj.get("on_ir")) or int(inj.get("weeks_out") or 0) >= OUT_WEEKS


def _starter_ids(players):
    """The team's starters by gm_direction's own slot counts (the same definition its needs use)."""
    from engines.gm_direction import position_group, starter_slots
    rows = []
    for p in players:
        if not p.get("contract") or _injured(p):
            continue
        group = position_group(p.get("position"))
        if group:
            rows.append({"id": p["player_id"], "group": group, "ovr": p.get("overall"), "age": p.get("age")})
    slots = starter_slots(rows)
    return {p["id"] for group, group_rows in slots.items() if group != "_depth" for p in group_rows}


def _contract_kind(p, ctx):
    """RESIGN for a deal expiring at the league year, else EXTENSION."""
    c = p.get("contract") or {}
    start, length = p.get("start"), c.get("length")
    if start is None or length is None:
        return "EXTENSION"
    return "RESIGN" if int(start) + int(length) - 1 <= int(ctx["T"]) else "EXTENSION"


def _facts(p, ctx, reason, extra=None):
    c = p.get("contract") or {}
    years_left = None
    if c.get("length") is not None and c.get("current_year") is not None:
        years_left = max(0, int(c["length"]) - int(c["current_year"]))
    facts = {"player": p.get("name"), "team": ctx["team"]["abbr"], "position": p.get("position"),
             "age": p.get("age"), "overall": p.get("overall"), "years_left": years_left,
             "eligible_reason": reason, "contract_kind": _contract_kind(p, ctx)}
    facts.update(extra or {})
    return facts


def _score(cur, ctx, p, terms):
    """(score, decision, counter, depth, bar) from franchise_api's shared free-agency scoring."""
    years, total, bonus, guaranteed = terms
    teammates = ctx["roster"]["ACTIVE"] + ctx["roster"]["IR"]
    return _call(_fa()._score, cur, ctx, p, years, total, bonus, guaranteed, teammates)


def _insert(cur, ctx, p, terms, score, decision, counter):
    """Store the offer in contract_offers; the league office writes an ACCEPTED one."""
    fa = _fa()
    years, total, bonus, guaranteed = terms
    kind = _contract_kind(p, ctx)
    return _call(fa.insert_offer, cur, ctx["season"], fa.stage_for(ctx["week"]), kind, p["player_id"],
                 ctx["team"]["id"], years, total, bonus, guaranteed, score["utility"],
                 fa.STATUS[decision], counter, fa._seed(ctx))


def _result(intent, facts, engine_ref, close, state, judge=True):
    return ActResult(facts=facts, intent=intent, engine_ref=engine_ref, close=close, state=state, judge=judge)


# -------------------------------------------------------------------------------------------------- scan ---

def scan(cur, season, week, team):
    """EXTENSION_WINDOW for each eligible starter of this team with no thread for him this season."""
    from engines.inbox import store
    fa = _fa()
    try:
        ctx = _call(fa._context, cur, team["abbr"])
    except InboxError:
        return []
    starters = _starter_ids(ctx.get("players") or [])
    out = []
    for p in ctx.get("players") or []:
        if not _active(p) or p.get("player_id") not in starters:
            continue
        try:
            ok, reason = _call(fa._eligibility, p, ctx)
        except InboxError:
            continue
        if not ok:
            continue
        source_ref = f"contract:{p['player_id']}:{season}"
        if store.find_thread(cur, team["team_id"], KIND, source_ref) is not None:
            continue
        out.append(Trigger(KIND, team["team_id"], COUNTERPART_TYPE, str(p["player_id"]), p["name"],
                           f"{p['name']}'s contract", source_ref, _facts(p, ctx, reason),
                           "EXTENSION_WINDOW", expires_season=season, actor_role="GM"))
    return out


def start(cur, team, body):
    """A JR-started extension talk about one player (body.player_id)."""
    fa = _fa()
    player_id = body.get("player_id")
    if player_id is None:
        raise InboxError(400, "pick a player")
    ctx = _call(fa._context, cur, team["abbr"])
    p = _call(fa._player, ctx, int(player_id))
    if not _active(p):
        raise InboxError(409, f"{p['name']} isn't on the active roster")
    ok, reason = _call(fa._eligibility, p, ctx)
    if not ok:
        raise InboxError(409, reason)
    season = ctx["season"]
    source_ref = f"contract:{p['player_id']}:{season}"
    return Trigger(KIND, team["team_id"], COUNTERPART_TYPE, str(p["player_id"]), p["name"],
                   f"{p['name']}'s contract", source_ref, _facts(p, ctx, reason),
                   "EXTENSION_WINDOW", expires_season=season, actor_role="GM")


# ----------------------------------------------------------------------------------------------- options ---

def options(cur, thread, last):
    state = thread.get("state") or {}
    out = [{"id": "MAKE_OFFER", "label": "Make an offer", "form": "contract_terms", "needs_note": False,
            "default": True}]
    if state.get("counter"):
        out.append({"id": "ACCEPT_COUNTER", "label": "Accept his counter", "form": None, "needs_note": False,
                    "default": False})
    out.append({"id": "WALK_AWAY", "label": "Walk away", "form": None, "needs_note": False, "default": False})
    return out


# --------------------------------------------------------------------------------------------------- act ---

def act(cur, thread, option_id, payload, note):
    state = dict(thread.get("state") or {})
    fa = _fa()
    ctx = _call(fa._context, cur, _abbr(cur, thread["team_id"]))
    player_id = int(thread["counterpart_id"])
    p = _call(fa._player, ctx, player_id)

    if option_id == "WALK_AWAY":
        facts = _facts(p, ctx, "JR ended the talks", {"reply": "WALK_AWAY"})
        return _result("WALKED_AWAY", facts, {"player_id": player_id}, True, state)

    if option_id not in ("MAKE_OFFER", "ACCEPT_COUNTER"):
        raise InboxError(400, f"unknown CONTRACT option {option_id!r}")
    if not _active(p):
        raise InboxError(409, f"{p['name']} isn't on the active roster")
    ok, reason = _call(fa._eligibility, p, ctx)
    if not ok:
        raise InboxError(409, reason)

    if option_id == "ACCEPT_COUNTER":
        counter = state.get("counter")
        if not counter:
            raise InboxError(409, "there's no counter-offer on the table")
        terms = _terms(counter)
    else:
        terms = _terms(payload)

    score, decision, new_counter, depth, bar = _score(cur, ctx, p, terms)
    oid = _insert(cur, ctx, p, terms, score, decision, new_counter)
    intent = INTENTS.get(decision, decision)
    facts = _facts(p, ctx, reason, {
        "decision": decision, "offer_id": oid, "counter": new_counter,
        "years": terms[0], "total_value": terms[1], "bonus": terms[2], "guaranteed": terms[3]})
    new_state = dict(state, offer_id=oid, counter=new_counter if decision == "COUNTER" else None,
                     phase="AGREED" if decision == "ACCEPT" else "TALKS")
    engine_ref = {"offer_id": oid, "decision": decision, "kind": _contract_kind(p, ctx)}
    if new_counter:
        engine_ref["counter"] = new_counter
    return _result(intent, facts, engine_ref, decision == "ACCEPT", new_state, judge=True)


# ---------------------------------------------------------------------------------------------- template ---

def _money(value):
    from engines.inbox.parse import money
    return money(value)


def _counter_line(counter):
    if not counter:
        return ""
    return (f" I'm looking for {counter.get('years')} years, {_money(counter.get('total_value'))} total, "
            f"{_money(counter.get('guaranteed'))} guaranteed.")


def template(intent, facts):
    player = facts.get("player") or "Your player"
    if intent == "EXTENSION_WINDOW":
        return (f"{player}: I've earned a real role here and I'd like to be paid like it. Can we talk about an "
                f"extension?")
    if intent == "ACCEPTED":
        return f"{player}: That works for me. I'm glad to stay."
    if intent == "COUNTER":
        c = facts.get("counter") or {}
        return f"{player}: I want to stay, but the offer needs to be better.{_counter_line(c)}"
    if intent == "REJECTED":
        return f"{player}: That's not close to what I'm worth. I'll keep playing and we can revisit it."
    if intent == "WALKED_AWAY":
        return f"{player}: If that's where we are, I'll play out my deal and we can talk next time."
    return f"{player}: Let's talk about my contract."


# ------------------------------------------------------------------------------------------------- draft ---

def draft(cur, thread, text, llm=None):
    """Typed contract terms -> a MAKE_OFFER draft JR confirms (parse.draft_summary wording)."""
    from engines.inbox import parse
    parsed = parse.parse_contract_terms(text)
    if parsed is None and llm is not None:
        parsed = parse.parse_with_llm(llm, text, {
            "years": {"type": "number", "min": 1, "max": MAX_YEARS},
            "total": {"type": "number", "min": 1, "max": parse.MAX_CONTRACT_TOTAL},
            "guaranteed": {"type": "number", "min": 0, "max": parse.MAX_CONTRACT_TOTAL, "optional": True},
            "bonus": {"type": "number", "min": 0, "max": parse.MAX_CONTRACT_TOTAL, "optional": True},
        }, [])
    if not parsed:
        return None
    payload = {"years": int(parsed["years"]), "total_value": int(parsed["total"]),
               "bonus": int(parsed.get("bonus") or 0), "guaranteed": int(parsed.get("guaranteed") or 0)}
    return {"option_id": "MAKE_OFFER", "payload": payload,
            "summary": parse.draft_summary("MAKE_OFFER", parsed)}
