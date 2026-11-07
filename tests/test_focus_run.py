"""Focus facts, seeding, run_season, report, set, revert and CLI over a fake league
(INFERRED_SLIDERS_AND_FOCUS_PLAN.md §2.6, §4). Offline."""
from copy import deepcopy

import pytest

from personality import focus as fo
from personality import focus_facts as ff
from personality import focus_run as fr
from personality import focus_store as fs
from tests.test_focus_store import FakeCursor as StoreCursor

U = 'mu:test'
S = 2030
LAMAR = {'kind': 'player', 'name': 'Lamar Jackson', 'team_2026': 'BAL'}


class League(StoreCursor):
    """The focus store plus the league tables focus_facts and seeding read (routed by their /* q:tag */)."""

    def __init__(self):
        super().__init__()
        self.missing = set()
        self.holders = [('coach', 'c1', 'curated-coach:BAL:HC:2026', {'traits': ['RING_CHASER']}, 'db-coach', '10'),
                        ('owner', 'o1', 'curated-owner:BAL:2026', {'traits': ['PATIENT_BUILDER']}, None, None)]
        self.teams = [(1, 'BAL', 'Ravens'), (2, 'NYJ', 'Jets')]
        self.coach_tenures = [(10, 1, 2024, None)]
        self.coordinator_tenures = []
        self.coaches = [(10, 'John Harbaugh')]
        self.directions = [(1, 'CONTEND'), (2, 'REBUILD')]
        self.games = [('SUPER_BOWL', 1, 2, 30, 20)]
        self.hot = []
        self.roster = [(100, 1, 'QB', 29, 'Lamar Jackson'), (101, 1, 'WR', 25, 'Zay Flowers'),
                       (102, 1, 'S', 25, 'Kyle Hamilton'), (103, 2, 'QB', 24, 'Tyler Shough')]
        self.ovr = {100: 95, 101: 88, 102: 93, 103: 80}
        self.retired = []
        self.gms = [(5, 1, 'Eric DeCosta', 2019, None)]
        self.players_2026 = {('BAL', 'Lamar Jackson'): 100}
        self.aliases = {('db-coach', '10'): 'c1'}
        self.persona_types = {'c1': 'coach', 'o1': 'owner'}

    def execute(self, sql, params=None):
        text = ' '.join(sql.split())
        if not text.startswith('/* q:'):
            return super().execute(sql, params)
        tag = text[5:].split(' ', 1)[0]
        self.sql.append(text)
        p = list(params or [])
        self._result, self.rowcount = [], -1
        if tag == 'ready':
            names = ('persona_core_profiles', 'persona_core_aliases') + ff.SOURCES
            self._result = [tuple(n not in self.missing for n in names)]
        elif tag == 'holders':
            self._result = list(self.holders)
        elif tag == 'teams':
            self._result = list(self.teams)
        elif tag == 'coach_tenures':
            self._result = [r for r in self.coach_tenures if r[2] <= p[0]]
        elif tag == 'coordinator_tenures':
            self._result = [r for r in self.coordinator_tenures if r[3] <= p[0]]
        elif tag == 'coaches':
            self._result = list(self.coaches)
        elif tag == 'coordinators':
            self._result = []
        elif tag == 'directions':
            self._result = list(self.directions)
        elif tag == 'games':
            self._result = list(self.games)
        elif tag == 'coach_hot_seat':
            self._result = list(self.hot)
        elif tag == 'coordinator_hot_seat':
            self._result = []
        elif tag == 'roster':
            self._result = list(self.roster)
        elif tag == 'ovr':
            self._result = [(pid, self.ovr[pid]) for pid in p[1] if pid in self.ovr]
        elif tag == 'retired':
            self._result = [(pid,) for pid in self.retired]
        elif tag == 'gms':
            self._result = list(self.gms)
        elif tag == 'coach_id':
            self._result = [(cid,) for cid, name in self.coaches if name == p[0]]
        elif tag == 'alias':
            pid = self.aliases.get((p[1], p[2]))
            self._result = [(pid,)] if pid else []
        elif tag == 'owner_person':
            self._result = [(h[1],) for h in self.holders if h[0] == 'owner' and h[2] == p[1]]
        elif tag == 'subject_player':
            pid = self.players_2026.get((p[1], p[2]))
            self._result = [(pid,)] if pid else []
        elif tag in ('subject_gm', 'coordinator_id'):
            self._result = []
        elif tag == 'active':
            rows = sorted((r for r in self.rows.values() if r['universe_id'] == p[0] and r['status'] == 'active'),
                          key=lambda r: (r['persona_id'], r['slot']))
            self._result = [tuple(r[c] for c in fs.FOCUS_COLUMNS) for r in rows]
        elif tag == 'season_rows':
            rows = sorted((r for r in self.rows.values() if r['universe_id'] == p[0] and r['valid_from_season'] == p[1]
                           and r['source'] == 'template'), key=lambda r: (r['persona_id'], r['slot'], r['seq']))
            self._result = [tuple(r[c] for c in fs.FOCUS_COLUMNS) for r in rows]
        elif tag == 'persona_type':
            t = self.persona_types.get(p[1])
            self._result = [(t,)] if t else []
        elif tag.startswith('name_'):
            self._result = [('Some Name',)]
        else:
            raise AssertionError('unexpected tagged SQL: ' + text)


def coach_doc(status='approved', focus_want=None, focus_fear=None):
    return {'schema_version': 'coach-profile-v2', 'key': 'coach:BAL:HC', 'name': 'John Harbaugh', 'role': 'HC',
            'team': 'BAL', 'seed_ref': {'table': 'COACHES_DATA', 'team': 'BAL', 'role': 'HC', 'name': 'John Harbaugh'},
            'motivation': {'want': 'to keep winning', 'fear': 'decline'},
            'focus': {'want': focus_want if focus_want is not None else
                      {'text': 'a second title with Lamar Jackson', 'subjects': [LAMAR], 'until_season': None},
                      'fear': focus_fear},
            'review': {'status': status, 'reviewer': 'JR', 'reviewed_at': '2026-11-07'}}


def owner_doc():
    return {'schema_version': 'owner-profile-v2', 'key': 'owner:BAL', 'name': 'Steve Bisciotti', 'role': 'OWNER',
            'team': 'BAL', 'seed_ref': {'table': 'OWNERS_DATA', 'team': 'BAL', 'name': 'Steve Bisciotti'},
            'focus': {'want': None, 'fear': None},
            'review': {'status': 'approved', 'reviewer': 'JR', 'reviewed_at': '2026-11-07'}}


DOCS = [coach_doc(), owner_doc()]
SUBJ = [{'kind': 'player', 'name': 'Lamar Jackson', 'binding': {'namespace': 'db-player', 'external_id': '100'}}]


# ----------------------------------------------------------------------------------------------- facts ---

def test_facts_holder_team_and_people():
    f = ff.facts(League(), U, S, bindings={'c1': SUBJ})
    c, o = f['c1'], f['o1']
    assert (c['seat'], c['team'], c['team_name'], c['holder_name']) == ('HC', 'BAL', 'Ravens', 'John Harbaugh')
    assert (c['direction'], c['made_playoffs'], c['won_super_bowl'], c['hot_seat']) == ('CONTEND', True, True, False)
    assert c['people']['qb1']['name'] == 'Lamar Jackson'
    assert c['people']['qb1']['binding'] == {'namespace': 'db-player', 'external_id': '100'}
    assert c['people']['young_star']['name'] == 'Kyle Hamilton'
    assert c['people']['top_defender']['name'] == 'Kyle Hamilton'
    assert c['people']['top_weapon']['name'] == 'Zay Flowers'
    assert c['people']['gm']['name'] == 'Eric DeCosta' and c['people']['head_coach'] is None
    assert (o['seat'], o['team']) == ('OWNER', 'BAL') and o['people']['head_coach']['name'] == 'John Harbaugh'
    assert c['subject_status'] == {'db-player:100': {'status': 'with_team', 'team': 1}}
    assert c['traits'] == ['RING_CHASER'] and c['missing'] == {}


@pytest.mark.parametrize('change,status', [
    (lambda lg: lg.retired.append(100), 'retired'),
    (lambda lg: lg.roster.__setitem__(0, (100, 2, 'QB', 29, 'Lamar Jackson')), 'elsewhere'),
    (lambda lg: lg.roster.pop(0), 'gone'),
])
def test_subject_status(change, status):
    lg = League()
    change(lg)
    assert ff.facts(lg, U, S, bindings={'c1': SUBJ})['c1']['subject_status']['db-player:100']['status'] == status


def test_coach_subject_status_and_moved_holder():
    lg = League()
    lg.coach_tenures = [(10, 1, 2024, 2030), (10, 2, 2031, None)]
    sub = [{'kind': 'coach', 'name': 'John Harbaugh', 'seat': {'team': 'BAL', 'role': 'HC'},
            'binding': {'namespace': 'db-coach', 'external_id': '10'}}]
    f = ff.facts(lg, U, S, bindings={'o1': sub})
    assert (f['c1']['team'], f['c1']['direction']) == ('NYJ', 'REBUILD')
    assert f['o1']['subject_status']['db-coach:10']['status'] == 'not_in_seat'
    lg.coach_tenures = [(10, 1, 2024, 2030)]
    f = ff.facts(lg, U, S)
    assert f['c1']['team'] is None and f['c1']['seat'] is None


def test_missing_tables_leave_facts_none():
    lg = League()
    lg.missing = {'gm_team_seasons', 'games', 'player_seasons'}
    c = ff.facts(lg, U, S, bindings={'c1': SUBJ})['c1']
    assert c['direction'] is None and c['made_playoffs'] is None and c['won_super_bowl'] is None
    assert c['people']['qb1'] is None
    assert c['subject_status']['db-player:100']['status'] == 'unknown'
    assert set(c['missing']) == {'gm_team_seasons', 'games', 'player_seasons'}
    lg.missing = {'persona_core_profiles'}
    assert ff.facts(lg, U, S) == {}


# ------------------------------------------------------------------------------------------------ seed ---

def test_seed_resolves_skips_drafts_and_is_idempotent():
    lg = League()
    out = fr.seed(lg, U, S, 24, DOCS + [dict(coach_doc('draft'), key='coach:NYJ:HC')])
    assert out['inserted'] == 1 and len(out['rows']) == 1
    row = lg.rows[fs.focus_id(U, 'c1', 'want', 0)]
    assert row['source'] == 'profile' and row['anchor'] == {'team': 'BAL', 'seat': 'HC'}
    assert row['subjects'][0]['binding'] == {'namespace': 'db-player', 'external_id': '100'}
    assert {s['reason'] for s in out['skipped']} == {'not a reviewed v2 profile'}
    assert out['people'] == {'c1': {'team': 'BAL', 'seat': 'HC', 'persona_type': 'coach'},
                             'o1': {'team': 'BAL', 'seat': 'OWNER', 'persona_type': 'owner'}}
    assert fr.seed(lg, U, S, 24, DOCS)['inserted'] == 0


def test_seed_unresolved_subject_and_v1_skipped():
    lg = League()
    lg.players_2026 = {}
    out = fr.seed(lg, U, S, 24, [coach_doc()])
    assert out['unresolved'] == [{'persona_id': 'c1', 'slot': 'want', 'subject': 'Lamar Jackson'}]
    assert lg.rows[fs.focus_id(U, 'c1', 'want', 0)]['subjects'][0]['binding'] is None
    v1 = dict(coach_doc(), schema_version='coach-profile-v1')
    assert fr.seed(League(), U, S, 24, [v1])['inserted'] == 0


# ----------------------------------------------------------------------------------------- run_season ---

def run(lg, **kw):
    return fr.run_season(lg, U, S, docs=DOCS, **kw)


def test_keep_when_nothing_is_stale_then_noop():
    lg = League()
    rep = run(lg)
    assert rep['status'] == 'applied' and rep['counts']['replaced'] == 0 and rep['counts']['kept'] == 1
    assert fs.read_run(lg, U, S, fo.FOCUS_RULES_VERSION)['status'] == 'APPLIED'
    assert run(lg)['status'] == 'noop'


def test_replace_when_the_subject_retires():
    lg = League()
    run(lg)
    lg.retired.append(100)
    lg.roster.pop(0)
    rep = run(lg)
    assert rep['counts']['replaced'] == 1 and rep['counts']['by_cause'] == {'SUBJECT_RETIRED': 1}
    change = rep['changes'][0]
    assert change['old'] == 'a second title with Lamar Jackson'
    assert change['note'].startswith('2030: with Lamar Jackson retired, he now wants ')
    now = fs.read_focus(lg, U, 'c1')['want']
    assert now['seq'] == 1 and now['source'] == 'template' and now['supersedes'] == fs.focus_id(U, 'c1', 'want', 0)
    assert now['stale_causes'] == [{'cause': 'SUBJECT_RETIRED', 'subject': 'Lamar Jackson'}]
    assert lg.rows[fs.focus_id(U, 'c1', 'want', 0)]['status'] == 'superseded'
    assert 'Lamar Jackson' not in now['text']


def test_jr_row_is_reported_not_replaced():
    lg = League()
    run(lg)
    fr.set_focus(lg, U, 'c1', 'want', text='one more ring with Lamar Jackson', subjects=deepcopy(SUBJ),
                 reason='mine', season=S, tick=2)
    lg.retired.append(100)
    rep = run(lg)
    assert rep['counts']['replaced'] == 0 and rep['jr_stale'][0]['slot'] == 'want'
    assert fs.read_focus(lg, U, 'c1')['want']['source'] == 'jr'


def test_moved_holder_drafts_an_empty_slot_too():
    lg = League()
    run(lg)
    lg.coach_tenures = [(10, 1, 2024, 2030), (10, 2, 2031, None)]
    rep = fr.run_season(lg, U, S, docs=DOCS)
    slots = {(c['persona_id'], c['slot']) for c in rep['changes']}
    assert slots == {('c1', 'want'), ('c1', 'fear')}
    fear = fs.read_focus(lg, U, 'c1')['fear']
    assert fear['seq'] == 0 and fear['supersedes'] is None and fear['anchor'] == {'team': 'NYJ', 'seat': 'HC'}
    assert fear['stale_causes'] == [{'cause': 'HOLDER_MOVED'}]


def test_dry_run_writes_nothing():
    lg = League()
    lg.retired.append(100)
    rep = run(lg, dry_run=True)
    assert rep['status'] == 'dry_run' and rep['counts']['would_replace'] == 1
    assert lg.rows == {} and lg.runs == {}


def test_report_is_deterministic():
    lg = League()
    run(lg)
    lg.retired.append(100)
    rep = run(lg)
    text = fr.render_report(rep)
    assert text == fr.render_report(deepcopy(rep))
    assert '# Focus refresh, season 2030' in text and 'SUBJECT_RETIRED (Lamar Jackson)' in text
    assert fr.report_path(U, S).endswith('focus_mu_test_2030.md')
    stored = fr.render_report(fr.stored_report(lg, U, S))
    assert 'a second title with Lamar Jackson' in stored


def test_record_failure_keeps_applied():
    lg = League()
    fr.record_failure(lg, U, S, RuntimeError('boom'))
    assert fs.read_run(lg, U, S, fo.FOCUS_RULES_VERSION)['status'] == 'FAILED'
    run(lg)
    fr.record_failure(lg, U, S, RuntimeError('boom'))
    assert fs.read_run(lg, U, S, fo.FOCUS_RULES_VERSION)['status'] == 'APPLIED'


# -------------------------------------------------------------------------------------- set and revert ---

def test_set_and_revert():
    lg = League()
    run(lg)
    row = fr.set_focus(lg, U, 'c1', 'want', text='a title before he turns sixty', subjects=[], reason='JR wants it',
                       season=S, tick=3)
    assert (row['source'], row['actor'], row['seq']) == ('jr', 'JR', 1)
    back = fr.revert(lg, row['focus_id'], 'changed my mind')
    assert back['text'] == 'a second title with Lamar Jackson' and back['source'] == 'jr' and back['seq'] == 2
    assert back['reason'] == f"revert:{row['focus_id']}: changed my mind"
    assert lg.rows[row['focus_id']]['status'] == 'reverted'
    with pytest.raises(fr.FocusRunError) as e:
        fr.revert(lg, row['focus_id'], 'again')
    assert e.value.code == fr.EXIT_CONFLICT
    with pytest.raises(fr.FocusRunError) as e:
        fr.set_focus(lg, U, 'c1', 'want', text=' '.join(['w'] * 15), subjects=[], reason='x', season=S, tick=3)
    assert e.value.code == fr.EXIT_INVALID
    with pytest.raises(fr.FocusRunError) as e:
        fr.set_focus(lg, U, 'nobody', 'want', text='x', subjects=[], reason='x', season=S, tick=3)
    assert e.value.code == fr.EXIT_NOT_FOUND


def test_revert_of_a_profile_row_is_refused():
    lg = League()
    run(lg)
    with pytest.raises(fr.FocusRunError) as e:
        fr.revert(lg, fs.focus_id(U, 'c1', 'want', 0), 'x')
    assert e.value.code == fr.EXIT_CONFLICT
    with pytest.raises(fr.FocusRunError) as e:
        fr.revert(lg, 'focus:nope', 'x')
    assert e.value.code == fr.EXIT_NOT_FOUND


# ------------------------------------------------------------------------------------------------- CLI ---

class Conn:
    def __init__(self, cur):
        self.cur, self.commits, self.rollbacks = cur, 0, 0

    def cursor(self):
        cur = self.cur

        class Ctx:
            def __enter__(self):
                return cur

            def __exit__(self, *a):
                return False
        return Ctx()

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


def test_cli_help_lists_five_subcommands(capsys):
    with pytest.raises(SystemExit) as e:
        fr.main(['--help'])
    assert e.value.code == 0
    out = capsys.readouterr().out
    for cmd in ('season', 'report', 'show', 'set', 'revert'):
        assert cmd in out


@pytest.mark.parametrize('dsn', ['postgresql://u@h/postgres', 'postgresql://u@h/madden_2036'])
def test_cli_refuses_databases(dsn, capsys):
    assert fr.main(['season', '--season', '2030', '--dsn', dsn]) == fr.EXIT_INVALID
    assert 'refusing database' in capsys.readouterr().err


def test_cli_exit_codes(monkeypatch, capsys):
    lg = League()
    conn = Conn(lg)
    monkeypatch.setattr(fr, 'connect', lambda dsn: conn)
    monkeypatch.setattr(fr, 'load_docs', lambda: DOCS)
    dsn = ['--dsn', 'postgresql://u@h/mu_scratch_test', '--universe', U]
    assert fr.main(['season', '--season', '2030', '--dry-run'] + dsn) == 0
    assert conn.rollbacks == 1 and lg.rows == {}
    assert fr.main(['season', '--season', '2030'] + dsn) == 0 and conn.commits == 1
    assert fr.main(['show', '--persona', 'c1'] + dsn) == 0
    assert '"history"' in capsys.readouterr().out
    assert fr.main(['show', '--persona', 'zz'] + dsn) == fr.EXIT_NOT_FOUND
    assert fr.main(['set', '--persona', 'c1', '--slot', 'want', '--text', 'a ring', '--reason', 'r',
                    '--subject', 'player:db-player:100'] + dsn) == 0
    assert fr.main(['set', '--persona', 'c1', '--slot', 'want', '--text', 'x', '--reason', 'r',
                    '--subject', 'bad'] + dsn) == fr.EXIT_INVALID
    assert fr.main(['revert', '--focus-id', 'focus:nope', '--reason', 'r', '--dsn', dsn[1]]) == fr.EXIT_NOT_FOUND
    assert fr.main(['revert', '--focus-id', fs.focus_id(U, 'c1', 'want', 0), '--reason', 'r',
                    '--dsn', dsn[1]]) == fr.EXIT_CONFLICT
    lg.ready = False
    assert fr.main(['season', '--season', '2030'] + dsn) == fr.EXIT_NOT_FOUND
