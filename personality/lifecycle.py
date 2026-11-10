"""Person lifecycle contracts (docs/done/PERSONALITY_DRAFT_INTEGRATION_PLAN.md §2).

Pure: no database, model calls, randomness or clock reads. Turns a save-reader snapshot plus reviewed
classification receipts (and optional staff records) into a reviewable inventory, then into a
`personality-lifecycle-v1` manifest of actions (initialize / bind / retain / pending / quarantine)
against an exported lifecycle state. Persistence lives in personality/lifecycle_store.py.

Identity rules (plan §2.1):
- A person's immutable `source_person_id` is a UUID allocated once at verified first entry; the core birth
  key is `source:<source_person_id>` and is never recomputed after a rename, trade, class or pgid change.
- Draft prospects adopt scouting's `prospect_uid` (engines/prospect_ids.py) as their source_person_id.
- Players on the starting roster derive it from universe + checkpoint season + pgid (a native source whose
  continuity is the verified starting snapshot). Later roster rows resolve by alias (handoff), never by
  allocation, so a drafted rookie keeps his prospect identity.
- Imported (CSV/OCR) classes need a durable sidecar of supplier IDs -> UUIDs (class_sidecar); a row
  without a supplier ID stays pending. Names, ranks, row indexes and teams are never identity.
- Unknown means unresolved: pending, never `ensure('unknown')` (plan Q1).
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import uuid

from engines import prospect_ids
from personality import core

SCHEMA = 'personality-lifecycle-v1'
SIDECAR_SCHEMA = 'personality-class-sidecar-v1'
LIFECYCLE_NS = uuid.uuid5(uuid.NAMESPACE_URL, 'project-mu:personality-lifecycle')
# Fixed phase order inside a season (spike, 2026-11-04): a season's offseason stages follow its playoffs and
# precede the next season's league_start/preseason. Never lexical order or wall-clock import time.
PHASES = ('league_start', 'preseason', 'regular_season', 'playoffs', 'resign', 'free_agency', 'draft', 'post_draft')
PLAYER_STATUSES = ('Draft', 'Signed', 'PracticeSquad', 'FreeAgent')
ROSTER_STATUSES = ('Signed', 'PracticeSquad', 'FreeAgent')
STAFF_ROLES = ('coach', 'gm', 'owner', 'scout')
ORIGINS = ('generated', 'real', 'unknown')
STATES = ('pending', 'ready', 'quarantined', 'archived')
ACTIONS = ('initialize', 'bind', 'retain', 'pending', 'quarantine', 'archive')
RECEIPT_FIELDS = ('origin', 'source_ref', 'source_sha256', 'reviewer', 'reason', 'reviewed_at', 'effective_at', 'scope')
REVIEW_FIELDS = ('reviewer', 'reviewed_manifest_sha256', 'decision', 'reason', 'reviewed_at')
SOURCE_PERSON = 'source-person'
DEFAULT_PGID_LIFETIME = '1'
VOLATILE_META = ('read_at', 'source', 'source_modified')
EXIT_OK, EXIT_INVALID, EXIT_UNRESOLVED, EXIT_CONFLICT = 0, 2, 3, 4


class LifecycleError(ValueError):
    """Invalid input or version (CLI exit 2)."""
    code = EXIT_INVALID


class StaleError(LifecycleError):
    """Stale state, conflicting batch, schema or profile failure (CLI exit 4)."""
    code = EXIT_CONFLICT


# ---------- canonical form ----------

def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def canonical_hash(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def snapshot_sha256(snapshot):
    """Hash of a reader snapshot without its volatile read timestamp/path fields."""
    snap = deepcopy(snapshot)
    meta = snap.get('meta')
    if isinstance(meta, dict):
        for key in VOLATILE_META:
            meta.pop(key, None)
    return canonical_hash(snap)


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise LifecycleError(f'{label} must be nonempty trimmed text')
    return value


def _int(value, label):
    if type(value) is not int:
        raise LifecycleError(f'{label} must be an integer')
    return value


def _uuid(value, label):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise LifecycleError(f'{label} must be a UUID') from None


# ---------- cutoffs and universes ----------

def validate_cutoff(cutoff):
    if not isinstance(cutoff, dict) or set(cutoff) != {'season', 'phase', 'sequence'}:
        raise LifecycleError('cutoff must be {season, phase, sequence}')
    _int(cutoff['season'], 'cutoff season')
    if cutoff['phase'] not in PHASES:
        raise LifecycleError(f'cutoff phase must be one of {", ".join(PHASES)}')
    if _int(cutoff['sequence'], 'cutoff sequence') < 0:
        raise LifecycleError('cutoff sequence must be >= 0')
    return dict(cutoff)


def cutoff_key(cutoff):
    validate_cutoff(cutoff)
    return (cutoff['season'], PHASES.index(cutoff['phase']), cutoff['sequence'])


def new_universe(league_id, initial_snapshot_sha256, start_season=2026, start_cutoff=None, *, universe_uuid=None,
                 pgid_lifetime=DEFAULT_PGID_LIFETIME):
    """A registration record: `mu:<uuid>`, bound to the league ID and the starting snapshot hash.

    An independent simulation fork gets a new universe by explicit declaration (plan Q3); resume/restore keep it."""
    universe = dict(universe_id='mu:' + str(universe_uuid or uuid.uuid4()), league_id=str(league_id),
                    initial_snapshot_sha256=initial_snapshot_sha256, start_season=start_season,
                    start_cutoff=start_cutoff or dict(season=start_season, phase='league_start', sequence=0),
                    manifest_version=SCHEMA, pgid_lifetime=pgid_lifetime)
    return validate_universe(universe)


def validate_universe(universe):
    required = {'universe_id', 'league_id', 'initial_snapshot_sha256', 'start_season', 'start_cutoff',
                'manifest_version'}
    if not isinstance(universe, dict) or not required <= set(universe):
        raise LifecycleError('universe needs ' + ', '.join(sorted(required)))
    if not re.fullmatch(r'mu:[0-9a-f-]{36}', str(universe['universe_id'])):
        raise LifecycleError('universe_id must be mu:<uuid>')
    _uuid(universe['universe_id'][3:], 'universe_id')
    _text(str(universe['league_id']), 'league_id')
    if not re.fullmatch(r'[0-9a-f]{64}', str(universe['initial_snapshot_sha256'])):
        raise LifecycleError('initial_snapshot_sha256 must be a sha256 hex digest')
    _int(universe['start_season'], 'start_season')
    validate_cutoff(universe['start_cutoff'])
    if universe['start_cutoff']['season'] != universe['start_season']:
        raise LifecycleError('start_cutoff season must equal start_season')
    if universe['manifest_version'] != SCHEMA:
        raise LifecycleError('unsupported manifest_version')
    _text(str(universe.get('pgid_lifetime', DEFAULT_PGID_LIFETIME)), 'pgid_lifetime')
    return universe


def pgid_namespace(universe):
    """pgid is a lifetime-scoped alias: a reused pgid needs a new lifetime and a reviewed mapping."""
    return 'save-pgid:' + str(universe.get('pgid_lifetime', DEFAULT_PGID_LIFETIME))


# ---------- identities ----------

def roster_person_id(universe_id, season, pgid):
    """Starting-roster players: derived from universe + checkpoint season + pgid (verified native source)."""
    return str(uuid.uuid5(LIFECYCLE_NS, f'{universe_id}|save-roster|{int(season)}|{int(pgid)}'))


def prospect_person_id(draft_season, pgid=None, prospect_key=None):
    """Adopts scouting's prospect_uid (plan §2.1): uuid5(MU_PROSPECT_NS, save-pgid:<class>:<pgid> | board:...)."""
    return str(prospect_ids.uid_from_birth_key(prospect_ids.birth_key(draft_season, pgid, prospect_key)))


def birth_key(source_person_id):
    return 'source:' + _uuid(source_person_id, 'source_person_id')


def identity(universe_id, role, source_person_id):
    return core.Identity(universe_id, role, birth_key(source_person_id))


# ---------- imported-class sidecars ----------

def class_sidecar(draft_season, source_ref, rows, existing=None, *, allocate=None):
    """A durable supplier-ID -> UUID sidecar for an imported (CSV/OCR) class.

    rows carry `source_id` (the supplier's immutable ID). Existing entries are kept exactly, so reordering
    or renaming rows never changes a person; a new supplier ID gets one new UUID (allocate() -> UUID, default
    uuid4, injectable for tests). Rows without a source_id are reported in `unmapped`, never keyed by
    name/rank/row index. Returns the new sidecar; the caller reviews and retains it."""
    allocate = allocate or uuid.uuid4
    sidecar = deepcopy(existing) if existing else dict(schema=SIDECAR_SCHEMA, draft_season=int(draft_season),
                                                       source_ref=source_ref, entries=[])
    validate_sidecar(sidecar)
    if sidecar['draft_season'] != int(draft_season):
        raise LifecycleError('sidecar belongs to another class')
    known = {e['source_id'] for e in sidecar['entries']}
    unmapped = []
    for index, row in enumerate(rows):
        sid = row.get('source_id')
        if sid is None or not str(sid).strip():
            unmapped.append(index)
            continue
        sid = str(sid).strip()
        if sid not in known:
            sidecar['entries'].append(dict(source_id=sid, source_person_id=str(allocate())))
            known.add(sid)
    sidecar['entries'].sort(key=lambda e: e['source_id'])
    validate_sidecar(sidecar)
    sidecar['unmapped'] = unmapped
    return sidecar


def validate_sidecar(sidecar):
    if not isinstance(sidecar, dict) or sidecar.get('schema') != SIDECAR_SCHEMA:
        raise LifecycleError('not a personality class sidecar')
    _int(sidecar.get('draft_season'), 'sidecar draft_season')
    seen_ids, seen_people = set(), set()
    for entry in sidecar.get('entries') or []:
        sid = _text(str(entry.get('source_id', '')), 'sidecar source_id')
        spid = _uuid(entry.get('source_person_id'), 'sidecar source_person_id')
        if sid in seen_ids or spid in seen_people:
            raise LifecycleError('sidecar maps one supplier ID or UUID twice')
        seen_ids.add(sid)
        seen_people.add(spid)
    return sidecar


def sidecar_person_id(sidecar, source_id):
    """The UUID for a supplier ID, or None (pending) when the sidecar doesn't map it."""
    if source_id is None:
        return None
    for entry in sidecar.get('entries') or []:
        if entry['source_id'] == str(source_id).strip():
            return entry['source_person_id']
    return None


# ---------- classification receipts ----------

def validate_receipt(receipt):
    if not isinstance(receipt, dict) or not set(RECEIPT_FIELDS) <= set(receipt):
        raise LifecycleError('classification receipt needs ' + ', '.join(RECEIPT_FIELDS))
    if receipt['origin'] not in ORIGINS:
        raise LifecycleError('receipt origin must be generated, real or unknown')
    for field in RECEIPT_FIELDS:
        _text(str(receipt[field]), 'receipt ' + field)
    if not re.fullmatch(r'(roster:\d{4}|class:\d{4}|staff:(coach|gm|owner|scout)|person:[a-z]+:[0-9a-f-]{36})',
                        receipt['scope']):
        raise LifecycleError(f'unsupported receipt scope {receipt["scope"]!r}')
    return receipt


def _receipts(classifications):
    receipts = (classifications or {}).get('receipts', [])
    if not isinstance(receipts, list):
        raise LifecycleError('classifications.receipts must be a list')
    by_scope = {}
    for r in receipts:
        by_scope.setdefault(validate_receipt(r)['scope'], []).append(r)
    return by_scope


def _classify(by_scope, scopes):
    """(origin, receipt, reason). Disagreeing receipts quarantine; none means pending."""
    found = [r for s in scopes for r in by_scope.get(s, [])]
    if not found:
        return None, None, 'no_classification_receipt'
    if len({r['origin'] for r in found}) > 1:
        return None, None, 'classification_conflict'
    return found[-1]['origin'], found[-1], None   # the most specific scope is listed last


def validate_review(review, manifest_sha256=None):
    if not isinstance(review, dict) or not set(REVIEW_FIELDS) <= set(review):
        raise LifecycleError('review receipt needs ' + ', '.join(REVIEW_FIELDS))
    for field in REVIEW_FIELDS:
        _text(str(review[field]), 'review ' + field)
    if review['decision'] != 'approve':
        raise LifecycleError('review decision must be approve')
    if manifest_sha256 is not None and review['reviewed_manifest_sha256'] != manifest_sha256:
        raise StaleError('review receipt names a different manifest')
    return review


# ---------- inventory ----------

def _person(role, spid, cutoff, aliases, source, *, origin=None, receipt=None, status='ready', reason=None,
            profile=None, resolve=None):
    person = dict(role=role, source_person_id=spid, first_seen=dict(cutoff), source=source,
                  aliases=sorted(aliases, key=lambda a: (a['namespace'], a['external_id'])),
                  classification=origin, receipt=receipt, status=status, reason=reason)
    if spid is not None:
        person['birth_key'] = birth_key(spid)
    if profile is not None:
        person['reviewed_profile'] = profile
    if resolve is not None:
        person['resolve'] = resolve
    return person


def _staff_people(staff, by_scope, cutoff, issues):
    people = []
    for index, entry in enumerate(staff or []):
        role = entry.get('role')
        if role not in STAFF_ROLES:
            issues.append(dict(kind='unsupported_staff_role', index=index, role=role))
            continue
        aliases = []
        for alias in entry.get('aliases') or []:
            aliases.append(dict(namespace=_text(str(alias['namespace']), 'alias namespace'),
                                external_id=_text(str(alias['external_id']), 'alias external_id')))
        source = dict(kind='staff', index=index, creation_receipt=entry.get('creation_receipt'))
        if not entry.get('source_person_id'):
            people.append(_person(role, None, cutoff, aliases, source, status='pending',
                                  reason='identity_unresolved'))
            continue
        spid = _uuid(entry['source_person_id'], 'staff source_person_id')
        aliases.append(dict(namespace=SOURCE_PERSON, external_id=spid))
        origin, receipt, why = _classify(by_scope, ['staff:' + role, f'person:{role}:{spid}'])
        profile = entry.get('profile')
        status, reason = 'ready', None
        if why == 'classification_conflict':
            status, reason = 'quarantined', why
        elif origin is None or origin == 'unknown':
            status, reason = 'pending', why or 'unknown_identity'
        elif origin == 'real' and not profile:
            status, reason = 'pending', 'real_profile_missing'      # real coaches wait for their reviewed manifest
        elif origin == 'generated' and profile:
            status, reason = 'quarantined', 'generated_with_supplied_profile'
        elif origin == 'generated' and not entry.get('creation_receipt'):
            status, reason = 'pending', 'missing_creation_receipt'
        people.append(_person(role, spid, cutoff, aliases, source, origin=origin if status != 'pending' else None,
                              receipt=receipt, status=status, reason=reason,
                              profile=profile if origin == 'real' else None))
    return people


def validate_inventory(snapshot, classifications, *, universe, cutoff, staff=None):
    """Complete inventory of the snapshot's Draft/Signed/PracticeSquad/FreeAgent people plus supplied staff.

    Rejects a league-stamp mismatch, and at the universe's start cutoff a snapshot that isn't the registered
    starting snapshot. Nothing is created here; unresolved people are pending, conflicts quarantined."""
    validate_universe(universe)
    cutoff = validate_cutoff(cutoff)
    if (snapshot.get('meta') or {}).get('game_year') != 27:
        raise LifecycleError('not a Madden 27 snapshot')
    league = (snapshot.get('franchise') or {}).get('league_id')
    if league is None or str(league) != str(universe['league_id']):
        raise LifecycleError(f'snapshot league {league!r} does not match universe league {universe["league_id"]!r}')
    if cutoff_key(cutoff) < cutoff_key(universe['start_cutoff']):
        raise LifecycleError('cutoff precedes the universe start')
    snap_hash = snapshot_sha256(snapshot)
    at_start = cutoff == universe['start_cutoff']
    if at_start and snap_hash != universe['initial_snapshot_sha256']:
        raise LifecycleError('starting cutoff needs the registered starting snapshot')
    by_scope = _receipts(classifications)
    season = cutoff['season']
    draft_season = season + 1        # load_save convention: a season's save carries the coming class
    pgid_ns = pgid_namespace(universe)
    issues, people = [], []
    players = snapshot.get('players') or []
    pgid_counts = {}
    for p in players:
        if p.get('pgid') is not None:
            pgid_counts[p['pgid']] = pgid_counts.get(p['pgid'], 0) + 1
    board = snapshot.get('draft_class')
    in_class = {d['pgid'] for d in board} if board else None
    for index, p in enumerate(players):
        status = p.get('status')
        pgid = p.get('pgid')
        if status not in PLAYER_STATUSES:
            issues.append(dict(kind='unsupported_status', index=index, status=status))
            continue
        source = dict(kind='save', status=status, pgid=pgid)
        if pgid is None:
            issues.append(dict(kind='missing_pgid', index=index, status=status))
            people.append(_person('player', None, cutoff, [], source, status='pending', reason='missing_pgid'))
            continue
        pgid_alias = dict(namespace=pgid_ns, external_id=str(int(pgid)))
        if pgid_counts[pgid] > 1:
            issues.append(dict(kind='duplicate_pgid', pgid=pgid))
            people.append(_person('player', None, cutoff, [pgid_alias], source, status='quarantined',
                                  reason='duplicate_pgid'))
            continue
        if status == 'Draft':
            if in_class is not None and pgid not in in_class:
                issues.append(dict(kind='draft_status_outside_class', pgid=pgid))
                people.append(_person('player', None, cutoff, [pgid_alias], source, status='pending',
                                      reason='draft_status_outside_class'))
                continue
            source['draft_season'] = draft_season
            spid = prospect_person_id(draft_season, pgid)
            scopes = [f'class:{draft_season}', f'person:player:{spid}']
        elif at_start:
            spid = roster_person_id(universe['universe_id'], season, pgid)
            scopes = [f'roster:{season}', f'person:player:{spid}']
        else:
            # Later checkpoints never allocate roster identities: they resolve by verified alias (handoff).
            people.append(_person('player', None, cutoff, [pgid_alias], source, status='pending',
                                  reason='resolve_by_alias', resolve=pgid_alias))
            continue
        origin, receipt, why = _classify(by_scope, scopes)
        aliases = [dict(namespace=SOURCE_PERSON, external_id=spid), pgid_alias]
        if why == 'classification_conflict':
            people.append(_person('player', spid, cutoff, aliases, source, status='quarantined', reason=why))
        elif origin is None or origin == 'unknown':
            people.append(_person('player', spid, cutoff, aliases, source, status='pending',
                                  reason=why or 'unknown_identity'))
        elif origin == 'real':
            # Every player is a generated fictional character (D12/D13); a real receipt is a review error.
            people.append(_person('player', spid, cutoff, aliases, source, status='quarantined',
                                  reason='players_are_generated'))
        else:
            people.append(_person('player', spid, cutoff, aliases, source, origin=origin, receipt=receipt))
    people.extend(_staff_people(staff, by_scope, cutoff, issues))
    _mark_collisions(people, issues)
    people.sort(key=_person_sort)
    issues.sort(key=canonical_json)
    inventory = dict(schema=SCHEMA, kind='inventory', universe=deepcopy(universe), cutoff=cutoff,
                     reader_version=(snapshot.get('meta') or {}).get('reader_version'),
                     input_hashes=dict(snapshot=snap_hash, classifications=canonical_hash(classifications or {}),
                                       staff=canonical_hash(staff or [])),
                     # The reviewed receipts themselves ride along: the applied batch is the durable record of
                     # what classified whom, and consumers (the scouting directors' staff:scout) read them there.
                     classifications=deepcopy(classifications or {}),
                     people=people, issues=issues)
    inventory['counts'] = _counts(people)
    return inventory


def _person_sort(p):
    return (p['role'], p.get('source_person_id') or '', canonical_json(p['aliases']), canonical_json(p['source']))


def _mark_collisions(people, issues):
    """Two people claiming one (role, namespace, external_id) alias, or one source ID in two roles."""
    owners = {}
    for i, p in enumerate(people):
        for a in p['aliases']:
            owners.setdefault((p['role'], a['namespace'], a['external_id']), set()).add(i)
    for key, idx in owners.items():
        if len(idx) > 1:
            issues.append(dict(kind='alias_collision', role=key[0], namespace=key[1], external_id=key[2]))
            for i in idx:
                if people[i]['status'] != 'quarantined':
                    people[i].update(status='quarantined', reason='alias_collision', classification=None)
    roles = {}
    for i, p in enumerate(people):
        if p.get('source_person_id'):
            roles.setdefault(p['source_person_id'], []).append(i)
    for spid, idx in roles.items():
        if len({people[i]['role'] for i in idx}) > 1:
            issues.append(dict(kind='cross_role_review', source_person_id=spid))
            for i in idx:
                if people[i]['role'] != 'player' and people[i]['status'] == 'ready':
                    # A human crossing roles gets a new profile linked through store.link_role_change, never a clone.
                    people[i].update(status='pending', reason='cross_role_review', classification=None)


def _counts(people, key='status'):
    out = {}
    for p in people:
        for name, value in (('by_' + key, p.get(key)), ('by_role', p['role']),
                            ('by_origin', p.get('classification') or 'none'),
                            ('by_source', p['source'].get('status') or p['source'].get('kind'))):
            bucket = out.setdefault(name, {})
            bucket[str(value)] = bucket.get(str(value), 0) + 1
    out['total'] = len(people)
    return out


# ---------- manifest ----------

def _preview(profile):
    """Restricted review preview: no catalogue, never exported to scouting/media."""
    return {k: deepcopy(profile[k]) for k in ('origin', 'persona_id', 'initialized_season', 'sliders', 'traits',
                                              'evidence')}


def profile_hash(profile):
    """Content hash of a core profile without its catalogue snapshot (the catalogue_version stands for it)."""
    return canonical_hash({k: v for k, v in profile.items() if k != 'catalogue'})


def _proposed(universe_id, person, season, catalogue):
    ident = identity(universe_id, person['role'], person['source_person_id'])
    if person['classification'] == 'generated':
        return core.initialize(ident, 'generated', season, catalogue=catalogue)
    reviewed = person['reviewed_profile']
    return core.initialize(ident, 'real', season, sliders=reviewed.get('sliders'), traits=reviewed.get('traits'),
                           evidence=reviewed.get('evidence'), catalogue=catalogue)


def validate_existing(existing):
    existing = existing or {}
    out = dict(universe_id=existing.get('universe_id'), people={}, bindings={}, legacy=set(), unmanaged=set())
    for p in existing.get('people') or []:
        out['people'][(p['role'], _uuid(p['source_person_id'], 'existing source_person_id'))] = p
    for b in existing.get('bindings') or []:
        if b.get('valid_to') is None:
            out['bindings'][(b['role'], b['namespace'], str(b['external_id']))] = str(b['source_person_id'])
    for role, ids in (existing.get('legacy_persona_ids') or {}).items():
        out['legacy'] |= {(role, i) for i in ids}
    for c in existing.get('core_profiles') or []:
        out['unmanaged'].add((c['role'], c['persona_id']))
    return out


def plan_lifecycle(inventory, existing, *, core_version, catalogue_version, catalogue=None):
    """Actions for every inventoried person against an exported lifecycle state (U2 export_state).

    Refuses a core/catalogue version other than the approved current one (no generator substitution)."""
    if inventory.get('schema') != SCHEMA or inventory.get('kind') != 'inventory':
        raise LifecycleError('not a lifecycle inventory')
    catalogue = deepcopy(catalogue if catalogue is not None else core.legacy.load_traits())
    if core_version != core.CORE_VERSION:
        raise LifecycleError(f'core version {core_version!r} is not the current {core.CORE_VERSION!r}')
    if catalogue_version != core.catalogue_version(catalogue):
        raise LifecycleError('catalogue version is not the current catalogue')
    universe = inventory['universe']
    uid = universe['universe_id']
    state = validate_existing(existing)
    if state['universe_id'] not in (None, uid):
        raise LifecycleError('existing export belongs to another universe')
    season = inventory['cutoff']['season']
    people, issues = [], deepcopy(inventory['issues'])
    for src in inventory['people']:
        p = deepcopy(src)
        p.pop('status', None)
        reason = src.get('reason')
        action = {'pending': 'pending', 'quarantined': 'quarantine'}.get(src['status'])
        if src['status'] == 'pending' and src.get('resolve'):
            r = src['resolve']
            spid = state['bindings'].get((src['role'], r['namespace'], r['external_id']))
            if spid:
                stored = state['people'].get((src['role'], spid))
                p.update(source_person_id=spid, birth_key=birth_key(spid), classification=(stored or {}).get(
                    'classification'), reason=None)
                p['aliases'] = sorted(p['aliases'] + [dict(namespace=SOURCE_PERSON, external_id=spid)],
                                      key=lambda a: (a['namespace'], a['external_id']))
                action = None
        if action is None:
            action, reason = _decide(uid, p, state, season, catalogue)
        p['action'], p['reason'] = action, reason
        if p.get('source_person_id'):
            p['persona_id'] = identity(uid, p['role'], p['source_person_id']).persona_id
        people.append(p)
    people.sort(key=_person_sort)
    manifest = dict(schema=SCHEMA, kind='manifest', visibility='restricted', universe=deepcopy(universe),
                    cutoff=deepcopy(inventory['cutoff']), reader_version=inventory.get('reader_version'),
                    input_hashes=dict(inventory['input_hashes'], inventory=canonical_hash(inventory)),
                    classifications=deepcopy(inventory.get('classifications') or {}),
                    core_version=core_version, catalogue_version=catalogue_version,
                    preconditions=dict(existing_sha256=canonical_hash(existing or {}),
                                       expected={f"{p['role']}:{p['source_person_id']}": p['expected']
                                                 for p in people if p.get('expected')}),
                    people=people, issues=issues)
    return finalize_manifest(manifest)


def _decide(uid, p, state, season, catalogue):
    """(action, reason) for a ready person; fills expected/proposed fields on p."""
    role, spid = p['role'], p['source_person_id']
    ident = identity(uid, role, spid)
    for a in p['aliases']:
        owner = state['bindings'].get((role, a['namespace'], a['external_id']))
        if owner is not None and owner != spid:
            return 'quarantine', 'alias_conflict'
    stored = state['people'].get((role, spid))
    if stored is None:
        if (role, ident.persona_id) in state['legacy']:
            return 'quarantine', 'legacy_collision'
        if (role, ident.persona_id) in state['unmanaged']:
            return 'quarantine', 'unmanaged_core_profile'
        profile = _proposed(uid, p, season, catalogue)
        p['proposed_profile'] = _preview(profile)
        p['generated_profile_hash'] = profile_hash(profile)
        p['proposed_profile_hash'] = profile_hash(profile)
        p['edited'] = False
        p.pop('reviewed_profile', None)
        return 'initialize', None
    p['expected'] = dict(revision=stored.get('revision', 0), profile_hash=stored.get('profile_hash'),
                         state=stored.get('state'))
    p.pop('reviewed_profile', None)
    if stored.get('classification') != p['classification']:
        return 'quarantine', 'classification_conflict'
    if stored.get('state') == 'quarantined':
        return 'quarantine', 'stored_quarantined'
    new = [a for a in p['aliases'] if (role, a['namespace'], a['external_id']) not in state['bindings']]
    return ('bind', None) if new else ('retain', None)


def finalize_manifest(manifest, catalogue=None):
    """Recomputes edited flags, proposed hashes, the action summary, counts and manifest_sha256.

    Run after JR edits a proposed profile: the hash changes and his review receipt must name the new one."""
    manifest = deepcopy(manifest)
    catalogue = deepcopy(catalogue if catalogue is not None else core.legacy.load_traits())
    uid = manifest['universe']['universe_id']
    season = manifest['cutoff']['season']
    for p in manifest['people']:
        if p.get('action') == 'initialize':
            profile, _ = _rebuild(uid, p, season, catalogue)
            p['proposed_profile_hash'] = profile_hash(profile)
            p['edited'] = p['proposed_profile_hash'] != p.get('generated_profile_hash')
    manifest['actions'] = sorted([p['action'], p['role'], p.get('source_person_id') or ''] for p in manifest['people'])
    manifest['counts'] = _counts(manifest['people'], 'action')
    manifest.pop('manifest_sha256', None)
    manifest['manifest_sha256'] = canonical_hash(manifest)
    return manifest


def _rebuild(uid, p, season, catalogue):
    """The full profile an initialize action will store (generator output, or JR's validated edit)."""
    ident = identity(uid, p['role'], p['source_person_id'])
    preview = p['proposed_profile']
    if preview.get('origin') != p['classification'] or preview.get('persona_id') != ident.persona_id:
        raise LifecycleError(f'proposed profile for {p["source_person_id"]} changed origin or identity')
    profile = core.initialize(ident, 'unknown', season, catalogue=catalogue)
    profile.update(origin=p['classification'], sliders=deepcopy(preview['sliders']), traits=list(preview['traits']),
                   evidence=deepcopy(preview.get('evidence') or {}))
    try:
        core.validate_profile(profile)
    except (core.PersonalityError, KeyError, TypeError, ValueError) as e:
        raise LifecycleError(f'invalid proposed profile for {p["source_person_id"]}: {e}') from None
    return profile, preview


def proposed_profile(manifest, person, catalogue=None):
    """The full validated core profile an initialize action stores (used by the store at apply)."""
    catalogue = deepcopy(catalogue if catalogue is not None else core.legacy.load_traits())
    return _rebuild(manifest['universe']['universe_id'], person, manifest['cutoff']['season'], catalogue)[0]


def validate_manifest(manifest, *, catalogue=None):
    """{valid, errors, manifest_sha256 (recomputed), stored_sha256, edited}. Never raises on bad content."""
    errors = []
    stored = manifest.get('manifest_sha256') if isinstance(manifest, dict) else None
    try:
        if manifest.get('schema') != SCHEMA or manifest.get('kind') != 'manifest':
            raise LifecycleError('not a lifecycle manifest')
        validate_universe(manifest['universe'])
        validate_cutoff(manifest['cutoff'])
        catalogue = deepcopy(catalogue if catalogue is not None else core.legacy.load_traits())
        if manifest['core_version'] != core.CORE_VERSION:
            errors.append('core version changed since preview')
        if manifest['catalogue_version'] != core.catalogue_version(catalogue):
            errors.append('catalogue changed since preview')
        keys = [_person_sort(p) for p in manifest['people']]
        if keys != sorted(keys):
            errors.append('people are not in canonical order')
        seen = set()
        for p in manifest['people']:
            if p.get('action') not in ACTIONS:
                errors.append(f'unsupported action {p.get("action")!r}')
            if p.get('source_person_id'):
                key = (p['role'], p['source_person_id'])
                if key in seen:
                    errors.append(f'duplicate person {key}')
                seen.add(key)
            if p.get('action') == 'initialize':
                if p.get('classification') not in ('generated', 'real'):
                    errors.append(f'initialize needs a generated or real classification: {p["source_person_id"]}')
                    continue
                validate_receipt(p.get('receipt'))
                if not errors:
                    _rebuild(manifest['universe']['universe_id'], p, manifest['cutoff']['season'], catalogue)
        recomputed = finalize_manifest(manifest, catalogue)['manifest_sha256'] if not errors else None
    except (LifecycleError, KeyError, TypeError, AttributeError) as e:
        errors.append(str(e) or e.__class__.__name__)
        recomputed = None
    edited = sorted(p['source_person_id'] for p in (manifest.get('people') or [])
                    if isinstance(p, dict) and p.get('action') == 'initialize'
                    and p.get('proposed_profile_hash') != p.get('generated_profile_hash'))
    if recomputed is not None and stored != recomputed:
        errors.append('manifest_sha256 does not match its content (rehash after editing)')
    return dict(valid=not errors, errors=errors, manifest_sha256=recomputed, stored_sha256=stored, edited=edited)


def unresolved(manifest):
    """People left pending or quarantined (CLI exit 3 when any)."""
    return [p for p in manifest['people'] if p['action'] in ('pending', 'quarantine')]


def compare_manifests(before, after):
    """Replay comparison: same canonical payload, or which people/actions changed."""
    def index(m):
        return {f"{p['role']}:{p.get('source_person_id') or canonical_json(p['aliases'])}": p for p in m['people']}
    b, a = index(before), index(after)
    changed = sorted(k for k in set(b) & set(a) if canonical_hash(b[k]) != canonical_hash(a[k]))
    diff = dict(same=before.get('manifest_sha256') == after.get('manifest_sha256'),
                hash_before=before.get('manifest_sha256'), hash_after=after.get('manifest_sha256'),
                added=sorted(set(a) - set(b)), removed=sorted(set(b) - set(a)), changed=changed,
                actions_before=before.get('counts', {}).get('by_action'),
                actions_after=after.get('counts', {}).get('by_action'))
    for field in ('universe', 'cutoff', 'core_version', 'catalogue_version', 'input_hashes'):
        if before.get(field) != after.get(field):
            diff.setdefault('header_changes', []).append(field)
    return diff


# ---------- handoff ----------

def validate_handoff(candidate, bindings):
    """Draft -> roster (or import) handoff: verified, pending, or conflict. Never a second profile.

    candidate: {role, from: {namespace, external_id}, to: {namespace, external_id}, evidence, receipt?}.
    evidence 'same_pgid_lifetime' needs from == to in a save-pgid namespace; 'import_receipt' needs a
    reviewed post-write receipt; 'name_match' is a review candidate only. Pick/team are context, not identity.
    bindings: [{role, namespace, external_id, source_person_id, valid_to}] (U2 export)."""
    role = candidate.get('role', 'player')
    live = {(b['role'], b['namespace'], str(b['external_id'])): str(b['source_person_id'])
            for b in bindings if b.get('valid_to') is None}
    frm, to = candidate['from'], candidate['to']
    origin = live.get((role, frm['namespace'], str(frm['external_id'])))
    target = live.get((role, to['namespace'], str(to['external_id'])))
    out = dict(status='pending', source_person_id=origin, reason=None)
    if origin is None:
        out['reason'] = 'origin_unresolved'
    elif target is not None and target != origin:
        out.update(status='conflict', reason='target_bound_to_another_person')
    elif candidate.get('evidence') == 'same_pgid_lifetime':
        if frm['namespace'].startswith('save-pgid:') and frm == to:
            out['status'] = 'verified'
        else:
            out['reason'] = 'pgid_lifetime_mismatch'
    elif candidate.get('evidence') == 'import_receipt':
        receipt = candidate.get('receipt') or {}
        if receipt.get('post_write_verified') is True and receipt.get('reviewer') and receipt.get('snapshot_sha256'):
            out['status'] = 'verified'
        else:
            out['reason'] = 'import_receipt_unverified'      # a donor report alone never binds personality
    elif candidate.get('evidence') == 'name_match':
        out['reason'] = 'name_match_is_candidate_only'
    else:
        out['reason'] = 'unsupported_evidence'
    return out
