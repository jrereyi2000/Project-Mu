"""Season facts for the focus refresh (INFERRED_SLIDERS_AND_FOCUS_PLAN.md §2.6).

    facts(cur, universe_id, season, *, bindings=None) -> {persona_id: facts}

Read-only, on the caller's cursor; never commits. Holders are the universe's core coaches and owners. A coach's
seat and team come from his db-coach / db-coordinator alias and the tenures: the tenure covering next season
(season + 1, a carousel hire) first, else an open tenure covering this season; a coach whose last tenure ended
this season has no seat (team and seat None, so HOLDER_MOVED never fires on a missing fact). An owner's team
comes from his birth key `curated-owner:<TEAM>:2026`.

Team facts (direction, made_playoffs, won_super_bowl, hot_seat) and people (qb1, young_star, top_defender,
top_weapon, head_coach, gm) are read for the holder's team in `season`. `bindings` ({persona_id: [subject, ...]},
the subjects of that person's active focus rows) are given a status: a player on the holder's team →
`with_team`, on another team → `elsewhere`, a player_retirements row → `retired`, no roster row → `gone`; a
coach (or GM) still in the seat the subject names → `with_team`, otherwise `not_in_seat`. A source table that
isn't there leaves its facts None (listed in `missing`); None is never stale (personality.focus.stale_causes).
"""
from __future__ import annotations

from collections import defaultdict

from engines.relationship_producers.rules import FIRING_LINE, HOT_SEAT_PRESSURE

FOCUS_TICK = 24
PLAYOFF_GAME_TYPES = ('WILD_CARD', 'DIVISIONAL', 'CONFERENCE', 'SUPER_BOWL')
COACH_NAMESPACES = ('db-coach', 'db-coordinator')
OWNER_BIRTH_PREFIX = 'curated-owner:'
YOUNG_STAR_MAX_AGE = 25
QB_POSITIONS = ('QB',)
WEAPON_POSITIONS = ('WR', 'TE', 'RB', 'HB', 'FB')
DEFENDER_POSITIONS = ('DE', 'DT', 'LE', 'RE', 'EDGE', 'DL', 'NT', 'LB', 'OLB', 'ILB', 'MLB', 'LOLB', 'ROLB',
                      'CB', 'S', 'FS', 'SS', 'DB')
SOURCES = ('teams', 'players', 'player_seasons', 'player_attributes', 'player_retirements', 'games',
           'gm_team_seasons', 'gm_profiles', 'coaches', 'coordinators', 'coach_tenures', 'coordinator_tenures',
           'coach_hot_seat', 'coordinator_hot_seat')
PEOPLE_KEYS = ('qb1', 'young_star', 'top_defender', 'top_weapon', 'head_coach', 'gm')


def _rows(cur, sql, params=()):
    cur.execute(sql, params)
    return [tuple(r) for r in cur.fetchall()]


def tables_ready(cur):
    names = ('persona_core_profiles', 'persona_core_aliases') + SOURCES
    (row,) = _rows(cur, '/* q:ready */ SELECT ' + ', '.join(f"to_regclass('{n}') IS NOT NULL" for n in names))
    return dict(zip(names, (bool(v) for v in row)))


def _holders(cur, universe_id):
    out = {}
    for ptype, pid, birth_key, metadata, ns, ext in _rows(cur, '''/* q:holders */
            SELECT p.persona_type, p.persona_id, p.birth_key, p.metadata, a.namespace, a.external_id
              FROM persona_core_profiles p
              LEFT JOIN persona_core_aliases a ON a.universe_id = p.universe_id AND a.persona_type = p.persona_type
                                              AND a.persona_id = p.persona_id AND a.namespace = ANY(%s)
             WHERE p.universe_id = %s AND p.persona_type IN ('coach', 'owner')
             ORDER BY p.persona_type, p.persona_id, a.namespace, a.external_id''',
                                                       (list(COACH_NAMESPACES), universe_id)):
        meta = metadata or {}
        h = out.setdefault(pid, dict(persona_id=pid, role=ptype, birth_key=birth_key,
                                     traits=list(meta.get('traits') or []), ids={n: [] for n in COACH_NAMESPACES}))
        if ns in COACH_NAMESPACES:
            try:
                h['ids'][ns].append(int(ext))
            except (TypeError, ValueError):
                pass
    return out


def owner_team(birth_key):
    """'BAL' from 'curated-owner:BAL:2026', else None."""
    if not isinstance(birth_key, str) or not birth_key.startswith(OWNER_BIRTH_PREFIX):
        return None
    parts = birth_key.split(':')
    return parts[1] if len(parts) == 3 else None


def _seat_of(tenures, season):
    """The tenure covering season + 1 (latest start), else an open tenure covering season, else None."""
    nxt = [t for t in tenures if t['start'] <= season + 1 and (t['end'] is None or t['end'] >= season + 1)]
    if nxt:
        return max(nxt, key=lambda t: t['start'])
    now = [t for t in tenures if t['start'] <= season and t['end'] is None]
    return max(now, key=lambda t: t['start']) if now else None


def _blank(h, universe_id, season, missing):
    return dict(universe_id=universe_id, season=season, persona_id=h['persona_id'], role=h['role'], seat=None,
                team=None, team_id=None, team_name=None, direction=None, made_playoffs=None, won_super_bowl=None,
                hot_seat=None, people={k: None for k in PEOPLE_KEYS}, subject_status={}, traits=list(h['traits']),
                holder_name=None, source_ids=[], missing=dict(missing))


def subject_key(binding):
    if not isinstance(binding, dict) or not binding.get('namespace') or binding.get('external_id') in (None, ''):
        return None
    return f"{binding['namespace']}:{binding['external_id']}"


def facts(cur, universe_id, season, *, bindings=None):
    """{persona_id: facts} for the universe's core coaches and owners (plan §2.4 facts shape)."""
    ready = tables_ready(cur)
    if not (ready['persona_core_profiles'] and ready['persona_core_aliases']):
        return {}
    missing = {t: 'table not migrated' for t in SOURCES if not ready[t]}
    holders = _holders(cur, universe_id)
    if not holders:
        return {}
    teams = {}
    if ready['teams']:
        teams = {tid: (abbr, name) for tid, abbr, name in _rows(cur, '''/* q:teams */
                SELECT id, abbreviation, name FROM teams ORDER BY id''')}
    by_abbr = {abbr: tid for tid, (abbr, _) in teams.items()}

    hc_ten, co_ten = defaultdict(list), defaultdict(list)
    hc_names, co_names = {}, {}
    if ready['coach_tenures']:
        for cid, team, start, end in _rows(cur, '''/* q:coach_tenures */
                SELECT coach_id, team_id, start_season, end_season FROM coach_tenures WHERE start_season <= %s
                 ORDER BY coach_id, start_season''', (season + 1,)):
            hc_ten[cid].append(dict(team=team, start=start, end=end, role='HC', id=cid))
    if ready['coordinator_tenures']:
        for cid, team, role, start, end in _rows(cur, '''/* q:coordinator_tenures */
                SELECT coordinator_id, team_id, role, start_season, end_season FROM coordinator_tenures
                 WHERE start_season <= %s ORDER BY coordinator_id, start_season''', (season + 1,)):
            co_ten[cid].append(dict(team=team, start=start, end=end, role=role, id=cid))
    if ready['coaches']:
        hc_names = dict(_rows(cur, '/* q:coaches */ SELECT id, coach_name FROM coaches'))
    if ready['coordinators']:
        co_names = dict(_rows(cur, '/* q:coordinators */ SELECT id, coordinator_name FROM coordinators'))

    head_coach_of = {}
    if ready['coach_tenures']:
        for cid, tens in hc_ten.items():
            t = _seat_of(tens, season)
            if t is not None:
                prev = head_coach_of.get(t['team'])
                if prev is None or t['start'] > prev['start']:
                    head_coach_of[t['team']] = t

    direction = {}
    if ready['gm_team_seasons']:
        direction = dict(_rows(cur, '''/* q:directions */
                SELECT team_id, direction FROM gm_team_seasons WHERE season = %s''', (season,)))
    sb_winner, playoff_teams = None, set()
    if ready['games']:
        for gtype, home, away, hs, as_ in _rows(cur, '''/* q:games */
                SELECT game_type, home_team_id, away_team_id, home_score, away_score FROM games
                 WHERE season = %s AND game_type = ANY(%s) AND status = 'COMPLETED' ORDER BY id''',
                                                (season, list(PLAYOFF_GAME_TYPES))):
            playoff_teams.update((home, away))
            if gtype == 'SUPER_BOWL' and hs is not None and as_ is not None and hs != as_:
                sb_winner = home if hs > as_ else away
    hot = {}
    if ready['coach_hot_seat']:
        for team, score, fired, inputs in _rows(cur, '''/* q:coach_hot_seat */
                SELECT team_id, score, fired, inputs FROM coach_hot_seat WHERE season = %s''', (season,)):
            line = (inputs or {}).get('firing_line', FIRING_LINE) if isinstance(inputs, dict) else FIRING_LINE
            hot[('HC', team)] = not fired and float(score or 0) >= HOT_SEAT_PRESSURE * line
    if ready['coordinator_hot_seat']:
        for team, role, score, fired, inputs in _rows(cur, '''/* q:coordinator_hot_seat */
                SELECT team_id, role, score, fired, inputs FROM coordinator_hot_seat WHERE season = %s''', (season,)):
            line = (inputs or {}).get('firing_line', FIRING_LINE) if isinstance(inputs, dict) else FIRING_LINE
            hot[(role, team)] = not fired and float(score or 0) >= HOT_SEAT_PRESSURE * line

    roster = defaultdict(list)            # team -> [{player_id, name, position, age, ovr}]
    player_team = {}                      # player_id -> team in `season` (active row)
    if ready['player_seasons']:
        rows = _rows(cur, '''/* q:roster */
                SELECT ps.player_id, ps.team_id, ps.position, ps.age, coalesce(p.full_name, p.player_name)
                  FROM player_seasons ps JOIN players p ON p.id = ps.player_id
                 WHERE ps.season = %s AND ps.released_week IS NULL AND ps.player_id IS NOT NULL
                 ORDER BY ps.player_id, ps.id''', (season,))
        ovr = {}
        if ready['player_attributes'] and rows:
            ovr = dict(_rows(cur, '''/* q:ovr */
                SELECT DISTINCT ON (player_id) player_id, overall_rating FROM player_attributes
                 WHERE season <= %s AND player_id = ANY(%s) AND overall_rating IS NOT NULL
                 ORDER BY player_id, season DESC, id DESC''', (season, sorted({r[0] for r in rows}))))
        for pid, team, pos, age, name in rows:
            player_team[pid] = team
            if team is not None:
                roster[team].append(dict(player_id=pid, name=name, position=pos, age=age, ovr=ovr.get(pid)))
    retired = set()
    if ready['player_retirements']:
        retired = {pid for (pid,) in _rows(cur, '''/* q:retired */
                SELECT DISTINCT player_id FROM player_retirements WHERE season <= %s AND player_id IS NOT NULL''',
                                           (season,))}
    gm_rows = {}
    if ready['gm_profiles']:
        for gid, team, name, start, end in _rows(cur, '''/* q:gms */
                SELECT id, team_id, gm_name, start_season, end_season FROM gm_profiles
                 WHERE start_season <= %s ORDER BY team_id, start_season, id''', (season + 1,)):
            gm_rows[gid] = dict(team=team, name=name, start=start, end=end)
    gm_of = {}
    for gid, g in gm_rows.items():
        if g['end'] is None or g['end'] >= season + 1 or (g['end'] >= season and g['start'] <= season):
            prev = gm_of.get(g['team'])
            if prev is None or g['start'] >= gm_rows[prev]['start']:
                gm_of[g['team']] = gid

    def best(team, keep):
        cands = [r for r in roster.get(team, []) if keep(r) and r['ovr'] is not None]
        if not cands:
            return None
        top = max(cands, key=lambda r: (r['ovr'], -r['player_id']))
        return dict(kind='player', name=top['name'], binding=dict(namespace='db-player', external_id=str(top['player_id'])))

    out = {}
    for pid in sorted(holders):
        h = holders[pid]
        f = _blank(h, universe_id, season, missing)
        team_id = None
        if h['role'] == 'owner':
            abbr = owner_team(h['birth_key'])
            team_id = by_abbr.get(abbr) if teams else None
            f['seat'] = 'OWNER'
            f['team'] = abbr
        else:
            tens = [t for cid in h['ids']['db-coach'] for t in hc_ten.get(cid, [])] if ready['coach_tenures'] else []
            ctens = ([t for cid in h['ids']['db-coordinator'] for t in co_ten.get(cid, [])]
                     if ready['coordinator_tenures'] else [])
            seat = _seat_of(tens, season) or _seat_of(ctens, season)
            if not ready['coach_tenures'] and not ready['coordinator_tenures']:
                seat = None
            if seat is not None:
                team_id = seat['team']
                f['seat'] = seat['role']
                f['team'] = teams.get(team_id, (None, None))[0]
                f['holder_name'] = (hc_names if seat['role'] == 'HC' else co_names).get(seat['id'])
                f['source_ids'].append(f"{'coach_tenures' if seat['role'] == 'HC' else 'coordinator_tenures'}:"
                                       f"{seat['id']}:{seat['start']}")
        f['team_id'] = team_id
        if team_id is not None:
            f['team_name'] = teams.get(team_id, (None, None))[1]
            if ready['gm_team_seasons']:
                f['direction'] = direction.get(team_id)
            if ready['games']:
                f['made_playoffs'] = team_id in playoff_teams
                f['won_super_bowl'] = sb_winner is not None and team_id == sb_winner
            hot_key = ('HC' if f['seat'] in ('HC', 'OWNER') else f['seat'], team_id)
            table = 'coach_hot_seat' if hot_key[0] == 'HC' else 'coordinator_hot_seat'
            if ready[table]:
                f['hot_seat'] = bool(hot.get(hot_key, False))
            if ready['player_seasons'] and ready['player_attributes']:
                f['people']['qb1'] = best(team_id, lambda r: r['position'] in QB_POSITIONS)
                f['people']['young_star'] = best(team_id, lambda r: r['age'] is not None
                                                 and r['age'] <= YOUNG_STAR_MAX_AGE)
                f['people']['top_defender'] = best(team_id, lambda r: r['position'] in DEFENDER_POSITIONS)
                f['people']['top_weapon'] = best(team_id, lambda r: r['position'] in WEAPON_POSITIONS)
            hc = head_coach_of.get(team_id)
            if hc is not None and ready['coaches'] and f['seat'] != 'HC':
                f['people']['head_coach'] = dict(kind='coach', name=hc_names.get(hc['id']),
                                                 binding=dict(namespace='db-coach', external_id=str(hc['id'])))
            gid = gm_of.get(team_id)
            if gid is not None:
                f['people']['gm'] = dict(kind='gm', name=gm_rows[gid]['name'],
                                         binding=dict(namespace='db-gm', external_id=str(gid)))
        for sub in (bindings or {}).get(pid) or []:
            key = subject_key((sub or {}).get('binding'))
            if key is None:
                continue
            f['subject_status'][key] = _status(sub, team_id, season, ready, player_team, retired, hc_ten, co_ten,
                                               gm_rows, by_abbr)
        out[pid] = f
    return out


def _status(sub, team_id, season, ready, player_team, retired, hc_ten, co_ten, gm_rows, by_abbr):
    b = sub['binding']
    ns = b.get('namespace')
    try:
        ext = int(b.get('external_id'))
    except (TypeError, ValueError):
        return dict(status='unknown', team=None)
    if ns == 'db-player':
        if not ready['player_seasons']:
            return dict(status='unknown', team=None)
        if ready['player_retirements'] and ext in retired:
            return dict(status='retired', team=None)
        if ext not in player_team:
            return dict(status='gone', team=None)
        now = player_team[ext]
        if team_id is None:
            return dict(status='unknown', team=now)
        return dict(status='with_team' if now == team_id else 'elsewhere', team=now)
    seat = sub.get('seat') or {}
    want_team = by_abbr.get(seat.get('team')) if seat.get('team') else team_id
    if ns == 'db-coach' or ns == 'db-coordinator':
        table = 'coach_tenures' if ns == 'db-coach' else 'coordinator_tenures'
        if not ready[table]:
            return dict(status='unknown', team=None)
        tens = (hc_ten if ns == 'db-coach' else co_ten).get(ext, [])
        t = _seat_of(tens, season)
        if t is None:
            return dict(status='not_in_seat', team=None)
        same_role = seat.get('role') in (None, t['role'])
        return dict(status='with_team' if t['team'] == want_team and same_role else 'not_in_seat', team=t['team'])
    if ns == 'db-gm':
        if not ready['gm_profiles']:
            return dict(status='unknown', team=None)
        g = gm_rows.get(ext)
        if g is None:
            return dict(status='not_in_seat', team=None)
        active = g['end'] is None or g['end'] > season
        return dict(status='with_team' if active and g['team'] == want_team else 'not_in_seat', team=g['team'])
    return dict(status='unknown', team=None)
