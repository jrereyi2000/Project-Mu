"""Offline proposed modifier checks; no production engine integration."""
import pytest
from tools.personality_contract_demo import fixtures, evaluate, build_report, ring_eligibility


def scenario(key):
    return next(s for s in fixtures() if s['id']==key)


def test_close_choice_changes_only_with_sufficient_modifier():
    s=scenario('tradeoff')
    assert evaluate(s,'neutral')['choice']=='B'
    assert evaluate(s,'money')['choice']=='A'
    assert evaluate(s,'ring')['rows']==evaluate(s,'neutral')['rows']
    assert evaluate(s,'money',cap=.06)['choice']=='B'
    assert evaluate(s,'money',cap=.07)['choice']=='A'
    assert evaluate(s,'money',cap=0)['choice']=='B'


def test_role_rule_cannot_be_bought_with_personality():
    for p in ('neutral','money','ring'):
        result=evaluate(scenario('backup'),p,coefficient=.2,cap=.12)
        assert result['rows'][0]['status']=='Role rule blocks'
        assert result['choice']=='B'


def test_attachment_replaces_count_and_hometown_is_distinct():
    s=scenario('loyalty')
    assert s['offers'][0]['score']['contention']==s['offers'][1]['score']['contention']
    assert evaluate(s,'neutral',attachment_tier='weak')['choice']=='B'
    r=evaluate(s,'neutral',attachment_tier='established')
    assert r['choice']=='A'
    assert r['rows'][0]['utility']-r['rows'][1]['utility']==pytest.approx(.0035)
    assert r['rows'][0]['hometown']==0
    r=evaluate(scenario('stacking'),'neutral',attachment_tier='strong')
    assert r['rows'][0]['attachment']==.055
    assert r['rows'][0]['hometown']==.01
    assert r['rows'][0]['raw']==pytest.approx(.065)
    assert evaluate(scenario('stacking'),'neutral',attachment_tier='weak')['choice']=='B'
    assert evaluate(scenario('stacking'),'money',cap=.04)['rows'][0]['delta']==.04


def test_ring_context_neither_requires_nor_follows_elite_rating():
    s=scenario('tradeoff')
    assert not ring_eligibility(s)
    assert not ring_eligibility(s,'established')
    assert ring_eligibility(s,'secure')  # financially secure role player can qualify
    assert ring_eligibility(scenario('veteran'))
    assert not ring_eligibility(scenario('backup'))  # no viable contender role
    assert not ring_eligibility(scenario('loyalty'),'secure')  # no contender offer
    assert evaluate(scenario('veteran'),'ring')['rows'][0]['preference']<0


def test_sweep_replay_bounds_and_zero_cap():
    report=build_report()
    assert report==build_report()
    assert len(report['sweep'])==525
    for s in report['scenarios']:
        for p in ('neutral','money','ring'):
            assert all(r['utility']==r['base'] for r in evaluate(s,p,cap=0)['rows'])
            for attachment in ('none','weak','established','strong'):
                assert all(abs(r['delta'])<=.04 for r in evaluate(s,p,cap=.04,attachment_tier=attachment)['rows'])


def test_financial_gap_threshold_and_default_controls():
    for profile in ('neutral','money','ring'):
        assert evaluate(scenario('clear_money'), profile)['choice']=='A'
    assert evaluate(scenario('weak'),'neutral')['choice']=='No immediate signing'
    assert evaluate(scenario('weak'),'money')['choice']=='B'


def test_preferences_never_invert_base_weights(monkeypatch):
    """CAP_RELATIVE_CONTRACTS_PLAN §2.6, S8: preference = k (financial - contention) x direction is a weight shift.
    Money First moves the team weight 0.20 -> 0.20 - k; Ring Chaser moves the money weight 0.45 -> 0.45 - k. For the
    trial k values neither weight reaches zero, so a trait can lean but never reverse the base preference."""
    from engines import free_agency_engine as fa
    import tools.personality_contract_demo as demo
    caps = sorted({r['cap'] for r in build_report()['sweep']})
    assert caps == [0, .02, .04, .08, .12]
    # Offer pairs scored through the demo's own fixtures() path: (pay, contention, depth, context).
    equal_money = [(f'money_{i}', 'Equal money', 75, [(pay, .30, 1, 0), (pay, .85, 1, 0)])
                   for i, pay in enumerate((.70, 1.00, 1.30, 1.60))]
    equal_contention = [(f'ring_{i}', 'Equal contention', 85, [(.80, con, 1, 0), (1.40, con, 1, 0)])
                        for i, con in enumerate((.75, .90))]
    monkeypatch.setattr(demo, 'SCENARIOS', equal_money + equal_contention)
    scenarios = demo.fixtures()
    for k in (.05, .10, .15):
        assert k < fa.W_DEFAULT['team'] < fa.W_DEFAULT['money']
        for cap in caps:
            for s in scenarios:
                lo, hi = s['offers']
                if s['id'].startswith('money_'):
                    assert lo['score']['financial'] == hi['score']['financial']
                    assert hi['score']['contention'] > lo['score']['contention']
                    rows = evaluate(s, 'money', coefficient=k, cap=cap)['rows']
                else:
                    assert lo['score']['contention'] == hi['score']['contention']
                    assert hi['score']['financial'] > lo['score']['financial']
                    result = evaluate(s, 'ring', coefficient=k, cap=cap, career_context='secure')
                    assert result['ring_active']
                    rows = result['rows']
                r_lo, r_hi = rows
                assert r_hi['utility'] >= r_lo['utility'] - 1e-12, (s['id'], k, cap)
                if not all(abs(r['raw']) >= cap for r in rows):  # the cap isn't binding on both offers
                    assert r_hi['utility'] > r_lo['utility'], (s['id'], k, cap)
