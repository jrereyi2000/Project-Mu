"""Review tool for real-coach and real-inspired owner profiles (docs/in_progress/REAL_COACH_PROFILES_PLAN.md
§2.3; owners: docs/in_progress/OWNERS_SCOUTS_VOTERS_PLAN.md §2.2).

Offline throughout: no database, network or model calls.

  python -m tools.coach_profile_review validate [--kind coach|owner] [--dir DIR] [--teams BUF,MIA,...] [--file PATH ...]
  python -m tools.coach_profile_review report [--kind coach|owner] --teams BUF,MIA,... --out verify_output/coach_review/<batch>.md
        [--dir DIR] [--file PATH ...]
  python -m tools.coach_profile_review apply-review [--kind coach|owner] --decisions PATH [--dir DIR] [--reviewer JR]
        [--today YYYY-MM-DD]
  python -m tools.coach_profile_review infer [--kind coach|owner] [--dir DIR] [--teams ...] [--file PATH ...] [--write]
  python -m tools.coach_profile_review convert-v2 [--kind coach|owner] [--dir DIR] [--teams ...] [--file PATH ...]
        [--edits PATH] [--write] [--today YYYY-MM-DD]

--kind owner (default coach) works on newsroom/config/owner_personas/<TEAM>.json through personality/owner_profiles.py:
the same rules, one file per team, keys owner:<TEAM>.

validate prints each file's errors (exit 0 when every file passes, 1 otherwise; requested teams with no files
are listed but do not fail). report writes one Markdown review page: per coach, sliders with their reasons,
traits with linked sources, views, want/fear, voice, signatures and ties, each claim with its confidence, and
a decision line for JR. apply-review reads {"<key>": {"status": "approved|edited|rejected", "notes": "...",
"edits": {field: value}}}, applies edits, re-validates and stamps review; nothing is written unless every
decision is valid (exit 0), otherwise exit 1 with the reasons.

infer (docs/done/INFERRED_SLIDERS_AND_FOCUS_PLAN.md §2.3) prints, per v2 file, each inferred slider with its
basis (traits and leans, seed step, balancing); --write stores the `slider_inference` block (JR's locked values and
the review status kept). v1 files are skipped. Exit 0, or 1 when a file fails other validation (not written).
convert-v2 moves v1 files to v2 mechanically (schema, want/fear -> motivation, focus {want: null, fear: null},
slider_inference); `--edits` ({key: {motivation, focus}}) supplies the hand-written motivation and focus before
validation. A reviewed file goes back to draft with a conversion note. --write writes only valid results.
"""
import argparse
from datetime import date
import json
from pathlib import Path
import sys

from newsroom import personas as legacy
from personality import coach_profiles as cp
from personality import focus as fo
from personality import owner_profiles as op
from personality import slider_inference as si

EDITABLE = ('sliders', 'slider_evidence', 'traits', 'trait_evidence', 'views', 'signatures', 'want', 'fear',
            'voice', 'ties', 'evidence', 'slider_inference', 'motivation', 'focus')
CONVERT_EDITABLE = ('motivation', 'focus', 'slider_inference')


class _Kind:
    """What differs between coach and owner files: module, directory, file names, keys and validation."""

    def __init__(self, name):
        self.name = name
        self.mod = op if name == 'owner' else cp

    @property
    def v2_version(self):
        return self.mod.SCHEMA_VERSION_V2

    @property
    def directory(self):
        return self.mod.PROFILE_DIR

    def load(self, directory, teams):
        return self.mod.load_profiles(directory, teams=teams)

    def validator(self):
        catalogue, coaches = legacy.load_traits(), cp.seed_index()
        if self.name == 'owner':
            owners = op.owner_index()
            return lambda doc: op.validate_profile_file(doc, catalogue=catalogue, index=owners, coach_index=coaches)
        return lambda doc: cp.validate_profile_file(doc, catalogue=catalogue, index=coaches)

    def files_for(self, team):
        return [f'{team}.json'] if self.name == 'owner' else [f'{team}_{r}.json' for r in cp.ROLES]

    def path_for_key(self, directory, key):
        parts = key.split(':') if isinstance(key, str) else []
        if self.name == 'owner':
            ok = len(parts) == 2 and parts[0] == 'owner'
            return Path(cp._resolve_dir(directory)) / f'{parts[1]}.json' if ok else None
        if len(parts) != 3 or parts[0] != 'coach':
            return None
        return Path(cp._resolve_dir(directory)) / f'{parts[1]}_{parts[2]}.json'


def _kind(args):
    return _Kind(getattr(args, 'kind', None) or 'coach')


def _teams(value):
    return [t.strip().upper() for t in value.split(',') if t.strip()] if value else None


def _load_file(path):
    doc = json.loads(Path(path).read_text())
    if isinstance(doc, dict):
        doc['_file'] = Path(path).name
    return doc


def _dir(args):
    return args.dir or _kind(args).directory


def _docs(args):
    if args.file:
        return [_load_file(p) for p in args.file]
    return _kind(args).load(_dir(args), _teams(args.teams))


def _missing(args, docs):
    if args.file or not args.teams:
        return []
    have = {d.get('_file') for d in docs}
    return [f[:-5] for t in _teams(args.teams) for f in _kind(args).files_for(t) if f not in have]


def _label(doc):
    return doc.get('key') or doc.get('_file') or '?'


# ------------------------------------------------------------------------------------------------ validate ---

def cmd_validate(args):
    docs = _docs(args)
    validate = _kind(args).validator()
    failed = 0
    for doc in docs:
        errors = validate(doc)
        if errors:
            failed += 1
            print(f'{_label(doc)}: {len(errors)} error(s)')
            for e in errors:
                print(f'  - {e}')
        else:
            print(f'{_label(doc)}: ok')
    missing = _missing(args, docs)
    if missing:
        print('not written yet: ' + ', '.join(missing))
    print(f'{len(docs)} profile(s), {failed} with errors')
    return 1 if failed else 0


# -------------------------------------------------------------------------------------------------- report ---

def _md(text):
    return str(text).replace('|', '\\|').replace('\n', ' ').strip()


def _cite(ids, by_id):
    parts = []
    for eid in ids or []:
        ev = by_id.get(eid)
        if ev is None:
            parts.append(f'{eid} (unknown)')
        else:
            parts.append(f"[{eid}]({ev.get('url')}) {ev.get('confidence')}")
    return ', '.join(parts) or 'none'


def _tie_target(target, index):
    kind = target.get('kind')
    if kind == 'coach':
        ref = target.get('seed_ref') or {}
        row = index.get((ref.get('table'), ref.get('team'), ref.get('role')))
        who = f"{row['name']} ({ref.get('team')} {ref.get('role')})" if row else f'{ref} (no seed row)'
        return f'coach {who}; in the 2026 seed: {"yes" if row else "no"}'
    return f"{kind} {target.get('name')} ({target.get('team_2026')}); resolved when the lifecycle plan applies"


def _slider_sources(doc):
    """{slider: source label} for the report: evidence (ids), inferred (basis), inferred and balanced, set by JR."""
    slider_ev = doc.get('slider_evidence') or {}
    out = {}
    block = doc.get('slider_inference') if isinstance(doc.get('slider_inference'), dict) else None
    basis = {}
    if block is not None:
        locked = [x for x in (block.get('locked') or []) if x in (block.get('values') or {})]
        values = block.get('values') or {}
        try:
            basis = si.infer_sliders(doc, locked={x: values[x] for x in locked})['basis']
        except (KeyError, TypeError, ValueError):
            basis = {}
    for s in legacy.SLIDERS:
        if slider_ev.get(s):
            out[s] = f"evidence ({', '.join(slider_ev[s])})"
        elif block is not None and s in (block.get('values') or {}):
            if s in (block.get('locked') or []):
                out[s] = 'set by JR'
                continue
            b = basis.get(s) or {}
            parts = [f'{t} {v:+d}' for t, v in (b.get('traits') or {}).items()]
            if b.get('jitter'):
                parts.append(f"seed {b['jitter']:+d}".replace('-', '\u2212'))
            label = f"inferred ({', '.join(parts)})" if parts else 'inferred'
            out[s] = label + (', balanced' if b.get('balanced') else '')
        else:
            out[s] = 'none (neutral)'
    return out


def _subject_label(sub, index):
    if sub.get('kind') == 'coach':
        ref = sub.get('seed_ref') or {}
        row = index.get((ref.get('table'), ref.get('team'), ref.get('role')))
        name = row['name'] if row else ref.get('name') or '?'
        return name, f"refreshes when {name} leaves the {ref.get('team')} {ref.get('role')} seat"
    name = sub.get('name') or '?'
    return name, f"refreshes when {name} leaves {sub.get('team_2026')}, retires or leaves the league"


def _focus_line(slot, entry, index):
    if not isinstance(entry, dict):
        return f'- {slot.capitalize()}: none (motivation only)'
    subs = [_subject_label(x, index) for x in entry.get('subjects') or [] if isinstance(x, dict)]
    line = f"- {slot.capitalize()}: {_md(entry.get('text', ''))}"
    if subs:
        line += f" (subjects: {', '.join(n for n, _ in subs)}; {'; '.join(r for _, r in subs)})"
    if entry.get('until_season'):
        line += f"; until {entry['until_season']}"
    return line


def _coach_section(doc, catalogue, index, validate=None):
    by_id = {ev.get('id'): ev for ev in doc.get('evidence') or [] if isinstance(ev, dict)}
    review = doc.get('review') or {}
    errors = (validate or (lambda d: cp.validate_profile_file(d, catalogue=catalogue, index=index)))(doc)
    out = [f"## {doc.get('name')}: {doc.get('team')} {doc.get('role')} (`{_label(doc)}`)", '']
    out.append(f"Review status: **{review.get('status')}**"
               + (f", by {review.get('reviewer')} on {review.get('reviewed_at')}" if review.get('reviewer') else '')
               + f". Sources: {len(by_id)}. Validation: "
               + ('passes.' if not errors else f'{len(errors)} error(s).'))
    for e in errors:
        out.append(f'- error: {e}')
    out += ['', f"**Decision ({_label(doc)}):** [ ] approve  [ ] edit  [ ] reject  Notes: ______", '']

    slider_ev = doc.get('slider_evidence') or {}
    v2 = cp.is_v2(doc)
    sliders = si.effective_sliders(doc) if v2 else (doc.get('sliders') or {})
    sources = _slider_sources(doc)
    note = ('a slider with no evidence gets an inferred value from his traits (marked inferred)' if v2
            else 'a slider with no evidence stays 5')
    out += ['### Sliders', '', f'Total {sum(v for v in sliders.values() if isinstance(v, int))} '
            f'(band {cp.SLIDER_RANGE[0]}-{cp.SLIDER_RANGE[1]}); {note}.', '',
            '| Slider | Value | Source | Citations |', '|---|---|---|---|']
    for s in legacy.SLIDERS:
        ids = slider_ev.get(s)
        out.append(f'| {s} | {sliders.get(s)} | {sources[s]} | {_cite(ids, by_id) if ids else "none"} |')

    trait_ev = doc.get('trait_evidence') or {}
    out += ['', '### Traits', '']
    for t in doc.get('traits') or []:
        entry = catalogue.get(t, {})
        out.append(f"- **{entry.get('label', t)}** (`{t}`, {entry.get('category', '?')}): "
                   f"{entry.get('prompt', '')}. Sources: {_cite(trait_ev.get(t), by_id)}")

    out += ['', '### Views', '']
    for v in doc.get('views') or []:
        out.append(f"- {_md(v.get('text', ''))} (when: {', '.join(v.get('when') or [])})")
    if cp.is_v2(doc):
        motivation = doc.get('motivation') or {}
        out += ['', '### Motivation', '', f"- Want: {_md(motivation.get('want', ''))}",
                f"- Fear: {_md(motivation.get('fear', ''))}", '', '### Current focus', '']
        for slot in fo.SLOTS:
            out.append(_focus_line(slot, (doc.get('focus') or {}).get(slot), index))
    else:
        out += ['', '### Want and fear', '', f"- Want: {_md(doc.get('want', ''))}",
                f"- Fear: {_md(doc.get('fear', ''))}"]
    out += ['', '### Voice', '', _md(doc.get('voice', '')), '', '### Signatures', '']
    for s in doc.get('signatures') or []:
        out.append(f"- {_md(s.get('text') if isinstance(s, dict) else s)}")

    out += ['', '### Starting ties', '']
    ties = doc.get('ties') or []
    if not ties:
        out.append('None.')
    for t in ties:
        out.append(f"- {t.get('relationship_kind')}, {t.get('outcome')}: "
                   f"{_tie_target(t.get('target') or {}, index)}. Sources: {_cite(t.get('evidence'), by_id)}")

    out += ['', '### Sources', '', '| ID | Published | Publisher | Confidence | What it shows (paraphrase) |',
            '|---|---|---|---|---|']
    for eid, ev in by_id.items():
        out.append(f"| [{eid}]({ev.get('url')}) | {ev.get('published')} | {_md(ev.get('publisher', ''))} | "
                   f"{ev.get('confidence')} | {_md(ev.get('summary', ''))} |")
    return out


def build_report(docs, *, title, missing=(), kind='coach'):
    catalogue, index = legacy.load_traits(), cp.seed_index()
    k = _Kind(kind)
    validate = k.validator()
    out = [f'# {kind.capitalize()} profile review: {title}', '',
           f'{len(docs)} profile(s). Schema {k.mod.SCHEMA_VERSION} or {k.mod.SCHEMA_VERSION_V2}; sources through {k.mod.SOURCE_CUTOFF}. '
           f'Each profile is a fictional character inspired by the real {kind}; every claim cites a dated public '
           'source by its ID and confidence, and each source is summarised once, in the Sources table. Tick one box per profile and add notes; the decisions are '
           'recorded with `python -m tools.coach_profile_review apply-review`.', '']
    if missing:
        out += ['Not written yet: ' + ', '.join(missing) + '.', '']
    for doc in sorted(docs, key=_label):
        out += _coach_section(doc, catalogue, index, validate) + ['']
    return '\n'.join(out).rstrip('\n') + '\n'


def cmd_report(args):
    docs = _docs(args)
    if not docs:
        print('no profiles to report', file=sys.stderr)
        return 1
    title = args.title or (args.teams.upper() if args.teams else ', '.join(_label(d) for d in docs))
    text = build_report(docs, title=title, missing=_missing(args, docs), kind=_kind(args).name)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text)
    print(f'wrote {out} ({len(docs)} profile(s))')
    return 0


# -------------------------------------------------------------------------------------------- apply-review ---

def _path_for_key(directory, key):
    return _Kind('coach').path_for_key(directory, key)


def apply_decisions(decisions, directory, *, reviewer='JR', today=None, kind='coach'):
    """([(path, new_doc)], errors). Nothing is written here."""
    today = today or date.today().isoformat()
    k = _Kind(kind)
    validate = k.validator()
    planned, errors = [], []
    if not isinstance(decisions, dict):
        return [], ['decisions must be an object keyed by profile key']
    for key, dec in sorted(decisions.items()):
        path = k.path_for_key(directory, key)
        if path is None or not path.exists():
            errors.append(f'{key}: no profile file')
            continue
        if not isinstance(dec, dict) or dec.get('status') not in ('approved', 'edited', 'rejected'):
            errors.append(f'{key}: status must be approved, edited or rejected')
            continue
        doc = _load_file(path)
        edits = dec.get('edits') or {}
        if edits and dec['status'] != 'edited':
            errors.append(f'{key}: edits need status edited')
            continue
        if dec['status'] == 'edited' and not edits:
            errors.append(f'{key}: status edited needs edits')
            continue
        bad = sorted(set(edits) - set(EDITABLE))
        if bad:
            errors.append(f'{key}: fields {bad} cannot be edited on review')
            continue
        doc.update(json.loads(json.dumps(edits)))
        doc['review'] = dict(status=dec['status'], reviewer=reviewer, reviewed_at=today, notes=dec.get('notes'))
        if dec['status'] != 'rejected':
            problems = validate(doc)
            if problems:
                errors.append(f'{key}: refused, the result fails validation: ' + '; '.join(problems))
                continue
        planned.append((path, cp.file_doc(doc)))
    return planned, errors


def cmd_apply_review(args):
    decisions = json.loads(Path(args.decisions).read_text())
    planned, errors = apply_decisions(decisions, _dir(args), reviewer=args.reviewer, today=args.today,
                                      kind=_kind(args).name)
    for e in errors:
        print(e)
    if errors:
        print('nothing written')
        return 1
    for path, doc in planned:
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + '\n')
        print(f"{doc['key']}: {doc['review']['status']}")
    return 0


# ---------------------------------------------------------------------------------------- infer / convert ---

def _basis_text(s, value, b):
    if b.get('locked'):
        return f'  {s} = {value} (set by JR, locked)'
    traits = ', '.join(f'{t} {v:+d}' for t, v in (b.get('traits') or {}).items()) or 'no leaning traits'
    text = f"  {s} = {value} (lean {b['lean']:+d} from {traits}; seed step {b['jitter']:+d}"
    if b.get('balanced'):
        text += f"; balanced {b['balanced']} step(s) toward 5"
    return text + ')'


def _write_doc(path, doc):
    Path(path).write_text(json.dumps(cp.file_doc(doc), indent=2, ensure_ascii=False) + '\n')


def _path(args, doc):
    return Path(cp._resolve_dir(_dir(args))) / doc['_file'] if not args.file else next(
        Path(p) for p in args.file if Path(p).name == doc['_file'])


def cmd_infer(args):
    validate = _kind(args).validator()
    failed = 0
    for doc in _docs(args):
        if not cp.is_v2(doc):
            print(f'{_label(doc)}: v1, skipped (run convert-v2 first)')
            continue
        new = cp.with_inference(doc)
        block = new['slider_inference']
        basis = si.infer_sliders(new, locked={x: block['values'][x] for x in block['locked']})['basis']
        print(f"{_label(doc)}: {len(block['values'])} inferred")
        for s, v in block['values'].items():
            print(_basis_text(s, v, basis[s]))
        errors = validate(new)
        if errors:
            failed += 1
            print(f'  not written: {len(errors)} error(s)')
            for e in errors:
                print(f'  - {e}')
            continue
        if args.write and cp.file_doc(new) != cp.file_doc(doc):
            _write_doc(_path(args, doc), new)
            print('  written')
    return 1 if failed else 0


def cmd_convert(args):
    k = _kind(args)
    validate = k.validator()
    today = args.today or date.today().isoformat()
    edits = json.loads(Path(args.edits).read_text()) if args.edits else {}
    failed = converted = 0
    for doc in _docs(args):
        if cp.is_v2(doc):
            print(f'{_label(doc)}: already v2')
            continue
        new = cp.convert_v2(doc, k.v2_version, today=today)
        extra = edits.get(doc.get('key')) or {}
        bad = sorted(set(extra) - set(CONVERT_EDITABLE))
        if bad:
            failed += 1
            print(f'{_label(doc)}: edits name fields {bad} (only {list(CONVERT_EDITABLE)})')
            continue
        if extra:
            new.update(json.loads(json.dumps(extra)))
            new = cp.with_inference(new)
        converted += 1
        errors = validate(new)
        n = len(new['slider_inference']['values'])
        print(f"{_label(doc)}: converted to {k.v2_version} ({n} inferred slider(s), review "
              f"{(new.get('review') or {}).get('status')})" + (f'; {len(errors)} error(s)' if errors else ''))
        for e in errors:
            print(f'  - {e}')
        if errors:
            failed += 1
        elif args.write:
            _write_doc(_path(args, doc), new)
            print('  written')
    print(f'{converted} conversion(s), {failed} not valid' + ('' if args.write else '; nothing written (dry run)'))
    return 1 if failed and args.write else 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m tools.coach_profile_review',
                                 description='Validate, report and record reviews of real-coach and owner profiles '
                                             '(offline).')
    sub = ap.add_subparsers(dest='cmd', required=True)
    v = sub.add_parser('validate', help='print each profile file\'s errors')
    r = sub.add_parser('report', help='write a Markdown review page for a batch')
    for p in (v, r):
        p.add_argument('--dir', help='profile directory (default: the kind\'s own)')
        p.add_argument('--teams', help='comma-separated team codes, e.g. BUF,MIA')
        p.add_argument('--file', nargs='+', help='specific profile files instead of --dir/--teams')
    r.add_argument('--out', required=True)
    r.add_argument('--title', help='page title (default: the team list)')
    a = sub.add_parser('apply-review', help="record JR's approve/edit/reject decisions in the files")
    a.add_argument('--decisions', required=True)
    a.add_argument('--dir', help='profile directory (default: the kind\'s own)')
    i = sub.add_parser('infer', help='print (and with --write store) the inferred values of no-evidence sliders')
    c = sub.add_parser('convert-v2', help='convert v1 files to the v2 schema (motivation, focus, slider_inference)')
    for p in (i, c):
        p.add_argument('--dir', help='profile directory (default: the kind\'s own)')
        p.add_argument('--teams', help='comma-separated team codes, e.g. BUF,MIA')
        p.add_argument('--file', nargs='+', help='specific profile files instead of --dir/--teams')
        p.add_argument('--write', action='store_true', help='write valid results (default: dry run)')
    c.add_argument('--edits', help='JSON {key: {motivation, focus, slider_inference}} applied before validation')
    c.add_argument('--today', help='conversion date (default: today)')
    for p in (v, r, a, i, c):
        p.add_argument('--kind', choices=('coach', 'owner'), default='coach')
    a.add_argument('--reviewer', default='JR')
    a.add_argument('--today', help='review date (default: today)')
    args = ap.parse_args(argv)
    if args.cmd == 'report' and not (args.teams or args.file):
        ap.error('report needs --teams or --file')
    return {'validate': cmd_validate, 'report': cmd_report, 'apply-review': cmd_apply_review, 'infer': cmd_infer,
            'convert-v2': cmd_convert}[args.cmd](args)


if __name__ == '__main__':
    sys.exit(main())
