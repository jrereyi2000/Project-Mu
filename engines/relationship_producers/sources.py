"""
Relationship producer sources (RELATIONSHIP_EVENT_PRODUCERS_PLAN.md §2.1, §2.4, §2.5): read one tick's inputs.

`read_tick(cur, league_id, season, tick, ...)` returns a plain-JSON dict (string keys, lists, ints) that
run.build_events turns into envelopes. Everything run.py needs is in it, so the dict's hash is the run's
input_hash and the offline preview feeds the same shape from a fixture. Nothing here writes, commits or connects.

Inputs dict (run.build_events documents the same keys):
  league_id, season, tick, source ('SAVE'|'CATCHUP')
  window      [[season, tick], ...] the ticks after the previous producer run up to this one (window events:
              streaks, season outcomes, injury starts and heals, contract years); just this tick on a first run
  previous    {season, tick, roster: {pid: team_id|None}} the last roster observation before this tick, or None
  roster      {pid: team_id|None} this tick (None: free agent). SAVE: the snapshot's roster when the watcher
              passes it, else player_seasons; CATCHUP: MADDEN chart membership, then the week's injuries rows
  players     {pid: {origin, position, overall}}
  weeks       [{season, tick, teams: {pid: team}, charts: {team: {slot: [pid]}}, byes: [team],
                injured: {pid: {injury_id, weeks_remaining, is_ir, injury_type}}}]
              in-season weeks of the season before and this season up to this tick (this tick last when it is
              in season), for role history; a week's membership is its roster observation, else its charts
  schemes     {season: {team: {offensive_scheme, defensive_scheme}}} the head coach's (coach_tenures)
  causes      {pid: cause} for players whose team changed (rules.move_events' cause shape)
  injuries    [{injury_id, player_id, team_id, season, tick, injury_type, weeks_remaining, is_ir}] this season
  games       {team: [{tick, result 'W'|'L'|'T', game_type}]} completed games this season, oldest first
  recognitions   [contract dicts] new ACTIVE contracts since the last run replacing one (SAVE only)
  contract_years [contract dicts] ACTIVE, years_remaining 1, this season (read when tick 1 is in the window)
  awards, staff_moves, hot_seat   this season's rows (see rules.py input shapes)
  watermarks  {player_contracts: max id read} (the next run's recognition cut-off)

Causes, in order (plan §2.2): an applied `transactions` row in the window; a WRITTEN league_office_runs edit
(release / sign / trade pgids, spike 2026-11-03) at or after the previous observation, flagged jr_move when a
WRITTEN franchise_pending_moves row of that run names the player; a player_season_transitions DRAFTED / MOVED by
TRADE row at a season edge; an expiring contract (years_remaining <= 1 on the team he left). Anything else is
unknown (tenure facts with flag cause_unknown, never a treatment).
"""
import json

from engines.relationship_producers import calendar as cal

LAST_GAME_TICK = cal.LAST_GAME_TICK
PLAYOFF_ROUNDS = {'WILD_CARD': 1, 'DIVISIONAL': 2, 'CONFERENCE': 3, 'SUPER_BOWL': 4}
TXN_CAUSES = {'RELEASED': 'RELEASE', 'TRADED': 'TRADE', 'SIGNED': 'SIGN'}
EDIT_CAUSES = {'release': 'RELEASE', 'sign': 'SIGN', 'trade': 'TRADE'}
PENDING_CAUSES = {'RELEASE': 'RELEASE', 'SIGN': 'SIGN', 'TRADE': 'TRADE'}
MAX_WINDOW = 2 * cal.TICKS_PER_SEASON


class SourceError(RuntimeError):
    pass


# ---------------------------------------------------------------- plumbing

def _rows(cur, sql, params=()):
    cur.execute(sql, params)
    return [tuple(r) if not isinstance(r, dict) else tuple(r.values()) for r in cur.fetchall()]


def _one(cur, sql, params=()):
    cur.execute(sql, params)
    row = cur.fetchone()
    if row is None:
        return None
    return tuple(row.values()) if isinstance(row, dict) else tuple(row)


def _json(value):
    return json.loads(value) if isinstance(value, str) else (value or {})


def _key(value):
    return str(int(value))


def _point(season, tick):
    return (int(season), int(tick))


# ---------------------------------------------------------------- league, registry, readiness

def league_id(cur):
    row = _one(cur, '/* q:league */ SELECT madden_league_id FROM league_state WHERE id = 1')
    if row is None or row[0] is None:
        raise SourceError('league_state.madden_league_id is not set (load a save first)')
    return int(row[0])


def tables_ready(cur):
    """{'producers': bool, 'relationships': bool, 'lifecycle': bool} from to_regclass."""
    row = _one(cur, '''/* q:ready */ SELECT to_regclass('relationship_producer_runs') IS NOT NULL,
                              to_regclass('persona_relationship_events') IS NOT NULL,
                              to_regclass('persona_lifecycle_universes') IS NOT NULL''')
    return {'producers': bool(row[0]), 'relationships': bool(row[1]), 'lifecycle': bool(row[2])}


def universe_id(cur, league, *, ready=None):
    """The lifecycle universe registered for this league, or None (events are then held)."""
    ready = ready if ready is not None else tables_ready(cur)
    if not ready['lifecycle']:
        return None
    row = _one(cur, '/* q:universe */ SELECT universe_id FROM persona_lifecycle_universes WHERE league_id = %s',
               (str(league),))
    return None if row is None else str(row[0])


def team_registry(cur):
    """{team_key: {team_id, abbreviation}}: the trusted registry append_events needs (plan §2.1)."""
    return {f'team:{tid}': {'team_id': int(tid), 'abbreviation': abbr}
            for tid, abbr in _rows(cur, '/* q:teams */ SELECT id, abbreviation FROM teams ORDER BY id')}


def roster_from_snapshot(cur, snapshot):
    """{player_id: team_id|None} from a reader snapshot, the way load_save.load_players places players."""
    from pipeline import load_save as ls
    teams = ls.team_ids(cur, snapshot)
    pgids = [p['pgid'] for p in snapshot.get('players') or [] if p.get('status') in ls.LOADED_STATUSES]
    by_pgid = dict(_rows(cur, '/* q:pgids */ SELECT madden_pgid, id FROM players WHERE madden_pgid = ANY(%s)',
                         (pgids,)))
    out = {}
    for p in snapshot.get('players') or []:
        if p.get('status') not in ls.LOADED_STATUSES or p['pgid'] not in by_pgid:
            continue
        team = teams.get(p.get('team_index')) if p['status'] != 'FreeAgent' else None
        out[int(by_pgid[p['pgid']])] = team
    return out


# ---------------------------------------------------------------- previous observation and run

def previous_observation(cur, league, season, tick):
    row = _one(cur, '''/* q:prev_obs */ SELECT season, tick FROM relationship_roster_observations
                       WHERE league_id = %s AND (season < %s OR (season = %s AND tick < %s))
                       ORDER BY season DESC, tick DESC LIMIT 1''', (league, season, season, tick))
    if row is None:
        return None
    s, t = int(row[0]), int(row[1])
    roster = {_key(p): (None if team is None else int(team)) for p, team in _rows(
        cur, '''/* q:obs */ SELECT player_id, team_id FROM relationship_roster_observations
                WHERE league_id = %s AND season = %s AND tick = %s ORDER BY player_id''', (league, s, t))}
    return {'season': s, 'tick': t, 'roster': roster}


def previous_run(cur, league, season, tick, rules_version):
    row = _one(cur, '''/* q:prev_run */ SELECT season, tick, counts FROM relationship_producer_runs
                       WHERE league_id = %s AND rules_version = %s AND status <> 'FAILED'
                         AND (season < %s OR (season = %s AND tick < %s))
                       ORDER BY season DESC, tick DESC LIMIT 1''', (league, rules_version, season, season, tick))
    if row is None:
        return None
    return {'season': int(row[0]), 'tick': int(row[1]), 'counts': _json(row[2])}


def window(prev_point, season, tick):
    """[[season, tick]] after prev_point up to (season, tick); just (season, tick) without one."""
    out = [[season, tick]]
    if prev_point is None or _point(*prev_point) >= _point(season, tick):
        return out
    out = []
    point = cal.advance(prev_point[0], prev_point[1], 1)
    while _point(point['season'], point['tick']) <= _point(season, tick) and len(out) < MAX_WINDOW:
        out.append([point['season'], point['tick']])
        point = cal.advance(point['season'], point['tick'], 1)
    if out[-1] != [season, tick]:
        out = out[-(MAX_WINDOW - 1):] + [[season, tick]]
    return out


# ---------------------------------------------------------------- weekly facts

def _charts(cur, seasons):
    out = {}
    for s, w, team, slot, depth, pid in _rows(
            cur, '''/* q:charts */ SELECT season, week, team_id, position_slot, depth, player_id FROM depth_charts
                    WHERE source = 'MADDEN' AND season = ANY(%s) AND week <= %s AND player_id IS NOT NULL
                    ORDER BY season, week, team_id, position_slot, depth''', (list(seasons), LAST_GAME_TICK)):
        out.setdefault((int(s), int(w)), {}).setdefault(_key(team), {}).setdefault(slot, []).append(int(pid))
    return out


def _injuries(cur, seasons):
    rows = []
    for iid, s, w, team, pid, itype, weeks, is_ir in _rows(
            cur, '''/* q:injuries */ SELECT id, season, week, team_id, player_id, injury_type, weeks_remaining, is_ir
                    FROM injuries WHERE season = ANY(%s) AND week <= %s AND player_id IS NOT NULL
                    ORDER BY season, week, id''', (list(seasons), LAST_GAME_TICK)):
        rows.append({'injury_id': int(iid), 'player_id': int(pid), 'team_id': int(team), 'season': int(s),
                     'tick': int(w), 'injury_type': itype, 'weeks_remaining': int(weeks or 0),
                     'is_ir': bool(is_ir)})
    return rows


def _games(cur, seasons):
    byes, games = {}, {}
    for s, w, home, away, hs, as_, status, gtype in _rows(
            cur, '''/* q:games */ SELECT season, week, home_team_id, away_team_id, home_score, away_score, status,
                           game_type FROM games WHERE season = ANY(%s) ORDER BY season, week, id''', (list(seasons),)):
        s, w = int(s), int(w)
        if 'BYE' in (str(status or '').upper(), str(gtype or '').upper()):
            for team in (home, away):
                if team is not None:
                    byes.setdefault((s, w), set()).add(int(team))
            continue
        if str(status or '').upper() != 'COMPLETED' or home is None or away is None:
            continue
        for team, mine, theirs in ((home, hs, as_), (away, as_, hs)):
            result = 'W' if (mine or 0) > (theirs or 0) else 'L' if (mine or 0) < (theirs or 0) else 'T'
            games.setdefault(s, {}).setdefault(_key(team), []).append(
                {'tick': w, 'result': result, 'game_type': gtype or 'REGULAR_SEASON'})
    return byes, games


def _schemes(cur, seasons):
    out = {}
    rows = _rows(cur, '''/* q:schemes */ SELECT ct.team_id, c.offensive_scheme, c.defensive_scheme, ct.start_season,
                                ct.end_season FROM coach_tenures ct JOIN coaches c ON c.id = ct.coach_id
                         WHERE ct.start_season <= %s AND (ct.end_season IS NULL OR ct.end_season >= %s)
                         ORDER BY ct.start_season, ct.id''', (max(seasons), min(seasons)))
    for s in seasons:
        for team, off, de, start, end in rows:
            if start <= s and (end is None or end >= s):
                out.setdefault(_key(s), {})[_key(team)] = {'offensive_scheme': off, 'defensive_scheme': de}
    return out


def _observed_teams(cur, league, seasons):
    out = {}
    for s, t, pid, team in _rows(
            cur, '''/* q:obs_hist */ SELECT season, tick, player_id, team_id FROM relationship_roster_observations
                    WHERE league_id = %s AND season = ANY(%s) AND tick <= %s''', (league, list(seasons), LAST_GAME_TICK)):
        out.setdefault((int(s), int(t)), {})[_key(pid)] = None if team is None else int(team)
    return out


def chart_membership(charts):
    """{pid: team} from {team: {slot: [pid]}} (the first team listing a player wins, by team id)."""
    out = {}
    for team in sorted(charts, key=int):
        for ids in charts[team].values():
            for pid in ids:
                out.setdefault(_key(pid), int(team))
    return out


# ---------------------------------------------------------------- moves

def _in_range(point, prev_point, current):
    return (prev_point is None or _point(*point) >= _point(*prev_point)) and _point(*point) <= _point(*current)


def _causes(cur, changed, prev, season, tick):
    """{pid: cause} for the players whose team changed since `prev` (an observation point)."""
    if not changed:
        return {}
    from pipeline import league_office as lo
    from engines import contracts_engine as ce
    pids = sorted(int(p) for p in changed)
    prev_point = (prev['season'], prev['tick']) if prev else None
    first_season = prev_point[0] if prev_point else season
    seasons = list(range(first_season, season + 1))
    out = {}

    for tid, s, w, kind, pid, years, value in _rows(
            cur, '''/* q:txns */ SELECT id, season, week, type, player_id, contract_years, contract_value
                    FROM transactions WHERE applied AND player_id = ANY(%s) AND season = ANY(%s)
                      AND type IN ('RELEASED', 'TRADED', 'SIGNED') ORDER BY id''', (pids, seasons)):
        point = (int(s), int(w) if w is not None else cal.FIRST_OFFSEASON_TICK)
        if prev_point is not None and _point(*point) <= _point(*prev_point) and w is not None:
            continue
        if _point(*point) > _point(season, tick):
            continue
        cause = {'kind': TXN_CAUSES[kind], 'ref': f'transactions:{tid}', 'jr_move': False}
        if kind == 'SIGNED':
            per_year = (value or 0) / max(int(years or 1), 1)
            cause['above_minimum'] = per_year > ce.minimum_salary(0)
        out[_key(pid)] = cause

    runs = [(int(rid), int(s), w, stage, _json(edits)) for rid, s, w, stage, edits in _rows(
        cur, '''/* q:lo_runs */ SELECT id, season, week, stage, edits FROM league_office_runs
                WHERE status = 'WRITTEN' AND season = ANY(%s) ORDER BY id''', (seasons,))]
    runs = [r for r in runs if _in_range((r[1], r[2] if r[2] is not None else lo.STAGE_WEEKS.get(r[3], 24)),
                                         prev_point, (season, tick))]
    if runs:
        pgid_cause = {}
        for rid, _, _, _, edits in runs:
            for g in edits.get('release') or []:
                pgid_cause[int(g)] = ('RELEASE', rid)
            for item in edits.get('sign') or []:
                pgid_cause[int(item['pgid'])] = ('SIGN', rid)
            for item in edits.get('trade') or []:
                for g in item.get('pgids') or []:
                    pgid_cause[int(g)] = ('TRADE', rid)
        by_pgid = dict(_rows(cur, '/* q:pgids */ SELECT madden_pgid, id FROM players WHERE madden_pgid = ANY(%s)',
                             (sorted(pgid_cause),))) if pgid_cause else {}
        jr = {}
        for mid, kind, payload, run in _rows(
                cur, '''/* q:pending_moves */ SELECT id, kind, payload, written_run FROM franchise_pending_moves
                        WHERE status = 'WRITTEN' AND written_run = ANY(%s) ORDER BY id''', ([r[0] for r in runs],)):
            pid = _json(payload).get('player_id')
            if pid is not None and kind in PENDING_CAUSES:
                jr[(_key(pid), PENDING_CAUSES[kind])] = int(mid)
        for g, (kind, rid) in sorted(pgid_cause.items()):
            pid = by_pgid.get(g)
            if pid is None or _key(pid) not in changed or _key(pid) in out:
                continue
            mid = jr.get((_key(pid), kind))
            out[_key(pid)] = {'kind': kind, 'ref': f'franchise_pending_moves:{mid}' if mid else
                              f'league_office_runs:{rid}', 'jr_move': mid is not None, 'above_minimum': False}

    rest = [p for p in pids if _key(p) not in out]
    if rest:
        for pid, to_season, status, src in _rows(
                cur, '''/* q:transitions */ SELECT player_id, to_season, status, source FROM player_season_transitions
                        WHERE player_id = ANY(%s) AND to_season = ANY(%s) ORDER BY to_season''',
                (rest, [s + 1 for s in seasons] + seasons)):
            kind = 'DRAFT' if status == 'DRAFTED' else 'TRADE' if (status == 'MOVED' and src == 'TRADE') else None
            if kind:
                out[_key(pid)] = {'kind': kind, 'ref': f'player_season_transitions:{pid}:{to_season}',
                                  'jr_move': False}
    rest = [p for p in pids if _key(p) not in out and changed[_key(p)][0] is not None and changed[_key(p)][1] is None]
    if rest:
        last = {}
        for cid, pid, team, years in _rows(
                cur, '''/* q:contracts_last */ SELECT id, player_id, team_id, years_remaining FROM player_contracts
                        WHERE player_id = ANY(%s) ORDER BY id''', (rest,)):
            if int(team) == changed[_key(pid)][0]:
                last[_key(pid)] = (int(cid), years)
        for pid, (cid, years) in last.items():
            if years is not None and int(years) <= 1:
                out[pid] = {'kind': 'EXPIRY', 'ref': f'player_contracts:{cid}', 'jr_move': False}
    return out


# ---------------------------------------------------------------- the tick

def read_tick(cur, league, season, tick, *, source='SAVE', roster=None, roster_season=None,
              rules_version='producers-v2'):
    """One tick's inputs (module docstring). `roster` ({pid: team|None}) overrides the database roster (the
    watcher passes the snapshot's); `roster_season` is the player_seasons season to read otherwise."""
    if source not in ('SAVE', 'CATCHUP'):
        raise SourceError(f'source must be SAVE or CATCHUP, got {source!r}')
    cal.time_point(season, tick)
    in_season = cal.is_in_season(tick)
    seasons = [season - 1, season]
    prev = previous_observation(cur, league, season, tick)
    prev_run = previous_run(cur, league, season, tick, rules_version)
    win = window((prev_run['season'], prev_run['tick']) if prev_run else None, season, tick)

    charts = _charts(cur, seasons)
    injuries = _injuries(cur, seasons)
    byes, games = _games(cur, seasons)
    observed = _observed_teams(cur, league, seasons)
    current_charts = charts.get((season, tick), {}) if in_season else {}

    if source == 'CATCHUP':
        now = chart_membership(current_charts)
        for row in injuries:
            if (row['season'], row['tick']) == (season, tick):
                now.setdefault(_key(row['player_id']), row['team_id'])
    elif roster is not None:
        now = {_key(p): (None if t is None else int(t)) for p, t in roster.items()}
    else:
        now = {_key(p): (None if t is None or rw is not None else int(t)) for p, t, rw in _rows(
            cur, '''/* q:roster */ SELECT ps.player_id, ps.team_id, ps.released_week FROM player_seasons ps
                    JOIN players p ON p.id = ps.player_id
                    WHERE ps.season = %s AND p.retired_season IS NULL ORDER BY ps.player_id''',
            (roster_season if roster_season is not None else season,))}

    weeks = []
    for (s, t) in sorted(charts):
        if _point(s, t) >= _point(season, tick):
            continue
        teams = observed.get((s, t)) or chart_membership(charts[(s, t)])
        weeks.append({'season': s, 'tick': t, 'teams': {p: v for p, v in teams.items() if v is not None},
                      'charts': charts[(s, t)], 'byes': sorted(byes.get((s, t), ())),
                      'injured': {_key(r['player_id']): {k: r[k] for k in ('injury_id', 'weeks_remaining', 'is_ir',
                                                                          'injury_type')}
                                  for r in injuries if (r['season'], r['tick']) == (s, t)}})
    if in_season:
        weeks.append({'season': season, 'tick': tick, 'teams': {p: v for p, v in now.items() if v is not None},
                      'charts': current_charts, 'byes': sorted(byes.get((season, tick), ())),
                      'injured': {_key(r['player_id']): {k: r[k] for k in ('injury_id', 'weeks_remaining', 'is_ir',
                                                                          'injury_type')}
                                  for r in injuries if (r['season'], r['tick']) == (season, tick)}})

    before = (prev or {}).get('roster') or {}
    changed = {}
    if prev is not None:
        for pid, team in now.items():
            was = before.get(pid)
            if pid not in before:
                was = None
            if was != team:
                changed[pid] = (was, team)
        if source == 'SAVE':
            for pid, was in before.items():
                if pid not in now and was is not None:
                    changed[pid] = (was, None)

    pids = sorted({int(p) for p in now} | {int(p) for p in before})
    players = {}
    if pids:
        for pid, origin, pos, ovr in _rows(
                cur, '''/* q:players */ SELECT p.id, p.origin, ps.position, ps.overall_rating FROM players p
                        LEFT JOIN player_seasons ps ON ps.player_id = p.id AND ps.season = %s
                        WHERE p.id = ANY(%s) ORDER BY p.id''',
                (roster_season if roster_season is not None else season, pids)):
            players[_key(pid)] = {'origin': origin, 'position': pos, 'overall': ovr}

    watermark = None
    recognitions = []
    if source == 'SAVE':
        watermark = int(_one(cur, '/* q:max_contract */ SELECT COALESCE(MAX(id), 0) FROM player_contracts')[0])
        since = ((prev_run or {}).get('counts') or {}).get('watermarks', {}).get('player_contracts')
        if since is not None and watermark > int(since):
            new = _rows(cur, '''/* q:contracts_new */ SELECT id, player_id, team_id, season, years_remaining, status
                                FROM player_contracts WHERE id > %s AND status = 'ACTIVE' ORDER BY id''', (int(since),))
            if new:
                older = {}
                for cid, pid, team, status in _rows(
                        cur, '''/* q:contracts_prior */ SELECT id, player_id, team_id, status FROM player_contracts
                                WHERE player_id = ANY(%s) AND id <= %s ORDER BY id''',
                        (sorted({int(r[1]) for r in new}), watermark)):
                    older.setdefault(int(pid), []).append((int(cid), int(team), status))
                for cid, pid, team, cseason, years, status in new:
                    prior = [o for o in older.get(int(pid), []) if o[0] < int(cid) and o[2] == 'SUPERSEDED']
                    if not prior:
                        continue
                    recognitions.append({'contract_id': int(cid), 'player_id': int(pid), 'team_id': int(team),
                                         'season': season, 'tick': tick, 'status': status,
                                         'years_remaining': years, 'superseded_contract_id': prior[-1][0],
                                         'superseded_team_id': prior[-1][1]})

    contract_years = []
    if [season, 1] in win:
        for cid, pid, team, years in _rows(
                cur, '''/* q:contract_years */ SELECT id, player_id, team_id, years_remaining FROM player_contracts
                        WHERE status = 'ACTIVE' AND season = %s AND years_remaining = 1 ORDER BY id''', (season,)):
            contract_years.append({'contract_id': int(cid), 'player_id': int(pid), 'team_id': int(team),
                                   'season': season, 'status': 'ACTIVE', 'years_remaining': 1})

    awards = [{'award_id': int(a), 'season': int(s), 'award_type': t, 'team_id': team, 'player_id': p,
               'coach_id': c} for a, s, t, team, p, c in _rows(
        cur, '''/* q:awards */ SELECT id, season, award_type, team_id, player_id, coach_id FROM awards
                WHERE season = %s ORDER BY id''', (season,))]
    staff = [{'staff_move_id': int(i), 'season': int(s), 'team_id': int(team), 'role': role, 'action': action,
              'source': src, 'coach_id': c, 'coordinator_id': k} for i, s, team, role, action, src, c, k in _rows(
        cur, '''/* q:staff */ SELECT id, season, team_id, role, action, source, coach_id, coordinator_id
                FROM staff_moves WHERE season = %s ORDER BY id''', (season,))]
    hot = [{'role': role, 'season': int(s), 'team_id': int(team), 'coach_id': c, 'coordinator_id': k,
            'score': float(score), 'fired': bool(fired), 'phase': phase} for role, s, team, c, k, score, fired, phase in
           _rows(cur, '''/* q:hot_seat */ SELECT 'HC', season, team_id, coach_id, NULL, score, fired, phase
                         FROM coach_hot_seat WHERE season = %s
                         UNION ALL SELECT role, season, team_id, NULL, coordinator_id, score, fired, phase
                         FROM coordinator_hot_seat WHERE season = %s ORDER BY 3, 1''', (season, season))]

    return {
        'league_id': int(league), 'season': int(season), 'tick': int(tick), 'source': source, 'window': win,
        'previous': prev, 'roster': now, 'players': players, 'weeks': weeks,
        'schemes': _schemes(cur, seasons),
        'causes': _causes(cur, changed, prev, season, tick) if changed else {},
        'injuries': [r for r in injuries if r['season'] == season and r['tick'] <= tick],
        'games': {team: [g for g in gs if g['tick'] <= tick] for team, gs in games.get(season, {}).items()},
        'recognitions': recognitions, 'contract_years': contract_years,
        'awards': awards, 'staff_moves': staff, 'hot_seat': hot,
        'tree_staff': tree_staff(cur, season) if tick >= cal.FIRST_OFFSEASON_TICK else [],
        'tree_reviewed_pairs': tree_reviewed_pairs() if tick >= cal.FIRST_OFFSEASON_TICK else [],
        'watermarks': {'player_contracts': watermark} if watermark is not None else {},
    }


def tree_staff(cur, season):
    """Coaching-tree staff seasons through `season` (engines.coaching_tree_facts.staff_seasons; COACHING_TREES §2.6)."""
    from engines import coaching_tree_facts
    return coaching_tree_facts.staff_seasons(cur, through=season)


def tree_reviewed_pairs(directory=None):
    """[[mentor person key, protégé person key]] of reviewed coaching-tree edges (the files; no database).
    sim_edges skips these pairs so a reviewed tie isn't produced twice."""
    from personality import coaching_trees as ct
    edges = ct.reviewed_edges(ct.load_batches(directory or ct.TREE_DIR))
    return sorted([list(p) for p in ct.reviewed_pairs(edges)])


def first_save_season(cur):
    """min(season) over captures with mode 'save', else the first season with a MADDEN chart (plan §2.5)."""
    row = _one(cur, "/* q:first_save */ SELECT MIN(season) FROM captures WHERE mode = 'save'")
    if row is None or row[0] is None:
        row = _one(cur, "/* q:first_chart */ SELECT MIN(season) FROM depth_charts WHERE source = 'MADDEN'")
    return None if row is None or row[0] is None else int(row[0])


def catch_up_ticks(cur, through):
    """[(season, tick)] the catch-up replays: every in-season tick with MADDEN charts, first save to `through`."""
    first = first_save_season(cur)
    if first is None:
        return []
    out = []
    for s, w in _rows(cur, '''/* q:chart_ticks */ SELECT DISTINCT season, week FROM depth_charts
                              WHERE source = 'MADDEN' AND season >= %s AND week <= %s ORDER BY season, week''',
                      (first, LAST_GAME_TICK)):
        if _point(s, w) <= _point(*through):
            out.append((int(s), int(w)))
    return out


def save_runs_through(cur, league, through, rules_version):
    """SAVE runs at or before `through` (the catch-up refuses to rewrite forward data)."""
    return [(int(s), int(t)) for s, t in _rows(
        cur, '''/* q:save_runs */ SELECT season, tick FROM relationship_producer_runs
                WHERE league_id = %s AND rules_version = %s AND source = 'SAVE'
                  AND (season < %s OR (season = %s AND tick <= %s)) ORDER BY season, tick''',
        (league, rules_version, through[0], through[0], through[1]))]
