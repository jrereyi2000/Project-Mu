"""tools/coaching_tree_review.py (COACHING_TREES_PLAN.md §2.5, U2) on synthetic files: validate exit codes, the
report page, all-or-nothing apply-review and the events preview."""
import json

import pytest

from personality import coaching_trees as ct
from tests.test_coaching_trees import INDEX, batch, edge, registry
from tools import coaching_tree_review as tool


@pytest.fixture
def tree_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(ct.cp, 'seed_index', lambda *a, **k: INDEX)
    (tmp_path / 'trees.json').write_text(json.dumps(registry()))
    (tmp_path / 'batch_C.json').write_text(json.dumps(batch()))
    return tmp_path


def test_validate_exit_codes(tree_dir, capsys):
    assert tool.main(['validate', '--dir', str(tree_dir)]) == 0
    assert tool.main(['validate', '--dir', str(tree_dir), '--batch', 'registry']) == 0
    assert tool.main(['validate', '--dir', str(tree_dir), '--batch', 'A']) == 1        # no file
    bad = batch()
    bad['edges'][0]['outcome'] = 'negative'
    (tree_dir / 'batch_C.json').write_text(json.dumps(bad))
    assert tool.main(['validate', '--dir', str(tree_dir), '--batch', 'C']) == 1
    assert 'outcome must be' in capsys.readouterr().out


def test_report_page(tree_dir):
    out = tree_dir / 'out' / 'batch_C.md'
    assert tool.main(['report', '--dir', str(tree_dir), '--batch', 'C', '--out', str(out)]) == 0
    page = out.read_text()
    assert 'Kyle Shanahan (SF HC) → Klint Kubiak (SEA OC), 3 seasons' in page
    assert 'SF 2021–2023, HC over pass game coordinator' in page
    assert '[E1](https://example.com/E1) (high)' in page
    assert 'Decision (key `coach:SF:HC>coach:SEA:OC`)' in page and 'member:coach:SF:HC' in page
    assert '## Sources' in page
    reg = tree_dir / 'out' / 'registry.md'
    assert tool.main(['report', '--dir', str(tree_dir), '--batch', 'registry', '--out', str(reg)]) == 0
    assert 'tree:mcvay' in reg.read_text()


def test_apply_review_is_all_or_nothing(tree_dir):
    before = (tree_dir / 'batch_C.json').read_text()
    planned, errors = tool.apply_decisions({'coach:SF:HC>coach:SEA:OC': {'status': 'approved'},
                                            'member:coach:SF:HC': {'status': 'edited', 'edits': {'trees': ['nope']}}},
                                           tree_dir, today='2026-11-08')
    assert planned == [] and any('fails validation' in e for e in errors)
    planned, errors = tool.apply_decisions({'coach:XX:HC>coach:SEA:OC': {'status': 'approved'}}, tree_dir)
    assert planned == [] and errors == ['coach:XX:HC>coach:SEA:OC: no such entry']
    assert (tree_dir / 'batch_C.json').read_text() == before
    dec = tree_dir / 'dec.json'
    dec.write_text(json.dumps({'coach:SF:HC>coach:SEA:OC': {'status': 'approved', 'notes': 'ok'},
                               'tree:shanahan': {'status': 'approved'}}))
    assert tool.main(['apply-review', '--dir', str(tree_dir), '--decisions', str(dec), '--today', '2026-11-08']) == 0
    doc = json.loads((tree_dir / 'batch_C.json').read_text())
    assert doc['edges'][0]['review'] == {'status': 'approved', 'reviewer': 'JR', 'reviewed_at': '2026-11-08',
                                         'notes': 'ok'}
    assert '_file' not in doc
    reg = json.loads((tree_dir / 'trees.json').read_text())
    assert reg['trees'][0]['review']['status'] == 'approved' and reg['trees'][1]['review']['status'] == 'draft'


def test_events_preview(tree_dir):
    reviewed = batch([edge(review={'status': 'approved', 'reviewer': 'JR', 'reviewed_at': '2026-11-08'})])
    (tree_dir / 'batch_C.json').write_text(json.dumps(reviewed))
    out = tree_dir / 'events.json'
    assert tool.main(['events', '--dir', str(tree_dir), '--out', str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc['preview'] is True and doc['edges'] == 1 and len(doc['envelopes']) == 2
    assert doc['envelopes'][0]['subject']['universe_id'] == 'preview'
    assert doc['envelopes'][0]['subject']['persona_id'] == 'coach:SEA:OC'
