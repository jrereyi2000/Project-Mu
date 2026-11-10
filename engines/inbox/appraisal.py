"""
Relationship events from the inbox (FRONT_OFFICE_INBOX_PLAN.md §2.5; PERSONALITY_RELATIONSHIPS_PLAN.md §0b, §2.2).

The relationships plan owns trust (D15). The inbox never writes persona_relationships and never computes a trust
number: it records what happened as that plan's canonical events, and its reviewed policy turns them into one-way
trust (D17). Until personality/relationship_store.py exists the events wait in the outbox table
inbox_relationship_events (PENDING), already in the §2.2 envelope.

  allowed(view) -> bool                       may this counterpart appraise? (persona_view's dict)
  interaction_event(thread, message, actor, counterpart, appraisal, at) -> event    meaningful_interaction
  treatment_event(thread, message, actor, counterpart, appraisal, at) -> event      treatment (disrespect)
  exchange_event(...) -> event | None         one source gives one event: treatment when mistreated, else interaction
  promise_made(thread, coach, player, criterion, due_at, ...) -> event
  promise_resolved(thread, promise, chart_row, ...) -> event                        kept | broken | unknown
  record(cur, event, ...) -> id               outbox insert, idempotent on source_event_id
  flush(cur, ...) -> dict                     append PENDING events through the relationships store when it exists

Identity. Before flush, every PersonRef is {"kind": "person", "universe_id": None, "role", "persona_id": None,
"_alias": {role, namespace, external_id}} and every TeamRef {"kind": "team", "universe_id": None, "team_key":
"team:<teams.id>"}. Aliases follow the relationship producers' namespaces: db-gm (gm_profiles.id), db-coach
(coaches.id), db-player (players.id), agent-memory (agent_memories.agent_id). flush resolves each through
personality.store.resolve_alias, fills universe_id and persona_id, and drops "_alias".

Time. occurred_at is {season, tick} on the producers' calendar (weeks 1-23, offseason stops 24-32); the outbox
also keeps {season, week, offseason_stage}. A tick that can't be computed stays None and the row waits at flush.
"""
from contextlib import contextmanager
from copy import deepcopy
import importlib
import json
import uuid

from psycopg.types.json import Jsonb
from engines.inbox.helpers import regclass as _regclass

RELATIONSHIP_SCHEMA_VERSION = "relationships-v1"
SOURCE_KIND = "inbox"
RELATIONSHIP_STORE = "personality.relationship_store"
STORE_TABLE = "persona_relationship_events"          # the store's table when the module names none (EVENTS_TABLE)
EVENT_KINDS = ("meaningful_interaction", "treatment", "promise_made", "promise_resolved")
OUTCOMES = ("positive", "negative", "mixed")
SIGNIFICANCES = ("routine", "meaningful")
RELATIONSHIP_KIND = {"positive": "support", "mixed": "collaboration", "negative": "conflict"}

ROLE_ALIAS = {      # inbox type / actor role -> (personality role, alias namespace)
    "gm": ("gm", "db-gm"), "GM": ("gm", "db-gm"),
    "coach": ("coach", "db-coach"), "COACH": ("coach", "db-coach"),
    "player": ("player", "db-player"), "PLAYER": ("player", "db-player"),
    "writer": ("writer", "agent-memory"),
    "owner": ("owner", "db-owner"), "scout": ("scout", "db-scout"),
}


# ------------------------------------------------------------------------------------------------- allowed ---

def allowed(view):
    """True for a counterpart with a loaded profile or persona: GMs, writers, players (generated profiles) and
    reviewed coaches. False for anyone whose profile isn't loaded (neutral), for 'unknown' identities, and for real
    coaches until their reviewed profiles exist (PERSONALITY_PLAN D11). False -> no appraisal, no interaction."""
    if not view or view.get("status") not in ("stored", "legacy"):
        return False
    if view.get("origin") == "unknown":
        return False
    if view.get("role") == "coach" and view.get("origin") == "real" and not view.get("reviewed"):
        return False
    return True


def normalize(appraisal):
    """The model's appraisal as {outcome, significance, mistreated, reason}, or None when it isn't usable."""
    if not isinstance(appraisal, dict):
        return None
    outcome = str(appraisal.get("outcome") or "").strip().lower()
    significance = str(appraisal.get("significance") or "routine").strip().lower()
    reason = appraisal.get("reason")
    mistreated = appraisal.get("mistreated", False)
    if isinstance(mistreated, str):
        mistreated = mistreated.strip().lower() in ("true", "yes", "1")
    if outcome not in OUTCOMES or significance not in SIGNIFICANCES:
        return None
    if not isinstance(reason, str) or not reason.strip():
        return None
    return {"outcome": outcome, "significance": significance, "mistreated": bool(mistreated),
            "reason": reason.strip()[:300]}


# ---------------------------------------------------------------------------------------------- references ---

def person_ref(kind_or_role, external_id):
    role, namespace = ROLE_ALIAS[kind_or_role]
    return {"kind": "person", "universe_id": None, "role": role, "persona_id": None,
            "_alias": {"role": role, "namespace": namespace, "external_id": str(external_id)}}


def team_ref(team_id):
    return {"kind": "team", "universe_id": None, "team_key": f"team:{int(team_id)}"}


def _ref(x, default_type=None):
    """A PersonRef from an Actor, a {role|type, persona_id|id} dict or an existing ref."""
    if x is None:
        return None
    if isinstance(x, dict) and x.get("kind") == "person":
        return deepcopy(x)
    if isinstance(x, dict):
        role = x.get("role") or x.get("type") or default_type
        pid = x.get("persona_id", x.get("id"))
        return person_ref(role, pid)
    return person_ref(x.role, x.persona_id)


def actor_ref(thread):
    return person_ref(thread["actor_role"], thread["actor_id"])


def counterpart_ref(thread):
    return person_ref(thread["counterpart_type"], thread["counterpart_id"])


def sim_time(at):
    """{season, week, offseason_stage} from a dict or a (season, week[, stage]) tuple."""
    if isinstance(at, dict):
        return {"season": at.get("season"), "week": at.get("week"), "offseason_stage": at.get("offseason_stage")}
    at = tuple(at) + (None,) * (3 - len(at))
    return {"season": at[0], "week": at[1], "offseason_stage": at[2]}


def time_point(at):
    from engines.inbox.store import tick_of
    st = sim_time(at)
    return {"season": st["season"], "tick": tick_of(st["week"], st["offseason_stage"])}


def _envelope(*, kind, source_event_id, at, subject, target, actor, payload, refs, visibility="private",
              responsible=(), reason_code=None, confidence="medium"):
    return {
        "schema_version": RELATIONSHIP_SCHEMA_VERSION,
        "universe_id": None,
        "event_id": source_event_id,
        "source_event_id": source_event_id,
        "source_kind": SOURCE_KIND,
        "source_refs": [{"namespace": "mu-inbox", "key": r} for r in refs],
        "kind": kind,
        "occurred_at": time_point(at),
        "subject": subject,
        "target": target,
        "actor": actor,
        "basis": "verified_simulation",
        "confidence": confidence,
        "reviewer": None,
        "evidence_scope": [],
        "visibility": visibility,
        "attribution": {"responsible": list(responsible), "reason_code": reason_code, "evidence_refs": list(refs)},
        "payload": payload,
        "supersedes_source_event_id": None,
        "_sim_time": sim_time(at),
    }


# ------------------------------------------------------------------------------------------------- events ---

def message_source_id(message):
    return f"inbox:msg:{message['id']}"


def interaction_event(thread, message, actor, counterpart, appraisal, at):
    """meaningful_interaction: the counterpart's (subject) appraisal of how JR's persona (target = actor) treated
    it in this exchange. message: the counterpart's reply that carries the appraisal."""
    ap = normalize(appraisal)
    if ap is None:
        raise ValueError("an interaction event needs a valid appraisal")
    me = _ref(actor) if actor is not None else actor_ref(thread)
    them = _ref(counterpart) if counterpart is not None else counterpart_ref(thread)
    opp = f"inbox_message:{message['id']}"
    payload = {"relationship_kind": RELATIONSHIP_KIND[ap["outcome"]], "outcome": ap["outcome"],
               "significance": ap["significance"], "opportunity_ref": opp, "appraisal_reason": ap["reason"],
               "channel": "inbox", "inbox_kind": thread["kind"]}
    return _envelope(kind="meaningful_interaction", source_event_id=message_source_id(message), at=at,
                     subject=them, target=me, actor=deepcopy(me), payload=payload,
                     refs=[opp, f"inbox_thread:{thread['id']}"])


def treatment_event(thread, message, actor, counterpart, appraisal, at):
    """treatment (disrespect): the counterpart felt mistreated by JR's persona (rude, dismissive, insulting)."""
    ap = normalize(appraisal)
    if ap is None:
        raise ValueError("a treatment event needs a valid appraisal")
    me = _ref(actor) if actor is not None else actor_ref(thread)
    them = _ref(counterpart) if counterpart is not None else counterpart_ref(thread)
    opp = f"inbox_message:{message['id']}"
    payload = {"team_key": team_ref(thread["team_id"])["team_key"], "treatment_kind": "disrespect",
               "outcome": "negative", "significance": ap["significance"], "outcome_refs": [opp],
               "appraisal_reason": ap["reason"], "channel": "inbox", "inbox_kind": thread["kind"]}
    return _envelope(kind="treatment", source_event_id=message_source_id(message), at=at, subject=them,
                     target=me, actor=deepcopy(me), payload=payload, refs=[opp, f"inbox_thread:{thread['id']}"],
                     responsible=[deepcopy(me)], reason_code="inbox_disrespect")


def exchange_event(thread, message, actor, counterpart, appraisal, at):
    """The one event an appraised exchange gives: treatment when mistreated, else meaningful_interaction; None
    without a usable appraisal (no model, no appraisal)."""
    ap = normalize(appraisal)
    if ap is None:
        return None
    build = treatment_event if ap["mistreated"] else interaction_event
    return build(thread, message, actor, counterpart, ap, at)


def promise_id_for(thread, n):
    return f"inbox:{thread['id']}:{int(n)}"


def _promise_source(promise_id, suffix):
    tail = promise_id[len("inbox:"):] if promise_id.startswith("inbox:") else promise_id
    return f"inbox:promise:{tail}:{suffix}"


def promise_made(thread, coach, player, criterion, due_at, *, promise_id=None, description=None, at=None):
    """promise_made: the coach (issuer, target, actor) promised the player (beneficiary, subject) a measurable role:
    criterion {type: 'depth_chart_slot', slot, max_depth}, due_at {season, week} (or a tick). Recorded as a fact,
    with or without a model."""
    if promise_id is None:
        promise_id = promise_id_for(thread, len((thread.get("state") or {}).get("promises") or []) + 1)
    issuer = _ref(coach, "COACH") if coach is not None else actor_ref(thread)
    beneficiary = _ref(player, "PLAYER") if player is not None else counterpart_ref(thread)
    crit = {"type": criterion.get("type", "depth_chart_slot"), "slot": criterion["slot"],
            "max_depth": int(criterion["max_depth"])}
    due = time_point(due_at) if "tick" not in due_at else {"season": due_at["season"], "tick": due_at["tick"]}
    desc = description or f"{crit['slot']} depth {crit['max_depth']} or better by week {due_at.get('week', due['tick'])}"
    payload = {"promise_id": promise_id, "issuer": deepcopy(issuer), "beneficiary": deepcopy(beneficiary),
               "terms": {"kind": "role", "description": desc, "criterion": crit}, "due_at": due,
               "authority_ref": f"inbox_thread:{thread['id']}"}
    return _envelope(kind="promise_made", source_event_id=_promise_source(promise_id, "made"),
                     at=at if at is not None else (thread["season"], thread["week"], thread.get("offseason_stage")),
                     subject=beneficiary, target=issuer, actor=deepcopy(issuer), payload=payload,
                     refs=[f"inbox_thread:{thread['id']}"], visibility="private", confidence="high")


def resolve(promise, chart_row):
    """'kept' | 'broken' | 'unknown': no chart read at the due week (or no depth for him) is unknown, never broken."""
    if not chart_row or chart_row.get("depth") is None:
        return "unknown"
    return "kept" if int(chart_row["depth"]) <= int(promise["max_depth"]) else "broken"


def promise_resolved(thread, promise, chart_row, *, at=None, outcome_refs=()):
    """promise_resolved from the actual depth chart at the due week. promise: {promise_id, slot, max_depth,
    due_season, due_week}; chart_row: {depth, source, season, week} or None."""
    resolution = resolve(promise, chart_row)
    chart_row = chart_row or {}
    payload = {"promise_id": promise["promise_id"], "resolution": resolution,
               "criterion_result": {"slot": promise["slot"], "depth_at_due": chart_row.get("depth"),
                                    "chart_source": chart_row.get("source"), "chart_season": chart_row.get("season"),
                                    "chart_week": chart_row.get("week")},
               "outcome_refs": list(outcome_refs), "beneficiary_ack_ref": None}
    issuer, beneficiary = actor_ref(thread), counterpart_ref(thread)
    when = at if at is not None else (promise.get("due_season"), promise.get("due_week"))
    return _envelope(kind="promise_resolved", source_event_id=_promise_source(promise["promise_id"], "resolved"),
                     at=when, subject=beneficiary, target=issuer, actor=deepcopy(issuer), payload=payload,
                     refs=[f"inbox_thread:{thread['id']}"], confidence="high" if resolution != "unknown" else "low")


# -------------------------------------------------------------------------------------------------- outbox ---

@contextmanager
def _savepoint(cur):
    name = "inbox_" + uuid.uuid4().hex
    cur.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
        cur.execute(f"RELEASE SAVEPOINT {name}")
        raise
    else:
        cur.execute(f"RELEASE SAVEPOINT {name}")


def record(cur, event, *, thread_id=None, message_id=None):
    """Insert the event into the outbox (PENDING). Idempotent on source_event_id: a second record returns the
    first row's id and changes nothing."""
    if event.get("kind") not in EVENT_KINDS:
        raise ValueError(f"unsupported inbox event kind {event.get('kind')!r}")
    ev = deepcopy(event)
    st = ev.pop("_sim_time", None) or {"season": (ev.get("occurred_at") or {}).get("season"), "week": None,
                                       "offseason_stage": None}
    cur.execute("INSERT INTO inbox_relationship_events (source_event_id, kind, thread_id, message_id, event, "
                "sim_time) VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (source_event_id) DO NOTHING RETURNING id",
                (ev["source_event_id"], ev["kind"], thread_id, message_id, Jsonb(ev), Jsonb(st)))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("SELECT id FROM inbox_relationship_events WHERE source_event_id = %s", (ev["source_event_id"],))
    return cur.fetchone()[0]


def _relationship_store():
    try:
        return importlib.import_module(RELATIONSHIP_STORE)
    except ModuleNotFoundError as exc:
        if exc.name in (RELATIONSHIP_STORE, "personality"):
            return None
        raise


def _universe(cur, rel_store):
    getter = getattr(rel_store, "universe_id", None)
    if callable(getter):
        try:
            return getter(cur)
        except Exception:
            return None
    try:
        from db.franchises import active_entry
        _, entry = active_entry()
        return (entry or {}).get("universe_id")
    except Exception:
        return None


def resolve_refs(cur, event, universe_id, resolver=None):
    """(event with universe_id and persona_ids filled, None) or (None, reason)."""
    if resolver is None:
        from personality.store import resolve_alias as resolver
    if (event.get("occurred_at") or {}).get("tick") is None:
        return None, "tick_missing"
    ev = deepcopy(event)
    ev["universe_id"] = universe_id
    cache, missing = {}, []

    def walk(v):
        if isinstance(v, dict):
            if v.get("kind") in ("person", "team") and "universe_id" in v:
                v["universe_id"] = universe_id
            alias = v.pop("_alias", None) if v.get("kind") == "person" else None
            if alias is not None:
                key = (alias["role"], alias["namespace"], alias["external_id"])
                if key not in cache:
                    cache[key] = resolver(cur, universe_id, *key)
                ident = cache[key]
                if ident is None:
                    missing.append(":".join(key))
                else:
                    v["persona_id"] = ident.persona_id
            for child in v.values():
                walk(child)
        elif isinstance(v, list):
            for child in v:
                walk(child)
    walk(ev)
    if missing:
        return None, "unresolved " + ", ".join(sorted(set(missing)))
    return ev, None


def _set_reason(cur, row_id, reason):
    cur.execute("UPDATE inbox_relationship_events SET status_reason = %s WHERE id = %s", (reason, row_id))


def flush(cur, *, rel_store=None, universe_id=None, team_registry=None, resolver=None):
    """Append PENDING outbox events through personality.relationship_store.append_events when that module imports
    and its table exists; otherwise a no-op. Rows whose refs don't resolve (or whose append conflicts) stay
    PENDING with status_reason. Returns {"status", "appended", "pending"}."""
    rel_store = rel_store if rel_store is not None else _relationship_store()
    if rel_store is None:
        return {"status": "relationship_store_missing", "appended": 0, "pending": None}
    if not _regclass(cur, getattr(rel_store, "EVENTS_TABLE", STORE_TABLE)):
        return {"status": "relationship_tables_missing", "appended": 0, "pending": None}
    if not _regclass(cur, "inbox_relationship_events"):
        return {"status": "outbox_missing", "appended": 0, "pending": None}
    cur.execute("SELECT id, source_event_id, kind, event FROM inbox_relationship_events WHERE status = 'PENDING' "
                "ORDER BY id FOR UPDATE")
    rows = [{"id": r[0], "source_event_id": r[1], "kind": r[2],
             "event": json.loads(r[3]) if isinstance(r[3], str) else r[3]} for r in cur.fetchall()]
    if not rows:
        return {"status": "ok", "appended": 0, "pending": 0}
    universe_id = universe_id or _universe(cur, rel_store)
    if universe_id is None:
        for r in rows:
            _set_reason(cur, r["id"], "universe_unregistered")
        return {"status": "universe_unregistered", "appended": 0, "pending": len(rows)}
    ready = []
    for r in rows:
        ev, why = resolve_refs(cur, r["event"], universe_id, resolver)
        if ev is None:
            _set_reason(cur, r["id"], why)
        else:
            ready.append((r, ev))
    if not ready:
        return {"status": "ok", "appended": 0, "pending": len(rows)}
    try:
        with _savepoint(cur):
            head = rel_store.read_head(cur, universe_id)
            receipt = rel_store.append_events(cur, [ev for _, ev in ready], expected_head=head,
                                              team_registry=team_registry if team_registry is not None else {})
    except Exception as e:                                       # a head conflict or a rejected batch: retry later
        for r, _ in ready:
            _set_reason(cur, r["id"], f"append failed: {e}"[:500])
        return {"status": "conflict", "appended": 0, "pending": len(rows), "error": str(e)}
    refused = {}
    for item in (receipt or {}).get("rejected", []) if isinstance(receipt, dict) else []:
        refused[item.get("source_event_id")] = item.get("reason") or "rejected by the relationships store"
    appended = 0
    for r, ev in ready:
        if r["source_event_id"] in refused:
            _set_reason(cur, r["id"], refused[r["source_event_id"]])
            continue
        cur.execute("UPDATE inbox_relationship_events SET status = 'APPENDED', status_reason = NULL, "
                    "appended_at = now(), event = %s WHERE id = %s", (Jsonb(ev), r["id"]))
        appended += 1
    return {"status": "ok", "appended": appended, "pending": len(rows) - appended, "receipt": receipt}
