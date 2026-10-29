"""
db/franchises.py: Registry of Madden franchises and their databases (docs/in_progress/PC_MIGRATION_PLAN.md §5.2).

Each Madden franchise gets its own Postgres database, `madden_<slug>`. A franchise is identified by
`Franchise.LeagueID` from the save (pc_save_reader.mjs puts it in the snapshot as `franchise.league_id`),
which Madden picks at random when the franchise is created and keeps across every save and week. All save slots
of one franchise (CAREER-WEEK1, an autosave, ...) carry the same id, so they map to the same database.

The registry is a small JSON file, data/franchises.json (gitignored, machine-local like the databases it
points at; override with MU_FRANCHISE_REGISTRY). Each franchise database also stamps its id in
`league_state.madden_league_id`, so a registry that points at the wrong database is caught before a load.

    {"active": "<league id>",
     "franchises": {"<league id>": {"db": "madden_colts", "name": "Colts 2034", "user_teams": ["IND"],
                                    "saves": ["CAREER-COLTS"], "created_at": "..."}}}

The active franchise is the one the site (server/api.py) and the db/*.sh scripts use when DATABASE_URL (MU_DB
for the scripts) is not set; an explicit setting always wins. Other CLIs (engines, newsroom) still read only
DATABASE_URL: run them with DATABASE_URL="$(python3 -m db.franchises dsn)".

  python3 -m db.franchises list              # franchises, their databases, the active one
  python3 -m db.franchises active [ID|DB]    # show, or set, the active franchise
  python3 -m db.franchises dsn               # DSN of the active franchise (for DATABASE_URL=...)
  python3 -m db.franchises dbname            # database name of the active franchise (for psql -d / pg_dump -d)

Only the standard library is imported at module level, so the CLI runs under any python3.
"""
import datetime
import fcntl
import json
import os
import re
import sys
from contextlib import contextmanager

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_REGISTRY = os.path.join(ROOT, "data", "franchises.json")
LEGACY_DSN = "host=localhost dbname=madden user=postgres password=postgres"
DB_PREFIX = "madden_"
# Databases the watcher never creates, seeds, drops or loads into.
RESERVED_DBS = {"madden", "madden_2036", "madden_test", "postgres", "template0", "template1"}


class FranchiseError(Exception):
    pass


def registry_path():
    return os.environ.get("MU_FRANCHISE_REGISTRY") or DEFAULT_REGISTRY


def load_registry(path=None):
    path = path or registry_path()
    if not os.path.exists(path):
        return {"active": None, "franchises": {}}
    with open(path) as fh:
        reg = json.load(fh)
    reg.setdefault("active", None)
    reg.setdefault("franchises", {})
    return reg


def save_registry(reg, path=None):
    """Atomic write: a reader never sees a half-written file."""
    path = path or registry_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w") as fh:
        json.dump(reg, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)


@contextmanager
def editing_registry(path=None):
    """Load, yield for changes, save; serialized with a lock file so two writers can't interleave."""
    path = path or registry_path()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(f"{path}.lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        reg = load_registry(path)
        yield reg
        save_registry(reg, path)


def franchise_key(league_id):
    """Registry key: the league id as a string ("file_<name>" for a snapshot without one, pipeline/watch_save.py)."""
    return str(league_id)


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "_", str(text).lower()).strip("_")[:40]


def _id_slug(league_id):
    key = franchise_key(league_id)
    return f"l{int(key) & 0xFFFFFFFF:08x}" if key.lstrip("-").isdigit() else slugify(key)


def db_name_for(league_id, name=None, taken=()):
    """`madden_<name slug>` when the franchise has a name, else `madden_l<league id as 8 hex digits>`."""
    db = DB_PREFIX + ((slugify(name) if name else "") or _id_slug(league_id))
    candidate, n = db, 2
    while candidate in RESERVED_DBS or candidate in taken:
        candidate, n = f"{db}_{n}", n + 1
    return candidate


def find(reg, ref):
    """A franchise by league id, database name or name; returns (key, entry) or (None, None)."""
    if ref is None:
        return None, None
    ref = str(ref)
    if ref in reg["franchises"]:
        return ref, reg["franchises"][ref]
    for key, entry in reg["franchises"].items():
        if ref in (entry.get("db"), entry.get("name")):
            return key, entry
    return None, None


def register(reg, league_id, name=None, user_teams=None, save_name=None):
    """Adds a franchise (no database work); the first one registered becomes active."""
    key = franchise_key(league_id)
    if key in reg["franchises"]:
        raise FranchiseError(f"franchise {key} is already registered")
    taken = {e["db"] for e in reg["franchises"].values()}
    reg["franchises"][key] = {
        "db": db_name_for(league_id, name, taken),
        "name": name,
        "user_teams": list(user_teams or []),
        "saves": [save_name] if save_name else [],
        "created_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    if not reg.get("active"):
        reg["active"] = key
    return reg["franchises"][key]


def note_save(entry, save_name):
    if save_name and save_name not in entry.setdefault("saves", []):
        entry["saves"].append(save_name)


# --- DSNs -------------------------------------------------------------------------------------------------

def _conninfo():
    from psycopg.conninfo import conninfo_to_dict, make_conninfo
    return conninfo_to_dict, make_conninfo


def base_dsn():
    """Host/user/password to reach Postgres: DATABASE_URL's, else the local Docker defaults."""
    return os.environ.get("DATABASE_URL") or LEGACY_DSN


def dsn_for_db(db, base=None):
    """`base` with its dbname swapped for `db` (plain key=value DSNs need no psycopg)."""
    base = base or base_dsn()
    if "://" not in base:
        parts = [p for p in base.split() if not p.startswith("dbname=")]
        return " ".join([f"dbname={db}"] + parts) if parts else f"dbname={db}"
    conninfo_to_dict, make_conninfo = _conninfo()
    d = conninfo_to_dict(base)
    d["dbname"] = db
    return make_conninfo(**d)


def active_entry(reg=None):
    reg = reg or load_registry()
    return find(reg, reg.get("active"))


def active_dsn(reg=None):
    """DSN of the active franchise, or None when no franchise is registered as active."""
    _, entry = active_entry(reg)
    if not entry:
        return None
    return dsn_for_db(entry["db"])


def export_active_dsn():
    """Sets DATABASE_URL to the active franchise when it isn't already set. Call it before importing modules
    that read DATABASE_URL at import time (server/api.py does this first thing)."""
    if not os.environ.get("DATABASE_URL"):
        try:
            dsn = active_dsn()
        except (OSError, ValueError):
            dsn = None
        if dsn:
            os.environ["DATABASE_URL"] = dsn
    return os.environ.get("DATABASE_URL")


# --- Database lifecycle -----------------------------------------------------------------------------------

def _admin_connect():
    import psycopg
    return psycopg.connect(dsn_for_db("postgres"), autocommit=True)


def db_exists(db):
    with _admin_connect() as conn:
        return conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (db,)).fetchone() is not None


def _check_writable(db):
    if db in RESERVED_DBS or not db.startswith(DB_PREFIX):
        raise FranchiseError(f"refusing to create or drop reserved database {db!r}")


def create_db(db):
    from psycopg import sql
    _check_writable(db)
    with _admin_connect() as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(db)))


def drop_db(db):
    from psycopg import sql
    _check_writable(db)
    with _admin_connect() as conn:
        conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(db)))


def db_state(dsn):
    """'empty' (no schema, or schema with no league_state row and no players), or 'seeded'."""
    import psycopg
    with psycopg.connect(dsn) as conn:
        if conn.execute("SELECT to_regclass('public.league_state')").fetchone()[0] is None:
            return "empty"
        has_state = conn.execute("SELECT 1 FROM league_state LIMIT 1").fetchone() is not None
        has_players = (conn.execute("SELECT to_regclass('public.players')").fetchone()[0] is not None
                       and conn.execute("SELECT 1 FROM players LIMIT 1").fetchone() is not None)
        return "seeded" if (has_state or has_players) else "empty"


def stamped_league_id(dsn):
    """league_state.madden_league_id of a franchise database (None if unset or the column is missing)."""
    import psycopg
    with psycopg.connect(dsn) as conn:
        has_col = conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_name = 'league_state' AND column_name = 'madden_league_id'"
        ).fetchone()
        if not has_col:
            return None
        row = conn.execute("SELECT madden_league_id FROM league_state WHERE id = 1").fetchone()
        return int(row[0]) if row and row[0] is not None else None


def stamp_league_id(dsn, league_id):
    import psycopg
    with psycopg.connect(dsn) as conn:
        conn.execute("ALTER TABLE league_state ADD COLUMN IF NOT EXISTS madden_league_id BIGINT")
        conn.execute("UPDATE league_state SET madden_league_id = %s WHERE id = 1", (int(league_id),))


# --- CLI --------------------------------------------------------------------------------------------------

def _print_list(reg):
    if not reg["franchises"]:
        print(f"No franchises registered ({registry_path()}).")
        return
    for key, e in sorted(reg["franchises"].items(), key=lambda kv: kv[1].get("created_at") or ""):
        mark = "*" if key == reg.get("active") else " "
        print(f"{mark} {e['db']:<28} league_id={key:<12} name={e.get('name') or '-'} "
              f"user_teams={','.join(e.get('user_teams') or []) or '-'} saves={','.join(e.get('saves') or []) or '-'}")


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Madden franchise registry (db/franchises.py)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    a = sub.add_parser("active")
    a.add_argument("ref", nargs="?", help="League id, database name or franchise name to make active")
    sub.add_parser("dsn")
    sub.add_parser("dbname")
    args = ap.parse_args(argv)

    if args.cmd == "list":
        _print_list(load_registry())
    elif args.cmd == "active":
        if args.ref:
            with editing_registry() as reg:
                key, entry = find(reg, args.ref)
                if not entry:
                    print(f"No franchise matches {args.ref!r}.", file=sys.stderr)
                    return 1
                reg["active"] = key
            print(f"Active franchise: {entry['db']} (league_id {key}). Restart the server to pick it up.")
        else:
            key, entry = active_entry()
            if not entry:
                print("No active franchise.", file=sys.stderr)
                return 1
            print(f"{entry['db']} (league_id {key}, name {entry.get('name') or '-'})")
    elif args.cmd in ("dsn", "dbname"):
        _, entry = active_entry()
        if not entry:
            print("No active franchise (python3 -m db.franchises list). Set MU_DB or DATABASE_URL instead.",
                  file=sys.stderr)
            return 1
        print(entry["db"] if args.cmd == "dbname" else dsn_for_db(entry["db"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
