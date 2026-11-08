# Project Mu: Front-Office Inbox & Direct Conversations Plan (V2.9)

**Status:** approved by JR (2026-11-03); building, wave 1 of 3 done (U1 and U2 merged 2026-11-07). The remaining units were held until the personality plans were built (PERSONALITY_PLAN §5.3); that hold is satisfied as of 2026-11-07, and JR directed the inbox to go next on 2026-11-07. **The 30-minute model-latency spike (§3 R3, §5 "Spike first") is tabled (JR, 2026-11-07)** until the final model for internal calls is chosen; wave 2 proceeds with the 8.0 s estimate unchanged, and the spike plus the `INBOX_LLM_BUDGET_S` update stay on the close-out list alongside the `llm_compare` gate. Wave 2 (U3, U4, U6) started 2026-11-07.
- As built (U2): `types/franchiseInbox.ts` carries the §2.12 shapes; `InboxModule.tsx` and the FranchisePage Inbox tab are built against them while `server/inbox_api.py` (U5) does not exist yet, so every fetch goes through `useFranchiseData`/`StateNote` and shows an unavailable/empty state until U5 lands. Details §2.12 leaves open, chosen here: the list query sends `status=OPEN` (uppercase, matching the table's CHECK values) and omits it for All, `as` carries the actor role and `kind` the kind key; the four form payloads are `contract_terms {years,total_value,bonus,guaranteed}`, `trade_assets {players:number[], picks:{season,round,original_team}[]}`, `promise {slot,max_depth,due_week}`, `vision {direction,emphasis,playing_time}`; starting a conversation posts `{kind, as, note, ...start_form payload}` and a plain reply posts `option_id` and `payload` null with the text as `note`; thread `actor`/`counterpart` are read defensively (falling back to `actor_role`/`counterpart_name`). Mu_Network has no test runner and U2 owns no test files, so its verify is `npm run build` (exit 0).
- As built (U1): the migration was not applied to `madden_test` (orchestrator rule: no real database; the DDL is exercised by the tests through an in-memory SQLite translation). The weekly hook is logged as "Step 7b: front-office inbox". Extra core helpers beyond §2: `voice.compose` (returns the model's extra JSON keys, e.g. REPORTER's `done`), `voice.exchange` (one JR turn in §2.4's order, for U5), `scan.start_thread` / `scan.scan_on_open`, and optional kind hooks `review_promises(cur, season, week, team)` (U3's ROLE_TALK promise reviews), `VOICE`, `EXTRA_JSON`, `on_reply`. `kinds/` is empty. The new spec sections are §2.21b and §3.3s (§3.3r was taken by the relationship producers).
**Builds on:** [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §2, §5.3.2, §6 V2.0 and V2.9, §7.2; [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) §8, phases 4 and 6; PROJECT_SPEC §2.16 (team direction), §2.17c (depth charts), §3.3f (GM negotiations), §3.3p (personas and relationships), §3.3q, §4.6b (franchise API), §2.21a (league office).

**Goal:** JR plays through personas he takes control of: a team's GM, its head coach, and later a player. The inbox is where each persona JR controls talks to the other agents in the league, one conversation per pair:
- JR's GM with another GM about a trade;
- JR's GM with one of his players about a contract;
- JR's coach with a player about his role;
- JR's coach and the team's GM agent (or JR's GM and the coach agent) agreeing on a team vision;
- JR, in any role, with his beat writer in a one-on-one interview.

Engines decide every outcome. The agent on the other side answers in its own voice. A language model writes that answer and it is checked against the facts; fixed template text is the fallback. Where its profile allows a private appraisal, the agent also appraises how it was treated; the inbox records that as a relationship event (with the reason) for the relationships plan, which alone turns events into trust. The inbox is also the shared surface the sibling plans plug into: V2.11 deepens trade talks, V2.10 adds prospect interviews and scouts, and V2.2 posts a notice when a press conference is ready.

**Terms.**
- An **agent** is a persona with a personality and an interface to act through: a GM, a coach, a player or a reporter. It never means a player's representative; players speak for themselves.
- A **controlled persona** is one JR has explicitly taken control of.
- A **counterpart** is the agent on the other side of a thread.

**Delivers:**
- Tables `controlled_personas`, `inbox_threads`, `inbox_messages`, `inbox_relationship_events` (the event outbox) and `team_vision` (migration `db/migrations/2027_01_19_front_office_inbox.sql`, folded into `db/init_db.sql`; the relationships plan reserves `2027_01_27_personality_relationships.sql`, and this one stays `2027_01_19`).
- Package `engines/inbox/`, each module with one job:
  - `actors.py`: which personas JR controls;
  - `store.py`: threads, messages and `jr_statements`;
  - `registry.py`: conversation kinds, auto-discovered;
  - `voice.py`: the counterpart's words;
  - `appraisal.py`: relationship events (`meaningful_interaction`, `treatment`, `promise_made`, `promise_resolved`) in the relationships plan's envelope, kept in an outbox until that plan's store exists;
  - `parse.py`: typed text → a draft JR confirms;
  - `scan.py`: inbound messages;
  - `__main__.py`: CLI.
- Five conversation kinds in `engines/inbox/kinds/`:
  - `gm_talk.py`: GM ↔ GM, trades;
  - `contract.py`: GM ↔ own player;
  - `role_talk.py`: coach ↔ own player, role, playing time and promises;
  - `vision.py`: coach ↔ GM, team vision;
  - `reporter.py`: any controlled persona ↔ beat writer, open-ended one-on-one interview.
- `engines/team_vision.py` (the vision engine), plus a direction pin and a needs emphasis in `engines/gm_direction.py`.
- Endpoints `/api/franchise/{abbr}/inbox*` in a new `server/inbox_api.py`, including taking and releasing control of a persona.
- An **Inbox** module (`#franchise-inbox`) in the Command Center, with an unread count, an "acting as" filter and a "take control" panel.
- A `step_inbox` call in `weekly_capture.run_weekly_engines`.
- Designed, not built: owner agents (options, §2.11) and controlled-player mode.

**JR's answers (2026-11-03):**
- D1: JR may control any number of GM, coach and player personas. Threads are (controlled persona ↔ counterpart persona). Controlled-player mode isn't designed yet, but the model must allow it.
- D2: One inbox per team. The team's beat writer can message JR. Players speak for themselves. JR's on-record answers are stored for articles later.
- D3: No owner threads in this build and no stub kind; `owner` stays a valid counterpart type. A short options section says what owner agents could own later. When they come, start with the cap mandate and job security.
- D4: Reporter talks in the inbox are one-on-one interviews. Postgame press conferences are a separate multi-agent panel owned by [`PRESS_CONFERENCE_PLAN.md`](../not_started/PRESS_CONFERENCE_PLAN.md); the inbox only carries its `PRESS` notice.
- D5: The inbox is built first; [`TRADE_NEGOTIATIONS_PLAN.md`](../not_started/TRADE_NEGOTIATIONS_PLAN.md) (V2.11) then extends `GM_TALK`.
- D6: The franchise registry's `user_teams` is the source of truth for which teams JR controls, not `gm_team_settings.user_controlled`. Madden keeps all 32 teams as user teams to stop CPU moves.
- D7: The language model is the default way counterparts speak (`MU_INBOX_LLM` on, `=0` for templates). It runs with an 8 s budget set by the spike, and the `llm_compare` gate runs before the plan closes; if the gate fails, templates stay the default until the prompt is fixed.
- D8 (Q1): **By default JR controls no one.** A persona is controlled only when JR explicitly takes control of it, which stores a `controlled_personas` row. A registry user team with no rows has no inbox actors. There is no coach-plus-GM default.
- D9 (Q2): **Build coach ↔ GM team-vision talks now.** The canonical league starts in 2026 from the real 2025 NFL seed, where head coaches and GMs are separate people with separate personas. Skip vision talks only on a team whose head coach is also its GM. Coach voice is neutral until coach personalities are switched on, as in the press plan.
- D10 (Q3): Typed text becomes a draft JR confirms, for contract terms, trade packages and role promises. Rules parse it first; the model is the fallback, limited to listed IDs and ranges. Nothing acts without JR's click.
- D11 (Q6, Q7): **A coach's promise never changes the depth chart.** The coach can tell a player he'll move up and then not do it. The promise is recorded as `promise_made` and resolved as `promise_resolved` from the actual depth chart at the due week (D16). What that does to the player's trust is the relationships plan's policy; it never changes a sim number. A depth-chart change happens only when JR makes it in the Depth Chart module.
- D12 (Q8): **Interviews are open-ended.** The reporter keeps asking until he's done or JR ends it, capped at 10 questions.
- D13: **The counterpart agent appraises the exchange from what was said** (respectful and in depth vs snippy or rude), with a stored reason; no fixed table of deltas. This matches the press conference plan. Since D15 the appraisal is evidence recorded as an event, not a trust write.
- D14: V2.10's `PROSPECT` kind (combine interviews and top-30 visits) and `SCOUT` kind belong to the GM persona JR controls on that team (`ACTOR_ROLES = ('GM',)`). If JR doesn't control that team's GM, he gets no prospect interviews or scout notices.

**Cross-plan decisions (JR, 2026-11-03, after review against [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) §4.7, §5.1, D8–D12 and [`PERSONALITY_RELATIONSHIPS_PLAN.md`](../done/PERSONALITY_RELATIONSHIPS_PLAN.md) §0b):**
- D15: **The relationships plan owns trust.** The inbox never writes `persona_relationships`. Each exchange with an appraisal-eligible counterpart records a `meaningful_interaction` event (outcome and significance from the counterpart's appraisal, its reason in the payload). Mistreatment is recorded as a `treatment` event. Both use that plan's §2.2 envelope, with `personality/relationship_store.append_events` as the target. Until that plan is built, events wait in the inbox's outbox (`inbox_relationship_events`) in the same shape, ready to append. The bounds 0.03 per exchange and 0.06 per promise become suggested starting values for its policy. The voice reads trust only through `relationship_context` once it exists, and is neutral before.
- D16: Role-talk promises use `promise_made` / `promise_resolved`: issuer, beneficiary, a measurable criterion (a depth-chart slot by a week), the due time, and a resolution read from the actual depth chart. The trade-requests plan consumes them.
- D17: Trust is one-way. The inbox never reads a relationship as mutual and never adopts legacy tags.
- D18 (revised later on 2026-11-03, PERSONALITY_PLAN D12–D13): every persona, real-inspired included, may appraise; existing players get generated profiles at the 2026 start, and real coaches appraise once their reviewed profiles exist (neutral voice and no appraisal before). Original text: Existing real players have no personality profiles (draft-prospect-only scope, PERSONALITY_PLAN §5.1; JR is reconsidering). They reply in the neutral voice and get no appraisal. A broken promise to one is recorded as a fact with no invented private reaction. Real coaches get reviewed profiles later (D11 there); until then the coach voice stays neutral and coaches give no appraisal.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `engines/gm_negotiation.py:425` `open_negotiation`, `:460` `offer`, `:503` `accept_counter`, `:529` `close` already run multi-turn trade talks with a GM (spec §3.3f). Only `#debug` reaches them (`server/debug_api.py:350`, `:374`–`:409`). | Trade talk needs no new engine. `GM_TALK` wraps these functions; V2.11 extends the same file. |
| S2 | `engines/gm_negotiation.py:434`–`:440` refuses unless `gm_team_settings.user_controlled` is true for JR's team and false for the other. | This disagrees with D6. V2.11 §2.10 moves the GM engines to the registry. Until then the inbox checks the registry first and shows `gm_negotiation`'s refusal if the flag disagrees (R2). |
| S3 | `engines/gm_negotiation.py:306` `gm_message`: fact sheet → LLM JSON `{"message"}`, kept only if `engines/gm_rationale.py:184` `validate_rationale` passes, else `template_message`. | `voice.py` reuses this pattern and the validator for every kind. |
| S4 | `server/franchise_api.py:1266` `propose` scores an extension or re-sign with `_score` (`:945`, which calls `engines/free_agency_engine.py:188` `offer_utility` and `:230` `acceptance`) after `_eligibility` (`:1237`), and stores `contract_offers` (`db/init_db.sql:578`). `pipeline/league_office.py:1156` `step_accepted_offers` writes JR's ACCEPTED offers to the save. | A contract conversation is real today. |
| S5 | `server/franchise_api.py:350` `stored_depth_chart`, `:314` `pending_moves`; `:1021` `put_depth_chart` is the only path that queues a `DEPTH_CHART` move. | `ROLE_TALK` only reads charts to check promises; it never writes one (D11). |
| S6 | `engines/depth_chart_engine.py:328` `staff_depth_chart`; `:278` `coach_trust` is scaled by `COACH_TRUST_SCALE = 0.0` (`:167`, "plan D4: coach traits ship at 0"). | "This player should start" is computable. A vision's playing-time setting has no consumer while the scale is 0 (§2.9, Q-open 1). |
| S7 | `engines/gm_direction.py:119` `assign_directions` overrides: projected wins ≥ 10.5 is always CONTEND; a team over the cap can't REBUILD. `:265` `compute` keeps the previous direction between checkpoint weeks. `:182` `positional_needs` ranks needs by `TRADE_POS_WEIGHT`. There is no pin. Tests: `tests/test_gm_direction.py`. | A vision's direction and emphasis need two small, pure additions to `gm_direction` (§2.9). |
| S8 | The canonical league starts in 2026 from the real 2025 NFL seed (`V2_MASTER_PLAN.md` §7.2). Head coaches (`coaches`, `coach_tenures`) and GMs (`gm_profiles`, seeded by `engines/gm_personas.py`) are separate personas, and `gm_profiles.coach_id` is NULL unless a team gives its coach full control. Spec §3.3f's Whitfield/Grant coach-GMs come from the discarded 2026–2036 universe. | Vision talks have a counterpart on almost every team. They are skipped only where `coach_id` is set, or where JR controls both the coach and the GM. |
| S9 | `personality/core.py:144` `feature_view` gives a neutral view for a missing profile; `personality/store.py:85` `ensure` has no caller outside tests, so no player or coach profile is seeded. Writers and GMs have sliders and traits in `persona_sliders`/`persona_traits`. | GMs and writers speak in character. Players and coaches are neutral until their personalities are switched on. |
| S10 | `persona_relationships` (`db/init_db.sql:1702`) and `newsroom/relationships.py` are the legacy writer-trust store. [`PERSONALITY_RELATIONSHIPS_PLAN.md`](../done/PERSONALITY_RELATIONSHIPS_PLAN.md) (not built; §0b binds consumers) owns trust: consumers record canonical events (§2.2 envelope: `schema_version, universe_id, event_id, source_event_id, source_kind, source_refs, kind, occurred_at {season, tick}, subject, target, actor, basis, confidence, reviewer, evidence_scope, visibility, attribution, payload, supersedes_source_event_id`) through `personality/relationship_store.append_events(cur, events, *, expected_head, team_registry)`, and read trust through `relationships.relationship_context(state, *, target)`. Trust is one-way; legacy tags are never adopted. Its migration is `2027_01_18_personality_relationships.sql`. | The inbox never writes `persona_relationships`. It records events in that envelope (`appraisal.py`), holds them in its own outbox table until the store exists, and reads trust only through `relationship_context` (neutral before). |
| S11 | `agent_memories` (`db/init_db.sql:1911`) has `assigned_team_id`, `display_name`, `person_key`; `LOCAL_BEAT_REPORTER` is an archetype in `newsroom/config/agent_personas.json`. | The interviewer is the team's beat writer, in his own voice. |
| S12 | `engines/gm_trade_engine.py:1447`–`:1448`, `:1621` flag ENGINE proposals that involve a user-controlled team (via `gm_team_settings`); `engines/front_office.py:492` `approve`, `:508` `reject`. | `GM_TALK`'s trigger reads PROPOSED ENGINE proposals involving a registry user team where JR controls the GM, whatever the flag says. |
| S13 | `server/franchise_api.py:213` `user_teams()` reads `db/franchises.py:169` `active_entry()`; `:223` `require_user_team`. | The same reader and the same 403 rule apply. |
| S14 | `pipeline/weekly_capture.py:560` `run_weekly_engines` runs `step_gm`, scouting, archive and carousel. The watcher runs no engines in the offseason. In 2026–2033 there's no user team at all (`V2_MASTER_PLAN.md` §7.2). | The weekly hook goes after `step_gm`, with a scan when JR opens the inbox. Before 2034 the inbox is simply empty. |
| S15 | `Mu_Network/src/components/franchise/FranchisePage.tsx:23` `MODULES`. | The Inbox is one more entry. |
| S16 | `engines/gm_llm.py:60` `make_llm(label)`, default `gemini-3.8-flash`; it has no timeout. | Replies run under a time budget, and the template is used past it. |
| S17 | Sibling plans build on this one: `PRESS_CONFERENCE_PLAN.md` (`press.py`, reads `jr_statements`, migration `2027_01_20`), `SCOUTING_REVAMP_PLAN.md` (`prospect.py`, `scout.py`, FK to `inbox_threads`, `2027_01_21`), `TRADE_NEGOTIATIONS_PLAN.md` (extends `gm_talk.py`, `2027_01_22`). | `Kind`, `Trigger`, `jr_statements` and the thread columns they name stay compatible; new fields have defaults. |

## 1. Scope

**In:**
- Taking and releasing control of personas (`controlled_personas`). Only registry user teams count. There is no default.
- Thread and message storage, one inbox per user team, unread state, expiry, caps.
- A registry of conversation kinds that sibling plans extend by adding one file.
- Five kinds: `GM_TALK`, `CONTRACT`, `ROLE_TALK`, `VISION`, `REPORTER`. A sixth, `OWNER` (an owner → JR's GM), comes later from the [owners, scouts and voters plan](../in_progress/OWNERS_SCOUTS_VOTERS_PLAN.md).
- `team_vision` with two engine consumers: a direction pin and a needs emphasis in `gm_direction`.
- Inbound messages from engine facts each week, and when JR opens the inbox.
- JR's replies: a button or form, an optional note, or typed text turned into a draft he confirms.
- Counterpart replies written by the model in the persona's voice, checked against the facts, with templates as fallback.
- Relationship events: after each exchange with an appraisal-eligible counterpart, a `meaningful_interaction` (or a `treatment` for mistreatment) with the agent's outcome, significance and reason; `promise_made` / `promise_resolved` for role-talk promises. Recorded in the relationships plan's envelope and appended to its store when it exists (D15–D17).
- `jr_statements`: JR's interview answers, for articles later.
- The Inbox module in `Mu_Network`.

**Out (non-goals):**
- Controlled-player mode (JR as a player talking to his coach, his GM or other teams). The tables and actor model allow it; no kind serves a PLAYER actor yet.
- Owner agents (§2.11).
- A consumer for the vision's playing-time setting (Q-open 1).
- Press conferences (V2.2), prospect interviews and scouts (V2.10), leaks, posture, CPU-to-CPU talks and queuing agreed trades (V2.11).
- Writers quoting JR's answers (V2.2 reads `jr_statements`).
- Computing or writing trust (the relationships plan's policy), and any effect of trust on a sim number.
- Holdouts and trade requests ([`PERSONALITY_TRADE_REQUESTS_PLAN.md`](../done/PERSONALITY_TRADE_REQUESTS_PLAN.md) consumes the `promise_made` / `promise_resolved` events).
- National insiders messaging JR.

## 2. Design

### 2.1 Controlled personas and the conversation matrix

A thread is always **(a persona JR controls) ↔ (a counterpart agent)**.

`engines/inbox/actors.py`:

```python
@dataclass(frozen=True)
class Actor:
    team_id: int; abbr: str
    role: str            # 'GM' | 'COACH' | 'PLAYER'
    persona_id: str      # gm_profiles.id | coaches.id | players.id, as text
    name: str

def user_teams() -> list[str]                          # db.franchises.active_entry()['user_teams'], upper case
def controlled(cur, abbr) -> list[Actor]               # active controlled_personas rows of a registry user team; else []
def actor_for(cur, abbr, role) -> Actor | None
def candidates(cur, abbr) -> list[dict]                # who JR could take control of: the open GM tenure and the head
                                                       # coach (players listed later, with controlled-player mode)
def take_control(cur, abbr, role, persona_id) -> Actor # 403 outside user_teams; 409 if the persona isn't on that team
def release_control(cur, abbr, role, persona_id) -> None   # active = false; open threads stay readable, can't be answered
```

There is **no default**. A user team with no active rows has no actors, so its inbox is empty and shows the "take control" panel. A GM whose `coach_id` matches the head coach is one candidate ("HC & GM"). Taking control of him creates both rows, and VISION is skipped for that team. CLI: `python3 -m engines.inbox control --team CAR [--role COACH|GM|PLAYER --persona ID] [--off]`. A PLAYER row is accepted, but no kind serves it yet.

**The matrix** (Now = this plan):

| JR controls | Counterpart | Kind | What decides | Status |
|---|---|---|---|---|
| GM | another team's GM | `GM_TALK` | `gm_negotiation` | **Now**; V2.11 deepens it |
| GM | own player | `CONTRACT` | free-agency utility (`_score`) | **Now** (player voice neutral until the [agent loops plan](./COACH_PLAYER_AGENT_LOOPS_PLAN.md)'s `player.respond_inbox`) |
| GM | own head coach | `VISION` | `team_vision.respond` (§2.9) | **Now** (coach voice neutral until the [real-coach profiles plan](../done/REAL_COACH_PROFILES_PLAN.md)'s `persona_for` exists); skipped when the coach is the GM or JR controls both |
| GM | beat writer | `REPORTER` | nothing; answers stored | **Now** |
| GM | draft prospect (combine interviews, top-30 visits) | `PROSPECT` (`ACTOR_ROLES = ('GM',)`) | scouting engines | V2.10 (sibling-owned); none if JR doesn't control that team's GM (D14) |
| GM | team scout (notices, shortlists) | `SCOUT` (`ACTOR_ROLES = ('GM',)`, neutral) | scouting engines | V2.10 (sibling-owned); none if JR doesn't control that team's GM (D14) |
| GM | owner | — | — | Later (§2.11) |
| Coach | own player | `ROLE_TALK` | staff chart (triggers), JR's chart (promise check) | **Now** (player voice neutral until the [agent loops plan](./COACH_PLAYER_AGENT_LOOPS_PLAN.md)'s `player.respond_inbox`) |
| Coach | own GM | `VISION` | `team_vision.respond` | **Now** (GM in character); same skips |
| Coach | beat writer | `REPORTER` | nothing | **Now** |
| Coach | the press panel | `PRESS` notice | V2.2 | V2.2 |
| Player | own coach / own GM / other GMs / beat writer | — | — | Later: controlled-player mode |

### 2.2 Who decides what: engines vs agents

The rule: **the engine decides what happens; the agent says it and judges how it was treated.** The agent never accepts, rejects or invents a number. If its words state anything that isn't on the fact sheet, or contradict the decision, they are thrown away and the template is used.

| Decision | Engine | Example | What the agent does |
|---|---|---|---|
| Contract: accept, counter or no, and the counter's terms | `free_agency_engine.offer_utility` + `acceptance` via `franchise_api._score` | JR's GM offers his WR 3 years, $40M, $15M guaranteed. The engine returns COUNTER at 3 years, $44M, $18M guaranteed. | "I want to stay, but I need a bit more security. Get to $18M guaranteed and we're done." It may use only numbers on the sheet. |
| Trade: accept, counter, reject or walk away | `gm_negotiation.respond` | JR offers a 2nd for a CB; the engine counters by adding JR's 5th. | Writes the GM's reply in his voice. |
| Team vision: is the direction allowed, does the other side agree | `team_vision.respond` (§2.9) over `gm_direction`'s rules | JR's coach asks the GM to REBUILD. The team isn't over the cap and isn't projected to 10.5 wins, and its D score is close to the REBUILD band, so the GM agrees. | "Fine. We'll sell veterans at the deadline and play the kids." |
| Should this player be starting? (role-talk trigger) | `depth_chart_engine.staff_depth_chart` vs JR's chart | The staff chart has the rookie at WR1 two weeks running; JR has him at WR3. | Writes the rookie's ask from facts on the sheet. |
| Was a promise kept? | the actual depth chart at the due week vs the promise's criterion | JR's coach promised "you'll start by week 9". At week 9 he's still WR3, so `promise_resolved` is `broken`. | Nothing: the resolution is a fact. Its effect on trust is the relationships policy's (D18 revised: every player with a profile can react). |
| Who writes to JR, and when | each kind's `scan` triggers | An ENGINE trade proposal names JR's LB, so a `TRADE_INTEREST` thread opens. | Writes the opening message. |
| How much trust moves | the relationships plan's reviewed policy, from recorded events | JR's GM answers a GM's counter with "Take it or leave it, I don't care." | The GM agent appraises it: outcome negative, significance routine, reason "brushed off my counter without discussing it", recorded as a `treatment` (`disrespect`). |

Every outcome above is the engine's, so a replay gives the same outcome. The agent's one free judgment is its appraisal, recorded as evidence; it moves no number here.

### 2.3 Typed text vs buttons and forms

- **Form:** JR picks "Make an offer", fills Years 3, Total $40,000,000, Guaranteed $15,000,000, and clicks Send.
- **Typed:** he writes "3 years, 40 million, 15 guaranteed". The inbox shows a draft: "Did you mean: **Offer 3 years, $40.0M total, $15.0M guaranteed, $0 bonus?** [Send offer] [Edit] [Just send as a message]". Only the confirm click acts.

`engines/inbox/parse.py`:

```python
def parse_contract_terms(text) -> dict | None              # rules: '3 years|yrs', '$40M|40 million', 'guaranteed|gtd', 'bonus'
def parse_with_llm(llm, text, schema, choices, budget_s) -> dict | None   # JSON validated against ranges; ids only from choices
def draft_summary(option_id, payload) -> str
```

Each kind's optional `draft(self, cur, thread, text, llm=None) -> {"option_id", "payload", "summary"} | None` does the matching:
- `CONTRACT`: the contract terms.
- `GM_TALK`: player names and picks matched against `gm_negotiation.team_assets`.
- `ROLE_TALK`: "you'll start / I'll move you up by week 9" becomes `PROMISE` with slot, depth and review week.
- `VISION`: "let's rebuild, lean defense" becomes `PROPOSE` with direction and emphasis.

No draft means the text is talk, or a kind's own reading of it (V2.10's typed questions).

### 2.4 How counterparts speak

Each counterpart line is written by the **model** in the persona's voice. It gets:
- the persona's sliders and traits, or "plain and professional" for a neutral persona;
- the fact sheet;
- the last 6 messages;
- JR's note;
- the decision it must convey;
- its relationship to JR's persona, only as `relationship_context(state, target=actor)` from the relationships plan once it exists and its policy is enabled; neutral (nothing) before.

A **template** (fixed text with the facts filled in) is used only on a timeout (`INBOX_LLM_BUDGET_S = 8.0`, an estimate set by the spike), a failed check (`validate_rationale`, the kind's optional `validate`, the intent check), `MU_INBOX_LLM=0`, or no API key. The UI tags template lines.

```python
def persona_view(cur, counterpart_type, counterpart_id) -> dict   # feature_view(profile, 'inbox'); missing -> neutral;
                                                                  # writers and GMs fall back to persona_sliders/traits
def write(cur, kind, thread, intent, facts, history, note=None, llm=None, budget_s=INBOX_LLM_BUDGET_S)
    -> tuple[str, str, dict | None]       # (text, 'LLM' | 'TEMPLATE', appraisal {"outcome", "significance", "mistreated", "reason"} | None)
def default_llm()                          # make_llm("inbox") unless MU_INBOX_LLM == '0'
```

One model call returns `{"message", "appraisal": {"outcome": "positive|negative|mixed", "significance": "routine|meaningful", "mistreated": bool, "reason"}}`, so appraising costs no extra call. The appraisal is asked for only when `appraisal.allowed(view)` (§2.5).

Order inside every reply:
1. `act` runs the engine and stores the decision with JR's message.
2. `write` produces the reply and the judgment within the budget.
3. The reply is stored.
4. `appraisal.record` stores the event (in the outbox).

### 2.5 Relationship events: the counterpart's appraisal as evidence (`engines/inbox/appraisal.py`)

[`PERSONALITY_RELATIONSHIPS_PLAN.md`](../done/PERSONALITY_RELATIONSHIPS_PLAN.md) owns trust (§0b there). The inbox never writes `persona_relationships` and never computes a trust number. It records what happened as that plan's canonical events (§2.2 envelope), and that plan's reviewed policy turns them into one-way trust.

```python
def allowed(view) -> bool
    # True for any counterpart with a profile or persona: real-inspired personas are fictional characters (PERSONALITY_PLAN
    # D13), so GMs, writers and players (generated profiles from the 2026 start, D12) all appraise. False only for real
    # coaches until their reviewed profiles exist (D11) and for anyone whose profile isn't loaded yet.
    # False -> no appraisal is asked for and no interaction event is recorded.
def interaction_event(thread, message, actor, counterpart, appraisal, at) -> dict
    # kind 'meaningful_interaction'; subject = the counterpart (PersonRef), target = actor = JR's persona (PersonRef);
    # basis 'verified_simulation', confidence 'medium', visibility 'private';
    # payload {relationship_kind: 'collaboration'|'support'|'conflict' (from outcome), outcome, significance,
    #          opportunity_ref: 'inbox_message:<id>', appraisal_reason, channel: 'inbox', inbox_kind}
def treatment_event(...) -> dict
    # kind 'treatment' when the appraisal says mistreated (rude, dismissive, insulting): treatment_kind 'disrespect',
    # outcome 'negative', significance from the appraisal, team_key of JR's team, outcome_refs ['inbox_message:<id>'],
    # attribution.responsible = [JR's persona], payload.appraisal_reason. One source gives one event, never both.
def promise_made(thread, coach, player, criterion, due_at) -> dict
    # payload {promise_id: 'inbox:<thread_id>:<n>', issuer: coach PersonRef, beneficiary: player PersonRef,
    #          terms {kind: 'role', description, criterion {type: 'depth_chart_slot', slot, max_depth}},
    #          due_at {season, tick}, authority_ref: 'inbox_thread:<id>'}; subject = player, target = actor = coach
def promise_resolved(thread, promise, chart_row) -> dict
    # payload {promise_id, resolution: 'kept'|'broken'|'unknown' (no chart read at the due week -> unknown, never broken),
    #          criterion_result {slot, depth_at_due, chart_source, chart_season, chart_week}, outcome_refs, beneficiary_ack_ref: None}
def record(cur, event) -> int            # inserts into inbox_relationship_events (outbox), idempotent on source_event_id
def flush(cur) -> dict                   # when personality.relationship_store imports and its table exists: resolve
                                         # PersonRefs (personality.store.resolve_alias), read_head, append_events(expected_head=...),
                                         # mark appended; conflicts and unresolved refs stay pending with a reason
```

- **Envelope details.** `source_event_id` is stable per occurrence (`inbox:msg:<message_id>`, `inbox:promise:<promise_id>:made`, `…:resolved`). `occurred_at` is `{season, tick}` from the relationships plan's calendar. Until that plan exists, the outbox also stores `{season, week, offseason_stage}` and the tick is filled at flush. Subjects and targets carry source refs (`gm_profiles:<id>`, `coaches:<id>`, `players:<id>`, `agent_memories:<agent_id>`) and are resolved to core `PersonRef`s at flush.
- **One-way.** Every event is about the counterpart's view of JR's persona (or the beneficiary's view of the issuer). The inbox never records or reads the reverse direction as implied, and never reads legacy tags.
- **What the appraisal judges:** how JR's persona treated the counterpart (respectful and in depth vs snippy or rude), not the engine's decision. Examples: a thoughtful answer to a beat writer's question; a curt "no comment" on a light question; an insulting lowball note; a coach explaining a benching versus "deal with it".
- **No model, no appraisal.** A template reply records no interaction event. Promise events are facts and are recorded with or without a model.
- **Existing players** (D18, revised): they have generated profiles from the 2026 start, so they speak in their own voice and appraise like anyone else. A player whose profile isn't loaded yet gets the neutral voice and no appraisal; his promises are still recorded as facts.
- **Suggested starting values for the relationships policy** (not used here): 0.03 per exchange and 0.06 per promise, the bounds this plan first proposed.
- **No sim effect.** Nothing here moves a number.

### 2.6 Tables (`db/migrations/2027_01_19_front_office_inbox.sql`, mirrored in `db/init_db.sql`)

```sql
CREATE TABLE IF NOT EXISTS controlled_personas (
    team_id     INT NOT NULL REFERENCES teams(id),
    role        VARCHAR(8) NOT NULL CHECK (role IN ('GM','COACH','PLAYER')),
    persona_id  VARCHAR(40) NOT NULL,              -- gm_profiles.id | coaches.id | players.id
    active      BOOLEAN NOT NULL DEFAULT true,
    taken_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ,
    PRIMARY KEY (team_id, role, persona_id)
);   -- JR controls no one by default (D8); rows of teams outside the registry's user_teams are ignored

CREATE TABLE IF NOT EXISTS inbox_threads (
    id                SERIAL PRIMARY KEY,
    team_id           INT NOT NULL REFERENCES teams(id),
    kind              VARCHAR(24) NOT NULL,                    -- registry key; no CHECK (sibling plans add kinds)
    actor_role        VARCHAR(8) NOT NULL CHECK (actor_role IN ('GM','COACH','PLAYER','TEAM')),
    actor_id          VARCHAR(40) NOT NULL,                    -- the controlled persona; 'TEAM' + abbr for notices
    counterpart_type  VARCHAR(16) NOT NULL
        CHECK (counterpart_type IN ('gm','coach','player','writer','prospect','scout','owner','system')),
    counterpart_id    VARCHAR(100) NOT NULL,                   -- gm_profiles.id, coaches.id, players.id, agent_memories.agent_id, prospect_uid (V2.10: draft_prospects.id changes on a board re-scan; the permanent prospect ID, engines/prospect_ids.py)
    counterpart_name  VARCHAR(255) NOT NULL,
    subject           VARCHAR(255) NOT NULL,
    status            VARCHAR(12) NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','WAITING_JR','CLOSED','EXPIRED')),
    started_by        VARCHAR(12) NOT NULL CHECK (started_by IN ('JR','COUNTERPART','SYSTEM')),
    source_ref        VARCHAR(120) NOT NULL,
    link_table        VARCHAR(48),
    link_id           INT,
    state             JSONB NOT NULL DEFAULT '{}',             -- kind-owned: {"questions_asked": 4}, {"promises": [...]}
    season            INT NOT NULL,
    week              INT,
    offseason_stage   VARCHAR(16),
    expires_season    INT,
    expires_week      INT,
    unread            BOOLEAN NOT NULL DEFAULT true,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (team_id, kind, source_ref)
);
CREATE INDEX IF NOT EXISTS inbox_threads_team_status ON inbox_threads (team_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS inbox_messages (
    id           SERIAL PRIMARY KEY,
    thread_id    INT NOT NULL REFERENCES inbox_threads(id) ON DELETE CASCADE,
    seq          SMALLINT NOT NULL,
    author       VARCHAR(12) NOT NULL CHECK (author IN ('JR','COUNTERPART','SYSTEM')),
    body         TEXT NOT NULL,
    body_source  VARCHAR(10) NOT NULL CHECK (body_source IN ('JR','LLM','TEMPLATE','ENGINE')),
    option_id    VARCHAR(40),
    options      JSONB NOT NULL DEFAULT '[]',
    facts        JSONB,                       -- never sent to the UI
    engine_ref   JSONB,
    appraisal    JSONB,                       -- the counterpart's appraisal of the JR message this replies to (NULL = none)
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (thread_id, seq)
);

CREATE TABLE IF NOT EXISTS inbox_relationship_events (     -- outbox for PERSONALITY_RELATIONSHIPS_PLAN's store
    id               SERIAL PRIMARY KEY,
    source_event_id  VARCHAR(120) NOT NULL UNIQUE,         -- 'inbox:msg:812', 'inbox:promise:44:1:made'
    kind             VARCHAR(32) NOT NULL CHECK (kind IN ('meaningful_interaction','treatment','promise_made','promise_resolved')),
    thread_id        INT REFERENCES inbox_threads(id) ON DELETE SET NULL,
    message_id       INT REFERENCES inbox_messages(id) ON DELETE SET NULL,
    event            JSONB NOT NULL,                       -- the full §2.2 envelope (tick and PersonRefs filled at flush)
    sim_time         JSONB NOT NULL,                       -- {season, week, offseason_stage}
    status           VARCHAR(10) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPENDED','REJECTED')),
    status_reason    TEXT,
    appended_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS team_vision (
    team_id       INT NOT NULL REFERENCES teams(id),
    season        INT NOT NULL,
    direction     VARCHAR(8) CHECK (direction IN ('CONTEND','RETOOL','REBUILD')),   -- NULL = no pin
    emphasis      VARCHAR(8) NOT NULL DEFAULT 'BALANCED' CHECK (emphasis IN ('OFFENSE','DEFENSE','BALANCED')),
    playing_time  VARCHAR(8) NOT NULL DEFAULT 'MERIT' CHECK (playing_time IN ('YOUTH','VETERANS','MERIT')),
    thread_id     INT REFERENCES inbox_threads(id) ON DELETE SET NULL,
    agreed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, season)
);
```

Option shape: `{"id", "label", "form": "contract_terms" | "trade_assets" | "promise" | "vision" | null, "needs_note", "default"}`.

### 2.7 Registry (`engines/inbox/registry.py`)

```python
@dataclass(frozen=True)
class Trigger:
    kind: str; team_id: int; counterpart_type: str; counterpart_id: str; counterpart_name: str
    subject: str; source_ref: str; facts: dict; intent: str
    link_table: str | None = None; link_id: int | None = None
    expires_season: int | None = None; expires_week: int | None = None
    actor_role: str | None = None; actor_id: str | None = None
    state: dict | None = None

@dataclass
class ActResult:
    facts: dict; intent: str; engine_ref: dict | None
    close: bool = False; link: tuple | None = None; state: dict | None = None
    judge: bool = True               # False: the counterpart doesn't judge this exchange (e.g. a SYSTEM line)

class Kind(Protocol):
    KIND: str
    COUNTERPART_TYPE: str
    ACTOR_ROLES: tuple = ('GM', 'COACH')     # optional; missing -> scanned once per team with its first actor, or 'TEAM'
    def scan(self, cur, season, week, team) -> list[Trigger]: ...       # team carries 'actor'
    def options(self, cur, thread, last) -> list[dict]: ...
    def act(self, cur, thread, option_id, payload, note) -> ActResult: ...
    def template(self, intent, facts) -> str: ...
    def start(self, cur, team, body) -> Trigger: ...
    # Optional: validate(text, facts) -> bool;  draft(cur, thread, text, llm=None) -> dict | None

def kinds() -> dict[str, Kind]        # auto-discovery in engines/inbox/kinds/ (pkgutil)
```

`act(option_id=None, note=...)` is talk by default. A kind may read it otherwise (V2.10's typed questions).

### 2.8 The five kinds

| Kind | Actor → counterpart | Inbound triggers | JR's options | Engine behind `act` |
|---|---|---|---|---|
| `GM_TALK` | GM → another GM | `TRADE_INTEREST`: a PROPOSED ENGINE proposal with JR's team (registry, JR controls its GM) on one side, not yet in a thread | Inbound: `OPEN_TALKS` (form `trade_assets`), `NOT_INTERESTED`. In talks: `MAKE_OFFER`, `ACCEPT_COUNTER`, `END_TALKS` | `gm_negotiation` functions and `front_office.reject`. The GM's text is the turn message `gm_negotiation` writes. The judgment comes from a short separate call on JR's note when there is one; none without a note. |
| `CONTRACT` | GM → own player | `EXTENSION_WINDOW`: a starter becomes eligible with no open thread | `MAKE_OFFER` (`contract_terms`), `ACCEPT_COUNTER`, `WALK_AWAY` | `franchise_api` helpers `_context`, `_player`, `_eligibility`, `_score`, `insert_offer` (lazy import, R4). ACCEPTED closes the thread, and the league office writes the contract. |
| `ROLE_TALK` | COACH → own player | `LOST_STARTING_JOB` (depth 1 at a base slot last week, not now); `WANTS_MORE` (the staff chart starts him and JR's chart hasn't, 2 straight weeks) | `PROMISE` (form `promise`: slot, depth he'll reach, review week ≥ next week, default week + `PROMISE_REVIEW_WEEKS = 2`), `EXPLAIN` (note required), `NO_CHANGE` | **No chart write** (D11). `PROMISE` adds `{promise_id, slot, max_depth, due_season, due_week, made_at_depth}` to `state.promises` and records `promise_made` (§2.5). At the due week, the scan reads the actual chart (`stored_depth_chart`, the newest chart in force: JR, else MU_COACH, else MADDEN). He is kept if he is at or above the promised depth; with no chart, the result is `unknown`. It records `promise_resolved`, and a SYSTEM line says "kept", "not kept" or "couldn't check". The thread links to `#franchise-depth-chart` for the real move. |
| `VISION` | COACH → own GM, or GM → own head coach | `SEASON_VISION`: the first scan of a season with no `team_vision` row. `DIRECTION_SHIFT`: `gm_direction`'s own band moved away from the pinned direction at a checkpoint. Skipped when the coach is the GM, or JR controls both | `PROPOSE` (form `vision`: direction, emphasis, playing time), `ACCEPT_COUNTER`, `KEEP_CURRENT` | `team_vision.respond` (§2.9). AGREED upserts `team_vision` and closes the thread. JR-started any time; one open per team. |
| `REPORTER` | any controlled persona → the team's beat writer | `INTERVIEW_REQUEST` on: a starter out 4+ weeks, an executed trade or signing, a 3-game streak, a starter losing his job. At most 2 open per team | Per question: `ON_RECORD` (note), `OFF_RECORD` (note), `NO_COMMENT`; any time `END_INTERVIEW` | None. **Open-ended** (D12): after each answer the reporter either asks a follow-up from the facts or wraps up (`"done": true` in the model's JSON; the template asks up to 3 then wraps up). Hard cap `INTERVIEW_MAX_QUESTIONS = 10`. JR-started with a topic. |

Caps: `MAX_OPEN_PER_KIND = 3` per team for `GM_TALK`, `CONTRACT` and `ROLE_TALK`; 2 for `REPORTER`; 1 for `VISION`. Expiry: with the source, else `INBOX_STALE_WEEKS = 2` weeks without a reply. A thread whose actor was released stays readable and can't be answered.

`jr_statements(cur, team_id, season, week=None) -> list[dict]`: one row per answered question: `{thread_id, team_id, season, week, writer_agent_id, writer_handle, subject, question, answer, on_record, actor_role, actor_name, created_at}`. NO_COMMENT isn't a statement.

### 2.9 Team vision (`engines/team_vision.py`, `engines/gm_direction.py`)

A vision is the coach and the GM agreeing on three settings. Each option is one the engine allows.

| Setting | Allowed options | What it changes |
|---|---|---|
| Direction | CONTEND / RETOOL / REBUILD, minus `assign_directions`' overrides (projected wins ≥ 10.5 → only CONTEND; over the cap → no REBUILD) | `gm_direction.compute` uses the pinned direction for that team and season, at any week, and the overrides still win. Direction feeds trade proposals, needs (the expiring multiplier) and the hot seat's expected wins. |
| Emphasis | OFFENSE / DEFENSE / BALANCED | `positional_needs(..., emphasis=)` multiplies the emphasised side's group scores by `VISION_EMPHASIS_WEIGHT = 1.15` (offense: QB, RB, WR, TE, OT, IOL; defense: EDGE, DT, LB, CB, S) |
| Playing time | YOUTH / VETERANS / MERIT | Stored only. Its eventual consumer (depth-chart score adjustments) is owned by [`PERSONALITY_DEVELOPMENT_COACHING_PLAN.md`](../done/PERSONALITY_DEVELOPMENT_COACHING_PLAN.md), not this plan (O1). |

```python
VISION_MARGIN = 0.5                    # D-score units; estimate
def allowed(scores_row, cap_space) -> dict          # {'direction': [...], 'emphasis': [...], 'playing_time': [...]}
def respond(cur, team_id, season, proposal, counterpart) -> dict
    # {'decision': 'AGREE'|'COUNTER'|'REFUSE', 'vision': {...}, 'reasons': [...]}
    # direction not allowed -> REFUSE with the override as reason. Allowed and equal to the engine's band -> agree.
    # Else agree if |D - nearest edge of the proposed band| <= VISION_MARGIN x lean, where lean = 0.5 + risk_appetite
    # for a GM counterpart, 1.0 for a coach (no profile); otherwise COUNTER with the engine's band.
    # Emphasis: agree unless the team's top 3 needs are all on the other side -> counter BALANCED. Playing time: agree.
def current(cur, team_id, season) -> dict | None
def upsert(cur, team_id, season, vision, thread_id)
```

`gm_direction` changes, pure and opt-in:
- `read_league` adds `league["visions"]` (guarded `to_regclass('team_vision')`).
- `compute` applies a pin through a new `apply_pin(pin, score_row, cap_space) -> (direction, reason)`, with the reason `"vision"` or the override that beat it.
- `positional_needs` takes `emphasis=None`.

With no `team_vision` rows, every output is byte-for-byte what it is today (a test checks this).

Example: JR controls Carolina's head coach; the GM is a CPU agent with `risk_appetite` 0.7. In the preseason JR proposes REBUILD, DEFENSE, YOUTH. The team is under the cap and projected for 7 wins, so REBUILD is allowed. D is 0.4 from the REBUILD edge, within 0.5 × 1.2 = 0.6, so the GM agrees. The engine pins REBUILD for the season, defensive needs rank 15% higher, and the GM's reply says so in his voice.

### 2.10 Scan (`engines/inbox/scan.py`)

```python
def scan(cur, season, week, teams=None, llm=None) -> dict   # {'created', 'expired', 'reviewed', 'by_kind'}
    # For each registry user team, each controlled actor, each kind serving its role: kind.scan -> Trigger ->
    # store.create_thread (ON CONFLICT DO NOTHING) -> opening message via voice.write. Then due promises (ROLE_TALK ->
    # promise_resolved), store.expire_stale, appraisal.flush. One failing kind is logged and skipped.
def step_inbox(season)   # pipeline/weekly_capture.py after step_gm; voice.default_llm(); never raises
```

Opening the inbox runs `scan(..., teams=[abbr], llm=None)`: fast templates, at most once per (team, season, week, stage) per server process. Promise resolutions and the outbox flush run in the weekly run or the CLI (`flush-events`). CLI: `python3 -m engines.inbox scan [--season S --week W] [--team CAR] [--no-llm] [--dry-run]`, `list`, `show <id>`, `control ...`.

### 2.11 Owner agents: options for later

| Owner could own | Engine data | Exists today? |
|---|---|---|
| **Cap and budget mandate** (first, D3) | `team_cap_seasons`, `contracts_engine.next_cap`, a veto check before `_score` | Data yes; the check is new |
| **Job security, hiring and firing** (first, D3) | `engines/coach_hot_seat.py`, `engines/gm_rollover.py` (`EXPECTED_WINS`, `fo_score`), `coaching_carousel` | Scores yes; the owner delivers them and the score decides |
| Direction mandate | `gm_direction` D score; the §2.9 pin | Pin built here |
| Patience with a rebuild | `gm_team_seasons.direction` history, `team_season_results` | Data yes |
| Stadium, tickets, revenue | `engines/market_constants.py`, `popularity_engine` | No revenue engine |
| Big-decision sign-off | `contract_offers`, `gm_trade_proposals`, `trade_value` | Data yes; an approval step is new |

### 2.12 API (`server/inbox_api.py`, router prefix `/api/franchise`)

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/{abbr}/inbox/actors` | — | `{"controlled": [Actor], "candidates": [{role, persona_id, name, also}]}` |
| POST | `/{abbr}/inbox/actors` | `{"role": "COACH", "persona_id": "41"}` | 201 `Actor` (403 outside `user_teams`, 409 not on the team) |
| DELETE | `/{abbr}/inbox/actors/{role}/{persona_id}` | — | 204 |
| GET | `/{abbr}/inbox` | `?status=&kind=&as=GM\|COACH&limit=50` | `{"team", "actors": [Actor], "unread", "threads": [Thread]}` (empty with no actors) |
| GET | `/{abbr}/inbox/kinds` | — | kinds a controlled actor can use: `[{"kind", "actor_roles", "can_start", "start_form"}]` |
| GET | `/{abbr}/inbox/{thread_id}` | — | `{"thread", "messages", "options"}` (marks read) |
| POST | `/{abbr}/inbox/{thread_id}/draft` | `{"text"}` | `{"draft": {...} \| null}` (acts on nothing) |
| POST | `/{abbr}/inbox/{thread_id}/reply` | `{"option_id", "payload", "note"}` | the thread with new messages |
| POST | `/{abbr}/inbox` | `{"kind", "as", ...kind fields, "note"}` | 201 the new thread |
| POST | `/{abbr}/inbox/{thread_id}/close` | — | the thread |

`Actor = {role, id, name}`. `Thread` adds `actor` and `counterpart` and leaves out `state`. `Message = {id, seq, author, body, source, option_id, engine_ref, appraisal: {outcome, reason} | null, created_at}`; it never includes `facts` or a GM's surplus. Errors use `{"detail"}`: 400, 403 (team not in the registry, or `as` not a controlled role), 404, 409. Notes are at most 600 characters.

### 2.13 Frontend

- `Mu_Network/src/types/franchiseInbox.ts`: the §2.12 shapes.
- `Mu_Network/src/components/franchise/InboxModule.tsx` (`{ teamAbbr }`):
  - **Take control:** with no actors, a panel lists the candidates (GM, head coach) with "Take control". A small "Personas" menu does the same later, and releases.
  - **List:** an "acting as" filter, open/all, kind chips, unread dots.
  - **Conversation:** messages, with template tags and a small appraisal marker on counterpart replies that carry one (+/−/~ and the reason on hover; no trust number); options, forms (`contract_terms`, `trade_assets`, `promise`, `vision`), a text box, the draft card, and a "Go to Depth Chart" link in role talks.
  - **New conversation:** a picker from `/inbox/kinds`.
- `FranchisePage.tsx`: the Inbox tab after Game Center, with the unread count.

### 2.14 How sibling plans plug in

| Sibling | Adds | Shared files |
|---|---|---|
| V2.11 | Extends `gm_talk.py` (posture, time limit, leaks, queued trades); moves the GM engines to the registry (fixes S2) | `gm_talk.py` after this plan merges |
| V2.10 | `prospect.py`, `scout.py` (neutral), both `ACTOR_ROLES = ('GM',)` (D14); FKs to `inbox_threads` | none |
| V2.2 | `press.py` (notice only); reads `jr_statements`; presser answers are recorded as relationship events the same way (D15) | none |

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Shared files with the sibling plans: `db/init_db.sql`, `server/api.py`, `pipeline/weekly_capture.py`, `FranchisePage.tsx`, `gm_talk.py`. | Siblings start after this plan (D5). | Siblings read the built code first. |
| R2 | `gm_negotiation` checks `gm_team_settings.user_controlled` until V2.11. | `test_inbox_kinds_gm_talk.py` covers a mismatch. | The thread shows its 400 text; JR ticks the flag on `#debug` until V2.11 lands. |
| R3 | Model latency beside Madden. | Spike; `voice.write` logs time and source. | Tune the budget; decisions are stored first. No model means no appraisal event. |
| R4 | `CONTRACT` and `ROLE_TALK` import private `franchise_api` helpers. | Their tests import them by name. | A later plan that owns `franchise_api.py` moves them to an engine module. |
| R5 | The model's appraisals drift (everything rude, or everything positive), or take the engine's decision personally. | Tests with fixed fake appraisals; the orchestrator's `llm_compare` run includes 10 polite and 10 rude synthetic exchanges. | Prompt fixes. Appraisals are evidence only, and the relationships policy (mode off/observe/enabled) decides whether they ever move trust. |
| R11 | The relationships plan isn't built when the inbox ships, or its envelope changes in review. | `flush` is guarded (import + table check); outbox rows keep the full envelope. | Events wait in `inbox_relationship_events` (PENDING); a small mapping fix at flush if the envelope changes. Nothing is lost. |
| R6 | The vision pin changes league-wide outputs. | A test: with no `team_vision` rows `compute` is unchanged; with a pin only that team changes. | Delete the row; the next run reverts. |
| R7 | A pinned direction raises or lowers the hot seat's expected wins (`EXPECTED_WINS` by direction), so JR's vision affects the coach's job security. | Noted in the vision template ("we'll be judged as a rebuilding team"). | Intended; flag for JR (Q-open 2). |
| R8 | The model writes a fact that isn't on the sheet, or contradicts the decision. | Validators; tests feed bad outputs. | Template. |
| R9 | A parsed draft is wrong. | The confirm card shows full terms. | Edit, or send as a message. |
| R10 | Too many inbound messages. | Scan counts on a busy synthetic week. | Caps (§2.8). |

**Spike first** (30 minutes on the PC before wave 2; doesn't block wave 1): run `make_llm("inbox")` 20 times on a filled `CONTRACT` prompt with the judgment JSON, with Madden open, and record p50 and p95. This sets `INBOX_LLM_BUDGET_S`. After the last wave the orchestrator runs the `llm_compare` gate (replies and judgments) before closing the plan (D7).

## 4. Tests (synthetic, no live DB)

- `tests/test_inbox_actors.py` → no rows → no actors (D8); take/release; a team outside the registry → 403; a persona not on the team → 409; the coach-GM candidate creates both rows; PLAYER rows are listed but served by no kind.
- `tests/test_inbox_store.py` → idempotent thread creation; `seq`; expiry; caps; `jr_statements` shape; released actor → thread read-only.
- `tests/test_inbox_registry.py` → auto-discovery; no `KIND` ignored; duplicate raises; missing `ACTOR_ROLES` → once per team.
- `tests/test_inbox_voice.py` → neutral view for a missing profile; GM and writer views; invented money, player or contradiction → template; slow fake model → template within the budget; `MU_INBOX_LLM=0`; the appraisal parsed from the same JSON and asked for only when `appraisal.allowed`; without `relationship_context` the prompt has no relationship lines.
- `tests/test_inbox_appraisal.py` → envelope fields for each of the four kinds; one source gives one event (interaction or treatment, never both); `allowed` true for GMs, writers and players with profiles, false for real coaches without reviewed profiles and for unloaded profiles; promise events are recorded either way; `unknown` resolution with no chart; idempotent on `source_event_id`; `flush` is a no-op without the relationships store, appends with `expected_head` when it exists (fake store), and leaves conflicts and unresolved refs PENDING with a reason; nothing writes `persona_relationships`.
- `tests/test_inbox_parse.py` → contract phrasings; "40" alone → None; the model fallback rejects unknown ids and out-of-range numbers; summaries.
- `tests/test_inbox_scan.py` → triggers once per actor role; no actors → nothing; reruns create nothing; promise reviews run once; failing kinds skipped.
- `tests/test_weekly_capture_inbox.py` → `step_inbox` after `step_gm`; errors logged.
- `tests/test_inbox_kinds_gm_talk.py` → trigger from a registry team where JR controls the GM, flag or not; reject; talk options call `gm_negotiation`; mismatch text; drafting from `team_assets`; no surplus in the JSON.
- `tests/test_inbox_kinds_contract.py` → `_score` path; intents; counter; ineligible → 409; drafting; the helpers exist.
- `tests/test_inbox_kinds_role_talk.py` → triggers; `PROMISE` writes no move (no `franchise_pending_moves` insert); a due promise with the player moved up → `promise_resolved` kept, without → broken, with no chart → unknown; `promise_made` carries issuer, beneficiary, criterion and due time; only COACH actors.
- `tests/test_inbox_kinds_reporter.py` → beat writer chosen; continues until the model says done; template wraps up after 3; hard stop at 10; `END_INTERVIEW`; ON_RECORD needs a note; statements stored.
- `tests/test_inbox_kinds_vision.py` → skipped for a coach-GM team and when JR controls both; COACH and GM actor sides; `PROPOSE` → AGREE / COUNTER / REFUSE from `team_vision.respond`; AGREE upserts `team_vision`; `DIRECTION_SHIFT` fires once.
- `tests/test_team_vision.py` → `allowed` under each override; `respond` margin with `risk_appetite`; emphasis counter; `current`/`upsert`.
- `tests/test_gm_direction.py` (extended) → no visions → identical output; a pin applies at a non-checkpoint week; overrides beat the pin; emphasis × 1.15 on the right groups.
- `tests/test_inbox_api.py` → every §2.12 shape; actors endpoints; empty inbox with no actors; `/draft` never acts; errors; appraisal shown on messages, no trust number.

Run with `pytest -m "not integration" tests/test_inbox_*.py tests/test_team_vision.py tests/test_gm_direction.py tests/test_weekly_capture_inbox.py`. Frontend: `npm run build` in `Mu_Network/`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Inbox core (tables, actors, store, registry, voice, appraisal events, parse, scan, CLI, weekly hook)

- **Owns:**
  - New: `db/migrations/2027_01_19_front_office_inbox.sql`; `engines/inbox/__init__.py`, `__main__.py`, `actors.py`, `store.py`, `registry.py`, `voice.py`, `appraisal.py`, `parse.py`, `scan.py`; `engines/inbox/kinds/__init__.py` (empty).
  - New tests: `tests/test_inbox_actors.py`, `test_inbox_store.py`, `test_inbox_registry.py`, `test_inbox_voice.py`, `test_inbox_appraisal.py`, `test_inbox_parse.py`, `test_inbox_scan.py`, `test_weekly_capture_inbox.py`.
  - Existing: `db/init_db.sql`, `pipeline/weekly_capture.py`.
- **Reads:** §2.1–§2.8, §2.10; `db/franchises.py:160-180`; `engines/gm_negotiation.py:300-325`; `engines/gm_rationale.py:184`; `engines/gm_llm.py`; `personality/core.py`, `personality/store.py`; `newsroom/personas.py`; `docs/done/PERSONALITY_RELATIONSHIPS_PLAN.md` §0b, §2.1–§2.2 (envelope, `append_events`, `relationship_context`); `pipeline/weekly_capture.py:540-576`; the sibling plans' inbox sections.
- **Depends on:** none.
- **Done when:** the migration applies to `madden_test` and is in `init_db.sql`; the CLI lists scan/list/show/control; the eight test files pass.
- **Verify:** `pytest -m "not integration" tests/test_inbox_actors.py tests/test_inbox_store.py tests/test_inbox_registry.py tests/test_inbox_voice.py tests/test_inbox_appraisal.py tests/test_inbox_parse.py tests/test_inbox_scan.py tests/test_weekly_capture_inbox.py` all pass.

### U2: Inbox frontend module

- **Owns:** `Mu_Network/src/types/franchiseInbox.ts` (new), `Mu_Network/src/components/franchise/InboxModule.tsx` (new), `Mu_Network/src/components/franchise/FranchisePage.tsx`.
- **Reads:** §2.3, §2.12–§2.13; `FranchisePage.tsx`, `ContractRoomModule.tsx`, `TradeMachineModule.tsx`, `DepthChartModule.tsx`, `types/franchiseMarket.ts`.
- **Depends on:** none (builds against §2.12's fixed JSON).
- **Done when:** with mocked fetches, `#franchise-inbox` renders the take-control panel, the list, a thread, appraisal markers and the draft card; the build passes.
- **Verify:** `cd Mu_Network && npm run build` exits 0.

### U3: Coach-player and interview kinds

- **Owns:** `engines/inbox/kinds/role_talk.py` (new), `engines/inbox/kinds/reporter.py` (new), `tests/test_inbox_kinds_role_talk.py` (new), `tests/test_inbox_kinds_reporter.py` (new).
- **Reads:** §2.2–§2.8; U1's package; `server/franchise_api.py:314-360`; `engines/depth_chart_engine.py:39-45`, `:328-420`; `agent_memories` (`db/init_db.sql:1911`); `docs/done/PERSONALITY_RELATIONSHIPS_PLAN.md` §2.2 (promise events).
- **Depends on:** U1.
- **Done when:** both kinds pass their tests; no chart is ever written.
- **Verify:** `pytest -m "not integration" tests/test_inbox_kinds_role_talk.py tests/test_inbox_kinds_reporter.py tests/test_franchise_api.py` pass.

### U4: GM-side kinds (trades and contracts)

- **Owns:** `engines/inbox/kinds/gm_talk.py` (new), `engines/inbox/kinds/contract.py` (new), `tests/test_inbox_kinds_gm_talk.py` (new), `tests/test_inbox_kinds_contract.py` (new).
- **Reads:** §2.2–§2.8, §2.14; U1's package; `server/franchise_api.py:945-1290`; `engines/gm_negotiation.py:325-638`; `engines/front_office.py:480-520`; `engines/gm_trade_engine.py:1440-1450`; `docs/not_started/TRADE_NEGOTIATIONS_PLAN.md` §2.7.
- **Depends on:** U1.
- **Done when:** both kinds pass their tests; `franchise_api.py` and `gm_negotiation.py` are unchanged.
- **Verify:** `pytest -m "not integration" tests/test_inbox_kinds_gm_talk.py tests/test_inbox_kinds_contract.py tests/test_gm_negotiation.py tests/test_franchise_api.py` pass.

### U6: Team vision (engine, `gm_direction` hooks, kind)

- **Owns:** `engines/team_vision.py` (new), `engines/gm_direction.py`, `engines/inbox/kinds/vision.py` (new), `tests/test_team_vision.py` (new), `tests/test_gm_direction.py`, `tests/test_inbox_kinds_vision.py` (new).
- **Reads:** §2.2, §2.7–§2.9; U1's package; `engines/gm_direction.py` (whole); `engines/gm_rollover.py:22-45`; `engines/gm_personas.py` (traits); `tests/test_gm_direction.py`.
- **Depends on:** U1 (the `team_vision` table and the registry).
- **Done when:** with no vision rows `gm_direction` output is unchanged; a pin and an emphasis apply as in §2.9; the kind passes.
- **Verify:** `pytest -m "not integration" tests/test_team_vision.py tests/test_gm_direction.py tests/test_inbox_kinds_vision.py` pass.

### U5: Inbox API

- **Owns:** `server/inbox_api.py` (new), `server/api.py`, `tests/test_inbox_api.py` (new).
- **Reads:** §2.12; `server/franchise_api.py:1-230`; `server/debug_api.py:350-410`; U1's package.
- **Depends on:** U1.
- **Done when:** every §2.12 endpoint answers with its shape on patched data; the router is mounted.
- **Verify:** `pytest -m "not integration" tests/test_inbox_api.py tests/test_franchise_api.py` pass; `python3 -c "import server.api"` exits 0 with `DATABASE_URL` pointing at `madden_test`.

(U-numbers are kept from the previous revision; U6 is new.)

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2 | — | No shared files; U2 builds against §2.12's fixed shapes. |
| 2 | U3, U4, U6 | U1 | Separate kind files found by auto-discovery; only U6 touches `gm_direction.py`. |
| 3 | U5 | U1 | Generic over kinds; it goes in its own wave only to keep three units per wave. It doesn't need wave 2, so it can run in wave 2 if JR allows four units in a wave. |

After wave 3 the orchestrator runs the page against `madden_test` once (take control of the coach and GM, propose a vision, make a typed contract offer, give an interview, make a promise and review it) and runs the quality gate.

**File ownership check:**

| File | Unit |
|---|---|
| `Mu_Network/src/components/franchise/FranchisePage.tsx` | U2 |
| `Mu_Network/src/components/franchise/InboxModule.tsx` | U2 |
| `Mu_Network/src/types/franchiseInbox.ts` | U2 |
| `db/init_db.sql` | U1 |
| `db/migrations/2027_01_19_front_office_inbox.sql` | U1 |
| `engines/gm_direction.py` | U6 |
| `engines/inbox/__init__.py` | U1 |
| `engines/inbox/__main__.py` | U1 |
| `engines/inbox/actors.py` | U1 |
| `engines/inbox/kinds/__init__.py` | U1 |
| `engines/inbox/kinds/contract.py` | U4 |
| `engines/inbox/kinds/gm_talk.py` | U4 |
| `engines/inbox/kinds/reporter.py` | U3 |
| `engines/inbox/kinds/role_talk.py` | U3 |
| `engines/inbox/kinds/vision.py` | U6 |
| `engines/inbox/parse.py` | U1 |
| `engines/inbox/registry.py` | U1 |
| `engines/inbox/scan.py` | U1 |
| `engines/inbox/store.py` | U1 |
| `engines/inbox/appraisal.py` | U1 |
| `engines/inbox/voice.py` | U1 |
| `engines/team_vision.py` | U6 |
| `pipeline/weekly_capture.py` | U1 |
| `server/api.py` | U5 |
| `server/inbox_api.py` | U5 |
| `tests/test_gm_direction.py` | U6 |
| `tests/test_inbox_actors.py` | U1 |
| `tests/test_inbox_api.py` | U5 |
| `tests/test_inbox_kinds_contract.py` | U4 |
| `tests/test_inbox_kinds_gm_talk.py` | U4 |
| `tests/test_inbox_kinds_reporter.py` | U3 |
| `tests/test_inbox_kinds_role_talk.py` | U3 |
| `tests/test_inbox_kinds_vision.py` | U6 |
| `tests/test_inbox_parse.py` | U1 |
| `tests/test_inbox_registry.py` | U1 |
| `tests/test_inbox_scan.py` | U1 |
| `tests/test_inbox_store.py` | U1 |
| `tests/test_inbox_appraisal.py` | U1 |
| `tests/test_inbox_voice.py` | U1 |
| `tests/test_team_vision.py` | U6 |
| `tests/test_weekly_capture_inbox.py` | U1 |

No path appears twice.

## 7. Spec changes when built

- §1.1 Repository Layout: `engines/inbox/` (package, `kinds/`), `engines/team_vision.py`, `server/inbox_api.py`, `InboxModule.tsx`, `types/franchiseInbox.ts`, the migration, the tests.
- New §2.21b "Front-Office Inbox": controlled personas (no default), the matrix, who decides what, kinds, voice, appraisal events and the outbox, drafts, scan, caps, CLI.
- §2.16: the vision pin (`apply_pin`) and needs emphasis; `team_vision`.
- New §3.3r: `controlled_personas`, `inbox_threads`, `inbox_messages`, `inbox_relationship_events`, `team_vision`.
- §3.3p / the relationships section: the inbox as a producer of `meaningful_interaction`, `treatment`, `promise_made` and `promise_resolved` events (outbox `inbox_relationship_events`); it never writes `persona_relationships`.
- New §4.6c: `/api/franchise/{abbr}/inbox*`.
- §5: the Inbox module.
- §6.0 / §6.0b: `step_inbox` after `step_gm`; the scan on opening the inbox.
- §3.3f: GM negotiations open from the inbox.
- `V2_MASTER_PLAN.md` §6 V2.9 status (orchestrator, when built). The stale §3.3f Whitfield/Grant history stays as history; it isn't the canonical league (S8).

## 8. Decisions

**Taken (JR, 2026-11-03):**

| # | Decision | Where in the plan |
|---|---|---|
| D1 | JR may control any number of GM, coach and player personas; threads are controlled persona ↔ counterpart | §2.1 |
| D2 | Per-team inbox; the beat writer can message JR; players speak for themselves; on-record answers stored | §2.8 `jr_statements` |
| D3 | No owner threads or stub; `owner` stays a counterpart type; owners start later with the cap mandate and job security | §2.11 |
| D4 | Inbox interviews are one-on-one; pressers belong to V2.2 (`PRESS` notice only) | §2.8, §2.14 |
| D5 | The inbox is built first; V2.11 extends `GM_TALK` | §2.14, R1 |
| D6 | The registry's `user_teams` is the truth for teams | §2.1, S2, R2 |
| D7 | The model is the default voice, templates the fallback; 8 s budget from the spike; the `llm_compare` gate before closing | §2.4, §3 |
| D8 | JR controls no one by default; control is explicit (`controlled_personas`) | §2.1, §2.12 |
| D9 | Coach ↔ GM vision talks built now; skipped where the coach is the GM; coach voice neutral until coach personalities are on | §2.8, §2.9, U6 |
| D10 | Typed text → a draft JR confirms (contract, trade, promise, vision) | §2.3 |
| D11 | Promises never move the depth chart; they are recorded and resolved from the actual chart, and never change a sim number | §2.5, §2.8 |
| D12 | Interviews are open-ended, capped at 10 questions | §2.8 |
| D13 | The counterpart appraises what was said, with a stored reason; no fixed table (as evidence since D15) | §2.5 |
| D14 | V2.10's `PROSPECT` and `SCOUT` kinds belong to the controlled GM (`ACTOR_ROLES = ('GM',)`); without a controlled GM on that team, none | §2.1, §2.14 |
| D15 | The relationships plan owns trust; the inbox records `meaningful_interaction` / `treatment` events in its envelope (outbox until its store exists); 0.03 / 0.06 are suggested policy starting values; voice reads trust only via `relationship_context` | §2.4, §2.5, §2.6 |
| D16 | Promises are `promise_made` / `promise_resolved` with a measurable depth-chart criterion and due time, resolved from the actual chart | §2.5, §2.8 |
| D17 | Trust is one-way; never read as mutual; no legacy tags | §2.5 |
| D18 | Revised (JR, 2026-11-03): every persona may appraise, real-inspired included; existing players have generated profiles from 2026; real coaches neutral until their reviewed profiles exist | §2.5 |

**Still open:**

| # | Question | Recommendation |
|---|---|---|
| O1 | The vision's playing-time setting has no consumer because `COACH_TRUST_SCALE` is 0 (depth-chart plan D4). | Settled: stored only here. Its eventual consumer (depth-chart score adjustments) is owned by [`PERSONALITY_DEVELOPMENT_COACHING_PLAN.md`](../done/PERSONALITY_DEVELOPMENT_COACHING_PLAN.md), not this plan. |
| O2 | A pinned direction changes the hot seat's expected wins (R7). Keep that link? | Yes. A coach who asks to rebuild is judged as a rebuilding coach. The vision template says so. |
| O3 | Trust bounds: 0.03 per exchange and 0.06 per promise. | Settled by D15: suggested starting values for the relationships plan's policy, not a calculation in this plan. |
| O4 | Wave 3 holds only the API to keep three units per wave. Allow four units in wave 2 instead? | Yes, if JR doesn't mind four parallel agents; it saves a wave. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | build/inbox-u1 | .claude/worktrees/inbox-u1 | merged (built by the orchestrator after the agent worktree was cut from a diverged base) | c04f6e1 |
| U2 | 1 | build/inbox-u2 | .claude/worktrees/inbox-u2 | merged (retry; the first agent was blocked because the harness cut its worktree from a 392-commits-old base, so the orchestrator cut `build/inbox-u2` from local main) | e54a117 |
