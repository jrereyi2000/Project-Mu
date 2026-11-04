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
from personality.core import Identity, ConflictError, NotFoundError, PersonalityError

pytestmark = pytest.mark.integration

MIGRATIONS=Path(__file__).resolve().parents[1]/'db'/'migrations'
CORE=('2027_01_09_personas.sql','2027_01_16_personality_core.sql')
EXTENSIONS='2027_01_29_personality_core_extensions.sql'


def apply(cursor, *names):
    for name in names:
        script=(MIGRATIONS/name).read_text(encoding='utf-8-sig')
        cursor.execute(script.replace('BEGIN;','').replace('COMMIT;',''))


@pytest.fixture
def bare():
    """Scratch schema with the persona and core migrations only (no extensions)."""
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
        apply(cursor,*CORE)
        yield cursor
    finally:
        conn.rollback()  # schema, migrations and all fixtures disappear
        conn.close()


@pytest.fixture
def cur(bare):
    apply(bare,EXTENSIONS)
    return bare


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


# ---- core extensions (docs/in_progress/PERSONALITY_CORE_EXTENSIONS_PLAN.md) ----

def seed_legacy(cur, role, pid, sliders, traits):
    cur.execute('''INSERT INTO persona_sliders(persona_type,persona_id,temperament,sociability,confidence,
                   openness,warmth,drive) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)''',(role,pid,*sliders))
    for t in traits:
        cur.execute('INSERT INTO persona_traits(persona_type,persona_id,trait_id) VALUES (%s,%s,%s)',(role,pid,t))


def shift(sliders):
    """Move one point between two sliders: always inside 0-10 and the 24-36 band."""
    up=next(k for k,v in sliders.items() if v<10)
    down=next(k for k,v in sliders.items() if v>0 and k!=up)
    return {up:sliders[up]+1,down:sliders[down]-1}


def jr(p, **kw):
    r=dict(override_id='jr-1',actor='JR',reason='fixture override',season=2028,tick=4,
           expected_revision=p['revision'],date='2026-11-04',sliders=shift(p['sliders']))
    r.update(kw)
    return r


def evolution(p, **kw):
    e=dict(event_id='annual',source_event_id='major',season=2028,expected_revision=p['revision'],
           kind='trait',remove=p['traits'][0],reason='fixture',rule_version='v1')
    e.update(kw)
    return e


def test_migration_twice_and_existing_rows_become_evolution(bare):
    p=store.ensure(bare,ident(),'generated',2027)
    bare.execute('''INSERT INTO persona_core_events(persona_type,persona_id,event_id,source_event_id,season,
                     request,before_state,after_state) VALUES ('player',%s,'old','old-source',2028,'{}','{}','{}')''',
                 (p['persona_id'],))
    apply(bare,EXTENSIONS)
    apply(bare,EXTENSIONS)
    bare.execute('SELECT kind,seq,effective_tick FROM persona_core_events')
    kind,seq,tick=bare.fetchone()
    assert kind=='evolution' and seq is not None and tick is None
    bare.execute("SELECT count(*) FROM pg_constraint WHERE conrelid='persona_core_events'::regclass AND contype='u'")
    assert bare.fetchone()[0]==1   # only the source_event_id key remains
    bare.execute("SELECT to_regclass('persona_core_links') IS NOT NULL")
    assert bare.fetchone()[0]


def test_migration_refuses_a_schema_without_the_season_constraint(bare):
    bare.execute("ALTER TABLE persona_core_events DROP CONSTRAINT persona_core_events_persona_type_persona_id_season_key")
    with pytest.raises(psycopg.errors.RaiseException): apply(bare,EXTENSIONS)


def test_migration_finds_a_renamed_season_constraint(bare):
    bare.execute('''ALTER TABLE persona_core_events RENAME CONSTRAINT
                   persona_core_events_persona_type_persona_id_season_key TO some_other_name''')
    apply(bare,EXTENSIONS)
    bare.execute("SELECT count(*) FROM pg_constraint WHERE conname='some_other_name'")
    assert bare.fetchone()[0]==0


def test_second_evolution_refused_override_in_same_season_allowed(cur):
    p=store.ensure(cur,ident(),'generated',2027)
    after=store.change(cur,ident(),evolution(p))
    with pytest.raises(ConflictError):
        store.change(cur,ident(),evolution(after,event_id='second',source_event_id='other',remove=after['traits'][0]))
    o=store.override(cur,ident(),jr(after))
    assert o['revision']==after['revision']+1 and o['last_changed_season']==2028
    assert store.read(cur,ident())==o
    cur.execute("SELECT kind,effective_tick,actor FROM persona_core_events ORDER BY seq")
    assert cur.fetchall()==[('evolution',None,None),('override',4,'JR')]
    # The partial index itself still refuses a second evolution row in one season.
    with pytest.raises(psycopg.errors.UniqueViolation):
        cur.execute('''INSERT INTO persona_core_events(persona_type,persona_id,event_id,source_event_id,season,
                       request,before_state,after_state) VALUES ('player',%s,'raw','raw-source',2028,'{}','{}','{}')''',
                    (p['persona_id'],))


def test_override_replay_and_stale_revision(cur):
    p=store.ensure(cur,ident(),'generated',2027)
    r=jr(p)
    o=store.override(cur,ident(),r)
    assert store.override(cur,ident(),r)==o
    with pytest.raises(ConflictError): store.override(cur,ident(),dict(r,reason='changed'))
    with pytest.raises(ConflictError): store.override(cur,ident(),jr(p,override_id='jr-2'))   # stale revision
    with pytest.raises(NotFoundError): store.override(cur,ident('absent'),jr(p))
    assert store.read(cur,ident())==o


def test_adopt_in_place_leaves_legacy_rows_and_replays(cur):
    seed_legacy(cur,'writer','ESPN',(7,9,8,2,2,2),['SPOTLIGHT_SEEKER','HOT_TAKER','QUOTABLE','FRONT_RUNNER'])
    cur.execute("SELECT id,trait_id,active,created_at FROM persona_traits WHERE persona_id='ESPN' ORDER BY id")
    before=cur.fetchall()
    a=store.adopt_legacy(cur,'test-universe','writer','ESPN','real',2026,reviewer='JR',reason='adopt writers')
    assert a['persona_id']=='ESPN' and a['origin']=='real' and a['traits'][0]=='SPOTLIGHT_SEEKER'
    cur.execute("SELECT id,trait_id,active,created_at FROM persona_traits WHERE persona_id='ESPN' ORDER BY id")
    assert cur.fetchall()==before
    assert store.read(cur,Identity.adopted('test-universe','writer','ESPN'))==a
    assert store.adopt_legacy(cur,'test-universe','writer','ESPN','real',2027,reviewer='JR',reason='again')==a
    with pytest.raises(ConflictError):
        store.adopt_legacy(cur,'other-universe','writer','ESPN','real',2026,reviewer='JR',reason='collide')
    with pytest.raises(NotFoundError):
        store.adopt_legacy(cur,'test-universe','writer','NOBODY','real',2026,reviewer='JR',reason='missing')
    cur.execute("SELECT kind,actor,before_state->'traits' FROM persona_core_events WHERE persona_id='ESPN'")
    assert cur.fetchall()==[('adoption','JR',['SPOTLIGHT_SEEKER','HOT_TAKER','QUOTABLE','FRONT_RUNNER'])]


def test_link_writes_new_profile_and_receipt_and_refuses_duplicate(cur):
    p=store.ensure(cur,ident(),'generated',2027)
    coach=Identity('test-universe','coach','person:one')
    c=store.link_role_change(cur,ident(),coach,2034,tick=2,reason='retired into coaching')
    assert c['sliders']==p['sliders'] and set(c['traits'])<=set(p['traits'])
    assert c['links']==[{'direction':'from','role':'player','persona_id':p['persona_id']}]
    assert store.read(cur,coach)==c and store.read(cur,ident())==p
    cur.execute("SELECT from_id,to_id,effective_season,effective_tick FROM persona_core_links")
    assert cur.fetchall()==[(p['persona_id'],coach.persona_id,2034,2)]
    cur.execute("SELECT DISTINCT reason FROM persona_trait_history WHERE persona_type='coach'")
    assert cur.fetchall() in ([('role change from player',)],[])
    with pytest.raises(ConflictError): store.link_role_change(cur,ident(),coach,2034,reason='again')
    with pytest.raises(ConflictError):
        store.link_role_change(cur,ident(),Identity('test-universe','coach','person:two'),2034,reason='second coach')
    with pytest.raises(PersonalityError): store.link_role_change(cur,ident(),ident('x'),2034,reason='same role')
    with pytest.raises(NotFoundError): store.link_role_change(cur,ident('absent'),coach,2034,reason='absent')


def test_history_and_read_as_of_across_adoption_override_evolution(cur):
    from newsroom.personas import seed_persona
    s=seed_persona('gm','8')
    seed_legacy(cur,'gm','8',tuple(s['sliders'].values()),s['traits'])
    gm=Identity.adopted('test-universe','gm','8')
    a=store.adopt_legacy(cur,'test-universe','gm','8','generated',2026,reviewer='JR',reason='adopt gms')
    o=store.override(cur,gm,jr(a,season=2027,tick=6))
    e=store.change(cur,gm,evolution(o,season=2027,tick=9))
    h=store.history(cur,gm)
    assert [r['kind'] for r in h]==['adoption','override','evolution']
    assert store.read_as_of(cur,gm,2025,32) is None
    assert store.read_as_of(cur,gm,2026,32)==a
    assert store.read_as_of(cur,gm,2027,5)==a
    assert store.read_as_of(cur,gm,2027,6)==o
    assert store.read_as_of(cur,gm,2027,None)==e
    coach=Identity('test-universe','coach','person:gm-8')
    store.link_role_change(cur,gm,coach,2028,reason='moved to coaching')
    assert [r['kind'] for r in store.history(cur,coach)]==['adoption','override','evolution','role_link']
    assert [r['kind'] for r in store.history(cur,gm)][-1]=='role_link'
    assert store.read_as_of(cur,gm,2030,None)==e


def test_extension_helpers_roll_back_on_failure_and_never_commit(cur):
    p=store.ensure(cur,ident(),'generated',2027)
    cur.execute('ALTER TABLE persona_core_events ADD CONSTRAINT reject_override CHECK (kind <> \'override\')')
    with pytest.raises(psycopg.errors.CheckViolation): store.override(cur,ident(),jr(p))
    assert store.read(cur,ident())==p
    cur.execute('SAVEPOINT caller_checkpoint')
    store.link_role_change(cur,ident(),Identity('test-universe','coach','c'),2030,reason='fixture')
    cur.execute('ROLLBACK TO SAVEPOINT caller_checkpoint')
    cur.execute('SELECT count(*) FROM persona_core_links')
    assert cur.fetchone()[0]==0
    assert cur.connection.info.transaction_status==psycopg.pq.TransactionStatus.INTRANS
