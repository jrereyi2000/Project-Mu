"""Each player's and coach's season facts for personality evolution (PERSONALITY_EVOLUTION_PLAN.md §2.1).

    season_facts(cur, universe_id, season) -> {persona_id: facts}

Read-only, on the caller's cursor; never commits. People are the universe's core profiles (role player or coach)
with a db-player, db-coach or db-coordinator alias (the relationship producers' namespaces). Every fact cites its
source rows (`<table>:<key>`) or relationship events (their source_event_id) in `source_event_ids[fact]`.

A source table that isn't there makes its facts None (plan R3), with the reason in `missing`, so the rules that
read them never fire and the rest still run.

Season conventions (As built):
- Relationship events count inside the evolution window (season - 1 tick 24, season tick 24]: everything since the
  previous season's evolution run, so an offseason release and signing count next season, once.
- `mentor_retired`: find_mentor (engines/progression.py) over last season's (season - 1) roster and Awareness, and
  that mentor's player_retirements row for season - 1 (he retired after it, so this season went without him).
- `coach_promoted_to_hc`: a staff_moves HC HIRED row from the season - 1 hiring cycle (the one into this season)
  whose from_role is OC or DC, naming one of his db-coach IDs: this season was his first as a head coach.
- `coach_fired` / `coach_kept_on_hot_seat`: this season's coach_hot_seat / coordinator_hot_seat rows.
- `seasons_in_league`: players years_of_experience + 1; coaches the seasons their franchise tenures cover (time
  before the franchise isn't counted).
"""
from __future__ import annotations

from collections import defaultdict

from engines.progression import find_mentor, _group_of
from engines.relationship_producers.rules import (FIRING_LINE, HOT_SEAT_PRESSURE, award_source_id,
                                                  staff_source_id)

EVOLUTION_TICK = 24
PLAYOFF_GAME_TYPES = ('WILD_CARD', 'DIVISIONAL', 'CONFERENCE', 'SUPER_BOWL')
DB_NAMESPACES = ('db-player', 'db-coach', 'db-coordinator')
HISTORY_SEASONS = 25            # how far back tenure and losing streaks look
SOURCES = ('persona_relationship_events', 'player_seasons', 'team_seasons', 'games', 'awards', 'coach_tenures',
           'coordinator_tenures', 'coach_hot_seat', 'coordinator_hot_seat', 'staff_moves', 'player_retirements',
           'player_attributes')
FACT_KEYS = ('age', 'seasons_in_league', 'tenure_seasons_on_team', 'team_id', 'position_group', 'won_super_bowl',
             'made_playoffs', 'losing_seasons_in_row', 'awards', 'unsatisfied_role_weeks', 'released_by_team',
             'signed_elsewhere_after_release', 'injury_weeks_out', 'mentor_retired', 'coach_fired',
             'coach_promoted_to_hc', 'coach_kept_on_hot_seat')


def _rows(cur, sql, params=()):
    cur.execute(sql, params)
    return [tuple(r) for r in cur.fetchall()]


def tables_ready(cur):
    """{table: bool} for every source plus the core and alias tables."""
    names = ('persona_core_profiles', 'persona_core_aliases') + SOURCES
    sql = '/* q:ready */ SELECT ' + ', '.join(f"to_regclass('{n}') IS NOT NULL" for n in names)
    (row,) = _rows(cur, sql)
    return dict(zip(names, (bool(v) for v in row)))


def in_window(occurred, season):
    """True for an event inside (season - 1 tick 24, season tick 24]."""
    key = (occurred['season'], occurred['tick'])
    return (season - 1, EVOLUTION_TICK) < key <= (season, EVOLUTION_TICK)


def _people(cur, universe_id):
    out = {}
    for ptype, pid, birth_key, metadata, ns, ext in _rows(cur, '''/* q:people */
            SELECT p.persona_type, p.persona_id, p.birth_key, p.metadata, a.namespace, a.external_id
              FROM persona_core_profiles p
              JOIN persona_core_aliases a ON a.universe_id = p.universe_id AND a.persona_type = p.persona_type
                                         AND a.persona_id = p.persona_id
             WHERE p.universe_id = %s AND p.persona_type IN ('player', 'coach') AND a.namespace = ANY(%s)
             ORDER BY p.persona_type, p.persona_id, a.namespace, a.external_id''', (universe_id, list(DB_NAMESPACES))):
        meta = metadata or {}
        person = out.setdefault(pid, dict(persona_id=pid, role=ptype, birth_key=birth_key,
                                          profile_view={'traits': list(meta.get('traits') or []),
                                                        'sliders': dict(meta.get('sliders') or {})},
                                          ids={n: [] for n in DB_NAMESPACES}))
        try:
            person['ids'][ns].append(int(ext))
        except (TypeError, ValueError):
            continue
    return out


def _covering(tenures, season):
    """The latest-starting tenure covering `season`, or None."""
    live = [t for t in tenures if t['start'] <= season and (t['end'] is None or t['end'] >= season)]
    return max(live, key=lambda t: t['start']) if live else None


def _covered_seasons(tenures, season):
    seen = set()
    for t in tenures:
        seen.update(range(t['start'], min(season, t['end'] if t['end'] is not None else season) + 1))
    return seen


def _losing_run(team_by_season, records, season):
    """(seasons in a row through `season` his team lost more than it won, cited team_seasons keys)."""
    run, cites, s = 0, [], season
    while True:
        team = team_by_season.get(s)
        rec = records.get((team, s)) if team is not None else None
        if rec is None or rec[0] >= rec[1]:
            return run, cites
        run += 1
        cites.append(f'team_seasons:{team}:{s}')
        s -= 1


def _blank(person, universe_id, season, missing):
    facts = dict(universe_id=universe_id, season=season, persona_id=person['persona_id'], role=person['role'],
                 birth_key=person['birth_key'], profile_view=person['profile_view'])
    facts.update({k: None for k in FACT_KEYS})
    facts['awards'] = []
    facts['source_event_ids'] = {}
    facts['missing'] = dict(missing)
    return facts


def _cite(facts, fact, ids):
    ids = [i for i in ids if i]
    if ids:
        facts['source_event_ids'].setdefault(fact, [])
        for i in ids:
            if i not in facts['source_event_ids'][fact]:
                facts['source_event_ids'][fact].append(i)


def _events_by_subject(cur, universe_id, season):
    from personality import relationship_events as rev
    envs = [r[0] for r in _rows(cur, '''/* q:events */ SELECT envelope FROM persona_relationship_events
             WHERE universe_id = %s AND occurred_season IN (%s, %s)
             ORDER BY occurred_season, occurred_tick, head, source_event_id, facet_key''',
                                (universe_id, season - 1, season))]
    active = rev.select_active(envs)['active'] if envs else []
    out = defaultdict(list)
    for ev in active:
        subject = ev.get('subject') or {}
        if subject.get('kind') == 'person' and in_window(ev['occurred_at'], season):
            out[subject.get('persona_id')].append(ev)
    return out


def _event_facts(facts, events):
    unsatisfied = [e for e in events if e['kind'] == 'role_window' and e['payload'].get('eligible')
                   and e['payload'].get('assessment') == 'unsatisfied']
    facts['unsatisfied_role_weeks'] = len(unsatisfied)
    _cite(facts, 'unsatisfied_role_weeks', [e['source_event_id'] for e in unsatisfied])
    injuries = [e for e in events if e['kind'] == 'situation' and e['payload'].get('situation_kind') == 'injury'
                and e['payload'].get('state') == 'negative']
    facts['injury_weeks_out'] = sum(int(e['payload'].get('weeks_out') or 0) for e in injuries)
    _cite(facts, 'injury_weeks_out', [e['source_event_id'] for e in injuries])
    releases = [e for e in events if e['kind'] == 'treatment' and e['payload'].get('treatment_kind') == 'dismissal'
                and str(e['payload'].get('rule', '')).startswith('release_dismissal')]
    facts['released_by_team'] = bool(releases)
    facts['signed_elsewhere_after_release'] = False
    _cite(facts, 'released_by_team', [e['source_event_id'] for e in releases])
    for rel in releases:
        when = (rel['occurred_at']['season'], rel['occurred_at']['tick'])
        arrivals = [e for e in events if e['kind'] == 'situation' and e['payload'].get('situation_kind') == 'arrival'
                    and (e['occurred_at']['season'], e['occurred_at']['tick']) >= when
                    and (e.get('target') or {}).get('team_key') != rel['payload'].get('team_key')]
        if arrivals:
            facts['signed_elsewhere_after_release'] = True
            _cite(facts, 'signed_elsewhere_after_release', [rel['source_event_id'], arrivals[0]['source_event_id']])
            break


def season_facts(cur, universe_id, season):
    """{persona_id: facts} for the universe's players and coaches (plan §2.1)."""
    ready = tables_ready(cur)
    if not (ready['persona_core_profiles'] and ready['persona_core_aliases']):
        return {}
    missing = {t: 'table not migrated' for t in SOURCES if not ready[t]}
    people = _people(cur, universe_id)
    if not people:
        return {}
    last = season - 1
    lo = season - HISTORY_SEASONS

    # player seasons: {player_id: {season: row}}
    ps = defaultdict(dict)
    rosters = defaultdict(list)          # last season's roster per team, for find_mentor
    if ready['player_seasons']:
        for rid, pid, s, team, pos, age, yoe, released in _rows(cur, '''/* q:player_seasons */
                SELECT id, player_id, season, team_id, position, age, years_of_experience, released_week
                  FROM player_seasons WHERE season BETWEEN %s AND %s AND player_id IS NOT NULL
                   AND position IS DISTINCT FROM 'DEF' ORDER BY player_id, season''', (lo, season)):
            ps[pid][s] = dict(id=rid, team=team, position=pos, age=age, yoe=yoe, released=released)
            if s == last and team is not None:
                rosters[team].append({'player_id': pid, 'position': pos, 'age': age})
    records = {}
    if ready['team_seasons']:
        records = {(t, s): (w or 0, l or 0) for t, s, w, l in _rows(cur, '''/* q:team_seasons */
                SELECT team_id, season, wins, losses FROM team_seasons WHERE season BETWEEN %s AND %s''', (lo, season))}
    sb_winner, playoff_teams, sb_cite, playoff_cites = None, set(), None, defaultdict(list)
    if ready['games']:
        for gid, gtype, home, away, hs, as_ in _rows(cur, '''/* q:games */
                SELECT id, game_type, home_team_id, away_team_id, home_score, away_score FROM games
                 WHERE season = %s AND game_type = ANY(%s) AND status = 'COMPLETED' ORDER BY id''',
                                                    (season, list(PLAYOFF_GAME_TYPES))):
            for team in (home, away):
                playoff_teams.add(team)
                playoff_cites[team].append(f'games:{gid}')
            if gtype == 'SUPER_BOWL' and hs is not None and as_ is not None and hs != as_:
                sb_winner, sb_cite = (home if hs > as_ else away), f'games:{gid}'
    awards_p, awards_c = defaultdict(list), defaultdict(list)
    if ready['awards']:
        for aid, atype, pid, cid in _rows(cur, '''/* q:awards */
                SELECT id, award_type, player_id, coach_id FROM awards WHERE season = %s ORDER BY id''', (season,)):
            if pid is not None:
                awards_p[pid].append((atype, award_source_id(aid)))
            elif cid is not None:
                awards_c[cid].append((atype, award_source_id(aid)))
    hc_ten, oc_ten = defaultdict(list), defaultdict(list)
    if ready['coach_tenures']:
        for cid, team, start, end in _rows(cur, '''/* q:coach_tenures */
                SELECT coach_id, team_id, start_season, end_season FROM coach_tenures WHERE start_season <= %s
                 ORDER BY coach_id, start_season''', (season,)):
            hc_ten[cid].append(dict(team=team, start=start, end=end, role='HC'))
    if ready['coordinator_tenures']:
        for cid, team, role, start, end in _rows(cur, '''/* q:coordinator_tenures */
                SELECT coordinator_id, team_id, role, start_season, end_season FROM coordinator_tenures
                 WHERE start_season <= %s ORDER BY coordinator_id, start_season''', (season,)):
            oc_ten[cid].append(dict(team=team, start=start, end=end, role=role))
    seats = {}                           # ('HC', coach_id) / ('OC', coordinator_id) -> (fired, kept, cite)
    if ready['coach_hot_seat']:
        for team, cid, score, fired, inputs in _rows(cur, '''/* q:coach_hot_seat */
                SELECT team_id, coach_id, score, fired, inputs FROM coach_hot_seat WHERE season = %s''', (season,)):
            line = (inputs or {}).get('firing_line', FIRING_LINE) if isinstance(inputs, dict) else FIRING_LINE
            seats[('HC', cid)] = (bool(fired), not fired and float(score or 0) >= HOT_SEAT_PRESSURE * line,
                                  f'coach_hot_seat:{season}:{team}')
    if ready['coordinator_hot_seat']:
        for team, role, cid, score, fired, inputs in _rows(cur, '''/* q:coordinator_hot_seat */
                SELECT team_id, role, coordinator_id, score, fired, inputs FROM coordinator_hot_seat
                 WHERE season = %s''', (season,)):
            line = (inputs or {}).get('firing_line', FIRING_LINE) if isinstance(inputs, dict) else FIRING_LINE
            seats[('CO', cid)] = (bool(fired), not fired and float(score or 0) >= HOT_SEAT_PRESSURE * line,
                                  f'coordinator_hot_seat:{season}:{team}:{role}')
    promoted = {}
    if ready['staff_moves']:
        for mid, cid in _rows(cur, '''/* q:staff_moves */
                SELECT id, coach_id FROM staff_moves WHERE season = %s AND role = 'HC' AND action = 'HIRED'
                   AND from_role IN ('OC', 'DC') AND coach_id IS NOT NULL ORDER BY id''', (last,)):
            promoted.setdefault(cid, staff_source_id(mid))
    retired = {}
    if ready['player_retirements']:
        retired = {pid: f'player_retirements:{rid}' for rid, pid in _rows(cur, '''/* q:retirements */
                SELECT id, player_id FROM player_retirements WHERE season = %s AND player_id IS NOT NULL
                 ORDER BY id''', (last,))}
    awareness = {}
    if ready['player_attributes'] and rosters:
        roster_ids = sorted({t['player_id'] for team in rosters.values() for t in team})
        for pid, ratings in _rows(cur, '''/* q:awareness */
                SELECT DISTINCT ON (player_id) player_id, ratings FROM player_attributes
                 WHERE season <= %s AND player_id = ANY(%s) ORDER BY player_id, season DESC, id DESC''',
                                  (last, roster_ids)):
            if isinstance(ratings, dict) and ratings.get('Awareness') is not None:
                awareness[pid] = int(ratings['Awareness'])
    events = (_events_by_subject(cur, universe_id, season) if ready['persona_relationship_events'] else {})

    out = {}
    for pid in sorted(people):
        person = people[pid]
        f = _blank(person, universe_id, season, missing)
        if person['role'] == 'player':
            _player_facts(f, person, season, ps, records, ready, sb_winner, sb_cite, playoff_teams, playoff_cites,
                          awards_p, rosters, awareness, retired, events.get(pid, []))
        else:
            _coach_facts(f, person, season, records, ready, sb_winner, sb_cite, playoff_teams, playoff_cites,
                         awards_c, hc_ten, oc_ten, seats, promoted)
        out[pid] = f
    return out


def _team_facts(f, team, season, ready, sb_winner, sb_cite, playoff_teams, playoff_cites):
    if not ready['games']:
        return
    f['won_super_bowl'] = team is not None and sb_winner is not None and team == sb_winner
    f['made_playoffs'] = team is not None and team in playoff_teams
    if f['won_super_bowl']:
        _cite(f, 'won_super_bowl', [sb_cite])
    if f['made_playoffs']:
        _cite(f, 'made_playoffs', playoff_cites[team][:1])


def _player_facts(f, person, season, ps, records, ready, sb_winner, sb_cite, playoff_teams, playoff_cites,
                  awards_p, rosters, awareness, retired, events):
    db_ids = person['ids']['db-player']
    if not db_ids:
        return
    player_id = db_ids[0]
    seasons = ps.get(player_id, {})
    row = seasons.get(season)
    if ready['player_seasons'] and row is not None:
        f['age'] = row['age']
        f['seasons_in_league'] = None if row['yoe'] is None else row['yoe'] + 1
        f['team_id'] = row['team']
        f['position_group'] = _group_of(row['position'])
        tenure, s = 0, season
        while seasons.get(s) is not None and seasons[s]['team'] == row['team'] and row['team'] is not None:
            tenure += 1
            s -= 1
        f['tenure_seasons_on_team'] = tenure
        _cite(f, 'age', [f'player_seasons:{row["id"]}'])
        _cite(f, 'seasons_in_league', [f'player_seasons:{row["id"]}'])
        _cite(f, 'tenure_seasons_on_team', [f'player_seasons:{seasons[x]["id"]}' for x in range(season, s, -1)])
        released = row['released'] is not None
        team_now = None if released else row['team']
        _team_facts(f, team_now, season, ready, sb_winner, sb_cite, playoff_teams, playoff_cites)
        if ready['team_seasons']:
            run, cites = _losing_run({s: r['team'] for s, r in seasons.items()}, records, season)
            f['losing_seasons_in_row'] = run
            _cite(f, 'losing_seasons_in_row', cites)
    if ready['awards']:
        mine = awards_p.get(player_id, [])
        f['awards'] = [a for a, _ in mine]
        _cite(f, 'awards', [c for _, c in mine])
    prev = seasons.get(season - 1)
    if ready['player_seasons'] and ready['player_retirements'] and ready['player_attributes']:
        f['mentor_retired'] = False
        if prev is not None and prev['team'] is not None:
            mentor = find_mentor({'player_id': player_id, 'position': prev['position']},
                                 rosters.get(prev['team'], []), awareness)
            if mentor is not None and mentor in retired:
                f['mentor_retired'] = True
                _cite(f, 'mentor_retired', [retired[mentor], f'player_seasons:{prev["id"]}'])
    if ready['persona_relationship_events']:
        _event_facts(f, events)


def _coach_facts(f, person, season, records, ready, sb_winner, sb_cite, playoff_teams, playoff_cites, awards_c,
                 hc_ten, oc_ten, seats, promoted):
    hc_ids, co_ids = person['ids']['db-coach'], person['ids']['db-coordinator']
    tenures = [t for cid in hc_ids for t in hc_ten.get(cid, [])] + [t for cid in co_ids for t in oc_ten.get(cid, [])]
    current = _covering(tenures, season)
    if current is not None:
        f['team_id'] = current['team']
        stay, s = 0, season
        while (_covering(tenures, s) or {}).get('team') == current['team']:
            stay += 1
            s -= 1
        f['tenure_seasons_on_team'] = stay
        f['position_group'] = current['role']
        _team_facts(f, current['team'], season, ready, sb_winner, sb_cite, playoff_teams, playoff_cites)
    if ready['coach_tenures'] or ready['coordinator_tenures']:
        covered = _covered_seasons(tenures, season)
        f['seasons_in_league'] = len(covered) if covered else None
    if ready['team_seasons'] and tenures:
        by_season = {}
        for s in range(season - HISTORY_SEASONS, season + 1):
            t = _covering(tenures, s)
            if t is not None:
                by_season[s] = t['team']
        run, cites = _losing_run(by_season, records, season)
        f['losing_seasons_in_row'] = run
        _cite(f, 'losing_seasons_in_row', cites)
    if ready['awards']:
        mine = [a for cid in hc_ids for a in awards_c.get(cid, [])]
        f['awards'] = [a for a, _ in mine]
        _cite(f, 'awards', [c for _, c in mine])
    if ready['coach_hot_seat'] or ready['coordinator_hot_seat']:
        rows = [seats[('HC', c)] for c in hc_ids if ('HC', c) in seats] + \
               [seats[('CO', c)] for c in co_ids if ('CO', c) in seats]
        f['coach_fired'] = any(r[0] for r in rows)
        f['coach_kept_on_hot_seat'] = any(r[1] for r in rows) and not f['coach_fired']
        _cite(f, 'coach_fired', [r[2] for r in rows if r[0]])
        _cite(f, 'coach_kept_on_hot_seat', [r[2] for r in rows if r[1]])
    if ready['staff_moves']:
        moves = [promoted[c] for c in hc_ids if c in promoted]
        f['coach_promoted_to_hc'] = bool(moves)
        _cite(f, 'coach_promoted_to_hc', moves[:1])
