"""Draft ingestion and player handoff (docs/in_progress/PERSONALITY_DRAFT_INTEGRATION_PLAN.md §2.4, §4; U3).

Offline: the in-memory SQLite build of the persona/core/lifecycle migrations from the store tests, plus
draft_prospects and the scouting prospect-ID tables (2027_01_21). Exercises load_save.load_personality,
load_capture.class_personality, draft_board's handoff candidates and scouting's observe check.
"""
import csv
import json
import uuid

import pytest

from engines import prospect_ids, scouting_engine
from personality import lifecycle as L, lifecycle_store as S, store
from pipeline import draft_board, load_capture, load_save
from pipeline.ingest_prospects import Prospect, load_prospects_from_csv
from tests.test_inbox_store import sqlite_ddl
from tests.test_personality_lifecycle import START, by_pgid, classes, manifest_for, receipt, snapshot, universe
from tests.test_personality_lifecycle_store import MIGRATIONS, Cursor, build_db, count, review

DRAFT_DDL = """CREATE TABLE draft_prospects (id INTEGER PRIMARY KEY AUTOINCREMENT, draft_season INT NOT NULL,
    projected_pick INT, projected_round TEXT, round_pick INT, full_name TEXT NOT NULL, position TEXT, college TEXT,
    height_in INT, weight INT, age INT, ovr INT, archetype TEXT, notes TEXT, source_capture TEXT, player_id INT)"""


class DraftCursor(Cursor):
    def executemany(self, sql, rows):
        for row in rows:
            self.execute(sql, row)
        return self


@pytest.fixture
def cur(monkeypatch):
    conn = build_db()
    conn.execute(DRAFT_DDL)
    conn.executescript(sqlite_ddl((MIGRATIONS / '2027_01_21_scouting_reports.sql').read_text()))
    c = DraftCursor(conn)
    monkeypatch.setattr(S, '_columns',
                        lambda cursor, table: {r[1] for r in cursor.conn.execute(f'PRAGMA table_info({table})')})
    return c


def players_of(snap, base=1000):
    """load_players' {pgid: (player id, team, position, name)} for the loaded statuses."""
    return {p['pgid']: (base + p['pgid'], None, 'QB', 'x') for p in snap['players']
            if p['status'] in load_save.LOADED_STATUSES}


def context(manifest, **extra):
    return dict(manifest=manifest, review=review(manifest), **extra)


def save_snapshot(players=None, **kw):
    snap = snapshot(players, **kw)
    for p in snap['players']:
        p.setdefault('first_name', 'F')
        p.setdefault('last_name', f"P{p['pgid']}")
        p.update(position=p.get('position', 'QB'), college=None, height_in=74, weight_lbs=220, age=22,
                 overall=70, archetype=None)
    for d in snap.get('draft_class') or []:
        d.setdefault('initial_draft_rank', 1)
    return snap


def profiles(cur):
    cur.execute('SELECT persona_id, metadata FROM persona_core_profiles ORDER BY persona_id')
    return cur.fetchall()


# ---------- save loads ----------

def test_observe_without_context_writes_nothing(cur):
    snap = save_snapshot()
    report = {}
    info = load_save.load_personality(cur, snap, 2026, players_of(snap), report)
    assert info == {'mode': 'observe', 'status': 'no universe registered for this league'}
    S.register_universe(cur, universe(snap))
    before = [count(cur, t) for t in ('persona_core_profiles', 'persona_lifecycle_bindings')]
    info = load_save.load_personality(cur, snap, 2026, players_of(snap), {})
    assert info['mode'] == 'observe' and info['pending'] == 5
    assert [count(cur, t) for t in ('persona_core_profiles', 'persona_lifecycle_bindings')] == before


def test_enforce_applies_manifest_and_binds_verified_aliases(cur):
    snap = save_snapshot()
    m = manifest_for(snap, uni=universe(snap))
    board = load_save.draft_board_prospects(snap)
    load_save.write_board(cur, 2027, board, source_capture='CAP1')
    prospect_ids.ensure_prospect_ids(cur, 2027, [dict(prospect_key=f'2027:P{p}:QB', pgid=p) for p in (201, 202)],
                                     'CAP1')
    report = {}
    info = load_save.load_personality(cur, snap, 2026, players_of(snap), report, context(m), board, 'CAP1')
    assert info['batch']['counts']['initialized'] == 5 and info['pending'] == 0
    assert info['bound'] == {'db_player': 3, 'board': 2, 'scout': 2}
    uid = m['universe']['universe_id']
    starter = by_pgid(m, 101)[0]
    assert store.resolve_alias(cur, uid, 'player', 'db-player', '1101').persona_id == starter['persona_id']
    ids = draft_board.board_ids(cur, 2027)
    prospect = by_pgid(m, 201)[0]
    r = S.resolve_person(cur, uid, 'player', 'draft:2027:CAP1', str(ids[1]), cutoff=START, feature='scouting')
    assert r['identity']['persona_id'] == prospect['persona_id']
    assert store.resolve_alias(cur, uid, 'player', 'draft:2027:CAP1', str(ids[1])) is None     # board IDs: lifecycle only
    assert store.resolve_alias(cur, uid, 'player', 'scout:2027', '2027:P201:QB').persona_id == prospect['persona_id']
    # prospects adopt scouting's prospect_uid as source_person_id
    assert prospect_ids.class_ids(cur, 2027)[201][0] == prospect['source_person_id']


def test_kept_board_is_not_mapped_by_rank(cur):
    snap = save_snapshot()
    m = manifest_for(snap, uni=universe(snap))
    info = load_save.load_personality(cur, snap, 2026, players_of(snap), {}, context(m), None, 'CAP1')
    assert info['bound']['board'] == 0 and 'not mapped' in info['board_aliases']


def test_manifest_from_another_snapshot_aborts(cur):
    snap = save_snapshot()
    m = manifest_for(snap, uni=universe(snap))
    other = save_snapshot()
    other['players'][0]['overall'] = 99
    with pytest.raises(load_save.LoadError, match='different snapshot'):
        load_save.load_personality(cur, other, 2026, players_of(other), {}, context(m))
    assert count(cur, 'persona_core_profiles') == 0


def _later(cur, m, players, cutoff, handoffs=None, cls=None):
    snap = save_snapshot(players, draft_class=False)
    export = S.export_state(cur, m['universe']['universe_id'])
    nxt = manifest_for(snap, existing=export, cutoff=cutoff, uni=m['universe'], cls=cls)
    return snap, nxt, load_save.load_personality(cur, snap, cutoff['season'], players_of(snap, 5000), {},
                                                 context(nxt, handoffs=handoffs or []))


def test_drafted_and_udfa_rookies_keep_one_profile_and_trade_never_reseeds(cur):
    snap = save_snapshot()
    m = manifest_for(snap, uni=universe(snap))
    load_save.load_personality(cur, snap, 2026, players_of(snap), {}, context(m))
    before = profiles(cur)
    cutoff = dict(season=2027, phase='post_draft', sequence=0)
    # 201 drafted (Signed), 202 an undrafted free agent signing: neither needs a pick or a team, only the pgid
    _, nxt, info = _later(cur, m, [dict(pgid=201, status='Signed'), dict(pgid=202, status='FreeAgent'),
                                   dict(pgid=101, status='Signed')], cutoff)
    assert info['batch']['counts']['retained'] == 3 and info['bound']['db_player'] == 3
    assert profiles(cur) == before
    uid = m['universe']['universe_id']
    assert store.resolve_alias(cur, uid, 'player', 'db-player', '5201').persona_id == by_pgid(m, 201)[0]['persona_id']
    traded = dict(season=2028, phase='preseason', sequence=0)
    _, _, info = _later(cur, m, [dict(pgid=201, status='Signed'), dict(pgid=101, status='Signed')], traded)
    assert profiles(cur) == before


def test_roster_first_unresolved_rookie_stays_pending(cur):
    snap = save_snapshot()
    m = manifest_for(snap, uni=universe(snap))
    load_save.load_personality(cur, snap, 2026, players_of(snap), {}, context(m))
    cutoff = dict(season=2027, phase='post_draft', sequence=0)
    _, nxt, info = _later(cur, m, [dict(pgid=888, status='Signed')], cutoff)
    assert by_pgid(nxt, 888)[0]['action'] == 'pending'
    assert info['pending'] == 1 and count(cur, 'persona_core_profiles') == 5


def test_import_receipt_handoff_binds_and_name_match_does_not(cur):
    snap = save_snapshot()
    m = manifest_for(snap, uni=universe(snap))
    load_save.load_personality(cur, snap, 2026, players_of(snap), {}, context(m))
    src = dict(namespace='save-pgid:1', external_id='201')
    ok = dict(post_write_verified=True, reviewer='JR', snapshot_sha256='a' * 64)
    handoffs = [dict(evidence='import_receipt', receipt=ok, **{'from': src,
                                                               'to': dict(namespace='save-pgid:2', external_id='9')}),
                dict(evidence='name_match', **{'from': src, 'to': dict(namespace='db-player', external_id='77')}),
                dict(evidence='import_receipt', receipt={}, **{'from': src,
                                                               'to': dict(namespace='db-player', external_id='78')})]
    cutoff = dict(season=2027, phase='preseason', sequence=0)
    _, _, info = _later(cur, m, [dict(pgid=101, status='Signed')], cutoff, handoffs)
    assert info['handoffs'] == {'verified': 1, 'pending': 2}
    uid = m['universe']['universe_id']
    assert store.resolve_alias(cur, uid, 'player', 'save-pgid:2', '9').persona_id == by_pgid(m, 201)[0]['persona_id']
    assert store.resolve_alias(cur, uid, 'player', 'db-player', '77') is None


def test_handoff_conflict_aborts_the_load(cur):
    snap = save_snapshot()
    m = manifest_for(snap, uni=universe(snap))
    load_save.load_personality(cur, snap, 2026, players_of(snap), {}, context(m))
    src = dict(namespace='save-pgid:1', external_id='201')
    taken = dict(namespace='save-pgid:1', external_id='101')        # already 101's
    ok = dict(post_write_verified=True, reviewer='JR', snapshot_sha256='a' * 64)
    with pytest.raises(load_save.LoadError, match='conflict'):
        _later(cur, m, [dict(pgid=101, status='Signed')], dict(season=2027, phase='preseason', sequence=0),
               [dict(evidence='import_receipt', receipt=ok, **{'from': src, 'to': taken})])


def test_load_snapshot_passes_context_and_observes_by_default(monkeypatch):
    calls = []
    for name in ('team_ids', 'load_team_ratings', 'load_player_attributes', 'load_games', 'load_game_lines',
                 'load_team_scores', 'load_season_lines', 'load_contracts', 'load_team_caps', 'load_injuries',
                 'load_highlights', 'load_depth_charts', 'league_settings_report', 'update_league_state'):
        monkeypatch.setattr(load_save, name, lambda *a, **k: {})
    monkeypatch.setattr(load_save, 'load_players', lambda *a, **k: {})
    monkeypatch.setattr(load_save, 'load_draft_class', lambda *a, **k: None)
    monkeypatch.setattr(load_save, 'store_prospect_ratings', lambda *a, **k: 0)
    monkeypatch.setattr(load_save, 'current_week', lambda s: 1)
    monkeypatch.setattr(load_save, 'load_personality', lambda cur, snap, season, players, report, ctx, board, folder:
                        calls.append(ctx))
    snap = dict(meta=dict(game_year=27), season=dict(calendar_year=2026, franchise_year=0, week_type=1,
                                                     week_index=0, stage=0))
    load_save.load_snapshot(None, snap)
    load_save.load_snapshot(None, snap, personality_context={'manifest': {}})
    assert calls == [None, {'manifest': {}}]


def test_cli_context_needs_both_files(tmp_path):
    with pytest.raises(load_save.LoadError):
        load_save.personality_context(manifest_path=str(tmp_path / 'm.json'))
    assert load_save.personality_context() is None


def test_save_board_prospects_carry_pgid_in_memory_only():
    board = load_save.draft_board_prospects(save_snapshot())
    assert [p.source_id for p in board] == ['201', '202']
    assert 'source_id' not in board[0].to_dict()


# ---------- imported (CSV/OCR) classes ----------

def write_csv(path, rows):
    with open(path, 'w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=['projected_pick', 'full_name', 'position', 'college', 'height', 'weight',
                                           'age', 'ovr', 'source_id'])
        w.writeheader()
        for r in rows:
            w.writerow(r)


def csv_rows(order=(0, 1, 2), rename=None):
    base = [dict(full_name='Real Name', source_id='NCAA-77'), dict(full_name='Other Guy', source_id='NCAA-78'),
            dict(full_name='No Id', source_id='')]
    rows = []
    for pick, i in enumerate(order, start=1):
        r = dict(base[i], projected_pick=pick, position='QB', college='State', height="6'2\"", weight=220, age=21,
                 ovr=70)
        if rename and r['source_id'] == rename[0]:
            r['full_name'] = rename[1]
        rows.append(r)
    return rows


def class_entry(prospects):
    ids = iter(uuid.UUID(int=i) for i in range(100, 110))
    car = L.class_sidecar(2027, 'prospects.csv', [dict(source_id=p.source_id) for p in prospects],
                          allocate=lambda: next(ids))
    car.pop('unmapped')
    entry = dict(schema=load_capture.CLASS_ENTRY_SCHEMA, universe_id=universe()['universe_id'], cutoff=START,
                 sidecar=car, receipt=receipt('class:2027'))
    return entry, dict(reviewer='JR', reviewed_manifest_sha256=L.canonical_hash(entry), decision='approve',
                       reason='imported class', reviewed_at='2026-11-04')


def test_imported_class_initializes_mapped_rows_once_through_rename_and_reorder(cur, tmp_path):
    S.register_universe(cur, universe())
    path = tmp_path / 'prospects.csv'
    write_csv(path, csv_rows())
    prospects = load_prospects_from_csv(str(path), strict=False, warnings=[])
    assert [p.source_id for p in prospects] == ['NCAA-77', 'NCAA-78', None]
    entry, rev = class_entry(prospects)
    draft_board.write_board(cur, 2027, prospects, 'SCAN1')
    out = load_capture.class_personality(cur, 2027, prospects, 'SCAN1', dict(entry=entry, review=rev))
    assert out['initialized'] == 2 and out['pending'] == 1
    first = profiles(cur)
    write_csv(path, csv_rows(order=(2, 1, 0), rename=('NCAA-77', 'Real Renamed')))
    again = load_prospects_from_csv(str(path), strict=False, warnings=[])
    draft_board.write_board(cur, 2027, again, 'SCAN2')
    out = load_capture.class_personality(cur, 2027, again, 'SCAN2', dict(entry=entry, review=rev))
    assert out['existing'] == 2 and out['initialized'] == 0 and profiles(cur) == first
    spid = entry['sidecar']['entries'][0]['source_person_id']
    r = S.resolve_person(cur, universe()['universe_id'], 'player', 'draft:2027:SCAN2',
                         str(draft_board.board_ids(cur, 2027)[3]), cutoff=START, feature='x')
    assert r['identity']['source_person_id'] == spid


def test_imported_class_links_unique_prospect_uid_and_skips_duplicate_scout_key(cur, tmp_path):
    S.register_universe(cur, universe())
    path = tmp_path / 'prospects.csv'
    write_csv(path, csv_rows())
    prospects = load_prospects_from_csv(str(path), strict=False, warnings=[])
    prospect_ids.ensure_prospect_ids(cur, 2027, [dict(prospect_key='2027:REALNAME:QB', pgid=None),
                                                 dict(prospect_key='2027:OTHERGUY:QB', pgid=None),
                                                 dict(prospect_key='2027:OTHERGUY:QB', pgid=None)], 'SCAN1')
    entry, rev = class_entry(prospects)
    draft_board.write_board(cur, 2027, prospects, 'SCAN1')
    out = load_capture.class_personality(cur, 2027, prospects, 'SCAN1', dict(entry=entry, review=rev))
    assert out['scout_linked'] == 1                         # OTHERGUY collides: no unique prospect_uid, no link
    spid = entry['sidecar']['entries'][0]['source_person_id']
    uid = prospect_ids.uid_for(cur, 2027, '2027:REALNAME:QB')
    assert prospect_ids.person_ref(cur, uid) == spid


def test_imported_class_rejects_unreviewed_or_unknown_entries(cur, tmp_path):
    S.register_universe(cur, universe())
    prospects = [Prospect(1, 'A', 'QB', 'S', None, None, None, None, source_id='X')]
    entry, rev = class_entry(prospects)
    with pytest.raises(load_capture.LoadError):
        load_capture.class_personality(cur, 2027, prospects, 'S', dict(entry=entry, review=dict(
            rev, reviewed_manifest_sha256='0' * 64)))
    entry['receipt'] = receipt('class:2027', 'unknown')
    rev['reviewed_manifest_sha256'] = L.canonical_hash(entry)
    with pytest.raises(load_capture.LoadError, match='generated'):
        load_capture.class_personality(cur, 2027, prospects, 'S', dict(entry=entry, review=rev))
    assert count(cur, 'persona_core_profiles') == 0
    assert load_capture.class_personality(cur, 2027, prospects, 'S') == {'mode': 'observe', 'pending': 1}


def test_name_matched_rookie_links_are_candidates_only(cur):
    cur.execute("INSERT INTO draft_prospects (draft_season, projected_pick, full_name, player_id) "
                "VALUES (2027, 1, 'A', 55)")
    assert draft_board.rookie_handoff_candidates(cur, 2027) == [
        dict(draft_prospect_id=1, player_id=55, evidence='name_match')]
    assert count(cur, 'persona_lifecycle_bindings') == 0


# ---------- scouting observe ----------

def test_scouting_observe_reads_only_and_reports_pending(cur):
    assert scouting_engine.personality_observe(cur, 2027) is None          # no league stamp / universe
    snap = save_snapshot()
    m = manifest_for(snap, uni=universe(snap))
    prospect_ids.ensure_prospect_ids(cur, 2027, [dict(prospect_key='2027:A:QB', pgid=201),
                                                 dict(prospect_key='2027:B:QB', pgid=203),
                                                 dict(prospect_key='2027:B:QB', pgid=204)], 'CAP1')
    S.apply_lifecycle(cur, m, expected_hash=m['manifest_sha256'], review_receipt=review(m))
    cur.execute("UPDATE league_state SET madden_league_id = 'LEAGUE-1' WHERE id = 1")
    before = [count(cur, t) for t in ('persona_core_profiles', 'persona_lifecycle_bindings', 'scout_prospect_ids')]
    rep = scouting_engine.personality_observe(cur, 2027)
    assert rep == dict(universe_id=m['universe']['universe_id'], prospects=3, pending=2, key_collisions=2)
    assert [count(cur, t) for t in ('persona_core_profiles', 'persona_lifecycle_bindings',
                                    'scout_prospect_ids')] == before
    lines = []
    scouting_engine._observe(cur, 2027, lines.append)
    assert lines and 'observe' in lines[0]


def test_scouting_observe_is_silent_without_lifecycle(monkeypatch):
    monkeypatch.setattr(S, 'available', lambda c: False)
    lines = []
    assert scouting_engine._observe(None, 2027, lines.append) is None and lines == []


def test_preview_writes_no_profiles(cur):
    snap = save_snapshot()
    before = [count(cur, t) for t in ('persona_core_profiles', 'persona_lifecycle_people')]
    manifest_for(snap, uni=universe(snap), existing=S.export_state(cur, universe(snap)['universe_id']))
    assert [count(cur, t) for t in ('persona_core_profiles', 'persona_lifecycle_people')] == before
