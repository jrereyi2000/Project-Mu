"""Reviewed real-coach profiles (docs/in_progress/REAL_COACH_PROFILES_PLAN.md §2.1-§2.2).

One file per 2026 head coach or coordinator in newsroom/config/coach_personas/<TEAM>_<ROLE>.json
(schema `coach-profile-v1`). Pure apart from reading files: no database, network or model calls.

  seed_index()                         {(table, team, role): row} parsed from db/seed_clean_db.py (no import,
                                       so psycopg is never loaded)
  load_profiles(directory, teams=)     the profile docs, sorted by file name; `_TEMPLATE.json` and other
                                       `_`-prefixed files are skipped; each doc carries `_file` (its base name)
  validate_profile_file(doc, ...)      list of error messages ([] = valid); never raises on bad content
  core_initial_values(doc)             {sliders, traits, evidence} for core.initialize(identity, 'real', ...)
  lifecycle_entries(docs)              reviewed coach manifest entries for the lifecycle plan (approved/edited)
  persona_for(doc, view=None)          render_persona-ready persona; a stored core view's sliders/traits win
  tie_events(doc, resolve)             (meaningful_interaction envelopes, unresolved ties)

Only `approved` and `edited` files leave this module: core_initial_values, persona_for and tie_events refuse
a draft or rejected file. Keys starting with `_` are in-memory annotations and never part of a file.
"""
from __future__ import annotations

import ast
from datetime import date
import hashlib
import json
from pathlib import Path
import re

from newsroom import personas as legacy
from personality import core
from personality import relationship_events as rev

SCHEMA_VERSION = 'coach-profile-v1'
SOURCE_CUTOFF = '2025-12-31'
MAX_QUOTE_WORDS = 15
PROFILE_DIR = 'newsroom/config/coach_personas'

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_FILE = REPO_ROOT / 'db' / 'seed_clean_db.py'
ROLES = ('HC', 'OC', 'DC')
TABLES = {'COACHES_DATA': ('HC',), 'COORDINATORS_DATA': ('OC', 'DC')}
REVIEW_STATUSES = ('draft', 'approved', 'edited', 'rejected')
REVIEWED = ('approved', 'edited')
CONFIDENCES = ('low', 'medium', 'high')
TIE_KINDS = ('coach', 'player', 'gm')
SLIDER_RANGE = (legacy.SLIDER_BUDGET_MIN, legacy.SLIDER_BUDGET_MAX)
TRAIT_COUNT = (3, 5)
VIEW_COUNT = (6, 10)
VIEW_TAGS = (1, 3)
SIGNATURE_COUNT = (2, 5)
START_SEASON = 2026
TIE_TIME = {'season': START_SEASON, 'tick': 1}
FIELDS = ('schema_version', 'key', 'name', 'role', 'team', 'seed_ref', 'sliders', 'slider_evidence', 'traits',
          'trait_evidence', 'views', 'signatures', 'want', 'fear', 'voice', 'ties', 'evidence', 'review')
_WORD = re.compile(r"[a-z0-9]+(?:'[a-z0-9]+)*")


# ------------------------------------------------------------------------------------------------ files ---

def _resolve_dir(directory):
    path = Path(directory)
    return path if path.is_absolute() else REPO_ROOT / path


def seed_index(path=SEED_FILE) -> dict:
    """{(table, team, role): {table, team, role, name, start_season}} for the 32 HCs and 64 coordinators.

    The literals are read with ast so the seeding module (and psycopg) is never imported."""
    tree = ast.parse(Path(path).read_text())
    rows = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        name = getattr(node.targets[0], 'id', None)
        if name not in TABLES:
            continue
        for row in ast.literal_eval(node.value):
            if name == 'COACHES_DATA':
                team, person, role, start = row[0], row[1], 'HC', row[-1]
            else:
                team, role, person, start = row[0], row[1], row[2], row[-1]
            rows[(name, team, role)] = dict(table=name, team=team, role=role, name=person, start_season=start)
    return rows


def load_profiles(directory=PROFILE_DIR, *, teams=None) -> list[dict]:
    """Every `<TEAM>_<ROLE>.json` in the directory (optionally only these teams), sorted by file name."""
    wanted = {t.strip().upper() for t in teams} if teams else None
    docs = []
    for path in sorted(_resolve_dir(directory).glob('*.json')):
        if path.name.startswith('_'):
            continue
        if wanted is not None and path.stem.rsplit('_', 1)[0].upper() not in wanted:
            continue
        doc = json.loads(path.read_text())
        if isinstance(doc, dict):
            doc['_file'] = path.name
        docs.append(doc)
    return docs


def file_doc(doc) -> dict:
    """The doc as it is written to disk: the in-memory `_` annotations removed."""
    return {k: v for k, v in doc.items() if not k.startswith('_')}


def content_sha256(doc) -> str:
    return hashlib.sha256(json.dumps(file_doc(doc), sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode('utf-8')).hexdigest()


# ------------------------------------------------------------------------------------------- validation ---

def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _iso(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _words(text):
    return _WORD.findall(text.lower()) if isinstance(text, str) else []


def _ngrams(words, n):
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def _text_fields(doc):
    """(label, text) for every free-text field a profile writes in its own words."""
    out = [(f, doc.get(f)) for f in ('want', 'fear', 'voice')]
    for i, v in enumerate(doc.get('views') or []):
        out.append((f'views[{i}]', v.get('text') if isinstance(v, dict) else v))
    for i, s in enumerate(doc.get('signatures') or []):
        out.append((f'signatures[{i}]', s.get('text') if isinstance(s, dict) else s))
    return [(label, text) for label, text in out if isinstance(text, str)]


def _check_identity(doc, index, errors):
    key, team, role = doc.get('key'), doc.get('team'), doc.get('role')
    if role not in ROLES:
        errors.append(f'role must be one of {list(ROLES)}, got {role!r}')
    if not _nonempty(team):
        errors.append('team must be a team code')
    if not _nonempty(doc.get('name')):
        errors.append('name must be nonempty')
    if key != f'coach:{team}:{role}':
        errors.append(f'key {key!r} does not match team and role (expected coach:{team}:{role})')
    fname = doc.get('_file')
    if fname and not fname.startswith('_') and fname != f'{team}_{role}.json':
        errors.append(f'file name {fname} does not match team and role (expected {team}_{role}.json)')
    ref = doc.get('seed_ref')
    if not isinstance(ref, dict):
        errors.append('seed_ref must be {table, team, role, name}')
        return
    table = ref.get('table')
    row = index.get((table, ref.get('team'), ref.get('role')))
    if table not in TABLES or row is None:
        errors.append(f"seed_ref {table}/{ref.get('team')}/{ref.get('role')} matches no seed row")
        return
    if ref.get('team') != team or ref.get('role') != role:
        errors.append('seed_ref team and role differ from the profile')
    if ref.get('name') != row['name']:
        errors.append(f"seed_ref name {ref.get('name')!r} differs from the seed row ({row['name']!r})")
    if doc.get('name') != row['name']:
        errors.append(f"name {doc.get('name')!r} differs from the seed row ({row['name']!r})")


def _check_citations(ids, label, evidence, errors, *, strong):
    if not isinstance(ids, list) or not ids:
        errors.append(f'{label} cites no evidence')
        return
    for eid in ids:
        ev = evidence.get(eid)
        if ev is None:
            errors.append(f'{label} cites unknown evidence {eid!r}')
        elif strong and ev.get('confidence') == 'low':
            errors.append(f'{label} cites low-confidence {eid}; low sources support only views, voice or signatures')


def _check_evidence(doc, errors):
    evidence = {}
    items = doc.get('evidence')
    if not isinstance(items, list) or not items:
        errors.append('evidence must be a nonempty list')
        return evidence
    cutoff = date.fromisoformat(SOURCE_CUTOFF)
    for i, ev in enumerate(items):
        if not isinstance(ev, dict):
            errors.append(f'evidence[{i}] must be an object')
            continue
        eid = ev.get('id')
        label = f'evidence {eid}' if _nonempty(eid) else f'evidence[{i}]'
        if not _nonempty(eid):
            errors.append(f'{label} needs an id')
        elif eid in evidence:
            errors.append(f'duplicate evidence id {eid}')
        else:
            evidence[eid] = ev
        if not (isinstance(ev.get('url'), str) and re.match(r'https?://\S+$', ev['url'])):
            errors.append(f'{label} needs an http(s) url')
        if not _nonempty(ev.get('publisher')):
            errors.append(f'{label} needs a publisher')
        published = _iso(ev.get('published'))
        if published is None:
            errors.append(f'{label} published must be an ISO date')
        elif published > cutoff:
            errors.append(f'{label} published {published} is after the source cutoff {SOURCE_CUTOFF}')
        if _iso(ev.get('accessed')) is None:
            errors.append(f'{label} accessed must be an ISO date')
        if not _nonempty(ev.get('summary')):
            errors.append(f'{label} needs a summary')
        if ev.get('confidence') not in CONFIDENCES:
            errors.append(f'{label} confidence must be low, medium or high')
    return evidence


def _check_quotes(doc, evidence, errors):
    n = MAX_QUOTE_WORDS + 1
    grams = {}
    for eid, ev in evidence.items():
        for g in _ngrams(_words(ev.get('summary')), n):
            grams.setdefault(g, eid)
    for label, text in _text_fields(doc):
        for g in _ngrams(_words(text), n):
            if g in grams:
                errors.append(f'{label} copies more than {MAX_QUOTE_WORDS} consecutive words from {grams[g]}')
                break


def validate_profile_file(doc, *, catalogue=None, index=None) -> list[str]:
    """Every rule of §2.1 as messages ([] = valid). `_file`, when present, is checked against team and role."""
    if not isinstance(doc, dict):
        return ['profile must be a JSON object']
    catalogue = legacy.load_traits() if catalogue is None else catalogue
    index = seed_index() if index is None else index
    errors = []
    if doc.get('schema_version') != SCHEMA_VERSION:
        errors.append(f'schema_version must be {SCHEMA_VERSION}')
    unknown = sorted(k for k in doc if k not in FIELDS and not k.startswith('_'))
    if unknown:
        errors.append(f'unknown fields {unknown}')
    _check_identity(doc, index, errors)
    evidence = _check_evidence(doc, errors)

    sliders = doc.get('sliders')
    slider_ev = doc.get('slider_evidence') or {}
    if not isinstance(slider_ev, dict):
        errors.append('slider_evidence must be an object')
        slider_ev = {}
    if (not isinstance(sliders, dict) or set(sliders) != set(legacy.SLIDERS)
            or any(type(v) is not int or not 0 <= v <= 10 for v in sliders.values())):
        errors.append(f'sliders must be the six {list(legacy.SLIDERS)}, integers 0-10')
        sliders = None
    else:
        total = sum(sliders.values())
        if not SLIDER_RANGE[0] <= total <= SLIDER_RANGE[1]:
            errors.append(f'slider total {total} is outside {SLIDER_RANGE[0]}-{SLIDER_RANGE[1]}')
        for s in legacy.SLIDERS:
            if sliders[s] != 5 or s in slider_ev:
                _check_citations(slider_ev.get(s), f'slider {s}={sliders[s]}', evidence, errors, strong=True)
    for s in slider_ev:
        if s not in legacy.SLIDERS:
            errors.append(f'slider_evidence names unknown slider {s!r}')

    traits = doc.get('traits')
    trait_ev = doc.get('trait_evidence') or {}
    if not isinstance(trait_ev, dict):
        errors.append('trait_evidence must be an object')
        trait_ev = {}
    if not isinstance(traits, list) or len(traits) != len(set(map(str, traits))):
        errors.append('traits must be a list without duplicates')
        traits = None
    else:
        if not TRAIT_COUNT[0] <= len(traits) <= TRAIT_COUNT[1]:
            errors.append(f'traits must number {TRAIT_COUNT[0]}-{TRAIT_COUNT[1]}, got {len(traits)}')
        per_category = {}
        for t in traits:
            entry = catalogue.get(t)
            if entry is None:
                errors.append(f'trait {t} is not in the catalogue')
                continue
            if entry.get('roles', {}).get('coach', 0) <= 0:
                errors.append(f'trait {t} is not eligible for coaches')
            cat = entry.get('category')
            per_category[cat] = per_category.get(cat, 0) + 1
            if per_category[cat] == 3:
                errors.append(f'more than two traits in category {cat}')
            clash = sorted(set(entry.get('conflicts', [])) & set(traits))
            if clash and t < clash[0]:
                errors.append(f'trait {t} conflicts with {clash[0]}')
            _check_citations(trait_ev.get(t), f'trait {t}', evidence, errors, strong=True)
        for t in trait_ev:
            if t not in traits:
                errors.append(f'trait_evidence names {t}, which is not among the traits')
        if not errors and sliders is not None:
            try:   # the core's own rules are the final word on eligibility, conflicts and categories
                core.initialize(core.Identity('coach-profile-check', 'coach', 'check'), 'real', START_SEASON,
                                sliders=dict(sliders), traits=list(traits), catalogue=catalogue)
            except core.PersonalityError as e:
                errors.append(f'core rejects the profile: {e}')

    views = doc.get('views')
    if not isinstance(views, list) or not VIEW_COUNT[0] <= len(views) <= VIEW_COUNT[1]:
        errors.append(f'views must number {VIEW_COUNT[0]}-{VIEW_COUNT[1]}')
    for i, v in enumerate(views if isinstance(views, list) else []):
        if not isinstance(v, dict) or not _nonempty(v.get('text')):
            errors.append(f'views[{i}] needs text')
            continue
        when = v.get('when')
        if not isinstance(when, list) or not VIEW_TAGS[0] <= len(when) <= VIEW_TAGS[1]:
            errors.append(f'views[{i}] needs {VIEW_TAGS[0]}-{VIEW_TAGS[1]} when tags')
        else:
            bad = sorted(set(map(str, when)) - legacy.ALL_TAGS)
            if bad:
                errors.append(f'views[{i}] has unknown tags {bad}')

    sigs = doc.get('signatures')
    if not isinstance(sigs, list) or not SIGNATURE_COUNT[0] <= len(sigs) <= SIGNATURE_COUNT[1]:
        errors.append(f'signatures must number {SIGNATURE_COUNT[0]}-{SIGNATURE_COUNT[1]}')
    for i, s in enumerate(sigs if isinstance(sigs, list) else []):
        if not (_nonempty(s) or (isinstance(s, dict) and _nonempty(s.get('text')))):
            errors.append(f'signatures[{i}] needs text')
    for f in ('want', 'fear', 'voice'):
        if not _nonempty(doc.get(f)):
            errors.append(f'{f} must be nonempty')
    _check_quotes(doc, evidence, errors)

    ties = doc.get('ties', [])
    if not isinstance(ties, list):
        errors.append('ties must be a list')
        ties = []
    for i, tie in enumerate(ties):
        label = f'ties[{i}]'
        target = tie.get('target') if isinstance(tie, dict) else None
        if not isinstance(target, dict):
            errors.append(f'{label} needs a target')
            continue
        kind = target.get('kind')
        if kind not in TIE_KINDS:
            errors.append(f'{label} target kind {kind!r} is not allowed (coach, player or gm; no reporters)')
        elif kind == 'coach':
            ref = target.get('seed_ref')
            if not isinstance(ref, dict) or (ref.get('table'), ref.get('team'), ref.get('role')) not in index:
                errors.append(f'{label} coach target needs a seed_ref that matches a seed row')
            elif (ref.get('team'), ref.get('role')) == (doc.get('team'), doc.get('role')):
                errors.append(f'{label} is a tie to himself')
        elif not (_nonempty(target.get('name')) and _nonempty(target.get('team_2026'))):
            errors.append(f'{label} {kind} target needs name and team_2026')
        if tie.get('relationship_kind') not in rev.RELATIONSHIP_KINDS:
            errors.append(f'{label} relationship_kind must be one of {list(rev.RELATIONSHIP_KINDS)}')
        if tie.get('outcome') not in rev.OUTCOMES:
            errors.append(f'{label} outcome must be one of {list(rev.OUTCOMES)}')
        _check_citations(tie.get('evidence'), label, evidence, errors, strong=False)

    review = doc.get('review')
    if not isinstance(review, dict) or review.get('status') not in REVIEW_STATUSES:
        errors.append(f'review status must be one of {list(REVIEW_STATUSES)}')
    elif review['status'] in REVIEWED:
        if not _nonempty(review.get('reviewer')) or _iso(review.get('reviewed_at')) is None:
            errors.append('a reviewed profile names its reviewer and reviewed_at date')
    return errors


# ------------------------------------------------------------------------------------------ conversions ---

def _require_reviewed(doc):
    status = (doc.get('review') or {}).get('status')
    if status not in REVIEWED:
        raise ValueError(f"{doc.get('key')}: only approved or edited profiles leave the review tools (status {status!r})")


def _lowest(evs):
    return min((ev['confidence'] for ev in evs), key=CONFIDENCES.index)


def _core_entry(ids, by_id, reviewer):
    evs = [by_id[i] for i in ids]
    return dict(source=evs[0]['url'], date=evs[0]['published'], confidence=_lowest(evs), reviewer=reviewer,
                reason=' '.join(ev['summary'].strip() for ev in evs), features=['all'])


def core_initial_values(doc) -> dict:
    """{sliders, traits, evidence} for core.initialize(identity, 'real', season, **values)."""
    _require_reviewed(doc)
    by_id = {ev['id']: ev for ev in doc['evidence']}
    reviewer = doc['review']['reviewer']
    evidence = {}
    for s in legacy.SLIDERS:
        ids = (doc.get('slider_evidence') or {}).get(s)
        if ids:
            evidence['slider:' + s] = _core_entry(ids, by_id, reviewer)
    for t in doc['traits']:
        evidence['trait:' + t] = _core_entry(doc['trait_evidence'][t], by_id, reviewer)
    return dict(sliders=dict(doc['sliders']), traits=list(doc['traits']), evidence=evidence)


def lifecycle_entries(docs) -> list[dict]:
    """The reviewed coach manifest: one entry per approved or edited file, sorted by key."""
    out = []
    for doc in sorted(docs, key=lambda d: d.get('key', '')):
        review = doc.get('review') or {}
        if review.get('status') not in REVIEWED:
            continue
        out.append(dict(
            key=doc['key'], name=doc['name'], role='coach', origin='real',
            birth_key=f"curated-coach:{doc['team']}:{doc['role']}:{START_SEASON}",
            alias=dict(namespace='db-coach' if doc['role'] == 'HC' else 'db-coordinator',
                       seed_ref=dict(doc['seed_ref'])),
            initial_values=core_initial_values(doc),
            review_receipt=dict(reviewer=review['reviewer'], reviewed_at=review['reviewed_at'],
                                file_sha256=content_sha256(doc), schema_version=doc['schema_version'])))
    return out


def persona_for(doc, view=None, change_notes=None) -> dict:
    """The render_persona shape. A stored core feature view's sliders and traits replace the file's, so JR's
    overrides and later evolution show; the file keeps the voice, views, signatures and want/fear. Evolution
    change notes (personality.evolution_run.change_notes_for, newest first) pass through as `change_notes`."""
    _require_reviewed(doc)
    sliders, traits = dict(doc['sliders']), list(doc['traits'])
    if view is not None and view.get('status') == 'stored':
        sliders, traits = dict(view['sliders']), list(view['traits'])
    sigs = [dict(s) if isinstance(s, dict) else {'text': s} for s in doc['signatures']]
    out = dict(voice=doc['voice'], sliders=sliders, traits=traits,
               views=[dict(text=v['text'], when=list(v['when'])) for v in doc['views']],
               signatures=sigs, want=doc['want'], fear=doc['fear'])
    if change_notes:
        out['change_notes'] = [str(n) for n in change_notes]
    return out


def tie_events(doc, resolve) -> tuple[list[dict], list[dict]]:
    """(envelopes, unresolved). resolve(target) -> PersonRef or None; the coach himself is resolved as
    {'kind': 'coach', 'seed_ref': doc['seed_ref']}. Each envelope is a validated relationships-v1
    meaningful_interaction (basis reviewed_evidence, season 2026 tick 1, private, prehistory)."""
    _require_reviewed(doc)
    ties = doc.get('ties') or []
    unresolved = []
    subject = resolve({'kind': 'coach', 'seed_ref': dict(doc['seed_ref'])})
    if subject is None:
        return [], [dict(index=n, target=t['target'], reason='the coach himself is not resolved in this league')
                    for n, t in enumerate(ties, 1)]
    by_id = {ev['id']: ev for ev in doc['evidence']}
    envelopes = []
    for n, tie in enumerate(ties, 1):
        target = resolve(tie['target'])
        if target is None:
            unresolved.append(dict(index=n, target=tie['target'],
                                   reason='the other person is not in the league as the same person'))
            continue
        evs = [by_id[i] for i in tie['evidence']]
        sid = f"mu:coach-tie:{doc['key']}:{n}"
        envelope = {
            'schema_version': rev.RELATIONSHIP_SCHEMA_VERSION, 'universe_id': subject['universe_id'],
            'event_id': sid, 'source_event_id': sid, 'source_kind': 'coach_profile',
            'source_refs': [{'namespace': 'coach-profile', 'key': doc['key']}],
            'kind': 'meaningful_interaction', 'occurred_at': dict(TIE_TIME), 'subject': dict(subject),
            'target': dict(target), 'actor': None, 'basis': 'reviewed_evidence', 'confidence': _lowest(evs),
            'reviewer': doc['review']['reviewer'], 'evidence_scope': ['coach_profile'], 'visibility': 'private',
            'attribution': {'responsible': [], 'reason_code': None, 'evidence_refs': [ev['url'] for ev in evs]},
            'payload': {'relationship_kind': tie['relationship_kind'], 'outcome': tie['outcome'],
                        'significance': 'meaningful', 'opportunity_ref': f"coach-profile:{doc['key']}:tie:{n}",
                        'prehistory': True},
            'supersedes_source_event_id': None,
        }
        try:
            envelopes.append(rev.validate_event(envelope, team_registry={}))
        except rev.RelationshipEventError as e:
            unresolved.append(dict(index=n, target=tie['target'], reason=f'invalid envelope: {e}'))
    return envelopes, unresolved
