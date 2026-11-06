"""Offline retirement preview (PERSONALITY_RETIREMENT_PLAN.md §2.6, U5).

  python -m tools.retirement_preview --input fixture.json [--policy policy.json] --mode baseline|compare --out report.json
  python -m tools.retirement_preview --example fixture.json      # write the synthetic example fixture

Reads a synthetic fixture, builds each player's context (engines.retirement_context), evaluates the annual decision
(engines.retirement_policy), runs the OPEN review through the career state machine (engines.career_decision_state)
and asks the default-deny planner (pipeline.retirement_edits) what a save edit would need. `baseline` runs the
neutral policy only; `compare` adds the effects arm with the same draw. Without --policy the review-only synthetic
policy is used. There is no DSN, commit or save-path flag: it reads two JSON files, writes one, and launches nothing.
Every report is labelled REVIEW-ONLY / UNCALIBRATED: a demonstration, not a prediction of Madden outcomes.

Fixture (JSON): {"universe_id", "players": [{"snapshot": build_context snapshot, "evidence": {domain: bundle},
                 "native": {"status": "Retired" | "Signed" | ...}}],
                 "save_snapshot": retirement_edits snapshot?, "authority": authority fixture?}
"""
import argparse
import json
from pathlib import Path
import sys

from engines import career_decision_state as cs
from engines import retirement_context as rc
from engines import retirement_policy as rp
from pipeline import retirement_edits as re_

REPORT_VERSION = 'retirement-preview-v1'
MODES = ('baseline', 'compare')
LABEL = 'REVIEW-ONLY / UNCALIBRATED: synthetic demonstration, not a prediction; no save or database was touched'


class PreviewError(ValueError):
    pass


def _review_state(decision):
    if decision.intent == 'BLOCKED_DATA' and not decision.decision_id:
        return {'state': 'BLOCKED_DATA', 'reasons': ['identity_missing']}
    s = cs.new_state(decision.universe_id, decision.person_key, decision.earned_season, decision.decision_id)
    r = cs.transition(s, {'event_id': f'preview:{decision.decision_id}', 'source_event_id': 'preview:open',
                          'kind': 'ANNUAL_REVIEW', 'expected_revision': 0, 'payload': {'intent': decision.intent},
                          'actor': None})
    return {'state': r['after']['state'], 'reasons': r['reasons']}


def _native_conflict(native_status, intent):
    if native_status == 'Retired' and intent == 'AVAILABLE':
        return 'KEEP_NATIVE_RETIREE'
    if native_status in re_.ACTIVE_STATUSES and intent == 'RETIRE_INTENT':
        return 'RETIRE_ACTIVE'
    return None


def _arm(decision):
    return {'intent': decision.intent, 'final_p': decision.final_p, 'base_p': decision.base['p'],
            'terms': decision.base['terms'], 'modifiers': decision.modifiers, 'warnings': list(decision.warnings),
            'input_hash': decision.input_hash}


def build_report(fixture, policy, mode):
    if mode not in MODES:
        raise PreviewError(f'mode must be one of {MODES}')
    if not isinstance(fixture, dict) or not isinstance(fixture.get('players'), list):
        raise PreviewError('fixture needs a players list')
    rows, candidates = [], []
    for i, entry in enumerate(fixture['players']):
        snap, evidence = entry.get('snapshot') or {}, entry.get('evidence') or {}
        native = (entry.get('native') or {}).get('status')
        row = {'index': i, 'person_key': snap.get('person_key'), 'pgid': snap.get('pgid'),
               'native_observation': native}
        try:
            neutral_ctx = rc.build_context(snap, evidence, feature_mode='neutral_preview')
        except rc.ContextError as e:
            row.update(status='context_error', error=str(e))
            rows.append(row)
            continue
        neutral = rp.evaluate_annual(neutral_ctx, policy, features_enabled=False)
        row.update(status='evaluated', coverage={d: v['coverage'] for d, v in neutral_ctx.evidence['domains'].items()},
                   evidence_warnings=list(neutral_ctx.evidence['warnings']), draw=neutral.draw,
                   draw_hex=neutral.draw_hex, decision_id=neutral.decision_id, neutral=_arm(neutral),
                   state=_review_state(neutral))
        if mode == 'compare':
            effects = rp.evaluate_annual(rc.build_context(snap, evidence, feature_mode='effects'), policy,
                                         features_enabled=True)
            assert effects.draw == neutral.draw
            row['effects'] = _arm(effects)
            row['effects_state'] = _review_state(effects)
        action = _native_conflict(native, neutral.intent)
        row['native_conflict'] = action
        if action:
            candidates.append({'decision_id': neutral.decision_id, 'pgid': snap.get('pgid'), 'action': action,
                               'state': row['state']['state']})
        rows.append(row)
    plan = re_.plan_retirement_edits(fixture.get('save_snapshot') or {}, candidates, fixture.get('authority'))
    blocks = {c['decision_id']: c['reasons'] for c in plan['conflicts']}
    for row in rows:
        if row.get('native_conflict'):
            row['authority_blocks'] = blocks.get(row['decision_id'], [])
    counts = {'players': len(rows), 'context_errors': sum(r['status'] == 'context_error' for r in rows)}
    for arm in ('neutral', 'effects'):
        for r in rows:
            if arm in r:
                key = f'{arm}_{r[arm]["intent"].lower()}'
                counts[key] = counts.get(key, 0) + 1
    counts['native_conflicts'] = len(candidates)
    counts['planned_edits'] = len((plan['edits'] or {}).get('players', []))
    return {'report_version': REPORT_VERSION, 'label': LABEL, 'mode': mode,
            'policy': {'version': policy.version, 'review_only': policy.review_only,
                       'calibrated': False},
            'universe_id': fixture.get('universe_id'), 'counts': counts, 'players': rows,
            'planner': {'edits': plan['edits'], 'conflicts': plan['conflicts'], 'expected': plan['expected']}}


def _bundle(universe, data, coverage='complete'):
    return {'version': rc.EVIDENCE_VERSION, 'universe_id': universe, 'source_event_ids': ['synthetic:1'],
            'as_of': {'season': 2027, 'tick': 30}, 'coverage': coverage, 'fingerprint': 'synthetic', 'data': data}


def example_fixture():
    """Fictional players (no real people), synthetic evidence."""
    u = 'u-example'
    players = []
    specs = [('example:qb-veteran', 1001, 37, 'QB', 84, 'Signed', ['LEGACY_BUILDER'], True),
             ('example:rb-worn', 1002, 31, 'HB', 70, 'Retired', ['COASTING'], False),
             ('example:wr-prime', 1003, 28, 'WR', 88, 'Signed', [], True),
             ('example:ol-unsigned', 1004, 34, 'LT', 66, 'FreeAgent', ['RING_CHASER'], False),
             ('example:cb-young', 1005, 23, 'CB', 62, 'FreeAgent', [], False)]
    for key, pgid, age, pos, ovr, native, traits, contract in specs:
        players.append({
            'snapshot': {'universe_id': u, 'person_key': key, 'identity_status': 'verified', 'player_id': pgid,
                         'pgid': pgid, 'earned_season': 2027, 'decision_season': 2028, 'age': age,
                         'age_status': 'known', 'position': pos},
            'evidence': {'ability': _bundle(u, {'base_overall': ovr}),
                         'health': _bundle(u, {'missed_games': 4 if pos == 'HB' else 1, 'games_possible': 17}),
                         'opportunity': _bundle(u, {'has_contract': contract, 'offer_ids': [],
                                                    'market_open': not contract}),
                         'career': _bundle(u, {'verified_seasons': max(1, age - 22)}),
                         'goals': _bundle(u, [{'goal_id': 'pass_td_record', 'kind': 'record',
                                               'current_value': 520, 'target_value': 540,
                                               'projected_range': [530, 560], 'coverage': 'complete'}]
                                          if pos == 'QB' else []),
                         'profile': _bundle(u, {'status': 'stored', 'traits': traits, 'sliders': {}})},
            'native': {'status': native}})
    save = {'source_save_hash': 'sha256:example', 'stage': 'OFFSEASON_START', 'schema_version': 'm27',
            'players': [{'pgid': p['snapshot']['pgid'], 'status': p['native']['status']} for p in players],
            'baseline': {str(p['snapshot']['pgid']): {'status': 'Signed', 'team_index': 4} for p in players},
            'pending': {}}
    return {'universe_id': u, 'players': players, 'save_snapshot': save, 'authority': None}


def _dump(obj):
    return json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m tools.retirement_preview', description=__doc__.split('\n\n')[0])
    ap.add_argument('--input', help='synthetic fixture JSON')
    ap.add_argument('--policy', help='policy JSON (default: the review-only synthetic policy)')
    ap.add_argument('--mode', choices=MODES, default='baseline')
    ap.add_argument('--out', help='report JSON path')
    ap.add_argument('--example', help='write the synthetic example fixture to this path and exit')
    args = ap.parse_args(argv)
    if args.example:
        Path(args.example).write_text(_dump(example_fixture()), encoding='utf-8')
        return 0
    if not args.input or not args.out:
        ap.error('--input and --out are required')
    try:
        fixture = json.loads(Path(args.input).read_text(encoding='utf-8'))
        policy = (rp.policy_from_dict(json.loads(Path(args.policy).read_text(encoding='utf-8')))
                  if args.policy else rp.synthetic_policy())
        report = build_report(fixture, policy, args.mode)
    except (PreviewError, rp.RetirementPolicyError, json.JSONDecodeError, OSError) as e:
        print(f'retirement_preview: {e}', file=sys.stderr)
        return 2
    Path(args.out).write_text(_dump(report), encoding='utf-8')
    c = report['counts']
    print(f'{LABEL}\n{c["players"]} players, {c["native_conflicts"]} native conflicts, '
          f'{c["planned_edits"]} planned edits -> {args.out}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
