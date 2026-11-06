# Project Mu: Player Dissatisfaction and Trade Requests Plan

**Status:** approved by JR (2026-11-03), Q1–Q4 settled as recommended (§8); built and on main (2026-11-06): pure policy, store and migration file (not applied), offline preview; no live caller, no production policy. Approval covers building the units; production policy, migration and activation each still need JR's separate go-ahead (§6 gates G3–G5).
- **As built (wave 0, spike):** answered offline from source, no database. Clock: the relationships calendar's TimePoint {season, tick} (32 ticks a season, `engines/relationship_producers/calendar.py`); `RequestClock.ordinal` = `season * 1000 + tick` (`clock_ordinal`), so it is monotonic across seasons and offseason ticks. Incidents: the relationships plan built reactions and breach states but no incident ledger, so U1 adds a pure adapter `incidents_from_shared(events, relationship_state, reaction_result, ...)` that maps the shared outputs without re-deciding them: a sustained-role `frustrated` reaction → `LOST_ROLE` (observations = its contiguous unsatisfied `role_window` run), an unresolved material breach (`dismissal`/`disrespect` treatment, `broken` promise) → `TREATMENT`/`BROKEN_PROMISE` (observations = contiguous known role windows on that team since the breach), a breach closed by the relationships repair rules or a role run ended by satisfied windows → `REPAIRED` (repair IDs = the closing repairs / satisfied windows; observations counted after them). A reaction result in `off` mode yields no incidents (neutral, deferred). Incident IDs (`inc:<kind>:<team_key>:<first source>`) are the contribution keys; their source IDs are the relationship breach source IDs the contract policy already keys on. Identity: core `persona_id` through verified `db-player` aliases. Teams: the real PK is `teams.id` (not `team_id`), team key `team:<teams.id>`. Authority: `control_context(team_id, user_team_ids, suggestions_on)` — JR's teams from the franchise registry's `user_teams` (abbreviations mapped to ids by the caller), never Madden's ALLUSER flag; `user_team_ids=None` gives `UNKNOWN`. GM handoff: `gm_request_signal(episode)` returns the activation plan's `{request_id, persona_id, team_id, status, cause_codes, actionable}`; scheduling and `gm_trade_engine` edits stay with the activation plan's U3.
- **As built (U1):** `engines/player_trade_requests.py` as §2.1–§2.5, plus `clock_ordinal`, `seeded_draw`, `request_id_for`/`decision_id_for` (UUID5 of the annual key), `control_context`, `reactivity_probability`, `gm_request_signal`, `incidents_from_shared` (the spike's adapter) and dict round-trips (`to_dict`, `*_from_dict`). `RequestEpisode` also carries `decision_id`, `initial_source_ids`, `cause_codes`, `policy_version`, `resolution_reason`, `linked_event_ids`, `disclosed_status`/`disclosed_ordinal` (public facts stay dated until the next disclosure). A review with an annual decision already stored returns `NOOP` (`annual_opportunity_used:<outcome>`); a closed window defers (`window_closed`). `request_projection` and `gm_request_signal` take an optional `clock` for `actionable` (open trade window). Response actions need `payload.response_ref` (actor `JR` on JR's teams, `GM` on simulated ones); `REVIEW_REPAIR` returns `REPAIR_STARTED` when every cause is repaired but not yet for `repair_observations`, `PAUSED` when a cause is unassessed/open or another open cause exists; `CLOSE_EMPLOYMENT` with reason `UNKNOWN` returns `DEFERRED`; a `CORRECT` with valid initial evidence links (revision +1) without changing status.
- **As built (U2):** the migration verifies `persona_core_profiles` and `teams` exist first; `team_id` references `teams(id)` (the real PK); `player_trade_request_decisions` adds an `outcome` column (REQUEST/NO_REQUEST) and its `receipt` holds `{annual, decision}`; `player_trade_requests.state` must agree with the `status`/`revision` columns. A review's event ID is `review:<season>:<window_id>:<checkpoint_id>` and its replay hash covers the caller's inputs (context, clock, policy, mode), not stored state, so an exact replay returns the original receipt after later changes. `record_decision` raises for SHADOW and returns the no-op without SQL for DISABLED; `apply_action` maps engine conflicts to `StoreConflict`. Rewinds: a stored active episode or this season's decision dated after the clock raises `MixedRewindError`. The four `TRADE_REQUESTS_TEST_DSN` scratch cases (repeat migration + lifecycle/replay/one-active, forced rollback keeps the caller transaction, incompatible table, concurrent annual claims) were **not run: no PostgreSQL in the build environment**, so G2 persistence acceptance is still outstanding; the fake-cursor half (16) passes.
- **As built (U3):** `--example PATH` writes the synthetic 2026–2035 input (benched DIVA → request, GM refusal, disclosure, executed trade; repaired broken promise; NO_REQUEST that cannot reroll; a JR team in 2034 with suggestions off and an approved-but-unexecuted deal rejected as `not_executed`); example personas are picked deterministically by their fixed seeded draws so the matched arms differ. Optional input `sensitivity: [{name, policy}]`; actions with `expected_revision: null` use the current revision. The contract handoff test uses a stub consumer (not production integration). A first merge had one failing assertion (it matched the word "environment" in the docstring); fixed on the same branch and merged again.
**JR decisions this plan follows (2026-11-03, PERSONALITY_PLAN §5.1, D8–D13):** every player has a generated profile (the 2026 roster at the league start, prospects at class entry), editable by JR; players named after real people are fictional characters inspired by them. The relationships plan owns one-way trust, and its `promise_made`/`promise_resolved` records (including promises from V2.9 inbox role talks) are this plan's broken-promise source.
**Builds on:** PROJECT_SPEC §2.17, §2.17a–d, §3.3f, §7.11; [PERSONALITY_PLAN.md](../in_progress/PERSONALITY_PLAN.md) §4.6–4.8, §5.2; [PERSONALITY_GENERATED_EFFECTS_DESIGN.md](../in_progress/PERSONALITY_GENERATED_EFFECTS_DESIGN.md) §3–5.

**Goal:** Give players a persistent, explainable way to request a move after verified sustained dissatisfaction, and to reconcile when the cause is repaired. Preserve JR's control and existing GM valuation, legality and execution. A request records a desired outcome; it does not execute a trade.

**Delivers:**
- Pure `engines/player_trade_requests.py`: qualification, annual seeded decisions, lifecycle and visibility projections.
- `engines/player_trade_request_store.py` and a separately applied migration: annual receipts, request episodes and append-only actions.
- `tools/player_trade_request_preview.py`: synthetic JSON review without database access.
- Proposed relationship/reaction and contract-policy interfaces, with production integration explicitly deferred.

Chronology: Madden starts in 2026, Mu manages the CPU league through 2033, JR takes over in 2034. That is not permission to invent retrospective history; this plan initializes no profiles.

## 0. Survey (code facts that set the scope)

Source inspection on 2026-10-31 in `/home/jrereyi/Project-Mu`. Existing tracked changes and untracked core/design work remain untouched. Refresh line references and dependency versions before implementation.

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `personality/core.py:38–54` derives identity from universe/role/immutable birth key; `:144–163` returns feature-scoped views, missing profiles neutral and real fields evidence-filtered. | Use `feature_view(profile, 'trade_requests')`; no name/team/OVR identity. Players are all generated, so the view is the full profile. |
| S2 | `personality/store.py:35–61` reads without seeding and detects external changes; `:119–142` owns verified aliases. | Require verified identity binding. This feature never initializes/adopts profiles. |
| S3 | `personality/store.py:145–184` audits explicit evolution; `personality/core.py:166–223` limits changes by revision and season. | Request state is separate feature state, not a trait mutation or use of the annual evolution slot. |
| S4 | `db/migrations/2027_01_16_personality_core.sql:8–42` supplies profile/alias/event sidecars. `personality/README.md` explicitly says no production ingestion hook or franchise migration was applied. | Core API availability is not feature activation. Binding, producer and migration gates remain. |
| S5 | `engines/gm_trade_engine.py:106–162` owns windows/stages, `:687–774` legality (NTC at `:701–704`), `:1181–1201` GM acceptance. | Requests cannot waive rights, discount value or bypass checks. |
| S6 | `engines/gm_trade_engine.py:1223–1225` excludes user teams with suggestions off; `:1621` flags advisory proposals; `:1478–1524` stores proposals/statuses. | Separate truthful player state from proposal permission and team response. |
| S7 | `pipeline/trade_moves.py:48–53` plans confirmed operations; `:152–166` executes them in caller transaction and records traded transactions. | Only verified executed movement resolves a request as traded; approval is insufficient. |
| S8 | `engines/contracts_engine.py:176` owns extension eligibility; `engines/free_agency_engine.py:188–205` scores offers and `:230–300` owns acceptance/waves. | Export request context; contract owner decides willingness and formulas. |
| S9 | `newsroom/personas.py:301–302` documents deterministic fallback. `db/migrations/2027_01_09_personas.sql:21` enforces total 30; `newsroom/personas.py:176–177` does too. | Never call fallback. Superseded 2026-11-02: the cap-relative plan corrected its survey and built the 24–36 band in Python and SQL (`2027_01_17_persona_slider_band.sql`, applied to `madden_test`). This feature writes no sliders. |
| S10 | Generated-effects design `:70–95` describes request-only effects and annual decisions; personality plan `:159–165` retains old grudge-discount prose. | Follow later design; coordinator reconciles obsolete prose in §7. Illustrative odds are not calibration. |
| S11 | `tests/conftest.py:1–12` describes DB isolation; `_check_connection_allowed` rejects franchise/archive connections. | Synthetic tests and isolated scratch schemas only; never napper wholesale. |

Related plans inspected: the [cap-relative plan](../done/CAP_RELATIVE_CONTRACTS_PLAN.md) (built and on main 2026-11-02) owns pricing, earnings and the slider band; do not edit or duplicate it. `AGENT_WIRE_VOICE_PLAN.md` owns writer biases, not player treatment truth. The [relationships plan](../done/PERSONALITY_RELATIONSHIPS_PLAN.md) (approved 2026-11-03, not built) supplies incidents, promises and repair; its interfaces are dependencies to reconcile before build. No proposed request function is claimed to exist today.

## 1. Scope

**In:**
- Persistent lost role, broken recorded promises and verified adverse treatment; consume shared assessments without copying relationship/mood state.
- Annual/window deduplication, deterministic choices, reconciliation, disclosure, user control, factual statuses and audit.
- Neutral event baseline for every player; only scoped traits modify it. Unknown inputs never create grudges.
- Offline baseline/personality comparison and isolated persistence checks; no live wiring.

**Out (non-goals):**
- Holdouts, missed practice, fines, effort/form/morale changes, reduced playing time, forced release or save writes. A later consequences plan must justify these independently.
- GM search budgets, trade valuation/discounts, acceptance, legality, execution or player consent rights. Existing owners retain them.
- Relationship storage, raw snaps-to-role inference, promise creation, treatment scoring, earnings/security or profile initialization/evolution.
- Production scheduler/API/frontend/newsroom edits; a later integration unit must own their shared files.
- Automatic extension refusal or utility penalties. Contract policy owns willingness, with shared incident deduplication.

## 2. Design

Names below are **proposed** except the cited existing core APIs. Build offline/shadow first. No production probabilities are supplied or implicitly approved.

### 2.1 Inputs and shared ownership

Define frozen dataclasses with schema-v1 JSON serialization in `engines/player_trade_requests.py`:

| Type | Exact fields |
|---|---|
| `RequestClock` | `universe_id: str, season: int, checkpoint_id: str, ordinal: int, window_id: str, window_kind: str, trades_open: bool, source_event_id: str` |
| `ControlContext` | `team_id: int, user_controlled: bool, suggestions_on: bool, authority: str` |
| `Incident` | `incident_id: str, source_event_ids: tuple[str,...], kind: str, target_team_id: int, first_ordinal: int, last_ordinal: int, eligible_observations: int, severity: str, state: str, repair_event_ids: tuple[str,...], disclosure: str` |
| `RequestContext` | `schema_version: int, persona_id: str, team_id: int, profile_view: dict, relationship_revision: str, reaction_revision: str, assessment_id: str, as_of_ordinal: int, complete_through_ordinal: int, incidents: tuple[Incident,...], control: ControlContext` |

`ordinal` is a monotonic canonical simulation checkpoint, never wall time. Window kind is `IN_SEASON|OFFSEASON|CLOSED`, with stage included in `window_id`. Consume authoritative window facts; do not reproduce GM window calculations. Authority is `SIMULATED|JR_RECORDED|UNKNOWN`, from explicit configuration rather than season alone.

Incident kinds: `LOST_ROLE|BROKEN_PROMISE|TREATMENT`; states: `OPEN|REPAIRED|DISPUTED|UNKNOWN`; severity: `MATERIAL|SEVERE|UNKNOWN`; disclosure: `PRIVATE|PUBLIC_VERIFIED`. Shared relationship/reaction owner groups repeated observations into one incident, certifies duration/severity/repair and owns that ledger. This feature stores decision snapshots/references only. Conflicting source IDs mapped to different incidents reject the batch pending reconciliation. Do not add trust, mood, attachment or grudge counters here.

A promise is a relationships-plan `promise_made` (issuer, beneficiary, measurable criterion, due time) and a breach its `promise_resolved` with `resolution='broken'`; V2.9 inbox role talks are the first producer. Prose, losing games, perceived unfairness or missing role data cannot establish a breach. Completeness means all eligible observations in the interval are accounted for; unavailable assessments are deferred, not interpreted as happiness or hostility.

Use existing `feature_view(profile, 'trade_requests')`. Persist filtered view and core/catalogue/profile versions in receipts. Missing/unknown profile gives neutral event baseline. A storage/schema/corruption error blocks persisted evaluation rather than silently substituting a profile. Every player, real-inspired or not, responds to actual recorded simulated events; nobody receives invented history. No request action changes a profile.

### 2.2 Pure interfaces and policy

```python
def validate_request_context(context: RequestContext, clock: RequestClock) -> None: ...
def qualify_request_causes(context: RequestContext, clock: RequestClock,
                           policy: RequestPolicy) -> Qualification: ...
def evaluate_request(context: RequestContext, clock: RequestClock,
                     prior: RequestEpisode | None, annual: AnnualDecision | None,
                     policy: RequestPolicy, *, mode: str) -> RequestDecision: ...
def transition_request(prior: RequestEpisode, action: RequestAction,
                       context: RequestContext, clock: RequestClock,
                       policy: RequestPolicy) -> TransitionReceipt: ...
def request_projection(episode: RequestEpisode, *, audience: str) -> dict | None: ...
def contract_request_context(episode: RequestEpisode | None) -> dict: ...
```

Modes: `DISABLED|SHADOW|ENABLED`. Disabled does nothing and consumes no annual opportunity; shadow outputs hypothetical receipts only to offline reports. No live enabled caller is included.

`RequestPolicy(version: str, role_observations: int, breach_observations: int, repair_observations: int, baseline_probability: float, lower_probability: float, higher_probability: float, reactivity_rule: str)` requires version, positive counts, finite [0,1] probabilities and lower ≤ baseline ≤ higher. No production numeric defaults. Synthetic fixtures may use .05/.10/.15 under `offline-illustration-v1`, explicitly rejected for enabled persistence.

`Qualification` returns `eligible`, sorted distinct incident/source IDs, `reasons`, `deferred_reasons`, `eligible_since_ordinal`. `RequestDecision` returns schema/policy versions, key, canonical input hash, mode, outcome (`NOOP|DEFERRED|INELIGIBLE|NO_REQUEST|REQUEST|EXISTING`), qualification, probability/draw, annual-slot flag, before/after episode and explanation. No LLM supplies truth or decides outcomes.

### 2.3 Qualification and seeded windows

Recommended v1 rules, subject to Q1:

1. Require active same-team employment, verified identity and complete chronological assessments. Closed windows permit repair/status actions but no new request draw. Incomplete data defers without consuming the annual slot. An active episode returns existing state without another draw.
2. Lost role needs four consecutive eligible team-game observations of material role loss against a recorded baseline/commitment, certified by the shared producer. Injury recovery, suspension, bye, offseason projection and unknown snaps do not count; an unexplained observation gap breaks the run. Ratings never create entitlement.
3. Verified broken promise/material treatment needs two subsequent eligible observations while unresolved. Disputed/unknown causes do not qualify. A severe incident has no hidden immediate exception in v1; independently verified other causes can still qualify.
4. First qualified open-window checkpoint gets one opportunity per `(universe_id, persona_id, season)`. `NO_REQUEST` consumes it. Weekly retries, new incidents, changed team or later window cannot reroll. Deferred/ineligible checks do not consume it. Active episodes carry across years without a new draw. Same league season numbering as the authoritative window owner, including offseason.
5. Seed = canonical UTF-8 JSON array `['trade-request-v1', universe_id, persona_id, season]`; SHA-256 first 8 bytes big-endian / `2**64` gives [0,1) draw. Store algorithm/draw. Policy changes cannot redraw or recompute a decided season. Identity, not team or display name, anchors the draw.
6. Offline reactivity proposal only: permitted DIVA or GRUDGE_HOLDER selects higher; PEACEMAKER selects lower; opposing signals select baseline. Never stack. Defer temperament mapping until its meaning is reviewed. LOYAL, money preference, quietness, confidence, hometown, age and OVR do not substitute for reaction evidence. No elite-OVR or financial-security shortcut.
7. Checkpoint review key `(universe_id, persona_id, season, window_id, checkpoint_id, action_kind)` dedupes no-op/deferred reviews too. Annual uniqueness is stronger; changing checkpoint names cannot buy another chance.

Four/two observations and the trait rule are recommendations, not empirical findings. Report sensitivity across supplied thresholds/odds before approval; do not invent a calibrated league request-rate target.

### 2.4 Episode lifecycle and reconciliation

`RequestEpisode`: `request_id` (UUID5 from annual key), universe/persona/team, `opened_season`, `opened_ordinal`, `revision`, `status`, `team_response`, `visibility`, immutable initial incident/source IDs, `last_review_ordinal`, `repair_started_ordinal`, `resolved_ordinal`, `resolution_source_event_id`. New evidence is linked by actions, never overwrites original causes.

Statuses: `ACTIVE|WITHDRAWN|RESOLVED_TRADE|CLOSED_EMPLOYMENT|VOIDED_CORRECTION`. Response is independently `NONE|ACKNOWLEDGED|DECLINED|EXPLORING`. A closed window makes `actionable=false`; it does not withdraw a request. Team refusal or rejection of a deal does not withdraw it either.

`RequestAction(event_id: str, source_event_id: str, expected_revision: int, kind: str, actor: str, payload: dict)` supports:

| Action | Required evidence/authority | Result |
|---|---|---|
| `ACKNOWLEDGE`, `DECLINE`, `EXPLORE` | Recorded JR response for JR team, explicit GM response event for simulated team | Response only; no new treatment incident automatically. |
| `REVIEW_REPAIR` | Shared assessment certifies all current causes repaired for two eligible observations, with repair IDs | `WITHDRAWN`, reason `RECONCILED`. One restored game/new promise alone is insufficient. Unknown repair pauses verification, never invents a breach. |
| `RECORD_WITHDRAWAL` | Verified player withdrawal event or explicit JR factual correction with provenance | `WITHDRAWN`; inbox dismissal/administrative suppression alone is not player withdrawal. |
| `RECORD_TRADE` | Executed transaction/ingest proof this player left requesting team by trade | `RESOLVED_TRADE`; approved/proposed deal never suffices. |
| `CLOSE_EMPLOYMENT` | Verified release, departure at expiry, retirement or other non-trade departure | `CLOSED_EMPLOYMENT` with specific reason; unknown roster mismatch defers. |
| `CORRECT` | Authorized factual correction, superseded references and explanation | `VOIDED_CORRECTION` where initial evidence invalid; preserve history, no implicit reroll. |
| `DISCLOSE` | Verified public statement or explicit reviewed simulation disclosure event | Public visibility; no automatic leak probability. |

Check team/identity, chronology and expected revision on every new action. Exact replay returns original receipt even after later changes; reused event/source IDs with changed payload fail. Terminal episodes never reopen in place. New-season requests require new qualifying evidence after resolution, not recycled repaired causes. Annual uniqueness prevents a same-season replacement. Unknown observations never erase known history, create grudges or certify repair.

### 2.5 Disclosure, JR control and consumer boundaries

`request_projection(..., audience='FRONT_OFFICE')` returns ID, identity/team, factual status, response, actionable, visibility, as-of checkpoint and safe reason codes. Authorized audit view can inspect private evidence separately. `audience='PUBLIC'` returns `None` for private/voided episodes and otherwise only disclosed facts. Never expose hidden traits, probability/draw, relationship scores or private promises to writers. Public closure requires a disclosure update; until then mark prior public facts as dated, not current. Private withdrawal stays private.

In 2026–2033 use simulated team authority where configured. From JR's 2034 takeover, Mu may simulate the player's decision while JR chooses team response and transactions. Other CPU teams remain simulated. `suggestions_on=false` suppresses GM proposal/advice, not authorized truthful request inbox state. Ignoring/dismissing a card is not treatment. Unknown authority blocks automated responses. Madden keeps all 32 teams as user teams (ALLUSER) to stop CPU moves; that flag is not authority. The franchise registry's `user_teams` (V2.9 inbox D6) is the source of truth for which teams JR controls.

Future GM handoff: `{request_id, persona_id, team_id, status, cause_codes, actionable}`. A later owner may consider an ordinary search using existing budget/rejection cooldown/valuation/legality. V1 exports a signal only; no `gm_trade_engine.py` edits. No guaranteed destination or deadline, price discount or universal player veto. Any NTC consent workflow belongs to legality owner; a request never waives a clause.

`contract_request_context` returns `{schema_version: 1, request_id, status, team_id, unresolved_incident_ids, source_event_ids, as_of_ordinal, policy_version}` or explicit absent context. No `refuse_extension` boolean or utility delta. Contract policy owns willingness while preserving legal eligibility. Relationship deterioration, request status and GRUDGE_HOLDER must refer to the same incident contribution key; one decision cannot penalize the same event three times. Request status is context/consequence, not a new adverse incident. Different consumers may make different decisions from one event; they cannot invent repeated additive penalties within one decision. Incident effects stay neutral in a consumer lacking deduplication support.

Earned attachment remains tenure/meaningful relationships/role/treatment, never friend counts; hometown is separate. This feature neither calculates attachment nor strong ring chasing. Financial security/established career/usable contender role remain contract-owner gates, never OVR shortcuts.

### 2.6 Persistence, migration, replay and rollback

Migration `db/migrations/2027_01_25_player_trade_requests.sql` (reserved in PERSONALITY_PLAN §5.3); coordinator rechecks collisions at build and renames references consistently. Do not edit core/legacy migrations.

- `player_trade_request_decisions`: `universe_id TEXT, persona_id VARCHAR(64), season INTEGER, decision_id UUID UNIQUE, input_hash TEXT, policy_version TEXT, seed_algorithm TEXT, draw DOUBLE PRECISION CHECK(draw>=0 AND draw<1), receipt JSONB`; PK universe/persona/season. Only annual REQUEST/NO_REQUEST rows, never deferred reservations.
- `player_trade_requests`: `request_id UUID PRIMARY KEY, decision_id UUID UNIQUE REFERENCES player_trade_request_decisions(decision_id), universe_id TEXT, persona_type VARCHAR(32) CHECK(persona_type='player'), persona_id VARCHAR(64), team_id INTEGER REFERENCES teams(team_id), opened_season INTEGER, revision INTEGER CHECK(revision>=0), status TEXT` (checked enum above), `state JSONB`. Composite FK to core profiles; validate matching universe through locked core read. Partial unique universe/persona where ACTIVE. Verify actual teams PK name before migration. Do not require a temporary roster row to live forever.
- `player_trade_request_actions`: universe/persona, `event_id TEXT, source_event_id TEXT, request_id UUID NULL REFERENCES player_trade_requests(request_id), season INTEGER, window_id TEXT, checkpoint_id TEXT, action_kind TEXT, payload_hash TEXT, before_state JSONB, after_state JSONB, receipt JSONB`; PK universe/persona/event_id; unique universe/persona/source_event_id/action_kind; unique checkpoint key in §2.3. Includes initial and deferred/no-op review receipts. Source incident IDs are references, not action IDs. Columns mandatory except stated nullable fields and before/after states for absent episodes.

Store APIs proposed: `read_active(cur, universe_id, persona_id)`, `read_annual(cur, universe_id, persona_id, season)`, `record_decision(cur, context, clock, policy, *, mode)`, `apply_action(cur, request_id, action, context, clock, policy)`, `read_receipt(cur, universe_id, persona_id, event_id)`.

Caller owns non-autocommit transaction. Helpers use savepoint and per-universe/persona advisory lock, verify binding/revision and append audit/update state atomically. Never connect/commit/seed/migrate. Shadow persists nowhere in DB. Enabled writes require an explicitly approved policy and caller activation context; no live caller is delivered. Canonical payload hash includes all inputs, revisions and policy. Look up replay before fresh revision validation; exact duplicate returns original result; conflicting reuse fails. Parallel annual claims serialize and unique constraints provide final defense.

Migration verifies prerequisites, creates empty feature tables/indexes transactionally and checks schema compatibility on repeat rather than ignoring wrong existing tables. No backfill or historical request generation. Scratch tests prove repeat migration, concurrency and forced rollback after each write.

Rollback: disable feature and future scheduling; retain/export audit. Never reverse real trades/contracts or regenerate profiles. No automatic production down migration. Optional table removal requires reviewed backup and no consumers, in reverse FK order. Scratch teardown touches only its verified isolated schema. Franchise rewind restores matching identity/events/request ledgers from one checkpoint; mixed revisions block evaluation. Public factual errors require explicit correction, not silent deletion.

### 2.7 Offline CLI

`python -m tools.player_trade_request_preview --input PATH --out DIR [--mode disabled|shadow]` accepts schema-v1 synthetic ordered contexts/clocks/actions and explicit policy. No DSN, implicit DB environment use or save path; reject enabled mode. Outputs deterministic `report.json` and `README.md`: neutral/personality matched decisions, eligibility/deferred causes, draw/annual use, reconciliation, public projection and incident contribution trace. Fixtures span 2026–2034 and later carryover. Stable sort and canonical input hashes make identical runs byte-identical. Reports are synthetic demonstrations, not Madden outcomes or calibration evidence.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | The shared role/promise/treatment contract is approved in the relationships plan but not built. | Prebuild interface review with relationship/reaction owner and complete/missing/disputed fixture examples. | Build pure offline policy only; no local inference or duplicate relationship ledger. |
| R2 | Annual opportunity misses a serious later incident after NO_REQUEST. | Q1 plus multi-window synthetic cases. | Keep explicit v1 limit; any exception requires versioned reviewed budget, never weekly retries. |
| R3 | Madden's ALLUSER flag gets mistaken for JR's authority. | Fixtures read authority from `user_teams` across 2026/2034/manual stages. | Unknown authority blocks responses/advice automation. |
| R4 | Trial odds/trait mapping/duration produce arbitrary volume. | Fixed-draw sensitivity grid; report eligibility denominator separately from requests by origin/window/trait. | No invented target rate; JR selects production policy before activation. |
| R5 | Consumers amplify one incident or feed request back into treatment. | Shared contribution-key review and synthetic cross-consumer test. | No request-as-incident; incident modifiers blocked without deduplication. |
| R6 | Concurrent migrations/core/interfaces drift. | Recheck Git/files and pin accepted contracts before dispatch. | Coordinator reconciles; never stage unrelated work automatically. |

**Spike first:** Coordinator interface/source review, estimated half a day before wave 1: clock semantics, incident grouping/repair, verified identity aliases, actual team PK and authority/contribution IDs. Record agreed example payloads here at review. No DB access needed. Failure blocks activation, not permission to duplicate producer state.

## 4. Tests (synthetic, no live DB)

- `tests/test_player_trade_requests.py`: role/breach/treatment duration, injury/bye/gap exclusions, disputed/unknown inputs, neutral event baseline and scoped trait rules; seed test vectors, no stacking, errors visible, annual/window/team deduplication, carryover, disabled no effects. Lifecycle covers one-game repair versus persistent repair, new promise insufficient, refusal not withdrawal, approved/rejected/expired deal not executed trade, employment closure, corrections/replay, privacy/stale public facts and ignored inbox not treatment.
- `tests/test_player_trade_request_store.py`: isolated synthetic schema; repeat/incompatible migration, annual/window uniqueness, concurrent claims, conflicting replay, historical receipts after later revisions, forced rollback after each write, caller transaction retained, role/universe/binding isolation, mixed rewind rejection. Opt-in `mu_scratch_*` DSN only, never automatic fixture provisioning from franchise DB.
- `tests/test_player_trade_request_preview.py`: byte-identical reports, disabled neutral equality, shadow without external writes, malformed/out-of-order inputs, 2026–2034/ALLUSER authority examples. Same incident presented through role/breach aliases counts once. Contract stub receives relationship + request + trait context and charges that incident once; request layer adds zero utility penalty. Clearly label this a handoff contract test, not production contract integration.

Run `.venv/bin/python -m pytest -m 'not integration' tests/test_player_trade_requests.py tests/test_player_trade_request_preview.py -q`. Store verification uses explicit scratch fixture environment and isolated schema compatible with `tests/conftest.py`; U2 documents exact environment invocation. No live/archive access, model calls, save writes or napper suite. This plan claims no tests have run.

## 5. Work breakdown

Shared coordinator files, never unit-owned: `PROJECT_SPEC.md`, this plan, `CLAUDE.md`. Parent owns consolidated personality/spec references. Recheck new paths against concurrent ownership before dispatch.

### U1: Pure policy and lifecycle
- **Owns:** `engines/player_trade_requests.py` (new), `tests/test_player_trade_requests.py` (new).
- **Reads:** core/README, generated-effects design, this plan and reconciled shared contracts.
- **Depends on:** none after interface spike.
- **Done when:** §2 types/functions cover qualification, lifecycle, control/privacy and deterministic annual behavior; production coefficients absent.
- **Verify:** `.venv/bin/python -m pytest -m 'not integration' tests/test_player_trade_requests.py -q` passes.

### U2: Persistence and migration
- **Owns:** `db/migrations/2027_01_25_player_trade_requests.sql` (new; collision check), `engines/player_trade_request_store.py` (new), `tests/test_player_trade_request_store.py` (new).
- **Reads:** U1, core store/migration/tests and test firewall.
- **Depends on:** U1.
- **Done when:** atomic replay/revision/annual guarantees and isolated migration/concurrency/rollback cases pass; no live caller added.
- **Verify:** `.venv/bin/python -m pytest -m 'not integration' tests/test_player_trade_request_store.py -q` with explicit scratch fixture; skipped store tests do not satisfy acceptance.

### U3: Offline harness and handoff guide
- **Owns:** `tools/player_trade_request_preview.py` (new), `tests/test_player_trade_request_preview.py` (new), `docs/reference/PLAYER_TRADE_REQUESTS.md` (new).
- **Reads:** U1, reconciled shared/contract interfaces and existing trade boundary code.
- **Depends on:** U1.
- **Done when:** reproducible matched reports and deduplication/control examples work; guide states deferred live integration accurately.
- **Verify:** `.venv/bin/python -m pytest -m 'not integration' tests/test_player_trade_request_preview.py -q`; `.venv/bin/python -m tools.player_trade_request_preview --help` succeeds.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 0 | Coordinator spike | JR approval (done 2026-11-03) | Fix producer/control contracts before build. |
| 1 | U1 | Wave 0 | Coherent pure policy/lifecycle. |
| 2 | U2, U3 | U1 | Disjoint persistence/harness files, both consume U1. |

**File ownership check:**

| File | Unit |
|---|---|
| `db/migrations/2027_01_25_player_trade_requests.sql` | U2 |
| `docs/reference/PLAYER_TRADE_REQUESTS.md` | U3 |
| `engines/player_trade_request_store.py` | U2 |
| `engines/player_trade_requests.py` | U1 |
| `tests/test_player_trade_request_preview.py` | U3 |
| `tests/test_player_trade_request_store.py` | U2 |
| `tests/test_player_trade_requests.py` | U1 |
| `tools/player_trade_request_preview.py` | U3 |

Every path appears once. No unit owns relationship/core/contract/GM shared files.

**Gates:** G0 JR approval (done 2026-11-03) + interface reconciliation; G1 disabled exact neutral control and pure tests; G2 persistence/replay/repair/privacy tests pass; G3 JR reviews sensitivity and chooses production policy; G4 separately scoped identity/producer/scheduler/control/contract integration proves neutral controls and incident deduplication (the scheduler and GM handoff are the [activation plan](../done/PERSONALITY_ACTIVATION_PLAN.md)'s U3); G5 explicit migration/activation authorization for a selected franchise/checkpoint. U1–U3 do not deliver G4/G5. No live integration or migration application is in these waves.

## 7. Spec changes when built

Parent coordinator consolidates these; this planning chat edits none:

- PROJECT_SPEC §7.11: scoped request consumer, neutral event baseline, annual draw, lifecycle, no auto-seeding/activation.
- §2.17: optional request signal versus GM-owned prices/search/legality/rights/execution; mark integration deferred until actually built. Remove grudge discount interpretation.
- §2.17a/b/d: context-only contract handoff and shared incident deduplication; no active extension refusal claimed.
- §3 schema: new subsection assigned by coordinator; §3.3o currently describes League Memory, so do not overwrite it. Document feature tables and actual migration status.
- Repository Layout: new modules, migration, preview, tests/reference guide and proposal entry. No API/frontend endpoint in v1.
- PERSONALITY_PLAN §4.6–4.8, §5.2, §6 and generated-effects design §3/§5: producer ownership, simulated events for every player, no grudge discount, annual/repair/privacy boundaries and deferred consequences. Consolidate 2026–2033/2034 chronology. Replace illustrative odds only after policy review.
- The cap-relative plan is built; its SQL constraint migration (`2027_01_17_persona_slider_band.sql`) exists. This feature never edits that work.

## 8. Decisions for JR

JR approved all four recommendations on 2026-11-03.

| # | Question | Decision (JR, 2026-11-03) |
|---|---|---|
| Q1 | Keep one first-qualified annual draw even if another serious incident occurs after NO_REQUEST? | Yes for v1: four eligible games of lost role, two observations after breach/treatment, two for repair; no immediate severe-incident exception. Review odds/durations offline before activation. |
| Q2 | Begin privately, with reporting only after verified disclosure? | Yes. Authorized front-office facts for JR; writers get dated, explicitly disclosed facts only. No automatic leak probability. |
| Q3 | Simulate player decisions and show factual requests on JR teams when suggestions are off? | Yes. Suggestions off suppresses GM advice/proposals; JR controls response and transactions. JR's teams come from `user_teams`, not Madden's ALLUSER flag. |
| Q4 | Include numeric extension unwillingness or GM search activation in this first build? | No. Deliver request/reconciliation state and offline evidence first; owning policies integrate later with incident deduplication and separate activation review. |

**Review boundary:** plan approval sets no production policy and applies no migration; those are gates G3–G5.

## 9. Build log

Units built one at a time by the orchestrator in their own worktrees (no subagents); PROJECT_SPEC.md was updated once, after wave 2.

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | build/tr-u1 | .claude/worktrees/tr-u1 | merged (53 tests) | 42fa0d0 |
| U2 | 2 | build/tr-u2 | .claude/worktrees/tr-u2 | merged (16 + 4 scratch skipped) | f86b765 |
| U3 | 2 | build/tr-u3 | .claude/worktrees/tr-u3 | merged (16; one test fix re-merged) | a685038, e08c6f6 |
