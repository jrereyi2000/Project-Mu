"""Default-deny retirement save-edit planner (PERSONALITY_RETIREMENT_PLAN.md §2.6, U5).

Pure: plans `save_writer.mjs` "players" edits from explicit dictionaries; never opens a save, launches a binary,
reads a database or writes anything. Not wired into the league office (the U13 integration is a separate, gated
step; until then `pipeline/league_office.py` keep_retired stays the only restore writer).

  plan_retirement_edits(snapshot, decisions, authority) -> {edits, preconditions, expected, conflicts}

snapshot:  {source_save_hash, stage, schema_version, players: [{pgid, status, team_index, ...}],
            baseline: {pgid: pre-native image {status, team_index, ...}}, pending: save_writer-shaped pending edits
            {release: [pgid], sign: [{pgid}], trade: [{pgids}], players: [{pgid, set}]}}
decisions: [{decision_id, pgid, action KEEP_NATIVE_RETIREE | RETIRE_ACTIVE, state, operation_id?}]
authority: a versioned authority fixture {version AUTHORITY_VERSION, fixture_id, source_save_hash, schema_version,
            stages [...], exclusive_owner true, actions {ACTION: {proven true, proof_id, fields [...]}}}

Default deny: no authority, an unsupported version, another save hash / stage / schema, a non-exclusive restoration
owner (e.g. the Contract Retirement Arbitrator still installed) or an unproven action blocks every affected decision
with a CONFLICT reason. KEEP_NATIVE_RETIREE and RETIRE_ACTIVE are proven separately: restoration proof never covers
retiring an active player. Per decision: a missing or ambiguous pgid, a Deleted record (recovery is not promised), a
missing pre-native baseline, an unexpected current status, a career state that does not allow the action, an
overlap with any pending release/sign/trade/player edit for the person, or a field the authority does not cover is
a CONFLICT. A restore copies the baseline's own status and team (an unexpired contract is not defaulted to
FreeAgent); release is never used as retirement. Only conflict-free decisions produce edits.
"""
from __future__ import annotations

AUTHORITY_VERSION = 'retirement-authority-v1'
ACTIONS = ('KEEP_NATIVE_RETIREE', 'RETIRE_ACTIVE')
KEEP_STATES = frozenset({'AVAILABLE', 'SIGN_PENDING', 'RETURNING', 'UNSIGNED_RETURN_ELIGIBLE'})
ACTIVE_STATUSES = frozenset({'Signed', 'FreeAgent', 'PracticeSquad'})
# Save field -> snapshot/baseline key for a restore.
RESTORE_FIELDS = {'ContractStatus': 'status', 'TeamIndex': 'team_index'}


def _authority_blocks(snapshot, authority):
    """Reasons that block every decision, and the per-action proof map."""
    if not authority:
        return ['authority_missing'], {}
    if authority.get('version') != AUTHORITY_VERSION:
        return [f'authority_version_unsupported:{authority.get("version")}'], {}
    reasons = []
    if authority.get('source_save_hash') != snapshot.get('source_save_hash'):
        reasons.append('source_hash_mismatch')
    if snapshot.get('stage') not in (authority.get('stages') or ()):
        reasons.append(f'stage_mismatch:{snapshot.get("stage")}')
    if authority.get('schema_version') != snapshot.get('schema_version'):
        reasons.append('schema_mismatch')
    if authority.get('exclusive_owner') is not True:
        reasons.append('restoration_owner_not_exclusive')
    return reasons, authority.get('actions') or {}


def _pending_moves(pending):
    moves = {}

    def note(pgid, kind):
        moves.setdefault(pgid, set()).add(kind)
    for g in pending.get('release') or ():
        note(g, 'release')
    for s in pending.get('sign') or ():
        note((s or {}).get('pgid'), 'sign')
    for t in pending.get('trade') or ():
        for g in (t or {}).get('pgids') or ():
            note(g, 'trade')
    for e in pending.get('players') or ():
        note((e or {}).get('pgid'), 'player_edit')
    return moves


def _players_by_pgid(snapshot):
    by = {}
    for p in snapshot.get('players') or ():
        by.setdefault(p.get('pgid'), []).append(p)
    return by


def plan_retirement_edits(snapshot, decisions, authority):
    global_blocks, proofs = _authority_blocks(snapshot, authority or {})
    by_pgid = _players_by_pgid(snapshot)
    baseline = {int(k) if isinstance(k, str) and k.isdigit() else k: v
                for k, v in (snapshot.get('baseline') or {}).items()}
    pending = _pending_moves(snapshot.get('pending') or {})
    seen = {}
    for d in decisions:
        seen[d.get('pgid')] = seen.get(d.get('pgid'), 0) + 1
    edits, expected, conflicts = [], {}, []
    preconditions = {'source_save_hash': snapshot.get('source_save_hash'), 'stage': snapshot.get('stage'),
                     'schema_version': snapshot.get('schema_version'),
                     'authority_fixture': (authority or {}).get('fixture_id'), 'players': {}}
    for d in sorted(decisions, key=lambda x: (str(x.get('pgid')), str(x.get('decision_id')))):
        pgid, action = d.get('pgid'), d.get('action')
        reasons = list(global_blocks)
        if action not in ACTIONS:
            conflicts.append({'decision_id': d.get('decision_id'), 'pgid': pgid, 'action': action,
                              'reasons': [f'unknown_action:{action}']})
            continue
        proof = proofs.get(action) or {}
        if not global_blocks and proof.get('proven') is not True:
            reasons.append(f'action_unproven:{action}')
        rows = by_pgid.get(pgid, [])
        if seen[pgid] > 1:
            reasons.append('pgid_in_several_decisions')
        if not rows:
            reasons.append('pgid_missing')
        elif len(rows) > 1:
            reasons.append('pgid_ambiguous')
        player = rows[0] if len(rows) == 1 else None
        base = baseline.get(pgid)
        if player is not None and player.get('status') == 'Deleted':
            reasons.append('deleted_unrecoverable')
        if base is None:
            reasons.append('baseline_missing')
        for move in sorted(pending.get(pgid, ())):
            reasons.append(f'overlaps_{move}')
        state = d.get('state')
        if action == 'KEEP_NATIVE_RETIREE':
            if player is not None and player.get('status') not in ('Retired', 'Deleted'):
                reasons.append(f'not_natively_retired:{player.get("status")}')
            if state not in KEEP_STATES:
                reasons.append(f'state_does_not_allow_keep:{state}')
            want = {f: base.get(k) for f, k in RESTORE_FIELDS.items()} if base else {}
            if base is not None and base.get('status') in (None, 'Retired', 'Deleted'):
                reasons.append('baseline_not_active')
        else:
            if player is not None and player.get('status') not in ACTIVE_STATUSES:
                reasons.append(f'not_active:{player.get("status")}')
            if state != 'RETIRE_PENDING':
                reasons.append(f'state_not_retire_pending:{state}')
            elif not d.get('operation_id'):
                reasons.append('operation_id_missing')
            want = {'ContractStatus': 'Retired'}
        allowed = set(proof.get('fields') or ())
        if not global_blocks and proof.get('proven') is True:
            for f in sorted(set(want) - allowed):
                reasons.append(f'field_unauthorized:{f}')
        if reasons:
            conflicts.append({'decision_id': d.get('decision_id'), 'pgid': pgid, 'action': action,
                              'reasons': reasons})
            continue
        edits.append({'pgid': pgid, 'set': dict(want)})
        expected[pgid] = dict(want)
        preconditions['players'][pgid] = {'status': player.get('status'), 'decision_id': d.get('decision_id'),
                                          'operation_id': d.get('operation_id'), 'proof_id': proof.get('proof_id')}
    return {'edits': {'players': edits} if edits else {}, 'preconditions': preconditions, 'expected': expected,
            'conflicts': conflicts}
