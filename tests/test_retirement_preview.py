"""tools/retirement_preview.py (PERSONALITY_RETIREMENT_PLAN.md §2.6, U5): deterministic report, no DSN/binary/save
flags or writes, clear review-only labels, the same neutral draw in both arms."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from engines import retirement_policy as rp
from tools import retirement_preview as pv

ROOT = Path(__file__).resolve().parents[1]


def run(tmp_path, fixture, *extra, name='r.json'):
    src = tmp_path / 'fixture.json'
    src.write_text(json.dumps(fixture))
    out = tmp_path / name
    code = pv.main(['--input', str(src), '--out', str(out), *extra])
    return code, out


def test_reports_are_byte_identical(tmp_path):
    fx = pv.example_fixture()
    c1, a = run(tmp_path, fx, '--mode', 'compare', name='a.json')
    c2, b = run(tmp_path, fx, '--mode', 'compare', name='b.json')
    assert c1 == c2 == 0 and a.read_bytes() == b.read_bytes()
    assert pv.example_fixture() == fx


def test_labels_and_review_only_policy(tmp_path):
    code, out = run(tmp_path, pv.example_fixture())
    r = json.loads(out.read_text())
    assert 'REVIEW-ONLY / UNCALIBRATED' in r['label']
    assert r['policy'] == {'version': 'synthetic-review-v1', 'review_only': True, 'calibrated': False}
    assert r['mode'] == 'baseline' and all('effects' not in p for p in r['players'])


def test_compare_keeps_same_draw_and_neutral_arm(tmp_path):
    _, out = run(tmp_path, pv.example_fixture(), '--mode', 'compare')
    _, base = run(tmp_path, pv.example_fixture(), name='base.json')
    cmp_, b = json.loads(out.read_text()), json.loads(base.read_text())
    for pc, pb in zip(cmp_['players'], b['players']):
        assert pc['draw'] == pb['draw'] and pc['neutral'] == pb['neutral']
    qb = next(p for p in cmp_['players'] if p['person_key'] == 'example:qb-veteran')
    assert qb['effects']['final_p'] < qb['neutral']['final_p']          # Legacy Builder with a reachable goal
    rb = next(p for p in cmp_['players'] if p['person_key'] == 'example:rb-worn')
    assert rb['effects']['final_p'] == rb['neutral']['final_p']         # COASTING has no numeric effect


def test_unknowns_and_young_players_are_labelled(tmp_path):
    _, out = run(tmp_path, pv.example_fixture())
    r = json.loads(out.read_text())
    young = next(p for p in r['players'] if p['person_key'] == 'example:cb-young')
    assert young['neutral']['intent'] == 'AVAILABLE'
    assert 'young_unsigned_exit_review_separately' in young['neutral']['warnings']
    assert young['coverage']['earnings'] == 'unknown'


def test_native_conflicts_are_blocked_by_default_authority(tmp_path):
    fx = pv.example_fixture()
    _, out = run(tmp_path, fx)
    r = json.loads(out.read_text())
    assert r['counts']['planned_edits'] == 0 and r['planner']['edits'] == {}
    for p in r['players']:
        if p.get('native_conflict'):
            assert 'authority_missing' in p['authority_blocks']


def test_context_errors_are_reported_not_fatal(tmp_path):
    fx = pv.example_fixture()
    fx['players'][0]['snapshot']['identity_status'] = 'ambiguous'
    code, out = run(tmp_path, fx)
    r = json.loads(out.read_text())
    assert code == 0 and r['players'][0]['status'] == 'context_error' and r['counts']['context_errors'] == 1


def test_policy_file_and_bad_input(tmp_path):
    pol = tmp_path / 'policy.json'
    pol.write_text(json.dumps(dict(rp.policy_to_dict(rp.synthetic_policy()), version='alt-review')))
    code, out = run(tmp_path, pv.example_fixture(), '--policy', str(pol))
    assert code == 0 and json.loads(out.read_text())['policy']['version'] == 'alt-review'
    pol.write_text(json.dumps(dict(rp.policy_to_dict(rp.synthetic_policy()), max_age=40)))
    assert run(tmp_path, pv.example_fixture(), '--policy', str(pol), name='x.json')[0] == 2
    assert run(tmp_path, {'players': 'nope'}, name='y.json')[0] == 2


def test_example_writer(tmp_path):
    p = tmp_path / 'ex.json'
    assert pv.main(['--example', str(p)]) == 0
    assert json.loads(p.read_text()) == pv.example_fixture()


def test_cli_help_has_no_dsn_commit_or_save_flags():
    out = subprocess.run([sys.executable, '-m', 'tools.retirement_preview', '--help'], cwd=ROOT,
                         capture_output=True, text=True, check=True).stdout
    assert '--mode' in out and '--policy' in out
    for flag in ('--dsn', '--commit', '--save', '--apply', '--write'):
        assert flag not in out
    src = (ROOT / 'tools' / 'retirement_preview.py').read_text()
    for forbidden in ('subprocess', 'psycopg', 'os.environ', '.exe'):
        assert forbidden not in src


def test_missing_required_args_exit(tmp_path):
    with pytest.raises(SystemExit):
        pv.main(['--input', 'x.json'])
