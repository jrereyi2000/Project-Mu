"""personality/relationship_events.py (PERSONALITY_RELATIONSHIPS_PLAN.md §2.1-§2.2, U1). Synthetic dicts only:
schema/time validation, source facets and ref conflicts, cross-universe/self refs, relocation, correction
cycles/order, promise authority/deadline/unknown outcome, overlapping intervals and calendar gaps. No database."""
from copy import deepcopy

import pytest

from personality import relationship_events as rev

U = 'u-test'
REG = {'team:3': {'team_id': 3, 'abbreviation': 'OAK'}, 'team:5': {'team_id': 5, 'abbreviation': 'KC'}}
CAL = {2026: 32, 2027: 32, 2028: 32}


def person(pid, role='player', universe=U):
    return {'kind': 'person', 'universe_id': universe, 'role': role, 'persona_id': pid}


def team(key='team:3', universe=U):
    return {'kind': 'team', 'universe_id': universe, 'team_key': key}


def tp(season, tick):
    return {'season': season, 'tick': tick}


def env(kind='tenure_window', sid='mu:tenure:2026:1:7', *, subject=None, target=None, payload=None, at=(2026, 1),
        **over):
    e = {
        'schema_version': rev.RELATIONSHIP_SCHEMA_VERSION, 'universe_id': U, 'event_id': f'{sid}|e',
        'source_event_id': sid, 'source_kind': 'test', 'source_refs': [{'namespace': 'mu-test', 'key': sid}],
        'kind': kind, 'occurred_at': tp(*at), 'subject': subject or person('p7'),
        'target': target if target is not None else team(), 'actor': None, 'basis': 'verified_simulation',
        'confidence': 'high', 'reviewer': None, 'evidence_scope': ['roster'], 'visibility': 'public',
        'attribution': {'responsible': [], 'reason_code': None, 'evidence_refs': []},
        'payload': payload if payload is not None else {'team_key': 'team:3', 'start': tp(*at),
                                                        'end': tp(at[0], at[1] + 1), 'coverage': 'complete'},
        'supersedes_source_event_id': None,
    }
    e.update(over)
    return e


def promise(sid='inbox:promise:1:made', pid='inbox:1:1', at=(2026, 2), due=(2026, 6)):
    return env('promise_made', sid, target=person('coach1', 'coach'), at=at, payload={
        'promise_id': pid, 'issuer': person('coach1', 'coach'), 'beneficiary': person('p7'),
        'terms': {'kind': 'role', 'description': 'WR1 by week 6',
                  'criterion': {'type': 'depth_chart_slot', 'slot': 'WR', 'max_depth': 1}},
        'due_at': tp(*due), 'authority_ref': 'inbox_thread:1'}, visibility='private')


def resolved(resolution='broken', sid='inbox:promise:1:resolved', pid='inbox:1:1', at=(2026, 6), ack=None,
             result=None):
    return env('promise_resolved', sid, target=person('coach1', 'coach'), at=at, payload={
        'promise_id': pid, 'resolution': resolution,
        'criterion_result': result if result is not None else {'slot': 'WR', 'depth_at_due': 3},
        'outcome_refs': ['mu:role:2026:6:7'], 'beneficiary_ack_ref': ack})


def treatment(sid='mu:release:2026:9:7', outcome='negative', kind='dismissal', at=(2026, 9)):
    return env('treatment', sid, at=at, actor=team(), payload={
        'team_key': 'team:3', 'treatment_kind': kind, 'outcome': outcome, 'significance': 'meaningful',
        'outcome_refs': []}, attribution={'responsible': [team()], 'reason_code': 'RELEASE', 'evidence_refs': []})


def correction(sid, supersedes, replacement, at=(2026, 1), target='same'):
    base = replacement or env()
    return env('correction', sid, at=at, subject=base['subject'],
               target=base['target'] if target == 'same' else target,
               payload={'reason': 'inputs_changed', 'replacement_event': replacement},
               supersedes_source_event_id=supersedes)


def code(fn, *a, **k):
    with pytest.raises(rev.RelationshipEventError) as ei:
        fn(*a, **k)
    return ei.value.code


# ------------------------------------------------------------------------------------------- envelopes ---

def test_valid_envelope_normalizes_to_a_copy_and_keeps_extra_payload_keys():
    e = env()
    e['payload']['rule'] = 'tenure_window@producers-v1'
    out = rev.validate_event(e, team_registry=REG)
    assert out == e and out is not e
    out['payload']['coverage'] = 'partial'
    assert e['payload']['coverage'] == 'complete'


@pytest.mark.parametrize('mutate,expected', [
    (lambda e: e.update(schema_version='relationships-v0'), 'schema_version'),
    (lambda e: e.update(extra=1), 'unknown_field'),
    (lambda e: e.pop('visibility'), 'missing_field'),
    (lambda e: e.update(occurred_at={'season': 2026, 'tick': -1}), 'invalid_int'),
    (lambda e: e.update(occurred_at={'season': 2026, 'week': 1}), 'invalid_time'),
    (lambda e: e.update(occurred_at={'season': '2026', 'tick': 1}), 'invalid_int'),
    (lambda e: e.update(kind='friendship'), 'invalid_enum'),
    (lambda e: e.update(basis='reviewed_evidence'), 'missing_reviewer'),
    (lambda e: e.update(confidence='certain'), 'invalid_enum'),
    (lambda e: e.update(universe_id=' u '), 'invalid_text'),
    (lambda e: e.update(source_refs=[{'namespace': 'a', 'key': 'b'}, {'namespace': 'a', 'key': 'b'}]),
     'invalid_source_ref'),
    (lambda e: e.update(supersedes_source_event_id='x'), 'invalid_field'),
    (lambda e: e['payload'].update(start=tp(2026, 2), end=tp(2026, 2)), 'invalid_interval'),
    (lambda e: e['payload'].update(coverage='most'), 'invalid_enum'),
    (lambda e: e['payload'].update(score=float('nan')), 'invalid_number'),
])
def test_schema_and_time_rejections(mutate, expected):
    e = env()
    mutate(e)
    assert code(rev.validate_event, e, team_registry=REG) == expected


def test_cross_universe_self_and_unsupported_role_refs_are_rejected():
    assert code(rev.validate_event, env(target=team(universe='other')), team_registry=REG) == 'cross_universe'
    e = env('meaningful_interaction', 'inbox:msg:1', target=person('p7'), payload={
        'relationship_kind': 'support', 'outcome': 'positive', 'significance': 'routine', 'opportunity_ref': 'm:1'})
    assert code(rev.validate_event, e, team_registry=REG) == 'self_edge'
    e['target'] = person('p7', 'coach')                      # same persona id, other role: another person
    rev.validate_event(e, team_registry=REG)
    assert code(rev.validate_event, env(subject=person('c1', 'coordinator')), team_registry=REG) == 'unsupported_role'
    assert code(rev.validate_event, env(target={'kind': 'team', 'universe_id': U, 'team_key': 'OAK'}),
                team_registry=REG) == 'unknown_team'
    bad = env()
    bad['attribution']['responsible'] = [person('gm', 'gm', universe='other')]
    assert code(rev.validate_event, bad, team_registry=REG) == 'cross_universe'


def test_relocation_keeps_the_team_key():
    moved = {'team:3': {'team_id': 3, 'abbreviation': 'LV', 'city': 'Las Vegas'}, 'team:5': REG['team:5']}
    a = rev.validate_event(env(), team_registry=REG)
    b = rev.validate_event(env(), team_registry=moved)
    assert a == b and rev.canonical_payload_hash(a) == rev.canonical_payload_hash(b)
    assert rev.registry_hash(REG) != rev.registry_hash(moved)


def test_hash_ignores_transport_id_and_head_but_not_semantics():
    a = env()
    b = dict(env(), event_id='other-transport', head=4)
    assert rev.canonical_payload_hash(a) == rev.canonical_payload_hash(b)
    c = env(visibility='private')
    assert rev.canonical_payload_hash(a) != rev.canonical_payload_hash(c)


def test_facets_separate_subjects_targets_and_kinds_of_one_occurrence():
    a = env('situation', 'mu:result:2026:3:win-streak:5', subject=person('p1'), payload={
        'situation_kind': 'team_result', 'state': 'positive', 'valid_until': None, 'outcome_refs': []})
    b = dict(deepcopy(a), subject=person('p2'))
    assert rev.facet_key(a) != rev.facet_key(b)
    assert rev.dedupe_key(a)[:2] == rev.dedupe_key(b)[:2]
    assert rev.facet_key(a) == rev.facet_key(deepcopy(a))


@pytest.mark.parametrize('kind,payload,expected', [
    ('role_window', {'team_key': 'team:3', 'start': tp(2026, 1), 'end': tp(2026, 2), 'observed_role': 'STARTER',
                     'assessment': 'unknown', 'assessment_basis_refs': []}, 'missing_field'),
    ('meaningful_interaction', {'relationship_kind': 'friendship', 'outcome': 'positive',
                                'significance': 'routine', 'opportunity_ref': 'x'}, 'invalid_enum'),
    ('treatment', {'team_key': 'team:9', 'treatment_kind': 'dismissal', 'outcome': 'negative',
                   'significance': 'meaningful', 'outcome_refs': []}, 'unknown_team'),
    ('repair', {'breach_source_ids': [], 'action': 'acknowledgment', 'outcome_refs': [],
                'accepted_by_subject': None}, 'invalid_list'),
    ('situation', {'situation_kind': 'injury', 'state': 'negative', 'outcome_refs': []}, 'missing_field'),
    ('home_familiarity', {'region_key': 'bay-area', 'team_key': 'team:3', 'match': 'probably',
                          'geography_refs': []}, 'invalid_enum'),
])
def test_payload_requirements(kind, payload, expected):
    assert code(rev.validate_event, env(kind, 'x:1', payload=payload), team_registry=REG) == expected


# -------------------------------------------------------------------------------------------- promises ---

def test_promise_needs_authority_measurable_terms_and_a_deadline_after_it_was_made():
    rev.validate_event(promise(), team_registry=REG)
    p = promise()
    p['payload']['authority_ref'] = ''
    assert code(rev.validate_event, p, team_registry=REG) == 'invalid_text'
    p = promise()
    p['payload']['terms']['criterion'] = {}
    assert code(rev.validate_event, p, team_registry=REG) == 'unmeasurable_promise'
    assert code(rev.validate_event, promise(at=(2026, 8), due=(2026, 6)), team_registry=REG) == 'invalid_deadline'
    p = promise()
    p['payload']['beneficiary'] = person('coach1', 'coach')
    assert code(rev.validate_event, p, team_registry=REG) == 'self_edge'


def test_resolution_unknown_is_allowed_and_waivers_need_acknowledgment():
    rev.validate_event(resolved('unknown', result={'slot': 'WR', 'depth_at_due': None}), team_registry=REG)
    r = resolved('unknown')
    r['payload']['criterion_result'] = None
    rev.validate_event(r, team_registry=REG)
    r = resolved('broken')
    r['payload']['criterion_result'] = None
    assert code(rev.validate_event, r, team_registry=REG) == 'missing_field'
    assert code(rev.validate_event, resolved('waived'), team_registry=REG) == 'missing_acknowledgment'
    rev.validate_event(resolved('renegotiated', ack='inbox_message:9'), team_registry=REG)


# ------------------------------------------------------------------------------------------------- batch ---

def test_batch_dedupes_identical_facets_orders_and_checks_the_calendar():
    t = env()
    batch = rev.validate_event_batch([resolved(), t, promise(), deepcopy(t)], calendar=CAL, team_registry=REG)
    assert [e['kind'] for e in batch] == ['tenure_window', 'promise_made', 'promise_resolved']
    with pytest.raises(rev.CalendarGapError):
        rev.validate_event_batch([env(at=(2030, 1))], calendar=CAL, team_registry=REG)
    assert code(rev.validate_event_batch, [env(at=(2026, 33), payload={
        'team_key': 'team:3', 'start': tp(2026, 33), 'end': tp(2026, 34), 'coverage': 'complete'})],
        calendar=CAL, team_registry=REG) == 'invalid_time'
    rev.validate_event_batch([env()], calendar={'2026': 32}, team_registry=REG)     # JSON string keys


def test_batch_conflicts():
    changed = env(visibility='private')
    assert code(rev.validate_event_batch, [env(), changed], calendar=CAL, team_registry=REG) == 'facet_conflict'
    reuse = env(sid='mu:tenure:2026:1:8', subject=person('p8'), event_id=env()['event_id'])
    reuse['source_refs'] = [{'namespace': 'mu-test', 'key': 'other'}]
    assert code(rev.validate_event_batch, [env(), reuse], calendar=CAL, team_registry=REG) == 'transport_conflict'
    alias = env(sid='mu:tenure:2026:1:8', subject=person('p8'), event_id='y')
    alias['source_refs'] = env()['source_refs']               # one external ref naming two occurrences
    assert code(rev.validate_event_batch, [env(), alias], calendar=CAL, team_registry=REG) == 'source_ref_conflict'
    other = env(universe_id='u2', subject=person('p7', universe='u2'), target=team(universe='u2'), event_id='z')
    assert code(rev.validate_event_batch, [env(), other], calendar=CAL, team_registry=REG) == 'cross_universe'


def test_many_refs_may_name_one_occurrence():
    a = env()
    a['source_refs'] = [{'namespace': 'roster', 'key': '3:2026:1'}, {'namespace': 'office', 'key': 'run:9'}]
    b = env(subject=person('p8'), event_id='b')
    b['source_refs'] = [{'namespace': 'roster', 'key': '3:2026:1'}]
    assert rev.source_ref_map([a, b])[('roster', '3:2026:1')] == a['source_event_id']


# -------------------------------------------------------------------------------------------- intervals ---

def test_overlapping_intervals_union_and_gaps():
    iv = [{'start': tp(2026, 1), 'end': tp(2026, 5)}, {'start': tp(2026, 3), 'end': tp(2026, 8)},
          {'start': tp(2026, 8), 'end': tp(2026, 9)}, {'start': tp(2026, 20), 'end': tp(2027, 2)},
          {'start': tp(2026, 4), 'end': tp(2026, 4)}]
    assert rev.union_intervals(iv) == [{'start': tp(2026, 1), 'end': tp(2026, 9)},
                                       {'start': tp(2026, 20), 'end': tp(2027, 2)}]
    assert rev.interval_gaps(iv) == [{'start': tp(2026, 9), 'end': tp(2026, 20)}]


def test_elapsed_ticks_need_every_crossed_season():
    assert rev.ticks_between(tp(2026, 30), tp(2027, 2), CAL) == 4
    assert rev.ticks_between(tp(2026, 1), tp(2028, 1), CAL) == 64
    with pytest.raises(rev.CalendarGapError):
        rev.ticks_between(tp(2026, 1), tp(2030, 1), CAL)
    assert rev.ticks_between(tp(2031, 1), tp(2031, 9), {}) == 8          # same season: no calendar needed


# ----------------------------------------------------------------------------------------- corrections ---

def test_correction_replaces_and_a_null_correction_retracts():
    orig = env()
    fixed = dict(env(), event_id=None)
    fixed['payload']['coverage'] = 'partial'
    fixed.pop('event_id')
    c = correction('mu:tenure:2026:1:7#correction:aa', orig['source_event_id'], fixed)
    rev.validate_event(c, team_registry=REG)
    out = rev.select_active([orig, c])
    assert [e['payload']['coverage'] for e in out['active']] == ['partial']
    assert out['corrections'] == {orig['source_event_id']: c['source_event_id']}
    assert out['excluded'] == [{'source_event_id': orig['source_event_id'], 'kind': 'tenure_window',
                                'reason': 'superseded'}]
    gone = correction('mu:tenure:2026:1:7#retract:bb', orig['source_event_id'], None)
    out = rev.select_active([orig, gone])
    assert out['active'] == [] and out['excluded'][0]['reason'] == 'retracted'


def test_later_correction_supersedes_the_one_in_effect_and_cutoffs_keep_history():
    orig = dict(env(), head=1)
    r1 = deepcopy(env())
    r1.pop('event_id')
    r1['payload']['coverage'] = 'partial'
    c1 = dict(correction('s#c1', orig['source_event_id'], r1), head=2)
    r2 = deepcopy(r1)
    r2['visibility'] = 'private'
    c2 = dict(correction('s#c2', 's#c1', r2), head=3)
    now = rev.select_active([orig, c1, c2])
    assert [(e['payload']['coverage'], e['visibility']) for e in now['active']] == [('partial', 'private')]
    then = rev.select_active([orig, c1, c2], known_through=1)
    assert then['active'] == [orig]
    mid = rev.select_active([orig, c1, c2], known_through=2)
    assert mid['active'][0]['visibility'] == 'public' and mid['active'][0]['head'] == 2


def test_correction_cycles_conflicts_and_missing_targets_are_excluded_with_reasons():
    orig = env()
    a = correction('s#a', 's#b', None)
    b = correction('s#b', 's#a', None)
    out = rev.select_active([orig, a, b])
    assert out['active'] == [orig]
    assert {x['reason'] for x in out['excluded']} == {'correction_cycle'}
    r1, r2 = deepcopy(env()), deepcopy(env())
    r1.pop('event_id'), r2.pop('event_id')
    r2['payload']['coverage'] = 'partial'
    x = correction('s#x', orig['source_event_id'], r1)
    y = correction('s#y', orig['source_event_id'], r2)
    out = rev.select_active([orig, x, y])
    assert out['active'] == []
    assert {e['reason'] for e in out['excluded']} == {'conflicting_corrections'}
    stray = correction('s#z', 'nothing', None)
    assert rev.select_active([stray])['excluded'][0]['reason'] == 'correction_target_missing'
    assert code(rev.validate_event, correction('s#s', 's#s', None), team_registry=REG) == 'correction_cycle'
    nested = correction('s#n', orig['source_event_id'], correction('s#m', 'q', None))
    assert code(rev.validate_event, nested, team_registry=REG) in ('invalid_replacement', 'missing_field')


def test_order_by_time_kind_source_and_semantic_links():
    made = promise(at=(2026, 6), due=(2026, 6))
    res = resolved(at=(2026, 6))
    t = env(at=(2026, 6), payload={'team_key': 'team:3', 'start': tp(2026, 6), 'end': tp(2026, 7),
                                   'coverage': 'complete'})
    out = rev.select_active([res, t, made])
    assert [e['kind'] for e in out['active']] == ['promise_made', 'tenure_window', 'promise_resolved']
    orphan = rev.select_active([res])
    assert orphan['active'] == [] and orphan['excluded'][0]['reason'] == 'promise_unknown'
    repair = env('repair', 'inbox:repair:1', at=(2026, 10), payload={
        'breach_source_ids': ['mu:release:2026:9:7'], 'action': 'acknowledgment', 'outcome_refs': [],
        'accepted_by_subject': None})
    assert rev.select_active([repair])['excluded'][0]['reason'] == 'repair_without_breach'
    assert [e['kind'] for e in rev.select_active([repair, treatment()])['active']] == ['treatment', 'repair']


def test_as_of_cutoff_and_one_universe():
    early, late = env(), env(sid='mu:tenure:2026:9:7', at=(2026, 9), payload={
        'team_key': 'team:3', 'start': tp(2026, 9), 'end': tp(2026, 10), 'coverage': 'complete'})
    assert rev.select_active([early, late], as_of=tp(2026, 5))['active'] == [early]
    other = env(universe_id='u2', subject=person('p7', universe='u2'), target=team(universe='u2'))
    assert code(rev.select_active, [early, other]) == 'cross_universe'


# ------------------------------------------------------------------------------- producer compatibility ---

def test_inbox_and_producer_style_envelopes_validate():
    inbox = env('meaningful_interaction', 'inbox:msg:5', subject=person('gm20', 'gm'), target=person('gm10', 'gm'),
                actor=person('gm10', 'gm'), visibility='private', confidence='medium', payload={
                    'relationship_kind': 'support', 'outcome': 'positive', 'significance': 'routine',
                    'opportunity_ref': 'inbox_message:5', 'appraisal_reason': 'He listened.', 'channel': 'inbox',
                    'inbox_kind': 'GM_CHAT'})
    inbox['event_id'] = inbox['source_event_id']
    rev.validate_event(inbox, team_registry={})
    healed = env('situation', 'mu:injury:4:resolved', payload={
        'situation_kind': 'injury', 'state': 'resolved', 'valid_until': None, 'outcome_refs': ['mu:injury:4'],
        'rule': 'injury_resolved@producers-v1'})
    rev.validate_event(healed, team_registry=REG)
