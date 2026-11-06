"""Evolution run and candidate store, run_season replay and revert (PERSONALITY_EVOLUTION_PLAN.md §2.4-§2.5, §4).

Fake-cursor unit tests, offline. The two opt-in scratch-schema cases at the bottom are marked integration and skip
unless EVOLUTION_TEST_DSN names a scratch database (never madden or madden_*).
"""
from copy import deepcopy
import os
import pathlib
import types
import uuid

import pytest

from personality import core, evolution_run as er, evolution_store as es
from personality.core import Identity, initialize

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATION = ROOT / 'db' / 'migrations' / '2027_01_30_personality_evolution.sql'
UNIVERSE = 'mu:test-universe'
SEASON = 2029


class FakeCursor:
    """The two evolution tables plus persona_core_events receipts, with savepoints; commit is forbidden."""

    def __init__(self):
        self.runs, self.cands, self.receipts, self.births = {}, {}, {}, {}
        self.sql, self._result, self.rowcount, self._sp = [], [], -1, {}
        self.connection = types.SimpleNamespace(commit=self._forbidden, rollback=self._forbidden)

    def _forbidden(self, *a, **k):
        raise AssertionError('the evolution store must never commit or roll back')

    commit = rollback = _forbidden

    def execute(self, sql, params=None):
        text = ' '.join(sql.split())
        self.sql.append(text)
        self._result, self.rowcount = [], -1
        p = [getattr(v, 'obj', v) for v in (params or [])]
        if text.startswith('SAVEPOINT '):
            self._sp[text.split()[1]] = deepcopy((self.runs, self.cands))
        elif text.startswith('ROLLBACK TO SAVEPOINT '):
            self.runs, self.cands = self._sp[text.split()[3]]
        elif text.startswith('RELEASE SAVEPOINT '):
            self._sp.pop(text.split()[2])
        elif text.startswith('INSERT INTO persona_evolution_candidates'):
            row = dict(zip(es.CANDIDATE_COLUMNS, p))
            if row['candidate_id'] in self.cands:
                self.rowcount = 0
            else:
                self.cands[row['candidate_id']] = row
                self.rowcount = 1
        elif text.startswith('INSERT INTO persona_evolution_runs'):
            row = dict(zip(es.RUN_COLUMNS, p))
            self.runs[(row['universe_id'], row['season'], row['rules_version'])] = row
        elif 'FROM persona_evolution_runs' in text:
            row = self.runs.get(tuple(p))
            self._result = [tuple(row[c] for c in es.RUN_COLUMNS)] if row else []
        elif 'FROM persona_evolution_candidates WHERE candidate_id' in text:
            row = self.cands.get(p[0])
            self._result = [tuple(row[c] for c in es.CANDIDATE_COLUMNS)] if row else []
        elif 'FROM persona_evolution_candidates' in text:
            rows = sorted((r for r in self.cands.values() if (r['universe_id'], r['season']) == tuple(p)),
                          key=lambda r: (r['persona_type'], r['persona_id'], r['rule_id']))
            self._result = [tuple(r[c] for c in es.CANDIDATE_COLUMNS) for r in rows]
        elif text.startswith('UPDATE persona_evolution_candidates'):
            row = self.cands.get(p[1])
            if row and row['status'] == 'applied':
                row['status'], row['reason'] = 'reverted', p[0]
                self.rowcount = 1
            else:
                self.rowcount = 0
        elif 'FROM persona_core_events' in text:
            rec = self.receipts.get((p[0], p[1], p[2]))
            self._result = [(rec['before_state'], rec['after_state'])] if rec else []
        elif 'SELECT birth_key FROM persona_core_profiles' in text:
            key = self.births.get((p[1], p[2]))
            self._result = [(key,)] if key else []
        else:
            raise AssertionError('unexpected SQL: ' + text)

    def fetchone(self):
        return self._result[0] if self._result else None

    def fetchall(self):
        return list(self._result)


class FakeStore:
    """personality.store's read/change/override/history over profiles in memory, writing receipts to the cursor."""

    def __init__(self, cur, profiles):
        self.cur, self.profiles, self.history_rows, self.calls = cur, profiles, {}, []

    def read(self, cur, identity):
        p = self.profiles.get(identity.persona_id)
        return deepcopy(p)

    def change(self, cur, identity, event):
        self.calls.append(('change', event))
        before = self.profiles[identity.persona_id]
        after, audit = core.apply_change(before, event)
        self.profiles[identity.persona_id] = after
        cur.receipts[(identity.role, identity.persona_id, event['event_id'])] = dict(
            before_state=audit['before'], after_state=audit['after'])
        self.history_rows.setdefault(identity.persona_id, []).append(dict(
            kind='evolution', event_id=event['event_id'], season=event['season'], effective_tick=event['tick'],
            seq=len(self.calls), request=deepcopy(event)))
        return after

    def override(self, cur, identity, request):
        self.calls.append(('override', request))
        after, _ = core.apply_override(self.profiles[identity.persona_id], request)
        self.profiles[identity.persona_id] = after
        self.history_rows.setdefault(identity.persona_id, []).append(dict(
            kind='override', event_id='override:' + request['override_id'], season=request['season'],
            effective_tick=request['tick'], seq=len(self.calls), request=deepcopy(request)))
        return after

    def history(self, cur, identity):
        return list(self.history_rows.get(identity.persona_id, []))


def person(key, traits=('RING_CHASER',), role='player', **sliders):
    base = dict(temperament=5, sociability=5, confidence=5, openness=5, warmth=5, drive=5)
    base.update(sliders)
    ident = Identity(UNIVERSE, role, key)
    return ident, initialize(ident, 'real', 2026, sliders=base, traits=list(traits))


def facts_for(ident, profile, **kw):
    f = dict(universe_id=UNIVERSE, season=SEASON, persona_id=ident.persona_id, role=ident.role,
             birth_key=ident.birth_key, profile_view={'traits': list(profile['traits']),
                                                      'sliders': dict(profile['sliders'])},
             age=None, seasons_in_league=None, tenure_seasons_on_team=None, team_id=None, position_group=None,
             won_super_bowl=None, made_playoffs=None, losing_seasons_in_row=None, awards=[],
             unsatisfied_role_weeks=None, released_by_team=None, signed_elsewhere_after_release=None,
             injury_weeks_out=None, mentor_retired=None, coach_fired=None, coach_promoted_to_hc=None,
             coach_kept_on_hot_seat=None, source_event_ids={}, missing={})
    f.update(kw)
    return f


def league(n=40):
    """n players who all won the Super Bowl and hold RING_CHASER: some draw a change, most don't."""
    people, facts = {}, {}
    for i in range(n):
        ident, prof = person(f'p{i}')
        people[ident.persona_id] = prof
        facts[ident.persona_id] = facts_for(ident, prof, won_super_bowl=True, seasons_in_league=6,
                                            source_event_ids={'won_super_bowl': ['games:900']})
    return people, facts


# ------------------------------------------------------------------------------------------------ store ---

def row(cid, status='applied', **kw):
    r = dict(candidate_id=cid, universe_id=UNIVERSE, persona_type='player', persona_id='pid', season=SEASON,
             source='RULE', rule_id=cid, rules_version='evolution-v1', change={'kind': 'slider'}, chance=0.5,
             draw=0.1, source_event_ids=['s'], note='n', status=status, reason=None, core_event_id=None)
    r.update(kw)
    return r


def test_candidates_written_once_and_never_committed():
    cur = FakeCursor()
    assert es.record_candidates(cur, [row('a'), row('b', status='drawn_out')]) == 2
    assert es.record_candidates(cur, [row('a', status='blocked'), row('c', status='superseded')]) == 1
    assert cur.cands['a']['status'] == 'applied'
    assert any(s.startswith('SAVEPOINT') for s in cur.sql)
    with pytest.raises(es.EvolutionStoreError):
        es.record_candidates(cur, [row('d', status='chosen')])
    assert 'd' not in cur.cands


def test_run_rows_replace_and_read_back():
    cur = FakeCursor()
    es.record_run(cur, dict(universe_id=UNIVERSE, season=SEASON, rules_version='evolution-v1', input_hash='h1',
                            counts={'applied': 1}, status='APPLIED', error=None))
    assert es.read_run(cur, UNIVERSE, SEASON, 'evolution-v1')['input_hash'] == 'h1'
    assert es.read_run(cur, UNIVERSE, SEASON + 1, 'evolution-v1') is None
    with pytest.raises(es.EvolutionStoreError):
        es.record_run(cur, dict(universe_id=UNIVERSE, season=SEASON, rules_version='v', input_hash='',
                                counts={}, status='NOOP', error=None))


def test_mark_reverted_only_applied():
    cur = FakeCursor()
    es.record_candidates(cur, [row('a'), row('b', status='superseded')])
    es.mark_reverted(cur, 'a', {'override_id': 'revert:a', 'reason': 'too much'})
    assert cur.cands['a']['status'] == 'reverted' and 'revert:a' in cur.cands['a']['reason']
    with pytest.raises(es.EvolutionStoreError):
        es.mark_reverted(cur, 'b', {'override_id': 'x', 'reason': 'y'})
    with pytest.raises(es.EvolutionStoreError):
        es.mark_reverted(cur, 'a', {'override_id': 'x', 'reason': 'y'})


# --------------------------------------------------------------------------------------- run and revert ---

def test_run_season_applies_records_and_is_noop_on_the_same_hash():
    people, facts = league()
    cur = FakeCursor()
    fake = FakeStore(cur, people)
    report = er.run_season(cur, UNIVERSE, SEASON, facts=facts, persona_store=fake)
    applied = [c for c in report['candidates'] if c['status'] == 'applied']
    assert report['status'] == 'applied' and 0 < len(applied) < 30
    assert report['counts']['applied'] == len(applied)
    assert report['counts']['drawn_out'] == 40 - len(applied)
    assert len(cur.cands) == 40 and len(fake.calls) == len(applied)
    for c in applied:
        assert fake.profiles[c['persona_id']]['traits'] == ['LEGACY_BUILDER']
        assert c['core_event_id'] == f"evo:{UNIVERSE}:{c['persona_id']}:{SEASON}"
    # same facts (profiles changed since, which the hash ignores): NOOP, nothing new written or changed
    facts2 = {pid: dict(f, profile_view={'traits': fake.profiles[pid]['traits'], 'sliders': {}})
              for pid, f in facts.items()}
    again = er.run_season(cur, UNIVERSE, SEASON, facts=facts2, persona_store=fake)
    assert again['status'] == 'noop' and len(fake.calls) == len(applied)
    assert len(again['candidates']) == 40
    run = cur.runs[(UNIVERSE, SEASON, 'evolution-v1')]
    assert run['status'] == 'APPLIED' and run['input_hash'] == report['input_hash']


def test_dry_run_writes_nothing():
    people, facts = league(10)
    cur = FakeCursor()
    fake = FakeStore(cur, people)
    report = er.run_season(cur, UNIVERSE, SEASON, facts=facts, persona_store=fake, dry_run=True)
    assert report['status'] == 'dry_run' and fake.calls == [] and cur.cands == {} and cur.runs == {}
    assert report['counts']['would_apply'] == sum(c['status'] == 'chosen' for c in report['candidates'])


def test_store_refusal_marks_the_candidate_blocked():
    people, facts = league(30)
    cur = FakeCursor()
    fake = FakeStore(cur, people)

    def refuse(cur_, identity, event):
        raise core.ConflictError('source event already consumed by profile evolution')
    fake.change = refuse
    report = er.run_season(cur, UNIVERSE, SEASON, facts=facts, persona_store=fake)
    blocked = [c for c in report['candidates'] if c['status'] == 'blocked']
    assert blocked and all(c['reason'].startswith('store refused') for c in blocked)
    assert report['counts']['applied'] == 0


def test_revert_overrides_back_marks_and_drops_the_note():
    people, facts = league()
    cur = FakeCursor()
    fake = FakeStore(cur, people)
    report = er.run_season(cur, UNIVERSE, SEASON, facts=facts, persona_store=fake)
    c = next(c for c in report['candidates'] if c['status'] == 'applied')
    pid = c['persona_id']
    ident = Identity(UNIVERSE, 'player', facts[pid]['birth_key'])
    cur.births[('player', pid)] = ident.birth_key
    assert er.change_notes_for(cur, ident, SEASON, persona_store=fake) == [
        'Since the 2029 title he talks more about legacy than rings.']
    out = er.revert(cur, c['candidate_id'], 'too early for him', persona_store=fake)
    assert out['override_id'] == 'revert:' + c['candidate_id']
    assert fake.profiles[pid]['traits'] == ['RING_CHASER']
    kind, request = fake.calls[-1]
    assert kind == 'override' and request['actor'] == 'JR' and request['reverts'] == c['core_event_id']
    assert request['expected_revision'] == 1
    assert cur.cands[c['candidate_id']]['status'] == 'reverted'
    assert (('player', pid, c['core_event_id']) in cur.receipts)      # the receipt stays
    assert er.change_notes_for(cur, ident, SEASON, persona_store=fake) == []
    with pytest.raises(er.EvolutionRunError):
        er.revert(cur, c['candidate_id'], 'again', persona_store=fake)


def test_revert_refuses_a_stale_revision():
    people, facts = league()
    cur = FakeCursor()
    fake = FakeStore(cur, people)
    report = er.run_season(cur, UNIVERSE, SEASON, facts=facts, persona_store=fake)
    c = next(c for c in report['candidates'] if c['status'] == 'applied')
    pid = c['persona_id']
    cur.births[('player', pid)] = facts[pid]['birth_key']
    # a later season changed him again
    later = dict(event_id='later', source_event_id='later-src', season=SEASON + 1, tick=24,
                 expected_revision=1, rule_version='evolution-v1', reason='later', kind='slider',
                 slider='drive', step=1)
    fake.change(cur, Identity(UNIVERSE, 'player', facts[pid]['birth_key']), later)
    with pytest.raises(core.ConflictError):
        er.revert(cur, c['candidate_id'], 'undo', persona_store=fake)
    assert cur.cands[c['candidate_id']]['status'] == 'applied'


# ----------------------------------------------------------------------------- migration (text + scratch) ---

def test_migration_text_matches_the_definition():
    sql = MIGRATION.read_text()
    assert sql.count('BEGIN;') == 1 and sql.rstrip().endswith('COMMIT;')
    for needle in ('CREATE TABLE IF NOT EXISTS persona_evolution_runs',
                   "CHECK (status IN ('APPLIED','PREVIEW','FAILED'))",
                   'PRIMARY KEY (universe_id, season, rules_version)',
                   'CREATE TABLE IF NOT EXISTS persona_evolution_candidates',
                   "CHECK (source IN ('RULE','RELATIONSHIPS','DEVELOPMENT','OTHER'))",
                   "CHECK (status IN ('drawn_out','blocked','superseded','applied','reverted'))",
                   'UNIQUE (universe_id, persona_type, persona_id, season, rule_id)',
                   'chance NUMERIC(5,4)', 'source_event_ids TEXT[] NOT NULL',
                   'CREATE INDEX IF NOT EXISTS persona_evolution_candidates_season',
                   'incompatible definition'):
        assert needle in sql, needle
    assert 'INSERT INTO' not in sql      # seeds nothing


DSN = os.environ.get('EVOLUTION_TEST_DSN')


def _scratch_ok():
    if not DSN:
        return False
    name = er.database_name(DSN) or ''
    return name not in er.REFUSED_DATABASES and not name.startswith('madden')


@pytest.mark.integration
@pytest.mark.skipif(not _scratch_ok(), reason='set EVOLUTION_TEST_DSN to a scratch (non-madden) database to run')
def test_migration_reruns_cleanly_and_refuses_an_incompatible_table():
    import psycopg

    schema = 'evo_' + uuid.uuid4().hex[:12]
    sql = MIGRATION.read_text()
    with psycopg.connect(DSN, autocommit=True) as conn:
        try:
            conn.execute(f'CREATE SCHEMA {schema}')
            conn.execute(f'SET search_path TO {schema}')
            conn.execute(sql)
            conn.execute(sql)
            conn.execute('DROP TABLE persona_evolution_runs')
            conn.execute('CREATE TABLE persona_evolution_runs (universe_id TEXT PRIMARY KEY)')
            with pytest.raises(psycopg.errors.RaiseException, match='incompatible'):
                conn.execute(sql)
        finally:
            conn.execute('ROLLBACK')
            conn.execute(f'DROP SCHEMA IF EXISTS {schema} CASCADE')


@pytest.mark.integration
@pytest.mark.skipif(not _scratch_ok(), reason='set EVOLUTION_TEST_DSN to a scratch (non-madden) database to run')
def test_store_round_trip_on_scratch_schema():
    import psycopg

    schema = 'evo_' + uuid.uuid4().hex[:12]
    with psycopg.connect(DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA {schema}')
            cur.execute(f'SET search_path TO {schema}')
            cur.execute(MIGRATION.read_text().replace('BEGIN;', '').replace('COMMIT;', ''))
            assert es.record_candidates(cur, [row('a'), row('b', status='drawn_out')]) == 2
            assert es.record_candidates(cur, [row('a')]) == 0
            es.record_run(cur, dict(universe_id=UNIVERSE, season=SEASON, rules_version='evolution-v1',
                                    input_hash='h', counts={'x': 1}, status='APPLIED', error=None))
            assert es.read_run(cur, UNIVERSE, SEASON, 'evolution-v1')['counts'] == {'x': 1}
            assert [c['candidate_id'] for c in es.read_candidates(cur, UNIVERSE, SEASON)] == ['a', 'b']
            es.mark_reverted(cur, 'a', {'override_id': 'o', 'reason': 'r'})
            assert es.read_candidate(cur, 'a')['status'] == 'reverted'
        conn.rollback()
