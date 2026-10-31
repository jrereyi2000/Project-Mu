# Project Mu: PC Migration & Native Save Architecture Plan

**Status: done, closed 2026-10-30.** The PC migration is complete: Windows 11 + WSL2 Ubuntu, Docker Postgres (`madden-db`), the native save reader/writer and loader, one database per Madden franchise with a safe `--auto-init`, the clean seed, and the mods staged and verified (§3, §5). No migration work remains. What is left of the old plan moved to the plans that own it:

| Was | Now |
| :--- | :--- |
| §1 background, §2 Mac/PS5 prep, §4 2037 teleporter (obsolete since 2026-10-29) | [`PC_MIGRATION_ARCHIVE.md`](./PC_MIGRATION_ARCHIVE.md), numbering kept |
| §6 CFB 27 → Madden bridge | [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §9 |
| §7 local models, GPU budget beside Madden, nightly GPU queue (§7.x → §10.x) | [`V3_STUDIO_PLAN.md`](../in_progress/V3_STUDIO_PLAN.md) §10 |
| §8 hardware and the Costco/SFF timeline | [`docs/reference/PC_HARDWARE.md`](../reference/PC_HARDWARE.md) |
| §9.1–§9.3 live color layer and game-day audio | [`V3_STUDIO_PLAN.md`](../in_progress/V3_STUDIO_PLAN.md) §6 |
| §9.4 headshots as in-game portraits, and the one-portrait spike | [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §10 |
| §10 weekly mod-pack trigger | [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §7.1 Phase 4 |
| §10 decouple and prune obsolete code | Not part of any pipeline version: the audit plan after V3 ([`AUDIT_LEDGER.md`](../../AUDIT_LEDGER.md)) |
| Re-running §5.4 on a modded save | [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §7.0 P5 |

**Franchise start (JR, 2026-10-30):** the Madden franchise starts in **2026** with the mods applied, and Project Mu runs it as a fully CPU league, with no user team, through 2033; JR takes over a team in 2034. This replaces the earlier plan to move a fresh franchise to 2034 with `seasonYearSetup.exe`. It waits for V2 and is V2's final milestone ([`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §7.2); coverage for the CPU years is a V3 batch job. Project Mu's own 2026–2036 history stays discarded (inflated stats); the old universe is the read-only `madden_2036` database.

Section numbers are kept so the many references to `PC_MIGRATION_PLAN.md §n` in code and docs still resolve: §3 and §5 are below, the rest point to their new homes.

---

## 1. Background

Moved to the [archive](./PC_MIGRATION_ARCHIVE.md#1-executive-summary--core-objectives). In short: Madden 27 moved from a PS5 read by screen capture and OCR (`madden_capture.py`, `extract.py`) to a PC whose franchise files Project Mu reads and writes directly ([`madden-franchise`](https://github.com/bep713/madden-franchise)).

## 2. Phase 0: Pre-Arrival Preparation (Mac) — obsolete

Moved to the [archive](./PC_MIGRATION_ARCHIVE.md#2-phase-0-pre-arrival-preparation-mac) (§2.1–§2.5).

---

## 3. Phase 1: Day 1 PC Setup

```
[Unbox PC] ──> [Connect HDMI to TV & Power to Wall]
                     │
                     ▼
             [Windows 11 Setup]
             • Disable sleep mode / hibernation
             • Set power plan to "High Performance"
                     │
                     ▼
             [Living-Room Ergonomics]
             • MSI Center: Turn OFF all RGB fan lights
             • NVIDIA Control Panel: Cap frame rate to 120 FPS
                     │
                     ▼
             [Windows side: games & save tooling]
             • EA app / Steam: Madden NFL 27, College Football 27
             • Node.js 20+ LTS (runs `madden-franchise`)
             • Ollama for Windows (uses the RTX 5070 Ti directly)
             • MMC Modding Tools (Frosty-based M27 mod editor/manager; re-download
               after each title update for the new SDK/cache)
             • MyFranchise Tool (M27 v2.0.1+) and Madden Franchise Editor with the
               M27 schema, to inspect and hand-fix teleporter writes
             • Mods: M27 Unlocked, Sabos Franchise Mod (sim stats, injuries, XP off),
               others as wanted (docs/MMC_MOD_SURVEY.md); install and load
               order in docs/MOD_INSTALL.md
                     │
                     ▼
             [WSL2 side: Project Mu]
             • `wsl --install` (Ubuntu) from an admin PowerShell
             • Docker Desktop with the WSL2 backend (Postgres `madden-db` container)
             • In Ubuntu: git, Python 3.12+, `git clone <repo>` into the Linux
               home folder (not /mnt/c — file I/O there is slow)
             • Copy `.env` to the repo root
             • `./db/restore_db.sh db/backups/madden_backup_*.sql`
             • `python -m uvicorn server.api:app --reload`
             • Verify `http://localhost:8000` loads `Mu_Network` from a Windows browser
                     │
                     ▼
             [Controller & Remote Access]
             • Pair PS5 DualSense via Bluetooth (PS + Share buttons)
             • Parsec (Mac control from the couch)
```

**As set up (2026-10-29):** WSL2 distro `Ubuntu` (Ubuntu 26.04, Python 3.14, Node 22 from apt), Docker Desktop 29.9 with Ubuntu integration, repo at `~/Project-Mu` with a `.venv`, Postgres 14.24 in `madden-db` (command in `PROJECT_SPEC.md` §1.2). Git in Ubuntu pushes through the Windows Git Credential Manager (`/mnt/c/Program Files/Git/ucrt64/bin/git-credential-manager.exe`). Ollama for Windows is installed with `gemma4:12b` pulled (Q4_K_M, 8 GB); WSL2 reaches it at `localhost:11434` through mirrored networking. `.env`, `CAREER-GIANTS-AUTOSAVE`, `data/commentary_m27.json`, `data/commentary_save_m27.json` and the headshots are in place. `backup_latest.sql` (2036 week 21, 3,903 players, 154 MB) restored without errors. On 2026-10-29 that database was backed up (`db/backups/madden_backup_20261029_pre_rename.sql`) and renamed `madden` → **`madden_2036`**, the read-only archive of the old universe and the quality-test inputs (§7.4); there is no `madden` database until the watcher creates the new franchise's. Every migration through `2027_01_12` is applied to `madden_2036` and `madden_test` (`2027_01_05_madden_pgid` is no longer held: `pipeline/load_save.py` needs it), and `db/init_db.sql` builds the same schema. Tests on the PC: save reader 31/31; pytest 1,775 passed, the only failures the macOS-only Apple Vision / `Quartz` code. Earlier PC-only fixes: `rembg[cpu]` added to `requirements.txt`, and the `#debug` job-stop fix (Ubuntu's `sh` is dash and exits on Ctrl-C before writing the job's exit code; `trap : INT` in `server/debug_api.py`). The OneDrive backup of Documents is turned off (2026-10-29), so Madden's saves are back in `C:\Users\jrere\Documents\Madden NFL 27\saves\` and `.env` sets `MADDEN_SAVES_DIR=/mnt/c/Users/jrere/Documents/Madden NFL 27/saves/`. A first, unmodded test franchise (2026, franchise year 0) left `CAREER-WEEK1`–`CAREER-WEEK3` there (regular-season weeks 1–3, all games simmed) for the §5.4 checks. `C:\Users\jrere\.wslconfig`: `memory=12GB`, `networkingMode=mirrored`. DualSense paired; Parsec replaces Remote Desktop for couch control. Mods (2026-10-29): MMC Modding Tools v1.1.0.7 and all 71 `.fbmod`s from `PC_MODS/` (gitignored) are staged in `C:\Madden27Modding` and pre-imported into the Mod Manager; load order and the groups that must be toggled one at a time (CBS vs FOX broadcast sets, crowd tiers, ball, FPS menus, autoplaycall vs Sabos Gameplay) are in [`MOD_INSTALL.md`](../MOD_INSTALL.md). The anti-cheat launcher is swapped, and the mod install is done (2026-10-29): JR loaded into a game with the full `Default` pack and no breakages, after Hair Fix and Faces (made for an older game build) was found crashing game loads and moved to `Broken\`. Per-game packs (broadcast window, crowd tier, October BCA) are built from the `Mu Base` pack by `pipeline/mod_packs.py`. The roster-size mod (`M27-Pre80Cut65SalaryCapADD40Pr3yVer1-1.fbmod`) is chosen and has to be in before the real franchise is created. Everything listed as still to do here was finished or moved when this plan closed on 2026-10-30 (status table at the top).

**Why not native Linux:** Madden uses EA Javelin anticheat, which runs at the Windows kernel level and does not work under Linux/Proton ([GamingOnLinux tracker](https://www.gamingonlinux.com/anticheat/vendor/11)); MMC Modding Tools and the community franchise tools are Windows-only too. WSL2 gives a real Ubuntu terminal (same `ls`, `cd`, `grep`, `git`, bash as the macOS terminal) next to Windows, so Project Mu's `.sh` scripts and Python code run unchanged.

**Crossing the boundary:** WSL2 reads the Windows save folder at `/mnt/c/Users/<Name>/Documents/Madden NFL 27/saves/`, or `/mnt/c/Users/<Name>/OneDrive/Documents/...` when Documents is redirected to OneDrive, as on JR's PC (offline franchise files, per the MMC tool instructions). Ollama on Windows is reachable from WSL2 at the Windows host address (or `localhost` with WSL mirrored networking). `pipeline/notify.py` uses `ntfy` over HTTP and works unchanged on WSL2. Mac-specific capture pieces (`chiaki-ng`, the screen capture macros) are not needed on the PC because saves are read directly from disk.

---

## 4. Phase 2: The 2037 Offseason Teleporter — obsolete

Moved to the [archive](./PC_MIGRATION_ARCHIVE.md) (§4.1–§4.5, numbering kept).

---

## 5. Phase 3: The Direct Save Loader

Once the league runs on PC, screen capture (`madden_capture.py`, `extract.py`, the capture steps in `pipeline/weekly_capture.py`) is retired.

### 5.1 Architecture

```
[Madden Saves Franchise to Disk] (C:\Users\<Name>\Documents\Madden NFL 27\saves\CAREER-*)
                   │
                   ▼
     [pipeline/save_reader/pc_save_reader.mjs] (Node.js + `madden-franchise`; built, v1)
     • Reads `Player`, `Contract`, `SeasonGame` and the tables that reference it
       (game stats, scoring plays), season/career stat tables, `Team`, `DraftPick`,
       transactions
     • Writes one JSON snapshot per run, keyed by PGID, to `captures/<run>/save.json`
                   │
                   ▼
     [pipeline/load_save.py] (Python, WSL2)
     • Matches players on `players.madden_pgid` (no name matching, no OCR checks)
     • Reuses the existing database writes: `pipeline/contracts.py`
       (`supersede_active`, `insert_contract`, `upsert_team_cap`), the transaction
       loader, the capture row, injuries, schedule/game results
     • Writes season stats, per-game lines (§5.3), scores, rosters, contracts in one
       PostgreSQL transaction
                   │
                   ▼
     [Existing engines & newsroom]
     • `engines/game_stat_generator.py` engine chain (gotw, game scripts, TWI, …)
     • Newsroom swarm and Agent Wire social desk
```

**Reader status (2026-10-27):** `pipeline/save_reader/pc_save_reader.mjs` is built and tested against `CAREER-GIANTS-AUTOSAVE`: 2,990 players, 324 games and 35 teams in ~1.2 s, 6,019 per-game stat lines, all resolving to an exported game. Per-game lines are reached through `Player.GameStats` (an array of references into the `Game*Stats` tables, each row pointing at its `SeasonGame`), season lines through `Player.SeasonStats`, contracts through `Player.Contract` → `PlayerContract`. It writes JSON to a path given with `--out` (the loader will choose `captures/<run>/save.json`).

**Writer status (2026-10-27):** `pipeline/save_reader/save_writer.mjs` sets Player fields by pgid on a copy and saves it to a new file, never the input. Tested on the sample save: name, speed, commentary ID, portrait ID, dev trait, archetype and IR flag on one player read back as written, while the season, teams, all 324 games and the other 2,989 players read back identical. The library doesn't recompute OVR and doesn't check numeric ranges or string lengths, so the writer validates every value against the schema first and refuses the whole edit set on one bad value. Still for the PC: whether Madden loads an edited file, recomputes OVR, and accepts names that differ from `PLYR_ASSETNAME`.

**Loader status (2026-10-27):** `pipeline/load_save.py` is built (`db/migrations/2027_01_05_madden_pgid.sql` adds `players.madden_pgid`). A dry run of the sample save into `madden_test` as season 2099 loads 2,582 players, 272 games (79 finished), 10,374 per-game line rows for the 64 games with lines, 158 team score rows and 3,092 season rows, then rolls back. Every player's season pass, rush and receiving yards equal the sum of their game lines, and every finished game's quarters add up to its score. It also loads contracts (1,694; Madden money is in $10,000 units and a year's cap hit is `SalaryYear` + `BonusYear`), the injury report (35), scoring plays as `game_highlights` for the 31 games Madden still keeps them for (they add up to every final), `league_state.current_week`, and a `captures` row on `--commit`. Madden 27's `College_Impact`/`College_Star`/`College_Elite` are the same stored values as `Star`/`Superstar`/`XFactor` (schema), so dev traits need no PC check. To confirm on the PC: LOLB/ROLB → WILL/SAM, `DEFTACKLES` = solo tackles, sacks = `DLINESACKS` + ½ `DLINEHALFSACK`, and first-year contracts: 660 of them (mostly year 0: new signings and rookies) disagree with `PLYR_CAPSALARY` (285 show 0, the rest look prorated), so check one against the Team Salary screen. Still to add: `team_cap_seasons` (the save's team cap fields aren't in a confirmed unit). Built: the watcher (`pipeline/watch_save.py`, §5.2).

**Why a new loader instead of CSVs for `pipeline/load_capture.py`:** `load_capture.py` is built around OCR output — the `36 CAP` year strip, row-count margins, fuzzy one-letter name matching, unmatched-row crop prompts. Faking CSVs means reproducing those quirks, and none of those checks mean anything with exact IDs. A thin loader that reuses the database-write helpers is less code. `load_capture.py` stays for reloading old captures.

### 5.2 Debounced File Watcher (`pipeline/watch_save.py`)
**Built and tested (2026-10-28); one database per franchise since 2026-10-29.** A Python daemon running on the PC/WSL2 (full description: `PROJECT_SPEC.md` §6.0b):
* **Watches** the `CAREER-*` save folder (`--saves-dir`, path argument, or `MADDEN_SAVES_DIR` in `.env`, `/mnt/c/Users/jrere/Documents/Madden NFL 27/saves/` on JR's PC). Saves from several franchises can share the folder; a backlog is processed oldest first.
* **Debounces** write bursts: acts only when `mtime` and `size` have been stable for `--debounce` seconds (default 10s) and `size > 0`. Tracks processed modification times per file so each state is executed once.
* **Routes by franchise:** `pc_save_reader.mjs` (reader v3) reports the save's `Franchise.LeagueID`, which Madden picks at random for each franchise and keeps in every save slot and week (CAREER-WEEK1–3: -1851690351 throughout; CAREER-GIANTS-AUTOSAVE: 2027975635). `db/franchises.py` maps it to that franchise's own database, `madden_<name>`, in the registry `data/franchises.json`, which also names the active franchise the site serves.
* **Sets up a new franchise only with `--auto-init`:** creates `madden_<--franchise-name>`, seeds it with `db/seed_clean_db.py` from the save's season and week and `--user-team`, and stamps `league_state.madden_league_id`. A database that has data is never seeded; a mismatched stamp stops the load; `--reseed` (with `--once`) drops and recreates one franchise's database and needs `--confirm-reseed <db>`. The old rule that treated a jump of more than a season as a "different franchise" and reseeded the same database is gone.
* **Compares** the save's season and week with that franchise's `league_state`:
  * *Same week:* no-op, temporary snapshot folder removed.
  * *Older week / season gap:* sends an ntfy notification and no-ops.
  * *New week:* runs `load_save.py` (`--commit`), then deterministic analytics engines (`weekly_capture.run_weekly_engines`), queues nightly jobs (V3 §10.5), and publishes newsroom articles (`newsroom.weekly_run.run_weekly_swarm`), all in a child process with `DATABASE_URL` set to the franchise's database.
* **Next week's mod pack (to build, JR 2026-10-29):** after a new week loads, the weekly run should also set up the mod pack for the user team's next game: `pipeline.mod_packs` `write --team <user team>` for the new `league_state` week (broadcast window, crowd tier, October BCA; [`MOD_INSTALL.md`](../MOD_INSTALL.md) §4), so `-launch` or the Mod Manager's dropdown has it ready before JR plays. Open points: the user team comes from the franchise registry's `user_teams` (several teams means several packs, named per combination as now); `write` refuses while `MMCModManager.exe` is open, so the step retries on the next save or leaves a notification rather than failing the week; a bye week or the offseason writes nothing; and it runs after the engines, since the crowd tier reads that week's records and popularity.
* **Locking & Error Handling:** Prevents concurrent runs via `captures/.weekly_run.lock` (shared with `weekly_capture.py`). Failed runs trigger high-priority phone notifications via `pipeline.notify`.
* **Flags:** `--once`, `--dry-run` (load without commit, skips engines/articles, never creates or seeds a database), `--no-articles`, `--no-engines` (load only), `--season N`, `--force`, `--auto-init`, `--franchise-name`, `--user-team`, `--reseed` + `--confirm-reseed`, `--dsn` (load into one database, no routing).
* **Testing:** `tests/test_watch_save.py` (debounce, week classification, run lock, franchise routing with a fake Postgres, the auto-init/reseed guards, the ingest child, the registry and its CLI, and an end-to-end dry run against `CAREER-GIANTS-AUTOSAVE` into `madden_test`). Real `--once` runs on 2026-10-29: CAREER-WEEK1 created and seeded a throwaway franchise database and loaded week 1 (2,853 players), WEEK2 and WEEK3 loaded weeks 2 and 3 into the same database, a second WEEK2 read was an older-week no-op, CAREER-GIANTS-AUTOSAVE got its own database, and the daemon over a folder holding both franchises' saves routed each to its own database. The throwaway databases were dropped afterwards.

### 5.3 Game-Level Data

**Checked on a real save (2026-10-27).** `CAREER-GIANTS-AUTOSAVE`, a Madden 27 franchise in 2027 (franchise year 1), current week index 4. The save reports schema 987; `madden-franchise` 4.4.1 falls back to its bundled 620, and the tables below decode cleanly with it.
* **Per-game stats exist for CPU-simmed games:** `GameOffensiveStats`, `GameDefensiveStats`, `GameKickingStats`, `GameOLineStats` and both return tables have a row per player per game for all 16 games of each of the first four regular-season weeks, simmed and user-played alike, linked to `SeasonGame`. Example: a QB line of 22/27, 264 yards, 3 TD, 65 downs played, game rating 79.
* **The latest played week had scores and scoring summaries but no per-game stat rows yet.** Confirmed on the PC (§5.4): they appear in the next week's save.
* **Retention:** the stat tables' capacities (7,700 offense rows, 13,500 defense rows) are below a full season at the observed ~460 and ~915 rows a week, so older weeks are probably purged. Scoring summaries were kept only for the last two weeks, plus a few user-played games from the previous season. The loader must read every week. On the PC's unmodded 2026 franchise a week was ~350 offense, ~700 defense, ~260 O-line and 64 kicking rows, about 19–23 weeks of capacity per table (§5.4).
* **Team box scores:** `SeasonGame.HomeTeamStatCache` / `AwayTeamStatCache` → a `TeamStats` row per side (first downs, offensive/total yards, rushing and passing yards and attempts, third/fourth downs, red zone, penalties, possession time, giveaways/takeaways, sacks). Found on the PC (§5.4); filled for every played game, the latest week included. The reader exports it and `load_save.py` writes it to `team_stats`.
* **Scoring summaries:** `SeasonGame.ScoringSummaries` → `ScoringSummary[]` → `ScoringSummary` rows with `Quarter`, `TimeStampInSec` (time left in the quarter), score before and after, and `Conversion`.
* **Quarter scores, weather and time of day are filled in** on played games.
* **Empty in this save:** `Highlight` (0 rows; likely only for games played by hand, none recent), `Injury` (0), `MorphHead` (0 rows; player heads use `GenericHeadAssetName` / `PLYR_GENERICHEAD`, not face sliders).
* **References:** a reference field is 32 bits, the first 15 the table ID and the last 17 the row index.

**What the Madden 27 schema defines.** `madden-franchise` 4.4.1 bundles the Madden 27 schema (`data/schemas/27/M27_620_0.gz`, 3,655 tables). A table in the schema is not proof the save fills it for CPU-simmed games; §5.4 checks that.

| Table | Key fields | Project Mu use |
| :--- | :--- | :--- |
| `SeasonGame` | `HomeScore`/`AwayScore`, `HomeScoreQuarter1–4`/`AwayScoreQuarter1–4`, `HomeScoreOT`/`AwayScoreOT`, `IsOvertimeGame`, `ScoringSummaries`, `Home/AwayPlayerStatCache`, `Home/AwayTeamStatCache`, `InjuryCache`, `IsSimmed`, `IsGameOfTheWeek`, weather (`Temperature`, `Weather`, `Wind`, `WindSpeed`, `Precipitation`, `CloudCover`), `Stadium`, `TimeOfDay`, holiday flags, uniforms, `SeasonWeek`, `SeasonYear` | `games` quarter scores; scoring summaries for lead changes and comebacks (Game Excitement Index, game scripts); conditions for previews and recaps |
| `GameOffensiveStats`, `GameDefensiveStats`, `GameKickingStats`, `GameOLineStats`, `GameOffensiveKPReturnStats`, `GameDefensiveKPReturnStats` (base `GameStats`, linked to `SeasonGame` and `OpposingTeam`) | full offense/defense/kicking/return lines plus `RUSHBROKENTACKLES`, `RUSHYARDSAFTER1STHIT`, `RECEIVEDROPS`, `RECEIVEYARDSAFTER`, `BIGHITS`, `CTHALLOWED`, `OLINEPANCAKES`, `OLINESACKSALLOWED`, and `DOWNSPLAYED`, `GAMERATING`, `GAMESSTARTED` | `player_stats` and `game_stat_*` directly; downs played gives snap share |
| `Highlight` | `PlayDescription`, `Quarter`, `Time`, `GreatnessScore`, `Type`, `Team`, scores, `Filename`, `IsUserHome` | play descriptions for recaps; likely only games JR plays (user fields, replay filename) |
| `NewsCenterArticle` / `NewsCenterArticleData` | header text, author, people and team referenced, week, year, breaking flag | Madden's own news events as story prompts |
| `Injury` | `Type`, `Severity`, `MinDuration`, `MaxDuration` | injury details and expected return |
| `PlayerStatRecord` | stat type, value, season, player, team | league records cross-check (§2.9 of the spec) |
| `HistoryEntry` | progression, XP, dev level, morale, scheme fit per week | player development stories |
| Season and career stat tables | `SeasonOffensiveStats` … `CareerOLineStats` with split fields (`DEEPPASS_YARDS`, `INSIDERUN_YARDS`, `BLITZ_YARDS`, …) | season totals and career history |

**How the loader uses it.**
* If Madden 27 has per-game player and team stat rows, the loader writes them directly as each game's box score.
* The week-over-week season-stat diff is the cross-check, and the fallback if per-game rows are missing or purged: each team plays at most one game a week, so a player's game line is the change in their season totals since the previous week's read, credited to the team they played for in that game.
* If a week's read is missed and there are no per-game rows, that week and the next are a two-game delta; `engines/game_stat_generator.py` spreads those as it does today.
* How long Madden keeps per-game rows is unknown (it may clear them at season rollover or keep only recent weeks). Reading the save every week covers either case.
* Playoff games: verify on the first PC postseason that they get game rows and their own stat rows.

### 5.4 First Checks on the PC
Read a fresh Madden 27 save and confirm (done 2026-10-29 on an unmodded test franchise; repeat on the modded 2026 franchise before its CPU era, V2 §7.0 P5):
1. `madden-franchise` opens it and lists the tables above.
2. PGIDs are stable across saves and across Advance Week.
3. That CPU-simmed games actually fill the `Game*Stats` tables, `ScoringSummaries` and the quarter-score fields, and that a sample game's rows match the in-game box score.
4. Whether `Highlight` rows exist for simmed games or only for games JR plays.
5. How many weeks of per-game rows survive: sim several weeks, then past a season rollover.
6. Season stat rows update after each simmed week, and the diff matches the per-game rows.
7. Whether Madden recomputes OVR on load (§4.4).
8. ~~Whether Madden draws the edited body skin tone in-game (§4.3, `CharacterVisuals.RawData`).~~ Obsolete: only the teleport needed it, and the fresh-save plan drops the teleport.

**Results (2026-10-29), on `CAREER-WEEK1/2/3`.** These are a throwaway, unmodded 2026 franchise (franchise year 0, user team CAR) with Regular Season weeks 1–3, every game simmed. Each save was made with the week's 15 CPU games already simmed and CAR's game still `Unplayed`; that game is simmed by the next save. Played-game counts of 63/79/95 are the 48 preseason games plus 15, 31 and 47 regular-season games. **Re-run every check on the modded 2026 franchise before its CPU era:** sim-stat and roster mods change the numbers, and items 5 and 7 need more than three weeks.
1. **Opens: yes.** All three report schema 988 and decode with `madden-franchise`'s bundled 620. Every §5.3 table is present: `SeasonGame` 335 rows, `Game*Stats`, `ScoringSummary`, `Highlight`, `NewsCenterArticle` (17/39/45 rows), `Injury` (8–10 rows), `PlayerStatRecord`, `HistoryEntry` (24k→51k rows) and `MorphHead` (0). `Highlight` has no schema entry and falls back to a generic one.
2. **PGIDs: stable.** 3,244 players in each save, with the same PGIDs, names and Player rows from week to week, and no duplicates. Between saves 66 and then 52 players changed team or status (free-agent signings and cuts), and 108 and then 101 changed OVR (in-season progression).
3. **Simmed games: filled.**
   - Quarter scores add up to the final for every played game.
   - Scoring summaries exist for the latest two regular-season weeks, never for preseason.
   - Per-game `Game*Stats` rows lag one save, for every regular-season week. The rows for the latest week already exist in the tables (338 offense rows in WEEK2), but with a null `SeasonGame` and no `Player.GameStats` link, so they can't be credited until the next save links them.
   - Preseason games get no per-game rows.
   - The team box score (§5.3) is present for every played game, including the latest week and preseason. Its rushing yards and attempts and its passing attempts and TDs equal the summed player lines for all 64 team-games checked. Its passing yards are the passers' gross yards.
   - Still needs JR: compare one game with the in-game box score (see below).
4. **Highlight: none.** 0 of 3,200 rows in all three saves, so the table isn't filled for simmed games. Whether it fills for a game JR plays is still open.
5. **Retention: at least two weeks** (WEEK3 still has week 1 and week 2 rows). Capacity works out to roughly 19 weeks for defense and 21–23 for the other tables, so a full regular season may only just fit, and playoffs may not. Season rollover can't be tested yet.
6. **Season rows: match.** Season rows lag exactly like the game rows: a save's season totals cover the weeks whose game rows it links. The change in season totals between saves equals the newly linked week's game rows:
   - 1,420 of 1,420 player rows for week 1.
   - 1,426 of 1,443 for week 2. The other 17 are half sacks: a season row folds two `DLINEHALFSACK` into one `DLINESACKS`, and the loader's sack formula already handles that.
   - `*LONGEST` fields are the running maximum (2,143 of 2,143).
   - `GAMERATING` is 0 in season rows.
   - A player's season row can move from `SeasonOffensiveStats` to `SeasonOffensiveKPReturnStats` once he returns a kick. The loader reads fields, not tables, so this doesn't affect it.
   - The 248 season rows already filled in WEEK1 have `SEAS_YEAR` = −1 (prior-year history) and are skipped.
7. **OVR recompute on load: not tested.** It needs an edited save loaded in-game. Each save's stored OVR is the highest archetype grade (the reader test checks this). Low priority now that the teleport is dropped; it matters only for V2.14.

**Loader run.** All three snapshots were committed in order into a scratch database (`init_db.sql` plus `seed_clean_db --season 2026 --week 1`), which was dropped afterwards.
- `league_state` moved 1 → 2 → 3.
- 47 games were completed.
- Game lines: 0, then 2,362, then 4,743 rows.
- Season totals match the summed game lines for every player.
- 2,853 players were loaded (draft prospects are skipped).
- Injuries: 252 / 227 / 216 rows, of which 217 / 194 / 170 are on IR.

Fixed in the loader:
- `team_stats` yards, first downs, conversions, turnovers, red zone, penalties and possession were left at 0 or NULL. They now come from the team box score (reader_version 3).
- A healed player Madden still has on IR was stored with injury type `INVALID_`. It is now NULL.

**Checked in-game by JR (2026-10-29, CAREER-WEEK1–3):**
- **Tackles.** Madden's box-score tackle column is the total: `DEFTACKLES` is solo tackles and `ASSDEFTACKLES` assists (Week 1, CHI 27 @ CAR 24: Bobby Okereke 7 solo + 4 assists = 11), which is what the loader already stored. Half sacks were not checked separately.
- **Team stats.** Every team-box-score number in that game matched (Bears 360 total, 289 passing, 71 rushing, 21 first downs, 5/12 on third down, 29/38 passing; Panthers 383, 221, 162, 18, 4/9, 23/30), confirming the reader_version 3 team box score.
- **LOLB/ROLB.** Madden's Panthers depth chart lists Devin Lloyd, Tyrel Dodson and Bam Martin-Scott (all ROLB in the save) as WILL. The loader now maps ROLB → WILL and so LOLB → SAM (MLB is MIKE).
- **Team Salary screen.** It shows the contract's own hit (SalaryYear + BonusYear) for the first-year mismatches (Jerome Baker $2.51M, Deonte Banks $2.60M, Joey Bosa $11.33M, Channing Tindall $1.70M), not `PLYR_CAPSALARY`. The loader already stores that number; `contracts_cap_hit_differs` is only a count.

The community *Madden Franchise Exporter* (a plain-text dump of a franchise file's career and season stats, team stats, game logs and rosters) is an independent check for items 3, 5 and 6 and for `pipeline/load_save.py` output.

---

## 6. CFB 27 → Madden Dynasty Bridge — moved

Now [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §9.

## 7. Local LLM for Short Tasks — moved

Now [`V3_STUDIO_PLAN.md`](../in_progress/V3_STUDIO_PLAN.md) §10; subsection §7.n is §10.n there (§7.3 workload split, §7.4 quality test, §7.5 sharing the GPU with Madden, §7.7 local headshots, §7.8 nightly GPU queue, §7.9 thinking budget).

## 8. Hardware and the SFF Transition — moved

Now [`docs/reference/PC_HARDWARE.md`](../reference/PC_HARDWARE.md).

## 9. Broadcast & Portraits — moved

§9.1–§9.3 (live color layer, constraints) are in [`V3_STUDIO_PLAN.md`](../in_progress/V3_STUDIO_PLAN.md) §6; §9.4 (headshots as in-game portraits) is [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §10.

## 10. Closed

See the status table at the top.
