"""Relationship producer runner, sources and CLI (RELATIONSHIP_EVENT_PRODUCERS_PLAN.md §2.4-§2.6, §4). Offline.

A fake league answers sources.py's tagged queries (/* q:<name> */) from in-memory tables; the producer tables
reuse the store tests' FakeCursor, and personality.relationship_store / personality.store.resolve_alias are the
store tests' fakes.
"""
from copy import deepcopy
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import types

import pytest

from engines.relationship_producers import PRODUCER_RULES_VERSION
from engines.relationship_producers import run as runner
from engines.relationship_producers import rules as R
from engines.relationship_producers import sources
from engines.relationship_producers import store
from engines.relationship_producers import __main__ as cli

ROOT = pathlib.Path(__file__).resolve().parent.parent
LEAGUE = 77
UNIVERSE = 'universe-1'


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


store_tests = _load('relprod_store_tests_for_run', ROOT / 'tests' / 'test_relationship_producer_store.py')


@pytest.fixture
def rel_store(monkeypatch):
    fake = store_tests.FakeRelationshipStore()
    monkeypatch.setitem(sys.modules, 'personality.relationship_store', fake)
    return fake


@pytest.fixture
def aliases(monkeypatch):
    known = set()
    import personality.store as persona_store
    from personality.core import Identity

    def resolve_alias(cur, universe_id, role, namespace, external_id):
        return Identity(universe_id, role, f'birth-{external_id}') if (role, namespace, external_id) in known else None

    monkeypatch.setattr(persona_store, 'resolve_alias', resolve_alias)
    return known


# ---------------------------------------------------------------- a fake league

def chart(qb, wr=()):
    return {'QB': list(qb), 'WR': list(wr)}


class League:
    """In-memory franchise tables (only what sources.py reads)."""

    def __init__(self):
        self.teams = {1: 'AAA', 2: 'BBB'}
        self.players = {pid: {'origin': 'SAVE', 'pgid': 100 * pid} for pid in range(1, 7)}
        self.players[6]['origin'] = 'HISTORY'
        self.player_seasons = {}            # (pid, season) -> (team, position, overall, released_week)
        self.charts = {}                    # (season, week) -> {team: chart}
        self.injuries = []                  # dicts like sources' injury rows
        self.games = []                     # (season, week, home, away, hs, as, status, type)
        self.transactions = []              # (id, season, week, type, pid, years, value)
        self.lo_runs = []                   # (id, season, week, stage, edits)
        self.pending_moves = []             # (id, kind, payload, written_run)
        self.contracts = []                 # (id, pid, team, season, years_remaining, status)
        self.awards, self.staff, self.hot = [], [], []
        self.hc_tenures, self.coord_tenures = [], []   # (id, name, team, abbr, start, end, role)
        self.transitions = []               # (pid, to_season, status, source)
        self.first_save = 2026
        self.ready = (True, True, True)


class LeagueCursor(store_tests.FakeCursor):
    def __init__(self, league):
        super().__init__()
        self.league = league
        self.connection = types.SimpleNamespace(commit=self._forbidden, rollback=self._forbidden, autocommit=False)

    def execute(self, sql, params=None):
        m = re.match(r'\s*/\* q:(\w+) \*/', sql)
        if not m:
            return super().execute(sql, params)
        self.sql.append(m.group(1))
        self._result = list(getattr(self, 'q_' + m.group(1))(*(params or ())))

    # league and readiness
    def q_league(self):
        return [(LEAGUE,)]

    def q_ready(self):
        return [self.league.ready]

    def q_universe(self, league):
        return [(UNIVERSE,)]

    def q_teams(self):
        return sorted(self.league.teams.items())

    def q_pgids(self, pgids):
        return [(p['pgid'], pid) for pid, p in self.league.players.items() if p['pgid'] in pgids]

    # producer tables
    def q_prev_obs(self, league, season, _s, tick):
        points = sorted({k[1:3] for k in self.obs if k[0] == league and k[1:3] < (season, tick)})
        return [points[-1]] if points else []

    def q_obs(self, league, season, tick):
        return sorted((k[3], v[0]) for k, v in self.obs.items() if k[:3] == (league, season, tick))

    def q_obs_hist(self, league, seasons, last):
        return [(k[1], k[2], k[3], v[0]) for k, v in self.obs.items()
                if k[0] == league and k[1] in seasons and k[2] <= last]

    def _runs(self):
        return [dict(zip(store._RUN_COLUMNS, row)) for row in self.runs.values()]

    def q_prev_run(self, league, version, season, _s, tick):
        runs = sorted((r['season'], r['tick'], r['counts']) for r in self._runs()
                      if r['league_id'] == league and r['rules_version'] == version and r['status'] != 'FAILED'
                      and (r['season'], r['tick']) < (season, tick))
        return runs[-1:]

    def q_save_runs(self, league, version, season, _s, tick):
        return sorted((r['season'], r['tick']) for r in self._runs()
                      if r['league_id'] == league and r['source'] == 'SAVE' and (r['season'], r['tick']) <= (season, tick))

    # weekly facts
    def q_charts(self, seasons, last):
        out = []
        for (s, w), teams in sorted(self.league.charts.items()):
            if s in seasons and w <= last:
                for team, c in sorted(teams.items()):
                    for slot, ids in sorted(c.items()):
                        out.extend((s, w, team, slot, d + 1, pid) for d, pid in enumerate(ids))
        return out

    def q_chart_ticks(self, first, last):
        return sorted(k for k in self.league.charts if k[0] >= first and k[1] <= last)

    def q_injuries(self, seasons, last):
        return [(r['injury_id'], r['season'], r['tick'], r['team_id'], r['player_id'], r['injury_type'],
                 r['weeks_remaining'], r['is_ir']) for r in self.league.injuries
                if r['season'] in seasons and r['tick'] <= last]

    def q_games(self, seasons):
        return [g for g in self.league.games if g[0] in seasons]

    def q_schemes(self, hi, lo):
        return []

    def q_roster(self, season):
        return sorted((pid, v[0], v[3]) for (pid, s), v in self.league.player_seasons.items() if s == season)

    def q_players(self, season, pids):
        return [(pid, self.league.players[pid]['origin'],
                 (self.league.player_seasons.get((pid, season)) or (None, 'QB', 70))[1],
                 (self.league.player_seasons.get((pid, season)) or (None, 'QB', 70))[2]) for pid in sorted(pids)]

    # moves
    def q_txns(self, pids, seasons):
        return [t for t in self.league.transactions if t[4] in pids and t[1] in seasons]

    def q_lo_runs(self, seasons):
        return [r for r in self.league.lo_runs if r[1] in seasons]

    def q_pending_moves(self, runs):
        return [m for m in self.league.pending_moves if m[3] in runs]

    def q_transitions(self, pids, seasons):
        return [t for t in self.league.transitions if t[0] in pids and t[1] in seasons]

    def q_contracts_last(self, pids):
        return [(c[0], c[1], c[2], c[4]) for c in self.league.contracts if c[1] in pids]

    # contracts, awards, coaches
    def q_max_contract(self):
        return [(max([c[0] for c in self.league.contracts], default=0),)]

    def q_contracts_new(self, since):
        return [(c[0], c[1], c[2], c[3], c[4], c[5]) for c in self.league.contracts if c[0] > since and c[5] == 'ACTIVE']

    def q_contracts_prior(self, pids, upto):
        return [(c[0], c[1], c[2], c[5]) for c in self.league.contracts if c[1] in pids and c[0] <= upto]

    def q_contract_years(self, season):
        return [(c[0], c[1], c[2], c[4]) for c in self.league.contracts
                if c[3] == season and c[5] == 'ACTIVE' and c[4] == 1]

    def q_awards(self, season):
        return [a for a in self.league.awards if a[1] == season]

    def q_staff(self, season):
        return [m for m in self.league.staff if m[1] == season]

    def q_hot_seat(self, season, _s):
        return [h for h in self.league.hot if h[1] == season]

    def q_first_save(self):
        return [(self.league.first_save,)]

    # coaching trees (engines/coaching_tree_facts.py)
    def q_regclass(self, table):
        return [(True,)]

    def q_fired(self, first, last):
        return [(h[1], h[2], h[0], h[4]) for h in self.league.hot if h[0] != 'HC' and h[6] and first <= h[1] <= last]

    def _tenures(self, rows, through, first):
        return [t for t in rows if t[4] <= through and (t[5] if t[5] is not None else through) >= first]

    def q_hc(self, through, _t, first):
        return self._tenures(self.league.hc_tenures, through, first)

    def q_coord(self, through, _t, first):
        return self._tenures(self.league.coord_tenures, through, first)


def base_league():
    lg = League()
    for pid, team in {1: 1, 2: 1, 3: 2, 4: 2, 5: None, 6: 1}.items():
        lg.player_seasons[(pid, 2026)] = (team, 'QB', 90 - pid, None)
    lg.charts[(2026, 1)] = {1: chart([1, 2, 6]), 2: chart([3, 4])}
    lg.charts[(2026, 2)] = {1: chart([1]), 2: chart([3, 4, 5])}
    lg.games = [(2026, 1, 1, 2, 21, 14, 'COMPLETED', 'REGULAR_SEASON'),
                (2026, 2, 2, 1, 10, 3, 'COMPLETED', 'REGULAR_SEASON')]
    lg.transactions = [(1, 2026, 2, 'RELEASED', 2, None, None), (2, 2026, 2, 'SIGNED', 5, 3, 9_000_000)]
    lg.contracts = [(10, 1, 1, 2026, 1, 'ACTIVE'), (11, 3, 2, 2026, 3, 'SUPERSEDED')]
    return lg


def roster_at(lg, season):
    return {pid: v[0] if v[3] is None else None for (pid, s), v in lg.player_seasons.items() if s == season}


def move_week2(lg):
    lg.player_seasons[(2, 2026)] = (1, 'QB', 88, 2)          # released in week 2
    lg.player_seasons[(5, 2026)] = (2, 'QB', 85, None)       # signed by team 2


def by_rule(cur, rule, season=None, tick=None):
    events = [e for env_hold in cur.pending.values() for e in [env_hold['envelope']]]
    return [e for e in events if e['payload'].get('rule', '').startswith(rule)
            and (season is None or e['occurred_at'] == {'season': season, 'tick': tick})]


# ---------------------------------------------------------------- run_tick over a fake league

def test_run_tick_week1_offseason_and_next_season(rel_store, aliases):
    lg = base_league()
    cur = LeagueCursor(lg)
    aliases.add(('player', 'db-player', '1'))

    first = runner.run_tick(cur, 2026, 1)
    assert first['status'] == 'APPENDED' and first['source'] == 'SAVE'
    counts = first['counts']
    assert counts['rules']['tenure_window@producers-v2'] == 4     # players 1-4 on teams; 6 is HISTORY, 5 a free agent
    assert counts['rules']['role_window@producers-v2'] == 4
    assert counts['rules']['contract_year@producers-v2'] == 1     # contract 10: years_remaining 1 at tick 1
    assert counts['appended'] >= 1 and counts['held'] == counts['events'] - counts['appended']
    assert counts['watermarks'] == {'player_contracts': 11}
    (call,) = rel_store.calls
    from personality.core import Identity
    assert {e['subject']['persona_id'] for e in call['events']} == {Identity(UNIVERSE, 'player', 'birth-1').persona_id}
    assert all('subject_alias' not in e and 'facet_key' not in e for e in call['events'])
    assert {(k[2], k[3]) for k in cur.obs} == {(1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6)}

    # same inputs: NOOP, nothing new appended
    again = runner.run_tick(cur, 2026, 1)
    assert again['status'] == 'NOOP' and len(rel_store.calls) == 1

    # week 2: player 2 released (transactions), player 5 signed above the minimum
    move_week2(lg)
    second = runner.run_tick(cur, 2026, 2)
    held = {e['source_event_id']: e for e in by_rule(cur, '')}
    assert held['mu:release:2026:2:2']['payload']['treatment_kind'] == 'dismissal'
    assert held['mu:release:2026:2:2']['attribution']['evidence_refs'] == ['mu-producer:transactions:1']
    assert held['mu:depart:2026:2:2:1']['payload']['state'] == 'negative'
    assert held['mu:arrive:2026:2:5:2']['payload']['state'] == 'positive'
    assert second['counts']['rules']['tenure_window@producers-v2'] == 4

    # an offseason stop: no role windows, tenure only; the season's awards are dated (2026, 23)
    lg.awards = [(50, 2026, 'MVP', 1, 1, None)]
    off = runner.run_tick(cur, 2026, 24)
    assert 'role_window@producers-v2' not in off['counts']['rules']
    assert off['counts']['rules']['award@producers-v2'] == 1

    # 2027 week 1: player 3 traded through a WRITTEN league-office run at PRESEASON (tick 32)
    for (pid, s), v in list(lg.player_seasons.items()):
        lg.player_seasons[(pid, 2027)] = v[:3] + (None,) if v[3] is None else (None,) + v[1:3] + (None,)
    lg.player_seasons[(3, 2027)] = (1, 'QB', 87, None)
    lg.lo_runs = [(7, 2026, None, 'PRESEASON', {'trade': [{'from_team_index': 1, 'to_team_index': 0,
                                                            'pgids': [300], 'picks': []}]})]
    lg.charts[(2027, 1)] = {1: chart([1, 3]), 2: chart([4, 5])}
    nxt = runner.run_tick(cur, 2027, 1)
    held = {e['source_event_id']: e for e in by_rule(cur, '')}
    assert held['mu:depart:2027:1:3:2']['payload']['rule'] == 'departure_trade@producers-v2'
    assert held['mu:arrive:2027:1:3:1']['attribution']['evidence_refs'] == ['mu-producer:league_office_runs:7']
    assert 'mu:release:2027:1:3' not in held                       # a trade is never a treatment
    assert nxt['status'] in ('APPENDED', 'HELD')


def test_rerun_with_changed_inputs_appends_corrections(rel_store, aliases):
    lg = base_league()
    cur = LeagueCursor(lg)
    for pid in '1234':
        aliases.add(('player', 'db-player', pid))
    runner.run_tick(cur, 2026, 1)
    lg.charts[(2026, 1)][1] = chart([2, 1])                       # the week's chart is re-ingested
    rerun = runner.run_tick(cur, 2026, 1)
    assert rerun['counts']['corrections'] == 2                     # players 1 and 2 swapped tiers
    kinds = [e['kind'] for e in rel_store.calls[-1]['events']]
    assert kinds.count('correction') == 2
    fix = next(e for e in rel_store.calls[-1]['events'] if e['kind'] == 'correction')
    assert fix['supersedes_source_event_id'].startswith('mu:role:2026:1:')
    assert fix['source_refs'] == []


def test_contract_rejections_are_not_counted_as_appended(rel_store, aliases):
    lg = base_league()
    cur = LeagueCursor(lg)
    aliases.update({('player', 'db-player', '1'), ('player', 'db-player', '2')})
    rel_store.reject = {'mu:tenure:2026:1:2': 'unknown_identity: player:x'}
    run = runner.run_tick(cur, 2026, 1)
    assert run['counts']['rejected'] == 1
    assert run['counts']['appended'] == len(rel_store.calls[0]['events']) - 1
    # the run's own flush retries the held rejection; the store rejects it again, so it stays PENDING
    assert [e['source_event_id'] for e in rel_store.calls[1]['events']] == ['mu:tenure:2026:1:2']
    assert run['counts']['flushed'] == 0
    (row,) = [v for k, v in cur.pending.items() if k[1] == 'mu:tenure:2026:1:2']
    assert row['status'] == 'PENDING' and row['reason'].startswith('rejected: unknown_identity')


def test_recognition_from_new_contract_after_the_watermark(rel_store, aliases):
    lg = base_league()
    cur = LeagueCursor(lg)
    runner.run_tick(cur, 2026, 1)
    lg.contracts.append((12, 3, 2, 2026, 4, 'ACTIVE'))           # re-signed: replaces contract 11 on team 2
    lg.contracts.append((13, 4, 2, 2026, 2, 'ACTIVE'))           # a first contract: no recognition
    run = runner.run_tick(cur, 2026, 2)
    assert run['counts']['rules']['resign_recognition@producers-v2'] == 1
    (rec,) = by_rule(cur, 'resign_recognition')
    assert rec['source_refs'] == [{'namespace': 'mu-producer', 'key': 'player_contracts:12'}]
    assert run['counts']['watermarks'] == {'player_contracts': 13}


def test_store_not_migrated_is_refused():
    lg = base_league()
    lg.ready = (True, False, False)
    with pytest.raises(runner.NotMigratedError):
        runner.run_tick(LeagueCursor(lg), 2026, 1)


def test_record_failure_never_overwrites_a_good_run(rel_store, aliases):
    lg = base_league()
    cur = LeagueCursor(lg)
    runner.run_tick(cur, 2026, 1)
    assert runner.record_failure(cur, LEAGUE, 2026, 1, 'SAVE', 'boom') is False
    assert runner.record_failure(cur, LEAGUE, 2026, 3, 'SAVE', 'boom') is True
    assert store.read_run(cur, LEAGUE, 2026, 3, PRODUCER_RULES_VERSION)['status'] == 'FAILED'


# ---------------------------------------------------------------- catch-up

def test_catch_up_matches_save_except_coverage_and_confidence(rel_store, aliases):
    save_lg, catch_lg = base_league(), base_league()
    save_cur, catch_cur = LeagueCursor(save_lg), LeagueCursor(catch_lg)
    membership = sources.chart_membership({str(k): v for k, v in save_lg.charts[(2026, 1)].items()})
    runner.run_tick(save_cur, 2026, 1, roster={int(p): t for p, t in membership.items()})
    report = runner.catch_up(catch_cur, through=(2026, 1))
    assert report['ticks'] == 1 and report['statuses'] == {'HELD': 1}

    def windows(cur):
        out = {}
        for e in by_rule(cur, 'tenure_window') + by_rule(cur, 'role_window'):
            e = deepcopy(e)
            e.pop('event_id', None)
            e['confidence'] = 'X'
            e['payload'].pop('coverage', None)
            out[(e['source_event_id'], e['facet_key'])] = e
        return out

    save_w, catch_w = windows(save_cur), windows(catch_cur)
    assert save_w == catch_w and save_w
    assert {e['envelope']['confidence'] for e in catch_cur.pending.values()} == {'medium'}
    assert {e['envelope']['payload']['coverage'] for e in catch_cur.pending.values()
            if e['envelope']['kind'] == 'tenure_window'} == {'partial'}


def test_catch_up_refuses_ticks_with_save_runs_and_dry_run_writes_nothing(rel_store, aliases):
    lg = base_league()
    cur = LeagueCursor(lg)
    dry = runner.catch_up(cur, through=(2026, 2), dry_run=True)
    assert dry['dry_run'] and dry['ticks'] == 2 and dry['counts']['events'] > 0
    assert cur.runs == {} and cur.obs == {} and cur.pending == {}
    runner.run_tick(cur, 2026, 2)
    with pytest.raises(runner.CatchUpRefused):
        runner.catch_up(cur, through=(2026, 2))
    with pytest.raises(runner.CatchUpRefused):
        runner.run_tick(cur, 2026, 2, source='CATCHUP')


# ---------------------------------------------------------------- pure builder

def test_history_players_and_free_agents_produce_no_windows():
    lg = base_league()
    inputs = sources.read_tick(LeagueCursor(lg), LEAGUE, 2026, 1)
    envs = runner.build_events(inputs)
    subjects = {e['subject_alias']['external_id'] for e in envs if e['kind'] in ('tenure_window', 'role_window')}
    assert subjects == {'1', '2', '3', '4'}
    assert all(e['facet_key'] == R.facet_key(e) for e in envs)


def test_demotion_only_after_established_role_while_healthy():
    lg = base_league()
    for w in range(1, 6):
        lg.charts[(2026, w)] = {1: chart([1, 2]), 2: chart([3, 4])}
    lg.charts[(2026, 6)] = {1: chart([2, 1]), 2: chart([3, 4])}
    env = next(e for e in runner.build_events(sources.read_tick(LeagueCursor(lg), LEAGUE, 2026, 6))
               if e['kind'] == 'role_window' and e['subject_alias']['external_id'] == '1')
    assert (env['payload']['agreed_role'], env['payload']['observed_role'], env['payload']['assessment']) == \
        ('STARTER', 'ROTATION', 'unsatisfied')
    lg.injuries = [{'injury_id': 5, 'player_id': 1, 'team_id': 1, 'season': 2026, 'tick': 6, 'injury_type': 'Knee',
                    'weeks_remaining': 2, 'is_ir': False}]
    envs = runner.build_events(sources.read_tick(LeagueCursor(lg), LEAGUE, 2026, 6))
    env = next(e for e in envs if e['kind'] == 'role_window' and e['subject_alias']['external_id'] == '1')
    assert env['payload']['assessment'] == 'unknown'                 # hurt: never a demotion
    assert any(e['source_event_id'] == 'mu:injury:5' for e in envs)


def test_streaks_season_outcome_and_coach_facts():
    lg = base_league()
    lg.charts = {(2026, w): {1: chart([1]), 2: chart([3])} for w in (1, 2, 3, 23)}
    lg.games = [(2026, w, 1, 2, 20, 10, 'COMPLETED', 'REGULAR_SEASON') for w in (1, 2, 3)]
    lg.games.append((2026, 23, 1, 2, 30, 7, 'COMPLETED', 'SUPER_BOWL'))
    lg.hot = [('HC', 2026, 2, 9, None, 0.8, False, 'BLACK_MONDAY'), ('OC', 2026, 2, None, 4, 0.9, True, 'BLACK_MONDAY')]
    lg.staff = [(70, 2026, 2, 'OC', 'LEFT', None, None, 4), (71, 2026, 2, 'OC', 'HIRED', 'PROMOTED', None, 5)]
    cur = LeagueCursor(lg)
    week3 = runner.build_events(sources.read_tick(cur, LEAGUE, 2026, 3))
    assert {e['source_event_id'] for e in week3 if e['payload']['rule'].startswith('win_streak')} == \
        {'mu:result:2026:1:win-streak:3'}
    sb = runner.build_events(dict(sources.read_tick(cur, LEAGUE, 2026, 24), window=[[2026, 23], [2026, 24]]))
    outcome = {e['source_event_id'] for e in sb if e['payload']['rule'].startswith('season_outcome')}
    assert outcome == {'mu:result:2026:1:won-super-bowl:23', 'mu:result:2026:2:lost-round-4:23'}
    coach = {e['source_event_id']: e for e in sb if e['subject_alias']['role'] == 'coach'}
    assert coach['mu:staff:70']['payload']['treatment_kind'] == 'dismissal'
    assert coach['mu:staff:70']['occurred_at'] == {'season': 2026, 'tick': 19}
    assert coach['mu:staff:71']['subject_alias'] == {'role': 'coach', 'namespace': 'db-coordinator',
                                                     'external_id': '5'}
    assert coach['mu:hotseat:2026:2:HC']['payload']['situation_kind'] == 'job_pressure'


def tree_league():
    lg = base_league()
    lg.hc_tenures = [(9, 'Ann Head', 2, 'BBB', 2020, None, 'HC')]
    lg.coord_tenures = [(4, 'Bo Coord', 2, 'BBB', 2025, None, 'OC'), (5, 'Cy One', 2, 'BBB', 2026, 2026, 'DC')]
    return lg


def test_coaching_tree_events_from_tick_24_with_historical_time():
    lg = tree_league()
    cur = LeagueCursor(lg)
    assert not [e for e in runner.build_events(sources.read_tick(cur, LEAGUE, 2026, 24))
                if e['payload']['rule'].startswith('coaching_tree')]          # 2025 doesn't count: 1 season in 2026
    week5 = sources.read_tick(cur, LEAGUE, 2027, 5)
    assert week5['tree_staff'] == [] and not [e for e in runner.build_events(week5)
                                              if e['payload']['rule'].startswith('coaching_tree')]
    tick = sources.read_tick(cur, LEAGUE, 2027, 24)
    assert {(r['season'], r['role']) for r in tick['tree_staff']} >= {(2026, 'HC'), (2027, 'OC'), (2026, 'DC')}
    tree = [e for e in runner.build_events(tick) if e['payload']['rule'] == 'coaching_tree@producers-v2']
    assert len(tree) == 2 and {e['source_event_id'] for e in tree} == {'mu:tree:9:4'}
    mentee = next(e for e in tree if e['payload']['direction'] == 'mentee')
    assert mentee['subject_alias'] == {'role': 'coach', 'namespace': 'db-coordinator', 'external_id': '4'}
    assert mentee['target_alias'] == {'role': 'coach', 'namespace': 'db-coach', 'external_id': '9'}
    assert mentee['occurred_at'] == {'season': 2027, 'tick': 24}
    later = [e for e in runner.build_events(sources.read_tick(cur, LEAGUE, 2029, 24))
             if e['payload']['rule'].startswith('coaching_tree')]
    assert later == tree                         # frozen at the 2027 crossing: identical facets, deduped by the store
    assert later[0]['payload']['seasons'] == 2


def test_coaching_tree_skips_reviewed_pairs(monkeypatch):
    monkeypatch.setattr(sources, 'tree_reviewed_pairs', lambda directory=None: [['p:ann head', 'p:bo coord']])
    tick = sources.read_tick(LeagueCursor(tree_league()), LEAGUE, 2027, 24)
    assert not [e for e in runner.build_events(tick) if e['payload']['rule'].startswith('coaching_tree')]


def test_window_and_build_are_deterministic():
    assert sources.window(None, 2026, 5) == [[2026, 5]]
    assert sources.window((2026, 22), 2027, 1) == [[2026, t] for t in range(23, 33)] + [[2027, 1]]
    lg = base_league()
    a = runner.build_events(sources.read_tick(LeagueCursor(lg), LEAGUE, 2026, 2))
    b = runner.build_events(sources.read_tick(LeagueCursor(lg), LEAGUE, 2026, 2))
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


# ---------------------------------------------------------------- CLI

def _fixture(tmp_path):
    lg = base_league()
    cur = LeagueCursor(lg)
    ticks = [sources.read_tick(cur, LEAGUE, 2026, 1)]
    path = tmp_path / 'fixture.json'
    path.write_text(json.dumps({'ticks': ticks}))
    return path


def test_preview_is_byte_identical_and_offline(tmp_path):
    fixture = _fixture(tmp_path)
    outs = []
    for name in ('a.json', 'b.json'):
        out = tmp_path / name
        code = subprocess.run([sys.executable, '-c',
                               'import sys, engines.relationship_producers.__main__ as m; '
                               'rc = m.main(sys.argv[1:]); '
                               'assert "psycopg" not in sys.modules, "preview imported psycopg"; sys.exit(rc)',
                               'preview', '--fixture', str(fixture), '--out', str(out)],
                              cwd=ROOT, capture_output=True, text=True)
        assert code.returncode == 0, code.stderr
        outs.append(out.read_bytes())
    assert outs[0] == outs[1]
    assert json.loads(outs[0])['counts']['tenure_window@producers-v2'] == 4


def test_cli_exit_codes(tmp_path, monkeypatch, rel_store, aliases):
    bad = tmp_path / 'bad.json'
    bad.write_text('{"ticks": [{"tick": 1}]}')
    assert cli.main(['preview', '--fixture', str(bad), '--out', str(tmp_path / 'o.json')]) == 2
    assert cli.main(['preview', '--fixture', str(tmp_path / 'missing.json'), '--out', str(tmp_path / 'o.json')]) == 2
    monkeypatch.delenv('DATABASE_URL', raising=False)
    assert cli.main(['flush']) == 2
    for db in ('madden', 'madden_2036', 'postgres'):
        assert cli.main(['run', '--season', '2026', '--tick', '1', '--dsn', f'postgresql://u@h/{db}']) == 2

    lg = base_league()

    class Conn:
        def __init__(self):
            self.cur = LeagueCursor(lg)
            self.committed = self.rolled_back = False

        def cursor(self):
            conn = self

            class Ctx:
                def __enter__(self):
                    return conn.cur

                def __exit__(self, *a):
                    return False
            return Ctx()

        def commit(self):
            self.committed = True

        def rollback(self):
            self.rolled_back = True

        def close(self):
            pass

    conns = []
    monkeypatch.setattr(cli, 'connect', lambda dsn: conns.append(Conn()) or conns[-1])
    dsn = 'postgresql://u@h/madden_test'
    assert cli.main(['run', '--season', '2026', '--tick', '1', '--dsn', dsn]) == 0 and conns[-1].committed
    assert cli.main(['flush', '--dsn', dsn]) == 0
    lg.ready = (False, True, True)
    assert cli.main(['flush', '--dsn', dsn]) == 3 and conns[-1].rolled_back
    lg.ready = (True, True, True)
    conns[-1].cur.runs = {}
    monkeypatch.setattr(cli, 'connect', lambda dsn: conns[0])  # the connection that holds a SAVE run
    assert cli.main(['catch-up', '--through', '2026:1', '--dsn', dsn]) == 4


def test_cli_help_exits_zero():
    out = subprocess.run([sys.executable, '-m', 'engines.relationship_producers', '--help'], cwd=ROOT,
                         capture_output=True, text=True)
    assert out.returncode == 0 and 'catch-up' in out.stdout
