"""Focus history and refresh-run records (INFERRED_SLIDERS_AND_FOCUS_PLAN.md §2.5).

Requires db/migrations/2027_02_03_persona_focus.sql. Explicit cursor on the caller's transaction: every write runs
in a savepoint and nothing here connects, commits or rolls back. Rows are append-only: a newer focus supersedes
the active one, a revert marks it `reverted`; nothing is deleted.

  tables_ready(cur)                         both tables exist
  insert_focus(cur, row)                    'inserted' | 'replayed' (same id and content); FocusConflict otherwise
  supersede(cur, old_focus_id, new_row)     old -> superseded, new active (one savepoint)
  mark_reverted(cur, focus_id)              an active row -> reverted
  read_focus(cur, universe, persona, as_of=None)   {'want': row | None, 'fear': row | None}
  history(cur, universe, persona)           every row, by slot and seq
  record_run(cur, run) / read_run(cur, universe, season, rules_version)
"""
from __future__ import annotations

from contextlib import contextmanager
import uuid

FOCUS_COLUMNS = ('focus_id', 'universe_id', 'persona_type', 'persona_id', 'slot', 'seq', 'text', 'subjects', 'anchor',
                 'until_season', 'source', 'template_id', 'rules_version', 'status', 'valid_from_season',
                 'valid_from_tick', 'supersedes', 'stale_causes', 'reason', 'actor')
CONTENT_COLUMNS = tuple(c for c in FOCUS_COLUMNS if c != 'status')
RUN_COLUMNS = ('universe_id', 'season', 'rules_version', 'input_hash', 'counts', 'status', 'error')
JSON_COLUMNS = ('subjects', 'anchor', 'stale_causes', 'counts')
SOURCES = ('profile', 'template', 'jr')
STATUSES = ('active', 'superseded', 'reverted')
RUN_STATUSES = ('APPLIED', 'PREVIEW', 'FAILED')
SLOTS = ('want', 'fear')
PERSONA_TYPES = ('coach', 'owner')
DEFAULTS = {'subjects': [], 'anchor': {}, 'stale_causes': [], 'actor': 'system', 'status': 'active'}


class FocusStoreError(ValueError):
    pass


class FocusConflict(FocusStoreError):
    pass


def focus_id(universe_id, persona_id, slot, seq) -> str:
    return f'focus:{universe_id}:{persona_id}:{slot}:{seq}'


@contextmanager
def _savepoint(cur):
    name = 'focus_' + uuid.uuid4().hex
    cur.execute(f'SAVEPOINT {name}')
    try:
        yield
    except BaseException:
        cur.execute(f'ROLLBACK TO SAVEPOINT {name}')
        cur.execute(f'RELEASE SAVEPOINT {name}')
        raise
    else:
        cur.execute(f'RELEASE SAVEPOINT {name}')


def _jsonb(value):
    from psycopg.types.json import Jsonb
    return Jsonb(value)


def _param(column, value):
    return _jsonb(value) if column in JSON_COLUMNS else value


def _normal(row) -> dict:
    out = {c: row.get(c, DEFAULTS.get(c)) for c in FOCUS_COLUMNS}
    if out['focus_id'] is None:
        out['focus_id'] = focus_id(out['universe_id'], out['persona_id'], out['slot'], out['seq'])
    return out


def _check(row):
    if row['persona_type'] not in PERSONA_TYPES:
        raise FocusStoreError(f"persona_type {row['persona_type']!r}")
    if row['slot'] not in SLOTS:
        raise FocusStoreError(f"slot {row['slot']!r}")
    if row['source'] not in SOURCES:
        raise FocusStoreError(f"source {row['source']!r}")
    if row['status'] not in STATUSES:
        raise FocusStoreError(f"status {row['status']!r}")
    if not isinstance(row['text'], str) or not row['text'].strip():
        raise FocusStoreError('focus text must be nonempty')
    if type(row['seq']) is not int or row['seq'] < 0:
        raise FocusStoreError('seq must be an integer >= 0')
    if row['focus_id'] != focus_id(row['universe_id'], row['persona_id'], row['slot'], row['seq']):
        raise FocusStoreError(f"focus_id {row['focus_id']} does not match universe, persona, slot and seq")
    if not (isinstance(row['valid_from_tick'], int) and 1 <= row['valid_from_tick'] <= 32):
        raise FocusStoreError('valid_from_tick must be 1-32')


def tables_ready(cur) -> bool:
    cur.execute("SELECT to_regclass('persona_focus') IS NOT NULL AND to_regclass('persona_focus_runs') IS NOT NULL")
    row = cur.fetchone()
    return bool(row and row[0])


def _select(cur, where, params):
    cur.execute('SELECT ' + ', '.join(FOCUS_COLUMNS) + ' FROM persona_focus WHERE ' + where
                + ' ORDER BY slot, seq', params)
    return [dict(zip(FOCUS_COLUMNS, r)) for r in cur.fetchall()]


def read_row(cur, fid):
    rows = _select(cur, 'focus_id = %s', (fid,))
    return rows[0] if rows else None


def _insert(cur, row):
    cur.execute('INSERT INTO persona_focus (' + ', '.join(FOCUS_COLUMNS) + ') VALUES ('
                + ', '.join(['%s'] * len(FOCUS_COLUMNS)) + ')', [_param(c, row[c]) for c in FOCUS_COLUMNS])


def insert_focus(cur, row) -> str:
    """Insert once. The same focus_id with the same content is 'replayed'; different content raises."""
    row = _normal(row)
    _check(row)
    existing = read_row(cur, row['focus_id'])
    if existing is not None:
        if any(existing.get(c) != row[c] for c in CONTENT_COLUMNS):
            raise FocusConflict(f"{row['focus_id']} already exists with different content")
        return 'replayed'
    with _savepoint(cur):
        _insert(cur, row)
    return 'inserted'


def supersede(cur, old_focus_id, new_row) -> dict:
    """The active old row becomes superseded and the new row (supersedes = old) becomes the active one."""
    new = _normal(dict(new_row, supersedes=old_focus_id, status='active'))
    _check(new)
    with _savepoint(cur):
        cur.execute("UPDATE persona_focus SET status = 'superseded' WHERE focus_id = %s AND status = 'active'",
                    (old_focus_id,))
        if cur.rowcount != 1:
            raise FocusStoreError(f'{old_focus_id} is not an active focus')
        _insert(cur, new)
    return new


def mark_reverted(cur, fid) -> None:
    with _savepoint(cur):
        cur.execute("UPDATE persona_focus SET status = 'reverted' WHERE focus_id = %s AND status = 'active'", (fid,))
        if cur.rowcount != 1:
            raise FocusStoreError(f'{fid} is not an active focus')


def history(cur, universe_id, persona_id) -> list[dict]:
    return _select(cur, 'universe_id = %s AND persona_id = %s', (universe_id, persona_id))


def read_focus(cur, universe_id, persona_id, *, as_of=None) -> dict:
    """The active row per slot, or with as_of=(season, tick) the row valid then: the highest seq whose
    valid_from is at or before as_of (its successor's valid_from is later)."""
    out = {s: None for s in SLOTS}
    for row in history(cur, universe_id, persona_id):
        if as_of is None:
            if row['status'] == 'active':
                out[row['slot']] = row
        elif (row['valid_from_season'], row['valid_from_tick']) <= tuple(as_of):
            cur_row = out[row['slot']]
            if cur_row is None or row['seq'] > cur_row['seq']:
                out[row['slot']] = row
    return out


def next_seq(cur, universe_id, persona_id, slot) -> int:
    rows = [r for r in history(cur, universe_id, persona_id) if r['slot'] == slot]
    return max((r['seq'] for r in rows), default=-1) + 1


def record_run(cur, run) -> None:
    """Insert or replace the (universe, season, rules_version) run row."""
    if run.get('status') not in RUN_STATUSES:
        raise FocusStoreError(f"run status {run.get('status')!r}")
    with _savepoint(cur):
        cur.execute('INSERT INTO persona_focus_runs (' + ', '.join(RUN_COLUMNS) + ') VALUES ('
                    + ', '.join(['%s'] * len(RUN_COLUMNS)) + ''') ON CONFLICT (universe_id, season, rules_version)
                    DO UPDATE SET input_hash = EXCLUDED.input_hash, counts = EXCLUDED.counts,
                                  status = EXCLUDED.status, error = EXCLUDED.error, created_at = now()''',
                    [_param(c, run.get(c)) for c in RUN_COLUMNS])


def read_run(cur, universe_id, season, rules_version):
    cur.execute('SELECT ' + ', '.join(RUN_COLUMNS) + ''' FROM persona_focus_runs
                   WHERE universe_id = %s AND season = %s AND rules_version = %s''',
                (universe_id, season, rules_version))
    row = cur.fetchone()
    return None if row is None else dict(zip(RUN_COLUMNS, row))
