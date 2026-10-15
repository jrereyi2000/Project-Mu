# Project Mu: Draft Prospects & Mock Draft Integration Notes

**Date:** October 14, 2026  
**Status:** Complete & Verified  

---

## 1. Overview & Objective
Designed and implemented an automated pipeline for NFL draft prospect ingestion, local Apple Vision OCR extraction from Madden console captures, and a 32-pick Round 1 Mock Draft simulation engine.

Key architectural requirements:
- **No Schema Conflicts:** Database schema changes (`db/init_db.sql` and migrations) are deferred until ongoing parallel sessions merge. All prospect data currently ingests directly from structured CSV boards (`pipeline/ingest_prospects.py`).
- **Zero Gemini Cost:** Replaced multimodal LLM vision calls with high-speed, local macOS Apple Vision OCR (`pipeline/extract_prospects_ocr.py`), with targeted Gemini vision remaining only as an automatic fallback.
- **Line-by-Line Capture:** Madden's Draft Board / College Prospects screen only displays a player's full legal name, height, and weight on the active highlighted row/card, requiring row-by-row navigation (`madden_capture.py --only prospects`).

---

## 2. Screen Anatomy & OCR Mechanics

### 2.1 Madden Draft Board Screen Layout
- **Top-Left Player Card:**
  - **Player Name:** First and last names appear stacked (`850 <= x <= 1500`, `150 <= y < 335`). In rare cases where only the last name is detected (e.g. large single text blocks like `BUCHANAN`), the upper first-name band (`950 <= x <= 1300`, `160 <= y <= 245`) is cropped and OCR'd independently (yielding `C.J.`).
  - **Player Badge:** Position, jersey number, and scouted baseline OVR appear below the card portrait (`80 <= x <= 450`, `480 <= y <= 560`), e.g. `LEDG #72 | OVR75` or `QB #18 | 0VR65`.
  - **Measurables (`HT/WT`):** Displayed on a single horizontal band (`335 <= y <= 410`), e.g. `HT/WT: 6'5", 280 LBS`.
  - **College Program:** Parsed from `COLLEGE: <NAME>` (`390 <= y <= 460`).
- **Table Highlighted Row:**
  - Table columns include: `PICK` (Round & Pick), `NAME` (`D.Vogel`), `POS`, `AGE`, `OVR`, and physical traits (`SPD`, `ACC`, `AGI`, `COD`, `STR`, `AWR`, `CAR`).
  - `AGE` is not visible on the top-left player card; it is extracted exclusively by matching the card player's last name against the table's `NAME` column (`650 <= x <= 900`, `750 <= y <= 1650`) and reading the corresponding row's `AGE` cell (`1150 <= x <= 1250`).
  - The matched row's `POS` and `OVR` columns also serve as automatic cross-checks if the top badge is ever obscured.

### 2.2 OCR Normalization Heuristics
Apple Vision OCR achieves near-instantaneous parsing, but Madden's custom game font introduces specific misread patterns handled by `pipeline/extract_prospects_ocr.py`:
1. **Font Glitches on Height:**
   - Numbers `1` read as `T`, `I`, or `l` (e.g. `6'T"` $\to$ `6'1"`).
   - Inch quotes `"` read as asterisks `*` (e.g. `6'4*`, `5'10*` $\to$ `6'4"`, `5'10"`).
   - Dropped apostrophes (e.g. `67 ,332 LBS` on offensive/defensive tackles $\to$ `6'7"`).
   - Dropped digits on heavyweights: `5'1"` for players $\ge 215$ lbs or non-DBs is normalized to `6'1"`.
2. **Lineman Weight Correction:**
   - Apple Vision occasionally misreads `3` as `8` on 300+ lb linemen (e.g. `827 LBS` on an offensive tackle $\to$ `327 LBS`). Weights $> 500$ lbs are automatically normalized by subtracting 500.
3. **Diacritics & Homoglyphs:**
   - Combines unicode normalization (`NFKD`) to strip stray combining accents (e.g. `BƯCHANAN` $\to$ `BUCHANAN`).
   - Cleans character substitutions (e.g. `SHAOUILLE` $\to$ `SHAQUILLE`, `BOBB'` $\to$ `BOBBY`, `0VR` $\to$ `OVR`).
   - Normalizes spacing in university names (e.g. `TEXASA&M` $\to$ `TEXAS A&M`).

---

## 3. Implemented Modules

### 3.1 [`pipeline/extract_prospects_ocr.py`](file:///Users/jrereyi/Downloads/Project%20Mu/pipeline/extract_prospects_ocr.py)
- **`find_prospect_frames(root)`**: Locates active row-by-row sequence frames (`prospect_\d{3,4}\.png`) in either `<root>/prospects/` or `<root>/` directly, filtering out legacy multi-run prefixes (`p1_...`, `p2_...`).
- **`parse_prospect_frame(path, pick_index)`**: Executes local Apple Vision OCR (`VNRecognizeTextRequest`) on a single frame, extracts all card and table attributes, applies normalization heuristics, and cross-references table age.
- **`extract_prospects_ocr(root)`**: Batch processes all frames in sequence and writes `prospects.csv`.
- **Board order from the screen (Oct 15):** frames sort by number (the folder holds resumed runs numbered 000–235, 1000–1074, 2000–2068, 3000–3011; sorting the names as text interleaved them, so CSV order didn't match the draft board). The highlighted row's PICK cell (`Round N, Pick M` / `Undrafted`) is read into `PROJECTED_ROUND` / `ROUND_PICK`; repeated frames at run boundaries are dropped, rows are sorted by (round, pick), and `PROJECTED_PICK` is the overall pick (undrafted numbered on from 225). The highlighted row is matched by first initial + last name first, which fixed ages taken from a neighbouring row (BOBBY WHITE had matched `T.Whitehead`).

### 3.2 [`extract.py`](file:///Users/jrereyi/Downloads/Project%20Mu/extract.py)
- Updated `extract_prospects(root)` to run local Apple Vision OCR first.
- Bypasses Gemini generic table prefetching for prospect line captures.
- Preserves targeted Gemini vision fallback only if OCR is unavailable or fields are missing.

### 3.3 [`madden_capture.py`](file:///Users/jrereyi/Downloads/Project%20Mu/madden_capture.py)
- Added `--only prospects` capture mode with support for custom limits (`--prospects 32`) or full draft classes (`--all-prospects` / `--prospects 0`).
- Steps row-by-row with `Down` keypresses, checking `same_table(prev_sig, sig)` to automatically detect the bottom of the draft board.
- Avoids returning to the franchise hub on exit to allow inspecting or resuming draft board state.

### 3.4 [`pipeline/ingest_prospects.py`](file:///Users/jrereyi/Downloads/Project%20Mu/pipeline/ingest_prospects.py)
- Parses, validates, and normalizes prospect boards from CSV.
- Canonical position mapping: `LEDG`/`REDG` $\to$ `EDGE`, `SAM`/`MIKE`/`WILL` $\to$ `LB`, `LT`/`RT` $\to$ `OT`, `RG`/`LG` $\to$ `OG`, `HB` $\to$ `RB`.
- Enforces strict constraints on pick numbers, weights (140–420 lbs), ages (18–30), and baseline ratings (50–99).

### 3.5 [`engines/mock_draft_engine.py`](file:///Users/jrereyi/Downloads/Project%20Mu/engines/mock_draft_engine.py)
- Simulates a 32-pick Round 1 Mock Draft using three live inputs:
  1. **Draft Order:** Live reverse standings, point differentials, and playoff finishes (`get_draft_order(season)`).
  2. **Team Needs:** Starter quality deficits, age cliffs (age 30+ without developmental backups), and phase rankings (`get_team_draft_needs(team, season)`).
  3. **Draft Board:** Consensus prospect class from CSV.
- **Scoring Function:**
  $$\text{FitScore} = 0.45 \cdot (\text{NeedScore} \cdot 1.5) + 0.40 \cdot (\text{BPA}) + 10.0 \cdot (M_{\text{pos}} - 1.0)$$
  where BPA balances scouted OVR against slot value delta $((i - \text{projected\_pick}) \cdot 0.5)$, and positional premium $M_{\text{pos}}$ prioritizes QB (1.25), OT/EDGE (1.12), and CB (1.10).

---

## 4. Verification & Benchmarks

### 4.1 Real Capture Tests
1. **Round 1 Test Set (`captures/test_prospects`, 32 frames):**
   - Extracted in ~10 seconds with 0 Gemini calls.
   - 100% field extraction across all 32 players.
2. **Full Draft Class Run (`captures/prospects`, 392 frames over 4 runs, Oct 15):** 389 unique prospects: 224 drafted (7 rounds × 32, every PICK cell read) + 165 undrafted. Every row has all fields after the HT/WT letter-for-digit fixups (`3U2 LBS` → 302 for JOSH WRIGHT, `E'11"` → 5'11" for IMMANUEL PERKINS); the board loads and the Round 1 mock draft runs on it. Vision reads the wide-tracked first names differently from run to run, so card name pieces are joined by position (MAL+ACHI → MALACHI) and overlapping pieces (RC+ONALD) are re-read from a crop. Remaining known misreads are fine to be spot checked by the user.
3. **Earlier run (236 frames, Oct 14):**
   - Extracted 236 prospects across Rounds 1–7 into `captures/prospects/prospects.csv` in ~75 seconds.
   - **Completeness:** 236/236 names, 236/236 positions, 236/236 heights, 236/236 weights, 236/236 ages, 236/236 colleges, 236/236 OVRs (0 missing fields).
   - Validated cleanly via `pipeline/ingest_prospects.py`.

### 4.2 Unit & Integration Tests
- `tests/test_extract_prospects_ocr.py`: 10 tests covering Unicode cleaning, frame parsing mocks, font and digit fixups, name-line joining, PICK labels, highlighted-row matching, numeric frame order, on-screen pick ordering, and real-capture end-to-end integration (~2 min: it OCRs every frame in `captures/prospects`).
- `tests/test_mock_draft_engine.py`: 7 unit tests covering order generation, team needs scoring, positional premiums, mock simulation uniqueness, and CLI execution.
- Full test suite: **365 tests passing in ~16 seconds**.

---

## 5. Next Steps
1. **Draft Board Menu Navigation:** Once console menu routing is added by other sessions, wire up automatic entry into the draft board from the franchise hub (e.g. `League > Scouting / Draft Board`).
2. **Database Schema Integration:** Once parallel schema work concludes, create a dedicated `draft_prospects` table and `mock_draft_picks` ledger to persist draft classes and historical mock simulations across seasons.
3. **Multi-Round Simulation:** Extend `mock_draft_engine.py` from 32 picks (Round 1) to full 7-round mock drafts (256+ picks) using the 236+ extracted prospect boards.
