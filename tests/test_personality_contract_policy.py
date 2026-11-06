"""engines/personality_contract_policy.py: bounded terms, gates, monotonicity and the hard contender discontinuity,
config/context validation, neutral views (PERSONALITY_CONTRACT_POLICY_PLAN.md §2.1-§2.2, §4). Synthetic, offline."""
from copy import deepcopy
import math

import pytest

from engines import personality_contract_policy as cp
from engines.free_agency_engine import W_DEFAULT
from personality.core import Identity, PersonalityError, apply_override, feature_view, initialize

AS_OF = {'season': 2031, 'week': 25, 'stage': 'FREE_AGENCY'}
CUR, OTHER = 7, 9


def active(**kw):
    return cp.trial_config(mode='active', **kw)


def view(traits, persona_id='p-1', revision=0, origin='generated'):
    return {'status': 'stored', 'origin': origin, 'sliders': {}, 'traits': list(traits), 'revision': revision,
            'persona_id': persona_id, 'catalogue_version': 'c', 'core_version': 'v', 'view_version': 'views-v2'}


def team(**kw):
    t = cp.unknown_team()
    t.update(kw)
    return t


def context(established=True, secure=True, teams=None, persona_id='p-1', revision=0, current=CUR):
    c = cp.neutral_context('mu:test', 1, AS_OF, 'batch-1', persona_id=persona_id, revision=revision,
                           current_team_id=current, team_ids=(CUR, OTHER))
    c['career']['established'] = established
    c['security']['secure'] = secure
    c['security']['through_season'] = 2030
    for tid, rec in (teams or {}).items():
        c['teams'][tid] = rec
    return c


def parts(F, C, O=1.0, team_id=OTHER):
    return {'financial': F, 'opportunity': O, 'contention': C, 'scheme': 0.5, 'team_id': team_id}


def delta(F, C, traits, cfg=None, ctx=None, O=1.0, team_id=OTHER, v=None):
    return cp.contract_personality_delta(parts(F, C, O, team_id), v or view(traits), ctx or context(),
                                         cfg or active())


def base_u(F, C, O=1.0, S=0.5):
    w = W_DEFAULT
    return w['money'] * F + w['role'] * O + w['team'] * C + w['scheme'] * S


# ------------------------------------------------------------------ config ----
def test_trial_config_is_valid_and_off_by_default():
    cp.validate_contract_policy(cp.TRIAL_CONFIG)
    assert cp.TRIAL_CONFIG['mode'] == 'off' and cp.TRIAL_CONFIG['relationship_enabled'] is False


@pytest.mark.parametrize('bad', [
    {'coefficient': float('nan')}, {'coefficient': math.inf}, {'aggregate_cap': -0.01}, {'coefficient': 0.20},
    {'mode': 'live'}, {'ambition_enabled': 1}, {'home_bonus': '0.01'},
    {'attachment_bonus': {'none': 0, 'weak': 0.05, 'established': 0.01, 'strong': 0.1}},
    {'attachment_bonus': {'none': 0, 'weak': 0.01}},
])
def test_invalid_config_rejected(bad):
    cfg = deepcopy(cp.TRIAL_CONFIG)
    cfg.update(bad)
    with pytest.raises(cp.ContractPolicyError):
        cp.validate_contract_policy(cfg)


def test_config_with_extra_key_rejected():
    with pytest.raises(cp.ContractPolicyError):
        cp.validate_contract_policy(dict(cp.TRIAL_CONFIG, client_enabled=True))


# ------------------------------------------------------------- neutral ----
def test_off_mode_evaluates_nothing():
    r = delta(1.4, 0.2, ['MONEY_FIRST'], cfg=cp.trial_config())
    assert r['capped_delta'] == 0.0 and r['reasons'] == ['mode_off']


def test_missing_profile_is_neutral_with_reason():
    missing = feature_view(None, 'contracts')
    r = cp.contract_personality_delta(parts(1.4, 0.2), missing, context(persona_id=None, revision=None), active())
    assert r['capped_delta'] == 0.0 and r['reasons'] == ['profile_missing']


def test_unsupported_traits_and_sliders_have_no_effect():
    v = view(['LEGACY_BUILDER', 'SPOTLIGHT_SEEKER', 'DIVA', 'WORKOUT_FANATIC'])
    v['sliders'] = {'warmth': 10, 'confidence': 0, 'drive': 10, 'sociability': 0, 'temperament': 10, 'openness': 6}
    r = delta(1.4, 0.2, [], v=v)
    assert r['capped_delta'] == 0.0 and 'no_contract_traits' in r['reasons']


def test_identity_or_revision_mismatch_raises():
    with pytest.raises(cp.ContractPolicyError):
        delta(1.0, 0.5, ['MONEY_FIRST'], v=view(['MONEY_FIRST'], revision=2))
    with pytest.raises(cp.ContractPolicyError):
        delta(1.0, 0.5, ['MONEY_FIRST'], v=view(['MONEY_FIRST'], persona_id='someone-else'))


# ------------------------------------------------------- generated views ----
def _profile_with(trait, start):
    for i in range(start, start + 200):
        p = initialize(Identity('mu:test', 'player', f'roster:{i}'), 'generated', 2026)
        if trait in p['traits']:
            return p
        if {'MONEY_FIRST', 'RING_CHASER'} & set(p['traits']):
            continue
        try:
            after, _ = apply_override(p, dict(override_id=f'jr-{i}', actor='JR', reason='JR edit', season=2027,
                                              tick=1, expected_revision=p['revision'], date='2026-11-06',
                                              add_traits=[trait], remove_traits=[p['traits'][0]]))
            return after
        except PersonalityError:
            continue
    raise AssertionError('no profile found')


def test_full_generated_profile_including_jr_edited_revision():
    p = _profile_with('MONEY_FIRST', 0)
    v = feature_view(p, 'contracts')
    ctx = context(persona_id=v['persona_id'], revision=v['revision'])
    r = cp.contract_personality_delta(parts(1.2, 0.4), v, ctx, active())
    assert r['terms']['ambition'] == pytest.approx(0.10 * (1.2 - 0.4)) or r['cap_applied']
    assert r['capped_delta'] > 0


def test_prospect_profile_reads_the_same_way():
    for i in range(400):
        p = initialize(Identity('mu:test', 'player', f'prospect:2027:{i}'), 'generated', 2027)
        if 'MONEY_FIRST' in p['traits'] and 'RING_CHASER' not in p['traits']:
            break
    else:
        pytest.skip('no generated prospect drew MONEY_FIRST')
    v = feature_view(p, 'contracts')
    r = cp.contract_personality_delta(parts(1.0, 0.5), v, context(persona_id=v['persona_id'], revision=0),
                                      active())
    assert r['terms']['ambition'] == pytest.approx(0.05)


# ---------------------------------------------------------------- ambition ----
def test_money_first_and_aggregate_cap():
    small = delta(0.9, 0.6, ['MONEY_FIRST'])
    assert small['terms']['ambition'] == pytest.approx(0.03) and not small['cap_applied']
    big = delta(1.6, 0.1, ['MONEY_FIRST'])
    assert big['raw_delta'] == pytest.approx(0.15) and big['capped_delta'] == pytest.approx(0.08) and big['cap_applied']


def test_ring_chaser_secure_role_player_qualifies_elite_insecure_youngster_fails():
    vet = delta(0.8, 0.85, ['RING_CHASER'], ctx=context(established=True, secure=True), O=0.65)
    assert vet['terms']['ambition'] == pytest.approx(0.10 * (0.85 - 0.8))
    young = delta(0.8, 0.85, ['RING_CHASER'], ctx=context(established=False, secure=False), O=1.0)
    assert young['terms']['ambition'] == 0.0
    assert {'ring_gate_established_career_false', 'ring_gate_secure_false'} <= set(young['reasons'])


@pytest.mark.parametrize('established,secure,O,C', [(None, True, 1.0, 0.9), (True, None, 1.0, 0.9),
                                                     (True, True, 0.3, 0.9), (True, True, 0.15, 0.9),
                                                     (True, True, 1.0, 0.69)])
def test_ring_gate_unknown_or_closed_means_zero(established, secure, O, C):
    r = delta(0.5, C, ['RING_CHASER'], ctx=context(established=established, secure=secure), O=O)
    assert r['terms']['ambition'] == 0.0


def test_both_ambition_traits_cancel():
    assert delta(1.4, 0.2, ['MONEY_FIRST', 'RING_CHASER'])['reasons'] == ['ambition_conflict']


def test_money_monotonicity_with_fixed_eligibility():
    for traits, C in ((['MONEY_FIRST'], 0.5), (['RING_CHASER'], 0.9), ([], 0.5)):
        us = [base_u(F, C) + delta(F, C, traits)['capped_delta'] for F in [x / 20 for x in range(0, 33)]]
        assert all(b >= a - 1e-12 for a, b in zip(us, us[1:])), traits


def test_contention_monotonicity_with_fixed_certification():
    for traits, Cs in ((['MONEY_FIRST'], [x / 100 for x in range(0, 101)]),
                       (['RING_CHASER'], [x / 100 for x in range(70, 101)]),      # certified throughout
                       (['RING_CHASER'], [x / 100 for x in range(0, 70)])):       # never certified
        us = [base_u(1.0, C) + delta(1.0, C, traits)['capped_delta'] for C in Cs]
        assert all(b >= a - 1e-12 for a, b in zip(us, us[1:])), traits


def test_hard_contender_gate_creates_a_downward_jump_when_money_leads():
    """Documented discontinuity (§2.2): crossing certification while C - F < 0 lowers U."""
    below = base_u(1.2, 0.699) + delta(1.2, 0.699, ['RING_CHASER'])['capped_delta']
    at = base_u(1.2, 0.70) + delta(1.2, 0.70, ['RING_CHASER'])['capped_delta']
    assert at < below


def test_effective_weights_stay_positive():
    k = cp.TRIAL_CONFIG['coefficient']
    assert W_DEFAULT['money'] - k > 0 and W_DEFAULT['team'] - k > 0


def test_pairwise_gap_bound():
    deltas = [delta(F / 10, C / 10, t)['capped_delta'] for F in range(0, 17, 2) for C in range(0, 11, 2)
              for t in (['MONEY_FIRST'], ['RING_CHASER'], [])]
    assert max(deltas) - min(deltas) <= 2 * cp.TRIAL_CONFIG['aggregate_cap'] + 1e-12
    assert all(abs(d) <= cp.TRIAL_CONFIG['aggregate_cap'] + 1e-12 for d in deltas)


def test_utility_is_not_clamped_to_one():
    r = delta(1.6, 0.0, ['MONEY_FIRST'])
    assert base_u(1.6, 0.0) + r['capped_delta'] > 1.0


# ------------------------------------------------------------ relationship ----
def rel_cfg():
    return active(relationship_enabled=True)


GOOD = dict(attachment='established', role_satisfactory=True, treatment_satisfactory=True, unresolved_breach=False,
            relationship_version='rel-v1', attachment_as_of='2031-24', source_event_ids=['ev-1'])


def test_loyal_current_team_only_with_all_gates_known():
    ctx = context(teams={CUR: team(**GOOD), OTHER: team(**GOOD)})
    here = delta(1.0, 0.5, ['LOYAL'], cfg=rel_cfg(), ctx=ctx, team_id=CUR)
    away = delta(1.0, 0.5, ['LOYAL'], cfg=rel_cfg(), ctx=ctx, team_id=OTHER)
    assert here['terms']['attachment'] == pytest.approx(0.035) and away['terms']['attachment'] == 0.0


@pytest.mark.parametrize('change', [{'unresolved_breach': None}, {'unresolved_breach': True},
                                    {'role_satisfactory': None}, {'treatment_satisfactory': False},
                                    {'attachment': 'unknown'}])
def test_loyal_blocked_by_breach_or_unknown(change):
    ctx = context(teams={CUR: team(**dict(GOOD, **change))})
    assert delta(1.0, 0.5, ['LOYAL'], cfg=rel_cfg(), ctx=ctx, team_id=CUR)['terms']['attachment'] == 0.0


def test_home_is_separate_from_attachment():
    ctx = context(teams={OTHER: team(meaningful_home_match=True, home_source_ids=['home-1'])})
    r = delta(1.0, 0.5, ['HOMETOWN_GUY', 'LOYAL'], cfg=rel_cfg(), ctx=ctx, team_id=OTHER)
    assert r['terms'] == {'ambition': 0.0, 'attachment': 0.0, 'home': pytest.approx(0.01), 'grudge': 0.0}


def test_grudge_is_signed_negative_only_when_certified():
    ctx = context(teams={OTHER: team(grudge=True, unresolved_breach=True, source_event_ids=['ev-9'])})
    r = delta(1.0, 0.5, ['GRUDGE_HOLDER'], cfg=rel_cfg(), ctx=ctx)
    assert r['terms']['grudge'] == pytest.approx(-0.03) and r['capped_delta'] == pytest.approx(-0.03)
    assert delta(1.0, 0.5, ['GRUDGE_HOLDER'], cfg=rel_cfg())['terms']['grudge'] == 0.0


def test_relationship_family_disabled_by_default():
    ctx = context(teams={CUR: team(**GOOD)})
    r = delta(1.0, 0.5, ['LOYAL'], ctx=ctx, team_id=CUR)
    assert r['capped_delta'] == 0.0 and 'relationships_disabled' in r['reasons']


# ----------------------------------------------------------------- context ----
def test_context_string_team_keys_normalized_once():
    ctx = context()
    ctx['teams'] = {str(k): v for k, v in ctx['teams'].items()}
    assert set(cp.normalize_contract_context(ctx)['teams']) == {CUR, OTHER}


@pytest.mark.parametrize('mutate', [
    lambda c: c['security'].update(through_season=2032),
    lambda c: c['teams'][CUR].update(attachment='bestie'),
    lambda c: c['teams'][CUR].update(attachment_as_of='2033-1'),
    lambda c: c['security'].update(earned_share=float('nan')),
    lambda c: c.update(revision=None),
    lambda c: c['career'].update(established='yes'),
    lambda c: c.pop('teams'),
])
def test_invalid_context_rejected(mutate):
    c = context()
    mutate(c)
    with pytest.raises(cp.ContractPolicyError):
        cp.validate_contract_context(c)


def test_nan_parts_rejected():
    with pytest.raises(cp.ContractPolicyError):
        delta(float('nan'), 0.5, ['MONEY_FIRST'])


def test_canonical_hash_stable():
    assert cp.canonical_hash({'b': 1, 'a': [1, 2]}) == cp.canonical_hash({'a': [1, 2], 'b': 1})
