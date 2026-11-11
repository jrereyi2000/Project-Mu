# Project Mu: Madden Stats Ingest Plan

**Status:** built and on `main` (2026-11-11). Approved by JR (2026-11-10, opus-queue chat; board M-0375/M-0376; go-ahead confirmed in `stats-ingest-2`, 2026-11-11). Spike run 2026-11-10 — results in S19–S22. Build started 2026-11-11 from the `stats-ingest` handoff (`docs/in_progress/MADDEN_STATS_INGEST_HANDOFF.md`).
**As built:** the post-merge end-to-end load of `CAREER-TUNE-P2G` (week 0 of franchise year 1) writes 2,484 `player_career_counters` rows but no season stat rows: at a week-0 save every `season_stats` row still carries `SEAS_YEAR` 0 (the previous season) and `load_season_lines` keeps only `SEAS_YEAR == franchise_year`, so `season_stat_lines` for the new season is empty. That is existing loader behaviour, not a regression. The six new season columns were confirmed non-zero with a second snapshot of `CAREER-TUNE-P2H` (2026, week 3): of the 3,384 `season_line_rows`, 90 rows have non-zero drops, 45 non-zero 20+ runs, 676 non-zero big hits, 434 non-zero catches allowed, 224 non-zero pancakes and 857 non-zero games started (their totals are 110, 76, 3,361, 1,727, 4,960 and 2,277 respectively). `player_season_counters` is empty after one load by design — every week's status is `BASELINE` until a second load exists to difference against.
**Builds on:** PROJECT_SPEC §3.3 (Games & Statistics), [`PC_MIGRATION_PLAN.md`](../done/PC_MIGRATION_PLAN.md) §5.1–§5.3 (the save reader and loader), [`MADDEN_STATS_AND_ADVANCED_METRICS.md`](../reference/MADDEN_STATS_AND_ADVANCED_METRICS.md) (the metric catalog), board M-0288 (JR's ask).

**Goal:** Store every stat Madden 27 actually records, so the advanced metrics in the reference doc can be tracked week by week and computed whenever they're needed. Two sources are involved. The per-game and season stat rows hold a handful of counters the loader drops today; those become new columns. Targets, pressures, coverage targets, stops, depth and pocket splits exist **only** as career totals on `Player.CareerStats`. The plan stores those totals at every weekly load and gets each week's numbers by subtracting the previous week's totals. Ratios are not stored; they're computed from the counters on demand. The plan also says plainly which metrics in the doc can't be computed and why.

**Delivers:**
- Save reader `READER_VERSION` + 1 (9 today; 10 if builder-4's drafted-cohort batch lands first, S18): each snapshot player gets `career_stats` = `{table, stats}` (`pipeline/save_reader/pc_save_reader.mjs`).
- Migration `db/migrations/2027_02_25_madden_stat_counters.sql`, mirrored in `db/init_db.sql`:
  - new columns `rec_drops`, `rush_20plus` (`player_stats`, `season_stats`); `big_hits`, `catches_allowed` (`game_stat_defense`, `season_stat_defense`); `pancakes` (`game_stat_blocking`, `season_stat_blocking`); `games_started` (`game_stat_general`, `season_stat_general`), all appended to `season_stat_lines` / `game_stat_lines`
  - table `player_career_counters`, function `counter_delta(jsonb, jsonb)`, views `player_week_counters` and `player_season_counters`
- `pipeline/load_save.py`: the new columns in the column maps, plus `load_career_counters()`.
- `engines/advanced_metrics.py`: every metric in the doc as a named formula over the stored counters, with `metric_status()` (computable / impossible + reason), `player_metrics(cur, season, player_id, week=None)` and a `python3 -m engines.advanced_metrics` CLI.
- Corrections to `docs/reference/MADDEN_STATS_AND_ADVANCED_METRICS.md` (§2.3, §3, §4, §5).

---

## 0. Survey (code facts that set the scope)

Spike results come from read-only reads of **copies** of `CAREER-MU2026` and `CAREER-TUNE-P2G` (2026-11-10). These are the same save one simulated season apart. Scripts: `career_spike.mjs` and `career_dump.mjs` in the session scratchpad. They're not part of the repo, and U1 replaces them.

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | The reader already copies **every** plain field on each Game/Season stat row: `plainFields(s, names, STAT_REF_FIELDS)` (`pipeline/save_reader/pc_save_reader.mjs:360`). | Nothing on those rows needs reader work; the loader just has to map more fields. |
| S2 | `offense_cols` / `defense_cols` / the blocking lambda (`pipeline/load_save.py:164-263`) map only the box-score fields. `RECEIVEDROPS`, `RUSH20YARDRUNS`, `BIGHITS`, `CTHALLOWED`, `OLINEPANCAKES` and `GAMESSTARTED` are dropped. `group_lines` keeps only `DOWNSPLAYED` and `GAMESPLAYED` (`:288`). | One migration plus column-map entries. |
| S3 | The full-season capture `captures/2026-11-10_174110/save.json` (CAREER-TUNE-P2G, 28,830 game rows): the dropped game-row fields are populated (`BIGHITS` 8,831 non-zero defensive rows, `CTHALLOWED` 5,319, `OLINEPANCAKES` 3,040, `RECEIVEDROPS` 527, `RUSH20YARDRUNS` 331, `GAMESSTARTED` on every table). `GAMERATING` is zero on every row of every table. | Store the six populated fields per game and per season; skip `GAMERATING`. |
| S4 | `FIRSTDOWNS` and `4QCOMEBACKS` exist on the **Season** rows only, not the Game rows (M27 schema `GameOffensiveStats`). They're zero in every season row of `captures/2026-11-10_180457/save.json`. `GAMEWINFGATTEMPTS` / `GAMEWINFGSMADE` are zero too. | Not stored. The doc's §2.2 "situational counters" aren't populated by the sim. |
| S5 | M27 schema `M27_620_0` (`pipeline/save_reader/node_modules/madden-franchise/data/schemas/27/`): `GameOffensiveStats` adds 21 fields and `GameDefensiveStats` 18; neither has `TARGETS`, `ROUTESRUN`, `AIRYARDS`, `PRESSURES`, `DEFTARGETS`, `STOPS`, `STUFFS` or any split. Those exist only on `CareerOffensiveStats` (86 fields) / `CareerDefensiveStats` (58) / `CareerOLineStats` / `CareerKickingStats` and their `*KPReturn` subclasses. `Player.CareerStats` is a **single** reference (type `CareerStats`), not an array. | The doc's §3 is wrong to put these in `Game*Stats`. Per-game targets can't be read directly from the save. |
| S6 | Nothing in `pipeline/` reads `Player.CareerStats`. `readPlayers` follows only `SeasonStats` and `GameStats` (`pc_save_reader.mjs:419-420`). | The reader needs one new field per player. |
| S7 | Live save: 2,594 of 3,261 players have a CareerStats row (CAREER-TUNE-P2G). For the box counters on both rows, career(P2G) − career(MU2026) equals the season row: 40/40 QBs (`PASSATTEMPTS`), 349/350 receivers (`RECEIVECATCHES`), 817/817 defenders (`DEFTACKLES`). | Subtracting career totals between saves is exact. Week-to-week differences give per-week numbers. |
| S8 | Career rows start from real-NFL history (Josh Allen: 33,977 career pass yards), but the tracking fields start near zero. Allen's depth splits sum to 539 attempts out of 4,576 career attempts. `DOWNSPLAYED` was 0 for every player in MU2026. | Absolute career values of the tracking fields mean nothing; only differences do. |
| S9 | Over the P2G season these grew: `TARGETS` / `ROUTESRUN` (346 players), `DEFTARGETS` (843), `STOPS` (817), `CTHALLOWED` (558), `DEFPRESSURES` (275), `STUFFS`, `RUSHSUCCESS` (157), `LOADEDBOX` / `LIGHTBOX` (113), and the QB-only fields `AIRYARDS`, `PRESSURES`, `SEPARATION_*`, `THROWLOCATION_*`, `INSIDE/OUTSIDEPOCKETTHROW_*` (all 42 QBs, nobody else). **Corrected by S19:** `SHORT/MEDIUM/DEEPPASS_*`, `BLITZ_*` and `QBSCRAMBLE_*` do **not** grow. | These are computable. Air yards and separation are **passer** stats, so QB aDOT can be computed but receiver aDOT, RACR and a receiver separation index can't. |
| S10 | These never grew for anyone over the season: `FIRSTDOWNS`, `4QCOMEBACKS`, `GAMERATING`, `INSIDERUN_*` / `OUTSIDERUN_*`, every `COVERAGEDEPTH_*`, every `DEFSHORT/MEDIUM/DEEPPASS_*`, every `DEFINSIDE/OUTSIDERUN_*`, `DEFBLITZ_*`, `GAMEWINFG*`. | These metrics stay impossible on CPU-simmed games (§2.6). They're stored anyway, since they cost nothing and show up if Madden ever fills them. |
| S11 | `ROUTESRUN` grows at a median 0.73 per target (146 players with 40+ targets), about 105 a season for a WR1, while `DOWNSPLAYED` grows about 895. Real WR1s run about 550 routes a season. | `ROUTESRUN` isn't routes run. TPRR, YPRR and route participation are blocked until the spike says what it counts (R2). |
| S12 | 38 players' CareerStats row moved table over the season. Malik Nabers moved from `CareerOffensiveStats` to `CareerOffensiveKPReturnStats` when he took on returns: box counters carried over (catches 127 → 235), `TARGETS` / `ROUTESRUN` / `GAMESPLAYED` reset to 0. | A table change makes that week's difference unknown, not negative. |
| S13 | Some counters shrink legitimately with the table unchanged: `RECEIVEYARDS` (22 players), `RUSHYARDS` (2), `DLINEHALFSACK` (71; a half sack later credited in full). | A shrinking counter alone isn't a reset, so the only reset signals are a table change or `GAMESPLAYED` going down. |
| S14 | Snapshot meta: `schema_expected {major: 988}`, `schema_used {major: 620}` (`captures/2026-11-10_180457/save.json`). The library reads a newer save through an older schema. | Field positions could be off. S7's exact match on the box counters suggests they aren't, but that's not proven for the tracking fields (R1). |
| S15 | `load_snapshot` runs on every weekly ingest via `pipeline/watch_save.py:1355`. `current_week(snapshot)` (`load_save.py:1173`) gives the games.week of the save, or None outside the regular season and playoffs. P2-10 (board M-0300) is changing tuning seasons to load one week at a time too. | `load_career_counters` slots into `load_snapshot`; every league, live and tuning, gets one row per week. `watch_save.py` and `weekly_capture.py` need no change. |
| S16 | `season_stat_lines` was last redefined by `db/migrations/2026_12_30_history_player_rows.sql:25` and mirrored at `db/init_db.sql:2684`; `game_stat_lines` at `init_db.sql:2718`. Readers include `newsroom/db_tools.py`, `engines/records_engine.py`, `engines/progression_inputs.py`, `engines/tuning/compare.py` and more. | `CREATE OR REPLACE VIEW` can only append columns, so the new columns go at the end of each view and existing readers are unaffected. |
| S17 | Latest migration is `2027_02_23_player_stats_team_key.sql`. `builder-4` has taken `2027_02_24` for `2027_02_24_player_draft_facts.sql` (board M-0323). | Migration name `2027_02_25_madden_stat_counters.sql`. U2 re-checks it's free at build time. |
| S18 | `builder-4` (M-0323, 2026-11-10) claims `pipeline/save_reader/pc_save_reader.mjs`, `pipeline/load_save.py`, `db/init_db.sql` and `tests/test_load_save.py` for the drafted-cohort batch (`PLYR_DRAFTROUND/PICK/TEAM` → `load_save`). | Wave 1 starts only after that claim's `RELEASE`, and U1 / U2 build on its merged code. The reader version is main's value + 1 at build time, and the loader gates on a constant `CAREER_COUNTERS_READER_VERSION`, not a literal. **Done:** the batch is on `main` (`5e49aa5`), claim released; `READER_VERSION` is **9** on main on 2026-11-10, so U1 writes **10**. |
| S19 | **Spike (2026-11-10, all 90 matched QBs, CAREER-MU2026 vs CAREER-TUNE-P2G).** `SHORTPASS_ATTEMPTS`, `MEDIUMPASS_ATTEMPTS` and `DEEPPASS_ATTEMPTS` did **not** grow for any QB over the season (0 of the 40 QBs with pass-attempt growth; totals static at 9,931 / 3,344 / 1,838). `BLITZ_PLAYS` and `QBSCRAMBLE_CARRIES` are static too (0 of 90 grew). They hold real-NFL legacy totals, like the S10 fields. | Doc §4.2.3 Deep rate, §4.2.4 depth-split comp % / YPA / passer rating, §4.2.6 blitz passer rating and INT rate, and §4.2.7 scramble rate are **IMPOSSIBLE**, not OK. §2.5 updated. The typed view columns stay (they cost nothing and fill if Madden ever starts writing them). |
| S20 | **Spike.** `INSIDE+OUTSIDEPOCKETTHROW_ATTEMPTS`, `THROWLOCATION_LEFT+MIDDLE+RIGHT` and `SEPARATION_TIGHT+NORMAL+OPEN+WIDEOPEN` are three partitions of the **same** tracked-dropback count: the three sums are equal for 40/40 QBs with pass-attempt growth. `AIRYARDS` and `PRESSURES` grew for 42 QBs. Median pocket sum / `PASSATTEMPTS` growth = 0.986; 13 QBs exceed 1.0, and sacks explain most of the excess (`Herbert` 565 vs 553+29) — `Drew Lock` (117 vs 26) is an unexplained outlier. | Pocket and throw-location splits are computable. Ratio the counters against themselves, never assume they equal `PASSATTEMPTS`. |
| S21 | **Spike.** `SEPARATION_TIGHT == SEPARATION_NORMAL == SEPARATION_OPEN` exactly for 41 of the 42 QBs with separation data (all four buckets equal for the 42nd), while `WIDEOPEN` differs. The per-bucket split is not a trustworthy distribution. | No per-bucket separation metric (receiver separation is already IMPOSSIBLE, S9); keep the raw sum as an input only. |
| S22 | **Spike, R2.** `ROUTESRUN` grows a median **0.72 per target** (WR 0.723, TE 0.660, HB 0.607, FB 0.500) and about 5% of `DOWNSPLAYED`; the top WR accumulates 110–130 a season against ~1,100 downs, where a real WR1 runs ~550 routes. | TPRR / YPRR / route participation stay **BLOCKED**. R2 is answered: `ROUTESRUN` is not routes run. |

## 1. Scope

**In:**
- Six dropped Game/Season counters as columns, per game and per season (S2, S3).
- The reader reads `Player.CareerStats` (S5, S6).
- One `player_career_counters` row per player per weekly load, storing every Career field raw (S7).
- SQL views that turn those rows into per-week and per-season differences, with a status flag where the difference is unknown (S12, S13).
- A metrics module that computes every doc metric that can be computed, and names every one that can't with its reason.
- Fixing the reference doc's errors (S4, S5, S9–S11; the §5 loader claim).

**Out (non-goals):**
- Using the metrics anywhere: Napper projections, Agent Wire / newsroom prompts, the Front Office, the Excitement Index (doc §5). Each is a follow-up plan once a season of data exists.
- Frontend pages or API endpoints for the metrics.
- Backfilling earlier seasons. The career totals in old snapshots weren't captured, and old saves aren't kept week by week.
- Splitting a multi-game difference across games. A difference that covers 2+ games stays at week level.
- Play-by-play or tracking data Madden doesn't save.
- Fixing the library's schema fallback (S14). That's a madden-franchise issue; R1 only checks whether it affects us.

## 2. Design

### 2.1 Reader: `career_stats` (U1)

`READER_VERSION` goes up by one from main's value at build time (`pc_save_reader.mjs:65`; **10**, since main is 9 after the drafted-cohort batch, S18). In `readPlayers` add, after `game_stats`:

```js
career_stats: await readCareerRow(tables, p.CareerStats),   // {table: 'CareerOffensiveStats', stats: {TARGETS: 136, ...}} or null
```

`readCareerRow(tables, bits)`: `decodeRef(bits)` → the row in that table → `{ table: t.name, stats: plainFields(row, fieldNames(row)) }`. If the reference is empty, the row is empty or it throws, return `null` (same guard as `readStatRows` `:361`). The header comment gets a paragraph for the new reader_version. Snapshot size grows by about 2,600 objects of up to 94 ints (estimate: +3–4 MB on a 14 MB file).

### 2.2 Migration `2027_02_25_madden_stat_counters.sql` (U2)

```sql
ALTER TABLE player_stats         ADD COLUMN IF NOT EXISTS rec_drops INT, ADD COLUMN IF NOT EXISTS rush_20plus INT;
ALTER TABLE season_stats         ADD COLUMN IF NOT EXISTS rec_drops INT, ADD COLUMN IF NOT EXISTS rush_20plus INT;
ALTER TABLE game_stat_defense    ADD COLUMN IF NOT EXISTS big_hits INT, ADD COLUMN IF NOT EXISTS catches_allowed INT;
ALTER TABLE season_stat_defense  ADD COLUMN IF NOT EXISTS big_hits INT, ADD COLUMN IF NOT EXISTS catches_allowed INT;
ALTER TABLE game_stat_blocking   ADD COLUMN IF NOT EXISTS pancakes INT;
ALTER TABLE season_stat_blocking ADD COLUMN IF NOT EXISTS pancakes INT;
ALTER TABLE game_stat_general    ADD COLUMN IF NOT EXISTS games_started INT;
ALTER TABLE season_stat_general  ADD COLUMN IF NOT EXISTS games_started INT;

-- Madden's career totals at each weekly load (raw Career*Stats fields). Only differences between rows mean anything.
CREATE TABLE IF NOT EXISTS player_career_counters (
    season       INT NOT NULL,
    week         INT NOT NULL,              -- current_week(snapshot); 0 = any load outside the regular season/playoffs
    player_id    INT NOT NULL REFERENCES players(id),
    madden_table VARCHAR(40) NOT NULL,      -- CareerOffensiveStats, CareerDefensiveKPReturnStats, ...
    counters     JSONB NOT NULL,            -- {Madden field: int}, every numeric field on the row
    games_played INT,                       -- counters->GAMESPLAYED, pulled out for the views
    capture_id   INT REFERENCES captures(id) ON DELETE SET NULL,
    read_at      TIMESTAMPTZ,               -- snapshot meta.read_at
    PRIMARY KEY (season, week, player_id));
CREATE INDEX IF NOT EXISTS player_career_counters_player ON player_career_counters (player_id, season, week);
```

`counter_delta(cur jsonb, prev jsonb) RETURNS jsonb` (SQL, IMMUTABLE): `{k: cur[k] − COALESCE(prev[k], 0)}` for every key in `cur`. Negative values are kept (S13).

`player_week_counters` view: one row per `player_career_counters` row, using `LAG(...) OVER (PARTITION BY player_id ORDER BY season, week)`:
- `season`, `week`, `player_id`, `madden_table`, `prev_season`, `prev_week`
- `games_covered` = `games_played − prev games_played`
- `status`:
  - `'BASELINE'`: no previous row
  - `'TABLE_CHANGED'`: `madden_table` differs from the previous row
  - `'RESET'`: `games_covered < 0`
  - `'NO_GAMES'`: `games_covered = 0`
  - `'OK'`: otherwise
- `delta` = `counter_delta(counters, prev counters)` when status is `OK` or `NO_GAMES`, else NULL

`player_season_counters` view: per `(season, player_id)`:
- `delta` = the `OK` week deltas summed key by key (`jsonb_each` + `SUM` + `jsonb_object_agg`)
- `games_covered` = sum over `OK` rows
- `weeks_unknown` = count of `TABLE_CHANGED` / `RESET` rows in the season

A season's rows include its week 0 baseline, so week 1's difference is measured from the preseason load.

Both views also expose typed columns for the counters U3 reads, cast from `delta`:
- `targets` (`TARGETS`), `madden_routes` (`ROUTESRUN`, named so it isn't mistaken for routes run, S11)
- `air_yards`, `pressures_faced` (`PRESSURES`), `sep_tight`, `sep_normal`, `sep_open`, `sep_wide_open`
- `short_att`, `short_cmp`, `short_yds`, `short_td`, `short_int` and the `medium_*` / `deep_*` sets
- `pocket_in_*` / `pocket_out_*` (att, cmp, yds, td, int)
- `blitz_plays`, `blitz_cmp`, `blitz_yds`, `blitz_td`, `blitz_int`
- `scramble_att`, `scramble_yds`, `scramble_td`
- `throw_left`, `throw_middle`, `throw_right`
- `rush_success`, `rush_10plus`, `light_box`, `loaded_box`
- `def_targets`, `def_catches_allowed` (`CTHALLOWED`), `def_pressures`, `stops`, `stuffs`, `downs_played`

U2 writes the full list into the migration from the M27 attribute names in S5/S9. A field that isn't in the row's table comes out NULL, not 0.

`db/init_db.sql` gets the same DDL: the new columns go in each `CREATE TABLE`, and the new table, function and views go at the end. The `season_stat_lines` / `game_stat_lines` definitions in both files gain, **appended at the end**: `rec_drops`, `rush_20plus`, `def_big_hits`, `def_catches_allowed`, `blk_pancakes`, `games_started` (S16).

### 2.3 Loader (U2)

- `offense_cols`: `"rec_drops": s.get("RECEIVEDROPS", 0)`, `"rush_20plus": s.get("RUSH20YARDRUNS", 0)`.
- `defense_cols`: `"big_hits": s.get("BIGHITS", 0)`, `"catches_allowed": s.get("CTHALLOWED", 0)`.
- The blocking lambda becomes `blocking_cols(s)`: `{"sacks_allowed": OLINESACKSALLOWED, "pancakes": OLINEPANCAKES}`.
- `group_lines` adds `("GAMESSTARTED", "games_started")` to the general fields (max over rows, like `downs_played`). `load_game_lines` writes `downs_played` and `games_started` to `game_stat_general` (`:647-650` today keeps only `downs_played`), and `load_season_lines` (`:729`) writes `games_played`, `downs_played` and `games_started`.
- New `load_career_counters(cur, snapshot, season, players, capture_id, report)`, called in `load_snapshot` after `load_season_lines`:
  - Skips (with `report["career_counters"] = "reader_version < N"`) on older snapshots, where N is the module constant `CAREER_COUNTERS_READER_VERSION`, equal to U1's `READER_VERSION`.
  - `week = current_week(snapshot) or 0`.
  - For every matched player with `career_stats`, it upserts `(season, week, player_id)` with `madden_table`, the numeric `counters`, `games_played`, `capture_id` and `read_at`. A reload of the same week replaces the row.
  - Writes `report["career_counter_rows"]`, which is also added to `REPORT_COUNTS`.
  - On a database without the table (migration not applied) it reports `"career_counters": "table missing"` and writes nothing, so it never fails the load.
- The module docstring's "Writes" list gains the `player_career_counters` line.

### 2.4 Metrics module `engines/advanced_metrics.py` (U3)

```python
@dataclass(frozen=True)
class Metric:
    key: str              # 'catch_rate', 'qb_adot', ...
    label: str
    group: str            # 'receiving' | 'passing' | 'rushing' | 'defense' | 'blocking' | 'special'
    inputs: tuple[str, ...]   # view column names (§2.2) or stat_lines column names
    formula: Callable[[dict], float | None]   # None on a zero denominator or a missing input
    status: str           # 'OK' | 'BLOCKED' | 'IMPOSSIBLE'
    reason: str           # empty for OK

METRICS: dict[str, Metric]
def metric_status() -> list[dict]           # [{key, label, status, reason}] for the CLI and docs
def compute(row: dict) -> dict[str, float | None]   # every OK metric over one merged row
def player_metrics(cur, season, player_id, week=None) -> dict
    # merges player_season_counters (or player_week_counters for one week) with season_stat_lines
    # (or game_stat_lines); returns {"inputs": {...}, "metrics": compute(...), "weeks_unknown": n}
```

CLI: `python3 -m engines.advanced_metrics --list` prints `metric_status()`. `python3 -m engines.advanced_metrics --season 2027 --player <id> [--week N]` prints one player's metrics. It's read-only and uses `DATABASE_URL`.

Team denominators (target share, snap share) come from `team_stats.passing_attempts` / `total_plays` summed over the player's team's games for the same season or week.

### 2.5 Metric status (what U3 encodes)

| Status | Metrics (doc §4 numbering) |
|---|---|
| **OK** | 4.1.3 Target share; 4.1.7 Catch rate, Drop rate (drops from the game rows); 4.2.1 P2S; 4.2.2 Pressure rate faced; 4.2.5 Pocket in/out split; **QB aDOT** = air_yards / pass attempts (new: replaces receiver aDOT); 4.3.1 Rush success rate; 4.3.2 YAC/att; 4.3.3 BTK rate; 4.3.4 Explosive rate (10+ from career deltas, 20+ from game rows); 4.3.5 Loaded/light box rate; 4.4.1 Coverage passer rating allowed (targets, catches allowed, INTs; coverage **yards and TDs aren't recorded**, so U3 uses a catch-and-INT version and names it `coverage_rating_lite`); 4.4.2 Burn %; 4.4.4 Sack conversion rate; 4.4.5 Stop rate (stops / downs played); 4.5.3 Snap share; 4.5.4 TB % |
| **BLOCKED** (until R2) | 4.1.1 TPRR; 4.1.2 YPRR; 4.1.4 Route participation: they need real routes run, and `ROUTESRUN` isn't that (S11) |
| **IMPOSSIBLE** | 4.1.5 receiver aDOT and 4.1.6 RACR (air yards is a passer stat, S9); 4.1.8 receiver separation index (separation is a passer stat), and no per-bucket separation split either (S21); 4.2.3 Deep rate, 4.2.4 depth-split comp % / YPA / passer rating, 4.2.6 blitz passer rating and INT rate, 4.2.7 scramble rate (their counters are static legacy totals, S19); 4.3.6 Inside/outside concept split (`INSIDE/OUTSIDERUN_*` never fill, S10); 4.4.3 PRate and 4.5.1 / 4.5.2 sack and pancake rates per pass-block or run-block snap (Madden doesn't split snaps by type); 4.4.5 Stuff rate per run-defense snap (same reason; `stuffs / downs_played` is offered as `stuff_rate_all_snaps`); 4.4.6 Press frequency (`COVERAGEDEPTH_*` never fill, S10); 4QCOMEBACKS, FIRSTDOWNS, GAMERATING, game-winning FGs (never filled, S3/S4) |

### 2.6 Reference doc corrections (U3)

These go in `docs/reference/MADDEN_STATS_AND_ADVANCED_METRICS.md`:
- **§2.3:** the tracking fields live only in `Career*Stats`, start near zero, and only their differences mean anything.
- **§3:** separate which table actually holds each field (Game / Season / Career only), and mark the never-filled fields.
- **§4:** add a status line to each metric, matching §2.5; replace receiver aDOT with QB aDOT.
- **§5:** the loader row lists what's really ingested and where (game rows → columns; career → `player_career_counters`).

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Schema 620 read against a 988 save could put tracking fields in the wrong place (S14). The box counters match exactly (S7), but the tracking fields aren't proven. | Spike step 1: for a QB, `SHORT+MEDIUM+DEEPPASS_ATTEMPTS` growth should be ≤ `PASSATTEMPTS` growth, `INSIDE+OUTSIDEPOCKETTHROW_ATTEMPTS` growth ≈ `PASSATTEMPTS` growth, and `THROWLOCATION_*` growth ≈ attempts. Allen's `SEPARATION_TIGHT`, `_NORMAL` and `_OPEN` are all 134, which is suspicious. | A misread field gets status `BLOCKED` with the reason, and its typed view column is dropped. The raw JSONB is kept, so a later fix needs no reload. |
| R2 | `ROUTESRUN` means something other than routes run (S11). | Spike step 2: compare `ROUTESRUN` growth with targets, catches and downs played over one week of a CAREER-TUNE-* clone, for WRs, TEs and HBs. | TPRR / YPRR / route participation stay `BLOCKED`, and the doc says so. |
| R3 | Preseason games might add to career totals, which would make the week 0 → week 1 difference cover preseason games too. | The first live week-1 load: `games_covered` should be ≤ 1 for every player. `test_load_save.py` can't check this; it's the live check in U2's Done-when. | Store week 0 from the **last** preseason load only (it already upserts, so the last load wins). If preseason still leaks, mark week 1 `NO_GAMES`-like and document it. |
| R4 | A missed weekly load makes the next difference cover 2+ games. | `games_covered > 1` in `player_week_counters`. | Expected and allowed. The season view still sums correctly; per-week metrics for that row are labelled "weeks N–M". |
| R5 | A career row changes table mid-season and loses its tracking fields (S12). | `status = 'TABLE_CHANGED'`, `weeks_unknown` on the season. | That week is lost, and the season shows `weeks_unknown`. 38 of about 2,600 players in a season (S12), and only once each. |
| R6 | Snapshot size and load time grow. | U1 records the snapshot size before and after on the sample save; U2 records the load time. | Acceptable up to about +30% (estimate). Past that, U1 drops the zero fields from `stats`. |
| R7 | All the evidence is from CPU-simmed seasons (supersim). Played games may fill more fields (S10). | Out of scope. The fields are stored raw, so any extra field shows up without a code change. | None. |

**Spike — done 2026-11-10** (orchestrator, offline on copies of `CAREER-MU2026` and `CAREER-TUNE-P2G`; no Madden PC claim needed). Results are S19–S22:
1. R1's consistency checks run across every matched QB. The pocket, throw-location and separation partitions are mutually consistent (S20); the depth splits and the blitz/scramble counters are static legacy totals (S19), so §2.5 marks their metrics IMPOSSIBLE; separation buckets are aliased (S21).
2. R2's `ROUTESRUN` comparison is answered: it is not routes run (S22), so TPRR / YPRR / route participation stay BLOCKED.

## 4. Tests (synthetic, no live DB)

- `pipeline/save_reader/test/read_save.test.mjs`: `READER_VERSION` is one above its value on the base commit. On the sample save (skips when absent, like the file's other real-save tests): most rostered players have `career_stats` with a `Career*` table name and numeric `stats`. In-memory fixture: a player whose `CareerStats` reference is empty gets `career_stats: null`, and a degenerate row doesn't throw.
- `tests/test_load_save.py`:
  - `offense_cols` / `defense_cols` / `blocking_cols` map `RECEIVEDROPS`, `RUSH20YARDRUNS`, `BIGHITS`, `CTHALLOWED`, `OLINEPANCAKES`.
  - `group_lines` carries `games_started`.
  - `load_career_counters`: upserts one row per player per week; a reload of the same week replaces it; week 0 is used off-season; a v8 snapshot writes nothing and reports why; a missing table reports and doesn't raise.
  - Run against the scratch Postgres (`madden_test`, as the file's other DB tests do): the `player_week_counters` statuses `BASELINE`, `OK`, `NO_GAMES`, `TABLE_CHANGED` and `RESET` from hand-built rows; negative deltas kept; `player_season_counters` sums only `OK` rows and counts `weeks_unknown`.
  - The season-totals-match-game-lines test (`:724`) is extended to cover the new columns.
- `tests/test_advanced_metrics.py`:
  - Every doc §4 metric has an entry in `METRICS`.
  - Every `OK` metric computes on a hand-built row with known answers (catch rate 109/136, QB aDOT, P2S, burn %, stop rate).
  - A zero denominator gives None; a missing input gives None.
  - `BLOCKED` / `IMPOSSIBLE` metrics are never computed and have a non-empty reason.
  - `player_metrics` against a stub cursor merges the counter and stat-line rows.

Run with `pytest -m "not integration" tests/test_load_save.py tests/test_advanced_metrics.py` and `cd pipeline/save_reader && npm test`. View tests run against `madden_test` only.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Reader reads CareerStats

- **Owns:** `pipeline/save_reader/pc_save_reader.mjs`, `pipeline/save_reader/test/read_save.test.mjs`
- **Reads:** this plan §2.1, `pipeline/save_reader/lib.mjs` (`decodeRef`, `plainFields`), `pc_save_reader.mjs:342-425`
- **Depends on:** none in this plan; starts from main after builder-4's M-0323 drafted-cohort batch is merged (S18)
- **Done when:** a snapshot of the sample save (or a copy of `CAREER-TUNE-P2G`) has `meta.reader_version` 10 (one above main's 9) and `career_stats` on players; snapshot size before/after noted in the unit report (R6).
- **Verify:** `cd pipeline/save_reader && npm test` passes. `node pc_save_reader.mjs <copy of a save> --out /tmp/x.json`, then the snapshot has `career_stats.table` starting with `Career` for at least 2,000 players.

### U2: Migration, loader and counter views

- **Owns:** `db/migrations/2027_02_25_madden_stat_counters.sql` (new), `db/init_db.sql`, `pipeline/load_save.py`, `tests/test_load_save.py`
- **Reads:** this plan §2.2–§2.3, `db/migrations/2026_10_23_capture_loader.sql`, `db/migrations/2026_12_30_history_player_rows.sql:25` (latest `season_stat_lines`), `db/init_db.sql:2684-2760`, `pipeline/load_save.py:160-305, 623-750, 1193-1232`
- **Depends on:** none in this plan (builds against the §2.1 snapshot shape; its tests use hand-built snapshots); starts from main after builder-4's M-0323 batch is merged (S18)
- **Done when:** the migration applies cleanly twice to a scratch database built from `init_db.sql`, and `init_db.sql` alone builds the same schema (`db.migrate diff` is empty; pg_dump differs only in column order on the four season tables, see U2). A load of a hand-built snapshot at the new reader_version writes `player_career_counters`, and the views return the expected statuses.
- **Verify:** `pytest -m "not integration" tests/test_load_save.py` passes. `psql $SCRATCH -f db/migrations/2027_02_25_madden_stat_counters.sql` applies twice with no error (recipe: memory "Postgres in Docker", `madden-db` container).

### U3: Metrics module and doc corrections

- **Owns:** `engines/advanced_metrics.py` (new), `tests/test_advanced_metrics.py` (new), `docs/reference/MADDEN_STATS_AND_ADVANCED_METRICS.md`
- **Reads:** this plan §2.4–§2.6 and §0, `pipeline/load_save.py` (`passer_rating`, `_ratio`), the reference doc
- **Depends on:** none (reads the §2.2 view columns by name; its tests use a stub cursor)
- **Done when:** `--list` prints every doc §4 metric with its status, and the doc's §2.3/§3/§4/§5 match §2.5–§2.6.
- **Verify:** `pytest -m "not integration" tests/test_advanced_metrics.py` passes. `python3 -m engines.advanced_metrics --list` prints about 35 rows with no traceback.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2, U3 | builder-4's M-0323 claim released (S18); spike first | No shared files. U2 and U3 build against the interfaces in §2.1 / §2.2, not merged code. |

After the merge, the orchestrator runs one end-to-end check: snapshot a copy of `CAREER-TUNE-P2G`, run `python3 -m pipeline.load_save <snapshot> --season 2027` (rollback, no `--commit`) against a scratch database, and confirm `career_counter_rows` > 2,000 and the new columns are non-zero in `season_stat_lines`.

**File ownership check:**

| File | Unit |
|---|---|
| `db/init_db.sql` | U2 |
| `db/migrations/2027_02_25_madden_stat_counters.sql` | U2 |
| `docs/reference/MADDEN_STATS_AND_ADVANCED_METRICS.md` | U3 |
| `engines/advanced_metrics.py` | U3 |
| `pipeline/load_save.py` | U2 |
| `pipeline/save_reader/pc_save_reader.mjs` | U1 |
| `pipeline/save_reader/test/read_save.test.mjs` | U1 |
| `tests/test_advanced_metrics.py` | U3 |
| `tests/test_load_save.py` | U2 |

No path appears twice.

## 7. Spec changes when built

- **§3.3:** `player_stats` / `season_stats` gain `rec_drops`, `rush_20plus`. The capture stat categories gain `big_hits`, `catches_allowed` (defense), `pancakes` (blocking), `games_started` (general). The `season_stat_lines` / `game_stat_lines` view lists gain the six appended columns. New bullets for `player_career_counters`, `counter_delta`, `player_week_counters` and `player_season_counters` (statuses, the "only differences mean anything" rule, week 0).
- **New §2.x Advanced Metrics (`engines/advanced_metrics.py`):** what's computable, blocked and impossible, and the CLI.
- **§1.1 Repository Layout:** `load_save.py` line (career counters, the new reader_version); `pc_save_reader.mjs` / save_reader line (the new reader_version, `career_stats`); the new `engines/advanced_metrics.py` line; the new migration; the reference doc's description (it now carries a status per metric).
- **§6.0b:** the weekly ingest also writes `player_career_counters` (no run-order change).

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | Store the career fields as one JSONB per row (with typed view columns) or as about 150 real columns? | **JSONB.** The Career tables have up to 94 fields, half of them never filled today, and Madden may fill more later. JSONB stores them all without a migration each time; the views give typed columns for the ones the metrics use. |
| Q2 | Should a week-level metric be shown when a difference covers 2+ games (a missed load)? | **Yes, labelled as weeks N–M.** Dropping it loses data the season totals already count. |
| Q3 | Metrics that can't be computed (receiver aDOT, RACR, separation, concept splits, press coverage): list them as impossible, or approximate them (e.g. a receiver's share of his QB's air yards by target share)? | **List them as impossible.** An approximation would look like a real Madden number to writers and fantasy players. If one is wanted, it belongs in a later plan with its own label. |
| Q4 | Build wave 1 even if the spike blocks `ROUTESRUN` (TPRR/YPRR)? | **Yes.** Everything else stands on its own; those three metrics just stay `BLOCKED`. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | `worktree-agent-afbe62c3f77d9688e` | `.claude/worktrees/agent-afbe62c3f77d9688e` | merged; reader_version 10, 2,484 career rows, snapshot +21.1%; offseason_check e2e 41 PASS / 4 INFO | `b3cb417` |
| U3 | 1 | `worktree-agent-a818a949e0cdaefa3` | `.claude/worktrees/agent-a818a949e0cdaefa3` | merged; 37 metrics (20 OK / 3 BLOCKED / 14 IMPOSSIBLE), 9 tests pass, doc §1–§5 corrected | `96d6c74` |
| U2 | 1 | `worktree-agent-a025a31777ebeacb6` | `.claude/worktrees/agent-a025a31777ebeacb6` | merged; migration + loader + views; 53 offline tests pass, migration applies twice, init_db.sql builds the same schema (`db.migrate diff` empty; the only pg_dump difference is column order on four tables: `season_stat_blocking.pancakes`, `season_stat_defense.catches_allowed`, `season_stat_general.games_started` and `season_stats.rush_20plus` sit before `source` in an init_db build and after it on a migrated database), 2,484 career-counter rows on P2G and non-zero new columns on P2H | `53a48dd` |
