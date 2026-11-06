"""Matched-control checks for the optional retirement modifiers (PERSONALITY_RETIREMENT_PLAN.md §2.3, U3).

Each case builds the same player twice through engines.retirement_context, once without the input under test, and
compares engines.retirement_policy decisions. Every number here is synthetic (review-only policy)."""
from dataclasses import replace

import pytest

from engines import retirement_context as rc
from engines import retirement_policy as rp

U = 'u-test'
POL = rp.synthetic_policy()


def bundle(data, coverage='complete'):
    return {'version': rc.EVIDENCE_VERSION, 'universe_id': U, 'source_event_ids': ['src:1'],
            'as_of': {'season': 2027, 'tick': 30}, 'coverage': coverage, 'fingerprint': 'fp', 'data': data}


SNAP = {'universe_id': U, 'person_key': 'person:9', 'identity_status': 'verified', 'player_id': 9, 'pgid': 900,
        'earned_season': 2027, 'decision_season': 2028, 'age': 34, 'age_status': 'known', 'position': 'QB'}
GOAL = {'goal_id': 'pass_yds_record', 'kind': 'record', 'current_value': 70000, 'target_value': 72000,
        'projected_range': [71000, 74500], 'coverage': 'complete', 'source_ids': ['rec:1']}
CONTRACT = {'has_contract': True, 'contract_id': 'c-1', 'offer_ids': [], 'market_open': False,
            'contender_role': True}
ATTACH = {'status': 'enabled', 'allocation': 'career', 'rule_version': 'r1',
          'edges': [{'target': {'kind': 'team', 'id': 3}, 'attachment': {'tier': 'EARNED', 'eligible': True},
                     'breaches': []}]}


def evidence(traits, *, goals=(GOAL,), opp=CONTRACT, seasons=12, earnings=None, attachment=None):
    ev = {'profile': bundle({'status': 'stored', 'traits': list(traits), 'sliders': {}}),
          'opportunity': bundle(dict(opp)), 'career': bundle({'verified_seasons': seasons}),
          'goals': bundle(list(goals)),
          'ability': bundle({'base_overall': 82}), 'health': bundle({'burden': 0.1})}
    if earnings is not None:
        ev['earnings'] = bundle(earnings)
    if attachment is not None:
        ev['attachment'] = bundle(attachment)
    return ev


def decide(ev, *, mode='effects', on=True, policy=POL):
    return rp.evaluate_annual(rc.build_context(SNAP, ev, feature_mode=mode), policy, features_enabled=on)


def neutral(ev):
    return decide(ev, mode='off', on=False)


def test_exact_neutral_control_features_off_and_neutral_preview():
    ev = evidence(['LEGACY_BUILDER', 'RING_CHASER'], earnings={'known_cap_share': '0.4', 'secure': True})
    base = neutral(ev)
    for d in (decide(ev, on=False), decide(ev, mode='neutral_preview', on=False), decide(ev, mode='off', on=True)):
        assert (d.final_p, d.intent, d.draw, d.base) == (base.final_p, base.intent, base.draw, base.base)


def test_legacy_builder_lowers_but_never_zeroes_risk():
    ev = evidence(['LEGACY_BUILDER'])
    on, off = decide(ev), neutral(ev)
    assert 0.0 < on.final_p < off.final_p and on.draw == off.draw
    assert on.modifiers['terms'] == {'legacy_builder': POL.modifier_coefficients['legacy_builder']}


@pytest.mark.parametrize('goal,reason', [
    (dict(GOAL, projected_range=[70500, 71500]), 'no_reachable_recorded_goal'),     # unreachable
    (dict(GOAL, current_value=72000), 'no_reachable_recorded_goal'),                # tied
    (dict(GOAL, current_value=73000), 'no_reachable_recorded_goal'),                # surpassed
    (dict(GOAL, coverage='partial'), 'no_reachable_recorded_goal')])                # uncertain record
def test_legacy_builder_needs_reachable_recorded_goal(goal, reason):
    d = decide(evidence(['LEGACY_BUILDER'], goals=(goal,)))
    assert d.modifiers['excluded']['LEGACY_BUILDER'] == reason
    assert d.final_p == neutral(evidence(['LEGACY_BUILDER'], goals=(goal,))).final_p


def test_legacy_builder_needs_usable_opportunity():
    opp = {'has_contract': False, 'offer_ids': [], 'market_open': True}
    d = decide(evidence(['LEGACY_BUILDER'], opp=opp))
    assert d.modifiers['excluded']['LEGACY_BUILDER'] == 'no_usable_opportunity'


def test_no_label_forced_retirement():
    for traits in (['COASTING'], ['MONEY_FIRST'], ['GUARDED', 'LONER'], ['DIVA']):
        ev = evidence(traits)
        d = decide(ev)
        assert d.final_p == neutral(ev).final_p and d.modifiers['delta'] == 0.0
    assert decide(evidence(['COASTING'])).modifiers['excluded']['COASTING'] == 'no_reviewed_mechanism'


@pytest.mark.parametrize('kw,reason', [
    ({'seasons': 3, 'earnings': {'known_cap_share': '0.5', 'secure': True}}, 'career_not_established'),
    ({'earnings': None}, 'earnings_unknown'),
    ({'earnings': {'known_cap_share': '0.01', 'secure': False}}, 'not_financially_secure'),
    ({'earnings': {'known_cap_share': '0.5', 'secure': True}, 'opp': dict(CONTRACT, contender_role=False)},
     'no_usable_contender_role')])
def test_ring_chaser_needs_career_security_and_role_together(kw, reason):
    d = decide(evidence(['RING_CHASER'], goals=(), **kw))
    assert d.modifiers['excluded']['RING_CHASER'] == reason and 'ring_chaser' not in d.modifiers['terms']


def test_ring_chaser_applies_with_all_three():
    ev = evidence(['RING_CHASER'], goals=(), earnings={'known_cap_share': '0.5', 'secure': True})
    d = decide(ev)
    assert d.modifiers['terms'] == {'ring_chaser': POL.modifier_coefficients['ring_chaser']}


def test_elite_ovr_is_no_substitute_for_security():
    ev = evidence(['RING_CHASER'], goals=())
    ev['ability'] = bundle({'base_overall': 99})
    assert decide(ev).modifiers['excluded']['RING_CHASER'] == 'earnings_unknown'


def test_attachment_needs_enabled_view_earned_tier_and_career_allocation():
    assert decide(evidence([], attachment=ATTACH)).modifiers['terms'] == {
        'attachment': POL.modifier_coefficients['attachment']}
    cases = [(dict(ATTACH, status='neutral'), 'relationships_not_enabled'),
             (dict(ATTACH, allocation='contract'), 'not_allocated_to_career'),
             (dict(ATTACH, allocation=None), 'not_allocated_to_career'),
             (dict(ATTACH, edges=[dict(ATTACH['edges'][0], breaches=[{'source_id': 'b'}])]), 'unresolved_breach'),
             (dict(ATTACH, edges=[dict(ATTACH['edges'][0], attachment={'tier': 'NONE', 'eligible': False})]),
              'no_earned_attachment')]
    for att, reason in cases:
        assert decide(evidence([], attachment=att)).modifiers['excluded']['attachment'] == reason


def test_friend_counts_and_hometown_do_not_substitute():
    att = dict(ATTACH, edges=[{'target': {'kind': 'team', 'id': 3}, 'attachment': {'tier': 'NONE',
                                                                                   'eligible': False},
                               'breaches': [], 'friend_count': 99, 'hometown': {'match': True}}])
    d = decide(evidence(['HOMETOWN_GUY'], attachment=att))
    assert d.modifiers['delta'] == 0.0


def test_aggregate_bound_holds_with_every_modifier():
    huge = replace(POL, modifier_coefficients={k: -10.0 for k in rp.MODIFIER_KEYS})
    ev = evidence(['LEGACY_BUILDER', 'RING_CHASER'], earnings={'known_cap_share': '0.5', 'secure': True},
                  attachment=ATTACH)
    d = decide(ev, policy=huge)
    assert len(d.modifiers['terms']) == 3 and d.modifiers['bounded']
    assert d.modifiers['delta'] == -huge.modifier_bound and d.final_p > 0.0


def test_profile_error_blocks_effects_but_neutral_preview_still_runs():
    ev = evidence(['LEGACY_BUILDER'])
    ev['profile'] = bundle({'status': 'error', 'error': 'bad slider total'})
    assert decide(ev).intent == 'BLOCKED_DATA'
    assert decide(ev, mode='neutral_preview', on=False).intent in ('AVAILABLE', 'RETIRE_INTENT')
