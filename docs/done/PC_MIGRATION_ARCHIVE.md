# PC Migration Plan: Archived Sections

**Archived 2026-10-30.** These sections of [`PC_MIGRATION_PLAN.md`](./PC_MIGRATION_PLAN.md) are obsolete: on 2026-10-29 JR decided against moving the 2026–2036 universe to the PC (no teleport, no PS5 finish). They are kept with their original numbering because code comments and older plans cite them (`§2.3`, `§4.3`, …). The code they describe is marked `OBSOLETE_FRESH_SAVE` in [`AUDIT_LEDGER.md`](../../AUDIT_LEDGER.md) §3b; removing it belongs to the audit plan after V3, not to any pipeline version.

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

*2026-10-29: the universe no longer moves (decision above). The PC starts a fresh 2034 franchise; the save loader, CFB 27 bridge and local-model goals stand.*

---

## 2. Phase 0: Pre-Arrival Preparation (Mac)

> **OBSOLETE (2026-10-29), delete with the teleport code.** The fresh 2034 save drops the 2036 finish (§2.1), the final Mac backup (§2.2; the last 2036 state is `madden_2036` on the PC, from `backup_latest.sql`, week 21) and the PS5 attribute scan (§2.3). §2.4 stays as the record of the finished pre-PC work; items 1 (QB OVR refit), 4–11 still serve the fresh save, items 2–3 (surname rename, skin tone) only served the teleport.

### 2.1 Finish 2036 on the PS5
Play the Conference Championships and the Super Bowl on the PS5 and run the normal capture + newsroom pipeline through the Week 23 slate, so 2036 concludes in Project Mu exactly as every earlier season did (season archive, awards, records).

### 2.2 Complete Database Snapshot
Dump the canonical database (all 11 seasons, players, contracts, historical stats, articles, awards) to an archival SQL file:
```bash
./db/backup_db.sh
```
*Verify that `db/backups/madden_backup_<timestamp>.sql` exists and has a plausible size.*

### 2.3 PS5 Attribute Scan (JR decision 2026-10-28)

> **OBSOLETE (2026-10-29):** the scan fed the teleport. `pipeline/attribute_reader.py`, `pipeline/load_attributes.py`, `pipeline/attribute_digits.json`, `madden_capture.py --only attributes` and the `player_attributes` table are marked for deletion.
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

## 4. Phase 2: The 2037 Offseason Teleporter

> **OBSOLETE (2026-10-29), delete with the teleport code.** No universe moves to PC (decision at the top). What survives: `seasonYearSetup.exe` and the roster-size mod from §4.2 (now used to start the fresh franchise in 2034); the OVR formulas of §4.4 (`engines/ovr_weights.py`, used by `engines/progression.py`); the draft step of §4.3 (`pipeline/teleport_draft.py`, Mu's scouted class into the save); and `players.madden_pgid` from §4.5, which `pipeline/load_save.py` writes for every player it loads. The rest (`pipeline/teleport.py`, `teleport_appearance.py`, `teleport_contracts.py`, `character_visuals.py`, `save_reader/visual_edits.mjs`) is marked `OBSOLETE_FRESH_SAVE` in [AUDIT_LEDGER.md](../../AUDIT_LEDGER.md) §3b.

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

The fit picks which ratings to edit and by how much; §4.3 Step 3 settles the last point.

**Empirically Verified (2026-10-30 on `CAREER-2026SB-MODIFIED-AUTOSAVE`):** Tested whether Madden recomputes OVR from ratings on load. In `CAREER-2026SB-MODIFIED`, Bryce Young's individual passing and athletic attributes were substantially edited via `save_writer.mjs` while `Player.OverallRating` was left at its original static value of `82`.
1. *In-Game Behavior:* Upon loading the save into Madden 27 on PC, Bryce Young's displayed overall rating dynamically recalculated to a morale-adjusted **93 OVR** in the UI, roster screens, and depth charts. Madden's runtime simulation engine calculates effective overall dynamically from the 54 individual ratings, factoring in archetype weights, team scheme fit, and morale modifiers.
2. *Formula Agreement:* Project Mu's reverse-engineered formula (`engines/ovr_weights.py`) predicted an exact base overall of **94 OVR** (`QB_FieldGeneral`: 94, `QB_StrongArm`: 93, `QB_Improviser`: 90, `QB_Scrambler`: 89) for those modified ratings, verifying within $\pm 1$ of the in-game calculation.
3. *Dual-Write Protocol:* Despite runtime recalculation in the UI, `save_writer.mjs` must still write `OverallRating`, `OverallGrade0-4`, and `PlayerType` directly alongside rating edits (`--write-ovr`). Raw binary database scans, external snapshot readers (`load_save.py`), and Madden CPU trade/roster AI evaluate the stored table column directly. Writing both guarantees 100% parity between raw save inspection and in-game rendering.

### 4.5 PGID Mapping

New column `players.madden_pgid` (Madden's player ID on the PC save: `Player.PresentationId`, unique for all 3,086 players in `CAREER-GIANTS-AUTOSAVE`; stability across Advance Week and season rollover is a §5.4 check), written by the teleporter for every player it places and by the weekly loader for rookies and new free agents. Project Mu's own `player_id` stays the primary key; the PGID is only the join to the save.
