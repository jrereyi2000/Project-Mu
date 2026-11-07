"""Evolution change notes in prompts and the OFFSEASON_START watcher step (PERSONALITY_EVOLUTION_PLAN.md §2.3,
§2.5, §4). Offline: no database, no notifications."""
import json
import os
import shutil
import tempfile
from unittest import mock

import pytest

from newsroom import personas
from personality import coach_profiles as cp, evolution as evo
from pipeline import watch_save as ws
from tests.test_coach_profiles import profile as coach_doc


@pytest.fixture(autouse=True)
def no_switchboard(monkeypatch):
    """The ingest reads the personality switchboard once (personality_modes), which opens a database connection;
    tests get None (no switchboard: every step falls back to its env flag) unless they patch it themselves."""
    monkeypatch.setattr("pipeline.watch_save.personality_modes", lambda *a, **k: None)

VOICE = 'You are a plain-spoken veteran.'


def persona(**kw):
    p = {'voice': VOICE, 'sliders': {}, 'traits': []}
    p.update(kw)
    return p


# --------------------------------------------------------------------------------------------- prompts ---

def test_render_persona_shows_up_to_two_notes_under_the_voice():
    notes = ['Since the 2031 title he talks more about legacy than rings.',
             'He has not forgotten the team that cut him in 2030.', '2029: an older note']
    text = personas.render_persona(persona(change_notes=notes), traits={})
    blocks = text.split('\n\n')
    assert blocks[0] == VOICE
    assert blocks[1].startswith('HOW YOU HAVE CHANGED LATELY')
    assert notes[0] in blocks[1] and notes[1] in blocks[1] and notes[2] not in text
    assert personas.MAX_CHANGE_NOTES == evo.MAX_CHANGE_NOTES == 2


def test_render_persona_shows_nothing_without_notes():
    for p in (persona(), persona(change_notes=[]), persona(change_notes=['  ', None])):
        assert 'HOW YOU HAVE CHANGED' not in personas.render_persona(p, traits={})
    assert personas.render_persona(persona(), traits={}) == VOICE


def test_persona_for_passes_notes_through():
    doc = coach_doc()
    plain = cp.persona_for(doc)
    assert 'change_notes' not in plain
    noted = cp.persona_for(doc, change_notes=['Since his 2030 firing he has a shorter fuse.'])
    assert noted['change_notes'] == ['Since his 2030 firing he has a shorter fuse.']
    assert {k: v for k, v in noted.items() if k != 'change_notes'} == plain
    assert 'shorter fuse' in personas.render_persona(noted, traits={})


# --------------------------------------------------------------------------------------------- watcher ---

def snapshot_file(tmp, stage='OFFSEASON_START'):
    path = os.path.join(tmp, 'save.json')
    with open(path, 'w') as f:
        json.dump({'season': {'calendar_year': 2030}}, f)
    return path


@pytest.fixture
def tmp():
    d = tempfile.mkdtemp()
    with mock.patch.dict(os.environ, {'DATABASE_URL': 'postgresql://u@h/madden_test'}):
        os.environ.pop(ws.EVOLUTION_ENV, None)
        yield d
    shutil.rmtree(d, ignore_errors=True)


def conn():
    c = mock.MagicMock()
    c.cursor.return_value.__enter__.return_value = mock.MagicMock()
    return c


def test_off_by_default_and_with_the_kill_switch(tmp):
    connect = mock.Mock()
    assert ws.step_personality_evolution(snapshot_file(tmp), 2030, connect=connect, log_fn=lambda m: None) == \
        {'status': 'off'}
    with mock.patch.dict(os.environ, {ws.EVOLUTION_ENV: 'off'}):
        assert ws.step_personality_evolution(snapshot_file(tmp), 2030, connect=connect)['status'] == 'off'
    connect.assert_not_called()


@pytest.mark.parametrize('stage', ['REGULAR_WEEK', 'RESIGN', 'FREE_AGENCY', 'DRAFT'])
def test_runs_only_at_offseason_start(tmp, stage):
    connect = mock.Mock()
    with mock.patch.dict(os.environ, {ws.EVOLUTION_ENV: 'on'}), \
            mock.patch('pipeline.league_office.detect_stage', return_value=stage), \
            mock.patch('personality.evolution_run.run_season') as run:
        res = ws.step_personality_evolution(snapshot_file(tmp), 2030, connect=connect, log_fn=lambda m: None)
    assert res['status'] == 'skipped' and res['reason'] == 'not_offseason_start'
    connect.assert_not_called()
    run.assert_not_called()


def test_on_at_tick_24_runs_writes_the_report_and_notifies(tmp):
    c = conn()
    report = {'status': 'applied', 'counts': {'applied': 3}, 'candidates': [], 'universe_id': 'mu:u', 'season': 2030,
              'rules_version': 'evolution-v1', 'input_hash': 'h'}
    path = os.path.join(tmp, 'r.md')
    with mock.patch.dict(os.environ, {ws.EVOLUTION_ENV: 'on'}), \
            mock.patch('pipeline.league_office.detect_stage', return_value='OFFSEASON_START'), \
            mock.patch('personality.evolution_run.tables_ready', return_value=True), \
            mock.patch('personality.evolution_run.resolve_universe', return_value='mu:u'), \
            mock.patch('personality.evolution_run.report_path', return_value=path), \
            mock.patch('personality.evolution_run.run_season', return_value=report) as run, \
            mock.patch('pipeline.watch_save.send_notify') as notify:
        res = ws.step_personality_evolution(snapshot_file(tmp), 2030, connect=lambda dsn: c, log_fn=lambda m: None)
    assert res == {'status': 'applied', 'season': 2030, 'universe_id': 'mu:u', 'changes': 3, 'report': path,
                   'development_candidates': 0}
    assert run.call_args[0][1:] == ('mu:u', 2030)
    assert run.call_args[1]['external'] == []
    c.commit.assert_called_once()
    assert os.path.exists(path)
    assert 'Mu: 3 personality changes in 2030' in notify.call_args[0][0]


def test_skipped_when_not_migrated(tmp):
    c = conn()
    with mock.patch.dict(os.environ, {ws.EVOLUTION_ENV: 'on'}), \
            mock.patch('pipeline.league_office.detect_stage', return_value='OFFSEASON_START'), \
            mock.patch('personality.evolution_run.tables_ready', return_value=False), \
            mock.patch('personality.evolution_run.run_season') as run:
        res = ws.step_personality_evolution(snapshot_file(tmp), 2030, connect=lambda dsn: c, log_fn=lambda m: None)
    assert res == {'status': 'skipped', 'reason': 'not_migrated'}
    run.assert_not_called()


def test_a_failure_is_recorded_and_never_raised(tmp):
    c = conn()
    with mock.patch.dict(os.environ, {ws.EVOLUTION_ENV: 'on'}), \
            mock.patch('pipeline.league_office.detect_stage', return_value='OFFSEASON_START'), \
            mock.patch('personality.evolution_run.tables_ready', return_value=True), \
            mock.patch('personality.evolution_run.resolve_universe', return_value='mu:u'), \
            mock.patch('personality.evolution_run.run_season', side_effect=RuntimeError('boom')), \
            mock.patch('personality.evolution_run.record_failure') as failure, \
            mock.patch('pipeline.watch_save.send_notify') as notify:
        res = ws.step_personality_evolution(snapshot_file(tmp), 2030, connect=lambda dsn: c, log_fn=lambda m: None)
    assert res == {'status': 'failed', 'error': 'boom'}
    c.rollback.assert_called()
    assert failure.call_args[0][1:3] == ('mu:u', 2030)
    notify.assert_called_once()


def test_ingest_runs_it_after_the_producers_and_before_the_league_office(tmp):
    order = []
    path = snapshot_file(tmp)
    patches = [
        mock.patch('pipeline.load_save.load', side_effect=lambda *a, **k: order.append('load') or {}),
        mock.patch('pipeline.watch_save.step_relationship_producers',
                   side_effect=lambda *a, **k: order.append('producers') or {'status': 'off'}),
        mock.patch('pipeline.watch_save.step_personality_evolution',
                   side_effect=lambda *a, **k: order.append('evolution') or {'status': 'off'}),
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
    assert order == ['load', 'producers', 'evolution', 'league_office']
    assert res['personality_evolution'] == {'status': 'off'}
