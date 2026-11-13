# P5 report readiness — is there a number for every feature?

**Task:** the §7.1 readiness rule (`docs/CLOSE_OUT_CHECKLIST.md:331-332`): *a feature that cannot be measured by a
number is not ready to activate; if a report doesn't exist, build the report before tuning the feature.* P5 has 13
open items (P5-02…P5-14, checklist `:365-377`), each a feature × a simulated season. The first completed tuning
season (P4-01, `check-d`, 2026-11-12, board M-0856, `docs/reference/P4_01_SUPERSIM_BASELINE.md`) wrote one compare
report and no feature number at all, so there is still no season anyone can check that readiness in. This document
is that check, done by reading the code.

**Run:** 2026-11-12 by `deepseek-v4.1-flash` (`p5-report-audit`). **Inventory only:** no report was built, no
season was run, no engine, feature or checklist row was changed, no PC, no save, no database, no model call. Every
claim below is a `file:line` in this checkout.

**The four questions asked of every feature**, exactly as §7.1 step 3 needs them:

1. **Does a report exist** that prints the number §7.2 (`docs/CLOSE_OUT_CHECKLIST.md:334-357`) says to read — name
   the module / CLI / query, or say there is none.
2. **Is it reachable from a tuning season** — does `engines/tuning/season_loop.py` or its decision report
   (`engines/tuning/decision_compare.py`) call it or cite it, or does it only exist as a one-off script or a test?
3. **Does it score against the P3-06 target** — a band, a comparator, a pass/fail — or does it only print raw
   numbers a human reads against `TUNING_ACCEPTANCE_THRESHOLDS.md`?
4. **What would a shadow season need** to produce it — a step in the season loop, a reader over tables the season
   already fills, a stored outcome that is not stored today, or new code. Sized S / M / L (S: a query and a row in
   the existing report; M: a new reader/CLI over data that already exists; L: needs an outcome, column or dataset
   that does not exist yet).

---

## 0. The answer in one paragraph

**No feature has a season report that is called by the tuning season.** `season_loop` writes exactly two files per
season — `season_<year>.md` (stats, `engines/tuning/compare.py`) and `decisions_<year>.md` (the A1–A7 decision
gates, `engines/tuning/decision_compare.py`, called at `engines/tuning/season_loop.py:474-484`) — and neither
mentions a switchboard feature or reads `personality_feature_modes`. Of the 20 features, **three** have a §7.2 comparator that
exists and is scored (`retirement` through A1/A2, `owner_patience` through A3, `development_opportunity` through
A6/A7 as a proxy) — and for the first two the number scored is the league's, not the feature's; **three** more have a
report somewhere that the season never runs (`evolution`, `focus_refresh`, `profiles`), **thirteen** record their
evidence in tables that nothing reads, and **two** (`scout_directors`, `media_voters`) cannot be observed in a shadow
season at all — their `shadow` mode is a
no-op and neither runs under `season_loop`. Six features name a comparator in §7.2 that does not exist anywhere.
Detail per feature in §2, the gaps in §3, and the shortest order that fixes it in §4. 
---

## 1. Summary counts

| Question | Answer |
|---|---|
| Features whose §7.2 number is printed by a report the tuning season calls | **0 of 20** |
| Features with a report of any kind, somewhere | **5** (`retirement`, `owner_patience`, `evolution`, `focus_refresh`, `profiles` — the last three not run by the season) |
| Features whose evidence is recorded in the tuning DB but has **no reader** | **13** |
| Features that cannot be measured in a shadow season today (a design gap, not a missing report) | **2** (`scout_directors`, `media_voters`) + **2 halves** (`coach_loop` / `player_loop` model-call behaviour, forced off by `MU_NO_LLM=1`) |
| (§7.2 comparator) exists **and** is scored | **3** — `retirement` A2, `owner_patience` A3, `development_opportunity` A6/A7 as a proxy. For the first two the number scored is the *league's*, not the feature's (§3.5) |
| §7.2 comparator exists but nothing evaluates it (a stated binary or constant rule) | **8** — `producers`, `relationships`, `profiles`, `evolution`, `development_learning`, `coach_loop`, `owner_meddling`, `media_voters` |
| §7.2 comparator does not exist anywhere | **6** — `contracts_ambition`, `contracts_relationships`, `trade_requests`, `player_loop`, `owner_hiring`, and `owner_spending` (its cash dataset) |
| §7.2 comparator undefined **and** the signal unmeasurable today | **3** — `focus_refresh`, `coaching_tree_hiring`, `scout_directors` |

The 13 features with recorded-but-unread evidence are `producers`, `relationships`, `contracts_ambition`,
`contracts_relationships`, `development_learning`, `trade_requests`, `development_opportunity`, `coach_loop`,
`player_loop`, `owner_hiring`, `coaching_tree_hiring`, `owner_spending`, `owner_meddling`. Their rows are in the
tuning database by the end of a season; nothing turns them into a number a person or a gate can read.

---

## 2. One row per feature (the D2 go-live order)

`FEATURES` is `personality/features.py:60-63`. "Reachable" means: set the feature to `shadow`, run
`engines/tuning/season_loop.py`, and the feature's engine or hook actually executes during that season. It assumes
the **week-by-week** walk, the loop's default: `--fast-season` sims the season in one `sim-season` shot and skips the
weekly league office entirely (`engines/tuning/season_loop.py:437-446`), so under it only the offseason-stage
features run at all — which is also why the P4-01 season could not have produced a feature number.

| # | Feature | §7.2 signal | Season report? where | Reachable from a season? | Scored against a target? | Gap | Size |
|---|---|---|---|---|---|---|---|
| 1 | `producers` | event volume, unresolved-identity queue, per-team coverage | **No.** Per-tick counts stored (`engines/relationship_producers/run.py:377-386`) + one log line per ingest (`pipeline/watch_save.py:641-700`); `catch-ups` prints one aggregate (`run.py:399-427`) | **Yes** — every weekly ingest (`pipeline/watch_save.py:641`, called at `:1481`) | **No** — "must be complete" is a stated rule, nothing evaluates it | a season aggregate: events, held by reason, rejected, corrections, per-team coverage from `relationship_roster_observations`, and a PASS/FAIL on "no missing identities" | S |
| 2 | `relationships` | trust distribution, decay rate, runaway rows | **No.** Trust is a read-time projection per subject (`personality/relationships.py:183`, `:329-390`); `personality/relationship_preview.py:1-30` takes a JSON fixture (offline); the inbox/press project one subject (`pipeline/watch_save.py:1045`, `newsroom/press_agents.py:436`). No trust table is written | **Events yes** (producer step), **trust no reader** | **No** — "no writer/agent ends at ±1 with everyone" is a stated rule, nothing evaluates it | project trust for every persona with events at season end and mid-season, print the distribution, mean decay, and count rows at the cap | M |
| 3 | `profiles` | inventory applied for all 2026 players + each draft class; 100% coverage, no `unknown` origin | **Partly.** `tools/personality_inventory.py:47,58` prints manifest `issues` / `unresolved` for one manifest; `persona_lifecycle_batches` records an applied batch. No league-wide coverage query | **No** — the inventory/apply is a load/CLI step (`pipeline/load_save.py --personality-manifest`, `personality/lifecycle_store.py`), not a per-week step; P5-03 *is* the apply | **No** — "100% coverage" is definable, not computed | coverage per season: every rostered player + the new class has a verified identity and origin; PASS at 100% | M |
| 4 | `evolution` | changes per agent per season (≤ 1, D6) | **Yes, a real report** — `python -m personality.evolution_run report --season S` (`personality/evolution_run.py:185-228`, CLI `:320-330`), written by the run to `verify_output/…/evolution_<universe>_<season>.md` (`:221-228`) | **Yes** — watcher step at OFFSEASON_START (`pipeline/watch_save.py:817`), which the season's offseason walk invokes | **No** — the report lists changes and counts by rule; nothing checks the ≤ 1 cap or the event-driven concentration | one row in the season report: changes, changes/persona (max ≤ 1), by rule | S |
| 5 | `focus_refresh` | focus distribution, churn; plausible spread, not everyone "Financial" | **Yes, a real report** — `python -m personality.focus_run report --season S` (`personality/focus_run.py:283-316`, CLI `:478-500`); prints changes, by cause, JR-set stale, unresolved | **Yes** — watcher step at OFFSEASON_START (`pipeline/watch_save.py:908`) | **No** — no distribution and no concentration rule ("plausible" is undefined) | season-end focus distribution per slot + a concentration bound (e.g. no single focus above X%) | S |
| 6 | `contracts_ambition` | FA acceptance/rejection rates, top-of-market deals, hometown/contender discounts | **No.** Online receipts exist (`engines/personality_contract_store.py:29-45`; the league office's `_dual` receipts record baseline vs adjusted per wave, `pipeline/league_office_personality.py:383-400`, `:488-511`). Offline only: `tools/personality_contract_policy_report.py:1-10` (fictional cohorts, no DB) | **Yes** — re-sign / free-agency / UDFA hooks every offseason (`pipeline/league_office_personality.py:406-420`, `:488-511`) | **No, and the target is missing:** §7.2 cites a "P3-06 FA band"; `data/tuning/decision_targets.json` has no FA/acceptance entry (keys: `career_length`, `coach_changes`, `draft_by_round`, `retirement_age`, `rookie_usage`, `team_movement`, `trades`), and `TUNING_ACCEPTANCE_THRESHOLDS.md:276-282` explicitly rejects FA dollars as a gate, using A5 team movement as the proxy | (a) a season count of ACCEPT/COUNTER/REJECT + top-of-market from the receipts and `contract_policy_decisions`; (b) **the FA band itself**, which is a data task (`nflverse`) | M + L |
| 7 | `contracts_relationships` | same, with attachment/grudge active; **no measurable change unless the relationship is real** | **No.** Same receipts; the paired baseline vs adjusted is recorded per decision by `_dual` (`pipeline/league_office_personality.py:383-400`) and per offer in the acceptance trace (`engines/free_agency_engine.py:54-64`) | **Yes** (same hooks) | **No** — the comparator is an equivalence ("no change unless…"), which needs both arms counted, and the FA band from #6 is missing too | a paired diff per player: decisions identical between arms except where a relationship term is non-zero; then the #6 band | M + L |
| 8 | `development_learning` | per-player progression delta with vs without personality; capped at ±1.5 OVR/season; league shape unchanged | **No season report, but the counterfactual is stored**: each progression checkpoint's personality block carries `shadow_effect` (`engines/progression_checkpoint.py:100`, `:120-140`, `:138`) inside `progression_events.metadata`. Offline demo: `tools/personality_development_demo.py:302` | **Yes** — `progression_weekly` every week (`pipeline/league_office_personality.py:513-537`) | **No** — the cap exists as config (`max_annual_ovr_difference`, `engines/personality_development.py:79`, enforced `:329-373`) but nothing tallies a season; league shape is only in the *stat* report (`engines/tuning/compare.py`), not tied to this feature | season tally per player from `progression_events.metadata->'personality'`, max \|ΔOVR\| vs the cap, and the league-shape delta between seasons | M |
| 9 | `trade_requests` | requests/season, execution rate; real holdout + trade-request band | **No.** Tables `player_trade_requests` / `_decisions` / `_actions`; the only tool is `tools/player_trade_request_preview.py:1-10` (offline fixture). `engines/player_trade_requests.py:21` says ENABLED needs an approved policy | **Yes** — watcher step (`pipeline/watch_save.py:1125`) | **No, and the target is missing:** `TUNING_ACCEPTANCE_THRESHOLDS.md:250-259` lists "Holdouts / public trade requests … a small curated table" as *not yet computable* | counts by cause/tier, executed share, denials by reason — cheap; the **band** needs a curated dataset | S + L (target) |
| 10 | `retirement` | retirement age by position (P3-03 band) | **Yes** — `decision_compare` A2 retirement age and A1 career length, per position group, with PASS/FAIL (`engines/tuning/decision_compare.py:929-952`), written as `decisions_<season>.md` | **Yes** — `season_loop` calls it (`engines/tuning/season_loop.py:365-391`, `:474-484`) | **Yes** — A2 median ±1.5 y, A1 ±1 (`TUNING_ACCEPTANCE_THRESHOLDS.md:104-155`) | the number scored is the **baseline** league's: shadow returns the baseline decision (`_dual`, `pipeline/league_office_personality.py:383-400`) and shadow even runs a review-only `rp.synthetic_policy()` (`:288-300`). To read the *feature* you need the receipts' keep/retire diff | S |
| 11 | `development_opportunity` | young-player snap share, depth-chart outcomes; rookies start at a realistic rate; no 68-OVR rookie benched behind a 34-year-old | **No.** `shadow_adjustments` come back from `engines/personality_coaching.py` and are recorded per chart (`pipeline/league_office_personality.py:538-552`, `:574-607`). A6/A7 in `decision_compare` score rookie usage, but as the draft/depth-chart engines' output | **Yes** — weekly charts on CPU teams | **Partly by proxy** — A6 (`:1082-1110`) and A7 (`:1111-1132`) | snap share per young player (`season_stat_general.downs_played` ÷ the team's plays, `pipeline/load_save.py:305`, `engines/compensatory_picks_run.py:136-137`) and the "68-OVR rookie behind a 34-year-old" query over `depth_charts` + OVR + age | S |
| 12 | `coach_loop` | close-call chart changes, marquee calls/week (≤ 4 model calls/week), chart churn small | **No.** Each chart decision's baseline/chosen/subject is recorded in the run receipt (`pipeline/league_office_personality.py:574-607`); every decision is a row in `agent_loop_decisions` with `choice_source` POLICY/MODEL/MODEL_REJECTED (`engines/agent_loops/base.py:34-46`, `:361-420`) | **Yes** — weekly CPU charts | **No** — the 4/week constant exists (`MARQUEE_PER_WEEK`, `base.py:35`, enforced `:354-356`) but nothing counts a season; "chart churn small" is undefined | CHART decisions by source per week, model share, MODEL_REJECTED, non-NEUTRAL rate, close-window options (`engines/agent_loops/coach.py:71-100`) | S |
| 13 | `player_loop` | counters used, leaks/season (cap 1/player), rejections | **No.** Same `agent_loop_decisions` table (CONTRACT/LEAK/INBOX_REPLY, `engines/agent_loops/base.py:34`); leak caps enforced in `engines/agent_loops/player.py:9-18` (`leak_allowed` 1/player/season, 3 league-wide/week) | **Yes** — contract-counter chooser (`pipeline/league_office_personality.py:452-487`, `:488-511`) | **No** — "holdout frequency band" does not exist (`TUNING_ACCEPTANCE_THRESHOLDS.md:250-259`) | counts from one table (counters, leaks, rejections, model share) + the "acceptance never changes" **diff** P5-09 asks for (compare acceptance with and without the loop — new code) | S + M |
| 14 | `owner_patience` | firings/season, hot-seat duration (P3-06 firing band) | **Partly.** A3 HC changes between seasons and (report-only) mid-season, scored (`engines/tuning/decision_compare.py:973-1014`); the owner terms' shadow is `owner_shadow` in the hot-seat report (`engines/coach_hot_seat.py:394-410`, `engines/gm_rollover.py:428`) but that report is **not persisted** | **Yes** — offseason hot seat + carousel | **Yes for firings** (A3 band 4–10, `TUNING_ACCEPTANCE_THRESHOLDS.md:157-169`); **hot-seat duration not measured** | A3 is the league's firing count, not the feature's effect: add the receipt's `owner_shadow` count (teams the terms would have fired differently) + hot-seat duration by team | S |
| 15 | `owner_hiring` | hire quality, retread share, coordinator→HC rate | **No.** `staff_moves` stores `source` POACHED/PROMOTED/RETREAD/REHIRED/HC_STAFF/OUTSIDE and the HC `inputs` (appeal, opening rank) (`db/init_db.sql:2082-2099`, written `engines/coaching_carousel.py:757`); policy in `engines/owner_policy.py:98-146` | **Yes** — offseason hiring cycle | **No** — "P3-06" has no hire-quality target; GM/coordinator changes are deferred (`TUNING_ACCEPTANCE_THRESHOLDS.md:250-259`) | a report over `staff_moves` (hires by source, retread share, coordinator→HC, GM tiers, `owner_shadow` count) is cheap; **hire quality needs a curated dataset** | S + L |
| 16 | `coaching_tree_hiring` | tree edges used in hires; plausible, no tree dominates | **No.** The carousel builds `report["tree_shadow"]` in memory (`engines/coaching_carousel.py:907-960`, `:1210`); `engines/coaching_tree_hiring.py:39` `tree_mode` gates it | **Yes** — offseason carousel, when a reviewed tree graph exists | **No** — "no tree dominates" is undefined and, worse, unmeasurable | **the tree edge that produced each hire is not stored**, so a season cannot say which tree was used. Needs a `staff_moves` column (or a carousel record) + a report | M (plus the column) |
| 17 | `owner_spending` | cash spend vs cap, facility upgrades; real cash-to-cap ratios | **No.** `engines/owner_policy.py:147-230` (`spending`, `facility_style`, `facility_upgrade_orders`); the data exists — `owner_cash_ledgers` (`db/init_db.sql:2137-2145`) and `team_cap_seasons` (`:447-453`); the shadow receipt records offer counts per wave (`pipeline/league_office_personality.py:615-649`) | **Yes** — free-agency waves | **No** — no cash-to-cap target in P3-06, and no real-NFL cash dataset in the repo | Mu-side ratio per team/season is a query; the **comparator** needs a real cash-to-cap dataset | S + L |
| 18 | `owner_meddling` | frequency, effect size; rare, bounded | **No.** `owner_decisions` stores every VETO/PUSH with `alternative` (`db/init_db.sql:3823-3836`); the shadow path records reviewed decisions in the receipt without writing rows (`pipeline/league_office_personality.py:656-700`); `VETO_CAP` 1/team/season | **Yes** — signings (and HC hires through the hire review) | **No** — "rare, bounded" has the bound (`VETO_CAP`) but no measure | reviewed candidates vs VETO/PUSH per team/season, veto effect (FA rank of the vetoed player), and shadow-mode counts | S |
| 19 | `scout_directors` | report divergence from consensus, board lean; bias visible but not distorting | **No — and nothing to report.** In `shadow` the directors are created but **the board lean and the private report rows are `active`-only** (`engines/scouting_engine.py:707-747`: `active` gates `extra_bias` and the edition's director rows) | **No** — scouting runs from the *season scan* (`pipeline/weekly_capture.py:527`), not from `watch_save`, so `season_loop` never runs it | **No** — "not distorting" is undefined | three things: a scouting step in the shadow season (or a documented one-off run), a shadow recording of the leaned board/edition, then the divergence report | M (design) |
| 20 | `media_voters` | ballot vs article agreement, winner distribution; 100% agreement after P1-12 | **No.** Ballots run only when `mode == "active"` (`newsroom/award_ballots.py:303-312`) and only inside the article path (`newsroom/swarm_dispatcher.py:1467-1507`). P1-12 **enforces** agreement with a rewrite (`docs/CLOSE_OUT_CHECKLIST.md:134`) but stores no agreement figure | **No** — `season_loop` ingests with `--no-articles` (`engines/tuning/season_loop.py:235-243`) and a tuning DB forces `MU_NO_LLM=1` (`pipeline/watch_save.py:1634-1636`); the ballot path never executes | **No** — the 100% rule has a writer-side check, no measurement | a ballot-only shadow entry point (independent of the writer), stored winners per season, then the agreement + distribution report; agreement against real articles needs an articles-on run | M + L |

---

## 3. Findings

### 3.1 The season loop has no personality report at all

`engines/tuning/season_loop.py:24-28` promises "a season report … then the decision comparison".

* `engines/tuning/season_loop.py:474-484` writes `season_<year>.md` (stats) and calls
  `decision_compare` for `decisions_<year>.md`.
* `engines/tuning/decision_compare.py:916-1152` contains only A1–A7 (career length, retirement age, coach changes,
  trades, team movement, draft by round, rookie usage) plus `A1.inflow`.
* Neither reads `personality_feature_modes`, `league_office_runs.summary`, or any of the tables in §2.

The one completed season shows it: P4-01 scored `CAREER-TUNE-BASE26` straight through
`engines.tuning.compare` and wrote `P4-01_compare.md` (`docs/reference/P4_01_SUPERSIM_BASELINE.md` §1) — a real
season, no `decision_compare` report and no feature number.

So §7.1 step 3 — "read the feature's report" — has nothing to read, for any feature. The runbook says it plainly
and then leaves the reader empty: `docs/reference/P5_01_SHADOW_GATE.md:149-160` (Step 6 runs the season, Step 7
says "read the reports") and nothing in Step 6 writes a feature report.

### 3.2 The evidence mostly exists, in tables nothing reads

By the end of a tuning season the database holds, per feature:

| Table | Written by | §7.2 quantity it already holds |
|---|---|---|
| `relationship_producer_runs.counts`, `relationship_pending_events` | `engines/relationship_producers/run.py:377-386` | events, appended/held/rejected/corrections, hold reasons |
| `persona_relationship_events` | `personality/relationship_store.py:207` | the raw material for trust/decay per subject |
| `persona_evolution_runs` / `persona_candidates` | `personality/evolution_run.py` | changes per persona, by rule |
| `persona_focus` + `persona_focus_runs` | `personality/focus_run.py` | the focus each slot held, changes, causes |
| `contract_policy_decisions` + `league_office_runs.summary->'personality'` | `engines/personality_contract_store.py:126`, `pipeline/league_office.py:2696-2697` | per-decision mode/result, baseline vs adjusted per wave |
| `progression_events.metadata->'personality'.shadow_effect` | `engines/progression_checkpoint.py:120-140` | the per-player development delta with vs without |
| `agent_loop_decisions` | `engines/agent_loops/base.py:361-420` | every CHART/CONTRACT/LEAK/INBOX decision, its source and chosen key |
| `staff_moves`, `coach_hot_seat`, `owner_decisions` | `engines/coaching_carousel.py:757`, `engines/coach_hot_seat.py` | hire source, firing decisions, meddling |
| `owner_cash_ledgers`, `team_cap_seasons` | `tools`/`engines/staff_contracts_run.py`, `pipeline/load_save.py` | cash and cap per team-season |

`grep -rl "<table>" tools/ engines/tuning/` finds **no reader** for any of these except `coach_hot_seat` and
`staff_moves`, which `decision_compare` reads for A3. That is the whole gap in one line: the numbers are being
recorded; nobody asks for them.

The single richest unread object is `league_office_runs.summary->'personality'`
(`pipeline/league_office.py:2696-2697`), which every league-office write stores and which carries
`{modes, counts: {"feature:mode:status": n}, receipts: [{feature, mode, status, stage, season, what, baseline,
adjusted}]}` (`pipeline/league_office_personality.py:357-364`). That is the per-feature shadow ledger already.

### 3.3 Four §7.2 comparators do not exist (a target gap, not a report gap)

§7.2 points five features at "P3-06" (`docs/CLOSE_OUT_CHECKLIST.md:343,347,351,352,353`). P3-06 built A1–A7 and
explicitly deferred the rest:

* **`contracts_ambition` / `contracts_relationships` — "P3-06 FA band":** no FA entry in
  `data/tuning/decision_targets.json`; `TUNING_ACCEPTANCE_THRESHOLDS.md:276-282` rejects *free-agent dollars per
  season* as a gate and offers A5 team movement as the proxy. So a shadow season can count acceptances, and there
  is still nothing to compare them to.
* **`trade_requests` — "real holdout + trade-request band":** `TUNING_ACCEPTANCE_THRESHOLDS.md:250-259` lists it as
  not yet computable ("reported events only; a small curated table").
* **`owner_hiring` — "P3-06":** nothing; GM and coordinator changes are deferred there too. Hire *quality* has no
  dataset at all.
* **`owner_spending` — "real cash-to-cap ratios":** no cash dataset in the repo; only the Mu-side tables.
* (`player_loop`'s "holdout frequency band" is the same missing thing as `trade_requests`'s.)

Building a report for these five is worth doing (the Mu-side number is cheap and five features are otherwise
invisible), but **it will not make them tunable** — the checklist rule says a feature without a number is not ready,
and a number without a target is not much better.

### 3.4 Two features cannot be observed in a shadow season at all — a design gap

Both fail the shadow contract in the same way: `shadow` is a no-op for the exact signal §7.2 names.

1. **`media_voters`.** `decide_awards` / `decide_all_pro` return `None` unless `mode == "active"`
   (`newsroom/award_ballots.py:303-312`), and the only caller is the article writer
   (`newsroom/swarm_dispatcher.py:1467-1507`). A tuning season runs `--no-articles`
   (`engines/tuning/season_loop.py:235-243`) and forces `MU_NO_LLM=1` (`pipeline/watch_save.py:1634-1636`), so in
   shadow the panel never votes, no winners are stored, and there is nothing to compare with an article.
   *What would measure it:* a ballot-only shadow path (run the panel, store the winners, apply nothing) plus a
   stored agreement figure — the P1-12 rewrite check already computes agreement in memory
   (`_structured_article_errors`, `newsroom/swarm_dispatcher.py:2064-2108`) and could record it.
2. **`scout_directors`.** In shadow the directors are created but the board lean and the private report rows are
   `active`-only (`engines/scouting_engine.py:707-747`), and scouting is not called from `watch_save` at all — it
   runs in the season-scan capture path (`pipeline/weekly_capture.py:527`). A shadow season therefore writes no
   director-influenced board and no report divergence to read.
   *What would measure it:* run the scouting engine once per class inside the season (or a documented one-off), and
   have shadow record the leaned board / report edition without applying it — the plan's own risk table already
   assumed this ("a shadow report of …"), it was just not built this way.

Two more are half-blocked by the no-model rule rather than by design:

3. **`coach_loop` / `player_loop` model behaviour.** A tuning ingest forces `MU_NO_LLM=1`
   (`pipeline/watch_save.py:1634-1636`), so `make_loop_llm` (`engines/agent_loops/base.py:254-257`) has nothing to
   call and every decision records `choice_source = POLICY` (`base.py:17-20`). The marquee budget and the model
   share §7.2 asks about are therefore **structurally zero** in a normal shadow season. They become observable only
   on a deliberate `MU_NO_LLM=0` run of one week (the override is explicit in `watch_save.py:1634`), which costs
   money and should be a named exception in P5-08/P5-09 rather than an accident.
4. **`coaching_tree_hiring`'s "no tree dominates"** is unmeasurable because the edge that produced a hire is not
   stored (`engines/coaching_carousel.py:989-1010` computes the candidate kind; `staff_moves` stores `source` but
   not the mentor edge). The in-memory `report["tree_shadow"]` is lost when the process exits.

### 3.5 Reports that exist but would mislead if used as-is

Written down as findings, not fixed here:

* **`retirement`'s A2 is the baseline's retirement age, not the feature's.** In shadow the league office returns the
  baseline decision (`_dual`, `pipeline/league_office_personality.py:383-400`) and the policy under test is the
  review-only `rp.synthetic_policy()` (`:288-300`). A2 is a fine league health gate; it is **not** a measurement of
  the `retirement` feature, and P5-07 should not read it as one.
* **`owner_patience`'s A3 likewise.** A3 counts the league's head-coach changes (`decision_compare.py:973-1014`);
  the feature's own effect is `owner_shadow` in a report that is never persisted (`engines/coach_hot_seat.py:394-410`,
  `engines/gm_rollover.py:320-358`). Same trap: A3 can pass while the owner terms would have changed ten firings.
* **`evolution`'s ≤ 1 change/agent/season** looks structural (one decision per offseason), but the report does not
  assert it, so a regression that produced two changes in a season would go unnoticed by the report reader.

### 3.6 One feature is closer than the table suggests

`development_learning` (#8) already stores the exact counterfactual §7.2 asks for — `shadow_effect` per player per
checkpoint (`engines/progression_checkpoint.py:138`) — because its shadow is a real paired computation, not a
baseline pass-through. It needs a reader, not a redesign. The same is true of `contracts_*` (`_dual`'s
`baseline`/`adjusted` receipts), which is why P5-05's report is an M and only its target is an L.

---

## 4. The smallest ordered set of report work that unblocks P5-02…P5-14

Ordered so each item is usable the moment it lands, and so the two D2-first features are measured first. "S" items
are one reader plus a row in the season's report; "L" items are data/targets, not code, and are called out because
**six features cannot be tuned until they exist**.

**Step 0 — the spine (S).** `engines/tuning/feature_compare.py`: one read-only module over the tuning DB that
returns `{feature: [{metric, value, target, verdict}]}` and renders `features_<season>.md`, called by
`season_loop.decision_report` (or a sibling `feature_report`) beside `decisions_<season>.md`, with one **section per
feature that was not `off`** and an explicit `— not measured: <reason>` row for the rest. This is the piece that
makes §7.1 step 3 possible at all, and it is also where every item below adds its rows. Without it each item is
another one-off script.

1. **`producers` completeness + `relationships` trust distribution** (S + M). Unblocks **P5-02** (the only D2 pair
   that must be measured before anything else can go active). Producers: aggregate `relationship_producer_runs` +
   `relationship_pending_events` + `relationship_roster_observations`, with `rejected == 0` and no unresolved
   identity as the pass test. Relationships: project `project_relationships` for every persona with events at
   season end, print the distribution, the share at ±cap, and the decay (mean |trust| mid vs end).
2. **`evolution` + `focus_refresh` season rows** (S). The renderers exist (`personality/evolution_run.py:185-228`,
   `personality/focus_run.py:283-316`); reuse them and add the two numbers the reports lack — changes/persona
   (max ≤ 1) and the season-end focus distribution with a concentration bound. Unblocks **P5-04**.
3. **`retirement` receipt row** (S). Count, from the season's `league_office_runs.summary->'personality'`, the
   players the policy would have retired differently, and their age distribution. Unblocks the `retirement` half of
   **P5-07** with a number that is the feature's own.
4. **`development_learning` season tally** (M). Sum `progression_events.metadata->'personality'.shadow_effect` per
   player per season; print max |ΔOVR|, the distribution, and the year-over-year league-shape delta from
   `compare.py`. Unblocks **P5-06**.
5. **`coach_loop` + `player_loop` + `development_opportunity`** (S + S + S). All three are queries:
   `agent_loop_decisions` by kind/source/week (model share, MODEL_REJECTED, leaks, counters), and
   `season_stat_general.downs_played` ÷ team plays for young-player snap share plus the "68-OVR rookie behind a
   34-year-old" check over `depth_charts`. Add one documented `MU_NO_LLM=0` week to observe model calls. Unblocks
   **P5-08** (both halves) and **P5-09**'s counts; P5-09's "acceptance never changes" needs a separate
   with/without-loop acceptance diff (M).
6. **`trade_requests` counts** (S). Requests, causes, executed share, denials by reason, from
   `player_trade_requests*`. Unblocks the trade half of **P5-07** — reported, **not scored** until its band exists.
7. **The owner family in one reader** (S). Patience: A3 already scored, add `owner_shadow` and hot-seat duration.
   Hiring: `staff_moves` by source (retread share, coordinator→HC rate), GM tiers. Meddling: `owner_decisions`
   plus the shadow receipt. Spending: `owner_cash_ledgers` ÷ `team_cap_seasons`. Unblocks most of **P5-10**.
8. **`coaching_tree_hiring` provenance** (M). Store the mentor edge (a `staff_moves` column or a carousel record)
   and report trees per hire. Unblocks the tree third of **P5-10**; without the column the signal cannot exist.
9. **`contracts_ambition` / `contracts_relationships` paired report** (M). ACCEPT/COUNTER/REJECT counts and
   top-of-market deals from `contract_policy_decisions` + the `_dual` receipts, and the paired with/without-relationship
   diff per player. Unblocks **P5-05**'s report; its verdict waits on the FA band (D1 below).
10. **`scout_directors` and `media_voters` shadow observability** (M each). Two design fixes first: record the
    director-leaned board in shadow, and add a ballot-only shadow path that stores winners. Then the divergence and
    agreement reports. Unblocks **P5-11**; neither feature is measurable today at all.
11. **All-features-shadow step for the cumulative league** (S). A `season_loop` pre-step (or a documented command
    loop over `FEATURES`) that sets every feature `shadow` at one version before the run. Unblocks **P5-14**.

**D — the four missing targets (L each, not report work, and each blocks a pass rather than a report):**

* **D1 FA band** (`contracts_ambition`, `contracts_relationships`): acceptance/counter/reject rates and top-of-market
  counts from `nflverse`. Decide the definition first — the trade-window lesson in
  `TUNING_ACCEPTANCE_THRESHOLDS.md:14-39` is the precedent for how much this matters.
* **D2 Holdout / trade-request band** (`trade_requests`, and the `player_loop` leak expectation): the curated table
  §4 already asks for.
* **D3 Hire-quality dataset** (`owner_hiring`): the deferred §4 item; HC changes stay the proxy until it exists.
* **D4 Real cash-to-cap dataset** (`owner_spending`): no repository source today.

Until D1–D4 exist, those features can be **reported** (the Mu-side number is cheap and worth having) but cannot be
**passed**, and §7.1 forbids tuning them on vibes. Say so in the P5 rows rather than letting a season "review" them.

---

## 5. Proposed checklist row (P5-00) — a recommendation, not an edit

`docs/CLOSE_OUT_CHECKLIST.md` is shared and other sessions own rows; this session did not touch it. The row below is
the one this audit says belongs in §7.3, to be added by whoever owns that table:

| ☐ | ID | Item | Done when | Size |
|---|---|---|---|---|
| ☐ | **P5-00** | **Reports ready for every feature.** Before a feature's P5 item starts, its §7.2 signal must have a report that prints it from the tuning DB **and** a target to read it against: `python3 -m engines.tuning.feature_compare --dsn <tuning DB> --season S` writes `features_<season>.md` beside `decisions_<season>.md`, one section per feature that was not `off`, each row carrying its target or `— not measured: <reason>`. A feature with no report **or no target** is not started; it is listed as blocked with the missing piece named (see `docs/reference/P5_REPORT_READINESS.md`). | `data/tuning/seasons/<run>/features_<season>.md` covers every feature started in that run, and every row names a target or an honest "not measured". | M |

---

## 6. How to re-check this document

```bash
# the 20 features and the order
python3 -c "from personality import features as f; print(len(f.FEATURES)); print(f.FEATURES)"

# the season writes two reports and nothing else
grep -n "def report\|decision_fn\|out\[.report.\]" engines/tuning/season_loop.py

# no personality read anywhere in the tuning layer
grep -rn "personality\|feature_modes\|summary->" engines/tuning/*.py

# the comparators that are said to be missing
python3 -c "import json;print(sorted(json.load(open('data/tuning/decision_targets.json'))))"

# the shadow no-ops: the panel votes only when active, and the lean/report rows are active-only
grep -n 'mode == "active"' newsroom/award_ballots.py
grep -n 'active = out.get\|extra_bias=extra\|if active else None' engines/scouting_engine.py

# the stored-but-unread evidence
grep -rn "summary->'personality'\|summary\[.personality.\]" --include=*.py .
```

When a report lands, the row for its feature in §2 should move from "No" to the module and CLI that now prints it,
and the count in §1 should be updated in the same commit.
