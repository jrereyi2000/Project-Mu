# Project Mu: Relationships and Reactions Plan

**Status:** approved by JR (2026-11-03): Q2 settled by his cross-plan decisions, Q1, Q3 and Q4 as recommended (§8); built and on main (2026-11-04), observe-only: nothing connected, migration `2027_01_27` not applied, no numeric policy. Production migration, intake and enabling numeric trust each still need JR's separate go-ahead (§6 gates G2–G4).
- **As built (wave 0, spike):** answered offline from source, no database: the producers' wave 1 (`engines/relationship_producers/`: 32 ticks a season, TeamRef `team:<teams.id>`, `db-player` / `db-coach` / `db-coordinator` aliases, corrections with `supersedes_source_event_id`) and the inbox's U1 outbox (`engines/inbox/appraisal.py`) already emit §2.2 envelopes; the intake design stood unchanged. Units were built one at a time by the orchestrator in their own worktrees (no subagents), and PROJECT_SPEC.md was updated once, after wave 3.
- **As built (U1):** envelope fields are exact (plus an optional store-assigned `head`, excluded from the hash with `event_id`); extra payload keys are kept (producer `rule`, inbox `appraisal_reason`); a correction's `target` may be null and its `replacement_event` may omit `event_id`; `source_refs` may be empty. `select_active(events, *, as_of, known_through)` (correction chains, cycles, competing corrections, cutoffs, promise/repair links) and the interval/calendar helpers are U1's shared helpers. A breach is a negative/mixed `dismissal`/`disrespect` treatment or a `broken` promise. The core has no `coordinator` role, so U1 rejects the producers' `coordinator_alias` envelopes: `tests/test_relationship_producer_rules.py::CoachFactTest::test_hired_and_promoted` now fails on main until producers wave 2 fixes `rules.py` (the known bug its plan records).
- **As built (U2):** policy shape `{mode, rule_version, trust, attachment, reactions}`; a trust section is all-or-nothing over `CONTRIBUTION_CODES` (interaction × outcome × significance, treatment kind × outcome, promise kept/broken), `cap`, `positive_half_life_ticks`, `breach_floor` {material, minor}, `repair_step` per action; attachment tiers `{min_tenure_ticks, min_affirmative, min_connections}`. Only positive contributions fade. A breach floor caps trust while the breach is open. A broken promise is attributed to its issuer (inbox `promise_resolved` envelopes carry no `attribution.responsible`). Repair steps count only accepted repairs; a minor breach also closes on an accepted acknowledgment. Tie relevance uses the tie's own tenure windows when the caller passes them.
- **As built (U3):** `validate_evolution_candidate` takes an extra `slider_band=None` keyword, because a pure function cannot know whether the caller's database has `2027_01_17`. Without a `reactions` policy section, sustained role frustration needs two contiguous unsatisfied windows (the definitional minimum of "not one scan"). Only `eager`/`uncertain` count as contradictory, so a celebration stays secondary to a breach.
- **As built (U4):** invalid envelopes and unknown identities are excluded into `receipt.rejected` (the shape the inbox's `flush` reads), while facet, transport and source-ref conflicts refuse the whole batch; a batch with nothing new writes nothing. The four `RELATIONSHIPS_TEST_DSN` scratch cases (repeat migration, append/replay/conflict/cutoffs, mid-batch rollback, incompatible table, concurrency) were not run: no PostgreSQL in the build environment, so persistence acceptance (G1) is still outstanding. Contract notes for consumers: the producers' `append_or_hold` counts every resolved envelope as appended and ignores `receipt.rejected`; the inbox's `flush` defaults `team_registry` to `{}`, so its `treatment` events are rejected (`unknown_team`) until a production registry is passed; `tests/test_inbox_appraisal.py::test_flush_is_a_noop_without_the_relationships_store` assumes the module is absent and now fails (owner: the inbox plan).
- **As built (U5):** optional `sensitivity: [{name, policy}]` input; `--stress` is a separate mode. Measured once (2026-11-04, WSL, tracemalloc on): 32 teams × 9 seasons × 53 players = 30,528 events, 14.0 s, ~153 MB peak for one subject's full validated replay (R5: measurement only).
**Builds on:** PROJECT_SPEC §3.3p, §7.11; [Personality plan](../in_progress/PERSONALITY_PLAN.md) §4.6–4.8 and §5.2; [Generated effects design](../in_progress/PERSONALITY_GENERATED_EFFECTS_DESIGN.md); `personality/README.md`.

**Goal:** Preserve how people experience other people and organizations across the 2026–2033 CPU simulation and JR's 2034 takeover. Give consumers explainable directional trust, earned attachment and transient reactions from recorded events. Core retains identity and profile mutation authority; each feature retains its decisions and formulas.

**Delivers (proposed, not existing):** `personality/relationship_events.py`, `relationships.py`, `reactions.py`, `relationship_store.py`, `relationship_preview.py`; an additive feature migration; focused offline tests. No live ingestion or consumer activation.

The parent coordinates spec edits. The survey used no database access, migrations, seeds or effects. Canonical source is `/home/jrereyi/Project-Mu`.

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `personality/core.py:39` defines universe/role/birth-key `Identity`; `:144` defines permission-filtered `feature_view`, including missing-neutral behavior. | Reuse identity and scoped `relationships`/`reactions` views. No new generator or name/team identity. |
| S2 | `personality/store.py:35` reads profiles; `:119` binds aliases; `:136` resolves them; `:145` applies changes. | Producers resolve verified identities. Feature reads never initialize or reseed profiles. |
| S3 | `personality/core.py:166` validates explicit annual changes; `db/migrations/2027_01_16_personality_core.sql:30–42` defines mutation receipts and annual/source uniqueness. | Frequent reactions need a separate event ledger. Trust updates do not consume an annual profile change. |
| S4 | `db/migrations/2027_01_09_personas.sql:52–64` has ordered `persona_type/persona_id → target_type/target_id`, numeric trust -1..1, note, tags and ordered-pair uniqueness. | A→B is independent of B→A. Existing notes lack canonical events, coverage and provenance; do not adopt them automatically. |
| S5 | `newsroom/personas.py:301–358` catches read failures and ultimately calls `seed_persona`; `:261` generates empty relationships. | Never use `get_persona` in this feature. Missing history is unknown; corruption/schema errors propagate. |
| S6 | `docs/in_progress/PERSONALITY_PLAN.md:97–114` proposes mood at prompt time and interaction-based trust. `newsroom/personas.py:734`, `:790` render persona context and assemble signals/recent work, not this event service. | Mood remains computed context, not identity. No prompt wiring is included. Team tags do not prove private feelings. |
| S7 | `engines/form_engine.py:5`, `:221–252` separates form overlays from neutral Madden morale. | No ratings, form or ConfidenceRating writes. Madden morale remains 50 under its owner. |
| S8 | `db/migrations/2027_01_09_personas.sql:21` enforces exactly 30 with `chk_persona_sliders_budget`; `newsroom/personas.py:159–177` does too. | Superseded 2026-11-02: the cap-relative plan corrected its S10 and built the 24–36 band in Python and SQL (`2027_01_17_persona_slider_band.sql`, applied to `madden_test`). This plan does not own that change. |
| S9 | `engines/transactions_engine.py:291`, `:298`, `:426`, `:509` distinguish moves, offers and accepted decisions; `db/migrations/2027_01_14_v2_franchise_os.sql:66–89` defines pending moves and office runs. | Proposed or accepted transactions alone do not establish verified applied treatment. Source owners must attest completed outcomes. |
| S10 | `tests/conftest.py:1–13` documents DB isolation/provisioning; `personality/README.md` documents opt-in isolated core tests and no production migration. | Pure fixtures and manually provisioned empty scratch schemas only. Never provision tests by cloning live/archive DBs. |
| S11 | `PROJECT_SPEC.md:1206` describes different relationship columns, warmth and -100..100 trust; actual SQL is S4. `personality/core.py:15` omits the spec's unsupported media role. | Parent corrects these discrepancies during consolidated spec work. No core role extension here. |
| S12 | The [cap-relative plan](../done/CAP_RELATIVE_CONTRACTS_PLAN.md) (built and on main 2026-11-02) delivered earnings/security (`engines/career_earnings.py`) and the slider band, separately owned. No other personality plan file was present in that folder at survey. | Build against the implemented earnings API (PROJECT_SPEC §2.17e); do not duplicate pricing/earnings/band work. |
| S13 | `db/migrations/2027_01_18_writer_voice.sql` and `newsroom/relationships.py` (AGENT_WIRE_VOICE U1, built 2026-11-03) add `persona_relationships.evidence` and seed, grow and decay writer biases toward people in the legacy table; `tests/test_relationships.py` tests them. | `2027_01_18` and that test path are taken: this plan's migration is `2027_01_27` and its trust tests are `tests/test_personality_relationships.py`. Writer biases stay the newsroom's legacy store (as the inbox plan's S10 treats them); this plan neither adopts nor dual-writes them (Q4). |

Read AGENTS.md/CLAUDE.md, relevant spec sections, personality README/core/store/core migration, both required personality designs, proposal skill/template, and completed `docs/done/team_management/06_DRAFT_ROUND_TRADES_PLAN.md`. Survey is source-only; no claim about live schema or deployed profiles. Concurrent dirty progression, dossier, images, scripts, JSON and PRE_PC_WORK_2 movement remain untouched.

## 0b. Consumer decisions (JR, 2026-11-03)

Settled while reviewing the V2 plans; they bind this plan's design:
- **This plan owns trust.** The V2.9 inbox and V2.2 press conferences record events here instead of writing `persona_relationships`: a conversation exchange or a presser answer is a `meaningful_interaction` (outcome and significance from the listening agent's own appraisal, with its reason in the payload); being skipped, cut off or stood up by a declined presser is a `treatment`. The agent's appraisal is evidence; this plan's reviewed policy turns events into trust. The bounds those plans proposed (inbox 0.03 per exchange, 0.06 per promise; press ±0.03 per answer, ±0.08 per presser) are starting points for that policy, not separate calculations.
- **Promises** made in inbox role talks use `promise_made` / `promise_resolved` as defined in §2.2 (issuer, beneficiary, measurable criterion such as a depth-chart slot by a week, due time; resolution read from the actual depth chart).
- **Trust is one-way.** No consumer reads a relationship as mutual or adopts legacy tags automatically. A coach has his own views of reporters, players and his GM once coaches have profiles.
- **Every persona may hold appraisals** (Q2, personality plan D13). Personas named after real people (writers, GMs, coaches, players) are fictional characters inspired by them. Existing players get generated profiles at the 2026 start and prospects at class entry (D12); real coaches get reviewed profiles from a separate workstream (D11) and have views of their own once those exist.
- Migration reserved as `2027_01_27_personality_relationships.sql` (personality plan §5.3; `2027_01_18` went to the writer-voice migration, S13).

## 1. Scope

**In:** canonical deduped interactions; directional trust; tenure/role coverage; meaningful ties; promises and organizational treatment; uncertainty, attribution, decay/repair; transient mood; explanations/previews; persistence through seasons; separate reviewed evolution candidates.

**Out:** contract utility/prices/security, trade requests or execution, retirement, ratings/form, save writes, newsroom publication, production ingestion/scheduling (built by [`RELATIONSHIP_EVENT_PRODUCERS_PLAN.md`](RELATIONSHIP_EVENT_PRODUCERS_PLAN.md)), profile seeding, researching real people (the coach-profile workstream owns reviewed coach profiles), legacy adoption, hidden work ethic/coachability axes. Hometown is familiarity, never earned loyalty. Quiet is not low confidence; money preference is not low effort; elite OVR is not financial security. No global league event bus: preserve external canonical IDs in a feature-specific intake.

## 2. Design

### 2.1 Proposed contracts and functions

All names here are new proposals. Freeze `RELATIONSHIP_SCHEMA_VERSION='relationships-v1'` after review. `TimePoint={season:int,tick:int}` uses verified simulation time, never wall time. Calendar input maps each crossed season to its total ticks; missing calendar blocks decay, not history reads. Audit timestamps use UTC only.

`PersonRef={kind:'person',universe_id,role,persona_id}` resolves to a core Identity. `TeamRef={kind:'team',universe_id,team_key}` uses a verified durable franchise key, not city/abbreviation. Relocation preserves the key. Teams are not fake core people. Subject is a person; target may be a person or team. Reject cross-universe, self-person and unsupported-role edges.

```python
# relationship_events.py
validate_event(event: dict, *, team_registry: dict) -> dict
canonical_payload_hash(event: dict) -> str
validate_event_batch(events: list[dict], *, calendar: dict, team_registry: dict) -> list[dict]
# relationships.py
validate_policy(policy: dict) -> dict
project_relationships(events: list[dict], *, subject: dict, as_of: dict,
                      known_through: int, policy: dict, calendar: dict) -> dict
relationship_context(state: dict, *, target: dict | None = None) -> dict
# reactions.py
project_reactions(events: list[dict], relationship_state: dict, *, feature_view: dict,
                  as_of: dict, known_through: int, policy: dict, calendar: dict) -> dict
select_mood(reactions: list[dict], *, as_of: dict, policy: dict) -> dict
validate_evolution_candidate(candidate: dict, *, profile: dict, source_events: list[dict]) -> dict
# relationship_store.py
append_events(cur, events: list[dict], *, expected_head: int, team_registry: dict) -> dict
read_events(cur, *, subject: dict, as_of: dict, known_through: int | None = None) -> list[dict]
read_head(cur, universe_id: str) -> int
# relationship_preview.py
preview(document: dict) -> dict
```

`known_through` is an ingestion sequence, independent of effective simulation time. Historical views fix both cutoffs. Missing profile goes through `feature_view(None, feature)`; invalid profiles/errors fail visibly. Policy has explicit `mode='off'|'observe'|'enabled'`, set per franchise by the [activation plan](../in_progress/PERSONALITY_ACTIVATION_PLAN.md)'s switchboard (`relationships`: off, shadow = observe, active = enabled). Off returns neutral consumer context. Observe computes private audit but returns the same neutral consumer context. Enabled returns reviewed context. Profile availability never activates a feature.

### 2.2 Canonical intake and attribution

Envelope fields:

```
{schema_version,universe_id,event_id,source_event_id,source_kind,
 source_refs:[{namespace,key}],kind,occurred_at:{season,tick},
 subject:PersonRef,target:PersonRef|TeamRef,actor:PersonRef|TeamRef|null,
 basis:'verified_simulation'|'reviewed_evidence',confidence:'low'|'medium'|'high',
 reviewer:str|null,evidence_scope:[str],visibility:'private'|'public',
 attribution:{responsible:[Ref],reason_code,evidence_refs:[str]},
 payload:dict,supersedes_source_event_id:str|null}
```

Producer owns stable `source_event_id` for ONE occurrence. Roster scans, office receipts and article references must share it; never guess equivalence from names/dates. Multiple affected people use explicit facets. Dedupe key is `(universe_id,source_event_id,subject_persona_id,target_key,kind)`. A reaction and attachment explanation may cite the same source without counting it twice. Transport `event_id` is separately checked for content conflicts. Hash excludes transport ID/recorded timestamp but includes every semantic field.

| Kind | Required payload |
|---|---|
| `tenure_window` | `team_key,start,end,coverage:'complete'|'partial'` |
| `role_window` | `team_key,start,end,agreed_role:str|null,observed_role,assessment:'satisfied'|'unsatisfied'|'unknown',assessment_basis_refs` |
| `meaningful_interaction` | `relationship_kind:'mentorship'|'support'|'conflict'|'collaboration',outcome:'positive'|'negative'|'mixed',significance:'routine'|'meaningful',opportunity_ref` |
| `promise_made` | `promise_id,issuer,beneficiary,terms:{kind:'role'|'opportunity'|'communication',description,criterion:dict},due_at,authority_ref` |
| `promise_resolved` | `promise_id,resolution:'kept'|'broken'|'waived'|'renegotiated'|'unknown',criterion_result,outcome_refs,beneficiary_ack_ref:str|null` |
| `treatment` | `team_key,treatment_kind:'support'|'dismissal'|'disrespect'|'consultation'|'recognition',outcome,significance,outcome_refs` |
| `repair` | `breach_source_ids,action:'acknowledgment'|'restitution'|'commitment_met'|'reconciliation',outcome_refs,accepted_by_subject:bool|null` |
| `situation` | `situation_kind:'arrival'|'departure'|'award'|'team_result'|'injury'|'job_pressure',state:'positive'|'negative'|'uncertain'|'resolved',valid_until,outcome_refs` |
| `home_familiarity` | `region_key,team_key,match:'verified'|'unknown',geography_refs` |
| `correction` | `reason,replacement_event:dict|null`; supersedes source required; null retracts |

Intervals are half-open and overlap is unioned. Promise authority, measurable terms and due time are mandatory. Missing outcome coverage means unknown, not broken. Renegotiation/waiver requires beneficiary acknowledgment. A proposal, ordinary competition, injury restriction, trade or rejected contract does not prove misconduct. Attribute responsibility only to explicitly evidenced actor/team, never all teammates or an automatically blamed GM. Co-membership is opportunity, not friendship.

Corrections append; reject cycles, conflicting active replacements and cross-universe links. Select active facts at knowledge cutoff, then order by season/tick, kind order, source ID and facet key. Kind order: promise made; windows/interactions/treatment/situation/home; promise resolved; repair. Repair must reference an existing breach. Corrections replay from earliest affected tick; earlier knowledge cutoffs retain original facts. Contradictory/incomplete evidence is excluded with reason rather than converted to adverse certainty.

### 2.3 Trust and earned attachment

Keep directional person ties and organizational attachment separate. Person relationships survive moves; relevance to current team requires verified current context. Departure preserves old-team history and starts new-team unknown history. No friend-count bonus, indiscriminate teammate trust or permanent loyalty.

Edge output:

```
{subject,target,status:'known'|'unknown'|'conflicted',trust:float|null,
 confidence,coverage:{start,end,gaps:[]},
 meaningful_ties:[{person,kind,source_ids,relevant_to_team:bool}],
 unresolved_breaches:[{source_id,promise_id:null|str,severity,repair_state}],
 attachment:{tier:'none'|'weak'|'established'|'strong'|'unknown',eligible:bool,
             dimensions:dict,blocking_reasons:[str]},
 hometown:{status:'verified'|'unknown',region_key:null|str,source_ids:[]},
 explanation:[{code,source_ids,before,after,excluded_reason:null|str}],
 rule_version,policy_hash,input_hash,as_of,known_through}
```

Trust scale is -1..1; known neutral is zero, unknown is null. Proposed reviewed numeric policy supplies event deltas, saturation, positive-memory half-life, breach floors, repair schedules, mood lifetimes and tier criteria. This plan supplies NO calibrated coefficients, durations or thresholds. Empty/default policy supports evidence-only observation and cannot enable numeric trust or positive attachment. `validate_policy` rejects missing enabled-rule values, nonfinite/out-of-range numbers, negative durations and unordered tier criteria.

Numeric contract when reviewed: one unique source contributes once per edge/domain through its strongest applicable rule; never both breach and duplicate treatment penalty. Trust is clamped sum of signed contributions times approved age weights, with each contribution/cap loss explained. Positive context may fade toward zero. Unresolved breach retains an approved floor until repair; elapsed time cannot restore trust. Repair adjusts the linked breach contribution, not an unlimited additive bonus; cumulative repair cannot restore beyond that breach's prior neutral contribution. Apology acknowledges; verified corrective action and acceptance close material breaches. Unknown acceptance keeps them open. Other positive experiences may coexist.

Attachment is a conjunction of evidence dimensions, not a sum. Every positive tier requires covered tenure plus affirmative role/treatment evidence. Established/strong also require meaningful connection evidence and stronger reviewed criteria. Strong requires all strong criteria. An unresolved material organizational breach blocks established/strong despite long tenure. Missing required dimensions yields unknown/ineligible, not weak by default. Complete history without qualifying attachment yields none. Repeated support may establish significance but never multiplies by number of friends. `LOYAL` cannot create history. Hometown familiarity cannot satisfy any attachment criterion. Consumers decide whether a verified tier matters to a specific decision.

Every persona, real-inspired or not, accumulates verified simulation facts and may form private appraisal from them (resentment, satisfaction, forgiveness, consequential attachment): a player named after a real person is a fictional character with a generated profile, and his appraisal works like anyone else's. Unknown origin (an unresolved identity) stays neutral until core classification. A reviewed real-coach profile may supply a narrow starting tie with `basis='reviewed_evidence'`, never invented past simulation events. Appraisal still needs recorded context; traits alone are insufficient.

### 2.4 Reactions and mood

Reaction shape: `{reaction_id,subject,target,source_ids,domain,tag,started_at,expires_at,priority,confidence,visibility,explanation,rule_version}`. ID hashes sources/subject/domain/rule version. Compute from active facts; do not write mood to core or generate events on reads. Sustained dissatisfaction requires covered window and explicit appraisal, not one depth-chart scan. Expiry removes current reaction, not history.

Recommended priority: serious unresolved treatment/promise; sustained role frustration; current job/injury uncertainty; transition; achievement/team result; neutral. One primary plus at most two noncontradictory secondary tags. Within priority sort approved severity, recency, then source ID. Same-domain conflicting reactions become mixed; explain suppressed candidates. Celebration can remain secondary without erasing breach. Labels: `neutral`, `frustrated`, `uncertain`, `encouraged`, `eager`, `mixed`. None is the Confidence slider.

Team loss/injury does not prove resentment/anxiety/determination. First increment uses evidence-only appraisals; personality-dependent intensity/lifetime needs an approved policy and scoped view. No request odds, sit-outs, ratings, work-ethic effects or automatic trait changes. Public writer context contains public facts plus the fictional interior context its `visibility` allows. Private simulation state belongs to the fictional character: it never becomes a sourced quote, and never a claim about the real person he was inspired by. Full audit is JR-only. Target relevance filters relationship context.

### 2.5 Durable evolution candidates

`validate_evolution_candidate` accepts explicit core candidate fields (`event_id,source_event_id,season,expected_revision,rule_version,reason,kind` plus trait add/remove or slider/step), `review_status:'pending'|'approved'`, reviewer, feature_scope and supporting_source_ids. Return `{status:'review_required'|'ready'|'blocked',candidate,reasons,support}`. Check subject, cutoff, active sources, nonduplicate support (the core extensions plan removes the special simulation-evidence rule for `real` profiles). No automatic event→trait inference or core mutation call ships here.

Ready candidates go to the [personality evolution plan](PERSONALITY_EVOLUTION_PLAN.md)'s arbiter (`source 'RELATIONSHIPS'`), which submits the season's one chosen change to `store.change`; core annual/revision/conflict checks remain final. Review may choose no change. Trust/repair never uses the annual slot. Single-slider candidate reports `blocked_slider_band` only on a database that lacks `2027_01_17_persona_slider_band.sql` (Python and SQL both carry the band since 2026-11-02). Trait candidates can be reviewed under current budget. Keep ephemeral mood outside profile revision and store.

### 2.6 Persistence, idempotency and rollback

Migration `db/migrations/2027_01_27_personality_relationships.sql` (reserved in PERSONALITY_PLAN §5.3; recheck at build). No core schema edits, legacy dual-write or `init_db.sql` changes. Add:

- `persona_relationship_batches`: `universe_id TEXT,head BIGINT,expected_head BIGINT,batch_hash TEXT,receipt JSONB,created_at TIMESTAMPTZ`; PK universe/head; unique universe/batch_hash. Serialized universe batch head starts at 1.
- `persona_relationship_events`: `universe_id TEXT,source_event_id TEXT,facet_key TEXT,event_id TEXT,head BIGINT,occurred_season INT,occurred_tick INT,schema_version TEXT,payload_hash TEXT,envelope JSONB,created_at TIMESTAMPTZ`; PK universe/source/facet; unique universe/event/facet; FK universe/head to batches. Facet hashes subject/target/kind. Checks nonnegative tick, positive head, supported schema/object envelope. Index universe/season/tick.
- `persona_relationship_sources`: `universe_id TEXT,namespace TEXT,external_key TEXT,source_event_id TEXT`; PK universe/namespace/external_key. Many refs can map to one occurrence; remapping conflicts. Application validates at least one event facet exists for every registered source in the atomic batch.

Append takes caller cursor, explicit trusted team registry, rejects autocommit, validates core identity/universe and team registry, uses savepoint plus advisory transaction lock `relationships:<universe>`, and never commits/connects. Exact prior batch replay returns original receipt before stale-head check; new work checks expected head. Same source/facet/hash is no-op; different hash or transport reuse conflicts. Mixed old/new facets get per-facet receipt. Batch registry and all inserts succeed atomically. Receipt stores IDs, source refs, hashes, registry hash and admissions/exclusions. Invalid caller JSON cannot establish a trusted team mapping; production registry delivery is an integration prerequisite.

Rule upgrades reproject facts without reappending events. Projection revision is `(head,policy_hash,as_of)`; no second mutable projection authority in v1. Full replay is initial implementation; cache only if measured necessary. Corrections retain original ledger and knowledge history. No yearly reset, including 2034.

Migration uses transactional DDL and validates repeat-application definitions; incompatible preexisting table fails rather than silent IF NOT EXISTS success. No backfill/legacy import. Scratch test applies prerequisite legacy/core DDL in an empty schema. Production migration is separately reviewed. Feature-off rollback disables consumers and retains audit; policy rollback replays old version. Destructive schema rollback requires verified export, zero consumers and separate maintenance approval, dropping only these feature tables in dependency order. No core/profile deletion.

### 2.7 Offline preview and consumer outputs

`python3 -m personality.relationship_preview --input fixture.json --out report.json`

Input: `{schema_version,subject,events,profile:null|dict,policy,calendar,as_of,known_through,team_registry,baseline_events:null|list,candidates:[]}`. Output: input/policy hashes, cutoffs, factual context, consumer context, private audit, reactions, primary/secondary mood, candidate validation and optional baseline diff. No DSN, seed, apply flag, LLM or clock inference. Atomically replace explicit output; deterministic JSON with no generated timestamp. Valid exit 0; invalid schema/JSON exit 2 with structured error.

Consumers use `relationship_context`: tier/eligibility, role coverage, relevant directional ties, attribution-specific breaches, home familiarity and reason/source IDs. No contract coefficients, request probabilities or career hazard. Contracts own aggregate preference cap; requests own frequency/annual dedupe; progression owns mentorship opportunity and must not reward the same event twice. These adapters are separate owners and gates, not delivered here.

## 3. Risks and unknowns

| # | Risk | How we find out | If unresolved |
|---|---|---|---|
| R1 | Incomplete canonical IDs, calendar, role coverage or verified outcome receipts. | Source-contract spike using code and synthetic exports. | Quarantine unsupported events; observe only. Never fabricate history. |
| R2 | Future event bus or concurrent plans conflict with proposed contracts. | Parent reconciles core/contracts/progression/request owners before interface freeze. | Keep external source IDs and revise adapter seam. |
| R3 | Trust/tier/decay criteria uncalibrated. | Fictional matched scenarios and parameter sensitivity reports reviewed by JR. | Evidence-only output; no enabled numeric policy. |
| R4 | Correction, relocation and concurrent ingestion misattribute treatment. | Scratch conflict/cutoff/lock tests and universe fixtures. | Atomic refusal; reviewed append correction. |
| R5 | Full 2026–2034 replay expensive. | Synthetic 32-team multicohort benchmark reports time/memory/event count. | Separately design disposable checkpoints after profiling; no invented latency threshold. |
| R6 | Private appraisal leaks into public context, or a fictional appraisal reads as a claim about the real person. | Visibility fixtures and writer-context tests. | Private by default; public context only through `visibility`, framed as the fictional character's. |
| R7 | Repair farming, long tenure or duplicated support masks bad treatment. | Adversarial scenario matrix and contribution explanations. | Breach blocks tiers; cap repair and dedupe occurrence. |

**Spike first:** estimated half day before wave 1, implementer performs source-only review of actual applied-transaction receipts, role-window coverage, franchise identity and calendar; drafts synthetic trade, promise/repair and prospect→player envelopes. No DB access. Parent reconciles concurrent contracts. Revise intake if trustworthy mapping/time is unavailable. Numeric activation separately requires JR's policy/scenario review; spikes do not calibrate coefficients by assertion.

## 4. Tests (synthetic, offline)

- `tests/test_relationship_events.py`: schema/time validation, source facets and alias conflicts, cross-universe/self refs, relocation, correction cycles/order, authority/deadline/unknown outcome, overlapping intervals/calendar gaps.
- `tests/test_personality_relationships.py`: asymmetric trust, unknown versus zero, interval union, no friend count, moved mentor relevance, tier conjunction, hometown-only ineligible, long-tenure breach, bounded repair, no automatic forgiveness, policy saturation/decay and complete explanations.
- `tests/test_reactions.py`: breach outranks celebration with secondary preserved, mixed/expiry/ties, no loss/injury stereotype, real-inspired generated players and reviewed coaches appraise like any persona, no core mutation, candidate retracted support and blocked slider band, off/observe neutral equality.
- `tests/test_relationship_store.py`: fake-cursor unit failures and opt-in empty scratch schemas for repeat migration, canonical/transport conflicts, replay after newer head, concurrency, mid-batch rollback, no commit, historical cutoffs and real core identity validation.
- `tests/test_relationship_preview.py`: deterministic CLI/diff/error exit, no DB/LLM/core writes, 2026 prospect→2034 continuity and aliases, synthetic stress/sensitivity reporting to caller-chosen output.

Run `python3 -m pytest -m "not integration" tests/test_relationship_events.py tests/test_personality_relationships.py tests/test_reactions.py tests/test_relationship_store.py tests/test_relationship_preview.py -q`. DB cases skip unless explicit `RELATIONSHIPS_TEST_DSN` targets manually provisioned empty `mu_scratch_relationships`, using isolated schema/rollback and firewall-compatible core test pattern. No automatic integration provisioning from live/archive DB, no napper wholesale. Skips alone do not satisfy persistence acceptance. No proposed tests have run: modules do not exist.

Neutral acceptance: off and observe return identical consumer context; profile hashes/revisions stay unchanged; no contracts/moves/ratings/form/morale outputs. Enabled cases change reviewed context only. Future consumers must independently demonstrate neutral equivalence and no duplicate rewards before their effects activate.

## 5. Work breakdown

Shared parent-only files: `PROJECT_SPEC.md`, this plan, `CLAUDE.md`. New sibling feature files require coordination with package-wide core ownership; no existing core files change.

### U1: Canonical event contract

- **Owns:** `personality/relationship_events.py`, `tests/test_relationship_events.py` (new).
- **Reads:** core README/identity, actual SQL, §2.1–2.2 and spike findings.
- **Depends on:** none; the source spike precedes coding.
- **Done when:** normalized envelope/facet/source/time/correction/registry contracts fixed; no connections.
- **Verify:** `python3 -m pytest -m "not integration" tests/test_relationship_events.py -q` passes.

### U2: Trust and attachment

- **Owns:** `personality/relationships.py`, `tests/test_personality_relationships.py` (new).
- **Reads:** U1, core views, §2.3 and earned-attachment design refinement.
- **Depends on:** U1.
- **Done when:** pure directional state explains unknown coverage, breaches, repair, tiers and hometown; incomplete policy cannot enable numbers.
- **Verify:** `python3 -m pytest -m "not integration" tests/test_personality_relationships.py -q` passes.

### U3: Reactions and candidate validation

- **Owns:** `personality/reactions.py`, `tests/test_reactions.py` (new).
- **Reads:** U1, frozen §2.3 state shape, §2.4–2.5, core annual constraints.
- **Depends on:** U1; builds against synthetic state, no U2 internal import.
- **Done when:** deterministic expiry/priorities/visibility and review validation work; no automatic profile mutation or game effects.
- **Verify:** `python3 -m pytest -m "not integration" tests/test_reactions.py -q` passes.

### U4: Persistence and additive migration

- **Owns:** `personality/relationship_store.py`, `db/migrations/2027_01_27_personality_relationships.sql`, `tests/test_relationship_store.py` (new; migration name rechecked).
- **Reads:** U1, core store/savepoints, actual prerequisite DDL, firewall, §2.6.
- **Depends on:** U1.
- **Done when:** atomic explicit-cursor append/read, original replay receipts and expected-head conflicts verified in empty scratch schema; no production runner.
- **Verify:** `python3 -m pytest -m "not integration" tests/test_relationship_store.py -q`, repeated with explicit empty scratch DSN; skipped DB cases are outstanding.

### U5: Offline preview and acceptance

- **Owns:** `personality/relationship_preview.py`, `tests/test_relationship_preview.py` (new).
- **Reads:** merged U1–U4, §2.7 and §4.
- **Depends on:** U2, U3, U4.
- **Done when:** standalone historical/baseline preview and sensitivity/stress output work; no adapters or activation.
- **Verify:** `python3 -m personality.relationship_preview --help` exits 0; §4 focused suite and scratch checks pass.

## 6. Waves and gates

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 0 | Source-contract spike | — | Confirm input reliability. The V2.9 inbox builds first and queues relationship events in its own outbox; that outbox drains into U4's store once it exists. |
| 1 | U1 | Wave 0 | Freeze shared contracts. |
| 2 | U2, U3, U4 | U1 | Exclusive files; U3 uses frozen state fixtures, not U2 code. |
| 3 | U5 | U2–U4 | Historical cross-module preview and neutral acceptance. |

Gates: G0 verified core identity/source contracts; G1 focused pure plus actual scratch persistence success; G2 JR scenario/numeric-policy review; G3 parent reconciles consumers and their neutral controls; G4 separately reviewed production migration/intake/activation. Schema/profile presence never activates effects. Initial delivery may stop at observe/preview. Missing pre-checkpoint history stays unknown; a late start never invents 2026–2033 feelings for 2034.

**File ownership check (sorted; no duplicates):**

| File | Unit |
|---|---|
| `db/migrations/2027_01_27_personality_relationships.sql` | U4 |
| `personality/reactions.py` | U3 |
| `personality/relationship_events.py` | U1 |
| `personality/relationship_preview.py` | U5 |
| `personality/relationship_store.py` | U4 |
| `personality/relationships.py` | U2 |
| `tests/test_reactions.py` | U3 |
| `tests/test_relationship_events.py` | U1 |
| `tests/test_relationship_preview.py` | U5 |
| `tests/test_personality_relationships.py` | U2 |
| `tests/test_relationship_store.py` | U4 |

## 7. Spec changes when built (parent coordinates)

- §1.1: new feature modules/tests/migration/plan layout; §1.2: offline preview command and scratch-only verification.
- §3.3p: correct legacy relationship columns/scale/direction and unsupported roles from SQL; document feature ledger/batch/source constraints, cutoffs and retention without replacing core/legacy tables.
- §7.11: canonical inputs, every persona (real-inspired included) may appraise, unknown coverage, trust/attachment/home distinctions, reaction versus evolution, candidate export and off/observe/enable gates.
- §6 workflow: record future verified-outcome adapter requirements as unconnected; scheduling is separate work.
- §2.21: reaffirm no mood→form/ratings/Madden morale writes, no new progression effect.
- Parent reconciles old personality Warmth→loyal wording and automatic injury/benching mood shortcuts; the cap-relative exact-30 survey and both band changes are done (2026-11-02). No shared files edited here.

## 8. Decisions for JR

Decided by JR on 2026-11-03.

| # | Design-changing question | Decision (JR, 2026-11-03) |
|---|---|---|
| Q1 | Evidence-only first increment, or an enabled numeric trust/decay/tier policy too? | Evidence-only observation first, with stable numeric seam and trial policies confined to offline fixtures. Review matched scenarios/sensitivity before adopting coefficients or tenure cutoffs. |
| Q2 | Should recorded events automatically establish private appraisal for real people? | Yes, for every persona (personality plan D13): real-inspired personas are fictional characters and appraise from recorded events like generated ones. Appraisal still needs recorded context, and private state stays private unless `visibility` allows it (§2.4). |
| Q3 | Can apology alone restore trust/attachment? | Record acknowledgment immediately, but require corrective action plus subject acceptance/reconciliation for material breach closure; unknown acceptance remains open. |
| Q4 | Replace/dual-write legacy relationship rows or keep feature history separate? | Separate additive ledger, no legacy dual-write. Future adapters target context API; legacy adoption needs explicit provenance review. |

Plan approval does not apply the migration, start production intake or enable numeric trust; those are gates G2–G4.

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | build/rel-u1 | .claude/worktrees/rel-u1 | merged | db0e122 |
| U2 | 2 | build/rel-u2 | .claude/worktrees/rel-u2 | merged | 2960f0a |
| U3 | 2 | build/rel-u3 | .claude/worktrees/rel-u3 | merged | f4911c7 |
| U4 | 2 | build/rel-u4 | .claude/worktrees/rel-u4 | merged | 743687e |
| U5 | 3 | build/rel-u5 | .claude/worktrees/rel-u5 | merged | bf34159 |
