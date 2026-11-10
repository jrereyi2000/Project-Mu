# The Tuning League

The disposable league Project Mu tunes against (CLOSE_OUT_CHECKLIST P2). It is driven by the same code paths as the
live franchise but can be thrown away at any point, and it cannot leak into the real franchise.

## 1. Saves

| Save | What it is | May it be simulated? |
|---|---|---|
| `CAREER-MUBASE` | The creation input, hash-pinned in `data/baselines/` (P0-6). Never opened in game. | **Never.** |
| `CAREER-MU2026` | The tuning baseline: the post-`league_office setup` save — 32 user teams, fill-roster off, morale 50, 2,847 players bootstrapped (P2-09). | **Never.** It is only a clone source. |
| `CAREER-TUNE-<name>` | A disposable clone of `CAREER-MU2026`, one per tuning run (`CAREER-TUNE-P2A`, `CAREER-TUNE-RETIRE`, …). | Yes; only with the `/man` rules below. |

Saves live in `MADDEN_SAVES_DIR` (`.env`; default `/mnt/c/Users/jrere/Documents/Madden NFL 27/saves/`). The
franchise list in game strips the `CAREER-` prefix, so `CAREER-TUNE-P2A` is the tile `TUNE-P2A`.

- **Make a clone:** `python3 -m tools.madden_pilot clone-save --from CAREER-MU2026 --to CAREER-TUNE-<name>`. It is a
  file copy, not a screen action. An existing `CAREER-TUNE-<name>` file is replaced, so re-cloning is the "fresh
  save" reset. **The copy is then capacity-pruned** (`pipeline/save_reader/prune_capacity.mjs`): `Highlight` goes
  from 3,200 allocated rows to 512, freeing 596,736 bytes of the 20 MiB unpacked window the modded season used to
  truncate at. The game keeps the smaller capacity when it rewrites the save, so one prune per clone lasts the
  whole run; a refused prune leaves the plain copy, prints a warning and records `pruned: false` in `result.json`.
  See `docs/in_progress/SAVE_CAPACITY_PRUNE_PLAN.md` §9.
- **How many:** keep the clone for the active run plus at most one predecessor with the same name. Delete older
  `CAREER-TUNE-*` clones by hand when a run is done. Never delete a `CAREER-MU*` file.
- **Quit rule:** the pilot may only load, sim and quit `CAREER-TUNE-*` saves, and never while `pipeline.watch_save`
  runs.

## 2. Databases

Tuning databases live in the `madden-db` Docker Postgres container (see `postgres-docker.md` in the user memory /
`PROJECT_SPEC.md`). The name **must** start with `madden_tune_`; every command below refuses any other name.

| Database | What it is |
|---|---|
| `madden_mu2026` | The live franchise database (LeagueID 1287775425). Never a tuning destination. |
| `madden_tune_<run>` | One disposable database per tuning run. Dropped and recreated freely. |

A clone keeps the live franchise's LeagueID. That is why:

- `pipeline.watch_save` skips `CAREER-TUNE-*` by default; a `--dsn` naming a `madden_tune_*` database (or
  `--allow-tune-save`) is the deliberate opt-in, and a tuning clone whose destination is any other database is
  refused (P1-24);
- **tuning clones get no registry entry** (P2-04). The registry (`db/franchises.py`) is keyed by LeagueID, so
  registering a clone would collide with the live franchise. Tuning ingests are driven manually with `--dsn`. The
  LeagueID is still stamped into `league_state` (never into the registry) because the personality universe keys on
  it.

Consequence of having no registry entry: a tuning database has **no user team**, so the inbox scan, user-facing
notifications and the next-week mod pack are skipped. That is correct for a tuning league — every decision in it
is a CPU decision, which is exactly what P5 measures.

## 3. Create, reset, drop

Use the repo's virtualenv: `.venv/bin/python3`.

The DSN for a tuning database (local Docker Postgres):

```
DSN="host=localhost dbname=madden_tune_p2a user=postgres password=postgres"
```

### Create and first ingest

```
.venv/bin/python3 -m pipeline.watch_save "$MADDEN_SAVES_DIR/CAREER-TUNE-P2A" --once --auto-init --dsn "$DSN" --no-articles --no-freeze
```

`--auto-init` with a `madden_tune_* --dsn` creates the database from `db/init_db.sql`, seeds it
(`db/seed_clean_db.py`: 32 teams, league state at the clone's season/week, coaches, GMs, draft picks, writers,
Napper), stamps the LeagueID and then performs the first load of the clone. This is the only path that creates a
tuning database.

### Reset (drop, recreate, re-seed, re-ingest)

```
.venv/bin/python3 -m pipeline.watch_save "$MADDEN_SAVES_DIR/CAREER-TUNE-P2A" --once --reseed --confirm-reseed madden_tune_p2a --dsn "$DSN" --no-articles --no-freeze
```

`--reseed` drops `madden_tune_p2a`, recreates it, seeds it from the clone and ingests it again. It needs
`--confirm-reseed` with the exact database name, and it refuses `--dry-run`. This is the P2-06 reset path.

### Drop just the database

```
.venv/bin/python3 -c "from db import franchises; franchises.drop_db('madden_tune_p2a')"
```

`drop_db` refuses reserved/off-prefix names, but use the exact name anyway. Do not drop with `psql`.

### List tuning databases

```
docker exec madden-db psql -U postgres -Atc "select datname from pg_database where datname like 'madden_tune_%' order by 1"
```

### Freezes

Tuning runs pass `--no-freeze`: the tuning database is disposable and the clone save already exists. Leave the
flag off only when a freeze is needed to debug a specific ingest.

## 4. Media and API budget (P2-07)

A tuning ingest must never spend API budget on articles.

- `--no-articles` skips the newsroom slate and (with it) the press-conference run.
- The watcher additionally sets **`MU_NO_LLM=1`** in the ingest child process whenever the destination is a
  `madden_tune_*` database. Under that switch `engines/gm_llm.make_llm` returns `None` (every GM, scouting and
  inbox seam falls back to its template) and `engines/llm_provider.generate_text` refuses with `LLMDisabled`.
- An explicit `MU_NO_LLM=0` in the environment is the operator's deliberate override and wins.

Proof: after the P2-04 ingest, `llm_usage` in `madden_tune_p2a` is **0 rows**, and the GM talks that ran were
template-driven.

## 5. Isolation proof (P2-04, recorded 2026-11-09)

Clone `CAREER-MU2026` → `CAREER-TUNE-P2A`, ingest into `madden_tune_p2a`, and the live database does not move:

| | `madden_mu2026` before | `madden_mu2026` after | `madden_tune_p2a` |
|---|---:|---:|---:|
| `league_state` | 2026 / 1 | 2026 / 1 | 2026 / 1 |
| players | 2,847 | 2,847 | 2,847 |
| games | 272 | 272 | 272 |
| news_feed | 0 | 0 | 0 |
| llm_usage | 0 | 0 | 0 |
| schema_migrations | 131 | 131 | 131 |
| database size (bytes) | 34,499,931 | 34,499,931 | 31,632,731 |

Check it yourself before and after any tuning ingest:

```
docker exec madden-db psql -U postgres -d madden_mu2026 -Atc "
select 'league_state='||current_season||'/'||current_week from league_state
union all select 'players='||count(*) from players
union all select 'games='||count(*) from games
union all select 'news_feed='||count(*) from news_feed
union all select 'llm_usage='||count(*) from llm_usage
union all select 'size='||pg_database_size('madden_mu2026')"
```

## 6. Season and offseason driving (P2-03 decision)

**Decision (2026-11-09): Mu drives every offseason stage through the save writer; the pilot advances exactly one
stage at a time and never sims through the offseason.**

The reasons:

- This is the production design. `pipeline/league_office.py` runs once per stage after JR advances Madden, plans
  Mu's moves for that stage (contracts, re-signings, free-agency waves, combine, pro day, draft, UDFA, cutdown) and
  writes them into the save; `watch_save` ingests each offseason stop as a stage.
- Simming through the offseason would let Madden's CPU make the cuts, signings, draft picks and retirements that
  P5 exists to tune. The result would measure Madden, not Project Mu.
- The 8 stops are in [`MADDEN_OFFSEASON_WALKTHROUGH.md`](MADDEN_OFFSEASON_WALKTHROUGH.md): Staff →
  Re-sign → FA prep → Free agency → Combine → Pro day → Draft → Recap/UDFA → preseason. Each is one Advance.

So a tuning season is a loop, not one long sim:

1. Pilot: load the clone, **advance exactly one stop** (`python3 -m tools.madden_pilot advance --save
   CAREER-TUNE-<run>`), let the autosave stand, quit.
2. Mu: `watch_save --once --dsn madden_tune_<run> --no-articles` (watcher stopped, Madden closed). The league
   office writes the stage's decisions into the save. While Madden27.exe is open, run it with
   `LEAGUE_OFFICE_FRANCHISE_CLOSED=1`.
3. Pilot: reload the written save and advance one more stop.
4. Repeat through the offseason; the regular season and playoffs can be simmed in one go up to the stop the
   pilot needs.

`advance` is the loop's step: one advance press from the hub (FORCE ADVANCE + SIM AHEAD, or the offseason
stage's own advance), the stop header read before and after, the autosave copied over the named save, then a
quit to the franchise list. It refuses anything but `CAREER-TUNE-*` and refuses while the watcher runs.

**The draft order is locked by the watcher.** The live franchise's Draft Room locks it by hand; with a
`madden_tune_*` destination the ingest calls `engines.draft_room.lock_order(cur, season + 1)` before the DRAFT
stage runs, so the league office has picks to write. Without it the run logs "no draft order … no picks
planned" and files nothing.

The whole season is one command (`engines/tuning/season_loop.py`, P2-05): clone, regular season, postseason,
ingest, the stage loop above one stop at a time through the eight offseason stops **and the preseason weeks and
cut day**, then a season report scored against the real-NFL targets. The loop ends when an advance lands on the
next regular-season week. The preseason hub header reads like a regular-season one (`2027. WEEK 1`), so the stop
comes from the save (`PRESEASON_<n>`) and the cut day is the week the league office detects as CUTDOWN.

```
.venv/bin/python3 -m engines.tuning.season_loop --run p2c
```

It writes `data/tuning/seasons/p2c/` (pilot run dirs, `stop_NN/` per offseason stop, `snapshot.json`,
`season_<year>.md|.json`, `run.json`). Use `--no-offseason` to stop after the postseason while the offseason
screens are still being catalogued.

Two places still belong to Madden inside that loop: the free-agency-week and UDFA live events must be skipped, and
the draft screen only advances by simming the draft. The pilot sims the draft **after** Mu has written the picks,
and the run must read the save back to confirm Madden kept them. The one exception to the loop is P4's supersim
measurement, which only needs `--to regular-season-end` and never touches the offseason.

**Week 1 has one more Madden gate:** the Weekly Hub's draft-class card ("Which rookies will we see in April's
draft?") must be delegated before the week can advance. The pilot highlights the card with the arrow keys (it is
not always selected on arrival; the keys wrap) and holds **Q** (the footer's HOLD TO DELEGATE). The catalog models
this as `HUB_WEEKLY_DRAFT_CARD`, the only screen on which Q is allowed; Space is still never pressed there.

**Every weekly advance is Force Advance, not Next Week.** With all 32 teams user-controlled, READY TO ADVANCE
cannot run the week. ADVANCE WEEK opens READY TO ADVANCE / FORCE ADVANCE (plus JUMP AHEAD / SIM TO MIDSEASON); the
pilot presses Down once and confirms FORCE ADVANCE. `ADVANCE_MENU` anchors on READY TO ADVANCE so the overlay is
never mistaken for the Gameday tab behind it. FORCE ADVANCE then asks ATTENTION / "You have selected to skip
ahead multiple weeks" — the pilot presses Up to **SIM AHEAD** and confirms (`FORCE_ADVANCE_CONFIRM`).

## 7. Rules

- Never simulate `CAREER-MUBASE` or `CAREER-MU2026`.
- Stop `pipeline.watch_save` before any pilot sim; never kill it yourself.
- A tuning clone only ever enters a `madden_tune_*` database.
- Every tuning ingest runs with `--no-articles` and `MU_NO_LLM=1` (set by the watcher for a tuning `--dsn`).
- Tuning databases are disposable; deleting one is normal. Deleting a `CAREER-MU*` save is not.
