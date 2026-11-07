"""pipeline/retirement_window.py (RETIREMENT_WINDOW_PLAN.md §2.2–2.6, U1). Synthetic fixtures only."""
import json
from copy import deepcopy

import pytest

from pipeline import retirement_edits as re_
from pipeline import retirement_window as rw

PLAYERS = [
    {'pgid': 111, 'status': 'Retired', 'team_index': 32},   # decided retired
    {'pgid': 222, 'status': 'Retired', 'team_index': 32},   # late native
    {'pgid': 333, 'status': 'Retired', 'team_index': 32},   # kept, re-retired
    {'pgid': 666, 'status': 'Retired', 'team_index': 32},   # late native, no baseline
    {'pgid': 777, 'status': 'Signed', 'team_index': 3},
]
DECIDED = {'111': {'player_id': 1, 'keep': False, 'reason': 'retired'},
           '333': {'player_id': 3, 'keep': True, 'reason': 'kept'}}
PRIOR = {'111': ['Retired', None], '222': ['Signed', 7], '333': ['FreeAgent', 32], '444': ['Signed', 3],
         '445': ['FreeAgent', 32], '555': ['Retired', None], '777': ['Signed', 3]}


def run(**kw):
    args = dict(stage='RESIGN', wave=None, season=2026, decided=DECIDED, prior_image=PRIOR,
                prior_stop='OFFSEASON_START', window_restores={})
    args.update(kw)
    return rw.classify(kw.pop('players', PLAYERS), **{k: v for k, v in args.items() if k != 'players'})


def test_classify_cases():
    r = run()
    assert r['version'] == rw.WINDOW_VERSION and r['stop'] == 'RESIGN' and r['stage_gap'] is False
    assert r['decided_retired'] == [111]
    assert r['restore'] == [
        {'pgid': 222, 'kind': 'LATE_NATIVE', 'baseline': {'status': 'Signed', 'team_index': 7}, 'user_team': False},
        {'pgid': 333, 'kind': 'RE_RETIRED', 'baseline': {'status': 'FreeAgent', 'team_index': 32},
         'user_team': False}]
    assert r['conflicts'] == [{'pgid': 666, 'kind': 'NO_BASELINE', 'was': 'LATE_NATIVE',
                               'reason': 'baseline_missing'}]
    assert r['vanished'] == [
        {'pgid': 444, 'kind': 'VANISHED_ROSTERED', 'last': {'status': 'Signed', 'team_index': 3}, 'user_team': False},
        {'pgid': 445, 'kind': 'VANISHED_FREE_AGENT', 'last': {'status': 'FreeAgent', 'team_index': 32},
         'user_team': False}]
    assert r['purged'] == [555]
    assert r['carried'] == []


def test_re_retired_again_is_a_conflict():
    r = run(window_restores={'333': 1})
    assert {'pgid': 333, 'kind': 'RE_RETIRED_AGAIN', 'reason': 're_retired_after_window_restore'} in r['conflicts']
    assert [x['pgid'] for x in r['restore']] == [222]


def test_inactive_baseline_and_still_retired():
    prior = dict(PRIOR, **{'222': ['Retired', None], '666': ['Draft', None]})
    r = run(prior_image=prior)
    assert r['carried'] == [{'pgid': 222, 'kind': 'STILL_RETIRED', 'was': 'LATE_NATIVE'}]
    assert {'pgid': 666, 'kind': 'NO_BASELINE', 'was': 'LATE_NATIVE', 'reason': 'baseline_not_active:Draft'} \
        in r['conflicts']


def test_stage_gap_and_stop_names():
    assert run(stage='FREE_AGENCY', wave=1, prior_stop='RESIGN')['stop'] == 'FREE_AGENCY:1'
    assert run(stage='FREE_AGENCY', wave=1, prior_stop='RESIGN')['stage_gap'] is False
    assert run(stage='FREE_AGENCY', wave=2, prior_stop='RESIGN')['stage_gap'] is True
    assert run(stage='COMBINE', wave=3, prior_stop='FREE_AGENCY:2')['stage_gap'] is False
    assert run(stage='CUTDOWN', prior_stop='CUTDOWN')['stage_gap'] is False
    assert run(prior_stop=None)['stage_gap'] is False
    with pytest.raises(rw.WindowError):
        run(stage='REGULAR_WEEK')


def test_user_team_flag_from_franchise_first_season():
    # JR (plan §8 Q3): from the franchise's first season (2026), not from 2034.
    r = run(user_team_indexes=frozenset({7, 3}))
    assert [x['user_team'] for x in r['restore']] == [True, False]
    assert r['vanished'][0]['user_team'] is True
    assert all(not x['user_team'] for x in run(season=2025, user_team_indexes=frozenset({7}))['restore'])


def test_classify_is_deterministic_and_sorted():
    shuffled = list(reversed(PLAYERS))
    a, b = run(), run(players=shuffled)
    assert a == b
    assert json.dumps(a, sort_keys=True) == json.dumps(run(), sort_keys=True)


def test_roster_image_round_trip():
    img = rw.roster_image({7: ('Signed', 3), 2: ('Retired', None)})
    assert img == {'2': ['Retired', None], '7': ['Signed', 3]}
    assert list(img) == ['2', '7']
    assert json.loads(json.dumps(img)) == img


def test_restore_requests_shape_and_default_state():
    r = run()
    reqs = rw.restore_requests(r)
    assert reqs == [{'decision_id': 'window:2026:222', 'pgid': 222, 'action': 'KEEP_NATIVE_RETIREE',
                     'state': 'AVAILABLE'},
                    {'decision_id': 'window:2026:333', 'pgid': 333, 'action': 'KEEP_NATIVE_RETIREE',
                     'state': 'AVAILABLE'}]
    assert rw.restore_requests(r, {'222': 'CONFLICT'})[0]['state'] == 'CONFLICT'


PROOF = {'kind': rw.PROOF_KIND, 'version': re_.AUTHORITY_VERSION, 'fixture_id': 'g4-test',
         'proof_save_hash': 'sha:proof', 'schema_version': 5, 'stages': ['OFFSEASON_START', 'RESIGN'],
         'exclusive_owner': True,
         'actions': {'KEEP_NATIVE_RETIREE': {'proven': True, 'proof_id': 'p1',
                                             'fields': ['ContractStatus', 'TeamIndex']}},
         'approved_by': 'JR', 'approved_on': '2026-11-20'}


def write(tmp_path, obj, raw=None):
    p = tmp_path / 'proof.json'
    p.write_text(raw if raw is not None else json.dumps(obj))
    return str(p)


def test_load_proof(tmp_path):
    assert rw.load_proof(None) is None and rw.load_proof('') is None
    assert rw.load_proof(write(tmp_path, PROOF)) == PROOF
    assert rw.proof_from_env({}) is None
    assert rw.proof_from_env({rw.AUTHORITY_ENV: write(tmp_path, PROOF)}) == PROOF


@pytest.mark.parametrize('mutate', [
    lambda p: p.update(kind='other'),
    lambda p: p.pop('approved_by'),
    lambda p: p.update(approved_on=''),
    lambda p: p.update(stages='RESIGN'),
    lambda p: p['actions'].update(RETIRE_ACTIVE={'proven': True}),
])
def test_load_proof_rejects_broken(tmp_path, mutate):
    bad = deepcopy(PROOF)
    mutate(bad)
    with pytest.raises(rw.WindowError):
        rw.load_proof(write(tmp_path, bad))


def test_load_proof_malformed_and_unreadable(tmp_path):
    with pytest.raises(rw.WindowError):
        rw.load_proof(write(tmp_path, None, raw='{not json'))
    with pytest.raises(rw.WindowError):
        rw.load_proof(str(tmp_path / 'missing.json'))


def plan(report, proof, stage='RESIGN', schema=5, pending=None, players=PLAYERS):
    auth = rw.authority_for_run(proof, source_save_hash='sha:live', schema_version=schema)
    snap = rw.planner_snapshot(players, source_save_hash='sha:live', stage=stage, schema_version=schema,
                               prior_image=PRIOR, pending=pending or {})
    return re_.plan_retirement_edits(snap, rw.restore_requests(report), auth)


def test_no_proof_blocks_every_restore():
    out = plan(run(), None)
    assert out['edits'] == {} and len(out['conflicts']) == 2
    assert all(c['reasons'] == ['authority_missing'] for c in out['conflicts'])


def test_proof_restores_baseline_status_and_team():
    out = plan(run(), PROOF)
    assert out['conflicts'] == []
    assert out['edits'] == {'players': [{'pgid': 222, 'set': {'ContractStatus': 'Signed', 'TeamIndex': 7}},
                                        {'pgid': 333, 'set': {'ContractStatus': 'FreeAgent', 'TeamIndex': 32}}]}
    assert rw.authority_for_run(PROOF, source_save_hash='sha:live', schema_version=5)['source_save_hash'] == 'sha:live'


def test_uncovered_stage_schema_change_and_pending_overlap():
    r = run(stage='DRAFT', prior_stop='PRO_DAY')
    assert all('stage_mismatch:DRAFT' in c['reasons'] for c in plan(r, PROOF, stage='DRAFT')['conflicts'])
    assert all('schema_mismatch' in c['reasons'] for c in plan(run(), PROOF, schema=6)['conflicts'])
    out = plan(run(), PROOF, pending={'sign': [{'pgid': 222}]})
    assert next(c for c in out['conflicts'] if c['pgid'] == 222)['reasons'] == ['overlaps_sign']
    assert out['edits']['players'] == [{'pgid': 333, 'set': {'ContractStatus': 'FreeAgent', 'TeamIndex': 32}}]


def test_notice():
    assert rw.notice(None) is None and rw.notice({}) is None
    line, prio = rw.notice({'role': 'decision', 'season': 2026,
                            'decided': {'1': {'keep': True}, '2': {'keep': False}}})
    assert prio == 'default' and 'decided 2' in line and '1 kept' in line
    base = {'role': 'window', 'stop': 'RESIGN', 'restored': [], 'conflicts': [], 'vanished': [],
            'blocked': [{'pgid': 222, 'reasons': ['authority_missing']}]}
    line, prio = rw.notice(base)
    assert prio == 'default' and 'not restored (authority_missing)' in line
    assert rw.notice(dict(base, blocked=[{'pgid': 2, 'reasons': ['stage_mismatch:DRAFT']}]))[1] == 'default'
    assert rw.notice(dict(base, blocked=[{'pgid': 2, 'reasons': ['overlaps_sign']}]))[1] == 'high'
    assert rw.notice(dict(base, conflicts=[{'pgid': 3}]))[1] == 'high'
    line, prio = rw.notice(dict(base, vanished=[{'pgid': 4, 'kind': 'VANISHED_ROSTERED', 'name': 'A B',
                                                 'last': {'status': 'Signed', 'team_index': 3}}]))
    assert prio == 'high' and 'A B (team 3)' in line
    assert rw.notice(dict(base, vanished=[{'pgid': 5, 'kind': 'VANISHED_FREE_AGENT',
                                           'last': {'status': 'FreeAgent', 'team_index': 32}}]))[1] == 'default'
    assert 'skipped since' in rw.notice(dict(base, stage_gap=True, prior_stop='OFFSEASON_START'))[0]
    assert 'on your team' in rw.notice(dict(base, restored=[{'pgid': 1, 'user_team': True}]))[0]


def test_module_is_pure():
    import ast
    import inspect
    tree = ast.parse(inspect.getsource(rw))
    names = {a.name.split('.')[0] for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))
             for a in getattr(n, 'names', [])} | {(n.module or '').split('.')[0] for n in ast.walk(tree)
                                                    if isinstance(n, ast.ImportFrom)}
    assert not names & {'psycopg2', 'subprocess', 'pipeline', 'db', 'engines'}
