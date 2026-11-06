"""Offline comparison report for the personality contract policy (PERSONALITY_CONTRACT_POLICY_PLAN.md §2.6 step 5,
§4, U1). Fictional cohorts only: no database, no discovery, no activation.

    python3 -m tools.personality_contract_policy_report --input tests/fixtures/contract_policy/cohorts.json \
        --out verify_output/contract_policy --mode compare

Modes: neutral (the engine's own decisions, no personality), shadow (the same decisions plus the deltas the policy
would have applied), compare (paired seeded baseline vs adjusted decisions on identical draws). Writes report.json
and report.md; the same input always gives byte-identical output (no timestamps).

Each player's offers are scored with free_agency_engine's own component functions (unrounded), the policy delta is
added, and one offseason decision is made the way free_agency_engine._decide does: offers ranked by utility plus the
seeded +-0.03 choice noise, the bar shifted by the seeded +-0.03 mood, the first ACCEPT, else the first COUNTER (the
smallest 5% raise up to +35% that clears the bar, rescored with the same frozen context), else REJECT. The report
counts signed deltas, cap saturation, ranking/threshold/counter flips, salary and cap-share distributions, team
concentration, missing/unknown input rates and family activation, plus a contender-gate sweep that lists every
cross-certification discontinuity for JR's review. Numbers are offline trial evidence, not calibration.
"""
import argparse
import json
import math
from pathlib import Path
import statistics

from engines import free_agency_engine as fae
from engines import personality_contract_policy as cp
from engines.trade_value import MARKET_PREMIUM, OVR_PIVOT

SCHEMA = 'contract-policy-cohorts-v1'
REPORT_VERSION = 'contract-policy-report-v1'
MODES = ('neutral', 'shadow', 'compare')
TAG = 'report'


# ------------------------------------------------------------------ inputs ----
def load_input(path):
    data = json.loads(Path(path).read_text())
    if data.get('schema') != SCHEMA:
        raise ValueError(f'input schema must be {SCHEMA}')
    for c in data['cohorts']:
        if not c.get('name') or not c.get('players') or not c.get('offers'):
            raise ValueError('every cohort needs a name, players and offers')
    return data


def market_from(spec):
    """A trade_value market fit whose W at OVR_PIVOT is base_worth (a fictional market)."""
    a = math.log(spec['base_worth'] / MARKET_PREMIUM)
    return {'b': spec.get('b', 0.09), 'a_league': a,
            'a': {g: math.log(v / MARKET_PREMIUM) for g, v in (spec.get('group_worth') or {}).items()}, 'n': {}}


def player_view(p, cohort):
    if p.get('profile') == 'missing':
        return None
    return {'status': 'stored', 'origin': p.get('origin', 'generated'), 'sliders': {}, 'traits': list(p['traits']),
            'revision': p.get('revision', 0), 'persona_id': f'{cohort}:p{p["id"]}', 'catalogue_version': 'fixture',
            'core_version': 'fixture', 'view_version': 'views-v2'}


def player_context(p, cohort, season, team_ids):
    view = player_view(p, cohort)
    ctx = cp.neutral_context(f'fixture:{cohort}', p['id'], {'season': season, 'week': 25, 'stage': 'FREE_AGENCY'},
                             f'{cohort}:batch', persona_id=view and view['persona_id'],
                             revision=view and view['revision'], current_team_id=p.get('current_team_id'),
                             team_ids=team_ids)
    ctx['career']['established'] = p.get('established')
    ctx['security']['secure'] = p.get('secure')
    ctx['security']['through_season'] = season - 1
    for tid, rec in (p.get('teams') or {}).items():
        ctx['teams'][int(tid)].update(rec)
    return view, cp.normalize_contract_context(ctx)


# ----------------------------------------------------------------- scoring ----
def raw_parts(offer, player, team, market):
    fin = fae.financial_score(offer, player, market)
    opp = fae.role_score(fae._depth(offer, player, team), player.get('ovr'))
    con = fae.contention_score(team)
    fit = (team.get('scheme_fit') or {}).get(player['id'])
    sch = fae.NEUTRAL if fit is None else max(0.0, min(1.0, float(fit)))
    return {'financial': fin, 'opportunity': opp, 'contention': con, 'scheme': sch, 'team_id': offer['team_id']}


def base_utility(parts):
    w = fae.W_DEFAULT
    return (w['money'] * parts['financial'] + w['role'] * parts['opportunity'] + w['team'] * parts['contention']
            + w['scheme'] * parts['scheme'])


def score(offer, player, team, market, view, ctx, cfg, adjust):
    parts = raw_parts(offer, player, team, market)
    base = base_utility(parts)
    d = cp.contract_personality_delta(parts, view, ctx, cfg)
    u = base + d['capped_delta'] if adjust else base
    return {'base': base, 'utility': round(u, 4), 'delta': d}


def counter(offer, player, team, market, bar, view, ctx, cfg, adjust):
    vet = (player.get('age') or 27) >= fae.VET_AGE
    years = (max(offer.get('years') or 1, fae.ideal_years(player.get('age'))) if vet
             else fae.ideal_years(player.get('age')))
    share = max(fae._guar_share(offer), fae.GUAR_TARGET_VET if vet else fae.GUAR_TARGET)
    bonus_share = (offer.get('bonus') or 0) / max(1, offer.get('total_value') or 1)
    base_aav = max(fae._aav(offer), 1.0)
    for k in range(int(round(fae.COUNTER_MAX_RAISE / fae.COUNTER_STEP)) + 1):
        total = int(round(base_aav * (1 + k * fae.COUNTER_STEP) * years))
        c = {'team_id': offer['team_id'], 'player_id': offer['player_id'], 'years': years, 'total_value': total,
             'bonus': int(round(total * bonus_share)), 'guaranteed': int(round(total * share)),
             'projected_depth': offer.get('projected_depth')}
        if score(c, player, team, market, view, ctx, cfg, adjust)['utility'] >= bar:
            return {k2: c[k2] for k2 in ('years', 'total_value', 'bonus', 'guaranteed')}
    return None


def decide(player, offers, teams, market, view, ctx, cfg, seed, adjust):
    """One decision, free_agency_engine._decide's order and draws (wave 3, all tiers considered)."""
    pid = player['id']
    scored = [(i, o, teams[o['team_id']], score(o, player, teams[o['team_id']], market, view, ctx, cfg, adjust))
              for i, o in enumerate(offers)]
    shift = fae._noise(fae.MOOD_NOISE, seed, 'wave', 3, pid) - fae.WAVE_COOLING * (3 - fae.wave_tier(player))
    bar = fae.accept_bar(player, False) + shift
    ranked = sorted(scored, key=lambda s: (-(s[3]['utility'] + fae._noise(fae.CHOICE_NOISE, seed, TAG, pid,
                                                                          s[1]['team_id'], s[0])), s[0]))
    first_counter = None
    for idx, o, team, s in ranked:
        blocked = ((player.get('ovr') or 0) >= fae.STAR_OVR and fae._depth(o, player, team) >= 3
                   and fae._aav(o) < fae.STAR_BACKUP_PREMIUM * fae._worth(player, market))
        if blocked:
            continue
        if s['utility'] >= bar:
            return {'status': 'ACCEPT', 'team_id': o['team_id'], 'offer': o, 'counter': None,
                    'top_team': ranked[0][1]['team_id'], 'deltas': [x[3]['delta'] for x in scored]}
        if first_counter is None:
            c = counter(o, player, team, market, bar, view, ctx, cfg, adjust)
            if c:
                first_counter = {'status': 'COUNTER', 'team_id': o['team_id'], 'offer': o, 'counter': c}
    out = first_counter or {'status': 'REJECT', 'team_id': ranked[0][1]['team_id'], 'offer': ranked[0][1],
                            'counter': None}
    out.update(top_team=ranked[0][1]['team_id'], deltas=[x[3]['delta'] for x in scored])
    return out


# ------------------------------------------------------------------ report ----
def _stats(xs):
    if not xs:
        return {'n': 0}
    xs = sorted(xs)
    return {'n': len(xs), 'mean': round(statistics.fmean(xs), 6), 'median': round(statistics.median(xs), 6),
            'min': round(xs[0], 6), 'max': round(xs[-1], 6)}


def gate_sweep(cfg):
    """A secure, established ring chaser with a starting role: U as contention crosses the contender floor, with
    financial above and below. Lists every point where U falls as C rises (cross-certification discontinuity)."""
    out = []
    floor = cfg['contender_floor']
    for F in (0.6, 0.8, 1.0, 1.2):
        view = {'status': 'stored', 'origin': 'generated', 'traits': ['RING_CHASER'], 'revision': 0,
                'persona_id': 'sweep', 'sliders': {}}
        ctx = cp.neutral_context('fixture:sweep', 1, {'season': 2031, 'week': 25, 'stage': 'FREE_AGENCY'},
                                 'sweep', persona_id='sweep', revision=0, team_ids=(1,))
        ctx['career']['established'] = True
        ctx['security']['secure'] = True
        ctx['security']['through_season'] = 2030
        points = []
        for step in range(0, 41):
            C = round(floor - 0.10 + step * 0.005, 4)
            parts = {'financial': F, 'opportunity': 1.0, 'contention': C, 'scheme': 0.5, 'team_id': 1}
            d = cp.contract_personality_delta(parts, view, ctx, cfg)
            points.append((C, base_utility(parts) + d['capped_delta']))
        drops = [{'from_contention': a[0], 'to_contention': b[0], 'utility_change': round(b[1] - a[1], 6)}
                 for a, b in zip(points, points[1:]) if b[1] < a[1] - 1e-12]
        out.append({'financial': F, 'discontinuities': drops})
    return out


def run(data, mode):
    if mode not in MODES:
        raise ValueError(f'mode must be one of {", ".join(MODES)}')
    cfg = cp.trial_config(**dict(data.get('config') or {}, mode='active' if mode == 'compare' else
                                 ('shadow' if mode == 'shadow' else 'off')))
    market = market_from(data['market'])
    cap = data.get('cap', 255_000_000)
    rows = []
    for cohort in data['cohorts']:
        teams = {t['team_id']: dict(t, scheme_fit={int(k): v for k, v in (t.get('scheme_fit') or {}).items()})
                 for t in cohort['teams']}
        season = cohort.get('season', 2031)
        for seed in data['seeds']:
            for p in cohort['players']:
                offers = [o for o in cohort['offers'] if o['player_id'] == p['id']]
                if not offers:
                    continue
                player = {k: p[k] for k in ('id', 'group', 'ovr', 'age', 'accrued_seasons')}
                view, ctx = player_context(p, cohort['name'], season, sorted(teams))
                base = decide(player, offers, teams, market, view, ctx, cfg, seed, adjust=False)
                adj = decide(player, offers, teams, market, view, ctx, cfg, seed, adjust=True) \
                    if mode == 'compare' else base
                rows.append({'cohort': cohort['name'], 'seed': seed, 'player_id': p['id'], 'base': base,
                             'adjusted': adj, 'view': view, 'ctx': ctx})
    return summarize(rows, cfg, mode, cap)


def _aav(d):
    terms = d['counter'] if d['status'] == 'COUNTER' else d['offer']
    return terms['total_value'] / max(1, terms['years'])


def summarize(rows, cfg, mode, cap):
    deltas = [d for r in rows for d in r['adjusted']['deltas'] if d['mode'] != 'off']
    fam = {'ambition': 0, 'attachment': 0, 'home': 0, 'grudge': 0}
    for d in deltas:
        for k, v in d['terms'].items():
            fam[k] += v != 0
    flips = {'ranking': sum(r['base']['top_team'] != r['adjusted']['top_team'] for r in rows),
             'status': sum(r['base']['status'] != r['adjusted']['status'] for r in rows),
             'destination': sum(r['base']['status'] == r['adjusted']['status'] == 'ACCEPT'
                                and r['base']['team_id'] != r['adjusted']['team_id'] for r in rows),
             'counter_terms': sum(r['base']['status'] == r['adjusted']['status'] == 'COUNTER'
                                  and r['base']['counter'] != r['adjusted']['counter'] for r in rows)}

    def outcome(key):
        acc = [r[key] for r in rows if r[key]['status'] == 'ACCEPT']
        by_team = {}
        for d in acc:
            by_team[str(d['team_id'])] = by_team.get(str(d['team_id']), 0) + 1
        return {'status': {s: sum(r[key]['status'] == s for r in rows) for s in ('ACCEPT', 'COUNTER', 'REJECT')},
                'accepted_aav': _stats([_aav(d) for d in acc]),
                'accepted_cap_share': _stats([_aav(d) / cap for d in acc]),
                'signings_by_team': dict(sorted(by_team.items())),
                'max_team_share': round(max(by_team.values()) / len(acc), 6) if acc else 0.0}

    by_player = {}
    for r in rows:
        k = (r['cohort'], r['player_id'])
        by_player.setdefault(k, r)
    players = list(by_player.values())
    return {
        'report_version': REPORT_VERSION, 'mode': mode, 'policy_version': cfg['version'],
        'config': {k: cfg[k] for k in cp.CONFIG_KEYS},
        'disclaimer': 'Fictional cohorts and offline trial settings; not calibration, activation or production '
                      'evidence. JR reviews the distributions and sets tolerances.',
        'counts': {'decisions': len(rows), 'players': len(players), 'scored_offers': sum(len(r['base']['deltas'])
                                                                                      for r in rows)},
        'inputs': {'profile_missing': sum(r['view'] is None for r in players),
                   'career_unknown': sum(r['ctx']['career']['established'] is None for r in players),
                   'security_unknown': sum(r['ctx']['security']['secure'] is None for r in players)},
        'delta': {'signed': _stats([d['capped_delta'] for d in deltas]),
                  'nonzero': sum(d['capped_delta'] != 0 for d in deltas),
                  'cap_saturated': sum(d['cap_applied'] for d in deltas),
                  'family_active': fam,
                  'reasons': dict(sorted(_count(x for d in deltas for x in d['reasons']).items()))},
        'flips': flips if mode == 'compare' else {k: 0 for k in flips},
        'baseline': outcome('base'), 'adjusted': outcome('adjusted'),
        'gate_sweep': gate_sweep(cfg if cfg['mode'] != 'off' else dict(cfg, mode='active')),
    }


def _count(it):
    out = {}
    for x in it:
        out[x] = out.get(x, 0) + 1
    return out


def markdown(rep):
    b, a, d = rep['baseline'], rep['adjusted'], rep['delta']
    lines = [f'# Contract policy report ({rep["mode"]})', '', rep['disclaimer'], '',
             f'Policy `{rep["policy_version"]}`, k {rep["config"]["coefficient"]}, cap {rep["config"]["aggregate_cap"]},'
             f' contender floor {rep["config"]["contender_floor"]}, relationships '
             f'{"on" if rep["config"]["relationship_enabled"] else "off"}.', '',
             f'- Decisions {rep["counts"]["decisions"]} ({rep["counts"]["players"]} players, '
             f'{rep["counts"]["scored_offers"]} scored offers).',
             f'- Inputs: profile missing {rep["inputs"]["profile_missing"]}, career unknown '
             f'{rep["inputs"]["career_unknown"]}, security unknown {rep["inputs"]["security_unknown"]}.',
             f'- Delta: nonzero {d["nonzero"]}, cap-saturated {d["cap_saturated"]}, signed {d["signed"]}.',
             f'- Family activation: {d["family_active"]}.',
             f'- Flips: {rep["flips"]}.',
             f'- Baseline status {b["status"]}, accepted AAV {b["accepted_aav"]}, max team share {b["max_team_share"]}.',
             f'- Adjusted status {a["status"]}, accepted AAV {a["accepted_aav"]}, max team share {a["max_team_share"]}.',
             '', '## Contender-gate sweep', '']
    for s in rep['gate_sweep']:
        drops = ', '.join(f'C {x["from_contention"]}->{x["to_contention"]}: {x["utility_change"]:+.4f}'
                          for x in s['discontinuities']) or 'none'
        lines.append(f'- F {s["financial"]}: {drops}')
    return '\n'.join(lines) + '\n'


def write(rep, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / 'report.json').write_text(json.dumps(rep, indent=2, sort_keys=True) + '\n')
    (out / 'report.md').write_text(markdown(rep))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--input', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--mode', choices=MODES, default='compare')
    a = ap.parse_args(argv)
    rep = run(load_input(a.input), a.mode)
    write(rep, a.out)
    print(f'wrote {a.out}/report.json and report.md ({rep["counts"]["decisions"]} decisions, mode {a.mode})')
    return rep


if __name__ == '__main__':
    main()
