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
ROLES = frozenset(('writer', 'gm', 'player', 'coach', 'owner', 'scout'))
ORIGINS = frozenset(('generated', 'real', 'unknown'))


class PersonalityError(ValueError):
    pass


class ConflictError(PersonalityError):
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
    """birth_key is caller-verified immutable identity, never name, team or roster row."""
    universe_id: str
    role: str
    birth_key: str

    def __post_init__(self):
        _text(self.universe_id, 'universe_id')
        _text(self.birth_key, 'birth_key')
        if self.role not in ROLES:
            raise PersonalityError('unsupported role')

    @property
    def persona_id(self):
        key = json.dumps([self.universe_id, self.role, self.birth_key], separators=(',', ':'))
        return str(uuid.uuid5(uuid.NAMESPACE_URL, 'project-mu:personality:' + key))


def catalogue_version(catalogue):
    return hashlib.sha256(json.dumps(catalogue, sort_keys=True, separators=(',', ':'),
                                    allow_nan=False).encode()).hexdigest()


def validate_profile(profile):
    p = profile
    identity = Identity(p['universe_id'], p['role'], p['birth_key'])
    if p['persona_id'] != identity.persona_id or p['core_version'] != CORE_VERSION:
        raise PersonalityError('identity or core version mismatch')
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
    if not (3 <= len(traits) <= 5 if p['origin'] == 'generated' else 0 <= len(traits) <= 5):
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
    profile = dict(universe_id=identity.universe_id, role=identity.role, birth_key=identity.birth_key,
                   persona_id=identity.persona_id, origin=origin, core_version=CORE_VERSION,
                   initialized_season=season, revision=0, last_changed_season=None,
                   slider_bounds=[getattr(legacy, 'SLIDER_BUDGET_MIN', legacy.SLIDER_BUDGET),
                                  getattr(legacy, 'SLIDER_BUDGET_MAX', legacy.SLIDER_BUDGET)],
                   catalogue_version=catalogue_version(catalogue), catalogue=catalogue,
                   sliders=sliders, traits=traits, evidence=deepcopy(evidence or {}))
    validate_profile(profile)
    return profile


def feature_view(profile, feature):
    """Missing -> explicitly neutral; invalid/stored-read errors must propagate, not reseed.

    Eligibility is provenance permission, not approval of a feature's coefficients.
    Existing real-person traits/sliders without scoped review are excluded.
    """
    _text(feature, 'feature')
    neutral = dict.fromkeys(legacy.SLIDERS, 5)
    if profile is None:
        return dict(status='missing', sliders=neutral, traits=[], revision=None, persona_id=None)
    validate_profile(profile)
    if profile['origin'] == 'generated':
        sliders, traits = deepcopy(profile['sliders']), list(profile['traits'])
    else:
        permitted = {k for k, e in profile['evidence'].items() if feature in e['features']}
        sliders = {s: profile['sliders'][s] if 'slider:' + s in permitted else 5 for s in legacy.SLIDERS}
        traits = [t for t in profile['traits'] if 'trait:' + t in permitted]
    return dict(status='stored', origin=profile['origin'], sliders=sliders, traits=traits,
                revision=profile['revision'], persona_id=profile['persona_id'],
                catalogue_version=profile['catalogue_version'], core_version=profile['core_version'])


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
    after = deepcopy(profile)
    # Evolution adopts the active validated band; before-state retains its old policy.
    # A DB still on the old constraint will reject the write atomically.
    after['slider_bounds'] = [getattr(legacy, 'SLIDER_BUDGET_MIN', legacy.SLIDER_BUDGET),
                              getattr(legacy, 'SLIDER_BUDGET_MAX', legacy.SLIDER_BUDGET)]
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
    # A real person's changed field requires an explicit reviewed simulation-event
    # record; old public evidence cannot grant new consequential permissions.
    target = 'slider:' + event['slider'] if kind == 'slider' else ('trait:' + event['add'] if event.get('add') else None)
    if target and profile['origin'] == 'real':
        proof = event.get('evidence')
        if not proof or proof.get('source') != 'simulation:' + event['source_event_id']:
            raise PersonalityError('real-person evolution needs scoped simulation evidence')
        after['evidence'][target] = deepcopy(proof)
    after['revision'] += 1
    after['last_changed_season'] = season
    validate_profile(after)
    audit = dict(event=deepcopy(event), before=deepcopy(profile), after=deepcopy(after))
    return after, audit
