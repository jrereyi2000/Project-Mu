"""Player trade-request persistence (PERSONALITY_TRADE_REQUESTS_PLAN.md §2.6, U2).

Tables from db/migrations/2027_01_25_player_trade_requests.sql. Caller-owned transaction: an explicit psycopg
cursor on a NON-autocommit connection. Nothing here connects, commits, seeds or migrates; each write runs in its own
savepoint under the transaction advisory lock for 'trade-requests:<universe>:<persona>', so a failure leaves nothing
behind and propagates, and parallel annual claims for one persona serialize (unique constraints are the final
defense).

  read_active(cur, universe_id, persona_id) -> RequestEpisode | None
  read_annual(cur, universe_id, persona_id, season) -> AnnualDecision | None
  record_decision(cur, context, clock, policy, *, mode) -> receipt dict
  apply_action(cur, request_id, action, context, clock, policy) -> receipt dict
  read_receipt(cur, universe_id, persona_id, event_id) -> receipt dict | None

Only ENABLED mode with an explicitly approved policy (never `offline-illustration-v1`) writes. SHADOW persists
nowhere (StoreError); DISABLED returns the no-op decision without touching the database. No live caller is
delivered with this module.

Order in both writers: lock -> verified binding (a locked read of persona_core_profiles: the persona must be a
stored `player` profile of this universe) -> exact replay lookup by event ID BEFORE fresh revision validation
(same payload hash returns the original receipt, even after later changes; a changed payload is a conflict) ->
rewind check -> evaluate/transition (engines.player_trade_requests) -> append the receipt and update state
atomically. A review's event ID is 'review:<season>:<window_id>:<checkpoint_id>', so every checkpoint review, no-op
and deferred ones included, is recorded once; renaming a checkpoint cannot buy another draw because the annual
decision row is unique per (universe, persona, season).

Rollback (plan §2.6): disable the feature; keep and export these ledgers. Never reverse real trades or contracts.
A franchise rewind must restore identity, events and these ledgers from one checkpoint: a stored episode or
annual decision dated after the clock blocks evaluation (MixedRewindError).
"""
from contextlib import contextmanager
import hashlib
import json
import uuid

from engines import player_trade_requests as tr

DECISIONS_TABLE = 'player_trade_request_decisions'
REQUESTS_TABLE = 'player_trade_requests'
ACTIONS_TABLE = 'player_trade_request_actions'


class StoreError(RuntimeError):
    pass


class StoreConflict(StoreError):
    """Conflicting replay, stale revision or a lost race."""


class BindingError(StoreError):
    """The persona is not a stored player profile of this universe."""


class MixedRewindError(StoreError):
    """Stored request ledgers are newer than the clock: identity/events/ledgers came from different checkpoints."""


def _jsonb(value):
    from psycopg.types.json import Jsonb
    return Jsonb(value)


@contextmanager
def _savepoint(cur):
    name = 'trade_requests_' + uuid.uuid4().hex
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
        raise StoreError('trade-request writes need a cursor on a non-autocommit connection')


def lock_key(universe_id, persona_id):
    """Signed 64-bit advisory lock key for one universe/persona."""
    digest = hashlib.sha256(f'trade-requests:{universe_id}:{persona_id}'.encode('utf-8')).digest()
    return int.from_bytes(digest[:8], 'big', signed=True)


def _lock(cur, universe_id, persona_id):
    cur.execute('SELECT pg_advisory_xact_lock(%s)', (lock_key(universe_id, persona_id),))
    cur.fetchone()


def _verify_binding(cur, universe_id, persona_id):
    cur.execute("SELECT universe_id FROM persona_core_profiles WHERE persona_type = 'player' AND persona_id = %s "
                'FOR SHARE', (persona_id,))
    row = cur.fetchone()
    if row is None:
        raise BindingError(f'{persona_id} is not a stored player profile (this feature never initializes one)')
    if row[0] != universe_id:
        raise BindingError(f'{persona_id} belongs to another universe')


def _loads(value):
    return json.loads(value) if isinstance(value, (str, bytes)) else value


# ------------------------------------------------------------------------------------------------ reads ---

def read_active(cur, universe_id, persona_id):
    cur.execute(f"SELECT state FROM {REQUESTS_TABLE} WHERE universe_id = %s AND persona_id = %s "
                "AND status = 'ACTIVE'", (universe_id, persona_id))
    row = cur.fetchone()
    return None if row is None else tr.episode_from_dict(_loads(row[0]))


def _read_episode(cur, request_id):
    cur.execute(f'SELECT state FROM {REQUESTS_TABLE} WHERE request_id = %s FOR UPDATE', (str(request_id),))
    row = cur.fetchone()
    return None if row is None else tr.episode_from_dict(_loads(row[0]))


def read_annual(cur, universe_id, persona_id, season):
    cur.execute(f'SELECT receipt FROM {DECISIONS_TABLE} WHERE universe_id = %s AND persona_id = %s AND season = %s',
                (universe_id, persona_id, season))
    row = cur.fetchone()
    return None if row is None else tr.annual_from_dict(_loads(row[0])['annual'])


def read_receipt(cur, universe_id, persona_id, event_id):
    cur.execute(f'SELECT receipt FROM {ACTIONS_TABLE} WHERE universe_id = %s AND persona_id = %s AND event_id = %s',
                (universe_id, persona_id, event_id))
    row = cur.fetchone()
    return None if row is None else _loads(row[0])


def _stored_action(cur, universe_id, persona_id, event_id):
    cur.execute(f'SELECT payload_hash, receipt FROM {ACTIONS_TABLE} '
                'WHERE universe_id = %s AND persona_id = %s AND event_id = %s', (universe_id, persona_id, event_id))
    row = cur.fetchone()
    return None if row is None else (row[0], _loads(row[1]))


def _source_reused(cur, universe_id, persona_id, source_event_id, action_kind):
    cur.execute(f'SELECT event_id FROM {ACTIONS_TABLE} WHERE universe_id = %s AND persona_id = %s '
                'AND source_event_id = %s AND action_kind = %s',
                (universe_id, persona_id, source_event_id, action_kind))
    row = cur.fetchone()
    return None if row is None else row[0]


# ----------------------------------------------------------------------------------------------- writes ---

def _insert_action(cur, *, universe_id, persona_id, event_id, source_event_id, request_id, clock, action_kind,
                   payload_hash, before, after, receipt):
    cur.execute(
        f'INSERT INTO {ACTIONS_TABLE} (universe_id, persona_id, event_id, source_event_id, request_id, season, '
        'window_id, checkpoint_id, action_kind, payload_hash, before_state, after_state, receipt) '
        'VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
        (universe_id, persona_id, event_id, source_event_id, request_id, clock.season, clock.window_id,
         clock.checkpoint_id, action_kind, payload_hash,
         None if before is None else _jsonb(tr.to_dict(before)),
         None if after is None else _jsonb(tr.to_dict(after)), _jsonb(receipt)))


def _replay(cur, universe_id, persona_id, event_id, payload_hash):
    stored = _stored_action(cur, universe_id, persona_id, event_id)
    if stored is None:
        return None
    if stored[0] != payload_hash:
        raise StoreConflict(f'event {event_id} was already recorded with a different payload')
    return dict(stored[1], replayed=True, status='replayed')


def review_event_id(clock):
    return f'review:{clock.season}:{clock.window_id}:{clock.checkpoint_id}'


def record_decision(cur, context, clock, policy, *, mode):
    """One persisted checkpoint review (ENABLED only). Returns {status: 'recorded'|'replayed', replayed, event_id,
    decision: RequestDecision as a dict}."""
    tr._enum(mode, tr.MODES, 'mode')
    if mode == 'DISABLED':
        d = tr.evaluate_request(context, clock, None, None, policy, mode='DISABLED')
        return {'status': 'noop', 'replayed': False, 'event_id': None, 'decision': tr.to_dict(d)}
    if mode == 'SHADOW':
        raise StoreError('SHADOW decisions persist nowhere; use engines.player_trade_requests.evaluate_request')
    tr.validate_policy(policy, enabled=True)
    tr.validate_request_context(context, clock)
    _require_transaction(cur)
    universe_id, persona_id = clock.universe_id, context.persona_id
    event_id = review_event_id(clock)
    payload_hash = tr.canonical_hash({'kind': 'REVIEW', 'context': context, 'clock': clock, 'policy': policy,
                                      'mode': mode})
    with _savepoint(cur):
        _lock(cur, universe_id, persona_id)
        _verify_binding(cur, universe_id, persona_id)
        replay = _replay(cur, universe_id, persona_id, event_id, payload_hash)
        if replay is not None:
            return replay
        other = _source_reused(cur, universe_id, persona_id, clock.source_event_id, 'REVIEW')
        if other is not None:
            raise StoreConflict(f'source {clock.source_event_id} already reviewed as {other}')
        prior = read_active(cur, universe_id, persona_id)
        annual = read_annual(cur, universe_id, persona_id, clock.season)
        if prior is not None and prior.last_review_ordinal > clock.ordinal:
            raise MixedRewindError('the active request is newer than the clock')
        if annual is not None and annual.ordinal > clock.ordinal:
            raise MixedRewindError("this season's annual decision is newer than the clock")
        d = tr.evaluate_request(context, clock, prior, annual, policy, mode=mode)
        if d.annual_slot_consumed:
            a = d.annual
            cur.execute(
                f'INSERT INTO {DECISIONS_TABLE} (universe_id, persona_id, season, decision_id, outcome, input_hash, '
                'policy_version, seed_algorithm, draw, receipt) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)',
                (a.universe_id, a.persona_id, a.season, a.decision_id, a.outcome, a.input_hash, a.policy_version,
                 a.seed_algorithm, a.draw, _jsonb({'annual': tr.to_dict(a), 'decision': tr.to_dict(d)})))
        if d.outcome == 'REQUEST':
            ep = d.after
            cur.execute(
                f'INSERT INTO {REQUESTS_TABLE} (request_id, decision_id, universe_id, persona_type, persona_id, '
                "team_id, opened_season, revision, status, state) VALUES (%s, %s, %s, 'player', %s, %s, %s, %s, %s, %s)",
                (ep.request_id, ep.decision_id, ep.universe_id, ep.persona_id, ep.team_id, ep.opened_season,
                 ep.revision, ep.status, _jsonb(tr.to_dict(ep))))
        receipt = {'status': 'recorded', 'replayed': False, 'event_id': event_id, 'decision': tr.to_dict(d)}
        request_id = d.after.request_id if d.after is not None else None
        _insert_action(cur, universe_id=universe_id, persona_id=persona_id, event_id=event_id,
                       source_event_id=clock.source_event_id, request_id=request_id, clock=clock,
                       action_kind='REVIEW', payload_hash=payload_hash, before=d.before, after=d.after,
                       receipt=receipt)
        return receipt


def apply_action(cur, request_id, action, context, clock, policy):
    """Apply one lifecycle action to a stored episode (ENABLED policy only). Returns {status: 'recorded'|
    'replayed', replayed, event_id, transition: TransitionReceipt as a dict}."""
    tr.validate_policy(policy, enabled=True)
    tr._validate_action(action)
    tr.validate_request_context(context, clock)
    _require_transaction(cur)
    universe_id, persona_id = clock.universe_id, context.persona_id
    payload_hash = tr.canonical_hash({'kind': 'ACTION', 'request_id': str(request_id), 'action': action,
                                      'context': context, 'clock': clock, 'policy': policy})
    with _savepoint(cur):
        _lock(cur, universe_id, persona_id)
        _verify_binding(cur, universe_id, persona_id)
        replay = _replay(cur, universe_id, persona_id, action.event_id, payload_hash)
        if replay is not None:
            return replay
        other = _source_reused(cur, universe_id, persona_id, action.source_event_id, action.kind)
        if other is not None:
            raise StoreConflict(f'source {action.source_event_id} already used for {action.kind} by {other}')
        prior = _read_episode(cur, request_id)
        if prior is None:
            raise StoreError(f'no request {request_id}')
        if prior.universe_id != universe_id or prior.persona_id != persona_id:
            raise BindingError('the request belongs to another universe or persona')
        if prior.last_review_ordinal > clock.ordinal:
            raise MixedRewindError('the stored request is newer than the clock')
        try:
            t = tr.transition_request(prior, action, context, clock, policy)
        except tr.TradeRequestConflict as e:
            raise StoreConflict(str(e)) from e
        if t.changed:
            after = t.after
            cur.execute(f'UPDATE {REQUESTS_TABLE} SET revision = %s, status = %s, state = %s, updated_at = now() '
                        'WHERE request_id = %s AND revision = %s',
                        (after.revision, after.status, _jsonb(tr.to_dict(after)), str(request_id), prior.revision))
            if cur.rowcount != 1:
                raise StoreConflict(f'request {request_id} changed concurrently')
        receipt = {'status': 'recorded', 'replayed': False, 'event_id': action.event_id, 'transition': tr.to_dict(t)}
        _insert_action(cur, universe_id=universe_id, persona_id=persona_id, event_id=action.event_id,
                       source_event_id=action.source_event_id, request_id=str(request_id), clock=clock,
                       action_kind=action.kind, payload_hash=payload_hash, before=t.before, after=t.after,
                       receipt=receipt)
        return receipt
