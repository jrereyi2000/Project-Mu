# Napper Updates: Injuries, Weekly Lineups, Weekly Trades, K/DEF Projections

Handoff notes from an agent session that ran out of quota partway through. This covers what the session found, what it was asked to build, what it changed, and what's left.

---

## 1. Goals (user request)

> The Napper leagues on a weekly cadence should run both the trade engine and the lineup optimizer, which should include usage of the IR slot where possible. We should also have a way to display injuries on Napper. The projection engine should also run for Kickers and D/ST if possible, no worries if not.

Four workstreams:

| # | Goal | Status |
|---|------|--------|
| G1 | Weekly lineup optimizer for AI Napper managers, with IR slot usage | Not started |
| G2 | Run the trade engine on the weekly cadence | Not started |
| G3 | Show injuries in the Napper UI | Not started |
| G4 | Projections for kickers (K) and team defenses (DEF) | **Done (Phase 1, 2026-10-19), uncommitted.** 2036 re-projected; see `PROJECT_SPEC.md` §3.4 |

---

## 2. State before the work (from the session's investigation)

### Injury ingestion
- **Source:** the Madden franchise injury report screen, captured weekly for all 32 teams by `madden_capture.py`.
- **Extraction:** Apple Vision OCR in `pipeline/extract_injuries_ocr.py` writes `captures/<run>/injuries.csv`.
- **Load:** `pipeline/load_capture.py` loads it into the `injuries` table, with columns `season, week, team_id, player_id, injury_type` (e.g. `"UPPER ARM FRACTURE"`), `duration, weeks_remaining, is_ir`.

### How injuries change projections (`engines/fantasy_projection_engine.py`)
- **Weekly zeroing:** `get_player_injury()` checks for IR or `weeks_remaining > 0`. When either is true, the player gets `projected_points = 0.00`, `pos_rank = NULL`, `matchup_stars = NULL` and `delta = NULL`.
- **ROS discounting:** `ros_total_points` skips any week `w` where `(w - week) < weeks_remaining`, which lowers `ros_rank`.
- **Limitation:** the engine doesn't hand an injured player's volume to the backups. A backup's projection still comes only from its depth-slot prior and its own past production.

### Start/sit in Napper
- **Human team:** the user sets the lineup by hand with `POST /api/napper/league/{id}/lineup` (`server/napper_api.py`).
- **AI teams:** `engines/napper_seeder.py` and `engines/napper_draft_engine.py` set starters and bench once, at seed or draft time, and write the same lineup for **weeks 1–14**. Nothing reviews it each week, so an injured AI starter or one on bye keeps the slot and scores 0.
- **Injury display:** `server/napper_api.py` hardcodes `"injury_status": None` in the roster payload (~line 972) and the matchup payload (~line 1218). Napper never joins `injuries`, so it has no OUT or IR badges.
- **Contrast:** the 100 macro leagues in `engines/fantasy_league_sim.py` do call `set_lineup()` every week from `fantasy_projections`. Those leagues only feed ADP and rostered %, not Napper.

### Trades
- The AI trade logic is in `engines/napper_trade_engine.py`. It runs only from the CLI and isn't called from `pipeline/weekly_capture.py` or from the API:
  ```bash
  python3 -m engines.napper_trade_engine --league 1 --week 6
  ```
  ```bash
  python3 -m engines.napper_trade_engine --league 1 --week 6 --process-ai
  ```
- How it works when run:
  1. It compares `napper_rosters` against `fantasy_projections.ros_total_points`.
  2. It weights positions by scarcity (e.g. RB 1.30 in standard leagues, QB 1.40 in superflex) and finds teams with surplus depth or a starter deficit.
  3. AI managers send reciprocal offers to other AI teams and to the user. Offers are saved to `napper_trades`, with banter posted to `napper_chat`.
  4. With `--process-ai`, pending AI-to-AI offers are accepted or rejected at random. Starter preservation stops a team from emptying a required slot. An accepted trade changes `napper_rosters` for every remaining week.

### Why rostered players show no projection (example: Jimmie Offord, `player_id = 186`, HB, DAL, 86 OVR)
- He projected 12.57 (RB12) in week 1. In week 2 he suffered an upper arm fracture (`"2 - 4 WEEKS"`, `weeks_remaining: 2`). In week 3 he had `weeks_remaining: 1`.
- That zeroed his week 2–3 projection and nulled his `pos_rank`, so:
  - `GET /api/fantasy/rankings/weekly` leaves him out, because it filters on `fp.pos_rank IS NOT NULL`.
  - `FantasyPlayerModal` shows `—` for WK RANK.
  - In Napper he stays in an AI starting RB slot (teams 252 and 312) at `0.00`.
- Other injured AI starters stuck at 0.00 the same way: Christopher Burr (QB, HOU), Zack Wade (WR, HOU), Kai Walker (HB, TEN), Tommy Williams (QB, GB), Makai Lemon (WR, PHI) and Markelle Selvie (QB, WAS).
- Before G4, every K and DEF on a Napper roster had `projected_points = NULL`, because the engine only covered QB/HB/FB/WR/TE.

---

## 3. Changes made (uncommitted, in `engines/fantasy_projection_engine.py`)

The session died after two errors (a network error, then "Agent execution terminated"). It reported **+56 / −4** in this file. `git diff` on the file now shows **+218 / −9**, because the file also holds earlier uncommitted work on week-1 projections (§3b). Neither part has been run or tested.

### 3a. K and DEF projections (this session, G4)
- **Constants:** `K` and `DEF` added to `POS_GROUP`, `GROUPS`, `PRIOR_SLOTS` (1 slot each) and `STARTER_SLOTS` (1 each).
- **Kicking points** from `game_stat_kicking`:
  `3·fgm + fgm_49 + 2·fgm_50 + xpm`
  - FGs of 40–49 yards are worth 4 and FGs of 50+ are worth 5.
  - Points go to `data.points[pid][week]` and to `data.allowed[(opp, "K", week)]`.
- **Defense points:** for each `player_seasons` row with `position = 'DEF'`, the engine joins that team's completed games to `game_stat_defense`, summed per team per game:
  - `sacks + 2·ints + 2·fumble_recoveries + 6·tds + 2·safeties + 2·blocks`
  - plus a points-allowed tier: 0 → 10, ≤6 → 7, ≤13 → 4, ≤20 → 1, ≤27 → 0, ≤34 → −1, otherwise −4.
  - Points go to `data.points` and to `data.allowed[(opp, "DEF", week)]`.
- After this, the existing weekly and ROS path handles K and DEF like the other groups (depth priors, matchup factor and stars, ranks such as `K1` and `DEF1`).

### 3b. Week-1 projections from the prior season (same file, likely an earlier session)
- **`load_season()`** now keeps each player's `ovr` and loads prior-season data:
  - per-player games, totals and PPG (`prev_player_stats`)
  - per-slot PPR priors (`prev_slot_priors`)
  - shrunk defense matchup factors (`prev_def_factors`)
  - matchup stars (`prev_def_stars`)
- **`_project_week_1()`** blends last season's production with the slot prior, using `PRESEASON_PRIOR_GAMES = 4`. A player with no history gets the slot prior × an OVR adjustment (0.6–1.4). The slot prior falls back to `DEFAULT_SLOT_PRIORS`, which includes K = 7.5 and DEF = 6.5.
- **`project_week()`** calls `_project_week_1` when week 1 has no completed games yet. `--all-weeks` now covers weeks 1–18 instead of 2–18.

### Things to check before committing
- **Double counting:** with K and DEF in `POS_GROUP`, the existing `player_stats` query (`st.position = ANY(...)`) also pulls K and DEF rows. If `player_stats.fantasy_points` already has kicker or defense points, they get counted twice.
- **DEF identity:** check that `player_seasons` holds one `position = 'DEF'` row per team, as the draft engine assumes, and that `game_stat_defense.team_id` is filled in.
- **Prior-season K/DEF priors:** these come only from `player_stats`. If that table has no K/DEF points, week 1 falls back to the hardcoded defaults.
- **Week-1 ROS loop:** for a player on IR with `weeks_remaining == 0`, it skips every week. That matches the existing behavior but should be confirmed.
- Run `python3 -m engines.fantasy_projection_engine --backtest` and `pytest tests/` (the projection and Napper tests).
- Update `PROJECT_SPEC.md` (the projection engine's positions, scoring formulas and week-1 behavior).

---

## 4. Remaining work

### G1. Weekly AI lineup optimizer with IR
- New module, e.g. `engines/napper_lineup_engine.py`, CLI `--league N --week W` (or every league).
- For each AI team and week:
  1. Move players with `is_ir` or `weeks_remaining > 0` into open IR slots, if the league's roster settings have them. The `napper_rosters.slot` value for IR needs to be confirmed or added to the slot vocabulary.
  2. Fill starting slots (QB/RB/WR/TE/FLEX/SUPERFLEX/K/DEF, per league settings) greedily by `fantasy_projections.projected_points`. Bye-week and OUT players score 0 and should be sat.
  3. Write that week's lineup and any later weeks that haven't been played. Overwriting the copied week 1–14 rows is fine.
- An optional step: when an IR move opens a bench spot, add the best free agent by ROS points. This needs waiver logic, which doesn't exist yet.
- The human team should never be auto-set. At most, flag their injured starters (see G3).
- Reuse the selection logic in `fantasy_league_sim.set_lineup()` where it fits.

### G2. Weekly trade cadence
- Add a weekly Napper step after projections are written: lineup optimizer → `napper_trade_engine --process-ai` → lineup optimizer again, since trades change rosters.
- Hook it into `pipeline/weekly_capture.py`, or wherever the weekly run order is defined, after the projection engine. Add it to the run-order section of `PROJECT_SPEC.md`.
- Run it for each active league (see the `napper_active_season` migration).

### G3. Injury display in Napper
- In `server/napper_api.py`, replace the hardcoded `"injury_status": None` in the roster (~972) and matchup (~1218) payloads with a join to `injuries` on `(season, week, player_id)`. Return:
  - a status: `IR` when `is_ir`, `OUT` when `weeks_remaining > 0`, otherwise `null`
  - `injury_type`
  - `weeks_remaining`
- Frontend: `Mu_Network/src/types/napper.ts`, `napperUtils.tsx`, `NapperTeamTab.tsx`, `NapperMatchupTab.tsx`, `NapperPlayersTab.tsx` and `NapperPlayerModal.tsx`.
  - Show a small OUT/IR badge next to the name, with the injury type and return estimate in a tooltip or the modal.
  - Warn when an injured player is in a starting slot.
- Optional: drop the `pos_rank IS NOT NULL` filter in `GET /api/fantasy/rankings/weekly`, or mark injured players there instead of hiding them.

### G4. Finish K/DEF projections
- Finish the checks in §3, run the projection engine for the season, and confirm that K and DEF rows on Napper rosters now have projections.
