# Project Mu: Draft Prospects & Mock Draft Integration Notes

**Date:** October 15, 2026  
**Status:** Feature Complete — Draft Board navigation automated (verified live Oct 15)  
**Active Branch:** `feat/draft-kiper-mock`  
**Active Worktree:** `/Users/jrereyi/Downloads/Project Mu/.claude/worktrees/draft-kiper-mock`  
**Test Suite Status:** 120/120 tests passing (`python3 -m unittest tests/test_madden_capture.py tests/test_writer_config.py tests/test_mock_draft_tools.py tests/test_mock_draft_engine.py tests/test_extract_prospects_ocr.py tests/test_load_capture.py tests/test_weekly_capture.py tests/test_debug_api.py`)

> [!IMPORTANT]
> **DO NOT MERGE INTO `main` YET.**  
> Keep all work strictly on `feat/draft-kiper-mock` inside the `.claude/worktrees/draft-kiper-mock` worktree. Parallel sessions are actively running with uncommitted edits on `main` (`db/init_db.sql`, `engines/records_engine.py`, etc.).

---

## 1. Executive Summary & Architecture

Designed and implemented an automated pipeline for NFL draft prospect ingestion, high-speed local Apple Vision OCR extraction from Madden console screenshots, an analytical 32-pick Round 1 Mock Draft simulation engine, a Mel Kiper Jr. newsroom persona, and decoupled pipeline/debug jobs.

### Key Decisions
1. **Zero Gemini Cost for Extraction:** Replaced multimodal LLM vision calls with on-device macOS Apple Vision OCR (`pipeline/extract_prospects_ocr.py`), with targeted Gemini vision remaining only as an automatic fallback.
2. **No Schema Conflicts (Deferred DB Migration):** Prospect boards and mock draft results read/write directly from structured CSV boards (`data/draft/{draft_season}_prospects.csv` and `captures/prospects/prospects.csv`). Database table additions (`draft_prospects`, `mock_draft_picks`) are deferred until parallel sessions merge.
3. **Line-by-Line Capture Mechanics:** Madden's Draft Board / College Prospects screen displays full legal names, heights, and weights only on the active highlighted row/card, requiring row-by-row keypresses (`madden_capture.py --only prospects`).
4. **Decoupled Scan Cadence:** The draft board scan (`--mode prospects` / `draft_scan`) is completely separate from the roster scan (`--mode roster` / `roster_scan`).

---

## 2. Implemented & Verified Capabilities

### 2.1 Apple Vision OCR Extractor (`pipeline/extract_prospects_ocr.py`, `extract.py`)
- **Screen Parsing:**
  - Card Name: First and last names stacked (`850 <= x <= 1500`, `150 <= y < 335`). Wide-tracked first names joined by position; overlapping pieces re-read from a crop.
  - Card Badge: Position, number, baseline scouted OVR (`LEDG #72 | OVR75`).
  - Card Measurables: Height and weight on `HT/WT` band (`6'5", 280 LBS`).
  - Table Highlighted Row: Matches player card to table row by initial + last name (`D.Vogel`), extracting table-only `AGE` (`1150 <= x <= 1250`).
- **Font & Glyph Normalizations:**
  - Font glitches on height: `6'T"` $\to$ `6'1"`, `5'1"` on $\ge 215$ lb players $\to$ `6'1"`, asterisks `*` $\to$ `"`, dropped apostrophes restored.
  - Lineman weight correction: $827 \text{ LBS} \to 327 \text{ LBS}$ (weights $> 500$ normalized by subtracting 500).
  - Diacritics and character substitutions cleaned (`BƯCHANAN` $\to$ `BUCHANAN`, `SHAOUILLE` $\to$ `SHAQUILLE`).
- **Board Order from Screen:** Frames sort by numeric frame number. The highlighted row's `PICK` cell fills `PROJECTED_ROUND` (1–7 or `UDFA`) and `ROUND_PICK`. Overall pick is computed from round and pick-in-round (1–224 for drafted, 225+ for undrafted).
- **Benchmark:** Parsed all 389 unique prospects (224 drafted + 165 UDFAs) from `captures/prospects` in ~75s with zero Gemini calls and 100% field completeness.
- **Syncing:** `extract.py` automatically synchronizes extracted `prospects.csv` to canonical storage `data/draft/{draft_season}_prospects.csv` where `draft_season = season + 1`.

### 2.2 Prospect Ingestion & Validation (`pipeline/ingest_prospects.py`)
- Standardizes heights (`6'2"`), weights (140–420 lbs), ages (18–30), baseline ratings (50–99), and canonical position mappings (`LEDG`/`REDG` $\to$ `EDGE`, `SAM`/`MIKE`/`WILL` $\to$ `LB`, `LT`/`RT` $\to$ `OT`, `RG`/`LG` $\to$ `OG`, `HB` $\to$ `RB`).
- `load_prospects_from_csv(csv_path)` validates pick uniqueness and returns sorted `List[Prospect]`.

### 2.3 Round 1 Mock Draft Engine (`engines/mock_draft_engine.py`)
- Evaluates 32 Round 1 selections using live league data:
  1. **Draft Order:** Reverse standings, point-differential tiebreakers, and playoff progression via `get_draft_order(season)`.
  2. **Team Needs:** Baseline rating deficits, age cliffs (starters aged 30+ without under-25 developmental backups), and phase ranks via `get_team_draft_needs(team, season)`.
  3. **Scoring Function:**
     $$\text{FitScore} = 0.45 \cdot (\text{NeedScore} \cdot 1.5) + 0.40 \cdot (\text{BPA}) + 10.0 \cdot (M_{\text{pos}} - 1.0)$$
     where BPA is centered on scouted OVR with slot value delta $((i - \text{projected\_pick}) \cdot 0.5)$, and positional premium $M_{\text{pos}}$ prioritizes QB (1.25), OT/EDGE (1.12), and CB (1.10). A team already set at QB receives a severe penalty.
- Produces 32 unique picks without duplicates and generates franchise-specific analytical rationales.
- CLI: `python3 -m engines.mock_draft_engine [--season N] [--file path/to/prospects.csv] [--json]`.

### 2.4 Mel Kiper Jr. Persona & ReAct Tools
- **ReAct Research Tools (`newsroom/db_tools.py`):**
  - `get_mock_draft(season, file_path)`: executes the Round 1 simulation engine and returns pick-by-pick selections, team fit, and analytical rationales.
  - `get_draft_prospects(position, top_n, file_path)`: queries consensus draft prospects with position filtering.
  - Registered in `newsroom/swarm_dispatcher.py` (`self.tools`).
- **Writer Persona (`newsroom/config/agent_personas.json`, `article_types.json`):**
  - Defined `MEL_KIPER` persona with signature voice ("Todd! Listen to me!", Big Board, trench warriors, signature Steals/Sleepers).
  - Assigned as primary writer for `OFFSEASON_MOCK_DRAFT`.
  - Verified via live test (`tests.test_editorial_flow --article-type OFFSEASON_MOCK_DRAFT --writer MEL_KIPER --live`) generating a full ~1,600-word Kiper column.

### 2.5 Separate Pipeline Orchestration & Debug UI
- **`madden_capture.py`:**
  - `--only roster`: strictly captures 32 team rosters (`run_roster()`).
  - `--only prospects [--prospects N | --all-prospects]`: captures draft board prospects line-by-line. Opens the Draft Board itself (`open_prospects()`, see §3).
- **`pipeline/load_capture.py`:**
  - `detect_mode(folder)` recognizes mode `"prospects"` when `prospects.csv` is present.
  - `load_prospects()` validates via `load_prospects_from_csv` and copies to `data/draft/{season+1}_prospects.csv` (respecting `dry_run`).
- **`pipeline/weekly_capture.py`:**
  - Added `"roster"` and `"prospects"` to `MODES`.
  - `python3 -m pipeline.weekly_capture --mode roster`: captures rosters, extracts cards, loads players/seasons, shrinks media, notifies (`"Rosters loaded for Season {season}."`), skipping weekly game engines and articles.
  - `python3 -m pipeline.weekly_capture --mode prospects`: captures draft board, extracts via Apple Vision OCR, loads/validates, syncs to `data/draft/{season+1}_prospects.csv`, shrinks media, notifies (`"Draft prospects loaded for Season {season}."`), skipping weekly game engines and articles.
- **Debug API & UI (`server/debug_api.py`, `Mu_Network/src/DebugPage.tsx`):**
  - Separate jobs: `roster_scan` ("Roster scan") and `draft_scan` ("Draft prospect scan").
  - The `#debug` page UI provides separate **Roster scan** and **Draft prospect scan** buttons.

---

## 3. Console Navigation to the Draft Board (done, verified live Oct 15)

`madden_capture.py --only prospects` (and the `draft_scan` job) now reaches the Draft Board by itself from anywhere in the franchise menus via `open_prospects()`, then captures row by row as before. If the console is already on the board, it starts there.

### Mapped Route
| Step | Screen (`classify()`) | Action |
|---|---|---|
| 1 | `HUB_ROSTER` (Manage Roster tab) | Highlight `SCOUTING`, press Cross |
| 2 | `SCOUTING` (TOP 10 PROSPECTS, MOCK DRAFTS; opens on SCOUTS) | Up to `PROSPECTS`, press Cross. The list draws its region tabs first, then the table about a second later. |
| 3 | `SCOUT_PROSPECTS` (button bar: SET FAVORITE, L3 EDIT PLAYERS) | Press L3 (chiaki-ng key `5`, `KEYCODES["l3"] = 23`) |
| 4 | `EDIT_CLASS_PROMPT` (EDIT DRAFT CLASS, IMPORT LOCAL FILE) | Opens on **DOWNLOAD FROM DOWNLOAD CENTER**. Press Down to `EDIT DRAFT CLASS`, then Cross. JR approved this; Madden posts a notice to the league transaction log. |
| 5 | `DRAFT_BOARD` (ALL ROUNDS, ADJUST ORDER) | Opens with Round 1, Pick 1 highlighted |

### Safety & Classification
- `DRAFT_BOARD` now needs `ALL ROUNDS` or `ADJUST ORDER`. The Prospects list's PROJ column reads "Round 1", which the old check took for the board.
- `cross()` allows Cross only on: `SCOUTING` in the Manage Roster menu, `PROSPECTS` in the Scouting menu, and `EDIT DRAFT CLASS` on the prompt. `prompt_option()` only counts buttons below 65% of the screen height, because the prompt's title also reads EDIT DRAFT CLASS. Cross is never pressed on the Draft Board, where it means EDIT PLAYER.
- `press_into()` retries L3 if Madden drops the press.
- Tests: `DraftRouteTest` in `tests/test_madden_capture.py`. Mapping frames: `captures/2026-10-15_explore/explore/` (gitignored).

### Not Yet Done
- A full `python3 -m pipeline.weekly_capture --mode prospects` run, end to end, using the new navigation.

---

## 4. Deferred Future Tasks (After Parallel Sessions Merge)

1. **Database Schema Integration:**
   - Add `draft_prospects` table and `mock_draft_picks` ledger in `db/init_db.sql` / migration once parallel sessions touch DB schema.
2. **Multi-Round Simulation:**
   - Extend `engines/mock_draft_engine.py` from 32 picks (Round 1) to full 7-round mock drafts (256+ picks) using the 389 extracted prospects.

---

## 5. Verification Commands

Run these commands inside the worktree to verify the environment:

```bash
# 1. Run all unit and integration test suites (120 tests)
python3 -m unittest \
  tests/test_madden_capture.py \
  tests/test_writer_config.py \
  tests/test_mock_draft_tools.py \
  tests/test_mock_draft_engine.py \
  tests/test_extract_prospects_ocr.py \
  tests/test_load_capture.py \
  tests/test_weekly_capture.py \
  tests/test_debug_api.py

# 2. Run Mock Draft Engine directly
python3 -m engines.mock_draft_engine --season 2035 --file data/draft/2036_prospects.csv

# 3. Dry-run load of prospects board
python3 -m pipeline.load_capture captures/prospects --dry-run

# 4. Verify frontend build
cd Mu_Network && npm run build
```
