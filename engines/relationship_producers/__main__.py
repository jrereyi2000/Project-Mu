"""
Relationship event producers CLI (RELATIONSHIP_EVENT_PRODUCERS_PLAN.md §2.6).

  python -m engines.relationship_producers run --season S --tick T [--dsn DSN]
  python -m engines.relationship_producers catch-up --through S:T [--dry-run] [--dsn DSN]
  python -m engines.relationship_producers flush [--dsn DSN]
  python -m engines.relationship_producers preview --fixture PATH --out PATH

run, catch-up and flush read DATABASE_URL or --dsn, refuse madden, madden_2036 and postgres (the watcher's own
step runs against the live franchise; by hand only a copy or a scratch database), use one transaction and commit
it only on success (catch-up --dry-run rolls back). preview is fully offline: a fixture JSON
({"ticks": [sources.read_tick-shaped inputs]}) in, deterministic envelopes and per-rule counts out, no database
import. Exit codes: 0 ok, 2 bad input, 3 store not migrated, 4 conflict.
"""
import argparse
import json
import os
import sys

EXIT_OK, EXIT_INVALID, EXIT_NOT_MIGRATED, EXIT_CONFLICT = 0, 2, 3, 4
REFUSED_DATABASES = ('madden', 'madden_2036', 'postgres')


class CliError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


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
        raise CliError(EXIT_INVALID, 'no database: pass --dsn or set DATABASE_URL')
    name = database_name(dsn)
    if name is None or name in REFUSED_DATABASES:
        raise CliError(EXIT_INVALID, f'refusing database {name!r} (madden, madden_2036 and postgres are refused)')
    return dsn


def _point(text):
    season, sep, tick = str(text).partition(':')
    if not sep:
        raise argparse.ArgumentTypeError('expected SEASON:TICK')
    try:
        return int(season), int(tick)
    except ValueError as e:
        raise argparse.ArgumentTypeError('expected SEASON:TICK') from e


def _parser():
    ap = argparse.ArgumentParser(prog='python -m engines.relationship_producers', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('run', help='produce one tick from the database')
    p.add_argument('--season', type=int, required=True)
    p.add_argument('--tick', type=int, required=True)
    p.add_argument('--dsn')
    p = sub.add_parser('catch-up', help='one-time CATCHUP replay from the first save (plan D3)')
    p.add_argument('--through', type=_point, required=True, help='SEASON:TICK, inclusive')
    p.add_argument('--dry-run', action='store_true', help='report counts and roll everything back')
    p.add_argument('--dsn')
    p = sub.add_parser('flush', help='append held events whose subject now has an identity')
    p.add_argument('--dsn')
    p = sub.add_parser('preview', help='offline: fixture JSON in, envelopes and counts out')
    p.add_argument('--fixture', required=True)
    p.add_argument('--out', required=True)
    return ap


def _dump(value):
    return json.dumps(value, sort_keys=True, indent=2, default=str) + '\n'


def _preview(args):
    from engines.relationship_producers import run as runner
    try:
        with open(args.fixture) as fh:
            fixture = json.load(fh)
    except (OSError, ValueError) as e:
        raise CliError(EXIT_INVALID, f'cannot read fixture {args.fixture}: {e}') from e
    try:
        result = runner.preview(fixture)
    except runner.ProducerError as e:
        raise CliError(EXIT_INVALID, str(e)) from e
    with open(args.out, 'w') as fh:
        fh.write(_dump(result))
    print(_dump({'out': args.out, 'counts': result['counts'], 'ticks': len(result['ticks'])}), end='')
    return EXIT_OK


def _run_db(args):
    from engines.relationship_producers import run as runner
    dsn = _dsn(args)
    conn = connect(dsn)
    try:
        with conn.cursor() as cur:
            if args.command == 'run':
                result = runner.run_tick(cur, args.season, args.tick, source='SAVE')
            elif args.command == 'catch-up':
                result = runner.catch_up(cur, through=args.through, dry_run=args.dry_run)
            else:
                result = runner.flush(cur)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    print(_dump({k: v for k, v in result.items() if k != 'event_hashes'}), end='')
    return EXIT_OK


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        if args.command == 'preview':
            return _preview(args)
        return _run_db(args)
    except CliError as e:
        print(f'error: {e}', file=sys.stderr)
        return e.code
    except Exception as e:  # noqa: BLE001 - map the producer's errors to exit codes
        from engines.relationship_producers import run as runner
        if isinstance(e, runner.ProducerError):
            print(f'error: {e}', file=sys.stderr)
            return e.code
        try:
            from personality.relationship_store import RelationshipConflictError, StaleHeadError
        except ImportError:  # pragma: no cover - the relationships store is on main
            raise e
        if isinstance(e, (RelationshipConflictError, StaleHeadError)):
            print(f'conflict: {e}', file=sys.stderr)
            return EXIT_CONFLICT
        raise


if __name__ == '__main__':
    sys.exit(main())
