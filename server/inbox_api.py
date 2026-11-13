"""Front-office inbox API for the #franchise page (`/api/franchise/{abbr}/inbox`, FRONT_OFFICE_INBOX_PLAN.md §2.12,
U5). The JSON contract is Mu_Network/src/types/franchiseInbox.ts: the frontend (U2) is built against these exact
shapes.

  GET    /{abbr}/inbox/actors                -> {controlled: [Actor], candidates: [{role, persona_id, name, also}]}
  POST   /{abbr}/inbox/actors                {role, persona_id} -> Actor (201; 403 outside user_teams, 409 not on team)
  DELETE /{abbr}/inbox/actors/{role}/{id}    -> 204
  GET    /{abbr}/inbox?status=&kind=&as=&limit=50 -> {team, actors: [Actor], unread, threads: [Thread]}
  GET    /{abbr}/inbox/kinds                 -> [{kind, actor_roles, can_start, start_form}]
  GET    /{abbr}/inbox/targets?kind=         -> {kind, targets: [Target]} (wave 4: who a JR-started talk opens with)
  GET    /{abbr}/inbox/{thread_id}           -> {thread, messages, options} (runs the template scan, marks read)
  POST   /{abbr}/inbox/{thread_id}/draft     {text} -> {draft: {option_id, payload, summary} | null} (acts on nothing)
  POST   /{abbr}/inbox/{thread_id}/reply     {option_id, payload, note} -> {thread, messages, options}
  POST   /{abbr}/inbox                       {kind, as, ...kind fields, note} -> Thread (201)
  POST   /{abbr}/inbox/{thread_id}/close     -> Thread

Actor = {role, id, name}; a Target is {id, name, position, overall} for CONTRACT (the team's active players that
franchise_api._eligibility accepts, so the picker never offers a player the kind would refuse) and ROLE_TALK (the
team's active-roster players), `id` = players.id as text, or {id, name, team} for GM_TALK (another team's open GM
tenure, `id` = gm_profiles.id as text, `team` = the abbreviation sent as gm_team); a targetless or unknown kind
answers an empty list. A Thread adds `actor` and `counterpart` and leaves out the kind-owned `state` (and the
internal `team_id`).
A Message never carries `facts` or a surplus, and its appraisal is only {outcome, reason}: the inbox never shows a
trust number. Notes are at most 600 characters. Errors are FastAPI's {"detail": string} with
400 (bad body / kind), 403 (team outside the active franchise's user_teams — and every team when the registry lists
none, "no user team is registered for this franchise" — or `as` not a controlled role),
404 (unknown team or thread) and 409 (refused persona, read-only thread, cap, missing migration).

The endpoints are thin: every rule (who JR controls, caps, expiry, read-only, event outbox) lives in
engines/inbox/, which never commits; this module owns each request's transaction. The routes are ordered so
`actors` and `kinds` are declared before `/{thread_id}` (a typed path parameter still matches the literal segment,
then fails validation, so declaration order is what keeps `/inbox/kinds` from being read as a thread id).
"""
import os
from contextlib import contextmanager

import psycopg
from fastapi import APIRouter, Body, HTTPException, Query, Response

from engines.inbox import InboxError, actors, registry, scan, store, voice

CONN_INFO = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")
NOTE_MAX_CHARS = voice.NOTE_MAX_CHARS          # 600
DEFAULT_LIMIT = 50
MAX_LIMIT = 200
ABBR_ALIASES = {"ARI": "AZ", "CRD": "AZ", "WSH": "WAS", "WTC": "WAS"}      # as server/franchise_api.py

router = APIRouter(prefix="/api/franchise")


# ------------------------------------------------------------------ plumbing ----
def _connect():
    return psycopg.connect(CONN_INFO)


@contextmanager
def _cursor():
    """One request's connection and cursor; commit on success, roll back on any error. The engines never commit."""
    conn = _connect()
    try:
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    finally:
        conn.close()


@contextmanager
def _errors():
    """An inbox refusal (InboxError, or a subclass) becomes its HTTP status with a {"detail"} body."""
    try:
        yield
    except InboxError as e:
        raise HTTPException(e.status, e.message) from None


def _team(cur, abbr):
    """(team_id, ABBR) for a registry user team: 404 for an unknown team, 403 outside user_teams (and 403 for every
    team when the registry lists none — "no user team is registered for this franchise"). The page's display
    abbreviations (ARI, WSH) resolve to the save's (AZ, WAS), as in server/franchise_api.py."""
    clean = str(abbr or "").upper().strip()
    team = actors.team_row(cur, ABBR_ALIASES.get(clean, clean))
    if team is None:
        raise HTTPException(404, f"Team '{abbr}' not found")
    refusal = actors.user_team_refusal(team[1])
    if refusal:
        raise HTTPException(403, refusal)
    return team


def _require_installed(cur):
    if not store.installed(cur):
        raise HTTPException(409, "the front-office inbox migration (2027_01_19_front_office_inbox.sql) isn't applied")


def _thread_or_404(cur, team_id, thread_id):
    thread = store.get_thread(cur, thread_id)
    if thread is None or thread["team_id"] != team_id:
        raise HTTPException(404, f"no conversation {thread_id}")
    return thread


# --------------------------------------------------------------------- shapes ----
THREAD_FIELDS = ("id", "kind", "subject", "status", "unread", "season", "week", "actor_role", "actor_id",
                 "counterpart_type", "counterpart_id", "counterpart_name", "started_by", "source_ref", "link_table",
                 "link_id", "offseason_stage", "expires_season", "expires_week", "created_at", "updated_at")


def _actor_name(cur, thread):
    role, pid = thread.get("actor_role"), thread.get("actor_id")
    if role in (None, "TEAM"):
        return str(pid or "TEAM")
    try:
        return actors.persona_name(cur, role, pid)
    except Exception:
        return str(pid)


def _thread_view(cur, thread):
    """The §2.12 Thread: the stored row minus `team_id` and `state`, plus `actor` and `counterpart`."""
    out = {k: thread.get(k) for k in THREAD_FIELDS}
    out["unread"] = bool(thread.get("unread"))       # a bool in the contract (SQLite keeps it as 0/1)
    out["actor"] = {"role": thread.get("actor_role"), "id": thread.get("actor_id"),
                    "name": _actor_name(cur, thread)}
    out["counterpart"] = {"role": thread.get("counterpart_type"), "id": thread.get("counterpart_id"),
                          "name": thread.get("counterpart_name")}
    return out


def _clean_ref(value):
    """A message's engine_ref without `facts` or any surplus the engine may have left in it."""
    if isinstance(value, dict):
        return {k: _clean_ref(v) for k, v in value.items() if k not in ("facts", "surplus", "surpluses")}
    if isinstance(value, list):
        return [_clean_ref(v) for v in value]
    return value


def _message_view(message):
    """The §2.12 Message: no `facts`, no `options`, appraisal reduced to {outcome, reason}."""
    appraisal = message.get("appraisal")
    shown = ({"outcome": appraisal.get("outcome"), "reason": appraisal.get("reason")}
             if isinstance(appraisal, dict) and appraisal.get("outcome") and appraisal.get("reason") else None)
    return {"id": message.get("id"), "seq": message.get("seq"), "author": message.get("author"),
            "body": message.get("body"), "source": message.get("body_source"),
            "option_id": message.get("option_id"), "engine_ref": _clean_ref(message.get("engine_ref")),
            "appraisal": shown, "created_at": message.get("created_at")}


def _option_view(option):
    out = {"id": option.get("id"), "label": option.get("label"), "form": option.get("form"),
           "needs_note": bool(option.get("needs_note"))}
    if "default" in option:
        out["default"] = option["default"]
    return out


def _options(messages):
    """The options on the latest counterpart line (what JR may answer now)."""
    for message in reversed(messages or []):
        if message.get("author") != "JR":
            return [_option_view(o) for o in (message.get("options") or [])]
    return []


def _draft_view(draft):
    if not isinstance(draft, dict):
        return None
    payload = draft.get("payload")
    return {"option_id": draft.get("option_id"), "payload": payload if isinstance(payload, dict) else None,
            "summary": draft.get("summary") or ""}


def _candidate_view(candidate):
    """The candidate's `also` is the extra role a coach-GM also fills (the TS contract's string | null)."""
    also = candidate.get("also")
    if isinstance(also, dict):
        also = also.get("role")
    return {"role": candidate.get("role"), "persona_id": candidate.get("persona_id"), "name": candidate.get("name"),
            "also": also or None}


# -------------------------------------------------------------------- targets ----
def _player_targets(cur, team_id):
    """ROLE_TALK's picker: the team's active-roster players as §2.12 Target (an ACTIVE contract, not on IR, not
    released and not a placeholder DEF row). This mirrors server/franchise_api.py's `_context` -> `roster["ACTIVE"]`
    shaping."""
    when = scan.league_time(cur)
    if when is None:                                        # no league_state row: nothing to list
        return []
    season, week = int(when[0]), int(when[1])
    cur.execute("""SELECT ps.player_id, COALESCE(p.full_name, p.player_name), ps.position, ps.overall_rating
                   FROM player_seasons ps JOIN players p ON p.id = ps.player_id
                   WHERE ps.season = %s AND ps.team_id = %s AND ps.released_week IS NULL
                     AND COALESCE(ps.position, '') <> 'DEF'
                     AND EXISTS (SELECT 1 FROM player_contracts c
                                 WHERE c.player_id = ps.player_id AND c.season = %s AND c.status = 'ACTIVE')
                     AND NOT EXISTS (
                         SELECT 1 FROM injuries i
                         WHERE i.team_id = ps.team_id AND i.season = ps.season AND i.player_id = ps.player_id
                           AND i.is_ir
                           AND i.week = (SELECT max(i2.week) FROM injuries i2
                                         WHERE i2.team_id = ps.team_id AND i2.season = ps.season AND i2.week <= %s))
                   ORDER BY ps.overall_rating DESC NULLS LAST, COALESCE(p.full_name, p.player_name)""",
                (season, team_id, season, week))
    return [{"id": str(r[0]), "name": r[1] or "", "position": r[2], "overall": r[3]} for r in cur.fetchall()]


def _eligible_player_targets(cur, abbr):
    """CONTRACT's picker: only the players franchise_api._eligibility accepts, so Start never fails on a pick the
    kind's own `start`/`scan` would refuse (FRONT_OFFICE_INBOX_PLAN.md §5 U10). franchise_api is imported lazily
    (the plan's R4). Any failure -- no league_state row, a broken context or eligibility call -- leaves the list
    empty rather than answering a 500."""
    try:
        from engines.inbox.kinds import contract
        from server import franchise_api
        ctx = franchise_api._context(cur, abbr)
        out = []
        for p in ctx.get("players") or []:
            if not contract._active(p):                 # no contract, or on IR
                continue
            ok, _reason = franchise_api._eligibility(p, ctx)
            if ok:
                out.append({"id": str(p["player_id"]), "name": p.get("name") or "",
                            "position": p.get("position"), "overall": p.get("overall")})
        out.sort(key=lambda t: (-(t["overall"] if t["overall"] is not None else -1), t["name"]))
        return out
    except Exception:
        return []


def _gm_targets(cur, team_id):
    """Every other team's open GM tenure as §2.12 Target {id, name, team}; `team` is the sendable abbreviation."""
    cur.execute("SELECT id, abbreviation FROM teams ORDER BY abbreviation")
    out = []
    for tid, abbr in cur.fetchall():
        if tid == team_id:
            continue
        gm = actors.current_gm(cur, tid)
        if gm:
            out.append({"id": str(gm["id"]), "name": gm["name"] or "", "team": str(abbr).upper()})
    return out


def _targets(cur, team_id, abbr, kind):
    if kind == "CONTRACT":
        return _eligible_player_targets(cur, abbr)
    if kind == "ROLE_TALK":
        return _player_targets(cur, team_id)
    if kind == "GM_TALK":
        return _gm_targets(cur, team_id)
    return []


def _detail(cur, thread_id):
    messages = store.messages(cur, thread_id)
    thread = store.get_thread(cur, thread_id)
    return {"thread": _thread_view(cur, thread), "messages": [_message_view(m) for m in messages],
            "options": _options(messages)}


# --------------------------------------------------------------------- actors ----
@router.get("/{abbr}/inbox/actors")
def get_actors(abbr: str):
    with _cursor() as cur, _errors():
        team = _team(cur, abbr)
        return {"controlled": [a.to_dict() for a in actors.controlled(cur, team[1])],
                "candidates": [_candidate_view(c) for c in actors.candidates(cur, team[1])]}


@router.post("/{abbr}/inbox/actors", status_code=201)
def take_control(abbr: str, body: dict = Body(default=None)):
    body = body or {}
    role, persona_id = body.get("role"), body.get("persona_id")
    if not role:
        raise HTTPException(400, "role is required")
    if persona_id is None:
        raise HTTPException(400, "persona_id is required")
    with _cursor() as cur, _errors():
        team = _team(cur, abbr)
        _require_installed(cur)
        return actors.take_control(cur, team[1], role, persona_id).to_dict()


@router.delete("/{abbr}/inbox/actors/{role}/{persona_id}", status_code=204)
def release_control(abbr: str, role: str, persona_id: str):
    with _cursor() as cur, _errors():
        team = _team(cur, abbr)
        _require_installed(cur)
        actors.release_control(cur, team[1], role, persona_id)
        return Response(status_code=204)


# ---------------------------------------------------------------------- kinds ----
@router.get("/{abbr}/inbox/kinds")
def get_kinds(abbr: str):
    with _cursor() as cur, _errors():
        team = _team(cur, abbr)
        controlled = actors.controlled(cur, team[1])
        roles = {a.role for a in controlled}
        out = []
        for name, kind in sorted(registry.kinds().items()):
            served = registry.actor_roles(kind) or registry.DEFAULT_ACTOR_ROLES
            if not roles.intersection(served):
                continue
            out.append({"kind": name, "actor_roles": list(served),
                        "can_start": bool(getattr(kind, "CAN_START", True)),
                        "start_form": getattr(kind, "START_FORM", None)})
        return out


# -------------------------------------------------------------------- targets ----
@router.get("/{abbr}/inbox/targets")
def get_targets(abbr: str, kind: str = Query(default=None)):
    """Who a JR-started conversation can be opened with (wave 4): players `_eligibility` accepts for CONTRACT, the
    active roster for ROLE_TALK, other teams' open GMs for GM_TALK; an unknown or targetless kind answers an empty
    list. Missing kind: 400."""
    if not kind or not str(kind).strip():
        raise HTTPException(400, "kind is required")
    wanted = str(kind).strip().upper()
    with _cursor() as cur, _errors():
        team = _team(cur, abbr)
        _require_installed(cur)
        return {"kind": wanted, "targets": _targets(cur, team[0], team[1], wanted)}


# ----------------------------------------------------------------------- list ----
@router.get("/{abbr}/inbox")
def list_inbox(abbr: str, status: str = Query(default=None), kind: str = Query(default=None),
               as_role: str = Query(default=None, alias="as"), limit: int = Query(default=DEFAULT_LIMIT)):
    with _cursor() as cur, _errors():
        team = _team(cur, abbr)
        team_id = team[0]
        controlled = actors.controlled(cur, team[1])
        roles = {a.role for a in controlled}
        if as_role:
            wanted = str(as_role).upper()
            if wanted not in roles:
                raise HTTPException(403, f"you don't control a {wanted.lower()} on {team[1]}")
        if not controlled:                                   # no actor: an empty inbox (§2.12)
            return {"team": team[1], "actors": [], "unread": 0, "threads": []}
        _scan_on_open(cur, team[1])                          # opening the inbox scans, not just opening a thread
        statuses = None
        if status:
            # "OPEN" is the page's Open / All toggle: every conversation JR can still act on, WAITING_JR included.
            statuses = list(store.OPEN_STATUSES) if str(status).upper() == "OPEN" else [str(status).upper()]
        limit = min(max(int(limit), 1), MAX_LIMIT)
        threads = store.list_threads(cur, team_id, status=statuses, kind=(str(kind).upper() if kind else None),
                                     actor_role=as_role, limit=limit)
        return {"team": team[1], "actors": [a.to_dict() for a in controlled],
                "unread": store.unread_count(cur, team_id),
                "threads": [_thread_view(cur, t) for t in threads]}


# --------------------------------------------------------------------- detail ----
def _scan_on_open(cur, abbr, thread=None):
    """The fast template scan when the inbox is opened (§2.10); once per (team, season, week, stage) per process.
    The list and the detail both open the inbox, so both call this; a thread is only a fallback for the time."""
    when = scan.league_time(cur)
    thread = thread or {}
    season, week = when if when else (thread.get("season"), thread.get("week"))
    if season is None or week is None:
        return
    try:
        scan.scan_on_open(cur, abbr, int(season), int(week))
    except Exception as e:                                  # a scan must never keep the inbox from opening
        print(f"[inbox] open scan skipped: {e!r}", flush=True)


@router.get("/{abbr}/inbox/{thread_id}")
def get_thread(abbr: str, thread_id: int):
    with _cursor() as cur, _errors():
        team = _team(cur, abbr)
        _require_installed(cur)
        _thread_or_404(cur, team[0], thread_id)
        _scan_on_open(cur, team[1], store.get_thread(cur, thread_id))
        store.mark_read(cur, thread_id)
        return _detail(cur, thread_id)


# ---------------------------------------------------------------------- reply ----
@router.post("/{abbr}/inbox/{thread_id}/draft")
def draft(abbr: str, thread_id: int, body: dict = Body(default=None)):
    body = body or {}
    text = body.get("text")
    if text is not None and not isinstance(text, str):
        raise HTTPException(400, "text must be a string")
    with _cursor() as cur, _errors():
        team = _team(cur, abbr)
        _require_installed(cur)
        thread = _thread_or_404(cur, team[0], thread_id)
        kind = registry.get(thread["kind"])
        draft_fn = getattr(kind, "draft", None) if kind is not None else None
        if not callable(draft_fn):
            return {"draft": None}
        store.require_writable(cur, thread)             # a closed / released conversation can't be answered
        return {"draft": _draft_view(draft_fn(cur, thread, text or "", llm=voice.default_llm()))}


def _now_at(cur, thread):
    """The league time a reply happens at: it becomes the thread's last-activity stamp, and so its expiry. Without
    it voice.exchange stamps the creation week and an active conversation expires anyway. None keeps that
    fallback for a save with no league_state row."""
    when = scan.league_time(cur)
    if when is None:
        return None
    return {"season": int(when[0]), "week": int(when[1]), "offseason_stage": thread.get("offseason_stage")}


@router.post("/{abbr}/inbox/{thread_id}/reply")
def reply(abbr: str, thread_id: int, body: dict = Body(default=None)):
    body = body or {}
    option_id, payload, note = body.get("option_id"), body.get("payload"), body.get("note")
    if option_id is not None and not isinstance(option_id, str):
        raise HTTPException(400, "option_id must be a string or null")
    if payload is not None and not isinstance(payload, dict):
        raise HTTPException(400, "payload must be an object or null")
    if note is not None and not isinstance(note, str):
        raise HTTPException(400, "note must be a string or null")
    with _cursor() as cur, _errors():
        team = _team(cur, abbr)
        _require_installed(cur)
        thread = _thread_or_404(cur, team[0], thread_id)
        kind = registry.get(thread["kind"])
        if kind is None:
            raise HTTPException(400, f"unknown conversation kind {thread['kind']}")
        voice.exchange(cur, kind, thread_id, option_id=option_id, payload=payload, note=note,
                       llm=voice.default_llm(), at=_now_at(cur, thread))
        return _detail(cur, thread_id)


# --------------------------------------------------------------------- create ----
@router.post("/{abbr}/inbox", status_code=201)
def start(abbr: str, body: dict = Body(default=None)):
    body = body or {}
    kind_name, role = body.get("kind"), body.get("as")
    note = body.get("note")
    if not kind_name or not isinstance(kind_name, str):
        raise HTTPException(400, "kind is required")
    if not role or not isinstance(role, str):
        raise HTTPException(400, "as is required")
    if note is not None and not isinstance(note, str):
        raise HTTPException(400, "note must be a string or null")
    if note and len(note) > NOTE_MAX_CHARS:
        raise HTTPException(400, f"notes are at most {NOTE_MAX_CHARS} characters")
    with _cursor() as cur, _errors():
        team = _team(cur, abbr)
        _require_installed(cur)
        when = scan.league_time(cur)
        if when is None:
            raise HTTPException(409, "league_state has no row")
        thread_id = scan.start_thread(cur, kind_name, team[1], role, body, season=int(when[0]), week=int(when[1]),
                                      llm=voice.default_llm())
        return _thread_view(cur, store.get_thread(cur, thread_id))


# ---------------------------------------------------------------------- close ----
@router.post("/{abbr}/inbox/{thread_id}/close")
def close(abbr: str, thread_id: int):
    with _cursor() as cur, _errors():
        team = _team(cur, abbr)
        _require_installed(cur)
        thread = _thread_or_404(cur, team[0], thread_id)
        store.require_writable(cur, thread)
        store.close_thread(cur, thread_id)
        return _thread_view(cur, store.get_thread(cur, thread_id))
