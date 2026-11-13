# Close-out status inventory — what exists in the repo today

**Written 2026-11-12 by `deepseek-v4.1-flash (closeout-survey)`**, against `main` at `9278242`.
**Source:** [`docs/CLOSE_OUT_CHECKLIST.md`](../CLOSE_OUT_CHECKLIST.md) (588 lines, P0–P9, Appendices A–C).

This is an **inventory, not an audit**. Auditing is postponed (JR has no Codex quota, board M-0642) and a
DeepSeek-routed session never audits. Nothing here is a verdict on whether a thing is *right*.

## How to read a row

| Status | Means exactly this |
|---|---|
| **IMPLEMENTED** | I read the named file/function/table/flag and it exists and behaves as the checklist describes; where a test is named, it exists and passed in my run. **This is the ceiling — "the code is there and its tests pass."** |
| **PARTIAL** | Some of the item exists and some does not; the gap is named. |
| **NOT FOUND** | I looked for the named artifact or behaviour and did not find it. |
| **UNVERIFIED-NEEDS-AUDIT** | The artifact exists, but saying whether it is *correct* needs judgement I am not allowed to give. Used only where the item's "done when" is itself a judgement. |

**What I did not do:** no verdicts (no CLOSED / PARTIAL-AUDIT / OPEN), no commit-range review, no correctness
claims, no writes outside this doc and one spec line, nothing on the Madden PC, no plan under `docs/in_progress/`
edited, and no database touched except read-only `SELECT`s and `db.migrate status` on `madden_test` and
`madden_mu2026`.

**Evidence I ran or read today (2026-11-12, ~05:41–05:55 EST):**

- `db.migrate status` on both databases: **`144 applied, 0 pending, 0 drifted, 0 in ledger only`** each;
  `db/migrations/*.sql` = **144 files**.
- `pytest` subset A (21 files: agent loops, scouting, voter, inbox voice, GM talks/trades, annual steps, report
  cards, staff contracts, watcher, retirements, godfather, decision targets, migrate, league office, season loop,
  tuning): **653 passed, 5 skipped**.
- `pytest` subset B (10 files: social desk/quality, press room/agents/wiring, dossier pilot, Go transport, swarm
  capture, article model compare, inbox API): **266 passed** (+9 subtests).
- `pytest` subset C (personality features/CLI/API): **64 passed, 1 skipped**.
- `personality.features show` on `madden_mu2026`: **20 features, every one `off`**, 0 clearances.
- No integration, live-network, Madden or save-writing job was run.

---

## Summary

| Section | Items | IMPLEMENTED | PARTIAL | NOT FOUND | NEEDS-AUDIT |
|---|---|---|---|---|---|
| P0 — decisions | 7 | 6 | 1 | 0 | 0 |
| P1 — close the code | 27 | 26 | 1 | 0 | 0 |
| P1 §3.5 — plan disposition | 9 | 8 | 0 | 1 | 0 |
| P2 — tuning-league infrastructure | 9 | 9 | 0 | 0 | 0 |
| P3 — offline tuning | 9 | 3 | 3 | 3 | 0 |
| P4 — Madden supersim | 8 | 1 | 1 | 6 | 0 |
| P5 — shadow seasons | 14 | 0 | 1 | 13 | 0 |
| P6 — clearance/activation/dress rehearsal | 5 | 0 | 2 | 3 | 0 |
| P7 — canonical 2026 start | 9 | 2 | 2 | 5 | 0 |
| P8 — checkpoint-2 quality of life | 20 | 2 | 5 | 13 | 0 |
| P9 — 2026–2033 → 2034 | 4 | 0 | 0 | 4 | 0 |
| **Total** | **112** | **49** | **16** | **47** | **0** |

Read the total as: **the code-closing phase (P0–P2) is essentially complete in the tree; the tuning and
checkpoint phases (P3–P9) are mostly not started.** All 47 NOT FOUND rows are in P3–P9, and the only
plan-disposition miss (§3.5) is `FRESH_SAVE_STRATEGY_NOTES.md` still sitting in `docs/in_progress/`.

Where a "done" claim rests only on a plan doc or a board message, the row says so. That is the
`IMPLEMENTED but unverified` case the task asked about; it is concentrated in P1/P2, whose plans were written
and merged but not re-checked after later changes.

---

## P0 — Decisions to lock before the programme starts

| ID | One-line ask | Status |
|---|---|---|
| P0-1 | Tuning-before-2026 written into the plans | IMPLEMENTED |
| P0-2 | What "tuned" means — acceptance thresholds | IMPLEMENTED |
| P0-3 | 5 simulated seasons count as tuned | IMPLEMENTED |
| P0-4 | The model (provider settled; model per job open) | PARTIAL |
| P0-5 | Un-hold the three deferrals | IMPLEMENTED |
| P0-6 | The 2026 base save `CAREER-MUBASE` | IMPLEMENTED |
| P0-7 | All personality features `active` at 2026 week 1 | IMPLEMENTED |

**Evidence**

- **P0-1** — `PROJECT_SPEC.md` §1 states the revision; `docs/in_progress/V2_MASTER_PLAN.md:392-403` carries the
  "Revised (JR, 2026-11-08): tuning happens *before* 2026 week 1" block and links the checklist.
- **P0-2** — `docs/reference/TUNING_ACCEPTANCE_THRESHOLDS.md` exists with §0 (§0 the two audits), §1 scoring,
  §2 nflverse 2015–2025 decision rates, §4 "not yet computable", §5 "rejected as gates", §7 decisions;
  `engines/tuning/decision_targets.py` exists; `data/tuning/decision_targets.{json,md}` are on disk (dated
  2026-11-08).
- **P0-3** — recorded in the checklist row itself; `data/tuning/progression_fit_seed{0,1,2}.md` were produced
  with 5 seasons / budget 200 (the md headers carry the run settings).
- **P0-4** — **PARTIAL by design**: the provider half is real (`newsroom/tool_loop/openai_loop.py`,
  `newsroom/tool_loop/go_transport.py`, `tools/go_usage.py` all exist; `docs/reference/MODEL_ROUTING_EVALUATION.md`
  is on `main`), the model-per-job half is the open P8-06 decision. `engines/llm_provider.py` still defaults to
  Gemini.
- **P0-5** — all three landed: P1-06 (below), P1-09 (below), P1-07 (below).
- **P0-6** — `data/baselines/CAREER-MUBASE.sha256` and `data/baselines/CAREER-MUBASE.pristine` exist; the
  sha256 pins the PC save path. `madden_mu2026.league_state` = `current_season 2026`, `current_week 1`,
  `madden_league_id 1287775425` (read-only query today), which is the LeagueID the checklist names. The cap
  figures (22,120 raw / 34,120 total) are save-level and not checkable from here.
- **P0-7** — the decision is written (checklist §3.2 / §5 map it to P5–P6). Note the *state* today: 20 features,
  **20 `off`** (`personality.features show` on `madden_mu2026`) — correct for an unstarted P5.

---

## P1 — Close the code

| ID | One-line ask | Status |
|---|---|---|
| P1-01 | Reconcile migration state | IMPLEMENTED |
| P1-02 | Add migration-state tracking | IMPLEMENTED |
| P1-03 | Verify every migration is mirrored in `db/init_db.sql` | IMPLEMENTED |
| P1-04 | Apply migrations to a franchise database | IMPLEMENTED |
| P1-05 | Agent loops U3 wiring | IMPLEMENTED |
| P1-06 | Agent Wire U3 planner | IMPLEMENTED |
| P1-07 | Retirement restore authority (G4) | IMPLEMENTED |
| P1-08 | Free-agency contract-row creation | IMPLEMENTED |
| P1-09 | Inbox latency spike + `llm_compare` gate | IMPLEMENTED |
| P1-10 | Press cloud latency + constants re-check | IMPLEMENTED |
| P1-11 | Scouting-director wiring | PARTIAL |
| P1-12 | Voter article alignment | IMPLEMENTED |
| P1-13 | Interview summary voice | IMPLEMENTED |
| P1-14 | SCOUT risers needs-proxy decision | IMPLEMENTED |
| P1-15 | `OPEN_PRESSER` navigation | IMPLEMENTED |
| P1-16 | Carried-talk flag persistence | IMPLEMENTED |
| P1-17 | Carried-talk team accounting | IMPLEMENTED |
| P1-18 | Comp-pick producer wired | IMPLEMENTED |
| P1-19 | Comp-pick article scheduling | IMPLEMENTED |
| P1-20 | Report-card producer | IMPLEMENTED |
| P1-21 | Report-card article scheduling | IMPLEMENTED |
| P1-22 | Staff-contract producer | IMPLEMENTED |
| P1-23 | CPU owner facility-upgrade AI | IMPLEMENTED |
| P1-24 | `watch_save` skips `CAREER-TUNE-*` | IMPLEMENTED |
| P1-25 | Trade-talks replay + transcript review | IMPLEMENTED |
| P1-26 | Retirement rows at the season boundary | IMPLEMENTED |
| P1-27 | Godfather trigger gates, part 1 | IMPLEMENTED |

**Evidence**

- **P1-01** — `db.migrate status` today: `144 applied, 0 pending, 0 drifted, 0 in ledger only` on `madden_test`.
  The checklist's "behind six" list is history; `2027_02_09_init_db_parity.sql` and the wave-3 files are applied.
- **P1-02** — ledger table populated (144 rows); `db/migrate.py` subcommands `status`, `apply`, `baseline`,
  `diff`, `sync-init-db`; `tests/test_migrate.py` passed (subset A).
- **P1-03** — `db.migrate diff` between the two databases reported "Schemas are identical" (board M-0640, and
  `sync-init-db` reported `db/init_db.sql already current (144 migrations)`); `tests/test_migrate.py` green.
- **P1-04** — `madden_mu2026` at 144/0/0, **216 public tables**, `db.franchises list` shows
  `madden_mu2026 league_id=1287775425 name=mu2026 saves=CAREER-MUBASE`; `personality.features show` runs (rc 0)
  and lists 20 features. The checklist's "128 applied" is stale — see Appendix B.
- **P1-05** — `tests/test_agent_loops_wiring.py`, `tests/test_agent_loops_statements.py`,
  `tests/test_agent_loops_promises_wiring.py` all passed; `coach_loop` and `player_loop` are in
  `personality/features.py` `FEATURES`. The "shadow run records CHART/CONTRACT/LEAK" half needs a shadow season,
  which does not exist (P5).
- **P1-06** — `newsroom/social_desk.py` with `--season/--week/--json/--dsn` (`main()` at :3526);
  `tools/social_quality_report.py`; `tests/test_social_desk.py` and `tests/test_social_quality_report.py` passed.
- **P1-07** — `docs/reference/RETIREMENT_G4_RUNBOOK.md`, `verify_output/retirement_g4/proof_draft.json`
  (`kind: retirement-authority-proof-v1`, `fixture_id: g4-2026-11-09`, `schema_version: 7`),
  `verify_output/retirement_g4/census.json`, `tools/retirement_g4_fixture.py`; the signing restore is
  `pipeline/league_office.py:2105 step_restore_retirees`.
- **P1-08** — `pipeline/save_reader/sign.mjs`; `save_writer.mjs:222` opens with `autoUnempty: true`;
  `tests/test_league_office.py` passed. The Node test `pipeline/save_reader/test/sign.test.mjs` exists (not run
  by me — needs the Node harness).
- **P1-09** — `engines/inbox/voice.py:35 INBOX_LLM_BUDGET_S = 4.0` with the spike figures in the comment;
  `tools/llm_compare.py:59-60` has `inbox_reply` and `inbox_appraisal`;
  `docs/reference/P1_LATENCY_AND_INBOX_GATE.md` records p50 1.988 s / p95 2.462 s / max 2.501 s.
- **P1-10** — same report file, press-cloud section (83 calls, 278,679 in / 9,200 out, $0.0474, 111 s).
- **P1-11** — **PARTIAL**: `engines/scouting_engine.py:479 reviewed_scout_context` reads
  `persona_lifecycle_batches` for an applied `staff:scout` batch; `scout_reports` gained `team_id`, `scout_id`,
  `voice` (migration `2027_02_11`); `tests/test_scout_director_wiring.py` passed. But
  `madden_mu2026.persona_lifecycle_batches` has **0 rows**, so the directors are inert — exactly as the
  checklist says.
- **P1-12** — `newsroom/swarm_dispatcher.py:1464 _ballot_brief`, used at :1589 before the Week 19/22 articles;
  `tests/test_voter_article_alignment.py` passed.
- **P1-13** — `engines/inbox/voice.py:384-385` honours `kind.intent_voice`; `tests/test_inbox_voice.py` and
  `tests/test_inbox_kinds_prospect_scout.py` passed.
- **P1-14** — decision-only item; recorded in the checklist row and no longer in `docs/FOLLOW_UPS.md`; the
  behaviour it describes (needs-weighted `fit`, no strict needs filter) is in
  `engines/scouting_boards.py:86 fit_bias`.
- **P1-15** — `Mu_Network/src/components/franchise/InboxModule.tsx:42` resolves `link_table` to a page and
  `:473` navigates on `OPEN_PRESSER`; `tests/test_inbox_kinds_press.py` exists. The browser click-through is
  P8-08, not this item.
- **P1-16** — migration `2027_02_12_trade_talk_flags.sql`; column exists
  (`gm_trade_negotiations.flags ARRAY`, read-only query); `engines/gm_talks.py:497-502` writes and reads it;
  `tests/test_gm_talks.py` passed.
- **P1-17** — `engines/gm_talks.py:844 resume_talks`, called at `engines/gm_trade_engine.py:2208` before the
  search; `tests/test_gm_trade_engine.py` passed.
- **P1-18 / P1-19 / P1-20 / P1-21 / P1-22 / P1-23** — all wired in
  `pipeline/league_office.py:3331-3347` (`compensatory_picks_run.award_and_persist`,
  `staff_contracts_run.annual`, `report_card_run.annual`, then `compensatory_picks_article.run(...)` and
  `report_card_article.run(...)`); `engines/owner_policy.py:198 facility_style`, `:208
  facility_upgrade_orders`; tests `test_league_office_annual.py`, `test_report_card_run.py`,
  `test_staff_contracts_run.py` passed. Tables exist on both databases (`team_report_cards`,
  `owner_cash_ledgers`, `staff_contracts`).
- **P1-24** — `pipeline/watch_save.py:101 TUNE_SAVE_PREFIX`, `:119 is_tuning_db`, `:125-148` refuse a clone
  routed anywhere but `madden_tune_*`, `:2174` `--allow-tune-save`; `tests/test_watch_save.py` passed.
- **P1-25** — `docs/done/UNILATERAL_TRADE_ENGINE_PLAN.md`, `docs/done/GM_TALK_VOICE_PLAN.md`,
  `verify_output/p1_25/voice2/` (5+ transcripts: agreed, agreed_counter, stalled ×2, walked_away). JR's
  approval is a doc claim, not something I can verify.
- **P1-26** — `pipeline/load_save.py:523 load_retirements`; migration `2027_02_04_nfl_history`-era tables exist
  (`player_retirements` present on both DBs); `tests/test_load_retirements.py` passed.
- **P1-27** — `engines/gm_rollover.py:480 hot_seat_only`, `engines/coaching_carousel.py:416 candidate_sliders`;
  `tests/test_godfather_gates.py` passed.

### §3.5 — Plan disposition (what leaves `in_progress/`)

| Plan | Checklist says | Status today |
|---|---|---|
| `COACH_PLAYER_AGENT_LOOPS_PLAN.md` | `done/` | IMPLEMENTED — in `docs/done/` |
| `AGENT_WIRE_VOICE_PLAN.md` | `done/` | IMPLEMENTED — in `docs/done/` |
| `FRONT_OFFICE_INBOX_PLAN.md` | remove from FOLLOW_UPS once P1-09 + P8-02 land | IMPLEMENTED as state — the plan is in `done/`; its one FOLLOW_UPS row ("Inbox page visual check") correctly stays open because P8-02 has not landed |
| `PERSONALITY_PLAN.md` | stays until P6 | IMPLEMENTED as state — still in `docs/in_progress/` |
| `PROGRESSION_PLAN.md` | stays until P3 signs / P6 applies | IMPLEMENTED as state — still in `docs/in_progress/` |
| `SEASON_CONTINUITY_PLAN.md` | `done/` | IMPLEMENTED — in `docs/done/` |
| `V2_MASTER_PLAN.md` | stays until P7 | IMPLEMENTED as state — still in `docs/in_progress/` |
| `FRESH_SAVE_STRATEGY_NOTES.md` | `done/` or delete | **NOT FOUND** — still `docs/in_progress/FRESH_SAVE_STRATEGY_NOTES.md` |
| `V3_STUDIO_PLAN.md`, `V3_DOSSIER_EVALUATION.md`, `DYNAMIC_PRESENTATION_*` | stay (checkpoint 2) | IMPLEMENTED as state |

---

## P2 — Tuning-league infrastructure

| ID | One-line ask | Status |
|---|---|---|
| P2-01 | First live `sim-season` | IMPLEMENTED |
| P2-02 | Extend the pilot catalog for the offseason | IMPLEMENTED |
| P2-03 | Offseason stage handling (one stop at a time) | IMPLEMENTED |
| P2-04 | Tuning database + routing | IMPLEMENTED |
| P2-05 | A tuning-run harness (`season_loop`) | IMPLEMENTED |
| P2-06 | Reset and isolation policy | IMPLEMENTED |
| P2-07 | Media off by default in tuning | IMPLEMENTED |
| P2-09 | Re-baseline the tuning tooling | IMPLEMENTED |
| P2-10 | Week-by-week tuning seasons | IMPLEMENTED |

(There is no **P2-08** in the checklist — the numbering skips it. Flagged in Appendix B.)

**Evidence**

- **P2-01** — `tools/madden_pilot/__main__.py:282 sim-season`, `:288 advance`; run artifacts under
  `data/tuning/seasons/p2d…p2h/` (per-run `pilot_regular/`, `pilot_playoffs/`, `run.json`, `season_2026.md`).
  Live-run evidence itself is board/doc history (M-0332 and the checklist).
- **P2-02** — `tools/madden_pilot/catalog.json` holds **31 screens**, including the offseason set named by the
  item: `ADVANCE_LOCKED`, `LIVE_EVENT_FRENZY`, `LIVE_EVENT_FRENZY_MODAL`, `FREE_AGENCY_ENDED`, `DRAFT_HUB`,
  `DRAFT_LAUNCH`, `ACCOUNT_ERROR`. The empty postseason hubs are handled inside `HUB_WEEKLY`'s header words
  (`DIVISIONAL`, `CONFERENCE`, `CHAMPIONSHIP` at catalog.json:766-768) rather than as separate screens.
  The checklist's "27 screens" is now stale.
- **P2-03** — one-stop advance is `tools/madden_pilot/__main__.py:288` (`--no-quit`, `--franchise`);
  `engines/tuning/season_loop.py` drives advance → `watch_save --once --dsn madden_tune_<run>` → advance and
  records every stop in `progress.json`; the draft-order lock is `80237fb`.
- **P2-04** — `pipeline/watch_save.py:134 setup_tuning_dsn`, flags `--auto-init` / `--reseed` /
  `--confirm-reseed` (`:51-57`), `:147` refuses a non-`madden_tune_*` destination; recipe in
  `docs/reference/TUNING_LEAGUE.md` §2–§5. One board claim does not hold: `engines/tuning/tuning_db.py` (M-0092)
  does not exist — the tuning-DSN logic is all in `pipeline/watch_save.py`, which is where P2-04's behaviour
  actually lives.
- **P2-05** — `engines/tuning/season_loop.py:491 main` with `--run`; `data/tuning/seasons/p2g/season_2026.md`
  reads **score 0.187, 257 of 398 stats inside the band** (matches the checklist); `p2f` 0.193.
- **P2-06** — `docs/reference/TUNING_LEAGUE.md` §3 has Create / Reset / Drop, including the exact
  `--reseed --confirm-reseed madden_tune_p2a` command at §3 line 77.
- **P2-07** — `engines/llm_provider.py:48 NO_LLM_ENV = "MU_NO_LLM"`, `:52-56` the kill switch;
  `pipeline/watch_save.py:1634-1635` sets `MU_NO_LLM=1` for every `madden_tune_*` ingest child unless the
  operator set `MU_NO_LLM=0`.
- **P2-09** — `tools/madden_pilot/jobs.py:50 BASELINE_SAVE = "CAREER-MU2026"`,
  `engines/tuning/supersim_loop.py:52 BASE_SAVE = "CAREER-MU2026"`, and `.claude/skills/tune/SKILL.md:59` names
  `CAREER-MU2026` and forbids `CAREER-WEEK1`.
- **P2-10** — commits `22db37f`, `f0e7bbd`, `e76995c`, `b348af5` all present by sha; `--max-weeks` at
  `engines/tuning/season_loop.py:272`; `data/tuning/seasons/p2h/progress.json` exists with `week_01`,
  `week_02`, `week_03` stop folders and `save: CAREER-TUNE-P2H`.

---

## P3 — Offline tuning (deterministic engines)

| ID | One-line ask | Status |
|---|---|---|
| P3-01 | Build the 2015–2025 targets | IMPLEMENTED |
| P3-02 | Run the progression fit | IMPLEMENTED |
| P3-03 | Career-length targets (real half) | PARTIAL |
| P3-04 | Development-curve targets verdict | PARTIAL |
| P3-05 | Retirement baseline | NOT FOUND |
| P3-06 | `decision_targets.py` | IMPLEMENTED |
| P3-06a | Measure Mu's own player inflow | IMPLEMENTED |
| P3-07 | Decision comparison report | PARTIAL |
| P3-08 | Post-unilateral trade flow re-measure | NOT FOUND |

**Evidence**

- **P3-01** — `data/tuning/targets_2015_2025.json` on disk (135,064 bytes, 2026-11-10 13:53);
  `engines/tuning/targets.py` exists (git-ignored data, as the spec says).
- **P3-02** — `data/tuning/progression_fit_seed{0,1,2}.md|.json` on disk (2026-11-10 19:39), each with the
  ranked-constants table and before/after score. Nothing applied — consistent with "recommends only"
  (`engines/tuning/progression_fit.py:81`).
- **P3-03** — **PARTIAL**: the real-NFL half exists — `data/tuning/decision_targets.md` §"Career length
  (seasons), by position" with the drafted/all/undrafted split and the 4.36 vs 3.40 reconciliation. The
  "compare with Mu's output" half waits on P5; P3-06a measured the inflow that bounds it.
- **P3-04** — **PARTIAL**: the verdict exists (`docs/reference/PROGRESSION_DEVELOPMENT_VERDICT.md`, written
  2026-11-11) and its follow-up is *partly built*: `db/migrations/2027_02_26_league_shape_season.sql` adds
  `league_state.shape_season` and `engines/progression.py:1280` now reads `league_shape_season(cur)` instead of
  the constant 2036. **Not done:** the P3-02 re-run the verdict asks for — every `progression_fit_seed*.md` is
  dated 2026-11-10, before the fix.
- **P3-05** — NOT FOUND: no retirement base-rate calibration artifact; `docs/FOLLOW_UPS.md` does not list it and
  no plan file covers it.
- **P3-06** — module + `tests/test_decision_targets.py` (passed) + `TUNING_ACCEPTANCE_THRESHOLDS.md` §2. The
  "remaining: curated items with no open dataset" (GM/coordinator changes, blockbuster rate, holdouts) is
  stated in §4 of that doc and still open.
- **P3-06a** — **IMPLEMENTED 2026-11-12 (`p3-decision-fit`)**: `decision_compare.read_player_inflow` measures
  players entering per season on a Mu database (first signed-or-played season), split drafted (Madden's
  `players.draft_round` 1–7, else a linked actual pick, else bounded by the class's pick count) vs undrafted,
  with the roster bound (`slots / entrants`) and a report-only `A1.inflow` row in every decision report.
  Measured on `madden_tune_p2c`: **2027 — 247 entrants (244 rookies, 3 veterans first seen), at least 224
  drafted and at most 20 undrafted, 1,869 slots → 7.57 seasons (6.87 on 32 × 53)**. `p2g`/`p2f` show 188/193
  entrants and 165 rookies (their DRAFT stage wrote only the comp picks). **Population chosen: the drafted
  cohort stands** (92% of Mu's rookie inflow), with the all-comers 4.36 as the floor; recorded in
  `TUNING_ACCEPTANCE_THRESHOLDS.md` §0.2, §2 A1 and §7 decision 2. Tests: `tests/test_decision_compare.py`
  (exact split, bounded split, unknown class, Postgres measurement; 62 passed on the scratch p2g copy).
- **P3-07** — **PARTIAL**: `engines/tuning/decision_compare.py` exists and `season_loop` writes the report —
  `data/tuning/seasons/p2g/decisions_2026.md` is on disk and reads "INCOMPLETE — 2 pass, 0 fail, 33 not
  measured" (it predates F4/F5 and the drafted-cohort change; p2g carries no `players.draft_round`, so A1/A2/A6
  stay unmeasured there). **F4 (A6 started-season) and F5 (A1/A2 ended-career cohort) are built**, not held:
  `7375d1f`, with the R1b/R2b/R4 (`a2238d8`) and A6 chart-in-force (`76a22fa`) rounds CLOSED in
  `data/audit_reports/p307_*` and `a6_rules_fix`. Verified 2026-11-12 (`p3-decision-fit`) with three new
  Postgres cohort tests (`tests/test_decision_compare.py`, 58 passed): the A6 class reaches a measured share,
  the A1/A2 cohort reaches measured gates with an unsigned free agent inside, and the audit's lone
  `(Y-2, 26)` chart credits only Y-1. The 33-not-measured remains the live symptom of the p2g data (no draft
  round, no linked class, no weekly trade engine), not of an unbuilt gate.
- **P3-08** — NOT FOUND: no post-unilateral re-measure; `docs/FOLLOW_UPS.md` still carries the row ("Post-unilateral
  trade flow re-measure → JR / P3-07").

---

## P4 — Madden supersim: measure Sabos first

| ID | One-line ask | Status |
|---|---|---|
| P4-01 | Baseline the installed game | PARTIAL |
| P4-02 | Pass → stop, record "Sabos as-is" | NOT FOUND |
| P4-03 | Verify EBX access | IMPLEMENTED |
| P4-04 | Narrow `space.json` from the worst stats | NOT FOUND |
| P4-05 | Measure noise first | NOT FOUND |
| P4-06 | Run the loop | NOT FOUND |
| P4-07 | Install accepted deltas | NOT FOUND |
| P4-08 | Re-run §5.4 save checks on the modded save | NOT FOUND |

**Evidence**

- **P4-01** — **PARTIAL**: the measuring machinery exists (`engines/tuning/compare.py`,
  `engines/tuning/supersim_loop.py:205 iteration`) and one simmed season *is* scored with a worst-10 table
  (`data/tuning/seasons/p2g/season_2026.md`: score 0.187, 257/398 inside band, worst `RB.rec_yards.p50` 1.91).
  But that report is a tuning-season report, not a P4 baseline: nothing in the tree names it as the P4-01
  go/no-go, and no "Sabos" baseline report file exists.
- **P4-02** — NOT FOUND: no signed-off "Sabos used as-is / supersim not tuned" ledger entry (and no
  `docs/reference/TUNING_LEDGER.md` at all — see P6-03).
- **P4-03** — IMPLEMENTED as a precondition only: `docs/reference/MOD_BUILDING.md:152` records
  `ebx_readable: true`, `key_source: editor_builtin`, 159 initfs files on 2026-10-31. No fresh probe was run
  (it needs the PC).
- **P4-04…P4-08** — NOT FOUND: no `space.json`, no two-baseline noise measurement, no `data/tuning/runs/<run>/`
  history, no accepted delta in `Mu Base`, no modded-save save-check rerun.

---

## P5 — Shadow seasons: the decision engines

| ID | One-line ask | Status |
|---|---|---|
| P5-01 | Migrations on the tuning DB; every feature can reach `shadow` | PARTIAL |
| P5-02 | `producers` + `relationships` for one season | NOT FOUND |
| P5-03 | `profiles` lifecycle inventory | NOT FOUND |
| P5-04 | `evolution` + `focus_refresh` | NOT FOUND |
| P5-05 | Contract features vs FA bands | NOT FOUND |
| P5-06 | `development_learning` vs the progression fit | NOT FOUND |
| P5-07 | `trade_requests` + `retirement` | NOT FOUND |
| P5-08 | `development_opportunity` + `coach_loop` | NOT FOUND |
| P5-09 | `player_loop` | NOT FOUND |
| P5-10 | Owner family (5 features) | NOT FOUND |
| P5-11 | `scout_directors` + `media_voters` | NOT FOUND |
| P5-12 | Facility personality sensitivities + Q2 refusal | NOT FOUND |
| P5-13 | Godfather gates, part 2 | NOT FOUND |
| P5-14 | Cumulative league | NOT FOUND |

**Evidence**

- **P5-01** — **PARTIAL**: the switchboard and its migrations exist on both readable databases
  (`personality_feature_modes`, `personality_feature_mode_changes`, `personality_feature_clearances` on
  `madden_test` and `madden_mu2026`; 144/0/0 both), `personality/features.py` is the CLI, and `set … shadow` is
  exercised by `tests/test_personality_features.py:101-147` (subset C green). I did **not** inspect a
  `madden_tune_*` database (out of the read-only scope I was given), so "every feature reaches `shadow` on a
  tuning DB" is unconfirmed here.
- **P5-02…P5-14** — NOT FOUND: no shadow-season run, no per-feature report, no adjusted/versioned constants.
  `personality.features show` on `madden_mu2026` reports 20 features, 20 `off`, and
  `personality_feature_clearances` has 0 rows. The engines themselves exist (the checklist's §7.2 report
  columns are engine-level features, off by default), so these are "not started", not "not built".

---

## P6 — Clearance, activation, dress rehearsal

| ID | One-line ask | Status |
|---|---|---|
| P6-01 | Export a clearance per feature | PARTIAL |
| P6-02 | Import and activate on the real franchise | PARTIAL |
| P6-03 | Write the tuning ledger | NOT FOUND |
| P6-04 | Dress rehearsal | NOT FOUND |
| P6-05 | Freeze declaration | NOT FOUND |

**Evidence**

- **P6-01 / P6-02** — **PARTIAL**: the CLI verbs exist — `personality/features.py:616 export-clearance`,
  `:621 import-clearance`, `:623 activate-cleared` (with `--label`, `--out`, `--report`, `--review`), tests in
  subset C. No clearance has been produced or imported: `personality_feature_clearances` = 0 rows on both
  databases, 20 features `off`.
- **P6-03** — NOT FOUND: `docs/reference/TUNING_LEDGER.md` does not exist and `data/tuning/frozen.json` does not
  exist.
- **P6-04 / P6-05** — NOT FOUND: no rehearsal report, no freeze declaration in the spec or a ledger.

---

## P7 — Start the canonical 2026 franchise (Checkpoint 1)

| ID | One-line ask | Status |
|---|---|---|
| P7-00 | The canonical start path | PARTIAL |
| P7-00e | Pilot managed-save allowance | NOT FOUND |
| P7-01 | Roster-size mod verification | IMPLEMENTED |
| P7-02 | Re-run §5.4 save checks on the modded save | NOT FOUND |
| P7-03 | Create and register the 2026 franchise | IMPLEMENTED |
| P7-04 | First `league_office setup` | PARTIAL |
| P7-05 | Seed verification (owners/coaches/GMs/inventory/history/features) | NOT FOUND |
| P7-06 | Start the watcher/freeze/site and run 2026 week 1 | NOT FOUND |
| P7-07 | Tuning is now closed | NOT FOUND |

**Evidence**

- **P7-00** — **PARTIAL**: (a) hash-pinned baseline exists (`data/baselines/CAREER-MUBASE.sha256`,
  `.pristine`); (b) the franchise DB is registered (`db.franchises list`: LeagueID 1287775425); (c) setup is
  recorded as done and `madden_mu2026` carries the result (2,847 players, 32 `team_seasons`); (d) the watcher
  scoping exists; (f) tuning re-baselined (P2-09). **(e) is missing** — see P7-00e.
- **P7-00e** — NOT FOUND: `tools/madden_pilot/jobs.py` has no `--managed` flag
  (`grep '"--managed"' tools/madden_pilot/*.py` → nothing); `sim_season` (`jobs.py:858`) still refuses every
  non-`CAREER-TUNE-` save and `TUNE_SAVE_PREFIX` (`:48`) is still the only allowed class. The checklist cites
  `jobs.py:372`; the guard has moved, the behaviour has not changed.
- **P7-01** — IMPLEMENTED: `engines/transactions_engine.py:60 ROSTER_LIMIT = 65`; the save-side evidence (65 at
  cut day, +$40M cap) is JR's in-game confirmation recorded in the checklist and not re-checkable here.
- **P7-02** — NOT FOUND: no §5.4 save-check rerun artifact.
- **P7-03** — IMPLEMENTED: `db.franchises list` shows `madden_mu2026` active, and its database exists with 216
  tables and `league_state` 2026/1. (It is the seeded rehearsal franchise from P1-04; the canonical *start* is
  still P7-06.)
- **P7-04** — **PARTIAL**: `pipeline/league_office.py` setup exists and its output `CAREER-MU2026` is the
  recorded clone source (P2-09), which implies it ran. The item's own "output save reads back clean" needs the
  PC and a save reader; I could not check it. Nothing in `madden_mu2026` contradicts it (2,847 players = the
  bootstrapped population the checklist quotes).
- **P7-05** — NOT FOUND: `madden_mu2026` today has `team_owners` 0, `coaches` 32, `gm_profiles` 32,
  `gm_team_seasons` 32, `nfl_games` **0** (history not loaded), `nfl_player_seasons` **0**, 20 features **off**.
  The item wants 32 owners, 96 coach profiles, the full lifecycle inventory, `nfl_history` loaded and all
  features `active`.
- **P7-06 / P7-07** — NOT FOUND: no week-1 run record, no freeze date.

---

## P8 — Checkpoint 2: live-franchise quality of life

| ID | One-line ask | Status |
|---|---|---|
| P8-01 | Agent Wire U3 planner (same as P1-06) | IMPLEMENTED |
| P8-02 | Measure tweets against the 2036 baseline | PARTIAL |
| P8-03 | Blind-read a week of live posts | NOT FOUND |
| P8-04 | Close the dossier factual defects | NOT FOUND |
| P8-04a | DeepSeek arm for the dossier pilot | IMPLEMENTED |
| P8-05 | Paired pilot on the widened Go matrix | PARTIAL |
| P8-06 | Decide routing / provider / model per job | NOT FOUND |
| P8-06a | If a Go model wins: move the newsroom callers | NOT FOUND |
| P8-06b | Pick the model for the decision call sites | NOT FOUND |
| P8-07 | Sample-read a live week's slate | NOT FOUND |
| P8-08 | Inbox page visual check | NOT FOUND |
| P8-09 | Presser page visual check | NOT FOUND |
| P8-10 | Trade Machine + League Talks visual check | NOT FOUND |
| P8-11 | Command Center layout sweep | NOT FOUND |
| P8-12 | Boot straight into the game | PARTIAL |
| P8-13 | Dynamic presentation mod — production integration | PARTIAL |
| P8-14 | Mods for playability | PARTIAL |
| P8-15 | In-game portraits (optional/deferred) | NOT FOUND |
| P8-16 | Interactivity latency with the live model | NOT FOUND |
| P8-17 | Full week's pipeline wall time | NOT FOUND |

**Evidence**

- **P8-01** — identical to P1-06 (evidence above).
- **P8-02** — **PARTIAL**: `tools/social_quality_report.py` exists with `--db/--season/--weeks/--json`
  (`:333-336`) and `tests/test_social_quality_report.py` passed; the archive-baseline reproduction
  (553/317, 160/72, 88/40) is recorded in `docs/done/AGENT_WIRE_VOICE_PLAN.md:232,312`. The "live league must
  beat it" half needs a live season, and `madden_2036` is outside the databases I may query.
- **P8-03** — NOT FOUND: no blind read record for a live week (`FOLLOW_UPS` carries it as "Agent Wire blind A/B
  + live post read", open).
- **P8-04** — NOT FOUND: no fix record for the three named dossier defects (wrong tackle leader, interception-rate
  misdescription, stale `BOOK` record claim); no doc references a P8-04 fix.
- **P8-04a** — IMPLEMENTED: `tools/dossier_pilot.py:5-6` documents `--provider gemini|deepseek`,
  `:39 DEEPSEEK_MODEL = 'deepseek-flash'`, peak/off-peak pricing at `:54`; built at `fc3fe72`, truncation
  flagging at `e99bec4`; `tests/test_dossier_pilot.py` passed.
- **P8-05** — **PARTIAL**: the parallel article-level track is the in-progress
  `docs/in_progress/NEWSROOM_MODEL_SIDE_BY_SIDE_PLAN.md` and is being run right now by
  `model-routing-research`; its code (`newsroom/tool_loop/go_transport.py`, `tools/article_model_compare.py`)
  and tests (`test_go_transport.py`, `test_swarm_capture.py`, `test_article_model_compare.py`) exist and passed.
  The checklist's own cell matrix has not been run.
- **P8-06 / P8-06a / P8-06b** — NOT FOUND as decisions. The transport they would use exists
  (`newsroom/tool_loop/go_transport.py`, `tools/go_usage.py`), but no per-job routing table or decision is
  recorded.
- **P8-07** — NOT FOUND.
- **P8-08 / P8-09 / P8-10 / P8-11** — NOT FOUND as checks: the pages and their API exist, but no browser
  walk-through artifact. All four are open rows in `docs/FOLLOW_UPS.md`.
- **P8-12** — **PARTIAL**: `pipeline/game_launch.py` writes the game's pack and starts Madden through the MMC
  manager (`-launch <pack>`), and the pilot's `launch` job presses through the cold boot to a menu
  (`docs/reference/MADDEN_PILOT.md:112`). The item wants the route all the way to the **user's pregame/kickoff
  screen** with a hand-back on timeout — that route is not in the catalog or the pilot.
- **P8-13** — **PARTIAL**: the research is in `docs/in_progress/DYNAMIC_PRESENTATION_{HANDOFF,RESEARCH,
  RESOURCE_AUDIT}.md` and `docs/not_started/DYNAMIC_PRESENTATION_MOD_PLAN.md`; no production integration code,
  and the item's "CBS banner layouts / night-package gaps / pack-vs-static" questions are still open there.
- **P8-14** — **PARTIAL**: `docs/MOD_INSTALL.md:77` documents the `Mu Base` pack and the broken/mod list (Hair
  Fix moved to `Broken/`); `pipeline/mod_packs.py` exists. No record of a full-season crash-free run.
  Note the count mismatch: the checklist says "49 always-on mods", `MOD_INSTALL.md` says "Always on (47)".
- **P8-15** — NOT FOUND (and Appendix C lists it as deliberately out of scope).
- **P8-16 / P8-17** — NOT FOUND: the constants exist (P1-09/P1-10) but no live-league confirmation or week wall
  time.

---

## P9 — 2026–2033 → the 2034 takeover

| ID | One-line ask | Status |
|---|---|---|
| P9-01 | Run 2026–2033 as a fully CPU league | NOT FOUND |
| P9-02 | Monthly health check | NOT FOUND |
| P9-03 | 2034: mark the team user-controlled and hand over | NOT FOUND |
| P9-04 | Post-takeover QoL pass | NOT FOUND |

**Evidence**

- All four: nothing to run yet. P9 is downstream of P7; `madden_mu2026` is at 2026 week 1 with no completed
  season (`league_state` 2026/1; its 272 `games` rows are the seeded schedule — every week from 2 on is 0–0 and
  only 15 week-1 rows carry a non-zero score).

---

## Appendix A — FOLLOW_UPS traceability (as it stands today)

The checklist claims "every open row in `docs/FOLLOW_UPS.md`, mapped. Nothing is dropped." That is now **mostly**
true but not exactly: `docs/FOLLOW_UPS.md` has **11 open rows**. Eight of them appear below; two of them
("Agent Wire quality targets unmeasured", "Agent Wire blind A/B + live post read") are checklist items P8-02 and
P8-03 but have no row in this traceability table; and one ("Mid-season head-coach firings") is mapped nowhere.

| # | Follow-up | Ticketed at | Today |
|---|---|---|---|
| 1 | Scouting-director wiring | P1-11 | PARTIAL (inert, no lifecycle batch) |
| 2 | SCOUT risers needs-proxy | P1-14 | landed (decision) |
| 3 | Interview summary voice | P1-13 | landed |
| 4 | Voter article alignment | P1-12 | landed |
| 5 | Agent loops U3 wiring | P1-05 | landed |
| 6 | Agent Wire U3 | P1-06 / P8-01 | landed |
| 7 | Retirement restore authority (G4) | P1-07 | landed |
| 8 | Free-agency contract-row creation | P1-08 | landed |
| 9 | Inbox latency spike + `llm_compare` | P1-09 | landed |
| 10 | Inbox page visual check | P8-08 | open |
| 11 | Press cloud latency + constants | P1-10 | landed |
| 12 | Press migration not applied | P1-01 / P1-04 | landed |
| 13 | Presser page visual check | P8-09 | open |
| 14 | `OPEN_PRESSER` navigation | P1-15 | landed |
| 15 | Madden pilot live acceptance | P2-01 / P2-02 | landed (P2-01/02) |
| 16 | Supersim tuning loop never run | P4-01…P4-04 | open |
| 17 | Trade-talks migrations not applied | P1-01 / P1-04 | landed |
| 18 | Trade-talks replay + review | P1-25 | landed |
| 19 | Trade Machine + League Talks visual | P8-10 | open |
| 20 | Carried talk loses its flags | P1-16 | landed |
| 21 | Carried-talk team accounting | P1-17 | landed |
| 22 | Comp-pick producer not wired | P1-18 | landed |
| 23 | Comp-pick article scheduling | P1-19 | landed |
| 24 | Report-card migration not applied | P1-01 / P1-04 | landed |
| 25 | Report-card producer not wired | P1-20 | landed |
| 26 | Report-card article scheduling | P1-21 | landed |
| 27 | Facility sensitivities + Q2 refusal | P5-12 | open (gated) |
| 28 | CPU owner facility-upgrade AI | P1-23 | landed |
| 29 | Staff-contracts migration not applied | P1-01 / P1-04 | landed |
| 30 | Staff-contract producer not wired | P1-22 | landed |
| 31 | Godfather trigger gates | P1-27 / P5-13 | P1-27 landed; P5-13 open |
| 32 | Post-unilateral trade flow re-measure | P1-25 / P3-08 | P3-08 open |
| 33 | Trade Finder misses rival overpay | — (no gate) | open by design |
| **—** | **Mid-season head-coach firings** (FOLLOW_UPS, added after the P3-07 audit) | **not mapped** | **open** — Appendix A needs a row 34 |
| **—** | **Agent Wire quality targets unmeasured** | **no Appendix A row** (checklist P8-02) | open |
| **—** | **Agent Wire blind A/B + live post read** | **no Appendix A row** (checklist P8-03) | open |

Count: **24 landed/closed, 9 open or partial** across the 33 numbered rows, plus 3 open rows the table does not
carry (the mid-season firing row and the two Agent Wire rows).

---

## Appendix B — Corrections to Appendix B (§13/§13.1) of the checklist

Appendix B describes 2026-11-09/10. These are the facts it now gets wrong, all checked today:

| Checklist says | Today |
|---|---|
| "127/127 migrations" (§13, both databases) and "128 applied" (P1-04) | **144 files, 144 applied / 0 pending / 0 drifted / 0 in ledger only** on `madden_test` and `madden_mu2026` (board M-0640; `db.migrate status` re-read today). `db/init_db.sql` is current at 144. |
| "Pilot catalog: 19 screens after P2-01" (§13) and "27 screens" (§13.1, P2-02) | **31 screens** in `tools/madden_pilot/catalog.json`, including `ACCOUNT_ERROR` and the offseason/live-event set. |
| "the seven newest migrations replay as no-ops" (P1-03) | True when written; the ledger now has 144 and `2027_02_29_stats_counter_season_games` is the head. |
| "Uncommitted work in the tree: `docs/MOD_INSTALL.md`, several plans, plus untracked research and Colab-result ZIPs" (§13) | Different tree now: the tracked modifications are a deleted `docs/in_progress/OPENCODE_*` pair and `docs/not_started/PRE_PC_WORK_2.md`, plus `headshots/local/backends.py`, `tools/article_model_compare.py`, `tests/test_article_model_compare.py`; a large set of untracked PNG/ZIP/PDF files sits at the root. Not mine, left untouched. |
| "Databases: … `madden_v3_eval_20261030` …" | Still true; the cluster now also holds `madden_press_scratch`, `madden_tt_voice` and five `mu_scratch_*` audit/builder databases (`pg_database`, read-only). |
| "Offline suite: 5,522 passed / 2 failed …" (§13.1) | I did **not** run the full suite; I ran three subsets (653+266+64 passed, 6 skipped). The 5,522 figure is not re-checked. |
| P2 numbering | The checklist has no **P2-08**; the sequence jumps P2-07 → P2-09. |
| P7-00e cites `tools/madden_pilot/jobs.py:372` | That line is now `wait_menu`; `sim_season` is at `:858` and `TUNE_SAVE_PREFIX` at `:48`. The behaviour is unchanged, the line number is stale. |
| P8-14 says `Mu Base` has "49 always-on mods" | `docs/MOD_INSTALL.md:77` says "Always on (47)". |

---

## Known-unverified: the blocked audit queue

These are **not** checklist items, but they are the "code exists, nobody has checked it" case, and any later
reader of this inventory should not mistake them for verified work:

- **stats3 R1/R2** — `26d1141` (per-game club identity, new `tests/test_advanced_metrics_coverage.py`) and its
  253-line test file. No audit verdict.
- **stats2b R1** — `07decd3` (legacy season-games bound, touches `db/init_db.sql` and
  `2027_02_29_stats_counter_season_games.sql`, both now applied to both databases). No audit verdict.
- **U18 N1** — `a641efa`: **CLOSED** by audit-5 at M-0632. The only audited item in this list.
- **`opencode-route-plan` plan reviews** — M-0618 (`dbdac5f^..8a972c0`) and M-0628
  (`dc96309^..fff897f`), blocked on quota alone.

Everything else in the audit queue is **unverified, not failed** (board M-0637/M-0642).

---

## Proposed checklist ticks and corrections — for JR

I did not edit `docs/CLOSE_OUT_CHECKLIST.md`. These are the exact lines I would change, quoted with their
section. Counts are what the evidence above supports.

### Would tick (☐ → ☑)

1. **§9 P7-03** —
   > "| ☐ | **P7-03** | Create the 2026 franchise; register it: `python3 -m pipeline.watch_save <save> --once --auto-init --franchise-name <name>` with **no** `--user-team`. | `db.franchises list` shows it active; its DB exists. | M |"
   → tick, with the correction "as the seeded `madden_mu2026` (P1-04); the canonical start remains P7-06",
   because `db.franchises list` shows `madden_mu2026  league_id=1287775425  saves=CAREER-MUBASE` and its
   216-table database exists.

2. **§6 P4-03 (optional)** —
   > "| ☐ | **P4-03** | **Fail → verify EBX access.** `MmcHarness.exe info` → `ebx_readable: true`, `key_source: editor_builtin`. Never search for or download the key. | Info returns true. | S |"
   → tick with "verified 2026-10-31 before P4 started (`docs/reference/MOD_BUILDING.md:152`); re-probe when
   P4-01 runs". I would only tick it if JR accepts a documented past probe.

### Would correct in place (no status change)

3. **§9 P7-00e** — replace `(tools/madden_pilot/jobs.py:372)` with `(tools/madden_pilot/jobs.py:858;
   `TUNE_SAVE_PREFIX` at :48)`. The line moved; the guard is the same.

4. **§4 P2-02** and **§13** — "27 screens"/"19 screens" → "31 screens today
   (`tools/madden_pilot/catalog.json`)".

5. **§3.1 P1-04** — "128 applied" → "144 applied (2026-11-12; board M-0640)", and §13's "127/127" the same.

6. **§12 Appendix A** — add a row **34**: "Mid-season head-coach firings | CLOSE_OUT_CHECKLIST P3-07 audit |
   P3-07 / unassigned", and rows for the two Agent Wire follow-ups (→ P8-02, P8-03), so no open `FOLLOW_UPS`
   row is unmapped.

7. **§3.5** — `FRESH_SAVE_STRATEGY_NOTES.md` is still in `docs/in_progress/`; either move it to `done/` or
   delete it, per the row's own disposition.

8. **§10.4 P8-14** — "49 always-on mods" → decide whether the number should be 47 (`docs/MOD_INSTALL.md:77`)
   or the doc is behind the pack.

### Would leave unticked, with a note

- **P5-01** — "every feature must be able to reach `shadow`" is true of the switchboard in both readable
  databases and is test-covered, but I did not inspect a `madden_tune_*` database; the note belongs on the row
  when P5 starts.
- **P6-01 / P6-02** — the CLI verbs exist; nothing has been exported or imported (0 clearance rows).
- **P7-00** — (e) is the one missing sub-step; the rest is done.
- **P7-04** — setup is implied by `CAREER-MU2026` being the clone source, but its own save read-back is a
  PC check I could not run.
