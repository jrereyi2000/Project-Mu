"""tools/coach_profile_review.py (REAL_COACH_PROFILES_PLAN.md §2.3, §4). Temp directories only; no DB, no network."""
import json
import subprocess
import sys
from pathlib import Path

from personality import coach_profiles as cp
from tests.test_coach_profiles import profile
from tools import coach_profile_review as tool

ROOT = Path(__file__).resolve().parent.parent


def write(tmp_path, doc, name=None):
    path = tmp_path / (name or doc['_file'])
    path.write_text(json.dumps(cp.file_doc(doc), indent=2))
    return path


def test_validate_exit_codes(tmp_path, capsys):
    write(tmp_path, profile('draft'))
    assert tool.main(['validate', '--dir', str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert 'coach:CAR:HC: ok' in out
    assert tool.main(['validate', '--dir', str(tmp_path), '--teams', 'CAR']) == 0
    assert 'not written yet: CAR_OC, CAR_DC' in capsys.readouterr().out
    bad = profile('draft')
    bad['views'][0]['when'] = ['NOPE']
    write(tmp_path, bad)
    assert tool.main(['validate', '--dir', str(tmp_path)]) == 1
    assert 'unknown tags' in capsys.readouterr().out


def test_validate_and_report_work_on_the_template(tmp_path, capsys):
    template = str(ROOT / cp.PROFILE_DIR / '_TEMPLATE.json')
    assert tool.main(['validate', '--file', template]) == 1
    assert 'matches no seed row' in capsys.readouterr().out
    out = tmp_path / 'template.md'
    assert tool.main(['report', '--file', template, '--out', str(out)]) == 0
    assert '### Sources' in out.read_text()


def test_report_has_every_claim_source_and_decision_line_and_is_deterministic(tmp_path):
    doc = profile('draft')
    write(tmp_path, doc)
    out1, out2 = tmp_path / 'a.md', tmp_path / 'b.md'
    for out in (out1, out2):
        assert tool.main(['report', '--dir', str(tmp_path), '--teams', 'CAR', '--out', str(out)]) == 0
    text = out1.read_text()
    assert text == out2.read_text()
    assert '**Decision (coach:CAR:HC):** [ ] approve  [ ] edit  [ ] reject' in text
    for ev in doc['evidence']:
        assert f"[{ev['id']}]({ev['url']})" in text
        assert text.count(ev['summary']) == 1          # summaries appear once, in the Sources table
        assert text.index(ev['summary']) > text.index('### Sources')
    for s, v in doc['sliders'].items():
        assert f'| {s} | {v} |' in text
    assert '| openness | 5 | none (neutral) |' in text
    for t in ('Scheme nerd', 'QB whisperer', 'Measured'):
        assert f'**{t}**' in text
    for v in doc['views']:
        assert v['text'] in text
    for field in ('want', 'fear', 'voice'):
        assert doc[field] in text
    assert 'opens with the injury report' in text and 'credits the line first' in text
    assert 'coach Mike Macdonald (SEA HC); in the 2026 seed: yes' in text
    assert 'player Bryce Young (CAR)' in text
    assert 'medium' in text and 'low' in text
    assert 'Not written yet: CAR_OC, CAR_DC.' in text


def decide(tmp_path, decisions):
    path = tmp_path / 'decisions.json'
    path.write_text(json.dumps(decisions))
    return tool.main(['apply-review', '--decisions', str(path), '--dir', str(tmp_path), '--today', '2026-11-05'])


def test_apply_review_approves(tmp_path):
    p = write(tmp_path, profile('draft'))
    assert decide(tmp_path, {'coach:CAR:HC': {'status': 'approved', 'notes': 'good'}}) == 0
    doc = json.loads(p.read_text())
    assert doc['review'] == {'status': 'approved', 'reviewer': 'JR', 'reviewed_at': '2026-11-05', 'notes': 'good'}
    assert '_file' not in doc
    assert cp.lifecycle_entries(cp.load_profiles(tmp_path))[0]['key'] == 'coach:CAR:HC'


def test_apply_review_edits_and_revalidates(tmp_path):
    p = write(tmp_path, profile('draft'))
    assert decide(tmp_path, {'coach:CAR:HC': {'status': 'edited', 'notes': 'softer',
                                              'edits': {'want': 'to win in Carolina'}}}) == 0
    doc = json.loads(p.read_text())
    assert doc['want'] == 'to win in Carolina' and doc['review']['status'] == 'edited'


def test_apply_review_refuses_an_invalid_edit_and_writes_nothing(tmp_path, capsys):
    p = write(tmp_path, profile('draft'))
    before = p.read_text()
    assert decide(tmp_path, {'coach:CAR:HC': {'status': 'edited', 'edits': {'traits': ['SCHEME_NERD']}}}) == 1
    assert 'refused' in capsys.readouterr().out and p.read_text() == before
    assert decide(tmp_path, {'coach:CAR:HC': {'status': 'edited', 'edits': {'key': 'coach:KC:HC'}}}) == 1
    assert decide(tmp_path, {'coach:KC:HC': {'status': 'approved'}}) == 1
    assert p.read_text() == before


def test_apply_review_rejects(tmp_path):
    p = write(tmp_path, profile('draft'))
    assert decide(tmp_path, {'coach:CAR:HC': {'status': 'rejected', 'notes': 'too thin'}}) == 0
    doc = json.loads(p.read_text())
    assert doc['review']['status'] == 'rejected' and doc['review']['notes'] == 'too thin'
    assert cp.lifecycle_entries(cp.load_profiles(tmp_path)) == []


def test_no_database_or_network_imports():
    code = ('import sys; import tools.coach_profile_review, personality.coach_profiles; '
            "bad = [m for m in ('psycopg', 'psycopg2', 'psycopg_pool', 'requests', 'httpx', 'urllib.request', "
            "'db.seed_clean_db', 'anthropic', 'google.genai') if m in sys.modules]; print(bad); sys.exit(1 if bad else 0)")
    run = subprocess.run([sys.executable, '-c', code], cwd=ROOT, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr


def test_help_exits_zero():
    run = subprocess.run([sys.executable, '-m', 'tools.coach_profile_review', '--help'], cwd=ROOT,
                         capture_output=True, text=True)
    assert run.returncode == 0 and 'apply-review' in run.stdout
