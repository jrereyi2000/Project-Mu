"""Reviewed real-inspired owner profiles (docs/in_progress/OWNERS_SCOUTS_VOTERS_PLAN.md §2.1-§2.2).

One file per team in newsroom/config/owner_personas/<TEAM>.json (schema `owner-profile-v1`): the coach-profile
schema (personality/coach_profiles.py, REAL_COACH_PROFILES_PLAN §2.1) with role OWNER, key `owner:<TEAM>` and a
seed_ref into OWNERS_DATA below. Each owner is a fictional character inspired by the real controlling owner as of
the eve of the 2026 season (2026-09-08). Pure apart from reading files: no database, network or model calls.

  OWNERS_DATA / owner_index()          the 32 owners {team: {table, team, name, start_season}}
  load_profiles(directory, teams=)     the profile docs, sorted by file name; `_`-prefixed files skipped
  validate_profile_file(doc, ...)      list of error messages ([] = valid); never raises on bad content
  core_initial_values(doc)             {sliders, traits, evidence} for core.initialize(identity, 'real', ...)
  lifecycle_entries(docs)              reviewed owner manifest entries (role owner, birth key curated-owner:<TEAM>:2026)
  persona_for(doc, view=None)          render_persona-ready persona (coach_profiles.persona_for)
  tie_events(doc, resolve)             (meaningful_interaction envelopes, unresolved ties); the owner himself is
                                       resolved as {'kind': 'owner', 'seed_ref': doc['seed_ref']}

The evidence, quote, view, signature, slider and review rules are the coach rules (coach_profiles helpers). The
differences: identity (role, key, file name, OWNERS_DATA), trait eligibility (role `owner`), and ties (coach, gm or
player; a coach tie names a 2026 seed seat). Only `approved` and `edited` files leave this module.
"""
from __future__ import annotations

import json
from pathlib import Path

from newsroom import personas as legacy
from personality import coach_profiles as cp
from personality import core
from personality import relationship_events as rev

SCHEMA_VERSION = 'owner-profile-v1'
SOURCE_CUTOFF = cp.SOURCE_CUTOFF   # eve of the 2026 opener (Sep 9, 2026), JR 2026-11-06
PROFILE_DIR = 'newsroom/config/owner_personas'
ROLE = 'OWNER'
TABLE = 'OWNERS_DATA'
START_SEASON = cp.START_SEASON
TIE_KINDS = cp.TIE_KINDS
FIELDS = cp.FIELDS

# (team, owner as of 2026-09-08, first season as controlling/principal owner or, for Green Bay, as president).
# Checked by JR on review; the profile files cite the sources.
OWNERS_DATA = [
    ('BUF', 'Terry Pegula', 2014),
    ('MIA', 'Stephen Ross', 2009),
    ('NE', 'Robert Kraft', 1994),
    ('NYJ', 'Woody Johnson', 2000),
    ('BAL', 'Steve Bisciotti', 2004),
    ('CIN', 'Mike Brown', 1991),
    ('CLE', 'Jimmy Haslam', 2012),
    ('PIT', 'Art Rooney II', 2017),
    ('HOU', 'Cal McNair', 2024),
    ('IND', 'Carlie Irsay-Gordon', 2025),
    ('JAX', 'Shad Khan', 2012),
    ('TEN', 'Amy Adams Strunk', 2015),
    ('DEN', 'Greg Penner', 2022),
    ('KC', 'Clark Hunt', 2006),
    ('LV', 'Mark Davis', 2011),
    ('LAC', 'Dean Spanos', 2018),
    ('DAL', 'Jerry Jones', 1989),
    ('NYG', 'John Mara', 2005),
    ('PHI', 'Jeffrey Lurie', 1994),
    ('WAS', 'Josh Harris', 2023),
    ('CHI', 'George McCaskey', 2025),
    ('DET', 'Sheila Ford Hamp', 2020),
    ('GB', 'Ed Policy', 2025),
    ('MIN', 'Zygi Wilf', 2005),
    ('ATL', 'Arthur Blank', 2002),
    ('CAR', 'David Tepper', 2018),
    ('NO', 'Gayle Benson', 2018),
    ('TB', 'Joel Glazer', 2014),
    ('AZ', 'Michael Bidwill', 2019),
    ('LAR', 'Stan Kroenke', 2010),
    ('SF', 'Jed York', 2024),
    ('SEA', 'Neeru Khosla', 2026),
]


def owner_index() -> dict:
    """{team: {table, team, name, start_season}} for the 32 owners."""
    return {team: dict(table=TABLE, team=team, name=name, start_season=start) for team, name, start in OWNERS_DATA}


def load_profiles(directory=PROFILE_DIR, *, teams=None) -> list[dict]:
    """Every `<TEAM>.json` in the directory (optionally only these teams), sorted by file name."""
    wanted = {t.strip().upper() for t in teams} if teams else None
    docs = []
    for path in sorted(cp._resolve_dir(directory).glob('*.json')):
        if path.name.startswith('_'):
            continue
        if wanted is not None and path.stem.upper() not in wanted:
            continue
        doc = json.loads(path.read_text())
        if isinstance(doc, dict):
            doc['_file'] = path.name
        docs.append(doc)
    return docs


file_doc = cp.file_doc
content_sha256 = cp.content_sha256


# ------------------------------------------------------------------------------------------- validation ---

def _check_identity(doc, index, errors):
    key, team, role = doc.get('key'), doc.get('team'), doc.get('role')
    if role != ROLE:
        errors.append(f'role must be {ROLE}, got {role!r}')
    if not cp._nonempty(team):
        errors.append('team must be a team code')
    if not cp._nonempty(doc.get('name')):
        errors.append('name must be nonempty')
    if key != f'owner:{team}':
        errors.append(f'key {key!r} does not match team (expected owner:{team})')
    fname = doc.get('_file')
    if fname and not fname.startswith('_') and fname != f'{team}.json':
        errors.append(f'file name {fname} does not match team (expected {team}.json)')
    ref = doc.get('seed_ref')
    if not isinstance(ref, dict):
        errors.append(f'seed_ref must be {{table: {TABLE}, team, name}}')
        return
    row = index.get(ref.get('team')) if ref.get('table') == TABLE else None
    if row is None:
        errors.append(f"seed_ref {ref.get('table')}/{ref.get('team')} matches no {TABLE} row")
        return
    if ref.get('team') != team:
        errors.append('seed_ref team differs from the profile')
    if ref.get('name') != row['name']:
        errors.append(f"seed_ref name {ref.get('name')!r} differs from {TABLE} ({row['name']!r})")
    if doc.get('name') != row['name']:
        errors.append(f"name {doc.get('name')!r} differs from {TABLE} ({row['name']!r})")


def _check_sliders(doc, evidence, errors):
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
        if not cp.SLIDER_RANGE[0] <= total <= cp.SLIDER_RANGE[1]:
            errors.append(f'slider total {total} is outside {cp.SLIDER_RANGE[0]}-{cp.SLIDER_RANGE[1]}')
        for s in legacy.SLIDERS:
            if sliders[s] != 5 or s in slider_ev:
                cp._check_citations(slider_ev.get(s), f'slider {s}={sliders[s]}', evidence, errors, strong=True)
    for s in slider_ev:
        if s not in legacy.SLIDERS:
            errors.append(f'slider_evidence names unknown slider {s!r}')
    return sliders


def _check_traits(doc, evidence, catalogue, sliders, errors):
    traits = doc.get('traits')
    trait_ev = doc.get('trait_evidence') or {}
    if not isinstance(trait_ev, dict):
        errors.append('trait_evidence must be an object')
        trait_ev = {}
    if not isinstance(traits, list) or len(traits) != len(set(map(str, traits))):
        errors.append('traits must be a list without duplicates')
        return
    if not cp.TRAIT_COUNT[0] <= len(traits) <= cp.TRAIT_COUNT[1]:
        errors.append(f'traits must number {cp.TRAIT_COUNT[0]}-{cp.TRAIT_COUNT[1]}, got {len(traits)}')
    per_category = {}
    for t in traits:
        entry = catalogue.get(t)
        if entry is None:
            errors.append(f'trait {t} is not in the catalogue')
            continue
        if entry.get('roles', {}).get('owner', 0) <= 0:
            errors.append(f'trait {t} is not eligible for owners')
        cat = entry.get('category')
        per_category[cat] = per_category.get(cat, 0) + 1
        if per_category[cat] == 3:
            errors.append(f'more than two traits in category {cat}')
        clash = sorted(set(entry.get('conflicts', [])) & set(traits))
        if clash and t < clash[0]:
            errors.append(f'trait {t} conflicts with {clash[0]}')
        cp._check_citations(trait_ev.get(t), f'trait {t}', evidence, errors, strong=True)
    for t in trait_ev:
        if t not in traits:
            errors.append(f'trait_evidence names {t}, which is not among the traits')
    if not errors and sliders is not None:
        try:   # the core's own rules are the final word on eligibility, conflicts and categories
            core.initialize(core.Identity('owner-profile-check', 'owner', 'check'), 'real', START_SEASON,
                            sliders=dict(sliders), traits=list(traits), catalogue=catalogue)
        except core.PersonalityError as e:
            errors.append(f'core rejects the profile: {e}')


def _check_text(doc, evidence, errors):
    views = doc.get('views')
    if not isinstance(views, list) or not cp.VIEW_COUNT[0] <= len(views) <= cp.VIEW_COUNT[1]:
        errors.append(f'views must number {cp.VIEW_COUNT[0]}-{cp.VIEW_COUNT[1]}')
    for i, v in enumerate(views if isinstance(views, list) else []):
        if not isinstance(v, dict) or not cp._nonempty(v.get('text')):
            errors.append(f'views[{i}] needs text')
            continue
        when = v.get('when')
        if not isinstance(when, list) or not cp.VIEW_TAGS[0] <= len(when) <= cp.VIEW_TAGS[1]:
            errors.append(f'views[{i}] needs {cp.VIEW_TAGS[0]}-{cp.VIEW_TAGS[1]} when tags')
        else:
            bad = sorted(set(map(str, when)) - legacy.ALL_TAGS)
            if bad:
                errors.append(f'views[{i}] has unknown tags {bad}')
    sigs = doc.get('signatures')
    if not isinstance(sigs, list) or not cp.SIGNATURE_COUNT[0] <= len(sigs) <= cp.SIGNATURE_COUNT[1]:
        errors.append(f'signatures must number {cp.SIGNATURE_COUNT[0]}-{cp.SIGNATURE_COUNT[1]}')
    for i, s in enumerate(sigs if isinstance(sigs, list) else []):
        if not (cp._nonempty(s) or (isinstance(s, dict) and cp._nonempty(s.get('text')))):
            errors.append(f'signatures[{i}] needs text')
    for f in ('want', 'fear', 'voice'):
        if not cp._nonempty(doc.get(f)):
            errors.append(f'{f} must be nonempty')
    cp._check_quotes(doc, evidence, errors)


def _check_ties(doc, evidence, coach_index, errors):
    ties = doc.get('ties', [])
    if not isinstance(ties, list):
        errors.append('ties must be a list')
        return
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
            if not isinstance(ref, dict) or (ref.get('table'), ref.get('team'), ref.get('role')) not in coach_index:
                errors.append(f'{label} coach target needs a seed_ref that matches a coach seed row')
        elif not (cp._nonempty(target.get('name')) and cp._nonempty(target.get('team_2026'))):
            errors.append(f'{label} {kind} target needs name and team_2026')
        if tie.get('relationship_kind') not in rev.RELATIONSHIP_KINDS:
            errors.append(f'{label} relationship_kind must be one of {list(rev.RELATIONSHIP_KINDS)}')
        if tie.get('outcome') not in rev.OUTCOMES:
            errors.append(f'{label} outcome must be one of {list(rev.OUTCOMES)}')
        cp._check_citations(tie.get('evidence'), label, evidence, errors, strong=False)


def validate_profile_file(doc, *, catalogue=None, index=None, coach_index=None) -> list[str]:
    """Every owner rule as messages ([] = valid). `_file`, when present, is checked against the team."""
    if not isinstance(doc, dict):
        return ['profile must be a JSON object']
    catalogue = legacy.load_traits() if catalogue is None else catalogue
    index = owner_index() if index is None else index
    coach_index = cp.seed_index() if coach_index is None else coach_index
    errors = []
    if doc.get('schema_version') != SCHEMA_VERSION:
        errors.append(f'schema_version must be {SCHEMA_VERSION}')
    unknown = sorted(k for k in doc if k not in FIELDS and not k.startswith('_'))
    if unknown:
        errors.append(f'unknown fields {unknown}')
    _check_identity(doc, index, errors)
    evidence = cp._check_evidence(doc, errors)
    sliders = _check_sliders(doc, evidence, errors)
    _check_traits(doc, evidence, catalogue, sliders, errors)
    _check_text(doc, evidence, errors)
    _check_ties(doc, evidence, coach_index, errors)
    review = doc.get('review')
    if not isinstance(review, dict) or review.get('status') not in cp.REVIEW_STATUSES:
        errors.append(f'review status must be one of {list(cp.REVIEW_STATUSES)}')
    elif review['status'] in cp.REVIEWED:
        if not cp._nonempty(review.get('reviewer')) or cp._iso(review.get('reviewed_at')) is None:
            errors.append('a reviewed profile names its reviewer and reviewed_at date')
    return errors


# ------------------------------------------------------------------------------------------ conversions ---

core_initial_values = cp.core_initial_values
persona_for = cp.persona_for


def lifecycle_entries(docs) -> list[dict]:
    """The reviewed owner manifest: one entry per approved or edited file, sorted by key."""
    out = []
    for doc in sorted(docs, key=lambda d: d.get('key', '')):
        review = doc.get('review') or {}
        if review.get('status') not in cp.REVIEWED:
            continue
        out.append(dict(
            key=doc['key'], name=doc['name'], role='owner', origin='real',
            birth_key=f"curated-owner:{doc['team']}:{START_SEASON}",
            alias=dict(namespace='db-owner', table='team_owners', seed_ref=dict(doc['seed_ref'])),
            initial_values=core_initial_values(doc),
            review_receipt=dict(reviewer=review['reviewer'], reviewed_at=review['reviewed_at'],
                                file_sha256=content_sha256(doc), schema_version=doc['schema_version'])))
    return out


def tie_events(doc, resolve) -> tuple[list[dict], list[dict]]:
    """(envelopes, unresolved), as coach_profiles.tie_events with the owner as subject
    ({'kind': 'owner', 'seed_ref': ...}), source_kind owner_profile and evidence scope owner_profile."""
    cp._require_reviewed(doc)
    ties = doc.get('ties') or []
    subject = resolve({'kind': 'owner', 'seed_ref': dict(doc['seed_ref'])})
    if subject is None:
        return [], [dict(index=n, target=t['target'], reason='the owner himself is not resolved in this league')
                    for n, t in enumerate(ties, 1)]
    by_id = {ev['id']: ev for ev in doc['evidence']}
    envelopes, unresolved = [], []
    for n, tie in enumerate(ties, 1):
        target = resolve(tie['target'])
        if target is None:
            unresolved.append(dict(index=n, target=tie['target'],
                                   reason='the other person is not in the league as the same person'))
            continue
        evs = [by_id[i] for i in tie['evidence']]
        sid = f"mu:owner-tie:{doc['key']}:{n}"
        envelope = {
            'schema_version': rev.RELATIONSHIP_SCHEMA_VERSION, 'universe_id': subject['universe_id'],
            'event_id': sid, 'source_event_id': sid, 'source_kind': 'owner_profile',
            'source_refs': [{'namespace': 'owner-profile', 'key': doc['key']}],
            'kind': 'meaningful_interaction', 'occurred_at': dict(cp.TIE_TIME), 'subject': dict(subject),
            'target': dict(target), 'actor': None, 'basis': 'reviewed_evidence', 'confidence': cp._lowest(evs),
            'reviewer': doc['review']['reviewer'], 'evidence_scope': ['owner_profile'], 'visibility': 'private',
            'attribution': {'responsible': [], 'reason_code': None, 'evidence_refs': [ev['url'] for ev in evs]},
            'payload': {'relationship_kind': tie['relationship_kind'], 'outcome': tie['outcome'],
                        'significance': 'meaningful', 'opportunity_ref': f"owner-profile:{doc['key']}:tie:{n}",
                        'prehistory': True},
            'supersedes_source_event_id': None,
        }
        try:
            envelopes.append(rev.validate_event(envelope, team_registry={}))
        except rev.RelationshipEventError as e:
            unresolved.append(dict(index=n, target=tie['target'], reason=f'invalid envelope: {e}'))
    return envelopes, unresolved
