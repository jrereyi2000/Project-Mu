"""personality/coach_profiles.py (REAL_COACH_PROFILES_PLAN.md §2.1-§2.2, §4). Synthetic profiles only; no DB."""
from copy import deepcopy

import pytest

from newsroom import personas as legacy
from personality import coach_profiles as cp
from personality import core
from personality import relationship_events as rev

U = 'u-test'


def ev(eid, confidence='high', published='2024-09-19', summary=None):
    return {'id': eid, 'url': f'https://example.com/{eid}', 'publisher': 'Example Times', 'published': published,
            'accessed': '2026-11-04', 'confidence': confidence,
            'summary': summary or f'Source {eid} shows the coach explaining a decision to reporters.'}


def profile(status='approved'):
    return {
        'schema_version': cp.SCHEMA_VERSION, 'key': 'coach:CAR:HC', 'name': 'Dave Canales', 'role': 'HC',
        'team': 'CAR',
        'seed_ref': {'table': 'COACHES_DATA', 'team': 'CAR', 'role': 'HC', 'name': 'Dave Canales'},
        'sliders': {'temperament': 3, 'sociability': 6, 'confidence': 5, 'openness': 5, 'warmth': 8, 'drive': 7},
        'slider_evidence': {'temperament': ['E1'], 'sociability': ['E2'], 'warmth': ['E2', 'E3'], 'drive': ['E1']},
        'traits': ['SCHEME_NERD', 'QB_WHISPERER', 'MEASURED'],
        'trait_evidence': {'SCHEME_NERD': ['E1'], 'QB_WHISPERER': ['E2'], 'MEASURED': ['E3']},
        'views': [{'text': f'View number {i} about the game.', 'when': w} for i, w in enumerate(
            (['SCHEME'], ['QB', 'YOUTH'], ['LOSING_STREAK'], ['WIN_STREAK', 'CONTENDER'], ['DRAFT'], ['COACHING']))],
        'signatures': ['opens with the injury report', {'text': 'credits the line first', 'detect': 'the line'}],
        'want': 'to build an offense that lasts', 'fear': 'losing the room',
        'voice': 'Calm, upbeat, explains the why. Deflects personnel questions.',
        'ties': [{'target': {'kind': 'coach', 'seed_ref': {'table': 'COACHES_DATA', 'team': 'SEA', 'role': 'HC'}},
                  'relationship_kind': 'collaboration', 'outcome': 'positive', 'evidence': ['E3']},
                 {'target': {'kind': 'player', 'name': 'Bryce Young', 'team_2026': 'CAR'},
                  'relationship_kind': 'mentorship', 'outcome': 'mixed', 'evidence': ['E4']}],
        'evidence': [ev('E1'), ev('E2'), ev('E3', 'medium'), ev('E4', 'low')],
        'review': ({'status': status, 'reviewer': 'JR', 'reviewed_at': '2026-11-04', 'notes': None}
                   if status in cp.REVIEWED else {'status': status, 'reviewer': None, 'reviewed_at': None,
                                                  'notes': None}),
        '_file': 'CAR_HC.json',
    }


def errors(doc):
    return cp.validate_profile_file(doc)


def has(errs, fragment):
    return any(fragment in e for e in errs)


def test_seed_index_has_96_people_without_importing_the_seeder(monkeypatch):
    import sys
    # Other tests (test_watch_save) import the seeder first; drop it so the check is order-independent.
    monkeypatch.delitem(sys.modules, 'db.seed_clean_db', raising=False)
    idx = cp.seed_index()
    assert len(idx) == 96
    assert idx[('COACHES_DATA', 'CAR', 'HC')]['name'] == 'Dave Canales'
    assert idx[('COORDINATORS_DATA', 'CAR', 'DC')]['name'] == 'Ejiro Evero'
    assert 'db.seed_clean_db' not in sys.modules


def test_valid_fixture_passes():
    assert errors(profile()) == []
    assert errors(profile('draft')) == []


def mutate_slider_total(d):
    d['sliders'] = {'temperament': 10, 'sociability': 10, 'confidence': 5, 'openness': 5, 'warmth': 8, 'drive': 7}
    d['slider_evidence'].update(confidence=['E1'], openness=['E1'])


def copy16(d):
    d['evidence'][0]['summary'] = ('one two three four five six seven eight nine ten eleven twelve thirteen '
                                   'fourteen fifteen sixteen words in a row')
    d['want'] = ('to say one two three four five six seven eight nine ten eleven twelve thirteen fourteen '
                 'fifteen sixteen')


@pytest.mark.parametrize('mutate,fragment', [
    (lambda d: d.update(_file='KC_HC.json'), 'file name'),
    (lambda d: d.update(key='coach:CAR:OC'), 'key'),
    (lambda d: d['seed_ref'].update(team='XXX'), 'matches no seed row'),
    (lambda d: d['seed_ref'].update(name='Someone Else'), 'differs from the seed row'),
    (mutate_slider_total, 'outside 24-36'),
    (lambda d: d['sliders'].update(confidence=3, openness=7), 'slider confidence=3 cites no evidence'),
    (lambda d: d['slider_evidence'].update(warmth=['E9']), 'unknown evidence'),
    (lambda d: (d['traits'].append('SPOTLIGHT_SEEKER'), d['trait_evidence'].update(SPOTLIGHT_SEEKER=['E1'])),
     'not eligible for coaches'),
    (lambda d: (d['traits'].append('OLD_SCHOOL'), d['traits'].append('ANALYTICS_BELIEVER'),
                d['trait_evidence'].update(OLD_SCHOOL=['E1'], ANALYTICS_BELIEVER=['E1'])), 'conflicts'),
    (lambda d: (d['traits'].extend(['FILM_JUNKIE', 'HISTORIAN']),
                d['trait_evidence'].update(FILM_JUNKIE=['E1'], HISTORIAN=['E1'])), 'more than two traits'),
    (lambda d: d.update(traits=['SCHEME_NERD', 'QB_WHISPERER']), 'traits must number'),
    (lambda d: d['trait_evidence'].pop('MEASURED'), 'trait MEASURED cites no evidence'),
    (lambda d: d['views'][0].update(when=['NOT_A_TAG']), 'unknown tags'),
    (lambda d: d['views'][0].update(when=[]), 'when tags'),
    (lambda d: d.update(views=d['views'][:5]), 'views must number'),
    (lambda d: d.update(signatures=['one']), 'signatures must number'),
    (lambda d: d.update(fear=' '), 'fear must be nonempty'),
    (lambda d: d['evidence'][0].update(published='2026-09-09'), 'after the source cutoff'),
    (lambda d: d['evidence'][0].update(url='not a url'), 'url'),
    (lambda d: d['evidence'][0].update(confidence='certain'), 'confidence must be'),
    (lambda d: d['trait_evidence'].update(MEASURED=['E4']), 'low-confidence E4'),
    (lambda d: d['slider_evidence'].update(warmth=['E4']), 'low-confidence E4'),
    (copy16, 'copies more than 15'),
    (lambda d: d['ties'].append({'target': {'kind': 'reporter', 'name': 'A Writer', 'team_2026': 'CAR'},
                                 'relationship_kind': 'conflict', 'outcome': 'negative', 'evidence': ['E1']}),
     'no reporters'),
    (lambda d: d['ties'][0].update(evidence=[]), 'ties[0] cites no evidence'),
    (lambda d: d['review'].update(status='maybe'), 'review status'),
    (lambda d: d['review'].update(reviewer=None), 'names its reviewer'),
])
def test_each_rule_fails_with_its_message(mutate, fragment):
    d = profile()
    mutate(d)
    errs = errors(d)
    assert has(errs, fragment), errs


def test_fifteen_copied_words_are_allowed():
    d = profile()
    d['evidence'][0]['summary'] = 'one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen'
    d['want'] = 'to say one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen'
    assert errors(d) == []


def test_low_source_may_back_a_tie_or_voice():
    assert profile()['ties'][1]['evidence'] == ['E4'] and errors(profile()) == []


def test_core_initial_values_are_accepted_by_core_initialize():
    values = cp.core_initial_values(profile())
    assert set(values['evidence']) == {'slider:temperament', 'slider:sociability', 'slider:warmth', 'slider:drive',
                                       'trait:SCHEME_NERD', 'trait:QB_WHISPERER', 'trait:MEASURED'}
    warmth = values['evidence']['slider:warmth']
    assert warmth['confidence'] == 'medium' and warmth['features'] == ['all'] and warmth['reviewer'] == 'JR'
    assert warmth['source'] == 'https://example.com/E2' and warmth['date'] == '2024-09-19'
    prof = core.initialize(core.Identity(U, 'coach', 'curated-coach:CAR:HC:2026'), 'real', 2026, **values)
    view = core.feature_view(prof, 'press')
    assert view['sliders'] == profile()['sliders'] and view['traits'] == profile()['traits']


def test_drafts_and_rejected_files_do_not_leave():
    for status in ('draft', 'rejected'):
        with pytest.raises(ValueError):
            cp.core_initial_values(profile(status))
        with pytest.raises(ValueError):
            cp.persona_for(profile(status))
        with pytest.raises(ValueError):
            cp.tie_events(profile(status), lambda t: None)


def test_lifecycle_entries_skip_drafts_and_fill_birth_keys_and_aliases():
    oc = profile('edited')
    oc.update(key='coach:CAR:OC', role='OC', name='Brad Idzik', _file='CAR_OC.json',
              seed_ref={'table': 'COORDINATORS_DATA', 'team': 'CAR', 'role': 'OC', 'name': 'Brad Idzik'})
    oc['ties'] = []
    assert errors(oc) == []
    entries = cp.lifecycle_entries([profile('draft'), profile('rejected'), oc, profile()])
    assert [e['key'] for e in entries] == ['coach:CAR:HC', 'coach:CAR:OC']
    hc, ocx = entries
    assert hc['birth_key'] == 'curated-coach:CAR:HC:2026' and hc['role'] == 'coach' and hc['origin'] == 'real'
    assert hc['alias'] == {'namespace': 'db-coach', 'seed_ref': profile()['seed_ref']}
    assert ocx['alias']['namespace'] == 'db-coordinator'
    receipt = hc['review_receipt']
    assert receipt['reviewer'] == 'JR' and receipt['schema_version'] == cp.SCHEMA_VERSION
    assert receipt['file_sha256'] == cp.content_sha256(profile()) and len(receipt['file_sha256']) == 64
    assert hc['initial_values'] == cp.core_initial_values(profile())


def test_persona_for_renders_and_takes_a_stored_views_sliders_and_traits():
    persona = cp.persona_for(profile())
    text = legacy.render_persona(persona, tags={'QB'}, seed='t')
    assert 'Calm, upbeat' in text and 'QB whisperer' in text and 'View number 1' in text
    assert persona['signatures'][0] == {'text': 'opens with the injury report'}
    view = {'status': 'stored', 'sliders': dict.fromkeys(legacy.SLIDERS, 5), 'traits': ['TEACHER', 'MEASURED', 'LOYAL']}
    over = cp.persona_for(profile(), view)
    assert over['sliders'] == view['sliders'] and over['traits'] == view['traits']
    assert over['voice'] == persona['voice'] and over['want'] == persona['want']
    assert cp.persona_for(profile(), {'status': 'missing'})['sliders'] == profile()['sliders']
    assert 'Teacher' in legacy.render_persona(over, seed='t')


def person(pid):
    return {'kind': 'person', 'universe_id': U, 'role': 'coach' if pid.startswith('c') else 'player',
            'persona_id': pid}


def test_tie_events_build_valid_envelopes_and_report_unresolved():
    def resolve(target):
        if target['kind'] == 'coach' and target['seed_ref']['team'] == 'CAR':
            return person('c-car')
        if target['kind'] == 'coach' and target['seed_ref']['team'] == 'SEA':
            return person('c-sea')
        return None
    envs, unresolved = cp.tie_events(profile(), resolve)
    assert len(envs) == 1 and len(unresolved) == 1
    e = envs[0]
    assert rev.validate_event(e, team_registry={}) == e
    assert e['basis'] == 'reviewed_evidence' and e['reviewer'] == 'JR' and e['visibility'] == 'private'
    assert e['occurred_at'] == {'season': 2026, 'tick': 1} and e['kind'] == 'meaningful_interaction'
    assert e['source_event_id'] == e['event_id'] == 'mu:coach-tie:coach:CAR:HC:1'
    assert e['payload']['prehistory'] is True and e['payload']['relationship_kind'] == 'collaboration'
    assert e['payload']['significance'] == 'meaningful' and e['confidence'] == 'medium'
    assert e['subject']['persona_id'] == 'c-car' and e['target']['persona_id'] == 'c-sea'
    assert unresolved[0]['index'] == 2 and 'not in the league' in unresolved[0]['reason']
    envs, unresolved = cp.tie_events(profile(), lambda t: None)
    assert envs == [] and len(unresolved) == 2 and 'himself' in unresolved[0]['reason']


def test_load_profiles_skips_the_template_and_filters_teams(tmp_path):
    import json
    d = cp.file_doc(profile())
    (tmp_path / 'CAR_HC.json').write_text(json.dumps(d))
    (tmp_path / '_TEMPLATE.json').write_text(json.dumps(d))
    (tmp_path / 'KC_HC.json').write_text(json.dumps(d))
    assert [x['_file'] for x in cp.load_profiles(tmp_path)] == ['CAR_HC.json', 'KC_HC.json']
    assert [x['_file'] for x in cp.load_profiles(tmp_path, teams=['car'])] == ['CAR_HC.json']
    loaded = cp.load_profiles(tmp_path, teams=['KC'])[0]
    assert has(errors(loaded), 'file name')


def test_template_has_only_placeholder_identity_errors():
    import json
    t = json.loads((cp.REPO_ROOT / cp.PROFILE_DIR / '_TEMPLATE.json').read_text())
    t['_file'] = '_TEMPLATE.json'
    errs = errors(t)
    assert errs and all('role' in e or 'seed_ref' in e or 'key' in e for e in errs), errs
    fixed = deepcopy(t)
    fixed.update(key='coach:CAR:HC', team='CAR', role='HC', name='Dave Canales',
                 seed_ref={'table': 'COACHES_DATA', 'team': 'CAR', 'role': 'HC', 'name': 'Dave Canales'})
    assert errors(cp.with_inference(fixed)) == []


# ------------------------------------------------- v2 schema (INFERRED_SLIDERS_AND_FOCUS_PLAN.md §2.1, §4) ---

from personality import slider_inference as si  # noqa: E402

YOUNG = {'kind': 'player', 'name': 'Bryce Young', 'team_2026': 'CAR'}


def profile_v2(status='approved', **over):
    d = profile(status)
    d['schema_version'] = cp.SCHEMA_VERSION_V2
    del d['want'], d['fear']
    d['motivation'] = {'want': 'to build an offense that lasts', 'fear': 'losing the room'}
    d['focus'] = {'want': {'text': 'to turn Bryce Young into a winning starter', 'subjects': [YOUNG],
                           'until_season': None}, 'fear': None}
    d.update(over)
    return cp.with_inference(d)


def test_v2_valid_and_v1_still_valid():
    assert errors(profile_v2()) == []
    assert errors(profile()) == []
    assert si.neutral_sliders(profile_v2()) == ['confidence', 'openness']


def test_v2_field_rules():
    d = profile_v2()
    d['want'] = 'old field'
    assert has(errors(d), 'unknown fields')
    d = profile()
    d['motivation'] = {'want': 'x', 'fear': 'y'}
    assert has(errors(d), 'unknown fields')
    d = profile_v2()
    del d['slider_inference']
    assert has(errors(d), 'slider_inference must be')


def test_v2_inference_block_rules():
    d = profile_v2()
    d['slider_inference']['values'] = dict(d['slider_inference']['values'], warmth=5)
    assert has(errors(d), 'cover exactly')
    d = profile_v2()
    s = 'openness'
    d['slider_inference']['values'][s] = 10 - d['slider_inference']['values'][s]
    assert has(errors(d), 'stale')
    d = profile_v2()
    d['slider_inference']['values']['openness'] = 9
    d['slider_inference']['locked'] = ['openness']
    assert errors(d) == []
    d['slider_inference']['locked'] = ['warmth']
    assert has(errors(d), 'not a no-evidence slider')
    d = profile_v2()
    d['traits'] = ['ANALYTICS_BELIEVER', 'QB_WHISPERER', 'MEASURED']
    d['trait_evidence'] = {'ANALYTICS_BELIEVER': ['E1'], 'QB_WHISPERER': ['E2'], 'MEASURED': ['E3']}
    fresh = cp.with_inference(d)['slider_inference']['values']
    if fresh != d['slider_inference']['values']:
        assert has(errors(d), 'stale')
    assert errors(cp.with_inference(d)) == []


def test_v2_effective_band():
    d = profile_v2(sliders={'temperament': 10, 'sociability': 10, 'confidence': 5, 'openness': 5, 'warmth': 10,
                            'drive': 10},
                   slider_evidence={'temperament': ['E1'], 'sociability': ['E2'], 'warmth': ['E2'], 'drive': ['E1']})
    assert has(errors(d), 'effective slider total')


def test_v2_motivation_and_focus_rules():
    d = profile_v2()
    d['motivation']['want'] = 'to win a title with Young under center'
    assert has(errors(d), 'names Bryce Young')
    d = profile_v2()
    d['focus']['want']['text'] = ' '.join(['word'] * 15)
    assert has(errors(d), '1-14 words')


def test_core_initial_values_overlay_with_provenance():
    d = profile_v2()
    vals = cp.core_initial_values(d)
    for s, v in d['slider_inference']['values'].items():
        assert vals['sliders'][s] == v
        assert si.is_inferred(vals['evidence']['slider:' + s])
    assert vals['sliders']['warmth'] == 8 and not si.is_inferred(vals['evidence']['slider:warmth'])
    core.initialize(core.Identity('t', 'coach', 'x'), 'real', 2026, **vals)
    d['slider_inference']['locked'] = ['openness']
    vals = cp.core_initial_values(d)
    assert vals['evidence']['slider:openness']['source'] == 'jr-set:2026-11-04'
    v1 = cp.core_initial_values(profile())
    assert v1['sliders'] == profile()['sliders'] and 'slider:confidence' not in v1['evidence']


def test_persona_for_v1_v2_and_stored_focus():
    p1 = cp.persona_for(profile())
    assert p1['want'] == 'to build an offense that lasts' and 'focus_want' not in p1
    p2 = cp.persona_for(profile_v2())
    assert p2['want'] == 'to build an offense that lasts' and p2['fear'] == 'losing the room'
    assert p2['focus_want'] == 'to turn Bryce Young into a winning starter' and 'focus_fear' not in p2
    assert p2['sliders'] == si.effective_sliders(profile_v2())
    stored = {'want': {'text': 'to win big with Andy Dalton running his offense'}, 'fear': None}
    p3 = cp.persona_for(profile_v2(), focus=stored)
    assert p3['focus_want'] == 'to win big with Andy Dalton running his offense' and 'focus_fear' not in p3
    with pytest.raises(ValueError):
        cp.persona_for(profile_v2('draft'))


def test_convert_v2_mechanical():
    new = cp.convert_v2(profile(), cp.SCHEMA_VERSION_V2, today='2026-11-07')
    assert new['schema_version'] == cp.SCHEMA_VERSION_V2 and 'want' not in new
    assert new['motivation'] == {'want': 'to build an offense that lasts', 'fear': 'losing the room'}
    assert new['focus'] == {'want': None, 'fear': None}
    keys = list(new)
    assert keys.index('slider_inference') == keys.index('slider_evidence') + 1
    assert new['review']['status'] == 'draft'
    assert 'converted to v2 on 2026-11-07' in new['review']['notes'] and 'was approved by JR' in new['review']['notes']
    assert errors(new) == []

