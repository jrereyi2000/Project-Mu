"""engines/player_trade_request_store.py and 2027_01_25_player_trade_requests.sql (PERSONALITY_TRADE_REQUESTS_PLAN.md
§2.6, U2).

Unit half: a fake cursor that emulates exactly the store's statements in memory (savepoints, the one-ACTIVE partial
index and the unique keys included): annual uniqueness, checkpoint replay after later changes, conflicting replay,
renamed checkpoints, deferred reviews recorded without a slot, lifecycle updates with revision checks, binding and
universe isolation, mixed-rewind rejection, forced rollback after each write, no commit and modes.

Scratch half: skipped unless TRADE_REQUESTS_TEST_DSN names an explicitly provisioned, empty `mu_scratch_*`
database. It applies the persona and core migrations plus a minimal `teams` table in a throwaway schema, then this
migration twice; the incompatible-table case expects a loud failure; the concurrency case commits a throwaway
schema and drops it. Never provisioned from a live or archive database. Skips alone do not satisfy persistence
acceptance:

  TRADE_REQUESTS_TEST_DSN=postgresql://.../mu_scratch_trade_requests \\
      .venv/bin/python -m pytest -m 'not integration' tests/test_player_trade_request_store.py -q
"""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import threading
import uuid

import pytest

from engines import player_trade_request_store as st
from engines import player_trade_requests as tr

U = 'u-test'
POL = replace(tr.offline_illustration_policy(), version='jr-approved-test-v1')


def clock(season=2027, tick=10, *, checkpoint=None, source=None, universe=U, open_=True):
    return tr.RequestClock(universe, season, checkpoint or f'cp:{season}:{tick}', tr.clock_ordinal(season, tick),
                           f'IN_SEASON:{season}', 'IN_SEASON' if open_ else 'CLOSED', open_,
                           source or f'mu:tick:{season}:{tick}')


def inc(obs=4, state='OPEN', repairs=()):
    return tr.Incident('inc:lost_role:team:3:r1', ('r1',), 'LOST_ROLE', 3, tr.clock_ordinal(2027, 1),
                       tr.clock_ordinal(2027, 5), obs, 'MATERIAL', state, tuple(repairs), 'PRIVATE')


def ctx(pid, c, incidents=None, complete=None):
    view = {'status': 'stored', 'origin': 'generated', 'sliders': {}, 'traits': ['DIVA'], 'revision': 0,
            'persona_id': pid, 'view_version': 'views-v2'}
    return tr.RequestContext(1, pid, 3, view, 'rel:1', 'rx:1', 'a:1', c.ordinal,
                             c.ordinal if complete is None else complete,
                             tuple(incidents if incidents is not None else [inc()]),
                             tr.control_context(3, user_team_ids=[], suggestions_on=True))


def persona(want_request, season=2027):
    for n in range(10000):
        pid = f'p-{n}'
        d = tr.seeded_draw(U, pid, season)
        if (d < 0.15) == want_request and (want_request or d >= 0.5):
            return pid
    raise AssertionError


YES, NO = persona(True), persona(False)


# ------------------------------------------------------------------------------------------- fake cursor ---

class FakeConn:
    def __init__(self, autocommit=False):
        self.autocommit = autocommit
        self.commits = 0

    def commit(self):
        self.commits += 1


class Boom(RuntimeError):
    pass


class FakeCursor:
    """In-memory emulation of exactly the statements player_trade_request_store issues."""

    def __init__(self, profiles=None, autocommit=False, fail_on=None):
        self.connection = FakeConn(autocommit)
        self.db = {'decisions': [], 'requests': [], 'actions': []}
        self.profiles = profiles if profiles is not None else {YES: U, NO: U}
        self.stack, self.sql, self.locks = [], [], []
        self.fail_on = fail_on
        self._rows, self.rowcount = [], 0

    @staticmethod
    def _j(v):
        return None if v is None else json.loads(json.dumps(getattr(v, 'obj', v)))

    def _unique(self, table, row, keys):
        for k in keys:
            if any(all(r[c] == row[c] for c in k) for r in self.db[table]):
                raise RuntimeError(f'unique violation {table} {k}')

    def execute(self, sql, params=()):
        s = ' '.join(sql.split())
        self.sql.append(s)
        self._rows, self.rowcount = [], 0
        if self.fail_on and self.fail_on in s and not s.startswith('SELECT'):
            raise Boom(self.fail_on)
        if re.fullmatch(r'SAVEPOINT \w+', s):
            self.stack.append(deepcopy(self.db))
        elif s.startswith('ROLLBACK TO SAVEPOINT'):
            self.db = deepcopy(self.stack[-1])
        elif s.startswith('RELEASE SAVEPOINT'):
            self.stack.pop()
        elif 'pg_advisory_xact_lock' in s:
            self.locks.append(params[0])
            self._rows = [(None,)]
        elif 'FROM persona_core_profiles' in s:
            u = self.profiles.get(params[0])
            self._rows = [] if u is None else [(u,)]
        elif s.startswith('SELECT payload_hash, receipt FROM player_trade_request_actions'):
            self._rows = [(r['payload_hash'], r['receipt']) for r in self.db['actions']
                          if (r['universe_id'], r['persona_id'], r['event_id']) == params]
        elif s.startswith('SELECT receipt FROM player_trade_request_actions'):
            self._rows = [(r['receipt'],) for r in self.db['actions']
                          if (r['universe_id'], r['persona_id'], r['event_id']) == params]
        elif s.startswith('SELECT event_id FROM player_trade_request_actions'):
            self._rows = [(r['event_id'],) for r in self.db['actions']
                          if (r['universe_id'], r['persona_id'], r['source_event_id'], r['action_kind']) == params]
        elif s.startswith('SELECT state FROM player_trade_requests WHERE universe_id'):
            self._rows = [(r['state'],) for r in self.db['requests']
                          if (r['universe_id'], r['persona_id']) == params and r['status'] == 'ACTIVE']
        elif s.startswith('SELECT state FROM player_trade_requests WHERE request_id'):
            self._rows = [(r['state'],) for r in self.db['requests'] if r['request_id'] == params[0]]
        elif s.startswith('SELECT receipt FROM player_trade_request_decisions'):
            self._rows = [(r['receipt'],) for r in self.db['decisions']
                          if (r['universe_id'], r['persona_id'], r['season']) == params]
        elif s.startswith('INSERT INTO player_trade_request_decisions'):
            cols = ('universe_id', 'persona_id', 'season', 'decision_id', 'outcome', 'input_hash', 'policy_version',
                    'seed_algorithm', 'draw', 'receipt')
            row = dict(zip(cols, params))
            row['receipt'] = self._j(row['receipt'])
            assert 0 <= row['draw'] < 1 and row['outcome'] in ('REQUEST', 'NO_REQUEST')
            self._unique('decisions', row, [('universe_id', 'persona_id', 'season'), ('decision_id',)])
            self.db['decisions'].append(row)
        elif s.startswith('INSERT INTO player_trade_requests'):
            cols = ('request_id', 'decision_id', 'universe_id', 'persona_id', 'team_id', 'opened_season', 'revision',
                    'status', 'state')
            row = dict(zip(cols, params))
            row['state'] = self._j(row['state'])
            assert row['decision_id'] in {d['decision_id'] for d in self.db['decisions']}
            self._unique('requests', row, [('request_id',), ('decision_id',)])
            if row['status'] == 'ACTIVE' and any(r['status'] == 'ACTIVE' and r['universe_id'] == row['universe_id']
                                                 and r['persona_id'] == row['persona_id'] for r in self.db['requests']):
                raise RuntimeError('unique violation one_active')
            self.db['requests'].append(row)
        elif s.startswith('INSERT INTO player_trade_request_actions'):
            cols = ('universe_id', 'persona_id', 'event_id', 'source_event_id', 'request_id', 'season', 'window_id',
                    'checkpoint_id', 'action_kind', 'payload_hash', 'before_state', 'after_state', 'receipt')
            row = dict(zip(cols, params))
            for k in ('before_state', 'after_state', 'receipt'):
                row[k] = self._j(row[k])
            self._unique('actions', row, [('universe_id', 'persona_id', 'event_id'),
                                          ('universe_id', 'persona_id', 'source_event_id', 'action_kind'),
                                          ('universe_id', 'persona_id', 'season', 'window_id', 'checkpoint_id',
                                           'action_kind')])
            self.db['actions'].append(row)
        elif s.startswith('UPDATE player_trade_requests'):
            revision, status, state, rid, expected = params
            for r in self.db['requests']:
                if r['request_id'] == rid and r['revision'] == expected:
                    r.update(revision=revision, status=status, state=self._j(state))
                    self.rowcount = 1
        else:
            raise AssertionError(f'unexpected SQL: {s}')

    def fetchone(self):
        return self._rows[0] if self._rows else None


def opened(cur=None):
    cur = cur or FakeCursor()
    c = clock()
    r = st.record_decision(cur, ctx(YES, c), c, POL, mode='ENABLED')
    assert r['decision']['outcome'] == 'REQUEST'
    return cur, r


# ---------------------------------------------------------------------------------------------- writes ---

def test_request_writes_decision_episode_and_review_atomically():
    cur, r = opened()
    assert r['status'] == 'recorded' and r['event_id'] == 'review:2027:IN_SEASON:2027:cp:2027:10'
    assert len(cur.db['decisions']) == len(cur.db['requests']) == len(cur.db['actions']) == 1
    ep = st.read_active(cur, U, YES)
    assert ep.status == 'ACTIVE' and ep.revision == 0
    assert st.read_annual(cur, U, YES, 2027).outcome == 'REQUEST'
    assert cur.connection.commits == 0 and cur.locks == [st.lock_key(U, YES)]
    assert not cur.stack                                             # every savepoint released


def test_no_request_consumes_the_annual_slot_and_cannot_reroll():
    cur = FakeCursor()
    c = clock()
    r = st.record_decision(cur, ctx(NO, c), c, POL, mode='ENABLED')
    assert r['decision']['outcome'] == 'NO_REQUEST' and cur.db['requests'] == []
    later = clock(tick=20, checkpoint='renamed-checkpoint')
    r2 = st.record_decision(cur, ctx(NO, later), later, POL, mode='ENABLED')
    assert r2['decision']['outcome'] == 'NOOP' and len(cur.db['decisions']) == 1 and len(cur.db['actions']) == 2


def test_deferred_review_is_recorded_without_a_slot():
    cur = FakeCursor()
    c = clock()
    r = st.record_decision(cur, ctx(YES, c, complete=c.ordinal - 1), c, POL, mode='ENABLED')
    assert r['decision']['outcome'] == 'DEFERRED' and cur.db['decisions'] == [] and len(cur.db['actions']) == 1
    c2 = clock(tick=11)
    assert st.record_decision(cur, ctx(YES, c2), c2, POL, mode='ENABLED')['decision']['outcome'] == 'REQUEST'


def test_exact_replay_returns_the_original_even_after_later_changes():
    cur, r = opened()
    ep = st.read_active(cur, U, YES)
    c2 = clock(tick=12)
    act = tr.RequestAction('ev:disclose', 'src:pc', 0, 'DISCLOSE', 'SYSTEM',
                           {'basis': 'public_statement', 'disclosure_ref': 'pc:1'})
    st.apply_action(cur, ep.request_id, act, ctx(YES, c2), c2, POL)
    c = clock()
    again = st.record_decision(cur, ctx(YES, c), c, POL, mode='ENABLED')
    assert again['replayed'] and again['status'] == 'replayed' and again['decision'] == r['decision']
    replay = st.apply_action(cur, ep.request_id, act, ctx(YES, c2), c2, POL)
    assert replay['replayed'] and replay['transition']['after']['revision'] == 1
    assert st.read_receipt(cur, U, YES, 'ev:disclose')['transition']['after']['visibility'] == 'PUBLIC'


def test_conflicting_replay_fails():
    cur, _ = opened()
    c = clock()
    with pytest.raises(st.StoreConflict):
        st.record_decision(cur, ctx(YES, c, incidents=[inc(obs=5)]), c, POL, mode='ENABLED')
    other = clock(tick=13, source='mu:tick:2027:10')                   # reused source event, new checkpoint
    with pytest.raises(st.StoreConflict):
        st.record_decision(cur, ctx(YES, other), other, POL, mode='ENABLED')


def test_lifecycle_updates_with_revision_checks():
    cur, _ = opened()
    ep = st.read_active(cur, U, YES)
    c = clock(tick=14)
    st.apply_action(cur, ep.request_id, tr.RequestAction('ev:ack', 'src:gm', 0, 'ACKNOWLEDGE', 'GM',
                                                         {'response_ref': 'gm:1'}), ctx(YES, c), c, POL)
    with pytest.raises(st.StoreConflict, match='stale_revision'):
        st.apply_action(cur, ep.request_id, tr.RequestAction('ev:dec', 'src:gm2', 0, 'DECLINE', 'GM',
                                                             {'response_ref': 'gm:2'}), ctx(YES, c),
                        clock(tick=15), POL)
    c3 = clock(tick=16)
    cl = tr.RequestAction('ev:cut', 'src:cut', 1, 'CLOSE_EMPLOYMENT', 'SYSTEM',
                          {'reason': 'RELEASED', 'evidence_ref': 'tx:1', 'from_team_id': 3})
    r = st.apply_action(cur, ep.request_id, cl, ctx(YES, c3), c3, POL)
    assert r['transition']['after']['status'] == 'CLOSED_EMPLOYMENT'
    assert st.read_active(cur, U, YES) is None
    assert [a['action_kind'] for a in cur.db['actions']] == ['REVIEW', 'ACKNOWLEDGE', 'CLOSE_EMPLOYMENT']
    assert cur.db['actions'][-1]['before_state']['status'] == 'ACTIVE'      # history kept


def test_paused_repair_is_recorded_without_a_state_change():
    cur, _ = opened()
    ep = st.read_active(cur, U, YES)
    c = clock(tick=14)
    r = st.apply_action(cur, ep.request_id, tr.RequestAction('ev:rr', 'src:rr', 0, 'REVIEW_REPAIR', 'SYSTEM', {}),
                        ctx(YES, c), c, POL)
    assert r['transition']['outcome'] == 'PAUSED' and st.read_active(cur, U, YES).revision == 0
    assert not any(s.startswith('UPDATE') for s in cur.sql)


def test_binding_and_universe_isolation():
    cur = FakeCursor(profiles={YES: 'other-universe'})
    c = clock()
    with pytest.raises(st.BindingError):
        st.record_decision(cur, ctx(YES, c), c, POL, mode='ENABLED')
    with pytest.raises(st.BindingError):
        st.record_decision(FakeCursor(profiles={}), ctx(YES, c), c, POL, mode='ENABLED')
    cur, _ = opened()
    ep = st.read_active(cur, U, YES)
    cur.profiles[NO] = U
    c2 = clock(tick=14)
    with pytest.raises(st.BindingError):
        st.apply_action(cur, ep.request_id, tr.RequestAction('ev:x', 'src:x', 0, 'ACKNOWLEDGE', 'GM',
                                                             {'response_ref': 'g'}), ctx(NO, c2), c2, POL)


def test_mixed_rewind_is_rejected():
    cur, _ = opened()
    early = clock(tick=5)
    with pytest.raises(st.MixedRewindError):
        st.record_decision(cur, ctx(YES, early), early, POL, mode='ENABLED')
    cur2 = FakeCursor()
    c = clock()
    st.record_decision(cur2, ctx(NO, c), c, POL, mode='ENABLED')
    with pytest.raises(st.MixedRewindError):
        st.record_decision(cur2, ctx(NO, early), early, POL, mode='ENABLED')


@pytest.mark.parametrize('fail_on', ['INSERT INTO player_trade_request_decisions', 'INSERT INTO player_trade_requests',
                                     'INSERT INTO player_trade_request_actions'])
def test_forced_rollback_after_each_review_write(fail_on):
    cur = FakeCursor(fail_on=fail_on)
    c = clock()
    with pytest.raises(Boom):
        st.record_decision(cur, ctx(YES, c), c, POL, mode='ENABLED')
    assert cur.db == {'decisions': [], 'requests': [], 'actions': []} and not cur.stack


@pytest.mark.parametrize('fail_on', ['UPDATE player_trade_requests', 'INSERT INTO player_trade_request_actions'])
def test_forced_rollback_after_each_action_write(fail_on):
    cur, _ = opened()
    before = deepcopy(cur.db)
    ep = st.read_active(cur, U, YES)
    cur.fail_on = fail_on
    c = clock(tick=14)
    with pytest.raises(Boom):
        st.apply_action(cur, ep.request_id, tr.RequestAction('ev:d', 'src:d', 0, 'DISCLOSE', 'SYSTEM',
                                                             {'basis': 'public_statement', 'disclosure_ref': 'p'}),
                        ctx(YES, c), c, POL)
    assert cur.db == before


def test_modes_and_transaction_guard():
    c = clock()
    cur = FakeCursor()
    assert st.record_decision(cur, ctx(YES, c), c, POL, mode='DISABLED')['status'] == 'noop' and cur.sql == []
    with pytest.raises(st.StoreError, match='SHADOW'):
        st.record_decision(cur, ctx(YES, c), c, POL, mode='SHADOW')
    with pytest.raises(tr.TradeRequestError, match='policy_not_approved'):
        st.record_decision(cur, ctx(YES, c), c, tr.offline_illustration_policy(), mode='ENABLED')
    with pytest.raises(st.StoreError, match='non-autocommit'):
        st.record_decision(FakeCursor(autocommit=True), ctx(YES, c), c, POL, mode='ENABLED')


def test_migration_text_matches_the_plan():
    sql = (Path(__file__).resolve().parents[1] / 'db' / 'migrations' / '2027_01_25_player_trade_requests.sql').read_text()
    for needle in ('REFERENCES teams (id)', "WHERE status = 'ACTIVE'", 'CHECK (draw >= 0 AND draw < 1)',
                   "CHECK (persona_type = 'player')", 'REFERENCES persona_core_profiles (persona_type, persona_id)',
                   'UNIQUE (universe_id, persona_id, season, window_id, checkpoint_id, action_kind)',
                   'UNIQUE (universe_id, persona_id, source_event_id, action_kind)', 'BEGIN;', 'COMMIT;'):
        assert needle in sql
    assert 'INSERT INTO' not in sql and 'DROP ' not in sql.upper().replace('DROPS', '')


# ------------------------------------------------------------------------------------------ scratch DB ---

DSN = os.environ.get('TRADE_REQUESTS_TEST_DSN')
MIGRATIONS = Path(__file__).resolve().parents[1] / 'db' / 'migrations'
PREREQS = ('2027_01_09_personas.sql', '2027_01_16_personality_core.sql')
MIGRATION = '2027_01_25_player_trade_requests.sql'
scratch = pytest.mark.skipif(not DSN, reason='TRADE_REQUESTS_TEST_DSN (empty mu_scratch_* database) not set')


def _sql(name):
    return (MIGRATIONS / name).read_text(encoding='utf-8-sig').replace('BEGIN;', '').replace('COMMIT;', '')


def _connect():
    import psycopg
    conn = psycopg.connect(DSN)
    if not conn.info.dbname.startswith('mu_scratch_'):
        conn.close()
        pytest.fail('TRADE_REQUESTS_TEST_DSN must name an explicitly provisioned mu_scratch_* database')
    return conn


def _schema(cursor, *, transactional=True):
    from psycopg import sql
    name = 'tr_test_' + uuid.uuid4().hex
    cursor.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(name)))
    cursor.execute(sql.SQL('SET LOCAL search_path TO {}' if transactional else 'SET search_path TO {}')
                   .format(sql.Identifier(name)))
    for prereq in PREREQS:
        cursor.execute(_sql(prereq))
    cursor.execute('CREATE TABLE teams (id SERIAL PRIMARY KEY, name VARCHAR(255) UNIQUE NOT NULL)')
    cursor.execute("INSERT INTO teams (id, name) VALUES (3, 'Team Three'), (8, 'Team Eight')")
    return name


def _identity(cursor, key):
    from personality import store
    from personality.core import Identity
    ident = Identity(U, 'player', key)
    store.ensure(cursor, ident, 'generated', 2026)
    return ident.persona_id


def _requesting(cursor):
    for n in range(500):
        pid = _identity(cursor, f'birth-{n}')
        if tr.seeded_draw(U, pid, 2027) < 0.15:
            return pid
    raise AssertionError


@pytest.fixture
def pg():
    conn = _connect()
    cursor = conn.cursor()
    try:
        _schema(cursor)
        cursor.execute(_sql(MIGRATION))
        cursor.execute(_sql(MIGRATION))                          # repeat application is a no-op
        yield cursor
    finally:
        conn.rollback()
        conn.close()


@scratch
def test_scratch_decision_lifecycle_replay_and_one_active(pg):
    pid = _requesting(pg)
    c = clock()
    r = st.record_decision(pg, ctx(pid, c), c, POL, mode='ENABLED')
    assert r['decision']['outcome'] == 'REQUEST'
    assert st.record_decision(pg, ctx(pid, c), c, POL, mode='ENABLED')['replayed']
    ep = st.read_active(pg, U, pid)
    c2 = clock(tick=14)
    st.apply_action(pg, ep.request_id, tr.RequestAction('ev:t', 'src:t', 0, 'RECORD_TRADE', 'SYSTEM',
                                                        {'executed': True, 'transaction_id': 'tx', 'from_team_id': 3,
                                                         'to_team_id': 8}), ctx(pid, c2), c2, POL)
    assert st.read_active(pg, U, pid) is None
    pg.execute("SELECT count(*) FROM player_trade_request_actions WHERE persona_id = %s", (pid,))
    assert pg.fetchone()[0] == 2


@scratch
def test_scratch_forced_rollback_keeps_the_caller_transaction(pg, monkeypatch):
    pid = _requesting(pg)
    c = clock()
    real = st._insert_action

    def boom(*a, **k):
        raise Boom('after the episode insert')
    monkeypatch.setattr(st, '_insert_action', boom)
    with pytest.raises(Boom):
        st.record_decision(pg, ctx(pid, c), c, POL, mode='ENABLED')
    monkeypatch.setattr(st, '_insert_action', real)
    pg.execute('SELECT count(*) FROM player_trade_request_decisions')
    assert pg.fetchone()[0] == 0                                   # the caller's transaction is still usable


@scratch
def test_scratch_incompatible_table_fails_the_migration():
    import psycopg
    conn = _connect()
    cursor = conn.cursor()
    try:
        _schema(cursor)
        cursor.execute('CREATE TABLE player_trade_requests (request_id UUID PRIMARY KEY)')
        with pytest.raises(psycopg.errors.RaiseException):
            cursor.execute(_sql(MIGRATION))
    finally:
        conn.rollback()
        conn.close()


@scratch
def test_scratch_concurrent_annual_claims_serialize():
    from psycopg import sql
    setup = _connect()
    cursor = setup.cursor()
    name = _schema(cursor, transactional=False)
    cursor.execute(_sql(MIGRATION))
    pid = _requesting(cursor)
    setup.commit()
    results, a, b = {}, _connect(), _connect()
    try:
        for conn in (a, b):
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(name)))
        ca, cb = a.cursor(), b.cursor()
        c1, c2 = clock(tick=10), clock(tick=11)
        st.record_decision(ca, ctx(pid, c1), c1, POL, mode='ENABLED')            # holds the lock

        def second():
            try:
                results['b'] = st.record_decision(cb, ctx(pid, c2), c2, POL, mode='ENABLED')
            except Exception as e:                       # noqa: BLE001
                results['b'] = e
        t = threading.Thread(target=second)
        t.start()
        t.join(0.5)
        assert t.is_alive()                              # blocked on the advisory lock
        a.commit()
        t.join(10)
        assert results['b']['decision']['outcome'] == 'EXISTING'                  # no second draw
        b.rollback()
    finally:
        a.close()
        b.close()
        cursor.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))
        setup.commit()
        setup.close()
