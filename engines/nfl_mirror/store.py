"""The NFL weekly mirror's tables (docs/in_progress/NFL_WEEKLY_MIRROR_PLAN.md §2.1, §2.4).

    read_settings(cur) -> dict | None
    set_enabled(cur, enabled, season=2026, last_week=None)
    mirror_active(cur, season, stage, week) -> bool
    set_injuries_state(cur, state)          set_injuries_field(cur, field_json)
    record_week(cur, season, week, **fields)                     upsert of nfl_mirror_weeks
    record_moves(cur, season, week, moves, replace=False) -> [id]
    mark_written(cur, season, week, run_id, outcomes=None, counts=None, report=None)
    week_status(cur, season, week) -> 'PLANNED' | 'WRITTEN' | 'FAILED' | None
    links(cur) -> {gsis_id: pgid}
    add_created_link(cur, player_id, gsis_id, evidence) -> bool

Tables come from db/migrations/2027_02_27_nfl_mirror.sql. Every function works inside the caller's transaction and
never commits. A database without the migration reads as "mirror off": `read_settings` is None and `mirror_active`
False.
"""
import json

SETTINGS_ID = 1
DEFAULT_SEASON = 2026
MIRROR_STAGES = frozenset(("REGULAR_WEEK", "PLAYOFF_WEEK"))
INJURIES_STATES = ("ON", "OFF")
MODES = ("CATCH_UP", "LIVE")
WEEK_STATUSES = ("PLANNED", "WRITTEN", "FAILED")
MOVE_KINDS = ("SIGN", "RELEASE", "TRADE", "PS", "IR", "ACTIVATE", "INJURY", "HEAL", "CREATE", "CHART", "STAFF",
              "SETTING")
MOVE_STATUSES = ("PLANNED", "WRITTEN", "SKIPPED", "REFUSED")
WEEK_FIELDS = ("real_week", "mode", "sources", "counts", "run_id", "status", "report")
MOVE_FIELDS = ("kind", "gsis_id", "player_id", "pgid", "from_team_id", "to_team_id", "detail", "status", "reason")
_JSON_FIELDS = ("sources", "counts", "detail", "madden_injuries_field", "evidence")
SETTINGS_COLUMNS = ("enabled", "season", "last_week", "madden_injuries_field", "injuries_state", "updated_at")


def _jsonb(value):
    from psycopg.types.json import Jsonb
    return Jsonb(value)


def _decode(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def _table_exists(cur, name):
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (name,))
    row = cur.fetchone()
    return bool(row and row[0])


def installed(cur):
    return _table_exists(cur, "nfl_mirror_settings")


# ---------------------------------------------------------------------------------------------- settings ----
def read_settings(cur):
    """The settings row as a dict, or None when the migration is not applied (or the row is missing)."""
    if not installed(cur):
        return None
    cur.execute(f"SELECT {', '.join(SETTINGS_COLUMNS)} FROM nfl_mirror_settings WHERE id = %s", (SETTINGS_ID,))
    row = cur.fetchone()
    if row is None:
        return None
    out = dict(zip(SETTINGS_COLUMNS, row))
    out["enabled"] = bool(out["enabled"])
    out["madden_injuries_field"] = _decode(out["madden_injuries_field"])
    return out


def _ensure_row(cur):
    cur.execute("INSERT INTO nfl_mirror_settings (id) VALUES (%s) ON CONFLICT (id) DO NOTHING", (SETTINGS_ID,))


def set_enabled(cur, enabled, season=DEFAULT_SEASON, last_week=None):
    """Turn the mirror on or off for `season`, through `last_week` (None = through the Super Bowl)."""
    if last_week is not None and int(last_week) < 1:
        raise ValueError(f"last_week must be a Mu week number, got {last_week!r}")
    _ensure_row(cur)
    cur.execute("""UPDATE nfl_mirror_settings SET enabled = %s, season = %s, last_week = %s, updated_at = now()
                   WHERE id = %s""",
                (bool(enabled), int(season), None if last_week is None else int(last_week), SETTINGS_ID))


def mirror_active(cur, season, stage, week):
    """True when the mirror is on for `season`, `stage` is a regular-season or playoff week and `week` (Mu's week
    number) is not past `last_week`. False when the tables are missing."""
    settings = read_settings(cur)
    if not settings or not settings["enabled"]:
        return False
    if season is None or int(settings["season"]) != int(season):
        return False
    if stage not in MIRROR_STAGES:
        return False
    if week is None:
        return False
    last = settings["last_week"]
    return last is None or int(week) <= int(last)


def set_injuries_state(cur, state):
    """Record what the league office last wrote to Madden's Injuries setting ('ON' / 'OFF')."""
    state = str(state).upper()
    if state not in INJURIES_STATES:
        raise ValueError(f"injuries_state must be ON or OFF, got {state!r}")
    _ensure_row(cur)
    cur.execute("UPDATE nfl_mirror_settings SET injuries_state = %s, updated_at = now() WHERE id = %s",
                (state, SETTINGS_ID))


def set_injuries_field(cur, field_json):
    """Store Spike A's Injuries field ({"table", "tableId", "row", "field", "off", "on"}); None clears it."""
    if field_json is not None and not isinstance(field_json, dict):
        raise ValueError("madden_injuries_field must be a mapping or None")
    _ensure_row(cur)
    cur.execute("UPDATE nfl_mirror_settings SET madden_injuries_field = %s, updated_at = now() WHERE id = %s",
                (None if field_json is None else _jsonb(field_json), SETTINGS_ID))


# ------------------------------------------------------------------------------------------------- weeks ----
def _week_value(name, value):
    if name in ("sources", "counts"):
        return _jsonb(value or {})
    if name == "mode" and value is not None and value not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {value!r}")
    if name == "status" and value not in WEEK_STATUSES:
        raise ValueError(f"status must be one of {WEEK_STATUSES}, got {value!r}")
    return value


def record_week(cur, season, week, **fields):
    """Insert or update the nfl_mirror_weeks row for (season, week). Only the fields given are set on an update."""
    unknown = set(fields) - set(WEEK_FIELDS)
    if unknown:
        raise ValueError(f"unknown nfl_mirror_weeks fields: {sorted(unknown)}")
    names = [n for n in WEEK_FIELDS if n in fields]
    values = [_week_value(n, fields[n]) for n in names]
    columns = ["season", "week"] + names
    update = ", ".join(f"{n} = EXCLUDED.{n}" for n in names)
    sql = (f"INSERT INTO nfl_mirror_weeks ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))}) "
           f"ON CONFLICT (season, week) DO " + (f"UPDATE SET {update}" if names else "NOTHING"))
    cur.execute(sql, (int(season), int(week), *values))


def week_status(cur, season, week):
    """The week's status, or None when it has no row (or the migration is not applied)."""
    if not _table_exists(cur, "nfl_mirror_weeks"):
        return None
    cur.execute("SELECT status FROM nfl_mirror_weeks WHERE season = %s AND week = %s", (int(season), int(week)))
    row = cur.fetchone()
    return row[0] if row else None


# ------------------------------------------------------------------------------------------------- moves ----
def _move_values(move):
    kind = move.get("kind")
    if kind not in MOVE_KINDS:
        raise ValueError(f"unknown move kind {kind!r}")
    status = move.get("status") or "PLANNED"
    if status not in MOVE_STATUSES:
        raise ValueError(f"unknown move status {status!r}")
    unknown = set(move) - set(MOVE_FIELDS)
    if unknown:
        raise ValueError(f"unknown nfl_mirror_moves fields: {sorted(unknown)}")
    out = []
    for name in MOVE_FIELDS:
        value = move.get(name)
        if name == "detail":
            value = _jsonb(value or {})
        elif name == "status":
            value = status
        out.append(value)
    return out


def record_moves(cur, season, week, moves, replace=False):
    """Insert the week's moves (dicts with MOVE_FIELDS keys; `kind` required, `status` defaults to PLANNED) and
    return their ids. `replace=True` first deletes the week's moves that were never written, so re-planning a week
    does not pile up duplicates; WRITTEN moves are never deleted."""
    if replace:
        cur.execute("DELETE FROM nfl_mirror_moves WHERE season = %s AND week = %s AND status <> 'WRITTEN'",
                    (int(season), int(week)))
    ids = []
    sql = (f"INSERT INTO nfl_mirror_moves (season, week, {', '.join(MOVE_FIELDS)}) "
           f"VALUES ({', '.join(['%s'] * (len(MOVE_FIELDS) + 2))}) RETURNING id")
    for move in moves:
        cur.execute(sql, (int(season), int(week), *_move_values(move)))
        ids.append(cur.fetchone()[0])
    return ids


def mark_written(cur, season, week, run_id, outcomes=None, counts=None, report=None):
    """The league office wrote the week: the week row is WRITTEN with its run, and its moves take their outcomes.

    Without `outcomes` every PLANNED move becomes WRITTEN (the pre-F3 behaviour). With it, `outcomes` maps each
    move's `detail.tag` to `[status, reason]` (WRITTEN / SKIPPED / REFUSED), so a refused move stays REFUSED; a
    PLANNED move with no outcome recorded is SKIPPED rather than claimed as written. `counts` / `report`, when
    given, describe the applied results on the week row (audit-4 U6 F3)."""
    fields = {"run_id": run_id, "status": "WRITTEN"}
    if counts is not None:
        fields["counts"] = counts
    if report is not None:
        fields["report"] = report
    record_week(cur, season, week, **fields)
    if outcomes is None:
        cur.execute("""UPDATE nfl_mirror_moves SET status = 'WRITTEN'
                       WHERE season = %s AND week = %s AND status = 'PLANNED'""", (int(season), int(week)))
        return
    for tag, outcome in outcomes.items():
        status, reason = _outcome(outcome)
        cur.execute("""UPDATE nfl_mirror_moves SET status = %s, reason = COALESCE(%s, reason)
                       WHERE season = %s AND week = %s AND status = 'PLANNED' AND detail ->> 'tag' = %s""",
                    (status, reason, int(season), int(week), tag))
    cur.execute("""UPDATE nfl_mirror_moves SET status = 'SKIPPED',
                       reason = COALESCE(reason, 'no outcome recorded')
                   WHERE season = %s AND week = %s AND status = 'PLANNED'""", (int(season), int(week)))


def _outcome(value):
    """(status, reason) from an operation record's outcome: `[status, reason]` or `{"status", "reason"}`."""
    if isinstance(value, dict):
        status, reason = value.get("status"), value.get("reason")
    elif isinstance(value, (list, tuple)) and value:
        status, reason = value[0], value[1] if len(value) > 1 else None
    else:
        status, reason = value, None
    status = str(status or "").upper()
    if status not in ("WRITTEN", "SKIPPED", "REFUSED"):
        raise ValueError(f"a move outcome must be WRITTEN, SKIPPED or REFUSED, got {value!r}")
    return status, reason


# ------------------------------------------------------------------------------------------------- links ----
def links(cur):
    """{gsis_id: madden_pgid} for every linked player whose save PresentationId is known."""
    if not _table_exists(cur, "nfl_player_links"):
        return {}
    cur.execute("""SELECT l.gsis_id, p.madden_pgid FROM nfl_player_links l JOIN players p ON p.id = l.player_id
                   WHERE p.madden_pgid IS NOT NULL""")
    return {gsis: pgid for gsis, pgid in cur.fetchall()}


def add_created_link(cur, player_id, gsis_id, evidence):
    """Link a player the mirror created to his real person (method 'CREATED'). Returns True when a link was added,
    False when this exact link already exists; raises ValueError when either side is already linked elsewhere.

    nfl_player_links.gsis_id references nfl_players. A 2026 rookie may not be there yet (the history load stops at
    2025), so when `evidence` carries a `name` the person is added with what the evidence knows; otherwise the
    foreign key decides."""
    evidence = dict(evidence or {})
    cur.execute("SELECT player_id, gsis_id FROM nfl_player_links WHERE player_id = %s OR gsis_id = %s",
                (int(player_id), gsis_id))
    existing = cur.fetchall()
    if existing:
        if all(int(p) == int(player_id) and g == gsis_id for p, g in existing):
            return False
        raise ValueError(f"player {player_id} or {gsis_id} is already linked: {existing}")
    if evidence.get("name"):
        cur.execute("""INSERT INTO nfl_players (gsis_id, display_name, position, birth_date, college)
                       VALUES (%s, %s, %s, %s, %s) ON CONFLICT (gsis_id) DO NOTHING""",
                    (gsis_id, evidence["name"], evidence.get("position"), evidence.get("birth_date"),
                     evidence.get("college")))
    cur.execute("""INSERT INTO nfl_player_links (player_id, gsis_id, method, score, evidence)
                   VALUES (%s, %s, 'CREATED', NULL, %s)""", (int(player_id), gsis_id, _jsonb(evidence)))
    return True
