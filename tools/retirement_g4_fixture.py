"""G4 restore-proof fixtures (CLOSE_OUT_CHECKLIST P1-07, RETIREMENT_G4_RUNBOOK session 3).

Offline: reads reader snapshots and a fixture config, asks the default-deny planner
(`pipeline.retirement_edits.plan_retirement_edits`) for the restore edits under an in-memory fixture
authority (the planner checks the authority's shape, not JR's approval — `load_proof` owns that), and writes
one `edits.json` per branch for `pipeline/save_reader/save_writer.mjs`. It opens no save, writes only the
edits/plan files and has no DSN flag.

  python3 -m tools.retirement_g4_fixture <config.json> [--out-dir DIR]

Config (JSON):

  {"fixture_id": "g4-2026-11-09", "schema_version": 6,
   "branches": [{"name": "start", "stage": "OFFSEASON_START",
                 "snapshot": "<reader snapshot of the source save>",
                 "source_save": "<the save the edits will be written to>",
                 "players": [{"pgid": 10697, "baseline_status": "Signed",
                              "baseline_team_index": 26, "why": "expired contract"}]}]}

A branch with `"contract_from": "<snapshot before the retirement>"` also restores by signing: its players'
baseline must be `FreeAgent`/32, and the tool writes `<branch>.sign.edits.json` (the save_writer `sign`
moves) with each player's remaining contract on the team he retired from. Apply it as two writes to one
copy: `<branch>.edits.json` first (Retired → FreeAgent), then `<branch>.sign.edits.json`. A contract source
taken during the season (its `current_year` is that season) needs `"contract_skip_years": 1` so the block
starts at the following league year.

Each player's baseline is the pre-retirement status/team the restore returns to; it must be explicit because
the saved stops after a native retirement no longer carry it (session-2 census: Madden clears TeamIndex and
the PlayerContract row at the retirement advance). Exit 1 when any branch has a planner conflict.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from pipeline import retirement_edits as re_
from pipeline.league_office import stage_and_wave

AUTHORITY_VERSION = re_.AUTHORITY_VERSION
ACTION = 'KEEP_NATIVE_RETIREE'
ACTIVE = re_.ACTIVE_STATUSES


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def sign_block(contract: dict, current_year: int) -> dict:
    """The remaining-year PlayerContract block for a signed restore (the `step_restore_retirees` shape)."""
    remaining = int(contract['length']) - int(current_year)
    if remaining < 1:
        raise ValueError(f'contract has no year left to restore (length {contract["length"]}, '
                         f'current_year {current_year})')
    block = {'Length': remaining, 'CurrentYear': 0}
    salary = contract.get('salary_by_year') or []
    bonus = contract.get('bonus_by_year') or []
    for i in range(remaining):
        block[f'SalaryYear{i}'] = salary[current_year + i] if current_year + i < len(salary) else 0
    for i in range(max(0, len(bonus) - current_year)):
        block[f'BonusYear{i}'] = bonus[current_year + i]
    if contract.get('void_years'):
        block['VoidYears'] = int(contract['void_years'])
    return block


def _signed_restores(branch: dict) -> list[dict]:
    """The save_writer `sign` moves for a signed branch: each player's remaining contract on his old team."""
    prior = json.loads(Path(branch['contract_from']).read_text(encoding='utf-8'))
    prior_by_pgid = {p.get('pgid'): p for p in prior.get('players') or ()}
    signs = []
    for entry in branch['players']:
        pgid = int(entry['pgid'])
        if entry['baseline_status'] != 'FreeAgent' or int(entry['baseline_team_index']) != 32:
            raise ValueError(f"{branch['name']}: pgid {pgid} must restore to FreeAgent/32 before signing")
        before = prior_by_pgid.get(pgid)
        if before is None or not before.get('contract'):
            raise ValueError(f"{branch['name']}: pgid {pgid} has no contract in {branch['contract_from']}")
        if not 0 <= int(before['team_index']) < 32:
            raise ValueError(f"{branch['name']}: pgid {pgid} has no old team in {branch['contract_from']}")
        skip = int(branch.get('contract_skip_years') or 0)
        signs.append({'pgid': pgid, 'team_index': int(before['team_index']), 'list': 'Roster',
                      'contract': sign_block(before['contract'],
                                             int(before['contract'].get('current_year') or 0) + skip)})
    return signs


def plan_branch(branch: dict, *, fixture_id: str, schema_version: int) -> dict:
    """Planner snapshot, decisions, authority and plan for one branch (pure: files named by the config are read)."""
    snapshot = json.loads(Path(branch['snapshot']).read_text(encoding='utf-8'))
    stop, wave = stage_and_wave(snapshot)
    if stop != branch['stage']:
        raise ValueError(f"{branch['name']}: snapshot is {stop}, config says {branch['stage']}")
    by_pgid = {p.get('pgid'): p for p in snapshot.get('players') or ()}
    source_hash = _sha256(branch['source_save'])
    baseline, decisions = {}, []
    for entry in branch['players']:
        pgid = int(entry['pgid'])
        player = by_pgid.get(pgid)
        if player is None:
            raise ValueError(f"{branch['name']}: pgid {pgid} not in the snapshot")
        if player.get('status') != 'Retired':
            raise ValueError(f"{branch['name']}: pgid {pgid} is {player.get('status')!r}, not Retired")
        if entry['baseline_status'] not in ACTIVE:
            raise ValueError(f"{branch['name']}: pgid {pgid} baseline {entry['baseline_status']!r} is not active")
        team = int(entry['baseline_team_index'])
        if not 0 <= team < 33:
            raise ValueError(f"{branch['name']}: pgid {pgid} baseline team {team} is not a team index")
        baseline[str(pgid)] = {'status': entry['baseline_status'], 'team_index': team}
        decisions.append({'decision_id': f"fixture:{branch['name']}:{pgid}", 'pgid': pgid,
                          'action': ACTION, 'state': 'AVAILABLE'})
    authority = {'version': AUTHORITY_VERSION, 'fixture_id': fixture_id, 'source_save_hash': source_hash,
                 'schema_version': schema_version, 'stages': [branch['stage']], 'exclusive_owner': True,
                 'actions': {ACTION: {'proven': True, 'proof_id': f'fixture:{branch["name"]}',
                                      'fields': sorted(re_.RESTORE_FIELDS)}}}
    snap = {'source_save_hash': source_hash, 'stage': branch['stage'], 'schema_version': schema_version,
            'players': [{'pgid': p.get('pgid'), 'status': p.get('status'), 'team_index': p.get('team_index')}
                        for p in snapshot.get('players') or ()],
            'baseline': baseline, 'pending': {}}
    plan = re_.plan_retirement_edits(snap, decisions, authority)
    signs = _signed_restores(branch) if branch.get('contract_from') else None
    return {'branch': branch['name'], 'stage': branch['stage'], 'source_save': branch['source_save'],
            'source_save_hash': source_hash, 'baseline': baseline,
            'edits': plan['edits'], 'conflicts': plan['conflicts'],
            'sign': ({'sign': signs} if signs else None)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog='python3 -m tools.retirement_g4_fixture',
                                 description='Build G4 restore edits with an in-memory fixture authority (offline).')
    ap.add_argument('config', help='fixture config JSON')
    ap.add_argument('--out-dir', default='verify_output/retirement_g4/restore')
    args = ap.parse_args(argv)
    config = json.loads(Path(args.config).read_text(encoding='utf-8'))
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    bad = 0
    for branch in config['branches']:
        result = plan_branch(branch, fixture_id=config['fixture_id'],
                             schema_version=int(config['schema_version']))
        (out / f"{result['branch']}.edits.json").write_text(
            json.dumps(result['edits'], indent=1, sort_keys=True) + '\n', encoding='utf-8')
        (out / f"{result['branch']}.plan.json").write_text(
            json.dumps(result, indent=1, sort_keys=True) + '\n', encoding='utf-8')
        edits = (result['edits'] or {}).get('players') or []
        print(f"{result['branch']}: {result['stage']} {len(edits)} edits, "
              f"{len(result['conflicts'])} conflicts -> {out / (result['branch'] + '.edits.json')}")
        if result['sign']:
            (out / f"{result['branch']}.sign.edits.json").write_text(
                json.dumps(result['sign'], indent=1, sort_keys=True) + '\n', encoding='utf-8')
            for s in result['sign']['sign']:
                print(f"   sign {s['pgid']} -> team {s['team_index']} {s['contract']}")
        for e in edits:
            print(f"   {e['pgid']}: {e['set']}")
        for c in result['conflicts']:
            print(f"   CONFLICT {c['pgid']}: {', '.join(c['reasons'])}", file=sys.stderr)
            bad = 1
    return bad


if __name__ == '__main__':
    sys.exit(main())
