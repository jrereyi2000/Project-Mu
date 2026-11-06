"""engines/retirement_store.py and 2027_01_26_retirement_policy.sql (PERSONALITY_RETIREMENT_PLAN.md §2.5, U4).

Unit half: a fake cursor that emulates exactly the store's statements in memory (savepoints, unique keys, the
one-PREPARED partial index and CAS updates): caller transaction ownership, replay after newer events, payload and
source collisions, CAS staleness, redraw refusal, partial-failure rollback, write receipts and acknowledgement.

Scratch half: skipped unless RETIREMENT_TEST_DSN names an explicitly provisioned, empty `mu_scratch_*` database. It
creates minimal `players` and `league_office_runs` tables in a throwaway schema, applies this migration twice, checks
an incompatible table fails loudly, and races two writers on one person. Never provisioned from live data:

  RETIREMENT_TEST_DSN=postgresql://.../mu_scratch_retirement \\
      .venv/bin/python -m pytest -m 'not integration' tests/test_retirement_store.py -q
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import threading
import uuid

import pytest

from engines import career_decision_state as cs
from engines import retirement_policy as rp
from engines import retirement_store as st

POL = rp.synthetic_policy()


def ctx(person='person:1', age=34, universe='u-test'):
    return rp.RetirementContext(universe, person, 1, 100, 2027, 2028, age, 'WR',
                                {'coverage': 'complete', 'employability': 0.5}, {'coverage': 'complete',
                                                                                 'burden': 0.1},
                                {'coverage': 'complete', 'has_contract': False, 'market_open': False},
                                {}, {}, (), {}, None, {})


def person_with(intent):
    for n in range(2000):
        d = rp.evaluate_annual(ctx(f'person:{n}'), POL)
        if d.intent == intent:
            return f'person:{n}', d
    raise AssertionError(intent)


def ev(kind, rev, src, actor=None, **payload):
    return {'event_id': str(uuid.uuid4()), 'source_event_id': src, 'kind': kind, 'expected_revision': rev,
            'payload': payload, 'actor': actor}


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
    def __init__(self, autocommit=False):
        self.connection = FakeConn(autocommit)
        self.db = {'states': {}, 'events': {}, 'receipts': {}}
        self.saves = {}
        self.result = []
        self.rowcount = 0
        self.locks = []
        self.fail_on = None

    def fetchone(self):
        return self.result[0] if self.result else None

    def _val(self, v):
        return v.obj if hasattr(v, 'obj') else v

    def execute(self, sql, params=()):
        sql = ' '.join(sql.split())
        p = [self._val(x) for x in params]
        if self.fail_on and self.fail_on in sql:
            raise Boom(sql)
        self.result, self.rowcount = [], 0
        if m := re.match(r'SAVEPOINT (\w+)', sql):
            self.saves[m[1]] = deepcopy(self.db)
        elif m := re.match(r'ROLLBACK TO SAVEPOINT (\w+)', sql):
            self.db = deepcopy(self.saves[m[1]])
        elif sql.startswith('RELEASE SAVEPOINT'):
            pass
        elif sql.startswith('SELECT pg_advisory_xact_lock'):
            self.locks.append(p[0])
            self.result = [(None,)]
        elif sql.startswith('SELECT universe_id, person_key') and 'WHERE decision_id' in sql:
            self.result = [self._srow(s) for s in self.db['states'].values() if s['decision_id'] == p[0]]
        elif sql.startswith('SELECT universe_id, person_key'):
            s = self.db['states'].get(tuple(p))
            self.result = [self._srow(s)] if s else []
        elif sql.startswith('INSERT INTO career_decision_states'):
            key = tuple(p[:3])
            if key in self.db['states'] or any(s['decision_id'] == p[6] for s in self.db['states'].values()):
                raise RuntimeError('unique violation: career_decision_states')
            self.db['states'][key] = dict(zip(('universe_id', 'person_key', 'earned_season', 'player_id', 'state',
                                               'revision', 'decision_id', 'reservation'), p))
            self.rowcount = 1
        elif sql.startswith('UPDATE career_decision_states'):
            for s in self.db['states'].values():
                if s['decision_id'] == p[3] and s['revision'] == p[4]:
                    s.update(state=p[0], revision=p[1], reservation=deepcopy(p[2]))
                    self.rowcount = 1
        elif sql.startswith('SELECT request_hash, receipt FROM retirement_policy_events'):
            e = self.db['events'].get(p[0])
            self.result = [(e['request_hash'], deepcopy(e['receipt']))] if e else []
        elif sql.startswith('SELECT event_id FROM retirement_policy_events'):
            self.result = [(e['event_id'],) for e in self.db['events'].values()
                           if e['decision_id'] == p[0] and e['source_event_id'] == p[1]]
        elif sql.startswith('SELECT draw_hex FROM retirement_policy_events'):
            self.result = [(e['draw_hex'],) for e in self.db['events'].values()
                           if e['decision_id'] == p[0] and e['draw_hex'] is not None][:1]
        elif sql.startswith('INSERT INTO retirement_policy_events'):
            cols = ('event_id', 'decision_id', 'checkpoint', 'event_kind', 'source_event_id', 'request_hash',
                    'input_snapshot', 'policy_snapshot', 'draw_hex', 'breakdown', 'before_state', 'after_state',
                    'receipt')
            row = dict(zip(cols, deepcopy(p)))
            if row['event_id'] in self.db['events'] or any(
                    e['decision_id'] == row['decision_id'] and e['source_event_id'] == row['source_event_id']
                    for e in self.db['events'].values()):
                raise RuntimeError('unique violation: retirement_policy_events')
            uuid.UUID(row['event_id'])
            self.db['events'][row['event_id']] = row
            self.rowcount = 1
        elif sql.startswith('SELECT decision_id FROM retirement_write_receipts'):
            r = self.db['receipts'].get(p[0])
            self.result = [(r['decision_id'],)] if r else []
        elif sql.startswith('SELECT operation_id, decision_id, status'):
            r = self.db['receipts'].get(p[0])
            keys = ('operation_id', 'decision_id', 'status', 'source_save_hash', 'output_hash', 'edits_hash',
                    'league_office_run', 'before_image', 'verification', 'request_hash', 'ack_hash')
            self.result = [tuple(deepcopy(r[k]) for k in keys)] if r else []
        elif sql.startswith('INSERT INTO retirement_write_receipts'):
            cols = ('operation_id', 'decision_id', 'status', 'source_save_hash', 'edits_hash', 'league_office_run',
                    'before_image', 'request_hash')
            row = dict(zip(cols, deepcopy(p)), output_hash=None, verification=None, ack_hash=None)
            if row['operation_id'] in self.db['receipts'] or any(
                    r['decision_id'] == row['decision_id'] and r['status'] == 'PREPARED'
                    for r in self.db['receipts'].values()):
                raise RuntimeError('unique violation: retirement_write_receipts')
            self.db['receipts'][row['operation_id']] = row
            self.rowcount = 1
        elif sql.startswith('UPDATE retirement_write_receipts'):
            r = self.db['receipts'].get(p[4])
            if r and r['status'] == 'PREPARED':
                r.update(status=p[0], output_hash=p[1], verification=deepcopy(p[2]), ack_hash=p[3])
                self.rowcount = 1
        else:
            raise AssertionError(f'unexpected SQL: {sql}')

    @staticmethod
    def _srow(s):
        return (s['universe_id'], s['person_key'], s['earned_season'], s['player_id'], s['state'], s['revision'],
                s['decision_id'], deepcopy(s['reservation']))


def review(cur, d, src='mu:offseason:2028:open', **kw):
    return st.record_review(cur, d, player_id=1, source_event_id=src, input_snapshot={'ctx': 'x'},
                            policy_snapshot=rp.policy_to_dict(POL), **kw)


# ------------------------------------------------------------------------------------------- unit tests ---

def test_requires_caller_transaction_and_never_commits():
    _, d = person_with('AVAILABLE')
    with pytest.raises(st.StoreError, match='non-autocommit'):
        review(FakeCursor(autocommit=True), d)
    cur = FakeCursor()
    review(cur, d)
    assert cur.connection.commits == 0 and cur.locks == [st.lock_key('u-test', d.person_key)]


def test_open_review_creates_state_and_event():
    _, d = person_with('RETIRE_INTENT')
    cur = FakeCursor()
    r = review(cur, d)
    assert r['outcome'] == 'APPLIED' and r['after']['state'] == 'RETIRE_INTENT' and not r['replayed']
    state = st.read_state(cur, 'u-test', d.person_key, 2027)
    assert state['revision'] == 1 and state['decision_id'] == d.decision_id
    (e,) = cur.db['events'].values()
    assert e['draw_hex'] == d.draw_hex and e['checkpoint'] == 'OPEN' and e['breakdown']['base'] == d.base


def test_blocked_identity_is_not_persisted():
    d = rp.evaluate_annual(ctx(person=''), POL)
    with pytest.raises(st.StoreError, match='needs'):
        review(FakeCursor(), d)


def test_replay_returns_original_even_after_newer_events():
    _, d = person_with('AVAILABLE')
    cur = FakeCursor()
    first = review(cur, d)
    st.reserve_contract(cur, d.decision_id, 'offer-1', 1, offer_kind='free_agency', acceptable=True, legal=True,
                        source_event_id='fa:wave1:offer-1')
    again = review(cur, d)
    assert again['replayed'] and again['after'] == first['after']
    assert st.read_state(cur, 'u-test', d.person_key, 2027)['state'] == 'SIGN_PENDING'


def test_changed_payload_under_same_event_conflicts():
    _, d = person_with('AVAILABLE')
    cur = FakeCursor()
    review(cur, d)
    with pytest.raises(st.StoreConflict, match='different payload'):
        st.record_review(cur, d, player_id=1, source_event_id='mu:offseason:2028:open',
                         input_snapshot={'ctx': 'changed'}, policy_snapshot={})


def test_redraw_refused_and_second_open_refused():
    _, d = person_with('AVAILABLE')
    cur = FakeCursor()
    review(cur, d)
    redrawn = rp.to_dict(d)
    redrawn.update(checkpoint='MARKET_CLOSE', draw_hex='00' * 8)
    with pytest.raises(st.StoreConflict, match='never redraw'):
        review(cur, redrawn, src='mu:market-close')
    with pytest.raises(st.StoreConflict, match='OPEN review already ran'):
        review(cur, d, src='mu:another-open')


def test_later_checkpoint_records_without_state_change():
    _, d = person_with('AVAILABLE')
    cur = FakeCursor()
    review(cur, d)
    mc = rp.evaluate_annual(ctx(d.person_key), POL, checkpoint='MARKET_CLOSE')
    r = review(cur, mc, src='mu:market-close')
    assert r['before'] == r['after'] and r['after']['state'] == 'AVAILABLE'
    with pytest.raises(st.StoreError, match='OPEN checkpoint'):
        review(FakeCursor(), mc, src='mu:market-close')


def test_source_event_reuse_conflicts():
    _, d = person_with('AVAILABLE')
    cur = FakeCursor()
    review(cur, d)
    st.apply_event(cur, d.decision_id, ev('RETURN_UNDER_CONTRACT', 1, 'src:x', contract_id='c-1'))
    with pytest.raises(st.StoreConflict, match='already recorded'):
        st.apply_event(cur, d.decision_id, ev('FLAG_CONFLICT', 2, 'src:x', reason='r'))


def test_cas_stale_and_rejected_write_nothing():
    _, d = person_with('AVAILABLE')
    cur = FakeCursor()
    review(cur, d)
    before = deepcopy(cur.db)
    with pytest.raises(st.StoreConflict, match='expected_revision'):
        st.reserve_contract(cur, d.decision_id, 'offer-1', 0, offer_kind='free_agency', acceptable=True,
                            legal=True, source_event_id='s1')
    with pytest.raises(st.TransitionRejected):
        st.prepare_retirement(cur, d.decision_id, 1, operation_id=uuid.uuid4(), source_event_id='s2')
    assert cur.db == before


def test_cas_race_loser_gets_conflict():
    _, d = person_with('AVAILABLE')
    cur = FakeCursor()
    review(cur, d)
    st.reserve_contract(cur, d.decision_id, 'offer-1', 1, offer_kind='free_agency', acceptable=True, legal=True,
                        source_event_id='writer-a')
    with pytest.raises(st.StoreConflict):                 # writer B read revision 1 before A committed
        st.reserve_contract(cur, d.decision_id, 'offer-2', 1, offer_kind='re_sign', acceptable=True, legal=True,
                            source_event_id='writer-b')


def test_partial_failure_rolls_back_the_savepoint():
    _, d = person_with('RETIRE_INTENT')
    cur = FakeCursor()
    cur.fail_on = 'INSERT INTO retirement_policy_events'
    with pytest.raises(Boom):
        review(cur, d)
    assert cur.db == {'states': {}, 'events': {}, 'receipts': {}}


def test_signing_and_retirement_exclusive_through_store():
    _, d = person_with('RETIRE_INTENT')
    cur = FakeCursor()
    review(cur, d)
    with pytest.raises(st.TransitionRejected):
        st.reserve_contract(cur, d.decision_id, 'offer-1', 1, offer_kind='free_agency', acceptable=True,
                            legal=True, source_event_id='s1')
    with pytest.raises(st.TransitionRejected, match='pending'):
        st.prepare_retirement(cur, d.decision_id, 1, operation_id=uuid.uuid4(), source_event_id='s2',
                              pending_moves=['extension'])


def _pending(cur):
    _, d = person_with('RETIRE_INTENT')
    review(cur, d)
    op = str(uuid.uuid4())
    st.prepare_retirement(cur, d.decision_id, 1, operation_id=op, source_event_id='prep')
    req = {'operation_id': op, 'decision_id': d.decision_id, 'source_save_hash': 'sha:in', 'edits_hash': 'sha:edits',
           'before_image': {'status': 'Retired'}, 'league_office_run': None}
    return d, op, req


def test_prepare_and_verified_acknowledgement():
    cur = FakeCursor()
    d, op, req = _pending(cur)
    w = st.prepare(cur, req, 2)
    assert w['status'] == 'PREPARED' and not w['replayed']
    assert st.prepare(cur, req, 2)['replayed']
    with pytest.raises(st.StoreConflict, match='different request'):
        st.prepare(cur, dict(req, edits_hash='other'), 2)
    with pytest.raises(st.StoreError, match='output_hash'):
        st.acknowledge(cur, op, {'status': 'VERIFIED', 'source_event_id': 'ack'})
    ack = {'status': 'VERIFIED', 'source_event_id': 'save:ack', 'output_hash': 'sha:out', 'save_receipt_id': 'rcpt',
           'verification': {'readback': 'ok'}}
    a = st.acknowledge(cur, op, ack)
    assert a['status'] == 'VERIFIED' and a['state']['state'] == 'RETIRED'
    assert cur.db['receipts'][op]['before_image'] == {'status': 'Retired'}      # prepared request immutable
    assert st.acknowledge(cur, op, ack)['replayed']
    with pytest.raises(st.StoreConflict, match='already acknowledged'):
        st.acknowledge(cur, op, dict(ack, status='FAILED'))


def test_failed_and_conflict_acknowledgements():
    cur = FakeCursor()
    d, op, req = _pending(cur)
    st.prepare(cur, req, 2)
    a = st.acknowledge(cur, op, {'status': 'FAILED', 'source_event_id': 'save:fail', 'verification': {'e': 1}})
    assert a['state']['state'] == 'RETIRE_INTENT'
    cur2 = FakeCursor()
    d2, op2, req2 = _pending(cur2)
    st.prepare(cur2, req2, 2)
    assert st.acknowledge(cur2, op2, {'status': 'CONFLICT', 'source_event_id': 's'})['state']['state'] == 'CONFLICT'


def test_prepare_needs_retire_pending_for_this_operation():
    cur = FakeCursor()
    d, op, req = _pending(cur)
    with pytest.raises(st.TransitionRejected):
        st.prepare(cur, dict(req, operation_id=str(uuid.uuid4())), 2)
    with pytest.raises(st.StoreConflict, match='expected_revision'):
        st.prepare(cur, req, 1)


def test_replay_reads_stored_receipt_and_event_ids_must_be_uuids():
    _, d = person_with('AVAILABLE')
    cur = FakeCursor()
    r = review(cur, d)
    assert st.replay(cur, r['event_id'])['after'] == r['after']
    assert st.replay(cur, str(uuid.uuid4())) is None
    with pytest.raises(st.StoreError, match='UUID'):
        st.apply_event(cur, d.decision_id, dict(ev('FLAG_CONFLICT', 1, 's'), event_id='not-a-uuid'))


def test_alias_change_does_not_create_second_decision():
    _, d = person_with('AVAILABLE')
    cur = FakeCursor()
    review(cur, d)
    again = rp.evaluate_annual(rp.RetirementContext(**dict(ctx(d.person_key).__dict__, player_id=555, pgid=9)),
                               POL)
    assert again.decision_id == d.decision_id
    assert len(cur.db['states']) == 1


def test_migration_text_contract():
    sql = (Path(__file__).resolve().parents[1] / 'db' / 'migrations' / '2027_01_26_retirement_policy.sql').read_text()
    for needle in ('BEGIN;', 'COMMIT;', 'career_decision_states', 'retirement_policy_events',
                   'retirement_write_receipts', 'UNIQUE (decision_id, source_event_id)',
                   'REFERENCES players (id)', 'REFERENCES league_office_runs (id)', 'already exists with an incompatible',
                   "status IN ('PREPARED', 'VERIFIED', 'FAILED', 'CONFLICT')"):
        assert needle in sql
    assert 'INSERT INTO' not in sql and 'DROP ' not in sql
    for state in cs.STATES:
        assert f"'{state}'" in sql
    init = (Path(__file__).resolve().parents[1] / 'db' / 'init_db.sql').read_text()
    body = sql[sql.index('CREATE TABLE IF NOT EXISTS career_decision_states'):sql.index('COMMIT;')].strip()
    assert body in init                                   # init_db mirrors the migration exactly


# ------------------------------------------------------------------------------------------ scratch DB ---

DSN = os.environ.get('RETIREMENT_TEST_DSN')
MIGRATION = Path(__file__).resolve().parents[1] / 'db' / 'migrations' / '2027_01_26_retirement_policy.sql'
scratch = pytest.mark.skipif(not DSN, reason='RETIREMENT_TEST_DSN (empty mu_scratch_* database) not set')


def _sql():
    return MIGRATION.read_text(encoding='utf-8-sig').replace('BEGIN;', '').replace('COMMIT;', '')


def _connect():
    import psycopg
    conn = psycopg.connect(DSN)
    if not conn.info.dbname.startswith('mu_scratch_'):
        conn.close()
        pytest.fail('RETIREMENT_TEST_DSN must name an explicitly provisioned mu_scratch_* database')
    return conn


def _schema(cursor, *, transactional=True):
    from psycopg import sql
    name = 'ret_test_' + uuid.uuid4().hex
    cursor.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(name)))
    cursor.execute(sql.SQL('SET LOCAL search_path TO {}' if transactional else 'SET search_path TO {}')
                   .format(sql.Identifier(name)))
    cursor.execute('CREATE TABLE players (id SERIAL PRIMARY KEY, player_name VARCHAR(255) NOT NULL)')
    cursor.execute('CREATE TABLE league_office_runs (id SERIAL PRIMARY KEY)')
    cursor.execute("INSERT INTO players (id, player_name) VALUES (1, 'Test Player')")
    return name


@pytest.fixture
def pg():
    conn = _connect()
    cursor = conn.cursor()
    try:
        _schema(cursor)
        cursor.execute(_sql())
        cursor.execute(_sql())                                     # repeat application is a no-op
        yield cursor
    finally:
        conn.rollback()
        conn.close()


@scratch
def test_scratch_review_reserve_replay(pg):
    _, d = person_with('AVAILABLE')
    review(pg, d)
    assert review(pg, d)['replayed']
    r = st.reserve_contract(pg, d.decision_id, 'offer-1', 1, offer_kind='free_agency', acceptable=True, legal=True,
                            source_event_id='fa:1')
    assert r['after']['state'] == 'SIGN_PENDING'
    pg.execute('SELECT count(*) FROM retirement_policy_events')
    assert pg.fetchone()[0] == 2


@scratch
def test_scratch_receipt_lifecycle(pg):
    d, op, req = _pending(pg)
    st.prepare(pg, req, 2)
    a = st.acknowledge(pg, op, {'status': 'VERIFIED', 'source_event_id': 'ack', 'output_hash': 'o',
                                'save_receipt_id': 'r', 'verification': {}})
    assert a['state']['state'] == 'RETIRED'


@scratch
def test_scratch_incompatible_table_fails_the_migration():
    import psycopg
    conn = _connect()
    cursor = conn.cursor()
    try:
        _schema(cursor)
        cursor.execute('CREATE TABLE career_decision_states (decision_id UUID PRIMARY KEY)')
        with pytest.raises(psycopg.errors.RaiseException):
            cursor.execute(_sql())
    finally:
        conn.rollback()
        conn.close()


@scratch
def test_scratch_concurrent_reservations_one_winner():
    from psycopg import sql
    setup = _connect()
    cursor = setup.cursor()
    name = _schema(cursor, transactional=False)
    cursor.execute(_sql())
    _, d = person_with('AVAILABLE')
    review(cursor, d)
    setup.commit()
    results, a, b = {}, _connect(), _connect()
    try:
        for conn in (a, b):
            conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(name)))
        ca, cb = a.cursor(), b.cursor()
        st.reserve_contract(ca, d.decision_id, 'offer-a', 1, offer_kind='free_agency', acceptable=True, legal=True,
                            source_event_id='a')

        def second():
            try:
                results['b'] = st.reserve_contract(cb, d.decision_id, 'offer-b', 1, offer_kind='re_sign',
                                                   acceptable=True, legal=True, source_event_id='b')
            except Exception as e:                           # noqa: BLE001
                results['b'] = e
        t = threading.Thread(target=second)
        t.start()
        t.join(0.5)
        assert t.is_alive()                                  # blocked on the advisory lock
        a.commit()
        t.join(10)
        assert isinstance(results['b'], st.StoreConflict)    # stale revision after A's commit
        b.rollback()
    finally:
        a.close()
        b.close()
        cursor.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))
        setup.commit()
        setup.close()
