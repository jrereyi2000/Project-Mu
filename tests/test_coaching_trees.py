"""personality/coaching_trees.py (COACHING_TREES_PLAN.md §2.2-§2.3, U2) on synthetic files and a synthetic seed
index: registry and batch validation, reviewed data, edge events, the in-sim rule and lineage labels."""
from copy import deepcopy

import pytest

from personality import coaching_trees as ct
from personality import relationship_events as rev

INDEX = {
    ('COACHES_DATA', 'SF', 'HC'): {'table': 'COACHES_DATA', 'team': 'SF', 'role': 'HC', 'name': 'Kyle Shanahan'},
    ('COACHES_DATA', 'LAR', 'HC'): {'table': 'COACHES_DATA', 'team': 'LAR', 'role': 'HC', 'name': 'Sean McVay'},
    ('COORDINATORS_DATA', 'SEA', 'OC'): {'table': 'COORDINATORS_DATA', 'team': 'SEA', 'role': 'OC',
                                         'name': 'Klint Kubiak'},
    ('COORDINATORS_DATA', 'SF', 'DC'): {'table': 'COORDINATORS_DATA', 'team': 'SF', 'role': 'DC',
                                        'name': 'Robert Saleh'},
    ('COACHES_DATA', 'KC', 'HC'): {'table': 'COACHES_DATA', 'team': 'KC', 'role': 'HC', 'name': 'Andy Reid'},
}
SF_HC = {'table': 'COACHES_DATA', 'team': 'SF', 'role': 'HC'}
LAR_HC = {'table': 'COACHES_DATA', 'team': 'LAR', 'role': 'HC'}
SEA_OC = {'table': 'COORDINATORS_DATA', 'team': 'SEA', 'role': 'OC'}
SF_DC = {'table': 'COORDINATORS_DATA', 'team': 'SF', 'role': 'DC'}
LONG = ('He spent three seasons as the quarterbacks coach on that staff before the head coach '
        'promoted him to run the passing game and call plays')
DRAFT = {'status': 'draft', 'reviewer': None, 'reviewed_at': None, 'notes': ''}
OK = {'status': 'approved', 'reviewer': 'JR', 'reviewed_at': '2026-11-08', 'notes': ''}


def ev(eid, confidence='high', **extra):
    return dict({'id': eid, 'url': f'https://example.com/{eid}', 'publisher': 'Pub', 'published': '2024-01-01',
                 'accessed': '2026-11-07', 'summary': f'Source {eid} says the two worked together.',
                 'confidence': confidence}, **extra)


def tree(key, root, parent=None, review=DRAFT):
    return {'key': key, 'label': f'{root.split()[-1]} tree', 'root': root, 'parent': parent,
            'summary': f'Coaches who worked under {root}.', 'evidence': ['T1', 'T2'], 'review': dict(review)}


def registry(review=DRAFT):
    return {'schema_version': ct.SCHEMA_VERSION, 'kind': 'registry',
            'trees': [tree('shanahan', 'Mike Shanahan', review=review),
                      tree('mcvay', 'Sean McVay', 'shanahan', review=review)],
            'evidence': [ev('T1'), ev('T2', 'medium')]}


def edge(mentor=SF_HC, protege=SEA_OC, stints=None, evidence=('E1', 'E2'), review=DRAFT, **over):
    e = {'key': f"{ct.profile_key(mentor)}>{ct.profile_key(protege)}", 'mentor': dict(mentor),
         'protege': dict(protege),
         'stints': stints or [{'team': 'SF', 'first_season': 2021, 'last_season': 2023, 'mentor_role': 'HC',
                               'protege_role': 'pass game coordinator'}],
         'outcome': 'positive', 'evidence': list(evidence), 'notes': '', 'review': dict(review)}
    e.update(over)
    return e


def batch(edges=None, members=None, letter='C', proposed=None):
    return {'schema_version': ct.SCHEMA_VERSION, 'kind': 'batch', 'batch': letter,
            'members': members if members is not None else
            [{'person': dict(SF_HC), 'trees': ['shanahan'], 'evidence': ['E1'], 'review': dict(DRAFT)}],
            'edges': edges if edges is not None else [edge()], 'proposed_trees': proposed or [],
            'evidence': [ev('E1'), ev('E2', 'low'), ev('E3', 'low', mentorship=True), ev('E4', 'low')]}


def errs(doc, reg=None):
    return ct.validate_batch(doc, registry=reg or registry(), index=INDEX)


# ------------------------------------------------------------------------------------------- validation ---

def test_person_key_normalizes():
    assert ct.person_key('A.J.  Brown') == 'p:aj brown' == ct.person_key('aj brown')


def test_valid_registry_and_batch_pass():
    assert ct.validate_registry(registry()) == []
    assert errs(batch()) == []


@pytest.mark.parametrize('mutate,message', [
    (lambda r: r['trees'][1].update(parent='walsh'), "parent 'walsh' is not a tree"),
    (lambda r: r['trees'][0].update(parent='mcvay'), 'parent cycle'),
    (lambda r: r['trees'][0].update(key='Bad-Key'), 'key must match'),
    (lambda r: r['trees'][0].update(evidence=['T1']), 'at least 2 sources'),
    (lambda r: r['trees'].append(deepcopy(r['trees'][0])), 'duplicate tree key'),
    (lambda r: r['evidence'][0].update(published='2026-10-01'), 'after the source cutoff'),
    (lambda r: r['trees'][0].update(review={'status': 'approved'}), 'reviewer'),
])
def test_registry_rules(mutate, message):
    r = registry()
    mutate(r)
    assert any(message in e for e in ct.validate_registry(r)), ct.validate_registry(r)


@pytest.mark.parametrize('mutate,message', [
    (lambda b: b['edges'][0].update(key='coach:SF:HC>coach:X:OC'), 'key must be'),
    (lambda b: b['edges'][0]['protege'].update(team='NYJ'), 'matches no seed row'),
    (lambda b: b['edges'][0].update(protege=dict(SF_HC), key='coach:SF:HC>coach:SF:HC'), 'same person'),
    (lambda b: b['edges'][0].update(evidence=['E1']), 'at least 2 sources'),
    (lambda b: b['edges'][0].update(evidence=['E2', 'E4']), 'medium or high'),
    (lambda b: b['edges'][0]['stints'][0].update(first_season=2023), 'fewer than 2 seasons'),
    (lambda b: b['edges'][0]['stints'][0].update(last_season=2020), 'first_season <= last_season'),
    (lambda b: b['edges'][0].update(outcome='negative'), 'outcome must be'),
    (lambda b: b['edges'].append(deepcopy(b['edges'][0])), 'duplicate edge'),
    (lambda b: b['members'][0].update(trees=['walsh']), "'walsh' is not in the registry"),
    (lambda b: (b['evidence'][0].update(summary=LONG), b['edges'][0].update(notes='So: ' + LONG)),
     'consecutive words'),
    (lambda b: b['edges'][0].update(review={'status': 'edited', 'reviewer': 'JR'}), 'reviewer'),
    (lambda b: b.update(batch='A'), 'not in batch A'),
])
def test_batch_rules(mutate, message):
    b = batch()
    mutate(b)
    found = errs(b)
    assert any(message in e for e in found), found


def test_one_season_needs_a_mentorship_source_and_proposed_trees_count():
    one = [{'team': 'SF', 'first_season': 2022, 'last_season': 2022, 'mentor_role': 'HC', 'protege_role': 'QB coach'}]
    assert errs(batch([edge(stints=one, evidence=['E1', 'E3'])])) == []
    prop = [dict(tree('kubiak', 'Gary Kubiak'), evidence=['E1', 'E2'])]
    members = [{'person': dict(SEA_OC), 'trees': ['kubiak'], 'evidence': ['E1'], 'review': dict(DRAFT)}]
    assert errs(batch(members=members, proposed=prop)) == []


def test_duplicate_edge_across_batches_fails():
    a, c = batch(), batch()
    a['_file'], c['_file'] = 'batch_C.json', 'batch_C2.json'
    c['batch'] = 'C'
    found = ct.validate_batches([a, dict(c, batch='X')], registry=registry(), index=INDEX)
    assert any('also in batch' in e for e in found['X'])


# ---------------------------------------------------------------------------------------- reviewed data ---

def test_reviewed_edges_drop_drafts_and_carry_the_facts():
    b = batch([edge(), edge(mentor=LAR_HC, review=OK, stints=[
        {'team': 'LAR', 'first_season': 2017, 'last_season': 2018, 'mentor_role': 'HC', 'protege_role': 'QB coach'},
        {'team': 'LAR', 'first_season': 2020, 'last_season': 2020, 'mentor_role': 'HC', 'protege_role': 'OC'}])])
    (e,) = ct.reviewed_edges([b], index=INDEX)
    assert (e['mentor_name'], e['protege_name'], e['seasons'], e['span']) == ('Sean McVay', 'Klint Kubiak', 3,
                                                                                [2017, 2020])
    assert e['basis'] == 'reviewed' and e['confidence'] == 'low' and e['reviewer'] == 'JR'
    assert ct.reviewed_pairs([e]) == {('p:sean mcvay', 'p:klint kubiak')}
    assert ct.reviewed_members([b], index=INDEX) == {}
    b['members'][0]['review'] = dict(OK)
    assert ct.reviewed_members([b], index=INDEX) == {'p:kyle shanahan': ['shanahan']}
    assert ct.registry_trees(registry()) == {}
    assert set(ct.registry_trees(registry(OK))) == {'shanahan', 'mcvay'}


def resolve(seed):
    return {'kind': 'person', 'universe_id': 'u1', 'role': 'coach', 'persona_id': ct.profile_key(seed)}


def test_edge_events_build_two_valid_facets_with_a_shared_source():
    (e,) = ct.reviewed_edges([batch([edge(review=OK)])], index=INDEX)
    envs, bad = ct.edge_events(e, resolve)
    assert bad == [] and len(envs) == 2
    mentee, mentor = envs
    assert mentee['source_event_id'] == mentor['source_event_id'] == 'mu:coach-tree:coach:SF:HC>coach:SEA:OC'
    assert mentee['event_id'].endswith('|mentee') and mentor['event_id'].endswith('|mentor')
    assert mentee['subject']['persona_id'] == 'coach:SEA:OC' and mentee['target']['persona_id'] == 'coach:SF:HC'
    assert mentee['payload']['direction'] == 'mentee' and mentor['payload']['direction'] == 'mentor'
    assert mentee['visibility'] == 'public' and mentee['basis'] == 'reviewed_evidence'
    assert rev.facet_key(mentee) != rev.facet_key(mentor)
    envs, bad = ct.edge_events(e, lambda s: None if s['team'] == 'SEA' else resolve(s))
    assert envs == [] and 'protege' in bad[0]['reason']


# ------------------------------------------------------------------------------------------- simulation ---

def row(season, team, role, name, eid, fired=False):
    return {'season': season, 'team_id': team, 'role': role, 'name': name, 'external_id': eid, 'fired': fired,
            'namespace': 'db-coach' if role == 'HC' else 'db-coordinator'}


def test_sim_edges_two_seasons_make_an_edge_one_does_not():
    rows = [row(2026, 1, 'HC', 'Ann Head', 1), row(2026, 1, 'OC', 'Bo Coord', 7), row(2026, 1, 'DC', 'Cy D', 8),
            row(2027, 1, 'HC', 'Ann Head', 1), row(2027, 1, 'OC', 'Bo Coord', 7)]
    (e,) = ct.sim_edges(rows, through=2027)
    assert (e['mentor_pk'], e['protege_pk'], e['seasons'], e['crossed_season']) == ('p:ann head', 'p:bo coord',
                                                                                     2, 2027)
    assert e['mentor_ref'] == {'namespace': 'db-coach', 'external_id': 1}
    assert e['protege_ref'] == {'namespace': 'db-coordinator', 'external_id': 7}
    assert ct.sim_edges(rows, through=2026) == []
    assert ct.sim_edges(rows, through=2027, reviewed_pairs={('p:ann head', 'p:bo coord')}) == []
    assert ct.sim_edges([dict(r, season=r['season'] - 2) for r in rows], through=2027) == []   # before 2026


def test_sim_edges_follow_across_teams_and_fired_is_mixed():
    rows = [row(2026, 1, 'HC', 'Ann Head', 1), row(2026, 1, 'OC', 'Bo Coord', 7),
            row(2027, 2, 'HC', 'Ann Head', 1), row(2027, 2, 'OC', 'Bo Coord', 7, fired=True),
            row(2028, 2, 'HC', 'Ann Head', 1), row(2028, 2, 'OC', 'New Guy', 9)]
    (e,) = ct.sim_edges(rows, through=2028)
    assert e['teams'] == [1, 2] and e['outcome'] == 'mixed'
    rows[-2] = row(2028, 2, 'HC', 'Other Head', 2)                      # the mentor left too
    assert ct.sim_edges(rows, through=2028)[0]['outcome'] == 'positive'


def test_sim_edges_skip_self_and_order_deterministically():
    rows = []
    for s in (2026, 2027):
        rows += [row(s, 2, 'HC', 'Zed Head', 2), row(s, 2, 'DC', 'Al D', 5), row(s, 1, 'HC', 'Amy Head', 1),
                 row(s, 1, 'OC', 'Amy  Head', 11)]                     # same person in both seats (normalized)
    out = ct.sim_edges(rows, through=2027)
    assert [e['mentor_pk'] for e in out] == ['p:zed head']
    assert ct.sim_edges(list(reversed(rows)), through=2027) == out


# --------------------------------------------------------------------------------------------- lineage ---

def e_(mentor, protege, seasons=3, basis='reviewed'):
    return {'mentor_pk': ct.person_key(mentor), 'mentor_name': mentor, 'protege_pk': ct.person_key(protege),
            'protege_name': protege, 'seasons': seasons, 'span': [2020, 2020 + seasons - 1], 'basis': basis}


TREES = {'shanahan': {'key': 'shanahan', 'label': 'Shanahan tree', 'root': 'Mike Shanahan', 'parent': None},
         'mcvay': {'key': 'mcvay', 'label': 'McVay tree', 'root': 'Sean McVay', 'parent': 'shanahan'}}


def test_lineage_own_membership_and_registry_parent():
    lin = ct.lineage('p:kyle shanahan', edges=[], registry=TREES, members={'p:kyle shanahan': ['shanahan']})
    assert lin['label'] == 'Shanahan tree' and lin['trees'][0]['key'] == 'shanahan'
    lin = ct.lineage(ct.person_key('Liam Coen'), edges=[e_('Sean McVay', 'Liam Coen', 5)], registry=TREES, members={})
    assert lin['label'] == 'McVay tree (Shanahan lineage)'
    assert [t['key'] for t in lin['trees']] == ['mcvay', 'shanahan']
    assert ct.lineage_line(lin) == 'Coaching tree: McVay tree (Shanahan lineage); served 5 seasons under Sean McVay.'


def test_lineage_walks_mentors_and_stops_at_depth():
    chain = [e_('Sean McVay', 'A One'), e_('A One', 'B Two'), e_('B Two', 'C Three'), e_('C Three', 'D Four')]
    assert ct.lineage('p:c three', edges=chain, registry=TREES, members={})['label'] == 'McVay tree (Shanahan lineage)'
    lin = ct.lineage('p:d four', edges=chain, registry=TREES, members={})
    assert lin['label'] is None and lin['mentors'][0]['name'] == 'C Three'
    assert ct.lineage_line(lin) == 'Served 3 seasons under C Three.'


def test_lineage_computes_a_sim_tree_at_two_hc_proteges():
    edges = [e_('Ann Head', 'Bo One', 2, 'simulated'), e_('Ann Head', 'Cy Two', 2, 'simulated'),
             e_('Ann Head', 'Di Three', 2, 'simulated')]
    lin = ct.lineage('p:di three', edges=edges, registry=TREES, members={}, head_coach_pks={'p:bo one'})
    assert lin['label'] is None
    lin = ct.lineage('p:di three', edges=edges, registry=TREES, members={},
                     head_coach_pks={'p:bo one', 'p:cy two'})
    assert lin['label'] == 'Head tree' and lin['trees'][0]['key'] == 'sim:p:ann head'
    own = ct.lineage('p:ann head', edges=edges, registry=TREES, members={}, head_coach_pks={'p:bo one', 'p:cy two'})
    assert own['root_of'][0]['label'] == 'Head tree' and len(own['proteges']) == 3
    assert ct.lineage_line(own).startswith('Root of the Head tree; mentor to Bo One')


def test_lineage_line_is_none_with_nothing_to_say():
    assert ct.lineage_line(ct.lineage('p:nobody', edges=[], registry=TREES, members={})) is None
    assert ct.lineage_line(None) is None
