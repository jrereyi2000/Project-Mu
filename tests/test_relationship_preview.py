"""personality/relationship_preview.py (PERSONALITY_RELATIONSHIPS_PLAN.md §2.7, §4, U5): deterministic CLI, baseline
diff, structured error exits, no database/model/core writes, 2026 prospect -> 2034 continuity through aliases
(one persona_id, no yearly reset), neutral acceptance (off == observe consumer context, profile unchanged), and
synthetic stress/sensitivity reporting to a caller-chosen output."""
from copy import deepcopy
import json
import subprocess
import sys

import pytest

from personality import core
from personality import relationship_events as rev
from personality import relationship_preview as rp
from personality import relationships as rel

U = 'u-test'
REG = {'team:3': {'team_id': 3}, 'team:5': {'team_id': 5}}
IDENT = core.Identity(U, 'player', 'prospect:2026:uid-77')        # the prospect's birth key, kept for life
ME = {'kind': 'person', 'universe_id': U, 'role': 'player', 'persona_id': IDENT.persona_id}
TEAM = {'kind': 'team', 'universe_id': U, 'team_key': 'team:3'}
CAL = {s: 32 for s in range(2026, 2036)}


def env(kind, payload, *, sid, at, target=TEAM, visibility='private', responsible=()):
    return {'schema_version': rev.RELATIONSHIP_SCHEMA_VERSION, 'universe_id': U, 'event_id': sid,
            'source_event_id': sid, 'source_kind': 'test', 'source_refs': [], 'kind': kind,
            'occurred_at': {'season': at[0], 'tick': at[1]}, 'subject': ME, 'target': target, 'actor': None,
            'basis': 'verified_simulation', 'confidence': 'high', 'reviewer': None, 'evidence_scope': [],
            'visibility': visibility, 'attribution': {'responsible': list(responsible), 'reason_code': None,
                                                      'evidence_refs': []},
            'payload': payload, 'supersedes_source_event_id': None}


def tenure(season):
    return env('tenure_window', {'team_key': 'team:3', 'start': {'season': season, 'tick': 1},
                                 'end': {'season': season + 1, 'tick': 1}, 'coverage': 'complete'},
               sid=f'mu:tenure:{season}:3:77', at=(season, 1), visibility='public')


CUT = env('treatment', {'team_key': 'team:3', 'treatment_kind': 'dismissal', 'outcome': 'negative',
                        'significance': 'meaningful', 'outcome_refs': []}, sid='mu:release:2030:9:77', at=(2030, 9),
          responsible=[TEAM])
AWARD = env('situation', {'situation_kind': 'award', 'state': 'positive', 'valid_until': {'season': 2035, 'tick': 1},
                          'outcome_refs': []}, sid='mu:award:9', at=(2034, 2), visibility='public')


def trial(mode):
    deltas = {c: 0.0 for c in rel.CONTRIBUTION_CODES}
    deltas.update({'treatment.dismissal.negative': -0.5, 'treatment.recognition.positive': 0.3})
    return {'mode': mode, 'rule_version': 'trial-fixture-1',
            'trust': {'cap': 1.0, 'positive_half_life_ticks': 64, 'deltas': deltas,
                      'breach_floor': {'material': -0.25, 'minor': 0.0},
                      'repair_step': {'acknowledgment': 0.0, 'restitution': 0.5, 'commitment_met': 0.5,
                                      'reconciliation': 1.0}},
            'attachment': {'tiers': {'weak': {'min_tenure_ticks': 32, 'min_affirmative': 1, 'min_connections': 0},
                                     'established': {'min_tenure_ticks': 64, 'min_affirmative': 2,
                                                     'min_connections': 1},
                                     'strong': {'min_tenure_ticks': 128, 'min_affirmative': 4,
                                                'min_connections': 2}}},
            'reactions': None}


@pytest.fixture
def profile():
    return core.initialize(IDENT, 'generated', 2026)


def document(profile=None, *, mode='observe', events=None, baseline=None, candidates=()):
    return {'schema_version': rev.RELATIONSHIP_SCHEMA_VERSION, 'subject': ME,
            'events': events if events is not None else [tenure(s) for s in range(2026, 2034)] + [CUT, AWARD],
            'profile': profile, 'policy': trial(mode), 'calendar': CAL, 'as_of': {'season': 2034, 'tick': 5},
            'known_through': None, 'team_registry': REG, 'baseline_events': baseline, 'candidates': list(candidates)}


# ------------------------------------------------------------------------------------------------ pure ---

def test_prospect_history_carries_into_2034_without_a_reset(profile):
    r = rp.preview(document(profile, mode='enabled'))
    (edge,) = r['audit']['relationships']['edges']
    assert edge['coverage']['start'] == {'season': 2026, 'tick': 1}
    assert edge['attachment']['dimensions']['tenure_ticks'] == 8 * 32
    assert edge['unresolved_breaches'][0]['source_id'] == 'mu:release:2030:9:77'     # 2030 breach still open
    assert r['mood'] == {'primary': 'frustrated', 'secondary': ['encouraged']}
    assert r['consumer']['relationships']['status'] == 'enabled'


def test_late_start_invents_no_history(profile):
    r = rp.preview(document(profile, events=[AWARD]))
    assert r['audit']['relationships']['edges'][0]['coverage']['start'] is None
    assert r['audit']['relationships']['edges'][0]['attachment']['tier'] == 'unknown'


def test_neutral_acceptance_off_equals_observe_and_profile_is_untouched(profile):
    before = deepcopy(profile)
    off, obs = rp.preview(document(profile, mode='off')), rp.preview(document(profile, mode='observe'))
    assert off['consumer'] == obs['consumer']
    assert off['consumer']['relationships']['edges'] == [] and off['consumer']['reactions']['mood'] == 'neutral'
    assert obs['audit']['relationships']['edges'][0]['trust'] == -0.5             # the private audit exists
    assert profile == before and profile['revision'] == 0


def test_baseline_diff_and_candidates(profile):
    cand = {'event_id': 'rel-1', 'source_event_id': 'mu:release:2030:9:77', 'season': 2031, 'expected_revision': 0,
            'rule_version': 'relationships-evo-1', 'reason': 'released', 'kind': 'slider', 'slider': 'temperament',
            'step': 1, 'review_status': 'pending', 'reviewer': None, 'feature_scope': ['relationships'],
            'supporting_source_ids': ['mu:release:2030:9:77']}
    base = [tenure(s) for s in range(2026, 2034)]
    r = rp.preview(document(profile, mode='enabled', baseline=base, candidates=[cand]))
    assert r['baseline_diff']['changed_edges']['team:team:3']['baseline']['open_breaches'] == 0
    assert r['baseline_diff']['mood']['baseline']['primary'] == 'neutral'
    assert r['candidates'][0]['status'] == 'review_required'
    assert rp.preview(document(None, candidates=[cand]))['candidates'][0]['reasons'] == ['profile_missing']


def test_profile_must_be_the_subject(profile):
    other = core.initialize(core.Identity(U, 'player', 'someone-else'), 'generated', 2026)
    with pytest.raises(rp.PreviewError):
        rp.preview(document(other))


def test_sensitivity_table():
    doc = document()
    doc['sensitivity'] = [{'name': 'harsher', 'policy': dict(trial('observe'), rule_version='harsher')}]
    doc['sensitivity'][0]['policy']['trust'] = dict(doc['policy']['trust'],
                                                    breach_floor={'material': -0.6, 'minor': 0.0})
    r = rp.preview(doc)
    assert r['sensitivity'][0]['edges']['team:team:3']['trust'] == -0.6      # the floor caps trust while open
    assert r['audit']['relationships']['edges'][0]['trust'] == -0.5


def test_stress_league_is_deterministic_and_runs():
    a, n = rp.synthetic_league(teams=2, seasons=3, players=4)
    b, _ = rp.synthetic_league(teams=2, seasons=3, players=4)
    assert a == b and n == 2 * 3 * 4 * 2
    out = rp.stress(teams=2, seasons=3, players=4)
    assert out['event_count'] == n and out['subject_edges'] == 1 and out['projection_seconds'] >= 0


# ------------------------------------------------------------------------------------------------- CLI ---

def cli(*args):
    return subprocess.run([sys.executable, '-m', 'personality.relationship_preview', *args],
                          capture_output=True, text=True)


def test_help_exits_zero():
    assert cli('--help').returncode == 0


def test_cli_is_deterministic_atomic_and_imports_no_database(tmp_path, profile):
    src = tmp_path / 'in.json'
    src.write_text(json.dumps(document(profile, mode='enabled')))
    out1, out2 = tmp_path / 'a.json', tmp_path / 'b.json'
    assert cli('--input', str(src), '--out', str(out1)).returncode == 0
    assert cli('--input', str(src), '--out', str(out2)).returncode == 0
    assert out1.read_bytes() == out2.read_bytes()
    report = json.loads(out1.read_text())
    assert report['mood']['primary'] == 'frustrated' and 'generated_at' not in json.dumps(report)
    assert sorted(p.name for p in tmp_path.iterdir()) == ['a.json', 'b.json', 'in.json']
    probe = subprocess.run([sys.executable, '-c', 'import sys, personality.relationship_preview; '
                            'print(sorted(m for m in ("psycopg", "psycopg2", "anthropic", "openai", '
                            '"personality.relationship_store", "personality.store") if m in sys.modules))'],
                           capture_output=True, text=True)
    assert probe.stdout.strip() == '[]'


@pytest.mark.parametrize('content,code', [
    ('{not json', 'invalid_json'),
    ('[]', 'invalid_document'),
    (json.dumps({'schema_version': 'relationships-v0'}), 'invalid_document'),
])
def test_cli_errors_exit_two_with_a_structured_error(tmp_path, content, code):
    src, out = tmp_path / 'in.json', tmp_path / 'out.json'
    src.write_text(content)
    r = cli('--input', str(src), '--out', str(out))
    assert r.returncode == 2 and json.loads(r.stdout)['error']['code'] == code
    assert not out.exists()


def test_cli_schema_errors(tmp_path):
    doc = document()
    doc['schema_version'] = 'relationships-v0'
    src, out = tmp_path / 'in.json', tmp_path / 'out.json'
    src.write_text(json.dumps(doc))
    r = cli('--input', str(src), '--out', str(out))
    assert r.returncode == 2 and json.loads(r.stdout)['error']['code'] == 'schema_version'
    doc = document()
    doc['events'][0]['kind'] = 'friendship'
    src.write_text(json.dumps(doc))
    assert json.loads(cli('--input', str(src), '--out', str(out)).stdout)['error']['code'] == 'invalid_enum'
    doc = document()
    doc['policy'] = {'mode': 'enabled', 'rule_version': 'x'}
    src.write_text(json.dumps(doc))
    assert json.loads(cli('--input', str(src), '--out', str(out)).stdout)['error']['code'] == 'invalid_policy'
    assert not out.exists()


def test_cli_stress_to_caller_chosen_output(tmp_path):
    out = tmp_path / 'stress.json'
    r = cli('--stress', '--teams', '2', '--seasons', '2', '--players', '3', '--out', str(out))
    assert r.returncode == 0
    report = json.loads(out.read_text())
    assert report['event_count'] == 2 * 2 * 3 * 2 and 'peak_memory_kb' in report
