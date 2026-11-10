"""
How counterparts speak (FRONT_OFFICE_INBOX_PLAN.md §2.4).

The engine has already decided (a kind's `act`); this module only says it, in the counterpart's voice. One model call
returns {"message", "appraisal"} (plus any kind-specific keys, e.g. REPORTER's "done"); the appraisal is asked for
only when appraisal.allowed(view). The model's words are kept only when they pass gm_rationale.validate_rationale
against the fact sheet, the kind's optional validate(text, facts), and the intent check (no contradiction of the
decision). Otherwise, and on a timeout (INBOX_LLM_BUDGET_S), MU_INBOX_LLM=0 or no API key, the kind's template is
used, and a template reply carries no appraisal (no model, no appraisal).

  persona_view(cur, counterpart_type, counterpart_id) -> view     feature_view(profile, 'inbox'); missing -> neutral;
                                                                   GMs and writers fall back to persona_sliders/traits
  compose(cur, kind, thread, intent, facts, history, ...) -> Reply(text, source, appraisal, data)
        The kind's optional loop_reply(cur, thread, intent, facts, llm, history, note) is asked first when an llm
        is present; non-empty words are the reply (source LLM, no appraisal), else the voice below speaks.
  write(...) -> (text, 'LLM' | 'TEMPLATE', appraisal | None)       compose without the extra JSON keys
  exchange(cur, kind, thread_id, option_id, payload, note, ...) -> thread    one JR turn, in §2.4's order:
        act -> JR's line -> the counterpart's reply -> the relationship event (outbox). A null option with a
        note is talk (no engine act, no state/outcome change) unless the kind is in TYPED_TEXT_KINDS.
  default_llm()                                                    make_llm("inbox") unless MU_INBOX_LLM == '0'

The relationship to JR's persona enters the prompt only through the relationships plan's relationship_context
(once that module exists); before that the prompt has no relationship lines (D15, D17). Nothing here commits.
"""
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
import json
import os
import re
import time

from engines.inbox import InboxError, appraisal as appraisal_mod, store
from engines.inbox.helpers import regclass as _regclass

INBOX_LLM_BUDGET_S = 4.0          # P1-09 spike 2026-11-09: p50 1.99 s, p95 2.46 s, max 2.50 s
                                  # (gemini-3.8-flash, filled CONTRACT prompt, Madden open)
HISTORY_MESSAGES = 6
NOTE_MAX_CHARS = 600
NEUTRAL_VOICE = "plain and professional"
RELATIONSHIP_MODULE = "personality.relationships"      # relationship_context(state, *, target) once it exists

# Kinds whose act() reads typed text when no option is picked (FRONT_OFFICE_INBOX_PLAN §2.7: "a kind may read it
# otherwise"). Every other kind answers a null option as talk — no engine act.
TYPED_TEXT_KINDS = ("PROSPECT",)

# intent words -> phrases that contradict that decision (the intent check)
_CONTRADICTS = {
    "negative": re.compile(r"\b(we have a deal|it'?s a deal|you have a deal|i accept|we accept|i agree to|"
                           r"we agree to|deal is done|consider it done)\b", re.I),
    "positive": re.compile(r"\b(no deal|i decline|we decline|i refuse|we refuse|i reject|we reject|"
                           r"i can'?t accept|we can'?t accept|not interested)\b", re.I),
}
_NEGATIVE_INTENTS = ("REJECT", "REFUSE", "DECLINE", "WALK", "END", "NO_")
_POSITIVE_INTENTS = ("ACCEPT", "AGREE", "AGREED", "DEAL")


@dataclass
class Reply:
    text: str
    source: str                       # 'LLM' | 'TEMPLATE'
    appraisal: dict | None = None
    data: dict = field(default_factory=dict)       # the model's other JSON keys (e.g. {"done": true}); {} for templates


# ------------------------------------------------------------------------------------------------ the model ---

def _savepoint(cur, name):
    """True when the savepoint could be taken (a scripted cursor in tests cannot)."""
    try:
        cur.execute(f"SAVEPOINT {name}")
        return True
    except Exception:      # noqa: BLE001
        return False


def default_llm():
    """The inbox's model (make_llm("inbox")), or None for templates: MU_INBOX_LLM=0, no API key, or unavailable."""
    if os.environ.get("MU_INBOX_LLM", "1").strip() == "0":
        return None
    try:
        from engines.gm_llm import make_llm
        return make_llm("inbox")
    except Exception as e:
        print(f"[inbox] model unavailable, using templates: {e}", flush=True)
        return None


def call_with_budget(llm, prompt, budget_s=INBOX_LLM_BUDGET_S):
    """llm(prompt) within budget_s seconds; None on a timeout, an error or no llm. A slow call is abandoned (its
    thread finishes in the background) so the reply never waits past the budget."""
    if llm is None:
        return None
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="inbox-llm")
    try:
        future = pool.submit(llm, prompt)
        return future.result(timeout=budget_s)
    except FutureTimeout:
        print(f"[inbox] model past the {budget_s}s budget, using the template", flush=True)
        return None
    except Exception as e:
        print(f"[inbox] model call failed, using the template: {e}", flush=True)
        return None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def parse_json(raw):
    """The first JSON object in the model's answer, or None."""
    if not isinstance(raw, str):
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        data = json.loads(m.group(0) if m else raw)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------------------------- the persona ---

def _neutral(role):
    from newsroom.personas import SLIDERS
    return {"status": "missing", "role": role, "origin": None, "sliders": dict.fromkeys(SLIDERS, 5), "traits": []}


def _universe_id():
    try:
        from db.franchises import active_entry
        _, entry = active_entry()
        return (entry or {}).get("universe_id")
    except Exception:
        return None


def loop_league_id(cur, thread=None):
    """The league the agent loops key their switchboard on: a thread's own league_id, else
    league_state.madden_league_id, else the active franchise registry's key, else None."""
    lid = (thread or {}).get("league_id")
    if isinstance(lid, int) and not isinstance(lid, bool):
        return lid
    if isinstance(lid, str) and lid.strip().lstrip("-").isdigit():
        return int(lid)
    try:
        with appraisal_mod._savepoint(cur):
            cur.execute("SELECT madden_league_id FROM league_state WHERE id = 1")
            row = cur.fetchone()
            if row and row[0] is not None:
                return int(row[0])
    except Exception:          # no league_state, or a database without the column
        pass
    try:
        from db.franchises import active_entry
        key, _entry = active_entry()
        return int(key) if key is not None else None
    except Exception:
        return None


def loop_thread(thread):
    """A copy of the thread with a tick the loop can record at when the thread has no week (offseason)."""
    out = dict(thread or {})
    if out.get("week") is None and out.get("tick") is None:
        out["tick"] = 24
    return out


def _core_view(cur, role, namespace, external_id, universe_id):
    if not universe_id or not _regclass(cur, "persona_core_aliases"):
        return None
    from personality.core import feature_view
    from personality.store import read, resolve_alias
    ident = resolve_alias(cur, universe_id, role, namespace, str(external_id))
    profile = read(cur, ident) if ident is not None else None
    if profile is None:
        return None
    view = feature_view(profile, "inbox")
    # Core extensions D1 (views-v2): a stored real profile is a reviewed one and is read in full by every
    # feature; its evidence is provenance, not a per-feature gate. Real coaches without a stored profile
    # never reach here (they get the neutral view, reviewed=False).
    return dict(view, role=role, reviewed=True)


def _legacy_view(cur, persona_type, persona_id):
    """GMs and writers: their persona_sliders / persona_traits rows (no relationships are read)."""
    if not _regclass(cur, "persona_sliders"):
        return None
    from newsroom.personas import SLIDERS
    cur.execute(f"SELECT {', '.join(SLIDERS)} FROM persona_sliders WHERE persona_type = %s AND persona_id = %s",
                (persona_type, str(persona_id)))
    row = cur.fetchone()
    if not row:
        return None
    traits = []
    if _regclass(cur, "persona_traits"):
        cur.execute("SELECT trait_id FROM persona_traits WHERE persona_type = %s AND persona_id = %s AND active "
                    "ORDER BY id", (persona_type, str(persona_id)))
        traits = [r[0] for r in cur.fetchall()]
    return {"status": "legacy", "role": persona_type, "origin": "legacy", "sliders": dict(zip(SLIDERS, row)),
            "traits": traits}


def persona_view(cur, counterpart_type, counterpart_id, *, universe_id=None):
    """The counterpart's voice and appraisal eligibility: the personality core's feature_view(profile, 'inbox')
    when a profile is stored; else GMs and writers from persona_sliders/traits ('legacy'); else neutral
    ('missing'). A read error never breaks the caller's transaction: it gives the neutral view."""
    alias = appraisal_mod.ROLE_ALIAS.get(counterpart_type)
    role = alias[0] if alias else counterpart_type
    if alias is None:
        return _neutral(role)
    try:
        with appraisal_mod._savepoint(cur):
            view = _core_view(cur, alias[0], alias[1], counterpart_id, universe_id or _universe_id())
            if view is None and counterpart_type in ("gm", "writer"):
                view = _legacy_view(cur, counterpart_type, counterpart_id)
    except Exception as e:
        print(f"[inbox] persona view for {counterpart_type} {counterpart_id} failed, neutral voice: {e}", flush=True)
        view = None
    return view or _neutral(role)


def voice_lines(view, kind=None):
    """The prompt's description of the counterpart's voice."""
    custom = getattr(kind, "VOICE", None)
    if view.get("status") == "missing":
        return [f"Your voice: {custom or NEUTRAL_VOICE}."]
    from newsroom.personas import slider_sentence
    lines = []
    sentence = slider_sentence(view.get("sliders") or {})
    if sentence:
        lines.append(sentence)
    if view.get("traits"):
        lines.append("Your traits: " + ", ".join(str(t).replace("_", " ") for t in view["traits"]) + ".")
    return lines or [f"Your voice: {custom or NEUTRAL_VOICE}."]


def relationship_lines(cur, thread):
    """Lines from the relationships plan's relationship_context(state, target=JR's persona), only once that module
    exists; [] before (neutral). The inbox never reads persona_relationships or legacy tags."""
    try:
        import importlib
        rel = importlib.import_module(RELATIONSHIP_MODULE)
    except ImportError:
        return []
    ctx_fn = getattr(rel, "relationship_context", None)
    state_fn = getattr(rel, "inbox_state", None)
    if not callable(ctx_fn) or not callable(state_fn):
        return []
    try:
        state = state_fn(cur, appraisal_mod.counterpart_ref(thread))
        ctx = ctx_fn(state, target=appraisal_mod.actor_ref(thread))
    except Exception:
        return []
    summary = (ctx or {}).get("summary")
    return [f"How you see them: {summary}"] if summary else []


# ------------------------------------------------------------------------------------------------- prompts ---

def _sheet(facts):
    """validate_rationale's sheet: the facts with the 'assets' and 'teams' keys it expects."""
    sheet = dict(facts or {})
    sheet.setdefault("assets", [])
    sheet.setdefault("teams", {})
    return sheet


def _history_lines(history):
    out = []
    for m in list(history or [])[-HISTORY_MESSAGES:]:
        who = {"JR": "Them", "COUNTERPART": "You", "SYSTEM": "Note"}.get(m.get("author"), m.get("author"))
        out.append(f"{who}: {m.get('body')}")
    return out


def prompt(thread, intent, facts, history, note, view, ask_appraisal, kind=None, rel_lines=(), who=None, voice=None):
    who = who or thread.get("counterpart_name") or "the counterpart"
    lines = [f"You are {who}, writing to the {str(thread.get('actor_role') or '').lower()} you deal with, in a "
             f"private front-office message ({thread.get('kind')}: {thread.get('subject')})."]
    # A per-intent voice (the kind's `intent_voice`) replaces the counterpart's persona lines for that intent.
    lines += [f"Your voice: {voice}."] if voice else voice_lines(view, kind)
    lines += list(rel_lines)
    if str(intent or "").upper() == "TALK":
        lines.append("No decision is being made here: they are talking to you, so answer them naturally and "
                     "do not accept, reject or settle anything.")
    else:
        lines.append(f"What has been decided, which you must convey and never change: {intent}.")
    hist = _history_lines(history)
    if hist:
        lines.append("The conversation so far:")
        lines += hist
    if note:
        lines.append(f"Their latest message to you: {note}")
    closing = ("and do not settle, accept or reject anything: nothing is being decided."
               if str(intent or "").upper() == "TALK" else "and never contradict the decision.")
    lines += ["FACT SHEET (JSON; the only facts you may state):", json.dumps(_sheet(facts), indent=1, default=str),
              "Write 1-4 sentences in your own voice. Never state a number, dollar amount, player, team or contract "
              "fact that isn't on the fact sheet, " + closing]
    keys = '"message": "..."'
    if ask_appraisal:
        lines.append("Also judge, privately, how they treated you in their latest message (respectful and in depth, "
                     "or snippy, dismissive or rude). Judge their manner, not the decision.")
        keys += (', "appraisal": {"outcome": "positive|negative|mixed", "significance": "routine|meaningful", '
                 '"mistreated": true|false, "reason": "one short sentence"}')
    extra = getattr(kind, "EXTRA_JSON", None)
    if extra:
        keys += ", " + extra
    lines.append("Answer with JSON only: {" + keys + "}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------- validation ---

def intent_ok(text, intent):
    """False when the words contradict the decision (a refusal that sounds like a deal, or the reverse)."""
    up = str(intent or "").upper()
    if up == "TALK":                     # nothing is being decided, so either direction of a deal is a contradiction
        return not (_CONTRADICTS["negative"].search(text) or _CONTRADICTS["positive"].search(text))
    if any(w in up for w in _NEGATIVE_INTENTS) and _CONTRADICTS["negative"].search(text):
        return False
    if any(w in up for w in _POSITIVE_INTENTS) and not any(w in up for w in _NEGATIVE_INTENTS) \
            and _CONTRADICTS["positive"].search(text):
        return False
    return True


def check(text, facts, intent, kind=None, league=None):
    """(ok, why): validate_rationale against the sheet, the kind's validate hook, the intent check."""
    from engines.gm_rationale import validate_rationale
    ok, why = validate_rationale(text, _sheet(facts), league)
    if not ok:
        return False, why
    hook = getattr(kind, "validate", None)
    if callable(hook):
        got = hook(text, facts)
        ok, why = got if isinstance(got, tuple) else (bool(got), "kind validate")
        if not ok:
            return False, why or "kind validate"
    if not intent_ok(text, intent):
        return False, "contradicts the decision"
    return True, None


# ------------------------------------------------------------------------------------------------- writing ---

def _template(kind, intent, facts):
    return _decorate(kind, kind.template(intent, facts), facts)


def _decorate(kind, text, facts):
    """A kind may append something fixed to its reply whoever wrote it (PROSPECT's closing scout memo). It runs
    after the model's text is accepted, so the model's own words are still what `check` validated."""
    hook = getattr(kind, "decorate", None)
    return hook(text, facts) if callable(hook) else text


def compose(cur, kind, thread, intent, facts, history=(), note=None, llm=None, budget_s=INBOX_LLM_BUDGET_S, *,
            judge=True, view=None, league=None):
    """The counterpart's reply. judge=False (an opening message, a SYSTEM line) asks for no appraisal."""
    started = time.monotonic()
    if llm is None:
        return Reply(_template(kind, intent, facts), "TEMPLATE")
    hook = getattr(kind, "loop_reply", None)
    if callable(hook):
        saved = _savepoint(cur, "inbox_loop")
        try:
            words = hook(cur, thread, intent, facts, llm, history, note)
            if saved:
                cur.execute("RELEASE SAVEPOINT inbox_loop")
        except Exception as e:      # noqa: BLE001 - the inbox always answers; the voice is the fallback
            if saved:               # the loop's own writes would otherwise abort the caller's transaction
                try:
                    cur.execute("ROLLBACK TO SAVEPOINT inbox_loop")
                except Exception:   # noqa: BLE001
                    pass
            print(f"[inbox] {thread.get('kind')} player loop failed, using the voice: {e}", flush=True)
            words = None
        if words and str(words).strip():
            return Reply(str(words).strip(), "LLM")
    if view is None:
        view = persona_view(cur, thread.get("counterpart_type"), thread.get("counterpart_id"))
    ask = bool(judge) and thread.get("counterpart_type") in appraisal_mod.ROLE_ALIAS and appraisal_mod.allowed(view)
    # A per-intent voice: the kind may name a different speaker for one intent (PROSPECT's closing summary is the
    # department scout's, not the prospect's). {"name", "voice"}; None keeps the counterpart's own voice.
    intent_voice = getattr(kind, "intent_voice", None)
    over = intent_voice(intent) if callable(intent_voice) else None
    over = over if isinstance(over, dict) else None
    text_prompt = prompt(thread, intent, facts, history, note, view, ask, kind, relationship_lines(cur, thread),
                         who=(over or {}).get("name"), voice=(over or {}).get("voice"))
    data = parse_json(call_with_budget(llm, text_prompt, budget_s))
    text = str((data or {}).get("message") or "").strip()
    ok, why = check(text, facts, intent, kind, league) if text else (False, "no message")
    elapsed = time.monotonic() - started
    if not ok:
        print(f"[inbox] {thread.get('kind')} reply: template ({why}; {elapsed:.1f}s)", flush=True)
        return Reply(_template(kind, intent, facts), "TEMPLATE")
    ap = appraisal_mod.normalize(data.get("appraisal")) if ask else None
    extra = {k: v for k, v in data.items() if k not in ("message", "appraisal")}
    print(f"[inbox] {thread.get('kind')} reply: model ({elapsed:.1f}s)", flush=True)
    return Reply(_decorate(kind, text, facts), "LLM", ap, extra)


def write(cur, kind, thread, intent, facts, history=(), note=None, llm=None, budget_s=INBOX_LLM_BUDGET_S, **kw):
    """(text, 'LLM' | 'TEMPLATE', appraisal {"outcome", "significance", "mistreated", "reason"} | None)."""
    r = compose(cur, kind, thread, intent, facts, history, note, llm, budget_s, **kw)
    return r.text, r.source, r.appraisal


# ------------------------------------------------------------------------------------------------ one turn ---

def _author(thread):
    return "SYSTEM" if thread.get("counterpart_type") == "system" else "COUNTERPART"


def _jr_body(kind, thread, option_id, payload, note, last_options):
    if note and note.strip():
        return note.strip()
    if option_id and payload:
        from engines.inbox.parse import draft_summary
        return draft_summary(option_id, payload)
    label = next((o.get("label") for o in last_options or () if o.get("id") == option_id), None)
    return label or option_id or "(no message)"


def _talk_result(history):
    """A null option with a note is talk (FRONT_OFFICE_INBOX_PLAN §2.7): no engine act, no state, no outcome.
    The counterpart answers with the last turn's facts, minus the decision keys a kind's act would have set, so
    no agent loop re-conveys a decision that is already on the table."""
    facts = {}
    for m in reversed(history or ()):
        if m.get("author") != "JR":          # the newest counterpart turn even with empty facts: walking back past
            facts = dict(m.get("facts") or {})   # it re-told an older decision (SCOUT's ACKNOWLEDGED carries {})
            break
    # Every fact a kind's act sets to describe its decision, across the kinds: a talk reply that reuses them
    # re-conveys a decision that is already on the table (GM_TALK's template echoed "Here's our counter: ...").
    for key in ("decision", "engine_status", "reply", "offer_id", "pending_move_id", "gm_message", "action",
                "status", "counter", "counter_reasons", "option", "question", "direction", "reasons"):
        facts.pop(key, None)
    from engines.inbox.registry import ActResult
    return ActResult(facts=facts, intent="TALK", engine_ref=None, close=False, judge=True)


def exchange(cur, kind, thread_id, option_id=None, payload=None, note=None, *, llm=None, at=None,
             budget_s=INBOX_LLM_BUDGET_S, league=None):
    """One JR turn: kind.act decides and its decision is stored with JR's line first; then the counterpart's reply
    (model or template) with its appraisal; then the relationship event in the outbox. A null option is talk
    (no engine act) unless the kind declares it reads typed text. Raises InboxError for a read-only thread (409),
    a missing one (404), a note over NOTE_MAX_CHARS (400) or a null option with no words (400). Returns the
    updated thread."""
    thread = store.get_thread(cur, thread_id)
    if thread is None:
        from engines.inbox import NotFound
        raise NotFound(f"no inbox thread {thread_id}")
    store.require_writable(cur, thread)
    if note is not None and len(note) > NOTE_MAX_CHARS:
        raise InboxError(400, f"notes are at most {NOTE_MAX_CHARS} characters")
    when = at if at is not None else {"season": thread["season"], "week": thread["week"],
                                      "offseason_stage": thread.get("offseason_stage")}
    before = store.messages(cur, thread_id)
    last_options = next((m["options"] for m in reversed(before) if m["author"] != "JR"), [])
    typed_text = str(getattr(kind, "KIND", "")).upper() in TYPED_TEXT_KINDS
    if option_id is None and not typed_text:                                 # 1. talk: no engine decision
        if not (note and note.strip()):
            raise InboxError(400, "a message needs words")
        result = _talk_result(before)
    else:
        result = kind.act(cur, thread, option_id, payload or {}, note)       # 1. the engine decides
    store.add_message(cur, thread_id, "JR", _jr_body(kind, thread, option_id, payload, note, last_options), "JR",
                      option_id=option_id, engine_ref=result.engine_ref, at=when)
    if result.state is not None:
        store.update_state(cur, thread_id, result.state)
    if result.link:
        store.set_link(cur, thread_id, result.link[0], result.link[1])
    thread = store.get_thread(cur, thread_id)
    history = store.messages(cur, thread_id)
    reply = compose(cur, kind, thread, result.intent, result.facts, history, note, llm, budget_s,  # 2. the words
                    judge=result.judge, league=league)
    options = [] if result.close else (list(last_options) if option_id is None and not typed_text
                                       else list(kind.options(cur, thread, result) or []))
    msg = store.add_message(cur, thread_id, _author(thread), reply.text, reply.source, options=options,  # 3. stored
                            facts=result.facts, engine_ref=result.engine_ref, appraisal=reply.appraisal, at=when)
    if reply.data and reply.source == "LLM":            # the kind's extra JSON (e.g. REPORTER's "done"), as in scan
        hook = getattr(kind, "on_reply", None)
        if callable(hook):
            hook(cur, store.get_thread(cur, thread_id), reply.data)
    if reply.appraisal:                                                     # 4. the event, in the outbox
        event = appraisal_mod.exchange_event(thread, msg, None, None, reply.appraisal, when)
        if event is not None:
            appraisal_mod.record(cur, event, thread_id=thread_id, message_id=msg["id"])
    if result.close:
        store.close_thread(cur, thread_id)
    return store.get_thread(cur, thread_id)
