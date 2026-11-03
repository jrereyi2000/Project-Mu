# Project Mu: Front-Office Inbox & Direct Conversations Plan (V2.9)

**Status:** proposal, awaiting JR's approval (2026-11-03).
**Builds on:** [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §2, §5.3.2, §6 V2.0 and V2.9; [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) §8 and phase 6; PROJECT_SPEC §3.3f (GM negotiations), §3.3p (personas), §3.3q, §4.6b (franchise API), §2.21a (league office).

**Goal:** One place in the Franchise Command Center where people in the league write to JR and JR writes back: his team's owner, players about their contracts, rival GMs, his beat reporter, and (through sibling plans) prospects and the press. Every message that matters is tied to something the engines already know or decide. Engines make every number and every deal; the language model only words the replies and never states a fact that isn't on the thread's fact sheet. The inbox is also the shared conversation surface the sibling plans (V2.2 press conferences, V2.10 combine interviews, V2.11 trade negotiations) plug their conversations into, so JR has one inbox, not four.

**Delivers:**
- Tables `inbox_threads` and `inbox_messages` (migration `db/migrations/2027_01_19_front_office_inbox.sql`, folded into `db/init_db.sql`).
- Package `engines/inbox/`: `store.py` (threads, messages, dedupe), `registry.py` (the conversation-kind protocol and auto-discovery), `voice.py` (dialogue writer: personality or neutral voice, fact-sheet validation, template fallback, time budget), `scan.py` (inbound message generation), `__main__.py` (CLI `python3 -m engines.inbox scan|list|show`).
- Four conversation kinds in `engines/inbox/kinds/`: `owner.py` (OWNER, neutral voice), `reporter.py` (REPORTER, the team's local beat writer), `contract.py` (CONTRACT, contract talks over `contract_offers`), `gm_talk.py` (GM_TALK: a rival GM's trade interest, and JR's multi-turn talks through the existing `engines/gm_negotiation.py`).
- Endpoints `/api/franchise/{abbr}/inbox*` in a new `server/inbox_api.py`.
- An **Inbox** module (`#franchise-inbox`) in the Command Center with an unread count on its tab.
- A `step_inbox` call at the end of `weekly_capture.run_weekly_engines`.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `engines/gm_negotiation.py:425` `open_negotiation`, `:460` `offer`, `:503` `accept_counter`, `:529` `close` already run multi-turn trade talks with a GM: patience, counters, moods, one OPEN talk per team pair, turns stored in `gm_trade_negotiation_turns` (spec §3.3f). They are reachable only from `#debug` (`server/debug_api.py:350`, `:374`–`:409`). | Trade talk needs no new engine. The inbox wraps these functions in a thread; V2.11 then deepens the same thread kind (leaks, posturing, CPU-to-CPU transcripts). |
| S2 | `engines/gm_negotiation.py:306` `gm_message` is the house dialogue pattern: build a fact sheet, ask the LLM for JSON `{"message"}`, keep it only if `engines/gm_rationale.py:184` `validate_rationale(text, sheet, league)` passes (no money, number, player or team not on the sheet), else `template_message`. Source is stored as `LLM` or `TEMPLATE`. | `engines/inbox/voice.py` reuses this pattern and `validate_rationale` for every kind. No new validator. |
| S3 | `server/franchise_api.py:1266` `propose` scores an extension/re-sign with `_score` (`:945`) after `_eligibility` (`:1237`) and stores a `contract_offers` row (`db/init_db.sql:578`; status OPEN/ACCEPTED/REJECTED/COUNTERED, `counter` JSONB). `pipeline/league_office.py:1156` `step_accepted_offers` writes JR's ACCEPTED offers to the save. | A contract conversation can be real today: each JR offer in a CONTRACT thread is one `contract_offers` row scored by the same code, and an accepted one reaches the save through the league office. No new contract maths. |
| S4 | `server/franchise_api.py:1395` `trades_evaluate` calls `gm_negotiation.respond` as a one-shot preview; it stores nothing. | The Trade Machine stays a preview tool. Real talks start from the inbox (or a "Talk to their GM" link, §1 Out). |
| S5 | `personality/core.py:14` `ROLES` includes `owner` and `scout`; `:144` `feature_view(profile, feature)` returns all six sliders at 5 and no traits when the profile is missing (`status: 'missing'`), and for real-origin profiles only evidence-approved sliders/traits. | Owners speak neutrally for free: the voice asks `feature_view(..., 'inbox')` and gets a neutral view until personality phase 6 seeds owners. No special case in code. |
| S6 | No owner table or owner name exists anywhere: the only `owner` in `db/init_db.sql` is the persona-type CHECK (`:1663`) and a draft "owner on the clock" column (`:1113`). | Owners have no names until phase 6. The thread shows "<Team> ownership" (§2.5, Q2). |
| S7 | `PERSONALITY_PLAN.md:216`: "A player's own AI loop does his negotiating; no hired agent sits between him and the GM." | "Player agents" in V2.9 means the player himself (his camp). No agent persona type (Q3). |
| S8 | `agent_memories` (`db/init_db.sql:1911`) has `assigned_team_id`, `display_name`, `person_key`; `LOCAL_BEAT_REPORTER` is an archetype in `newsroom/config/agent_personas.json`; writers already have sliders/traits in `persona_sliders`/`persona_traits` (spec §3.3p). | The REPORTER counterpart is the team's beat writer, voiced with his existing persona. |
| S9 | `engines/gm_trade_engine.py:1447`–`:1448`, `:1621`: ENGINE proposals that involve a user-controlled team are stored with an `advisory:<ABBR>` flag; `engines/front_office.py:492` `approve`, `:508` `reject`. | A rival GM's interest in JR's players already exists as PROPOSED rows. GM_TALK turns each into an inbound message; "not interested" calls `reject`. |
| S10 | `pipeline/weekly_capture.py:560` `run_weekly_engines` runs `step_gm` (proposals) then scouting, archive, carousel. In the offseason the watcher runs only the load and the league office, no engines (spec §6.0b step 4). | The weekly hook goes after `step_gm`. Offseason inbound messages come from a lazy scan when JR opens the inbox (templates only, fast). |
| S11 | `server/franchise_api.py:148` router prefix `/api/franchise`; `:223` `require_user_team` gives 403 outside the active franchise's `user_teams`; `server/api.py:20`, `:149` include routers. CAR and IND are both user teams. | The inbox is per team under the same prefix and the same 403 rule. |
| S12 | `Mu_Network/src/components/franchise/FranchisePage.tsx:23` `MODULES` is a list of `{slug, label, Component}`; each module takes `{ teamAbbr }` and fetches its own data. | The Inbox is one more entry; the only shared frontend file is `FranchisePage.tsx`. |
| S13 | `engines/gm_llm.py:60` `make_llm(label, ...)`, default model `gemini-3.8-flash` (`:11`). V2 master §5.3.2 and V3 §7: interactive talk stays responsive; no latency is guaranteed until measured on the PC. | Interactive replies use `make_llm("inbox")` with a hard time budget and a template fallback, so a slow model never blocks a decision (§2.4). |
| S14 | Last migration is `db/migrations/2027_01_18_writer_voice.sql`. | Next free: `2027_01_19_front_office_inbox.sql`. The sibling plans written today may pick the same date (R1). |

## 1. Scope

**In:**
- Thread and message storage, one inbox per user team, unread state, expiry.
- A conversation-kind registry that sibling plans extend by adding one file each.
- Four kinds: OWNER, REPORTER, CONTRACT, GM_TALK (definitions in §2.3).
- Inbound messages generated from engine facts each week (and lazily in the offseason), deduplicated.
- JR's replies: pick one of the engine-permitted options for that message, with an optional typed note; or a typed note alone (talk, no action).
- In-character replies from the counterpart, LLM-worded and validated against a fact sheet, template on failure or timeout.
- JR-started threads: a contract talk with one of his players, trade talk with a GM, a note to ownership.
- The Inbox module in `Mu_Network`.

**Out (non-goals):**
- Press conferences themselves (V2.2). The inbox only defines a `PRESS` kind slot and a SYSTEM notice shape V2.2 can use.
- Prospect and combine interviews, scout reports (V2.10). The inbox defines the `PROSPECT` and `SCOUT` kind slots only.
- Leaks to insiders, posturing, published CPU-to-CPU transcripts, new negotiation maths (V2.11). GM_TALK only wraps today's `gm_negotiation`.
- Owner personalities, names, owner effects on JR (budget, firing): personality phase 6 and league office phase H.
- Holdouts and trade requests as inbox messages: [`PERSONALITY_TRADE_REQUESTS_PLAN.md`](./PERSONALITY_TRADE_REQUESTS_PLAN.md) adds a kind file when built.
- Writers using JR's on-record statements in articles, and reporter trust changes from JR's answers (Q4: shared with V2.2).
- Turning free text into an action (an LLM reading "give him 3 years at 40" and making the offer). Actions come only from buttons/forms in this plan (Q5).
- "Discuss in inbox" links from the Contract Room and Trade Machine (they would edit those modules; a small follow-up).
- National insiders (Schefter, Rapoport personas) messaging JR (Q6).

## 2. Design

### 2.1 Tables (`db/migrations/2027_01_19_front_office_inbox.sql`, mirrored in `db/init_db.sql`)

```sql
CREATE TABLE IF NOT EXISTS inbox_threads (
    id                SERIAL PRIMARY KEY,
    team_id           INT NOT NULL REFERENCES teams(id),       -- JR's team the thread belongs to
    kind              VARCHAR(24) NOT NULL,                    -- registry key; no CHECK (sibling plans add kinds)
    counterpart_type  VARCHAR(16) NOT NULL
        CHECK (counterpart_type IN ('owner','player','gm','writer','prospect','scout','system')),
    counterpart_id    VARCHAR(100) NOT NULL,                   -- 'CAR' (owner), players.id, gm_profiles.id, agent_memories.agent_id, prospect_key (V2.10: draft_prospects.id changes on a board re-scan)
    counterpart_name  VARCHAR(255) NOT NULL,                   -- display name at creation ('Carolina ownership')
    subject           VARCHAR(255) NOT NULL,
    status            VARCHAR(12) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN','WAITING_JR','CLOSED','EXPIRED')),
    started_by        VARCHAR(12) NOT NULL CHECK (started_by IN ('JR','COUNTERPART','SYSTEM')),
    source_ref        VARCHAR(120) NOT NULL,                   -- dedupe key, e.g. 'gm_trade_proposals:812', 'owner:preseason:2036'
    link_table        VARCHAR(48),                             -- 'gm_trade_negotiations' | 'contract_offers' | sibling tables
    link_id           INT,
    season            INT NOT NULL,
    week              INT,                                     -- league_state.current_week at creation (NULL offseason)
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
    option_id    VARCHAR(40),          -- the option JR picked (author JR), NULL for a note alone
    options      JSONB NOT NULL DEFAULT '[]',  -- options offered to JR with this message (author COUNTERPART/SYSTEM)
    facts        JSONB,                -- the fact sheet the body was checked against (audit)
    engine_ref   JSONB,                -- what the engine did: {"offer_id": 512, "decision": "COUNTER"} / {"negotiation_id": 9, "turn_no": 3}
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (thread_id, seq)
);
```

Options shape (stored in `options`, returned by the API):
`{"id": "MAKE_OFFER", "label": "Make an offer", "form": "contract_terms" | "trade_assets" | null, "needs_note": false, "default": {...} | null}`.
`form` tells the UI which input to show; `default` pre-fills it (a counter's terms). Every option a handler returns is one its engine allows right now; `act` re-checks it.

### 2.2 Registry (`engines/inbox/registry.py`)

```python
@dataclass(frozen=True)
class Trigger:                 # one inbound message the scan wants to create
    kind: str; team_id: int; counterpart_type: str; counterpart_id: str; counterpart_name: str
    subject: str; source_ref: str; facts: dict; intent: str     # intent: e.g. 'PRESEASON_EXPECTATIONS'
    link_table: str | None = None; link_id: int | None = None
    expires_season: int | None = None; expires_week: int | None = None

@dataclass
class ActResult:
    facts: dict                # fact sheet for the counterpart's reply
    intent: str                # what the reply must convey: 'ACCEPTED', 'COUNTER', 'NOTED', ...
    engine_ref: dict | None
    close: bool = False        # thread ends after this reply
    link: tuple | None = None  # (link_table, link_id) to set on the thread

class Kind(Protocol):
    KIND: str                              # 'OWNER'
    COUNTERPART_TYPE: str                  # 'owner'
    PERSONA_ROLE: str | None               # personality ROLES entry used for the voice, None = neutral always
    def scan(self, cur, season: int, week: int | None, team: dict) -> list[Trigger]: ...
    def options(self, cur, thread: dict, last: dict) -> list[dict]: ...
    def act(self, cur, thread: dict, option_id: str | None, payload: dict | None, note: str | None) -> ActResult: ...
    def template(self, intent: str, facts: dict) -> str: ...
    def start(self, cur, team: dict, body: dict) -> Trigger: ...   # JR-started thread; raise InboxError(400) if not allowed
    # Optional: def validate(self, text: str, facts: dict) -> bool   # extra fact check for this kind (V2.10 prospects)

def kinds() -> dict[str, Kind]   # auto-discovers every module in engines/inbox/kinds/ that defines KIND (pkgutil),
                                 # so a sibling plan adds a file and edits nothing shared.
```

`InboxError(status, message)` mirrors `FrontOfficeError` and becomes an HTTP error in the API. `act(option_id=None, note=...)` is talk only by default: no engine call, intent `TALK`. A kind may treat it otherwise (V2.10's PROSPECT kind classifies the note as a typed question).

### 2.3 The four kinds

| Kind | Counterpart | Inbound triggers (scan) | JR's options | Engine behind `act` |
|---|---|---|---|---|
| `OWNER` | `owner`, id = team abbr, name "<City> ownership" | `PRESEASON_EXPECTATIONS` (first scan of a season: direction from `gm_team_seasons`, expected wins from `engines/gm_rollover.py:22` `EXPECTED_WINS`); `MIDSEASON_CHECK` (week 9: record vs that pace); `LOSING_STREAK` (3+ straight losses, once per streak); `SEASON_REVIEW` (season over for the team); `CAP_WARNING` (next year's projected cap room < 0, from the same cap code as `/cap`) | `ACKNOWLEDGE`, `REASSURE` (note), `PUSH_BACK` (note) | None. Recorded only; no effect on anything (owner effects are phase 6/H). |
| `REPORTER` | `writer`, the team's `LOCAL_BEAT_REPORTER` (`agent_memories.assigned_team_id`) | `COMMENT_REQUEST` on: a starter injured 4+ weeks, an executed trade or signing involving the team, a 3-game streak, a benching (starter lost his depth-chart spot). At most 2 open REPORTER threads per team | `ON_RECORD` (note required), `OFF_RECORD` (note), `NO_COMMENT` | None in this plan. The answer is stored with `option_id`; `jr_statements(cur, team_id, season, week=None)` in `engines/inbox/store.py` reads them for V2.2/newsroom (Q4). |
| `CONTRACT` | `player`, id = `players.id`, name = player | `EXTENSION_WINDOW` when a starter becomes eligible (same `_eligibility` as the Contract Room) and has no open thread; `COUNTER_WAITING` is not a trigger (it's a reply) | `MAKE_OFFER` (form `contract_terms`), `ACCEPT_COUNTER` (default = last counter), `WALK_AWAY` | `server.franchise_api` helpers `_context`, `_player`, `_eligibility`, `_score`, `insert_offer` (imported lazily; §3 R4). Each offer is a `contract_offers` row (`from_user` true); the thread links to the newest. ACCEPTED closes the thread; the league office writes the contract (S3). |
| `GM_TALK` | `gm`, id = `gm_profiles.id`, name = `gm_name` | `TRADE_INTEREST`: an ENGINE `gm_trade_proposals` row, status PROPOSED, `advisory:<JR's team>` flag (S9), not yet in a thread | Inbound: `OPEN_TALKS` (form `trade_assets`, default = the proposal's legs), `NOT_INTERESTED`. In open talks: `MAKE_OFFER` (form `trade_assets`), `ACCEPT_COUNTER`, `END_TALKS` | `gm_negotiation.open_negotiation` / `offer` / `accept_counter` / `close` and `front_office.reject`. The GM's own reply text comes from `gm_negotiation` (it already writes the GM's message per turn); the inbox copies it as the COUNTERPART message with `body_source` from the turn, so the words are never written twice. AGREED, WALKED_AWAY, CLOSED, EXPIRED close the thread. |

JR-started threads (`start`): OWNER (a note to ownership, talk only), CONTRACT (`player_id`, refused when `_eligibility` fails), GM_TALK (`gm_team` + first offer's assets → `open_negotiation`). REPORTER is inbound only.

Every scan is pure reads plus inserts keyed by `source_ref`, so running it twice changes nothing. A thread expires when its source does: the proposal's `expires_week`, the end of the extension window, or 2 weeks without a reply for OWNER/REPORTER (`INBOX_STALE_WEEKS = 2`).

### 2.4 Voice (`engines/inbox/voice.py`)

```python
def persona_view(cur, counterpart_type, counterpart_id, role) -> dict
    # personality.store.read + core.feature_view(profile, 'inbox'); missing / owner / scout -> neutral view.
    # Writers and GMs fall back to persona_sliders/persona_traits (newsroom.personas) when no core profile exists.
def write(cur, kind, thread, intent, facts, history, note=None, llm=None, budget_s=INBOX_LLM_BUDGET_S) -> tuple[str, str]
    # (text, 'LLM' | 'TEMPLATE'). Builds the prompt: counterpart's voice (sliders, traits as 'tends to' lines, or
    # 'plain, professional, no personality' when neutral), the fact sheet, the last 6 messages, JR's note, the intent.
    # LLM answer kept only if validate_rationale(text, facts_as_sheet, league) passes, the kind's optional
    # validate(text, facts) passes (called via getattr), and it doesn't contradict the
    # intent (an 'ACCEPTED' reply that says no is rejected by a keyword check); else Kind.template(intent, facts).
INBOX_LLM_BUDGET_S = 8.0     # estimate; set after the PC measurement (§3 spike)
INBOX_LLM_ENV = "MU_INBOX_LLM"   # '1' turns the LLM on; default off until the quality gate (V2.0) passes
```

Order inside every reply request: (1) the kind's `act` runs the engine and commits its decision in the same transaction as JR's message; (2) the voice writes the reply within the budget; (3) the reply is stored. A timeout or failed validation gives the template; the decision never waits on the words. The fact sheet uses the `gm_rationale` sheet shape (`{"assets": [...], "numbers": [...], "money": [...]}`) so `validate_rationale` works unchanged.

### 2.5 Neutral counterparts

Owners (and scouts, for V2.10) are neutral until personality phase 6: `feature_view` returns the neutral view (S5), the prompt says "plain, professional", and templates are written neutral. Owners have no names: the name is "<City> ownership" (`teams.city`), and the prompt forbids inventing a name. When phase 6 seeds owner profiles, `persona_view` picks them up with no inbox change; names would come from that plan.

### 2.6 Scan (`engines/inbox/scan.py`)

```python
def scan(cur, season, week, teams=None, llm=None) -> dict   # {'created': n, 'expired': n, 'by_kind': {...}}
    # teams default = the active franchise's user teams (server.franchise_api.user_teams() logic, via db.franchises).
    # For each team and each registered kind: kind.scan -> Trigger -> store.create_thread (ON CONFLICT DO NOTHING)
    # -> first COUNTERPART message via voice.write (llm may be None = templates). Then store.expire_stale.
def step_inbox(season)   # in pipeline/weekly_capture.py, after step_gm; logs and never raises (like step_coach_hot_seat)
```

The API runs `scan(..., llm=None, teams=[team])` when JR opens the inbox list, at most once per `(team, season, week, stage)` per server process (an in-process cache); the `source_ref` dedupe keys make a repeat scan harmless anyway. This is how offseason messages appear, since the watcher runs no engines then (S10). CLI: `python3 -m engines.inbox scan [--season S --week W] [--team CAR] [--llm] [--dry-run]`, `list --team CAR [--status OPEN]`, `show <thread_id>`.

### 2.7 API (`server/inbox_api.py`, router prefix `/api/franchise`)

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/{abbr}/inbox` | `?status=OPEN,WAITING_JR&kind=&limit=50` | `{"team": "CAR", "unread": 3, "threads": [Thread]}` |
| GET | `/{abbr}/inbox/kinds` | — | `[{"kind": "CONTRACT", "can_start": true, "start_form": "player"}]` |
| GET | `/{abbr}/inbox/{thread_id}` | — | `{"thread": Thread, "messages": [Message], "options": [Option]}` (marks read) |
| POST | `/{abbr}/inbox/{thread_id}/reply` | `{"option_id": "MAKE_OFFER" \| null, "payload": {...} \| null, "note": "..." \| null}` | same as GET thread, with the new JR and COUNTERPART messages |
| POST | `/{abbr}/inbox` | `{"kind": "CONTRACT", "player_id": 123, "note": "..."}` / `{"kind": "GM_TALK", "gm_team": "ATL", "assets": [...], "note": ...}` / `{"kind": "OWNER", "note": "..."}` | 201, the new thread as GET thread |
| POST | `/{abbr}/inbox/{thread_id}/close` | — | the thread |

`Thread = {id, kind, counterpart: {type, id, name}, subject, status, unread, started_by, season, week, expires: {season, week} | null, link: {table, id} | null, last: {author, body, created_at}, updated_at}`.
`Message = {id, seq, author, body, source, option_id, engine_ref, created_at}` (`facts` never sent; a GM's `surplus_*` never appears).
Errors `{"detail": string}`: 400 bad body or an option not offered, 403 not a user team (same rule as `require_user_team`), 404 unknown thread or a thread of another team, 409 thread CLOSED/EXPIRED or the engine refused (message from `InboxError` / `FrontOfficeError`). `reply` with neither option nor note → 400. The note is at most 600 characters. The LLM client is `make_llm("inbox")` only when `MU_INBOX_LLM=1`.

### 2.8 Frontend

- `Mu_Network/src/types/franchiseInbox.ts`: `InboxThread`, `InboxMessage`, `InboxOption`, `InboxList`, `InboxThreadDetail` matching §2.7 exactly.
- `Mu_Network/src/components/franchise/InboxModule.tsx` (`{ teamAbbr }`): left list (filters: open / all; kind chips; unread dot; counterpart and subject; expiry), right conversation (bubbles by author, a small "template" tag when `source` is TEMPLATE, the options as buttons, the form for `contract_terms` (years, total, bonus, guaranteed, pre-filled from `default`) and `trade_assets` (the two rosters and picks from the existing `/trades` assets shape the Trade Machine uses), a note box, Send). "New message" opens a picker built from `/inbox/kinds`. A sent reply shows JR's message at once and a "…" until the response returns.
- `FranchisePage.tsx`: add `{ slug: "inbox", label: "Inbox", Component: InboxModule }` after Game Center, and an unread count on the tab from `GET /{abbr}/inbox?status=OPEN,WAITING_JR&limit=1`.

### 2.9 How sibling plans plug in

| Sibling | What it adds to the inbox | Shared files |
|---|---|---|
| V2.11 multi-turn trade negotiations | Owns deeper behaviour of `GM_TALK` (posturing lines, leak option, walk-away tells) by editing `engines/inbox/kinds/gm_talk.py` after this plan merges; may add a `TRADE_LEAK` REPORTER intent. CPU-to-CPU transcripts are newsroom output, not inbox threads. | `engines/inbox/kinds/gm_talk.py`, `engines/gm_negotiation.py` |
| V2.10 scouting and combine interviews | A new file `engines/inbox/kinds/prospect.py` (`KIND = "PROSPECT"`, counterpart `prospect`, link `draft_prospects`), and optionally `scout.py` (neutral scouts). Interview truth stays in the scouting engines. | none if it only adds files |
| V2.2 press conferences | A new file `engines/inbox/kinds/press.py` (`KIND = "PRESS"`, counterpart `system` or `writer`): a SYSTEM notice "postgame presser ready" linking to its own screen, and reporter follow-ups. Uses `jr_statements` (or its own quotes table) for articles. | `engines/inbox/store.py` if it extends `jr_statements` |

The kind name, counterpart types (`prospect`, `scout`, `system` are already in the CHECK) and the `link_table`/`link_id` pair are fixed here so siblings add files and touch no shared code.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Shared files with the sibling plans: `db/init_db.sql`, the migration date `2027_01_19`, `server/api.py` (router include), `pipeline/weekly_capture.py`, `FranchisePage.tsx`, `engines/inbox/kinds/gm_talk.py` (V2.11). Parallel builds will collide. | The orchestrator compares the four plans' Owns lists before any wave. | Build V2.9 first (Q1). Siblings take later migration dates (`2027_01_20`+) and edit `gm_talk.py`/`api.py`/`FranchisePage.tsx` only after V2.9 is on main. |
| R2 | LLM latency beside a running Madden is unmeasured (§5.3.2). | Spike below; `voice.write` logs time per call. | Budget hits give templates; the decision is already stored. `MU_INBOX_LLM` stays off. |
| R3 | The LLM states a fact not on the sheet, or contradicts the decision ("he's in" on a COUNTER). | `validate_rationale` plus the intent keyword check; tests with bad model outputs. | Template is used; the bad text is never stored. |
| R4 | `CONTRACT` imports private helpers from `server/franchise_api.py` (an engine importing a server module). A later rename breaks the inbox. | `tests/test_inbox_kinds_contract.py` imports them; any rename fails the test. | Later: move `_score`/`_eligibility` into an engine module in a plan that owns `franchise_api.py`. Not done here to keep that file out of this plan. |
| R5 | GM_TALK and the `#debug` negotiation buttons can both act on the same `gm_trade_negotiations` row. | Both go through `gm_negotiation`, which checks status per call. | The thread reads the negotiation's turns on every GET, so it shows actions taken on `#debug` too. |
| R6 | An AGREED trade becomes an APPROVED proposal (`gm_negotiation.py:539`); whether the league office writes it to the save without a `franchise_pending_moves` TRADE row is V2.11's question. | V2.11's survey. | The inbox says "Agreed. The front office will process it" (template) and links the proposal; it claims nothing about the save. |
| R7 | Too many inbound messages (32 GMs' advisory proposals, every injury). | Scan counts in tests on a synthetic busy week. | Caps: `MAX_OPEN_PER_KIND = 3` per team for GM_TALK and REPORTER, 1 OWNER; oldest-first expiry. |
| R8 | Two user teams (CAR, IND): JR may expect one combined inbox. | JR (Q7). | The API is per team; a combined view is a frontend change only. |

**Spike first:** a 30-minute check on the PC (JR or the orchestrator, before wave 2, not blocking wave 1): call `make_llm("inbox")` 20 times with a filled CONTRACT prompt while Madden is open and record p50/p95 seconds. It sets `INBOX_LLM_BUDGET_S` and whether `MU_INBOX_LLM` can be on by default. If p95 > 8 s, the inbox ships template-first.

## 4. Tests (synthetic, no live DB)

- `tests/test_inbox_store.py` → create thread is idempotent on `(team_id, kind, source_ref)`; messages get consecutive `seq`; `expire_stale` by week, stage and `INBOX_STALE_WEEKS`; caps per kind; `jr_statements` returns only ON_RECORD/OFF_RECORD with the flag; unread toggles.
- `tests/test_inbox_registry.py` → auto-discovery finds a kind file dropped into a temp package; a module without `KIND` is ignored; duplicate `KIND` raises.
- `tests/test_inbox_voice.py` → neutral view for a missing profile and for `owner`; writer and GM views from stored sliders; LLM text with an invented dollar amount, player, or a contradiction of the intent → template; timeout (fake slow LLM) → template within the budget; `llm=None` → template; prompt for a neutral counterpart contains no traits.
- `tests/test_inbox_scan.py` → each trigger fires once; a rerun creates nothing; teams default to user teams; one failing kind doesn't stop the others.
- `tests/test_inbox_kinds_owner_reporter.py` → OWNER triggers (preseason, week 9, 3-loss streak once per streak, season review, cap warning) from fake rows; options; `act` has no engine side effects; REPORTER picks the team's beat writer, requires a note for ON_RECORD, respects the cap.
- `tests/test_inbox_kinds_contract.py` → `MAKE_OFFER` stores one `contract_offers` row through `_score` (fake cursor) with ACCEPT/COUNTER/REJECT mapped to intents; `ACCEPT_COUNTER` sends the stored counter terms; ineligible player → 409; ACCEPTED closes the thread; the private helpers it imports exist.
- `tests/test_inbox_kinds_gm_talk.py` → advisory PROPOSED proposal becomes one TRADE_INTEREST thread; `NOT_INTERESTED` calls `front_office.reject`; `OPEN_TALKS`/`MAKE_OFFER`/`ACCEPT_COUNTER`/`END_TALKS` call the matching `gm_negotiation` function (patched) and copy the GM's turn message; terminal statuses close the thread; surplus never in the thread JSON.
- `tests/test_inbox_api.py` → every endpoint's shape (§2.7) on patched store/kinds; 400/403/404/409 cases; GET thread marks read; reply with neither option nor note → 400; note length limit.
- `tests/test_weekly_capture_inbox.py` → `run_weekly_engines` calls `step_inbox` after `step_gm` in weekly/playoff modes; a raising scan is logged, not raised.

Run with `pytest -m "not integration" tests/test_inbox_*.py tests/test_weekly_capture_inbox.py`. Frontend: `npm run build` in `Mu_Network/`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Inbox core (tables, store, registry, voice, scan, CLI, weekly hook)

- **Owns:** `db/migrations/2027_01_19_front_office_inbox.sql` (new), `db/init_db.sql`, `engines/inbox/__init__.py` (new), `engines/inbox/__main__.py` (new), `engines/inbox/store.py` (new), `engines/inbox/registry.py` (new), `engines/inbox/voice.py` (new), `engines/inbox/scan.py` (new), `engines/inbox/kinds/__init__.py` (new, empty package marker), `pipeline/weekly_capture.py`, `tests/test_inbox_store.py` (new), `tests/test_inbox_registry.py` (new), `tests/test_inbox_voice.py` (new), `tests/test_inbox_scan.py` (new), `tests/test_weekly_capture_inbox.py` (new)
- **Reads:** this plan §2.1–§2.6; `engines/gm_negotiation.py:300-325`; `engines/gm_rationale.py:184`; `engines/gm_llm.py`; `personality/core.py`, `personality/store.py`; `newsroom/personas.py`; `pipeline/weekly_capture.py:540-576`; `db/migrations/2027_01_18_writer_voice.sql` (style)
- **Depends on:** none
- **Done when:** the migration applies to `madden_test` and is in `init_db.sql`; `python3 -m engines.inbox --help` lists scan/list/show; with no kind files, `scan` returns zero created; all five test files pass.
- **Verify:** `pytest -m "not integration" tests/test_inbox_store.py tests/test_inbox_registry.py tests/test_inbox_voice.py tests/test_inbox_scan.py tests/test_weekly_capture_inbox.py` all pass.

### U2: Inbox frontend module

- **Owns:** `Mu_Network/src/types/franchiseInbox.ts` (new), `Mu_Network/src/components/franchise/InboxModule.tsx` (new), `Mu_Network/src/components/franchise/FranchisePage.tsx`
- **Reads:** this plan §2.7–§2.8; `FranchisePage.tsx`, `ContractRoomModule.tsx`, `TradeMachineModule.tsx` (forms and the trade-assets shape), `Mu_Network/src/types/franchiseMarket.ts`
- **Depends on:** none (builds against §2.7's fixed JSON)
- **Done when:** `#franchise-inbox` renders the list and a thread from mocked fetches in dev; the tab shows an unread count; the build passes.
- **Verify:** `cd Mu_Network && npm run build` exits 0.

### U3: Owner and reporter kinds

- **Owns:** `engines/inbox/kinds/owner.py` (new), `engines/inbox/kinds/reporter.py` (new), `tests/test_inbox_kinds_owner_reporter.py` (new)
- **Reads:** §2.2–§2.5; `engines/inbox/registry.py`, `voice.py`, `store.py`; `engines/gm_rollover.py:22`; `server/franchise_api.py` (cap code behind `/cap`, `/medical`); `newsroom/relationships.py:115` (person keys); `agent_memories` in `db/init_db.sql:1911`
- **Depends on:** U1
- **Done when:** a synthetic season scan creates each OWNER and REPORTER trigger once; replies store the option and a template reply.
- **Verify:** `pytest -m "not integration" tests/test_inbox_kinds_owner_reporter.py` passes.

### U4: Contract and GM-talk kinds

- **Owns:** `engines/inbox/kinds/contract.py` (new), `engines/inbox/kinds/gm_talk.py` (new), `tests/test_inbox_kinds_contract.py` (new), `tests/test_inbox_kinds_gm_talk.py` (new)
- **Reads:** §2.2–§2.4, §2.9; `server/franchise_api.py:945-1290`; `engines/gm_negotiation.py:325-638`; `engines/front_office.py:480-520`; `engines/gm_trade_engine.py:1440-1450`; `tests/test_gm_negotiation.py`, `tests/test_franchise_api.py` (fakes)
- **Depends on:** U1
- **Done when:** both kinds pass their tests; no edit to `franchise_api.py` or `gm_negotiation.py`.
- **Verify:** `pytest -m "not integration" tests/test_inbox_kinds_contract.py tests/test_inbox_kinds_gm_talk.py tests/test_gm_negotiation.py tests/test_franchise_api.py` all pass.

### U5: Inbox API

- **Owns:** `server/inbox_api.py` (new), `server/api.py`, `tests/test_inbox_api.py` (new)
- **Reads:** §2.7; `server/franchise_api.py:1-230` (docstring style, `_connect`, `require_user_team`, `user_teams`); `server/debug_api.py:350-410`; `engines/inbox/*`
- **Depends on:** U1
- **Done when:** every §2.7 endpoint answers with the documented shape on patched data; `server/api.py` includes the router.
- **Verify:** `pytest -m "not integration" tests/test_inbox_api.py tests/test_franchise_api.py` passes; `python3 -c "import server.api"` exits 0 with `DATABASE_URL` pointing at `madden_test`.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2 | — | No shared files; U2 builds against §2.7's fixed shapes. |
| 2 | U3, U4, U5 | U1 | Each imports U1's store/registry/voice; kinds are separate files found by auto-discovery, so they never touch each other or U5. |

After wave 2, the orchestrator runs the page against `madden_test` once (open a thread, make an offer) before closing the plan.

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
| `engines/inbox/kinds/__init__.py` | U1 |
| `engines/inbox/kinds/contract.py` | U4 |
| `engines/inbox/kinds/gm_talk.py` | U4 |
| `engines/inbox/kinds/owner.py` | U3 |
| `engines/inbox/kinds/reporter.py` | U3 |
| `engines/inbox/registry.py` | U1 |
| `engines/inbox/scan.py` | U1 |
| `engines/inbox/store.py` | U1 |
| `engines/inbox/voice.py` | U1 |
| `pipeline/weekly_capture.py` | U1 |
| `server/api.py` | U5 |
| `server/inbox_api.py` | U5 |
| `tests/test_inbox_api.py` | U5 |
| `tests/test_inbox_kinds_contract.py` | U4 |
| `tests/test_inbox_kinds_gm_talk.py` | U4 |
| `tests/test_inbox_kinds_owner_reporter.py` | U3 |
| `tests/test_inbox_registry.py` | U1 |
| `tests/test_inbox_scan.py` | U1 |
| `tests/test_inbox_store.py` | U1 |
| `tests/test_inbox_voice.py` | U1 |
| `tests/test_weekly_capture_inbox.py` | U1 |

No path appears twice.

## 7. Spec changes when built

- §1.1 Repository Layout: `engines/inbox/` (package and `kinds/`), `server/inbox_api.py`, `InboxModule.tsx`, `types/franchiseInbox.ts`, the migration, the new tests.
- New §2.21b "Front-Office Inbox" (engines): registry protocol, the four kinds and their triggers/options/engines, voice and time budget, scan and caps, CLI.
- New §3.3r: `inbox_threads`, `inbox_messages`.
- New §4.6c: `/api/franchise/{abbr}/inbox*` endpoints and shapes.
- §5 (frontend): the Inbox module and tab badge in the Command Center.
- §6.0 / §6.0b: `step_inbox` after `step_gm` in `run_weekly_engines`; offseason lazy scan on read.
- §3.3f: GM negotiations can now be opened and driven from the inbox as well as `#debug`.
- `V2_MASTER_PLAN.md` §6 V2.9 status (orchestrator, when built).

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | Build order with the three sibling plans? | V2.9 first (or at least its U1), then V2.10/V2.11/V2.2 add kind files. It avoids collisions on `init_db.sql`, `server/api.py`, `FranchisePage.tsx` and `gm_talk.py`. |
| Q2 | What do owners look like before personality phase 6? | No name, "<City> ownership", neutral voice, messages tied to record/direction/cap only, no effect on JR. Names and personalities arrive with phase 6. |
| Q3 | "Player agents": a separate agent persona, or the player himself? | The player himself, as PERSONALITY_PLAN §8 says (no hired agent). The thread is titled with the player's name; his personality voice is used when his profile is eligible for `inbox`, else neutral. |
| Q4 | Do JR's on-record answers to his beat writer feed articles and writer trust in this plan? | No. Store them and expose `jr_statements`; the newsroom wiring and any trust change go in V2.2, which produces the same kind of quotes from pressers. |
| Q5 | Free text that triggers actions (the LLM reads JR's message and makes the offer)? | Not now. Actions only via buttons/forms; typed notes colour the reply. Revisit once the voice passes the quality gate, with a "confirm this action" step. |
| Q6 | Which writers can message JR? | Only his team's local beat writer at first. National insiders (Schefter, Rapoport personas) come with V2.11's leaks. |
| Q7 | One inbox per user team (CAR and IND separate) or one combined inbox? | Per team, following the team picker like every other module. A combined view later is frontend-only. |
| Q8 | LLM replies on from day one? | Off by default (`MU_INBOX_LLM` unset → templates) until the latency spike and a `tools/llm_compare.py` side-by-side pass (V2.0 quality gate); then on. |
