"""Focus model: file rules, staleness, templates, drafting and change notes
(INFERRED_SLIDERS_AND_FOCUS_PLAN.md §2.4, §4). Pure, offline."""
import json

import pytest

from personality import focus as fo

TEMPLATES = fo.load_templates()
LAMAR = {'kind': 'player', 'name': 'Lamar Jackson', 'team_2026': 'BAL'}


def doc(**over):
    d = {'team': 'BAL', 'role': 'HC',
         'motivation': {'want': 'to prove he can build a lasting contender',
                        'fear': 'being judged against the coach he replaced'},
         'focus': {'want': {'text': 'a title with Lamar Jackson in his prime', 'subjects': [LAMAR],
                            'until_season': None},
                   'fear': None},
         'ties': [{'target': {'kind': 'gm', 'name': 'Eric DeCosta', 'team_2026': 'BAL'}}]}
    d.update(over)
    return d


def facts(**over):
    f = {'persona_id': 'p1', 'role': 'coach', 'seat': 'HC', 'team': 'BAL', 'team_name': 'Ravens',
         'direction': 'CONTEND', 'made_playoffs': True, 'won_super_bowl': False, 'hot_seat': False,
         'people': {'qb1': {'kind': 'player', 'name': 'Tyler Shough', 'binding': {'namespace': 'db-player',
                                                                                   'external_id': '9'}},
                    'young_star': None, 'top_defender': None, 'top_weapon': None, 'head_coach': None, 'gm': None},
         'subject_status': {}, 'source_ids': [], 'missing': {}}
    f.update(over)
    return f


def row(**over):
    r = {'slot': 'want', 'text': 'a title with Lamar Jackson in his prime',
         'subjects': [{'kind': 'player', 'name': 'Lamar Jackson',
                       'binding': {'namespace': 'db-player', 'external_id': '8'}}],
         'anchor': {'team': 'BAL', 'seat': 'HC'}, 'until_season': None}
    r.update(over)
    return r


def test_valid_file_focus():
    assert fo.validate_file_focus(doc()) == []


def test_word_caps_and_shapes():
    long = ' '.join(['word'] * 15)
    errs = fo.validate_file_focus(doc(motivation={'want': long, 'fear': 'x'}))
    assert any('motivation.want must be at most 14' in e for e in errs)
    d = doc()
    d['focus']['want']['text'] = long
    assert any('1-14 words' in e for e in fo.validate_file_focus(d))
    d = doc()
    d['focus']['want']['subjects'] = [LAMAR, LAMAR, LAMAR]
    assert any('at most 2' in e for e in fo.validate_file_focus(d))
    d = doc()
    d['focus']['want']['subjects'] = [{'kind': 'reporter', 'name': 'x', 'team_2026': 'BAL'}]
    assert any('kind must be' in e for e in fo.validate_file_focus(d))
    d = doc()
    d['focus']['want']['subjects'] = [{'kind': 'coach', 'seed_ref': {'table': 'COACHES_DATA', 'team': 'BAL',
                                                                     'role': 'HC', 'name': 'X'}}]
    assert any('holder himself' in e for e in fo.validate_file_focus(d))
    assert fo.validate_file_focus(doc(focus={'want': None})) != []
    assert any('motivation must be' in e for e in fo.validate_file_focus(doc(motivation='x')))


def test_motivation_must_not_name_subjects_or_ties():
    errs = fo.validate_file_focus(doc(motivation={'want': 'to win with Jackson', 'fear': 'losing'}))
    assert any('names Lamar Jackson' in e for e in errs)
    errs = fo.validate_file_focus(doc(motivation={'want': 'to win', 'fear': 'falling out with DeCosta'}))
    assert any('names Eric DeCosta' in e for e in errs)
    assert fo.validate_file_focus(doc(motivation={'want': 'to win a jackpot', 'fear': 'losing'})) == []


def test_until_season():
    d = doc()
    d['focus']['want']['until_season'] = 2025
    assert any('until_season' in e for e in fo.validate_file_focus(d))
    d['focus']['want']['until_season'] = 2028
    assert fo.validate_file_focus(d) == []


@pytest.mark.parametrize('status,cause', [('retired', 'SUBJECT_RETIRED'), ('elsewhere', 'SUBJECT_LEFT_TEAM'),
                                          ('not_in_seat', 'SUBJECT_LEFT_ROLE'), ('gone', 'SUBJECT_LEFT_LEAGUE')])
def test_stale_subject_causes(status, cause):
    f = facts(subject_status={'db-player:8': {'status': status, 'team': None}})
    assert fo.stale_causes(row(), f, 2030) == [{'cause': cause, 'subject': 'Lamar Jackson'}]


def test_unknown_and_none_never_stale():
    assert fo.stale_causes(row(), facts(subject_status={'db-player:8': {'status': 'unknown'}}), 2030) == []
    assert fo.stale_causes(row(), facts(subject_status={'db-player:8': {'status': 'with_team'}}), 2030) == []
    assert fo.stale_causes(row(), facts(team=None, seat=None), 2030) == []
    assert fo.stale_causes(row(), None, 2030) == []


def test_unresolved_holder_moved_and_expired():
    r = row(subjects=[{'kind': 'player', 'name': 'Lamar Jackson', 'binding': None}])
    assert fo.stale_causes(r, facts(), 2030) == [{'cause': 'SUBJECT_UNRESOLVED', 'subject': 'Lamar Jackson'}]
    assert {'cause': 'HOLDER_MOVED'} in fo.stale_causes(row(), facts(team='NYJ'), 2030)
    assert {'cause': 'HOLDER_MOVED'} in fo.stale_causes(row(), facts(seat='OC'), 2030)
    assert fo.stale_causes(row(until_season=2029), facts(), 2030) == [{'cause': 'EXPIRED'}]
    assert fo.stale_causes(row(until_season=2030), facts(), 2030) == []


def test_templates_load_with_generics_and_caps():
    assert len(TEMPLATES) >= 40
    ids = {t['template_id'] for t in TEMPLATES}
    for seat in fo.SEATS:
        for slot in fo.SLOTS:
            assert f'GENERIC_{seat}_{slot.upper()}' in ids


def _write(tmp_path, templates):
    p = tmp_path / 't.json'
    p.write_text(json.dumps({'version': fo.FOCUS_RULES_VERSION, 'templates': templates}))
    return p


def test_bad_templates_rejected(tmp_path):
    base = json.loads((fo.REPO_ROOT / fo.TEMPLATES_FILE).read_text())['templates']
    with pytest.raises(fo.FocusError, match='not in needs'):
        fo.load_templates(_write(tmp_path, base + [dict(template_id='X', seats=['HC'], slot='want',
                                                        text='win with {qb1}')]))
    with pytest.raises(fo.FocusError, match='exceeds'):
        fo.load_templates(_write(tmp_path, base + [dict(template_id='X', seats=['HC'], slot='want',
                                                        text=' '.join(['w'] * 15))]))
    with pytest.raises(fo.FocusError, match='generic'):
        fo.load_templates(_write(tmp_path, [t for t in base if t['template_id'] != 'GENERIC_DC_FEAR']))
    with pytest.raises(fo.FocusError, match='duplicate'):
        fo.load_templates(_write(tmp_path, base + [base[0]]))


def test_draft_deterministic_and_honours_when_needs():
    kw = dict(universe_id='u', persona_id='p1', season=2031, slot='want', facts=facts(), traits=[],
              templates=TEMPLATES)
    a = fo.draft_focus(**kw)
    assert a == fo.draft_focus(**kw)
    assert a['anchor'] == {'team': 'BAL', 'seat': 'HC'}
    t = next(x for x in TEMPLATES if x['template_id'] == a['template_id'])
    assert 'HC' in t['seats'] and t['slot'] == 'want'
    for k, v in t['when'].items():
        assert facts()[k] == v
    for k in t['needs']:
        assert facts()['people'][k]
    for season in range(2026, 2060):
        d = fo.draft_focus(**dict(kw, season=season))
        t = next(x for x in TEMPLATES if x['template_id'] == d['template_id'])
        assert 'young_star' not in t['needs'] and t['when'].get('direction', 'CONTEND') == 'CONTEND'
        if '{qb1}' in t['text']:
            assert d['subjects'][0]['name'] == 'Tyler Shough' and 'Tyler Shough' in d['text']
        assert len(fo.words(d['text'])) <= fo.MAX_FOCUS_WORDS


def test_draft_falls_back_to_generic():
    only = [t for t in TEMPLATES if t['template_id'].startswith('GENERIC_')]
    f = facts(direction=None, made_playoffs=None, won_super_bowl=None, hot_seat=None, people={})
    d = fo.draft_focus(universe_id='u', persona_id='p', season=2030, slot='fear', facts=f, traits=[],
                       templates=TEMPLATES)
    assert d['template_id'] == 'GENERIC_HC_FEAR' and d['subjects'] == []
    d = fo.draft_focus(universe_id='u', persona_id='p', season=2030, slot='want',
                       facts=facts(role='owner', seat='OWNER'), traits=[], templates=only)
    assert d['template_id'] == 'GENERIC_OWNER_WANT' and 'Ravens' in d['text']


def test_traits_any_weights_double():
    two = [dict(template_id='A', seats=['HC'], slot='want', when={}, needs=[], traits_any=[], text='a'),
           dict(template_id='B', seats=['HC'], slot='want', when={}, needs=[], traits_any=['LOYAL'], text='b')]
    picks = [fo.draft_focus(universe_id='u', persona_id=f'p{i}', season=2030, slot='want', facts=facts(),
                            traits=['LOYAL'], templates=two)['template_id'] for i in range(300)]
    assert picks.count('B') > picks.count('A') * 1.5


def test_change_note():
    new = {'text': 'a title with Tyler Shough in his prime', 'subjects': []}
    note = fo.change_note(row(), new, [{'cause': 'SUBJECT_RETIRED', 'subject': 'Lamar Jackson'}], 2031)
    assert note == '2031: with Lamar Jackson retired, he now wants a title with Tyler Shough in his prime.'
    note = fo.change_note(row(slot='fear'), {'text': 'that the league figures out his team'},
                          [{'cause': 'HOLDER_MOVED'}], 2032)
    assert note == '2032: in his new job, he now fears that the league figures out his team.'
    assert len(fo.change_note(row(), {'text': ' '.join(['w'] * 40)}, [], 2030).split()) <= fo.MAX_NOTE_WORDS
