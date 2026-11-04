"""Staff person lifecycle (docs/in_progress/PERSONALITY_DRAFT_INTEGRATION_PLAN.md §2.5, §4; U4).

Offline: the in-memory SQLite build of the persona/core/lifecycle migrations from the store tests, plus a small staff
schema for engines/fire_coordinator.fire/undo. coaching_carousel.hiring_cycle itself needs PostgreSQL (DISTINCT ON,
= ANY); its events are exercised here through staff_lifecycle with the same event shapes, and its draws are pure and
unchanged (coordinator_candidacy, opening_appeal, pick).
"""
import uuid

import pytest

from engines import coaching_carousel as cc, fire_coordinator as fc
from personality import lifecycle as L, lifecycle_store as S, store
from tests.test_personality_lifecycle import START, manifest_for, receipt, universe
from tests.test_personality_lifecycle_store import Cursor, build_db, count

STAFF_DDL = """
CREATE TABLE teams (id INTEGER PRIMARY KEY, city TEXT, name TEXT, abbreviation TEXT);
CREATE TABLE gm_team_settings (team_id INTEGER, user_controlled BOOLEAN);
CREATE TABLE coordinators (id INTEGER PRIMARY KEY AUTOINCREMENT, coordinator_name TEXT, side TEXT, scheme TEXT,
    playbook TEXT, pass_lean TEXT, blitz_rate TEXT, man_coverage TEXT, origin_notes TEXT);
CREATE TABLE coordinator_tenures (coordinator_id INTEGER, team_id INTEGER, role TEXT, start_season INTEGER,
    end_season INTEGER);
CREATE TABLE coordinator_hot_seat (season INTEGER, team_id INTEGER, role TEXT, coordinator_id INTEGER, score REAL,
    fired BOOLEAN, phase TEXT, inputs TEXT);
CREATE TABLE staff_moves (season INTEGER, team_id INTEGER, role TEXT, action TEXT, person TEXT, coordinator_id INTEGER,
    coach_id INTEGER, source TEXT, notes TEXT, inputs TEXT);
INSERT INTO teams VALUES (1, 'Carolina', 'Panthers', 'CAR');
INSERT INTO gm_team_settings VALUES (1, 1);
INSERT INTO coordinators VALUES (1, 'Old Coordinator', 'OFFENSIVE', 'West Coast', 'Panthers', 'Medium', NULL, NULL, NULL);
INSERT INTO coordinators VALUES (2, 'Free Agent Coach', 'OFFENSIVE', 'Air Raid', 'X', 'High', NULL, NULL, NULL);
INSERT INTO coordinator_tenures VALUES (1, 1, 'OC', 2024, NULL);
"""
SEASON = 2026
CUTOFF = dict(season=2026, phase='resign', sequence=0)


@pytest.fixture
def cur(monkeypatch):
    conn = build_db()
    conn.executescript(STAFF_DDL)
    c = Cursor(conn)
    monkeypatch.setattr(S, '_columns',
                        lambda cursor, table: {r[1] for r in cursor.conn.execute(f'PRAGMA table_info({table})')})
    monkeypatch.setattr(fc, 'phase_for', lambda cur, season, team_id: 'BLACK_MONDAY')
    S.register_universe(c, universe())
    return c


UID = universe()['universe_id']


def ctx():
    return dict(universe_id=UID, cutoff=CUTOFF, receipt=receipt('staff:coach'))


def created(event, namespace, external_id, **kw):
    return dict(kind='created', event=event, creator='coaching_carousel:OUTSIDE', namespace=namespace,
                external_id=external_id, **kw)


def coach(cur, namespace, external_id):
    return S.resolve_person(cur, UID, 'coach', namespace, str(external_id), cutoff=CUTOFF, feature='x', current=True)


def test_observe_touches_nothing():
    info = cc.staff_lifecycle(None, [created('carousel:2026:CAR:OC', 'db-coordinator', 5)])
    assert info == dict(mode='observe', events=1, created=1, pending=[], quarantined=[])


def test_fictional_outsider_seeds_once_from_its_creation_event(cur):
    ev = [created('carousel:2026:CAR:HC', 'db-coach', 40)]
    info = cc.staff_lifecycle(cur, ev, ctx())
    assert not info['pending'] and not info['quarantined']
    r = coach(cur, 'db-coach', 40)
    spid = cc.staff_person_id(UID, 'carousel:2026:CAR:HC')
    assert r['status'] == 'ready' and r['identity']['source_person_id'] == spid
    first = store.read(cur, L.identity(UID, 'coach', spid))
    cc.staff_lifecycle(cur, ev, ctx())                                  # a second run changes nothing
    assert count(cur, 'persona_core_profiles') == 1
    assert store.read(cur, L.identity(UID, 'coach', spid)) == first
    # the identity comes from the event, not the name/team/job: another event is another person
    assert cc.staff_person_id(UID, 'carousel:2027:CAR:HC') != spid


def test_coordinator_promoted_to_head_coach_keeps_one_identity(cur):
    cc.staff_lifecycle(cur, [created('carousel:2026:1:OC:promo', 'db-coordinator', 7)], ctx())
    before = coach(cur, 'db-coordinator', 7)
    info = cc.staff_lifecycle(cur, [dict(kind='promoted', event='carousel:2027:ATL:HC', coordinator_id=7,
                                         namespace='db-coach', external_id=55)], ctx())
    assert not info['pending']
    after = coach(cur, 'db-coach', 55)
    assert after['identity'] == before['identity'] and count(cur, 'persona_core_profiles') == 1


def test_real_coach_is_pending_until_reviewed_manifest_and_retread_keeps_identity(cur):
    info = cc.staff_lifecycle(cur, [dict(kind='promoted', event='e', coordinator_id=99, namespace='db-coach',
                                         external_id=12),
                                    dict(kind='retained', event='r', namespace='db-coach', external_id=13)], ctx())
    assert [p['reason'] for p in info['pending']] == ['coordinator identity unresolved', 'identity unresolved']
    assert count(cur, 'persona_core_profiles') == 0                    # never seeded without a reviewed profile
    spid = str(uuid.UUID(int=5))
    profile = dict(sliders=dict(temperament=6, sociability=5, confidence=5, openness=5, warmth=5, drive=4), traits=[])
    S.initialize_person(cur, UID, 'coach', spid, 'real', 2026, cutoff=START, receipt=receipt(f'person:coach:{spid}',
                                                                                              'real'),
                        aliases=[dict(namespace='db-coach', external_id='13')], profile=profile)
    info = cc.staff_lifecycle(cur, [dict(kind='retained', event='carousel:2027:NYG:HC', namespace='db-coach',
                                         external_id=13)], ctx())
    assert not info['pending'] and coach(cur, 'db-coach', 13)['view']['origin'] == 'real'


def test_same_cycle_stand_in_is_archived_not_deleted(cur):
    cc.staff_lifecycle(cur, [created('carousel:2026:1:OC:promo', 'db-coordinator', 8),
                             dict(kind='deleted', event='carousel:2026:CAR:OC:undone', namespace='db-coordinator',
                                  external_id=8)], ctx())
    spid = cc.staff_person_id(UID, 'carousel:2026:1:OC:promo')
    cur.execute('SELECT state FROM persona_lifecycle_people WHERE source_person_id = %s', (spid,))
    assert cur.fetchone()[0] == 'archived'
    assert count(cur, 'persona_core_profiles') == 1                    # profile and receipt history kept
    later = dict(season=2027, phase='preseason', sequence=0)
    assert S.resolve_person(cur, UID, 'coach', 'db-coordinator', '8', cutoff=later, feature='x')['status'] == 'missing'


def test_name_matched_existing_row_and_missing_provenance_quarantine(cur):
    info = cc.staff_lifecycle(cur, [created('carousel:2026:CAR:HC', 'db-coach', 41, reused_row=True)], ctx())
    assert info['quarantined'][0]['reason'].startswith('invented name matched')
    assert count(cur, 'persona_core_profiles') == 0
    with pytest.raises(L.LifecycleError):
        cc.staff_lifecycle(cur, [], dict(ctx(), receipt=receipt('staff:coach', 'real')))
    with pytest.raises(L.LifecycleError):
        cc.staff_lifecycle(cur, [], dict(ctx(), receipt=receipt('class:2027')))
    cc.staff_lifecycle(cur, [created('a', 'db-coach', 42)], ctx())
    info = cc.staff_lifecycle(cur, [created('b', 'db-coach', 42)], ctx())   # alias already someone else's
    assert info['quarantined'] and count(cur, 'persona_core_profiles') == 1     # atomic: the second never lands


def test_jr_named_replacement_is_created_and_undo_archives(cur):
    out = fc.fire(cur, SEASON, 'CAR', 'OC', replacement='Brand New', log=lambda m: None, personality=ctx())
    assert out['personality']['created'] == 1 and not out['personality']['quarantined']
    cid = out['replacement']['coordinator_id']
    r = coach(cur, 'db-coordinator', cid)
    assert r['identity']['source_person_id'] == cc.staff_person_id(UID, 'jr-fire:2026:CAR:OC')
    undone = fc.undo(cur, SEASON, 'CAR', 'OC', log=lambda m: None, personality=ctx())
    assert undone['personality']['events'] == 1
    cur.execute('SELECT state FROM persona_lifecycle_people')
    assert cur.fetchone()[0] == 'archived'


def test_jr_rehire_keeps_identity_and_observe_default(cur):
    S.initialize_person(cur, UID, 'coach', cc.staff_person_id(UID, 'old'), 'generated', 2026, cutoff=START,
                        receipt={'creator': 'x'}, aliases=[dict(namespace='db-coordinator', external_id='2')])
    out = fc.fire(cur, SEASON, 'CAR', 'OC', replacement='Free Agent Coach', log=lambda m: None, personality=ctx())
    assert out['replacement']['source'] == 'REHIRED' and not out['personality']['pending']
    assert count(cur, 'persona_core_profiles') == 1


def test_fire_without_personality_is_observe_only(cur):
    before = [count(cur, t) for t in ('persona_core_profiles', 'persona_lifecycle_people')]
    out = fc.fire(cur, SEASON, 'CAR', 'OC', replacement='Another', log=lambda m: None)
    assert out['personality']['mode'] == 'observe' and out['replacement']['source'] == 'OUTSIDE'
    assert [count(cur, t) for t in ('persona_core_profiles', 'persona_lifecycle_people')] == before


def test_gm_owner_scout_inventory_and_cross_role_block():
    spid = str(uuid.UUID(int=77))
    staff = [dict(role=r, source_person_id=None, aliases=[dict(namespace=f'db-{r}', external_id='1')])
             for r in ('gm', 'owner', 'scout')]
    staff.append(dict(role='coach', source_person_id=spid, creation_receipt={'creator': 'x'},
                      aliases=[dict(namespace='db-coach', external_id='3')]))
    staff.append(dict(role='scout', source_person_id=spid, creation_receipt={'creator': 'x'},
                      aliases=[dict(namespace='db-scout', external_id='9')]))
    m = manifest_for(staff=staff, cls=dict(receipts=[receipt('roster:2026'), receipt('class:2027'),
                                                     receipt('staff:coach'), receipt('staff:scout')]))
    by_role = {}
    for p in m['people']:
        if p['role'] != 'player':
            by_role.setdefault(p['role'], []).append(p)
    assert all(p['action'] == 'pending' for r in ('gm', 'owner') for p in by_role[r])
    assert {p['reason'] for p in by_role['scout']} == {'identity_unresolved', 'cross_role_review'}
    assert by_role['coach'][0]['reason'] == 'cross_role_review'


def test_neutral_draws_unchanged():
    assert cc.coordinator_candidacy(1, 1, 2, True, 3) == cc.coordinator_candidacy(1, 1, 2, True, 3)
    pool = [dict(key='a', score=0.5), dict(key='b', score=0.2)]
    assert cc.pick(pool, '2026:CAR:HC') is cc.pick(pool, '2026:CAR:HC')
