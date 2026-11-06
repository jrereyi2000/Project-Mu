"""engines/personality_contract_context.py: identity/alias/revision/universe handling, fake providers, no
initialization or legacy persona reads, storage-failure propagation, frozen security, the relationship adapter
and the cache (PERSONALITY_CONTRACT_POLICY_PLAN.md §2.1, §4). Fakes only, no database."""
import pytest

from engines import personality_contract_context as pcc
from engines import personality_contract_policy as cp
from engines.career_earnings import FINANCIAL_SECURITY_SHARE
from personality import store
from personality.core import Identity, initialize

U = 'mu:test'
AS_OF = {'season': 2031, 'week': 25, 'stage': 'FREE_AGENCY'}
NS = 'save-pgid:1'


@pytest.fixture
def profile():
    return initialize(Identity(U, 'player', 'roster:42'), 'generated', 2026)


@pytest.fixture
def guarded(monkeypatch):
    """Fail loudly if anything initializes a profile or reads the legacy persona path."""
    from newsroom import personas
    monkeypatch.setattr(store, 'ensure', lambda *a, **k: pytest.fail('ensure called on a feature read'))
    monkeypatch.setattr(personas, 'get_persona', lambda *a, **k: pytest.fail('get_persona called'))


def bind(monkeypatch, profile, identity=None):
    ident = identity or Identity(U, 'player', 'roster:42')
    monkeypatch.setattr(store, 'resolve_alias', lambda cur, uid, role, ns, ext: ident if (role, ns, ext) == (
        'player', NS, '7001') else None)
    monkeypatch.setattr(store, 'read', lambda cur, i: profile if i == ident else None)


def load(deps=None, external_id=7001, cache=None, team_ids=(3, 9)):
    return pcc.load_contract_context(object(), universe_id=U, player_id=42, alias_namespace=NS,
                                     external_id=external_id, as_of=AS_OF, team_ids=team_ids, batch_id='b1',
                                     dependencies=deps, cache=cache)


def test_view_is_the_stored_contracts_view_with_identity_and_revision(monkeypatch, guarded, profile):
    bind(monkeypatch, profile)
    view, ctx = load()
    assert view['status'] == 'stored' and view['traits'] == profile['traits']
    assert ctx['persona_id'] == profile['persona_id'] and ctx['revision'] == profile['revision'] == view['revision']
    assert ctx['career']['established'] is None and ctx['security']['secure'] is None   # no providers: unknown
    assert set(ctx['teams']) == {3, 9} and all(t['attachment'] == 'unknown' for t in ctx['teams'].values())


def test_missing_alias_is_neutral_not_initialized(monkeypatch, guarded, profile):
    bind(monkeypatch, profile)
    view, ctx = load(external_id=9999)
    assert view['status'] == 'missing' and ctx['persona_id'] is None and ctx['revision'] is None
    r = cp.contract_personality_delta({'financial': 1.4, 'opportunity': 1, 'contention': 0.2, 'scheme': 0.5,
                                       'team_id': 3}, view, ctx, cp.trial_config(mode='active'))
    assert r['capped_delta'] == 0 and r['reasons'] == ['profile_missing']


def test_alias_without_profile_is_corruption(monkeypatch, guarded, profile):
    bind(monkeypatch, None)
    with pytest.raises(pcc.ContractContextError):
        load()


def test_profile_from_another_universe_is_rejected(monkeypatch, guarded):
    other = initialize(Identity('mu:other', 'player', 'roster:42'), 'generated', 2026)
    bind(monkeypatch, other)
    with pytest.raises(pcc.ContractContextError):
        load()


def test_storage_failure_propagates(monkeypatch, guarded):
    def boom(*a, **k):
        raise RuntimeError('connection lost')
    monkeypatch.setattr(store, 'resolve_alias', boom)
    with pytest.raises(RuntimeError):
        load()


def test_providers_fill_the_context_and_future_sources_are_rejected(monkeypatch, guarded, profile):
    bind(monkeypatch, profile)
    ledger = {'share': FINANCIAL_SECURITY_SHARE + 0.1, 'first_known': 2024, 'complete': True}
    deps = {'current_team': lambda pid, a: 3,
            'career': lambda pid, a: pcc.career_from_accrued(6, ['ps:2030']),
            'security': lambda pid, a: pcc.security_from_ledger(ledger, 2030, ['contracts:2030']),
            'relationships': lambda persona, teams, a: {3: dict(cp.unknown_team(), attachment='strong',
                                                               role_satisfactory=True, treatment_satisfactory=True,
                                                               unresolved_breach=False,
                                                               relationship_version='rel-v1',
                                                               attachment_as_of='2031-24')}}
    view, ctx = load(deps)
    assert ctx['current_team_id'] == 3 and ctx['career']['established'] is True and ctx['security']['secure'] is True
    assert ctx['teams'][3]['attachment'] == 'strong' and ctx['teams'][9]['attachment'] == 'unknown'
    deps['security'] = lambda pid, a: pcc.security_from_ledger(ledger, 2032, [])
    with pytest.raises(cp.ContractPolicyError):
        load(deps)


def test_career_trial_is_four_completed_seasons_without_age_or_ovr():
    assert pcc.career_from_accrued(3, [])['established'] is False
    assert pcc.career_from_accrued(4, [])['established'] is True
    assert pcc.career_from_accrued(None, [])['established'] is None


def test_security_is_frozen_and_independent_of_the_offer(monkeypatch, guarded, profile):
    """The ledger never includes the proposed deal: changing offer money cannot move security or eligibility."""
    bind(monkeypatch, profile)
    ledger = {'share': 0.05, 'first_known': 2027, 'complete': False}
    deps = {'security': lambda pid, a: pcc.security_from_ledger(ledger, 2030, [])}
    _, ctx = load(deps)
    assert ctx['security']['secure'] is None                     # incomplete and below: unknown, not False
    v = {'status': 'stored', 'origin': 'generated', 'traits': ['RING_CHASER'], 'revision': ctx['revision'],
         'persona_id': ctx['persona_id'], 'sliders': {}}
    gates = [cp.contract_personality_delta({'financial': f, 'opportunity': 1.0, 'contention': 0.9, 'scheme': 0.5,
                                            'team_id': 3}, v, ctx, cp.trial_config(mode='active'))['gates']
             for f in (0.4, 0.8, 1.6)]
    assert all(g == gates[0] for g in gates)


def test_cache_is_scoped_to_batch_as_of_and_revision(monkeypatch, guarded, profile):
    bind(monkeypatch, profile)
    calls = []
    deps = {'career': lambda pid, a: calls.append(1) or pcc.career_from_accrued(5, [])}
    cache = {}
    load(deps, cache=cache)
    load(deps, cache=cache)
    assert len(calls) == 1
    profile['revision'] = profile['revision'] + 1                # a JR override bumps the revision
    load(deps, cache=cache)
    assert len(calls) == 2


def test_relationship_adapter_maps_team_edges_and_neutral_is_unknown():
    from personality.relationships import neutral_context
    assert pcc.relationship_teams(neutral_context({'kind': 'person'}), {3: 'CAR'}, as_of_text='2031-24')[3] == \
        cp.unknown_team()
    rel = {'status': 'enabled', 'rule_version': 'relationships-v1', 'edges': [
        {'target': {'kind': 'team', 'universe_id': U, 'team_key': 'CAR'}, 'status': 'known',
         'attachment': {'tier': 'established', 'eligible': True}, 'breaches': [],
         'hometown': {'status': 'verified', 'source_ids': ['home-1']}, 'source_ids': ['ev-1']},
        {'target': {'kind': 'team', 'universe_id': U, 'team_key': 'ATL'}, 'status': 'known',
         'attachment': {'tier': 'none', 'eligible': False},
         'breaches': [{'source_id': 'ev-9', 'promise_id': 'pr-1', 'severity': 'major', 'repair_state': 'open'}],
         'hometown': {'status': 'unknown', 'source_ids': []}, 'source_ids': ['ev-9']}]}
    teams = pcc.relationship_teams(rel, {3: 'CAR', 9: 'ATL', 11: 'NO'}, as_of_text='2031-24')
    assert teams[3]['attachment'] == 'established' and teams[3]['role_satisfactory'] is True
    assert teams[3]['unresolved_breach'] is False and teams[3]['meaningful_home_match'] is True
    assert teams[9]['grudge'] is True and teams[9]['unresolved_breach'] is True and teams[9]['role_satisfactory'] is None
    assert teams[11] == cp.unknown_team()
    ctx = cp.neutral_context(U, 42, AS_OF, 'b', team_ids=(3, 9, 11))
    ctx['teams'].update(teams)
    cp.validate_contract_context(ctx)
