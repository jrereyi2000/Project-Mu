"""Press conference session engine, storage, runner and CLI
(docs/in_progress/PRESS_CONFERENCE_PLAN.md §2.2, §2.6–§2.9; PROJECT_SPEC §3.3r, §7.14).

One postgame presser per head coach per completed game. The module owns the four tables of migration
``2027_01_20_press_conferences.sql`` (``press_conferences``, ``press_attendees``, ``press_hands``,
``press_turns``), the state machine that runs a room (`run_session`), the week runner, the user (JR) lifecycle,
the post-presser judgments/memories/relationship events (`close_effects`, `flush_relationship_events`) and the
reader interfaces other units consume (`approved_quotes`, `brief_block`, `declined_note`,
`get_press_conference`).

Public API (U6)::

    run_session(cur_factory, conference_id, llm, user_answer=None) -> dict
    schedule_week(cur, season, week, llm=None) -> dict
    run_week(conn_info, season, week, workers=PRESS_WORKERS, llm=None, teams=None) -> dict
    ensure_team_presser(conn_info, team_id, season, week) -> str
    start_user(cur, conference_id, opening=None) / pick_user / answer_user / end_user
    edit_user(cur, conference_id, turn_id, text=None, on_record=None)
    approve(cur, conference_id) / decline(cur, conference_id, reason) / decline_open_for_launch / expire_user_pressers
    close_effects(conn_info, conference_id, llm) -> dict
    flush_relationship_events(cur, conference_id) -> int
    subject_controller(cur, team_id, subject_type="coach", subject_id=None) -> "USER" | "AGENT"
    approved_quotes(cur, season, week, team_id=None) / brief_block / declined_note
    get_press_conference(team_identifier, season=0, week=0) -> str
    build_conference(cur, conference_id) / current_hands(cur, conference_id) / get_conference_row(cur, id)

Conventions. Every reader guards with ``to_regclass('press_conferences')``; a missing table is "no pressers", never
an exception. Every function takes a cursor and never commits (the CLI, the API and the runner commit). The
session state the agents need is rebuilt from the conference row, its attendees and its turns; the small
process-independent scratch state (the drafted first-round hands, pick counts, round counters) lives in the
``press_conferences.patience`` JSONB column, whose documented keys are ``budget``/``spent``/``mood`` plus
``drafts``/``round``/``picks``/``hand_counts``/``done``/``passes_in_a_row``/``questions_asked``/
``follow_ups_in_a_row``/``wants_to_end``/``seq``. The schema has no column for the drafted question text; keeping
it there is what makes ``pick_user`` instant and a reopened LIVE presser keep its raised hands.

SQL statements carry a ``/* q:<tag> */`` marker, the project's convention for offline fake cursors in tests.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from psycopg.types.json import Jsonb

from newsroom import agent_memory, press_agents, press_cast, press_facts

# --------------------------------------------------------------------------------------------- constants
PRESS_WORKERS = 6                 # pressers run in parallel, §2.10
PRESS_REPOLL = "changed"          # "changed" keeps unchanged drafted hands; "all" polls everyone
PRESS_MODEL = os.environ.get("PRESS_MODEL") or None        # None = the provider's default route for source "press"
MAX_ROUND_CALLS = 6               # reporter calls in parallel inside one round

BRIEF_GUIDANCE = ("Quote the head coach only from these exchanges, word for word. Do not attribute anything else "
                  "to him; if he is not quoted here, do not put words in his mouth. Coordinators, players and team "
                  "captains may be quoted as before.")

STATUSES = ("SCHEDULED", "LIVE", "REVIEW", "CLOSED", "DECLINED", "FAILED")
END_REASONS = ("RAN_OUT", "SUBJECT_ENDED", "PATIENCE", "CAP", "DECLINED")


class PressRoomError(Exception):
    """A lifecycle refusal; ``status`` maps to FastAPI's HTTP codes in ``server/press_api.py``."""
    status = 409


class PressNotFound(PressRoomError):
    status = 404


class PressForbidden(PressRoomError):
    status = 403


class PressBadRequest(PressRoomError):
    status = 400


class PressConflict(PressRoomError):
    status = 409


# ---------------------------------------------------------------------------------------------- plumbing
def _installed(cur) -> bool:
    """True when all four press tables exist (one guarded read, never an exception)."""
    try:
        cur.execute("/* q:ready */ SELECT to_regclass('press_conferences') IS NOT NULL "
                    "AND to_regclass('press_attendees') IS NOT NULL "
                    "AND to_regclass('press_hands') IS NOT NULL "
                    "AND to_regclass('press_turns') IS NOT NULL")
        row = cur.fetchone()
        return bool(row and row[0])
    except Exception:  # noqa: BLE001 - unmigrated databases simply have no pressers
        return False


def _regclass_table(cur, table: str) -> bool:
    try:
        cur.execute("/* q:regclass */ SELECT to_regclass(%s) IS NOT NULL", (table,))
        row = cur.fetchone()
        return bool(row and row[0])
    except Exception:  # noqa: BLE001
        return False


def _row_dict(row, keys):
    return dict(zip(keys, row)) if row is not None else None


def _json(value):
    """JSONB parameter (psycopg's Jsonb), passing None through."""
    return None if value is None else Jsonb(value)


def _loads(value, default=None):
    if value is None:
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:  # noqa: BLE001
            return default
    return value


def _cursor_scope(cur_factory):
    """A cursor from a factory (callable) or a plain cursor; no context manager, nothing committed here."""
    return cur_factory() if callable(cur_factory) else cur_factory


def _connect(conn_info):
    import psycopg
    return psycopg.connect(conn_info)


def _conn_info():
    """The DSN the press room uses outside a caller-provided cursor: DATABASE_URL, else the active franchise."""
    dsn = os.environ.get("DATABASE_URL")
    if dsn:
        return dsn
    try:
        from db.franchises import active_dsn
        return active_dsn()
    except Exception:  # noqa: BLE001
        return None


def _user_teams() -> list:
    """The active franchise's user teams (uppercased). [] means JR controls no team (never "every team")."""
    try:
        from db.franchises import active_entry
        _, entry = active_entry()
    except Exception:  # noqa: BLE001
        return []
    return [str(t).upper() for t in ((entry or {}).get("user_teams") or [])]


def _universe_id(cur) -> str | None:
    """The franchise universe id for relationship events: the registry's, else the lifecycle registration."""
    try:
        from db.franchises import active_entry
        _, entry = active_entry()
        value = (entry or {}).get("universe_id")
        if value:
            return str(value)
    except Exception:  # noqa: BLE001
        pass
    try:
        cur.execute("/* q:lifecycle_universe */ SELECT u.universe_id FROM persona_lifecycle_universes u "
                    "JOIN league_state s ON s.id = 1 AND u.league_id = s.madden_league_id::text LIMIT 1")
        row = cur.fetchone()
        return str(row[0]) if row and row[0] else None
    except Exception:  # noqa: BLE001 - an unmigrated or unregistered league simply has no universe
        return None


def _league_id(cur) -> int | None:
    try:
        cur.execute("/* q:league */ SELECT current_season, current_week FROM league_state WHERE id = 1")
        row = cur.fetchone()
        return 1 if row else None
    except Exception:  # noqa: BLE001
        return None


def _league_state(cur):
    try:
        cur.execute("/* q:league */ SELECT current_season, current_week FROM league_state WHERE id = 1")
        row = cur.fetchone()
        return (int(row[0]), int(row[1])) if row else (0, 0)
    except Exception:  # noqa: BLE001
        return (0, 0)


def _team_abbr(cur, team_id):
    try:
        cur.execute("/* q:team_by_id */ SELECT abbreviation FROM teams WHERE id = %s", (team_id,))
        row = cur.fetchone()
        return row[0] if row else None
    except Exception:  # noqa: BLE001
        return None


def _team_by_abbr(cur, abbr):
    clean = str(abbr or "").upper().strip()
    aliases = {"ARI": "AZ", "CRD": "AZ", "WSH": "WAS", "WTC": "WAS"}
    try:
        cur.execute("/* q:team_abbr */ SELECT id, abbreviation, name, city FROM teams "
                    "WHERE abbreviation IN (%s, %s)", (clean, aliases.get(clean, clean)))
        row = cur.fetchone()
    except Exception:  # noqa: BLE001
        row = None
    if not row:
        raise PressNotFound(f"Team '{abbr}' not found")
    return {"id": row[0], "abbr": row[1], "name": row[2], "city": row[3] if len(row) > 3 else None}


def _team_registry(cur) -> dict:
    try:
        cur.execute("/* q:teams */ SELECT id, abbreviation, name FROM teams ORDER BY id")
        rows = cur.fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {f"team:{row[0]}": {"team_id": row[0], "abbreviation": row[1], "name": row[2]} for row in rows}


# ------------------------------------------------------------------------------------------------ the LLM
class MeteredLLM:
    """A callable model that counts calls and tokens. ``llm=None`` routes through ``generate_text(source="press")``
    (respecting ``LLM_PROVIDER_PRESS``), using ``PRESS_MODEL`` as the model override; otherwise the injected
    callable is used and token counts stay 0."""

    def __init__(self, llm=None, model=None):
        self.llm = llm
        self.model = model or PRESS_MODEL
        self.calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self._lock = threading.Lock()

    def __call__(self, prompt):
        with self._lock:
            self.calls += 1
        if self.llm is not None:
            return self.llm(prompt)
        from engines.llm_provider import generate_text
        kwargs = {"source": "press", "return_usage": True}
        if self.model:
            kwargs["model"] = self.model
        text, usage = generate_text(prompt, **kwargs)
        with self._lock:
            self.input_tokens += int((usage or {}).get("input_tokens") or 0)
            self.output_tokens += int((usage or {}).get("output_tokens") or 0)
        return text


def _meter(llm):
    return llm if isinstance(llm, MeteredLLM) else MeteredLLM(llm)


def _local_meter(llm):
    """A fresh meter over the same model, so one conference's counters never include another's calls."""
    source = llm.llm if isinstance(llm, MeteredLLM) else llm
    return MeteredLLM(source, model=getattr(llm, "model", None))


def _default_llm():
    return MeteredLLM(None)


def _user_llm():
    """The model used by the user lifecycle inside a request (pick/answer rounds). Tests patch this."""
    return _default_llm()


# ---------------------------------------------------------------------------------------------- conference
CONFERENCE_KEYS = (
    "id", "season", "week", "game_id", "team_id", "subject_type", "subject_id", "subject_name", "controller",
    "status", "end_reason", "interest", "tier", "cast_inputs", "facts", "patience", "model", "calls",
    "input_tokens", "output_tokens", "effects_applied", "created_at", "started_at", "closed_at",
    "abbreviation", "team_name", "home_team_id", "away_team_id", "home_score", "away_score", "game_type",
)


def get_conference_row(cur, conference_id):
    """The raw conference row (joined to its team and game), or None. Guards the table."""
    if not _installed(cur):
        return None
    cur.execute(
        "/* q:conference */ SELECT pc.id, pc.season, pc.week, pc.game_id, pc.team_id, pc.subject_type, "
        "pc.subject_id, pc.subject_name, pc.controller, pc.status, pc.end_reason, pc.interest, pc.tier, "
        "pc.cast_inputs, pc.facts, pc.patience, pc.model, pc.calls, pc.input_tokens, pc.output_tokens, "
        "pc.effects_applied, pc.created_at, pc.started_at, pc.closed_at, t.abbreviation, t.name, "
        "g.home_team_id, g.away_team_id, g.home_score, g.away_score, g.game_type "
        "FROM press_conferences pc JOIN teams t ON t.id = pc.team_id JOIN games g ON g.id = pc.game_id "
        "WHERE pc.id = %s", (conference_id,))
    return _row_dict(cur.fetchone(), CONFERENCE_KEYS)


def _conference_row_or_404(cur, conference_id):
    row = get_conference_row(cur, conference_id)
    if row is None:
        raise PressNotFound(f"Press conference {conference_id} not found")
    return row


def _require_user(row):
    if str((row or {}).get("controller") or "").upper() != "USER":
        raise PressForbidden("This press conference belongs to an AI coach.")


def _require_status(row, *statuses):
    status = str((row or {}).get("status") or "").upper()
    if status not in statuses:
        raise PressConflict(f"Press conference {row.get('id')} is {status}; expected {' or '.join(statuses)}.")


ATTENDEE_KEYS = ("agent_id", "person_key", "role", "pool_tier", "questions", "passes", "done", "hands", "picked",
                 "judgment", "display_name", "handle")


def _load_attendees(cur, conference_id):
    cur.execute(
        "/* q:attendees */ SELECT pa.agent_id, pa.person_key, pa.role, pa.pool_tier, pa.questions, pa.passes, "
        "pa.done, pa.hands, pa.picked, pa.judgment, COALESCE(am.display_name, pa.agent_id), "
        "COALESCE(am.handle, '@' || pa.agent_id) "
        "FROM press_attendees pa LEFT JOIN agent_memories am ON am.agent_id = pa.agent_id "
        "WHERE pa.conference_id = %s ORDER BY CASE pa.role WHEN 'BEAT' THEN 0 ELSE 1 END, pa.agent_id",
        (conference_id,))
    out = []
    for row in cur.fetchall():
        item = dict(zip(ATTENDEE_KEYS, row))
        item["judgment"] = _loads(item.get("judgment"), item.get("judgment"))
        item["done"] = bool(item.get("done"))
        for key in ("questions", "passes", "hands", "picked"):
            item[key] = int(item.get(key) or 0)
        out.append(item)
    return out


TURN_KEYS = ("id", "seq", "speaker", "agent_id", "kind", "reply_to", "text", "about", "tone", "source", "issues",
             "on_record", "display_name", "handle", "role", "pool_tier")


def _load_turns(cur, conference_id):
    cur.execute(
        "/* q:turns */ SELECT pt.id, pt.seq, pt.speaker, pt.agent_id, pt.kind, pt.reply_to, pt.text, pt.about, "
        "pt.tone, pt.source, pt.issues, pt.on_record, am.display_name, am.handle, pa.role, pa.pool_tier "
        "FROM press_turns pt LEFT JOIN agent_memories am ON am.agent_id = pt.agent_id "
        "LEFT JOIN press_attendees pa ON pa.conference_id = pt.conference_id AND pa.agent_id = pt.agent_id "
        "WHERE pt.conference_id = %s ORDER BY pt.seq, pt.id", (conference_id,))
    out = []
    for row in cur.fetchall():
        item = dict(zip(TURN_KEYS, row))
        item["about"] = _loads(item.get("about"), []) or []
        item["issues"] = _loads(item.get("issues"), []) or []
        item["on_record"] = bool(item.get("on_record"))
        out.append(item)
    return out


def _load_hand_rounds(cur, conference_id):
    try:
        cur.execute("/* q:hand_rounds */ SELECT agent_id, round, picked FROM press_hands "
                    "WHERE conference_id = %s ORDER BY round, agent_id", (conference_id,))
        rows = cur.fetchall()
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for agent_id, round_no, picked in rows:
        out.setdefault(agent_id, []).append({"round": int(round_no), "picked": bool(picked)})
    return out


# -------------------------------------------------------------------------------------------- state builders
def _sheet(row):
    return (row or {}).get("facts") or {}


def _team_facts(row, team_id=None):
    sheet = _sheet(row)
    wanted = team_id if team_id is not None else (row or {}).get("team_id")
    for team in sheet.get("teams") or []:
        if team.get("team_id") == wanted:
            return team
    return None


def _opponent_facts(row):
    for team in _sheet(row).get("teams") or []:
        if team.get("team_id") != (row or {}).get("team_id"):
            return team
    return None


def _result(row):
    """{"outcome", "margin", "mine", "theirs", "label"} for the subject team, or None before a score exists."""
    mine = _team_facts(row)
    final = (_sheet(row).get("game") or {}).get("final") or {}
    if not mine or final.get("home") is None or final.get("away") is None:
        return None
    home = bool(mine.get("home"))
    a = final.get("home") if home else final.get("away")
    b = final.get("away") if home else final.get("home")
    if a is None or b is None:
        return None
    outcome = "win" if a > b else "loss" if a < b else "tie"
    past = {"win": "won", "loss": "lost", "tie": "tied"}[outcome]
    return {"outcome": outcome, "margin": abs(a - b), "mine": int(a), "theirs": int(b),
            "label": f"{past} {a}-{b}"}


def _game_label(row):
    week = (row or {}).get("week") or ((_sheet(row).get("game") or {}).get("week"))
    opponent = _opponent_facts(row)
    parts = []
    if week:
        parts.append(f"Week {week}")
    if opponent and opponent.get("name"):
        parts.append(f"vs {opponent['name']}")
    label = " ".join(parts) or "the game"
    result = _result(row)
    if result:
        label += f", {result['label']}"
    return label


def _subject(row):
    team = _team_facts(row) or {}
    coach = team.get("coach") or {}
    subject_id = (row or {}).get("subject_id")
    subject = {
        "id": subject_id,
        "name": (row or {}).get("subject_name") or coach.get("name") or "the head coach",
        "team_id": (row or {}).get("team_id"),
        "team_name": team.get("name") or (row or {}).get("team_name"),
        "team_abbr": (row or {}).get("abbreviation") or team.get("abbr"),
        "team_key": f"team:{(row or {}).get('team_id')}" if (row or {}).get("team_id") is not None else None,
        "persona_id": str(subject_id) if subject_id is not None else None,
        "role": (row or {}).get("subject_type") or "coach",
        "tenure": coach.get("tenure"),
        "schemes": coach.get("schemes"),
        "tendencies": coach.get("tendencies"),
        "record": team.get("record_after"),
        "hot_seat": coach.get("hot_seat"),
        "profile": coach,
    }
    return subject


def _bag(row):
    """The session scratch state from ``press_conferences.patience`` with every key defaulted."""
    raw = _loads((row or {}).get("patience"), {}) or {}
    if not isinstance(raw, dict):
        raw = {}
    controller = str((row or {}).get("controller") or "AGENT").upper()
    budget = raw.get("budget")
    if budget is None and controller == "AGENT":
        budget = press_agents.patience_budget(_subject(row), _sheet(row))
    bag = {
        "budget": budget,
        "spent": int(raw.get("spent") or 0),
        "mood": raw.get("mood"),
        "drafts": dict(raw.get("drafts") or {}),
        "round": int(raw.get("round") or 0),
        "picks": {str(k): int(v or 0) for k, v in (raw.get("picks") or {}).items()},
        "hand_counts": {str(k): int(v or 0) for k, v in (raw.get("hand_counts") or {}).items()},
        "done": [str(x) for x in (raw.get("done") or [])],
        "passes_in_a_row": {str(k): int(v or 0) for k, v in (raw.get("passes_in_a_row") or {}).items()},
        "questions_asked": int(raw.get("questions_asked") or 0),
        "questions_by_agent": {str(k): int(v or 0) for k, v in (raw.get("questions_by_agent") or {}).items()},
        "follow_ups_in_a_row": int(raw.get("follow_ups_in_a_row") or 0),
        "wants_to_end": bool(raw.get("wants_to_end")),
        "picked_turn_id": raw.get("picked_turn_id"),
        "answered_turn_id": raw.get("answered_turn_id"),
        "seq": int(raw.get("seq") or 0),
    }
    return bag


def _save_patience(cur, conference_id, bag):
    cur.execute("/* q:save_patience */ UPDATE press_conferences SET patience = %s WHERE id = %s",
                (_json(bag), conference_id))


def _save_status(cur, conference_id, status, end_reason=None, started=False, closed=False):
    cur.execute("/* q:save_status */ UPDATE press_conferences SET status = %s, end_reason = %s, "
                "started_at = CASE WHEN %s THEN COALESCE(started_at, NOW()) ELSE started_at END, "
                "closed_at = CASE WHEN %s THEN NOW() ELSE closed_at END WHERE id = %s",
                (status, end_reason, bool(started), bool(closed), conference_id))


def _save_counters(cur, conference_id, llm):
    if not isinstance(llm, MeteredLLM):
        return
    cur.execute("/* q:save_counters */ UPDATE press_conferences SET calls = calls + %s, "
                "input_tokens = input_tokens + %s, output_tokens = output_tokens + %s WHERE id = %s",
                (llm.calls, llm.input_tokens, llm.output_tokens, conference_id))


def _meter_snapshot(llm):
    if not isinstance(llm, MeteredLLM):
        return (0, 0, 0)
    return (llm.calls, llm.input_tokens, llm.output_tokens)


def _save_counters_since(cur, conference_id, llm, before):
    """Add only the calls made since ``before`` (the judgment pass after the session already counted its own)."""
    if not isinstance(llm, MeteredLLM):
        return
    calls = llm.calls - before[0]
    inp = llm.input_tokens - before[1]
    out = llm.output_tokens - before[2]
    if calls or inp or out:
        cur.execute("/* q:save_counters */ UPDATE press_conferences SET calls = calls + %s, "
                    "input_tokens = input_tokens + %s, output_tokens = output_tokens + %s WHERE id = %s",
                    (calls, inp, out, conference_id))


def _save_attendee(cur, conference_id, attendee, bag):
    agent_id = attendee.get("agent_id")
    questions = int((bag.get("questions_by_agent") or {}).get(agent_id, 0))
    cur.execute("/* q:save_attendee */ UPDATE press_attendees SET questions = %s, passes = %s, done = %s, "
                "hands = %s, picked = %s WHERE conference_id = %s AND agent_id = %s",
                (questions, int((bag.get("passes_in_a_row") or {}).get(agent_id, 0)),
                 agent_id in set(bag.get("done") or []), int((bag.get("hand_counts") or {}).get(agent_id, 0)),
                 int((bag.get("picks") or {}).get(agent_id, 0)), conference_id, agent_id))


def _save_judgment(cur, conference_id, agent_id, judgment):
    cur.execute("/* q:save_judgment */ UPDATE press_attendees SET judgment = %s "
                "WHERE conference_id = %s AND agent_id = %s", (_json(judgment), conference_id, agent_id))


def _session_state(cur, row, attendees=None, turns=None, bag=None):
    attendees = _load_attendees(cur, row["id"]) if attendees is None else attendees
    turns = _load_turns(cur, row["id"]) if turns is None else turns
    bag = _bag(row) if bag is None else bag
    subject = _subject(row)
    controller = str(row.get("controller") or "AGENT").upper()
    picks = bag.get("picks") or {}
    hand_counts = bag.get("hand_counts") or {}
    skip = {}
    if subject.get("id") is not None:
        for attendee in attendees:
            for key in (attendee.get("person_key"), attendee.get("agent_id")):
                if key and key not in skip:
                    try:
                        skip[key] = press_agents.skip_history(cur, key, subject["id"], int(row.get("season") or 0))
                    except Exception:  # noqa: BLE001
                        skip[key] = {"hands": 0, "picked": 0, "skipped": 0, "pressers": 0}
    memory_blocks = {}
    for attendee in attendees:
        try:
            block = agent_memory.build_memory_block(
                cur, attendee.get("agent_id"), int(row.get("season") or 0), int(row.get("week") or 1),
                subject_team_id=subject.get("team_id"))
        except Exception:  # noqa: BLE001
            block = ""
        if block:
            memory_blocks[attendee.get("agent_id")] = block
    last_answer, last_asker = _last_answer_and_asker(turns)
    last_question = None
    if last_answer is not None:
        last_question = next((t for t in turns if t.get("id") == last_answer.get("reply_to")), None)
    patience = None
    if controller == "AGENT":
        patience = {"budget": int(bag.get("budget") or 0), "spent": int(bag.get("spent") or 0)}
    max_seq = max([int(t.get("seq") or 0) for t in turns] or [0])
    if bag.get("seq", 0) < max_seq:
        bag["seq"] = max_seq
    return {
        "conference_id": row["id"],
        "season": row.get("season"),
        "week": row.get("week"),
        "subject": subject,
        "sheet": _sheet(row),
        "transcript": turns,
        "turns": turns,
        "hands": [],
        "picks": picks,
        "hand_counts": hand_counts,
        "last_answer": last_answer,
        "last_question": last_question,
        "last_asker": last_asker,
        "questions_asked": int(bag.get("questions_asked") or 0),
        "follow_ups_in_a_row": int(bag.get("follow_ups_in_a_row") or 0),
        "passes_in_a_row": bag.get("passes_in_a_row") or {},
        "done": list(bag.get("done") or []),
        "mood": bag.get("mood") or 3,
        "patience": patience,
        "wants_to_end": bool(bag.get("wants_to_end")),
        "controller": controller,
        "game_label": _game_label(row),
        "memory_blocks": memory_blocks,
        "relationship_contexts": {},
        "skip_history": skip,
        "attendees": attendees,
        "cur": cur,
        "universe_id": _universe_id(cur),
        "league_id": _league_id(cur),
    }


def _last_answer_and_asker(turns):
    for turn in reversed(turns or []):
        if turn.get("speaker") == "SUBJECT" and str(turn.get("kind") or "").upper() in ("ANSWER", "NO_COMMENT"):
            question = next((t for t in turns if t.get("id") == turn.get("reply_to")), None)
            return turn, (question or {}).get("agent_id")
    return None, None


# ----------------------------------------------------------------------------------------------- turn writes
def _next_seq(bag):
    bag["seq"] = int(bag.get("seq") or 0) + 1
    return bag["seq"]


def _insert_turn(cur, row, bag, speaker, kind, text, *, agent_id=None, about=None, tone=None, reply_to=None,
                 source="AGENT", issues=None, retries=0, on_record=True):
    seq = _next_seq(bag)
    cur.execute("/* q:insert_turn */ INSERT INTO press_turns (conference_id, seq, speaker, agent_id, kind, "
                "reply_to, text, about, tone, source, issues, retries, on_record) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (row["id"], seq, speaker, agent_id, kind, reply_to, text, _json(list(about or [])), tone, source,
                 _json(list(issues or [])), int(retries or 0), bool(on_record)))
    result = cur.fetchone()
    turn_id = result[0] if result else None
    return turn_id, seq


def _turn_payload(row, turn):
    reporter = None
    if turn.get("speaker") == "REPORTER":
        reporter = {"agent_id": turn.get("agent_id"), "display_name": turn.get("display_name") or turn.get("agent_id"),
                    "handle": turn.get("handle") or "", "role": turn.get("role"), "pool_tier": turn.get("pool_tier")}
    return {"id": turn.get("id"), "seq": turn.get("seq"), "speaker": turn.get("speaker"), "kind": turn.get("kind"),
            "reporter": reporter, "text": turn.get("text") or "", "reply_to": turn.get("reply_to"),
            "source": turn.get("source"), "issues": turn.get("issues") or [], "on_record": bool(turn.get("on_record"))}


def _turn_as_dict(cur, row, turn_id):
    for turn in _load_turns(cur, row["id"]):
        if turn.get("id") == turn_id:
            return turn
    return None


# ---------------------------------------------------------------------------------------------- hand writes
def _record_hands(cur, row, round_no, hands):
    for hand in hands:
        cur.execute("/* q:insert_hand */ INSERT INTO press_hands (conference_id, round, agent_id, picked, "
                    "pick_source) VALUES (%s, %s, %s, FALSE, NULL) "
                    "ON CONFLICT (conference_id, round, agent_id) DO NOTHING",
                    (row["id"], round_no, hand["agent_id"]))


def _pick_hand(cur, row, round_no, agent_id, source):
    cur.execute("/* q:pick_hand */ INSERT INTO press_hands (conference_id, round, agent_id, picked, pick_source) "
                "VALUES (%s, %s, %s, TRUE, %s) "
                "ON CONFLICT (conference_id, round, agent_id) DO UPDATE SET picked = TRUE, "
                "pick_source = EXCLUDED.pick_source",
                (row["id"], round_no, agent_id, source))


def _hand_shape(attendee, action, picks):
    agent_id = attendee.get("agent_id")
    kind = str((action or {}).get("action") or "").upper()
    return {"agent_id": agent_id,
            "display_name": attendee.get("display_name") or agent_id,
            "handle": attendee.get("handle") or "",
            "role": attendee.get("role"),
            "pool_tier": attendee.get("pool_tier"),
            "picked_so_far": int((picks or {}).get(agent_id, 0) or 0),
            "follow_up": kind == "FOLLOW_UP"}


def current_hands(cur, conference_id):
    """The current round's raised hands (no question text), from the stored drafts. [] when none."""
    row = get_conference_row(cur, conference_id)
    if row is None:
        return []
    bag = _bag(row)
    attendees = {a["agent_id"]: a for a in _load_attendees(cur, conference_id)}
    out = []
    for agent_id in bag.get("drafts") or {}:
        attendee = attendees.get(agent_id)
        if attendee:
            out.append(_hand_shape(attendee, bag["drafts"][agent_id], bag.get("picks")))
    order = {a["agent_id"]: i for i, a in enumerate(attendees.values())}
    return sorted(out, key=lambda h: order.get(h["agent_id"], 999))


# -------------------------------------------------------------------------------------------- conference dict
def build_conference(cur, conference_id, include_hands=None):
    """The §2.11 ``Conference`` shape (team/opponent abbreviations, score from the subject's side). None if
    missing or when the press tables don't exist."""
    row = get_conference_row(cur, conference_id)
    if row is None:
        return None
    attendees = _load_attendees(cur, conference_id)
    turns = _load_turns(cur, conference_id)
    team = _team_facts(row) or {}
    opponent = _opponent_facts(row) or {}
    result = _result(row)
    payload = {
        "id": row["id"],
        "season": row.get("season"),
        "week": row.get("week"),
        "game_id": row.get("game_id"),
        "team": row.get("abbreviation") or team.get("abbr"),
        "opponent": opponent.get("abbr"),
        "result": {"win": "W", "loss": "L", "tie": "T"}.get((result or {}).get("outcome")),
        "score": f"{result['mine']}-{result['theirs']}" if result else None,
        "subject_name": row.get("subject_name"),
        "controller": row.get("controller"),
        "status": row.get("status"),
        "end_reason": row.get("end_reason"),
        "tier": row.get("tier"),
        "attendees": [{"agent_id": a["agent_id"], "display_name": a.get("display_name"),
                       "handle": a.get("handle"), "role": a.get("role"), "pool_tier": a.get("pool_tier"),
                       "person_key": a.get("person_key"), "questions": a.get("questions"),
                       "passes": a.get("passes"), "hands": a.get("hands"), "picked": a.get("picked"),
                       "done": a.get("done")} for a in attendees],
        "turns": [_turn_payload(row, t) for t in turns],
    }
    if include_hands is None:
        include_hands = str(row.get("status") or "").upper() == "LIVE"
    if include_hands:
        payload["hands"] = current_hands(cur, conference_id)
    return payload


# ------------------------------------------------------------------------------------------ the session loop
def _about_ids(item):
    """The set of (type, id) a turn/hand/action touches."""
    out = set()
    for part in (item or {}).get("about") or []:
        if isinstance(part, dict):
            out.add((str(part.get("type") or ""), part.get("id")))
    return out


def _parallel(funcs):
    if not funcs:
        return []
    with ThreadPoolExecutor(max_workers=min(MAX_ROUND_CALLS, len(funcs))) as pool:
        futures = [pool.submit(func) for func in funcs]
        out = []
        for future in futures:
            try:
                out.append(future.result())
            except Exception as exc:  # noqa: BLE001 - one reporter failing never kills the round
                out.append({"action": "PASS", "text": "", "about": [], "tone": "neutral", "reply_to": None,
                            "source": "FALLBACK", "retries": 0, "issues": [], "dropped": True,
                            "problem": f"reporter error: {exc}"})
    return out


def _run_round(cur, row, bag, state, llm):
    """Poll the reporters, write this round's hands, and return the raised hands. Uses the drafted hands for
    reporters whose ``about`` the last answer did not touch (``PRESS_REPOLL = "changed"``)."""
    attendees = state.get("attendees") or []
    done = set(bag.get("done") or [])
    eligible = [a for a in attendees if a.get("agent_id") not in done]
    kept = {}
    if PRESS_REPOLL != "all" and bag.get("drafts") and state.get("last_answer"):
        changed = _about_ids(state["last_answer"]) | _about_ids(state.get("last_question"))
        for attendee in eligible:
            draft = bag["drafts"].get(attendee.get("agent_id"))
            if draft and attendee.get("agent_id") != state.get("last_asker") and not (_about_ids(draft) & changed):
                kept[attendee["agent_id"]] = draft
    poll = [a for a in eligible if a.get("agent_id") not in kept]
    results = _parallel([(lambda a=a: press_agents.reporter_turn(a, state, llm)) for a in poll])

    drafts = dict(kept)
    passes = dict(bag.get("passes_in_a_row") or {})
    for attendee, result in zip(poll, results):
        agent_id = attendee.get("agent_id")
        if result.get("dropped") or str(result.get("action") or "").upper() == "PASS":
            passes[agent_id] = int(passes.get(agent_id, 0)) + 1
            if passes[agent_id] >= press_agents.PASSES_IN_A_ROW_DONE:
                done.add(agent_id)
        else:
            passes[agent_id] = 0
            drafts[agent_id] = result
    bag["drafts"] = drafts
    bag["passes_in_a_row"] = passes
    bag["done"] = sorted(done)
    bag["round"] = int(bag.get("round") or 0) + 1
    hands = [_hand_shape(a, drafts[a["agent_id"]], bag.get("picks"))
             for a in eligible if a.get("agent_id") in drafts]
    hand_counts = dict(bag.get("hand_counts") or {})
    for hand in hands:
        hand_counts[hand["agent_id"]] = int(hand_counts.get(hand["agent_id"], 0)) + 1
    bag["hand_counts"] = hand_counts
    _record_hands(cur, row, bag["round"], [{"agent_id": h["agent_id"]} for h in hands])
    for attendee in attendees:
        attendee["_turns"] = state.get("turns") or []
        _save_attendee(cur, row["id"], attendee, bag)
    _save_patience(cur, row["id"], bag)
    return hands


def _repeated_topic(turns, about):
    wanted = _about_ids({"about": about})
    if not wanted:
        return False
    for turn in turns:
        if turn.get("speaker") == "REPORTER" and _about_ids(turn) & wanted:
            return True
    return False


def _ask(cur, row, bag, state, attendee, action, pick_source):
    action_kind = str(action.get("action") or "ASK").upper()
    kind = "QUESTION" if action_kind == "ASK" else action_kind
    reply_to = action.get("reply_to")
    if kind == "FOLLOW_UP":
        reply_to = (state.get("last_answer") or {}).get("id")
    elif kind == "REACT":
        valid = {t.get("id") for t in state.get("turns") or []}
        reply_to = reply_to if reply_to in valid else (state.get("last_answer") or {}).get("id")
    else:
        reply_to = None
    turn_id, seq = _insert_turn(
        cur, row, bag, "REPORTER", kind, action.get("text") or "", agent_id=attendee.get("agent_id"),
        about=action.get("about"), tone=action.get("tone"), reply_to=reply_to,
        source=action.get("source") or "AGENT", issues=action.get("issues"), retries=action.get("retries", 0))
    _pick_hand(cur, row, bag["round"], attendee["agent_id"], pick_source)
    picks = dict(bag.get("picks") or {})
    picks[attendee["agent_id"]] = int(picks.get(attendee["agent_id"], 0)) + 1
    bag["picks"] = picks
    bag["questions_asked"] = int(bag.get("questions_asked") or 0) + 1
    by_agent = dict(bag.get("questions_by_agent") or {})
    by_agent[attendee["agent_id"]] = int(by_agent.get(attendee["agent_id"], 0)) + 1
    bag["questions_by_agent"] = by_agent
    bag["follow_ups_in_a_row"] = int(bag.get("follow_ups_in_a_row") or 0) + 1 if kind == "FOLLOW_UP" else 0
    bag["picked_turn_id"] = turn_id
    bag["answered_turn_id"] = None
    _save_patience(cur, row["id"], bag)
    turn = {"id": turn_id, "seq": seq, "speaker": "REPORTER", "agent_id": attendee.get("agent_id"), "kind": kind,
            "reply_to": reply_to, "text": action.get("text") or "", "about": action.get("about") or [],
            "tone": action.get("tone"), "source": action.get("source") or "AGENT",
            "issues": action.get("issues") or [], "on_record": True, "display_name": attendee.get("display_name"),
            "handle": attendee.get("handle"), "role": attendee.get("role"), "pool_tier": attendee.get("pool_tier")}
    return turn, kind, action.get("tone")


def _answer_ai(cur, row, bag, state, llm, question_turn):
    reply = press_agents.coach_turn(_subject(row), state, llm, "ANSWER")
    kind = "ANSWER"
    turn_id, seq = _insert_turn(cur, row, bag, "SUBJECT", kind, reply.get("text") or "",
                                reply_to=question_turn.get("id"), source=reply.get("source") or "AGENT",
                                issues=reply.get("issues"), retries=reply.get("retries", 0))
    bag["mood"] = reply.get("mood") if reply.get("mood") in (1, 2, 3, 4, 5) else 3
    bag["wants_to_end"] = bool(reply.get("wants_to_end"))
    prior = [t for t in (state.get("turns") or []) if t.get("id") != question_turn.get("id")]
    repeated = _repeated_topic(prior, question_turn.get("about"))
    bag["spent"] = int(bag.get("spent") or 0) + press_agents.question_cost(question_turn.get("tone"), repeated)
    bag["answered_turn_id"] = turn_id
    bag["picked_turn_id"] = None
    _save_patience(cur, row["id"], bag)
    return {"id": turn_id, "seq": seq, "speaker": "SUBJECT", "agent_id": None, "kind": kind,
            "reply_to": question_turn.get("id"), "text": reply.get("text") or "", "about": [], "tone": None,
            "source": reply.get("source") or "AGENT", "issues": reply.get("issues") or [], "on_record": True}


def _answer_user(cur, row, bag, question_turn, text, no_comment):
    kind = "NO_COMMENT" if no_comment else "ANSWER"
    body = "No comment." if no_comment else (text or "")
    issues = []
    if not no_comment:
        try:
            issues = press_facts.check_line(body, _sheet(row), _subject(row).get("name") or "") or []
        except Exception:  # noqa: BLE001 - warnings only for JR's words
            issues = []
    turn_id, seq = _insert_turn(cur, row, bag, "SUBJECT", kind, body, reply_to=question_turn.get("id"),
                                source="USER", issues=issues)
    bag["answered_turn_id"] = turn_id
    bag["picked_turn_id"] = None
    _save_patience(cur, row["id"], bag)
    return {"id": turn_id, "seq": seq, "speaker": "SUBJECT", "agent_id": None, "kind": kind,
            "reply_to": question_turn.get("id"), "text": body, "about": [], "tone": None, "source": "USER",
            "issues": issues, "on_record": True}


def _close_ai(cur, row, bag, state, llm, reason):
    reply = press_agents.coach_turn(_subject(row), state, llm, "CLOSING")
    _insert_turn(cur, row, bag, "SUBJECT", "CLOSING", reply.get("text") or "", source=reply.get("source") or "AGENT",
                 issues=reply.get("issues"), retries=reply.get("retries", 0))
    _save_patience(cur, row["id"], bag)
    _save_counters(cur, row["id"], llm)
    _save_status(cur, row["id"], "CLOSED", reason, closed=True)
    return {"conference_id": row["id"], "status": "CLOSED", "end_reason": reason}


def run_session(cur_factory, conference_id, llm, user_answer=None):
    """Drive one presser as the §2.6 state machine and return its outcome.

    An AI session runs to CLOSED and returns ``{"conference_id", "status", "end_reason"}``. A user-controlled
    session returns ``{"awaiting_pick": [Hand]}`` after drafting the first round, then
    ``{"awaiting_answer": turn_id}`` once a pick has been made; ``user_answer`` (``{"text", "no_comment"}``)
    resumes it. Nothing is committed here.
    """
    cur = _cursor_scope(cur_factory)
    row = _conference_row_or_404(cur, conference_id)
    if str(row.get("status") or "").upper() in ("CLOSED", "DECLINED"):
        return {"conference_id": row["id"], "status": row["status"], "end_reason": row.get("end_reason")}
    controller = str(row.get("controller") or "AGENT").upper()
    llm = _meter(llm)
    bag = _bag(row)
    state = _session_state(cur, row, bag=bag)

    if controller == "USER":
        if str(row.get("status") or "").upper() == "SCHEDULED":
            _save_status(cur, row["id"], "LIVE", started=True)
        if user_answer is not None:
            question = _turn_as_dict(cur, row, bag.get("picked_turn_id"))
            if question is None:
                raise PressConflict("There is no picked question to answer.")
            if isinstance(user_answer, str):
                answer_text, no_comment = user_answer, False
            else:
                answer_text = (user_answer or {}).get("text")
                no_comment = bool((user_answer or {}).get("no_comment"))
            answer = _answer_user(cur, row, bag, question, answer_text, no_comment)
            state = _session_state(cur, row, bag=bag)
            reason = press_agents.end_condition(state)
            if reason:
                _save_status(cur, row["id"], "REVIEW", reason)
                return {"conference_id": row["id"], "status": "REVIEW", "answer": _turn_payload(row, answer),
                        "hands": [], "ended": True, "end_reason": reason}
            hands = _run_round(cur, row, bag, state, llm)
            return {"conference_id": row["id"], "answer": _turn_payload(row, answer),
                    "hands": _public_hands(hands), "ended": False, "end_reason": None}
        if bag.get("picked_turn_id") is not None:
            return {"conference_id": row["id"], "status": "LIVE",
                    "awaiting_answer": bag["picked_turn_id"]}
        if not bag.get("drafts"):
            state = _session_state(cur, row, bag=bag)
            hands = _run_round(cur, row, bag, state, llm)
        else:
            hands = current_hands(cur, row["id"])
        _save_counters(cur, row["id"], llm)
        return {"conference_id": row["id"], "status": "LIVE", "awaiting_pick": hands}

    # ------- AI subject: OPENING, then rounds until an end condition
    opening = press_agents.coach_turn(_subject(row), state, llm, "OPENING")
    _insert_turn(cur, row, bag, "SUBJECT", "OPENING", opening.get("text") or "",
                 source=opening.get("source") or "AGENT", issues=opening.get("issues"),
                 retries=opening.get("retries", 0))
    bag["mood"] = opening.get("mood") if opening.get("mood") in (1, 2, 3, 4, 5) else 3
    _save_patience(cur, row["id"], bag)
    _save_status(cur, row["id"], "LIVE", started=True)

    while True:
        state = _session_state(cur, row, bag=bag)
        if press_agents.end_condition(state) or not (
                [a for a in state["attendees"] if a["agent_id"] not in set(bag.get("done") or [])]):
            reason = press_agents.end_condition(state) or "RAN_OUT"
            return _close_ai(cur, row, bag, state, llm, reason)
        hands = _run_round(cur, row, bag, state, llm)
        if not hands:
            state = _session_state(cur, row, bag=bag)
            return _close_ai(cur, row, bag, state, llm, "RAN_OUT")
        pick = press_agents.coach_pick(_subject(row), hands, state, llm)
        agent_id = pick.get("agent_id")
        attendee = next((a for a in state["attendees"] if a["agent_id"] == agent_id), None)
        if attendee is None:
            fallback = press_agents.fallback_pick(hands, state)
            agent_id = fallback.get("agent_id")
            attendee = next((a for a in state["attendees"] if a["agent_id"] == agent_id), None)
            pick = {"source": "FALLBACK"}
        if attendee is None:
            return _close_ai(cur, row, bag, state, llm, "RAN_OUT")
        question, _kind, _tone = _ask(cur, row, bag, state, attendee, bag["drafts"][agent_id],
                                      pick.get("source") or "AGENT")
        state = _session_state(cur, row, bag=bag)
        answer = _answer_ai(cur, row, bag, state, llm, question)
        state = _session_state(cur, row, bag=bag)
        reason = press_agents.end_condition(state)
        if reason:
            state["last_answer"] = answer
            return _close_ai(cur, row, bag, state, llm, reason)


def _public_hands(hands):
    return [dict(h) for h in (hands or [])]


# ----------------------------------------------------------------------------------------------- scheduling
def _facts_for_game(cur, game_id, casts):
    """One frozen fact sheet per game with the whole cast's display names added before freezing (§2.3, U4 note)."""
    try:
        facts = press_facts.fact_sheet(cur, game_id)
    except Exception:  # noqa: BLE001 - a sheet problem must not stop the other games
        return {}
    if not facts:
        return facts
    names = list(facts.get("names") or [])
    for cast in casts:
        for attendee in (cast.get("nationals") or []) + [a for group in (cast.get("beats") or {}).values()
                                                         for a in group]:
            name = attendee.get("display_name")
            if name:
                names.append(name)
    facts["names"] = sorted({str(n) for n in names if n})
    return facts


def _subject_for_conference(cur, team_id, team_facts, season):
    coach = (team_facts or {}).get("coach") or {}
    subject_id = coach.get("id")
    controller = subject_controller(cur, team_id)
    return {
        "subject_type": "coach",
        "subject_id": subject_id,
        "subject_name": coach.get("name") or f"{(team_facts or {}).get('name') or 'Team'} head coach",
        "controller": controller,
    }


def _conference_exists(cur, game_id, team_id, subject_type, subject_id):
    cur.execute("/* q:existing_conference */ SELECT id FROM press_conferences "
                "WHERE game_id = %s AND team_id = %s AND subject_type = %s "
                "AND subject_id IS NOT DISTINCT FROM %s LIMIT 1",
                (game_id, team_id, subject_type, subject_id))
    row = cur.fetchone()
    return row[0] if row else None


def _attendees_from_cast(cast, team_id):
    beat = list((cast.get("beats") or {}).get(team_id) or [])
    national = list(cast.get("nationals") or [])
    return beat + national


def _insert_attendees(cur, conference_id, attendees):
    for attendee in attendees:
        cur.execute("/* q:insert_attendee */ INSERT INTO press_attendees "
                    "(conference_id, agent_id, person_key, role, pool_tier) VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (conference_id, agent_id) DO NOTHING",
                    (conference_id, attendee.get("agent_id"), attendee.get("person_key") or attendee.get("agent_id"),
                     attendee.get("role") or "NATIONAL", attendee.get("pool_tier")))


def _draft_first_round(cur, row, llm):
    """Draft the first round of hands for a user presser in advance (one parallel model round)."""
    bag = _bag(row)
    state = _session_state(cur, row, bag=bag)
    attendees = [a for a in state["attendees"] if a["agent_id"] not in set(bag.get("done") or [])]
    results = _parallel([(lambda a=a: press_agents.reporter_turn(a, state, llm)) for a in attendees])
    drafts = {}
    for attendee, result in zip(attendees, results):
        if not result.get("dropped") and str(result.get("action") or "").upper() != "PASS":
            drafts[attendee["agent_id"]] = result
    bag["drafts"] = drafts
    bag["round"] = int(bag.get("round") or 0) + 1
    hand_counts = dict(bag.get("hand_counts") or {})
    hands = []
    for attendee in attendees:
        if attendee["agent_id"] in drafts:
            hands.append({"agent_id": attendee["agent_id"]})
            hand_counts[attendee["agent_id"]] = int(hand_counts.get(attendee["agent_id"], 0)) + 1
    bag["hand_counts"] = hand_counts
    _record_hands(cur, row, bag["round"], hands)
    _save_patience(cur, row["id"], bag)
    for attendee in state["attendees"]:
        attendee["_turns"] = state.get("turns") or []
        _save_attendee(cur, row["id"], attendee, bag)
    _save_counters(cur, row["id"], llm)
    return current_hands(cur, row["id"])


def schedule_week(cur, season, week, llm=None):
    """Cast one week and insert every game's two pressers (AI and user). Idempotent.

    User subjects get their first round of hands drafted in advance when a model is supplied, so
    ``start_user`` can show the room at once. A dry run passes no model.
    """
    season, week = int(season), int(week)
    if not _installed(cur):
        return {"scheduled": 0, "user": 0, "agent": 0, "conferences": [], "games": 0}
    casts = press_cast.cast_week(cur, season, week)
    created, user, agent = [], 0, 0
    for game_id, cast in sorted(casts.items()):
        facts = _facts_for_game(cur, game_id, [cast])
        teams = {t.get("team_id"): t for t in (facts.get("teams") or [])}
        for team_id in ((cast.get("beats") or {}).keys()):
            team_facts = teams.get(team_id) or {}
            subject = _subject_for_conference(cur, team_id, team_facts, season)
            existing = _conference_exists(cur, game_id, team_id, subject["subject_type"], subject["subject_id"])
            if existing is not None:
                created.append(existing)
                continue
            model = getattr(llm, "model", None) or PRESS_MODEL
            cur.execute("/* q:insert_conference */ INSERT INTO press_conferences "
                        "(season, week, game_id, team_id, subject_type, subject_id, subject_name, controller, "
                        "interest, tier, cast_inputs, facts, model) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                        "%s, %s, %s) RETURNING id",
                        (season, week, game_id, team_id, subject["subject_type"], subject["subject_id"],
                         subject["subject_name"], subject["controller"], cast.get("interest"), cast.get("tier"),
                         _json(cast.get("inputs") or {}), _json(facts), model))
            conference_id = cur.fetchone()[0]
            _insert_attendees(cur, conference_id, _attendees_from_cast(cast, team_id))
            created.append(conference_id)
            if subject["controller"] == "USER":
                user += 1
                if llm is not None:
                    row = get_conference_row(cur, conference_id)
                    try:
                        _draft_first_round(cur, row, _local_meter(llm))
                    except Exception:  # noqa: BLE001 - a drafted-hand failure leaves it to start_user
                        pass
            else:
                agent += 1
    return {"scheduled": len(created), "user": user, "agent": agent, "conferences": created, "games": len(casts)}


# ------------------------------------------------------------------------------------------------ the runner
def _week_conferences(cur, season, week, team_ids=None):
    sql = ("/* q:week_conferences */ SELECT id, status, controller, team_id FROM press_conferences "
           "WHERE season = %s AND week = %s")
    params = [int(season), int(week)]
    if team_ids:
        sql += " AND team_id = ANY(%s)"
        params.append(list(team_ids))
    sql += " ORDER BY id"
    cur.execute(sql, tuple(params))
    return [{"id": r[0], "status": r[1], "controller": r[2], "team_id": r[3]} for r in cur.fetchall()]


def _mark_failed(conn_info, conference_id, error):
    try:
        with _connect(conn_info) as conn:
            with conn.cursor() as cur:
                cur.execute("/* q:mark_failed */ UPDATE press_conferences SET status = 'FAILED' "
                            "WHERE id = %s AND status <> 'CLOSED'", (conference_id,))
            conn.commit()
    except Exception:  # noqa: BLE001 - a failure marker never masks the original failure
        pass
    print(f"[press_room] conference {conference_id} FAILED: {error}", file=sys.stderr, flush=True)


def _run_conference(conn_info, conference_id, llm):
    """One AI presser on its own connection: run the session, close its effects, commit. Returns the summary."""
    local = _local_meter(llm)
    with _connect(conn_info) as conn:
        try:
            with conn.cursor() as cur:
                summary = run_session(lambda: cur, conference_id, local)
                if summary.get("status") == "CLOSED":
                    _close_effects_cur(cur, conference_id, local)
            conn.commit()
            return summary
        except Exception:
            conn.rollback()
            raise


def run_week(conn_info, season, week, workers=PRESS_WORKERS, llm=None, teams=None):
    """Run one week's AI pressers (idempotent), one failure at a time, and return §2.6's counters."""
    if conn_info is None:
        raise RuntimeError("run_week needs a connection DSN (DATABASE_URL or the active franchise)")
    season, week = int(season), int(week)
    started = time.time()
    meter = _meter(llm)
    team_ids = []
    if teams:
        with _connect(conn_info) as conn:
            with conn.cursor() as cur:
                for abbr in teams:
                    team_ids.append(_team_by_abbr(cur, abbr)["id"])
    with _connect(conn_info) as conn:
        with conn.cursor() as cur:
            schedule_week(cur, season, week, llm=meter)
            expire_user_pressers(cur, season, week)
        conn.commit()
        with conn.cursor() as cur:
            rows = _week_conferences(cur, season, week, team_ids or None)
    pending = [r["id"] for r in rows if r["controller"] != "USER" and r["status"] in ("SCHEDULED", "FAILED")]
    def work(conference_id):
        try:
            summary = _run_conference(conn_info, conference_id, meter)
            return conference_id, summary, None
        except Exception as exc:  # noqa: BLE001 - one broken presser doesn't stop the week
            _mark_failed(conn_info, conference_id, exc)
            return conference_id, None, exc

    if pending:
        with ThreadPoolExecutor(max_workers=max(1, min(int(workers or PRESS_WORKERS), len(pending)))) as pool:
            for _conference_id, _summary, _error in pool.map(work, pending):
                pass

    # Close effects left behind by an interrupted earlier run, then append any waiting relationship events.
    with _connect(conn_info) as conn:
        with conn.cursor() as cur:
            for conference_id in _effects_pending_ids(cur, season, week):
                try:
                    _close_effects_cur(cur, conference_id, _local_meter(meter))
                except Exception:  # noqa: BLE001 - the next run retries
                    pass
            for conference_id in _waiting_conference_ids(cur):
                try:
                    flush_relationship_events(cur, conference_id)
                except Exception:  # noqa: BLE001 - events wait for the next run
                    pass
            rows = _week_conferences(cur, season, week, team_ids or None)
        conn.commit()

    closed = sum(1 for r in rows if str(r["status"]).upper() == "CLOSED")
    failed = sum(1 for r in rows if str(r["status"]).upper() == "FAILED")
    scheduled_user = sum(1 for r in rows if str(r["controller"]).upper() == "USER"
                         and str(r["status"]).upper() == "SCHEDULED")
    calls, input_tokens, output_tokens = meter.calls, meter.input_tokens, meter.output_tokens
    with _connect(conn_info) as conn:
        with conn.cursor() as cur:
            try:
                cur.execute("/* q:week_totals */ SELECT COALESCE(SUM(calls),0), COALESCE(SUM(input_tokens),0), "
                            "COALESCE(SUM(output_tokens),0) FROM press_conferences WHERE season = %s AND week = %s",
                            (season, week))
                totals = cur.fetchone()
                if totals:
                    calls, input_tokens, output_tokens = int(totals[0]), int(totals[1]), int(totals[2])
            except Exception:  # noqa: BLE001
                pass
    return {"closed": closed, "failed": failed, "scheduled_user": scheduled_user, "calls": int(calls),
            "input_tokens": int(input_tokens), "output_tokens": int(output_tokens),
            "seconds": round(time.time() - started, 2)}


def ensure_team_presser(conn_info, team_id, season, week):
    """The team's presser status for one week, running an AI presser inline when it is missing or FAILED."""
    season, week = int(season), int(week)
    with _connect(conn_info) as conn:
        with conn.cursor() as cur:
            cur.execute("/* q:team_game */ SELECT id FROM games WHERE season = %s AND week = %s AND status = 'COMPLETED' "
                        "AND (home_team_id = %s OR away_team_id = %s) ORDER BY id LIMIT 1", (season, week, team_id, team_id))
            row = cur.fetchone()
            if not row:
                return "NO_GAME"
            game_id = row[0]
            cur.execute("/* q:team_game_conference */ SELECT id, status FROM press_conferences "
                        "WHERE game_id = %s AND team_id = %s ORDER BY id LIMIT 1", (game_id, team_id))
            found = cur.fetchone()
            if found is None:
                schedule_week(cur, season, week, llm=None)
                cur.execute("/* q:team_game_conference */ SELECT id, status FROM press_conferences "
                            "WHERE game_id = %s AND team_id = %s ORDER BY id LIMIT 1", (game_id, team_id))
                found = cur.fetchone()
            conn.commit()
            if found is None:
                return "MISSING"
            conference_id, status = found[0], str(found[1]).upper()
            if status in ("CLOSED", "DECLINED") or status not in ("SCHEDULED", "FAILED"):
                return status
            row_conf = get_conference_row(cur, conference_id)
            if row_conf and str(row_conf.get("controller")).upper() == "USER":
                return status
            try:
                meter = _meter(None)
                summary = run_session(lambda: cur, conference_id, meter)
                if summary.get("status") == "CLOSED":
                    _close_effects_cur(cur, conference_id, meter)
                conn.commit()
                return summary.get("status") or "CLOSED"
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                _mark_failed(conn_info, conference_id, exc)
                return "FAILED"


# ---------------------------------------------------------------------------------------------- user lifecycle
def start_user(cur, conference_id, opening=None):
    """SCHEDULED -> LIVE for JR's presser; returns ``{conference, hands}`` (the drafted first round)."""
    row = _conference_row_or_404(cur, conference_id)
    _require_user(row)
    _require_status(row, "SCHEDULED")
    bag = _bag(row)
    _save_status(cur, row["id"], "LIVE", started=True)
    if opening:
        issues = []
        try:
            issues = press_facts.check_line(opening, _sheet(row), _subject(row).get("name") or "") or []
        except Exception:  # noqa: BLE001
            issues = []
        _insert_turn(cur, row, bag, "SUBJECT", "OPENING", opening, source="USER", issues=issues)
        _save_patience(cur, row["id"], bag)
    if not bag.get("drafts"):
        state = _session_state(cur, row, bag=bag)
        _run_round(cur, row, bag, state, _user_llm())
        hands = current_hands(cur, row["id"])
    else:
        hands = current_hands(cur, row["id"])
    return {"conference": build_conference(cur, conference_id, include_hands=False), "hands": hands}


def pick_user(cur, conference_id, agent_id):
    """Reveal the drafted question of one raised hand; returns the ``Turn``."""
    row = _conference_row_or_404(cur, conference_id)
    _require_user(row)
    _require_status(row, "LIVE")
    bag = _bag(row)
    action = (bag.get("drafts") or {}).get(agent_id)
    if not action or str(action.get("action") or "").upper() == "PASS":
        raise PressBadRequest("That reporter does not have a hand up.")
    if bag.get("picked_turn_id") is not None:
        raise PressConflict("The last question has not been answered yet.")
    attendees = _load_attendees(cur, row["id"])
    attendee = next((a for a in attendees if a.get("agent_id") == agent_id), None)
    if attendee is None:
        raise PressBadRequest("That reporter is not in this press conference.")
    state = _session_state(cur, row, attendees=attendees, bag=bag)
    question, _kind, _tone = _ask(cur, row, bag, state, attendee, action, "USER")
    return _turn_payload(row, question)


def answer_user(cur, conference_id, turn_id, text, no_comment):
    """Record JR's answer and run the next round inside the request; returns the next hands or the end."""
    row = _conference_row_or_404(cur, conference_id)
    _require_user(row)
    _require_status(row, "LIVE")
    bag = _bag(row)
    if bag.get("picked_turn_id") is None:
        raise PressConflict("There is no picked question to answer.")
    if int(turn_id) != int(bag["picked_turn_id"]):
        if bag.get("answered_turn_id") is not None:
            raise PressConflict("That question has already been answered.")
        raise PressNotFound(f"Turn {turn_id} is not the picked question.")
    question = _turn_as_dict(cur, row, bag["picked_turn_id"])
    if question is None:
        raise PressNotFound(f"Turn {turn_id} not found")
    answer = _answer_user(cur, row, bag, question, text, bool(no_comment))
    meter = _user_llm()
    state = _session_state(cur, row, bag=bag)
    reason = press_agents.end_condition(state)
    if reason:
        _save_status(cur, row["id"], "REVIEW", reason)
        _save_counters(cur, row["id"], meter)
        return {"answer": _turn_payload(row, answer), "hands": [], "ended": True, "end_reason": reason}
    hands = _run_round(cur, row, bag, state, meter)
    if not hands:
        _save_status(cur, row["id"], "REVIEW", "RAN_OUT")
        _save_counters(cur, row["id"], meter)
        return {"answer": _turn_payload(row, answer), "hands": [], "ended": True, "end_reason": "RAN_OUT"}
    _save_counters(cur, row["id"], meter)
    return {"answer": _turn_payload(row, answer), "hands": _public_hands(hands), "ended": False, "end_reason": None}


def end_user(cur, conference_id):
    """LIVE -> REVIEW ("That's all, thanks"). Returns the Conference."""
    row = _conference_row_or_404(cur, conference_id)
    _require_user(row)
    _require_status(row, "LIVE")
    _save_status(cur, row["id"], "REVIEW", row.get("end_reason") or "SUBJECT_ENDED")
    return build_conference(cur, conference_id, include_hands=False)


def edit_user(cur, conference_id, turn_id, text=None, on_record=None):
    """Edit JR's own answer in REVIEW (words and/or on/off record); returns the Turn."""
    row = _conference_row_or_404(cur, conference_id)
    _require_user(row)
    _require_status(row, "REVIEW")
    turn = _turn_as_dict(cur, row, turn_id)
    if turn is None:
        raise PressNotFound(f"Turn {turn_id} not found")
    if turn.get("speaker") != "SUBJECT" or str(turn.get("source")) != "USER":
        raise PressConflict("Only JR's own turns can be edited.")
    new_text = turn.get("text") if text is None else str(text)
    new_record = turn.get("on_record") if on_record is None else bool(on_record)
    issues = turn.get("issues") or []
    if text is not None:
        try:
            issues = press_facts.check_line(new_text, _sheet(row), _subject(row).get("name") or "") or []
        except Exception:  # noqa: BLE001
            issues = []
    cur.execute("/* q:update_turn */ UPDATE press_turns SET text = %s, on_record = %s, issues = %s "
                "WHERE id = %s AND conference_id = %s", (new_text, new_record, _json(list(issues)), turn_id,
                                                         conference_id))
    turn.update({"text": new_text, "on_record": new_record, "issues": issues})
    return _turn_payload(row, turn)


def approve(cur, conference_id):
    """REVIEW -> CLOSED. Returns the Conference (``close_effects`` runs after, in a background thread)."""
    row = _conference_row_or_404(cur, conference_id)
    _require_user(row)
    _require_status(row, "REVIEW")
    _save_status(cur, row["id"], "CLOSED", row.get("end_reason") or "SUBJECT_ENDED", closed=True)
    return build_conference(cur, conference_id, include_hands=False)


def decline(cur, conference_id, reason="PLAY_GAME"):
    """SCHEDULED/LIVE -> DECLINED; a REVIEW presser is approved as it stands (D7). Returns the Conference."""
    row = _conference_row_or_404(cur, conference_id)
    status = str(row.get("status") or "").upper()
    if status == "REVIEW":
        _save_status(cur, row["id"], "CLOSED", row.get("end_reason") or "SUBJECT_ENDED", closed=True)
    elif status in ("SCHEDULED", "LIVE"):
        _save_status(cur, row["id"], "DECLINED", "DECLINED", closed=True)
    else:
        return build_conference(cur, conference_id, include_hands=False)
    return build_conference(cur, conference_id, include_hands=False)


def decline_open_for_launch(cur, team_id, season):
    """Play Game counts an unstarted presser as declined (SCHEDULED or LIVE); REVIEW is approved. Returns ids."""
    if not _installed(cur):
        return []
    cur.execute("/* q:launch_conferences */ SELECT id, status FROM press_conferences "
                "WHERE team_id = %s AND season = %s AND status IN ('SCHEDULED', 'LIVE', 'REVIEW') ORDER BY week, id",
                (team_id, int(season)))
    ids = []
    for conference_id, status in cur.fetchall():
        if str(status).upper() == "REVIEW":
            _save_status(cur, conference_id, "CLOSED", "SUBJECT_ENDED", closed=True)
        else:
            _save_status(cur, conference_id, "DECLINED", "DECLINED", closed=True)
        ids.append(conference_id)
    return ids


def expire_user_pressers(cur, season, week):
    """Decline user pressers left over from earlier weeks (the next ingest's fallback); REVIEW is approved."""
    if not _installed(cur):
        return []
    cur.execute("/* q:expire_conferences */ SELECT id, status FROM press_conferences "
                "WHERE season = %s AND week < %s AND controller = 'USER' "
                "AND status IN ('SCHEDULED', 'LIVE', 'REVIEW') ORDER BY week, id",
                (int(season), int(week)))
    ids = []
    for conference_id, status in cur.fetchall():
        if str(status).upper() == "REVIEW":
            _save_status(cur, conference_id, "CLOSED", "SUBJECT_ENDED", closed=True)
        else:
            _save_status(cur, conference_id, "DECLINED", "DECLINED", closed=True)
        ids.append(conference_id)
    return ids


# ------------------------------------------------------------------------------------------------ effects
def _conference_for_agents(cur, row, attendees=None):
    turns = _load_turns(cur, row["id"])
    attendees = _load_attendees(cur, row["id"]) if attendees is None else attendees
    hand_rounds = _load_hand_rounds(cur, row["id"])
    stats = {}
    for attendee in attendees:
        rounds = hand_rounds.get(attendee["agent_id"]) or []
        hand_left_up = bool(rounds and not rounds[-1].get("picked"))
        stats[attendee["agent_id"]] = {
            "agent_id": attendee["agent_id"], "display_name": attendee.get("display_name"),
            "person_key": attendee.get("person_key"), "hands": int(attendee.get("hands") or 0),
            "picked": int(attendee.get("picked") or 0), "hand_left_up": hand_left_up,
            "skipped": int(attendee.get("hands") or 0) > int(attendee.get("picked") or 0),
        }
    skip = {}
    subject = _subject(row)
    if subject.get("id") is not None:
        for attendee in attendees:
            key = attendee.get("person_key") or attendee.get("agent_id")
            try:
                skip[key] = press_agents.skip_history(cur, key, subject["id"], int(row.get("season") or 0))
            except Exception:  # noqa: BLE001
                pass
    memory_blocks = {}
    for attendee in attendees:
        try:
            block = agent_memory.build_memory_block(cur, attendee.get("agent_id"), int(row.get("season") or 0),
                                                    int(row.get("week") or 1), subject_team_id=subject.get("team_id"))
        except Exception:  # noqa: BLE001
            block = ""
        if block:
            memory_blocks[attendee.get("agent_id")] = block
    return {
        "conference_id": row["id"],
        "status": row.get("status"),
        "season": row.get("season"),
        "week": row.get("week"),
        "subject": subject,
        "team_key": subject.get("team_key"),
        "sheet": _sheet(row),
        "turns": turns,
        "transcript": turns,
        "attendees": stats,
        "hand_rounds": {k: [r["round"] for r in v] for k, v in hand_rounds.items()},
        "skip_history": skip,
        "game_label": _game_label(row),
        "memory_blocks": memory_blocks,
        "relationship_contexts": {},
        "universe_id": _universe_id(cur),
        "cur": cur,
    }


def _memory_text(row, attendee, appraisal, conference):
    subject = _subject(row)
    coach = subject.get("name") or "the head coach"
    label = _game_label(row)
    exchanged = []
    for turn in conference.get("turns") or []:
        if turn.get("speaker") != "SUBJECT" or str(turn.get("kind")).upper() not in ("ANSWER", "NO_COMMENT"):
            continue
        question = next((t for t in conference["turns"] if t.get("id") == turn.get("reply_to")), None)
        if question is None or question.get("agent_id") != attendee.get("agent_id"):
            continue
        answer = "No comment." if str(turn.get("kind")).upper() == "NO_COMMENT" else (turn.get("text") or "")
        exchanged.append(f"You asked \"{(question.get('text') or '')[:120]}\"; he said \"{answer[:160]}\".")
    stats = (conference.get("attendees") or {}).get(attendee.get("agent_id")) or {}
    if str(row.get("status") or "").upper() == "DECLINED":
        parts = [f"Coach {coach} didn't take questions after {label}."]
    else:
        parts = [f"Week {row.get('week')}, Coach {coach}'s presser after {label}."]
        parts.extend(exchanged[:2])
        parts.append(f"You had your hand up {int(stats.get('hands') or 0)} times and he called on you "
                     f"{int(stats.get('picked') or 0)} times.")
    treatment = (appraisal or {}).get("treatment")
    if isinstance(treatment, dict) and treatment.get("reason"):
        parts.append(f"You came away feeling {treatment.get('reason')[:120]}")
    memory = (appraisal or {}).get("memory")
    if memory:
        parts.append(str(memory))
    return " ".join(part.strip() for part in parts if part).strip()[:600]


def _has_press_memory(cur, agent_id, season, week, team_id):
    cur.execute("/* q:press_memory */ SELECT 1 FROM agent_memory_entries WHERE agent_id = %s AND season = %s "
                "AND week = %s AND kind = 'PRESS' AND team_id IS NOT DISTINCT FROM %s LIMIT 1",
                (agent_id, int(season), int(week), team_id))
    return cur.fetchone() is not None


def _insert_press_memory(cur, agent_id, season, week, text, team_id, player_id=None):
    cur.execute("/* q:insert_memory */ INSERT INTO agent_memory_entries "
                "(agent_id, season, week, kind, text, team_id, player_id) VALUES (%s, %s, %s, 'PRESS', %s, %s, %s)",
                (agent_id, int(season), int(week), text, team_id, player_id))


def _first_player_about(conference, agent_id):
    for turn in conference.get("turns") or []:
        if turn.get("speaker") == "REPORTER" and turn.get("agent_id") == agent_id:
            for part in turn.get("about") or []:
                if isinstance(part, dict) and part.get("type") == "player" and part.get("id") is not None:
                    return int(part["id"])
    return None


def _close_effects_cur(cur, conference_id, llm):
    """close_effects on a caller's cursor; never commits."""
    row = _conference_row_or_404(cur, conference_id)
    status = str(row.get("status") or "").upper()
    if row.get("effects_applied"):
        return {"status": "already", "judged": 0, "memories": 0, "events": 0}
    if status not in ("CLOSED", "DECLINED"):
        return {"status": "not_closed", "judged": 0, "memories": 0, "events": 0}
    conference = _conference_for_agents(cur, row)
    universe_id = conference.get("universe_id")
    before = _meter_snapshot(llm)
    judged, memories, events = 0, 0, 0
    for attendee in _load_attendees(cur, conference_id):
        try:
            appraisal = press_agents.reporter_judgment(attendee, conference, llm)
        except Exception:  # noqa: BLE001 - one broken judgment never stops the room
            appraisal = press_agents.validate_judgment("", attendee, conference)
        envelopes = []
        if universe_id:
            try:
                envelopes = press_agents.judgment_events(conference, attendee, appraisal, universe_id)
            except Exception:  # noqa: BLE001
                envelopes = []
        judgment = {"appraisal": appraisal, "events": envelopes, "appended": False}
        _save_judgment(cur, conference_id, attendee.get("agent_id"), judgment)
        judged += 1
        events += len(envelopes)
        season, week = int(row.get("season") or 0), int(row.get("week") or 1)
        if not _has_press_memory(cur, attendee.get("agent_id"), season, week, row.get("team_id")):
            text = _memory_text(row, attendee, appraisal, conference)
            _insert_press_memory(cur, attendee.get("agent_id"), season, week, text, row.get("team_id"),
                                 _first_player_about(conference, attendee.get("agent_id")))
            memories += 1
    cur.execute("/* q:save_effects */ UPDATE press_conferences SET effects_applied = TRUE WHERE id = %s",
                (conference_id,))
    _save_counters_since(cur, conference_id, llm, before)
    return {"status": "ok", "judged": judged, "memories": memories, "events": events}


def close_effects(conn_info, conference_id, llm=None):
    """Run the §2.8 judgments, PRESS memories and relationship-event envelopes once, on CLOSED or DECLINED."""
    meter = _meter(llm)
    with _connect(conn_info) as conn:
        with conn.cursor() as cur:
            result = _close_effects_cur(cur, conference_id, meter)
        conn.commit()
    return result


# ------------------------------------------------------------------------------------------ relationships
def _relationship_store():
    """personality.relationship_store when it exists, else None (its absence is not an error)."""
    try:
        from personality import relationship_store
        return relationship_store
    except Exception:  # noqa: BLE001 - the relationships plan may not be built
        return None


def _effects_pending_ids(cur, season, week):
    if not _installed(cur):
        return []
    cur.execute("/* q:effects_pending */ SELECT id FROM press_conferences WHERE season = %s AND week = %s "
                "AND status = 'CLOSED' AND effects_applied = FALSE ORDER BY id", (int(season), int(week)))
    return [r[0] for r in cur.fetchall()]


def _waiting_conference_ids(cur):
    if not _installed(cur):
        return []
    cur.execute("/* q:waiting_events */ SELECT DISTINCT conference_id FROM press_attendees "
                "WHERE judgment IS NOT NULL AND COALESCE((judgment->>'appended')::boolean, FALSE) = FALSE "
                "ORDER BY conference_id")
    return [r[0] for r in cur.fetchall()]


def flush_relationship_events(cur, conference_id):
    """Append the conference's waiting envelopes through the relationships store.

    Returns the number of events appended. 0 (events left waiting) when the store module or its tables are
    absent, the universe is unregistered, or the batch is refused.
    """
    if not _installed(cur):
        return 0
    store = _relationship_store()
    if store is None:
        return 0
    events_table = getattr(store, "EVENTS_TABLE", "persona_relationship_events")
    if not _regclass_table(cur, events_table):
        return 0
    rows = []
    cur.execute("/* q:waiting_judgments */ SELECT agent_id, judgment FROM press_attendees "
                "WHERE conference_id = %s AND judgment IS NOT NULL", (conference_id,))
    for agent_id, judgment in cur.fetchall():
        parsed = _loads(judgment, judgment)
        if not isinstance(parsed, dict) or parsed.get("appended"):
            continue
        rows.append((agent_id, parsed))
    pending = [event for _, judgment in rows for event in (judgment.get("events") or [])]
    if not pending:
        return 0
    universe_id = _universe_id(cur)
    if not universe_id:
        return 0
    try:
        head = store.read_head(cur, universe_id)
        receipt = store.append_events(cur, pending, expected_head=head, team_registry=_team_registry(cur))
    except Exception:  # noqa: BLE001 - a stale head or refused batch waits for the next run
        return 0
    stored = {item.get("source_event_id") for item in (receipt or {}).get("appended", [])}
    stored |= {item.get("source_event_id") for item in (receipt or {}).get("duplicates", [])}
    rejected = {item.get("source_event_id") for item in (receipt or {}).get("rejected", [])}
    flushed = 0
    for agent_id, judgment in rows:
        source_ids = {event.get("source_event_id") for event in (judgment.get("events") or [])}
        if source_ids and source_ids <= stored and not (source_ids & rejected):
            judgment["appended"] = True
            _save_judgment(cur, conference_id, agent_id, judgment)
            flushed += len(source_ids)
    return flushed


# ----------------------------------------------------------------------------------------------- readers
def subject_controller(cur, team_id, subject_type="coach", subject_id=None):
    """USER when the team is one of the active franchise's ``user_teams``; AGENT otherwise. An empty list means
    JR controls no team, never that he controls all of them. This is the one place to extend later."""
    abbr = _team_abbr(cur, team_id)
    if not abbr:
        return "AGENT"
    teams = _user_teams()
    return "USER" if teams and str(abbr).upper() in teams else "AGENT"


APPROVED_QUOTE_KEYS = ("conference_id", "game_id", "team_id", "subject_type", "subject_id", "subject_name",
                       "controller", "week", "agent_id", "question", "answer", "flags")


def approved_quotes(cur, season, week, team_id=None):
    """ANSWER turns of CLOSED conferences only, on the record, joined to their question (the V3 interface)."""
    if not _installed(cur):
        return []
    sql = ("/* q:approved_quotes */ SELECT pc.id, pc.game_id, pc.team_id, pc.subject_type, pc.subject_id, "
           "pc.subject_name, pc.controller, pc.week, q.agent_id, q.text, a.text, a.issues "
           "FROM press_turns a JOIN press_conferences pc ON pc.id = a.conference_id AND pc.status = 'CLOSED' "
           "JOIN press_turns q ON q.id = a.reply_to "
           "WHERE pc.season = %s AND pc.week = %s AND a.speaker = 'SUBJECT' "
           "AND a.kind = 'ANSWER' AND a.on_record AND q.speaker = 'REPORTER'")
    params = [int(season), int(week)]
    if team_id is not None:
        sql += " AND pc.team_id = %s"
        params.append(team_id)
    sql += " ORDER BY pc.id, a.seq"
    cur.execute(sql, tuple(params))
    out = []
    for row in cur.fetchall():
        item = dict(zip(APPROVED_QUOTE_KEYS, row))
        item["flags"] = _loads(item.get("flags"), []) or []
        out.append(item)
    return out


def _latest_conference_id(cur, team_id, season, week, statuses):
    sql = ("/* q:team_conference_latest */ SELECT id FROM press_conferences WHERE team_id = %s AND season = %s "
           "AND week <= %s AND status IN (" + ", ".join(["%s"] * len(statuses)) + ") "
           "ORDER BY week DESC, id DESC LIMIT 1")
    cur.execute(sql, (team_id, int(season), int(week)) + tuple(statuses))
    row = cur.fetchone()
    return row[0] if row else None


def brief_block(cur, team_id, season, week):
    """The beat brief's ``press_conference`` block (§2.9), or None when the team has no CLOSED presser."""
    if not _installed(cur):
        return None
    conference_id = _latest_conference_id(cur, team_id, season, week, ("CLOSED",))
    if conference_id is None:
        return None
    row = get_conference_row(cur, conference_id)
    if row is None:
        return None
    exchanges, no_comment, corrections = [], [], []
    for turn in _load_turns(cur, conference_id):
        if turn.get("speaker") != "SUBJECT" or str(turn.get("kind")).upper() not in ("ANSWER", "NO_COMMENT"):
            continue
        question = next((t for t in _load_turns(cur, conference_id) if t.get("id") == turn.get("reply_to")), None)
        if question is None:
            continue
        if str(turn.get("kind")).upper() == "NO_COMMENT":
            no_comment.append(question.get("text") or "")
            continue
        if turn.get("on_record"):
            exchanges.append({"reporter": question.get("handle") or question.get("agent_id"),
                              "question": question.get("text") or "", "answer": turn.get("text") or ""})
        for issue in turn.get("issues") or []:
            if issue.get("recorded") is not None:
                corrections.append(f"He said {issue.get('claim')}; the box score has {issue.get('recorded')}.")
    return {"subject": row.get("subject_name"), "game": _game_label(row), "ended": row.get("end_reason"),
            "exchanges": exchanges, "no_comment": no_comment, "corrections": corrections,
            "reporting_guidance": BRIEF_GUIDANCE}


def declined_note(cur, team_id, season, week):
    """The beat brief's note when the team's latest presser was DECLINED (§2.9), else None."""
    if not _installed(cur):
        return None
    conference_id = _latest_conference_id(cur, team_id, season, week, ("DECLINED",))
    if conference_id is None:
        return None
    row = get_conference_row(cur, conference_id)
    if row is None:
        return None
    return f"Coach {row.get('subject_name')} did not take questions after {_game_label(row)}; do not quote him on it."


def history_ids(cur, season, week):
    """Closed presser ids for a week (any team), in a stable order."""
    if not _installed(cur):
        return []
    cur.execute("/* q:history */ SELECT id FROM press_conferences WHERE status = 'CLOSED' AND season = %s "
                "AND week = %s ORDER BY game_id, team_id", (int(season), int(week)))
    return [r[0] for r in cur.fetchall()]


# --------------------------------------------------------------------------------------------- tool reader
def _tool_connect():
    from db.db_handler import DatabaseHandler
    import psycopg
    return psycopg.connect(DatabaseHandler().db_conn_info)


def _resolve_tool_season(cur, season):
    if season:
        return int(season)
    try:
        from newsroom.run_context import context_season
        ctx = context_season()
        if ctx:
            return int(ctx)
    except Exception:  # noqa: BLE001
        pass
    cur.execute("/* q:league */ SELECT current_season, current_week FROM league_state WHERE id = 1")
    row = cur.fetchone()
    return int(row[0]) if row and row[0] else 0


def _resolve_cutoff_week(season, week):
    if week:
        return int(week)
    try:
        from newsroom.run_context import cutoff_week
        cutoff = cutoff_week(season)
        if cutoff:
            return int(cutoff)
    except Exception:  # noqa: BLE001
        pass
    return None


def get_press_conference(team_identifier, season=0, week=0):
    """The beat writers' tool: the team's press conference transcripts up to the publication week (§2.9).

    Prints the subject, game, end reason and each on-record question and answer, plus the declined note when the
    coach never showed. Read-only, one connection of its own (like the other ``newsroom/db_tools.py`` tools).
    """
    with _tool_connect() as conn:
        with conn.cursor() as cur:
            if not _installed(cur):
                return "No press conferences are stored yet."
            cur.execute("/* q:tool_team */ SELECT id, abbreviation, name, city FROM teams "
                        "WHERE abbreviation ILIKE %s OR name ILIKE %s OR city ILIKE %s ORDER BY id LIMIT 1",
                        (team_identifier, team_identifier, team_identifier))
            row = cur.fetchone()
            if not row:
                return f"No team matches '{team_identifier}'."
            team_id = row[0]
            season = _resolve_tool_season(cur, season)
            cutoff = _resolve_cutoff_week(season, week)
            sql = ("/* q:tool_conferences */ SELECT id, status FROM press_conferences WHERE team_id = %s "
                   "AND season = %s AND status IN ('CLOSED', 'DECLINED')")
            params = [team_id, season]
            if cutoff:
                sql += " AND week <= %s"
                params.append(int(cutoff))
            sql += " ORDER BY week DESC, id DESC LIMIT 6"
            cur.execute(sql, tuple(params))
            ids = cur.fetchall()
            if not ids:
                return f"No press conferences for the {row[2]} in {season}."
            blocks = []
            for conference_id, status in ids:
                conf = build_conference(cur, conference_id, include_hands=False)
                if conf is None:
                    continue
                lines = [f"{conf.get('team')} {conf.get('subject_name')}: Week {conf.get('week')} vs "
                         f"{conf.get('opponent')}, {conf.get('result') or '-'} {conf.get('score') or ''} "
                         f"({status}{', ' + str(conf.get('end_reason')) if conf.get('end_reason') else ''})".strip()]
                if str(status).upper() == "DECLINED":
                    note = declined_note(cur, team_id, season, conf.get("week"))
                    if note:
                        lines.append(note)
                for turn in conf.get("turns") or []:
                    if turn.get("speaker") == "REPORTER":
                        lines.append(f"Q {(turn.get('reporter') or {}).get('handle') or ''}: {turn.get('text')}")
                    elif str(turn.get("kind")).upper() in ("ANSWER", "NO_COMMENT", "OPENING", "CLOSING"):
                        label = "A" if str(turn.get("kind")).upper() != "OPENING" else "OPENING"
                        lines.append(f"{label}: {turn.get('text')}")
                blocks.append("\n".join(lines))
            return "\n\n".join(blocks)


# ---------------------------------------------------------------------------------------------------- CLI
def _print_json(value):
    print(json.dumps(value, indent=2, sort_keys=True, default=str))


def _dry_run(season, week, teams=None):
    """Print the casts and the first round's prompts. NO model calls, NO writes."""
    conn_info = _conn_info()
    if conn_info is None:
        raise SystemExit("No database: set DATABASE_URL or an active franchise (python3 -m db.franchises active).")
    team_ids = []
    with _connect(conn_info) as conn:
        with conn.cursor() as cur:
            casts = press_cast.cast_week(cur, int(season), int(week))
            if teams:
                team_ids = [_team_by_abbr(cur, abbr)["id"] for abbr in teams]
            for game_id, cast in sorted(casts.items()):
                if team_ids and not (set((cast.get("beats") or {}).keys()) & set(team_ids)):
                    continue
                facts = _facts_for_game(cur, game_id, [cast])
                print(f"\n=== Game {game_id}: {cast.get('tier')} ({cast.get('interest')}) ===")
                print(json.dumps({"game_id": game_id, "tier": cast.get("tier"), "interest": cast.get("interest"),
                                  "nationals": [a.get("display_name") for a in cast.get("nationals") or []],
                                  "beats": {str(t): [a.get("display_name") for a in group]
                                            for t, group in (cast.get("beats") or {}).items()}},
                                 indent=2, sort_keys=True, default=str))
                for team_id in (cast.get("beats") or {}):
                    if team_ids and team_id not in team_ids:
                        continue
                    team_facts = next((t for t in (facts.get("teams") or []) if t.get("team_id") == team_id), {})
                    subject = _subject_for_conference(cur, team_id, team_facts, int(season))
                    row = {"id": "dry-run", "season": int(season), "week": int(week), "team_id": team_id,
                           "subject_type": subject["subject_type"], "subject_id": subject["subject_id"],
                           "subject_name": subject["subject_name"], "controller": subject["controller"],
                           "facts": facts, "patience": None, "abbreviation": team_facts.get("abbr"),
                           "team_name": team_facts.get("name")}
                    state = {"conference_id": "dry-run", "season": int(season), "week": int(week),
                             "subject": _subject(row), "sheet": facts, "transcript": [], "turns": [],
                             "hands": [], "picks": {}, "hand_counts": {}, "last_answer": None, "last_asker": None,
                             "questions_asked": 0, "follow_ups_in_a_row": 0, "passes_in_a_row": {}, "done": [],
                             "mood": 3, "patience": None, "wants_to_end": False,
                             "controller": subject["controller"], "game_label": _game_label(row),
                             "memory_blocks": {}, "relationship_contexts": {}, "skip_history": {},
                             "attendees": _attendees_from_cast(cast, team_id)}
                    print(f"\n--- {subject['subject_name']} ({team_facts.get('abbr')}, {subject['controller']}) ---")
                    for attendee in _attendees_from_cast(cast, team_id):
                        prompt = press_agents._reporter_prompt(attendee, state)
                        print(f"\n[reporter {attendee.get('display_name')} / {attendee.get('role')}]\n{prompt}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Press conference session engine and week runner (newsroom.press_room)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run-week", help="cast and run one week's pressers")
    run.add_argument("--season", type=int, required=True)
    run.add_argument("--week", type=int, required=True)
    run.add_argument("--workers", type=int, default=PRESS_WORKERS)
    run.add_argument("--team", action="append", default=None, help="only this team abbreviation (repeatable)")
    run.add_argument("--model", default=None)
    run.add_argument("--dry-run", action="store_true", help="print casts and first-round prompts; no model calls")

    show = sub.add_parser("show", help="show one conference as JSON")
    show.add_argument("--conference", type=int, required=True)

    wake = sub.add_parser("wakeup", help="ensure a team's presser then start its beat wakeup")
    wake.add_argument("--team", required=True)
    wake.add_argument("--season", type=int, required=True)
    wake.add_argument("--week", type=int, required=True)

    cast = sub.add_parser("cast", help="casts only")
    cast.add_argument("--season", type=int, required=True)
    cast.add_argument("--week", type=int, required=True)

    args = parser.parse_args(argv)

    if args.cmd == "run-week":
        if args.dry_run:
            _dry_run(args.season, args.week, args.team)
            return 0
        conn_info = _conn_info()
        if conn_info is None:
            raise SystemExit("No database: set DATABASE_URL or an active franchise.")
        llm = MeteredLLM(None, model=args.model)
        _print_json(run_week(conn_info, args.season, args.week, workers=args.workers, llm=llm, teams=args.team))
        return 0
    if args.cmd == "show":
        conn_info = _conn_info()
        if conn_info is None:
            raise SystemExit("No database: set DATABASE_URL or an active franchise.")
        with _connect(conn_info) as conn:
            with conn.cursor() as cur:
                _print_json(build_conference(cur, args.conference))
        return 0
    if args.cmd == "cast":
        conn_info = _conn_info()
        if conn_info is None:
            raise SystemExit("No database: set DATABASE_URL or an active franchise.")
        with _connect(conn_info) as conn:
            with conn.cursor() as cur:
                _print_json(press_cast.cast_week(cur, args.season, args.week))
        return 0
    if args.cmd == "wakeup":
        conn_info = _conn_info()
        if conn_info is None:
            raise SystemExit("No database: set DATABASE_URL or an active franchise.")
        with _connect(conn_info) as conn:
            with conn.cursor() as cur:
                team = _team_by_abbr(cur, args.team)
        status = ensure_team_presser(conn_info, team["id"], args.season, args.week)
        print(f"{args.team}: presser {status}")
        try:  # U7 wires the real wakeup; guarded so U6 runs alone
            from server.api import maybe_start_team_wakeup
            with _connect(conn_info) as conn:
                with conn.cursor() as cur:
                    maybe_start_team_wakeup(cur, team["id"], args.season)
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            print(f"beat wakeup not started: {exc}", file=sys.stderr)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
