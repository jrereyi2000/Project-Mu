# Project Mu: NFL Weekly Mirror Plan

**Status:** built and on main (2026-11-11), approved by JR (2026-11-11); waves 5–6 done (U1–U9 and U12 merged; U10 and U11 merged as follow-ups; the audit follow-ups U13–U19, approved by JR on 2026-11-11, all merged: U13, U14, U15, U17 and U19 built here, U18 built by `builder-9` as the U6 F1–F5 batch and delivering U16; U17 is scoped to F3's mechanical half, its policy question stays open with JR (Q2); audit-5's close-out follow-ups C1 and N1 are fixed (`f8cdc4e`, `a641efa`), and its residual review CLOSED U18 R1–R4 and U8 F1/F2; spikes A–C run with JR on 2026-11-11 — S18–S21). Waves 2–6 are handed to a DeepSeek session (JR's /execute-plan rule, 2026-11-11).
- As built (wave 4, U7): `pipeline/watch_save.py` gains `mirror_week_active` (the save's own season/stage/week through `store.mirror_active` on a 5 s connection; a snapshot with no `players` reads "not a mirror week" without connecting, which keeps the untouched `tests/test_watch_save.py` green under conftest's BaseException DB guard), `mirror_week_notice` (N = the week's WRITTEN moves; M = the real week's unconfirmed switches from the cached nflverse files, else the planned unconfirmed RELEASE moves) and the `nfl_mirror_week` result key; `pipeline/weekly_capture.run_weekly_engines` gains `gm=True`, with `gm=False` skipping `step_gm`; `engines/nfl_mirror/__main__.py` is the five-command CLI. Deviation: the press-conference skip lives in `run_ingest` (where the child's result is read), not literally in `watch_loop`. `preview` deliberately stops at the roster diff — it does not plan created players, injuries or staff changes, because those only exist on the league office's write path.
- As built (wave 3, U6): `pipeline/league_office_mirror.py` holds the adapter (`mirror_week` → `MirrorWeek`, `mirror_restore`, `chart_override`, `bind`, `install`) and `pipeline/league_office.py` runs it in `plan_stage` where the engine-trades step sits, plus `step_nfl_mirror`, `_record_mirror`, `_record_mirror_transactions`, `Plan.switches`, `PRIORITY_MIRROR`, and the merged chart override in `step_coach_charts`. Deviations: (a) `store.mark_written` still flips every PLANNED move of the week to WRITTEN, including a move `Plan._claim` refused, so `nfl_mirror_moves` can call a refused move WRITTEN though the save never got it — store-level, outside U6's files; **fixed by U16, delivered in U18** (`mark_written(..., outcomes=...)` records each move's outcome, so a refused move stays REFUSED); (b) a fetch failure fails the whole plan (nothing is written) rather than falling back to the engine-trades step, as the module docstring says; (c) the week's `transactions` rows use `txn_date = '<season> SEASON - WEEK <week>'` and `raw_text` `NFL mirror`, the same shape the other producers write. Merged from a scratch worktree branch and fast-forwarded onto `main` because the shared checkout held another session's staged batch (no conflict, none of U6's files touched).
- As built (wave 5, U13): `_save_week` returns an `int` subclass carrying `.year` and reads the Pro Bowl as Mu week 22 (it used to read None), `_mode_reader` gained a `PRO_BOWL` mode (not LIVE), and `catch_up` now refuses a save whose calendar year disagrees with the settings before the PC, re-reads the settings and requires the current week WRITTEN before every sim, stops ahead of `last_week`, and quits/copies through the Pro Bowl to the Super Bowl without waiting. Deviation: the Super Bowl → offseason transition is out of scope — a `to_week=None` run that sims the final Super Bowl still stops on the exactly-one guard (offseason reads no week); it needs its own end status. Also merged in wave 5: U14 sets the contract window end to 2027-03-01 (the 2027 league year opens in March) and keeps `year_signed != 2026` for date-less rows; U15 refuses a later EA page carrying another iteration rather than letting the newest iteration win, and a failed refresh leaves the cache untouched.
- As built (wave 5, U17): `staff.py` pre-indexes the changes that will really write a seat (`_named_seat_targets`) and, for every HC change, looks up the promoted man's current OC/DC seats with the same `_current_coord` tie-break `_wanted` uses (`_current_coordinator_seats`). A change in the same list naming another man for that team + role resolves the seat; with none, `apply_staff_changes` returns a REFUSED STAFF move (`promoted to HC with no named DC replacement`) and leaves the tenure rows untouched — repeated applies keep returning it, so a re-plan cannot lose the refusal. Cross-team promotions report the source team's seat. Q2 stays open: no successor is invented. Verified on main with `offseason_check e2e` on CAREER-MUBASE (41 PASS / 4 INFO, original sha256 unchanged).
- As built (U17 follow-up, audit-5's C1): before any coach row, profile move or tenure write, `apply_staff_changes` checks whether an HC change's incoming man currently holds a coordinator seat on a user team (`_protected_promotion_source`, the same `_current_coord` tie-break `_wanted` uses) and, if he does, returns the destination change as a SKIPPED STAFF move (reason `promotion would take user team <abbr>'s current <role>: Mu never changes JR's staff`, `detail.protected_seat` naming the seat and person) instead of promoting him. Without that guard the carousel's D2 rule moved that team's one `coach_madden_profiles` row from COORD to COACH, leaving its current coordinator without a profile even when the named replacement was skipped for ownership. Ordinary non-user named replacement, unresolved-seat refusal, idempotency and Q2 are unchanged. Verified offline (four new cases, three of them failing on the pre-fix tree) and on scratch PostgreSQL (with and without a named replacement, both change orders); the auditor's `data/audit_reports/audit-5/closeout/jr_source_repro.py` case now keeps COORD 101 and writes nothing. `offseason_check e2e` on CAREER-MUBASE: see the Build log row.
- As built (U18 follow-up, audit-5's N1): `Plan.trade_player` now writes the plan's virtual status canonically — `Signed` after a trade promotes the player to the destination's Roster, `PracticeSquad` after a demotion, his unchanged status without a list change — instead of the native list name `Roster`, which stays only in the writer payload's `lists` entry. Every `Signed` reader therefore sees the promoted man in the same write: the mirror's real-chart filter (`league_office_mirror.py:199`) places him in the destination's real depth slot rather than leaving him off the chart while the writer put him on the active roster. Verified offline and on a CAREER-MUBASE copy through the production diff, Plan and writer (a promoted trade brings WR 14668 into the same write's CHI WR chart; same-team promotion, `Roster`→`PracticeSquad` and unchanged-list controls), with both new assertions demonstrated failing on the pre-fix tree (`('Roster', 0)` where `('Signed', 0)` is expected). `offseason_check timelines` 39 PASS and `e2e` 41 PASS / 4 INFO, source sha unchanged; see the Build log row.
- As built (wave-5 follow-up, audit-5's R1/R2): the catch-up re-reads `nfl_mirror_settings` after the readiness wait as well as before it, so a disable, a season move or a narrowed `last_week` during the wait stops before the save is loaded; and with the production reader every header read (initial, reload, post-sim) must carry a readable year matching the settings — a header that loses its `calendar_year` stops before the sim or, after a sim, before the quit, the save copy and the acknowledgment. Verified against the auditor's `data/audit_reports/audit-5/wave5/gap_repro.py` cases (both now stop with 0 loads / 0 sims / 0 quits) and six new tests in `tests/test_madden_pilot_catch_up.py`.
- As built (wave 5, U19): `enable`/`disable` check `--confirm` against the database the opened connection resolved (`conn.info.dbname`, else `SELECT current_database()`) and run the update on that same connection, so a repeated `dbname=`, a URL query override or a service default that disagrees refuses with exit 2 and commits nothing. `preview` binds its capture to the database's `league_state.madden_league_id`, season and stage (regular 1–18, playoff 19–23), accepts `--snapshot PATH` only after checking the same identity, and refuses before fetching when nothing matches; the unbound `_newest_snapshot()` was removed. The status next week skips Mu 22 (21→23, 22→23, none after 23). Verified with 50 tests against a real `mu_scratch_u19_test`, created and dropped by the test.
- Open from the wave-3 review: `pipeline/league_office_mirror.py`'s `_injuries` in CATCH_UP uses `weeks_until_back` against the later weeks' statuses; a player the save has injured but the real week has healthy gets `HEAL_FIELDS` — checked in `tests/test_league_office_mirror.py`, not against a real save.
- As built (wave 4, U8, merged early at `7f007cd`): `catch_up(pilot, save=None, to_week=None, dsn=None, progress=None, *, watcher=None, folder=None, store=None, mode_for=None, week_reader=None, load=None, sim=None, quit_to_menu=None)` in `tools/madden_pilot/jobs.py`, its `catch-up` CLI subcommand and `DryMirrorStore` dry-run, with `quit_franchise` split into `_quit_now` plus a wrapper so the loop shares the quit body. Refuses the four §2.12 cases before the PC is touched; stops at the first LIVE week or `--to-week`, as Stuck on a FAILED week, a two-week jump, a wrong loaded week or the week cap.
- As built (wave 1): `store.mark_written` takes `(cur, season, week, run_id)` and also flips the week's PLANNED moves to WRITTEN; `record_moves` has `replace=False`; `build_week` takes an optional `sources=`. The migration is `2027_02_27_nfl_mirror.sql`. It was renamed from `2027_02_26_…` after the merge at audit-2's request (M-0444), because builder-8's `2027_02_26_league_shape_season.sql` was already on main. Neither migration is applied to any franchise DB; only `mu_scratch_*` DBs were used.
- As built (wave 2, U3): `plan_rosters` and `ContractBook` as §2.5. `contract_for` takes an extra optional `years_exp=` kwarg (diff passes the real player's entry year, so the minimum scales on real experience rather than the OTC draft year). A snapshot's `college` is a `{table, row}` reference, so the new-link phase resolves it through `pipeline.colleges.college_name` before `link.match`. The S5a cross-position name-only fallback was built in U12 (see the U12 Status bullet): it is a review stage in `plan_rosters`, not a `link.match` change.
- As built (wave 2, U4): `weeks_until_back`, `estimate_weeks`, `injury_fields` / `HEAL_FIELDS`, `real_chart` and `merge` as §2.6. Deviation: the plan's `HEAL_FIELDS` used `HEALTHY = ("Uninjured", None)`, but `pipeline/save_reader/lib.mjs:168-172` refuses `None` and every `_`-suffixed enum member (`Invalid_`, `Max_`), so a heal writes only `InjuryStatus` `Uninjured` plus zero durations, and an unmapped body part (concussion, calf, illness, neck) leaves `InjuryType` alone instead of writing `Invalid_`. Verified against the real save's `fieldMeta` on CAREER-2026SB.
- As built (wave 2, U5): `ea_ratings.py` and `create.py` as §2.8. Deviation: college is a reference field, so `created_edit` carries `real.college` in the target and the CREATED-link evidence but never writes it; only height, weight, age, years pro and jersey go into the `players` edit. `pick_donors`'s `links` may be `{gsis_id: pgid}`, `{pgid: gsis_id}` (None = a link whose person has no 2025–2026 roster row, so eligible) or a set of blocked pgids — U6 must pass only links to people with a roster row. Age is counted on a fixed 2026-09-01. EA's live page was read once for research to pin the shape; tests use only the fixture.
- As built (U10, audit F1/F2): as §2.5 with the F1 leg test and the F2 team/as-of scoping. Deviations: `contract_for`'s `team` is optional — `team=None` keeps the old no-team-filter behaviour because the existing tests and the auditor's F2b repro call it without a team, while `plan_rosters` always passes the real player's team. A mirrored week's contract as-of boundary is derived from the 2026 opener (2026-09-09; the last day of week *n* is `opener + 7(n−1) + 6`), because `RealWeek` carries no date and `real_state.py` is outside the unit; U6 should pass an exact week date once the real state carries one. OTC nicknames map through a local `OTC_TEAM_CODES` table (sourced from `db/seed_clean_db.py` `TEAMS_DATA` and checked against the real parquet); split rows such as `SEA/LV` map to no franchise. Trade-leg dates are no longer used as signing dates.
- As built (U12, S5a/P1): `plan_rosters` adds a review stage after the new-link phase. A still-unlinked real player whose normalized name matches exactly one save player (linked or not; retired/draft rows excluded) in a different position group, with agreeing entry-season (snapshot `years_pro`) or real-2026-team evidence, is reported on `MirrorRosterPlan.review` as `candidate` with `gsis_id`, `pgid`, both positions, the agreeing evidence and a `why` explaining it was not auto-linked; two or more same-name save players are reported `ambiguous` with every candidate (`pgid` + position). Candidates are never auto-linked and are kept out of `creates` (`ps_fill` unchanged, so a review candidate whose only real place is a practice squad still fills a PS slot). The new-link phase now passes `link.read_overrides()` to `link.match`, so an accepted `nfl_link_overrides.csv` row links once and the person stops being a candidate; `link.match` itself is unchanged, and `plan_rosters` is no longer fully pure (it reads that committed CSV). U6 must persist and report `plan.review`.
- As built (U11, I1 / JR's 2026-11-11 decision): a `sign` item with `"switch": true`, whose pgid is also in `"release"` and which signs to a different team, is accepted as one weekly move; every other same-player double move is still refused (no flag, switch to the current team, switch sign with no release, a switch that is not exactly one release + one sign, release+trade, sign+trade, two trades). Deviations: the signing takes a different PlayerContract row rather than the one the release freed (`planSign` normally reuses a same-write freed row; `avoidSwitchDealReuse` re-points any signing that landed on the switch player's old row), so the old deal reads back exactly as a release-only write leaves it; when a valid switch is present the release is applied in memory before `planSign` runs (physical apply order stays staff → release → sign → trade → depthCharts); `moveConflicts(edits, currentTeams = new Map())` is now exported and the report lists `switches`. The 200-move test now compares the counts' change to the roster change instead of counts == list length, because CAREER-MUBASE carries IR players in `SalCapRosterSize`; both samples still pass. Still open for U6: `Plan._claim` must allow the flagged pair.
- Open from wave 1: (b) `staff.py` writes no `staff_moves` or `staff_contracts` rows for mirrored coaching changes, and a coordinator promoted to interim HC keeps his coordinator tenure unless the CSV names his replacement.
- Wave 1 audit (M-0473, audit-3): PARTIAL. F1 (a matched existing HC/OC/DC with no Madden profile is booked but skipped downstream) and F2 (a same-season A→B→A return loses to the interim in every reader) are fixed as a follow-up unit U9 (`9425923`); F3 (promotion with no named replacement) is an open policy question for JR and is not being implemented. Left over from F2, now fixed: `engines/staff_contracts_run.current_staff` ordered its three tenure reads (HC, OC/DC, GM) by `start_season DESC, id DESC` only, so it would pick the interim on an A→B→A return; all three now put `(end_season IS NULL) DESC` first (`caa3766`, two real-PG tests that fail on the parent, timelines 39 PASS).
- Wave 2 audit (M-0497, audit-3; `data/audit_reports/nfl_mirror_wave2/report.md`): U3 PARTIAL, U4's pure/schema checks PASS. F1 (a CSV trade leg matched on gsis and week only, so a stale or wrong-destination leg suppressed later cuts, promotions and switches) and F2 (the OTC lookup ignored the recipient team and accepted a deal dated after the signing) are follow-up unit U10. P1 (S5a's cross-position review stage was left to `link.match`, which cannot supply it) is U12. I1 (a real team switch needs release + sign in one week, which both `Plan._claim` and `save_writer.moveConflicts` refuse) is JR's 2026-11-11 decision: extend the save writer with a declared `switch`, follow-up unit U11, and U6 then allows the pair in `Plan`.
- Orchestrator fix: (a) `coaching_carousel.staff_in()` and `head_coaches()` now break same-season tenure ties with `ct.id DESC` (`7d83d14`, two integration tests, `timelines` 39 PASS, `e2e` 41 PASS).
- Orchestrator fix: `tests/test_nfl_history_store.py` now leaves `nfl_mirror_*` out of its nfl_* table check (`346e063`).
**Builds on:** PROJECT_SPEC §2.21a (League Office), §3.3x (Real NFL history and `nfl_player_links`), §6.0b (save watcher), §2.19 (coaching tenures), [`REAL_NFL_HISTORY_PLAN.md`](../done/REAL_NFL_HISTORY_PLAN.md), [`COACH_SAVE_WRITES_PLAN.md`](../done/COACH_SAVE_WRITES_PLAN.md).

**Goal:** Before each simulated week of the 2026 season, make the save's rosters match the real NFL's for that week. That covers signings, releases, trades, practice squads, IR, injuries, depth charts and head-coach and coordinator changes. Madden still sims the games. First we catch up on the real weeks already played, one simulated week at a time, with the Madden pilot doing the simming. After that we go live, one simulated week per real week, through the Super Bowl. During catch-up Madden's own injuries are off and the real ones are copied in with their real return dates. Madden's injuries come back on at the first live week. Mirrored weeks get no articles, but their simulated games count in Mu's 2026 history and record book. Real players missing from the save are created with their EA Madden 27 ratings when EA has them, and with position averages by role when it doesn't. From the 2027 offseason on, Mu's engines run the league as they do now.

**Delivers:**
- `engines/nfl_mirror/`: `fetch.py`, `real_state.py`, `trades.py`, `store.py`, `staff.py`, `diff.py`, `contracts.py`, `injuries.py`, `depth.py`, `ea_ratings.py`, `create.py`, `__main__.py` (CLI `python3 -m engines.nfl_mirror status|preview|trades|enable|disable`)
- A pilot job, `catch-up` (`tools/madden_pilot/jobs.py`): it sims the real franchise one week at a time while the mirror is in catch-up, waiting for each week's league office write between weeks
- Migration `db/migrations/2027_02_27_nfl_mirror.sql`: `nfl_mirror_settings`, `nfl_mirror_weeks`, `nfl_mirror_moves`; `nfl_player_links.method` gains `CREATED`
- Hand-kept inputs: `data/history/nfl_trades_2026.csv` and `data/history/nfl_staff_changes_2026.csv`
- A league office step, `step_nfl_mirror` (`pipeline/league_office.py`), plus its adapter `pipeline/league_office_mirror.py`
- Watcher gating: in a mirrored week, no GM trade proposals, no articles and no press conferences

**JR's answers (2026-11-11):**
- D1. Catch up on the real weeks already played, then go live one week at a time for the rest of 2026. 2027 onward is Mu's engines as planned.
- D2. Injury lengths are estimates; in live weeks we can't know the future.
- D3. Madden's own injuries are turned off completely in the save for the mirrored weeks, not just set to slider 0.
- D4. Real players with no Madden record: create active and IR players by reusing spare records. Skip practice-squad-only players and fill the slot from Madden's own free agents.
- D5. Depth charts: real nflverse order for linked players, with Mu's depth-chart engine filling the rest of Madden's 35 slots.
- D6. Real 2026 coaching changes (midseason firings, interim head coaches, coordinator changes) are mirrored too.

**JR's answers (2026-11-11, second round):**
- D7. Catch-up weeks use the real return date (hindsight), not an estimate.
- D8. The Madden pilot sims the catch-up weeks on the real franchise.
- D9. A created player takes his EA Madden 27 ratings when EA's ratings database has him, and position averages by role otherwise.
- D10. Madden's injuries come back on as soon as the catch-up reaches the current real week, at the first LIVE week.
- D11. The simulated games of mirrored weeks count in Mu's 2026 history and record book.
- D12. In LIVE weeks (Madden's injuries on), the mirror copies only real IR, PUP and NFI placements and activations. It doesn't copy the weekly Out/Doubtful reports, and it never heals a player Madden hurt.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | nflverse publishes `roster_weekly_2026`, `injuries_2026` and `depth_charts_2026`. All three covered weeks 1–10 and were updated 2026-11-11 07:00 UTC (checked this session). Contracts (`historical_contracts.parquet`, OTC) were updated 2026-11-10. `trades.csv` was last updated **2026-05-19**. | Rosters, injuries, depth charts and contracts can be fetched. Trades can't: they come from a hand-kept CSV (§2.3). |
| S2 | Weekly roster statuses at week 10: `ACT` (A01 1,335; I01/I02 = inactive 144), `DEV` (practice squad, P01–P07 471), `RES` (R01 IR 227, R48 designated for return 28, R04 PUP 18, R05 4), `CUT`, `RET`, `EXE`. Only 28 teams have week-10 rows. | Teams on a bye have no rows that week, so `real_state` carries the last week forward. `RES` codes map to IR; `DEV` maps to Madden's `PracticeSquad` list. |
| S3 | Injury reports carry `report_status` (Out 588, Doubtful 43, Questionable 590, blank 1,620) and body-part text, but no length. | Lengths are estimated (§2.6). |
| S4 | `engines/nfl_history/link.py:321` `match()` links Madden pgids to gsis ids, stored in `nfl_player_links` (§3.3x). It linked 97.3% of veterans on CAREER-WEEK1. Its `method` CHECK allows only `EXACT, ESPN, ALIAS, FUZZY, OVERRIDE` (`db/migrations/2027_02_04_nfl_history.sql:152`). | The mirror keys every player on these links. Created players need a new method, `CREATED`. |
| S5 | On `CAREER-MUBASE` (this session, crude name match): 148 of 1,439 real week-1 active players sit on a different team, or in free agency, in Madden. There are 332 team joins after week 1 (58 active, ~227 team to team). | Week 1 alone is ~150 moves. |
| S5a | `link.match` run offline (snapshot positions, roster_weekly people) on `CAREER-MUBASE` against the 2,621 real players rostered at week 10: 2,201 linked, 420 unlinked. 16 of the 420 have a same-name Madden player at another position, e.g. Travis Hunter (CB in Madden, WR in nflverse) and Connor Heyward (TE vs RB). The other 404 are absent from the save. By role on the newest nflverse depth chart: 10 starters, 46 backups, 50 deeper reserves, 45 IR or reserve, 253 practice squad. 360 of the 404 went undrafted, and 174 are 2026 rookies. | Creation covers about 150 players (D4: everyone but the practice squad). `diff.py` (U12) tries a name-only match across position groups and reports a unique cross-position save player as a `review` candidate (or an ambiguous name with every candidate), so a position change isn't treated as a missing player and is never auto-linked. |
| S5b | EA's public ratings page (`https://www.ea.com/games/madden-nfl/ratings?page=N`, 100 per page in its `__NEXT_DATA__`) serves Madden 27's newest weekly iteration (`madden-ratings-week-8` on 2026-11-11): 1,958 players, each with position, archetype, team, birthdate, height, weight, college, jersey and 55 ratings. The `drop-api.ea.com/rating/madden-nfl` JSON endpoint still serves Madden 26, and asking it for Madden 27 iteration ids returns nothing. Of the 404 absent players, EA has **119**: all 10 starters (e.g. Aaron Donald DT 95, kickers Dominic Zvada 75 and Drew Stevens 74), 44 of 46 backups, 48 of 50 deeper reserves and 17 of 45 IR. It has none of the 253 practice-squad players. | Our save is the launch roster, so EA's weekly roster updates added these players after it. `ea_ratings.py` reads the newest iteration and the creation path uses it first (D9); position averages cover the other ~32 non-practice-squad players. |
| S6 | `save_writer.mjs` accepts `players` (any Player field), `release`, `sign`, `trade`, `depthCharts`, `records` and `staff` (`pipeline/save_reader/save_writer.mjs:8-35`). Injuries are Player fields: `InjuryStatus, InjuryType, InjurySeverity, InjurySide, Min/MaxInjuryDuration, IsInjuredReserve` (`pc_save_reader.mjs:464-466`). | Every mirror move fits the existing writer, and no new `.mjs` is needed. Giving a healthy player a real injury is a `players` edit. Spike B checks that Madden honours it. |
| S7 | There is no player-create block. `pipeline/madden_player_edit.py:151` `player_edit(donor_row, target, new_name=, age_add=, commentary_id=)` builds a full rewrite of an existing record (name, position, archetype, ratings, commentary ID). | D4's creation reuses a spare record through `player_edit` (§2.8). |
| S8 | `plan_stage` (`pipeline/league_office.py:2184`) runs: JR's pending moves, engine trades (`step_engine_trades`, `:1345`, PRIORITY 1), accepted offers, drift write-back, then IN_SEASON `step_injuries` (`:1502`) and waivers, then coach depth charts (`step_coach_charts`, `:1554`) and `step_legality` (`:1528`, at most `MAX_ACTIVE` 63, `:222`). The first claim on a player stands (`:245-249`). | The mirror step takes the engine-trades slot, so drift and stage moves can't undo it. `step_injuries`, waivers and engine trades are skipped in a mirrored week. Depth charts come from the mirror for linked starters. |
| S9 | `step_staff` runs only in `STAFF_STAGES` (offseason, `pipeline/league_office.py:210`). `engines/coach_save_sync.py:104` `_wanted()` reads `coach_tenures` / `coordinator_tenures` covering the target season, latest start first, and its `plan_staff_edits` emits release / assign / create. Spike C (2026-11-10) showed a created head coach works in game (`pipeline/save_reader/staff.mjs:42`). | Coaching mirror = write the real changes into the tenure tables, then run `step_staff` in a mirrored REGULAR_WEEK too. |
| S10 | Tenures are season-granular: `coach_tenures(start_season, end_season)` (`db/init_db.sql:679-687`). | A firing during 2026 ends the old tenure at 2026, and the interim's tenure starts in 2026 with a higher id, so `_wanted` picks the interim. No schema change is needed for the tenure itself; `nfl_mirror_moves` records the week. |
| S11 | Each weekly ingest runs `run_weekly_engines` (GM proposals `step_gm`, `pipeline/weekly_capture.py:442`, `:700`), then the league office, then `step_articles`, then press conferences (`pipeline/watch_save.py:1418-1440`, §6.0b). | In a mirrored week the watcher skips `step_gm`, the articles and press conferences. The stats engines still run, because the games are still Madden's and they count. |
| S12 | `bootstrap_untracked_players` (`pipeline/league_office.py:2082`) seeds progression state for players Mu has never tracked, before progression plans. | Created players are picked up by progression with no new work. |
| S13 | `league_setup.mjs` already writes LeagueSetting fields (`Field_44` CoachFiring, `Field_82` career clock; `pipeline/save_reader/league_setup.mjs:29`). The Injuries on/off field is not known. | Spike A finds the field. It is then written with a `records` edit. |
| S14 | `contracts_engine.minimum_salary(accrued_seasons, cap)` (`engines/contracts_engine.py:126`); pyarrow 26 and pandas 3.1 are installed. | A midseason signing takes the real OTC terms when found, else the minimum for his years of service. |
| S15 | The latest migration is `2027_02_25_madden_stat_counters.sql`; no plan names `2027_02_26`. | Migration `2027_02_27_nfl_mirror.sql`; re-check at build time. |
| S16 | The pilot's `sim_season` job sims only `CAREER-TUNE-*` saves and refuses while the watcher runs (`tools/madden_pilot/jobs.py:18-21`, `:767-771`). It has `_sim_one_week` (`:501`), a save-header week reader (`_save_week`, `:416`), and `quit_franchise` with `allow_quit` (`navigate.py:254`). The league office replaces the save only while the franchise is closed (§2.21a). | D8 needs a separate job, `catch-up`, that does the opposite: the real franchise, with the watcher running. Each week it loads the franchise, sims one week, saves, quits to the main menu, waits for the league office's WRITTEN run for the next week, and reloads. The TUNE-only rule on `sim_season` stays as it is. |
| S17 | `pc_save_reader.mjs` run on `CAREER-DEBUGWEEK12` (2026-11-11, no PC use: the reader opens a temp copy) gives the injury enums Spike B was to dump. `InjuryStatus`: `Uninjured`, `Injured`. `InjurySeverity`: `Invalid_`, `Max_`, `SeasonEnding`, `GameEnding`, `CoupleGames`, `SeveralGames`, `CoupleQtrs`. `InjuryType`: `Invalid_` plus 45 real values, e.g. `KneeACLCompleteTear`, `AnkleDislocated`, `BackRupturedDisk`, `KneeKneecapFracture`, `HandFingerBroken`, `AnkleAchillesTear`, `AnkleBroken`, `RibPectoralTear`, `KneeMCLCompleteTear`, `HandWristDislocated`, `LegQuadStrain`, `KneePCLSprain`, `LegGroinTear`, `LegTibiaBroken`, `AnkleHighSprain`, `ArmTornTricep`, `HipPointer`, `KneeACLSprain`, `ArmTornBicep`, `HipFracture`, `HandThumbBroken`, `BackStrain`, `ShoulderDislocation`, `ShoulderRotatorCuffTear`, `FootStressFracture`, `HandBroken`, `LegHamstringTear`, `LegFibulaBroken`, `FootFracture`, `ElbowDislocate`, `FootTurfToe`, `KneeDislocated`, `ShoulderStrain`, `FootBrokenToe`, `LegBrokenFemur`, `ArmUpperFracture`, `LegGroinPull`, `HipDislocation`, `ElbowFracture`. `InjurySide` is `NA` on every player in that save. `CAREER-MUBASE` has no injured players at all. | U4's `BODY_PART_TO_TYPE` and its fallback are written against these names; Spike B only has to settle whether a written injury is honoured while injuries are off (R2). |
| S18 | Spike A (2026-11-11, `CAREER-TUNE-P2H`, JR at the PC): toggling League Settings' two injury switches and diffing `league_settings_dump.mjs` snapshots names them — **`Injuries` = `LeagueSetting#4290` row 0 `Field_100`**, **`Practice Injuries` = `Field_110`**, each `1` = on, `0` = off. Practice injuries can add injuries during the week, so a mirrored week turns both off (D3) and the first LIVE week turns both on (D10). | `madden_injuries_field` stores both fields: `{"table": "LeagueSetting", "tableId": 4290, "row": 0, "fields": {"Injuries": {"field": "Field_100", "off": 0, "on": 1}, "PracticeInjuries": {"field": "Field_110", "off": 0, "on": 1}}}`. |
| S19 | Spike B (2026-11-11, `CAREER-TUNE-SPIKEB` cloned from `CAREER-TUNE-P2H` with both injury switches off, JR at the PC): a written 3-week `LegHamstringTear` on Josh Allen (pgid 13197) is honoured with Madden's injuries off — he sat out week 5, the save reads `Injured`, and `Min/MaxInjuryDuration` fell 3 → 2 after week 5's sim and 2 → 1 after week 6's, so Madden runs the return clock itself. A heal on Khalil Shakir (22074) cleared `InjuryStatus` and the durations but he still did not play: the heal left `IsInjuredReserve` true and his WR slot off the depth chart. | R2's fallback (what to do if Madden ignored written injuries with injuries off) is unnecessary — the injury fields alone are honoured. A heal is not sufficient on its own; see S20. |
| S20 | Spike B2 (2026-11-11, `CAREER-TUNE-SPIKEB2` from `CAREER-TUNE-SPIKEB`, JR at the PC): with Shakir's `IsInjuredReserve` set false and his WR slot restored, one more sim gave him 4 receptions for 62 yards and a TD, `GAMESPLAYED` 4 → 5 and `RECEIVEYARDS` 217 → 279. | A healed player takes the field only when the save also clears his IR flag **and** puts him back on the depth chart. `diff.plan_rosters` already emits the `activate` op and `depth.real_chart` drops IR players, so U6 must write the heal and the activation in the same week's write and build the depth chart from the post-heal roster, not the raw snapshot. |
| S21 | Spike C (2026-11-11, `CAREER-TUNE-SPIKEC` from `CAREER-TUNE-P2H`, JR at the PC): a free-agent donor (pgid 21710, Shane Buechele) rewritten through `player_edit` as "Marcus Vance", QB, age 23, 6'3" / 220 lb, jersey 7, signed to CHI for 1 year at $1.05M, survived a simmed week with every written field intact — name, position, jersey, height/weight, college link, `overall` 78, ratings and contract — with no crash and no Madden rewrite of his ratings. He took no snaps (second QB), so the week proves the save tolerates a created player rather than that he plays. His `appearance.asset_name` and `commentary_id` stay the donor's (`BuecheleShane_21710`, 5129). | U5's create path is sound: the writer, `player_edit` and a sim week all accept a created player, so §2.8's donor rewrite needs no fallback. U5 records the donor's asset/commentary IDs as a known cosmetic limitation. |

## 1. Scope

**In:**
- Fetching the real week's rosters, injury report, depth chart, per-game head coach (`games.csv`) and OTC contracts, cached with sha256 receipts.
- Planning one week's moves from the save snapshot and the real week: release, sign (roster or practice squad), trade (from the hand CSV), IR on/off, injury fields, depth charts, and creation of missing active and IR players.
- Copying real injuries into catch-up weeks with their real return dates (D7). Live weeks follow Q1.
- Turning Madden's injuries off at the first mirrored write and back on at the first LIVE week's write (D10).
- Creating missing active and IR players from EA's Madden 27 ratings, falling back to position averages by role (D9).
- The pilot's `catch-up` job (D8).
- Mirroring head-coach changes from `games.csv` and coordinator changes from the hand CSV into the tenure tables and the save's staff block.
- Gating in the league office and the watcher: no engine trades, waivers, GM proposals, articles or press conferences in a mirrored week.
- CLI `status / preview / trades / enable / disable`, plus a markdown report for each week under `verify_output/nfl_mirror/`.

**Out (non-goals):**
- Real game results or real stats. Madden sims every game.
- Real ratings. Progression still owns them.
- Real draft-pick ownership in the save. Pick legs of real trades go into Mu's books only, as engine trades do today (§2.21a).
- Any season after 2026, including the 2027 real offseason (D1).
- Creating practice-squad-only players (D4).
- Articles or coverage of mirrored weeks (a V3 batch job, V3_STUDIO_PLAN §3).
- Letting the pilot sim the real franchise outside catch-up. Live weeks are simmed as JR does now.
- Updating the ratings of players already in the save from EA's weekly ratings. Progression owns them.

## 2. Design

### 2.1 When a week is mirrored

`nfl_mirror_settings` holds one row: `enabled BOOLEAN`, `season INT` (2026), `last_week INT NULL` (NULL = through the Super Bowl), `madden_injuries_field JSONB NULL` (Spike A's `{"table": "LeagueSetting", "tableId": 4290, "row": 0, "fields": {"Injuries": {"field": "Field_100", "off": 0, "on": 1}, "PracticeInjuries": {"field": "Field_110", "off": 0, "on": 1}}}`, S18), `injuries_state VARCHAR(8)` (`ON` / `OFF`, which the league office last wrote).

`store.mirror_active(cur, season, stage, week) -> bool` is true when `enabled`, `season` matches, the stage is REGULAR_WEEK or PLAYOFF_WEEK, and `week <= last_week` (if set). Mu's week numbers are used (`league_week`, playoffs 19/20/21/23 as in `PRO_BOWL_WEEK`, `pipeline/league_office.py:217`). `real_week_for(mu_week)` maps 19→WC, 20→DIV, 21→CON, 23→SB, which in nflverse are weeks 19–22.

The save at Madden's next unplayed week `w` gets the real state of week `w`. Mode: `CATCH_UP` when every real game of week `w` is final in `games.csv`; otherwise `LIVE`, using the newest data available. The first LIVE week is when Madden's injuries come back on (D10). Which real week that is depends on the day the franchise starts. On 2026-11-11 nflverse's newest week is 10, with games on 2026-11-12 to 11-16, so a start today would catch up on weeks 1–9 and go live at week 10.

### 2.2 Real week (`engines/nfl_mirror/real_state.py`, pure)

```python
@dataclass(frozen=True)
class RealPlayer:  gsis_id: str; name: str; position: str; team: str; status: str   # ACTIVE|INACTIVE|PS|IR|PUP|NFI|SUSP
                   designation: str; birth_date: str|None; college: str|None; height_in: int|None; weight_lbs: int|None
                   entry_year: int|None; draft_number: int|None; jersey: int|None
@dataclass(frozen=True)
class RealInjury:  gsis_id: str; team: str; report_status: str; body_part: str|None; practice_status: str|None
@dataclass(frozen=True)
class RealWeek:    season: int; week: int; mode: str; rosters: dict[str, list[RealPlayer]]   # team -> players
                   injuries: dict[str, RealInjury]; depth: dict[str, dict[str, list[str]]]  # team -> pos -> gsis ids
                   head_coach: dict[str, str]; carried_forward: tuple[str, ...]; sources: dict[str, str]  # name -> sha256
def build_week(rows_rosters, rows_injuries, rows_depth, rows_games, season, week) -> RealWeek
```

`STATUS_MAP` maps `(status, status_description_abbr)` → status: `A01`→ACTIVE, `I0x`→INACTIVE, `P0x`→PS, `R01`/`R48`→IR, `R04`→PUP, `R05`→NFI, anything else under RES→IR (logged). `CUT`, `RET` and `EXE` players are left off the team. A team with no rows for `week` reuses its last week's rows and is listed in `carried_forward`. Team codes go through `engines/nfl_history/teams.py` (LA→LAR; nflverse `ARI` vs the save's `AZ` is handled in the map).

`fetch.py` reuses `engines.nfl_history.fetch.get(url, cache_name, refresh=True)` for `weekly_rosters/roster_weekly_2026.csv.gz`, `injuries/injuries_2026.csv.gz`, `depth_charts/depth_charts_2026.csv.gz`, `games.csv` and `contracts/historical_contracts.parquet`. `fetch_week(season, refresh) -> dict[name, FetchedFile]`.

### 2.3 Trades (`engines/nfl_mirror/trades.py`)

`data/history/nfl_trades_2026.csv` columns: `trade_id, date, week, from_team, to_team, gsis_id, player_name, note`. There is one row per player leg; pick legs go in `note`. JR or a session adds rows from news. `read_trades(path) -> list[TradeLeg]`. `infer_switches(prev_week, week) -> list[Switch]` lists every team-to-team move between consecutive weeks. A switch whose `(gsis_id, to_team, week)` has no CSV row is planned as a release plus a sign and listed in the weekly report as "unconfirmed: trade or claim?". CLI `trades --week N` prints them.

### 2.4 Store (`engines/nfl_mirror/store.py`) and migration

`2027_02_27_nfl_mirror.sql`, mirrored at the end of `db/init_db.sql`:
- `nfl_mirror_settings` as in §2.1 (one row, `id SMALLINT PRIMARY KEY CHECK (id = 1)`).
- `nfl_mirror_weeks` (`season, week` PK; `real_week INT`, `mode VARCHAR(8)` CATCH_UP/LIVE, `sources JSONB`, `counts JSONB`, `run_id INT REFERENCES league_office_runs(id)`, `status VARCHAR(10)` PLANNED/WRITTEN/FAILED, `report TEXT`, `created_at`).
- `nfl_mirror_moves` (`id SERIAL`, `season, week`, `kind VARCHAR(10)` CHECK IN `SIGN, RELEASE, TRADE, PS, IR, ACTIVATE, INJURY, HEAL, CREATE, CHART, STAFF, SETTING`, `gsis_id`, `player_id INT REFERENCES players(id) ON DELETE SET NULL`, `pgid INT`, `from_team_id`, `to_team_id`, `detail JSONB`, `status VARCHAR(10)` PLANNED/WRITTEN/SKIPPED/REFUSED, `reason TEXT`).
- `ALTER TABLE nfl_player_links` drops and re-adds the `method` CHECK with `CREATED` added.

Functions: `read_settings(cur)`, `set_enabled(cur, enabled, season=2026, last_week=None)`, `mirror_active(cur, season, stage, week)`, `record_week(cur, season, week, **fields)`, `record_moves(cur, season, week, moves)`, `mark_written(cur, run_id)`, `links(cur) -> {gsis_id: pgid}`, `add_created_link(cur, player_id, gsis_id, evidence)`.

### 2.5 Roster diff (`engines/nfl_mirror/diff.py`, `contracts.py`, pure)

```python
def plan_rosters(snapshot, real: RealWeek, links: dict[str, int], trades: list[TradeLeg], team_index: dict[str, int],
                 contracts: ContractBook, cap: int) -> MirrorRosterPlan
# MirrorRosterPlan: ops: list[dict]   # {"kind": "release"|"sign"|"trade"|"ir"|"activate", "pgid", "gsis_id",
#                                       "to_index", "list": "Roster"|"PracticeSquad", "contract": {...}|None, "why"}
#                   creates: list[RealPlayer]    # active/IR real players with no link and no free Madden match
#                   ps_fill: list[dict]          # {"team_index", "position"}: PS-only real players skipped (D4)
#                   unlinked_new: list[tuple[str, int]]   # (gsis_id, pgid) new links found this week
```

Order of operations: first new links, by running `link.match` over unlinked real players against unlinked Madden players (free agents included). Then trades from the CSV. Then releases (a Madden player on a team the real week doesn't place him on). Then signings, practice-squad moves, IR on and IR off. `ps_fill` signs the best free agent at the position by OVR (`Deps.free_agent_pool` order). `contracts.py`: `ContractBook.from_otc(parquet_path)`, then `contract_for(gsis_id, signed_date, cap) -> dict` (real APY and years when OTC has a deal signed on or after 2026-08-01 for that team, else `minimum_salary(years_exp, cap)` for one year), returned in `league_office.offer_contract` shape.

### 2.6 Injuries and depth charts (`engines/nfl_mirror/injuries.py`, `depth.py`, pure)

`weeks_until_back(gsis_id, week, later_weeks) -> int` is used in CATCH_UP mode (D7). It counts the weeks until the player is next ACTIVE with no Out or Doubtful report, read from the later weeks' rosters and injury reports, and is capped at the weeks left in the regular season. `estimate_weeks(injury: RealInjury, status: str, history: list[str]) -> int` is used in LIVE mode, for whatever Q1 copies: IR and R48 give 4 (the real IR minimum), PUP and NFI give 4, Out gives 1 (2 when he was Out on the previous report too), Doubtful gives 1, Questionable gives 0. `injury_fields(weeks, body_part, enums) -> dict` sets `InjuryStatus`, `InjuryType` (`BODY_PART_TO_TYPE`, Spike B's enum list, else the generic type), `InjurySeverity`, `Min/MaxInjuryDuration = weeks`. A player hurt in Madden but healthy in reality gets `HEAL_FIELDS` (`HEALTHY` values from `pipeline/league_office.py:224`).

`depth.real_chart(real.depth[team], links, madden_roster) -> {slot: [pgid…]}` maps nflverse positions to Madden's 35 depth slots (`DEPTH_SLOTS` table: QB, HB, FB, WR, TE, LT/LG/C/RG/RT, LE/RE/DT, LOLB/MLB/ROLB, CB/FS/SS, K, P; return and special slots left to the engine). An injured, IR or unlinked player is dropped. The league office gives the engine's `staff_depth_chart` result for every slot, or tail of a slot, that `real_chart` leaves empty (`depth.merge(real, engine)`).

### 2.7 Coaching (`engines/nfl_mirror/staff.py`)

`real_staff_changes(real_week, prev_week, staff_csv) -> list[StaffChange]`. A head coach comes from the `games.csv` per-game coach, which changes on a firing. OC/DC changes come from `data/history/nfl_staff_changes_2026.csv` (`date, week, team, role, out_name, in_name, interim, note`). `apply_staff_changes(cur, season, changes) -> list[dict]` sets `end_season = season` on the outgoing tenure. It inserts the incoming person, matched by name to `coaches` / `coordinators` or created with a name-only row (`coach_madden_profile.build_profile` supplies the Madden profile, as the carousel does), and a tenure with `start_season = season`. It records STAFF moves. It never touches a team in the registry's `user_teams`, and there are none in 2026.

### 2.8 Creating players (`engines/nfl_mirror/create.py`)

`ea_ratings.py`:
- `fetch_iteration(refresh=False) -> FetchedFile` downloads every page of `https://www.ea.com/games/madden-nfl/ratings?page=N` until a page adds no new ids. It sends a generic User-Agent, waits 1.5 s between pages, and caches `data/history/.cache/ea_m27_<iteration>.json` with its sha256. The iteration id is read from the page.
- `index(items) -> dict[key, item]` keys each item by `normalize_name` plus birthdate.
- `find(real: RealPlayer, idx) -> item | None` matches on name and birthdate, or on name alone when EA has no birthdate and the position group agrees.
- `madden_fields(item) -> {"position", "archetype", "ratings": {SaveField: value}}` maps EA's 55 camelCase stat keys to the save's `<Name>Rating` fields (`EA_TO_SAVE`, e.g. `bCVision` → `BCVisionRating`, `speed` → `SpeedRating`), and EA's position codes (`LEDG`, `MIKE`, …) through `pipeline/load_save.POSITIONS` reversed. An EA key with no save field is listed in the report, never guessed.

`create.py`:
- `pick_donors(snapshot, links, n, position) -> list[pgid]` takes free agents with no real link (or linked to a person with no 2025–2026 roster row), lowest OVR first, never `Draft` status.
- `created_edit(donor, real: RealPlayer, ea_item | None, template) -> dict` calls `player_edit(donor_row, target, new_name=real.name, commentary_id=commentary_names lookup)` with real height, weight, age (from birth date), college and jersey. Ratings, position and archetype come from `ea_ratings.madden_fields` when EA has the player (D9; source `EA`). Otherwise they come from `ROLE_TEMPLATE` (source `TEMPLATE`): the median Madden player at the position for a real starter by depth chart, the 25th percentile for a backup, and the 10th for IR.
- The edit rides the same write, and the pgid stays the donor's. `add_created_link` stores `CREATED` with the ratings source in `evidence`.

EA's newest iteration (week 8 on 2026-11-11) lags the real week, so a player signed after it falls back to the template. The mirror never re-rates a player once he is created.

### 2.9 League office wiring (`pipeline/league_office.py`, `pipeline/league_office_mirror.py`)

`PRIORITY_MIRROR = PRIORITY_ENGINE_TRADE`. In `plan_stage`, when `deps.mirror_week(cur, season, stage, week)` returns a `MirrorWeek` (not None):
- The "engine trades" step is replaced by `step_nfl_mirror(plan, mirror)`. It applies `creates` (players edits), then `ops` through `plan.release_player / sign_player / trade_player / set_fields` at `PRIORITY_MIRROR` with tag `mirror:<kind>:<gsis_id>`. Next come the injury and heal fields and IR flags, then `ps_fill`, and finally the setting record (both injury switches, `Injuries` and `Practice Injuries`, OFF on the first mirrored write).
- Waivers are skipped in every mirrored week, and `step_injuries` in CATCH_UP weeks. Drift still runs, and players the mirror has claimed are protected by first-claim-stands.
- `step_coach_charts` gets `deps.chart_override(team_index)` = `depth.merge(...)`. Source `MU_COACH` stays.
- `step_staff(plan, cur, snapshot)` also runs in a mirrored REGULAR_WEEK when `mirror.staff_changed`.
- `step_legality` still runs. Mirror releases normally leave each team at 53 + IR, under 63.
- `record_written` calls `store.mark_written(cur, run_id)` and writes one `transactions` row per SIGN / RELEASE / TRADE move (`raw_text = 'NFL mirror'`, `parse_ok = true`, `row_key = 'mirror:<season>:<week>:<gsis_id>:<kind>'`).

At the first run whose mirror week is LIVE, or whose `mirror_active` is false, while `injuries_state = 'OFF'`, a records edit writes both fields' `on` values and sets `injuries_state = 'ON'` (D10). In LIVE weeks `step_injuries` runs again as normal for Madden's own injuries. The mirror then copies only what Q1 decides.

`pipeline/league_office_mirror.py`: `mirror_week(cur, snapshot, season, stage, week) -> MirrorWeek | None` does the fetch, build, diff, estimate and staff work, and records PLANNED rows. `Deps.mirror_week` returns None by default; `default_deps` wires the real adapter. A fetch failure in LIVE mode is a plan error, so nothing is written. JR re-runs once nflverse answers.

### 2.10 Watcher gating (`pipeline/watch_save.py`, `pipeline/weekly_capture.py`)

`ingest_snapshot` reads `store.mirror_active` for the save's (season, stage, week). When true, `run_weekly_engines(..., gm=False)` skips `step_gm`, and the articles and press conferences are skipped with the log line "NFL mirror week: no articles". The notification adds `mirror: N moves (M unconfirmed switches)`.

### 2.11 CLI (`engines/nfl_mirror/__main__.py`)

- `status` prints the settings, the last mirrored week and the next real week's mode.
- `preview --week N [--dsn]` runs fetch, build and diff against the newest snapshot and prints the plan, writing nothing.
- `trades --week N` prints the unconfirmed switches.
- `enable [--last-week N]` and `disable` each need `--confirm <db>`.

### 2.12 Pilot catch-up job (`tools/madden_pilot/jobs.py`, `tools/madden_pilot/__main__.py`)

`python3 -m tools.madden_pilot catch-up --save CAREER-<name> [--to-week N] [--dsn ...]` runs the job `catch_up(pilot, save, to_week=None, dsn=None, progress=None)`. Before touching the PC it refuses in these cases:
- the save is a `CAREER-TUNE-*` clone;
- the watcher isn't running (the reverse of `sim_season`), since the league office write between weeks comes from it;
- `nfl_mirror_settings.enabled` is false;
- the save's next week (`_save_week`) is not CATCH_UP for `real_state`.

Each loop:
1. Load the franchise.
2. `_sim_one_week`.
3. Save, and `quit_franchise(allow_quit=True)` to the main menu so the franchise is closed.
4. Wait, up to `CATCH_UP_WRITE_TIMEOUT` (15 min), for `nfl_mirror_weeks` to show WRITTEN for the next week.
5. Repeat.

It stops at the first LIVE week (or `--to-week`), on a FAILED mirror week, or on any refusal. On stopping it sends one ntfy line per week and a summary. The `/madden` skill text gains the job and its rule: real franchise only during catch-up, under `CLAIM · Madden PC`.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Where the Injuries on/off switch lives in the save | Spike A | If no save field exists: the injury slider at 0 (it is in the settings Madden shows) plus R2's fallback |
| R2 | Madden may ignore or clear a written injury while its injuries are off | Spike B | Fallback: keep the IR flag, and leave Out players off the depth chart (`depth.merge` already drops them), so they don't play |
| R3 | `InjuryType` / `InjurySeverity` enum names | Spike B reads them from the schema | Generic type for every injury |
| R4 | A reused record may show the donor's face or name in game, or crash | Spike C | Treat creation as off: those slots get the best Madden free agent at the position (`ps_fill` logic) |
| R4a | EA's ratings page changes shape or stops serving Madden 27 | `fetch_iteration` checks `totalItems` and the stat keys and fails loudly | Template ratings for every created player; already-created players keep theirs |
| R4b | Name collisions in EA's data (e.g. Zach Bako-Bewele came back as OVR 87, which looks wrong) | `find` requires birthdate agreement when both sides have one; the weekly report lists every EA match with OVR and position | Fall back to the template for doubtful matches |
| R4c | The pilot's catch-up loop sims a week twice or loads the wrong save | It reads the save-header week before and after each sim and refuses when it didn't advance by exactly one; the watcher's `same_week` check never re-ingests | The weekly freeze (`pipeline/weekly_freeze.py restore`) rolls the save and DB back together |
| R5 | A team change is wrongly read as a trade, or the reverse | The hand CSV is the authority; the report lists unconfirmed switches | Release plus sign is safe; only the `transactions` type differs |
| R6 | nflverse lags on Wednesday moves in a LIVE week | `sources` sha256 in `nfl_mirror_weeks`; the next week catches up | A move lands a week late |
| R7 | Real contracts against Madden's cap fields | `CAP_ROOM_RANGE` allows negatives (`pipeline/league_office.py:233`); `CAPSALARY_RANGE` clamps | A few teams show over the cap in Madden until the offseason |
| R8 | Sim standings differ from reality, so real trades look odd | Accepted (D1) | — |
| R9 | Catch-up length depends on when the franchise starts (still held until V2 and tuning) | Known at start | All weeks are CATCH_UP if it starts after the season ends; the design is the same |

**Spike first (before wave 2, JR at the Madden PC under `CLAIM · Madden PC`, about 1 hour, on a `CAREER-TUNE-*` clone):**
- **A.** Toggle franchise Injuries off in League Settings, save, and diff the two snapshots' LeagueSetting rows to name the field and its values.
- **B.** With injuries off, write an Out injury (3 weeks) on a healthy starter and a heal on an injured player. Sim one week and check he sits and the other plays. (The `InjuryType` / `InjurySeverity` enum dump is already done offline from `CAREER-DEBUGWEEK12`, S17; only the sim check needs the PC.)
- **C.** Rewrite one free-agent donor with `player_edit` as a made-up player, sign him and play-sim a week. Check the roster card name, commentary and no crash.

The results go into §0 and `madden_injuries_field`. Wave 1 doesn't depend on them.

## 4. Tests (synthetic, no live DB)

- `tests/test_nfl_mirror_real_state.py`: status map, bye-week carry forward, team code map, playoff week mapping, CATCH_UP vs LIVE from games.
- `tests/test_nfl_mirror_trades.py`: CSV read, switch inference, unconfirmed list.
- `tests/test_nfl_mirror_store.py`: `mirror_active` cases (off, wrong season, offseason stage, past `last_week`); move recording; `CREATED` link; an integration test applies the migration twice on a `mu_scratch_*` database.
- `tests/test_nfl_mirror_staff.py`: HC change from per-game coaches, OC/DC from the CSV, tenure end/start, JR team skipped, a name-only person created once.
- `tests/test_nfl_mirror_diff.py`: release / sign / trade / PS / IR ops on a toy snapshot; CSV trade beats inference; PS-only missing player goes to `ps_fill`; active missing player goes to `creates`; incremental link; OTC contract vs minimum.
- `tests/test_nfl_mirror_injuries.py`: `weeks_until_back` in CATCH_UP (back next week, back after IR, never back, capped), the LIVE estimate table, heal fields, `real_chart` slot mapping, injured players dropped, `merge` fills tails.
- `tests/test_nfl_mirror_create.py`: EA page parsing from a fixture `__NEXT_DATA__`, pagination stop, `find` with and without a birthdate, `EA_TO_SAVE` covering every fixture key, EA position to Madden position; donor choice (never Draft, never a linked active person); EA ratings used when found, the template otherwise; `CREATED` link with its source.
- `tests/test_league_office_mirror.py`: a CATCH_UP REGULAR_WEEK skips engine trades, waivers and `step_injuries`; mirror claims beat drift; injuries OFF on the first write and ON at the first LIVE week; `step_injuries` back in LIVE weeks; staff runs in season only when changed; transactions rows; a non-mirrored week is byte-for-byte unchanged against today's plan.
- `tests/test_madden_pilot_catch_up.py`: refusals (TUNE save, no watcher, mirror off, LIVE week), one loop with a fake pilot and a fake store, stop at the first LIVE week, stop on FAILED, the week-advanced-by-one check.
- `tests/test_watch_save_mirror.py`: a mirrored week skips GM proposals, articles and press; a normal week doesn't.

Run with `pytest -m "not integration" tests/test_nfl_mirror_*.py tests/test_league_office_mirror.py tests/test_league_office.py tests/test_watch_save_mirror.py tests/test_madden_pilot_catch_up.py`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Real week source

- **Owns:** `engines/nfl_mirror/__init__.py` (new), `engines/nfl_mirror/fetch.py` (new), `engines/nfl_mirror/real_state.py` (new), `engines/nfl_mirror/trades.py` (new), `data/history/nfl_trades_2026.csv` (new, header plus the trades JR has named), `tests/test_nfl_mirror_real_state.py` (new), `tests/test_nfl_mirror_trades.py` (new)
- **Reads:** `engines/nfl_history/fetch.py`, `engines/nfl_history/teams.py`, §2.1–2.3
- **Depends on:** none
- **Done when:** `build_week` returns a `RealWeek` from fixture rows with carry-forward and modes; `infer_switches` lists unconfirmed moves.
- **Verify:** `pytest -m "not integration" tests/test_nfl_mirror_real_state.py tests/test_nfl_mirror_trades.py` passes.

### U2: Store, migration and coaching

- **Owns:** `db/migrations/2027_02_27_nfl_mirror.sql` (new), `db/init_db.sql`, `engines/nfl_mirror/store.py` (new), `engines/nfl_mirror/staff.py` (new), `data/history/nfl_staff_changes_2026.csv` (new, header only), `tests/test_nfl_mirror_store.py` (new), `tests/test_nfl_mirror_staff.py` (new)
- **Reads:** `db/migrations/2027_02_04_nfl_history.sql`, `engines/coach_save_sync.py`, `engines/coach_madden_profile.py`, `engines/coaching_carousel.py`, §2.4, §2.7
- **Depends on:** none. `staff.py` takes plain `{team: head_coach}` dicts per week, the shape of `RealWeek.head_coach`.
- **Done when:** the migration applies twice cleanly on a scratch DB; `mirror_active` and the tenure changes behave as §4 says.
- **Verify:** `pytest -m "not integration" tests/test_nfl_mirror_store.py tests/test_nfl_mirror_staff.py` passes.

### U3: Roster diff and contracts

- **Owns:** `engines/nfl_mirror/diff.py` (new), `engines/nfl_mirror/contracts.py` (new), `tests/test_nfl_mirror_diff.py` (new)
- **Reads:** `engines/nfl_history/link.py`, `pipeline/league_office.py` (`offer_contract`, `Plan`), `engines/contracts_engine.py`, §2.5
- **Depends on:** U1
- **Done when:** `plan_rosters` gives the expected ops, creates and `ps_fill` on the toy snapshot.
- **Verify:** `pytest -m "not integration" tests/test_nfl_mirror_diff.py` passes.

### U4: Injuries and depth charts

- **Owns:** `engines/nfl_mirror/injuries.py` (new), `engines/nfl_mirror/depth.py` (new), `tests/test_nfl_mirror_injuries.py` (new)
- **Reads:** `pipeline/league_office.py:388-395, 1502-1526`, `engines/depth_chart_engine.py`, Spike B results, §2.6
- **Depends on:** U1
- **Done when:** estimates, heal fields and merged charts match §4.
- **Verify:** `pytest -m "not integration" tests/test_nfl_mirror_injuries.py` passes.

### U5: Creating players (EA ratings first)

- **Owns:** `engines/nfl_mirror/ea_ratings.py` (new), `engines/nfl_mirror/create.py` (new), `tests/test_nfl_mirror_create.py` (new), `tests/fixtures/ea_ratings_page.json` (new)
- **Reads:** `pipeline/madden_player_edit.py`, `pipeline/commentary_names.py`, `pipeline/load_save.py` (`POSITIONS`), a snapshot's `ratings` keys, Spike C results, §2.8
- **Depends on:** U1, U2
- **Done when:** `created_edit` builds a valid players edit from an EA item or a template; `pick_donors` follows its rules; `EA_TO_SAVE` maps every EA stat key in the fixture.
- **Verify:** `pytest -m "not integration" tests/test_nfl_mirror_create.py` passes.

### U6: League office wiring

- **Owns:** `pipeline/league_office.py`, `pipeline/league_office_mirror.py` (new), `tests/test_league_office_mirror.py` (new)
- **Reads:** §2.9, the U1–U5 modules
- **Depends on:** U1, U2, U3, U4, U5
- **Done when:** a mirrored week plans as §2.9 says, and a non-mirrored week's plan is unchanged.
- **Spike note (S20):** a heal only takes effect when the same week's write also clears `IsInjuredReserve` and puts the player back on the chart, so `step_nfl_mirror` must build the depth chart from the roster as it stands after this week's heal and activation ops, not from the raw snapshot, or a returning player sits a week and plays from the bench.
- **Verify:** `pytest -m "not integration" tests/test_league_office_mirror.py tests/test_league_office.py tests/test_league_office_personality.py tests/test_league_office_staff.py` passes.

### U7: Watcher gating and CLI

- **Owns:** `pipeline/watch_save.py`, `pipeline/weekly_capture.py`, `engines/nfl_mirror/__main__.py` (new), `tests/test_watch_save_mirror.py` (new)
- **Reads:** §2.10–2.11, `engines/nfl_mirror/store.py`, `pipeline/league_office_mirror.py`
- **Depends on:** U2, U6
- **Done when:** gating works as §4 says; `python3 -m engines.nfl_mirror --help` lists the five commands.
- **Verify:** `pytest -m "not integration" tests/test_watch_save_mirror.py` passes, and `python3 -m engines.nfl_mirror --help` exits 0.

### U8: Pilot catch-up job

- **Owns:** `tools/madden_pilot/jobs.py`, `tools/madden_pilot/__main__.py`, `.claude/skills/madden/SKILL.md`, `docs/reference/MADDEN_PILOT.md`, `tests/test_madden_pilot_catch_up.py` (new)
- **Reads:** `tools/madden_pilot/navigate.py`, `engines/nfl_mirror/store.py`, `engines/nfl_mirror/real_state.py`, §2.12
- **Depends on:** U1, U2
- **Done when:** `catch-up` refuses as §2.12 says and loops correctly with fakes; the skill and pilot doc describe it.
- **Verify:** `pytest -m "not integration" tests/test_madden_pilot_catch_up.py` passes; `python3 -m tools.madden_pilot catch-up --help` exits 0.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2 | — | No shared files; U2's staff code takes plain dicts |
| 2 | U3, U4, U5 | U1 (U5 also U2); Spikes A–C done | All pure modules on `RealWeek`; no shared files |
| 3 | U6 | U1–U5, U10, U11 | Wires everything into `plan_stage` |
| 4 | U7, U8 | U2, U6 (U8: U1, U2) | No shared files; U8 is ready after wave 1 but waits so the first real catch-up runs on the finished mirror |
| follow-up | U10, U11, U12 | merged U3 / the save writer | Audit fixes F1, F2 and P1, plus JR's switch decision (I1), built beside waves 2–4 |
| 5 | U13, U14, U15, U19 | U8, U10, U5, U7 (all merged) | JR's 2026-11-11 decision: the open audit findings. Disjoint files (pilot jobs, contracts, EA ratings, CLI) |
| 6 | U17 | U2 (merged) | Wave-1 F3's mechanical half (a named replacement writes cleanly; without one the week refuses loudly). U16 is delivered by U18, so it is not scheduled separately |
| audit fix (in flight) | U18 | U6, U2 (merged) | `builder-9`'s single batch of audit-4's U6 findings F1–F5 (M-0532, M-0536, M-0541). Owns the league-office files and `store.py`, so it covers U16 |

### U10: Audit fixes F1 and F2 (wave-2 review)

- **Owns:** `engines/nfl_mirror/diff.py`, `engines/nfl_mirror/contracts.py`, `tests/test_nfl_mirror_diff.py`
- **Reads:** `data/audit_reports/nfl_mirror_wave2/report.md` (F1, F2), §2.3, §2.5
- **Depends on:** U3
- **Done when:** an inapplicable or stale CSV trade leg no longer suppresses a later cut, promotion or wrong-destination move, and an OTC deal is used only for its own team and never before its date; the auditor's repros and regression matrix pass.
- **Verify:** `pytest -m "not integration" tests/test_nfl_mirror_diff.py` passes.

### U11: Switch write — release and sign in one week (I1)
- **Owns:** `pipeline/save_reader/save_writer.mjs`, `pipeline/save_reader/test/write_save.test.mjs`
- **Reads:** `data/audit_reports/nfl_mirror_wave2/report.md` (I1), `pipeline/save_reader/release.mjs`, `sign.mjs`, `pipeline/league_office.py` (`Plan.release_player`, `Plan.sign_player`)
- **Depends on:** nothing (the save writer only)
- **Done when (JR's decision, 2026-11-11):** one deliberate release + sign of the same player to a **different** team is accepted as one weekly move — the old deal's dead cap and the new contract both applied — while every other same-player double move is still refused. The sign names the pairing (a `switch` flag), so the writer never infers intent.
- **Verify:** `node --test test/write_save.test.mjs` in `pipeline/save_reader` passes (a real-save readback where a sample save is present), plus `pytest -m "not integration" tests/test_franchise_api.py tests/test_watch_save.py`.

### U12: Cross-position review candidates (P1)

- **Owns:** `engines/nfl_mirror/diff.py`, `tests/test_nfl_mirror_diff.py`
- **Reads:** S5a, §2.5, `data/audit_reports/nfl_mirror_wave2/report.md` (P1), `engines/nfl_history/link.py` (`match`, read-only)
- **Depends on:** U10 (same files)
- **Done when:** S5a's promise is kept without weakening `link.match`: an unlinked real player whose normalized name uniquely matches a save player in a **different** position group becomes a review candidate on the plan (pgid, both positions, the agreeing evidence — entry season or 2026 team — and why it was not auto-linked), never an auto-link, and he is not also planned as a `create`. An ambiguous name (two or more save players) is reported as ambiguous with every candidate and creates nothing. A candidate accepted through `data/history/nfl_link_overrides.csv` links once and stops being a candidate.
- **Verify:** `pytest -m "not integration" tests/test_nfl_mirror_diff.py` passes.

### U13: U8 catch-up audit fixes (F1, F2, F3)

- **Owns:** `tools/madden_pilot/jobs.py`, `tests/test_madden_pilot_catch_up.py`
- **Reads:** audit-3's U8 handoff (M-0508), §2.12
- **Depends on:** U8 (merged)
- **Done when:** F1 — the save's calendar year is respected, so a 2027 snapshot never advances under 2026 mirror settings; F2 — a FAILED or missing current-week readiness and the `last_week` cutoff are checked before the sim; F3 — Conference21 → Pro Bowl no longer yields None and stops before the quit, and an assigning-22 week no longer reads LIVE; the auditor's repros pass.
- **Verify:** `pytest -m "not integration" tests/test_madden_pilot_catch_up.py` passes.

### U14: U10 audit N1 — the January 2027 contract window

- **Owns:** `engines/nfl_mirror/contracts.py`, `tests/test_nfl_mirror_diff.py`
- **Reads:** `data/audit_reports/nfl_mirror_followup/report.md` (M-0510, N1), §2.5
- **Depends on:** U10, U12 (same test file)
- **Done when:** a deal signed in January 2027 is used for a 2026-season signing whose as-of date is in that January (the Jan 10 BUF 2yr/$6M deal as of Jan 31 returns STANDARD, not MINIMUM), because the 2026 mirror window runs through the Super Bowl; the auditor's parquet postseason regressions pass.
- **Verify:** `pytest -m "not integration" tests/test_nfl_mirror_diff.py` passes.

### U15: U5 audit F1 — the EA multi-page refresh

- **Owns:** `engines/nfl_mirror/ea_ratings.py`, `tests/test_nfl_mirror_create.py`
- **Reads:** `data/audit_reports/nfl_mirror_create/report.md` (M-0509, F1), §2.8
- **Depends on:** U5 (merged)
- **Done when:** a refresh whose pages carry different iteration ids never caches two iterations' items under one id (the newest iteration wins, or the mixed page is refused), an existing cache file survives a partial failure, and the auditor's repro and its old-cache-preservation test pass.
- **Verify:** `pytest -m "not integration" tests/test_nfl_mirror_create.py` passes.

### U16: The mirror's write record — only written moves read WRITTEN

- **Owns:** `engines/nfl_mirror/store.py`, `pipeline/league_office.py`, `tests/test_nfl_mirror_store.py`, `tests/test_league_office_mirror.py`
- **Reads:** §2.9; `engines/nfl_mirror/store.py` (`mark_written`), `pipeline/league_office.py` (`_record_mirror`, `step_nfl_mirror`); the U6 deviation in the Status line
- **Depends on:** U6 (merged)
- **Delivered by:** U18 — audit-4's U6 F3 is this defect in the same function, with the same files, so it lands in `builder-9`'s one-pass batch rather than a second climb through `store.py` and `league_office.py`. The row stays so the mapping is explicit.
- **Done when:** a write that refused a move leaves that move REFUSED with its reason in `nfl_mirror_moves` instead of WRITTEN, while the week still reads WRITTEN with its run; `mark_written` flips only the moves the save actually got; a non-mirrored write is unchanged.
- **Verify:** `pytest -m "not integration" tests/test_nfl_mirror_store.py tests/test_league_office_mirror.py` passes.

### U17: Wave-1 audit F3 — a promoted coordinator's source seat

- **Owns:** `engines/nfl_mirror/staff.py`, `tests/test_nfl_mirror_staff.py`
- **Reads:** wave-1 audit (`data/audit_reports/nfl_mirror_wave1/report.md`, F3), §2.7, §8 Q2
- **Depends on:** U2 (merged)
- **Policy:** who occupies the source coordinator seat when the promoted man has no named replacement is **open with JR (Q2)**. This unit does not answer it and never invents a successor.
- **Done when:** (1) with a named replacement in `nfl_staff_changes_2026.csv`, an in-season promotion ends the promoted man's coordinator tenure and installs the named person, so `_wanted` asks for each man once, every seat resolves to a distinct profile (no `promotion skipped … no coach_madden_profiles row`), and a repeated apply is idempotent; (2) without a named replacement, `staff.py` reports the unresolved seat as a REFUSED change carrying its reason instead of skipping it, so the plan records the refusal and the week cannot read as cleanly written; (3) a same-team promotion and a cross-team incoming coordinator take the same source-seat path; (4) JR's teams are never touched.
- **Verify:** `pytest -m "not integration" tests/test_nfl_mirror_staff.py` passes (real-PostgreSQL cases run only with a scratch DSN).

### U18: U6 audit F1–F5 (builder-9's batch; covers U16)

- **Owns:** `pipeline/league_office.py`, `pipeline/league_office_mirror.py`, `engines/nfl_mirror/store.py`, `tests/test_league_office_mirror.py`
- **Reads:** `data/audit_reports/audit-4/u6/report.md` and its repros; M-0532, M-0536, M-0541, M-0548
- **Depends on:** U6, U2 (merged)
- **Done when:** the five accepted findings are fixed in one pass — F1 a created IR/active player joins the same week's injury and chart reconciliation; F2 a creation is preflighted and claimed before the donor is edited, a refused creation leaves no link or transaction, and a JR-owned player cannot be switched; F3 only applied moves read WRITTEN and refused or skipped ones carry their reason (U16); F4 ordinary and `OVERRIDE` links persist with their method and evidence and the retired-linked donor case is decided explicitly; F5 the week's as-of date comes from the shared `diff._week_as_of` helper, with boundary tests.
- **Verify:** `pytest -m "not integration" tests/test_league_office_mirror.py tests/test_nfl_mirror_store.py` passes, plus `python3 -m tools.offseason_check` tables (`e2e` on CAREER-MUBASE, `timelines` for any contract change) in the report per §"After every change".

### U19: U7 audit F1–F3 — CLI confirmation, preview identity, status week mapping

- **Owns:** `engines/nfl_mirror/__main__.py`, `tests/test_watch_save_mirror.py`
- **Reads:** `data/audit_reports/audit-5/u7/report.md` and `repro.py` (M-0551), §2.11
- **Depends on:** U7 (merged)
- **Done when:** F1 — `enable` and `disable` compare `--confirm` with the **opened** connection's resolved database (`conn.info.dbname` / `current_database()`) before any mutation and use that same connection, so a repeated or overridden `dbname=`, a URL query override or an environment/service default that disagrees refuses nonzero with no INSERT/UPDATE/commit; F2 — `preview` binds its snapshot to the target database (`league_state.madden_league_id`, with the season/stage checked) and accepts an explicit `--snapshot` only after validating its identity, refusing loudly when the association cannot be established, so one franchise's links are never diffed against another's save; F3 — `status` reports the next supported real-game Mu week (21 → 23, then none), skipping the internal no-game week 22. The auditor's F1–F3 repros become regressions, including its real-scratch-PostgreSQL cases.
- **Verify:** `pytest -m "not integration" tests/test_watch_save_mirror.py tests/test_watch_save.py` passes; the Postgres cases run against `mu_scratch_*` via `TEST_DATABASE_URL`.

**File ownership check:**

| File | Unit |
|---|---|
| `.claude/skills/madden/SKILL.md` | U8 |
| `data/history/nfl_staff_changes_2026.csv` | U2 |
| `data/history/nfl_trades_2026.csv` | U1 |
| `db/init_db.sql` | U2 |
| `db/migrations/2027_02_27_nfl_mirror.sql` | U2 |
| `docs/reference/MADDEN_PILOT.md` | U8 |
| `engines/nfl_mirror/__init__.py` | U1 |
| `engines/nfl_mirror/__main__.py` | U7, then U19 |
| `engines/nfl_mirror/contracts.py` | U3, then U10, then U14 |
| `engines/nfl_mirror/create.py` | U5 |
| `engines/nfl_mirror/depth.py` | U4 |
| `engines/nfl_mirror/diff.py` | U3, then U10, then U12 |
| `engines/nfl_mirror/ea_ratings.py` | U5, then U15 |
| `engines/nfl_mirror/fetch.py` | U1 |
| `engines/nfl_mirror/injuries.py` | U4 |
| `engines/nfl_mirror/real_state.py` | U1 |
| `engines/nfl_mirror/staff.py` | U2, then U17 |
| `engines/nfl_mirror/store.py` | U2, then U18 (covers U16) |
| `engines/nfl_mirror/trades.py` | U1 |
| `pipeline/league_office.py` | U6, then U18 (covers U16) |
| `pipeline/save_reader/save_writer.mjs` | U11 |
| `pipeline/save_reader/test/write_save.test.mjs` | U11 |
| `pipeline/league_office_mirror.py` | U6, then U18 (covers U16) |
| `pipeline/watch_save.py` | U7 |
| `pipeline/weekly_capture.py` | U7 |
| `tools/madden_pilot/__main__.py` | U8 |
| `tools/madden_pilot/jobs.py` | U8, then U13 |
| `tests/fixtures/ea_ratings_page.json` | U5 |
| `tests/test_league_office_mirror.py` | U6, then U18 (covers U16) |
| `tests/test_madden_pilot_catch_up.py` | U8, then U13 |
| `tests/test_nfl_mirror_create.py` | U5, then U15 |
| `tests/test_nfl_mirror_diff.py` | U3, then U10, then U12, then U14 |
| `tests/test_nfl_mirror_injuries.py` | U4 |
| `tests/test_nfl_mirror_real_state.py` | U1 |
| `tests/test_nfl_mirror_staff.py` | U2, then U17 |
| `tests/test_nfl_mirror_store.py` | U2, then U16 |
| `tests/test_nfl_mirror_trades.py` | U1 |
| `tests/test_watch_save_mirror.py` | U7, then U19 |

No path appears twice.

## 7. Spec changes when built

- §1 Current direction: the 2026 season mirrors the real NFL week by week (catch-up then live), with Mu's engines from the 2027 offseason.
- New §2.21c NFL Weekly Mirror: sources, modes, diff order, injury estimates, creation, coaching, settings and CLI.
- §2.21a League Office: the mirrored-week branch of `plan_stage`, `PRIORITY_MIRROR`, the injuries setting record, `step_staff` in season, transactions rows.
- §3.3x: `nfl_player_links.method` gains `CREATED`. New §3.3 subsection for the three `nfl_mirror_*` tables.
- §6.0b: mirrored-week gating (no GM proposals, articles or press) and the notification line.
- §1.2 Running the System: `python3 -m engines.nfl_mirror` commands and `python3 -m tools.madden_pilot catch-up`.
- Repository Layout: `engines/nfl_mirror/` and its files (with `ea_ratings.py`), `pipeline/league_office_mirror.py`, the migration, both CSVs, the EA cache file pattern and the new tests; the pilot line gains the `catch-up` job.

## 8. Decisions for JR

Settled on 2026-11-11 (D7–D12): real return dates in catch-up; the pilot sims catch-up; EA ratings first, then position averages; Madden injuries back on at the first live week; mirrored weeks' stats count.

| # | Question | Recommendation |
|---|---|---|
| Q1 | Once Madden's injuries are back on (live weeks), what real injury news do we still copy? | **Decided (D12):** only IR, PUP and NFI placements and activations; no weekly reports; no healing of Madden's injuries. |
| Q2 | Wave-1 F3: an in-season promotion with no named coordinator replacement — who occupies the source seat? | **Open.** U17 builds only the mechanical half: a named replacement writes cleanly, and without one the seat is reported REFUSED with its reason. No successor is invented, and no policy is assumed until JR answers. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | worktree-agent-a4c4046ed309775c0 | .claude/worktrees/agent-a4c4046ed309775c0 | merged (42 tests) | 534fb66 |
| U2 | 1 | worktree-agent-a113b2696c3f27a19 | .claude/worktrees/agent-a113b2696c3f27a19 | merged (38 tests + integration on mu_scratch) | 6953da6 |
| U3 | 2 | worktree-agent-ab477134fdc19c085 | .claude/worktrees/agent-ab477134fdc19c085 | merged (18 tests; review fix: snapshot college references resolved before linking) | 67f8850 |
| U4 | 2 | worktree-agent-a3473b7f0b3d4ce3d | .claude/worktrees/agent-a3473b7f0b3d4ce3d | merged (55 tests; review fix: only save-writable injury enums) | 14db099 |
| U9 | 2 | worktree-agent-ade8f1b34cbd6b018 | .claude/worktrees/agent-ade8f1b34cbd6b018 | merged (26 offline + 5 new real-PG regressions; timelines 39 PASS, e2e 41 PASS; F1/F2 fixes) | 9425923 |
| pickup | — | — | — | orchestrator handoff `NFL_WEEKLY_MIRROR_HANDOFF.md` read by `build-nfl-mirror-2` and deleted | — |
| U8 | 4 | worktree-agent-a673f6f388263a232 | .claude/worktrees/agent-a673f6f388263a232 | merged (23 tests; CLI help exits 0; unit commit `742961a`) | 7f007cd |
| U5 | 2 | worktree-agent-a065c1a546eb86e28 | .claude/worktrees/agent-a065c1a546eb86e28 | merged (19 tests; 173 passed across the mirror suite; unit commit `2369c02`, trailer corrected from `deepseek-flash`) | 3665608 |
| U10 | follow-up | worktree-agent-ac536c720da81913f | .claude/worktrees/agent-ac536c720da81913f | merged (38 tests in `test_nfl_mirror_diff.py`; 135 across the mirror suites; audit repros 5 passed; unit commit `feb3091`, trailer corrected from `deepseek-flash`) | 391556a |
| U12 | follow-up | worktree-agent-a4ec0bf3a62748bde | .claude/worktrees/agent-a4ec0bf3a62748bde | merged (47 tests in `test_nfl_mirror_diff.py`; audit P1 repro covered; unit commit `ef540f5`) | 22e22a5 |
| U11 | follow-up | worktree-agent-ae5d843b2f1a39028 | .claude/worktrees/agent-ae5d843b2f1a39028 | merged (9 node tests pass + 1 pre-existing skip, real-save readback; 202 pytest; `offseason_check e2e` 41 PASS / 4 INFO, `timelines` 39 PASS on main; unit commit `c0b0469`) | 4dc0720 |
| U7 | 4 | worktree-agent-a99e21e134bf502ca | .claude/worktrees/agent-a99e21e134bf502ca | merged (19 new tests; 165 with `test_watch_save.py` untouched; CLI help exits 0 with the five commands; unit commit `2a13792`) | d95ea10 |
| U6 | 3 | worktree-agent-ad15aea0f6cdf19c5 | .claude/worktrees/agent-ad15aea0f6cdf19c5 | merged (15 new offline tests; 163 across the league-office suites on main; `offseason_check e2e` 41 PASS / 4 INFO; unit commit `bb4a351`, merge built on a scratch `merge-u6` branch and fast-forwarded) | 1849951 |
| U13 | 5 | worktree-agent-a0f331c482cba00aa | .claude/worktrees/agent-a0f331c482cba00aa | merged (14 new catch-up tests, 124 with `test_madden_pilot_jobs.py`; auditor F1/F2/F3 repros now behave; unit commit `c85c8d5`, merge built on scratch `merge-u13`) | 68fb1ec |
| U14 | 5 | worktree-agent-accc675413c0816df | .claude/worktrees/agent-accc675413c0816df | merged (49 tests in `test_nfl_mirror_diff.py`; the postseason repro exits 0; unit commit `a2975fe`, scratch `merge-u14`) | 5288708 |
| U15 | 5 | worktree-agent-a8f199ba31ee6da3b | .claude/worktrees/agent-a8f199ba31ee6da3b | merged (23 tests in `test_nfl_mirror_create.py`; the mixed-iteration repro now refuses; unit commit `6e1afa1`, scratch `merge-u15`) | 188356e |
| U17 | 6 | worktree-agent-a0cd4a7003f04c95b | .claude/worktrees/agent-a0cd4a7003f04c95b | merged (6 new staff tests, 21 in `test_nfl_mirror_staff.py`; 225 across the mirror and coach suites; `offseason_check e2e` 41 PASS / 4 INFO; unit commit `e3be5c4`, scratch `merge-u17`) | 7369d36 |
| U18 | audit fix | builder-9-u13 | .claude/worktrees/builder-9-u13 | merged by `builder-9` (audit-4's U6 F1–F5 in one pass; covers U16; 4 files, 815 insertions; unit commit `fb2454b`) | 0e92cf8 |
| U19 | 5 | worktree-agent-a02d2dd02d86a6f4e | .claude/worktrees/agent-a02d2dd02d86a6f4e | merged (189 offline with `test_watch_save.py`, 50 with a real `mu_scratch_u19_test`; CLI help exits 0; audit-5's F1 repro now refuses with exit 2 and no SQL; unit commit `e43ee08`, scratch `merge-u19`) | 2f86fb5 |
| U13 fix (R1/R2) | follow-up | on `main` | — | merged (6 new catch-up tests, 43 in the file; 259 across the pilot+mirror suites; the auditor's two gap repros now stop with no load, sim or quit) | be04a8d |
| U17 fix (C1) | follow-up | on `main` | — | merged (4 new offline staff tests + 1 scratch-PostgreSQL case, 25 offline / 3 integration in the file; 114 passed across the staff, adapter and real-state suites; the 3 C1 offline cases fail on the pre-fix tree; the auditor's C1 repro keeps COORD 101 and writes nothing; `offseason_check e2e` 41 PASS / 4 INFO) | f8cdc4e |
| U18 fix (N1) | follow-up | on `main` | — | merged (canonical `Plan.trade_player` view status; both new regression assertions fail on the pre-fix tree — `('Roster', 0)` instead of `('Signed', 0)` — and a CAREER-MUBASE copy shows the promoted WR 14668 in the same write's CHI chart; 168 passed in the league-office suites, 148 in the mirror regression set, node writer suite 82 pass / 2 pre-existing prune-capacity failures on the played save; `offseason_check timelines` 39 PASS and `e2e` 41 PASS / 4 INFO, source sha unchanged) | a641efa |
