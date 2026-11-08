# Project Mu — Open follow-ups

The single source of truth for work left open by plans that are marked `docs/done/` (or built and on main).
A plan moves to `done/` when **all its units are built and merged**; anything a unit deferred — cross-plan
wiring, a held unit, activation — is recorded here, so "done" never means "nothing left to do".

Convention:
- A plan's inline "As built" / "TODO (JR, …)" notes stay in the plan for context; this file is the canonical
  list you check before starting new work.
- When a follow-up lands, delete its row and update the source plan's note. Don't leave stale rows.
- "Next plans" that were never started live in `docs/not_started/` and are **not** listed here.

## Open

| Follow-up | Source | What's needed | Owner | Blocks | Status |
|---|---|---|---|---|---|
| Scouting-director wiring | OWNERS_SCOUTS_VOTERS U6 | Wire `scout_directors.board_bias` / `scout_voice.regional_bias` into `scouting_reports.py` (per-scout report rows: `scout_id` + `voice` set, grade = consensus + bias) and `scouting_boards.py` (draft-board lean). The directors and hooks exist but nothing consumes them. | SCOUTING_REVAMP (a new wiring unit) | Directors having any effect on reports or boards | Open |
| Voter article alignment | OWNERS_SCOUTS_VOTERS U7 | Pass the stored ballot results into article generation — `results_facts` for the Week 22 awards, `all_pro_facts` for the Week 19 All-Pro — and check each article against the stored winners/teams (`check_against_stored`) with a rewrite on a miss (`stored_winners_fix_prompt`). Today the writer isn't fed the results, so active `media_voters` can name a different winner than the stored ballot. | newsroom (`newsroom/swarm_dispatcher.py`) | `media_voters` active mode reporting the stored winners/teams | Open (JR TODO 2026-11-07) |
| Agent loops U3 wiring | COACH_PLAYER_AGENT_LOOPS_PLAN U3 | Wire loop decisions into the league office, inbox kinds and social desk. The inbox `CONTRACT`/`ROLE_TALK` kinds it needed are built (FRONT_OFFICE_INBOX U4/U3, main 2026-11-07); **trade talks U6 is the only remaining blocker**, plus the social-desk change that follows it. | agent-loops / inbox / trade-talks | Loop decisions reaching a live caller | Held (PERSONALITY_PLAN §5.3) |
| Agent Wire U3 | AGENT_WIRE_VOICE_PLAN U3 | Rebase/rework and merge U3 (it owns `newsroom/social_desk.py`, which trade talks U6 also rewrites); U1/U2 are merged, U3 is built unmerged. | AGENT_WIRE_VOICE / trade-talks | — | Held by JR to the end of the queue |
| Retirement restore authority (G4) | RETIREMENT_WINDOW_PLAN / `docs/reference/RETIREMENT_AUTHORITY.md` §5 | Produce the save-authority proof file + G4 proof; late native retirements stay observe-only until then. | JR | `retirement` save-write authority | JR's PC to-do |
| Free-agency contract-row creation | V2_CORE_BUILD_PLAN wave 1 | Settle creating `PlayerContract` rows (un-emptying a table row) so a Roster signing needs no release in the same write; spike R1. | V2 core | Free-agency signings without a matching release | Open |
| Inbox model-latency spike + `llm_compare` gate | FRONT_OFFICE_INBOX_PLAN | Once JR settles the internal model: (1) run `make_llm("inbox")` 20× on a filled CONTRACT prompt with Madden open, record p50/p95 and set `INBOX_LLM_BUDGET_S` in `engines/inbox/voice.py`; (2) run the `tools/llm_compare` gate on inbox replies and appraisals (D7). The plan stays in `docs/in_progress/` until both pass. | JR | Inbox plan closing; model-written counterpart voice | Deferred (JR) |
| Inbox page visual check | FRONT_OFFICE_INBOX_PLAN | The Command Center Inbox tab has never been opened in a browser: every wave was verified by `npm run build` plus API-level smoke on `madden_test` (take control, threads, replies, pickers). Click through take-control → list → thread → reply forms → new-conversation pickers with the API running, and check layout at narrow width. | JR / a session with a browser | Confidence in the UI before the 2026 start | Open |

## Activation gates (deliberate, not gaps)

These are the known "built but off" items — every personality feature is `off` by design until the activation
plan's shadow season and JR's clearance. Not code gaps; kept here so they aren't mistaken for missing work.

- **Owners/scouts/voters six features** (`owner_patience`, `owner_hiring`, `owner_spending`, `owner_meddling`,
  `scout_directors`, `media_voters`): apply `db/migrations/2027_02_02_owners_scouts_voters.sql` to a database,
  then run each through a tuning-franchise shadow season and JR's clearance (`personality/features.py`,
  PERSONALITY_ACTIVATION_PLAN). The migration is written and mirrored in `db/init_db.sql` but applied nowhere.
- The whole personality programme is gated the same way (contracts, development, evolution, relationships, trade
  requests, retirement); the activation plan owns the schedule.
