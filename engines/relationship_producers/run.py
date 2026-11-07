"""
Relationship producer runner (RELATIONSHIP_EVENT_PRODUCERS_PLAN.md §2.4-§2.5).

  build_events(inputs, rules=None) -> [envelopes]      pure: sources.read_tick's dict (or a preview fixture) in
  rule_counts(envelopes) -> {rule: n}
  run_tick(cur, season, tick, *, source='SAVE', ...) -> run dict    read, hash, NOOP / build / correct, append or
                                                                     hold, flush, record; one savepoint
  record_failure(cur, league_id, season, tick, source, error)       a FAILED run row (never over a good run)
  catch_up(cur, *, through, dry_run=False) -> report                 CATCHUP runs from the first save (D3)
  flush(cur) -> flush_pending's report

The caller owns the transaction: nothing here connects or commits. store.py (psycopg) and sources.py are imported
inside the database functions, so build_events and the preview need no database driver.

Every envelope gets `facet_key = rules.facet_key(env)` (the key store.py and the pending queue use).

Event timing (deterministic, so a rerun reproduces it and repeats are store duplicates, never conflicts):
  tenure_window, role_window, moves, recognitions   at the run's tick
  injury start / heal, streaks                       at their game week, when that week is in the run's window
  contract year                                      (season, 1), when tick 1 is in the window
  season outcome                                     (season, 23), when tick 23 is in the window and the Super Bowl
                                                     is played
  awards                                             (season, 23), on every run from tick 23 on
  hot seat kept                                      (season, 19) BLACK_MONDAY / (season, 23) PLAYOFF_EXIT
  staff moves                                        LEFT at its hot-seat phase tick, HIRED at (season, 24)
"""
from contextlib import contextmanager
import hashlib
import json
import uuid

from engines.relationship_producers import PRODUCER_RULES_VERSION
from engines.relationship_producers import calendar as cal
from engines.relationship_producers import rules as default_rules

SOURCES = ('SAVE', 'CATCHUP')
AWARD_TICK = cal.LAST_GAME_TICK               # awards land after the Super Bowl
OUTCOME_TICK = cal.LAST_GAME_TICK
HIRE_TICK = cal.FIRST_OFFSEASON_TICK           # the carousel hires after the playoffs
PHASE_TICKS = {'BLACK_MONDAY': 19, 'PLAYOFF_EXIT': cal.LAST_GAME_TICK}
PLAYOFF_ROUNDS = {'WILD_CARD': 1, 'DIVISIONAL': 2, 'CONFERENCE': 3, 'SUPER_BOWL': 4}


class ProducerError(RuntimeError):
    code = 2


class NotMigratedError(ProducerError):
    code = 3


class CatchUpRefused(ProducerError):
    code = 4


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False, default=str)


def input_hash(value):
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _k(value):
    return str(int(value))


# ---------------------------------------------------------------- pure: history and observations

def _scheme(inputs, season, team):
    return ((inputs.get('schemes') or {}).get(_k(season)) or {}).get(_k(team)) or {}


def observe(inputs, R):
    """(history {pid: [{season, tick, team_id, tier, eligible}]} before this tick, this tick's obs {pid: obs})."""
    season, tick = inputs['season'], inputs['tick']
    previous = ((inputs.get('previous') or {}).get('roster')) or {}
    last_team, history, current = {}, {}, {}
    for week in sorted(inputs.get('weeks') or [], key=lambda w: (w['season'], w['tick'])):
        s, t = week['season'], week['tick']
        byes = {int(b) for b in week.get('byes') or []}
        for pid, team in sorted(week.get('teams', {}).items(), key=lambda kv: int(kv[0])):
            if team is None:
                continue
            chart = (week.get('charts') or {}).get(_k(team))
            tier = R.role_tier(chart, _scheme(inputs, s, team), int(pid)) if chart else None
            moved = pid in last_team and last_team[pid] != team
            if (s, t) == (season, tick) and pid in previous and previous[pid] not in (None, team):
                moved = True
            obs = {'player_id': int(pid), 'team_id': int(team), 'season': s, 'tick': t, 'tier': tier,
                   'has_chart': bool(chart), 'bye': int(team) in byes, 'first_week_on_team': moved,
                   'injury': (week.get('injured') or {}).get(pid)}
            if (s, t) == (season, tick):
                current[pid] = obs
            else:
                ok, _ = R.eligible(obs)
                history.setdefault(pid, []).append({'season': s, 'tick': t, 'team_id': int(team), 'tier': tier,
                                                    'eligible': ok})
            last_team[pid] = team
    return history, current


def _rating_ranks(inputs, R):
    by_team = {}
    for pid, team in (inputs.get('roster') or {}).items():
        if team is None:
            continue
        info = (inputs.get('players') or {}).get(pid) or {}
        by_team.setdefault(team, []).append({'player_id': int(pid), 'position': info.get('position'),
                                             'overall': info.get('overall')})
    return {pid: R.rating_rank_at_position(by_team[team], int(pid))
            for pid, team in (inputs.get('roster') or {}).items() if team is not None}


def _origin(inputs, pid):
    return ((inputs.get('players') or {}).get(_k(pid)) or {}).get('origin') if pid is not None else None


def _team_players(inputs, team):
    return [{'player_id': int(pid), 'origin': _origin(inputs, pid)}
            for pid, t in (inputs.get('roster') or {}).items() if t is not None and int(t) == int(team)]


def _moves(inputs, R, source):
    prev = inputs.get('previous')
    if not prev:
        return [], {}
    season, tick = inputs['season'], inputs['tick']
    before, now = prev.get('roster') or {}, inputs.get('roster') or {}
    pids = set(now) | (set(before) if source == 'SAVE' else set())
    events, flags = [], {}
    for pid in sorted(pids, key=int):
        was = before.get(pid) if pid in before else None
        team = now.get(pid) if pid in now else None
        if was == team:
            continue
        prev_side = None if pid not in before else {'player_id': int(pid), 'team_id': was, 'season': prev['season'],
                                                    'tick': prev['tick'], 'origin': _origin(inputs, pid)}
        curr_side = {'player_id': int(pid), 'team_id': team, 'season': season, 'tick': tick,
                     'origin': _origin(inputs, pid)}
        out = R.move_events(prev_side, curr_side, (inputs.get('causes') or {}).get(pid), source=source)
        events.extend(out['events'])
        if out['flags']:
            flags[pid] = out['flags']
    return events, flags


def _injury_events(inputs, R, source):
    season, window = inputs['season'], {tuple(w) for w in inputs.get('window') or []}
    observed = {w['tick'] for w in inputs.get('weeks') or [] if w['season'] == season}
    rows = {}
    for row in inputs.get('injuries') or []:
        if row['season'] == season:
            rows.setdefault(row['player_id'], {})[row['tick']] = row
    out = []
    for pid, by_tick in sorted(rows.items()):
        qual = {t: r for t, r in by_tick.items() if (r.get('weeks_remaining') or 0) > 0 or r.get('is_ir')}
        start = None
        for t in sorted(qual):
            if t - 1 not in qual:
                start = qual[t]
                if (season, t) in window:
                    out.append(R.injury_situation(dict(start, origin=_origin(inputs, pid)), source=source))
            healed = t + 1
            if healed not in qual and healed in observed and (season, healed) in window and start is not None:
                out.append(R.injury_resolved(dict(start, origin=_origin(inputs, pid)), season, healed, source=source))
    return out


def _team_results(inputs, R, source):
    season, window = inputs['season'], {tuple(w) for w in inputs.get('window') or []}
    games = inputs.get('games') or {}
    out = []
    for team in sorted(games, key=int):
        played = sorted(games[team], key=lambda g: g['tick'])
        players = _team_players(inputs, team)
        results = [{'tick': g['tick'], 'result': g['result']} for g in played]
        for i, g in enumerate(played):
            if (season, g['tick']) in window and g['tick'] <= cal.LAST_GAME_TICK:
                nxt = played[i + 1]['tick'] if i + 1 < len(played) else None
                out.extend(R.streak_events(int(team), season, g['tick'], results, players, next_game_tick=nxt,
                                           source=source))
    super_bowl = any(g.get('game_type') == 'SUPER_BOWL' for gs in games.values() for g in gs)
    if (season, OUTCOME_TICK) in window and super_bowl:
        for team in sorted(games, key=int):
            playoff = [g for g in sorted(games[team], key=lambda g: g['tick']) if g.get('game_type') in PLAYOFF_ROUNDS]
            if not playoff:
                outcome = {'kind': 'MISSED_PLAYOFFS'}
            elif playoff[-1]['game_type'] == 'SUPER_BOWL' and playoff[-1]['result'] == 'W':
                outcome = {'kind': 'WON_SUPER_BOWL'}
            else:
                outcome = {'kind': 'LOST_ROUND', 'round': PLAYOFF_ROUNDS[playoff[-1]['game_type']]}
            out.extend(R.season_outcome_events(int(team), season, OUTCOME_TICK, outcome,
                                               _team_players(inputs, team), source=source))
    return out


def _coach_events(inputs, R, source):
    season, tick = inputs['season'], inputs['tick']
    hot = {(r['team_id'], r['role']): r for r in inputs.get('hot_seat') or []}
    out = []
    for move in inputs.get('staff_moves') or []:
        seat = hot.get((move['team_id'], move['role']))
        if move.get('action') == 'LEFT':
            at = PHASE_TICKS.get((seat or {}).get('phase'), HIRE_TICK)
        else:
            at = HIRE_TICK
        if tick >= at:
            out.extend(R.staff_move_events(dict(move, season=season, tick=at, fired=bool(seat and seat['fired'])),
                                           source=source))
    for row in inputs.get('hot_seat') or []:
        at = PHASE_TICKS.get(row.get('phase'), cal.LAST_GAME_TICK)
        if tick >= at:
            out.append(R.hot_seat_pressure(dict(row, season=season, tick=at), source=source))
    out.extend(_tree_events(inputs, R, source))
    return out


def _tree_events(inputs, R, source):
    """coaching_tree (COACHING_TREES §2.6): from TREE_TICK on, every simulated edge that has crossed by this season,
    at its own (crossed_season, TREE_TICK). Each edge is read as of its crossing season (seasons and outcome frozen
    there), so a later tick rebuilds the identical facets and the store's dedupe makes repeats free."""
    season, tick = inputs['season'], inputs['tick']
    staff = inputs.get('tree_staff') or []
    if tick < R.TREE_TICK or not staff:
        return []
    from personality import coaching_trees as ct
    pairs = frozenset(tuple(p) for p in inputs.get('tree_reviewed_pairs') or [])
    out = []
    for s in range(ct.FIRST_SEASON, season + 1):
        for edge in ct.sim_edges(staff, through=s, reviewed_pairs=pairs):
            if edge['crossed_season'] == s:
                out.extend(R.coaching_tree_events(edge, source=source))
    return out


def build_events(inputs, rules=None):
    """Every producer envelope for one tick's inputs, each with facet_key set, in a stable order."""
    R = rules or default_rules
    season, tick, source = inputs['season'], inputs['tick'], inputs.get('source', 'SAVE')
    if source not in SOURCES:
        raise ProducerError(f'source must be one of {SOURCES}')
    cal.time_point(season, tick)
    window = {tuple(w) for w in inputs.get('window') or [[season, tick]]}
    coverage = 'complete' if source == 'SAVE' else 'partial'
    history, current = observe(inputs, R)
    ranks = _rating_ranks(inputs, R)
    events, flags = _moves(inputs, R, source)

    for pid, team in sorted((inputs.get('roster') or {}).items(), key=lambda kv: int(kv[0])):
        if team is None:
            continue
        obs = dict(current.get(pid) or {'player_id': int(pid), 'team_id': int(team), 'season': season, 'tick': tick,
                                        'tier': None, 'has_chart': False, 'bye': False,
                                        'first_week_on_team': False, 'injury': None},
                   origin=_origin(inputs, pid), rating_rank=ranks.get(pid))
        events.append(R.tenure_window(obs, coverage=coverage, source=source, flags=flags.get(pid, ())))
        if cal.is_in_season(tick):
            events.append(R.role_window(obs, history.get(pid, []), source=source))

    for c in inputs.get('recognitions') or []:
        events.append(R.recognition(dict(c, origin=_origin(inputs, c['player_id'])), source=source))
    if (season, 1) in window:
        for c in inputs.get('contract_years') or []:
            events.append(R.contract_year(dict(c, origin=_origin(inputs, c['player_id'])), 1, source=source))
    events.extend(_injury_events(inputs, R, source))
    if tick >= AWARD_TICK:
        for a in inputs.get('awards') or []:
            events.append(R.award_situation(dict(a, season=season, tick=AWARD_TICK,
                                                 origin=_origin(inputs, a.get('player_id'))), source=source))
    events.extend(_team_results(inputs, R, source))
    events.extend(_coach_events(inputs, R, source))

    out, seen = [], set()
    for env in events:
        if env is None:
            continue
        env['facet_key'] = R.facet_key(env)
        key = (env['source_event_id'], env['facet_key'])
        if key in seen:
            continue
        seen.add(key)
        out.append(env)
    out.sort(key=lambda e: (e['occurred_at']['season'], e['occurred_at']['tick'], e['source_event_id'],
                            e['facet_key']))
    return out


def rule_counts(envelopes):
    counts = {}
    for env in envelopes:
        rule = (env.get('payload') or {}).get('rule') or env['kind']
        counts[rule] = counts.get(rule, 0) + 1
    return dict(sorted(counts.items()))


def preview(fixture, rules=None):
    """Offline: fixture {"ticks": [inputs, ...]} -> {"ticks": [{season, tick, counts, envelopes}], "counts"}."""
    if not isinstance(fixture, dict) or not isinstance(fixture.get('ticks'), list):
        raise ProducerError('fixture must be an object with a "ticks" list')
    ticks, total = [], {}
    for inputs in fixture['ticks']:
        if not isinstance(inputs, dict) or 'season' not in inputs or 'tick' not in inputs:
            raise ProducerError('every fixture tick needs season and tick')
        try:
            envs = build_events(inputs, rules)
        except (KeyError, TypeError, ValueError) as e:
            raise ProducerError(f'bad fixture tick {inputs.get("season")}:{inputs.get("tick")}: {e}') from e
        counts = rule_counts(envs)
        for rule, n in counts.items():
            total[rule] = total.get(rule, 0) + n
        ticks.append({'season': inputs['season'], 'tick': inputs['tick'], 'counts': counts, 'envelopes': envs})
    return {'rules_version': PRODUCER_RULES_VERSION, 'ticks': ticks, 'counts': dict(sorted(total.items()))}


# ---------------------------------------------------------------- database

@contextmanager
def _savepoint(cur, rollback=False):
    name = 'relprod_run_' + uuid.uuid4().hex
    cur.execute(f'SAVEPOINT {name}')
    try:
        yield
    except BaseException:
        cur.execute(f'ROLLBACK TO SAVEPOINT {name}')
        cur.execute(f'RELEASE SAVEPOINT {name}')
        raise
    else:
        if rollback:
            cur.execute(f'ROLLBACK TO SAVEPOINT {name}')
        cur.execute(f'RELEASE SAVEPOINT {name}')


def _ready(cur, sources):
    ready = sources.tables_ready(cur)
    missing = [name for name in ('producers', 'relationships') if not ready[name]]
    if missing:
        raise NotMigratedError(f'not migrated: {", ".join(missing)} tables are missing')
    return ready


def run_tick(cur, season, tick, *, source='SAVE', rules=None, league_id=None, roster=None, roster_season=None):
    """One producer run for (season, tick) inside the caller's transaction (plan §2.4). Returns the run dict
    (status NOOP when the same inputs already ran)."""
    from engines.relationship_producers import sources, store
    R = rules or default_rules
    if source not in SOURCES:
        raise ProducerError(f'source must be one of {SOURCES}')
    ready = _ready(cur, sources)
    league = int(league_id) if league_id is not None else sources.league_id(cur)
    prior = store.read_run(cur, league, season, tick, PRODUCER_RULES_VERSION)
    if source == 'CATCHUP' and prior is not None and prior['source'] == 'SAVE':
        raise CatchUpRefused(f'{season}:{tick} already has a SAVE run; the catch-up never rewrites forward data')
    inputs = sources.read_tick(cur, league, season, tick, source=source, roster=roster, roster_season=roster_season,
                               rules_version=PRODUCER_RULES_VERSION)
    registry = sources.team_registry(cur)
    digest, reg_digest = input_hash(inputs), input_hash(registry)
    good_prior = prior is not None and prior['status'] != 'FAILED'
    if good_prior and prior['input_hash'] == digest and prior['registry_hash'] == reg_digest:
        return dict(prior, status='NOOP', previous_status=prior['status'])

    envelopes = build_events(inputs, R)
    batch, fixes = envelopes, []
    if good_prior and prior.get('event_hashes'):
        fixes = store.corrections(prior['event_hashes'], envelopes)
        changed = {f"{f['payload']['original_source_event_id']}|{f['supersedes_facet_key']}"
                   for f in fixes if f['payload']['replacement_event'] is not None}
        batch = [e for e in envelopes if store._event_key(e) not in changed] + fixes
    universe = sources.universe_id(cur, league, ready=ready)
    with _savepoint(cur):
        observed = store.save_observations(cur, league, season, tick,
                                           [{'player_id': int(p), 'team_id': t}
                                            for p, t in sorted(inputs['roster'].items(), key=lambda kv: int(kv[0]))],
                                           source)
        result = store.append_or_hold(cur, batch, league_id=league, universe_id=universe, team_registry=registry)
        flushed = store.flush_pending(cur, league, universe, registry)
        counts = {'rules': rule_counts(envelopes), 'events': len(batch), 'corrections': len(fixes),
                  'observations': observed, 'appended': result['appended'], 'duplicates': result['duplicates'],
                  'held': result['held'], 'newly_held': result['newly_held'], 'rejected': result['rejected'],
                  'dropped': result['dropped'], 'hold_reasons': result['hold_reasons'],
                  'flushed': flushed['appended'], 'watermarks': inputs.get('watermarks') or {}}
        status = ('APPENDED' if result['appended'] or flushed['appended'] else 'HELD' if result['held'] else 'NOOP')
        run = {'league_id': league, 'season': season, 'tick': tick, 'rules_version': PRODUCER_RULES_VERSION,
               'source': source, 'input_hash': digest, 'registry_hash': reg_digest,
               'event_hashes': result['event_hashes'], 'counts': counts, 'status': status, 'error': None}
        store.record_run(cur, run)
    return dict(run, universe_id=universe)


def record_failure(cur, league_id, season, tick, source, error):
    """A FAILED run row, unless a good run already holds this key (its event_hashes drive later corrections)."""
    from engines.relationship_producers import store
    prior = store.read_run(cur, league_id, season, tick, PRODUCER_RULES_VERSION)
    if prior is not None and prior['status'] != 'FAILED':
        return False
    store.record_run(cur, {'league_id': league_id, 'season': season, 'tick': tick,
                           'rules_version': PRODUCER_RULES_VERSION, 'source': source, 'input_hash': '',
                           'registry_hash': '', 'event_hashes': {}, 'counts': {}, 'status': 'FAILED',
                           'error': str(error)[:2000]})
    return True


def catch_up(cur, *, through, dry_run=False, rules=None, league_id=None):
    """Replay every in-season tick with MADDEN charts from the first save to `through` as CATCHUP runs (D3).
    Refuses when any SAVE run sits at or before `through`. dry_run rolls everything back and reports counts."""
    from engines.relationship_producers import sources
    _ready(cur, sources)
    through = (int(through[0]), int(through[1]))
    cal.time_point(*through)
    league = int(league_id) if league_id is not None else sources.league_id(cur)
    saves = sources.save_runs_through(cur, league, through, PRODUCER_RULES_VERSION)
    if saves:
        raise CatchUpRefused(f'SAVE runs exist at or before {through[0]}:{through[1]} '
                             f'(first {saves[0][0]}:{saves[0][1]}); run the catch-up over an earlier range')
    ticks = sources.catch_up_ticks(cur, through)
    report = {'through': list(through), 'dry_run': bool(dry_run), 'ticks': len(ticks), 'statuses': {},
              'counts': {}, 'rules': {}}
    with _savepoint(cur, rollback=dry_run):
        for season, tick in ticks:
            run = run_tick(cur, season, tick, source='CATCHUP', rules=rules, league_id=league)
            report['statuses'][run['status']] = report['statuses'].get(run['status'], 0) + 1
            for key in ('events', 'appended', 'held', 'rejected', 'corrections'):
                report['counts'][key] = report['counts'].get(key, 0) + int((run.get('counts') or {}).get(key) or 0)
            for rule, n in ((run.get('counts') or {}).get('rules') or {}).items():
                report['rules'][rule] = report['rules'].get(rule, 0) + n
    report['rules'] = dict(sorted(report['rules'].items()))
    return report


def flush(cur, league_id=None):
    """Append held events whose subject now resolves (store.flush_pending) for this database's league."""
    from engines.relationship_producers import sources, store
    ready = _ready(cur, sources)
    league = int(league_id) if league_id is not None else sources.league_id(cur)
    universe = sources.universe_id(cur, league, ready=ready)
    with _savepoint(cur):
        return dict(store.flush_pending(cur, league, universe, sources.team_registry(cur)), universe_id=universe)
