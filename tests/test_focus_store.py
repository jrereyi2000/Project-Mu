"""Focus store and migration (INFERRED_SLIDERS_AND_FOCUS_PLAN.md §2.5, §4).

Fake-cursor unit tests, offline. The opt-in case at the bottom is marked integration and skips unless
FOCUS_TEST_DSN names a scratch database (never madden or madden_*)."""
from copy import deepcopy
import os
import pathlib
import types

import pytest

from personality import focus_store as fs

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / 'db' / 'migrations' / '2027_02_03_persona_focus.sql'
U = 'mu:test'


class FakeCursor:
    """persona_focus and persona_focus_runs in memory with savepoints; commit is forbidden."""

    def __init__(self, ready=True):
        self.rows, self.runs, self._sp, self.ready = {}, {}, {}, ready
        self._result, self.rowcount, self.sql = [], -1, []
        self.connection = types.SimpleNamespace(commit=self._forbidden, rollback=self._forbidden)

    def _forbidden(self, *a, **k):
        raise AssertionError('the focus store must never commit or roll back')

    def execute(self, sql, params=None):
        text = ' '.join(sql.split())
        self.sql.append(text)
        self._result, self.rowcount = [], -1
        p = [getattr(v, 'obj', v) for v in (params or [])]
        if text.startswith('SAVEPOINT '):
            self._sp[text.split()[1]] = deepcopy((self.rows, self.runs))
        elif text.startswith('ROLLBACK TO SAVEPOINT '):
            self.rows, self.runs = self._sp[text.split()[3]]
        elif text.startswith('RELEASE SAVEPOINT '):
            self._sp.pop(text.split()[2])
        elif text.startswith('SELECT to_regclass'):
            self._result = [(self.ready,)]
        elif text.startswith('INSERT INTO persona_focus_runs'):
            row = dict(zip(fs.RUN_COLUMNS, p))
            self.runs[(row['universe_id'], row['season'], row['rules_version'])] = row
        elif text.startswith('INSERT INTO persona_focus'):
            row = dict(zip(fs.FOCUS_COLUMNS, p))
            if row['focus_id'] in self.rows:
                raise AssertionError('duplicate key')
            if row['status'] == 'active' and any(
                    r['status'] == 'active' and (r['universe_id'], r['persona_id'], r['slot'])
                    == (row['universe_id'], row['persona_id'], row['slot']) for r in self.rows.values()):
                raise AssertionError('partial unique violated')
            self.rows[row['focus_id']] = row
        elif text.startswith('UPDATE persona_focus SET status'):
            new = text.split("status = '")[1].split("'")[0]
            r = self.rows.get(p[0])
            if r and r['status'] == 'active':
                r['status'] = new
                self.rowcount = 1
            else:
                self.rowcount = 0
        elif 'FROM persona_focus_runs' in text:
            row = self.runs.get(tuple(p))
            self._result = [tuple(row[c] for c in fs.RUN_COLUMNS)] if row else []
        elif 'FROM persona_focus WHERE focus_id' in text:
            r = self.rows.get(p[0])
            self._result = [tuple(r[c] for c in fs.FOCUS_COLUMNS)] if r else []
        elif 'FROM persona_focus WHERE universe_id' in text:
            rows = sorted((r for r in self.rows.values() if (r['universe_id'], r['persona_id']) == tuple(p)),
                          key=lambda r: (r['slot'], r['seq']))
            self._result = [tuple(r[c] for c in fs.FOCUS_COLUMNS) for r in rows]
        else:
            raise AssertionError('unexpected SQL: ' + text)

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


def frow(seq=0, slot='want', **over):
    r = dict(universe_id=U, persona_type='coach', persona_id='c1', slot=slot, seq=seq, text=f'focus {seq}',
             subjects=[], anchor={'team': 'BAL', 'seat': 'HC'}, until_season=None,
             source='profile' if seq == 0 else 'template', template_id=None, rules_version='focus-v1',
             valid_from_season=2026 + seq, valid_from_tick=24 if seq else 1, stale_causes=[], reason=None)
    r.update(over)
    return r


def test_migration_text():
    sql = MIGRATION.read_text()
    for needle in ('CREATE TABLE IF NOT EXISTS persona_focus (', 'CREATE TABLE IF NOT EXISTS persona_focus_runs',
                   "CHECK (persona_type IN ('coach','owner'))", "CHECK (slot IN ('want','fear'))",
                   "CHECK (source IN ('profile','template','jr'))",
                   "CHECK (status IN ('active','superseded','reverted'))", 'BETWEEN 1 AND 32',
                   'UNIQUE (universe_id, persona_id, slot, seq)', "WHERE status = 'active'",
                   'REFERENCES persona_core_profiles(persona_type, persona_id)',
                   'supersedes TEXT REFERENCES persona_focus(focus_id)',
                   "CHECK (status IN ('APPLIED','PREVIEW','FAILED'))", '2027_01_16_personality_core.sql',
                   '2027_01_24_personality_lifecycle.sql', 'DO $$', 'BEGIN;', 'COMMIT;'):
        assert needle in sql, needle
    assert 'INSERT INTO' not in sql


def test_tables_ready():
    assert fs.tables_ready(FakeCursor()) is True
    assert fs.tables_ready(FakeCursor(ready=False)) is False


def test_insert_replay_and_conflict():
    cur = FakeCursor()
    assert fs.insert_focus(cur, frow()) == 'inserted'
    assert fs.insert_focus(cur, frow()) == 'replayed'
    with pytest.raises(fs.FocusConflict):
        fs.insert_focus(cur, frow(text='other'))
    with pytest.raises(fs.FocusStoreError):
        fs.insert_focus(cur, frow(slot='hope'))
    with pytest.raises(fs.FocusStoreError):
        fs.insert_focus(cur, frow(focus_id='focus:wrong'))
    assert list(cur.rows) == [f'focus:{U}:c1:want:0']


def test_supersede_and_read_focus_as_of():
    cur = FakeCursor()
    fs.insert_focus(cur, frow())
    fs.insert_focus(cur, frow(slot='fear'))
    new = fs.supersede(cur, f'focus:{U}:c1:want:0', frow(seq=1, valid_from_season=2030))
    assert new['supersedes'] == f'focus:{U}:c1:want:0'
    assert cur.rows[f'focus:{U}:c1:want:0']['status'] == 'superseded'
    now = fs.read_focus(cur, U, 'c1')
    assert now['want']['seq'] == 1 and now['fear']['seq'] == 0
    assert fs.read_focus(cur, U, 'c1', as_of=(2029, 32))['want']['seq'] == 0
    assert fs.read_focus(cur, U, 'c1', as_of=(2030, 23))['want']['seq'] == 0
    assert fs.read_focus(cur, U, 'c1', as_of=(2030, 24))['want']['seq'] == 1
    assert fs.read_focus(cur, U, 'c1', as_of=(2025, 1)) == {'want': None, 'fear': None}
    with pytest.raises(fs.FocusStoreError):
        fs.supersede(cur, f'focus:{U}:c1:want:0', frow(seq=2))
    assert len(cur.rows) == 3   # the failed supersede left nothing behind
    assert fs.next_seq(cur, U, 'c1', 'want') == 2


def test_history_and_mark_reverted():
    cur = FakeCursor()
    fs.insert_focus(cur, frow())
    fs.supersede(cur, f'focus:{U}:c1:want:0', frow(seq=1))
    fs.mark_reverted(cur, f'focus:{U}:c1:want:1')
    assert [r['status'] for r in fs.history(cur, U, 'c1')] == ['superseded', 'reverted']
    with pytest.raises(fs.FocusStoreError):
        fs.mark_reverted(cur, f'focus:{U}:c1:want:1')


def test_runs():
    cur = FakeCursor()
    run = dict(universe_id=U, season=2030, rules_version='focus-v1', input_hash='h', counts={'rows': 2},
               status='APPLIED', error=None)
    fs.record_run(cur, run)
    assert fs.read_run(cur, U, 2030, 'focus-v1') == run
    fs.record_run(cur, dict(run, status='FAILED', error='boom'))
    assert fs.read_run(cur, U, 2030, 'focus-v1')['status'] == 'FAILED'
    assert fs.read_run(cur, U, 2031, 'focus-v1') is None
    with pytest.raises(fs.FocusStoreError):
        fs.record_run(cur, dict(run, status='DONE'))


@pytest.mark.integration
def test_scratch_database_roundtrip():
    dsn = os.environ.get('FOCUS_TEST_DSN')
    if not dsn:
        pytest.skip('FOCUS_TEST_DSN not set')
    import re
    dbname = re.search(r'dbname=(\S+)|/([^/?]+)(?:\?|$)', dsn)
    name = next(g for g in dbname.groups() if g) if dbname else ''
    if name in ('madden', 'postgres') or name.startswith('madden_'):
        pytest.skip('FOCUS_TEST_DSN must be a scratch database')
    import psycopg
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute('CREATE TABLE IF NOT EXISTS persona_core_profiles (persona_type VARCHAR(32), '
                    'persona_id VARCHAR(64), PRIMARY KEY (persona_type, persona_id))')
        cur.execute("INSERT INTO persona_core_profiles VALUES ('coach', 'c1') ON CONFLICT DO NOTHING")
        conn.commit()
        cur.execute(MIGRATION.read_text())
        conn.commit()
        cur.execute(MIGRATION.read_text())   # rerun is safe
        conn.commit()
        assert fs.tables_ready(cur)
        fs.insert_focus(cur, frow())
        assert fs.insert_focus(cur, frow()) == 'replayed'
        fs.supersede(cur, f'focus:{U}:c1:want:0', frow(seq=1))
        assert fs.read_focus(cur, U, 'c1')['want']['seq'] == 1
        assert fs.read_focus(cur, U, 'c1', as_of=(2026, 5))['want']['seq'] == 0
        with pytest.raises(Exception):
            cur.execute("INSERT INTO persona_focus (focus_id, universe_id, persona_type, persona_id, slot, seq, "
                        "text, source, rules_version, status, valid_from_season, valid_from_tick) VALUES "
                        "('x', %s, 'coach', 'c1', 'want', 9, 't', 'jr', 'focus-v1', 'active', 2030, 1)", (U,))
        conn.rollback()
