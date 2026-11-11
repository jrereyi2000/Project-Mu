# MADDEN_STATS_INGEST_HANDOFF

**From:** `deepseek-v4.1-flash (stats-ingest)`, 2026-11-10 23:56 – 2026-11-11 01:45 EST.
**Why this session ended:** JR asked for a handoff because its context was filling, before wave 1 started. **No code was written, no migration applied, no database touched, no Madden PC use.** The only edits are the plan doc and this handoff.

## 0. State in one paragraph

`docs/not_started/MADDEN_STATS_INGEST_PLAN.md` (untracked until this session's handoff commit) is JR-approved from the opus-queue chat (board M-0375/M-0376). This session did the full session start (DeepSeek skill, spec, board, NOTICE M-0377, inbox listener), the `/execute-plan` preconditions, and the plan's **spike**; its results are written into the plan as §0 S19–S22 with §2.5, §3, §2.1, §4 and U1's Done-when corrected. JR then asked, before approving the build, for a full explanation of which doc metrics are implementable; that answer was given in chat and matches §2.5. JR's next message was the handoff request — so **JR has not yet given the in-window go-ahead**. The build session must confirm it (skill precondition 2) before wave 1.

## 1. Preconditions checked (re-check at build time; `main` has moved since)

Run at `main` `6e984da`; `main` has since gained `a2238d8`, `97d24c0`, `13f680a`, `2f711e1` (builder-7's batches). None touches this plan's files.
- Main checkout, `main` checked out, unit-owned files clean (`pipeline/save_reader/pc_save_reader.mjs`, `pipeline/save_reader/test/read_save.test.mjs`, `db/init_db.sql`, `pipeline/load_save.py`, `tests/test_load_save.py`, `engines/advanced_metrics.py`, `tests/test_advanced_metrics.py`, `docs/reference/MADDEN_STATS_AND_ADVANCED_METRICS.md`, `db/migrations/`).
- Ownership: no file in two units; no unit owns `PROJECT_SPEC.md`, `CLAUDE.md` or the plan (plan §5 / §6).
- **S18 is clear:** builder-4's drafted-cohort batch is on `main` (`5e49aa5`), its claim released. `READER_VERSION = 9` (`pipeline/save_reader/pc_save_reader.mjs:65`), so **U1 writes 10**; the loader constant `CAREER_COUNTERS_READER_VERSION = 10`.
- **Migration number:** `2027_02_25` is free (latest is `2027_02_24_player_draft_facts.sql`). Re-check before U2.
- Dirty files in the main checkout belong to other sessions (board-startup's `.agents/`, `.claude/`, `PROJECT_SPEC.md`, tools lines; dynamic-presentation docs). Leave them; commit by path only (`git commit --only -- <paths>`).
- Board: my NOTICE/claim is M-0377. M-0379 (opus-queue relaying JR): **send implementations to `audit-2`, not `audit`** — after each wave, NOTICE `audit-2` with commits, tests, spec changes; pick up its verdicts. builder-7 signed off (M-0399) and released its `PROJECT_SPEC.md` claim; no other session claims the stats files.

## 2. Spike outcome (done; the plan is the source of truth)

Read-only over copies of `CAREER-MU2026` (2026, week 0) and `CAREER-TUNE-P2G` (2027, week 0); scripts in `/tmp/mu-stats-ingest-spike/` (scratch, not durable; U1 replaces them). 2,552 / 2,484 players with career rows, 2,095 matched by pgid.

- **S19** — `SHORTPASS/MEDIUMPASS/DEEPPASS_ATTEMPTS`, `BLITZ_*` and `QBSCRAMBLE_*` do **not** grow for any QB over a season (totals static: 9,931 / 3,344 / 1,838). Doc metrics 4.2.3, 4.2.4, 4.2.6, 4.2.7 are **IMPOSSIBLE**, not OK. The plan's original table (S9) said they grew; S9 is corrected in place.
- **S20** — the pocket, throw-location and separation breakdowns are three partitions of the *same* tracked-dropback count (equal sums for 40/40 QBs). Median pocket sum / pass-attempt growth = 0.986; 13 QBs exceed 1.0 and sacks explain most of it — `Drew Lock` (117 vs 26) is an unexplained outlier. Pocket and throw-location metrics are OK; ratio the counters against themselves.
- **S21** — `SEPARATION_TIGHT == NORMAL == OPEN` exactly for 41 of 42 QBs (wide-open differs). No per-bucket separation metric.
- **S22** — `ROUTESRUN` grows a median 0.72 per target and ~5% of `DOWNSPLAYED` (WR 0.723 / TE 0.660 / HB 0.607 / FB 0.500); a WR1 gets 110–130 a season against ~1,100 downs. TPRR / YPRR / route participation stay **BLOCKED**.
- Confirmed from the earlier survey: `DOWNSPLAYED` is 0 in every MU2026 career row (S8), 38 career-table changes over the season (S12), `FIRSTDOWNS` / `4QCOMEBACKS` / `GAMERATING` never fill.

**Metric status after the spike (31 doc §4 metrics):** 15 OK + 1 new (QB aDOT) + `coverage_rating_lite` + 3 BLOCKED + 13 IMPOSSIBLE (4.4.5 splits into an OK stop rate and an impossible per-run-snap stuff rate). The chat answer to JR lists every metric with its reason.

## 3. Next steps — run `/execute-plan docs/not_started/MADDEN_STATS_INGEST_PLAN.md`

1. Confirm the go-ahead with JR (AskUserQuestion), then follow the skill exactly:
   - `git mv` the plan to `docs/in_progress/`, set the Status line, append `## 9. Build log` (table), add the plan to the `docs/in_progress/` list in `PROJECT_SPEC.md`'s Repository Layout, commit `docs(<scope>): approve <NAME> and move to docs/in_progress`, record `BASE_SHA`.
   - `PROJECT_SPEC.md` is edited by others: read `git diff HEAD -- PROJECT_SPEC.md` first, edit only your hunks, commit with a scratch index / `git commit --only`.
2. **Wave 1 = U1, U2, U3 together** (at most 3 agents; the plan's §6 needs no ordering between them). Use `.claude/skills/execute-plan/unit_prompt.md`, `isolation: "worktree"`, background. Review each as it finishes: report STATUS/BLOCKERS, `git log`/`diff --stat` against its Owns list, the diff itself, run its **Verify** command in the worktree.
   - **Skill rule to honour:** a unit touching `pipeline/save_reader/` must attach `python3 -m tools.offseason_check` result tables (`e2e` on `CAREER-MUBASE`). That means **U1**, even though it is a read-only reader change. If that is impractical, say why in the unit report rather than silently skipping it.
   - Merge each with `--no-ff`; on conflict run `combine_conflicts.py` (different lines → combine; same line → abort and tell JR). Re-run Verify on main.
3. Close the wave: apply the units' SPEC CHANGES to `PROJECT_SPEC.md` yourself per plan §7 (new §2.x Advanced Metrics, §3.3 tables/views, §1.1 layout, §6.0b), update the plan Build log + Status, commit `docs(spec): <NAME> wave 1`, remove the merged worktrees/branches.
4. Finish: run every Verify command on main plus `pytest -m "not integration" tests/test_load_save.py tests/test_advanced_metrics.py`; move the plan to `docs/done/`; commit; **HANDOFF to `audit-2`** (not `audit`) with the commit range, per-unit lines, deviations, migrations and the DBs they were applied to.

## 4. Facts the units need

- Reader: `pc_save_reader.mjs` `readPlayers` (~line 396), `readStatRows` (~line 350) is the pattern for `readCareerRow`; helpers `decodeRef`, `plainFields` in `lib.mjs`. `CareerStats` is a **single** reference, and its row's table name varies (`CareerOffensiveKPReturnStats` after a return role is added — S12).
- Loader: `offense_cols` / `defense_cols` / the blocking lambda `pipeline/load_save.py:164-263`; `group_lines` `:288`; `load_game_lines` `:647`, `load_season_lines` `:729`; `load_snapshot` calls `load_season_lines`; `current_week(snapshot)` `:1173`. New columns go at the **end** of `season_stat_lines` / `game_stat_lines` (readers today: `newsroom/db_tools.py`, `engines/records_engine.py`, `engines/progression_inputs.py`, `engines/tuning/compare.py`).
- M27 schema field names (all verified in `node_modules/madden-franchise/data/schemas/27/M27_620_0.gz`): `TARGETS`, `ROUTESRUN`, `AIRYARDS`, `PRESSURES`, `SEPARATION_TIGHT/NORMAL/OPEN/WIDEOPEN`, `THROWLOCATION_LEFT/MIDDLE/RIGHT`, `INSIDEPOCKETTHROW_*`, `OUTSIDEPOCKETTHROW_*`, `SHORT/MEDIUM/DEEPPASS_*`, `BLITZ_*`, `QBSCRAMBLE_CARRIES/YARDS/TD`, `RUSHSUCCESS`, `RUSH10YARDRUNS`, `RUSH20YARDRUNS`, `LIGHTBOX`, `LOADEDBOX`, `DEFTARGETS`, `CTHALLOWED`, `DEFPRESSURES`, `STOPS`, `STUFFS`, `BIGHITS`, `OLINEPANCAKES`, `GAMESSTARTED`, `DOWNSPLAYED`, `GAMESPLAYED`. Career tables inherit `CareerStats` (DOWNSPLAYED, GAMESPLAYED, GAMESSTARTED, GAMERATING, STAT_KEEP).
- Saves for the end-to-end check: `/mnt/c/Users/jrere/Documents/Madden NFL 27/saves/CAREER-TUNE-P2G` (and `CAREER-MU2026`). Copy before opening; the reader never writes the original.
- Postgres: `madden-db` Docker container; view tests run against `madden_test` per the plan. Scratch-DB recipe in the user memory "Postgres in Docker".
- Commit trailer: `Model: deepseek-v4.1-flash`; `Co-Authored-By: Claude Code <noreply@anthropic.com>`; commit by path; never push.

## 5. Open items

- **Plan §8 Q1–Q4** keep their recommendations (JSONB; show multi-game weeks labelled; list impossible metrics rather than approximate; build wave 1 even with ROUTESRUN blocked). JR's approval covers them; confirm only if unsure.
- The plan's own §3 R3 (does preseason add to career totals?) is answered by the **first live week-1 load**, not by tests — `games_covered` must be ≤ 1.
- `4.4.5` ships two metrics: `stop_rate` (OK) and `stuff_rate_all_snaps` (named, not per-run-snap).
- Any new "blocked" finding reported by a unit goes into the plan's §2.5 and the doc with its reason, per the plan's own rule.
