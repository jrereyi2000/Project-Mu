"""Development evolution candidates (personality/evolution_development.py) and their wiring into the watcher's
evolution step: source DEVELOPMENT, gated by the switchboard's development_learning and evolution modes. Offline."""
import json
import os
import shutil
import tempfile
from unittest import mock

import pytest

from personality import evolution as evo, evolution_development as ed
from pipeline import watch_save as ws
from tests.test_personality_evolution import SEASON, UNIVERSE, profile


def block(mode='enabled', prep=0.2, effect=0.1):
    key = 'attributed_effect' if mode == 'enabled' else 'shadow_effect'
    return {'mode': mode, 'raw_channels': {'MENTAL': {'awareness': {'drive': prep, 'coasting': -0.05}}},
            key: {'awareness': effect}}


def receipts(player_id=11, n=3, **kw):
    return [{'event_id': f'pe-{player_id}-{i}', 'player_id': player_id, 'kind': 'MENTAL',
             'metadata': {ed.POLICY_SCHEMA: block(**kw)}} for i in range(n)]


def people(p, player_id=11):
    return {p['persona_id']: {'role': p['role'], 'profile_view': {'traits': list(p['traits'])},
                              'ids': {'db-player': [player_id]}}}


def test_development_mode_gate():
    assert ed.development_mode('off', 'active') is None
    assert ed.development_mode('active', 'off') is None
    assert ed.development_mode('active', 'active') == 'enabled'
    assert ed.development_mode('shadow', 'active') == 'enabled'
    assert ed.development_mode('shadow', 'shadow') == 'shadow'
    assert ed.development_mode('active', 'shadow') is None      # a shadow effect never changes a profile


def test_sustained_preparation_removes_coasting_and_the_arbiter_accepts_it():
    p = profile(traits=('COASTING',))
    out = ed.candidates(receipts(), people(p), universe_id=UNIVERSE, season=SEASON, receipt_mode='enabled')
    assert len(out) == 1
    item = out[0]
    assert item['source'] == 'DEVELOPMENT' and item['status'] == 'ready' and item['priority'] == ed.PRIORITY
    assert item['candidate']['remove'] == 'COASTING' and item['candidate']['kind'] == 'trait'
    assert item['candidate']['supporting_source_ids'] == ['pe-11-0', 'pe-11-1', 'pe-11-2']
    decided = evo.arbitrate([], out, season=SEASON, profiles={p['persona_id']: p})[p['persona_id']]
    assert decided['chosen']['source'] == 'DEVELOPMENT'
    assert decided['chosen']['rule_id'] == f"external:{item['candidate']['event_id']}"
    assert decided['chosen']['source_event_ids'][0] == 'pe-11-0'


@pytest.mark.parametrize('kw, traits, mode', [
    ({'n': 2}, ('COASTING',), 'enabled'),                    # one good stretch is not sustained
    ({'prep': -0.2}, ('COASTING',), 'enabled'),              # preparation not net positive
    ({'effect': 0.0}, ('COASTING',), 'enabled'),             # no effect resulted
    ({}, ('MENTOR',), 'enabled'),                            # nothing to remove
    ({'mode': 'shadow'}, ('COASTING',), 'enabled'),          # receipts of the other mode are ignored
    ({}, ('COASTING',), None),
])
def test_no_candidate_without_sustained_evidence(kw, traits, mode):
    p = profile(traits=traits)
    assert ed.candidates(receipts(**kw), people(p), universe_id=UNIVERSE, season=SEASON, receipt_mode=mode) == []


def test_shadow_receipts_feed_shadow_mode():
    p = profile(traits=('COASTING',))
    out = ed.candidates(receipts(mode='shadow'), people(p), universe_id=UNIVERSE, season=SEASON,
                        receipt_mode='shadow')
    assert [i['candidate']['remove'] for i in out] == ['COASTING']


def test_external_candidates_reads_nothing_when_gated_off():
    cur = mock.Mock()
    assert ed.external_candidates(cur, UNIVERSE, SEASON, receipt_mode=None) == []
    cur.execute.assert_not_called()


def test_season_receipts_without_the_table():
    cur = mock.Mock()
    cur.fetchone.return_value = (False,)
    assert ed.season_receipts(cur, SEASON) == []
    assert cur.execute.call_count == 1


# ------------------------------------------------------------------------------------------ watcher wiring ---

def snap(**modes):
    return {'league_id': 1, 'source': 'switchboard',
            'features': {f: {'mode': m} for f, m in modes.items()}}


@pytest.mark.parametrize('evo_mode, dev_mode, expected', [
    ('active', 'off', None), ('active', 'shadow', None), ('active', 'active', 'enabled'),
    ('shadow', 'shadow', 'shadow'), ('shadow', 'active', 'enabled')])
def test_watcher_passes_the_gated_receipt_mode(evo_mode, dev_mode, expected):
    with mock.patch('personality.evolution_development.external_candidates', return_value=[]) as ext:
        ws.development_evolution_candidates('cur', 'mu:u', 2030, evo_mode,
                                            snap(evolution=evo_mode, development_learning=dev_mode))
    assert ext.call_args[1]['receipt_mode'] == expected


def test_watcher_without_a_switchboard_reads_development_off():
    with mock.patch('personality.evolution_development.external_candidates', return_value=[]) as ext:
        ws.development_evolution_candidates('cur', 'mu:u', 2030, 'active', None)
    assert ext.call_args[1]['receipt_mode'] is None


def test_step_hands_development_candidates_to_run_season(monkeypatch):
    monkeypatch.setattr('pipeline.watch_save.personality_modes', lambda *a, **k: None)
    tmp = tempfile.mkdtemp()
    try:
        path = os.path.join(tmp, 's.json')
        with open(path, 'w') as fh:
            json.dump({'season': {'calendar_year': 2030}}, fh)
        c = mock.MagicMock()
        item = {'source': 'DEVELOPMENT'}
        report = {'status': 'dry_run', 'counts': {}, 'candidates': [], 'universe_id': 'mu:u', 'season': 2030,
                  'rules_version': 'evolution-v1', 'input_hash': 'h'}
        modes = snap(evolution='shadow', development_learning='active')
        with mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch('pipeline.league_office.detect_stage', return_value='OFFSEASON_START'), \
                mock.patch('personality.evolution_run.tables_ready', return_value=True), \
                mock.patch('personality.evolution_run.resolve_universe', return_value='mu:u'), \
                mock.patch('personality.evolution_run.report_path', return_value=os.path.join(tmp, 'r.md')), \
                mock.patch('personality.evolution_development.external_candidates', return_value=[item]), \
                mock.patch('personality.evolution_run.run_season', return_value=report) as run:
            os.environ.pop(ws.EVOLUTION_ENV, None)
            res = ws.step_personality_evolution(path, 2030, connect=lambda dsn: c, log_fn=lambda m: None,
                                                modes=modes)
        assert run.call_args[1] == {'dry_run': True, 'external': [item]}
        assert res['development_candidates'] == 1
        c.rollback.assert_called()
    finally:
        shutil.rmtree(tmp)
