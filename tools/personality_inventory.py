"""Person lifecycle inventory CLI (docs/done/PERSONALITY_DRAFT_INTEGRATION_PLAN.md §2.6).

Offline (no database import or connection, no model calls; writes only --out):

  python -m tools.personality_inventory preview --snapshot save.json --universe universe.json
        --classifications classifications.json --cutoff cutoff.json [--staff staff.json] [--existing export.json]
        --out manifest.json
  python -m tools.personality_inventory validate --manifest manifest.json [--rehash --out manifest.json]
  python -m tools.personality_inventory compare --before manifest.json --after replay.json [--out diff.json]
  python -m tools.personality_inventory universe --snapshot save.json [--start-season 2026] --out universe.json

Database (an explicit --dsn is required: never DATABASE_URL or the active franchise; refuses madden_2036 and
postgres; personality/lifecycle_store.py is imported only here; --dry-run rolls back):

  python -m tools.personality_inventory export --universe ID --dsn DSN --out export.json
  python -m tools.personality_inventory apply --manifest PATH --review PATH --expect-hash HASH --dsn DSN [--dry-run]
  python -m tools.personality_inventory revert --batch ID --expect-after-hash HASH --review PATH --dsn DSN [--dry-run]

Exit codes: 0 valid/applied; 2 invalid input or version; 3 unresolved classification/identity (preview still
writes the reviewable manifest); 4 stale/conflict/schema/profile failure.
"""
import argparse
import json
import sys
from pathlib import Path

from personality import core, lifecycle as L

REFUSED_DATABASES = ('madden_2036', 'postgres', 'template0', 'template1')


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write(path, value):
    text = json.dumps(value, sort_keys=True, indent=1, ensure_ascii=False) + '\n'
    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding='utf-8')
    else:
        sys.stdout.write(text)


def _summary(manifest):
    return dict(manifest_sha256=manifest['manifest_sha256'], counts=manifest['counts'],
                issues=len(manifest['issues']), unresolved=len(L.unresolved(manifest)))


def cmd_preview(a):
    inventory = L.validate_inventory(_load(a.snapshot), _load(a.classifications), universe=_load(a.universe),
                                     cutoff=_load(a.cutoff), staff=_load(a.staff) if a.staff else None)
    catalogue = core.legacy.load_traits()
    manifest = L.plan_lifecycle(inventory, _load(a.existing) if a.existing else {}, core_version=core.CORE_VERSION,
                                catalogue_version=core.catalogue_version(catalogue), catalogue=catalogue)
    _write(a.out, manifest)
    print(json.dumps(_summary(manifest), sort_keys=True), file=sys.stderr)
    return L.EXIT_UNRESOLVED if L.unresolved(manifest) else L.EXIT_OK


def cmd_validate(a):
    manifest = _load(a.manifest)
    if a.rehash:
        manifest = L.finalize_manifest(manifest)
        if a.out:
            _write(a.out, manifest)
    result = L.validate_manifest(manifest)
    print(json.dumps(result, sort_keys=True, indent=1))
    return L.EXIT_OK if result['valid'] else L.EXIT_INVALID


def cmd_compare(a):
    diff = L.compare_manifests(_load(a.before), _load(a.after))
    _write(a.out, diff)
    return L.EXIT_OK if diff['same'] else L.EXIT_CONFLICT


def cmd_universe(a):
    snapshot = _load(a.snapshot)
    league = (snapshot.get('franchise') or {}).get('league_id')
    if league is None:
        raise L.LifecycleError('snapshot has no franchise.league_id (reader too old)')
    _write(a.out, L.new_universe(league, L.snapshot_sha256(snapshot), a.start_season))
    return L.EXIT_OK


def _connect(dsn):
    import psycopg                                   # lazy: offline commands never import a driver
    conn = psycopg.connect(dsn, autocommit=False)
    if conn.info.dbname in REFUSED_DATABASES:
        conn.close()
        raise L.LifecycleError(f'refusing database {conn.info.dbname!r}')
    return conn


def _db(a, work):
    from personality import lifecycle_store         # lazy: only database subcommands need the store
    conn = _connect(a.dsn)
    try:
        with conn.cursor() as cur:
            result = work(lifecycle_store, cur)
        if getattr(a, 'dry_run', False):
            conn.rollback()
        else:
            conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()
    return result


def cmd_export(a):
    _write(a.out, _db(a, lambda store, cur: store.export_state(cur, a.universe)))
    return L.EXIT_OK


def cmd_apply(a):
    manifest, review = _load(a.manifest), _load(a.review)
    receipt = _db(a, lambda store, cur: store.apply_lifecycle(cur, manifest, expected_hash=a.expect_hash,
                                                               review_receipt=review))
    print(json.dumps(dict(receipt, dry_run=a.dry_run), sort_keys=True, indent=1, default=str))
    return L.EXIT_OK


def cmd_revert(a):
    review = _load(a.review)
    receipt = _db(a, lambda store, cur: store.revert_batch(cur, a.batch, expected_after_hash=a.expect_after_hash,
                                                            review_receipt=review))
    print(json.dumps(dict(receipt, dry_run=a.dry_run), sort_keys=True, indent=1, default=str))
    return L.EXIT_OK


def parser():
    ap = argparse.ArgumentParser(prog='python -m tools.personality_inventory', description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('preview', help='offline inventory + manifest from a snapshot')
    for name in ('snapshot', 'universe', 'classifications', 'cutoff'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--staff')
    p.add_argument('--existing', help='export.json from the export command (default: nothing stored yet)')
    p.add_argument('--out', required=True)
    p.set_defaults(func=cmd_preview)
    p = sub.add_parser('validate', help='offline manifest check')
    p.add_argument('--manifest', required=True)
    p.add_argument('--rehash', action='store_true', help='recompute hashes after editing a proposed profile')
    p.add_argument('--out')
    p.set_defaults(func=cmd_validate)
    p = sub.add_parser('compare', help='offline replay comparison')
    p.add_argument('--before', required=True)
    p.add_argument('--after', required=True)
    p.add_argument('--out')
    p.set_defaults(func=cmd_compare)
    p = sub.add_parser('universe', help='offline universe registration record from the starting snapshot')
    p.add_argument('--snapshot', required=True)
    p.add_argument('--start-season', type=int, default=2026)
    p.add_argument('--out', required=True)
    p.set_defaults(func=cmd_universe)
    p = sub.add_parser('export', help='database: export a universe lifecycle state')
    p.add_argument('--universe', required=True)
    p.add_argument('--dsn', required=True)
    p.add_argument('--out', required=True)
    p.set_defaults(func=cmd_export)
    p = sub.add_parser('apply', help='database: apply a reviewed manifest')
    p.add_argument('--manifest', required=True)
    p.add_argument('--review', required=True)
    p.add_argument('--expect-hash', required=True)
    p.add_argument('--dsn', required=True)
    p.add_argument('--dry-run', action='store_true')
    p.set_defaults(func=cmd_apply)
    p = sub.add_parser('revert', help='database: revert an applied batch')
    p.add_argument('--batch', required=True)
    p.add_argument('--expect-after-hash', required=True)
    p.add_argument('--review', required=True)
    p.add_argument('--dsn', required=True)
    p.add_argument('--dry-run', action='store_true')
    p.set_defaults(func=cmd_revert)
    return ap


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        return args.func(args)
    except L.LifecycleError as e:
        print(f'personality_inventory: {e}', file=sys.stderr)
        return e.code
    except core.ConflictError as e:
        print(f'personality_inventory: conflict: {e}', file=sys.stderr)
        return L.EXIT_CONFLICT
    except (core.PersonalityError, ValueError, KeyError, OSError) as e:
        print(f'personality_inventory: invalid input: {e}', file=sys.stderr)
        return L.EXIT_INVALID


if __name__ == '__main__':
    sys.exit(main())
