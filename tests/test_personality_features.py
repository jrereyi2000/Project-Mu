"""Personality feature switchboard (docs/done/PERSONALITY_ACTIVATION_PLAN.md §2.1-§2.2, §2.5, U1).

Offline: a fake cursor dispatches on each statement's '/* q:<name> */' tag over in-memory tables. The opt-in
scratch test (PERSONALITY_FEATURES_TEST_DSN) applies the migration twice and checks an incompatible table is refused.
"""
import json
import os
import re
from pathlib import Path

import pytest

from personality import features as pf

LEAGUE = 4242
UNIVERSE = 'mu:00000000-0000-4000-8000-000000000001'
MIGRATION = Path(__file__).resolve().parent.parent / 'db' / 'migrations' / '2027_01_31_personality_feature_modes.sql'
PRODUCERS_VERSION = '2027_01_28_relationship_producers'
LEDGER_VERSIONS = {pf.version_of(pf.MIGRATIONS / m) for _, m in pf.REQUIREMENTS.values() if m}


class FakeCursor:
    def __init__(self, ready=True, batch=False, league_state=(LEAGUE, 2027, 5)):
        self.ready, self.batch, self.league_state = ready, batch, league_state
        self.modes, self.changes, self.clearances = {}, [], {}
        self.ledger = set(LEDGER_VERSIONS)          # schema_migrations: every requirement applied by default
        self.queries = []
        self._out = []

    def execute(self, sql, params=()):
        tag = re.search(r'/\* q:(\w+) \*/', sql).group(1)
        self.queries.append(tag)
        getattr(self, 'q_' + tag)(params)

    def fetchone(self):
        return self._out[0] if self._out else None

    def fetchall(self):
        return list(self._out)

    # -- queries
    def q_ready(self, p):
        self._out = [(self.ready, self.ready, self.ready)]

    def q_migration(self, p):
        self._out = [(1,)] if p[0] in self.ledger else []

    def q_universe_ready(self, p):
        self._out = [(True,)]

    def q_universe_league(self, p):
        self._out = [(str(LEAGUE),)] if p[0] == UNIVERSE else []

    def q_modes(self, p):
        self._out = [(f,) + tuple(r[k] for k in ('mode', 'version', 'config', 'review_ref', 'ss', 'st', 'set_by'))
                     for (lg, f), r in sorted(self.modes.items()) if lg == p[0]]

    def q_upsert(self, p):
        lg, f, mode, version, cfg, review, ss, st, set_by = p
        self.modes[(lg, f)] = {'mode': mode, 'version': version, 'config': json.loads(cfg) if isinstance(cfg, str)
                               else cfg.obj, 'review_ref': review, 'ss': ss, 'st': st, 'set_by': set_by}
        self._out = []

    def q_change(self, p):
        self.changes.append((len(self.changes) + 1,) + tuple(p))
        self._out = []

    def q_history(self, p):
        lg, f, _ = p
        self._out = [(c[0], c[2], c[3], c[4], c[5], c[6], c[7], c[8], c[9], c[10], None) for c in self.changes
                     if c[1] == lg and (f is None or c[2] == f)]

    def _cl_row(self, c):
        return (c['feature'], c['version'], c['source_db'], c['source_label'], c['fs'], c['ft'], c['ts'], c['tt'],
                c['report_path'], c['report_sha256'], c['config'], c['cleared_by'])

    def q_clearance(self, p):
        c = self.clearances.get(tuple(p))
        self._out = [self._cl_row(c)] if c else []

    def q_clearance_latest(self, p):
        rows = [c for (lg, f, v), c in self.clearances.items() if (lg, f) == tuple(p)]
        self._out = [self._cl_row(rows[-1])] if rows else []

    def q_clearance_insert(self, p):
        lg, f, v, db, label, fs, ft, ts, tt, path, sha, cfg, by = p
        self.clearances[(lg, f, v)] = {'feature': f, 'version': v, 'source_db': db, 'source_label': label, 'fs': fs,
                                       'ft': ft, 'ts': ts, 'tt': tt, 'report_path': path, 'report_sha256': sha,
                                       'config': json.loads(cfg) if isinstance(cfg, str) else cfg.obj,
                                       'cleared_by': by}
        self._out = []

    def q_lifecycle_ready(self, p):
        self._out = [(True,)]

    def q_lifecycle_batch(self, p):
        self._out = [(self.batch,)]

    def q_dbname(self, p):
        self._out = [('madden_tune_2026a',)]

    def q_league_state(self, p):
        self._out = [self.league_state]


NOW = {'season': 2027, 'tick': 5}


def shadow(cur, feature, season=2026, tick=10, version='v1'):
    return pf.set_mode(cur, LEAGUE, feature, 'shadow', version=version, review_ref='r', now={'season': season,
                                                                                               'tick': tick})


def activate_all_before(cur, feature, now=NOW):
    for f in pf.FEATURES[:pf.FEATURES.index(feature)]:
        pf.set_mode(cur, LEAGUE, f, 'active', version='v1', review_ref='r', now=now, force_reason='test setup')


@pytest.fixture(autouse=True)
def everything_installed(monkeypatch):
    monkeypatch.setattr(pf, 'installed', lambda feature: (True, None))
    # Every shadow/active write must name an actor (D5: no literal 'JR' fallback), so the suite names one for the
    # cases that are not about the actor chain; those set or delete the env themselves.
    monkeypatch.setenv('MU_SET_BY', 'pytest')
    monkeypatch.delenv('MU_AGENT_MODEL', raising=False)


# ---------------------------------------------------------------------------------------------- reads ---
def test_missing_row_and_missing_tables_read_off():
    cur = FakeCursor()
    assert pf.mode(cur, LEAGUE, 'retirement') == 'off'
    assert pf.config(cur, LEAGUE, 'retirement')['config'] == {}
    assert pf.mode(FakeCursor(ready=False), LEAGUE, 'producers') == 'off'
    snap = pf.snapshot(FakeCursor(ready=False), LEAGUE)
    assert snap['source'] == 'default' and set(snap['features']) == set(pf.FEATURES)
    assert pf.snapshot_mode(None, 'retirement') == 'off'


def test_unknown_feature_and_mode_are_bad_requests():
    with pytest.raises(pf.FeatureError) as e:
        pf.mode(FakeCursor(), LEAGUE, 'telepathy')
    assert e.value.code == pf.EXIT_INVALID
    with pytest.raises(pf.FeatureError):
        pf.set_mode(FakeCursor(), LEAGUE, 'producers', 'on', version='v1', now=NOW)


def test_universe_id_resolves_to_its_league():
    cur = FakeCursor()
    shadow(cur, 'contracts_ambition')
    assert pf.config(cur, UNIVERSE, 'contracts_ambition')['mode'] == 'shadow'
    assert pf.mode(cur, 'mu:00000000-0000-4000-8000-000000000009', 'contracts_ambition') == 'off'


def test_contract_store_reads_the_switchboard():
    from engines import personality_contract_store as cs
    cur = FakeCursor()
    a = cs.read_contract_activation(cur, UNIVERSE)
    assert a['source'] == 'switchboard' and a['config']['mode'] == 'off'
    pf.set_mode(cur, LEAGUE, 'contracts_ambition', 'shadow', version='contracts-v1', review_ref='r', now=NOW)
    a = cs.read_contract_activation(cur, UNIVERSE)
    assert a['families']['ambition'] == 'shadow' and a['config']['mode'] == 'shadow'


def test_snapshot_is_stable_and_complete():
    cur = FakeCursor()
    shadow(cur, 'producers')
    a, b = pf.snapshot(cur, LEAGUE), pf.snapshot(cur, LEAGUE)
    assert a == b and list(a['features']) == list(pf.FEATURES)
    assert pf.snapshot_mode(a, 'producers') == 'shadow' and pf.snapshot_mode(a, 'evolution') == 'off'


# --------------------------------------------------------------------------------------------- rules ---
def test_order_rule_refuses_and_allows():
    cur = FakeCursor()
    shadow(cur, 'relationships', season=2025)
    with pytest.raises(pf.FeatureRefused) as e:
        pf.set_mode(cur, LEAGUE, 'relationships', 'active', version='v1', review_ref='r', now=NOW)
    assert 'D2' in str(e.value) and 'producers' in str(e.value) and e.value.code == pf.EXIT_REFUSED
    activate_all_before(cur, 'relationships')
    assert pf.set_mode(cur, LEAGUE, 'relationships', 'active', version='v1', review_ref='r', now=NOW)['to_mode'] == \
        'active'


def test_profiles_count_as_active_once_a_lifecycle_batch_is_applied():
    cur = FakeCursor()
    activate_all_before(cur, 'profiles')
    shadow(cur, 'evolution')
    with pytest.raises(pf.FeatureRefused, match='profiles'):
        pf.set_mode(cur, LEAGUE, 'evolution', 'active', version='v1', review_ref='r', now=NOW)
    cur.batch = True
    assert pf.set_mode(cur, LEAGUE, 'evolution', 'active', version='v1', review_ref='r', now=NOW)['to_mode'] == 'active'


def test_shadow_season_rule_at_the_boundary():
    cur = FakeCursor()
    shadow(cur, 'producers', season=2026, tick=25)           # after tick 24 of the previous season: too late
    with pytest.raises(pf.FeatureRefused, match='D3'):
        pf.set_mode(cur, LEAGUE, 'producers', 'active', version='v1', review_ref='r', now=NOW)
    cur2 = FakeCursor()
    shadow(cur2, 'producers', season=2026, tick=24)          # exactly tick 24: one full season
    assert pf.set_mode(cur2, LEAGUE, 'producers', 'active', version='v1', review_ref='r', now=NOW)['to_mode'] == \
        'active'


def test_a_new_version_needs_its_own_shadow_season():
    cur = FakeCursor()
    shadow(cur, 'producers', season=2025)
    with pytest.raises(pf.FeatureRefused, match='D3'):
        pf.set_mode(cur, LEAGUE, 'producers', 'active', version='v2', review_ref='r', now=NOW)
    shadow(cur, 'producers', season=2027, tick=5, version='v2')
    assert pf.config(cur, LEAGUE, 'producers')['shadow_since'] == {'season': 2027, 'tick': 5}


def test_staying_in_shadow_keeps_the_shadow_start():
    cur = FakeCursor()
    shadow(cur, 'producers', season=2026, tick=3)
    shadow(cur, 'producers', season=2026, tick=20)
    assert pf.config(cur, LEAGUE, 'producers')['shadow_since'] == {'season': 2026, 'tick': 3}


def test_force_is_recorded_and_off_is_always_allowed():
    cur = FakeCursor()
    r = pf.set_mode(cur, LEAGUE, 'retirement', 'active', version='v1', review_ref='r', now=NOW,
                    force_reason='JR: proof passed')
    assert r['force_reason'] == 'JR: proof passed'
    assert cur.changes[-1][7] == 'JR: proof passed'
    assert pf.set_mode(cur, LEAGUE, 'retirement', 'off', version='v1', review_ref='r', now=NOW)['to_mode'] == 'off'
    assert pf.config(cur, LEAGUE, 'retirement')['shadow_since'] is None


def test_every_change_writes_history():
    cur = FakeCursor()
    shadow(cur, 'producers')
    pf.set_mode(cur, LEAGUE, 'producers', 'off', version='v1', review_ref='stop', now=NOW)
    h = pf.history(cur, LEAGUE)
    assert [(c['feature'], c['from_mode'], c['to_mode']) for c in h] == [('producers', None, 'shadow'),
                                                                         ('producers', 'shadow', 'off')]
    assert pf.history(cur, LEAGUE, 'retirement') == []


def test_uninstalled_code_cannot_go_to_shadow(monkeypatch):
    # Keep the autouse `everything_installed` stub and report one feature uninstalled, so the refusal is
    # tested whether or not that feature's code happens to exist on disk.
    monkeypatch.setattr(pf, 'installed', lambda feature: (False, f'{feature} code is not installed')
                        if feature == 'scout_directors' else (True, None))
    with pytest.raises(pf.FeatureRefused, match='not installed'):
        pf.set_mode(FakeCursor(), LEAGUE, 'scout_directors', 'shadow', version='v1', review_ref='r', now=NOW)


def test_writes_refuse_without_tables():
    with pytest.raises(pf.FeatureRefused, match='not migrated'):
        pf.set_mode(FakeCursor(ready=False), LEAGUE, 'producers', 'shadow', version='v1', review_ref='r', now=NOW)


def test_shadow_refused_when_the_database_ledger_lacks_the_migration():
    """P5-01a: installed() reads the repo; the target database's schema_migrations is the second half."""
    cur = FakeCursor()
    cur.ledger.discard(PRODUCERS_VERSION)
    with pytest.raises(pf.FeatureRefused,
                       match=r'2027_01_28_relationship_producers\.sql is not applied.*db\.migrate apply'):
        pf.set_mode(cur, LEAGUE, 'producers', 'shadow', version='v1', review_ref='r', now=NOW)
    assert pf.mode(cur, LEAGUE, 'producers') == 'off'
    assert pf.check(cur, LEAGUE, 'producers', 'shadow', version='v1', now=NOW).startswith(
        'producers code is not installed on this database')


def test_shadow_allowed_when_the_database_ledger_has_the_migration():
    cur = FakeCursor()
    assert pf.set_mode(cur, LEAGUE, 'producers', 'shadow', version='v1', review_ref='r',
                       now=NOW)['to_mode'] == 'shadow'


def test_no_ledger_query_for_a_feature_without_a_migration():
    cur = FakeCursor()
    cur.ledger.clear()
    pf.set_mode(cur, LEAGUE, 'development_learning', 'shadow', version='v1', review_ref='r', now=NOW)
    assert 'migration' not in cur.queries
    assert pf.REQUIREMENTS['development_learning'][1] is None


def test_show_reports_the_blocked_next_step(monkeypatch):
    cur = FakeCursor()
    cur.ledger.discard(PRODUCERS_VERSION)
    monkeypatch.setattr(pf, 'connect', lambda dsn: FakeConn(cur))
    out = []
    assert pf.main(['show', '--dsn', 'postgresql://u@localhost/madden_tune'],
                   out=out.append, err=lambda m: None) == 0
    shown = json.loads(out[-1])['features'][0]
    assert shown['feature'] == 'producers' and shown['next'] == 'shadow'
    assert shown['allowed'] is False and '2027_01_28_relationship_producers.sql' in shown['why']


def test_off_is_allowed_on_a_database_with_an_empty_ledger():
    cur = FakeCursor()
    shadow(cur, 'producers')
    cur.ledger.clear()
    cur.queries.clear()
    assert pf.check(cur, LEAGUE, 'producers', 'off', version='v1', now=NOW) is None
    assert pf.set_mode(cur, LEAGUE, 'producers', 'off', version='v1', review_ref='r',
                       now=NOW)['to_mode'] == 'off'
    assert 'migration' not in cur.queries


def test_active_still_needs_d3_with_the_migration_applied():
    cur = FakeCursor()
    activate_all_before(cur, 'producers')
    with pytest.raises(pf.FeatureRefused, match='D3'):
        pf.set_mode(cur, LEAGUE, 'producers', 'active', version='v1', review_ref='r', now=NOW)


def test_set_by_precedence_flag_then_env_then_model_and_no_fallback(monkeypatch):
    cur = FakeCursor()
    receipt = pf.set_mode(cur, LEAGUE, 'producers', 'shadow', version='v1', review_ref='r', now=NOW,
                          set_by='builder-11')
    assert receipt['set_by'] == 'builder-11'
    assert pf.config(cur, LEAGUE, 'producers')['set_by'] == 'builder-11'
    assert pf.history(cur, LEAGUE, 'producers')[0]['set_by'] == 'builder-11'

    monkeypatch.setenv('MU_SET_BY', 'session-name')
    monkeypatch.setenv('MU_AGENT_MODEL', 'some-model')
    assert pf.set_mode(FakeCursor(), LEAGUE, 'producers', 'shadow', version='v1', review_ref='r',
                       now=NOW)['set_by'] == 'session-name'

    monkeypatch.delenv('MU_SET_BY')
    assert pf.set_mode(FakeCursor(), LEAGUE, 'producers', 'shadow', version='v1', review_ref='r',
                       now=NOW)['set_by'] == 'some-model'

    monkeypatch.delenv('MU_AGENT_MODEL')
    with pytest.raises(pf.FeatureError, match='no identifiable actor') as e:
        pf.set_mode(FakeCursor(), LEAGUE, 'producers', 'shadow', version='v1', review_ref='r', now=NOW)
    assert e.value.code == pf.EXIT_INVALID                  # the 'JR' fallback is gone (D5)


def test_no_actor_exits_2_on_a_shadow_or_active_write_but_off_and_reads_stay_permissive(monkeypatch):
    """D5 (JR, 2026-11-12: 'Drop the literal chair fullback'): nobody writes a switch as JR."""
    monkeypatch.delenv('MU_SET_BY', raising=False)
    monkeypatch.delenv('MU_AGENT_MODEL', raising=False)
    cur = FakeCursor()
    for mode in ('shadow', 'active'):
        with pytest.raises(pf.FeatureError, match='no identifiable actor') as e:
            pf.set_mode(cur, LEAGUE, 'producers', mode, version='v1', review_ref='r', now=NOW)
        assert e.value.code == pf.EXIT_INVALID and e.value.code == 2
    assert cur.queries == []                                # nothing was read or written
    assert pf.mode(cur, LEAGUE, 'producers') == 'off'       # reads need no actor
    assert pf.snapshot(cur, LEAGUE)['features']['producers']['mode'] == 'off'

    off = pf.set_mode(cur, LEAGUE, 'producers', 'off', version='v1', review_ref='r', now=NOW)
    assert (off['to_mode'], off['set_by']) == ('off', pf.UNATTRIBUTED)
    assert pf.config(cur, LEAGUE, 'producers')['set_by'] == pf.UNATTRIBUTED

    with pytest.raises(pf.FeatureError, match='cleared_by is not set: no identifiable actor'):
        pf._actor(None, 'cleared_by')                       # a clearance row names its importer, or exits 2

    conn = FakeConn(FakeCursor())
    monkeypatch.setattr(pf, 'connect', lambda dsn: conn)
    err = []
    assert pf.main(['set', 'producers', 'shadow', '--version', 'v1', '--review', 'r',
                    '--dsn', 'postgresql://u@localhost/madden_test'], out=lambda m: None, err=err.append) == 2
    assert 'no identifiable actor' in err[-1] and conn.commits == 0


def test_empty_actor_is_a_bad_request(monkeypatch):
    monkeypatch.setenv('MU_SET_BY', 'session-name')
    with pytest.raises(pf.FeatureError, match='set_by must be non-empty text'):
        pf.set_mode(FakeCursor(), LEAGUE, 'producers', 'shadow', version='v1', review_ref='r', now=NOW,
                    set_by='  ')


def test_next_step_explains():
    cur = FakeCursor()
    assert pf.next_step(cur, LEAGUE, 'producers', now=NOW) == {'next': 'shadow', 'allowed': True, 'why': 'allowed'}
    shadow(cur, 'relationships', season=2027, tick=1)
    step = pf.next_step(cur, LEAGUE, 'relationships', now=NOW)
    assert step['next'] == 'active' and not step['allowed'] and 'D2' in step['why']


# ---------------------------------------------------------------------------------------- clearances ---
def report(tmp_path, text='shadow report'):
    p = tmp_path / 'report.json'
    p.write_text(text)
    return str(p)


def test_export_refuses_without_a_full_shadow_season(tmp_path):
    cur = FakeCursor()
    with pytest.raises(pf.FeatureRefused, match='not in shadow'):
        pf.export_clearance(cur, LEAGUE, 'producers', source_label='CAREER-TUNE-a', now=NOW,
                            report_path=report(tmp_path))
    shadow(cur, 'producers', season=2026, tick=30)
    with pytest.raises(pf.FeatureRefused, match='full simulated season'):
        pf.export_clearance(cur, LEAGUE, 'producers', source_label='CAREER-TUNE-a', now=NOW,
                            report_path=report(tmp_path))


def test_clearance_meets_the_shadow_rule_for_its_feature_and_version_only(tmp_path):
    tune = FakeCursor()
    pf.set_mode(tune, LEAGUE, 'producers', 'shadow', version='v1', config={'k': 1}, review_ref='r',
                now={'season': 2026, 'tick': 1})
    doc = pf.export_clearance(tune, LEAGUE, 'producers', source_label='CAREER-TUNE-a', now=NOW,
                              report_path=report(tmp_path))
    assert doc['source_db'] == 'madden_tune_2026a' and doc['config'] == {'k': 1}
    real = FakeCursor()
    assert pf.import_clearance(real, LEAGUE, doc)['status'] == 'imported'
    assert pf.import_clearance(real, LEAGUE, doc)['status'] == 'replayed'
    with pytest.raises(pf.FeatureRefused, match='D3'):
        pf.set_mode(real, LEAGUE, 'producers', 'active', version='v2', review_ref='r', now={'season': 2026, 'tick': 1})
    r = pf.set_mode(real, LEAGUE, 'producers', 'active', version='v1', review_ref='r', now={'season': 2026, 'tick': 1})
    assert r['to_mode'] == 'active' and r['config'] == {'k': 1}


def test_tampered_report_hash_is_refused(tmp_path):
    tune = FakeCursor()
    shadow(tune, 'producers', season=2025)
    path = report(tmp_path)
    doc = pf.export_clearance(tune, LEAGUE, 'producers', source_label='CAREER-TUNE-a', now=NOW, report_path=path)
    Path(path).write_text('edited after export')
    with pytest.raises(pf.FeatureRefused, match='does not match'):
        pf.import_clearance(FakeCursor(), LEAGUE, doc)
    with pytest.raises(pf.FeatureRefused, match='D3'):
        pf.import_clearance(FakeCursor(), LEAGUE, dict(doc, shadow_from={'season': 2027, 'tick': 1}))


def test_activate_cleared_follows_the_order_and_stops_at_the_first_gap(tmp_path):
    real = FakeCursor()
    path = report(tmp_path)
    for feature in ('producers', 'relationships', 'evolution'):
        tune = FakeCursor()
        shadow(tune, feature, season=2025)
        pf.import_clearance(real, LEAGUE, pf.export_clearance(tune, LEAGUE, feature, source_label='T', now=NOW,
                                                              report_path=path))
    out = pf.activate_cleared(real, LEAGUE, now={'season': 2026, 'tick': 1}, review_ref='go')
    assert [(o['feature'], o['status']) for o in out] == [('producers', 'activated'), ('relationships', 'activated'),
                                                          ('profiles', 'stopped')]
    assert pf.mode(real, LEAGUE, 'evolution') == 'off'
    real.batch = True                                     # lifecycle inventory applied: profiles counts as active
    out = pf.activate_cleared(real, LEAGUE, now={'season': 2026, 'tick': 1}, review_ref='go')
    assert [o['status'] for o in out[:4]] == ['already_active', 'already_active', 'already_active', 'activated']
    assert out[4] == {'feature': 'focus_refresh', 'status': 'stopped', 'reason': 'no clearance imported'}


# ----------------------------------------------------------------------------------------------- CLI ---
class FakeConn:
    def __init__(self, cur):
        self.cur, self.commits, self.rollbacks = cur, 0, 0

    def cursor(self):
        return self.cur

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def test_cli_exit_codes_and_dsn_guard(monkeypatch):
    cur = FakeCursor()
    conn = FakeConn(cur)
    monkeypatch.setattr(pf, 'connect', lambda dsn: conn)
    out, err = [], []
    dsn = 'postgresql://u@localhost/madden_test'
    assert pf.main(['set', 'producers', 'shadow', '--version', 'v1', '--review', 'ok', '--dsn', dsn],
                   out=out.append, err=err.append) == 0
    assert conn.commits == 1 and pf.mode(cur, LEAGUE, 'producers') == 'shadow'
    assert pf.main(['set', 'relationships', 'active', '--version', 'v1', '--review', 'x', '--dsn', dsn],
                   out=out.append, err=err.append) == pf.EXIT_REFUSED
    assert pf.main(['show', '--dsn', dsn], out=out.append, err=err.append) == 0
    shown = json.loads(out[-1])
    assert shown['now'] == NOW and shown['features'][0]['mode'] == 'shadow'
    assert pf.main(['history', '--dsn', dsn], out=out.append, err=err.append) == 0
    for bad in ('postgresql://u@localhost/madden_2036', 'postgresql://u@localhost/postgres'):
        assert pf.main(['show', '--dsn', bad], out=out.append, err=err.append) == pf.EXIT_INVALID
    assert pf.main(['set', 'producers', 'on', '--version', 'v1', '--review', 'x', '--dsn', dsn],
                   out=out.append, err=lambda m: None) == pf.EXIT_INVALID
    monkeypatch.delenv('DATABASE_URL', raising=False)
    assert pf.main(['show'], out=out.append, err=err.append) == pf.EXIT_INVALID


def test_cli_clearance_round_trip(monkeypatch, tmp_path):
    tune, real = FakeCursor(), FakeCursor()
    shadow(tune, 'producers', season=2025)
    path = report(tmp_path)
    doc_path = tmp_path / 'clear.json'
    dsn = 'postgresql://u@localhost/madden_tune'
    monkeypatch.setattr(pf, 'connect', lambda d: FakeConn(tune))
    assert pf.main(['export-clearance', 'producers', '--label', 'CAREER-TUNE-a', '--out', str(doc_path),
                    '--report', path, '--dsn', dsn], out=lambda m: None) == 0
    monkeypatch.setattr(pf, 'connect', lambda d: FakeConn(real))
    assert pf.main(['import-clearance', str(doc_path), '--dsn', dsn], out=lambda m: None) == 0
    assert pf.main(['activate-cleared', '--review', 'go', '--dsn', dsn], out=lambda m: None) == 0
    assert pf.mode(real, LEAGUE, 'producers') == 'active'


def test_cli_set_by_and_cleared_by_flags(monkeypatch, tmp_path):
    """P5-01a gap 1: the CLI records who threw the switch, not an assumed 'JR'."""
    tune, real = FakeCursor(), FakeCursor()
    shadow(tune, 'producers', season=2025)
    path = report(tmp_path)
    doc_path = tmp_path / 'clear.json'
    dsn = 'postgresql://u@localhost/madden_tune'
    monkeypatch.setattr(pf, 'connect', lambda d: FakeConn(tune))
    assert pf.main(['export-clearance', 'producers', '--label', 'CAREER-TUNE-a', '--out', str(doc_path),
                    '--report', path, '--dsn', dsn], out=lambda m: None) == 0
    monkeypatch.setattr(pf, 'connect', lambda d: FakeConn(real))
    assert pf.main(['import-clearance', str(doc_path), '--cleared-by', 'audit-5', '--dsn', dsn],
                   out=lambda m: None) == 0
    assert pf._clearance(real, LEAGUE, 'producers', 'v1')['cleared_by'] == 'audit-5'
    assert pf.main(['activate-cleared', '--review', 'go', '--set-by', 'builder-11', '--dsn', dsn],
                   out=lambda m: None) == 0
    assert pf.config(real, LEAGUE, 'producers')['set_by'] == 'builder-11'
    monkeypatch.delenv('MU_SET_BY')                                   # no flag: the harness env names the actor
    monkeypatch.setenv('MU_AGENT_MODEL', 'deepseek-v4.1-flash')
    out = []
    assert pf.main(['set', 'producers', 'shadow', '--version', 'v1', '--review', 'r', '--dsn', dsn],
                   out=out.append, err=lambda m: None) == 0
    assert json.loads(out[-1])['set_by'] == 'deepseek-v4.1-flash'
    assert pf.history(real, LEAGUE, 'producers')[-1]['set_by'] == 'deepseek-v4.1-flash'


# -------------------------------------------------------------------------------------------- migration ---
def test_migration_is_transactional_checked_and_seeds_nothing():
    sql = MIGRATION.read_text()
    assert sql.count('BEGIN;') == 1 and sql.rstrip().endswith('COMMIT;')
    for table in pf.TABLES:
        assert f'CREATE TABLE IF NOT EXISTS {table}' in sql
        assert f"('{table}'," in sql                       # checked for compatibility on a rerun
    assert 'INSERT' not in sql.upper().replace('INSERTS', '')
    assert "CHECK (mode IN ('off','shadow','active'))" in sql


@pytest.mark.skipif(not os.environ.get('PERSONALITY_FEATURES_TEST_DSN'), reason='opt-in scratch database')
def test_scratch_migration_twice_and_incompatible_table_refused():
    import psycopg
    dsn = os.environ['PERSONALITY_FEATURES_TEST_DSN']
    assert pf.database_name(dsn) not in ('madden', 'madden_2036', 'postgres')
    with psycopg.connect(dsn, autocommit=True) as conn:
        cur = conn.cursor()
        cur.execute('CREATE SCHEMA IF NOT EXISTS pf_test')
        cur.execute('SET search_path TO pf_test')
        for t in pf.TABLES:
            cur.execute(f'DROP TABLE IF EXISTS {t}')
        cur.execute(MIGRATION.read_text())
        cur.execute(MIGRATION.read_text())
        cur.execute('SELECT count(*) FROM personality_feature_modes')
        assert cur.fetchone()[0] == 0
        cur.execute('DROP TABLE personality_feature_clearances')
        cur.execute('CREATE TABLE personality_feature_clearances (league_id BIGINT PRIMARY KEY)')
        with pytest.raises(psycopg.Error, match='incompatible'):
            cur.execute(MIGRATION.read_text())
        cur.execute('ROLLBACK')   # the migration's own BEGIN left the refused transaction open
        cur.execute('DROP SCHEMA pf_test CASCADE')


def test_focus_refresh_follows_evolution_with_its_requirement():
    """INFERRED_SLIDERS_AND_FOCUS_PLAN §2.7: focus_refresh sits right after evolution in the D2 order."""
    assert pf.FEATURES.index('focus_refresh') == pf.FEATURES.index('evolution') + 1
    assert pf.REQUIREMENTS['focus_refresh'] == ('personality.focus_run', '2027_02_03_persona_focus.sql')
    assert (pf.MIGRATIONS / '2027_02_03_persona_focus.sql').exists()



def test_coaching_tree_hiring_follows_owner_hiring_off_by_default():
    """COACHING_TREES_PLAN §2.8: coaching_tree_hiring sits right after owner_hiring, needs no migration, reads off."""
    assert pf.FEATURES.index('coaching_tree_hiring') == pf.FEATURES.index('owner_hiring') + 1
    assert pf.REQUIREMENTS['coaching_tree_hiring'] == ('engines.coaching_tree_hiring', None)
    assert pf.installed('coaching_tree_hiring') == (True, None)
    assert pf._off('coaching_tree_hiring')['mode'] == 'off'
