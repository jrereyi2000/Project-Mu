"""Throwaway scratch databases for offseason_check: create, build from db/init_db.sql, seed, drop.

Every database this module creates is named `mu_scratch_offseason_<random>_test` and passes `safety.check_dbname`
before the CREATE and before the DROP. The CREATE / DROP DATABASE statements run on an admin connection to the
server's `postgres` maintenance database (autocommit, nothing else is run there); all work happens on the scratch
database through `safety.connect_scratch`.

The server is the local Docker container `madden-db` (postgres/postgres) unless `OFFSEASON_CHECK_PG` gives another
base DSN without a dbname.
"""
import os
import secrets
import subprocess
import sys
from contextlib import contextmanager

from psycopg.conninfo import conninfo_to_dict, make_conninfo

from tools.offseason_check import safety

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
INIT_DB = os.path.join(ROOT, "db", "init_db.sql")
BASE_ENV = "OFFSEASON_CHECK_PG"
DEFAULT_BASE = "host=localhost port=5432 user=postgres password=postgres"
# The carousel's gumbel draws are keyed by coaches.id / coordinators.id. The auditor's reference run (verify_e2e,
# verify_final, verify_last) had the seed's 32 head coaches start at id 33 and its 64 coordinators at id 65, and only
# that offset reproduces its OUTSIDE + RETREAD + POACHED head-coach mix; so a fresh seed is offset the same way.
SEED_ID_OFFSETS = {"coaches_id_seq": 32, "coordinators_id_seq": 64}


def base_dsn():
    return os.environ.get(BASE_ENV, DEFAULT_BASE)


def dsn_for(name, base=None):
    safety.check_dbname(name)
    parts = conninfo_to_dict(base or base_dsn())
    parts["dbname"] = name
    return make_conninfo(**parts)


def new_name(tag="offseason"):
    name = f"mu_scratch_{tag}_{secrets.token_hex(4)}_test"
    return safety.check_dbname(name)


def _admin(base=None):
    import psycopg
    parts = conninfo_to_dict(base or base_dsn())
    parts["dbname"] = "postgres"
    return psycopg.connect(make_conninfo(**parts), autocommit=True)


def create(name, base=None):
    safety.check_dbname(name)
    with _admin(base) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    return dsn_for(name, base)


def drop(name, base=None):
    safety.check_dbname(name)
    with _admin(base) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def init_schema(dsn, offsets=None):
    """Builds the schema from db/init_db.sql, then moves the ID sequences named in `offsets` ({seq: last value})."""
    with safety.connect_scratch(dsn, autocommit=True) as conn:
        with open(INIT_DB) as fh:
            conn.execute(fh.read())
        for seq, value in (offsets or {}).items():
            conn.execute("SELECT setval(%s, %s)", (seq, int(value)))


def seed(dsn, season):
    """db/seed_clean_db.py --season <season> on the scratch DB (32 teams, real 2026 coaches, GM personas, writers)."""
    safety.check_scratch_dsn(dsn)
    p = subprocess.run([sys.executable, "-m", "db.seed_clean_db", "--season", str(season), "--db", dsn],
                       cwd=ROOT, env=child_env(dsn), capture_output=True, text=True, timeout=900)
    if p.returncode != 0:
        raise RuntimeError(f"seed_clean_db failed:\n{p.stdout[-2000:]}\n{p.stderr[-2000:]}")
    return p.stdout


# Every child process gets these: no model, image or phone notification can be reached from a check run.
_STRIP_ENV = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "DEEPSEEK_API_KEY",
              "NTFY_TOPIC", "NTFY_URL", "NTFY_TOKEN")


def child_env(dsn):
    safety.check_scratch_dsn(dsn)
    env = {k: v for k, v in os.environ.items() if k not in _STRIP_ENV and not k.startswith("NTFY")}
    env.update(DATABASE_URL=dsn, MU_NO_LLM="1", MU_INBOX_LLM="0", PYTHONPATH=ROOT)
    return env


@contextmanager
def scratch_db(tag="offseason", keep=False, base=None, log=print):
    """Yields (name, dsn) of a fresh, empty scratch database; drops it afterwards unless `keep`."""
    name = new_name(tag)
    dsn = create(name, base)
    log(f"scratch DB {name} created")
    try:
        yield name, dsn
    finally:
        if keep:
            log(f"scratch DB {name} kept (--keep): drop it with DROP DATABASE \"{name}\"")
        else:
            drop(name, base)
            log(f"scratch DB {name} dropped")
