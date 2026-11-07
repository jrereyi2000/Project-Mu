"""Coaching trees (docs/done/COACHING_TREES_PLAN.md §2.2-§2.3): reviewed, cited mentor -> protégé edges for
the 2026 staffs, named-tree memberships, the in-sim protégé rule and the lineage label writers use.

Pure apart from reading the `coaching-trees-v1` files in newsroom/config/coaching_trees/:

- trees.json, the named-tree registry ({kind: registry, trees, evidence});
- batch_<A|B|C>.json, researched edges and memberships ({kind: batch, batch, members, edges, proposed_trees,
  evidence}); the protégé's (or member's) 2026 team decides the batch.

Only `approved` and `edited` entries leave this module (reviewed_edges, reviewed_members, registry_trees). People
are keyed by normalized name (`person_key`), the carousel's own rule: one person can hold a `coordinators` row and
a `coaches` row. Simulated edges (`sim_edges`) count simulated seasons only (2026 on), so seeded overlap before 2026
counts only through reviewed edges.
"""
import json
import re
from pathlib import Path

from personality import coach_profiles as cp
from personality import relationship_events as rev

SCHEMA_VERSION = 'coaching-trees-v1'
TREE_DIR = 'newsroom/config/coaching_trees'
SOURCE_CUTOFF = cp.SOURCE_CUTOFF        # '2026-09-08'
BATCHES = {'A': ('BUF', 'MIA', 'NE', 'NYJ', 'BAL', 'CIN', 'CLE', 'PIT', 'HOU', 'IND', 'JAX'),
           'B': ('TEN', 'DEN', 'KC', 'LV', 'LAC', 'DAL', 'NYG', 'PHI', 'WAS', 'CHI', 'DET'),
           'C': ('GB', 'MIN', 'ATL', 'CAR', 'NO', 'TB', 'AZ', 'LAR', 'SF', 'SEA')}
DIRECTIONS = rev.DIRECTIONS
TREE_MIN_SEASONS = 2          # simulated seasons under one head coach (estimate; plan Q2)
FIRST_SEASON = 2026
NAMED_TREE_MIN_HCS = 2        # protégés who became head coaches before a coach gets his own tree (plan Q7)
LINEAGE_DEPTH = 3             # mentor hops walked for a label
TIE_TIME = {'season': 2026, 'tick': 1}
EDGE_OUTCOMES = ('positive', 'mixed')
REVIEW_STATUSES = cp.REVIEW_STATUSES
REVIEWED = cp.REVIEWED
CONFIDENCES = cp.CONFIDENCES
TREE_KEY = re.compile(r'^[a-z][a-z0-9_]*$')
REGISTRY_FILE = 'trees.json'


# ------------------------------------------------------------------------------------------------ names ---

def person_key(name) -> str:
    """'p:' + lowercase name, punctuation dropped, single spaces ('A.J. Brown' -> 'p:aj brown')."""
    text = re.sub(r"[^\w\s]", '', str(name or '').lower())
    return 'p:' + ' '.join(text.split())


def profile_key(ref) -> str:
    return f"coach:{ref.get('team')}:{ref.get('role')}"


def batch_of(team):
    for b, teams in BATCHES.items():
        if team in teams:
            return b
    return None


def _surname(name):
    parts = str(name or '').split()
    while len(parts) > 1 and parts[-1].rstrip('.').lower() in ('jr', 'sr', 'ii', 'iii', 'iv'):
        parts = parts[:-1]
    return parts[-1] if parts else ''


# ------------------------------------------------------------------------------------------------ files ---

def _dir(directory):
    return cp._resolve_dir(directory)


def _load(path):
    doc = json.loads(Path(path).read_text())
    if isinstance(doc, dict):
        doc['_file'] = Path(path).name
    return doc


def load_registry(directory=TREE_DIR) -> dict:
    """trees.json, or an empty registry when the file doesn't exist yet."""
    path = _dir(directory) / REGISTRY_FILE
    if not path.exists():
        return {'schema_version': SCHEMA_VERSION, 'kind': 'registry', 'trees': [], 'evidence': []}
    return _load(path)


def load_batches(directory=TREE_DIR, *, batches=None) -> list[dict]:
    """batch_<X>.json files present in the directory (optionally only these batch letters), in batch order."""
    out = []
    for b in sorted(BATCHES):
        if batches is not None and b not in batches:
            continue
        path = _dir(directory) / f'batch_{b}.json'
        if path.exists():
            out.append(_load(path))
    return out


def file_doc(doc) -> dict:
    return {k: v for k, v in doc.items() if not k.startswith('_')}


# ------------------------------------------------------------------------------------------- validation ---

def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _check_review(review, label, errors):
    if not isinstance(review, dict) or review.get('status') not in REVIEW_STATUSES:
        errors.append(f'{label} review status must be one of {list(REVIEW_STATUSES)}')
    elif review['status'] in REVIEWED:
        if not _nonempty(review.get('reviewer')) or cp._iso(review.get('reviewed_at')) is None:
            errors.append(f'{label} is reviewed but does not name its reviewer and reviewed_at date')


def _check_header(doc, kind, errors):
    if not isinstance(doc, dict):
        errors.append('the file must be a JSON object')
        return False
    if doc.get('schema_version') != SCHEMA_VERSION:
        errors.append(f'schema_version must be {SCHEMA_VERSION}')
    if doc.get('kind') != kind:
        errors.append(f'kind must be {kind!r}')
    return True


def _cited(ids, label, evidence, errors, *, minimum=1, strong=False):
    """Check citations; returns the cited evidence dicts that exist."""
    if not isinstance(ids, list) or not ids:
        errors.append(f'{label} cites no evidence')
        return []
    found = []
    for eid in ids:
        ev = evidence.get(eid)
        if ev is None:
            errors.append(f'{label} cites unknown evidence {eid!r}')
        else:
            found.append(ev)
    if len(set(ids)) < minimum:
        errors.append(f'{label} needs at least {minimum} sources (has {len(set(ids))})')
    if strong and found and not any(ev.get('confidence') in ('medium', 'high') for ev in found):
        errors.append(f'{label} needs at least one medium or high confidence source')
    return found


def _check_quotes(texts, evidence, errors):
    n = cp.MAX_QUOTE_WORDS + 1
    grams = {}
    for eid, ev in evidence.items():
        for g in cp._ngrams(cp._words(ev.get('summary')), n):
            grams.setdefault(g, eid)
    for label, text in texts:
        for g in cp._ngrams(cp._words(text), n):
            if g in grams:
                errors.append(f'{label} copies more than {cp.MAX_QUOTE_WORDS} consecutive words from {grams[g]}')
                break


def _check_tree(tree, label, evidence, errors):
    """One registry or proposed tree: key, label, root, parent (checked by the caller), summary, evidence."""
    if not isinstance(tree, dict):
        errors.append(f'{label} must be an object')
        return
    if not (isinstance(tree.get('key'), str) and TREE_KEY.match(tree['key'])):
        errors.append(f"{label} key must match {TREE_KEY.pattern}")
    for f in ('label', 'root', 'summary'):
        if not _nonempty(tree.get(f)):
            errors.append(f'{label} needs a {f}')
    if 'parent' not in tree:
        errors.append(f'{label} needs a parent (another tree key or null)')
    _cited(tree.get('evidence'), label, evidence, errors, minimum=2, strong=True)
    _check_review(tree.get('review'), label, errors)


def validate_registry(doc) -> list[str]:
    errors = []
    if not _check_header(doc, 'registry', errors):
        return errors
    evidence = cp._check_evidence(doc, errors)
    trees = doc.get('trees')
    if not isinstance(trees, list) or not trees:
        errors.append('trees must be a nonempty list')
        return errors
    keys = {}
    for i, t in enumerate(trees):
        label = f"tree {t.get('key')}" if isinstance(t, dict) and t.get('key') else f'trees[{i}]'
        _check_tree(t, label, evidence, errors)
        if isinstance(t, dict) and isinstance(t.get('key'), str):
            if t['key'] in keys:
                errors.append(f"duplicate tree key {t['key']}")
            keys[t['key']] = t
    for key, t in keys.items():
        parent = t.get('parent')
        if parent is not None and parent not in keys:
            errors.append(f'tree {key} parent {parent!r} is not a tree in the registry')
    for key in keys:
        seen, cur = set(), key
        while cur is not None and cur in keys:
            if cur in seen:
                errors.append(f'tree {key} has a parent cycle')
                break
            seen.add(cur)
            cur = keys[cur].get('parent')
    _check_quotes([(f"tree {t.get('key')} summary", t.get('summary')) for t in keys.values()], evidence, errors)
    return errors


def _check_person(ref, label, index, errors):
    """A seed_ref {table, team, role} without a name; returns the seed row or None."""
    if not isinstance(ref, dict) or set(ref) != {'table', 'team', 'role'}:
        errors.append(f'{label} must be a seed ref {{table, team, role}}')
        return None
    row = index.get((ref.get('table'), ref.get('team'), ref.get('role')))
    if row is None:
        errors.append(f"{label} {ref.get('table')}/{ref.get('team')}/{ref.get('role')} matches no seed row")
    return row


def _stint_seasons(stints):
    total = 0
    for s in stints or []:
        if isinstance(s, dict) and isinstance(s.get('first_season'), int) and isinstance(s.get('last_season'), int):
            total += max(0, s['last_season'] - s['first_season'] + 1)
    return total


def validate_batch(doc, *, registry, index=None) -> list[str]:
    """One batch file against the registry (a registry doc) and the seed (`coach_profiles.seed_index`)."""
    errors = []
    if not _check_header(doc, 'batch', errors):
        return errors
    index = cp.seed_index() if index is None else index
    batch = doc.get('batch')
    if batch not in BATCHES:
        errors.append(f'batch must be one of {sorted(BATCHES)}')
    teams = BATCHES.get(batch, ())
    evidence = cp._check_evidence(doc, errors) if doc.get('evidence') else {}
    if not doc.get('evidence') and (doc.get('edges') or doc.get('members') or doc.get('proposed_trees')):
        errors.append('evidence must be a nonempty list')
    for f in ('members', 'edges', 'proposed_trees'):
        if not isinstance(doc.get(f), list):
            errors.append(f'{f} must be a list')
    registry_keys = {t.get('key') for t in (registry or {}).get('trees') or [] if isinstance(t, dict)}
    proposed = {}
    for i, t in enumerate(doc.get('proposed_trees') or []):
        label = f"proposed tree {t.get('key')}" if isinstance(t, dict) and t.get('key') else f'proposed_trees[{i}]'
        _check_tree(t, label, evidence, errors)
        if isinstance(t, dict) and isinstance(t.get('key'), str):
            if t['key'] in registry_keys or t['key'] in proposed:
                errors.append(f"{label} key is already a tree")
            proposed[t['key']] = t
    known = registry_keys | set(proposed)
    for key, t in proposed.items():
        if t.get('parent') is not None and t['parent'] not in known:
            errors.append(f'proposed tree {key} parent {t["parent"]!r} is not a known tree')
    texts = [(f'proposed tree {k} summary', t.get('summary')) for k, t in proposed.items()]

    seen_members = set()
    for i, m in enumerate(doc.get('members') or []):
        if not isinstance(m, dict):
            errors.append(f'members[{i}] must be an object')
            continue
        ref = m.get('person')
        label = f'member {profile_key(ref)}' if isinstance(ref, dict) else f'members[{i}]'
        if _check_person(ref, label, index, errors) is not None:
            if ref.get('team') not in teams:
                errors.append(f"{label} team {ref.get('team')} is not in batch {batch}")
            if profile_key(ref) in seen_members:
                errors.append(f'duplicate {label}')
            seen_members.add(profile_key(ref))
        trees = m.get('trees')
        if not isinstance(trees, list) or not trees:
            errors.append(f'{label} names no trees')
        else:
            for k in trees:
                if k not in known:
                    errors.append(f'{label} tree {k!r} is not in the registry or proposed_trees')
        _cited(m.get('evidence'), label, evidence, errors)
        if 'notes' in m:
            texts.append((f'{label} notes', m.get('notes')))
        _check_review(m.get('review'), label, errors)

    seen_edges = set()
    for i, e in enumerate(doc.get('edges') or []):
        if not isinstance(e, dict):
            errors.append(f'edges[{i}] must be an object')
            continue
        label = f"edge {e.get('key')}" if _nonempty(e.get('key')) else f'edges[{i}]'
        m_row = _check_person(e.get('mentor'), f'{label} mentor', index, errors)
        p_row = _check_person(e.get('protege'), f'{label} protege', index, errors)
        if m_row is not None and p_row is not None:
            expected = f"{profile_key(e['mentor'])}>{profile_key(e['protege'])}"
            if e.get('key') != expected:
                errors.append(f'{label} key must be {expected}')
            if profile_key(e['mentor']) == profile_key(e['protege']) or \
                    person_key(m_row['name']) == person_key(p_row['name']):
                errors.append(f'{label} mentor and protege are the same person')
            if e['protege'].get('team') not in teams:
                errors.append(f"{label} protege team {e['protege'].get('team')} is not in batch {batch}")
        if e.get('key') in seen_edges:
            errors.append(f'duplicate {label}')
        seen_edges.add(e.get('key'))
        stints = e.get('stints')
        if not isinstance(stints, list) or not stints:
            errors.append(f'{label} needs at least one stint')
        else:
            for j, s in enumerate(stints):
                sl = f'{label} stints[{j}]'
                if not isinstance(s, dict):
                    errors.append(f'{sl} must be an object')
                    continue
                for f in ('team', 'mentor_role', 'protege_role'):
                    if not _nonempty(s.get(f)):
                        errors.append(f'{sl} needs {f}')
                fs, ls = s.get('first_season'), s.get('last_season')
                if not (isinstance(fs, int) and isinstance(ls, int)) or isinstance(fs, bool) or ls < fs:
                    errors.append(f'{sl} needs integer first_season <= last_season')
                elif ls > FIRST_SEASON:
                    errors.append(f'{sl} last_season {ls} is after the {FIRST_SEASON} start')
        cited = _cited(e.get('evidence'), label, evidence, errors, minimum=2, strong=True)
        if _stint_seasons(stints) < 2 and not any(ev.get('mentorship') is True for ev in cited):
            errors.append(f'{label} has fewer than 2 seasons and no cited source marked "mentorship": true')
        if e.get('outcome') not in EDGE_OUTCOMES:
            errors.append(f'{label} outcome must be one of {list(EDGE_OUTCOMES)}')
        if 'notes' in e:
            texts.append((f'{label} notes', e.get('notes')))
        _check_review(e.get('review'), label, errors)
    _check_quotes([(lab, t) for lab, t in texts if isinstance(t, str)], evidence, errors)
    return errors


def validate_batches(docs, *, registry, index=None) -> dict:
    """{batch letter or file: [errors]}, including edge keys duplicated across batches."""
    index = cp.seed_index() if index is None else index
    out, owner = {}, {}
    for doc in docs:
        name = doc.get('batch') if isinstance(doc, dict) and doc.get('batch') else doc.get('_file', '?')
        errs = validate_batch(doc, registry=registry, index=index)
        for e in (doc.get('edges') or []) if isinstance(doc, dict) else []:
            key = e.get('key') if isinstance(e, dict) else None
            if key in owner and owner[key] != name:
                errs.append(f'edge {key} is also in batch {owner[key]}')
            elif key:
                owner[key] = name
        out[name] = errs
    return out


# ---------------------------------------------------------------------------------------- reviewed data ---

def _reviewed(entry):
    return isinstance(entry, dict) and (entry.get('review') or {}).get('status') in REVIEWED


def _lowest(evs):
    return min((ev['confidence'] for ev in evs), key=CONFIDENCES.index)


def registry_trees(registry) -> dict:
    """{key: {key, label, root, parent}} for approved/edited registry trees; a parent that isn't reviewed reads None."""
    trees = {t['key']: {'key': t['key'], 'label': t['label'], 'root': t['root'], 'parent': t.get('parent')}
             for t in (registry or {}).get('trees') or [] if _reviewed(t)}
    for t in trees.values():
        if t['parent'] not in trees:
            t['parent'] = None
    return trees


def reviewed_edges(batches, *, index=None) -> list[dict]:
    """Approved/edited batch edges as Edge dicts (basis 'reviewed'), sorted by key."""
    index = cp.seed_index() if index is None else index
    out = []
    for doc in batches:
        by_id = {ev['id']: ev for ev in doc.get('evidence') or [] if isinstance(ev, dict) and 'id' in ev}
        for e in doc.get('edges') or []:
            if not _reviewed(e):
                continue
            m = index[(e['mentor']['table'], e['mentor']['team'], e['mentor']['role'])]
            p = index[(e['protege']['table'], e['protege']['team'], e['protege']['role'])]
            evs = [by_id[i] for i in e['evidence']]
            stints = e['stints']
            out.append({
                'key': e['key'], 'mentor_pk': person_key(m['name']), 'mentor_name': m['name'],
                'protege_pk': person_key(p['name']), 'protege_name': p['name'],
                'seasons': _stint_seasons(stints),
                'span': [min(s['first_season'] for s in stints), max(s['last_season'] for s in stints)],
                'teams': sorted({s['team'] for s in stints}), 'outcome': e['outcome'], 'basis': 'reviewed',
                'evidence_urls': [ev['url'] for ev in evs], 'confidence': _lowest(evs),
                'reviewer': e['review']['reviewer'],
                'mentor_seed': dict(e['mentor']), 'protege_seed': dict(e['protege'])})
    return sorted(out, key=lambda x: x['key'])


def reviewed_members(batches, *, index=None) -> dict:
    """{person_key: [tree keys]} from approved/edited memberships."""
    index = cp.seed_index() if index is None else index
    out = {}
    for doc in batches:
        for m in doc.get('members') or []:
            if not _reviewed(m):
                continue
            row = index[(m['person']['table'], m['person']['team'], m['person']['role'])]
            keys = out.setdefault(person_key(row['name']), [])
            keys.extend(k for k in m['trees'] if k not in keys)
    return out


def reviewed_pairs(edges) -> frozenset:
    return frozenset((e['mentor_pk'], e['protege_pk']) for e in edges)


def edge_events(edge, resolve) -> tuple[list[dict], list[dict]]:
    """(envelopes, unresolved) for one reviewed edge: the protégé's facet (direction 'mentee', target the mentor)
    and the mentor's (direction 'mentor', target the protégé), sharing source_event_id mu:coach-tree:<key>.
    resolve(seed_ref) -> PersonRef | None."""
    mentor, protege = resolve(dict(edge['mentor_seed'])), resolve(dict(edge['protege_seed']))
    missing = [who for who, ref in (('mentor', mentor), ('protege', protege)) if ref is None]
    if missing:
        return [], [dict(key=edge['key'], reason=f"the {' and '.join(missing)} is not resolved in this league")]
    sid = f"mu:coach-tree:{edge['key']}"
    envelopes, unresolved = [], []
    for direction, subject, target in (('mentee', protege, mentor), ('mentor', mentor, protege)):
        envelope = {
            'schema_version': rev.RELATIONSHIP_SCHEMA_VERSION, 'universe_id': subject['universe_id'],
            'event_id': f'{sid}|{direction}', 'source_event_id': sid, 'source_kind': 'coaching_tree',
            'source_refs': [{'namespace': 'coaching-tree', 'key': edge['key']}],
            'kind': 'meaningful_interaction', 'occurred_at': dict(TIE_TIME), 'subject': dict(subject),
            'target': dict(target), 'actor': None, 'basis': 'reviewed_evidence', 'confidence': edge['confidence'],
            'reviewer': edge['reviewer'], 'evidence_scope': ['coaching_tree'], 'visibility': 'public',
            'attribution': {'responsible': [], 'reason_code': None, 'evidence_refs': list(edge['evidence_urls'])},
            'payload': {'relationship_kind': 'mentorship', 'outcome': edge['outcome'], 'significance': 'meaningful',
                        'opportunity_ref': f"coaching-tree:{edge['key']}", 'prehistory': True,
                        'direction': direction, 'seasons': edge['seasons']},
            'supersedes_source_event_id': None,
        }
        try:
            envelopes.append(rev.validate_event(envelope, team_registry={}))
        except rev.RelationshipEventError as e:
            unresolved.append(dict(key=edge['key'], reason=f'invalid envelope: {e}'))
    return envelopes, unresolved


# ------------------------------------------------------------------------------------------ simulation ---

def sim_edges(staff_seasons, *, through, reviewed_pairs=frozenset(), min_seasons=TREE_MIN_SEASONS,
              first_season=FIRST_SEASON) -> list[dict]:
    """Simulated mentor -> protégé edges: a coordinator with `min_seasons` seasons (first_season..through, summed
    across teams) as OC or DC under one head coach. Rows: {season, team_id, role, namespace, external_id, name,
    fired} (optional `team` abbreviation). Sorted by (crossed_season, mentor_pk, protege_pk)."""
    by_team = {}
    for r in staff_seasons:
        if first_season <= r['season'] <= through:
            by_team.setdefault((r['season'], r['team_id']), []).append(r)
    hc_at = {}            # (season, team_id) -> set of HC person keys
    for (season, team), rows in by_team.items():
        hc_at[(season, team)] = {person_key(r['name']) for r in rows if r['role'] == 'HC'}
    pairs = {}
    for (season, team), rows in sorted(by_team.items(), key=lambda kv: (kv[0][0], str(kv[0][1]))):
        hcs = [r for r in rows if r['role'] == 'HC']
        coords = [r for r in rows if r['role'] in ('OC', 'DC')]
        for h in hcs:
            for c in coords:
                mpk, ppk = person_key(h['name']), person_key(c['name'])
                if mpk == ppk or (mpk, ppk) in reviewed_pairs:
                    continue
                p = pairs.setdefault((mpk, ppk), {'seasons': {}, 'mentor': h, 'protege': c})
                if season not in p['seasons']:
                    p['seasons'][season] = (team, c)
                p['mentor'], p['protege'] = h, c
    out = []
    for (mpk, ppk), p in pairs.items():
        seasons = sorted(p['seasons'])
        if len(seasons) < min_seasons:
            continue
        last = seasons[-1]
        last_team, last_row = p['seasons'][last]
        mixed = bool(last_row.get('fired'))
        if mixed and (last + 1, last_team) in hc_at and mpk not in hc_at[(last + 1, last_team)]:
            mixed = False            # the mentor left too: not a falling-out
        teams = []
        for s in seasons:
            team, row = p['seasons'][s]
            label = row.get('team') or team
            if label not in teams:
                teams.append(label)
        m, c = p['mentor'], p['protege']
        out.append({'key': f'sim:{mpk}>{ppk}', 'mentor_pk': mpk, 'mentor_name': m['name'], 'protege_pk': ppk,
                    'protege_name': c['name'], 'seasons': len(seasons), 'span': [seasons[0], last],
                    'teams': teams, 'outcome': 'mixed' if mixed else 'positive', 'basis': 'simulated',
                    'mentor_ref': {'namespace': m.get('namespace', 'db-coach'), 'external_id': m['external_id']},
                    'protege_ref': {'namespace': c.get('namespace', 'db-coordinator'),
                                    'external_id': c['external_id']},
                    'crossed_season': seasons[min_seasons - 1]})
    return sorted(out, key=lambda e: (e['crossed_season'], e['mentor_pk'], e['protege_pk']))


# --------------------------------------------------------------------------------------------- lineage ---

def _tree_view(t):
    return {'key': t['key'], 'label': t['label'], 'root': t['root']}


def _registry_depth(key, trees):
    depth, seen = 0, set()
    while key in trees and trees[key].get('parent') and key not in seen:
        seen.add(key)
        key = trees[key]['parent']
        depth += 1
    return depth


def _people(edges, pk, side):
    """Direct mentors (side 'mentor') or protégés of pk, most seasons first."""
    rows = {}
    for e in edges:
        if side == 'mentor' and e['protege_pk'] == pk:
            other, name = e['mentor_pk'], e['mentor_name']
        elif side == 'protege' and e['mentor_pk'] == pk:
            other, name = e['protege_pk'], e['protege_name']
        else:
            continue
        cur = rows.get(other)
        if cur is None or e['seasons'] > cur['seasons']:
            rows[other] = {'pk': other, 'name': name, 'seasons': e['seasons'], 'span': list(e['span']),
                           'basis': e['basis']}
    return sorted(rows.values(), key=lambda r: (-r['seasons'], r['name']))


def lineage(pk, *, edges, registry, members, head_coach_pks=frozenset()) -> dict:
    """{trees: [{key, label, root}], label, mentors, proteges, root_of}. `registry` is a registry doc or the
    `registry_trees` dict; `members` is `reviewed_members`; `edges` mixes reviewed and simulated edges."""
    trees = registry if isinstance(registry, dict) and 'trees' not in registry else registry_trees(registry)
    root_of = {person_key(t['root']): [] for t in trees.values()}
    for t in sorted(trees.values(), key=lambda t: t['key']):
        root_of[person_key(t['root'])].append(t)
    hc = set(head_coach_pks)

    def computed(m, name):
        n = sum(1 for p in _people(edges, m, 'protege') if p['pk'] in hc)
        if n >= NAMED_TREE_MIN_HCS:
            return {'key': f'sim:{m}', 'label': f'{_surname(name)} tree', 'root': name, 'parent': None}
        return None

    found = []

    def add(t):
        if t['key'] not in [x['key'] for x in found]:
            found.append(t)

    for k in members.get(pk, []):
        if k in trees:
            add(trees[k])
    frontier, seen = [pk], {pk}
    walked = []
    for _ in range(LINEAGE_DEPTH):
        hop = []
        for who in frontier:
            for m in _people(edges, who, 'mentor'):
                if m['pk'] not in seen:
                    seen.add(m['pk'])
                    hop.append(m)
        if not hop:
            break
        gained = []
        for m in hop:
            if root_of.get(m['pk']):
                gained.extend(root_of[m['pk']])
            gained.extend(trees[k] for k in members.get(m['pk'], []) if k in trees)
            if not root_of.get(m['pk']):
                c = computed(m['pk'], m['name'])
                if c is not None:
                    gained.append(c)
        walked.append(hop)
        if gained:
            for t in gained:
                add(t)
            break
        frontier = [m['pk'] for m in hop]
    order = {t['key']: i for i, t in enumerate(found)}
    found.sort(key=lambda t: (-_registry_depth(t['key'], trees), order[t['key']]))
    chain = list(found)
    for t in found:
        cur = t.get('parent')
        while cur in trees and cur not in [x['key'] for x in chain]:
            chain.append(trees[cur])
            cur = trees[cur].get('parent')
    label = None
    if chain:
        first = chain[0]
        label = first['label']
        parent = trees.get(first.get('parent')) if first.get('parent') else None
        if parent is not None:
            base = re.sub(r'\s+tree$', '', parent['label'], flags=re.I)
            label = f'{label} ({base} lineage)'
    own = list(root_of.get(pk, []))
    my_name = next((e['mentor_name'] for e in edges if e['mentor_pk'] == pk), None)
    if not own and my_name:
        c = computed(pk, my_name)
        if c is not None:
            own = [c]
    strip = lambda rows: [{k: r[k] for k in ('name', 'seasons', 'span', 'basis')} for r in rows]
    return {'trees': [_tree_view(t) for t in chain], 'label': label,
            'mentors': strip(_people(edges, pk, 'mentor')), 'proteges': strip(_people(edges, pk, 'protege')),
            'root_of': [_tree_view(t) for t in own]}


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def lineage_line(lin) -> str | None:
    """One sentence for prompts, e.g. 'Coaching tree: McVay tree (Shanahan lineage); served 5 seasons under Sean
    McVay.' None when there is nothing to say."""
    if not lin:
        return None
    parts = []
    if lin.get('label'):
        parts.append(f"Coaching tree: {lin['label']}")
    if lin.get('root_of'):
        parts.append('root of the ' + ' and '.join(t['label'] for t in lin['root_of']))
    if lin.get('mentors'):
        m = lin['mentors'][0]
        parts.append(f"served {_plural(m['seasons'], 'season')} under {m['name']}")
    if lin.get('proteges'):
        names = [p['name'] for p in lin['proteges'][:3]]
        more = len(lin['proteges']) - len(names)
        parts.append('mentor to ' + ', '.join(names) + (f' and {more} more' if more > 0 else ''))
    if not parts:
        return None
    text = '; '.join(parts)
    return text[0].upper() + text[1:] + '.'
