"""Directional trust and earned attachment (docs/in_progress/PERSONALITY_RELATIONSHIPS_PLAN.md §2.3, unit U2).

Pure: active facts from personality.relationship_events in, explainable per-target state out. No database, clock,
model, profile write or game effect.

  validate_policy(policy) -> normalized policy                     PolicyError on anything incomplete
  project_relationships(events, *, subject, as_of, known_through, policy, calendar) -> state
  relationship_context(state, *, target=None) -> consumer context  neutral unless mode == 'enabled'

Policy ({mode, rule_version, trust, attachment, reactions}):
  mode           'off' | 'observe' | 'enabled' (the activation plan's switchboard: off / shadow / active)
  trust          None (evidence only) or a COMPLETE numeric section: cap (0, 1], positive_half_life_ticks > 0,
                 deltas {code: -1..1} for every CONTRIBUTION_CODES entry, breach_floor {material, minor}: -1..0,
                 repair_step {action: 0..1} for every repair action
  attachment     None or {tiers: {weak, established, strong}: {min_tenure_ticks, min_affirmative,
                 min_connections}} (ints >= 0, non-decreasing weak -> strong; established/strong need >= 1
                 connection)
  reactions      owned and validated by personality.reactions (passed through untouched here)
'enabled' needs both numeric sections. This module ships NO coefficients: an empty policy observes evidence only.

Trust is one-way (subject -> target), -1..1, null when unknown or not numeric. One source counts once per edge
(its strongest rule). Positive contributions fade with the half-life; an unresolved breach keeps its full
weight and caps trust at its floor until an accepted corrective repair closes it; repair shrinks only the
linked breach's contribution, never past neutral. Attachment (subject -> team) is a conjunction of covered tenure,
affirmative role/treatment evidence and meaningful connections; hometown never counts.
"""
from copy import deepcopy
import hashlib
import math

from personality import relationship_events as rev

POLICY_MODES = ('off', 'observe', 'enabled')
OUTCOMES = rev.OUTCOMES
CONTRIBUTION_CODES = tuple(
    [f'interaction.{o}.{s}' for o in OUTCOMES for s in rev.SIGNIFICANCES]
    + [f'treatment.{k}.{o}' for k in rev.TREATMENT_KINDS for o in OUTCOMES]
    + ['promise.kept', 'promise.broken'])
SEVERITIES = ('material', 'minor')
CLOSING_ACTIONS = ('restitution', 'commitment_met', 'reconciliation')
BREACH_TREATMENTS = ('dismissal', 'disrespect')
POSITIVE_TREATMENTS = ('support', 'recognition', 'consultation')
TIERS = ('weak', 'established', 'strong')
TIER_FIELDS = ('min_tenure_ticks', 'min_affirmative', 'min_connections')
CONFIDENCE_RANK = {c: i for i, c in enumerate(rev.CONFIDENCES)}


class PolicyError(ValueError):
    pass


def _sha(value):
    return hashlib.sha256(rev.canonical_json(value).encode('utf-8')).hexdigest()


# ---------------------------------------------------------------------------------------------- policy ---

def _number(value, label, lo, hi, *, lo_open=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PolicyError(f'{label} must be a finite number')
    if value < lo or value > hi or (lo_open and value == lo):
        raise PolicyError(f'{label} must be within {"(" if lo_open else "["}{lo}, {hi}]')
    return float(value)


def _exact(section, keys, label):
    if not isinstance(section, dict):
        raise PolicyError(f'{label} must be an object')
    missing, extra = set(keys) - set(section), set(section) - set(keys)
    if missing or extra:
        raise PolicyError(f'{label}: missing {sorted(missing)}, unexpected {sorted(extra)}')
    return section


def validate_policy(policy):
    """Normalized copy of a policy; PolicyError on unknown keys, incomplete numeric sections, nonfinite or
    out-of-range numbers, negative durations, unordered tier criteria, or 'enabled' without numbers."""
    if not isinstance(policy, dict):
        raise PolicyError('policy must be an object')
    extra = set(policy) - {'mode', 'rule_version', 'trust', 'attachment', 'reactions'}
    if extra:
        raise PolicyError(f'unexpected policy keys {sorted(extra)}')
    mode = policy.get('mode')
    if mode not in POLICY_MODES:
        raise PolicyError(f'mode must be one of {POLICY_MODES}')
    version = policy.get('rule_version')
    if not isinstance(version, str) or not version.strip():
        raise PolicyError('rule_version must be nonempty text')
    out = {'mode': mode, 'rule_version': version, 'trust': None, 'attachment': None,
           'reactions': deepcopy(policy.get('reactions'))}
    if out['reactions'] is not None and not isinstance(out['reactions'], dict):
        raise PolicyError('reactions must be an object or null')
    trust = policy.get('trust')
    if trust is not None:
        _exact(trust, ('cap', 'positive_half_life_ticks', 'deltas', 'breach_floor', 'repair_step'), 'trust')
        deltas = _exact(trust['deltas'], CONTRIBUTION_CODES, 'trust.deltas')
        floors = _exact(trust['breach_floor'], SEVERITIES, 'trust.breach_floor')
        steps = _exact(trust['repair_step'], rev.REPAIR_ACTIONS, 'trust.repair_step')
        out['trust'] = {
            'cap': _number(trust['cap'], 'trust.cap', 0, 1, lo_open=True),
            'positive_half_life_ticks': _number(trust['positive_half_life_ticks'], 'trust.positive_half_life_ticks',
                                                0, 1e9, lo_open=True),
            'deltas': {k: _number(deltas[k], f'trust.deltas.{k}', -1, 1) for k in CONTRIBUTION_CODES},
            'breach_floor': {k: _number(floors[k], f'trust.breach_floor.{k}', -1, 0) for k in SEVERITIES},
            'repair_step': {k: _number(steps[k], f'trust.repair_step.{k}', 0, 1) for k in rev.REPAIR_ACTIONS},
        }
    attachment = policy.get('attachment')
    if attachment is not None:
        _exact(attachment, ('tiers',), 'attachment')
        tiers = _exact(attachment['tiers'], TIERS, 'attachment.tiers')
        norm = {}
        for tier in TIERS:
            crit = _exact(tiers[tier], TIER_FIELDS, f'attachment.tiers.{tier}')
            for f in TIER_FIELDS:
                v = crit[f]
                if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                    raise PolicyError(f'attachment.tiers.{tier}.{f} must be an int >= 0')
            norm[tier] = {f: crit[f] for f in TIER_FIELDS}
        for lower, higher in zip(TIERS, TIERS[1:]):
            if any(norm[higher][f] < norm[lower][f] for f in TIER_FIELDS):
                raise PolicyError(f'attachment tier {higher} must not be easier than {lower}')
        if norm['weak']['min_tenure_ticks'] < 1 or norm['weak']['min_affirmative'] < 1:
            raise PolicyError('every positive tier needs covered tenure and affirmative evidence')
        if norm['established']['min_connections'] < 1:
            raise PolicyError('established and strong need meaningful connection evidence')
        out['attachment'] = {'tiers': norm}
    if mode == 'enabled' and (out['trust'] is None or out['attachment'] is None):
        raise PolicyError("mode 'enabled' needs reviewed trust and attachment sections")
    return out


def policy_hash(policy):
    return _sha(validate_policy(policy))


# ------------------------------------------------------------------------------------------- helpers ---

def _edge_targets(ev):
    """Which of the subject's edges an event speaks to, with attribution only to explicitly evidenced parties."""
    kind, p = ev['kind'], ev['payload']
    if kind == 'treatment':
        responsible = ev['attribution']['responsible']
        return [deepcopy(r) for r in responsible] if responsible else [deepcopy(ev['target'])]
    if kind in ('promise_made', 'promise_resolved'):
        return [deepcopy(ev['target'])]
    if kind == 'repair':
        return []                                   # applied to its linked breaches' edges
    return [deepcopy(ev['target'])] if ev.get('target') is not None else []


def _breach(ev):
    """(severity) when the event is a breach, else None."""
    p = ev['payload']
    if ev['kind'] == 'treatment' and p['treatment_kind'] in BREACH_TREATMENTS and p['outcome'] in ('negative', 'mixed'):
        return 'material' if p['significance'] == 'meaningful' else 'minor'
    if ev['kind'] == 'promise_resolved' and p['resolution'] == 'broken':
        return 'material'
    return None


def _code(ev):
    p = ev['payload']
    if ev['kind'] == 'meaningful_interaction':
        return f"interaction.{p['outcome']}.{p['significance']}"
    if ev['kind'] == 'treatment':
        return f"treatment.{p['treatment_kind']}.{p['outcome']}"
    if ev['kind'] == 'promise_resolved' and p['resolution'] in ('kept', 'broken'):
        return f"promise.{p['resolution']}"
    return None


def _covering(windows, at):
    k = rev.tp_key(at)
    return any(rev.tp_key(w['start']) <= k < rev.tp_key(w['end']) for w in windows)


def _round(x):
    return None if x is None else round(x + 0.0, 6)


# ------------------------------------------------------------------------------------------- projection ---

def project_relationships(events, *, subject, as_of, known_through, policy, calendar):
    """Directional state of `subject` (a PersonRef) toward every person and team its active facts name.

    `events` are validated envelopes (validate_event_batch or the store); other people's tenure windows in the
    list are used only to tell whether a tie is currently on a team. Returns {schema_version, mode,
    rule_version, policy_hash, input_hash, subject, as_of, known_through, edges: [edge], excluded: [...]}."""
    pol = validate_policy(policy)
    as_of = rev.time_point(as_of, 'as_of')
    cal = rev.validate_calendar(calendar)
    selected = rev.select_active(list(events), as_of=as_of, known_through=known_through)
    active = selected['active']
    mine = [ev for ev in active if rev.same_person(ev['subject'], subject)]
    others = [ev for ev in active if not rev.same_person(ev['subject'], subject)]
    input_hash = _sha(sorted(rev.canonical_payload_hash(ev) + f":{ev.get('head')}"
                             for ev in events if rev.same_person(ev['subject'], subject)
                             or ev['kind'] == 'tenure_window'))
    common = {'rule_version': pol['rule_version'], 'policy_hash': _sha(pol), 'input_hash': input_hash,
              'as_of': as_of, 'known_through': known_through}

    # Group facts by edge.
    edges, refs = {}, {}
    for ev in mine:
        for tgt in _edge_targets(ev):
            key = rev.target_key(tgt)
            refs[key] = tgt
            edges.setdefault(key, []).append(ev)
    breaches = {ev['source_event_id']: ev for ev in mine if _breach(ev)}
    repairs = [ev for ev in mine if ev['kind'] == 'repair']
    for rep in repairs:
        for sid in rep['payload']['breach_source_ids']:
            if sid in breaches:
                for tgt in _edge_targets(breaches[sid]):
                    key = rev.target_key(tgt)
                    if rep not in edges.setdefault(key, []):
                        edges[key].append(rep)
    for ev in mine:
        if ev['kind'] == 'home_familiarity':
            key = 'team:' + ev['payload']['team_key']
            refs.setdefault(key, {'kind': 'team', 'universe_id': ev['universe_id'],
                                  'team_key': ev['payload']['team_key']})
            if ev not in edges.setdefault(key, []):
                edges[key].append(ev)

    conflicted_sources = {x['source_event_id'] for x in selected['excluded'] if x['reason'] == 'conflicting_corrections'}
    ties = _ties(mine, others, as_of)
    out_edges = []
    for key in sorted(edges):
        evs = sorted(edges[key], key=rev.order_key)
        conflicted = any(ev['source_event_id'] in conflicted_sources for ev in events
                         if rev.same_person(ev['subject'], subject) and key in
                         [rev.target_key(t) for t in _edge_targets(ev)])
        out_edges.append(_edge(subject, refs[key], evs, ties, breaches, pol, cal, as_of, common, conflicted))
    return {'schema_version': rev.RELATIONSHIP_SCHEMA_VERSION, 'mode': pol['mode'], 'subject': deepcopy(subject),
            **common, 'edges': out_edges, 'excluded': selected['excluded']}


def _ties(mine, others, as_of):
    """Meaningful person ties: positive/mixed meaningful interactions (one per person, sources listed)."""
    found = {}
    for ev in mine:
        if ev['kind'] != 'meaningful_interaction' or ev['target']['kind'] != 'person':
            continue
        p = ev['payload']
        if p['significance'] != 'meaningful' or p['outcome'] == 'negative':
            continue
        key = rev.target_key(ev['target'])
        tie = found.setdefault(key, {'person': deepcopy(ev['target']), 'kind': p['relationship_kind'],
                                     'source_ids': []})
        if ev['source_event_id'] not in tie['source_ids']:
            tie['source_ids'].append(ev['source_event_id'])
    tenure = {}
    for ev in others:
        if ev['kind'] == 'tenure_window':
            tenure.setdefault((rev.subject_key(ev['subject']), ev['payload']['team_key']), []).append(ev['payload'])
    return [dict(t, _tenure=tenure) for t in found.values()]


def _tie_view(tie, team_key, as_of):
    windows = tie['_tenure'].get((rev.subject_key(tie['person']), team_key), []) if team_key else []
    return {'person': deepcopy(tie['person']), 'kind': tie['kind'], 'source_ids': sorted(tie['source_ids']),
            'relevant_to_team': bool(team_key) and _covering(windows, as_of)}


def _edge(subject, target, evs, ties, breaches, pol, cal, as_of, common, conflicted):
    team_key = target['team_key'] if target['kind'] == 'team' else None
    facts = [ev for ev in evs if ev['kind'] != 'home_familiarity']
    explanation = []
    unresolved = _breach_states(evs, breaches, target)
    trust = None
    if pol['trust'] is not None and facts:
        trust = _trust(evs, unresolved, pol['trust'], cal, as_of, explanation)
    elif facts:
        for ev in facts:
            explanation.append({'code': f"evidence.{ev['kind']}", 'source_ids': [ev['source_event_id']],
                                'before': None, 'after': None,
                                'excluded_reason': None if pol['trust'] is not None else 'no_numeric_policy'})
    if team_key:
        edge_ties = [_tie_view(t, team_key, as_of) for t in ties]
    else:
        edge_ties = [_tie_view(t, None, as_of) for t in ties
                     if rev.target_key(t['person']) == rev.target_key(target)]
    coverage = _coverage(evs, team_key)
    attachment = _attachment(evs, team_key, edge_ties, unresolved, pol, cal, coverage)
    status = 'conflicted' if conflicted else ('known' if facts else 'unknown')
    confidence = None
    if facts:
        confidence = min((ev['confidence'] for ev in facts), key=CONFIDENCE_RANK.get)
    return {'subject': deepcopy(subject), 'target': deepcopy(target), 'status': status, 'trust': _round(trust),
            'confidence': confidence, 'coverage': coverage, 'meaningful_ties': edge_ties,
            'unresolved_breaches': [{k: b[k] for k in ('source_id', 'promise_id', 'severity', 'repair_state')}
                                    for b in unresolved if b['repair_state'] != 'closed'],
            'attachment': attachment, 'hometown': _hometown(evs, team_key),
            'explanation': explanation, **deepcopy(common)}


def _breach_states(evs, breaches, target):
    """Every breach on this edge with its repair state: open, acknowledged, repairing or closed."""
    key = rev.target_key(target)
    out = []
    repairs = [ev for ev in evs if ev['kind'] == 'repair']
    for sid, ev in sorted(breaches.items()):
        if key not in [rev.target_key(t) for t in _edge_targets(ev)]:
            continue
        severity = _breach(ev)
        linked = [r for r in repairs if sid in r['payload']['breach_source_ids']]
        accepted = [r for r in linked if r['payload']['accepted_by_subject'] is True]
        closing = [r for r in accepted if r['payload']['action'] in CLOSING_ACTIONS
                   or (severity == 'minor' and r['payload']['action'] == 'acknowledgment')]
        if closing:
            state = 'closed'
        elif accepted:
            state = 'repairing'
        elif linked:
            state = 'acknowledged'
        else:
            state = 'open'
        out.append({'source_id': sid, 'promise_id': ev['payload'].get('promise_id') if ev['kind'] == 'promise_resolved'
                    else None, 'severity': severity, 'repair_state': state, 'accepted_repairs': accepted,
                    'event': ev})
    return out


def _trust(evs, breach_states, tp, cal, as_of, explanation):
    """Clamped sum of one contribution per source (strongest rule), positive ones faded, breaches repaired only
    through their own accepted repairs, then capped at every unresolved breach's floor."""
    by_source = {}
    for ev in evs:
        code = _code(ev)
        if code is None:
            continue
        delta = tp['deltas'][code]
        prev = by_source.get(ev['source_event_id'])
        if prev is None or abs(delta) > abs(prev[1]):
            by_source[ev['source_event_id']] = (code, delta, ev)
        if prev is not None:
            explanation.append({'code': 'duplicate_source', 'source_ids': [ev['source_event_id']], 'before': None,
                                'after': None, 'excluded_reason': 'one_contribution_per_source'})
    states = {b['source_id']: b for b in breach_states}
    total = 0.0
    for sid, (code, delta, ev) in sorted(by_source.items(), key=lambda kv: rev.order_key(kv[1][2])):
        weight = 1.0
        if sid in states:
            repaired = sum(tp['repair_step'][r['payload']['action']] for r in states[sid]['accepted_repairs'])
            weight = max(0.0, 1.0 - min(1.0, repaired))      # never past the breach's neutral
        elif delta > 0:
            try:
                elapsed = rev.ticks_between(ev['occurred_at'], as_of, cal)
            except rev.CalendarGapError:
                explanation.append({'code': 'decay_blocked', 'source_ids': [sid], 'before': _round(total),
                                    'after': None, 'excluded_reason': 'calendar_gap'})
                return None
            weight = 0.5 ** (elapsed / tp['positive_half_life_ticks'])
        before = total
        total += delta * weight
        explanation.append({'code': code, 'source_ids': [sid], 'before': _round(before), 'after': _round(total),
                            'excluded_reason': None})
    clamped = max(-tp['cap'], min(tp['cap'], total))
    if clamped != total:
        explanation.append({'code': 'saturation', 'source_ids': [], 'before': _round(total), 'after': _round(clamped),
                            'excluded_reason': None})
    total = clamped
    for b in breach_states:
        if b['repair_state'] != 'closed':
            floor = tp['breach_floor'][b['severity']]
            if total > floor:
                explanation.append({'code': 'breach_floor', 'source_ids': [b['source_id']], 'before': _round(total),
                                    'after': _round(floor), 'excluded_reason': None})
                total = floor
    return total


def _coverage(evs, team_key):
    windows = [ev['payload'] for ev in evs if ev['kind'] == 'tenure_window']
    if not team_key or not windows:
        return {'start': None, 'end': None, 'gaps': [], 'complete': False}
    merged = rev.union_intervals(windows)
    return {'start': merged[0]['start'], 'end': merged[-1]['end'], 'gaps': rev.interval_gaps(windows),
            'complete': all(w['coverage'] == 'complete' for w in windows)}


def _attachment(evs, team_key, ties, breach_states, pol, cal, coverage):
    if not team_key:
        return {'tier': 'unknown', 'eligible': False, 'dimensions': {}, 'blocking_reasons': ['person_target']}
    windows = rev.union_intervals([ev['payload'] for ev in evs if ev['kind'] == 'tenure_window'])
    satisfied = sorted({ev['source_event_id'] for ev in evs if ev['kind'] == 'role_window'
                        and ev['payload']['assessment'] == 'satisfied'})
    unsatisfied = sorted({ev['source_event_id'] for ev in evs if ev['kind'] == 'role_window'
                          and ev['payload']['assessment'] == 'unsatisfied'})
    supported = sorted({ev['source_event_id'] for ev in evs if ev['kind'] == 'treatment'
                        and ev['payload']['treatment_kind'] in POSITIVE_TREATMENTS
                        and ev['payload']['outcome'] == 'positive'})
    connections = sorted({rev.target_key(t['person']) for t in ties if t['relevant_to_team']})
    open_material = [b['source_id'] for b in breach_states if b['severity'] == 'material' and b['repair_state'] != 'closed']
    blocking, tenure_ticks = [], None
    try:
        tenure_ticks = sum(rev.ticks_between(w['start'], w['end'], cal) for w in windows)
    except rev.CalendarGapError:
        blocking.append('calendar_gap')
    dims = {'tenure_ticks': tenure_ticks, 'tenure_coverage': 'complete' if coverage['complete'] and not coverage['gaps']
            else ('partial' if windows else 'unknown'),
            'affirmative_role': len(satisfied), 'unsatisfied_role': len(unsatisfied),
            'positive_treatment': len(supported), 'connections': len(connections),
            'unresolved_material_breaches': open_material}
    if not windows:
        blocking.append('tenure_unknown')
    if pol['attachment'] is None:
        blocking.append('no_attachment_policy')
    if blocking:
        return {'tier': 'unknown', 'eligible': False, 'dimensions': dims, 'blocking_reasons': sorted(set(blocking))}
    affirmative = len(satisfied) + len(supported)
    tiers = pol['attachment']['tiers']
    reasons = []
    for tier in reversed(TIERS):
        crit = tiers[tier]
        unmet = [f for f, have in (('min_tenure_ticks', tenure_ticks), ('min_affirmative', affirmative),
                                   ('min_connections', len(connections))) if have < crit[f]]
        if tier != 'weak' and open_material:
            unmet.append('unresolved_material_breach')
        if not unmet:
            return {'tier': tier, 'eligible': True, 'dimensions': dims, 'blocking_reasons': reasons}
        reasons.extend(f'{tier}:{u}' for u in unmet)
    complete = dims['tenure_coverage'] == 'complete'
    return {'tier': 'none' if complete else 'unknown', 'eligible': False, 'dimensions': dims,
            'blocking_reasons': reasons if complete else reasons + ['tenure_partial']}


def _hometown(evs, team_key):
    found = [ev for ev in evs if ev['kind'] == 'home_familiarity' and ev['payload']['match'] == 'verified'
             and ev['payload']['team_key'] == team_key]
    if not team_key or not found:
        return {'status': 'unknown', 'region_key': None, 'source_ids': []}
    return {'status': 'verified', 'region_key': found[-1]['payload']['region_key'],
            'source_ids': sorted(ev['source_event_id'] for ev in found)}


# ------------------------------------------------------------------------------------------- consumers ---

def neutral_context(subject, target=None):
    """What every consumer sees while the feature is off or observing: no edges, no numbers."""
    return {'status': 'neutral', 'subject': deepcopy(subject), 'target': deepcopy(target), 'edges': []}


def relationship_context(state, *, target=None):
    """Consumer view: tier/eligibility, role coverage, relevant directional ties, attribution-specific breaches,
    home familiarity and reason/source IDs. Neutral (identical for off and observe) unless mode is 'enabled'.
    `target` (a PersonRef or TeamRef) filters to that one edge."""
    if state.get('mode') != 'enabled':
        return neutral_context(state.get('subject'), target)
    wanted = rev.target_key(target) if target is not None else None
    edges = []
    for e in state['edges']:
        if wanted is not None and rev.target_key(e['target']) != wanted:
            continue
        relevant = [t for t in e['meaningful_ties'] if t['relevant_to_team'] or e['target']['kind'] == 'person']
        sources = sorted({sid for x in e['explanation'] for sid in x['source_ids']}
                         | {b['source_id'] for b in e['unresolved_breaches']})
        edges.append({'target': deepcopy(e['target']), 'status': e['status'], 'trust': e['trust'],
                      'confidence': e['confidence'],
                      'attachment': {'tier': e['attachment']['tier'], 'eligible': e['attachment']['eligible']},
                      'role_coverage': deepcopy(e['coverage']), 'ties': deepcopy(relevant),
                      'breaches': deepcopy(e['unresolved_breaches']), 'hometown': deepcopy(e['hometown']),
                      'reasons': sorted({x['code'] for x in e['explanation']}
                                        | set(e['attachment']['blocking_reasons'])),
                      'source_ids': sources})
    return {'status': 'enabled', 'subject': deepcopy(state['subject']), 'target': deepcopy(target),
            'rule_version': state['rule_version'], 'policy_hash': state['policy_hash'], 'edges': edges}
