"""Evolution run and candidate records (PERSONALITY_EVOLUTION_PLAN.md §2.4-§2.5).

Requires db/migrations/2027_01_30_personality_evolution.sql. Explicit cursor on the caller's transaction: every
write runs in a savepoint and nothing here connects, commits or rolls back. Profiles are never written here; the
applied change itself is a persona_core_events receipt written by personality.store.change.
"""
from __future__ import annotations

from contextlib import contextmanager
import uuid

from psycopg.types.json import Jsonb

CANDIDATE_COLUMNS = ('candidate_id', 'universe_id', 'persona_type', 'persona_id', 'season', 'source', 'rule_id',
                     'rules_version', 'change', 'chance', 'draw', 'source_event_ids', 'note', 'status', 'reason',
                     'core_event_id')
RUN_COLUMNS = ('universe_id', 'season', 'rules_version', 'input_hash', 'counts', 'status', 'error')
STORED_STATUSES = ('drawn_out', 'blocked', 'superseded', 'applied', 'reverted')
RUN_STATUSES = ('APPLIED', 'PREVIEW', 'FAILED')


class EvolutionStoreError(ValueError):
    pass


@contextmanager
def _savepoint(cur):
    name = 'evolution_' + uuid.uuid4().hex
    cur.execute(f'SAVEPOINT {name}')
    try:
        yield
    except BaseException:
        cur.execute(f'ROLLBACK TO SAVEPOINT {name}')
        cur.execute(f'RELEASE SAVEPOINT {name}')
        raise
    else:
        cur.execute(f'RELEASE SAVEPOINT {name}')


def _value(column, value):
    if column in ('change', 'counts'):
        return Jsonb(value)
    if column == 'source_event_ids':
        return list(value or [])
    return value


def record_candidates(cur, rows) -> int:
    """Insert candidates once; a candidate already recorded (same candidate_id) is kept as it was. Returns how
    many were new."""
    written = 0
    with _savepoint(cur):
        for row in rows:
            if row.get('status') not in STORED_STATUSES:
                raise EvolutionStoreError(f"candidate {row.get('candidate_id')} has status {row.get('status')!r}")
            cur.execute('INSERT INTO persona_evolution_candidates (' + ', '.join(CANDIDATE_COLUMNS) + ') VALUES ('
                        + ', '.join(['%s'] * len(CANDIDATE_COLUMNS)) + ') ON CONFLICT (candidate_id) DO NOTHING',
                        [_value(c, row.get(c)) for c in CANDIDATE_COLUMNS])
            written += max(cur.rowcount, 0)
    return written


def record_run(cur, run) -> None:
    """Insert or replace the (universe, season, rules_version) run row."""
    if run.get('status') not in RUN_STATUSES:
        raise EvolutionStoreError(f"run status {run.get('status')!r}")
    with _savepoint(cur):
        cur.execute('INSERT INTO persona_evolution_runs (' + ', '.join(RUN_COLUMNS) + ') VALUES ('
                    + ', '.join(['%s'] * len(RUN_COLUMNS)) + ''') ON CONFLICT (universe_id, season, rules_version)
                    DO UPDATE SET input_hash = EXCLUDED.input_hash, counts = EXCLUDED.counts,
                                  status = EXCLUDED.status, error = EXCLUDED.error, created_at = now()''',
                    [_value(c, run.get(c)) for c in RUN_COLUMNS])


def read_run(cur, universe_id, season, rules_version):
    cur.execute('SELECT ' + ', '.join(RUN_COLUMNS) + ''' FROM persona_evolution_runs
                   WHERE universe_id = %s AND season = %s AND rules_version = %s''',
                (universe_id, season, rules_version))
    row = cur.fetchone()
    return None if row is None else dict(zip(RUN_COLUMNS, row))


def read_candidates(cur, universe_id, season):
    """Every recorded candidate of a season, in a stable order."""
    cur.execute('SELECT ' + ', '.join(CANDIDATE_COLUMNS) + ''' FROM persona_evolution_candidates
                   WHERE universe_id = %s AND season = %s ORDER BY persona_type, persona_id, rule_id''',
                (universe_id, season))
    out = []
    for row in cur.fetchall():
        d = dict(zip(CANDIDATE_COLUMNS, row))
        d['chance'] = None if d['chance'] is None else float(d['chance'])
        d['source_event_ids'] = list(d['source_event_ids'] or [])
        out.append(d)
    return out


def read_candidate(cur, candidate_id):
    cur.execute('SELECT ' + ', '.join(CANDIDATE_COLUMNS) + ' FROM persona_evolution_candidates WHERE candidate_id = %s',
                (candidate_id,))
    row = cur.fetchone()
    return None if row is None else dict(zip(CANDIDATE_COLUMNS, row))


def mark_reverted(cur, candidate_id, override_receipt) -> None:
    """Mark an applied candidate reverted, naming the JR override that undid it. The evolution receipt stays."""
    with _savepoint(cur):
        cur.execute('''UPDATE persona_evolution_candidates SET status = 'reverted', reason = %s
                       WHERE candidate_id = %s AND status = 'applied' ''',
                    ('reverted by JR override ' + str(override_receipt.get('override_id')) + ': '
                     + str(override_receipt.get('reason')), candidate_id))
        if cur.rowcount != 1:
            raise EvolutionStoreError(f'candidate {candidate_id} is not an applied change')
