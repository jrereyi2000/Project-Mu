# Project Mu: Live Multi-Turn Trade Negotiations Plan (V2.11)

**Status:** approved by JR (2026-11-03); building, wave 2 of 2 done (2026-11-08). All decisions recorded (§8). V2.9 is built and on main, so the precondition is met.
- As built (wave 1): a follow-up **U7** was added for `newsroom/db_tools.py` (U6 found that `fetch_rumors` sent every rumor id to the integer proposal query, so a `leak:<id>` row would break it and be dropped; no unit owned the file). U7 splits leak rows from proposal rows and renders leaks with `leak_line`.
- As built (U1): the migration also widens `gm_trade_negotiation_turns.actor` to `VARCHAR(4)` — `GM_A` does not fit the old `VARCHAR(2)`, and the plan's DDL only re-created the check. `gm_negotiation`'s turn reads/writes now include the new columns, so the `2027_01_22` migration is required on any database where JR's talks run (not just the CPU path). `run_talks` returns `{talks, agreed, stalled, teams}` (no `used` argument): U2 folds `teams` into its week's used set. A resumed talk has no stored trigger, so its outcome proposal uses `NEED`.
- As built (U5): the inbox start body also carries `as: "GM"` (V2.9's `/inbox` refuses without it), and `assets` is the builder's send side in V2.9's `trade_assets` shape — the inbox form is one-directional, so the receive side is what the GM counters for.
- As built (U6): `trade_rumors` suppresses a leaked talk's proposal at any status (not only STALLED), `_talk_quotes` treats the presence of `gm_trade_leaks` as the migration marker, and `rumor_facts`' "never name a player" grounding is relaxed only when the leak names one.
- As built (U2): `run_week` calls `run_talks` after the initiator loop (§2.6), so the `used` fold happens too late to keep a team in a carried talk out of that week's search — the plan's §2.3 and §2.6 say both "after the initiator loop" and "before the search", and only `gm_talks` could split the resume out. `dry_run` relies on the caller rolling back (the engine CLI does); `picked` (deals found, stored or talked) drives the weekly budget; `--approval` stays in the signature and CLI but no longer decides a CPU deal.
- As built (U3): `PRIORITY_ENGINE_TRADE` is 1 and the rest shift down (only the relative order matters); `record_written` skips an already-EXECUTED proposal; the second-refusal expiry counts the WRITTEN runs' `engine_trades_failed_ids`. `_apply_db` would otherwise double-count the cap room of trades `trade_moves.execute_trade` already moves, so `Plan.trade_cap_db` carries that part through `plan_record`/`plan_from_record` and `_apply_db` subtracts it.
- As built (U4): `engine_ref` keeps V2.9's `status`/`action`/`proposal_id` and adds `posture` (the last GM turn's) rather than shrinking to three keys; the inbox's first GM reply on a JR talk draws its leak on JR's opening offer so an `OPENED` leak can fire (U1's staging only recognises turn 1); the queued move id is found by scanning `franchise_pending_moves` TRADE payloads (`_agree` doesn't return it); `/trade-talks` returns `{talks: []}` or 404 on an older schema instead of erroring.
**Builds on:** [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §6 V2.11, §2.6 (Trade Machine), §5.3.2 (interactive latency boundary), §7.2 (the 2026–2033 all-CPU era); [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) §8 (agentic loops) and §9 phase 5; PROJECT_SPEC §2.17 (trade value, proposal engine, negotiation), §2.21a (league office), §3.3f (GM agents tables), §4.6b (franchise endpoints); [`V2_CORE_BUILD_PLAN.md`](../done/V2_CORE_BUILD_PLAN.md); [`FRONT_OFFICE_INBOX_PLAN.md`](../done/FRONT_OFFICE_INBOX_PLAN.md) (V2.9: JR's GM talks live in its `GM_TALK` inbox kind, `engines/inbox/kinds/gm_talk.py`, which this plan extends).
**Precondition:** V2.9 is built and on main before this plan's wave 1 (its own Q1 recommends going first): its U1 owns `pipeline/weekly_capture.py`'s inbox hook and migration `2027_01_19`, its U2 `FranchisePage.tsx` and `types/franchiseInbox.ts`, its U4 `engines/inbox/kinds/gm_talk.py`, its U5 `server/api.py`. Nothing here edits `server/api.py` or `FranchisePage.tsx`; this plan's edits to `weekly_capture.py` and `gm_talk.py` start from V2.9's merged versions.

**Goal:** Today a CPU-to-CPU trade is one engine step: the search finds the smallest package both GMs accept and stores it as a PROPOSED row that waits for JR to approve and make by hand in Madden. JR never uses Madden's menus, and from 2026 to 2033 there is no user team at all, so those trades never happen. This plan turns each engine trade into a short conversation between the two GMs: an opening offer, counters, posturing, a deal or a breakdown. The numbers stay with the engines; the GMs' language model writes what they say and picks among moves the engine has already checked. Agreed CPU deals are written into the save by the league office with no step from JR. Some talks leak to the newsroom's insiders while they are still going on, and the full transcript of every CPU deal that goes through is published. JR's own talks run in the V2.9 inbox (one conversation surface for the whole front office): this plan gives its `GM_TALK` kind posturing lines, leaks and an agreed deal that is queued for the league office like any other move, and the Trade Machine gets a "Talk to their GM" button that opens that inbox thread.

**Delivers:**
- `engines/gm_talks.py` (new): the two-GM talk loop (`open_talk`, `advance_talk`, `run_talks`), the option builder (`options_for`), the persona policy (`policy_choice`), the language-model chooser (`llm_choose`), leak draws (`leak_events`), publication (`publish_talk`), a reply timeout wrapper (`call_with_timeout`) and an inbox adapter (`talk_thread`). CLI `python3 -m engines.gm_talks --show <id> | --replay-week [--season S --week W] [--dry-run] [--no-llm]`.
- `db/migrations/2027_01_22_trade_talks.sql` (new): `gm_trade_negotiations.kind` (`USER` | `CPU`), `initiator_team_id`, `source_proposal_id`, `carried_weeks`, `published_at`; turn columns `actor_team_id`, `options`, `choice`, `choice_source`, `posture`; new status `STALLED` on proposals and talks; new table `gm_trade_leaks`.
- `gm_trade_engine.run_week`: CPU-to-CPU deals go through talks; agreed deals are stored APPROVED with flag `talks:<id>` and `auto_approved`; broken-down talks are stored `STALLED`.
- League office: new step `step_engine_trades` writes APPROVED CPU deals; `step_pending_trade` accepts picks (moved in the database only); `record_written` executes every written trade in the database (`front_office._execute`) and publishes the CPU transcripts.
- `engines/inbox/kinds/gm_talk.py` (V2.9's file, extended): posture shown on the GM's turns, the reply-time limit, the leak draw on JR's talks, "Agreed. Queued for the league office" with the pending move id.
- `GET /api/franchise/trade-talks` and `/trade-talks/{id}` (published CPU transcripts, league-wide).
- Trade Machine: **Talk to their GM** (opens a `GM_TALK` inbox thread with the builder's package, then `#franchise-inbox`) and a **League talks** list of published transcripts.
- Newsroom: leaks become rumor rows (`front_office.trade_rumors`), rendered by `roster_moves.leak_line`, first to the insider who got the leak (`social_desk`); an executed trade with a published transcript carries up to three quoted GM lines (`roster_moves.trade_line`).

**JR's answers (2026-11-03):**
- **D1:** Project Mu's `draft_picks` is the source of truth for traded picks. The save's DraftPick rows are not written.
- **D2:** Agreed CPU-to-CPU deals need no action from JR.
- **D3:** In JR's talks the model writes the GM's words only. The engine and the persona policy choose his moves.
- **D4:** GMs may leak JR's talks: positions only, the same odds as CPU talks, unless JR insulted the GM.
- **D5:** A talk may carry over once, only when the sides are close, never past the deadline.
- **D6:** Transcripts are published only for completed deals.
- **D7:** Build order: the inbox plan (V2.9) first.
- **D8 (S7):** The franchise registry's `user_teams` is the source of truth for which teams JR controls. In Madden every team stays a user team (V2_CORE_BUILD_PLAN spike R2: the only way to stop Madden's own CPU-to-CPU trades), so Madden's flags say nothing about JR. §2.10 makes the GM engines read the registry.
- **Terminology:** "agent" means an agentic interface for a persona (GM, coach, player), never a sports agent.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `engines/gm_trade_engine.py:1527` `run_week` searches, then `choose` (`:1204`, the smallest package both accept by `accepts`, `:1188`) and `store` (`:1478`) with `status="PROPOSED"`. Nothing approves an engine proposal except JR on `#debug` (`engines/front_office.py:492` `approve`). | With no user team (2026–2033, master plan §7.2) no CPU trade is ever made. The talk loop replaces the one-shot store for CPU-to-CPU deals and approves its own agreements. |
| S2 | `pipeline/league_office.py` has no reference to `gm_trade_proposals`. Trades reach the writer only from JR's pending moves (`step_pending_trade`, `:1134`) and drift (`:1210`). | A new league office step must read APPROVED engine deals and write them. |
| S3 | `step_pending_trade` refuses any trade with a pick (`:1143-1145`, "traded draft picks aren't mapped to the save's DraftPick rows yet"). The save writer can move picks (`pipeline/save_reader/trade.mjs:16-19`, by DraftPick table id and row), but the league office has no mapping. Mu's own draft reads pick owners from `draft_picks` (`pipeline/league_office.py:1015`). | Pick legs can be moved in the database only (by `pipeline/trade_moves.execute_trade`) and still be honoured by Mu's draft. Writing the save's DraftPick rows is a follow-up (Q1). |
| S4 | `record_written` (`pipeline/league_office.py:1784`) marks pending moves WRITTEN but never runs `trade_moves.execute_trade`; only `#debug`'s **Made it** does (`engines/front_office.py:527` `_execute`, `:540` `confirm`). | A trade written by the league office leaves `gm_trade_proposals`, contracts, cap rows and `draft_picks` stale. `record_written` must execute each written trade. |
| S5 | `engines/gm_negotiation.py` already runs JR-vs-GM talks: `respond` (`:166`), `counter` (`:111`, returns only the single best counter), patience and mood, `open_negotiation` (`:425`), `offer` (`:460`), `accept_counter` (`:503`), `_agree` (`:539`, stores an APPROVED USER proposal with Madden steps). Tables `gm_trade_negotiations` / `_turns` (`db/init_db.sql:878`, `:897`; turn `actor` is `JR` or `GM`, `db/migrations/2026_12_21_gm_negotiations.sql:33`). | The judging, counter and patience maths exist and are pure. CPU talks reuse them with the initiating GM in JR's seat; the tables are extended rather than duplicated. `counter` must return several options for a chooser. |
| S6 | `counter`, `judge`, `gm_floor`, `user_pool` take any two team ids and read `b["ctx"][team]` and rosters from the books (`engines/gm_negotiation.py:43-108`); nothing in them is specific to JR. | No new valuation code. The initiator's own acceptance of a counter is `judge` with the roles swapped. |
| S7 | `open_negotiation` requires `gm_team_settings.user_controlled` for JR's team (`engines/gm_negotiation.py:437-438`); the Command Center's authority is the franchise registry's `user_teams` (`server/franchise_api.py:223` `require_user_team`). | The two can disagree, and nothing keeps them in step (spec/code mismatch). Per D8 the registry wins: §2.10 makes the GM books read `user_teams`, so `open_negotiation`'s check, the engine's advisory flags and `eligible_team` all follow the registry. |
| S8 | `POST /{abbr}/trades/evaluate` (`server/franchise_api.py:1395`) runs `gm_negotiation.respond` as a fresh negotiation and never stores anything; `MOVE_KINDS` (`:133`) has no `TRADE`, so the Command Center cannot queue a trade at all. `franchise_pending_moves.kind` already allows `TRADE` (V2_CORE_BUILD_PLAN.md:160). | The agreed trade needs a way into the queue. The table needs no change. |
| S15 | V2.9 (`FRONT_OFFICE_INBOX_PLAN.md` §2.3, §2.9, R6) makes the inbox the place JR talks to GMs: kind `GM_TALK` (`engines/inbox/kinds/gm_talk.py`) wraps `gm_negotiation.open_negotiation` / `offer` / `accept_counter` / `close` and copies the GM's turn message; its voice layer has an 8 s budget (`INBOX_LLM_BUDGET_S`); it leaves posturing, leaks, CPU transcripts and "does the league office write an agreed trade" to V2.11. Its migration is `2027_01_19`. | No parallel conversation surface or talk endpoints here: JR's talks are the inbox thread, extended in `gm_talk.py`. This plan's migration takes `2027_01_22`. |
| S9 | `engines/gm_llm.py:89` `call` has no timeout; the GM reply is one Gemini call validated by `gm_rationale.validate_rationale` with a template fallback (`engines/gm_negotiation.py:306` `gm_message`). | JR's turn needs a timeout around the call (§5.3.2: no sub-second promise). The validator is reused for every line in a transcript. |
| S10 | Rumors already flow: `front_office.trade_rumors` (`engines/front_office.py:142`, rumor_ok proposals never executed, positions only) → `newsroom/roster_moves.py:483` `rumor_line` → writer tool `get_trade_rumors` and the social desk's insiders (`newsroom/social_desk.py:478` `load_trades`). | Leaks plug into `trade_rumors`; every downstream reader picks them up without new tools. |
| S11 | GM personas carry sliders (`temperament`, `sociability`, `confidence`, `openness`, `warmth`, `drive`) and traits in the books (`b["gm"][t]["sliders"]`, `["traits"]`, PROJECT_SPEC §3.3f Persona Integration); `GUARDED` and `CAP_MIND` are GM-eligible traits (`newsroom/config/persona_traits.json`). Writers with the `INSIDER` trait exist (`newsroom/config/agent_personas.json:53`, `:231`, `:321`). | Posture and leak odds come from stored sliders and traits; leak recipients come from INSIDER writers. No new persona data. |
| S12 | Pacing counts `EXECUTED` and `REJECTED` as decided (`engines/gm_trade_engine.py:386-387`), and a player in a proposal JR REJECTED is barred for the week (PROJECT_SPEC §2.17 "Rejected players"). Status CHECK is unnamed in `2026_12_10_gm_agents.sql:116-117` (VARCHAR(9)). | A broken-down talk must not be `REJECTED` (that would bar its players as if JR said no). New status `STALLED` (7 chars) counts as decided. The migration drops the default-named `gm_trade_proposals_status_check`. |
| S13 | The watcher runs engines (`step_gm` inside `run_weekly_engines`) before the league office in the same ingest (`pipeline/watch_save.py:684-688`). | A CPU deal agreed this week is written by the same ingest's league office run, so a same-week leak would already be stale. Leaks matter only for talks that carry over a week (§2.3) or break down. |
| S14 | `game_launch` refuses while the team has PENDING moves (`pipeline/game_launch.py:6`). | JR's agreed trade is a pending move like a release; it gets written by the next league office run, as today. |

## 1. Scope

**In:**
- CPU-to-CPU talks for in-season and offseason engine trades: opening offer, counters, acceptance, walk-away, breakdown, carry-over of one week (or one offseason stage).
- Posture and choice: the engine builds the legal options for each turn; a seeded persona policy picks one; when a model is available it may pick instead, from the same list only.
- Dialogue for every turn in the GM's voice, validated against the fact sheet, with the template fallback.
- Automatic approval and execution of agreed CPU deals through the league office (players in the save, picks in the database), and database execution of every written trade, JR's included.
- Leaks: seeded per turn from the GMs' sliders and traits, stored in `gm_trade_leaks`, delivered to one INSIDER writer first, then to all rumor readers.
- Published transcripts for executed CPU deals (site and newsroom).
- JR's live talks through V2.9's `GM_TALK` inbox kind: posture on the GM's turns, the reply-time limit, leaks, his agreements queued as `TRADE` pending moves; a Trade Machine button that opens the thread.
- A `talk_thread` adapter (read-only transcript in the inbox's message shape) for any later inbox view of CPU talks.

**Out (non-goals):**
- Draft-day and Round 2–7 trades (`run_draft_day`, `run_draft_round`): they run live pick by pick and stay one-shot.
- Writing traded picks into the save's DraftPick rows (Q1; a separate follow-up once a pick mapping exists).
- Three-team trades, player trade requests (`PERSONALITY_TRADE_REQUESTS_PLAN.md`), owners.
- The inbox itself (tables, scan, API, `InboxModule.tsx`, `FranchisePage.tsx`, `server/api.py`): V2.9's plan. CPU talks are newsroom output, not inbox threads (V2.9 §2.9).
- New talk endpoints on `/api/franchise/{abbr}/trades/*`: the inbox's `/inbox` endpoints carry JR's talks. `trades/evaluate` stays the preview.
- A `TRADE_LEAK` REPORTER inbox intent (a beat writer asking JR about a leak): possible later, V2.9 §2.9 allows it.
- Any change to trade values, thresholds, legality, shapes or the weekly budget formula. The budget adapts through `p_exec` on its own.
- Articles written from transcripts beyond the quoted lines in the trade line (V3 media).

## 2. Design

### 2.1 Ground rules carried into the code

- Every option a chooser sees has already passed `ge.check_legality` and its acting GM's floor (`gm_negotiation.gm_floor`). A model's answer that names anything else is ignored and the policy's choice is used (`choice_source = 'POLICY'`, flag on the turn `llm_choice_rejected`).
- Surpluses are stored on turns (as today) and never shown on the site, in transcripts or to the newsroom.
- A line of dialogue is kept only when `gm_rationale.validate_rationale(text, sheet, league)` passes; otherwise `gm_negotiation.template_message`.
- Leaks state only what happened (talks opened, close, stalled), teams, positions, and a player name only when the leaker names him (§2.4). No figures, no "done deal".
- Record, don't replay: the chosen option, its source and the text are stored; re-running a week never re-asks the model for a decided turn.

### 2.2 Schema (`db/migrations/2027_01_22_trade_talks.sql`, mirrored in `db/init_db.sql`)

```sql
ALTER TABLE gm_trade_negotiations
  ADD COLUMN IF NOT EXISTS kind VARCHAR(4) NOT NULL DEFAULT 'USER' CHECK (kind IN ('USER', 'CPU')),
  ADD COLUMN IF NOT EXISTS initiator_team_id INT REFERENCES teams(id),          -- CPU: the GM who called; USER: NULL
  ADD COLUMN IF NOT EXISTS source_proposal_id INT REFERENCES gm_trade_proposals(id) ON DELETE SET NULL,
  ADD COLUMN IF NOT EXISTS carried_weeks SMALLINT NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS published_at TIMESTAMPTZ;
-- status gains STALLED; for kind CPU, user_team_id holds the initiator's team (same as initiator_team_id)
ALTER TABLE gm_trade_negotiations DROP CONSTRAINT IF EXISTS gm_trade_negotiations_status_check;
ALTER TABLE gm_trade_negotiations ADD CONSTRAINT gm_trade_negotiations_status_check
  CHECK (status IN ('OPEN', 'AGREED', 'WALKED_AWAY', 'CLOSED', 'EXPIRED', 'STALLED'));

ALTER TABLE gm_trade_negotiation_turns
  ADD COLUMN IF NOT EXISTS actor_team_id INT REFERENCES teams(id),
  ADD COLUMN IF NOT EXISTS options JSONB,            -- [{key, action, assets, su, sg}] the chooser saw (never public)
  ADD COLUMN IF NOT EXISTS choice TEXT,              -- the chosen option key
  ADD COLUMN IF NOT EXISTS choice_source VARCHAR(6) CHECK (choice_source IN ('LLM', 'POLICY', 'JR')),
  ADD COLUMN IF NOT EXISTS posture VARCHAR(8) CHECK (posture IN ('FIRM', 'ANCHOR', 'SQUEEZE', 'MEET', 'HOLD'));
ALTER TABLE gm_trade_negotiation_turns DROP CONSTRAINT IF EXISTS gm_trade_negotiation_turns_actor_check;
ALTER TABLE gm_trade_negotiation_turns ADD CONSTRAINT gm_trade_negotiation_turns_actor_check
  CHECK (actor IN ('JR', 'GM', 'GM_A'));             -- GM_A: the initiating GM in a CPU talk; GM: the other GM

ALTER TABLE gm_trade_proposals DROP CONSTRAINT IF EXISTS gm_trade_proposals_status_check;
ALTER TABLE gm_trade_proposals ADD CONSTRAINT gm_trade_proposals_status_check
  CHECK (status IN ('PROPOSED', 'APPROVED', 'EXECUTED', 'REJECTED', 'EXPIRED', 'STALLED'));

CREATE TABLE IF NOT EXISTS gm_trade_leaks (
  id SERIAL PRIMARY KEY,
  negotiation_id INT NOT NULL REFERENCES gm_trade_negotiations(id) ON DELETE CASCADE,
  turn_no INT NOT NULL,
  season INT NOT NULL,
  week INT NOT NULL,                                 -- engine week of the leak
  leaker_team_id INT NOT NULL REFERENCES teams(id),
  stage VARCHAR(7) NOT NULL CHECK (stage IN ('OPENED', 'CLOSE', 'STALLED')),
  named_player_ids INT[] NOT NULL DEFAULT '{}',      -- empty: positions only
  detail JSONB NOT NULL,                             -- {teams:[abbr,abbr], initiator, positions:[{pos, from, to}], picks:int}
  insider_key TEXT,                                  -- agent_personas.json key of the writer who got it first
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (negotiation_id, turn_no)
);
CREATE INDEX IF NOT EXISTS gm_trade_leaks_week ON gm_trade_leaks (season, week);
```

The build checks the real constraint names on `madden_test` (`\d gm_trade_proposals`) before writing the DROPs; Postgres names an unnamed column CHECK `<table>_<column>_check`.

### 2.3 The CPU talk loop (`engines/gm_talks.py`)

Constants (estimates, tuned on a replay, §3 R2): `TURNS_PER_RUN = 4`, `TALK_MAX_TURNS = 8`, `MAX_CARRY = 1`, `CARRY_P_CLOSE = 0.6`, `ANCHOR_BASE = 0.2`, `SQUEEZE_BASE = 0.3`, `OPTION_K = 3`.

**Start.** `run_week` (§2.6) hands `run_talks(cur, b, valuer, deals, rng, llm, log)` the deals it would have stored, each already accepted by both GMs (`accepts`). `deals` holds only pairs where neither team is user-controlled (advisory proposals keep the old path). For each deal `D*`, `open_talk` inserts a `CPU` negotiation (`user_team_id = initiator_team_id = D*.a`, `gm_team_id = D*.b`, patience from `patience_for` for each side: the initiator's in `patience`, the other's in `patience_left` at the start, both tracked in the turns), reserves from `reserve_for(season, nid, aggressiveness)` per side.

**Options** (`options_for(b, valuer, talk, actor, offer) -> list[Option]`, pure). `Option = {key: str, action: 'OFFER'|'COUNTER'|'ACCEPT'|'REJECT'|'WALK_AWAY', posture, assets, su, sg}`.
- Opening (initiator): `FIRM` = `D*`; `ANCHOR` = up to `OPTION_K − 1` one-edit variants of `D*` that raise the initiator's surplus (drop one of his non-core sends, or add one of the other side's free assets from `user_pool` with roles swapped), each legal. The anchor may sit below the other GM's floor: that is the point of posturing.
- Responding to an offer (either GM): if `judge` clears the actor's floor: `ACCEPT`, plus `SQUEEZE` = the best `counter` above floor + markup when one exists and the actor has patience above 1. Otherwise: up to `OPTION_K` counters from `gm_negotiation.counter_options` (`MEET` posture: the softest; `HOLD`: the dearest), plus `REJECT`. Patience at zero forces `WALK_AWAY` as the only option.
- Every option's assets pass `check_legality` and keep the core (the asset with the highest league value the other side sends, as in `counter`).

**Policy** (`policy_choice(options, persona, rng) -> key`, pure, seeded `random.Random(f"talk:{season}:{nid}:{turn_no}")`):
- Opening: `p_anchor = clamp(ANCHOR_BASE + 0.4·aggressiveness + 0.04·(confidence − 5) − 0.03·(warmth − 5), 0.05, 0.85)`; ANCHOR picks the variant with the smallest surplus gain (the mildest ask).
- Responding with ACCEPT available: `p_squeeze = SQUEEZE_BASE·aggressiveness·(1 + 0.05·(drive − 5))`, only when SQUEEZE exists; else ACCEPT.
- Countering: `MEET` with probability `0.5 + 0.05·(warmth − 5) − 0.05·(temperament − 5)` (clamped 0.1–0.9), else `HOLD`; `CAP_MIND` adds 0.1 to `HOLD`. A GM with `GRUDGE_HOLDER` who was INSULTED earlier in the talk takes `REJECT` over `MEET` with probability 0.5.

**Model choice** (`llm_choose(llm, sheet, persona, options, history) -> (key, text, source)`): one call per turn, prompt = the GM's voice and traits, the fact sheet (`gm_negotiation.message_sheet`), the transcript so far, and the options with labels (`gm_negotiation.diff` text, never surpluses). Answer: JSON `{"choice": "<key>", "message": "<2–4 sentences>"}`. A key not in `options` → policy key; a message failing the validator → template for the chosen action. With `llm=None` (tests, `--no-llm`) the policy decides and the template speaks.

**Advance** (`advance_talk(cur, b, valuer, talk, rng, llm) -> status`): plays turns alternately until AGREED, WALKED_AWAY, a REJECT with no patience left, `TURNS_PER_RUN` turns this run, or `TALK_MAX_TURNS` in all. At the turn limit of a run:
- mood of the last decline is CLOSE, `carried_weeks < MAX_CARRY`, the window allows a later run (in season: `week < TRADE_DEADLINE_WEEK`; offseason: not the last stage) and the seeded draw `< CARRY_P_CLOSE` → stays OPEN, `carried_weeks += 1`;
- else → STALLED.

**Carry-over.** At the start of the next `run_week`, `run_talks` resumes every OPEN `CPU` talk first: fresh books, the last offer re-judged; an asset no longer legal (injury, trade elsewhere) → STALLED "the deal stopped working". Teams in a resumed talk count against `used` for the week. `gm_negotiation.expire_stale` (`:396`) skips `kind = 'CPU'` (the loop owns their life cycle).

**Outcomes.** AGREED → `ge.store(..., status="APPROVED", flags=[f"talks:{nid}", "auto_approved", ...release/llm flags], trigger=D*.trigger)` with `rationale_for` written from the agreed deal (unchanged), `approved_at` set, and `gm_trade_negotiations.proposal_id` set. STALLED → `ge.store(..., status="STALLED", flags=[f"talks:{nid}"])` on the last offer on the table, `rumor_ok` seeded as today, `counts_for_pacing` true. `source_proposal_id` points at the stored row either way.

**Leaks** (`leak_events(talk, turn, books, rng) -> list[Leak]`, pure, after each turn except an AGREED one): per GM `p = LEAK_BASE 0.12 × (0.5 + sociability/10) × (0.5 if GUARDED) × (1.5 if QUOTABLE) × STAGE_W[stage]`, `STAGE_W = {OPENED: 0.6, CLOSE: 1.0, STALLED: 1.3}`; stage OPENED after turn 1, CLOSE after a CLOSE-mood decline, STALLED when the talk stalls. At most one leak per talk per week. The leaker names the players he receives when his aggressiveness ≥ 0.65 (pressure through the press), otherwise positions only. `insider_key` = a seeded pick among writers whose persona has `INSIDER` (from `newsroom.personas` loader, read-only), weighted ×2 for an insider with a stored `persona_relationships` row with that GM, when the table has one.

**Publication** (`publish_talk(cur, nid)`): sets `published_at`; called by the league office once the deal is executed (§2.5). Published transcript shape (`talk_json(cur, nid, public=True)`):

```json
{"id": 41, "kind": "CPU", "season": 2036, "weeks": [6, 7], "status": "AGREED", "proposal_id": 512,
 "teams": [{"abbr": "NYJ", "gm": "Silas Whitlock", "role": "initiator"}, {"abbr": "NE", "gm": "Dana Okonkwo"}],
 "turns": [{"turn_no": 1, "week": 6, "team": "NYJ", "action": "OFFER", "posture": "ANCHOR",
            "assets": [{"from": "NYJ", "to": "NE", "label": "2037 5th round"}, {"from": "NE", "to": "NYJ", "label": "CB T. Reyes (78)"}],
            "message": "..."}],
 "final": {"assets": [...]}}
```

No `su`, `sg`, `options`, `choice_source` or `legality` in the public shape.

**Inbox adapter** (`talk_thread(cur, nid) -> list[{author: 'COUNTERPART'|'JR', body, source, engine_ref: {negotiation_id, turn_no}, created_at}]`): read-only, in V2.9's `Message` shape (FRONT_OFFICE_INBOX_PLAN §2.7) without ids; for a later inbox or site view of a CPU talk. `gm_talk.py` doesn't need it (it reads the inbox's own messages).

**Timeout** (`call_with_timeout(llm, prompt, seconds) -> str | None`): runs `gm_llm.call` in a worker thread and returns None after `seconds`; the caller uses the template. JR's turns use V2.9's `INBOX_LLM_BUDGET_S` (8 s, an estimate; measure on the PC, §3 R4). Batch CPU talks use no timeout.

### 2.4 JR's talks (`engines/gm_negotiation.py` changes)

- `counter_options(b, valuer, deal, user, gm, reserve, counters_made, k=OPTION_K) -> list[counter dict]`; `counter` becomes `counter_options(...)[0]` or None (behaviour unchanged; existing tests stay green).
- `open_negotiation`'s control check is unchanged in code; it now reads the registry through the books (§2.10), so no bypass flag is needed.
- `_agree` also calls `queue_trade_move(cur, proposal_id)` (in `gm_talks`), which inserts a `franchise_pending_moves` row `kind = 'TRADE'`, `team_id` = JR's team, payload `{"proposal_id", "other_team_id", "send": {"players": [ids], "picks": [{season, round, original_team_id}]}, "receive": {...}}`, when that table exists. The `#debug` card's Madden steps stay for old saves; the league office path replaces them where the table exists.
- JR's GM reply: `gm_message` and `open_negotiation` / `offer` / `accept_counter` get `timeout=None` and `posture=None` passthroughs; `gm_talk.py` passes the inbox budget. The responding GM's counter is chosen by `policy_choice` among `counter_options` (MEET or HOLD), and the chosen `posture` is stored on the turn. The model writes the words only (Q3): one model call per JR turn.
- Leaks on JR's talks: `leak_events` runs on JR's talks too (Q4), with JR's side never named unless JR's offer was INSULTING.
- `expire_stale` skips `kind = 'CPU'`.

### 2.5 League office (`pipeline/league_office.py`)

- `step_engine_trades(plan, proposals)`: for each APPROVED proposal with flag `auto_approved` (read by `run` as `deps["engine_trades"]`: `season` = the plan's season, `status = 'APPROVED'`, `'auto_approved' = ANY(flags)`), player legs → `plan.trade_player(g, to_index, PRIORITY_ENGINE_TRADE, f"trade #{pid}")`, tag `("trade", pid)`; picks are not written. `PRIORITY_ENGINE_TRADE` sits below JR's and above the stage moves. Runs right after `step_pending`. A player already claimed → the whole trade is refused for this write (`plan.note(tag, False, ...)`), the proposal stays APPROVED and is retried next write; refused twice → `front_office.could_not_make` (EXPIRED, "the league office couldn't make it").
- `step_pending_trade`: picks no longer refuse the move; they are noted as "picks moved in Mu's books only" and handled by `record_written`.
- `record_written`: for every trade tag written (JR's TRADE pending moves with `proposal_id`, and `("trade", pid)` engine tags) → `front_office._execute(cur, proposal, "PROPOSED")` then status EXECUTED, `executed_at`; for engine trades with a `talks:<nid>` flag → `gm_talks.publish_talk(cur, nid)`. A JR TRADE move without `proposal_id` (old payloads) keeps today's behaviour.
- Run report gains `engine_trades` counts.

### 2.6 Trade engine wiring (`engines/gm_trade_engine.py`, `pipeline/weekly_capture.py`)

- `run_week(..., talk_llm=None)`: after `choose`, a pick where neither team is user-controlled is collected instead of stored; after the initiator loop, `gm_talks.run_talks(cur, b, valuer, collected, rng, talk_llm, log)` runs (and resumes carried talks first, before the search). With `dry_run`, talks run in memory and nothing is stored. Draft-day and round entry points are untouched.
- Pacing: `decided` counts `('EXECUTED', 'REJECTED', 'STALLED')`; `rejected_players` keeps reading only REJECTED.
- `step_gm` builds `make_llm("gm_talks")` and passes it as `talk_llm`; `flush` after.
- `--simulate-season` gains `--talk-stall P` (the share of talks that stall; default 0.25, an estimate) so JR can see the season total still lands near `IN_SEASON_TARGET` 20.

### 2.7 JR's talks in the inbox (`engines/inbox/kinds/gm_talk.py`, extended) and published transcripts (`server/franchise_api.py`)

V2.9's `GM_TALK` kind stays the only way JR talks to a GM. V2.9 is being revised so JR can control any number of personas (GM, coach, player); a `GM_TALK` thread is then "a GM persona JR controls ↔ another GM". The extension below is written for that: it takes JR's side from the thread (the controlled GM persona's team, `user_team_id` on the negotiation), never from a single "JR's team", and works the same for any number of controlled GMs. A turn's actor `JR` means "the persona JR controls". This plan changes, inside `gm_talk.py` only (no change to the inbox's store, registry, scan, API or tables):
- `act` calls `gm_negotiation` with `timeout=INBOX_LLM_BUDGET_S`; the GM's COUNTERPART message carries `engine_ref = {negotiation_id, turn_no, posture}`.
- After every GM turn, `gm_talks.leak_events` runs on the talk (Q4); a leak is stored in `gm_trade_leaks` and is never mentioned in the thread.
- On AGREED, the reply template becomes "Agreed. Queued for the league office (move #<id>)", using the pending move `_agree` queued (§2.4); the thread closes as V2.9 already does.
Published transcripts (read-only, `server/franchise_api.py`; no inbox file involved):

| Endpoint | Returns |
|---|---|
| `GET /api/franchise/trade-talks?season&week` | `{talks: PublishedTalk[]}`: CPU talks with `published_at` set, newest first, the public shape of §2.3 |
| `GET /api/franchise/trade-talks/{id}` | `PublishedTalk`, 404 unless published |

Both are open to every team (league news, not a user-team action). `trades/evaluate` is unchanged.

### 2.8 Trade Machine (`Mu_Network/src/components/franchise/TradeMachineModule.tsx`, `Mu_Network/src/types/franchiseMarket.ts`)

- **Talk to their GM** next to **Evaluate**: `POST /api/franchise/{abbr}/inbox` with `{kind: "GM_TALK", gm_team, assets, note}` (V2.9 §2.7 body; types imported from `types/franchiseInbox.ts`), then `window.location.hash = "#franchise-inbox"`. A 409 (talks already open) shows the inbox link instead.
- **League talks** tab: the published CPU transcripts for the season, newest first, each expandable to its turns (team, posture chip, message) and the final deal.
- Type `PublishedTradeTalk` (and its turn) in `franchiseMarket.ts`, mirroring §2.3's public shape.

### 2.9 Newsroom (`engines/front_office.py`, `newsroom/roster_moves.py`, `newsroom/social_desk.py`)

- `front_office.trade_rumors` adds one row per `gm_trade_leaks` row made by `week` (when the table exists): `{rumor_id: f"leak:{id}", week, teams, initiator, trigger, assets: positions, named: [{player_id, name, position}], stage, insider_key, source: 'LEAK', status: 'RUMOR'}`. A leaked talk's STALLED proposal does not also appear as a plain rumor.
- `front_office.executed_trades` adds `talk_id` and `talk_quotes` (up to 3 GM lines from the published transcript, LLM-sourced lines first) to a trade whose proposal has `talks:<nid>` and is published.
- `roster_moves.leak_line(leak, team_names)`: "Week 6 · RUMOR, not a done deal · per a source, the Jets and the Patriots have talked about CB Tre Reyes; talks are close." Stage words: OPENED "have opened talks", CLOSE "talks are close", STALLED "talks stalled, no deal". `RUMOR_NOTE` gains one sentence: a named player in a leak may be named, still never as traded. `trade_line` appends the quotes as "GM <name>: '<line>'".
- `social_desk.load_trades`: a leak with `insider_key` goes to that insider's pool first (the existing two-rumor cap applies); other insiders see it from the following week.

### 2.10 Team control: the registry is the source of truth (D8)

Choice: **the GM engines read the registry**, rather than keeping `gm_team_settings.user_controlled` as a synced copy. A copy needs a writer that runs at every registry change (registry edits happen outside the database, by `db.franchises` CLI or by hand) and would drift silently; reading at book time cannot drift. Every reader already goes through `read_books`, so one overlay covers `open_negotiation`'s check (`engines/gm_negotiation.py:437-438`), `eligible_team`, the `advisory:` flags and the CPU-talk filter.

- `front_office.controlled_team_ids(cur) -> set[int] | None` (U6): `SELECT current_database()`; the registry entry (`db.franchises.load_registry()`) whose `db` equals it; its `user_teams` abbreviations → team ids. No entry (tests, `madden_test`, `madden_2036`) or an unreadable registry → None. An entry with an empty list → `set()`: no team is JR's (the 2026–2033 all-CPU era). This differs on purpose from `franchise_api.require_user_team`, where an empty list allows every team (a convenience for the Command Center); that rule is unchanged.
- `gm_trade_engine.read_books` (U2): after loading `gm_team_settings`, `ids = front_office.controlled_team_ids(cur)` (lazy import); when `ids is not None`, `b["gm"][t]["user_controlled"] = t in ids` for every team. `suggestions_on` still comes from `gm_team_settings`.
- `front_office.set_team_control` (U6): when `controlled_team_ids(cur)` is not None, a `user_controlled` change → `FrontOfficeError(409, "team control comes from the franchise registry (python3 -m db.franchises)")`; `suggestions_on` still saves. `front_office_state` reports `user_controlled` from the same overlay.
- `gm_team_settings.user_controlled` stays as a column (fallback only). Nothing writes it from the registry.
- Personas: when V2.9 lets JR control GM, coach or player personas, a controlled **GM persona** means its team is in `user_teams`; the registry stays the single list.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Talks converge too rarely (anchors insult, patience runs out) and trade volume falls below the target. | U2's `--simulate-season --talk-stall` and a `gm_talks --replay-week --dry-run --no-llm` on `madden_2036` weeks 4–8 (read-only). | Lower `ANCHOR_BASE`, raise `TURNS_PER_RUN`; `p_exec` already raises the weekly proposal count when fewer go through. |
| R2 | The model keeps picking one posture (e.g. always HOLD), so talks feel samey or stall more than the policy would. | Log `choice_source` counts per week; compare LLM vs POLICY stall rates on a replay with the key set. | Keep the model on dialogue only for CPU talks too (a one-line switch, `TALK_LLM_CHOOSES = False`). |
| R3 | A carried-over talk's deal stops working (injury, cap) and leaks contradict what happened. | U1 tests: resume re-judges and stalls; leaks never say "agreed". | Leaks only ever say "talks", so a stalled carried talk stays true. |
| R4 | JR's reply time: one Gemini call per turn may take several seconds on the PC. | Time an inbox `GM_TALK` reply (`POST /{abbr}/inbox/{id}/reply`) on the PC with and without a key. | `INBOX_LLM_BUDGET_S` falls back to the template; JR sees the verdict either way. |
| R5 | The save and `draft_picks` disagree on traded picks (Madden's own draft board, any Madden screen). | Mu makes all picks from `draft_picks` (S3); JR never uses Madden's draft. | Q1 follow-up: map `draft_picks` to the save's DraftPick rows (`draft_tables.mjs`) and send picks to `trade.mjs`. |
| R6 | `record_written` executing trades double-books with `#debug`'s **Made it**. | U3 test: a proposal executed by the league office refuses `confirm` (status EXECUTED, `front_office.confirm` already 409s). | — |
| R7 | Shared files with sibling plans: `engines/inbox/kinds/gm_talk.py` (V2.9 U4 creates it; this plan's U4 extends it), `pipeline/weekly_capture.py` (V2.9 U1's `step_inbox` hook; maybe V2.2's postgame step), `db/init_db.sql` and migration order (V2.9 `2027_01_19`, V2.2 press conferences `2027_01_20`, V2.10 scouting `2027_01_21`, this plan `2027_01_22`; V2.2 also edits `init_db.sql` in later waves, so U1's mirror edit must not run alongside them), `newsroom/swarm_dispatcher.py` (V2.2 edits it; U6 only reads `new_week_trades`, so a rename there breaks U6), `server/api.py` (V2.2 and V2.9 edit it; this plan doesn't), `server/franchise_api.py` (V2.10 may add endpoints), `newsroom/social_desk.py` and `newsroom/roster_moves.py` (V2.2 press conferences, `AGENT_WIRE_VOICE_PLAN.md`), `Mu_Network/src/types/franchiseMarket.ts` (V2.9 U2 reads it). | The orchestrator compares the four plans' Owns lists before wave 1. | V2.9 first (precondition); with V2.2 or V2.10, run waves one plan at a time where Owns lists overlap. Renumber the migration date if another must apply first. |
| R8 | V2.9's `gm_talk.py` changes shape while it is built (e.g. option names), so §2.7's extension no longer fits. | U4 reads the merged `gm_talk.py` and `tests/test_inbox_kinds_gm_talk.py` first. | Keep the extension to the four §2.7 bullets and adapt names; the engine side (`gm_negotiation`, `gm_talks`) doesn't depend on the inbox. |
| R9 | `gm_talk.py` and `#debug`'s Negotiate card both drive JR's talks; only the inbox path draws leaks. | U4 test. | Acceptable: `#debug` is a tool, not JR's normal surface. |
| R10 | V2.9's revision (JR controls many personas) changes how a `GM_TALK` thread names JR's side. | U4 reads V2.9's merged `gm_talk.py` first. | §2.7's extension takes JR's side from the negotiation row, so only field names should need adapting. |
| R11 | A registry edit while the server runs: `read_books` reads the registry on every call, so it is current; `franchise_api.user_teams()` is too. | — | — |

**Spike first:** none needed. R1's replay runs after wave 2 as U2's verification, before any live week.

## 4. Tests (synthetic, no live DB)

- `tests/test_gm_talks.py` → options are all legal and keep the core; an anchor can sit below the other floor; ACCEPT appears only above the actor's floor; patience 0 offers only WALK_AWAY; policy is seeded (same seed, same choice) and follows sliders (high aggressiveness anchors more over 1,000 draws); a model choice outside the list falls back to POLICY; an invalid message falls back to the template; two fixed personas converge to AGREED and the agreed deal passes `accepts`; carry-over only from CLOSE, never past the deadline week, at most once; a resumed talk with an injured core stalls; leaks: GUARDED halves the rate (1,000 draws), names players only for aggressive leakers, never on an AGREED turn, at most one per talk per week; `talk_json(public=True)` has no surpluses, options or legality; `call_with_timeout` returns None on a slow fake; `talk_thread` order and shape.
- `tests/test_gm_negotiation.py` → `counter_options[0]` equals the old `counter` on the existing fixtures; the policy's posture is stored on the GM's turn; `timeout` reaches the reply call; `_agree` queues one TRADE pending move with `proposal_id` when the table exists, none when it doesn't; `expire_stale` leaves CPU talks alone.
- `tests/test_gm_trade_engine.py` → `read_books` takes `user_controlled` from `controlled_team_ids` when it isn't None and from `gm_team_settings` when it is; CPU-to-CPU picks go to `run_talks` and advisory ones are stored as before; AGREED stores APPROVED with `talks:` and `auto_approved`; STALLED counts as decided and doesn't bar players; dry run stores nothing; draft-day paths unchanged; `simulate_season` with `talk_stall=0.25` stays within ±2 of the old mean.
- `tests/test_weekly_capture.py` → `step_gm` passes a `gm_talks` LLM and a failure still only warns.
- `tests/test_league_office.py` → `step_engine_trades` plans player legs and skips picks; a claimed player refuses the whole trade; the second refusal calls `could_not_make`; `step_pending_trade` accepts a pick trade (players written, pick noted); `record_written` executes JR's and engine trades once and publishes talks; an old payload without `proposal_id` is untouched.
- `tests/test_inbox_kinds_gm_talk.py` (V2.9's file, extended) → `act` passes the budget; two threads for two different controlled GM personas each act for their own team; posture lands in `engine_ref`; a leak draw runs per GM turn and never appears in a message; AGREED reply names the queued pending move; V2.9's existing cases still pass.
- `tests/test_franchise_api.py` → `/trade-talks` returns only published CPU talks, newest first, with no surplus, options or legality; `/trade-talks/{id}` 404s for an unpublished or USER talk.
- `tests/test_front_office.py` → `controlled_team_ids`: registry entry for this database with teams → their ids; entry with an empty list → empty set; no entry → None; `set_team_control` refuses `user_controlled` with 409 when the registry lists the database, still sets `suggestions_on`; leak rows join `trade_rumors` only when the table exists; a leaked talk's proposal isn't duplicated as a plain rumor; `executed_trades` carries `talk_quotes` only for published talks.
- `tests/test_roster_moves.py` → `leak_line` wording per stage, named vs positions only, never "traded" or a figure; `trade_line` with quotes.
- `tests/test_social_desk.py` → a leak goes to its insider first and to others the week after.
- Frontend: `npm run build` in `Mu_Network/` (type check of the Talk to their GM button and League talks list against mocked shapes).

Run with `pytest -m "not integration" tests/test_gm_talks.py tests/test_gm_negotiation.py tests/test_gm_trade_engine.py tests/test_weekly_capture.py tests/test_league_office.py tests/test_inbox_kinds_gm_talk.py tests/test_franchise_api.py tests/test_front_office.py tests/test_roster_moves.py tests/test_social_desk.py`. The migration is applied to `madden_test` only.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Talk engine and schema

- **Owns:** `engines/gm_talks.py` (new), `engines/gm_negotiation.py`, `db/migrations/2027_01_22_trade_talks.sql` (new), `db/init_db.sql`, `tests/test_gm_talks.py` (new), `tests/test_gm_negotiation.py`
- **Reads:** §2.1–§2.4; `engines/gm_negotiation.py`, `engines/gm_trade_engine.py:1188-1225,1478-1527`, `engines/gm_rationale.py`, `engines/gm_llm.py`, `engines/gm_personas.py`, `newsroom/personas.py` (INSIDER lookup), `db/migrations/2026_12_21_gm_negotiations.sql`
- **Depends on:** none
- **Done when:** a scripted two-persona talk on fixture books reaches AGREED and STALLED in the expected cases with the policy alone and with a fake model; the migration applies to `madden_test` twice without error; existing negotiation tests pass.
- **Verify:** `pytest -m "not integration" tests/test_gm_talks.py tests/test_gm_negotiation.py` all pass; `python3 db/migrations/migrate_db.py` pattern or `psql madden_test -f db/migrations/2027_01_22_trade_talks.sql` twice, no error; `python3 -m engines.gm_talks --help` prints usage.

### U2: Trade engine wiring

- **Owns:** `engines/gm_trade_engine.py`, `pipeline/weekly_capture.py`, `tests/test_gm_trade_engine.py`, `tests/test_weekly_capture.py`
- **Reads:** §2.3, §2.6, §2.10; `engines/gm_talks.py` (U1); `front_office.controlled_team_ids` (U6); `pipeline/weekly_capture.py` as merged by V2.9 (its `step_inbox` hook stays after `step_gm`)
- **Depends on:** U1, U6 (and V2.9 on main)
- **Done when:** `read_books` takes team control from the registry; `run_week` routes CPU-to-CPU deals through talks and stores APPROVED / STALLED rows; the season simulation stays near target.
- **Verify:** `pytest -m "not integration" tests/test_gm_trade_engine.py tests/test_gm_trade_shapes.py tests/test_weekly_capture.py` pass; `python3 -m engines.gm_trade_engine --simulate-season --talk-stall 0.25` prints a mean within 18–22 a season.

### U3: League office execution

- **Owns:** `pipeline/league_office.py`, `tests/test_league_office.py`
- **Reads:** §2.5; `pipeline/trade_moves.py`, `engines/front_office.py:517-552`, `engines/gm_talks.py` (U1, `publish_talk`)
- **Depends on:** U1
- **Done when:** engine trades and JR's pick trades are planned and, once written, executed in the database and published.
- **Verify:** `pytest -m "not integration" tests/test_league_office.py tests/test_trade_moves.py` pass.

### U4: GM_TALK extension and published transcripts

- **Owns:** `engines/inbox/kinds/gm_talk.py` (V2.9's, extended), `tests/test_inbox_kinds_gm_talk.py`, `server/franchise_api.py`, `tests/test_franchise_api.py`
- **Reads:** §2.4, §2.7; `FRONT_OFFICE_INBOX_PLAN.md` §2.2–§2.4; `engines/inbox/registry.py`, `voice.py` (V2.9); `engines/gm_negotiation.py`, `engines/gm_talks.py` (U1)
- **Depends on:** U1 (and V2.9 on main)
- **Done when:** JR's inbox talks store posture, draw leaks and name the queued move on agreement; the two `/trade-talks` endpoints answer with §2.3's public shape; the franchise module docstring lists them.
- **Verify:** `pytest -m "not integration" tests/test_inbox_kinds_gm_talk.py tests/test_inbox_api.py tests/test_franchise_api.py` pass.

### U5: Trade Machine: Talk to their GM, League talks

- **Owns:** `Mu_Network/src/components/franchise/TradeMachineModule.tsx`, `Mu_Network/src/types/franchiseMarket.ts`
- **Reads:** §2.3, §2.7–§2.8; `types/franchiseInbox.ts` and `InboxModule.tsx` (V2.9, read only); `RosterCapModule.tsx` (`franchiseFetch`, state notes)
- **Depends on:** none in this plan (V2.9 on main; builds against §2.3 and §2.7 shapes)
- **Done when:** Talk to their GM posts the inbox body and moves to `#franchise-inbox`; the League talks list renders from a mocked fetch.
- **Verify:** `npm run build` in `Mu_Network/` succeeds with no type errors.

### U6: Leaks and transcripts in the newsroom

- **Owns:** `engines/front_office.py`, `newsroom/roster_moves.py`, `newsroom/social_desk.py`, `tests/test_front_office.py`, `tests/test_roster_moves.py`, `tests/test_social_desk.py`
- **Reads:** §2.2 (`gm_trade_leaks`), §2.9, §2.10 (`controlled_team_ids`, `set_team_control`); `newsroom/swarm_dispatcher.py` (`new_week_trades`); `db/franchises.py` (`load_registry`, entry `db`, `user_teams`)
- **Depends on:** none (reads the §2.2 table only when it exists)
- **Done when:** `controlled_team_ids` reads the registry; leak rows appear as labelled rumors and reach their insider first; executed trades carry quotes when published.
- **Verify:** `pytest -m "not integration" tests/test_front_office.py tests/test_roster_moves.py tests/test_social_desk.py tests/test_newsroom_trades.py` pass.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U5, U6 | — | No shared files; U5 and U6 build against §2.2 and §2.7 shapes |
| 2 | U2, U3, U4 | U1, U6 (U2 only) | Each imports `gm_talks` or the new `gm_negotiation` options; no shared files between them |

All waves start only after V2.9 is on main (precondition).

After wave 2: the R1 replay (`python3 -m engines.gm_talks --replay-week --season 2036 --week 6 --dry-run --no-llm` against a copy of `madden_2036`, read-only) and JR's look at three transcripts before the first live week.

**File ownership check:**

| File | Unit |
|---|---|
| `Mu_Network/src/components/franchise/TradeMachineModule.tsx` | U5 |
| `Mu_Network/src/types/franchiseMarket.ts` | U5 |
| `db/init_db.sql` | U1 |
| `db/migrations/2027_01_22_trade_talks.sql` | U1 |
| `engines/front_office.py` | U6 |
| `engines/gm_negotiation.py` | U1 |
| `engines/gm_talks.py` | U1 |
| `engines/gm_trade_engine.py` | U2 |
| `engines/inbox/kinds/gm_talk.py` | U4 |
| `newsroom/roster_moves.py` | U6 |
| `newsroom/social_desk.py` | U6 |
| `pipeline/league_office.py` | U3 |
| `pipeline/weekly_capture.py` | U2 |
| `server/franchise_api.py` | U4 |
| `tests/test_franchise_api.py` | U4 |
| `tests/test_front_office.py` | U6 |
| `tests/test_gm_negotiation.py` | U1 |
| `tests/test_gm_talks.py` | U1 |
| `tests/test_gm_trade_engine.py` | U2 |
| `tests/test_inbox_kinds_gm_talk.py` | U4 |
| `tests/test_league_office.py` | U3 |
| `tests/test_roster_moves.py` | U6 |
| `tests/test_social_desk.py` | U6 |
| `tests/test_weekly_capture.py` | U2 |

No path appears twice.

## 7. Spec changes when built

- §2.17 Proposal engine: CPU-to-CPU deals go through talks; APPROVED with `talks:` / `auto_approved`; `STALLED` status and pacing; `--simulate-season --talk-stall`.
- §2.17 Negotiation: `counter_options`, the TRADE pending move on agreement, reply timeout, leaks on JR's talks; a new **Talks** paragraph for `engines/gm_talks.py` (options, policy formulas, model choice, carry-over, leaks, publication, `talk_thread`, CLI); tests line.
- §2.21a League office: `step_engine_trades`, picks in `step_pending_trade`, trade execution and publication in `record_written`, report counts.
- §3.3f and §2.17 (team control): the franchise registry's `user_teams` is the source of truth for which teams JR controls; `gm_trade_engine.read_books` reads it through `front_office.controlled_team_ids` (registry entry matched by `current_database()`); `gm_team_settings.user_controlled` is only the fallback for a database with no registry entry (tests, `madden_2036`), and `#debug`'s JR-controls toggle refuses (409) on a registered franchise. Note why: in Madden all 32 teams stay user teams to stop Madden's own CPU trades (V2_CORE_BUILD_PLAN spike R2), so Madden's flags never say which teams JR runs. Fixes the S7 mismatch.
- §3.3f: new columns on `gm_trade_negotiations` and `gm_trade_negotiation_turns`, `STALLED` on proposals and talks, table `gm_trade_leaks`; migration list.
- §4.6b: `/trade-talks` and `/trade-talks/{id}`.
- Inbox section (added by V2.9): `GM_TALK` posture, leaks, the queued move on agreement.
- §5.2 (Trade Machine): Talk to their GM and the League talks list.
- §6.0b: the watcher's engine step now executes CPU trades through the same ingest's league office run.
- §7 newsroom (rumors / social desk): leaks, insider first, quoted GM lines.
- Repository Layout: `engines/gm_talks.py`, `db/migrations/2027_01_22_trade_talks.sql`, `tests/test_gm_talks.py`.
- V2_MASTER_PLAN §6 V2.11 and PERSONALITY_PLAN §9 phase 5: status to built (orchestrator, when moved to done).

## 8. Decisions for JR

All decided by JR on 2026-11-03 (D1–D8 at the top). Nothing is open.

| # | Question | Decision |
|---|---|---|
| Q1 | Traded picks: Mu's books only, or also the save's DraftPick rows? | Mu's `draft_picks` is the source of truth; the save's rows are not written (D1). |
| Q2 | Do agreed CPU-to-CPU deals need a step from JR? | No (D2). |
| Q3 | In JR's talks, does the model choose the GM's counter or only write his words? | Words only (D3). |
| Q4 | Can a GM leak JR's talks? | Yes: positions only, same odds as CPU talks, unless JR insulted him (D4). |
| Q5 | May a talk carry over? | Yes, once, only when close, never past the deadline (D5). |
| Q6 | Which transcripts are published? | Completed deals only (D6). |
| Q7 | Build order with V2.9? | V2.9 first (D7). |
| Q8 | Which list says which teams JR controls? | The franchise registry's `user_teams`; the GM books read it (§2.10, D8). |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | `worktree-agent-a6ad6293974380683` | `.claude/worktrees/agent-a6ad6293974380683` | merged (2 commits; two stall-path defects sent back and fixed) | `2a0b1a9` |
| U5 | 1 | `worktree-agent-a03f7a9166eaff35c` | `.claude/worktrees/agent-a03f7a9166eaff35c` | merged | `f975697` |
| U6 | 1 | `worktree-agent-a6fb118a78bb36451` | `.claude/worktrees/agent-a6fb118a78bb36451` | merged | `ca82520` |
| U7 | 1 (follow-up to U6) | `worktree-agent-aa04ea8b2ad7c7c5c` | `.claude/worktrees/agent-aa04ea8b2ad7c7c5c` | merged | `b406c63` |
| U2 | 2 | `worktree-agent-abcdf949e73d8a008` | `.claude/worktrees/agent-abcdf949e73d8a008` | merged | `5ccf194` |
| U3 | 2 | `worktree-agent-ac616cbdaf1157713` | `.claude/worktrees/agent-ac616cbdaf1157713` | merged | `5c4850b` |
| U4 | 2 | `worktree-agent-a54dd7cb974d63dfd` | `.claude/worktrees/agent-a54dd7cb974d63dfd` | merged | `94e1ebd` |

