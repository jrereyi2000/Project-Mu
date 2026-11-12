# P5-01 — The Shadow Gate

**Task:** `docs/CLOSE_OUT_CHECKLIST.md` P5-01 — *apply migrations to a tuning database; run
`personality.features show` — every feature must be able to reach `shadow`.*

Run 2026-11-12 by `p5-01-shadow-gate` (`deepseek-v4.1-flash`). Database work only: no Madden, no PC, no
`CAREER-*` save, no engine or fit code, no checklist edit, no audit verdict.

---

## 0. Result

**PASS.** All 20 personality features reached `shadow` on `madden_tune_p2g`, each one confirmed by a
`personality.features show` of its own, in `FEATURES` order. They were then all returned to `off`, which is the
documented state the database is left in (§6).

| | Before | After |
|---|---|---|
| `madden_tune_p2g` migrations | 138 applied, **7 pending**, 0 drifted | **145 applied, 0 pending, 0 drifted** |
| `league_state.shape_season` | absent (column did not exist) | **2026** |
| existing table rows | 355,419 | 355,427 (+7 ledger rows, +1 `nfl_mirror_settings` default row) |
| features in `shadow` | 0 of 20 | 20 of 20, then **0 of 20** |
| feature mode changes | — | 40 (`off→shadow` ×20, `shadow→off` ×20), kept as history |

**Head is 145 migrations, not 144.** The brief said 144 applied on `madden_mu2026` / `madden_test`; both read
**145** now because `2027_02_30_fact_gate.sql` landed on `main` today (board M-0679) after that snapshot was taken.
`ls db/migrations/*.sql | wc -l` = 145, and the migration-ledger block at the end of `db/init_db.sql` (written by
`db.migrate sync-init-db`) ends with `2027_02_30_fact_gate`, so the head of the list is 145 by every count in the
repo.

---

## 1. Which database, and whether it could be migrated in place

`madden_tune_p2g` holds the only offseason season data in the tuning set (2026 season + offseason ingested,
`league_state` = 2027 / week 1), so JR's rule was: back it up, migrate **in place only if the pending files are
additive**, otherwise copy it to a scratch `madden_tune_p501` and work there.

**Backup first** (before anything else, `db/backups/` is git-ignored):

```bash
TS=$(date +%Y%m%d_%H%M%S)
docker exec madden-db pg_dump -U postgres -d madden_tune_p2g > db/backups/madden_tune_p2g_backup_${TS}.sql
tail -1 db/backups/madden_tune_p2g_backup_${TS}.sql   # -- PostgreSQL database dump complete
```

Result: `db/backups/madden_tune_p2g_backup_20261112_155444.sql`, 120,524,340 bytes, 198 `CREATE TABLE`s,
dump-complete marker present.

### What the 7 pending files actually do

| Migration | What it changes |
|---|---|
| `2027_02_24_player_draft_facts` | `players.draft_team_id` (new nullable column + FK) |
| `2027_02_25_madden_stat_counters` | new tables (`player_career_counters`, …) — none existed here |
| `2027_02_26_league_shape_season` | `league_state.shape_season` (new column + backfill: first `player_seasons` season) |
| `2027_02_27_nfl_mirror` | 3 new `nfl_mirror_*` tables + one seeded settings row; widens `nfl_player_links`' CHECK with `CREATED` |
| `2027_02_28_stats_loader_weeks` | `player_career_counters.stage/load_seq` + PK rebuild + `player_week_counters` view |
| `2027_02_29_stats_counter_season_games` | replaces the `player_week_counters` / `player_season_counters` views |
| `2027_02_30_fact_gate` | `news_feed.fact_gate_status` + `fact_gate` + CHECK + partial index |

Three things in that list look non-additive and are not, on this database:

- `2027_02_28` DROP/ADD PRIMARY KEY is on `player_career_counters`, which **does not exist** before the batch —
  `2027_02_25` creates it, in the same `apply`. So the rebuild runs on an empty table.
- `2027_02_27` and `2027_02_30` swap CHECK constraints. `nfl_player_links` has **0 rows** and the new method list
  is a superset of the old one; `news_feed` has 2 rows and all of them satisfy the new status list or are NULL.
  A violation would fail the file's own transaction, never half-apply it.
- Nothing drops a table, deletes rows, renames a column or narrows a type.

### The proof, on a throwaway clone, before touching p2g

```bash
docker exec madden-db psql -U postgres -c "CREATE DATABASE madden_tune_p501 TEMPLATE madden_tune_p2g"
DSN="host=localhost dbname=madden_tune_p501 user=postgres password=postgres"
python3 -m db.migrate status --dsn "$DSN"     # 138 applied, 7 pending, 0 drifted
python3 -m db.migrate apply  --dsn "$DSN"     # 7 applied
python3 -m db.migrate status --dsn "$DSN"     # 145 applied, 0 pending, 0 drifted
```

Every `public` table's row count was captured before and after on the clone: **198 tables → 202**, no table
dropped, **no existing table's row count changed**, and the only changed row count was
`schema_migrations` 138 → 145. That is what made the in-place apply on `p2g` safe, and it is the check to repeat
on any database before applying in place. The clone was dropped afterwards
(`python3 -c "from db import franchises; franchises.drop_db('madden_tune_p501')"`).

---

## 2. The runbook — a fresh tuning database (this is what P5-02 uses)

`.venv/bin/python3`, from the repo root, with the Docker Postgres (`madden-db`) up. `$RUN` is the run name and the
database is `madden_tune_<run>` lowercased (`engines/tuning/season_loop.py:72`).

```bash
RUN=P502            # -> save CAREER-TUNE-P502, database madden_tune_p502
DSN="host=localhost dbname=madden_tune_p502 user=postgres password=postgres"
SAVES="${MADDEN_SAVES_DIR:-/mnt/c/Users/jrere/Documents/Madden NFL 27/saves}"
```

**Step 1 — clone the save (on the PC; P5-02's own step, the only PC part of this runbook).**

```bash
.venv/bin/python3 -m tools.madden_pilot clone-save --from CAREER-MU2026 --to "CAREER-TUNE-$RUN"
```

**Step 2 — create, seed and first-ingest the database.** This is the only path that creates a tuning database
(`docs/reference/TUNING_LEAGUE.md` §3). Run it on its own, **before** the season, so the switchboard can be set
while the database is still untouched:

```bash
.venv/bin/python3 -m pipeline.watch_save "$SAVES/CAREER-TUNE-$RUN" --once --auto-init \
    --dsn "$DSN" --no-articles --no-freeze
```

`--auto-init` is idempotent: it creates the database if absent, seeds it if empty, and does nothing if it already
holds data, so re-running it or letting `season_loop` run it again is harmless.

**Step 3 — migrations.** A database built from `db/init_db.sql` is already at head (the ledger block at the end of
that file marks every migration applied), so `apply` is usually a no-op; run both anyway, it is the whole point of
the gate:

```bash
.venv/bin/python3 -m db.migrate sync-init-db                  # keeps init_db.sql at head; `git diff` must be empty
.venv/bin/python3 -m db.migrate status --dsn "$DSN"          # expect 145 applied, 0 pending, 0 drifted
.venv/bin/python3 -m db.migrate apply  --dsn "$DSN"          # applies anything pending
.venv/bin/python3 -m db.migrate status --dsn "$DSN"          # must now read 145 / 0 / 0
```

**Step 4 — the switchboard must be present and empty.**

```bash
.venv/bin/python3 -m personality.features show --dsn "$DSN"
```

Expect 20 features, every one `off`, and every one with `"next": "shadow", "allowed": true`. If `show` says the
switchboard tables are not migrated, `2027_01_31_personality_feature_modes.sql` is missing — go back to step 3. If
it says `league_state.madden_league_id is not set`, the load in step 2 did not happen.

**Step 5 — shadow only what this run measures.** P5-02 measures `producers` and `relationships`; the other 18 stay
`off` (see §5 of the checklist, `docs/CLOSE_OUT_CHECKLIST.md` §7.1).

```bash
V=p502-1
.venv/bin/python3 -m personality.features set producers     shadow --version $V --review "P5-02 observe-only season" --dsn "$DSN"
.venv/bin/python3 -m personality.features set relationships shadow --version $V --review "P5-02 observe-only season" --dsn "$DSN"
.venv/bin/python3 -m personality.features show --dsn "$DSN"     # both shadow, shadow_since = the league's now
```

**Step 6 — run the season** through the normal P2-05 path, which reuses the database created in step 2:

```bash
.venv/bin/python3 -m engines.tuning.season_loop --run "$RUN" --decision-targets data/tuning/decision_targets.json
```

**Step 7 — read the reports, then leave the database in the state you mean.** `producers` and `relationships` may
stay in `shadow` for the season (that is their clearance); everything else must read `off`:

```bash
.venv/bin/python3 -m personality.features show --dsn "$DSN"
.venv/bin/python3 -m personality.features history --dsn "$DSN"      # one row per change, JR-style receipt trail
```

`python3 -m personality.features set <feature> off --version <v> --review "<why>" --dsn "$DSN"` is always allowed
and writes history; `off` also clears `shadow_since`.

---

## 3. Migration numbers, before and after

```
madden_tune_p2g: 138 applied, 7 pending, 0 drifted, 0 in ledger only     # before
madden_tune_p2g: 145 applied, 0 pending, 0 drifted, 0 in ledger only     # after
```

The 7 applied, in order, at 2026-11-12 15:55 EST (recorded in `schema_migrations.applied_at`):
`2027_02_24_player_draft_facts`, `2027_02_25_madden_stat_counters`, `2027_02_26_league_shape_season`,
`2027_02_27_nfl_mirror`, `2027_02_28_stats_loader_weeks`, `2027_02_29_stats_counter_season_games`,
`2027_02_30_fact_gate`.

Effect on the database: 4 new empty tables (`nfl_mirror_settings` gets its one default row, disabled, season 2026),
`nfl_mirror_weeks`, `nfl_mirror_moves`, `player_career_counters`; `league_state.shape_season` backfilled to **2026**
(min `player_seasons` season); `players.draft_team_id` and `news_feed.fact_gate_status` / `fact_gate` added as NULL.
No existing row changed.

### The rest of the tuning set, for the record

| Database | Migrations | Note |
|---|---|---|
| `madden_tune_p2g` | **145 / 0 / 0** | migrated in place today (this task) |
| `madden_tune_p2h` | 138 / 7 / 0 | still has the same 7 pending |
| `madden_tune_p2c` / `p2d` / `p2e` / `p2f` | 137 / 8 / 0 | pending from `2027_02_24` on |
| `madden_tune_p2a` | 131 / 14 / 0 | older still |
| `madden_mu2026`, `madden_test` | 145 / 0 / 0 | live franchise and its clone, already at head |

`python3 -m db.migrate status --dsn "host=localhost dbname=<db> user=postgres password=postgres"` gives the line
for any of them. Nothing else was migrated in this task.

### One pre-existing schema difference, not caused by this task

`db.migrate diff --a madden_tune_p2g --b madden_mu2026` reports one difference:
`player_stats` carries a UNIQUE **constraint** `player_stats_game_id_player_name_position_team_id_key` on `p2g`
where `madden_mu2026` (and `init_db.sql`) carry the UNIQUE **index** `player_stats_game_name_position_team` from
`2027_02_23_player_stats_team_key.sql`. The constraint is in p2g's **pre-migration backup**, so it came from a hand
fix during the 2026-11-10 ingest failure (the same incident that produced `2027_02_23`), not from any of the 7
migrations applied here. It covers the same four columns, so it is equivalent for the loader's upsert; a database
built from `init_db.sql` will not have it. Left alone — no personality feature reads `player_stats`.

---

## 4. Every feature's transition

Run on `madden_tune_p2g`, league id `1287775425`, `now` = season **2027**, tick **1** (from `league_state`:
`current_season`, `current_week` clamped to the 32-tick calendar). Version **`p501-gate-1`** for every feature,
review note *"P5-01 reachability probe 2026-11-12: shadow path only, no season run; returned to off after the
probe."*

Each row is `off → shadow`, confirmed by its own `show`, then `shadow → off`, confirmed by `show`.

| # | Feature | `off → shadow` | `show` after set | `shadow_since` | `shadow → off` | `show` after clear |
|---|---|---|---|---|---|---|
| 1 | `producers` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 2 | `relationships` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 3 | `profiles` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 4 | `evolution` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 5 | `focus_refresh` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 6 | `contracts_ambition` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 7 | `contracts_relationships` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 8 | `development_learning` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 9 | `trade_requests` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 10 | `retirement` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 11 | `development_opportunity` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 12 | `coach_loop` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 13 | `player_loop` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 14 | `owner_patience` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 15 | `owner_hiring` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 16 | `coaching_tree_hiring` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 17 | `owner_spending` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 18 | `owner_meddling` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 19 | `scout_directors` | ok | `shadow` | 2027 tick 1 | ok | `off` |
| 20 | `media_voters` | ok | `shadow` | 2027 tick 1 | ok | `off` |

- **20 of 20 reached `shadow`**, in `FEATURES` order, with no `--force` needed anywhere.
- With all 20 in shadow, `show` reported **0 of 20** allowed to go `active`: `producers` is refused by **D3** ("needs
  one full simulated season in shadow … or an imported clearance") and the other 19 by **D2** ("`producers` must be
  active before …"). That is the designed ceiling — shadow is the furthest the switchboard will take any feature on a
  database that has never run a shadow season.
- 40 rows in `personality_feature_mode_changes` (20 `None→shadow`, 20 `shadow→off`), all at 2027 tick 1, all version
  `p501-gate-1`, `personality_feature_clearances` still empty. They are the receipt for this gate and are left in
  place; `show` and every consumer read only `personality_feature_modes`, which is all `off`.
- `set_mode`'s CLI did not expose `set_by` when this probe ran, so the history rows read `set_by = JR`. They are
  this probe's, not a tuning decision by JR; P5-01a adds the flag but leaves these rows as they are.

---

## 5. What can block a feature from reaching `shadow`

`set_mode(feature, 'shadow')` runs two gates — `installed(feature)` in `personality/features.py` (the module
imports and the requirement's migration **file** is in `db/migrations/`) and `migration_applied(feature)`, which
reads this database's own `schema_migrations` for that file's name (its version is the filename stem, via
`db.migrate.version_of`) — after `_require_tables` and the league lookup. Everything else (D2 order, D3 shadow
season) applies only to `active`. So the ways a feature can fail to reach shadow are:

| Blocker | The message you get | Fix |
|---|---|---|
| The switchboard tables are missing (`2027_01_31_personality_feature_modes.sql` not applied) | `refused: the switchboard tables are not migrated …` | `db.migrate apply` |
| `league_state.madden_league_id` is NULL (no save loaded) | `error: league_state.madden_league_id is not set (load a save first)` | load the clone (§2 step 2) |
| The feature's module does not import — `REQUIREMENTS[feature][0]` | `… code is not installed (module <module> not found)` | the feature's own code must be on the branch |
| The feature's migration **file** is not in `db/migrations/` — `REQUIREMENTS[feature][1]` | `… code is not installed (migration <file> not present)` | write the migration (not just apply it — the check is on the file's presence) |
| The feature's migration is **not applied on this database** — its row is absent from `schema_migrations` | `… code is not installed on this database (migration <file> is not applied; run python3 -m db.migrate apply)` | `db.migrate apply` after backing up (§1/§2) |
| DSN names `madden_2036` or `postgres` | `refusing database …` | use the tuning database |

**Today, nothing blocks on `madden_tune_p2g`.** Every module in `REQUIREMENTS` imports on `main`, every
requirement migration file exists, and every one of them is **applied** on `madden_tune_p2g` (checked against
`schema_migrations`: none pending). The three features with no migration of their own — `development_learning`,
`development_opportunity`, `coaching_tree_hiring` — are the only ones whose requirement is module-only; they skip
the ledger query entirely. Going `off` is allowed on any database, ledger or not.

The one that used to block is historical and is closed: `owner_patience`, `owner_hiring`, `owner_spending`,
`owner_meddling`, `scout_directors` and `media_voters` all require `2027_02_02_owners_scouts_voters.sql`, the owners
plan's U6 migration, which `personality/features.py`'s own comment describes as held when that comment was written.
It is written and applied; P5-13's remaining dependency on it is now only "the features must actually be shadowing".

### What the gate does **not** prove

- **It proves the ledger row, not the tables.** `migration_applied()` reads `schema_migrations`; a row recorded on
  a database whose objects were later dropped, or by a hasty `baseline`, still passes. Run the check in §5.1 when
  you suspect the schema, not just the ledger.
- **It does not prove the feature's migration ran in this database's history with its current checksum.**
  `db.migrate status` reports drift (a file edited after it was applied); `set` does not.
- **It does not prove a shadow season runs.** Shadow is a mode, not an execution: P5-02 is the first run that puts
  `producers` and `relationships` through a real season, and P5-14 the first with all 20 at once.
- **It says nothing about becoming `active`** — that needs D2 order, a full shadow season (D3) or a clearance (D4),
  and JR's own decision in P6.

### 5.1 The missing-catalog check (run it per database, not per repo)

Since unit P5-01a the per-feature check is built in: `set FEATURE shadow` refuses (exit 4) on a database whose
`schema_migrations` lacks the feature's migration, and `show` reports `allowed: false` with the file named. The
script below is the bulk pre-flight over all 20 features at once, still worth running after a migration:

```bash
python3 - <<'EOF'
import subprocess
from personality.features import FEATURES, REQUIREMENTS
dsn = 'host=localhost dbname=madden_tune_p2g user=postgres password=postgres'
out = subprocess.run(['python3','-m','db.migrate','status','--dsn',dsn],capture_output=True,text=True).stdout
state = {p[1]: p[0] for l in out.splitlines() if len(p := l.split()) >= 3 and p[0] in ('ok','PEND','DRIFT','MISS')}
print('unapplied:', [(f, REQUIREMENTS[f][1]) for f in FEATURES
                     if REQUIREMENTS[f][1] and state.get(REQUIREMENTS[f][1]) != 'ok'] or 'none')
EOF
```

Result on `madden_tune_p2g`: `unapplied: none`.

---

## 6. State left behind

- **`madden_tune_p2g`**: 145 applied / 0 pending / 0 drifted; `league_state` 2027 week 1, `shape_season` 2026;
  6,428 `player_seasons`, 557 `games` (unchanged); **all 20 features `off`**; 40 history rows recording the probe.
- **Backup**: `db/backups/madden_tune_p2g_backup_20261112_155444.sql` (pre-migration, 120 MB, git-ignored). Restore
  with `gunzip -c`-free plain SQL: `docker exec -i madden-db psql -U postgres -d madden_tune_p2g < <file>` after
  dropping and recreating the database.
- **`madden_tune_p501`**: created as the additivity clone, migrated, verified, **dropped**. It does not exist.
- **No other database, file, engine, save or PC state was touched.** `db/init_db.sql` was not edited
  (`sync-init-db` reports it current at 145), and no migration file changed.

### Open for JR

1. **The other tuning databases are still behind** (§3): `p2h` 138/7 and `p2c`–`p2f` 137/8. Migrating them in place
   is the same additive operation proven here, but each needs its own backup first; nothing needs them today.
2. **P5-02's target.** The runbook in §2 creates its own fresh database from a fresh `CAREER-TUNE-*` clone. `p2g` is
   at head and holds the 2026 offseason, but it is not a week-1 neutral clone, so it is not the P5-02 season
   vehicle — it is the migrated fallback. Say the word if P5-02 should instead resume from `p2g`.
3. **The `set` CLI could not record `set_by`** and did not check the database's own migration state (§5).
   **Both fixed in unit P5-01a (2026-11-12):** `set` and `activate-cleared` take `--set-by`, `import-clearance`
   takes `--cleared-by`, and the value falls back flag → `MU_SET_BY` → `MU_AGENT_MODEL` → `'JR'`; `set` refuses a
   feature whose requirement migration is absent from the database's `schema_migrations`. The 40 history rows
   P5-01 wrote still read `set_by = JR` — they were not rewritten. Open for JR: whether the flag should be
   **required** on a `shadow`/`active` write instead of defaulting.
