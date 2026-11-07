"""personality/relationships.py (PERSONALITY_RELATIONSHIPS_PLAN.md §2.3, U2) on synthetic, already-valid envelopes:
asymmetric trust, unknown versus zero, interval union, no friend count, moved mentor relevance, tier conjunction,
hometown-only ineligible, long-tenure breach, bounded repair, no automatic forgiveness, policy saturation/decay,
complete explanations and neutral consumer context. Trial numbers live only in this fixture (plan Q1)."""
from copy import deepcopy

import pytest

from personality import relationship_events as rev
from personality import relationships as rel

U = 'u-test'
REG = {'team:3': {'team_id': 3}, 'team:5': {'team_id': 5}}
CAL = {s: 32 for s in range(2026, 2036)}
NOW = {'season': 2028, 'tick': 1}


def person(pid, role='player'):
    return {'kind': 'person', 'universe_id': U, 'role': role, 'persona_id': pid}


def team(key='team:3'):
    return {'kind': 'team', 'universe_id': U, 'team_key': key}


def tp(s, t):
    return {'season': s, 'tick': t}


ME, GM, COACH, MENTOR = person('p7'), person('gm1', 'gm'), person('c1', 'coach'), person('p1')
_n = [0]


def env(kind, payload, *, subject=ME, target=None, at=(2026, 1), sid=None, responsible=(), confidence='high', head=None):
    _n[0] += 1
    sid = sid or f'src:{kind}:{_n[0]}'
    e = {'schema_version': rev.RELATIONSHIP_SCHEMA_VERSION, 'universe_id': U, 'event_id': sid + '|t',
         'source_event_id': sid, 'source_kind': 'test', 'source_refs': [], 'kind': kind, 'occurred_at': tp(*at),
         'subject': subject, 'target': target if target is not None else team(), 'actor': None,
         'basis': 'verified_simulation', 'confidence': confidence, 'reviewer': None, 'evidence_scope': [],
         'visibility': 'private', 'attribution': {'responsible': list(responsible), 'reason_code': None,
                                                  'evidence_refs': []},
         'payload': payload, 'supersedes_source_event_id': None}
    if head is not None:
        e['head'] = head
    return rev.validate_event(e, team_registry=REG)


def tenure(start, end, *, who=ME, key='team:3', coverage='complete'):
    return env('tenure_window', {'team_key': key, 'start': tp(*start), 'end': tp(*end), 'coverage': coverage},
               subject=who, target=team(key), at=start)


def role(at, assessment='satisfied', key='team:3'):
    return env('role_window', {'team_key': key, 'start': tp(*at), 'end': tp(at[0], at[1] + 1), 'agreed_role': 'STARTER',
                               'observed_role': 'STARTER', 'assessment': assessment, 'assessment_basis_refs': []},
               target=team(key), at=at)


def talk(target, outcome='positive', significance='routine', kind='support', at=(2026, 2), subject=ME, sid=None):
    return env('meaningful_interaction', {'relationship_kind': kind, 'outcome': outcome, 'significance': significance,
                                          'opportunity_ref': 'opp'}, subject=subject, target=target, at=at, sid=sid)


def treat(kind='dismissal', outcome='negative', significance='meaningful', at=(2026, 9), key='team:3', sid=None):
    return env('treatment', {'team_key': key, 'treatment_kind': kind, 'outcome': outcome, 'significance': significance,
                             'outcome_refs': []}, target=team(key), at=at, responsible=[team(key)], sid=sid)


def repair(breach, action, accepted, at=(2026, 12)):
    return env('repair', {'breach_source_ids': [breach], 'action': action, 'outcome_refs': [],
                          'accepted_by_subject': accepted}, at=at)


def home(key='team:3'):
    return env('home_familiarity', {'region_key': 'bay-area', 'team_key': key, 'match': 'verified',
                                    'geography_refs': ['census:1']}, target=team(key))


def trial_policy(mode='enabled', **over):
    deltas = {c: 0.0 for c in rel.CONTRIBUTION_CODES}
    deltas.update({'interaction.positive.routine': 0.1, 'interaction.positive.meaningful': 0.2,
                   'interaction.negative.routine': -0.1, 'interaction.negative.meaningful': -0.2,
                   'treatment.dismissal.negative': -0.5, 'treatment.disrespect.negative': -0.3,
                   'treatment.recognition.positive': 0.3, 'treatment.support.positive': 0.2,
                   'promise.kept': 0.2, 'promise.broken': -0.4})
    p = {'mode': mode, 'rule_version': 'trial-fixture-1',
         'trust': {'cap': 0.8, 'positive_half_life_ticks': 32, 'deltas': deltas,
                   'breach_floor': {'material': -0.25, 'minor': -0.05},
                   'repair_step': {'acknowledgment': 0.0, 'restitution': 0.5, 'commitment_met': 0.5,
                                   'reconciliation': 1.0}},
         'attachment': {'tiers': {'weak': {'min_tenure_ticks': 16, 'min_affirmative': 1, 'min_connections': 0},
                                  'established': {'min_tenure_ticks': 48, 'min_affirmative': 3, 'min_connections': 1},
                                  'strong': {'min_tenure_ticks': 96, 'min_affirmative': 6, 'min_connections': 2}}},
         'reactions': None}
    p.update(over)
    return p


EVIDENCE_ONLY = {'mode': 'observe', 'rule_version': 'evidence-only'}


def project(events, policy=None, subject=ME, as_of=NOW, known_through=None, calendar=CAL):
    return rel.project_relationships(events, subject=subject, as_of=as_of, known_through=known_through,
                                     policy=policy or trial_policy(), calendar=calendar)


def edge(state, target):
    found = [e for e in state['edges'] if rev.target_key(e['target']) == rev.target_key(target)]
    return found[0] if found else None


# ------------------------------------------------------------------------------------------------ policy ---

def test_empty_policy_observes_evidence_only_and_cannot_enable():
    assert rel.validate_policy(EVIDENCE_ONLY)['trust'] is None
    with pytest.raises(rel.PolicyError):
        rel.validate_policy({'mode': 'enabled', 'rule_version': 'x'})
    with pytest.raises(rel.PolicyError):
        rel.validate_policy(trial_policy(attachment=None))


@pytest.mark.parametrize('mutate', [
    lambda p: p['trust']['deltas'].pop('promise.kept'),
    lambda p: p['trust']['deltas'].update({'promise.kept': float('inf')}),
    lambda p: p['trust']['deltas'].update({'interaction.made.up': 0.1}),
    lambda p: p['trust'].update(cap=0),
    lambda p: p['trust'].update(positive_half_life_ticks=-5),
    lambda p: p['trust']['breach_floor'].update(material=0.2),
    lambda p: p['trust']['repair_step'].update(reconciliation=1.5),
    lambda p: p['attachment']['tiers']['strong'].update(min_tenure_ticks=10),
    lambda p: p['attachment']['tiers']['established'].update(min_connections=0),
    lambda p: p['attachment']['tiers']['weak'].update(min_affirmative=0),
    lambda p: p.update(mode='on'),
    lambda p: p.update(extra=True),
])
def test_policy_rejections(mutate):
    p = trial_policy()
    mutate(p)
    with pytest.raises(rel.PolicyError):
        rel.validate_policy(p)


# ------------------------------------------------------------------------------------------------- trust ---

def test_trust_is_directional_and_unknown_is_not_zero():
    a_to_b = talk(GM, subject=ME, sid='talk:1')
    s = project([a_to_b])
    assert edge(s, GM)['trust'] > 0
    reverse = project([a_to_b], subject=GM)
    assert reverse['edges'] == []                                          # GM has no view of p7 from p7's appraisal
    assert edge(project([]), GM) is None
    evidence = edge(project([a_to_b], policy=EVIDENCE_ONLY), GM)
    assert evidence['status'] == 'known' and evidence['trust'] is None
    assert evidence['explanation'][0]['excluded_reason'] == 'no_numeric_policy'
    neutral = talk(COACH, outcome='mixed', sid='talk:2')
    assert edge(project([neutral]), COACH)['trust'] == 0.0                 # known neutral is zero


def test_positive_context_fades_and_needs_the_calendar():
    t = talk(GM, at=(2026, 1))
    fresh = edge(project([t], as_of=tp(2026, 1)), GM)['trust']
    year = edge(project([t], as_of=tp(2027, 1)), GM)['trust']
    assert fresh == pytest.approx(0.1) and year == pytest.approx(0.05)
    blocked = edge(project([t], as_of=tp(2027, 1), calendar={2027: 32}), GM)
    assert blocked['trust'] is None and blocked['explanation'][-1]['excluded_reason'] == 'calendar_gap'


def test_saturation_is_capped_and_explained():
    talks = [talk(GM, significance='meaningful', at=(2027, 30 + i % 2), sid=f'talk:{i}') for i in range(8)]
    e = edge(project(talks, as_of=tp(2027, 31)), GM)
    assert e['trust'] == 0.8
    assert e['explanation'][-1]['code'] == 'saturation'
    codes = [x['code'] for x in e['explanation']]
    assert codes.count('interaction.positive.meaningful') == 8


def test_one_source_counts_once_per_edge():
    a = treat(sid='mu:release:1')
    b = env('treatment', dict(a['payload'], treatment_kind='disrespect'), target=team(), at=(2026, 9),
            responsible=[team()], sid='mu:release:1')
    e = edge(project([a, b]), team())
    assert [x['code'] for x in e['explanation']].count('treatment.dismissal.negative') == 1
    assert 'treatment.disrespect.negative' not in [x['code'] for x in e['explanation']]


def test_breach_is_attributed_only_to_the_evidenced_party():
    cut = treat()
    s = project([cut, talk(GM, sid='talk:gm')])
    assert edge(s, team())['unresolved_breaches'][0]['severity'] == 'material'
    assert edge(s, GM)['unresolved_breaches'] == []


def test_breach_holds_its_floor_and_time_does_not_forgive():
    cut = treat(at=(2026, 9))
    later = [talk(team(), significance='meaningful', at=(2030, i + 1), sid=f't{i}') for i in range(6)]
    e = edge(project([cut] + later, as_of=tp(2030, 8)), team())
    assert e['trust'] == -0.25 and e['explanation'][-1]['code'] == 'breach_floor'
    assert edge(project([cut], as_of=tp(2035, 1)), team())['trust'] == -0.5     # no decay on an open breach


def test_apology_acknowledges_but_only_accepted_corrective_action_closes():
    cut = treat(sid='mu:release:9')
    sorry = repair('mu:release:9', 'acknowledgment', None)
    e = edge(project([cut, sorry]), team())
    assert e['unresolved_breaches'][0]['repair_state'] == 'acknowledged' and e['trust'] == -0.5
    fix = repair('mu:release:9', 'restitution', None, at=(2026, 13))
    assert edge(project([cut, sorry, fix]), team())['unresolved_breaches'][0]['repair_state'] == 'acknowledged'
    fixed = repair('mu:release:9', 'restitution', True, at=(2026, 13))
    e = edge(project([cut, sorry, fixed]), team())
    assert e['unresolved_breaches'] == [] and e['trust'] == pytest.approx(-0.25)


def test_repair_cannot_restore_beyond_neutral():
    cut = treat(sid='mu:release:9')
    fixes = [repair('mu:release:9', 'reconciliation', True, at=(2026, 12 + i)) for i in range(4)]
    assert edge(project([cut] + fixes), team())['trust'] == 0.0


def test_promise_broken_is_a_breach_toward_the_issuer():
    made = env('promise_made', {'promise_id': 'p1', 'issuer': COACH, 'beneficiary': ME,
                                'terms': {'kind': 'role', 'description': 'WR1', 'criterion': {'slot': 'WR'}},
                                'due_at': tp(2026, 6), 'authority_ref': 'thread:1'}, target=COACH, at=(2026, 2))
    broken = env('promise_resolved', {'promise_id': 'p1', 'resolution': 'broken', 'criterion_result': {'depth': 3},
                                      'outcome_refs': [], 'beneficiary_ack_ref': None}, target=COACH, at=(2026, 6))
    e = edge(project([made, broken]), COACH)
    assert e['unresolved_breaches'] == [{'source_id': broken['source_event_id'], 'promise_id': 'p1',
                                         'severity': 'material', 'repair_state': 'open'}]
    unknown = env('promise_resolved', dict(broken['payload'], resolution='unknown'), target=COACH, at=(2026, 6))
    assert edge(project([made, unknown]), COACH)['unresolved_breaches'] == []


# --------------------------------------------------------------------------------------------- attachment ---

def career(seasons=3, satisfied=6):
    evs = [tenure((2026 + i, 1), (2027 + i, 1)) for i in range(seasons)]
    evs += [role((2026, 1 + i)) for i in range(satisfied)]
    return evs


def test_interval_union_counts_overlap_once():
    evs = [tenure((2026, 1), (2026, 20)), tenure((2026, 10), (2027, 1))]
    e = edge(project(evs), team())
    assert e['coverage'] == {'start': tp(2026, 1), 'end': tp(2027, 1), 'gaps': [], 'complete': True}
    assert e['attachment']['dimensions']['tenure_ticks'] == 32


def test_tier_is_a_conjunction():
    mentors = [talk(MENTOR, significance='meaningful', kind='mentorship', sid='m1'),
               tenure((2026, 1), (2029, 1), who=MENTOR)]
    assert edge(project(career() + mentors), team())['attachment']['tier'] == 'established'
    no_ties = edge(project(career()), team())['attachment']
    assert no_ties['tier'] == 'weak' and 'established:min_connections' in no_ties['blocking_reasons']
    short = edge(project([tenure((2027, 20), (2027, 30)), role((2027, 20))] + mentors), team())['attachment']
    assert short['tier'] == 'none' and not short['eligible']


def test_no_friend_count_bonus():
    friends = []
    for i in range(10):
        p = person(f'f{i}')
        friends += [talk(p, significance='meaningful', sid=f'f{i}'), tenure((2026, 1), (2029, 1), who=p)]
    e = edge(project(career(satisfied=3) + friends), team())
    assert e['attachment']['tier'] == 'established'                  # strong needs tenure and evidence, not friends
    assert e['attachment']['dimensions']['connections'] == 10
    assert e['trust'] is None or e['trust'] == edge(project(career(satisfied=3)), team())['trust']


def test_moved_mentor_keeps_the_tie_but_not_team_relevance():
    tie = talk(MENTOR, significance='meaningful', kind='mentorship', sid='m1')
    moved = [tie, tenure((2026, 1), (2027, 1), who=MENTOR), tenure((2027, 1), (2029, 1), who=MENTOR, key='team:5')]
    s = project(career() + moved)
    (t,) = edge(s, team())['meaningful_ties']
    assert t['relevant_to_team'] is False
    assert edge(s, MENTOR)['meaningful_ties'][0]['person'] == MENTOR


def test_hometown_alone_is_ineligible():
    e = edge(project([home()]), team())
    assert e['hometown']['status'] == 'verified'
    assert e['attachment']['tier'] == 'unknown' and not e['attachment']['eligible']
    assert 'tenure_unknown' in e['attachment']['blocking_reasons']
    assert e['status'] == 'unknown' and e['trust'] is None


def test_long_tenure_with_an_open_breach_cannot_be_established():
    mentors = [talk(MENTOR, significance='meaningful', sid='m1'), tenure((2026, 1), (2030, 1), who=MENTOR),
               talk(person('p2'), significance='meaningful', sid='m2'), tenure((2026, 1), (2030, 1), who=person('p2'))]
    evs = career(seasons=4, satisfied=8) + mentors + [treat(sid='mu:cut:1')]
    a = edge(project(evs, as_of=tp(2029, 30)), team())['attachment']
    assert a['tier'] == 'weak' and 'strong:unresolved_material_breach' in a['blocking_reasons']
    fixed = evs + [repair('mu:cut:1', 'reconciliation', True)]
    assert edge(project(fixed, as_of=tp(2029, 30)), team())['attachment']['tier'] == 'strong'


def test_partial_history_is_unknown_not_none():
    a = edge(project([tenure((2027, 20), (2027, 22), coverage='partial')]), team())['attachment']
    assert a['tier'] == 'unknown' and 'tenure_partial' in a['blocking_reasons']


def test_missing_attachment_policy_is_unknown():
    a = edge(project(career(), policy=EVIDENCE_ONLY), team())['attachment']
    assert a['tier'] == 'unknown' and a['blocking_reasons'] == ['no_attachment_policy']


# ------------------------------------------------------------------------------------- context and audit ---

def test_off_and_observe_give_identical_neutral_context():
    evs = career() + [treat()]
    off = rel.relationship_context(project(evs, policy=trial_policy('off')))
    observe_state = project(evs, policy=trial_policy('observe'))
    assert rel.relationship_context(observe_state) == off == rel.neutral_context(ME)
    assert edge(observe_state, team())['trust'] is not None                # private audit still computed
    enabled = rel.relationship_context(project(evs), target=team())
    assert enabled['status'] == 'enabled' and len(enabled['edges']) == 1
    assert enabled['edges'][0]['breaches'][0]['severity'] == 'material'


def test_complete_explanations_and_deterministic_hashes():
    evs = career() + [treat(), talk(GM, sid='g1')]
    a, b = project(evs), project(list(reversed(evs)))
    assert a == b
    e = edge(a, team())
    for x in e['explanation']:
        assert set(x) == {'code', 'source_ids', 'before', 'after', 'excluded_reason'}
    assert {k: e[k] for k in ('rule_version', 'as_of', 'known_through')} == \
        {'rule_version': 'trial-fixture-1', 'as_of': NOW, 'known_through': None}
    assert project(evs, policy=trial_policy(rule_version='other'))['policy_hash'] != a['policy_hash']


def test_knowledge_cutoff_hides_later_ingestion():
    early = talk(GM, sid='g1')
    late = dict(deepcopy(talk(GM, outcome='negative', sid='g2', at=(2026, 3))), head=5)
    assert edge(project([early, late], known_through=4), GM)['trust'] == pytest.approx(0.1 * 0.5 ** (63 / 32), abs=1e-6)


# ------------------------------------------------------------------- mentorship direction (COACHING_TREES §2.1) ---

def directed(target, direction, sid, at=(2026, 3)):
    return env('meaningful_interaction', {'relationship_kind': 'mentorship', 'outcome': 'positive',
                                          'significance': 'meaningful', 'opportunity_ref': 'coaching-tree:x',
                                          'direction': direction}, target=target, at=at, sid=sid)


def test_ties_carry_direction_and_undirected_ties_read_none():
    s = project([talk(MENTOR, significance='meaningful', kind='mentorship', sid='m0')])
    assert edge(s, MENTOR)['meaningful_ties'][0]['direction'] is None
    s = project([directed(MENTOR, 'mentee', 'tree1')])
    (t,) = edge(s, MENTOR)['meaningful_ties']
    assert (t['kind'], t['direction']) == ('mentorship', 'mentee')


@pytest.mark.parametrize('directed_first', [True, False])
def test_directed_mentorship_beats_an_undirected_tie_in_either_order(directed_first):
    plain = talk(MENTOR, significance='meaningful', kind='support', sid='prof1', at=(2026, 2 if directed_first else 1))
    tree = directed(MENTOR, 'mentee', 'tree1', at=(2026, 1 if directed_first else 2))
    (t,) = edge(project([plain, tree]), MENTOR)['meaningful_ties']
    assert (t['kind'], t['direction']) == ('mentorship', 'mentee')
    assert t['source_ids'] == ['prof1', 'tree1']


def test_relationship_context_and_tie_label_read_the_direction():
    from server.personality_api import tie_label
    for direction, words in (('mentee', 'mentored by'), ('mentor', 'mentor to')):
        ctx = rel.relationship_context(project([directed(MENTOR, direction, f't-{direction}')]), target=MENTOR)
        (e,) = ctx['edges']
        assert e['ties'][0]['direction'] == direction
        assert tie_label(e) == words
