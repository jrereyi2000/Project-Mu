# Project Mu: Personality Contract Policy Plan

**Status:** approved by JR (2026-11-03), Q1–Q4 settled as recommended (§8); building, wave 2 of 3 done. Approval covers building the units; production migration, seeding and activation each still need JR's separate go-ahead (§2.6).
- As built (wave 2): `acceptance` and `_counter` also take keyword-only `persona` (the view) and `trace` (a dict receiving bar parts, counter attempts, stale-score and the shadow alternative), so the return shapes stay unchanged. With mode off (or no policy) every path is the original, including `contract_inputs` entries whose policy is off (nothing is recorded). `transactions_engine.extension_offer_terms(player, contract)` takes the new-money contract part. In active mode a rejected extension leaves the original contract and the 5th-year option check still runs; a REJECTed re-sign may still be tagged (consent recorded as REJECT). `load_contract_context` adds a `current_team` provider and an optional batch-scoped `cache`; `relationship_teams` maps the relationships plan's `relationship_context` (attachment eligibility stands for satisfactory role/treatment; unresolved breaches for breach and certified grudge). The league office still passes no inputs (the activation plan's `PersonalityDeps`).
- As built (wave 1): `read_contract_activation` returns `{config, source, families}`: one mode per decision (the most active family's), a family in a lower mode disabled for that decision. The migration is not folded into `db/init_db.sql` (not owned here, like the core/relationship/evolution migrations). The store's opt-in scratch tests need `CONTRACT_POLICY_TEST_DSN` (madden_test or a scratch database); not run here (no PostgreSQL).
- As built (wave 0 spike, offline, 2026-11-06): **gate** — contender certification is `contention >= contender_floor` on the offer team's contention score, which comes only from the frozen team snapshot of the batch, so offer money can never move it; money monotonicity and fixed-certification contention monotonicity hold, and the report's gate sweep lists the cross-certification jumps for JR (Q2's recommendation, no semantic change). **Extension representation** — the incremental new-money years only (the owed final year excluded), and an accepted counter rebuilds `[owed year] + make_contract(counter)` exactly as today's extension does, so extension activation is not blocked. **Runner** — the league office's `Deps.resign_decisions` / `run_wave` / `udfa_market` are the only callers (no `street_market` caller); this plan adds the `contract_inputs` keyword, the activation plan wires it. **Migration** `2027_01_23` is free. **Providers** — earnings through `career_earnings.is_secure`; relationships through `personality.relationships.relationship_context` (edges mapped by an adapter); the shared switchboard `personality.features` does not exist yet, so `read_contract_activation` reads off until it does.
**JR decisions this plan follows (2026-11-03, PERSONALITY_PLAN §5 step 3, §5.1, D8–D13):** every player has an engine-generated profile (existing players once at the 2026 start, prospects at draft-class entry), and JR can edit any of them; the relationships plan owns one-way trust and the promise format.
**Builds on:** PROJECT_SPEC §2.17a–b, §2.17d, §3.3p, §4.6b; [core README](../../personality/README.md); [personality plan](../in_progress/PERSONALITY_PLAN.md); [generated effects design](../in_progress/PERSONALITY_GENERATED_EFFECTS_DESIGN.md); [cap-relative plan](../done/CAP_RELATIVE_CONTRACTS_PLAN.md) (built and on main 2026-11-02).

**Goal:** Let persistent, permitted personality affect voluntary contract decisions through bounded, explainable feature policy. Preserve existing pricing and legal authority. Support Mu's 2026–2033 CPU league and JR's 2034 takeover. Every player's contract preferences come from his stored generated profile from the first offseason on, and returning people are never redrawn.

**Delivers:** a pure contract policy, explicit core/earnings/relationship adapters, common scoring through counters and markets, CPU voluntary-deal integration, immutable decision receipts, factual event outputs, signed UI explanations and an offline comparison report.

All new symbols below are **proposed**, unless §0 identifies them as existing. Numerical settings are offline trials, not calibrated facts. Unapproved concurrent interfaces remain dependencies to reconcile. The parent coordinates shared spec references. The `/propose` template and completed draft-round-trades plan supply the house style.

## 0. Survey (code facts that set the scope)

Inspected canonical `/home/jrereyi/Project-Mu` on 2026-10-31. Read root AGENTS.md through WSL (Windows Get-Content could not follow that entry); its instructions match CLAUDE.md. Read relevant spec sections, core README/core/store/migration, personality/design proposals, cap-relative proposal (since built) and completed draft-round-trades plan. No database access. Unrelated progression, dossier, PRE_PC_WORK_2, images, scripts and other uncommitted work remain untouched.

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `engines/free_agency_engine.py:188` accepts and ignores persona; `:202` returns persona zero. Components come from financial_score (`:154`), contention_score (`:167`) and role_score (`:178`). | Feature owns one adjustment to existing utility; core owns no contract formula. |
| S2 | `free_agency_engine.py:205` computes bar; `:210` searches counter raises in 5% steps through +35%; `:225` rescores without persona. `:230` also computes a missing score without persona. | Thread the same frozen view/context into every candidate. Top-level-only scoring would make counters inconsistent. |
| S3 | `free_agency_engine.py:255` adds choice noise keyed partly by offer index, then chooses first ACCEPT, otherwise first COUNTER. `:277` adds mood/cooling; `:327` serves street and UDFA. | Cover all paths, preserve old neutral draws/order, and record noise separately. Offer permutation invariance is not current behavior. |
| S4 | `free_agency_engine.py:238` rejects an 80+ backup below 1.5 W; `:100` floors W at minimum salary. | Personality cannot bypass hard role rejection, minimums, legal windows, roster/cap limits or team affordability. Utility need not be bounded by 1. |
| S5 | `transactions_engine.py:298` plans re-signs; `:342` contains independent test-market randomness; `:380` proposes extensions on team surplus. Neither invokes player utility. `:426` builds FA offers, `:497` market needs, `:509` signings from decisions. | CPU voluntary deals require explicit integration; team value/pricing remain separate. |
| S6 | `server/franchise_api.py:945` builds score context; `:964–:968` scores/accepts. `:1176` handles FA offers, `:1267` contract proposals, `:443` inserts offers. | One authoritative adapter must serve both UI routes, with receipts in the offer transaction. |
| S7 | `Mu_Network/src/types/franchiseMarket.ts:43` describes zero persona. `components/franchise/FreeAgencyModule.tsx:126` renders weighted parts; `:136` draws only positive persona; `:8` documents reuse by Contract Room. | Negative adjustments need signed explanation and correct rendering in both reused result paths. |
| S8 | `personality/core.py:144` feature_view filters real fields by exact permission and makes missing profiles neutral. `store.py:35` reads; `:136` resolves aliases; `:85` explicitly initializes. | Consume feature_view(profile, 'contracts'); never call ensure or newsroom.personas.get_persona on a feature read. Corruption/schema errors propagate. Every player is `generated` origin (the lifecycle plan initializes the 2026 roster and each class), so the view is his full stored profile; the core's `real`-origin evidence filter never applies to players. |
| S9 | `core.py:166` validates explicit changes; `store.py:145` persists them. `2027_01_16_personality_core.sql:8,21,30` defines profiles, aliases and audit events. | Consume identity/revision; do not duplicate evolution or trust math. Core exists but live ingestion is not connected and production profiles/migrations were not applied. |
| S10 | `2027_01_09_personas.sql:21` contains chk_persona_sliders_budget requiring exactly 30; `core.py:136` records current bounds. | Superseded 2026-11-02: the cap-relative plan built the 24–36 band in Python and SQL (`2027_01_17_persona_slider_band.sql`, applied to `madden_test`); `core.py` picks up the new bounds. Single-slider evolution works where the migration is applied; this feature changes neither. |
| S11 | Built 2026-11-02: `engines/career_earnings.py` provides `earned_shares` and the three-state `is_secure` (True / False / None at 0.15 of a cap; PROJECT_SPEC §2.17e). Generated design §2/refinement describes trial capped effects and earned attachment. | Reuse those earnings semantics; no parallel earnings threshold/ledger. Build against the implemented API. |
| S12 | Existing tools/personality_contract_demo.py and its test are untracked illustrative artifacts; the design's demo section excludes production counters/markets. | Demo is not calibration, activation or full-path verification. Keep its files under their existing owner's control. |
| S13 | PROJECT_SPEC §2.17b still documents persona zero; §3.3p includes older rollout descriptions alongside newer core ownership documented in personality/README.md. | Parent consolidates §7 changes. Historical later takeover dates do not replace the requested 2026–2034 timeline. |

**Corrected 2026-11-03 ([activation plan](../not_started/PERSONALITY_ACTIVATION_PLAN.md) S2):** the league office does run these engines through its `Deps` class (`pipeline/league_office.py:890` `resign_decisions`, `:899` `run_wave`, `:909` `udfa_market`; `:1549` `step_free_agency`). The activation plan's `PersonalityDeps` passes this plan's `contract_inputs` through them. This plan's units add the keyword and stay neutral by default.

## 1. Scope

**In:** money/contention preferences; financial-security/career/role gates; earned attachment/home/grudge context; bounded aggregate effects; monotonicity and threshold/ranking behavior; counters and every available simulation/UI path; separate market noise; receipts/explanations; neutral/shadow controls and activation gates.

**Out:** cap-relative pricing, earnings reconstruction/security threshold and slider band (cap-relative owner); core identity initialization/adoption and ingestion wiring; relationship history-to-tier scoring, trust/grudge creation/repair; baseline contention redesign; lowball rules, leaks, new promises UI, dialogue; development, trade requests, retirement; live/save/archive access or automatic deployment.

Money preference is not work ethic. Quiet is not low confidence. Hometown is not loyalty. No warmth/sociability/confidence/drive contract multiplier. LEGACY_BUILDER/SPOTLIGHT_SEEKER remain numeric-neutral until separately justified inputs exist.

## 2. Design

### 2.1 Exact policy and adapter interfaces

New `engines/personality_contract_policy.py`:

```python
def validate_contract_policy(config: dict) -> None: ...
def validate_contract_context(context: dict) -> None: ...
def contract_personality_delta(parts: dict, view: dict, context: dict, config: dict) -> dict: ...
```

Parts are unrounded financial/opportunity/contention/scheme floats from the existing scorer. View is exactly core's contracts feature view (for a player, his full generated profile, including any revision JR has edited). Result: `{terms:{ambition,attachment,home,grudge}, raw_delta, capped_delta, cap_applied, gates:dict, reasons:list[str], policy_version}`. Grudge term is signed negative. Reject NaN/infinity and invalid configuration.

Config fields: `version:str`, `mode:'off'|'shadow'|'active'`, `ambition_enabled:bool`, `relationship_enabled:bool`, `coefficient:float`, `aggregate_cap:float`, `contender_floor:float`, `attachment_bonus:{none,weak,established,strong:float}`, `home_bonus:float`, `grudge_penalty:float`. Numbers are finite/nonnegative; coefficient strictly below min(W_DEFAULT.money,W_DEFAULT.team). Config is server-owned; a client cannot enable it.

Context schema v1 (Python int team keys; JSON decimal-string keys normalized once):

```text
universe_id:str; player_id:int; persona_id:str|null; revision:int|null
as_of:{season:int,week:int,stage:str}; batch_id:str; context_version:str
career:{established:bool|null,source_ids:list[str],version:str}
security:{secure:bool|null,earned_share:float|null,complete:bool|null,
          through_season:int,source_ids:list[str],version:str}
current_team_id:int|null
teams:{team_id:{attachment:'none'|'weak'|'established'|'strong'|'unknown',
       role_satisfactory:bool|null,treatment_satisfactory:bool|null,
       unresolved_breach:bool|null,grudge:bool|null,source_event_ids:list[str],
       relationship_version:str,attachment_as_of:str,
       meaningful_home_match:bool|null,home_source_ids:list[str]}}
```

Missing data becomes explicitly unknown. Reject identity/revision mismatch, future source dates, invalid tiers and cross-universe links. Missing alias/profile means neutral with reason. Storage/corruption failures stop scoring; they are not missing data.

New `engines/personality_contract_context.py`:

```python
def load_contract_context(cur, *, universe_id, player_id, alias_namespace,
                          external_id, as_of, team_ids, batch_id, dependencies) -> tuple[dict, dict]: ...
def evaluate_voluntary_contract(league, ctx, player, contract, *, contract_inputs, seed) -> dict: ...
```

First returns `(view, context)` through resolve_alias/read/feature_view. No initialization. Alias namespace is verified by ingestion; never assume roster ID equals pgid. Dependencies supply read-only `career(player_id,as_of)`, `security(player_id,as_of)`, `relationships(persona_id,team_ids,as_of)` adapters producing the above shapes. The relationships adapter reads the player's own one-way view of each team from the relationships plan's `relationship_context`, which owns trust, attachment and breaches; this plan never reads the team's view of the player as his. These are requested contracts, not claims those providers exist. Earnings/relationship owners must reconcile them. Cache only within frozen batch/as-of/revision.

Career trial recommendation: four completed accrued seasons from verified history (Q2), independent of age/OVR. Security exclusively consumes `career_earnings.is_secure` (built); unknown is inactive. Exclude proposed deal/future promised salary from already-earned security. No duplicate earnings cutoff here.

### 2.2 Formula, bounded effects and monotonicity

Offline trial settings: k=.10, aggregate cap=.08 utility; attachment none/weak/established/strong = 0/.015/.035/.055; home=.01; grudge=.03; contender floor=.70. These reproduce a review mechanism, not calibrated production constants.

- Permitted MONEY_FIRST: ambition A=k*(F-C).
- Permitted RING_CHASER: A=k*(C-F) only with established career=True, security=True, certified contender destination and usable projected depth 1/2 without hard role block. Unknown gate means zero. Keep latent aspiration for career-building players without activating strong preference. An elite insecure young player fails; a secure established role player can qualify.
- LOYAL attachment L: current team only; owner-supplied earned tier; role and treatment satisfactory; explicitly no unresolved qualifying breach. All gates must be known. Tenure/meaningful relationships/role/treatment support the owner's tier, never friend counts or independent per-friend bonuses.
- HOMETOWN_GUY H: verified meaningful home-region match. No name/school inference or drafted-team proxy. Home never grants trust.
- GRUDGE_HOLDER G: negative only for owner-certified unresolved qualifying event toward this destination. A loss or rejected offer alone is insufficient.

`raw=A+L+H-G`; `delta=clamp(raw,-cap,+cap)`; `U=base_U+delta`. Exactly one aggregate clamp and one ambition path. Record raw terms and capped result. Do not clamp U to 1. A .08 cap permits .16 pairwise gap change; it is not an 8% salary discount.

With fixed eligibility, effective weights remain positive: Money First (.45+k financial,.20-k contention); eligible Ring Chaser (.45-k,.20+k). Saturation preserves weak monotonicity. **The hard contender gate can create a downward jump** when it activates while C-F is negative. Do not claim global monotonicity from the demo. Prebuild spike must compare a continuous monotone gate against frozen independent contender certification. Recommendation: freeze certification from a team snapshot for a decision batch, require money monotonicity and fixed-certification contention monotonicity, and report cross-certification discontinuities for JR's review. No activation until accepted or redesigned. Changing only offer money must never change security/attachment eligibility.

### 2.3 Shared scoring and counter contract

Extend `offer_utility(..., persona=None, *, contract_context=None, policy=None)` compatibly. Legacy persona-only callers remain zero-effect. New persona is filtered view. Keep existing keys; add `base_utility` and `policy_explanation`. Active utility is round(unrounded base+delta,4); off executes original arithmetic/rounding exactly. Shadow returns baseline utility/decision and computes adjusted alternatives on copied inputs.

Extend `acceptance(best,player,in_season, *, contract_context=None,policy=None)` and `_counter(..., *, persona=None,contract_context=None,policy=None)`. Freeze the same context for all candidate rescoring. Validate supplied best.score against input/context/config fingerprints; stale cached score cannot authorize acceptance. Store every counter attempt, score and first clearing candidate. Existing term/guarantee/raise search remains; counters still require owner's cap/legal validation before execution. Keep hard backup rejection before acceptance.

Add keyword-only `contract_inputs=None` to run_wave/street_market/udfa_market and private _market/_decide: player ID maps to `{view,context,policy}`. Defaults retain current behavior. Pass inputs through rank/accept/counter paths. Record each candidate, not winner only.

Keep existing ±.03 choice noise, mood noise, cooling and seed construction for first increment. Record raw utility, adjusted utility, choice draw, noisy ranking score, rank, bar base/mood/cooling, hard constraints and outcome. Ranking uses noisy adjusted utility; threshold uses unnoised adjusted utility against shifted bar. No persona-specific noise. Preserve existing index-seeded order sensitivity, canonicalize batch offer order for retries, and test it explicitly. Any permutation-invariant seed redesign is a separate reviewed change.

### 2.4 CPU re-sign/extension paths and instruments

Off/shadow must preserve plan_resign exactly, including test-market draws and moves. In active mode, evaluate existing unrestricted re-sign offer through common utility after team value/cap eligibility. ACCEPT permits current CONTRACT. COUNTER is accepted only after existing team surplus/room/roster/legal checks; otherwise LET_GO with reason. REJECT can feed the existing legal tag alternative. Recommend replacing the independent unrestricted test-market lottery with this explicit decision only when active (Q3); layering both would create a second refusal rule.

Extensions use proposed `extension_offer_terms(player,contract)->dict` in transactions_engine: isolate incremental offered years and avoid counting existing owed salary as new benefit. Player rejection leaves original contract intact; accepted counter requires faithful reconstruction of guarantees/old years and extension legality. Representation is a prebuild spike. If unavailable, block extension activation and therefore full CPU/UI rollout rather than silently approximate.

Rookie-scale contracts, ERFA/RFA tenders, franchise/transition tags and fifth-year options remain legal/team instruments with no invented personality veto. Record instrument versus voluntary consent distinctly. UDFA/street voluntary choices use common policy; rookies normally fail ring gate. A tag never erases the preceding rejection or implies consent.

### 2.5 Persistence, API, UI and event boundary

Server _score loads authoritative context once for both FA and RESIGN/EXTENSION routes. Negotiation JSON adds optional policy_explanation; existing fields remain. Server recalculates submit; changed preview revision/config/context yields 409 plus refreshed result. Client score/config is never authoritative. Add `GET /api/franchise/offers/{offer_id}/policy`, authorized through existing franchise/offer ownership, returning immutable receipt. No public private-motivation endpoint. Audit includes only feature-permitted view, not unrelated evidence.

FreeAgencyModule's reused UtilityBar/NegotiationResult shows base contributions, signed adjustment, final utility and bar. Negative delta cannot disappear or become negative CSS width. Labels can exceed 1; any chart clipping is presentation only. ContractRoom consumes the reused component without a second implementation. Missing/review-disabled context shows neutral reason, not invented badges.

Migration `db/migrations/2027_01_23_contract_personality_policy.sql` (reserved in PERSONALITY_PLAN §5.3; recheck at build):

| Table | Exact columns and keys |
|---|---|
| contract_policy_decisions | universe_id TEXT, decision_id TEXT, season INTEGER, week INTEGER, stage TEXT, player_id BIGINT, persona_id TEXT NULL, revision INTEGER NULL, mode TEXT CHECK off/shadow/active, policy_version TEXT, input_hash TEXT, snapshot JSONB, result JSONB, created_at TIMESTAMPTZ DEFAULT now(); PK(universe_id,decision_id). All other fields NOT NULL. |
| contract_policy_event_outbox | universe_id TEXT, event_id TEXT, decision_id TEXT, kind TEXT, payload JSONB NOT NULL, delivered_at TIMESTAMPTZ NULL; required IDs/kind NOT NULL; PK(universe_id,event_id), composite FK to receipt. |
| ~~contract_policy_activation~~ | Not created (activation plan D1, 2026-11-03): the mode, version, config and review note come from the shared switchboard, `personality.features.config(cur, league_id, 'contracts_ambition' / 'contracts_relationships')`. `read_contract_activation` reads that instead. |

Snapshot includes permitted view/revision/catalogue version, source/provider versions/as-of, canonical offer order, market fit/basis/cap, all components/gates/draws/bars/counters, and baseline/adjusted outcomes. Result links existing offer/execution IDs. No FK to mutable profile revision; retirement/archive cannot break historical replay.

New `engines/personality_contract_store.py`: `record_contract_decision(cur,receipt)->dict`, `read_contract_decision(cur,universe_id,decision_id)->dict|None`, `enqueue_contract_events(cur,receipt,facts)->list[str]`, `read_contract_activation(cur,universe_id)->dict`. Caller owns transaction; no commits. Stable IDs identify actual negotiation attempts/batches, not player-season. Same ID/hash returns original; changed hash conflicts. Handle unique-conflict races by reread/hash verification. Receipt, offer status and outbox writes are atomic; previews/dry runs calculate in memory only.

Emit factual events at their actual lifecycle: CONTRACT_OFFER_SUBMITTED, CONTRACT_RESPONSE_RECORDED, CONTRACT_EXECUTED, CONTRACT_INSTRUMENT_APPLIED. Payload v1: `{event_id,source_decision_id,universe_id,player_persona_id,team_id,gm_persona_id|null,as_of,offer_id|null,terms,market_reference,response,execution_id|null}`. Consumers dedupe event_id. Submitted/accepted UI offers are not executed contracts. No preview event. Do not emit LOWBALL, trust delta, promise breach or automatic grudge: relationship owner classifies facts under its own approved policy. Projected role is not a promise; a commitment counts only as a relationships-plan `promise_made` with its `promise_resolved` (for example from a V2.9 inbox role talk).

### 2.6 Migration, neutral controls, activation and rollback

1. Implement pure/report path with off default. Shadow executes original decisions; counterfactual counters never reserve cap, change offers or sign players.
2. Reconcile dependencies and aliases. Unknown security disables strong ring preference; absent relationships disable that family. Ambiguous identity blocks that person's activation; schema errors stop active batch. Profile availability never enables a feature.
3. Additive scratch migration first; apply twice and check actual schema compatibility rather than relying only on IF NOT EXISTS. No backfill, seeds or import-time migration. Production migration requires separate authorization.
4. Gate: exact old arithmetic/results/seeds off and neutral, shadow baseline equality, counter consistency, legal controls, receipts/replay and every available caller. Future league-office execution seam must meet the same contract before production activation.
5. Paired seeded synthetic cohorts through 2034: report signed delta/cap saturation, threshold/ranking flips, counter changes, salary/cap-share distributions, team/player concentration, missing/profile/security/context rates and family activation. No invented acceptable percentages; JR reviews distributions and sets tolerances/config after evidence.
6. Possible first activation is ambition-only for verified player identities (the whole 2026 roster and every class, all generated) under explicit universe/version record. Relationships remain independently disabled until reviewed. CPU voluntary/UI routes activate together after parity; absent runner/identity integration blocks production readiness.
7. Rollback sets mode off for new decisions. Never overwrite historical receipts or reverse signed contracts by rescoring. Cancel unexecuted batches via existing owner and use new batch IDs. Preserve tables/outbox audit. Actual execution rollback requires paired save/DB checkpoint restoration; disabling effects cannot undo signings. Dropping audit data is separately authorized destructive work.

## 3. Risks and unknowns

| # | Risk | How we find out | Fallback |
|---|---|---|---|
| R1 | Ring eligibility discontinuity and popularity inside existing contention. | Sweep below/at/above .70 with F above/below C; compare certification/continuous gate. | JR revises gate before build; no silent baseline contention rewrite. |
| R2 | Proposed earnings uses reconstructed APY rather than verified cash, incomplete history. | Review dependency coverage/as-of/double-counting tests. | Unknown/inadequate evidence disables strong effect; no local approximation. |
| R3 | Relationship tier/breach/home outputs are approved in the relationships plan but not built, and its first increment is evidence-only (its Q1). | Reconcile source-backed outputs and event deduplication with owner. | Disable family; no interim trust math. |
| R4 | CPU extension representation and test-market lottery diverge from UI. | Pure extension/counter seam spike and baseline traces. | Block active rollout until faithful representation and Q3 review. |
| R5 | Noise overwhelms small deltas; rounding flips boundaries. | Paired draws, raw/final receipt traces and boundary tests. | Review settings separately; no anecdotal demo calibration. |
| R6 | Other plans edit the same files: `SALARY_CAP_GROWTH_PLAN` (transactions, franchise API) and the approved V2.9 inbox, press conference, scouting and trade-talks plans (`server/franchise_api.py`, `tests/test_franchise_api.py`). | Ownership review before build/merge. | Those land first; re-read the files and merge after them; no simultaneous ownership. |
| R7 | Execution runner/IDs unavailable. | Source seam inventory before build and wiring. | External activation gate; no invented save writer or readiness claim. |

**Spike first:** implementation orchestrator, estimated half-day, source/offline only: monotonicity gate alternatives; incremental extension/counter representation; current runner/caller/migration inventory; earnings/relationship interface and cross-plan ownership review. Any formula/CPU semantics change returns to JR before wave 1. No database calibration or migration spike.

## 4. Tests (synthetic, no live DB)

- `tests/test_personality_contract_policy.py`: bounded terms/stacking/pairwise bound, fixed-gate monotonicity and cross-gate discontinuity; finite config checks; missing/neutral views; full generated profiles for existing players and prospects alike, including a JR-edited revision; secure veteran role player versus elite insecure youngster; attachment/home separation and breach/unknown gates; no unsupported trait/slider effects or final U clamp.
- `tests/test_contract_policy_context.py`: fake providers/cursor; aliases/as-of/revision/universe; no get_persona/ensure; storage failure propagation; frozen security independent of offers; faithful incremental extension representation.
- `tests/test_contract_policy_store.py`: opt-in explicit madden_test fresh-schema transactions only; migration twice and incompatible schema; replay/conflict/race; rollback between offer/receipt/outbox; no commits; durable snapshot after profile change/archive. Missing/unsafe DSN refuses integration tests. No default live/archive connection.
- Existing `tests/test_free_agency_engine.py`: exact off/shadow baseline; scoring/ranking/bar/counter context; stale score; repeated identical seed/order and documented permutation sensitivity; hard backup rejection and street/UDFA.
- Existing `tests/test_transactions_engine.py`: neutral CPU traces; voluntary accept/counter/reject, room and team value; statutory exclusions; original extension/guarantee preservation; rejection/tag distinction.
- Existing `tests/test_franchise_api.py`: both submit routes, authoritative recompute/stale preview, authorization, atomic receipt, no preview events/client policy override, accepted proposal distinct from execution.
- `tests/test_contract_policy_report.py`: deterministic fictional multi-cohort JSON/report; paired seeds; no DB imports/connections.
- UI build and synthetic visual checks: negative/zero/capped/>1 utility, threshold label, narrow layout, accessible signed text and Contract Room reuse. No production backend required.

Focused verification after build: `python3 -m pytest -m "not integration" tests/test_personality_contract_policy.py tests/test_contract_policy_context.py tests/test_free_agency_engine.py tests/test_transactions_engine.py tests/test_franchise_api.py tests/test_contract_policy_report.py -q`. Store tests run separately with guarded explicit scratch DSN and rollback. Never run napper wholesale. No implementation tests were run or claimed during planning.

Proposed CLI: `python3 -m tools.personality_contract_policy_report --input tests/fixtures/contract_policy/cohorts.json --out verify_output/contract_policy --mode compare`. Mode neutral/shadow/compare only; no activation. Explicit fictional personas/histories/teams/terms/seeds input; report.json/report.md output, no automatic DB discovery. Preserve existing demo files unchanged.

## 5. Work breakdown

Shared files, parent/orchestrator only: PROJECT_SPEC.md, this plan, CLAUDE.md. Exclusive ownership below is within this plan; reconcile cross-plan shared files first.

### U1: Pure policy and offline evidence

- **Owns:** `engines/personality_contract_policy.py`, `tests/test_personality_contract_policy.py`, `tools/personality_contract_policy_report.py`, `tests/test_contract_policy_report.py`, `tests/fixtures/contract_policy/cohorts.json` (all new).
- **Reads:** core API/README, existing components, generated design and demo read-only.
- **Depends on:** approved spike/§2 contract; no merged unit.
- **Done when:** bounded policy and reproducible report expose gates/discontinuities and paired outcomes without calibration claims.
- **Verify:** `python3 -m pytest tests/test_personality_contract_policy.py tests/test_contract_policy_report.py -q`; report CLI above. Tests pass; repeated report identical.

### U2: Immutable store and additive schema

- **Owns:** `engines/personality_contract_store.py`, `db/migrations/2027_01_23_contract_personality_policy.sql`, `tests/test_contract_policy_store.py` (new; reserve alternate suffix as one owned path if occupied).
- **Reads:** core transaction style, current offer schema, §2.5 envelope contract.
- **Depends on:** fixed §2 shapes; no U1 import required for envelope/hash storage.
- **Done when:** replay/conflict/atomic rollback and default-off migration pass scratch checks.
- **Verify:** explicit guarded CONTRACT_POLICY_TEST_DSN to madden_test, `python3 -m pytest tests/test_contract_policy_store.py -q`; all changes roll back.

### U3: Context, common scoring and CPU wiring

- **Owns:** `engines/personality_contract_context.py`, `tests/test_contract_policy_context.py` (new); `engines/free_agency_engine.py`, `tests/test_free_agency_engine.py`, `engines/transactions_engine.py`, `tests/test_transactions_engine.py`.
- **Reads:** U1/U2, approved dependency providers, contract legality and transaction code.
- **Depends on:** U1, U2; the cap-relative work is already merged (2026-11-02); reconciled dependency contracts. Missing relationship provider stays explicitly disabled.
- **Done when:** all available pure paths/counters/CPU voluntary deals share context, neutral controls pass, future runner gate remains explicit.
- **Verify:** `python3 -m pytest tests/test_contract_policy_context.py tests/test_free_agency_engine.py tests/test_transactions_engine.py -q`; unchanged neutral and passing active/legal fixtures.

### U4: HTTP and reused UI result

- **Owns:** `server/franchise_api.py`, `tests/test_franchise_api.py`, `Mu_Network/src/types/franchiseMarket.ts`, `Mu_Network/src/components/franchise/FreeAgencyModule.tsx`.
- **Reads:** U3 context, U2 persistence, ContractRoomModule.tsx read-only reuse.
- **Depends on:** U3.
- **Done when:** both routes use server-owned context/receipts, authorized historical read/stale handling work, signed deltas render in reused result.
- **Verify:** `python3 -m pytest tests/test_franchise_api.py -q`; `npm run build` in Mu_Network and §4 synthetic UI checks. No legacy response break.

## 6. Waves

| Wave | Units | Needs merged first | Reason |
|---|---|---|---|
| 0 | Spikes and design reconciliation | JR approval (done 2026-11-03) | Resolve formula/CPU semantics and cross-plan seams. |
| 1 | U1, U2 | Fixed §2 contracts | Disjoint pure/report and persistence files. |
| 2 | U3 | U1, U2 (cap-relative already on main) | One owner for shared engine/transactions edits. |
| 3 | U4 | U3 | Settled scoring/receipts feed HTTP/UI. |
| Gate | Parent neutral/shadow review | All units plus identity/runner dependencies | No automatic production migration, seed or active config. |

**File ownership check (sorted):**

| File | Unit |
|---|---|
| Mu_Network/src/components/franchise/FreeAgencyModule.tsx | U4 |
| Mu_Network/src/types/franchiseMarket.ts | U4 |
| db/migrations/2027_01_23_contract_personality_policy.sql | U2 |
| engines/free_agency_engine.py | U3 |
| engines/personality_contract_context.py | U3 |
| engines/personality_contract_policy.py | U1 |
| engines/personality_contract_store.py | U2 |
| engines/transactions_engine.py | U3 |
| server/franchise_api.py | U4 |
| tests/fixtures/contract_policy/cohorts.json | U1 |
| tests/test_contract_policy_context.py | U3 |
| tests/test_contract_policy_report.py | U1 |
| tests/test_contract_policy_store.py | U2 |
| tests/test_franchise_api.py | U4 |
| tests/test_free_agency_engine.py | U3 |
| tests/test_personality_contract_policy.py | U1 |
| tests/test_transactions_engine.py | U3 |
| tools/personality_contract_policy_report.py | U1 |

No repeated owned path. Core, cap/earnings modules, legacy slider validator/SQL, demo, progression and other plans are unowned here. A discovered execution-runner file requires approved ownership amendment before implementation; no unowned edits are implied.

## 7. Spec changes when built

Parent coordinates these; this task does not edit shared spec/plans.

- §2.17b: scoped delta, gates, aggregate bound, common counters, ranking/noise versus bar, trial versus approved config.
- §2.17a/§2.17d: CPU voluntary acceptance/incremental extension terms, unchanged statutory/legal authority, cap-relative cross-reference.
- §3.3d/§3.3p: receipt/outbox/activation tables, core identity/provenance boundary, historical snapshots, no implicit seeding/evolution; correct stale rollout descriptions without claiming production application.
- §4.6b/§5.2: optional explanation, authorized receipt endpoint, stale-preview handling and signed reused result UI.
- §6/running instructions: off/shadow/active, unavailable execution dependency, synthetic CLI, migration/rollback, 2026–2033 CPU and 2034 JR timeline.
- §7.11: profile/catalogue availability is not activation; players read as generated profiles; relationship event ownership; no money-to-work-ethic inference.
- Repository Layout: new modules/tool/migration/tests/fixture listed in §6; parent coordinates plan index/status references.

## 8. Decisions for JR

JR approved all four recommendations on 2026-11-03.

| # | Question | Decision (JR, 2026-11-03) |
|---|---|---|
| Q1 | Affect ranking and acceptance thresholds, or ranking only? | Both with unchanged base bars/hard rules, so counters reflect willingness. k=.10/cap=.08 stays offline trial pending cohort review. |
| Q2 | Career and contender eligibility, including the hard .70 discontinuity? | Trial four completed accrued seasons plus approved earned security=True and usable role; frozen independent contender certification per batch. Review gate spike before fixing production semantics; no OVR shortcut. |
| Q3 | Replace active CPU unrestricted test-market lottery with shared player acceptance/counters? | Yes in active mode; exact baseline lottery in off/shadow, statutory instruments preserved. Activate CPU/UI together after parity. |
| Q4 | Must relationship effects activate with ambition? | Build adapters now, ambition-first activation after evidence review; attachment/home/grudge stay disabled until owner supplies reviewed source-backed outputs. No local trust substitute. |

Plan approval is not migration, seed or activation approval; those stay separate JR decisions (§2.6).

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | build/cpol-u1 | .claude/worktrees/cpol-u1 | merged | 6af5560 |
| U2 | 1 | build/cpol-u2 | .claude/worktrees/cpol-u2 | merged | 0f39b1a |
| U3 | 2 | build/cpol-u3 | .claude/worktrees/cpol-u3 | merged | 44b92e5 |
