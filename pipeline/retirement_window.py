"""Retirement window: one retirement decision per offseason (docs/in_progress/RETIREMENT_WINDOW_PLAN.md §2.2–2.6, U1).

Pure: no database, subprocess, save or league office import. The league office (U3) calls it.

Project Mu decides every retirement for season S once, at the decision stage (the first offseason stop with a
WRITTEN league office run, normally OFFSEASON_START). At every later stop through CUTDOWN, `classify` compares the
reader snapshot with that decision set and the previous run's roster image:

  DECIDED_RETIRED     Retired now, decided keep False                  -> decided_retired (left alone)
  LATE_NATIVE         Retired now, not decided                         -> restore
  RE_RETIRED          Retired now, decided keep True, restores < max   -> restore
  RE_RETIRED_AGAIN    as above, restores >= MAX_WINDOW_RESTORES        -> conflicts
  NO_BASELINE         restore candidate without an active prior image  -> conflicts
  STILL_RETIRED       restore candidate already Retired in the prior image (reported at an earlier stop, e.g.
                      blocked without a proof)                         -> carried (reported once, not re-raised)
  VANISHED_ROSTERED   active on a team in the prior image, absent now  -> vanished (unrecoverable)
  VANISHED_FREE_AGENT active free agent in the prior image, absent now -> vanished (may be a free-agent purge)
  PURGED              Retired in the prior image, absent now           -> purged (expected)

Restores go through pipeline.retirement_edits.plan_retirement_edits (default deny): `restore_requests` and
`planner_snapshot` build its inputs, and `authority_for_run` stamps a JR-approved proof file (env
MU_RETIREMENT_AUTHORITY, read by `load_proof`) onto the current run. Without a proof every restore is blocked
`authority_missing` (observe-only until the G4 restore proof).
"""
from __future__ import annotations

import json
import os

WINDOW_VERSION = 'retirement-window-v1'
WINDOW_STAGES = ('RESIGN', 'FREE_AGENCY', 'COMBINE', 'PRO_DAY', 'DRAFT', 'UDFA', 'PRESEASON', 'CUTDOWN')
STOP_ORDER = ('OFFSEASON_START', 'RESIGN', 'FREE_AGENCY:1', 'FREE_AGENCY:2', 'COMBINE', 'PRO_DAY', 'DRAFT', 'UDFA',
              'PRESEASON', 'CUTDOWN')
MAX_WINDOW_RESTORES = 1
ACTIVE_STATUSES = ('Signed', 'FreeAgent', 'PracticeSquad')
FREE_AGENT_TEAM_INDEX = 32
# JR (2026-11-07, plan §8 Q3): the user-team flag applies from the franchise's first season, not from 2034.
USER_TEAM_FROM_SEASON = 2026
AUTHORITY_ENV = 'MU_RETIREMENT_AUTHORITY'
PROOF_KIND = 'retirement-authority-proof-v1'
KEEP_ACTION = 'KEEP_NATIVE_RETIREE'
DEFAULT_CAREER_STATE = 'AVAILABLE'
# Planner reasons that mean "not authorised here yet" (no proof, or a stop the proof doesn't list): default priority.
OBSERVE_ONLY_REASONS = ('authority_missing', 'stage_mismatch')
_PROOF_REQUIRED = ('kind', 'version', 'fixture_id', 'schema_version', 'stages', 'exclusive_owner', 'actions',
                   'approved_by', 'approved_on')


class WindowError(ValueError):
    """A broken proof file or bad window input (loud, never a silent deny)."""


def stop_name(stage: str, wave: int | None = None) -> str:
    """The STOP_ORDER name of a league office stage (FREE_AGENCY carries its wave)."""
    if stage == 'FREE_AGENCY' and wave in (1, 2):
        return f'FREE_AGENCY:{wave}'
    return stage


def _key(pgid) -> str:
    return str(int(pgid)) if isinstance(pgid, (int, str)) and str(pgid).lstrip('-').isdigit() else str(pgid)


def roster_image(view: dict) -> dict[str, list]:
    """{pgid: (status, team_index)} -> {"<pgid>": [status, team_index]} (JSON-safe, sorted by pgid)."""
    out = {}
    for pgid in sorted(view, key=lambda g: int(g) if str(g).lstrip('-').isdigit() else 0):
        status, team = (list(view[pgid]) + [None, None])[:2]
        out[_key(pgid)] = [status, team]
    return out


def _is_active(entry) -> bool:
    return bool(entry) and entry[0] in ACTIVE_STATUSES


def classify(players: list[dict], *, stage: str, wave: int | None, season: int,
             decided: dict[str, dict], prior_image: dict[str, list] | None, prior_stop: str | None,
             window_restores: dict[str, int], user_team_indexes: frozenset[int] = frozenset()) -> dict:
    """Classify one later offseason stop (plan §2.3). `players` is the reader snapshot's players list."""
    stop = stop_name(stage, wave)
    if stop not in STOP_ORDER:
        raise WindowError(f'not an offseason stop: {stop}')
    decided = {_key(k): v for k, v in (decided or {}).items()}
    prior = {_key(k): list(v) for k, v in (prior_image or {}).items()}
    restores_done = {_key(k): int(v) for k, v in (window_restores or {}).items()}
    i = STOP_ORDER.index(stop)
    expected_prior = STOP_ORDER[i - 1] if i > 0 else None
    stage_gap = prior_stop is not None and prior_stop not in (expected_prior, stop)
    present = {}
    for p in players or ():
        if p.get('pgid') is None:
            continue
        present.setdefault(_key(p['pgid']), p)
    decided_retired, restore, conflicts, carried, vanished, purged = [], [], [], [], [], []
    for k in sorted(present, key=int):
        p = present[k]
        if p.get('status') != 'Retired':
            continue
        pgid = int(k)
        d = decided.get(k)
        if d is not None and d.get('keep') is False:
            decided_retired.append(pgid)
            continue
        if d is None:
            kind = 'LATE_NATIVE'
        elif restores_done.get(k, 0) >= MAX_WINDOW_RESTORES:
            conflicts.append({'pgid': pgid, 'kind': 'RE_RETIRED_AGAIN', 'reason': 're_retired_after_window_restore'})
            continue
        else:
            kind = 'RE_RETIRED'
        base = prior.get(k)
        if base and base[0] == 'Retired':
            carried.append({'pgid': pgid, 'kind': 'STILL_RETIRED', 'was': kind})
            continue
        if not _is_active(base):
            conflicts.append({'pgid': pgid, 'kind': 'NO_BASELINE', 'was': kind,
                              'reason': 'baseline_missing' if not base else f'baseline_not_active:{base[0]}'})
            continue
        team = base[1]
        restore.append({'pgid': pgid, 'kind': kind, 'baseline': {'status': base[0], 'team_index': team},
                        'user_team': season >= USER_TEAM_FROM_SEASON and team in user_team_indexes})
    for k in sorted(prior, key=int):
        if k in present:
            continue
        status, team = (prior[k] + [None, None])[:2]
        if status == 'Retired':
            purged.append(int(k))
        elif status in ACTIVE_STATUSES:
            kind = 'VANISHED_FREE_AGENT' if team == FREE_AGENT_TEAM_INDEX or team is None else 'VANISHED_ROSTERED'
            vanished.append({'pgid': int(k), 'kind': kind, 'last': {'status': status, 'team_index': team},
                             'user_team': (kind == 'VANISHED_ROSTERED' and season >= USER_TEAM_FROM_SEASON
                                           and team in user_team_indexes)})
    return {'version': WINDOW_VERSION, 'season': season, 'stage': stage, 'stop': stop, 'prior_stop': prior_stop,
            'stage_gap': stage_gap, 'decided_retired': decided_retired, 'restore': restore, 'conflicts': conflicts,
            'carried': carried, 'vanished': vanished, 'purged': purged}


def restore_requests(report: dict, career_states: dict[str, str] | None = None) -> list[dict]:
    """plan_retirement_edits decisions for the report's restore entries (state AVAILABLE unless given, §2.6)."""
    states = {_key(k): v for k, v in (career_states or {}).items()}
    return [{'decision_id': f'window:{report["season"]}:{r["pgid"]}', 'pgid': r['pgid'], 'action': KEEP_ACTION,
             'state': states.get(_key(r['pgid']), DEFAULT_CAREER_STATE)} for r in report.get('restore') or ()]


def planner_snapshot(players: list[dict], *, source_save_hash: str, stage: str, schema_version: int,
                     prior_image: dict[str, list], pending: dict) -> dict:
    """plan_retirement_edits' snapshot: baseline from the prior roster image, pending = moves already claimed."""
    baseline = {}
    for k, v in (prior_image or {}).items():
        status, team = (list(v) + [None, None])[:2]
        baseline[int(k)] = {'status': status, 'team_index': team}
    return {'source_save_hash': source_save_hash, 'stage': stage, 'schema_version': schema_version,
            'players': [{'pgid': p.get('pgid'), 'status': p.get('status'), 'team_index': p.get('team_index')}
                        for p in players or ()],
            'baseline': baseline, 'pending': dict(pending or {})}


def load_proof(path: str | None) -> dict | None:
    """The JR-approved proof file (§2.4), or None when no path is set. A broken file raises WindowError."""
    if not path:
        return None
    try:
        with open(path, encoding='utf-8') as fh:
            proof = json.load(fh)
    except (OSError, ValueError) as exc:
        raise WindowError(f'retirement authority proof {path} unreadable: {exc}') from exc
    if not isinstance(proof, dict):
        raise WindowError(f'retirement authority proof {path} is not an object')
    missing = [f for f in _PROOF_REQUIRED if proof.get(f) in (None, '', [], {})
               and not (f == 'exclusive_owner' and proof.get(f) is False)]
    if missing:
        raise WindowError(f'retirement authority proof {path} lacks {", ".join(missing)}')
    if proof['kind'] != PROOF_KIND:
        raise WindowError(f'retirement authority proof {path} has kind {proof["kind"]!r}, not {PROOF_KIND}')
    if not isinstance(proof['stages'], list) or not isinstance(proof['actions'], dict):
        raise WindowError(f'retirement authority proof {path}: stages must be a list and actions an object')
    if not isinstance(proof['exclusive_owner'], bool):
        raise WindowError(f'retirement authority proof {path}: exclusive_owner must be true or false')
    if (proof['actions'].get('RETIRE_ACTIVE') or {}).get('proven') is True:
        raise WindowError(f'retirement authority proof {path} lists RETIRE_ACTIVE as proven (phase 2 only)')
    return proof


def proof_from_env(environ=None) -> dict | None:
    """load_proof of $MU_RETIREMENT_AUTHORITY (unset = default deny)."""
    return load_proof((environ if environ is not None else os.environ).get(AUTHORITY_ENV))


def authority_for_run(proof: dict | None, *, source_save_hash: str, schema_version: int) -> dict | None:
    """The planner's authority for this run: the proof stamped with this run's save hash (§2.4). The proof covers a
    class of save (schema version + stages), so the planner still denies an uncovered stop or a schema change."""
    if not proof:
        return None
    return {'version': proof.get('version'), 'fixture_id': proof.get('fixture_id'),
            'source_save_hash': source_save_hash, 'schema_version': proof.get('schema_version'),
            'run_schema_version': schema_version, 'stages': list(proof.get('stages') or ()),
            'exclusive_owner': proof.get('exclusive_owner') is True, 'actions': dict(proof.get('actions') or {})}


def notice(window: dict | None) -> tuple[str, str] | None:
    """(line, priority) for the watcher's offseason notification, or None without a window summary. High priority
    when a rostered player vanished or a restore hit a conflict (restores blocked only because no proof covers this
    stop stay default)."""
    if not window:
        return None
    if window.get('role') == 'decision':
        decided = window.get('decided') or {}
        kept = sum(1 for d in decided.values() if d.get('keep'))
        return (f'Retirement window: decided {len(decided)} retirement(s) for {window.get("season")} '
                f'({kept} kept).', 'default')
    restored = window.get('restored') or []
    blocked = window.get('blocked') or []
    conflicts = window.get('conflicts') or []
    vanished = window.get('vanished') or []
    rostered = [v for v in vanished if v.get('kind') == 'VANISHED_ROSTERED']
    parts = [f'Retirement window {window.get("stop") or window.get("stage")}: {len(restored)} restored']
    if blocked:
        reasons = sorted({(b.get('reasons') or ['?'])[0] for b in blocked})
        parts.append(f'{len(blocked)} late retirement(s) not restored ({", ".join(reasons)})')
    if conflicts:
        parts.append(f'{len(conflicts)} conflict(s)')
    if rostered:
        who = ', '.join(f'{v.get("name") or v["pgid"]} (team {v["last"]["team_index"]})' for v in rostered)
        parts.append(f'{len(rostered)} rostered player(s) vanished, unrecoverable: {who}')
    if len(vanished) > len(rostered):
        parts.append(f'{len(vanished) - len(rostered)} free agent(s) vanished')
    flagged = [r for r in restored if r.get('user_team')]
    if flagged:
        parts.append(f'{len(flagged)} restored on your team (release to accept Madden\'s retirement)')
    if window.get('stage_gap'):
        parts.append(f'stop skipped since {window.get("prior_stop")}')
    high = bool(rostered or conflicts or any(
        not r.startswith(OBSERVE_ONLY_REASONS) for b in blocked for r in (b.get('reasons') or ())))
    return '; '.join(parts) + '.', 'high' if high else 'default'
