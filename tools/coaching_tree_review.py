"""Review tool for coaching trees (docs/done/COACHING_TREES_PLAN.md §2.5). Offline: no database, network
or model calls.

  python3 -m tools.coaching_tree_review validate [--dir DIR] [--batch A|B|C|registry]
  python3 -m tools.coaching_tree_review report --batch A --out verify_output/coaching_tree_review/batch_A.md [--dir DIR]
  python3 -m tools.coaching_tree_review apply-review --decisions PATH [--dir DIR] [--reviewer JR] [--today YYYY-MM-DD]
  python3 -m tools.coaching_tree_review events [--dir DIR] --out PATH

validate prints each file's errors (exit 0 when all pass, 1 otherwise). report writes one Markdown page per batch
(or the registry, --batch registry): each edge as "mentor -> protégé, N seasons (teams, years)", outcome, sources
by ID with link and confidence and a decision line; then memberships and proposed trees; then a Sources table.
apply-review takes {"<edge key>" | "member:<profile key>" | "tree:<key>": {"status": "approved|edited|rejected",
"notes": "...", "edits": {...}}} and writes nothing unless every decision validates (exit 0; else 1 with reasons).
events writes the reviewed edges as relationships-v1 envelopes with placeholder PersonRefs (universe 'preview',
persona_id = profile key), a preview only: nothing is appended to any ledger (plan Q3).
"""
import argparse
from datetime import date
import json
from pathlib import Path
import sys

from personality import coach_profiles as cp
from personality import coaching_trees as ct

EDGE_EDITABLE = ('stints', 'outcome', 'evidence', 'notes')
MEMBER_EDITABLE = ('trees', 'evidence', 'notes')
TREE_EDITABLE = ('label', 'root', 'parent', 'summary', 'evidence')


def _dir(args):
    return args.dir or ct.TREE_DIR


def _write(path, doc):
    Path(path).write_text(json.dumps(ct.file_doc(doc), indent=2, ensure_ascii=False) + '\n')


# ----------------------------------------------------------------------------------------------- validate ---

def validate_all(directory, batch=None, index=None):
    """{name: [errors]} for the registry and/or batches."""
    index = cp.seed_index() if index is None else index
    registry = ct.load_registry(directory)
    out = {}
    if batch in (None, 'registry'):
        if not (cp._resolve_dir(directory) / ct.REGISTRY_FILE).exists():
            out['registry'] = [f'{ct.REGISTRY_FILE} does not exist']
        else:
            out['registry'] = ct.validate_registry(registry)
    if batch != 'registry':
        docs = ct.load_batches(directory)
        found = ct.validate_batches(docs, registry=registry, index=index)
        if batch is not None:
            if batch not in found:
                found = {batch: [f'batch_{batch}.json does not exist']}
            found = {batch: found[batch]}
        out.update(found)
    return out


def cmd_validate(args):
    results = validate_all(_dir(args), args.batch)
    bad = 0
    for name, errors in results.items():
        if errors:
            bad += 1
            print(f'{name}: {len(errors)} error(s)')
            for e in errors:
                print(f'  - {e}')
        else:
            print(f'{name}: ok')
    return 1 if bad else 0


# ------------------------------------------------------------------------------------------------- report ---

def _md(text):
    return str(text or '').replace('|', '\\|').replace('\n', ' ')


def _cite(ids, by_id):
    out = []
    for i in ids or []:
        ev = by_id.get(i)
        out.append(f"[{i}]({ev['url']}) ({ev.get('confidence')})" if ev else f'{i} (unknown)')
    return ', '.join(out) or 'none'


def _who(ref, index):
    row = index.get((ref.get('table'), ref.get('team'), ref.get('role'))) if isinstance(ref, dict) else None
    if row is None:
        return f'unknown {ref}'
    return f"{row['name']} ({ref['team']} {ref['role']})"


def _stint_text(s):
    years = str(s['first_season']) if s['first_season'] == s['last_season'] else f"{s['first_season']}–{s['last_season']}"
    return f"{s['team']} {years}, {s['mentor_role']} over {s['protege_role']}"


def _status(entry):
    review = entry.get('review') or {}
    text = review.get('status', '?')
    if review.get('reviewer'):
        text += f" by {review['reviewer']} {review.get('reviewed_at')}"
    return text


def _sources_table(evidence):
    lines = ['## Sources', '', '| ID | Publisher | Published | Confidence | Summary |', '|---|---|---|---|---|']
    for ev in evidence:
        mark = ' (mentorship)' if ev.get('mentorship') is True else ''
        lines.append(f"| [{_md(ev.get('id'))}]({ev.get('url')}) | {_md(ev.get('publisher'))} | {ev.get('published')} "
                     f"| {ev.get('confidence')}{mark} | {_md(ev.get('summary'))} |")
    return lines


def _tree_lines(t, by_id, prefix):
    return [f"### {prefix}{t.get('label')} (`{t.get('key')}`)", '',
            f"- Root: {t.get('root')}; parent: {t.get('parent') or 'none'}",
            f"- {t.get('summary')}",
            f"- Sources: {_cite(t.get('evidence'), by_id)}",
            f"- Status: {_status(t)}",
            f"- Decision (key `tree:{t.get('key')}`): approve / edit / reject", '']


def build_report(doc, *, registry=None, index=None, errors=()):
    index = cp.seed_index() if index is None else index
    by_id = {ev['id']: ev for ev in doc.get('evidence') or [] if isinstance(ev, dict) and 'id' in ev}
    if doc.get('kind') == 'registry':
        lines = ['# Coaching trees: named-tree registry', '',
                 f"{len(doc.get('trees') or [])} trees. Decide each with its `tree:<key>`.", '']
        if errors:
            lines += ['**Validation errors:**', ''] + [f'- {e}' for e in errors] + ['']
        for t in doc.get('trees') or []:
            lines += _tree_lines(t, by_id, '')
        return '\n'.join(lines + _sources_table(doc.get('evidence') or [])) + '\n'
    b = doc.get('batch')
    edges, members = doc.get('edges') or [], doc.get('members') or []
    lines = [f"# Coaching trees: batch {b} ({' '.join(ct.BATCHES.get(b, ()))})", '',
             f'{len(edges)} edges, {len(members)} memberships, {len(doc.get("proposed_trees") or [])} proposed trees. '
             'Each entry has a decision line; decide edges by their key, memberships by `member:<profile key>`.', '']
    if errors:
        lines += ['**Validation errors:**', ''] + [f'- {e}' for e in errors] + ['']
    lines += ['## Edges (mentor → protégé)', '']
    for e in edges:
        seasons = ct._stint_seasons(e.get('stints'))
        stints = '; '.join(_stint_text(s) for s in e.get('stints') or [] if isinstance(s, dict))
        lines += [f"### {_who(e.get('mentor'), index)} → {_who(e.get('protege'), index)}, {seasons} seasons", '',
                  f'- Stints: {stints}', f"- Outcome: {e.get('outcome')}",
                  f"- Sources: {_cite(e.get('evidence'), by_id)}"]
        if e.get('notes'):
            lines.append(f"- Notes: {e['notes']}")
        lines += [f'- Status: {_status(e)}', f"- Decision (key `{e.get('key')}`): approve / edit / reject", '']
    lines += ['## Memberships', '']
    for m in members:
        key = ct.profile_key(m.get('person') or {})
        lines += [f"- **{_who(m.get('person'), index)}**: {', '.join(m.get('trees') or [])}. "
                  f"Sources: {_cite(m.get('evidence'), by_id)}. Status: {_status(m)}. "
                  f"Decision (key `member:{key}`): approve / edit / reject"
                  + (f". Notes: {m['notes']}" if m.get('notes') else '')]
    lines.append('')
    if doc.get('proposed_trees'):
        lines += ['## Proposed trees', '']
        for t in doc['proposed_trees']:
            lines += _tree_lines(t, by_id, 'Proposed: ')
    return '\n'.join(lines + _sources_table(doc.get('evidence') or [])) + '\n'


def cmd_report(args):
    directory = _dir(args)
    registry = ct.load_registry(directory)
    if args.batch == 'registry':
        doc, errors = registry, ct.validate_registry(registry)
    else:
        docs = ct.load_batches(directory, batches=[args.batch])
        if not docs:
            print(f'batch_{args.batch}.json does not exist')
            return 1
        doc = docs[0]
        errors = ct.validate_batch(doc, registry=registry)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build_report(doc, registry=registry, errors=errors))
    print(f'wrote {out} ({len(errors)} validation error(s))')
    return 0


# ------------------------------------------------------------------------------------------- apply-review ---

def _locate(key, registry, batches):
    """(doc, list name, index) for a decision key, or None."""
    if key.startswith('tree:'):
        tk = key[5:]
        for i, t in enumerate(registry.get('trees') or []):
            if t.get('key') == tk:
                return registry, 'trees', i
        for doc in batches:
            for i, t in enumerate(doc.get('proposed_trees') or []):
                if t.get('key') == tk:
                    return doc, 'proposed_trees', i
        return None
    if key.startswith('member:'):
        pk = key[7:]
        for doc in batches:
            for i, m in enumerate(doc.get('members') or []):
                if ct.profile_key(m.get('person') or {}) == pk:
                    return doc, 'members', i
        return None
    for doc in batches:
        for i, e in enumerate(doc.get('edges') or []):
            if e.get('key') == key:
                return doc, 'edges', i
    return None


def apply_decisions(decisions, directory, *, reviewer='JR', today=None, index=None):
    """([(path, new_doc)], errors). Nothing is written here."""
    today = today or date.today().isoformat()
    index = cp.seed_index() if index is None else index
    if not isinstance(decisions, dict):
        return [], ['decisions must be an object keyed by edge key, member:<profile key> or tree:<key>']
    registry = ct.load_registry(directory)
    batches = ct.load_batches(directory)
    errors, touched = [], {}
    for key, dec in sorted(decisions.items()):
        where = _locate(key, registry, batches)
        if where is None:
            errors.append(f'{key}: no such entry')
            continue
        doc, field, i = where
        if not isinstance(dec, dict) or dec.get('status') not in ('approved', 'edited', 'rejected'):
            errors.append(f'{key}: status must be approved, edited or rejected')
            continue
        edits = dec.get('edits') or {}
        if edits and dec['status'] != 'edited':
            errors.append(f'{key}: edits need status edited')
            continue
        if dec['status'] == 'edited' and not edits:
            errors.append(f'{key}: status edited needs edits')
            continue
        allowed = {'edges': EDGE_EDITABLE, 'members': MEMBER_EDITABLE}.get(field, TREE_EDITABLE)
        bad = sorted(set(edits) - set(allowed))
        if bad:
            errors.append(f'{key}: fields {bad} cannot be edited on review')
            continue
        entry = doc[field][i]
        entry.update(json.loads(json.dumps(edits)))
        entry['review'] = dict(status=dec['status'], reviewer=reviewer, reviewed_at=today, notes=dec.get('notes') or '')
        touched[id(doc)] = doc
    if errors:
        return [], errors
    problems = ct.validate_registry(registry) if id(registry) in touched else []
    errors += [f'registry: refused, the result fails validation: {p}' for p in problems]
    results = ct.validate_batches(batches, registry=registry, index=index)
    for doc in batches:
        if id(doc) in touched or id(registry) in touched:
            name = doc.get('batch')
            errors += [f'batch {name}: refused, the result fails validation: {p}' for p in results.get(name, [])]
    if errors:
        return [], errors
    root = cp._resolve_dir(directory)
    return [(root / doc['_file'], ct.file_doc(doc)) for doc in touched.values()], []


def cmd_apply_review(args):
    decisions = json.loads(Path(args.decisions).read_text())
    planned, errors = apply_decisions(decisions, _dir(args), reviewer=args.reviewer, today=args.today)
    for e in errors:
        print(e)
    if errors:
        print('nothing written')
        return 1
    for path, doc in planned:
        _write(path, doc)
        print(f'wrote {path}')
    return 0


# ------------------------------------------------------------------------------------------------- events ---

def preview_resolve(seed_ref):
    return {'kind': 'person', 'universe_id': 'preview', 'role': 'coach', 'persona_id': ct.profile_key(seed_ref)}


def preview_events(directory, index=None):
    edges = ct.reviewed_edges(ct.load_batches(directory), index=index)
    envelopes, unresolved = [], []
    for e in edges:
        env, bad = ct.edge_events(e, preview_resolve)
        envelopes += env
        unresolved += bad
    return {'schema_version': ct.SCHEMA_VERSION, 'preview': True, 'edges': len(edges), 'envelopes': envelopes,
            'unresolved': unresolved}


def cmd_events(args):
    out = preview_events(_dir(args))
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + '\n')
    print(f"wrote {path}: {len(out['envelopes'])} envelopes from {out['edges']} reviewed edges "
          f"({len(out['unresolved'])} unresolved)")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog='tools.coaching_tree_review', description='Review tool for coaching trees (offline).')
    sub = p.add_subparsers(dest='cmd', required=True)
    v = sub.add_parser('validate', help='print each coaching-tree file\'s errors')
    v.add_argument('--batch', choices=sorted(ct.BATCHES) + ['registry'])
    r = sub.add_parser('report', help='write a Markdown review page for a batch or the registry')
    r.add_argument('--batch', required=True, choices=sorted(ct.BATCHES) + ['registry'])
    r.add_argument('--out', required=True)
    a = sub.add_parser('apply-review', help="record JR's approve/edit/reject decisions in the files")
    a.add_argument('--decisions', required=True)
    a.add_argument('--reviewer', default='JR')
    a.add_argument('--today', help='review date (default: today)')
    e = sub.add_parser('events', help='write reviewed edges as relationship envelopes (preview only)')
    e.add_argument('--out', required=True)
    for x in (v, r, a, e):
        x.add_argument('--dir', help=f'coaching-tree directory (default: {ct.TREE_DIR})')
    args = p.parse_args(argv)
    return {'validate': cmd_validate, 'report': cmd_report, 'apply-review': cmd_apply_review,
            'events': cmd_events}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
