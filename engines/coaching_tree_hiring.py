"""Coaching-tree hiring (docs/done/COACHING_TREES_PLAN.md §2.8): switchboard feature `coaching_tree_hiring`,
off by default.

When active, engines/coaching_carousel.hiring_cycle adds `candidate_term` to head-coach and coordinator candidates'
scores before its seeded pick, and a new head coach who brings his own coordinator may bring an out-of-work protégé
(`follow_candidates`, hired as staff_moves source REHIRED) instead of the invented HC_STAFF stand-in. In shadow the
picks are made as today and the carousel reports where the tree terms would have picked someone else. Off: the
carousel is unchanged, byte for byte.

Pure apart from `tree_mode` and `mentor_records` (database reads in a savepoint; a missing table reads as off/empty).
The terms are estimates for review; separate from the owner's own hiring term (engines/owner_policy.hiring_key).
"""
from contextlib import contextmanager
import uuid

from personality import coaching_trees as ct

FEATURE = 'coaching_tree_hiring'
TREE_PEDIGREE = 0.10   # HC candidate whose mentor has a winning career record or a Super Bowl win (estimate)
TREE_REUNION = 0.20    # coordinator opening: an out-of-work protégé of the hiring team's head coach (estimate)
FOLLOW_SCORE = 0.30    # a new head coach's out-of-work protégé, against the invented HC_STAFF stand-in at 0 (estimate)
TREE_CAP = 0.20


@contextmanager
def _savepoint(cur):
    name = 'tree_hiring_' + uuid.uuid4().hex
    cur.execute(f'SAVEPOINT {name}')
    try:
        yield
    except BaseException:
        cur.execute(f'ROLLBACK TO SAVEPOINT {name}')
        cur.execute(f'RELEASE SAVEPOINT {name}')
        raise
    else:
        cur.execute(f'RELEASE SAVEPOINT {name}')


def tree_mode(cur) -> str:
    """'off' | 'shadow' | 'active' from the personality switchboard for the loaded league; missing tables, no
    league or any read error -> 'off'."""
    try:
        with _savepoint(cur):
            cur.execute("SELECT to_regclass('league_state') IS NOT NULL")
            row = cur.fetchone()
            if not row or not row[0]:
                return 'off'
            cur.execute('SELECT madden_league_id FROM league_state WHERE id = 1')
            row = cur.fetchone()
            if not row or row[0] is None:
                return 'off'
            from personality import features
            mode = features.mode(cur, int(row[0]), FEATURE)
    except Exception:  # noqa: BLE001 - the carousel never fails on the switchboard
        return 'off'
    return mode if mode in ('off', 'shadow', 'active') else 'off'


def mentor_records(cur) -> dict:
    """{person_key: {wins, losses, super_bowl_wins}} from the coach_records view ({} when it can't be read)."""
    try:
        with _savepoint(cur):
            cur.execute('SELECT coach_name, career_wins, career_losses, super_bowl_wins FROM coach_records')
            rows = cur.fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {ct.person_key(n): {'wins': w or 0, 'losses': l or 0, 'super_bowl_wins': sb or 0} for n, w, l, sb in rows}


def _pk(candidate):
    name = candidate.get('name')
    return ct.person_key(name) if name else None


def _mentors(graph, pk):
    return {e['mentor_pk'] for e in graph.get('edges') or [] if e['protege_pk'] == pk}


def _proteges(graph, pk):
    return {e['protege_pk'] for e in graph.get('edges') or [] if e['mentor_pk'] == pk}


def _winning(rec):
    return bool(rec) and (rec.get('super_bowl_wins', 0) > 0 or rec.get('wins', 0) > rec.get('losses', 0))


def candidate_term(candidate, *, graph, hiring_hc_pk=None, mentor_records) -> float:
    """The tree term for one carousel candidate, |x| <= TREE_CAP. Head-coach openings (hiring_hc_pk None): +TREE_PEDIGREE
    when one of his mentors has a winning career record or a Super Bowl win. Coordinator openings (hiring_hc_pk the
    hiring team's head coach): +TREE_REUNION for a REHIRED (out-of-work) protégé of that head coach. Candidates
    without a name (promotion, outsider) get 0."""
    pk = _pk(candidate)
    if pk is None:
        return 0.0
    x = 0.0
    if hiring_hc_pk is None:
        if any(_winning((mentor_records or {}).get(m)) for m in _mentors(graph, pk)):
            x += TREE_PEDIGREE
    elif candidate.get('kind') == 'REHIRED' and pk in _proteges(graph, hiring_hc_pk):
        x += TREE_REUNION
    return round(max(-TREE_CAP, min(TREE_CAP, x)), 4)


def follow_candidates(hc_pk, out_of_work, *, graph, role) -> list[dict]:
    """Carousel pool entries (kind 'FOLLOWED', score FOLLOW_SCORE) for the out-of-work coordinators who are the new
    head coach's protégés and last held `role`. out_of_work: [{coordinator_id, name, role, team_id, ...}]."""
    mine = _proteges(graph, hc_pk)
    out = []
    for c in sorted(out_of_work, key=lambda c: c['coordinator_id']):
        if c.get('role') == role and ct.person_key(c['name']) in mine:
            out.append(dict(c, key=f"follow:{c['coordinator_id']}", kind='FOLLOWED', score=FOLLOW_SCORE))
    return out


def adjusted(pool, *, graph, hiring_hc_pk=None, records=None) -> list[dict]:
    """Copies of the pool entries with candidate_term added to their scores."""
    return [dict(c, score=round(c['score'] + candidate_term(c, graph=graph, hiring_hc_pk=hiring_hc_pk,
                                                             mentor_records=records or {}), 4)) for c in pool]
