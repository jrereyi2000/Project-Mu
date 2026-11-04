"""Canonical relationship events (docs/in_progress/PERSONALITY_RELATIONSHIPS_PLAN.md §2.1-§2.2, unit U1).

Pure: plain dicts in, normalized dicts out. No database, clock, model or random reads.

One envelope records ONE occurrence (`source_event_id`, owned by the producer) as seen by one subject toward one
target. The shared contract every producer (relationship_producers, the inbox outbox, press conferences) and
every reader (relationships.py, reactions.py, relationship_store.py, relationship_preview.py) relies on:

  validate_event(event, *, team_registry) -> normalized envelope        raises RelationshipEventError
  canonical_payload_hash(event) -> sha256 hex                           every semantic field; no event_id/head
  facet_key(event) -> str                                               hash of subject / target / kind
  dedupe_key(event) -> (universe_id, source_event_id, subject persona_id, target key, kind)
  validate_event_batch(events, *, calendar, team_registry) -> [envelopes]   deduped, calendar-checked, ordered
  select_active(events, *, as_of=None, known_through=None) -> {"active", "excluded", "corrections"}
  validate_calendar / ticks_between / union_intervals / interval_gaps    simulation time helpers

Time is simulation time only: TimePoint {season, tick}, ordered as (season, tick). Intervals are half-open
[start, end). Elapsed ticks across seasons need the calendar ({season: total ticks}); a missing season raises
CalendarGapError (it blocks decay, never a history read).

Refs: PersonRef {kind: 'person', universe_id, role, persona_id} names a core Identity (personality.core.ROLES);
TeamRef {kind: 'team', universe_id, team_key} names a durable franchise key from the caller's trusted
team_registry ({team_key: {...}}); relocation keeps the key. Teams are never people.

Records read back from the store may carry `head` (the ingestion batch, an int >= 1): transport metadata for
knowledge cutoffs, excluded from the hash like `event_id`.
"""
from copy import deepcopy
import hashlib
import json
import math

from personality.core import ROLES

RELATIONSHIP_SCHEMA_VERSION = 'relationships-v1'

ENVELOPE_FIELDS = ('schema_version', 'universe_id', 'event_id', 'source_event_id', 'source_kind', 'source_refs',
                   'kind', 'occurred_at', 'subject', 'target', 'actor', 'basis', 'confidence', 'reviewer',
                   'evidence_scope', 'visibility', 'attribution', 'payload', 'supersedes_source_event_id')
TRANSPORT_FIELDS = ('event_id', 'head')          # never part of the semantic hash

KINDS = ('tenure_window', 'role_window', 'meaningful_interaction', 'promise_made', 'promise_resolved',
         'treatment', 'repair', 'situation', 'home_familiarity', 'correction')
# §2.2 replay order within one tick: promise made; windows/interactions/treatment/situation/home; resolved; repair.
KIND_ORDER = {'promise_made': 0, 'tenure_window': 1, 'role_window': 1, 'meaningful_interaction': 1,
              'treatment': 1, 'situation': 1, 'home_familiarity': 1, 'promise_resolved': 2, 'repair': 3,
              'correction': 4}

BASES = ('verified_simulation', 'reviewed_evidence')
CONFIDENCES = ('low', 'medium', 'high')
VISIBILITIES = ('private', 'public')
OUTCOMES = ('positive', 'negative', 'mixed')
SIGNIFICANCES = ('routine', 'meaningful')
RELATIONSHIP_KINDS = ('mentorship', 'support', 'conflict', 'collaboration')
COVERAGES = ('complete', 'partial')
ASSESSMENTS = ('satisfied', 'unsatisfied', 'unknown')
TERM_KINDS = ('role', 'opportunity', 'communication')
RESOLUTIONS = ('kept', 'broken', 'waived', 'renegotiated', 'unknown')
TREATMENT_KINDS = ('support', 'dismissal', 'disrespect', 'consultation', 'recognition')
REPAIR_ACTIONS = ('acknowledgment', 'restitution', 'commitment_met', 'reconciliation')
SITUATION_KINDS = ('arrival', 'departure', 'award', 'team_result', 'injury', 'job_pressure')
SITUATION_STATES = ('positive', 'negative', 'uncertain', 'resolved')
HOME_MATCHES = ('verified', 'unknown')


class RelationshipEventError(ValueError):
    """An envelope, batch or calendar that cannot be admitted. `code` is stable for receipts and tests."""

    def __init__(self, code, message, path=None):
        self.code, self.path = code, path
        super().__init__(f'{code}: {message}' + (f' (at {path})' if path else ''))


class CalendarGapError(RelationshipEventError):
    pass


# ------------------------------------------------------------------------------------------- primitives ---

def _fail(code, message, path=None):
    raise RelationshipEventError(code, message, path)


def _text(value, path, *, nullable=False):
    if value is None and nullable:
        return None
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        _fail('invalid_text', 'must be nonempty trimmed text', path)
    return value


def _int(value, path, *, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int):
        _fail('invalid_int', 'must be an integer', path)
    if minimum is not None and value < minimum:
        _fail('invalid_int', f'must be >= {minimum}', path)
    return value


def _enum(value, allowed, path):
    if value not in allowed:
        _fail('invalid_enum', f'must be one of {list(allowed)}, got {value!r}', path)
    return value


def _dict(value, path):
    if not isinstance(value, dict):
        _fail('invalid_object', 'must be an object', path)
    return value


def _texts(value, path, *, nonempty=False):
    if not isinstance(value, list):
        _fail('invalid_list', 'must be a list', path)
    for i, item in enumerate(value):
        _text(item, f'{path}[{i}]')
    if nonempty and not value:
        _fail('invalid_list', 'must not be empty', path)
    return list(value)


def _json_safe(value, path):
    """Plain JSON only: finite numbers, string keys; no tuples, sets or objects."""
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            _fail('invalid_number', 'numbers must be finite', path)
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _json_safe(item, f'{path}[{i}]')
        return
    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                _fail('invalid_object', 'keys must be strings', path)
            _json_safe(v, f'{path}.{k}')
        return
    _fail('invalid_json', f'unsupported value {type(value).__name__}', path)


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False, ensure_ascii=False)


def _sha(value):
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


# ------------------------------------------------------------------------------------------------- time ---

def time_point(value, path='time'):
    """A validated {season, tick} copy (tick >= 0)."""
    _dict(value, path)
    if set(value) != {'season', 'tick'}:
        _fail('invalid_time', 'a time point is exactly {season, tick}', path)
    return {'season': _int(value['season'], f'{path}.season'), 'tick': _int(value['tick'], f'{path}.tick', minimum=0)}


def tp_key(tp):
    return (tp['season'], tp['tick'])


def validate_calendar(calendar):
    """{season (int or numeric str): total ticks (int >= 1)} -> {int: int}."""
    _dict(calendar, 'calendar')
    out = {}
    for season, total in calendar.items():
        try:
            s = int(season)
        except (TypeError, ValueError):
            _fail('invalid_calendar', f'season key {season!r} is not an integer', 'calendar')
        if str(s) != str(season).strip() and not isinstance(season, int):
            _fail('invalid_calendar', f'season key {season!r} is not an integer', 'calendar')
        out[s] = _int(total, f'calendar.{season}', minimum=1)
    return out


def check_in_calendar(tp, calendar, path='time'):
    """Raise CalendarGapError when the season is unknown, invalid_time when the tick is past its last tick."""
    cal = calendar if all(isinstance(k, int) for k in calendar) else validate_calendar(calendar)
    total = cal.get(tp['season'])
    if total is None:
        raise CalendarGapError('calendar_gap', f"season {tp['season']} is not in the calendar", path)
    if tp['tick'] > total:
        _fail('invalid_time', f"tick {tp['tick']} is past season {tp['season']}'s {total} ticks", path)
    return tp


def ticks_between(start, end, calendar):
    """Elapsed ticks from start to end (end >= start). Crossing a season needs every crossed season's total."""
    cal = validate_calendar(calendar)
    a, b = tp_key(start), tp_key(end)
    if b < a:
        _fail('invalid_time', 'end precedes start', 'ticks_between')
    if a[0] == b[0]:
        return b[1] - a[1]
    for season in range(a[0], b[0]):
        if season not in cal:
            raise CalendarGapError('calendar_gap', f'season {season} is not in the calendar', 'ticks_between')
    return (cal[a[0]] - a[1]) + sum(cal[s] for s in range(a[0] + 1, b[0])) + b[1]


def union_intervals(intervals):
    """Half-open [start, end) intervals (dicts with start/end time points) unioned and sorted.

    Touching intervals merge ([w1, w2) + [w2, w3) = [w1, w3)); empty ones vanish."""
    spans = sorted((tp_key(i['start']), tp_key(i['end'])) for i in intervals if tp_key(i['end']) > tp_key(i['start']))
    merged = []
    for s, e in spans:
        if merged and s <= merged[-1][1]:
            if e > merged[-1][1]:
                merged[-1][1] = e
        else:
            merged.append([s, e])
    return [{'start': {'season': s[0], 'tick': s[1]}, 'end': {'season': e[0], 'tick': e[1]}} for s, e in merged]


def interval_gaps(intervals):
    """The uncovered stretches between unioned intervals, as [start, end) dicts."""
    merged = union_intervals(intervals)
    return [{'start': deepcopy(merged[i]['end']), 'end': deepcopy(merged[i + 1]['start'])}
            for i in range(len(merged) - 1)]


# ------------------------------------------------------------------------------------------------- refs ---

def validate_team_registry(team_registry):
    """{team_key: {...}}: the caller's trusted, verified franchise keys (relocation keeps the key)."""
    _dict(team_registry, 'team_registry')
    for key, info in team_registry.items():
        _text(key, 'team_registry key')
        if not isinstance(info, dict):
            _fail('invalid_registry', f'entry {key!r} must be an object', 'team_registry')
        _json_safe(info, f'team_registry.{key}')
    return team_registry


def registry_hash(team_registry):
    return _sha(validate_team_registry(team_registry))


def _team_key(value, registry, path):
    _text(value, path)
    if value not in registry:
        _fail('unknown_team', f'team_key {value!r} is not in the trusted team registry', path)
    return value


def person_ref(value, universe_id, path):
    _dict(value, path)
    if value.get('kind') != 'person' or set(value) != {'kind', 'universe_id', 'role', 'persona_id'}:
        _fail('invalid_ref', 'a PersonRef is exactly {kind: person, universe_id, role, persona_id}', path)
    if value['universe_id'] != universe_id:
        _fail('cross_universe', 'ref universe differs from the event universe', path)
    if value['role'] not in ROLES:
        _fail('unsupported_role', f"role {value['role']!r} is not a core role", path)
    _text(value['persona_id'], f'{path}.persona_id')
    return dict(value)


def team_ref(value, universe_id, registry, path):
    _dict(value, path)
    if value.get('kind') != 'team' or set(value) != {'kind', 'universe_id', 'team_key'}:
        _fail('invalid_ref', 'a TeamRef is exactly {kind: team, universe_id, team_key}', path)
    if value['universe_id'] != universe_id:
        _fail('cross_universe', 'ref universe differs from the event universe', path)
    _team_key(value['team_key'], registry, f'{path}.team_key')
    return dict(value)


def any_ref(value, universe_id, registry, path, *, nullable=False):
    if value is None and nullable:
        return None
    _dict(value, path)
    if value.get('kind') == 'person':
        return person_ref(value, universe_id, path)
    if value.get('kind') == 'team':
        return team_ref(value, universe_id, registry, path)
    _fail('invalid_ref', "ref kind must be 'person' or 'team'", path)


def target_key(ref):
    """Stable key for a target: 'team:<team_key>' / 'person:<role>:<persona_id>' / '-' (None)."""
    if ref is None:
        return '-'
    if ref.get('kind') == 'team':
        return 'team:' + ref['team_key']
    return f"person:{ref['role']}:{ref['persona_id']}"


def subject_key(ref):
    return f"{ref['role']}:{ref['persona_id']}"


def same_person(a, b):
    return (isinstance(a, dict) and isinstance(b, dict) and a.get('kind') == b.get('kind') == 'person'
            and a.get('role') == b.get('role') and a.get('persona_id') == b.get('persona_id'))


# ------------------------------------------------------------------------------------------------ payloads ---

def _window(p, registry, path):
    _team_key(p.get('team_key'), registry, f'{path}.team_key')
    start, end = time_point(p.get('start'), f'{path}.start'), time_point(p.get('end'), f'{path}.end')
    if tp_key(end) <= tp_key(start):
        _fail('invalid_interval', 'a window is half-open [start, end) with end after start', path)


def _payload(kind, p, ctx):
    universe_id, registry, path = ctx['universe_id'], ctx['registry'], 'payload'
    _dict(p, path)
    _json_safe(p, path)
    if kind == 'tenure_window':
        _window(p, registry, path)
        _enum(p.get('coverage'), COVERAGES, f'{path}.coverage')
    elif kind == 'role_window':
        _window(p, registry, path)
        if 'agreed_role' not in p:
            _fail('missing_field', 'agreed_role is required (null when none)', path)
        _text(p['agreed_role'], f'{path}.agreed_role', nullable=True)
        _text(p.get('observed_role'), f'{path}.observed_role')
        _enum(p.get('assessment'), ASSESSMENTS, f'{path}.assessment')
        _texts(p.get('assessment_basis_refs'), f'{path}.assessment_basis_refs')
    elif kind == 'meaningful_interaction':
        _enum(p.get('relationship_kind'), RELATIONSHIP_KINDS, f'{path}.relationship_kind')
        _enum(p.get('outcome'), OUTCOMES, f'{path}.outcome')
        _enum(p.get('significance'), SIGNIFICANCES, f'{path}.significance')
        _text(p.get('opportunity_ref'), f'{path}.opportunity_ref')
    elif kind == 'promise_made':
        _text(p.get('promise_id'), f'{path}.promise_id')
        person_ref(p.get('issuer'), universe_id, f'{path}.issuer')
        person_ref(p.get('beneficiary'), universe_id, f'{path}.beneficiary')
        if same_person(p['issuer'], p['beneficiary']):
            _fail('self_edge', 'a promise needs two people', path)
        terms = _dict(p.get('terms'), f'{path}.terms')
        _enum(terms.get('kind'), TERM_KINDS, f'{path}.terms.kind')
        _text(terms.get('description'), f'{path}.terms.description')
        criterion = _dict(terms.get('criterion'), f'{path}.terms.criterion')
        if not criterion:
            _fail('unmeasurable_promise', 'a promise needs a measurable criterion', f'{path}.terms.criterion')
        due = time_point(p.get('due_at'), f'{path}.due_at')
        if tp_key(due) < tp_key(ctx['occurred_at']):
            _fail('invalid_deadline', 'a promise cannot fall due before it was made', f'{path}.due_at')
        _text(p.get('authority_ref'), f'{path}.authority_ref')
    elif kind == 'promise_resolved':
        _text(p.get('promise_id'), f'{path}.promise_id')
        resolution = _enum(p.get('resolution'), RESOLUTIONS, f'{path}.resolution')
        if 'criterion_result' not in p or not (p['criterion_result'] is None or isinstance(p['criterion_result'], dict)):
            _fail('missing_field', 'criterion_result is required (an object, or null when unknown)', path)
        if p['criterion_result'] is None and resolution in ('kept', 'broken'):
            _fail('missing_field', 'kept/broken needs the measured criterion_result', path)
        _texts(p.get('outcome_refs'), f'{path}.outcome_refs')
        ack = p.get('beneficiary_ack_ref')
        _text(ack, f'{path}.beneficiary_ack_ref', nullable=True)
        if resolution in ('waived', 'renegotiated') and ack is None:
            _fail('missing_acknowledgment', 'a waiver or renegotiation needs the beneficiary\'s acknowledgment', path)
    elif kind == 'treatment':
        _team_key(p.get('team_key'), registry, f'{path}.team_key')
        _enum(p.get('treatment_kind'), TREATMENT_KINDS, f'{path}.treatment_kind')
        _enum(p.get('outcome'), OUTCOMES, f'{path}.outcome')
        _enum(p.get('significance'), SIGNIFICANCES, f'{path}.significance')
        _texts(p.get('outcome_refs'), f'{path}.outcome_refs')
    elif kind == 'repair':
        _texts(p.get('breach_source_ids'), f'{path}.breach_source_ids', nonempty=True)
        _enum(p.get('action'), REPAIR_ACTIONS, f'{path}.action')
        _texts(p.get('outcome_refs'), f'{path}.outcome_refs')
        if p.get('accepted_by_subject', 'missing') not in (True, False, None):
            _fail('missing_field', 'accepted_by_subject is true, false or null', path)
    elif kind == 'situation':
        _enum(p.get('situation_kind'), SITUATION_KINDS, f'{path}.situation_kind')
        _enum(p.get('state'), SITUATION_STATES, f'{path}.state')
        if 'valid_until' not in p:
            _fail('missing_field', 'valid_until is required (null when open)', path)
        if p['valid_until'] is not None:
            time_point(p['valid_until'], f'{path}.valid_until')
        _texts(p.get('outcome_refs'), f'{path}.outcome_refs')
    elif kind == 'home_familiarity':
        _text(p.get('region_key'), f'{path}.region_key')
        _team_key(p.get('team_key'), registry, f'{path}.team_key')
        _enum(p.get('match'), HOME_MATCHES, f'{path}.match')
        _texts(p.get('geography_refs'), f'{path}.geography_refs')
    elif kind == 'correction':
        _text(p.get('reason'), f'{path}.reason')
        if 'replacement_event' not in p:
            _fail('missing_field', 'replacement_event is required (null retracts)', path)


# ------------------------------------------------------------------------------------------------ envelope ---

def _envelope(event, team_registry, *, nested=False):
    _dict(event, 'event')
    allowed = set(ENVELOPE_FIELDS) | {'head'}
    extra = set(event) - allowed
    if extra:
        _fail('unknown_field', f'unexpected envelope fields {sorted(extra)}', 'event')
    required = set(ENVELOPE_FIELDS) - ({'event_id'} if nested else set())
    missing = required - set(event)
    if missing:
        _fail('missing_field', f'missing envelope fields {sorted(missing)}', 'event')
    ev = deepcopy(event)
    _json_safe(ev, 'event')
    if ev['schema_version'] != RELATIONSHIP_SCHEMA_VERSION:
        _fail('schema_version', f"unsupported schema_version {ev['schema_version']!r}", 'schema_version')
    universe_id = _text(ev['universe_id'], 'universe_id')
    if 'event_id' in ev:
        _text(ev['event_id'], 'event_id', nullable=nested)
    if 'head' in ev:
        _int(ev['head'], 'head', minimum=1)
    _text(ev['source_event_id'], 'source_event_id')
    _text(ev['source_kind'], 'source_kind')
    if not isinstance(ev['source_refs'], list):
        _fail('invalid_list', 'must be a list', 'source_refs')
    seen = set()
    for i, ref in enumerate(ev['source_refs']):
        _dict(ref, f'source_refs[{i}]')
        if set(ref) != {'namespace', 'key'}:
            _fail('invalid_source_ref', 'a source ref is exactly {namespace, key}', f'source_refs[{i}]')
        pair = (_text(ref['namespace'], f'source_refs[{i}].namespace'), _text(ref['key'], f'source_refs[{i}].key'))
        if pair in seen:
            _fail('invalid_source_ref', 'duplicate source ref', f'source_refs[{i}]')
        seen.add(pair)
    kind = _enum(ev['kind'], KINDS, 'kind')
    occurred = time_point(ev['occurred_at'], 'occurred_at')
    subject = person_ref(ev['subject'], universe_id, 'subject')
    target = any_ref(ev['target'], universe_id, team_registry, 'target', nullable=(kind == 'correction'))
    if same_person(subject, target):
        _fail('self_edge', 'a person cannot be his own target', 'target')
    any_ref(ev['actor'], universe_id, team_registry, 'actor', nullable=True)
    basis = _enum(ev['basis'], BASES, 'basis')
    _enum(ev['confidence'], CONFIDENCES, 'confidence')
    _text(ev['reviewer'], 'reviewer', nullable=True)
    if basis == 'reviewed_evidence' and ev['reviewer'] is None:
        _fail('missing_reviewer', 'reviewed evidence names its reviewer', 'reviewer')
    _texts(ev['evidence_scope'], 'evidence_scope')
    _enum(ev['visibility'], VISIBILITIES, 'visibility')
    att = _dict(ev['attribution'], 'attribution')
    if set(att) != {'responsible', 'reason_code', 'evidence_refs'}:
        _fail('invalid_attribution', 'attribution is exactly {responsible, reason_code, evidence_refs}', 'attribution')
    if not isinstance(att['responsible'], list):
        _fail('invalid_list', 'must be a list', 'attribution.responsible')
    for i, ref in enumerate(att['responsible']):
        any_ref(ref, universe_id, team_registry, f'attribution.responsible[{i}]')
    _text(att['reason_code'], 'attribution.reason_code', nullable=True)
    _texts(att['evidence_refs'], 'attribution.evidence_refs')
    _payload(kind, ev['payload'], {'universe_id': universe_id, 'registry': team_registry, 'occurred_at': occurred})
    sup = _text(ev['supersedes_source_event_id'], 'supersedes_source_event_id', nullable=True)
    if kind == 'correction':
        if sup is None:
            _fail('missing_field', 'a correction names the source it supersedes', 'supersedes_source_event_id')
        if sup == ev['source_event_id']:
            _fail('correction_cycle', 'a correction cannot supersede itself', 'supersedes_source_event_id')
        replacement = ev['payload']['replacement_event']
        if replacement is not None:
            rep = _envelope(replacement, team_registry, nested=True)
            if rep['kind'] == 'correction':
                _fail('invalid_replacement', 'a replacement is a fact, not another correction', 'payload.replacement_event')
            if rep['universe_id'] != universe_id:
                _fail('cross_universe', 'replacement universe differs', 'payload.replacement_event')
            if not same_person(rep['subject'], subject):
                _fail('invalid_replacement', 'a replacement keeps the corrected subject', 'payload.replacement_event')
            ev['payload']['replacement_event'] = rep
    elif sup is not None:
        _fail('invalid_field', 'only a correction supersedes a source', 'supersedes_source_event_id')
    return ev


def validate_event(event, *, team_registry):
    """A normalized deep copy of one §2.2 envelope, or RelationshipEventError naming the first problem.

    Extra payload keys (producer rule names, appraisal reasons) are kept; envelope fields are exact."""
    validate_team_registry(team_registry)
    return _envelope(event, team_registry)


def _semantic(event):
    out = {k: v for k, v in event.items() if k not in TRANSPORT_FIELDS}
    payload = out.get('payload')
    if isinstance(payload, dict) and isinstance(payload.get('replacement_event'), dict):
        out['payload'] = dict(payload, replacement_event=_semantic(payload['replacement_event']))
    return out


def canonical_payload_hash(event):
    """sha256 of every semantic field (transport event_id and ingestion head excluded)."""
    return _sha(_semantic(event))


def dedupe_key(event):
    return (event['universe_id'], event['source_event_id'], event['subject']['persona_id'],
            target_key(event.get('target')), event['kind'])


def facet_key(event):
    """One facet of an occurrence: a hash of subject, target and kind (store primary key with source)."""
    return 'f:' + _sha([subject_key(event['subject']), target_key(event.get('target')), event['kind']])[:40]


def order_key(event):
    return (event['occurred_at']['season'], event['occurred_at']['tick'], KIND_ORDER[event['kind']],
            event['source_event_id'], facet_key(event))


def source_ref_map(events):
    """{(namespace, key): source_event_id}; one external ref naming two occurrences is a conflict."""
    out = {}
    for ev in events:
        for ref in ev['source_refs']:
            pair = (ref['namespace'], ref['key'])
            if out.setdefault(pair, ev['source_event_id']) != ev['source_event_id']:
                _fail('source_ref_conflict', f'{pair[0]}:{pair[1]} maps to two occurrences', 'source_refs')
    return out


def validate_event_batch(events, *, calendar, team_registry):
    """Validate every event, check its time against the calendar, dedupe identical facets and return them in
    replay order. Same facet with different content, transport event_id reuse, an external source ref naming two
    occurrences, or more than one universe raise RelationshipEventError."""
    if not isinstance(events, list):
        _fail('invalid_list', 'events must be a list', 'events')
    cal = validate_calendar(calendar)
    validate_team_registry(team_registry)
    by_key, by_event_id, out = {}, {}, []
    universes = set()
    for i, raw in enumerate(events):
        try:
            ev = _envelope(raw, team_registry)
        except RelationshipEventError as e:
            raise RelationshipEventError(e.code, str(e), f'events[{i}]') from e
        universes.add(ev['universe_id'])
        check_in_calendar(ev['occurred_at'], cal, f'events[{i}].occurred_at')
        digest = canonical_payload_hash(ev)
        key = dedupe_key(ev)
        if key in by_key:
            if by_key[key] != digest:
                _fail('facet_conflict', f'{key[1]} has two different facts for one subject/target/kind', f'events[{i}]')
            continue
        prior = by_event_id.get(ev['event_id'])
        if prior is not None and prior != (key, digest):
            _fail('transport_conflict', f"event_id {ev['event_id']!r} reused for different content", f'events[{i}]')
        by_key[key] = digest
        by_event_id[ev['event_id']] = (key, digest)
        out.append(ev)
    if len(universes) > 1:
        _fail('cross_universe', 'a batch belongs to one universe', 'events')
    source_ref_map(out)
    out.sort(key=order_key)
    return out


# ------------------------------------------------------------------------------------------ active facts ---

def _breach_ids(active):
    ids = set()
    for ev in active:
        p = ev['payload']
        if ev['kind'] == 'treatment' and p['outcome'] in ('negative', 'mixed') \
                and p['treatment_kind'] in ('dismissal', 'disrespect'):
            ids.add(ev['source_event_id'])
        elif ev['kind'] == 'promise_resolved' and p['resolution'] == 'broken':
            ids.add(ev['source_event_id'])
    return ids


def select_active(events, *, as_of=None, known_through=None):
    """The facts in effect at a knowledge cutoff (`known_through`: ingestion head; events without `head` count as
    known) and an effective time (`as_of`: occurred_at <= as_of), corrections applied, in replay order.

    Returns {"active": [envelopes], "excluded": [{source_event_id, kind, reason}], "corrections":
    {active source_event_id: correction source_event_id}}. Excluded reasons: correction_cycle,
    conflicting_corrections, correction_target_missing, retracted, superseded, promise_unknown,
    repair_without_breach. Events must already be valid (validate_event / the store)."""
    universes = {ev['universe_id'] for ev in events}
    if len(universes) > 1:
        _fail('cross_universe', 'active facts belong to one universe', 'events')
    known = [ev for ev in events if known_through is None or ev.get('head') is None or ev['head'] <= known_through]
    if as_of is not None:
        cut = tp_key(time_point(as_of, 'as_of'))
        known = [ev for ev in known if tp_key(ev['occurred_at']) <= cut]
    excluded = []
    facts = [ev for ev in known if ev['kind'] != 'correction']
    corrections = [ev for ev in known if ev['kind'] == 'correction']

    def applies(corr, ev):
        if ev['source_event_id'] != corr['supersedes_source_event_id'] or not same_person(ev['subject'], corr['subject']):
            return False
        return corr['target'] is None or target_key(ev.get('target')) == target_key(corr['target'])

    # Corrections superseding corrections form chains; a chain that loops is excluded whole.
    by_sid = {}
    for c in corrections:
        by_sid.setdefault(c['source_event_id'], []).append(c)
    bad = set()
    for c in corrections:
        seen, cur = {c['source_event_id']}, c
        while True:
            nxt = [p for p in by_sid.get(cur['supersedes_source_event_id'], []) if same_person(p['subject'], c['subject'])]
            if not nxt:
                break
            cur = nxt[0]
            if cur['source_event_id'] in seen:
                bad |= seen
                break
            seen.add(cur['source_event_id'])
    for c in corrections:
        if c['source_event_id'] in bad:
            excluded.append({'source_event_id': c['source_event_id'], 'kind': 'correction', 'reason': 'correction_cycle'})
    corrections = [c for c in corrections if c['source_event_id'] not in bad]

    # Each original fact is resolved through its correction chain to the one correction in effect.
    active, applied = [], {}
    used = set()
    for ev in sorted(facts, key=order_key):
        current, head_corr = ev, None
        chain_seen = set()
        while True:
            sid = head_corr['source_event_id'] if head_corr else ev['source_event_id']
            cands = [c for c in corrections if c['source_event_id'] not in chain_seen
                     and c['supersedes_source_event_id'] == sid and same_person(c['subject'], ev['subject'])
                     and (c['target'] is None or target_key(c['target']) == target_key(ev.get('target')))]
            if not cands:
                break
            if len({canonical_payload_hash(c) for c in cands}) > 1:
                head_corr = 'conflict'
                for c in cands:
                    used.add(c['source_event_id'])
                    excluded.append({'source_event_id': c['source_event_id'], 'kind': 'correction',
                                     'reason': 'conflicting_corrections'})
                break
            head_corr = cands[0]
            chain_seen.add(head_corr['source_event_id'])
            used.add(head_corr['source_event_id'])
        if head_corr == 'conflict':
            excluded.append({'source_event_id': ev['source_event_id'], 'kind': ev['kind'],
                             'reason': 'conflicting_corrections'})
            continue
        if head_corr is None:
            active.append(current)
            continue
        replacement = head_corr['payload']['replacement_event']
        excluded.append({'source_event_id': ev['source_event_id'], 'kind': ev['kind'],
                         'reason': 'retracted' if replacement is None else 'superseded'})
        if replacement is not None:
            rep = deepcopy(replacement)
            rep['event_id'] = rep.get('event_id') or head_corr['event_id']
            if head_corr.get('head') is not None:
                rep['head'] = head_corr['head']
            applied[rep['source_event_id']] = head_corr['source_event_id']
            active.append(rep)
    for c in corrections:
        if c['source_event_id'] not in used and not any(applies(c, ev) for ev in facts):
            excluded.append({'source_event_id': c['source_event_id'], 'kind': 'correction',
                             'reason': 'correction_target_missing'})

    # Semantic links: a resolution needs its promise; a repair needs an existing breach.
    active.sort(key=order_key)
    final, promises = [], set()
    for ev in active:
        if ev['kind'] == 'promise_made':
            promises.add(ev['payload']['promise_id'])
        elif ev['kind'] == 'promise_resolved' and ev['payload']['promise_id'] not in promises:
            excluded.append({'source_event_id': ev['source_event_id'], 'kind': ev['kind'], 'reason': 'promise_unknown'})
            continue
        elif ev['kind'] == 'repair' and not set(ev['payload']['breach_source_ids']) <= _breach_ids(final):
            excluded.append({'source_event_id': ev['source_event_id'], 'kind': ev['kind'],
                             'reason': 'repair_without_breach'})
            continue
        final.append(ev)
    excluded.sort(key=lambda x: (x['source_event_id'], x['kind'], x['reason']))
    return {'active': final, 'excluded': excluded, 'corrections': applied}
