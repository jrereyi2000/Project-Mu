"""Transient reactions, mood and evolution-candidate review (PERSONALITY_RELATIONSHIPS_PLAN.md §2.4-§2.5, unit U3).

Pure. Reactions are computed from active facts at read time; nothing here writes mood to the core, generates
events, touches ratings, form, Madden morale or contracts, or calls store.change.

  project_reactions(events, relationship_state, *, feature_view, as_of, known_through, policy, calendar) -> result
  select_mood(reactions, *, as_of, policy) -> {primary, secondary, ranked, suppressed}
  reaction_context(result) / public_context(result)                 consumer and writer views
  validate_evolution_candidate(candidate, *, profile, source_events, slider_band=None) -> review result

`relationship_state` is the §2.3 state (personality.relationships.project_relationships), read only for its
`subject` and each edge's `target` / `unresolved_breaches`; this module does not import relationships.py.

Priority (lower first): 1 serious unresolved treatment/promise breach; 2 sustained role frustration; 3 current
job or injury uncertainty; 4 transition (arrival/departure); 5 achievement / team result; 6 neutral. Labels:
neutral, frustrated, uncertain, encouraged, eager, mixed. None is the Confidence slider. A team loss or an
injury never becomes resentment, anxiety or determination; a loss gives no reaction at all.

Policy: the shared policy's `mode` / `rule_version` plus an optional `reactions` section
{sustained_role_windows: int >= 2, severity: {tag: 0..1}}. Without it, sustained role frustration needs at least
two consecutive, contiguous unsatisfied role windows (never one depth-chart scan) and every severity is 0.
Off and observe return the same neutral consumer context; observe keeps the private audit.
"""
from copy import deepcopy
import hashlib
import math

from personality import core
from personality import relationship_events as rev

REACTIONS_VERSION = 'reactions-v1'
POLICY_MODES = ('off', 'observe', 'enabled')
TAGS = ('neutral', 'frustrated', 'uncertain', 'encouraged', 'eager', 'mixed')
PRIORITY = {'breach': 1, 'role': 2, 'uncertainty': 3, 'transition': 4, 'achievement': 5, 'neutral': 6}
CONTRADICTORY = (frozenset(('eager', 'uncertain')),)
MIN_SUSTAINED_ROLE_WINDOWS = 2
MAX_SECONDARY = 2


class ReactionPolicyError(ValueError):
    pass


def _sha(value):
    return hashlib.sha256(rev.canonical_json(value).encode('utf-8')).hexdigest()


# ---------------------------------------------------------------------------------------------- policy ---

def validate_reaction_policy(policy):
    """{mode, rule_version, sustained_role_windows, severity} from the shared policy (other sections ignored)."""
    if not isinstance(policy, dict):
        raise ReactionPolicyError('policy must be an object')
    if policy.get('mode') not in POLICY_MODES:
        raise ReactionPolicyError(f'mode must be one of {POLICY_MODES}')
    version = policy.get('rule_version')
    if not isinstance(version, str) or not version.strip():
        raise ReactionPolicyError('rule_version must be nonempty text')
    section = policy.get('reactions')
    out = {'mode': policy['mode'], 'rule_version': version, 'sustained_role_windows': MIN_SUSTAINED_ROLE_WINDOWS,
           'severity': {}}
    if section is None:
        return out
    if not isinstance(section, dict) or set(section) - {'sustained_role_windows', 'severity'}:
        raise ReactionPolicyError('reactions is {sustained_role_windows?, severity?}')
    n = section.get('sustained_role_windows', MIN_SUSTAINED_ROLE_WINDOWS)
    if isinstance(n, bool) or not isinstance(n, int) or n < MIN_SUSTAINED_ROLE_WINDOWS:
        raise ReactionPolicyError(f'sustained_role_windows must be an int >= {MIN_SUSTAINED_ROLE_WINDOWS}')
    severity = section.get('severity', {})
    if not isinstance(severity, dict):
        raise ReactionPolicyError('severity must be an object')
    for tag, value in severity.items():
        if tag not in TAGS:
            raise ReactionPolicyError(f'unknown tag {tag!r}')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) \
                or not 0 <= value <= 1:
            raise ReactionPolicyError(f'severity.{tag} must be a finite number in [0, 1]')
    out.update(sustained_role_windows=n, severity={k: float(v) for k, v in severity.items()})
    return out


# ------------------------------------------------------------------------------------------- reactions ---

def _visibility(evs):
    return 'public' if all(ev['visibility'] == 'public' for ev in evs) else 'private'


def _confidence(evs):
    return min((ev['confidence'] for ev in evs), key=rev.CONFIDENCES.index)


def _reaction(subject, target, evs, *, domain, tag, kind, started_at, expires_at, explanation, rule_version):
    sources = sorted({ev['source_event_id'] for ev in evs})
    rid = 'r:' + _sha([sources, rev.subject_key(subject), domain, rule_version])[:32]
    return {'reaction_id': rid, 'subject': deepcopy(subject), 'target': deepcopy(target), 'source_ids': sources,
            'domain': domain, 'tag': tag, 'started_at': deepcopy(started_at),
            'expires_at': deepcopy(expires_at), 'priority': PRIORITY[kind], 'confidence': _confidence(evs),
            'visibility': _visibility(evs), 'explanation': list(explanation), 'rule_version': rule_version}


def _is_active(r, as_of):
    k = rev.tp_key(as_of)
    return rev.tp_key(r['started_at']) <= k and (r['expires_at'] is None or k < rev.tp_key(r['expires_at']))


def _breach_reactions(subject, state, by_source, version):
    out = []
    for edge in state.get('edges', []):
        for b in edge.get('unresolved_breaches', []):
            ev = by_source.get(b['source_id'])
            if ev is None or b['severity'] != 'material' or b['repair_state'] == 'closed':
                continue
            domain = 'promise' if b.get('promise_id') else 'organization'
            out.append(_reaction(subject, edge['target'], [ev], domain=domain, tag='frustrated', kind='breach',
                                 started_at=ev['occurred_at'], expires_at=None,
                                 explanation=[f"unresolved_{domain}_breach:{b['repair_state']}"],
                                 rule_version=version))
    return out


def _role_reactions(subject, mine, needed, version):
    """Sustained role frustration: the latest `needed` role windows on one team are all unsatisfied and
    contiguous (no coverage gap). Unknown assessments break the run; a satisfied window ends it."""
    out = []
    by_team = {}
    for ev in mine:
        if ev['kind'] == 'role_window':
            by_team.setdefault(ev['payload']['team_key'], []).append(ev)
    for team_key, windows in sorted(by_team.items()):
        windows.sort(key=lambda ev: rev.tp_key(ev['payload']['start']))
        run = []
        for ev in windows:
            p = ev['payload']
            if p['assessment'] == 'unsatisfied' and (not run or run[-1]['payload']['end'] == p['start']):
                run.append(ev)
            elif p['assessment'] == 'unsatisfied':
                run = [ev]
            else:
                run = []
        if len(run) >= needed:
            target = {'kind': 'team', 'universe_id': run[-1]['universe_id'], 'team_key': team_key}
            out.append(_reaction(subject, target, run, domain='role', tag='frustrated', kind='role',
                                 started_at=run[needed - 1]['occurred_at'], expires_at=None,
                                 explanation=[f'unsatisfied_role_windows:{len(run)}'], rule_version=version))
    return out


SITUATION_RULES = {        # (situation_kind, state) -> (priority class, domain, tag); absent -> no reaction
    ('job_pressure', 'negative'): ('uncertainty', 'job', 'uncertain'),
    ('job_pressure', 'uncertain'): ('uncertainty', 'job', 'uncertain'),
    ('injury', 'negative'): ('uncertainty', 'health', 'uncertain'),
    ('injury', 'uncertain'): ('uncertainty', 'health', 'uncertain'),
    ('arrival', 'positive'): ('transition', 'transition', 'eager'),
    ('arrival', 'uncertain'): ('transition', 'transition', 'uncertain'),
    ('arrival', 'negative'): ('transition', 'transition', 'uncertain'),
    ('departure', 'positive'): ('transition', 'transition', 'eager'),
    ('departure', 'uncertain'): ('transition', 'transition', 'uncertain'),
    ('departure', 'negative'): ('transition', 'transition', 'uncertain'),
    ('award', 'positive'): ('achievement', 'achievement', 'encouraged'),
    ('team_result', 'positive'): ('achievement', 'achievement', 'encouraged'),
}


def _situation_reactions(subject, mine, version):
    out = []
    situations = sorted((ev for ev in mine if ev['kind'] == 'situation'), key=rev.order_key)
    for ev in situations:
        p = ev['payload']
        rule = SITUATION_RULES.get((p['situation_kind'], p['state']))
        if rule is None:
            continue
        expires = p['valid_until']
        for later in situations:                        # a later 'resolved' of the same kind and target ends it
            lp = later['payload']
            if (lp['state'] == 'resolved' and lp['situation_kind'] == p['situation_kind']
                    and rev.target_key(later['target']) == rev.target_key(ev['target'])
                    and rev.tp_key(later['occurred_at']) >= rev.tp_key(ev['occurred_at'])
                    and (expires is None or rev.tp_key(later['occurred_at']) < rev.tp_key(expires))):
                expires = later['occurred_at']
                break
        kind, domain, tag = rule
        out.append(_reaction(subject, ev['target'], [ev], domain=domain, tag=tag, kind=kind,
                             started_at=ev['occurred_at'], expires_at=expires,
                             explanation=[f"situation:{p['situation_kind']}:{p['state']}"], rule_version=version))
    for ev in mine:
        p = ev['payload']
        if (ev['kind'] == 'treatment' and p['treatment_kind'] in ('recognition', 'support')
                and p['outcome'] == 'positive') or (ev['kind'] == 'promise_resolved' and p['resolution'] == 'kept'):
            label = 'treatment:' + p['treatment_kind'] if ev['kind'] == 'treatment' else 'promise:kept'
            out.append(_reaction(subject, ev['target'], [ev], domain='achievement', tag='encouraged',
                                 kind='achievement', started_at=ev['occurred_at'], expires_at=None,
                                 explanation=[label], rule_version=version))
    return out


def _merge_domains(reactions):
    """Same-domain reactions with different tags become one 'mixed' reaction; the merged ones are explained."""
    by_domain, out, suppressed = {}, [], []
    for r in reactions:
        by_domain.setdefault(r['domain'], []).append(r)
    for domain, rs in sorted(by_domain.items()):
        if len({r['tag'] for r in rs}) <= 1:
            out.extend(rs)
            continue
        rs.sort(key=lambda r: (r['priority'], r['reaction_id']))
        lead = deepcopy(rs[0])
        lead['tag'] = 'mixed'
        lead['source_ids'] = sorted({s for r in rs for s in r['source_ids']})
        lead['started_at'] = min((r['started_at'] for r in rs), key=rev.tp_key)
        lead['expires_at'] = None if any(r['expires_at'] is None for r in rs) \
            else max((r['expires_at'] for r in rs), key=rev.tp_key)
        lead['visibility'] = 'public' if all(r['visibility'] == 'public' for r in rs) else 'private'
        lead['confidence'] = min((r['confidence'] for r in rs), key=rev.CONFIDENCES.index)
        lead['explanation'] = sorted({e for r in rs for e in r['explanation']}) + ['mixed:' + domain]
        lead['reaction_id'] = 'r:' + _sha([lead['source_ids'], rev.subject_key(lead['subject']), domain,
                                           lead['rule_version']])[:32]
        out.append(lead)
        suppressed.extend({'reaction_id': r['reaction_id'], 'reason': f'merged_into:{lead["reaction_id"]}'}
                          for r in rs)
    return out, suppressed


def _view_ok(feature_view):
    if not isinstance(feature_view, dict) or feature_view.get('status') not in ('missing', 'stored'):
        raise ValueError('feature_view must come from personality.core.feature_view')
    return feature_view.get('origin') != 'unknown'


def project_reactions(events, relationship_state, *, feature_view, as_of, known_through, policy, calendar):
    """Every reaction active at `as_of` for the state's subject, merged per domain, plus the mood.

    `feature_view` is core.feature_view(profile, 'reactions'): a missing profile still reacts to recorded
    events (evidence only); an unknown-origin identity stays neutral. Returns {schema_version, mode,
    rule_version, subject, as_of, known_through, reactions, mood, suppressed, inactive, input_hash}."""
    pol = validate_reaction_policy(policy)
    as_of = rev.time_point(as_of, 'as_of')
    rev.validate_calendar(calendar)
    subject = relationship_state['subject']
    version = f"{REACTIONS_VERSION}:{pol['rule_version']}"
    base = {'schema_version': rev.RELATIONSHIP_SCHEMA_VERSION, 'mode': pol['mode'], 'rule_version': version,
            'subject': deepcopy(subject), 'as_of': as_of, 'known_through': known_through,
            'input_hash': _sha(sorted(rev.canonical_payload_hash(ev) for ev in events
                                      if rev.same_person(ev['subject'], subject)))}
    if not _view_ok(feature_view):
        return dict(base, reactions=[], suppressed=[], inactive=[], neutral_reason='origin_unknown',
                    mood=select_mood([], as_of=as_of, policy=policy))
    selected = rev.select_active(list(events), as_of=as_of, known_through=known_through)
    mine = [ev for ev in selected['active'] if rev.same_person(ev['subject'], subject)]
    by_source = {ev['source_event_id']: ev for ev in mine}
    candidates = (_breach_reactions(subject, relationship_state, by_source, version)
                  + _role_reactions(subject, mine, pol['sustained_role_windows'], version)
                  + _situation_reactions(subject, mine, version))
    active = [r for r in candidates if _is_active(r, as_of)]
    inactive = sorted(({'reaction_id': r['reaction_id'], 'reason': 'expired'} for r in candidates
                       if not _is_active(r, as_of)), key=lambda x: x['reaction_id'])
    merged, suppressed = _merge_domains(active)
    merged.sort(key=lambda r: _rank(r, pol))
    return dict(base, reactions=merged, suppressed=suppressed, inactive=inactive, neutral_reason=None,
                mood=select_mood(merged, as_of=as_of, policy=policy))


def _rank(r, pol):
    return (r['priority'], -pol['severity'].get(r['tag'], 0.0), tuple(-x for x in rev.tp_key(r['started_at'])),
            r['source_ids'][0] if r['source_ids'] else '', r['reaction_id'])


def select_mood(reactions, *, as_of, policy):
    """One primary tag plus at most two non-contradictory secondary tags from the reactions active at `as_of`,
    ranked by priority, approved severity, recency, then source ID. Suppressed candidates are explained."""
    pol = validate_reaction_policy(policy)
    as_of = rev.time_point(as_of, 'as_of')
    ranked = sorted((r for r in reactions if _is_active(r, as_of)), key=lambda r: _rank(r, pol))
    if not ranked:
        return {'primary': 'neutral', 'secondary': [], 'ranked': [], 'suppressed': []}
    primary, chosen, suppressed = ranked[0], [ranked[0]['tag']], []
    secondary = []
    for r in ranked[1:]:
        if r['tag'] in chosen:
            suppressed.append({'reaction_id': r['reaction_id'], 'reason': 'duplicate_tag'})
        elif any(frozenset((r['tag'], t)) in CONTRADICTORY for t in chosen):
            suppressed.append({'reaction_id': r['reaction_id'], 'reason': 'contradicts_' + primary['tag']
                               if frozenset((r['tag'], primary['tag'])) in CONTRADICTORY else 'contradicts_secondary'})
        elif len(secondary) >= MAX_SECONDARY:
            suppressed.append({'reaction_id': r['reaction_id'], 'reason': 'secondary_limit'})
        else:
            secondary.append(r['tag'])
            chosen.append(r['tag'])
    return {'primary': primary['tag'], 'secondary': secondary, 'ranked': [r['reaction_id'] for r in ranked],
            'suppressed': suppressed}


def reaction_context(result):
    """Consumer view: neutral (identical for off and observe) unless the mode is 'enabled'."""
    if result.get('mode') != 'enabled':
        return {'status': 'neutral', 'subject': deepcopy(result.get('subject')), 'mood': 'neutral',
                'secondary': [], 'reactions': []}
    return {'status': 'enabled', 'subject': deepcopy(result['subject']), 'mood': result['mood']['primary'],
            'secondary': list(result['mood']['secondary']),
            'reactions': [{k: deepcopy(r[k]) for k in ('reaction_id', 'target', 'domain', 'tag', 'source_ids',
                                                        'started_at', 'expires_at', 'visibility')}
                          for r in result['reactions']]}


def public_context(result, *, policy):
    """Writer view: only reactions whose every source is public, framed as the fictional character's own
    interior state (never a quote, never a claim about a real person). Neutral unless enabled."""
    if result.get('mode') != 'enabled':
        return reaction_context(result)
    public = [r for r in result['reactions'] if r['visibility'] == 'public']
    mood = select_mood(public, as_of=result['as_of'], policy=policy)
    return {'status': 'enabled', 'subject': deepcopy(result['subject']), 'mood': mood['primary'],
            'secondary': mood['secondary'], 'framing': 'fictional_character_interior',
            'reactions': [{k: deepcopy(r[k]) for k in ('target', 'domain', 'tag', 'source_ids')} for r in public]}


# -------------------------------------------------------------------------------- evolution candidates ---

CANDIDATE_REQUIRED = ('event_id', 'source_event_id', 'season', 'expected_revision', 'rule_version', 'reason',
                      'kind', 'review_status', 'reviewer', 'feature_scope', 'supporting_source_ids')
CANDIDATE_OPTIONAL = ('add', 'remove', 'slider', 'step', 'tick', 'evidence')
CORE_FIELDS = ('event_id', 'source_event_id', 'season', 'expected_revision', 'rule_version', 'reason', 'kind',
               'add', 'remove', 'slider', 'step', 'tick', 'evidence')


def validate_evolution_candidate(candidate, *, profile, source_events, slider_band=None):
    """Review a durable trait/slider candidate for the evolution plan's arbiter (source 'RELATIONSHIPS').

    Returns {status: 'review_required'|'ready'|'blocked', candidate, reasons, support}. Never mutates the
    profile or calls the store. Checks fields, subject, season cutoff, active and non-duplicate support,
    then the core's own validity (core.apply_change on a copy: revision, annual slot, origin, traits, band).
    `slider_band` (lo, hi) is the caller's database constraint when it differs from the code's band (a database
    without 2027_01_17 is (30, 30)); a single-slider move outside it reports blocked_slider_band."""
    reasons, support = [], {'active': [], 'excluded': []}
    if not isinstance(candidate, dict):
        return {'status': 'blocked', 'candidate': deepcopy(candidate), 'reasons': ['invalid_candidate'],
                'support': support}
    cand = deepcopy(candidate)
    missing = [k for k in CANDIDATE_REQUIRED if k not in cand]
    extra = sorted(set(cand) - set(CANDIDATE_REQUIRED) - set(CANDIDATE_OPTIONAL))
    if missing:
        reasons.append('missing_fields:' + ','.join(missing))
    if extra:
        reasons.append('unexpected_fields:' + ','.join(extra))
    if cand.get('review_status') not in ('pending', 'approved'):
        reasons.append('invalid_review_status')
    scope = cand.get('feature_scope')
    if not isinstance(scope, list) or not scope or not all(isinstance(s, str) and s for s in scope):
        reasons.append('invalid_feature_scope')
    sids = cand.get('supporting_source_ids')
    if not isinstance(sids, list) or not sids or not all(isinstance(s, str) and s for s in sids):
        reasons.append('no_support')
        sids = []
    elif len(set(sids)) != len(sids):
        reasons.append('duplicate_support')
    if missing:
        return {'status': 'blocked', 'candidate': cand, 'reasons': reasons, 'support': support}

    identity = {'role': profile.get('role'), 'persona_id': profile.get('persona_id')}
    season = cand['season']
    selected = rev.select_active(list(source_events))
    active = {ev['source_event_id']: ev for ev in selected['active']}
    excluded = {x['source_event_id']: x['reason'] for x in selected['excluded']}
    for sid in dict.fromkeys(sids):
        ev = active.get(sid)
        if ev is None:
            reasons.append(f"support_{excluded.get(sid, 'missing')}:{sid}")
            support['excluded'].append(sid)
            continue
        if ev['subject']['role'] != identity['role'] or ev['subject']['persona_id'] != identity['persona_id'] \
                or ev['universe_id'] != profile.get('universe_id'):
            reasons.append(f'support_other_subject:{sid}')
            support['excluded'].append(sid)
            continue
        if isinstance(season, int) and ev['occurred_at']['season'] > season:
            reasons.append(f'support_after_cutoff:{sid}')
            support['excluded'].append(sid)
            continue
        support['active'].append(sid)

    core_event = {k: deepcopy(cand[k]) for k in CORE_FIELDS if k in cand}
    try:
        after, _ = core.apply_change(deepcopy(profile), core_event)
    except core.ConflictError as e:
        reasons.append('stale_revision' if 'revision' in str(e) else 'annual_slot_used')
    except core.PersonalityError as e:
        msg = str(e)
        if 'slider total' in msg:
            reasons.append('blocked_slider_band')
        elif 'unknown' in msg:
            reasons.append('origin_unknown')
        else:
            reasons.append('invalid_change:' + msg)
    except (KeyError, TypeError) as e:
        reasons.append(f'invalid_change:{e}')
    else:
        if slider_band is not None and cand['kind'] == 'slider':
            lo, hi = slider_band
            if not lo <= sum(after['sliders'].values()) <= hi:
                reasons.append('blocked_slider_band')
    if reasons:
        status = 'blocked'
    elif cand['review_status'] == 'approved' and isinstance(cand['reviewer'], str) and cand['reviewer'].strip():
        status = 'ready'
    else:
        status = 'review_required'
    return {'status': status, 'candidate': cand, 'reasons': reasons, 'support': support}
