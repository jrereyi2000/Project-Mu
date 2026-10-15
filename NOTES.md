# Project Mu: Draft Prospects & Mock Draft Integration Notes

**Date:** October 15, 2026  
**Status:** Agent 0 of `docs/PARALLEL_ARTICLE_UPGRADE_HANDOFF.md` done: board and mock draft in the database, one board tool, playoff-exit draft order, 24-pick `OFFSEASON_MOCK_DRAFT`; merged into `main`  
**Active Branch:** `feat/draft-kiper-mock`  
**Active Worktree:** `/Users/jrereyi/Downloads/Project Mu/.claude/worktrees/draft-kiper-mock`  
**Test Suite Status:** 534/534 tests passing (`python3 -m pytest -q tests`; the real-capture OCR test takes ~4 min)  

> [!NOTE]
> Merged into `main` on Oct 15 at the end of Agent 0 (`docs/PARALLEL_ARTICLE_UPGRADE_HANDOFF.md`). Agents 1, 2 and 4–9 branch from that `main`.

---

## 1. Executive Summary & Architecture

Designed and implemented an automated pipeline for NFL draft prospect ingestion, high-speed local Apple Vision OCR extraction from Madden console screenshots, an analytical 32-pick Round 1 Mock Draft simulation engine, a Mel Kiper Jr. newsroom persona, and decoupled pipeline/debug jobs.

### Key Decisions
1. **Zero Gemini Cost for Extraction:** Replaced multimodal LLM vision calls with on-device macOS Apple Vision OCR (`pipeline/extract_prospects_ocr.py`), with targeted Gemini vision remaining only as an automatic fallback.
2. **Board and Mock Draft in the Database:** `draft_prospects` holds the scouted board per draft class, and `mock_draft_picks` holds each week's saved Round 1 mock (`db/migrations/2026_11_19_draft_board.sql`, `pipeline/draft_board.py`). The CSVs are extraction output and an export copy only. Tools and tests never read `data/draft/` (on Oct 15 that held a hand-made 32-row stub that tests had come to depend on).
5. **Lenient Board Loads (JR, Oct 15):** a board with a blank or implausible cell still loads. The cell is stored as NULL and listed as a warning; next season's roster scan has the real values once the prospect is a rookie, and links him (`draft_prospects.player_id`).
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
- **Digit & Name Reliability (Oct 15):** Vision misread digits differently on each run (1↔7, 3↔8, 5'11" read as 5'1"). The old `5'1"` → `6'1"` rule was wrong: every case checked was 5'11".
  - **Height/weight** are now read glyph by glyph: `read_ht_wt` matches digit templates in `pipeline/assets/prospect_htwt_digits.png` and settles 0/3/6/8/9 by their loops. Vision stays the fallback.
  - **PICK cells** are mended from the consecutive board sequence (`fix_pick_sequence`); a real gap is reported instead.
  - **First names** are re-read from their band alone. **Last names** go by vote: card, band and the table's short name.
  - **Result:** both full captures (`captures/prospects`, `captures/2026-10-15_full_board3`) extract to identical CSVs, 389/389 rows and no blank cells. 31 + 54 + 15 changed cards were checked by eye.
- **Benchmark:** Parsed all 389 unique prospects (224 drafted + 165 UDFAs) from `captures/prospects` in ~75s with zero Gemini calls and 100% field completeness.
- **Syncing:** `extract.py` still copies the extracted `prospects.csv` to `data/draft/{draft_season}_prospects.csv` (`draft_season = season + 1`). Nothing reads that copy now: the board reaches the tools only through `pipeline.load_capture` → `draft_prospects`.

### 2.2 Prospect Ingestion & Validation (`pipeline/ingest_prospects.py`)
- Standardizes heights (`6'2"`), weights (140–420 lbs), ages (18–30), baseline ratings (50–99), and canonical position mappings (`LEDG`/`REDG` $\to$ `EDGE`, `SAM`/`MIKE`/`WILL` $\to$ `LB`, `LT`/`RT` $\to$ `OT`, `RG`/`LG` $\to$ `OG`, `HB` $\to$ `RB`).
- `load_prospects_from_csv(csv_path, strict=True, warnings=None)` returns sorted `List[Prospect]` (now with `projected_round`, `round_pick`, `prospect_id`).
  - Strict mode raises on the first bad field.
  - Lenient mode (`strict=False`, used by the loader and `--file`) turns a bad field into `None` with a warning, skips a row with no name or pick, and keeps the first of two rows claiming one pick.
  - Either way, an empty board raises.
  - `height_inches` / `inches_height` convert for the database. An impossible height like `5'71"` is treated as blank.

### 2.3 Round 1 Mock Draft Engine (`engines/mock_draft_engine.py`)
- Evaluates Round 1 selections (32, or the first `limit`) using live league data:
  1. **Draft Order:** `get_draft_order(season)` → `engines/draft_order.py`.
     * Brackets: missed playoffs 1–18, Wild Card losers 19–24, Divisional 25–28, Conference 29–30, Super Bowl 31/32.
     * Within a bracket: worst record first, ties counting half, then regular-season point differential.
     * Each pick is `locked` or provisional.
     * While only Wild Card games exist, each conference's best record is its bye.
     * The run's week cutoff is respected.
  2. **Team Needs:** Baseline rating deficits, age cliffs (starters aged 30+ without under-25 developmental backups), and phase ranks via `get_team_draft_needs(team, season)`.
  3. **Scoring Function:**
     $$\text{FitScore} = 0.45 \cdot (\text{NeedScore} \cdot 1.5) + 0.40 \cdot (\text{BPA}) + 10.0 \cdot (M_{\text{pos}} - 1.0)$$
     where BPA is centered on scouted OVR with slot value delta $((i - \text{projected\_pick}) \cdot 0.5)$, and positional premium $M_{\text{pos}}$ prioritizes QB (1.25), OT/EDGE (1.12), and CB (1.10). A team already set at QB receives a severe penalty.
- Produces unique picks with `team_id`, `locked`, `selection_status`, fit score and rationale. A prospect with a blank OVR is valued at 50, not dropped.
- Reads the board from `draft_prospects`. `save()` stores a run in `mock_draft_picks` for (season, week).
- CLI: `python3 -m engines.mock_draft_engine [--season N] [--limit 24] [--save] [--file captures/<run>/prospects.csv] [--json]`.

### 2.4 Mel Kiper Jr. Persona & ReAct Tools
- **ReAct Research Tools (`newsroom/db_tools.py`):**
  - `get_mock_draft(season, limit)`: the week's saved mock draft, so every article that week cites the same picks. It runs and saves the engine when there is none. `limit` 24 for Week 20.
  - `get_scouted_draft_board(season, limit, position)`: replaces `get_draft_prospects`. It reads `draft_prospects`, returns blanks as null, and filters by position or position group.
  - Registered in `newsroom/swarm_dispatcher.py` (`self.tools` and the writer research-tools list), alongside the playoff-aware `get_draft_order`.
- **`OFFSEASON_MOCK_DRAFT` (Week 20, Slot 4): picks 1–24 locked**, writers `MEL_KIPER` / `YATES` / `BARNWELL`.
  * Calls `get_draft_order`, `get_mock_draft` (limit 24), `get_scouted_draft_board` and `get_team_draft_needs`.
  * Never names a prospect who isn't on the board, and never fills in a null field.
  * Strict `## [Pick]. [Team]: [Prospect], [Position], [School]` headers, followed by height, weight and OVR from the board.
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
  - `load_prospects()` loads `prospects.csv` leniently into `draft_prospects` for class season + 1, replacing an earlier scan of that class. Warnings go in `report["prospect_warnings"]`, and an export copy goes to `data/draft/` unless it's a `--dry-run`.
  - A roster load links this season's rookies to their prospect rows. A roster folder with a `prospects.csv` loads the board too, and a bad board no longer fails the roster.
- **`pipeline/weekly_capture.py`:**
  - Added `"roster"` and `"prospects"` to `MODES`.
  - `python3 -m pipeline.weekly_capture --mode roster`: captures rosters, extracts cards, loads players/seasons, shrinks media, notifies (`"Rosters loaded for Season {season}."`), skipping weekly game engines and articles.
  - `python3 -m pipeline.weekly_capture --mode prospects`: captures draft board, extracts via Apple Vision OCR, loads it into `draft_prospects`, shrinks media, notifies (`"Draft prospects loaded for Season {season}."`), skipping weekly game engines and articles.
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

### Leaving the Board (`close_draft_board()`)
A bare Back does not leave the board. Back opens a **Draft Class Edited** prompt (`EXIT_CLASS_PROMPT`) drawn over the board. It opens on EXPORT LOCAL FILE, so press Down twice to **SAVE AND EXIT**, then Cross. That lands on the `SCOUTING` screen. `run_prospects()` closes the board after every capture, and `go_hub()` calls `close_draft_board()` whenever it finds the board or the prompt, never a bare Back. If `open_prospects()` finds the prompt already open, it finishes the exit and re-enters from Scouting.

### Safety & Classification
- `DRAFT_BOARD` now needs `ALL ROUNDS` or `ADJUST ORDER`. The Prospects list's PROJ column reads "Round 1", which the old check took for the board.
- `cross()` allows Cross only on: `SCOUTING` in the Manage Roster menu, `PROSPECTS` in the Scouting menu, `EDIT DRAFT CLASS` on the entry prompt, and `SAVE AND EXIT` on the exit prompt. `prompt_option()` only counts buttons below 65% of the screen height, because the prompt's title also reads EDIT DRAFT CLASS. Cross is never pressed on the Draft Board, where it means EDIT PLAYER.
- `press_into()` retries L3 if Madden drops the press.
- Tests: `DraftRouteTest` in `tests/test_madden_capture.py`. Mapping frames: `captures/2026-10-15_explore/explore/` (gitignored).

### Full-Board Capture Reliability
- **End of board:** the board ends on the Undrafted rows. A board that stops moving anywhere else raises `Abort`. `DRAFT_BOARD` is in `LIST_SCREENS`, so `run_mode`'s frozen-stream probe covers it, restarts the stream and redoes the board from Pick 1. The first full run stopped after 18 rows, right after chiaki-ng stopped drawing.
- **Skipped rows:** when a frame looks unchanged, the capture waits 0.6 s and looks again before pressing Down again (`PROSPECT_RETRIES` = 3). Pressing straight away skipped 5 prospects in the second run (e.g. Round 2, Pick 29 Colin Franklin).
- **Starting on the board:** the capture leaves the board and reopens it, since the highlight may not be on Pick 1.

### Live Checks (Oct 15)
- `madden_capture.py --only prospects --prospects 3`, starting from the hub: navigated in, captured picks 1–3 (Vogel, Capps, Reynolds), left via SAVE AND EXIT, and `go_hub()` returned to the Manage Roster hub.
- Each entry posts an "edit draft class" notice to the league transaction log.

- Full board (`--only prospects --all-prospects`, `captures/2026-10-15_full_board3`): 389 frames, 224 drafted + 163 UDFA + 2 unreadable PICK cells. All 389 players match `captures/prospects/prospects.csv`.
  - The OCR differences that remained (1↔7, 3↔8, PICK cells, first names) are fixed by the extractor changes above.

### Not Yet Done
- A full `python3 -m pipeline.weekly_capture --mode prospects` run (extract + load), end to end.
- The glyph templates were mapped at the 3024×1888 chiaki-ng window. At any other size `read_ht_wt` returns nothing and Vision's reading is used.

---

## 4. Future Tasks

1. **Multi-Round Simulation:**
   - Extend `engines/mock_draft_engine.py` from 32 picks (Round 1) to full 7-round mock drafts (256+ picks) using the 389 extracted prospects.

---

## 5. Verification Commands

Run these commands inside the worktree to verify the environment:

```bash
# 1. Run the whole test suite (534 tests; the real-capture OCR test takes ~4 min)
python3 -m pytest -q tests

# 2. Run Mock Draft Engine directly
python3 -m engines.mock_draft_engine --season 2035 --limit 24          # board from draft_prospects

# 3. Dry-run load of prospects board
python3 -m pipeline.load_capture captures/2026-10-15_full_board3 --dry-run   # rows + prospect_warnings, writes nothing

# 4. Verify frontend build
cd Mu_Network && npm run build
```
