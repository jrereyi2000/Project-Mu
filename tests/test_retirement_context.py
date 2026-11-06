"""engines/retirement_context.py (PERSONALITY_RETIREMENT_PLAN.md §2.1, U3). Pure, synthetic evidence."""
import pytest

from engines import retirement_context as rc
from engines import retirement_policy as rp

U = 'u-test'


def bundle(data, coverage='complete', season=2027, universe=U, version=rc.EVIDENCE_VERSION, ids=('src:1',)):
    return {'version': version, 'universe_id': universe, 'source_event_ids': list(ids),
            'as_of': {'season': season, 'tick': 30}, 'coverage': coverage, 'fingerprint': 'fp:' + str(season),
            'data': data}


def snap(**kw):
    s = {'universe_id': U, 'person_key': 'person:7', 'identity_status': 'verified', 'player_id': 7, 'pgid': 700,
         'earned_season': 2027, 'decision_season': 2028, 'age': 33, 'age_status': 'known', 'position': 'WR'}
    s.update(kw)
    return s


def test_minimal_snapshot_builds_neutral_context_with_unknowns():
    c = rc.build_context(snap(), {}, feature_mode='off')
    assert isinstance(c, rp.RetirementContext)
    assert c.ability['coverage'] == c.health['coverage'] == c.opportunity['coverage'] == 'unknown'
    assert c.earnings == {'known_cap_share': None, 'coverage': 'unknown', 'secure': None, 'source_version': None,
                          'source_ids': []}
    assert c.feature_view is None and c.goals == () and c.attachment == {}
    d = rp.evaluate_annual(c, rp.synthetic_policy())
    assert d.intent in ('AVAILABLE', 'RETIRE_INTENT') and d.final_p is not None


def test_unknown_is_not_zero():
    unknown = rc.build_context(snap(), {'health': bundle({}, coverage='unknown')}, feature_mode='off')
    zero = rc.build_context(snap(), {'health': bundle({'burden': 0.0})}, feature_mode='off')
    assert 'burden' not in unknown.health and zero.health['burden'] == 0.0
    pol = rp.synthetic_policy()
    assert 'health_unknown' in rp.baseline_probability(unknown, pol).warnings
    assert 'health_unknown' not in rp.baseline_probability(zero, pol).warnings


def test_health_burden_from_recorded_games_only():
    c = rc.build_context(snap(), {'health': bundle({'missed_games': 6, 'games_possible': 17})}, feature_mode='off')
    assert c.health['burden'] == pytest.approx(6 / 17)
    p = rc.build_context(snap(), {'health': bundle({'missed_games': 6}, coverage='partial')}, feature_mode='off')
    assert 'burden' not in p.health


def test_form_and_wear_excluded_from_ability():
    c = rc.build_context(snap(), {'ability': bundle({'base_overall': 80, 'form_delta': -3, 'current_overall': 77,
                                                     'mileage': 9000})}, feature_mode='off')
    assert set(c.ability) == {'base_overall', 'coverage', 'employability'}
    assert c.ability['employability'] == pytest.approx((80 - 55) / 30)
    assert any(w.startswith('ability_excluded:') for w in c.evidence['warnings'])


def test_need_alone_is_not_an_offer():
    c = rc.build_context(snap(), {'opportunity': bundle({'has_contract': False, 'team_need': 0.9,
                                                         'market_open': True})}, feature_mode='off')
    assert 'team_need' not in c.opportunity and c.opportunity['offer_ids'] == []
    with pytest.raises(rc.ContextError):
        rc.build_context(snap(), {'opportunity': bundle({'offer_ids': [None]})}, feature_mode='off')


def test_partial_low_earnings_are_not_insecurity():
    c = rc.build_context(snap(), {'earnings': bundle({'known_cap_share': '0.020000', 'secure': False},
                                                     coverage='partial')}, feature_mode='off')
    assert c.earnings['secure'] is None
    full = rc.build_context(snap(), {'earnings': bundle({'known_cap_share': '0.020000', 'secure': False})},
                            feature_mode='off')
    assert full.earnings['secure'] is False


def test_earnings_from_ledger_three_states():
    assert rc.earnings_from_ledger(None, source_version='v', source_ids=[])['secure'] is None
    partial = rc.earnings_from_ledger({'share': 0.02, 'first_known': 2030, 'complete': False},
                                      source_version='v', source_ids=['c:1'])
    assert partial['secure'] is None and partial['coverage'] == 'partial' and partial['known_cap_share'] == '0.020000'
    rich = rc.earnings_from_ledger({'share': 0.5, 'first_known': 2030, 'complete': False}, source_version='v',
                                   source_ids=[])
    assert rich['secure'] is True
    poor = rc.earnings_from_ledger({'share': 0.02, 'first_known': 2020, 'complete': True}, source_version='v',
                                   source_ids=[])
    assert poor['secure'] is False


def test_feature_modes_and_profile_permissions():
    view = {'status': 'stored', 'traits': ['LEGACY_BUILDER'], 'sliders': {}, 'persona_id': 'p'}
    ev = {'profile': bundle(view)}
    assert rc.build_context(snap(), ev, feature_mode='off').feature_view is None
    assert rc.build_context(snap(), ev, feature_mode='neutral_preview').feature_view['traits'] == ['LEGACY_BUILDER']
    assert rc.build_context(snap(), ev, feature_mode='effects').feature_view['status'] == 'stored'
    missing = rc.build_context(snap(), {'profile': bundle({'status': 'missing'})}, feature_mode='effects')
    assert missing.feature_view is None
    with pytest.raises(rc.ContextError):
        rc.build_context(snap(), {'profile': bundle({'status': 'weird'})}, feature_mode='effects')
    with pytest.raises(rc.ContextError):
        rc.build_context(snap(), {}, feature_mode='on')


def test_profile_view_uses_core_and_reports_errors():
    assert rc.profile_view(None)['status'] == 'missing'
    assert rc.profile_view({'persona_id': 'broken'})['status'] == 'error'


def test_future_evidence_refused():
    with pytest.raises(rc.ContextError, match='after decision season'):
        rc.build_context(snap(), {'ability': bundle({'base_overall': 70}, season=2029)}, feature_mode='off')


def test_cross_universe_and_version_refused():
    with pytest.raises(rc.ContextError, match='universe'):
        rc.build_context(snap(), {'health': bundle({}, universe='u-other')}, feature_mode='off')
    with pytest.raises(rc.ContextError, match='version'):
        rc.build_context(snap(), {'health': bundle({}, version='v0')}, feature_mode='off')
    with pytest.raises(rc.ContextError, match='domains'):
        rc.build_context(snap(), {'friends': bundle({})}, feature_mode='off')


@pytest.mark.parametrize('kw', [{'identity_status': 'ambiguous'}, {'person_key': ''}, {'universe_id': None},
                                {'player_id': '7'}, {'decision_season': 2026}])
def test_identity_and_shape_errors(kw):
    with pytest.raises(rc.ContextError):
        rc.build_context(snap(**kw), {}, feature_mode='off')


def test_ambiguous_age_blocks_the_decision():
    c = rc.build_context(snap(age_status='unverified'), {}, feature_mode='off')
    assert c.age is None and 'age_ambiguous' in c.evidence['warnings']
    assert rp.evaluate_annual(c, rp.synthetic_policy()).intent == 'BLOCKED_DATA'


def test_goal_coverage_follows_bundle():
    g = {'goal_id': 'g1', 'kind': 'record', 'current_value': 10, 'target_value': 12, 'projected_range': [11, 14],
         'coverage': 'complete'}
    partial = rc.build_context(snap(), {'goals': bundle([g], coverage='partial')}, feature_mode='off')
    assert partial.goals[0]['coverage'] == 'partial' and rp.goal_status(partial.goals[0]) == 'unknown'
    full = rc.build_context(snap(), {'goals': bundle([g])}, feature_mode='off')
    assert rp.goal_status(full.goals[0]) == 'reachable'
    with pytest.raises(rc.ContextError):
        rc.build_context(snap(), {'goals': bundle([{'goal_id': 'x', 'kind': 'fame'}])}, feature_mode='off')


def test_attachment_drops_friend_counts_and_hometown():
    data = {'status': 'enabled', 'allocation': 'career', 'rule_version': 'r1',
            'edges': [{'target': {'kind': 'team', 'id': 3}, 'attachment': {'tier': 'EARNED', 'eligible': True},
                       'breaches': [], 'friend_count': 40, 'hometown': {'match': True}}]}
    c = rc.build_context(snap(), {'attachment': bundle(data)}, feature_mode='off')
    assert 'friend_count' not in c.attachment['edges'][0] and 'hometown' not in c.attachment['edges'][0]
    with pytest.raises(rc.ContextError):
        rc.build_context(snap(), {'attachment': bundle(dict(data, status='friends'))}, feature_mode='off')


def test_evidence_summary_and_fingerprint_are_deterministic():
    ev = {'ability': bundle({'base_overall': 70}), 'health': bundle({'burden': 0.1})}
    a = rc.build_context(snap(), ev, feature_mode='off')
    b = rc.build_context(snap(), dict(reversed(list(ev.items()))), feature_mode='off')
    assert a.evidence['fingerprint'] == b.evidence['fingerprint']
    assert a.evidence['domains']['ability']['source_event_ids'] == ['src:1']
