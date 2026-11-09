"""db/migrate.py: the migration ledger and runner (CLOSE_OUT_CHECKLIST P1-02/P1-03).

Offline: discovery, ordering, checksums, the generated db/init_db.sql block, the transaction wrapper and the
archive refusal. Integration (needs Postgres): a fresh db/init_db.sql build reports every migration applied,
re-running apply is a no-op, and `madden_test` -- the fully-migrated database -- matches that fresh build
object for object (P1-03).
"""
import pathlib

import pytest

from db import migrate

ROOT = pathlib.Path(__file__).resolve().parents[1]
ADMIN = "host=localhost dbname=postgres user=postgres password=postgres connect_timeout=3"
SCRATCH = "mu_scratch_migrate_ledger"


def _connect(name):
    import psycopg
    return psycopg.connect(f"host=localhost dbname={name} user=postgres password=postgres", autocommit=True)


# -------------------------------------------------------------------------------------------------- offline ---

def test_migrations_are_discovered_in_filename_order():
    names = [m["filename"] for m in migrate.migrations()]
    assert names == sorted(names)
    assert all(n.endswith(".sql") for n in names)
    assert "2027_02_08_nflpa_report_cards.sql" in names


def test_every_migration_has_a_checksum():
    for m in migrate.migrations():
        assert len(m["checksum"]) == 64 and int(m["checksum"], 16) >= 0


def test_init_db_ledger_block_lists_every_migration_with_its_checksum():
    init = (ROOT / "db" / "init_db.sql").read_text()
    start = init.index(migrate.BLOCK_START)
    block = init[start:]
    for m in migrate.migrations():
        assert f"('{m['version']}', '{m['filename']}', '{m['checksum']}', 'init_db.sql')" in block, m["filename"]
    assert migrate.BLOCK_START in block and migrate.BLOCK_END in block


def test_sync_init_db_is_idempotent(tmp_path):
    init = tmp_path / "init_db.sql"
    init.write_text((ROOT / "db" / "init_db.sql").read_text())
    assert migrate.sync_init_db(init) is False
    assert init.read_text().count(migrate.BLOCK_START) == 1


def test_strip_outer_transaction_keeps_the_body():
    sql = "-- header\n\nBEGIN;\nCREATE TABLE x (id INT);\nCOMMIT;\n"
    body = migrate._strip_outer_transaction(sql)
    assert "CREATE TABLE x" in body
    assert "BEGIN;" not in body and "COMMIT;" not in body


def test_a_body_without_transaction_is_left_alone():
    assert migrate._strip_outer_transaction("CREATE TABLE x (id INT);\n").startswith("CREATE TABLE x")


@pytest.mark.parametrize("dsn", ["host=localhost dbname=madden_2036", "postgresql://u@h/postgres",
                                 "dbname=template0"])
def test_refused_databases(dsn):
    with pytest.raises(migrate.MigrationError):
        migrate.check_dsn(dsn)


def test_dbname_parsing():
    assert migrate.dbname_of("host=localhost dbname=madden_test user=postgres") == "madden_test"
    assert migrate.dbname_of("postgresql://u@h/mu_scratch_x") == "mu_scratch_x"


# ---------------------------------------------------------------------------------------------- integration ---

@pytest.fixture(scope="module")
def fresh():
    """A database built from db/init_db.sql alone, and a connection to it."""
    import psycopg
    try:
        admin = psycopg.connect(ADMIN, autocommit=True)
    except Exception:
        pytest.skip("no Postgres")
    admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {SCRATCH}")
    with _connect(SCRATCH) as conn:
        conn.execute((ROOT / "db" / "init_db.sql").read_text())
    yield SCRATCH
    admin.execute(f"DROP DATABASE IF EXISTS {SCRATCH} WITH (FORCE)")
    admin.close()


@pytest.mark.integration
def test_a_fresh_init_db_build_reports_every_migration_applied(fresh):
    with _connect(fresh) as conn:
        entries = migrate.status_of(conn)
        assert entries and all(e["state"] == "applied" for e in entries)
        assert {e["source"] for e in entries} == {"init_db.sql"}
        assert migrate.apply(conn, dry_run=True) == []


@pytest.mark.integration
def test_madden_test_matches_a_fresh_init_db_build(fresh):
    """P1-03: the fully-migrated database and db/init_db.sql describe the same schema."""
    with _connect("madden_test") as migrated, _connect(fresh) as rebuilt:
        diffs = migrate.compare_schemas(migrate.schema_snapshot(migrated), migrate.schema_snapshot(rebuilt),
                                        ("madden_test", fresh))
    assert diffs == []


@pytest.mark.integration
def test_status_detects_checksum_drift_and_apply_refuses(fresh):
    with _connect(fresh) as conn:
        version = migrate.migrations()[0]["version"]
        conn.execute(f"UPDATE {migrate.LEDGER} SET checksum = 'changed' WHERE version = %s", (version,))
        try:
            state = {e["version"]: e["state"] for e in migrate.status_of(conn)}
            assert state[version] == "drift"
            with pytest.raises(migrate.MigrationError):
                migrate.apply(conn)
        finally:
            conn.execute(f"DELETE FROM {migrate.LEDGER}")
            for m in migrate.migrations():
                conn.execute(f"INSERT INTO {migrate.LEDGER} (version, filename, checksum, source) "
                             f"VALUES (%s, %s, %s, 'init_db.sql')", (m["version"], m["filename"], m["checksum"]))


@pytest.mark.integration
def test_the_wave_1_migrations_are_no_ops_on_a_fresh_init_db_build():
    """P1-03: re-running the newest migrations on db/init_db.sql's build changes no schema object."""
    import psycopg
    scratch = "mu_scratch_migrate_replay"
    try:
        admin = psycopg.connect(ADMIN, autocommit=True)
    except Exception:
        pytest.skip("no Postgres")
    wave = ["2027_01_20_press_conferences.sql", "2027_02_02_owners_scouts_voters.sql",
            "2027_02_06_compensatory_draft_picks.sql", "2027_02_07_staff_contracts_and_full_control.sql",
            "2027_02_07_staff_development_award.sql", "2027_02_08_nflpa_report_cards.sql",
            "2027_02_09_init_db_parity.sql"]
    admin.execute(f"DROP DATABASE IF EXISTS {scratch} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {scratch}")
    try:
        with _connect(scratch) as conn:
            conn.execute((ROOT / "db" / "init_db.sql").read_text())
            before = migrate.schema_snapshot(conn)
            for name in wave:
                body = migrate._strip_outer_transaction((ROOT / "db" / "migrations" / name).read_text())
                conn.execute("BEGIN;\n" + body + "\n; COMMIT;")
            diffs = migrate.compare_schemas(before, migrate.schema_snapshot(conn))
        assert diffs == []
    finally:
        admin.execute(f"DROP DATABASE IF EXISTS {scratch} WITH (FORCE)")
        admin.close()


@pytest.mark.integration
def test_baseline_records_every_migration(fresh):
    with _connect(fresh) as conn:
        conn.execute(f"DELETE FROM {migrate.LEDGER}")
        done = migrate.baseline(conn)
        assert len(done) == len(migrate.migrations())
        assert all(e["state"] == "applied" and e["source"] == "baseline" for e in migrate.status_of(conn))
        # restore the init_db.sql source for the other tests in this module
        conn.execute(f"DELETE FROM {migrate.LEDGER}")
        for m in migrate.migrations():
            conn.execute(f"INSERT INTO {migrate.LEDGER} (version, filename, checksum, source) "
                         f"VALUES (%s, %s, %s, 'init_db.sql')", (m["version"], m["filename"], m["checksum"]))
