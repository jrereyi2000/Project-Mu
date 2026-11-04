"""Lifecycle persistence (docs/in_progress/PERSONALITY_DRAFT_INTEGRATION_PLAN.md §2.2-§2.6, §4).

Offline: an in-memory SQLite database built from the real migrations (persona tables from 2027_01_09, core
sidecars from 2027_01_16, the core-extension event columns, and 2027_01_24), driven through the psycopg-like
FakeCursor from the inbox tests, so personality.store and lifecycle_store run their real SQL. PostgreSQL-only
pieces (advisory locks, information_schema) are stubbed. The opt-in scratch-schema case at the end needs
PERSONALITY_LIFECYCLE_TEST_DSN naming a scratch database.
"""
from copy import deepcopy
import os
import pathlib
import re
import sqlite3
import uuid

import pytest

from personality import core, lifecycle as L, lifecycle_store as S, store
from tests.test_inbox_store import FakeCursor, sqlite_ddl
from tests.test_personality_lifecycle import START, classes, manifest_for, snapshot, universe, by_pgid

ROOT = pathlib.Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / 'db' / 'migrations'
LIFECYCLE = MIGRATIONS / '2027_01_24_personality_lifecycle.sql'


def _blocks(sql, *tables):
    return '\n'.join(re.search(rf'CREATE TABLE IF NOT EXISTS {t} \(.*?\n\);', sql, re.S).group(0) for t in tables)


def lifecycle_ddl():
    return sqlite_ddl(re.sub(r'DO \$\$.*?END \$\$;', '', LIFECYCLE.read_text(), flags=re.S))


def build_db(stamp=None):
    conn = sqlite3.connect(':memory:', isolation_level=None)
    conn.create_function('hashtextextended', 2, lambda text, seed: hash(text) & 0xFFFF)
    conn.create_function('pg_advisory_xact_lock', 1, lambda key: None)
    personas = (MIGRATIONS / '2027_01_09_personas.sql').read_text()
    conn.executescript(sqlite_ddl(_blocks(personas, 'persona_sliders', 'persona_traits', 'persona_trait_history')))
    conn.executescript(sqlite_ddl((MIGRATIONS / '2027_01_16_personality_core.sql').read_text()))
    for column in ("kind TEXT NOT NULL DEFAULT 'evolution'", 'effective_tick INTEGER', 'actor TEXT', 'seq INTEGER',
                   'recorded_at TEXT'):
        conn.execute(f'ALTER TABLE persona_core_events ADD COLUMN {column}')
    conn.execute('''CREATE TABLE persona_core_links (universe_id TEXT, from_type TEXT, from_id TEXT, to_type TEXT,
                    to_id TEXT, link_kind TEXT, effective_season INTEGER, effective_tick INTEGER, receipt TEXT)''')
    conn.executescript(lifecycle_ddl())
    conn.execute('CREATE TABLE league_state (id INTEGER PRIMARY KEY, madden_league_id INTEGER)')
    conn.execute('INSERT INTO league_state VALUES (1, ?)', (stamp,))
    return conn


class Cursor(FakeCursor):
    def __init__(self, conn):
        super().__init__(conn)
        self.conn = conn


@pytest.fixture
def cur(monkeypatch):
    c = Cursor(build_db())

    def columns(cursor, table):
        return {r[1] for r in cursor.conn.execute(f'PRAGMA table_info({table})').fetchall()}
    monkeypatch.setattr(S, '_columns', columns)
    return c


def review(manifest, **kw):
    r = dict(reviewer='JR', reviewed_manifest_sha256=manifest['manifest_sha256'], decision='approve',
             reason='fixture review', reviewed_at='2026-11-04')
    r.update(kw)
    return r


def apply(cur, manifest):
    return S.apply_lifecycle(cur, manifest, expected_hash=manifest['manifest_sha256'], review_receipt=review(manifest))


def count(cur, table):
    cur.execute(f'SELECT count(*) FROM {table}')
    return cur.fetchone()[0]


# ---------- migration ----------

def test_migration_requires_core_verifies_fingerprint_and_seeds_nothing():
    sql = LIFECYCLE.read_text()
    assert 'INSERT INTO' not in sql
    assert "2027_01_16_personality_core.sql" in sql and 'RAISE EXCEPTION' in sql
    fingerprint = re.search(r"IS DISTINCT FROM '([^']+)'", sql).group(1)
    assert tuple(fingerprint.split(',')) == S.FINGERPRINT
    conn = build_db()
    conn.executescript(lifecycle_ddl())                     # rerun is harmless; the DO block checks the columns
    got = sorted(f'{t}.{r[1]}' for t in S.TABLES for r in conn.execute(f'PRAGMA table_info({t})'))
    assert tuple(got) == S.FINGERPRINT


def test_schema_check_rejects_a_different_schema(cur, monkeypatch):
    S.check_schema(cur)
    monkeypatch.setattr(S, '_columns', lambda c, t: {'universe_id'})
    with pytest.raises(L.StaleError, match='fingerprint'):
        S.check_schema(cur)


# ---------- apply / replay ----------

def test_apply_initializes_every_person_once_with_aliases_and_receipt(cur):
    m = manifest_for()
    receipt = apply(cur, m)
    assert receipt['counts']['initialized'] == 5 and not receipt['replay']
    assert count(cur, 'persona_core_profiles') == 5 and count(cur, 'persona_lifecycle_people') == 5
    assert count(cur, 'persona_lifecycle_bindings') == 10 and count(cur, 'persona_core_aliases') == 10
    starter = by_pgid(m, 101)[0]
    ident = store.resolve_alias(cur, m['universe']['universe_id'], 'player', 'save-pgid:1', '101')
    assert ident.persona_id == starter['persona_id']
    assert L.profile_hash(store.read(cur, ident)) == starter['proposed_profile_hash']
    again = apply(cur, m)                                   # exact replay returns the stored receipt
    assert again['replay'] and again['after_sha256'] == receipt['after_sha256']
    assert count(cur, 'persona_core_profiles') == 5
    assert count(cur, 'persona_lifecycle_batches') == 1


def test_apply_rejects_wrong_hash_review_and_stale_state(cur):
    m = manifest_for()
    with pytest.raises(L.StaleError):
        S.apply_lifecycle(cur, m, expected_hash='0' * 64, review_receipt=review(m))
    with pytest.raises(L.StaleError):
        S.apply_lifecycle(cur, m, expected_hash=m['manifest_sha256'],
                          review_receipt=review(m, reviewed_manifest_sha256='f' * 64))
    with pytest.raises(L.LifecycleError):
        S.apply_lifecycle(cur, m, expected_hash=m['manifest_sha256'], review_receipt=review(m, decision='reject'))
    apply(cur, m)
    later = manifest_for(snapshot([dict(pgid=777, status='Draft')]), cutoff=dict(season=2027, phase='draft',
                                                                                    sequence=0), uni=universe())
    with pytest.raises(L.StaleError, match='changed since preview'):    # previewed against an empty state
        apply(cur, later)
    assert count(cur, 'persona_lifecycle_batches') == 1


def test_conflicting_batch_same_hash_different_payload(cur):
    m = manifest_for()
    apply(cur, m)
    cur.execute('UPDATE persona_lifecycle_batches SET manifest = ?', ('{"x": 1}',))
    with pytest.raises(L.StaleError, match='different payload'):
        apply(cur, m)


def test_replay_preview_from_export_retains_and_applies_cleanly(cur):
    m = manifest_for()
    apply(cur, m)
    export = S.export_state(cur, m['universe']['universe_id'])
    assert len(export['people']) == 5 and export['universe']['league_id'] == 'LEAGUE-1'
    replay = manifest_for(existing=export)
    assert {p['action'] for p in replay['people'] if p['source'].get('pgid') != 901} == {'retain'}
    r = apply(cur, replay)
    assert r['counts']['retained'] == 5 and count(cur, 'persona_core_profiles') == 5


def test_later_class_entry_and_rookie_handoff_keep_one_profile(cur):
    m = manifest_for()
    apply(cur, m)
    uid = m['universe']['universe_id']
    export = S.export_state(cur, uid)
    later = snapshot([dict(pgid=201, status='Signed'), dict(pgid=301, status='Draft')])
    cutoff = dict(season=2027, phase='post_draft', sequence=0)
    nxt = manifest_for(later, cls=classes(L.validate_receipt(dict(
        origin='generated', source_ref='f', source_sha256='0' * 64, reviewer='JR', reason='class',
        reviewed_at='x', effective_at='x', scope='class:2028'))), existing=export, cutoff=cutoff, uni=universe())
    assert by_pgid(nxt, 201)[0]['action'] == 'retain' and by_pgid(nxt, 301)[0]['action'] == 'initialize'
    apply(cur, nxt)
    assert count(cur, 'persona_core_profiles') == 6
    rookie = by_pgid(m, 201)[0]
    S.bind_person(cur, uid, 'player', rookie['source_person_id'], [dict(namespace='db-player', external_id='55')],
                  cutoff=cutoff, receipt=dict(handoff='same_pgid_lifetime'))
    assert store.resolve_alias(cur, uid, 'player', 'db-player', '55').persona_id == rookie['persona_id']
    with pytest.raises(L.StaleError):
        S.bind_person(cur, uid, 'player', by_pgid(m, 101)[0]['source_person_id'],
                      [dict(namespace='db-player', external_id='55')], cutoff=cutoff, receipt={})


def test_jr_edit_before_first_use_is_stored_as_reviewed(cur):
    m = deepcopy(manifest_for())
    person = by_pgid(m, 101)[0]
    s = person['proposed_profile']['sliders']
    hi, lo = max(s, key=s.get), min(s, key=s.get)
    s[hi] -= 1
    s[lo] += 1
    m = L.finalize_manifest(m)
    apply(cur, m)
    ident = L.identity(m['universe']['universe_id'], 'player', person['source_person_id'])
    stored = store.read(cur, ident)
    assert stored['sliders'] == s and stored['revision'] == 0
    assert stored['traits'] == by_pgid(m, 101)[0]['proposed_profile']['traits']


def test_legacy_collision_and_league_stamp(cur, monkeypatch):
    m = manifest_for()
    cur.execute('UPDATE league_state SET madden_league_id = 999 WHERE id = 1')
    with pytest.raises(L.StaleError, match='stamped'):
        apply(cur, m)
    cur.execute('UPDATE league_state SET madden_league_id = NULL WHERE id = 1')
    pid = by_pgid(m, 101)[0]['persona_id']
    cur.execute('''INSERT INTO persona_sliders (persona_type, persona_id, temperament, sociability, confidence,
                   openness, warmth, drive) VALUES ('player', %s, 5, 5, 5, 5, 5, 5)''', (pid,))
    with pytest.raises(L.StaleError):                       # a legacy row the preview didn't see
        apply(cur, m)
    assert count(cur, 'persona_core_profiles') == 0          # atomic: nothing from the batch survived


def test_forced_failure_after_core_write_rolls_back_whole_batch(cur, monkeypatch):
    m = manifest_for()
    real = S._insert_person
    calls = []

    def boom(*a, **k):
        calls.append(1)
        if len(calls) == 3:
            raise RuntimeError('injected')
        return real(*a, **k)
    monkeypatch.setattr(S, '_insert_person', boom)
    with pytest.raises(RuntimeError):
        apply(cur, m)
    for table in ('persona_core_profiles', 'persona_sliders', 'persona_lifecycle_people', 'persona_core_aliases',
                  'persona_lifecycle_universes', 'persona_lifecycle_batches'):
        assert count(cur, table) == 0, table


def test_universe_registration_is_immutable(cur):
    u = universe()
    S.register_universe(cur, u)
    S.register_universe(cur, u)
    with pytest.raises(L.StaleError):
        S.register_universe(cur, dict(u, initial_snapshot_sha256='a' * 64))
    other = L.new_universe('LEAGUE-1', 'b' * 64, 2026)
    with pytest.raises(L.StaleError, match='registered'):
        S.register_universe(cur, other)


# ---------- revert ----------

def test_revert_removes_exclusive_rows_and_keeps_receipt(cur):
    m = manifest_for()
    r = apply(cur, m)
    with pytest.raises(L.StaleError):
        S.revert_batch(cur, m['manifest_sha256'], expected_after_hash='0' * 64, review_receipt=review(m))
    out = S.revert_batch(cur, m['manifest_sha256'], expected_after_hash=r['after_sha256'], review_receipt=review(m))
    assert out['removed'] == dict(bindings=10, profiles=5)
    for table in ('persona_core_profiles', 'persona_sliders', 'persona_traits', 'persona_trait_history',
                  'persona_core_aliases', 'persona_lifecycle_people', 'persona_lifecycle_bindings'):
        assert count(cur, table) == 0, table
    assert count(cur, 'persona_core_catalogues') == 1 and count(cur, 'persona_lifecycle_universes') == 1
    cur.execute('SELECT status FROM persona_lifecycle_batches')
    assert cur.fetchone()[0] == 'reverted'
    with pytest.raises(L.StaleError, match='reverted'):
        apply(cur, m)


def test_revert_refused_after_downstream_use(cur):
    m = manifest_for()
    r = apply(cur, m)
    uid = m['universe']['universe_id']
    starter = by_pgid(m, 101)[0]
    S.bind_person(cur, uid, 'player', starter['source_person_id'], [dict(namespace='db-player', external_id='9')],
                  cutoff=START, receipt=dict(x=1))
    with pytest.raises(L.StaleError, match='gained'):
        S.revert_batch(cur, m['manifest_sha256'], expected_after_hash=r['after_sha256'], review_receipt=review(m))
    assert count(cur, 'persona_core_profiles') == 5


def test_revert_refused_after_evolution_receipt(cur):
    m = manifest_for()
    r = apply(cur, m)
    ident = L.identity(m['universe']['universe_id'], 'player', by_pgid(m, 101)[0]['source_person_id'])
    p = store.read(cur, ident)
    store.change(cur, ident, dict(event_id='e1', source_event_id='s1', season=2027, expected_revision=0, kind='trait',
                                  remove=p['traits'][0], reason='fixture', rule_version='v1'))
    with pytest.raises(L.StaleError):
        S.revert_batch(cur, m['manifest_sha256'], expected_after_hash=r['after_sha256'], review_receipt=review(m))


# ---------- resolution ----------

def test_resolve_ready_missing_future_quarantined_and_corrupt(cur):
    m = manifest_for()
    apply(cur, m)
    uid = m['universe']['universe_id']
    r = S.resolve_person(cur, uid, 'player', 'save-pgid:1', '101', cutoff=START, feature='contracts')
    assert r['status'] == 'ready' and r['view']['status'] == 'stored' and r['profile_revision'] == 0
    missing = S.resolve_person(cur, uid, 'player', 'save-pgid:1', '4040', cutoff=START, feature='contracts')
    assert missing['status'] == 'missing' and missing['view']['status'] == 'missing'
    assert set(missing['view']['sliders'].values()) == {5}
    early = dict(season=2025, phase='playoffs', sequence=0)
    assert S.resolve_person(cur, uid, 'player', 'save-pgid:1', '101', cutoff=early,
                            feature='contracts')['status'] == 'future'
    spid = by_pgid(m, 102)[0]['source_person_id']
    cur.execute("UPDATE persona_lifecycle_people SET state='quarantined' WHERE source_person_id=%s", (spid,))
    assert S.resolve_person(cur, uid, 'player', 'save-pgid:1', '102', cutoff=START,
                            feature='contracts')['status'] == 'quarantined'
    pid = by_pgid(m, 103)[0]['persona_id']
    cur.execute("UPDATE persona_core_profiles SET metadata='{\"broken\": true}' WHERE persona_id=%s", (pid,))
    with pytest.raises(Exception):
        S.resolve_person(cur, uid, 'player', 'save-pgid:1', '103', cutoff=START, feature='contracts')


def test_resolve_as_of_after_evolution(cur):
    m = manifest_for()
    apply(cur, m)
    uid = m['universe']['universe_id']
    ident = L.identity(uid, 'player', by_pgid(m, 101)[0]['source_person_id'])
    before = store.read(cur, ident)
    store.change(cur, ident, dict(event_id='e1', source_event_id='s1', season=2028, expected_revision=0, kind='trait',
                                  remove=before['traits'][0], reason='fixture', rule_version='v1'))
    then = S.resolve_person(cur, uid, 'player', 'save-pgid:1', '101', cutoff=dict(season=2027, phase='draft',
                                                                                  sequence=0), feature='x')
    assert then['view']['traits'] == before['traits']
    with pytest.raises(S.HistoricalResolutionError):
        S.resolve_person(cur, uid, 'player', 'save-pgid:1', '101', cutoff=dict(season=2028, phase='draft',
                                                                               sequence=0), feature='x')
    now = S.resolve_person(cur, uid, 'player', 'save-pgid:1', '101', cutoff=dict(season=2028, phase='draft',
                                                                                 sequence=0), feature='x', current=True)
    assert now['profile_revision'] == 1


def test_require_ready_observe_and_enforce(cur):
    m = manifest_for()
    apply(cur, m)
    uid = m['universe']['universe_id']
    refs = [dict(universe_id=uid, role='player', namespace='save-pgid:1', external_id='101'),
            dict(universe_id=uid, role='player', namespace='save-pgid:1', external_id='4040')]
    report = S.require_ready(cur, refs, cutoff=START, feature='contracts', mode='observe')
    assert report['ready'] == 1 and len(report['unresolved']) == 1 and not report['blocked']
    with pytest.raises(S.NotReadyError) as e:
        S.require_ready(cur, refs, cutoff=START, feature='contracts', mode='enforce')
    assert e.value.report['blocked'] and e.value.code == L.EXIT_UNRESOLVED
    refs[1]['required'] = False
    assert S.require_ready(cur, refs, cutoff=START, feature='contracts', mode='enforce')['ready'] == 1


# ---------- creator hooks ----------

def test_initialize_person_once_archive_and_close_binding(cur):
    u = universe()
    S.register_universe(cur, u)
    uid = u['universe_id']
    spid = str(uuid.UUID(int=7))
    first = S.initialize_person(cur, uid, 'coach', spid, 'generated', 2026, cutoff=START,
                                receipt=dict(creator='hiring_cycle', event='c1'),
                                aliases=[dict(namespace='db-coordinator', external_id='3')])
    again = S.initialize_person(cur, uid, 'coach', spid, 'generated', 2027, cutoff=START,
                                receipt=dict(creator='hiring_cycle', event='c1'),
                                aliases=[dict(namespace='db-coach', external_id='12')])
    assert first['created'] and not again['created'] and first['persona_id'] == again['persona_id']
    assert count(cur, 'persona_core_profiles') == 1
    with pytest.raises(L.StaleError):
        S.initialize_person(cur, uid, 'coach', spid, 'real', 2027, cutoff=START, receipt=dict(x=1),
                            profile=dict(traits=[]))
    with pytest.raises(L.LifecycleError):
        S.initialize_person(cur, uid, 'coach', str(uuid.UUID(int=8)), 'unknown', 2026, cutoff=START, receipt={'x': 1})
    assert S.archive_person(cur, uid, 'coach', spid, cutoff=START, reason='row deleted')
    assert not S.archive_person(cur, uid, 'coach', spid, cutoff=START, reason='row deleted')
    r = S.resolve_person(cur, uid, 'coach', 'db-coach', '12', cutoff=START, feature='x')
    assert r['status'] == 'ready' and r['reason'] == 'archived'
    board = dict(namespace='draft:2027:abc', external_id='77')
    S.bind_person(cur, uid, 'coach', spid, [board], cutoff=START, receipt={'x': 1})
    assert count(cur, 'persona_core_aliases') == 3           # board IDs stay lifecycle-only
    assert S.close_binding(cur, uid, 'coach', 'draft:2027:abc', '77', cutoff=dict(season=2026, phase='draft',
                                                                                 sequence=0)) == 1
    assert S.resolve_person(cur, uid, 'coach', 'draft:2027:abc', '77', cutoff=dict(season=2027, phase='draft',
                                                                                   sequence=0),
                            feature='x')['reason'] == 'binding_closed'


def test_initialize_person_needs_registered_universe(cur):
    with pytest.raises(L.StaleError, match='not registered'):
        S.initialize_person(cur, universe()['universe_id'], 'coach', str(uuid.UUID(int=7)), 'generated', 2026,
                            cutoff=START, receipt={'x': 1})


def test_store_never_commits(cur):
    with pytest.raises(AssertionError):
        cur.commit()
    apply(cur, manifest_for())                               # would raise if the store committed


# ---------- opt-in PostgreSQL scratch schema ----------

@pytest.mark.integration
def test_scratch_schema_migration_apply_replay_revert():
    dsn = os.environ.get('PERSONALITY_LIFECYCLE_TEST_DSN')
    if not dsn:
        pytest.skip('explicit scratch DSN required')
    import psycopg
    from psycopg import sql
    conn = psycopg.connect(dsn)
    if conn.info.dbname in ('madden', 'madden_2036', 'postgres', 'template0', 'template1'):
        conn.close()
        pytest.fail('refusing a live/archive/system database')
    c = conn.cursor()
    schema = 'lifecycle_test_' + uuid.uuid4().hex
    try:
        c.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        c.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
        for name in ('2027_01_09_personas.sql', '2027_01_16_personality_core.sql',
                     '2027_01_29_personality_core_extensions.sql', '2027_01_24_personality_lifecycle.sql'):
            c.execute((MIGRATIONS / name).read_text().replace('BEGIN;', '').replace('COMMIT;', ''))
        c.execute((MIGRATIONS / '2027_01_24_personality_lifecycle.sql').read_text()
                  .replace('BEGIN;', '').replace('COMMIT;', ''))      # rerun verifies the fingerprint
        m = manifest_for()
        r = apply(c, m)
        assert apply(c, m)['replay']
        S.revert_batch(c, m['manifest_sha256'], expected_after_hash=r['after_sha256'], review_receipt=review(m))
    finally:
        conn.rollback()
        conn.close()
