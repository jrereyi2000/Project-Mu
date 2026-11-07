# Generated-player personality: effect matrix and worked examples

**Status:** design for review, 2026-10-30. No production seeding, migrations, engine edits or effect activation. Implements the next design deliverable in PERSONALITY_PLAN.md §5.1. JR plans to control the simulation from its starting checkpoint through 2034; confirm the exact starting season before scheduling classes.
**JR decisions (2026-11-03; supersede conflicting text below):** (1) every persona named after a real person (writers, GMs, coaches, players) is a fictional character inspired by that person and may hold views, moods and relationships formed from simulated events; the conservative real-person rules below (neutral private appraisal, scoped evidence permissions) no longer apply to them. (2) Existing players get engine-generated profiles once at the 2026 start (not researched); prospects at draft-class entry; JR can edit any profile. (3) Existing real coaches get reviewed profiles shaped to their real counterparts (separate workstream). (4) The relationships plan owns trust; trust is one-way; promises use `promise_made`/`promise_resolved`. (5) Migration dates are reserved in PERSONALITY_PLAN §5.3. See PERSONALITY_PLAN §5 step 3, §5.1 and D8–D13. For this design: its effects now apply to existing players too, since they get generated profiles.

## 1. What exists and where to integrate

These findings come from current owning code, not proposed behavior:

| Source | Current behavior | Integration consequence |
|---|---|---|
| `newsroom/personas.py`: seed_sliders, seed_traits, seed_persona | Deterministic role/ID seed; six integer sliders total 30; default four catalogue traits, conflicts and two-per-category limit | Reuse schema/catalogue; do not invent parallel motivation sliders. Current seed is Python Random over `type:id`, not the SHA recipe suggested by older plan prose. Freeze/version outputs at initialization. |
| Same: get_persona | Falls back to deterministic seed if missing tables/profile or on read failure | Unsafe as an engine authority: missing real-person profiles must not gain random motivations. Engine resolver must distinguish stored approved profile, legitimately absent profile, and storage failure. Missing profile is neutral; storage failure blocks or explicitly marks a neutral preview, never silently commits changed decisions. |
| `engines/free_agency_engine.py`: offer_utility, acceptance, _counter, _decide, run_wave, _market | Money/role/team/scheme weights .45/.25/.20/.10; persona argument explicitly ignored. Acceptance and counter paths recompute scores; waves add seeded mood/choice noise | Best first integration. Pass the same resolved profile and effect version through initial scoring, counters, re-sign/extension previews, waves, street and UDFA callers. Updating only offer_utility would leave inconsistent decisions. |
| `engines/transactions_engine.py`, `server/franchise_api.py` | Call free-agency scoring/acceptance | Owning integration must cover simulation and user previews, and persist the breakdown used for executed decisions. |
| `engines/progression.py`: compute_player_delta, PERSONA_TERM/COACH_TERM, mentor_term/find_mentor | Personality/coaching terms are zero. Age, ability, play, injury, wear, continuity and veteran-mentor mechanisms already exist | Coordinate with progression owner; modify eligible existing terms rather than duplicate mentor/wear/scheme rewards. Madden PT flags and dev tiers are not persona traits. |
| `engines/gm_trade_engine.py`, `engines/gm_negotiation.py` | Structured GM persona supplies dialogue; GM valuation/negotiation uses existing GM mechanics | Player dissatisfaction may create a request or alter extension willingness; never a second arbitrary deduction from trade value or universal player trade veto. |
| `engines/season_ledger.py` | Consumes captured retirements and reports career status | No retirement-choice engine was found in this audit. Retirement personality requires a separate baseline hazard and write-ownership design before activation. |
| `pipeline/load_save.py`: player upsert; `engines/draft_room.py`: prospect records | Active players map pgid to DB ID; draft room uses draft season/prospect keys and optional prospect IDs | Prospect-to-player identity handoff is a prerequisite. `origin='SAVE'` is ingestion provenance, not proof a person is fictional. |

Catalogue currently contains zero-valued effect placeholders (including free-agency, progression and retirement keys). They are not an implemented contract for interpreting coefficients. Effects below are proposed mechanics, not claims those placeholders already work.

## 2. First implementation recommendation: contract preferences

Keep current component scores and base weights. For approved generated personas, propose one bounded preference adjustment:

`U_final = U_base + clamp(sum(eligible trait deltas), -0.08, +0.08)`

Initial trial coefficients below are deliberately explicit **offline tuning proposals**, not calibrated facts. Do not clamp final utility to 1: current financial scoring permits utilities above 1. Use one modifier path, not both weight shifts and an additive bonus for the same preference. Leave acceptance bars, market worth, legal offer constraints, minimum salaries and star-backup rule unchanged in the first increment.

| Existing trait | Trial contract delta | Eligibility/data needed | Exclusions |
|---|---|---|---|
| MONEY_FIRST | `0.10 × (financial - contention)` | Approved fictional preference; existing scores | No lower work ethic, extension slump, injury risk or automatic maximum demand |
| RING_CHASER | `0.10 × (contention - financial)` only when strong win-now preference is career-eligible | Explicit established career + financial security + usable contender role; unknown means inactive | No automatic cheap contract or playing hurt; still needs acceptable legal offer |
| LOYAL | Trial earned attachment: none 0 / weak .015 / established .035 / strong .055, current team only | Logged tenure, meaningful relationships, satisfactory role and treatment; tiers are not relationship counts; classification still to design | Not warmth-as-loyalty; not hometown; no permanent refusal to leave |
| HOMETOWN_GUY | Trial `+0.01` for a verified meaningful home-region match | Explicit generated hometown and destination mapping | Not drafted-team bonus; missing geography gives zero |
| GRUDGE_HOLDER | `-0.03` toward a team tied to an unresolved qualifying event | Logged dismissal/broken commitment with that team and an explicit relationship update | No grudge inferred from a loss; no automatic trade-value discount |
| LEGACY_BUILDER / SPOTLIGHT_SEEKER | Defer numeric effect | Need record-opportunity/media-exposure inputs separate from contention/popularity | Do not award a second generic contender/popularity bonus |

MONEY_FIRST and RING_CHASER are weight shifts, not extra bonuses (checked 2026-11-02). With base weights .45 money and .20 team, MONEY_FIRST at coefficient k moves the team weight from .20 to .20 − k, and RING_CHASER moves the money weight from .45 to .45 − k. So the bound is k < .20 for MONEY_FIRST and k < .45 for RING_CHASER: below it, a MONEY_FIRST player still prefers the better team when money is equal, and a RING_CHASER still prefers more money when contention is equal. The demo sweep's top value, .20, is MONEY_FIRST's indifference point: there he no longer cares about contention at equal money. `tests/test_personality_contract_demo.py::test_preferences_never_invert_base_weights` checks k = .05, .10 and .15 at every swept cap.

MONEY_FIRST and RING_CHASER conflict in the catalogue; never assign both. LOYAL and HOMETOWN_GUY can coexist but their contributions share the aggregate cap. Every decision records base score, component scores, eligible deltas, raw sum, capped delta, threshold and outcome. Noise is a separately recorded existing term, not personality.

### Matched fictional-player contract example

Same 75 OVR, age-27 player; same two-year term, full target guarantee, starter role (opportunity 1), scheme .5. Amounts are multiples of market worth W, not proposed dollar valuations.

| Offer | AAV / W | Financial score | Contention | Base utility |
|---|---:|---:|---:|---:|
| A: larger offer, weaker team | 1.20 | 1.140 | .20 | .85300 |
| B: smaller offer, contender | .95 | .965 | .90 | .91425 |

| Controlled variant | A adjusted utility | B adjusted utility | Highest utility |
|---|---:|---:|---|
| Neutral | .85300 | .91425 | B |
| MONEY_FIRST only | .93300 (delta capped at +.08) | .92075 | A |
| RING_CHASER only | .77300 (delta capped at -.08) | .90775 | B |

The table uses unrounded arithmetic; current offer_utility rounds utility to four decimals, yielding B=.9142/.9207/.9077 when the trial adjustment follows that rounded result. This original matched ambition example assumes an established, financially secure player with a usable contender role; the revised demo disables the Ring Chaser modifier for the default career-building case. All exceed the unchanged .68 acceptance bar. These isolate personality by turning off choice/mood noise in the demonstration; they do not claim run_wave currently produces these persona outcomes. Existing ±.03 choice noise can reverse a narrow preference, so later seeded multi-run evaluation must quantify robustness. MONEY_FIRST still accepts a contender if its overall offer is better; neutral players already value contention. Singleton trait variants isolate a mechanism; they are not valid 3–5-trait production profiles.

## 3. Broader effect matrix and boundaries

These are proposed follow-on mechanisms. Each requires owning-engine integration and neutral-control validation; none activates via catalogue edits alone.

| Existing trait/slider | Proposed consequence | Bound / no double-counting | Priority |
|---|---|---|---|
| WORKOUT_FANATIC / COASTING | Improve/reduce maintenance and eligible development effort | Trial ±10% of an existing eligible term; no extra durability rating, automatic medical recovery or ceiling invention | Progression-owner design next |
| FILM_ROOM_RAT / PERFECTIONIST | Direct effort toward mental/technical growth respectively | Reallocate or modestly scale eligible growth; keep actual talent ceilings; shared annual cap | Same |
| MENTOR / LONER | Scale the existing eligible veteran mentor term ×1.5 / ×0 | Require actual young teammate and opportunity; no separate additive mentor reward, Loner does not harm own intelligence | Same |
| Drive | Modestly scale an eligible effort term | Trial factor `1 + .02 × (drive - 5)`; aggregate with traits before a single cap, not another work-ethic variable | Same |
| TEACHER / QB_WHISPERER / YOUTH_DEVELOPER | Scale existing coaching opportunities for eligible groups | Trial up to +10% of eligible coaching term; no bonus if term/opportunity absent; overlap shares cap | Coach/progression owner |
| Openness / SCHEME_NERD | Candidate scheme-installation choices and communication | Do not directly add scheme-fit, awareness or growth; choices must feed existing opportunity/continuity inputs once | Later |
| DIVA / GRUDGE_HOLDER; Temperament | Different likelihood of reacting after persistent lost role or logged broken promise | First increment: request event only, no automatic sit-out, trade, salary change or GM value penalty; propose low/neutral/high request odds .05/.10/.15 per eligible annual review, not per week | Later request workflow |
| LOYAL / PEACEMAKER | May moderate eligible relationship deterioration | Shared event policy; not unconditional happiness or inability to request a move | Later |
| LEGACY_BUILDER / COASTING | Different retirement willingness when eligible | Defer numbers until baseline hazard, career goals, active offers and write authority exist; never immortality or forced retirement from label alone | Last |
| Confidence | No direct numeric effect proposed in first increment | Catalogue axis is doubting ↔ brash; quiet style is not inability, and brashness is not proven form volatility | Defer |
| Sociability / Warmth | Voice/relationship context initially | No contract loyalty from warmth, no coachability from sociability | Presentation/context |
| Remaining media/quirk traits | Flavor or specific future contextual events | No need for every trait to alter a number; never Family Man → relocation refusal or Foodie → conditioning penalty | Optional |

The older personality/progression prose proposes Money First → worse upkeep/work after payment, Diva → rookie form harm and low confidence → bad PT flags. **Do not implement those links by default.** Financial preference and work habits are independent. This design recommends revising those earlier proposals with the owning chat; it does not edit their shared progression plan or engine. No new hidden coachability/resilience/IQ sliders are introduced. A trait name alone does not justify a causal impairment.

### Matched development example (illustrative arithmetic, not engine output)

An eligible technical development contribution is +0.60 OVR-equivalent before personality. A proposed Perfectionist effort factor 1.10 yields +0.66; Coasting 0.90 yields +0.54; neutral stays +0.60. Difference attributable to personality is +.06 / -.06, not the whole seasonal delta. A coach bonus cannot also reward the same underlying instruction twice. Missing playing/development opportunity yields zero eligible contribution, not free improvement. Actual per-rating rounding and annual cap enforcement belong to the progression owner.

Measure all player-plus-coach personality effects against the same neutral counterfactual and cap their combined contribution at the existing proposed ±1.5 OVR per season, across checkpoints, rather than allowing ±1.5 each checkpoint. Track ceiling/wear side effects and career accumulation in evaluation; a single-season delta cap cannot bound hidden future advantages on its own.

### Matched trade and career examples (workflow proposals)

A player loses a promised starting role for a full review window. With the same stored annual draw .12, a neutral .10 request probability produces no request; a qualifying higher-reactivity .15 case produces a request. Repeated weekly evaluation must reuse/dedupe the annual decision, not reroll until success. A request enters the owning trade workflow; it neither executes a trade nor lowers the GM's valuation. Team legality and any contractual rights are checked by the existing owner, not inferred from persona.

An aging Legacy Builder within reach of a recorded milestone could be more willing to consider another season **if an acceptable opportunity exists**. No numeric example is claimed until a neutral retirement baseline exists. Health, roster status and valid save-write ownership remain required. A retirement engine is not part of the first contract increment.

## 4. Initialization and persistence through 2034

1. Inventory the actual starting checkpoint. Distinguish verified real people, generated prospects/players and unknown origin; unknown is neutral pending classification. Never classify from an unfamiliar name, age, `origin='SAVE'`, or late draft year alone (future classes can contain real imported prospects).
2. Give each generated person a stable universe-scoped identity at first prospect ingestion, before scouting/interviews/UDFA offers. Candidate key: universe ID + draft class ID + immutable prospect ID. Current season/prospect_key is usable only after its stability is verified. Keep alias mapping to player DB ID and save pgid after drafting; missing or ambiguous handoff blocks that person's activation. Names and team membership never seed identity.
3. Generate six budget-30 sliders and default four existing role-eligible traits once; validate conflicts/categories. Use the existing generator with the stable identity, persist the result and seed/catalogue version. Default four for all generated players avoids giving future stars an extra trait advantage. Do not redraw on promotion, trade, extension or catalogue changes.
4. Initialize generated people already at the checkpoint once. Real people receive only reviewed evidence and baseline for unknowns; safe engine resolution must not call the random fallback. Persist provenance/origin, review/use permissions, generation version and identity linkage in a design-approved extension to current tables, not an undocumented JSON interpretation.
5. Scouting reveals imperfect knowledge of the pre-existing true persona. Interview answers never reroll traits. Traits do not determine draft talent/dev tier; roster position and ratings do not predetermine work ethic or greed. Madden Focus/PT flags are optional input proposals requiring review; never reread engine-produced flags as personality evidence.
6. Log simulation events, then evaluate at most one validated change per person per season. Proposed events create candidates, not automatic stereotype transformations (a title need not make every winner a Legacy Builder). A slider move under budget 30 requires an explicit paired transfer; if no supported counter-move exists, do not move a slider. Trait changes must preserve count/conflicts/category rules. Store before/after, event ID, season, seed and rule version; dedupe reruns.
7. Executed decisions store the resolved persona/version and explanation. Resume/rollback restores the same profile and event history; previews remain write-free. Archive retired people instead of recycling identities. A profile lookup failure must not replace history with a fresh draw.

The identity/provenance/version additions are a design requirement, not a migration supplied here. Existing persona keys, relationships and trait-history tables remain the foundation. Coach/decision-maker initialization needs its own real/generated classification; generating a fictional successor does not establish facts about a real predecessor.

## 5. Implementation sequence and acceptance gates

| Increment | Concrete work | Acceptance gate |
|---|---|---|
| A — identity and safe resolver | Confirm checkpoint/prospect handoff; versioned one-time generated seeding; explicit neutral real/unknown behavior; offline export preview | Duplicate ingest, rename, trade, restart and draft handoff retain one identity/profile; failures cannot silently randomize |
| B — contracts first | MONEY_FIRST/RING_CHASER capped utility delta; common scoring context through counters, waves and previews; decision breakdown | Neutral results identical; identical inputs reproducible; ranking can differ as demonstrated; legal/backup constraints unchanged; offer order and repeated previews do not redraw profiles |
| C — relationship context | LOYAL/HOMETOWN_GUY/GRUDGE_HOLDER with verified data and deduped events | Missing context gives zero; team/home relationship distinctions and shared cap hold |
| D — development/coaches | Implement approved eligible-term adjustments with progression owner | Existing neutral progression fixtures unchanged; combined checkpoint cap and no double-counting; distribution/career drift reported |
| E — requests/retirement | Request workflow, then separate retirement baseline/ownership | No unowned save writes; no request equals automatic move; seeded event replay and missing-data controls |

Before production enablement, review fictional-player outputs and cohort distributions: trait/category counts, correlations with draft rank/dev/position, contract money/contender choices, career development and outliers. Compare identical league seeds neutral versus enabled over multiple cohorts. Report material cap hits, effect frequency, missing-profile rates and reasons; no invented pass thresholds presented as calibration. First deliverable does not require media, conversations, portraits or visible badges.

## 6. Offline verification of this design

Run only synthetic pure functions: validate existing seed output for 100 fictional identities, check budget/conflicts/categories and deterministic replay; calculate the contract table using actual current component functions and the explicitly proposed modifier outside engine code. Confirm the existing engine ignores personas, and run existing free-agency tests. No database, production profile or save writes. Development/request arithmetic above is illustrative, not an assertion of integrated behavior.

**Verification completed (2026-10-30):** 100 fictional seed identities reproduced exactly and passed existing slider/trait validators; synthetic current-engine scoring confirmed the table within documented rounding and confirmed persona inputs currently have no effect. Existing `tests/test_free_agency_engine.py`: **17 passed** (only dependency deprecation warnings). No DB or live save access was used for these checks.

Review decisions: accept/revise the ±.08 contract cap and .10 tradeoff coefficient as the first offline trial; agree that contract motivation does not imply low effort; confirm initialization before scouting with persistent identity; approve the sequence A→B before development coordination. These are reviewable recommendations, not a request to approve production changes now.

### Interactive offline demonstration (2026-10-30)

Run `python -m tools.personality_contract_demo` to regenerate `verify_output/personality_contract_demo/report.json` and its Markdown report. Uses current engine component functions on synthetic offers, plus the proposed modifier in an offline helper. Seven scenarios cover close and clear financial gaps, relationship bonuses, stacking caps, star backup rejection and acceptance-threshold crossings. Twenty-one default comparisons and 525 parameter combinations are reproducible; 6 focused checks plus 17 existing free-agency tests pass. An accompanying conversation comparison exposes strength, cap and relationship-bonus controls; browser checks verified interaction, backup rejection and a 360px layout.

Default findings: cap .04 does not flip the close Money First choice; .08 does. A cap bounds each offer independently, so the A–B gap can change by up to twice the cap. Personality can change threshold eligibility too. ±.08 is utility, not a salary percentage. Noise is deliberately disabled: the close default winning margin is about .01225, below the possible .06 relative choice-noise spread. These are demonstrations, not calibrated league outcomes or executed signings; counters, legal/cap checks, market waves and production activation remain outside the demo.

### Reviewed refinement: career context and earned attachment

JR agreed (2026-10-30) to situational strong ring chasing, earned loyalty separate from hometown familiarity, and an equal-strength loyalty comparison. The first count-based demonstration is superseded. Revised offline trial coefficients remain tuning proposals; no production integrations are activated.

Strong win-now preference requires all three explicit inputs: established career, financial security already achieved, and a usable contender opportunity. The offline contender proxy is current contention score >= .7 with starter/situational depth and no hard role block. This .7 is a trial threshold, not calibrated playoff probability. Elite OVR is neither sufficient nor necessary: a secure veteran role player can qualify. Age, current contract and ratings alone do not establish security. "Financial security already achieved" means `engines/career_earnings.py` `is_secure` returns True: career pay of at least `FINANCIAL_SECURITY_SHARE` (0.15 of a cap), counted in cap shares. False or None (unknown history) leaves the modifier inactive. Before class generation is implemented, distinguish drawing a latent aspiration from activating its strong contract effect. Do not assign strong ring chasing uniformly across young classes.

Attachment comes from simulated history: tenure plus meaningful relationships, role satisfaction and treatment. These are assessment dimensions, not independent bonuses multiplied by a count of friends. Multiple relationship records about the same event must not inflate attachment. The demo takes a tier as an explicit hypothetical input; production history-to-tier thresholds, breaches and repair remain to design. Hometown is a smaller independent location preference and never establishes earned trust. A falling-out can reduce attachment despite long tenure; recorded unresolved breaches must be considered before activating it.

For otherwise equal two-year offers, same role, security, scheme and contention: staying for $10M has base utility .85; leaving for $11M has .8815. Weak attachment .015 leaves B ahead; established .035 makes staying .885, just .0035 ahead; strong .055 makes staying .905. Hometown alone .01 is insufficient to offset the gap; established plus hometown produces .895. Shared cap still applies to all preferences together. These very close cases are intentionally sensitive to existing market noise, which remains disabled here. They do not guarantee a player will sacrifice $1M.

The revised comparison separates each offer's ambition, earned attachment and hometown contributions and offers a career-context control. Synthetic career-building cases default to inactive strong ring preference; the secure veteran case activates it when a suitable contender offer exists. Offline checks now total 23 (6 demo + 17 existing free-agency), with 21 default comparisons and 525 parameter combinations; browser checks cover attachment choice reversal, hometown separation, career/role gates and narrow layout.
