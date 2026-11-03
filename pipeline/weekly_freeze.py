"""
pipeline/weekly_freeze.py: a paired freeze of the franchise database and the Madden save after every ingested week
or offseason stage (V2_MASTER_PLAN §7.2, V3_STUDIO_PLAN §3).

Why: several tables hold only the latest state (season_stats, team_seasons indices, player_seasons team/OVR/
popularity, contracts, cap rows). Instead of making them temporal, the watcher freezes the whole database once per
week, so a later media backfill can read the league exactly as it stood that week, and JR can roll the league
(database + save) back to any week with both halves in sync.

Layout (MU_FREEZE_DIR, default captures/freezes/):
  <root>/<db>/<label>/db.dump          pg_dump -Fc of the franchise database after the week's ingest (load, engines,
                                       league office and, when they ran, the week's articles)
  <root>/<db>/<label>/<CAREER-file>    the save as it stood at the same moment (the league office's output when it
                                       replaced the save)
  <root>/<db>/<label>/manifest.json    db, label, season, week, stage, save name, sha256 of both files, sizes, time
Labels: 2026_w05 (regular season / playoff week in games.week numbering), 2026_off_RESIGN, 2026_off_FREE_AGENCY_w2
(offseason stops: season = the year just played), 2026_pre (a load with no current week, e.g. preseason).
A second freeze of the same label (e.g. after a league office retry) replaces the first.

CLI:
  python3 -m pipeline.weekly_freeze list [--db DB]
  python3 -m pipeline.weekly_freeze restore --db madden_x --label 2030_w05 --saves-dir DIR --confirm madden_x
      Rolls the league back: freezes the current state first (label pre_restore_<time>), drops and recreates the
      franchise database from the freeze, and copies the frozen save back into DIR under its original name.
      Everything written to the database after the freeze (later weeks, and any articles or memories backfilled
      later) is gone afterwards; the pre_restore freeze keeps it. Refused while Madden27.exe runs.
  python3 -m pipeline.weekly_freeze restore --db madden_x --label 2030_w05 --to-db madden_snap
      Restores the database half only into a scratch database (the media backfill reads a week's football state
      from it; never the canonical database). The scratch name must start with madden_ and not be a franchise's.

pg_dump / pg_restore run locally when on PATH, else inside the Postgres container (`docker exec`, container
MU_DB_CONTAINER, default madden-db).
"""
import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ROOT = os.path.join(ROOT, "captures", "freezes")
DUMP_NAME = "db.dump"
MANIFEST = "manifest.json"
FREEZE_VERSION = 1


class FreezeError(Exception):
    pass


def freeze_root(root=None):
    return root or os.environ.get("MU_FREEZE_DIR") or DEFAULT_ROOT


def container():
    return os.environ.get("MU_DB_CONTAINER", "madden-db")


def label_for(season, week=None, stage=None):
    """'2026_w05' for a week, '2026_off_FREE_AGENCY_w2' for an offseason stop (stage: league_office_stage's dict),
    '2026_pre' for a load with neither."""
    if stage:
        wave = f"_w{stage['wave']}" if stage.get("wave") else ""
        return f"{stage.get('season', season)}_off_{stage['stage']}{wave}"
    if week is not None:
        return f"{season}_w{int(week):02d}"
    return f"{season}_pre"


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ----------------------------------------------------------------------------- pg_dump / pg_restore runners ----

def _run(runner, cmd, **kw):
    try:
        return runner(cmd, **kw)
    except FileNotFoundError as e:
        raise FreezeError(f"{cmd[0]} not found: install the PostgreSQL client tools (pg_dump / pg_restore) or "
                          f"make docker reach the {container()} container") from e


def dump_db(db, dsn, out_path, runner=subprocess.run):
    """pg_dump -Fc of `db` into out_path (local pg_dump with the DSN, else inside the container)."""
    if shutil.which("pg_dump"):
        p = _run(runner, ["pg_dump", "-Fc", "-d", dsn, "-f", out_path], capture_output=True)
    else:
        with open(out_path, "wb") as fh:
            p = _run(runner, ["docker", "exec", container(), "pg_dump", "-U", "postgres", "-Fc", "-d", db],
                     stdout=fh, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise FreezeError(f"pg_dump of {db} failed (exit {p.returncode}): {(p.stderr or b'').decode(errors='replace')[:500]}")


def restore_db(db, dump_path, runner=subprocess.run):
    """pg_restore of dump_path into the (empty) database `db`."""
    from db import franchises
    if shutil.which("pg_restore"):
        p = _run(runner, ["pg_restore", "--no-owner", "-d", franchises.dsn_for_db(db), dump_path],
                 capture_output=True)
    else:
        with open(dump_path, "rb") as fh:
            p = _run(runner, ["docker", "exec", "-i", container(), "pg_restore", "-U", "postgres", "--no-owner",
                              "-d", db], stdin=fh, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise FreezeError(f"pg_restore into {db} failed (exit {p.returncode}): "
                          f"{(p.stderr or b'').decode(errors='replace')[:500]}")


# ------------------------------------------------------------------------------------------------- freeze ----

def freeze(db, dsn, save_path, label, season=None, week=None, stage=None, extra=None, root=None, dumper=dump_db):
    """Writes <root>/<db>/<label>/ (database dump, save copy, manifest) atomically; replaces an earlier freeze of
    the same label. Returns the manifest."""
    if save_path and not os.path.isfile(save_path):
        raise FreezeError(f"save file {save_path} not found")
    base = os.path.join(freeze_root(root), db)
    final = os.path.join(base, label)
    tmp = final + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    os.makedirs(tmp)
    try:
        dump_path = os.path.join(tmp, DUMP_NAME)
        dumper(db, dsn, dump_path)
        manifest = {"version": FREEZE_VERSION, "db": db, "label": label, "season": season, "week": week,
                    "stage": stage, "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
                    "dump_sha256": file_sha256(dump_path), "dump_bytes": os.path.getsize(dump_path),
                    "save_name": None, "save_sha256": None, "save_bytes": None}
        if save_path:
            name = os.path.basename(save_path)
            shutil.copy2(save_path, os.path.join(tmp, name))
            manifest.update(save_name=name, save_sha256=file_sha256(os.path.join(tmp, name)),
                            save_bytes=os.path.getsize(save_path))
        manifest.update(extra or {})
        with open(os.path.join(tmp, MANIFEST), "w") as fh:
            json.dump(manifest, fh, indent=2, default=str)
        if os.path.exists(final):
            old = final + ".old"
            shutil.rmtree(old, ignore_errors=True)
            os.rename(final, old)
            os.rename(tmp, final)
            shutil.rmtree(old, ignore_errors=True)
        else:
            os.rename(tmp, final)
        return manifest
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        raise


def list_freezes(db=None, root=None):
    """Manifests under the root (one database or all), oldest first per database."""
    base = freeze_root(root)
    out = []
    if not os.path.isdir(base):
        return out
    for d in sorted(os.listdir(base)):
        if db and d != db:
            continue
        for label in sorted(os.listdir(os.path.join(base, d))):
            path = os.path.join(base, d, label, MANIFEST)
            if label.endswith((".tmp", ".old")) or not os.path.isfile(path):
                continue
            with open(path) as fh:
                out.append(json.load(fh))
    out.sort(key=lambda m: (m["db"], m.get("created_at") or ""))
    return out


def load_manifest(db, label, root=None):
    folder = os.path.join(freeze_root(root), db, label)
    path = os.path.join(folder, MANIFEST)
    if not os.path.isfile(path):
        raise FreezeError(f"no freeze {label} for {db} under {freeze_root(root)}")
    with open(path) as fh:
        return folder, json.load(fh)


def verify(folder, manifest):
    if file_sha256(os.path.join(folder, DUMP_NAME)) != manifest["dump_sha256"]:
        raise FreezeError(f"{folder}: db.dump doesn't match its manifest sha256")
    if manifest.get("save_name") and file_sha256(os.path.join(folder, manifest["save_name"])) != manifest["save_sha256"]:
        raise FreezeError(f"{folder}: {manifest['save_name']} doesn't match its manifest sha256")


# ------------------------------------------------------------------------------------------------ restore ----

def restore_scratch(db, label, to_db, root=None, restorer=restore_db):
    """The database half of a freeze into a scratch database (the media backfill's read-only source)."""
    from db import franchises
    reg = franchises.load_registry()
    taken = {e.get("db") for e in reg.get("franchises", {}).values()}
    if to_db == db or to_db in taken:
        raise FreezeError(f"{to_db} is a franchise database; --to-db must be a scratch database")
    folder, manifest = load_manifest(db, label, root)
    verify(folder, manifest)
    franchises.drop_db(to_db)            # refuses reserved and non-madden_ names
    franchises.create_db(to_db)
    restorer(to_db, os.path.join(folder, DUMP_NAME))
    return manifest


def restore_league(db, label, saves_dir, confirm, root=None, restorer=restore_db, dumper=dump_db,
                   madden_running=None, log_fn=print):
    """Rolls the franchise back to a freeze: database and save together. Freezes the current state first."""
    from db import franchises
    if confirm != db:
        raise FreezeError(f"--confirm must name the database being replaced ({db})")
    folder, manifest = load_manifest(db, label, root)
    verify(folder, manifest)
    if not manifest.get("save_name"):
        raise FreezeError(f"freeze {label} has no save copy; use --to-db for the database half")
    if madden_running is None:
        from pipeline.gpu_queue.process_check import is_madden_running as madden_running
    if madden_running():
        raise FreezeError("Madden27.exe is running: close it before restoring a save")
    if not os.path.isdir(saves_dir):
        raise FreezeError(f"saves folder {saves_dir} not found")
    target = os.path.join(saves_dir, manifest["save_name"])
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    dsn = franchises.dsn_for_db(db)
    pre = freeze(db, dsn, target if os.path.isfile(target) else None, f"pre_restore_{stamp}",
                 extra={"restoring": label}, root=root, dumper=dumper)
    log_fn(f"Current state frozen as {pre['label']} (undo with: restore --label {pre['label']})")
    franchises.drop_db(db)
    franchises.create_db(db)
    restorer(db, os.path.join(folder, DUMP_NAME))
    # copyfile (not copy2): a fresh mtime, so the watcher looks at it once and sees league_state's own week (no-op).
    shutil.copyfile(os.path.join(folder, manifest["save_name"]), target)
    log_fn(f"Restored {db} and {target} to {label} (season {manifest.get('season')}, week {manifest.get('week')})")
    return manifest


# ---------------------------------------------------------------------------------------------------- CLI ----

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--db")
    rs = sub.add_parser("restore")
    rs.add_argument("--db", required=True)
    rs.add_argument("--label", required=True)
    rs.add_argument("--saves-dir")
    rs.add_argument("--confirm")
    rs.add_argument("--to-db", help="Restore the database half only, into this scratch database")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "list":
            for m in list_freezes(args.db):
                mb = (m.get("dump_bytes") or 0) / 1e6
                print(f"{m['db']}  {m['label']:<28} season {m.get('season')} week {m.get('week')}  "
                      f"dump {mb:.1f} MB  save {m.get('save_name') or '-'}  {m.get('created_at')}")
            return 0
        if args.to_db:
            m = restore_scratch(args.db, args.label, args.to_db)
            print(f"Restored {args.db} {m['label']} into {args.to_db}")
            return 0
        from pipeline.notify import env
        saves_dir = args.saves_dir or env("MADDEN_SAVES_DIR", None)
        if not saves_dir:
            raise FreezeError("--saves-dir (or MADDEN_SAVES_DIR) is required to restore the save")
        restore_league(args.db, args.label, saves_dir, args.confirm)
        return 0
    except FreezeError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
