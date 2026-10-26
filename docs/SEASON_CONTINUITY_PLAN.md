# Multi-Season Continuity: Implementation Plan

**Status (2026-10-26):** Written, not built. Phase 1 must land before the 2036 Super Bowl is loaded; phase 2's capture before this offseason's retirements are processed in Madden.
**Scope:** Keep each finished season on record and connect it to the next: a season-end archive, head-coach records from 2035, a retirement scan with a player season ledger, Napper league history with a restart for the new season, and a career layer that reads from all of it.
**Reference specs:** `PROJECT_SPEC.md` §1.2 (running the system), §2.11 (Coach of the Year), §3.1–§3.3a (players, seasons, coaches), §3.3e (League Transactions), §3.3f (GM agents, rollover), §3.4b / §4.4b (Napper), §6.0 (weekly run).

---

## 0. Decisions (JR, 2026-10-26)

| # | Decision |
|---|---|
| D1 | **Retirements are scanned in the offseason.** A player on last season's rosters who is not on this season's and **is not recorded as retiring** is a **free agent**. There is no age- or rating-based guessing. |
| D2 | **Coach records start in 2035.** 2035 is the first season with every game on record, and every 2035 head coach has a career snapshot through 2034. |
| D3 | **Napper stays one-season redraft.** League history is kept, and a finished league can be **restarted for the new season** with the same name, managers and settings. |
| D4 | **Capture forward here; 2026–2034 is rebuilt separately.** (JR, revised 2026-10-26.) Nothing in this plan reconstructs 2026–2034. `docs/HISTORY_BACKFILL_PLAN.md` generates it at season level, every row marked `source = 'SYNTH'` and fit to what's already stored, into the same tables (`team_season_results`, `league_seasons`, `coach_season_lines`, `player_seasons`, `season_stats`). |

Defaults added by this plan (change any before its phase):

| # | Default |
|---|---|
| D5 | Archive standings are computed from `games`, not read off a Madden screen. Playoff seeds come from the playoff games themselves (exact). Division places use the NFL tiebreakers this league can compute (win %, head-to-head, division record, common games, conference record, strength of victory, strength of schedule), and a tie they can't break is flagged. |
| D6 | A coach hired by the carousel (`engines/coaching_carousel.py`) with no earlier record starts at 0-0 in the Mu era. His pre-Mu career is "not recorded", never invented. |
| D7 | A restarted Napper league uses the reverse of last season's final standings as its draft order; JR can still change it in the existing Draft Order modal. |
| D8 | Retired players stay in `players` (stats, awards and records point at them). A retired player who later signs (Madden lets players unretire) is moved back by the next transactions or roster load. |

---

## 1. Survey (live database and code, 2026-10-26)

| Area | What exists | Gap |
|---|---|---|
| Season boundary | The **Season scan** (`pipeline/weekly_capture.py --mode season`) moves `league_state` to the new season, rescans rosters, contracts, team overalls, injuries and prospects, then runs the GM rollover (`engines/gm_rollover.py`: hot seat, directions, `fo_quality` carried forward, pick seeding) and scouting. Offseason stages (`gm_trade_engine.OFFSEASON_STAGES`): post-Super Bowl, re-signing, free agency, draft, preseason. | Nothing freezes the season that just ended. |
| Players | `players` (identity only: no retired flag). `player_seasons` exists for 2035 (1,751) and 2036 (1,829). 1,490 players appear in both. **261 players on 2035 rosters have no 2036 row** and nothing says why. | No transitions, no retirements, no year-over-year OVR or development-trait change. |
| Retirements | No code captures them. `madden_capture.OPTIONS_MENU` lists `RETIRE` (the user's own retirement option), which must never be pressed. League Transactions has only been loaded for the 2036 regular season, so it's unknown whether its offseason headings list retirements. | A capture source must be confirmed on the console (§2). |
| Standings | `team_seasons` holds `wins`, `losses` and analytics. It has **no ties, points for/against, division place, seed or playoff result**. Full 32-team rows exist for 2035–2036 only. | A frozen final table per season. |
| Head coaches | `coach_tenures` (start and end seasons). `coach_seasons` holds career snapshots for 2025 (30) and 2034 (32). All 32 head coaches of 2035 have a 2034 snapshot, and 2035 has every game on record (272 regular season, 6 Wild Card, 4 Divisional, 2 Conference, 1 Super Bowl). `coach_profile.tenure_record` returns null "with team" records when an earlier season of the tenure isn't fully on record (e.g. Zac Taylor, since 2019). | No per-season coach lines; nothing writes a snapshot at season end. |
| Coaching carousel | Another session is building it (`engines/coaching_carousel.py`, `coach_hot_seat`, `coordinator_hot_seat`, `staff_moves`; uncommitted on 2026-10-26). It fires and hires and writes tenures for season + 1. | It writes no records, so it complements this plan. |
| Career stats | `season_stats` and `season_stat_lines` per season, meaningful from 2035. | No career totals or Mu-era milestones. |
| Napper | `napper_leagues` has one row per league per season (`season`, `status` IN_SEASON / PRE_DRAFT, `champion_team_id`). `napper_teams`, `napper_matchups` and `napper_rosters` are per league, so a finished league keeps its results. `napper_user_profiles.trophies` exists, but the profile endpoint returns a hard-coded `3`. | No link from one season's league to the next, no restart, no history view, no real trophy count. |

---

## 2. Console facts to confirm (before phase 2's capture)

| # | Question | Why it matters |
|---|---|---|
| C1 | Where does Madden list the season's retirements? Candidates: offseason rows in League Transactions, a retirements list or news item at the start of the re-signing stage, or a player-card flag. | It decides whether phase 2 extends the League Transactions scan or adds a new read-only screen to `madden_capture.py`. |
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

### 3.2 Head-coach records from 2035 (phase 1, D2, D6)

- **`coach_season_lines`**: (`coach_id`, `team_id`, `season`) PK, `wins`, `losses`, `ties`, `playoff_wins`, `playoff_losses`, `playoff_result` (as above), `games` (regular-season games coached). It is written by `season_archive.archive` from the head coach's tenure covering the season (one coach per team per season; a mid-season change is out of scope until the carousel creates one).
- **`coach_seasons`** (existing career snapshot) gets a row each season end: the previous snapshot + that season's line. Backfill 2035 = 2034 snapshot + 2035 lines.
- **`engines/coach_profile.coach_profile`:**
  - `record_with_team` = the sum of the coach's `coach_season_lines` with that team plus the current season from `games`. When his tenure began before 2035 it also returns `record_with_team_since: 2035` instead of null.
  - The career record = the latest snapshot + later lines + the current season. A coach with no snapshot (D6) reads as Mu-era only, labelled.
- **Consumers:** the Front Office staff card shows "With team (since 2035)" where that applies, in place of "early seasons not on record". The writer tool `get_coach_profile` passes the same fields.

### 3.3 Retirement scan and player season ledger (phase 2, D1, D8)

**Retirement scan** (shape set by C1–C4):
- Capture: a read-only step in the offseason stage run at the stage C2 names. It is either new rows in the League Transactions scan or a new screen in `madden_capture.py`. Both write `retirements/` frames.
- Extract: `pipeline/extract_retirements.py` produces `retirements.csv` (`name, position, team, age, flags`) using the same on-device OCR plus Gemini re-read pattern as League Transactions.
- Load: `pipeline/load_retirements.py`, inside `load_capture`'s transaction, writes **`player_retirements`** (`season` = the season just finished, `player_id`, `player_name`, `position`, `team_id`, `capture_id`; unique `(season, player_id)`; an unmatched or ambiguous name is stored with `player_id` NULL and a flag). It also sets **`players.retired_season`** (new nullable column). A later signing of that player (transactions load) or a roster scan that finds him clears it (D8).

**Player season ledger**, `engines/season_ledger.py`, run by the Season scan right after the roster load:
- **`player_season_transitions`**: (`player_id`, `to_season`) PK, `from_season`, `from_team_id`, `to_team_id`, `status`, `ovr_before` / `ovr_after`, `dev_before` / `dev_after`, `age_before` / `age_after`, `exp_before` / `exp_after`, `source`, `flags`.
- `status` is decided by a pure `classify()`:
  - `RETAINED`: same team both seasons.
  - `MOVED`: a different team. `source` = `TRADE` (an executed GM trade), `TRANSACTION` (an offseason signing), else `ROSTER_SCAN`.
  - `RETIRED`: in `player_retirements` for `from_season`.
  - `FREE_AGENT`: on last season's rosters, on no roster now, and not retired (**D1**).
  - `DRAFTED`: a first card with `years_of_experience = 0` that matches a drafted prospect (`draft_prospects` with `actual_pick` / `drafted_team_id` set by `pipeline/draft_results.py`).
  - `NEW`: a first card that isn't a drafted rookie (undrafted rookie or a new free agent).
- When the retirement scan didn't run for `from_season`, `FREE_AGENT` rows carry the flag `retirement_scan_missing`, so a later scan can turn them into `RETIRED` (a re-run is idempotent).
- Backfill 2035 → 2036: the 261 missing players become `FREE_AGENT` with `retirement_scan_missing` (D1; no 2035 scan exists).
- Read helpers: `free_agent_pool(cur, season)` (last seen, not retired, not on a roster) and `career_path(cur, player_id)` (every season's team and status).

**Consumers** (later phases; each needs JR's go-ahead where it touches the editorial slate):
- Writer tools `get_player_career` (path, transitions and Mu-era totals) and `get_offseason_movement` (retirements, notable free agents, offseason moves by team).
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
| **2. Retirement scan + player ledger** | Console facts C1–C4 answered; capture, extract and load for retirements; `players.retired_season`; `player_retirements`; `engines/season_ledger.py` in the Season scan; 2035 → 2036 backfill; `free_agent_pool`, `career_path`. | The 2036 offseason's retirements load with every matched player marked; the 2036 → 2037 ledger has a status for every 2036 player; the 261 2035 departures read `FREE_AGENT` with `retirement_scan_missing`. |
| **3. Napper history + restart** | Migration (lineage, `COMPLETE`, `final_rank`); restart, history and profile endpoints; League History panel and restart banner. | A completed 2036 league restarts as a 2037 league with the same managers and settings, reverse-standings draft order and a 0-0 start; history lists both seasons; trophies count real championships; the 2036 league can't be edited. |
| **4. Career layer + consumers** | `player_career_mu` view; writer tools; player Career section; Team hub History view; proposals to JR for offseason-package lines. | A player's page shows his Mu-era path and totals; the Panthers' history lists 2035–2036 with coach, GM and result. |

---

## 6. Tests

- `tests/test_season_archive.py`: pure standings, ties, seeds from a synthetic bracket, division tiebreakers (and `UNBROKEN`), playoff results, coach lines and snapshot arithmetic. A read-only live check of 2035 against `games` (skipped when Postgres is down).
- `tests/test_season_ledger.py`: pure `classify()` for every status, the D1 rule, `retirement_scan_missing`, idempotence; retirement extraction and matching on fixtures.
- `tests/test_napper_restart.py`: **pure only** (the restart plan built from a fixture league). The existing Napper API, engine, end-to-end and draft tests write to the live `madden` database (memory), so this work must not run them wholesale; any live restart check runs on a restored copy via `DATABASE_URL`.
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
- **Timing:** phase 1 after the 2036 Super Bowl can still backfill (the archive reads `games`), so missing the deadline costs nothing but a rerun. Phase 2's retirement capture can't be recovered once Madden stops showing the list (C2).
