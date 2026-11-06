"""Contract-policy context and CPU voluntary-deal evaluation (PERSONALITY_CONTRACT_POLICY_PLAN.md §2.1, §2.4, U3).

  load_contract_context(cur, *, universe_id, player_id, alias_namespace, external_id, as_of, team_ids, batch_id,
                        dependencies, cache=None) -> (view, context)
      view: personality.core.feature_view(profile, 'contracts') through store.resolve_alias + store.read (never
      ensure/initialize, never newsroom.personas.get_persona); a missing alias is the neutral 'missing' view; an
      alias whose profile is gone, or a profile from another universe, raises (corruption is not missing data).
      context: context v1 (engines/personality_contract_policy.py) from read-only providers in `dependencies`:
        current_team(player_id, as_of)            -> team id | None
        career(player_id, as_of)                  -> {established, source_ids, version} | None
        security(player_id, as_of)                -> {secure, earned_share, complete, through_season, source_ids,
                                                      version} | None
        relationships(persona_id, team_ids, as_of) -> {team_id: team record} | None   (his own one-way view)
      A missing provider (or None) is explicitly unknown, so nothing it would gate can activate. Provider errors
      propagate. `cache` (a dict) memoizes within one frozen (batch, universe, player, as_of, revision) only.

  Provider adapters (pure): career_from_accrued (trial Q2: four completed accrued seasons, no age/OVR shortcut),
  security_from_ledger (career_earnings.is_secure, built; the proposed deal is never in the ledger), and
  relationship_teams (personality.relationships.relationship_context edges -> team records; the relationships
  plan owns trust, attachment and breaches; a neutral/observe context gives all-unknown records).

  evaluate_voluntary_contract(league, ctx, player, contract, *, contract_inputs, seed) -> dict
      A CPU re-sign or extension offer (transactions_engine league/ctx/player; contract = offer terms) put to the
      player through free_agency_engine's shared utility, acceptance and counter search with the same frozen
      inputs. Returns {status, counter, score, bar, trace, offer}. The team-side checks stay in transactions_engine.
"""
from copy import deepcopy

from engines import free_agency_engine as fae
from engines import personality_contract_policy as cp

CAREER_VERSION = 'career-accrued-v1'
SECURITY_VERSION = 'career-earnings-v1'
ESTABLISHED_SEASONS = 4


class ContractContextError(cp.ContractPolicyError):
    pass


# --------------------------------------------------------------- adapters ----
def career_from_accrued(accrued_seasons, source_ids, *, version=CAREER_VERSION, min_seasons=ESTABLISHED_SEASONS):
    """Established career from verified completed accrued seasons (None = unknown)."""
    if accrued_seasons is None:
        return {'established': None, 'source_ids': list(source_ids), 'version': version}
    return {'established': int(accrued_seasons) >= min_seasons, 'source_ids': list(source_ids), 'version': version}


def security_from_ledger(entry, through_season, source_ids, *, version=SECURITY_VERSION):
    """career_earnings ledger entry -> security block. Unknown stays unknown (inactive)."""
    from engines.career_earnings import is_secure
    return {'secure': is_secure(entry), 'earned_share': None if not entry else float(entry['share']),
            'complete': None if not entry else bool(entry.get('complete')), 'through_season': int(through_season),
            'source_ids': list(source_ids), 'version': version}


def relationship_teams(rel_context, team_keys, *, as_of_text):
    """personality.relationships.relationship_context(...) -> {team_id: team record}. team_keys {team_id:
    team_key}. Only TeamRef edges count; attachment eligibility stands for satisfactory role/treatment evidence
    (the owner's tier gate), unresolved breaches for breach and certified grudge."""
    out = {int(t): cp.unknown_team() for t in team_keys}
    if not rel_context or rel_context.get('status') != 'enabled':
        return out
    version = rel_context.get('rule_version') or 'relationships'
    by_key = {e['target'].get('team_key'): e for e in rel_context['edges'] if e['target'].get('kind') == 'team'}
    for tid, key in team_keys.items():
        e = by_key.get(key)
        if e is None:
            continue
        known = e.get('status') in ('known', 'conflicted')
        tier = e['attachment']['tier'] if e['attachment']['tier'] in cp.TIERS else 'unknown'
        eligible = e['attachment']['eligible']
        breaches = bool(e.get('breaches'))
        home = e.get('hometown') or {}
        out[int(tid)] = {'attachment': tier, 'role_satisfactory': True if eligible else None,
                         'treatment_satisfactory': True if eligible else None,
                         'unresolved_breach': True if breaches else (False if known else None),
                         'grudge': True if breaches else (False if known else None),
                         'source_event_ids': list(e.get('source_ids') or []), 'relationship_version': version,
                         'attachment_as_of': as_of_text,
                         'meaningful_home_match': True if home.get('status') == 'verified' else None,
                         'home_source_ids': list(home.get('source_ids') or [])}
    return out


# ---------------------------------------------------------------- loading ----
def _view(cur, universe_id, alias_namespace, external_id):
    from personality import store
    from personality.core import feature_view
    identity = store.resolve_alias(cur, universe_id, 'player', alias_namespace, str(external_id))
    if identity is None:
        return feature_view(None, 'contracts'), None
    if identity.universe_id != universe_id:
        raise ContractContextError('alias resolved into another universe')
    profile = store.read(cur, identity)
    if profile is None:
        raise ContractContextError(f'alias {alias_namespace}:{external_id} has no stored profile (corrupt)')
    if profile.get('universe_id') not in (None, universe_id):
        raise ContractContextError('stored profile belongs to another universe')
    return feature_view(profile, 'contracts'), identity


def load_contract_context(cur, *, universe_id, player_id, alias_namespace, external_id, as_of, team_ids, batch_id,
                          dependencies, cache=None):
    """(view, context) for one player decision (module docstring). No initialization, no writes."""
    deps = dependencies or {}
    view, _identity = _view(cur, universe_id, alias_namespace, external_id)
    key = (batch_id, universe_id, player_id, tuple(sorted(as_of.items())), view.get('revision'),
           tuple(sorted(int(t) for t in team_ids)))
    if cache is not None and key in cache:
        return deepcopy(cache[key])
    ctx = cp.neutral_context(universe_id, int(player_id), as_of, batch_id, persona_id=view.get('persona_id'),
                             revision=view.get('revision'), team_ids=team_ids)
    if deps.get('current_team'):
        ctx['current_team_id'] = deps['current_team'](player_id, as_of)
    if deps.get('career'):
        ctx['career'] = deps['career'](player_id, as_of) or ctx['career']
    if deps.get('security'):
        ctx['security'] = deps['security'](player_id, as_of) or ctx['security']
    if deps.get('relationships') and view.get('persona_id'):
        for tid, rec in (deps['relationships'](view['persona_id'], list(team_ids), as_of) or {}).items():
            ctx['teams'][int(tid)] = rec
    ctx = cp.normalize_contract_context(ctx)
    if cache is not None:
        cache[key] = deepcopy((view, ctx))
    return view, ctx


# ------------------------------------------------------------- evaluation ----
def evaluate_voluntary_contract(league, ctx, player, contract, *, contract_inputs, seed):
    """A CPU re-sign/extension offer put to the player (module docstring). contract: {years, total_value, bonus,
    guaranteed, salary_by_year?}; contract_inputs: {view, context, policy} for this player."""
    from engines import transactions_engine as te
    inp = contract_inputs or {}
    years_ahead = league['target'] - league['season']
    fplayer = {'id': player['id'], 'group': player.get('group'), 'ovr': player.get('ovr'),
               'age': (player.get('age') or 27) + years_ahead, 'accrued_seasons': te.accrued(player, league)}
    depth = te.projected_depth(player, ctx)
    team = {'team_id': ctx['team_id'], 'win_pct': ctx.get('win_pct'), 'popularity_index': ctx.get('popularity_index'),
            'direction': ctx.get('direction'), 'projected_depth': {player['id']: depth}}
    offer = {'team_id': ctx['team_id'], 'player_id': player['id'], 'years': int(contract['years']),
             'total_value': int(contract['total_value']), 'bonus': int(contract.get('bonus') or 0),
             'guaranteed': int(contract.get('guaranteed') or 0), 'projected_depth': depth}
    if contract.get('salary_by_year'):
        offer['salary_by_year'] = list(contract['salary_by_year'])
    market = (league.get('fits') or {}).get('market')
    kw = {'contract_context': inp.get('context'), 'policy': inp.get('policy')}
    score = fae.offer_utility(offer, fplayer, team, market, inp.get('view'), **kw)
    trace = {'seed': seed}
    status, counter = fae.acceptance({'offer': offer, 'team': team, 'market': market, 'score': score}, fplayer,
                                     False, persona=inp.get('view'), trace=trace, **kw)
    return {'status': status, 'counter': counter, 'score': score, 'bar': round(fae.accept_bar(fplayer, False), 4),
            'trace': trace, 'offer': offer}
