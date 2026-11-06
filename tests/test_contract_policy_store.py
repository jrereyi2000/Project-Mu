"""engines/personality_contract_store.py and migration 2027_01_23 (PERSONALITY_CONTRACT_POLICY_PLAN.md §2.5, §4).

Fake-cursor unit tests, offline: insert-once replay, hash conflict, race reread, outbox dedupe, savepoint rollback,
no commits, durable snapshots, the switchboard read. The opt-in scratch-schema cases at the bottom are marked
integration and skip unless CONTRACT_POLICY_TEST_DSN names madden_test or a scratch database (never madden or any
other madden_* database); everything they write is rolled back or dropped.
"""
from copy import deepcopy
import os
import pathlib
import sys
import types
import uuid

import pytest

from engines import personality_contract_store as cs

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / 'db' / 'migrations' / '2027_01_23_contract_personality_policy.sql'
U = 'mu:test-universe'


def receipt(decision_id='offer:501', **kw):
    r = {'universe_id': U, 'decision_id': decision_id, 'season': 2031, 'week': 25, 'stage': 'FREE_AGENCY',
         'player_id': 77, 'persona_id': 'p-77', 'revision': 2, 'mode': 'shadow', 'policy_version': 'v1-trial',
         'snapshot': {'as_of': {'season': 2031, 'week': 25, 'stage': 'FREE_AGENCY'}, 'view': {'traits': ['LOYAL']},
                      'offers': [{'team_id': 3, 'total_value': 9_000_000}]},
         'result': {'offer_id': 501, 'status': 'ACCEPT'}}
    r.update(kw)
    return r


class FakeCursor:
    """The two policy tables with savepoints and ON CONFLICT DO NOTHING; commit/rollback are forbidden."""

    def __init__(self, race=None):
        self.decisions, self.events, self._sp = {}, {}, {}
        self.sql, self._result = [], []
        self.race = race                       # a row another transaction inserts just before ours
        self.fail_on = None
        self.connection = types.SimpleNamespace(commit=self._forbidden, rollback=self._forbidden)

    def _forbidden(self, *a, **k):
        raise AssertionError('the contract store must never commit or roll back')

    commit = rollback = _forbidden

    def execute(self, sql, params=None):
        text = ' '.join(sql.split())
        self.sql.append(text)
        self._result = []
        p = [getattr(v, 'obj', v) for v in (params or [])]
        if self.fail_on and self.fail_on in text:
            raise RuntimeError('simulated storage failure')
        if text.startswith('SAVEPOINT '):
            self._sp[text.split()[1]] = deepcopy((self.decisions, self.events))
        elif text.startswith('ROLLBACK TO SAVEPOINT '):
            self.decisions, self.events = self._sp[text.split()[3]]
        elif text.startswith('RELEASE SAVEPOINT '):
            self._sp.pop(text.split()[2])
        elif text.startswith('INSERT INTO contract_policy_decisions'):
            row = dict(zip(cs.DECISION_COLUMNS, p))
            key = (row['universe_id'], row['decision_id'])
            if self.race is not None:
                self.decisions[key] = dict(row, **self.race)
                self.race = None
            if key in self.decisions:
                self._result = []
            else:
                self.decisions[key] = row
                self._result = [(row['decision_id'],)]
        elif 'FROM contract_policy_decisions' in text:
            row = self.decisions.get(tuple(p))
            self._result = [tuple(row[c] for c in cs.DECISION_COLUMNS)] if row else []
        elif text.startswith('INSERT INTO contract_policy_event_outbox'):
            uid, eid, did, kind, payload = p
            if (uid, did) not in self.decisions:
                raise RuntimeError('FK violation: receipt missing')
            if (uid, eid) in self.events:
                self._result = []
            else:
                self.events[(uid, eid)] = {'decision_id': did, 'kind': kind, 'payload': payload}
                self._result = [(eid,)]
        elif text.startswith('SELECT payload FROM contract_policy_event_outbox'):
            e = self.events.get(tuple(p))
            self._result = [(e['payload'],)] if e else []
        else:
            raise AssertionError(f'unexpected SQL {text}')

    def fetchone(self):
        return self._result[0] if self._result else None


def test_record_once_then_replay_returns_original():
    cur = FakeCursor()
    first = cs.record_contract_decision(cur, receipt())
    again = cs.record_contract_decision(cur, receipt())
    assert first['status'] == 'recorded' and again['status'] == 'replayed'
    assert again['input_hash'] == first['input_hash'] == cs.canonical_hash(receipt()['snapshot'])
    assert len(cur.decisions) == 1


def test_changed_snapshot_conflicts():
    cur = FakeCursor()
    cs.record_contract_decision(cur, receipt())
    changed = receipt()
    changed['snapshot']['offers'][0]['total_value'] = 9_500_000
    with pytest.raises(cs.ContractStoreConflict):
        cs.record_contract_decision(cur, changed)


def test_concurrent_insert_race_resolves_by_reread_and_hash():
    same = cs.validate_receipt(receipt())
    cur = FakeCursor(race={'input_hash': same['input_hash']})
    assert cs.record_contract_decision(cur, receipt())['status'] == 'replayed'
    cur = FakeCursor(race={'input_hash': 'someone-else'})
    with pytest.raises(cs.ContractStoreConflict):
        cs.record_contract_decision(cur, receipt())


def test_supplied_input_hash_must_match_snapshot():
    with pytest.raises(cs.ContractStoreError):
        cs.validate_receipt(receipt(input_hash='nope'))


@pytest.mark.parametrize('bad', [{'mode': 'live'}, {'season': '2031'}, {'snapshot': []}, {'universe_id': ''},
                                 {'extra': 1}])
def test_malformed_receipt_rejected(bad):
    r = receipt()
    r.update(bad)
    with pytest.raises(cs.ContractStoreError):
        cs.record_contract_decision(FakeCursor(), r)


def test_snapshot_is_durable_after_profile_change():
    cur = FakeCursor()
    r = receipt()
    cs.record_contract_decision(cur, r)
    r['snapshot']['view']['traits'] = ['GRUDGE_HOLDER']     # the caller's copy (or the profile) changes later
    stored = cs.read_contract_decision(cur, U, 'offer:501')
    assert stored['snapshot']['view']['traits'] == ['LOYAL']


def fact(kind, **kw):
    f = {'kind': kind, 'team_id': 3, 'gm_persona_id': None, 'offer_id': 501,
         'terms': {'years': 2, 'total_value': 9_000_000, 'bonus': 0, 'guaranteed': 0},
         'market_reference': {'worth': 4_500_000}, 'response': 'ACCEPT', 'execution_id': None}
    f.update(kw)
    return f


def test_events_queue_once_with_stable_ids():
    cur = FakeCursor()
    r = cs.record_contract_decision(cur, receipt())
    ids = cs.enqueue_contract_events(cur, r, [fact('CONTRACT_OFFER_SUBMITTED'), fact('CONTRACT_RESPONSE_RECORDED')])
    assert ids == ['offer:501:CONTRACT_OFFER_SUBMITTED', 'offer:501:CONTRACT_RESPONSE_RECORDED']
    assert cs.enqueue_contract_events(cur, r, [fact('CONTRACT_OFFER_SUBMITTED'),
                                                fact('CONTRACT_RESPONSE_RECORDED')]) == ids
    payload = cur.events[(U, ids[1])]['payload']
    assert set(cs.EVENT_PAYLOAD_KEYS) <= set(payload) and payload['player_persona_id'] == 'p-77'
    with pytest.raises(cs.ContractStoreConflict):
        cs.enqueue_contract_events(cur, r, [fact('CONTRACT_OFFER_SUBMITTED', response='REJECT')])


@pytest.mark.parametrize('kind', ['LOWBALL', 'TRUST_DELTA', 'PROMISE_BREACH', 'GRUDGE', 'PREVIEW'])
def test_only_factual_event_kinds(kind):
    cur = FakeCursor()
    r = cs.record_contract_decision(cur, receipt())
    with pytest.raises(cs.ContractStoreError):
        cs.enqueue_contract_events(cur, r, [fact(kind)])


def test_outbox_failure_rolls_back_to_savepoint_and_never_commits():
    cur = FakeCursor()
    r = cs.record_contract_decision(cur, receipt())
    cur.fail_on = 'INSERT INTO contract_policy_event_outbox'
    with pytest.raises(RuntimeError):
        cs.enqueue_contract_events(cur, r, [fact('CONTRACT_OFFER_SUBMITTED')])
    assert cur.events == {} and len(cur.decisions) == 1
    assert not cur._sp                                     # every savepoint released


def test_event_without_receipt_fails():
    with pytest.raises(RuntimeError):
        cs.enqueue_contract_events(FakeCursor(), receipt(), [fact('CONTRACT_EXECUTED')])


# ------------------------------------------------------------- activation ----
def test_activation_reads_off_without_switchboard(monkeypatch):
    monkeypatch.setitem(sys.modules, 'personality.features', None)     # import fails like an absent module
    a = cs.read_contract_activation(object(), U)
    assert a['source'] == 'default' and a['config']['mode'] == 'off'


def test_activation_from_switchboard_combines_family_modes(monkeypatch):
    rows = {'contracts_ambition': {'mode': 'active', 'version': 'contracts-v1-jr', 'config': {'coefficient': 0.08}},
            'contracts_relationships': {'mode': 'shadow'}}
    fake = types.ModuleType('personality.features')
    fake.config = lambda cur, uid, name: rows.get(name)
    monkeypatch.setitem(sys.modules, 'personality.features', fake)
    import personality
    monkeypatch.setattr(personality, 'features', fake, raising=False)
    a = cs.read_contract_activation(object(), U)
    cfg = a['config']
    assert a['source'] == 'switchboard' and cfg['mode'] == 'active' and cfg['version'] == 'contracts-v1-jr'
    assert cfg['ambition_enabled'] is True and cfg['relationship_enabled'] is False
    assert cfg['coefficient'] == 0.08


def test_activation_rejects_bad_switchboard_mode(monkeypatch):
    fake = types.ModuleType('personality.features')
    fake.config = lambda cur, uid, name: {'mode': 'on'}
    monkeypatch.setitem(sys.modules, 'personality.features', fake)
    import personality
    monkeypatch.setattr(personality, 'features', fake, raising=False)
    with pytest.raises(cs.ContractStoreError):
        cs.read_contract_activation(object(), U)


# --------------------------------------------------------------- migration ----
def test_migration_is_additive_checked_and_seeds_nothing():
    sql = MIGRATION.read_text()
    assert sql.count('BEGIN;') == 1 and sql.rstrip().endswith('COMMIT;')
    for needle in ('CREATE TABLE IF NOT EXISTS contract_policy_decisions',
                   'CREATE TABLE IF NOT EXISTS contract_policy_event_outbox', 'PRIMARY KEY (universe_id, decision_id)',
                   'PRIMARY KEY (universe_id, event_id)',
                   'REFERENCES contract_policy_decisions (universe_id, decision_id)',
                   "CHECK (mode IN ('off', 'shadow', 'active'))", 'incompatible definition'):
        assert needle in sql, needle
    assert 'INSERT INTO' not in sql and 'contract_policy_activation' not in sql.split('BEGIN;')[1]
    assert 'DROP ' not in sql


# ------------------------------------------------------------- integration ----
DSN = os.environ.get('CONTRACT_POLICY_TEST_DSN')


def _dbname(dsn):
    for part in dsn.replace('?', ' ').replace('&', ' ').split():
        if part.startswith('dbname='):
            return part.split('=', 1)[1]
    tail = dsn.rsplit('/', 1)[-1] if '://' in dsn else ''
    return tail.split('?')[0] or None


def _scratch_ok():
    if not DSN:
        return False
    name = _dbname(DSN) or ''
    return name == 'madden_test' or (bool(name) and not name.startswith('madden'))


@pytest.mark.integration
@pytest.mark.skipif(not _scratch_ok(), reason='set CONTRACT_POLICY_TEST_DSN to madden_test or a scratch database')
def test_migration_twice_and_incompatible_schema_on_scratch():
    import psycopg

    schema = 'cpol_' + uuid.uuid4().hex[:12]
    sql = MIGRATION.read_text()
    with psycopg.connect(DSN, autocommit=True) as conn:
        try:
            conn.execute(f'CREATE SCHEMA {schema}')
            conn.execute(f'SET search_path TO {schema}')
            conn.execute(sql)
            conn.execute(sql)
            conn.execute('DROP TABLE contract_policy_event_outbox')
            conn.execute('CREATE TABLE contract_policy_event_outbox (universe_id TEXT PRIMARY KEY)')
            with pytest.raises(psycopg.errors.RaiseException, match='incompatible'):
                conn.execute(sql)
        finally:
            conn.execute('ROLLBACK')
            conn.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')


@pytest.mark.integration
@pytest.mark.skipif(not _scratch_ok(), reason='set CONTRACT_POLICY_TEST_DSN to madden_test or a scratch database')
def test_store_round_trip_and_rollback_on_scratch():
    import psycopg

    schema = 'cpol_' + uuid.uuid4().hex[:12]
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA {schema}')
            cur.execute(f'SET search_path TO {schema}')
            cur.execute(MIGRATION.read_text().replace('BEGIN;', '').replace('COMMIT;', ''))
            r = cs.record_contract_decision(cur, receipt())
            assert cs.record_contract_decision(cur, receipt())['status'] == 'replayed'
            assert cs.enqueue_contract_events(cur, r, [fact('CONTRACT_OFFER_SUBMITTED')])
            with pytest.raises(cs.ContractStoreConflict):
                cs.record_contract_decision(cur, receipt(snapshot={'as_of': {}, 'changed': True}))
            assert cs.read_contract_decision(cur, U, 'offer:501')['result'] == {'offer_id': 501, 'status': 'ACCEPT'}
        conn.rollback()
