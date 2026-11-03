# Project Mu: Front-Office Inbox & Direct Conversations Plan (V2.9)

**Status:** proposal, revised after JR's review (2026-11-03); awaiting JR's approval.
**Builds on:** [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §2, §5.3.2, §6 V2.0 and V2.9; [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) §8, phases 4 and 6; PROJECT_SPEC §2.16 (team direction), §2.17c (depth charts), §3.3f (GM negotiations), §3.3p (personas), §3.3q, §4.6b (franchise API), §2.21a (league office).

**Goal:** JR plays through personas: a team's GM, its head coach, and later a player. The inbox is where each persona JR controls talks to the other agents in the league, one conversation per pair: JR's GM with another GM about a trade, JR's GM with one of his players about a contract, JR's coach with a player about his role, JR (in either hat) with his beat writer in a one-on-one interview. Engines decide every outcome. The agent on the other side answers in its own voice, written by a language model and checked against the facts, with fixed template text as the fallback. The inbox is also the shared surface the sibling plans plug into: V2.11 deepens trade talks, V2.10 adds prospect interviews and scouts, V2.2 posts a notice when a press conference is ready.

**Terms.** An **agent** is a persona with a personality and an interface to act through: a GM, a coach, a player or a reporter. It is never a player's representative; players speak for themselves. A **controlled persona** is one JR plays. A **counterpart** is the agent on the other side of a thread.

**Delivers:**
- Tables `inbox_threads`, `inbox_messages` and `controlled_personas` (migration `db/migrations/2027_01_19_front_office_inbox.sql`, folded into `db/init_db.sql`).
- Package `engines/inbox/`: `actors.py` (which personas JR controls), `store.py`, `registry.py` (conversation kinds, auto-discovered), `voice.py` (the counterpart's words), `parse.py` (typed text → a draft JR confirms), `scan.py` (inbound messages), `__main__.py` (CLI).
- Four conversation kinds in `engines/inbox/kinds/`: `gm_talk.py` (GM ↔ GM, trades), `contract.py` (GM ↔ own player), `role_talk.py` (coach ↔ own player, role and playing time), `reporter.py` (any controlled persona ↔ beat writer, one-on-one interview).
- Endpoints `/api/franchise/{abbr}/inbox*` in a new `server/inbox_api.py`.
- An **Inbox** module (`#franchise-inbox`) in the Command Center, with an unread count and an "acting as" picker.
- A `step_inbox` call in `weekly_capture.run_weekly_engines`.
- Designed but not built here: team-vision talks (coach ↔ GM, §2.9) and owner agents (options, §2.10).

**JR's answers (2026-11-03, from his review):**
- D1: JR may control any number of GM, coach and player personas. Threads are (controlled persona ↔ counterpart persona). Controlled-player mode isn't designed yet, but the model must allow it.
- D2: One inbox per team. The team's beat writer can message JR. Players speak for themselves. JR's on-record answers are stored for articles later.
- D3: No owner threads in this build. A short options section says what owner agents could own later.
- D4: Reporter talks in the inbox are one-on-one interviews. Postgame press conferences are a separate multi-agent panel owned by [`PRESS_CONFERENCE_PLAN.md`](./PRESS_CONFERENCE_PLAN.md); the inbox only carries its `PRESS` notice.
- D5: The inbox is built first; [`TRADE_NEGOTIATIONS_PLAN.md`](./TRADE_NEGOTIATIONS_PLAN.md) (V2.11) then extends `GM_TALK`.
- D6: The franchise registry's `user_teams` is the source of truth for which teams JR controls, not `gm_team_settings.user_controlled` (Madden keeps all 32 teams as user teams to stop CPU moves).
- D7: The language model is the intended way counterparts speak; templates are the fallback.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `engines/gm_negotiation.py:425` `open_negotiation`, `:460` `offer`, `:503` `accept_counter`, `:529` `close` already run multi-turn trade talks with a GM (patience, counters, moods, turns in `gm_trade_negotiation_turns`, spec §3.3f). Only `#debug` reaches them (`server/debug_api.py:350`, `:374`–`:409`). | Trade talk needs no new engine. `GM_TALK` wraps these; V2.11 extends the same file. |
| S2 | `engines/gm_negotiation.py:434`–`:440` refuses unless `gm_team_settings.user_controlled` is true for JR's team and false for the other. | This disagrees with D6. V2.11 §2.10 moves the GM engines to the registry. Until then the inbox checks the registry first and shows `gm_negotiation`'s refusal if the flag disagrees (R2). |
| S3 | `engines/gm_negotiation.py:306` `gm_message`: fact sheet → LLM JSON `{"message"}` → kept only if `engines/gm_rationale.py:184` `validate_rationale` passes (no money, number, player or team that isn't on the sheet), else `template_message`. | `engines/inbox/voice.py` reuses the pattern and the validator for every kind. |
| S4 | `server/franchise_api.py:1266` `propose` scores an extension or re-sign with `_score` (`:945`, which calls `engines/free_agency_engine.py:188` `offer_utility` and `:230` `acceptance`) after `_eligibility` (`:1237`), and stores a `contract_offers` row (`db/init_db.sql:578`). `pipeline/league_office.py:1156` `step_accepted_offers` writes JR's ACCEPTED offers to the save. | A contract conversation is real today: each offer is one scored `contract_offers` row, and an accepted one reaches the save. |
| S5 | `server/franchise_api.py:1021`–`:1047` `put_depth_chart` checks every slot is sent, at most `DEPTH` (6) per slot, no player twice, every player on the team, then cancels earlier pending charts and queues a `DEPTH_CHART` move. The checks are inline, not a function. | A coach's "I'll move you up" can be a real depth-chart move with the same checks (re-stated in `role_talk.py`, R5). |
| S6 | `engines/depth_chart_engine.py:328` `staff_depth_chart` builds the staff's chart; `:278` `coach_trust` (youth vs veteran) is scaled by `COACH_TRUST_SCALE = 0.0` (`:167`). | "This player should start" is computable today. A playing-time philosophy has no engine effect yet (§2.9). |
| S7 | `engines/gm_direction.py:119` `assign_directions` decides CONTEND / RETOOL / REBUILD from the D score with two hard overrides: projected wins ≥ 10.5 is always CONTEND; a team over the cap can't REBUILD. There is no manual pin. | The engine-permitted direction options for a team-vision talk exist; honouring an agreed direction needs a pin in `gm_direction` (§2.9, later). |
| S8 | Spec §3.3f: Marcus Whitfield is Carolina's head coach and GM (since 2034); Charles Grant is Indianapolis's head coach with full control (2036). `gm_profiles.coach_id` marks a coach who is also the GM. | On both of JR's teams the coach and GM are one person, so a coach ↔ GM vision talk has no counterpart today. |
| S9 | `personality/core.py:14` `ROLES` = writer, gm, player, coach, owner, scout; `:144` `feature_view` gives all six sliders at 5 and no traits when a profile is missing. `personality/store.py:85` `ensure` has no caller outside tests: no player or coach profile is seeded yet. Writers and GMs have sliders and traits in `persona_sliders`/`persona_traits` (spec §3.3p). | GMs and writers speak in character now. Players and coaches speak in a neutral voice until their profiles are seeded (personality phase 4 integrations), with no inbox change needed. |
| S10 | `agent_memories` (`db/init_db.sql:1911`) has `assigned_team_id`, `display_name`, `person_key`; `LOCAL_BEAT_REPORTER` is an archetype in `newsroom/config/agent_personas.json`. | The interviewer is the team's beat writer, in his own voice. |
| S11 | `engines/gm_trade_engine.py:1447`–`:1448`, `:1621` mark ENGINE proposals involving a user-controlled team with `advisory:<ABBR>`, using `gm_team_settings`. `engines/front_office.py:492` `approve`, `:508` `reject`. | `GM_TALK`'s inbound trigger reads PROPOSED ENGINE proposals that involve a registry user team, whatever the flag says (D6). |
| S12 | `server/franchise_api.py:213` `user_teams()` reads `db/franchises.py:169` `active_entry()`; `:223` `require_user_team` gives 403 outside it. `server/api.py:20`, `:149` mount routers. | The inbox uses the same reader and the same 403 rule. |
| S13 | `pipeline/weekly_capture.py:560` `run_weekly_engines` runs `step_gm`, scouting, archive, carousel. In the offseason the watcher runs no engines (spec §6.0b step 4). | The weekly hook goes after `step_gm`; offseason messages come from a scan when JR opens the inbox. |
| S14 | `Mu_Network/src/components/franchise/FranchisePage.tsx:23` `MODULES` lists `{slug, label, Component}` modules, each `{ teamAbbr }`. | The Inbox is one more entry. |
| S15 | `engines/gm_llm.py:60` `make_llm(label)`, default `gemini-3.8-flash` (`:11`); no timeout. Latency beside Madden is unmeasured (V2 master §5.3.2). | Replies run with a time budget; past it, the template is used (§2.4). |
| S16 | The sibling plans already build on this plan's interfaces: `PRESS_CONFERENCE_PLAN.md` (`press.py`, reads `jr_statements`, migration `2027_01_20`), `SCOUTING_REVAMP_PLAN.md` (`prospect.py`, `scout.py`, `thread_id` FK to `inbox_threads`, `2027_01_21`), `TRADE_NEGOTIATIONS_PLAN.md` (extends `gm_talk.py`, `2027_01_22`). | `Kind`, `Trigger`, `jr_statements` and the thread columns they name stay as written; this revision only adds fields with defaults. |

## 1. Scope

**In:**
- Controlled personas per user team (default: the team's head coach and GM; a `controlled_personas` table to change that). Threads carry both sides.
- Thread and message storage, one inbox per user team, unread state, expiry.
- A registry of conversation kinds that sibling plans extend by adding one file.
- Four kinds, buildable now: `GM_TALK`, `CONTRACT`, `ROLE_TALK` (coach side), `REPORTER` (one-on-one interviews).
- Inbound messages from engine facts each week (and on opening the inbox in the offseason), deduplicated and capped.
- JR's replies: a button or form, an optional note, or typed text that the inbox turns into a draft action JR confirms.
- Counterpart replies written by the language model in the persona's voice, checked against the facts, with templates as fallback.
- `jr_statements`: JR's interview answers, stored for articles later.
- The Inbox module in `Mu_Network`.

**Out (non-goals):**
- Team-vision talks (coach ↔ GM): designed in §2.9, built later (Q2).
- Controlled-player mode (JR as a player talking to his coach, his GM or other teams): later. The tables and actor model allow it.
- Owner agents (§2.10 lists options). `owner` stays a valid counterpart type so no migration is needed later.
- Press conferences (V2.2). The inbox carries only V2.2's `PRESS` notice kind.
- Prospect and combine interviews, scouts (V2.10's `PROSPECT` and `SCOUT` kinds).
- Leaks, posturing, CPU-to-CPU transcripts, the registry switch in the GM engines, queuing agreed trades (V2.11).
- Writers quoting JR's answers and remembering them (V2.2 reads `jr_statements`).
- Holdouts and trade requests ([`PERSONALITY_TRADE_REQUESTS_PLAN.md`](./PERSONALITY_TRADE_REQUESTS_PLAN.md) adds a kind file).
- National insiders messaging JR (only the beat writer, D2).
- "Discuss in inbox" buttons in the Contract Room and Depth Chart modules (V2.11 adds one to the Trade Machine).

## 2. Design

### 2.1 Controlled personas and the conversation matrix

A thread is always **(the persona JR controls) ↔ (a counterpart agent)**. Which threads can exist depends on which hat JR wears.

`engines/inbox/actors.py`:

```python
@dataclass(frozen=True)
class Actor:
    team_id: int; abbr: str
    role: str            # 'GM' | 'COACH' | 'PLAYER'
    persona_id: str      # gm_profiles.id | coaches.id | players.id, as text
    name: str            # 'Marcus Whitfield'
    also: tuple = ()     # other roles the same person holds: ('COACH',) for a coach-GM

def user_teams() -> list[str]                    # db.franchises.active_entry()['user_teams'], upper case; [] if none
def controlled(cur, abbr: str) -> list[Actor]    # [] when abbr isn't a registry user team (D6)
def actor_for(cur, abbr: str, role: str) -> Actor | None
```

`controlled` uses the team's `controlled_personas` rows when it has any active ones. Otherwise it uses the default: the head coach (open `coach_tenures` row) as COACH and the open `gm_profiles` tenure as GM, with `also` set when `gm_profiles.coach_id` is that coach (S8). Rows for a team not in `user_teams` are ignored. CLI: `python3 -m engines.inbox control --team CAR [--role COACH|GM|PLAYER --persona ID] [--off]` lists or changes the rows. A PLAYER row is accepted, but no kind serves it until controlled-player mode is designed.

**The matrix.** Now = built by this plan. Later = what's missing.

| JR controls | Counterpart | Kind | What decides | Status |
|---|---|---|---|---|
| GM | another team's GM | `GM_TALK` (trades) | `gm_negotiation` (trade value, patience, legality) | **Now**; V2.11 deepens it |
| GM | own player | `CONTRACT` | free-agency utility and acceptance (`_score`) | **Now** (player voice neutral until his profile is seeded) |
| GM | own head coach | `VISION` | `gm_direction` rules (§2.9) | **Later**: no counterpart while coach and GM are one person (S8); needs coach profiles for a voice and a direction pin in `gm_direction` |
| GM | beat writer | `REPORTER` (interview) | nothing; JR's answers are stored | **Now** |
| GM | draft prospect / scout | `PROSPECT` / `SCOUT` | scouting engines | V2.10 |
| GM | owner | — | — | **Later** (§2.10) |
| Coach | own player | `ROLE_TALK` (role, playing time) | depth-chart rules and the staff chart | **Now** (player voice neutral) |
| Coach | own GM | `VISION` | `gm_direction` rules | **Later**, as above |
| Coach | beat writer | `REPORTER` | nothing | **Now** |
| Coach | the press (panel) | `PRESS` notice only | V2.2 | V2.2 |
| Player | own coach | `ROLE_TALK` (player side) | the coach agent's staff chart | **Later**: controlled-player mode |
| Player | own GM | `CONTRACT` (player side) | needs a team-side offer engine for a controlled player | **Later**: controlled-player mode |
| Player | other GMs (free agency) | — | `free_agency_engine` | **Later** |
| Player | beat writer | `REPORTER` | nothing | **Later** (the kind already works for any actor) |

When one person holds both hats (Whitfield, Grant), the inbox shows him once ("Marcus Whitfield, HC & GM") and the thread's `actor_role` records which hat the thread uses.

### 2.2 Who decides what: engines vs agents

The rule in plain words: **the engine decides what happens; the agent only says it.** The agent (the counterpart's language model) never accepts, rejects or invents a number. It gets the engine's decision and a fact sheet, and writes the reply in the persona's voice. If its words state anything not on the sheet, or contradict the decision, the inbox throws them away and uses the template.

| Decision | Engine that makes it | Example | What the agent does |
|---|---|---|---|
| Does a player accept a contract offer, counter, or say no? And the counter's terms | `free_agency_engine.offer_utility` + `acceptance`, via `franchise_api._score` | JR's GM offers his WR 3 years, $40M, $15M guaranteed. Utility comes out under the player's bar, so the engine returns COUNTER at 3 years, $44M, $18M guaranteed. | Writes: "I like it here and I want to stay, but I need a little more security. Get to $18M guaranteed and we're done." It may use $44M and $18M (they are on the sheet), never a number of its own. |
| Does a GM accept a trade, counter, reject or walk away? Which counter? | `gm_negotiation.respond` (trade value surpluses, patience, counter search, legality) | JR offers a 2nd-round pick for a CB. The GM's surplus is below his floor; the engine counters by adding JR's 5th. | Writes the GM's reply in his voice ("Add the fifth and I can sell it upstairs"). V2.11 adds posture and leaks; the maths stays in the engine. |
| Can a depth-chart move be made? | `put_depth_chart`'s checks (S5) in `role_talk.py`, queued as a `DEPTH_CHART` pending move | JR's coach tells his backup RB "you're starting Sunday". The move is legal, so it's queued exactly as the Depth Chart module would queue it. | The player answers in his voice ("I'll be ready, coach"). |
| Should this player be starting? (the trigger for a role talk) | `depth_chart_engine.staff_depth_chart` vs JR's chart | The staff chart has the rookie WR at WR1 two weeks running; JR's chart has him at WR3. The rookie asks for a talk. | Writes the rookie's ask, citing only facts on the sheet (his depth, the staff's view). |
| Who writes to JR, about what, and when | each kind's `scan` triggers (deterministic, deduplicated, capped) | An ENGINE trade proposal names JR's LB; a `TRADE_INTEREST` thread opens with that GM. | Writes the opening message. |
| What JR said in an interview | nobody: JR's own words | The beat writer asks about the 3-game losing streak; JR answers on the record. | Writes the question and follow-ups; stores JR's answer as a statement. |
| Team vision (later, §2.9) | `gm_direction` rules decide which directions are allowed and whether the GM agrees | | Words it. |

The master plan also lets an LLM choose among engine-permitted options. This plan doesn't use that: every choice here is the engine's, so a replay gives the same outcome. (V2.11 uses it for CPU GMs' posture.)

### 2.3 Typed text vs buttons and forms

JR can act two ways:

- **Buttons and forms.** He picks "Make an offer", fills Years 3, Total $40,000,000, Guaranteed $15,000,000, and clicks Send. The engine scores exactly those numbers.
- **Typed text.** He types "3 years, 40 million, 15 guaranteed".

Typed text never acts on its own. The inbox turns it into a **draft** of the same form and asks JR to confirm:

> Did you mean: **Offer 3 years, $40.0M total, $15.0M guaranteed, $0 bonus?** [Send offer] [Edit] [Just send as a message]

Only the confirm click calls the engine. "Just send as a message" sends the words as talk: no action, and the counterpart answers in character. Recommendation (Q3): build drafting now for the three actions that have forms: contract terms, a depth-chart move, and a trade package.

`engines/inbox/parse.py`:

```python
def parse_contract_terms(text) -> dict | None
    # deterministic: '3 years|yrs|-year', '$40M|40 million|40m', 'guaranteed|gtd', 'bonus'; None when unsure
def parse_with_llm(llm, text, schema: dict, choices: dict | None, budget_s) -> dict | None
    # fallback: JSON that must validate against schema (number ranges) and pick ids only from `choices`
def draft_summary(option_id, payload) -> str       # 'Offer 3 years, $40.0M total, ...'
```

Each kind can define `draft(self, cur, thread, text, llm=None) -> {"option_id", "payload", "summary"} | None`. `CONTRACT` uses the contract parser; `ROLE_TALK` matches "start him / move him up / first team" to `MOVE_UP`; `GM_TALK` matches player names and "2nd round 2038"-style picks against `gm_negotiation.team_assets`' two asset lists (exact, then last-name match; LLM fallback restricted to those ids). No draft → the text is talk, or a kind's own reading of it (V2.10's `PROSPECT` treats it as a typed question).

### 2.4 How counterparts speak: language model first, templates as fallback

Every counterpart line is written one of two ways:

- **Language model (the intended way).** The model gets the persona's voice (sliders, traits as "tends to" lines, or "plain and professional" when the persona is neutral), the fact sheet, the last 6 messages and JR's note. It writes the reply in character.
- **Template.** Fixed text per kind and intent with the facts filled in ("He's asking for 3 years, $44.0M, $18.0M guaranteed.").

The first version of this plan kept the model off until it was measured. JR wants it on, so `MU_INBOX_LLM` defaults to on, and `MU_INBOX_LLM=0` forces templates. A template is used only when:
1. the model times out (`INBOX_LLM_BUDGET_S = 8.0`, an estimate set by the spike in §3),
2. the model's text fails `validate_rationale` (a number, dollar amount, player or team not on the sheet), the kind's optional `validate(text, facts)`, or the intent check (an ACCEPTED reply that says no), or
3. there's no API key.

The UI marks a template line with a small "template" tag, so JR can see how often it happens.

`engines/inbox/voice.py`:

```python
def persona_view(cur, counterpart_type, counterpart_id) -> dict
    # personality.store.read + core.feature_view(profile, 'inbox'); missing -> neutral (S9).
    # Writers and GMs fall back to persona_sliders/persona_traits (newsroom.personas) when no core profile exists.
def write(cur, kind, thread, intent, facts, history, note=None, llm=None, budget_s=INBOX_LLM_BUDGET_S) -> tuple[str, str]
    # (text, 'LLM' | 'TEMPLATE'); the model runs in a worker thread and is abandoned after budget_s.
def default_llm()        # make_llm("inbox") unless MU_INBOX_LLM == '0'
INBOX_LLM_BUDGET_S = 8.0
```

Order inside every reply: (1) the kind's `act` runs the engine and stores the decision with JR's message; (2) the voice writes the reply within the budget; (3) the reply is stored. The decision never waits on the words.

### 2.5 Tables (`db/migrations/2027_01_19_front_office_inbox.sql`, mirrored in `db/init_db.sql`)

```sql
CREATE TABLE IF NOT EXISTS controlled_personas (
    team_id     INT NOT NULL REFERENCES teams(id),
    role        VARCHAR(8) NOT NULL CHECK (role IN ('GM','COACH','PLAYER')),
    persona_id  VARCHAR(40) NOT NULL,                  -- gm_profiles.id | coaches.id | players.id
    active      BOOLEAN NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, role, persona_id)
);   -- no rows for a team = the default (head coach + GM); only registry user teams count (D6)

CREATE TABLE IF NOT EXISTS inbox_threads (
    id                SERIAL PRIMARY KEY,
    team_id           INT NOT NULL REFERENCES teams(id),       -- the inbox (JR's team) the thread belongs to
    kind              VARCHAR(24) NOT NULL,                    -- registry key; no CHECK (sibling plans add kinds)
    actor_role        VARCHAR(8) NOT NULL CHECK (actor_role IN ('GM','COACH','PLAYER','TEAM')),
    actor_id          VARCHAR(40) NOT NULL,                    -- the controlled persona; 'TEAM' + abbr for team notices
    counterpart_type  VARCHAR(16) NOT NULL
        CHECK (counterpart_type IN ('gm','coach','player','writer','prospect','scout','owner','system')),
    counterpart_id    VARCHAR(100) NOT NULL,                   -- gm_profiles.id, coaches.id, players.id, agent_memories.agent_id, prospect_key (V2.10: draft_prospects.id changes on a board re-scan)
    counterpart_name  VARCHAR(255) NOT NULL,
    subject           VARCHAR(255) NOT NULL,
    status            VARCHAR(12) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN','WAITING_JR','CLOSED','EXPIRED')),
    started_by        VARCHAR(12) NOT NULL CHECK (started_by IN ('JR','COUNTERPART','SYSTEM')),
    source_ref        VARCHAR(120) NOT NULL,                   -- dedupe key: 'gm_trade_proposals:812', 'role:4411:2036:7'
    link_table        VARCHAR(48),                             -- 'gm_trade_negotiations' | 'contract_offers' | 'franchise_pending_moves' | sibling tables
    link_id           INT,
    state             JSONB NOT NULL DEFAULT '{}',             -- kind-owned state: {"questions_asked": 2}, {"promise_review_week": 9}
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
    author       VARCHAR(12) NOT NULL CHECK (author IN ('JR','COUNTERPART','SYSTEM')),  -- 'JR' = the controlled persona
    body         TEXT NOT NULL,
    body_source  VARCHAR(10) NOT NULL CHECK (body_source IN ('JR','LLM','TEMPLATE','ENGINE')),
    option_id    VARCHAR(40),                  -- the option JR picked; NULL for talk
    options      JSONB NOT NULL DEFAULT '[]',  -- options offered with this message
    facts        JSONB,                        -- the fact sheet the body was checked against (audit; never sent to the UI)
    engine_ref   JSONB,                        -- {"offer_id": 512, "decision": "COUNTER"} / {"negotiation_id": 9, "turn_no": 3} / {"move_id": 77}
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (thread_id, seq)
);
```

Option shape: `{"id": "MAKE_OFFER", "label": "Make an offer", "form": "contract_terms" | "trade_assets" | "depth_slot" | null, "needs_note": false, "default": {...} | null}`. Every option a kind offers is one its engine allows right now; `act` checks it again.

### 2.6 Registry (`engines/inbox/registry.py`)

```python
@dataclass(frozen=True)
class Trigger:
    kind: str; team_id: int; counterpart_type: str; counterpart_id: str; counterpart_name: str
    subject: str; source_ref: str; facts: dict; intent: str
    link_table: str | None = None; link_id: int | None = None
    expires_season: int | None = None; expires_week: int | None = None
    actor_role: str | None = None; actor_id: str | None = None   # None -> filled by scan from the actor it ran for
    state: dict | None = None

@dataclass
class ActResult:
    facts: dict; intent: str; engine_ref: dict | None
    close: bool = False; link: tuple | None = None; state: dict | None = None

class Kind(Protocol):
    KIND: str
    COUNTERPART_TYPE: str
    ACTOR_ROLES: tuple = ('GM', 'COACH')    # optional; which hats the kind serves. Missing -> scanned once per team
                                            # with the team's first actor (GM before COACH) or 'TEAM' for SYSTEM kinds
    def scan(self, cur, season: int, week: int | None, team: dict) -> list[Trigger]: ...   # team carries 'actor'
    def options(self, cur, thread: dict, last: dict) -> list[dict]: ...
    def act(self, cur, thread: dict, option_id: str | None, payload: dict | None, note: str | None) -> ActResult: ...
    def template(self, intent: str, facts: dict) -> str: ...
    def start(self, cur, team: dict, body: dict) -> Trigger: ...   # JR-started; InboxError(400) when not allowed
    # Optional: def validate(self, text: str, facts: dict) -> bool         (V2.10 prospects)
    # Optional: def draft(self, cur, thread, text, llm=None) -> dict | None (§2.3)

def kinds() -> dict[str, Kind]   # auto-discovers modules in engines/inbox/kinds/ that define KIND (pkgutil)
```

`InboxError(status, message)` becomes an HTTP error. `act(option_id=None, note=...)` is talk by default (intent `TALK`, no engine call); a kind may read it otherwise (V2.10's typed questions). The sibling plans' signatures (`scan(cur, season, week, team)`, `Trigger`'s first fields) are unchanged; the new fields have defaults.

### 2.7 The four kinds built now

| Kind | Actor → counterpart | Inbound triggers | JR's options | Engine behind `act` |
|---|---|---|---|---|
| `GM_TALK` (`gm_talk.py`) | GM → another team's GM (`gm_profiles.id`) | `TRADE_INTEREST`: an ENGINE `gm_trade_proposals` row, PROPOSED, with JR's team (registry) on one side, not yet in a thread (S11) | Inbound: `OPEN_TALKS` (form `trade_assets`, default = the proposal's legs), `NOT_INTERESTED`. In talks: `MAKE_OFFER` (`trade_assets`), `ACCEPT_COUNTER`, `END_TALKS` | `gm_negotiation.open_negotiation` / `offer` / `accept_counter` / `close`; `front_office.reject`. The GM's reply is the turn message `gm_negotiation` already writes (copied, never written twice). AGREED, WALKED_AWAY, CLOSED, EXPIRED close the thread. JR-started: `{gm_team, assets, note}`. |
| `CONTRACT` (`contract.py`) | GM → own player (`players.id`) | `EXTENSION_WINDOW`: a starter becomes eligible (`_eligibility`) with no open thread | `MAKE_OFFER` (`contract_terms`), `ACCEPT_COUNTER` (default = the last counter), `WALK_AWAY` | `franchise_api` helpers `_context`, `_player`, `_eligibility`, `_score`, `insert_offer` (lazy import, R4). One `contract_offers` row per offer; ACCEPTED closes the thread and the league office writes it (S4). JR-started: `{player_id, note}`. |
| `ROLE_TALK` (`role_talk.py`) | COACH → own player | `LOST_STARTING_JOB`: depth 1 at a base slot in the previous week's chart, not in the current one. `WANTS_MORE`: the staff chart starts him and JR's chart hasn't for 2 straight weeks (S6). `PROMISE_DUE`: a promise's review week arrived (a SYSTEM line in the same thread) | `MOVE_UP` (form `depth_slot`: the slot; he swaps with the player one place above), `PROMISE_LOOK` (stored with `state.promise_review_week = week + 2`), `EXPLAIN` (note required, talk), `NO_CHANGE` | `MOVE_UP`: the current pending JR chart (else the stored one) with the swap, checked like `put_depth_chart` (S5), then `cancel_pending` + `insert_move(DEPTH_CHART)`. A promise is checked at review: kept if his depth improved. It's recorded for later morale and trade-request plans and has no effect here. JR-started: `{player_id, note}`. |
| `REPORTER` (`reporter.py`) | any controlled persona → the team's `LOCAL_BEAT_REPORTER` | `INTERVIEW_REQUEST` on: a starter out 4+ weeks, an executed trade or signing, a 3-game streak, a starter losing his job. At most 2 open per team | Per question: `ON_RECORD` (note required), `OFF_RECORD` (note), `NO_COMMENT`. Then `END_INTERVIEW` | None. A one-on-one interview: an opening question, up to `INTERVIEW_MAX_QUESTIONS = 3` follow-ups from the same facts, then thanks and close. JR-started: `{note}` ("call your beat writer" with a topic). |

Caps: `MAX_OPEN_PER_KIND = 3` per team for `GM_TALK`, `CONTRACT` and `ROLE_TALK`, and 2 for `REPORTER`; oldest first. Expiry: with the source (a proposal's `expires_week`, the end of the extension window), else after `INBOX_STALE_WEEKS = 2` weeks without a reply.

`jr_statements(cur, team_id, season, week=None) -> list[dict]` (`store.py`, read by V2.2): one row per answered question: `{thread_id, team_id, season, week, writer_agent_id, writer_handle, subject, question, answer, on_record, actor_role, actor_name, created_at}`. OFF_RECORD rows have `on_record` false; NO_COMMENT isn't a statement.

### 2.8 Scan (`engines/inbox/scan.py`)

```python
def scan(cur, season, week, teams=None, llm=None) -> dict   # {'created', 'expired', 'by_kind'}
    # teams default = actors.user_teams(). For each team, each controlled actor, each kind serving that role:
    # kind.scan -> Trigger (actor filled) -> store.create_thread (ON CONFLICT DO NOTHING) -> opening message via
    # voice.write. One failing kind is logged and skipped. Then store.expire_stale.
def step_inbox(season)   # pipeline/weekly_capture.py, after step_gm; uses voice.default_llm(); logs, never raises
```

When JR opens the inbox, the API runs `scan(..., teams=[abbr], llm=None)`: templates, so the page opens fast. It runs at most once per (team, season, week, stage) per server process. The dedupe keys make a repeat harmless. CLI: `python3 -m engines.inbox scan [--season S --week W] [--team CAR] [--no-llm] [--dry-run]`, `list --team CAR`, `show <id>`, `control ...` (§2.1).

### 2.9 Team-vision talks (designed, not built)

A vision talk is the coach and the GM agreeing on what the team is trying to do. It can change only settings an engine already uses, chosen from options the engine allows:

| Setting | Options the engine allows | Who uses it | What's needed |
|---|---|---|---|
| Team direction | CONTEND / RETOOL / REBUILD, minus `gm_direction.assign_directions`' overrides: projected wins ≥ 10.5 allows only CONTEND; over the cap removes REBUILD (S7) | trade proposals, needs, the hot seat's expected wins | a pin read by `gm_direction` for that season (respecting the overrides) |
| Roster emphasis | OFFENSE / DEFENSE / BALANCED | `gm_direction.positional_needs` ranking (a weight on the side's groups) | a weight in `positional_needs` |
| Playing time | YOUTH / VETERANS / MERIT | `depth_chart_engine.coach_trust` | a non-zero `COACH_TRUST_SCALE` for the pinned team (today 0, S6) |

Example: JR controls only the coach of a team whose GM is a CPU agent. He asks the GM to REBUILD. The engine allows REBUILD (the team isn't over the cap, projected 7 wins), and the GM agrees if the team's D score is within `VISION_MARGIN` of the REBUILD band, a margin widened by his `risk_appetite`. Otherwise the engine returns its own direction as a counter. The agent words it. The agreed vision would be stored in a `team_vision` table (team_id, season, direction, emphasis, playing_time, thread_id).

Not built now (Q2). On both of JR's teams the coach is the GM (S8), so there's nobody to talk to. Coaches also have no personality profiles yet, and the engine consumers listed above are three separate engine changes. Build it when JR first controls a team where coach and GM are different people, or with controlled-player mode.

### 2.10 Owner agents: options for later

No owner threads in this build. These are the things an owner agent could own later, each grounded in data that exists or is named as missing:

| Owner could own | Engine data it would use | Exists today? |
|---|---|---|
| Budget and cap mandate ("stay $10M under the cap next year", "no guarantees over $X without my sign-off") | `team_cap_seasons`, `contracts_engine.next_cap`, `contract_offers` (a veto check before an offer is scored) | Data yes; a mandate check is new |
| Job security: firing and hiring the coach or GM | `engines/coach_hot_seat.py` scores, `engines/gm_rollover.py` hot seat (`EXPECTED_WINS`, `fo_score`), `coaching_carousel` | Scores yes; the owner would deliver them, and the engine score still decides |
| Team direction mandate ("we're contending this year") | `gm_direction` D score, `EXPECTED_WINS`; the §2.9 pin | Pin is new |
| Patience with a rebuild (years allowed before a mandate to win) | `gm_team_seasons.direction` history, `team_season_results` | Data yes |
| Stadium, ticket prices, revenue | `engines/market_constants.py` (market size), `popularity_engine` | No revenue engine; new work |
| Big-decision sign-off (franchise tags, record contracts, trading a star) | `contract_offers`, `gm_trade_proposals`, `trade_value` | Data yes; an approval step is new |

Owners have no names or profiles until personality phase 6. Recommendation (Q5): start with the cap mandate and job security, which reuse scores the engines already compute.

### 2.11 API (`server/inbox_api.py`, router prefix `/api/franchise`)

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/{abbr}/inbox` | `?status=OPEN,WAITING_JR&kind=&as=GM\|COACH&limit=50` | `{"team": "CAR", "actors": [Actor], "unread": 3, "threads": [Thread]}` |
| GET | `/{abbr}/inbox/kinds` | — | `[{"kind": "CONTRACT", "actor_roles": ["GM"], "can_start": true, "start_form": "player"}]` (only kinds a controlled actor can use) |
| GET | `/{abbr}/inbox/{thread_id}` | — | `{"thread": Thread, "messages": [Message], "options": [Option]}` (marks read) |
| POST | `/{abbr}/inbox/{thread_id}/draft` | `{"text": "3 years, 40 million, 15 guaranteed"}` | `{"draft": {"option_id", "payload", "summary"} \| null}` (acts on nothing) |
| POST | `/{abbr}/inbox/{thread_id}/reply` | `{"option_id": "MAKE_OFFER" \| null, "payload": {...} \| null, "note": "..." \| null}` | the thread, with the new messages |
| POST | `/{abbr}/inbox` | `{"kind": "CONTRACT", "as": "GM", "player_id": 123, "note": "..."}` / `{"kind": "GM_TALK", "as": "GM", "gm_team": "ATL", "assets": [...], "note": ...}` / `{"kind": "ROLE_TALK", "as": "COACH", "player_id": 77}` / `{"kind": "REPORTER", "note": "..."}` | 201, the new thread |
| POST | `/{abbr}/inbox/{thread_id}/close` | — | the thread |

`Actor = {role, id, name, also}`. `Thread = {id, kind, actor: {role, id, name}, counterpart: {type, id, name}, subject, status, unread, started_by, season, week, expires, link, last: {author, body, created_at}, updated_at}`. `Message = {id, seq, author, body, source, option_id, engine_ref, created_at}` (no `facts`, never a GM's surplus). Errors `{"detail"}`: 400 bad body or an option not offered; 403 team not in the registry's `user_teams`, or `as` not a controlled role; 404 unknown thread or another team's thread; 409 thread closed or the engine refused. `reply` with neither option nor note → 400; notes ≤ 600 characters.

### 2.12 Frontend

- `Mu_Network/src/types/franchiseInbox.ts`: the §2.11 shapes.
- `Mu_Network/src/components/franchise/InboxModule.tsx` (`{ teamAbbr }`):
  - **List:** an "acting as" filter (All / GM / Coach; one name when one person holds both hats), open/all, kind chips, unread dots.
  - **Conversation:** messages by author with a "template" tag on template lines; options as buttons; forms for `contract_terms`, `trade_assets` (the Trade Machine's asset shape) and `depth_slot`; a text box.
  - **Send:** without an option, Send first calls `/draft`. A returned draft shows the "Did you mean" card (§2.3); otherwise the text goes as talk. A sent reply shows at once with "…" until the answer arrives.
  - **New conversation:** a picker from `/inbox/kinds`.
- `FranchisePage.tsx`: `{ slug: "inbox", label: "Inbox", Component: InboxModule }` after Game Center, with the unread count on the tab.

### 2.13 How sibling plans plug in

| Sibling | What it adds | Shared files |
|---|---|---|
| V2.11 trade negotiations | Extends `engines/inbox/kinds/gm_talk.py` (posture, time limit, leaks, "queued for the league office"); takes JR's side from the thread's actor; moves the GM engines to the registry (fixes S2). Adds "Talk to their GM" in the Trade Machine. | `gm_talk.py` after this plan merges |
| V2.10 scouting | Adds `prospect.py` (`PROSPECT`, counterpart `prospect`, `ACTOR_ROLES = ('GM',)`, typed questions) and `scout.py` (`SCOUT`, neutral). Its tables reference `inbox_threads(id)`. | none |
| V2.2 press conferences | Adds `press.py` (`PRESS`, counterpart `system`, a notice only; the panel lives in the Game Center). Reads `jr_statements` for beat briefs and writer memory. | none |

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Shared files with the sibling plans: `db/init_db.sql`, `server/api.py`, `pipeline/weekly_capture.py`, `FranchisePage.tsx`, `gm_talk.py`. | The siblings already plan to start after this one (D5; their migrations are `2027_01_20`–`22`). | Build this plan first; siblings read the built code before their waves. |
| R2 | `gm_negotiation` still checks `gm_team_settings.user_controlled` (S2) until V2.11 lands. | `tests/test_inbox_kinds_gm_talk.py` covers a registry/flag mismatch. | The inbox checks the registry, then shows `gm_negotiation`'s 400 text ("you don't control CAR…") in the thread. JR ticks the flag on `#debug` until V2.11 removes it. |
| R3 | Model latency beside Madden is unmeasured; with the model on by default, slow replies mean frequent templates. | Spike below; `voice.write` logs time and source per reply. | Raise or lower `INBOX_LLM_BUDGET_S`; the decision is stored before the words either way. |
| R4 | `CONTRACT` and `ROLE_TALK` import private helpers from `server/franchise_api.py` (an engine importing a server module); a rename breaks them. | Their tests import the helpers by name. | Later, a plan that owns `franchise_api.py` moves `_score`, `_eligibility` and the depth-chart checks into an engine module. |
| R5 | `ROLE_TALK`'s `MOVE_UP` replaces the pending chart while JR may be editing it in the Depth Chart module. Its checks copy `put_depth_chart`'s. | Test: the queued chart equals the old one with one swap; the module reloads pending charts. | The thread names the move id; the Depth Chart module shows the new pending chart on reload. |
| R6 | The model writes a fact that isn't on the sheet, or contradicts the decision. | `validate_rationale`, the kind's `validate`, and the intent check; tests feed bad outputs. | The template is used; the bad text is never stored. |
| R7 | A parsed draft is wrong ("40" read as $40K). | The confirm card shows the full terms; parser tests. | JR edits or sends it as a message; nothing happens without the click. |
| R8 | Too many inbound messages. | Scan counts on a synthetic busy week. | Caps per kind (§2.7), oldest first. |
| R9 | Player and coach voices are neutral until their profiles are seeded (S9), so role and contract talks may read flat. | JR reads a week of threads. | Expected; phase 4 seeding changes them with no inbox change. |

**Spike first** (30 minutes on the PC before wave 2; doesn't block wave 1): run `make_llm("inbox")` 20 times on a filled `CONTRACT` prompt with Madden open and record p50 and p95. It sets `INBOX_LLM_BUDGET_S`. After wave 2, the orchestrator runs a `tools/llm_compare.py` side-by-side (the V2.0 quality gate) on a week of synthetic threads before closing the plan. If it fails, the default flips to templates until the prompt is fixed.

## 4. Tests (synthetic, no live DB)

- `tests/test_inbox_actors.py` → default actors (coach + GM; one person with `also` for a coach-GM); `controlled_personas` rows replace the default; teams outside the registry give []; PLAYER rows listed but served by no kind.
- `tests/test_inbox_store.py` → thread creation is idempotent on `(team_id, kind, source_ref)`; consecutive `seq`; expiry by week, stage and `INBOX_STALE_WEEKS`; caps; `jr_statements` row shape, on/off record, no NO_COMMENT rows; unread.
- `tests/test_inbox_registry.py` → auto-discovery; a module without `KIND` ignored; duplicate `KIND` raises; missing `ACTOR_ROLES` → scanned once per team.
- `tests/test_inbox_voice.py` → neutral view for a missing profile; writer and GM views from stored sliders; invented money, player or a contradiction → template; a slow fake model → template within the budget; `MU_INBOX_LLM=0` → template; neutral prompt has no traits.
- `tests/test_inbox_parse.py` → "3 years, 40 million, 15 guaranteed", "3 yrs $40M $15M gtd", "three-year $40M deal" (→ None or a correct draft), "40" alone → None; the LLM fallback rejects ids not in `choices` and numbers out of range; `draft_summary` text.
- `tests/test_inbox_scan.py` → each trigger fires once per actor role; a rerun creates nothing; the actor is filled; one failing kind doesn't stop the others.
- `tests/test_weekly_capture_inbox.py` → `step_inbox` runs after `step_gm` in weekly/playoff modes; a raising scan is logged.
- `tests/test_inbox_kinds_gm_talk.py` → a PROPOSED ENGINE proposal with a registry user team makes one `TRADE_INTEREST` thread, flag or not; `NOT_INTERESTED` → `front_office.reject`; the four talk options call the matching `gm_negotiation` function and copy the GM's turn text; a flag mismatch shows the 400 text; terminal statuses close; trade drafting matches names and picks from `team_assets`; no surplus in the JSON.
- `tests/test_inbox_kinds_contract.py` → `MAKE_OFFER` stores one `contract_offers` row through `_score` (fake cursor); ACCEPT/COUNTER/REJECT map to intents; `ACCEPT_COUNTER` sends the stored counter; ineligible → 409; ACCEPTED closes; drafting from text; the imported helpers exist.
- `tests/test_inbox_kinds_role_talk.py` → `LOST_STARTING_JOB` and `WANTS_MORE` fire once; `MOVE_UP` queues one `DEPTH_CHART` move equal to the old chart with one swap and cancels earlier pending charts; an illegal swap → 409; `PROMISE_LOOK` sets the review week and the later scan posts kept/not kept; only COACH actors.
- `tests/test_inbox_kinds_reporter.py` → picks the team's beat writer; at most 3 follow-ups then close; ON_RECORD needs a note; statements land in `jr_statements`; cap of 2; any actor role.
- `tests/test_inbox_api.py` → every §2.11 shape on patched store/kinds; `as` filtering; `/draft` never acts; 400/403/404/409; GET marks read; note limit.

Run with `pytest -m "not integration" tests/test_inbox_*.py tests/test_weekly_capture_inbox.py`. Frontend: `npm run build` in `Mu_Network/`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Inbox core (tables, actors, store, registry, voice, parse, scan, CLI, weekly hook)

- **Owns:** `db/migrations/2027_01_19_front_office_inbox.sql` (new), `db/init_db.sql`, `engines/inbox/__init__.py` (new), `engines/inbox/__main__.py` (new), `engines/inbox/actors.py` (new), `engines/inbox/store.py` (new), `engines/inbox/registry.py` (new), `engines/inbox/voice.py` (new), `engines/inbox/parse.py` (new), `engines/inbox/scan.py` (new), `engines/inbox/kinds/__init__.py` (new, empty), `pipeline/weekly_capture.py`, `tests/test_inbox_actors.py` (new), `tests/test_inbox_store.py` (new), `tests/test_inbox_registry.py` (new), `tests/test_inbox_voice.py` (new), `tests/test_inbox_parse.py` (new), `tests/test_inbox_scan.py` (new), `tests/test_weekly_capture_inbox.py` (new)
- **Reads:** this plan §2.1–§2.8; `db/franchises.py:160-180`; `engines/gm_negotiation.py:300-325`; `engines/gm_rationale.py:184`; `engines/gm_llm.py`; `personality/core.py`, `personality/store.py`; `newsroom/personas.py`; `pipeline/weekly_capture.py:540-576`; `db/migrations/2027_01_18_writer_voice.sql` (style); the sibling plans' §2 inbox sections (interfaces they rely on)
- **Depends on:** none
- **Done when:** the migration applies to `madden_test` and is in `init_db.sql`; `python3 -m engines.inbox --help` lists scan/list/show/control; with no kind files, `scan` creates nothing; the seven test files pass.
- **Verify:** `pytest -m "not integration" tests/test_inbox_actors.py tests/test_inbox_store.py tests/test_inbox_registry.py tests/test_inbox_voice.py tests/test_inbox_parse.py tests/test_inbox_scan.py tests/test_weekly_capture_inbox.py` all pass.

### U2: Inbox frontend module

- **Owns:** `Mu_Network/src/types/franchiseInbox.ts` (new), `Mu_Network/src/components/franchise/InboxModule.tsx` (new), `Mu_Network/src/components/franchise/FranchisePage.tsx`
- **Reads:** §2.3, §2.11–§2.12; `FranchisePage.tsx`, `ContractRoomModule.tsx`, `TradeMachineModule.tsx`, `DepthChartModule.tsx`, `Mu_Network/src/types/franchiseMarket.ts`
- **Depends on:** none (builds against §2.11's fixed JSON)
- **Done when:** `#franchise-inbox` renders the list, a thread, the "acting as" filter and the draft card from mocked fetches; the tab shows the unread count; the build passes.
- **Verify:** `cd Mu_Network && npm run build` exits 0.

### U3: Coach-side and interview kinds

- **Owns:** `engines/inbox/kinds/role_talk.py` (new), `engines/inbox/kinds/reporter.py` (new), `tests/test_inbox_kinds_role_talk.py` (new), `tests/test_inbox_kinds_reporter.py` (new)
- **Reads:** §2.2–§2.7; U1's `engines/inbox/*`; `server/franchise_api.py:314-360`, `:1007-1047` (pending moves, stored charts, `put_depth_chart`); `engines/depth_chart_engine.py:39-45`, `:328-420`; `agent_memories` (`db/init_db.sql:1911`); `newsroom/relationships.py:115`
- **Depends on:** U1
- **Done when:** both kinds pass their tests; `franchise_api.py` unchanged.
- **Verify:** `pytest -m "not integration" tests/test_inbox_kinds_role_talk.py tests/test_inbox_kinds_reporter.py tests/test_franchise_api.py` all pass.

### U4: GM-side kinds (trades and contracts)

- **Owns:** `engines/inbox/kinds/gm_talk.py` (new), `engines/inbox/kinds/contract.py` (new), `tests/test_inbox_kinds_gm_talk.py` (new), `tests/test_inbox_kinds_contract.py` (new)
- **Reads:** §2.2–§2.7, §2.13; U1's `engines/inbox/*`; `server/franchise_api.py:945-1290`; `engines/gm_negotiation.py:325-638`; `engines/front_office.py:480-520`; `engines/gm_trade_engine.py:1440-1450`; `tests/test_gm_negotiation.py`, `tests/test_franchise_api.py` (fakes); `docs/not_started/TRADE_NEGOTIATIONS_PLAN.md` §2.7 (what V2.11 will add, so the file is easy to extend)
- **Depends on:** U1
- **Done when:** both kinds pass their tests; `franchise_api.py` and `gm_negotiation.py` unchanged.
- **Verify:** `pytest -m "not integration" tests/test_inbox_kinds_gm_talk.py tests/test_inbox_kinds_contract.py tests/test_gm_negotiation.py tests/test_franchise_api.py` all pass.

### U5: Inbox API

- **Owns:** `server/inbox_api.py` (new), `server/api.py`, `tests/test_inbox_api.py` (new)
- **Reads:** §2.11; `server/franchise_api.py:1-230`; `server/debug_api.py:350-410`; U1's `engines/inbox/*`
- **Depends on:** U1
- **Done when:** every §2.11 endpoint answers with the documented shape on patched data; `server/api.py` mounts the router.
- **Verify:** `pytest -m "not integration" tests/test_inbox_api.py tests/test_franchise_api.py` pass; `python3 -c "import server.api"` exits 0 with `DATABASE_URL` pointing at `madden_test`.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2 | — | No shared files; U2 builds against §2.11's fixed shapes. |
| 2 | U3, U4, U5 | U1 | Each imports U1; kinds are separate files found by auto-discovery, so they don't touch each other or U5. |

After wave 2, the orchestrator runs the page against `madden_test` once (open a trade thread, make a contract offer by typing it, move a player up) and runs the quality gate in §3 before closing the plan.

**File ownership check:**

| File | Unit |
|---|---|
| `Mu_Network/src/components/franchise/FranchisePage.tsx` | U2 |
| `Mu_Network/src/components/franchise/InboxModule.tsx` | U2 |
| `Mu_Network/src/types/franchiseInbox.ts` | U2 |
| `db/init_db.sql` | U1 |
| `db/migrations/2027_01_19_front_office_inbox.sql` | U1 |
| `engines/inbox/__init__.py` | U1 |
| `engines/inbox/__main__.py` | U1 |
| `engines/inbox/actors.py` | U1 |
| `engines/inbox/kinds/__init__.py` | U1 |
| `engines/inbox/kinds/contract.py` | U4 |
| `engines/inbox/kinds/gm_talk.py` | U4 |
| `engines/inbox/kinds/reporter.py` | U3 |
| `engines/inbox/kinds/role_talk.py` | U3 |
| `engines/inbox/parse.py` | U1 |
| `engines/inbox/registry.py` | U1 |
| `engines/inbox/scan.py` | U1 |
| `engines/inbox/store.py` | U1 |
| `engines/inbox/voice.py` | U1 |
| `pipeline/weekly_capture.py` | U1 |
| `server/api.py` | U5 |
| `server/inbox_api.py` | U5 |
| `tests/test_inbox_actors.py` | U1 |
| `tests/test_inbox_api.py` | U5 |
| `tests/test_inbox_kinds_contract.py` | U4 |
| `tests/test_inbox_kinds_gm_talk.py` | U4 |
| `tests/test_inbox_kinds_reporter.py` | U3 |
| `tests/test_inbox_kinds_role_talk.py` | U3 |
| `tests/test_inbox_parse.py` | U1 |
| `tests/test_inbox_registry.py` | U1 |
| `tests/test_inbox_scan.py` | U1 |
| `tests/test_inbox_store.py` | U1 |
| `tests/test_inbox_voice.py` | U1 |
| `tests/test_weekly_capture_inbox.py` | U1 |

No path appears twice.

## 7. Spec changes when built

- §1.1 Repository Layout: `engines/inbox/` (package, `kinds/`), `server/inbox_api.py`, `InboxModule.tsx`, `types/franchiseInbox.ts`, the migration, the new tests.
- New §2.21b "Front-Office Inbox": controlled personas and the conversation matrix, who decides what, kinds with triggers/options/engines, voice (model first, template fallback, budget), typed-text drafts, scan and caps, CLI.
- New §3.3r: `controlled_personas`, `inbox_threads`, `inbox_messages`.
- New §4.6c: `/api/franchise/{abbr}/inbox*`.
- §5: the Inbox module, "acting as" filter and tab badge.
- §6.0 / §6.0b: `step_inbox` after `step_gm`; the scan on opening the inbox in the offseason.
- §3.3f: GM negotiations open from the inbox as well as `#debug`.
- §2.17c: depth-chart moves can be queued from a role talk.
- `V2_MASTER_PLAN.md` §6 V2.9 status (orchestrator, when built).

## 8. Decisions for JR

Settled in his review: D1–D7 above (controlled personas, per-team inbox, players speak for themselves, statements stored, no owner threads, interviews one-on-one with pressers in V2.2, inbox first, registry as truth, model first).

| # | Question | Recommendation |
|---|---|---|
| Q1 | How does JR say which personas he controls on a team? | Default to the head coach and the GM of every registry user team, plus a `controlled_personas` table (and `engines.inbox control` CLI) to change that per team. The registry stays the truth for teams. |
| Q2 | Build team-vision talks (coach ↔ GM) now? | No. Keep the §2.9 design and build it when JR first controls a team where coach and GM are different people, or with controlled-player mode. Today there's nobody on the other side (Whitfield and Grant hold both jobs). |
| Q3 | Typed text: draft-then-confirm, or forms only? | Draft-then-confirm for contract terms, depth-chart moves and trade packages: a deterministic parser first, the model as fallback limited to listed ids and ranges, and nothing acts without JR's click. |
| Q4 | Model on by default? | Yes (`MU_INBOX_LLM` on, `=0` for templates), with the 8 s budget set by the spike and the `llm_compare` gate before the plan closes. If the gate fails, templates stay the default until the prompt is fixed. |
| Q5 | Owner threads: none at all, or an empty stub kind? | None, and no stub file. `owner` stays a valid counterpart type. When owners come, start with the cap mandate and job security (§2.10). |
| Q6 | Should a role-talk promise ("I'll look at it") have any effect? | Record only, plus a kept/not-kept line at the review week. Effects on morale or trade requests belong to the personality trade-request plan. |
| Q7 | Coach "move you up": queue the depth-chart change directly from the thread? | Yes, with the Depth Chart module's checks and its pending-chart rules, so the thread and the module never disagree. |
| Q8 | How long is a one-on-one interview? | The opening question plus up to 3 follow-ups, then the reporter closes. JR can end it any time. |
