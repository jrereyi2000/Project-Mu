"""Synthetic, offline contract-personality demonstration; never changes engine behavior.

python -m tools.personality_contract_demo --out verify_output/personality_contract_demo
"""
import argparse
import json
import math
from pathlib import Path

from engines import free_agency_engine as fa
from engines.trade_value import MARKET_PREMIUM, OVR_PIVOT

SCENARIOS = [
    ('tradeoff', 'Money versus contention', 75, [(1.20, .20, 1, 0), (.95, .90, 1, 0)]),
    ('clear_money', 'Large financial gap', 75, [(1.60, .20, 1, 0), (.70, .90, 1, 0)]),
    ('loyalty', 'Loyalty: stay for $10M or leave for $11M', 75, [(1.00, .50, 1, 1), (1.10, .50, 1, 0)]),
    ('stacking', 'Loyalty + hometown: equal team strength', 75, [(1.00, .50, 1, 2), (1.10, .50, 1, 0)]),
    ('veteran', 'Secure veteran: money versus contention', 85, [(1.20, .20, 1, 0), (.95, .90, 1, 0)]),
    ('backup', 'Star offered a backup role', 85, [(1.40, .90, 3, 0), (1.00, .60, 1, 0)]),
    ('weak', 'Near the acceptance threshold', 75, [(.65, .20, 2, 0), (.70, .35, 2, 0)]),
]


def fixtures():
    result = []
    for key, title, ovr, terms in SCENARIOS:
        age = 32 if key in ('veteran', 'backup') else 27
        player = dict(id=1, group='WR', ovr=ovr, age=age, accrued_seasons=9 if age == 32 else 5)
        intercept = math.log(10_000_000 / MARKET_PREMIUM) - .09 * (ovr - OVR_PIVOT)
        market = dict(b=.09, a_league=intercept, a={}, n={})
        offers = []
        for i, (pay, contention, depth, context) in enumerate(terms):
            total = round(10_000_000 * pay * 2)
            offer = dict(player_id=1, team_id=i+1, years=2, total_value=total,
                         bonus=0, guaranteed=total, projected_depth=depth)
            team = dict(team_id=i+1, win_pct=contention, playoff_odds=contention,
                        popularity_index=100*contention, scheme_fit={1:.5})
            score = fa.offer_utility(offer, player, team, market)
            assert score == fa.offer_utility(offer, player, team, market, persona={'traits':['MONEY_FIRST']})
            blocked = ovr >= fa.STAR_OVR and depth >= 3 and pay < fa.STAR_BACKUP_PREMIUM
            offers.append(dict(label=chr(65+i), aav=total//2, depth=depth, current_team=context > 0, home_match=context > 1,
                               score=score, blocked=blocked))
        result.append(dict(id=key,title=title,ovr=ovr,age=age,career_context='secure' if key in ('veteran', 'backup') else 'building',bar=fa.accept_bar(player,False),offers=offers))
    return result


ATTACHMENT = {'none': 0.0, 'weak': .015, 'established': .035, 'strong': .055}


def ring_eligibility(scenario, career_context=None):
    context = career_context or scenario['career_context']
    established_and_secure = context == 'secure'
    opportunity = any(o['score']['contention'] >= .7 and o['depth'] <= 2 and not o['blocked']
                      for o in scenario['offers'])
    return established_and_secure and opportunity


def evaluate(scenario, profile, coefficient=.10, cap=.08, attachment_tier='established',
             hometown_bonus=.01, career_context=None):
    ring_active = ring_eligibility(scenario, career_context)
    rows=[]
    for o in scenario['offers']:
        s=o['score']
        direction = {'neutral':0, 'money':1, 'ring':-1 if ring_active else 0}[profile]
        preference = coefficient*(s['financial']-s['contention'])*direction
        attachment = ATTACHMENT[attachment_tier] if o['current_team'] else 0.0
        hometown = hometown_bonus if o['home_match'] else 0.0
        raw=preference+attachment+hometown
        delta=max(-cap,min(cap,raw))
        utility=s['utility']+delta
        status='Role rule blocks' if o['blocked'] else ('Eligible' if utility >= scenario['bar'] else 'Below bar')
        rows.append(dict(offer=o['label'],base=s['utility'],preference=preference,
                         attachment=attachment,hometown=hometown,raw=raw,delta=delta,
                         utility=utility,status=status))
    eligible=[r for r in rows if r['status']=='Eligible']
    eligible.sort(key=lambda r:(-r['utility'],r['offer']))
    return dict(rows=rows,ring_active=ring_active,choice=eligible[0]['offer'] if eligible else 'No immediate signing')

def build_report():
    scenarios=fixtures()
    comparisons=[]
    for scenario in scenarios:
        for profile in ('neutral','money','ring'):
            comparisons.append(dict(scenario=scenario['id'],profile=profile,**evaluate(scenario,profile)))
    sweep=[]
    for coefficient in (0,.05,.10,.15,.20):
        for cap in (0,.02,.04,.08,.12):
            for profile in ('neutral','money','ring'):
                for scenario in scenarios:
                    e=evaluate(scenario,profile,coefficient,cap)
                    assert all(abs(r['delta']) <= cap+1e-12 for r in e['rows'])
                    sweep.append(dict(scenario=scenario['id'],profile=profile,coefficient=coefficient,cap=cap,choice=e['choice']))
    return dict(synthetic=True,engine_effects_activated=False,noise=False,counteroffers=False,
                version=2,attachment_levels=ATTACHMENT,defaults=dict(coefficient=.10,cap=.08,attachment_tier='established',hometown_bonus=.01),scenarios=scenarios,
                comparisons=comparisons,sweep=sweep)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,default=Path('verify_output/personality_contract_demo'))
    args=parser.parse_args()
    report=build_report()
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    lines=['# Offline contract personality demonstration','',
           'Synthetic WRs, market worth $10M/year, two-year terms, target guarantees, scheme fit .5; veteran cases age 32, others age 27.',
           'Baseline components and role constraints come from the current engine. Personality deltas are proposed only.',
           'Attachment tiers are explicit synthetic history assumptions: none 0, weak .015, established .035, strong .055. They are not relationship counts. Hometown is a separate .01 preference. Neutral means no money/contention trait; shared attachment remains in relationship scenarios.',
           'No noise, counters, legal/cap validation, market waves, database access or save writes. Eligible means clears the existing score/role gates, not an executed signing.','',
           '| Scenario | Neutral | Money First | Ring Chaser |','|---|---|---|---|']
    for s in report['scenarios']:
        choices=[evaluate(s,p)['choice'] for p in ('neutral','money','ring')]
        lines.append('| '+s['title']+' | '+' | '.join(choices)+' |')
    lines+=['','## What to inspect','',
            '- At coefficient .10, Money First switches the close tradeoff to A when the cap reaches about .068; cap .04 keeps B.',
            '- No personality can shift one offer more than the selected cap. The relative A-versus-B gap can change by up to twice that cap.',
            '- Coefficient 0 disables money/contention preferences; attachment and hometown have separate controls. Cap 0 disables all modifiers.',
            '- The larger financial gap stays decisive at the default bounds. Star backup rejection still applies even when utility clears the bar.',
            '- Existing choice noise is ±.03 per offer (up to .06 relative gap). The default Money First margin in the close tradeoff is only .01225: its winner is not robust to every market draw.',
            '- Near the acceptance threshold, a modifier can turn a non-signing offer into an eligible one; it does not just change rankings.',
            '- ±.08 is utility, not an 8% salary discount or 8% signing probability. Actual salary effects depend on the other scores and constraints.',
            '- Strong ring preference requires explicit established/financially-secure context plus a usable contender role (contention >= .7 in this trial); OVR alone is neither sufficient nor necessary. Financial security is an explicit fixture input, not inferred from current salary. No trait distribution is being assigned here.',
            '- Deferred: production attachment-history and financial-security classification, counteroffers, market noise, contract legality, persistence and production integration.']
    (args.out/'README.md').write_text('\n'.join(lines)+'\n')
    print(f'{len(report["comparisons"])} default comparisons; {len(report["sweep"])} parameter combinations -> {args.out}')

if __name__=='__main__':
    main()
