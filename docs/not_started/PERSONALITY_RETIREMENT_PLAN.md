# Project Mu: Retirement Decisions Plan

**Status:** approved by JR (2026-11-03), Q1–Q4 settled as recommended (§8); not built. Approval covers building the units; save experiments, migration and each activation still need JR's separate go-ahead (§2.6 gates G4–G6).
**JR decisions this plan follows (2026-11-03, PERSONALITY_PLAN §5.1, D8–D13):** every player has a generated profile (the 2026 roster at the league start, prospects at class entry), editable by JR; players named after real people are fictional characters inspired by them. Earned attachment comes from the relationships plan, which owns one-way trust.
**Builds on:** [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) §5.3 (parent tracker), §6; PROJECT_SPEC §2.17b–d, §3.3m, §3.3q, §6.0b; [V2 core](../done/V2_CORE_BUILD_PLAN.md) U13; [core API](../../personality/README.md); [effect design](../in_progress/PERSONALITY_GENERATED_EFFECTS_DESIGN.md). The separately owned [cap-relative plan](../done/CAP_RELATIVE_CONTRACTS_PLAN.md) (built and on main 2026-11-02) supplies earnings inputs: `engines/career_earnings.py` (`earned_shares`, three-state `is_secure`, PROJECT_SPEC §2.17e).

**Goal:** Give Mu a reproducible, explainable annual retirement policy, starting with a neutral baseline. Keep willingness to play, contract acceptance and retirement execution consistent. Prove save authority before replacing Madden's choices.

**Delivers:** proposed neutral policy, career state machine, audit sidecars, offline preview and default-deny edit planner. All functions and files in §2 are proposals, not claims of existing behavior. Production wiring requires a separately reviewed U13 integration.

Mu starts in 2026, controls CPU teams through 2033, and JR takes over in 2034. Drafting involved no database access, executable launch, migrations, seeding, save writes or coefficients; consolidated spec edits are deferred to §7.

## 0. Survey (code facts that set the scope)

Source inspection on 2026-10-31. Revalidate before building because concurrent changes are present.

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `engines/season_ledger.py:67–91` classifies an absent roster player as FREE_AGENT without a recorded retirement; `:152–154` reads `player_retirements` and treats a nonempty set as scan evidence. | Disappearance is not retirement. A completed empty scan needs explicit completeness evidence, not `bool(retired)`. |
| S2 | `db/migrations/2027_01_03_player_season_ledger.sql:18–35` defines `players.retired_season` and captured `player_retirements`. | Preserve observed history separately from policy intent and verified execution. |
| S3 | `db/migrations/2027_01_14_v2_franchise_os.sql:100–107` defines `retirement_decisions(season, player_id, madden_retired, mu_keep, reason)`. `PROJECT_SPEC.md:1215–1221` says its writer awaits league office. No retirement writer was found in engines/pipeline. **Corrected 2026-11-03 ([activation plan](PERSONALITY_ACTIVATION_PLAN.md) S3):** the league office already writes it: `pipeline/league_office.py:343` `keep_retired` and `:1398` `step_retirements` record each Madden retirement and un-retire by a seeded age/OVR rule, and `:1416` signs a kept retiree back, all through the verified save write. | Existing schema is an override projection, not a decision engine, replay log or execution receipt. Restoring a native retiree already has a writer; when active, this plan's decision replaces `keep_retired`'s through `deps.retirement_decision`. Retiring an active player still has none. |
| S4 | `pipeline/save_reader/pc_save_reader.mjs:253–261` skips Deleted rows and exports ContractStatus for other named players; `:85` exports offseason stage. | Capture before deletion. Normal snapshots do not promise recovery of deleted people. |
| S5 | `docs/done/V2_CORE_BUILD_PLAN.md:20` reports 59 Retired from the first offseason save, 60 by recap, then Deleted at preseason. `docs/reference/MADDEN_OFFSEASON_WALKTHROUGH.md:35` reports the purge. | These notes establish a pre-rollover cutoff, not that restoring status preserves contracts/references or survives the next advance. |
| S6 | `pipeline/save_reader/save_writer.mjs:25–27,55–75` rejects overlapping release/sign/trade edits and certain field/record conflicts; `:184–185` verifies writes. | Retirement needs the same conflict boundary. Release means free agency, not retirement. |
| S7 | `engines/free_agency_engine.py:188,230,255,277` owns utility, acceptance, choices and waves; `engines/transactions_engine.py:509,856–865` converts decisions and produces re-sign/CPU offers. | Every signing execution path must share career-state arbitration; no second acceptance formula here. |
| S8 | `pipeline/load_transactions.py:57–81` skips non-moves; source search found no retirement branch in `pipeline/transactions_parse.py`. | A RETIRED enum does not establish a working capture parser. Reconcile capture ownership before activation. |
| S9 | `pipeline/watch_save.py:224,403,454` separates week classification, ingest and child execution; spec §6.0b documents offseason no-op and weekly locking. | U13 owns offseason scheduling; no second writer or annual reroll in ordinary ingest. |
| S10 | `personality/core.py:120–144` initializes/version-controls profiles and exposes feature views; `personality/store.py:35–61,136–142` separates absence from failure and resolves aliases. | Consume `feature_view(profile, 'retirement')`; never use `newsroom.personas.get_persona` fallback. |
| S11 | `db/migrations/2027_01_09_personas.sql:21` contains exact-30 `chk_persona_sliders_budget`. | Superseded 2026-11-02: the cap-relative plan built the 24–36 band in Python and SQL (`2027_01_17_persona_slider_band.sql`, applied to `madden_test`), so single-slider evolution works where that migration is applied. This plan owns neither. |
| S12 | `engines/tuning/progression_fit.py:28–33,103,472–473` uses forced ages and mixed roster exits in an offline demonstration. | Do not adopt those shortcuts as a calibrated retirement model. Cuts, unsigned players and retirement differ. |
| S13 | `docs/MOD_INSTALL.md:24` lists Contract Retirement Arbitrator. Local package contains `snapshot_franchise.exe` and `restore_retired_players.exe`. | Existence is not proof of compatibility or authority. Inventory exclusive ownership; do not launch binaries for planning. |
| S14 | `tests/conftest.py:1–13` documents DB isolation; relevant suites include season ledger, transactions, save loader and watcher. | Focused synthetic tests; isolated migration tests only. Never run napper tests wholesale. |

Existing behavior: native Madden retirements, captured ledger consumers and an override table. Proposal: neutral career choice, immutable decisions, then guarded execution after proof. V2's intended ownership is not evidence of current write authority.

## 1. Scope

**In:** neutral age/position/ability/health/opportunity baseline; optional bounded goals/personality/attachment; explicit unknown earnings; seeded annual replay; shared signing/retirement state; audit, previews and save-authority investigation.

**Out:** contract prices/utility, earnings reconstruction/security thresholds and slider bands (cap-relative owner); profile initialization/evolution (core); relationship production; medical/progression formulas; live activation, UI/API work, archival rewriting, automatic comebacks and forced roster-population quotas. No private motives inferred from quietness, money preference, OVR or labels.

## 2. Design

### 2.1 Exact pure interfaces and data

Proposed `engines/retirement_policy.py` exports frozen `RetirementContext`, `RetirementPolicy`, `ProbabilityBreakdown` and `RetirementDecision` dataclasses:

```python
RetirementContext(universe_id: str, person_key: str, player_id: int,
                  pgid: int, earned_season: int, decision_season: int,
                  age: int, position: str, ability: dict, health: dict,
                  opportunity: dict, career: dict, earnings: dict,
                  goals: tuple[dict, ...], attachment: dict,
                  feature_view: dict | None, evidence: dict)
baseline_probability(ctx, policy) -> ProbabilityBreakdown
personality_adjustment(ctx, base, policy) -> dict
annual_draw(universe_id, person_key, earned_season, seed_version) -> float
evaluate_annual(ctx, policy, *, features_enabled=False) -> RetirementDecision
```

`ProbabilityBreakdown` fields: `p: float|None`, `terms: dict`, `missing: tuple[str,...]`, `eligible: bool`, `policy_version: str`. Decision fields: `decision_id: str`, `checkpoint: str`, `draw: float`, `base: dict`, `modifiers: dict`, `final_p: float|None`, `intent: str`, `input_hash: str`, `warnings: tuple[str,...]`. None probability means blocked essential data, not zero risk.

`engines/retirement_context.py` exports `build_context(snapshot: dict, evidence: dict, *, feature_mode: str) -> RetirementContext`. It performs no implicit DB reads. Each evidence bundle includes `source_event_ids`, `as_of`, `coverage` (`complete|partial|unknown`) and fingerprint. Reject future facts, cross-universe identity and unsupported versions.

- Ability uses permanent base ratings/version, positional employability and covered recent production. Exclude temporary form and avoid counting wear twice through ability and health.
- Health uses recorded injury duration, missed games and mileage coverage. Unknown is not healthy or permanently disabled. This policy creates no diagnosis.
- Opportunity includes existing legal contract, usable role, real offer IDs, acceptance results from the contract owner, roster legality, stage and market coverage. Need alone is not an offer. No offers before market opening is not rejection.
- Career includes verified seasons, tenure and achievements. Earnings shape: `{known_cap_share: str|null, coverage, secure: bool|null, source_version: str|null, source_ids: [...]}`. Partial low earnings do not prove insecurity. A lower bound can prove security only under the earnings owner's reviewed rule. Face value is not earned money; earnings are not net worth.
- Goal shape: `{goal_id, kind: record|title|role, target_id, current_value, target_value, projected_range, confidence, coverage, source_ids}`. Reachability needs a documented production/role scenario. Tied, surpassed, unreachable and unknown are distinct. Missing record coverage disables the term.
- Attachment contains versioned tenure, meaningful relationships, role satisfaction, treatment and unresolved breaches, from the relationships plan's `relationship_context` (the player's own one-way view of his team). Never friend counts. Hometown remains separate and initially unused here.

Baseline snapshots work without core, earnings or relationship integration. Use a verified universe/immutable player key for replay; absent or ambiguous identity blocks. Core aliases later bind that same person without redrawing. Origin comes from the lifecycle plan (every player is generated), never from SAVE provenance or a name. Optional adapters consume explicit versioned snapshots rather than importing unapproved interfaces.

### 2.2 Neutral baseline before modifiers

Proposed form: `p_base = sigmoid(age_position_intercept + ability_term + health_term + opportunity_term)`. `RetirementPolicy` stores version, supported domains, position/age curves, bounded terms, modifier bounds and `review_only: bool`.

No coefficient values are calibrated or approved here. Synthetic policy files must say `review_only=true`; production policy validation refuses them. Age/position establish career stage, ability addresses feasible work, health addresses recorded burden and opportunity addresses an actual destination. Missing optional evidence gives a zero term plus warning; missing essential age/position/identity yields BLOCKED_DATA. Review young unsigned exits separately; low ability or no contract does not itself mean retirement.

No age cliff, compulsory maximum age or fixed annual exit count. Calibration needs recorded retirement outcomes with cuts/unsigned players censored; mixed roster disappearance cannot train retirement labels. Review position cohorts, rare long careers and uncertainty. Before calibration, output is a demonstration, not a prediction.

### 2.3 Bounded optional inputs

Optional adjustment is one bounded aggregate log-odds delta, with base/final probability and excluded reasons recorded. Bounds, floors/ceilings and coefficients require later review; catalogue placeholders are not coefficients. Same inputs with features off, absent profile or empty permitted view must produce exactly the neutral result.

Legacy Builder requires a reachable recorded goal and usable opportunity; it never suppresses retirement risk to zero. Coasting alone never triggers retirement; defer numeric effect without a distinct reviewed career-preference mechanism. Strong ring preference requires established career evidence, achieved financial security and usable contender role together; unknown earnings disables it, and elite OVR is no substitute. Earned attachment is team-specific, can weaken after mistreatment, and does not guarantee a contract or lifelong loyalty. Allocate attachment once across career willingness/contract utility to avoid double counting.

Read only core fields permitted for `retirement`; every player is generated, so that is his full profile. Every player may evolve from canonical simulated events through the core; this engine does not mutate profiles. A missing profile is neutral; schema/storage/corruption error blocks effects-enabled finalization. A labelled neutral preview may still be shown.

### 2.4 Seeded annual review and one career state machine

`annual_draw` hashes canonical UTF-8 JSON `["mu-retirement-v1", universe_id, person_key, earned_season, seed_version]` with SHA-256, uses the first 64 bits unsigned divided by `2**64`, and persists the draw/algorithm. No global RNG, Python hash, weekly reroll, team or name seed. Updated policy needs explicit superseding review, not a new lucky draw.

Checkpoints are ordered `OPEN`, `MARKET_CLOSE`, `ROLLOVER_REVIEW`. New evidence can change the threshold/state with the same draw; each frozen checkpoint replays exactly. Market-close fallback is a distinct reviewed context, not another independent annual hazard.

Proposed `engines/career_decision_state.py` exports `transition(state: dict, event: dict) -> dict`. State shape: `{decision_id, universe_id, person_key, earned_season, state, revision, reservation}`. Event shape: `{event_id, source_event_id, kind, expected_revision, payload, actor}`. Receipt: `{event_id, request_hash, before, after, outcome, reasons}`.

| State | Allowed path |
|---|---|
| OPEN | annual review → AVAILABLE, RETIRE_INTENT or BLOCKED_DATA |
| AVAILABLE | acceptable legal offer reservation → SIGN_PENDING; market close → RETIRE_INTENT or UNSIGNED_RETURN_ELIGIBLE |
| RETIRE_INTENT | explicit cancellation/review, or prepare → RETIRE_PENDING; no signing reservation |
| SIGN_PENDING | verified contract receipt → RETURNING; failed execution → AVAILABLE; no retirement preparation |
| RETIRE_PENDING | verified save receipt → RETIRED; failed execution → RETIRE_INTENT; no contract acceptance |
| RETURNING | annual terminal choice backed by existing contract or verified signing; no new annual roll |
| UNSIGNED_RETURN_ELIGIBLE | remains genuinely unsigned, may reserve a later legal street offer; next season gets its own annual review |
| RETIRED | terminal; comeback needs separately reviewed reversal workflow |
| BLOCKED_DATA / CONFLICT | reconciliation only; no automatic edits |

An offer is needed to sign, not to want to continue. No acceptable offer can yield retirement or continued unsigned availability under the reviewed market-close policy. Existing contracted players may return without a new deal. If native purge prevents preserving an unsigned player, block rollover for review; never manufacture a signing.

Persistence APIs `reserve_contract(cur, decision_id, offer_id, expected_revision)` and `prepare_retirement(cur, decision_id, expected_revision)` take the same per-person advisory lock and CAS revision inside the caller's transaction. Retirement excludes pending sign/extension/tag/trade reservations; signing excludes retirement. Pure acceptance previews do not reserve. Every committing re-sign, extension, FA wave, street and user-move path must participate before activation. Contract acceptance/legality stays with its owner. Reconcile this proposed boundary with U13 and contract owners at G0.

### 2.5 Persistence, migration, idempotency and rollback

Migration `db/migrations/2027_01_26_retirement_policy.sql` (reserved in PERSONALITY_PLAN §5.3; recheck at build). Baseline requires V2 schema, not persona migration.

- `career_decision_states`: `universe_id TEXT`, `person_key TEXT`, `earned_season INT`, `player_id INT REFERENCES players(id)`, `state TEXT` checked against §2.4, `revision INT CHECK >=0`, `decision_id UUID UNIQUE`, `reservation JSONB NULL`, `updated_at TIMESTAMPTZ`; PK `(universe_id,person_key,earned_season)`.
- `retirement_policy_events`: `event_id UUID PRIMARY KEY`, `decision_id UUID REFERENCES career_decision_states(decision_id)`, `checkpoint TEXT`, `event_kind TEXT`, `source_event_id TEXT`, `request_hash TEXT`, `input_snapshot JSONB`, `policy_snapshot JSONB`, `draw_hex TEXT`, `breakdown JSONB`, `before_state JSONB`, `after_state JSONB`, `created_at TIMESTAMPTZ`; unique `(decision_id,source_event_id)`.
- `retirement_write_receipts`: `operation_id UUID PRIMARY KEY`, decision FK, `status TEXT CHECK (PREPARED,VERIFIED,FAILED,CONFLICT)`, `source_save_hash TEXT`, nullable output hash, `edits_hash TEXT`, nullable `league_office_run INT` FK, `before_image JSONB`, `verification JSONB`, creation time. Prepared request stays immutable; acknowledgement uses CAS.

All non-null fields above are NOT NULL except explicitly nullable fields. JSON includes actor, authority, input coverage, all profile/catalogue/goal/earnings/relationship versions, policy snapshot and neutral counterfactual. Decimal parameters serialize canonically. Feature errors cannot silently produce an effects-enabled receipt.

`retirement_store.prepare(cur, request, expected_revision)`, `acknowledge(cur, operation_id, receipt)`, `replay(cur, event_id)` never commit. Same ID/payload returns original receipt even after newer revisions; changed payload under event/source ID fails. Source-event deduplication and CAS prevent duplicate execution. Alias changes never create another annual decision.

Existing `retirement_decisions` remains a U13 compatibility projection after verification: observed native retirement and verified keep override, never policy authority. Preserve captured `player_retirements`; Mu receipts are separate. Continuity owner must reconcile reads and stale `players.retired_season` after native restoration before activation. No synthetic capture rows.

Transactional DDL, guarded creation and structural validation make reapply safe; `IF NOT EXISTS` alone does not validate incompatible tables. Mirror in init_db under one owner. No auto-backfill or auto-application. Before activation rollback can drop empty sidecars after disabling consumers; after audit exists retain tables and disable features. Save writes cannot be undone by DB rollback: reconcile receipts or restore the exact pre-write disposable save with watcher paused. Never redraw acknowledged history.

### 2.6 Save authority, native conflicts and preview

`pipeline/retirement_edits.py` exports `plan_retirement_edits(snapshot: dict, decisions: list[dict], authority: dict) -> dict` returning `{edits, preconditions, expected, conflicts}`. Default deny until a versioned authority fixture proves schema, stage, fields/references and mod exclusivity. `KEEP_NATIVE_RETIREE` and `RETIRE_ACTIVE` need separate proof. Restoration does not prove active contracted retirement is safe.

Retain a pre-native baseline of identity, status, roster/PS/depth, contract/cap, injury and attributes. Compare first offseason retirement snapshot. Preserving someone for market consideration is not signing them. Unexpired contracts must be restored correctly; do not default everyone to FreeAgent. Missing baseline, Deleted record or ambiguous pgid → CONFLICT; recovery after deletion is not promised.

Prove roster/depth/PS references, injuries, contract/dead money/cap effects, transactions/history and next-advance behavior. Release is not retirement. Writer must reject overlaps with sign/release/trade/player/record edits for the person. U13 owns closed-game checks, exact source hash/franchise/stage, weekly lock, backup, output copy, readback, semantic verification, acknowledgement, re-ingest and watcher self-write suppression. Save receipt must precede execution claims. Native retirement after a Mu return is a conflict that pauses advance, not an automatic weekly override loop.

Inventory the external Arbitrator's actual workflow. Either disable it for the managed lineage or prove an exclusive handoff; never run two restoration owners. Current files establish no authority.

`python -m tools.retirement_preview --input fixture.json --policy policy.json --mode baseline|compare --out report.json` is offline only, accepts no DSN or commit flag, and launches no binaries. Report contains coverage, base/final terms, same draw, state, native observation, conflicts, authority blocks and review-only labels.

Activation gates: **G0** direct plan review/interface reconciliation; **G1** synthetic baseline and later calibrated-policy review; **G2** replay/state/CAS proofs; **G3** separate optional modifier/control review; **G4** disposable-save authority proof and reviewed U13 integration; **G5** separately authorized migration/shadow run and baseline activation; **G6** separately authorized personality activation. Profile availability opens no gate. This plan's units deliver offline policy infrastructure, not live wiring.

Control recommendation: same CPU policy through 2033. From 2034 the configured JR team receives a review hold before irreversible retirement; approve/defer or explicit audited override. No fixed team-ID immunity and no forcing an unwilling person to accept a contract by clicking return. Unanswered holds stop rollover when preservation is at risk.

## 3. Risks and unknowns

| # | Risk | Find out | Failure outcome |
|---|---|---|---|
| R1 | Status reset misses references or is re-retired next advance. | Disposable before/after stage and rollover comparisons. | Preview only. |
| R2 | Arbitrator overlaps or supports restoration Mu cannot. | Source/package workflow inventory, later authorized isolated test. | Choose one owner; unsupported actions blocked. |
| R3 | No retirement ground truth/calibration. | Censor cut/unsigned exits; review approved recorded cohorts and uncertainty. | Synthetic policy only, no numerical claims. |
| R4 | Contract/cap/history corruption on restore/retire. | Separate expired/active/FA/PS/injured fixtures and next-advance checks. | Restrict supported actions or defer authority. |
| R5 | Signing/save races or partial DB/save success. | CAS concurrency, stale hash and crash-after-write tests. | Refuse batch or reconcile receipt, never claim success. |
| R6 | Earnings/relationships/identity interfaces change. | Reconcile owning snapshots at G0; version adapters. | Neutral baseline remains available; modifiers off. |
| R7 | Record/age coverage incomplete; walkthrough notes no age increment. | Audit age source and canonical record completeness. | Disable goal terms; block essential age ambiguity. |

**Spike first:** U0 source-only authority/interface inventory before wave 1, estimated one focused session. It may leave field authority explicitly unresolved without blocking offline policy. Disposable-copy save experiments require later direct authorization before G4; no live/archive database needed. Compare pre-native, first retirement, restored, next-stage and rollover snapshots; active-retirement proof is separate.

## 4. Tests (synthetic, no live DB)

- `tests/test_retirement_policy.py`: supported domains, missing essentials, bounded neutral terms, position/age sensitivity, young unsigned distinction, no quotas/cliffs, deterministic draw across rename/trade/order and universe isolation.
- `tests/test_retirement_context.py`: unknown vs zero, partial earnings not insecurity, feature permissions, future evidence, form exclusion and identity errors.
- `tests/test_retirement_modifiers.py`: exact neutral control, no label-forced retirement or immortal Legacy Builder, uncertain records, career/security/role conjunction, no friend-count/hometown substitutes and aggregate bounds. Numeric fixtures are synthetic only.
- `tests/test_career_decision_state.py`: all transitions, existing-contract return, no-offer retirement and unsigned outcomes, same annual draw, signing/retirement exclusivity and failed-operation recovery.
- `tests/test_retirement_store.py`: caller transaction ownership, collisions, replay after newer events, CAS race winner, partial failure rollback, twice-applied migration, schema mismatch and audit retention. Real DB cases marked integration in isolated madden_test schema only.
- `tests/test_retirement_edits.py`: unsupported authority, source hash/stage mismatch, deleted/missing baseline, move/field conflicts and distinct restore/retire capabilities using synthetic fixtures.
- `tests/test_retirement_preview.py`: deterministic comparison report, no DSN/binary/save writes, clear unknown/uncalibrated labels and same neutral draw.

Run `.venv/bin/python -m pytest -m "not integration" tests/test_retirement_policy.py tests/test_retirement_context.py tests/test_retirement_modifiers.py tests/test_career_decision_state.py tests/test_retirement_store.py tests/test_retirement_edits.py tests/test_retirement_preview.py -q`. Scratch integration requires explicit existing configuration; never provision from live data. No tests run merely to draft this plan.

## 5. Work breakdown

Shared orchestrator files: PROJECT_SPEC.md, this plan, CLAUDE.md. No unit edits core, earnings, other plans or active progression files.

### U0: Authority/interface inventory
- **Owns:** `docs/reference/RETIREMENT_AUTHORITY.md` (new).
- **Reads:** reader/writer, U13, walkthrough, mod inventory, current personality/contract plans.
- **Depends on:** none.
- **Done when:** cited matrix distinguishes observed timing from proven authority, lists unsupported actions and reconciled/unresolved interface decisions.
- **Verify:** manually check cited source locations; no executables/DB/save mutations.

### U1: Neutral and optional pure policy
- **Owns:** `engines/retirement_policy.py`, `tests/test_retirement_policy.py` (new).
- **Reads:** §2.1–2.4, core README, offline tuning limitations.
- **Depends on:** U0 inventory.
- **Done when:** all named policy functions work with explicit synthetic parameters and production rejects review-only policy; modifier coefficients remain unapproved.
- **Verify:** `.venv/bin/python -m pytest -m "not integration" tests/test_retirement_policy.py -q` passes.

### U2: Career arbitration
- **Owns:** `engines/career_decision_state.py`, `tests/test_career_decision_state.py` (new).
- **Reads:** §2.4, contract execution paths, U13 design.
- **Depends on:** U0; dictionary boundary does not import U1.
- **Done when:** transitions enforce exclusive reservations and represent unsigned availability honestly.
- **Verify:** `.venv/bin/python -m pytest -m "not integration" tests/test_career_decision_state.py -q` passes.

### U3: Context and modifier controls
- **Owns:** `engines/retirement_context.py`, `tests/test_retirement_context.py`, `tests/test_retirement_modifiers.py` (new).
- **Reads:** core, effect design, `engines/career_earnings.py` (built; PROJECT_SPEC §2.17e), relationship proposals.
- **Depends on:** U1.
- **Done when:** explicit snapshots validate coverage/scopes and matched controls exercise U1 functions without external dependencies. Any required policy fixes go back to U1's owner sequentially.
- **Verify:** focused context/modifier pure tests pass.

### U4: Durable state and schema
- **Owns:** `engines/retirement_store.py`, `db/migrations/2027_01_26_retirement_policy.sql`, `db/init_db.sql`, `tests/test_retirement_store.py` (new except init_db).
- **Reads:** §2.5, core transaction patterns, firewall and U13 receipts.
- **Depends on:** U1, U2.
- **Done when:** replay/reservations/receipts and schema checks pass offline/scratch tests; no auto-application. Recheck migration name; `db/init_db.sql` is also edited by the inbox, press conference, scouting, trade-talks, lifecycle and salary-cap-growth plans, so U4 re-reads it and merges after whichever lands first.
- **Verify:** focused pure store tests plus explicitly configured isolated-schema migration/CAS tests pass.

### U5: Guarded planner and preview
- **Owns:** `pipeline/retirement_edits.py`, `tools/retirement_preview.py`, `tests/test_retirement_edits.py`, `tests/test_retirement_preview.py` (new).
- **Reads:** authority report, §2.6 and writer conflict checks.
- **Depends on:** U1, U2, U3; uses documented receipt dictionaries, no DB import.
- **Done when:** deterministic offline report shows control/uncertainty/authority blocks and CLI has no commit path.
- **Verify:** focused edit/preview pure tests, CLI `--help`, then full focused §4 pure suite pass once.

## 6. Waves

| Wave | Units | Needs first | Reason |
|---|---|---|---|
| 0 | U0 | JR approval (done 2026-11-03) | Inventory design-changing authority gaps. |
| 1 | U1, U2 | U0 | Independent policy and state files. |
| 2 | U3, U4 | U1; U4 also U2 | Context/controls and persistence share no files. |
| 3 | U5 | U1, U2, U3 | Compose pure preview and guarded planning. |
| Gate only | Separate U13 integration | G1–G4 and reviewed file ownership | Coordinate writer/reader/watcher/contracts/ledger changes before live action. |

Earnings/relationships do not block baseline work. U13 integration is explicitly not delivered by these units; it needs its own concrete file list/tests after authority is known.

**File ownership check:**

| File | Unit |
|---|---|
| `db/init_db.sql` | U4 |
| `db/migrations/2027_01_26_retirement_policy.sql` | U4 |
| `docs/reference/RETIREMENT_AUTHORITY.md` | U0 |
| `engines/career_decision_state.py` | U2 |
| `engines/retirement_context.py` | U3 |
| `engines/retirement_policy.py` | U1 |
| `engines/retirement_store.py` | U4 |
| `pipeline/retirement_edits.py` | U5 |
| `tests/test_career_decision_state.py` | U2 |
| `tests/test_retirement_context.py` | U3 |
| `tests/test_retirement_edits.py` | U5 |
| `tests/test_retirement_modifiers.py` | U3 |
| `tests/test_retirement_policy.py` | U1 |
| `tests/test_retirement_preview.py` | U5 |
| `tests/test_retirement_store.py` | U4 |
| `tools/retirement_preview.py` | U5 |

No duplicate paths. Shared init_db edits must be scheduled exclusively; never let concurrent units edit it.

## 7. Spec changes when built

Parent/orchestrator coordinates these; no shared spec edit in this planning task.

- Engine sections: retirement baseline, state machine, optional gates and uncertainty; distinguish offline tuning exits.
- §2.17a–d / §4.6b: reservations/user review only after actual integration; no new endpoint delivered here.
- §3.3m: captured facts vs intent/execution/return and explicit scan completeness, after continuity integration.
- §3.3q: new state/events/receipts, compatibility projection, migration status, replay and rollback.
- §3.3p / §7.11: retirement-scoped core reads, neutral absence vs errors, activation separate from profile availability.
- §6.0b/offseason: proven stage cutoff, locks, native/mod authority, output re-ingest and conflict handling only when wired.
- Repository Layout: proposed modules/tests/reference/migration/CLI above. Personality roadmap should link this plan and qualify old unconditional Coasting/Legacy examples. No earnings/band ownership changes.

## 8. Decisions for JR

JR approved all four recommendations on 2026-11-03.

| # | Design-changing question | Decision (JR, 2026-11-03) |
|---|---|---|
| Q1 | Should Mu ultimately restore native retirees and retire native-active players, or only restore initially? | Design both actions; enable restoration first after proof. Retire-active stays unsupported until its own contract/cap/history test passes. Preview only if neither is proven. |
| Q2 | From 2034, should JR's team get a review hold, and what if unanswered at rollover? | Hold irreversible retirements for review and stop advance until resolved. Explicit overrides audited; no forced contract acceptance. |
| Q3 | May someone who wants to continue but has no acceptable offer remain unsigned next season? | Yes when preservation is proven; otherwise block rollover for review, not invented retirement/signing. Reuse annual draw, no weekly rerolls. |
| Q4 | What unlocks baseline calibration and later modifiers? | Synthetic/state work first; recorded retirement cohorts with cut/unsigned censoring and position/age uncertainty before baseline activation. Separate matched-control review for modifiers; no numerical coefficients approved now. |

Plan approval does not authorize save experiments, migration or activation; those are gates G4–G6.
