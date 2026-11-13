# Project Mu — Close-Out Checklist

**Purpose:** the single ordered list of work between the project's current state (2026-11-08) and the two
"doneness" checkpoints JR named:

1. **Tuned league.** Sliders and constants across the project are tested and calibrated, so progression,
   career length, development curves and every agent decision (coach / player / GM / owner) behave like the
   real NFL — and stay that way for a live run.
2. **Live-franchise quality of life.** The 2034 Command Center, articles, tweets, pressers, inbox, play
   launch and mods look and feel right.

This file is **not** a replacement for [`FOLLOW_UPS.md`](FOLLOW_UPS.md); it *consumes* it. FOLLOW_UPS is the
row-level ledger of deferred work from `docs/done/` plans. This file puts every one of those rows into an
ordered programme, adds the work FOLLOW_UPS excludes (never-started plans, live validation, activation,
tuning), and marks the two gates. Check FOLLOW_UPS before starting anything; tick items here as they land.

**Current state (2026-11-12).** P1 is done except P1-11's activation prerequisite; P2 is done. P3 has
P3-05 and P3-06a open and P3-03/P3-07 partial; P3-02 is marked done but its table predates the
P3-04 full-roster fix and needs a re-run. P4 has not started — P4-01 is the go/no-go. P5-01 is **done**
(all 20 features can reach `shadow`) and P5-01a awaits an audit verdict; P5-02…P5-14 are open. P6 is
untouched. P7 has P7-01 done and P7-00 **partial** ((a), (c), (e) and (f) done; (b) and (d) open) and is **not
passed**; the old “CHECKPOINT 1 PASSED” marker was wrong.
P8 runs in parallel, with the visual mod check at code pin `5f76c43` under recheck. P9 is untouched.

---

## 0. The decision that shapes the whole programme

> **JR, 2026-11-08: tuning happens BEFORE the 2026–2033 run.**
> 2026 week 1 is the start of the real league and is treated as canon from that moment. Constants are frozen
> before it starts. No mid-run retuning.

This **replaces** the earlier assumption in [`V2_MASTER_PLAN.md`](in_progress/V2_MASTER_PLAN.md) §7.2, which
called 2026–2033 the acceptance run. The reasons the old plan is now unsafe:

- **History becomes inconsistent.** Progression writes permanent ratings off a base that carries forward. A
  constant changed in 2029 means seasons 2026–2028 were generated under a different engine than 2029+, and
  the league's age/OVR shape reflects the mixture, not either policy.
- **Decisions compound.** Trades, coach hirings, retirements and contracts in season *n* determine the roster
  in season *n+1*. Retuning in 2028 rewrites the causal chain you were trying to evaluate.
- **You cannot tell tuning noise from drift.** A tuning league you are willing to throw away is the only
  clean instrument.

**Consequence — a freeze-and-change-control rule for the live run:**

- Every tunable constant gets a **version** and is recorded in a tuning ledger before 2026 week 1.
- After 2026 week 1, a constant changes only through a deliberate, documented migration with a version bump,
  a stated reason, and an explicit statement that the change is a break in the live run's history.
- The activation switchboard already supports this (`personality/features.py` carries per-feature `version`
  and `config`); use it rather than editing constants in place.

---

## 1. The shape of the programme

```
TODAY
  │
  ├── P1  Close the code (every follow-up, held unit, unwired producer)
  │        │
  │        ├── P2  Tuning-league infrastructure (scratch DB, save clones, pilot offseason)
  │        │        │
  │        │        ├── P3  Offline tuning (progression, career length, decision targets)
  │        │        ├── P4  Madden supersim tuning
  │        │        └── P5  Shadow seasons (all 20 personality/decision features, in order)
  │        │                  │
  │        │                  ▼
  │        │            P6  Clearance → import → activate → dress rehearsal
  │        │                  │
  │        │                  ▼
  │        └────────────► P7  Start the canonical 2026 franchise  ◄── CHECKPOINT 1 NOT YET PASSED
  │
  ├── P8  Checkpoint-2 lane (QoL: tweets, articles, UI, launch, mods) — runs in parallel from P1
  │         │
  │         ▼
  │    ◄── CHECKPOINT 2 PASSED (after P7 is live)
  │
  └── P9  2026–2033 CPU era → JR takes a team in 2034
```

`P8` is deliberately parallel: most of it is browser checks, prompt work and presentation research that does
not touch the simulation, and none of it can be validated without a live league anyway.

**Size scale:** S = under an hour · M = one session · L = several sessions / a work unit · XL = needs its own
plan (`/propose` + `/execute-plan`).

---

## 2. P0 — Decisions to lock before the programme starts

Nothing below can be sequenced until these are answered. Each is JR's.

| ID | Decision | Why it blocks |
|---|---|---|
| **P0-1** | **Tuning-before-2026 confirmed and written into the plans.** §0 above; V2_MASTER_PLAN §7.2 and §7.3 updated. | Every phase below assumes it. |
| **P0-2** | **What "tuned" means — acceptance thresholds. ✅ Closed (JR, 2026-11-08).** [`reference/TUNING_ACCEPTANCE_THRESHOLDS.md`](reference/TUNING_ACCEPTANCE_THRESHOLDS.md), computed from nflverse 2015–2025 via `engines/tuning/decision_targets.py`. Settled: cutdown week = preseason (in-season trades gate at 5–21); career length and retirement age on the **drafted** cohort; tolerances (±1 season, ±10 points, ±20% relative); the 3-season averaging rule; §4 scope (blockbusters, holdouts and the inflow measurement in; GM/coordinator changes deferred); and the §5 rejected-gate list (records/seeds/awards, FA dollars, retirement timing). | — |
| **P0-3** | **How many simulated seasons count as tuned. ✅ Decided (JR, 2026-11-08): 5 seasons.** `progression_fit` runs 5 seasons (P3); each personality feature needs one full simulated season in shadow (P5). | Sets the length of P2–P5. |
| **P0-4** | **The model. ⚠️ Provider settled 2026-11-12 (JR); the model-per-job choice stays open to P8-06.** The provider is **OpenCode Go**: `newsroom/tool_loop/openai_loop.py` bills `deepseek` through `opencode-go` (`OPENCODE_GO_BASE_URL`, `OPENCODE_GO_API_KEY`) since `OPENCODE_GO_PIPELINE_PLAN`, and agent sessions already run there (`OPENCODE_DEEPSEEK_ROUTE_PLAN`). DeepSeek V4.1 Flash is what the OpenAI-compatible calls use, and `gemini-3.8-flash` is still the `engines/llm_provider.py` default, so the choice still gates the inbox latency spike, the press-cloud constants and every `llm_compare` gate (P1). Go's catalogue is wider than two models (GLM-5.3-Flash, Kimi K3, Qwen3.8, Muse Spark); P8-05 and P8-06 decide from evidence, not from the current default. | P1-09, P1-10, P8-05, P8-06, P8-06b. |
| **P0-5** | **Un-hold the three deferrals. ✅ Decided (JR, 2026-11-08): unheld.** Agent Wire U3 → P1-06, inbox latency spike → P1-09, retirement G4 → P1-07. | — |
| **P0-6** | **The 2026 base save. ✅ Created and verified (JR, 2026-11-09): `CAREER-MUBASE`.** A newly created franchise with the required settings (the roster-size mod only applies at creation; `CAREER-WEEK1` lacked it). Verified from a read-only snapshot: **LeagueID 1287775425** (new, so no collision with the 80+ old saves), 2026 week index 0 RegularSeason franchise_year 0, 3,238 players, 389 prospects, **cap 22,120 raw / 34,120 total vs `CAREER-WEEK1`'s 18,120 — the mod's +$40M**, `IsDevTraitRegressionEnabled` and breakout scenarios both **off**. JR confirmed the **65 cut-day limit in game**. Progression sliders remain at 100 and are zeroed by the progression pass. `league_office setup` still supplies the all-32-user state (superseding `CAREER-REALUSERS`). See **P7-00**. | P7-00, P7. |
| **P0-7** | **All personality features `active` at 2026 week 1. ✅ Decided (JR, 2026-11-08).** No feature may enter the live league in `shadow`; P5 must finish every feature and P6 must activate them all before P7 starts. | P5, P6, P7. |

---

## 3. P1 — Close the code

**Gate:** nothing in P2–P5 starts until P1 is done. This is the "don't leave pre-checkpoint requirements
undone" rule made concrete.

### 3.1 Schema and migration state

| ☐ | ID | Item | Done when | Size |
|---|---|---|---|---|
| ☑ | **P1-01** | **Reconcile migration state. ✅ Applied (2026-11-09).** `madden_test` was verified **behind six** migrations: `2027_01_20` press, `2027_02_02` owners/scouts/voters, `2027_02_06` comp picks, both `2027_02_07` staff files, `2027_02_08` report cards. (`2027_01_22` trade talks and its trigger `2027_02_05` were already applied on 2026-11-08; `persona_focus` and `nfl_history` too.) All six applied with `db/migrate.py`; every table and added column verified with `to_regclass`. | `select to_regclass(...)` returns non-null for every table listed in the migrations; `madden_test` matches a fresh `init_db.sql` build. | M |
| ☑ | **P1-02** | **Add migration-state tracking. ✅ Built (2026-11-09).** `schema_migrations` (`version`, `filename`, `checksum`, `applied_at`, `source`) plus `db/migrate.py` (`status`, `apply`, `baseline`, `diff`, `sync-init-db`). `init_db.sql` ends with a generated ledger block, so a fresh build is at head and `apply` is a no-op. `madden_test` was baselined to 124/124 after the parity proof. | A new database reports its applied set; re-running is a no-op. | M |
| ☑ | **P1-03** | **Verify every migration is mirrored in `db/init_db.sql`. ✅ Proven (2026-11-09).** `db/migrate.py diff` compared `madden_test` against a fresh `init_db.sql` build object by object (tables/columns/types/defaults, views, indexes, constraints, sequences, functions, triggers): identical. It found two real drifts, now closed by `2027_02_09_init_db_parity.sql` (`napper_leagues`/`napper_drafts.season` NOT NULL; `gm_profiles.start_season >= 1970`), and the seven newest migrations replay as no-ops on a fresh build (`tests/test_migrate.py`). | A fresh DB from `init_db.sql` diffed against a fully-migrated DB shows no difference. | M |
| ☑ | **P1-04** | **Apply migrations to a franchise database. ✅ Done (2026-11-09).** `madden_mu2026` was created and seeded from `CAREER-MUBASE` by `watch_save --once --auto-init --franchise-name mu2026 --no-engines --no-articles --no-league-office` (no user team; LeagueID 1287775425 registered and active; league state 2026 week 1, 2,847 players, 208 public tables). `db.migrate status` reports **128 applied, 0 pending, 0 drifted** (the 128th is `2027_02_13_gm_voice_cards`, applied 2026-11-09) — every file sourced from `init_db.sql` — and `db.migrate apply` is a no-op. `personality.features show` runs and reports all 20 features `off`. Resolves follow-ups #12, #17, #24, #29 and the six-feature activation gate. | A franchise DB has every table; `personality.features show` runs against it. | S |

### 3.2 Held and unwired units

| ☐ | ID | Item | Done when | Size |
|---|---|---|---|---|
| ☑ | **P1-05** | **Agent loops U3 wiring** (follow-up #21). ✅ Built (2026-11-09) as U3a + U3b: league office coach charts and CPU contract counters (`counter_chooser` seam), inbox `CONTRACT`/`ROLE_TALK` replies, social desk `PLAYER_STATEMENT` posts, and the `coach_loop`/`player_loop` switchboard features (installed, off by default). `tests/test_agent_loops_wiring.py` (16), `tests/test_agent_loops_statements.py` (11) and `tests/test_agent_loops_promises_wiring.py` are green. The coach-promise watcher (`coach.promise_options`/`decide_promise`/`review_promises`) and the CPU contract marquee floor followed in `AGENT_LOOPS_FOLLOWUPS_PLAN` (2026-11-09). | `tests/test_agent_loops_wiring.py` green; a shadow run records CHART / CONTRACT / LEAK decisions. | L |
| ☑ | **P1-06** | **Agent Wire U3 planner** (follow-up #22). ✅ Built (2026-11-09): U3 was reworked onto the trade-talks `newsroom/social_desk.py` and merged; the whole plan (U1–U5) is on main, so `week_mix`, league topics, `PICKS`/`HOT_TAKE`/`SEASON_CALL`, structured output, graded picks/calls, relationship wiring and the quality report are all in. **This is the item that makes tweets stop reading like captioned box scores.** | `python3 -m newsroom.social_desk --season 2035 --week 12` prints a plan; `tools/social_quality_report` reproduces the 2036 archive baseline (553/317) — reproducing or beating the live target waits for a live season (P8-02). | L |
| ☑ | **P1-07** | **Retirement restore authority (G4)** (follow-up #23). ✅ Done (2026-11-09). The PC procedure is in [`docs/reference/RETIREMENT_G4_RUNBOOK.md`](reference/RETIREMENT_G4_RUNBOOK.md); the approved §5.2 proof is `verify_output/retirement_g4/proof_draft.json` (the name is historical — `load_proof` accepts it). **Sessions 1–2 done (2026-11-09) on `CAREER-TUNE-RETIRE` with CPU teams:** all 10 stops (plus preseason weeks 2–3 and 2027 week 1) saved and read, census at `verify_output/retirement_g4/census.json`. It found 78 `Retired` already at `OFFSEASON_START`, **12 new retirees at `FREE_AGENCY` wave 1** (each with exactly 1 contract year left and a contract row Madden deletes), **no rostered player vanished without first showing `Retired`**, and the purge of 88/90 `Retired` rows entering the preseason (Kelce and Rodgers persist). **Session 3 run (2026-11-09):** 9 players restored through the planner + `save_writer` (`CAREER-TUNE-G4-START/FAW1/UDFA`, tool `tools/retirement_g4_fixture.py`), JR advanced 7 stops. Madden holds each `Signed` on his old team through the next stops and keeps the late restores through the preseason purge — **but creates no `PlayerContract` row or roster/depth entry, and deletes 2 of the 3 UDFA restores at the cutdown advance**. **A signed variant of the same branch (`CAREER-TUNE-G4SIGN-UDFA`: FreeAgent → sign with the remaining 1-year contract) survives all three advances with the contract, roster entry and a Madden-populated depth slot.** The window restore must therefore be a signing, not a field edit. **Signing restore wired 2026-11-09** (`RETIREMENT_WINDOW_SIGNING_RESTORE_PLAN`, merges `6e1120a` + `802e4cd`): reader v7 exports roster/PS/free-agent lists, the window and the proven decision-stage keep write `FreeAgent`/32, and the next run's `step_restore_retirees` signs the player back with his stored contract. Rerun done 2026-11-09 (free-agent flip → one advance → sign-back, reader v7): all 9 restored, 8 signed at the next run and Woods via the cut-down retry; `OFFSEASON_START`, `FREE_AGENCY` and `UDFA` pass (`CUTDOWN` not claimed). **Approved by JR 2026-11-09** (`approved_by: JR`, `approved_on: 2026-11-09`); `load_proof` accepts it and `MU_RETIREMENT_AUTHORITY` enables restores at the three stages. `CUTDOWN` restores stay observe-only (not claimed), and the `retirement` feature itself stays off until P6. | `docs/reference/RETIREMENT_AUTHORITY.md` §5 proof file exists; G4 passes. | M |
| ☑ | **P1-08** | **Free-agency contract-row creation** (follow-up #24, V2 §7.3 item 6). ✅ Built (2026-11-09). `pipeline/save_reader/sign.mjs` fills one of the `PlayerContract` table's empty rows when no released row is free; `save_writer.mjs` opens the save with `autoUnempty` so the terms turn the empty row live, and the league office no longer defers an accepted offer without a matching release (`tests/test_league_office.py`). Verified on a copy of the sample save: the signing lands, reads back with exactly one holder, and the source sha256 is unchanged (`pipeline/save_reader/test/sign.test.mjs`). | A signing with no release lands and reads back. | L |
| ☑ | **P1-09** | **Inbox latency spike + `llm_compare` gate** (follow-up #25). ✅ Done (2026-11-09). 20× `make_llm("inbox")` on a filled CONTRACT prompt with Madden open: p50 **1.99 s**, p95 **2.46 s**, max 2.50 s → `INBOX_LLM_BUDGET_S` **8.0 → 4.0 s**. `tools/llm_compare` gained `inbox_reply` and `inbox_appraisal` (built from the real `voice.prompt()`); the gate exposed a money-format veto in `validate_rationale` (50% first run), fixed and re-run at **100% (4/4)**. Report: [`docs/reference/P1_LATENCY_AND_INBOX_GATE.md`](reference/P1_LATENCY_AND_INBOX_GATE.md). | Constants set from measurement; report filed. | M |
| ☑ | **P1-10** | **Press cloud latency + constants re-check** (follow-up #27). ✅ Done (2026-11-09). Corpus re-checked (175 transcripts, mean 11.95 questions/presser; loss rooms longer): `BASE_PATIENCE` 14 and no loss penalty still hold, unchanged. One warm cloud presser on a scratch clone: **83 calls, 278,679 in / 9,200 out tokens, $0.0474, 111 s**, per-call p50 **2.25 s** / p95 **3.32 s**, against §2.10's ~75 calls, ~280k input, $0.05–0.06, 1.5–2 min. Report: [`docs/reference/P1_LATENCY_AND_INBOX_GATE.md`](reference/P1_LATENCY_AND_INBOX_GATE.md). | One warm cloud round measured; report filed. | M |
| ◐ | **P1-11** | **Scouting-director wiring** (follow-up #17). **Wired; inert until a reviewed `staff:scout` receipt exists (2026-11-09).** `scouting_engine.reviewed_scout_context` loads the receipt and cutoff from the franchise's applied lifecycle batch, and `catch_up`/`step_scouting`, the Draft Room and the CLI all pass it to `run`; with no batch it returns None, logs once and behaves exactly as before. `madden_mu2026` has no lifecycle batch yet, so the directors stay off until JR runs the review (steps in `docs/FOLLOW_UPS.md`). Nothing is fabricated. `scouting_engine.run(..., personality=)` creates each team's director through the reviewed `staff:scout` lifecycle context when `scout_directors` is active, leans the team board (`scouting_boards.board(extra_bias=)`) and writes the team's private report rows (`scout_reports.team_id/scout_id/voice`, grade = consensus + the bounded bias); migration `2027_02_11_scout_report_team_rows.sql` moves the PK to two partial unique indexes. The department row and the consensus never move (`tests/test_scout_director_wiring.py`). | Scout reports carry `scout_id` + `voice`; board lean moves with the director. | M |
| ☑ | **P1-12** | **Voter article alignment** (follow-up #20). ✅ Built (2026-11-09). With `media_voters` active, `swarm_dispatcher._ballot_brief` runs the ballots before the Week 19/22 articles, appends `all_pro_facts`/`results_facts` to the writer's prompt, checks the article against the stored result and gives it one rewrite on a miss; the storage step no longer re-runs the ballots (`tests/test_voter_article_alignment.py`). | An active `media_voters` run never names a winner the ballot didn't. | M |
| ☑ | **P1-13** | **Interview summary voice** (follow-up #19). ✅ Built (2026-11-09). `voice.compose` honours a kind's optional `intent_voice(intent)`; the PROSPECT kind uses it so the LLM-on closing summary is the department scout's third-person memo (`tests/test_inbox_voice.py`, `tests/test_inbox_kinds_prospect_scout.py`). | LLM-on summary reads third-person scout. | S |
| ☑ | **P1-14** | **SCOUT risers needs-proxy** (follow-up #18). ✅ Decided (2026-11-09): the proxy is **accepted in writing**. Board membership already encodes the GM's needs-weighted `fit`; a strict filter on `gm_team_seasons.needs` positions would make the scouting inbox read a non-scouting table for a five-name line. Revisit only if the line reads wrong on a live board. | Decision recorded; behaviour matches it. | S |
| ☑ | **P1-15** | **`OPEN_PRESSER` navigation** (follow-up #30). ✅ Built (2026-11-09). `InboxModule.tsx` resolves the thread's `link_table` to a page ("Go to Game Center" for `press_conferences`) and shows that link in the conversation; `OPEN_PRESSER` records the act and then navigates to `#franchise-game-center`. The Depth Chart link stays. | Clicking the notice lands on the Game Center. | S |
| ☑ | **P1-16** | **Carried-talk flag persistence** (follow-up #36). ✅ Built (2026-11-09). `gm_trade_negotiations.flags TEXT[]` (`2027_02_12_trade_talk_flags.sql`) stores the stored-path `shape:`/`stage:`/`release:` flags; `open_talk` writes them and `_talk` reads them, so a carried talk's eventual proposal keeps them (`tests/test_gm_talks.py`). | A carried blockbuster still counts against `MAX_BLOCKBUSTERS`. | M |
| ☑ | **P1-17** | **Carried-talk team accounting** (follow-up #37). ✅ Built (2026-11-09). `gm_talks.resume_talks` is split out of `run_talks`; `gm_trade_engine.run_week` runs it before the search and seeds `used`, so a carrying team cannot open a second deal that week (`tests/test_gm_trade_engine.py`). | A carrying team cannot negotiate two new deals in one week. | S |

### 3.3 Producers with no caller

Five engines exist, are tested, and **nothing calls them**. Under the "nothing left undone" rule each gets a
league-office step (gated, warning-only, like `compensatory_picks_run.py`).

| ☐ | ID | Item | Done when | Size |
|---|---|---|---|---|
| ☑ | **P1-18** | **Comp-pick producer** (follow-up #38). ✅ Wired (2026-11-09): `pipeline.league_office.annual_offseason_steps` runs `compensatory_picks_run.award_and_persist(cur, season, season+1)` after a WRITTEN FREE_AGENCY wave 1 write, before DRAFT (`tests/test_league_office_annual.py`). | Comp picks appear without a manual run. | M |
| ☑ | **P1-19** | **Comp-pick article scheduling** (follow-up #39). ✅ Wired (2026-11-09): the same annual steps publish `newsroom.compensatory_picks_article` for the class at week 26, after the producer commits. | The announcement publishes by itself. | M |
| ☑ | **P1-20** | **Report-card producer** (follow-up #41). ✅ Built (2026-11-09): `engines/report_card_run.annual` carries last season's card or the baseline, completes renovations, degrades, applies OpEx, and files `team_report_cards` + `owner_cash_ledgers` (staff payroll deducted) at FREE_AGENCY (`tests/test_report_card_run.py`). | `team_report_cards` and `owner_cash_ledgers` fill each season. | M |
| ☑ | **P1-21** | **Report-card article scheduling** (follow-up #42). ✅ Wired (2026-11-09): the annual steps publish `newsroom.report_card_article` at FREE_AGENCY (week 26), after the cards commit. | The exposé publishes by itself. | S |
| ☑ | **P1-22** | **Staff-contract producer** (follow-up #46). ✅ Built (2026-11-09): `engines/staff_contracts_run.annual` files each current HC/OC/DC at the market salary, rolls `remaining_years` deterministically, moves a fired staffer's remaining guaranteed salary into `buyout_owed`, and the report-card ledger deducts the payroll (`tests/test_staff_contracts_run.py`). | Staff payroll deducts from owner cash; buyouts and expiry happen. | L |
| ☑ | **P1-23** | **CPU owner facility-upgrade AI** (follow-up #44). ✅ Built (2026-11-09): `engines/owner_policy.facility_style` / `facility_upgrade_orders` (aggressive/balanced/frugal) is applied to CPU teams by the report-card producer; JR's teams never order their own (`tests/test_report_card_run.py`). | CPU owners upgrade facilities on their own policy. | M |

### 3.4 Correctness items that would poison a tuning league

| ☐ | ID | Item | Done when | Size |
|---|---|---|---|---|
| ☑ | **P1-24** | **`watch_save` must skip `CAREER-TUNE-*` saves. ✅ Built (2026-11-09).** A clone keeps the real franchise's `LeagueID`, so the watcher would ingest a tuning season into the real franchise database. `find_save_files` now skips `CAREER-TUNE-*` (directory scans and a directly named file), `--allow-tune-save` (or a `--dsn` naming a `madden_tune_*` database, which is P2-04's deliberate tuning path) opts in, and `process_save` refuses a tuning clone whose destination is any other database as defence in depth. `--pattern "CAREER-MU*"` remains the primary isolation. | `tests/test_watch_save` covers the refusal; the watcher ignores a tuning save. | S |
| ☑ | **P1-25** | **Trade-talks replay + transcript review** (follow-up #34). ✅ Done (2026-11-09; JR approved the transcripts). The pre-unilateral sample (7 talks, all AGREED in 2 turns) exposed bilateral pre-screening; [`UNILATERAL_TRADE_ENGINE_PLAN.md`](done/UNILATERAL_TRADE_ENGINE_PLAN.md) fixed the mechanics (24 talks: 9/9/6, 62.5% breakdown) and [`GM_TALK_VOICE_PLAN.md`](done/GM_TALK_VOICE_PLAN.md) fixed the writing (per-GM speech cards, fact sheet v2, writer-only turns). The post-build replay on `madden_2036` weeks 4–8 produced **26 talks — 9 AGREED, 9 STALLED, 8 WALKED_AWAY (65.4% breakdown), 2 carried, 9 leaks**, with **75 model lines / 27 template fallbacks (73.5% model)**; JR read and approved the agreed, stalled and walked-away transcripts in `verify_output/p1_25/voice2/`. The convergence gap against `TALK_STALL 0.25` and the template-fallback share were retuned in P3-08 (post-unilateral re-measure, 2026-11-12): the 2036 weeks 4–8 replay measured a 64.0% breakdown and an 8.1% template share, so `TALK_STALL` is now 0.64 — see [`TUNING_LEDGER.md`](reference/TUNING_LEDGER.md). | Convergence rate matches the assumption; transcripts approved. | M |
| ☑ | **P1-26** | **Retirement rows at the season boundary.** ✅ Built (2026-11-09): `pipeline/load_save.load_retirements` reads every Player row the save exports with `ContractStatus = Retired`, writes one `player_retirements` row per `(season, player_id)` and sets `players.retired_season` (a later roster row or signing clears it); the season ledger now labels those departures `RETIRED`. `SEASON_CONTINUITY_PLAN` phase 2b is built and the plan moved to `docs/done/` (`tests/test_load_retirements.py`). | A season rollover writes `player_retirements` + `players.retired_season`. | M |
| ☑ | **P1-27** | **Godfather trigger gates** (follow-up #47), part 1. ✅ Built (2026-11-09): carousel candidates carry `drive`/`ego` (`candidate_sliders`: lifecycle `db-coach`/`db-coordinator` profiles or legacy coach personas; `ego` = the confidence slider), so the `drive`/`ego ≥ 8` stature branch fires; and the GM hot seat runs **before** the coaching carousel (`gm_rollover.hot_seat_only` from `weekly_capture.step_gm_hot_seat`, Super-Bowl-gated and idempotent), so a genuine same-cycle HC+GM sweep is a dual vacancy (`tests/test_godfather_gates.py`). | The trigger can fire beyond its rarity gates in a test. | M |

### 3.5 Plan disposition — what leaves `in_progress/`

Under "wrap up all in-progress work", each open plan needs a terminal state. (Never-started V3/dossier work is
checkpoint 2 and is handled in P8, not here.)

| Plan | Closes when | Moves to |
|---|---|---|
| `COACH_PLAYER_AGENT_LOOPS_PLAN.md` | ✅ P1-05 landed 2026-11-09; the promise watcher and marquee floor followed the same day (`AGENT_LOOPS_FOLLOWUPS_PLAN`) | `done/` |
| `AGENT_WIRE_VOICE_PLAN.md` | P1-06 lands | `done/` |
| `FRONT_OFFICE_INBOX_PLAN.md` (in `done/`, still listed) | P1-09 + P8-02 land | remove from FOLLOW_UPS |
| `PERSONALITY_PLAN.md` | P6 activates every feature | `done/` |
| `PROGRESSION_PLAN.md` | P3 signs off the constants and P6 applies them | `done/` |
| `SEASON_CONTINUITY_PLAN.md` | ✅ P1-26 landed 2026-11-09 | `done/` |
| `V2_MASTER_PLAN.md` | P7 starts the franchise; residual items move to this checklist | `done/` |
| `FRESH_SAVE_STRATEGY_NOTES.md` | nothing left; it is a correction note | `done/` or delete |
| `V3_STUDIO_PLAN.md`, `V3_DOSSIER_EVALUATION.md`, `DYNAMIC_PRESENTATION_*` | P8 | stay `in_progress/` (checkpoint 2) |

### 3.6 Execution waves for P1

P1 is 27 items; they are **not** all one job. Waves exist so a session can take a coherent slice and
leave the rest untouched. Items inside a wave touch different files and can run in parallel; a wave is done
when every item's "done when" holds and the relevant tests pass.

| Wave | Items | Why together | PC needed? |
|---|---|---|---|
| **1** | **P1-01, P1-02, P1-03** (migration state, ledger, `init_db.sql` parity) and **P1-24** (watcher `CAREER-TUNE-*` guard) and **P1-15** (`OPEN_PRESSER` navigation). **Done 2026-11-09.** | Local, self-contained, no Madden. P1-01/02/03 are one investigation of the schema machinery and should be one unit; P1-24 and P1-15 are independent single-file changes. Unblocks P1-04 and every tuning run. | No |
| **2** | **P1-05** (agent loops U3), **P1-06** (Agent Wire U3–U5). **Done 2026-11-09.** | The two units that actually unlock the checkpoints: without them, agent decisions and tweet quality cannot be tuned. Both are existing plans with file ownership already defined. | No |
| **3** | P1-08, P1-11, P1-12, P1-13, P1-14, P1-16, P1-17, P1-18, P1-19, P1-20, P1-21, P1-22, P1-23, P1-26, P1-27. **Done 2026-11-09.** | The long tail of wiring and producers. Independent of each other; several are small. | No |
| **4** | P1-07 (retirement G4), P1-09 (inbox latency), P1-10 (press cloud). **P1-09 and P1-10 done 2026-11-09;** P1-07 done 2026-11-09 (census, signing restore built in `6e1120a`/`802e4cd`, proof rerun and JR's approval). | Everything that needs the PC, Madden, or credentials. Batch into one PC session. | Yes |

**P1-04** (apply migrations to a franchise DB) is not in a wave: it waits for the franchise database, which is
created by seeding `CAREER-MUBASE` (P7-00b). **Done 2026-11-09:** `madden_mu2026` exists and is at head.

**P1-25** (trade-talks replay) left Wave 4 and is **done 2026-11-09**. The initial replay exposed the bilateral pre-screening; [`UNILATERAL_TRADE_ENGINE_PLAN.md`](done/UNILATERAL_TRADE_ENGINE_PLAN.md) fixed the mechanics and [`GM_TALK_VOICE_PLAN.md`](done/GM_TALK_VOICE_PLAN.md) fixed the writing. JR approved the three post-build transcripts; the `TALK_STALL`/volume retune was P3-08 (done 2026-11-12: 0.64, 8.1% template share).

**Wave 1 file ownership** (no path appears twice):

| Item | Owns |
|---|---|
| P1-01 / P1-02 / P1-03 | `db/` (migration application and ledger), `db/init_db.sql`, `tests/` for the migration tooling |
| P1-24 | `pipeline/watch_save.py`, its tests |
| P1-15 | `Mu_Network/src/components/franchise/InboxModule.tsx`, `Mu_Network/src/types/franchiseInbox.ts`, `engines/inbox/kinds/press.py` (the `OPEN_PRESSER` option and its act), its tests |

---

## 4. P2 — Tuning-league infrastructure

**Goal:** a disposable league JR is happy to throw away, driven by the same code paths as the real one, with
no ability to leak into the real franchise.

| ☐ | ID | Item | Done when | Size |
|---|---|---|---|---|
| ☑ | **P2-01** | **First live `sim-season`. ✅ Done (2026-11-10).** `CAREER-TUNE-P2A` simmed the whole 2026 regular season, one **FORCE ADVANCE** per week (the all-user league cannot use READY TO ADVANCE), across resumed runs: the save reads back **2026 WildcardPlayoff, week_index 18, 326 games all with scores, 3,238 players**. It then ingested into `madden_tune_p2a` (`league_state` 2026/19, 272 completed games, **0 `llm_usage`**), while `madden_mu2026` kept identical row counts. Getting here closed the real pilot gaps: DPI-aware full-screen capture, the Franchise landing page (Right → CREATE / LOAD), the Week 1 draft-class card (highlight, hold Q), FORCE ADVANCE + its SIM AHEAD confirmation, the nearest-label border reader, bye-week header fallback, year-less postseason headers, and the right-button abort. | One season sims end to end on `CAREER-TUNE-*`. | L |
| ☑ | **P2-02** | **Extend the pilot catalog for the offseason. ✅ Done (2026-11-10, `p2-resume`).** The catalog now holds **27 screens**; the last ten came from the live walk — `ADVANCE_LOCKED`, `LIVE_EVENT_FRENZY`, `LIVE_EVENT_FRENZY_MODAL`, `FREE_AGENCY_ENDED` (the free-agency live event and the post-draft UDFA event: LIVE EVENT tab → hold Escape → SIMULATE TO END OF FRENZY) and `DRAFT_HUB`, `DRAFT_LAUNCH` (draft week replaces GAMEDAY and WEEKLY HUB with a DRAFT tab; SIMULATE DRAFT is the only way past it). The run also forced four classification fixes, each with a test: `HUB_GAMEDAY` no longer matches the news ticker's "matchup", `HUB_OTHER` no longer needs a spelled-out COACH CENTRAL, the empty postseason hubs (`DIVISIONAL`, `CONFERENCE`, `CHAMPIONSHIP`) and their header words are catalogued, and the preseason stop is read from the save (`PRESEASON_<n>`) because the preseason hub header reads "2027. WEEK 1". **Proof:** `CAREER-TUNE-P2C` ran 2026 regular season → playoffs → eight offseason stages → preseason weeks 1-3 → cut day → 2027 week 1 with **no unknown screen**. The only uncatalogued screen left is pre-snap (`first_down_line`), listed in MADDEN_PILOT.md §7. | A full annual cycle (regular season → playoffs → offseason → preseason → week 1) sims without an unknown screen. | L |
| ☑ | **P2-03** | **Offseason stage handling.** The pilot must answer or avoid Madden's offseason prompts (re-sign decisions, draft, retirement) in a way that leaves the decisions to Project Mu. **Decided 2026-11-09: Mu drives every stage through the save writer; the pilot advances exactly one stop at a time and never sims through the offseason.** **Mechanism built and proved (2026-11-10, `p2-resume`):** `python3 -m tools.madden_pilot advance --save CAREER-TUNE-<run>` advances exactly one stop — one advance press, the stop read before and after, Madden's autosave copied over the named save, then a quit to the franchise list so the league office can write with the franchise closed — and `engines/tuning/season_loop.py` drives the loop (advance → `watch_save --once --dsn madden_tune_<run>` with `LEAGUE_OFFICE_FRANCHISE_CLOSED=1` → advance), recording every stop and ending on the first uncatalogued screen with its screenshot. **Proof:** `CAREER-TUNE-P2C` went Staff → Re-sign → FA prep → Free agency → Combine → Pro day → Draft → Recap/UDFA → preseason weeks → cut day → **2027 week 1**, the league office writing eight stages (RESIGN, FREE_AGENCY ×2, COMBINE, PRO_DAY, DRAFT, PRESEASON, CUTDOWN — all WRITTEN, 0 `llm_usage`) and the pilot loading each written save and skipping both live events and the draft sim itself. **Draft order (fixed 2026-11-10, `80237fb`):** the watcher locks the coming draft's order (`draft_room.lock_order`) before the DRAFT stage in a `madden_tune_*` database; `madden_tune_p2e` logged "Locked the 2027 draft order (224 picks)" and its DRAFT run wrote the picks, where `madden_tune_p2c` had logged "no draft order … no picks planned". **Draft order and progression (both fixed 2026-11-10):** the watcher locks the coming draft's order before the DRAFT stage in a `madden_tune_*` database (`madden_tune_p2f` logged "Locked the 2027 draft order (224 picks)" and wrote the picks), and the ingest that seeds a tuning database now applies the same neutral progression bootstrap `league_office setup` gives a live franchise — without it OFFSEASON_START and the next season's first regular week both refused with "explicit neutral bootstrap required". Proven on a scratch database: 2,847 players bootstrapped for 2026 and a REGULAR_WEEK dry run planned clean. ****What still blocks ☑ (superseded 2026-11-10, `p2-finish`):** the draft order and the progression bootstrap were both fixed and the full cycle ran again — `--run p2g` wrote **11 stages** (PLAYOFF_WEEK, OFFSEASON_START, RESIGN, FREE_AGENCY ×2, COMBINE, PRO_DAY, DRAFT, UDFA, PRESEASON, CUTDOWN, all WRITTEN) and a resumed walk took the save from the draft stop through UDFA, preseason weeks 1-3 and the cut day to **2027 regular-season week 1** (9.8 min). ✅ The 2027 week-1 REGULAR_WEEK plan then failed for a different reason: a drafted rookie (`player 2848`) has no progression state — `season_loop` bootstraps from the week-1 clone, and nobody bootstraps players who join later (open, P3-02's area). The bootstrap itself is proved: with the database seeded at the week-1 clone (2,847 players) OFFSEASON_START and PLAYOFF_WEEK both plan and WRITE, where every earlier run stopped with "explicit neutral bootstrap is required". The older note follows: at DRAFT the league office wrote **no picks** — `draft_picks.overall_pick` is 0 of 672 because `engines.draft_room.lock_order(cur, season + 1)` is a manual Draft Room action nobody performs in a tuning league; lock the order before the DRAFT ingest. Secondary: an offseason/preseason ingest leaves `league_state.current_week` at the seed week (the loader keeps the stored week when a save has none), so the stage is right and the league week is not (JR decided 2026-11-10 to leave it). **Newcomers fixed 2026-11-10 (`5cc41a6`, builder-4):** a player Mu has never tracked (no state, no event) seeds his neutral progression state at any ingest, at the league year the stage belongs to, so `madden_tune_p2g`'s 389 rookies no longer fail 2027 week 1 and the live league's first rookie class is covered; the P2G snapshot now heals all 389 at the next ingest. **Draft-order guard fixed 2026-11-10 (builder-4):** `lock_tuning_draft_order` counted *any* numbered pick as a locked order, and `compensatory_picks_run` inserts the comp picks numbered at the free-agency waves, so the 10 comps made it skip the lock; `madden_tune_p2g`'s DRAFT stage then signed only those 10 (224 standard picks NULL, no drafted cohort recorded). The guard now counts standard picks only (`standard_picks_locked`); filling `draft_prospects.actual_*` for a tuning draft is held for JR's Q3. | A season boundary completes under Mu's control. | XL |
| ☑ | **P2-04** | **Tuning database + routing. ✅ Proved (2026-11-09).** A clone keeps the live `LeagueID`, so `route_franchise` would send it to the live DB. Decided: **tuning clones get no registry entry** (the registry is keyed by LeagueID; a clone would collide) and are ingested manually with `--dsn madden_tune_*`; the LeagueID is stamped into `league_state` only, for the personality universe. `--dsn` gained `--auto-init`/`--reseed` for a `madden_tune_*` destination: it creates the database from `init_db.sql`, seeds it, stamps the ID and loads the clone; `--reseed --confirm-reseed <db>` is the reset. Proof: `CAREER-MU2026` → `CAREER-TUNE-P2A` ingested into `madden_tune_p2a` (2,847 players, 272 games, 0 `llm_usage`); `madden_mu2026` before and after is byte-for-byte the same size (34,499,931) with identical row counts. Recorded in [`reference/TUNING_LEAGUE.md`](reference/TUNING_LEAGUE.md). | A tuning season ingests into `madden_tune_*` with zero writes to the real DB. | M |
| ☑ | **P2-05** | **A tuning-run harness.** Generalise `engines/tuning/supersim_loop.iteration()` into: clone save → sim with pilot → ingest to tuning DB → run weekly engines + league office → produce a report. One command per simulated season. **Built (2026-11-10, `p2-resume`):** `python3 -m engines.tuning.season_loop --run <name>` clones a fresh pruned clone, sims the regular season and the postseason, ingests into `madden_tune_<name>` (weekly engines + league office, `LEAGUE_OFFICE_FRANCHISE_CLOSED=1`), walks the eight offseason stops, the preseason weeks and the cut day one stop at a time until an advance lands on the next regular-season week, and writes `data/tuning/seasons/<name>/season_<year>.md|.json` scored against the real-NFL targets; 16 unit tests drive a whole season through a fake runner. ****✅ Done 2026-11-10 (`p2-finish`):** `--run p2g` ran the whole thing live in 29.4 min — seed at the week-1 clone, 2026 regular season and postseason, snapshot, ingest, then the stage loop through DRAFT before a screen gap stopped it; a resumed walk finished the cycle to 2027 week 1. `season_2026.md` scores **257 of 398 stats inside the real 2015-2025 band** (score 0.183). The report of the earlier note follows: one live run — `--run p2c` resumes `CAREER-TUNE-P2C` at 2027 week 1 **First clean end-to-end run (2026-11-10):** `--run p2f` cloned, simmed the 2026 season and postseason, took the report snapshot, seeded `madden_tune_p2f`, walked all eleven stops to 2027 week 1, and wrote `season_2026.md` — **score 0.193, 252 of 398 stats inside the real band** — in 26 minutes. | `python3 -m engines.tuning.season_loop --run <name>` runs a season and writes a report. | L |
| ☑ | **P2-06** | **Reset and isolation policy. ✅ Written and proved (2026-11-09).** [`reference/TUNING_LEAGUE.md`](reference/TUNING_LEAGUE.md): `CAREER-MUBASE` (never simulated) and `CAREER-MU2026` (clone source only) vs `CAREER-TUNE-*`; where saves and databases live; retention (active clone + one predecessor); create, **reset** (`--reseed --confirm-reseed` on a `madden_tune_* --dsn`; proved by dropping and rebuilding `madden_tune_p2a`) and drop. | A written recipe in `docs/reference/`; a `--reset` path exists. | S |
| ☑ | **P2-07** | **Media off by default in tuning. ✅ Built and proved (2026-11-09).** `--no-articles` skips the slate and the press run; the watcher also sets `MU_NO_LLM=1` for every `madden_tune_*` ingest, under which `make_llm` returns `None` and `generate_text` raises `LLMDisabled`. An explicit `MU_NO_LLM=0` is the operator override. Proof: the P2-04 ingest ran the GM talks and scouting on templates with **0 rows in `llm_usage`**. | A tuning season runs with zero model calls. | S |
| ☑ | **P2-09** | **Re-baseline the tuning tooling. ✅ Operational defaults done (2026-11-09).** `jobs.BASELINE_SAVE`, `supersim_loop.BASE_SAVE`, both skills and the test fixtures now point at **`CAREER-MUBASE`**; the supersim measurement-first flow is written into `/tune`, `MADDEN_PILOT_AND_TUNING_PLAN` §2.4 and the acceptance doc §3. **Completed 2026-11-09:** `league_office setup` produced `CAREER-MU2026` (32 users, fill-roster off, morale 50, 2,847 players bootstrapped) and every path now points at it — `jobs.BASELINE_SAVE`, `supersim_loop.BASE_SAVE`, both skills and the test fixtures. The two-tier rule is collapsed to one name: `CAREER-MU2026` is the single clone source, and `CAREER-MUBASE` is kept only as the hash-pinned creation input in `data/baselines/`. | No tuning path references `CAREER-WEEK1`; sim/league-office clones come from `CAREER-MU2026` after setup. | S |
| ☑ | **P2-10** | **Week-by-week tuning seasons (added 2026-11-10, JR). ✅ Proved live 2026-11-10 (`p2-weekly`, board M-0332):** `season_loop --run p2h --resume --max-weeks 3` walked `CAREER-TUNE-P2H` from week 1 to week 4, with three `REGULAR_WEEK` runs WRITTEN, `league_state` 2026/4, 0 `llm_usage`, and `--resume` used for real after a stopped seeding ingest; each stop takes about 2.4 min (`progress.json`), so a full season adds about an hour. Commits `22db37f`, `f0e7bbd`, `e76995c`, `b348af5`, `936745d`. Week 1 is never planned (the seeding load can't run the league office). The original brief follows. `season_loop` sims the regular season in one `sim-season`, so the weekly league office never runs: `madden_tune_p2g` has 0 of 18 `REGULAR_WEEK` runs, and in-season trades, mid-season HC changes, depth charts, agent loops and the in-season progression checkpoints never happen (found by P3-07's report, `data/tuning/seasons/p2g/decisions_2026.md`). Walk the regular season one `tools.madden_pilot advance` at a time with the league office writing after each week; keep the one-shot sim behind a flag for stat-only work. Handed to `p2-weekly` (board M-0293). Blocks P3-05, P3-06a and every P5 shadow season. | A live run shows one `REGULAR_WEEK` WRITTEN per simmed week, `--resume` restarts mid-season, and the per-week wall time is recorded. | M |

---

## 5. P3 — Offline tuning (deterministic engines)

**No Madden required.** This is where progression, career length and development curves get their numbers.

### 5.1 Progression and career length

| ☐ | ID | Item | Done when | Size |
|---|---|---|---|---|
| ☑ | **P3-01** | **Build the 2015–2025 targets. ✅ Current (2026-11-10):** rebuilt 13:53 that day. `python3 -m engines.tuning.targets` → `data/tuning/targets_2015_2025.json` (git-ignored, ~5 s once nflverse is cached). | Targets file exists and is current. | S |
| ☑ | **P3-02** | **Run the progression fit. ✅ Done 2026-11-10 (three seeds, `data/tuning/progression_fit_seed{0,1,2}.md`; board M-0311, M-0327).** On the `CAREER-MU2026` snapshot, 5 seasons, budget 200: seed 0 0.9804 → 0.9399, seed 1 0.9856 → 0.9234, seed 2 0.9488 → 0.8887. **The only stable recommendation is `AGE_BAND_OFFSET[0]` (≤ 23) −0.86 → −0.66, on all three seeds.** `[3]` (+0.2, +0.2, −0.2) and `[2]` (−0.2, +0.2, 0) disagree between seeds, and every other move came from a single seed. **Nothing is applied; applying `[0]` is JR's call.** Caveat (P3-04): the fit's replay holds the snapshot's own league shape, but the live engine targets season 2036, which a 2026 league doesn't have. So the recommendation describes the engine as designed, and the fit should be re-run once that is fixed. **Current (2026-11-12):** the fix landed (`46d1716`, merged `c82f13b`, spec `fe0c44e`) and was audited PASS (R8), but a fresh 5-season fit against the corrected full-roster engine has not been run, so this table is pre-fix. The original brief: `/tune progression --seasons 5 --budget 200 --seed 0`, then `--seed 1` to confirm the recommendation holds. **Never apply what it recommends** — present the numbers. | `data/tuning/progression_fit.md` with before/after score and ranked constants. | M |
| ◐ | **P3-03** | **Career-length targets. Real half done (2026-11-08):** the per-position table (all / drafted / undrafted) is in `data/tuning/decision_targets.md` and `TUNING_ACCEPTANCE_THRESHOLDS.md` A1, built by P3-06. Comparing Mu's output waits for P5 and needs drafted players linked to their picks (board M-0272). Measure real 2015–2025 career lengths by position from `data/history/nfl_careers_through_2025.csv` (mind the survivor bias already documented: 1,713 of 1,886 careers are still active). Compare with Mu's retirement output at the end of P5. | A table of real median/quartile career length per position; Mu's output sits inside it. | M |
| ☑ | **P3-04** | **Development-curve targets. ✅ Verdict 2026-11-11: [`PROGRESSION_DEVELOPMENT_VERDICT.md`](reference/PROGRESSION_DEVELOPMENT_VERDICT.md).** The design is accepted: from the 2026 league the development term is +0.5 a year (+1.1 over a rookie's first three years, so a 68–72 rookie gains +0.8 a year against Madden's ~+1). The "+5–6 a year" figure was the 2036 league's. **Follow-up fixed 2026-11-12:** the live engine's target was season 2036 (`LEAGUE_SHAPE_SEASON`), which a 2026 database doesn't have, so the term calibrated to an empty league (development 0, star −1; seen in `madden_tune_p2g` 2027). JR ruled 2026-11-11 that a 2026 league holds its own start season's shape and that the projection models leavers and the coming draft class. Builder-10 implemented the full-roster `league_shape_pool` (`46d1716`, merged `c82f13b`, spec `fe0c44e`) and the R8 audit passed; re-run P3-02 against it and watch the term in P5 (revisit if it goes above +1.5 a year). The original brief follows. The fit scores against the snapshot's own shape and the careers CSV. Extract what "a 68-OVR rookie gains ~5–6 a year under the league term" means against real rookie-to-year-3 arcs; flag if the league-shape term is doing work that real careers don't justify. | A short written verdict; either accept or open a follow-up to revisit the league term. | M |
| ☐ | **P3-05** | **Retirement baseline.** With P1-07 (G4) and P1-26 (retirement rows), calibrate the retirement policy's base rate against P3-03. Modifiers stay off. | Base-retirement age distribution matches real NFL within the P0-2 thresholds. | L |

### 5.2 Decision targets — **missing today**

`engines/tuning/targets.py` builds **player stat** targets only. Checkpoint 1 is about *decisions*, and there
is currently no yardstick for them. This is new work.

| ☐ | ID | Item | Done when | Size |
|---|---|---|---|---|
| ☑ | **P3-06** | **Build `engines/tuning/decision_targets.py`. ✅ Built and audited 2026-11-08.** Career length is measured from **season rosters** (active statuses, CUT-only excluded), which reproduces the quoted ~3-year average (3.40 including cut-only players) and shows the drafted cohort averages ~6; trades are split **in-season (5–21/season) vs offseason (45–84)**. Also: retirement age by population, HC changes, team movement, draft by round, rookie usage. `tests/test_decision_targets.py` covers the helpers; the audit is written up in `TUNING_ACCEPTANCE_THRESHOLDS.md` §0. **Remaining:** the curated items with no open dataset (GM changes, coordinator changes, blockbuster rate, holdouts) — §4 there. | Module runs; tolerances signed off (P0-2). | L |
| ☐ | **P3-06a** | **Measure Mu's own player inflow.** Career length is arithmetically bounded by `roster slots / new players per season`; a 224-pick class with ~2,200 slots implies ~10 years before the retirement engine is consulted. Count drafted + generated UDFA players entering per simulated season, then set the A1 gate against the population Mu actually produces. **Without this the career-length gate is a guess.** | Inflow measured; population chosen (drafted vs all-comers) and recorded. | M |
| ◐ | **P3-07** | **Wire a decision comparison report** (`compare_decisions(observed, targets)`) so a shadow season produces a scored report next to the stat report. **Built (`4f6cc3e`, `7d7a4bc`) and audited; the P3-07 findings F1, F3 and F6–F10 are fixed.** A3 (between seasons), A4 (in-season trades) and A5 (team movement) are scored on the mean of three consecutive simulated seasons with each season also inside its band; a season with no completed games, no hiring cycle or a cohort below 300 players is NOT MEASURED rather than a pass; A3.midseason is report-only (no ✓/✗); A4 is 3–25 in each season and 5–21 on the mean. The Mu-side extractors are named in `TUNING_ACCEPTANCE_THRESHOLDS.md` §2 (e.g. `gm_trade_proposals` APPROVED, `player_seasons` team changes with ≥8 games both seasons, `coach_tenures`). **Still held:** F4 (A6 "started season") and F5 (A1/A2 ended-career cohort) — both measure a different population and are not built in this pass, so the item is not fully done. | A shadow season prints "HC changes 7 vs real 4–10 ✓ / in-season trades 13 vs real 5–21 ✓ / …" on a three-season mean. | M |
| ☑ | **P3-08** | **Post-unilateral trade flow re-measure.** ✅ Done (2026-11-12; the config is measured and reproducible — audit verdict still owed). `UNILATERAL_TRADE_ENGINE_PLAN` and `GM_TALK_VOICE_PLAN` changed how CPU trade talks run, so the pre-build numbers are void. Replayed weeks 4–8 on a fresh clone of `madden_2036` (`mu_scratch_p3_trade_remeasure_test`), **executing** each week's agreed deals through `front_office._execute` so the pacing sees them: **25 talks — 9 AGREED, 16 STALLED, 0 WALKED_AWAY (64.0% breakdown), 5 ANCHOR / 20 FIRM openings, 13 counters, 2 leaks; 9 executed in-season trades in the window (≈11 a season, inside 5–21); writer 57 LLM / 5 TEMPLATE (8.1% fallback, under the 10% target)**. `TALK_STALL` retuned **0.25 → 0.64**; `IN_SEASON_TARGET`/`MAX_NEW_PER_WEEK` unchanged (the window was supply-bound). The replay also found and fixed a talk-flow defect: after a REJECT the package's author could Accept its own rejected offer, executing it (6 of 17 agreements before the fix). Values and the reproduction recipe in [`TUNING_LEDGER.md`](reference/TUNING_LEDGER.md); fixture `tests/fixtures/trade_flow_2036_weeks4_8.json`; tool `tools/trade_flow_measure.py`. Residual: every stored deal was a SWAP (no RENTAL was proposed), so `SHAPE_MIX`'s rental-heavy mix is still unmeasured. | A measured, reproducible config whose agreed-trade rate sits inside the real band. | M |

### 5.3 Non-personality decision constants

These are already built and have never been run against a real league. They are tuned in P5 (they need league
context), but the constants to look at should be listed here so they are not forgotten:

`free_agency_engine` (COUNTER_STEP, COUNTER_MAX_RAISE, bar), `trade_value` (surplus curves), `gm_trade_engine`
(`--talk-stall`, blockbuster cap, weekly volume), `depth_chart_engine` (`close_score_window`),
`coach_hot_seat`, `coaching_carousel`, `contracts_engine` (market worth, dead money), salary-cap growth.

### 5.4 Acceptance thresholds (P0-2) — drafted, awaiting sign-off

Full table: [`reference/TUNING_ACCEPTANCE_THRESHOLDS.md`](reference/TUNING_ACCEPTANCE_THRESHOLDS.md).
Computed 2015–2025; re-runnable with `python3 -m engines.tuning.decision_targets`.

| Domain | Gate | Real band (2015–2025) |
|---|---|---|
| Progression | Backtest MAE on 2035→2036 | ≤ 1.15 (current 1.134) |
| Progression | 5-season 90+ count vs 2036 shape | within ±5% of 144 |
| Progression | Mean OVR vs 78.13 | within ±0.3 |
| Career length | Median by position — **drafted cohort (decided 2026-11-08)** | median 5–6 across groups (QB/TE/OL/DL/K/P 6, RB/WR/LB/DB 5); ±1 (±1.5 QB/K/P); all-comers mean 4.36 reported as the floor. Revisit after P3-06a |
| Retirement | Median age by position — **drafted cohort** | QB 29.1, RB 27.1, WR 27.2, TE 28.8, OL 29.1, DL 28.5, LB 27.7, DB 28.1, K 30.8; ±1.5 |
| Coaching | HC changes between seasons | 4–10 (median 7) |
| Coaching | Mid-season HC changes | 0–3 (median 2) |
| Trades | **In-season** trades/season (Week 1 → season end; cutdown = preseason, decided 2026-11-08) | **5–21 (median 13)**; offseason 45–84 |
| Movement | Established players changing teams | 17.9–26.5% (median 24.9%) |
| Draft | Round-1 picks with 2+ started seasons | 85% ±10 pts; monotonic decline by round |
| Rookies | Share of drafted skill rookies with 8+ games in year 1 | 56% ±10 pts |
| Stats (supersim) | `compare` score, worst-10 stats | score ≤ baseline; no stat > 2 band widths out |

---

## 6. P4 — Madden supersim: measure Sabos first, tune only if it fails

**Premise (JR, 2026-11-09): do not start from vanilla.** Sabos FranchiseMod 1.3 ships **157 supersim tuning
assets** (`football/attribsys/data/supersim_*`: compound abilities, tackle chances, injury, fatigue, play time,
run/pass/punt/FG play), described as tuned to the last three real seasons, and it is **always-on in `Mu Base`**.
So the baseline is already a tuned league. The job is to *measure* it against the 2015–2025 targets and only
tune if it misses — **using Sabos's values as the starting point, never vanilla.**

| ☐ | ID | Item | Done when | Size |
|---|---|---|---|---|
| ☐ | **P4-01** | **Baseline the installed game (new first step).** Sim one full season on a `CAREER-TUNE-*` clone of the canonical base with `Mu Base` active, read it with `pc_save_reader`, and score it with `engines/tuning/compare` against `targets_2015_2025.json`. Record the score and the worst-10 stats. **This is the go/no-go for all of P4.** | A written baseline report: overall score, stats inside/outside the band, worst 10. | M |
| ☐ | **P4-02** | **Pass → stop.** If the baseline meets the §3 gates (`TUNING_ACCEPTANCE_THRESHOLDS.md`: overall ≤ baseline, no stat more than 2 band widths out), **record "Sabos used as-is; supersim not tuned"** and skip P4-03…P4-05. This is a legitimate completion of P4, not a shortcut. Re-measure once on the canonical league after P7 (P4-06). | A signed-off "no tuning needed" entry in the tuning ledger with the report attached. | S |
| ☐ | **P4-03** | **Fail → verify EBX access.** `MmcHarness.exe info` → `ebx_readable: true`, `key_source: editor_builtin`. Never search for or download the key. | Info returns true. | S |
| ☐ | **P4-04** | **Fail → build a narrow space from the worst stats.** Determine whether `ebx-dump` returns the **installed (Sabos) values or vanilla** — this decides the whole approach. Then write `space.json` covering only the few fields that plausibly move the worst stats, with `start` = **the running Sabos value**, not vanilla. Show JR the list first. | A reviewed `space.json` whose `start` values come from the installed game. | L |
| ☐ | **P4-05** | **Measure noise first.** Two baseline seasons on the same clone; compare scores. Decide `--repeats 2|3`. | Noise floor known. | M |
| ☐ | **P4-06** | **Run the loop** (`/tune supersim <run>`) if P4-04 was needed. Each iteration ≈ 20–30 min of PC time. Stop at the loop's stop rule or JR's call. | A converged run with a logged history, or a recorded decision to keep Sabos. | XL |
| ☐ | **P4-07** | **Install accepted deltas (only if P4-06 ran).** Tuning mods go into `Mu Base` only after acceptance. If P4-02 passed, nothing to install — Sabos is already there. | `Mu Base` carries the accepted sim values, or is unchanged with the decision recorded. | M |
| ☐ | **P4-08** | **Re-run the §5.4 save checks on the modded save** (V2 §7.3 item 3). Every original check was on unmodded `CAREER-WEEK1`; sim and roster mods change the numbers. | The §5.4 checklist passes on the modded save. | M |

---

## 7. P5 — Shadow seasons: the decision engines

This is the heart of checkpoint 1. Each of the 20 switchboard features runs a full simulated season in
`shadow` (observe only), produces a report, gets its coefficients adjusted, and repeats until it passes.
Order is fixed by `personality/features.py` `FEATURES` (the D2 go-live order).

```
producers → relationships → profiles → evolution → focus_refresh
→ contracts_ambition → contracts_relationships → development_learning
→ trade_requests → retirement → development_opportunity
→ coach_loop → player_loop → owner_patience → owner_hiring
→ coaching_tree_hiring → owner_spending → owner_meddling
→ scout_directors → media_voters
```

### 7.1 The per-feature loop

For each feature:

1. `python3 -m personality.features set <FEATURE> shadow --version <v> --review "..."` on the **tuning DB**.
2. Run one full simulated season (P2-05).
3. Read the feature's report; compare against the decision targets (P3-06).
4. Adjust the feature's constants; bump `<version>`; repeat.
5. When it passes, leave it in `shadow` at the passing version. Clearance is exported in P6.

**Rule of thumb:** a feature that cannot be measured by a number is not ready to activate. If a report doesn't
exist, build the report before tuning the feature.

### 7.2 What "looks like the NFL" means per feature

| Feature | Report / signal to read | Real-NFL comparator |
|---|---|---|
| `producers` | Event volume, unresolved-identity queue, per-team coverage | n/a — must be complete, no missing identities |
| `relationships` | Trust distribution, decay rate, runaway rows | n/a — no writer/agent ends at ±1 with everyone |
| `profiles` | Inventory applied for all 2026 players + each draft class | 100% coverage, no `unknown` origin on an active player |
| `evolution` | Changes per agent per season | ≤ 1 (D6); distribution concentrated in event-driven cases |
| `focus_refresh` | Focus distribution, churn | plausible spread; not everyone "Financial" |
| `contracts_ambition` | FA acceptance/rejection rates, top-of-market deals, hometown/contender discounts | P3-06 FA band |
| `contracts_relationships` | Same, with attachment/grudge active | no measurable change unless the relationship is real |
| `development_learning` | Per-player progression delta with vs without personality | capped at ±1.5 OVR/season; league shape unchanged |
| `trade_requests` | Requests/season, execution rate | real holdout+trade-request band |
| `retirement` | Retirement age by position | P3-03 band |
| `development_opportunity` | Young-player snap share, depth-chart outcomes | rookies start at a realistic rate; no 68-OVR rookie benched behind a 34-year-old |
| `coach_loop` | Close-call chart changes, marquee calls/week | ≤4 model calls/week; chart churn small |
| `player_loop` | Counters used, leaks/season (cap 1/player), rejections | holdout frequency band |
| `owner_patience` | Firings/season, hot-seat duration | P3-06 firing band |
| `owner_hiring` | Hire quality, retread share, coordinator→HC rate | P3-06 |
| `coaching_tree_hiring` | Tree edges used in hires | plausible; no tree dominates |
| `owner_spending` | Cash spend vs cap, facility upgrades | real cash-to-cap ratios |
| `owner_meddling` | Frequency, effect size | rare, bounded |
| `scout_directors` | Report divergence from consensus, board lean | bias visible but not distorting |
| `media_voters` | Ballot vs article agreement, winner distribution | 100% agreement after P1-12 |

### 7.3 Work items

| ☐ | ID | Item | Size |
|---|---|---|---|
| ☑ | **P5-01** | Apply migrations to the tuning DB; run `personality.features show` — every feature must be able to reach `shadow`. **✅ Done 2026-11-12 (M-0709/M-0715, `p5-01-shadow-gate`):** `madden_tune_p2g` was backed up and migrated 138 → 145, all 20 features were taken to `shadow` one at a time and confirmed by `personality.features show`, then returned to `off` (40 history rows, version `p501-gate-1`). | S |
| ☐ | **P5-01a** | **Audit the switchboard's record-keeping unit** (`_actor` chain and the `migration_applied()` ledger gate), built on `main` as `6de1943` (M-0722). An auditor verdict is still owed; there is no report in `data/audit_reports/`. | S |
| ☐ | **P5-02** | Run `producers` + `relationships` (observe-only) for one season; review event completeness. | M |
| ☐ | **P5-03** | Run `profiles` (lifecycle inventory) for the tuning league's universe; preview → review → apply. | L |
| ☐ | **P5-04** | Run `evolution` + `focus_refresh` for a season; review change rates. | M |
| ☐ | **P5-05** | Tune the contract features (`contracts_ambition`, `contracts_relationships`) against FA bands. | L |
| ☐ | **P5-06** | Tune `development_learning` against the progression fit; confirm no double-counting with P3. | L |
| ☐ | **P5-07** | Tune `trade_requests` + `retirement` against their bands. | L |
| ☐ | **P5-08** | Tune `development_opportunity` + `coach_loop`; verify chart churn and marquee budget. | L |
| ☐ | **P5-09** | Tune `player_loop`; verify counter mix, leak caps, and that acceptance never changes. | L |
| ☐ | **P5-10** | Tune the owner family (`owner_patience`, `owner_hiring`, `coaching_tree_hiring`, `owner_spending`, `owner_meddling`). | XL |
| ☐ | **P5-11** | Tune `scout_directors` + `media_voters` (depends on P1-11, P1-12). | L |
| ☐ | **P5-12** | **Facility personality sensitivities + Q2 refusal** (follow-up #43) — the deferred veteran family/travel upweighting, high-ego −0.05, and Q2 refusal. These need ego/loyalty from the personality contract path, which only reaches `free_agency_engine.offer_utility` once this programme is active. **Do it here, not in P1.** | M |
| ☐ | **P5-13** | **Godfather trigger gates, part 2** (follow-up #47) — the `DEEP_POCKETS` + `HEADLINE_HUNTER` gate needs `2027_02_02` applied and owner features shadowing. | M |
| ☐ | **P5-14** | **Run the cumulative league.** All features shadowing at once for one final full season. This is the rehearsal for P6 and the first place compounding effects (trades → cap → free agency → record → firing) show up. | XL |

---

## 8. P6 — Clearance, activation, dress rehearsal

| ☐ | ID | Item | Done when | Size |
|---|---|---|---|---|
| ☐ | **P6-01** | **Export a clearance per feature** from the tuning DB: `personality.features export-clearance <FEATURE> --label CAREER-TUNE-<run> --out <path> --report <report> --dsn <tuning>`. Needs the full shadow season and a readable report (its sha256 is recorded). | One clearance document per feature, hash-verified. | M |
| ☐ | **P6-02** | **Import and activate on the real franchise:** `import-clearance`, then `activate-cleared --review "..."` **before 2026 week 1**. Stops at the first feature neither active nor cleared. | `features show` reports every feature `active` with its cleared version/config. | M |
| ☐ | **P6-03** | **Write the tuning ledger.** Every frozen constant, its value, its version, and the report/receipt that justifies it. This is the record that makes 2026 reproducible and the change-control rule enforceable. | `docs/reference/TUNING_LEDGER.md` (or `data/tuning/frozen.json`) committed. | S |
| ☐ | **P6-04** | **Dress rehearsal.** A fresh clone, everything `active`, one full simulated season: regular season, playoffs, offseason, draft, carousel, retirement, week 1 of the next year. No crashes, no silent step skips, stats inside band, decisions inside band, articles/tweets sampled. | A written rehearsal report JR signs off. **This is the real gate for P7.** | XL |
| ☐ | **P6-05** | **Freeze declaration.** After P6-04, no constant changes without a versioned, documented migration. | Recorded in the ledger and in `PROJECT_SPEC.md`. | S |

---

## 9. P7 — Start the canonical 2026 franchise  ◄ CHECKPOINT 1 NOT YET PASSED

| ☐ | ID | Item | Done when | Size |
|---|---|---|---|---|
| ◐ | **P7-00** | **The canonical start path (follows P0-6).** `CAREER-MUBASE` exists and is verified (P0-6/P7-01). **Current (2026-11-12): (a), (c), (e) and (f) are done; (b) seed the franchise DB and (d) scope the watcher remain open.** Runbook: **(a) ✅ done 2026-11-09** — `CAREER-MUBASE` hash-pinned (`data/baselines/CAREER-MUBASE.sha256`) with a pristine copy; it must never be simulated; **(b)** seed the franchise DB from it and register LeagueID 1287775425 with `--auto-init`, no `--user-team`; **(c) ✅ done 2026-11-09** — `league_office setup --save CAREER-MUBASE --out CAREER-MU2026` — the production port of the `realusers3.mjs` spike: a `FranchiseUser` row for all 32 teams, `Coach.IsUserControlled`, fill roster off, every player's `ConfidenceRating` 50, `CoachFiring` OFF, career clock off, plus progression's neutral bootstrap. **This supersedes `CAREER-REALUSERS`.** **(d)** run the watcher with **`--pattern "CAREER-MU*"`**; **(e) ✅ done 2026-11-12 (P7-00e)** — the pilot's managed-save allowance: `sim-season --managed` accepts a `CAREER-MU*` save as an explicit second class while `CAREER-TUNE-*` and every other refusal are unchanged; **(f) re-baseline tuning** from `CAREER-WEEK1` to `CAREER-MUBASE` (P2-09). **2034 is settled:** Carolina is the founding user and teams can be switched mid-franchise. | `CAREER-MU2026` produced by `setup`; watcher scoped; managed-save allowance; baseline archived. | L |
| ☑ | **P7-00e** | **Pilot managed-save allowance.** ✅ Done (2026-11-12, `p7-managed-save`). `jobs.sim_season` refused any save not named `CAREER-TUNE-*` and `clone_save` wrote only that prefix. The guard stays for its original reason (a clone keeps the real LeagueID), and one explicit second class was added instead of loosening the rule. **As built:** one rule in `jobs.save_name_refusal(save, managed)` — `CAREER-TUNE-*` unchanged; `CAREER-MU*` simmable only with the new `sim-season --managed` flag; every other name refused with or without it; **`CAREER-MUBASE` refused even with the flag** because P7-00(a) pins it pristine. A managed run allows exactly that save's tile on CURRENT FRANCHISES (a shared `_allow_tile`, also used by `catch-up`) and lifts the TUNE-only quit guard for its own quit, since the save is made by quitting to write the autosave. The watcher rule is unchanged: `--managed` still refuses while `pipeline.watch_save` runs. `clone-save` refuses a `CAREER-MU*` destination (a clone would share the LeagueID inside the watcher's `CAREER-MU*` scope); `advance` and `watch-game` stay TUNE-only. Guide: `docs/reference/MADDEN_PILOT.md` §3, §4, §5, §6. | `sim-season --managed --save CAREER-MU2026` runs; the same command without `--managed`, or on any other name, still refuses. ✅ `tests/test_madden_pilot_jobs.py` covers all four: the accepted run, the flag-less refusal, other prefixes (`CAREER-2029SB`, `SAVE-MU1`, `MU2026`, `CAREER-`) and the baseline, `CAREER-TUNE-*` unchanged with and without the flag, the in-place managed franchise, the refused managed clone destination, and a `--dry-run` of the acceptance command. | M |
| ☑ | **P7-01** | **Roster-size mod verification. ✅ Verified (JR, 2026-11-09).** The mod replaces `franchise-franchise-binary.ftc` and applies only at franchise creation: roster 80 preseason, cuts to **65** at cut day, PS eligibility 3 years, starting cap **+$40M**. JR confirmed the **65 cut-day limit in game**; the snapshot confirms the cap (`CAREER-MUBASE` 22,120 raw / 34,120 total against `CAREER-WEEK1` 18,120 / 30,120). This is load-bearing: `engines/transactions_engine.ROSTER_LIMIT = 65`. | Done. | — |
| ☐ | **P7-02** | Re-run the §5.4 save checks on the modded save (P4-06). | All pass. | M |
| ☐ | **P7-03** | Create the 2026 franchise; register it: `python3 -m pipeline.watch_save <save> --once --auto-init --franchise-name <name>` with **no** `--user-team`. | `db.franchises list` shows it active; its DB exists. | M |
| ☐ | **P7-04** | First `league_office setup` — now part of **P7-00(c)**; kept separate only because it has **never run on a franchise database** (V2 §7.3 item 2) and its own read-back check matters. | Setup completes; the output save reads back clean; 32 users, fill-roster off, every player at 50. | M |
| ☐ | **P7-05** | Seed verification: 32 team owners, 96 coach profiles re-seated to the 2026 staffs, 32 GMs, the full player lifecycle inventory, `nfl_history` 1999–2025 loaded, `personality.features show` all `active`. | A written pre-flight checklist passes. | M |
| ☐ | **P7-06** | Start `pipeline.watch_save`, `weekly_freeze`, and the site; run 2026 week 1 end to end (sim week → ingest → engines → league office → media). | Week 1 is on record with every step green. | M |
| ☐ | **P7-07** | **Tuning is now closed.** Any further constant change is a documented live-run break. | Ledger records the freeze date. | S |

---

## 10. P8 — Checkpoint 2: live-franchise quality of life

Runs in parallel from P1. Most items can be built and browser-checked long before P7; the *validation* needs
the live league.

### 10.1 Tweets — the largest gap

| ☐ | ID | Item | Size |
|---|---|---|---|
| ☑ | **P8-01** | Agent Wire U3 planner lands (same item as P1-06; listed here for checkpoint 2). ✅ Built 2026-11-09. | — |
| ☐ | **P8-02** | **Measure against the 2036 baseline.** `tools/social_quality_report --db madden_2036 --season 2036` must reproduce the baseline (REACTION 553 posts / 317 distinct openers), then the live league must beat it: distinct openers ≥85%, forward-looking ≥30%, posts citing 3+ numbers ≤15%. | M |
| ☐ | **P8-03** | Blind-read a week of live posts: do they read like real tweets (one moment, short, unfinished, rivalry trolling, label-first aggregator, stat account)? JR verdict. | M |

### 10.2 Articles

| ☐ | ID | Item | Size |
|---|---|---|---|
| ☑ | **P8-04** | **Close the dossier factual defects. ✅ Closed 2026-11-12 at the source.** The pilot's three defects are reproduced offline from the saved week-21 packet and fixed in `newsroom/db_tools.py` + `tools/dossier_pilot.py` (writer routing and model choice untouched): (1) the per-team leaders payload names the one metric that ranks each list and the pilot asks for a `tackles` leaderboard, so an interceptions-list row is no longer read as the tackle leader; (2) QB scheme splits label every figure as a per-game count/average, scope each split to the opponent's season defense and list the pressure/hurry and interception-rate splits that are not recorded; (3) `get_record_book` cross-checks previous values against the holder's recorded season line, flags a disagreeing `BOOK` row as `source_conflict` and recomputes broke/tied/ranks, so the stale 27 no longer makes Davis's 29 a broken record. Regressions: `tests/test_db_tools_dossier_facts.py` and `tests/test_dossier_pilot.py` (all fail at the parent commit); details in `docs/in_progress/V3_DOSSIER_EVALUATION.md` §P8-04. **Dossier evaluation cancelled by JR (2026-11-12):** the `newsroom/db_tools.py` corrections above remain because the leaderboard/metric/record-book fixes serve the ReAct/tool-call readers too; the dossier-packet and dossier-pilot parts are no longer a production requirement. The corrected paired run P8-05 is cancelled with it. | L |
| ☑ | **P8-04a** | **DeepSeek arm for the pilot** (JR, 2026-11-11). `tools/dossier_pilot.py --provider gemini\|deepseek`: DeepSeek V4.1 Flash (`deepseek-flash`) runs both arms, ReAct through `newsroom/tool_loop/openai_loop.py` and the dossier as one call, with the same writer, cutoff, packet, temperature and output cap as the Gemini arm. Usage accounting covers uncached, cached, output and reasoning tokens, priced at DeepSeek's peak or off-peak rate by the call's UTC time, with the same budget reservation and a $1 default ceiling. Offline tests use a fake client; no live call without `--live` and `DEEPSEEK_API_KEY`. Built by a DeepSeek session, audited by Opus. ✅ Built 2026-11-11 (`fc3fe72`, `e99bec4`; audit F1, cut-off articles flagged by `finish_reason=length`, CLOSED). **Experiment only after JR's 2026-11-12 decision to keep ReAct/tool calls; the dossier evaluation is cancelled, and this arm is retained as archive evidence, not a production dependency.** | — |
| ☑ | **P8-05** | **CANCELLED by JR (2026-11-12): production stays on the ReAct/tool-call path; the dossier arm and this paired matrix are not run.** The article-level side-by-side already run (below) and the model choice in P8-06 stand; the open follow-up is the recap fact gate, not a dossier rerun. **Historical brief:** Run the paired pilot on the widened matrix (widened 2026-11-12, JR). ReAct vs dossier × the Go shortlist from `docs/reference/MODEL_ROUTING_EVALUATION.md` — at least Gemini 3.8 Flash as today's baseline, DeepSeek V4.1 Flash, and the best two or three Go candidates that doc names — on the corrected packet, same writer/cutoff within each provider. Gate per cell: zero known factual contradictions in accepted outputs; median voice score ≥ the Gemini ReAct baseline with no dimension −0.5; report the cost per accepted article from the provider's own figure (`list_cost_usd`, and the Go allowance via `tools/go_usage.py`). **Go cells cost no cash:** paid Go models draw on their own monthly allowance, so the comparison is allowance accounting, not a bill. **Free models are usable cells** (corrected 2026-11-12): `beam-free`, `iceman-contributor-free` and `longcat-2.5-free` answer an external client through Go with `billing_source: "go"` and `allowance: 0` — the earlier "HTTP 403 from the inference endpoint" note described the **Zen** free tier, not Go. They stay out of the first pass only because none is scored and `iceman-contributor-free` trains on request data. **A parallel article-level track is built and has its first run** (2026-11-12): [`NEWSROOM_MODEL_SIDE_BY_SIDE_PLAN.md`](done/NEWSROOM_MODEL_SIDE_BY_SIDE_PLAN.md) ran the newsroom's own compiled prompts and tool set through Gemini 3.8 Flash, Muse Spark 1.3 and GLM-5.3-Flash on the `madden_2036` Week 18 archive — six slots each, **no failures**, 8–14× cost gap (Muse $0.18, GLM-5.3-Flash $0.32, Gemini $2.56 for the same six), Muse researching hardest (202 tool calls) and GLM-5.3-Flash least (129, with only 5 on the Hero). Results and artifacts: [`MODEL_ROUTING_EVALUATION.md`](reference/MODEL_ROUTING_EVALUATION.md) §8.1. **JR's blind read is in (2026-11-12) and the judge was dropped by him, not left pending.** His ranking, decoded: **GLM-5.3-Flash 10 points** (first or tied first in 3 of 6 slots, never clearly beaten but Slot 4), **Gemini 3.8 Flash 9** and **Muse Spark 1.3 9**; the incumbent never won a slot outright and was last on Fantasy at 8× the price. That retires Gemini as the default but does not separate the two Go models: the spread is one point over six articles and one week. **JR took the decision on 2026-11-12:** `glm-5.3-flash` writes every article (`ac8df19`); see P8-06. **Veracity check on the same 18 articles** (`tools/article_factcheck.py`, checker `deepseek-v4.1-flash`, claims judged against each writer's own evidence): contradicted 2.5% Gemini / 5.4% GLM-5.3-Flash / 6.7% Muse, grounded 53.3% / 53.6% / 50.8% of 240 claims each. A large share of all three models' flags is the archive's known Davis 27-vs-29 seeded-record error, not model error; the rest is recap precision (wrong opponent or score in a sentence). On Slot 4, where JR ranked GLM last, the checker found **0** contradictions against 2 and 3 — its loss there tracks tool depth (16 calls vs 32/44), not accuracy. **Open follow-up worth building:** a post-write fact gate over recap claims, forcing one correction pass on a CONTRADICTED verdict. | L |
| ◐ | **P8-06** | **JR decided the article half on 2026-11-12: `glm-5.3-flash` on OpenCode Go writes every article** (national slate, Mu Fantasy, custom commissions, offseason magazine) through `newsroom/model_default.DEFAULT_WRITER_MODEL`, with `gemini-3.8-flash` kept only as the comparison control and a per-slot fallback; `newsroom/swarm_dispatcher.py` builds the client from the model id, so a Go id needs `OPENCODE_GO_API_KEY` and not `GEMINI_API_KEY` (`ac8df19`). Agent Wire, season memories, press conferences and the engines' short calls are **not** articles and stay where they are until this row's decision half is answered — **the short-text evidence is in [`GLM_UNIFICATION_MEASUREMENTS.md`](reference/GLM_UNIFICATION_MEASUREMENTS.md) and the decision is deferred to P8-06b (JR, 2026-11-12).** The production routing is now settled by JR (2026-11-12): **ReAct/tool calls stay; the dossier is out** (P8-05 cancelled). What remains is the provider and the **model per job** (P8-06b for decisions): family-by-family migration; OpenCode Go (any Go model) or Gemini on its own API. Consume `docs/reference/MODEL_ROUTING_EVALUATION.md` (Go's per-model monthly limits and published scores) with P8-05's measured cells; where the two disagree, the measured cell wins. Cost note (2026-11-11, W21 pilot token counts): on DeepSeek a ReAct article costs ~$0.01 off-peak against ~$0.015 for a dossier, because cached input is ~50× cheaper there; so on DeepSeek the dossier must win on quality or speed, not cost. October's whole newsroom (~$67 on Gemini) prices at ~$10–20 on DeepSeek. Under Go this spend draws on the model's monthly allowance rather than a card charge — `python3 -m tools.go_usage` reports the 5-hour, weekly and monthly windows — so the per-job table also says which allowance it eats. | M |
| ☐ | **P8-06a** | **If DeepSeek (or another Go model) wins P8-06:** move the newsroom's callers onto the Go route. Two shapes, decided at P8-06: (a) **point the existing Gemini client at Go** — Go serves Gemini-family models through `/inference/go/google/v1beta/models/<model>:generateContent`, so `engines/llm_provider.py` changes base URL and key and `swarm_dispatcher.py` stays as it is; or (b) **port** `newsroom/swarm_dispatcher.py`'s tool loop and `db/llm_usage.UsageMeter` to the OpenAI-compatible client (the short GM / wire / memory calls follow). Either way, drop the Colab open-model path (`tools/prepare_colab_pilot.py`, `notebooks/`) and JR decides the Gemini Pro subscription. If Gemini on its own API wins, record why and keep Colab as a candidate only. | L |
| ◐ | **P8-06b** | **Pick the model for the decision call sites** (added 2026-11-12, JR). What a model actually chooses is press-conference casting and answers (`newsroom/press_room.py`, `newsroom/press_agents.py`) and the inbox counterpart's words (`engines/inbox/voice.py`; the inbox's *decisions* are deterministic — only the lines come from a model) plus the short GM / wire / memory calls. **The plumbing is built and waiting** (`GLM_5_3_UNIFICATION_PLAN`, on main 2026-11-12): the `go` provider, Agent Wire on a Go id, and `INBOX_MODEL` / `AGENT_MEMORY_MODEL` / `PRESS_MODEL` / `SOCIAL_MODEL` — **none flipped**. **Evidence in hand:** [`GLM_UNIFICATION_MEASUREMENTS.md`](reference/GLM_UNIFICATION_MEASUREMENTS.md) — inbox p95 3.06 s against the 4.0 s budget, JSON parse 6/6 wire / 3/3 takes / 2/2 inbox, Agent Wire blind read Gemini 46 / GLM 35 / Muse 39 (GLM last on 11 of 20), inbox blind read 5–4 to GLM (a wash). **Decision deferred by JR (2026-11-12):** improve the writing and prompting first and re-read, because passing `gemini-3.8-flash` is not the bar he wants — he does not rate 3.8 Flash as a voice benchmark either. Still unread: a memory block, 10 scouting reports, the `llm_compare` GM tasks; still unrun: one dry presser and the inbox p95 with Madden open. §9 of the findings doc lists what would change the recommendation (per-archetype prompt pass, a baseline JR respects, the unread sites, the PC number, then allowance cost). Score each candidate on the same briefs and a blind human read, and record the cost per site; the result is a row in P8-06's per-job routing table. | M |
| ☐ | **P8-07** | Sample-read a live week's slate for voice, continuity and factual accuracy; JR verdict. | M |

### 10.3 UI and surfaces

| ☐ | ID | Item | Size |
|---|---|---|---|
| ☐ | **P8-08** | **Inbox page visual check** (follow-up #26): take-control → list → thread → reply forms → new-conversation pickers, at narrow width, against the live API. | M |
| ☐ | **P8-09** | **Presser page visual check** (follow-up #29): podium → pick reporter → answer → no-comment → end → edit → approve → review screen → history links. | M |
| ☐ | **P8-10** | **Trade Machine + League Talks visual check** (follow-up #35): build a package, open a talk, hit the 409, read a published transcript. | M |
| ☐ | **P8-11** | Sweep the Command Center for layout/legibility at 1440p and narrow width; fix what looks wrong. | L |

### 10.4 Playing is easy

| ☐ | ID | Item | Size |
|---|---|---|---|
| ☐ | **P8-12** | **Boot straight into the game** (V2 §8, V2 §7.3 item 9). Route the pilot from launch to the user's pregame/kickoff screen, with screen checks and a hand-back to JR on timeout. V2 §8's route 2 is the chosen one. | XL |
| ☐ | **P8-13** | **Dynamic presentation mod — production integration.** Research is far ahead of the plan: **G0/G1 and G2 pass; R3 has a viable native-only away-conference route** (v0.5.1 switches native → cyan → native in-process with no reload; SNF/TNF/MNF render natively; the v0.28 stack and the 2,116-row resource audit exist). What's left: resolve the Monday/Thursday discrepancies, finish the coherent package (CBS banner layouts and night-package gaps are still open), and decide pack-vs-static-mod. **Not** "start from zero". **Current (2026-11-12):** the visual mod check that feeds this is at code pin `5f76c43` (merged `6ae10b5`); `audit-visual-r3` is rechecking it report-only and is paused on quota; F1's halftime show is still unmeasured, and live selected-row, reuse-launch and receiving→supersim evidence are owed. | XL |
| ☐ | **P8-14** | **Mods for playability.** Confirm `Mu Base` (49 always-on mods) is crash-free across a full season; keep the broken/older-build list current (Hair Fix crashed Madden). Verify `Mu Tune` values are folded in (P4-05). | M |
| ☐ | **P8-15** | Optional and deferred: in-game portraits (V2 §10 P4). The one-portrait spike has still not run. Decide do-now or post-2034. | L |

### 10.5 Interactivity latency

| ☐ | ID | Item | Size |
|---|---|---|---|
| ☐ | **P8-16** | Confirm inbox/press/GM-negotiation response feel with the live model (P1-09, P1-10 constants). | S |
| ☐ | **P8-17** | Confirm a full week's pipeline wall time is acceptable (ingest → engines → league office → media). | S |

---

## 11. P9 — 2026–2033 → the 2034 takeover

| ☐ | ID | Item | Size |
|---|---|---|---|
| ☐ | **P9-01** | Run 2026–2033 as a fully CPU league. Each week: sim → ingest → engines → league office → media. Weekly freeze on every commit. | — |
| ☐ | **P9-02** | Monthly health check: league shape (90+ count, mean OVR, age mix), decision rates against P3-06, article/tweet quality spot-check. Any drift past the P0-2 band is a bug report, **not** a tuning session. | — |
| ☐ | **P9-03** | 2034: mark the team user-controlled (`#debug`, `gm_team_settings.user_controlled`) and hand over the Command Center. | — |
| ☐ | **P9-04** | Post-takeover QoL pass: the live 2034 franchise's UI, articles, tweets, press, inbox, playing. **This is the final validation of checkpoint 2.** | — |

---

## 12. Appendix A — FOLLOW_UPS traceability

Every open row in `docs/FOLLOW_UPS.md`, mapped. Nothing is dropped.

| # | Follow-up | Where |
|---|---|---|
| 1 | Scouting-director wiring | P1-11 |
| 2 | SCOUT risers needs-proxy | P1-14 |
| 3 | Interview summary voice (LLM on) | P1-13 |
| 4 | Voter article alignment | P1-12 |
| 5 | Agent loops U3 wiring | P1-05 |
| 6 | Agent Wire U3 | P1-06 / P8-01 |
| 7 | Retirement restore authority (G4) | P1-07 |
| 8 | Free-agency contract-row creation | P1-08 |
| 9 | Inbox model-latency spike + `llm_compare` | P1-09 |
| 10 | Inbox page visual check | P8-08 |
| 11 | Press cloud latency + constants re-check | P1-10 |
| 12 | Press migration not applied | P1-01 / P1-04 |
| 13 | Presser page visual check | P8-09 |
| 14 | `OPEN_PRESSER` inbox option has no navigation | P1-15 |
| 15 | Madden pilot live acceptance | P2-01 / P2-02 |
| 16 | Supersim tuning loop never run for real | P4-01…P4-04 |
| 17 | Trade-talks migrations not applied | P1-01 / P1-04 |
| 18 | Trade-talks replay + transcript review | P1-25 |
| 19 | Trade Machine + League talks visual check | P8-10 |
| 20 | Carried talk loses its flags | P1-16 |
| 21 | Carried-talk teams count against the week | P1-17 |
| 22 | Comp-pick producer not wired | P1-18 |
| 23 | Comp-pick article scheduling | P1-19 |
| 24 | Report-card migration not applied | P1-01 / P1-04 |
| 25 | Report-card producer not wired | P1-20 |
| 26 | Report-card article scheduling | P1-21 |
| 27 | Facility personality sensitivities + Q2 refusal | P5-12 |
| 28 | CPU owner facility-upgrade AI | P1-23 |
| 29 | Staff-contracts migration not applied | P1-01 / P1-04 |
| 30 | Staff-contract producer not wired | P1-22 |
| 31 | Godfather trigger gates not fully live | P1-27 / P5-13 |
| 32 | Post-unilateral trade flow re-measure | P1-25 / P3-08 |
| 33 | Trade Finder misses the rival-overpay package | — (debug tooling, no close-out gate) |
| — | Activation gates (owners/scouts/voters; whole programme) | P5 → P6 |

---

## 13. Appendix B — Verified state at 2026-11-09 (after P1 waves 1–3, P1-04 and P1-25)

- **Databases:** `madden_mu2026` (**the live franchise database**, LeagueID 1287775425, seeded from
  `CAREER-MUBASE` 2026-11-09; 127/127 migrations, every personality feature `off`), `madden_2036` (read-only
  archive), `madden_test`, `madden_v3_eval_20261030`. `madden_tt_replay` is a scratch copy of the archive used
  for P1-25, not a franchise.
- **`madden_test` is at the head of `db/migrations/`:** all **127** SQL migrations applied and recorded in
  `schema_migrations` (the six it was behind, `2027_01_20`/`2027_02_02`/`2027_02_06`/both `2027_02_07`/
  `2027_02_08`, plus the parity repair `2027_02_09` and wave-3's `2027_02_10`/`2027_02_11`/`2027_02_12`; the
  others are `baseline`, because they were applied before the ledger existed and the parity diff proves the
  schema is at head). Trade talks (`2027_01_22`) and
  its trigger (`2027_02_05`) were already applied on 2026-11-08. `persona_focus`, `nfl_history`,
  `persona_relationships`, `agent_loop_decisions`, `agent_season_calls`, `player_progression_history`,
  `agent_predictions` are applied.
- **Applied state is a query:** `python3 -m db.migrate status --dsn <DSN>` reports applied / pending / drifted
  per file; `init_db.sql` carries a generated ledger block so a fresh build reports 124/124 and `apply` is a
  no-op. `python3 -m db.migrate diff` proves `madden_test` is schema-identical to a fresh `init_db.sql` build.
- **`CAREER-TUNE-*` guard exists:** the watcher skips tuning clones (with `--allow-tune-save` / a
  `madden_tune_*` `--dsn` as the opt-in) and refuses one routed to any other database (P1-24).
- **Tuning league is isolated and free (P2-04/P2-06/P2-07, 2026-11-09):** `madden_tune_p2a` was created from
  `CAREER-TUNE-P2A` (a clone of `CAREER-MU2026`), ingested with `--no-articles`, and reset once with
  `--reseed`; it has 2,847 players, 272 games, the LeagueID stamp and **0 `llm_usage` rows**, no registry entry.
  `madden_mu2026` was byte-identical in size and row counts before and after. Recipe:
  [`reference/TUNING_LEAGUE.md`](reference/TUNING_LEAGUE.md).
- **`OPEN_PRESSER` navigates:** the inbox shows a generic notice link resolved from `link_table`, and the act
  lands on the Game Center (P1-15).
- **Switchboard:** 20 features, D2 go-live order, all migrations for them now exist. All read `off`.
- **Pilot catalog:** 19 screens after P2-01 (2026-11-10): the title, the Franchise landing page, the Week 1 draft-class card, FORCE ADVANCE and its confirmation joined the original 13. No postseason, offseason-stage, Super Bowl, savepoint-dialog or pre-snap screens yet.
- **First season simmed (P2-01):** `CAREER-TUNE-P2A` reached 2026 WildcardPlayoff (week_index 18, 326 games scored) and ingested into `madden_tune_p2a` (272 completed games, 0 `llm_usage`).
- **Tuning tooling:** `targets.py`, `compare.py`, `progression_fit.py`, `supersim_loop.py`, and (built
  2026-11-08) `decision_targets.py` for career length, retirement age, coach changes, trades, team movement and
  draft outcomes. The decision *comparator* (`decision_compare.py`, P3-07) is built and writes
  `decisions_<season>.md|.json`; its F1/F3/F6–F10 audit fixes are in, while F4 (A6 started-season) and F5
  (A1/A2 ended-career cohort) are held and not built in this pass.
- **Dynamic presentation research:** G0/G1/G2 pass; R3 native-only route viable (v0.5.1); resource audit
  complete (956 unique resources, 160 unresolved). No production integration.
- **Uncommitted work in the tree:** `docs/MOD_INSTALL.md`, several plans, plus untracked research and
  Colab-result ZIPs. Left untouched.

---

### 13.1 Verified state at 2026-11-10 (after a full tuning annual cycle)

- **2026-11-10, `p2-finish`: the cycle runs from one command.** `engines.tuning.season_loop --run p2g` seeded
  `madden_tune_p2g` from the week-1 clone (2,847 players bootstrapped — a played season's save is refused by the
  neutrality gate), simmed 2026 and the postseason, and walked the stages in 29.4 min; a resumed walk finished to
  **2027 regular-season week 1**. `league_office_runs` 1-11 (PLAYOFF_WEEK, OFFSEASON_START, RESIGN, FREE_AGENCY ×2,
  COMBINE, PRO_DAY, DRAFT, UDFA, PRESEASON, CUTDOWN) are all WRITTEN; `season_2026.md` = **score 0.187, 257 of 398
  stats inside the 2015-2025 band**.
- **Open after that run:** the 2027 week-1 REGULAR_WEEK plan fails because a drafted rookie has no progression
  state (`player 2848: explicit neutral bootstrap required`) — nothing bootstraps players who join after setup.
  **Fixed 2026-11-10 (builder-4, `bootstrap_new_players`):** a player Mu has never tracked (no state, no event)
  now gets his neutral state at any ingest, at the league year the stage belongs to, for the live league and
  every `madden_tune_*` database; the league office tops up again before it plans a progression stage. Proven by
  tests and on the P2G snapshot (`unseeded after: 0`, the 389 rookies are the repro). `league_state.current_week` still waits at the seed
  week through an offseason ingest — JR decided 2026-11-10 to leave it (the stage is authoritative, nothing
  plans wrong).

- **A complete season under Mu's control:** `CAREER-TUNE-P2C` (a fresh, capacity-pruned clone of `CAREER-MU2026`)
  ran the 2026 regular season, the postseason, all eight offseason stages, the 2027 preseason weeks and the cut
  day, and landed on **2027 regular-season week 1** — one `tools.madden_pilot advance` per stop, the pilot
  skipping both live events and simming the draft itself.
- **The league office wrote eight stages into that save** — `league_office_runs` 1-8: RESIGN, FREE_AGENCY ×2,
  COMBINE, PRO_DAY, DRAFT, PRESEASON, CUTDOWN, all `WRITTEN`. The cut day cut the save's players 3,238 → 3,142.
  `madden_tune_p2c` holds 2026 (285 scored games, `league_state` 2026/1, **0 `llm_usage`**); `madden_mu2026` was
  not touched.
- **Pilot catalog: 27 screens**, the whole annual cycle with no unknown screen. The only uncatalogued screen left
  is pre-snap (`first_down_line`).
- **Offline suite:** 5,522 passed / 2 failed in `tests/test_madden_pilot_win.py` (a pre-existing focus-count
  assertion, unrelated), plus `tests/test_local_headshot.py` not collecting (pre-existing).
- **Two open gaps in the tuning path:** the DRAFT stage wrote no picks (`draft_picks.overall_pick` 0 of 672 —
  `engines.draft_room.lock_order` is a manual Draft Room step), and an offseason/preseason ingest leaves
  `league_state.current_week` at the seed week (the stage itself is detected correctly).

---

## 14. Appendix C — Deliberately out of scope before 2026 week 1

Not gaps; recorded so they are not mistaken for missing work.

- **V3 multimedia:** podcasts, draft-walkout videos, LoRAs, photojournalism, fan ecosystem.
- **CFB 27 → Madden bridge** (`§9`): no `cfb_save_reader`.
- **In-game portraits** (V2 §10 P4): deferred, one-portrait spike not run.
- **Arbitrary CBS/FOX selection for an identical afternoon matchup** (dynamic presentation §9): not
  demonstrated; the accepted policy is the native away-conference rule.
- **A manifest/script/runtime bridge** for presentation: not justified by the current native-only result.
- **Napper conversational personas** (V3 §7).
