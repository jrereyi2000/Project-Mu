"""Personality contract policy (docs/done/PERSONALITY_CONTRACT_POLICY_PLAN.md §2.1-§2.2, U1).

Pure: no database, no model calls, no randomness. Turns a player's stored personality (the core's `contracts` feature
view, personality.core.feature_view) and verified contract context (career, earned security, his own one-way view of
each team) into one bounded additive delta on free_agency_engine's offer utility. The engines own pricing, legality,
counters and execution; this module owns only the delta and its explanation.

  ambition   MONEY_FIRST   A = k (F - C)                                  (F financial, C contention, unrounded)
             RING_CHASER   A = k (C - F) only when career established, earned security True (career_earnings.
                           is_secure), the destination is a certified contender (C >= contender_floor; C comes from
                           the batch's frozen team snapshot, so offer money never moves it) and the role is usable
                           (opportunity >= the situational value: depth 1 or 2, never a hard-blocked backup).
                           Any unknown gate -> 0. Both traits at once -> 0 (one ambition path).
  attachment LOYAL         L = attachment_bonus[tier] on the current team only, with an owner-supplied earned tier,
                           role and treatment satisfactory and explicitly no unresolved breach (all known).
  home       HOMETOWN_GUY  H = home_bonus on a verified meaningful home-region match. Home never grants trust.
  grudge     GRUDGE_HOLDER G = grudge_penalty when the relationships owner certifies an unresolved qualifying grudge
                           toward this destination (a loss or a rejected offer alone is not one).

  raw = A + L + H - G;  delta = clamp(raw, -aggregate_cap, +aggregate_cap);  U = base U + delta (U is not clamped to 1).

Trial settings (TRIAL_CONFIG: k .10, cap .08, attachment 0/.015/.035/.055, home .01, grudge .03, contender floor .70)
reproduce a review mechanism; they are not calibrated production constants. Off is the default. No slider, warmth,
sociability, confidence or drive term; LEGACY_BUILDER / SPOTLIGHT_SEEKER stay numeric-neutral.
"""
from copy import deepcopy
import hashlib
import json
import math

POLICY_VERSION = 'contract-policy-v1'
CONTEXT_VERSION = 'contract-context-v1'
MODES = ('off', 'shadow', 'active')
TIERS = ('none', 'weak', 'established', 'strong')
TEAM_TIERS = TIERS + ('unknown',)
FAMILY_TRAITS = {'ambition': ('MONEY_FIRST', 'RING_CHASER'),
                 'relationship': ('LOYAL', 'HOMETOWN_GUY', 'GRUDGE_HOLDER')}
CONFIG_KEYS = ('version', 'mode', 'ambition_enabled', 'relationship_enabled', 'coefficient', 'aggregate_cap',
               'contender_floor', 'attachment_bonus', 'home_bonus', 'grudge_penalty')
TRIAL_CONFIG = {'version': POLICY_VERSION + '-trial', 'mode': 'off', 'ambition_enabled': True,
                'relationship_enabled': False, 'coefficient': 0.10, 'aggregate_cap': 0.08, 'contender_floor': 0.70,
                'attachment_bonus': {'none': 0.0, 'weak': 0.015, 'established': 0.035, 'strong': 0.055},
                'home_bonus': 0.01, 'grudge_penalty': 0.03}
TEAM_KEYS = ('attachment', 'role_satisfactory', 'treatment_satisfactory', 'unresolved_breach', 'grudge',
             'source_event_ids', 'relationship_version', 'attachment_as_of', 'meaningful_home_match',
             'home_source_ids')
PART_KEYS = ('financial', 'opportunity', 'contention', 'scheme')


class ContractPolicyError(ValueError):
    pass


# ----------------------------------------------------------------- helpers ----
def canonical_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(',', ':'), default=str)


def canonical_hash(obj):
    """sha256 of the canonical JSON: the fingerprint receipts, stale-score checks and replay use."""
    return hashlib.sha256(canonical_json(obj).encode()).hexdigest()


def _finite(value, label, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ContractPolicyError(f'{label} must be a finite number')
    if nonnegative and value < 0:
        raise ContractPolicyError(f'{label} must be >= 0')
    return float(value)


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ContractPolicyError(f'{label} must be nonempty text')
    return value


def _int(value, label, nullable=False):
    if value is None and nullable:
        return None
    if type(value) is not int:
        raise ContractPolicyError(f'{label} must be an integer')
    return value


def _tri(value, label):
    if value is not None and not isinstance(value, bool):
        raise ContractPolicyError(f'{label} must be true, false or null')
    return value


def _ids(value, label):
    if not isinstance(value, list) or not all(isinstance(v, str) and v for v in value):
        raise ContractPolicyError(f'{label} must be a list of nonempty strings')
    return list(value)


def _season_of(stamp, label):
    """Leading season of an as-of text ('2031', '2031-12', '2031:OFFSEASON'): the part future checks compare."""
    _text(stamp, label)
    digits = ''
    for ch in stamp:
        if not ch.isdigit():
            break
        digits += ch
    if not digits:
        raise ContractPolicyError(f'{label} must start with a season')
    return int(digits)


# ------------------------------------------------------------------ config ----
def validate_contract_policy(config):
    """Raise ContractPolicyError unless config is a complete, finite, server-owned policy config."""
    from engines.free_agency_engine import W_DEFAULT
    if not isinstance(config, dict) or set(config) != set(CONFIG_KEYS):
        raise ContractPolicyError(f'config must have exactly {", ".join(CONFIG_KEYS)}')
    _text(config['version'], 'version')
    if config['mode'] not in MODES:
        raise ContractPolicyError(f'mode must be one of {", ".join(MODES)}')
    for k in ('ambition_enabled', 'relationship_enabled'):
        if not isinstance(config[k], bool):
            raise ContractPolicyError(f'{k} must be a boolean')
    for k in ('coefficient', 'aggregate_cap', 'contender_floor', 'home_bonus', 'grudge_penalty'):
        _finite(config[k], k, nonnegative=True)
    if config['coefficient'] >= min(W_DEFAULT['money'], W_DEFAULT['team']):
        raise ContractPolicyError('coefficient must stay below min(W money, W team) so effective weights stay positive')
    bonus = config['attachment_bonus']
    if not isinstance(bonus, dict) or set(bonus) != set(TIERS):
        raise ContractPolicyError(f'attachment_bonus must have exactly {", ".join(TIERS)}')
    for t in TIERS:
        _finite(bonus[t], f'attachment_bonus.{t}', nonnegative=True)
    if any(bonus[a] > bonus[b] for a, b in zip(TIERS, TIERS[1:])):
        raise ContractPolicyError('attachment_bonus must not fall as the tier rises')


def trial_config(**overrides):
    """A copy of TRIAL_CONFIG with overrides, validated (offline trials and tests only)."""
    cfg = deepcopy(TRIAL_CONFIG)
    cfg.update(deepcopy(overrides))
    validate_contract_policy(cfg)
    return cfg


# ----------------------------------------------------------------- context ----
def unknown_team():
    """The explicit unknown record for a team the relationships provider said nothing about."""
    return {'attachment': 'unknown', 'role_satisfactory': None, 'treatment_satisfactory': None,
            'unresolved_breach': None, 'grudge': None, 'source_event_ids': [], 'relationship_version': 'none',
            'attachment_as_of': '0', 'meaningful_home_match': None, 'home_source_ids': []}


def normalize_contract_context(context):
    """A validated copy of a context v1 with int team keys (JSON decimal-string keys normalized once)."""
    if not isinstance(context, dict):
        raise ContractPolicyError('context must be an object')
    need = ('universe_id', 'player_id', 'persona_id', 'revision', 'as_of', 'batch_id', 'context_version', 'career',
            'security', 'current_team_id', 'teams')
    missing = [k for k in need if k not in context]
    if missing:
        raise ContractPolicyError(f'context is missing {", ".join(missing)}')
    c = deepcopy(context)
    _text(c['universe_id'], 'universe_id')
    _int(c['player_id'], 'player_id')
    if c['persona_id'] is not None:
        _text(c['persona_id'], 'persona_id')
    _int(c['revision'], 'revision', nullable=True)
    if (c['persona_id'] is None) != (c['revision'] is None):
        raise ContractPolicyError('persona_id and revision are both set or both null')
    a = c['as_of']
    if not isinstance(a, dict) or set(a) != {'season', 'week', 'stage'}:
        raise ContractPolicyError('as_of must be {season, week, stage}')
    _int(a['season'], 'as_of.season')
    _int(a['week'], 'as_of.week')
    _text(a['stage'], 'as_of.stage')
    _text(c['batch_id'], 'batch_id')
    _text(c['context_version'], 'context_version')
    career = c['career']
    if not isinstance(career, dict) or set(career) != {'established', 'source_ids', 'version'}:
        raise ContractPolicyError('career must be {established, source_ids, version}')
    _tri(career['established'], 'career.established')
    _ids(career['source_ids'], 'career.source_ids')
    _text(career['version'], 'career.version')
    sec = c['security']
    if not isinstance(sec, dict) or set(sec) != {'secure', 'earned_share', 'complete', 'through_season',
                                                 'source_ids', 'version'}:
        raise ContractPolicyError('security must be {secure, earned_share, complete, through_season, source_ids, '
                                  'version}')
    _tri(sec['secure'], 'security.secure')
    if sec['earned_share'] is not None:
        _finite(sec['earned_share'], 'security.earned_share', nonnegative=True)
    _tri(sec['complete'], 'security.complete')
    if _int(sec['through_season'], 'security.through_season') > a['season']:
        raise ContractPolicyError('security is dated after the decision (future source)')
    _ids(sec['source_ids'], 'security.source_ids')
    _text(sec['version'], 'security.version')
    _int(c['current_team_id'], 'current_team_id', nullable=True)
    if not isinstance(c['teams'], dict):
        raise ContractPolicyError('teams must be an object')
    teams = {}
    for key, rec in c['teams'].items():
        if isinstance(key, str) and key.isdecimal():
            tid = int(key)
        elif type(key) is int:
            tid = key
        else:
            raise ContractPolicyError(f'team key {key!r} must be an int or a decimal string')
        if tid in teams:
            raise ContractPolicyError(f'team {tid} appears twice')
        if not isinstance(rec, dict) or set(rec) != set(TEAM_KEYS):
            raise ContractPolicyError(f'team {tid} must have exactly {", ".join(TEAM_KEYS)}')
        if rec['attachment'] not in TEAM_TIERS:
            raise ContractPolicyError(f'team {tid} attachment must be one of {", ".join(TEAM_TIERS)}')
        for k in ('role_satisfactory', 'treatment_satisfactory', 'unresolved_breach', 'grudge',
                  'meaningful_home_match'):
            _tri(rec[k], f'team {tid} {k}')
        _ids(rec['source_event_ids'], f'team {tid} source_event_ids')
        _ids(rec['home_source_ids'], f'team {tid} home_source_ids')
        _text(rec['relationship_version'], f'team {tid} relationship_version')
        if _season_of(rec['attachment_as_of'], f'team {tid} attachment_as_of') > a['season']:
            raise ContractPolicyError(f'team {tid} relationship evidence is dated after the decision (future source)')
        teams[tid] = rec
    c['teams'] = teams
    return c


def validate_contract_context(context):
    normalize_contract_context(context)


def neutral_context(universe_id, player_id, as_of, batch_id, *, persona_id=None, revision=None,
                    current_team_id=None, team_ids=()):
    """Every input explicitly unknown: what a missing provider yields (no effect can activate from it)."""
    return normalize_contract_context({
        'universe_id': universe_id, 'player_id': player_id, 'persona_id': persona_id, 'revision': revision,
        'as_of': dict(as_of), 'batch_id': batch_id, 'context_version': CONTEXT_VERSION,
        'career': {'established': None, 'source_ids': [], 'version': 'none'},
        'security': {'secure': None, 'earned_share': None, 'complete': None, 'through_season': as_of['season'],
                     'source_ids': [], 'version': 'none'},
        'current_team_id': current_team_id, 'teams': {int(t): unknown_team() for t in team_ids}})


# ------------------------------------------------------------------ policy ----
def _neutral_result(config, reasons, gates=None):
    return {'terms': {'ambition': 0.0, 'attachment': 0.0, 'home': 0.0, 'grudge': 0.0}, 'raw_delta': 0.0,
            'capped_delta': 0.0, 'cap_applied': False, 'gates': gates or {}, 'reasons': sorted(set(reasons)),
            'policy_version': config['version'], 'mode': config['mode']}


def contract_personality_delta(parts, view, context, config):
    """parts: {financial, opportunity, contention, scheme (unrounded floats), team_id}; view: the core's contracts
    feature view (None = missing); context: context v1; config: a validated policy config. Returns {terms:
    {ambition, attachment, home, grudge (signed, <= 0)}, raw_delta, capped_delta, cap_applied, gates, reasons,
    policy_version, mode}. Off mode returns zeros with reason mode_off (nothing is evaluated)."""
    validate_contract_policy(config)
    for k in PART_KEYS:
        _finite(parts.get(k), f'parts.{k}')
    team_id = parts.get('team_id')
    if config['mode'] == 'off':
        return _neutral_result(config, ['mode_off'])
    ctx = normalize_contract_context(context)
    if view is None or view.get('status') == 'missing':
        return _neutral_result(config, ['profile_missing'])
    if view.get('status') != 'stored' or 'traits' not in view:
        raise ContractPolicyError('view must be the core contracts feature view')
    if view.get('persona_id') != ctx['persona_id'] or view.get('revision') != ctx['revision']:
        raise ContractPolicyError('view identity/revision does not match the context')
    traits = set(view['traits'])
    F, O, C = float(parts['financial']), float(parts['opportunity']), float(parts['contention'])
    k = config['coefficient']
    terms = {'ambition': 0.0, 'attachment': 0.0, 'home': 0.0, 'grudge': 0.0}
    gates, reasons = {}, []
    if view.get('origin') == 'unknown':
        reasons.append('origin_unknown')

    # ambition: one path
    money, ring = 'MONEY_FIRST' in traits, 'RING_CHASER' in traits
    if (money or ring) and not config['ambition_enabled']:
        reasons.append('ambition_disabled')
    elif money and ring:
        reasons.append('ambition_conflict')
    elif money:
        terms['ambition'] = k * (F - C)
        reasons.append('money_first')
    elif ring:
        from engines.free_agency_engine import SITUATIONAL_U
        g = {'established_career': ctx['career']['established'], 'secure': ctx['security']['secure'],
             'certified_contender': C >= config['contender_floor'], 'usable_role': O >= SITUATIONAL_U}
        gates['ring_chaser'] = g
        if all(v is True for v in g.values()):
            terms['ambition'] = k * (C - F)
            reasons.append('ring_chaser')
        else:
            reasons.extend(f'ring_gate_{n}_{"unknown" if v is None else "false"}' for n, v in sorted(g.items())
                           if v is not True)

    # relationship family
    rel = [t for t in FAMILY_TRAITS['relationship'] if t in traits]
    if rel and not config['relationship_enabled']:
        reasons.append('relationships_disabled')
    elif rel:
        team = ctx['teams'].get(team_id) if team_id is not None else None
        if team is None:
            team = unknown_team()
            reasons.append('team_context_missing')
        if 'LOYAL' in traits:
            current = team_id is not None and team_id == ctx['current_team_id']
            g = {'current_team': current, 'tier': team['attachment'], 'role_satisfactory': team['role_satisfactory'],
                 'treatment_satisfactory': team['treatment_satisfactory'],
                 'no_unresolved_breach': None if team['unresolved_breach'] is None else not team['unresolved_breach']}
            gates['loyal'] = g
            ok = (current and team['attachment'] in TIERS and g['role_satisfactory'] is True
                  and g['treatment_satisfactory'] is True and g['no_unresolved_breach'] is True)
            if ok:
                terms['attachment'] = config['attachment_bonus'][team['attachment']]
                reasons.append(f'loyal_{team["attachment"]}')
            else:
                reasons.append('loyal_gate_closed')
        if 'HOMETOWN_GUY' in traits:
            gates['hometown'] = {'meaningful_home_match': team['meaningful_home_match']}
            if team['meaningful_home_match'] is True:
                terms['home'] = config['home_bonus']
                reasons.append('hometown_match')
            else:
                reasons.append('hometown_unverified')
        if 'GRUDGE_HOLDER' in traits:
            gates['grudge'] = {'certified_grudge': team['grudge']}
            if team['grudge'] is True:
                terms['grudge'] = -config['grudge_penalty']
                reasons.append('grudge_certified')
            else:
                reasons.append('grudge_uncertified')

    raw = terms['ambition'] + terms['attachment'] + terms['home'] + terms['grudge']
    cap = config['aggregate_cap']
    capped = max(-cap, min(cap, raw))
    if not traits & set(FAMILY_TRAITS['ambition'] + FAMILY_TRAITS['relationship']):
        reasons.append('no_contract_traits')
    return {'terms': terms, 'raw_delta': raw, 'capped_delta': capped, 'cap_applied': capped != raw, 'gates': gates,
            'reasons': sorted(set(reasons)), 'policy_version': config['version'], 'mode': config['mode']}
