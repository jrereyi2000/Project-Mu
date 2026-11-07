"""Owner wiring context (docs/in_progress/OWNERS_SCOUTS_VOTERS_PLAN.md §2.3, U5): the shared reads behind the four
owner features, so the engines that use them (coach_hot_seat, gm_rollover, coaching_carousel, transactions_engine,
the league office's personality layer and the inbox OWNER kind) stay small.

  feature_mode(cur, feature) -> 'off' | 'shadow' | 'active'   the switchboard mode for league_state's league;
                                                              missing tables, no league or any read error -> 'off'
  owner_views(directory=PROFILE_DIR) -> {abbr: view}          the reviewed (approved / edited) owner profiles as
                                                              {key, team, name, traits, sliders}
  team_owner_views(cur, *, exclude=()) -> {team_id: view}     by teams.abbreviation; `exclude` drops team ids
  user_team_ids(cur) -> {team_id}                             gm_team_settings.user_controlled (JR's teams)
  patience_terms(cur) -> {team_id: owner_policy.patience}     CPU teams only
  vetoes_used(cur, season, team_id) -> int                    VETO rows in owner_decisions (0 without the table)
  record_decision(cur, *, season, tick, team_id, move_kind, move_ref, decision, alternative=None) -> bool

The owner's view is his reviewed profile file (personality/owner_profiles.core_initial_values: the effective
sliders and traits, views-v2). team_owners and owner_decisions come from the owners plan's U6 migration (held):
until it exists the owner features can't leave 'off' in the switchboard (personality/features.REQUIREMENTS), and
record_decision / vetoes_used read and write nothing. JR's teams never get an owner term: every helper that
returns per-team terms leaves them out, and meddling never reviews their moves (owner_policy.meddle_options).
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import uuid

FEATURES = ('owner_patience', 'owner_hiring', 'owner_spending', 'owner_meddling')
MODES = ('off', 'shadow', 'active')


@contextmanager
def _savepoint(cur):
    name = 'owner_ctx_' + uuid.uuid4().hex
    cur.execute(f'SAVEPOINT {name}')
    try:
        yield
    except BaseException:
        cur.execute(f'ROLLBACK TO SAVEPOINT {name}')
        cur.execute(f'RELEASE SAVEPOINT {name}')
        raise
    else:
        cur.execute(f'RELEASE SAVEPOINT {name}')


def _regclass(cur, table):
    cur.execute('SELECT to_regclass(%s) IS NOT NULL', (table,))
    row = cur.fetchone()
    return bool(row and row[0])


def feature_mode(cur, feature) -> str:
    """'off' | 'shadow' | 'active' for an owner feature (personality/features.py) for the loaded league."""
    if feature not in FEATURES:
        raise ValueError(f'not an owner feature: {feature!r}')
    try:
        with _savepoint(cur):
            if not _regclass(cur, 'league_state'):
                return 'off'
            cur.execute('SELECT madden_league_id FROM league_state WHERE id = 1')
            row = cur.fetchone()
            if not row or row[0] is None:
                return 'off'
            from personality import features
            mode = features.mode(cur, int(row[0]), feature)
    except Exception:  # noqa: BLE001 - an engine never fails on the switchboard
        return 'off'
    return mode if mode in MODES else 'off'


def owner_views(directory=None) -> dict:
    """{abbr: {key, team, name, traits, sliders}} for every reviewed owner profile; drafts and unreadable files
    are left out (that team's owner is neutral)."""
    from personality import owner_profiles as op
    out = {}
    for doc in op.load_profiles(directory or op.PROFILE_DIR):
        try:
            values = op.core_initial_values(doc)
        except Exception:  # noqa: BLE001 - not reviewed or malformed: neutral owner
            continue
        team = str(doc.get('team') or '').upper()
        if team:
            out[team] = {'key': doc.get('key'), 'team': team, 'name': doc.get('name'),
                         'traits': list(values['traits']), 'sliders': dict(values['sliders'])}
    return out


def team_owner_views(cur, *, exclude=(), views=None) -> dict:
    """{team_id: owner view} for teams with a reviewed owner profile, without the team ids in `exclude`."""
    views = owner_views() if views is None else views
    cur.execute('SELECT id, abbreviation FROM teams WHERE abbreviation IS NOT NULL')
    skip = set(exclude or ())
    return {tid: views[str(abbr).upper()] for tid, abbr in cur.fetchall()
            if tid not in skip and str(abbr).upper() in views}


def user_team_ids(cur) -> set:
    """JR's teams (gm_team_settings.user_controlled); empty when the table is missing."""
    try:
        with _savepoint(cur):
            if not _regclass(cur, 'gm_team_settings'):
                return set()
            cur.execute('SELECT team_id FROM gm_team_settings WHERE user_controlled')
            return {r[0] for r in cur.fetchall()}
    except Exception:  # noqa: BLE001
        return set()


def patience_terms(cur, *, views=None) -> dict:
    """{team_id: {hot_seat_line_scale, min_tenure_delta}} for CPU teams with a reviewed owner."""
    from engines import owner_policy as op
    by_team = team_owner_views(cur, exclude=user_team_ids(cur), views=views)
    return {tid: op.patience(v) for tid, v in by_team.items()}


def vetoes_used(cur, season, team_id) -> int:
    try:
        with _savepoint(cur):
            if not _regclass(cur, 'owner_decisions'):
                return 0
            cur.execute("SELECT count(*) FROM owner_decisions WHERE season = %s AND team_id = %s "
                        "AND decision = 'VETO'", (season, team_id))
            row = cur.fetchone()
            return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001
        return 0


def record_decision(cur, *, season, tick, team_id, move_kind, move_ref, decision, alternative=None) -> bool:
    """Store a VETO / PUSH (or APPROVE) in owner_decisions (idempotent on its unique key). False when the table
    doesn't exist yet (U6's migration) or the write fails; the caller's decision stands either way."""
    try:
        with _savepoint(cur):
            if not _regclass(cur, 'owner_decisions'):
                return False
            cur.execute("""INSERT INTO owner_decisions (season, tick, team_id, move_kind, move_ref, decision,
                                                        alternative)
                           VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s::jsonb) ON CONFLICT DO NOTHING""",
                        (season, tick, team_id, move_kind, json.dumps(move_ref, sort_keys=True, default=str),
                         decision, None if alternative is None else json.dumps(alternative, sort_keys=True,
                                                                               default=str)))
            return True
    except Exception:  # noqa: BLE001
        return False
