"""Immutable contract-policy receipts and factual event outbox (PERSONALITY_CONTRACT_POLICY_PLAN.md §2.5, U2).

Tables from db/migrations/2027_01_23_contract_personality_policy.sql. The caller owns the transaction: nothing here
commits, rolls back the outer transaction or connects; each write runs inside a savepoint so a failure leaves the
caller's transaction usable. Previews and dry runs never call this module (they calculate in memory).

  record_contract_decision(cur, receipt) -> dict    insert once; the same decision_id with the same input hash
                                                    returns the original (status 'replayed'); a changed hash raises
                                                    ContractStoreConflict. Unique races resolve by reread + hash.
  read_contract_decision(cur, universe_id, decision_id) -> dict | None
  enqueue_contract_events(cur, receipt, facts) -> [event_id]
                                                    factual events only (EVENT_KINDS); event_id is
                                                    '<decision_id>:<kind>[:<n>]', stable, deduped the same way.
  read_contract_activation(cur, universe_id) -> {config, source, families}
                                                    the policy config from the shared switchboard
                                                    (personality.features, activation plan D1); 'off' with source
                                                    'default' while the switchboard does not exist.

receipt = {universe_id, decision_id, season, week, stage, player_id, persona_id|null, revision|null, mode,
           policy_version, snapshot: dict, result: dict, input_hash?}. input_hash defaults to sha256 of the
canonical snapshot; a supplied one must match it.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import uuid

EVENT_KINDS = ('CONTRACT_OFFER_SUBMITTED', 'CONTRACT_RESPONSE_RECORDED', 'CONTRACT_EXECUTED',
               'CONTRACT_INSTRUMENT_APPLIED')
MODES = ('off', 'shadow', 'active')
DECISION_COLUMNS = ('universe_id', 'decision_id', 'season', 'week', 'stage', 'player_id', 'persona_id', 'revision',
                    'mode', 'policy_version', 'input_hash', 'snapshot', 'result')
EVENT_PAYLOAD_KEYS = ('event_id', 'source_decision_id', 'universe_id', 'player_persona_id', 'team_id',
                      'gm_persona_id', 'as_of', 'offer_id', 'terms', 'market_reference', 'response', 'execution_id')
FACT_KEYS = ('kind', 'team_id', 'gm_persona_id', 'offer_id', 'terms', 'market_reference', 'response',
             'execution_id')
SWITCHBOARD_FEATURES = {'ambition': 'contracts_ambition', 'relationship': 'contracts_relationships'}
EVENT_VERSION = 'contract-event-v1'


class ContractStoreError(ValueError):
    pass


class ContractStoreConflict(ContractStoreError):
    pass


def canonical_hash(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def _jsonb(value):
    from psycopg.types.json import Jsonb
    return Jsonb(value)


@contextmanager
def _savepoint(cur):
    name = 'contract_policy_' + uuid.uuid4().hex
    cur.execute(f'SAVEPOINT {name}')
    try:
        yield
    except BaseException:
        cur.execute(f'ROLLBACK TO SAVEPOINT {name}')
        cur.execute(f'RELEASE SAVEPOINT {name}')
        raise
    else:
        cur.execute(f'RELEASE SAVEPOINT {name}')


def _text(v, label):
    if not isinstance(v, str) or not v.strip():
        raise ContractStoreError(f'{label} must be nonempty text')
    return v


def _int(v, label, nullable=False):
    if v is None and nullable:
        return v
    if type(v) is not int:
        raise ContractStoreError(f'{label} must be an integer')
    return v


def validate_receipt(receipt):
    """A normalized copy with input_hash set; raises ContractStoreError on a malformed receipt."""
    if not isinstance(receipt, dict):
        raise ContractStoreError('receipt must be an object')
    extra = set(receipt) - set(DECISION_COLUMNS)
    missing = set(DECISION_COLUMNS) - {'input_hash'} - set(receipt)
    if extra or missing:
        raise ContractStoreError(f'receipt keys: missing {sorted(missing)}, unexpected {sorted(extra)}')
    r = deepcopy(receipt)
    for k in ('universe_id', 'decision_id', 'stage', 'policy_version'):
        _text(r[k], k)
    for k in ('season', 'week', 'player_id'):
        _int(r[k], k)
    if r['persona_id'] is not None:
        _text(r['persona_id'], 'persona_id')
    _int(r['revision'], 'revision', nullable=True)
    if r['mode'] not in MODES:
        raise ContractStoreError(f'mode must be one of {", ".join(MODES)}')
    for k in ('snapshot', 'result'):
        if not isinstance(r[k], dict):
            raise ContractStoreError(f'{k} must be an object')
    h = canonical_hash(r['snapshot'])
    if r.get('input_hash') not in (None, h):
        raise ContractStoreError('input_hash does not match the snapshot')
    r['input_hash'] = h
    return r


def _row_to_receipt(row):
    return dict(zip(DECISION_COLUMNS, row))


def read_contract_decision(cur, universe_id, decision_id):
    cur.execute(f'SELECT {", ".join(DECISION_COLUMNS)} FROM contract_policy_decisions '
                'WHERE universe_id = %s AND decision_id = %s', (universe_id, decision_id))
    row = cur.fetchone()
    return None if row is None else _row_to_receipt(row)


def record_contract_decision(cur, receipt):
    """Insert-once receipt. Returns the stored receipt plus status 'recorded' | 'replayed'."""
    r = validate_receipt(receipt)
    with _savepoint(cur):
        params = [r[c] for c in DECISION_COLUMNS]
        params[DECISION_COLUMNS.index('snapshot')] = _jsonb(r['snapshot'])
        params[DECISION_COLUMNS.index('result')] = _jsonb(r['result'])
        cur.execute(f'INSERT INTO contract_policy_decisions ({", ".join(DECISION_COLUMNS)}) '
                    f'VALUES ({", ".join(["%s"] * len(DECISION_COLUMNS))}) '
                    'ON CONFLICT (universe_id, decision_id) DO NOTHING RETURNING decision_id', params)
        inserted = cur.fetchone()
        if inserted is not None:
            return dict(r, status='recorded')
        stored = read_contract_decision(cur, r['universe_id'], r['decision_id'])
        if stored is None:
            raise ContractStoreError('receipt conflict without a stored row (concurrent delete?)')
        if stored['input_hash'] != r['input_hash']:
            raise ContractStoreConflict(f'decision {r["decision_id"]} already recorded with a different input hash')
        return dict(stored, status='replayed')


def event_payload(receipt, fact, event_id):
    """Payload v1 for one fact. No preview, LOWBALL, trust delta, promise breach or grudge event exists here."""
    if fact.get('kind') not in EVENT_KINDS:
        raise ContractStoreError(f'event kind must be one of {", ".join(EVENT_KINDS)}')
    extra = set(fact) - set(FACT_KEYS)
    if extra:
        raise ContractStoreError(f'unexpected fact keys {sorted(extra)}')
    snap = receipt['snapshot']
    return {'event_id': event_id, 'event_version': EVENT_VERSION, 'kind': fact['kind'],
            'source_decision_id': receipt['decision_id'], 'universe_id': receipt['universe_id'],
            'player_persona_id': receipt['persona_id'], 'team_id': fact.get('team_id'),
            'gm_persona_id': fact.get('gm_persona_id'),
            'as_of': snap.get('as_of') or {'season': receipt['season'], 'week': receipt['week'],
                                            'stage': receipt['stage']},
            'offer_id': fact.get('offer_id'), 'terms': fact.get('terms'),
            'market_reference': fact.get('market_reference'), 'response': fact.get('response'),
            'execution_id': fact.get('execution_id')}


def enqueue_contract_events(cur, receipt, facts):
    """Queue factual events for a recorded receipt; returns their event ids in order."""
    r = validate_receipt({k: v for k, v in receipt.items() if k in DECISION_COLUMNS})
    ids, seen = [], {}
    payloads = []
    for fact in facts:
        kind = fact.get('kind')
        n = seen.get(kind, 0)
        seen[kind] = n + 1
        eid = f'{r["decision_id"]}:{kind}' + (f':{n}' if n else '')
        payloads.append((eid, event_payload(r, fact, eid)))
    with _savepoint(cur):
        for eid, payload in payloads:
            cur.execute('INSERT INTO contract_policy_event_outbox (universe_id, event_id, decision_id, kind, payload) '
                        'VALUES (%s, %s, %s, %s, %s) ON CONFLICT (universe_id, event_id) DO NOTHING '
                        'RETURNING event_id', (r['universe_id'], eid, r['decision_id'], payload['kind'],
                                               _jsonb(payload)))
            if cur.fetchone() is None:
                cur.execute('SELECT payload FROM contract_policy_event_outbox WHERE universe_id = %s '
                            'AND event_id = %s', (r['universe_id'], eid))
                row = cur.fetchone()
                if row is None or canonical_hash(row[0]) != canonical_hash(payload):
                    raise ContractStoreConflict(f'event {eid} already queued with a different payload')
            ids.append(eid)
    return ids


def _default_config():
    from engines.personality_contract_policy import TRIAL_CONFIG
    return deepcopy(TRIAL_CONFIG)


def read_contract_activation(cur, universe_id):
    """{config: a policy config, source: 'default' | 'switchboard', families: {ambition, relationship: mode}}.

    The switchboard (personality.features.config(cur, universe_id, feature) -> {mode, version?, config?,
    review_note?} | None) is the activation plan's; until it exists every universe reads off. Import errors of the
    switchboard itself are the only thing treated as 'absent'; its storage errors propagate."""
    _text(universe_id, 'universe_id')
    cfg = _default_config()
    try:
        from personality import features
    except ImportError:
        return {'config': cfg, 'source': 'default', 'families': {'ambition': 'off', 'relationship': 'off'}}
    modes, notes = {}, {}
    for family, name in SWITCHBOARD_FEATURES.items():
        row = features.config(cur, universe_id, name) or {}
        modes[family] = row.get('mode') or 'off'
        if modes[family] not in MODES:
            raise ContractStoreError(f'switchboard mode for {name} must be one of {", ".join(MODES)}')
        cfg.update({k: v for k, v in (row.get('config') or {}).items()
                    if k not in ('mode', 'ambition_enabled', 'relationship_enabled')})
        if row.get('version'):
            cfg['version'] = row['version']
        notes[family] = row.get('review_note')
    # One mode per decision: the most active family's. A family in a lower mode is disabled for the decision
    # (shadowing relationships while ambition is active would otherwise apply them).
    order = {m: i for i, m in enumerate(MODES)}
    cfg['mode'] = max(modes.values(), key=order.get)
    cfg['ambition_enabled'] = modes['ambition'] == cfg['mode'] != 'off'
    cfg['relationship_enabled'] = modes['relationship'] == cfg['mode'] != 'off'
    from engines.personality_contract_policy import validate_contract_policy
    validate_contract_policy(cfg)
    return {'config': cfg, 'source': 'switchboard', 'families': modes, 'review_notes': notes}
