"""Coaching-tree database reads (docs/in_progress/COACHING_TREES_PLAN.md §2.4) for writers, producers and the
carousel.

  staff_seasons(cur, through=...)      one row per (season, team, seat) from coach_tenures / coordinator_tenures
  tree_graph(cur, through=...)         {edges, registry, members, head_coach_pks}: reviewed + simulated edges
  tree_context(cur, name, through=...) lineage + lineage_line for one person, or None

These are documented history and the league's own tenures, so they don't wait for the personality switchboard
(plan Q8). Reads run in a savepoint; missing tables read as no rows (tree_context then returns None) without
aborting the caller's transaction.
"""
from contextlib import contextmanager
import uuid

from personality import coaching_trees as ct

FIRST_SEASON = ct.FIRST_SEASON
TREE_DIR = ct.TREE_DIR
REQUIRED_TABLES = ('coaches', 'coach_tenures', 'coordinators', 'coordinator_tenures')


@contextmanager
def _savepoint(cur):
    name = 'coaching_tree_' + uuid.uuid4().hex
    cur.execute(f'SAVEPOINT {name}')
    try:
        yield
    except BaseException:
        cur.execute(f'ROLLBACK TO SAVEPOINT {name}')
        cur.execute(f'RELEASE SAVEPOINT {name}')
        raise
    else:
        cur.execute(f'RELEASE SAVEPOINT {name}')


def _exists(cur, table):
    cur.execute('/* q:regclass */ SELECT to_regclass(%s) IS NOT NULL', (table,))
    row = cur.fetchone()
    return bool(row and row[0])


def _expand(rows, role_of, namespace, through, first_season, fired):
    out = []
    for r in rows:
        pid, name, team_id, abbr, start, end, role = r
        last = through if end is None else min(end, through)
        for season in range(max(start, first_season), last + 1):
            out.append({'season': season, 'team_id': team_id, 'team': abbr, 'role': role_of(role),
                        'namespace': namespace, 'external_id': pid, 'name': name,
                        'fired': bool(end is not None and season == end and (season, team_id, role, pid) in fired)})
    return out


def _staff_rows(cur, through, first_season):
    if not all(_exists(cur, t) for t in REQUIRED_TABLES):
        return None
    fired = set()
    if _exists(cur, 'coordinator_hot_seat'):
        cur.execute('/* q:fired */ SELECT season, team_id, role, coordinator_id FROM coordinator_hot_seat '
                    'WHERE fired AND season BETWEEN %s AND %s', (first_season, through))
        fired = {tuple(r) for r in cur.fetchall()}
    cur.execute('/* q:hc */ SELECT ct.coach_id, c.coach_name, ct.team_id, t.abbreviation, ct.start_season, '
                "ct.end_season, 'HC' FROM coach_tenures ct JOIN coaches c ON c.id = ct.coach_id "
                'LEFT JOIN teams t ON t.id = ct.team_id '
                'WHERE ct.start_season <= %s AND coalesce(ct.end_season, %s) >= %s '
                'ORDER BY ct.start_season, ct.team_id, ct.coach_id', (through, through, first_season))
    hcs = cur.fetchall()
    cur.execute('/* q:coord */ SELECT ct.coordinator_id, c.coordinator_name, ct.team_id, t.abbreviation, '
                'ct.start_season, ct.end_season, ct.role FROM coordinator_tenures ct '
                'JOIN coordinators c ON c.id = ct.coordinator_id LEFT JOIN teams t ON t.id = ct.team_id '
                'WHERE ct.start_season <= %s AND coalesce(ct.end_season, %s) >= %s '
                'ORDER BY ct.start_season, ct.team_id, ct.role, ct.coordinator_id', (through, through, first_season))
    coords = cur.fetchall()
    rows = _expand(hcs, lambda r: 'HC', 'db-coach', through, first_season, set())
    rows += _expand(coords, lambda r: r, 'db-coordinator', through, first_season, fired)
    return sorted(rows, key=lambda r: (r['season'], str(r['team_id']), r['role'], r['external_id']))


def staff_seasons(cur, *, through, first_season=FIRST_SEASON) -> list[dict]:
    """{season, team_id, team, role ('HC'|'OC'|'DC'), namespace, external_id, name, fired} for every season of
    every tenure inside [first_season, through] (inclusive end_season, open = through). `fired` is
    coordinator_hot_seat.fired in the stint's last season. Missing tables give []."""
    with _savepoint(cur):
        rows = _staff_rows(cur, through, first_season)
    return rows or []


def _head_coach_pks(staff):
    return frozenset(ct.person_key(r['name']) for r in staff if r['role'] == 'HC')


def graph_from(staff, *, through, directory=TREE_DIR, index=None) -> dict:
    """tree_graph without the database: reviewed edges/members from the files plus sim_edges from `staff`."""
    batches = ct.load_batches(directory)
    reviewed = ct.reviewed_edges(batches, index=index)
    sim = ct.sim_edges(staff, through=through, reviewed_pairs=ct.reviewed_pairs(reviewed))
    return {'edges': reviewed + sim, 'registry': ct.registry_trees(ct.load_registry(directory)),
            'members': ct.reviewed_members(batches, index=index), 'head_coach_pks': _head_coach_pks(staff),
            'through': through}


def tree_graph(cur, *, through, directory=TREE_DIR) -> dict:
    """{edges, registry, members, head_coach_pks, through}: reviewed edges and members from the files,
    sim_edges from staff_seasons, head coaches = everyone with a coach_tenures row through `through`."""
    return graph_from(staff_seasons(cur, through=through), through=through, directory=directory)


def context_from(graph, name) -> dict | None:
    """{label, line, trees, mentors, proteges, root_of} for one person from a tree_graph, or None."""
    lin = ct.lineage(ct.person_key(name), edges=graph['edges'], registry=graph['registry'],
                     members=graph['members'], head_coach_pks=graph['head_coach_pks'])
    line = ct.lineage_line(lin)
    if line is None:
        return None
    return dict(lin, line=line)


def tree_context(cur, name, *, through, directory=TREE_DIR) -> dict | None:
    """lineage + lineage_line for one name, or None (nothing to say, or the tenure tables are missing)."""
    if not name:
        return None
    try:
        with _savepoint(cur):
            staff = _staff_rows(cur, through, FIRST_SEASON)
    except Exception:  # noqa: BLE001 - a writer's tool call never fails on tree facts
        return None
    if staff is None:
        return None
    return context_from(graph_from(staff, through=through, directory=directory), name)
