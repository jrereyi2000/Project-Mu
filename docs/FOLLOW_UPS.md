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
| Inbox page visual check | FRONT_OFFICE_INBOX_PLAN | The Command Center Inbox tab has never been opened in a browser: every wave was verified by `npm run build` plus API-level smoke on `madden_test` (take control, threads, replies, pickers). Click through take-control → list → thread → reply forms → new-conversation pickers with the API running, and check layout at narrow width. | JR / a session with a browser | Confidence in the UI before the 2026 start | Open |
| Presser page visual check | PRESS_CONFERENCE_PLAN U5 | The podium panel has only been verified by `npm run build` against the typed shapes; it has never run against the live API in a browser. Take a podium with the API and the migration applied, pick a reporter, answer, no-comment, end, edit, approve, and check the review screen and the read-only history links. | JR / a session with a browser | Confidence in the podium before the 2026 start | Open |
| Madden pilot live acceptance | MADDEN_PILOT_AND_TUNING_PLAN U1/U3 | The driver and jobs have only run unit tests and `--dry-run` (the ScriptedMadden fake); the first live `sim-season` is still to do, and MAIN_MENU, the savepoint dialog, post-season, pre-snap and settings screens aren't in `catalog.json` yet, so those paths stop with a screenshot until `/madden` catalogues them (PROJECT_SPEC §1.2). Also V2 §7.3 item 8. | JR (`/madden`) | The pilot driving Madden for real; the V2 CPU-era `sim-season` acceptance | Open |
| Supersim tuning loop never run for real | MADDEN_PILOT_AND_TUNING_PLAN U5 | `supersim_loop` has only its mocked iteration (in `tests/test_tuning_loop.py`); a real iteration (ebx-set recipe → mod build/install into `Mu Tune` → pilot `sim-season` → `pc_save_reader` → `compare` → `next_change`) needs `MmcHarness info` `ebx_readable: true`, a `space.json` recipe, and the live pilot above. `progression_fit` and `compare` first runs are already done (PROJECT_SPEC §1.2); `data/tuning/targets_2015_2025.json` is git-ignored and rebuilds in ~5 s. | `/tune supersim` + JR | Supersim tuning against real simmed seasons | Open |
| Trade flow: full-season proof and RENTAL share | UNILATERAL_TRADE_ENGINE_PLAN §3 R1, §9.1; P3-08 | P3-08 measured weeks 4–8 of the 2036 archive (25 talks, 9 executed, 64.0% breakdown, 8.1% template share), so the season figure (~11) is the 12/15 shape projection, not a season run. It also measured the proposal mix at **8 RENTAL / 17 SWAP** (4/5 executed), far from `SHAPE_MIX`'s 80/15/5. Still owed: a full-season replay (weeks 1–8 or a season driven through the league office) and a second archive season before changing `SHAPE_MIX`; P3-07's `compare_decisions` is the natural home. | P3-07 / a tuning session | The season volume read and the shape mix | Open — evidence in `docs/reference/TUNING_LEDGER.md` |
| Trade Finder misses the rival-overpay package | UNILATERAL_TRADE_ENGINE_PLAN (U1) | `find_packages` (#debug Trade Finder) calls `rental_packages(...)` without `include_overpay`, so for a taxed buyer–seller pair it hides the one-round-earlier ladder package the weekly search now generates; pass `include_overpay=True` (or show it as a separate note) so the diagnostic matches the engine. | A follow-up unit | A truthful debug view of taxed deals | Open |
| Trade Machine + League talks visual check | TRADE_NEGOTIATIONS_PLAN U5 | The **Talk to their GM** button and the **League Talks** list were verified by `npm run build` against the typed shapes only; neither has run in a browser against the live API. With `2027_01_22` applied: build a package, open a talk, hit the 409 (talk already open) path, and read a published transcript. Once P1-25 has re-run, also read a STALLED or WALKED_AWAY transcript (UNILATERAL_TRADE_ENGINE_PLAN §9.1) so both outcome shapes render cleanly. | JR / a session with a browser | Confidence in the module before the 2026 start | Open |
| Facility personality sensitivities + Q2 refusal | NFLPA_REPORT_CARD_PLAN U2 | Deferred: the §2.2 veteran family/travel upweighting, the high-ego flat −0.05, and the Q2 refusal (ego ≥ 85 refuses an F-facility team). They need the ego/loyalty traits from the personality contract path, which only reach `free_agency_engine.offer_utility` once the personality programme is active (activation gates below). The core S_org facility modifier is built. | personality / free agency (a new unit) | — | Gated on personality activation |
| Agent Wire quality targets unmeasured | AGENT_WIRE_VOICE_PLAN §2.6 | `tools/social_quality_report` reproduces the archive baseline (553/317, 160/72, 88/40) but has never measured a season written with the new prompts. The archive itself measures 60.6% distinct openers, 24.9% forward-looking and 61.8% citing 3+ numbers against the plan's targets (≥85%, ≥30%, ≤15%), so the rewrite's live effect is unproven. Run it on a live season once posts exist. | JR / a live season | Confidence that the rewrite actually works | Open — CLOSE_OUT_CHECKLIST P8-02 |
| Agent Wire blind A/B + live post read | AGENT_WIRE_VOICE_PLAN §2.6, U5 | `tools/social_model_compare.py --prompts both` was verified with `--dry-run` only (no model calls). Run it with models and blind-read old vs new, then blind-read a week of live posts: scores mean nothing if the posts still read canned. | JR | Accepting the prompt rewrite | Open — CLOSE_OUT_CHECKLIST P8-03 |
| Mid-season head-coach firings | CLOSE_OUT_CHECKLIST P3-07 audit (JR, 2026-11-10; board M-0299) | Mu's coaching carousel only changes head coaches between seasons, so the real 0–3 mid-season changes a year never happen. JR made the A3 mid-season count report-only (TUNING_ACCEPTANCE_THRESHOLDS §7 decision 8) and asked for this as a follow-up: an in-season firing path (owner patience / hot seat during the season, an interim HC) and then the gate back on. | unassigned | Nothing before 2026 week 1 | Open, not scheduled |

## Activation gates (deliberate, not gaps)

These are the known "built but off" items — every personality feature is `off` by design until the activation
plan's shadow season and JR's clearance. Not code gaps; kept here so they aren't mistaken for missing work.

- **Owners/scouts/voters six features** (`owner_patience`, `owner_hiring`, `owner_spending`, `owner_meddling`,
  `scout_directors`, `media_voters`): the migration is written and mirrored in `db/init_db.sql` and applied to
  `madden_test` and to the franchise database `madden_mu2026` (both 2026-11-09; CLOSE_OUT_CHECKLIST P1-04).
  Each feature now waits for its tuning-franchise shadow season and JR's clearance (`personality/features.py`,
  PERSONALITY_ACTIVATION_PLAN / P5).
- The whole personality programme is gated the same way (contracts, development, evolution, relationships, trade
  requests, retirement); the activation plan owns the schedule.

### Scouting directors need a reviewed `staff:scout` receipt (P1-11)

`scout_directors` is wired end to end (`scouting_engine.reviewed_scout_context` → `catch_up`/`step_scouting`,
the Draft Room, the CLI), but it stays inert until a reviewed receipt exists in the franchise's applied
lifecycle batch. JR's steps:

1. Add a `staff:scout` entry to the lifecycle `classifications.json` receipts (`scope` `staff:scout`, `origin`
   `generated`, with `reviewer`, `reason`, `reviewed_at`, `effective_at`, `source_ref`, `source_sha256`).
2. `python3 -m tools.personality_inventory preview --snapshot <save snapshot> --universe <universe.json>
   --classifications classifications.json --cutoff cutoff.json` and read the manifest it writes.
3. `python3 -m tools.personality_inventory apply --manifest <path> --review <review.json> --expect-hash <sha256>
   --dsn "<the franchise DSN>"` (or `load_save --personality-manifest/--personality-review`).
4. Run the scouting step: the log line "scouting directors off" disappears, and each team gets its director,
   board lean and private report rows once `scout_directors` is shadow or active.

Until `754a658` (2026-11-09) step 4 could not work however carefully steps 1–3 were done: `plan_lifecycle` kept
the receipts only as a hash, so the applied batch's manifest had no `classifications` and the loader reported "the
applied lifecycle batch carries no staff:scout receipt" every time. The receipts now ride in the manifest, and
`tests/test_scout_directors_e2e_pg.py` proves the whole path on a scratch database. A batch applied before that
commit has no receipts stored: preview and apply once more (the hash changes) before expecting directors.
