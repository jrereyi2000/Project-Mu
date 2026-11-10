"""The guards every offseason_check command passes before it touches a database or a save.

Pure checks first (no connection, no file open), so the tests can drive them with fake strings:

* `check_scratch_dsn(dsn)` parses the DSN with psycopg's `conninfo_to_dict` and refuses anything whose dbname is not
  `mu_scratch_*`, and the named live databases outright (madden, madden_test, madden_mu2026, madden_2036, any other
  `madden_*`). `assert_connected_scratch(conn)` repeats the check on `current_database()` after connecting, so a
  service file, a `PGDATABASE` default or a URI the parser read differently can't slip through.
* `check_save_path(path)` refuses the pristine CAREER-MUBASE copy under `data/baselines` (it is the pin's backup) and
  anything that isn't a plain file. Reading a save in the Madden saves folder is allowed (that is where the pinned
  CAREER-MUBASE lives); `check_writable_path(path)` refuses every write target inside that folder or under
  `data/baselines`, so the tool can only ever write its own temp copies.
* `sha256_file(path)` and `ShaGuard` prove the original save is byte-identical after the run.

Nothing here launches Madden or opens a save for writing.
"""
import hashlib
import os
import re

SCRATCH_PREFIX = "mu_scratch_"
# Hard-refused by name, whatever else is said: the live and frozen franchises, and the shared test database.
REFUSED_DBS = frozenset({"madden", "madden_test", "madden_mu2026", "madden_2036", "postgres", "template0",
                         "template1"})
# The Madden saves folder as WSL sees it (C:\Users\<u>\Documents\Madden NFL 27\saves); matched case-insensitively.
_SAVES_DIR_RE = re.compile(r"/madden nfl 27/saves(/|$)", re.IGNORECASE)
_BASELINES_RE = re.compile(r"/data/baselines(/|$)")


class SafetyError(RuntimeError):
    """A guard refused: the command stops before touching anything."""


def dsn_dbname(dsn):
    """The dbname a libpq DSN or URI names, parsed by psycopg exactly as a connect would; '' when it names none."""
    from psycopg.conninfo import conninfo_to_dict
    try:
        parts = conninfo_to_dict(dsn or "")
    except Exception as e:      # noqa: BLE001 - an unparseable DSN is refused, not guessed at
        raise SafetyError(f"cannot parse the DSN: {e}") from e
    return str(parts.get("dbname") or "").strip()


def check_dbname(name):
    """Raises SafetyError unless `name` is a `mu_scratch_*` database and not one of the refused names."""
    if not name:
        raise SafetyError("the DSN names no database (dbname); refusing to fall back to a default")
    low = name.lower()
    if low in REFUSED_DBS:
        raise SafetyError(f"refusing database {name!r}: it is a live, frozen or shared database")
    if low.startswith("madden"):
        raise SafetyError(f"refusing database {name!r}: every madden_* database is a franchise or test database")
    if not name.startswith(SCRATCH_PREFIX):
        raise SafetyError(f"refusing database {name!r}: offseason_check only runs on {SCRATCH_PREFIX}* databases")
    return name


def check_scratch_dsn(dsn):
    """The DSN's dbname when it is a scratch database; SafetyError otherwise. No connection is opened."""
    return check_dbname(dsn_dbname(dsn))


def assert_connected_scratch(conn, expected=None):
    """After connecting: the server's own current_database() must pass the same check (and equal `expected`)."""
    with conn.cursor() as cur:
        cur.execute("SELECT current_database()")
        actual = cur.fetchone()[0]
    check_dbname(actual)
    if expected is not None and actual != expected:
        raise SafetyError(f"connected to {actual!r}, not the requested {expected!r}")
    return actual


def connect_scratch(dsn, **kwargs):
    """psycopg.connect to a scratch database, checked before and after connecting."""
    import psycopg
    name = check_scratch_dsn(dsn)
    conn = psycopg.connect(dsn, **kwargs)
    try:
        assert_connected_scratch(conn, name)
    except Exception:
        conn.close()
        raise
    return conn


def _real(path):
    return os.path.realpath(os.path.abspath(os.path.expanduser(str(path))))


def in_saves_folder(path):
    return bool(_SAVES_DIR_RE.search(_real(path).replace("\\", "/")))


def in_baselines(path):
    return bool(_BASELINES_RE.search(_real(path)))


def check_save_path(path):
    """The resolved save path when it may be READ and copied; SafetyError for the pristine pin copy or a non-file."""
    real = _real(path)
    if in_baselines(real) or os.path.basename(real).endswith(".pristine"):
        raise SafetyError(f"refusing {path}: the pristine baseline under data/baselines is never used as a source; "
                          "pass the save it pins instead")
    if not os.path.isfile(real):
        raise SafetyError(f"{path} is not a file")
    return real


def check_writable_path(path):
    """The resolved path when the tool may write there; SafetyError inside the Madden saves folder or baselines."""
    real = _real(path)
    if in_saves_folder(real):
        raise SafetyError(f"refusing to write {path}: it is inside the Madden saves folder")
    if in_baselines(real):
        raise SafetyError(f"refusing to write {path}: it is under data/baselines")
    return real


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as fh:            # read-only
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def pinned_sha(path, pin_dirs):
    """The sha256 a `data/baselines/*.sha256` pin records for `path` (by resolved path or basename), or None."""
    real = _real(path)
    for d in pin_dirs:
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".sha256"):
                continue
            with open(os.path.join(d, name)) as fh:
                for line in fh:
                    parts = line.strip().split(None, 1)
                    if len(parts) != 2:
                        continue
                    digest, target = parts
                    if _real(target) == real:
                        return digest
    return None


class ShaGuard:
    """Records the original save's sha256 and proves it unchanged: `verify()` raises SafetyError on any change."""

    def __init__(self, path):
        self.path = check_save_path(path)
        self.before = sha256_file(self.path)
        self.after = None

    def verify(self):
        self.after = sha256_file(self.path)
        if self.after != self.before:
            raise SafetyError(f"THE ORIGINAL SAVE CHANGED: {self.path} sha256 {self.before} -> {self.after}")
        return self.after
