"""tools/personality_contract_policy_report.py: deterministic fictional multi-cohort report, paired seeds, no
database (PERSONALITY_CONTRACT_POLICY_PLAN.md §4)."""
import ast
import json
from pathlib import Path

import pytest

from tools import personality_contract_policy_report as rpt

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / 'tests' / 'fixtures' / 'contract_policy' / 'cohorts.json'


def test_report_is_byte_identical_across_runs(tmp_path):
    rpt.main(['--input', str(FIXTURE), '--out', str(tmp_path / 'a'), '--mode', 'compare'])
    rpt.main(['--input', str(FIXTURE), '--out', str(tmp_path / 'b'), '--mode', 'compare'])
    for name in ('report.json', 'report.md'):
        assert (tmp_path / 'a' / name).read_bytes() == (tmp_path / 'b' / name).read_bytes()


def test_compare_pairs_seeds_and_reports_effects():
    rep = rpt.run(rpt.load_input(FIXTURE), 'compare')
    data = json.loads(FIXTURE.read_text())
    players = sum(len(c['players']) for c in data['cohorts'])
    assert rep['counts']['decisions'] == players * len(data['seeds'])
    assert rep['delta']['nonzero'] > 0 and rep['flips']['ranking'] > 0
    assert rep['inputs']['profile_missing'] == 2
    assert set(rep['delta']['family_active']) == {'ambition', 'attachment', 'home', 'grudge'}
    cap = rep['config']['aggregate_cap']
    assert -cap <= rep['delta']['signed']['min'] and rep['delta']['signed']['max'] <= cap
    assert 'not calibration' in rep['disclaimer']


@pytest.mark.parametrize('mode', ['neutral', 'shadow'])
def test_neutral_and_shadow_keep_baseline_decisions(mode):
    rep = rpt.run(rpt.load_input(FIXTURE), mode)
    assert rep['baseline'] == rep['adjusted']
    assert all(v == 0 for v in rep['flips'].values())
    if mode == 'neutral':
        assert rep['delta']['nonzero'] == 0
    else:
        assert rep['delta']['nonzero'] > 0        # shadow computes the deltas without applying them


def test_gate_sweep_lists_cross_certification_discontinuities():
    rep = rpt.run(rpt.load_input(FIXTURE), 'compare')
    by_f = {s['financial']: s['discontinuities'] for s in rep['gate_sweep']}
    assert by_f[0.6] == []                                    # contention leads money: the gate only adds
    assert by_f[1.2] and all(d['utility_change'] < 0 for d in by_f[1.2])
    assert {d['to_contention'] for d in by_f[1.2]} == {rep['config']['contender_floor']}


def test_wrong_schema_rejected(tmp_path):
    p = tmp_path / 'bad.json'
    p.write_text(json.dumps({'schema': 'other', 'cohorts': []}))
    with pytest.raises(ValueError):
        rpt.load_input(p)


def test_report_module_imports_no_database_code():
    tree = ast.parse((ROOT / 'tools' / 'personality_contract_policy_report.py').read_text())
    names = {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    assert not {m for m in names if m and (m.startswith('psycopg') or m.startswith('db') or 'store' in m)}
