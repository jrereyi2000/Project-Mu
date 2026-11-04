"""Pure lifecycle contracts (docs/done/PERSONALITY_DRAFT_INTEGRATION_PLAN.md §4). No database."""
from copy import deepcopy
import uuid

import pytest

from engines import prospect_ids
from personality import core, lifecycle as L

UNIVERSE_UUID = uuid.UUID('11111111-2222-3333-4444-555555555555')
START = dict(season=2026, phase='league_start', sequence=0)


def snapshot(players=None, league='LEAGUE-1', draft_class=True):
    players = players if players is not None else [
        dict(pgid=101, status='Signed', first_name='Tom', last_name='Starter'),
        dict(pgid=102, status='PracticeSquad', first_name='Pat', last_name='Squad'),
        dict(pgid=103, status='FreeAgent', first_name='Fred', last_name='Agent'),
        dict(pgid=201, status='Draft', first_name='Danny', last_name='Prospect'),
        dict(pgid=202, status='Draft', first_name='Late', last_name='Undrafted'),
        dict(pgid=901, status='Retired', first_name='Old', last_name='Timer'),
    ]
    snap = dict(meta=dict(game_year=27, reader_version=5, read_at='2026-11-04T00:00:00Z', source='/x/save'),
                franchise=dict(league_id=league), season=dict(calendar_year=2026), players=players)
    if draft_class:
        snap['draft_class'] = [dict(pgid=p['pgid'], initial_draft_rank=i + 1)
                               for i, p in enumerate(players) if p['status'] == 'Draft']
    return snap


def universe(snap=None, **kw):
    return L.new_universe('LEAGUE-1', L.snapshot_sha256(snap or snapshot()), 2026, universe_uuid=UNIVERSE_UUID, **kw)


def receipt(scope, origin='generated'):
    return dict(origin=origin, source_ref='fixture', source_sha256='0' * 64, reviewer='JR', reason='fixture',
                reviewed_at='2026-11-04', effective_at='2026-11-04', scope=scope)


def classes(*extra):
    return dict(receipts=[receipt('roster:2026'), receipt('class:2027'), *extra])


def manifest_for(snap=None, cls=None, staff=None, existing=None, cutoff=START, uni=None):
    snap = snap or snapshot()
    inv = L.validate_inventory(snap, cls if cls is not None else classes(), universe=uni or universe(snap),
                               cutoff=cutoff, staff=staff)
    cat = core.legacy.load_traits()
    return L.plan_lifecycle(inv, existing or {}, core_version=core.CORE_VERSION,
                            catalogue_version=core.catalogue_version(cat), catalogue=cat)


def by_pgid(manifest, pgid):
    return [p for p in manifest['people'] if p['source'].get('pgid') == pgid]


def test_manifest_is_deterministic_and_volatile_fields_do_not_change_hash():
    m1 = manifest_for()
    snap = snapshot()
    snap['meta']['read_at'] = '2030-01-01T00:00:00Z'
    m2 = manifest_for(snap, uni=universe())
    assert m1 == m2
    assert m1['manifest_sha256'] == L.finalize_manifest(m1)['manifest_sha256']
    assert L.validate_manifest(m1)['valid']


def test_whole_starting_roster_and_class_classified_generated_by_two_receipts():
    m = manifest_for()
    init = [p for p in m['people'] if p['action'] == 'initialize']
    assert sorted(p['source']['pgid'] for p in init) == [101, 102, 103, 201, 202]
    assert all(p['classification'] == 'generated' for p in init)
    assert all(3 <= len(p['proposed_profile']['traits']) <= 5 for p in init)
    assert {i['kind'] for i in m['issues']} == {'unsupported_status'}
    for p in init:   # generated four-trait/category/conflict/budget validity through the core validator
        assert sum(p['proposed_profile']['sliders'].values()) == 30
        assert len(p['proposed_profile']['traits']) == 4


def test_prospect_adopts_scouting_prospect_uid_and_roster_id_is_universe_scoped():
    m = manifest_for()
    prospect = by_pgid(m, 201)[0]
    uid = str(prospect_ids.uid_from_birth_key(prospect_ids.birth_key(2027, 201)))
    assert prospect['source_person_id'] == uid
    assert prospect['birth_key'] == 'source:' + uid
    assert prospect['source']['draft_season'] == 2027
    starter = by_pgid(m, 101)[0]
    assert starter['source_person_id'] == L.roster_person_id(f'mu:{UNIVERSE_UUID}', 2026, 101)
    assert {'namespace': 'save-pgid:1', 'external_id': '101'} in starter['aliases']


def test_two_universes_same_pgid_have_different_personas():
    other = L.new_universe('LEAGUE-1', L.snapshot_sha256(snapshot()), 2026,
                           universe_uuid=uuid.UUID('99999999-2222-3333-4444-555555555555'))
    a = by_pgid(manifest_for(), 101)[0]
    b = by_pgid(manifest_for(uni=other), 101)[0]
    assert a['persona_id'] != b['persona_id']
    pa, pb = by_pgid(manifest_for(), 201)[0], by_pgid(manifest_for(uni=other), 201)[0]
    assert pa['source_person_id'] == pb['source_person_id'] and pa['persona_id'] != pb['persona_id']


def test_missing_receipt_and_unknown_stay_pending_never_unknown_profiles():
    m = manifest_for(cls=dict(receipts=[receipt('roster:2026')]))
    for pgid in (201, 202):
        p = by_pgid(m, pgid)[0]
        assert p['action'] == 'pending' and p['reason'] == 'no_classification_receipt'
        assert 'proposed_profile' not in p
    m = manifest_for(cls=classes(receipt('class:2027', 'unknown')))
    assert by_pgid(m, 201)[0]['action'] == 'quarantine'      # generated + unknown for one scope disagree
    m = manifest_for(cls=dict(receipts=[receipt('roster:2026'), receipt('class:2027', 'unknown')]))
    assert by_pgid(m, 201)[0]['action'] == 'pending'
    assert L.unresolved(m)


def test_real_player_classification_quarantines():
    m = manifest_for(cls=classes(receipt('person:player:' + L.roster_person_id(f'mu:{UNIVERSE_UUID}', 2026, 101),
                                         'real')))
    assert by_pgid(m, 101)[0]['action'] == 'quarantine'


def test_real_imported_class_is_generated_at_entry_through_sidecar():
    rows = [dict(source_id='NCAA-77', name='Real Name'), dict(source_id='NCAA-78', name='Other'), dict(name='NoId')]
    counter = iter(uuid.UUID(int=i) for i in range(1, 10))
    car = L.class_sidecar(2027, 'import.csv', rows, allocate=lambda: next(counter))
    assert car['unmapped'] == [2]
    again = L.class_sidecar(2027, 'import.csv', list(reversed(rows)) + [dict(source_id='NCAA-77', name='Renamed')],
                            existing={k: v for k, v in car.items() if k != 'unmapped'},
                            allocate=lambda: next(counter))
    assert L.sidecar_person_id(again, 'NCAA-77') == L.sidecar_person_id(car, 'NCAA-77')
    assert len(again['entries']) == 2
    assert L.sidecar_person_id(car, None) is None and L.sidecar_person_id(car, 'NCAA-99') is None
    with pytest.raises(L.LifecycleError):
        L.class_sidecar(2028, 'x', rows, existing={k: v for k, v in car.items() if k != 'unmapped'})


def test_duplicate_pgid_and_missing_pgid():
    players = [dict(pgid=101, status='Signed'), dict(pgid=101, status='FreeAgent'), dict(pgid=None, status='Signed')]
    snap = snapshot(players)
    m = manifest_for(snap, uni=universe(snap))
    assert [p['action'] for p in by_pgid(m, 101)] == ['quarantine', 'quarantine']
    assert by_pgid(m, None)[0]['reason'] == 'missing_pgid'
    assert {'duplicate_pgid', 'missing_pgid'} <= {i['kind'] for i in m['issues']}


def test_league_stamp_snapshot_hash_and_cutoff_rejections():
    with pytest.raises(L.LifecycleError, match='does not match universe league'):
        manifest_for(snapshot(league='OTHER'), uni=universe())
    changed = snapshot()
    changed['players'][0]['pgid'] = 555
    with pytest.raises(L.LifecycleError, match='registered starting snapshot'):
        manifest_for(changed, uni=universe())
    with pytest.raises(L.LifecycleError, match='precedes'):
        manifest_for(cutoff=dict(season=2025, phase='draft', sequence=0))
    with pytest.raises(L.LifecycleError):
        L.validate_cutoff(dict(season=2026, phase='lunch', sequence=0))
    assert L.cutoff_key(dict(season=2026, phase='draft', sequence=0)) > \
        L.cutoff_key(dict(season=2026, phase='playoffs', sequence=9))


def test_version_rejection():
    snap = snapshot()
    inv = L.validate_inventory(snap, classes(), universe=universe(snap), cutoff=START)
    with pytest.raises(L.LifecycleError, match='core version'):
        L.plan_lifecycle(inv, {}, core_version='old', catalogue_version='x')
    with pytest.raises(L.LifecycleError, match='catalogue'):
        L.plan_lifecycle(inv, {}, core_version=core.CORE_VERSION, catalogue_version='0' * 64)


def test_jr_edit_changes_hash_and_must_stay_valid():
    m = manifest_for()
    edited = deepcopy(m)
    person = by_pgid(edited, 101)[0]
    sliders = person['proposed_profile']['sliders']
    up, down = sorted(sliders, key=lambda s: sliders[s])[0], sorted(sliders, key=lambda s: -sliders[s])[0]
    sliders[up] += 1
    sliders[down] -= 1
    assert not L.validate_manifest(edited)['valid']                 # stale hash until rehashed
    rehashed = L.finalize_manifest(edited)
    result = L.validate_manifest(rehashed)
    assert result['valid'] and result['edited'] == [person['source_person_id']]
    assert rehashed['manifest_sha256'] != m['manifest_sha256']
    full = L.proposed_profile(rehashed, by_pgid(rehashed, 101)[0])
    assert full['sliders'] == sliders and full['origin'] == 'generated'
    bad = deepcopy(rehashed)
    by_pgid(bad, 101)[0]['proposed_profile']['traits'] = ['NOT_A_TRAIT', 'X', 'Y']
    assert not L.validate_manifest(bad)['valid']


def _existing(m, *pgids, classification='generated'):
    people, bindings = [], []
    for p in m['people']:
        if p['source'].get('pgid') in pgids:
            people.append(dict(role='player', source_person_id=p['source_person_id'], classification=classification,
                               state='ready', revision=0, profile_hash=p['proposed_profile_hash']))
            bindings += [dict(role='player', namespace=a['namespace'], external_id=a['external_id'],
                              source_person_id=p['source_person_id'], valid_to=None) for a in p['aliases']]
    return dict(universe_id=m['universe']['universe_id'], people=people, bindings=bindings)


def test_replay_against_applied_state_retains_and_never_redraws():
    m = manifest_for()
    existing = _existing(m, 101, 102, 103, 201, 202)
    replay = manifest_for(existing=existing)
    assert {p['action'] for p in replay['people'] if p['source'].get('pgid') != 901} == {'retain'}
    assert all('proposed_profile' not in p for p in replay['people'])
    conflict = manifest_for(existing=_existing(m, 101, classification='real'))
    assert by_pgid(conflict, 101)[0]['reason'] == 'classification_conflict'


def test_alias_conflict_legacy_and_unmanaged_collisions_quarantine():
    m = manifest_for()
    starter = by_pgid(m, 101)[0]
    other = str(uuid.uuid4())
    existing = dict(bindings=[dict(role='player', namespace='save-pgid:1', external_id='101', source_person_id=other,
                                   valid_to=None)])
    assert by_pgid(manifest_for(existing=existing), 101)[0]['reason'] == 'alias_conflict'
    legacy = dict(legacy_persona_ids={'player': [starter['persona_id']]})
    assert by_pgid(manifest_for(existing=legacy), 101)[0]['reason'] == 'legacy_collision'
    unmanaged = dict(core_profiles=[dict(role='player', persona_id=starter['persona_id'])])
    assert by_pgid(manifest_for(existing=unmanaged), 101)[0]['reason'] == 'unmanaged_core_profile'


def test_later_checkpoint_roster_resolves_by_alias_handoff_only():
    m = manifest_for()
    existing = _existing(m, 201)
    later = snapshot([dict(pgid=201, status='Signed'), dict(pgid=777, status='Signed')], draft_class=False)
    cutoff = dict(season=2027, phase='post_draft', sequence=0)
    replay = manifest_for(later, existing=existing, cutoff=cutoff, uni=universe())
    rookie = by_pgid(replay, 201)[0]
    assert rookie['action'] == 'retain' and rookie['source_person_id'] == by_pgid(m, 201)[0]['source_person_id']
    stranger = by_pgid(replay, 777)[0]
    assert stranger['action'] == 'pending' and stranger['reason'] == 'resolve_by_alias'


def test_handoff_rules():
    spid = str(uuid.uuid4())
    bindings = [dict(role='player', namespace='save-pgid:1', external_id='201', source_person_id=spid, valid_to=None)]
    same = dict(namespace='save-pgid:1', external_id='201')
    assert L.validate_handoff(dict(evidence='same_pgid_lifetime', **{'from': same, 'to': same}),
                              bindings)['status'] == 'verified'
    db = dict(namespace='db-player', external_id='55')
    assert L.validate_handoff(dict(evidence='name_match', **{'from': same, 'to': db}), bindings)['reason'] == \
        'name_match_is_candidate_only'
    assert L.validate_handoff(dict(evidence='import_receipt', receipt={}, **{'from': same, 'to': db}),
                              bindings)['status'] == 'pending'
    ok = dict(post_write_verified=True, reviewer='JR', snapshot_sha256='a' * 64)
    assert L.validate_handoff(dict(evidence='import_receipt', receipt=ok, **{'from': same, 'to': db}),
                              bindings)['status'] == 'verified'
    taken = bindings + [dict(role='player', namespace='db-player', external_id='55', source_person_id='x',
                             valid_to=None)]
    assert L.validate_handoff(dict(evidence='import_receipt', receipt=ok, **{'from': same, 'to': db}),
                              taken)['status'] == 'conflict'
    unknown = dict(namespace='save-pgid:1', external_id='999')
    assert L.validate_handoff(dict(evidence='same_pgid_lifetime', **{'from': unknown, 'to': unknown}),
                              bindings)['reason'] == 'origin_unresolved'


def coach_entry(spid, **kw):
    entry = dict(role='coach', source_person_id=spid, aliases=[dict(namespace='db-coach', external_id='7')])
    entry.update(kw)
    return entry


def test_staff_real_coach_pending_until_reviewed_profile_and_generated_needs_creation_receipt():
    spid = str(uuid.UUID(int=42))
    m = manifest_for(staff=[coach_entry(spid)], cls=classes(receipt(f'person:coach:{spid}', 'real')))
    coach = [p for p in m['people'] if p['role'] == 'coach'][0]
    assert coach['action'] == 'pending' and coach['reason'] == 'real_profile_missing'
    profile = dict(sliders=dict(temperament=6, sociability=5, confidence=5, openness=5, warmth=5, drive=4), traits=[])
    m = manifest_for(staff=[coach_entry(spid, profile=profile)], cls=classes(receipt(f'person:coach:{spid}', 'real')))
    coach = [p for p in m['people'] if p['role'] == 'coach'][0]
    assert coach['action'] == 'initialize' and coach['proposed_profile']['sliders']['temperament'] == 6
    m = manifest_for(staff=[coach_entry(spid)], cls=classes(receipt('staff:coach')))
    assert [p for p in m['people'] if p['role'] == 'coach'][0]['reason'] == 'missing_creation_receipt'
    m = manifest_for(staff=[coach_entry(None)], cls=classes())
    assert [p for p in m['people'] if p['role'] == 'coach'][0]['reason'] == 'identity_unresolved'


def test_hidden_profile_stays_restricted():
    m = manifest_for()
    assert m['visibility'] == 'restricted'
    assert all('catalogue' not in (p.get('proposed_profile') or {}) for p in m['people'])


def test_compare_manifests():
    a = manifest_for()
    assert L.compare_manifests(a, a)['same']
    b = manifest_for(existing=_existing(a, 101))
    diff = L.compare_manifests(a, b)
    assert not diff['same'] and diff['changed']
