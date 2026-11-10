"""
GM_TALK: JR's GM <-> another team's GM, trades (FRONT_OFFICE_INBOX_PLAN.md §2.8; V2.11 extends this file).

Inbound TRADE_INTEREST: a PROPOSED ENGINE proposal (gm_trade_proposals) with a registry user team on one side and
no thread for it yet. JR opens talks with OPEN_TALKS (a `trade_assets` package) or turns the proposal down with
NOT_INTERESTED (front_office.reject). In talks he MAKES_OFFER, ACCEPTs the GM's counter or ENDs the talks. The
engine is gm_negotiation: open_negotiation / offer / accept_counter / close. The GM's words are the turn message
gm_negotiation writes (returned as the reply template); the inbox voice writes or rephrases it and, only when JR
wrote a note, judges how he was treated (judge = bool(note)). Nothing in the replies or engine_ref carries a
surplus: only ids, the action and the assets on the table do.

A negotiation agreed by the engine closes the thread. The deal is stored by gm_negotiation as an APPROVED USER
proposal and queued as a TRADE pending move for the league office (V2.11 §2.4/§2.7); the reply names that move.
The GM's reply has the inbox's 8 s model budget (`INBOX_LLM_BUDGET_S`), and every GM turn draws leaks through
gm_talks.leak_events, stored in gm_trade_leaks and never mentioned in the thread. This kind never writes a trade
itself.
"""
import json
import random
import re

from engines.inbox import InboxError
from engines.inbox.registry import ActResult, Trigger

KIND = "GM_TALK"
COUNTERPART_TYPE = "gm"
ACTOR_ROLES = ("GM",)
CAN_START = True
START_FORM = "team_pick"            # wave 4: the new-conversation picker's form
VOICE = "a rival NFL general manager: direct, competitive, protective of your roster and your reputation"

# engine action -> the decision the inbox voice must convey
INTENTS = {"ACCEPT": "AGREED", "COUNTER": "COUNTER", "REJECT": "REJECT", "WALK_AWAY": "WALK_AWAY"}


# ----------------------------------------------------------------------------------------------- helpers ---

def _negotiation():
    from engines import gm_negotiation
    return gm_negotiation


def _front_office():
    from engines import front_office
    return front_office


def _thread_history(cur, thread, limit=3):
    """The thread's last few stored lines, for the writer's repetition rules. Without them the GM only ever saw
    JR's current note and could repeat himself on every turn."""
    from engines.inbox import store
    out = []
    for m in store.messages(cur, thread["id"])[-limit:]:
        who = "JR" if m.get("author") == "JR" else (thread.get("counterpart_name") or "the GM")
        # JR's stored messages carry body_source "JR"; the writer's repetition rules only exempt "TEMPLATE" and
        # "USER", so his own words were treated as the GM's and could trip a false repetition.
        source = "USER" if m.get("author") == "JR" else (m.get("body_source") or "TEMPLATE")
        out.append({"message": f"{who}: {m.get('body') or ''}", "source": source})
    return out


def _engine_call(fn, *args, **kwargs):
    """Call an engine helper, mapping its refusal to the inbox's HTTP-shaped error (R2's mismatch text)."""
    from fastapi import HTTPException

    from engines.front_office import FrontOfficeError
    try:
        return fn(*args, **kwargs)
    except FrontOfficeError as e:
        raise InboxError(e.status, e.message) from None
    except HTTPException as e:
        raise InboxError(e.status_code, str(e.detail)) from None


def _llm_budget():
    """The inbox's reply-time budget (voice.INBOX_LLM_BUDGET_S, 8 s): the GM's turn is written within it."""
    from engines.inbox.voice import INBOX_LLM_BUDGET_S
    return INBOX_LLM_BUDGET_S


def _abbr(cur, team_id):
    cur.execute("SELECT abbreviation FROM teams WHERE id = %s", (team_id,))
    row = cur.fetchone()
    return str(row[0]).upper() if row and row[0] else None


def _team_id(cur, value):
    """A team id from an abbreviation or a numeric id, or None."""
    if value is None:
        return None
    if isinstance(value, int) or str(value).isdigit():
        return int(value)
    cur.execute("SELECT id FROM teams WHERE abbreviation = %s", (str(value).upper(),))
    row = cur.fetchone()
    return int(row[0]) if row else None


def _gm(cur, team_id):
    """(gm_profiles.id, gm_name) of the team's open GM tenure, or (None, None)."""
    cur.execute("SELECT id, gm_name FROM gm_profiles WHERE team_id = %s AND end_season IS NULL "
                "ORDER BY start_season DESC, id DESC LIMIT 1", (team_id,))
    row = cur.fetchone()
    return (row[0], row[1]) if row else (None, None)


def _player_name(cur, player_id):
    cur.execute("SELECT player_name FROM players WHERE id = %s", (player_id,))
    row = cur.fetchone()
    return row[0] if row and row[0] else str(player_id)


def _asset_labels(cur, assets):
    """Readable labels for the assets of a proposal or counter (no values, no surpluses)."""
    out = []
    for x in assets or []:
        if x.get("type") == "PLAYER":
            out.append(_player_name(cur, x.get("player_id")))
        elif x.get("type") == "PICK":
            pick = x.get("pick")
            if pick:
                season, rnd, orig = pick
                out.append(f"{season} R{rnd} ({_abbr(cur, orig) or orig})")
            else:
                out.append(f"{x.get('draft_season')} R{x.get('round')} "
                           f"({x.get('original_team') or x.get('from')})")
    return out


def _legs(cur, user_abbr, gm_team_id, payload):
    """The U2 `trade_assets` form ({players: [id], picks: [{season, round, original_team}]}) as Front Office JSON
    assets, all sent by JR's team to the GM's team (the form has no direction; the GM counters for his own side)."""
    gm_abbr = _abbr(cur, gm_team_id)
    if gm_abbr is None:
        raise InboxError(400, "that team's GM isn't in the league")
    raw = []
    for pid in payload.get("players") or []:
        try:
            raw.append({"type": "PLAYER", "player_id": int(pid), "from": user_abbr, "to": gm_abbr})
        except (TypeError, ValueError):
            raise InboxError(400, f"unknown player {pid!r}") from None
    for pick in payload.get("picks") or []:
        try:
            season = int(pick.get("season", pick.get("draft_season")))
            rnd = int(pick["round"])
        except (TypeError, ValueError):
            raise InboxError(400, "a pick needs a season and a round") from None
        raw.append({"type": "PICK", "draft_season": season, "round": rnd,
                    "original_team": pick.get("original_team") or user_abbr, "from": user_abbr, "to": gm_abbr})
    return raw


def _facts(cur, thread, user_abbr, gm_team_id, extra=None):
    return dict({"gm": thread.get("counterpart_name") or "The GM", "team": user_abbr,
                 "other_team": _abbr(cur, gm_team_id)}, **(extra or {}))


# -------------------------------------------------------------------------------------------------- scan ---

def scan(cur, season, week, team):
    """TRADE_INTEREST for each PROPOSED ENGINE proposal involving this team with no thread yet."""
    from engines import gm_trade_engine as ge
    from engines.inbox import store
    tid = team["team_id"]
    try:
        proposals = ge.read_proposals(cur, season, ("PROPOSED",))
    except Exception:
        return []
    out = []
    for p in proposals:
        if p.get("origin") != "ENGINE":
            continue
        a, b = p.get("team_a_id"), p.get("team_b_id")
        if tid not in (a, b):
            continue
        other = b if a == tid else a
        source_ref = f"gm_talk:proposal:{p['id']}"
        if store.find_thread(cur, tid, KIND, source_ref) is not None:
            continue
        gm_id, gm_name = _gm(cur, other)
        if gm_id is None or _abbr(cur, other) is None:
            continue
        labels = _asset_labels(cur, p.get("assets") or [])
        facts = {"gm": gm_name, "team": team["abbr"], "other_team": _abbr(cur, other),
                 "proposal_id": p["id"], "headline": p.get("headline") or "a trade",
                 "assets": labels}
        out.append(Trigger(KIND, tid, COUNTERPART_TYPE, str(gm_id), gm_name,
                           (p.get("headline") or "A trade proposal")[:255], source_ref, facts, "TRADE_INTEREST",
                           link_table="gm_trade_proposals", link_id=p["id"],
                           expires_season=season, actor_role="GM",
                           state={"proposal_id": p["id"], "gm_team_id": other, "phase": "INBOUND"}))
    return out


def start(cur, team, body):
    """A conversation JR starts with another GM: an open talk thread in which his first MAKE_OFFER calls
    gm_negotiation. Opening the engine here would be a side effect before the store's cap check (a refused thread
    would leave an orphan negotiation), so the offer is made inside the thread."""
    user = team["abbr"]
    gm_team_id = _team_id(cur, body.get("gm_team") or body.get("gm_team_id"))
    if gm_team_id is None:
        raise InboxError(400, "pick the GM's team")
    if gm_team_id == team["team_id"]:
        raise InboxError(400, "that's your own team's front office: pick another team's GM")
    gm_id, gm_name = _gm(cur, gm_team_id)
    if gm_id is None:
        raise InboxError(400, f"{_abbr(cur, gm_team_id)} has no GM")
    # One open talk per GM, and a fresh ref per attempt: (team, kind, source_ref) is unique over closed threads too,
    # so a fixed ref made every later talk with that GM a permanent 409. Both reads are in SQL rather than over
    # store.list_threads, whose default page is 50 rows.
    from engines.inbox import Conflict
    base = f"gm_talk:start:{user}:{_abbr(cur, gm_team_id)}"
    cur.execute("SELECT id FROM inbox_threads WHERE team_id = %s AND kind = %s AND source_ref LIKE %s "
                "AND status IN ('OPEN','WAITING_JR') LIMIT 1", (team["team_id"], KIND, base + ":%"))
    if cur.fetchone():
        raise Conflict(f"a trade talk with {_abbr(cur, gm_team_id)} is already open")
    cur.execute("SELECT source_ref FROM inbox_threads WHERE team_id = %s AND kind = %s AND source_ref LIKE %s",
                (team["team_id"], KIND, base + ":%"))
    attempts = [int(str(r[0]).rsplit(":", 1)[-1]) for r in cur.fetchall()
                if str(r[0]).rsplit(":", 1)[-1].isdigit()]
    source_ref = f"{base}:{(max(attempts) + 1) if attempts else 1}"
    subject = str(body.get("topic") or body.get("note") or "Trade talks")[:120]
    facts = _facts(cur, {"counterpart_name": gm_name}, user, gm_team_id)
    return Trigger(KIND, team["team_id"], COUNTERPART_TYPE, str(gm_id), gm_name, subject, source_ref,
                   facts, "OPEN_TALKS", actor_role="GM",
                   state={"gm_team_id": gm_team_id, "phase": "TALKS"})


# ----------------------------------------------------------------------------------------------- options ---

def options(cur, thread, last):
    state = thread.get("state") or {}
    if state.get("phase") == "TALKS":
        out = [{"id": "MAKE_OFFER", "label": "Make an offer", "form": "trade_assets", "needs_note": False,
                "default": True}]
        if state.get("counter"):
            out.append({"id": "ACCEPT_COUNTER", "label": "Accept their counter", "form": None,
                        "needs_note": False, "default": False})
        out.append({"id": "END_TALKS", "label": "End the talks", "form": None, "needs_note": False,
                    "default": False})
        return out
    return [{"id": "OPEN_TALKS", "label": "Open talks", "form": "trade_assets", "needs_note": False,
             "default": True},
            {"id": "NOT_INTERESTED", "label": "Not interested", "form": None, "needs_note": False,
             "default": False}]


# --------------------------------------------------------------------------------------------------- act ---

def _counter_from(res):
    """The counter on the table as {assets, turn_no}, without any surplus, or None."""
    table = res.get("on_table")
    if not table:
        return None
    return {"assets": table.get("assets") or [], "turn_no": table.get("turn_no")}


def _move_id(cur, proposal_id):
    """The TRADE pending move the league office will write for an agreed deal (gm_talks.queue_trade_move), or None
    when the table/payload isn't there (older saves)."""
    if proposal_id is None:
        return None
    from engines.inbox import appraisal
    try:
        with appraisal._savepoint(cur):        # a failed read must not abort the caller's transaction
            cur.execute("SELECT to_regclass('franchise_pending_moves') IS NOT NULL")
            row = cur.fetchone()
            if not row or not row[0]:
                return None
            cur.execute("SELECT id, payload FROM franchise_pending_moves WHERE kind = 'TRADE' ORDER BY id DESC")
            for move_id, payload in cur.fetchall():
                if isinstance(payload, str):
                    try:
                        payload = json.loads(payload)
                    except ValueError:
                        continue
                if str((payload or {}).get("proposal_id")) == str(proposal_id):
                    return move_id
    except Exception:
        return None
    return None


def _leak_after(cur, res, thread, state):
    """Run the leak draw after a GM turn (V2.11 §2.7, Q4): gm_talks.record_leaks over the talk, stored in
    gm_trade_leaks by the engine's own writer, never mentioned in the thread. The transcript goes with it, so the
    first GM reply of a JR talk lands an OPENED leak. A draw that fails never breaks JR's turn."""
    from engines.inbox import appraisal
    try:
        turns = res.get("turns") or []
        turn = next((t for t in reversed(turns) if t.get("actor") == "GM"), None)
        if turn is None:
            return
        from engines import gm_talks
        talk = {"id": res.get("id"), "season": res.get("season") or thread.get("season"),
                "week": res.get("week") or thread.get("week"), "user_team_id": thread.get("team_id"),
                "gm_team_id": state.get("gm_team_id"), "status": res.get("status")}
        with appraisal._savepoint(cur):        # a failed draw must not abort JR's reply
            b, _ = _negotiation()._books(cur)
            rng = random.Random(f"leak:{talk['season']}:{talk['id']}:{turn.get('turn_no')}")
            gm_talks.record_leaks(cur, talk, turn, b, rng, turns, user_team_id=thread.get("team_id"))
    except Exception:
        return


def _from_result(cur, res, state, user_abbr):
    """(intent, facts, new state) from a gm_negotiation negotiation json."""
    status = res.get("status")
    turns = res.get("turns") or []
    last = turns[-1] if turns else {}
    action = last.get("action") if last.get("actor") == "GM" else status
    message = last.get("message") if last.get("actor") == "GM" else None
    counter = _counter_from(res)
    facts = {"gm": None, "team": user_abbr, "other_team": _abbr(cur, state.get("gm_team_id")),
             "status": status, "action": action, "gm_message": message,
             "counter": counter, "proposal_id": res.get("proposal_id") or state.get("proposal_id")}
    if status == "AGREED":
        facts["pending_move_id"] = _move_id(cur, facts["proposal_id"])
    new_state = dict(state, negotiation_id=res.get("id"), phase="TALKS", counter=counter,
                     proposal_id=facts["proposal_id"])
    intent = INTENTS.get(action) or status or "COUNTER"
    return intent, facts, new_state


def _closed(res):
    """The thread ends when the deal is agreed or the GM walks away (an OPEN negotiation keeps going)."""
    return res.get("status") in ("AGREED", "WALKED_AWAY", "CLOSED")


def _engine_ref(res):
    """The ids V2.11 §2.7 puts on the message: the GM's last turn's number and posture (never a surplus)."""
    turns = res.get("turns") or []
    last = next((t for t in reversed(turns) if t.get("actor") == "GM"), {})
    ref = {"negotiation_id": res.get("id"), "status": res.get("status"),
           "action": last.get("action"), "turn_no": last.get("turn_no"), "posture": last.get("posture")}
    if res.get("proposal_id") is not None:
        ref["proposal_id"] = res.get("proposal_id")
    return ref


def act(cur, thread, option_id, payload, note):
    state = dict(thread.get("state") or {})
    user = _abbr(cur, thread["team_id"])
    gm_team_id = state.get("gm_team_id")

    if option_id == "NOT_INTERESTED":
        pid = state.get("proposal_id")
        if pid is not None:
            _engine_call(_front_office().reject, cur, pid, "JR passed on the proposal")
        facts = _facts(cur, thread, user, gm_team_id, {"reply": "NOT_INTERESTED"})
        return ActResult(facts=facts, intent="NOT_INTERESTED", engine_ref={"proposal_id": pid}, close=True)

    if option_id == "END_TALKS":
        nid = state.get("negotiation_id")
        if nid is not None:
            _engine_call(_negotiation().close, cur, nid, note)
        facts = _facts(cur, thread, user, gm_team_id, {"reply": "END_TALKS"})
        return ActResult(facts=facts, intent="END_TALKS", engine_ref={"negotiation_id": nid}, close=True)

    if option_id == "ACCEPT_COUNTER":
        nid = state.get("negotiation_id")
        if nid is None or not state.get("counter"):
            raise InboxError(409, "there's no counter-offer on the table")
        res = _engine_call(_negotiation().accept_counter, cur, nid, timeout=_llm_budget())
        intent, facts, new_state = _from_result(cur, res, state, user)
        _leak_after(cur, res, thread, new_state)
        facts["gm"] = thread.get("counterpart_name") or facts.get("gm")
        return ActResult(facts=facts, intent=intent, engine_ref=_engine_ref(res),
                         close=_closed(res), state=new_state,
                         link=("gm_trade_negotiations", nid), judge=bool(note))

    if option_id in ("OPEN_TALKS", "MAKE_OFFER"):
        if gm_team_id is None:
            raise InboxError(400, "this conversation has no GM team")
        legs = _legs(cur, user, gm_team_id, payload)
        if not legs:
            raise InboxError(400, "an offer needs at least one player or pick")
        nid = state.get("negotiation_id")
        if nid is None:
            res = _engine_call(_negotiation().open_negotiation, cur, user, _abbr(cur, gm_team_id), legs, note,
                               timeout=_llm_budget(), history=_thread_history(cur, thread))
        else:
            res = _engine_call(_negotiation().offer, cur, nid, legs, note, timeout=_llm_budget(),
                               history=_thread_history(cur, thread))
        intent, facts, new_state = _from_result(cur, res, state, user)
        _leak_after(cur, res, thread, new_state)
        facts["gm"] = thread.get("counterpart_name") or facts.get("gm")
        return ActResult(facts=facts, intent=intent, engine_ref=_engine_ref(res),
                         close=_closed(res), state=new_state,
                         link=("gm_trade_negotiations", res.get("id")), judge=bool(note))

    raise InboxError(400, f"unknown GM_TALK option {option_id!r}")


# ---------------------------------------------------------------------------------------------- template ---

def template(intent, facts):
    gm = facts.get("gm") or "The GM"
    if intent in ("AGREED", "ACCEPT"):
        move = facts.get("pending_move_id")
        if move is not None:
            return f"{gm}: Agreed. Queued for the league office (move #{move})."
        return f"{gm}: Agreed. Queued for the league office."
    message = facts.get("gm_message")
    if message:
        return message
    if intent == "TRADE_INTEREST":
        what = facts.get("headline") or "a trade"
        return f"{gm}: We've done our homework on your roster and we want to talk about {what}."
    if intent == "OPEN_TALKS":
        return f"{gm}: I'm listening. What did you have in mind?"
    if intent == "NOT_INTERESTED":
        return f"{gm}: Understood. We'll look elsewhere, then."
    if intent == "END_TALKS":
        return f"{gm}: Fine. We'll pick this up another time."
    if intent == "COUNTER":
        return f"{gm}: Here's where we'd get it done. Take a look at the package."
    if intent == "REJECT":
        return f"{gm}: That doesn't work for us."
    if intent == "WALK_AWAY":
        return f"{gm}: We've gone back and forth enough. I'm moving on."
    return f"{gm}: Let's talk."


# ------------------------------------------------------------------------------------------------- draft ---

ROUND_WORDS = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7}


def _round_of(text):
    low = text.lower()
    for word, n in ROUND_WORDS.items():
        if re.search(rf"\b{word}\b", low):
            return n
    m = re.search(r"\br\s*([1-7])\b", low) or re.search(r"\b([1-7])(?:st|nd|rd|th)\b", low)
    return int(m.group(1)) if m else None


def draft(cur, thread, text, llm=None):
    """Match player names and picks in typed text against gm_negotiation.team_assets (the send side)."""
    if not text or not text.strip():
        return None
    state = thread.get("state") or {}
    user = _abbr(cur, thread["team_id"])
    gm_team_id = state.get("gm_team_id")
    if user is None or gm_team_id is None:
        return None
    try:
        assets = _negotiation().team_assets(cur, user, _abbr(cur, gm_team_id))
        mine = (assets.get("teams") or {}).get(user) or {}
    except Exception:
        return None
    low = text.lower()
    players, labels = [], []
    for p in mine.get("players") or []:
        if p.get("tradeable") is False or not p.get("name"):
            continue
        name = p["name"].lower()
        tokens = [t for t in name.split() if len(t) > 2]
        if name in low or any(t in low for t in tokens):
            players.append(int(p["player_id"]))
            labels.append(f"{p.get('pos') or '?'} {p['name']}")
    picks = []
    rnd = _round_of(text)
    seasons = set(re.findall(r"\b(20\d\d)\b", low))
    matched = []
    for pick in mine.get("picks") or []:
        if pick.get("booked"):
            continue
        season = pick.get("draft_season")
        if seasons and str(season) not in seasons:
            continue
        if rnd is not None and int(pick.get("round") or 0) != rnd:
            continue
        if rnd is None and not seasons:
            continue                       # a pick needs a season or a round to be recognised
        matched.append(pick)
    if not seasons and matched:            # "a second" means the nearest draft's second-rounder
        nearest = min(int(p.get("draft_season") or 0) for p in matched)
        matched = [p for p in matched if int(p.get("draft_season") or 0) == nearest]
    for pick in matched:
        picks.append({"season": pick.get("draft_season"), "round": pick.get("round"),
                      "original_team": pick.get("original_team")})
        labels.append(pick.get("label") or f"{pick.get('draft_season')} R{pick.get('round')}")
    if not players and not picks:
        return None
    option = "MAKE_OFFER" if state.get("phase") == "TALKS" else "OPEN_TALKS"
    return {"option_id": option, "payload": {"players": players, "picks": picks},
            "summary": "Offer: " + ", ".join(labels)}
