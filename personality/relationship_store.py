"""Relationship ledger persistence (docs/in_progress/PERSONALITY_RELATIONSHIPS_PLAN.md §2.6, unit U4).

Caller-owned transaction: an explicit psycopg cursor on a NON-autocommit connection. Nothing here connects,
commits or rolls back the caller's work; every append runs in its own savepoint under the transaction advisory
lock 'relationships:<universe>', so a failure leaves nothing of the batch behind and propagates.

  read_head(cur, universe_id) -> int                    0 before the first batch; batches count from 1
  append_events(cur, events, *, expected_head, team_registry) -> receipt
  read_events(cur, *, subject, as_of, known_through=None) -> [envelope + 'head']

append_events, in order:
  1. every envelope through relationship_events.validate_event (the trusted team_registry is the caller's;
     invalid JSON cannot establish a team) and every PersonRef against persona_core_profiles in its universe:
     failures are EXCLUDED into receipt['rejected'] ([{source_event_id, event_id, reason}]), not appended;
  2. lock, then an exact replay of an earlier batch (same batch_hash) returns that batch's stored receipt,
     before any head check;
  3. expected_head must equal the universe's head (StaleHeadError otherwise);
  4. a facet already stored with the same payload hash is a no-op (receipt['duplicates'] with its original
     head); the same facet with a different hash, a reused transport event_id with other content, or an
     external source ref already naming another occurrence refuses the whole batch (RelationshipConflictError);
  5. new facets, the batch row (head = expected_head + 1) and their source refs are written atomically.
A batch with nothing new writes nothing and keeps the head. Corrections are ordinary appended facts.

Receipt: {universe_id, status: 'appended'|'noop'|'rejected', head, expected_head, batch_hash, registry_hash,
appended: [{source_event_id, facet_key, event_id, payload_hash}], duplicates: [... + head],
rejected: [...], source_refs: [{namespace, key, source_event_id}], replayed: bool}.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import uuid

from psycopg.types.json import Jsonb

from personality import relationship_events as rev

EVENTS_TABLE = 'persona_relationship_events'
BATCHES_TABLE = 'persona_relationship_batches'
SOURCES_TABLE = 'persona_relationship_sources'


class RelationshipStoreError(RuntimeError):
    pass


class StaleHeadError(RelationshipStoreError):
    pass


class RelationshipConflictError(RelationshipStoreError):
    pass


@contextmanager
def _savepoint(cur):
    name = 'relstore_' + uuid.uuid4().hex
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
        raise RelationshipStoreError('append_events needs a cursor on a non-autocommit connection')


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False, default=str)


def _sha(value):
    return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _receipt_obj(value):
    return json.loads(value) if isinstance(value, str) else value


# ---------------------------------------------------------------------------------------------------- head ---

def read_head(cur, universe_id):
    rev._text(universe_id, 'universe_id')
    cur.execute(f'SELECT COALESCE(MAX(head), 0) FROM {BATCHES_TABLE} WHERE universe_id = %s', (universe_id,))
    row = cur.fetchone()
    return int(row[0] if not isinstance(row, dict) else next(iter(row.values())))


# ------------------------------------------------------------------------------------------------ identity ---

def _person_refs(value, out):
    if isinstance(value, dict):
        if value.get('kind') == 'person' and 'persona_id' in value:
            out.add((value['role'], value['persona_id']))
        for child in value.values():
            _person_refs(child, out)
    elif isinstance(value, list):
        for child in value:
            _person_refs(child, out)
    return out


def _known_identities(cur, universe_id, refs):
    if not refs:
        return set()
    roles, ids = zip(*sorted(refs))
    cur.execute('''SELECT persona_type, persona_id FROM persona_core_profiles
                   WHERE universe_id = %s AND (persona_type, persona_id) IN
                         (SELECT r, p FROM unnest(%s::text[], %s::text[]) AS k(r, p))''',
                (universe_id, list(roles), list(ids)))
    return {(r[0], r[1]) for r in cur.fetchall()}


# -------------------------------------------------------------------------------------------------- append ---

def _admit(cur, events, team_registry):
    """(universe_id, admitted [(envelope, facet, hash)], rejected [...]) after validation and identity checks."""
    if not isinstance(events, list):
        raise RelationshipStoreError('events must be a list')
    admitted, rejected = [], []
    for raw in events:
        sid = raw.get('source_event_id') if isinstance(raw, dict) else None
        eid = raw.get('event_id') if isinstance(raw, dict) else None
        try:
            if isinstance(raw, dict) and 'head' in raw:
                raise rev.RelationshipEventError('invalid_field', 'head is assigned by the store', 'head')
            ev = rev.validate_event(raw, team_registry=team_registry)
        except rev.RelationshipEventError as e:
            rejected.append({'source_event_id': sid, 'event_id': eid, 'reason': str(e)})
            continue
        admitted.append(ev)
    universes = sorted({ev['universe_id'] for ev in admitted})
    if len(universes) > 1:
        raise RelationshipStoreError(f'one batch belongs to one universe, got {universes}')
    universe_id = universes[0] if universes else None
    if universe_id is None:
        return None, [], rejected
    refs = set()
    for ev in admitted:
        _person_refs(ev, refs)
    known = _known_identities(cur, universe_id, refs)
    out = []
    for ev in admitted:
        missing = sorted(f'{r}:{p}' for r, p in _person_refs(ev, set()) - known)
        if missing:
            rejected.append({'source_event_id': ev['source_event_id'], 'event_id': ev['event_id'],
                             'reason': 'unknown_identity: ' + ', '.join(missing)})
            continue
        out.append((ev, rev.facet_key(ev), rev.canonical_payload_hash(ev)))
    return universe_id, out, rejected


def _dedupe_batch(admitted):
    seen, by_event, out = {}, {}, []
    for ev, facet, digest in admitted:
        key = (ev['source_event_id'], facet)
        if key in seen:
            if seen[key] != digest:
                raise RelationshipConflictError(f"{ev['source_event_id']} has two different facts for one facet")
            continue
        prior = by_event.get((ev['event_id'], facet))
        if prior is not None and prior != (ev['source_event_id'], digest):
            raise RelationshipConflictError(f"event_id {ev['event_id']!r} reused for different content")
        seen[key] = digest
        by_event[(ev['event_id'], facet)] = (ev['source_event_id'], digest)
        out.append((ev, facet, digest))
    try:
        rev.source_ref_map([ev for ev, _, _ in out])
    except rev.RelationshipEventError as e:
        raise RelationshipConflictError(str(e)) from e
    return out


def _existing(cur, universe_id, items):
    sids, facets, eids = [ev['source_event_id'] for ev, _, _ in items], [f for _, f, _ in items], \
        [ev['event_id'] for ev, _, _ in items]
    cur.execute(f'''SELECT source_event_id, facet_key, event_id, payload_hash, head FROM {EVENTS_TABLE}
                    WHERE universe_id = %s AND ((source_event_id, facet_key) IN
                          (SELECT s, f FROM unnest(%s::text[], %s::text[]) AS k(s, f))
                       OR (event_id, facet_key) IN (SELECT e, f FROM unnest(%s::text[], %s::text[]) AS k(e, f)))''',
                (universe_id, sids, facets, eids, facets))
    by_facet, by_event = {}, {}
    for sid, facet, eid, digest, head in cur.fetchall():
        by_facet[(sid, facet)] = (eid, digest, head)
        by_event[(eid, facet)] = (sid, digest)
    return by_facet, by_event


def _existing_sources(cur, universe_id, refs):
    if not refs:
        return {}
    ns, keys = zip(*refs)
    cur.execute(f'''SELECT namespace, external_key, source_event_id FROM {SOURCES_TABLE}
                    WHERE universe_id = %s AND (namespace, external_key) IN
                          (SELECT n, k FROM unnest(%s::text[], %s::text[]) AS r(n, k))''',
                (universe_id, list(ns), list(keys)))
    return {(r[0], r[1]): r[2] for r in cur.fetchall()}


def append_events(cur, events, *, expected_head, team_registry):
    """Append one batch of canonical envelopes for one universe. See the module docstring for the contract."""
    _require_transaction(cur)
    rev.validate_team_registry(team_registry)
    if isinstance(expected_head, bool) or not isinstance(expected_head, int) or expected_head < 0:
        raise RelationshipStoreError('expected_head must be an int >= 0')
    universe_id, admitted, rejected = _admit(cur, events, team_registry)
    reg_hash = rev.registry_hash(team_registry)
    batch_hash = _sha({'events': events, 'registry_hash': reg_hash})
    base = {'universe_id': universe_id, 'expected_head': expected_head, 'batch_hash': batch_hash,
            'registry_hash': reg_hash, 'appended': [], 'duplicates': [], 'rejected': rejected, 'source_refs': [],
            'replayed': False}
    if not admitted:                                        # nothing valid: write nothing, keep the head
        return dict(base, status='rejected', head=None)
    items = _dedupe_batch(admitted)
    with _savepoint(cur):
        cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', ('relationships:' + universe_id,))
        cur.execute(f'SELECT receipt FROM {BATCHES_TABLE} WHERE universe_id = %s AND batch_hash = %s',
                    (universe_id, batch_hash))
        row = cur.fetchone()
        if row is not None:
            return dict(_receipt_obj(row[0]), replayed=True)
        head = read_head(cur, universe_id)
        if head != expected_head:
            raise StaleHeadError(f'relationships head for {universe_id} is {head}, caller expected {expected_head}')
        by_facet, by_event = _existing(cur, universe_id, items)
        new, duplicates = [], []
        for ev, facet, digest in items:
            prior = by_facet.get((ev['source_event_id'], facet))
            if prior is not None:
                if prior[1] != digest:
                    raise RelationshipConflictError(
                        f"{ev['source_event_id']} facet {facet} is stored with different content; append a correction")
                duplicates.append({'source_event_id': ev['source_event_id'], 'facet_key': facet,
                                   'event_id': prior[0], 'payload_hash': digest, 'head': prior[2]})
                continue
            other = by_event.get((ev['event_id'], facet))
            if other is not None and other != (ev['source_event_id'], digest):
                raise RelationshipConflictError(f"event_id {ev['event_id']!r} is stored for other content")
            new.append((ev, facet, digest))
        refs = {}
        for ev, _, _ in new:
            for ref in ev['source_refs']:
                refs[(ref['namespace'], ref['key'])] = ev['source_event_id']
        stored = _existing_sources(cur, universe_id, list(refs))
        for pair, sid in sorted(refs.items()):
            if stored.get(pair, sid) != sid:
                raise RelationshipConflictError(f'{pair[0]}:{pair[1]} already names {stored[pair]}, not {sid}')
        receipt = dict(base, duplicates=duplicates,
                       appended=[{'source_event_id': ev['source_event_id'], 'facet_key': f, 'event_id': ev['event_id'],
                                  'payload_hash': d} for ev, f, d in new],
                       source_refs=[{'namespace': n, 'key': k, 'source_event_id': s}
                                    for (n, k), s in sorted(refs.items())])
        if not new:
            return dict(receipt, status='noop', head=head)
        head += 1
        receipt.update(status='appended', head=head)
        cur.execute(f'''INSERT INTO {BATCHES_TABLE} (universe_id, head, expected_head, batch_hash, receipt)
                        VALUES (%s, %s, %s, %s, %s)''',
                    (universe_id, head, expected_head, batch_hash, Jsonb(receipt)))
        for ev, facet, digest in new:
            cur.execute(f'''INSERT INTO {EVENTS_TABLE} (universe_id, source_event_id, facet_key, event_id, head,
                                occurred_season, occurred_tick, schema_version, payload_hash, envelope)
                            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)''',
                        (universe_id, ev['source_event_id'], facet, ev['event_id'], head,
                         ev['occurred_at']['season'], ev['occurred_at']['tick'], ev['schema_version'], digest,
                         Jsonb(ev)))
        for (namespace, key), sid in sorted(refs.items()):
            if (namespace, key) in stored:
                continue
            cur.execute(f'''INSERT INTO {SOURCES_TABLE} (universe_id, namespace, external_key, source_event_id)
                            VALUES (%s, %s, %s, %s)''', (universe_id, namespace, key, sid))
        return deepcopy(receipt)


# ---------------------------------------------------------------------------------------------------- read ---

def read_events(cur, *, subject, as_of, known_through=None):
    """The subject's stored facts (corrections included) that occurred by `as_of` and were ingested by
    `known_through` (a head; None = everything), each with its ingestion `head`, in replay order. Pure reads:
    never initializes a profile; schema or corruption errors propagate."""
    if not isinstance(subject, dict) or subject.get('kind') != 'person':
        raise RelationshipStoreError('subject must be a PersonRef')
    as_of = rev.time_point(as_of, 'as_of')
    if known_through is not None and (isinstance(known_through, bool) or not isinstance(known_through, int)):
        raise RelationshipStoreError('known_through must be an int head or None')
    cur.execute(f'''SELECT envelope, head FROM {EVENTS_TABLE}
                    WHERE universe_id = %s
                      AND envelope->'subject'->>'role' = %s AND envelope->'subject'->>'persona_id' = %s
                      AND (occurred_season, occurred_tick) <= (%s, %s)
                      AND (%s::bigint IS NULL OR head <= %s::bigint)
                    ORDER BY occurred_season, occurred_tick, head, source_event_id, facet_key''',
                (subject['universe_id'], subject['role'], subject['persona_id'], as_of['season'], as_of['tick'],
                 known_through, known_through))
    out = []
    for envelope, head in cur.fetchall():
        ev = _receipt_obj(envelope)
        ev = dict(ev, head=int(head))
        out.append(ev)
    out.sort(key=rev.order_key)
    return out
