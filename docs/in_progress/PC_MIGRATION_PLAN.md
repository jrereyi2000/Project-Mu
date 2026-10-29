# Project Mu: PC Migration & Universe Teleportation Master Plan

**Status (2026-10-28):** Phase 0 (Mac prep, §2) done: all 11 pre-PC items complete (§2.4, [PRE_PC_WORK.md](../done/PRE_PC_WORK.md)). Phases 1–7 wait on the PC; the teleporter (§4.3) has dry runs only. Part 2 (V2) status is at its end.

**Target Execution Window:** Late October 2026 – Early 2027  
**Architecture Transition:** PS5 Remote Play screen capture (`madden_capture.py` + `extract.py` at the repo root, driven by `pipeline/weekly_capture.py`) $\longrightarrow$ native PC franchise-file I/O ([`madden-franchise`](https://github.com/bep713/madden-franchise), which lists full support for Madden 19–27 and CFB 27 PC Dynasty saves)  
**Primary Hardware:** AMD Ryzen 7 9700X, NVIDIA GeForce RTX 5070 Ti 16GB, 24GB–32GB DDR5, 1TB+ NVMe SSD  
**Operating System:** Windows 11 for Madden, CFB and mods; WSL2 (Ubuntu) for Project Mu  

Timings and counts below are estimates until measured on the PC.

**Part 1 (§1–§9)** is the migration. **Part 2 (V2.0–V2.16)** is the follow-on work the PC makes possible.

---

## 1. Executive Summary & Core Objectives

Project Mu has simulated and chronicled 11 seasons (2026–2036) of NFL universe history across a PostgreSQL database, 192+ AI journalist personas, and an interactive media network (`Mu_Network`). The platform is now limited by running Madden on a PS5 over `chiaki-ng` Remote Play:

| Metric / Capability | Current PS5 Remote Play Setup | Native PC Save-File Architecture |
| :--- | :--- | :--- |
| **Weekly Ingestion Latency** | ~25 to 30 minutes (140+ screenshots, menu macro scrolls) | Seconds (read the franchise file directly) |
| **Data Quality / OCR Errors** | Fragile (dropped `1`s, text fuzzing, manual `pipeline/resolve_player.py`) | Exact values, matched on Madden's player ID (PGID) |
| **Weekly Game Box Scores** | Spread across games by `engines/game_stat_generator.py` | Real per-game player and team stats, quarter scores and scoring plays if Madden 27 stores them as CFB 27 does; otherwise exact lines from week-over-week season-stat diffs (§5.3) |
| **Stat Depth** | What the Season Stats screens show | Adds the save's split fields (`DEEPPASS_YARDS`, `INSIDERUN_YARDS`, `RUSHYARDSAFTER1STHIT`, `BLITZ_YARDS`, …) |
| **CFB 27 Integration** | Synthetic college generation via `engines/scouting_events.py` | Direct Dynasty import (real college stats & custom draft classes) |
| **Two-Way Front Office** | Manual controller execution of AI GM trades | Direct save injection (trades written straight to the save file) |
| **LLM Usage** | ~500 Gemini OCR calls a day plus newsroom, social and GM calls | No OCR calls; a local model takes the short calls (social, memory, scouting, GM) once tested; articles stay on Gemini (§7) |

This document defines the plan to move the universe to PC at the **2037 offseason**, replace screen capture with a direct save loader, wire up CFB 27, and move the short LLM calls to a local model.

---

## 2. Phase 0: Pre-Arrival Preparation (Mac)

### 2.1 Finish 2036 on the PS5
Play the Conference Championships and the Super Bowl on the PS5 and run the normal capture + newsroom pipeline through the Week 23 slate, so 2036 concludes in Project Mu exactly as every earlier season did (season archive, awards, records).

### 2.2 Complete Database Snapshot
Dump the canonical database (all 11 seasons, players, contracts, historical stats, articles, awards) to an archival SQL file:
```bash
./db/backup_db.sh
```
*Verify that `db/backups/madden_backup_<timestamp>.sql` exists and has a plausible size.*

### 2.3 PS5 Attribute Scan (JR decision 2026-10-28)
Read every player's full set of ratings off the PS5 so the PC players carry their real attributes, not a donor's ratings scaled to the right OVR (§4.3). They then play as they did on the PS5, and their OVR follows from the same Madden 27 formula. This can be built and tested now; it doesn't wait for the PC.

* **Screen (mapped 2026-10-28, reference views in `captures/2026-10-28_widelist/`):** Manage Roster → Roster, the list `--only roster` already navigates (team through the held-L2 dropdown). The player card isn't needed.
  * **Columns:** NAME, CONV, POS, AGE, OVR, DEV, MOR, SAVINGS, PENALTY, then 54 ratings, left to right: SPD ACC AGI COD STR AWR CAR BCV BTK TRK SFA SPM JKM CTH CIT SPC SRR MRR DRR RLS JMP THP SAC MAC DAC RUN TUP BSK PAC PBK PBP PBF RBK RBP RBF LBK IBL PRC TAK POW BSH FMV PMV PUR MCV ZCV PRS RET KPW KAC LS STA TGH INJ. INJ is last (right does nothing past it). The chip at the top right of the list names the cursor's column in full (SPEED RATING, CHANGE OF DIRECTION, HIT POWER, …), which gives the header → save-field mapping.
  * **Moving across:** d-pad right moves a column cursor one column. The view starts scrolling once the cursor reaches the right edge (the 8th press, at SPD), then shifts one column (~190 px at 3024 px wide) per press. One view shows 11 columns, so a page of rows needs ~6 views.
  * **NAME is not pinned.** It scrolls off with the rest, so rows are joined across views by their position on the page. All of a page's views must be taken before moving down (sweep right on one page, down, sweep left on the next).
  * **Moving down:** keeps the horizontal position. The list shows 14 rows and scrolls one row per press once the highlight nears the bottom.
  * **Screen check:** once SAVINGS/PENALTY/MOR scroll off, `classify()` calls the view PLAYER_STATS. The attribute capture checks the L2 team chip and the column chip instead.
  * Not yet checked: whether the team dropdown includes free agents.
* **Capture (built 2026-10-28):** `python3 madden_capture.py --only attributes [--team-names PANTHERS]`. Per team it sweeps the whole width (6 views at cursor columns 0, 19, 30, 41, 52, 62), moves down 7 rows, sweeps back, and so on. The highlight settles on row 8, so every player is seen once not highlighted, and most twice. How far the list moved is read from the view itself; a short move gets one more press to tell a dropped press from the end, and the end gets one more sweep a row up. The chip is checked before every saved view. Panthers test: 53 players in 8 sweeps, ~5.5 minutes and 43 MB (grayscale list-area crops), so ~3 hours and ~1.4 GB for the league.
* **Reading: digit templates, not OCR (`pipeline/attribute_reader.py`).** All cells use one fixed font on a fixed grid. Apple Vision misread or skipped ~20% of cells whole-frame and did worse per cell. The templates (threshold, split into digits, nearest of 10 averaged digit images) read all 252 labelled cells, including 70 held out and 18 on the bold highlighted row. Lessons from the Panthers run: digits can touch (a run too wide for one digit is cut at its thinnest column); the highlight box's sides and edges reach into neighbouring cells; at the end of the list the rows sit ~16 px higher, so each view's rows are placed from its own text lines; short positions (C, RG, RT) need per-cell Apple Vision. On the Panthers every cell seen in two sweeps read the same (37 of 53 players have all 54 ratings read twice; rows 1–7 are seen once), and all 53 names match the database. No LLM tokens. Fallback: `extract.py` with Gemini, roughly 1,000 screenshots for the league (~$6–7 at 2026 prices).
* **Check:** every value is 0–99, the team's row count matches its roster, and the scanned ratings run through `engines/ovr_weights` (top archetype grade of the position) should give the shown OVR within 1 (a shown 99 accepts any prediction of 99 or more). Panthers: 49 of 53 pass. The four misses are the formula, not the reading: K, LS and one RT are off by 2–4, the small groups §4.4 lists as weak. **Full league (2026-10-28, `captures/2026-10-28_attributes/`):** 1,739 players read, 1,737 loaded into `player_attributes`; 1,562 pass the OVR check. Every non-specialist position passes almost to a player; the misses were QB (75 of 78), LS, K, P and FB. The QB misses were Long Snap alone, root-caused to an anomalous converted receiver in the sample save (Carter Landry, pgid 24437) who distorted NNLS with LongSnap² weight ~1.5. Following the QB refit (§2.4, item 1), all 78 of 78 (100.0%) scanned QBs in `player_attributes` predict within ±1 (73 exact / 93.6%, error range −1 to +1), while sample-save cross-validation reaches 1.000 across all 4 QB archetypes.
* **Mapping:** each column header (SPD, STR, AGI, …) maps to a Madden 27 `Player.<Name>Rating` field. A rating in the save that the list doesn't show keeps the donor's value.
* **Storage (built 2026-10-28):** `player_attributes` (`db/migrations/2027_01_06_player_attributes.sql`): one row per player per scan, ratings as JSONB keyed by save field name, plus the shown and predicted OVR. `python3 -m pipeline.load_attributes RUN [--commit]` matches each row to the team's player_seasons by list name and position (one OCR name slip allowed); the Panthers test matched 53 of 53 by name in a `madden_test` dry run.
* **Timing:** the full-league scan has run (above). Run the final scan as late as possible on the PS5. Madden applies progression in the offseason, so if the PS5 franchise is advanced past progression into the 2037 offseason, the scan matches the teleport's starting point (§4.1). Otherwise, scan after the Super Bowl and expect PC ratings to be end-of-2036.
* **Not covered:** draft prospects (their ratings sit behind scouting) and anyone added after the final scan. They keep the donor-plus-OVR route.

### 2.4 Pre-PC Work List (JR, 2026-10-28)

Work that needs neither the PC nor a real 2037 save. Each item has a brief in [`PRE_PC_WORK.md`](../done/PRE_PC_WORK.md) (goal, what exists, steps, when it's done) that an agent can be pointed at.

- [x] 1. [QB OVR refit](../done/PRE_PC_WORK.md#1-qb-ovr-refit): tame Long Snap in the QB formulas, checked against the 78 scanned QBs (§2.3, §4.4)
- [x] 2. [Surname rename tool](../done/PRE_PC_WORK.md#2-surname-rename-tool-commentary-ids): Madden 27 commentary lookup, IDs for exact matches, a ranked rename list for the ~165 unmatched for JR to review (§4.3)
- [x] 3. [Body skin tone](../done/PRE_PC_WORK.md#3-body-skin-tone-charactervisualsrawdata): decode and write `skinTone` in `CharacterVisuals.RawData` so the body matches the head (§4.3 Appearance)
- [x] 4. [`watch_save.py`](../done/PRE_PC_WORK.md#4-pipelinewatch_savepy): debounced save watcher that runs reader → loader → engines → newsroom on a new week (§5.2)
- [x] 5. [Thinking caps](../done/PRE_PC_WORK.md#5-thinking-caps-on-the-short-gemini-calls) on `agent_memory`, `scouting_notes` and the GM calls (§7.9)
- [x] 6. [Gemini-or-local provider switch](../done/PRE_PC_WORK.md#6-gemini-or-local-provider-switch) on each short-task caller, local runs logged to `llm_usage` (§7.3)
- [x] 7. [Quality test harness](../done/PRE_PC_WORK.md#7-quality-test-harness) (`tools/llm_compare.py`, §7.4)
- [x] 8. [Fold `OPEN_MODEL_RESEARCH.md` into §7](../done/PRE_PC_WORK.md#8-fold-open_model_researchmd-into-plan-7) (§7.2, §7.3, §7.5)
- [x] 9. [League memory](../done/PRE_PC_WORK.md#9-league-memory-v28) (V2.8)
- [x] 10. [Personality plan phases 2 and 3](../done/PRE_PC_WORK.md#10-personality-plan-phases-2-and-3-v213): catalogue and tables, then GMs (V2.13) [DONE 2026-10-28]
- [x] 11. [Progression plan phase C](../done/PRE_PC_WORK.md#11-progression-plan-phase-c-v214): `engines/progression.py` offline, backtested (V2.14)

On hold: applying `madden_pgid` to the live database (after the PC move); the CFB 27 bridge (§6, until a dynasty is planned); the pregame show (§9.1); the rest of Part 2. The full Project Mu draft order for `PickNumber` (§4.3) comes in once the Super Bowl is final.

**Round 2 (audit 2026-10-28):** what's left that doesn't need the PC, sorted into standalone pieces (buildable off-repo on another device), repo + sample save work, database work and Mac/PS5-only steps: [`PRE_PC_WORK_2.md`](../not_started/PRE_PC_WORK_2.md).

### 2.5 Try Local LLM Inference on the Mac (optional)
1. Install Ollama: `brew install ollama` (or download from [ollama.com](https://ollama.com)).
2. Pull a mid-size instruct model and send it an Agent Wire prompt through `http://localhost:11434/api/generate` to get a feel for output quality before the PC arrives.

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
               others as wanted (docs/MMC_MOD_SURVEY.md)
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
             • Enable Windows Remote Desktop (Mac control from the couch)
```

**As set up (2026-10-29):** WSL2 distro `Ubuntu` (Ubuntu 26.04, Python 3.14, Node 22 from apt), Docker Desktop 29.9 with Ubuntu integration, repo at `~/Project-Mu` with a `.venv`, Postgres 14.24 in `madden-db` (command in `PROJECT_SPEC.md` §1.2). Git in Ubuntu pushes through the Windows Git Credential Manager (`/mnt/c/Program Files/Git/ucrt64/bin/git-credential-manager.exe`). Ollama for Windows is installed with no models yet. `.env`, `CAREER-GIANTS-AUTOSAVE`, `data/commentary_m27.json`, `data/commentary_save_m27.json` and the headshots are in place. `backup_latest.sql` (2036 week 21, 3,903 players, 154 MB) restored without errors; it predates three migrations, which are not yet applied to the PC's `madden`: `2027_01_05_madden_pgid` (held until the teleport, §4.5), `2027_01_08_player_progression` and `2027_01_09_personas`. Tests on the PC: save reader 31/31; pytest 1,769 passed and 14 failed. Of the 14, 8 are macOS-only code (Apple Vision, `Quartz`), 3 need the unapplied migrations, 1 needed `rembg[cpu]` (now in `requirements.txt`), and the `#debug` job-stop test failed because Ubuntu's `sh` (dash) exits on Ctrl-C before writing the job's exit code, fixed with `trap : INT` in `server/debug_api.py`. Windows Documents is redirected to OneDrive, so Madden's saves are in `C:\Users\jrere\OneDrive\Documents\Madden NFL 27\saves\` (confirmed after the first launch: `PROFILE-MADDEN`, `ROSTER-Official`, no `CAREER-*` until a franchise is started); `.env` sets `MADDEN_SAVES_DIR` to its `/mnt/c/...` path and `watch_save.py --once` scans it. Consider excluding that folder from OneDrive sync so uploads don't touch the files `watch_save.py` debounces. `C:\Users\jrere\.wslconfig`: `memory=12GB`, `networkingMode=mirrored`. Still to do: start a franchise for the §5.4 checks, the Mac's final post-Super Bowl backup and its restore, MMC tools and mods.

**Why not native Linux:** Madden uses EA Javelin anticheat, which runs at the Windows kernel level and does not work under Linux/Proton ([GamingOnLinux tracker](https://www.gamingonlinux.com/anticheat/vendor/11)); MMC Modding Tools and the community franchise tools are Windows-only too. WSL2 gives a real Ubuntu terminal (same `ls`, `cd`, `grep`, `git`, bash as the macOS terminal) next to Windows, so Project Mu's `.sh` scripts and Python code run unchanged.

**Crossing the boundary:** WSL2 reads the Windows save folder at `/mnt/c/Users/<Name>/Documents/Madden NFL 27/saves/`, or `/mnt/c/Users/<Name>/OneDrive/Documents/...` when Documents is redirected to OneDrive, as on JR's PC (offline franchise files, per the MMC tool instructions). Ollama on Windows is reachable from WSL2 at the Windows host address (or `localhost` with WSL mirrored networking). `pipeline/notify.py` uses `ntfy` over HTTP and works unchanged on WSL2. Mac-specific capture pieces (`chiaki-ng`, the screen capture macros) are not needed on the PC because saves are read directly from disk.

---

## 4. Phase 2: The 2037 Offseason Teleporter

The PS5 league already runs Madden 27, the same game as the PC, but the franchise file can't be moved: PS5 saves are encrypted on the console and Madden has no cross-platform transfer for offline franchises. The teleporter builds a PC Madden 27 franchise that matches Project Mu's 2037 offseason.

Because both are Madden 27, the schema, archetypes, OVR formulas and recorded commentary names are the same on both sides. The OVRs Project Mu captured on the PS5 come from the same formula the PC weights are fitted to (§4.4), so a target OVR means the same thing on both.

### 4.1 Strategy: Clean Cut at the 2037 Offseason

```
┌─────────────────────────────────────────────────────────────┐
│ 1. Conclude 2036 on the PS5 and in Project Mu (§2.1).       │
│ 2. Start a fresh Madden 27 franchise on PC.                 │
│ 3. Get it to the 2037 offseason (§4.2).                     │
│ 4. Run the teleporter on that save (§4.3).                  │
│ 5. Start the PC era on Day 1 of the 2037 NFL Draft.         │
└─────────────────────────────────────────────────────────────┘
```

Starting in the offseason avoids rebuilding a mid-playoff bracket and matches the Editorial Matrix's season boundary.

### 4.2 Reaching 2037

The franchise's own history is thrown away (Project Mu is the record), so nothing about how the save gets to 2037 matters except the result.

* **Option 1 — set the year in the save (try first):** start the franchise, advance to the offseason, and write `CurrentSeasonYear` / `BaseCalendarYear` (and `BaseSuperBowlNumber`) in the season-info table, as the community `seasonYearSetup` script does ([madden-franchise-utils](https://github.com/Sinthros/madden-franchise-utils) for Madden 24/25; an M27 build, `seasonYearSetup.exe`, is in *Prime's Retro Franchise Tools (M27)* on the MMC Discord, see [`MMC_MOD_SURVEY.md`](../MMC_MOD_SURVEY.md) §2, so it can be run as is or read for the exact fields). Player ages and contracts are overwritten by the teleporter anyway. Test for side effects: draft class year, contract year labels, schedule, awards screens.
* **Option 2 — unattended sim (fallback):** set all 32 teams to CPU control so no offseason stage waits on a decision, and sim to the 2037 offseason. Expect hours rather than minutes; it can run overnight.
* **Decide before creating the franchise:** the community *M27 Roster size increase* mod (80 preseason / 65 cut day, practice-squad eligibility 3 years, starting cap +$40M) only takes effect in a new franchise. Practice-squad size itself is hard-coded. If Project Mu rosters need more than 53 + 16, apply it before step 1.

### 4.3 The Teleporter (`pipeline/teleport.py`)

```
[Project Mu DB (2037 offseason)]               [PC Save (2037 offseason)]
Players, contracts, picks, prospects           Generated players
        │                                                  │
        └───────────────────────┬──────────────────────────┘
                                │
                                ▼
         [Step 1: Chassis Swap]
         Match each Project Mu player to a generated PC donor with the
         same position and archetype and the closest OVR. Write the
         Project Mu identity onto the donor: name, age, years pro, team,
         jersey number, dev trait, college. Record the donor's PGID in
         `players.madden_pgid`. Set the commentary ID to a recorded
         surname that matches the new name, or to none (see below).
         Set appearance from the headshot traits (see below).
                                │
                                ▼
         [Step 2: OVR Adjustment with Fitted Weights]
         Close any OVR gap (e.g. donor 84, target 86) by editing the
         archetype's highest-weight ratings, using the weights from §4.4.
                                │
                                ▼
         [Step 3: Check Against the Game]
         Save, let Madden load the file, read OVR back. Fix any player
         still off by one with a single-rating nudge and repeat.
                                │
                                ▼
         [Step 4: Contracts, Draft Capital & Prospects]
         • `Contract` records (length, years left, value, bonus)
         • `DraftPick` ownership (traded picks)
         • 2037 draft class prospects
                                │
                                ▼
                   [Save the franchise to disk]
```

**Scanned players (§2.3, built 2026-10-28):** for every player with a PS5 attribute scan, Step 2 writes the scanned ratings onto the donor instead of adjusting the donor's own (`teleport.scanned_ratings`; `--no-scan` turns it off). Their OVR is then the scan's by construction, and Step 3 is only a check. The scan's OVR wins over Project Mu's for the same season (JR decision 2026-10-28; the save loader then brings Project Mu's OVRs in line): only 576 of 1,651 scanned 2036 players have the same OVR in `player_seasons`, the rest mostly 1–3 lower there (an older read than the scan), and Madden computed the scan's OVR from these very ratings. Only when the teleport's season is past the scan's (Project Mu progression ran) are the scanned ratings moved, by Project Mu's OVR change on the predicted grade. Pairing ignores the donor's OVR for scanned players. Dry run on the sample save with the live 2036 rosters: 1,737 scanned players, every scanned rating read back exactly; the fit predicts the scan's OVR within ±1 for 1,649 (95%; the misses are LS 39, K 17, P 14, FB 12, the weak groups of §4.4), and 129 have another archetype predicted on top, which Madden settles on load (a §5.4 check). For the rest (draft prospects, players added after the scan), the donor's underlying ratings (and so its on-field style) are kept; only OVR, position, archetype and identity are guaranteed to match Project Mu. Player-behavior mods change play style anyway.

**Built (2026-10-27): `pipeline/teleport.py`.** Two changes from the diagram above, both forced by the save:
* **No team moves.** Madden ties players to teams through each team's `Roster` and `DepthChart` lists, not just `Player.TeamIndex`, so moving a player between teams isn't a single field. Every Project Mu team has 54–62 players and every Madden team about 63–65 (signed + practice squad), so each Project Mu player takes over a donor already on the same team: an identity swap. The pairing per team is an optimal assignment: same position and archetype first, then another archetype (PlayerType rewritten), then a neighbouring position (Position rewritten).
* **OVR by archetype grade, then settled.** The donor's ratings move to the Project Mu OVR for that archetype (`engines/ovr_weights.adjust_ratings`). If another archetype then ends up higher, the settling step lowers ratings that feed the rival more than the player's own archetype and restores the target on ratings that favour it.

Dry run on the sample save with the 2036 rosters: all 1,797 players matched (Project Mu's 32 team-defense `DEF` rows skipped), 852 same archetype, 772 another archetype, 78 a neighbouring position and 95 another position. The sample save's real-NFL rosters carry fewer tackles, outside linebackers, fullbacks, long snappers and QBs than Project Mu's; a generated 2037 PC save may balance differently. 236 donors are left over. Applying the 30,275 field edits to a copy with `save_writer.mjs` and reading it back: names and teams right for all 1,797, no other player, team or game changed, and the predicted OVR equals the Project Mu OVR with the right archetype on top for all 1,706 players that have one (91 have no OVR in Project Mu and keep the donor's ratings). Rating points moved per player: median 27, 90th percentile 76.

**Contracts (2026-10-27):** `pipeline/teleport_contracts.py` adds each player's ACTIVE contract to the edit, and `save_writer.mjs` writes it into the donor's own `PlayerContract` record in place. On the sample save every Signed player has their own record and practice-squad players have none. The rule: Length; CurrentYear = length − years remaining; cap hits rounded to $10K; bonus spread evenly over up to 7 years, never more than a year's hit; salary = hit − bonus; guarantees and void years cleared. Dry run: 1,532 of 1,797 contracts written, every remaining cap hit read back exactly. 203 players landed on practice-squad donors and 62 have no contract; all 265 keep the donor's contract (a matching cost that steers contracted players to Signed donors would recover most of the 203). Still for the PC: whether Madden accepts the edited contracts and how it charges dead money.

**Releasing leftover donors (2026-10-27): `pipeline/save_reader/release.mjs`.** A Madden player belongs to a team through `Player.TeamIndex` and `ContractStatus` plus the team's `Roster` / `PracticeSquad` lists (filled from slot 0 with no gaps, 100 and 12 slots), its depth chart (35 position lists of 6 slots, best first) and smaller team lists. Real free agents have TeamIndex 32, ContractStatus FreeAgent, Contract null, and sit in `League.FreeAgents` (practice-squad players are listed there too). `teleport.py --release` adds the leftover donors to the edits, and the writer makes each one look like those free agents, removes them from every team list and depth chart, appends them to the free-agent list and lowers the team's roster counts. On the sample save all 236 are released (115 signed, 121 practice squad) and every team's roster equals its Project Mu donors, with nothing else changed. PC checks: released players can be signed, cap totals are recalculated (the old contract record is left unreferenced, no dead money), and depth charts are reordered (one list, SEA RRE, ends up empty).

**Draft class and picks (Step 5, 2026-10-27): `pipeline/teleport_draft.py`.** Future picks are DraftPick rows (YearOffset 0 = the coming draft, Round, PickNumber) whose owner is CurrentTeam, a Team reference; the writer's `records` edits re-point it only to an existing Team row. Draft prospects are Player rows with ContractStatus Draft, each linked from a DraftPlayer row (DraftPosition, InitialDraftRank, TrueOverallRanking). Each Project Mu prospect takes over one Madden prospect by position and OVR (Project Mu prospects have no archetype or dev trait, so the donor's are kept), and the DraftPlayer board and true ranks follow Project Mu. Sample save: 388/388 prospects at target OVR (334 same position; Project Mu has 12 LS prospects, the save none), all 672 picks mapped, 125 owners changed. College is not written (it points into the game's built-in college list); 14 Madden prospects are left over and ranked last; PickNumber waits for a full Project Mu order. PC checks: YearOffset 0 is the 2037 draft at teleport time, Madden keeps or recomputes PickNumber and accepts the re-pointed owners.

**Everything together (2026-10-27):** `teleport --draft --release --appearance` on the sample save writes 80,508 player fields on 2,185 players (1,532 contracts) and 978 fields on 527 other records, and releases 236 donors, in about 18 seconds. Read back: all 32 rosters equal their Project Mu players, all 1,706 players with an OVR are on target, all 236 released players are free agents, games unchanged. Still to build: portraits (§9.4), the body's skin tone (`CharacterVisuals`, below), surname renames (below), and choosing `--age-add`, `--write-ovr` and `--contract-years-add` once the PC checks show how Madden ages players, recomputes OVR and rolls contracts.

**Commentary IDs (Built 2026-10-28):** Madden says one pre-recorded surname clip per player, chosen by `Player.PLYR_COMMENT`. Checked on `CAREER-GIANTS-AUTOSAVE`: the ID belongs to the surname, not the player (every Smith is 4600, every Johnson 2506); suffixes share it (Smith, Smith II, Smith Jr → 4600); sound-alike spellings can share it (Andersen, Anderson → 103); IDs run roughly alphabetically up to ~9,500; 0 means no recording (200 of 3,086 players), and the commentators then avoid the name. A donor keeps the donor's ID, so without this step Madden calls Project Mu players by the donor's surname.

Built (`pipeline/commentary_names.py`):
1. **Source List:** Madden 27 commentary lookup `data/commentary_m27.json` (7,865 surnames → 7,666 IDs, fetched from [madden-franchise-utils](https://github.com/Sinthros/madden-franchise-utils); git-ignored), overlaid by the IDs a real save uses: `python -m pipeline.commentary_names harvest <save | snapshot.json>` writes `data/commentary_save_m27.json` (git-ignored), which takes precedence. Checked with `validate` against `CAREER-GIANTS-AUTOSAVE` (2026-10-28): the file agrees with the save for 2,733 of the 2,738 players it covers; 53 players have a recording the file lacks (the 2026 rookie class, IDs ~9296–9340, incl. Jacas, Zvada, Eckley, Ponds), 5 get a different ID (Kendrick, Ridder, Yeast, and Elarms-Orr / McNeil-Warren, which the file matched to Orr / Warren), and 8 have 0 in the save although the file has an ID (Trader, Purchase, Chambliss, four hyphenated names). Surnames not in the save can only be checked by ear in-game. Re-run `harvest` on the PC save at migration time.
2. **Normalization & Matching:** `normalize()` drops suffixes (`Jr`, `Sr`, `II`, `III`, `IV`, `V`), strips accents/punctuation, folds case, and matches with/without hyphens. A suffixed surname with its own recording is tried first (Samuel Jr 8440, Thomas V 9204; the save agrees for all 14 such players), else the bare surname (Smith Jr → Smith 4600). `commentary_id(full_name)` matches full names, short names, and compound surnames.
3. **Phonetic Ranking:** `suggest_surnames()` generates top 5 recorded candidates for unmatched players using Double Metaphone phonetic similarity tier + Levenshtein edit distance, preferring same first letter and filtering out surnames already on the player's team roster.
4. **Coverage & Review Workflow:** `python -m pipeline.commentary_names report` reports 1,681 / 1,797 active non-DEF players matched (93.5%) with the save's IDs (1,665 with the community file alone), generating `captures/commentary_unmatched.csv` with the 116 unmatched and a `chosen` column for JR review. All 116 are generated players (no ESPN id, under 10 years' experience in 2036); JR approved renaming them (2026-10-28); the real players all have save recordings.
5. **Database Renames & Aliases:** `python -m pipeline.commentary_names apply <reviewed.csv> [--commit]` dry-runs by default; with `--commit`, updates `players.full_name` and `player_name`, recording old names in `player_name_aliases` (`db/migrations/2027_01_07_player_name_aliases.sql`). Search in `server/api.py` and `server/napper_api.py` matches through `player_name_aliases`. **Held until after the last PS5 capture (JR, 2026-10-28):** the 116 reviewed renames (`captures/commentary_reviewed.csv`, dry run clean on live; three picks changed to avoid duplicating active players' full names: Gonzales → Guzman, Bazley → Bagley, Rainford → Radford) are committed with `apply --commit` just before the teleport. Committed earlier, they would break capture loads, which match the names Madden shows against `players.player_name` and don't read the aliases, and roster scans would write the old names back. Open: `teleport_appearance.py` seeds each player's look from the current `player_name` / `full_name`, so after the renames those 116 would get a head that no longer matches their headshot unless seeding uses the original name from `player_name_aliases`.
6. **Teleporter Integration:** `pipeline/teleport.py` and `pipeline/teleport_draft.py` look up `commentary_id()` directly for each player (0 when unmatched), and record `commentary_matched` and `commentary_unmatched` counts in the teleport report. Past articles: decide at migration time whether to rewrite exact old full-name strings in stored articles or leave them as written.

**Appearance:** `pipeline/teleport_appearance.py` (`teleport --appearance`) reseeds `headshots/headshot_appearance_generator.py` exactly as each headshot was drawn (2035 roster name/team for the original batch, full name and rookie team for rookie headshots; all 1,242 checkable players matched their headshot prompts) and maps the traits onto plain Player fields. A generic head (`GenericHeadAssetName`/`PLYR_GENERICHEAD`, named `<skin tone 1–7>_<hair>_<facial hair>_<n>`) is chosen from heads already in the save whose tone digit, then facial-hair and hair codes match (sample: 1,737 of 1,797 on all three; head tones exactly match the headshot tone mix). `CharacterBodyType` comes from the headshot's build (lean/athletic/stocky/very large → Thin/Standard/Muscular/Heavy), and `PLYR_ASSETNAME` is renamed so the donor's face scan no longer loads. The body's tint lives in `CharacterVisuals.RawData` (zstd dictionary `0x1823777C` holding an ISON document, with root `skinTone` 1–8). Built (2026-10-28): `pipeline/character_visuals.py` handles ISON parsing/serialization and zstd re-compression (100% round-trip fidelity); generic head tone digits 1–7 map 1-to-1 to body `skinTone` 1–7 (derived from modal frequencies across all 2,985 players in `CAREER-GIANTS-AUTOSAVE`). `pipeline/save_reader/visual_edits.mjs` integrates into `save_writer.mjs` via a `visuals: {"skinTone": N}` block, implementing copy-on-write allocation for shared `CharacterVisuals` records, Table3 overflow handling, and strict read-back verification. With `--appearance`, 100% of teleported players have matching head and body skin tones. PC checks: the asset rename, which head field Madden draws from, the hair/facial code reading, and whether Madden draws the edited body skin tone in-game. Custom faces are out of scope; headshots show as portraits (§9.4).

Generated players left without a Project Mu match are released or retired so the league's player pool matches the database.

### 4.4 Fitting the OVR Weights

Madden computes `OverallRating` from a player's ratings with position/archetype-specific weights that EA does not publish. The PC save holds both the full ratings and the OVR of every player, so the weights can be fitted:

1. Read every player in the save (plus draft class and free agents for more samples).
2. Group by position and archetype (~1,800+ players over ~70 archetypes, ~25+ each).
3. Per group, fit OVR as a weighted sum of ratings with least squares; round to the game's integer behavior.
4. Validate on held-out players: expect most within ±1, with rounding/truncation causing the rest.

**First result (2026-10-27, `CAREER-GIANTS-AUTOSAVE`, 3,086 players, 5-fold cross-validation, non-negative weights per group):** grouping by archetype (`Player.PlayerType`) gives 78% exact and 96% within ±1; grouping by position gives 45% exact and 86% within ±1, so `OverallRating` follows the archetype. Most archetypes fit almost perfectly (`DT_NoseTackle`, `G_Power`, `CB_MantoMan`, `DE_SmallerSpeedRusher` ≥ 99% within ±1). The misses are small groups (`KP_Accurate` n=15, `FB_Utility` n=17, `MLB_PassCoverage` n=20, `WR_Physical` n=20) and `QB_FieldGeneral` (39% within ±1); more samples from draft classes and other saves should close them. Ratings are on `Player` as `<Name>Rating` (with `Original<Name>Rating` copies).

**Module (2026-10-27, updated 2026-10-28):** `engines/ovr_weights.py`, rebuilt on Madden's own per-archetype OVRs. Every player stores an OVR for each archetype of their position (`OverallGrade0-4`); `OverallRating` is the highest of them for 2,984 of 2,990 players (the rest have no grades yet) and `PlayerType` is that archetype. On ties the stored archetype is always one of the tied ones but not by slot order; most likely a player keeps their archetype until another is strictly higher. Fitting each archetype on all its grades (9,106 clean samples instead of 2,990 OVRs) with squared terms where they cross-validate better gives 91% exact and 100% within ±1, and taking the top predicted grade gives the stored OVR for 95% exactly and ~100% within ±1 (first version: 78% / 96%).

*QB refit (2026-10-28, §2.4 item 1):* The initial QB formulas leaned spuriously on LongSnap (worth ~1.5 OVR per LongSnap²/100), root-caused to an anomalous converted receiver in `CAREER-GIANTS-AUTOSAVE` (Carter Landry, pgid 24437, overall 91, flat 83 grades, LongSnap 59) where all other QBs had LongSnap 0 or 10. Excluding anomalous QBs whose grades disagree with overall rating and dropping LongSnap from QB candidate features refit all 4 QB archetypes with true passing attributes (ThrowPower, ThrowAccuracy, Awareness, ThrowUnderPressure, etc.). All 4 QB archetypes reached 1.000 within ±1 on sample-save cross-validation (cv_exact 90.3%–94.4%), all 42 non-QB archetypes remained unchanged, and 78 of 78 (100.0%) PS5-scanned QBs in `player_attributes` predict within ±1 (73 exact / 93.6%, range −1 to +1). LongSnap is also clamped to 10 in `predict_grade` as a backward-compatibility safeguard, and `adjust_ratings` never moves LongSnap for QBs. Still weak: fullbacks (25 samples). More saves and draft classes would help them; Madden's recalculation on load is a PC check.

Prior art: the community [Madden Roster Editor](https://github.com/kn1meR/MaddenRosterEditor) recalculates OVR from attributes and archetype using weights in its `config/archetype_breakdown.xlsx`; check it against the fit as a starting point or cross-check.

The fit picks which ratings to edit and by how much; §4.3 Step 3 settles the last point. Before relying on it, test whether Madden recomputes OVR from ratings when the file loads or only at progression; if only at progression, write `OverallRating` directly alongside the rating edits.

### 4.5 PGID Mapping

New column `players.madden_pgid` (Madden's player ID on the PC save: `Player.PresentationId`, unique for all 3,086 players in `CAREER-GIANTS-AUTOSAVE`; stability across Advance Week and season rollover is a §5.4 check), written by the teleporter for every player it places and by the weekly loader for rookies and new free agents. Project Mu's own `player_id` stays the primary key; the PGID is only the join to the save.

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
**Built and tested (2026-10-28).** A Python daemon running on the PC/WSL2:
* **Watches** the `CAREER-*` save folder (`--saves-dir`, path argument, or `MADDEN_SAVES_DIR` in `.env`, e.g. `/mnt/c/Users/<Name>/Documents/Madden NFL 27/saves/`).
* **Debounces** write bursts: acts only when `mtime` and `size` have been stable for `--debounce` seconds (default 10s) and `size > 0`. Tracks processed modification times so each state is executed once.
* **Snapshots & Compares:** Snapshots via `pc_save_reader.mjs` into `captures/<run>/save.json` and compares save season and week against `league_state`:
  * *Same week:* no-op, temporary snapshot folder removed.
  * *Older week / different franchise:* sends an ntfy notification and no-ops.
  * *New week:* runs `load_save.py` (`--commit`), then deterministic analytics engines (`weekly_capture.run_weekly_engines`), queues nightly jobs (§7.5), and publishes newsroom articles (`newsroom.weekly_run.run_weekly_swarm`).
* **Locking & Error Handling:** Prevents concurrent runs via `captures/.weekly_run.lock` (shared with `weekly_capture.py`). Failed runs trigger high-priority phone notifications via `pipeline.notify`.
* **Flags:** `--once` (checks once and exits), `--dry-run` (load without commit, skips engines/articles), `--no-articles` (loads and runs engines, skips newsroom articles), `--season N` (season override), `--force` (bypasses week check for testing).
* **Testing:** 21 unit and integration tests in `tests/test_watch_save.py` covering debounce settle/bursts, week classification, run lock, flag semantics, and end-to-end dry run against `CAREER-GIANTS-AUTOSAVE` and `madden_test`.

### 5.3 Game-Level Data

**Checked on a real save (2026-10-27).** `CAREER-GIANTS-AUTOSAVE`, a Madden 27 franchise in 2027 (franchise year 1), current week index 4. The save reports schema 987; `madden-franchise` 4.4.1 falls back to its bundled 620, and the tables below decode cleanly with it.
* **Per-game stats exist for CPU-simmed games:** `GameOffensiveStats`, `GameDefensiveStats`, `GameKickingStats`, `GameOLineStats` and both return tables have a row per player per game for all 16 games of each of the first four regular-season weeks, simmed and user-played alike, linked to `SeasonGame`. Example: a QB line of 22/27, 264 yards, 3 TD, 65 downs played, game rating 79.
* **The latest played week had scores and scoring summaries but no per-game stat rows yet.** Check on the PC whether they appear after the next Advance Week.
* **Retention:** the stat tables' capacities (7,700 offense rows, 13,500 defense rows) are below a full season at the observed ~460 and ~915 rows a week, so older weeks are probably purged. Scoring summaries were kept only for the last two weeks, plus a few user-played games from the previous season. The loader must read every week.
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
Before building §4–§5, read a fresh Madden 27 save and confirm:
1. `madden-franchise` opens it and lists the tables above.
2. PGIDs are stable across saves and across Advance Week.
3. That CPU-simmed games actually fill the `Game*Stats` tables, `ScoringSummaries` and the quarter-score fields, and that a sample game's rows match the in-game box score.
4. Whether `Highlight` rows exist for simmed games or only for games JR plays.
5. How many weeks of per-game rows survive: sim several weeks, then past a season rollover.
6. Season stat rows update after each simmed week, and the diff matches the per-game rows.
7. Whether Madden recomputes OVR on load (§4.4).
8. Whether Madden draws the edited body skin tone in-game (§4.3, `CharacterVisuals.RawData`).

The community *Madden Franchise Exporter* (a plain-text dump of a franchise file's career and season stats, team stats, game logs and rosters) is an independent check for items 3, 5 and 6 and for `pipeline/load_save.py` output.

---

## 6. Phase 4: The CFB 27 $\rightarrow$ Madden Dynasty Bridge

`madden-franchise` reads CFB 27 PC Dynasty saves, so Project Mu can run a Saturday-to-Sunday pipeline:

```
[CFB 27 Dynasty Season]
          │
          ├──> Week by week: college game results, rankings, playoff bracket
          │    and award-race stats populate Project Mu's `scout_events` table
          │
          ▼
[Offseason "Players Leaving" Stage]
          │
          ├──> `pipeline/cfb_save_reader.js` extracts graduating seniors & early entrants
          │    (names, schools, physicals, traits, college season totals)
          │
          ▼
[Rating Translation & Compression Engine]
          │
          ├──> Compresses CFB 27 ratings into Madden's draft-class rookie distribution
          │    (top prospect ~82–84 OVR; late rounds ~62–66 OVR; assigns hidden dev traits)
          │
          ▼
[Name Check]
          │
          ├──> Each prospect's surname goes through the teleporter's surname rule (§4.3).
          │    Prospects with a recorded surname get its commentary ID; the rest get 0
          │    and are listed so JR can decide whether to keep the name
          │
          ▼
[Inject into Madden Draft Class]
          │
          └──> Writes the prospects into Madden's draft class (community
               `draftPlayerImportNcaa` / `playersToDraftClass` utilities are prior art)
```

**Prior art to compare before building:** *Pipeline — CFB 27 → Madden 26/27 Draft Class Tool* (MMC Discord, 2026-08) already does most of this chain as a Windows app: it reads the dynasty's leaving players (or predicts them from earlier saves), projects round and pick, converts ratings to Madden's scale with a deterministic, tunable curve, assigns dev traits by target shares and writes an importable draft class with faces. Project Mu still needs its own reader for `scout_events` and the name check, but the rating translation and injection could start from that tool or check against it. Community M27 classes (Eli2Manningham 2027–2033, matched to M27's higher rookie scale) are the fallback for a year without a CFB class. If a class import runs out of player rows, WiiMaster's `trimFreeAgents` utility deletes the lowest free agents to make room ([`MMC_MOD_SURVEY.md`](../MMC_MOD_SURVEY.md) §2).

**Rookie names:** Madden-generated draft classes already carry commentary IDs. Anything Project Mu names itself (CFB 27 imports that get renamed, fictional prospects or created players) picks surnames from Madden's recorded list, so every new player can be called by name. Existing players keep their names.

---

## 7. Phase 5: Local LLM for Short Tasks (RTX 5070 Ti)

A local model takes over short, high-volume calls, with ultra-compact models running resident beside Madden for interactive gameplay tasks and mid-size models running on the nightly GPU queue. Newsroom articles remain cloud-hosted (Gemini or hosted open-weight models). Nothing moves until side-by-side quality testing passes (§7.4).

Detailed research, costs, benchmark comparisons, and sourcing: [`OPEN_MODEL_RESEARCH.md`](../OPEN_MODEL_RESEARCH.md) (2026-10-28).

### 7.1 Measured Usage (`llm_usage`, 2026-10-22 – 2026-10-27, all `gemini-3.8-flash`)

| Source | Calls / day | Avg in | Max in | Avg out | Avg thinking |
| :--- | ---: | ---: | ---: | ---: | ---: |
| `newsroom` (national articles and local beat articles) | ~250–550 | 44K | 184K | 304 | 513 |
| `agent_wire` ([`newsroom/social_writer.py`](../../newsroom/social_writer.py)) | ~120–320 | 1.7K | 2.4K | 63 | 936 |
| `agent_memory` ([`newsroom/agent_memory.py`](../../newsroom/agent_memory.py)) | ~65–180 | 1.1K | 4.2K | 161 | 1,143 |
| `scouting_notes` ([`engines/scouting_narrative.py`](../../engines/scouting_narrative.py)) | ~8–32 | 0.3K | 0.4K | 75 | 1,049 |
| `gm_persona` / `gm_rationale` / `gm_negotiation` ([`engines/gm_negotiation.py`](../../engines/gm_negotiation.py) and the GM agents) | a few | 0.3–1K | 1.3K | 130–180 | ~1,000 |
| `extract` (screen OCR) | ~500 | 1.2K | 1.4K | 668 | 801 |

* Each newsroom article is a tool loop of dozens of calls whose context grows with each step. The newsroom sends ~18M input tokens a day, ~85% of it cache hits.
* The short tasks send ~0.5M input tokens a day (~3% of the input), but their output plus thinking is about half of the daily total, mostly thinking on 60-token tweets.
* `extract` goes away with the save loader (§5); that is the largest LLM saving of the migration and needs no local model.

### 7.2 What Fits on 16GB

Candidate models for the RTX 5070 Ti (16GB VRAM) evaluated at 4-bit quantization (Q4_K_M or similar) in Ollama or llama.cpp:

| Candidate | Architecture / Params | Q4 Size | Fits 16GB VRAM? | Expected Speed & Role in Project Mu |
| :--- | :--- | ---: | :--- | :--- |
| **Gemma 4 26B-A4B** | ~26B total / 4B active MoE | ~14.5–16GB | Tight fit (may need 3-bit quant or partial offload) | ~130 tok/s on 5070 Ti; top MoE candidate for short tasks; same family as Gemini helps persona tone carry over (`gemma4:26b`) |
| **Qwen3.8-27B** | 27B dense | ~16–17GB | Needs ~3.5-bit to fit VRAM | ~40 tok/s; strongest dense model at this size; 262K native context |
| **LFM2.5-24B-A2B** (Liquid) | 24B total / 2.45B active MoE | ~14GB | Fits cleanly with context room | ~180+ tok/s; high reported IFEval (96.8) and tool use; fast short-task candidate |
| **Qwen3.6-35B-A3B** | 35B total / 3B active MoE | ~20.6GB | VRAM + System RAM offload (`--n-cpu-moe`) | Tens of tok/s; viable via MoE CPU expert offload on 32GB DDR5 |
| **Nemotron 3 Nano** (NVIDIA) | 30B total / ~3B active MoE | ~18GB | VRAM + System RAM offload (`--n-cpu-moe`) | Viable via MoE CPU expert offload; up to 1M context, strong tool use |
| **gpt-oss-20b** (OpenAI) | 21B total / 3.6B active MoE | ~13GB | Fits cleanly | ~189 tok/s; fast baseline, known for flatter prose |
| **Gemma 4 E4B / E2B** | ~4B / ~2B effective | 3–5GB | Fits easily; resident beside Madden | Fast response; low VRAM footprint enables concurrent execution during gameplay |
| **LFM3-1.7B** (Liquid) | 1.73B hybrid | ~1GB | Fits easily; resident beside Madden | ~1GB footprint; dedicated to real-time structured JSON / classification during gameplay (§7.5) |

**MoE expert offload changes the memory boundary:**
While dense models cannot offload layers to system RAM without severe token-generation latency (saturating PCIe bus bandwidth reading all weights every token), mixture-of-experts (MoE) models behave fundamentally differently. With llama.cpp's `--n-cpu-moe` / `--cpu-moe`, attention layers and shared weights remain resident in GPU VRAM while only the sparse, per-token routed experts (~2–4B active parameters) are streamed from system RAM. With 32GB of DDR5 system RAM, 30–35B MoE models with ~3B active parameters (e.g. Qwen3.6-35B-A3B, Nemotron 3 Nano) achieve viable inference speeds (tens of tok/s; 40+ tok/s with an expert cache) on a 16GB card. With 24GB system RAM headroom is tighter alongside Windows and Docker.

Pick the specific model at test time (§7.4); Ollama or llama.cpp for Windows hosts it at `http://localhost:11434`, reachable from WSL2 (§3).

### 7.3 Workload Split

| Task | Target | Why |
| :--- | :--- | :--- |
| Agent Wire tweets | Local 24–27B (or MoE offload) | <2.5K-token prompts, ~60-token output; ~2s a call, ~10 min a day without thinking; runs on nightly queue (§7.8) or idle GPU |
| Agent memory takes & season summaries | Local 24–27B (or MoE offload) | <4.2K-token prompts, short output; runs on nightly queue (§7.8) |
| Scouting mover notes | Local 24–27B (or MoE offload) | Tiny prompts, a handful a week |
| GM persona & trade rationale | Local 24–27B (or MoE offload) | Short prompts; structured output; runs when Madden is idle or during nightly queue |
| Live GM negotiations & commission classification | Resident local ~1–2GB option (LFM3-1.7B, Gemma 4 E2B/E4B) with Gemini fallback | Option to test resident alongside Madden (§7.5); provider switch falls back to Gemini when local model is unavailable or unfeasible |
| National articles | Gemini or Hosted Open Model API (DeepSeek V4.1 Flash, GLM-5.3-Flash) | 25–75K-token contexts (up to 150K+) in dozens-of-call tool loops; cannot fit next to local model weights on 16GB VRAM. Evaluated against hosted open-weight models due to 2027 price change (see below) |
| Local beat articles | Gemini or Hosted Open Model API (DeepSeek V4.1 Flash, GLM-5.3-Flash) | Same `newsroom` tool loop and 192 persona voice requirements as national articles |

**Hosted open-weight models for the newsroom (2027 pricing alternative):**
Gemini 3.8 Flash prices double on 2027-01-01 (input: $0.75 → $1.50 per 1M; output & thinking: $3.75 → $7.50 per 1M). Because the save loader (§5) eliminates screen OCR calls, articles become ~73% of the post-migration LLM bill, projected to rise from ~$125/month to ~$245/month (~$8.2/day).
Hosted open-weight frontier models provide a cost-effective alternative for the newsroom's long tool loops:
* **DeepSeek V4.1 Flash (1017):** 552B MoE (8–16B active), 1M context, MIT license. API pricing: $0.30 input / $1.20 output per 1M tokens at peak; 50% discount off-peak (which aligns with overnight newsroom runs). Estimated newsroom cost: ~$1.6/day (~$45/month).
* **GLM-5.3-Flash:** 320B MoE (18B active), ~1M context, MIT license. API pricing: $0.15 input / $0.03 cached / $0.50 output per 1M tokens. Estimated newsroom cost: ~$1.0/day (~$30/month).
* **Switching requirements:** The newsroom's automatic tool loop (`newsroom/swarm_dispatcher.py`) uses Google GenAI SDK's native function calling and `UsageMeter`. Using DeepSeek, GLM, or other OpenAI-compatible endpoints requires porting the tool-dispatch loop (~50 lines) to an OpenAI client and verifying tool-calling reliability and persona voice preservation over 50-turn loops (§7.4).

Each moved caller gets a provider switch via `engines/llm_provider.py` (Gemini or local Ollama) so it can fall back to Gemini when the local model is down, unreachable, or times out (never on bad answers or validation failures). Provider choice is configured via model prefix (e.g. `ollama:qwen2.5:14b`), per-label/source environment variable (`LLM_PROVIDER_<LABEL>`, e.g. `LLM_PROVIDER_AGENT_WIRE`), global `LLM_PROVIDER`, or explicit parameter (default `gemini`). The `llm_usage` logging records local calls with `model = 'ollama/<model_name>'`, mapping `prompt_eval_count` to `input_tokens` and `eval_count` to `output_tokens`, keeping all model telemetry in one table.

### 7.4 Quality Test Before Switching

Before migrating any task to local or hosted open-weight models, evaluate outputs side by side against the Gemini baseline using the quality test harness (`tools/llm_compare.py`, PRE_PC_WORK.md §7).

#### Quality Test Harness CLI (`tools/llm_compare.py`)

Run side-by-side evaluations across two or more models (Gemini at various thinking levels and local Ollama models) without modifying any table. Inputs come from the frozen 2036 archive `madden_2036` by default (`--db` takes another database name or DSN, or set `LLM_COMPARE_DB`); every connection the process opens is read-only (`PGOPTIONS=-c default_transaction_read_only=on`) and model calls skip the `llm_usage` write. If the database is unreachable the harness warns and falls back to its built-in sample inputs.

```bash
# The whole matrix in one command: all 7 tasks, one combined report (verify_output/compare_suite_<ts>.html):
python3 -m tools.llm_compare --suite --models gemini-3.8-flash:low ollama:gemma4:12b ollama:gemma4:12b@nothink --limit 5

# Compare Agent Wire tweets between Gemini 3.8 Flash (low thinking) and 3.5 Flash-Lite:
python3 -m tools.llm_compare --task agent_wire --season 2036 --week 20 --models gemini-3.8-flash:low gemini-3.5-flash-lite --limit 5

# GM calls on the beside-Madden tier, reasoning off, JSON output requested:
python3 -m tools.llm_compare --suite --tasks gm_persona gm_rationale gm_negotiation --models ollama:gemma4:e4b@nothink ollama:lfm3:1.7b@nothink --json-mode --limit 3
```

**Ollama models on the PC.** Ollama for Windows serves WSL at `localhost:11434` (mirrored networking). Model specs are `ollama:<tag>`, with the tag's own colon (`ollama:gemma4:12b`). The harness never falls back to Gemini: an Ollama timeout or connection error is scored as a failed call (before 2026-10-29 a 60s timeout on a cold model load quietly answered with Gemini and scored it as the local model at $0). Options:
* `@think` / `@nothink` on an Ollama spec forces the model's reasoning on or off (Ollama `think`). gemma4 and lfm3 reason by default. Ollama counts reasoning inside `eval_count`, so `thinking_tokens` is an estimate from the reasoning's share of the generated text.
* `--ollama-timeout` (default 300s: a cold 8GB load plus a reasoning pass runs past `llm_provider`'s 60s default) and `--num-ctx` (default 16,384; Ollama's own 4,096 default silently truncates the longer prompts).
* `--json-mode` asks for JSON output (Ollama `format: json`, Gemini `response_mime_type`) on `gm_persona`, `gm_rationale` and `gm_negotiation`. The production GM calls don't request JSON yet, so leave it off to measure them as they run today.

**Smoke test (2026-10-29, season 2036 week 20, `--limit 1`, database inputs):**

| Model | Pass | Total time, 7 calls | Cost | Notes |
| :--- | :--- | :--- | :--- | :--- |
| `gemini-3.8-flash:low` | 7/7 | 18s (1.8–3.5s a call) | $0.0015 | reported 0 thinking tokens on every call |
| `ollama:gemma4:12b` (reasoning on) | 7/7 | 108s (4.7–23s a call) | $0 | ~650–3,600 reasoning tokens a call |
| `ollama:gemma4:12b@nothink` | 6/7 | 10s (0.6–3s a call) | $0 | `gm_rationale` failed: a trailing comma in its JSON. With `--json-mode`, 6/6 on the three GM tasks at `--limit 2` |
| `ollama:gemma4:e4b@nothink` | 7/7 | 36s (31s was the cold load; 0.4–1s a call after) | $0 | |
| `ollama:lfm3:1.7b` (reasoning on) | 6/7 | 101s (7–20s a call) | $0 | `memory_summary` failed; 2,400–6,200 reasoning tokens a call |

A smoke test is one input per task: it shows the pipeline works, not which model is good enough. The voice and facts verdicts still need a `--limit 5` run and JR's review in the HTML report.

**Candidate local models (Ollama registry tags and download sizes, checked 2026-10-29).** Pulled: `gemma4:12b`, `gemma4:e4b`, `lfm3:1.7b`.

| Tag | Size | Fits the 16GB GPU |
| :--- | :--- | :--- |
| `lfm3:1.7b` | 1.1 GB | yes; beside-Madden tier |
| `gemma4:e2b` | 4.6 GB | yes; beside-Madden tier |
| `gemma4:e4b` | 6.6 GB | yes; beside-Madden tier |
| `gemma4:12b` | 8.0 GB | yes |
| `qwen2.5:14b` | 9.0 GB | yes (the provider's default Ollama model) |
| `qwen3:14b` | 9.3 GB | yes |
| `gpt-oss:20b` | 13.8 GB | yes, with little room for context |
| `lfm2.5:24b` | 14.5 GB | barely; expect some CPU offload |
| `mistral-small3.2:24b` | 15.2 GB | partial CPU offload |
| `qwen3.8:27b` | 17.7 GB | partial CPU offload (dense, slow) |
| `qwen3:30b-a3b` | 18.6 GB | MoE, CPU expert offload |
| `gemma4:26b` (= `gemma4:26b-a4b`) | 18.7 GB | MoE, CPU expert offload |
| `qwen3.6:35b-a3b` | 22.6 GB | MoE, CPU expert offload |

Not on the registry: `gemma4:4b`, `gemma4:27b`, `lfm2.5:24b-a2b`. To pull the next tier: `ollama pull gpt-oss:20b`, `ollama pull qwen3:14b`, `ollama pull gemma4:26b`.

**Supported Tasks:**
* `agent_wire`: Social desk posts (tweets, <= 280 chars, money & rumor guards)
* `memory_takes`: Writer take extraction from articles (JSON list of up to 3 takes)
* `memory_summary`: Running season memory summary (<= 120 words)
* `scouting_notes`: College draft prospect notes (scouting narrative rules)
* `gm_persona`: Front office GM persona & bio generation (JSON)
* `gm_rationale`: Trade rationale explanation in GM voices (JSON)
* `gm_negotiation`: Trade negotiation responses (JSON)

**Outputs (`verify_output/`):**
* `verify_output/compare_suite_<timestamp>.json` / `.html` (`--suite` only): pass rate, average latency and cost per task per model plus totals, linking each task's report.
* `verify_output/compare_<task>_<timestamp>.json`: full structured telemetry containing per-model tokens (input, output, thinking), latency, cost, and automated check results.
* `verify_output/compare_<task>_<timestamp>.html`: interactive side-by-side dashboard featuring:
  - Summary KPI cards: Pass Rate (%), Total Failures, Token Breakdown (In / Out / Thinking), Latency, and Cost.
  - Side-by-side comparison columns for all evaluated models.
  - Automated validation check badges (PASS / FAIL) with itemized issue breakdowns.
  - Interactive **Persona Voice Verdict** dropdowns (`🌟 Excellent`, `👍 Good`, `👌 Generic`, `👎 Off-Voice`, `🚫 Hallucinated`) and manual review notes, automatically persisted to browser `localStorage` for JR review.

#### Evaluation Order (from OPEN_MODEL_RESEARCH.md §5)

1. **Now, no hardware:** Cap thinking on short Gemini calls (plan §7.9, `engines/llm_config.py`). Run `tools/social_model_compare.py` with `gemini-3.8-flash:low` and `gemini-3.5-flash-lite` to establish the baseline quality, token usage, and cost bar that open models must meet.
2. **Hosted open models for newsroom (now):** Run one week of newsroom articles through DeepSeek V4.1 Flash and GLM-5.3-Flash (and Kimi K3 as an upper quality ceiling) side by side with Gemini 3.8 Flash. Evaluate via an OpenAI-compatible tool loop port (§7.3). Key gates: tool-calling reliability over 50-step loops, persona voice fidelity across 192 distinct writers, and zero factual contradictions with deterministic data tools.
3. **Local short tasks (on Mac now via §2.5, PC on arrival):** Test Agent Wire, agent memory, and scouting prompts through candidate local models (Gemma 4 26B-A4B, Qwen3.8-27B, LFM2.5-24B-A2B, gpt-oss-20b, and Qwen3.6-35B-A3B with CPU expert offload), both with thinking enabled and disabled.
4. **Beside-Madden resident tier (on PC):** Test live GM negotiations (`gm_negotiation`, rationale JSON) and custom commission classification through resident LFM3-1.7B and Gemma 4 E2B/E4B while Madden NFL 27 is active. Measure free GPU VRAM and verify that Madden's frame rate remains locked at 120 FPS.
5. **Pre-switch re-verification:** Re-evaluate top open-weight releases before final migration cutover, monitoring incoming releases (e.g. Mistral Large 4, MiniMax M3.1 Flash, third-party LFM3 benchmarks).

#### Quality Gates & Verification Criteria

For each task in §7.3, side-by-side evaluation verifies:
* **Voice:** Do tweets, takes, and articles preserve distinct persona signatures and follow anti-cliché, banned-phrase, and variety directives?
* **Facts:** Strict adherence to data tools and input facts (the hallucination policy permits invented narrative color, but never incorrect statistics, scores, or names).
* **Structure:** Strict JSON schema adherence and valid formatting for GM calls, trade proposals, and classification.
* **Thinking & Latency:** With Gemini thinking capped at `low`, short tasks consume ~100–150 tokens. Test whether candidate local models require thinking for task adherence; unconstrained thinking can expand daily short-task execution time from 10–20 minutes to 1–1.5 hours.

A task moves to a local or hosted open model only if its output passes all quality checks; otherwise it remains on Gemini.

### 7.5 Sharing the GPU with Madden

Madden NFL 27 consumes a substantial portion of the RTX 5070 Ti's 16GB VRAM during active gameplay. While mid-to-large models (14–27B) and image generation pipelines cannot safely share VRAM with Madden without risk of out-of-memory crashes or dropped frames, ultra-compact models alter this trade-off:

* **Beside-Madden resident option to test (~1–2GB Tier):** Test running ultra-compact models (LFM3-1.7B at ~1GB weights, Gemma 4 E2B/E4B at ~3–5GB) resident in VRAM alongside Madden during active play as an option to evaluate, not a final decision. Interactive structured tasks triggered during gameplay—specifically live GM negotiations (`engines/gm_negotiation.py`, rationale JSON) and custom commission classification (`newsroom/custom_commission.py`)—can test executing against this resident model without waiting for the nightly queue.
* **Nightly queue for heavy local batch jobs:** Creative text generation requiring 14–27B models (Agent Wire batches, agent memory takes, scouting notes) and image generation models (headshots, §7.7) are deferred by the save watcher (§5.2) to the nightly GPU queue (§7.8), executing once Madden has exited or after a designated idle timeout.
* **Cloud fallback:** When the local model is unavailable, offline, times out, or if resident execution alongside Madden degrades gameplay frame rate, the provider switch (`engines/llm_provider.py`, PRE_PC_WORK.md §6) automatically falls back to Gemini (or a hosted open model). Live GM negotiations always retain cloud fallback.
* **Cloud / hosted newsroom execution:** Gemini or hosted open-weight API calls (DeepSeek V4.1 Flash, GLM-5.3-Flash) do not utilize local GPU resources and run immediately after the weekly save load regardless of Madden's execution state.

### 7.6 Seasonal and One-Time Calls

| Task | Code | How often | Plan |
| :--- | :--- | :--- | :--- |
| Player headshots (`gemini-nano-banana-2.1`) | `headshots/make_headshot.py`, `headshots/gen.py`, `headshots/rookie_headshots.py` | once for all players; ~250–300 rookies a season | local image model (§7.7) |
| GM persona bios, trade rationale | `engines/gm_llm.py` | per new GM; per trade | local text model (§7.3) |
| Custom commission classification | `newsroom/custom_commission.py` | on demand | local for the classify step; the article itself follows the newsroom (Gemini) |
| Agent memory season summaries | `newsroom/agent_memory.py` | season rollover, every few takes | local text model |
| Screen OCR | `extract.py`, `pipeline/ocr_extractor.py`, `pipeline/roster_scanner.py`, `pipeline/schedule_scanner.py`, `pipeline/heal_roster.py` | weekly and seasonal scans | retired by the save loader (§5) |

### 7.7 Local Headshot Generation

* **Model:** an open image model that fits in 16GB (a ~12B model at 8-bit, or a smaller SDXL-class model), run through ComfyUI or `diffusers` from Python. Very roughly 10–30 s per 1024px portrait: a rookie class in 1–2 hours, all 1,829 players in one night.
* **What changes:** only the generate call in `headshots/make_headshot.py` / `headshots/rookie_headshots.py`. The appearance generator, prompts, `rembg` cutout and WebP output to `static/players/{player_id}.webp` stay as they are.
* **New capabilities:**
  * Identity reference (IP-Adapter / face-ID adapters): keep a player's face across renders, so headshots can be aged each season or redone after a team change and still be the same person.
  * Pose/depth control (ControlNet): the same head size, angle and crop for every player, which the in-game portraits need (§9.4).
  * No per-image cost or batch queue, so re-renders are free.
* **Quality gate:** before switching, render one rookie class both ways and compare side by side, as in §7.4.

### 7.8 Nightly GPU Queue

The text model, the image model and Madden can't share the 16GB at once, so local GPU work runs as one queue, one model at a time, only while Madden isn't running:

1. Newsroom-side local text jobs held since the last run (Agent Wire, memory, scouting notes, GM text).
2. Headshots for new players and any re-renders (§7.7), then portrait conversion for the next mod build (§9.4).
3. Audio segments for the broadcast layer (pregame and postgame TTS, Phase 7).

The queue unloads each model before loading the next. An interactive job that can't wait executes against the resident ~1–2GB model or falls back to Gemini (§7.5). Gemini-side / hosted API newsroom articles don't use the GPU and run immediately after the weekly load.

### 7.9 Before the PC Arrives

Cap the thinking budget on the short Gemini calls (Agent Wire, agent memory, scouting notes, GM calls) now. They spend ~15× more tokens thinking than writing, and lowering that is a small code change that cuts cost without any hardware.

**Implementation (`engines/llm_config.py`):**
* Provides unified helpers `parse_model_thinking(model_str)`, `resolve_thinking_level(label, model, thinking_level)`, and `make_content_config(...)` supporting the `model:level` convention (e.g. `gemini-3.8-flash:low`).
* Callers configured:
  - `newsroom/agent_memory.py` `gemini_text()`: defaults to thinking level `low` (configurable via `AGENT_MEMORY_THINKING` env var or `model:level`).
  - `engines/gm_llm.py` `GeminiText` / `make_llm`: defaults to thinking level `low` for `gm_persona`, `gm_rationale`, `gm_negotiation`, `scouting_notes`, and `draft_rationale` (configurable via `GM_LLM_THINKING`, `GM_THINKING_<LABEL>`, or `model:level`).
  - `newsroom/social_writer.py` `write_post`: uses `make_content_config`, honoring configured `model:level` or unconstrained when unspecified.

**Measured Savings & Token Impact:**
* **Baseline averages from §7.1** (default Gemini 3.8 Flash unconstrained thinking):
  - `agent_wire` ([`newsroom/social_writer.py`](../../newsroom/social_writer.py)): avg 936 thinking tokens (1.7K in, 63 out)
  - `agent_memory` ([`newsroom/agent_memory.py`](../../newsroom/agent_memory.py)): avg 1,143 thinking tokens (1.1K in, 161 out)
  - `scouting_notes` ([`engines/scouting_narrative.py`](../../engines/scouting_narrative.py)): avg 1,049 thinking tokens (0.3K in, 75 out)
  - GM calls (`gm_persona`, `gm_rationale`, `gm_negotiation`, [`engines/gm_negotiation.py`](../../engines/gm_negotiation.py)): avg ~1,000 thinking tokens (0.3–1K in, 130–180 out)
* **First reading (2026-10-29 smoke test, §7.4):** at `low`, `gemini-3.8-flash` reported 0 thinking tokens on one call of each of the 7 tasks, all passing, $0.0015 in total. One call per task is not a measurement of savings or pass rates.
* **To measure:** Run the quality test harness (`tools/llm_compare.py`, §7.4) against the `madden_2036` archive:
  ```bash
  python3 -m tools.llm_compare --suite --tasks memory_takes memory_summary scouting_notes gm_rationale --season 2036 --week 20 --models gemini-3.8-flash gemini-3.8-flash:low --limit 5
  ```
  (≈ 40 Gemini calls across the 4 tasks; JR will run this).

---

## 8. Phase 6: Day 80 Costco Return / SFF Transition Roadmap

To transition from the temporary Costco prebuilt to your permanent, whisper-quiet Small Form Factor living-room build:

1. **Days 1–75 (Active Operation):**
   * Keep the Costco PC intact in its designated spot on the hardwood floor.
   * Keep the original shipping box and all styrofoam inserts safe in a closet.
   * Run your 2036–2037 franchise seasons and refine the save reader scripts.
2. **Days 60–75 (Black Friday / Holiday Sales Monitoring):**
   * Watch for holiday discounts on the four SFF components:
     * **Case:** Fractal Design Terra (Jade or Silver, 10.4L) or Lian Li / DAN A4-H2O (11L)
     * **Motherboard:** ASRock B850I Lightning WiFi (Mini-ITX AM5)
     * **Power Supply:** Corsair SF850 (2024 Platinum SFX)
     * **Cooler:** Noctua NH-L12S (Low-profile) or Cooler Master 240 Atmos
3. **Day 80 (Data Export & System Wipe):**
   * Run `./db/backup_db.sh` to grab the latest database snapshot.
   * Copy `CAREER-*` franchise saves and your Git repo to a flash drive.
   * In Windows 11: Go to **Settings $\rightarrow$ System $\rightarrow$ Recovery $\rightarrow$ "Reset this PC" $\rightarrow$ "Remove everything"**.
4. **Day 82 (Return to Costco):**
   * Repack the MSI Aegis into its original box with included mouse, keyboard, and cables.
   * Walk into any Costco warehouse for a **100% full `$2,200` refund**.
5. **Day 85 (Final Living-Room Deployment):**
   * Assemble the custom SFF PC and place it on the open shelf behind the framed print (or on the media console where the candle sits).
   * Restore the database snapshot. The entire gridiron universe continues without missing a beat.

---

## 9. Phase 7: Project Mu Broadcast & Portrait Layer (after the migration)

Madden's commentary is pre-recorded clips triggered inside the game engine. A mod can only swap one recording for another, so Madden's live play-by-play stays. Project Mu adds a color-commentary layer on top, built from what it knows and Madden doesn't: records, rivalries, contract years, hot seats, season storylines.

### 9.1 Build Order

1. **Pregame show:** a generated audio segment before kickoff from the newsroom's matchup primer (storylines, injuries, stakes, rivalry history). Needs no live game state.
2. **Halftime and postgame:** halftime from the scorebug state plus the save; postgame from the save's scoring plays (§5.3), if Madden 27 stores them.
3. **Radio-style recaps of simmed games** for `Mu_Network`, from the scoring plays. Simmed games are never played on screen, so they get recaps, not live calls.
4. **Live color layer** (§9.2), for games JR plays or watches.

### 9.2 Live Color Layer

```
[Madden running, commentary volume lowered]
          │
          ▼
[Scorebug reader] (screen capture, 1–2 fps)
  score, clock, quarter, down & distance, possession, timeouts
          │
          ▼
[Event triggers]
  score change, turnover, quarter end, two-minute warning,
  conversion on 3rd/4th-and-long, red-zone stand
          │
          ▼
[Line generator] (Project Mu context: records, rivalries, storylines)
          │
          ▼
[TTS] → played between snaps, ducking Madden's commentary
```

* There are usually 15–30 seconds between snaps, so 2–4 seconds of generation plus speech is acceptable.
* The scorebug is one fixed layout, a much simpler read than the menu screens the PS5 capture handles today.
* No memory reading or engine hooks: those need the anti-tamper module, work offline only and break with patches.

### 9.3 Constraints

* **GPU:** Madden is using the 5070 Ti during play (§7.5). Use cloud LLM and TTS for the live layer; pregame, postgame and recaps can run locally when Madden isn't running.
* **Voices:** several newsroom personas are modeled on real journalists. Broadcast voices are original synthetic voices, never clones of a real person's voice.
* **Player names:** Madden's own calls depend on the commentary IDs set by the teleporter (§4.3).

### 9.4 Headshots as In-Game Portraits

Player models are barely visible in play, so they only need to be close: the trait mapping in §4.3 (skin tone, hair, facial hair, build on a generic head) is enough, and custom 3D faces are dropped. What is visible is the player portrait on cards, menus and presentation, and that can show the Project Mu headshot.

**How portraits work (checked on `CAREER-GIANTS-AUTOSAVE`):**
* `Player.PLYR_PORTRAIT` picks the portrait image; all players use the `library_icons_brt` portrait library.
* Real NFL players have their own portrait IDs: 2,865 of 3,086 players have a unique ID (range up to 30,000).
* Madden-generated players share a small set of generic portraits (IDs around 3,300–3,950), and 92 players have 0 (no portrait).
* The community calls this field "Portrait ID"; with `PLYR_ASSETNAME` (the face scan) it is what portrait and cyberface mods key on, so mods ship ID lists and patchers that rewrite both fields per player (e.g. RFM's 300+ cyberfaces and portraits, Kevin's *Bulk Draft Class Portrait and Cyberface Assignment* from a CSV). MyFranchise Tool can also import a custom portrait image per player, keyed by Portrait ID ([`MMC_MOD_SURVEY.md`](../MMC_MOD_SURVEY.md) §4).

**Plan:**
1. By 2036 the real players behind those 2,865 unique slots are long retired, so their portrait images can be overwritten with Project Mu headshots. 2,865 slots cover the 1,829 active players plus several years of rookie classes; slots of players who retire are reused after that.
2. The teleporter gives each Project Mu player a slot and writes it to `PLYR_PORTRAIT`; the rookie import does the same each season after `headshots/rookie_headshots.py` runs.
3. A converter takes each headshot (already a transparent cutout, `static/players/{player_id}.webp`), resizes it to Madden's portrait size and encodes it in the game's texture format.
4. The portrait textures are replaced through MMC Modding Tools (the community's Frosty-based Madden 27 editor, updated for each title update; [FMT.Madden26Plugin](https://github.com/FMTDev/FMT.Madden26Plugin) is the alternative) and loaded as a mod with the anti-tamper module, offline only.

**One-portrait spike first,** on the PC: export one portrait with MMC Modding Tools to learn its size, format and how the ID maps to the image; replace it with a headshot; check it in-game on the player card. Also try the MyFranchise Tool custom-portrait import on one player and check whether Madden's own screens show it or only MFT does; if Madden shows it, it may replace steps 3–4. Portrait texture mods already exist for Madden 27 (WiiMaster's *Old Style Portraits*, Keanu Steez's roster/portrait pack), so the texture route is proven in principle.

---

# Part 2: V2 Integration Efforts

Part 1 (§1–§9) moves Project Mu onto the PC. Part 2 uses what the PC adds, mainly a local model whose calls cost nothing and have no rate limit, to make the universe feel lived-in. Two kinds of work become practical that a paid API makes you ration: **volume** (thousands of small voices) and **interaction** (JR talking to the world, live).

## V2.0 Ground Rules

* **Numbers stay deterministic.** Engines decide values, ratings, deals, draft boards and outcomes; the model writes prose, dialogue and choices among options the engines allow. This is the existing rule for GM agents ("the LLM never chooses or changes a deal") and scouting ("numbers are always deterministic; the LLM only rewrites prose"), extended to everything below.
* **Hallucination policy:** invent untracked narrative freely, never contradict data. Quotes, fan posts, photos and storylines must agree with box scores, injuries, contracts and records.
* **Where it runs:** batch work (images, audio, bulk text) goes on the nightly GPU queue (§7.8). Interactive work (press conferences, conversations, negotiations) uses the local model while JR is at the PC but not playing; otherwise it falls back to Gemini.
* **Quality gate:** each feature passes the §7.4 side-by-side check before it replaces anything Gemini does today.

## V2.1 Project Mu News Inside Madden

Write Project Mu's top stories into Madden's own news tables (`NewsCenterArticle`, `NewsCenterArticleData`: header text, author, people and team referenced, week, year, breaking flag; 38 rows in `CAREER-GIANTS-AUTOSAVE`), so the universe's headlines show on Madden's in-game news screens. Save editing only, no mod. **First check on the PC:** that Madden displays injected rows and doesn't overwrite them on the next Advance Week.

## V2.2 Press Conferences

After each Panthers game, newsroom personas question JR from the real box score, scoring plays and storylines, with follow-ups. JR's answers become quotes in that week's articles, and writers react to them. Reporters' memory (`newsroom/agent_memory.py`) carries over, so a reporter JR brushed off remembers it. Optional voice through the broadcast layer's TTS.

## V2.3 Player Voices

* Postgame quotes for every game, CPU-simmed included, from the real per-game lines (§5.3).
* Player accounts on Agent Wire next to the journalists: celebrations, subtweets, rookie excitement, contract-year frustration, injury updates.
* Players' tone follows their situation (contract, role, team direction, streaks) and their persona (V2.13).

## V2.4 Photography with Consistent Faces

Use each player's headshot as a face reference for the local image model (IP-Adapter / face-ID adapters, §7.7) so every image of a player has the same face: draft-night photos in the new team's cap, trophy and MVP shots, press conferences, retirement ceremonies, Hall of Fame busts, and a lead photo for feature articles. The same approach gives coaches and GMs faces once they have headshots.

## V2.5 Fans

Fans are already agents (§7.7 of the spec). V2 scales them up: comment sections under articles, a forum per team, reactions after big wins and losses, and a fan sentiment score per team that writers, press questions and owner messages can read. Fans react to the data and get louder as streaks build.

## V2.6 Radio and Podcasts

Daily radio segments and weekly two-host podcasts built from the week's data and articles, voiced with local TTS, produced overnight on the GPU queue and published on `Mu_Network`. Original synthetic voices only, never clones of real people.

## V2.7 A Recap for Every Game

A short write-up for all 16 games each week from the real box scores and scoring plays, plus "around the league" notes, alongside the newsroom's existing slate.

## V2.8 League Memory

A narrative search index over every stored article, take, and franchise/league record, so writers, players and broadcasters can pull authentic callbacks ("the last time Carolina came back from 17 down was 2031"). Feeds every feature here that writes text.

**Architecture (Built 2026-10-28):**
* **Storage:** PostgreSQL 14 table `league_memory` (`id`, `source_table`, `source_id`, `season`, `week`, `team_ids VARCHAR(8)[]`, `player_ids INT[]`, `text_chunk TEXT`, `embedding REAL[]`, `model VARCHAR(64)`, `created_at`, `UNIQUE(source_table, source_id)`). Uses standard Postgres arrays without requiring the `pgvector` extension; cosine similarity runs in Python/numpy.
* **Corpus Ingestion:** Incremental indexing CLI (`python3 -m newsroom.league_memory index [--limit N] [--season S] [--commit] [--dry-run] [--batch-size 100]`) indexing `news_feed`, `agent_memory_entries`, `franchise_records`, and `league_record_entries`. Skips unchanged rows based on chunk content and model hash. Initial backfill verified on `madden_test` (7,158 chunks, ~437k tokens, ~$0.065 cost).
* **Embedding Seam:** `embed_text(texts, model=...)` connects to Gemini embeddings (`gemini-embedding-001`), metered into `llm_usage` under source `memory_index`, with test mock seams (`set_mock_embedder`) and local embedding hooks (`set_local_embedder`) for PC migration.
* **Writer Tool:** `@tool def search_league_memory(query, team=None, player_id=None, seasons=None, top_k=5)` in `newsroom/db_tools.py` and `newsroom/league_memory.py`, registered in `SwarmDispatcher.tools`. Automatically hides future-week memories via `is_visible_week(season, week)` when in an article context.


## V2.9 Front-Office Inbox and Conversations

* **Inbox:** messages from the owner, agents and players: trade demands, holdout threats, an impatient owner after a losing streak, a veteran asking about their role. JR chooses how to respond; the response feeds morale, media stories and the hot seat inputs where the engines allow.
* **Conversations:** message a rival GM, a coach, a retired legend, a beat writer or a draft prospect, each answering from their persona, memory and the data.

## V2.10 Scouting Revamp

Today (spec §2.18): hidden true value `V`, consensus `C = V + e`, team boards, seeded events that explain each step's move, and optional Gemini rewrites for up to 8 movers a step.

* **Every prospect, every step:** with local calls free, every event note gets written, not just the top movers, and prospects get persistent personalities and backstories that carry from college to the NFL.
* **Team scouts as personas:** each front office gets scouts with their own voice and biases, writing reports that match their team board (`scout_team_boards`), so boards disagree in words as well as numbers.
* **Interviews:** JR interviews prospects at the combine and on visits; the prospect answers in character. The interview never changes `V`; at most it reveals already-hidden facts the engine allows (e.g. a `MEDICAL` or character flag).
* **Real college tape:** once the CFB 27 bridge runs (§6), reports cite the prospect's real college games and stats.

## V2.11 Trade Negotiations

Today (spec §3.3f): GM agents propose trades, `engines/trade_value.py` prices them, `gm_trade_negotiations` tracks patience, and the LLM writes rationale only.

* **Free-form talks:** multi-turn negotiation in the GM's voice, with counteroffers chosen from deals the trade-value engine accepts, and patience shown in tone before the GM walks away.
* **Personality-driven:** aggressiveness, team direction and front-office standing shape how a GM argues, bluffs and concedes.
* **Leaks and coverage:** talks can leak through `gm_trade_sightings` to insiders and fans, and failed talks become stories.
* **AI-to-AI deals with a paper trail:** trades between CPU teams get their own negotiation transcripts for the newsroom to report.

## V2.12 Team and League Narratives

A storyline layer that tracks arcs as state across weeks and seasons, each with beats that writers, press questions, player voices, the broadcast layer and the inbox can draw on:

* **Team arcs:** hot seat (spec §2.19), contract years, rebuilds and contention windows, rivalries, streaks, a rookie QB's first season.
* **League arcs:** a deep QB class, a dynasty under threat, parity, records being chased (`engines/records_engine.py`), the coaching carousel.
* **Inputs:** game scripts (spec §2.14), the season archive and team arc history, records, contracts, scouting, and Madden's own storyline tables (`Story`, `BranchingStory`, `FootballStory` in the Madden 27 schema; check on the PC what they hold).

## V2.13 Personalities Across the NFL Universe

**Status (2026-10-28):** Phases 1, 2, and 3 built. Writers and GMs use structured database tables (`persona_sliders`, `persona_traits`, `persona_views`, `persona_relationships`, `persona_trait_history`), a 50-trait validated catalogue (`newsroom/config/persona_traits.json`), deterministic seeding (`f"{type}:{id}"`), `#debug` review endpoints (`GET /api/debug/personas`, `POST /api/debug/personas/{type}/{id}`), and GM prompt/dialogue personality reflection.

Extend the persona and memory model the newsroom already uses (identity line, takes log, season summaries) beyond writers and GMs to:

* **Players:** stars and captains first, then anyone who speaks (V2.3, V2.2).
* **Coaches and coordinators:** from `coaches` and the coaching carousel.
* **Owners, agents and the commissioner:** for the inbox (V2.9) and league stories.
* **Scouts:** for V2.10.

Personas are generated once on the local model and stored; memory layers update as they act. Identity stays fixed, like `agent_memories.persistent_memory`. Player and coach personas also get numeric traits (work ethic, coachability, teacher, specialty, …) that feed progression (V2.14).

The personality model (Sims-style sliders with a budget, traits from a catalogue, views that apply only when relevant, mood from the week's data, relationships) and the agent loops are in [`PERSONALITY_PLAN.md`](PERSONALITY_PLAN.md).

## V2.14 Project Mu Owns Progression

Turn off Madden's player progression (XP sliders at 0, which live in the save's `ProgressionXPSlider` table; Madden's offseason regression overwritten from a snapshot) and decide every rating change in `engines/progression.py`: age, dev trait, a hidden ceiling seeded from scouting's `V`, playing time and game grade, injuries, and later coach and player personality traits. Changes are written through the save writer and stored with their causes for the newsroom. Plan: [`PROGRESSION_PLAN.md`](PROGRESSION_PLAN.md).

## V2.15 Project Mu Runs the League

Madden plays the games; Project Mu makes every front-office decision for all 32 teams and the save writer carries them out: trades without JR's manual Madden steps, the whole draft, the coaching carousel, re-signings and extensions with player agents, free agency, cuts, waivers and the practice squad, coach-set depth charts, retirements, injury durations, media award ballots, the cap and rookie scale. Needs sign and trade moves in the writer, a read-back check after every write, and either all 32 teams user-controlled or the CPU's automation off. JR runs his team from `Mu_Network`. Plan: [`LEAGUE_OFFICE_PLAN.md`](../not_started/LEAGUE_OFFICE_PLAN.md).

## V2.16 Suggested Order

| Order | Feature | Builds on |
| :--- | :--- | :--- |
| 1 | V2.1 News inside Madden | save writer (§4–§5) |
| 2 | V2.8 League memory | stored articles and stats |
| 3 | V2.13 Personalities (players, coaches) | agent memory |
| 4 | V2.2 Press conferences, V2.3 Player voices | V2.8, V2.13 |
| 5 | V2.4 Photography | local headshots (§7.7) |
| 6 | V2.12 Narratives | game scripts, records, hot seat |
| 7 | V2.11 Negotiations, V2.9 Inbox | GM agents, V2.12, V2.13 |
| 8 | V2.10 Scouting revamp | scouting engine, CFB bridge (§6) |
| 9 | V2.5 Fans, V2.6 Radio, V2.7 Every-game recaps | V2.8, TTS (§9) |
| alongside 3–7 | V2.14 Progression (engine first, then personality and coach terms) | save writer, OVR weights (§4.4), V2.13 |
| from the first PC season | V2.15 League office (trades, draft and coaching changes first; then the transactions engine, depth charts, retirements, awards) | save writer moves, V2.14 personalities, V2.9, V2.11 |
