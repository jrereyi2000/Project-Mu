"""One career state machine shared by signing and retirement (PERSONALITY_RETIREMENT_PLAN.md §2.4, U2).

Pure dictionaries in and out: no database, clock or RNG, and no import of the retirement policy. A person-season has
exactly one state; a contract reservation and a retirement preparation exclude each other.

  new_state(universe_id, person_key, earned_season, decision_id) -> state
  transition(state, event) -> receipt {event_id, request_hash, before, after, outcome, reasons}
  can_reserve_contract(state) / can_prepare_retirement(state) -> (bool, reason)
  request_hash(event) -> sha256 hex of the canonical event

State: {decision_id, universe_id, person_key, earned_season, state, revision, reservation}.
Event: {event_id, source_event_id, kind, expected_revision, payload, actor}.
Outcome: APPLIED (after is the new state, revision + 1), REJECTED (not allowed from this state or bad payload;
after == before) or STALE (expected_revision != revision; after == before). transition never raises for a
well-formed state and event; it raises CareerStateError only for malformed input.

  OPEN                      ANNUAL_REVIEW {intent AVAILABLE | RETIRE_INTENT | BLOCKED_DATA}
  AVAILABLE                 RESERVE_CONTRACT {offer_id, offer_kind, acceptable, legal} -> SIGN_PENDING
                            RETURN_UNDER_CONTRACT {contract_id} -> RETURNING (no new deal, no new roll)
                            MARKET_CLOSE {outcome RETIRE_INTENT | UNSIGNED_RETURN_ELIGIBLE, preservation_proven}
                              (unsigned without proven preservation -> CONFLICT: block rollover for review)
  RETIRE_INTENT             CANCEL_RETIREMENT {review_id} -> AVAILABLE (explicit review, needs an actor)
                            PREPARE_RETIREMENT {operation_id, pending_moves} -> RETIRE_PENDING
                              (refused while any sign/extension/tag/trade is pending for the person)
  SIGN_PENDING              CONTRACT_VERIFIED {offer_id, receipt_id} -> RETURNING
                            CONTRACT_FAILED {offer_id} -> the state the reservation came from
  RETIRE_PENDING            RETIREMENT_VERIFIED {operation_id, save_receipt_id} -> RETIRED
                            RETIREMENT_FAILED {operation_id} -> RETIRE_INTENT
  UNSIGNED_RETURN_ELIGIBLE  RESERVE_CONTRACT (street offers only) -> SIGN_PENDING; next season has its own review
  RETURNING, RETIRED        terminal for the season (a comeback is a separately reviewed workflow)
  BLOCKED_DATA, CONFLICT    RECONCILE {to OPEN | AVAILABLE | RETIRE_INTENT, review_id} with an actor only
  any non-terminal          FLAG_CONFLICT {reason} -> CONFLICT
  NATIVE_RETIREMENT_OBSERVED: CONFLICT from AVAILABLE, SIGN_PENDING, RETURNING, UNSIGNED_RETURN_ELIGIBLE (a native
                            retirement after a Mu return pauses the advance; never an automatic override loop);
                            recorded without change in RETIRE_INTENT / RETIRE_PENDING.

An offer is needed to sign, not to want to continue: AVAILABLE never requires an offer, and no acceptable offer at
market close yields retirement or honest unsigned availability, never a manufactured signing.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json

STATES = ('OPEN', 'AVAILABLE', 'RETIRE_INTENT', 'SIGN_PENDING', 'RETIRE_PENDING', 'RETURNING',
          'UNSIGNED_RETURN_ELIGIBLE', 'RETIRED', 'BLOCKED_DATA', 'CONFLICT')
TERMINAL = frozenset({'RETURNING', 'RETIRED'})
EVENT_KINDS = ('ANNUAL_REVIEW', 'RESERVE_CONTRACT', 'RETURN_UNDER_CONTRACT', 'MARKET_CLOSE', 'CANCEL_RETIREMENT',
               'PREPARE_RETIREMENT', 'CONTRACT_VERIFIED', 'CONTRACT_FAILED', 'RETIREMENT_VERIFIED',
               'RETIREMENT_FAILED', 'RECONCILE', 'FLAG_CONFLICT', 'NATIVE_RETIREMENT_OBSERVED')
OFFER_KINDS = ('re_sign', 'extension', 'tag', 'free_agency', 'street', 'trade', 'user_move')
PENDING_BLOCKERS = frozenset({'sign', 'extension', 'tag', 'trade', 're_sign', 'free_agency', 'street', 'user_move'})
RECONCILE_TARGETS = ('OPEN', 'AVAILABLE', 'RETIRE_INTENT')
OUTCOMES = ('APPLIED', 'REJECTED', 'STALE')
_STATE_KEYS = ('decision_id', 'universe_id', 'person_key', 'earned_season', 'state', 'revision', 'reservation')
_EVENT_KEYS = ('event_id', 'source_event_id', 'kind', 'expected_revision', 'payload', 'actor')


class CareerStateError(ValueError):
    pass


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def request_hash(event):
    return hashlib.sha256(_canon(event).encode('utf-8')).hexdigest()


def _text(v):
    return isinstance(v, str) and v.strip() != ''


def new_state(universe_id, person_key, earned_season, decision_id):
    s = {'decision_id': decision_id, 'universe_id': universe_id, 'person_key': person_key,
         'earned_season': earned_season, 'state': 'OPEN', 'revision': 0, 'reservation': None}
    validate_state(s)
    return s


def validate_state(state):
    if not isinstance(state, dict) or set(state) != set(_STATE_KEYS):
        raise CareerStateError(f'state needs exactly {_STATE_KEYS}')
    for k in ('decision_id', 'universe_id', 'person_key'):
        if not _text(state[k]):
            raise CareerStateError(f'state.{k} must be nonempty text')
    if not isinstance(state['earned_season'], int) or isinstance(state['earned_season'], bool):
        raise CareerStateError('state.earned_season must be an integer')
    if state['state'] not in STATES:
        raise CareerStateError(f'unknown state {state["state"]!r}')
    if not isinstance(state['revision'], int) or isinstance(state['revision'], bool) or state['revision'] < 0:
        raise CareerStateError('state.revision must be an integer >= 0')
    r = state['reservation']
    if state['state'] in ('SIGN_PENDING', 'RETIRE_PENDING'):
        want = 'CONTRACT' if state['state'] == 'SIGN_PENDING' else 'RETIREMENT'
        if not isinstance(r, dict) or r.get('kind') != want:
            raise CareerStateError(f'{state["state"]} needs a {want} reservation')
    elif r is not None and state['state'] != 'RETURNING':
        raise CareerStateError(f'{state["state"]} holds no reservation')
    return state


def validate_event(event):
    if not isinstance(event, dict) or set(event) != set(_EVENT_KEYS):
        raise CareerStateError(f'event needs exactly {_EVENT_KEYS}')
    for k in ('event_id', 'source_event_id', 'kind'):
        if not _text(event[k]):
            raise CareerStateError(f'event.{k} must be nonempty text')
    if event['kind'] not in EVENT_KINDS:
        raise CareerStateError(f'unknown event kind {event["kind"]!r}')
    if not isinstance(event['expected_revision'], int) or isinstance(event['expected_revision'], bool):
        raise CareerStateError('event.expected_revision must be an integer')
    if not isinstance(event['payload'], dict):
        raise CareerStateError('event.payload must be an object')
    if event['actor'] is not None and not _text(event['actor']):
        raise CareerStateError('event.actor must be nonempty text or null')
    return event


def can_reserve_contract(state):
    s = state['state']
    if s == 'AVAILABLE':
        return True, 'available'
    if s == 'UNSIGNED_RETURN_ELIGIBLE':
        return True, 'street_offers_only'
    return False, f'no_contract_reservation_in_{s}'


def can_prepare_retirement(state):
    if state['state'] == 'RETIRE_INTENT':
        return True, 'retire_intent'
    return False, f'no_retirement_preparation_in_{state["state"]}'


def _receipt(event, before, after, outcome, reasons):
    return {'event_id': event['event_id'], 'request_hash': request_hash(event), 'before': before,
            'after': after, 'outcome': outcome, 'reasons': list(reasons)}


def transition(state, event):
    validate_state(state)
    validate_event(event)
    before = deepcopy(state)
    if event['expected_revision'] != state['revision']:
        return _receipt(event, before, deepcopy(state),
                        'STALE', [f'expected_revision {event["expected_revision"]} != revision {state["revision"]}'])
    result = _step(state, event)
    if isinstance(result, str):
        return _receipt(event, before, deepcopy(state), 'REJECTED', [result])
    new, reasons = result
    if new is None:                                    # recorded without a state change
        return _receipt(event, before, deepcopy(state), 'APPLIED', reasons)
    after = dict(deepcopy(state), state=new['state'], reservation=new.get('reservation'),
                 revision=state['revision'] + 1)
    validate_state(after)
    return _receipt(event, before, after, 'APPLIED', reasons)


def _step(state, event):
    """(new {'state', 'reservation'} or None, reasons) or a rejection reason string."""
    s, kind, p, actor = state['state'], event['kind'], event['payload'], event['actor']
    if s in TERMINAL:
        return f'{s} is terminal for the season'
    if kind == 'FLAG_CONFLICT':
        if s == 'CONFLICT':
            return 'already in CONFLICT'
        return {'state': 'CONFLICT'}, [f'conflict:{p.get("reason", "unspecified")}']
    if kind == 'NATIVE_RETIREMENT_OBSERVED':
        if s in ('AVAILABLE', 'SIGN_PENDING', 'UNSIGNED_RETURN_ELIGIBLE'):
            return {'state': 'CONFLICT'}, ['native_retirement_conflicts_with_mu_decision']
        if s in ('RETIRE_INTENT', 'RETIRE_PENDING', 'CONFLICT', 'BLOCKED_DATA', 'OPEN'):
            return None, ['native_retirement_recorded']
    if s == 'OPEN':
        if kind != 'ANNUAL_REVIEW':
            return f'{kind} not allowed in OPEN'
        intent = p.get('intent')
        if intent not in ('AVAILABLE', 'RETIRE_INTENT', 'BLOCKED_DATA'):
            return 'annual review intent must be AVAILABLE, RETIRE_INTENT or BLOCKED_DATA'
        return {'state': intent}, [f'annual_review:{intent}']
    if kind == 'RESERVE_CONTRACT':
        ok, why = can_reserve_contract(state)
        if not ok:
            return why
        if not _text(p.get('offer_id')):
            return 'a contract reservation needs a real offer_id'
        if p.get('offer_kind') not in OFFER_KINDS:
            return f'offer_kind must be one of {OFFER_KINDS}'
        if s == 'UNSIGNED_RETURN_ELIGIBLE' and p['offer_kind'] != 'street':
            return 'an unsigned returner may reserve only a later street offer'
        if p.get('acceptable') is not True or p.get('legal') is not True:
            return 'only an acceptable, legal offer (decided by the contract owner) can be reserved'
        return ({'state': 'SIGN_PENDING', 'reservation': {'kind': 'CONTRACT', 'offer_id': p['offer_id'],
                                                          'offer_kind': p['offer_kind'], 'from_state': s}},
                ['contract_reserved'])
    if s == 'AVAILABLE':
        if kind == 'RETURN_UNDER_CONTRACT':
            if not _text(p.get('contract_id')):
                return 'returning without a new deal needs the existing contract_id'
            return ({'state': 'RETURNING', 'reservation': {'kind': 'EXISTING_CONTRACT',
                                                           'contract_id': p['contract_id']}},
                    ['returns_under_existing_contract'])
        if kind == 'MARKET_CLOSE':
            outcome = p.get('outcome')
            if outcome == 'RETIRE_INTENT':
                return {'state': 'RETIRE_INTENT'}, ['market_close:retire_intent']
            if outcome == 'UNSIGNED_RETURN_ELIGIBLE':
                if p.get('preservation_proven') is not True:
                    return {'state': 'CONFLICT'}, ['unsigned_preservation_unproven:block_rollover_for_review']
                return {'state': 'UNSIGNED_RETURN_ELIGIBLE'}, ['market_close:unsigned_return_eligible']
            return 'market close outcome must be RETIRE_INTENT or UNSIGNED_RETURN_ELIGIBLE'
        return f'{kind} not allowed in AVAILABLE'
    if s == 'RETIRE_INTENT':
        if kind == 'CANCEL_RETIREMENT':
            if actor is None or not _text(p.get('review_id')):
                return 'cancelling a retirement intent needs an explicit review (actor and review_id)'
            return {'state': 'AVAILABLE'}, [f'retirement_cancelled_by:{actor}']
        if kind == 'PREPARE_RETIREMENT':
            if not _text(p.get('operation_id')):
                return 'preparing a retirement needs an operation_id'
            pending = [m for m in p.get('pending_moves') or () if m in PENDING_BLOCKERS]
            if pending:
                return f'pending {", ".join(sorted(set(pending)))} excludes retirement'
            return ({'state': 'RETIRE_PENDING', 'reservation': {'kind': 'RETIREMENT',
                                                                'operation_id': p['operation_id']}},
                    ['retirement_prepared'])
        return f'{kind} not allowed in RETIRE_INTENT'
    if s == 'SIGN_PENDING':
        res = state['reservation']
        if kind in ('CONTRACT_VERIFIED', 'CONTRACT_FAILED') and p.get('offer_id') != res['offer_id']:
            return 'offer_id does not match the reservation'
        if kind == 'CONTRACT_VERIFIED':
            if not _text(p.get('receipt_id')):
                return 'a verified contract needs its receipt_id'
            return ({'state': 'RETURNING', 'reservation': dict(res, receipt_id=p['receipt_id'])},
                    ['contract_verified'])
        if kind == 'CONTRACT_FAILED':
            return {'state': res.get('from_state', 'AVAILABLE')}, ['contract_failed:reservation_released']
        return f'{kind} not allowed in SIGN_PENDING (no retirement while a contract is reserved)'
    if s == 'RETIRE_PENDING':
        res = state['reservation']
        if kind in ('RETIREMENT_VERIFIED', 'RETIREMENT_FAILED') and p.get('operation_id') != res['operation_id']:
            return 'operation_id does not match the reservation'
        if kind == 'RETIREMENT_VERIFIED':
            if not _text(p.get('save_receipt_id')):
                return 'a retirement is verified only by a save receipt'
            return {'state': 'RETIRED', 'reservation': None}, ['retirement_verified']
        if kind == 'RETIREMENT_FAILED':
            return {'state': 'RETIRE_INTENT'}, ['retirement_failed:back_to_intent']
        return f'{kind} not allowed in RETIRE_PENDING (no contract acceptance while retiring)'
    if s in ('BLOCKED_DATA', 'CONFLICT'):
        if kind != 'RECONCILE':
            return f'{s} accepts reconciliation only'
        if actor is None or not _text(p.get('review_id')):
            return 'reconciliation needs an actor and a review_id'
        if p.get('to') not in RECONCILE_TARGETS:
            return f'reconciliation target must be one of {RECONCILE_TARGETS}'
        return {'state': p['to']}, [f'reconciled_by:{actor}']
    if s == 'UNSIGNED_RETURN_ELIGIBLE':
        return f'{kind} not allowed in UNSIGNED_RETURN_ELIGIBLE (next season has its own annual review)'
    return f'{kind} not allowed in {s}'
