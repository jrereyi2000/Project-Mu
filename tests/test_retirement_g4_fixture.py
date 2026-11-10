"""tests for tools/retirement_g4_fixture.py (G4 restore-proof fixtures, offline)."""
import json

import pytest

from tools import retirement_g4_fixture as fx


def _snapshot(players, stage=1, week=0, year=2026):
    return {'season': {'stage': 'OffSeason', 'offseason_stage': stage, 'week_index': week, 'calendar_year': year},
            'players': players}


def _write(tmp_path, name, obj):
    p = tmp_path / name
    p.write_text(json.dumps(obj), encoding='utf-8')
    return p


def _branch(tmp_path, snapshot, players, stage='OFFSEASON_START'):
    snap = _write(tmp_path, 'snap.json', snapshot)
    save = tmp_path / 'save'
    save.write_bytes(b'not a real save')
    return {'name': 'b', 'stage': stage, 'snapshot': str(snap), 'source_save': str(save),
            'players': players}


def test_plans_restore_edits_for_each_player(tmp_path):
    snap = _snapshot([{'pgid': 1, 'status': 'Retired', 'team_index': 32},
                      {'pgid': 2, 'status': 'Retired', 'team_index': 32}])
    branch = _branch(tmp_path, snap, [
        {'pgid': 1, 'baseline_status': 'Signed', 'baseline_team_index': 26},
        {'pgid': 2, 'baseline_status': 'Signed', 'baseline_team_index': 12}])
    out = fx.plan_branch(branch, fixture_id='g4-test', schema_version=6)
    assert out['conflicts'] == []
    assert {(e['pgid'], e['set']['ContractStatus'], e['set']['TeamIndex']) for e in out['edits']['players']} == {
        (1, 'Signed', 26), (2, 'Signed', 12)}
    assert out['source_save_hash'] == fx._sha256(str(tmp_path / 'save'))


def test_rejects_a_snapshot_at_another_stop(tmp_path):
    snap = _snapshot([{'pgid': 1, 'status': 'Retired', 'team_index': 32}], stage=1, week=2)
    branch = _branch(tmp_path, snap, [{'pgid': 1, 'baseline_status': 'Signed', 'baseline_team_index': 3}],
                     stage='RESIGN')
    with pytest.raises(ValueError, match='snapshot is FREE_AGENCY'):
        fx.plan_branch(branch, fixture_id='g4-test', schema_version=6)


def test_rejects_a_player_madden_did_not_retire(tmp_path):
    snap = _snapshot([{'pgid': 1, 'status': 'Signed', 'team_index': 3}])
    branch = _branch(tmp_path, snap, [{'pgid': 1, 'baseline_status': 'Signed', 'baseline_team_index': 3}])
    with pytest.raises(ValueError, match='not Retired'):
        fx.plan_branch(branch, fixture_id='g4-test', schema_version=6)


def test_rejects_a_retired_baseline(tmp_path):
    snap = _snapshot([{'pgid': 1, 'status': 'Retired', 'team_index': 32}])
    branch = _branch(tmp_path, snap, [{'pgid': 1, 'baseline_status': 'Retired', 'baseline_team_index': 3}])
    with pytest.raises(ValueError, match='not active'):
        fx.plan_branch(branch, fixture_id='g4-test', schema_version=6)


def test_main_writes_edits_and_plans(tmp_path):
    snap = _snapshot([{'pgid': 7, 'status': 'Retired', 'team_index': 32}])
    branch = _branch(tmp_path, snap, [{'pgid': 7, 'baseline_status': 'Signed', 'baseline_team_index': 4}])
    config = _write(tmp_path, 'cfg.json', {'fixture_id': 'g4-test', 'schema_version': 6, 'branches': [branch]})
    out = tmp_path / 'out'
    assert fx.main([str(config), '--out-dir', str(out)]) == 0
    edits = json.loads((out / 'b.edits.json').read_text())
    plan = json.loads((out / 'b.plan.json').read_text())
    assert edits == {'players': [{'pgid': 7, 'set': {'ContractStatus': 'Signed', 'TeamIndex': 4}}]}
    assert plan['conflicts'] == [] and plan['stage'] == 'OFFSEASON_START'


def test_sign_block_keeps_the_remaining_years_and_void_years():
    contract = {'length': 2, 'current_year': 1, 'void_years': 3,
                'salary_by_year': [150, 750], 'bonus_by_year': [180, 180, 130, 130, 130]}
    assert fx.sign_block(contract, 1) == {'Length': 1, 'CurrentYear': 0, 'SalaryYear0': 750,
                                          'BonusYear0': 180, 'BonusYear1': 130, 'BonusYear2': 130,
                                          'BonusYear3': 130, 'VoidYears': 3}


def test_sign_block_rejects_an_expired_contract():
    with pytest.raises(ValueError, match='no year left'):
        fx.sign_block({'length': 1, 'current_year': 1, 'salary_by_year': [0], 'bonus_by_year': []}, 1)


def test_signed_branch_writes_sign_edits(tmp_path):
    snap = _snapshot([{'pgid': 7, 'status': 'Retired', 'team_index': 32}])
    prior = _snapshot([{'pgid': 7, 'status': 'Signed', 'team_index': 4,
                        'contract': {'length': 2, 'current_year': 1, 'void_years': 0,
                                     'salary_by_year': [10, 20], 'bonus_by_year': [1, 2]}}])
    branch = _branch(tmp_path, snap, [{'pgid': 7, 'baseline_status': 'FreeAgent', 'baseline_team_index': 32}])
    branch['contract_from'] = str(_write(tmp_path, 'prior.json', prior))
    config = _write(tmp_path, 'cfg.json', {'fixture_id': 'g4-test', 'schema_version': 6, 'branches': [branch]})
    out = tmp_path / 'out'
    assert fx.main([str(config), '--out-dir', str(out)]) == 0
    edits = json.loads((out / 'b.edits.json').read_text())
    signs = json.loads((out / 'b.sign.edits.json').read_text())
    assert edits == {'players': [{'pgid': 7, 'set': {'ContractStatus': 'FreeAgent', 'TeamIndex': 32}}]}
    assert signs == {'sign': [{'pgid': 7, 'team_index': 4, 'list': 'Roster',
                               'contract': {'Length': 1, 'CurrentYear': 0, 'SalaryYear0': 20,
                                            'BonusYear0': 2}}]}


def test_signed_branch_needs_a_free_agent_baseline(tmp_path):
    snap = _snapshot([{'pgid': 7, 'status': 'Retired', 'team_index': 32}])
    prior = _snapshot([{'pgid': 7, 'status': 'Signed', 'team_index': 4,
                        'contract': {'length': 2, 'current_year': 1, 'salary_by_year': [10, 20],
                                     'bonus_by_year': [1, 2]}}])
    branch = _branch(tmp_path, snap, [{'pgid': 7, 'baseline_status': 'Signed', 'baseline_team_index': 4}])
    branch['contract_from'] = str(_write(tmp_path, 'prior.json', prior))
    with pytest.raises(ValueError, match='must restore to FreeAgent/32'):
        fx.plan_branch(branch, fixture_id='g4-test', schema_version=6)
