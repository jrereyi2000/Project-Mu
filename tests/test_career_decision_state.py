"""engines/career_decision_state.py (PERSONALITY_RETIREMENT_PLAN.md §2.4, U2). Pure dictionaries."""
import itertools

import pytest

from engines import career_decision_state as cs

_ids = itertools.count()


def st(state='OPEN', revision=0, reservation=None):
    s = cs.new_state('u-test', 'person:1', 2027, 'd-1')
    s.update(state=state, revision=revision, reservation=reservation)
    return s


def ev(kind, rev=0, actor=None, **payload):
    n = next(_ids)
    return {'event_id': f'e-{n}', 'source_event_id': f'src-{n}', 'kind': kind, 'expected_revision': rev,
            'payload': payload, 'actor': actor}


def run(state, *events):
    receipts = []
    for e in events:
        e = dict(e, expected_revision=state['revision'])
        r = cs.transition(state, e)
        receipts.append(r)
        state = r['after']
    return state, receipts


OFFER = dict(offer_id='offer-9', offer_kind='free_agency', acceptable=True, legal=True)


@pytest.mark.parametrize('intent', ['AVAILABLE', 'RETIRE_INTENT', 'BLOCKED_DATA'])
def test_annual_review_from_open(intent):
    r = cs.transition(st(), ev('ANNUAL_REVIEW', intent=intent))
    assert r['outcome'] == 'APPLIED' and r['after']['state'] == intent and r['after']['revision'] == 1
    assert r['before']['state'] == 'OPEN'


def test_annual_review_rejects_bad_intent_and_other_kinds_in_open():
    assert cs.transition(st(), ev('ANNUAL_REVIEW', intent='RETIRED'))['outcome'] == 'REJECTED'
    assert cs.transition(st(), ev('RESERVE_CONTRACT', **OFFER))['outcome'] == 'REJECTED'


def test_signing_path_returns_player():
    s, rs = run(st('AVAILABLE'), ev('RESERVE_CONTRACT', **OFFER),
                ev('CONTRACT_VERIFIED', offer_id='offer-9', receipt_id='rcpt-1'))
    assert [r['outcome'] for r in rs] == ['APPLIED', 'APPLIED']
    assert s['state'] == 'RETURNING' and s['reservation']['receipt_id'] == 'rcpt-1'


def test_reservation_needs_real_acceptable_legal_offer():
    for bad in (dict(OFFER, offer_id=''), dict(OFFER, acceptable=False), dict(OFFER, legal=None),
                dict(OFFER, offer_kind='need')):
        assert cs.transition(st('AVAILABLE'), ev('RESERVE_CONTRACT', **bad))['outcome'] == 'REJECTED'


def test_existing_contract_returns_without_new_deal():
    r = cs.transition(st('AVAILABLE'), ev('RETURN_UNDER_CONTRACT', contract_id='c-1'))
    assert r['after']['state'] == 'RETURNING' and r['after']['reservation']['kind'] == 'EXISTING_CONTRACT'
    assert cs.transition(st('AVAILABLE'), ev('RETURN_UNDER_CONTRACT'))['outcome'] == 'REJECTED'


def test_no_offer_market_close_retirement_or_unsigned():
    r = cs.transition(st('AVAILABLE'), ev('MARKET_CLOSE', outcome='RETIRE_INTENT'))
    assert r['after']['state'] == 'RETIRE_INTENT'
    r = cs.transition(st('AVAILABLE'), ev('MARKET_CLOSE', outcome='UNSIGNED_RETURN_ELIGIBLE',
                                          preservation_proven=True))
    assert r['after']['state'] == 'UNSIGNED_RETURN_ELIGIBLE'
    r = cs.transition(st('AVAILABLE'), ev('MARKET_CLOSE', outcome='UNSIGNED_RETURN_ELIGIBLE'))
    assert r['after']['state'] == 'CONFLICT' and 'block_rollover' in r['reasons'][0]
    assert cs.transition(st('AVAILABLE'), ev('MARKET_CLOSE', outcome='SIGNED'))['outcome'] == 'REJECTED'


def test_unsigned_returner_may_reserve_only_street_offer():
    s = st('UNSIGNED_RETURN_ELIGIBLE')
    assert cs.transition(s, ev('RESERVE_CONTRACT', **OFFER))['outcome'] == 'REJECTED'
    r = cs.transition(s, ev('RESERVE_CONTRACT', **dict(OFFER, offer_kind='street')))
    assert r['after']['state'] == 'SIGN_PENDING'
    back = cs.transition(r['after'], ev('CONTRACT_FAILED', rev=1, offer_id='offer-9'))
    assert back['after']['state'] == 'UNSIGNED_RETURN_ELIGIBLE' and back['after']['reservation'] is None
    assert cs.transition(s, ev('ANNUAL_REVIEW', intent='AVAILABLE'))['outcome'] == 'REJECTED'


def test_retirement_path():
    s, rs = run(st('RETIRE_INTENT'), ev('PREPARE_RETIREMENT', operation_id='op-1'),
                ev('RETIREMENT_VERIFIED', operation_id='op-1', save_receipt_id='save-1'))
    assert s['state'] == 'RETIRED' and [r['outcome'] for r in rs] == ['APPLIED', 'APPLIED']


def test_retirement_needs_save_receipt_and_matching_operation():
    pending = cs.transition(st('RETIRE_INTENT'), ev('PREPARE_RETIREMENT', operation_id='op-1'))['after']
    assert cs.transition(pending, ev('RETIREMENT_VERIFIED', rev=1, operation_id='op-1'))['outcome'] == 'REJECTED'
    assert cs.transition(pending, ev('RETIREMENT_VERIFIED', rev=1, operation_id='op-2',
                                     save_receipt_id='s'))['outcome'] == 'REJECTED'


def test_signing_and_retirement_exclude_each_other():
    signing = cs.transition(st('AVAILABLE'), ev('RESERVE_CONTRACT', **OFFER))['after']
    assert cs.transition(signing, ev('PREPARE_RETIREMENT', rev=1, operation_id='op'))['outcome'] == 'REJECTED'
    assert cs.can_prepare_retirement(signing)[0] is False
    retiring = cs.transition(st('RETIRE_INTENT'), ev('PREPARE_RETIREMENT', operation_id='op'))['after']
    assert cs.transition(retiring, ev('RESERVE_CONTRACT', rev=1, **OFFER))['outcome'] == 'REJECTED'
    assert cs.transition(retiring, ev('CONTRACT_VERIFIED', rev=1, offer_id='x', receipt_id='r'))['outcome'] \
        == 'REJECTED'
    assert cs.can_reserve_contract(retiring)[0] is False
    assert cs.transition(st('RETIRE_INTENT'), ev('RESERVE_CONTRACT', **OFFER))['outcome'] == 'REJECTED'


def test_pending_moves_block_retirement_preparation():
    for move in ('sign', 'extension', 'tag', 'trade'):
        r = cs.transition(st('RETIRE_INTENT'), ev('PREPARE_RETIREMENT', operation_id='op', pending_moves=[move]))
        assert r['outcome'] == 'REJECTED' and move in r['reasons'][0]


def test_failed_operations_recover():
    signing = cs.transition(st('AVAILABLE'), ev('RESERVE_CONTRACT', **OFFER))['after']
    r = cs.transition(signing, ev('CONTRACT_FAILED', rev=1, offer_id='offer-9'))
    assert r['after']['state'] == 'AVAILABLE' and r['after']['reservation'] is None
    retiring = cs.transition(st('RETIRE_INTENT'), ev('PREPARE_RETIREMENT', operation_id='op'))['after']
    r = cs.transition(retiring, ev('RETIREMENT_FAILED', rev=1, operation_id='op'))
    assert r['after']['state'] == 'RETIRE_INTENT'


def test_cancel_retirement_needs_explicit_review():
    assert cs.transition(st('RETIRE_INTENT'), ev('CANCEL_RETIREMENT', review_id='rv'))['outcome'] == 'REJECTED'
    r = cs.transition(st('RETIRE_INTENT'), ev('CANCEL_RETIREMENT', actor='JR', review_id='rv'))
    assert r['after']['state'] == 'AVAILABLE'


def test_terminal_states_refuse_everything():
    for s in ('RETIRED', 'RETURNING'):
        for kind in ('ANNUAL_REVIEW', 'RESERVE_CONTRACT', 'PREPARE_RETIREMENT', 'RECONCILE', 'FLAG_CONFLICT'):
            assert cs.transition(st(s, reservation=None), ev(kind, intent='AVAILABLE'))['outcome'] == 'REJECTED'


def test_blocked_and_conflict_reconcile_only():
    for s in ('BLOCKED_DATA', 'CONFLICT'):
        assert cs.transition(st(s), ev('ANNUAL_REVIEW', intent='AVAILABLE'))['outcome'] == 'REJECTED'
        assert cs.transition(st(s), ev('RECONCILE', to='OPEN', review_id='r'))['outcome'] == 'REJECTED'
        assert cs.transition(st(s), ev('RECONCILE', actor='JR', to='RETIRED', review_id='r'))['outcome'] \
            == 'REJECTED'
        r = cs.transition(st(s), ev('RECONCILE', actor='JR', to='OPEN', review_id='r'))
        assert r['after']['state'] == 'OPEN'


def test_native_retirement_after_return_is_conflict():
    for s in ('AVAILABLE', 'UNSIGNED_RETURN_ELIGIBLE'):
        assert cs.transition(st(s), ev('NATIVE_RETIREMENT_OBSERVED'))['after']['state'] == 'CONFLICT'
    signing = cs.transition(st('AVAILABLE'), ev('RESERVE_CONTRACT', **OFFER))['after']
    assert cs.transition(signing, ev('NATIVE_RETIREMENT_OBSERVED', rev=1))['after']['state'] == 'CONFLICT'
    r = cs.transition(st('RETIRE_INTENT'), ev('NATIVE_RETIREMENT_OBSERVED'))
    assert r['outcome'] == 'APPLIED' and r['after'] == r['before']


def test_stale_revision_changes_nothing():
    r = cs.transition(st('AVAILABLE', revision=3), ev('RESERVE_CONTRACT', rev=2, **OFFER))
    assert r['outcome'] == 'STALE' and r['after'] == r['before']


def test_receipt_hash_is_deterministic_and_input_not_mutated():
    s, e = st('AVAILABLE'), ev('RESERVE_CONTRACT', **OFFER)
    a, b = cs.transition(s, e), cs.transition(s, e)
    assert a == b and a['request_hash'] == cs.request_hash(e) and s['state'] == 'AVAILABLE'


def test_malformed_input_raises():
    with pytest.raises(cs.CareerStateError):
        cs.transition({'state': 'OPEN'}, ev('ANNUAL_REVIEW', intent='AVAILABLE'))
    with pytest.raises(cs.CareerStateError):
        cs.transition(st(), dict(ev('ANNUAL_REVIEW'), kind='TELEPORT'))
    with pytest.raises(cs.CareerStateError):
        cs.validate_state(st('SIGN_PENDING'))


def test_module_does_not_import_policy():
    import engines.career_decision_state as mod
    assert 'retirement_policy' not in open(mod.__file__).read()
