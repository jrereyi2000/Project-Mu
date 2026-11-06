"""engines/player_trade_requests.py (PERSONALITY_TRADE_REQUESTS_PLAN.md §2, U1): qualification durations and
exclusions, the neutral event baseline and the trait rule (no stacking), seed vectors, annual/window/team
deduplication and carryover, disabled/shadow/enabled modes, the lifecycle (repair, refusal, executed trade,
departure, correction, disclosure), JR/GM authority, privacy and stale public facts, consumer handoffs and the
shared-relationships adapter. Synthetic only; no database."""
from dataclasses import replace
import hashlib
import json
import math

import pytest

from engines import player_trade_requests as tr
from personality import core
from personality import reactions as rx
from personality import relationship_events as rev

U = 'u-test'
PID = 'player-7'
POL = tr.offline_illustration_policy()
APPROVED = replace(POL, version='jr-approved-test-v1')


def clock(season=2027, tick=10, *, open_=True, kind='IN_SEASON', checkpoint=None, universe=U):
    return tr.RequestClock(universe, season, checkpoint or f'cp:{season}:{tick}', tr.clock_ordinal(season, tick),
                           f'{kind}:{season}:wk', 'CLOSED' if not open_ and kind == 'CLOSED' else kind, open_,
                           f'mu:tick:{season}:{tick}')


def inc(iid='inc:lost_role:team:3:r1', kind='LOST_ROLE', obs=4, state='OPEN', severity='MATERIAL', team=3,
        sids=None, repairs=(), first=None, last=None, disclosure='PRIVATE'):
    first = first if first is not None else tr.clock_ordinal(2027, 1)
    last = last if last is not None else tr.clock_ordinal(2027, 5)
    return tr.Incident(iid, tuple(sids or (iid + ':src',)), kind, team, first, last, obs, severity, state,
                       tuple(repairs), disclosure)


def view(traits=(), origin='generated', status='stored'):
    if status == 'missing':
        return core.feature_view(None, 'trade_requests')
    return {'status': 'stored', 'origin': origin, 'sliders': {}, 'traits': list(traits), 'revision': 0,
            'persona_id': PID, 'view_version': core.FEATURE_VIEW_VERSION}


def ctx(incidents=(), *, traits=(), team=3, c=None, complete=None, pview=None, control=None, persona=PID):
    c = c or clock()
    return tr.RequestContext(1, persona, team, pview or view(traits), 'rel:1', 'rx:1', 'assess:1', c.ordinal,
                             c.ordinal if complete is None else complete, tuple(incidents),
                             control or tr.control_context(team, user_team_ids=[], suggestions_on=True))


def find_persona(want_request, prob=0.15, season=2027):
    """A persona whose fixed seeded draw falls under/over `prob` (deterministic search)."""
    for n in range(10000):
        pid = f'p-{n}'
        d = tr.seeded_draw(U, pid, season)
        if (d < prob) == want_request and (want_request or d >= 0.5):
            return pid
    raise AssertionError('no persona found')


def opened(traits=('DIVA',), incidents=None, season=2027):
    pid = find_persona(True, 0.15, season)
    c = clock(season)
    context = ctx(incidents or [inc()], traits=traits, c=c, persona=pid,
                  pview=dict(view(traits), persona_id=pid))
    d = tr.evaluate_request(context, c, None, None, POL, mode='SHADOW')
    assert d.outcome == 'REQUEST'
    return d.after, pid


def act(kind, payload, *, rev_=0, actor='JR', eid=None):
    return tr.RequestAction(eid or f'ev:{kind}', f'src:{kind}', rev_, kind, actor, payload)


# ---------------------------------------------------------------------------------------- determinism ---

def test_seed_vector_is_sha256_of_the_canonical_annual_key():
    raw = json.dumps(['trade-request-v1', U, PID, 2027], separators=(',', ':')).encode()
    expect = int.from_bytes(hashlib.sha256(raw).digest()[:8], 'big') / 2 ** 64
    assert tr.seeded_draw(U, PID, 2027) == expect
    assert 0 <= expect < 1
    assert tr.seeded_draw(U, PID, 2028) != expect          # one draw per season
    assert tr.request_id_for(U, PID, 2027) == tr.request_id_for(U, PID, 2027)
    assert tr.decision_id_for(U, PID, 2027) != tr.request_id_for(U, PID, 2027)


def test_clock_ordinal_is_monotonic_across_seasons():
    assert tr.clock_ordinal(2026, 32) < tr.clock_ordinal(2027, 0) < tr.clock_ordinal(2027, 1)
    with pytest.raises(tr.TradeRequestError):
        tr.clock_ordinal(2026, 1000)


def test_policy_validation_and_offline_policy_rejected_for_enabled():
    tr.validate_policy(POL)
    with pytest.raises(tr.TradeRequestError, match='policy_not_approved'):
        tr.validate_policy(POL, enabled=True)
    for bad in (replace(POL, lower_probability=0.2), replace(POL, higher_probability=math.nan),
                replace(POL, role_observations=0), replace(POL, version=''), replace(POL, reactivity_rule='x'),
                replace(POL, baseline_probability=True)):
        with pytest.raises(tr.TradeRequestError):
            tr.validate_policy(bad)


# --------------------------------------------------------------------------------------- qualification ---

@pytest.mark.parametrize('kind,needed', [('LOST_ROLE', 4), ('BROKEN_PROMISE', 2), ('TREATMENT', 2)])
def test_duration_thresholds(kind, needed):
    short = tr.qualify_request_causes(ctx([inc(kind=kind, obs=needed - 1)]), clock(), POL)
    ok = tr.qualify_request_causes(ctx([inc(kind=kind, obs=needed)]), clock(), POL)
    assert not short.eligible and any('insufficient_observations' in r for r in short.reasons)
    assert ok.eligible and ok.incident_ids == ('inc:lost_role:team:3:r1',)


@pytest.mark.parametrize('state,severity', [('DISPUTED', 'MATERIAL'), ('UNKNOWN', 'MATERIAL'),
                                            ('REPAIRED', 'MATERIAL'), ('OPEN', 'UNKNOWN')])
def test_disputed_unknown_and_repaired_never_qualify(state, severity):
    q = tr.qualify_request_causes(ctx([inc(obs=9, state=state, severity=severity)]), clock(), POL)
    assert not q.eligible and q.incident_ids == ()


def test_severe_has_no_immediate_exception():
    q = tr.qualify_request_causes(ctx([inc(kind='TREATMENT', obs=1, severity='SEVERE')]), clock(), POL)
    assert not q.eligible


def test_incomplete_assessment_and_closed_window_defer():
    c = clock()
    q = tr.qualify_request_causes(ctx([inc()], c=c, complete=c.ordinal - 1), c, POL)
    assert q.deferred_reasons == ('assessment_incomplete',) and not q.eligible
    closed = clock(kind='CLOSED', open_=False)
    q = tr.qualify_request_causes(ctx([inc()], c=closed), closed, POL)
    assert 'window_closed' in q.deferred_reasons


def test_other_team_incidents_do_not_count():
    q = tr.qualify_request_causes(ctx([inc(team=9)]), clock(), POL)
    assert not q.eligible and q.reasons[0].startswith('other_team')


def test_conflicting_source_ids_reject_the_batch():
    a = inc('inc:a', sids=('s1',))
    b = inc('inc:b', kind='TREATMENT', sids=('s1',))
    with pytest.raises(tr.TradeRequestError, match='incident_conflict'):
        tr.validate_request_context(ctx([a, b]), clock())


def test_errors_are_visible_not_substituted():
    with pytest.raises(tr.TradeRequestError, match='invalid_profile_view'):
        tr.validate_request_context(ctx(pview={'status': 'corrupt'}), clock())
    with pytest.raises(tr.TradeRequestError, match='out_of_order'):
        c = clock()
        tr.validate_request_context(replace(ctx(c=c), as_of_ordinal=c.ordinal + 1), c)
    with pytest.raises(tr.TradeRequestError, match='team_mismatch'):
        tr.validate_request_context(ctx(control=tr.control_context(4, user_team_ids=[], suggestions_on=True)),
                                    clock())


# -------------------------------------------------------------------------------------- reactivity rule ---

@pytest.mark.parametrize('traits,expected', [((), 0.10), (('DIVA',), 0.15), (('GRUDGE_HOLDER',), 0.15),
                                             (('DIVA', 'GRUDGE_HOLDER'), 0.15), (('PEACEMAKER',), 0.05),
                                             (('DIVA', 'PEACEMAKER'), 0.10), (('LOYAL',), 0.10)])
def test_trait_rule_never_stacks(traits, expected):
    assert tr.reactivity_probability(view(traits), POL)[0] == expected


def test_missing_and_unknown_profiles_are_neutral_baseline():
    assert tr.reactivity_probability(view(status='missing'), POL)[0] == 0.10
    unknown = core.feature_view(None, 'trade_requests')
    assert tr.reactivity_probability(unknown, POL)[0] == 0.10
    assert tr.reactivity_probability(view(('DIVA',)), replace(POL, reactivity_rule='none'))[0] == 0.10


def test_neutral_baseline_reacts_to_events_without_traits():
    pid = find_persona(True, 0.10)
    c = clock()
    d = tr.evaluate_request(ctx([inc()], c=c, persona=pid, pview=view(status='missing')), c, None, None, POL,
                            mode='SHADOW')
    assert d.outcome == 'REQUEST' and d.probability == 0.10


# ------------------------------------------------------------------------------------ annual decisions ---

def test_disabled_does_nothing_and_consumes_nothing():
    d = tr.evaluate_request(ctx([inc()]), clock(), None, None, POL, mode='DISABLED')
    assert d.outcome == 'NOOP' and not d.annual_slot_consumed and d.annual is None and d.draw is None


def test_shadow_request_and_no_request_consume_the_slot():
    yes, _ = opened()
    assert yes.status == 'ACTIVE' and yes.revision == 0 and yes.cause_codes == ('lost_role',)
    pid = find_persona(False)
    c = clock()
    d = tr.evaluate_request(ctx([inc()], c=c, persona=pid, pview=dict(view(), persona_id=pid)), c, None, None, POL,
                            mode='SHADOW')
    assert d.outcome == 'NO_REQUEST' and d.annual_slot_consumed and d.annual.outcome == 'NO_REQUEST'
    assert d.after is None and d.annual.request_id is None


def test_no_reroll_after_no_request_in_the_same_season():
    pid = find_persona(False)
    c = clock()
    first = tr.evaluate_request(ctx([inc()], c=c, persona=pid, pview=dict(view(), persona_id=pid)), c, None, None,
                                POL, mode='SHADOW')
    later = clock(tick=20)
    new_incident = inc('inc:treatment:team:3:t9', kind='TREATMENT', obs=5)
    for team in (3, 8):
        again = tr.evaluate_request(ctx([inc(team=team), replace(new_incident, target_team_id=team)], c=later,
                                        team=team, persona=pid, pview=dict(view(), persona_id=pid)),
                                    later, None, first.annual, POL, mode='SHADOW')
        assert again.outcome == 'NOOP' and not again.annual_slot_consumed and again.draw is None


def test_deferred_and_ineligible_do_not_consume():
    c = clock()
    d = tr.evaluate_request(ctx([inc()], c=c, complete=c.ordinal - 1), c, None, None, POL, mode='SHADOW')
    assert d.outcome == 'DEFERRED' and not d.annual_slot_consumed and d.annual is None
    d = tr.evaluate_request(ctx([inc(obs=1)]), clock(), None, None, POL, mode='SHADOW')
    assert d.outcome == 'INELIGIBLE' and not d.annual_slot_consumed


def test_active_episode_carries_across_seasons_without_a_draw():
    ep, pid = opened()
    c = clock(2028)
    d = tr.evaluate_request(ctx([inc()], c=c, persona=pid, pview=dict(view(), persona_id=pid)), c, ep, None, POL,
                            mode='SHADOW')
    assert d.outcome == 'EXISTING' and d.draw is None and d.after == ep


def test_policy_change_cannot_redraw_a_decided_season():
    pid = find_persona(False)
    c = clock()
    first = tr.evaluate_request(ctx([inc()], c=c, persona=pid, pview=dict(view(), persona_id=pid)), c, None, None,
                                POL, mode='SHADOW')
    generous = replace(POL, version='other-v2', higher_probability=1.0, baseline_probability=1.0,
                       lower_probability=1.0)
    d = tr.evaluate_request(ctx([inc()], c=c, persona=pid, pview=dict(view(), persona_id=pid)), c, None,
                            first.annual, generous, mode='SHADOW')
    assert d.outcome == 'NOOP'


def test_enabled_needs_an_approved_policy_and_annual_must_match():
    with pytest.raises(tr.TradeRequestError, match='policy_not_approved'):
        tr.evaluate_request(ctx([inc()]), clock(), None, None, POL, mode='ENABLED')
    pid = find_persona(False)
    c = clock()
    d = tr.evaluate_request(ctx([inc()], c=c, persona=pid, pview=dict(view(), persona_id=pid)), c, None, None,
                            APPROVED, mode='ENABLED')
    assert d.mode == 'ENABLED' and d.annual.policy_version == 'jr-approved-test-v1'
    with pytest.raises(tr.TradeRequestError, match='identity_mismatch'):
        tr.evaluate_request(ctx([inc()]), clock(2028), None, d.annual, POL, mode='SHADOW')


def test_input_hash_is_deterministic_and_review_key_names_the_checkpoint():
    a = tr.evaluate_request(ctx([inc()]), clock(), None, None, POL, mode='SHADOW')
    b = tr.evaluate_request(ctx([inc()]), clock(), None, None, POL, mode='SHADOW')
    assert a.input_hash == b.input_hash and a == b
    assert a.review_key['checkpoint_id'] == 'cp:2027:10' and a.review_key['action_kind'] == 'REVIEW'


def test_round_trip_through_json():
    d = tr.evaluate_request(ctx([inc()]), clock(), None, None, POL, mode='SHADOW')
    raw = json.loads(tr.canonical_json(d))
    assert tr.context_from_dict(json.loads(tr.canonical_json(ctx([inc()])))) == ctx([inc()])
    if d.after:
        assert tr.episode_from_dict(raw['after']) == d.after
    assert tr.annual_from_dict(raw['annual']) == d.annual
    with pytest.raises(tr.TradeRequestError):
        tr.episode_from_dict({'nope': 1})


# ------------------------------------------------------------------------------------------ lifecycle ---

def repaired_ctx(ep, obs=2, repairs=('rep1',), extra=(), c=None):
    c = c or clock(tick=20)
    incs = [inc(iid, obs=obs, state='REPAIRED', repairs=repairs) for iid in ep.initial_incident_ids]
    return ctx(incs + list(extra), c=c, persona=ep.persona_id, pview=dict(view(), persona_id=ep.persona_id)), c


def test_one_restored_game_is_not_repair_and_two_reconcile():
    ep, _ = opened()
    context, c = repaired_ctx(ep, obs=1)
    r = tr.transition_request(ep, act('REVIEW_REPAIR', {}, actor='SYSTEM'), context, c, POL)
    assert r.outcome == 'REPAIR_STARTED' and r.after.status == 'ACTIVE' and r.after.repair_started_ordinal == c.ordinal
    context, c2 = repaired_ctx(r.after, obs=2, c=clock(tick=22))
    r2 = tr.transition_request(r.after, act('REVIEW_REPAIR', {}, rev_=1, actor='SYSTEM', eid='ev:rr2'), context, c2,
                               POL)
    assert r2.after.status == 'WITHDRAWN' and r2.after.resolution_reason == 'RECONCILED' and r2.after.revision == 2


def test_new_promise_or_unknown_repair_does_not_reconcile():
    ep, _ = opened()
    open_ctx = ctx([inc()], c=clock(tick=20), persona=ep.persona_id, pview=dict(view(), persona_id=ep.persona_id))
    r = tr.transition_request(ep, act('REVIEW_REPAIR', {}, actor='SYSTEM'), open_ctx, clock(tick=20), POL)
    assert r.outcome == 'PAUSED' and not r.changed and r.after == ep
    blank = ctx([], c=clock(tick=20), persona=ep.persona_id, pview=dict(view(), persona_id=ep.persona_id))
    r = tr.transition_request(ep, act('REVIEW_REPAIR', {}, actor='SYSTEM'), blank, clock(tick=20), POL)
    assert r.outcome == 'PAUSED' and 'cause_unassessed' in r.reasons[0]
    context, c = repaired_ctx(ep, repairs=())
    assert tr.transition_request(ep, act('REVIEW_REPAIR', {}, actor='SYSTEM'), context, c, POL).outcome == 'PAUSED'
    other = inc('inc:treatment:team:3:x', kind='TREATMENT', obs=0)
    context, c = repaired_ctx(ep, extra=[other])
    r = tr.transition_request(ep, act('REVIEW_REPAIR', {}, actor='SYSTEM'), context, c, POL)
    assert r.outcome == 'PAUSED' and any(x.startswith('other_open_cause') for x in r.reasons)


def test_refusal_is_a_response_not_a_withdrawal():
    ep, pid = opened()
    c = clock(tick=12)
    jr = tr.control_context(3, user_team_ids=[3], suggestions_on=False)
    context = ctx([inc()], c=c, persona=pid, control=jr, pview=dict(view(), persona_id=pid))
    r = tr.transition_request(ep, act('DECLINE', {'response_ref': 'inbox:1'}), context, c, POL)
    assert r.after.status == 'ACTIVE' and r.after.team_response == 'DECLINED'
    with pytest.raises(tr.TradeRequestError, match='authority'):
        tr.transition_request(ep, act('DECLINE', {'response_ref': 'x'}, actor='GM'), context, c, POL)
    sim = ctx([inc()], c=c, persona=pid, pview=dict(view(), persona_id=pid))
    r = tr.transition_request(ep, act('EXPLORE', {'response_ref': 'gm:1'}, actor='GM'), sim, c, POL)
    assert r.after.team_response == 'EXPLORING'
    unknown = ctx([inc()], c=c, persona=pid, pview=dict(view(), persona_id=pid),
                  control=tr.control_context(3, user_team_ids=None, suggestions_on=True))
    with pytest.raises(tr.TradeRequestError, match='authority_unknown'):
        tr.transition_request(ep, act('ACKNOWLEDGE', {'response_ref': 'x'}, actor='GM'), unknown, c, POL)


def test_allusers_flag_is_not_authority():
    # Madden marks all 32 teams user teams; only the registry's user_teams counts.
    assert tr.control_context(3, user_team_ids=[29], suggestions_on=True).authority == 'SIMULATED'
    assert tr.control_context(29, user_team_ids=[29], suggestions_on=False).authority == 'JR_RECORDED'


@pytest.mark.parametrize('payload', [{'executed': False, 'transaction_id': 't', 'from_team_id': 3, 'to_team_id': 8},
                                     {'status': 'approved', 'transaction_id': 't', 'from_team_id': 3},
                                     {'executed': True, 'transaction_id': 't', 'from_team_id': 5, 'to_team_id': 8}])
def test_approved_rejected_or_expired_deal_is_not_a_trade(payload):
    ep, pid = opened()
    c = clock(tick=14)
    with pytest.raises(tr.TradeRequestError):
        tr.transition_request(ep, act('RECORD_TRADE', payload, actor='SYSTEM'),
                              ctx([], c=c, persona=pid, team=8, pview=dict(view(), persona_id=pid),
                                  control=tr.control_context(8, user_team_ids=[], suggestions_on=True)), c, POL)


def test_executed_trade_resolves_and_terminal_never_reopens():
    ep, pid = opened()
    c = clock(tick=14)
    after_ctx = ctx([], c=c, persona=pid, team=8, pview=dict(view(), persona_id=pid),
                    control=tr.control_context(8, user_team_ids=[], suggestions_on=True))
    r = tr.transition_request(ep, act('RECORD_TRADE', {'executed': True, 'transaction_id': 'tx:1', 'from_team_id': 3,
                                                        'to_team_id': 8}, actor='SYSTEM'), after_ctx, c, POL)
    assert r.after.status == 'RESOLVED_TRADE' and r.after.resolved_ordinal == c.ordinal
    with pytest.raises(tr.TradeRequestConflict, match='terminal'):
        tr.transition_request(r.after, act('DISCLOSE', {'basis': 'public_statement', 'disclosure_ref': 'x'}, rev_=1),
                              ctx([], c=c, persona=pid, team=3, pview=dict(view(), persona_id=pid)), c, POL)


def test_employment_closure_and_unknown_departure_defers():
    ep, pid = opened()
    c = clock(tick=15)
    context = ctx([], c=c, persona=pid, pview=dict(view(), persona_id=pid))
    r = tr.transition_request(ep, act('CLOSE_EMPLOYMENT', {'reason': 'UNKNOWN'}, actor='SYSTEM'), context, c, POL)
    assert r.outcome == 'DEFERRED' and not r.changed
    r = tr.transition_request(ep, act('CLOSE_EMPLOYMENT', {'reason': 'RELEASED', 'evidence_ref': 'tx:cut',
                                                            'from_team_id': 3}, actor='SYSTEM'), context, c, POL)
    assert r.after.status == 'CLOSED_EMPLOYMENT' and r.after.resolution_reason == 'RELEASED'


def test_withdrawal_needs_player_event_not_inbox_dismissal():
    ep, pid = opened()
    c = clock(tick=15)
    context = ctx([], c=c, persona=pid, pview=dict(view(), persona_id=pid))
    with pytest.raises(tr.TradeRequestError, match='missing_evidence'):
        tr.transition_request(ep, act('RECORD_WITHDRAWAL', {'basis': 'inbox_dismissal', 'evidence_ref': 'x'}),
                              context, c, POL)
    r = tr.transition_request(ep, act('RECORD_WITHDRAWAL', {'basis': 'player_event', 'evidence_ref': 'stmt:1'},
                                      actor='SYSTEM'), context, c, POL)
    assert r.after.status == 'WITHDRAWN' and r.after.visibility == 'PRIVATE'


def test_correction_voids_or_links_and_never_rerolls():
    ep, pid = opened()
    c = clock(tick=16)
    context = ctx([], c=c, persona=pid, pview=dict(view(), persona_id=pid))
    linked = tr.transition_request(ep, act('CORRECT', {'superseded_refs': ['s'], 'explanation': 'typo',
                                                       'initial_evidence_invalid': False}), context, c, POL)
    assert linked.after.status == 'ACTIVE' and linked.after.revision == 1
    voided = tr.transition_request(linked.after, act('CORRECT', {'superseded_refs': ['s'], 'explanation': 'bad data',
                                                                 'initial_evidence_invalid': True}, rev_=1,
                                                     eid='ev:c2'), context, c, POL)
    assert voided.after.status == 'VOIDED_CORRECTION'
    assert voided.after.initial_incident_ids == ep.initial_incident_ids     # history preserved


def test_stale_revision_and_out_of_order_conflict():
    ep, pid = opened()
    c = clock(tick=16)
    context = ctx([], c=c, persona=pid, pview=dict(view(), persona_id=pid))
    with pytest.raises(tr.TradeRequestConflict, match='stale_revision'):
        tr.transition_request(ep, act('DISCLOSE', {'basis': 'public_statement', 'disclosure_ref': 'x'}, rev_=3),
                              context, c, POL)
    early = clock(tick=2)
    with pytest.raises(tr.TradeRequestConflict, match='out_of_order'):
        tr.transition_request(ep, act('DISCLOSE', {'basis': 'public_statement', 'disclosure_ref': 'x'}),
                              ctx([], c=early, persona=pid, pview=dict(view(), persona_id=pid)), early, POL)


# --------------------------------------------------------------------------------- privacy and handoff ---

def test_private_until_disclosed_and_stale_public_facts():
    ep, pid = opened()
    assert tr.request_projection(ep, audience='PUBLIC') is None
    fo = tr.request_projection(ep, audience='FRONT_OFFICE', clock=clock())
    assert fo['actionable'] is True and fo['reason_codes'] == ['lost_role']
    assert not {'draw', 'probability', 'traits', 'initial_source_ids'} & set(fo)
    closed = clock(kind='CLOSED', open_=False)
    assert tr.request_projection(ep, audience='FRONT_OFFICE', clock=closed)['actionable'] is False
    c = clock(tick=16)
    context = ctx([], c=c, persona=pid, pview=dict(view(), persona_id=pid))
    with pytest.raises(tr.TradeRequestError, match='missing_evidence'):
        tr.transition_request(ep, act('DISCLOSE', {'basis': 'leak', 'disclosure_ref': 'x'}), context, c, POL)
    pub = tr.transition_request(ep, act('DISCLOSE', {'basis': 'public_statement', 'disclosure_ref': 'pc:1'}),
                                context, c, POL).after
    view_ = tr.request_projection(pub, audience='PUBLIC')
    assert view_ == {'request_id': ep.request_id, 'persona_id': pid, 'team_id': 3, 'status': 'ACTIVE',
                     'as_of_ordinal': c.ordinal, 'current': True}
    gone = tr.transition_request(pub, act('RECORD_WITHDRAWAL', {'basis': 'player_event', 'evidence_ref': 'p'},
                                          rev_=1, actor='SYSTEM', eid='ev:w'), context, clock(tick=18), POL).after
    stale = tr.request_projection(gone, audience='PUBLIC')
    assert stale['status'] == 'ACTIVE' and stale['current'] is False      # dated, not current
    assert tr.request_projection(gone, audience='AUDIT')['status'] == 'WITHDRAWN'


def test_contract_context_has_no_refusal_or_utility():
    ep, _ = opened()
    cc = tr.contract_request_context(ep)
    assert set(cc) == {'schema_version', 'request_id', 'status', 'team_id', 'unresolved_incident_ids',
                       'source_event_ids', 'as_of_ordinal', 'policy_version'}
    assert cc['unresolved_incident_ids'] == list(ep.initial_incident_ids)
    assert tr.contract_request_context(None) == {'schema_version': 1, 'status': 'ABSENT'}


def test_gm_signal_is_only_a_signal():
    ep, pid = opened()
    sig = tr.gm_request_signal(ep, clock=clock())
    assert sig == {'request_id': ep.request_id, 'persona_id': pid, 'team_id': 3, 'status': 'ACTIVE',
                   'cause_codes': ['lost_role'], 'actionable': True}
    assert tr.gm_request_signal(replace(ep, status='WITHDRAWN')) is None


def test_suggestions_off_keeps_truthful_request_state():
    pid = find_persona(True, 0.10)
    c = clock()
    jr = tr.control_context(3, user_team_ids=[3], suggestions_on=False)
    d = tr.evaluate_request(ctx([inc()], c=c, persona=pid, control=jr, pview=dict(view(), persona_id=pid)), c, None,
                            None, POL, mode='SHADOW')
    assert d.outcome == 'REQUEST'
    assert tr.request_projection(d.after, audience='FRONT_OFFICE', clock=c)['status'] == 'ACTIVE'


# ------------------------------------------------------------------------------------ shared adapter ---

REG = {'team:3': {'team_id': 3}}
CAL = {s: 32 for s in range(2026, 2036)}
TEAM = {'kind': 'team', 'universe_id': U, 'team_key': 'team:3'}
ME = {'kind': 'person', 'universe_id': U, 'role': 'player', 'persona_id': PID}


def tp(s, t):
    return {'season': s, 'tick': t}


def env(kind, payload, *, sid, at):
    e = {'schema_version': rev.RELATIONSHIP_SCHEMA_VERSION, 'universe_id': U, 'event_id': sid + '|t',
         'source_event_id': sid, 'source_kind': 'test', 'source_refs': [], 'kind': kind, 'occurred_at': tp(*at),
         'subject': ME, 'target': TEAM, 'actor': None, 'basis': 'verified_simulation', 'confidence': 'high',
         'reviewer': None, 'evidence_scope': [], 'visibility': 'private',
         'attribution': {'responsible': [TEAM], 'reason_code': None, 'evidence_refs': []},
         'payload': payload, 'supersedes_source_event_id': None}
    return rev.validate_event(e, team_registry=REG)


def role(tick, assessment):
    return env('role_window', {'team_key': 'team:3', 'start': tp(2027, tick), 'end': tp(2027, tick + 1),
                               'agreed_role': 'STARTER', 'observed_role': 'RESERVE', 'assessment': assessment,
                               'assessment_basis_refs': []}, sid=f'mu:role:{tick}', at=(2027, tick))


CUT = env('treatment', {'team_key': 'team:3', 'treatment_kind': 'disrespect', 'outcome': 'negative',
                        'significance': 'meaningful', 'outcome_refs': []}, sid='mu:disrespect:1', at=(2027, 3))
REPAIR = env('repair', {'breach_source_ids': ['mu:disrespect:1'], 'action': 'reconciliation', 'outcome_refs': [],
                        'accepted_by_subject': True}, sid='mu:repair:1', at=(2027, 8))


def state(breaches=()):
    return {'subject': ME, 'edges': [{'target': TEAM, 'unresolved_breaches': list(breaches)}]}


OPEN = {'source_id': 'mu:disrespect:1', 'promise_id': None, 'severity': 'material', 'repair_state': 'open'}
VIEW = core.feature_view(None, 'reactions')


def reactions(events, st, mode='observe', as_of=(2027, 31)):
    return rx.project_reactions(events, st, feature_view=VIEW, as_of=tp(*as_of), known_through=None,
                                policy={'mode': mode, 'rule_version': 't-1', 'reactions': None}, calendar=CAL)


def test_adapter_maps_role_frustration_and_breaches():
    events = [CUT] + [role(t, 'unsatisfied') for t in range(4, 8)]
    st = state([OPEN])
    incs = tr.incidents_from_shared(events, st, reactions(events, st), team_id=3, team_key='team:3')
    by_kind = {i.kind: i for i in incs}
    assert by_kind['LOST_ROLE'].eligible_observations == 4
    assert by_kind['LOST_ROLE'].incident_id == 'inc:lost_role:team:3:mu:role:4'
    assert by_kind['TREATMENT'].eligible_observations == 4 and by_kind['TREATMENT'].source_event_ids == ('mu:disrespect:1',)
    c = clock(tick=8)
    context = ctx(incs, c=c)
    q = tr.qualify_request_causes(context, c, POL)
    assert q.eligible and len(q.incident_ids) == 2


def test_adapter_off_mode_and_unknown_windows():
    events = [role(4, 'unsatisfied'), role(5, 'unknown'), role(6, 'unsatisfied')]
    st = state()
    assert tr.incidents_from_shared(events, st, reactions(events, st, mode='off'), team_id=3, team_key='team:3') == []
    assert tr.incidents_from_shared(events, st, reactions(events, st), team_id=3, team_key='team:3') == []


def test_adapter_repair_paths():
    events = [CUT, REPAIR, role(9, 'satisfied'), role(10, 'satisfied')]
    prior = ('inc:treatment:team:3:mu:disrespect:1', 'inc:lost_role:team:3:mu:role:4')
    events_role = events + [role(t, 'unsatisfied') for t in range(4, 8)] + [role(8, 'satisfied')]
    incs = tr.incidents_from_shared(events_role, state(), reactions(events_role, state()), team_id=3,
                                    team_key='team:3', prior_incident_ids=prior)
    by = {i.incident_id: i for i in incs}
    t = by['inc:treatment:team:3:mu:disrespect:1']
    assert t.state == 'REPAIRED' and t.repair_event_ids == ('mu:repair:1',) and t.eligible_observations == 3
    r = by['inc:lost_role:team:3:mu:role:4']
    assert r.state == 'REPAIRED' and r.eligible_observations == 3
    # closed without a visible accepted repair: unassessed, not invented
    assert tr.incidents_from_shared([CUT], state(), reactions([CUT], state()), team_id=3, team_key='team:3',
                                    prior_incident_ids=prior[:1]) == []


def test_same_incident_through_aliases_counts_once():
    events = [CUT] + [role(t, 'unsatisfied') for t in range(4, 8)]
    st = state([OPEN])
    a = tr.incidents_from_shared(events, st, reactions(events, st), team_id=3, team_key='team:3')
    b = tr.incidents_from_shared(list(reversed(events)), st, reactions(events, st), team_id=3, team_key='team:3')
    assert a == b and len({i.incident_id for i in a}) == len(a)
