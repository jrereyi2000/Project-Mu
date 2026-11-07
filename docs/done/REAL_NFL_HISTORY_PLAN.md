# Project Mu: Real NFL History Backfill Plan (1999–2025)

**Status:** approved by JR (2026-11-07, "Approve as written", every §8 question as recommended); built and on main (2026-11-07). Migration `2027_02_04` applied to no real database and no real data loaded (JR's go-ahead, commands in §2.12); scratch tests on `mu_scratch_test` passed.
- As built (U1): `nfl_awards_key` is unique on `coalesce(gsis_id, pfr_id, coach_name, player_name)` (Wikipedia gives no PFR ids); `nfl_player_seasons` adds `st_tds` (nflverse's whole `special_teams_tds`), `kr_tds`/`pr_tds` stay NULL (S20); `store.STAT_COLUMNS` is the column list, `read.nfl_first_seasons` and `read.nfl_champions` were added for U6/U7.
- As built (U2): pure parsers emit the Mu abbreviation in `team_id`/`home_team_id`/`away_team_id`; `nflverse.resolve_team_ids` swaps in `teams.id` before a write. `parse_player_seasons(..., skipped=[])` leaves out rows with no team code (one in 1999: Steve Bono) instead of raising. `players_from_stats` adds the 6 stat-file players missing from `players.csv`. Computed place and seed (1999–2001 only) differ from nfldata on 50 of 768 places and 67 seeds when run on 2002–2025, so treat 1999–2001 seeds as approximate.
- As built (wave 0 / spike): see S20–S22. R5 (pgids across two saves) stays open: it needs a second snapshot from JR's PC; overrides are guarded by name and position.
- As built (U3): Pro-Football-Reference blocked, so `awards.py` reads Wikipedia (fallback, Pro Bowl dropped per Q6); the CSV adds a `gsis_id` column resolved from nflverse (1,741 rows: 7 single awards × 27 seasons + 2003 co-MVP, 750 First and 800 Second Team AP All-Pro; 2 rows without `gsis_id`: Pat McAfee 2014, Deonte Harris 2019). Positions are Wikipedia's (`LT`, `EDGE`, `SCB`, `APR`, ...). CLI: `python3 -m engines.nfl_history.awards build`.
- As built (U4): position groups are looser than §2.6 (Madden LE/RE match nflverse LB/DE/DL because nflverse lists most edge rushers LB; CB/FS/SS match any defensive back, for position changes like Jalen Ramsey's); a name that exists but whose evidence disagrees goes to unmatched, not fuzzy; nfl_players has no jersey, so jersey isn't evidence; ESPN-id roster teams are ignored for people whose last season is before 2020 (S20). On CAREER-WEEK1 (offline, no DB): 2,750 of 2,853 players linked (2,736 EXACT, 14 FUZZY), 97.3% of veterans, every 80+ player but the legends Elway and Rice and the rookie Vega Ioane; 1 ambiguous (Anthony Johnson Jr.). `run_link` with apply also records an `nfl_history_loads` row.
- As built (U5): `load` also adds `players_from_stats` stubs and nulls award/draft `gsis_id`s missing from `nfl_players`; a dry run parses only (no DB writes). Scratch run on `mu_scratch_test` (init_db + clean seed): load 24,873 players, 61,537 season lines, 7,276 games, 861 team / 904 coach seasons, 1,740 awards, 7,154 picks in ~5 s, reload identical; coach baseline 32 coaches (6 first-time: Minter, Brady, Hafley, Monken, M. LaFleur, Kubiak; no `COACH_NAME_FIX` needed); `check` 417 rows, 7 small differences (2011 passing yards for Brees/Stafford/Rodgers, four rushing totals).
- As built (U6): endpoint reads go through `api.nfl_player_career` / `api.nfl_team_seasons` / `history_api.nfl_award_seasons`, which swallow errors and run last in each request; the Awards page "Real NFL" table shows the seven award-history columns plus the champion (All-Pro stays on the All-Pro view). `server/history_api.py`'s milestones docstring still says "since 2026" (U6 didn't own the milestones change).
- As built (U7): milestones' top-level `since` stays 2026; each crossed/chasing entry carries its own `since` and the output adds `real_career_included`. `get_team_history`'s real seasons leave out All-Pro rows from the awards list.
**Builds on:** PROJECT_SPEC §1 "Current direction" (fresh PC franchise from the 2026 season, real NFL history through 2025), §2.9 (Record Book), §3.1 (`players.madden_pgid`), §3.3, §3.3a, §3.3h (season archive, `coach_seasons`), §4.7, §5.2; [`HISTORY_BACKFILL_PLAN.md`](./HISTORY_BACKFILL_PLAN.md) (old 2034 universe: reused only for its nflverse download helper's pattern and its data files, not its design); [`MADDEN_PILOT_AND_TUNING_PLAN.md`](../in_progress/MADDEN_PILOT_AND_TUNING_PLAN.md) §2.4 (real 2015–2025 tuning targets, same nflverse cache).

**Goal:** The new league starts in 2026, but its players and coaches had real careers before that. Today a fresh franchise database knows none of it: a player page starts at 2026, a coach's career record starts at zero, and a writer can't say a quarterback just passed 40,000 career yards. This plan loads real NFL history for 1999–2025 from nflverse (and award lists from Pro-Football-Reference) into its own `nfl_*` tables: every player's real season stat lines (regular season and playoffs), every game, team standings and playoff results, head-coach records, awards (MVP, OPOY, DPOY, OROY, DROY, COTY, Super Bowl MVP, AP All-Pro, Pro Bowl) and the draft. It links real players to the save's Madden players with a review report for anything it can't settle, and it gives the 32 current head coaches their real career records as the 2025 baseline the season archive builds on. Real rows never sit in the tables the league writes, so they can't mix with simulated seasons. Madden ratings are not backfilled (JR agreed, 2026-11-07: inconsistent across editions, and the sim doesn't use them).

**Delivers:**
- Migration `db/migrations/2027_02_04_nfl_history.sql` (mirrored in `db/init_db.sql`): tables `nfl_players`, `nfl_player_seasons`, `nfl_games`, `nfl_team_seasons`, `nfl_coach_seasons`, `nfl_awards`, `nfl_draft_picks`, `nfl_player_links`, `nfl_history_loads`; view `nfl_season_lines`.
- Package `engines/nfl_history/`: `fetch.py` (cached downloads), `teams.py` (historical team codes), `nflverse.py` (pure parsing and derivations), `awards.py` (Pro-Football-Reference award lists), `link.py` (real player ↔ Madden player matching), `coach_baseline.py`, `check.py` (record book cross-check), `store.py` (writes), `read.py` (reads for the site and writers), `__main__.py` (CLI).
- CLI `python3 -m engines.nfl_history fetch | load | link | coach-baseline | init | check | status` with `--db` (required for every command except `fetch`), `--seasons 1999-2025`, `--refresh`, `--apply` (dry run without it), `--snapshot <pc_save_reader json>`.
- Data files: `data/history/nfl_awards_1999_2025.csv` (committed, reviewable) and `data/history/nfl_link_overrides.csv` (JR's manual links).
- Review report `verify_output/nfl_history/link_<db>.md` / `.csv`; record check `verify_output/nfl_history/record_check_<db>.md`.
- Live-table write: one `coach_seasons` row per current head coach for season 2025 (`team_id` NULL), the real career baseline.
- Readers: player page "Real NFL career (through 2025)" block, Team History real seasons, Awards page real seasons, writer tools `get_player_career` / `get_team_history`, career milestones counting the real career, popularity Awards Equity counting real awards.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | A fresh franchise database is created by `db/seed_clean_db.py` (`run_clean_seed`, called from `pipeline/watch_save.py:486`) **before** the first save load; the seed writes teams, coaches, tenures, record book, GMs and writers, and no players or `coach_seasons` (`db/seed_clean_db.py:80-480`, no `coach_seasons` insert). Players arrive from `pipeline/load_save.py:426` with `madden_pgid`. | The backfill can't link players at seed time. It runs after the first save load (`init`). |
| S2 | `coach_seasons` is empty in a fresh database. The season archive gives a coach with no earlier snapshot `mu_era_only = true` and starts him at zero (`engines/season_archive.py:319-328`; spec §3.3h). The old universe got its 2025 baseline (season 2025, `team_id` NULL) from `db/migrations/2026_10_31_coach_baseline.sql`, now marked `OBSOLETE_FRESH_SAVE`. | Without a baseline, Andy Reid's career record restarts at 0-0 when 2026 is archived. `coach-baseline` writes the 2025 rows in the same shape, and must run before the first archive. |
| S3 | All 32 head coaches seeded for 2026 started as NFL head coaches in 1999 or later (Reid's first season is 1999; `db/seed_clean_db.py` `COACHES_DATA`). | 1999–2025 covers every current coach's whole career. No pre-1999 offsets needed. |
| S4 | `season_stat_lines` is a view anchored on `player_seasons` (`db/init_db.sql:2364-2396`), and `player_seasons` is `UNIQUE (player_id, season)` with a `players` FK (`db/init_db.sql:93-121`). `season_stats`, the `season_stat_*` tables, `awards`, `league_seasons`, `team_season_results` and `coach_season_lines` already carry `source VARCHAR(8)` (`CAPTURE` / `SYNTH`; `db/init_db.sql:1807-1849`). | Writing real seasons into these tables would need a `players` row for every retired real player and a `player_seasons` row for every real season, both of which over 40 reader files treat as league facts. Separate tables avoid that (Q1). |
| S5 | `team_season_results.division_place` is `CHECK BETWEEN 1 AND 4` (`db/init_db.sql:1485`) and requires a `league_seasons` row; 1999–2001 had six-team divisions and 31 teams. `coach_season_lines.coach_id` is `NOT NULL` FK `coaches` (`db/init_db.sql:1499-1511`). | Real team seasons don't fit the archive tables before 2002, and real coach lines would need a `coaches` row for every past coach. |
| S6 | The coaching carousel's retread pool is every `coaches` row with a tenure that ended in the last two seasons (`engines/coaching_carousel.py:437-443`). | Adding past real coaches to `coaches` / `coach_tenures` would make them hire candidates. The plan adds none. |
| S7 | The record book reads earlier history only from `franchise_records` and `league_record_entries` (sources `NFL`, `BOOK`, `LEAGUE`), and per-season values from `season_stats` / `season_stat_lines` for the season it scores (`engines/records_engine.py:76-121`). The real NFL record book through 2025 is already seeded (`db/seed_clean_db.py` step 5; spec §2.9). | The record book needs no change and must not read `nfl_*`. A report-only `check` compares the two. |
| S8 | The player page builds its career from `player_seasons` + `player_stats` and `season_stats`, both `source <> 'SYNTH'` (`server/api.py:967-1000`); `career_totals` sums every row (`server/api.py:1389-1405`). | Real rows in `season_stats` would silently enter the page's league career. A separate block keeps the two apart. |
| S9 | Career milestones sum `season_stat_lines` from `FIRST_SEASON = 2026` (`engines/career_milestones.py:23`, `:120-129`). | Today a 2026 veteran's "career" is only his 2026 season. U7 adds his real career as `career_before` (Q3). |
| S10 | Popularity Awards Equity reads `awards WHERE season < s` (`engines/player_popularity_engine.py:134`) and weights the last 3 seasons plus 25% of career points (`:81-89`, `AWARD_POINTS` at `:8`). | In 2026 nobody has awards, so recent real MVPs count for nothing. U7 adds `nfl_awards` (Q4). |
| S11 | `playoff_clout_engine` reads `team_season_results WHERE season < 2035` (`engines/playoff_clout_engine.py:44-48`, `:88-93`); `coach_hot_seat` reads `coach_season_lines` (`engines/coach_hot_seat.py:247-252`); `gm_record` reads `team_season_results` with `gm_id` (`engines/gm_record.py:63`). | None of these see `nfl_*`; their behaviour is unchanged. Wiring them is out of scope. |
| S12 | `engines/history_backfill/sources.py` (marked `OBSOLETE_FRESH_SAVE`) has the cached downloader `_get` (`:86-95`) writing to `data/history/.cache/` (git-ignored, `.gitignore:16`); `CAREER_SEASONS = range(2016, 2026)` (`:42`). `engines/tuning/targets.py:37` imports `_get` from it. | The new package gets its own downloader on the same cache folder (shared downloads with tuning); the old module stays for the audit plan to delete. |
| S13 | `data/history/nfl_careers_through_2025.csv` has 1,886 career totals summed from 2016 only (114 rows start in 2016, so earlier careers like Andy Dalton's, 2011, are cut off), keyed to the old database's `player_id`; `engines/tuning/progression_fit.py:86` reads it. `data/history/rosters_2026.csv` is ESPN's real 2026 rosters (2,557 players, `espn_id`, team, position, birth date; its `player_id` column points at the old database, 194 filled). | Neither file can be the career source. `rosters_2026.csv` is a useful matching input (real 2026 team per `espn_id`). Both files stay unchanged. |
| S14 | The save snapshot carries per player `first_name`, `last_name`, `position`, `team_index`, `age`, `years_pro`, `year_drafted`, `college` (a reference decoded by `pipeline/colleges.py`), `jersey`, `height_in`, `weight_lbs` (`pipeline/save_reader/pc_save_reader.mjs:302-316`). The database keeps name, height, weight, number (`players`) and position, team, age, experience (`player_seasons`), but not draft year or college. | Matching works from the database alone, and better with `--snapshot` (draft year, college). |
| S15 | `player_name_aliases` (`db/migrations/2027_01_07_player_name_aliases.sql`) holds a player's original name where Madden's commentary rename changed it. | Aliases are extra names for matching. |
| S16 | `players.espn_id` exists (`db/init_db.sql:42`) but the save loader never fills it. | The link table carries real IDs; `players` is left alone. |
| S17 | `tests/conftest.py` blocks every database but `madden_test`; `db/franchises.py:42` `RESERVED_DBS` lists `madden`, `madden_2036`, `madden_test`. | Tests are synthetic. The CLI refuses `madden` and `madden_2036` by name. |
| S18 | Migrations on disk end at `2027_02_03_persona_focus.sql`; `2027_02_02` (owners) is reserved and unwritten (`docs/in_progress/PERSONALITY_PLAN.md` §5.3). `docs/not_started/COACHING_TREES_PLAN.md` (written alongside this one) needs no migration (its S14). | This plan takes `2027_02_04` (Q8). |
| S19 | Spec mismatch: §1.2 and the `sources.py` docstring still describe `nfl_careers_through_2025.csv` as covering "every 2035/2036 player", and nothing in the spec says a fresh database has no coach baseline. | Noted for the orchestrator's spec pass (§7). |
| S20 | **Spike (a), 2026-11-06.** All nflverse release files download (GitHub, no auth). `stats_player_{reg,post}_<season>.csv` share one 160-column layout (1999 and 2025 checked): no `kr_tds`/`pr_tds` split, only `special_teams_tds`; `fg_long` present; height in `players.csv` is already inches. `games.csv` covers 1999–2026 (2025 complete through the Super Bowl), game types `REG/WC/DIV/CON/SB`, coaches filled for every 1999 game, `overtime` blank for 150 rows. `nfldata/standings.csv` has `season, conf, division, team, div_rank, seed, playoff` but **starts in 2002**. `draft_picks.csv` 1980–2026 (257 picks in 2026), `pick` is the overall pick, team codes are PFR-style (`GNB, KAN, LVR, NOR, NWE, SDG, SFO, TAM`). No real 2026 rostered player has a rookie season before 2005 (Rodgers 2005, Folk 2007); five `rosters_2026.csv` rookies share an `espn_id` with a 1978–2000 nflverse player (nflverse id collisions). | `teams.py` maps PFR draft codes too; 1999–2001 place and seed are `COMPUTED`; `kr_tds`/`pr_tds` stay NULL and a `st_tds` column keeps `special_teams_tds`; ESPN-id links must agree on position group and entry season. |
| S21 | **Spike (b), 2026-11-06.** Pro-Football-Reference answers both test pages (AP MVP award page, `years/2024/allpro.htm`) with HTTP 403 and a Cloudflare "Just a moment..." challenge for a plain client. Wikipedia's API (`action=parse`) serves the award and `<season> All-Pro Team` pages. | §2.5 fallback taken: `awards.py` builds the CSV from Wikipedia (seven single winners and AP First/Second Team All-Pro), Pro Bowl dropped (Q6). Wikipedia has no PFR ids, so the CSV carries `gsis_id` resolved at build time from nflverse (name + team + season), blank when unresolved. |
| S22 | **Spike (c), 2026-11-06.** `madden_test` has 3,903 players and none with `madden_pgid` (old universe), so the count used the CAREER-WEEK1 snapshot (`verify_output/v2_spike/week1.json`, 2,855 non-prospect players): 2,719 (95.2%) have exactly one same-name real person, 34 several, 102 none (65 veterans: nicknames like Cam/Dax/Dru/Olu, "Slaton Jr" spellings, Madden legends such as Elway and Rice). The snapshot's `year_drafted` is relative: real draft year = 2027 + `year_drafted` for most (±1). This snapshot has no college. pgids across two saves were not compared (needs a second snapshot from JR's PC). | Nicknames and fuzzy matching are needed to reach 98%; draft-year evidence uses `2027 + year_drafted` with ±1. R5 stays open; overrides are guarded by name and position. |

## 1. Scope

**In:**
- Real player season lines, regular season and postseason, 1999–2025, for **every** player nflverse lists (complete league seasons, not just current players), with the stat columns in §2.3.
- Every game 1999–2025 (regular season and playoffs) with scores and both head coaches.
- Team seasons 1999–2025: record, points, division and place, conference seed, playoff result.
- Head-coach seasons 1999–2025 from the per-game coach, interim coaches included.
- Awards 1999–2025: MVP, OPOY, DPOY, OROY, DROY, COTY, SBMVP, AP First and Second Team All-Pro, Pro Bowl.
- Draft picks 1999–2026 (the 2026 class is Madden 27's real rookies).
- Linking real players to the database's Madden players, with overrides and a review report.
- The 2025 `coach_seasons` baseline for current head coaches.
- Idempotent loads with recorded source files and hashes; network downloads cached.
- Run on `madden_test` first, then each franchise database once after its first save load.
- Readers: player page, Team History, Awards page, two writer tools, career milestones, popularity Awards Equity.
- A report-only cross-check against the seeded record book.

**Out (non-goals):**
- Madden ratings for past seasons.
- Any real row in `season_stats`, `season_stat_*`, `player_seasons`, `player_stats`, `awards`, `league_seasons`, `team_season_results`, `coach_season_lines`, `franchise_records`, `league_record_entries`, `players` or `coaches` (only `coach_seasons` gets real rows, §2.7).
- Record book changes. It stays on its Pro-Football-Reference seed.
- Seasons before 1999.
- Weekly player game logs and per-team splits for players traded midseason (a traded player's season sits with his last team; §3 R7).
- Real contract and earnings history (a follow-up for `engines/career_earnings.py`; nflverse has Over The Cap contracts).
- Personality profile evidence from real careers (a follow-up for PERSONALITY_PLAN §5.1).
- Coach with-team records before 2026 in `engines/coach_profile.py`, hot seat, playoff clout, progression, fantasy ADP: they keep reading league tables only. Follow-ups.
- Coordinators' past careers (nflverse has head coaches only). The coaching-trees plan may read `nfl_coach_seasons`.
- `madden_2036` and live `madden`: never touched.
- Running automatically from the save watcher (Q7).

## 2. Design

### 2.1 Where real history lives

Real history goes into its own `nfl_*` tables, never the league's tables (Q1). A reader that wants both reads both and labels them. Every row in `nfl_*` is real by definition; the view `nfl_season_lines` also exposes `source = 'NFL'` (the record book's word for real history) so a reader can `UNION ALL` it with `season_stat_lines` and still tell them apart.

The one exception is `coach_seasons`: the season archive, `coach_records` and `coach_profile` need a real career snapshot to build on (S2). The 2025 row with `team_id` NULL is that baseline, the same convention the old universe used.

Team IDs are the database's `teams.id` for the **current** franchise (Oakland and Las Vegas → LV, San Diego → LAC, St. Louis → LAR, Houston from 2002); the historical code is kept in `team_abbr`.

### 2.2 Schema (`db/migrations/2027_02_04_nfl_history.sql`, mirrored at the end of `db/init_db.sql`)

Idempotent (`CREATE ... IF NOT EXISTS`), in one transaction. Header: run `./db/backup_db.sh` first; apply on `madden_test` first.

```sql
CREATE TABLE IF NOT EXISTS nfl_players (            -- one real person (nflverse players.csv), upserted, never deleted
    gsis_id        TEXT PRIMARY KEY,
    espn_id        TEXT, pfr_id TEXT,
    display_name   TEXT NOT NULL, first_name TEXT, last_name TEXT,
    position       VARCHAR(8), birth_date DATE, college TEXT,
    height_in      SMALLINT, weight SMALLINT,
    rookie_season  INT, last_season INT,
    draft_year     INT, draft_round SMALLINT, draft_pick SMALLINT, draft_team_abbr VARCHAR(4)
);
CREATE INDEX IF NOT EXISTS nfl_players_espn ON nfl_players (espn_id);
CREATE INDEX IF NOT EXISTS nfl_players_pfr  ON nfl_players (pfr_id);

CREATE TABLE IF NOT EXISTS nfl_player_seasons (     -- nflverse stats_player_{reg,post}_<season>.csv
    gsis_id      TEXT NOT NULL REFERENCES nfl_players(gsis_id),
    season       INT NOT NULL,
    season_type  VARCHAR(4) NOT NULL CHECK (season_type IN ('REG', 'POST')),
    team_id      INT REFERENCES teams(id),           -- current franchise of team_abbr
    team_abbr    VARCHAR(4) NOT NULL,                -- nflverse's code that season (OAK, SD, STL, LA, ...)
    position     VARCHAR(8),
    games_played INT,
    -- the columns of NFL_STAT_COLUMNS (§2.3), INT except def_sacks NUMERIC(4,1); NULL = nflverse has no value
    ...,
    PRIMARY KEY (gsis_id, season, season_type)
);
CREATE INDEX IF NOT EXISTS nfl_player_seasons_season ON nfl_player_seasons (season, season_type);

CREATE TABLE IF NOT EXISTS nfl_games (              -- nflverse schedules/games.csv
    game_id      TEXT PRIMARY KEY,                   -- nflverse id, e.g. 2019_21_TEN_KC
    season INT NOT NULL, week INT NOT NULL,
    game_type    VARCHAR(4) NOT NULL CHECK (game_type IN ('REG', 'WC', 'DIV', 'CON', 'SB')),
    gameday      DATE,
    home_team_id INT REFERENCES teams(id), away_team_id INT REFERENCES teams(id),
    home_abbr VARCHAR(4) NOT NULL, away_abbr VARCHAR(4) NOT NULL,
    home_score INT, away_score INT, overtime BOOLEAN,
    home_coach TEXT, away_coach TEXT
);
CREATE INDEX IF NOT EXISTS nfl_games_season ON nfl_games (season, game_type);

CREATE TABLE IF NOT EXISTS nfl_team_seasons (
    season INT NOT NULL, team_id INT NOT NULL REFERENCES teams(id),
    team_abbr VARCHAR(4) NOT NULL, conference VARCHAR(3), division TEXT,   -- the division that season
    wins INT NOT NULL, losses INT NOT NULL, ties INT NOT NULL,
    points_for INT NOT NULL, points_against INT NOT NULL,
    division_place SMALLINT CHECK (division_place >= 1),                   -- up to 6 before 2002
    conference_seed SMALLINT CHECK (conference_seed BETWEEN 1 AND 7),      -- NULL = missed
    playoff_result VARCHAR(16) NOT NULL CHECK (playoff_result IN
        ('MISSED', 'WILD_CARD', 'DIVISIONAL', 'CONFERENCE', 'SUPER_BOWL_LOSS', 'CHAMPION')),
    playoff_wins INT NOT NULL DEFAULT 0, playoff_losses INT NOT NULL DEFAULT 0,
    head_coach TEXT,                                 -- coached most of the regular season
    head_coach_id INT REFERENCES coaches(id) ON DELETE SET NULL,           -- only when he is in this database's coaches
    standings_source VARCHAR(8) NOT NULL CHECK (standings_source IN ('NFLDATA', 'COMPUTED')),
    PRIMARY KEY (season, team_id)
);

CREATE TABLE IF NOT EXISTS nfl_coach_seasons (      -- from nfl_games' per-game coach
    coach_name TEXT NOT NULL, season INT NOT NULL, team_id INT NOT NULL REFERENCES teams(id),
    coach_id INT REFERENCES coaches(id) ON DELETE SET NULL,
    wins INT NOT NULL, losses INT NOT NULL, ties INT NOT NULL, games INT NOT NULL,
    full_season BOOLEAN NOT NULL,                    -- coached every regular-season game of the team
    playoff_wins INT NOT NULL DEFAULT 0, playoff_losses INT NOT NULL DEFAULT 0,
    playoff_result VARCHAR(16) NOT NULL,
    super_bowl_wins INT NOT NULL DEFAULT 0, super_bowl_losses INT NOT NULL DEFAULT 0,
    PRIMARY KEY (coach_name, season, team_id)
);

CREATE TABLE IF NOT EXISTS nfl_awards (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    award_type VARCHAR(32) NOT NULL,                 -- NFL_AWARD_TYPES (§2.5)
    gsis_id TEXT REFERENCES nfl_players(gsis_id), pfr_id TEXT,
    player_name TEXT, coach_name TEXT,
    team_id INT REFERENCES teams(id), team_abbr VARCHAR(4),
    position VARCHAR(8)
);
CREATE UNIQUE INDEX IF NOT EXISTS nfl_awards_key
    ON nfl_awards (season, award_type, coalesce(position, ''), coalesce(pfr_id, coach_name, player_name));

CREATE TABLE IF NOT EXISTS nfl_draft_picks (        -- nflverse draft_picks/draft_picks.csv
    season INT NOT NULL, overall SMALLINT NOT NULL,
    round SMALLINT NOT NULL, pick SMALLINT,
    team_id INT REFERENCES teams(id), team_abbr VARCHAR(4) NOT NULL,
    gsis_id TEXT REFERENCES nfl_players(gsis_id), pfr_id TEXT,
    player_name TEXT NOT NULL, position VARCHAR(8), college TEXT,
    PRIMARY KEY (season, overall)
);

CREATE TABLE IF NOT EXISTS nfl_player_links (       -- this database's Madden player <-> the real person
    player_id INT PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    gsis_id   TEXT NOT NULL UNIQUE REFERENCES nfl_players(gsis_id),
    method    VARCHAR(10) NOT NULL CHECK (method IN ('EXACT', 'ESPN', 'ALIAS', 'FUZZY', 'OVERRIDE')),
    score     NUMERIC(5, 2),
    evidence  JSONB NOT NULL DEFAULT '{}',
    linked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS nfl_history_loads (      -- one row per applied command
    id SERIAL PRIMARY KEY,
    kind VARCHAR(16) NOT NULL CHECK (kind IN ('players', 'stats', 'games', 'teams', 'awards', 'draft',
                                              'link', 'coach_baseline')),
    first_season INT, last_season INT,
    files JSONB NOT NULL DEFAULT '[]',               -- [{"url", "cache_name", "sha256", "bytes"}]
    counts JSONB NOT NULL DEFAULT '{}',
    report JSONB NOT NULL DEFAULT '{}',
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(), finished_at TIMESTAMPTZ
);

CREATE OR REPLACE VIEW nfl_season_lines AS
SELECT s.*, p.display_name, l.player_id, 'NFL'::varchar(8) AS source
FROM nfl_player_seasons s
JOIN nfl_players p ON p.gsis_id = s.gsis_id
LEFT JOIN nfl_player_links l ON l.gsis_id = s.gsis_id;
```

### 2.3 Sources and parsing (`fetch.py`, `teams.py`, `nflverse.py`)

`fetch.py`:
- `CACHE_DIR = data/history/.cache/` (shared with tuning). `get(url, cache_name, refresh=False) -> FetchedFile(path, url, cache_name, sha256, bytes)`. User-Agent `project-mu-nfl-history`; 60 s timeout; writes to a temp name then renames, so a cut download never caches.
- `NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"`. Files: `players/players.csv`; `stats_player/stats_player_reg_<season>.csv` and `stats_player/stats_player_post_<season>.csv` for each season; `schedules/games.csv`; `draft_picks/draft_picks.csv`. Standings: `NFLDATA_STANDINGS = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/standings.csv"` (spike confirms columns `season, team, div_rank, seed`).
- `fetch_all(seasons, refresh) -> list[FetchedFile]`. No database.

`teams.py` (pure): `mu_abbr(nfl_abbr, season) -> str` with `NFL_TO_MU = {"ARI": "AZ", "WSH": "WAS", "OAK": "LV", "SD": "LAC", "STL": "LAR", "LA": "LAR"}` (nflverse uses `LA` for the Rams from 2016); raises `UnknownTeam` for anything else unknown. `division_of(nfl_abbr, season) -> (conference, division)` from `DIVISIONS_1999_2001` (the six-team-division alignment) and `teams.division` from 2002.

`nflverse.py` (pure, parsed CSV rows in, plain dicts out):
- `NFL_STAT_COLUMNS`: nflverse column → `nfl_player_seasons` column. The sums in `history_backfill.sources.CAREER_COLUMNS` (completions … `pr_yards`, names as in `season_stat_lines`), plus `def_tackles` (solo + assists), the four `kick_fgm_<r>` / `kick_fga_<r>` ranges (`FG_RANGES`), `kick_fg_long` (`fg_long`), `rec_targets` (`targets`), `kr_tds`, `pr_tds` (`special_teams_tds` split where nflverse splits it; else NULL), `rush_fumbles_lost` (`rushing_fumbles_lost`). Stats nflverse lacks (broken tackles, yards after contact, sacks allowed, downs played, punt long) are not columns.
- `parse_players(rows) -> list[dict]` (nfl_players shape; height "6-2" → 74).
- `parse_player_seasons(rows, season, season_type) -> list[dict]`; a row with no team code raises; zero stays 0, a blank stays NULL.
- `parse_games(rows, seasons) -> list[dict]`; `game_type` maps nflverse `REG/WC/DIV/CON/SB`.
- `team_seasons(games, standings_rows) -> list[dict]`: W-L-T and points from regular-season games; playoff result from the deepest playoff game; place and seed from `standings_rows` when given (`standings_source = 'NFLDATA'`), else `place_and_seed(games)` computes them (`'COMPUTED'`: division place by win percentage then head-to-head then net points, and seeds from the bracket: pre-2020 six seeds with two byes, 2020 on seven seeds with one bye).
- `coach_seasons(games) -> list[dict]`: one row per (coach, season, team); `full_season` when he coached all the team's regular-season games.
- `parse_draft(rows, seasons) -> list[dict]`.

### 2.4 Which seasons and why

1999–2025 for players, games, teams, coaches and awards; 1999–2026 for the draft. nflverse player stats start in 1999, and the oldest player or coach active in 2026 entered after that (S3; spike confirms no 2026 player has a 1998 rookie season). Defensive tackles and pass deflections in nflverse come from play-by-play and are thin before about 2001 (R6): they are loaded as listed, and the load report lists per-season coverage (players with any tackle) so a reader knows.

### 2.5 Awards (`awards.py`, `data/history/nfl_awards_1999_2025.csv`)

nflverse has no awards. `awards.py` builds the CSV once from Pro-Football-Reference, caching every page in `data/history/.cache/pfr_*.html` and waiting `PFR_DELAY_S = 4` seconds between requests (about 60 pages):
- Major awards: one PFR award page each for MVP, OPOY, DPOY, OROY, DROY, COTY (AP), plus Super Bowl MVP.
- `years/<season>/allpro.htm`: AP First and Second Team only (other selectors dropped).
- `years/<season>/probowl.htm`: Pro Bowl selections (named, not alternates).
- `parse_award_page(html)`, `parse_allpro(html, season)`, `parse_probowl(html, season)` are pure; they read tables inside HTML comments too (PFR hides some tables there).
- `NFL_AWARD_TYPES = ('MVP', 'OPOY', 'DPOY', 'OROY', 'DROY', 'COTY', 'SBMVP', 'First Team All-Pro', 'Second Team All-Pro', 'Pro Bowl')`, the same strings as `awards.award_type` where the league has the award.
- CSV columns: `season, award_type, pfr_id, player_name, coach_name, team_abbr, position`. Committed, so loads never need PFR and JR can read it. `load` maps `pfr_id` to `gsis_id` through `nfl_players.pfr_id`.
- If PFR refuses automated fetches (R3), the fallback is Wikipedia's award lists for the seven single winners and a hand-checked CSV for All-Pro; Pro Bowl is then dropped (Q6).

### 2.6 Linking real players to Madden players (`link.py`)

The hard part. Pure core, thin database wrapper.

- `normalize_name(s)`: lower case, accents stripped, punctuation removed, suffixes `jr sr ii iii iv v` dropped, spaces collapsed.
- `madden_side(cur, season, snapshot=None) -> list[MuPlayer]`: every `players` row with `madden_pgid`, its `player_seasons` row for `season` (position, team, age, `years_of_experience`), its `player_name_aliases`, and from the snapshot (when given) `year_drafted` and college (`pipeline/colleges.college_name`).
- `real_side(nfl_players, rosters_2026) -> list[RealPerson]`: `nfl_players` with `rookie_season <= 2026` and (`last_season >= 2023` or on `rosters_2026.csv` or `draft_year = 2026`), each with his real 2026 team from `rosters_2026.csv` by `espn_id`.
- `POSITION_GROUPS`: Madden positions → groups (QB; HB/FB → RB; WR; TE; LT/LG/C/RG/RT → OL; LE/RE/DT → DL; LOLB/MLB/ROLB → LB, with LE/RE/LOLB/ROLB also allowed to match nflverse OLB/DE; CB; FS/SS → S; K; P; LS).
- Evidence, each a weight in `WEIGHTS`: position group, entry season (`season - years_of_experience` vs `rookie_season`, ±1), draft year (snapshot), college, real 2026 team, jersey, height (±1 in), weight (±15 lb).
- `match(mu_players, people, overrides) -> MatchResult(links, ambiguous, fuzzy, unmatched)`, in order:
  1. `OVERRIDE`: a row of `data/history/nfl_link_overrides.csv` (`madden_pgid, full_name, position, gsis_id, note`; `gsis_id = none` means "no real person"). Refused, and reported, when the pgid's name and position don't match the row (protects against pgids differing between saves).
  2. `EXACT` / `ALIAS`: one person with the same normalized full name (or an alias), same position group, and entry season or 2026 team agreeing. When several share the name, the highest score wins if it leads the next by `MARGIN = 2.0` and its entry season agrees; otherwise `ambiguous`.
  3. `FUZZY`: no name match; one person with the same first initial and last name within edit distance 2 (or a first-name nickname from `NICKNAMES`), same position group, entry season and 2026 team. Linked and listed for review (Q5).
  4. Everything else is `unmatched`. A person is linked at most once; a second claim makes both ambiguous.
- `run_link(cur, season, snapshot=None, overrides_path=..., apply=False) -> MatchResult`: writes the report always; with `apply`, replaces every `nfl_player_links` row in one transaction.
- Report `verify_output/nfl_history/link_<db>.md` and `.csv`: counts per method; ambiguous players with each candidate and its evidence; fuzzy links; unmatched veterans (`years_of_experience >= 1`) sorted by OVR; unmatched rookies separately; real players active in 2025 with no link (left the league, or missed). JR fixes any of them with one override line and re-runs.
- Target (an estimate, checked in the spike): at least 98% of rostered veterans linked, and every player of OVR 80+ linked or overridden.

### 2.7 Coach baseline (`coach_baseline.py`)

- `BASELINE_SEASON = 2025`.
- `baseline_rows(coaches, nfl_coach_seasons) -> list[dict]`, pure. For each head coach with an open tenure (`coach_tenures.end_season IS NULL`): career W-L-T, playoff W-L and Super Bowl W-L summed over his `nfl_coach_seasons`; `winning_seasons` = seasons with `full_season` and W > L. A first-time head coach gets a zero row: a real career of zero, so `mu_era_only = false`. Names are matched exactly, with `COACH_NAME_FIX` for spelling differences between `coaches.coach_name` and nflverse.
- `apply_baseline(cur, rows)`: `INSERT ... ON CONFLICT (coach_id, season) DO UPDATE` for season 2025, `team_id` NULL. Refuses the whole run, naming the coaches, if any of them already has a `coach_seasons` row after 2025 (the archive already started him from zero; fix with `season_archive --replace` after the baseline). Also sets `nfl_coach_seasons.coach_id` and `nfl_team_seasons.head_coach_id` for the names it matched.

### 2.8 Writes, reads and idempotency (`store.py`, `read.py`)

`store.py`:
- `REFUSED_DBS = {"madden", "madden_2036"}`; `connect(db)` takes a DSN or database name, refuses those names, and returns a connection.
- `upsert_players(cur, rows)` (never deletes: `nfl_player_links` points at it).
- `replace_kind(cur, kind, first, last, rows)` for `stats`, `games`, `teams` (team and coach seasons), `awards`, `draft`: deletes that kind's rows for the season range and inserts the new ones, in the caller's transaction.
- `record_load(cur, kind, first, last, files, counts, report) -> int`.
- `write_links(cur, links)`; `write_coach_baseline(cur, rows)`.
- A rerun with the same cached files gives the same rows. `--refresh` downloads again; the load row records the new hashes.

`read.py` (what U6 and U7 call; each returns empty when `installed(cur)` is false):
- `installed(cur) -> bool` (`to_regclass('nfl_player_seasons')`).
- `player_nfl_career(cur, player_id) -> dict | None`: `{"gsis_id", "seasons": [REG rows, newest first, with team_abbr and mu team abbreviation], "postseason": [...], "totals": {stat: sum}, "postseason_totals": {...}, "awards": [{"season", "award_type"}], "draft": {"season", "round", "pick", "team"} | None}`.
- `nfl_career_before(cur, player_ids, stats) -> {player_id: {stat: total}}` (regular season).
- `team_nfl_seasons(cur, team_id) -> list[dict]` (newest first, with awards won by the team's players that season).
- `nfl_awards_by_season(cur) -> {season: [rows]}` and `nfl_player_awards(cur) -> list[(player_id, season, award_type)]` (linked players only).

### 2.9 CLI (`__main__.py`)

```
python3 -m engines.nfl_history fetch          [--seasons 1999-2025] [--refresh] [--awards]
python3 -m engines.nfl_history load           --db DB [--seasons ...] [--only players,stats,games,teams,awards,draft] [--apply]
python3 -m engines.nfl_history link           --db DB [--season 2026] [--snapshot PATH] [--apply]
python3 -m engines.nfl_history coach-baseline --db DB [--apply]
python3 -m engines.nfl_history init           --db DB [--snapshot PATH] [--apply]   # load, link, coach-baseline
python3 -m engines.nfl_history check          --db DB                               # record book cross-check, read-only
python3 -m engines.nfl_history status         --db DB                               # last load per kind, row counts, link counts
```

Without `--apply` every command prints what it would write and writes nothing (the link report is still written). `load` needs the cache to be filled (`fetch`), so it never touches the network unless `--fetch` is given. `link --season` defaults to `league_state.current_season`.

### 2.10 Record book once real history exists

Nothing changes in `engines/records_engine.py`: it keeps measuring each league season against `franchise_records` / `league_record_entries`, whose `NFL` rows are Pro-Football-Reference's full history back to 1920. `nfl_*` holds only 1999 on, so it can't be a record source. `check.py` (`record_check(cur) -> list[Mismatch]`) compares, for 1999–2025, every `NFL` record-book row in a category it can compute (season passing, rushing, receiving yards and TDs, receptions, sacks, interceptions, field goals made, team wins and points) with the value in `nfl_player_seasons` / `nfl_team_seasons`, and writes `verify_output/nfl_history/record_check_<db>.md`. Mismatches are expected to be few (sacks are official from 1982 and nflverse's play-by-play sacks can differ by a half); the record book stays the authority.

### 2.11 Readers

- **Player page** (`server/api.py` `/api/player/{id}`): adds `nfl_career` (from `read.player_nfl_career`) beside `career_stats`; `career_totals` is unchanged. `PlayerPage.tsx` shows a "Real NFL career (through 2025)" table with a total row, the draft line and real honors, under the league career and visibly separate.
- **Team History** (`/api/team/{abbr}/history`): adds `nfl_seasons` (1999–2025). `TeamHistory.tsx` lists them below the league seasons under a "Real NFL" heading; the league totals since 2026 stay league-only.
- **Awards page** (`server/history_api.py` `/api/awards`): adds `nfl_seasons` (winners 1999–2025, champion from `nfl_team_seasons`). `AwardsPage.tsx` shows them after the league seasons, marked "Real NFL".
- **Writer tools** (`newsroom/db_tools.py`): `get_player_career` adds a `real_nfl_career` block (seasons, totals, playoffs, awards, draft) and a `career_including_real_nfl` total, labeled; `get_team_history` adds the real seasons. The tool note says: real NFL seasons through 2025 are fact; league seasons from 2026 are this league; quote the combined total only as "career". `newsroom/swarm_dispatcher.py`'s tool line changes from "every season since 2026" to say the real career is included.
- **Career milestones** (`engines/career_milestones.py`): `career_before` adds `read.nfl_career_before` for each player (Q3). The output's `since` becomes the player's real first season where he has one.
- **Popularity** (`engines/player_popularity_engine.py`): Awards Equity also counts `read.nfl_player_awards` (Q4). `AWARD_POINTS` has no Pro Bowl entry, so Pro Bowls score 0.

### 2.12 Run order (after the build is merged)

1. `fetch --awards` once on JR's PC (network). 2. On `madden_test`: apply the migration, `init --db madden_test` dry run, then `--apply`, `check`, `status`. 3. JR reads `link_madden_test.md`, adds overrides. 4. On the franchise database after its first save load and before the first season archive: migration (already in `init_db.sql` for new databases), `init --db madden_<slug> --snapshot <snapshot.json> --apply`.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | nflverse files or columns differ from what §2.3 assumes (e.g. no `stats_player_post_*` for early seasons, `games.csv` coach columns missing before some year). | Spike (a). | Adjust `NFL_STAT_COLUMNS` / URLs before wave 1; a missing early file narrows that kind's seasons, reported. |
| R2 | `nfldata/standings.csv` missing or without `div_rank` / `seed`. | Spike (a). | `place_and_seed` computes them (`COMPUTED`); slightly less exact tiebreaks before 2002. |
| R3 | Pro-Football-Reference blocks automated fetches or its terms forbid it. | Spike (b): one award page and one All-Pro page with the delay. | Wikipedia for the seven single winners, hand-checked All-Pro CSV, drop Pro Bowl (Q6). |
| R4 | Match rate lower than the 98% estimate (Madden spellings, nicknames, players nflverse lists under another name). | Spike (c): run the matcher logic by hand on `madden_test` players or a CAREER-WEEK1 snapshot with `rosters_2026.csv`. | More `NICKNAMES` / `COACH_NAME_FIX` entries; JR's override file covers the rest. |
| R5 | `madden_pgid` differs between franchises started from the same roster, so overrides don't carry over. | Compare pgids of two scratch saves (CAREER-WEEK1 vs CAREER-STAFF) in the spike. | Overrides are checked by name and position; a mismatch is refused and reported, not misapplied. |
| R6 | Thin defensive stats before ~2001 (play-by-play gaps). | Load report's per-season coverage. | Documented in the report and the writer note; nothing derived from them. |
| R7 | A player traded midseason has one season row with his last team (nflverse season files). | Known. | Accepted; per-team splits from weekly files are a follow-up. |
| R8 | The coach baseline runs after the first season archive (the archive would have started coaches from zero). | `apply_baseline` refuses and names them. | Run `season_archive --replace` for 2026 after the baseline. |
| R9 | Shared files with other plans (`db/init_db.sql`, `server/api.py`, `newsroom/db_tools.py`, `newsroom/swarm_dispatcher.py`) change underneath. | `/execute-plan` rebases each unit on current `main`. | Normal merge; conflicts go to JR. |
| R10 | Writers add real and league stats together wrongly or present real stats as league stats. | Tool output labels both blocks and gives only one explicit combined total. | Tighten the tool note. |
| R11 | nflverse revises past files. | Hashes in `nfl_history_loads.files`. | A `--refresh` reload replaces the kind's rows; links stay (keyed on `gsis_id`). |

**Spike first** (orchestrator, before wave 1, about 45 minutes, network, read-only): (a) download `players.csv`, `stats_player_reg_1999.csv`, `stats_player_reg_2025.csv`, `stats_player_post_1999.csv`, `games.csv`, `draft_picks.csv`, `standings.csv` into the cache and list their columns; confirm coach columns in 1999 games and the 2026 draft rows; confirm no player on `rosters_2026.csv` has a rookie season before 1999. (b) Fetch PFR's AP MVP page and `years/2024/allpro.htm` with a 4-second gap and check the tables parse. (c) Count name-plus-position matches between `madden_test` players (or a CAREER-WEEK1 snapshot) and `players.csv`, and compare pgids across two scratch saves. Write findings into §0 of this plan; (a) and (b) can change §2.3 and §2.5.

## 4. Tests (synthetic, no live DB)

- `tests/test_nfl_history_store.py` → `connect` refuses `madden` and `madden_2036`; `replace_kind` deletes only its kind and season range (fake cursor records SQL); `upsert_players` never deletes; `record_load` stores hashes and counts; `read.installed` false → every read returns empty; `player_nfl_career` shapes rows (fake cursor); the migration file and `init_db.sql` both define every `nfl_*` table and the view (text check).
- `tests/test_nfl_history_nflverse.py` → `mu_abbr` for OAK/LV, SD/LAC, STL/LA/LAR, ARI, WSH, HOU before 2002 raising; `parse_player_seasons` column mapping, blank vs 0, `def_tackles`, FG ranges; `parse_games` game types; `team_seasons` W-L-T with a tie, points, each playoff result, NFLDATA vs COMPUTED place and seed (six-seed and seven-seed brackets); `coach_seasons` with an interim coach and `full_season`; `parse_draft`; `fetch.get` uses the cache, writes atomically and returns the sha256 (network monkeypatched).
- `tests/test_nfl_history_awards.py` → parsing a synthetic award page, an All-Pro page with the table inside an HTML comment (AP rows only), a Pro Bowl page; CSV round trip; `NFL_AWARD_TYPES` strings equal the league's award types where both exist.
- `tests/test_nfl_history_link.py` → exact unique match; alias match; suffix and accents; two same-name players separated by entry season; a tie reported ambiguous; fuzzy one-letter slip linked and listed; one person claimed twice → both ambiguous; override applied, override `none`, override refused on a name mismatch; unmatched veteran and rookie lists; deterministic output on rerun.
- `tests/test_nfl_history_coach_baseline.py` → career sums, winning seasons only for full seasons, Super Bowl counts, first-time coach zero row, `COACH_NAME_FIX`, refusal when a later snapshot exists.
- `tests/test_nfl_history_cli.py` → `--help` for each command; `--db` required; dry run writes nothing (fake connection); `init` calls load, link, baseline in order; `check` mismatch report from synthetic rows.
- `tests/test_nfl_history_api.py` → player endpoint includes `nfl_career` and leaves `career_totals` unchanged; team history and awards include `nfl_seasons`; all three behave when the tables are missing (read functions monkeypatched).
- `tests/test_career_milestones.py` (extended) → real career counts toward `career_before`; a player with no link unchanged; `since` uses the real first season.
- `tests/test_nfl_history_writer_tools.py` → `get_player_career` / `get_team_history` output has the labeled real block and the combined total (reads monkeypatched).
- `tests/test_player_and_playoff_metrics.py` (extended) → `awards_equity` with real awards from the last 3 seasons; Pro Bowl scores 0.

Run with `pytest -m "not integration" tests/test_nfl_history_*.py tests/test_career_milestones.py tests/test_player_and_playoff_metrics.py`. One integration test in `tests/test_nfl_history_store.py` (marked `integration`) applies the migration to `madden_test` and loads a three-row synthetic set twice, checking the second load changes nothing.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Schema, store and reads

- **Owns:** `db/migrations/2027_02_04_nfl_history.sql` (new), `db/init_db.sql`, `engines/nfl_history/store.py` (new), `engines/nfl_history/read.py` (new), `tests/test_nfl_history_store.py` (new)
- **Reads:** this plan §2.2, §2.8; `db/migrations/2027_01_05_madden_pgid.sql`, `2026_12_31_awards_source.sql` (header style); `db/init_db.sql:1800-1890`; `tests/conftest.py`
- **Depends on:** none
- **Done when:** the migration applies twice on a scratch database without error; `init_db.sql` defines the same objects; store and read functions match §2.8.
- **Verify:** `pytest -m "not integration" tests/test_nfl_history_store.py` passes.

### U2: nflverse sources and derivations

- **Owns:** `engines/nfl_history/__init__.py` (new), `engines/nfl_history/fetch.py` (new), `engines/nfl_history/teams.py` (new), `engines/nfl_history/nflverse.py` (new), `tests/test_nfl_history_nflverse.py` (new)
- **Reads:** this plan §2.3–§2.4 and the spike's findings in §0; `engines/history_backfill/sources.py` (`_get`, `CAREER_COLUMNS`, `FG_RANGES`); `engines/tuning/targets.py` (nflverse parsing); `db/seed_clean_db.py` `TEAMS_DATA`
- **Depends on:** none
- **Done when:** the pure functions turn synthetic nflverse rows into rows in exactly the §2.2 column shapes; `fetch_all` caches.
- **Verify:** `pytest -m "not integration" tests/test_nfl_history_nflverse.py` passes.

### U3: Awards

- **Owns:** `engines/nfl_history/awards.py` (new), `data/history/nfl_awards_1999_2025.csv` (new), `tests/test_nfl_history_awards.py` (new)
- **Reads:** this plan §2.5; the spike's PFR findings
- **Depends on:** none
- **Done when:** parsers pass on synthetic HTML; the CSV is built from cached PFR pages (if the worktree has no network, the agent leaves the CSV with its header and reports that the orchestrator must run `python3 -m engines.nfl_history.awards build`); the CSV has one MVP per season 1999–2025.
- **Verify:** `pytest -m "not integration" tests/test_nfl_history_awards.py` passes; `python3 -m engines.nfl_history.awards --help` prints usage.

### U4: Player linking

- **Owns:** `engines/nfl_history/link.py` (new), `data/history/nfl_link_overrides.csv` (new, header only), `tests/test_nfl_history_link.py` (new)
- **Reads:** this plan §2.6; `pipeline/colleges.py`; `pipeline/save_reader/pc_save_reader.mjs:295-340`; `db/migrations/2027_01_07_player_name_aliases.sql`; `data/history/rosters_2026.csv`; `engines/nfl_history/store.py`, `nflverse.py`
- **Depends on:** U1, U2
- **Done when:** `match` follows the §2.6 order on synthetic players; `run_link` writes the report and, with `apply`, the links.
- **Verify:** `pytest -m "not integration" tests/test_nfl_history_link.py` passes.

### U5: CLI, coach baseline and record check

- **Owns:** `engines/nfl_history/__main__.py` (new), `engines/nfl_history/coach_baseline.py` (new), `engines/nfl_history/check.py` (new), `tests/test_nfl_history_cli.py` (new), `tests/test_nfl_history_coach_baseline.py` (new)
- **Reads:** this plan §2.7, §2.9, §2.10; `engines/season_archive.py:300-420`; `engines/coach_profile.py:340-440`; `engines/records_engine.py:60-130`; `engines/nfl_history/store.py`, `nflverse.py`, `awards.py`
- **Depends on:** U1, U2, U3 (calls `link.run_link` through the §2.6 signature; U4 builds it in the same wave)
- **Done when:** every command in §2.9 parses, dry runs write nothing, `init` runs the three steps in order, the baseline follows §2.7.
- **Verify:** `pytest -m "not integration" tests/test_nfl_history_cli.py tests/test_nfl_history_coach_baseline.py` passes; `python3 -m engines.nfl_history --help` lists the seven commands.

### U6: Site

- **Owns:** `server/api.py`, `server/history_api.py`, `Mu_Network/src/components/league/PlayerPage.tsx`, `Mu_Network/src/components/team/TeamHistory.tsx`, `Mu_Network/src/components/history/AwardsPage.tsx`, `tests/test_nfl_history_api.py` (new)
- **Reads:** this plan §2.8 (`read.py`), §2.11; `server/api.py:342-420`, `:811-1230`, `:1389-1405`; `server/history_api.py:518-600`
- **Depends on:** U1
- **Done when:** the three endpoints return the new keys, the pages show the real blocks separate from league data, and nothing changes when the tables are missing.
- **Verify:** `pytest -m "not integration" tests/test_nfl_history_api.py tests/test_history_api.py` passes; `npm run build` in `Mu_Network/` succeeds.

### U7: Writers, milestones and popularity

- **Owns:** `newsroom/db_tools.py`, `newsroom/swarm_dispatcher.py`, `engines/career_milestones.py`, `engines/player_popularity_engine.py`, `tests/test_career_milestones.py`, `tests/test_player_and_playoff_metrics.py`, `tests/test_nfl_history_writer_tools.py` (new)
- **Reads:** this plan §2.8, §2.11; `newsroom/db_tools.py:586-780`, `:888-1060`; `newsroom/swarm_dispatcher.py:1099`
- **Depends on:** U1
- **Done when:** the writer tools show the labeled real block, milestones count the real career, Awards Equity counts real awards, and each behaves as before when the tables are missing.
- **Verify:** `pytest -m "not integration" tests/test_career_milestones.py tests/test_player_and_playoff_metrics.py tests/test_nfl_history_writer_tools.py tests/test_player_career_ratings.py` passes.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2, U3 | — (spike done) | Schema, parsers and awards share no files and build against §2.2–§2.5 |
| 2 | U4, U5, U6 | U1, U2, U3 | Linking, CLI and site use the schema and parsers; U5 calls U4 only through the fixed `run_link` signature |
| 3 | U7 | U1 | Readers in newsroom and engines; a separate wave only to keep three units per wave |

After wave 3: the orchestrator runs §2.12 steps 1–2 on `madden_test` and brings JR the link report. Franchise databases follow once JR is happy with it.

**File ownership check:**

| File | Unit |
|---|---|
| `Mu_Network/src/components/history/AwardsPage.tsx` | U6 |
| `Mu_Network/src/components/league/PlayerPage.tsx` | U6 |
| `Mu_Network/src/components/team/TeamHistory.tsx` | U6 |
| `data/history/nfl_awards_1999_2025.csv` | U3 |
| `data/history/nfl_link_overrides.csv` | U4 |
| `db/init_db.sql` | U1 |
| `db/migrations/2027_02_04_nfl_history.sql` | U1 |
| `engines/career_milestones.py` | U7 |
| `engines/nfl_history/__init__.py` | U2 |
| `engines/nfl_history/__main__.py` | U5 |
| `engines/nfl_history/awards.py` | U3 |
| `engines/nfl_history/check.py` | U5 |
| `engines/nfl_history/coach_baseline.py` | U5 |
| `engines/nfl_history/fetch.py` | U2 |
| `engines/nfl_history/link.py` | U4 |
| `engines/nfl_history/nflverse.py` | U2 |
| `engines/nfl_history/read.py` | U1 |
| `engines/nfl_history/store.py` | U1 |
| `engines/nfl_history/teams.py` | U2 |
| `engines/player_popularity_engine.py` | U7 |
| `newsroom/db_tools.py` | U7 |
| `newsroom/swarm_dispatcher.py` | U7 |
| `server/api.py` | U6 |
| `server/history_api.py` | U6 |
| `tests/test_career_milestones.py` | U7 |
| `tests/test_nfl_history_api.py` | U6 |
| `tests/test_nfl_history_awards.py` | U3 |
| `tests/test_nfl_history_cli.py` | U5 |
| `tests/test_nfl_history_coach_baseline.py` | U5 |
| `tests/test_nfl_history_link.py` | U4 |
| `tests/test_nfl_history_nflverse.py` | U2 |
| `tests/test_nfl_history_store.py` | U1 |
| `tests/test_nfl_history_writer_tools.py` | U7 |
| `tests/test_player_and_playoff_metrics.py` | U7 |

No path appears twice.

## 7. Spec changes when built

- §1 "Current direction": a fresh franchise also loads real NFL history 1999–2025 into `nfl_*` once after its first save load (`engines.nfl_history init`).
- §1.2 Running the System: the `engines.nfl_history` commands and the §2.12 run order; fix the stale `nfl_careers_through_2025.csv` description (S19).
- §2.9 Record Book: one paragraph — `nfl_*` is never a record source; `check` cross-checks the `NFL` rows.
- §3.3 / new §3.3x "Real NFL history (1999–2025)": every `nfl_*` table, the view, `nfl_history_loads`, the linking rules and report, sources and caching.
- §3.3h: a fresh database's 2025 `coach_seasons` baseline comes from `coach-baseline`; it must run before the first archive.
- §4.2 / §4.3 / §4.7: `nfl_seasons` on `/api/team/{abbr}/history` and `/api/awards`; `nfl_career` on `/api/player/{id}`.
- §5.2: Player page, Team History and Awards page real-NFL blocks.
- §2.5 popularity: Awards Equity counts real awards. Career milestones line (§3.3l or wherever milestones live): career includes the real career.
- §7 writer tools: `get_player_career`, `get_team_history` real blocks and the labeling rule.
- `PERSONALITY_PLAN.md` §5.3 migration reservations: `2027_02_04_nfl_history.sql` (this plan).
- Repository Layout: `engines/nfl_history/` (10 files), `data/history/nfl_awards_1999_2025.csv`, `data/history/nfl_link_overrides.csv`, `db/migrations/2027_02_04_nfl_history.sql`, the new test files, `verify_output/nfl_history/`, and this plan's line under `docs/not_started/` (later `in_progress/`, `done/`).

## 8. Decisions for JR

Made as recommended so the plan could be written; each can change.

| # | Question | Recommendation |
|---|---|---|
| Q1 | Put real history in its own `nfl_*` tables, or as `source = 'NFL'` rows in the league's tables (`season_stats`, `player_seasons`, `awards`, `team_season_results`, ...)? | **Own tables.** The league tables need a `players` row per retired real player and a `coaches` row per past coach, and over 40 reader files would start counting real seasons as league seasons (S4–S8). Own tables change nothing until a reader opts in. |
| Q2 | Load every real player-season, or only players who are in the save? | **Every one** (about 55,000 rows, an estimate). Real seasons are then complete (league leaders, "passed Peyton Manning"), and only links depend on matching. |
| Q3 | Should a player's "career" in milestones and writer tools be real career + league career? | **Yes, labeled.** The league continues the real NFL, so 40,000 career yards should count his real yards. The player page keeps the two tables separate, with the league total unchanged. |
| Q4 | Should real awards count in popularity's Awards Equity? | **Yes.** Otherwise in 2026 every player starts with zero awards. Only 2023–2025 weigh much (3-season decay + 25% of career points); Pro Bowls score 0. |
| Q5 | Link fuzzy matches automatically (listed for review) or hold them until you approve? | **Link and list.** Fuzzy needs position, entry season and 2026 team all agreeing; you reject a bad one with one override line. |
| Q6 | Include Pro Bowls? | **Yes**, stored and shown on the player page; no popularity points. Dropped if Pro-Football-Reference can't be fetched. |
| Q7 | Run `init` automatically from the save watcher on a new franchise database, or by hand? | **By hand for now**, once per franchise after the first save load. A watcher step can come later; it would touch `pipeline/watch_save.py`, which other plans are changing. |
| Q8 | Migration number. | **`2027_02_04_nfl_history.sql`**, the next free number (the coaching-trees plan needs none). Recheck at build. |
| Q9 | Load playoff player lines too? | **Yes** (`season_type = 'POST'`). Writers can cite a player's real playoff career; the record book's career playoff rows stay the authority. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | build/nfh-u1 | .claude/worktrees/nfh-u1 | merged | 6a91b6c |
| U2 | 1 | build/nfh-u2 | .claude/worktrees/nfh-u2 | merged | 0369a41 |
| U3 | 1 | build/nfh-u3 | .claude/worktrees/nfh-u3 | merged | 6cb2a64 |
| U4 | 2 | build/nfh-u4 | .claude/worktrees/nfh-u4 | merged | 8c14fb9 |
| U5 | 2 | build/nfh-u5 | .claude/worktrees/nfh-u5 | merged | a1e77f3 |
| U6 | 2 | build/nfh-u6 | .claude/worktrees/nfh-u6 | merged | 2ccf7b2 |
| U7 | 3 | build/nfh-u7 | .claude/worktrees/nfh-u7 | merged | 0529e73 |
