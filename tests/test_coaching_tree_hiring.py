"""engines/coaching_tree_hiring.py (COACHING_TREES_PLAN.md §2.8, U9): the tree terms, their cap, follow candidates,
shadow versus active picks on a stubbed pool, and the switchboard read. Pure; no database."""
from engines import coaching_carousel as cc
from engines import coaching_tree_hiring as th
from personality import coaching_trees as ct


def edge(mentor, protege, seasons=3):
    return {'mentor_pk': ct.person_key(mentor), 'mentor_name': mentor, 'protege_pk': ct.person_key(protege),
            'protege_name': protege, 'seasons': seasons, 'span': [2026, 2028], 'basis': 'simulated'}


GRAPH = {'edges': [edge('Ann Head', 'Bo Coord'), edge('Ann Head', 'Cy Coord'), edge('Loser Head', 'Di Coord')]}
RECORDS = {'p:ann head': {'wins': 90, 'losses': 60, 'super_bowl_wins': 0},
           'p:loser head': {'wins': 40, 'losses': 80, 'super_bowl_wins': 0}}


def test_pedigree_for_head_coach_candidates():
    assert th.candidate_term({'name': 'Bo Coord', 'kind': 'POACHED'}, graph=GRAPH, mentor_records=RECORDS) == 0.10
    assert th.candidate_term({'name': 'Di Coord', 'kind': 'POACHED'}, graph=GRAPH, mentor_records=RECORDS) == 0.0
    sb = dict(RECORDS, **{'p:loser head': {'wins': 40, 'losses': 80, 'super_bowl_wins': 1}})
    assert th.candidate_term({'name': 'Di Coord', 'kind': 'POACHED'}, graph=GRAPH, mentor_records=sb) == 0.10
    assert th.candidate_term({'key': 'outside:X', 'kind': 'OUTSIDE'}, graph=GRAPH, mentor_records=RECORDS) == 0.0


def test_reunion_for_coordinator_openings_and_the_cap():
    c = {'name': 'Bo Coord', 'kind': 'REHIRED'}
    assert th.candidate_term(c, graph=GRAPH, hiring_hc_pk='p:ann head', mentor_records=RECORDS) == th.TREE_CAP
    assert th.TREE_REUNION <= th.TREE_CAP
    assert th.candidate_term(dict(c, kind='PROMOTED'), graph=GRAPH, hiring_hc_pk='p:ann head',
                             mentor_records=RECORDS) == 0.0
    assert th.candidate_term(c, graph=GRAPH, hiring_hc_pk='p:other', mentor_records=RECORDS) == 0.0
    for cand in ({'name': n, 'kind': k} for n in ('Bo Coord', 'Di Coord') for k in ('REHIRED', 'POACHED')):
        for hc in (None, 'p:ann head'):
            assert abs(th.candidate_term(cand, graph=GRAPH, hiring_hc_pk=hc, mentor_records=RECORDS)) <= th.TREE_CAP


def test_follow_candidates_are_his_out_of_work_proteges_in_the_role():
    ow = [{'coordinator_id': 7, 'name': 'Cy Coord', 'role': 'OC', 'team_id': 3},
          {'coordinator_id': 5, 'name': 'Bo Coord', 'role': 'OC', 'team_id': 2},
          {'coordinator_id': 6, 'name': 'Bo Coord Jr', 'role': 'OC', 'team_id': 2},
          {'coordinator_id': 8, 'name': 'Di Coord', 'role': 'OC', 'team_id': 4},
          {'coordinator_id': 9, 'name': 'Cy Coord', 'role': 'DC', 'team_id': 3}]
    out = th.follow_candidates('p:ann head', ow, graph=GRAPH, role='OC')
    assert [(c['coordinator_id'], c['kind'], c['score'], c['key']) for c in out] == \
        [(5, 'FOLLOWED', th.FOLLOW_SCORE, 'follow:5'), (7, 'FOLLOWED', th.FOLLOW_SCORE, 'follow:7')]
    assert th.follow_candidates('p:nobody', ow, graph=GRAPH, role='OC') == []


def _seed_where_terms_flip():
    pool = [{'key': 'a', 'name': 'Zed Plain', 'kind': 'POACHED', 'score': 0.5},
            {'key': 'b', 'name': 'Bo Coord', 'kind': 'POACHED', 'score': 0.5}]
    for i in range(500):
        seed = f's{i}'
        base = cc.pick(pool, seed)
        alt = cc.pick(th.adjusted(pool, graph=GRAPH, records=RECORDS), seed)
        if base['key'] != alt['key']:
            return pool, seed
    raise AssertionError('no seed where the tree term changes the pick')


def test_shadow_leaves_the_pick_and_active_applies_the_terms():
    pool, seed = _seed_where_terms_flip()
    base = cc.pick(pool, seed)
    assert base['key'] == 'a'                                       # the tree term only ever helps Bo Coord
    assert cc.pick(th.adjusted(pool, graph=GRAPH, records=RECORDS), seed)['key'] == 'b'
    assert [c['score'] for c in pool] == [0.5, 0.5]                 # adjusted copies, the pool is untouched


def test_active_can_pick_a_follower_over_the_stand_in():
    fpool = th.follow_candidates('p:ann head', [{'coordinator_id': 5, 'name': 'Bo Coord', 'role': 'DC',
                                                  'team_id': 2}], graph=GRAPH, role='DC')
    picks = {cc.pick([{'key': 'hc_staff', 'kind': 'HC_STAFF', 'score': 0.0}] + fpool, f'2030:AAA:DC:{i}')['kind']
             for i in range(60)}
    assert picks == {'HC_STAFF', 'FOLLOWED'}                        # a lean, not a certainty (seeded noise)


class Cur:
    def __init__(self, rows):
        self.rows, self.sql = list(rows), []

    def execute(self, sql, params=None):
        self.sql.append(sql)
        if sql.startswith(('SAVEPOINT', 'RELEASE', 'ROLLBACK')):
            return
        self.result = self.rows.pop(0) if self.rows else None
        if isinstance(self.result, Exception):
            raise self.result

    def fetchone(self):
        return self.result

    def fetchall(self):
        return self.result or []


def test_tree_mode_reads_off_without_a_league_or_on_errors():
    assert th.tree_mode(Cur([(False,)])) == 'off'
    assert th.tree_mode(Cur([(True,), (None,)])) == 'off'
    cur = Cur([RuntimeError('boom')])
    assert th.tree_mode(cur) == 'off' and any(s.startswith('ROLLBACK TO SAVEPOINT') for s in cur.sql)


def test_tree_mode_reads_the_switchboard(monkeypatch):
    from personality import features
    monkeypatch.setattr(features, 'mode', lambda cur, league, feature: 'shadow' if feature == th.FEATURE else 'off')
    assert th.tree_mode(Cur([(True,), (77,)])) == 'shadow'


def test_mentor_records_by_person_key():
    cur = Cur([[('Ann Head', 90, 60, 1), ('Bo X', None, None, None)]])
    assert th.mentor_records(cur) == {'p:ann head': {'wins': 90, 'losses': 60, 'super_bowl_wins': 1},
                                      'p:bo x': {'wins': 0, 'losses': 0, 'super_bowl_wins': 0}}
    assert th.mentor_records(Cur([RuntimeError('no view')])) == {}
