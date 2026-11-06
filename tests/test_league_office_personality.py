"""PersonalityDeps and the league office seams (docs/done/PERSONALITY_ACTIVATION_PLAN.md §2.3, U2).

Offline: fake feature adapters, faked engines and tests/test_league_office.py's FakeDeps / FakeCursor / snapshot
helpers. Shadow must return exactly the baseline and record the adjusted result; active returns the adjusted one.
"""

import pytest

from engines import free_agency_engine as fae
from engines import transactions_engine as te
from personality import features as pf
from pipeline import league_office as lo
from pipeline import league_office_personality as lop
from tests import test_league_office as tlo

LEAGUE = 7


def snap_modes(**modes):
    feats = {f: {'feature': f, 'mode': 'off', 'version': None, 'config': {}} for f in pf.FEATURES}
    for f, m in modes.items():
        feats[f] = {'feature': f, 'mode': m, 'version': f'{f}-v1', 'config': {}}
    return {'league_id': LEAGUE, 'source': 'switchboard', 'features': feats}


class FakeFeatures:
    def __init__(self, fail=False):
        self.fail, self.calls = fail, []

    def contract_inputs(self, cur, league_id, policy, *, stage, season, week, players, batch_id):
        self.calls.append(('contracts', stage, policy['mode'] if policy else None, batch_id))
        if self.fail:
            raise RuntimeError('no universe')
        return {1: {'view': {}, 'context': {}, 'policy': policy}}

    def chart_adjustments(self, cur, league_id, row, *, team_id, season, week, roster, scheme, injuries):
        self.calls.append(('chart', team_id))
        return {'adjustments': {'WR': {roster[-1]['player_id']: 5.0}}, 'reasons': []}

    def retirement(self, cur, league_id, row, mode, *, player, season, seed):
        self.calls.append(('retirement', player['pgid'], mode))
        if player['pgid'] == 9:
            return None                                   # blocked: today's rule decides
        return {'keep': False, 'reason': 'retired by the retirement policy', 'decision': {}}

    def development_config(self, row, mode):
        from engines.personality_development import DevelopmentConfig
        return DevelopmentConfig(version='dev-v1', mode='enabled' if mode == 'active' else 'shadow')

    def development_inputs(self, cur, league_id, inputs, *, season, week):
        return {pid: dict(d, development_context={'reasons': []}) for pid, d in inputs.items()}


class PDeps(lop.PersonalityDeps, tlo.FakeDeps):
    """PersonalityDeps over the league office test fakes (FakeDeps supplies the baseline engines)."""

    def __init__(self, modes, snap=None, features=None, **kw):
        tlo.FakeDeps.__init__(self, snap, **kw)
        lop.PersonalityDeps.__init__(self, None, LEAGUE, modes, features or FakeFeatures())
        self.chart_kw = []

    def staff_depth_chart(self, roster, scheme, injuries, score_adjustments=None):
        self.chart_kw.append(score_adjustments)
        return tlo.FakeDeps.staff_depth_chart(self, roster, scheme, injuries)


@pytest.fixture
def engines(monkeypatch):
    calls = []

    def resign(cur, season, seed, *, contract_inputs=None, decisions=None):
        calls.append(('resign', contract_inputs))
        return [{'kind': 'adjusted-resign'}]

    def wave(players, offers, wave, seed, *, contract_inputs=None):
        calls.append(('wave', contract_inputs))
        return [{'status': 'adjusted-wave'}]

    def udfa(prospects, needs, seed, *, contract_inputs=None):
        calls.append(('udfa', contract_inputs))
        return [{'status': 'adjusted-udfa'}]

    monkeypatch.setattr(te, 'resign_decisions', resign)
    monkeypatch.setattr(fae, 'run_wave', wave)
    monkeypatch.setattr(fae, 'udfa_market', udfa)
    monkeypatch.setattr(lop, 'contract_policy', lambda snap, counterfactual=False: {
        'mode': 'active' if counterfactual else max((pf.snapshot_mode(snap, f) for f in lop.CONTRACT_FEATURES),
                                                     key=lop.ORDER.get)})
    return calls


class BaseDeps(lop.PersonalityDeps):
    """Baseline engine calls answered without a database (the league office's own Deps would read it)."""

    def __init__(self, modes, features=None):
        super().__init__(None, LEAGUE, modes, features or FakeFeatures())

    def free_agent_pool(self, cur, season, target):
        return []


def base_patch(monkeypatch):
    monkeypatch.setattr(lo.Deps, 'resign_decisions', lambda self, cur, s, seed: [{'kind': 'baseline-resign'}])
    monkeypatch.setattr(lo.Deps, 'run_wave', lambda self, p, o, w, seed: [{'status': 'baseline-wave'}])
    monkeypatch.setattr(lo.Deps, 'udfa_market', lambda self, p, n, seed: [{'status': 'baseline-udfa'}])


# --------------------------------------------------------------------------------------------- deps_for ---
def test_deps_for_picks_plain_deps_when_everything_is_off():
    for snap in (None, snap_modes(), snap_modes(producers='active')):
        d = lop.deps_for(None, snap)
        assert type(d) is lo.Deps
    assert lop.deps_for(None, snap_modes())  .modes_snapshot == snap_modes()
    assert isinstance(lop.deps_for(None, snap_modes(retirement='shadow')), lop.PersonalityDeps)


def test_default_deps_reads_league_state_and_the_switchboard(monkeypatch):
    class Cur:
        def execute(self, sql, params=()):
            self.row = (LEAGUE,)

        def fetchone(self):
            return self.row
    monkeypatch.setattr(pf, 'snapshot', lambda cur, league: snap_modes(contracts_ambition='shadow'))
    d = lop.default_deps(Cur())
    assert isinstance(d, lop.PersonalityDeps) and d.mode('contracts_ambition') == 'shadow'


# --------------------------------------------------------------------------------------------- contracts ---
@pytest.mark.parametrize('stage_call', ['resign', 'wave', 'udfa'])
def test_contract_shadow_returns_the_baseline_and_records_the_adjusted(monkeypatch, engines, stage_call):
    base_patch(monkeypatch)
    d = BaseDeps(snap_modes(contracts_ambition='shadow'))
    d.begin_stage('RESIGN', 2026, 25, None, 'seed')
    call = {'resign': lambda: d.resign_decisions(None, 2026, 'seed'),
            'wave': lambda: d.run_wave([{'id': 1}], [], 1, 'seed'),
            'udfa': lambda: d.udfa_market([{'id': 1}], {}, 'seed')}[stage_call]
    out = call()
    assert out[0] in ({'kind': 'baseline-resign'}, {'status': 'baseline-wave'}, {'status': 'baseline-udfa'})
    r = d.receipts[-1]
    assert r['status'] == 'recorded' and r['baseline'] == out and r['adjusted'][0] != out[0]
    assert engines[-1][1][1]['policy']['mode'] == 'active'          # the counterfactual is computed as active


@pytest.mark.parametrize('stage_call', ['resign', 'wave', 'udfa'])
def test_contract_active_returns_the_adjusted(monkeypatch, engines, stage_call):
    base_patch(monkeypatch)
    d = BaseDeps(snap_modes(contracts_ambition='active', contracts_relationships='active'))
    d.begin_stage('FREE_AGENCY', 2026, 26, 1, 'seed')
    d.bind_run(55)
    out = {'resign': lambda: d.resign_decisions(None, 2026, 'seed'),
           'wave': lambda: d.run_wave([{'id': 1}], [], 1, 'seed'),
           'udfa': lambda: d.udfa_market([{'id': 1}], {}, 'seed')}[stage_call]()
    assert 'adjusted' in str(out[0]) and d.receipts[-1]['status'] == 'applied'
    assert d.features.calls[-1][3] == '55:FREE_AGENCY:1'             # one frozen batch per stage write


def test_contract_off_is_the_exact_original_call(monkeypatch, engines):
    base_patch(monkeypatch)
    d = BaseDeps(snap_modes(retirement='shadow'))
    assert d.resign_decisions(None, 2026, 'seed') == [{'kind': 'baseline-resign'}]
    assert engines == [] and d.receipts == []


def test_shadow_adjusted_error_is_recorded_not_raised(monkeypatch, engines):
    base_patch(monkeypatch)
    d = BaseDeps(snap_modes(contracts_ambition='shadow'), FakeFeatures(fail=True))
    assert d.resign_decisions(None, 2026, 'seed') == [{'kind': 'baseline-resign'}]
    assert d.receipts[-1]['status'] == 'error'
    d = BaseDeps(snap_modes(contracts_ambition='active'), FakeFeatures(fail=True))
    with pytest.raises(RuntimeError):
        d.resign_decisions(None, 2026, 'seed')


def test_contract_policy_from_the_snapshot():
    assert lop.contract_policy(snap_modes()) is None
    cfg = lop.contract_policy(snap_modes(contracts_ambition='shadow'))
    assert cfg['mode'] == 'shadow' and cfg['ambition_enabled'] and not cfg['relationship_enabled']
    cfg = lop.contract_policy(snap_modes(contracts_ambition='shadow'), counterfactual=True)
    assert cfg['mode'] == 'active' and cfg['ambition_enabled']


# ------------------------------------------------------------------------------------------- retirement ---
def retire_snap():
    return tlo.snapshot("OffSeason", "OffSeason", 0, 1, players=[
        tlo.player(1, 0, pos="QB", ovr=85), tlo.player(2, 0, years_pro=1),
        tlo.player(8, 32, status="Retired", age=30, ovr=80), tlo.player(9, 32, status="Retired", age=37, ovr=70)])


def test_retirement_shadow_keeps_todays_rule_and_records_the_policy():
    snap = retire_snap()
    base = tlo.plan("OFFSEASON_START", snap, tlo.FakeDeps(snap))
    d = PDeps(snap_modes(retirement='shadow'), snap)
    p = tlo.plan("OFFSEASON_START", snap, d)
    assert p["db"]["retirements"] == base["db"]["retirements"]
    assert p["edits"] == base["edits"]
    rec = [r for r in d.receipts if r['feature'] == 'retirement']
    assert len(rec) == 2 and all(r['status'] == 'recorded' for r in rec)
    assert p["summary"]["personality"]["counts"] == {'retirement:shadow:recorded': 2}


def test_retirement_active_uses_the_policy_and_falls_back_when_blocked():
    snap = retire_snap()
    d = PDeps(snap_modes(retirement='active'), snap)
    p = tlo.plan("OFFSEASON_START", snap, d)
    keeps = {r["player_id"]: (r["mu_keep"], r["reason"]) for r in p["db"]["retirements"]}
    assert keeps[tlo.pid(8)] == (False, 'retired by the retirement policy')        # today's rule kept him
    assert keeps[tlo.pid(9)][0] is False and 'blocked' in keeps[tlo.pid(9)][1]
    assert 8 not in {e["pgid"] for e in p["edits"].get("players", []) if "TeamIndex" in e.get("set", {})}


def test_retirement_decision_never_retires_an_active_player():
    d = PDeps(snap_modes(retirement='active'))
    keep, reason = d.retirement_decision(None, tlo.player(3, 1), 2026, 'seed')
    assert keep is True and 'never retires an active player' in reason
    assert d.features.calls == []


def test_base_deps_retirement_is_keep_retired():
    p = tlo.player(8, 32, status="Retired", age=30, ovr=80)
    assert lo.Deps().retirement_decision(None, p, 2026, 's') == lo.keep_retired(p, 2026, 's')


# ----------------------------------------------------------------------------------------------- charts ---
def test_chart_shadow_is_computed_recorded_and_not_used():
    snap = tlo.snapshot()
    base = tlo.plan("REGULAR_WEEK", snap, tlo.FakeDeps(snap))
    d = PDeps(snap_modes(development_opportunity='shadow'), snap)
    p = tlo.plan("REGULAR_WEEK", snap, d)
    assert p["edits"] == base["edits"]
    assert set(d.chart_kw) == {None} and d.features.calls
    assert {r['status'] for r in d.receipts if r['feature'] == 'development_opportunity'} == {'recorded'}


def test_chart_active_passes_the_adjustments():
    snap = tlo.snapshot()
    d = PDeps(snap_modes(development_opportunity='active'), snap)
    tlo.plan("REGULAR_WEEK", snap, d)
    assert d.chart_kw and all(kw and 'WR' in kw for kw in d.chart_kw)


def test_base_deps_chart_adjustments_are_none():
    assert lo.Deps().chart_adjustments(None, 1, 2026, 3, [], {}, {}) is None


# ---------------------------------------------------------------------------------- development learning ---
def test_development_off_uses_the_progression_hook(monkeypatch):
    d = BaseDeps(snap_modes(retirement='shadow'))
    seen = []
    monkeypatch.setattr(lo.Deps, 'progression_weekly', lambda self, *a: seen.append(a) or {'p': 1})
    assert d.progression_weekly(None, 2026, 3, {'players': []}, 'fp') == {'p': 1} and seen


def test_development_shadow_passes_a_shadow_config(monkeypatch):
    from engines import form_engine, progression_checkpoint as pc, progression_inputs
    got = {}
    monkeypatch.setattr(progression_inputs, 'fetch_inputs', lambda cur, s, w: {10: {'pgid': 1}, 11: {'pgid': 99}})
    monkeypatch.setattr(form_engine, 'weekly_form', lambda cur, s, w: {})
    monkeypatch.setattr(pc, 'prepare_pass', lambda cur, snap, s, w, **kw: got.update(kw) or {'event_ids': []})
    d = BaseDeps(snap_modes(development_learning='shadow'))
    d.progression_weekly(None, 2026, 3, {'players': [{'pgid': 1}]}, 'fp')
    assert got['development_config'].mode == 'shadow' and list(got['player_inputs']) == [10]
    assert 'development_context' in got['player_inputs'][10]
    assert d.receipts[-1]['status'] == 'recorded'


# ------------------------------------------------------------------------------------ recovery snapshot ---
def test_mode_snapshot_is_stored_in_the_operation_and_reused_by_recovery(monkeypatch, tmp_path):
    monkeypatch.delenv(lo.FRANCHISE_CLOSED_ENV, raising=False)
    save = tmp_path / "CAREER-TEST"
    save.write_bytes(b"ORIGINAL SAVE")
    modes = snap_modes(retirement='shadow')
    deps = PDeps(modes, tlo.snapshot(), prep_doc={"players": [{"pgid": 1, "set": {"SpeedRating": 81}}],
                                                  "records": []})
    cur, run_id, _ = tlo._run(str(save), deps)
    op = cur.run_row(run_id)["summary"]["operation"]
    assert op["personality_modes"] == modes and deps.run_id == run_id
    assert "personality" in cur.run_row(run_id)["summary"]

    # recovery rebuilds deps from the stored snapshot and never reads the switchboard
    monkeypatch.setattr(pf, 'snapshot', lambda *a: pytest.fail('recovery read the switchboard'))
    seen = []
    monkeypatch.setattr(lop, 'deps_for', lambda c, s: seen.append(s) or lo.Deps())
    assert lo.recover(cur, run_id, out=lambda s: None) == "ALREADY_WRITTEN"
    assert seen == [modes]
