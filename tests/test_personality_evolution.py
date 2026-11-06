"""Personality evolution rules, draw, arbiter and change notes (PERSONALITY_EVOLUTION_PLAN.md §2.2-§2.3, §4).

Synthetic identities and facts only; no database.
"""
from copy import deepcopy
import inspect

import pytest

from personality import core, evolution as evo, evolution_rules as er
from personality.core import Identity, initialize

UNIVERSE = 'scratch-universe'
SEASON = 2029


def profile(role='player', key='p1', traits=('RING_CHASER',), sliders=None, origin='real', **kw):
    base = dict(temperament=5, sociability=5, confidence=5, openness=5, warmth=5, drive=5)
    base.update(sliders or {})
    p = initialize(Identity(UNIVERSE, role, key), origin, 2026, sliders=base, traits=list(traits))
    p.update(kw)
    return p


def facts(p, **kw):
    f = dict(universe_id=UNIVERSE, season=SEASON, persona_id=p['persona_id'], role=p['role'],
             profile_view={'traits': list(p['traits']), 'sliders': dict(p['sliders'])},
             age=None, seasons_in_league=None, tenure_seasons_on_team=None, team_id=1, position_group=None,
             won_super_bowl=None, made_playoffs=None, losing_seasons_in_row=None, awards=[],
             unsatisfied_role_weeks=None, released_by_team=None, signed_elsewhere_after_release=None,
             injury_weeks_out=None, mentor_retired=None, coach_fired=None, coach_promoted_to_hc=None,
             coach_kept_on_hot_seat=None, source_event_ids={}, missing={})
    f.update(kw)
    return f


def fired(rule_id, f):
    return er.RULES_BY_ID[rule_id].when(f)


# ------------------------------------------------------------------------------------------------- rules ---

def test_rule_table_matches_the_plan():
    table = {r.rule_id: (sorted(r.roles), r.chance, r.priority) for r in er.RULES}
    assert table == {
        'TITLE_LEGACY': (['coach', 'player'], 0.35, 1), 'FIRED_TEMPER': (['coach'], 0.50, 1),
        'MENTOR_RETIRES': (['player'], 0.25, 2), 'VETERAN_LEADER': (['player'], 0.10, 4),
        'BENCHED_TEMPER': (['player'], 0.30, 2), 'CUT_GRUDGE': (['player'], 0.15, 3),
        'MVP_CONFIDENCE': (['player'], 0.40, 2), 'LONG_INJURY_DRIVE': (['player'], 0.20, 3),
        'PROMOTED_CONFIDENCE': (['coach'], 0.40, 2), 'LOSING_OPENNESS': (['coach'], 0.25, 3),
        'COY_CONFIDENCE': (['coach'], 0.40, 2)}
    assert er.EVOLUTION_RULES_VERSION == 'evolution-v1'


@pytest.mark.parametrize('rule_id,yes,no,change', [
    ('TITLE_LEGACY', dict(won_super_bowl=True, seasons_in_league=4),
     dict(won_super_bowl=True, seasons_in_league=3),
     {'kind': 'trait', 'remove': 'RING_CHASER', 'add': 'LEGACY_BUILDER'}),
    ('MENTOR_RETIRES', dict(mentor_retired=True, age=27), dict(mentor_retired=True, age=26),
     {'kind': 'trait', 'add': 'MENTOR'}),
    ('VETERAN_LEADER', dict(age=31, tenure_seasons_on_team=5), dict(age=31, tenure_seasons_on_team=4),
     {'kind': 'trait', 'add': 'MENTOR'}),
    ('BENCHED_TEMPER', dict(unsatisfied_role_weeks=6), dict(unsatisfied_role_weeks=5),
     {'kind': 'slider', 'slider': 'temperament', 'step': 1}),
    ('CUT_GRUDGE', dict(released_by_team=True, signed_elsewhere_after_release=True),
     dict(released_by_team=True, signed_elsewhere_after_release=False),
     {'kind': 'trait', 'add': 'GRUDGE_HOLDER'}),
    ('MVP_CONFIDENCE', dict(awards=['First Team All-Pro']), dict(awards=['Second Team All-Pro', 'OROY']),
     {'kind': 'slider', 'slider': 'confidence', 'step': 1}),
    ('LONG_INJURY_DRIVE', dict(injury_weeks_out=8), dict(injury_weeks_out=7),
     {'kind': 'slider', 'slider': 'drive', 'step': 1}),
])
def test_player_rules_when_and_change(rule_id, yes, no, change):
    p = profile()
    assert fired(rule_id, facts(p, **yes))
    assert not fired(rule_id, facts(p, **no))
    assert er.RULES_BY_ID[rule_id].change(facts(p, **yes)) == change


@pytest.mark.parametrize('rule_id,yes,no,change', [
    ('FIRED_TEMPER', dict(coach_fired=True), dict(coach_fired=False),
     {'kind': 'slider', 'slider': 'temperament', 'step': 1}),
    ('PROMOTED_CONFIDENCE', dict(coach_promoted_to_hc=True), dict(coach_promoted_to_hc=False),
     {'kind': 'slider', 'slider': 'confidence', 'step': 1}),
    ('LOSING_OPENNESS', dict(losing_seasons_in_row=2, coach_fired=False),
     dict(losing_seasons_in_row=2, coach_fired=True), {'kind': 'slider', 'slider': 'openness', 'step': 1}),
    ('COY_CONFIDENCE', dict(awards=['COTY']), dict(awards=['MVP']),
     {'kind': 'slider', 'slider': 'confidence', 'step': 1}),
    ('TITLE_LEGACY', dict(won_super_bowl=True, seasons_in_league=10), dict(won_super_bowl=False, seasons_in_league=10),
     {'kind': 'trait', 'remove': 'RING_CHASER', 'add': 'LEGACY_BUILDER'}),
])
def test_coach_rules_when_and_change(rule_id, yes, no, change):
    p = profile(role='coach', key='c1')
    assert fired(rule_id, facts(p, **yes))
    assert not fired(rule_id, facts(p, **no))
    assert er.RULES_BY_ID[rule_id].change(facts(p, **yes)) == change


def test_trait_gates_and_missing_facts_never_fire():
    mentor = profile(traits=('MENTOR',))
    loner = profile(traits=('LONER',))
    peace = profile(traits=('PEACEMAKER',))
    no_ring = profile(traits=())
    assert not fired('MENTOR_RETIRES', facts(mentor, mentor_retired=True, age=30))
    assert not fired('VETERAN_LEADER', facts(loner, age=33, tenure_seasons_on_team=8))
    assert not fired('CUT_GRUDGE', facts(peace, released_by_team=True, signed_elsewhere_after_release=True))
    assert not fired('TITLE_LEGACY', facts(no_ring, won_super_bowl=True, seasons_in_league=9))
    # R3: a fact whose source is missing is None and fires nothing
    blank = facts(profile())
    assert not any(r.when(blank) for r in er.RULES)
    assert not fired('LOSING_OPENNESS', facts(profile(role='coach', key='c'), losing_seasons_in_row=3))


def test_rule_candidates_only_for_the_persons_role():
    p = profile()
    f = facts(p, coach_fired=True, won_super_bowl=True, seasons_in_league=5)
    ids = [c['rule_id'] for c in evo.rule_candidates(f)]
    assert ids == ['TITLE_LEGACY']


def test_no_rule_reads_money_or_contracts():
    banned = ('contract', 'salary', 'money', 'payday', 'cap_', 'bonus', 'extension', 'MONEY_FIRST')
    for rule in er.RULES:
        for fact in rule.facts:
            assert not any(b.lower() in fact.lower() for b in banned), (rule.rule_id, fact)
        assert not any(b.lower() in rule.note.lower() for b in banned)
    source = inspect.getsource(er).split('RULES = (', 1)[1].split('RULES_BY_ID', 1)[0]
    assert not any(b.lower() in source.lower() for b in banned)


def test_notes_fill_from_facts():
    p = profile()
    f = facts(p, awards=['MVP'], injury_weeks_out=9)
    assert er.fill_note(er.RULES_BY_ID['MVP_CONFIDENCE'], f) == \
        'Since his MVP season in 2029 he carries himself with more certainty.'
    assert '9 weeks in 2029' in er.fill_note(er.RULES_BY_ID['LONG_INJURY_DRIVE'], f)


# -------------------------------------------------------------------------------------------------- draw ---

def test_draw_is_deterministic_and_varies_with_season_and_rule():
    a = evo.draw(UNIVERSE, 'pid', SEASON, 'TITLE_LEGACY')
    assert a == evo.draw(UNIVERSE, 'pid', SEASON, 'TITLE_LEGACY')
    assert 0 <= a < 1
    assert a != evo.draw(UNIVERSE, 'pid', SEASON + 1, 'TITLE_LEGACY')
    assert a != evo.draw(UNIVERSE, 'pid', SEASON, 'MVP_CONFIDENCE')
    assert a != evo.draw('other', 'pid', SEASON, 'TITLE_LEGACY')
    draws = [evo.draw(UNIVERSE, str(i), SEASON, 'X') for i in range(2000)]
    assert 0.45 < sum(draws) / len(draws) < 0.55


def test_candidates_status_follows_the_draw():
    hits = misses = 0
    for i in range(200):
        p = profile(key=f'p{i}')
        (c,) = evo.rule_candidates(facts(p, injury_weeks_out=10,
                                         source_event_ids={'injury_weeks_out': [f'mu:injury:{i}']}))
        assert c['draw'] == evo.draw(UNIVERSE, p['persona_id'], SEASON, 'LONG_INJURY_DRIVE')
        assert c['status'] == ('eligible' if c['draw'] < 0.20 else 'drawn_out')
        assert c['source_event_ids'] == [f'mu:injury:{i}']
        assert c['candidate_id'] == f"{UNIVERSE}:{p['persona_id']}:{SEASON}:LONG_INJURY_DRIVE"
        hits += c['status'] == 'eligible'
        misses += c['status'] == 'drawn_out'
    assert 20 < hits < 65 and hits + misses == 200


# ------------------------------------------------------------------------------------------- arbitration ---

def cand(p, rule_id, *, priority, d, status='eligible', change=None, sids=('src-1',)):
    return dict(candidate_id=f"{UNIVERSE}:{p['persona_id']}:{SEASON}:{rule_id}", universe_id=UNIVERSE,
                persona_type=p['role'], persona_id=p['persona_id'], season=SEASON, source='RULE', rule_id=rule_id,
                rules_version='evolution-v1', change=change or {'kind': 'slider', 'slider': 'drive', 'step': 1},
                chance=0.5, draw=d, source_event_ids=list(sids), note=f'{rule_id} note', priority=priority,
                status=status, reason=None, core_event_id=None)


def external(p, status='ready', change=None, eid='rel-evo-1', source='RELATIONSHIPS', **kw):
    c = dict(event_id=eid, source_event_id='rel:src', season=SEASON, expected_revision=p['revision'],
             rule_version='relationships-v1', reason='Since the 2029 trade he trusts fewer people.',
             review_status='approved', reviewer='JR', feature_scope=['voice'],
             supporting_source_ids=['rel:src', 'rel:other'])
    c.update(change or {'kind': 'slider', 'slider': 'warmth', 'step': -1})
    out = dict(source=source, persona_type=p['role'], persona_id=p['persona_id'], universe_id=UNIVERSE,
               status=status, candidate=c)
    out.update(kw)
    return out


def test_arbitration_priority_then_draw_then_rule():
    p = profile()
    cs = [cand(p, 'B', priority=2, d=0.1), cand(p, 'A', priority=1, d=0.3), cand(p, 'C', priority=1, d=0.2)]
    out = evo.arbitrate(cs, season=SEASON)[p['persona_id']]
    assert out['chosen']['rule_id'] == 'C' and out['chosen']['status'] == 'chosen'
    assert {c['rule_id']: c['status'] for c in out['others']} == {'A': 'superseded', 'B': 'superseded'}
    tie = [cand(p, 'Z', priority=1, d=0.2), cand(p, 'Y', priority=1, d=0.2)]
    assert evo.arbitrate(tie, season=SEASON)[p['persona_id']]['chosen']['rule_id'] == 'Y'
    assert cs[0]['status'] == 'eligible'        # inputs untouched


def test_drawn_out_candidates_never_win():
    p = profile()
    out = evo.arbitrate([cand(p, 'A', priority=1, d=0.9, status='drawn_out'), cand(p, 'B', priority=4, d=0.05)],
                        season=SEASON)[p['persona_id']]
    assert out['chosen']['rule_id'] == 'B'
    assert out['others'][0]['status'] == 'drawn_out'
    none = evo.arbitrate([cand(p, 'A', priority=1, d=0.9, status='drawn_out')], season=SEASON)
    assert none[p['persona_id']]['chosen'] is None


def test_external_ready_candidates_join_and_others_are_ignored():
    p = profile()
    ext = [external(p), external(p, status='review_required', eid='x2'), external(p, status='blocked', eid='x3')]
    out = evo.arbitrate([cand(p, 'A', priority=1, d=0.01)], ext, season=SEASON,
                        profiles={p['persona_id']: p})[p['persona_id']]
    assert out['chosen']['source'] == 'RELATIONSHIPS'
    assert out['chosen']['rule_id'] == 'external:rel-evo-1'
    assert out['chosen']['change'] == {'kind': 'slider', 'slider': 'warmth', 'step': -1}
    assert out['chosen']['source_event_ids'] == ['rel:src', 'rel:other']
    assert [c['rule_id'] for c in out['others']] == ['A']
    # a lower explicit priority number still wins over a reviewed external one
    out = evo.arbitrate([cand(p, 'A', priority=-1, d=0.5)], [external(p)], season=SEASON)[p['persona_id']]
    assert out['chosen']['rule_id'] == 'A'
    # an external candidate alone, for a person with no rule candidates
    q = profile(key='q')
    alone = evo.arbitrate([], [external(q, source='DEVELOPMENT')], season=SEASON)
    assert alone[q['persona_id']]['chosen']['source'] == 'DEVELOPMENT'


def test_blocked_candidates_for_invalid_changes():
    full = profile(traits=('RING_CHASER', 'MENTOR', 'GRUDGE_HOLDER', 'FILM_JUNKIE', 'FOODIE'))
    top = profile(key='top', traits=(), sliders=dict(drive=10, temperament=0))
    band = profile(key='band', traits=(), sliders=dict(temperament=8, sociability=8, confidence=8, openness=6,
                                                       warmth=3, drive=3))
    conflict = profile(key='conf', traits=('PEACEMAKER',))
    cases = [
        (full, {'kind': 'trait', 'add': 'LEGACY_BUILDER'}, 'invalid_change'),
        (top, {'kind': 'slider', 'slider': 'drive', 'step': 1}, 'slider_out_of_range'),
        (band, {'kind': 'slider', 'slider': 'drive', 'step': 1}, 'slider_total_outside_band'),
        (conflict, {'kind': 'trait', 'add': 'GRUDGE_HOLDER'}, 'invalid_change'),
    ]
    for p, change, why in cases:
        assert sum(p['sliders'].values()) in range(24, 37)
        out = evo.arbitrate([cand(p, 'R', priority=1, d=0.1, change=change)], season=SEASON,
                            profiles={p['persona_id']: p})[p['persona_id']]
        assert out['chosen'] is None
        (c,) = out['others']
        assert c['status'] == 'blocked' and c['reason'].startswith(why), (change, c['reason'])
    # the next valid candidate wins when the first is blocked
    out = evo.arbitrate([cand(top, 'R', priority=1, d=0.1, change={'kind': 'slider', 'slider': 'drive', 'step': 1}),
                         cand(top, 'S', priority=3, d=0.1, change={'kind': 'slider', 'slider': 'warmth', 'step': 1})],
                        season=SEASON, profiles={top['persona_id']: top})[top['persona_id']]
    assert out['chosen']['rule_id'] == 'S'
    # no stored profile: blocked, never forced
    q = profile(key='ghost')
    out = evo.arbitrate([cand(q, 'R', priority=1, d=0.1)], season=SEASON, profiles={})[q['persona_id']]
    assert out['chosen'] is None and out['others'][0]['reason'] == 'no_profile'


def test_one_change_per_person_and_skip_when_already_changed():
    a, b = profile(key='a'), profile(key='b')
    cs = [cand(a, 'A1', priority=1, d=0.1), cand(a, 'A2', priority=2, d=0.1), cand(b, 'B1', priority=3, d=0.1)]
    out = evo.arbitrate(cs, season=SEASON, profiles={a['persona_id']: a, b['persona_id']: b})
    assert [out[k]['chosen']['rule_id'] for k in (a['persona_id'], b['persona_id'])] == ['A1', 'B1']
    changed = deepcopy(a)
    changed, _ = core.apply_change(changed, evo.change_event(cand(a, 'A1', priority=1, d=0.1), changed, SEASON))
    out = evo.arbitrate(cs[:2], season=SEASON, profiles={a['persona_id']: changed})[a['persona_id']]
    assert out['chosen'] is None
    assert {c['reason'] for c in out['others']} == {'annual_slot_used'}


def test_change_event_shape_is_accepted_by_core():
    p = profile()
    f = facts(p, won_super_bowl=True, seasons_in_league=6,
              source_event_ids={'won_super_bowl': ['mu:result:2029:4:won-super-bowl:23'], 'seasons_in_league': []})
    (c,) = evo.rule_candidates(f)
    event = evo.change_event(c, p, SEASON)
    assert event == dict(event_id=f"evo:{UNIVERSE}:{p['persona_id']}:{SEASON}",
                         source_event_id='mu:result:2029:4:won-super-bowl:23', season=SEASON, tick=24,
                         expected_revision=0, rule_version='evolution-v1',
                         reason='Since the 2029 title he talks more about legacy than rings.',
                         kind='trait', remove='RING_CHASER', add='LEGACY_BUILDER')
    after, audit = core.apply_change(p, event)
    assert after['traits'] == ['LEGACY_BUILDER'] and after['last_changed_season'] == SEASON
    slider = evo.change_event(cand(p, 'X', priority=1, d=0.1), p, SEASON)
    assert core.apply_change(p, slider)[0]['sliders']['drive'] == 6
    with pytest.raises(core.PersonalityError):
        evo.change_event(cand(p, 'X', priority=1, d=0.1, sids=()), p, SEASON)


# ------------------------------------------------------------------------------------------ change notes ---

def receipt(season, kind, reason, tick=24, seq=1):
    return dict(persona_type='player', persona_id='p', kind=kind, event_id=f'e{season}{seq}', season=season,
                effective_tick=tick, seq=seq, request={'reason': reason}, before_state={}, after_state={})


def test_change_notes_order_limit_and_season_prefix():
    history = [receipt(2027, 'evolution', 'Since the 2027 title he talks more about legacy than rings.', seq=1),
               receipt(2028, 'evolution', 'Since his own mentor retired he has taken the younger players under '
                                          'his wing.', seq=2),
               receipt(2029, 'override', 'fixing a typo', tick=3, seq=3),
               receipt(2029, 'override', 'note: He has softened since the 2029 trade.', tick=5, seq=4),
               receipt(2030, 'evolution', 'He has not forgotten the team that cut him in 2030.', seq=5),
               dict(receipt(2026, 'adoption', 'adopted'), seq=0)]
    assert evo.change_notes(history, as_of=2030) == [
        'He has not forgotten the team that cut him in 2030.', 'He has softened since the 2029 trade.']
    assert evo.change_notes(history, as_of=2029, limit=5) == [
        'He has softened since the 2029 trade.',
        '2028: Since his own mentor retired he has taken the younger players under his wing.',
        'Since the 2027 title he talks more about legacy than rings.']
    assert evo.change_notes(history, as_of=(2029, 4)) == [
        '2028: Since his own mentor retired he has taken the younger players under his wing.',
        'Since the 2027 title he talks more about legacy than rings.']
    assert evo.change_notes([], as_of=2030) == []
    assert evo.MAX_CHANGE_NOTES == 2
