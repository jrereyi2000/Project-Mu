# Multi-Season Continuity: Implementation Plan

**Status (2026-10-27):** Phases 1, 1b, 2a and 3 built: the season archive, head-coach records from 2026, the guard that keeps the archive off generated seasons, and the player season ledger with its retirement tables (2a: `engines/season_ledger.py`, migration `2027_01_03_player_season_ledger.sql`, 2035 → 2036 built). Phase 3: Napper lineage, completion freeze, restart, history and computed trophies (`2027_01_04_napper_lineage.sql`; leagues 1, 2 and 15 completed). Phase 2b, parsing League Transactions `RETIRED` rows, waits for the first offseason scan. (Phase 1: `engines/season_archive.py`, migration `2026_12_24_season_archive.sql`, 2035 backfilled.) Phase 4 built 2026-10-27 (career milestones since 2026 on #records, the ledger's Career Path on player pages, `get_offseason_movement`, ledger moves in `get_player_career`). Phase 2b not built; phase 2b's retirement rows come from the League Transactions scan (C1). **Revised 2026-10-27 for the 2026–2034 history backfill** (`docs/HISTORY_BACKFILL_PLAN.md`, applied 2026-10-26): it filled the archive tables and player history for 2026–2034 as `SYNTH` rows and built most of phase 4. See D9–D12, §1 and the phase table.
**Scope:** Keep each finished season on record and connect it to the next: a season-end archive, head-coach records from 2035, a retirement scan with a player season ledger, Napper league history with a restart for the new season, and a career layer that reads from all of it.
**Reference specs:** `PROJECT_SPEC.md` §1.2 (running the system), §2.11 (Coach of the Year), §3.1–§3.3a (players, seasons, coaches), §3.3e (League Transactions), §3.3f (GM agents, rollover), §3.4b / §4.4b (Napper), §6.0 (weekly run).

---

## 0. Decisions (JR, 2026-10-26)

| # | Decision |
|---|---|
| D1 | **Retirements are scanned in the offseason.** A player on last season's rosters who is not on this season's and **is not recorded as retiring** is a **free agent**. There is no age- or rating-based guessing. |
| D2 | **Coach with-team records start in 2026** (JR, revised 2026-10-27; first set at 2035 on 2026-10-26). Every finished season from 2026 has a `coach_season_lines` row: 2026–2034 from the history backfill, whose per-coach totals are the exact `coach_seasons` 2025 → 2034 differences ("the wins are not synthetic"), and 2035 on from the archive. A tenure that began before 2026 counts from 2026. |
| D3 | **Napper stays one-season redraft.** League history is kept, and a finished league can be **restarted for the new season** with the same name, managers and settings. |
| D4 | **Capture forward here; 2026–2034 is rebuilt separately.** (JR, revised 2026-10-26.) Nothing in this plan reconstructs 2026–2034. `docs/HISTORY_BACKFILL_PLAN.md` generates it at season level, every row marked `source = 'SYNTH'` and fit to what's already stored, into the same tables (`team_season_results`, `league_seasons`, `coach_season_lines`, `player_seasons`, `season_stats`). |

Defaults added by this plan (change any before its phase):

| # | Default |
|---|---|
| D5 | Archive standings are computed from `games`, not read off a Madden screen. Playoff seeds come from the playoff games themselves (exact). Division places use the NFL tiebreakers this league can compute (win %, head-to-head, division record, common games, conference record, strength of victory, strength of schedule), and a tie they can't break is flagged. |
| D6 | A coach hired by the carousel (`engines/coaching_carousel.py`) with no earlier record starts at 0-0 in the Mu era. His pre-Mu career is "not recorded", never invented. |
| D7 | A restarted Napper league uses the reverse of last season's final standings as its draft order; JR can still change it in the existing Draft Order modal. |
| D8 | Retired players stay in `players` (stats, awards and records point at them). A retired player who later signs (Madden lets players unretire) is moved back by the next transactions or roster load. |

Added 2026-10-27 after the history backfill (defaults; change any before its phase):

| # | Default |
|---|---|
| D9 | **Two owners, split by `source`.** The history backfill owns every `SYNTH` row (2026–2034) and only ever deletes `SYNTH` rows. The season archive owns `CAPTURE` rows (2035 on) and **never writes over a season whose rows are `SYNTH`**, `--replace` included. A season is one or the other, never both. |
| D10 | **The ledger covers captured seasons only** (2035 → 2036 onward). 2026–2034 transitions aren't generated: the history keeps each pool player on his 2035 team (history B16), so they would say nothing. A player's whole path still reads from `player_seasons` across both sources. |
| D11 | **History-era retirements are implied, not stored.** A player with `players.origin` `HISTORY` or `HISTORY_FILLIN` retired after his last generated season (all 1,811 end by 2034). Readers derive it from `origin`; `players.retired_season` is written only by the retirement scan. This keeps a regenerated or reverted history (history B15, `revert`) from leaving stale retirement rows behind. |
| D12 | **The generated 2026–2034 `coach_season_lines` count** (JR, 2026-10-27, replacing "stay since 2035"; history B17). `coach_profile.RECORDS_FROM` = 2026: with-team records on the staff card and in `get_coach_profile` sum them with the archive's lines and the current season's games. |

---

## 1. Survey (live database and code, 2026-10-26)

| Area | What exists | Gap |
|---|---|---|
| Season boundary | The **Season scan** (`pipeline/weekly_capture.py --mode season`) moves `league_state` to the new season, rescans rosters, contracts, team overalls, injuries and prospects, then runs the GM rollover (`engines/gm_rollover.py`: hot seat, directions, `fo_quality` carried forward, pick seeding) and scouting. Offseason stages (`gm_trade_engine.OFFSEASON_STAGES`): post-Super Bowl, re-signing, free agency, draft, preseason. | Nothing freezes the season that just ended. |
| Players | `players` (no retired flag; `origin` NULL, `HISTORY` (1,732 real players who retired during 2026–2034) or `HISTORY_FILLIN` (79)). `player_seasons`: captured 2035 (1,751) and 2036 (1,829); `SYNTH` 2026–2034 (1,522–1,716 a season). 1,490 players appear in both captured seasons. **261 players on 2035 rosters have no 2036 row** (all captured players, none from the history) and nothing says why. | No transitions or retirements between captured seasons; no year-over-year OVR or development-trait change (generated seasons carry stats, not ratings). |
| Retirements | No code captures them. `madden_capture.OPTIONS_MENU` lists `RETIRE` (the user's own retirement option), which must never be pressed. League Transactions has only been loaded for the 2036 regular season, so it's unknown whether its offseason headings list retirements. | A capture source must be confirmed on the console (§2). |
| Standings | `team_seasons` holds `wins`, `losses` and analytics only. `team_season_results` / `league_seasons` now hold 2026–2034 (`SYNTH`, history §3.3j) and 2035 (`CAPTURE`, this plan's archive). | Built: phase 1 archives every season from 2036 on. A guard against overwriting `SYNTH` seasons is missing (D9, phase 1b). |
| Head coaches | `coach_tenures` (start and end seasons). `coach_seasons` holds career snapshots for 2025 (30) and 2034 (32). All 32 head coaches of 2035 have a 2034 snapshot, and 2035 has every game on record (272 regular season, 6 Wild Card, 4 Divisional, 2 Conference, 1 Super Bowl). `coach_profile.tenure_record` returns null "with team" records when an earlier season of the tenure isn't fully on record (e.g. Zac Taylor, since 2019). | No per-season coach lines; nothing writes a snapshot at season end. |
| Coaching carousel | Another session is building it (`engines/coaching_carousel.py`, `coach_hot_seat`, `coordinator_hot_seat`, `staff_moves`; uncommitted on 2026-10-26). It fires and hires and writes tenures for season + 1. | It writes no records, so it complements this plan. |
| Career stats | `season_stats` / `season_stat_lines` for every season 2026–2036 (`SYNTH` before 2035); `player_career_totals` (Madden's Career Stats, history §3.3i). History phase 4 built the player page's history seasons and "Career since 2026" totals (`server/api.career_totals`), the Team History tab, `get_player_career` / `get_team_history`, and the Record Book's single-season Record Watch (`/api/records/watch`). | Career milestones (e.g. first to 10,000 yards since 2026); a ledger-driven career path. |
| Tests | Since `c68c367` the suite can't reach live `madden` (`tests/conftest.py`): unit tests get no database, and `@pytest.mark.integration` tests run on `madden_test`, copied from `madden`. | Napper restart and ledger integration tests can use `madden_test`. |
| Napper | `napper_leagues` has one row per league per season (`season`, `status` IN_SEASON / PRE_DRAFT, `champion_team_id`). `napper_teams`, `napper_matchups` and `napper_rosters` are per league, so a finished league keeps its results. `napper_user_profiles.trophies` exists, but the profile endpoint returns a hard-coded `3`. | No link from one season's league to the next, no restart, no history view, no real trophy count. |

---

## 2. Console facts to confirm (before phase 2's capture)

| # | Question | Why it matters |
|---|---|---|
| C1 | **Answered (JR, 2026-10-26): retirements are listed in the League Transactions screen.** The first offseason scan will give example rows to fix the wording. | Phase 2 extends the existing League Transactions scan (`pipeline/transactions_parse.py` grammar, `load_transactions` effects) with a `RETIRED` type, not a new screen. |
| C2 | At which offseason stage are retirements decided and shown, and does the list stay viewable after that stage? | It sets when the scan runs (§4). |
| C3 | Does the list name the player's last team, position and age (or years)? | Name matching (`pipeline/capture_matching`) needs at least name + position, and team helps. |
| C4 | Can the screen be read without pressing Cross on anything that changes state? | Same safety rule as master plan D7: read-only screens only, named one by one in `ROSTER_CROSS_OK`-style allow-lists. `RETIRE` in Options is never pressed. |

---

## 3. Design

### 3.1 Season-end archive (phase 1)

New tables (`db/migrations/<date>_season_archive.sql`, folded into `init_db.sql`):

- **`league_seasons`**: `season` PK, `champion_team_id`, `runner_up_team_id`, `super_bowl_game_id`, `regular_season_games`, `postseason_games`, `finalized_at`, `notes`. One row per finished season.
- **`team_season_results`**: (`season`, `team_id`) PK, `wins`, `losses`, `ties`, `points_for`, `points_against`, `division_place` (1–4), `division_tiebreak` (text: which rule decided it, or `UNBROKEN`), `conference_seed` (1–7, NULL if out), `playoff_result` (`MISSED` | `WILD_CARD` | `DIVISIONAL` | `CONFERENCE` | `SUPER_BOWL_LOSS` | `CHAMPION`), `playoff_wins`, `playoff_losses`, `head_coach_id`, `gm_id` (the GM whose tenure covered the season, if the GM migration is applied), `finalized_at`.

Engine `engines/season_archive.py`:
- `standings(games)`, `seeds(playoff_games, standings)` and `division_places(...)` are pure. Seeds come from the bracket (D5): Wild Card pairs 2v7, 3v6 and 4v5, and the #1 seed is the conference team in the Divisional round with no Wild Card game.
- `archive(cur, season)` writes `league_seasons` and `team_season_results` once the season's Super Bowl is `COMPLETED`. It is idempotent: a second run replaces the season's rows with the same values. `python3 -m engines.season_archive [--season S] [--dry-run]`.
- **Run:** a new optional step in `pipeline/weekly_capture.py` after the Super Bowl postgame load (`--mode postgame`), before the articles. A failure only warns, like the GM step. Backfill: run once for 2035 (complete). 2034 and earlier are not archived from `games` (D4); the history backfill writes them as `SYNTH` rows.
- **Guard (phase 1b, D9):** `archive()` returns status `history` and writes nothing when the season's `league_seasons` row is `SYNTH`, even with `--replace`. Without it, `--season 2034 --replace` would rebuild 2034 from its 20 stored games and delete the generated standings and coach lines, which are canon (history B15). Rows it writes say `source = 'CAPTURE'` explicitly, not by column default.

### 3.2 Head-coach records from 2026 (phase 1, D2, D6, D12)

- **`coach_season_lines`**: (`coach_id`, `team_id`, `season`) PK, `wins`, `losses`, `ties`, `playoff_wins`, `playoff_losses`, `playoff_result` (as above), `games` (regular-season games coached). It is written by `season_archive.archive` from the head coach's tenure covering the season (one coach per team per season; a mid-season change is out of scope until the carousel creates one).
- **`coach_seasons`** (existing career snapshot) gets a row each season end: the previous snapshot + that season's line. Backfill 2035 = 2034 snapshot + 2035 lines.
- **`engines/coach_profile.coach_profile`:**
  - `record_with_team` = the sum of the coach's `coach_season_lines` with that team for each finished season (`with_team_record`; a finished season with no line falls back to its games when all 17 are stored, else the record is null) plus the current season from `games`. It counts from `record_with_team_since` = max(tenure start, 2026), so a tenure that began earlier reads "since 2026".
  - The career record = the latest snapshot + later lines + the current season. A coach with no snapshot (D6) reads as Mu-era only, labelled.
- **Consumers:** the Front Office staff card shows "With team (since 2026)" where that applies, in place of "early seasons not on record". The writer tool `get_coach_profile` passes the same fields.

### 3.3 Retirement scan and player season ledger (phase 2, D1, D8)

**Retirement scan** (shape set by C1–C4):
- Capture: the existing League Transactions scan (C1). Its offseason headings carry the retirement rows; no new screen.
- Parse: a `RETIRED` type in `pipeline/transactions_parse.py`, its wording fixed from the first offseason scan's rows. The `transactions.type` CHECK needs `RETIRED` added (migration).
- Load: the `RETIRED` effect in `pipeline/load_transactions.py` (inside `load_capture`'s transaction) writes **`player_retirements`** (`season` = the season just finished, `player_id`, `player_name`, `position`, `team_id`, `capture_id`; unique `(season, player_id)`; an unmatched or ambiguous name is stored with `player_id` NULL and a flag). It also sets **`players.retired_season`** (new nullable column). A later signing of that player (transactions load) or a roster scan that finds him clears it (D8).

**Player season ledger**, `engines/season_ledger.py`, run by the Season scan right after the roster load:
- **`player_season_transitions`** (captured seasons only, D10; `player_id` FK `ON DELETE CASCADE`, so a history revert that deletes `HISTORY` players is never blocked): (`player_id`, `to_season`) PK, `from_season`, `from_team_id`, `to_team_id`, `status`, `ovr_before` / `ovr_after`, `dev_before` / `dev_after`, `age_before` / `age_after`, `exp_before` / `exp_after`, `source`, `flags`.
- `status` is decided by a pure `classify()`:
  - `RETAINED`: same team both seasons.
  - `MOVED`: a different team. `source` = `TRADE` (an executed GM trade), `TRANSACTION` (an offseason signing), else `ROSTER_SCAN`.
  - `RETIRED`: in `player_retirements` for `from_season`.
  - `FREE_AGENT`: on last season's rosters, on no roster now, and not retired (**D1**).
  - `DRAFTED`: a first card with `years_of_experience = 0` that matches a drafted prospect (`draft_prospects` with `actual_pick` / `drafted_team_id` set by `pipeline/draft_results.py`).
  - `NEW`: a first card that isn't a drafted rookie (undrafted rookie or a new free agent).
- When the retirement scan didn't run for `from_season`, `FREE_AGENT` rows carry the flag `retirement_scan_missing`, so a later scan can turn them into `RETIRED` (a re-run is idempotent).
- Backfill 2035 → 2036: the 261 missing players become `FREE_AGENT` with `retirement_scan_missing` (D1; no 2035 scan exists).
- Read helpers: `free_agent_pool(cur, season)` (last seen in a captured season, not retired, not on a roster; `HISTORY` / `HISTORY_FILLIN` players are retired by D11 and never in it) and `career_path(cur, player_id)` (every `player_seasons` season, `SYNTH` ones marked "hist", with the transition status between captured seasons and the implied retirement for history players).

**Consumers** (later phases; each needs JR's go-ahead where it touches the editorial slate):
- `get_player_career` already exists (history §3.3l): add the transitions and retirement to it rather than a new tool. New: `get_offseason_movement` (retirements, notable free agents, offseason moves by team).
- Retirement and progression lines in the offseason package (`newsroom/offseason_service.py`), proposed to JR as rule changes, not switched on silently.
- The Front Office tab's Transactions panel can show the offseason's retirements and departures for the team.

### 3.4 Napper league history and restart (phase 3, D3, D7)

Schema (Napper migration, folded into `init_db.sql`):
- `napper_leagues.previous_league_id` (FK self, NULL for a league's first season) and a `lineage_id` (the first season's league id, so history is one query).
- `napper_leagues.status` gains `COMPLETE`, set when the fantasy championship is final (where `champion_team_id` is set today).
- `napper_teams.final_rank` (1–N, written at completion: champion, runner-up, then playoff results, then regular-season record).

Endpoints (`server/napper_api.py`):
- `POST /api/napper/league/{id}/restart` (only for a `COMPLETE` league once `league_state.current_season` > its season; refused otherwise, and refused if a successor already exists). It creates the new league for the current season with the same name, settings, scoring, roster format, avatar and `lineage_id`, `previous_league_id` = this league, and status `PRE_DRAFT`. It copies every team (user, AI manager, handle, team name, avatar, persona) with a fresh 0-0 record, and seeds the draft order from D7. The old league becomes read-only (no lineup, waiver or trade writes; those endpoints already check status).
- `GET /api/napper/league/{id}/history`: each season of the lineage with the champion, the user's team and final rank, every team's final record and rank, and links to that season's league.
- `GET /api/napper/profile`: `trophies` = championships won by the user's teams across all leagues (replacing the hard-coded 3), plus `seasons_played`.

Frontend (`src/components/napper/`): a **League History** panel in the LEAGUE tab (season list, champions, the user's finishes); on a `COMPLETE` league after the NFL season rolls over, a **"Start the {season} season"** banner that calls restart and opens the new league's draft room.

### 3.5 Career layer (phase 4)

**Mostly built by the history backfill's phase 4 (2026-10-27 review):** history seasons and "Career since 2026" totals on the player page (`server/api.career_totals`, which replaces the `player_career_mu` view below), the Team History tab (`GET /api/team/{abbr}/history`, which reads this plan's archive from 2035), `get_player_career` / `get_team_history`, and the Record Book's single-season Record Watch. What's left for phase 4: career milestones (career totals since 2026 crossing round marks, fed to Record Watch and the slate), the ledger's path on the player page (moved / retired / free agent between seasons), and the offseason-package proposals. The original design follows for reference.


- A view **`player_career_mu`**: per player, Mu-era (2035+) totals over `season_stats` and the defense and kicking `season_stat_lines`, plus seasons played and teams. Labelled "since 2035" everywhere; pre-Mu careers are not recorded and never estimated. Once the history backfill is applied, the view covers 2026 onward ("since 2026"), with generated seasons marked (`docs/HISTORY_BACKFILL_PLAN.md` §5).
- Career milestones (e.g. the first Mu-era 10,000-yard passer) feed the Record Book's Record Watch, which the `#records` work (`docs/RECORD_BOOK_AND_AWARDS_PLAN.md`) owns. This plan supplies the view, not the page.
- Player page: a **Career** section (career path from the ledger, Mu-era totals by season). Team hub: a **History** view (season-by-season `team_season_results`, head coach, GM, playoff result, award winners).

---

## 4. Order at the season boundary

1. **Super Bowl load** (Week 23, `--mode postgame`) → **season archive** for the season (§3.1) → coach lines and career snapshot (§3.2). The Napper fantasy leagues are already `COMPLETE` by then (their playoffs end in the NFL regular season).
2. **Coaching carousel** (other session): firings and hirings for season + 1. The hires read the archive's coach records.
3. **Offseason stage runs**: at the stage C2 names, the **retirement scan** (§3.3) is captured and loaded.
4. **Season scan** → roster load → **player season ledger** (§3.3) → GM rollover → scouting → … (`league_state` moves to the new season here).
5. **Napper restart** becomes available (§3.4).

Every step is idempotent and optional in the run: a failure warns and the step can be rerun by hand.

---

## 5. Phases (one commit to `main` each, spec updated in the same commit)

| Phase | Delivers | Done when |
|---|---|---|
| **1. Season archive + coach records from 2035** | Migration (`league_seasons`, `team_season_results`, `coach_season_lines`); `engines/season_archive.py`; postgame step; 2035 backfill; `coach_profile` with-team and career changes; Front Office staff card label. **Before the 2036 Super Bowl load.** | 2035: 32 team rows with ties, points, division places, seeds 1–7 per conference matching the bracket, Panthers `CHAMPION`, Broncos `SUPER_BOWL_LOSS`; 32 coach lines; 2035 snapshots equal 2034 + 2035. Zac Taylor shows a with-team record since 2035. A second run changes nothing. |
| **1b. Archive guard** (built 2026-10-27, with D2's with-team records from 2026) | D9: `archive()` refuses `SYNTH` seasons (status `history`), writes `source = 'CAPTURE'` explicitly; a test that `--replace` on 2034 writes nothing. | `--season 2034 --replace` leaves every 2034 `SYNTH` row in place. |
| **2a. Player ledger** (built 2026-10-27) | `player_season_transitions`, `player_retirements`, `players.retired_season`, `RETIRED` allowed in `transactions`; `engines/season_ledger.py` in the Season scan; 2035 → 2036 built; `free_agent_pool`, `career_path`. | 2,090 players accounted for (1,277 retained, 133 moved, 341 free agents all flagged `retirement_scan_missing`, 182 drafted, 157 new); the pool (307) has no history players. |
| **2b. Retirement scan** (after the first offseason scan) | C1 answered (League Transactions); parse and load `RETIRED` rows; `players.retired_season`; `player_retirements`; `engines/season_ledger.py` in the Season scan; 2035 → 2036 backfill; `free_agent_pool`, `career_path` (D10, D11). | The 2036 offseason's retirements load with every matched player marked; the 2036 → 2037 ledger has a status for every 2036 player; the 261 2035 departures read `FREE_AGENT` with `retirement_scan_missing`; no `HISTORY` player is in the free-agent pool. |
| **3. Napper history + restart** (built 2026-10-27) | Migration (lineage, `COMPLETE`, `final_rank`); restart, history and profile endpoints; League History panel and restart banner. | A completed 2036 league restarts as a 2037 league with the same managers and settings, reverse-standings draft order and a 0-0 start; history lists both seasons; trophies count real championships; the 2036 league can't be edited. |
| **4. Career layer + consumers** (reduced) | History phase 4 built the totals, player history seasons, Team History tab and career writer tools. Left: career milestones since 2026 into Record Watch and the slate; the ledger path on the player page and in `get_player_career`; `get_offseason_movement`; proposals to JR for offseason-package lines. | A player's page shows moves, free-agent spells and retirement between captured seasons; a career mark since 2026 shows in Record Watch. |

---

## 6. Tests

- `tests/test_season_archive.py`: pure standings, ties, seeds from a synthetic bracket, division tiebreakers (and `UNBROKEN`), playoff results, coach lines and snapshot arithmetic. Integration (`madden_test`): 2035 against `games`, and (phase 1b) a `SYNTH` season refused even with `--replace`.
- `tests/test_season_ledger.py`: pure `classify()` for every status, the D1 rule, `retirement_scan_missing`, idempotence; retirement extraction and matching on fixtures.
- `tests/test_napper_restart.py`: the restart plan as pure tests on a fixture league, plus `@pytest.mark.integration` tests of the endpoints on `madden_test` (`tests/conftest.py` blocks live `madden` for the whole suite since `c68c367`).
- `tests/test_coach_profile.py` additions: with-team records since 2035; a D6 hire with no snapshot.

---

## 7. Spec updates (per phase)

- §1.1 Repository Layout: new engines, pipeline modules, migrations, tests and this plan.
- §3: new tables and columns (`league_seasons`, `team_season_results`, `coach_season_lines`, `player_retirements`, `players.retired_season`, `player_season_transitions`, Napper lineage columns); `player_career_mu`.
- §6.0: the archive step after the Super Bowl load, the retirement scan's offseason stage, the ledger step in the Season scan.
- §2.11 / coach profile, §3.4b / §4.4b Napper, §5.2 (staff card label, League History, Career section).

---

## 8. Risks

- **Retirement source unknown (C1):** if Madden shows no readable list, D1 still holds (every departure is a free agent) and phase 2 ships the ledger alone, with `retirement_scan_missing` on all departures.
- **Name matching:** retirements and departures rely on the same matching as League Transactions; an ambiguous name is stored unmatched with a flag, never guessed.
- **Shared checkout:** the coaching-carousel and Record Book sessions are editing `init_db.sql`, `server/api.py`, `App.tsx` and `engines/coach_*` at the same time. Stage only this work's hunks, and agree migration dates so files don't collide.
- **Mid-season coach changes:** not modelled; `coach_season_lines` assumes one head coach per team per season until the carousel introduces in-season firings.
- **History regeneration or revert** (history B15, `revert`): it deletes only `SYNTH` rows and `HISTORY` players, so the archive's `CAPTURE` rows survive; ledger rows cascade with their player (D10), and history-era retirements are derived (D11), so nothing goes stale.
- **Readers mixing sources:** anything that sums across seasons (milestones, career paths) must label `SYNTH` seasons "hist" and never treat them as having games or box scores (history B11).
- **Timing:** phase 1 after the 2036 Super Bowl can still backfill (the archive reads `games`), so missing the deadline costs nothing but a rerun. Phase 2's retirement capture can't be recovered once Madden stops showing the list (C2).

## 9. Proposals for JR (phase 4; nothing switched on)

- **P1:** a milestone reached first (`first = alone`) becomes a slate candidate, like a record broken (`find_records_broken_in_week`), and an Agent Wire insider post.
- **P2:** after phase 2b's retirement scan, an offseason-package retirements and departures roundup built on `get_offseason_movement`.
- **P3:** offseason beat pieces cite `get_offseason_movement` for their team.
