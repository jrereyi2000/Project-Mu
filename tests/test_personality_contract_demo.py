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
