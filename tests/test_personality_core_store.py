"""Opt-in PostgreSQL integration, isolated schema and rollback; never public profiles.

PERSONALITY_CORE_TEST_DSN must explicitly name a scratch DB. No default connection.
"""
import os
from pathlib import Path
import uuid
import pytest
import psycopg
from psycopg import sql
from personality import store
from personality.core import Identity, ConflictError, PersonalityError

pytestmark = pytest.mark.integration


@pytest.fixture
def cur():
    dsn=os.environ.get('PERSONALITY_CORE_TEST_DSN')
    if not dsn: pytest.skip('explicit scratch DSN required')
    conn=psycopg.connect(dsn)
    if conn.info.dbname in ('madden_2036','postgres','template0','template1'):
        conn.close()
        pytest.fail('refusing archive/system DB for personality tests')
    cursor=conn.cursor()
    schema='persona_test_'+uuid.uuid4().hex
    try:
        cursor.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(schema)))
        cursor.execute(sql.SQL('SET LOCAL search_path TO {}').format(sql.Identifier(schema)))
        for name in ('2027_01_09_personas.sql','2027_01_16_personality_core.sql'):
            script=(Path(__file__).resolve().parents[1]/'db'/'migrations'/name).read_text(encoding='utf-8-sig')
            script=script.replace('BEGIN;','').replace('COMMIT;','')
            cursor.execute(script)
        yield cursor
    finally:
        conn.rollback()  # schema, migrations and all fixtures disappear
        conn.close()


def ident(key='one',u='test-universe'):
    return Identity(u,'player',key)


def test_initialize_once_alias_handoff_restart_and_universe_scope(cur):
    p=store.ensure(cur,ident(),'generated',2027)
    assert store.ensure(cur,ident(),'generated',2030,catalogue={})==p
    store.bind_alias(cur,ident(),'draft:2027','prospect-8')
    store.bind_alias(cur,ident(),'save-pgid','12345')
    store.bind_alias(cur,ident(),'save-pgid','12345')
    assert store.resolve_alias(cur,'test-universe','player','save-pgid','12345')==ident()
    other=store.ensure(cur,ident('two'),'generated',2027)
    with pytest.raises(ConflictError): store.bind_alias(cur,ident('two'),'save-pgid','12345')
    assert store.read(cur,ident('two'))==other
    assert store.read(cur,ident())==p
    store.ensure(cur,ident(u='other-universe'),'generated',2027)
    store.bind_alias(cur,ident(u='other-universe'),'save-pgid','12345')
    with pytest.raises(ConflictError): store.ensure(cur,ident(),'real',2027)


def test_audited_evolution_replay_stale_rejection_and_atomic_failure(cur):
    p=store.ensure(cur,ident(),'generated',2027)
    e=dict(event_id='annual-review',source_event_id='major-event',season=2028,
           expected_revision=0,kind='trait',remove=p['traits'][0],reason='fixture',rule_version='v1')
    after=store.change(cur,ident(),e)
    assert store.change(cur,ident(),e)==after
    with pytest.raises(ConflictError): store.change(cur,ident(),dict(e,reason='changed payload'))
    with pytest.raises(ConflictError): store.change(cur,ident(),dict(e,event_id='second',expected_revision=1))
    assert store.read(cur,ident())==after
    with pytest.raises(PersonalityError):
        store.change(cur,ident(),dict(e,event_id='bad',season=2029,expected_revision=1,remove=after['traits'][0]))
    assert store.read(cur,ident())==after
    cur.execute('SELECT count(*) FROM persona_core_events')
    assert cur.fetchone()[0]==1


def test_schema_failure_no_fallback_and_legacy_mutation_detection(cur):
    assert store.read(cur,ident()) is None
    p=store.ensure(cur,ident(),'real',2027)
    cur.execute('UPDATE persona_sliders SET temperament=4,drive=6 WHERE persona_id=%s',(p['persona_id'],))
    with pytest.raises(ConflictError): store.read(cur,ident())
    cur.execute('SAVEPOINT deliberate_missing')
    cur.execute('ALTER TABLE persona_core_profiles RENAME TO temporarily_missing')
    with pytest.raises(psycopg.errors.UndefinedTable): store.read(cur,ident())
    cur.execute('ROLLBACK TO SAVEPOINT deliberate_missing')


def test_helpers_do_not_commit_and_initialization_does_not_replace_legacy(cur):
    cur.execute('SAVEPOINT caller_checkpoint')
    store.ensure(cur,ident(),'generated',2027)
    cur.execute('ROLLBACK TO SAVEPOINT caller_checkpoint')
    assert store.read(cur,ident()) is None
    cur.execute('''INSERT INTO persona_sliders(persona_type,persona_id,temperament,sociability,
                   confidence,openness,warmth,drive) VALUES ('player',%s,5,5,5,5,5,5)''',(ident().persona_id,))
    with pytest.raises(ConflictError): store.ensure(cur,ident(),'generated',2027)
    cur.execute('SELECT count(*) FROM persona_core_profiles')
    assert cur.fetchone()[0]==0


def test_database_failure_after_canonical_write_rolls_back_everything(cur):
    p=store.ensure(cur,ident(),'generated',2027)
    cur.execute('ALTER TABLE persona_core_events ADD CONSTRAINT reject_fixture CHECK (season < 2020)')
    e=dict(event_id='fails-at-audit',source_event_id='canonical',season=2028,
           expected_revision=0,kind='trait',remove=p['traits'][0],reason='fixture',rule_version='v1')
    with pytest.raises(psycopg.errors.CheckViolation): store.change(cur,ident(),e)
    assert store.read(cur,ident())==p
    cur.execute('SELECT count(*) FROM persona_core_events')
    assert cur.fetchone()[0]==0
    cur.execute('SELECT count(*) FROM persona_trait_history WHERE removed_season IS NOT NULL')
    assert cur.fetchone()[0]==0


def test_earlier_replay_returns_original_receipt_without_reverting_newer_state(cur):
    p=store.ensure(cur,ident(),'generated',2027)
    e=dict(event_id='first',source_event_id='first-source',season=2028,
           expected_revision=0,kind='trait',remove=p['traits'][0],reason='fixture',rule_version='v1')
    first=store.change(cur,ident(),e)
    second=store.change(cur,ident(),dict(e,event_id='second',source_event_id='second-source',
                        season=2029,expected_revision=1,remove=first['traits'][0],add=p['traits'][0]))
    assert store.change(cur,ident(),e)==first
    assert store.read(cur,ident())==second
    with pytest.raises(ConflictError):
        store.change(cur,ident(),dict(e,event_id='relabelled',season=2030,expected_revision=2))
