"""Persistence for the relationship event producers (RELATIONSHIP_EVENT_PRODUCERS_PLAN.md §2.3–§2.4).

The caller owns the transaction: nothing here connects, commits or rolls back the caller's work. Every write
batch runs inside its own savepoint, so a failure part-way through leaves nothing of that batch behind and the
error propagates to the caller.

Envelopes arrive from rules.py with a producer-side `subject_alias` ({role, namespace, external_id}) and a
`subject` PersonRef whose `universe_id` and `persona_id` are still None. `append_or_hold` resolves the alias
through personality.store.resolve_alias; resolved envelopes go in one call to
personality.relationship_store.append_events, and unresolved ones wait in relationship_pending_events until
`flush_pending` finds an identity for them.

Rejections. append_events EXCLUDES envelopes it cannot admit into receipt['rejected'] ({source_event_id,
event_id, reason}) and appends the rest. Those are never counted as appended: an `unknown_identity` rejection (the
alias resolved but the core profile is not there yet) is held PENDING like an unresolved subject, so a later flush
retries it; any other rejection (the envelope breaks the relationships contract) is stored DROPPED with the reason,
so it is reported once and never retried. Both keep their key in `event_hashes`.

Event keys. Each envelope's occurrence key is "<source_event_id>|<facet_key>", where facet_key is the
envelope's own `facet_key` when it has one, otherwise "<role>:<namespace>:<external_id>><target>><kind>"
(target = team_key, a target alias or "-"). The run's `event_hashes` maps that key to
{"hash": <producer payload hash>, "ref": <the header needed to retract it later>}. The hash ignores
event_id, universe_id and persona_id, so it does not change when an identity appears.

Corrections. On a re-ingest the caller passes `corrections(previous_event_hashes, current)` together with the
current envelopes whose key is new or unchanged (never the changed ones themselves: their correction carries
them as `replacement_event`). `append_or_hold`'s returned `event_hashes` then covers every key in effect,
with a correction counted under the key it replaces and a retraction removing its key, and is what
`record_run` stores.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import importlib
import json
import uuid

from psycopg.types.json import Jsonb

SOURCES = ('SAVE', 'CATCHUP')
RUN_STATUSES = ('APPENDED', 'HELD', 'NOOP', 'FAILED')
PRODUCER_KEYS = ('subject_alias', 'target_alias', 'facet_key', 'supersedes_facet_key')
RELATIONSHIP_STORE = 'personality.relationship_store'

_RUN_COLUMNS = ('league_id', 'season', 'tick', 'rules_version', 'source', 'input_hash', 'registry_hash',
                'event_hashes', 'counts', 'status', 'error', 'created_at')
_REF_FIELDS = ('schema_version', 'source_kind', 'kind', 'occurred_at', 'subject_alias', 'target_alias',
               'subject', 'target', 'visibility')


@contextmanager
def _savepoint(cur):
    name = 'relprod_' + uuid.uuid4().hex
    cur.execute(f'SAVEPOINT {name}')
    try:
        yield
    except BaseException:
        cur.execute(f'ROLLBACK TO SAVEPOINT {name}')
        cur.execute(f'RELEASE SAVEPOINT {name}')
        raise
    else:
        cur.execute(f'RELEASE SAVEPOINT {name}')


def _col(row, index, name):
    return row[name] if isinstance(row, dict) else row[index]


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False, default=str)


def _relationship_store():
    """The relationships plan's store module, or None when it is not built yet (imported lazily)."""
    try:
        return importlib.import_module(RELATIONSHIP_STORE)
    except ModuleNotFoundError as exc:
        if exc.name in (RELATIONSHIP_STORE, 'personality'):
            return None
        raise


# ---------------------------------------------------------------- keys and hashes

def _alias(value, label):
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be an object')
    for field in ('role', 'namespace', 'external_id'):
        if not isinstance(value.get(field), str) or not value[field]:
            raise ValueError(f'{label}.{field} must be a non-empty string')
    return value


def _alias_token(alias):
    return f"{alias['role']}:{alias['namespace']}:{alias['external_id']}"


def _target_token(envelope):
    if envelope.get('target_alias') is not None:
        return _alias_token(_alias(envelope['target_alias'], 'target_alias'))
    target = envelope.get('target')
    if isinstance(target, dict):
        if target.get('team_key'):
            return str(target['team_key'])
        if target.get('persona_id'):
            return str(target['persona_id'])
    return '-'


def _facet_key(envelope):
    if envelope.get('facet_key'):
        return str(envelope['facet_key'])
    alias = _alias(envelope.get('subject_alias'), 'subject_alias')
    return f"{_alias_token(alias)}>{_target_token(envelope)}>{envelope['kind']}"


def _check(envelope):
    if not isinstance(envelope, dict):
        raise ValueError('envelope must be an object')
    if not isinstance(envelope.get('source_event_id'), str) or not envelope['source_event_id']:
        raise ValueError('envelope.source_event_id must be a non-empty string')
    if not isinstance(envelope.get('kind'), str) or not envelope['kind']:
        raise ValueError('envelope.kind must be a non-empty string')
    _alias(envelope.get('subject_alias'), 'subject_alias')
    return envelope


def _event_key(envelope):
    return f"{envelope['source_event_id']}|{_facet_key(envelope)}"


def _strip_identity(value):
    if isinstance(value, dict):
        return {k: _strip_identity(v) for k, v in value.items() if k not in ('universe_id', 'persona_id')}
    if isinstance(value, list):
        return [_strip_identity(v) for v in value]
    return value


def _producer_view(envelope):
    """The envelope without producer-only keys or the transport event_id."""
    return {k: v for k, v in envelope.items() if k not in PRODUCER_KEYS and k != 'event_id'}


def _envelope_hash(envelope):
    return hashlib.sha256(_canonical(_strip_identity(_producer_view(envelope))).encode()).hexdigest()


def _ref(envelope):
    ref = {field: deepcopy(envelope.get(field)) for field in _REF_FIELDS if envelope.get(field) is not None}
    ref['source_event_id'] = envelope['source_event_id']
    return _strip_identity(ref) | {'subject_alias': deepcopy(envelope['subject_alias'])}


def _hash_entries(envelopes):
    """event_hashes entries for a batch: corrections count under the key they replace, retractions remove it."""
    entries, retracted = {}, []
    for env in envelopes:
        if env['kind'] == 'correction' and env.get('supersedes_facet_key'):
            replacement = (env.get('payload') or {}).get('replacement_event')
            original = (env.get('payload') or {}).get('original_source_event_id') or env['supersedes_source_event_id']
            key = f"{original}|{env['supersedes_facet_key']}"
            if replacement is None:
                retracted.append(key)
                entries.pop(key, None)
            else:
                aliases = {k: env[k] for k in ('subject_alias', 'target_alias') if env.get(k) is not None}
                ref = _ref(replacement | aliases) | {'active_source_event_id': env['source_event_id']}
                entries[key] = {'hash': _envelope_hash(replacement), 'ref': ref}
        else:
            entries[_event_key(env)] = {'hash': _envelope_hash(env), 'ref': _ref(env)}
    return entries, retracted


# ---------------------------------------------------------------- resolution

def _fill_universe(value, universe_id):
    if isinstance(value, dict):
        if value.get('kind') in ('person', 'team') and 'universe_id' in value and value['universe_id'] is None:
            value['universe_id'] = universe_id
        for child in value.values():
            _fill_universe(child, universe_id)
    elif isinstance(value, list):
        for child in value:
            _fill_universe(child, universe_id)


def _resolved(envelope, universe_id, subject_id, target_id):
    """Relationships-plan envelope: producer keys stripped, universe and persona IDs filled."""
    out = {k: deepcopy(v) for k, v in envelope.items() if k not in PRODUCER_KEYS}
    out['universe_id'] = universe_id
    _fill_universe(out, universe_id)
    out['subject']['persona_id'] = subject_id.persona_id
    if target_id is not None:
        out['target']['persona_id'] = target_id.persona_id
    payload = out.get('payload')
    if isinstance(payload, dict) and isinstance(payload.get('replacement_event'), dict):
        payload['replacement_event'] = _resolved(payload['replacement_event'], universe_id, subject_id, target_id)
    return out


def _resolve(cur, envelope, universe_id):
    """(resolved envelope, None) or (None, hold reason)."""
    from personality import store as persona_store
    alias = envelope['subject_alias']
    subject_id = persona_store.resolve_alias(cur, universe_id, alias['role'], alias['namespace'],
                                             alias['external_id'])
    if subject_id is None:
        return None, 'subject_unresolved'
    target_id = None
    if envelope.get('target_alias') is not None:
        alias = envelope['target_alias']
        target_id = persona_store.resolve_alias(cur, universe_id, alias['role'], alias['namespace'],
                                                alias['external_id'])
        if target_id is None:
            return None, 'target_unresolved'
    return _resolved(envelope, universe_id, subject_id, target_id), None


def _hold_reason(universe_id):
    if universe_id is None:
        return 'universe_unregistered', None
    rel_store = _relationship_store()
    if rel_store is None:
        return 'relationship_store_missing', None
    return None, rel_store


def _append(cur, rel_store, events, universe_id, team_registry):
    head = rel_store.read_head(cur, universe_id)
    return rel_store.append_events(cur, events, expected_head=head, team_registry=team_registry)


# ---------------------------------------------------------------- runs and observations

def read_run(cur, league_id, season, tick, rules_version):
    cur.execute(f'''SELECT {", ".join(_RUN_COLUMNS)} FROM relationship_producer_runs
                    WHERE league_id=%s AND season=%s AND tick=%s AND rules_version=%s''',
                (league_id, season, tick, rules_version))
    row = cur.fetchone()
    if row is None:
        return None
    return {name: _col(row, i, name) for i, name in enumerate(_RUN_COLUMNS)}


def record_run(cur, run):
    """Insert or replace the run row for (league_id, season, tick, rules_version)."""
    for field in ('league_id', 'season', 'tick', 'rules_version', 'source', 'input_hash', 'registry_hash',
                  'event_hashes', 'counts', 'status'):
        if run.get(field) is None:
            raise ValueError(f'run.{field} is required')
    if not 1 <= int(run['tick']) <= 32:
        raise ValueError('run.tick must be between 1 and 32')
    if run['source'] not in SOURCES:
        raise ValueError(f'run.source must be one of {SOURCES}')
    if run['status'] not in RUN_STATUSES:
        raise ValueError(f'run.status must be one of {RUN_STATUSES}')
    if not isinstance(run['event_hashes'], dict) or not isinstance(run['counts'], dict):
        raise ValueError('run.event_hashes and run.counts must be objects')
    with _savepoint(cur):
        cur.execute('''INSERT INTO relationship_producer_runs
            (league_id,season,tick,rules_version,source,input_hash,registry_hash,event_hashes,counts,status,error)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (league_id,season,tick,rules_version) DO UPDATE SET
              source=EXCLUDED.source, input_hash=EXCLUDED.input_hash, registry_hash=EXCLUDED.registry_hash,
              event_hashes=EXCLUDED.event_hashes, counts=EXCLUDED.counts, status=EXCLUDED.status,
              error=EXCLUDED.error, created_at=now()''',
            (run['league_id'], run['season'], run['tick'], run['rules_version'], run['source'],
             run['input_hash'], run['registry_hash'], Jsonb(run['event_hashes']), Jsonb(run['counts']),
             run['status'], run.get('error')))


def save_observations(cur, league_id, season, tick, rows, source):
    """Replace the tick's roster observation with `rows` ({player_id, team_id or None}); returns rows written."""
    if source not in SOURCES:
        raise ValueError(f'source must be one of {SOURCES}')
    players, teams = [], []
    for row in rows:
        player_id, team_id = row.get('player_id'), row.get('team_id')
        if type(player_id) is not int or (team_id is not None and type(team_id) is not int):
            raise ValueError('observation player_id must be an int and team_id an int or None')
        players.append(player_id)
        teams.append(team_id)
    if len(set(players)) != len(players):
        raise ValueError('duplicate player_id in observations')
    with _savepoint(cur):
        cur.execute('''DELETE FROM relationship_roster_observations
                       WHERE league_id=%s AND season=%s AND tick=%s AND NOT (player_id = ANY(%s::int[]))''',
                    (league_id, season, tick, players))
        if players:
            cur.execute('''INSERT INTO relationship_roster_observations
                (league_id,season,tick,player_id,team_id,source)
                SELECT %s,%s,%s,p,t,%s FROM unnest(%s::int[], %s::int[]) AS o(p,t)
                ON CONFLICT (league_id,season,tick,player_id) DO UPDATE SET
                  team_id=EXCLUDED.team_id, source=EXCLUDED.source''',
                (league_id, season, tick, source, players, teams))
    return len(players)


# ---------------------------------------------------------------- append, hold, flush

def _insert_holds(cur, league_id, holds, status='PENDING'):
    """Idempotent on (league_id, source_event_id, facet_key); returns how many rows were new."""
    if not holds:
        return 0
    cur.execute('''INSERT INTO relationship_pending_events
        (league_id,source_event_id,facet_key,subject_alias,envelope,reason,status)
        SELECT %s,s,f,a::jsonb,e::jsonb,r,st
        FROM unnest(%s::text[], %s::text[], %s::text[], %s::text[], %s::text[], %s::text[]) AS h(s,f,a,e,r,st)
        ON CONFLICT (league_id,source_event_id,facet_key) DO NOTHING''',
        (league_id, [env['source_event_id'] for env, _ in holds], [_facet_key(env) for env, _ in holds],
         [_canonical(env['subject_alias']) for env, _ in holds], [_canonical(env) for env, _ in holds],
         [reason for _, reason in holds], [status] * len(holds)))
    return max(cur.rowcount or 0, 0)


def _split_receipt(receipt, pairs):
    """(accepted pairs, retry pairs with reason, dropped pairs with reason) from an append_events receipt.

    pairs: [(item, resolved event)]. A rejection names its envelope by (source_event_id, event_id)."""
    rejected = {}
    for item in (receipt or {}).get('rejected') or []:
        rejected[(item.get('source_event_id'), item.get('event_id'))] = str(item.get('reason') or 'rejected')
    accepted, retry, dropped = [], [], []
    for item, event in pairs:
        why = rejected.get((event.get('source_event_id'), event.get('event_id')))
        if why is None:
            accepted.append((item, event))
        elif why.startswith('unknown_identity'):
            retry.append((item, 'rejected: ' + why))
        else:
            dropped.append((item, 'rejected: ' + why))
    return accepted, retry, dropped


def append_or_hold(cur, envelopes, *, league_id, universe_id, team_registry):
    """Append envelopes whose subject resolves, hold the rest. Returns counts, the receipt and event_hashes."""
    batch, seen = [], {}
    for env in envelopes:
        _check(env)
        key, digest = _event_key(env), _envelope_hash(env)
        if key in seen:
            if seen[key] != digest:
                raise ValueError(f'conflicting envelopes for {key}')
            continue
        seen[key] = digest
        batch.append(env)

    reason, rel_store = _hold_reason(universe_id)
    resolved, holds = [], []
    for env in batch:
        if reason is not None:
            holds.append((env, reason))
            continue
        event, why = _resolve(cur, env, universe_id)
        if event is None:
            holds.append((env, why))
        else:
            resolved.append((env, event))

    receipt, accepted, dropped = None, [], []
    with _savepoint(cur):
        if resolved:
            receipt = _append(cur, rel_store, [event for _, event in resolved], universe_id, team_registry)
            accepted, retry, dropped = _split_receipt(receipt, resolved)
            holds.extend(retry)
        new_holds = _insert_holds(cur, league_id, holds)
        _insert_holds(cur, league_id, dropped, status='DROPPED')

    reasons = {}
    for _, why in holds + dropped:
        reasons[why] = reasons.get(why, 0) + 1
    entries, retracted = _hash_entries(batch)
    return {'appended': len(accepted), 'duplicates': len((receipt or {}).get('duplicates') or []),
            'held': len(holds), 'newly_held': new_holds, 'already_held': len(holds) - new_holds,
            'rejected': len(resolved) - len(accepted), 'dropped': len(dropped), 'hold_reasons': reasons,
            'receipt': receipt, 'event_hashes': entries, 'retracted': retracted}


def _pending_order(row):
    envelope = row['envelope']
    occurred = envelope.get('occurred_at') or {}
    return (row['created_at'], occurred.get('season') or 0, occurred.get('tick') or 0,
            envelope.get('kind') == 'correction', row['source_event_id'], row['facet_key'])


def flush_pending(cur, league_id, universe_id, team_registry):
    """Append held events whose subject now resolves, oldest first, and mark them APPENDED."""
    cur.execute('''SELECT source_event_id, facet_key, envelope, created_at FROM relationship_pending_events
                   WHERE league_id=%s AND status='PENDING'
                   ORDER BY created_at, source_event_id, facet_key FOR UPDATE''', (league_id,))
    rows = [{name: _col(row, i, name) for i, name in
             enumerate(('source_event_id', 'facet_key', 'envelope', 'created_at'))} for row in cur.fetchall()]
    for row in rows:
        if isinstance(row['envelope'], str):
            row['envelope'] = json.loads(row['envelope'])
    rows.sort(key=_pending_order)

    reason, rel_store = _hold_reason(universe_id)
    if reason is not None:
        return {'appended': 0, 'pending': len(rows), 'hold_reasons': {reason: len(rows)} if rows else {},
                'receipt': None}

    ready, reasons = [], {}
    for row in rows:
        event, why = _resolve(cur, row['envelope'], universe_id)
        if event is None:
            reasons[why] = reasons.get(why, 0) + 1
        else:
            ready.append((row, event))

    receipt, accepted, retry, dropped = None, [], [], []
    if ready:
        with _savepoint(cur):
            receipt = _append(cur, rel_store, [event for _, event in ready], universe_id, team_registry)
            accepted, retry, dropped = _split_receipt(receipt, ready)
            if accepted:
                cur.execute('''UPDATE relationship_pending_events SET status='APPENDED', appended_at=now(), reason=NULL
                               WHERE league_id=%s AND status='PENDING'
                                 AND (source_event_id, facet_key) IN
                                     (SELECT s, f FROM unnest(%s::text[], %s::text[]) AS k(s,f))''',
                            (league_id, [row['source_event_id'] for row, _ in accepted],
                             [row['facet_key'] for row, _ in accepted]))
            for status, group in (('PENDING', retry), ('DROPPED', dropped)):
                if group:
                    cur.execute('''UPDATE relationship_pending_events SET status=%s, reason=k.r
                                   FROM unnest(%s::text[], %s::text[], %s::text[]) AS k(s,f,r)
                                   WHERE league_id=%s AND status='PENDING'
                                     AND source_event_id=k.s AND facet_key=k.f''',
                                (status, [row['source_event_id'] for row, _ in group],
                                 [row['facet_key'] for row, _ in group], [why for _, why in group], league_id))
    for _, why in retry + dropped:
        reasons[why] = reasons.get(why, 0) + 1
    return {'appended': len(accepted), 'duplicates': len((receipt or {}).get('duplicates') or []),
            'pending': len(rows) - len(accepted) - len(dropped), 'rejected': len(retry) + len(dropped),
            'dropped': len(dropped), 'hold_reasons': reasons, 'receipt': receipt}


# ---------------------------------------------------------------- corrections

def _entry(value):
    """event_hashes value -> (hash, ref or None). A bare hash string is accepted for change detection."""
    if isinstance(value, dict):
        return value.get('hash'), value.get('ref')
    return value, None


def _correction(supersedes, original_sid, facet, ref, replacement, digest):
    replacement_view = None if replacement is None else deepcopy(replacement)
    if replacement_view is not None:
        replacement_view.pop('event_id', None)
        for key in PRODUCER_KEYS:
            replacement_view.pop(key, None)
    basis = replacement if replacement is not None else ref
    tag = f'correction:{digest[:16]}' if replacement is not None else f'retract:{digest[:16]}'
    source_event_id = f'{original_sid}#{tag}'
    subject = _strip_identity(deepcopy(basis.get('subject')
                                       or {'kind': 'person', 'role': basis['subject_alias']['role']}))
    envelope = {
        'schema_version': basis.get('schema_version'),
        'universe_id': None,
        'event_id': source_event_id,
        'source_event_id': source_event_id,
        'source_kind': basis.get('source_kind'),
        'source_refs': [],             # a chain of corrections shares its original: evidence only, never owned
        'kind': 'correction',
        'occurred_at': deepcopy(basis.get('occurred_at')),
        'subject': subject | {'universe_id': None, 'persona_id': None},
        'target': None,
        'actor': None,
        'basis': 'verified_simulation',
        'confidence': (replacement or {}).get('confidence') or 'high',
        'reviewer': None,
        'evidence_scope': [],
        'visibility': basis.get('visibility') or 'private',
        'attribution': {'responsible': [], 'reason_code': 'producer_correction',
                        'evidence_refs': [f'mu-producer:correction:{original_sid}']},
        'payload': {'reason': 'inputs_changed' if replacement is not None else 'event_vanished',
                    'replacement_event': replacement_view,
                    'original_source_event_id': original_sid},
        'supersedes_source_event_id': supersedes,
        'subject_alias': deepcopy(basis['subject_alias']),
        'supersedes_facet_key': facet,
    }
    target = basis.get('target')
    if isinstance(target, dict):
        envelope['target'] = _strip_identity(deepcopy(target)) | {'universe_id': None}
        if target.get('kind') == 'person':
            envelope['target']['persona_id'] = None
    if basis.get('target_alias') is not None:
        envelope['target_alias'] = deepcopy(basis['target_alias'])
    return envelope


def corrections(previous_event_hashes, current_envelopes):
    """Correction envelopes for events that changed (a replacement) or vanished (a null retraction)."""
    current = {}
    for env in current_envelopes:
        _check(env)
        if env['kind'] == 'correction':
            continue
        current[_event_key(env)] = env
    out = []
    for key in sorted(previous_event_hashes):
        old_hash, ref = _entry(previous_event_hashes[key])
        original_sid, _, facet = key.partition('|')
        if facet.endswith('>correction') or (ref or {}).get('kind') == 'correction':
            continue
        supersedes = (ref or {}).get('active_source_event_id') or original_sid
        env = current.get(key)
        if env is not None:
            new_hash = _envelope_hash(env)
            if new_hash != old_hash:
                out.append(_correction(supersedes, original_sid, facet, ref, env, new_hash))
        else:
            if ref is None or not ref.get('subject_alias'):
                raise ValueError(f'cannot retract {key}: its event_hashes entry has no ref')
            out.append(_correction(supersedes, original_sid, facet, ref, None, old_hash))
    return out
