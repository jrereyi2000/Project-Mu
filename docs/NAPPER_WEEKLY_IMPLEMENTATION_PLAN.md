# Napper Weekly Cadence: Implementation Plan

Based on [NAPPER_UPDATES.md](NAPPER_UPDATES.md), and checked against the code as of 2026-10-19.

**Goal:** every Napper league stays current each week without anyone touching it:
- AI managers set their lineups each week, using IR slots when they can.
- AI managers propose and accept trades each week.
- Injuries show up in the Napper UI.
- Kickers and D/ST get projections like every other position.

---

## 0. What the code survey changed about the picture

These facts came out of reading the code. They change the plan's scope.

| Finding | Where | Effect on the plan |
|---|---|---|
| The frontend already has injury UI: the `injury_status` type field, `napper-injury-pill` (`out`/`ques`/`ir`) CSS, an "Injured Reserve (IR)" section on the Team tab, and IR in the slot-type union. | `types/napper.ts:70`, `NapperTeamTab.tsx:135-137, 494`, `NapperMatchupTab.tsx:282, 353`, `napper.css:332-352` | G3 is mostly backend work. The UI only lights up once the API sends data. |
| `IR` isn't a valid slot anywhere in the backend. It's missing from `roster_format`, from `ELIGIBLE_POSITIONS`, and from the slot-limit check. | `napper_api.py:313-323, 1404-1416`; the default `roster_format` in `2026_12_02_napper_fantasy.sql` | IR needs schema/config work first. |
| Scoring counts every slot except `BN`: `nr.slot <> 'BN'`. | `_get_weekly_team_totals`, `napper_api.py:117` | An IR player would count as a starter. This has to become `NOT IN ('BN','IR')`. |
| Accepted AI trades move the players with `slot = 'BN'` for `week >= W`. | `napper_trade_engine.py`, `respond_trade` | **Fixed 2026-10-19:** `fill_empty_starter_slots` refills AI teams' emptied slots after both trade paths. Phase 4 should reuse it. The optimizer still runs after trades to re-rank whole lineups. |
| Napper scoring already counts K/DEF points with the same formulas as the new projection code. | `_ACTUAL_PLAYER_POINTS_CTE`, `napper_api.py:69` | K/DEF projections and actual scores line up. The same `player_stats` double-count question applies to both. |
| Weekly projections run in `game_stat_generator` (`project_upcoming_week`, then `simulate_season`), called by `step_engines` in `weekly_capture.py`. | `game_stat_generator.py:744-748` | The new Napper weekly step goes right after `simulate_season`. |
| Draft finalize and the seeder copy one lineup into weeks 1–14. `_ensure_matchup_and_rosters` carries the latest week forward when a week has no rows. | `napper_draft_engine.py:1522-1590`, `napper_api.py:341` | The optimizer only needs to rewrite the target week. Later weeks fill in as they come. |
| The local Postgres was down during this survey, so the K/DEF data assumptions weren't checked. | (none) | Phase 1 starts with those checks. |

---

## Phase 1: Finish and land the K/DEF projections (G4) — DONE (commit 1709f23)

The work is already in the uncommitted diff of `engines/fantasy_projection_engine.py`, together with the week-1 preseason projection change.

1. **Check the data with the DB running.**
   - Run `SELECT position, count(*), sum(fantasy_points) FROM player_stats WHERE position IN ('K','DEF') GROUP BY 1`.
   - If K or DEF rows have points, the new kicking and defense queries double-count them. Fix this by excluding `K`/`DEF` from the `player_stats` query in `load_season()` (filter on `list(SKILL_POSITIONS)`, not `list(POS_GROUP)`). Apply the same exclusion to `_ACTUAL_PLAYER_POINTS_CTE`'s first branch in `napper_api.py`.
   - Run `SELECT season, count(*), count(DISTINCT team_id) FROM player_seasons WHERE position='DEF' GROUP BY 1`. Expect 32 rows and 32 teams per season.
   - Run `SELECT count(*), count(team_id) FROM game_stat_defense`. `team_id` should always be filled in.
2. **Feed prior-season K/DEF into the week-1 baselines.** The prior-season block reads only `player_stats`. Add the same kicking and defense queries for `prev_season`, so `prev_player_stats` and `prev_slot_priors` include K/DEF and don't always fall back to `DEFAULT_SLOT_PRIORS`. The simplest way is to factor the two queries into helpers, `_kicking_points(cur, season)` and `_defense_points(cur, season)`, and call them for both seasons.
3. **Share the scoring formula.** Pull the K and DEF SQL fragments into one module-level constant so the projection engine and `napper_api.py` can't drift apart.
4. **Backtest:** run `python3 -m engines.fantasy_projection_engine --backtest`. It should report K/DEF alongside the other groups.
5. **Tests** (`tests/test_napper_engines.py` or a new `tests/test_fantasy_projection_engine.py`):
   - K/DEF rows show up with `pos_rank` values `K1…`/`DEF1…`.
   - A team on bye gets a 0 projection.
   - Week 1 with no completed games returns rows.
   - No double counting when `player_stats` also has K rows.
6. **Re-project** the current season (`--all-weeks`). Check that K/DEF players on Napper rosters now have `proj_fpts`.
7. **Spec:** update the projection-engine section of `PROJECT_SPEC.md` (positions, the K/DEF formulas, week-1 behavior, the `--all-weeks` range).

**Done when:** every K/DEF on a Napper roster has a projection for the current week, the backtest runs, and the tests pass.

---

## Phase 2: IR slot support (needed by G1 and G3) — DONE (2026-10-19)

> Landed as planned, with these differences: waiver claims enforce no roster-size limit at all, so there was nothing to exempt IR from (an IR-aware limit is left for Phase 4's drop logic, D3); the matchup view hides IR players instead of listing them; `get_injury_map` follows the projection engine's countdown rule; no frontend change was needed, since the Team tab already renders `IR` slots and shows the API's 400 message.

1. **Migration `db/migrations/2026_12_04_napper_ir_slot.sql`**:
   - Add `"IR": 1` to the default `roster_format`, then backfill existing leagues with `UPDATE napper_leagues SET roster_format = roster_format || '{"IR": 1}' WHERE NOT roster_format ? 'IR'`.
   - Mirror the change in `db/init_db.sql` if it holds the Napper tables.
2. **`server/napper_api.py`**:
   - Add `"IR"` to `ELIGIBLE_POSITIONS`, with all positions allowed.
   - In `update_lineup`, reject an `IR` move unless the player is IR-eligible (see the rule below). Count IR against `roster_format["IR"]`.
   - Change `_get_weekly_team_totals` to `nr.slot NOT IN ('BN','IR')`.
   - Find every other `slot <> 'BN'` or `slot != "BN"` check with `grep -n "BN" server/napper_api.py engines/napper_*.py` and give IR the same treatment as BN wherever the code means "not starting".
   - Confirm `_get_slot_order` doesn't treat `IR` as a starter. It doesn't today, because it only iterates the starter keys.
3. **Waiver claims** (`claim_waiver_player`, `napper_api.py:1530`): a player in an IR slot doesn't count toward the roster limit. That's the point of IR, because it frees a bench spot.
4. **`napper_trade_engine.py`**:
   - Leave IR players out of trade packages, or allow them and expect a big discount. Excluding them is simpler; that's decision D2.
   - Don't count IR players toward bench depth targets.
5. **IR-eligibility rule (one helper):** `is_ir_eligible(injury) = injury.is_ir or injury.weeks_remaining >= IR_MIN_WEEKS`, with `IR_MIN_WEEKS = 2` (decision D1). Put it in a new shared module, `engines/napper_injuries.py`, together with `get_injury_map(cur, season, week) -> {player_id: {status, injury_type, duration, weeks_remaining, is_ir}}`.

**Done when:** a manual lineup call can move an eligible injured player to IR, the IR player doesn't score, and an ineligible player is rejected with a 400 error.

---

## Phase 3: Injury display (G3) — DONE (2026-10-19)

> Landed as planned. A shared `InjuryPill` (`napperUtils.tsx`; tooltip such as "Out · Upper Arm Fracture · 1 wk left") replaced the inline pills. It also shows in the Players and Trades tabs, the trade builder (IR players can't be selected) and the player modal. Step 4, showing injured players in the weekly rankings instead of hiding them, was skipped.

1. **API payloads:** replace the hardcoded `"injury_status": None` at `napper_api.py:972` (team) and `:1218` (matchup) with values from `get_injury_map(cur, season, wk)`. Load the map once per request, not per player.
   - The Madden report has no "questionable" level, so there are two statuses:
     - `"IR"` when `is_ir`
     - `"O"` when `weeks_remaining > 0`
     - otherwise `null`
   - Also return `injury_type`, `injury_duration` and `injury_weeks_remaining`, so the UI can show "Upper Arm Fracture · 2 wks".
2. **Also add injury fields to:**
   - `teams_rosters` (`:840`), for the League tab's roster view
   - `waivers` (`:1458`), so injured free agents are visible
   - `trades` (`:1636`), so offers involving injured players are visible
3. **Frontend:**
   - `types/napper.ts`: add the optional fields `injury_type`, `injury_duration`, `injury_weeks_remaining`.
   - `NapperTeamTab.tsx` / `NapperMatchupTab.tsx`: the pills already render. Add a `title` tooltip with the type and time remaining, and make the pill class map `"IR"` to `ir`. Today anything other than `"O"` falls through to `ques`.
   - `NapperTeamTab.tsx`: show a warning banner when an injured or bye-week player is in a starting slot, e.g. "2 starters are out this week". The human team is never auto-managed.
   - `NapperPlayersTab.tsx`, `NapperTradesTab.tsx`, `NapperPlayerModal.tsx`: same pill and tooltip. The modal can also show the injury history from `injuries` for the season.
4. **Optional:** `GET /api/fantasy/rankings/weekly` drops injured players through `pos_rank IS NOT NULL`. Consider returning them in a separate `injured` list instead.
5. **Tests** (`tests/test_napper_api.py`):
   - An injured player gets `injury_status = "O"`.
   - An `is_ir` player gets `"IR"`.
   - A healthy player gets `null`.
   - The matchup payload matches the team payload.

**Done when:** Jimmie Offord (pid 186) shows an `O` pill with "Upper Arm Fracture" on team 252's roster and matchup views for weeks 2–3.

---

## Phase 4: AI lineup optimizer (G1) — DONE (2026-10-19)

> Landed as planned. The CLI is `--league N | --all-active`, `--week`, `--dry-run` (`--fill-ir-spots` was later replaced by the waiver run, Phase 7). `fill_empty_starter_slots` and the slot table moved into this module, and the trade engine and API import them from here. The update-lineup endpoint now uses `refresh_matchup_projections`. A one-week injury is benched, not IR'd (D1). Dry run for week 4: 16 AI teams adjusted and 9 players moved to IR across leagues 1 and 15. A second run makes no changes.

**New module `engines/napper_lineup_engine.py`.**

```
optimize_team_lineup(cur, league, team_id, week, proj, injuries) -> list[(player_id, slot)]
optimize_league_lineups(league_id, week, conn=None) -> dict   # AI teams only
CLI: python3 -m engines.napper_lineup_engine --league N --week W
     python3 -m engines.napper_lineup_engine --all-active [--week W]
```

**Algorithm, per AI team and week:**
1. Load the team's `napper_rosters` rows for `week`. If there are none, call `_ensure_matchup_and_rosters` first.
2. Load each player's value for the week from `fantasy_projections` (`projected_points`, falling back to `ros_total_points / remaining weeks` and then 0). Treat players on bye or out as 0, and mark them `unavailable`.
3. **IR pass:**
   - Rank the IR-eligible players by `ros_total_points`, highest first, and fill up to `roster_format["IR"]` IR slots.
   - Any player in IR who is no longer eligible (healthy again) comes back to `BN`. If that puts the roster over its size limit, drop the lowest-ROS bench player. Dropping requires D3.
4. **Starter pass:** fill the slots greedily, in this order:
   1. QB, RB, WR, TE, K, DEF, taking the highest projection among available eligible players
   2. SUPERFLEX
   3. FLEX

   This is the same structure as `fantasy_league_sim.set_lineup()` and draft finalize, so reuse `ELIGIBLE_POSITIONS`. Move it into `engines/napper_injuries.py` or a small `engines/napper_slots.py` so the API and the engines share it.
5. If no available player fits a slot, fall back to the best unavailable one. A slot shouldn't be empty when anyone eligible is on the roster.
6. Everyone else goes to `BN`.
7. Write only the target week's `slot` values (an `UPDATE` per changed player). Then recompute `home_proj`/`away_proj`/`win_prob_home` for the week's matchups using the logic in `update_lineup` step 7. Pull that logic into a helper, `refresh_matchup_projections(cur, league_id, week)`.
8. Post a short `napper_chat` line for notable moves, e.g. "📋 {team} moves {player} to IR". This is optional but cheap.

**Optional free-agent pickup:** when the IR pass opens a roster spot, an AI team claims the best available free agent by ROS points at the most-needed position. Reuse the `claim_waiver_player` logic, pulled into a callable function. Put this behind a flag (`--fill-ir-spots`) so it can ship later.

**Tests** (`tests/test_napper_engines.py`):
- An injured starter is replaced by the best healthy bench player at that position.
- A bye-week starter is benched.
- An IR-eligible player goes to IR, and a healthy one comes back off it.
- FLEX/SUPERFLEX get the best leftover players.
- Human teams are never changed.
- After a trade leaves starting slots empty (traded players arrive as `BN`), the optimizer fills them.

**Done when:** after a run for the current week, no AI starter has a 0 projection while an eligible healthy bench player has a positive one.

---

## Phase 5: Weekly cadence (G2 + G1 wired in) — DONE (2026-10-19)

> Landed as planned in `engines/napper_weekly.py`, hooked into `game_stat_generator` after `simulate_season` and into the preseason step (week 1 lineups, no trades). Two differences: the optional `/advance` endpoint was skipped (`python3 -m engines.napper_weekly --league N` covers it), and the lineup engine now always signs a free agent when a starting slot can't be filled (found when Burden' Bridges' only QB went to IR). The trade engine CLI defaults to the current week and takes `--all-active`. Checked on leagues 1 and 15 in a rolled-back transaction: a second run of the week added no trades or chat.

**New orchestrator** in `engines/napper_weekly.py`:

```
run_napper_week(season, week=None):
  for each league WHERE season = current AND status = 'IN_SEASON':
    1. _ensure_matchup_and_rosters(league, week)   # carry the roster forward
    2. lineup optimizer (AI teams)                 # IR moves before trades see the rosters
    3. trade engine: generate_trade_proposals(league, week)
    4. trade engine: process_ai_to_ai_trades(league, week)
    5. lineup optimizer again                      # fixes BN-arrival holes from accepted trades
    6. UPDATE napper_leagues SET current_week = week
```

1. **Week choice:** the same "first week with scheduled games" rule as `project_upcoming_week`, so it always follows the projections just written.
2. **Pipeline hook:** in `engines/game_stat_generator.py`, right after `simulate_season(season_year)`, add `run_napper_week(season_year)` inside its own `try`/`except` that logs a `[WARN]`. A Napper failure must not fail the stats sync, the same pattern as the other optional engines there.
3. **Preseason hook:** in `step_preseason_engines` (`weekly_capture.py:301`), after `project_weeks(season, [1])`, run only the lineup optimizer for week 1. Skip trades in week 1.
4. **Trade engine CLI:** it defaults to `--league 1 --week 6`. Change `--week` to default to the current week, and add `--all-active`.
5. **Idempotency:**
   - A rerun of the same week should only re-optimize lineups.
   - Proposal generation should skip a league that already has proposals for that week (check `napper_trades` by league and week). Otherwise a pipeline `--resume` duplicates offers and chat banter.
6. **Optional on-demand trigger:** `POST /api/napper/league/{id}/advance` (admin/dev) runs `run_napper_week` for one league, for testing without a full capture.
7. **Tests:** run the orchestrator twice for the same week and check that there are no duplicate trades or chat rows, and that lineups are valid after a trade.

**Done when:** one `python3 weekly_capture.py` run (or `python3 -m engines.game_stat_generator`) leaves every active Napper league with fresh AI lineups for the upcoming week, new trade activity, and correct matchup projections.

---

## Phase 6: Spec, docs, cleanup — DONE (2026-10-19)

> Every spec item below was kept current phase by phase. The final pass also added Napper as a consumer of the `injuries` table, refreshed the trade engine's layout line, and marked `NAPPER_UPDATES.md` complete (kept as the record of the original findings).

- `PROJECT_SPEC.md`:
  - new files (`napper_lineup_engine.py`, `napper_weekly.py`, `napper_injuries.py`, the migration)
  - the IR slot in `roster_format`
  - injury fields in the Napper API payloads
  - the Napper step in the post-week run order
  - the K/DEF projection formulas
  - the trade engine CLI flags
- Mark the goals done in `NAPPER_UPDATES.md`, or delete that file once all phases ship.
- Commit each phase on its own, merging into `main` per CLAUDE.md. Stage only this work's hunks, since the shared checkout has unrelated uncommitted Napper edits.

---

## Phase 7: Waivers and free agency — DONE (2026-10-19)

Designed with the user after Phase 5 (they declined to auto-sign a QB for Burden' Bridges until this existed). It replaced the lineup engine's free-agent signing (always-on for unfillable slots, plus `--fill-ir-spots`).

- **Model:** a weekly waiver run (`engines/napper_waiver_engine.py`) in `run_league_week` after the first lineup pass, from week 2. Players dropped since the last run are on waivers and can only be claimed. Every other unrostered player is a free agent the user can add instantly. AI managers act only in the run.
- **Priority:** reverse standings, the same order every round, one add per team per round.
- **AI pickups:** NEED (a slot nobody can fill; always), STREAM (bye/out starters), UPGRADE (open spots, or ROS swaps past a 1.15× + 1 margin). STREAM + UPGRADE are capped at 2 a week (Waiver Hawks 3). Drops follow D3.
- **Roster limit:** active roster (not IR) ≤ starters + BN. It's enforced on adds, queued claims and IR activation.
- **UI:** the Players tab shows the roster count, waiver priority, a WAIVERS badge with a Claim button, a drop picker, pending claims (cancel) and the last run's results.
- Schema: `db/migrations/2026_12_05_napper_waivers.sql`. Details are in `PROJECT_SPEC.md` §3.4b / §4.4b.

---

## Order and dependencies

```
Phase 1 (K/DEF) ─────────────────────────────┐
Phase 2 (IR slot + napper_injuries) ─┬─> Phase 3 (display)
                                     └─> Phase 4 (optimizer) ─> Phase 5 (cadence) ─> Phase 6
```

Phase 1 and Phase 2 can run in parallel; they touch different files except for the small `_ACTUAL_PLAYER_POINTS_CTE` edit in `napper_api.py`. Once Phase 2 lands, Phases 3 and 4 can also run in parallel.

---

## Decisions (all defaults accepted by the user, 2026-10-19)

| # | Question | Decision |
|---|---|---|
| D1 | Who can go to IR? | `is_ir` **or** `weeks_remaining >= 2`. A 1-week injury is benched, not IR'd. |
| D2 | Can injured or IR players be traded? | No. AI excludes IR players from offers, and the user can't propose them. |
| D3 | Can AI managers drop players to make room (IR return, FA pickup)? | Yes, the lowest-ROS bench player, posted in chat. The FA pickup ships behind a flag. |
| D4 | How many IR slots? | 1 per league, set through `roster_format["IR"]`. |
| D5 | Should the user get a lineup warning or an auto-fix offer for their own team? | A warning banner only. Never an auto-change. |
