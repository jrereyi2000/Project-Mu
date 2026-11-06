"""Retirement context from explicit snapshots (PERSONALITY_RETIREMENT_PLAN.md §2.1, §2.3, U3).

Pure: no implicit database reads. Callers hand in one player snapshot and versioned evidence bundles; this module
validates coverage, scope and time, keeps unknown distinct from zero, and returns an
`engines.retirement_policy.RetirementContext`.

  build_context(snapshot, evidence, *, feature_mode) -> RetirementContext
  earnings_from_ledger(entry, *, source_version, source_ids, threshold=None) -> earnings dict
  profile_view(profile) -> feature view dict (core feature_view(profile, 'retirement'); errors -> status 'error')

snapshot: {universe_id, person_key, identity_status 'verified', player_id, pgid, earned_season, decision_season,
           age, age_status 'known' (anything else is ambiguous -> age None, BLOCKED_DATA), position}
evidence: {domain: bundle} for domains ability, health, opportunity, career, earnings, goals, attachment, profile.
  bundle: {version EVIDENCE_VERSION, universe_id, source_event_ids [...], as_of {season, tick}, coverage
           complete|partial|unknown, fingerprint, data {...}}

Refused (ContextError): absent or unverified identity, a bundle from another universe, a bundle dated after the
decision season, an unsupported bundle version, malformed fields. Absent bundles are 'unknown' coverage.

Filters: ability drops temporary form and wear (`form`, `form_delta`, `current_overall`, `mileage`, `wear`); health
keeps recorded burden only (no diagnosis); opportunity drops `team_need` (need alone is not an offer) and keeps only
real offer IDs; attachment drops friend counts and hometown (separate, unused here); earnings never treat partial
low earnings as insecurity (career_earnings.is_secure is three-state). feature_mode: 'off' (no profile read),
'neutral_preview' (profile recorded for labels, the policy runs features off) or 'effects'.
"""
from __future__ import annotations

import hashlib
import json

from engines.retirement_policy import COVERAGES, GOAL_KINDS, RetirementContext

EVIDENCE_VERSION = 'retirement-evidence-v1'
SUPPORTED_VERSIONS = frozenset({EVIDENCE_VERSION})
DOMAINS = ('ability', 'health', 'opportunity', 'career', 'earnings', 'goals', 'attachment', 'profile')
FEATURE_MODES = ('off', 'neutral_preview', 'effects')
FORM_KEYS = frozenset({'form', 'form_delta', 'current_overall', 'mileage', 'wear'})
ABILITY_KEYS = ('employability', 'base_overall', 'ratings_version', 'production')
HEALTH_KEYS = ('burden', 'missed_games', 'games_possible', 'injury_weeks', 'mileage_coverage')
OPPORTUNITY_KEYS = ('has_contract', 'contract_id', 'contract_years_left', 'offer_ids', 'acceptance_ids',
                    'market_open', 'stage', 'contender_role', 'roster_legal')
CAREER_KEYS = ('verified_seasons', 'tenure', 'achievements')
GOAL_KEYS = ('goal_id', 'kind', 'target_id', 'current_value', 'target_value', 'projected_range', 'confidence',
             'coverage', 'source_ids')
EDGE_DROP = frozenset({'friend_count', 'friends', 'hometown'})
# Evidence normalization placeholder (review-only, not a coefficient): permanent base OVR 55 -> 0, 85 -> 1.
EMPLOYABILITY_OVR = (55, 85)


class ContextError(ValueError):
    pass


def _fail(msg):
    raise ContextError(msg)


def _text(v):
    return isinstance(v, str) and v.strip() != ''


def _int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)
                          .encode('utf-8')).hexdigest()


def _bundle(evidence, domain, universe_id, decision_season):
    b = evidence.get(domain)
    if b is None:
        return {'coverage': 'unknown', 'data': {}, 'source_event_ids': [], 'as_of': None, 'fingerprint': None}
    if not isinstance(b, dict):
        _fail(f'{domain} evidence must be an object')
    if b.get('version') not in SUPPORTED_VERSIONS:
        _fail(f'{domain} evidence version {b.get("version")!r} is not supported')
    if b.get('universe_id') != universe_id:
        _fail(f'{domain} evidence belongs to universe {b.get("universe_id")!r}, not {universe_id!r}')
    as_of = b.get('as_of')
    if not isinstance(as_of, dict) or not _int(as_of.get('season')):
        _fail(f'{domain} evidence needs as_of.season')
    if as_of['season'] > decision_season:
        _fail(f'{domain} evidence is dated {as_of["season"]}, after decision season {decision_season}')
    if b.get('coverage') not in COVERAGES:
        _fail(f'{domain} evidence coverage must be one of {COVERAGES}')
    ids = b.get('source_event_ids')
    if not isinstance(ids, list) or not all(_text(i) for i in ids):
        _fail(f'{domain} evidence needs source_event_ids (a list of IDs)')
    if not _text(b.get('fingerprint')):
        _fail(f'{domain} evidence needs a fingerprint')
    data = b.get('data', {})
    if not isinstance(data, (dict, list)):
        _fail(f'{domain} evidence data must be an object or list')
    return b


def _pick(data, keys):
    return {k: data[k] for k in keys if k in data}


def _ability(b, warnings):
    data = b['data'] or {}
    dropped = sorted(FORM_KEYS & set(data))
    if dropped:
        warnings.append('ability_excluded:' + ','.join(dropped))
    out = _pick(data, ABILITY_KEYS)
    out['coverage'] = b['coverage']
    if 'employability' not in out and _num(out.get('base_overall')):
        lo, hi = EMPLOYABILITY_OVR
        out['employability'] = max(0.0, min(1.0, (out['base_overall'] - lo) / (hi - lo)))
    if 'production' in out:
        prod = out['production']
        if not isinstance(prod, dict) or prod.get('coverage') not in COVERAGES:
            _fail('ability.production needs its own coverage')
    return out


def _health(b):
    data = b['data'] or {}
    out = _pick(data, HEALTH_KEYS)
    out['coverage'] = b['coverage']
    if 'burden' not in out and _int(out.get('missed_games')) and _int(out.get('games_possible')) \
            and out['games_possible'] > 0 and b['coverage'] != 'unknown':
        out['burden'] = min(1.0, out['missed_games'] / out['games_possible'])
    return out


def _opportunity(b, warnings):
    data = b['data'] or {}
    if 'team_need' in data:
        warnings.append('opportunity_excluded:team_need')
    out = _pick(data, OPPORTUNITY_KEYS)
    out['coverage'] = b['coverage']
    offers = out.get('offer_ids', [])
    if not isinstance(offers, list) or not all(_text(o) for o in offers):
        _fail('opportunity.offer_ids must be a list of real offer IDs')
    out['offer_ids'] = list(offers)
    return out


def _career(b):
    out = _pick(b['data'] or {}, CAREER_KEYS)
    out['coverage'] = b['coverage']
    if 'verified_seasons' in out and not _int(out['verified_seasons']):
        _fail('career.verified_seasons must be an integer')
    return out


def earnings_from_ledger(entry, *, source_version, source_ids, threshold=None):
    """Earnings input from one `engines.career_earnings.earned_shares` entry (or None). Face value is not earned
    money and earnings are not net worth: only the ledger's share and its three-state security are carried."""
    from engines import career_earnings as ce
    if entry is None:
        return {'known_cap_share': None, 'coverage': 'unknown', 'secure': None, 'source_version': source_version,
                'source_ids': list(source_ids)}
    secure = ce.is_secure(entry) if threshold is None else ce.is_secure(entry, threshold)
    return {'known_cap_share': f'{entry["share"]:.6f}', 'coverage': 'complete' if entry.get('complete') else 'partial',
            'secure': secure, 'source_version': source_version, 'source_ids': list(source_ids)}


def _earnings(b):
    data = b['data'] or {}
    if b['coverage'] == 'unknown' or not data:
        return {'known_cap_share': None, 'coverage': 'unknown', 'secure': None,
                'source_version': data.get('source_version'), 'source_ids': list(b['source_event_ids'])}
    secure = data.get('secure')
    if secure not in (True, False, None):
        _fail('earnings.secure must be true, false or null')
    share = data.get('known_cap_share')
    if share is not None and not isinstance(share, str):
        _fail('earnings.known_cap_share is canonical text (or null)')
    if secure is False and b['coverage'] != 'complete':
        secure = None                                    # partial low earnings do not prove insecurity
    return {'known_cap_share': share, 'coverage': b['coverage'], 'secure': secure,
            'source_version': data.get('source_version'), 'source_ids': list(data.get('source_ids') or [])}


def _goals(b):
    data = b['data'] or []
    if not isinstance(data, list):
        _fail('goals evidence data must be a list')
    out = []
    for g in data:
        if not isinstance(g, dict) or not _text(g.get('goal_id')) or g.get('kind') not in GOAL_KINDS:
            _fail(f'goal needs goal_id and kind in {GOAL_KINDS}')
        goal = _pick(g, GOAL_KEYS)
        cov = goal.get('coverage', b['coverage'])
        goal['coverage'] = cov if cov in COVERAGES else 'unknown'
        if b['coverage'] != 'complete':                  # missing record coverage disables the term
            goal['coverage'] = 'unknown' if b['coverage'] == 'unknown' else 'partial'
        out.append(goal)
    return tuple(sorted(out, key=lambda g: g['goal_id']))


def _attachment(b):
    data = b['data'] or {}
    if not data:
        return {}
    status = data.get('status')
    if status not in ('neutral', 'enabled'):
        _fail("attachment.status must be 'neutral' or 'enabled' (relationship_context output)")
    alloc = data.get('allocation')
    if alloc not in (None, 'career', 'contract'):
        _fail("attachment.allocation must be 'career', 'contract' or null")
    edges = []
    for e in data.get('edges') or []:
        edges.append({k: v for k, v in e.items() if k not in EDGE_DROP})
    return {'status': status, 'allocation': alloc, 'rule_version': data.get('rule_version'), 'edges': edges,
            'coverage': b['coverage']}


def profile_view(profile):
    """The retirement-scoped core view. Missing profile -> status 'missing' (neutral); a validation or storage error
    is returned as status 'error' so effects-enabled finalization blocks instead of silently reseeding."""
    from personality import core
    try:
        return core.feature_view(profile, 'retirement')
    except (ValueError, KeyError, TypeError) as e:     # PersonalityError is a ValueError; malformed = corrupt
        return {'status': 'error', 'error': f'{type(e).__name__}: {e}'}


def _profile(b, feature_mode):
    if feature_mode == 'off':
        return None
    view = b['data'] or None
    if view is None:
        return None
    if view.get('status') not in ('stored', 'missing', 'error'):
        _fail("profile view status must be 'stored', 'missing' or 'error'")
    return None if view['status'] == 'missing' else dict(view)


def build_context(snapshot, evidence, *, feature_mode):
    if feature_mode not in FEATURE_MODES:
        _fail(f'feature_mode must be one of {FEATURE_MODES}')
    if not isinstance(snapshot, dict) or not isinstance(evidence, dict):
        _fail('snapshot and evidence must be objects')
    unknown = set(evidence) - set(DOMAINS)
    if unknown:
        _fail(f'unknown evidence domains: {sorted(unknown)}')
    universe_id, person_key = snapshot.get('universe_id'), snapshot.get('person_key')
    if not _text(universe_id) or not _text(person_key):
        _fail('a verified universe and immutable person key are required')
    if snapshot.get('identity_status') != 'verified':
        _fail(f'identity is {snapshot.get("identity_status")!r}, not verified: absent or ambiguous identity blocks')
    for k in ('player_id', 'pgid', 'earned_season', 'decision_season'):
        if not _int(snapshot.get(k)):
            _fail(f'snapshot.{k} must be an integer')
    if snapshot['decision_season'] < snapshot['earned_season']:
        _fail('decision_season must be >= earned_season')
    ds = snapshot['decision_season']
    bundles = {d: _bundle(evidence, d, universe_id, ds) for d in DOMAINS}
    warnings = []
    age = snapshot.get('age') if snapshot.get('age_status') == 'known' and _int(snapshot.get('age')) else None
    if age is None:
        warnings.append('age_ambiguous')
    position = snapshot.get('position') if _text(snapshot.get('position')) else ''
    ev = {d: {'coverage': b['coverage'], 'source_event_ids': list(b['source_event_ids']), 'as_of': b['as_of'],
              'fingerprint': b['fingerprint']} for d, b in bundles.items()}
    ctx_evidence = {'version': EVIDENCE_VERSION, 'feature_mode': feature_mode, 'domains': ev,
                    'warnings': warnings, 'fingerprint': _fingerprint(ev)}
    ctx = RetirementContext(
        universe_id=universe_id, person_key=person_key, player_id=snapshot['player_id'], pgid=snapshot['pgid'],
        earned_season=snapshot['earned_season'], decision_season=ds, age=age, position=position,
        ability=_ability(bundles['ability'], warnings), health=_health(bundles['health']),
        opportunity=_opportunity(bundles['opportunity'], warnings), career=_career(bundles['career']),
        earnings=_earnings(bundles['earnings']), goals=_goals(bundles['goals']),
        attachment=_attachment(bundles['attachment']),
        feature_view=_profile(bundles['profile'], feature_mode), evidence=ctx_evidence)
    ctx_evidence['warnings'] = list(warnings)
    return ctx
