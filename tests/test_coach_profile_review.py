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


# ------------------------------------------- infer / convert-v2 / v2 report (INFERRED_SLIDERS_AND_FOCUS_PLAN) ---

from tests.test_coach_profiles import profile_v2  # noqa: E402


def test_infer_prints_basis_and_writes_only_with_flag(tmp_path, capsys):
    doc = profile_v2('draft')
    doc['slider_inference']['values'] = {s: 5 for s in doc['slider_inference']['values']}
    path = write(tmp_path, doc)
    before = path.read_text()
    assert tool.main(['infer', '--dir', str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert 'coach:CAR:HC: 2 inferred' in out and 'confidence =' in out and 'seed step' in out
    assert path.read_text() == before
    assert tool.main(['infer', '--dir', str(tmp_path), '--write']) == 0
    stored = json.loads(path.read_text())
    assert stored['slider_inference'] == cp.with_inference(doc)['slider_inference']
    assert stored['review']['status'] == 'draft'
    write(tmp_path, profile('draft'))
    assert tool.main(['infer', '--dir', str(tmp_path)]) == 0
    assert 'v1, skipped' in capsys.readouterr().out


def test_infer_keeps_locked_and_refuses_invalid(tmp_path, capsys):
    doc = profile_v2('draft')
    doc['slider_inference']['values']['openness'] = 9
    doc['slider_inference']['locked'] = ['openness']
    path = write(tmp_path, doc)
    assert tool.main(['infer', '--dir', str(tmp_path), '--write']) == 0
    assert 'set by JR, locked' in capsys.readouterr().out
    assert json.loads(path.read_text())['slider_inference']['values']['openness'] == 9
    bad = profile_v2('draft')
    bad['views'][0]['when'] = ['NOPE']
    path = write(tmp_path, bad)
    before = path.read_text()
    assert tool.main(['infer', '--dir', str(tmp_path), '--write']) == 1
    assert 'not written' in capsys.readouterr().out and path.read_text() == before


def test_convert_v2_dry_run_write_and_edits(tmp_path, capsys):
    path = write(tmp_path, profile('approved'))
    before = path.read_text()
    assert tool.main(['convert-v2', '--dir', str(tmp_path), '--today', '2026-11-07']) == 0
    out = capsys.readouterr().out
    assert 'converted to coach-profile-v2' in out and 'nothing written (dry run)' in out
    assert path.read_text() == before
    edits = tmp_path.parent / (tmp_path.name + '_edits.json')
    edits.write_text(json.dumps({'coach:CAR:HC': {'focus': {'want': {
        'text': 'to turn Bryce Young into a winning starter',
        'subjects': [{'kind': 'player', 'name': 'Bryce Young', 'team_2026': 'CAR'}], 'until_season': None},
        'fear': None}}}))
    assert tool.main(['convert-v2', '--dir', str(tmp_path), '--edits', str(edits), '--write',
                      '--today', '2026-11-07']) == 0
    stored = json.loads(path.read_text())
    assert stored['schema_version'] == 'coach-profile-v2' and stored['review']['status'] == 'draft'
    assert 'converted to v2 on 2026-11-07' in stored['review']['notes']
    assert stored['focus']['want']['subjects'][0]['name'] == 'Bryce Young'
    assert cp.validate_profile_file(dict(stored, _file='CAR_HC.json')) == []
    assert tool.main(['convert-v2', '--dir', str(tmp_path)]) == 0
    assert 'already v2' in capsys.readouterr().out


def test_convert_v2_writes_only_valid(tmp_path, capsys):
    doc = profile('draft')
    doc['want'] = ' '.join(['word'] * 20)
    path = write(tmp_path, doc)
    before = path.read_text()
    assert tool.main(['convert-v2', '--dir', str(tmp_path), '--write']) == 1
    assert 'motivation.want must be at most 14 words' in capsys.readouterr().out
    assert path.read_text() == before
    bad = tmp_path.parent / (tmp_path.name + '_bad.json')
    bad.write_text(json.dumps({'coach:CAR:HC': {'views': []}}))
    assert tool.main(['convert-v2', '--dir', str(tmp_path), '--edits', str(bad)]) == 0
    assert 'edits name fields' in capsys.readouterr().out


def test_report_v2_source_column_and_focus(tmp_path):
    doc = profile_v2('draft')
    doc['slider_inference']['values']['openness'] = 9
    doc['slider_inference']['locked'] = ['openness']
    write(tmp_path, doc)
    out = tmp_path / 'r.md'
    assert tool.main(['report', '--dir', str(tmp_path), '--teams', 'CAR', '--out', str(out)]) == 0
    text = out.read_text()
    assert '| Slider | Value | Source | Citations |' in text
    assert '| temperament | 3 | evidence (E1) |' in text
    assert '| openness | 9 | set by JR |' in text
    assert '| confidence |' in text and 'inferred' in text
    assert '### Motivation' in text and '### Current focus' in text
    assert 'refreshes when Bryce Young leaves CAR, retires or leaves the league' in text
    assert '- Fear: none (motivation only)' in text


def test_apply_review_edits_v2_fields(tmp_path):
    doc = profile_v2('draft')
    write(tmp_path, doc)
    planned = tool.apply_decisions({'coach:CAR:HC': {'status': 'edited', 'edits': {
        'motivation': {'want': 'to build a program that lasts', 'fear': 'losing the room'}}}},
        str(tmp_path), today='2026-11-08')
    assert planned[1] == [] and planned[0][0][1]['motivation']['want'] == 'to build a program that lasts'

