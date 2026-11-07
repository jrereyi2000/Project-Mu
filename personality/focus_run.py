"""The offseason focus refresh: seed, run a season, report, set and revert (INFERRED_SLIDERS_AND_FOCUS_PLAN.md §2.6).

    seed(cur, universe_id, season, tick, docs, *, write=True)   seq-0 rows from the reviewed v2 profile files
    run_season(cur, universe_id, season, *, dry_run=False, docs=None, templates=None) -> report
    render_report(report) / report_path(universe_id, season) / write_report(report, path)
    record_failure(cur, universe_id, season, error)
    set_focus(cur, universe_id, persona_id, slot, *, text, subjects, reason, season, tick)   a JR row
    revert(cur, focus_id, reason)                               back to the row it superseded, as a JR row

    python3 -m personality.focus_run season --season S [--dry-run] [--universe U] [--dsn DSN]
    python3 -m personality.focus_run report --season S [--out PATH] [--universe U] [--dsn DSN]
    python3 -m personality.focus_run show --persona ID [--as-of S:T] [--universe U] [--dsn DSN]
    python3 -m personality.focus_run set --persona ID --slot want|fear --text TEXT [--subject kind:namespace:id ...]
                                         --reason TEXT [--season S --tick T] [--universe U] [--dsn DSN]
    python3 -m personality.focus_run revert --focus-id ID --reason TEXT [--dsn DSN]

At (season, tick 24) every active row is checked against the season facts (personality.focus_facts): no stale
cause keeps it; a stale `profile` or `template` row is replaced by a template draft (personality.focus.draft_focus)
with the causes and a change note; a stale `jr` row is only reported (`jr_stale`), never replaced. A slot with no
focus stays empty unless the holder moved, when one is drafted. Rows are append-only (focus_store). The caller's
cursor and transaction throughout; the CLI commits unless --dry-run. DATABASE_URL or --dsn, refusing madden_2036
and postgres. Exit 0 / 2 invalid / 3 not found or not migrated / 4 conflict.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

from personality import focus as fo
from personality import focus_facts as ff
from personality import focus_store as fs

EXIT_OK, EXIT_INVALID, EXIT_NOT_FOUND, EXIT_CONFLICT = 0, 2, 3, 4
REFUSED_DATABASES = ('madden_2036', 'postgres')
REPORT_DIR = os.path.join('verify_output', 'personality_focus')
FOCUS_TICK = ff.FOCUS_TICK
START_SEASON = 2026
REVIEWED = ('approved', 'edited')


class FocusRunError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


def tables_ready(cur):
    return fs.tables_ready(cur)


# ------------------------------------------------------------------------------------------------ docs ---

def load_docs():
    """Every coach and owner profile file (both schemas); seeding uses only reviewed v2 ones."""
    from personality import coach_profiles as cp, owner_profiles as op
    return cp.load_profiles() + op.load_profiles()


def _is_seedable(doc):
    return (isinstance(doc, dict) and str(doc.get('schema_version', '')).endswith('-v2')
            and (doc.get('review') or {}).get('status') in REVIEWED)


def _one(cur, sql, params):
    cur.execute(sql, params)
    rows = cur.fetchall()
    return rows[0][0] if len(rows) == 1 else None


def find_person(cur, universe_id, doc):
    """The core persona_id of a profile file's person in this universe, or None."""
    if doc.get('role') == 'OWNER':
        return _one(cur, '''/* q:owner_person */ SELECT persona_id FROM persona_core_profiles
                             WHERE universe_id = %s AND persona_type = 'owner' AND birth_key = %s''',
                    (universe_id, f"curated-owner:{doc.get('team')}:{START_SEASON}"))
    ref = doc.get('seed_ref') or {}
    if doc.get('role') == 'HC':
        ext = _one(cur, "/* q:coach_id */ SELECT id FROM coaches WHERE coach_name = %s", (ref.get('name'),))
        ns = 'db-coach'
    else:
        ext = _one(cur, '''/* q:coordinator_id */ SELECT DISTINCT c.id FROM coordinators c
                            JOIN coordinator_tenures t ON t.coordinator_id = c.id JOIN teams tm ON tm.id = t.team_id
                           WHERE c.coordinator_name = %s AND t.role = %s AND tm.abbreviation = %s
                             AND t.start_season <= %s AND (t.end_season IS NULL OR t.end_season >= %s)''',
                   (ref.get('name'), doc.get('role'), doc.get('team'), START_SEASON, START_SEASON))
        ns = 'db-coordinator'
    if ext is None:
        return None
    return _one(cur, '''/* q:alias */ SELECT persona_id FROM persona_core_aliases
                         WHERE universe_id = %s AND persona_type = 'coach' AND namespace = %s AND external_id = %s''',
                (universe_id, ns, str(ext)))


def resolve_subject(cur, sub):
    """{kind, name, binding, seat?} for a file subject; binding None when it can't be settled."""
    kind = sub.get('kind')
    if kind == 'player':
        pid = _one(cur, '''/* q:subject_player */ SELECT DISTINCT p.id FROM players p
                            JOIN player_seasons ps ON ps.player_id = p.id JOIN teams t ON t.id = ps.team_id
                           WHERE ps.season = %s AND t.abbreviation = %s AND (p.full_name = %s OR p.player_name = %s)''',
                   (START_SEASON, sub.get('team_2026'), sub.get('name'), sub.get('name')))
        return dict(kind='player', name=sub.get('name'),
                    binding=None if pid is None else dict(namespace='db-player', external_id=str(pid)))
    if kind == 'gm':
        gid = _one(cur, '''/* q:subject_gm */ SELECT g.id FROM gm_profiles g JOIN teams t ON t.id = g.team_id
                         WHERE t.abbreviation = %s AND g.gm_name = %s AND g.start_season <= %s
                           AND (g.end_season IS NULL OR g.end_season >= %s)''',
                   (sub.get('team_2026'), sub.get('name'), START_SEASON, START_SEASON))
        return dict(kind='gm', name=sub.get('name'), seat=dict(team=sub.get('team_2026')),
                    binding=None if gid is None else dict(namespace='db-gm', external_id=str(gid)))
    ref = sub.get('seed_ref') or {}
    if ref.get('role') == 'HC':
        cid = _one(cur, "/* q:coach_id */ SELECT id FROM coaches WHERE coach_name = %s", (ref.get('name'),))
        ns = 'db-coach'
    else:
        cid = _one(cur, '''/* q:coordinator_id */ SELECT DISTINCT c.id FROM coordinators c
                            JOIN coordinator_tenures t ON t.coordinator_id = c.id JOIN teams tm ON tm.id = t.team_id
                           WHERE c.coordinator_name = %s AND t.role = %s AND tm.abbreviation = %s
                             AND t.start_season <= %s AND (t.end_season IS NULL OR t.end_season >= %s)''',
                   (ref.get('name'), ref.get('role'), ref.get('team'), START_SEASON, START_SEASON))
        ns = 'db-coordinator'
    return dict(kind='coach', name=ref.get('name'), seat=dict(team=ref.get('team'), role=ref.get('role')),
                binding=None if cid is None else dict(namespace=ns, external_id=str(cid)))


def _persona_type(doc):
    return 'owner' if doc.get('role') == 'OWNER' else 'coach'


def seed(cur, universe_id, season, tick, docs, *, write=True):
    """seq-0 rows for each reviewed v2 file's non-null focus slots, once per (person, slot). Returns
    {'rows': [...], 'inserted', 'unresolved': [{persona_id, slot, subject}], 'skipped': [{key, reason}],
    'people': {persona_id: {team, seat}}} (people = the profile's own seat, for the moved-holder check)."""
    out = dict(rows=[], inserted=0, unresolved=[], skipped=[], people={})
    for doc in sorted(docs or [], key=lambda d: d.get('key', '')):
        if not _is_seedable(doc):
            out['skipped'].append(dict(key=doc.get('key'), reason='not a reviewed v2 profile'))
            continue
        pid = find_person(cur, universe_id, doc)
        if pid is None:
            out['skipped'].append(dict(key=doc.get('key'), reason='person not in this universe'))
            continue
        out['people'][pid] = dict(team=doc.get('team'), seat=doc.get('role'), persona_type=_persona_type(doc))
        existing = {r['slot'] for r in fs.history(cur, universe_id, pid)}
        for slot in fo.SLOTS:
            entry = (doc.get('focus') or {}).get(slot)
            if not isinstance(entry, dict) or slot in existing:
                continue
            subjects = [resolve_subject(cur, s) for s in entry.get('subjects') or []]
            for s in subjects:
                if s['binding'] is None:
                    out['unresolved'].append(dict(persona_id=pid, slot=slot, subject=s['name']))
            row = dict(universe_id=universe_id, persona_type=_persona_type(doc), persona_id=pid, slot=slot, seq=0,
                       text=entry['text'], subjects=subjects, anchor=dict(team=doc.get('team'), seat=doc.get('role')),
                       until_season=entry.get('until_season'), source='profile', template_id=None,
                       rules_version=fo.FOCUS_RULES_VERSION, status='active', valid_from_season=season,
                       valid_from_tick=tick, supersedes=None, stale_causes=[], reason=None, actor='system')
            row['focus_id'] = fs.focus_id(universe_id, pid, slot, 0)
            out['rows'].append(row)
            if write and fs.insert_focus(cur, row) == 'inserted':
                out['inserted'] += 1
    return out


# ------------------------------------------------------------------------------------------------- run ---

def _active_rows(cur, universe_id):
    cur.execute('/* q:active */ SELECT ' + ', '.join(fs.FOCUS_COLUMNS) + ''' FROM persona_focus
                   WHERE universe_id = %s AND status = 'active' ORDER BY persona_id, slot''', (universe_id,))
    return [dict(zip(fs.FOCUS_COLUMNS, r)) for r in cur.fetchall()]


def input_hash(facts, rows):
    payload = {'rules_version': fo.FOCUS_RULES_VERSION, 'facts': facts,
               'rows': sorted(({k: r.get(k) for k in ('focus_id', 'text', 'subjects', 'anchor', 'source', 'seq')}
                               for r in rows), key=lambda r: r['focus_id'])}
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def _counts(facts, rows, changes, jr_stale, *, dry_run):
    by_cause = {}
    for c in changes:
        for cause in c['causes']:
            by_cause[cause['cause']] = by_cause.get(cause['cause'], 0) + 1
    missing = sorted({t for f in facts.values() for t in (f.get('missing') or {})})
    return {'people': len(facts), 'rows': len(rows), 'kept': len(rows) - len([c for c in changes if c['old']])
            - len(jr_stale), ('would_replace' if dry_run else 'replaced'): len(changes), 'jr_stale': len(jr_stale),
            'by_cause': dict(sorted(by_cause.items())), 'missing_sources': missing}


def run_season(cur, universe_id, season, *, dry_run=False, docs=None, templates=None):
    """The OFFSEASON_START refresh for `season` (plan §2.6). dry_run writes nothing."""
    templates = fo.load_templates() if templates is None else templates
    docs = load_docs() if docs is None else docs
    seeded = seed(cur, universe_id, season, FOCUS_TICK, docs, write=not dry_run)
    rows = _active_rows(cur, universe_id)
    have = {r['focus_id'] for r in rows}
    rows += [r for r in seeded['rows'] if r['focus_id'] not in have]
    bindings = {}
    for r in rows:
        bindings.setdefault(r['persona_id'], []).extend(r.get('subjects') or [])
    facts = ff.facts(cur, universe_id, season, bindings=bindings)
    digest = input_hash(facts, rows)
    previous = fs.read_run(cur, universe_id, season, fo.FOCUS_RULES_VERSION)
    if previous and previous['status'] == 'APPLIED' and previous['input_hash'] == digest:
        return dict(universe_id=universe_id, season=season, rules_version=fo.FOCUS_RULES_VERSION, status='noop',
                    input_hash=digest, counts=previous['counts'], changes=[], jr_stale=[],
                    unresolved=seeded['unresolved'])
    active = {(r['persona_id'], r['slot']): r for r in rows}
    changes, jr_stale = [], []
    people = sorted(set(facts) & (set(seeded['people']) | {r['persona_id'] for r in rows}))
    for pid in people:
        f = facts[pid]
        for slot in fo.SLOTS:
            row = active.get((pid, slot))
            if row is not None:
                causes = fo.stale_causes(row, f, season)
                if not causes:
                    continue
                if row['source'] == 'jr':
                    jr_stale.append(dict(persona_id=pid, slot=slot, focus_id=row['focus_id'], causes=causes))
                    continue
            else:
                home = seeded['people'].get(pid)
                moved = (home is not None and f.get('team') is not None and f.get('seat') is not None
                         and (f['team'], f['seat']) != (home['team'], home['seat']))
                if not moved:
                    continue
                causes = [{'cause': 'HOLDER_MOVED'}]
            new = fo.draft_focus(universe_id=universe_id, persona_id=pid, season=season, slot=slot, facts=f,
                                 traits=f.get('traits') or [], templates=templates)
            note = fo.change_note(row, dict(new, slot=slot), causes, season)
            seq = max(fs.next_seq(cur, universe_id, pid, slot), row['seq'] + 1 if row else 0)
            ptype = row['persona_type'] if row else (seeded['people'].get(pid) or {}).get('persona_type',
                                                                                         f.get('role'))
            new_row = dict(universe_id=universe_id, persona_type=ptype, persona_id=pid, slot=slot, seq=seq,
                           text=new['text'], subjects=new['subjects'], anchor=new['anchor'], until_season=None,
                           source='template', template_id=new['template_id'], rules_version=fo.FOCUS_RULES_VERSION,
                           status='active', valid_from_season=season, valid_from_tick=FOCUS_TICK,
                           supersedes=row['focus_id'] if row else None, stale_causes=causes, reason=note,
                           actor='system')
            new_row['focus_id'] = fs.focus_id(universe_id, pid, slot, seq)
            if not dry_run:
                if row is not None:
                    fs.supersede(cur, row['focus_id'], new_row)
                else:
                    fs.insert_focus(cur, new_row)
            changes.append(dict(persona_id=pid, slot=slot, old=row['text'] if row else None, new=new['text'],
                                causes=causes, note=note, template_id=new['template_id'],
                                focus_id=new_row['focus_id']))
    counts = _counts(facts, rows, changes, jr_stale, dry_run=dry_run)
    status = 'dry_run' if dry_run else 'applied'
    if not dry_run:
        fs.record_run(cur, dict(universe_id=universe_id, season=season, rules_version=fo.FOCUS_RULES_VERSION,
                                input_hash=digest, counts=counts, status='APPLIED', error=None))
    return dict(universe_id=universe_id, season=season, rules_version=fo.FOCUS_RULES_VERSION, status=status,
                input_hash=digest, counts=counts, changes=changes, jr_stale=jr_stale,
                unresolved=seeded['unresolved'])


def record_failure(cur, universe_id, season, error):
    """A FAILED run row, unless the season already has an APPLIED one (kept as it is)."""
    previous = fs.read_run(cur, universe_id, season, fo.FOCUS_RULES_VERSION)
    if previous and previous['status'] == 'APPLIED':
        return
    fs.record_run(cur, dict(universe_id=universe_id, season=season, rules_version=fo.FOCUS_RULES_VERSION,
                            input_hash='', counts={}, status='FAILED', error=str(error)[:2000]))


# ---------------------------------------------------------------------------------------------- report ---

def _cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def render_report(report):
    counts = report.get('counts') or {}
    changes = sorted(report.get('changes') or [], key=lambda c: (c['persona_id'], c['slot']))
    title = 'would change' if report['status'] == 'dry_run' else 'changed'
    out = [f"# Focus refresh, season {report['season']}", '',
           f"Universe `{report['universe_id']}`, rules `{report['rules_version']}`, run {report['status']}, "
           f"input `{(report.get('input_hash') or '')[:12]}`.", '',
           f"{len(changes)} {title}; {counts.get('kept', 0)} kept, {counts.get('jr_stale', 0)} set by JR and stale "
           f"(not replaced), of {counts.get('rows', 0)} focus rows for {counts.get('people', 0)} people.", '']
    if counts.get('missing_sources'):
        out += ['Sources missing (their facts read as unknown): ' + ', '.join(counts['missing_sources']) + '.', '']
    out += ['## Changes', '']
    if changes:
        out += ['| Person | Slot | Was | Now | Causes | Note |', '|---|---|---|---|---|---|']
        for c in changes:
            causes = ', '.join(x['cause'] + (f" ({x['subject']})" if x.get('subject') else '') for x in c['causes'])
            out.append('| ' + ' | '.join(_cell(v) for v in (f"`{c['persona_id']}`", c['slot'], c['old'] or '(none)',
                                                            c['new'], causes, c['note'])) + ' |')
    else:
        out.append('None.')
    stale = report.get('jr_stale') or []
    if stale:
        out += ['', '## Set by JR and stale (kept; `set` or `revert` by hand)', '']
        out += [f"- `{s['persona_id']}` {s['slot']} ({s['focus_id']}): "
                + ', '.join(x['cause'] for x in s['causes']) for s in stale]
    unresolved = report.get('unresolved') or []
    if unresolved:
        out += ['', '## Unresolved subjects at seeding', '']
        out += [f"- `{u['persona_id']}` {u['slot']}: {u['subject']}" for u in unresolved]
    if counts.get('by_cause'):
        out += ['', '## By cause', '', '| Cause | Count |', '|---|---|']
        out += [f'| {k} | {v} |' for k, v in counts['by_cause'].items()]
    return '\n'.join(out) + '\n'


def report_path(universe_id, season, directory=REPORT_DIR):
    safe = ''.join(ch if ch.isalnum() or ch in '-_' else '_' for ch in str(universe_id))
    return os.path.join(directory, f'focus_{safe}_{season}.md')


def write_report(report, path):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w') as fh:
        fh.write(render_report(report))
    return path


def stored_report(cur, universe_id, season):
    run = fs.read_run(cur, universe_id, season, fo.FOCUS_RULES_VERSION)
    if run is None:
        raise FocusRunError(EXIT_NOT_FOUND, f'no focus run recorded for {universe_id} season {season}')
    cur.execute('/* q:season_rows */ SELECT ' + ', '.join(fs.FOCUS_COLUMNS) + ''' FROM persona_focus
                   WHERE universe_id = %s AND valid_from_season = %s AND source = 'template'
                   ORDER BY persona_id, slot, seq''', (universe_id, season))
    changes = []
    for r in (dict(zip(fs.FOCUS_COLUMNS, x)) for x in cur.fetchall()):
        old = fs.read_row(cur, r['supersedes']) if r.get('supersedes') else None
        changes.append(dict(persona_id=r['persona_id'], slot=r['slot'], old=old['text'] if old else None,
                            new=r['text'], causes=r.get('stale_causes') or [], note=r.get('reason') or '',
                            focus_id=r['focus_id']))
    return dict(universe_id=universe_id, season=season, rules_version=run['rules_version'],
                input_hash=run['input_hash'], status=run['status'].lower(), counts=run['counts'] or {},
                changes=changes, jr_stale=[], unresolved=[])


# --------------------------------------------------------------------------------------- set and revert ---

def _check_text(text, subjects):
    n = len(fo.words(text))
    if not isinstance(text, str) or not text.strip() or n > fo.MAX_FOCUS_WORDS:
        raise FocusRunError(EXIT_INVALID, f'focus text must be 1-{fo.MAX_FOCUS_WORDS} words, got {n}')
    if len(subjects) > fo.MAX_SUBJECTS:
        raise FocusRunError(EXIT_INVALID, f'at most {fo.MAX_SUBJECTS} subjects')
    for s in subjects:
        if s.get('kind') not in fo.SUBJECT_KINDS or ff.subject_key(s.get('binding')) is None:
            raise FocusRunError(EXIT_INVALID, f'subject {s} must be kind player|coach|gm with a binding')


def _persona_type_of(cur, universe_id, persona_id):
    ptype = _one(cur, '''/* q:persona_type */ SELECT persona_type FROM persona_core_profiles
                          WHERE universe_id = %s AND persona_id = %s''', (universe_id, persona_id))
    if ptype not in ('coach', 'owner'):
        raise FocusRunError(EXIT_NOT_FOUND, f'{persona_id} is not a coach or owner in {universe_id}')
    return ptype


def set_focus(cur, universe_id, persona_id, slot, *, text, subjects, reason, season, tick):
    """JR's own focus for a slot: a `jr` row that supersedes the active one (never replaced automatically)."""
    if slot not in fo.SLOTS:
        raise FocusRunError(EXIT_INVALID, 'slot must be want or fear')
    if not (isinstance(reason, str) and reason.strip()):
        raise FocusRunError(EXIT_INVALID, 'a reason is required')
    subjects = list(subjects or [])
    _check_text(text, subjects)
    ptype = _persona_type_of(cur, universe_id, persona_id)
    current = fs.read_focus(cur, universe_id, persona_id)[slot]
    seq = fs.next_seq(cur, universe_id, persona_id, slot)
    row = dict(universe_id=universe_id, persona_type=ptype, persona_id=persona_id, slot=slot, seq=seq, text=text.strip(),
               subjects=subjects, anchor=dict(current['anchor']) if current else {}, until_season=None, source='jr',
               template_id=None, rules_version=fo.FOCUS_RULES_VERSION, status='active', valid_from_season=season,
               valid_from_tick=tick, stale_causes=[], reason=reason.strip(), actor='JR')
    row['focus_id'] = fs.focus_id(universe_id, persona_id, slot, seq)
    if current is not None:
        return fs.supersede(cur, current['focus_id'], row)
    fs.insert_focus(cur, dict(row, supersedes=None))
    return fs.read_row(cur, row['focus_id'])


def revert(cur, focus_id, reason):
    """Undo an active row: it becomes `reverted` and a new JR row copies the row it superseded. Append-only."""
    if not (isinstance(reason, str) and reason.strip()):
        raise FocusRunError(EXIT_INVALID, 'a reason is required')
    row = fs.read_row(cur, focus_id)
    if row is None:
        raise FocusRunError(EXIT_NOT_FOUND, f'no focus row {focus_id}')
    if row['status'] != 'active':
        raise FocusRunError(EXIT_CONFLICT, f'{focus_id} is {row["status"]}, not the active focus')
    prev = fs.read_row(cur, row['supersedes']) if row.get('supersedes') else None
    if prev is None:
        raise FocusRunError(EXIT_CONFLICT, f'{focus_id} superseded nothing; use set instead')
    fs.mark_reverted(cur, focus_id)
    seq = fs.next_seq(cur, row['universe_id'], row['persona_id'], row['slot'])
    new = dict(universe_id=row['universe_id'], persona_type=row['persona_type'], persona_id=row['persona_id'],
               slot=row['slot'], seq=seq, text=prev['text'], subjects=prev.get('subjects') or [],
               anchor=prev.get('anchor') or {}, until_season=prev.get('until_season'), source='jr', template_id=None,
               rules_version=fo.FOCUS_RULES_VERSION, status='active', valid_from_season=row['valid_from_season'],
               valid_from_tick=row['valid_from_tick'], supersedes=focus_id, stale_causes=[],
               reason=f'revert:{focus_id}: {reason.strip()}', actor='JR')
    new['focus_id'] = fs.focus_id(row['universe_id'], row['persona_id'], row['slot'], seq)
    fs.insert_focus(cur, new)
    return fs.read_row(cur, new['focus_id'])


# ------------------------------------------------------------------------------------------------- CLI ---

def connect(dsn):
    """A non-autocommit connection. Tests replace this with a fake."""
    import psycopg
    return psycopg.connect(dsn)


def database_name(dsn):
    try:
        from psycopg.conninfo import conninfo_to_dict
        return conninfo_to_dict(dsn).get('dbname')
    except Exception:  # noqa: BLE001 - an unparsable DSN is refused below
        return None


def _dsn(args):
    dsn = args.dsn or os.environ.get('DATABASE_URL')
    if not dsn:
        raise FocusRunError(EXIT_INVALID, 'no database: pass --dsn or set DATABASE_URL')
    name = database_name(dsn)
    if name is None or name in REFUSED_DATABASES:
        raise FocusRunError(EXIT_INVALID, f'refusing database {name!r} (madden_2036 and postgres are refused)')
    return dsn


def resolve_universe(cur, explicit=None):
    if explicit:
        return explicit
    from engines.relationship_producers import sources
    try:
        league = sources.league_id(cur)
    except Exception as e:  # noqa: BLE001
        raise FocusRunError(EXIT_INVALID, f'no league: {e}') from e
    universe = sources.universe_id(cur, league)
    if universe is None:
        raise FocusRunError(EXIT_NOT_FOUND, f'no lifecycle universe registered for league {league}')
    return universe


def parse_subject(cur, value):
    """'kind:namespace:external_id' -> a subject with its binding (and a name looked up when possible)."""
    parts = str(value).split(':')
    if len(parts) != 3 or parts[0] not in fo.SUBJECT_KINDS or not parts[1] or not parts[2]:
        raise FocusRunError(EXIT_INVALID, f'--subject {value!r} must be kind:namespace:external_id')
    kind, ns, ext = parts
    sql = {'db-player': "/* q:name_player */ SELECT coalesce(full_name, player_name) FROM players WHERE id = %s",
           'db-coach': '/* q:name_coach */ SELECT coach_name FROM coaches WHERE id = %s',
           'db-coordinator': '/* q:name_coordinator */ SELECT coordinator_name FROM coordinators WHERE id = %s',
           'db-gm': '/* q:name_gm */ SELECT gm_name FROM gm_profiles WHERE id = %s'}.get(ns)
    name = None
    if sql:
        try:
            name = _one(cur, sql, (int(ext),))
        except ValueError:
            name = None
    return dict(kind=kind, name=name or ext, binding=dict(namespace=ns, external_id=ext))


def _parser():
    ap = argparse.ArgumentParser(prog='python -m personality.focus_run',
                                 description='Focus refresh: run a season, report it, show, set or revert a focus.')
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('season', help="run a season's focus refresh (or preview it with --dry-run)")
    p.add_argument('--season', type=int, required=True)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--universe')
    p = sub.add_parser('report', help="markdown report of a recorded season's refresh")
    p.add_argument('--season', type=int, required=True)
    p.add_argument('--out')
    p.add_argument('--universe')
    p = sub.add_parser('show', help="a person's current focus (or as of season:tick) and history")
    p.add_argument('--persona', required=True)
    p.add_argument('--as-of')
    p.add_argument('--universe')
    p = sub.add_parser('set', help="set JR's own focus for a slot")
    p.add_argument('--persona', required=True)
    p.add_argument('--slot', choices=fo.SLOTS, required=True)
    p.add_argument('--text', required=True)
    p.add_argument('--subject', action='append', default=[])
    p.add_argument('--reason', required=True)
    p.add_argument('--season', type=int, default=START_SEASON)
    p.add_argument('--tick', type=int, default=1)
    p.add_argument('--universe')
    p = sub.add_parser('revert', help='undo an active focus back to the one it superseded')
    p.add_argument('--focus-id', required=True)
    p.add_argument('--reason', required=True)
    for name in ('season', 'report', 'show', 'set', 'revert'):
        sub.choices[name].add_argument('--dsn')
    return ap


def _dump(value):
    return json.dumps(value, sort_keys=True, indent=2, default=str) + '\n'


def _as_of(value):
    try:
        s, t = value.split(':')
        return int(s), int(t)
    except (AttributeError, ValueError) as e:
        raise FocusRunError(EXIT_INVALID, f'--as-of {value!r} must be season:tick') from e


def _command(args):
    dsn = _dsn(args)
    conn = connect(dsn)
    try:
        with conn.cursor() as cur:
            if not tables_ready(cur):
                raise FocusRunError(EXIT_NOT_FOUND, 'persona_focus tables missing: apply 2027_02_03_persona_focus.sql')
            if args.command == 'season':
                universe = resolve_universe(cur, args.universe)
                output = _dump(run_season(cur, universe, args.season, dry_run=args.dry_run))
            elif args.command == 'report':
                universe = resolve_universe(cur, args.universe)
                text = render_report(stored_report(cur, universe, args.season))
                if args.out:
                    os.makedirs(os.path.dirname(args.out) or '.', exist_ok=True)
                    with open(args.out, 'w') as fh:
                        fh.write(text)
                    output = _dump({'out': args.out})
                else:
                    output = text
            elif args.command == 'show':
                universe = resolve_universe(cur, args.universe)
                as_of = _as_of(args.as_of) if args.as_of else None
                hist = fs.history(cur, universe, args.persona)
                if not hist:
                    raise FocusRunError(EXIT_NOT_FOUND, f'no focus rows for {args.persona} in {universe}')
                output = _dump({'focus': fs.read_focus(cur, universe, args.persona, as_of=as_of), 'history': hist})
            elif args.command == 'set':
                universe = resolve_universe(cur, args.universe)
                subjects = [parse_subject(cur, s) for s in args.subject]
                output = _dump(set_focus(cur, universe, args.persona, args.slot, text=args.text, subjects=subjects,
                                         reason=args.reason, season=args.season, tick=args.tick))
            else:
                output = _dump(revert(cur, args.focus_id, args.reason))
        if args.command == 'season' and args.dry_run or args.command in ('report', 'show'):
            conn.rollback()
        else:
            conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(output, end='')
    return EXIT_OK


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        return _command(args)
    except FocusRunError as e:
        print(f'error: {e}', file=sys.stderr)
        return e.code
    except fs.FocusConflict as e:
        print(f'conflict: {e}', file=sys.stderr)
        return EXIT_CONFLICT
    except fs.FocusStoreError as e:
        print(f'error: {e}', file=sys.stderr)
        return EXIT_INVALID


if __name__ == '__main__':
    sys.exit(main())
