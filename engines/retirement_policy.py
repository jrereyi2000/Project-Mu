"""Annual retirement policy: neutral baseline plus bounded optional modifiers (PERSONALITY_RETIREMENT_PLAN.md §2.1-2.4,
U1).

Pure: frozen dataclasses in, frozen dataclasses out. No database, clock, global RNG or model reads; no profile writes.

  annual_draw(universe_id, person_key, earned_season, seed_version) -> float in [0, 1)
  baseline_probability(ctx, policy) -> ProbabilityBreakdown
  personality_adjustment(ctx, base, policy) -> dict
  evaluate_annual(ctx, policy, *, features_enabled=False, checkpoint='OPEN', production=False) -> RetirementDecision
  validate_policy(policy, *, production=False) / synthetic_policy() / policy_from_dict(d) / policy_to_dict(p)
  to_dict(obj) / canonical_json(value) / canonical_hash(value)

Baseline (neutral): p_base = sigmoid(age_position + ability + health + opportunity). The age/position intercept is a
piecewise-linear curve over the policy's knots, extended linearly past the ends: there is no age cliff, maximum age
or annual exit count. Each other term is clamped to +-term_bound. Missing optional evidence is a zero term plus a
warning; missing identity, age or position (or a position the policy does not cover) is BLOCKED_DATA, p None (None
means blocked data, never zero risk). Below `min_review_age` a player is not reviewed (eligible False, p 0.0); a
young unsigned exit is reviewed separately, never as a retirement.

Modifiers (optional, effects-enabled only): one aggregate log-odds delta clamped to +-modifier_bound, with every
excluded reason recorded. LEGACY_BUILDER needs a reachable recorded goal and usable opportunity; RING_CHASER needs
an established career, earnings security True (unknown disables it; OVR is no substitute) and a usable contender
role together; attachment needs an enabled relationship view, an eligible tier, no unresolved breach and an explicit
`allocation: 'career'` (attachment is allocated once, across career and contract). COASTING has no numeric effect
(no reviewed career-preference mechanism). Friend counts and hometown are never read. With features off, a missing
profile or an empty view, the decision equals the neutral one exactly.

No coefficient here is calibrated or approved. `synthetic_policy()` is review-only; production validation refuses
any review-only policy, so offline numbers can never reach a live decision by accident.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields, is_dataclass
import hashlib
import json
import math
import uuid

POLICY_ENGINE = 'retirement-policy-v1'
SEED_TAG = 'mu-retirement-v1'
SEED_ALGORITHM = 'sha256-json-be64-v1'
DECISION_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, 'project-mu:retirement-decisions')

CHECKPOINTS = ('OPEN', 'MARKET_CLOSE', 'ROLLOVER_REVIEW')
INTENTS = ('AVAILABLE', 'RETIRE_INTENT', 'BLOCKED_DATA')
COVERAGES = ('complete', 'partial', 'unknown')
GOAL_KINDS = ('record', 'title', 'role')
GOAL_STATUSES = ('reachable', 'tied', 'surpassed', 'unreachable', 'unknown')
MODIFIER_KEYS = ('legacy_builder', 'ring_chaser', 'attachment')


class RetirementPolicyError(ValueError):
    pass


def _fail(msg):
    raise RetirementPolicyError(msg)


# ----------------------------------------------------------------------------------------------- data ---

@dataclass(frozen=True)
class RetirementContext:
    universe_id: str
    person_key: str
    player_id: int
    pgid: int
    earned_season: int
    decision_season: int
    age: int
    position: str
    ability: dict
    health: dict
    opportunity: dict
    career: dict
    earnings: dict
    goals: tuple
    attachment: dict
    feature_view: dict | None
    evidence: dict


@dataclass(frozen=True)
class RetirementPolicy:
    version: str
    review_only: bool
    seed_version: int
    min_review_age: int
    position_groups: dict          # position code -> curve group
    curves: dict                   # group -> ((age, logit), ...) knots, ages strictly increasing
    term_weights: dict             # ability, health, contract, no_offer
    term_bound: float
    modifier_coefficients: dict    # legacy_builder, ring_chaser, attachment (log-odds; synthetic until reviewed)
    modifier_bound: float
    established_seasons: int
    attachment_tiers: tuple


@dataclass(frozen=True)
class ProbabilityBreakdown:
    p: float | None
    terms: dict
    missing: tuple
    eligible: bool
    policy_version: str
    warnings: tuple = ()


@dataclass(frozen=True)
class RetirementDecision:
    decision_id: str
    universe_id: str
    person_key: str
    earned_season: int
    checkpoint: str
    draw: float
    draw_hex: str
    seed_algorithm: str
    seed_version: int
    base: dict
    modifiers: dict
    final_p: float | None
    intent: str
    input_hash: str
    policy_version: str
    features_enabled: bool
    warnings: tuple = field(default_factory=tuple)


# ----------------------------------------------------------------------------------------- canonical ---

def _plain(value):
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail('non-finite number')
        return float(repr(round(value, 12)))
    return value


def to_dict(obj):
    return _plain(obj)


def canonical_json(value):
    return json.dumps(_plain(value), sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def canonical_hash(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _sigmoid(x):
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    z = math.exp(x)
    return z / (1.0 + z)


def _logit(p):
    return math.log(p / (1.0 - p))


def _clamp(x, bound):
    return max(-bound, min(bound, x))


def _num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


# ----------------------------------------------------------------------------------------------- draw ---

def annual_key(universe_id, person_key, earned_season, seed_version):
    return [SEED_TAG, universe_id, person_key, earned_season, seed_version]


def _digest(universe_id, person_key, earned_season, seed_version):
    raw = json.dumps(annual_key(universe_id, person_key, earned_season, seed_version), separators=(',', ':'),
                     ensure_ascii=False)
    return hashlib.sha256(raw.encode('utf-8')).digest()


def annual_draw(universe_id, person_key, earned_season, seed_version):
    """[0, 1): first 64 bits (unsigned, big-endian) of SHA-256 over the canonical UTF-8 JSON annual key, / 2**64.
    Anchored on the universe and the immutable person key: team, name, roster order and policy never change it."""
    if not isinstance(universe_id, str) or not universe_id or not isinstance(person_key, str) or not person_key:
        _fail('annual_draw needs a universe and a person key')
    return int.from_bytes(_digest(universe_id, person_key, earned_season, seed_version)[:8], 'big') / 2 ** 64


def draw_hex(universe_id, person_key, earned_season, seed_version):
    return _digest(universe_id, person_key, earned_season, seed_version)[:8].hex()


def decision_id_for(universe_id, person_key, earned_season):
    """One decision per person and earned season: a new policy is a superseding review, never a new decision."""
    return str(uuid.uuid5(DECISION_NAMESPACE, canonical_json([universe_id, person_key, earned_season])))


# --------------------------------------------------------------------------------------------- policy ---

_POLICY_FIELDS = {f.name for f in fields(RetirementPolicy)}
_TERM_WEIGHTS = ('ability', 'health', 'contract', 'no_offer')


def validate_policy(policy, *, production=False):
    if not isinstance(policy, RetirementPolicy):
        _fail('policy must be a RetirementPolicy')
    if not isinstance(policy.version, str) or not policy.version.strip():
        _fail('policy version must be nonempty text')
    if production and policy.review_only:
        _fail(f'policy {policy.version} is review-only and cannot be used for a production decision')
    if not isinstance(policy.seed_version, int) or isinstance(policy.seed_version, bool) or policy.seed_version < 1:
        _fail('seed_version must be a positive integer')
    if not isinstance(policy.min_review_age, int) or policy.min_review_age < 18:
        _fail('min_review_age must be an integer >= 18')
    if not policy.curves:
        _fail('policy needs at least one age curve')
    for group, knots in policy.curves.items():
        if len(knots) < 2:
            _fail(f'curve {group} needs at least two knots')
        ages = [k[0] for k in knots]
        if any(not isinstance(a, int) for a in ages) or ages != sorted(set(ages)):
            _fail(f'curve {group} ages must be strictly increasing integers')
        if any(not _num(k[1]) for k in knots):
            _fail(f'curve {group} logits must be finite numbers')
    for pos, group in policy.position_groups.items():
        if group not in policy.curves:
            _fail(f'position {pos} maps to unknown curve {group}')
    if set(policy.term_weights) != set(_TERM_WEIGHTS) or not all(_num(v) for v in policy.term_weights.values()):
        _fail(f'term_weights must be exactly {_TERM_WEIGHTS} with finite values')
    if set(policy.modifier_coefficients) != set(MODIFIER_KEYS) or not all(
            _num(v) for v in policy.modifier_coefficients.values()):
        _fail(f'modifier_coefficients must be exactly {MODIFIER_KEYS} with finite values')
    for name in ('term_bound', 'modifier_bound'):
        v = getattr(policy, name)
        if not _num(v) or v < 0:
            _fail(f'{name} must be a finite number >= 0')
    if not isinstance(policy.established_seasons, int) or policy.established_seasons < 1:
        _fail('established_seasons must be a positive integer')
    return policy


def policy_from_dict(d):
    """Build a policy from JSON. Unknown keys (an age cap, a quota, ...) are refused, not ignored."""
    extra = set(d) - _POLICY_FIELDS
    if extra:
        _fail(f'unknown policy keys: {sorted(extra)}')
    missing = _POLICY_FIELDS - set(d)
    if missing:
        _fail(f'missing policy keys: {sorted(missing)}')
    p = RetirementPolicy(
        version=d['version'], review_only=d['review_only'], seed_version=d['seed_version'],
        min_review_age=d['min_review_age'], position_groups=dict(d['position_groups']),
        curves={g: tuple((int(a), float(v)) for a, v in knots) for g, knots in d['curves'].items()},
        term_weights={k: float(v) for k, v in d['term_weights'].items()}, term_bound=float(d['term_bound']),
        modifier_coefficients={k: float(v) for k, v in d['modifier_coefficients'].items()},
        modifier_bound=float(d['modifier_bound']), established_seasons=d['established_seasons'],
        attachment_tiers=tuple(d['attachment_tiers']))
    if not isinstance(p.review_only, bool):
        _fail('review_only must be true or false')
    return validate_policy(p)


def policy_to_dict(policy):
    d = to_dict(policy)
    d['curves'] = {g: [list(k) for k in knots] for g, knots in d['curves'].items()}
    return d


_GROUPS = {'QB': 'QB', 'HB': 'RB', 'FB': 'RB', 'WR': 'SKILL', 'TE': 'SKILL', 'LT': 'OL', 'LG': 'OL', 'C': 'OL',
           'RG': 'OL', 'RT': 'OL', 'LE': 'DL', 'RE': 'DL', 'DT': 'DL', 'LOLB': 'LB', 'MLB': 'LB', 'ROLB': 'LB',
           'CB': 'DB', 'FS': 'DB', 'SS': 'DB', 'K': 'SPEC', 'P': 'SPEC', 'LS': 'SPEC'}


def synthetic_policy():
    """Review-only demonstration numbers (not calibrated, not approved; plan §2.2, Q4)."""
    curves = {
        'QB': ((22, -6.0), (30, -4.2), (34, -2.6), (38, -1.0), (42, 0.4)),
        'RB': ((21, -5.5), (27, -3.4), (30, -2.0), (33, -0.6)),
        'SKILL': ((21, -5.8), (29, -3.8), (32, -2.3), (35, -0.9)),
        'OL': ((21, -6.0), (30, -3.8), (33, -2.2), (36, -0.8)),
        'DL': ((21, -5.8), (29, -3.7), (32, -2.2), (35, -0.8)),
        'LB': ((21, -5.8), (29, -3.7), (32, -2.2), (35, -0.8)),
        'DB': ((21, -5.8), (29, -3.7), (32, -2.2), (35, -0.8)),
        'SPEC': ((21, -6.5), (34, -4.0), (40, -2.2), (45, -0.6)),
    }
    return validate_policy(RetirementPolicy(
        version='synthetic-review-v1', review_only=True, seed_version=1, min_review_age=24,
        position_groups=dict(_GROUPS), curves=curves,
        term_weights={'ability': 1.5, 'health': 1.2, 'contract': 0.8, 'no_offer': 0.9}, term_bound=2.0,
        modifier_coefficients={'legacy_builder': -0.35, 'ring_chaser': -0.3, 'attachment': -0.25},
        modifier_bound=0.6, established_seasons=6, attachment_tiers=('EARNED', 'DEEP')))


# ------------------------------------------------------------------------------------------- baseline ---

def _curve(knots, age):
    if age <= knots[0][0]:
        (a0, v0), (a1, v1) = knots[0], knots[1]
    elif age >= knots[-1][0]:
        (a0, v0), (a1, v1) = knots[-2], knots[-1]
    else:
        for (a0, v0), (a1, v1) in zip(knots, knots[1:]):
            if a0 <= age <= a1:
                break
    return v0 + (v1 - v0) * (age - a0) / (a1 - a0)


def _coverage(d):
    c = (d or {}).get('coverage', 'unknown')
    return c if c in COVERAGES else 'unknown'


def _ability_term(ctx, policy, warnings):
    a = ctx.ability or {}
    e = a.get('employability')
    if _coverage(a) == 'unknown' or not _num(e):
        warnings.append('ability_unknown')
        return 0.0
    if not 0.0 <= e <= 1.0:
        _fail('ability.employability must be in [0, 1]')
    if _coverage(a) == 'partial':
        warnings.append('ability_partial')
    return _clamp(policy.term_weights['ability'] * (0.5 - e) * 2.0, policy.term_bound)


def _health_term(ctx, policy, warnings):
    h = ctx.health or {}
    b = h.get('burden')
    if _coverage(h) == 'unknown' or not _num(b):
        warnings.append('health_unknown')            # unknown is neither healthy nor disabled
        return 0.0
    if not 0.0 <= b <= 1.0:
        _fail('health.burden must be in [0, 1]')
    if _coverage(h) == 'partial':
        warnings.append('health_partial')
    return _clamp(policy.term_weights['health'] * b, policy.term_bound)


def _opportunity_term(ctx, policy, warnings):
    o = ctx.opportunity or {}
    if _coverage(o) == 'unknown':
        warnings.append('opportunity_unknown')
        return 0.0
    term = 0.0
    if o.get('has_contract') is True:
        term -= policy.term_weights['contract']
    elif o.get('offer_ids'):
        term -= policy.term_weights['contract'] / 2.0          # a real offer: a destination, not a contract yet
    elif o.get('market_open') is True and _coverage(o) == 'complete':
        term += policy.term_weights['no_offer']                 # covered open market, no offer
    elif o.get('market_open') is not True:
        warnings.append('market_not_open')                      # no offers before the market opens is not rejection
    else:
        warnings.append('market_partial')
    return _clamp(term, policy.term_bound)


def baseline_probability(ctx, policy):
    """Neutral probability. Uses age, position, ability, health and opportunity only."""
    validate_policy(policy)
    missing, warnings = [], []
    if not isinstance(ctx.universe_id, str) or not ctx.universe_id:
        missing.append('universe_id')
    if not isinstance(ctx.person_key, str) or not ctx.person_key:
        missing.append('person_key')
    if not isinstance(ctx.age, int) or isinstance(ctx.age, bool) or ctx.age <= 0:
        missing.append('age')
    if not isinstance(ctx.position, str) or not ctx.position:
        missing.append('position')
    elif ctx.position not in policy.position_groups:
        missing.append('position:unsupported')
    if missing:
        return ProbabilityBreakdown(None, {}, tuple(missing), False, policy.version, ())
    if ctx.age < policy.min_review_age:
        if (ctx.opportunity or {}).get('has_contract') is not True:
            warnings.append('young_unsigned_exit_review_separately')
        warnings.append('below_review_age')
        return ProbabilityBreakdown(0.0, {}, (), False, policy.version, tuple(warnings))
    terms = {'age_position': _curve(policy.curves[policy.position_groups[ctx.position]], ctx.age),
             'ability': _ability_term(ctx, policy, warnings),
             'health': _health_term(ctx, policy, warnings),
             'opportunity': _opportunity_term(ctx, policy, warnings)}
    p = _sigmoid(sum(terms.values()))
    return ProbabilityBreakdown(p, terms, (), True, policy.version, tuple(warnings))


# ------------------------------------------------------------------------------------------ modifiers ---

def goal_status(goal):
    """reachable | tied | surpassed | unreachable | unknown for one goal (plan §2.1 goal shape)."""
    cur, tgt, rng = goal.get('current_value'), goal.get('target_value'), goal.get('projected_range')
    if _coverage(goal) != 'complete' or not _num(cur) or not _num(tgt):
        return 'unknown'
    if cur == tgt:
        return 'tied'
    if cur > tgt:
        return 'surpassed'
    if not isinstance(rng, (list, tuple)) or len(rng) != 2 or not all(_num(x) for x in rng):
        return 'unknown'
    return 'reachable' if rng[1] >= tgt else 'unreachable'


def _usable_opportunity(o):
    return o.get('has_contract') is True or bool(o.get('offer_ids'))


def personality_adjustment(ctx, base, policy):
    """{delta, terms, excluded, raw, bounded}. delta is the clamped aggregate log-odds change; terms are the
    contributions that applied; excluded maps each considered input to why it did not apply."""
    validate_policy(policy)
    out = {'delta': 0.0, 'terms': {}, 'excluded': {}, 'raw': 0.0, 'bounded': False}
    view = ctx.feature_view
    if base.p is None or not base.eligible:
        out['excluded']['all'] = 'baseline_not_eligible'
        return out
    if view is None or view.get('status') != 'stored':
        out['excluded']['all'] = 'no_profile'
        return out
    traits = set(view.get('traits') or ())
    coef = policy.modifier_coefficients
    opp = ctx.opportunity or {}
    if 'COASTING' in traits:
        out['excluded']['COASTING'] = 'no_reviewed_mechanism'
    if 'LEGACY_BUILDER' in traits:
        reachable = [g for g in ctx.goals if g.get('kind') in GOAL_KINDS and goal_status(g) == 'reachable']
        if not reachable:
            out['excluded']['LEGACY_BUILDER'] = 'no_reachable_recorded_goal'
        elif not _usable_opportunity(opp):
            out['excluded']['LEGACY_BUILDER'] = 'no_usable_opportunity'
        else:
            out['terms']['legacy_builder'] = coef['legacy_builder']
    if 'RING_CHASER' in traits:
        seasons = (ctx.career or {}).get('verified_seasons')
        secure = (ctx.earnings or {}).get('secure')
        if not isinstance(seasons, int) or seasons < policy.established_seasons:
            out['excluded']['RING_CHASER'] = 'career_not_established'
        elif secure is None:
            out['excluded']['RING_CHASER'] = 'earnings_unknown'
        elif secure is not True:
            out['excluded']['RING_CHASER'] = 'not_financially_secure'
        elif opp.get('contender_role') is not True or not _usable_opportunity(opp):
            out['excluded']['RING_CHASER'] = 'no_usable_contender_role'
        else:
            out['terms']['ring_chaser'] = coef['ring_chaser']
    att = ctx.attachment or {}
    if att:
        if att.get('status') != 'enabled':
            out['excluded']['attachment'] = 'relationships_not_enabled'
        elif att.get('allocation') != 'career':
            out['excluded']['attachment'] = 'not_allocated_to_career'
        else:
            edges = att.get('edges') or []
            breached = any(e.get('breaches') for e in edges)
            earned = [e for e in edges if (e.get('attachment') or {}).get('eligible')
                      and (e.get('attachment') or {}).get('tier') in policy.attachment_tiers]
            if breached:
                out['excluded']['attachment'] = 'unresolved_breach'
            elif not earned:
                out['excluded']['attachment'] = 'no_earned_attachment'
            else:
                out['terms']['attachment'] = coef['attachment']
    raw = sum(out['terms'].values())
    out['raw'] = raw
    out['delta'] = _clamp(raw, policy.modifier_bound)
    out['bounded'] = out['delta'] != raw
    return out


# ------------------------------------------------------------------------------------------- evaluate ---

def _context_hash(ctx, policy, features_enabled, checkpoint):
    return canonical_hash({'engine': POLICY_ENGINE, 'context': ctx, 'policy': policy,
                           'features_enabled': features_enabled, 'checkpoint': checkpoint})


def evaluate_annual(ctx, policy, *, features_enabled=False, checkpoint='OPEN', production=False):
    """One checkpoint review. The draw is the person's annual draw (same at every checkpoint); new evidence moves the
    threshold, never the draw. intent: RETIRE_INTENT when draw < final_p, AVAILABLE otherwise, BLOCKED_DATA when an
    essential input is missing or (effects enabled) the profile view is an error."""
    if not isinstance(ctx, RetirementContext):
        _fail('ctx must be a RetirementContext')
    validate_policy(policy, production=production)
    if checkpoint not in CHECKPOINTS:
        _fail(f'checkpoint must be one of {CHECKPOINTS}')
    if not isinstance(ctx.earned_season, int) or not isinstance(ctx.decision_season, int) \
            or ctx.decision_season < ctx.earned_season:
        _fail('decision_season must be an integer >= earned_season')
    base = baseline_probability(ctx, policy)
    warnings = list(base.warnings)
    have_identity = 'universe_id' not in base.missing and 'person_key' not in base.missing
    draw = annual_draw(ctx.universe_id, ctx.person_key, ctx.earned_season, policy.seed_version) \
        if have_identity else 0.0
    dhex = draw_hex(ctx.universe_id, ctx.person_key, ctx.earned_season, policy.seed_version) \
        if have_identity else ''
    did = decision_id_for(ctx.universe_id, ctx.person_key, ctx.earned_season) if have_identity else ''
    modifiers = {'delta': 0.0, 'terms': {}, 'excluded': {'all': 'features_off'}, 'raw': 0.0, 'bounded': False}
    final_p = base.p
    intent = None
    if base.p is None:
        intent = 'BLOCKED_DATA'
        warnings.extend(f'missing:{m}' for m in base.missing)
    elif features_enabled:
        view = ctx.feature_view
        if view is not None and view.get('status') == 'error':
            intent = 'BLOCKED_DATA'
            warnings.append('feature_view_error')
            final_p = None
        else:
            modifiers = personality_adjustment(ctx, base, policy)
            if base.eligible and modifiers['delta'] != 0.0:
                final_p = _sigmoid(_logit(base.p) + modifiers['delta'])
    if intent is None:
        intent = 'RETIRE_INTENT' if base.eligible and draw < final_p else 'AVAILABLE'
    return RetirementDecision(
        decision_id=did, universe_id=ctx.universe_id, person_key=ctx.person_key, earned_season=ctx.earned_season,
        checkpoint=checkpoint, draw=draw, draw_hex=dhex, seed_algorithm=SEED_ALGORITHM,
        seed_version=policy.seed_version, base=to_dict(base), modifiers=to_dict(modifiers), final_p=final_p,
        intent=intent, input_hash=_context_hash(ctx, policy, features_enabled, checkpoint),
        policy_version=policy.version, features_enabled=bool(features_enabled), warnings=tuple(warnings))
