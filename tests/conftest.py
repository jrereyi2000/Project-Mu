"""Global pytest configuration and Live-DB Firewall (tests/conftest.py).

Enforces strict database isolation across the entire test suite:
1. Live 'madden' database connections are unconditionally blocked with
   LiveDatabaseAccessForbiddenError (a BaseException subclass so `except Exception:`
   blocks in application code cannot swallow unmocked DB access).
2. Unit tests (not marked `@pytest.mark.integration`) are forbidden from opening
   any real PostgreSQL connection unless targeting an explicit per-test scratch DB
   (`mu_scratch_*` / `TX_TEST_DATABASE_URL`).
3. Integration tests (`@pytest.mark.integration`) run exclusively against the
   isolated `madden_test` database (`TEST_DATABASE_URL`), which is automatically
   provisioned from `madden` (if missing or when `--recreate-test-db` is passed).
"""

from contextlib import contextmanager
import os
import re
import subprocess
import pytest

TEST_DB_NAME = os.environ.get("MADDEN_TEST_DB_NAME", "madden_test")
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    f"host=localhost dbname={TEST_DB_NAME} user=postgres password=postgres",
)

# Set DATABASE_URL before any test module is imported so module-level constants
# (CONN_INFO, DATABASE_URL, DSN) and default function arguments bind to madden_test
# instead of live madden.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL

import psycopg
from psycopg.rows import dict_row
import psycopg2


class LiveDatabaseAccessForbiddenError(BaseException):
    """Raised when a test attempts to connect to live 'madden' or an unmocked DB in unit mode."""


_ORIG_PSYCOPG_CONNECT = psycopg.connect
_ORIG_PSYCOPG_CONN_CONNECT = psycopg.Connection.connect
_ORIG_PSYCOPG2_CONNECT = psycopg2.connect

_CURRENT_ITEM = None
_IN_TEARDOWN = False
_HAD_INTEGRATION_IN_MODULE = False
_TEST_DB_READY = None  # None = unchecked, True = ready, False = unavailable


def _extract_dbname(conninfo: str = "", kwargs: dict | None = None) -> str:
    kwargs = kwargs or {}
    if "dbname" in kwargs and kwargs["dbname"]:
        return str(kwargs["dbname"]).strip()
    if "database" in kwargs and kwargs["database"]:
        return str(kwargs["database"]).strip()
    s = str(conninfo or "")
    m = re.search(r"dbname\s*=\s*([^\s'\"]+)", s)
    if m:
        return m.group(1).strip()
    m_uri = re.search(r"postgres(?:ql)?://[^/]+/([^\s\?]+)", s)
    if m_uri:
        return m_uri.group(1).strip()
    return ""


def _is_scratch_db(dbname: str) -> bool:
    if not dbname:
        return False
    tx_url = os.environ.get("TX_TEST_DATABASE_URL", "")
    if tx_url and dbname == _extract_dbname(tx_url):
        return True
    return dbname.startswith("mu_scratch_")


def _check_connection_allowed(conninfo: str, kwargs: dict) -> None:
    dbname = _extract_dbname(conninfo, kwargs)

    # 1. Absolute ban on connecting to the live databases: legacy 'madden', the frozen 'madden_2036' archive, and
    #    every franchise database (madden_<slug>, db/franchises.py); only madden_test is open to tests
    if dbname == "madden" or (dbname.startswith("madden_") and dbname != TEST_DB_NAME):
        item_name = _CURRENT_ITEM.nodeid if _CURRENT_ITEM is not None else "<collection/import>"
        raise LiveDatabaseAccessForbiddenError(
            f"[{item_name}] Attempted connection to live database {dbname!r} ({conninfo!r}) "
            f"is strictly forbidden during pytest!"
        )

    # Allow explicit scratch databases (e.g. test_debug_api TX_TEST_DATABASE_URL)
    if _is_scratch_db(dbname):
        return

    # 2. Collection/import-time connections are strictly forbidden
    if _CURRENT_ITEM is None:
        raise LiveDatabaseAccessForbiddenError(
            f"[<collection/import>] Attempted database connection at import/collection time "
            f"({conninfo!r}). Move DB calls inside test functions or fixtures."
        )

    # 3. Unit tests (without @pytest.mark.integration) cannot open real DB connections
    is_integration = _CURRENT_ITEM.get_closest_marker("integration") is not None
    if not is_integration and not (_IN_TEARDOWN and _HAD_INTEGRATION_IN_MODULE):
        raise LiveDatabaseAccessForbiddenError(
            f"[{_CURRENT_ITEM.nodeid}] Unit test attempted a real database connection "
            f"({conninfo!r}). Mock the database or mark the test @pytest.mark.integration."
        )


def _guarded_psycopg_connect(conninfo="", *args, **kwargs):
    _check_connection_allowed(conninfo, kwargs)
    return _ORIG_PSYCOPG_CONNECT(conninfo, *args, **kwargs)


def _guarded_psycopg_conn_connect(cls, conninfo="", *args, **kwargs):
    _check_connection_allowed(conninfo, kwargs)
    return _ORIG_PSYCOPG_CONN_CONNECT(conninfo, *args, **kwargs)


def _guarded_psycopg2_connect(dsn=None, *args, **kwargs):
    _check_connection_allowed(dsn or "", kwargs)
    return _ORIG_PSYCOPG2_CONNECT(dsn, *args, **kwargs)


def _ensure_test_database_provisioned(recreate: bool = False) -> bool:
    """Ensures `madden_test` exists and has the schema + seed state."""
    global _TEST_DB_READY
    if _TEST_DB_READY is not None and not recreate:
        return _TEST_DB_READY

    admin_dsn = "host=localhost dbname=postgres user=postgres password=postgres connect_timeout=3"
    try:
        with _ORIG_PSYCOPG_CONNECT(admin_dsn, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (TEST_DB_NAME,))
                exists = cur.fetchone() is not None
    except Exception:
        _TEST_DB_READY = False
        return False

    if recreate or not exists:
        try:
            subprocess.run(
                ["docker", "exec", "madden-db", "psql", "-U", "postgres", "-c", f"DROP DATABASE IF EXISTS {TEST_DB_NAME};"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["docker", "exec", "madden-db", "psql", "-U", "postgres", "-c", f"CREATE DATABASE {TEST_DB_NAME};"],
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["docker", "exec", "madden-db", "sh", "-c", f"pg_dump -U postgres madden | psql -U postgres -q -d {TEST_DB_NAME}"],
                check=True,
                capture_output=True,
            )
        except Exception:
            _TEST_DB_READY = False
            return False

    _TEST_DB_READY = True
    return True


def pytest_addoption(parser):
    parser.addoption(
        "--recreate-test-db",
        action="store_true",
        default=False,
        help="Drop and re-clone the madden_test integration database from madden before running integration tests.",
    )


def pytest_configure(config):
    os.environ["DATABASE_URL"] = TEST_DATABASE_URL
    config.addinivalue_line(
        "markers",
        "integration: stateful SQL integration tests running against the isolated madden_test database (never live madden)",
    )
    psycopg.connect = _guarded_psycopg_connect
    psycopg.Connection.connect = classmethod(_guarded_psycopg_conn_connect)
    psycopg2.connect = _guarded_psycopg2_connect

    # Also patch server.api.db_pool so pool.connection() goes through the guarded psycopg.connect
    try:
        import server.api as api_mod

        @contextmanager
        def _guarded_pool_connection(timeout=None):
            with psycopg.connect(api_mod.CONN_INFO, row_factory=dict_row) as conn:
                yield conn

        api_mod.CONN_INFO = TEST_DATABASE_URL
        api_mod.db_pool.connection = _guarded_pool_connection
    except Exception:
        pass


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    global _CURRENT_ITEM, _HAD_INTEGRATION_IN_MODULE
    _CURRENT_ITEM = item
    if item.get_closest_marker("integration") is not None:
        _HAD_INTEGRATION_IN_MODULE = True
    try:
        yield
    finally:
        if nextitem is None or getattr(nextitem, "module", None) is not getattr(item, "module", None):
            _HAD_INTEGRATION_IN_MODULE = False
        _CURRENT_ITEM = None


def pytest_runtest_setup(item):
    if item.get_closest_marker("integration") is not None:
        recreate = item.config.getoption("--recreate-test-db") and _TEST_DB_READY is None
        if not _ensure_test_database_provisioned(recreate=recreate):
            pytest.skip(f"Integration database '{TEST_DB_NAME}' is not available")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    global _IN_TEARDOWN
    _IN_TEARDOWN = True
    try:
        yield
    finally:
        _IN_TEARDOWN = False


@pytest.fixture(autouse=True)
def guard_llm_usage_logging_in_unit_tests(request, monkeypatch):
    """Prevent unit tests from attempting any real llm_usage database logging."""
    is_integration = request.node.get_closest_marker("integration") is not None
    if not is_integration:
        # If the test specifically exercises log_usage / log_response mechanics with mocked DB, don't override
        if "test_log_usage" in request.node.name or "test_log_response" in request.node.name:
            return

        def _noop_log_usage(source, label, model, input_tokens=0, cached_tokens=0, output_tokens=0, thinking_tokens=0):
            return {
                "input_tokens": int(input_tokens or 0),
                "cached_tokens": int(cached_tokens or 0),
                "output_tokens": int(output_tokens or 0),
                "thinking_tokens": int(thinking_tokens or 0),
            }

        def _noop_log_response(source, label, model, resp):
            from db.llm_usage import _usage
            return _usage(resp)

        def _noop_record(self, source, label=None, model=None, capture_folder=None, mode=None):
            totals = self.totals()
            self.take()
            return totals

        try:
            import db.llm_usage
            monkeypatch.setattr(db.llm_usage, "log_usage", _noop_log_usage)
            monkeypatch.setattr(db.llm_usage, "log_response", _noop_log_response)
            monkeypatch.setattr(db.llm_usage.UsageMeter, "record", _noop_record)
        except (ImportError, AttributeError):
            pass

        try:
            import engines.llm_provider
            monkeypatch.setattr(engines.llm_provider, "log_usage", _noop_log_usage)
            monkeypatch.setattr(engines.llm_provider, "log_response", _noop_log_response)
        except (ImportError, AttributeError):
            pass


