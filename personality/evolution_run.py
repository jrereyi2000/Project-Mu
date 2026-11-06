"""Run a season of personality evolution, report it, and revert a change (PERSONALITY_EVOLUTION_PLAN.md §2.5).

  python -m personality.evolution_run season --season S [--dry-run] [--universe U] [--dsn DSN]
  python -m personality.evolution_run report --season S [--out PATH] [--universe U] [--dsn DSN]
  python -m personality.evolution_run revert --candidate ID --reason TEXT [--dsn DSN]

Every command reads DATABASE_URL or --dsn and refuses madden, madden_2036 and postgres (the watcher's own step runs
against the franchise database; by hand only a copy or a scratch database). One transaction, committed only on
success; `season --dry-run` writes nothing. The universe is the lifecycle universe registered for the database's
league unless --universe names one. Exit codes: 0 ok, 2 bad input, 3 tables not migrated, 4 conflict.

run_season(cur, universe_id, season, *, dry_run=False, external=()):
  1. read the season facts (personality/evolution_facts.py) and hash them with the external candidates; an APPLIED
     run with the same key and hash returns 'noop';
  2. build rule candidates, arbitrate with the external 'ready' ones (personality/evolution.py) and submit each
     chosen change through personality.store.change (core checks stay final: a refusal marks it 'blocked');
  3. record every candidate and the run (personality/evolution_store.py).
revert is a JR override back to the before-state (store.override, actor JR, `reverts` naming the receipt) and marks
the candidate 'reverted'; it refuses when the profile changed since (stale revision, plan R4). The evolution receipt
is never deleted; change_notes_for leaves reverted changes out of the prompt notes.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
import sys

from personality import core, evolution as evo, evolution_store as estore
from personality.evolution_rules import EVOLUTION_RULES_VERSION

EXIT_OK, EXIT_INVALID, EXIT_NOT_MIGRATED, EXIT_CONFLICT = 0, 2, 3, 4
REFUSED_DATABASES = ('madden', 'madden_2036', 'postgres')
REPORT_DIR = os.path.join('verify_output', 'personality_evolution')


class EvolutionRunError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _persona_store():
    from personality import store
    return store


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), default=str)


def input_hash(facts, external):
    """sha256 of the season's facts (without profile_view: the profiles are what the run changes, so a rerun
    after applying still matches) and the external candidates."""
    season = {pid: {k: v for k, v in f.items() if k != 'profile_view'} for pid, f in facts.items()}
    payload = {'rules_version': EVOLUTION_RULES_VERSION, 'facts': season, 'external': list(external)}
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def tables_ready(cur):
    cur.execute('''SELECT to_regclass('persona_evolution_runs') IS NOT NULL,
                          to_regclass('persona_evolution_candidates') IS NOT NULL''')
    row = cur.fetchone()
    return bool(row[0]) and bool(row[1])


def _identity(cur, universe_id, role, persona_id, facts):
    f = facts.get(persona_id)
    if f is not None:
        return core.Identity(universe_id, role, f['birth_key'])
    cur.execute('''SELECT birth_key FROM persona_core_profiles
                   WHERE universe_id = %s AND persona_type = %s AND persona_id = %s''', (universe_id, role, persona_id))
    row = cur.fetchone()
    return None if row is None else core.Identity(universe_id, role, row[0])


def _counts(rows, facts, *, dry_run):
    statuses = ('drawn_out', 'blocked', 'superseded', 'applied', 'chosen')
    counts = {'people': len(facts), 'candidates': len(rows)}
    counts.update({s: sum(1 for r in rows if r['status'] == s) for s in statuses})
    if dry_run:
        counts['would_apply'] = counts.pop('chosen')
        counts.pop('applied')
    else:
        counts.pop('chosen')
    by_rule = {}
    for r in rows:
        entry = by_rule.setdefault(r['rule_id'] if r['source'] == 'RULE' else r['source'], {'candidates': 0, 'changes': 0})
        entry['candidates'] += 1
        entry['changes'] += r['status'] in ('applied', 'chosen')
    counts['by_rule'] = {k: by_rule[k] for k in sorted(by_rule)}
    counts['missing_sources'] = sorted({m for f in facts.values() for m in (f.get('missing') or {})})
    return counts


def run_season(cur, universe_id, season, *, dry_run=False, external=(), facts=None, persona_store=None):
    """One season's evolution (see the module docstring). Returns the report dict."""
    from personality.evolution_facts import season_facts
    persona_store = persona_store or _persona_store()
    facts = season_facts(cur, universe_id, season) if facts is None else facts
    external = [deepcopy(e) for e in external]
    digest = input_hash(facts, external)
    base = dict(universe_id=universe_id, season=season, rules_version=EVOLUTION_RULES_VERSION, input_hash=digest)
    if not dry_run:
        previous = estore.read_run(cur, universe_id, season, EVOLUTION_RULES_VERSION)
        if previous and previous['input_hash'] == digest and previous['status'] == 'APPLIED':
            return dict(base, status='noop', counts=previous['counts'],
                        candidates=estore.read_candidates(cur, universe_id, season))
    candidates = [c for pid in sorted(facts) for c in evo.rule_candidates(facts[pid])]
    wanted = {(c['persona_type'], c['persona_id']) for c in candidates if c['status'] == 'eligible'}
    wanted |= {(e.get('persona_type'), e.get('persona_id')) for e in external
               if isinstance(e, dict) and e.get('status') == 'ready'}
    identities, profiles = {}, {}
    for role, pid in sorted(wanted, key=str):
        try:
            ident = _identity(cur, universe_id, role, pid, facts)
            profile = None if ident is None else persona_store.read(cur, ident)
        except core.PersonalityError:
            ident, profile = None, None
        if ident is not None and profile is not None:
            identities[pid], profiles[pid] = ident, profile
    for e in external:
        if isinstance(e, dict) and not e.get('universe_id'):
            e['universe_id'] = universe_id
    decided = evo.arbitrate(candidates, external, season=season, profiles=profiles)
    rows = []
    for pid in sorted(decided):
        chosen = decided[pid]['chosen']
        if chosen is not None and not dry_run:
            event = evo.change_event(chosen, profiles[pid], season)
            try:
                persona_store.change(cur, identities[pid], event)
            except core.PersonalityError as err:
                chosen['status'], chosen['reason'] = 'blocked', f'store refused: {err}'
            else:
                chosen['status'], chosen['core_event_id'] = 'applied', event['event_id']
        rows.extend(([chosen] if chosen is not None else []) + decided[pid]['others'])
    for r in rows:
        r.pop('priority', None)
        r.pop('evidence', None)
    counts = _counts(rows, facts, dry_run=dry_run)
    report = dict(base, status='dry_run' if dry_run else 'applied', counts=counts, candidates=rows)
    if dry_run:
        return report
    estore.record_candidates(cur, rows)
    estore.record_run(cur, dict(base, counts=counts, status='APPLIED', error=None))
    return report


def record_failure(cur, universe_id, season, error):
    """A FAILED run row, unless the season already has an APPLIED one (kept as it is)."""
    previous = estore.read_run(cur, universe_id, season, EVOLUTION_RULES_VERSION)
    if previous and previous['status'] == 'APPLIED':
        return
    estore.record_run(cur, dict(universe_id=universe_id, season=season, rules_version=EVOLUTION_RULES_VERSION,
                                input_hash='', counts={}, status='FAILED', error=str(error)[:2000]))


def stored_report(cur, universe_id, season):
    run = estore.read_run(cur, universe_id, season, EVOLUTION_RULES_VERSION)
    if run is None:
        raise EvolutionRunError(EXIT_INVALID, f'no evolution run recorded for {universe_id} season {season}')
    return dict(universe_id=universe_id, season=season, rules_version=run['rules_version'],
                input_hash=run['input_hash'], status=run['status'].lower(), counts=run['counts'],
                candidates=estore.read_candidates(cur, universe_id, season))


def _describe(change):
    if change.get('kind') == 'slider':
        return f"{change['slider']} {'+' if change['step'] > 0 else ''}{change['step']}"
    parts = []
    if change.get('remove'):
        parts.append(f"drop {change['remove']}")
    if change.get('add'):
        parts.append(f"add {change['add']}")
    return ', '.join(parts) if len(parts) < 2 else f"{change['remove']} -> {change['add']}"


def _cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def render_report(report):
    """Deterministic markdown: every applied (or, in a dry run, chosen) change with its cause, note and draw, then
    counts of drawn_out / blocked / superseded and the blocked reasons."""
    counts = report.get('counts') or {}
    rows = sorted(report.get('candidates') or [], key=lambda r: (r['persona_type'], r['persona_id'], r['rule_id']))
    changed = [r for r in rows if r['status'] in ('applied', 'chosen', 'reverted')]
    title = 'would change' if report['status'] == 'dry_run' else 'changed'
    out = [f"# Personality evolution, season {report['season']}", '',
           f"Universe `{report['universe_id']}`, rules `{report['rules_version']}`, run {report['status']}, "
           f"input `{(report.get('input_hash') or '')[:12]}`.", '',
           f"{len(changed)} {title}; {counts.get('drawn_out', 0)} drawn out, {counts.get('blocked', 0)} blocked, "
           f"{counts.get('superseded', 0)} superseded, of {counts.get('candidates', len(rows))} candidates for "
           f"{counts.get('people', 0)} people.", '']
    if counts.get('missing_sources'):
        out += ['Sources missing (their rules could not fire): ' + ', '.join(counts['missing_sources']) + '.', '']
    out += ['## Changes', '']
    if changed:
        out += ['| Person | Rule | Change | Note | Draw / chance | Cause | Status | Candidate |',
                '|---|---|---|---|---|---|---|---|']
        for r in changed:
            draw = '-' if r.get('draw') is None else f"{r['draw']:.4f} / {r['chance']:.2f}"
            out.append('| ' + ' | '.join(_cell(v) for v in (
                f"{r['persona_type']} `{r['persona_id']}`", r['rule_id'], _describe(r['change']), r.get('note') or '',
                draw, ', '.join(r.get('source_event_ids') or []), r['status'], r['candidate_id'])) + ' |')
    else:
        out.append('None.')
    out += ['', '## By rule', '', '| Rule | Candidates | Changes |', '|---|---|---|']
    for rule, entry in (counts.get('by_rule') or {}).items():
        out.append(f"| {rule} | {entry['candidates']} | {entry['changes']} |")
    blocked = [r for r in rows if r['status'] == 'blocked']
    if blocked:
        out += ['', '## Blocked', '']
        out += [f"- {r['persona_type']} `{r['persona_id']}` {r['rule_id']}: {r.get('reason')}" for r in blocked]
    return '\n'.join(out) + '\n'


def report_path(universe_id, season, directory=REPORT_DIR):
    safe = ''.join(ch if ch.isalnum() or ch in '-_' else '_' for ch in str(universe_id))
    return os.path.join(directory, f'evolution_{safe}_{season}.md')


def write_report(report, path):
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'w') as fh:
        fh.write(render_report(report))
    return path


def revert(cur, candidate_id, reason, *, persona_store=None):
    """Undo an applied change by a JR override back to its before-state (plan §2.5). Returns the override result."""
    persona_store = persona_store or _persona_store()
    core._text(reason, 'reason')
    cand = estore.read_candidate(cur, candidate_id)
    if cand is None:
        raise EvolutionRunError(EXIT_INVALID, f'no evolution candidate {candidate_id}')
    if cand['status'] != 'applied' or not cand['core_event_id']:
        raise EvolutionRunError(EXIT_CONFLICT, f"candidate {candidate_id} is {cand['status']}, not an applied change")
    ident = _identity(cur, cand['universe_id'], cand['persona_type'], cand['persona_id'], {})
    if ident is None:
        raise EvolutionRunError(EXIT_INVALID, f"no core profile for {cand['persona_type']} {cand['persona_id']}")
    cur.execute('''SELECT before_state, after_state FROM persona_core_events
                   WHERE persona_type = %s AND persona_id = %s AND event_id = %s''',
                (cand['persona_type'], cand['persona_id'], cand['core_event_id']))
    row = cur.fetchone()
    if row is None:
        raise EvolutionRunError(EXIT_CONFLICT, f"evolution receipt {cand['core_event_id']} is missing")
    before, after = row
    current = persona_store.read(cur, ident)
    if current is None:
        raise EvolutionRunError(EXIT_INVALID, 'the profile is gone')
    request = dict(override_id='revert:' + candidate_id, actor='JR',
                   reason=f'revert evolution {candidate_id}: {reason}', season=cand['season'],
                   tick=evo.EVOLUTION_TICK, expected_revision=after['revision'], reverts=cand['core_event_id'],
                   sliders={k: v for k, v in before['sliders'].items() if current['sliders'].get(k) != v},
                   add_traits=[t for t in before['traits'] if t not in current['traits']],
                   remove_traits=[t for t in current['traits'] if t not in before['traits']])
    result = persona_store.override(cur, ident, request)
    estore.mark_reverted(cur, candidate_id, request)
    return dict(candidate_id=candidate_id, override_id=request['override_id'], profile=result)


def change_notes_for(cur, identity, as_of, *, limit=evo.MAX_CHANGE_NOTES, persona_store=None):
    """evolution.change_notes over the person's history, leaving out changes JR reverted."""
    persona_store = persona_store or _persona_store()
    history = persona_store.history(cur, identity)
    reverted = {(r.get('request') or {}).get('reverts') for r in history if r.get('kind') == 'override'}
    kept = [r for r in history if not (r.get('kind') == 'evolution' and r.get('event_id') in reverted)]
    return evo.change_notes(kept, as_of=as_of, limit=limit)


# ------------------------------------------------------------------------------------------------------ CLI ---

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
        raise EvolutionRunError(EXIT_INVALID, 'no database: pass --dsn or set DATABASE_URL')
    name = database_name(dsn)
    if name is None or name in REFUSED_DATABASES:
        raise EvolutionRunError(EXIT_INVALID,
                                f'refusing database {name!r} (madden, madden_2036 and postgres are refused)')
    return dsn


def resolve_universe(cur, explicit=None):
    if explicit:
        return explicit
    from engines.relationship_producers import sources
    try:
        league = sources.league_id(cur)
    except Exception as e:  # noqa: BLE001
        raise EvolutionRunError(EXIT_INVALID, f'no league: {e}') from e
    universe = sources.universe_id(cur, league)
    if universe is None:
        raise EvolutionRunError(EXIT_INVALID, f'no lifecycle universe registered for league {league}')
    return universe


def _parser():
    ap = argparse.ArgumentParser(prog='python -m personality.evolution_run',
                                 description='Personality evolution: run a season, report it, revert a change.')
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('season', help="run a season's evolution (or preview it with --dry-run)")
    p.add_argument('--season', type=int, required=True)
    p.add_argument('--dry-run', action='store_true')
    p.add_argument('--universe')
    p.add_argument('--dsn')
    p = sub.add_parser('report', help="markdown report of a recorded season's run")
    p.add_argument('--season', type=int, required=True)
    p.add_argument('--out')
    p.add_argument('--universe')
    p.add_argument('--dsn')
    p = sub.add_parser('revert', help='undo an applied change by a JR override')
    p.add_argument('--candidate', required=True)
    p.add_argument('--reason', required=True)
    p.add_argument('--dsn')
    return ap


def _dump(value):
    return json.dumps(value, sort_keys=True, indent=2, default=str) + '\n'


def _command(args):
    dsn = _dsn(args)
    conn = connect(dsn)
    try:
        with conn.cursor() as cur:
            if not tables_ready(cur):
                raise EvolutionRunError(EXIT_NOT_MIGRATED,
                                        'persona_evolution_* tables missing: apply 2027_01_30_personality_evolution.sql')
            if args.command == 'season':
                universe = resolve_universe(cur, args.universe)
                result = run_season(cur, universe, args.season, dry_run=args.dry_run)
                output = _dump({k: v for k, v in result.items() if k != 'candidates'})
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
            else:
                result = revert(cur, args.candidate, args.reason)
                output = _dump({k: v for k, v in result.items() if k != 'profile'})
        if args.command == 'season' and args.dry_run:
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
    except EvolutionRunError as e:
        print(f'error: {e}', file=sys.stderr)
        return e.code
    except (core.ConflictError, estore.EvolutionStoreError) as e:
        print(f'conflict: {e}', file=sys.stderr)
        return EXIT_CONFLICT
    except core.PersonalityError as e:
        print(f'error: {e}', file=sys.stderr)
        return EXIT_INVALID


if __name__ == '__main__':
    sys.exit(main())
