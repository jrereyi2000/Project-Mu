"""Relationship producer store (RELATIONSHIP_EVENT_PRODUCERS_PLAN.md §2.3–§2.4, §4).

Fake-cursor unit tests, offline. store.py is loaded by file path so these tests do not need the package's
__init__.py (owned by U1). personality.relationship_store is replaced by a fake module in sys.modules, and
personality.store.resolve_alias by a dict lookup. The opt-in scratch-schema cases at the bottom are marked
integration and skip unless RELATIONSHIP_PRODUCERS_TEST_DSN is set.
"""
from copy import deepcopy
import importlib.util
import json
import os
import pathlib
import sys
import types
import uuid

import pytest

from personality.core import Identity

ROOT = pathlib.Path(__file__).resolve().parent.parent
STORE_PATH = ROOT / 'engines' / 'relationship_producers' / 'store.py'
MIGRATION = ROOT / 'db' / 'migrations' / '2027_01_28_relationship_producers.sql'
UNIVERSE = 'universe-1'
LEAGUE = 77
REGISTRY = {'team:5': {'team_id': 5, 'abbreviation': 'CHI'}}


def _load_store():
    spec = importlib.util.spec_from_file_location('relationship_producer_store_under_test', STORE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store = _load_store()


# ---------------------------------------------------------------- fakes

class FakeCursor:
    """Just enough of the three producer tables, with savepoints, to sequence the store."""

    def __init__(self):
        self.runs, self.obs, self.pending = {}, {}, {}
        self.sql = []
        self.rowcount = -1
        self._result = []
        self._savepoints = {}
        self._clock = 0
        self.fail_on = None
        self.connection = types.SimpleNamespace(commit=self._forbidden, rollback=self._forbidden)

    def _forbidden(self, *a, **k):
        raise AssertionError('store must never commit or roll back the caller transaction')

    commit = _forbidden

    def _snapshot(self):
        return deepcopy((self.runs, self.obs, self.pending))

    def execute(self, sql, params=None):
        text = ' '.join(sql.split())
        self.sql.append(text)
        if self.fail_on and self.fail_on in text:
            raise RuntimeError('boom: ' + self.fail_on)
        self._result, self.rowcount = [], -1
        if text.startswith('SAVEPOINT '):
            self._savepoints[text.split()[1]] = self._snapshot()
        elif text.startswith('ROLLBACK TO SAVEPOINT '):
            self.runs, self.obs, self.pending = self._savepoints[text.split()[3]]
        elif text.startswith('RELEASE SAVEPOINT '):
            self._savepoints.pop(text.split()[2])
        elif text.startswith('SELECT') and 'FROM relationship_producer_runs' in text:
            row = self.runs.get(tuple(params))
            self._result = [row] if row else []
        elif text.startswith('INSERT INTO relationship_producer_runs'):
            cols = ('league_id', 'season', 'tick', 'rules_version', 'source', 'input_hash', 'registry_hash',
                    'event_hashes', 'counts', 'status', 'error')
            values = [getattr(p, 'obj', p) for p in params]
            row = dict(zip(cols, values))
            row['created_at'] = self._tick()
            self.runs[tuple(values[:4])] = tuple(deepcopy(row[c]) for c in store._RUN_COLUMNS)
        elif text.startswith('DELETE FROM relationship_roster_observations'):
            league, season, tick, keep = params
            for key in [k for k in self.obs if k[:3] == (league, season, tick) and k[3] not in keep]:
                del self.obs[key]
        elif text.startswith('INSERT INTO relationship_roster_observations'):
            league, season, tick, source, players, teams = params
            for p, t in zip(players, teams):
                self.obs[(league, season, tick, p)] = (t, source)
            self.rowcount = len(players)
        elif text.startswith('INSERT INTO relationship_pending_events'):
            league, sids, facets, aliases, envelopes, reasons, statuses = params
            new = 0
            for s, f, a, e, r, st in zip(sids, facets, aliases, envelopes, reasons, statuses):
                if (league, s, f) not in self.pending:
                    self.pending[(league, s, f)] = {'subject_alias': json.loads(a), 'envelope': json.loads(e),
                                                    'reason': r, 'status': st,
                                                    'created_at': self._tick(), 'appended_at': None}
                    new += 1
            self.rowcount = new
        elif text.startswith('SELECT') and 'FROM relationship_pending_events' in text:
            (league,) = params
            rows = [(k[1], k[2], deepcopy(v['envelope']), v['created_at'])
                    for k, v in self.pending.items() if k[0] == league and v['status'] == 'PENDING']
            self._result = list(reversed(rows))  # the store must order them itself
        elif text.startswith('UPDATE relationship_pending_events SET status=%s'):
            status, sids, facets, reasons, league = params
            for s, f, r in zip(sids, facets, reasons):
                row = self.pending[(league, s, f)]
                if row['status'] == 'PENDING':
                    row.update(status=status, reason=r)
        elif text.startswith('UPDATE relationship_pending_events'):
            league, sids, facets = params
            for s, f in zip(sids, facets):
                row = self.pending[(league, s, f)]
                if row['status'] == 'PENDING':
                    row.update(status='APPENDED', appended_at='now', reason=None)
        else:
            raise AssertionError('unexpected SQL: ' + text)

    def _tick(self):
        self._clock += 1
        return self._clock

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class FakeRelationshipStore(types.ModuleType):
    def __init__(self):
        super().__init__('personality.relationship_store')
        self.head = 0
        self.calls = []
        self.fail = False
        self.reject = {}        # source_event_id -> rejection reason (append_events excludes those envelopes)
        self.stored = set()     # (source_event_id, event_id) already in the ledger: duplicates

    def read_head(self, cur, universe_id):
        return self.head

    def append_events(self, cur, events, *, expected_head, team_registry):
        if self.fail:
            raise RuntimeError('append failed')
        assert expected_head == self.head
        self.calls.append({'events': deepcopy(events), 'expected_head': expected_head,
                           'team_registry': team_registry})
        rejected, appended, duplicates = [], [], []
        for ev in events:
            item = {'source_event_id': ev['source_event_id'], 'event_id': ev['event_id']}
            if ev['source_event_id'] in self.reject:
                rejected.append(dict(item, reason=self.reject[ev['source_event_id']]))
            elif (ev['source_event_id'], ev['event_id']) in self.stored:
                duplicates.append(item)
            else:
                appended.append(item)
                self.stored.add((ev['source_event_id'], ev['event_id']))
        if appended:
            self.head += 1
        status = 'appended' if appended else ('noop' if duplicates else 'rejected')
        return {'status': status, 'head': self.head, 'appended': appended, 'duplicates': duplicates,
                'rejected': rejected}


@pytest.fixture
def rel_store(monkeypatch):
    fake = FakeRelationshipStore()
    monkeypatch.setitem(sys.modules, 'personality.relationship_store', fake)
    return fake


@pytest.fixture
def aliases(monkeypatch):
    known = set()
    import personality.store as persona_store

    def resolve_alias(cur, universe_id, role, namespace, external_id):
        if (role, namespace, external_id) in known:
            return Identity(universe_id, role, f'birth-{external_id}')
        return None

    monkeypatch.setattr(persona_store, 'resolve_alias', resolve_alias)
    return known


def env(player_id, *, kind='tenure_window', tick=1, sid=None, payload=None, role='player', ns='db-player'):
    return {
        'schema_version': 'relationships-v1',
        'universe_id': None,
        'event_id': None,
        'source_event_id': sid or f'mu:tenure:2026:{tick}:{player_id}',
        'source_kind': 'mu_producer',
        'source_refs': [{'namespace': 'mu-producer', 'key': f'players:{player_id}'}],
        'kind': kind,
        'occurred_at': {'season': 2026, 'tick': tick},
        'subject': {'kind': 'person', 'universe_id': None, 'role': role, 'persona_id': None},
        'target': {'kind': 'team', 'universe_id': None, 'team_key': 'team:5'},
        'actor': None,
        'basis': 'verified_simulation',
        'confidence': 'high',
        'reviewer': None,
        'evidence_scope': [],
        'visibility': 'public',
        'attribution': {'responsible': [{'kind': 'team', 'universe_id': None, 'team_key': 'team:5'}],
                        'reason_code': None, 'evidence_refs': []},
        'payload': payload or {'team_key': 'team:5', 'start': {'season': 2026, 'tick': tick},
                               'end': {'season': 2026, 'tick': tick + 1}, 'coverage': 'complete',
                               'rule': 'tenure@producers-v1'},
        'supersedes_source_event_id': None,
        'subject_alias': {'role': role, 'namespace': ns, 'external_id': str(player_id)},
    }


# ---------------------------------------------------------------- append and hold

def test_resolved_subjects_append_in_one_call_and_unresolved_are_held(rel_store, aliases):
    aliases.add(('player', 'db-player', '1'))
    cur = FakeCursor()
    result = store.append_or_hold(cur, [env(1), env(2)], league_id=LEAGUE, universe_id=UNIVERSE,
                                  team_registry=REGISTRY)
    assert (result['appended'], result['held'], result['newly_held']) == (1, 1, 1)
    assert result['hold_reasons'] == {'subject_unresolved': 1}
    assert len(rel_store.calls) == 1
    call = rel_store.calls[0]
    assert call['expected_head'] == 0 and call['team_registry'] is REGISTRY
    (event,) = call['events']
    assert event['universe_id'] == UNIVERSE
    assert event['subject'] == {'kind': 'person', 'universe_id': UNIVERSE, 'role': 'player',
                                'persona_id': Identity(UNIVERSE, 'player', 'birth-1').persona_id}
    assert event['target']['universe_id'] == UNIVERSE
    assert event['attribution']['responsible'][0]['universe_id'] == UNIVERSE
    assert 'subject_alias' not in event
    (held,) = cur.pending.values()
    assert held['subject_alias'] == {'role': 'player', 'namespace': 'db-player', 'external_id': '2'}
    assert held['envelope']['subject']['persona_id'] is None
    assert set(result['event_hashes']) == {store._event_key(env(1)), store._event_key(env(2))}


def test_no_universe_holds_everything(rel_store, aliases):
    aliases.add(('player', 'db-player', '1'))
    cur = FakeCursor()
    result = store.append_or_hold(cur, [env(1)], league_id=LEAGUE, universe_id=None, team_registry=REGISTRY)
    assert (result['appended'], result['held']) == (0, 1)
    assert result['hold_reasons'] == {'universe_unregistered': 1}
    assert rel_store.calls == []


def test_missing_relationship_store_holds_everything(monkeypatch, aliases):
    aliases.add(('player', 'db-player', '1'))
    monkeypatch.setitem(sys.modules, 'personality.relationship_store', None)
    cur = FakeCursor()
    result = store.append_or_hold(cur, [env(1), env(2)], league_id=LEAGUE, universe_id=UNIVERSE,
                                  team_registry=REGISTRY)
    assert result['hold_reasons'] == {'relationship_store_missing': 2}
    assert len(cur.pending) == 2
    assert all(r['reason'] == 'relationship_store_missing' for r in cur.pending.values())


def test_holding_is_idempotent(rel_store, aliases):
    cur = FakeCursor()
    first = store.append_or_hold(cur, [env(2)], league_id=LEAGUE, universe_id=UNIVERSE, team_registry=REGISTRY)
    again = store.append_or_hold(cur, [env(2), env(2)], league_id=LEAGUE, universe_id=UNIVERSE,
                                 team_registry=REGISTRY)
    assert first['newly_held'] == 1
    assert (again['held'], again['newly_held'], again['already_held']) == (1, 0, 1)
    assert len(cur.pending) == 1


def test_conflicting_duplicate_key_is_refused(rel_store, aliases):
    other = env(2)
    other['visibility'] = 'private'
    with pytest.raises(ValueError, match='conflicting'):
        store.append_or_hold(FakeCursor(), [env(2), other], league_id=LEAGUE, universe_id=UNIVERSE,
                             team_registry=REGISTRY)


def test_hash_ignores_identity_fields():
    resolved = env(1)
    resolved['universe_id'] = UNIVERSE
    resolved['subject']['persona_id'] = 'p'
    resolved['event_id'] = 'transport'
    assert store._envelope_hash(resolved) == store._envelope_hash(env(1))


# ---------------------------------------------------------------- flush

def test_flush_appends_oldest_first_and_marks_appended(rel_store, aliases):
    cur = FakeCursor()
    store.append_or_hold(cur, [env(3, tick=1)], league_id=LEAGUE, universe_id=UNIVERSE, team_registry=REGISTRY)
    store.append_or_hold(cur, [env(3, tick=2), env(4, tick=2)], league_id=LEAGUE, universe_id=UNIVERSE,
                         team_registry=REGISTRY)
    assert rel_store.calls == []
    aliases.add(('player', 'db-player', '3'))
    result = store.flush_pending(cur, LEAGUE, UNIVERSE, REGISTRY)
    assert (result['appended'], result['pending']) == (2, 1)
    assert result['hold_reasons'] == {'subject_unresolved': 1}
    (call,) = rel_store.calls
    assert [e['source_event_id'] for e in call['events']] == ['mu:tenure:2026:1:3', 'mu:tenure:2026:2:3']
    statuses = {k[1]: v['status'] for k, v in cur.pending.items()}
    assert statuses == {'mu:tenure:2026:1:3': 'APPENDED', 'mu:tenure:2026:2:3': 'APPENDED',
                        'mu:tenure:2026:2:4': 'PENDING'}
    # A second flush finds nothing new to append for the resolved subject.
    again = store.flush_pending(cur, LEAGUE, UNIVERSE, REGISTRY)
    assert again['appended'] == 0 and len(rel_store.calls) == 1


def test_flush_without_universe_or_store_appends_nothing(monkeypatch, aliases):
    cur = FakeCursor()
    store.append_or_hold(cur, [env(3)], league_id=LEAGUE, universe_id=None, team_registry=REGISTRY)
    assert store.flush_pending(cur, LEAGUE, None, REGISTRY)['hold_reasons'] == {'universe_unregistered': 1}
    monkeypatch.setitem(sys.modules, 'personality.relationship_store', None)
    assert store.flush_pending(cur, LEAGUE, UNIVERSE, REGISTRY)['hold_reasons'] == {'relationship_store_missing': 1}


def test_flush_orders_correction_after_its_original_within_a_tick(rel_store, aliases):
    cur = FakeCursor()
    original = env(5)
    first = store.append_or_hold(cur, [original], league_id=LEAGUE, universe_id=UNIVERSE, team_registry=REGISTRY)
    changed = env(5, payload=dict(original['payload'], coverage='partial'))
    fixes = store.corrections(first['event_hashes'], [changed])
    store.append_or_hold(cur, fixes, league_id=LEAGUE, universe_id=UNIVERSE, team_registry=REGISTRY)
    aliases.add(('player', 'db-player', '5'))
    store.flush_pending(cur, LEAGUE, UNIVERSE, REGISTRY)
    kinds = [e['kind'] for e in rel_store.calls[0]['events']]
    assert kinds == ['tenure_window', 'correction']
    replacement = rel_store.calls[0]['events'][1]['payload']['replacement_event']
    assert replacement['subject']['persona_id'] == Identity(UNIVERSE, 'player', 'birth-5').persona_id
    assert replacement['universe_id'] == UNIVERSE and 'subject_alias' not in replacement


# ---------------------------------------------------------------- runs, observations and NOOP

def _run(**over):
    run = {'league_id': LEAGUE, 'season': 2026, 'tick': 3, 'rules_version': 'producers-v1', 'source': 'SAVE',
           'input_hash': 'h1', 'registry_hash': 'r1', 'event_hashes': {'k': {'hash': 'x', 'ref': {}}},
           'counts': {'appended': 1}, 'status': 'APPENDED'}
    run.update(over)
    return run


def test_record_and_read_run_round_trip_and_noop_on_same_key_and_hash(rel_store, aliases):
    cur = FakeCursor()
    assert store.read_run(cur, LEAGUE, 2026, 3, 'producers-v1') is None
    store.record_run(cur, _run())
    run = store.read_run(cur, LEAGUE, 2026, 3, 'producers-v1')
    assert run['input_hash'] == 'h1' and run['status'] == 'APPENDED' and run['error'] is None
    assert run['event_hashes'] == {'k': {'hash': 'x', 'ref': {}}}
    # Same key and hash: the caller sees the stored run and skips (NOOP); corrections() finds nothing to do
    # and re-appending identical envelopes holds nothing new.
    aliases.add(('player', 'db-player', '1'))
    first = store.append_or_hold(cur, [env(1), env(2)], league_id=LEAGUE, universe_id=UNIVERSE,
                                 team_registry=REGISTRY)
    assert store.corrections(first['event_hashes'], [env(1), env(2)]) == []
    again = store.append_or_hold(cur, [env(2)], league_id=LEAGUE, universe_id=UNIVERSE, team_registry=REGISTRY)
    key = store._event_key(env(2))
    assert again['newly_held'] == 0 and again['event_hashes'] == {key: first['event_hashes'][key]}
    store.record_run(cur, _run(status='NOOP', counts={}))
    assert store.read_run(cur, LEAGUE, 2026, 3, 'producers-v1')['status'] == 'NOOP'
    assert len(cur.runs) == 1


def test_record_run_validates():
    with pytest.raises(ValueError):
        store.record_run(FakeCursor(), _run(tick=33))
    with pytest.raises(ValueError):
        store.record_run(FakeCursor(), _run(status='DONE'))
    with pytest.raises(ValueError):
        store.record_run(FakeCursor(), _run(input_hash=None))


def test_save_observations_replaces_the_tick():
    cur = FakeCursor()
    assert store.save_observations(cur, LEAGUE, 2026, 1, [{'player_id': 1, 'team_id': 5},
                                                         {'player_id': 2, 'team_id': None}], 'SAVE') == 2
    assert store.save_observations(cur, LEAGUE, 2026, 1, [{'player_id': 1, 'team_id': 6}], 'CATCHUP') == 1
    assert cur.obs == {(LEAGUE, 2026, 1, 1): (6, 'CATCHUP')}
    with pytest.raises(ValueError):
        store.save_observations(cur, LEAGUE, 2026, 1, [{'player_id': 1}, {'player_id': 1}], 'SAVE')
    with pytest.raises(ValueError):
        store.save_observations(cur, LEAGUE, 2026, 1, [], 'LIVE')


# ---------------------------------------------------------------- corrections

def test_corrections_replace_changed_and_retract_vanished_events():
    a, b, c = env(1), env(2), env(3)
    previous = store._hash_entries([a, b, c])[0]
    b2 = env(2, payload=dict(b['payload'], coverage='partial'))
    fixes = store.corrections(previous, [a, b2])
    assert len(fixes) == 2
    by_reason = {f['payload']['reason']: f for f in fixes}
    change, retract = by_reason['inputs_changed'], by_reason['event_vanished']
    assert change['kind'] == 'correction' and change['supersedes_source_event_id'] == 'mu:tenure:2026:1:2'
    assert change['payload']['replacement_event']['payload']['coverage'] == 'partial'
    assert change['subject_alias'] == b['subject_alias']
    assert change['source_event_id'].startswith('mu:tenure:2026:1:2#correction:')
    assert retract['supersedes_source_event_id'] == 'mu:tenure:2026:1:3'
    assert retract['payload']['replacement_event'] is None
    assert retract['subject_alias'] == c['subject_alias'] and retract['occurred_at'] == c['occurred_at']
    assert retract['target']['team_key'] == 'team:5'
    # Deterministic.
    assert store.corrections(previous, [a, b2]) == fixes


def test_corrections_chain_and_hash_entries_track_the_key_in_effect():
    a = env(1)
    previous = store._hash_entries([a])[0]
    a2 = env(1, payload=dict(a['payload'], coverage='partial'))
    (fix,) = store.corrections(previous, [a2])
    after, retracted = store._hash_entries([fix])
    key = store._event_key(a)
    assert retracted == [] and list(after) == [key]
    assert after[key]['hash'] == store._envelope_hash(a2)
    assert store.corrections(after, [a2]) == []
    # A later change supersedes the active correction, not the original again.
    a3 = env(1, payload=dict(a['payload'], coverage='complete', rule='tenure@producers-v1b'))
    (fix2,) = store.corrections(after, [a3])
    assert fix2['supersedes_source_event_id'] == fix['source_event_id']
    # A retraction removes the key.
    (gone,) = store.corrections(after, [])
    assert gone['supersedes_source_event_id'] == fix['source_event_id']
    assert store._hash_entries([gone]) == ({}, [key])


def test_corrections_ignore_previous_corrections_and_need_a_ref_to_retract():
    assert store.corrections({'x#correction:1|player:db-player:1>team:5>correction': 'h'}, []) == []
    with pytest.raises(ValueError, match='no ref'):
        store.corrections({store._event_key(env(1)): 'bare-hash'}, [])
    # A bare hash still detects a change.
    (fix,) = store.corrections({store._event_key(env(1)): 'bare-hash'}, [env(1)])
    assert fix['payload']['reason'] == 'inputs_changed'


# ---------------------------------------------------------------- savepoints and commits

def test_append_failure_rolls_back_the_whole_batch(rel_store, aliases):
    aliases.add(('player', 'db-player', '1'))
    rel_store.fail = True
    cur = FakeCursor()
    with pytest.raises(RuntimeError, match='append failed'):
        store.append_or_hold(cur, [env(1), env(2)], league_id=LEAGUE, universe_id=UNIVERSE,
                             team_registry=REGISTRY)
    assert cur.pending == {}
    assert any(s.startswith('ROLLBACK TO SAVEPOINT relprod_') for s in cur.sql)


def test_observation_failure_mid_batch_restores_the_tick():
    cur = FakeCursor()
    store.save_observations(cur, LEAGUE, 2026, 1, [{'player_id': 1, 'team_id': 5}], 'SAVE')
    cur.fail_on = 'INSERT INTO relationship_roster_observations'
    with pytest.raises(RuntimeError):
        store.save_observations(cur, LEAGUE, 2026, 1, [{'player_id': 2, 'team_id': 5}], 'SAVE')
    assert cur.obs == {(LEAGUE, 2026, 1, 1): (5, 'SAVE')}


def test_flush_failure_leaves_rows_pending(rel_store, aliases):
    cur = FakeCursor()
    store.append_or_hold(cur, [env(1)], league_id=LEAGUE, universe_id=UNIVERSE, team_registry=REGISTRY)
    aliases.add(('player', 'db-player', '1'))
    rel_store.fail = True
    with pytest.raises(RuntimeError):
        store.flush_pending(cur, LEAGUE, UNIVERSE, REGISTRY)
    assert [v['status'] for v in cur.pending.values()] == ['PENDING']


def test_store_never_commits_or_connects(rel_store, aliases, monkeypatch):
    import psycopg

    def no_connect(*a, **k):
        raise AssertionError('store must never connect')

    monkeypatch.setattr(psycopg, 'connect', no_connect)
    aliases.add(('player', 'db-player', '1'))
    cur = FakeCursor()  # commit/rollback on the cursor and its connection raise AssertionError
    store.record_run(cur, _run())
    store.read_run(cur, LEAGUE, 2026, 3, 'producers-v1')
    store.save_observations(cur, LEAGUE, 2026, 3, [{'player_id': 1, 'team_id': 5}], 'SAVE')
    result = store.append_or_hold(cur, [env(1), env(2)], league_id=LEAGUE, universe_id=UNIVERSE,
                                  team_registry=REGISTRY)
    store.flush_pending(cur, LEAGUE, UNIVERSE, REGISTRY)
    store.corrections(result['event_hashes'], [env(1)])
    assert not any(s.split()[0] in ('COMMIT', 'ROLLBACK', 'BEGIN', 'END') for s in cur.sql)
    source = STORE_PATH.read_text()
    assert '.commit(' not in source and 'connect(' not in source


# ---------------------------------------------------------------- rejections (receipt['rejected'])

def test_rejected_envelopes_are_held_or_dropped_never_counted_as_appended(rel_store, aliases):
    for pid in ('1', '2', '3'):
        aliases.add(('player', 'db-player', pid))
    rel_store.reject = {'mu:tenure:2026:1:2': 'unknown_identity: player:p2',
                        'mu:tenure:2026:1:3': "unsupported_role: role 'coordinator' is not a core role (at subject)"}
    cur = FakeCursor()
    result = store.append_or_hold(cur, [env(1), env(2), env(3)], league_id=LEAGUE, universe_id=UNIVERSE,
                                  team_registry=REGISTRY)
    assert (result['appended'], result['rejected'], result['held'], result['dropped']) == (1, 2, 1, 1)
    assert result['hold_reasons'] == {
        'rejected: unknown_identity: player:p2': 1,
        "rejected: unsupported_role: role 'coordinator' is not a core role (at subject)": 1}
    rows = {k[1]: v for k, v in cur.pending.items()}
    assert rows['mu:tenure:2026:1:2']['status'] == 'PENDING'
    assert rows['mu:tenure:2026:1:3']['status'] == 'DROPPED'
    assert 'mu:tenure:2026:1:1' not in rows
    assert len(result['event_hashes']) == 3                  # every key stays tracked for corrections

    # the profile appears: a flush retries the identity rejection, never the dropped one
    rel_store.reject = {}
    flushed = store.flush_pending(cur, LEAGUE, UNIVERSE, REGISTRY)
    assert (flushed['appended'], flushed['pending'], flushed['rejected']) == (1, 0, 0)
    assert [e['source_event_id'] for e in rel_store.calls[-1]['events']] == ['mu:tenure:2026:1:2']
    assert {k[1]: v['status'] for k, v in cur.pending.items()} == {'mu:tenure:2026:1:2': 'APPENDED',
                                                                    'mu:tenure:2026:1:3': 'DROPPED'}


def test_flush_keeps_rows_the_store_still_rejects(rel_store, aliases):
    cur = FakeCursor()
    store.append_or_hold(cur, [env(4), env(5)], league_id=LEAGUE, universe_id=UNIVERSE, team_registry=REGISTRY)
    aliases.update({('player', 'db-player', '4'), ('player', 'db-player', '5')})
    rel_store.reject = {'mu:tenure:2026:1:4': 'unknown_identity: player:p4',
                        'mu:tenure:2026:1:5': 'invalid_ref: bad'}
    result = store.flush_pending(cur, LEAGUE, UNIVERSE, REGISTRY)
    assert (result['appended'], result['pending'], result['rejected'], result['dropped']) == (0, 1, 2, 1)
    rows = {k[1]: v for k, v in cur.pending.items()}
    assert rows['mu:tenure:2026:1:4']['status'] == 'PENDING'
    assert rows['mu:tenure:2026:1:4']['reason'] == 'rejected: unknown_identity: player:p4'
    assert rows['mu:tenure:2026:1:5']['status'] == 'DROPPED'


def test_duplicates_count_as_in_the_ledger(rel_store, aliases):
    aliases.add(('player', 'db-player', '1'))
    cur = FakeCursor()
    store.append_or_hold(cur, [env(1)], league_id=LEAGUE, universe_id=UNIVERSE, team_registry=REGISTRY)
    again = store.append_or_hold(cur, [env(1)], league_id=LEAGUE, universe_id=UNIVERSE, team_registry=REGISTRY)
    assert (again['appended'], again['duplicates'], again['rejected'], again['held']) == (1, 1, 0, 0)


def test_corrections_own_no_source_ref_so_a_chain_never_conflicts():
    first = env(6)
    second = env(6, payload=dict(first['payload'], coverage='partial'))
    entries, _ = store._hash_entries([first])
    (fix,) = store.corrections(entries, [second])
    assert fix['source_refs'] == []
    assert fix['attribution']['evidence_refs'] == ['mu-producer:correction:mu:tenure:2026:1:6']


# ---------------------------------------------------------------- schema text

def test_migration_and_init_db_carry_the_same_tables():
    migration = ' '.join(MIGRATION.read_text().split())
    init_db = ' '.join((ROOT / 'db' / 'init_db.sql').read_text().split())
    assert migration.startswith('-- ') and 'BEGIN;' in migration and migration.endswith('COMMIT;')
    assert 'RAISE EXCEPTION' in migration and 'INSERT INTO' not in migration
    for text in (migration, init_db):
        for table in ('relationship_producer_runs', 'relationship_roster_observations',
                      'relationship_pending_events'):
            assert f'CREATE TABLE IF NOT EXISTS {table} (' in text
        assert ('CREATE INDEX IF NOT EXISTS relationship_pending_open ON relationship_pending_events '
                '(league_id, status);') in text
        assert 'PRIMARY KEY (league_id, season, tick, rules_version)' in text
        assert 'PRIMARY KEY (league_id, source_event_id, facet_key)' in text


# ---------------------------------------------------------------- opt-in scratch schema (never run by default)

DSN = os.environ.get('RELATIONSHIP_PRODUCERS_TEST_DSN')


@pytest.mark.integration
@pytest.mark.skipif(not DSN, reason='set RELATIONSHIP_PRODUCERS_TEST_DSN to a scratch database to run')
def test_migration_reruns_cleanly_and_refuses_an_incompatible_table():
    import psycopg

    schema = 'relprod_' + uuid.uuid4().hex[:12]
    sql = MIGRATION.read_text()
    with psycopg.connect(DSN, autocommit=True) as conn:
        try:
            conn.execute(f'CREATE SCHEMA {schema}')
            conn.execute(f'SET search_path TO {schema}')
            conn.execute('CREATE TABLE players (id INT PRIMARY KEY)')
            conn.execute('CREATE TABLE teams (id INT PRIMARY KEY)')
            conn.execute(sql)
            conn.execute(sql)
            conn.execute('DROP TABLE relationship_roster_observations')
            conn.execute('CREATE TABLE relationship_roster_observations (league_id BIGINT PRIMARY KEY)')
            with pytest.raises(psycopg.errors.RaiseException, match='incompatible'):
                conn.execute(sql)
        finally:
            conn.execute('ROLLBACK')  # the refused rerun leaves its BEGIN open and aborted
            conn.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')
