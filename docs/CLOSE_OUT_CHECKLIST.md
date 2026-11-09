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
  │        └────────────► P7  Start the canonical 2026 franchise  ◄── CHECKPOINT 1 PASSED
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
| **P0-4** | **The model.** DeepSeek v4.1 Flash is the current routing (CLAUDE.md). It gates the inbox latency spike, the press-cloud constants and all `llm_compare` gates (P1). | P1-09, P1-10, P8. |
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
| ☑ | **P1-04** | **Apply migrations to a franchise database. ✅ Done (2026-11-09).** `madden_mu2026` was created and seeded from `CAREER-MUBASE` by `watch_save --once --auto-init --franchise-name mu2026 --no-engines --no-articles --no-league-office` (no user team; LeagueID 1287775425 registered and active; league state 2026 week 1, 2,847 players, 208 public tables). `db.migrate status` reports **127 applied, 0 pending, 0 drifted** — every file sourced from `init_db.sql` — and `db.migrate apply` is a no-op. `personality.features show` runs and reports all 20 features `off`. Resolves follow-ups #12, #17, #24, #29 and the six-feature activation gate. | A franchise DB has every table; `personality.features show` runs against it. | S |

### 3.2 Held and unwired units

| ☐ | ID | Item | Done when | Size |
|---|---|---|---|---|
| ☑ | **P1-05** | **Agent loops U3 wiring** (follow-up #21). ✅ Built (2026-11-09) as U3a + U3b: league office coach charts and CPU contract counters (`counter_chooser` seam), inbox `CONTRACT`/`ROLE_TALK` replies, social desk `PLAYER_STATEMENT` posts, and the `coach_loop`/`player_loop` switchboard features (installed, off by default). `tests/test_agent_loops_wiring.py` (16), `tests/test_agent_loops_statements.py` (11) and `tests/test_agent_loops_promises_wiring.py` are green. The coach-promise watcher (`coach.promise_options`/`decide_promise`/`review_promises`) and the CPU contract marquee floor followed in `AGENT_LOOPS_FOLLOWUPS_PLAN` (2026-11-09). | `tests/test_agent_loops_wiring.py` green; a shadow run records CHART / CONTRACT / LEAK decisions. | L |
| ☑ | **P1-06** | **Agent Wire U3 planner** (follow-up #22). ✅ Built (2026-11-09): U3 was reworked onto the trade-talks `newsroom/social_desk.py` and merged; the whole plan (U1–U5) is on main, so `week_mix`, league topics, `PICKS`/`HOT_TAKE`/`SEASON_CALL`, structured output, graded picks/calls, relationship wiring and the quality report are all in. **This is the item that makes tweets stop reading like captioned box scores.** | `python3 -m newsroom.social_desk --season 2035 --week 12` prints a plan; `tools/social_quality_report` reproduces the 2036 archive baseline (553/317) — reproducing or beating the live target waits for a live season (P8-02). | L |
| ☐ | **P1-07** | **Retirement restore authority (G4)** (follow-up #23). Produce the save-authority proof file. Until then late native retirements stay observe-only, and the `retirement` feature cannot be activated. Needs the PC. | `docs/reference/RETIREMENT_AUTHORITY.md` §5 proof file exists; G4 passes. | M |
| ☑ | **P1-08** | **Free-agency contract-row creation** (follow-up #24, V2 §7.3 item 6). ✅ Built (2026-11-09). `pipeline/save_reader/sign.mjs` fills one of the `PlayerContract` table's empty rows when no released row is free; `save_writer.mjs` opens the save with `autoUnempty` so the terms turn the empty row live, and the league office no longer defers an accepted offer without a matching release (`tests/test_league_office.py`). Verified on a copy of the sample save: the signing lands, reads back with exactly one holder, and the source sha256 is unchanged (`pipeline/save_reader/test/sign.test.mjs`). | A signing with no release lands and reads back. | L |
| ☐ | **P1-09** | **Inbox latency spike + `llm_compare` gate** (follow-up #25). 20× `make_llm("inbox")` on a filled CONTRACT prompt with Madden open; record p50/p95; set `INBOX_LLM_BUDGET_S`. Gate inbox replies and appraisals through `tools/llm_compare`. | Constants set from measurement; report filed. | M |
| ☐ | **P1-10** | **Press cloud latency + constants re-check** (follow-up #27). The cloud half was never measured (no `.env`/`GEMINI_API_KEY` at build). Confirm `BASE_PATIENCE` 14 and no-loss-penalty still hold. | One warm cloud round measured; report filed. | M |
| ☑ | **P1-11** | **Scouting-director wiring** (follow-up #17). ✅ Built (2026-11-09). `scouting_engine.run(..., personality=)` creates each team's director through the reviewed `staff:scout` lifecycle context when `scout_directors` is active, leans the team board (`scouting_boards.board(extra_bias=)`) and writes the team's private report rows (`scout_reports.team_id/scout_id/voice`, grade = consensus + the bounded bias); migration `2027_02_11_scout_report_team_rows.sql` moves the PK to two partial unique indexes. The department row and the consensus never move (`tests/test_scout_director_wiring.py`). | Scout reports carry `scout_id` + `voice`; board lean moves with the director. | M |
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
| ◐ | **P1-25** | **Trade-talks replay + transcript review** (follow-up #34). **Re-run after the unilateral build; awaiting JR's read (2026-11-09).** The pre-unilateral sample (7 talks, all AGREED in 2 turns, 0 stalls) exposed the bilateral pre-screening; [`UNILATERAL_TRADE_ENGINE_PLAN.md`](done/UNILATERAL_TRADE_ENGINE_PLAN.md) sent in the initiator's personality-selected offer and let the responder judge it. Replay on a fresh scratch copy of `madden_2036` (all CPU, history cleared), weeks 4–8: **model sample 24 talks — 9 AGREED, 9 STALLED, 6 WALKED_AWAY (breakdown 62.5%), 9 ANCHOR/15 FIRM openings, 1 carried (resumed, then stalled "the deal stopped working"), 8 leaks**; deterministic `--no-llm` twin **25 talks — 8/11/6 (breakdown 68%), 2 carries, 9 leaks**; agreed talks average 2.4 (model) / 2.9 (policy) turns. **`TALK_STALL 0.25` is far below the measured breakdown share** — retune in P3-07 with the full-season volume check (9 executed in this slice, inside the 5–21 band but not a full-season proof). Transcripts: model `verify_output/p1_25/voice/` (agreed, stalled+leak, walked away) and policy `verify_output/p1_25/unilateral/transcripts/`; both scratch DBs kept until JR has read them. | Convergence rate matches the assumption; transcripts approved. | M |
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
| **4** | P1-07 (retirement G4), P1-09 (inbox latency), P1-10 (press cloud) | Everything that needs the PC, Madden, or credentials. Batch into one PC session. | Yes |

**P1-04** (apply migrations to a franchise DB) is not in a wave: it waits for the franchise database, which is
created by seeding `CAREER-MUBASE` (P7-00b). **Done 2026-11-09:** `madden_mu2026` exists and is at head.

**P1-25** (trade-talks replay) left Wave 4: the initial replay ran on 2026-11-09 and exposed the bilateral pre-screening issue; it was re-run on 2026-11-09 after [`UNILATERAL_TRADE_ENGINE_PLAN.md`](done/UNILATERAL_TRADE_ENGINE_PLAN.md) landed, and only JR's read of the three multi-turn transcripts remains.

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
| ☐ | **P2-01** | **First live `sim-season`** (follow-up #31, V2 §7.3 item 8). The pilot has only run unit tests and `--dry-run` against `ScriptedMadden`. **This is the gate for everything in P3–P5.** | One season sims end to end on `CAREER-TUNE-*`. | L |
| ☐ | **P2-02** | **Extend the pilot catalog for the offseason.** Catalogued today: `MAIN_MENU`, `FRANCHISE_LIST`, `CHOOSE_TEAM`, `HUB_GAMEDAY`, `HUB_WEEKLY`, `HUB_OTHER`, `ADVANCE_MENU`, `WEEKLY_RECAP`, `COACH_PROGRESS`, `ROSTER_DECISION_PROMPT`, `PROGRESSION_WARNING`, `DELETE_CONFIRM`, `QUIT_CONFIRM`. **Missing and required:** savepoint dialog, post-season, Super Bowl, offseason stages (re-sign, free agency, draft, preseason), pre-snap. Every unknown screen stops the run with a screenshot. | A full annual cycle (regular season → playoffs → offseason → preseason → week 1) sims without an unknown screen. | L |
| ☐ | **P2-03** | **Offseason stage handling.** The pilot must answer or avoid Madden's offseason prompts (re-sign decisions, draft, retirement) in a way that leaves the decisions to Project Mu. Decide: does the pilot sim *through* the offseason, or does Mu drive each stage through the save writer? | A season boundary completes under Mu's control. | XL |
| ☐ | **P2-04** | **Tuning database + routing.** A clone keeps the real `LeagueID`, so `route_franchise` would send it to the real DB. Establish the tuning path: `watch_save --once --dsn madden_tune_<x> --no-articles`. Decide whether to give tuning clones a registry entry or drive ingest manually. | A tuning season ingests into `madden_tune_*` with zero writes to the real DB. | M |
| ☐ | **P2-05** | **A tuning-run harness.** Generalise `engines/tuning/supersim_loop.iteration()` into: clone save → sim with pilot → ingest to tuning DB → run weekly engines + league office → produce a report. One command per simulated season. | `python3 -m engines.tuning.season_loop --run <name>` runs a season and writes a report. | L |
| ☐ | **P2-06** | **Reset and isolation policy.** Document how a tuning DB is dropped, how many clones are kept, where they live, and that `CAREER-MUBASE` is never simulated. | A written recipe in `docs/reference/`; a `--reset` path exists. | S |
| ☐ | **P2-07** | **Media off by default in tuning.** Confirm `--no-articles` (it exists) and that social/memory writes are skipped or cheap. Tuning must never spend API budget on 320 articles per season. | A tuning season runs with zero model calls. | S |
| ☑ | **P2-09** | **Re-baseline the tuning tooling. ✅ Operational defaults done (2026-11-09).** `jobs.BASELINE_SAVE`, `supersim_loop.BASE_SAVE`, both skills and the test fixtures now point at **`CAREER-MUBASE`**; the supersim measurement-first flow is written into `/tune`, `MADDEN_PILOT_AND_TUNING_PLAN` §2.4 and the acceptance doc §3. **One step remains:** once `league_office setup` has produced `CAREER-MU2026` (P7-00c), switch the source for anything that **sims or runs the league office** to it, because only the post-setup save carries the 32 users, fill-roster off and morale 50. Offline work (the progression fit) can stay on `CAREER-MUBASE`. | No tuning path references `CAREER-WEEK1`; sim/league-office clones come from `CAREER-MU2026` after setup. | S |

---

## 5. P3 — Offline tuning (deterministic engines)

**No Madden required.** This is where progression, career length and development curves get their numbers.

### 5.1 Progression and career length

| ☐ | ID | Item | Done when | Size |
|---|---|---|---|---|
| ☐ | **P3-01** | **Build the 2015–2025 targets.** `python3 -m engines.tuning.targets` → `data/tuning/targets_2015_2025.json` (git-ignored, ~5 s once nflverse is cached). | Targets file exists and is current. | S |
| ☐ | **P3-02** | **Run the progression fit.** `/tune progression --seasons 5 --budget 200 --seed 0`, then `--seed 1` to confirm the recommendation holds. **Never apply what it recommends** — present the numbers. | `data/tuning/progression_fit.md` with before/after score and ranked constants. | M |
| ☐ | **P3-03** | **Career-length targets.** Measure real 2015–2025 career lengths by position from `data/history/nfl_careers_through_2025.csv` (mind the survivor bias already documented: 1,713 of 1,886 careers are still active). Compare with Mu's retirement output at the end of P5. | A table of real median/quartile career length per position; Mu's output sits inside it. | M |
| ☐ | **P3-04** | **Development-curve targets.** The fit scores against the snapshot's own shape and the careers CSV. Extract what "a 68-OVR rookie gains ~5–6 a year under the league term" means against real rookie-to-year-3 arcs; flag if the league-shape term is doing work that real careers don't justify. | A short written verdict; either accept or open a follow-up to revisit the league term. | M |
| ☐ | **P3-05** | **Retirement baseline.** With P1-07 (G4) and P1-26 (retirement rows), calibrate the retirement policy's base rate against P3-03. Modifiers stay off. | Base-retirement age distribution matches real NFL within the P0-2 thresholds. | L |

### 5.2 Decision targets — **missing today**

`engines/tuning/targets.py` builds **player stat** targets only. Checkpoint 1 is about *decisions*, and there
is currently no yardstick for them. This is new work.

| ☐ | ID | Item | Done when | Size |
|---|---|---|---|---|
| ☐ | **P3-06** | **Build `engines/tuning/decision_targets.py`. ✅ Built and audited 2026-11-08.** Career length is measured from **season rosters** (active statuses, CUT-only excluded), which reproduces the quoted ~3-year average (3.40 including cut-only players) and shows the drafted cohort averages ~6; trades are split **in-season (5–21/season) vs offseason (45–84)**. Also: retirement age by population, HC changes, team movement, draft by round, rookie usage. `tests/test_decision_targets.py` covers the helpers; the audit is written up in `TUNING_ACCEPTANCE_THRESHOLDS.md` §0. **Remaining:** the curated items with no open dataset (GM changes, coordinator changes, blockbuster rate, holdouts) — §4 there. | Module runs; tolerances signed off (P0-2). | L |
| ☐ | **P3-06a** | **Measure Mu's own player inflow.** Career length is arithmetically bounded by `roster slots / new players per season`; a 224-pick class with ~2,200 slots implies ~10 years before the retirement engine is consulted. Count drafted + generated UDFA players entering per simulated season, then set the A1 gate against the population Mu actually produces. **Without this the career-length gate is a guess.** | Inflow measured; population chosen (drafted vs all-comers) and recorded. | M |
| ☐ | **P3-07** | **Wire a decision comparison report** (`compare_decisions(observed, targets)`) so a shadow season produces a scored report next to the stat report. The Mu-side extractors are named in `TUNING_ACCEPTANCE_THRESHOLDS.md` §2 (e.g. `gm_trade_proposals` APPROVED, `player_season_transitions` MOVED, `coach_tenures`). | A shadow season prints "HC changes 7 vs real 4–10 ✓ / trades 61 vs real 49–102 ✓ / …". | M |

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
| ☐ | **P5-01** | Apply migrations to the tuning DB; run `personality.features show` — every feature must be able to reach `shadow`. | S |
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

## 9. P7 — Start the canonical 2026 franchise  ◄ CHECKPOINT 1 PASSED

| ☐ | ID | Item | Done when | Size |
|---|---|---|---|---|
| ☐ | **P7-00** | **The canonical start path (follows P0-6).** `CAREER-MUBASE` exists and is verified (P0-6/P7-01). Runbook: **(a)** hash `CAREER-MUBASE` and archive a pristine copy outside the saves folder — it becomes the tuning baseline and must never be simulated; **(b)** seed the franchise DB from it and register LeagueID 1287775425 with `--auto-init`, no `--user-team`; **(c)** `league_office setup --save CAREER-MUBASE --out CAREER-MU2026` — the production port of the `realusers3.mjs` spike: a `FranchiseUser` row for all 32 teams, `Coach.IsUserControlled`, fill roster off, every player's `ConfidenceRating` 50, `CoachFiring` OFF, career clock off, plus progression's neutral bootstrap. **This supersedes `CAREER-REALUSERS`.** **(d)** run the watcher with **`--pattern "CAREER-MU*"`**; **(e)** give the pilot a managed-save allowance (P7-00e); **(f) re-baseline tuning** from `CAREER-WEEK1` to `CAREER-MUBASE` (P2-09). **2034 is settled:** Carolina is the founding user and teams can be switched mid-franchise. | `CAREER-MU2026` produced by `setup`; watcher scoped; pilot advance flag; baseline archived. | L |
| ☐ | **P7-00e** | **Pilot managed-save allowance.** `jobs.sim_season` refuses any save not named `CAREER-TUNE-*` (`tools/madden_pilot/jobs.py:372`), and `clone_save` only writes that prefix. That guard exists for a good reason (a clone keeps the real LeagueID), but the 2026–2033 CPU era requires the pilot to advance the **canonical** save every week. Add an explicit second allowed class — a `--managed` flag plus a `CAREER-MU*` prefix — rather than loosening the rule: `CAREER-TUNE-*` stays for disposable tuning, `CAREER-MU*` is only simmable with the flag, and everything else still refuses. | `sim-season --managed --save CAREER-MU2026` runs; the same command without `--managed`, or on any other name, still refuses. | M |
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
| ☐ | **P8-04** | **Close the dossier factual defects.** The pilot found: wrong tackle leader, an interception-rate misdescription, a record claim built on a stale `BOOK` row (since corrected on the eval DB only). Fix source conflicts and metric labelling. | L |
| ☐ | **P8-05** | **Run the paired pilot** on the corrected packet (ReAct vs dossier, same writer/cutoff/model). Gate: zero known factual contradictions in accepted outputs; median voice score ≥ ReAct with no dimension −0.5. | L |
| ☐ | **P8-06** | Decide the production routing: ReAct stays, dossier replaces it, or family-by-family migration. | M |
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
| ☐ | **P8-13** | **Dynamic presentation mod — production integration.** Research is far ahead of the plan: **G0/G1 and G2 pass; R3 has a viable native-only away-conference route** (v0.5.1 switches native → cyan → native in-process with no reload; SNF/TNF/MNF render natively; the v0.28 stack and the 2,116-row resource audit exist). What's left: resolve the Monday/Thursday discrepancies, finish the coherent package (CBS banner layouts and night-package gaps are still open), and decide pack-vs-static-mod. **Not** "start from zero". | XL |
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
| 32 | Post-unilateral trade flow re-measure | P1-25 / P3-06 / P3-07 |
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
- **`OPEN_PRESSER` navigates:** the inbox shows a generic notice link resolved from `link_table`, and the act
  lands on the Game Center (P1-15).
- **Switchboard:** 20 features, D2 go-live order, all migrations for them now exist. All read `off`.
- **Pilot catalog:** 13 screens; no offseason, Super Bowl, savepoint or pre-snap screens.
- **Tuning tooling:** `targets.py`, `compare.py`, `progression_fit.py`, `supersim_loop.py`, and (built
  2026-11-08) `decision_targets.py` for career length, retirement age, coach changes, trades, team movement and
  draft outcomes. The decision *comparator* (`compare_decisions`) is still to build (P3-07).
- **Dynamic presentation research:** G0/G1/G2 pass; R3 native-only route viable (v0.5.1); resource audit
  complete (956 unique resources, 160 unresolved). No production integration.
- **Uncommitted work in the tree:** `docs/MOD_INSTALL.md`, several plans, plus untracked research and
  Colab-result ZIPs. Left untouched.

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
