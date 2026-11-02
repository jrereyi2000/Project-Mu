"""PostgreSQL persistence using existing sliders/traits plus core sidecars.

Every mutation uses a savepoint, never commits. The caller owns the outer transaction.
No imports from db_handler; no automatic migrations, seed-on-read or connections.
"""
from contextlib import contextmanager
from copy import deepcopy
import uuid

from psycopg.types.json import Jsonb

from personality.core import (Identity, ConflictError,
                              apply_change, initialize, validate_profile, _text)


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
        cur.execute('''INSERT INTO persona_core_catalogues(version,catalogue) VALUES (%s,%s)
                       ON CONFLICT (version) DO NOTHING''',
                    (profile['catalogue_version'], Jsonb(profile['catalogue'])))
        _write_canonical(cur, profile)
        cur.execute('''INSERT INTO persona_core_profiles
            (persona_type,persona_id,universe_id,birth_key,origin,catalogue_version,revision,metadata)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
            (identity.role,identity.persona_id,identity.universe_id,identity.birth_key,
             origin,profile['catalogue_version'],profile['revision'],Jsonb(_metadata(profile))))
        for trait in profile['traits']:
            cur.execute('''INSERT INTO persona_trait_history
                (persona_type,persona_id,trait_id,added_season,reason) VALUES (%s,%s,%s,%s,%s)''',
                (identity.role,identity.persona_id,trait,season,'personality core initialization'))
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
    return None if row is None else Identity(universe_id,role,row[0])


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
        _write_canonical(cur,after)
        cur.execute('''UPDATE persona_core_profiles SET revision=%s,metadata=%s
                       WHERE persona_type=%s AND persona_id=%s''',
                    (after['revision'],Jsonb(_metadata(after)),identity.role,identity.persona_id))
        cur.execute('''INSERT INTO persona_core_events
            (persona_type,persona_id,event_id,source_event_id,season,request,before_state,after_state)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',
            (identity.role,identity.persona_id,event['event_id'],event['source_event_id'],event['season'],Jsonb(event),
             Jsonb(audit['before']),Jsonb(audit['after'])))
        if event['kind']=='trait':
            if event.get('remove'):
                cur.execute('''UPDATE persona_trait_history SET removed_season=%s
                    WHERE persona_type=%s AND persona_id=%s AND trait_id=%s AND removed_season IS NULL''',
                    (event['season'],identity.role,identity.persona_id,event['remove']))
            if event.get('add'):
                cur.execute('''INSERT INTO persona_trait_history
                    (persona_type,persona_id,trait_id,added_season,reason) VALUES (%s,%s,%s,%s,%s)''',
                    (identity.role,identity.persona_id,event['add'],event['season'],event['reason']))
        return after
