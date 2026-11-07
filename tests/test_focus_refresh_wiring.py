"""The OFFSEASON_START focus-refresh watcher step (INFERRED_SLIDERS_AND_FOCUS_PLAN.md §2.7, §4). Offline."""
import json
import os
import shutil
import tempfile
from unittest import mock

import pytest

from pipeline import watch_save as ws


@pytest.fixture(autouse=True)
def no_switchboard(monkeypatch):
    monkeypatch.setattr("pipeline.watch_save.personality_modes", lambda *a, **k: None)


def snapshot_file(tmp):
    path = os.path.join(tmp, 'save.json')
    with open(path, 'w') as f:
        json.dump({'season': {'calendar_year': 2030}}, f)
    return path


@pytest.fixture
def tmp():
    d = tempfile.mkdtemp()
    with mock.patch.dict(os.environ, {'DATABASE_URL': 'postgresql://u@h/madden_test'}):
        os.environ.pop(ws.FOCUS_ENV, None)
        yield d
    shutil.rmtree(d, ignore_errors=True)


def conn():
    c = mock.MagicMock()
    c.cursor.return_value.__enter__.return_value = mock.MagicMock()
    return c


def report(status='applied', replaced=2):
    key = 'replaced' if status == 'applied' else 'would_replace'
    return {'status': status, 'counts': {key: replaced, 'kept': 1, 'jr_stale': 0, 'rows': 3, 'people': 2},
            'changes': [], 'jr_stale': [], 'unresolved': [], 'universe_id': 'mu:u', 'season': 2030,
            'rules_version': 'focus-v1', 'input_hash': 'h'}


def test_off_by_default_and_with_the_kill_switch(tmp):
    connect = mock.Mock()
    assert ws.step_focus_refresh(snapshot_file(tmp), 2030, connect=connect, log_fn=lambda m: None) == {'status': 'off'}
    with mock.patch.dict(os.environ, {ws.FOCUS_ENV: 'off'}):
        modes = {'features': {'focus_refresh': {'mode': 'active'}}}
        with mock.patch('personality.features.snapshot_mode', return_value='active'):
            assert ws.step_focus_refresh(snapshot_file(tmp), 2030, connect=connect, modes=modes)['status'] == 'off'
    connect.assert_not_called()
    assert ws.focus_refresh_on() is False


@pytest.mark.parametrize('stage', ['REGULAR_WEEK', 'RESIGN', 'FREE_AGENCY', 'DRAFT'])
def test_runs_only_at_offseason_start(tmp, stage):
    connect = mock.Mock()
    with mock.patch.dict(os.environ, {ws.FOCUS_ENV: 'on'}), \
            mock.patch('pipeline.league_office.detect_stage', return_value=stage), \
            mock.patch('personality.focus_run.run_season') as run:
        res = ws.step_focus_refresh(snapshot_file(tmp), 2030, connect=connect, log_fn=lambda m: None)
    assert res['status'] == 'skipped' and res['reason'] == 'not_offseason_start'
    connect.assert_not_called()
    run.assert_not_called()


def test_active_applies_writes_the_report_and_notifies(tmp):
    c = conn()
    path = os.path.join(tmp, 'r.md')
    with mock.patch.dict(os.environ, {ws.FOCUS_ENV: 'on'}), \
            mock.patch('pipeline.league_office.detect_stage', return_value='OFFSEASON_START'), \
            mock.patch('personality.focus_run.tables_ready', return_value=True), \
            mock.patch('personality.focus_run.resolve_universe', return_value='mu:u'), \
            mock.patch('personality.focus_run.report_path', return_value=path), \
            mock.patch('personality.focus_run.run_season', return_value=report()) as run, \
            mock.patch('pipeline.watch_save.send_notify') as notify:
        res = ws.step_focus_refresh(snapshot_file(tmp), 2030, connect=lambda dsn: c, log_fn=lambda m: None)
    assert res == {'status': 'applied', 'season': 2030, 'universe_id': 'mu:u', 'changes': 2, 'report': path}
    assert run.call_args[0][1:] == ('mu:u', 2030) and run.call_args[1] == {'dry_run': False}
    c.commit.assert_called_once()
    assert os.path.exists(path)
    assert 'Mu: 2 focus refreshes in 2030' in notify.call_args[0][0]


def test_shadow_is_a_dry_run_and_writes_nothing(tmp):
    c = conn()
    path = os.path.join(tmp, 'r.md')
    with mock.patch('pipeline.league_office.detect_stage', return_value='OFFSEASON_START'), \
            mock.patch('personality.features.snapshot_mode', return_value='shadow'), \
            mock.patch('personality.focus_run.tables_ready', return_value=True), \
            mock.patch('personality.focus_run.resolve_universe', return_value='mu:u'), \
            mock.patch('personality.focus_run.report_path', return_value=path), \
            mock.patch('personality.focus_run.run_season', return_value=report('dry_run')) as run, \
            mock.patch('pipeline.watch_save.send_notify') as notify:
        res = ws.step_focus_refresh(snapshot_file(tmp), 2030, connect=lambda dsn: c, log_fn=lambda m: None,
                                    modes={'features': {}})
    assert res['status'] == 'dry_run' and res['changes'] == 0
    assert run.call_args[1] == {'dry_run': True}
    c.commit.assert_not_called()
    c.rollback.assert_called()
    notify.assert_not_called()


def test_skipped_when_not_migrated_or_no_universe(tmp):
    c = conn()
    with mock.patch.dict(os.environ, {ws.FOCUS_ENV: 'on'}), \
            mock.patch('pipeline.league_office.detect_stage', return_value='OFFSEASON_START'), \
            mock.patch('personality.focus_run.tables_ready', return_value=False), \
            mock.patch('personality.focus_run.run_season') as run:
        res = ws.step_focus_refresh(snapshot_file(tmp), 2030, connect=lambda dsn: c, log_fn=lambda m: None)
    assert res == {'status': 'skipped', 'reason': 'not_migrated'}
    run.assert_not_called()
    from personality import focus_run as fr
    with mock.patch.dict(os.environ, {ws.FOCUS_ENV: 'on'}), \
            mock.patch('pipeline.league_office.detect_stage', return_value='OFFSEASON_START'), \
            mock.patch('personality.focus_run.tables_ready', return_value=True), \
            mock.patch('personality.focus_run.resolve_universe', side_effect=fr.FocusRunError(3, 'none')):
        res = ws.step_focus_refresh(snapshot_file(tmp), 2030, connect=lambda dsn: c, log_fn=lambda m: None)
    assert res == {'status': 'skipped', 'reason': 'no_universe'}


def test_a_failure_is_recorded_and_never_raised(tmp):
    c = conn()
    with mock.patch.dict(os.environ, {ws.FOCUS_ENV: 'on'}), \
            mock.patch('pipeline.league_office.detect_stage', return_value='OFFSEASON_START'), \
            mock.patch('personality.focus_run.tables_ready', return_value=True), \
            mock.patch('personality.focus_run.resolve_universe', return_value='mu:u'), \
            mock.patch('personality.focus_run.run_season', side_effect=RuntimeError('boom')), \
            mock.patch('personality.focus_run.record_failure') as failure, \
            mock.patch('pipeline.watch_save.send_notify') as notify:
        res = ws.step_focus_refresh(snapshot_file(tmp), 2030, connect=lambda dsn: c, log_fn=lambda m: None)
    assert res == {'status': 'failed', 'error': 'boom'}
    assert failure.call_args[0][1:3] == ('mu:u', 2030)
    notify.assert_called_once()


def test_ingest_runs_it_after_evolution_and_before_the_league_office(tmp):
    order = []
    path = snapshot_file(tmp)
    patches = [
        mock.patch('pipeline.load_save.load', side_effect=lambda *a, **k: order.append('load') or {}),
        mock.patch('pipeline.watch_save.step_relationship_producers',
                   side_effect=lambda *a, **k: order.append('producers') or {'status': 'off'}),
        mock.patch('pipeline.watch_save.step_personality_evolution',
                   side_effect=lambda *a, **k: order.append('evolution') or {'status': 'off'}),
        mock.patch('pipeline.watch_save.step_focus_refresh',
                   side_effect=lambda *a, **k: order.append('focus') or {'status': 'off'}),
        mock.patch('pipeline.watch_save.step_trade_requests',
                   side_effect=lambda *a, **k: order.append('trade_requests') or {'status': 'off'}),
        mock.patch('pipeline.watch_save.send_notify'),
        mock.patch('pipeline.watch_save.run_league_office',
                   side_effect=lambda *a, **k: order.append('league_office') or {'status': 'written'}),
        mock.patch('pipeline.watch_save.report_league_office', return_value=''),
    ]
    for p in patches:
        p.start()
    try:
        res = ws.ingest_snapshot(path, 2030, None, save_name='CAREER-A', league_office=True, stage_only=True)
    finally:
        for p in patches:
            p.stop()
    assert order == ['load', 'producers', 'evolution', 'focus', 'trade_requests', 'league_office']
    assert res['focus_refresh'] == {'status': 'off'}
