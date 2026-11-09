"""Press conference reporter and coach agents (PRESS_CONFERENCE_PLAN §2.5; PROJECT_SPEC §7.14).

This module is the agents half of a postgame presser. It writes prompts, calls one injected model per turn
(``llm(prompt) -> str``; the default is ``engines.llm_provider.generate_text(prompt, source="press")``), validates
the answer with ``newsroom.press_facts.check_line`` and returns plain dicts. It never opens a database connection,
never commits, and never calls the network in tests: every database read is a guarded best-effort and a missing
or failing module means neutral, never an exception.

Public API (U6 imports exactly these names and shapes)::

    reporter_turn(attendee, state, llm) -> dict
    coach_turn(subject, state, llm, kind) -> dict
    coach_pick(subject, hands, state, llm, coach_views=None) -> dict
    coach_views(cur, coach, reporters) -> None
    fallback_pick(hands, state) -> dict
    patience_budget(subject, sheet) -> int
    reporter_judgment(attendee, conference, llm) -> dict
    validate_judgment(raw, attendee, conference) -> dict
    judgment_events(conference, attendee, appraisal, universe_id) -> list[dict]
    skip_history(cur, person_key, coach_id, season) -> dict
    MAX_QUESTIONS, MAX_FOLLOW_UPS_IN_A_ROW and the patience constants

``state`` (built by U6, used by the tests) is a mapping. Every key is optional at the edges, but a real session
supplies them all::

    {
      "conference_id": int | str,          # used in the prompt seeds and stable event ids
      "season": int,
      "week": int,                         # the game's week
      "subject": {                         # the person at the podium
          "id": int | None,                # coaches.id
          "name": str,
          "team_id": int,
          "team_name": str,
          "team_abbr": str,
          "team_key": str | None,          # durable key, e.g. "team:<teams.id>"
          "persona_id": str | None,        # core persona id for relationship reads/events
          "role": "coach",
          # optional: tenure, schemes, tendencies, record, hot_seat, temperament, profile,
          # personality_profile or personality_view (a core feature_view('press') result), and
          # identity (core Identity fields, read through personality.store when state has a cursor)
      },
      "sheet": dict,                       # press_facts.fact_sheet(cur, game_id)
      "transcript": [turn],                # turns recorded so far, oldest first
      "hands": [hand],                     # this round's raised hands (Attendee shape + optional "follow_up")
      "picks": {agent_id: int},            # times each reporter has been picked this presser
      "hand_counts": {agent_id: int},      # rounds in which each reporter had a hand up
      "last_answer": turn | None,          # the coach's last ANSWER/NO_COMMENT turn
      "last_asker": agent_id | None,       # the reporter whose question that turn answers
      "questions_asked": int,
      "follow_ups_in_a_row": int,          # follow-ups by the last asker in the current chain
      "passes_in_a_row": {agent_id: int},  # a reporter who passes twice in a row is done
      "done": [agent_id, ...],
      "mood": int | None,                  # 1..5, 1 = irritated
      "patience": {"budget": int, "spent": int} | None,   # None for a user-controlled subject
      "wants_to_end": bool,                # the coach's last reply
      "controller": "AGENT" | "USER",
      "game_label": str,                   # optional; built from the sheet when absent
      "memory_blocks": {agent_id: str},    # optional prebuilt build_memory_block output
      "relationship_contexts": {person_key: dict},  # optional pre-resolved relationship_context
      "skip_history": {person_key: {"hands": int, "picked": int}},  # from skip_history()
      "attendees": [agent_id | attendee],  # for end_condition's RAN_OUT
      "cur", "universe_id", "league_id"    # optional; only for guarded relationship/memory reads
    }

``hand`` is U2's ``Attendee`` plus an optional ``"follow_up": bool``. ``turn`` is the plan §2.7 turn shape
(``turn_id``/``id``, ``speaker`` REPORTER|SUBJECT, ``agent_id``, ``kind``, ``text``, ``reply_to``, ``about``,
``tone``). ``check_line`` is called with the reporter's display name or the coach's name as the speaker.

``conference`` (for ``reporter_judgment``/``validate_judgment``/``judgment_events``) is::

    {
      "conference_id": int | str,
      "status": "SCHEDULED" | "LIVE" | "REVIEW" | "CLOSED" | "DECLINED" | "FAILED",
      "season": int, "week": int,
      "subject": <the state subject shape>,
      "team_key": str | None,
      "sheet": dict | None,
      "turns": [turn],                     # alias: "transcript"
      "attendees": {agent_id: {"hands", "picked", "hand_left_up"}} | [attendee dicts],
      "hand_rounds": {agent_id: [round ints]},   # the press_hands rounds, for treatment outcome_refs
      "skip_history": {person_key: {"hands", "picked"}},
      "game_label": str | None,
      "memory_blocks": {agent_id: str}, "relationship_contexts": {person_key: dict},
      "universe_id": str
    }
"""
from __future__ import annotations

import json
import random
import re

from newsroom import agent_memory, personas, press_facts

try:  # pure contract module; guarded so a missing relationships feature is neutral, not an exception
    from personality.relationship_events import RELATIONSHIP_SCHEMA_VERSION
except Exception:  # noqa: BLE001
    RELATIONSHIP_SCHEMA_VERSION = "relationships-v1"

# --------------------------------------------------------------------------------------------- constants
MAX_QUESTIONS = 25               # hard safety cap (plan D1; U1: the observed maximum was 27)
MAX_FOLLOW_UPS_IN_A_ROW = 2      # a third follow-up from the same reporter is not offered as a hand
PASSES_IN_A_ROW_DONE = 2         # a reporter who passes twice in a row is done

MAX_REPORTER_WORDS = 60
MAX_COACH_WORDS = 120
MAX_REASON_WORDS = 30
MAX_MEMORY_WORDS = 60

# Patience constants, set from U1's corpus (PRESS_CONFERENCE_CORPUS.md §8) rather than §2.5's first guesses.
BASE_PATIENCE = 14
WIN_BONUS = 1
TIE_ADJUST = 0
LOSS_ADJUST = 0                 # loss pressers are longer than wins in the corpus: no loss penalty
BLOWOUT_LOSS_PENALTY = -1
BLOWOUT_MARGIN = 17
LOSING_STREAK_FREE_GAMES = 2
LOSING_STREAK_PENALTY = -1      # per game of a losing streak beyond the first two
HOT_SEAT_PENALTY = -2
# The producer rules' own threshold, so a coach the relationship producers call job pressure is the one the
# podium docks: a kept coach at or above this share of his firing line.
from engines.relationship_producers.rules import FIRING_LINE, HOT_SEAT_PRESSURE   # noqa: E402
TEMPERAMENT_COEFFICIENT = 0.5
MIN_PATIENCE = 6
MAX_PATIENCE = 20

POINTED_COST = 2
REPEATED_TOPIC_COST = 2

COACH_NON_ANSWER = "We'll look at the tape on that one."
NEUTRAL_COACH_LINE = "plain, professional, guarded about injuries and the game plan"

REPORTER_ACTIONS = ("ASK", "FOLLOW_UP", "REACT", "PASS")
TONES = ("neutral", "pointed", "light")
ABOUT_TYPES = ("player", "coach", "team", "game")
COACH_KINDS = ("OPENING", "ANSWER", "CLOSING")

JUDGMENT_RELATIONSHIP_KINDS = ("support", "collaboration", "conflict")
OUTCOMES = ("positive", "negative", "mixed")
SIGNIFICANCES = ("routine", "meaningful")
TREATMENT_KINDS = ("disrespect", "dismissal", "recognition")
TREATMENT_ENUM_TEXT = "disrespect|dismissal|recognition"

_PERSONAS_CACHE = {}


# ------------------------------------------------------------------------------------------------ helpers
def _get(state, key, default=None):
    """Read a state key from a mapping or (for convenience) an attribute."""
    if isinstance(state, dict):
        return state.get(key, default)
    return getattr(state, key, default)


def _conference_id(state):
    value = _get(state, "conference_id")
    if value is None:
        value = _get(state, "id")
    return value if value is not None else "unknown"


def _subject(state):
    return _get(state, "subject") or {}


def _sheet(state):
    return _get(state, "sheet") or {}


def _subject_name(subject):
    return (subject or {}).get("name") or "the head coach"


def _team_facts(state, team_id=None):
    sheet = _sheet(state)
    subject = _subject(state)
    teams = sheet.get("teams") or []
    wanted = team_id if team_id is not None else subject.get("team_id")
    if wanted is not None:
        for team in teams:
            if team.get("team_id") == wanted:
                return team
    return teams[0] if len(teams) == 1 else None


def _opponent_facts(state):
    sheet = _sheet(state)
    subject = _subject(state)
    for team in sheet.get("teams") or []:
        if team.get("team_id") != subject.get("team_id"):
            return team
    return None


def _result(state):
    """{"outcome", "margin", "team_score", "opponent_score"} for the subject's team, or None."""
    team = _team_facts(state)
    if not team:
        return None
    final = (_sheet(state).get("game") or {}).get("final") or {}
    home = bool(team.get("home"))
    mine = final.get("home") if home else final.get("away")
    theirs = final.get("away") if home else final.get("home")
    if mine is None or theirs is None:
        return None
    if mine > theirs:
        outcome = "win"
    elif mine < theirs:
        outcome = "loss"
    else:
        outcome = "tie"
    return {"outcome": outcome, "margin": abs(mine - theirs),
            "team_score": mine, "opponent_score": theirs}


def _game_label(state):
    explicit = _get(state, "game_label")
    if explicit:
        return explicit
    game = _sheet(state).get("game") or {}
    week = game.get("week") or _get(state, "week")
    opponent = _opponent_facts(state)
    parts = []
    if week:
        parts.append(f"Week {week}")
    if opponent and opponent.get("name"):
        parts.append(f"against the {opponent['name']}")
    label = " ".join(parts) or "the game"
    result = _result(state)
    if result:
        past = {"win": "won", "loss": "lost", "tie": "tied"}[result["outcome"]]
        label += f", {past} {result['team_score']}-{result['opponent_score']}"
    return label


def _turn_id(turn):
    if not isinstance(turn, dict):
        return None
    return turn.get("turn_id", turn.get("id"))


def _turns(state):
    return _get(state, "transcript") or _get(state, "turns") or []


def _turn_by_id(turns, turn_id):
    for turn in turns or []:
        if _turn_id(turn) == turn_id:
            return turn
    return None


def _last_answer_and_asker(state):
    turns = _turns(state)
    answer = _get(state, "last_answer")
    if answer is None:
        for turn in reversed(turns):
            if turn.get("speaker") == "SUBJECT" and str(turn.get("kind") or "").upper() in ("ANSWER", "NO_COMMENT"):
                answer = turn
                break
    asker = _get(state, "last_asker")
    if asker is None and answer is not None:
        question = _turn_by_id(turns, answer.get("reply_to"))
        asker = (question or {}).get("agent_id")
    return answer, asker


def _follow_ups_in_a_row(state):
    try:
        return int(_get(state, "follow_ups_in_a_row") or 0)
    except (TypeError, ValueError):
        return 0


def _follow_up_allowed(state, agent_id):
    answer, asker = _last_answer_and_asker(state)
    return bool(answer is not None and asker == agent_id and _follow_ups_in_a_row(state) < MAX_FOLLOW_UPS_IN_A_ROW)


def _reporter_done(state, agent_id):
    if agent_id in set(_get(state, "done") or []):
        return True
    passes = (_get(state, "passes_in_a_row") or {}).get(agent_id, 0)
    try:
        return int(passes) >= PASSES_IN_A_ROW_DONE
    except (TypeError, ValueError):
        return False


def _cap_reached(state):
    try:
        return int(_get(state, "questions_asked") or 0) >= MAX_QUESTIONS
    except (TypeError, ValueError):
        return False


def patience_remaining(state):
    """Questions left in the coach's budget; None when there is no budget (a user-controlled subject)."""
    patience = _get(state, "patience")
    if not isinstance(patience, dict):
        return None
    if patience.get("remaining") is not None:
        return int(patience["remaining"])
    return int(patience.get("budget") or 0) - int(patience.get("spent") or 0)


def question_cost(tone, repeated=False):
    """A question costs 1; a pointed one costs 2; a repeated topic costs 2 (never more than 2)."""
    cost = POINTED_COST if str(tone or "").lower() == "pointed" else 1
    if repeated:
        cost = max(cost, REPEATED_TOPIC_COST)
    return cost


def _all_reporters_done(state):
    attendees = _get(state, "attendees") or []
    if not attendees:
        return False
    done = set(_get(state, "done") or [])
    for item in attendees:
        agent_id = item.get("agent_id") if isinstance(item, dict) else item
        if agent_id not in done:
            return False
    return True


def end_condition(state):
    """One of PATIENCE, SUBJECT_ENDED, RAN_OUT, CAP or None (plan §2.5 end conditions)."""
    remaining = patience_remaining(state)
    if remaining is not None and remaining <= 0:
        return "PATIENCE"
    if _get(state, "wants_to_end") and remaining is not None and remaining <= 3:
        return "SUBJECT_ENDED"
    if _all_reporters_done(state):
        return "RAN_OUT"
    if _cap_reached(state):
        return "CAP"
    return None


def _persona_for(attendee):
    archetype = str(attendee.get("archetype") or "").upper()
    if archetype not in _PERSONAS_CACHE:
        try:
            _PERSONAS_CACHE.update(personas.load_personas_json())
        except Exception:  # noqa: BLE001 - a persona file problem never breaks an agent
            _PERSONAS_CACHE.setdefault(archetype, None)
    persona = _PERSONAS_CACHE.get(archetype)
    if persona:
        return persona
    name = attendee.get("display_name") or attendee.get("agent_id") or "a reporter"
    return f"You are {name}, a journalist covering this game."


def _persona_block(attendee, state, fmt="POST"):
    persona = _persona_for(attendee)
    conference_id = _conference_id(state)
    agent_id = attendee.get("agent_id")
    try:
        return personas.render_persona(persona, fmt, tags={"GAME_REVIEW"},
                                       seed=f"press:{conference_id}:{agent_id}")
    except Exception:  # noqa: BLE001
        return persona if isinstance(persona, str) else ""


def _memory_block(attendee, state):
    blocks = _get(state, "memory_blocks") or {}
    agent_id = attendee.get("agent_id")
    person_key = attendee.get("person_key")
    for key in (agent_id, person_key):
        if key in blocks:
            return blocks[key] or ""
    cur = _get(state, "cur")
    if cur is None:
        return ""
    subject = _subject(state)
    try:
        return agent_memory.build_memory_block(
            cur, agent_id, int(_get(state, "season") or 0), int(_get(state, "week") or 1),
            subject_team_id=subject.get("team_id"),
        ) or ""
    except Exception:  # noqa: BLE001 - missing memory tables are simply an empty block
        return ""


def _person_ref(attendee, universe_id):
    persona_id = attendee.get("persona_id") or attendee.get("person_key") or attendee.get("agent_id")
    if not persona_id:
        return None
    return {"kind": "person", "universe_id": universe_id,
            "role": attendee.get("person_role") or "writer", "persona_id": str(persona_id)}


def _coach_ref(subject, universe_id):
    persona_id = (subject or {}).get("persona_id") or (subject or {}).get("person_key")
    if not persona_id:
        return None
    return {"kind": "person", "universe_id": universe_id, "role": "coach", "persona_id": str(persona_id)}


def _relationship_context(attendee, state):
    """The reporter's one-way view of the coach, or None.

    A pre-resolved consumer context in ``state['relationship_contexts']`` wins (tests and U6). Otherwise this is a
    clearly marked best-effort read through the relationships plan: the switchboard is off by default, so a missing
    module, missing tables, absent league/universe or any error is neutral. It never reads persona_relationships.
    """
    injected = (_get(state, "relationship_contexts") or {}).get(attendee.get("person_key"))
    if injected is not None:
        return injected
    cur = _get(state, "cur")
    subject = _subject(state)
    universe_id = _get(state, "universe_id") or subject.get("universe_id")
    league_id = _get(state, "league_id") or subject.get("league_id")
    if cur is None or not universe_id or league_id is None:
        return None
    try:
        from personality import features as pf
        from personality import relationship_store as rs
        from personality import relationships as rel
        policy = pf.relationship_policy(pf.config(cur, int(league_id), "relationships"))
        if policy is None:
            return None
        subject_ref = _person_ref(attendee, universe_id)
        target_ref = _coach_ref(subject, universe_id)
        if subject_ref is None or target_ref is None:
            return None
        as_of = {"season": int(_get(state, "season") or 0), "tick": int(_get(state, "week") or 1)}
        events = rs.read_events(cur, subject=subject_ref, as_of=as_of) or []
        seasons = {ev.get("occurred_at", {}).get("season") for ev in events if isinstance(ev, dict)}
        seasons.discard(None)
        seasons.add(as_of["season"])
        calendar = {season: 32 for season in range(min(seasons), max(seasons) + 1)}
        projected = rel.project_relationships(events, subject=subject_ref, as_of=as_of, known_through=None,
                                              policy=policy, calendar=calendar)
        return rel.relationship_context(projected, target=target_ref)
    except Exception:  # noqa: BLE001 - the switchboard being off must never fail a presser
        return None


def _trust_word(trust):
    try:
        value = float(trust)
    except (TypeError, ValueError):
        return "unknown"
    if value >= 0.5:
        return "warm"
    if value >= 0.15:
        return "positive"
    if value > -0.15:
        return "neutral"
    if value > -0.5:
        return "cool"
    return "bad"


def _relationship_block(attendee, state):
    context = _relationship_context(attendee, state)
    if not isinstance(context, dict) or context.get("status") != "enabled":
        return ""
    edges = context.get("edges") or []
    if not edges:
        return ""
    lines = []
    meaningful = False
    for edge in edges:
        target = edge.get("target") or {}
        name = target.get("name") or target.get("team_key") or target.get("persona_id") or "this coach"
        bits = [f"status {edge.get('status') or 'unknown'}"]
        if edge.get("trust") is not None:
            bits.append(f"you feel {_trust_word(edge.get('trust'))} about him")
        attachment = (edge.get("attachment") or {}).get("tier")
        if attachment and attachment not in ("none", "unknown"):
            bits.append(f"attachment {attachment}")
        breaches = edge.get("breaches") or []
        if breaches:
            bits.append(f"{len(breaches)} open grievance(s)")
        lines.append(f"- {name}: " + ", ".join(bits))
        if (edge.get("status") in ("known", "conflicted") or edge.get("trust") not in (None, 0)
                or attachment not in (None, "none", "unknown") or breaches):
            meaningful = True
    if not meaningful:
        return ""
    return ("YOUR RELATIONSHIP WITH THIS COACH (one-way: how you see him, never how he sees you):\n"
            + "\n".join(lines))


def _render_transcript(turns, state=None):
    lines = []
    for turn in turns or []:
        tid = _turn_id(turn)
        label = f"[turn {tid}] " if tid is not None else ""
        if turn.get("speaker") == "SUBJECT":
            speaker = "Coach"
        else:
            speaker = turn.get("display_name") or turn.get("handle") or turn.get("agent_id") or "Reporter"
        kind = str(turn.get("kind") or "").upper()
        text = turn.get("text") or ""
        if kind == "NO_COMMENT":
            text = "No comment."
        lines.append(f"{label}{speaker}: {text}")
    return "\n".join(lines)


def _sheet_json(sheet):
    return json.dumps(sheet or {}, indent=1, sort_keys=True, default=str)


def _word_count(text):
    return len(re.findall(r"[A-Za-z0-9'’$.-]+", text or ""))


def _trim_words(text, limit):
    words = (text or "").split()
    return " ".join(words[:limit])


def _clean_text(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _parse_json(text):
    """The first JSON object in a model reply (fenced or bare), or None."""
    if not isinstance(text, str):
        return None
    value = text.strip()
    if value.startswith("```"):
        value = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", value)
        value = re.sub(r"\s*```$", "", value).strip()
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else None
    except Exception:  # noqa: BLE001
        pass
    decoder = json.JSONDecoder()
    for index, char in enumerate(value):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(value[index:])
        except Exception:  # noqa: BLE001
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _call_llm(llm, prompt):
    """One model call. The injected callable takes the prompt as its only positional argument."""
    if llm is None:
        from engines.llm_provider import generate_text
        reply = generate_text(prompt, source="press")
    else:
        reply = llm(prompt)
    if isinstance(reply, tuple):
        reply = reply[0]
    if isinstance(reply, dict):
        return reply
    return reply if isinstance(reply, str) else ""


def _reply_dict(reply):
    return reply if isinstance(reply, dict) else _parse_json(reply)


def _check_text(text, sheet, speaker):
    try:
        return press_facts.check_line(text or "", sheet or {}, speaker or "") or []
    except Exception:  # noqa: BLE001 - the checker never fails a turn
        return []


def _issue_problems(issues):
    parts = []
    for issue in issues[:4]:
        kind = issue.get("kind")
        claim = issue.get("claim")
        recorded = issue.get("recorded")
        if kind == "NUMBER":
            parts.append(f"\"{claim}\" does not match the fact sheet (recorded: {recorded})")
        elif kind == "NAME":
            parts.append(f"\"{claim}\" is not a name in the fact sheet")
        else:
            parts.append(f"\"{claim}\" is not supported by the box score (recorded: {recorded})")
    return "; ".join(parts)


def _normalise_question(text):
    return " ".join(re.findall(r"[a-z0-9'’]+", str(text or "").lower()))


def _repeat_problem(text, turns):
    norm = _normalise_question(text)
    if not norm:
        return None
    words = set(norm.split())
    for turn in turns or []:
        if turn.get("speaker") != "REPORTER":
            continue
        if str(turn.get("kind") or "").upper() not in ("QUESTION", "FOLLOW_UP", "REACT"):
            continue
        other = _normalise_question(turn.get("text"))
        if not other:
            continue
        if other == norm:
            return "That question was already asked in this press conference; ask something new."
        other_words = set(other.split())
        union = words | other_words
        if union and len(words & other_words) / len(union) >= 0.9:
            return "That question was already asked in this press conference; ask something new."
    return None


def _normalise_about(value):
    out = []
    if not isinstance(value, list):
        return out
    for item in value:
        if not isinstance(item, dict):
            continue
        about_type = str(item.get("type") or "").lower()
        if about_type not in ABOUT_TYPES:
            continue
        subject_id = item.get("id")
        try:
            subject_id = int(subject_id) if subject_id is not None else None
        except (TypeError, ValueError):
            subject_id = None
        out.append({"type": about_type, "id": subject_id})
    return out


def _reporter_hand_counts(attendee, state):
    agent_id = attendee.get("agent_id")
    hands = (_get(state, "hand_counts") or {}).get(agent_id)
    picked = (_get(state, "picks") or {}).get(agent_id)
    if hands is None:
        hands = sum(1 for turn in _turns(state)
                    if turn.get("speaker") == "REPORTER" and turn.get("agent_id") == agent_id)
    if picked is None:
        picked = sum(1 for turn in _turns(state)
                     if turn.get("speaker") == "REPORTER" and turn.get("agent_id") == agent_id
                     and str(turn.get("kind") or "").upper() in ("QUESTION", "FOLLOW_UP", "REACT"))
    return int(hands or 0), int(picked or 0)


# --------------------------------------------------------------------------------------- reporter turn
REPORTER_JSON_SCHEMA = """{
  "action": "ASK" | "FOLLOW_UP" | "REACT" | "PASS",
  "text": "your question (empty only for PASS)",
  "about": [{"type": "player" | "coach" | "team" | "game", "id": <int or null>}],
  "tone": "neutral" | "pointed" | "light",
  "reply_to": <turn id to reply to, or null>
}"""

REPORTER_RULES = f"""RULES FOR YOUR QUESTION:
- Ask one question. ASK is a new question; FOLLOW_UP presses on the coach's last answer and is allowed only if you
  asked that question and only twice in a row; REACT picks up another reporter's question or the coach's answer to it
  (for example, "To Adam's point..."); PASS means you have nothing left to ask.
- At most {MAX_REPORTER_WORDS} words; about 20 words is typical for this room.
- Use only numbers, scores, records and names that appear in the fact sheet. Never invent a stat or a name.
- Do not repeat a question that has already been asked.
- "about" names the people, team or game the question touches, with ids from the fact sheet when you know them.
- "tone" is neutral, pointed or light. "reply_to" is the turn id you are reacting or following up to, else null.
- Answer with exactly one JSON object, no markdown and no text around it:"""


def _reporter_prompt(attendee, state):
    subject = _subject(state)
    team = _team_facts(state)
    hands, picked = _reporter_hand_counts(attendee, state)
    if str(attendee.get("role") or "").upper() == "BEAT":
        team_name = attendee.get("team_name") or (team or {}).get("name") or subject.get("team_name") or "your team"
        role_line = f"You are a beat reporter for the {team_name} and ask only about the {team_name}."
    else:
        role_line = "You are a national reporter covering this game."

    memory = _memory_block(attendee, state)
    sections = [
        _persona_block(attendee, state),
        f"YOUR MEMORY:\n{memory}" if memory else "",
        _relationship_block(attendee, state),
        (f"YOU ARE AT: Coach {_subject_name(subject)}'s postgame press conference after {_game_label(state)}.\n"
         f"{role_line}"),
        f"THE FACTS (the only numbers, scores, records and names you may use):\n{_sheet_json(_sheet(state))}",
        f"THE TRANSCRIPT SO FAR:\n{_render_transcript(_turns(state)) or '(the coach has not spoken yet)'}",
        (f"YOUR TURN: you have raised your hand {hands} time(s) in this press conference and the coach has picked "
         f"you {picked} time(s)."),
        ("You may FOLLOW_UP on the coach's last answer: you asked that question."
         if _follow_up_allowed(state, attendee.get("agent_id"))
         else "You may not FOLLOW_UP now: only the reporter who asked the last question may follow up, and at most "
              "two follow-ups in a row are allowed."),
        REPORTER_RULES,
        REPORTER_JSON_SCHEMA,
    ]
    return "\n\n".join(section for section in sections if section)


def _validate_reporter_action(raw, attendee, state):
    """(action dict, problems). A non-empty problems list means the turn must be retried or dropped."""
    if not isinstance(raw, dict):
        return None, ["The reply was not a JSON object."]
    problems = []
    action = str(raw.get("action") or "").strip().upper()
    if action not in REPORTER_ACTIONS:
        problems.append(f"action must be one of {', '.join(REPORTER_ACTIONS)}.")
    text = raw.get("text")
    if not isinstance(text, str):
        text = ""
        if action != "PASS":
            problems.append("text must be a string.")
    text = _clean_text(text)
    if action != "PASS" and not text:
        problems.append("text must not be empty unless the action is PASS.")
    if action != "PASS" and _word_count(text) > MAX_REPORTER_WORDS:
        problems.append(f"the question is {_word_count(text)} words; the limit is {MAX_REPORTER_WORDS}.")
    if action in ("ASK", "REACT") and text:
        repeat = _repeat_problem(text, _turns(state))
        if repeat:
            problems.append(repeat)
    if action in ("ASK", "FOLLOW_UP", "REACT") and text:
        issues = _check_text(text, _sheet(state), attendee.get("display_name") or attendee.get("agent_id"))
        if issues:
            problems.append(_issue_problems(issues))
    if action == "FOLLOW_UP":
        answer, asker = _last_answer_and_asker(state)
        if answer is None:
            problems.append("there is no last answer to follow up on.")
        elif asker != attendee.get("agent_id"):
            problems.append("only the reporter who asked the last question may follow up.")
        elif _follow_ups_in_a_row(state) >= MAX_FOLLOW_UPS_IN_A_ROW:
            problems.append(f"only {MAX_FOLLOW_UPS_IN_A_ROW} follow-ups in a row are allowed; ask a new question.")

    tone = str(raw.get("tone") or "neutral").strip().lower()
    if tone not in TONES:
        tone = "neutral"
    reply_to = raw.get("reply_to")
    if reply_to is not None:
        try:
            reply_to = int(reply_to)
        except (TypeError, ValueError):
            reply_to = None
    if action == "FOLLOW_UP" and reply_to is None:
        answer, _ = _last_answer_and_asker(state)
        reply_to = _turn_id(answer) if answer else None
    if action == "PASS":
        text, reply_to = "", None
        about = []
    else:
        about = _normalise_about(raw.get("about"))

    action_dict = {"action": action, "text": text, "about": about, "tone": tone, "reply_to": reply_to}
    return action_dict, problems


def reporter_turn(attendee, state, llm):
    """One reporter's action: ASK, FOLLOW_UP, REACT or PASS, validated against the fact sheet.

    Returns ``{"action", "text", "about", "tone", "reply_to", "source", "retries", "issues", "dropped"}``.
    A bad line is retried once with the problem spelled out, then the hand is dropped (PASS, source FALLBACK).
    """
    agent_id = attendee.get("agent_id")
    if _cap_reached(state):
        return {"action": "PASS", "text": "", "about": [], "tone": "neutral", "reply_to": None,
                "source": "ENGINE", "retries": 0, "issues": [], "dropped": True,
                "problem": f"the {MAX_QUESTIONS}-question safety cap was reached"}
    if _reporter_done(state, agent_id):
        return {"action": "PASS", "text": "", "about": [], "tone": "neutral", "reply_to": None,
                "source": "ENGINE", "retries": 0, "issues": [], "dropped": True,
                "problem": "this reporter is done (two passes in a row)."}

    prompt = _reporter_prompt(attendee, state)
    raw = _reply_dict(_call_llm(llm, prompt))
    action, problems = _validate_reporter_action(raw, attendee, state)
    retries = 0
    issues = []
    if problems:
        retries = 1
        retry_prompt = (prompt + "\n\nYour previous reply had these problems: " + "; ".join(problems)
                        + "\nAnswer again with exactly one JSON object.")
        raw = _reply_dict(_call_llm(llm, retry_prompt))
        action, problems = _validate_reporter_action(raw, attendee, state)
        if problems:
            return {"action": "PASS", "text": "", "about": [], "tone": "neutral", "reply_to": None,
                    "source": "FALLBACK", "retries": retries, "issues": [],
                    "dropped": True, "problem": "; ".join(problems)}
    if action is None:
        return {"action": "PASS", "text": "", "about": [], "tone": "neutral", "reply_to": None,
                "source": "FALLBACK", "retries": retries, "issues": [], "dropped": True,
                "problem": "; ".join(problems) or "the reply could not be parsed"}
    if action.get("action") in ("ASK", "FOLLOW_UP", "REACT"):
        issues = _check_text(action.get("text"), _sheet(state), attendee.get("display_name") or agent_id)
    return {**action, "source": "AGENT", "retries": retries, "issues": issues, "dropped": False}


# ------------------------------------------------------------------------------------------ coach turn
COACH_GUIDANCE = """HOW HEAD COACHES TALK AT THE PODIUM (follow these instincts):
- Deflect credit and pressure: praise the players and coordinators before yourself; say "we" more than "I".
- Take blame rarely and briefly, usually in a loss when the room expects it ("that's on me"), then move on.
- Refuse injury details: "I don't have an update on that", "we'll see how he responds", "the medical staff will
  handle it". Never diagnose, timetable or invent a body part.
- Protect the game plan: do not reveal calls, checks, signals or what you will run next week.
- Stay on the record but guarded: no front-office, contract, owner or personnel speculation.
- Sound like a person, not a statement: short, concrete, a little tired after a loss."""

COACH_TASKS = {
    "OPENING": "Give your opening statement. Set the tone for the room: the result, the players who stood out, "
               "and one thing you will look at. Do not take questions yet.",
    "ANSWER": "Answer the last question in the transcript. Say it the way a head coach says it at the podium.",
    "CLOSING": "Close the press conference. One or two sentences. Thank the room, or say that's all.",
}


def _coach_profile(subject, state):
    subject = subject or {}
    team = _team_facts(state)
    sheet_coach = (team or {}).get("coach") or {}
    profile = {
        "name": subject.get("name") or sheet_coach.get("name") or "the head coach",
        "team": subject.get("team_name") or (team or {}).get("name"),
        "tenure": subject.get("tenure", sheet_coach.get("tenure")),
        "schemes": subject.get("schemes", sheet_coach.get("schemes")),
        "tendencies": subject.get("tendencies", sheet_coach.get("tendencies")),
        "record": subject.get("record", (team or {}).get("record_after")),
        "hot_seat": subject.get("hot_seat", sheet_coach.get("hot_seat")),
    }
    return profile


def _personality_view(subject, state=None):
    subject = subject or {}
    view = subject.get("personality_view")
    if view is not None:
        return view
    profile = subject.get("personality_profile") or subject.get("profile")
    if profile is None:
        # U6 may hand over the coach's core Identity instead; read it through personality.store (guarded).
        identity_fields = subject.get("identity")
        cur = _get(state, "cur") if state is not None else None
        if identity_fields and cur is not None:
            try:
                from personality import store as pstore
                from personality.core import Identity
                profile = pstore.read(cur, Identity(**identity_fields))
            except Exception:  # noqa: BLE001 - a missing/absent profile is neutral
                profile = None
    if profile is None:
        return None
    try:
        from personality.core import feature_view
        return feature_view(profile, "press")
    except Exception:  # noqa: BLE001 - a corrupt profile is not a presser failure
        return None


def _coach_personality_block(subject, state=None):
    view = _personality_view(subject, state)
    if not isinstance(view, dict) or view.get("status") != "stored":
        return f"YOUR DEMEANOR: {NEUTRAL_COACH_LINE}."
    lines = ["YOUR PERSONALITY (stay in character; let it colour how you answer, do not recite it):"]
    try:
        sentence = personas.slider_sentence(view.get("sliders") or {})
    except Exception:  # noqa: BLE001
        sentence = ""
    if sentence:
        lines.append(sentence)
    traits = view.get("traits") or []
    if traits:
        try:
            catalogue = personas.load_traits()
            labels = [catalogue.get(t, {}).get("label") or t for t in traits]
        except Exception:  # noqa: BLE001
            labels = list(traits)
        lines.append("TENDENCIES: " + ", ".join(str(label) for label in labels) + ".")
    return "\n".join(lines)


def _mood_line(state):
    try:
        mood = int(_get(state, "mood") or 3)
    except (TypeError, ValueError):
        mood = 3
    words = {1: "irritated", 2: "tense", 3: "even", 4: "pleased", 5: "upbeat"}
    result = _result(state)
    if result:
        past = {"win": "won", "loss": "lost", "tie": "tied"}[result["outcome"]]
        return (f"THE RESULT: you {past} {result['team_score']}-{result['opponent_score']} "
                f"(margin {result['margin']}). Your mood: {mood}/5 ({words.get(mood, 'even')}).")
    return f"Your mood: {mood}/5 ({words.get(mood, 'even')})."


def _hot_seat_text(hot_seat):
    if not hot_seat:
        return "no hot-seat pressure recorded"
    if isinstance(hot_seat, dict):
        return hot_seat.get("line") or json.dumps(hot_seat, sort_keys=True, default=str)
    return str(hot_seat)


def _coach_prompt(subject, state, kind):
    profile = _coach_profile(subject, state)
    sections = [
        ("COACH PROFILE:\n"
         f"Name: {profile['name']}\n"
         f"Team: {profile.get('team') or 'unknown'}\n"
         f"Tenure: {profile.get('tenure') if profile.get('tenure') is not None else 'unknown'} season(s)\n"
         f"Schemes: {json.dumps(profile.get('schemes') or {}, sort_keys=True, default=str)}\n"
         f"Tendencies: {json.dumps(profile.get('tendencies') or {}, sort_keys=True, default=str)}\n"
         f"Record: {profile.get('record') or 'unknown'}\n"
         f"Hot seat: {_hot_seat_text(profile.get('hot_seat'))}"),
        _coach_personality_block(subject, state),
        _mood_line(state),
        COACH_GUIDANCE,
        f"THE FACTS (the only numbers, scores, records and names you may use):\n{_sheet_json(_sheet(state))}",
        f"THE TRANSCRIPT SO FAR:\n{_render_transcript(_turns(state)) or '(nothing has been said yet)'}",
        f"YOUR TASK: {COACH_TASKS.get(kind, COACH_TASKS['ANSWER'])}",
        (f"RULES: at most {MAX_COACH_WORDS} words. Use only numbers, scores, records and names that appear in the "
         "fact sheet; never invent a stat, an injury detail or a name. Answer with exactly one JSON object, no "
         "markdown and no text around it:\n"
         '{"text": "<what you say>", "mood": <1..5, 1 = irritated>, "wants_to_end": <true|false>}'),
    ]
    return "\n\n".join(section for section in sections if section)


def _validate_coach_reply(raw, subject, state):
    if not isinstance(raw, dict):
        return None, ["The reply was not a JSON object."]
    problems = []
    text = raw.get("text")
    if not isinstance(text, str) or not _clean_text(text):
        problems.append("text must be a non-empty string.")
        text = ""
    text = _clean_text(text)
    if text and _word_count(text) > MAX_COACH_WORDS:
        problems.append(f"the answer is {_word_count(text)} words; the limit is {MAX_COACH_WORDS}.")
    mood = raw.get("mood")
    try:
        mood = int(mood)
        if mood < 1 or mood > 5:
            raise ValueError
    except (TypeError, ValueError):
        problems.append("mood must be an integer from 1 to 5.")
        mood = None
    wants_to_end = raw.get("wants_to_end")
    if not isinstance(wants_to_end, bool):
        wants_to_end = bool(wants_to_end)
    if text:
        issues = _check_text(text, _sheet(state), _subject_name(subject))
        if issues:
            problems.append(_issue_problems(issues))
    return {"text": text, "mood": mood, "wants_to_end": wants_to_end}, problems


def _passing_sentences(text, subject, state):
    sentences = re.split(r"(?<=[.!?])\s+", _clean_text(text))
    clean = [sentence for sentence in sentences if sentence and not _check_text(sentence, _sheet(state),
                                                                                _subject_name(subject))]
    result = " ".join(clean).strip()
    return _trim_words(result, MAX_COACH_WORDS) if result else ""


def coach_turn(subject, state, llm, kind):
    """The coach's OPENING, ANSWER or CLOSING turn, validated against the fact sheet.

    Returns ``{"text", "mood", "wants_to_end", "source", "retries", "issues"}``. A bad line is retried once; then
    the sentences that passed are kept, or the non-answer is used with ``source = 'FALLBACK'``.
    """
    kind = str(kind or "ANSWER").upper()
    if kind not in COACH_KINDS:
        kind = "ANSWER"
    prompt = _coach_prompt(subject, state, kind)
    raw = _reply_dict(_call_llm(llm, prompt))
    reply, problems = _validate_coach_reply(raw, subject, state)
    retries = 0
    issues = []
    if problems:
        retries = 1
        retry_prompt = (prompt + "\n\nYour previous reply had these problems: " + "; ".join(problems)
                        + "\nAnswer again with exactly one JSON object.")
        raw = _reply_dict(_call_llm(llm, retry_prompt))
        reply, problems = _validate_coach_reply(raw, subject, state)
        if problems:
            fallback_text = _passing_sentences((reply or {}).get("text") or "", subject, state)
            if not fallback_text:
                fallback_text = COACH_NON_ANSWER
            issues = _check_text(fallback_text, _sheet(state), _subject_name(subject))
            mood = (reply or {}).get("mood")
            wants = (reply or {}).get("wants_to_end")
            return {"text": fallback_text, "mood": mood if mood in (1, 2, 3, 4, 5) else 3,
                    "wants_to_end": bool(wants), "source": "FALLBACK", "retries": retries, "issues": issues}
    if reply is None:
        return {"text": COACH_NON_ANSWER, "mood": 3, "wants_to_end": False,
                "source": "FALLBACK", "retries": retries, "issues": []}
    issues = _check_text(reply.get("text"), _sheet(state), _subject_name(subject))
    return {**reply, "source": "AGENT", "retries": retries, "issues": issues}


# ------------------------------------------------------------------------------------------ coach pick
def coach_views(cur, coach, reporters):
    """CLEARLY MARKED SLOT: the coach's own views of reporters (plan D11).

    Today it returns None, so the coach sees only the raised hands, his pick/skip history and neutral guidance.
    When the relationships plan serves coach -> reporter context, this fills ``coach_pick``'s ``coach_views`` from
    ``relationship_context`` (one-way: never a reporter's view of the coach, never legacy tags).
    """
    return None


def _hand_line(hand):
    role = str(hand.get("role") or "NATIONAL").upper()
    role_text = "beat" if role == "BEAT" else "national"
    outlet = hand.get("team_name") or hand.get("outlet") or hand.get("pool_tier") or ""
    follow_up = " (wants a follow-up)" if hand.get("follow_up") else ""
    name = hand.get("display_name") or hand.get("agent_id")
    handle = hand.get("handle") or ""
    outlet_text = f", {outlet}" if outlet else ""
    return f"- {name} ({handle}), {role_text} reporter{outlet_text}{follow_up} [agent id: {hand.get('agent_id')}]"


def _pick_prompt(subject, hands, state, coach_views):
    history = _get(state, "skip_history") or {}
    cur = _get(state, "cur")
    coach_id = (subject or {}).get("id")
    history_lines = []
    for hand in hands:
        key = hand.get("person_key") or hand.get("agent_id")
        record = history.get(key) or history.get(hand.get("agent_id"))
        if record is None and cur is not None and coach_id is not None and _get(state, "season") is not None:
            record = skip_history(cur, hand.get("person_key") or hand.get("agent_id"), coach_id,
                                  int(_get(state, "season")))
        record = record or {}
        history_lines.append(
            f"- {hand.get('display_name') or hand.get('agent_id')}: hands up "
            f"{int(record.get('hands') or 0)}, picked {int(record.get('picked') or 0)}"
        )
    sections = [
        (f"Coach {_subject_name(subject)} is choosing who asks next at his postgame press conference after "
         f"{_game_label(state)}."),
        "THE RAISED HANDS (names, outlets and roles; a follow-up is marked):\n" + "\n".join(
            _hand_line(hand) for hand in hands),
        ("YOUR PICK AND SKIP HISTORY WITH THESE REPORTERS THIS SEASON (from press_hands):\n"
         + "\n".join(history_lines) if history_lines else
         "YOUR PICK AND SKIP HISTORY WITH THESE REPORTERS THIS SEASON: none yet."),
        ("GUIDANCE: a coach usually lets the local beat open the questioning, takes a fair spread of the room, and "
         "allows a follow-up when a reporter wants to press on the last answer. The questions themselves are not "
         "yours to see; pick the person, not the question."),
    ]
    if coach_views is not None:
        sections.append("YOUR OWN VIEWS OF THE REPORTERS (one-way; not what they think of you):\n"
                        + json.dumps(coach_views, indent=1, sort_keys=True, default=str))
    sections.append(
        "Pick exactly one of the raised hands. Answer with exactly one JSON object, no markdown:\n"
        '{"agent_id": "<the agent id of one raised hand>", "reason": "<short reason, at most 20 words>"}'
    )
    return "\n\n".join(section for section in sections if section)


def _pick_problem(raw, valid_ids):
    if not isinstance(raw, dict):
        return "The reply was not a JSON object."
    agent_id = raw.get("agent_id")
    if not isinstance(agent_id, str) or agent_id not in valid_ids:
        return f"agent_id must be one of the raised hands: {', '.join(valid_ids)}."
    return None


def coach_pick(subject, hands, state, llm, coach_views=None):
    """The AI coach picks who asks next, or the fallback does (``source = 'FALLBACK'``).

    The coach sees the hands as names/outlets/roles with follow-ups marked and his own pick/skip history; he does
    not see the drafted questions and gets no relationship input except his own ``coach_views`` slot.
    """
    hands = [hand for hand in (hands or []) if isinstance(hand, dict) and hand.get("agent_id")]
    valid_ids = [hand["agent_id"] for hand in hands]
    if not hands:
        fallback = fallback_pick([], state)
        return {**fallback, "source": "FALLBACK"}
    prompt = _pick_prompt(subject, hands, state, coach_views)
    raw = _reply_dict(_call_llm(llm, prompt))
    problem = _pick_problem(raw, valid_ids)
    retries = 0
    if problem:
        retries = 1
        retry_prompt = (prompt + "\n\nYour previous reply had a problem: " + problem
                        + "\nPick one of the raised hands and answer again with exactly one JSON object.")
        raw = _reply_dict(_call_llm(llm, retry_prompt))
        problem = _pick_problem(raw, valid_ids)
    if problem:
        fallback = fallback_pick(hands, state)
        return {"agent_id": fallback["agent_id"], "reason": fallback["reason"],
                "source": "FALLBACK", "retries": retries, "problem": problem}
    reason = _clean_text(raw.get("reason")) or "The coach called on him."
    return {"agent_id": raw["agent_id"], "reason": reason, "source": "AGENT", "retries": retries}


def fallback_pick(hands, state):
    """Safety order when the coach agent fails: follow-up from the last asker, then least picked, beats first,
    ties seeded. Deterministic for the same state."""
    hands = [hand for hand in (hands or []) if isinstance(hand, dict) and hand.get("agent_id")]
    if not hands:
        return {"agent_id": None, "reason": "No hands were raised."}
    last_asker = _get(state, "last_asker")
    if last_asker is None:
        _, last_asker = _last_answer_and_asker(state)
    for hand in hands:
        if hand.get("follow_up") and hand.get("agent_id") == last_asker:
            return {"agent_id": hand["agent_id"],
                    "reason": "A follow-up from the reporter who asked the last question."}
    picks = _get(state, "picks") or {}

    def picked(hand):
        try:
            return int(picks.get(hand["agent_id"], 0) or 0)
        except (TypeError, ValueError):
            return 0

    least = min(picked(hand) for hand in hands)
    candidates = [hand for hand in hands if picked(hand) == least]
    beats = [hand for hand in candidates if str(hand.get("role") or "").upper() == "BEAT"]
    if beats:
        candidates = beats
    candidates = sorted(candidates, key=lambda hand: str(hand["agent_id"]))
    seed = f"press:{_conference_id(state)}:fallback:{len(_turns(state))}"
    chosen = random.Random(seed).choice(candidates)
    times = "once" if least == 1 else f"{least} times"
    return {"agent_id": chosen["agent_id"],
            "reason": f"Picked least so far ({times}); the local beat goes first on a tie."}


# ---------------------------------------------------------------------------------------------- patience
def _team_streak(team):
    return (team or {}).get("streak") or {}


def _hot_seat_for(subject, team):
    """True only when the coach is genuinely under pressure. `coach_hot_seat` writes a row for all 32 teams each
    offseason with `fired = false`, so "a retained row exists" meant every returning coach, including a Super Bowl
    winner, was on the hot seat and lost HOT_SEAT_PENALTY patience."""
    value = (subject or {}).get("hot_seat")
    if value is None:
        value = ((team or {}).get("coach") or {}).get("hot_seat")
    if not value:
        return False
    if not isinstance(value, dict):
        return True                                     # a caller that already decided
    if value.get("fired"):
        return False
    if "score" not in value:
        return True
    line = float(value.get("firing_line") or FIRING_LINE)
    return float(value.get("score") or 0.0) >= HOT_SEAT_PRESSURE * line


def _temperament(subject):
    subject = subject or {}
    value = subject.get("temperament")
    if value is not None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    for key in ("personality_view", "personality_profile", "profile"):
        view = subject.get(key)
        if isinstance(view, dict):
            sliders = view.get("sliders")
            if isinstance(sliders, dict) and sliders.get("temperament") is not None:
                try:
                    return int(sliders["temperament"])
                except (TypeError, ValueError):
                    return None
    return None


def patience_budget(subject, sheet):
    """The coach's question budget (plan §2.5, constants from U1's corpus). A user-controlled subject has none."""
    budget = BASE_PATIENCE
    teams = (sheet or {}).get("teams") or []
    subject = subject or {}
    team = None
    for candidate in teams:
        if candidate.get("team_id") == subject.get("team_id"):
            team = candidate
            break
    if team is None and len(teams) == 1:
        team = teams[0]
    final = ((sheet or {}).get("game") or {}).get("final") or {}
    outcome = None
    margin = None
    if team is not None:
        home = bool(team.get("home"))
        mine = final.get("home") if home else final.get("away")
        theirs = final.get("away") if home else final.get("home")
        if mine is not None and theirs is not None:
            outcome = "win" if mine > theirs else "loss" if mine < theirs else "tie"
            margin = abs(mine - theirs)
    if outcome == "win":
        budget += WIN_BONUS
    elif outcome == "tie":
        budget += TIE_ADJUST
    elif outcome == "loss":
        budget += LOSS_ADJUST
    if outcome == "loss" and margin is not None and margin >= BLOWOUT_MARGIN:
        budget += BLOWOUT_LOSS_PENALTY
    streak = _team_streak(team)
    if str(streak.get("type") or "").upper() == "L":
        try:
            count = int(streak.get("count") or 0)
        except (TypeError, ValueError):
            count = 0
        budget += LOSING_STREAK_PENALTY * max(0, count - LOSING_STREAK_FREE_GAMES)
    if _hot_seat_for(subject, team):
        budget += HOT_SEAT_PENALTY
    temperament = _temperament(subject)
    if temperament is not None:
        budget += round((temperament - 5) * TEMPERAMENT_COEFFICIENT)
    return int(max(MIN_PATIENCE, min(MAX_PATIENCE, budget)))


# ------------------------------------------------------------------------------------- reporter judgment
JUDGMENT_JSON_SCHEMA = """{
  "exchanges": [
    {"turn_id": <the turn id of an answer you got>,
     "relationship_kind": "support" | "collaboration" | "conflict",
     "outcome": "positive" | "negative" | "mixed",
     "significance": "routine" | "meaningful",
     "reason": "<why, at most 30 words>"}
  ],
  "treatment": {
     "treatment_kind": "disrespect" | "dismissal" | "recognition",
     "outcome": "positive" | "negative" | "mixed",
     "significance": "routine" | "meaningful",
     "reason": "<why, at most 30 words>"} | null,
  "memory": "<first person, at most 60 words>"
}"""


def _conference_turns(conference):
    return (conference or {}).get("turns") or (conference or {}).get("transcript") or []


def _own_exchanges(attendee, conference):
    turns = _conference_turns(conference)
    agent_id = (attendee or {}).get("agent_id")
    out = []
    for turn in turns:
        if turn.get("speaker") != "SUBJECT":
            continue
        question = _turn_by_id(turns, turn.get("reply_to"))
        if question is not None and question.get("agent_id") == agent_id:
            out.append({"question": question, "answer": turn})
    return out


def _own_turn(turn_id, attendee, conference):
    """The reporter's own turn, or None. Accepts either the answer turn or its question's id."""
    turns = _conference_turns(conference)
    turn = _turn_by_id(turns, turn_id)
    if turn is None:
        return None
    if turn.get("speaker") == "SUBJECT":
        question = _turn_by_id(turns, turn.get("reply_to"))
        return turn if question is not None and question.get("agent_id") == attendee.get("agent_id") else None
    return turn if turn.get("agent_id") == attendee.get("agent_id") else None


def _is_no_comment(turn_id, conference):
    turns = _conference_turns(conference)
    turn = _turn_by_id(turns, turn_id)
    if turn is not None and str(turn.get("kind") or "").upper() == "NO_COMMENT":
        return True
    return any(str(answer.get("kind") or "").upper() == "NO_COMMENT" and answer.get("reply_to") == turn_id
               for answer in turns)


def _stats_for_attendee(attendee, conference):
    agent_id = (attendee or {}).get("agent_id")
    attendees = (conference or {}).get("attendees")
    record = None
    if isinstance(attendees, dict):
        record = attendees.get(agent_id)
    elif isinstance(attendees, list):
        for item in attendees:
            if isinstance(item, dict) and item.get("agent_id") == agent_id:
                record = item
                break
    record = dict(record or {})
    for key in ("hands", "picked", "hand_left_up", "skipped"):
        if key in (attendee or {}) and key not in record:
            record[key] = attendee[key]
    return record


def _declined(conference):
    return str((conference or {}).get("status") or "").upper() == "DECLINED"


def _treatment_matches(treatment, attendee, conference):
    kind = treatment.get("treatment_kind")
    if _declined(conference):
        return kind in ("dismissal", "disrespect")
    stats = _stats_for_attendee(attendee, conference)
    try:
        hands = int(stats.get("hands") or 0)
        picked = int(stats.get("picked") or 0)
    except (TypeError, ValueError):
        hands, picked = 0, 0
    hand_left_up = bool(stats.get("hand_left_up"))
    skipped = bool(stats.get("skipped")) or (hands > 0 and picked < hands)
    if kind == "recognition":
        return picked >= 1 and not hand_left_up
    if kind in ("dismissal", "disrespect"):
        return bool(skipped or hand_left_up)
    return False


def _valid_reason(value):
    return isinstance(value, str) and bool(value.strip()) and _word_count(value) <= MAX_REASON_WORDS


def _built_memory(attendee, conference):
    subject = (conference or {}).get("subject") or {}
    coach = subject.get("name") or "the head coach"
    label = (conference or {}).get("game_label") or _game_label(conference)
    stats = _stats_for_attendee(attendee, conference)
    try:
        hands = int(stats.get("hands") or 0)
        picked = int(stats.get("picked") or 0)
    except (TypeError, ValueError):
        hands, picked = 0, 0
    count = len(_own_exchanges(attendee, conference))
    if _declined(conference):
        text = (f"Coach {coach} did not take questions after {label}. I was in the room with my hand up and got "
                "nothing from him.")
    else:
        plural = "" if count == 1 else "s"
        text = (f"I was at Coach {coach}'s press conference after {label}. I asked {count} question{plural} and had "
                f"my hand up {hands} times; he picked me {picked} times. ")
        if picked >= 1 and hands > picked:
            text += "He went to other reporters more often than me."
        elif picked >= 1:
            text += "It was a routine press conference."
        else:
            text += "He never called on me."
    return _trim_words(_clean_text(text), MAX_MEMORY_WORDS)


def _judgment_problems(raw, attendee, conference):
    if not isinstance(raw, dict):
        return ["The reply was not a JSON object."]
    problems = []
    exchanges = raw.get("exchanges")
    if not isinstance(exchanges, list):
        problems.append("exchanges must be a list.")
    else:
        for index, item in enumerate(exchanges):
            if not isinstance(item, dict):
                problems.append(f"exchanges[{index}] must be an object.")
                continue
            turn_id = item.get("turn_id")
            if not isinstance(turn_id, int) or isinstance(turn_id, bool):
                problems.append(f"exchanges[{index}].turn_id must be an integer.")
            if item.get("relationship_kind") not in JUDGMENT_RELATIONSHIP_KINDS:
                problems.append(f"exchanges[{index}].relationship_kind must be one of "
                                f"{', '.join(JUDGMENT_RELATIONSHIP_KINDS)}.")
            if item.get("outcome") not in OUTCOMES:
                problems.append(f"exchanges[{index}].outcome must be one of {', '.join(OUTCOMES)}.")
            if item.get("significance") not in SIGNIFICANCES:
                problems.append(f"exchanges[{index}].significance must be one of {', '.join(SIGNIFICANCES)}.")
            if not _valid_reason(item.get("reason")):
                problems.append(f"exchanges[{index}].reason must be non-empty and at most {MAX_REASON_WORDS} words.")
            if (isinstance(turn_id, int) and item.get("outcome") == "positive"
                    and _is_no_comment(turn_id, conference)):
                problems.append(f"exchanges[{index}] is a NO_COMMENT answer and cannot be positive.")
    treatment = raw.get("treatment")
    if treatment is not None:
        if not isinstance(treatment, dict):
            problems.append("treatment must be an object or null.")
        else:
            if treatment.get("treatment_kind") not in TREATMENT_KINDS:
                problems.append(f"treatment.treatment_kind must be one of {TREATMENT_ENUM_TEXT}.")
            if treatment.get("outcome") not in OUTCOMES:
                problems.append(f"treatment.outcome must be one of {', '.join(OUTCOMES)}.")
            if treatment.get("significance") not in SIGNIFICANCES:
                problems.append(f"treatment.significance must be one of {', '.join(SIGNIFICANCES)}.")
            if not _valid_reason(treatment.get("reason")):
                problems.append(f"treatment.reason must be non-empty and at most {MAX_REASON_WORDS} words.")
            if not _treatment_matches(treatment, attendee, conference):
                problems.append("the treatment does not match what happened in this press conference.")
    if not isinstance(raw.get("memory"), str):
        problems.append("memory must be a string.")
    return problems


def _conference_memory(attendee, conference):
    """The reporter's memory block for an appraisal, from the conference or a guarded build_memory_block."""
    blocks = (conference or {}).get("memory_blocks") or {}
    for key in (attendee.get("agent_id"), attendee.get("person_key")):
        if key in blocks:
            return blocks[key] or ""
    cur = (conference or {}).get("cur")
    if cur is None:
        return ""
    subject = (conference or {}).get("subject") or {}
    try:
        return agent_memory.build_memory_block(
            cur, attendee.get("agent_id"), int((conference or {}).get("season") or 0),
            int((conference or {}).get("week") or 1), subject_team_id=subject.get("team_id")) or ""
    except Exception:  # noqa: BLE001
        return ""


def _conference_skip_history(attendee, conference):
    """This season's pick/skip history with the coach, from the conference or a guarded skip_history()."""
    history = (conference or {}).get("skip_history") or {}
    for key in (attendee.get("person_key"), attendee.get("agent_id")):
        if key in history:
            return history[key]
    cur = (conference or {}).get("cur")
    subject = (conference or {}).get("subject") or {}
    season = (conference or {}).get("season")
    if cur is None or season is None or subject.get("id") is None:
        return None
    try:
        return skip_history(cur, attendee.get("person_key") or attendee.get("agent_id"), subject["id"], int(season))
    except Exception:  # noqa: BLE001
        return None


def _judgment_prompt(attendee, conference):
    subject = (conference or {}).get("subject") or {}
    memory = _conference_memory(attendee, conference)
    relationships = ((conference or {}).get("relationship_contexts") or {}).get(attendee.get("person_key"))
    relationship_block = ""
    if isinstance(relationships, dict) and relationships.get("status") == "enabled":
        relationship_block = _relationship_block(attendee, {"relationship_contexts": {
            attendee.get("person_key"): relationships}})

    sections = [
        _persona_block(attendee, conference, fmt="POST"),
        f"YOUR MEMORY:\n{memory}" if memory else "",
        relationship_block,
        f"YOU ARE APPRAISING: Coach {_subject_name(subject)}'s postgame press conference after "
        f"{conference.get('game_label') or _game_label(conference)}.",
    ]
    if _declined(conference):
        sections.append("The coach did not take questions after the game. You were in the room and got nothing "
                        "from him.")
    else:
        exchanges = _own_exchanges(attendee, conference)
        lines = []
        for exchange in exchanges:
            question, answer = exchange["question"], exchange["answer"]
            asked = _clean_text(question.get("text"))
            if str(answer.get("kind") or "").upper() == "NO_COMMENT":
                got = "No comment."
            else:
                got = _clean_text(answer.get("text"))
            lines.append(f"- [turn {_turn_id(answer)}] You asked: \"{asked}\" He answered: \"{got}\"")
        sections.append("YOUR EXCHANGES:\n" + ("\n".join(lines) if lines else "(you got no answers from him)"))
        stats = _stats_for_attendee(attendee, conference)
        hands = int(stats.get("hands") or 0)
        picked = int(stats.get("picked") or 0)
        hand_up = bool(stats.get("hand_left_up"))
        comparison = [f"You raised your hand {hands} time(s); the coach picked you {picked} time(s)."]
        if hand_up:
            comparison.append("You still had your hand up when the coach walked off.")
        attendees = (conference or {}).get("attendees")
        others = []
        if isinstance(attendees, dict):
            for agent_id, record in attendees.items():
                if agent_id == attendee.get("agent_id") or not isinstance(record, dict):
                    continue
                others.append(f"{record.get('display_name') or agent_id}: {int(record.get('hands') or 0)} hands up, "
                              f"{int(record.get('picked') or 0)} picked")
        if others:
            comparison.append("The rest of the room: " + "; ".join(sorted(others)) + ".")
        sections.append("YOUR TREATMENT:\n" + "\n".join(comparison))
        history = _conference_skip_history(attendee, conference) or {}
        if history:
            sections.append(f"YOUR HISTORY WITH THIS COACH THIS SEASON: hands up {int(history.get('hands') or 0)}, "
                            f"picked {int(history.get('picked') or 0)}.")
    sections.append(
        "YOUR TASK: judge how the coach treated you, in the relationships plan's terms, with no trust numbers. "
        "Give one exchange per answer you got, using that answer's turn id. Give a treatment only when the room "
        "shows one (recognition, being dismissed, being disrespected). Write a first-person memory.\n"
        f"RULES: relationship_kind, outcome and significance use the values below; a NO_COMMENT answer can never "
        f"be positive; each reason is non-empty and at most {MAX_REASON_WORDS} words; use no numbers or names that "
        f"are not in the facts; the memory is at most {MAX_MEMORY_WORDS} words. Answer with exactly one JSON "
        "object, no markdown:\n" + JUDGMENT_JSON_SCHEMA
    )
    return "\n\n".join(section for section in sections if section)


def validate_judgment(raw, attendee, conference):
    """Validate and bound one appraisal (plan §2.5). Invalid input becomes the neutral appraisal.

    Rules: the shape and enums must parse; ``turn_id``s must be this reporter's own exchanges (others are
    dropped); a NO_COMMENT exchange is never positive; the treatment must match what happened (recognition needs
    a pick and no hand left up; dismissal/disrespect need a skip, a cut-off or a decline; a declined presser allows
    only dismissal or disrespect); reasons are non-empty and at most 30 words; a memory that fails ``check_line``
    or runs over 60 words is replaced by the built one.
    """
    built = _built_memory(attendee, conference)
    neutral = {"exchanges": [], "treatment": None, "memory": built, "source": "FALLBACK"}
    if isinstance(raw, str):
        raw = _parse_json(raw)
    if not isinstance(raw, dict):
        return neutral
    exchanges = raw.get("exchanges")
    if not isinstance(exchanges, list):
        return neutral
    memory = raw.get("memory")
    if not isinstance(memory, str):
        return neutral

    cleaned = []
    seen = set()
    for item in exchanges:
        if not isinstance(item, dict):
            return neutral
        turn_id = item.get("turn_id")
        if not isinstance(turn_id, int) or isinstance(turn_id, bool):
            return neutral
        if turn_id in seen:
            continue
        if _own_turn(turn_id, attendee, conference) is None:
            continue                                   # another reporter's exchange is dropped
        relationship_kind = item.get("relationship_kind")
        outcome = item.get("outcome")
        significance = item.get("significance")
        if relationship_kind not in JUDGMENT_RELATIONSHIP_KINDS or outcome not in OUTCOMES \
                or significance not in SIGNIFICANCES:
            return neutral
        if not _valid_reason(item.get("reason")):
            continue                                   # a bad reason drops that exchange
        if outcome == "positive" and _is_no_comment(turn_id, conference):
            outcome = "mixed"                          # NO_COMMENT can never be positive
        seen.add(turn_id)
        cleaned.append({"turn_id": turn_id, "relationship_kind": relationship_kind, "outcome": outcome,
                        "significance": significance, "reason": item["reason"].strip()})

    treatment = raw.get("treatment")
    if treatment is not None:
        if not isinstance(treatment, dict):
            return neutral
        kind = treatment.get("treatment_kind")
        outcome = treatment.get("outcome")
        significance = treatment.get("significance")
        if kind not in TREATMENT_KINDS or outcome not in OUTCOMES or significance not in SIGNIFICANCES:
            return neutral
        if not _valid_reason(treatment.get("reason")):
            treatment = None
        else:
            treatment = {"treatment_kind": kind, "outcome": outcome, "significance": significance,
                         "reason": treatment["reason"].strip()}
            if not _treatment_matches(treatment, attendee, conference):
                treatment = None

    memory = _clean_text(memory)
    if not memory or _word_count(memory) > MAX_MEMORY_WORDS:
        memory = built
    else:
        sheet = (conference or {}).get("sheet") or {}
        if _check_text(memory, sheet, ((conference or {}).get("subject") or {}).get("name")):
            memory = built
    return {"exchanges": cleaned, "treatment": treatment, "memory": memory, "source": "AGENT"}


def reporter_judgment(attendee, conference, llm):
    """One reporter's structured appraisal of a closed or declined presser (plan §2.5).

    One model call; an invalid reply is retried once with the problems spelled out, then becomes the neutral
    appraisal. Returns ``{"exchanges", "treatment", "memory", "source", "retries"}``.
    """
    prompt = _judgment_prompt(attendee, conference)
    raw = _reply_dict(_call_llm(llm, prompt))
    problems = _judgment_problems(raw, attendee, conference)
    retries = 0
    if problems:
        retries = 1
        retry_prompt = (prompt + "\n\nYour previous reply had these problems: " + "; ".join(problems)
                        + "\nAnswer again with exactly one JSON object.")
        raw = _reply_dict(_call_llm(llm, retry_prompt))
        if _judgment_problems(raw, attendee, conference):
            result = validate_judgment("", attendee, conference)
            result["retries"] = retries
            return result
    result = validate_judgment(raw, attendee, conference)
    result["retries"] = retries
    return result


# ---------------------------------------------------------------------------------------------- events
def _event_common(conference, attendee, universe_id, kind):
    subject_ref = _person_ref(attendee, universe_id)
    coach_ref = _coach_ref((conference or {}).get("subject") or {}, universe_id)
    if subject_ref is None or coach_ref is None:
        return None
    season = (conference or {}).get("season")
    week = (conference or {}).get("week")
    if season is None or week is None:
        game = ((conference or {}).get("sheet") or {}).get("game") or {}
        season = season if season is not None else game.get("season")
        week = week if week is not None else game.get("week")
    if season is None or week is None:
        return None
    return {
        "schema_version": RELATIONSHIP_SCHEMA_VERSION,
        "universe_id": universe_id,
        "kind": kind,
        "occurred_at": {"season": int(season), "tick": int(week)},
        "subject": subject_ref,
        "target": coach_ref,
        "actor": coach_ref,
        "basis": "verified_simulation",
        "confidence": "medium",
        "reviewer": None,
        "evidence_scope": [],
        "visibility": "public",
        "attribution": {"responsible": [coach_ref], "reason_code": None, "evidence_refs": []},
        "supersedes_source_event_id": None,
    }


def _team_key(conference):
    subject = (conference or {}).get("subject") or {}
    key = (conference or {}).get("team_key") or subject.get("team_key")
    if key:
        return key
    if subject.get("team_id") is not None:
        return f"team:{subject['team_id']}"
    return "team:unknown"


def _outcome_refs(conference, attendee):
    rounds = ((conference or {}).get("hand_rounds") or {}).get(attendee.get("agent_id"))
    if rounds is None:
        rounds = attendee.get("hand_rounds")
    if rounds is None:
        rounds = ((conference or {}).get("hand_rounds") or {}).get(attendee.get("person_key"))
    conference_id = (conference or {}).get("conference_id")
    return [f"press_hands:{conference_id}:{round_}" for round_ in (rounds or [])]


def judgment_events(conference, attendee, appraisal, universe_id):
    """The relationships plan §2.2 envelopes for one reporter's appraisal (plan §2.5).

    One ``meaningful_interaction`` per exchange, one ``treatment`` for a skip/cut-off/recognition, and the
    one-per-occurrence ``press:<conference>:declined`` treatment for a declined presser. Trust is one-way:
    subject is the reporter, target and actor are the coach. Nothing is written here.
    """
    if not isinstance(appraisal, dict):
        return []
    conference_id = (conference or {}).get("conference_id")
    agent_id = attendee.get("agent_id")
    events = []

    for exchange in appraisal.get("exchanges") or []:
        if not isinstance(exchange, dict) or exchange.get("turn_id") is None:
            continue
        turn_id = exchange["turn_id"]
        event = _event_common(conference, attendee, universe_id, "meaningful_interaction")
        if event is None:
            continue
        event["event_id"] = f"press-event:{conference_id}:turn:{turn_id}:{agent_id}"
        event["source_event_id"] = f"press:{conference_id}:turn:{turn_id}"
        event["source_kind"] = "press_conference"
        event["source_refs"] = [{"namespace": "press_turns", "key": str(turn_id)}]
        event["payload"] = {
            "relationship_kind": exchange.get("relationship_kind"),
            "outcome": exchange.get("outcome"),
            "significance": exchange.get("significance"),
            "opportunity_ref": f"press:{conference_id}:turn:{turn_id}",
            "reason": exchange.get("reason"),
        }
        events.append(event)

    treatment = appraisal.get("treatment")
    if isinstance(treatment, dict) and treatment.get("treatment_kind"):
        event = _event_common(conference, attendee, universe_id, "treatment")
        if event is not None:
            declined = _declined(conference)
            event["event_id"] = f"press-event:{conference_id}:treatment:{agent_id}"
            event["source_event_id"] = (f"press:{conference_id}:declined" if declined
                                        else f"press:{conference_id}:treatment:{agent_id}")
            event["source_kind"] = "press_conference"
            event["source_refs"] = [{"namespace": "press_conferences", "key": str(conference_id)}]
            event["payload"] = {
                "team_key": _team_key(conference),
                "treatment_kind": treatment.get("treatment_kind"),
                "outcome": treatment.get("outcome"),
                "significance": treatment.get("significance"),
                "outcome_refs": _outcome_refs(conference, attendee),
                "reason": treatment.get("reason"),
            }
            events.append(event)
    return events


# ----------------------------------------------------------------------------------------- skip history
def skip_history(cur, person_key, coach_id, season):
    """This reporter's hands-up/picked history with one coach this season, from ``press_hands``.

    Returns ``{"hands", "picked", "skipped", "pressers"}``; a missing table/cursor is zeros, never an exception.
    """
    empty = {"hands": 0, "picked": 0, "skipped": 0, "pressers": 0}
    if cur is None:
        return empty
    try:
        cur.execute("SELECT to_regclass('press_conferences') IS NOT NULL")
        row = cur.fetchone()
        if not row or not row[0]:
            return empty
    except Exception:  # noqa: BLE001 - an unmigrated database is simply no history
        return empty
    try:
        cur.execute(
            """
            SELECT COUNT(*), COUNT(*) FILTER (WHERE ph.picked), COUNT(DISTINCT ph.conference_id)
            FROM press_hands ph
            JOIN press_attendees pa ON pa.conference_id = ph.conference_id AND pa.agent_id = ph.agent_id
            JOIN press_conferences pc ON pc.id = ph.conference_id
            WHERE pa.person_key = %s AND pc.season = %s
              AND pc.subject_type = 'coach' AND pc.subject_id = %s
            """,
            (person_key, season, coach_id),
        )
        row = cur.fetchone()
    except Exception:  # noqa: BLE001
        return empty
    if not row:
        return empty
    hands = int(row[0] or 0)
    picked = int(row[1] or 0)
    pressers = int(row[2] or 0)
    return {"hands": hands, "picked": picked, "skipped": hands - picked, "pressers": pressers}
