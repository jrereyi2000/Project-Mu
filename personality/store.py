"""PostgreSQL persistence using existing sliders/traits plus core sidecars.

Every mutation uses a savepoint, never commits. The caller owns the outer transaction.
No imports from db_handler; no automatic migrations, seed-on-read or connections.
"""
from contextlib import contextmanager
from copy import deepcopy
import uuid

from psycopg.types.json import Jsonb

from personality.core import (Identity, ConflictError, NotFoundError, PersonalityError,
                              adopt, apply_change, apply_override, carry_over, initialize,
                              profile_as_of, validate_profile, _text)


@contextmanager
def _savepoint(cur):
    name = 'persona_' + uuid.uuid4().hex
    cur.execute(f'SAVEPOINT {name}')
    try:
        yield
    except BaseException:
        cur.execute(f'ROLLBACK TO SAVEPOINT {name}')
        cur.execute(f'RELEASE SAVEPOINT {name}')
        raise
    else:
        cur.execute(f'RELEASE SAVEPOINT {name}')


def _lock(cur, identity):
    cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))',
                ('personality:' + identity.persona_id,))


def read(cur, identity):
    """None only means absent; schema/connectivity/corruption errors propagate."""
    cur.execute('''SELECT p.origin, p.catalogue_version, p.revision, p.metadata, c.catalogue,
                         s.temperament,s.sociability,s.confidence,s.openness,s.warmth,s.drive
                  FROM persona_core_profiles p
                  JOIN persona_core_catalogues c ON c.version=p.catalogue_version
                  JOIN persona_sliders s ON s.persona_type=p.persona_type AND s.persona_id=p.persona_id
                  WHERE p.universe_id=%s AND p.persona_type=%s AND p.birth_key=%s''',
                (identity.universe_id, identity.role, identity.birth_key))
    row = cur.fetchone()
    if row is None:
        return None
    origin, version, revision, metadata, catalogue, *values = row
    profile = deepcopy(metadata)
    from newsroom.personas import SLIDERS
    profile.update(origin=origin, catalogue_version=version, revision=revision,
                   catalogue=catalogue, sliders=dict(zip(SLIDERS, values)))
    cur.execute('''SELECT trait_id FROM persona_traits WHERE persona_type=%s AND persona_id=%s
                   AND active=TRUE ORDER BY id''', (identity.role, identity.persona_id))
    profile['traits'] = [r[0] for r in cur.fetchall()]
    # Legacy debug writes must not silently edit a core-managed profile.
    if profile['sliders'] != metadata['sliders'] or profile['traits'] != metadata['traits']:
        raise ConflictError('canonical profile changed outside personality core')
    validate_profile(profile)
    if (profile['universe_id'],profile['role'],profile['birth_key']) != (identity.universe_id,identity.role,identity.birth_key):
        raise ConflictError('stored metadata belongs to another identity')
    return profile


def _write_canonical(cur, profile):
    from newsroom.personas import SLIDERS
    cur.execute('''INSERT INTO persona_sliders
        (persona_type,persona_id,temperament,sociability,confidence,openness,warmth,drive)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (persona_type,persona_id) DO UPDATE SET
        temperament=EXCLUDED.temperament,sociability=EXCLUDED.sociability,
        confidence=EXCLUDED.confidence,openness=EXCLUDED.openness,
        warmth=EXCLUDED.warmth,drive=EXCLUDED.drive,updated_at=now()''',
        (profile['role'], profile['persona_id'], *(profile['sliders'][s] for s in SLIDERS)))
    cur.execute('DELETE FROM persona_traits WHERE persona_type=%s AND persona_id=%s',
                (profile['role'], profile['persona_id']))
    for trait in profile['traits']:
        cur.execute('INSERT INTO persona_traits(persona_type,persona_id,trait_id) VALUES (%s,%s,%s)',
                    (profile['role'], profile['persona_id'], trait))


def _metadata(profile):
    return {k:deepcopy(v) for k,v in profile.items() if k != 'catalogue'}


def _insert_profile(cur, profile, season, reason):
    """The ensure insert path: catalogue snapshot, canonical rows, core row, trait history."""
    cur.execute('''INSERT INTO persona_core_catalogues(version,catalogue) VALUES (%s,%s)
                   ON CONFLICT (version) DO NOTHING''',
                (profile['catalogue_version'], Jsonb(profile['catalogue'])))
    _write_canonical(cur, profile)
    _insert_core_row(cur, profile)
    for trait in profile['traits']:
        cur.execute('''INSERT INTO persona_trait_history
            (persona_type,persona_id,trait_id,added_season,reason) VALUES (%s,%s,%s,%s,%s)''',
            (profile['role'],profile['persona_id'],trait,season,reason))


def _insert_core_row(cur, profile):
    cur.execute('''INSERT INTO persona_core_profiles
        (persona_type,persona_id,universe_id,birth_key,origin,catalogue_version,revision,metadata)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
        (profile['role'],profile['persona_id'],profile['universe_id'],profile['birth_key'],
         profile['origin'],profile['catalogue_version'],profile['revision'],Jsonb(_metadata(profile))))


def _receipt(cur, identity, kind, event_id, source_event_id, season, request, before, after,
             *, tick=None, actor=None):
    cur.execute('''INSERT INTO persona_core_events
        (persona_type,persona_id,event_id,source_event_id,season,request,before_state,after_state,
         kind,effective_tick,actor)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
        (identity.role,identity.persona_id,event_id,source_event_id,season,Jsonb(request),
         Jsonb(before),Jsonb(after),kind,tick,actor))


def _update_profile(cur, identity, after):
    _write_canonical(cur, after)
    cur.execute('''UPDATE persona_core_profiles SET revision=%s,metadata=%s
                   WHERE persona_type=%s AND persona_id=%s''',
                (after['revision'],Jsonb(_metadata(after)),identity.role,identity.persona_id))


def _trait_history(cur, identity, season, removed, added, reason):
    for trait in removed:
        cur.execute('''UPDATE persona_trait_history SET removed_season=%s
            WHERE persona_type=%s AND persona_id=%s AND trait_id=%s AND removed_season IS NULL''',
            (season,identity.role,identity.persona_id,trait))
    for trait in added:
        cur.execute('''INSERT INTO persona_trait_history
            (persona_type,persona_id,trait_id,added_season,reason) VALUES (%s,%s,%s,%s,%s)''',
            (identity.role,identity.persona_id,trait,season,reason))


def ensure(cur, identity, origin, season, **initial_values):
    """Initialize exactly once. Existing identity returns stored state, not new defaults.

    Replaying with a different origin fails. Replaying after catalogue/seed changes
    returns the frozen original without recomputation. Aliases are bound explicitly.
    """
    with _savepoint(cur):
        _lock(cur, identity)
        stored = read(cur, identity)
        if stored is not None:
            if stored['origin'] != origin:
                raise ConflictError('origin cannot change through initialization')
            return stored
        profile = initialize(identity, origin, season, **initial_values)
        cur.execute('SELECT 1 FROM persona_sliders WHERE persona_type=%s AND persona_id=%s',
                    (identity.role, identity.persona_id))
        if cur.fetchone():
            raise ConflictError('unmanaged legacy identity collision')
        _insert_profile(cur, profile, season, 'personality core initialization')
        return profile


def bind_alias(cur, identity, namespace, external_id):
    """Caller attests verified mapping, e.g. draft:2028 or save-pgid. Never name-match."""
    _text(namespace, 'namespace')
    _text(external_id, 'external_id')
    with _savepoint(cur):
        _lock(cur, identity)
        if read(cur, identity) is None:
            raise ConflictError('initialize identity before binding alias')
        cur.execute('''INSERT INTO persona_core_aliases
            (universe_id,persona_type,namespace,external_id,persona_id) VALUES (%s,%s,%s,%s,%s)
            ON CONFLICT (universe_id,persona_type,namespace,external_id) DO NOTHING''',
            (identity.universe_id,identity.role,namespace,external_id,identity.persona_id))
        resolved = resolve_alias(cur, identity.universe_id, identity.role, namespace, external_id)
        if resolved != identity:
            raise ConflictError('alias already belongs to another identity')


def resolve_alias(cur, universe_id, role, namespace, external_id):
    cur.execute('''SELECT p.birth_key FROM persona_core_aliases a JOIN persona_core_profiles p
                  ON p.persona_type=a.persona_type AND p.persona_id=a.persona_id
                  WHERE a.universe_id=%s AND a.persona_type=%s AND a.namespace=%s AND a.external_id=%s''',
                (universe_id,role,namespace,external_id))
    row=cur.fetchone()
    return None if row is None else Identity(universe_id,role,row[0])  # legacy:<role>:<id> keys rebuild adopted identities


def change(cur, identity, event):
    with _savepoint(cur):
        _lock(cur, identity)
        profile = read(cur, identity)
        if profile is None:
            raise ConflictError('cannot evolve absent profile')
        cur.execute('''SELECT request,after_state FROM persona_core_events
                       WHERE persona_type=%s AND persona_id=%s AND event_id=%s''',
                    (identity.role,identity.persona_id,event['event_id']))
        previous=cur.fetchone()
        if previous:
            if previous[0] != event:
                raise ConflictError('event ID reused with different payload')
            # Return the originally recorded result, even if later years evolved.
            return previous[1]
        cur.execute('''SELECT 1 FROM persona_core_events
                       WHERE persona_type=%s AND persona_id=%s AND source_event_id=%s''',
                    (identity.role,identity.persona_id,event['source_event_id']))
        if cur.fetchone():
            raise ConflictError('source event already consumed by profile evolution')
        after,audit=apply_change(profile,event)
        _update_profile(cur,identity,after)
        # Requires 2027_01_29: kind/effective_tick; the one-a-season index covers evolution only.
        _receipt(cur,identity,'evolution',event['event_id'],event['source_event_id'],event['season'],
                 event,audit['before'],audit['after'],tick=event.get('tick'))
        if event['kind']=='trait':
            _trait_history(cur,identity,event['season'],[event['remove']] if event.get('remove') else [],
                           [event['add']] if event.get('add') else [],event['reason'])
        return after


def override(cur, identity, request):
    """JR's audited override (core.apply_override). Replay of the same override_id returns the original."""
    _text(request['override_id'], 'override_id')
    event_id = 'override:' + request['override_id']
    with _savepoint(cur):
        _lock(cur, identity)
        profile = read(cur, identity)
        if profile is None:
            raise NotFoundError('cannot override absent profile')
        cur.execute('''SELECT request,after_state FROM persona_core_events
                       WHERE persona_type=%s AND persona_id=%s AND event_id=%s''',
                    (identity.role,identity.persona_id,event_id))
        previous = cur.fetchone()
        if previous:
            if previous[0] != request:
                raise ConflictError('override ID reused with different payload')
            return previous[1]
        after, audit = apply_override(profile, request)
        _update_profile(cur, identity, after)
        _receipt(cur, identity, 'override', event_id, event_id, request['season'], request,
                 audit['before'], audit['after'], tick=request['tick'], actor=request['actor'])
        removed = [t for t in profile['traits'] if t not in after['traits']]
        added = [t for t in after['traits'] if t not in profile['traits']]
        _trait_history(cur, identity, request['season'], removed, added, 'JR override: ' + request['reason'])
        return after


def _legacy_state(cur, role, persona_id):
    from newsroom.personas import SLIDERS
    cur.execute('''SELECT temperament,sociability,confidence,openness,warmth,drive FROM persona_sliders
                   WHERE persona_type=%s AND persona_id=%s''', (role, persona_id))
    row = cur.fetchone()
    if row is None:
        return None
    cur.execute('''SELECT trait_id FROM persona_traits WHERE persona_type=%s AND persona_id=%s
                   AND active=TRUE ORDER BY id''', (role, persona_id))
    return dict(persona_type=role, persona_id=persona_id, sliders=dict(zip(SLIDERS, row)),
                traits=[r[0] for r in cur.fetchall()])


def adopt_legacy(cur, universe_id, role, legacy_persona_id, origin, season, *, reviewer, reason):
    """Bring a legacy writer/GM persona under the core in place, keeping its ID and current values.

    The persona_sliders/persona_traits rows are left as they are. Running it again returns the
    stored profile. Writers are 'real' (hand-written, reviewed); GMs 'generated' (plan Q2).
    """
    _text(reviewer, 'reviewer')
    _text(reason, 'reason')
    identity = Identity.adopted(universe_id, role, legacy_persona_id)
    with _savepoint(cur):
        _lock(cur, identity)
        stored = read(cur, identity)
        if stored is not None:
            if stored['origin'] != origin:
                raise ConflictError('origin cannot change through adoption')
            return stored
        cur.execute('''SELECT universe_id,birth_key FROM persona_core_profiles
                       WHERE persona_type=%s AND persona_id=%s''', (role, legacy_persona_id))
        if cur.fetchone():
            raise ConflictError('legacy persona already managed under another identity')
        state = _legacy_state(cur, role, legacy_persona_id)
        if state is None:
            raise NotFoundError('no legacy persona to adopt')
        profile = adopt(identity, origin, season, state['sliders'], state['traits'])
        cur.execute('''INSERT INTO persona_core_catalogues(version,catalogue) VALUES (%s,%s)
                       ON CONFLICT (version) DO NOTHING''',
                    (profile['catalogue_version'], Jsonb(profile['catalogue'])))
        _insert_core_row(cur, profile)
        request = dict(universe_id=universe_id, role=role, legacy_persona_id=legacy_persona_id,
                       origin=origin, season=season, reviewer=reviewer, reason=reason)
        _receipt(cur, identity, 'adoption', 'adoption', 'adoption:' + role + ':' + legacy_persona_id,
                 season, request, state, profile, actor=reviewer)
        return profile


def link_role_change(cur, from_identity, to_identity, season, *, tick=None, reason):
    """D3: carry a personality to a new role as a new linked profile; the old one stays for history."""
    _text(reason, 'reason')
    with _savepoint(cur):
        for ident in sorted((from_identity, to_identity), key=lambda i: (i.role, i.persona_id)):
            _lock(cur, ident)
        source = read(cur, from_identity)
        if source is None:
            raise NotFoundError('role change needs a stored profile')
        if from_identity.role == to_identity.role:
            raise PersonalityError('role change needs a different role')
        if source['origin'] == 'unknown':
            raise PersonalityError('classify unknown identity before carrying it over')
        if tick is not None and (type(tick) is not int or not 1 <= tick <= 32):
            raise PersonalityError('tick must be an integer 1-32')
        if read(cur, to_identity) is not None:
            raise ConflictError('target role already has a profile')
        cur.execute('SELECT 1 FROM persona_sliders WHERE persona_type=%s AND persona_id=%s',
                    (to_identity.role, to_identity.persona_id))
        if cur.fetchone():
            raise ConflictError('unmanaged legacy identity collision')
        cur.execute('''SELECT 1 FROM persona_core_links WHERE from_type=%s AND from_id=%s AND to_type=%s''',
                    (from_identity.role, from_identity.persona_id, to_identity.role))
        if cur.fetchone():
            raise ConflictError('profile already carried over to that role')
        from newsroom.personas import load_traits
        carried = carry_over(source, to_identity, season, catalogue=load_traits())
        _insert_profile(cur, carried, season, 'role change from ' + from_identity.role)
        request = dict(from_role=from_identity.role, from_id=from_identity.persona_id,
                       to_role=to_identity.role, to_id=to_identity.persona_id,
                       season=season, tick=tick, reason=reason)
        source_event = 'role_link:' + from_identity.role + ':' + from_identity.persona_id
        _receipt(cur, to_identity, 'role_link', 'role_link', source_event, season, request,
                 source, carried, tick=tick)
        cur.execute('''INSERT INTO persona_core_links
            (universe_id,from_type,from_id,to_type,to_id,link_kind,effective_season,effective_tick,receipt)
            VALUES (%s,%s,%s,%s,%s,'role_change',%s,%s,%s)''',
            (to_identity.universe_id,from_identity.role,from_identity.persona_id,to_identity.role,
             to_identity.persona_id,season,tick,Jsonb(request)))
        return carried


_RECEIPT_COLUMNS = ('persona_type','persona_id','kind','event_id','source_event_id','season',
                    'effective_tick','seq','actor','request','before_state','after_state')


def _receipts(cur, pairs):
    if not pairs:
        return []
    clause = ' OR '.join(['(persona_type=%s AND persona_id=%s)'] * len(pairs))
    cur.execute('SELECT ' + ','.join(_RECEIPT_COLUMNS) + ' FROM persona_core_events WHERE ' + clause +
                ' ORDER BY season, effective_tick NULLS FIRST, seq',
                [v for pair in pairs for v in pair])
    return [dict(zip(_RECEIPT_COLUMNS, row)) for row in cur.fetchall()]


def _own_receipts(cur, identity):
    return _receipts(cur, [(identity.role, identity.persona_id)])


def history(cur, identity):
    """Receipts in (season, effective_tick NULLS FIRST, seq) order, reading across role links:
    the profiles this one was carried from (and theirs), and the role_link receipts of profiles
    it was carried to. Each receipt names its persona_type/persona_id."""
    pairs, frontier = [(identity.role, identity.persona_id)], [(identity.role, identity.persona_id)]
    while frontier:
        role, pid = frontier.pop()
        cur.execute('SELECT from_type,from_id FROM persona_core_links WHERE to_type=%s AND to_id=%s', (role, pid))
        for row in cur.fetchall():
            if tuple(row) not in pairs:
                pairs.append(tuple(row))
                frontier.append(tuple(row))
    rows = _receipts(cur, pairs)
    cur.execute('SELECT to_type,to_id FROM persona_core_links WHERE from_type=%s AND from_id=%s',
                (identity.role, identity.persona_id))
    outgoing = [tuple(r) for r in cur.fetchall()]
    rows += [r for r in _receipts(cur, outgoing) if r['kind'] == 'role_link']
    return sorted(rows, key=lambda r: (r['season'], -1 if r['effective_tick'] is None else r['effective_tick'], r['seq']))


def read_as_of(cur, identity, season, tick):
    """core.profile_as_of over this profile's own receipts; None when absent or not yet initialized."""
    current = read(cur, identity)
    if current is None:
        return None
    return profile_as_of(current, _own_receipts(cur, identity), season, tick)
