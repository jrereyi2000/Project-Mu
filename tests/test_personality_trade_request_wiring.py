"""Trade-request scheduling, the GM handoff and switchboard-driven watcher flags
(docs/in_progress/PERSONALITY_ACTIVATION_PLAN.md §2.4, U3). Offline: fake connections, fake stores, synthetic books."""
import json
import os
from unittest import mock

import pytest

from engines import gm_trade_engine as ge
from engines import player_trade_request_store as trs
from engines import player_trade_requests as tr
from personality import features as pf
from pipeline import watch_save as ws
from pipeline import weekly_capture as wc
from tests import test_gm_trade_engine as tge

UNIVERSE = {'universe_id': 'mu:00000000-0000-4000-8000-000000000001'}


def modes(**m):
    feats = {f: {'feature': f, 'mode': 'off', 'version': None, 'config': {}} for f in pf.FEATURES}
    for f, v in m.items():
        feats[f] = {'feature': f, 'mode': v, 'version': f'{f}-v1', 'config': {}}
    return {'league_id': 7, 'source': 'switchboard', 'features': feats}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (ws.PRODUCERS_ENV, ws.EVOLUTION_ENV, ws.TRADE_REQUESTS_ENV):
        monkeypatch.delenv(name, raising=False)


# ------------------------------------------------------------------------------------------------- gates ---
def test_gate_reads_the_switchboard_and_the_kill_switch_wins(monkeypatch):
    assert ws.feature_gate('producers', ws.PRODUCERS_ENV, None)[0] == 'off'
    assert ws.feature_gate('producers', ws.PRODUCERS_ENV, modes(producers='shadow'))[0] == 'shadow'
    assert ws.relationship_producers_on(modes(producers='active'))
    assert ws.personality_evolution_on(modes(evolution='shadow'))
    monkeypatch.setenv(ws.PRODUCERS_ENV, 'off')
    monkeypatch.setenv(ws.EVOLUTION_ENV, 'off')
    monkeypatch.setenv(ws.TRADE_REQUESTS_ENV, 'off')
    assert not ws.relationship_producers_on(modes(producers='active'))
    assert not ws.personality_evolution_on(modes(evolution='active'))
    assert ws.feature_gate('trade_requests', ws.TRADE_REQUESTS_ENV, modes(trade_requests='active'))[0] == 'off'
    monkeypatch.setenv(ws.PRODUCERS_ENV, 'on')                  # the old flag still runs the step by hand
    assert ws.feature_gate('producers', ws.PRODUCERS_ENV, None) == ('active', 'MU_RELATIONSHIP_PRODUCERS=on '
                                                                              '(operator override)')


def test_steps_stay_off_without_the_switchboard_and_never_connect():
    connect = mock.Mock(side_effect=AssertionError('connected'))
    assert ws.step_relationship_producers('x.json', 2026, connect=connect, log_fn=lambda m: None) == {'status': 'off'}
    assert ws.step_personality_evolution('x.json', 2026, connect=connect, log_fn=lambda m: None) == {'status': 'off'}
    assert ws.step_trade_requests('x.json', 2026, connect=connect, log_fn=lambda m: None,
                                  modes=modes(evolution='active')) == {'status': 'off'}
    connect.assert_not_called()


def test_personality_modes_never_raises(monkeypatch):
    monkeypatch.delenv('DATABASE_URL', raising=False)
    assert ws.personality_modes(None, connect=None) is None              # no database: nothing to read
    assert ws.personality_modes('postgresql://u@h/madden_test', connect=mock.Mock(side_effect=RuntimeError('down')),
                                log_fn=lambda m: None) is None


def test_personality_modes_reads_one_snapshot(monkeypatch):
    cur = mock.MagicMock()
    cur.fetchone.return_value = (7,)
    conn = mock.MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    monkeypatch.setattr(pf, 'snapshot', lambda c, league: {'league_id': league, 'features': {}})
    assert ws.personality_modes('dsn', connect=lambda d: conn) == {'league_id': 7, 'features': {}}
    conn.rollback.assert_called_once()


# ---------------------------------------------------------------------------------------- watcher step ---
def snapshot_file(tmp_path):
    path = tmp_path / 'snap.json'
    path.write_text(json.dumps({'season': {'calendar_year': 2026, 'stage': 'NFLSeason', 'week_type': 'RegularSeason',
                                           'week_index': 4, 'offseason_stage': None}}))
    return str(path)


def fake_conn(ready=(True, True)):
    cur = mock.MagicMock()
    cur.fetchone.return_value = ready
    conn = mock.MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


def test_shadow_step_writes_a_report_and_persists_nothing(tmp_path, monkeypatch):
    conn, cur = fake_conn()
    monkeypatch.setattr(ws, 'TRADE_REQUEST_REPORTS', str(tmp_path / 'reports'))
    monkeypatch.setattr('personality.lifecycle_store.universe_for_league', lambda c, l: UNIVERSE)
    monkeypatch.setattr('engines.relationship_producers.sources.league_id', lambda c: 7)
    monkeypatch.setattr(ws, 'registry_user_team_ids', lambda c: set())
    seen = {}

    def review(c, universe, season, tick, mode, m, **kw):
        seen.update(season=season, tick=tick, mode=mode)
        return {'status': mode, 'counts': {'NO_REQUEST': 1}, 'decisions': [{'outcome': 'NO_REQUEST'}]}
    monkeypatch.setattr(ws, 'review_trade_requests', review)
    res = ws.step_trade_requests(snapshot_file(tmp_path), 2026, connect=lambda d: conn, log_fn=lambda m: None,
                                 modes=modes(trade_requests='shadow'))
    assert res['status'] == 'shadow' and seen['mode'] == 'shadow' and seen['season'] == 2026
    assert os.path.exists(res['report'])
    conn.rollback.assert_called()
    conn.commit.assert_not_called()


def test_active_step_commits(tmp_path, monkeypatch):
    conn, cur = fake_conn()
    monkeypatch.setattr('personality.lifecycle_store.universe_for_league', lambda c, l: UNIVERSE)
    monkeypatch.setattr('engines.relationship_producers.sources.league_id', lambda c: 7)
    monkeypatch.setattr(ws, 'registry_user_team_ids', lambda c: set())
    monkeypatch.setattr(ws, 'review_trade_requests', lambda *a, **k: {'status': 'active', 'counts': {}, 'decisions': []})
    res = ws.step_trade_requests(snapshot_file(tmp_path), 2026, connect=lambda d: conn, log_fn=lambda m: None,
                                 modes=modes(trade_requests='active'))
    assert res['status'] == 'active' and 'report' not in res
    conn.commit.assert_called_once()


def test_step_skips_unmigrated_and_never_fails_the_ingest(tmp_path, monkeypatch):
    conn, _ = fake_conn(ready=(False, True))
    res = ws.step_trade_requests(snapshot_file(tmp_path), 2026, connect=lambda d: conn, log_fn=lambda m: None,
                                 modes=modes(trade_requests='active'))
    assert res == {'status': 'skipped', 'reason': 'not_migrated'}
    monkeypatch.setattr(ws, 'send_notify', lambda *a, **k: None)
    res = ws.step_trade_requests(snapshot_file(tmp_path), 2026, connect=mock.Mock(side_effect=RuntimeError('boom')),
                                 log_fn=lambda m: None, modes=modes(trade_requests='active'))
    assert res == {'status': 'failed', 'error': 'boom'}


def context(persona='p-1', team=3):
    clock = ws.trade_request_clock(UNIVERSE['universe_id'], 2026, 4)
    return tr.RequestContext(schema_version=tr.SCHEMA_VERSION, persona_id=persona, team_id=team,
                             profile_view={'status': 'missing', 'traits': []}, relationship_revision='none',
                             reaction_revision='none', assessment_id='2026:4', as_of_ordinal=clock.ordinal,
                             complete_through_ordinal=clock.ordinal, incidents=(),
                             control=tr.control_context(team, user_team_ids=set(), suggestions_on=True)), team


def test_review_in_shadow_never_touches_the_store(monkeypatch):
    monkeypatch.setattr(trs, 'record_decision', mock.Mock(side_effect=AssertionError('persisted in shadow')))
    monkeypatch.setattr(trs, 'read_active', mock.Mock(side_effect=AssertionError('read in shadow')))
    out = ws.review_trade_requests(None, UNIVERSE, 2026, 4, 'shadow', modes(trade_requests='shadow'),
                                   contexts=[context()])
    assert out['status'] == 'shadow' and out['decisions'][0]['outcome'] in tr.OUTCOMES


def test_review_active_records_through_the_store(monkeypatch):
    calls = []
    monkeypatch.setattr(trs, 'read_active', lambda cur, u, p: None)
    monkeypatch.setattr(trs, 'record_decision', lambda cur, ctx, clock, policy, *, mode: calls.append(mode) or
                        {'decision': {'outcome': 'NO_REQUEST'}})
    monkeypatch.setattr(tr, 'policy_from_dict', lambda d: tr.offline_illustration_policy())
    m = modes(trade_requests='active')
    m['features']['trade_requests']['config'] = {'policy': {'version': 'reviewed'}}
    out = ws.review_trade_requests(None, UNIVERSE, 2026, 4, 'active', m, contexts=[context()])
    assert calls == ['ENABLED'] and out['counts'] == {'NO_REQUEST': 1}


def test_review_active_without_a_reviewed_policy_is_skipped():
    out = ws.review_trade_requests(None, UNIVERSE, 2026, 4, 'active', modes(trade_requests='active'), contexts=[])
    assert out['status'] == 'skipped' and out['reason'] == 'no_policy'


def test_review_active_episode_gets_a_repair_review_and_errors_stay_per_player(monkeypatch):
    episode = mock.Mock(team_id=3, revision=2, request_id='r-1')
    actions = []
    monkeypatch.setattr(trs, 'read_active', lambda cur, u, p: episode if p == 'p-1' else None)
    monkeypatch.setattr(trs, 'apply_action', lambda cur, rid, action, ctx, clock, policy: actions.append(action.kind)
                        or {'transition': {}})
    monkeypatch.setattr(trs, 'record_decision', mock.Mock(side_effect=RuntimeError('bad binding')))
    m = modes(trade_requests='active')
    m['features']['trade_requests']['config'] = {'policy': {}}
    monkeypatch.setattr(tr, 'policy_from_dict', lambda d: tr.offline_illustration_policy())
    out = ws.review_trade_requests(None, UNIVERSE, 2026, 4, 'active', m, contexts=[context(), context('p-2')])
    assert actions == ['REVIEW_REPAIR'] and out['counts'] == {'REVIEW_REPAIR': 1, 'ERROR': 1}


def test_clock_uses_the_gm_window():
    assert ws.trade_request_clock('u', 2026, 4).trades_open
    late = ws.trade_request_clock('u', 2026, ge.TRADE_DEADLINE_WEEK + 1)
    assert late.window_kind == 'CLOSED' and not late.trades_open
    assert ws.trade_request_clock('u', 2026, 26).window_kind == 'OFFSEASON'


# ------------------------------------------------------------------------------------------- GM handoff ---
def test_request_shopping_only_for_actionable_cpu_requests():
    b = tge.books()
    pid = next(iter(b['roster'][2]))
    sig = {'request_id': 'r', 'persona_id': 'p', 'team_id': 2, 'status': 'ACTIVE', 'cause_codes': [],
           'actionable': True, 'player_id': pid}
    assert ge.request_shopping(b, [sig]) == {2: {pid: 'r'}}
    assert ge.request_shopping(b, [dict(sig, actionable=False)]) == {}
    assert ge.request_shopping(b, None) == {}
    b['gm'][2]['user_controlled'] = True                           # JR's team: no automatic shopping
    assert ge.request_shopping(b, [sig]) == {}


def test_request_deals_shop_at_the_normal_valuation():
    b = tge.star_books()
    v = ge.Valuer(b, tge.FITS)
    deals = ge.request_deals(b, v, 2, {tge.STAR: 'req-1'})
    assert deals and all(d['a'] == 2 and d['b'] == 1 and d['request_id'] == 'req-1' for d in deals)
    assert all(any(x['type'] == 'PLAYER' and x['player_id'] == tge.STAR for x in d['assets']) for d in deals)
    x = {'type': 'PLAYER', 'player_id': tge.STAR, 'from': 2, 'to': 1}
    before = v.value(x, 1)
    ge.request_deals(b, v, 2, {tge.STAR: 'req-1'})
    assert v.value(x, 1) == before                                  # no price change
    cands = ge.evaluate(deals, b, v)                                # the usual legality and acceptance
    assert all(c['legality'] for c in cands)


def test_step_gm_passes_no_signals_in_shadow(monkeypatch):
    cur = mock.MagicMock()
    cur.fetchone.return_value = (7,)
    monkeypatch.setattr(pf, 'snapshot', lambda c, league: modes(trade_requests='shadow'))
    assert wc.trade_request_signals(cur, 2026, 4) is None
    monkeypatch.setattr(pf, 'snapshot', lambda c, league: modes(trade_requests='active'))
    monkeypatch.setenv(ws.TRADE_REQUESTS_ENV, 'off')
    assert wc.trade_request_signals(cur, 2026, 4) is None


def test_step_gm_signals_skip_user_teams(monkeypatch):
    state = {'request_id': 'r', 'persona_id': 'p-1', 'team_id': 3}
    cur = mock.MagicMock()
    answers = iter([(7,), (True, True), (11,)])
    cur.fetchone.side_effect = lambda: next(answers)
    cur.fetchall.side_effect = [[(5,)], [(json.dumps(state),)]]
    monkeypatch.setattr(pf, 'snapshot', lambda c, league: modes(trade_requests='active'))
    monkeypatch.setattr('personality.lifecycle_store.universe_for_league', lambda c, l: UNIVERSE)
    monkeypatch.setattr('db.franchises.active_entry', lambda: ('x', {'user_teams': ['CAR']}))
    monkeypatch.setattr(tr, 'episode_from_dict', lambda d: mock.Mock(**d))
    monkeypatch.setattr(tr, 'gm_request_signal', lambda e, clock=None: {
        'request_id': e.request_id, 'persona_id': e.persona_id, 'team_id': e.team_id, 'status': 'ACTIVE',
        'cause_codes': [], 'actionable': True})
    sigs = wc.trade_request_signals(cur, 2026, 4)
    assert sigs == [{'request_id': 'r', 'persona_id': 'p-1', 'team_id': 3, 'status': 'ACTIVE', 'cause_codes': [],
                     'actionable': True, 'player_id': 11}]
    cur.fetchall.side_effect = [[(3,)], [(json.dumps(state),)]]      # team 3 is JR's: nothing shopped
    answers = iter([(7,), (True, True)])
    cur.fetchone.side_effect = lambda: next(answers)
    assert wc.trade_request_signals(cur, 2026, 4) == []
