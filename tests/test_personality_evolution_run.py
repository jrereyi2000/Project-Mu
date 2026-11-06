"""Evolution season facts, run_season end to end, the report and the CLI (PERSONALITY_EVOLUTION_PLAN.md §2.1,
§2.5, §4). Offline: a fake cursor answers each tagged facts query; the persona store is a fake."""
from copy import deepcopy
import types

import pytest

from personality import core, evolution_facts as ef, evolution_run as er
from personality.core import Identity, initialize

U = 'mu:facts-universe'
S = 2029


def ident(role, key):
    return Identity(U, role, key)


A, B, C = ident('player', 'a'), ident('player', 'b'), ident('player', 'c')
X, Y, Z = ident('coach', 'x'), ident('coach', 'y'), ident('coach', 'z')


def meta(traits):
    return {'traits': list(traits), 'sliders': dict(temperament=5, sociability=5, confidence=5, openness=5,
                                                    warmth=5, drive=5)}


PEOPLE = [  # persona_type, persona_id, birth_key, metadata, namespace, external_id
    ('player', A.persona_id, 'a', meta(['RING_CHASER']), 'db-player', '101'),
    ('player', B.persona_id, 'b', meta([]), 'db-player', '201'),
    ('player', C.persona_id, 'c', meta([]), 'db-player', '301'),
    ('coach', X.persona_id, 'x', meta([]), 'db-coach', '7'),
    ('coach', Y.persona_id, 'y', meta(['RING_CHASER']), 'db-coach', '8'),
    ('coach', Y.persona_id, 'y', meta(['RING_CHASER']), 'db-coordinator', '30'),
    ('coach', Z.persona_id, 'z', meta([]), 'db-coordinator', '31'),
]

PLAYER_SEASONS = [  # id, player_id, season, team_id, position, age, years_of_experience, released_week
    (1, 101, 2026, 1, 'WR', 27, 4, None), (2, 101, 2027, 1, 'WR', 28, 5, None),
    (3, 101, 2028, 1, 'WR', 29, 6, None), (4, 101, 2029, 1, 'WR', 30, 7, None),
    (5, 102, 2028, 1, 'TE', 33, 11, None),           # A's mentor in 2028 (REC group, Awareness 90)
    (6, 103, 2028, 1, 'WR', 31, 9, None),            # older but less aware
    (7, 201, 2029, 2, 'CB', 26, 3, 5), (8, 301, 2029, 2, 'QB', 24, 1, None),
]
TEAM_SEASONS = [(1, 2028, 12, 5), (1, 2029, 13, 4), (2, 2027, 9, 8), (2, 2028, 6, 11), (2, 2029, 4, 13)]
GAMES = [(500, 'WILD_CARD', 1, 3, 30, 20), (510, 'SUPER_BOWL', 4, 1, 17, 24)]
AWARDS = [(70, 'First Team All-Pro', 101, None), (71, 'COTY', None, 8), (72, 'Second Team All-Pro', 301, None)]
COACH_TENURES = [(7, 2, 2027, 2029), (8, 1, 2029, None)]
COORD_TENURES = [(30, 2, 'OC', 2026, 2028), (31, 2, 'OC', 2029, None)]
HC_SEAT = [(2, 7, 1.4, True, {'firing_line': 1.0})]
CO_SEAT = [(2, 'OC', 31, 0.8, False, {})]
STAFF = [(900, 8)]
RETIRE = [(40, 102)]
ATTRS = [(102, {'Awareness': 90}), (103, {'Awareness': 86})]


def env(subject, sid, kind, season, tick, payload, target=None):
    return {'universe_id': U, 'event_id': sid, 'source_event_id': sid, 'kind': kind,
            'occurred_at': {'season': season, 'tick': tick},
            'subject': {'kind': 'person', 'universe_id': U, 'role': subject.role, 'persona_id': subject.persona_id},
            'target': target, 'payload': payload, 'supersedes_source_event_id': None}


def team(n):
    return {'kind': 'team', 'universe_id': U, 'team_key': f'team:{n}'}


EVENTS = (
    [env(B, 'mu:release:2029:5:201', 'treatment', S, 5,
         {'treatment_kind': 'dismissal', 'team_key': 'team:2', 'rule': 'release_dismissal@producers-v1'}, team(2)),
     env(B, 'mu:arrive:2029:7:201:1', 'situation', S, 7, {'situation_kind': 'arrival', 'state': 'uncertain'}, team(1)),
     env(B, 'mu:injury:55', 'situation', S, 15, {'situation_kind': 'injury', 'state': 'negative', 'weeks_out': 9},
         team(1))]
    + [env(B, f'mu:role:2029:{t}:201', 'role_window', S, t, {'eligible': True, 'assessment': 'unsatisfied'}, team(1))
       for t in range(8, 14)]
    + [env(B, 'mu:role:2029:14:201', 'role_window', S, 14, {'eligible': False, 'assessment': 'unsatisfied'}, team(1)),
       # C: a release before the window (2028 tick 24) and one inside it (2028 tick 26), each with a signing
       env(C, 'mu:release:2028:20:301', 'treatment', S - 1, 20,
           {'treatment_kind': 'dismissal', 'team_key': 'team:3', 'rule': 'release_dismissal@producers-v1'}, team(3)),
       env(C, 'mu:arrive:2028:22:301:4', 'situation', S - 1, 22, {'situation_kind': 'arrival'}, team(4)),
       env(C, 'mu:role:2029:25:301', 'role_window', S, 25, {'eligible': True, 'assessment': 'unsatisfied'}, team(2))]
)


class FactsCursor:
    def __init__(self, missing=()):
        self.missing = set(missing)
        self.sql, self._result = [], []

    def execute(self, sql, params=()):
        self.sql.append(sql)
        tag = sql.split('/* q:', 1)[1].split(' */', 1)[0] if '/* q:' in sql else None
        rows = {
            'ready': lambda: [tuple(n not in self.missing for n in ('persona_core_profiles', 'persona_core_aliases')
                                    + ef.SOURCES)],
            'people': lambda: PEOPLE,
            'player_seasons': lambda: [r for r in PLAYER_SEASONS if params[0] <= r[2] <= params[1]],
            'team_seasons': lambda: TEAM_SEASONS,
            'games': lambda: GAMES if params[0] == S else [],
            'awards': lambda: AWARDS if params[0] == S else [],
            'coach_tenures': lambda: COACH_TENURES,
            'coordinator_tenures': lambda: COORD_TENURES,
            'coach_hot_seat': lambda: HC_SEAT,
            'coordinator_hot_seat': lambda: CO_SEAT,
            'staff_moves': lambda: STAFF if params[0] == S - 1 else [],
            'retirements': lambda: RETIRE if params[0] == S - 1 else [],
            'awareness': lambda: [r for r in ATTRS if r[0] in params[1]],
            'events': lambda: [(deepcopy(e),) for e in EVENTS if e['occurred_at']['season'] in params[1:]],
        }
        if tag not in rows:
            raise AssertionError('unexpected SQL: ' + sql)
        self._result = rows[tag]()

    def fetchall(self):
        return list(self._result)

    def fetchone(self):
        return self._result[0] if self._result else None


# ------------------------------------------------------------------------------------------------- facts ---

def test_player_facts_title_mentor_award_and_tenure():
    facts = ef.season_facts(FactsCursor(), U, S)
    a = facts[A.persona_id]
    assert (a['role'], a['birth_key'], a['team_id'], a['age']) == ('player', 'a', 1, 30)
    assert a['won_super_bowl'] is True and a['made_playoffs'] is True
    assert a['source_event_ids']['won_super_bowl'] == ['games:510']
    assert a['seasons_in_league'] == 8 and a['tenure_seasons_on_team'] == 4 and a['position_group'] == 'REC'
    assert a['mentor_retired'] is True
    assert a['source_event_ids']['mentor_retired'] == ['player_retirements:40', 'player_seasons:3']
    assert a['awards'] == ['First Team All-Pro'] and a['source_event_ids']['awards'] == ['mu:award:70']
    assert a['losing_seasons_in_row'] == 0
    assert a['profile_view']['traits'] == ['RING_CHASER']


def test_player_facts_release_signing_role_weeks_and_injury():
    facts = ef.season_facts(FactsCursor(), U, S)
    b = facts[B.persona_id]
    assert b['released_by_team'] is True and b['signed_elsewhere_after_release'] is True
    assert b['source_event_ids']['signed_elsewhere_after_release'] == ['mu:release:2029:5:201',
                                                                       'mu:arrive:2029:7:201:1']
    assert b['unsatisfied_role_weeks'] == 6        # the ineligible week doesn't count
    assert b['injury_weeks_out'] == 9 and b['source_event_ids']['injury_weeks_out'] == ['mu:injury:55']
    assert b['won_super_bowl'] is False           # released from team 2 (which didn't win)
    assert b['losing_seasons_in_row'] == 1
    c = facts[C.persona_id]
    # the release and signing at 2028 ticks 20-22 were before the window; tick 25 of 2029 is after it
    assert c['released_by_team'] is False and c['unsatisfied_role_weeks'] == 0
    assert c['mentor_retired'] is False and c['awards'] == ['Second Team All-Pro']


def test_coach_facts_firing_promotion_award_and_losing_run():
    facts = ef.season_facts(FactsCursor(), U, S)
    x = facts[X.persona_id]
    assert x['coach_fired'] is True and x['source_event_ids']['coach_fired'] == ['coach_hot_seat:2029:2']
    assert x['losing_seasons_in_row'] == 2 and x['tenure_seasons_on_team'] == 3 and x['team_id'] == 2
    assert x['coach_kept_on_hot_seat'] is False and x['coach_promoted_to_hc'] is False
    y = facts[Y.persona_id]
    assert y['coach_promoted_to_hc'] is True and y['source_event_ids']['coach_promoted_to_hc'] == ['mu:staff:900']
    assert y['awards'] == ['COTY'] and y['won_super_bowl'] is True and y['team_id'] == 1
    assert y['seasons_in_league'] == 4 and y['tenure_seasons_on_team'] == 1 and y['coach_fired'] is False
    z = facts[Z.persona_id]
    assert z['coach_kept_on_hot_seat'] is True and z['coach_fired'] is False and z['awards'] == []
    assert z['age'] is None


def test_missing_sources_leave_facts_none_and_rules_quiet():
    from personality import evolution as evo
    facts = ef.season_facts(FactsCursor(missing={'persona_relationship_events', 'games', 'coach_hot_seat',
                                                 'coordinator_hot_seat'}), U, S)
    b, a, x = facts[B.persona_id], facts[A.persona_id], facts[X.persona_id]
    assert b['released_by_team'] is None and b['unsatisfied_role_weeks'] is None and b['injury_weeks_out'] is None
    assert a['won_super_bowl'] is None and x['coach_fired'] is None
    assert 'games' in a['missing'] and 'persona_relationship_events' in b['missing']
    rules = {c['rule_id'] for f in facts.values() for c in evo.rule_candidates(f)}
    assert not rules & {'TITLE_LEGACY', 'FIRED_TEMPER', 'CUT_GRUDGE', 'BENCHED_TEMPER', 'LONG_INJURY_DRIVE'}
    assert 'MVP_CONFIDENCE' in rules            # awards still read
    assert ef.season_facts(FactsCursor(missing={'persona_core_profiles'}), U, S) == {}


def test_facts_are_deterministic():
    assert ef.season_facts(FactsCursor(), U, S) == ef.season_facts(FactsCursor(), U, S)


# --------------------------------------------------------------------------------------- run end to end ---

class FakeStore:
    def __init__(self, profiles):
        self.profiles, self.calls = profiles, []

    def read(self, cur, identity):
        return deepcopy(self.profiles.get(identity.persona_id))

    def change(self, cur, identity, event):
        self.calls.append(event)
        after, _ = core.apply_change(self.profiles[identity.persona_id], event)
        self.profiles[identity.persona_id] = after
        return after


def profiles():
    out = {}
    for _type, pid, key, m, _ns, _ext in PEOPLE:
        out[pid] = initialize(Identity(U, _type, key), 'real', 2026, sliders=m['sliders'], traits=m['traits'])
    return out


@pytest.fixture
def memory_store(monkeypatch):
    saved = {'cands': {}, 'runs': {}}

    def record_candidates(cur, rows):
        new = [r for r in rows if r['candidate_id'] not in saved['cands']]
        saved['cands'].update({r['candidate_id']: deepcopy(r) for r in new})
        return len(new)
    monkeypatch.setattr(er.estore, 'record_candidates', record_candidates)
    monkeypatch.setattr(er.estore, 'record_run',
                        lambda cur, run: saved['runs'].__setitem__((run['universe_id'], run['season']), deepcopy(run)))
    monkeypatch.setattr(er.estore, 'read_run', lambda cur, u, s, v: saved['runs'].get((u, s)))
    monkeypatch.setattr(er.estore, 'read_candidates', lambda cur, u, s: sorted(
        saved['cands'].values(), key=lambda r: (r['persona_type'], r['persona_id'], r['rule_id'])))
    return saved


def test_run_season_end_to_end(memory_store):
    store = FakeStore(profiles())
    report = er.run_season(FactsCursor(), U, S, persona_store=store)
    by = {}
    for c in report['candidates']:
        by.setdefault(c['persona_id'], []).append(c)
    fired = {pid: sorted(c['rule_id'] for c in cs) for pid, cs in by.items()}
    assert fired[A.persona_id] == ['MENTOR_RETIRES', 'MVP_CONFIDENCE', 'TITLE_LEGACY']
    assert fired[B.persona_id] == ['BENCHED_TEMPER', 'CUT_GRUDGE', 'LONG_INJURY_DRIVE']
    assert fired[X.persona_id] == ['FIRED_TEMPER']
    assert fired[Y.persona_id] == ['COY_CONFIDENCE', 'PROMOTED_CONFIDENCE', 'TITLE_LEGACY']
    for pid, cs in by.items():
        assert sum(c['status'] == 'applied' for c in cs) <= 1
        assert all(c['status'] in ('applied', 'drawn_out', 'blocked', 'superseded') for c in cs)
    assert len(store.calls) == report['counts']['applied']
    assert all(e['tick'] == 24 and e['rule_version'] == 'evolution-v1' for e in store.calls)
    assert memory_store['runs'][(U, S)]['status'] == 'APPLIED'
    assert len(memory_store['cands']) == len(report['candidates'])
    again = er.run_season(FactsCursor(), U, S, persona_store=store)
    assert again['status'] == 'noop' and len(store.calls) == report['counts']['applied']


def test_losing_openness_needs_a_kept_job():
    facts = ef.season_facts(FactsCursor(), U, S)
    x = dict(facts[X.persona_id])
    from personality import evolution as evo
    assert 'LOSING_OPENNESS' not in {c['rule_id'] for c in evo.rule_candidates(x)}
    x['coach_fired'] = False
    assert 'LOSING_OPENNESS' in {c['rule_id'] for c in evo.rule_candidates(x)}


def test_external_candidates_reach_the_arbiter(memory_store):
    store = FakeStore(profiles())
    p = store.profiles[C.persona_id]
    ext = {'source': 'DEVELOPMENT', 'persona_type': 'player', 'persona_id': C.persona_id, 'status': 'ready',
           'candidate': {'event_id': 'dev-1', 'source_event_id': 'dev:src:1', 'season': S, 'expected_revision': 0,
                         'rule_version': 'development-v1', 'reason': 'Since a 2029 season of extra film work he '
                         'prepares like a coach.', 'kind': 'slider', 'slider': 'drive', 'step': 1,
                         'supporting_source_ids': ['dev:src:1']}}
    report = er.run_season(FactsCursor(), U, S, external=[ext], persona_store=store)
    (c,) = [c for c in report['candidates'] if c['persona_id'] == C.persona_id and c['source'] == 'DEVELOPMENT']
    assert c['status'] == 'applied' and store.profiles[C.persona_id]['sliders']['drive'] == p['sliders']['drive'] + 1


def test_report_is_deterministic(memory_store):
    a = er.render_report(er.run_season(FactsCursor(), U, S, persona_store=FakeStore(profiles()), dry_run=True))
    b = er.render_report(er.run_season(FactsCursor(), U, S, persona_store=FakeStore(profiles()), dry_run=True))
    assert a == b
    assert a.startswith('# Personality evolution, season 2029')
    assert '## Changes' in a and '## By rule' in a and 'TITLE_LEGACY' in a
    assert er.report_path('mu:abc', 2029).endswith('evolution_mu_abc_2029.md')


# --------------------------------------------------------------------------------------------------- CLI ---

class FakeConn:
    def __init__(self, cur):
        self.cur, self.committed, self.rolled, self.closed = cur, 0, 0, False

    def cursor(self):
        conn = self

        class Ctx:
            def __enter__(self):
                return conn.cur

            def __exit__(self, *a):
                return False
        return Ctx()

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled += 1

    def close(self):
        self.closed = True


class ReadyCursor(FactsCursor):
    def __init__(self, ready=True, **kw):
        super().__init__(**kw)
        self.ready = ready

    def execute(self, sql, params=()):
        if "to_regclass('persona_evolution_runs')" in sql:
            self.sql.append(sql)
            self._result = [(self.ready, self.ready)]
            return
        super().execute(sql, params)


def test_cli_help_and_dsn_guard(monkeypatch, capsys):
    with pytest.raises(SystemExit) as e:
        er.main(['--help'])
    assert e.value.code == 0
    monkeypatch.delenv('DATABASE_URL', raising=False)
    assert er.main(['season', '--season', '2029']) == er.EXIT_INVALID
    for db in ('madden', 'madden_2036', 'postgres'):
        assert er.main(['season', '--season', '2029', '--dsn', f'postgresql://u@h/{db}']) == er.EXIT_INVALID
    assert 'refusing database' in capsys.readouterr().err


def test_cli_not_migrated_and_dry_run(monkeypatch, capsys, memory_store):
    conns = []

    def connect(dsn):
        conns.append(FakeConn(ReadyCursor(ready=len(conns) > 0)))
        return conns[-1]
    monkeypatch.setattr(er, 'connect', connect)
    monkeypatch.setattr(er, '_persona_store', lambda: FakeStore(profiles()))
    dsn = 'postgresql://u@h/mu_scratch_evo'
    assert er.main(['season', '--season', '2029', '--dsn', dsn, '--universe', U]) == er.EXIT_NOT_MIGRATED
    assert conns[0].rolled == 1 and conns[0].committed == 0
    assert er.main(['season', '--season', '2029', '--dsn', dsn, '--universe', U, '--dry-run']) == er.EXIT_OK
    assert conns[1].rolled == 1 and conns[1].committed == 0 and memory_store['runs'] == {}
    assert '"status": "dry_run"' in capsys.readouterr().out
    assert er.main(['season', '--season', '2029', '--dsn', dsn, '--universe', U]) == er.EXIT_OK
    assert conns[2].committed == 1


def test_cli_report_and_revert_conflict(monkeypatch, capsys, tmp_path, memory_store):
    monkeypatch.setattr(er, 'connect', lambda dsn: FakeConn(ReadyCursor()))
    monkeypatch.setattr(er, '_persona_store', lambda: FakeStore(profiles()))
    dsn = 'postgresql://u@h/mu_scratch_evo'
    assert er.main(['report', '--season', '2029', '--dsn', dsn, '--universe', U]) == er.EXIT_INVALID  # no run yet
    assert er.main(['season', '--season', '2029', '--dsn', dsn, '--universe', U]) == er.EXIT_OK
    out = tmp_path / 'r.md'
    assert er.main(['report', '--season', '2029', '--dsn', dsn, '--universe', U, '--out', str(out)]) == er.EXIT_OK
    first = out.read_text()
    assert er.main(['report', '--season', '2029', '--dsn', dsn, '--universe', U, '--out', str(out)]) == er.EXIT_OK
    assert out.read_text() == first and 'run applied' in first
    drawn = next(c for c in memory_store['cands'].values() if c['status'] != 'applied')
    monkeypatch.setattr(er.estore, 'read_candidate', lambda cur, cid: deepcopy(memory_store['cands'].get(cid)))
    capsys.readouterr()
    assert er.main(['revert', '--candidate', drawn['candidate_id'], '--reason', 'no', '--dsn', dsn]) == er.EXIT_CONFLICT
    assert er.main(['revert', '--candidate', 'nope', '--reason', 'no', '--dsn', dsn]) == er.EXIT_INVALID
