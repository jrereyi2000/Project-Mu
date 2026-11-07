# Project Mu: Personality and Agents Plan

**Status (reconciled 2026-11-03):** phases 1–3 and the feature-independent core are built. Cap-relative pricing, career earnings and the 24–36 slider validation band were built on 2026-11-02. JR approved all 14 sub-plans in §5.3 on 2026-11-03 (the six original feature/lifecycle plans, the event producers, and seven plans that close the gaps found that day), each with its §8 decisions; planning is complete. Only the event producers' wave 1 is built; building the rest waits for JR's go-ahead, and migrations, production seeding and activation stay separate decisions in each plan. Core availability is separate from production ingestion, migration and feature activation. Player/coach personality effects remain inactive.
**Expands:** V2.13 of [`PC_MIGRATION_PLAN.md`](../done/PC_MIGRATION_PLAN.md). Feeds [`PROGRESSION_PLAN.md`](PROGRESSION_PLAN.md) and [`LEAGUE_OFFICE_PLAN.md`](../done/LEAGUE_OFFICE_PLAN.md).
**Implementation references:** [core API](../../personality/README.md), [generated effect design and offline examples](../reference/PERSONALITY_GENERATED_EFFECTS_DESIGN.md), [completed cap-relative work](../done/CAP_RELATIVE_CONTRACTS_PLAN.md). The [Panthers sample](../reference/PERSONALITY_PANTHERS_SAMPLE.md) is a limited historical evidence-method reference, not the current implementation priority.

One personality system for writers, GMs, players, coaches, owners and scouts. The core owns identity, initialization, provenance, validation and audited profile changes. Separate feature policies own what those profiles mean for contracts, development, coaching decisions, relationships, trade requests and retirement. Numeric decisions remain in their owning engines.
---

## 1. What an agent is

The language model has no memory or personality of its own; every call starts from nothing. An agent is:

1. **A record** in the database: who they are, what they believe, what they have done, who they know.
2. **A prompt built fresh each time** from that record plus the current data.
3. **A loop**: a trigger fires → the record and the relevant data are loaded → the model says or chooses something → the output is checked → what happened is written back to the record.

The personality lives in the database. The model is the voice that reads it.

**Agentic** means the loop also has **tools** and a **trigger**. The model is still prompted, by the program rather than a person: a scheduler or an event wakes the agent ("Week 7 started", "an offer arrived"), and it chooses its own steps from the tools it is given (look up a roster, price a deal, message a GM) until it is done or hits a step limit. The newsroom already works this way: each writer gets an assignment and the writer tools (`newsroom/db_tools.py`) and decides what to look up across dozens of calls. GMs don't yet; the trade engine decides everything and the model writes only the rationale.

**Ground rule (V2.0):** engines decide every number. Traits act on numbers only through engine formulas; the model chooses among options the engines allow and does the talking.

---

## 2. What makes agents feel alive

In rough order of effect:

1. **Memory with consequences, especially between people.** A GM who was fleeced by a team is colder with it two seasons later; a reporter JR brushed off writes a sharper piece. History with specific people matters more than a long biography.
2. **A specific situation.** Contract year, hot seat, losing streak, a feud with a coordinator. Project Mu's data holds most of this already; it has to reach the prompt.
3. **Wants and fears that pull against each other.** They give the agent choices to make, and stay in the background rather than being said aloud.
4. **Consistency with room to change.** The same voice and grudges over seasons, shifted by big events.
5. **Agents reacting to each other.** A player's quote leads to a writer's take, which leads to the GM's answer.

---

## 3. Original writer-persona problem (historical motivation)

Before phase 1, writer personas in `newsroom/config/agent_personas.json` were written as orders, not as character. The following describes that historical problem, not the current built configuration. Barnwell's says he "constantly" looks for regression to the mean, must "End with a 'Regression Warning' section", and must use a fixed lexicon. The model obeys, so every Barnwell piece is a regression piece. 10 of the 38 archetype personas and 3 of the 8 article personas use "always / constantly / never / must / End with" wording of this kind.

A belief written as a rule for every piece becomes a catchphrase. Adding a want and a fear the same way would add two more. The fix is the structure below: broad traits as the base, many views each with when it applies, signature moves as a pool, and memory that pushes back on repetition.

---

## 4. The personality model

### 4.1 Sliders (everyone; numbers with a budget)

Six sliders from 0 to 10. The total must stay between 24 and 36 (of 60), and seeding deals exactly 30, so nobody is high on everything. A high setting in one place still costs something somewhere else, which is what makes characters distinct (The Sims 1–2: 25 points over five sliders). Why a band and not an exact 30 (JR, 2026-11-02): an exact total makes some characters impossible and ties unrelated sliders together, because one slider can't move unless another moves the other way.

| Slider | 0 ↔ 10 |
|---|---|
| **Temperament** | calm ↔ volatile |
| **Sociability** | private ↔ media-friendly |
| **Confidence** | doubting ↔ brash |
| **Openness** | traditional ↔ experimental |
| **Warmth** | detached ↔ warm |
| **Drive** | content ↔ obsessive |

### 4.2 Traits (catalogue and origin-specific counts)

Generated core profiles currently receive four role-eligible catalogue traits, with 3–5 allowed after validated changes. The core technically supports zero to five reviewed traits for real people; this is not an existing-player rollout requirement (§5.1). unknown-origin profiles remain neutral with no traits. Do not fill evidence gaps or award extra traits because a player is a star. Existing writer profiles retain their own validated catalogue selections. At most two traits may come from one category, and conflicting pairs are forbidden.

| Category | Examples | Conflicts |
|---|---|---|
| **Football mind** | Film Junkie, Analytics Believer, Old School, Scheme Nerd, Historian | Analytics Believer ✕ Old School |
| **Social** | Loyal, Grudge Holder, Mentor, Diva, Peacemaker, Loner | Peacemaker ✕ Grudge Holder; Mentor ✕ Loner |
| **Ambition** | Ring Chaser, Money First, Legacy Builder, Hometown Guy, Spotlight Seeker | Ring Chaser ✕ Money First |
| **Media** | Quotable, Cliché Machine, Troll, Guarded, Hot Taker | Guarded ✕ Quotable |
| **Work** | Workout Fanatic, Film Room Rat, Coasting, Perfectionist | Workout Fanatic ✕ Coasting |
| **Teaching** (coaches, scouts) | Teacher, QB Whisperer, Disciplinarian, Players' Coach, Youth Developer | Disciplinarian ✕ Players' Coach |
| **Quirks** | Superstitious, Prankster, Foodie, Car Guy, Family Man | — |

Each catalogue entry defines:
* **Roles** that can draw it, and the odds per role.
* **Effect metadata** (optional): descriptive or zero-valued placeholders; existence is not activation. Feature policies own formulas, coefficients, context gates and caps (§6).
* **Prompt text**: a tendency phrased as "tends to …", plus **when it applies** (subjects, situations).
* **Conflicts** and **category**.

The catalogue lives in one data file (`newsroom/config/persona_traits.json`, started in phase 1 with the writer traits and the plan's examples; `data/` is git-ignored) so it can grow without code changes. Player work ethic, coachability and drive (progression plan §6) and coach teacher and specialty (§7) are entries in it, not separate systems.

### 4.3 Views (writers, GMs, coaches; many, each with when it applies)

Eight to twelve opinions about football, each with a trigger: "rookie QBs need two years (when a young QB struggles)", "records that outrun point differential don't last (when record and differential disagree by 2+ wins)". A prompt includes only the views whose trigger matches the piece's subject or the data. Regression shows up when the data supports it, not in every column.

### 4.4 Wants and fears

One want and one fear, kept in the background as motivation ("wants front offices to respect his work; fears being remembered as a hype man"). Prompts state them as context for the model, never as something to say.

### 4.5 Signature moves (a pool, not a requirement)

Two to five habits per writer or speaker (a historical comparison, a closing one-liner, a section title). The prompt offers them as optional, and the takes log tracks when each was last used, so a signature appears now and then.

### 4.6 Mood (from this week's data)

Design intent: derive temporary mood from verified context, separately from permanent identity and on-field form. The ordered policy below is a requirement for the proposed relationships/reactions component, not a claim that a universal mood engine is already active:

| Situation (from data) | Mood |
|---|---|
| Contract year, hot seat | Pressure |
| Losing streak, missed prediction, benched | Frustrated / defensive |
| Win streak, award, correct call | Confident |
| New team, new job | Eager / uncertain |
| Injury | Anxious / determined (by traits) |

When more than one situation applies, they are read in this order: own role or health first, then contract or job security, then team results. The first match is the main mood and the next one is an undertone. Example: a benched starter in a contract year on a 10–0 team is Frustrated, with an undertone of Pressure.

Mood shifts tone for the week without changing who the agent is.

### 4.7 Relationships

One-way rows (A → B), as `persona_relationships` stores them (`persona_type/persona_id → target_type/target_id`). A's view of B and B's view of A are separate rows, so one-sided grudges and unreturned loyalty are allowed. The legacy table stores `trust` (−1 to 1), a note, tags and created/updated timestamps; it has no `last_interaction` column or canonical event ledger. Event-backed trust, attachment, decay/repair and temporary reactions are built (observe-only, 2026-11-04) by [the relationships plan](../done/PERSONALITY_RELATIONSHIPS_PLAN.md). Earned attachment depends on recorded history and treatment, never a count of friends. Hometown is a separate location preference. Consumers must not invent their own competing loyalty state.

**Trust ownership (JR, 2026-11-03).** The relationships plan owns trust. Features where agents talk (the V2.9 inbox, V2.2 press conferences) do not write `persona_relationships` themselves: they record relationship events (an interaction, a treatment such as being skipped, a promise made or resolved) with the agent's own appraisal and reason attached, and the relationships policy turns events into trust. Trust is never read as mutual: a coach's view of a reporter is his own row, and until coaches have profiles and views he has none. Promises use the relationships plan's format (`promise_made` / `promise_resolved`: issuer, beneficiary, measurable criterion, due time, kept or broken), which is also what the trade-requests plan consumes.

### 4.8 Memory

The newsroom's model, extended to every agent: an episodic log (takes, quotes, deals, decisions), a season summary rewritten every few entries, and, once V2.8 exists, search over the full log so old history can come back. Prompts see only memories from before the moment being written, as `newsroom/agent_memory.py` does today.

---

## 5. How a persona is formed

1. **Establish identity and origin.** A caller verifies universe, role and immutable birth key. The core derives a UUID and binds verified prospect/player/save aliases. Names, team, age, OVR and ingestion provenance do not establish that someone is fictional.
2. **Initialize once.** Generated profiles use the existing role-weighted generator with the core version and UUID; seeding totals 30, uses four traits and records the catalogue snapshot/version. Stored profiles are not redrawn. Current implementation is deterministic but does not implement the older proposed league-wide stratification. `Player.Focus`/PT mappings are not an active core seeding rule; any future input mapping requires its own review and must not turn financial preference into lower work ethic.
3. **Real-inspired people are fictional characters (JR, 2026-11-03).** Project Mu is a personal universe: every persona named after a real person (writers, GMs, coaches, players) is a fictional character directly inspired by that person, not a claim about the real person. Such personas may hold views, moods and relationships formed from simulated events like any generated persona. Coaches get reviewed profiles shaped to their real counterparts (§5.1); existing players get engine-generated profiles (§5.1). JR can edit any profile. Core feature reads never use the legacy random fallback.
4. **Keep facts and voice distinct.** Bio facts are fixed. The proposed voice layer may refresh voice notes after a logged personality change; the core does not generate or rewrite prose. Core-managed review/adoption workflows remain explicit integration work, not unrestricted legacy debug edits.
5. **Let simulated history accumulate.** Owning policies may propose changes from verified events; the core validates and records them. Relationships, memories and temporary reactions are separate state. No automatic title/payday/benching-to-trait rule is activated by the core.

### 5.1 Approved rollout direction (JR, 2026-10-30)

The franchise starts in 2026. Mu controls CPU teams through 2033, and JR takes over a team in 2034. **Player rollout scope (JR, 2026-11-03, final):** every player gets a profile. Existing players in the starting 2026 save get engine-generated profiles once, at the start of the league, with no research into the real player; most will have left the league by 2034, so their personalities mainly shape the 2026–2033 simulation. Draft prospects are initialized once when they enter the universe, before personality-dependent scouting/draft decisions. Either way the profile is kept for the whole career; reaching an active roster or becoming a veteran never causes another initialization. JR can edit any player's profile (for example a real-inspired player whose generated personality he dislikes), recorded as an audited JR edit; before a profile's first use this is the D4 edit, afterwards it goes through the core's audited JR override ([core extensions plan](../done/PERSONALITY_CORE_EXTENSIONS_PLAN.md), §10 D4).

Do not research authored personalities for existing players; their profiles are generated, not researched. Bio facts stay factual (name, team, age, stats); only sliders, traits and the voice are generated. A real-player imported draft class (CFB 27 or community classes) is treated the same way: generated profiles at entry.

The Panthers sample is historical evidence-method exploration, not a roster-profiling task; its reviewed-evidence method is reused for coaches only. Generated profiles (existing players and prospects alike) can receive plausible seeded motivations and subsequently evolve from recorded simulated events.

**Coach scope (JR, 2026-11-03):** existing real coaches get reviewed personality profiles that represent their real-life counterparts. Unlike today's players, many of them will still be in the league in 2034 and beyond, so neutral defaults would leave the league's longest-lived decision-makers without character. Their views (coach → reporter, coach → player, coach → GM) also need fleshing out, through the relationships plan. Profiles use the reviewed evidence method (D5, the Panthers sample's method) for traits and style; like every real-inspired persona they are fictional characters and may form views from simulated events. The workstream that researches and seeds them is not planned yet (§5.3). Fictional incoming coaches remain a lifecycle decision.

**Writers and GMs (JR, 2026-11-03):** the existing writer personas (phase 1) and GM personas (phase 3), most named after real people, are fictional characters inspired by them and form their own views (press conferences, inbox, trade talks).

Design deliverable drafted: [generated-player effect matrix and examples](../reference/PERSONALITY_GENERATED_EFFECTS_DESIGN.md), including deterministic identity/seed handling, persistence across seasons, bounded effects, event evolution and offline validation. It recommends safe identity/resolution first, contract preferences next, then coordination with development/coaching owners and later requests/retirement. Proposed personality coefficients remain for review; feature-effect integrations are not implemented by this design. The separately completed cap-relative work changes pricing/earnings prerequisites, not personality effects. Offline demonstration now available: `python -m tools.personality_contract_demo` produces seven synthetic situations and 525 parameter combinations; see the design document for bounds, threshold and noise findings. This direction does not authorize production seeding or activate engine effects. JR refinement: strong ring chasing is situational (established career, achieved financial security and usable contender role), not universal or inferred from elite ratings. Earned loyalty reflects simulated history and treatment, separately from a smaller verified-hometown preference; no relationship-count bonuses. Equal-strength loyalty examples and trial tiers are in the generated-player design. The existing six sliders and trait catalogue remain the foundation.

### 5.2 Feature-independent core (built 2026-10-31)

`personality/README.md` documents the implemented API and ownership boundaries. The core supplies stable universe identity, explicit one-time initialization, verified aliases, versioned catalogue/profile snapshots, provenance-scoped feature views and audited one-change-per-season evolution candidates. Persistence reuses existing slider/trait/history tables with sidecars in `2027_01_16_personality_core.sql`; no production migration or seeding has run. Existing writer/GM paths are unchanged. Generated initialization still seeds four traits and a total of 30; real/unknown defaults are neutral, with sparse evidence permissions for reviewed real people. Missing reads never generate profiles and storage failures are explicit.

Contracts, progression, relationships/mood, trade requests and retirement remain separate feature policies. The core checks and records a candidate; it does not decide that a title, payday or benching should cause one. Verified pipeline identity binding, classification/adoption workflows and feature activation remain integration work. Unknown identities must not be guessed as generated. The slider band (2026-11-02) moved both the Python validator and the SQL constraint (`2027_01_17_persona_slider_band.sql`) from an exact 30 to 24–36, so single-slider evolution is possible once that migration is applied. Core trait evolution already works, and future slider evolution adopts the active validated band without reseeding.

Cap history, career earnings and pricing prerequisites are now built; see §5.3 for the 14 separate sub-plans. Shared core schemas and public API changes require coordination; feature policies must not edit core state directly or introduce duplicate personality schemas.

### 5.3 Workstream tracker and review gate (2026-11-03)

This is the parent tracker for every personality sub-plan: the six drafted from 2026-10-31, the relationship event producers, and the seven gap plans written on 2026-11-03 (core extensions, real-coach profiles, evolution, agent loops, owners/scouts/voters, display, activation). JR approved all 14 on 2026-11-03; the original six were first rewritten to follow his decisions (§5 step 3, §5.1, D8–D13), and each plan's §8 records its own. Rows are in build order: each plan's inputs come from rows above it, the display plan can build at any point, and activation builds last. An approved plan is not a live effect: each still gates migration, production seeding and activation on JR, and each re-reads the files it shares with the approved V2 plans before building.

| Workstream | Owning artifact | Current status | Dependency / next gate |
|---|---|---|---|
| Personality core | [Core API and boundaries](../../personality/README.md), §5.2 | Built; explicit API and offline preview, no automatic production binding | Lifecycle owner must verify identities and ingestion; feature owners consume scoped views. Core gaps closed by the [core extensions plan](../done/PERSONALITY_CORE_EXTENSIONS_PLAN.md) (built 2026-11-04): JR override after first use (D4), full reads of reviewed real profiles (D13), as-of reads, in-place adoption of writer and GM personas, role-change carry-over |
| Pricing, cap history, career earnings and slider band | [Cap-relative contracts](../done/CAP_RELATIVE_CONTRACTS_PLAN.md) | Built on main 2026-11-02; personality effects still inactive | DB-specific migration state and earnings uncertainty remain explicit; not a second contract-effect policy |
| Core extensions | [Core extensions plan](../done/PERSONALITY_CORE_EXTENSIONS_PLAN.md) | Built on main 2026-11-04; migration `2027_01_29` not applied, writers/GMs not adopted (JR's call, after `--dry-run`) | JR override after first use (D4), full reads of reviewed real profiles (D13), as-of reads, in-place adoption of the 40 writer and 32 GM personas, role-change carry-over; builds first, since lifecycle, coach profiles and evolution use it |
| Player lifecycle (starting roster and draft prospects) | [Draft integration plan](../done/PERSONALITY_DRAFT_INTEGRATION_PLAN.md) | Built on main 2026-11-04; migration `2027_01_24` not applied, no inventory applied to any franchise (JR's call: preview, review, then `apply`) | Scope (JR, 2026-11-03): generated profiles for the starting 2026 roster and for each draft class; stable handoff and origin classification; adopts the scouting plan's `prospect_uid`. Offline inventory/manifest CLI (`tools/personality_inventory.py`), lifecycle ledgers and resolver (`personality/lifecycle*.py`), observe-by-default loader, scouting and staff hooks |
| Relationships, attachment, mood and reactions | [Relationships plan](../done/PERSONALITY_RELATIONSHIPS_PLAN.md) | Built on main 2026-11-04, observe-only; migration `2027_01_27` not applied (scratch persistence tests not yet run: no PostgreSQL at build), no numeric policy | Canonical event contract, one-way trust and earned attachment, reactions/mood and candidate review, the ledger store and an offline preview; production migration, intake and numeric trust each need JR's go-ahead (its §6 gates) |
| Simulation event producers | [Relationship event producers plan](../done/RELATIONSHIP_EVENT_PRODUCERS_PLAN.md) | Built on main 2026-11-04 (waves 1–2), off by default; migration `2027_01_28` not applied, no catch-up run (JR's call: preview and `catch-up --dry-run` first, then `MU_RELATIONSHIP_PRODUCERS=on`) | Turns rosters, depth charts, moves, contracts, injuries, awards, results and coach moves into relationship events by fixed rules; held queue for unresolved identities; watcher step after the load; `coordinator_alias` and rejected-receipt handling fixed in wave 2 |
| Real-coach profiles and coach views | [Real-coach profiles plan](../done/REAL_COACH_PROFILES_PLAN.md) | Built on main (waves 1–3, 2026-11-05); re-seated to the 2026 staffs 2026-11-06; all 96 profiles approved by JR 2026-11-07 (and re-approved after the v2 conversion); plan in docs/done | Reviewed, cited profiles for the 32 HCs and 64 coordinators (sliders, traits, views, want/fear, voice, signatures, reviewed starting ties), researched by agents in four batches and reviewed by JR; applied through the lifecycle plan's reviewed manifest; read in full under the core extensions plan (D1); feeds V2.2 press conferences, V2.9 inbox coach voices and the development/coaching teaching channels |
| Personality evolution | [Personality evolution plan](../done/PERSONALITY_EVOLUTION_PLAN.md) | Built on main 2026-11-06 (waves 1–3), off by default; migration `2027_01_30` not applied, nothing run on a franchise (JR's call: `season --dry-run` report, then `MU_PERSONALITY_EVOLUTION=on`) | Seeded event-to-trait/slider rules (evolution-v1) for players and coaches, the one-change-a-season arbiter over rule and other features' candidates, revert by override, change notes; inputs from the producers, relationships and development plans |
| Personality-contract decisions | [Contract policy plan](../done/PERSONALITY_CONTRACT_POLICY_PLAN.md) | Built on main 2026-11-06 (waves 0–3), off by default; migration `2027_01_23` not applied, no league-office inputs and no switchboard yet, so every path reads off (JR's call: comparison report, then activation through the activation plan) | Core/lifecycle, earnings and relationship outputs; consistent scoring/counters/CPU/UI and activation controls |
| Development and coaching | [Development/coaching plan](../done/PERSONALITY_DEVELOPMENT_COACHING_PLAN.md) | Built on main 2026-11-06 (waves 1–3), off by default; no migration, no active preset, no production caller passes a config/context (JR's call: candidate configs from the demo's paired reports, then activation through the activation plan, learning and opportunities separately) | Pure preparation/teaching/mentor policy with annual caps, default-off progression receipt and offseason hooks, depth-chart `score_adjustments` seam (consumer of the inbox `playing_time` vision), synthetic paired-control demo; mentor effects stay disabled until progression isolates its mentor rating component |
| Dissatisfaction and trade requests | [Trade requests plan](../done/PERSONALITY_TRADE_REQUESTS_PLAN.md) | Built on main 2026-11-06 (waves 1–2), no live caller; migration `2027_01_25` not applied (scratch persistence tests not yet run: no PostgreSQL at build), no production policy (JR's call: sensitivity review of the preview, then scheduling and the GM signal through the activation plan) | Pure qualification/annual draw/lifecycle over the relationships plan's shared reactions and breaches (`engines/player_trade_requests.py`), store (`engines/player_trade_request_store.py`), offline preview (`tools/player_trade_request_preview.py`); a request is not trade execution, a price discount or a player veto |
| Retirement decisions | [Retirement plan](../done/PERSONALITY_RETIREMENT_PLAN.md); [retirement window plan](../done/RETIREMENT_WINDOW_PLAN.md) | Built on main 2026-11-06 (waves 0–3), no live caller; the [retirement window](../done/RETIREMENT_WINDOW_PLAN.md) (one decision per offseason, late native retirements restored through the default-deny planner) built on main 2026-11-07 (approved by JR 2026-11-07), observe-only until the G4 proof file; migration `2027_01_26` not applied (scratch persistence tests not yet run: no PostgreSQL at build), no approved policy, save authority unproven (`docs/reference/RETIREMENT_AUTHORITY.md`; JR's call: disposable-save restore proof (G4), then calibration from recorded cohorts, then activation through the activation plan) | Establish neutral baseline and prove save/native-retirement authority before activating modifiers or writes |
| Coach and player agent loops | [Agent loops plan](./COACH_PLAYER_AGENT_LOOPS_PLAN.md) | Built on main 2026-11-06 except U3 (U1 base/coach loop, U2 player loop and `counter_options`); migration `2027_02_01` not applied, no live caller; U3 wiring held (below) | CPU coach close calls, promises and reviews; player counter choice, capped public statements and inbox replies; model only where seen (4 marquee CPU cases a week). U3 (league office, inbox kinds, social desk, switchboard) waits for the inbox `CONTRACT`/`ROLE_TALK` kinds and trade talks U6 |
| Owners, scouts and media voters | [Owners, scouts and voters plan](./OWNERS_SCOUTS_VOTERS_PLAN.md) | Built on main 2026-11-06/07 except U6 (U1 owner profile schema, owner/scout traits and `review --kind owner`; U2–U3 32 owner profiles, approved by JR as v2; U4 owner policy; U5 owner wiring into the hot seat, GM rollover, carousel, CPU offers and league office plus the `OWNER` inbox kind, built 2026-11-07, every owner feature off; U7 media voter ballots for the awards and All-Pro teams, switchboard-gated, with an open TODO to give the articles the ballot results); migration `2027_02_02` (U6) not written | 32 reviewed real-inspired owners (patience, hiring, spending, meddling; talk only on JR's teams), one generated scouting director per team, award winners and All-Pro teams from writer ballots. U6 (directors, migration, features) waits for scouting U2's `scout_voice.py`; until its migration exists the owner features can't leave off |
| Personality on the site | [Personality display plan](../done/PERSONALITY_DISPLAY_PLAN.md) | Built on main 2026-11-06 (waves 1–2), no migration; mood, ties and attachment read unavailable until the activation plan supplies an enabled relationships policy; writers/GMs show from legacy persona rows until adopted. Front-office staff ids added as a follow-up; plan in docs/done | `/api/personality` read and override; a Personality panel on player pages, staff cards, the front office tab and author pages; no engine numbers; prospects hidden until drafted |
| Inferred sliders and refreshing focus | [Inferred sliders and focus plan](../done/INFERRED_SLIDERS_AND_FOCUS_PLAN.md) | Built on main 2026-11-07 (approved by JR 2026-11-07; waves 1–3), off by default; migration `2027_02_03` not applied (scratch test only); all 128 coach/owner profiles converted to v2 and back to draft | Inferred values for no-evidence coach/owner sliders (low-confidence provenance, reach the core only through the reviewed lifecycle apply); durable motivation plus a current focus refreshed at OFFSEASON_START (switchboard `focus_refresh`, after `evolution`). JR's call: re-approve the converted profiles, then migration, then the feature to shadow |
| Coaching trees | [Coaching trees plan](../done/COACHING_TREES_PLAN.md) | Built on main 2026-11-07 (approved by JR 2026-11-07, Q1–Q9 as recommended; waves 1–3), no migration; `direction` on mentorship ties, coaching-tree files/module/review tool, producer rule `coaching_tree` (producers-v2), writer and voice lineage, `coaching_tree_hiring` off by default; the registry (19 trees + reich, arians) and batches A–C (139 edges, 67 memberships) approved by JR 2026-11-07 | Tree hiring to shadow through the activation plan |
| Live wiring and activation | [Activation plan](../done/PERSONALITY_ACTIVATION_PLAN.md) | Built on main 2026-11-06 (U1 switchboard, U2 league-office PersonalityDeps, U3 trade-request reviews, GM signal and switchboard watcher flags); every feature off, migration 2027_01_31 not applied, no shadow season run | One switchboard per franchise (off/shadow/active, go-live order, one shadow season); shadow seasons run on tuning franchises and their clearances are imported, so every cleared feature is live from the real franchise's 2026 week 1; `PersonalityDeps` in the league office; trade-request scheduling and GM signal |

**Build order with the V2 plans (JR, 2026-11-03).** The personality plans build before the V2 plans ([inbox](./FRONT_OFFICE_INBOX_PLAN.md), [scouting](./SCOUTING_REVAMP_PLAN.md), [press conferences](../not_started/PRESS_CONFERENCE_PLAN.md), [trade talks](../not_started/TRADE_NEGOTIATIONS_PLAN.md)), with one chain and three held units:

1. **First, two V2 units:** inbox U1, then scouting U1. The lifecycle plan adopts scouting's permanent prospect ID (`prospect_uid`, `engines/prospect_ids.py`, scouting U1), and scouting U1 lists inbox U1 as its prerequisite (mostly merge order on `db/init_db.sql`). The rest of both plans waits.
2. **Then every personality plan in the table's order,** holding back three units:
   - **Agent loops U3** (wiring): needs the inbox's `CONTRACT` and `ROLE_TALK` kinds, and the social desk after trade talks U6;
   - **Owners U5:** needs the inbox kind registry for the `OWNER` kind;
   - **Owners U6:** needs scouting U2's `engines/scout_voice.py`.
3. **Then the V2 plans.**
4. **Then the three held units.**

Inbox and press conferences record relationship events into their own outbox until the relationships store exists, so the relationships plan doesn't wait for them. Shared files (`server/franchise_api.py`, `engines/gm_trade_engine.py`, `pipeline/weekly_capture.py`, `db/init_db.sql`) only mean the second builder re-reads them first.

**Parallel boundaries:** lifecycle and relationship designs can proceed independently; contract policy consumes both plus the completed earnings/pricing work. Development/coaching pure policies can proceed with their existing owners. Trade-request and retirement baselines can be designed independently, but execution waits for the required shared state and write authority. Shared-file ownership and cross-plan interfaces must be reconciled before implementation waves. Proposed migration filenames in separate drafts are reservations to reconcile, not permission to reuse a number.

**Migration reservations (2026-11-03).** `2027_01_17` is taken by `persona_slider_band` and `2027_01_18` by `writer_voice` (built), and the V2 plans hold `2027_01_19` (inbox), `_20` (press conferences), `_21` (scouting) and `_22` (trade talks). The personality sub-plans hold: contract policy `2027_01_23_contract_personality_policy.sql`, draft lifecycle `2027_01_24_personality_lifecycle.sql`, trade requests `2027_01_25_player_trade_requests.sql`, retirement `2027_01_26_retirement_policy.sql`, relationships `2027_01_27_personality_relationships.sql`, event producers `2027_01_28_relationship_producers.sql` (built), core extensions `2027_01_29_personality_core_extensions.sql`, evolution `2027_01_30_personality_evolution.sql`, activation `2027_01_31_personality_feature_modes.sql`, agent loops `2027_02_01_agent_loops.sql`, owners/scouts/voters `2027_02_02_owners_scouts_voters.sql`, inferred sliders and focus `2027_02_03_persona_focus.sql` (built 2026-11-07); outside the personality plans, real NFL history takes `2027_02_04_nfl_history.sql` ([REAL_NFL_HISTORY_PLAN](../done/REAL_NFL_HISTORY_PLAN.md), built 2026-11-07, not applied). Development/coaching, real-coach profiles, display and coaching trees need no migration. Recheck at build, since the V2 plans' units build in their own order.

**Review record (2026-11-03):** JR's decisions are recorded in each owning plan's §8, and each plan is marked approved. Building one goes through `/execute-plan` on that plan.

### Change over time

Built in the [personality evolution plan](../done/PERSONALITY_EVOLUTION_PLAN.md) (2026-11-06, off by default): seeded rules for players and coaches, a one-change-a-season arbiter, a season report with revert, and change notes in prompts. Writers and GMs keep their authored personalities.

Like Sims aspirations: at most once a season, a major event can add or drop a trait or move a slider a step (seeded). A title can turn Ring Chaser into Legacy Builder; a firing can raise Temperament; a mentor's retirement can make a player a Mentor. A slider move that keeps the total inside the 24–36 band needs no paired move on another slider. Each change is logged with its cause, so writers can tell the story.

---

## 6. Where personalities act

The core supplies validated profile state and feature-scoped permissions. Feature policies own consequences, context and numerical limits. The approved sub-plans in §5.3 supersede older example lists as implementation-design references; unapproved coefficients remain offline trials.

| Feature | Intended consequence and owner | Current boundary |
|---|---|---|
| Contracts | Contract policy translates money/contention preferences and verified attachment/home context into bounded offer scoring and negotiation | Existing pricing/legality/free-agency engines keep authority. Earnings security is an input, not automatic activation. Strong ring chasing also needs established career and usable contender opportunity. |
| Development/coaching | Separate policy adjusts supported learning and opportunities through the existing progression/depth-chart lifecycle | No second progression engine; no double-counting ratings, mentoring, scheme fit, exposure or annual attribution. Caps and coefficients require owning-plan review. |
| Relationships/reactions | Shared event-backed trust, earned attachment, temporary mood and repair | Consumers reuse state; no friend-count bonuses or competing trust calculations. Mood is separate from form and neutral Madden morale. |
| Trade requests | A decision component produces requests/reconciliation from sustained verified dissatisfaction | Existing GM valuation, contractual rights, legality and execution remain authoritative. No arbitrary Grudge Holder trade discount. |
| Retirement | A retirement baseline plus separately reviewed personality modifiers | Recording a retirement is not proof Mu controls the decision or its save write. No forced retirement or immortality from a trait alone. |
| Prompts/site | Scoped voice, context, views, memory and optional display | Presentation is useful but not the purpose of consequential simulation personalities. Core does not generate prose or make tool choices. |

**Rejected causal shortcuts:** Money First does not cause coasting or a post-payday development penalty. Quiet/guarded style does not imply low confidence or bad PT flags. Warmth is not private loyalty. Hometown does not mean old-team attachment. A contract extension does not prove financial security; the earnings ledger has explicit uncertainty. Scheme Nerd does not add a duplicate scheme-fit multiplier. Catalogue `effects` can contain empty or zero-valued placeholders; they do not activate anything.

The offline contract demonstration illustrates ranking/acceptance sensitivity and aggregate bounds; it is not calibrated production behavior. The proposed money/contention tradeoff preserves component monotonicity only under its documented coefficient limits; this does not mean it cannot change which overall offer wins.

---

## 7. Prompt assembly

```
[identity]       name, role, bio                            (fixed)
[voice notes]    how this person talks                      (rewritten after a logged trait or slider change)
[personality]    sliders as one sentence                    (fixed, slow change)
[active traits]  traits whose "when" matches, as tendencies
[active views]   views whose trigger matches the subject/data
[mood]           from this week's data
[stakes]         contract, job security, team direction     (from data)
[relationship]   with each person involved
[memory]         recent entries + summary (+ search hits, V2.8)
[avoid]          last N angles and signatures used
[task + tools]
```

Every line is chosen by code from stored data. Nothing in it says "always".

---

## 8. Agentic loops

Coach and player loops are planned in the [agent loops plan](./COACH_PLAYER_AGENT_LOOPS_PLAN.md) (approved 2026-11-03; built 2026-11-06 except U3 wiring, held): CPU coaches decide close depth-chart calls and make and keep promises; players choose among the engine's clearing counters and may go public, capped; a seeded policy everywhere, the model only where JR or the press sees it. GM loops are the V2.11 trade talks plan.

Target design for agents that act rather than only write (GMs, players, coaches, owners); phase 5 and feature-specific loops are not claimed built. A player's own AI loop does his negotiating; no hired agent sits between him and the GM:

* **Triggers:** schedule (each week, each offseason stage) and events (injury to a starter, offer received, benched, fired).
* **Tools per role, kept narrow.** GM: roster, needs, cap, `price_deal`, `message_gm`, `make_offer`, inbox. Player (negotiating): market value, offers, `counter`, `leak`. Coach: depth chart, practice reports, `set_depth`. No web access; the league is the world.
* **Guardrails (proposed role loops):** step and budget limits; every action that changes the league goes through the owning engine's legality and acceptance checks; outputs validated against the data.
* **Record, don't replay:** model choices aren't reproducible, so every action is stored and the stored record is the truth.
* **Cost:** many calls per loop. A cloud model for now (JR, 2026-11-03, V2.2 press conferences and V2.9 inbox); the local model (migration plan §7) only where it passes the §7.4 quality gate and doesn't compete with Madden for the GPU.

---

## 9. Phases

| Phase | What | Needs |
|---|---|---|
| **1. Writer personas, now** (built) | `agent_personas.json` rewritten from rules into sliders, traits, views with triggers, signature pools and a want/fear, with the long-form voices folded in as `formats.ARTICLE`; no "always / must / end with". `newsroom/personas.py` picks active views from the piece's tags and the subject team's results; recent headlines and recently used signatures form the avoid list (from `news_feed`, no new table). The 13 writer slate-selection directives rewritten as leanings with alternatives. Still to do: the side-by-side test against current output on a week's slate (costs model calls) | Nothing new |
| **2. Catalogue and tables** (built 2026-10-28) | Full 50-trait catalogue in `persona_traits.json` with categories, role odds, prompt tendencies, and optional effects; tables for persona sliders (`persona_sliders`), traits (`persona_traits`), views (`persona_views`), relationships (`persona_relationships`), and trait history (`persona_trait_history`); deterministic seeding and budget validation; review endpoints in `#debug` (`GET/POST /api/debug/personas`) | — |
| **3. GMs** (built 2026-10-28) | Sliders and traits on top of `gm_profiles` (and `engines/gm_personas.py`), GM prompt formatting and negotiation dialogue reflection in `engines/gm_negotiation.py` without altering deterministic trade values | 2 |
| **4. Player and coach personalities and feature integrations** (core built; integrations proposed) | Core §5.2; 14 separately approved sub-plans tracked in §5.3. Generate profiles for existing players once at the 2026 start and for prospects at draft-class entry, and preserve them through 2034 and beyond (§5.1). Existing real coaches get reviewed profiles (JR, 2026-11-03, §5.1); that workstream needs a plan. Effects require owning-engine integration and activation gates. | Core + verified lifecycle + individual plan approval |
| **5. Agentic GMs** | Trigger + tools + loop for GM talks and trades (V2.11), inside the trade engine's checks | 3, league office save moves |
| **6. Owners, scouts, media voters** | Owner and scout profiles and voices, media voters for award ballots. The V2.9 inbox and V2.10 scouting plans are not blocked by this phase (JR, 2026-11-03): they ship with neutral owners and scouts and pick up these profiles when they exist. Planned in the [owners, scouts and voters plan](./OWNERS_SCOUTS_VOTERS_PLAN.md) (approved 2026-11-03; built 2026-11-06 except U5 and U6, held) | 2–5 |

---

## 10. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| D1 | Slider budget and count | Six sliders, 0–10 each, total 24–36, seeded at 30 (JR, 2026-11-02) |
| D2 | Traits per person | Generated core profiles seed four; 3–5 validated later. Real core profiles may be sparse (0–5); unknowns neutral. Do not add traits for star status. |
| D3 | Are traits visible on the site? | Yes for traits and sliders; mood shown as a tag; engine effects never shown as numbers Built by the [personality display plan](../done/PERSONALITY_DISPLAY_PLAN.md): prospects hidden until drafted, ties as labels, on-page editor for JR |
| D4 | Can JR edit personas? | Yes, before first use; afterwards only through events. **JR (2026-11-03):** he can also change any player's personality later if he dislikes it; an audited JR override outside the once-a-season event limit, planned in the [core extensions plan](../done/PERSONALITY_CORE_EXTENSIONS_PLAN.md) (CLI and `#debug`) |
| D5 | Real-world writers and coaches | Traits that fit their public style; reviewed by JR |
| D6 | How often traits change | At most one change per agent per season, after a logged event |
| D7 | Start phase 1 before the PC? | Yes; it fixes the repetitive writer columns now |
| D8 | Who calculates trust? | **JR (2026-11-03):** the relationships plan. Inbox and press conferences record events with the agent's appraisal; no feature writes trust directly (§4.7) |
| D9 | Promise format | **JR (2026-11-03):** the relationships plan's `promise_made` / `promise_resolved` (§4.7) |
| D10 | Is trust mutual? | **JR (2026-11-03):** no. One-way rows only; coaches get their own views once they have profiles |
| D11 | Profiles for existing real coaches? | **JR (2026-11-03):** yes, reviewed profiles representing their real counterparts (§5.1); needs its own plan |
| D12 | Profiles for existing real players? | **JR (2026-11-03):** yes, engine-generated once at the 2026 start, not researched; JR can edit any (§5.1, D4) |
| D13 | Can personas named after real people form views? | **JR (2026-11-03):** yes. All real-inspired writers, GMs, coaches and players are fictional characters inspired by their counterparts (§5 step 3) |

---

## 11. Spec changes when built

Persona tables (sliders, traits, views, relationships, trait history) in spec §3; prompt assembly in the newsroom section (§7) and `agent_personas.json` structure; engine trait effects in each engine's section; writer tools gaining relationships and traits.

---

## 12. Deferred

Agreed on 2026-10-31 and left for later work:

* **Lowball zone in re-sign and extension talks.** An offer far below his value costs trust with the GM and may leak to the press.
* **Cap casualty after a discount.** A player who took less and is then cut for cap reasons loses trust toward that GM (one-way). Any trait change waits for the yearly review.
* **Elite positional-rank demand.** A top player asks to be paid among the top few at his position.
* **Two or three views for players who speak.** Players who give interviews get views like writers do.
* **Owner and scout categories (phase 6).** Owners: Meddler / Hands-Off, Deep Pockets / Budget Conscious, Headline Hunter / Patient Builder. Scouts: Traits Chaser, Tape Grinder, Character Hawk, Floor Seeker / Ceiling Gambler. Now planned in the [owners, scouts and voters plan](./OWNERS_SCOUTS_VOTERS_PLAN.md).

---

## 13. Considered and rejected (2026-10-31)

| Idea | Why not |
|---|---|
| "Bag Drop": Money First plus low Drive gains Coasting | Wanting money must not mean working less after payday (§6 design qualification). |
| Warmth as a switching cost | Warmth is manner, not attachment. Staying comes from earned attachment (LOYAL tiers). |
| Confidence changes guarantee demands | Public style must not set pay terms; the engine already scores guarantees. |
| Scheme Nerd contract multiplier | Scheme fit is already a utility term; a multiplier counts it twice. |
| Sliders on 0–100 | False precision. Prompts and yearly steps work in whole points on 0–10. |
| Separate hired agents for players | One more actor with no new decisions. The player's own loop negotiates. |
