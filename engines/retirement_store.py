"""Retirement policy persistence (PERSONALITY_RETIREMENT_PLAN.md §2.4-2.5, U4).

Tables from db/migrations/2027_01_26_retirement_policy.sql (mirrored in db/init_db.sql). Caller-owned transaction:
an explicit psycopg cursor on a NON-autocommit connection. Nothing here connects, commits, seeds or migrates. Each
write runs in its own savepoint under the transaction advisory lock for 'retirement:<universe>:<person>', so a
failure leaves nothing behind and propagates, and two writers for one person serialize (the unique keys and the CAS
revision are the final defense).

  record_review(cur, decision, *, player_id, source_event_id, input_snapshot, policy_snapshot, actor=None)
  apply_event(cur, decision_id, event, *, input_snapshot=None) -> receipt
  reserve_contract(cur, decision_id, offer_id, expected_revision, *, offer_kind, acceptable, legal,
                   source_event_id, event_id=None, actor=None) -> receipt
  prepare_retirement(cur, decision_id, expected_revision, *, operation_id, source_event_id, pending_moves=(),
                     event_id=None, actor=None) -> receipt
  prepare(cur, request, expected_revision) -> write receipt (PREPARED)
  acknowledge(cur, operation_id, receipt) -> write receipt (VERIFIED | FAILED | CONFLICT)
  replay(cur, event_id) -> stored event receipt | None
  read_state(cur, universe_id, person_key, earned_season) / read_state_by_decision(cur, decision_id)

`decision` is an `engines.retirement_policy.RetirementDecision` or its `to_dict`. The OPEN review creates the state
row (OPEN, revision 0) and applies ANNUAL_REVIEW; later checkpoints append a review event without changing state
(the caller then applies MARKET_CLOSE etc. through apply_event). A review whose draw differs from the stored draw for
the decision is refused: acknowledged history is never redrawn.

Idempotency: the same event ID with the same request hash returns the original receipt (replayed True), even after
newer revisions; the same event ID with another payload, or another event ID reusing a source event ID, is a
StoreConflict. STALE (wrong expected_revision) is a StoreConflict; REJECTED is a TransitionRejected; neither writes.

Write receipts: `prepare` stores the immutable request (operation_id, decision_id, source_save_hash, edits_hash,
before_image, league_office_run) and needs the decision in RETIRE_PENDING with the same operation_id; `acknowledge`
moves PREPARED -> VERIFIED / FAILED / CONFLICT by CAS and applies RETIREMENT_VERIFIED / RETIREMENT_FAILED /
FLAG_CONFLICT to the state. A save receipt must exist before any execution claim. No live caller is delivered.
"""
from contextlib import contextmanager
import hashlib
import json
import uuid

from engines import career_decision_state as cs

STATES_TABLE = 'career_decision_states'
EVENTS_TABLE = 'retirement_policy_events'
RECEIPTS_TABLE = 'retirement_write_receipts'
EVENT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, 'project-mu:retirement-events')
ACK_STATUSES = ('VERIFIED', 'FAILED', 'CONFLICT')
_ACK_EVENT = {'VERIFIED': 'RETIREMENT_VERIFIED', 'FAILED': 'RETIREMENT_FAILED', 'CONFLICT': 'FLAG_CONFLICT'}


class StoreError(RuntimeError):
    pass


class StoreConflict(StoreError):
    """Conflicting replay, reused source event, stale revision, redraw or a lost race."""


class TransitionRejected(StoreError):
    """The career state machine refused the event; nothing was written."""


def _jsonb(value):
    from psycopg.types.json import Jsonb
    return Jsonb(value)


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, default=str)


def _hash(value):
    return hashlib.sha256(_canon(value).encode('utf-8')).hexdigest()


def _loads(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


@contextmanager
def _savepoint(cur):
    name = 'retirement_' + uuid.uuid4().hex
    cur.execute(f'SAVEPOINT {name}')
    try:
        yield
    except BaseException:
        cur.execute(f'ROLLBACK TO SAVEPOINT {name}')
        cur.execute(f'RELEASE SAVEPOINT {name}')
        raise
    else:
        cur.execute(f'RELEASE SAVEPOINT {name}')


def _require_transaction(cur):
    conn = getattr(cur, 'connection', None)
    if conn is None or getattr(conn, 'autocommit', True):
        raise StoreError('retirement writes need a cursor on a non-autocommit connection')


def lock_key(universe_id, person_key):
    digest = hashlib.sha256(f'retirement:{universe_id}:{person_key}'.encode('utf-8')).digest()
    return int.from_bytes(digest[:8], 'big', signed=True)


def _lock(cur, universe_id, person_key):
    cur.execute('SELECT pg_advisory_xact_lock(%s)', (lock_key(universe_id, person_key),))
    cur.fetchone()


def review_event_id(decision_id, checkpoint, source_event_id):
    return str(uuid.uuid5(EVENT_NAMESPACE, _canon(['review', str(decision_id), checkpoint, source_event_id])))


# ------------------------------------------------------------------------------------------------ reads ---

_STATE_COLS = 'universe_id, person_key, earned_season, player_id, state, revision, decision_id, reservation'


def _row_state(row):
    if row is None:
        return None
    universe_id, person_key, earned_season, player_id, state, revision, decision_id, reservation = row
    return {'decision_id': str(decision_id), 'universe_id': universe_id, 'person_key': person_key,
            'earned_season': earned_season, 'state': state, 'revision': revision,
            'reservation': _loads(reservation)}, player_id


def read_state(cur, universe_id, person_key, earned_season):
    cur.execute(f'SELECT {_STATE_COLS} FROM {STATES_TABLE} WHERE universe_id = %s AND person_key = %s '
                'AND earned_season = %s', (universe_id, person_key, earned_season))
    r = _row_state(cur.fetchone())
    return None if r is None else r[0]


def read_state_by_decision(cur, decision_id, *, for_update=False):
    cur.execute(f'SELECT {_STATE_COLS} FROM {STATES_TABLE} WHERE decision_id = %s'
                + (' FOR UPDATE' if for_update else ''), (str(decision_id),))
    r = _row_state(cur.fetchone())
    return None if r is None else r[0]


def replay(cur, event_id):
    cur.execute(f'SELECT request_hash, receipt FROM {EVENTS_TABLE} WHERE event_id = %s', (str(event_id),))
    row = cur.fetchone()
    return None if row is None else _loads(row[1])


def _stored_event(cur, event_id):
    cur.execute(f'SELECT request_hash, receipt FROM {EVENTS_TABLE} WHERE event_id = %s', (str(event_id),))
    row = cur.fetchone()
    return None if row is None else (row[0], _loads(row[1]))


def _source_used(cur, decision_id, source_event_id):
    cur.execute(f'SELECT event_id FROM {EVENTS_TABLE} WHERE decision_id = %s AND source_event_id = %s',
                (str(decision_id), source_event_id))
    row = cur.fetchone()
    return None if row is None else str(row[0])


def _stored_draw(cur, decision_id):
    cur.execute(f'SELECT draw_hex FROM {EVENTS_TABLE} WHERE decision_id = %s AND draw_hex IS NOT NULL LIMIT 1',
                (str(decision_id),))
    row = cur.fetchone()
    return None if row is None else row[0]


def _read_receipt(cur, operation_id):
    cur.execute(f'SELECT operation_id, decision_id, status, source_save_hash, output_hash, edits_hash, '
                f'league_office_run, before_image, verification, request_hash, ack_hash FROM {RECEIPTS_TABLE} '
                'WHERE operation_id = %s FOR UPDATE', (str(operation_id),))
    row = cur.fetchone()
    if row is None:
        return None
    keys = ('operation_id', 'decision_id', 'status', 'source_save_hash', 'output_hash', 'edits_hash',
            'league_office_run', 'before_image', 'verification', 'request_hash', 'ack_hash')
    d = dict(zip(keys, row))
    d['operation_id'], d['decision_id'] = str(d['operation_id']), str(d['decision_id'])
    d['before_image'], d['verification'] = _loads(d['before_image']), _loads(d['verification'])
    return d


# ----------------------------------------------------------------------------------------------- writes ---

def _replay_or_conflict(cur, event_id, request_hash):
    stored = _stored_event(cur, event_id)
    if stored is None:
        return None
    if stored[0] != request_hash:
        raise StoreConflict(f'event {event_id} was already recorded with a different payload')
    return dict(stored[1], replayed=True)


def _insert_event(cur, *, event_id, decision_id, checkpoint, event_kind, source_event_id, request_hash,
                  input_snapshot, policy_snapshot, draw_hex, breakdown, before, after, receipt):
    cur.execute(
        f'INSERT INTO {EVENTS_TABLE} (event_id, decision_id, checkpoint, event_kind, source_event_id, request_hash, '
        'input_snapshot, policy_snapshot, draw_hex, breakdown, before_state, after_state, receipt) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
        (str(event_id), str(decision_id), checkpoint, event_kind, source_event_id, request_hash,
         _jsonb(input_snapshot), _jsonb(policy_snapshot), draw_hex,
         None if breakdown is None else _jsonb(breakdown), _jsonb(before), _jsonb(after), _jsonb(receipt)))


def _update_state(cur, before, after):
    cur.execute(f'UPDATE {STATES_TABLE} SET state = %s, revision = %s, reservation = %s, updated_at = now() '
                'WHERE decision_id = %s AND revision = %s',
                (after['state'], after['revision'],
                 None if after['reservation'] is None else _jsonb(after['reservation']),
                 before['decision_id'], before['revision']))
    if cur.rowcount != 1:
        raise StoreConflict(f'decision {before["decision_id"]} changed concurrently')


def _transition_checked(state, event):
    r = cs.transition(state, event)
    if r['outcome'] == 'STALE':
        raise StoreConflict('; '.join(r['reasons']))
    if r['outcome'] == 'REJECTED':
        raise TransitionRejected('; '.join(r['reasons']))
    return r


def _decision_dict(decision):
    if hasattr(decision, '__dataclass_fields__'):
        from engines import retirement_policy as rp
        return rp.to_dict(decision)
    return dict(decision)


def record_review(cur, decision, *, player_id, source_event_id, input_snapshot, policy_snapshot, actor=None):
    """Persist one checkpoint review. Returns the event receipt (with `replayed`)."""
    d = _decision_dict(decision)
    for k in ('decision_id', 'universe_id', 'person_key', 'checkpoint', 'intent', 'draw_hex'):
        if not d.get(k):
            raise StoreError(f'a reviewed decision needs {k} (blocked identity is not persisted)')
    if not isinstance(source_event_id, str) or not source_event_id:
        raise StoreError('source_event_id is required')
    _require_transaction(cur)
    event_id = review_event_id(d['decision_id'], d['checkpoint'], source_event_id)
    request_hash = _hash({'kind': 'REVIEW', 'decision': d, 'player_id': player_id, 'input': input_snapshot,
                          'policy': policy_snapshot, 'actor': actor})
    with _savepoint(cur):
        _lock(cur, d['universe_id'], d['person_key'])
        replayed = _replay_or_conflict(cur, event_id, request_hash)
        if replayed is not None:
            return replayed
        state = read_state_by_decision(cur, d['decision_id'], for_update=True)
        if state is None:
            existing = read_state(cur, d['universe_id'], d['person_key'], d['earned_season'])
            if existing is not None:
                raise StoreConflict('another decision already exists for this person and season')
            if d['checkpoint'] != 'OPEN':
                raise StoreError('the first review of a season must be the OPEN checkpoint')
            state = cs.new_state(d['universe_id'], d['person_key'], d['earned_season'], d['decision_id'])
            cur.execute(f'INSERT INTO {STATES_TABLE} (universe_id, person_key, earned_season, player_id, state, '
                        'revision, decision_id, reservation) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
                        (state['universe_id'], state['person_key'], state['earned_season'], player_id,
                         state['state'], state['revision'], state['decision_id'], None))
        else:
            other = _source_used(cur, d['decision_id'], source_event_id)
            if other is not None:
                raise StoreConflict(f'source {source_event_id} already recorded as {other}')
        drawn = _stored_draw(cur, d['decision_id'])
        if drawn is not None and drawn != d['draw_hex']:
            raise StoreConflict('the review carries a different draw than the stored decision: never redraw')
        if d['checkpoint'] == 'OPEN':
            if state['state'] != 'OPEN':
                raise StoreConflict(f'the OPEN review already ran (state {state["state"]})')
            event = {'event_id': event_id, 'source_event_id': source_event_id, 'kind': 'ANNUAL_REVIEW',
                     'expected_revision': state['revision'], 'payload': {'intent': d['intent']}, 'actor': actor}
            r = _transition_checked(state, event)
            _update_state(cur, state, r['after'])
        else:
            r = {'event_id': event_id, 'request_hash': request_hash,
                 'before': state, 'after': state, 'outcome': 'APPLIED', 'reasons': [f'review:{d["checkpoint"]}']}
        receipt = dict(r, replayed=False, decision={'final_p': d.get('final_p'), 'intent': d['intent'],
                                                    'checkpoint': d['checkpoint']})
        _insert_event(cur, event_id=event_id, decision_id=d['decision_id'], checkpoint=d['checkpoint'],
                      event_kind='REVIEW', source_event_id=source_event_id, request_hash=request_hash,
                      input_snapshot=input_snapshot, policy_snapshot=policy_snapshot, draw_hex=d['draw_hex'],
                      breakdown={'base': d.get('base'), 'modifiers': d.get('modifiers'), 'final_p': d.get('final_p'),
                                 'warnings': list(d.get('warnings') or ())},
                      before=r['before'], after=r['after'], receipt=receipt)
        return receipt


def apply_event(cur, decision_id, event, *, input_snapshot=None):
    """Apply one career-state event (engines.career_decision_state event shape) with lock, replay and CAS."""
    cs.validate_event(event)
    try:
        uuid.UUID(event['event_id'])
    except ValueError:
        raise StoreError('event_id must be a UUID') from None
    _require_transaction(cur)
    request_hash = cs.request_hash(event)
    peek = read_state_by_decision(cur, decision_id)
    if peek is None:
        raise StoreError(f'no decision {decision_id}')
    with _savepoint(cur):
        _lock(cur, peek['universe_id'], peek['person_key'])
        replayed = _replay_or_conflict(cur, event['event_id'], request_hash)
        if replayed is not None:
            return replayed
        other = _source_used(cur, decision_id, event['source_event_id'])
        if other is not None:
            raise StoreConflict(f'source {event["source_event_id"]} already recorded as {other}')
        state = read_state_by_decision(cur, decision_id, for_update=True)
        r = _transition_checked(state, event)
        if r['after'] != r['before']:
            _update_state(cur, state, r['after'])
        receipt = dict(r, replayed=False)
        _insert_event(cur, event_id=event['event_id'], decision_id=decision_id, checkpoint='EVENT',
                      event_kind=event['kind'], source_event_id=event['source_event_id'], request_hash=request_hash,
                      input_snapshot=input_snapshot if input_snapshot is not None else {'payload': event['payload']},
                      policy_snapshot={}, draw_hex=None, breakdown=None, before=r['before'], after=r['after'],
                      receipt=receipt)
        return receipt


def _event_id(event_id, *parts):
    return event_id or str(uuid.uuid5(EVENT_NAMESPACE, _canon(list(parts))))


def reserve_contract(cur, decision_id, offer_id, expected_revision, *, offer_kind, acceptable, legal,
                     source_event_id, event_id=None, actor=None):
    """Reserve an acceptable, legal offer (the contract owner decides both) for this person-season."""
    event = {'event_id': _event_id(event_id, 'reserve', str(decision_id), source_event_id),
             'source_event_id': source_event_id, 'kind': 'RESERVE_CONTRACT', 'expected_revision': expected_revision,
             'payload': {'offer_id': offer_id, 'offer_kind': offer_kind, 'acceptable': acceptable, 'legal': legal},
             'actor': actor}
    return apply_event(cur, decision_id, event)


def prepare_retirement(cur, decision_id, expected_revision, *, operation_id, source_event_id, pending_moves=(),
                       event_id=None, actor=None):
    """RETIRE_INTENT -> RETIRE_PENDING; refused while a sign/extension/tag/trade is pending for the person."""
    event = {'event_id': _event_id(event_id, 'prepare', str(decision_id), source_event_id),
             'source_event_id': source_event_id, 'kind': 'PREPARE_RETIREMENT',
             'expected_revision': expected_revision,
             'payload': {'operation_id': str(operation_id), 'pending_moves': list(pending_moves)}, 'actor': actor}
    return apply_event(cur, decision_id, event)


_REQUEST_KEYS = ('operation_id', 'decision_id', 'source_save_hash', 'edits_hash', 'before_image')


def prepare(cur, request, expected_revision):
    """Store the immutable save-write request for a RETIRE_PENDING decision. Same operation and payload returns the
    original receipt; a changed payload under the same operation_id is a StoreConflict."""
    for k in _REQUEST_KEYS:
        if request.get(k) in (None, ''):
            raise StoreError(f'write request needs {k}')
    _require_transaction(cur)
    request_hash = _hash({k: request.get(k) for k in _REQUEST_KEYS + ('league_office_run',)})
    peek = read_state_by_decision(cur, request['decision_id'])
    if peek is None:
        raise StoreError(f'no decision {request["decision_id"]}')
    with _savepoint(cur):
        _lock(cur, peek['universe_id'], peek['person_key'])
        stored = _read_receipt(cur, request['operation_id'])
        if stored is not None:
            if stored['request_hash'] != request_hash:
                raise StoreConflict(f'operation {request["operation_id"]} was prepared with a different request')
            return dict(stored, replayed=True)
        state = read_state_by_decision(cur, request['decision_id'], for_update=True)
        if state['revision'] != expected_revision:
            raise StoreConflict(f'expected_revision {expected_revision} != revision {state["revision"]}')
        res = state['reservation'] or {}
        if state['state'] != 'RETIRE_PENDING' or res.get('operation_id') != str(request['operation_id']):
            raise TransitionRejected('a write request needs the decision in RETIRE_PENDING for this operation')
        cur.execute(f'INSERT INTO {RECEIPTS_TABLE} (operation_id, decision_id, status, source_save_hash, edits_hash, '
                    'league_office_run, before_image, request_hash) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)',
                    (str(request['operation_id']), str(request['decision_id']), 'PREPARED',
                     request['source_save_hash'], request['edits_hash'], request.get('league_office_run'),
                     _jsonb(request['before_image']), request_hash))
        return {'operation_id': str(request['operation_id']), 'decision_id': str(request['decision_id']),
                'status': 'PREPARED', 'source_save_hash': request['source_save_hash'], 'output_hash': None,
                'edits_hash': request['edits_hash'], 'league_office_run': request.get('league_office_run'),
                'before_image': request['before_image'], 'verification': None, 'request_hash': request_hash,
                'ack_hash': None, 'replayed': False}


def acknowledge(cur, operation_id, receipt):
    """PREPARED -> VERIFIED | FAILED | CONFLICT by CAS, and the matching state event. receipt: {status,
    source_event_id, verification, output_hash (VERIFIED), save_receipt_id (VERIFIED), actor?}."""
    status = receipt.get('status')
    if status not in ACK_STATUSES:
        raise StoreError(f'acknowledgement status must be one of {ACK_STATUSES}')
    if status == 'VERIFIED' and (not receipt.get('output_hash') or not receipt.get('save_receipt_id')):
        raise StoreError('a VERIFIED acknowledgement needs output_hash and save_receipt_id (read-back receipt)')
    if not receipt.get('source_event_id'):
        raise StoreError('acknowledgement needs source_event_id')
    _require_transaction(cur)
    ack_hash = _hash(receipt)
    cur.execute(f'SELECT decision_id FROM {RECEIPTS_TABLE} WHERE operation_id = %s', (str(operation_id),))
    row = cur.fetchone()
    if row is None:
        raise StoreError(f'no prepared operation {operation_id}')
    peek = read_state_by_decision(cur, row[0])
    with _savepoint(cur):
        _lock(cur, peek['universe_id'], peek['person_key'])
        stored = _read_receipt(cur, operation_id)
        if stored['status'] != 'PREPARED':
            if stored['ack_hash'] == ack_hash:
                return dict(stored, replayed=True)
            raise StoreConflict(f'operation {operation_id} was already acknowledged as {stored["status"]}')
        state = read_state_by_decision(cur, stored['decision_id'], for_update=True)
        payload = {'operation_id': str(operation_id)}
        if status == 'VERIFIED':
            payload['save_receipt_id'] = receipt['save_receipt_id']
        elif status == 'CONFLICT':
            payload = {'reason': 'save_write_conflict'}
        event = {'event_id': str(uuid.uuid5(EVENT_NAMESPACE, _canon(['ack', str(operation_id)]))),
                 'source_event_id': receipt['source_event_id'], 'kind': _ACK_EVENT[status],
                 'expected_revision': state['revision'], 'payload': payload, 'actor': receipt.get('actor')}
        other = _source_used(cur, stored['decision_id'], event['source_event_id'])
        if other is not None:
            raise StoreConflict(f'source {event["source_event_id"]} already recorded as {other}')
        r = _transition_checked(state, event)
        cur.execute(f'UPDATE {RECEIPTS_TABLE} SET status = %s, output_hash = %s, verification = %s, ack_hash = %s, '
                    "acknowledged_at = now() WHERE operation_id = %s AND status = 'PREPARED'",
                    (status, receipt.get('output_hash'), _jsonb(receipt.get('verification') or {}), ack_hash,
                     str(operation_id)))
        if cur.rowcount != 1:
            raise StoreConflict(f'operation {operation_id} was acknowledged concurrently')
        if r['after'] != r['before']:
            _update_state(cur, state, r['after'])
        _insert_event(cur, event_id=event['event_id'], decision_id=stored['decision_id'], checkpoint='EVENT',
                      event_kind=event['kind'], source_event_id=event['source_event_id'],
                      request_hash=cs.request_hash(event), input_snapshot={'acknowledgement': receipt},
                      policy_snapshot={}, draw_hex=None, breakdown=None, before=r['before'], after=r['after'],
                      receipt=dict(r, replayed=False))
        return dict(stored, status=status, output_hash=receipt.get('output_hash'),
                    verification=receipt.get('verification') or {}, ack_hash=ack_hash, replayed=False,
                    state=r['after'])
