"""Offline player trade-request preview (PERSONALITY_TRADE_REQUESTS_PLAN.md §2.7, U3).

  python -m tools.player_trade_request_preview --input PATH --out DIR [--mode disabled|shadow]
  python -m tools.player_trade_request_preview --example PATH      # write the synthetic 2026-2035 example input

Reads schema-v1 synthetic input (ordered checkpoint reviews and lifecycle actions per player plus an explicit
policy), runs every player twice through engines.player_trade_requests: once with the player's own profile view
and once with the neutral missing-profile view (matched control), and writes a deterministic report.json and
README.md. No database, DSN, environment, save path or model call; ENABLED mode is rejected. The reports are
synthetic demonstrations, not Madden outcomes or calibration evidence, and the illustrative odds are not a
production policy.

Input (JSON):
  {"schema_version": 1, "policy": RequestPolicy, "sensitivity": [{"name", "policy"}]?,
   "players": [{"name", "persona_id", "profile_view": feature_view dict,
                "steps": [{"clock": RequestClock, "context": RequestContext without persona_id/profile_view,
                           "action": RequestAction (expected_revision null = current)?}]}]}
A step without `action` is a checkpoint review; steps must be in nondecreasing clock ordinal per player.
"""
import argparse
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import sys

from engines import player_trade_requests as tr

REPORT_VERSION = 'trade-request-preview-v1'
ARMS = ('personality', 'neutral')
NEUTRAL_VIEW = {'status': 'missing', 'origin': None, 'sliders': dict.fromkeys(
    ('temperament', 'sociability', 'confidence', 'openness', 'warmth', 'drive'), 5), 'traits': [],
    'revision': None, 'persona_id': None, 'view_version': 'views-v2'}


class PreviewError(ValueError):
    pass


def _neutral_view():
    from personality.core import feature_view
    return feature_view(None, 'trade_requests')


# ------------------------------------------------------------------------------------------------ input ---

def load_input(data, *, mode):
    if mode not in ('disabled', 'shadow'):
        raise PreviewError('the preview runs disabled or shadow only (ENABLED is rejected)')
    if not isinstance(data, dict) or data.get('schema_version') != 1:
        raise PreviewError('input schema_version must be 1')
    if data.get('mode') not in (None, 'disabled', 'shadow'):
        raise PreviewError('the input may not ask for ENABLED mode')
    try:
        policy = tr.validate_policy(tr.policy_from_dict(data.get('policy')))
        sensitivity = [(s['name'], tr.validate_policy(tr.policy_from_dict(s['policy'])))
                       for s in data.get('sensitivity', [])]
    except (tr.TradeRequestError, KeyError, TypeError) as e:
        raise PreviewError(f'policy: {e}') from e
    players = data.get('players')
    if not isinstance(players, list) or not players:
        raise PreviewError('players must be a nonempty list')
    names = [p.get('name') for p in players]
    if len(set(names)) != len(names) or not all(isinstance(n, str) and n for n in names):
        raise PreviewError('player names must be unique nonempty text')
    out = []
    for p in players:
        pid = p.get('persona_id')
        steps = []
        last = None
        for i, s in enumerate(p.get('steps') or []):
            where = f"{p['name']}.steps[{i}]"
            try:
                c = tr.validate_clock(tr.clock_from_dict(s['clock']))
                raw = dict(s['context'], persona_id=pid, profile_view=NEUTRAL_VIEW)
                ctx = tr.context_from_dict(raw)
                action = None
                if s.get('action') is not None:
                    a = dict(s['action'])
                    if a.get('expected_revision') is None:
                        a['expected_revision'] = 0
                        current = True
                    else:
                        current = False
                    action = (tr.action_from_dict(a), current)
            except (tr.TradeRequestError, KeyError, TypeError) as e:
                raise PreviewError(f'{where}: {e}') from e
            if last is not None and c.ordinal < last:
                raise PreviewError(f'{where}: steps are out of order (ordinal {c.ordinal} after {last})')
            last = c.ordinal
            steps.append((c, ctx, action))
        if not steps:
            raise PreviewError(f"{p['name']}: needs at least one step")
        view = p.get('profile_view') or _neutral_view()
        if view.get('status') not in ('missing', 'stored'):
            raise PreviewError(f"{p['name']}: profile_view must come from personality.core.feature_view")
        out.append({'name': p['name'], 'persona_id': pid, 'view': view, 'steps': steps})
    return policy, sensitivity, out


# -------------------------------------------------------------------------------------------- running ---

def _err(e):
    return getattr(e, 'code', type(e).__name__)


def run_arm(player, view, policy, mode):
    """Replay one player's steps with one profile view. Returns the arm record."""
    episode, annual, timeline = None, {}, []
    eng_mode = mode.upper()
    for c, ctx0, action in player['steps']:
        ctx = replace(ctx0, profile_view=view)
        row = {'checkpoint_id': c.checkpoint_id, 'season': c.season, 'ordinal': c.ordinal,
               'window_kind': c.window_kind}
        if action is None:
            prior = episode if episode is not None and episode.status == 'ACTIVE' else None
            try:
                d = tr.evaluate_request(ctx, c, prior, annual.get(c.season), policy, mode=eng_mode)
            except tr.TradeRequestError as e:
                timeline.append(dict(row, step='review', error=_err(e)))
                continue
            if d.annual_slot_consumed:
                annual[c.season] = d.annual
            if d.outcome == 'REQUEST':
                episode = d.after
            q = d.qualification
            timeline.append(dict(row, step='review', outcome=d.outcome, annual_slot_consumed=d.annual_slot_consumed,
                                 probability=d.probability, draw=d.draw, explanation=list(d.explanation),
                                 eligible=None if q is None else q.eligible,
                                 deferred=[] if q is None else list(q.deferred_reasons),
                                 qualifying=[] if q is None else list(q.incident_ids)))
            continue
        act, current = action
        if eng_mode == 'DISABLED':
            timeline.append(dict(row, step='action', kind=act.kind, outcome='DISABLED'))
            continue
        if episode is None:
            timeline.append(dict(row, step='action', kind=act.kind, outcome='NO_EPISODE'))
            continue
        if current:
            act = replace(act, expected_revision=episode.revision)
        try:
            t = tr.transition_request(episode, act, ctx, c, policy)
        except tr.TradeRequestError as e:
            timeline.append(dict(row, step='action', kind=act.kind, outcome='REJECTED', error=_err(e)))
            continue
        episode = t.after
        timeline.append(dict(row, step='action', kind=act.kind, outcome=t.outcome, changed=t.changed,
                             status=episode.status, team_response=episode.team_response,
                             visibility=episode.visibility, reasons=list(t.reasons)))
    last_clock = player['steps'][-1][0]
    incidents = {}
    for _, ctx0, _ in player['steps']:
        for inc in ctx0.incidents:
            incidents[inc.incident_id] = inc
    contract = tr.contract_request_context(episode)
    return {'timeline': timeline,
            'annual': {str(s): {'outcome': a.outcome, 'draw': a.draw, 'probability': a.probability,
                                'checkpoint_id': a.checkpoint_id} for s, a in sorted(annual.items())},
            'final_status': None if episode is None else episode.status,
            'front_office': tr.request_projection(episode, audience='FRONT_OFFICE', clock=last_clock),
            'public': tr.request_projection(episode, audience='PUBLIC', clock=last_clock),
            'contract_context': contract,
            'gm_signal': tr.gm_request_signal(episode, clock=last_clock),
            'contribution_trace': contribution_trace(incidents, episode, contract)}


def contribution_trace(incidents, episode, contract):
    """One row per incident: which consumers see it and the single contribution key they share. The request
    layer charges nothing; a consumer charges an incident at most once whatever path it arrives by."""
    out = {}
    for iid, inc in sorted(incidents.items()):
        out[iid] = {'kind': inc.kind, 'source_event_ids': list(inc.source_event_ids),
                    'request_cause': bool(episode and iid in episode.initial_incident_ids),
                    'contract_context_unresolved': iid in contract.get('unresolved_incident_ids', []),
                    'contribution_key': iid, 'request_utility_penalty': 0}
    return out


def run(policy, sensitivity, players, mode):
    neutral = _neutral_view()
    report_players = []
    for p in players:
        arms = {'personality': run_arm(p, p['view'], policy, mode), 'neutral': run_arm(p, neutral, policy, mode)}
        report_players.append({'name': p['name'], 'persona_id': p['persona_id'],
                               'traits': sorted(p['view'].get('traits') or []), 'arms': arms,
                               'arms_equal': arms['personality'] == arms['neutral']})
    summary = {arm: _summary([rp['arms'][arm] for rp in report_players]) for arm in ARMS}
    grid = []
    for name, pol in sensitivity:
        rows = [run_arm(p, p['view'], pol, mode) for p in players]
        grid.append({'name': name, 'policy': tr.to_dict(pol), 'summary': _summary(rows)})
    return {'report_version': REPORT_VERSION, 'engine_version': tr.ENGINE_VERSION, 'mode': mode,
            'policy': tr.to_dict(policy), 'synthetic': True,
            'input_hash': tr.canonical_hash({'policy': policy, 'sensitivity': sensitivity, 'mode': mode,
                                             'players': [[p['name'], p['persona_id'], p['view'],
                                                          [[c, x, a] for c, x, a in p['steps']]] for p in players]}),
            'summary': summary, 'sensitivity': grid, 'players': report_players}


def _summary(arms):
    reviews = [r for a in arms for r in a['timeline'] if r['step'] == 'review']
    by = {}
    for r in reviews:
        key = r.get('outcome') or 'ERROR'
        by[key] = by.get(key, 0) + 1
    seasons = [(s, a2['outcome']) for a in arms for s, a2 in a['annual'].items()]
    return {'players': len(arms), 'reviews': len(reviews), 'outcomes': dict(sorted(by.items())),
            'eligible_draws': len(seasons), 'requests': sum(1 for _, o in seasons if o == 'REQUEST'),
            'no_requests': sum(1 for _, o in seasons if o == 'NO_REQUEST'),
            'final_status': dict(sorted({s: sum(1 for a in arms if a['final_status'] == s)
                                         for s in {a['final_status'] for a in arms if a['final_status']}}.items()))}


# --------------------------------------------------------------------------------------------- output ---

def render_readme(report):
    s = report['summary']
    lines = ['# Player trade-request preview', '',
             f"Mode `{report['mode']}`, policy `{report['policy']['version']}`, engine `{report['engine_version']}`. "
             'Synthetic demonstration only: not Madden outcomes, not calibration evidence, not a production policy.',
             '', '| Arm | Players | Reviews | Eligible draws | Requests | No request |', '|---|---|---|---|---|---|']
    for arm in ARMS:
        a = s[arm]
        lines.append(f"| {arm} | {a['players']} | {a['reviews']} | {a['eligible_draws']} | {a['requests']} | "
                     f"{a['no_requests']} |")
    if report['sensitivity']:
        lines += ['', '## Sensitivity (personality arm)', '', '| Policy | Eligible draws | Requests |', '|---|---|---|']
        lines += [f"| {g['name']} | {g['summary']['eligible_draws']} | {g['summary']['requests']} |"
                  for g in report['sensitivity']]
    lines += ['', '## Players', '']
    for p in report['players']:
        a = p['arms']['personality']
        lines.append(f"- **{p['name']}** ({', '.join(p['traits']) or 'no traits'}): final "
                     f"`{a['final_status'] or 'no request'}`, neutral "
                     f"`{p['arms']['neutral']['final_status'] or 'no request'}`, "
                     f"public {'disclosed' if a['public'] else 'private'}.")
    return '\n'.join(lines) + '\n'


def write_report(report, out_dir):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'report.json').write_text(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False) + '\n',
                                     encoding='utf-8')
    (out / 'README.md').write_text(render_readme(report), encoding='utf-8')


# -------------------------------------------------------------------------------------------- example ---

def _clock(season, tick, kind='IN_SEASON'):
    return {'universe_id': 'u-example', 'season': season, 'checkpoint_id': f'cp:{season}:{tick}',
            'ordinal': tr.clock_ordinal(season, tick), 'window_id': f'{kind}:{season}', 'window_kind': kind,
            'trades_open': kind != 'CLOSED', 'source_event_id': f'mu:tick:{season}:{tick}'}


def _incident(iid, kind, season, obs, state='OPEN', repairs=(), team=3, disclosure='PRIVATE'):
    return {'incident_id': iid, 'source_event_ids': [iid.rsplit(':', 1)[-1]], 'kind': kind, 'target_team_id': team,
            'first_ordinal': tr.clock_ordinal(season, 1), 'last_ordinal': tr.clock_ordinal(season, 2),
            'eligible_observations': obs, 'severity': 'MATERIAL', 'state': state, 'repair_event_ids': list(repairs),
            'disclosure': disclosure}


def _context(season, tick, incidents, *, team=3, user_team_ids=(), suggestions_on=True):
    c = tr.control_context(team, user_team_ids=list(user_team_ids), suggestions_on=suggestions_on)
    o = tr.clock_ordinal(season, tick)
    return {'schema_version': 1, 'team_id': team, 'relationship_revision': f'rel:{season}:{tick}',
            'reaction_revision': f'rx:{season}:{tick}', 'assessment_id': f'a:{season}:{tick}', 'as_of_ordinal': o,
            'complete_through_ordinal': o, 'incidents': incidents, 'control': tr.to_dict(c)}


def _view(pid, traits):
    return {'status': 'stored', 'origin': 'generated', 'sliders': dict(NEUTRAL_VIEW['sliders']), 'traits': traits,
            'revision': 0, 'persona_id': pid, 'view_version': 'views-v2'}


def _pick(prefix, season, lo, hi):
    """The first `<prefix>-<n>` persona whose fixed seeded draw for `season` lies in [lo, hi)."""
    for n in range(100000):
        pid = f'{prefix}-{n}'
        if lo <= tr.seeded_draw('u-example', pid, season) < hi:
            return pid
    raise PreviewError(f'no example persona for {prefix}')


def example_input():
    """Synthetic 2026-2035 fixtures: a benched DIVA (request, refusal, disclosure, executed trade), a broken
    promise repaired over two observations, a NO_REQUEST season that cannot reroll, a JR team after the 2034
    takeover with suggestions off, and an approved-but-unexecuted deal that never counts as a trade."""
    diva, promised = _pick('p-diva', 2027, 0.10, 0.15), _pick('p-promise', 2029, 0.0, 0.15)
    calm, jr = _pick('p-calm', 2030, 0.05, 0.10), _pick('p-jr', 2034, 0.0, 0.10)
    lost = 'inc:lost_role:team:3:role-a'
    promise = 'inc:broken_promise:team:3:promise-b'
    jr_team = 'inc:treatment:team:29:cut-d'
    players = [
        {'name': 'benched_diva', 'persona_id': diva, 'profile_view': _view(diva, ['DIVA', 'COMPETITOR']),
         'steps': [
             {'clock': _clock(2027, 6), 'context': _context(2027, 6, [_incident(lost, 'LOST_ROLE', 2027, 3)])},
             {'clock': _clock(2027, 8), 'context': _context(2027, 8, [_incident(lost, 'LOST_ROLE', 2027, 4)])},
             {'clock': _clock(2027, 9), 'context': _context(2027, 9, [_incident(lost, 'LOST_ROLE', 2027, 5)]),
              'action': {'event_id': 'ev:decline', 'source_event_id': 'gm:decline:1', 'expected_revision': None,
                         'kind': 'DECLINE', 'actor': 'GM', 'payload': {'response_ref': 'gm:decline:1'}}},
             {'clock': _clock(2027, 10), 'context': _context(2027, 10, [_incident(lost, 'LOST_ROLE', 2027, 6)]),
              'action': {'event_id': 'ev:disclose', 'source_event_id': 'presser:1', 'expected_revision': None,
                         'kind': 'DISCLOSE', 'actor': 'SYSTEM',
                         'payload': {'basis': 'public_statement', 'disclosure_ref': 'presser:1'}}},
             {'clock': _clock(2028, 4), 'context': _context(2028, 4, [_incident(lost, 'LOST_ROLE', 2027, 9)])},
             {'clock': _clock(2028, 7), 'context': _context(2028, 7, [], team=8),
              'action': {'event_id': 'ev:trade', 'source_event_id': 'tx:900', 'expected_revision': None,
                         'kind': 'RECORD_TRADE', 'actor': 'SYSTEM',
                         'payload': {'executed': True, 'transaction_id': 'tx:900', 'from_team_id': 3,
                                     'to_team_id': 8}}}]},
        {'name': 'broken_promise_repaired', 'persona_id': promised,
         'profile_view': _view(promised, ['GRUDGE_HOLDER']),
         'steps': [
             {'clock': _clock(2029, 12), 'context': _context(2029, 12,
                                                             [_incident(promise, 'BROKEN_PROMISE', 2029, 2)])},
             {'clock': _clock(2029, 14), 'context': _context(2029, 14, [_incident(
                 promise, 'BROKEN_PROMISE', 2029, 1, 'REPAIRED', ['repair-1'])]),
              'action': {'event_id': 'ev:rr1', 'source_event_id': 'review:2029:14', 'expected_revision': None,
                         'kind': 'REVIEW_REPAIR', 'actor': 'SYSTEM', 'payload': {}}},
             {'clock': _clock(2029, 16), 'context': _context(2029, 16, [_incident(
                 promise, 'BROKEN_PROMISE', 2029, 2, 'REPAIRED', ['repair-1'])]),
              'action': {'event_id': 'ev:rr2', 'source_event_id': 'review:2029:16', 'expected_revision': None,
                         'kind': 'REVIEW_REPAIR', 'actor': 'SYSTEM', 'payload': {}}}]},
        {'name': 'no_request_no_reroll', 'persona_id': calm, 'profile_view': _view(calm, ['PEACEMAKER']),
         'steps': [
             {'clock': _clock(2030, 10), 'context': _context(2030, 10, [_incident(lost, 'LOST_ROLE', 2030, 4)])},
             {'clock': _clock(2030, 20), 'context': _context(2030, 20, [_incident(lost, 'LOST_ROLE', 2030, 9)])},
             {'clock': _clock(2030, 31, 'CLOSED'), 'context': _context(2030, 31, [])}]},
        {'name': 'jr_team_2034', 'persona_id': jr, 'profile_view': _view(jr, ['DIVA']),
         'steps': [
             {'clock': _clock(2034, 9), 'context': _context(2034, 9, [_incident(jr_team, 'TREATMENT', 2034, 2,
                                                                                team=29)],
                                                             team=29, user_team_ids=[29], suggestions_on=False)},
             {'clock': _clock(2034, 10), 'context': _context(2034, 10, [], team=29, user_team_ids=[29],
                                                              suggestions_on=False),
              'action': {'event_id': 'ev:jr-ack', 'source_event_id': 'inbox:jr:1', 'expected_revision': None,
                         'kind': 'ACKNOWLEDGE', 'actor': 'JR', 'payload': {'response_ref': 'inbox:jr:1'}}},
             {'clock': _clock(2034, 11), 'context': _context(2034, 11, [], team=29, user_team_ids=[29],
                                                              suggestions_on=False),
              'action': {'event_id': 'ev:approved', 'source_event_id': 'deal:approved:1', 'expected_revision': None,
                         'kind': 'RECORD_TRADE', 'actor': 'SYSTEM',
                         'payload': {'executed': False, 'status': 'approved', 'transaction_id': 'deal:1',
                                     'from_team_id': 29, 'to_team_id': 3}}},
             {'clock': _clock(2035, 3), 'context': _context(2035, 3, [], team=29, user_team_ids=[29],
                                                             suggestions_on=False)}]},
    ]
    certain = tr.to_dict(replace(tr.offline_illustration_policy(), version='offline-certain-v1',
                                 lower_probability=1.0, baseline_probability=1.0, higher_probability=1.0))
    return {'schema_version': 1, 'policy': tr.to_dict(tr.offline_illustration_policy()),
            'sensitivity': [{'name': 'illustration', 'policy': tr.to_dict(tr.offline_illustration_policy())},
                            {'name': 'every_eligible_requests', 'policy': certain}],
            'players': players}


# ------------------------------------------------------------------------------------------------ main ---

def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m tools.player_trade_request_preview',
                                 description='Offline synthetic player trade-request preview (no database).')
    ap.add_argument('--input', help='schema-v1 synthetic input JSON')
    ap.add_argument('--out', help='output directory for report.json and README.md')
    ap.add_argument('--mode', choices=('disabled', 'shadow'), default='shadow')
    ap.add_argument('--example', metavar='PATH', help='write the synthetic example input to PATH and exit')
    args = ap.parse_args(argv)
    if args.example:
        Path(args.example).write_text(json.dumps(example_input(), sort_keys=True, indent=2) + '\n', encoding='utf-8')
        return 0
    if not args.input or not args.out:
        ap.error('--input and --out are required (or --example PATH)')
    try:
        data = json.loads(Path(args.input).read_text(encoding='utf-8'))
        policy, sensitivity, players = load_input(data, mode=args.mode)
    except (OSError, json.JSONDecodeError, PreviewError) as e:
        print(f'error: {e}', file=sys.stderr)
        return 2
    write_report(run(policy, sensitivity, players, args.mode), args.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
