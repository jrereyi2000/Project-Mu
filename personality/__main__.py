"""Personality core CLI (docs/done/PERSONALITY_CORE_EXTENSIONS_PLAN.md §2.4).

  python -m personality [preview] --universe U --role R --birth-key K [--origin O] --season S [--out PATH]
  python -m personality override (--birth-key K | --alias NS:ID | --legacy-id ID) --universe U --role R
                        --season S --tick T [--set SLIDER=N ...] [--add-trait T ...] [--remove-trait T ...]
                        --reason TEXT [--override-id ID] [--dry-run] [--dsn DSN]
  python -m personality adopt --universe U (--writers | --gms | --role R --legacy-id ID) --season S --reason TEXT
                        [--dry-run] [--dsn DSN]
  python -m personality link --universe U --from ROLE:KEY --to ROLE:KEY --season S [--tick T] --reason TEXT
                        [--dry-run] [--dsn DSN]
  python -m personality history|as-of --universe U --role R (--birth-key K | --alias NS:ID | --legacy-id ID)
                        [--season S --tick T] [--dsn DSN]

preview is offline (no database). The other commands read DATABASE_URL or --dsn, refuse madden_2036 and
postgres, print the receipt as JSON, and commit unless --dry-run (which rolls back). history and as-of never
write. Exit codes: 0 ok, 2 invalid request, 3 not found, 4 conflict (stale revision, existing profile).
"""
import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from personality.core import (ConflictError, Identity, NotFoundError, PersonalityError, ROLES, initialize)

COMMANDS = ('preview', 'override', 'adopt', 'link', 'history', 'as-of')
REFUSED_DATABASES = ('madden_2036', 'postgres')
ADOPT_ORIGIN = {'writer': 'real', 'gm': 'generated'}   # plan Q2: writers hand-written, GMs engine-seeded
EXIT_OK, EXIT_INVALID, EXIT_NOT_FOUND, EXIT_CONFLICT = 0, 2, 3, 4
ROLE_CHOICES = ('player', 'coach', 'gm', 'owner', 'scout', 'writer')


class CliError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def _preview_parser(prog=None):
    parser = argparse.ArgumentParser(prog=prog, description='Pure offline profile preview. No database or save-file access.')
    parser.add_argument('--universe', required=True)
    parser.add_argument('--role', choices=ROLE_CHOICES, required=True)
    parser.add_argument('--birth-key', required=True)
    parser.add_argument('--origin', choices=('generated', 'real', 'unknown'), default='generated')
    parser.add_argument('--season', type=int, required=True)
    parser.add_argument('--out', type=Path)
    return parser


def _preview(args):
    p = initialize(Identity(args.universe, args.role, args.birth_key), args.origin, args.season)
    text = json.dumps(p, sort_keys=True, indent=2) + '\n'
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    else:
        print(text, end='')
    return EXIT_OK


def _db_args(parser, *, write=True):
    parser.add_argument('--dsn', help='connection string (default: DATABASE_URL)')
    if write:
        parser.add_argument('--dry-run', action='store_true', help='do everything, then roll back')


def _who_args(parser):
    parser.add_argument('--universe', required=True)
    parser.add_argument('--role', choices=ROLE_CHOICES, required=True)
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument('--birth-key')
    who.add_argument('--alias', help='NS:ID, a verified alias bound to the identity')
    who.add_argument('--legacy-id', help='an adopted legacy persona ID (writer key, gm_profiles.id)')


def _parser():
    parser = argparse.ArgumentParser(prog='python -m personality', description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', metavar='{' + ','.join(COMMANDS) + '}')
    sub.add_parser('preview', parents=[_preview_parser()], add_help=False,
                   help='offline profile preview (the default)')

    o = sub.add_parser('override', help="JR's audited override after first use")
    _who_args(o)
    o.add_argument('--season', type=int, required=True)
    o.add_argument('--tick', type=int, required=True)
    o.add_argument('--set', action='append', default=[], metavar='SLIDER=N')
    o.add_argument('--add-trait', action='append', default=[])
    o.add_argument('--remove-trait', action='append', default=[])
    o.add_argument('--reason', required=True)
    o.add_argument('--override-id', help='replay key (default: a new cli-<uuid>)')
    _db_args(o)

    a = sub.add_parser('adopt', help='bring legacy writer/GM personas under the core, in place')
    a.add_argument('--universe', required=True)
    which = a.add_mutually_exclusive_group(required=True)
    which.add_argument('--writers', action='store_true')
    which.add_argument('--gms', action='store_true')
    which.add_argument('--legacy-id')
    a.add_argument('--role', choices=ROLE_CHOICES)
    a.add_argument('--origin', choices=('generated', 'real'), help='default: real for writers, generated for GMs')
    a.add_argument('--season', type=int, required=True)
    a.add_argument('--reason', required=True)
    _db_args(a)

    ln = sub.add_parser('link', help='carry a personality over to a new role (D3)')
    ln.add_argument('--universe', required=True)
    ln.add_argument('--from', dest='from_', required=True, metavar='ROLE:KEY')
    ln.add_argument('--to', required=True, metavar='ROLE:KEY')
    ln.add_argument('--season', type=int, required=True)
    ln.add_argument('--tick', type=int)
    ln.add_argument('--reason', required=True)
    _db_args(ln)

    for name, text in (('history', 'receipts in order, read across role links'),
                       ('as-of', 'the profile as it was at a season and tick')):
        h = sub.add_parser(name, help=text)
        _who_args(h)
        h.add_argument('--season', type=int, required=(name == 'as-of'))
        h.add_argument('--tick', type=int)
        _db_args(h, write=False)
    return parser


def connect(dsn):
    """Open a non-autocommit connection. Tests replace this with a fake."""
    import psycopg
    return psycopg.connect(dsn)


def _dsn(args):
    dsn = args.dsn or os.environ.get('DATABASE_URL')
    if not dsn:
        raise CliError(EXIT_INVALID, 'no database: pass --dsn or set DATABASE_URL')
    try:
        from psycopg.conninfo import conninfo_to_dict
        name = conninfo_to_dict(dsn).get('dbname')
    except Exception:
        name = None
    if name in REFUSED_DATABASES:
        raise CliError(EXIT_INVALID, f'refusing database {name}')
    return dsn


def _identity(cur, args, store):
    if args.birth_key:
        return Identity(args.universe, args.role, args.birth_key)
    if args.legacy_id:
        return Identity.adopted(args.universe, args.role, args.legacy_id)
    ns, sep, ext = args.alias.partition(':')
    if not sep or not ns or not ext:
        raise CliError(EXIT_INVALID, '--alias must be NS:ID')
    found = store.resolve_alias(cur, args.universe, args.role, ns, ext)
    if found is None:
        raise CliError(EXIT_NOT_FOUND, f'no identity for alias {args.alias}')
    return found


def _role_key(text, universe):
    role, sep, key = text.partition(':')
    if not sep or role not in ROLES or not key:
        raise CliError(EXIT_INVALID, f'expected ROLE:KEY, got {text!r}')
    return Identity(universe, role, key)


def _sliders(pairs):
    out = {}
    for pair in pairs:
        name, sep, value = pair.partition('=')
        try:
            out[name] = int(value)
        except ValueError:
            raise CliError(EXIT_INVALID, f'--set expects SLIDER=N, got {pair!r}')
        if not sep:
            raise CliError(EXIT_INVALID, f'--set expects SLIDER=N, got {pair!r}')
    return out


def _override(cur, args, store):
    identity = _identity(cur, args, store)
    current = store.read(cur, identity)
    if current is None:
        raise CliError(EXIT_NOT_FOUND, 'no stored profile for that identity')
    request = dict(override_id=args.override_id or 'cli-' + uuid.uuid4().hex, actor='JR', reason=args.reason,
                   season=args.season, tick=args.tick, expected_revision=current['revision'])
    if args.set:
        request['sliders'] = _sliders(args.set)
    if args.add_trait:
        request['add_traits'] = list(args.add_trait)
    if args.remove_trait:
        request['remove_traits'] = list(args.remove_trait)
    after = store.override(cur, identity, request)
    return dict(kind='override', persona_type=identity.role, persona_id=identity.persona_id,
                request=request, before=current, after=after)


def _adopt(cur, args, store):
    if args.legacy_id:
        if not args.role:
            raise CliError(EXIT_INVALID, '--legacy-id needs --role')
        targets = [(args.role, args.legacy_id)]
    else:
        role = 'writer' if args.writers else 'gm'
        cur.execute('SELECT persona_id FROM persona_sliders WHERE persona_type=%s ORDER BY persona_id', (role,))
        targets = [(role, r[0]) for r in cur.fetchall()]
    receipts = []
    for role, legacy_id in targets:
        origin = args.origin or ADOPT_ORIGIN.get(role)
        if origin is None:
            raise CliError(EXIT_INVALID, f'--origin is required to adopt a {role}')
        profile = store.adopt_legacy(cur, args.universe, role, legacy_id, origin, args.season,
                                     reviewer='JR', reason=args.reason)
        receipts.append(dict(kind='adoption', persona_type=role, persona_id=legacy_id, origin=origin,
                             revision=profile['revision'], sliders=profile['sliders'], traits=profile['traits']))
    return dict(kind='adoption', count=len(receipts), adopted=receipts)


def _link(cur, args, store):
    source, target = _role_key(args.from_, args.universe), _role_key(args.to, args.universe)
    carried = store.link_role_change(cur, source, target, args.season, tick=args.tick, reason=args.reason)
    return dict(kind='role_link', from_=dict(role=source.role, persona_id=source.persona_id),
                to=dict(role=target.role, persona_id=target.persona_id), profile=carried)


def _history(cur, args, store):
    identity = _identity(cur, args, store)
    return dict(persona_type=identity.role, persona_id=identity.persona_id, receipts=store.history(cur, identity))


def _as_of(cur, args, store):
    identity = _identity(cur, args, store)
    profile = store.read_as_of(cur, identity, args.season, args.tick)
    if profile is None:
        raise CliError(EXIT_NOT_FOUND, 'no profile at that point')
    return dict(persona_type=identity.role, persona_id=identity.persona_id, season=args.season,
                tick=args.tick, profile=profile)


HANDLERS = {'override': _override, 'adopt': _adopt, 'link': _link, 'history': _history, 'as-of': _as_of}
READ_ONLY = ('history', 'as-of')


def _run_db(args):
    from personality import store
    dsn = _dsn(args)
    conn = connect(dsn)
    try:
        name = getattr(getattr(conn, 'info', None), 'dbname', None)
        if name in REFUSED_DATABASES:
            raise CliError(EXIT_INVALID, f'refusing database {name}')
        cur = conn.cursor()
        result = HANDLERS[args.command](cur, args, store)
        dry = args.command in READ_ONLY or args.dry_run
        if dry:
            conn.rollback()
        else:
            conn.commit()
        if args.command not in READ_ONLY:
            result['dry_run'] = bool(args.dry_run)
        print(json.dumps(result, sort_keys=True, indent=2, default=str))
        return EXIT_OK
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in COMMANDS and argv[0] not in ('-h', '--help'):
        # No subcommand: today's preview, byte for byte.
        return _preview(_preview_parser().parse_args(argv))
    args = _parser().parse_args(argv)
    if args.command == 'preview':
        return _preview(args)
    try:
        return _run_db(args)
    except CliError as e:
        print(f'error: {e}', file=sys.stderr)
        return e.code
    except NotFoundError as e:
        print(f'not found: {e}', file=sys.stderr)
        return EXIT_NOT_FOUND
    except ConflictError as e:
        print(f'conflict: {e}', file=sys.stderr)
        return EXIT_CONFLICT
    except PersonalityError as e:
        print(f'invalid: {e}', file=sys.stderr)
        return EXIT_INVALID


if __name__ == '__main__':
    sys.exit(main())
