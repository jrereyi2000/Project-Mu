"""Personality inputs for the league office (docs/done/PERSONALITY_ACTIVATION_PLAN.md §2.3, U2).

`PersonalityDeps` is a `pipeline.league_office.Deps` whose engine calls pass each personality feature's inputs
according to that feature's mode in the switchboard (`personality/features.py`), read once per write and frozen:

  resign_decisions / run_wave / udfa_market   contract_inputs, when contracts_ambition / contracts_relationships
                                              is not off (the contract plan's policy, PERSONALITY_CONTRACT_POLICY_PLAN)
  progression_weekly                          development_config / development_context, when development_learning
                                              is not off (PERSONALITY_DEVELOPMENT_COACHING_PLAN U3)
  chart_adjustments                           opportunity score adjustments for CPU staff charts, used only when
                                              development_opportunity is active (shadow: computed and recorded)
  retirement_decision                         the retirement policy's keep / retire for players Madden retired
                                              (PERSONALITY_RETIREMENT_PLAN); never retires an active player
  cpu_offers                                  each CPU team's owner spending knobs (owner_spending;
                                              OWNERS_SCOUTS_VOTERS_PLAN §2.3 U5); shadow plans the owner offers
                                              without storing them and records per-team counts
  signings_from_decisions                     the owner's review of a CPU team's top-50 free-agent signing
                                              (owner_meddling): active drops a VETOed signing (one veto a team a
                                              season, stored in owner_decisions when the table exists); shadow
                                              records the decisions. JR's teams are never reviewed.

Modes: **off** is today's call, exactly. **shadow** runs the baseline (today's call), returns it unchanged, and
records the adjusted result (computed as if active) in `receipts`; an adjusted-path error is recorded and never
fails the write. Development learning is the exception: its progression events are written while they are
prepared, so it is not run twice; the development policy's own shadow mode records the comparison in the
progression receipt and changes nothing. **active** returns the adjusted result; an adjusted-path error fails the
plan (the league office records the failure and writes nothing).

  deps_for(cur, snapshot) -> Deps          PersonalityDeps when any feature beyond producers is not off, else Deps()
  default_deps(cur) -> Deps                the switchboard snapshot for league_state's league, then deps_for
  contract_policy(snapshot, *, counterfactual=False) -> policy config | None

Both carry `modes_snapshot`; the league office stores it in the run's operation record so `--recover` rebuilds
the same deps without reading the switchboard again. The feature calls themselves go through `features`
(`LiveFeatures` by default), so tests pass fakes and no feature logic lives here.
"""
from __future__ import annotations

from copy import deepcopy

from personality import features as pf
from pipeline import league_office as lo

CONTRACT_FEATURES = ('contracts_ambition', 'contracts_relationships')
OWNER_FEATURES = ('owner_spending', 'owner_meddling')
LEAGUE_OFFICE_FEATURES = CONTRACT_FEATURES + ('development_learning', 'development_opportunity', 'retirement') + \
    OWNER_FEATURES
ORDER = {m: i for i, m in enumerate(pf.MODES)}
YOUTH_MAX_AGE, VETERAN_MIN_AGE = 24, 30        # cohort labels for opportunity candidates (the coaching plan's cohorts)
OFFSEASON_TICK = 24                             # a free-agency stage without a week: the first offseason tick


def _fields(cls, data, drop=('mode', 'version')):
    names = set(getattr(cls, '__dataclass_fields__', {}))
    return {k: v for k, v in (data or {}).items() if k in names and k not in drop}


def contract_policy(snapshot, *, counterfactual=False):
    """The contract policy config for this write from the frozen snapshot (read_contract_activation's rules), or
    None when both contract features are off. counterfactual=True treats a shadow family as active (what shadow
    records)."""
    from engines import personality_contract_store as cs
    from engines.personality_contract_policy import validate_contract_policy
    modes, cfg = {}, cs._default_config()
    for family, name in cs.SWITCHBOARD_FEATURES.items():
        row = ((snapshot or {}).get('features') or {}).get(name) or {}
        m = row.get('mode') or 'off'
        modes[family] = 'active' if counterfactual and m == 'shadow' else m
        cfg.update({k: v for k, v in (row.get('config') or {}).items()
                    if k not in ('mode', 'ambition_enabled', 'relationship_enabled', 'report_path')})
        if row.get('version'):
            cfg['version'] = row['version']
    top = max(modes.values(), key=ORDER.get)
    if top == 'off':
        return None
    cfg['mode'] = top
    cfg['ambition_enabled'] = modes['ambition'] == top
    cfg['relationship_enabled'] = modes['relationship'] == top
    validate_contract_policy(cfg)
    return cfg


class LiveFeatures:
    """Production adapters over the merged feature modules. Each returns plain data; tests replace the object."""

    def __init__(self):
        self._universe = {}

    # -- shared
    def universe(self, cur, league_id):
        if league_id not in self._universe:
            cur.execute("SELECT to_regclass('persona_lifecycle_universes') IS NOT NULL")
            if not cur.fetchone()[0] or league_id is None:
                self._universe[league_id] = None
            else:
                from personality import lifecycle_store
                self._universe[league_id] = lifecycle_store.universe_for_league(cur, league_id)
        return self._universe[league_id]

    def _universe_or_fail(self, cur, league_id):
        u = self.universe(cur, league_id)
        if u is None:
            raise lo.StageError(f'no personality lifecycle universe for league {league_id}')
        return u

    def _pgids(self, cur):
        cur.execute('SELECT id, madden_pgid FROM players WHERE madden_pgid IS NOT NULL')
        return {int(p): int(g) for p, g in cur.fetchall()}

    def head_coach(self, cur, team_id, season):
        cur.execute("SELECT to_regclass('coach_tenures') IS NOT NULL")
        if not cur.fetchone()[0]:
            return None
        cur.execute('SELECT coach_id FROM coach_tenures WHERE team_id = %s AND start_season <= %s '
                    'AND COALESCE(end_season, 9999) >= %s ORDER BY start_season DESC, id DESC LIMIT 1',
                    (team_id, season, season))
        row = cur.fetchone()
        return None if row is None else row[0]

    # -- contracts
    def contract_inputs(self, cur, league_id, policy, *, stage, season, week, players, batch_id):
        """{player_id: {view, context, policy}}. players None = the re-sign stage's candidates (contracts ending
        this season or entering their final year)."""
        from engines import personality_contract_context as pcc
        from engines import transactions_engine as te
        from personality import lifecycle as plc
        universe = self._universe_or_fail(cur, league_id)
        if players is None:
            league = te.fetch_league(cur, season)
            S, T = league['season'], league['target']
            players = [p for p in league['players'].values() if p.get('team_id') and te.end_season(p) in (S, T)]
        pgids, cache, out = self._pgids(cur), {}, {}
        as_of = {'season': season, 'week': week, 'stage': stage}
        for p in players:
            pid = p.get('id', p.get('player_id'))
            if pid is None:
                continue
            team = p.get('team_id')
            pgid = pgids.get(int(pid))
            view, ctx = pcc.load_contract_context(
                cur, universe_id=universe['universe_id'], player_id=int(pid),
                alias_namespace=plc.pgid_namespace(universe), external_id='none' if pgid is None else pgid,
                as_of=as_of, team_ids=[team] if team else [], batch_id=batch_id,
                dependencies={'current_team': lambda _pid, _a, t=team: t}, cache=cache)
            out[int(pid)] = {'view': view, 'context': ctx, 'policy': policy}
        return out

    # -- development learning
    def development_config(self, row, mode):
        from engines.personality_development import DevelopmentConfig
        return DevelopmentConfig.from_dict(dict(_fields(DevelopmentConfig, row.get('config')), version=row.get(
            'version') or '', mode='enabled' if mode == 'active' else 'shadow'))

    def development_inputs(self, cur, league_id, inputs, *, season, week):
        """inputs (progression_inputs.fetch_inputs) with a frozen development_context per player. Teaching stays
        neutral without caller-verified exposure intervals (none are recorded yet)."""
        from engines.personality_development_inputs import load_development_context
        from personality import lifecycle as plc
        universe = self._universe_or_fail(cur, league_id)
        cur.execute('SELECT id, team_id FROM players WHERE id = ANY(%s)', (sorted(inputs),))
        teams = dict(cur.fetchall())
        out = {}
        for pid, data in inputs.items():
            hc = self.head_coach(cur, teams.get(pid), season) if teams.get(pid) else None
            ctx = load_development_context(
                cur, universe_id=universe['universe_id'], player_id=int(pid), earned_season=season,
                as_of_week=week, player_alias=(plc.pgid_namespace(universe), str(data.get('pgid'))),
                staff_aliases=[('db-coach', str(hc))] if hc is not None else [], mentor_alias=None,
                source_event_ids=[])
            out[pid] = dict(data, development_context=ctx)
        return out

    # -- depth-chart opportunities
    def chart_adjustments(self, cur, league_id, row, *, team_id, season, week, roster, scheme, injuries):
        """The coaching policy's result {adjustments, shadow_adjustments?, reasons} for one CPU chart, computed as
        enabled (the caller decides whether it is used)."""
        from engines import depth_chart_engine as dce
        from engines import personality_coaching as pc
        from personality import core, store
        universe = self._universe_or_fail(cur, league_id)
        config = pc.CoachingConfig.from_dict(dict(_fields(pc.CoachingConfig, row.get('config')),
                                                  version=row.get('version') or '', mode='enabled'))
        hc = self.head_coach(cur, team_id, season)
        view = None
        if hc is not None:
            ident = store.resolve_alias(cur, universe['universe_id'], 'coach', 'db-coach', str(hc))
            view = core.feature_view(None if ident is None else store.read(cur, ident), 'coaching')
        candidates = []
        for p in roster:
            age = p.get('age') or 0
            cohort = 'youth' if age and age <= YOUTH_MAX_AGE else 'veteran' if age >= VETERAN_MIN_AGE else 'other'
            for slot in dce.POSITION_SLOTS:
                score = dce.slot_score(p, slot, scheme)
                if score is None:
                    continue
                candidates.append({'player_id': p['player_id'], 'slot': slot, 'neutral_score': score,
                                   'eligible': True, 'out': dce.is_out(p['player_id'], injuries), 'cohort': cohort,
                                   'cohort_weight': 1.0})
        return pc.opportunity_adjustments(candidates=candidates, coach_view=view, config=config)

    # -- owners
    def owner_views(self, cur, exclude=()):
        """{team_id: owner view} for CPU teams with a reviewed owner profile (engines/owner_context)."""
        from engines import owner_context as oc
        return oc.team_owner_views(cur, exclude=set(exclude) | oc.user_team_ids(cur))

    def owner_vetoes_used(self, cur, season, team_id):
        from engines import owner_context as oc
        return oc.vetoes_used(cur, season, team_id)

    def owner_record(self, cur, **decision):
        from engines import owner_context as oc
        return oc.record_decision(cur, **decision)

    # -- retirement
    def retirement(self, cur, league_id, row, mode, *, player, season, seed):
        """{keep, reason, decision} from the retirement policy for a player Madden retired, or None when the
        policy can't decide (blocked data, no identity)."""
        from engines import retirement_context as rc
        from engines import retirement_policy as rp
        from personality import lifecycle as plc, store
        universe = self._universe_or_fail(cur, league_id)
        cfg = row.get('config') or {}
        if cfg.get('policy') is not None:
            policy = rp.policy_from_dict(cfg['policy'])
        elif mode == 'shadow':
            policy = rp.synthetic_policy()                  # review-only numbers, recorded never used
        else:
            raise lo.StageError('retirement is active without a reviewed policy in its switchboard config')
        uid, pgid = universe['universe_id'], player.get('pgid')
        ident = store.resolve_alias(cur, uid, 'player', plc.pgid_namespace(universe), str(pgid))
        if ident is None:
            return None
        profile = store.read(cur, ident)
        tick = {'season': season, 'tick': 24}
        cur.execute('SELECT id FROM players WHERE madden_pgid = %s', (pgid,))
        found = cur.fetchone()
        if found is None:
            return None
        pid = found[0]
        evidence = {
            'ability': {'version': rc.EVIDENCE_VERSION, 'universe_id': uid, 'source_event_ids': [f'save:{pgid}'],
                        'as_of': tick, 'coverage': 'partial', 'fingerprint': f'ovr:{player.get("overall")}',
                        'data': {'base_overall': player.get('overall')}},
            'profile': {'version': rc.EVIDENCE_VERSION, 'universe_id': uid, 'source_event_ids': [f'profile:{pgid}'],
                        'as_of': tick, 'coverage': 'complete', 'fingerprint': f'rev:{(profile or {}).get("revision")}',
                        'data': rc.profile_view(profile)}}
        snap = {'universe_id': uid, 'person_key': ident.birth_key,
                'identity_status': 'verified', 'player_id': int(pid), 'pgid': int(pgid), 'earned_season': season,
                'decision_season': season, 'age': player.get('age'), 'age_status': 'known' if player.get('age')
                else 'unknown', 'position': player.get('position')}
        ctx = rc.build_context(snap, evidence, feature_mode='effects')
        d = rp.evaluate_annual(ctx, policy, features_enabled=True, production=mode == 'active')
        if d.intent == 'BLOCKED_DATA':
            return None
        keep = d.intent == 'AVAILABLE'
        reason = (f"{'kept' if keep else 'retired'} by the retirement policy {d.policy_version}: "
                  f"p {d.final_p:.3f}, draw {d.draw:.3f}")
        return {'keep': keep, 'reason': reason, 'decision': rp.to_dict(d)}


class PersonalityDeps(lo.Deps):
    """league office Deps that pass personality inputs by switchboard mode (module docstring)."""

    def __init__(self, cur=None, league_id=None, snapshot_modes=None, features=None):
        self.cur = cur
        self.modes_snapshot = deepcopy(snapshot_modes) if snapshot_modes else {
            'league_id': league_id, 'source': 'default', 'features': {}}
        self.league_id = league_id if league_id is not None else self.modes_snapshot.get('league_id')
        self.features = features or LiveFeatures()
        self.receipts = []
        self.run_id = None
        self.stage = {'stage': None, 'season': None, 'week': None, 'wave': None, 'seed': None}
        self._fa_rank = {}
        self._owner_vetoes = {}

    # -- hooks the league office calls
    def bind_run(self, run_id):
        self.run_id = run_id

    def begin_stage(self, stage, season, week, wave, seed):
        self.stage = {'stage': stage, 'season': season, 'week': week, 'wave': wave, 'seed': seed}

    def personality_report(self):
        counts = {}
        for r in self.receipts:
            key = f"{r['feature']}:{r['mode']}:{r['status']}"
            counts[key] = counts.get(key, 0) + 1
        return {'modes': {f: self.mode(f) for f in LEAGUE_OFFICE_FEATURES}, 'counts': counts,
                'receipts': self.receipts}

    # -- modes
    def mode(self, feature):
        return pf.snapshot_mode(self.modes_snapshot, feature)

    def row(self, feature):
        return ((self.modes_snapshot.get('features') or {}).get(feature)) or {}

    def contract_mode(self):
        return max((self.mode(f) for f in CONTRACT_FEATURES), key=ORDER.get)

    def batch_id(self):
        s = self.stage
        return f"{self.run_id if self.run_id is not None else s['seed']}:{s['stage']}:{s['wave'] or 0}"

    def record(self, feature, mode, status, **detail):
        self.receipts.append(dict({'feature': feature, 'mode': mode, 'status': status,
                                   'stage': self.stage['stage'], 'season': self.stage['season']}, **detail))

    def _dual(self, feature, mode, what, baseline, adjusted):
        """off: baseline; shadow: baseline returned, adjusted recorded (errors recorded); active: adjusted."""
        if mode == 'off':
            return baseline()
        if mode == 'shadow':
            base = baseline()
            try:
                adj = adjusted()
            except Exception as e:      # noqa: BLE001 - shadow never fails the write
                self.record(feature, mode, 'error', what=what, error=f'{type(e).__name__}: {e}')
                return base
            self.record(feature, mode, 'recorded', what=what, baseline=base, adjusted=adj)
            return base
        adj = adjusted()
        self.record(feature, mode, 'applied', what=what, adjusted=adj)
        return adj

    # -- contracts
    def _contract_inputs(self, cur, players, stage):
        policy = contract_policy(self.modes_snapshot, counterfactual=self.contract_mode() == 'shadow')
        s = self.stage
        return self.features.contract_inputs(cur, self.league_id, policy, stage=stage, season=s['season'],
                                             week=s['week'], players=players, batch_id=self.batch_id())

    def resign_decisions(self, cur, season, seed):
        from engines import transactions_engine as te
        base = lambda: super(PersonalityDeps, self).resign_decisions(cur, season, seed)     # noqa: E731
        adj = lambda: te.resign_decisions(cur, season, seed,                                 # noqa: E731
                                          contract_inputs=self._contract_inputs(cur, None, lo.RESIGN))
        return self._dual('contracts', self.contract_mode(), 'resign', base, adj)

    def run_wave(self, players, offers, wave, seed):
        from engines import free_agency_engine as fae
        base = lambda: super(PersonalityDeps, self).run_wave(players, offers, wave, seed)    # noqa: E731
        adj = lambda: fae.run_wave(players, offers, wave, seed,                              # noqa: E731
                                   contract_inputs=self._contract_inputs(self.cur, players, lo.FREE_AGENCY))
        return self._dual('contracts', self.contract_mode(), f'free_agency:{wave}', base, adj)

    def udfa_market(self, prospects, needs, seed):
        from engines import free_agency_engine as fae
        base = lambda: super(PersonalityDeps, self).udfa_market(prospects, needs, seed)      # noqa: E731
        adj = lambda: fae.udfa_market(prospects, needs, seed,                                # noqa: E731
                                      contract_inputs=self._contract_inputs(self.cur, prospects, lo.UDFA))
        return self._dual('contracts', self.contract_mode(), 'udfa', base, adj)

    # -- development learning (not run twice: the development policy's own shadow mode records the comparison)
    def progression_weekly(self, cur, season, week, snapshot, source_fingerprint):
        mode = self.mode('development_learning')
        if mode == 'off':
            return super().progression_weekly(cur, season, week, snapshot, source_fingerprint)
        from engines import form_engine as form
        from engines import progression_checkpoint as pc
        from engines.progression_inputs import fetch_inputs
        try:
            config = self.features.development_config(self.row('development_learning'), mode)
            saved = {p['pgid'] for p in snapshot.get('players', []) if p.get('pgid', 0) > 0}
            inputs = {pid: d for pid, d in fetch_inputs(cur, season, week).items() if d.get('pgid') in saved}
            inputs = self.features.development_inputs(cur, self.league_id, inputs, season=season, week=week)
        except Exception as e:      # noqa: BLE001
            if mode == 'active':
                raise
            self.record('development_learning', mode, 'error', what='weekly', error=f'{type(e).__name__}: {e}')
            return super().progression_weekly(cur, season, week, snapshot, source_fingerprint)
        prepared = pc.prepare_pass(cur, snapshot, season, week, source_fingerprint=source_fingerprint,
                                   player_inputs=inputs, forms=form.weekly_form(cur, season, week),
                                   development_config=config)
        self.record('development_learning', mode, 'applied' if mode == 'active' else 'recorded', what='weekly',
                    players=len(inputs), config_version=config.version)
        return prepared

    # -- depth-chart opportunities
    def chart_adjustments(self, cur, team_id, season, week, roster=None, scheme=None, injuries=None):
        mode = self.mode('development_opportunity')
        if mode == 'off' or not roster:
            return None

        def compute():
            res = self.features.chart_adjustments(cur, self.league_id, self.row('development_opportunity'),
                                                  team_id=team_id, season=season, week=week, roster=roster,
                                                  scheme=scheme or {}, injuries=injuries or {})
            return res.get('adjustments') or res.get('shadow_adjustments') or {}

        adj = self._dual('development_opportunity', mode, f'chart:{team_id}', lambda: None, compute)
        return adj or None

    # -- owners (OWNERS_SCOUTS_VOTERS_PLAN §2.3, U5)
    def _owner_views(self, cur):
        try:
            jr = set(self.user_team_ids(cur) or ())
        except Exception:      # noqa: BLE001 - no registry: the owner layer still skips gm_team_settings teams
            jr = set()
        return self.features.owner_views(cur, exclude=jr)

    def cpu_offers(self, cur, season, stage, wave, seed):
        mode = self.mode('owner_spending')
        if mode == 'off':
            return super().cpu_offers(cur, season, stage, wave, seed)
        from engines import owner_policy as op
        from engines import transactions_engine as te
        try:
            spending = {t: op.spending(v) for t, v in self._owner_views(cur).items()}
        except Exception as e:      # noqa: BLE001
            if mode == 'active':
                raise
            self.record('owner_spending', mode, 'error', what=f'free_agency:{wave}', error=f'{type(e).__name__}: {e}')
            return super().cpu_offers(cur, season, stage, wave, seed)

        def counts(offers):
            out = {}
            for o in offers or []:
                out[str(o.get('team_id'))] = out.get(str(o.get('team_id')), 0) + 1
            return out

        if mode == 'active':
            offers = te.cpu_offers(cur, season, stage, wave, seed, spending=spending)
            self.record('owner_spending', mode, 'applied', what=f'free_agency:{wave}', teams=len(spending),
                        offers=counts(offers))
            return offers
        base = super().cpu_offers(cur, season, stage, wave, seed)
        try:
            league = te.fetch_league(cur, season, te.target_season(season, stage=stage))
            adj = te.plan_offers(league, stage, wave, seed, spending=spending)
            self.record('owner_spending', mode, 'recorded', what=f'free_agency:{wave}', teams=len(spending),
                        baseline=counts(base), adjusted=counts(adj))
        except Exception as e:      # noqa: BLE001 - shadow never fails the write
            self.record('owner_spending', mode, 'error', what=f'free_agency:{wave}', error=f'{type(e).__name__}: {e}')
        return base

    def free_agent_pool(self, cur, season, target):
        pool = super().free_agent_pool(cur, season, target)
        ranked = sorted(pool or [], key=lambda p: (-(p.get('ovr') or 0), p.get('id') or 0))
        self._fa_rank = {p.get('id'): n for n, p in enumerate(ranked, 1) if p.get('id') is not None}
        return pool

    def signings_from_decisions(self, decisions, offers):
        moves = super().signings_from_decisions(decisions, offers)
        mode = self.mode('owner_meddling')
        if mode == 'off' or not moves:
            return moves
        from engines import owner_policy as op
        cur, s = self.cur, self.stage
        season = s['season']
        week = s['week']
        tick = week if isinstance(week, int) and 1 <= week <= 32 else OFFSEASON_TICK
        try:
            views = self._owner_views(cur)
        except Exception as e:      # noqa: BLE001
            if mode == 'active':
                raise
            self.record('owner_meddling', mode, 'error', what='signings', error=f'{type(e).__name__}: {e}')
            return moves
        kept, reviewed = [], []
        for m in moves:
            view = views.get(m.get('team_id'))
            move = {'kind': 'SIGNING', 'team_id': m.get('team_id'), 'ref': m.get('player_id'),
                    'fa_rank': self._fa_rank.get(m.get('player_id')), 'user_controlled': view is None}
            if view is None or not op.is_big_move(move):
                kept.append(m)
                continue
            key = (season, m.get('team_id'))
            if key not in self._owner_vetoes:
                self._owner_vetoes[key] = int(self.features.owner_vetoes_used(cur, season, m.get('team_id')) or 0)
            decision = op.meddle_choice(move, view, season=season, tick=tick, vetoes_used=self._owner_vetoes[key])
            reviewed.append({'team_id': m.get('team_id'), 'player_id': m.get('player_id'),
                             'fa_rank': move['fa_rank'], 'decision': decision})
            if decision == 'VETO' and mode == 'active':
                self._owner_vetoes[key] += 1
                self.features.owner_record(cur, season=season, tick=tick, team_id=m.get('team_id'),
                                           move_kind='SIGNING', move_ref={'player_id': m.get('player_id'),
                                                                          'offer_id': m.get('offer_id')},
                                           decision='VETO')
                continue
            kept.append(m)
        if reviewed:
            self.record('owner_meddling', mode, 'applied' if mode == 'active' else 'recorded', what='signings',
                        decisions=reviewed)
        return kept if mode == 'active' else moves

    # -- retirement
    def retirement_decision(self, cur, player, season, seed):
        if player.get('status') != 'Retired':
            return True, 'not retired by Madden: the retirement policy never retires an active player'
        mode = self.mode('retirement')
        base = lambda: super(PersonalityDeps, self).retirement_decision(cur, player, season, seed)   # noqa: E731
        if mode == 'off':
            return base()

        def adjusted():
            r = self.features.retirement(cur, self.league_id, self.row('retirement'), mode, player=player,
                                         season=season, seed=seed)
            if r is None:                     # blocked data / unknown identity: today's rule decides
                keep, reason = base()
                return {'keep': keep, 'reason': f'{reason} (retirement policy blocked)', 'blocked': True}
            return r

        out = self._dual('retirement', mode, f"player:{player.get('pgid')}", base, adjusted)
        if isinstance(out, tuple):
            return out
        return bool(out['keep']), out['reason']


def deps_for(cur, snapshot):
    """PersonalityDeps when any feature beyond producers is not off, else a plain Deps; both keep the snapshot."""
    if snapshot and any(pf.snapshot_mode(snapshot, f) != 'off' for f in pf.FEATURES if f != 'producers'):
        return PersonalityDeps(cur, snapshot.get('league_id'), snapshot)
    deps = lo.Deps()
    deps.modes_snapshot = deepcopy(snapshot) if snapshot else None
    return deps


def default_deps(cur):
    """deps_for the switchboard snapshot of league_state's league (plain Deps without a league or tables)."""
    cur.execute('SELECT madden_league_id FROM league_state WHERE id = 1')
    row = cur.fetchone()
    if not row or row[0] is None:
        return deps_for(cur, None)
    return deps_for(cur, pf.snapshot(cur, int(row[0])))
