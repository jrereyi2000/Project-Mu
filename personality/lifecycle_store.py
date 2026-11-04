"""Person lifecycle persistence (docs/done/PERSONALITY_DRAFT_INTEGRATION_PLAN.md §2.2-§2.6).

Ledgers from db/migrations/2027_01_24_personality_lifecycle.sql. Every function takes the caller's cursor, works
inside a savepoint and never commits or connects: profile, aliases, lifecycle rows and the batch receipt share
the caller's transaction (plan S2). Profiles are created only through personality.store (insert-once ensure,
or the same insert path for a reviewed/JR-edited profile before first use); nothing here redraws a profile.

  apply_lifecycle    a reviewed `personality-lifecycle-v1` manifest, exactly once (same payload replays its receipt)
  revert_batch       undo a batch only while nothing has used what it created
  export_state       the universe's lifecycle state, the `existing` input of lifecycle.plan_lifecycle
  resolve_person     scoped alias -> {status, identity, view, ...}; missing/unknown neutral, corruption raises
  require_ready      observe (report only) or enforce (block unresolved required people)
  initialize_person / bind_person / archive_person / close_binding   single-person hooks for creators
                     (draft-class entry, staff creation) with durable creator receipts
"""
from copy import deepcopy

from psycopg.types.json import Jsonb

from personality import core, lifecycle as L, store
from personality.core import Identity

TABLES = ('persona_lifecycle_universes', 'persona_lifecycle_people', 'persona_lifecycle_bindings',
          'persona_lifecycle_batches')
FINGERPRINT = (
    'persona_lifecycle_batches.after_state', 'persona_lifecycle_batches.applied_at',
    'persona_lifecycle_batches.batch_id', 'persona_lifecycle_batches.before_state',
    'persona_lifecycle_batches.manifest', 'persona_lifecycle_batches.revert_receipt',
    'persona_lifecycle_batches.review_receipt', 'persona_lifecycle_batches.status',
    'persona_lifecycle_batches.universe_id', 'persona_lifecycle_bindings.external_id',
    'persona_lifecycle_bindings.namespace', 'persona_lifecycle_bindings.receipt', 'persona_lifecycle_bindings.role',
    'persona_lifecycle_bindings.source_person_id', 'persona_lifecycle_bindings.universe_id',
    'persona_lifecycle_bindings.valid_from', 'persona_lifecycle_bindings.valid_to',
    'persona_lifecycle_people.birth_key', 'persona_lifecycle_people.classification',
    'persona_lifecycle_people.classification_receipt', 'persona_lifecycle_people.first_seen',
    'persona_lifecycle_people.persona_id', 'persona_lifecycle_people.revision', 'persona_lifecycle_people.role',
    'persona_lifecycle_people.source_person_id', 'persona_lifecycle_people.state',
    'persona_lifecycle_people.universe_id', 'persona_lifecycle_universes.initial_snapshot_sha256',
    'persona_lifecycle_universes.league_id', 'persona_lifecycle_universes.manifest_version',
    'persona_lifecycle_universes.registration', 'persona_lifecycle_universes.start_cutoff',
    'persona_lifecycle_universes.start_season', 'persona_lifecycle_universes.universe_id')
# Replaceable board IDs live only in lifecycle bindings (closable); every other verified alias is also a core alias.
LIFECYCLE_ONLY_PREFIXES = ('draft:',)
LIFECYCLE_ROLES = ('player', 'coach', 'gm', 'owner', 'scout')


class NotReadyError(L.LifecycleError):
    """enforce mode: a required person is unresolved (CLI exit 3). .report carries the require_ready report."""
    code = L.EXIT_UNRESOLVED

    def __init__(self, report):
        super().__init__(f"{len(report['unresolved'])} required people unresolved for {report['feature']}")
        self.report = report


class HistoricalResolutionError(L.LifecycleError):
    """An earlier cutoff that the stored receipts can't place exactly (never answered with today's profile)."""


# ---------- schema, universe, locks ----------

def _columns(cur, table):
    cur.execute('SELECT column_name FROM information_schema.columns WHERE table_schema = current_schema() '
                'AND table_name = %s', (table,))
    return {r[0] for r in cur.fetchall()}


def available(cur):
    """True when the lifecycle migration is applied (hooks stay in observe mode otherwise)."""
    cur.execute("SELECT to_regclass('persona_lifecycle_people') IS NOT NULL")
    row = cur.fetchone()
    return bool(row and row[0])


def check_schema(cur):
    got = tuple(sorted(f'{t}.{c}' for t in TABLES for c in _columns(cur, t)))
    if got != FINGERPRINT:
        raise L.StaleError('persona_lifecycle schema fingerprint mismatch')


def _league_stamp(cur):
    if 'madden_league_id' not in _columns(cur, 'league_state'):
        return None
    cur.execute('SELECT madden_league_id FROM league_state WHERE id = 1')
    row = cur.fetchone()
    return None if row is None or row[0] is None else str(row[0])


def check_league(cur, universe):
    """Reject a database stamped for another Madden league before any mutation."""
    stamp = _league_stamp(cur)
    if stamp is not None and stamp != str(universe['league_id']):
        raise L.StaleError(f'database is stamped for league {stamp}, universe is {universe["league_id"]}')


def _lock_universe(cur, universe_id):
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', ('personality-lifecycle:' + universe_id,))


def get_universe(cur, universe_id):
    cur.execute('SELECT registration FROM persona_lifecycle_universes WHERE universe_id = %s', (universe_id,))
    row = cur.fetchone()
    return None if row is None else row[0]


def universe_for_league(cur, league_id):
    cur.execute('SELECT registration FROM persona_lifecycle_universes WHERE league_id = %s', (str(league_id),))
    row = cur.fetchone()
    return None if row is None else row[0]


def register_universe(cur, universe):
    """Immutable registration; the same record replays, anything else is a conflict."""
    L.validate_universe(universe)
    stored = get_universe(cur, universe['universe_id'])
    if stored is not None:
        if L.canonical_json(stored) != L.canonical_json(universe):
            raise L.StaleError('universe registration is immutable (explicit correction review needed)')
        return stored
    other = universe_for_league(cur, universe['league_id'])
    if other is not None:
        raise L.StaleError(f'league {universe["league_id"]} is registered to {other["universe_id"]}')
    cur.execute('''INSERT INTO persona_lifecycle_universes (universe_id, league_id, initial_snapshot_sha256,
                   start_season, start_cutoff, manifest_version, registration) VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                (universe['universe_id'], str(universe['league_id']), universe['initial_snapshot_sha256'],
                 universe['start_season'], Jsonb(universe['start_cutoff']), universe['manifest_version'],
                 Jsonb(universe)))
    return universe


# ---------- export ----------

def _person_rows(cur, universe_id):
    cur.execute('''SELECT role, source_person_id, birth_key, classification, state, persona_id, revision, first_seen
                   FROM persona_lifecycle_people WHERE universe_id = %s''', (universe_id,))
    return [dict(role=r[0], source_person_id=str(r[1]), birth_key=r[2], classification=r[3], state=r[4],
                 persona_id=r[5], lifecycle_revision=r[6], first_seen=r[7]) for r in cur.fetchall()]


def export_state(cur, universe_id):
    """Sorted, canonical lifecycle state for plan_lifecycle's `existing` (and the apply precondition)."""
    people = []
    for row in _person_rows(cur, universe_id):
        entry = dict(role=row['role'], source_person_id=row['source_person_id'], persona_id=row['persona_id'],
                     classification=row['classification'], state=row['state'], revision=None, profile_hash=None)
        if row['persona_id'] is not None:
            profile = store.read(cur, Identity(universe_id, row['role'], row['birth_key']))
            if profile is not None:
                entry.update(revision=profile['revision'], profile_hash=L.profile_hash(profile))
        people.append(entry)
    cur.execute('''SELECT role, namespace, external_id, source_person_id, valid_from, valid_to
                   FROM persona_lifecycle_bindings WHERE universe_id = %s''', (universe_id,))
    bindings = [dict(role=r[0], namespace=r[1], external_id=r[2], source_person_id=str(r[3]), valid_from=r[4],
                     valid_to=r[5]) for r in cur.fetchall()]
    placeholders = ','.join(['%s'] * len(LIFECYCLE_ROLES))
    cur.execute(f'''SELECT s.persona_type, s.persona_id FROM persona_sliders s
                    WHERE s.persona_type IN ({placeholders}) AND NOT EXISTS (SELECT 1 FROM persona_core_profiles p
                    WHERE p.persona_type = s.persona_type AND p.persona_id = s.persona_id)''', LIFECYCLE_ROLES)
    legacy = {}
    for role, pid in cur.fetchall():
        legacy.setdefault(role, []).append(pid)
    cur.execute('''SELECT p.persona_type, p.persona_id FROM persona_core_profiles p WHERE p.universe_id = %s
                   AND NOT EXISTS (SELECT 1 FROM persona_lifecycle_people l WHERE l.universe_id = p.universe_id
                   AND l.role = p.persona_type AND l.persona_id = p.persona_id)''', (universe_id,))
    unmanaged = [dict(role=r[0], persona_id=r[1]) for r in cur.fetchall()]
    return dict(universe_id=universe_id, universe=get_universe(cur, universe_id),
                people=sorted(people, key=lambda p: (p['role'], p['source_person_id'])),
                bindings=sorted(bindings, key=lambda b: (b['role'], b['namespace'], b['external_id'])),
                legacy_persona_ids={k: sorted(v) for k, v in sorted(legacy.items())},
                core_profiles=sorted(unmanaged, key=lambda c: (c['role'], c['persona_id'])))


def _empty(export):
    return not (export['people'] or export['bindings'] or export['legacy_persona_ids'] or export['core_profiles'])


def _preconditions_hold(manifest, export):
    expected = manifest['preconditions']['existing_sha256']
    return expected == L.canonical_hash(export) or (_empty(export) and expected == L.canonical_hash({}))


# ---------- single-person writes ----------

def _core_alias(namespace):
    return not namespace.startswith(LIFECYCLE_ONLY_PREFIXES)


def _insert_binding(cur, universe_id, role, spid, alias, cutoff, receipt):
    """Inserts one lifecycle binding (and its core alias). Already bound to this person: no-op. Conflict raises."""
    cur.execute('''SELECT source_person_id, valid_to FROM persona_lifecycle_bindings
                   WHERE universe_id=%s AND role=%s AND namespace=%s AND external_id=%s''',
                (universe_id, role, alias['namespace'], alias['external_id']))
    row = cur.fetchone()
    if row is not None:
        if str(row[0]) != spid:
            raise L.StaleError(f'alias {alias["namespace"]}/{alias["external_id"]} belongs to another person')
        return None
    cur.execute('''INSERT INTO persona_lifecycle_bindings (universe_id, role, namespace, external_id,
                   source_person_id, valid_from, valid_to, receipt) VALUES (%s,%s,%s,%s,%s,%s,NULL,%s)''',
                (universe_id, role, alias['namespace'], alias['external_id'], spid, Jsonb(cutoff), Jsonb(receipt)))
    core_bound = False
    if _core_alias(alias['namespace']):
        try:
            store.bind_alias(cur, L.identity(universe_id, role, spid), alias['namespace'], alias['external_id'])
        except core.ConflictError as e:
            raise L.StaleError(str(e)) from None
        core_bound = True
    return dict(namespace=alias['namespace'], external_id=alias['external_id'], core=core_bound)


def _store_profile(cur, ident, profile, season, *, edited, reason):
    """ensure for generator output / reviewed real values; the same insert path for a JR edit before first use."""
    try:
        if not edited:
            extra = {} if profile['origin'] == 'generated' else dict(
                sliders=profile['sliders'], traits=profile['traits'], evidence=profile['evidence'])
            return store.ensure(cur, ident, profile['origin'], season, **extra)
        with store._savepoint(cur):
            store._lock(cur, ident)
            if store.read(cur, ident) is not None:
                raise core.ConflictError('profile already exists')
            cur.execute('SELECT 1 FROM persona_sliders WHERE persona_type=%s AND persona_id=%s',
                        (ident.role, ident.persona_id))
            if cur.fetchone():
                raise core.ConflictError('unmanaged legacy identity collision')
            store._insert_profile(cur, profile, season, reason)
            return profile
    except core.ConflictError as e:
        raise L.StaleError(str(e)) from None


def _insert_person(cur, universe_id, role, spid, origin, receipt, cutoff, persona_id, state='ready'):
    cur.execute('''INSERT INTO persona_lifecycle_people (universe_id, source_person_id, role, birth_key,
                   classification, classification_receipt, first_seen, state, persona_id, revision)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0)''',
                (universe_id, spid, role, L.birth_key(spid), origin, Jsonb(receipt), Jsonb(cutoff), state,
                 persona_id))


def _person(cur, universe_id, role, spid):
    cur.execute('''SELECT classification, state, persona_id, first_seen, revision FROM persona_lifecycle_people
                   WHERE universe_id=%s AND role=%s AND source_person_id=%s''', (universe_id, role, spid))
    row = cur.fetchone()
    return None if row is None else dict(classification=row[0], state=row[1], persona_id=row[2],
                                         first_seen=row[3], revision=row[4])


def initialize_person(cur, universe_id, role, source_person_id, origin, season, *, cutoff, receipt, aliases=(),
                      profile=None):
    """Creator hook: one verified person (draft-class entrant, fictional staff hire) initialized once.

    origin 'generated' seeds through the core; 'real' needs `profile` {sliders, traits, evidence}. receipt is the
    durable creator/classification receipt. Replays bind any new aliases and return the stored person; a
    different classification is a conflict. Returns {persona_id, created, bound}."""
    spid = L._uuid(source_person_id, 'source_person_id')
    cutoff = L.validate_cutoff(cutoff)
    if origin not in ('generated', 'real'):
        raise L.LifecycleError('initialize only verified generated or real people (unknown stays pending)')
    if not isinstance(receipt, dict) or not receipt:
        raise L.LifecycleError('a durable creator receipt is required')
    with store._savepoint(cur):
        _lock_universe(cur, universe_id)
        if get_universe(cur, universe_id) is None:
            raise L.StaleError('universe is not registered')
        ident = L.identity(universe_id, role, spid)
        stored = _person(cur, universe_id, role, spid)
        created = stored is None
        if stored is not None:
            if stored['classification'] != origin:
                raise L.StaleError('classification cannot change through initialization')
            if stored['state'] == 'quarantined':
                raise L.StaleError('person is quarantined')
        else:
            if origin == 'real':
                if not profile:
                    raise L.LifecycleError('a real person needs a reviewed profile')
                values = core.initialize(ident, 'real', season, sliders=profile.get('sliders'),
                                         traits=profile.get('traits'), evidence=profile.get('evidence'))
            else:
                values = core.initialize(ident, 'generated', season)
            _store_profile(cur, ident, values, season, edited=False, reason='personality lifecycle initialization')
            _insert_person(cur, universe_id, role, spid, origin, receipt, cutoff, ident.persona_id)
        bound = [b for b in (_insert_binding(cur, universe_id, role, spid, a, cutoff, receipt)
                             for a in [dict(namespace=L.SOURCE_PERSON, external_id=spid), *aliases]) if b]
        return dict(persona_id=ident.persona_id, source_person_id=spid, created=created, bound=bound)


def bind_person(cur, universe_id, role, source_person_id, aliases, *, cutoff, receipt):
    """Verified handoff/promotion alias for an existing person (e.g. db-player after the draft, db-coach after a
    coordinator's promotion). Conflicts raise; existing bindings to the same person are no-ops."""
    spid = L._uuid(source_person_id, 'source_person_id')
    cutoff = L.validate_cutoff(cutoff)
    with store._savepoint(cur):
        _lock_universe(cur, universe_id)
        stored = _person(cur, universe_id, role, spid)
        if stored is None:
            raise L.StaleError('bind needs an initialized person')
        if stored['state'] == 'quarantined':
            raise L.StaleError('person is quarantined')
        return [b for b in (_insert_binding(cur, universe_id, role, spid, a, cutoff, receipt) for a in aliases) if b]


def close_binding(cur, universe_id, role, namespace, external_id, *, cutoff):
    """Ends a replaceable/time-scoped binding (a removed board row); identity and history stay."""
    cur.execute('''UPDATE persona_lifecycle_bindings SET valid_to=%s WHERE universe_id=%s AND role=%s
                   AND namespace=%s AND external_id=%s AND valid_to IS NULL''',
                (Jsonb(L.validate_cutoff(cutoff)), universe_id, role, namespace, external_id))
    return cur.rowcount


def archive_person(cur, universe_id, role, source_person_id, *, cutoff, reason):
    """Archives (never deletes) a person: a withdrawn prospect, a same-cycle coordinator whose row was deleted."""
    spid = L._uuid(source_person_id, 'source_person_id')
    L._text(reason, 'reason')
    L.validate_cutoff(cutoff)
    with store._savepoint(cur):
        _lock_universe(cur, universe_id)
        stored = _person(cur, universe_id, role, spid)
        if stored is None:
            raise L.StaleError('archive needs a stored person')
        if stored['state'] == 'archived':
            return False
        cur.execute('''UPDATE persona_lifecycle_people SET state='archived', revision=revision+1
                       WHERE universe_id=%s AND role=%s AND source_person_id=%s''', (universe_id, role, spid))
        return True


# ---------- batches ----------

def apply_lifecycle(cur, manifest, *, expected_hash, review_receipt):
    """Applies a reviewed manifest in the caller's transaction. Replaying the same batch returns its receipt;
    a different payload, a changed lifecycle state, a stale revision or a schema mismatch raises StaleError."""
    result = L.validate_manifest(manifest)
    if not result['valid']:
        raise L.LifecycleError('invalid manifest: ' + '; '.join(result['errors']))
    if manifest['manifest_sha256'] != expected_hash:
        raise L.StaleError('manifest hash is not the expected hash')
    L.validate_review(review_receipt, expected_hash)
    universe = manifest['universe']
    uid = universe['universe_id']
    with store._savepoint(cur):
        _lock_universe(cur, uid)
        check_schema(cur)
        cur.execute('SELECT manifest, status, after_state FROM persona_lifecycle_batches WHERE batch_id = %s',
                    (expected_hash,))
        row = cur.fetchone()
        if row is not None:
            if L.canonical_json(row[0]) != L.canonical_json(manifest):
                raise L.StaleError('batch ID reused with a different payload')
            if row[1] != 'applied':
                raise L.StaleError('batch was reverted; preview and review again')
            return _batch_receipt(expected_hash, uid, row[1], row[2], replay=True)
        check_league(cur, universe)
        export = export_state(cur, uid)
        if not _preconditions_hold(manifest, export):
            raise L.StaleError('lifecycle state changed since preview; preview and review again')
        register_universe(cur, universe)
        cutoff, season = manifest['cutoff'], manifest['cutoff']['season']
        stamp = dict(batch_id=expected_hash, reviewer=review_receipt['reviewer'])
        before = dict(existing_sha256=L.canonical_hash(export), people=[])
        after = dict(initialized=[], bound=[], retained=[], pending=[], quarantined=[])
        current = {(p['role'], p['source_person_id']): p for p in export['people']}
        for p in manifest['people']:
            key = (p['role'], p.get('source_person_id'))
            action = p['action']
            if action == 'initialize':
                ident = L.identity(uid, p['role'], p['source_person_id'])
                profile = L.proposed_profile(manifest, p)
                stored = _store_profile(cur, ident, profile, season, edited=p.get('edited', False),
                                        reason='personality lifecycle initialization' +
                                               (' (edited by JR before first use)' if p.get('edited') else ''))
                if L.profile_hash(stored) != p['proposed_profile_hash']:
                    raise L.StaleError(f'stored profile differs from the reviewed one for {p["source_person_id"]}')
                _insert_person(cur, uid, p['role'], p['source_person_id'], p['classification'], p['receipt'],
                               p['first_seen'], ident.persona_id)
                bound = [b for b in (_insert_binding(cur, uid, p['role'], p['source_person_id'], a, cutoff, stamp)
                                     for a in p['aliases']) if b]
                after['initialized'].append(dict(role=p['role'], source_person_id=p['source_person_id'],
                                                 persona_id=ident.persona_id, profile_hash=p['proposed_profile_hash'],
                                                 bindings=bound))
            elif action in ('bind', 'retain'):
                stored = current.get(key)
                expected = p.get('expected') or {}
                if stored is None or stored.get('revision') != expected.get('revision') or \
                        stored.get('profile_hash') != expected.get('profile_hash'):
                    raise L.StaleError(f'stale profile revision for {p["source_person_id"]}')
                before['people'].append(stored)
                if action == 'bind':
                    bound = [b for b in (_insert_binding(cur, uid, p['role'], p['source_person_id'], a, cutoff, stamp)
                                         for a in p['aliases']) if b]
                    after['bound'].append(dict(role=p['role'], source_person_id=p['source_person_id'],
                                               bindings=bound))
                else:
                    after['retained'].append(dict(role=p['role'], source_person_id=p['source_person_id']))
            else:
                # pending/quarantine are reported, never stored as a profile or a classification (plan Q1)
                after['pending' if action == 'pending' else 'quarantined'].append(
                    dict(role=p['role'], source_person_id=p.get('source_person_id'), reason=p.get('reason'),
                         aliases=p['aliases']))
        cur.execute('''INSERT INTO persona_lifecycle_batches (batch_id, universe_id, manifest, before_state,
                       after_state, status, review_receipt) VALUES (%s,%s,%s,%s,%s,'applied',%s)''',
                    (expected_hash, uid, Jsonb(manifest), Jsonb(before), Jsonb(after), Jsonb(review_receipt)))
        return _batch_receipt(expected_hash, uid, 'applied', after, replay=False)


def _batch_receipt(batch_id, universe_id, status, after, *, replay):
    return dict(batch_id=batch_id, universe_id=universe_id, status=status, replay=replay,
                after_sha256=L.canonical_hash(after),
                counts={k: len(v) for k, v in after.items()})


def _count(cur, sql, params):
    cur.execute(sql, params)
    return cur.fetchone()[0]


def revert_batch(cur, batch_id, *, expected_after_hash, review_receipt):
    """Reverses a batch only if its after-state is unchanged and nothing used it since (no receipts, extra aliases,
    role links). Deletes batch-created bindings and profiles in FK order; keeps catalogues, the universe and the
    reverted batch row. Once downstream use exists, restore a consistent checkpoint instead."""
    L.validate_review(review_receipt, batch_id)
    with store._savepoint(cur):
        cur.execute('SELECT universe_id, after_state, status FROM persona_lifecycle_batches WHERE batch_id = %s',
                    (batch_id,))
        row = cur.fetchone()
        if row is None:
            raise L.StaleError('no such batch')
        uid, after, status = row
        _lock_universe(cur, uid)
        if status != 'applied':
            raise L.StaleError('batch is not applied')
        if L.canonical_hash(after) != expected_after_hash:
            raise L.StaleError('after-state hash is not the expected hash')
        cur.execute("SELECT to_regclass('persona_core_links') IS NOT NULL")
        links = bool(cur.fetchone()[0])
        for item in after['initialized']:
            ident = L.identity(uid, item['role'], item['source_person_id'])
            profile = store.read(cur, ident)
            if profile is None or L.profile_hash(profile) != item['profile_hash']:
                raise L.StaleError(f'profile {ident.persona_id} changed since the batch')
            pair = (ident.role, ident.persona_id)
            if _count(cur, 'SELECT count(*) FROM persona_core_events WHERE persona_type=%s AND persona_id=%s', pair):
                raise L.StaleError(f'profile {ident.persona_id} has receipts (downstream use)')
            if links and _count(cur, '''SELECT count(*) FROM persona_core_links WHERE (from_type=%s AND from_id=%s)
                                        OR (to_type=%s AND to_id=%s)''', pair + pair):
                raise L.StaleError(f'profile {ident.persona_id} has a role link')
            if _count(cur, 'SELECT count(*) FROM persona_core_aliases WHERE persona_type=%s AND persona_id=%s',
                      pair) != sum(1 for b in item['bindings'] if b['core']):
                raise L.StaleError(f'profile {ident.persona_id} gained aliases since the batch')
            if _count(cur, '''SELECT count(*) FROM persona_lifecycle_bindings WHERE universe_id=%s AND role=%s
                              AND source_person_id=%s''', (uid, item['role'], item['source_person_id'])) != \
                    len(item['bindings']):
                raise L.StaleError(f'person {item["source_person_id"]} gained bindings since the batch')
            state = _person(cur, uid, item['role'], item['source_person_id'])
            if state is None or state['state'] != 'ready' or state['revision'] != 0:
                raise L.StaleError(f'person {item["source_person_id"]} changed state since the batch')
        for item in after['bound']:
            for b in item['bindings']:
                cur.execute('''SELECT source_person_id FROM persona_lifecycle_bindings WHERE universe_id=%s
                               AND role=%s AND namespace=%s AND external_id=%s''',
                            (uid, item['role'], b['namespace'], b['external_id']))
                got = cur.fetchone()
                if got is None or str(got[0]) != item['source_person_id']:
                    raise L.StaleError('a batch binding changed since the batch')
        removed = dict(bindings=0, profiles=0)
        for item in after['bound']:
            ident = L.identity(uid, item['role'], item['source_person_id'])
            for b in item['bindings']:
                _delete_binding(cur, uid, ident, b)
                removed['bindings'] += 1
        for item in after['initialized']:
            ident = L.identity(uid, item['role'], item['source_person_id'])
            for b in item['bindings']:
                _delete_binding(cur, uid, ident, b)
                removed['bindings'] += 1
            cur.execute('''DELETE FROM persona_lifecycle_people WHERE universe_id=%s AND role=%s
                           AND source_person_id=%s''', (uid, item['role'], item['source_person_id']))
            pair = (ident.role, ident.persona_id)
            for table in ('persona_trait_history', 'persona_traits', 'persona_core_profiles', 'persona_sliders'):
                cur.execute(f'DELETE FROM {table} WHERE persona_type=%s AND persona_id=%s', pair)
            removed['profiles'] += 1
        cur.execute('''UPDATE persona_lifecycle_batches SET status='reverted', revert_receipt=%s
                       WHERE batch_id=%s''', (Jsonb(review_receipt), batch_id))
        return dict(batch_id=batch_id, universe_id=uid, status='reverted', removed=removed)


def _delete_binding(cur, uid, ident, b):
    if b['core']:
        cur.execute('''DELETE FROM persona_core_aliases WHERE universe_id=%s AND persona_type=%s AND namespace=%s
                       AND external_id=%s AND persona_id=%s''',
                    (uid, ident.role, b['namespace'], b['external_id'], ident.persona_id))
    cur.execute('''DELETE FROM persona_lifecycle_bindings WHERE universe_id=%s AND role=%s AND namespace=%s
                   AND external_id=%s''', (uid, ident.role, b['namespace'], b['external_id']))


# ---------- resolution ----------

def resolve_person(cur, universe_id, role, namespace, external_id, *, cutoff, feature, current=False):
    """{status, identity, view, profile_revision, profile_hash, reason, cutoff}.

    status: ready | missing | unknown | ambiguous | future | quarantined. missing/unknown carry the explicit neutral
    view; storage/schema errors and a ready person without a core profile (corruption) raise, never randomize.
    A cutoff earlier than recorded changes reads the profile as of that season through store.read_as_of; a change
    inside the cutoff's own season can't be placed by phase and raises HistoricalResolutionError unless
    `current=True` (the caller is deciding now, at the accepted current cutoff)."""
    cutoff = L.validate_cutoff(cutoff)
    out = dict(status=None, identity=None, view=None, profile_revision=None, profile_hash=None, reason=None,
               cutoff=cutoff)

    def neutral(status, reason):
        out.update(status=status, reason=reason, view=core.feature_view(None, feature))
        return out
    cur.execute('''SELECT source_person_id, valid_from, valid_to FROM persona_lifecycle_bindings
                   WHERE universe_id=%s AND role=%s AND namespace=%s AND external_id=%s''',
                (universe_id, role, namespace, str(external_id)))
    row = cur.fetchone()
    if row is None:
        return neutral('missing', 'no_binding')
    spid, valid_from, valid_to = str(row[0]), row[1], row[2]
    if L.cutoff_key(valid_from) > L.cutoff_key(cutoff):
        out.update(status='future', reason='binding_after_cutoff')
        return out
    if valid_to is not None and L.cutoff_key(cutoff) >= L.cutoff_key(valid_to):
        return neutral('missing', 'binding_closed')
    person = _person(cur, universe_id, role, spid)
    if person is None:
        raise L.StaleError('binding without a lifecycle person (corrupt ledger)')
    ident = L.identity(universe_id, role, spid)
    out['identity'] = dict(universe_id=universe_id, role=role, source_person_id=spid, persona_id=ident.persona_id)
    if L.cutoff_key(person['first_seen']) > L.cutoff_key(cutoff):
        out.update(status='future', reason='first_seen_after_cutoff')
        return out
    if person['state'] == 'quarantined':
        out.update(status='quarantined', reason='quarantined')
        return out
    if person['state'] == 'pending' or person['classification'] == 'unknown':
        return neutral('unknown', 'unclassified')
    if _core_alias(namespace):
        resolved = store.resolve_alias(cur, universe_id, role, namespace, str(external_id))
        if resolved is not None and resolved != ident:
            out.update(status='ambiguous', reason='core_alias_disagrees')
            return out
    profile = store.read(cur, ident)
    if profile is None:
        raise L.StaleError(f'lifecycle person {spid} is ready but its core profile is missing (corrupt)')
    if profile['revision'] > 0 and not current:
        receipts = store._own_receipts(cur, ident)
        if any(r['season'] == cutoff['season'] for r in receipts):
            raise HistoricalResolutionError('a change inside the cutoff season cannot be placed by phase')
        if any(r['season'] > cutoff['season'] for r in receipts):
            profile = store.read_as_of(cur, ident, cutoff['season'], None)
            if profile is None:
                out.update(status='future', reason='profile_after_cutoff')
                return out
    out.update(status='ready', reason='archived' if person['state'] == 'archived' else None,
               view=core.feature_view(profile, feature), profile_revision=profile['revision'],
               profile_hash=L.profile_hash(profile))
    return out


def require_ready(cur, references, *, cutoff, feature, mode):
    """references: [{universe_id, role, namespace, external_id, required=True}].

    observe: report only, current decisions unchanged. enforce: NotReadyError when a required reference isn't
    ready. Enforce never activates an effect; the feature owner decides on neutral fallback for missing people."""
    if mode not in ('observe', 'enforce'):
        raise L.LifecycleError('mode must be observe or enforce')
    results, unresolved = [], []
    for ref in references:
        r = resolve_person(cur, ref['universe_id'], ref['role'], ref['namespace'], ref['external_id'],
                           cutoff=cutoff, feature=feature, current=ref.get('current', False))
        results.append(r)
        if r['status'] != 'ready' and ref.get('required', True):
            unresolved.append(dict(reference={k: ref[k] for k in ('role', 'namespace', 'external_id')},
                                   status=r['status'], reason=r['reason']))
    report = dict(mode=mode, feature=feature, cutoff=deepcopy(cutoff), total=len(results),
                  ready=sum(1 for r in results if r['status'] == 'ready'), unresolved=unresolved,
                  blocked=mode == 'enforce' and bool(unresolved))
    if report['blocked']:
        raise NotReadyError(report)
    report['results'] = results
    return report
