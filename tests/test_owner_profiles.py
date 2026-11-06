"""personality/owner_profiles.py and the review tool's --kind owner (OWNERS_SCOUTS_VOTERS_PLAN.md §2.1-§2.2, §4).
Synthetic profiles and temp directories only; no DB, no network."""
from copy import deepcopy
import json
from pathlib import Path

from newsroom import personas as legacy
from personality import coach_profiles as cp
from personality import core
from personality import owner_profiles as op
from tools import coach_profile_review as tool

ROOT = Path(__file__).resolve().parent.parent
OWNER_TRAITS = ('MEDDLER', 'HANDS_OFF', 'DEEP_POCKETS', 'BUDGET_CONSCIOUS', 'HEADLINE_HUNTER', 'PATIENT_BUILDER')
SCOUT_TRAITS = ('TRAITS_CHASER', 'TAPE_GRINDER', 'CHARACTER_HAWK', 'FLOOR_SEEKER', 'CEILING_GAMBLER')


def ev(eid, confidence='high', published='2025-03-01'):
    return {'id': eid, 'url': f'https://example.com/{eid}', 'publisher': 'Example Times', 'published': published,
            'accessed': '2026-11-06', 'confidence': confidence,
            'summary': f'Source {eid} shows the owner explaining a franchise decision.'}


def profile(status='approved'):
    return {
        'schema_version': op.SCHEMA_VERSION, 'key': 'owner:CAR', 'name': 'David Tepper', 'role': 'OWNER',
        'team': 'CAR', 'seed_ref': {'table': 'OWNERS_DATA', 'team': 'CAR', 'name': 'David Tepper'},
        'sliders': {'temperament': 3, 'sociability': 5, 'confidence': 8, 'openness': 5, 'warmth': 5, 'drive': 8},
        'slider_evidence': {'temperament': ['E1'], 'confidence': ['E2'], 'drive': ['E1']},
        'traits': ['MEDDLER', 'DEEP_POCKETS', 'HEADLINE_HUNTER'],
        'trait_evidence': {'MEDDLER': ['E1'], 'DEEP_POCKETS': ['E2'], 'HEADLINE_HUNTER': ['E1', 'E3']},
        'views': [{'text': f'Owner view number {i}.', 'when': w} for i, w in enumerate(
            (['FRONT_OFFICE'], ['COACHING'], ['LOSING_STREAK'], ['CONTENDER'], ['CAP'], ['DRAFT']))],
        'signatures': ['talks about winning now', 'answers questions about the coach himself'],
        'want': 'a winner soon', 'fear': 'being seen as the problem',
        'voice': 'Blunt and impatient in public.',
        'ties': [{'target': {'kind': 'coach', 'seed_ref': {'table': 'COACHES_DATA', 'team': 'CAR', 'role': 'HC'}},
                  'relationship_kind': 'collaboration', 'outcome': 'positive', 'evidence': ['E3']},
                 {'target': {'kind': 'gm', 'name': 'Dan Morgan', 'team_2026': 'CAR'},
                  'relationship_kind': 'collaboration', 'outcome': 'positive', 'evidence': ['E4']}],
        'evidence': [ev('E1'), ev('E2'), ev('E3', 'medium'), ev('E4', 'low')],
        'review': ({'status': status, 'reviewer': 'JR', 'reviewed_at': '2026-11-06', 'notes': None}
                   if status in cp.REVIEWED else {'status': status, 'reviewer': None, 'reviewed_at': None,
                                                  'notes': None}),
        '_file': 'CAR.json',
    }


def has(errs, fragment):
    return any(fragment in e for e in errs)


def test_owners_data_has_the_32_seed_teams():
    teams = [t for t, _, _ in op.OWNERS_DATA]
    assert len(teams) == 32 == len(set(teams))
    assert set(teams) == {k[1] for k in cp.seed_index()}
    assert op.owner_index()['CAR'] == {'table': 'OWNERS_DATA', 'team': 'CAR', 'name': 'David Tepper',
                                       'start_season': 2018}


def test_catalogue_has_owner_and_scout_traits_that_pass_the_catalogue_checks():
    cat = legacy.load_traits()
    assert legacy.validate_trait_catalogue(cat)
    for t in OWNER_TRAITS:
        assert cat[t]['group'] == 'ownership' and cat[t]['roles']['owner'] > 0
        assert all(w == 0 for r, w in cat[t]['roles'].items() if r != 'owner')
        assert cat[t]['effects'] == {}
    for t in SCOUT_TRAITS:
        assert cat[t]['group'] == 'scouting' and cat[t]['roles']['scout'] > 0
        assert all(w == 0 for r, w in cat[t]['roles'].items() if r != 'scout')
    for a, b in (('MEDDLER', 'HANDS_OFF'), ('DEEP_POCKETS', 'BUDGET_CONSCIOUS'),
                 ('HEADLINE_HUNTER', 'PATIENT_BUILDER'), ('FLOOR_SEEKER', 'CEILING_GAMBLER')):
        assert b in cat[a]['conflicts'] and a in cat[b]['conflicts']


def test_an_owner_can_hold_one_trait_from_each_of_the_three_pairs():
    p = core.initialize(core.Identity('u', 'owner', 'curated-owner:CAR:2026'), 'real', 2026,
                        sliders={s: 5 for s in legacy.SLIDERS}, traits=['MEDDLER', 'DEEP_POCKETS', 'HEADLINE_HUNTER'])
    assert sorted(p['traits']) == ['DEEP_POCKETS', 'HEADLINE_HUNTER', 'MEDDLER']


def test_new_traits_do_not_change_other_roles_seeded_traits():
    cat = legacy.load_traits()
    old = {k: v for k, v in cat.items() if k not in OWNER_TRAITS + SCOUT_TRAITS}
    for role in ('player', 'coach', 'writer', 'gm', 'media'):
        for pid in ('a', 'b', 'c', 'd'):
            assert legacy.seed_traits(role, pid, traits=cat) == legacy.seed_traits(role, pid, traits=old)


def test_valid_fixture_passes():
    assert op.validate_profile_file(profile()) == []
    assert op.validate_profile_file(profile('draft')) == []


def test_identity_rules():
    d = profile()
    d['key'] = 'owner:DAL'
    assert has(op.validate_profile_file(d), 'does not match team')
    d = profile()
    d['_file'] = 'CAR_HC.json'
    assert has(op.validate_profile_file(d), 'file name')
    d = profile()
    d['role'] = 'HC'
    assert has(op.validate_profile_file(d), 'role must be OWNER')
    d = profile()
    d['name'] = d['seed_ref']['name'] = 'Someone Else'
    assert has(op.validate_profile_file(d), 'differs from OWNERS_DATA')
    d = profile()
    d['seed_ref']['table'] = 'COACHES_DATA'
    assert has(op.validate_profile_file(d), 'matches no OWNERS_DATA row')


def test_trait_rules_use_owner_eligibility():
    d = profile()
    d['traits'][0] = 'SCHEME_NERD'
    d['trait_evidence'] = {'SCHEME_NERD': ['E1'], 'DEEP_POCKETS': ['E2'], 'HEADLINE_HUNTER': ['E1']}
    assert has(op.validate_profile_file(d), 'not eligible for owners')
    d = profile()
    d['traits'] = ['MEDDLER', 'HANDS_OFF', 'DEEP_POCKETS']
    d['trait_evidence'] = {t: ['E1'] for t in d['traits']}
    assert has(op.validate_profile_file(d), 'conflicts with')
    d = profile()
    d['traits'].append('LEGACY_BUILDER')
    d['trait_evidence']['LEGACY_BUILDER'] = ['E4']
    assert has(op.validate_profile_file(d), 'low-confidence')


def test_coach_rules_still_apply():
    d = profile()
    d['evidence'][0]['published'] = '2026-09-10'
    assert has(op.validate_profile_file(d), 'after the source cutoff')
    d = profile()
    d['sliders']['warmth'] = 9
    assert has(op.validate_profile_file(d), 'slider warmth=9 cites no evidence')
    d = profile()
    d['views'][0]['when'] = ['NOPE']
    assert has(op.validate_profile_file(d), 'unknown tags')
    d = profile()
    d['ties'][0]['target'] = {'kind': 'writer', 'name': 'x', 'team_2026': 'CAR'}
    assert has(op.validate_profile_file(d), 'not allowed')
    d = profile()
    d['ties'][0]['target']['seed_ref']['role'] = 'GM'
    assert has(op.validate_profile_file(d), 'coach seed row')
    d = profile()
    d['review'] = {'status': 'approved'}
    assert has(op.validate_profile_file(d), 'reviewer')


def test_lifecycle_entries_use_role_owner_and_skip_drafts():
    entries = op.lifecycle_entries([profile(), dict(profile('draft'), key='owner:ZZZ')])
    assert len(entries) == 1
    e = entries[0]
    assert e['role'] == 'owner' and e['origin'] == 'real'
    assert e['birth_key'] == 'curated-owner:CAR:2026'
    assert e['alias']['namespace'] == 'db-owner' and e['alias']['table'] == 'team_owners'
    assert e['review_receipt']['schema_version'] == 'owner-profile-v1'
    p = core.initialize(core.Identity('u', 'owner', e['birth_key']), 'real', 2026, **e['initial_values'])
    assert p['traits'] == ['MEDDLER', 'DEEP_POCKETS', 'HEADLINE_HUNTER']


def test_persona_for_and_tie_events():
    from tests.test_coach_profiles import person
    persona = op.persona_for(profile())
    assert legacy.render_persona(persona)

    def resolve(target):
        if target['kind'] == 'owner':
            return person('o-car')
        if target['kind'] == 'coach':
            return person('c-car')
        return None
    envs, unresolved = op.tie_events(profile(), resolve)
    assert len(envs) == 1 and envs[0]['source_kind'] == 'owner_profile'
    assert envs[0]['event_id'] == 'mu:owner-tie:owner:CAR:1' and envs[0]['payload']['prehistory'] is True
    assert envs[0]['subject']['persona_id'] == 'o-car' and envs[0]['confidence'] == 'medium'
    assert [u['index'] for u in unresolved] == [2]
    envs, unresolved = op.tie_events(profile(), lambda t: None)
    assert envs == [] and 'himself' in unresolved[0]['reason']
    try:
        op.tie_events(profile('draft'), resolve)
        raise AssertionError('a draft must not leave the module')
    except ValueError:
        pass


def test_review_tool_validate_report_and_apply_with_kind_owner(tmp_path, capsys):
    doc = profile('draft')
    (tmp_path / 'CAR.json').write_text(json.dumps(op.file_doc(doc), indent=2))
    assert tool.main(['validate', '--kind', 'owner', '--dir', str(tmp_path), '--teams', 'CAR,DAL']) == 0
    out = capsys.readouterr().out
    assert 'owner:CAR: ok' in out and 'not written yet: DAL' in out
    page = tmp_path / 'r.md'
    assert tool.main(['report', '--kind', 'owner', '--dir', str(tmp_path), '--teams', 'CAR', '--out', str(page)]) == 0
    text = page.read_text()
    assert text.startswith('# Owner profile review') and '**Decision (owner:CAR):**' in text
    dec = tmp_path / 'd.json'
    dec.write_text(json.dumps({'owner:CAR': {'status': 'approved'}}))
    assert tool.main(['apply-review', '--kind', 'owner', '--decisions', str(dec), '--dir', str(tmp_path),
                      '--today', '2026-11-07']) == 0
    stored = json.loads((tmp_path / 'CAR.json').read_text())
    assert stored['review']['status'] == 'approved' and stored['review']['reviewed_at'] == '2026-11-07'


def test_review_tool_on_the_owner_template(tmp_path, capsys):
    template = str(ROOT / op.PROFILE_DIR / '_TEMPLATE.json')
    assert tool.main(['validate', '--kind', 'owner', '--file', template]) == 1
    errs = capsys.readouterr().out
    assert 'matches no OWNERS_DATA row' in errs and '1 error(s)' in errs
    out = tmp_path / 't.md'
    assert tool.main(['report', '--kind', 'owner', '--file', template, '--out', str(out)]) == 0
    assert '### Sources' in out.read_text()


def test_coach_kind_is_still_the_default(tmp_path, capsys):
    from tests.test_coach_profiles import profile as coach_profile
    (tmp_path / 'CAR_HC.json').write_text(json.dumps(cp.file_doc(coach_profile('draft'))))
    assert tool.main(['validate', '--dir', str(tmp_path)]) == 0
    assert 'coach:CAR:HC: ok' in capsys.readouterr().out
    assert deepcopy(profile())['key'] == 'owner:CAR'
