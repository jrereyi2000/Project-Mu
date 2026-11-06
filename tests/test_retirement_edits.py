"""pipeline/retirement_edits.py (PERSONALITY_RETIREMENT_PLAN.md §2.6, U5). Synthetic fixtures only."""
from copy import deepcopy

import pytest

from pipeline import retirement_edits as re_

SNAP = {'source_save_hash': 'sha:in', 'stage': 'OFFSEASON_START', 'schema_version': 'm27',
        'players': [{'pgid': 1, 'status': 'Retired'}, {'pgid': 2, 'status': 'Signed'},
                    {'pgid': 3, 'status': 'Deleted'}],
        'baseline': {'1': {'status': 'Signed', 'team_index': 7}, '2': {'status': 'Signed', 'team_index': 3}},
        'pending': {}}
AUTH = {'version': re_.AUTHORITY_VERSION, 'fixture_id': 'fx-1', 'source_save_hash': 'sha:in', 'schema_version': 'm27',
        'stages': ['OFFSEASON_START'], 'exclusive_owner': True,
        'actions': {'KEEP_NATIVE_RETIREE': {'proven': True, 'proof_id': 'proof-keep',
                                            'fields': ['ContractStatus', 'TeamIndex']},
                    'RETIRE_ACTIVE': {'proven': True, 'proof_id': 'proof-retire', 'fields': ['ContractStatus']}}}
KEEP = {'decision_id': 'd1', 'pgid': 1, 'action': 'KEEP_NATIVE_RETIREE', 'state': 'AVAILABLE'}
RETIRE = {'decision_id': 'd2', 'pgid': 2, 'action': 'RETIRE_ACTIVE', 'state': 'RETIRE_PENDING',
          'operation_id': 'op-2'}


def reasons(plan, decision_id):
    return next(c['reasons'] for c in plan['conflicts'] if c['decision_id'] == decision_id)


@pytest.mark.parametrize('authority', [None, {}, dict(AUTH, version='v0')])
def test_default_deny_without_authority(authority):
    plan = re_.plan_retirement_edits(SNAP, [KEEP, RETIRE], authority)
    assert plan['edits'] == {} and plan['expected'] == {} and len(plan['conflicts']) == 2
    assert any(r.startswith('authority_') for r in reasons(plan, 'd1'))


def test_proven_authority_plans_both_actions():
    plan = re_.plan_retirement_edits(SNAP, [KEEP, RETIRE], AUTH)
    assert plan['conflicts'] == []
    assert plan['edits'] == {'players': [{'pgid': 1, 'set': {'ContractStatus': 'Signed', 'TeamIndex': 7}},
                                         {'pgid': 2, 'set': {'ContractStatus': 'Retired'}}]}
    assert plan['preconditions']['source_save_hash'] == 'sha:in'
    assert plan['preconditions']['players'][1]['proof_id'] == 'proof-keep'


def test_restore_keeps_baseline_contract_status_not_free_agent():
    plan = re_.plan_retirement_edits(SNAP, [KEEP], AUTH)
    assert plan['expected'][1]['ContractStatus'] == 'Signed'


def test_source_hash_stage_schema_and_owner_mismatch():
    for auth, reason in ((dict(AUTH, source_save_hash='sha:other'), 'source_hash_mismatch'),
                         (dict(AUTH, stages=['PRESEASON']), 'stage_mismatch:OFFSEASON_START'),
                         (dict(AUTH, schema_version='m26'), 'schema_mismatch'),
                         (dict(AUTH, exclusive_owner=False), 'restoration_owner_not_exclusive')):
        plan = re_.plan_retirement_edits(SNAP, [KEEP], auth)
        assert reason in reasons(plan, 'd1') and plan['edits'] == {}


def test_restore_and_retire_capabilities_are_separate():
    keep_only = deepcopy(AUTH)
    keep_only['actions']['RETIRE_ACTIVE'] = {'proven': False}
    plan = re_.plan_retirement_edits(SNAP, [KEEP, RETIRE], keep_only)
    assert reasons(plan, 'd2') == ['action_unproven:RETIRE_ACTIVE']
    assert plan['edits']['players'] == [{'pgid': 1, 'set': {'ContractStatus': 'Signed', 'TeamIndex': 7}}]
    retire_only = deepcopy(AUTH)
    del retire_only['actions']['KEEP_NATIVE_RETIREE']
    assert 'action_unproven:KEEP_NATIVE_RETIREE' in reasons(re_.plan_retirement_edits(SNAP, [KEEP], retire_only), 'd1')


def test_deleted_missing_baseline_and_ambiguous_pgid():
    deleted = dict(KEEP, decision_id='d3', pgid=3)
    plan = re_.plan_retirement_edits(SNAP, [deleted], AUTH)
    assert {'deleted_unrecoverable', 'baseline_missing'} <= set(reasons(plan, 'd3'))
    missing = dict(KEEP, decision_id='d9', pgid=9)
    assert 'pgid_missing' in reasons(re_.plan_retirement_edits(SNAP, [missing], AUTH), 'd9')
    dup = deepcopy(SNAP)
    dup['players'].append({'pgid': 1, 'status': 'Retired'})
    assert 'pgid_ambiguous' in reasons(re_.plan_retirement_edits(dup, [KEEP], AUTH), 'd1')
    twice = re_.plan_retirement_edits(SNAP, [KEEP, dict(KEEP, decision_id='d1b')], AUTH)
    assert all('pgid_in_several_decisions' in c['reasons'] for c in twice['conflicts'])


@pytest.mark.parametrize('pending,move', [({'release': [1]}, 'overlaps_release'),
                                          ({'sign': [{'pgid': 1}]}, 'overlaps_sign'),
                                          ({'trade': [{'pgids': [1, 5]}]}, 'overlaps_trade'),
                                          ({'players': [{'pgid': 1, 'set': {'Age': 30}}]}, 'overlaps_player_edit')])
def test_overlapping_moves_conflict(pending, move):
    snap = dict(SNAP, pending=pending)
    assert move in reasons(re_.plan_retirement_edits(snap, [KEEP], AUTH), 'd1')


def test_field_authority_and_status_and_state_checks():
    narrow = deepcopy(AUTH)
    narrow['actions']['KEEP_NATIVE_RETIREE']['fields'] = ['ContractStatus']
    assert 'field_unauthorized:TeamIndex' in reasons(re_.plan_retirement_edits(SNAP, [KEEP], narrow), 'd1')
    assert 'not_natively_retired:Signed' in reasons(
        re_.plan_retirement_edits(SNAP, [dict(KEEP, pgid=2, decision_id='dx')], AUTH), 'dx')
    assert 'state_does_not_allow_keep:RETIRE_INTENT' in reasons(
        re_.plan_retirement_edits(SNAP, [dict(KEEP, state='RETIRE_INTENT')], AUTH), 'd1')
    assert 'state_not_retire_pending:RETIRE_INTENT' in reasons(
        re_.plan_retirement_edits(SNAP, [dict(RETIRE, state='RETIRE_INTENT')], AUTH), 'd2')
    assert 'not_active:Retired' in reasons(
        re_.plan_retirement_edits(SNAP, [dict(RETIRE, pgid=1, decision_id='dy')], AUTH), 'dy')
    assert 'unknown_action:RELEASE' in reasons(
        re_.plan_retirement_edits(SNAP, [dict(KEEP, action='RELEASE')], AUTH), 'd1')


def test_baseline_must_be_active_for_restore():
    snap = deepcopy(SNAP)
    snap['baseline']['1'] = {'status': 'Retired', 'team_index': 32}
    assert 'baseline_not_active' in reasons(re_.plan_retirement_edits(snap, [KEEP], AUTH), 'd1')


def test_planner_is_pure_and_deterministic():
    snap, auth = deepcopy(SNAP), deepcopy(AUTH)
    a = re_.plan_retirement_edits(snap, [RETIRE, KEEP], auth)
    b = re_.plan_retirement_edits(snap, [KEEP, RETIRE], auth)
    assert a == b and snap == SNAP and auth == AUTH
    src = open(re_.__file__).read()
    for forbidden in ('subprocess', 'psycopg', 'open(', 'save_writer.mjs\'', 'os.environ'):
        assert forbidden not in src
