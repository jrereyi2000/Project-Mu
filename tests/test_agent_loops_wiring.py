"""Agent-loop wiring (COACH_PLAYER_AGENT_LOOPS_PLAN.md §2.5, U3a).

Offline: the league office over fake feature adapters, the free-agency and transactions engines over synthetic
dicts, and the inbox over tests/test_inbox_store.InboxDB. The loops' decision store is a MemoryStore (SqlStore is
replaced), so no database is touched. The model is never called: make_loop_llm returns None and the policy decides.
"""
import pytest

from engines import depth_chart_engine as dce
from engines import free_agency_engine as fae
from engines import personality_contract_context as pcc
from engines import transactions_engine as te
from engines.agent_loops import base
from engines.agent_loops import player as player_loop
from engines.inbox import store as inbox_store, voice
from engines.inbox.kinds import contract as contract_kind, role_talk as role_talk_kind
from personality import features as pf
from pipeline import league_office_personality as lop
from tests import test_free_agency_engine as tfa
from tests import test_transactions_engine as tte
from tests.test_inbox_store import InboxDB

LEAGUE = 7
SEASON = 2026
WEEK = 5


# --------------------------------------------------------------------------------------------- fixtures ---
def snap_modes(**modes):
    feats = {f: {'feature': f, 'mode': 'off', 'version': None, 'config': {}} for f in pf.FEATURES}
    for f, m in modes.items():
        feats[f] = {'feature': f, 'mode': m, 'version': f'{f}-v1', 'config': {}}
    feats['development_opportunity']['config'] = {'close_score_window': 2.0}
    return {'league_id': LEAGUE, 'source': 'switchboard', 'features': feats}


def person(pid, position, overall, age=26):
    return {'player_id': pid, 'position': position, 'overall': overall, 'age': age, 'archetype': None,
            'ratings': {}}


ROSTER = [person(1, 'QB', 90), person(2, 'QB', 89),                       # a close QB call; 2 is the backup
          person(10, 'HB', 80), person(11, 'FB', 75),
          person(3, 'WR', 85), person(4, 'WR', 84), person(5, 'WR', 83), person(6, 'WR', 82),
          person(12, 'TE', 75),
          person(13, 'LT', 80), person(14, 'LG', 80), person(15, 'C', 80), person(16, 'RG', 80),
          person(17, 'RT', 80),
          person(18, 'LE', 80), person(19, 'RE', 80), person(20, 'DT', 80), person(21, 'DT', 79),
          person(22, 'LOLB', 80), person(23, 'MLB', 80), person(24, 'ROLB', 80),
          person(25, 'CB', 80), person(26, 'CB', 79), person(27, 'FS', 80), person(28, 'SS', 80),
          person(29, 'K', 80), person(30, 'P', 80), person(31, 'LS', 80)]


class ChartFeatures:
    def __init__(self, adjustments=None, view_fails=False):
        self.adjustments = adjustments or {}
        self.view_fails = view_fails
        self.calls = []

    def chart_adjustments(self, cur, league_id, row, *, team_id, season, week, roster, scheme, injuries):
        self.calls.append(('dev', team_id))
        return {'adjustments': self.adjustments, 'reasons': []}

    def coach_view(self, cur, league_id, team_id, season):
        self.calls.append(('view', team_id))
        if self.view_fails:
            raise RuntimeError('no coach view')
        return {'status': 'stored', 'persona_id': 'coach-1', 'traits': [], 'sliders': {}}


def view_for(pid, traits=()):
    return {'status': 'stored', 'origin': 'generated', 'sliders': {}, 'traits': list(traits), 'revision': 0,
            'persona_id': f'p-{pid}', 'catalogue_version': 'c', 'core_version': 'v', 'view_version': 'views-v2'}


class ContractFeatures:
    def __init__(self, traits=('MONEY_FIRST',)):
        self.traits = traits
        self.calls = []

    def contract_inputs(self, cur, league_id, policy, *, stage, season, week, players, batch_id):
        self.calls.append(('inputs', stage, policy))
        return {p['id']: {'view': view_for(p['id'], self.traits), 'context': {}, 'policy': policy}
                for p in (players or [])}

    def chart_adjustments(self, *a, **k):
        return {'adjustments': {}, 'reasons': []}

    def coach_view(self, *a, **k):
        return None


def deps(**modes):
    features = modes.pop('features', None)
    return lop.PersonalityDeps(None, LEAGUE, snap_modes(**modes), features)


def counter_case():
    p = tfa.player(pid=1, ovr=85)
    o = dict(tfa.offer(p, team_id=3, mult=0.75), team=tfa.REBUILDER, market=tfa.MARKET)
    return p, o


@pytest.fixture
def memory(monkeypatch):
    """Decision store = one MemoryStore; no loop model is ever built."""
    store = base.MemoryStore()
    monkeypatch.setattr(base, 'SqlStore', lambda cur: store)
    monkeypatch.setattr(base, 'make_loop_llm', lambda **kw: None)
    return store


# --------------------------------------------------------------------------------------- switchboard ---
def test_switchboard_order_keeps_the_loops_after_development_opportunity():
    i = list(pf.FEATURES).index('development_opportunity')
    assert pf.FEATURES[i + 1] == 'coach_loop' and pf.FEATURES[i + 2] == 'player_loop'
    assert pf.REQUIREMENTS['coach_loop'][0] == 'engines.agent_loops.base'
    assert pf.REQUIREMENTS['player_loop'][0] == 'engines.agent_loops.base'


# ---------------------------------------------------------------------------------------------- charts ---
def test_coach_loop_off_leaves_chart_adjustments_unchanged(memory):
    feats = ChartFeatures(adjustments={'WR': {3: 5.0}})
    d = deps(development_opportunity='active', features=feats)
    assert d.chart_adjustments(None, 1, SEASON, WEEK, ROSTER, {}, {}) == {'WR': {3: 5.0}}
    assert feats.calls == [('dev', 1)] and memory.find(LEAGUE, SEASON, 'CHART') == []
    d2 = deps(features=ChartFeatures())
    assert d2.chart_adjustments(None, 1, SEASON, WEEK, ROSTER, {}, {}) is None
    assert d2.features.calls == []


def test_coach_loop_shadow_records_a_chart_and_returns_the_development_baseline(memory):
    d = deps(development_opportunity='active', coach_loop='shadow',
             features=ChartFeatures(adjustments={'WR': {3: 5.0}}))
    out = d.chart_adjustments(None, 1, SEASON, WEEK, ROSTER, {}, {})
    assert out == {'WR': {3: 5.0}}
    rows = memory.find(LEAGUE, SEASON, 'CHART')
    assert len(rows) == 1 and rows[0]['subject_key'] == 'team:1:chart'
    assert rows[0]['chosen_key'] in {o['key'] for o in rows[0]['options']}
    rec = [r for r in d.receipts if r['feature'] == 'coach_loop']
    assert len(rec) == 1 and rec[0]['status'] == 'recorded' and rec[0]['baseline'] == out


def test_coach_loop_active_adjustments_reproduce_the_chosen_chart(monkeypatch, memory):
    monkeypatch.setattr(base, 'policy_choice',
                        lambda options, persona, weights, rng:
                        next(o['key'] for o in options if o['key'] != 'NEUTRAL'))
    d = deps(coach_loop='active', features=ChartFeatures())
    adj = d.chart_adjustments(None, 1, SEASON, WEEK, ROSTER, {}, {})
    row = memory.find(LEAGUE, SEASON, 'CHART')[0]
    option = next(o for o in row['options'] if o['key'] == row['chosen_key'])
    detail = option['detail']
    assert detail and detail['slot'] in adj
    delta = adj[detail['slot']]
    expected = min(abs(detail['gap']) + 0.01, dce.MAX_SCORE_ADJUSTMENT)
    assert delta[detail['promote']] == pytest.approx(expected)
    assert delta[detail['demote']] == pytest.approx(-expected)
    chart = dce.staff_depth_chart(ROSTER, {}, {}, score_adjustments=adj)
    ids = chart[detail['slot']]
    assert ids.index(detail['promote']) < ids.index(detail['demote'])


def test_failed_coach_loop_never_fails_a_shadow_write(memory):
    feats = ChartFeatures(adjustments={'WR': {3: 5.0}}, view_fails=True)
    d = deps(development_opportunity='active', coach_loop='shadow', features=feats)
    assert d.chart_adjustments(None, 1, SEASON, WEEK, ROSTER, {}, {}) == {'WR': {3: 5.0}}
    assert any(r['feature'] == 'coach_loop' and r['status'] == 'error' for r in d.receipts)


# ------------------------------------------------------------------------------------ CPU contracts ---
def test_player_loop_off_leaves_free_agency_unchanged(memory):
    players, offers = tfa.wave_fixture(12)
    d = deps(features=ContractFeatures())
    assert d.run_wave(players, offers, 3, 's') == fae.run_wave(players, offers, 3, 's')
    assert d.receipts == [] and memory.find(LEAGUE, SEASON, 'CONTRACT') == []


def test_player_loop_off_leaves_resign_on_the_baseline_path(monkeypatch):
    seen = []
    monkeypatch.setattr(te, 'resign_decisions',
                        lambda cur, season, seed, *, contract_inputs=None, decisions=None:
                        seen.append(contract_inputs) or [{'kind': 'baseline'}])
    d = deps(features=ContractFeatures())
    assert d.resign_decisions(None, SEASON, 's') == [{'kind': 'baseline'}]
    assert seen == [None]          # the baseline call, never the adjusted path with contract inputs


def test_player_loop_shadow_records_a_contract_and_keeps_the_engine_counter(memory):
    p, offer = counter_case()
    baseline = fae.run_wave([p], [offer], 1, 's')[0]
    assert baseline['status'] == 'COUNTER'
    d = deps(player_loop='shadow', features=ContractFeatures())
    d.begin_stage('FREE_AGENCY', SEASON, 25, 1, 'seed')
    out = d.run_wave([p], [offer], 1, 's')[0]
    assert (out['status'], out['team_id'], out['counter']) == (
        baseline['status'], baseline['team_id'], baseline['counter'])
    rows = memory.find(LEAGUE, SEASON, 'CONTRACT')
    assert len(rows) == 1 and rows[0]['subject_key'] == 'player:1:offer:team3'
    assert rows[0]['chosen_key'] in {'COUNTER_MIN', 'COUNTER_ASK', 'COUNTER_HARD', 'REJECT', 'ACCEPT'}
    assert any(r['feature'] == 'player_loop' and r['status'] == 'recorded' for r in d.receipts)


def test_player_loop_active_uses_the_chosen_clearing_counter(monkeypatch, memory):
    monkeypatch.setattr(base, 'policy_choice',
                        lambda options, persona, weights, rng:
                        ([o['key'] for o in options if str(o['key']).startswith('COUNTER_')] or ['REJECT'])[-1])
    p, offer = counter_case()
    baseline = fae.run_wave([p], [offer], 1, 's')[0]
    d = deps(player_loop='active', features=ContractFeatures())
    d.begin_stage('FREE_AGENCY', SEASON, 25, 1, 'seed')
    out = d.run_wave([p], [offer], 1, 's')[0]
    row = memory.find(LEAGUE, SEASON, 'CONTRACT')[0]
    chosen = next(o for o in row['options'] if o['key'] == row['chosen_key'])['detail']['counter']
    assert out['counter'] == chosen and chosen != baseline['counter']
    assert any(r['feature'] == 'player_loop' and r['status'] == 'applied' for r in d.receipts)


def test_evaluate_voluntary_contract_passes_the_counter_chooser():
    lg, _ids = tte.star_league()
    ctx = te.contexts(lg)[3]
    p = lg['players'][10]
    inp = dict(tte.pinputs(10, 'active', ['MONEY_FIRST']))
    chosen = []

    def chooser(offer, player, team, market, bar, accepts):
        opts = fae.counter_options(offer, player, team, market, bar, persona=inp['view'],
                                   contract_context=inp['context'], policy=inp['policy'])
        chosen.append(opts[-1] if opts else None)
        return chosen[-1]

    inp['counter_chooser'] = chooser
    terms = te._resign_terms(8_000_000, 3, (p.get('age') or 27) + 1 >= te.VET_AGE)
    ev = pcc.evaluate_voluntary_contract(lg, ctx, p, terms, contract_inputs=inp, seed='s')
    assert ev['status'] == 'COUNTER' and chosen and ev['counter'] == chosen[-1]


def test_re_sign_still_lets_the_team_refuse_the_loops_counter():
    lg, _ids = tte.star_league()
    ctx = te.contexts(lg)[3]
    p = lg['players'][10]
    inp = dict(tte.pinputs(10, 'active', ['MONEY_FIRST']))
    chosen = []

    def chooser(offer, player, team, market, bar, accepts):
        opts = fae.counter_options(offer, player, team, market, bar, persona=inp['view'],
                                   contract_context=inp['context'], policy=inp['policy'])
        chosen.append(opts[-1] if opts else None)
        return chosen[-1]

    inp['counter_chooser'] = chooser
    vet = (p.get('age') or 27) + 1 >= te.VET_AGE
    move, _cost = te._resign_by_consent(lg, ctx, p, 10_000_000, 8_000_000, 3, False, 0,
                                        te.salaries_by_group(lg), 's', inp, None, vet)
    assert move['kind'] == 'LET_GO' and move['consent'] == 'COUNTER'
    assert move['reason'] == 'no cap room for his counter'
    assert chosen and move['counter'] == chosen[-1]


def test_failed_player_loop_never_fails_a_shadow_write(monkeypatch, memory):
    p, offer = counter_case()
    baseline = fae.run_wave([p], [offer], 1, 's')[0]

    def boom(*a, **k):
        raise RuntimeError('loop down')

    monkeypatch.setattr(player_loop, 'respond_contract', boom)
    d = deps(player_loop='shadow', features=ContractFeatures())
    d.begin_stage('FREE_AGENCY', SEASON, 25, 1, 'seed')
    out = d.run_wave([p], [offer], 1, 's')[0]
    assert (out['status'], out['counter']) == (baseline['status'], baseline['counter'])
    assert any(r['feature'] == 'player_loop' and r['status'] == 'error' for r in d.receipts)


# -------------------------------------------------------------------------------------------- inbox ---
@pytest.fixture
def inbox_db():
    d = InboxDB()
    d.conn.execute('ALTER TABLE league_state ADD COLUMN madden_league_id INTEGER')
    d.conn.execute('UPDATE league_state SET madden_league_id = ?', (LEAGUE,))
    return d


@pytest.fixture
def cur(inbox_db):
    return inbox_db.cursor()


class FakeLLM:
    def __init__(self, answer='not json'):
        self.answer, self.calls = answer, []

    def __call__(self, prompt):
        self.calls.append(prompt)
        return self.answer


def contract_thread(cur):
    tid = inbox_store.create_thread(cur, {
        'team_id': 1, 'kind': 'CONTRACT', 'actor_role': 'GM', 'actor_id': '10',
        'counterpart_type': 'player', 'counterpart_id': '101', 'counterpart_name': 'Xavier Legette',
        'subject': "Xavier Legette's contract", 'started_by': 'COUNTERPART', 'source_ref': 'contract:101:2026',
        'season': SEASON, 'week': WEEK, 'state': {}})
    return inbox_store.get_thread(cur, tid)


def role_thread(cur):
    tid = inbox_store.create_thread(cur, {
        'team_id': 1, 'kind': 'ROLE_TALK', 'actor_role': 'COACH', 'actor_id': '41',
        'counterpart_type': 'player', 'counterpart_id': '101', 'counterpart_name': 'Xavier Legette',
        'subject': "Xavier Legette's role", 'started_by': 'COUNTERPART', 'source_ref': 'role:lost:101:2026:5',
        'season': SEASON, 'week': WEEK,
        'state': {'topic': 'LOST_STARTING_JOB', 'slot': 'WR', 'unhappy_weeks': 3}})
    return inbox_store.get_thread(cur, tid)


CONTRACT_FACTS = {'player': 'Xavier Legette', 'team': 'CAR', 'decision': 'COUNTER',
                  'counter': {'years': 4, 'total_value': 44_000_000, 'bonus': 0, 'guaranteed': 18_000_000}}


def test_inbox_contract_uses_the_player_loop(monkeypatch, cur):
    monkeypatch.setattr(pf, 'mode', lambda cur, league, feature: 'active' if feature == 'player_loop' else 'off')
    seen = []

    def fake_respond(cur, thread, view, facts, *, llm, **kw):
        seen.append(dict(facts))
        return {'decision': {}, 'stance': 'COUNTER_ASK', 'text': 'I want more guaranteed money.'}

    monkeypatch.setattr(player_loop, 'respond_inbox', fake_respond)
    llm = FakeLLM()
    reply = voice.compose(cur, contract_kind, contract_thread(cur), 'COUNTER', CONTRACT_FACTS, [], llm=llm)
    assert reply.text == 'I want more guaranteed money.' and reply.source == 'LLM' and reply.appraisal is None
    assert llm.calls == []
    assert seen[0]['engine_status'] == 'COUNTER'
    assert seen[0]['counters']['COUNTER_MIN']['total_value'] == 44_000_000


def test_inbox_contract_keeps_its_voice_when_player_loop_off(monkeypatch, cur):
    monkeypatch.setattr(pf, 'mode', lambda *a: 'off')
    monkeypatch.setattr(player_loop, 'respond_inbox',
                        lambda *a, **k: pytest.fail('the player loop must not run when it is off'))
    llm = FakeLLM()
    reply = voice.compose(cur, contract_kind, contract_thread(cur), 'COUNTER', CONTRACT_FACTS, [], llm=llm)
    assert reply.source == 'TEMPLATE' and 'offer needs to be better' in reply.text
    assert llm.calls


def test_inbox_role_talk_uses_the_player_loop_and_passes_unhappy_weeks(monkeypatch, cur):
    monkeypatch.setattr(pf, 'mode', lambda cur, league, feature: 'shadow' if feature == 'player_loop' else 'off')
    seen = []

    def fake_respond(cur, thread, view, facts, *, llm, **kw):
        seen.append(dict(facts))
        return {'decision': {}, 'stance': 'FRUSTRATED', 'text': 'I want those reps.'}

    monkeypatch.setattr(player_loop, 'respond_inbox', fake_respond)
    reply = voice.compose(cur, role_talk_kind, role_thread(cur), 'LOST_STARTING_JOB',
                          {'team': 'CAR', 'player': 'Xavier Legette', 'slot': 'WR'}, [], llm=FakeLLM())
    assert reply.text == 'I want those reps.' and reply.source == 'LLM'
    assert seen[0]['unhappy_weeks'] == 3


def test_inbox_role_talk_keeps_its_voice_when_player_loop_off(monkeypatch, cur):
    monkeypatch.setattr(pf, 'mode', lambda *a: 'off')
    monkeypatch.setattr(player_loop, 'respond_inbox',
                        lambda *a, **k: pytest.fail('the player loop must not run when it is off'))
    reply = voice.compose(cur, role_talk_kind, role_thread(cur), 'LOST_STARTING_JOB',
                          {'team': 'CAR', 'player': 'Xavier Legette', 'slot': 'WR'}, [], llm=FakeLLM())
    assert reply.source == 'TEMPLATE' and 'I was starting' in reply.text


def test_inbox_role_talk_turns_get_distinct_decisions_and_replay(monkeypatch, memory, cur):
    """message_seq = len(history): each new turn records its own INBOX_REPLY; the same turn replays."""
    monkeypatch.setattr(pf, 'mode', lambda cur, league, feature: 'active')
    real = player_loop.respond_inbox
    decisions = []

    def spy(*a, **kw):
        res = real(*a, **kw)
        decisions.append(res['decision'])
        return res

    monkeypatch.setattr(player_loop, 'respond_inbox', spy)
    thread = role_thread(cur)
    facts = {'team': 'CAR', 'player': 'Xavier Legette', 'slot': 'WR'}
    llm = FakeLLM()                      # its words don't ground, but the decision is still recorded
    assert role_talk_kind.loop_reply(cur, thread, 'LOST_STARTING_JOB', facts, llm, history=[{}, {}]) is None
    assert role_talk_kind.loop_reply(cur, thread, 'LOST_STARTING_JOB', facts, llm, history=[{}, {}]) is None
    assert role_talk_kind.loop_reply(cur, thread, 'LOST_STARTING_JOB', facts, llm,
                                     history=[{}, {}, {}]) is None
    subjects = sorted(r['subject_key'] for r in memory.find(LEAGUE, SEASON, 'INBOX_REPLY'))
    assert subjects == [f"thread:{thread['id']}:msg:2", f"thread:{thread['id']}:msg:3"]
    assert [d['replayed'] for d in decisions] == [False, True, False]
