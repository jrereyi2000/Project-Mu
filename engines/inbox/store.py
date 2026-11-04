"""
Inbox threads and messages (FRONT_OFFICE_INBOX_PLAN.md §2.6, §2.8).

One inbox per user team. A thread is unique on (team_id, kind, source_ref), so a trigger that fires again creates
nothing. Messages are numbered per thread (`seq` 1, 2, ...). A counterpart or system line marks the thread
WAITING_JR and unread; JR's line marks it OPEN.

  create_thread(cur, thread) -> id | None      None: the source already has a thread, or the kind's cap is reached
  add_message(cur, thread_id, author, body, body_source, ...) -> {"id", "seq"}
  get_thread / list_threads / messages / mark_read / set_status / update_state / close_thread
  writable(cur, thread) -> bool                CLOSED / EXPIRED, or the actor was released -> read-only
  expire_stale(cur, season, week, team_ids=None) -> int
  jr_statements(cur, team_id, season, week=None) -> [statement]   REPORTER answers, NO_COMMENT excluded

Caps: MAX_OPEN_PER_KIND per team (open = OPEN or WAITING_JR). Expiry: the thread's expires_season/week when set,
else INBOX_STALE_WEEKS ticks after its last activity. Activity is the sim time passed to add_message (`at`),
kept in the thread's state under the reserved key "_touched" ([season, tick]); before any, the thread's own
season/week. Ticks follow the relationship producers' calendar (weeks 1-23, offseason stops 24-32), so expiry
runs through the offseason. Nothing here commits.
"""
from psycopg.types.json import Jsonb

from engines.inbox import Conflict

MAX_OPEN_PER_KIND = {"GM_TALK": 3, "CONTRACT": 3, "ROLE_TALK": 3, "REPORTER": 2, "VISION": 1}
DEFAULT_MAX_OPEN = 3
INBOX_STALE_WEEKS = 2
TICKS_PER_SEASON = 32
OPEN_STATUSES = ("OPEN", "WAITING_JR")
STATEMENT_OPTIONS = {"ON_RECORD": True, "OFF_RECORD": False}      # NO_COMMENT isn't a statement
TOUCHED = "_touched"

THREAD_COLS = ("id", "team_id", "kind", "actor_role", "actor_id", "counterpart_type", "counterpart_id",
               "counterpart_name", "subject", "status", "started_by", "source_ref", "link_table", "link_id", "state",
               "season", "week", "offseason_stage", "expires_season", "expires_week", "unread", "created_at",
               "updated_at")
MESSAGE_COLS = ("id", "thread_id", "seq", "author", "body", "body_source", "option_id", "options", "facts",
                "engine_ref", "appraisal", "created_at")
_INSERT_COLS = ("team_id", "kind", "actor_role", "actor_id", "counterpart_type", "counterpart_id",
                "counterpart_name", "subject", "status", "started_by", "source_ref", "link_table", "link_id", "state",
                "season", "week", "offseason_stage", "expires_season", "expires_week")
_JSON_THREAD = ("state",)


def installed(cur):
    cur.execute("SELECT to_regclass('inbox_threads') IS NOT NULL AND to_regclass('inbox_messages') IS NOT NULL")
    row = cur.fetchone()
    return bool(row and row[0])


def max_open(kind):
    return MAX_OPEN_PER_KIND.get(kind, DEFAULT_MAX_OPEN)


def _thread(row):
    t = dict(zip(THREAD_COLS, row))
    t["state"] = dict(t.get("state") or {})
    return t


def _message(row):
    m = dict(zip(MESSAGE_COLS, row))
    m["options"] = list(m.get("options") or [])
    return m


# ------------------------------------------------------------------------------------------------- threads ---

def find_thread(cur, team_id, kind, source_ref):
    cur.execute("SELECT id FROM inbox_threads WHERE team_id = %s AND kind = %s AND source_ref = %s",
                (team_id, kind, source_ref))
    row = cur.fetchone()
    return row[0] if row else None


def count_open(cur, team_id, kind):
    cur.execute("SELECT count(*) FROM inbox_threads WHERE team_id = %s AND kind = %s "
                "AND status IN ('OPEN','WAITING_JR')", (team_id, kind))
    row = cur.fetchone()
    return int(row[0] if row else 0)


def at_cap(cur, team_id, kind):
    return count_open(cur, team_id, kind) >= max_open(kind)


def create_thread(cur, thread, *, enforce_cap=True):
    """Insert a thread (a dict with _INSERT_COLS; status defaults to OPEN, state to {}). Returns its id, or None
    when (team_id, kind, source_ref) already has a thread or the kind's open cap is reached."""
    t = dict(thread)
    t.setdefault("status", "OPEN")
    t["state"] = t.get("state") or {}
    for col in _INSERT_COLS:
        t.setdefault(col, None)
    if find_thread(cur, t["team_id"], t["kind"], t["source_ref"]) is not None:
        return None
    if enforce_cap and at_cap(cur, t["team_id"], t["kind"]):
        return None
    vals = [Jsonb(t[c]) if c in _JSON_THREAD else t[c] for c in _INSERT_COLS]
    cur.execute(f"INSERT INTO inbox_threads ({', '.join(_INSERT_COLS)}) VALUES ({', '.join(['%s'] * len(vals))}) "
                "ON CONFLICT (team_id, kind, source_ref) DO NOTHING RETURNING id", vals)
    row = cur.fetchone()
    return row[0] if row else None


def get_thread(cur, thread_id):
    cur.execute(f"SELECT {', '.join(THREAD_COLS)} FROM inbox_threads WHERE id = %s", (thread_id,))
    row = cur.fetchone()
    return _thread(row) if row else None


def list_threads(cur, team_id, status=None, kind=None, actor_role=None, limit=50):
    """Newest activity first. status: one status or a list of them; kind; actor_role ('GM', 'COACH', ...).
    An inbox holds a few dozen threads at most (caps), so filtering happens here rather than in SQL."""
    cur.execute(f"SELECT {', '.join(THREAD_COLS)} FROM inbox_threads WHERE team_id = %s "
                "ORDER BY updated_at DESC, id DESC", (team_id,))
    rows = [_thread(r) for r in cur.fetchall()]
    if status:
        wanted = {status} if isinstance(status, str) else set(status)
        rows = [r for r in rows if r["status"] in wanted]
    if kind:
        rows = [r for r in rows if r["kind"] == kind]
    if actor_role:
        rows = [r for r in rows if r["actor_role"] == str(actor_role).upper()]
    return rows[:limit] if limit else rows


def unread_count(cur, team_id):
    return sum(1 for t in list_threads(cur, team_id, limit=None) if t["unread"])


def _update(cur, thread_id, touch=True, **fields):
    sets = [f"{k} = %s" for k in fields]
    vals = [Jsonb(v) if k in _JSON_THREAD else v for k, v in fields.items()]
    if touch:
        sets.append("updated_at = now()")
    cur.execute(f"UPDATE inbox_threads SET {', '.join(sets)} WHERE id = %s", vals + [thread_id])


def mark_read(cur, thread_id):
    _update(cur, thread_id, touch=False, unread=False)


def set_status(cur, thread_id, status):
    _update(cur, thread_id, status=status)


def close_thread(cur, thread_id):
    set_status(cur, thread_id, "CLOSED")


def update_state(cur, thread_id, state):
    _update(cur, thread_id, state=dict(state or {}))


def set_link(cur, thread_id, link_table, link_id):
    _update(cur, thread_id, link_table=link_table, link_id=link_id)


# ------------------------------------------------------------------------------------------------ messages ---

def messages(cur, thread_id):
    cur.execute(f"SELECT {', '.join(MESSAGE_COLS)} FROM inbox_messages WHERE thread_id = %s ORDER BY seq",
                (thread_id,))
    return [_message(r) for r in cur.fetchall()]


def add_message(cur, thread_id, author, body, body_source, *, option_id=None, options=(), facts=None,
                engine_ref=None, appraisal=None, at=None):
    """Append a message with the next seq. author JR -> the thread is OPEN and read; COUNTERPART/SYSTEM ->
    WAITING_JR and unread (a CLOSED or EXPIRED thread keeps its status). at: {season, week, offseason_stage} or
    (season, week) -> the thread's last-activity time for expiry. Returns {"id", "seq"}."""
    cur.execute("SELECT id FROM inbox_threads WHERE id = %s FOR UPDATE", (thread_id,))
    if not cur.fetchone():
        raise ValueError(f"no inbox thread {thread_id}")
    cur.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM inbox_messages WHERE thread_id = %s", (thread_id,))
    seq = int(cur.fetchone()[0])
    cur.execute("INSERT INTO inbox_messages (thread_id, seq, author, body, body_source, option_id, options, facts, "
                "engine_ref, appraisal) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
                (thread_id, seq, author, body, body_source, option_id, Jsonb(list(options or [])),
                 None if facts is None else Jsonb(facts), None if engine_ref is None else Jsonb(engine_ref),
                 None if appraisal is None else Jsonb(appraisal)))
    mid = cur.fetchone()[0]
    t = get_thread(cur, thread_id)
    fields = {}
    if t["status"] in OPEN_STATUSES:
        fields["status"] = "OPEN" if author == "JR" else "WAITING_JR"
    fields["unread"] = author != "JR"
    tp = _time_point(at)
    if tp is not None:
        fields["state"] = dict(t["state"], **{TOUCHED: list(tp)})
    _update(cur, thread_id, **fields)
    return {"id": mid, "seq": seq}


# -------------------------------------------------------------------------------------------- read-only rule ---

def writable(cur, thread):
    """False for a CLOSED or EXPIRED thread, or one whose controlled actor was released (it stays readable)."""
    if thread is None or thread["status"] not in OPEN_STATUSES:
        return False
    if thread["actor_role"] == "TEAM":
        return True
    from engines.inbox import actors
    return actors.is_active(cur, thread["team_id"], thread["actor_role"], thread["actor_id"])


def require_writable(cur, thread):
    if not writable(cur, thread):
        raise Conflict("this conversation is read-only")
    return thread


# ------------------------------------------------------------------------------------------------- expiry ---

def tick_of(week, offseason_stage=None):
    """The calendar tick (1-32) of a sim time: the league week (in season 1-23, offseason stops 24-32), else the
    offseason stage's STAGE_WEEKS; None when unknown."""
    if week is not None and 1 <= int(week) <= TICKS_PER_SEASON:
        return int(week)
    if offseason_stage:
        try:
            from pipeline.league_office import STAGE_WEEKS
            return STAGE_WEEKS.get(str(offseason_stage).upper())
        except Exception:
            return None
    return None


def _time_point(at):
    if at is None:
        return None
    if isinstance(at, dict):
        season, tick = at.get("season"), tick_of(at.get("week"), at.get("offseason_stage"))
    else:
        season, tick = at[0], tick_of(at[1])
    return None if season is None or tick is None else (int(season), tick)


def _index(season, tick):
    return int(season) * TICKS_PER_SEASON + int(tick)


def is_stale(thread, season, week, offseason_stage=None):
    """True when an open thread is past its expiry: (season, week) beyond expires_season/expires_week when set,
    else more than INBOX_STALE_WEEKS ticks after its last activity."""
    if thread["status"] not in OPEN_STATUSES:
        return False
    now = tick_of(week, offseason_stage)
    if now is None or season is None:
        return False
    if thread.get("expires_season") is not None:
        exp_tick = thread.get("expires_week") or TICKS_PER_SEASON
        return _index(season, now) > _index(thread["expires_season"], exp_tick)
    touched = (thread.get("state") or {}).get(TOUCHED)
    if touched:
        last = _index(touched[0], touched[1])
    else:
        t = tick_of(thread.get("week"), thread.get("offseason_stage"))
        if t is None or thread.get("season") is None:
            return False
        last = _index(thread["season"], t)
    return _index(season, now) - last > INBOX_STALE_WEEKS


def expire_stale(cur, season, week, team_ids=None, offseason_stage=None):
    """Mark stale open threads EXPIRED; returns how many."""
    cur.execute(f"SELECT {', '.join(THREAD_COLS)} FROM inbox_threads WHERE status IN ('OPEN','WAITING_JR') "
                "ORDER BY id")
    n = 0
    for t in (_thread(r) for r in cur.fetchall()):
        if team_ids is not None and t["team_id"] not in team_ids:
            continue
        if is_stale(t, season, week, offseason_stage):
            _update(cur, t["id"], status="EXPIRED")
            n += 1
    return n


# ---------------------------------------------------------------------------------------------- statements ---

def pair_statements(thread, msgs):
    """[(question, answer message, on_record)] for one REPORTER thread: each JR ON_RECORD / OFF_RECORD answer with
    the reporter's line before it. NO_COMMENT and END_INTERVIEW aren't statements."""
    out, question = [], None
    for m in sorted(msgs, key=lambda m: m["seq"]):
        if m["author"] == "COUNTERPART":
            question = m["body"]
        elif m["author"] == "JR" and m.get("option_id") in STATEMENT_OPTIONS and (m.get("body") or "").strip():
            out.append((question, m, STATEMENT_OPTIONS[m["option_id"]]))
    return out


def jr_statements(cur, team_id, season, week=None):
    """One row per answered interview question (for V2.2's writers): {thread_id, team_id, season, week,
    writer_agent_id, writer_handle, subject, question, answer, on_record, actor_role, actor_name, created_at}."""
    from engines.inbox import actors
    cur.execute(f"SELECT {', '.join(THREAD_COLS)} FROM inbox_threads WHERE team_id = %s AND kind = 'REPORTER' "
                "AND season = %s ORDER BY id", (team_id, season))
    threads = [_thread(r) for r in cur.fetchall()]
    out = []
    for t in threads:
        if week is not None and t["week"] != week:
            continue
        pairs = pair_statements(t, messages(cur, t["id"]))
        if not pairs:
            continue
        cur.execute("SELECT handle FROM agent_memories WHERE agent_id = %s", (t["counterpart_id"],))
        row = cur.fetchone()
        handle = row[0] if row else None
        actor_name = (actors.persona_name(cur, t["actor_role"], t["actor_id"])
                      if t["actor_role"] != "TEAM" else t["actor_id"])
        for question, m, on_record in pairs:
            out.append({"thread_id": t["id"], "team_id": t["team_id"], "season": t["season"], "week": t["week"],
                        "writer_agent_id": t["counterpart_id"], "writer_handle": handle, "subject": t["subject"],
                        "question": question, "answer": m["body"], "on_record": on_record,
                        "actor_role": t["actor_role"], "actor_name": actor_name, "created_at": m["created_at"]})
    return out
