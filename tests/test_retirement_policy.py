"""engines/retirement_policy.py (PERSONALITY_RETIREMENT_PLAN.md §2.1-2.4, U1). Pure, synthetic numbers only."""
from dataclasses import replace
import hashlib
import json

import pytest

from engines import retirement_policy as rp

POL = rp.synthetic_policy()


def ctx(**kw):
    base = dict(universe_id='u-test', person_key='person:42', player_id=42, pgid=4200, earned_season=2027,
                decision_season=2028, age=33, position='WR',
                ability={'coverage': 'complete', 'employability': 0.5, 'ratings_version': 'r1'},
                health={'coverage': 'complete', 'burden': 0.2},
                opportunity={'coverage': 'complete', 'has_contract': True, 'offer_ids': [], 'market_open': False},
                career={'verified_seasons': 10}, earnings={'coverage': 'unknown', 'secure': None},
                goals=(), attachment={}, feature_view=None, evidence={'coverage': 'complete'})
    base.update(kw)
    return rp.RetirementContext(**base)


def test_synthetic_policy_is_review_only_and_production_refuses_it():
    assert POL.review_only is True
    rp.validate_policy(POL)
    with pytest.raises(rp.RetirementPolicyError, match='review-only'):
        rp.validate_policy(POL, production=True)
    with pytest.raises(rp.RetirementPolicyError, match='review-only'):
        rp.evaluate_annual(ctx(), POL, production=True)
    approved = replace(POL, version='jr-approved-test', review_only=False)
    assert rp.evaluate_annual(ctx(), approved, production=True).policy_version == 'jr-approved-test'


def test_policy_dict_roundtrip_and_unknown_keys_refused():
    d = rp.policy_to_dict(POL)
    assert rp.policy_from_dict(json.loads(json.dumps(d))) == POL
    for bad in ('max_age', 'annual_quota'):
        with pytest.raises(rp.RetirementPolicyError, match='unknown policy keys'):
            rp.policy_from_dict(dict(d, **{bad: 40}))
    with pytest.raises(rp.RetirementPolicyError, match='missing'):
        rp.policy_from_dict({k: v for k, v in d.items() if k != 'curves'})


def test_policy_validation_rejects_bad_curves_and_terms():
    with pytest.raises(rp.RetirementPolicyError):
        rp.validate_policy(replace(POL, curves={'QB': ((30, -1.0),)}))
    with pytest.raises(rp.RetirementPolicyError):
        rp.validate_policy(replace(POL, curves=dict(POL.curves, QB=((30, -1.0), (25, 0.0)))))
    with pytest.raises(rp.RetirementPolicyError):
        rp.validate_policy(replace(POL, term_weights={'ability': 1.0}))
    with pytest.raises(rp.RetirementPolicyError):
        rp.validate_policy(replace(POL, position_groups={'QB': 'NOPE'}))


@pytest.mark.parametrize('field,value,missing', [
    ('age', None, 'age'), ('position', '', 'position'), ('position', 'XX', 'position:unsupported'),
    ('person_key', '', 'person_key'), ('universe_id', '', 'universe_id')])
def test_missing_essentials_block_with_none_not_zero(field, value, missing):
    d = rp.evaluate_annual(ctx(**{field: value}), POL)
    assert d.intent == 'BLOCKED_DATA'
    assert d.final_p is None and d.base['p'] is None
    assert missing in d.base['missing']


def test_missing_optional_evidence_is_zero_term_with_warning():
    b = rp.baseline_probability(ctx(ability={}, health={'coverage': 'unknown'}, opportunity={}), POL)
    assert b.eligible and b.p is not None
    assert b.terms['ability'] == b.terms['health'] == b.terms['opportunity'] == 0.0
    assert {'ability_unknown', 'health_unknown', 'opportunity_unknown'} <= set(b.warnings)


def test_terms_are_bounded():
    wide = replace(POL, term_weights={'ability': 50.0, 'health': 50.0, 'contract': 50.0, 'no_offer': 50.0})
    b = rp.baseline_probability(ctx(ability={'coverage': 'complete', 'employability': 0.0},
                                    health={'coverage': 'complete', 'burden': 1.0}), wide)
    for k in ('ability', 'health', 'opportunity'):
        assert abs(b.terms[k]) <= wide.term_bound


def test_age_and_position_sensitivity():
    ps = [rp.baseline_probability(ctx(age=a), POL).p for a in (28, 31, 34, 37)]
    assert ps == sorted(ps) and ps[0] < ps[-1]
    rb = rp.baseline_probability(ctx(position='HB', age=31), POL).p
    qb = rp.baseline_probability(ctx(position='QB', age=31), POL).p
    assert rb > qb


def test_no_age_cliff_or_forced_retirement():
    ps = [rp.baseline_probability(ctx(age=a, position='QB'), POL).p for a in range(30, 61)]
    assert all(0.0 < p < 1.0 for p in ps)
    jumps = [b - a for a, b in zip(ps, ps[1:])]
    assert max(jumps) < 0.2                               # smooth: no single-year cliff
    assert not hasattr(POL, 'max_age') and not hasattr(POL, 'annual_quota')


def test_ability_health_opportunity_directions():
    lo = rp.baseline_probability(ctx(ability={'coverage': 'complete', 'employability': 0.9}), POL).p
    hi = rp.baseline_probability(ctx(ability={'coverage': 'complete', 'employability': 0.1}), POL).p
    assert hi > lo
    hurt = rp.baseline_probability(ctx(health={'coverage': 'complete', 'burden': 0.9}), POL).p
    well = rp.baseline_probability(ctx(health={'coverage': 'complete', 'burden': 0.0}), POL).p
    assert hurt > well
    no_offer = rp.baseline_probability(ctx(opportunity={'coverage': 'complete', 'has_contract': False,
                                                        'offer_ids': [], 'market_open': True}), POL)
    contract = rp.baseline_probability(ctx(), POL)
    assert no_offer.p > contract.p


def test_no_offers_before_market_open_is_not_rejection():
    closed = rp.baseline_probability(ctx(opportunity={'coverage': 'complete', 'has_contract': False,
                                                      'offer_ids': [], 'market_open': False}), POL)
    assert closed.terms['opportunity'] == 0.0 and 'market_not_open' in closed.warnings
    need_only = rp.baseline_probability(ctx(opportunity={'coverage': 'complete', 'has_contract': False,
                                                         'offer_ids': [], 'market_open': False,
                                                         'team_need': 0.99}), POL)
    assert need_only.terms['opportunity'] == 0.0


def test_young_unsigned_exit_is_separate_from_retirement():
    d = rp.evaluate_annual(ctx(age=22, opportunity={'coverage': 'complete', 'has_contract': False,
                                                    'market_open': True}), POL)
    assert d.intent == 'AVAILABLE' and d.base['eligible'] is False and d.final_p == 0.0
    assert 'young_unsigned_exit_review_separately' in d.warnings


def test_draw_matches_documented_algorithm():
    raw = json.dumps(['mu-retirement-v1', 'u-test', 'person:42', 2027, 1], separators=(',', ':'))
    want = int.from_bytes(hashlib.sha256(raw.encode('utf-8')).digest()[:8], 'big') / 2 ** 64
    assert rp.annual_draw('u-test', 'person:42', 2027, 1) == want
    assert 0.0 <= want < 1.0
    assert rp.draw_hex('u-test', 'person:42', 2027, 1) == hashlib.sha256(raw.encode()).digest()[:8].hex()


def test_draw_ignores_rename_trade_order_and_policy():
    a = rp.evaluate_annual(ctx(), POL)
    b = rp.evaluate_annual(ctx(player_id=999, pgid=1, opportunity={'coverage': 'complete', 'has_contract': True,
                                                                   'team_id': 7}), POL)
    c = rp.evaluate_annual(ctx(), replace(POL, version='other-review', term_bound=1.0))
    assert a.draw == b.draw == c.draw and a.decision_id == b.decision_id == c.decision_id
    batch = [rp.evaluate_annual(ctx(person_key=f'p:{i}'), POL).draw for i in range(5)]
    assert batch == [rp.evaluate_annual(ctx(person_key=f'p:{i}'), POL).draw for i in reversed(range(5))][::-1]


def test_universe_isolation_and_season_change():
    assert rp.annual_draw('u-a', 'p', 2027, 1) != rp.annual_draw('u-b', 'p', 2027, 1)
    assert rp.annual_draw('u-a', 'p', 2027, 1) != rp.annual_draw('u-a', 'p', 2028, 1)
    assert rp.decision_id_for('u-a', 'p', 2027) != rp.decision_id_for('u-b', 'p', 2027)


def test_same_draw_across_checkpoints_threshold_moves():
    o = rp.evaluate_annual(ctx(opportunity={'coverage': 'complete', 'has_contract': False, 'market_open': False}),
                           POL, checkpoint='OPEN')
    m = rp.evaluate_annual(ctx(opportunity={'coverage': 'complete', 'has_contract': False, 'market_open': True,
                                            'offer_ids': []}), POL, checkpoint='MARKET_CLOSE')
    assert o.draw == m.draw and m.final_p > o.final_p
    with pytest.raises(rp.RetirementPolicyError):
        rp.evaluate_annual(ctx(), POL, checkpoint='WEEK_5')


def test_intent_follows_draw_vs_probability_and_replays_exactly():
    for i in range(200):
        d = rp.evaluate_annual(ctx(person_key=f'p:{i}', age=36), POL)
        assert d.intent == ('RETIRE_INTENT' if d.draw < d.final_p else 'AVAILABLE')
        assert rp.evaluate_annual(ctx(person_key=f'p:{i}', age=36), POL) == d


def test_features_off_or_missing_profile_is_exactly_neutral():
    view = {'status': 'stored', 'traits': ['LEGACY_BUILDER', 'RING_CHASER'], 'sliders': {}}
    goal = {'goal_id': 'g', 'kind': 'record', 'current_value': 900, 'target_value': 1000,
            'projected_range': [950, 1100], 'coverage': 'complete'}
    off = rp.evaluate_annual(ctx(feature_view=view, goals=(goal,)), POL, features_enabled=False)
    neutral = rp.evaluate_annual(ctx(), POL)
    missing = rp.evaluate_annual(ctx(feature_view=None), POL, features_enabled=True)
    empty = rp.evaluate_annual(ctx(feature_view={'status': 'stored', 'traits': [], 'sliders': {}}), POL,
                               features_enabled=True)
    for d in (off, missing, empty):
        assert (d.final_p, d.intent, d.draw) == (neutral.final_p, neutral.intent, neutral.draw)
    on = rp.evaluate_annual(ctx(feature_view=view, goals=(goal,)), POL, features_enabled=True)
    assert on.final_p < neutral.final_p and on.base == neutral.base


def test_feature_view_error_blocks_effects_enabled_only():
    bad = {'status': 'error'}
    assert rp.evaluate_annual(ctx(feature_view=bad), POL, features_enabled=True).intent == 'BLOCKED_DATA'
    assert rp.evaluate_annual(ctx(feature_view=bad), POL).intent in ('AVAILABLE', 'RETIRE_INTENT')


def test_modifier_aggregate_is_bounded_and_never_zero():
    huge = replace(POL, modifier_coefficients={'legacy_builder': -50.0, 'ring_chaser': -50.0, 'attachment': -50.0})
    view = {'status': 'stored', 'traits': ['LEGACY_BUILDER'], 'sliders': {}}
    goal = {'goal_id': 'g', 'kind': 'title', 'current_value': 0, 'target_value': 1, 'projected_range': [0, 1],
            'coverage': 'complete'}
    d = rp.evaluate_annual(ctx(feature_view=view, goals=(goal,), age=40), huge, features_enabled=True)
    assert d.modifiers['bounded'] and abs(d.modifiers['delta']) == huge.modifier_bound
    assert 0.0 < d.final_p


def test_decision_ids_and_hash_are_stable_text():
    d = rp.evaluate_annual(ctx(), POL)
    assert len(d.input_hash) == 64 and d.decision_id.count('-') == 4
    assert rp.to_dict(d)['seed_algorithm'] == rp.SEED_ALGORITHM
