"""personality/relationship_store.py and 2027_01_27_personality_relationships.sql (PERSONALITY_RELATIONSHIPS_PLAN.md
§2.6, U4).

Unit half: a fake cursor that emulates the store's few statements in memory (savepoints included), for
validation exclusions, identity checks, replay after a newer head, stale heads, canonical/transport/source
conflicts, mid-batch rollback, no commit and historical cutoffs.

Scratch half: skipped unless RELATIONSHIPS_TEST_DSN names an explicitly provisioned, empty `mu_scratch_*`
database. It applies the persona, core and relationships migrations in a throwaway schema (the relationships
one twice), uses the real core store for identities, and rolls everything back (the concurrency case commits a
throwaway schema and drops it). Never provisioned from a live or archive database. Skips alone do not satisfy
persistence acceptance."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import threading
import uuid

import pytest

from personality import relationship_events as rev
from personality import relationship_store as rs

U = 'u-test'
REG = {'team:3': {'team_id': 3}}


def person(pid, role='player', universe=U):
    return {'kind': 'person', 'universe_id': universe, 'role': role, 'persona_id': pid}


TEAM = {'kind': 'team', 'universe_id': U, 'team_key': 'team:3'}


def env(sid='mu:tenure:2026:1:7', *, subject=None, tick=1, coverage='complete', refs=None, event_id=None,
        universe=U):
    subject = subject or person('p7', universe=universe)
    return {'schema_version': rev.RELATIONSHIP_SCHEMA_VERSION, 'universe_id': universe,
            'event_id': event_id or f'{sid}|{subject["persona_id"]}', 'source_event_id': sid, 'source_kind': 'test',
            'source_refs': refs if refs is not None else [{'namespace': 'mu-test', 'key': sid}],
            'kind': 'tenure_window', 'occurred_at': {'season': 2026, 'tick': tick}, 'subject': subject,
            'target': dict(TEAM, universe_id=universe), 'actor': None, 'basis': 'verified_simulation',
            'confidence': 'high', 'reviewer': None, 'evidence_scope': [], 'visibility': 'public',
            'attribution': {'responsible': [], 'reason_code': None, 'evidence_refs': []},
            'payload': {'team_key': 'team:3', 'start': {'season': 2026, 'tick': tick},
                        'end': {'season': 2026, 'tick': tick + 1}, 'coverage': coverage},
            'supersedes_source_event_id': None}


# ------------------------------------------------------------------------------------------- fake cursor ---

class FakeConn:
    def __init__(self, autocommit=False):
        self.autocommit = autocommit


class FakeCursor:
    """In-memory emulation of exactly the statements relationship_store issues."""

    def __init__(self, identities=(), autocommit=False, fail_on_event_insert=None):
        self.connection = FakeConn(autocommit)
        self.db = {'batches': [], 'events': [], 'sources': []}
        self.identities = set(identities)
        self.stack, self.sql, self.locks = [], [], []
        self.fail_on_event_insert = fail_on_event_insert
        self.inserted_events = 0
        self._rows = []

    def _jsonb(self, v):
        return json.loads(json.dumps(getattr(v, 'obj', v)))

    def execute(self, sql, params=()):
        s = ' '.join(sql.split())
        self.sql.append(s)
        if re.fullmatch(r'SAVEPOINT \w+', s):
            self.stack.append(deepcopy(self.db))
        elif s.startswith('ROLLBACK TO SAVEPOINT'):
            self.db = deepcopy(self.stack[-1])
        elif s.startswith('RELEASE SAVEPOINT'):
            self.stack.pop()
        elif 'pg_advisory_xact_lock' in s:
            self.locks.append(params[0])
            self._rows = [(None,)]
        elif s.startswith('SELECT COALESCE(MAX(head), 0)'):
            heads = [b['head'] for b in self.db['batches'] if b['universe_id'] == params[0]]
            self._rows = [(max(heads, default=0),)]
        elif 'FROM persona_core_profiles' in s:
            universe, roles, ids = params
            self._rows = [(r, p) for r, p in zip(roles, ids) if (universe, r, p) in self.identities]
        elif s.startswith('SELECT receipt FROM persona_relationship_batches'):
            self._rows = [(json.dumps(b['receipt']),) for b in self.db['batches']
                          if (b['universe_id'], b['batch_hash']) == tuple(params)]
        elif s.startswith('SELECT source_event_id, facet_key, event_id, payload_hash, head'):
            universe, sids, facets, eids, _ = params
            keys, ekeys = set(zip(sids, facets)), set(zip(eids, facets))
            self._rows = [(e['source_event_id'], e['facet_key'], e['event_id'], e['payload_hash'], e['head'])
                          for e in self.db['events'] if e['universe_id'] == universe and
                          ((e['source_event_id'], e['facet_key']) in keys or (e['event_id'], e['facet_key']) in ekeys)]
        elif s.startswith('SELECT namespace, external_key, source_event_id'):
            universe, ns, keys = params
            want = set(zip(ns, keys))
            self._rows = [(x['namespace'], x['external_key'], x['source_event_id']) for x in self.db['sources']
                          if x['universe_id'] == universe and (x['namespace'], x['external_key']) in want]
        elif s.startswith('INSERT INTO persona_relationship_batches'):
            u, head, expected, bh, receipt = params
            if any(b['universe_id'] == u and (b['head'] == head or b['batch_hash'] == bh) for b in self.db['batches']):
                raise RuntimeError('unique violation: batches')
            assert expected == head - 1
            self.db['batches'].append({'universe_id': u, 'head': head, 'expected_head': expected, 'batch_hash': bh,
                                       'receipt': self._jsonb(receipt)})
        elif s.startswith('INSERT INTO persona_relationship_events'):
            self.inserted_events += 1
            if self.fail_on_event_insert == self.inserted_events:
                raise RuntimeError('simulated failure mid-batch')
            u, sid, facet, eid, head, season, tick, version, digest, envelope = params
            if any(e['universe_id'] == u and ((e['source_event_id'], e['facet_key']) == (sid, facet)
                                              or (e['event_id'], e['facet_key']) == (eid, facet))
                   for e in self.db['events']):
                raise RuntimeError('unique violation: events')
            assert any(b['universe_id'] == u and b['head'] == head for b in self.db['batches'])
            self.db['events'].append({'universe_id': u, 'source_event_id': sid, 'facet_key': facet, 'event_id': eid,
                                      'head': head, 'season': season, 'tick': tick, 'payload_hash': digest,
                                      'envelope': self._jsonb(envelope)})
        elif s.startswith('INSERT INTO persona_relationship_sources'):
            u, ns, key, sid = params
            if any((x['universe_id'], x['namespace'], x['external_key']) == (u, ns, key) for x in self.db['sources']):
                raise RuntimeError('unique violation: sources')
            self.db['sources'].append({'universe_id': u, 'namespace': ns, 'external_key': key, 'source_event_id': sid})
        elif s.startswith('SELECT envelope, head FROM persona_relationship_events'):
            u, role, pid, season, tick, kt, _ = params
            rows = [e for e in self.db['events'] if e['universe_id'] == u
                    and e['envelope']['subject']['role'] == role and e['envelope']['subject']['persona_id'] == pid
                    and (e['season'], e['tick']) <= (season, tick) and (kt is None or e['head'] <= kt)]
            self._rows = [(json.dumps(e['envelope']), e['head']) for e in rows]
        else:
            raise AssertionError(f'unexpected SQL: {s}')

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


def known(*pids, universe=U, role='player'):
    return {(universe, role, p) for p in pids}


@pytest.fixture
def cur():
    return FakeCursor(identities=known('p7', 'p8', 'p9'))


# ------------------------------------------------------------------------------------------------ unit ---

def test_append_assigns_heads_and_reads_back_with_cutoffs(cur):
    assert rs.read_head(cur, U) == 0
    r1 = rs.append_events(cur, [env()], expected_head=0, team_registry=REG)
    assert (r1['status'], r1['head'], len(r1['appended'])) == ('appended', 1, 1)
    r2 = rs.append_events(cur, [env('mu:tenure:2026:2:7', tick=2)], expected_head=1, team_registry=REG)
    assert r2['head'] == 2 and rs.read_head(cur, U) == 2
    evs = rs.read_events(cur, subject=person('p7'), as_of={'season': 2026, 'tick': 5})
    assert [(e['source_event_id'], e['head']) for e in evs] == [('mu:tenure:2026:1:7', 1), ('mu:tenure:2026:2:7', 2)]
    assert rs.read_events(cur, subject=person('p7'), as_of={'season': 2026, 'tick': 5}, known_through=1) == evs[:1]
    assert rs.read_events(cur, subject=person('p7'), as_of={'season': 2026, 'tick': 1}) == evs[:1]
    assert rs.read_events(cur, subject=person('p8'), as_of={'season': 2026, 'tick': 5}) == []
    assert cur.locks == ['relationships:' + U] * 2
    assert not any(s.upper().startswith(('COMMIT', 'BEGIN')) for s in cur.sql)


def test_exact_replay_returns_the_original_receipt_even_after_a_newer_head(cur):
    first = rs.append_events(cur, [env()], expected_head=0, team_registry=REG)
    rs.append_events(cur, [env('mu:tenure:2026:2:7', tick=2)], expected_head=1, team_registry=REG)
    again = rs.append_events(cur, [env()], expected_head=0, team_registry=REG)
    assert again == dict(first, replayed=True)
    assert len(cur.db['events']) == 2


def test_stale_head_is_refused(cur):
    rs.append_events(cur, [env()], expected_head=0, team_registry=REG)
    with pytest.raises(rs.StaleHeadError):
        rs.append_events(cur, [env('mu:tenure:2026:2:7', tick=2)], expected_head=0, team_registry=REG)
    assert rs.read_head(cur, U) == 1


def test_same_facet_same_hash_is_a_noop_and_mixed_batches_get_per_facet_receipts(cur):
    rs.append_events(cur, [env()], expected_head=0, team_registry=REG)
    noop = rs.append_events(cur, [dict(env(), event_id='retransmitted-2')], expected_head=1,
                            team_registry=REG)
    assert noop['status'] == 'noop' and noop['head'] == 1 and noop['duplicates'][0]['head'] == 1
    mixed = rs.append_events(cur, [env(), env('mu:tenure:2026:3:7', tick=3)], expected_head=1, team_registry=REG)
    assert mixed['status'] == 'appended' and mixed['head'] == 2
    assert [d['source_event_id'] for d in mixed['duplicates']] == ['mu:tenure:2026:1:7']
    assert [a['source_event_id'] for a in mixed['appended']] == ['mu:tenure:2026:3:7']


def test_canonical_transport_and_source_conflicts_refuse_the_whole_batch(cur):
    rs.append_events(cur, [env()], expected_head=0, team_registry=REG)
    with pytest.raises(rs.RelationshipConflictError):
        rs.append_events(cur, [env(coverage='partial'), env('mu:tenure:2026:4:7', tick=4)], expected_head=1,
                         team_registry=REG)
    reuse = env('mu:tenure:2026:5:7', tick=5, event_id='mu:tenure:2026:1:7|p7')
    with pytest.raises(rs.RelationshipConflictError):
        rs.append_events(cur, [reuse], expected_head=1, team_registry=REG)
    alias = env('mu:tenure:2026:6:7', tick=6, refs=[{'namespace': 'mu-test', 'key': 'mu:tenure:2026:1:7'}])
    with pytest.raises(rs.RelationshipConflictError):
        rs.append_events(cur, [alias], expected_head=1, team_registry=REG)
    with pytest.raises(rs.RelationshipConflictError):
        rs.append_events(cur, [env('x', tick=7), env('x', tick=7, coverage='partial')], expected_head=1,
                         team_registry=REG)
    assert rs.read_head(cur, U) == 1 and len(cur.db['events']) == 1 and len(cur.db['sources']) == 1


def test_many_refs_map_to_one_occurrence(cur):
    a = env(refs=[{'namespace': 'roster', 'key': '3:2026:1'}, {'namespace': 'office', 'key': 'run:1'}])
    b = env(subject=person('p8'), event_id='b', refs=[{'namespace': 'roster', 'key': '3:2026:1'}])
    r = rs.append_events(cur, [a, b], expected_head=0, team_registry=REG)
    assert len(r['appended']) == 2 and len(cur.db['sources']) == 2


def test_invalid_and_unknown_identity_events_are_excluded_not_appended(cur):
    bad = dict(env('bad:1'), kind='friendship')
    stranger = env('mu:tenure:2026:1:99', subject=person('p99'))
    unknown_team = env('t:9')
    unknown_team['target'] = dict(TEAM, team_key='team:9')
    r = rs.append_events(cur, [env(), bad, stranger, unknown_team], expected_head=0, team_registry=REG)
    assert r['head'] == 1 and [a['source_event_id'] for a in r['appended']] == ['mu:tenure:2026:1:7']
    reasons = {x['source_event_id']: x['reason'].split(':')[0] for x in r['rejected']}
    assert reasons == {'bad:1': 'invalid_enum', 'mu:tenure:2026:1:99': 'unknown_identity', 't:9': 'unknown_team'}
    only_bad = rs.append_events(cur, [bad], expected_head=1, team_registry=REG)
    assert only_bad['status'] == 'rejected' and rs.read_head(cur, U) == 1
    other_universe = FakeCursor(identities=known('p7', universe='u2'))
    r = rs.append_events(other_universe, [env(universe='u2')], expected_head=0, team_registry=REG)
    assert r['head'] == 1 and rs.read_head(other_universe, U) == 0


def test_identity_is_checked_for_every_person_ref(cur):
    e = env()
    e['attribution']['responsible'] = [person('gm1', 'gm')]
    r = rs.append_events(cur, [e], expected_head=0, team_registry=REG)
    assert r['status'] == 'rejected' and 'gm:gm1' in r['rejected'][0]['reason']


def test_one_batch_one_universe_and_caller_head_is_rejected(cur):
    with pytest.raises(rs.RelationshipStoreError):
        rs.append_events(cur, [env(), env(universe='u2', subject=person('p7', universe='u2'))], expected_head=0,
                         team_registry=REG)
    r = rs.append_events(cur, [dict(env(), head=5)], expected_head=0, team_registry=REG)
    assert r['status'] == 'rejected'


def test_mid_batch_failure_rolls_back_everything():
    cur = FakeCursor(identities=known('p7'), fail_on_event_insert=2)
    with pytest.raises(RuntimeError, match='mid-batch'):
        rs.append_events(cur, [env(), env('mu:tenure:2026:2:7', tick=2)], expected_head=0, team_registry=REG)
    assert cur.db == {'batches': [], 'events': [], 'sources': []} and cur.stack == []


def test_autocommit_is_refused():
    with pytest.raises(rs.RelationshipStoreError):
        rs.append_events(FakeCursor(identities=known('p7'), autocommit=True), [env()], expected_head=0,
                         team_registry=REG)


def test_registry_change_is_a_new_batch_not_a_replay(cur):
    rs.append_events(cur, [env()], expected_head=0, team_registry=REG)
    moved = {'team:3': {'team_id': 3, 'city': 'Las Vegas'}}
    r = rs.append_events(cur, [env()], expected_head=1, team_registry=moved)
    assert r['status'] == 'noop' and not r['replayed']


# ------------------------------------------------------------------------------------------ scratch DB ---

DSN = os.environ.get('RELATIONSHIPS_TEST_DSN')
MIGRATIONS = Path(__file__).resolve().parents[1] / 'db' / 'migrations'
PREREQS = ('2027_01_09_personas.sql', '2027_01_16_personality_core.sql')
MIGRATION = '2027_01_27_personality_relationships.sql'
scratch = pytest.mark.skipif(not DSN, reason='RELATIONSHIPS_TEST_DSN (empty mu_scratch_* database) not set')


def _sql(name):
    return (MIGRATIONS / name).read_text(encoding='utf-8-sig')


def _connect():
    import psycopg
    conn = psycopg.connect(DSN)
    if not conn.info.dbname.startswith('mu_scratch_'):
        conn.close()
        pytest.fail('RELATIONSHIPS_TEST_DSN must name an explicitly provisioned mu_scratch_* database')
    return conn


def _schema(cursor, *, transactional=True):
    from psycopg import sql
    name = 'rel_test_' + uuid.uuid4().hex
    cursor.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(name)))
    cursor.execute(sql.SQL('SET LOCAL search_path TO {}' if transactional else 'SET search_path TO {}')
                   .format(sql.Identifier(name)))
    for prereq in PREREQS:
        cursor.execute(_sql(prereq).replace('BEGIN;', '').replace('COMMIT;', ''))
    return name


def _identities(cursor, *keys):
    from personality import store
    from personality.core import Identity
    out = []
    for key in keys:
        ident = Identity(U, 'player', key)
        store.ensure(cursor, ident, 'generated', 2026)
        out.append(person(ident.persona_id))
    return out


@pytest.fixture
def pg():
    conn = _connect()
    cursor = conn.cursor()
    try:
        _schema(cursor)
        body = _sql(MIGRATION).replace('BEGIN;', '').replace('COMMIT;', '')
        cursor.execute(body)
        cursor.execute(body)                                   # repeat application is a no-op
        yield cursor
    finally:
        conn.rollback()
        conn.close()


@scratch
def test_scratch_append_replay_conflict_and_cutoffs(pg):
    me, other = _identities(pg, 'birth-7', 'birth-8')
    r1 = rs.append_events(pg, [env(subject=me)], expected_head=0, team_registry=REG)
    assert r1['head'] == 1
    r2 = rs.append_events(pg, [env('mu:tenure:2026:2:7', tick=2, subject=me)], expected_head=1, team_registry=REG)
    assert rs.append_events(pg, [env(subject=me)], expected_head=0, team_registry=REG) == dict(r1, replayed=True)
    with pytest.raises(rs.StaleHeadError):
        rs.append_events(pg, [env('s:3', tick=3, subject=me)], expected_head=1, team_registry=REG)
    with pytest.raises(rs.RelationshipConflictError):
        rs.append_events(pg, [env(subject=me, coverage='partial')], expected_head=2, team_registry=REG)
    stranger = env('s:4', subject=person('not-a-profile'))
    assert rs.append_events(pg, [stranger], expected_head=2, team_registry=REG)['status'] == 'rejected'
    assert r2['head'] == rs.read_head(pg, U) == 2
    evs = rs.read_events(pg, subject=me, as_of={'season': 2026, 'tick': 9}, known_through=1)
    assert [e['source_event_id'] for e in evs] == ['mu:tenure:2026:1:7']
    assert rs.read_events(pg, subject=other, as_of={'season': 2026, 'tick': 9}) == []


@scratch
def test_scratch_mid_batch_failure_rolls_back(pg, monkeypatch):
    (me,) = _identities(pg, 'birth-7')
    real = rs.rev.facet_key
    calls = {'n': 0}

    def flaky(ev):
        calls['n'] += 1
        if calls['n'] == 2:
            raise RuntimeError('boom')
        return real(ev)
    monkeypatch.setattr(rs.rev, 'facet_key', flaky)
    with pytest.raises(RuntimeError):
        rs.append_events(pg, [env(subject=me), env('s:2', tick=2, subject=me)], expected_head=0, team_registry=REG)
    monkeypatch.setattr(rs.rev, 'facet_key', real)
    assert rs.read_head(pg, U) == 0


@scratch
def test_scratch_incompatible_table_fails_the_migration():
    import psycopg
    conn = _connect()
    cursor = conn.cursor()
    try:
        _schema(cursor)
        cursor.execute('CREATE TABLE persona_relationship_sources (universe_id TEXT PRIMARY KEY)')
        with pytest.raises(psycopg.errors.RaiseException):
            cursor.execute(_sql(MIGRATION).replace('BEGIN;', '').replace('COMMIT;', ''))
    finally:
        conn.rollback()
        conn.close()


@scratch
def test_scratch_concurrent_appends_serialize_on_the_head():
    from psycopg import sql
    setup = _connect()
    cursor = setup.cursor()
    name = _schema(cursor, transactional=False)
    cursor.execute(_sql(MIGRATION).replace('BEGIN;', '').replace('COMMIT;', ''))
    (me,) = _identities(cursor, 'birth-7')
    setup.commit()
    results, a, b = {}, _connect(), _connect()
    try:
        for c in (a, b):
            c.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(name)))
        ca, cb = a.cursor(), b.cursor()
        rs.append_events(ca, [env('s:a', subject=me)], expected_head=0, team_registry=REG)   # holds the lock

        def second():
            try:
                results['b'] = rs.append_events(cb, [env('s:b', tick=2, subject=me)], expected_head=0,
                                                team_registry=REG)
            except Exception as e:                       # noqa: BLE001
                results['b'] = e
        t = threading.Thread(target=second)
        t.start()
        t.join(0.5)
        assert t.is_alive()                              # blocked on the advisory lock
        a.commit()
        t.join(10)
        assert isinstance(results['b'], rs.StaleHeadError)
        b.rollback()
    finally:
        a.close()
        b.close()
        cursor.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(name)))
        setup.commit()
        setup.close()
