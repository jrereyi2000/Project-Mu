# Project Mu: 4-Phase Deprecation & Lineage Audit Ledger

**Generated:** 2026-10-28  
**Updated:** 2026-10-29 (fresh 2034 save: teleport and Mu 2026–2036 history classified, §3b; Tier 4 moved to refactor-now)  
**Audit Scope:** Multi-generation evolution (`v0` $\to$ `v1` $\to$ `v2`), plus the abandoned `v2-teleport` branch and the discarded Mu history  
**Status:** Non-destructive. Phases 1–3 done; Phase 4 markers in place (`# @AUDIT-STATUS` headers, `pipeline/tombstone.py` decorators in `db/db_handler.py`). Nothing in Tiers 1–5 has been deleted yet.

---

## 1. Executive Summary & Definitions

This project has evolved across three distinct architectural paradigms:
- **`v0` ("Twitch stream based capture of PS5 game"):** OpenCV video stream ingestion (`cv2.VideoCapture`), Streamlink (`streamlink`), template matching classifier (`pipeline/template_classifier.py`), video batch scripts (`pipeline/batch_*.py`), stream ingestion (`pipeline/main.py`), and early Streamlit dashboards (`dashboards/`).
- **`v1` ("Remote control PS5 game via chiaki"):** `chiaki-ng` macro automation via AppleScript/held keypresses (`madden_capture.py`), Gemini vision OCR extraction (`extract.py`), weekly Remote Play orchestration (`pipeline/weekly_capture.py`), CSV capture loader (`pipeline/load_capture.py`), and Vision OCR extraction modules (`pipeline/extract_*_ocr.py`).
- **`v2` ("Move to PC - Native Save File I/O"):** Direct Madden PC `.CAREER-*` franchise save file parsing and editing via Node.js/`madden-franchise` (`pipeline/save_reader/`), directory save file watcher daemon (`pipeline/watch_save.py`), direct PostgreSQL save loader matching on `madden_pgid` (`pipeline/load_save.py`), universe teleportation/bridge suite (`pipeline/teleport.py`, etc.), and offline progression/OVR systems (`engines/progression.py`, `engines/ovr_weights.py`).

- **`v2-teleport` (abandoned 2026-10-29):** the 2037 offseason teleporter that would have rebuilt Mu's 2036 league in a PC save (`pipeline/teleport*.py`, `character_visuals.py`, `visual_edits.mjs`) and the PS5 attribute scan that fed it (`attribute_reader.py`, `load_attributes.py`).
- **`mu-history` (discarded 2026-10-29):** Project Mu's own 2026–2036 universe: the 2026–2034 history backfill (`engines/history_backfill/`, `load_career_totals.py`) and the data-only migrations that wrote its lore (coaches, coordinators, record names, pick owners).

**2026-10-29 decision (JR):** the PC starts a fresh Madden 27 franchise in 2034 and all of Mu's 2026–2036 history is dropped (stats inflated). The 2036 database is kept read-only as `madden_2036`. Everything that only served the teleport or that history is classified **`OBSOLETE_FRESH_SAVE`** (§3b) and marked in place with `@AUDIT-STATUS: OBSOLETE_FRESH_SAVE (...)`.

`v1` components were classified **`LEGACY_SHIM`** while 2036 was being finished on the PS5. That finish is cancelled, so they no longer need preserving: Tier 4 is now "refactor the shared pieces out, then prune" (§5).

---

## 2. The `v2` Root Map (Source of Truth)

The following roots represent the active `v2` system boundaries:

### A. Ingestion, Save I/O & Universe Teleportation
- `pipeline/watch_save.py` — Debounced save watcher daemon for PC franchise saves.
- `pipeline/save_reader/pc_save_reader.mjs` — Fast native franchise save reader generating `save.json`.
- `pipeline/save_reader/save_writer.mjs` — Direct franchise save file editor.
- `pipeline/save_reader/draft_tables.mjs` — Draft pick, player, and team extractor.
- `pipeline/load_save.py` — Atomic database loader for save snapshots via `madden_pgid`.
- `pipeline/teleport_draft.py` — Draft class and pick injection (Mu's scouted class and pick owners into the save). Stays; must stop importing `pipeline/teleport.py` (§3b).
- `pipeline/save_reader/contract_edits.mjs`, `record_edits.mjs`, `release.mjs` — Save edit kinds `save_writer.mjs` applies (League Office sign/trade/release builds on them).
- `pipeline/commentary_names.py` — Madden 27 commentary ID lookup and surname aliasing (Mu-named prospects, CFB 27 bridge; `server/api.py` and `server/napper_api.py` use `player_name_aliases_exist`).
- ~~`pipeline/teleport.py`, `teleport_appearance.py`, `teleport_contracts.py`, `character_visuals.py`, `attribute_reader.py`, `load_attributes.py`~~ — no longer roots: `OBSOLETE_FRESH_SAVE` (§3b).

### B. Offline Simulation & Progression
- `engines/ovr_weights.py` & `engines/ovr_weights_m27.json` — Madden 27 OVR NNLS formula engine (used by progression and the draft step, no longer by a teleport).
- `engines/progression.py` — Deterministic offline yearly player progression and regression engine. Calibrated on the 2035→2036 offseason (`madden_2036`); recalibrate on the fresh league. Must stop importing `pipeline/teleport.py` (§3b).
- `engines/llm_config.py` & `engines/llm_provider.py` — Ollama/Gemini dynamic model provider abstraction.

### C. Active Application, Serving & Swarm Ecosystem
- `server/api.py` — FastAPI REST backend (social, debug, napper, draft, front-office, history).
- `Mu_Network/` — React 19 + TypeScript + Vite frontend.
- `newsroom/weekly_run.py` & `newsroom/offseason_run.py` — Swarm orchestrationDesks.
- `newsroom/social_publish.py` — Agent Wire publishing engine.
- `newsroom/db_tools.py` & `newsroom/fantasy_tools.py` — 57 dynamic `@tool` ReAct agent research tools.
- `engines/*.py` — 30+ quantitative analytics engines (SRS, TWI, Excitement, COTY, Hot Seat, Scouting, GM Agents, Napper Fantasy).

---

## 3. Feature Supersession Matrix

| File / Symbol | Generation | Classification | Replaced By (v2 Path/Symbol) | Confidence | Evidence / Why It's Dead |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `pipeline/main.py` | v0 | `SUPERSEDED_DEAD` | `pipeline/watch_save.py`, `pipeline/load_save.py` | High | Twitch video stream ingestion loop using `streamlink` and `cv2`. Zero inbound imports from v2. |
| `pipeline/ingest_stream.py` | v0 | `SUPERSEDED_DEAD` | `pipeline/watch_save.py` | High | Video frame extractor from Twitch stream. Imported only by dead `sort_video.py`. |
| `pipeline/sort_video.py` | v0 | `SUPERSEDED_DEAD` | `pipeline/save_reader/pc_save_reader.mjs` | High | Video frame sorting script. Zero inbound imports from v2. |
| `pipeline/batch_historical.py` | v0 | `SUPERSEDED_DEAD` | `pipeline/load_save.py`, `engines/history_backfill/` | High | Batch historical video processor. Zero inbound imports from v2. |
| `pipeline/batch_menus.py` | v0 | `SUPERSEDED_DEAD` | `pipeline/save_reader/pc_save_reader.mjs` | High | Batch menu frame processor. Zero inbound imports from v2. |
| `pipeline/template_classifier.py` | v0 | `SUPERSEDED_DEAD` | `pipeline/save_reader/pc_save_reader.mjs` | High | OpenCV template matcher for screen frames. Zero inbound imports from v2. |
| `pipeline/roster_scanner.py` | v0 | `SUPERSEDED_DEAD` | `pipeline/save_reader/pc_save_reader.mjs`, `pipeline/load_save.py` | High | OpenCV roster video scanner. Zero inbound imports from v2. |
| `pipeline/schedule_scanner.py` | v0 | `SUPERSEDED_DEAD` | `pipeline/save_reader/pc_save_reader.mjs`, `pipeline/load_save.py` | High | OpenCV schedule video scanner. Zero inbound imports from v2. |
| `pipeline/heal_roster.py` | v0 | `SUPERSEDED_DEAD` | `pipeline/load_save.py` | High | Heuristic roster healing script for video OCR drops. Superseded by PGID direct mapping. |
| `dashboards/admin_dashboard.py` | v0 | `SUPERSEDED_DEAD` | `server/debug_api.py`, `Mu_Network` (#debug UI) | High | Streamlit admin dashboard. Superseded by FastAPI + React network UI. |
| `dashboards/espn_dashboard.py` | v0 | `SUPERSEDED_DEAD` | `server/api.py`, `Mu_Network` | High | Streamlit ESPN-style dashboard. Superseded by Mu_Network. |
| `dashboards/__init__.py` | v0 | `SUPERSEDED_DEAD` | `server/api.py` | High | Package init for dead Streamlit dashboards. |
| `db/db_handler.py:create_active_game` | v0 | `SUPERSEDED_DEAD` | `pipeline/load_save.py:load_snapshot` | High | Only called by dead `pipeline/main.py` and `tests/test_db.py`. |
| `db/db_handler.py:update_game_meta` | v0 | `SUPERSEDED_DEAD` | `pipeline/load_save.py:load_snapshot` | High | Only called by dead `pipeline/main.py`. |
| `db/db_handler.py:upsert_schedule` | v0 | `SUPERSEDED_DEAD` | `pipeline/load_save.py:load_snapshot` | High | Only called by dead `pipeline/main.py`. |
| `db/db_handler.py:upsert_roster` | v0 | `SUPERSEDED_DEAD` | `pipeline/load_save.py:load_snapshot` | High | Only called by dead `pipeline/main.py` and dead `roster_scanner.py`. |
| `db/db_handler.py:upsert_team_stats` | v0 | `SUPERSEDED_DEAD` | `pipeline/load_save.py:load_snapshot` | High | Only called by dead `pipeline/main.py`. |
| `db/db_handler.py:upsert_player_stats` | v0 | `SUPERSEDED_DEAD` | `pipeline/load_save.py:load_snapshot` | High | Only called by dead `pipeline/main.py`, `batch_historical.py`, and `test_db.py`. |
| `db/db_handler.py:emit_game_completed` | v0 | `SUPERSEDED_DEAD` | `pipeline/watch_save.py:process_save` | High | Redis event emitter only called by dead `pipeline/main.py` and `tests/test_db.py`. |
| `pipeline/templates/*.png` | v0 | `ORPHANED_ARTIFACT` | NONE | High | 10 template PNG crops used solely by dead `template_classifier.py`. |
| `tools/vision_cells.py` | v1 | `ORPHANED_ARTIFACT` | NONE | High | Experimental one-off per-cell OCR script from Oct 13. |
| `tools/vision_dump.py` | v1 | `ORPHANED_ARTIFACT` | NONE | High | Experimental one-off Apple Vision dump script from Oct 13. |
| `tools/debug/debug_flicker.py` | v0 | `ORPHANED_ARTIFACT` | NONE | High | One-off flicker debug script for video capture. |
| `tools/debug/extract_schedule_weeks.py` | v1 | `ORPHANED_ARTIFACT` | NONE | High | One-off schedule frame extraction script. |
| `tools/debug/read_block_17.py` | v1 | `ORPHANED_ARTIFACT` | NONE | High | One-off block reading script. |
| `tools/debug/__init__.py` | v0 | `ORPHANED_ARTIFACT` | NONE | High | Empty init file in tools debug directory. |
| `WEEKLY_RUN` | v0/v1 | `ORPHANED_ARTIFACT` | NONE | High | 735KB static text log file left at repo root from early runs. |
| `Panthers Full History.pdf` | v0/v1 | `ORPHANED_ARTIFACT` | NONE | High | 14.4MB PDF reference document stored at root. |
| `PROGRESS.md` | v0/v1 | `ORPHANED_ARTIFACT` | `PROJECT_SPEC.md` | High | Outdated progress notes from Oct 15 superseded by `PROJECT_SPEC.md`. |
| `tests/test_db.py` | v0 | `ORPHANED_ARTIFACT` | `tests/test_load_save.py` | High | Test file testing superseded `db_handler` methods (`upsert_player_stats`, `emit_game_completed`). |
| `madden_capture.py` | v1 | `LEGACY_SHIM` | `pipeline/save_reader/pc_save_reader.mjs` | High | PS5 Remote Play capture runner via `chiaki-ng`. Active legacy shim for 2036 finish. |
| `extract.py` | v1 | `LEGACY_SHIM` | `pipeline/save_reader/pc_save_reader.mjs` | High | Gemini Vision OCR extraction pass over screenshots. Active legacy shim for 2036 finish. |
| `pipeline/weekly_capture.py` | v1 | `LEGACY_SHIM` | `pipeline/watch_save.py` | High | PS5 weekly runner; `watch_save.py` imports `run_weekly_engines` and `step_articles` from it. |
| `pipeline/load_capture.py` | v1 | `LEGACY_SHIM` | `pipeline/load_save.py` | High | PS5 capture CSV loader; `load_save.py` imports `team_line_totals` from it. |
| `pipeline/ocr_extractor.py` | v0/v1 | `LEGACY_SHIM` | `pipeline/save_reader/pc_save_reader.mjs` | High | Pydantic models imported by `db/db_handler.py:7`. |
| `pipeline/resolve_player.py` | v1 | `LEGACY_SHIM` | `pipeline/load_save.py` | High | Interactive CLI for resolving OCR dropped names. Superseded by PC PGID matching. |
| `pipeline/capture_matching.py` | v1 | `LEGACY_SHIM` | `pipeline/load_save.py` | High | Name normalization/fuzzy matching for OCR captures. |
| `pipeline/extract_draft_results.py` | v1 | `LEGACY_SHIM` | `pipeline/save_reader/draft_tables.mjs` | High | Apple Vision OCR for Draft Recap screen. |
| `pipeline/extract_injuries_ocr.py` | v1 | `LEGACY_SHIM` | `pipeline/load_save.py` | High | Apple Vision OCR for injury screen. Superseded by save `Player.Injury`. |
| `pipeline/extract_prospects_ocr.py` | v1 | `LEGACY_SHIM` | `pipeline/save_reader/draft_tables.mjs` | High | Apple Vision OCR for draft board screen. |
| `pipeline/extract_team_overalls_ocr.py` | v1 | `LEGACY_SHIM` | `pipeline/save_reader/pc_save_reader.mjs` | High | Apple Vision OCR for team overalls screen. |
| `pipeline/extract_transactions_ocr.py` | v1 | `LEGACY_SHIM` | `pipeline/save_reader/pc_save_reader.mjs` | High | Apple Vision OCR for League Transactions. |
| `pipeline/ir_returns.py` | v1 | `LEGACY_SHIM` | `pipeline/load_save.py` | High | Workaround script for Madden PS5 IR heal glitch. |
| `pipeline/load_career_totals.py` | v1 | `LEGACY_SHIM` | `pipeline/save_reader/pc_save_reader.mjs` | High | Loads Career Stats capture into `player_career_totals`. |
| `pipeline/money.py` | v1 | `LEGACY_SHIM` | `pipeline/save_reader/pc_save_reader.mjs` | High | Parses OCR money strings (`$16.8M`). Superseded by save raw numbers. |
| `pipeline/transactions_parse.py` | v1 | `LEGACY_SHIM` | `pipeline/save_reader/pc_save_reader.mjs` | High | Parses OCR transaction grammar. |
| `pipeline/contracts.py` | v1 | `LEGACY_SHIM` | `pipeline/load_save.py` | High | Contract upsert logic shared with capture loader; imported by `load_save.py`. |
| `pipeline/trade_moves.py` | v1 | `LEGACY_SHIM` | `pipeline/save_reader/save_writer.mjs` | High | Trade planner for transactions load; imported by `engines/front_office.py`. |
| `pipeline/ingest_prospects.py` | v1 | `LEGACY_SHIM` | `pipeline/save_reader/draft_tables.mjs` | High | Draft prospect CSV parser. |
| `pipeline/draft_board.py` | v1 | `LEGACY_SHIM` | `pipeline/save_reader/draft_tables.mjs` | High | Draft board helper called by draft tools and mock draft engine. |
| `pipeline/draft_results.py` | v1 | `LEGACY_SHIM` | `pipeline/save_reader/pc_save_reader.mjs` | High | Round 1 draft results validator. |
| `pipeline/notify.py` | v1 | `LEGACY_SHIM` | `pipeline/watch_save.py:send_notify` | High | Phone notifications via ntfy. |
| `engines/game_stat_generator.py` | v1 | `LEGACY_SHIM` | `pipeline/load_save.py` | High | Synthesizes game lines from season totals; called by `weekly_capture.run_weekly_engines`. |
| `engines/backfill_team_stats.py` | v1 | `LEGACY_SHIM` | `pipeline/load_save.py` | High | CLI team stat backfill utility. |
| `tools/check_capture_load.py` | v1 | `LEGACY_SHIM` | `tests/test_load_save.py` | High | Capture verification script. |
| `tools/rebuild_backfill.py` | v1 | `LEGACY_SHIM` | `pipeline/load_save.py` | High | Script to clear and re-run backfilled lines. |
| `tools/debug/validate_draft_recap_flow.py` | v1 | `LEGACY_SHIM` | `pipeline/save_reader/test/read_save.test.mjs` | High | Remote play draft recap flow validator. |

---

## 3b. Fresh-Save Obsolescence (`v2-teleport`, `mu-history`; JR, 2026-10-29)

The PC starts a fresh 2034 franchise; nothing is teleported and Mu's 2026–2036 history is dropped (§1). Evidence is real import analysis on 2026-10-29 (who imports each file, outside its own tests). Each `OBSOLETE_FRESH_SAVE` file carries an `@AUDIT-STATUS` / `@REPLACED-BY` header (comment only; no behavior changed). JSON files can't carry a comment and are listed here only.

### Obsolete: the teleport (`v2-teleport`)

| File / Symbol | Classification | Replaced By | Evidence |
| :--- | :--- | :--- | :--- |
| `pipeline/teleport.py` | `OBSOLETE_FRESH_SAVE` (prune after extract) | NONE | The teleporter itself (old plan §4.3). Still imported by `engines/progression.py` (`TO_MADDEN_POSITION`, `madden_archetype`, `settle_archetype`) and `pipeline/teleport_draft.py` (`ANYWHERE`, `DSN`, `NEIGHBOUR`, `NEIGHBOURS`, `TO_MADDEN_POSITION`, `player_edit`), so those helpers move out first (decouplings below). |
| `pipeline/teleport_appearance.py` | `OBSOLETE_FRESH_SAVE` | NONE | Gives Mu players a donor's head and body. Imported only by `teleport.py` and its test. |
| `pipeline/teleport_contracts.py` | `OBSOLETE_FRESH_SAVE` | NONE | Writes Mu contracts onto donors. Imported only by `teleport.py` and tests. `contract_edits.mjs` (the save edit kind) stays. |
| `pipeline/character_visuals.py` | `OBSOLETE_FRESH_SAVE` | NONE | `CharacterVisuals.RawData` skin-tone codec for the teleport's appearance step. Imported only by `teleport_appearance.py` and its test. |
| `pipeline/save_reader/visual_edits.mjs` (+ `test/visual_edits.test.mjs`) | `OBSOLETE_FRESH_SAVE` | NONE | The `visuals` edit kind. Called only from `character_visuals.py`; `save_writer.mjs` imports it statically, so drop that import and the `visuals` branch when pruning. |
| `pipeline/attribute_reader.py`, `pipeline/attribute_digits.json` | `OBSOLETE_FRESH_SAVE` | NONE | PS5 attribute-scan digit reader (old plan §2.3). Imported by `madden_capture.py` (v1 shim) and `load_attributes.py`. |
| `pipeline/load_attributes.py` | `OBSOLETE_FRESH_SAVE` | NONE | Loads the scan into `player_attributes`. Imported only by its test. |
| `db/migrations/2027_01_06_player_attributes.sql`, table `player_attributes` | `OBSOLETE_FRESH_SAVE` (schema) | NONE | Only the scan writes it and only the teleport reads it. Drop the table from `db/init_db.sql` at prune time. |
| `tests/test_teleport.py`, `test_teleport_appearance.py`, `test_teleport_contracts.py`, `test_teleport_release.py`, `test_attribute_reader.py`, `test_load_attributes.py` | `OBSOLETE_FRESH_SAVE` | NONE | Tests of the files above. `tests/test_teleport_draft.py` stays with the draft step. |
| `madden_capture.py --only attributes` | `OBSOLETE_FRESH_SAVE` (branch) | NONE | Goes with `madden_capture.py` (Tier 4). |

### Obsolete: Mu's 2026–2036 history (`mu-history`)

| File / Symbol | Classification | Replaced By | Evidence |
| :--- | :--- | :--- | :--- |
| `engines/history_backfill/` (all modules) | `OBSOLETE_FRESH_SAVE` | NONE | Synthesizes the 2026–2034 team arc, player lines and All-Pros. Imported only by itself and `tests/test_history_*.py`; reads `data/history/` (also only used here). |
| `data/history/rosters_2026.csv`, `start_2026.csv`, `nfl_careers_through_2025.csv` | `OBSOLETE_FRESH_SAVE` (data) | NONE | Inputs of `history_backfill` only. Check `nfl_careers_through_2025.csv` for reuse (real careers) before deleting. |
| `pipeline/load_career_totals.py` | `OBSOLETE_FRESH_SAVE` (also v1 `LEGACY_SHIM`) | `pipeline/save_reader/pc_save_reader.mjs` | PS5 Career Stats capture loader. Imported only by `history_backfill/remainder.py` and its test. The save has career tables. |
| `tests/test_history_career.py`, `test_history_player_lines.py`, `test_history_sources.py`, `test_history_team_arc.py` | `OBSOLETE_FRESH_SAVE` | NONE | Tests of the above. `tests/test_history_api.py` (record book and awards endpoints) stays. |
| Schema migrations `2026_12_26_player_career_totals`, `2026_12_28_history_team_arc`, `2026_12_29_history_player_lines`, `2026_12_30_history_player_rows` | `OBSOLETE_FRESH_SAVE` (schema) | NONE | Staging tables of the backfill. Drop from `db/init_db.sql` at prune time, after checking nothing on the site reads them. |
| Data-only migrations `2026_10_24_player_id_keys.py`, `2026_10_27_name_casing.py`, `2026_10_31_coach_baseline`, `2026_11_01_coach_schemes`, `2026_11_03_whitfield_2034`, `2026_11_09_coach_full_names`, `2026_11_10_whitfield_tendencies`, `2026_11_11_whitfield_ratings`, `2026_11_16_record_book_names`, `2026_11_17_record_book_names_2`, `2026_11_21_rename_colliding_players.py`, `2026_11_22_rename_colliding_prospects.py`, `2026_11_27_whitfield_bears_coordinator_lore`, `2026_11_28_panthers_bears_coordinators_update`, `2026_11_29_panthers_history_backfill`, `2026_11_29_panthers_shavon_bell_dc`, `2026_11_30_panthers_2034_playoff_statlines`, `2026_12_01_coach_tenures_correction`, `2026_12_11_draft_pick_owners`, `2026_12_14_coordinator_tenure_repair`, `2026_12_15_colts_oc_evan_marsh`, `2026_12_16_name_2036_coordinators`, `2026_12_20_marcus_whitfield_full_control`, `2026_12_25_saleh_2025_baseline`, `2026_12_27_career_fit_fixes`, `2026_12_29_franklin_2032_record`, `2027_01_01_record_book_implied` | `OBSOLETE_FRESH_SAVE` (data) | `db/init_db.sql` + `db/seed_clean_db.py` | Contain no schema of their own (the trigger and indexes `2026_10_24_player_id_keys.py` creates are already in `init_db.sql`); they only insert or fix rows of the old universe (Whitfield, Panthers/Bears/Colts coordinators, 2034 coach counters, 2036 pick owners, name fixes). A fresh database takes its schema from `init_db.sql` (matched to the migrated schema 2026-10-29) and its starting data from the clean seed. |
| `db/migrations/migrate_db.py`, `migrate_player_stats.py`, `migrate_season_stats.py` | `PRUNE_FILE` (v0) | `db/init_db.sql` | One-off scripts from the original database, hardcoded to `dbname=madden`. |
| `db/migrations/migrate_super_bowl_week.py` | `PRUNE_FILE` (v1) | NONE | One-off data fix (Super Bowl week 22 → 23) on the old database. |

### Kept (checked, still used by the fresh save)

| File | Why it stays |
| :--- | :--- |
| `pipeline/teleport_draft.py` (+ `tests/test_teleport_draft.py`) | Writes Mu's scouted draft class and pick owners into the save every year (Fresh Save Strategy §2.3 item 4). Needs the decoupling below; consider renaming to `pipeline/draft_sync.py`. |
| `engines/ovr_weights.py`, `engines/ovr_weights_m27.json` | Used by `engines/progression.py` and the draft step. Docstring still describes the teleport. |
| `engines/progression.py` | Mu owns progression (V2.14). Recalibrate on the fresh league; decouple from `teleport.py`. |
| `pipeline/commentary_names.py`, `db/migrations/2027_01_07_player_name_aliases.sql` | Commentary IDs for Mu-named prospects and the CFB 27 bridge; `player_name_aliases_exist` is used by `server/api.py` and `server/napper_api.py`. Its `report`/`apply` run on the 2036 roster (old PRE_PC_WORK_2 D3) is obsolete; the tool is not. |
| `pipeline/save_reader/save_writer.mjs`, `contract_edits.mjs`, `record_edits.mjs`, `release.mjs`, `draft_tables.mjs`, `lib.mjs` | The general save writer and its edit kinds; the draft step and League Office (sign/trade/release) use them. |
| `engines/season_archive.py`, `engines/season_ledger.py` | Season rollover and retirements for every season, not just the old ones. Called from `pipeline/weekly_capture.py` (Tier 4 refactor must carry them over). |
| `engines/game_stat_generator.py` | Stays `LEGACY_SHIM`: the fallback for weeks without per-game rows (PC plan §5.3). |
| `db/migrations/2026_10_25_former_players.sql`, `2026_10_19_roundtable_panel_agent.sql`, `2026_10_22_agent_wire_social.sql`, `2027_01_07_writer_identity_lines.sql` | Newsroom and Agent Wire data (writers, former-player accounts), not universe history. `db/seed_clean_db.py` doesn't load them; review whether the fresh seed should. |
| Mixed schema + data migrations (`2026_10_30_coaches`, `2026_11_05_coach_tendencies`, `2026_11_07_coach_2034_totals`, `2026_12_19_charles_grant_full_control`, `2026_12_31_awards_source`) | Their schema lives on in `init_db.sql`; the data parts are old-universe only. Left unmarked; prune with the rest of the migration history if migrations are ever squashed. |

### Seed problem found (not fixed here)

`db/seed_clean_db.py:seed_records` runs `2026_10_28_league_record_book.sql`, which loads **Mu's own 2026–2035 record book** (`source = 'BOOK'`) into the fresh database. That contradicts dropping the history. The seed should stop running it (the file stays for `madden_2036`). Owned by the watcher/auto-init work.

### Decouplings needed before pruning

1. Move `TO_MADDEN_POSITION`, `NEIGHBOURS`/`NEIGHBOUR`/`ANYWHERE`, `madden_archetype`, `settle_archetype`, `player_edit` (and the helpers it calls: `split_name`, `height_inches`, `DEV_TO_MADDEN`, `WEIGHT_OFFSET`) from `pipeline/teleport.py` into a shared module (e.g. `pipeline/madden_player_edit.py`); repoint `engines/progression.py` and `pipeline/teleport_draft.py`. `teleport_draft.py` also takes `DSN` from `teleport.py` (and defaults to `dbname=madden`): read it from `DATABASE_URL` directly.
2. `pipeline/save_reader/save_writer.mjs`: remove the static import of `visual_edits.mjs` and the `visuals` edit branch.
3. `madden_capture.py`: drop `--only attributes` and its `attribute_reader` import (or prune the whole file, Tier 4).
4. Drop `player_attributes`, `player_career_totals` and the history staging tables from `db/init_db.sql`; check `server/history_api.py` and `newsroom/db_tools.py` first.

---

## 4. Schema & Config Bloat

### A. Dead Database Handler Methods & Imports
1. **`db/db_handler.py:4` (`import redis`) & `self.redis_client = redis.Redis.from_url(...)`:**
   - Redis was used in `v0` solely for `emit_game_completed()` in `pipeline/main.py`. Neither `v1` nor `v2` uses Redis. Every caller instantiating `DatabaseHandler` connects to Redis pointlessly.
2. **`db/db_handler.py:7` (`from pipeline.ocr_extractor import PlayerStat, TeamStat, GameMeta, RosterPlayer`):**
   - Imported at module level but only referenced by dead `v0` methods (`upsert_player_stats`, `upsert_team_stats`, `update_game_meta`).
3. **Dead Methods in `db/db_handler.py`:**
   - `create_active_game`, `update_game_meta`, `upsert_schedule`, `upsert_roster`, `upsert_team_stats`, `upsert_player_stats`, `emit_game_completed`.

### B. Obsolete Pydantic Schemas (`pipeline/ocr_extractor.py`)
- `PlayerStat`, `ScheduleGame`, `ScheduleExtraction`, `BoxScoreExtraction`, `GameMeta`, `TeamStat`, `RosterPlayer`, `RosterExtraction`.
- These were designed for Gemini Vision structured parsing of video screenshots in `v0`. In `v2`, `load_save.py` ingests Madden's native data model directly from JSON.

### C. Config & Environment Variable Bloat
- `REDIS_URL`: Present in `.env` and `DatabaseHandler`, completely unused by `v2`.
- `CHIAKI_HOST` & `CHIAKI_HOST_IP`: Used by `madden_capture.py` (`v1`). Obsolete now (PS5 retired 2026-10-29); superseded by `MADDEN_SAVES_DIR`. Still in `.env`, `README.md` and `docs/USAGE_GUIDE.md`; remove with `madden_capture.py`.

---

## 5. Quarantine Plan

### Tier 1: Safe to Move to `/_deprecated` Immediately (Zero Inbound Edges from `v2` or `v1`)
**Status 2026-10-29: not done.** All 13 are still in place (marked `PRUNE_FILE`); no `_deprecated/` folder exists.
These 12 files and 1 template folder have zero runtime consumers in the active codebase:
1. `dashboards/admin_dashboard.py`
2. `dashboards/espn_dashboard.py`
3. `dashboards/__init__.py`
4. `pipeline/main.py`
5. `pipeline/ingest_stream.py`
6. `pipeline/sort_video.py`
7. `pipeline/batch_historical.py`
8. `pipeline/batch_menus.py`
9. `pipeline/template_classifier.py`
10. `pipeline/templates/` (entire folder: 10 PNG templates)
11. `pipeline/roster_scanner.py`
12. `pipeline/schedule_scanner.py`
13. `pipeline/heal_roster.py`

### Tier 2: Orphaned Artifacts Safe to Archive or Delete Immediately
**Status 2026-10-29: partly done.** Items 1–3 are gone from the repo; items 4–9 are still in place (marked `PRUNE_FILE`).
1. ~~`WEEKLY_RUN` (735KB log artifact)~~ (gone)
2. ~~`Panthers Full History.pdf` (14.4MB PDF)~~ (gone)
3. ~~`PROGRESS.md` (superseded notes)~~ (gone)
4. `tools/vision_cells.py`
5. `tools/vision_dump.py`
6. `tools/debug/debug_flicker.py`
7. `tools/debug/extract_schedule_weeks.py`
8. `tools/debug/read_block_17.py`
9. `tools/debug/__init__.py`

### Tier 3: Inline Dead Methods Requiring Tombstone Instrumentation
Do NOT delete yet. Mark with `@tombstone` runtime tripwire and `# @AUDIT-STATUS: PRUNE_SYMBOL` in Phase 4 (**done**: all 7 carry the marker, plus the Redis client as `DEAD_BRANCH`):
- `db/db_handler.py`:
  - `create_active_game`
  - `update_game_meta`
  - `upsert_schedule`
  - `upsert_roster`
  - `upsert_team_stats`
  - `upsert_player_stats`
  - `emit_game_completed`

### Tier 4: Transitional Legacy Shims (`v1`)
**Changed 2026-10-29:** the PS5 franchise will not be finished, so nothing here needs preserving. Refactor the shared pieces out now, then prune the v1 files (§3 `LEGACY_SHIM` rows: `madden_capture.py`, `extract.py`, the `extract_*_ocr.py` modules, `resolve_player.py`, `capture_matching.py`, `ir_returns.py`, `load_career_totals.py`, `money.py`, `transactions_parse.py`, `tools/check_capture_load.py`, `tools/rebuild_backfill.py`, `tools/debug/validate_draft_recap_flow.py`) and the `CHIAKI_*` settings. Keep `engines/game_stat_generator.py` as the missing-week fallback. The refactor:
- Extract `run_weekly_engines` and `step_articles` out of `pipeline/weekly_capture.py` into a shared engine module (`pipeline/weekly_engines.py`).
- Extract `team_line_totals` out of `pipeline/load_capture.py` into a shared calculation module.
- Strip `pipeline/ocr_extractor.py` import from `db/db_handler.py`.
- Before pruning, check each shim's other importers (e.g. `pipeline/load_capture.py` is imported by `engines/dev_quality.py`, `draft_room.py`, `fo_quality.py`, `fo_standing.py`, `mock_draft_engine.py`, `scouting_engine.py` and `pipeline/load_save.py`; `pipeline/weekly_capture.py` by `pipeline/watch_save.py` and `server/debug_api.py`) and move what they use.

### Tier 5: Fresh-Save Obsolete (`OBSOLETE_FRESH_SAVE`, added 2026-10-29)
Everything in §3b's two obsolete tables. Order:
1. Do the §3b decouplings (shared Madden player-edit helpers out of `teleport.py`, `visual_edits.mjs` out of `save_writer.mjs`).
2. Stop `db/seed_clean_db.py` loading Mu's 2026–2035 record book.
3. Delete the teleport files, the attribute scan, `engines/history_backfill/`, `data/history/` (after the reuse check), their tests, and the old-universe data migrations.
4. Drop the obsolete tables from `db/init_db.sql`. `madden_2036` keeps its data; it's an archive and is never migrated forward.
