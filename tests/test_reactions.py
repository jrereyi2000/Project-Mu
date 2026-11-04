"""personality/reactions.py (PERSONALITY_RELATIONSHIPS_PLAN.md §2.4-§2.5, U3) on synthetic envelopes and a frozen
§2.3 relationship-state fixture (no relationships.py import): breach outranks celebration with the secondary kept,
mixed/expiry/ties, no loss or injury stereotype, real-inspired players and reviewed coaches appraise like any
persona, no core mutation, candidate retracted support and blocked slider band, off/observe neutral equality."""
from copy import deepcopy

import pytest

from personality import core
from personality import reactions as rx
from personality import relationship_events as rev

U = 'u-test'
REG = {'team:3': {'team_id': 3}}
CAL = {s: 32 for s in range(2026, 2036)}
NOW = {'season': 2026, 'tick': 12}


def person(pid, role='player'):
    return {'kind': 'person', 'universe_id': U, 'role': role, 'persona_id': pid}


TEAM = {'kind': 'team', 'universe_id': U, 'team_key': 'team:3'}
ME = person('p7')


def tp(s, t):
    return {'season': s, 'tick': t}


def env(kind, payload, *, sid, at=(2026, 1), target=TEAM, subject=ME, visibility='private', responsible=(),
        supersedes=None):
    e = {'schema_version': rev.RELATIONSHIP_SCHEMA_VERSION, 'universe_id': U, 'event_id': sid + '|t',
         'source_event_id': sid, 'source_kind': 'test', 'source_refs': [], 'kind': kind, 'occurred_at': tp(*at),
         'subject': subject, 'target': target, 'actor': None, 'basis': 'verified_simulation', 'confidence': 'high',
         'reviewer': None, 'evidence_scope': [], 'visibility': visibility,
         'attribution': {'responsible': list(responsible), 'reason_code': None, 'evidence_refs': []},
         'payload': payload, 'supersedes_source_event_id': supersedes}
    return rev.validate_event(e, team_registry=REG)


def situation(kind, state, *, sid, at=(2026, 10), until=(2026, 20), visibility='public', subject=ME):
    return env('situation', {'situation_kind': kind, 'state': state,
                             'valid_until': tp(*until) if until else None, 'outcome_refs': []},
               sid=sid, at=at, visibility=visibility, subject=subject)


def role(tick, assessment, *, sid=None, start=None):
    start = start if start is not None else tick
    return env('role_window', {'team_key': 'team:3', 'start': tp(2026, start), 'end': tp(2026, start + 1),
                               'agreed_role': 'STARTER', 'observed_role': 'RESERVE', 'assessment': assessment,
                               'assessment_basis_refs': []}, sid=sid or f'mu:role:2026:{tick}:7', at=(2026, tick))


CUT = env('treatment', {'team_key': 'team:3', 'treatment_kind': 'dismissal', 'outcome': 'negative',
                        'significance': 'meaningful', 'outcome_refs': []}, sid='mu:release:1', at=(2026, 9),
          responsible=[TEAM])


def state(breaches=(), subject=ME):
    """The frozen §2.3 state shape, only the fields reactions read."""
    return {'subject': subject, 'edges': [{'target': TEAM, 'unresolved_breaches': list(breaches)}]}


OPEN = {'source_id': 'mu:release:1', 'promise_id': None, 'severity': 'material', 'repair_state': 'open'}


def policy(mode='enabled', **reactions):
    return {'mode': mode, 'rule_version': 'trial-1', 'reactions': reactions or None}


def view(origin='generated'):
    return {'status': 'stored', 'origin': origin, 'sliders': {}, 'traits': [], 'revision': 0,
            'persona_id': 'p7', 'view_version': core.FEATURE_VIEW_VERSION}


MISSING = core.feature_view(None, 'reactions')


def run(events, st=None, *, pol=None, fv=None, as_of=NOW, known_through=None):
    return rx.project_reactions(events, st or state(), feature_view=fv or view(), as_of=as_of,
                                known_through=known_through, policy=pol or policy(), calendar=CAL)


# ---------------------------------------------------------------------------------------------- priorities ---

def test_breach_outranks_celebration_and_keeps_it_secondary():
    award = situation('award', 'positive', sid='mu:award:1')
    out = run([CUT, award], state([OPEN]))
    assert out['mood']['primary'] == 'frustrated'
    assert out['mood']['secondary'] == ['encouraged']
    assert [r['priority'] for r in out['reactions']] == [1, 5]


def test_sustained_role_frustration_needs_more_than_one_scan():
    one = run([role(5, 'unsatisfied')])
    assert one['reactions'] == []
    two = run([role(5, 'unsatisfied'), role(6, 'unsatisfied')])
    assert [r['tag'] for r in two['reactions']] == ['frustrated'] and two['reactions'][0]['domain'] == 'role'
    gap = run([role(5, 'unsatisfied'), role(8, 'unsatisfied')])
    assert gap['reactions'] == []
    healed = run([role(5, 'unsatisfied'), role(6, 'unsatisfied'), role(7, 'satisfied')])
    assert healed['reactions'] == []
    strict = run([role(5, 'unsatisfied'), role(6, 'unsatisfied')], pol=policy(sustained_role_windows=3))
    assert strict['reactions'] == []
    with pytest.raises(rx.ReactionPolicyError):
        rx.validate_reaction_policy(policy(sustained_role_windows=1))


def test_no_loss_or_injury_stereotype():
    loss = situation('team_result', 'negative', sid='mu:result:loss')
    out = run([loss])
    assert out['reactions'] == [] and out['mood']['primary'] == 'neutral'
    hurt = run([situation('injury', 'negative', sid='mu:injury:1')])
    assert [r['tag'] for r in hurt['reactions']] == ['uncertain']
    assert all(r['tag'] in rx.TAGS for r in hurt['reactions'])


def test_expiry_and_resolution_remove_the_reaction_not_the_history():
    hurt = situation('injury', 'negative', sid='mu:injury:1', at=(2026, 4), until=(2026, 10))
    assert run([hurt], as_of=tp(2026, 9))['mood']['primary'] == 'uncertain'
    gone = run([hurt], as_of=tp(2026, 10))
    assert gone['reactions'] == [] and gone['inactive'][0]['reason'] == 'expired'
    healed = situation('injury', 'resolved', sid='mu:injury:1:resolved', at=(2026, 7), until=None)
    assert run([hurt, healed], as_of=tp(2026, 9))['reactions'] == []


def test_same_domain_conflict_becomes_mixed_and_is_explained():
    a = situation('arrival', 'positive', sid='mu:arrive:1')
    d = situation('departure', 'uncertain', sid='mu:depart:1')
    out = run([a, d])
    (r,) = out['reactions']
    assert r['tag'] == 'mixed' and r['source_ids'] == ['mu:arrive:1', 'mu:depart:1']
    assert {s['reason'].split(':')[0] for s in out['suppressed']} == {'merged_into'}


def test_ties_break_by_severity_then_recency_then_source():
    job = situation('job_pressure', 'uncertain', sid='mu:hot:1', at=(2026, 3))
    hurt = situation('injury', 'negative', sid='mu:injury:2', at=(2026, 5))
    out = run([job, hurt])
    assert out['reactions'][0]['source_ids'] == ['mu:injury:2']           # same priority: newer first
    award = situation('award', 'positive', sid='mu:award:1', at=(2026, 2))
    arrive = situation('arrival', 'positive', sid='mu:arrive:1', at=(2026, 2))
    m = run([award, arrive, job], pol=policy(severity={'encouraged': 0.9}))
    assert m['mood']['primary'] == 'uncertain' and m['mood']['secondary'] == ['encouraged']
    assert m['mood']['suppressed'] == [{'reaction_id': m['reactions'][1]['reaction_id'],
                                        'reason': 'contradicts_uncertain'}]          # eager vs uncertain


def test_secondary_limit_is_two():
    evs = [situation('job_pressure', 'uncertain', sid='a'), situation('award', 'positive', sid='b'),
           role(5, 'unsatisfied'), role(6, 'unsatisfied')]
    out = run(evs + [CUT], state([OPEN]))
    assert out['mood']['primary'] == 'frustrated'
    assert out['mood']['secondary'] == ['uncertain', 'encouraged']
    assert len(out['mood']['secondary']) <= rx.MAX_SECONDARY


# ---------------------------------------------------------------------------------------- who reacts ---

@pytest.mark.parametrize('origin', ['generated', 'real'])
def test_real_inspired_and_generated_people_appraise_alike(origin):
    coach = person('c1', 'coach')
    evs = [situation('job_pressure', 'uncertain', sid='mu:hot:c1', subject=coach)]
    out = run(evs, state(subject=coach), fv=view(origin))
    assert out['mood']['primary'] == 'uncertain'


def test_unknown_origin_stays_neutral_and_missing_profile_uses_evidence_only():
    evs = [situation('award', 'positive', sid='mu:award:1')]
    assert run(evs, fv=view('unknown'))['neutral_reason'] == 'origin_unknown'
    assert run(evs, fv=view('unknown'))['reactions'] == []
    assert run(evs, fv=MISSING)['mood']['primary'] == 'encouraged'
    with pytest.raises(ValueError):
        run(evs, fv={'origin': 'generated'})


def test_off_and_observe_give_identical_neutral_context_and_visibility_filters_writers():
    evs = [CUT, situation('award', 'positive', sid='mu:award:1')]
    off = rx.reaction_context(run(evs, state([OPEN]), pol=policy('off')))
    observe = run(evs, state([OPEN]), pol=policy('observe'))
    assert rx.reaction_context(observe) == off and off['mood'] == 'neutral'
    assert observe['mood']['primary'] == 'frustrated'                      # private audit kept
    enabled = run(evs, state([OPEN]))
    writer = rx.public_context(enabled, policy=policy())
    assert writer['mood'] == 'encouraged' and writer['framing'] == 'fictional_character_interior'
    assert all(r['domain'] != 'organization' for r in writer['reactions'])


def test_reads_are_deterministic_and_change_nothing():
    evs = [CUT, situation('award', 'positive', sid='mu:award:1')]
    before = deepcopy(evs)
    a = run(evs, state([OPEN]))
    b = run(list(reversed(evs)), state([OPEN]))
    assert a == b and evs == before
    assert a['reactions'][0]['reaction_id'] == run(evs, state([OPEN]))['reactions'][0]['reaction_id']


# ------------------------------------------------------------------------------------- evolution review ---

@pytest.fixture
def profile():
    return core.initialize(core.Identity(U, 'player', 'birth-7'), 'generated', 2026)


def candidate(profile, **over):
    c = {'event_id': 'rel-cand-1', 'source_event_id': 'mu:release:1', 'season': 2027,
         'expected_revision': profile['revision'], 'rule_version': 'relationships-evo-1', 'reason': 'released',
         'kind': 'slider', 'slider': 'temperament', 'step': 1, 'review_status': 'pending', 'reviewer': None,
         'feature_scope': ['relationships'], 'supporting_source_ids': ['mu:release:1']}
    c.update(over)
    return c


def pme(profile):
    return dict(ME, persona_id=profile['persona_id'])


def cut_for(profile):
    return env('treatment', CUT['payload'], sid='mu:release:1', at=(2026, 9), subject=pme(profile),
               responsible=[TEAM])


def test_candidate_review_and_ready(profile):
    before = deepcopy(profile)
    out = rx.validate_evolution_candidate(candidate(profile), profile=profile, source_events=[cut_for(profile)])
    assert out['status'] == 'review_required' and out['support']['active'] == ['mu:release:1']
    ready = rx.validate_evolution_candidate(candidate(profile, review_status='approved', reviewer='JR'),
                                            profile=profile, source_events=[cut_for(profile)])
    assert ready['status'] == 'ready' and ready['reasons'] == []
    assert profile == before


def test_candidate_retracted_support_is_blocked(profile):
    retract = env('correction', {'reason': 'event_vanished', 'replacement_event': None},
                  sid='mu:release:1#retract:x', at=(2026, 9), subject=pme(profile), supersedes='mu:release:1')
    out = rx.validate_evolution_candidate(candidate(profile), profile=profile,
                                          source_events=[cut_for(profile), retract])
    assert out['status'] == 'blocked' and out['reasons'] == ['support_retracted:mu:release:1']


def test_candidate_duplicate_other_subject_and_cutoff(profile):
    dup = rx.validate_evolution_candidate(candidate(profile, supporting_source_ids=['mu:release:1'] * 2),
                                          profile=profile, source_events=[cut_for(profile)])
    assert 'duplicate_support' in dup['reasons']
    other = rx.validate_evolution_candidate(candidate(profile), profile=profile, source_events=[CUT])
    assert other['reasons'] == ['support_other_subject:mu:release:1']
    early = rx.validate_evolution_candidate(candidate(profile, season=2025), profile=profile,
                                            source_events=[cut_for(profile)])
    assert 'support_after_cutoff:mu:release:1' in early['reasons']


def test_blocked_slider_band_only_on_an_exact_30_database(profile):
    total = sum(profile['sliders'].values())
    assert total == 30
    ok = rx.validate_evolution_candidate(candidate(profile), profile=profile, source_events=[cut_for(profile)])
    assert 'blocked_slider_band' not in ok['reasons']
    old = rx.validate_evolution_candidate(candidate(profile), profile=profile, source_events=[cut_for(profile)],
                                          slider_band=(30, 30))
    assert old['reasons'] == ['blocked_slider_band']
    trait = profile['traits'][0]
    t = rx.validate_evolution_candidate(candidate(profile, kind='trait', remove=trait, slider=None, step=None),
                                        profile=profile, source_events=[cut_for(profile)], slider_band=(30, 30))
    assert t['status'] == 'review_required'


def test_candidate_stale_revision_and_annual_slot(profile):
    stale = rx.validate_evolution_candidate(candidate(profile, expected_revision=4), profile=profile,
                                            source_events=[cut_for(profile)])
    assert stale['reasons'] == ['stale_revision']
    used = dict(profile, last_changed_season=2027, revision=1)
    annual = rx.validate_evolution_candidate(candidate(used, expected_revision=1), profile=used,
                                             source_events=[cut_for(profile)])
    assert annual['reasons'] == ['annual_slot_used']
