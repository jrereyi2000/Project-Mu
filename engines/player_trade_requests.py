"""Player dissatisfaction and trade requests: pure policy and lifecycle (PERSONALITY_TRADE_REQUESTS_PLAN.md §2, U1).

Pure: frozen dataclasses and plain dicts in, frozen dataclasses out. No database, clock, model or random reads.
A request records a desired outcome; it never executes a trade, discounts a price, waives a clause or gives the
player a veto. Nothing here writes a personality profile, mood, trust or a relationship fact.

  clock_ordinal(season, tick) -> int                 season * 1000 + tick (relationships TimePoint, 32 ticks)
  seeded_draw(universe_id, persona_id, season) -> float in [0, 1)
  control_context(team_id, *, user_team_ids, suggestions_on) -> ControlContext
  incidents_from_shared(events, relationship_state, reaction_result, *, team_id, team_key, ...) -> [Incident]
  validate_policy(policy, *, enabled=False) / offline_illustration_policy()
  validate_request_context(context, clock)
  qualify_request_causes(context, clock, policy) -> Qualification
  evaluate_request(context, clock, prior, annual, policy, *, mode) -> RequestDecision
  transition_request(prior, action, context, clock, policy) -> TransitionReceipt
  request_projection(episode, *, audience, clock=None) -> dict | None
  contract_request_context(episode) -> dict          context only: no refusal flag, no utility delta
  gm_request_signal(episode, *, clock=None) -> dict | None   the activation plan's GM handoff (not wired here)

Modes: DISABLED does nothing and consumes no annual opportunity; SHADOW computes hypothetical receipts for offline
reports only; ENABLED needs an explicitly approved policy (never `offline-illustration-v1`). No live caller here.

Incidents come from the relationships plan's shared outputs (reactions and breach states); this module never
infers role loss from snaps, never creates promises or scores treatment, and keeps no trust/mood/grudge counters.
An incident's ID is its contribution key: relationship deterioration, the request and a trait read in one
consumer decision must refer to the same incident so it is charged once.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields, replace
import hashlib
import json
import math
import uuid

SCHEMA_VERSION = 1
ENGINE_VERSION = 'trade-request-v1'
SEED_ALGORITHM = 'sha256-json-be64-v1'
SEED_TAG = 'trade-request-v1'
OFFLINE_POLICY_VERSION = 'offline-illustration-v1'
TICK_STRIDE = 1000
REQUEST_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, 'project-mu:player-trade-requests')

MODES = ('DISABLED', 'SHADOW', 'ENABLED')
WINDOW_KINDS = ('IN_SEASON', 'OFFSEASON', 'CLOSED')
AUTHORITIES = ('SIMULATED', 'JR_RECORDED', 'UNKNOWN')
INCIDENT_KINDS = ('LOST_ROLE', 'BROKEN_PROMISE', 'TREATMENT')
INCIDENT_STATES = ('OPEN', 'REPAIRED', 'DISPUTED', 'UNKNOWN')
SEVERITIES = ('MATERIAL', 'SEVERE', 'UNKNOWN')
DISCLOSURES = ('PRIVATE', 'PUBLIC_VERIFIED')
OUTCOMES = ('NOOP', 'DEFERRED', 'INELIGIBLE', 'NO_REQUEST', 'REQUEST', 'EXISTING')
STATUSES = ('ACTIVE', 'WITHDRAWN', 'RESOLVED_TRADE', 'CLOSED_EMPLOYMENT', 'VOIDED_CORRECTION')
TERMINAL = frozenset(STATUSES[1:])
RESPONSES = ('NONE', 'ACKNOWLEDGED', 'DECLINED', 'EXPLORING')
VISIBILITIES = ('PRIVATE', 'PUBLIC')
ACTION_KINDS = ('ACKNOWLEDGE', 'DECLINE', 'EXPLORE', 'REVIEW_REPAIR', 'RECORD_WITHDRAWAL', 'RECORD_TRADE',
                'CLOSE_EMPLOYMENT', 'CORRECT', 'DISCLOSE')
RESPONSE_OF = {'ACKNOWLEDGE': 'ACKNOWLEDGED', 'DECLINE': 'DECLINED', 'EXPLORE': 'EXPLORING'}
CLOSE_REASONS = ('RELEASED', 'EXPIRED', 'RETIRED', 'OTHER_DEPARTURE')
WITHDRAWAL_BASES = ('player_event', 'jr_correction')
DISCLOSURE_BASES = ('public_statement', 'reviewed_simulation')
AUDIENCES = ('FRONT_OFFICE', 'PUBLIC', 'AUDIT')
REACTIVITY_RULES = ('none', 'trait-v1')
HIGHER_TRAITS = frozenset(('DIVA', 'GRUDGE_HOLDER'))
LOWER_TRAITS = frozenset(('PEACEMAKER',))
CAUSE_CODES = {'LOST_ROLE': 'lost_role', 'BROKEN_PROMISE': 'broken_promise', 'TREATMENT': 'treatment'}


class TradeRequestError(ValueError):
    """Invalid input. `code` is stable for receipts and tests."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(f'{code}: {message}')


class TradeRequestConflict(TradeRequestError):
    """Stale revision, terminal episode, out-of-order checkpoint or conflicting reuse."""


def _fail(code, message, conflict=False):
    raise (TradeRequestConflict if conflict else TradeRequestError)(code, message)


# ------------------------------------------------------------------------------------------- canonical ---

def _plain(value):
    if hasattr(value, '__dataclass_fields__'):
        return {f.name: _plain(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    return value


def canonical_json(value):
    return json.dumps(_plain(value), sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def canonical_hash(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _text(value, label):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _fail('invalid_field', f'{label} must be nonempty trimmed text')
    return value


def _int(value, label, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        _fail('invalid_field', f'{label} must be an integer')
    if minimum is not None and value < minimum:
        _fail('invalid_field', f'{label} must be >= {minimum}')
    return value


def _enum(value, allowed, label):
    if value not in allowed:
        _fail('invalid_field', f'{label} must be one of {allowed}, got {value!r}')
    return value


def _texts(values, label):
    if not isinstance(values, tuple) or not all(isinstance(v, str) and v.strip() for v in values):
        _fail('invalid_field', f'{label} must be a tuple of nonempty text')
    return values


def _tuple(value):
    return tuple(value) if isinstance(value, (list, tuple)) else value


# --------------------------------------------------------------------------------------------- types ---

@dataclass(frozen=True)
class RequestClock:
    universe_id: str
    season: int
    checkpoint_id: str
    ordinal: int
    window_id: str
    window_kind: str
    trades_open: bool
    source_event_id: str


@dataclass(frozen=True)
class ControlContext:
    team_id: int
    user_controlled: bool
    suggestions_on: bool
    authority: str


@dataclass(frozen=True)
class Incident:
    incident_id: str
    source_event_ids: tuple
    kind: str
    target_team_id: int
    first_ordinal: int
    last_ordinal: int
    eligible_observations: int
    severity: str
    state: str
    repair_event_ids: tuple
    disclosure: str


@dataclass(frozen=True)
class RequestContext:
    schema_version: int
    persona_id: str
    team_id: int
    profile_view: dict
    relationship_revision: str
    reaction_revision: str
    assessment_id: str
    as_of_ordinal: int
    complete_through_ordinal: int
    incidents: tuple
    control: ControlContext


@dataclass(frozen=True)
class RequestPolicy:
    version: str
    role_observations: int
    breach_observations: int
    repair_observations: int
    baseline_probability: float
    lower_probability: float
    higher_probability: float
    reactivity_rule: str


@dataclass(frozen=True)
class Qualification:
    eligible: bool
    incident_ids: tuple
    source_event_ids: tuple
    reasons: tuple
    deferred_reasons: tuple
    eligible_since_ordinal: int | None


@dataclass(frozen=True)
class RequestEpisode:
    request_id: str
    decision_id: str
    universe_id: str
    persona_id: str
    team_id: int
    opened_season: int
    opened_ordinal: int
    revision: int
    status: str
    team_response: str
    visibility: str
    initial_incident_ids: tuple
    initial_source_ids: tuple
    cause_codes: tuple
    policy_version: str
    last_review_ordinal: int
    repair_started_ordinal: int | None = None
    resolved_ordinal: int | None = None
    resolution_source_event_id: str | None = None
    resolution_reason: str | None = None
    linked_event_ids: tuple = ()
    disclosed_status: str | None = None
    disclosed_ordinal: int | None = None


@dataclass(frozen=True)
class AnnualDecision:
    universe_id: str
    persona_id: str
    season: int
    decision_id: str
    outcome: str
    draw: float
    probability: float
    policy_version: str
    seed_algorithm: str
    input_hash: str
    checkpoint_id: str
    ordinal: int
    request_id: str | None


@dataclass(frozen=True)
class RequestAction:
    event_id: str
    source_event_id: str
    expected_revision: int
    kind: str
    actor: str
    payload: dict


@dataclass(frozen=True)
class RequestDecision:
    schema_version: int
    engine_version: str
    policy_version: str
    review_key: dict
    input_hash: str
    mode: str
    outcome: str
    qualification: Qualification | None
    probability: float | None
    draw: float | None
    seed_algorithm: str | None
    annual_slot_consumed: bool
    annual: AnnualDecision | None
    before: RequestEpisode | None
    after: RequestEpisode | None
    explanation: tuple


@dataclass(frozen=True)
class TransitionReceipt:
    schema_version: int
    engine_version: str
    request_id: str
    event_id: str
    source_event_id: str
    action_kind: str
    payload_hash: str
    review_key: dict
    changed: bool
    outcome: str
    reasons: tuple
    before: RequestEpisode
    after: RequestEpisode


def to_dict(obj):
    """Schema-v1 JSON-safe dict of any type here (tuples become lists)."""
    return _plain(obj)


def _build(cls, data, nested=None):
    if isinstance(data, cls) or data is None:
        return data
    if not isinstance(data, dict):
        _fail('invalid_field', f'{cls.__name__} must be an object')
    names = {f.name for f in fields(cls)}
    extra = set(data) - names
    if extra:
        _fail('invalid_field', f'{cls.__name__} has unexpected fields {sorted(extra)}')
    kw = {}
    for name in names & set(data):
        value = data[name]
        if nested and name in nested:
            fn = nested[name]
            value = fn(value)
        else:
            value = _tuple(value)
        kw[name] = value
    try:
        return cls(**kw)
    except TypeError as e:
        _fail('invalid_field', f'{cls.__name__}: {e}')


def clock_from_dict(d):
    return _build(RequestClock, d)


def control_from_dict(d):
    return _build(ControlContext, d)


def incident_from_dict(d):
    return _build(Incident, d)


def context_from_dict(d):
    return _build(RequestContext, d, {'incidents': lambda xs: tuple(incident_from_dict(x) for x in xs),
                                       'control': control_from_dict, 'profile_view': lambda v: v})


def policy_from_dict(d):
    return _build(RequestPolicy, d)


def qualification_from_dict(d):
    return _build(Qualification, d)


def episode_from_dict(d):
    return _build(RequestEpisode, d)


def annual_from_dict(d):
    return _build(AnnualDecision, d)


def action_from_dict(d):
    return _build(RequestAction, d, {'payload': lambda v: v})


# ------------------------------------------------------------------------------------- small helpers ---

def clock_ordinal(season, tick):
    """Canonical monotonic checkpoint ordinal from a relationships TimePoint (never wall time)."""
    _int(season, 'season')
    _int(tick, 'tick', minimum=0)
    if tick >= TICK_STRIDE:
        _fail('invalid_field', f'tick must be < {TICK_STRIDE}')
    return season * TICK_STRIDE + tick


def annual_key(universe_id, persona_id, season):
    return [SEED_TAG, _text(universe_id, 'universe_id'), _text(persona_id, 'persona_id'), _int(season, 'season')]


def seeded_draw(universe_id, persona_id, season):
    """[0, 1) from SHA-256 of the canonical annual key: first 8 bytes big-endian / 2**64. Identity anchors the
    draw, never team or display name; a policy change cannot redraw a season."""
    raw = json.dumps(annual_key(universe_id, persona_id, season), separators=(',', ':'), ensure_ascii=False)
    digest = hashlib.sha256(raw.encode('utf-8')).digest()
    return int.from_bytes(digest[:8], 'big') / 2 ** 64


def request_id_for(universe_id, persona_id, season):
    return str(uuid.uuid5(REQUEST_NAMESPACE, canonical_json(['request'] + annual_key(universe_id, persona_id, season))))


def decision_id_for(universe_id, persona_id, season):
    return str(uuid.uuid5(REQUEST_NAMESPACE, canonical_json(['decision'] + annual_key(universe_id, persona_id, season))))


def control_context(team_id, *, user_team_ids, suggestions_on):
    """JR's teams come from the franchise registry's `user_teams` (mapped to team ids by the caller), never
    Madden's ALLUSER flag. `user_team_ids=None` (registry unknown) gives UNKNOWN authority."""
    _int(team_id, 'team_id')
    if user_team_ids is None:
        return ControlContext(team_id, False, bool(suggestions_on), 'UNKNOWN')
    mine = team_id in {int(t) for t in user_team_ids}
    return ControlContext(team_id, mine, bool(suggestions_on), 'JR_RECORDED' if mine else 'SIMULATED')


def offline_illustration_policy(reactivity_rule='trait-v1'):
    """.05/.10/.15 synthetic odds for fixtures only; rejected for enabled persistence."""
    return RequestPolicy(OFFLINE_POLICY_VERSION, 4, 2, 2, 0.10, 0.05, 0.15, reactivity_rule)


def validate_policy(policy, *, enabled=False):
    if not isinstance(policy, RequestPolicy):
        _fail('invalid_policy', 'policy must be a RequestPolicy')
    _text(policy.version, 'policy.version')
    for name in ('role_observations', 'breach_observations', 'repair_observations'):
        _int(getattr(policy, name), f'policy.{name}', minimum=1)
    probs = []
    for name in ('lower_probability', 'baseline_probability', 'higher_probability'):
        v = getattr(policy, name)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not 0 <= v <= 1:
            _fail('invalid_policy', f'policy.{name} must be a finite number in [0, 1]')
        probs.append(float(v))
    if not probs[0] <= probs[1] <= probs[2]:
        _fail('invalid_policy', 'policy needs lower <= baseline <= higher')
    _enum(policy.reactivity_rule, REACTIVITY_RULES, 'policy.reactivity_rule')
    if enabled and policy.version == OFFLINE_POLICY_VERSION:
        _fail('policy_not_approved', 'the offline illustration policy cannot be used in ENABLED mode')
    return policy


def validate_clock(clock):
    if not isinstance(clock, RequestClock):
        _fail('invalid_clock', 'clock must be a RequestClock')
    for name in ('universe_id', 'checkpoint_id', 'window_id', 'source_event_id'):
        _text(getattr(clock, name), f'clock.{name}')
    _int(clock.season, 'clock.season')
    _int(clock.ordinal, 'clock.ordinal')
    _enum(clock.window_kind, WINDOW_KINDS, 'clock.window_kind')
    if not isinstance(clock.trades_open, bool):
        _fail('invalid_clock', 'clock.trades_open must be a bool')
    if clock.window_kind == 'CLOSED' and clock.trades_open:
        _fail('invalid_clock', 'a CLOSED window cannot have trades open')
    return clock


def validate_request_context(context, clock):
    """Shape, enums, chronology and incident-ID reconciliation. Errors are visible, never substituted."""
    validate_clock(clock)
    if not isinstance(context, RequestContext):
        _fail('invalid_context', 'context must be a RequestContext')
    if context.schema_version != SCHEMA_VERSION:
        _fail('schema_version', f'context schema_version must be {SCHEMA_VERSION}')
    _text(context.persona_id, 'persona_id')
    _int(context.team_id, 'team_id')
    for name in ('relationship_revision', 'reaction_revision', 'assessment_id'):
        _text(getattr(context, name), name)
    view = context.profile_view
    if not isinstance(view, dict) or view.get('status') not in ('missing', 'stored'):
        _fail('invalid_profile_view', "profile_view must come from personality.core.feature_view(profile, 'trade_requests')")
    if view.get('status') == 'stored' and view.get('persona_id') not in (None, context.persona_id):
        _fail('identity_mismatch', 'profile_view belongs to another persona')
    _int(context.as_of_ordinal, 'as_of_ordinal')
    _int(context.complete_through_ordinal, 'complete_through_ordinal')
    if context.as_of_ordinal > clock.ordinal:
        _fail('out_of_order', 'context as_of_ordinal is after the clock')
    if context.complete_through_ordinal > context.as_of_ordinal:
        _fail('out_of_order', 'complete_through_ordinal is after as_of_ordinal')
    c = context.control
    if not isinstance(c, ControlContext):
        _fail('invalid_context', 'control must be a ControlContext')
    _int(c.team_id, 'control.team_id')
    _enum(c.authority, AUTHORITIES, 'control.authority')
    if not isinstance(c.user_controlled, bool) or not isinstance(c.suggestions_on, bool):
        _fail('invalid_context', 'control flags must be bools')
    if c.team_id != context.team_id:
        _fail('team_mismatch', 'control team differs from the context team')
    if c.user_controlled and c.authority == 'SIMULATED':
        _fail('invalid_context', 'a JR-controlled team cannot have SIMULATED authority')
    if not isinstance(context.incidents, tuple):
        _fail('invalid_context', 'incidents must be a tuple')
    seen_ids, owner = set(), {}
    for inc in context.incidents:
        if not isinstance(inc, Incident):
            _fail('invalid_context', 'incidents must be Incident values')
        _text(inc.incident_id, 'incident_id')
        if inc.incident_id in seen_ids:
            _fail('incident_conflict', f'duplicate incident {inc.incident_id}')
        seen_ids.add(inc.incident_id)
        _texts(inc.source_event_ids, f'{inc.incident_id}.source_event_ids')
        if not inc.source_event_ids:
            _fail('invalid_field', f'{inc.incident_id} needs source_event_ids')
        _texts(inc.repair_event_ids, f'{inc.incident_id}.repair_event_ids')
        _enum(inc.kind, INCIDENT_KINDS, f'{inc.incident_id}.kind')
        _enum(inc.state, INCIDENT_STATES, f'{inc.incident_id}.state')
        _enum(inc.severity, SEVERITIES, f'{inc.incident_id}.severity')
        _enum(inc.disclosure, DISCLOSURES, f'{inc.incident_id}.disclosure')
        _int(inc.target_team_id, f'{inc.incident_id}.target_team_id')
        _int(inc.eligible_observations, f'{inc.incident_id}.eligible_observations', minimum=0)
        _int(inc.first_ordinal, f'{inc.incident_id}.first_ordinal')
        _int(inc.last_ordinal, f'{inc.incident_id}.last_ordinal')
        if not inc.first_ordinal <= inc.last_ordinal <= context.as_of_ordinal:
            _fail('out_of_order', f'{inc.incident_id} ordinals must satisfy first <= last <= as_of')
        for sid in inc.source_event_ids:
            prev = owner.setdefault(sid, inc.incident_id)
            if prev != inc.incident_id:
                _fail('incident_conflict', f'source {sid} is mapped to {prev} and {inc.incident_id}; reconcile first')
    return context


# ------------------------------------------------------------------------------------- qualification ---

def qualify_request_causes(context, clock, policy):
    """Which open, certified incidents on the current team qualify (§2.3 rules 1-3). Deferred reasons
    (incomplete assessment, closed window) never consume the annual opportunity."""
    validate_request_context(context, clock)
    validate_policy(policy)
    reasons, deferred, qualifying = [], [], []
    if context.complete_through_ordinal < context.as_of_ordinal:
        deferred.append('assessment_incomplete')
    if not clock.trades_open or clock.window_kind == 'CLOSED':
        deferred.append('window_closed')
    for inc in sorted(context.incidents, key=lambda i: i.incident_id):
        if inc.target_team_id != context.team_id:
            reasons.append(f'other_team:{inc.incident_id}')
            continue
        if inc.state in ('DISPUTED', 'UNKNOWN'):
            reasons.append(f'not_certified:{inc.incident_id}:{inc.state.lower()}')
            continue
        if inc.state == 'REPAIRED':
            reasons.append(f'repaired:{inc.incident_id}')
            continue
        if inc.severity == 'UNKNOWN':
            reasons.append(f'severity_unknown:{inc.incident_id}')
            continue
        needed = policy.role_observations if inc.kind == 'LOST_ROLE' else policy.breach_observations
        if inc.eligible_observations < needed:
            reasons.append(f'insufficient_observations:{inc.incident_id}:{inc.eligible_observations}/{needed}')
            continue
        qualifying.append(inc)
        reasons.append(f'qualifies:{inc.incident_id}:{CAUSE_CODES[inc.kind]}')
    eligible = bool(qualifying) and not deferred
    return Qualification(
        eligible=eligible,
        incident_ids=tuple(sorted(i.incident_id for i in qualifying)),
        source_event_ids=tuple(sorted({s for i in qualifying for s in i.source_event_ids})),
        reasons=tuple(reasons), deferred_reasons=tuple(deferred),
        eligible_since_ordinal=min((i.last_ordinal for i in qualifying), default=None) if qualifying else None)


def reactivity_probability(profile_view, policy):
    """(probability, reason). Offline proposal: DIVA or GRUDGE_HOLDER -> higher, PEACEMAKER -> lower, both -> baseline;
    never stacked. A missing or unknown-origin profile is the neutral baseline. Temperament is not mapped (deferred
    for review); LOYAL, money, quietness, confidence, hometown, age and OVR never substitute for reaction evidence."""
    if policy.reactivity_rule == 'none':
        return float(policy.baseline_probability), 'baseline:rule_none'
    traits = set(profile_view.get('traits') or ()) if profile_view.get('status') == 'stored' else set()
    hi, lo = bool(traits & HIGHER_TRAITS), bool(traits & LOWER_TRAITS)
    if hi and not lo:
        return float(policy.higher_probability), 'higher:' + ','.join(sorted(traits & HIGHER_TRAITS))
    if lo and not hi:
        return float(policy.lower_probability), 'lower:' + ','.join(sorted(traits & LOWER_TRAITS))
    return float(policy.baseline_probability), 'baseline:opposing_signals' if hi else 'baseline:neutral'


def _review_key(clock, persona_id, action_kind):
    return {'universe_id': clock.universe_id, 'persona_id': persona_id, 'season': clock.season,
            'window_id': clock.window_id, 'checkpoint_id': clock.checkpoint_id, 'action_kind': action_kind}


def _new_episode(context, clock, qual, policy, decision_id, request_id):
    kinds = {i.incident_id: i.kind for i in context.incidents}
    return RequestEpisode(
        request_id=request_id, decision_id=decision_id, universe_id=clock.universe_id,
        persona_id=context.persona_id, team_id=context.team_id, opened_season=clock.season,
        opened_ordinal=clock.ordinal, revision=0, status='ACTIVE', team_response='NONE', visibility='PRIVATE',
        initial_incident_ids=qual.incident_ids, initial_source_ids=qual.source_event_ids,
        cause_codes=tuple(sorted({CAUSE_CODES[kinds[i]] for i in qual.incident_ids})),
        policy_version=policy.version, last_review_ordinal=clock.ordinal)


def evaluate_request(context, clock, prior, annual, policy, *, mode):
    """One checkpoint review (§2.3). `prior` is the persona's ACTIVE episode or None; `annual` is this season's
    stored AnnualDecision or None. Only a qualified open-window review with no annual decision draws, and it
    consumes the season's one opportunity whether the result is REQUEST or NO_REQUEST."""
    _enum(mode, MODES, 'mode')
    if mode == 'DISABLED':
        return RequestDecision(SCHEMA_VERSION, ENGINE_VERSION, getattr(policy, 'version', ''), {}, '', mode, 'NOOP',
                               None, None, None, None, False, None, prior, prior, ('disabled',))
    validate_policy(policy, enabled=(mode == 'ENABLED'))
    validate_request_context(context, clock)
    if prior is not None:
        if not isinstance(prior, RequestEpisode):
            _fail('invalid_episode', 'prior must be a RequestEpisode')
        if prior.universe_id != clock.universe_id or prior.persona_id != context.persona_id:
            _fail('identity_mismatch', 'prior episode belongs to another identity')
    if annual is not None:
        if not isinstance(annual, AnnualDecision):
            _fail('invalid_annual', 'annual must be an AnnualDecision')
        if (annual.universe_id, annual.persona_id, annual.season) != (clock.universe_id, context.persona_id,
                                                                       clock.season):
            _fail('identity_mismatch', 'annual decision belongs to another universe/persona/season')
    key = _review_key(clock, context.persona_id, 'REVIEW')
    input_hash = canonical_hash({'engine': ENGINE_VERSION, 'context': context, 'clock': clock, 'policy': policy,
                                 'prior': prior, 'annual': annual, 'mode': mode})

    def decision(outcome, qual=None, *, prob=None, draw=None, consumed=False, new_annual=None, after=prior,
                 explanation=()):
        return RequestDecision(SCHEMA_VERSION, ENGINE_VERSION, policy.version, key, input_hash, mode, outcome,
                               qual, prob, draw, SEED_ALGORITHM if draw is not None else None, consumed,
                               new_annual if new_annual is not None else annual, prior, after, tuple(explanation))

    if prior is not None and prior.status == 'ACTIVE':
        return decision('EXISTING', explanation=['active_episode:' + prior.request_id])
    if annual is not None:
        return decision('NOOP', explanation=[f'annual_opportunity_used:{annual.outcome}'])
    qual = qualify_request_causes(context, clock, policy)
    if qual.deferred_reasons:
        return decision('DEFERRED', qual, explanation=list(qual.deferred_reasons))
    if not qual.eligible:
        return decision('INELIGIBLE', qual, explanation=['no_qualifying_incident'])
    prob, why = reactivity_probability(context.profile_view, policy)
    draw = seeded_draw(clock.universe_id, context.persona_id, clock.season)
    outcome = 'REQUEST' if draw < prob else 'NO_REQUEST'
    decision_id = decision_id_for(clock.universe_id, context.persona_id, clock.season)
    request_id = request_id_for(clock.universe_id, context.persona_id, clock.season) if outcome == 'REQUEST' else None
    new_annual = AnnualDecision(clock.universe_id, context.persona_id, clock.season, decision_id, outcome, draw, prob,
                                policy.version, SEED_ALGORITHM, input_hash, clock.checkpoint_id, clock.ordinal,
                                request_id)
    after = _new_episode(context, clock, qual, policy, decision_id, request_id) if outcome == 'REQUEST' else prior
    return decision(outcome, qual, prob=prob, draw=draw, consumed=True, new_annual=new_annual, after=after,
                    explanation=[why, f'draw<{prob}' if outcome == 'REQUEST' else f'draw>={prob}'])


# ------------------------------------------------------------------------------------------ lifecycle ---

def _validate_action(action):
    if not isinstance(action, RequestAction):
        _fail('invalid_action', 'action must be a RequestAction')
    _text(action.event_id, 'action.event_id')
    _text(action.source_event_id, 'action.source_event_id')
    _int(action.expected_revision, 'action.expected_revision', minimum=0)
    _enum(action.kind, ACTION_KINDS, 'action.kind')
    _text(action.actor, 'action.actor')
    if not isinstance(action.payload, dict):
        _fail('invalid_action', 'action.payload must be an object')
    return action


def action_payload_hash(action):
    return canonical_hash({'source_event_id': action.source_event_id, 'expected_revision': action.expected_revision,
                           'kind': action.kind, 'actor': action.actor, 'payload': action.payload})


def _need(payload, name, kind):
    v = payload.get(name)
    if not isinstance(v, str) or not v.strip():
        _fail('missing_evidence', f'{kind} needs payload.{name}')
    return v


def _repair_review(prior, context, policy):
    """(state, reasons): 'certified' when every cause is REPAIRED with repair IDs and enough observations since,
    and no other open incident on the team; 'started' when all are repaired but not yet long enough; else
    'paused'. Unknown repair pauses verification and never invents a breach."""
    by_id = {i.incident_id: i for i in context.incidents}
    reasons, short = [], False
    for iid in prior.initial_incident_ids:
        inc = by_id.get(iid)
        if inc is None:
            reasons.append(f'cause_unassessed:{iid}')
        elif inc.state != 'REPAIRED':
            reasons.append(f'cause_{inc.state.lower()}:{iid}')
        elif not inc.repair_event_ids:
            reasons.append(f'repair_unreferenced:{iid}')
        elif inc.eligible_observations < policy.repair_observations:
            short = True
            reasons.append(f'repair_observations:{iid}:{inc.eligible_observations}/{policy.repair_observations}')
    others = sorted(i.incident_id for i in context.incidents if i.incident_id not in prior.initial_incident_ids
                    and i.state == 'OPEN' and i.target_team_id == prior.team_id and i.severity != 'UNKNOWN')
    reasons.extend(f'other_open_cause:{iid}' for iid in others)
    if any(not r.startswith('repair_observations') for r in reasons):
        return 'paused', reasons
    return ('started' if short else 'certified'), reasons


def transition_request(prior, action, context, clock, policy):
    """Apply one evidence-backed action (§2.4). Raises TradeRequestConflict for a stale revision, an out-of-order
    checkpoint or a terminal episode, TradeRequestError for missing evidence or authority. A review that cannot
    yet certify (paused repair, unknown departure reason) returns changed=False. Replay is the store's job."""
    if not isinstance(prior, RequestEpisode):
        _fail('invalid_episode', 'prior must be a RequestEpisode')
    _validate_action(action)
    validate_policy(policy)
    validate_request_context(context, clock)
    if prior.universe_id != clock.universe_id or prior.persona_id != context.persona_id:
        _fail('identity_mismatch', 'the action names another identity')
    if action.expected_revision != prior.revision:
        _fail('stale_revision', f'expected revision {action.expected_revision}, episode is at {prior.revision}', True)
    if clock.ordinal < prior.last_review_ordinal:
        _fail('out_of_order', 'checkpoint is before the episode\'s last review', True)
    if prior.status in TERMINAL:
        _fail('terminal_episode', f'{prior.status} episodes never reopen', True)
    kind, p = action.kind, action.payload
    departure = kind in ('RECORD_TRADE', 'CLOSE_EMPLOYMENT')
    if not departure and context.team_id != prior.team_id:
        _fail('team_mismatch', 'the context team is not the requesting team')
    changes, reasons, outcome = {}, [], 'APPLIED'
    resolved = dict(resolved_ordinal=clock.ordinal, resolution_source_event_id=action.source_event_id)

    if kind in RESPONSE_OF:
        auth = context.control.authority
        if auth == 'UNKNOWN':
            _fail('authority_unknown', 'unknown authority blocks automated responses')
        if auth == 'JR_RECORDED':
            if action.actor != 'JR':
                _fail('authority', "a JR-controlled team's response must be recorded by JR")
        elif action.actor != 'GM':
            _fail('authority', "a simulated team's response must come from an explicit GM response event")
        _need(p, 'response_ref', kind)
        changes['team_response'] = RESPONSE_OF[kind]
        reasons.append('response_only')
    elif kind == 'REVIEW_REPAIR':
        state, reasons = _repair_review(prior, context, policy)
        if state == 'certified':
            changes.update(status='WITHDRAWN', resolution_reason='RECONCILED', **resolved)
        elif state == 'started' and prior.repair_started_ordinal is None:
            changes['repair_started_ordinal'] = clock.ordinal
            outcome = 'REPAIR_STARTED'
        else:
            outcome = 'PAUSED'
    elif kind == 'RECORD_WITHDRAWAL':
        basis = p.get('basis')
        if basis not in WITHDRAWAL_BASES:
            _fail('missing_evidence', 'withdrawal needs a verified player event or a JR factual correction '
                                      '(an inbox dismissal is not a withdrawal)')
        _need(p, 'evidence_ref', kind)
        if basis == 'jr_correction' and action.actor != 'JR':
            _fail('authority', 'only JR records a factual correction')
        changes.update(status='WITHDRAWN', resolution_reason='PLAYER_WITHDREW' if basis == 'player_event'
                       else 'JR_CORRECTION', **resolved)
    elif kind == 'RECORD_TRADE':
        if p.get('executed') is not True:
            _fail('not_executed', 'only an executed trade resolves a request; proposals and approvals never do')
        _need(p, 'transaction_id', kind)
        if p.get('from_team_id') != prior.team_id:
            _fail('team_mismatch', 'the trade did not move the player off the requesting team')
        if p.get('to_team_id') in (None, prior.team_id):
            _fail('missing_evidence', 'the trade needs a different receiving team')
        changes.update(status='RESOLVED_TRADE', resolution_reason='TRADED', **resolved)
    elif kind == 'CLOSE_EMPLOYMENT':
        reason = p.get('reason')
        if reason == 'UNKNOWN':
            outcome, reasons = 'DEFERRED', ['departure_unverified']
        else:
            _enum(reason, CLOSE_REASONS, 'payload.reason')
            _need(p, 'evidence_ref', kind)
            if p.get('from_team_id') != prior.team_id:
                _fail('team_mismatch', 'the departure is not from the requesting team')
            changes.update(status='CLOSED_EMPLOYMENT', resolution_reason=reason, **resolved)
    elif kind == 'CORRECT':
        refs = p.get('superseded_refs')
        if not isinstance(refs, list) or not refs or not all(isinstance(r, str) and r for r in refs):
            _fail('missing_evidence', 'a correction names the superseded references')
        _need(p, 'explanation', kind)
        if action.actor not in ('JR', 'SYSTEM_CORRECTION'):
            _fail('authority', 'corrections need an authorized actor')
        if p.get('initial_evidence_invalid') is True:
            changes.update(status='VOIDED_CORRECTION', resolution_reason='CORRECTED', **resolved)
        else:
            reasons.append('linked_correction')
    elif kind == 'DISCLOSE':
        if p.get('basis') not in DISCLOSURE_BASES:
            _fail('missing_evidence', 'disclosure needs a verified public statement or a reviewed simulation event '
                                      '(no automatic leak)')
        _need(p, 'disclosure_ref', kind)
        changes.update(visibility='PUBLIC', disclosed_status=prior.status, disclosed_ordinal=clock.ordinal)

    after = prior
    changed = bool(changes) or 'linked_correction' in reasons      # a linked correction is new evidence
    if changed:
        after = replace(prior, revision=prior.revision + 1, last_review_ordinal=clock.ordinal,
                        linked_event_ids=prior.linked_event_ids + (action.event_id,), **changes)
    return TransitionReceipt(SCHEMA_VERSION, ENGINE_VERSION, prior.request_id, action.event_id,
                             action.source_event_id, kind, action_payload_hash(action),
                             _review_key(clock, prior.persona_id, kind), changed, outcome, tuple(reasons), prior,
                             after)


# ---------------------------------------------------------------------------------------- projections ---

def _actionable(episode, clock):
    return episode.status == 'ACTIVE' and clock is not None and bool(clock.trades_open)


def request_projection(episode, *, audience, clock=None):
    """FRONT_OFFICE: authorized facts for JR (no traits, draws, scores or private promises). PUBLIC: None for
    private or voided episodes, otherwise only the disclosed status, dated, with `current` False once the real
    status has moved on without a new disclosure. AUDIT: the whole episode."""
    _enum(audience, AUDIENCES, 'audience')
    if episode is None:
        return None
    if audience == 'AUDIT':
        return to_dict(episode)
    if audience == 'PUBLIC':
        if episode.visibility != 'PUBLIC' or episode.status == 'VOIDED_CORRECTION':
            return None
        return {'request_id': episode.request_id, 'persona_id': episode.persona_id, 'team_id': episode.team_id,
                'status': episode.disclosed_status, 'as_of_ordinal': episode.disclosed_ordinal,
                'current': episode.disclosed_status == episode.status}
    return {'request_id': episode.request_id, 'persona_id': episode.persona_id, 'team_id': episode.team_id,
            'status': episode.status, 'team_response': episode.team_response,
            'actionable': _actionable(episode, clock), 'visibility': episode.visibility,
            'as_of_ordinal': episode.last_review_ordinal, 'reason_codes': list(episode.cause_codes)}


def contract_request_context(episode):
    """Context for the contract policy (which owns willingness): no refusal flag and no utility delta. The
    incident and source IDs are the shared contribution keys, so the same event is charged once."""
    if episode is None:
        return {'schema_version': 1, 'status': 'ABSENT'}
    active = episode.status == 'ACTIVE'
    return {'schema_version': 1, 'request_id': episode.request_id, 'status': episode.status,
            'team_id': episode.team_id,
            'unresolved_incident_ids': list(episode.initial_incident_ids) if active else [],
            'source_event_ids': list(episode.initial_source_ids), 'as_of_ordinal': episode.last_review_ordinal,
            'policy_version': episode.policy_version}


def gm_request_signal(episode, *, clock=None):
    """The activation plan's GM handoff signal for an ACTIVE episode (None otherwise). A signal only: no
    destination, deadline, price change or veto; the GM engine keeps its valuation, legality and acceptance."""
    if episode is None or episode.status != 'ACTIVE':
        return None
    return {'request_id': episode.request_id, 'persona_id': episode.persona_id, 'team_id': episode.team_id,
            'status': episode.status, 'cause_codes': list(episode.cause_codes),
            'actionable': _actionable(episode, clock)}


# ------------------------------------------------------------------------- shared relationships adapter ---

def _tp(tp):
    return clock_ordinal(tp['season'], tp['tick'])


def _role_windows(events, subject_pid, team_key):
    out = [ev for ev in events if ev.get('kind') == 'role_window' and ev['subject'].get('persona_id') == subject_pid
           and ev['payload'].get('team_key') == team_key]
    return sorted(out, key=lambda ev: (_tp(ev['payload']['start']), ev['source_event_id']))


def _contiguous_known_after(windows, start_ordinal):
    """Known (satisfied/unsatisfied) role windows starting at or after `start_ordinal`, contiguous from the
    first; an unknown assessment or a coverage gap ends the count."""
    run = []
    for ev in windows:
        p = ev['payload']
        if _tp(p['start']) < start_ordinal:
            continue
        if p['assessment'] == 'unknown' or (run and run[-1]['payload']['end'] != p['start']):
            break
        run.append(ev)
    return run


def incidents_from_shared(events, relationship_state, reaction_result, *, team_id, team_key, prior_incident_ids=(),
                          public_source_ids=()):
    """Map the relationships plan's shared outputs to Incidents for one subject on one team (spike, §2.1).

    `events`: the subject's active relationship envelopes (relationship_events.select_active / store.read_events);
    `relationship_state`: personality.relationships.project_relationships(...); `reaction_result`:
    personality.reactions.project_reactions(...). Nothing is re-decided: role frustration and breach/repair
    states are the shared owner's; this only counts the certified role windows as observations.
      - sustained role `frustrated` reaction on the team -> LOST_ROLE, observations = its unsatisfied run;
      - unresolved material breach on the team edge -> BROKEN_PROMISE (promise) or TREATMENT, observations =
        contiguous known role windows since the breach;
      - an incident in `prior_incident_ids` no longer open -> REPAIRED, with the closing repair events (breach)
        or the satisfied windows after the run (role) as repair IDs, observations counted after the repair.
    A reaction result in mode 'off' gives no incidents. Unknown is never turned into a grievance."""
    if not isinstance(reaction_result, dict) or reaction_result.get('mode') == 'off':
        return []
    subject = relationship_state['subject']
    pid = subject['persona_id']
    windows = _role_windows(events, pid, team_key)
    by_sid = {ev['source_event_id']: ev for ev in events}
    public = set(public_source_ids)
    out = {}

    def disclosure(sids):
        return 'PUBLIC_VERIFIED' if sids and all(s in public for s in sids) else 'PRIVATE'

    # Lost role: the shared sustained-role reaction, keyed by the run's first window.
    for r in reaction_result.get('reactions', []):
        if r.get('domain') != 'role' or r.get('target', {}).get('team_key') != team_key \
                or r.get('tag') not in ('frustrated', 'mixed'):
            continue
        run = [by_sid[s] for s in r['source_ids'] if s in by_sid and by_sid[s]['kind'] == 'role_window']
        if not run:
            continue
        run.sort(key=lambda ev: _tp(ev['payload']['start']))
        sids = tuple(sorted(ev['source_event_id'] for ev in run))
        iid = f"inc:lost_role:{team_key}:{run[0]['source_event_id']}"
        out[iid] = Incident(iid, sids, 'LOST_ROLE', team_id, _tp(run[0]['occurred_at']), _tp(run[-1]['occurred_at']),
                            len(run), 'MATERIAL', 'OPEN', (), disclosure(sids))

    # Breaches: the team edge's unresolved list (material only; minor never qualifies).
    edge = next((e for e in relationship_state.get('edges', []) if e['target'].get('kind') == 'team'
                 and e['target'].get('team_key') == team_key), None)
    open_breaches = {b['source_id']: b for b in (edge or {}).get('unresolved_breaches', [])}
    for sid, b in sorted(open_breaches.items()):
        ev = by_sid.get(sid)
        if ev is None:
            continue
        kind = 'BROKEN_PROMISE' if b.get('promise_id') else 'TREATMENT'
        iid = f"inc:{CAUSE_CODES[kind]}:{team_key}:{sid}"
        at = _tp(ev['occurred_at'])
        obs = _contiguous_known_after(windows, at)
        severity = 'MATERIAL' if b['severity'] == 'material' else 'UNKNOWN'
        last = max([at] + [_tp(w['occurred_at']) for w in obs])
        out[iid] = Incident(iid, (sid,), kind, team_id, at, last, len(obs), severity, 'OPEN', (), disclosure((sid,)))

    # Repaired: prior causes no longer open.
    for iid in sorted(set(prior_incident_ids) - set(out)):
        code = next((c for c in CAUSE_CODES.values() if iid.startswith(f'inc:{c}:{team_key}:')), None)
        if code is None:
            continue          # another team's cause: not assessed here
        first_sid = iid[len(f'inc:{code}:{team_key}:'):]
        if first_sid not in by_sid:
            continue          # cause unassessed: verification pauses
        ev = by_sid[first_sid]
        if code == 'lost_role':
            start = _tp(ev['payload']['start'])
            later = [w for w in windows if _tp(w['payload']['start']) >= start]
            satisfied = []
            for w in later:
                a = w['payload']['assessment']
                if a == 'satisfied' and (not satisfied or satisfied[-1]['payload']['end'] == w['payload']['start']):
                    satisfied.append(w)
                elif a == 'satisfied':
                    satisfied = [w]
                else:
                    satisfied = []
            if not satisfied:
                continue
            rids = tuple(sorted(w['source_event_id'] for w in satisfied))
            out[iid] = Incident(iid, (first_sid,), 'LOST_ROLE', team_id, _tp(ev['occurred_at']),
                                _tp(satisfied[-1]['occurred_at']), len(satisfied), 'MATERIAL', 'REPAIRED', rids,
                                disclosure((first_sid,)))
        else:
            kind = 'BROKEN_PROMISE' if code == 'broken_promise' else 'TREATMENT'
            repairs = sorted((r for r in events if r.get('kind') == 'repair'
                              and first_sid in r['payload'].get('breach_source_ids', [])
                              and r['payload'].get('accepted_by_subject') is True),
                             key=lambda r: (_tp(r['occurred_at']), r['source_event_id']))
            if not repairs:
                continue          # closed without a visible accepted repair: stays unassessed, never invented
            at = _tp(repairs[-1]['occurred_at'])
            obs = _contiguous_known_after(windows, at)
            last = max([at] + [_tp(w['occurred_at']) for w in obs])
            out[iid] = Incident(iid, (first_sid,), kind, team_id, _tp(ev['occurred_at']), last, len(obs), 'MATERIAL',
                                'REPAIRED', tuple(r['source_event_id'] for r in repairs), disclosure((first_sid,)))
    return [out[k] for k in sorted(out)]
