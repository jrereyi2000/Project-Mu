# Project Mu: 4-Phase Deprecation & Lineage Audit Ledger

**Generated:** 2026-10-28  
**Audit Scope:** Multi-generation evolution (`v0` $\to$ `v1` $\to$ `v2`)  
**Status:** Non-Destructive Audit Complete (Phases 1–3). Phase 4 instrumentation pending confirmation.

---

## 1. Executive Summary & Definitions

This project has evolved across three distinct architectural paradigms:
- **`v0` ("Twitch stream based capture of PS5 game"):** OpenCV video stream ingestion (`cv2.VideoCapture`), Streamlink (`streamlink`), template matching classifier (`pipeline/template_classifier.py`), video batch scripts (`pipeline/batch_*.py`), stream ingestion (`pipeline/main.py`), and early Streamlit dashboards (`dashboards/`).
- **`v1` ("Remote control PS5 game via chiaki"):** `chiaki-ng` macro automation via AppleScript/held keypresses (`madden_capture.py`), Gemini vision OCR extraction (`extract.py`), weekly Remote Play orchestration (`pipeline/weekly_capture.py`), CSV capture loader (`pipeline/load_capture.py`), and Vision OCR extraction modules (`pipeline/extract_*_ocr.py`).
- **`v2` ("Move to PC - Native Save File I/O"):** Direct Madden PC `.CAREER-*` franchise save file parsing and editing via Node.js/`madden-franchise` (`pipeline/save_reader/`), directory save file watcher daemon (`pipeline/watch_save.py`), direct PostgreSQL save loader matching on `madden_pgid` (`pipeline/load_save.py`), universe teleportation/bridge suite (`pipeline/teleport.py`, etc.), and offline progression/OVR systems (`engines/progression.py`, `engines/ovr_weights.py`).

Per user direction, `v1` components are classified as **`LEGACY_SHIM`** during this transitional period because the 2036 season is currently concluding on PS5 before 2037 migrates to PC.

---

## 2. The `v2` Root Map (Source of Truth)

The following roots represent the active `v2` system boundaries:

### A. Ingestion, Save I/O & Universe Teleportation
- `pipeline/watch_save.py` — Debounced save watcher daemon for PC franchise saves.
- `pipeline/save_reader/pc_save_reader.mjs` — Fast native franchise save reader generating `save.json`.
- `pipeline/save_reader/save_writer.mjs` — Direct franchise save file editor.
- `pipeline/save_reader/draft_tables.mjs` — Draft pick, player, and team extractor.
- `pipeline/load_save.py` — Atomic database loader for save snapshots via `madden_pgid`.
- `pipeline/teleport.py` — Offseason universe teleporter from PS5/database onto PC save.
- `pipeline/teleport_draft.py` — Draft class and pick injection.
- `pipeline/teleport_appearance.py` — Generic head, body type, and visual trait injection.
- `pipeline/teleport_contracts.py` — Player contract injection.
- `pipeline/character_visuals.py` — ISON parser and zstd dictionary codec for `CharacterVisuals.RawData`.
- `pipeline/commentary_names.py` — Madden 27 commentary ID lookup and surname aliasing.
- `pipeline/attribute_reader.py` & `pipeline/load_attributes.py` — High-fidelity PS5 attribute scan reader and loader.

### B. Offline Simulation & Progression
- `engines/ovr_weights.py` & `engines/ovr_weights_m27.json` — Madden 27 OVR NNLS formula engine.
- `engines/progression.py` — Deterministic offline yearly player progression and regression engine.
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
- `CHIAKI_HOST` & `CHIAKI_HOST_IP`: Used by `madden_capture.py` (`v1`). Obsolete upon 2037 PC arrival; superseded by `MADDEN_SAVES_DIR`.

---

## 5. Quarantine Plan

### Tier 1: Safe to Move to `/_deprecated` Immediately (Zero Inbound Edges from `v2` or `v1`)
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
1. `WEEKLY_RUN` (735KB log artifact)
2. `Panthers Full History.pdf` (14.4MB PDF)
3. `PROGRESS.md` (superseded notes)
4. `tools/vision_cells.py`
5. `tools/vision_dump.py`
6. `tools/debug/debug_flicker.py`
7. `tools/debug/extract_schedule_weeks.py`
8. `tools/debug/read_block_17.py`
9. `tools/debug/__init__.py`

### Tier 3: Inline Dead Methods Requiring Tombstone Instrumentation
Do NOT delete yet. Mark with `@tombstone` runtime tripwire and `# @AUDIT-STATUS: PRUNE_SYMBOL` in Phase 4:
- `db/db_handler.py`:
  - `create_active_game`
  - `update_game_meta`
  - `upsert_schedule`
  - `upsert_roster`
  - `upsert_team_stats`
  - `upsert_player_stats`
  - `emit_game_completed`

### Tier 4: Transitional Legacy Shims (`v1`)
Preserve during 2036 PS5 completion. Refactor before physical pruning at 2037 arrival:
- Extract `run_weekly_engines` and `step_articles` out of `pipeline/weekly_capture.py` into a shared engine module (`pipeline/weekly_engines.py`).
- Extract `team_line_totals` out of `pipeline/load_capture.py` into a shared calculation module.
- Strip `pipeline/ocr_extractor.py` import from `db/db_handler.py`.
