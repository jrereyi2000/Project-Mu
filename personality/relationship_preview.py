"""Offline relationships preview (docs/in_progress/PERSONALITY_RELATIONSHIPS_PLAN.md §2.7, unit U5).

  python3 -m personality.relationship_preview --input fixture.json --out report.json
  python3 -m personality.relationship_preview --stress [--teams 32] [--seasons 9] [--players 53] --out stress.json

Pure and offline: no DSN, database driver, seed, apply flag, model call or clock inference in the preview.
The report is deterministic JSON (sorted keys, no generated timestamp) and replaces --out atomically.
Exit 0 on success; 2 with a structured error ({"error": {"code", "message"}} on stdout) for invalid JSON or
schema, writing nothing.

Input document:
  {schema_version, subject: PersonRef, events: [envelope], profile: null | core profile, policy,
   calendar: {season: ticks}, as_of: {season, tick}, known_through: int | null, team_registry: {team_key: {...}},
   baseline_events: null | [envelope], candidates: [evolution candidate],
   sensitivity: optional [{name, policy}]   (alternative trial policies, reported side by side)}

Report: input/policy hashes, cutoffs, factual context (active and excluded facts), consumer context (neutral
unless the policy is enabled), private audit (full state and reactions; JR-only), reactions, primary/secondary
mood, candidate validation, an optional baseline diff and the sensitivity table.

--stress builds a synthetic multi-cohort league (teams x seasons x players, 2026 onward) and reports event
count, projection seconds and peak memory for one subject's full replay: a measurement for the R5 decision, not
a threshold. Its timings are the one non-deterministic output, so it never shares a file with a preview.
"""
import argparse
from copy import deepcopy
import hashlib
import json
import os
import sys
import tempfile

from personality import core
from personality import reactions as rx
from personality import relationship_events as rev
from personality import relationships as rel

PREVIEW_VERSION = 'relationship-preview-v1'
DOCUMENT_FIELDS = ('schema_version', 'subject', 'events', 'profile', 'policy', 'calendar', 'as_of', 'known_through',
                   'team_registry', 'baseline_events', 'candidates')
OPTIONAL_FIELDS = ('sensitivity',)


class PreviewError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _sha(value):
    return hashlib.sha256(rev.canonical_json(value).encode('utf-8')).hexdigest()


def _document(document):
    if not isinstance(document, dict):
        raise PreviewError('invalid_document', 'the input must be a JSON object')
    missing = [k for k in DOCUMENT_FIELDS if k not in document]
    extra = sorted(set(document) - set(DOCUMENT_FIELDS) - set(OPTIONAL_FIELDS))
    if missing or extra:
        raise PreviewError('invalid_document', f'missing fields {missing}, unexpected fields {extra}')
    if document['schema_version'] != rev.RELATIONSHIP_SCHEMA_VERSION:
        raise PreviewError('schema_version', f"unsupported schema_version {document['schema_version']!r}")
    for key in ('events', 'candidates'):
        if not isinstance(document[key], list):
            raise PreviewError('invalid_document', f'{key} must be a list')
    if document['baseline_events'] is not None and not isinstance(document['baseline_events'], list):
        raise PreviewError('invalid_document', 'baseline_events must be a list or null')
    kt = document['known_through']
    if kt is not None and (isinstance(kt, bool) or not isinstance(kt, int) or kt < 0):
        raise PreviewError('invalid_document', 'known_through must be an int >= 0 or null')
    sens = document.get('sensitivity', [])
    if not isinstance(sens, list) or any(not isinstance(s, dict) or set(s) != {'name', 'policy'}
                                         or not isinstance(s['name'], str) for s in sens):
        raise PreviewError('invalid_document', 'sensitivity is a list of {name, policy}')
    return document


def _subject(document):
    s = document['subject']
    if not isinstance(s, dict):
        raise PreviewError('invalid_subject', 'subject must be a PersonRef')
    rev.person_ref(s, s.get('universe_id'), 'subject')
    rev._text(s['universe_id'], 'subject.universe_id')
    return s


def _profile_view(document, subject):
    profile = document['profile']
    if profile is None:
        return None, core.feature_view(None, 'reactions')
    core.validate_profile(profile)
    if (profile['universe_id'], profile['role'], profile['persona_id']) != \
            (subject['universe_id'], subject['role'], subject['persona_id']):
        raise PreviewError('profile_mismatch', 'the profile belongs to another identity than the subject')
    return profile, core.feature_view(profile, 'reactions')


def _run(events, *, subject, view, document, policy):
    common = dict(as_of=document['as_of'], known_through=document['known_through'], policy=policy,
                  calendar=document['calendar'])
    state = rel.project_relationships(events, subject=subject, **common)
    reacts = rx.project_reactions(events, state, feature_view=view, **common)
    return state, reacts


def _edge_summary(state):
    return {rev.target_key(e['target']): {'status': e['status'], 'trust': e['trust'],
                                          'tier': e['attachment']['tier'],
                                          'open_breaches': len(e['unresolved_breaches'])} for e in state['edges']}


def _diff(base, now):
    a, b = _edge_summary(base[0]), _edge_summary(now[0])
    changed = {k: {'baseline': a[k], 'current': b[k]} for k in sorted(set(a) & set(b)) if a[k] != b[k]}
    mood_a = {'primary': base[1]['mood']['primary'], 'secondary': base[1]['mood']['secondary']}
    mood_b = {'primary': now[1]['mood']['primary'], 'secondary': now[1]['mood']['secondary']}
    return {'added_edges': sorted(set(b) - set(a)), 'removed_edges': sorted(set(a) - set(b)),
            'changed_edges': changed, 'mood': None if mood_a == mood_b else {'baseline': mood_a, 'current': mood_b}}


def preview(document):
    """The report for one preview document (pure; raises PreviewError, RelationshipEventError, PolicyError,
    ReactionPolicyError or PersonalityError on invalid input)."""
    doc = _document(deepcopy(document))
    subject = _subject(doc)
    policy = rel.validate_policy(doc['policy'])
    rx.validate_reaction_policy(doc['policy'])
    registry = rev.validate_team_registry(doc['team_registry'])
    rev.validate_calendar(doc['calendar'])
    as_of = rev.time_point(doc['as_of'], 'as_of')
    profile, view = _profile_view(doc, subject)
    events = rev.validate_event_batch(doc['events'], calendar=doc['calendar'], team_registry=registry)
    for ev in events:
        if ev['universe_id'] != subject['universe_id']:
            raise PreviewError('cross_universe', 'every event belongs to the subject universe')
    state, reacts = _run(events, subject=subject, view=view, document=doc, policy=doc['policy'])
    selected = rev.select_active(events, as_of=as_of, known_through=doc['known_through'])
    mine = [ev for ev in selected['active'] if rev.same_person(ev['subject'], subject)]
    candidates = []
    for cand in doc['candidates']:
        if profile is None:
            candidates.append({'status': 'blocked', 'candidate': deepcopy(cand), 'reasons': ['profile_missing'],
                               'support': {'active': [], 'excluded': []}})
        else:
            candidates.append(rx.validate_evolution_candidate(cand, profile=profile, source_events=events))
    baseline = None
    if doc['baseline_events'] is not None:
        base_events = rev.validate_event_batch(doc['baseline_events'], calendar=doc['calendar'], team_registry=registry)
        baseline = _diff(_run(base_events, subject=subject, view=view, document=doc, policy=doc['policy']),
                         (state, reacts))
    sensitivity = []
    for variant in doc.get('sensitivity', []):
        alt = _run(events, subject=subject, view=view, document=doc, policy=variant['policy'])
        sensitivity.append({'name': variant['name'], 'policy_hash': _sha(rel.validate_policy(variant['policy'])),
                            'edges': _edge_summary(alt[0]), 'mood': alt[1]['mood']['primary']})
    return {
        'schema_version': rev.RELATIONSHIP_SCHEMA_VERSION, 'preview_version': PREVIEW_VERSION,
        'input_hash': _sha(doc), 'policy_hash': _sha(policy), 'registry_hash': rev.registry_hash(registry),
        'cutoffs': {'as_of': as_of, 'known_through': doc['known_through']},
        'subject': deepcopy(subject), 'mode': policy['mode'],
        'factual': {'active': [{'source_event_id': ev['source_event_id'], 'kind': ev['kind'],
                                'occurred_at': ev['occurred_at'], 'target': ev['target'],
                                'visibility': ev['visibility']} for ev in mine],
                    'excluded': selected['excluded']},
        'consumer': {'relationships': rel.relationship_context(state), 'reactions': rx.reaction_context(reacts)},
        'audit': {'relationships': state, 'reactions': reacts},
        'reactions': reacts['reactions'],
        'mood': {'primary': reacts['mood']['primary'], 'secondary': reacts['mood']['secondary']},
        'candidates': candidates, 'baseline_diff': baseline, 'sensitivity': sensitivity,
    }


# ---------------------------------------------------------------------------------------------- stress ---

def synthetic_league(*, teams=32, seasons=9, players=53, universe='u-stress', start=2026):
    """A synthetic league's tenure, role and result facts (one cohort per team per season, some carry over):
    (document, event count). Deterministic; no randomness."""
    registry = {f'team:{t}': {'team_id': t} for t in range(1, teams + 1)}
    events = []
    for t in range(1, teams + 1):
        for s in range(start, start + seasons):
            for p in range(players):
                pid = f'p{t}-{(s - start) // 3}-{p}'          # each cohort plays three seasons
                subject = {'kind': 'person', 'universe_id': universe, 'role': 'player', 'persona_id': pid}
                base = {'schema_version': rev.RELATIONSHIP_SCHEMA_VERSION, 'universe_id': universe,
                        'source_kind': 'stress', 'source_refs': [], 'subject': subject,
                        'target': {'kind': 'team', 'universe_id': universe, 'team_key': f'team:{t}'},
                        'actor': None, 'basis': 'verified_simulation', 'confidence': 'high', 'reviewer': None,
                        'evidence_scope': [], 'visibility': 'public',
                        'attribution': {'responsible': [], 'reason_code': None, 'evidence_refs': []},
                        'supersedes_source_event_id': None, 'occurred_at': {'season': s, 'tick': 1}}
                sid = f'stress:tenure:{s}:{t}:{pid}'
                events.append(dict(base, event_id=sid, source_event_id=sid, kind='tenure_window', payload={
                    'team_key': f'team:{t}', 'start': {'season': s, 'tick': 1},
                    'end': {'season': s + 1, 'tick': 1}, 'coverage': 'complete'}))
                sid = f'stress:role:{s}:{t}:{pid}'
                events.append(dict(base, event_id=sid, source_event_id=sid, kind='role_window', payload={
                    'team_key': f'team:{t}', 'start': {'season': s, 'tick': 1}, 'end': {'season': s, 'tick': 2},
                    'agreed_role': 'STARTER', 'observed_role': 'STARTER',
                    'assessment': 'satisfied' if p % 3 else 'unsatisfied', 'assessment_basis_refs': []}))
    subject = deepcopy(events[0]['subject'])
    document = {'schema_version': rev.RELATIONSHIP_SCHEMA_VERSION, 'subject': subject, 'events': events,
                'profile': None, 'policy': {'mode': 'observe', 'rule_version': 'stress'},
                'calendar': {s: 32 for s in range(start, start + seasons + 1)},
                'as_of': {'season': start + seasons, 'tick': 1}, 'known_through': None, 'team_registry': registry,
                'baseline_events': None, 'candidates': []}
    return document, len(events)


def stress(*, teams=32, seasons=9, players=53):
    import time
    import tracemalloc
    document, count = synthetic_league(teams=teams, seasons=seasons, players=players)
    tracemalloc.start()
    t0 = time.perf_counter()
    report = preview(document)
    seconds = time.perf_counter() - t0
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return {'kind': 'stress', 'teams': teams, 'seasons': seasons, 'players_per_team_season': players,
            'event_count': count, 'subject_edges': len(report['audit']['relationships']['edges']),
            'projection_seconds': round(seconds, 3), 'peak_memory_kb': peak // 1024,
            'note': 'measurement only (plan R5); no latency threshold is implied'}


# ------------------------------------------------------------------------------------------------- CLI ---

def _write(path, payload):
    text = json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix='.relationship_preview.', suffix='.tmp', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _error(code, message):
    print(json.dumps({'error': {'code': code, 'message': message}}, sort_keys=True))
    return 2


def main(argv=None):
    parser = argparse.ArgumentParser(prog='python3 -m personality.relationship_preview',
                                     description='Offline relationships preview (no database, no model).')
    parser.add_argument('--input', help='preview document (JSON)')
    parser.add_argument('--out', required=True, help='report path, replaced atomically')
    parser.add_argument('--stress', action='store_true', help='synthetic multi-cohort replay measurement')
    parser.add_argument('--teams', type=int, default=32)
    parser.add_argument('--seasons', type=int, default=9)
    parser.add_argument('--players', type=int, default=53)
    args = parser.parse_args(argv)
    if args.stress:
        if args.input:
            return _error('usage', '--stress takes no --input')
        _write(args.out, stress(teams=args.teams, seasons=args.seasons, players=args.players))
        return 0
    if not args.input:
        return _error('usage', '--input is required (or --stress)')
    try:
        with open(args.input, encoding='utf-8') as fh:
            document = json.load(fh, parse_constant=lambda c: (_ for _ in ()).throw(ValueError(f'invalid constant {c}')))
    except (OSError, ValueError) as e:
        return _error('invalid_json', str(e))
    try:
        report = preview(document)
    except PreviewError as e:
        return _error(e.code, str(e))
    except rev.RelationshipEventError as e:
        return _error(e.code, str(e))
    except (rel.PolicyError, rx.ReactionPolicyError) as e:
        return _error('invalid_policy', str(e))
    except (core.PersonalityError, KeyError, TypeError) as e:
        return _error('invalid_document', f'{type(e).__name__}: {e}')
    _write(args.out, report)
    return 0


if __name__ == '__main__':
    sys.exit(main())
