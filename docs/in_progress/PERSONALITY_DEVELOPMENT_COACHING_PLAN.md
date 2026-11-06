# Project Mu: Personality Development and Coaching Plan

**Status:** approved by JR (2026-11-03), Q1–Q4 settled as recommended (§8); building, wave 1 of 3 (2026-11-06). Approval covers building the units; enabled configs and each activation (learning, then opportunities) still need JR's separate go-ahead (§2.7 gates G3–G5).
**JR decisions this plan follows (2026-11-03, PERSONALITY_PLAN §5.1, D11–D13):** every player has a generated profile (the 2026 roster at the league start, prospects at class entry), editable by JR. Existing real coaches get reviewed profiles from a separate workstream; personas named after real people are fictional characters inspired by them. This plan also owns the eventual consumer of the V2.9 inbox's team-vision playing-time setting (§2.5).
**Builds on:** PROJECT_SPEC §§2.17c, 2.19, 2.21, 3.3n, 3.3p, 7.11; [core README](../../personality/README.md); [PERSONALITY_PLAN.md](../in_progress/PERSONALITY_PLAN.md) §§5.1–5.2; [generated-effects design](../in_progress/PERSONALITY_GENERATED_EFFECTS_DESIGN.md). Active progression implementation owns the current entitlement/state system; reconcile interfaces before integrating.

**Goal:** Let persistent preparation and teaching influence eligible development, and coaching preferences influence close opportunity decisions, with one owner for every effect. Separate permanent learning from temporary form, health and role choices. Measure each change against a replayable neutral control before activation.

**Delivers (proposed, not existing):**
- `engines/personality_development.py`: pure preparation, teaching, mentor policy and annual attribution limiter.
- `engines/personality_coaching.py`: pure opportunity preferences and scheme-choice advice.
- `engines/personality_development_inputs.py`: read-only provenance-scoped adapter after verified identity binding exists.
- `tools/personality_development_demo.py`: synthetic paired-control CLI, without a database connection.
- Disabled integration seams in existing progression receipts and staff depth-chart scoring, after owner handoff; tests and rollout gates.

## 0. Survey (code facts that set the scope)

Surveyed the canonical WSL working tree on 2026-10-31. Progression and shared documents have concurrent edits; recheck these references at implementation handoff. No live/archive database access.

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `personality/core.py:144–163` exposes generated profiles and filters real fields by exact feature permission; missing is neutral. `personality/README.md:3` assigns numeric consequences to features. | Consume scoped views; no second profile or `newsroom.personas.get_persona` fallback. Players are all generated, so they read in full. Reviewed real-coach profiles are `real` origin; today's filter would hide their fields from `coaching`, and the core extensions plan (D1, approved 2026-11-03) changes `feature_view` to read them in full. That change is a dependency of the teaching/opportunity channels. |
| S2 | `personality/store.py:35–61` propagates failures and rejects unmanaged canonical edits; `:85–116` initializes once; `:119–142` binds/resolves verified aliases. | Reads never seed; pipeline identity binding is an external prerequisite. |
| S3 | `personality/core.py:166–225` enforces chronology, revision, annual change limit and simulation evidence for real people. `personality/store.py:145–175` deduplicates event/source and uses caller transactions. | Feature may later propose reviewed event changes, never mutate profiles directly or infer evolution from one good game. |
| S4 | `db/migrations/2027_01_09_personas.sql:21` really contains exact-30 `chk_persona_sliders_budget`. `2027_01_16_personality_core.sql:1–3` leaves legacy constraints and activation unchanged. | Superseded 2026-11-02: the cap-relative plan built the 24–36 band in Python and SQL (`2027_01_17_persona_slider_band.sql`, applied to `madden_test`). This plan owns neither; single-slider evolution works where the migration is applied. |
| S5 | `engines/progression.py:155–185,241,326` retains annual scalar math with zero persona/coach terms. `:1272–1275` refuses legacy direct commit with managed state. | Do not activate legacy constants as another OVR award; multiplying a zero coach term creates no teaching effect. |
| S6 | `engines/progression_policy.py:129–141` defines fractional entitlements; the function below requires meaningful evidence, restricts supported skills and separates REVIEW eligibility/reliability. | Modify eligible fractional learning once; persona cannot invent opportunity, potential or dev-tier writes. |
| S7 | `engines/progression_checkpoint.py:39–71` loads authoritative base/band and returns prior events on retry; `:72–132` accumulates learning/carry, quantizes and prepares one event. | Hook before quantization; immutable input and attribution snapshots travel in that event. |
| S8 | `engines/progression_state.py:73–100` owns IDs/remainder/quantization/form composition; `:201–207` prepares without awarding; `:255–314` acknowledges verified batches and advances annual accounting. | No parallel authoritative award store; only APPLIED receipts spend budget. |
| S9 | `engines/progression_offseason.py:14–49` subtracts applied awards, avoids double-paying carry and avoids clawing back positive learning from a reduced estimate; `:67–133` prepares final events. | Full-year effect targets include checkpoint effects once, then use existing annual reconciliation. |
| S10 | `engines/progression_evidence.py:71–105` uses completed player-aligned games for mental intervals (default four), technical midpoint (nine) and closure; `:108–120` distinguishes measured/proxy opportunities. | No weekly personality lottery; trades do not restart evidence; missing film/practice logs prove nothing about effort. |
| S11 | `engines/progression.py:558–601` owns continuity, mentor selection and growth: same group, mentor age 29+/Awareness 85+, young recipient through 25. `:612–622` also uses mentor presence for QB behavior. | Isolate and modify the existing growth component only. No second mentor award, friend-count bonus or extra PT effect. Roster availability is not proof of actual instruction. |
| S12 | `engines/form_engine.py:93,176,211` computes form and overlays base; `engines/progression_checkpoint.py:208–280` verifies output, stores form and publishes a whitelist projection on acknowledgement. | Never learn from form-inflated ratings; mood, health and confidence remain separate. |
| S13 | `engines/depth_chart_engine.py:165–171,278–302` has zero-scale youth/veteran trust plus separate scheme fit; `:328–378` excludes out players and greedily assigns base starters. | Use explicit bounded scoring inputs, preserve uniqueness/eligibility and default neutrality; do not enable global trust or add scheme fit twice. |
| S14 | `newsroom/config/persona_traits.json:691–875` contains work/teaching traits with zero or empty effects; MENTOR/LONER are at `:260–296`. | Catalogue placeholders are not approved coefficients; no new coachability/IQ slider. |
| S15 | `engines/coach_hot_seat.py:19–34,50–100` owns firing budgets/protections and seeded decisions; `engines/coaching_carousel.py:204–208,321–327` owns opening appeal/churn and hiring. | Owner patience and hiring are later integrations in those engines, not a second scheduler. |
| S16 | `engines/progression_report.py:24–29,185,276` whitelists/sanitizes public projections; `tests/conftest.py:1–12` documents DB isolation. | Extend safe explanations only; synthetic testing first, no wholesale napper tests. |

**Existing versus proposed:** Core is implemented but not bound to live ingestion. Progression now has permanent per-rating entitlement and receipts, not just older OVR prose. No implemented combined personality annual cap was found. The design's ±1.5 OVR/year, ±10%, and LONER ×0 examples are review proposals, not enforced/calibrated behavior. Existing mentor presence does not establish mentoring quality. The cap-relative plan is built and separately owned (2026-11-02, [docs/done/CAP_RELATIVE_CONTRACTS_PLAN.md](../done/CAP_RELATIVE_CONTRACTS_PLAN.md)); the agent-wire plan is a separately owned proposal whose unapproved interfaces are dependencies to reconcile, not facts.

## 1. Scope

**In:**
- Every player's generated profile (the 2026 roster and each class), reviewed real-coach profiles once their workstream lands, persistent identities across years and transfers, and later recorded simulation evolution.
- Bounded film/technical preparation and coach teaching on eligible mental/technical learning; isolated existing mentor growth.
- Close youth/veteran opportunity decisions; advice among caller-supplied legal scheme options; independent activation gates.
- Immutable snapshots, annual attribution, deterministic replay, synthetic controls, receipt/year-end integration and default-off hooks.
- Synthetic 2026–2033 seasons, 2034 JR handoff and longer runs for compounding. Imported real-player classes are generated at entry like any other class.

**Out (non-goals):**
- Core seeding/binding, researching real coaches (their workstream), catalogue edits, slider migration, autonomous profile evolution and production activation.
- Contract prices/earnings/security, trade requests and retirement. Money preference is not effort. Strong ring chasing belongs elsewhere and needs established career, financial security and usable contender role, never an elite-OVR shortcut.
- Personality effects on physical ratings, wear, durability, health/recovery, upkeep, ceilings, REVIEW odds, breakthroughs, PT flags and actual dev abilities in release one. WORKOUT_FANATIC can appear in preview with an explicit deferred reason; no unsupported training effect is silently invented.
- Practice simulation, rotations/snap allocation, automatic scheme installation, second hiring engine, mood or relationship store. No friend-count attachment logic.
- Editing active progression files before owner handoff, or editing shared plans/spec during this drafting task.

## 2. Design

All new names below are **proposed**. Existing APIs are identified explicitly.

### 2.1 Inputs, scopes and modes

Use existing `feature_view(profile, 'development')` for players/mentors and `'coaching'` for coaches/owners. A real coach's teaching traits come from his reviewed profile; a public scheme tendency alone does not establish teaching ability. Until reviewed coach profiles exist, coaches are missing and their channels are neutral. Every profile can later evolve from recorded simulated events through reviewed core changes; unknown origin remains neutral. Never infer private motivations from contract size, quietness, name, age, rating or loader origin.

Frozen `DevelopmentConfig` fields:

| Field | Type/default | Meaning |
|---|---|---|
| `version` | `str` | Reviewed policy version, part of immutable input. |
| `mode` | `'off'|'shadow'|'enabled' = 'off'` | Off preserves output without querying core; shadow returns neutral target plus private comparison. |
| `weights` | `dict[str,float] = {}` | Allowed keys: drive, film, technical_preparation, coasting, teaching, qb_teaching, mentor. |
| `max_relative_change` | `float = 0` | One combined scaling cap, constrained to 0..1. |
| `max_annual_abs_points` | `float = 0` | Sum of absolute attributed rating-point spend. |
| `max_annual_ovr_difference` | `float = 0` | Paired neutral/effect annual OVR envelope. |
| `max_per_rating_annual_difference` | `float = 0` | Per-rating annual absolute envelope. |

Reject NaN/infinity, unknown weights and invalid signs. Film/technical/teaching/mentor weights are nonnegative; coasting nonpositive. Enabled config needs an explicit reviewed version and nonzero caps. No active numerical preset is selected by this plan. Missing profile gives zero adjustment; schema/storage/corruption failure aborts an enabled batch, not silently neutralize a committed decision.

Proposed read-only adapter:

```python
def load_development_context(cur, *, universe_id: str, player_id: int,
    earned_season: int, as_of_week: int, player_alias: tuple[str,str],
    staff_aliases: list[tuple[str,str]], mentor_alias: tuple[str,str] | None,
    source_event_ids: list[str]) -> dict: ...
```

Returns `{player_view, coach_views, mentor_view, intervals, source_event_ids, cutoff, context_hash}` through existing `store.resolve_alias/read` and `feature_view`. Intervals are `{start_week:int,end_week:int,coach_ids:list[str],mentor_id:str|None}` backed by caller-verified canonical staff/roster records. Split exposure at trades/coordinator changes; never apply destination staff retroactively. Missing historical coverage neutralizes the affected channel with a reason. Reads never call ensure/seed or name-match. Verified pipeline alias binding remains another owner's prerequisite.

### 2.2 One fractional preparation/teaching path

```python
def evaluate_learning_effect(*, neutral_result: dict, player_view: dict,
    coach_views: list[dict], mentor_view: dict | None, context: dict,
    config: DevelopmentConfig) -> dict: ...
```

`neutral_result` is existing `evaluate_development` output for identical authoritative base, evidence, band, weights and draws. Return `{skill_deltas:dict[str,float], attribution:dict}`. Do not alter eligibility, evidence counts, target band, desired label or actual dev writes. REVIEW remains unchanged in release one to avoid rewarding both learning and potential from the same trait.

For each positive eligible mental/technical entitlement `b`, sum eligible dimensionless channels `s`, clamp once to `[-max_relative_change,+max_relative_change]`, propose `b*(1+s)`. Zero neutral learning stays zero. Supported-skill mapping and 0..99 constraints remain progression-owned. Physical/fixed skills and negative aging/injury deltas are unchanged. Reuse neutral variation/review draws; a personality revision must not reroll ordinary progression.

| Channel | Eligible effect | Ownership/exclusions |
|---|---|---|
| Drive | Center existing scoped drive at 5; modest eligible learning adjustment. | Shares trait cap; no new work-ethic field. |
| FILM_ROOM_RAT | Awareness/PlayRecognition preparation. | No new ceiling, practice evidence or production bonus. |
| PERFECTIONIST | Supported positive technical learning. | No unobserved skills or automatic mental stacking. |
| COASTING | Reduce positive preparation within shared bounds. | No health damage or subtraction of acknowledged growth; never inferred from money/payday. |
| TEACHER | Instruction in actual side/role and covered evidence interval. | Scale baseline eligible learning, not zero legacy COACH_TERM. |
| QB_WHISPERER | Teaching for QB recipients. | Use strongest generic/specialty contribution, not both. |
| YOUTH_DEVELOPER | Opportunity preference only, §2.5. | No simultaneous direct teaching bonus for this trait. |
| MENTOR | Existing component only, §2.3. | No separate checkpoint award. |

Choose the strongest eligible teaching contribution per skill/interval; do not sum HC+OC+assistant duplicates. Weight by eligible observed exposure, not calendar weeks alone. Missing assistant identity contributes nothing. Disciplinarian/Players' Coach do not imply better learning; sociability/warmth/confidence do not imply coachability. No extra hidden receptiveness scalar. Quiet does not imply low confidence; brashness does not prove form volatility.

### 2.3 Mentoring without duplicate growth

Keep existing `find_mentor`/`mentor_term` eligibility as neutral baseline. Agree deterministic ties with the progression owner before changing current roster-order ties. Snapshot selected identity and exposure cutoff. More veteran teammates or friends do not multiply mentoring or earned attachment.

Proposed `mentor_adjustment(*, neutral_component:dict[str,float], mentor_view:dict|None, verified_exposure:bool, config:DevelopmentConfig) -> dict` returns a bounded difference from the **existing mental mentor rating component**. Progression must isolate its distributed component; this feature must not translate annual 0.15 OVR into a second award. MENTOR can scale that contribution; missing profile/exposure gives zero difference. LONER alone does not erase neutral mentoring or establish refusal. A recorded refusal requires a separate reviewed event policy.

If progression cannot isolate that component and reconcile it annually, mentor personality effects remain disabled. Shadow tests can inspect it. Existing mentor PT behavior gets no added personality multiplier. No new checkpoint mentor award while annual progression owns the component. This recommendation supersedes the earlier design's LONER ×0 shortcut, pending JR review.

### 2.4 Annual caps, attribution and reconciliation

```python
def limit_annual_effect(*, annual_start: dict, neutral_target: dict,
    proposed_target: dict, applied_effects: dict, config: DevelopmentConfig,
    ovr_at: Callable[[dict],float]) -> dict: ...
```

Inputs are fractional absolute rating targets, not scalar OVR awards. Return `{target, annual_effects, remaining_budget, capped, reasons}`. Apply all three combined player+teacher+mentor limits: per-rating absolute difference; cumulative absolute rating-point spend; paired OVR difference under identical versioned weights/position. Net cancellation across skills and rounded OVR plateaus cannot hide movement. Absolute incremental spend counts both signs; unused allowance never carries years.

The older ±1.5 OVR example may be an offline sweep candidate, not a default or approved entitlement. Apply caps before existing quantization; preserve separate fractional neutral/effect attribution. `applied_effects` comes only from APPLIED receipt snapshots, never PREPARED proposals. Budget is shared across mental/technical/postseason/annual events in one earned season and does not reset on trades, scheme changes, profile revisions or config switches. Midseason policy changes need explicit reconciliation and a new version, not fresh allowance.

Keep immutable neutral path state sufficient to reproduce the year-start paired counterfactual; reevaluating neutral policy on personality-inflated bases is not an adequate control. This is private receipt metadata, not another authoritative rating table. Exact annual adapter must be settled with progression: full-season target includes the same accumulated effect once, then existing `reconcile_annual` subtracts applied total awards. Carry is never added again. Legacy PERSONA_TERM/COACH_TERM remain zero. If this cannot be proved, enabled integration stops at the spike gate.

Proposed `metadata.personality_development_v1`:

```text
{mode, policy_version, config_hash, context_hash,
 profile_refs:[{persona_id,revision,catalogue_version,scope}], evidence_ids,
 neutral_skill_deltas, raw_channels, capped_channels, requested_effect,
 attributed_effect, annual_abs_spend, neutral_target, effect_target, cap_reasons}
```

Freeze effective revisions per evidence interval; future profile changes affect only future evidence. Original pending/applied retries return their original snapshot. Attribution is updated atomically by the existing verified receipt lifecycle. No separate award commit or DB connection.

### 2.5 Youth/veteran opportunities and scheme advice

Frozen `CoachingConfig`: `version:str`, `mode:str='off'`, `weights:dict={}`, `max_depth_score_shift:float=0`, `close_score_window:float=0`, `max_scheme_score_shift:float=0`. Finite nonnegative caps; enabled requires explicit reviewed config. Use existing depth-chart youth/veteran age conventions through caller-normalized cohort weights clipped to 0..1; extreme ages cannot amplify preference.

```python
def opportunity_adjustments(*, candidates: list[dict], coach_view: dict,
    config: CoachingConfig) -> dict: ...
# candidates: {player_id:int, slot:str, neutral_score:float, eligible:bool,
#              out:bool, cohort:'youth'|'veteran'|'other',cohort_weight:float}[]
# result: {adjustments:{slot:{player_id:float}}, reasons:list[dict]}

def rank_scheme_choices(*, choices: list[dict], coach_view: dict,
    config: CoachingConfig) -> dict: ...
# choices: {choice_id:str,legal:bool,neutral_score:float,
#           continuity_cost:float,supported_tendency:bool}[]
# result: {ranked:list[str],advice_only:True,attribution:list[dict]}
```

YOUTH_DEVELOPER favors youth only inside the configured neutral-score gap from the strongest eligible candidate for a slot. The V2.9 inbox stores a team-vision `playing_time` setting (`YOUTH|VETERANS|MERIT`) with no consumer (its O1); its eventual consumer is this seam, as one more bounded input inside the same close-score window and cap, never a separate adjustment, and only after opportunity activation. Veteran preference requires a reviewed specific coaching input; OLD_SCHOOL alone is not evidence of veteran favoritism. Neutral score already includes ability, eligibility penalty and scheme fit. Clip once, then run existing greedy staff algorithm. Preserve injuries, base-starter uniqueness, fallbacks and ties. This is neither rotation logic nor guaranteed snaps.

Proposed optional keyword `score_adjustments=None` on existing `staff_depth_chart`, threaded internally by its owner, avoids enabling global COACH_TRUST_SCALE for unrelated callers. Off/zero produces identical charts. Validate finite adjustments, known eligible player/slot and agreed score caps. Current production callers pass none until a later reviewed execution integration.

More opportunity may cause more snaps/performance and indirect development. Do not add a YOUTH_DEVELOPER learning bonus too. Measure this in a separate feedback arm; the direct-learning cap does not bound role-mediated career advantages. Opportunity activation needs separate JR review.

Scheme advice ranks only caller-supplied legal choices. Neutral ordering changes only for a supported scoped football tendency matching that option; general SCHEME_NERD/high openness offers context, not proof a particular scheme is better. Stable ties use choice_id. No bonus for churn or random weekly installation. Existing owner applies any eventual scheme at its legal stage and owns continuity cost once; depth chart owns archetype fit once. This plan writes no scheme/playbook/installation or extra adaptation penalty. Live scheme execution is deferred.

### 2.6 Later event evolution, hiring and owner patience

Receipts can supply verified source IDs for later reviewed sustained preparation, development milestones or mentoring events. One good week or invented practice prose cannot change a profile. Core owns revision/annual limits; a cross-feature coordinator arbitrates its single annual change slot: the arbiter in the [personality evolution plan](../done/PERSONALITY_EVOLUTION_PLAN.md), which takes this plan's candidates as `source 'DEVELOPMENT'`. unknowns need classification; slider changes wait for both band migrations.

Later hiring preferences belong in `coaching_carousel.opening_appeal`/`pick`; owner patience belongs in existing hot-seat policy. Both are planned in the [owners, scouts and voters plan](../not_started/OWNERS_SCOUTS_VOTERS_PLAN.md). Preserve one hiring cycle, user-team restrictions, tenure/championship protections, firing budgets and recorded moves. Do not infer teaching quality from win totals, count churn twice, add an owner scalar store or build a parallel hiring engine. These are dependency notes, not implementation units.

### 2.7 CLI, visibility and activation gates

`python -m tools.personality_development_demo --scenario all --seed 2026 --seasons 8 --config PATH --out PATH` consumes synthetic JSON only and writes report.json/README.md. Optional `--sweep PATH` reads explicit candidate configs. No DSN/migrate/apply/seed/live-export flag. Missing config means off. Scenarios: learning, teaching, mentoring, opportunity, scheme, reconciliation, all. Reject unknown fields/NaN. Reports label candidate coefficients, direct/indirect differences and causal interaction residuals.

Private attribution separates baseline learning, preparation, teaching, mentoring, caps, rounding, receipt status and multi-year divergence. Public projection uses existing safe PERSONALITY/COACHING reasons only for supported applied effects; no seeds, private motives, evidence text, hidden ceilings or shadow claims. No new endpoint/frontend.

G0: JR plan approval (done 2026-11-03). G1: owner reconciliation/spikes. G2: off/zero equality and synthetic invariants. G3: JR reviews paired multi-season reports and explicit strengths/caps. G4: verified binding, schema readiness, neutral bootstrap and existing save verification gates; shadow changes no target. G5: separate direct activation decision for learning, then separately for opportunities. Profile availability never activates effects. No gate is executed by drafting this plan.

## 3. Risks and unknowns

| # | Risk | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Active progression changes interfaces. | Re-read sources and agree handoff before U3. | Preserve pure/offline work; revise U3, never edit concurrently. |
| R2 | Mentor isolation or annual neutral/effect reconciliation fails. | Spike A traces synthetic checkpoints, distribution and annual targets. | Mentor stays off; enabled learning blocked if annual accounting cannot be proved. |
| R3 | OVR plateaus/archetype changes hide large rating shifts. | Spike B tests synthetic bases with checked-in weights. | Rating bounds remain primary; refuse candidates when all caps cannot be proven. |
| R4 | Historical staff/exposure aliases are unavailable. | Spike C inventories source adapters without DB queries. | Unsupported channels neutral; no retroactive destination staff. |
| R5 | Opportunity feedback compounds. | Direct-only/opportunity-only/combined long-run arms. | Hold opportunity activation; direct cap is insufficient evidence. |
| R6 | Older money/quiet/Loner shortcuts survive. | Invariance cases and consolidated §7 revisions. | Reject those mappings rather than reinterpret zero catalogue fields. |
| R7 | Missing migration resembles absent profile. | Inject schema/connection/corruption errors. | Abort enabled batch; off never queries core. |
| R8 | Strengths lack calibration. | Explicit sweeps by age/group/base/profile and reviewed reports. | Ship no active preset or claim of empirical league stability. |

**Spike first:** coordinator runs A (annual neutral/effect accounting and mentor component contract), B (safe cap evaluation with versioned weights), C (verified alias/staff/exposure source availability). Estimate: one bounded research session, not a calibration promise. A/B can change design and must settle before U1 interface freeze; C blocks non-neutral integration, not pure experiments. Record findings here. No DB access needed. If additional shared files/schema/evidence types are required, return an amended plan to JR; do not expand ownership silently.

## 4. Tests (synthetic, no live DB)

- `tests/test_personality_development.py`: off/zero equality; scopes; eligible channels; one aggregate cap; strongest teacher/specialty; overlap; no opportunity/unsupported skill; no physical/band/REVIEW/dev mutation; money/quiet/confidence/hometown invariance; mentor absence and Loner neutrality.
- `tests/test_personality_coaching.py`: close-score boundaries, clipped cohorts, illegal/out candidates, neutral ordering and stable ties; no scheme-fit duplication or execution.
- `tests/test_personality_development_inputs.py`: mocked cursor/core only; aliases/cutoffs across trades/staff changes; absence versus failure; no ensure/seed/get_persona; exact feature scopes.
- `tests/test_personality_development_demo.py`: strict fixtures/CLI/no DB; identical inputs/seed/config give byte-stable canonical JSON; revisions do not reroll baseline; paired 2026–2033 trajectories/2034 handoff and longer compounding; interaction residuals visible.
- `tests/test_personality_development_integration.py`: real module calls with fake receipt storage; PREPARED/crash/retry spend nothing, APPLIED spends once; changed retry/stale state fails; historical replay cannot overwrite; fractional carry/remainder once; annual combined caps across checkpoints/postseason/trades; config cannot reset spend; public leak tests; off performs no core query.
- `tests/test_personality_depth_integration.py`: real staff algorithm with synthetic players/weights; injuries/fallbacks/uniqueness retained; defaults identical; learning-only activation cannot change chart.

Paired runs hold base, age, underlying OVR, band, evidence, legal choices, health, weights and draws fixed. Change only permitted trait/coach input. Singleton views isolate mechanisms, not valid production seeds; also exercise valid core-generated profiles. Compare full rating vectors and fractional learning, not just rounded OVR. Arms: neutral, preparation, teaching, mentor, opportunity, combined. Report indirect feedback separately and show unallocated interaction residuals. Assert no award where neutral contribution is zero; cancellation/rounding cannot bypass limits.

Future commands (not run while drafting):

```text
.venv/bin/python -m pytest -m 'not integration' tests/test_personality_development.py tests/test_personality_coaching.py tests/test_personality_development_inputs.py tests/test_personality_development_demo.py tests/test_personality_development_integration.py tests/test_personality_depth_integration.py -q
.venv/bin/python -m pytest -m 'not integration' tests/test_progression_policy.py tests/test_progression_evidence.py tests/test_progression_checkpoint.py tests/test_progression_offseason.py tests/test_progression_state.py tests/test_progression_report.py tests/test_depth_chart_engine.py tests/test_form_engine.py -q
.venv/bin/python -m tools.personality_development_demo --help
```

Integration-named new files above are ordinary offline tests using fakes, not DB integration markers. Any later PostgreSQL verification uses an explicitly supplied disposable isolated schema/database without live copying and requires amended ownership if fixtures are added. Never connect to madden/archives or run napper wholesale. Current progression testing remains with its active owner until handoff.

## 5. Work breakdown

Shared documents are orchestrator-only: PROJECT_SPEC.md, this plan, CLAUDE.md and consolidated personality references. Each unit receives current sources, this plan and the frozen spike/interface note.

### U1: Pure development and coaching policy

- **Owns:** `engines/personality_development.py`, `engines/personality_coaching.py`, `tests/test_personality_development.py`, `tests/test_personality_coaching.py` (all new).
- **Reads:** core README/API, progression policy/evidence, depth chart and §§0–4.
- **Depends on:** A/B spike outcomes; no code unit.
- **Done when:** exact pure interfaces exist; defaults are neutral; invalid active configs fail; every causal mapping tested; no DB writes or active preset.
- **Verify:** `.venv/bin/python -m pytest -m 'not integration' tests/test_personality_development.py tests/test_personality_coaching.py -q` passes.

### U2: Synthetic experiment runner

- **Owns:** `tools/personality_development_demo.py`, `tests/test_personality_development_demo.py` (new).
- **Reads:** U1, progression policy/evidence/state/offseason, OVR weights and §4.
- **Depends on:** U1.
- **Done when:** named scenarios and separate causal arms run from fixtures, replayable reports label candidate strengths and feedback limitations. Outputs are scratch artifacts, not additional source ownership.
- **Verify:** `.venv/bin/python -m pytest -m 'not integration' tests/test_personality_development_demo.py -q` and demo `--help` pass; inspect scratch report for annual caps/rounding/compounding.

### U3: Progression-owned neutral integration

- **Owns:** `engines/personality_development_inputs.py` (new), `engines/progression_checkpoint.py`, `engines/progression_offseason.py`, `engines/progression_report.py`, `tests/test_personality_development_inputs.py`, `tests/test_personality_development_integration.py` (new tests).
- **Reads:** U1, current progression_state/policy/inputs/progression.py, core/store, A–C contracts.
- **Depends on:** U1, U2 report review, progression owner's explicit file handoff and neutral-control completion. Verified binding required for non-neutral contexts.
- **Done when:** optional context/config defaults off; effect runs before quantization, snapshots use existing receipts, annual target includes effects once and public reports sanitize. Mentor stays disabled unless isolated component is supplied without additional shared-file edits. No schema change/activation.
- **Verify:** `.venv/bin/python -m pytest -m 'not integration' tests/test_personality_development_inputs.py tests/test_personality_development_integration.py tests/test_progression_checkpoint.py tests/test_progression_offseason.py tests/test_progression_report.py -q` passes after handoff.
- **Interface reconciliation:** proposed preparation keywords `development_context=None, development_config=None`; existing `config` retains progression meaning. Freeze input before preparation, never reread profile on ack. Annual hook placement depends on Spike A. If progression.py/policy/state/schema edits are needed, stop for revised exclusive ownership/review; not implicitly claimed.

### U4: Depth-chart opportunity seam

- **Owns:** `engines/depth_chart_engine.py`, `tests/test_personality_depth_integration.py` (new).
- **Reads:** U1 coaching, existing chart tests and staff-setting callers.
- **Depends on:** U1 and depth-chart owner handoff; not U3. The V2.9 inbox (builds first) reads `engines/depth_chart_engine.py`; re-read it after that lands.
- **Done when:** optional score_adjustments is bounded/validated, neutral charts identical, original eligibility/uniqueness preserved. Production callers pass none; scheme execution remains absent.
- **Verify:** `.venv/bin/python -m pytest -m 'not integration' tests/test_personality_coaching.py tests/test_personality_depth_integration.py tests/test_depth_chart_engine.py -q` passes.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| Prebuild | Spikes A–C/owner reconciliation | JR approval (done 2026-11-03) | Settle annual accounting, safe caps and sources. |
| 1 | U1 | Frozen interfaces | Coherent pure policies/tests. |
| 2 | U2, U4 | U1; U4 handoff | Runner/chart seam share only read-only policy interfaces. |
| 3 | U3 | U1/U2 review and progression handoff | Receipt/annual integration requires stable owner/control evidence. |
| Gates | JR config/activation review | Relevant checks/reports | No merge automatically activates effects. |

U4 may be deferred independently.

**File ownership check (sorted; no path duplicated):**

| File | Unit |
|---|---|
| `engines/depth_chart_engine.py` | U4 |
| `engines/personality_coaching.py` | U1 |
| `engines/personality_development.py` | U1 |
| `engines/personality_development_inputs.py` | U3 |
| `engines/progression_checkpoint.py` | U3 |
| `engines/progression_offseason.py` | U3 |
| `engines/progression_report.py` | U3 |
| `tests/test_personality_coaching.py` | U1 |
| `tests/test_personality_depth_integration.py` | U4 |
| `tests/test_personality_development.py` | U1 |
| `tests/test_personality_development_demo.py` | U2 |
| `tests/test_personality_development_inputs.py` | U3 |
| `tests/test_personality_development_integration.py` | U3 |
| `tools/personality_development_demo.py` | U2 |

**Migration/idempotency/rollback:** No new migration proposed. Existing progression JSONB holds versioned attribution; core sidecars/aliases and managed progression schema are external prerequisites, never automatically installed. Off works without core queries. Explicit schema readiness precedes shadow/enabled. Older receipts without metadata mean no recorded effect, not reconstructed history. No retrospective 2026–2033 motive/award backfill or reseeding.

Rollback disables future effect preparation and retains receipts/profiles. Rebuild only unacknowledged proposals through the owning workflow; do not delete APPLIED history, subtract growth automatically or relabel old configs. Wrongly applied saves require existing whole-save recovery and verified state reconciliation. Disabling does not reverse permanent growth. Core/slider migration downgrade is not this feature's responsibility; never narrow the band over stored out-of-band totals.

## 7. Spec changes when built

The parent consolidates these when the units land.

- PROJECT_SPEC §1.1: new policy/adapter/demo/test paths and actual status.
- §§2.21, 3.3n: fractional placement, paired control, annual attribution/caps, year-end remainder, immutable metadata, receipt spending; legacy persona/coach constants remain zero.
- §2.17c: optional per-slot scoring, close candidate preference, default neutrality, preserved eligibility and indirect feedback gate.
- §2.19: later hiring/patience dependencies only, no implemented effect claim.
- §§3.3p, 7.11: exact permission names, generated players read in full, reviewed coach fields once exposed, missing/error distinction, binding prerequisites, no active presets, reviewed evolution and SQL/Python band dependency.
- §6: actual hook order only when built; preserve Advance Week → save → ingest and verified writer lifecycle. Availability never implies activation.
- PERSONALITY_PLAN §6/generated-effects design §3: reconcile money-to-upkeep, quiet/confidence impairment, Loner mentoring shortcuts; distinguish old trial coefficients from current behavior. Keep financial security/earned attachment separately owned. The cap-relative exact-30 survey error is corrected (2026-11-02).
- Active PROGRESSION_PLAN references: parent coordinates with existing owner; do not overwrite current entitlement/state design with old OVR-only prose.
- Public/private projection and completed test evidence, with closed gates explicit. This planning file changes no runtime behavior; JR reserved consolidated spec edits for parent.

## 8. Decisions for JR

JR approved all four recommendations on 2026-11-03. Numerical tuning is a later report review, not a request to guess coefficients.

| # | Question | Decision (JR, 2026-11-03) |
|---|---|---|
| Q1 | Restrict release-one permanent effects to supported mental/technical learning and isolated mentoring, leaving physical maintenance, wear, ceilings, REVIEW odds and PT flags unchanged? | Yes. Use the entitlement lifecycle and avoid duplicate preparation effects. Preview maintenance as deferred. |
| Q2 | Separate opportunity activation from direct development, with YOUTH_DEVELOPER owning opportunity only in release one? | Yes. Build neutral seam/offline experiments; review snap feedback separately. Defer live scheme installation, hiring and owner patience to their owning engines. |
| Q3 | Require verified exposure for mentor personality adjustment, preserve neutral mentoring and avoid LONER-as-refusal? | Yes. One existing component, no friend counts or extra awards; disable adjustment if owner cannot isolate it. |
| Q4 | Require reviewed configs/multi-season paired reports and both rating-point/OVR caps, rather than treating ±1.5 OVR as approved? | Yes. Off/zero defaults; prove annual reconciliation before activation. Architecture approval selects no coefficients. |

**Review boundary:** plan approval selects no coefficients and activates nothing; enabled configs and activation are gates G3–G5.

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
