"""Pure personality core. No database, model calls, feature formulas or random reads."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import date
import hashlib
import json
import uuid

from newsroom import personas as legacy

CORE_VERSION = 'personality-core-v1'
FEATURE_VIEW_VERSION = 'views-v2'   # D1: real profiles read in full (core extensions plan)
CHANGE_KINDS = ('evolution', 'override', 'adoption', 'role_link')
LEGACY_PREFIX = 'legacy:'
ROLES = frozenset(('writer', 'gm', 'player', 'coach', 'owner', 'scout'))
ORIGINS = frozenset(('generated', 'real', 'unknown'))


class PersonalityError(ValueError):
    pass


class ConflictError(PersonalityError):
    pass


class NotFoundError(PersonalityError):
    pass


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise PersonalityError(f'{label} must be nonempty trimmed text')
    return value


def _integer(value, label):
    if type(value) is not int:
        raise PersonalityError(f'{label} must be an integer')
    return value


@dataclass(frozen=True)
class Identity:
    """birth_key is caller-verified immutable identity, never name, team or roster row.

    Adopted legacy personas (writers, GMs) keep their legacy persona_id; their birth key is
    'legacy:<role>:<legacy_persona_id>', so the identity is the same however it is rebuilt.
    """
    universe_id: str
    role: str
    birth_key: str
    legacy_persona_id: str | None = None

    def __post_init__(self):
        _text(self.universe_id, 'universe_id')
        _text(self.birth_key, 'birth_key')
        if self.role not in ROLES:
            raise PersonalityError('unsupported role')
        prefix = LEGACY_PREFIX + self.role + ':'
        if self.legacy_persona_id is None and self.birth_key.startswith(prefix):
            object.__setattr__(self, 'legacy_persona_id', self.birth_key[len(prefix):])
        if self.legacy_persona_id is not None:
            _text(self.legacy_persona_id, 'legacy_persona_id')
            if len(self.legacy_persona_id) > 64 or self.birth_key != prefix + self.legacy_persona_id:
                raise PersonalityError('legacy persona ID does not match its birth key')

    @classmethod
    def adopted(cls, universe_id, role, legacy_persona_id):
        _text(legacy_persona_id, 'legacy_persona_id')
        return cls(universe_id, role, LEGACY_PREFIX + role + ':' + legacy_persona_id, legacy_persona_id)

    @property
    def persona_id(self):
        if self.legacy_persona_id is not None:
            return self.legacy_persona_id
        key = json.dumps([self.universe_id, self.role, self.birth_key], separators=(',', ':'))
        return str(uuid.uuid5(uuid.NAMESPACE_URL, 'project-mu:personality:' + key))


def _active_bounds():
    return [getattr(legacy, 'SLIDER_BUDGET_MIN', legacy.SLIDER_BUDGET),
            getattr(legacy, 'SLIDER_BUDGET_MAX', legacy.SLIDER_BUDGET)]


def _validate_links(links):
    if not isinstance(links, list):
        raise PersonalityError('links must be a list')
    for link in links:
        if not isinstance(link, dict) or set(link) != {'direction', 'role', 'persona_id'}:
            raise PersonalityError('invalid link')
        if link['direction'] not in ('from', 'to') or link['role'] not in ROLES:
            raise PersonalityError('invalid link')
        _text(link['persona_id'], 'link persona_id')


def catalogue_version(catalogue):
    return hashlib.sha256(json.dumps(catalogue, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def validate_profile(profile):
    p = profile
    identity = Identity(p['universe_id'], p['role'], p['birth_key'], p.get('legacy_persona_id'))
    if p['persona_id'] != identity.persona_id or p['core_version'] != CORE_VERSION:
        raise PersonalityError('identity or core version mismatch')
    if identity.legacy_persona_id is not None and p.get('legacy_persona_id') != identity.legacy_persona_id:
        raise PersonalityError('adopted profile must record its legacy persona ID')
    links = p.get('links', [])
    _validate_links(links)
    carried = any(link['direction'] == 'from' for link in links)
    if p['origin'] not in ORIGINS:
        raise PersonalityError('unsupported origin')
    _integer(p['initialized_season'], 'initialized_season')
    if _integer(p['revision'], 'revision') < 0:
        raise PersonalityError('negative revision')
    if p['last_changed_season'] is not None:
        if _integer(p['last_changed_season'], 'last_changed_season') < p['initialized_season']:
            raise PersonalityError('change predates initialization')
    sliders = p['sliders']
    if set(sliders) != set(legacy.SLIDERS) or any(type(v) is not int or not 0 <= v <= 10 for v in sliders.values()):
        raise PersonalityError('invalid six sliders')
    lo, hi = p['slider_bounds']
    if type(lo) is not int or type(hi) is not int or not 0 <= lo <= hi <= 60:
        raise PersonalityError('invalid slider policy')
    if not lo <= sum(sliders.values()) <= hi:
        raise PersonalityError('slider total outside recorded policy')
    cat = p['catalogue']
    if catalogue_version(cat) != p['catalogue_version']:
        raise PersonalityError('catalogue snapshot mismatch')
    traits = p['traits']
    if not isinstance(traits, list) or len(traits) != len(set(traits)):
        raise PersonalityError('duplicate or invalid traits')
    # A carried-over profile keeps only the traits valid for its new role; nothing is redrawn (D3).
    if not (3 <= len(traits) <= 5 if p['origin'] == 'generated' and not carried else 0 <= len(traits) <= 5):
        raise PersonalityError('invalid trait count for origin')
    categories = {}
    for tid in traits:
        if tid not in cat or cat[tid].get('roles', {}).get(p['role'], 0) <= 0:
            raise PersonalityError('trait not eligible for role')
        category = cat[tid]['category']
        categories[category] = categories.get(category, 0) + 1
        if categories[category] > 2 or set(cat[tid].get('conflicts', [])) & set(traits):
            raise PersonalityError('trait category overflow or conflict')
    for target, evidence in p['evidence'].items():
        if target not in {'trait:' + t for t in traits} | {'slider:' + s for s in legacy.SLIDERS}:
            raise PersonalityError('evidence target is not present')
        if evidence['confidence'] not in ('low', 'medium', 'high'):
            raise PersonalityError('invalid confidence')
        _text(evidence['source'], 'evidence source')
        date.fromisoformat(evidence['date'])
        _text(evidence['reviewer'], 'evidence reviewer')
        _text(evidence['reason'], 'evidence reason')
        features = evidence['features']
        if not isinstance(features, list) or not features or len(features) != len(set(features)):
            raise PersonalityError('invalid evidence permissions')
        for feature in features:
            _text(feature, 'feature')
        if evidence['confidence'] == 'low' and any(f != 'voice' for f in features):
            raise PersonalityError('weak evidence is wording-only')
    if p['origin'] == 'unknown' and (traits or any(v != 5 for v in sliders.values()) or p['evidence']):
        raise PersonalityError('unknown people must remain neutral')
    return True


def initialize(identity, origin, season, *, sliders=None, traits=None, evidence=None, catalogue=None):
    """Explicit construction only; repository initialization is insert-once, never a reseed."""
    if origin not in ORIGINS:
        raise PersonalityError('unsupported origin')
    catalogue = deepcopy(catalogue if catalogue is not None else legacy.load_traits())
    if origin == 'generated':
        if sliders is not None or traits is not None or evidence:
            raise PersonalityError('generated initialization uses the versioned seed only')
        seed = legacy.seed_persona(identity.role, CORE_VERSION + ':' + identity.persona_id, traits=catalogue)
        sliders, traits = seed['sliders'], seed['traits']
    else:
        sliders = dict.fromkeys(legacy.SLIDERS, 5) if sliders is None else deepcopy(sliders)
        traits = [] if traits is None else list(traits)
    return _build(identity, origin, season, sliders, traits, evidence, catalogue)


def _build(identity, origin, season, sliders, traits, evidence, catalogue, links=None):
    profile = dict(universe_id=identity.universe_id, role=identity.role, birth_key=identity.birth_key,
                   persona_id=identity.persona_id, origin=origin, core_version=CORE_VERSION,
                   initialized_season=season, revision=0, last_changed_season=None,
                   slider_bounds=_active_bounds(),
                   catalogue_version=catalogue_version(catalogue), catalogue=catalogue,
                   sliders=sliders, traits=traits, evidence=deepcopy(evidence or {}))
    if identity.legacy_persona_id is not None:
        profile['legacy_persona_id'] = identity.legacy_persona_id
    if links:
        profile['links'] = deepcopy(links)
    validate_profile(profile)
    return profile


def adopt(identity, origin, season, sliders, traits, *, catalogue=None):
    """Pure: a legacy persona's current sliders and traits as a core profile, kept as they are.

    Unlike initialize, a generated origin keeps the legacy (already seeded) values; nothing is redrawn.
    """
    if identity.legacy_persona_id is None:
        raise PersonalityError('adoption needs Identity.adopted')
    if origin not in ('generated', 'real'):
        raise PersonalityError('adopted personas are generated or real')
    catalogue = deepcopy(catalogue if catalogue is not None else legacy.load_traits())
    return _build(identity, origin, season, deepcopy(sliders), list(traits), None, catalogue)


def feature_view(profile, feature):
    """Missing -> explicitly neutral; invalid/stored-read errors must propagate, not reseed.

    D1 (views-v2): generated and reviewed real profiles are read in full by every feature;
    evidence is provenance (who reviewed what), not a per-feature permission. Unknown
    profiles are neutral by validation. Feature names stay policy namespaces, not formulas.
    """
    _text(feature, 'feature')
    neutral = dict.fromkeys(legacy.SLIDERS, 5)
    if profile is None:
        return dict(status='missing', origin=None, sliders=neutral, traits=[], revision=None,
                    persona_id=None, view_version=FEATURE_VIEW_VERSION)
    validate_profile(profile)
    if profile['origin'] == 'unknown':
        sliders, traits = neutral, []
    else:
        sliders, traits = deepcopy(profile['sliders']), list(profile['traits'])
    return dict(status='stored', origin=profile['origin'], sliders=sliders, traits=traits,
                revision=profile['revision'], persona_id=profile['persona_id'],
                catalogue_version=profile['catalogue_version'], core_version=profile['core_version'],
                view_version=FEATURE_VIEW_VERSION)


def apply_change(profile, event):
    """Validate an explicit reviewed candidate; no autonomous event-to-trait rule here.

    One slider step OR one trait add/drop/replacement per season. Caller persists
    returned audit atomically. Duplicate event replay is handled by the repository.
    """
    validate_profile(profile)
    _text(event['event_id'], 'event_id')
    _text(event['source_event_id'], 'source_event_id')
    _text(event['reason'], 'reason')
    _text(event['rule_version'], 'rule_version')
    season = _integer(event['season'], 'season')
    if _integer(event['expected_revision'], 'expected_revision') != profile['revision']:
        raise ConflictError('stale profile revision')
    if season < profile['initialized_season'] or (profile['last_changed_season'] is not None and season <= profile['last_changed_season']):
        raise ConflictError('change predates profile or annual change already used')
    if profile['origin'] == 'unknown':
        raise PersonalityError('classify unknown identity before evolution')
    if 'tick' in event and event['tick'] is not None:
        _tick(event['tick'])
    after = deepcopy(profile)
    # Evolution adopts the active validated band; before-state retains its old policy.
    # A DB still on the old constraint will reject the write atomically.
    after['slider_bounds'] = _active_bounds()
    kind = event['kind']
    if kind == 'slider':
        slider = event['slider']
        step = event['step']
        if slider not in legacy.SLIDERS or type(step) is not int or step not in (-1, 1):
            raise PersonalityError('slider change must be one step')
        after['sliders'][slider] += step
    elif kind == 'trait':
        remove, add = event.get('remove'), event.get('add')
        if not remove and not add:
            raise PersonalityError('empty trait change')
        if remove and remove == add:
            raise PersonalityError('trait replacement must change the profile')
        if remove:
            if remove not in after['traits']:
                raise PersonalityError('removed trait absent')
            after['traits'].remove(remove)
            after['evidence'].pop('trait:' + remove, None)
        if add:
            if add in after['traits']:
                raise PersonalityError('added trait already present')
            after['traits'].append(add)
    else:
        raise PersonalityError('unsupported change kind')
    # D1: no real-person evolution gate. Evidence passed with a change is still recorded
    # under its target as provenance.
    target = 'slider:' + event['slider'] if kind == 'slider' else ('trait:' + event['add'] if event.get('add') else None)
    if target and event.get('evidence'):
        after['evidence'][target] = deepcopy(event['evidence'])
    after['revision'] += 1
    after['last_changed_season'] = season
    validate_profile(after)
    audit = dict(event=deepcopy(event), before=deepcopy(profile), after=deepcopy(after))
    return after, audit


def _tick(value):
    if type(value) is not int or not 1 <= value <= 32:
        raise PersonalityError('tick must be an integer 1-32')
    return value


def apply_override(profile, request):
    """JR's audited override after first use (PERSONALITY_PLAN D4): outside the annual slot.

    The result must pass the same validation as any profile (Q3). last_changed_season is
    untouched; revision goes up by one. Returns (after, audit).
    """
    validate_profile(profile)
    _text(request['override_id'], 'override_id')
    if request['actor'] != 'JR':
        raise PersonalityError('only JR can override a profile')
    reason = _text(request['reason'], 'reason')
    season = _integer(request['season'], 'season')
    _tick(request['tick'])
    if _integer(request['expected_revision'], 'expected_revision') != profile['revision']:
        raise ConflictError('stale profile revision')
    if season < profile['initialized_season']:
        raise ConflictError('override predates profile')
    if profile['origin'] == 'unknown':
        raise PersonalityError('classify unknown identity before overriding it')
    sliders = request.get('sliders') or {}
    add, remove = list(request.get('add_traits') or []), list(request.get('remove_traits') or [])
    if not isinstance(sliders, dict) or set(sliders) - set(legacy.SLIDERS):
        raise PersonalityError('unknown slider')
    if len(set(add)) != len(add) or len(set(remove)) != len(remove) or set(add) & set(remove):
        raise PersonalityError('duplicate or contradictory trait override')
    stamp = request.get('date') or date.today().isoformat()
    provenance = dict(source='override:' + request['override_id'], date=stamp, confidence='high',
                      reviewer='JR', reason=reason, features=['all'])
    after = deepcopy(profile)
    after['slider_bounds'] = _active_bounds()
    for name, value in sliders.items():
        if type(value) is not int or not 0 <= value <= 10:
            raise PersonalityError('slider values are integers 0-10')
        if value != after['sliders'][name] and 'slider:' + name in after['evidence']:
            after['evidence']['slider:' + name] = dict(provenance)
        after['sliders'][name] = value
    for trait in remove:
        if trait not in after['traits']:
            raise PersonalityError('removed trait absent')
        after['traits'].remove(trait)
        after['evidence'].pop('trait:' + trait, None)
    for trait in add:
        if trait in after['traits']:
            raise PersonalityError('added trait already present')
        after['traits'].append(trait)
        after['evidence']['trait:' + trait] = dict(provenance)
    if after['sliders'] == profile['sliders'] and after['traits'] == profile['traits']:
        raise PersonalityError('override must change the profile')
    after['revision'] += 1
    validate_profile(after)
    audit = dict(kind='override', request=deepcopy(request), before=deepcopy(profile), after=deepcopy(after))
    return after, audit


def carry_over(profile, new_identity, season, *, catalogue=None):
    """D3: a role change carries the personality over to a new linked profile.

    Sliders copied exactly; traits kept only where the catalogue gives the new role odds;
    nothing is redrawn. The old profile is left as it is.
    """
    validate_profile(profile)
    _integer(season, 'season')
    if new_identity.universe_id != profile['universe_id']:
        raise PersonalityError('role change stays inside one universe')
    if new_identity.role == profile['role']:
        raise PersonalityError('role change needs a different role')
    if profile['origin'] == 'unknown':
        raise PersonalityError('classify unknown identity before carrying it over')
    if season < profile['initialized_season']:
        raise ConflictError('role change predates profile')
    catalogue = deepcopy(catalogue if catalogue is not None else profile['catalogue'])
    kept = [t for t in profile['traits'] if catalogue.get(t, {}).get('roles', {}).get(new_identity.role, 0) > 0]
    evidence = {k: deepcopy(v) for k, v in profile['evidence'].items()
                if k.startswith('slider:') or k[len('trait:'):] in kept}
    links = [dict(direction='from', role=profile['role'], persona_id=profile['persona_id'])]
    return _build(new_identity, profile['origin'], season, deepcopy(profile['sliders']), kept,
                  evidence, catalogue, links)


def _position(receipt):
    tick = receipt.get('effective_tick')
    return (receipt['season'], 1 if tick is None else tick)


def profile_as_of(current, receipts, season, tick):
    """The profile as it was at (season, tick), rebuilt from receipts.

    receipts are persona_core_events rows (dicts with season, effective_tick, seq,
    before_state, after_state) sorted by (season, effective_tick NULLS FIRST, seq).
    A receipt without a tick counts as tick 1 of its season. tick None means the season's end.
    """
    if current is None:
        return None
    _integer(season, 'season')
    target = (season, 33 if tick is None else _tick(tick))
    if season < current['initialized_season']:
        return None
    chosen = None
    for receipt in receipts:
        if _position(receipt) <= target:
            chosen = receipt
    if chosen is not None:
        return deepcopy(chosen['after_state'])
    if receipts:
        return deepcopy(receipts[0]['before_state'])
    return deepcopy(current)
