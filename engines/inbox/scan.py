"""
Inbound messages (FRONT_OFFICE_INBOX_PLAN.md §2.10).

For each registry user team, each controlled actor and each kind serving its role: kind.scan -> Trigger ->
store.create_thread (idempotent on (team, kind, source_ref), capped per kind) -> the opening message via voice.
Then each kind's optional review_promises hook (ROLE_TALK resolves due promises), store.expire_stale and
appraisal.flush. One failing kind is logged and skipped (its work is rolled back to a savepoint).

  scan(cur, season, week, teams=None, llm=None, ...) -> {'created', 'expired', 'reviewed', 'by_kind', 'errors'}
  open_thread(cur, kind, trigger, actor, season, week, ...) -> thread id | None
  start_thread(cur, kind, abbr, role, body, ...) -> thread id          a JR-started conversation (kind.start)
  scan_on_open(cur, abbr, season, week, offseason_stage=None) -> dict | None   once per (team, season, week, stage)
                                                                                per process, templates only
  step_inbox(season, log_fn=print) -> dict | None                      the weekly run's step; never raises

No user team (2026-2033), no controlled persona or no inbox tables -> nothing happens. Nothing here commits except
step_inbox, which owns its connection.
"""
import traceback

from engines.inbox import Conflict, InboxError, NotUserTeam, actors, appraisal, registry, store, voice

_OPENED = set()          # (abbr, season, week, stage) already scanned on opening the inbox in this process


def _team(acts, abbr, season, week, offseason_stage):
    return {"team_id": acts[0].team_id, "abbr": abbr, "season": season, "week": week,
            "offseason_stage": offseason_stage, "actors": list(acts)}


def _thread_row(trigger, actor, season, week, offseason_stage, started_by):
    role = trigger.actor_role or (actor.role if actor is not None else "TEAM")
    actor_id = trigger.actor_id or (actor.persona_id if actor is not None else "TEAM")
    return {"team_id": trigger.team_id, "kind": trigger.kind, "actor_role": role, "actor_id": str(actor_id),
            "counterpart_type": trigger.counterpart_type, "counterpart_id": str(trigger.counterpart_id),
            "counterpart_name": trigger.counterpart_name, "subject": trigger.subject[:255],
            "started_by": started_by, "source_ref": trigger.source_ref, "link_table": trigger.link_table,
            "link_id": trigger.link_id, "state": dict(trigger.state or {}), "season": season, "week": week,
            "offseason_stage": offseason_stage, "expires_season": trigger.expires_season,
            "expires_week": trigger.expires_week}


def open_thread(cur, kind, trigger, actor, season, week, *, offseason_stage=None, llm=None, started_by=None,
                enforce_cap=True):
    """Store the trigger's thread and its opening message. None when the source already has a thread or the
    kind's cap is reached."""
    if started_by is None:
        started_by = "SYSTEM" if trigger.counterpart_type == "system" else "COUNTERPART"
    tid = store.create_thread(cur, _thread_row(trigger, actor, season, week, offseason_stage, started_by),
                              enforce_cap=enforce_cap)
    if tid is None:
        return None
    thread = store.get_thread(cur, tid)
    reply = voice.compose(cur, kind, thread, trigger.intent, trigger.facts, (), None, llm, judge=False)
    options = list(kind.options(cur, thread, None) or [])
    author = "SYSTEM" if trigger.counterpart_type == "system" else "COUNTERPART"
    store.add_message(cur, tid, author, reply.text, reply.source, options=options, facts=trigger.facts,
                      at={"season": season, "week": week, "offseason_stage": offseason_stage})
    if reply.data and reply.source == "LLM":
        hook = getattr(kind, "on_reply", None)
        if callable(hook):
            hook(cur, store.get_thread(cur, tid), reply.data)
    return tid


def start_thread(cur, kind_name, abbr, role, body, *, season, week, offseason_stage=None, llm=None):
    """A conversation JR starts as his controlled persona `role` (kind.start(cur, team, body) -> Trigger). Raises
    NotUserTeam (403) for a team outside the registry or a role JR doesn't control, InboxError 400 for an unknown
    kind or a role it doesn't serve, Conflict (409) when the source already has a thread or the cap is reached."""
    kind = registry.get(kind_name)
    if kind is None:
        raise InboxError(400, f"unknown conversation kind {kind_name}")
    actor = actors.actor_for(cur, abbr, role)
    if actor is None:
        raise NotUserTeam(f"you don't control a {str(role).lower()} on {str(abbr).upper()}")
    if not registry.serves(kind, actor.role):
        raise InboxError(400, f"a {actor.role.lower()} can't start a {kind_name} conversation")
    acts = actors.controlled(cur, abbr)
    team = dict(_team(acts, actor.abbr, season, week, offseason_stage), actor=actor)
    trigger = kind.start(cur, team, dict(body or {}))
    if store.at_cap(cur, trigger.team_id, trigger.kind):
        raise Conflict(f"too many open {kind_name} conversations")
    tid = open_thread(cur, kind, trigger, actor, season, week, offseason_stage=offseason_stage, llm=llm,
                      started_by="JR")
    if tid is None:
        raise Conflict(f"that {kind_name} conversation is already open")
    return tid


def _log(log_fn, msg):
    if log_fn is not None:
        log_fn(msg)


def scan(cur, season, week, teams=None, llm=None, *, offseason_stage=None, dry_run=False, review=True,
         flush=True, log_fn=print):
    """The inbound scan. teams: abbreviations (default: every registry user team); only registry user teams count.
    dry_run: count triggers without storing anything. review / flush: run the promise reviews and the outbox flush
    (the weekly run and the CLI; opening the inbox passes False for both)."""
    out = {"created": 0, "expired": 0, "reviewed": 0, "by_kind": {}, "errors": [], "triggers": 0, "flush": None}
    if not store.installed(cur) or not actors.installed(cur):
        out["skipped"] = "inbox tables missing"
        return out
    user = actors.user_teams()
    wanted = [str(t).upper() for t in (teams if teams is not None else user)]
    abbrs = [t for t in wanted if t in user]
    kinds = registry.kinds()
    team_ids = []
    for abbr in abbrs:
        acts = actors.controlled(cur, abbr)
        if not acts:
            continue
        team = _team(acts, abbr, season, week, offseason_stage)
        team_ids.append(team["team_id"])
        for name, kind in sorted(kinds.items()):
            for actor in registry.actors_to_scan(kind, acts):
                try:
                    with appraisal._savepoint(cur):
                        triggers = list(kind.scan(cur, season, week, dict(team, actor=actor)) or [])
                        out["triggers"] += len(triggers)
                        for trig in triggers:
                            if dry_run:
                                continue
                            if open_thread(cur, kind, trig, actor, season, week, offseason_stage=offseason_stage,
                                           llm=llm) is not None:
                                out["created"] += 1
                                out["by_kind"][name] = out["by_kind"].get(name, 0) + 1
                except Exception as e:
                    out["errors"].append(f"{abbr} {name}: {e!r}")
                    _log(log_fn, f"  [WARN] inbox kind {name} skipped for {abbr}: {e!r}")
            hook = getattr(kind, "review_promises", None)
            if review and not dry_run and callable(hook) and any(registry.serves(kind, a.role) for a in acts):
                try:
                    with appraisal._savepoint(cur):
                        out["reviewed"] += int(hook(cur, season, week, team) or 0)
                except Exception as e:
                    out["errors"].append(f"{abbr} {name} review: {e!r}")
                    _log(log_fn, f"  [WARN] inbox promise review {name} skipped for {abbr}: {e!r}")
    if not dry_run and team_ids:
        out["expired"] = store.expire_stale(cur, season, week, team_ids=team_ids, offseason_stage=offseason_stage)
    if flush and not dry_run:
        try:
            with appraisal._savepoint(cur):
                out["flush"] = appraisal.flush(cur)
        except Exception as e:
            out["errors"].append(f"flush: {e!r}")
            _log(log_fn, f"  [WARN] inbox event flush skipped: {e!r}")
    return out


def scan_on_open(cur, abbr, season, week, offseason_stage=None, log_fn=None):
    """Opening the inbox: a fast template scan of one team, at most once per (team, season, week, stage) per
    process. Returns the scan's result, or None when it already ran."""
    key = (str(abbr).upper(), season, week, offseason_stage)
    if key in _OPENED:
        return None
    _OPENED.add(key)
    return scan(cur, season, week, teams=[key[0]], llm=None, offseason_stage=offseason_stage, review=False,
                flush=False, log_fn=log_fn)


def league_time(cur):
    """(season, week) from league_state, or None."""
    cur.execute("SELECT current_season, current_week FROM league_state WHERE id = 1")
    row = cur.fetchone()
    return (row[0], row[1]) if row else None


def step_inbox(season, log_fn=print, dsn=None):
    """The weekly run's inbox step (after step_gm): the scan with the model voice, promise reviews and the outbox
    flush for league_state's week. Skipped with no registry user team or without the inbox tables. Never raises."""
    try:
        if not actors.user_teams():
            _log(log_fn, "  Inbox skipped: no user team in the franchise registry")
            return None
        import psycopg
        if dsn is None:
            from pipeline.load_capture import DSN as dsn
        llm = voice.default_llm()
        with psycopg.connect(dsn) as conn:
            cur = conn.cursor()
            if not store.installed(cur) or not actors.installed(cur):
                _log(log_fn, "  Inbox skipped: the front-office inbox migration isn't applied")
                return None
            now = league_time(cur)
            week = now[1] if now and now[0] == season else None
            if week is None:
                _log(log_fn, f"  Inbox skipped: league_state isn't in season {season}")
                return None
            res = scan(cur, season, week, llm=llm, log_fn=log_fn)
            conn.commit()
        try:
            from engines.gm_llm import flush as flush_usage
            flush_usage(llm)
        except Exception:
            pass
        _log(log_fn, f"  Inbox: {res['created']} new, {res['expired']} expired, {res['reviewed']} promises reviewed"
                     + (f", {len(res['errors'])} kind errors" if res["errors"] else ""))
        return res
    except Exception as e:
        traceback.print_exc()
        _log(log_fn, f"  [WARN] Inbox step skipped: {e!r}")
        return None
