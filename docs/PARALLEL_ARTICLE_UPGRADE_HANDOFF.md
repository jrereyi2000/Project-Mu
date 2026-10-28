# Project Mu: Parallel Article Generation Upgrade Handoff Specification

**Status (2026-10-28):** done. Agents 0–9 all landed on `main` on 2026-10-15 (Agent 3's task was folded into Agent 0).

## Overview & Context
We recently completed the integration of two major data pipelines into Project Mu:
1. **Coaching System**: Head-coach profiles (`coaches`, `coach_tenures`, `coach_seasons`, `coty_scores`), measured pass lean and fourth-down aggressiveness (`team_seasons.observed_pass_lean`, `team_seasons.observed_fourth_down`), coverage philosophies, career playoff/Super Bowl records, and Coach of the Year outperformance metrics.
2. **Scouted Prospect Board**: Console-captured draft prospect boards (`data/draft/{season}_prospects.csv` / `prospects.csv`), scouted ratings (OVR, physical measurables, college, traits), and the 32-pick simulation engine (`engines/mock_draft_engine.py`).

Previously, article types, directives, and editorial slots were stripped down or used generic placeholders because this data was not yet available in the database. Now that these pipelines are active, we are upgrading the newsroom article generation directives, slot routing, and schemas.

### Where the prospect board stands (Oct 15)
The engine, loader and OCR extractor are on `main` (commit `5b5e10c`). The rest lives on branch `feat/draft-kiper-mock` (worktree `.claude/worktrees/draft-kiper-mock`):
* **Writer tools:** `get_mock_draft` and `get_draft_prospects`, registered in `swarm_dispatcher.py`.
* **`MEL_KIPER` persona** as lead writer of `OFFSEASON_MOCK_DRAFT`, with that entry already rewritten around `get_mock_draft`.
* **The automated board scan:** `madden_capture.py --only prospects` navigates to the Draft Board and back by itself. A full scan takes about 7 minutes.
* **The extractor fixes:** height, weight and pick numbers are now read reliably. Two full scans give identical CSVs, 389/389 rows.

Today the board is stored only as CSV: the real board reaches `data/draft/{draft_season}_prospects.csv` only when that branch's scan is extracted or loaded, and `data/` is git-ignored. Agent 0 moves it into the database (task 2). Until then, the local `data/draft/2036_prospects.csv` is a hand-made 32-row stub whose pick 1 is "Dante Moore"; the real board's pick 1 is DAVE VOGEL. Tests must not depend on either file. They use a fixture CSV under `tests/fixtures/`.

**Agent 0 is therefore run from the `feat/draft-kiper-mock` session.** It also absorbs Agent 3. It finishes by merging that branch into `main`. Agents 1, 2 and 4–9 are then spawned from that session and branch from the merged `main`.

---

## Instructions for Dispatching Agents

### Worktree & Concurrency Protocol
To parallelize these upgrades across multiple independent agents without file lock conflicts or dirty state:
1. **Agent 0 runs first and alone**, on `feat/draft-kiper-mock`. Nobody edits `main`'s checkout while it runs.
2. After Agent 0 has merged, each remaining agent is started from the Agent 0 session in its own git worktree, branched from the merged `main`. Work never happens directly in `main`'s checkout: uncommitted edits there block every merge. The branch is named `feat/agent-<N>-<short-name>`.
3. The agent executes only its assigned scope and reads `PROJECT_SPEC.md` first (CLAUDE.md).
4. The agent updates `PROJECT_SPEC.md` for its change (CLAUDE.md): only the lines describing its own article type, slot or tool.
5. The agent runs the test suite. `tests/` mixes unittest and pytest styles, so use pytest, which runs both. The real-capture OCR test takes about 4 minutes:
   ```bash
   python3 -m pytest -q tests
   ```
6. The agent commits its changes with a clean message and notifies the user that the rewrite is ready for review.
7. Upon user approval, merge the branch into `main`, one agent at a time; stop and report on any conflict. Then clean up the worktree:
   ```bash
   git -C <main checkout> merge feat/agent-<N>-<short-name>
   git worktree remove <worktree path>
   ```

---

## Agent Assignment Matrix

| Agent ID | Scope / Assignment | Target Week(s) & Slot | Primary Files |
| :--- | :--- | :--- | :--- |
| **Agent 0** | **Prospect & Mock Draft Tables, Prospect Tool, Postseason Draft Order & `OFFSEASON_MOCK_DRAFT` (absorbs Agent 3); merges `feat/draft-kiper-mock`** | Shared Core / Prerequisite; Week 20, Slot 4 | `db/init_db.sql`, `db/migrations/`, `pipeline/ingest_prospects.py`, `pipeline/load_capture.py`, `newsroom/db_tools.py`, `newsroom/swarm_dispatcher.py`, `engines/mock_draft_engine.py`, `newsroom/config/article_types.json` (`OFFSEASON_MOCK_DRAFT` only), `newsroom/config/agent_personas.json`, `tests/` |
| **Agent 1** | **`COACHING_HOT_SEAT_AUDIT` Archetype** | Week 19, Slot 6 | `newsroom/config/article_types.json`, `newsroom/swarm_dispatcher.py`, `newsroom/slot_targets.py`, `docs/EDITORIAL_MATRIX.md` |
| **Agent 2** | **`PLAYOFF_AUTOPSY` Seat Temperature** | Weeks 20–22, Slots 6 & 7 | `newsroom/config/article_types.json` |
| ~~Agent 3~~ | ~~`OFFSEASON_MOCK_DRAFT` Locked Projections~~ → **done by Agent 0** | Week 20, Slot 4 | — |
| **Agent 4** | **`DRAFT_BLUEPRINT_ROSTER_AUDIT` Core Diagnostics** | Week 21, Slot 5 | `newsroom/config/article_types.json` |
| **Agent 5** | **`FRANCHISE_STATE_OF_THE_UNION` Rookie-Coach Alignment**| Weeks 18–19 | `newsroom/config/article_types.json` |
| **Agent 6** | **`SUPER_BOWL_MEDIA_INTERVIEW_AUDIT` Coaching Records** | Week 23, Slot 4 | `newsroom/config/article_types.json` |
| **Agent 7** | **`SUPER_BOWL_COMPREHENSIVE_PREVIEW` Chess Match** | Week 23, Hero Slot | `newsroom/config/article_types.json` |
| **Agent 8** | **`SUPER_BOWL_HISTORICAL_STAKES` (HC + QB Legacy)** | Week 22, Slot 8 | `newsroom/config/article_types.json`, `newsroom/swarm_dispatcher.py`, `docs/EDITORIAL_MATRIX.md` |
| **Agent 9** | **Crisis Beat Allocation & Purgatory Draft Context** | Weeks 2–18 (Slot 6) & Midseason | `newsroom/slot_targets.py`, `newsroom/config/article_types.json`, `docs/EDITORIAL_MATRIX.md` |

---

## Detailed Task Specifications

### Agent 0: Prospect Tool, Postseason Draft Order & Mock Draft (runs in the `feat/draft-kiper-mock` session) — ✅ done Oct 15, merged into `main`
* **Objective:**
  * Give writer agents one scouted prospect board tool.
  * Make `get_draft_order` aware of playoff exits.
  * Ground `OFFSEASON_MOCK_DRAFT` in locked picks 1–24 (absorbs Agent 3).
  * Store the prospect board and each week's mock draft in the database, like every other engine's output.
  * Land `feat/draft-kiper-mock` on `main`.
* **Why:**
  * The branch already has a board tool (`get_draft_prospects`) and a mock-draft tool (`get_mock_draft`).
  * An earlier, uncommitted Agent 0 attempt in `main`'s checkout added a near-copy (`get_scouted_draft_board`). It also blocks the merge.
  * The board is the only dataset never stored in Postgres: it lives in git-ignored CSVs. Tools and tests ended up reading whatever local file existed (the "Dante Moore" stub). The mock draft is recomputed on every call, so a published article can't be checked against it, and prospects can't be linked to the `players` rows they become as rookies.
  * `get_draft_order` places only the Super Bowl winner and runner-up. It doesn't separate Wild Card (19–24), Divisional (25–28) or Conference Championship (29–30) exits. `MockDraftEngine` takes its order from `get_draft_order`, so fixing it fixes the mock draft too.
* **Specific Tasks:**
  1. **Revert the earlier attempt in `main`'s checkout.**
     * First save it for reference: `git diff` plus `tests/test_draft_tools.py`, into the session scratchpad.
     * Then restore `newsroom/db_tools.py`, `newsroom/swarm_dispatcher.py` and `tests/test_writer_config.py` to `HEAD`.
     * Remove that attempt's hunks from `PROJECT_SPEC.md`, keeping the Repository Layout line for this document.
     * Delete `tests/test_draft_tools.py`.
     * Leave `GEMINI.md` alone. This document stays untracked in `main`'s checkout until task 8.
  2. **Database storage (new).** Follow the repo's pattern: add the tables to `db/init_db.sql` and as a dated file in `db/migrations/`.
     * **`draft_prospects`**
       * Columns: `draft_season`, `projected_pick`, `projected_round` (1–7 or `UDFA`), `round_pick`, `full_name`, `position`, `college`, `height_in`, `weight`, `age`, `ovr`, `archetype`, `notes`, `source_capture`, `loaded_at`, and a nullable `player_id` that links to `players` once the prospect is a rookie.
       * Every column except `draft_season`, `projected_pick` and `full_name` is nullable.
       * `projected_pick` is unique within a season. *(As built: name + position is not unique, because two real prospects can share both; a re-scan replaces the class by pick.)*
       * A re-scan of the same draft season replaces that season's rows in one transaction.
     * **`mock_draft_picks`**
       * Columns: `season`, `week`, `pick`, `team_id`, `prospect_id` (→ `draft_prospects`), `locked`, `fit_score`, `rationale` and `generated_at`. Unique by (`season`, `week`, `pick`).
     * **Loading is lenient (JR, Oct 15).** A board that isn't perfect still loads.
       * A blank or implausible cell (height, weight, age, OVR, college, round/pick) is stored as NULL and listed in the load's warnings. Those values are filled in by next season's roster scan, once the prospect is a rookie.
       * A row with no readable name is skipped with a warning, not fatal.
       * The load stops only when nothing usable is left: an empty board, or no named rows.
       * `pipeline/ingest_prospects.load_prospects_from_csv` gets a matching lenient mode. Out-of-range values become `None` with a warning instead of raising. A duplicate projected pick keeps the first row and warns.
     * **`pipeline/load_capture.py`** writes the board to `draft_prospects` in one transaction, as the roster load does. It still writes `data/draft/{draft_season}_prospects.csv`, now only as an export. `--dry-run` reports the rows and warnings and writes nothing. `weekly_capture --mode prospects` therefore ends with the board in the database.
     * **Rookie link.** When a roster load meets a rookie (`years_of_experience` 0), it sets `draft_prospects.player_id` for that season's draft class. The match uses name + position, then college. Blank prospect fields are left as they are: the rookie's real measurables live in `players` / `player_seasons`.
     * **`engines/mock_draft_engine.py`** saves each run to `mock_draft_picks` for its (season, week), overwriting the same week's earlier run. The engine reads prospects from `draft_prospects`. Its CLI keeps `--file` for a board that hasn't been loaded.
  3. **One prospect board tool.** In `newsroom/db_tools.py`, replace `get_draft_prospects` with:
     * `@tool get_scouted_draft_board(season: int = 0, limit: int = 32, position: str = "")`.
     * It reads `draft_prospects` for the draft season (season + 1). NULL fields come back as `null`, so the writer knows they're unknown.
     * `position` filters by exact position or position group (`EDGE`, `OT`, `LB`…).
     * It returns JSON with `season`, `draft_season`, `source_file`, `total_prospects` and `prospects`. Each prospect has `projected_pick`, `projected_round`, `round_pick`, `name`, `pos`, `college`, `ht`, `wt`, `age`, `ovr`, `archetype` and `notes`.
     * Rename it everywhere it's used: `swarm_dispatcher.py` imports, `self.tools`, the writer research-tools list, `article_types.json` and tests.
     * `get_mock_draft` stays and returns the saved run for the week. It runs and saves the engine only when that week has no run yet, so every article in a week cites the same picks. Its hard-coded `2036_prospects.csv` fallback and CSV lookup go.
  4. **Playoff-exit draft order.** In `get_draft_order`, completed playoff rounds put eliminated teams in their brackets, each sorted by regular-season win percentage, then point differential:
     * Non-playoff teams: 1–18
     * Wild Card losers: 19–24
     * Divisional losers: 25–28
     * Conference Championship losers: 29–30
     * Super Bowl loser 31, winner 32
     * Each pick gets a `locked` flag (true once the team's season is over) and its `selection_status`.
     * Teams still alive keep provisional slots by record.
     * Only games visible to the run's cutoff week count (`run_context`).
  5. **Mock draft carries lock status.** `get_mock_draft(season, limit=32)` returns each selection with `locked` from the draft order, and `limit` truncates the board (24 for Week 20).
  6. **`OFFSEASON_MOCK_DRAFT` at 24 locked picks (Agent 3's task).** In `newsroom/config/article_types.json`, starting from the branch's `MEL_KIPER` version:
     * Name back to "Offseason Mock Draft 1.0 (Picks 1–24 Locked)". Writers: `MEL_KIPER`, `YATES`, `BARNWELL`.
     * `data_queried`:
       * "Round 1 Draft Order (get_draft_order)"
       * "Round 1 Mock Draft Simulation (get_mock_draft)"
       * "Scouted Prospect Board (get_scouted_draft_board)"
       * "Team Draft Needs (get_team_draft_needs)"
       * plus the existing P_draft, Y_prod and M_pos entries.
     * `directive` / `curatorial_autonomy`:
       * Call `get_draft_order` for the locked picks 1–24.
       * Call `get_mock_draft` (limit 24) as the starting projection.
       * Call `get_scouted_draft_board` for available prospects, using the position filter for team needs.
       * Balance team needs against positional value ($M_{\text{pos}}$ for QB, OT, EDGE, CB).
       * Never name a prospect who isn't on the board. Don't project picks 25–32, whose teams are still playing.
     * `structure_directive`:
       * Headline example "Picks 1–24".
       * Selection headers strictly `## [Pick Number]. [Team Name]: [Prospect Name], [Position], [School]`, followed by the prospect's exact height, weight and scouted OVR from the tool.
       * Closing `###` Draft Floor Watch / Sleeper.
  7. **Tests.**
     * A small fixture board, `tests/fixtures/prospects_board.csv`, used by all draft tests. It includes a few blank cells and one nameless row. No test reads `data/draft/`, `captures/` or the live database: database code is tested with mocks, the way the other loaders are.
     * Lenient load: blank cells become NULL with warnings, the nameless row is skipped, and an empty board stops the load.
     * A re-scan replaces the season's rows. The mock-draft run is saved per (season, week).
     * Board tool: JSON shape, `limit`, `position` filter, NULLs as `null`.
     * Registration in `swarm_dispatcher.py`.
     * Draft-order brackets: a mocked standings and playoff-results fixture covering a Wild Card, a Divisional and a Conference exit, plus a still-alive team flagged provisional.
     * `get_mock_draft(limit=24)` returns 24 picks with `locked` flags, from the saved run when there is one.
     * The `OFFSEASON_MOCK_DRAFT` config names all four tools and says 1–24.
  8. **Merge into `main`.**
     * Commit on `feat/draft-kiper-mock`, including this document.
     * Merge `main` into the branch. Resolve `newsroom/config/agent_personas.json`: keep `main`'s `CLARK` article persona (marquee-injury wording) plus the branch's `MEL_KIPER` entries. Reconcile anything else.
     * Run the full suite.
     * Apply the migration to the live database. Back it up first with `db/backup_db.sh`, as for other schema changes. Then load the board from `captures/2026-10-15_full_board3`.
     * Delete this document's untracked copy from `main`'s checkout (its content is now in the branch), then merge `feat/draft-kiper-mock` into `main`.
     * Update `PROJECT_SPEC.md` and `NOTES.md` as part of the same work.
* **Verification:**
  * `python3 -m pytest -q tests` passes on the branch and again on `main` after the merge.
  * `python3 -m engines.mock_draft_engine --season 2035` runs.
  * `get_scouted_draft_board` returns valid JSON against the real board, now from `draft_prospects`: 389 rows for draft season 2036.

---

### Agent 1: Reinstate `COACHING_HOT_SEAT_AUDIT` (Week 19, Slot 6)
* **Objective:** Create the "Black Monday Mandate" article archetype targeting eliminated head coaches following Week 18.
* **Why:** Week 19 currently duplicates `FRANCHISE_STATE_OF_THE_UNION` in both Slot 6 and Slot 7. Slot 6 should be the definitive national Black Monday evaluation.
* **Specific Tasks:**
  1. In `newsroom/config/article_types.json`, define `"COACHING_HOT_SEAT_AUDIT"`:
     * Allowed writers: `["SANDO", "BARNWELL"]`.
     * Data queried: `Baseline Roster Talent (R_rating)`, `Draft Order (Picks 1-18)`, `Coach of the Year / Wins Above Expectation (get_coach_of_the_year_race)`, `Head-Coach Profile (get_coach_profile)`.
     * Directive & Structure: 4-part Black Monday Mandate: `# The Black Monday Verdicts`, `## The Firing Line: Severe Underperformance vs. Talent`, `## On the Bubble: Ultimatum Years & Scheme Mandates`, `### The Safe Seats: Talent Deficit Pardons`.
  2. In `newsroom/swarm_dispatcher.py`:
     * Update `POSTSEASON_EDITORIAL_SLATES[19]`: Change `("Slot 6", "FRANCHISE_STATE_OF_THE_UNION")` to `("Slot 6", "COACHING_HOT_SEAT_AUDIT")`.
  3. In `newsroom/slot_targets.py`:
     * Add targeting logic for `COACHING_HOT_SEAT_AUDIT` in Week 19: target eliminated franchises where head coaches underperformed their roster expectation ($\Delta W = \text{Wins} - \text{Expected Wins} < -1.5$).
  4. In `docs/EDITORIAL_MATRIX.md`:
     * Update Week 19 Slot 6 to reflect `COACHING_HOT_SEAT_AUDIT`.
* **Verification:** Run `python3 -m unittest tests/test_slot_targets.py tests/test_newsroom_dispatch.py`.

---

### Agent 2: `PLAYOFF_AUTOPSY` Coaching Staff Audit (Weeks 20–22, Slots 6 & 7)
* **Objective:** Expand Section 4 from general organizational culture into a concrete Coaching Staff Accountability & Seat Temperature audit.
* **Why:** Postseason eliminations in the NFL trigger immediate coaching staff cross-examinations.
* **Specific Tasks:**
  1. In `newsroom/config/article_types.json` under `"PLAYOFF_AUTOPSY"`:
     * Update `curatorial_autonomy`: Instruct the writer to cross-examine head coach tenure, playoff record with the team, and game management using `get_coach_profile`.
     * Update `structure_directive` Section 4: Change from generic "Front Office & Coaching Seat Temperature" to:
       `4. Coaching Staff Accountability & Seat Temperature:`
       `Use a ## header (e.g., ## The Hot Seat & Coordinator Cross-Examination).`
       `Evaluate whether the head coach enters next season with an ultimatum. Cross-examine the team's situational failures in the elimination game (red-zone conversion stalls, fourth-down decision points, and third-down failures from the game box score) against their regular-season baseline from get_team_efficiency_profile. Deliver a concrete verdict: dismissal, coordinator overhaul, or ascending foundation patience.`
* **Verification:** Run `python3 -m unittest tests/test_writer_config.py`.

---

### ~~Agent 3: `OFFSEASON_MOCK_DRAFT` Real Prospect Integration (Week 20, Slot 4)~~ → done by Agent 0, task 5
Not dispatched separately. The `feat/draft-kiper-mock` branch had already rewritten this entry (Mel Kiper as lead writer, `get_mock_draft`, the strict selection-header format). Agent 0 finishes it to this spec, at picks 1–24.

---

### Agent 4: `DRAFT_BLUEPRINT_ROSTER_AUDIT` Core Diagnostics (Week 21, Slot 5)
* **Objective:** Anchor the 28 eliminated franchises in their locked draft positions 1–28 and foundational roster age curves.
* **Why:** Post-Divisional round, 28 of 32 teams are locked into their draft slots.
* **Specific Tasks:**
  1. In `newsroom/config/article_types.json` under `"DRAFT_BLUEPRINT_ROSTER_AUDIT"`:
     * Add `"Round 1 Draft Order (get_draft_order)"` and `"Team Draft Needs (get_team_draft_needs)"` to `data_queried`.
     * Update `curatorial_autonomy`: Mandate pulling `get_draft_order` for picks 1–28. Group teams into distinct core diagnostics:
       - Ascending youth cores ($Y_{\text{prod}} \ge 0.35$) with high picks needing an immediate cornerstone.
       - Rosters triggering the Purgatory Penalty stuck with picks 12–20 needing a total identity reset.
     * Update `structure_directive` Section 2: Require 3–4 featured franchises to list their exact locked pick number and top 3 positional needs.
     * *(New, from Agent 0's tools.)* Add `"Scouted Prospect Board (get_scouted_draft_board)"` to `data_queried`. For each featured franchise, the writer may name one or two real prospects at its top need (`position` filter), and must never invent one.
* **Verification:** Run `python3 -m pytest -q tests/test_writer_config.py`.

---

### Agent 5: `FRANCHISE_STATE_OF_THE_UNION` Rookie-Coach Alignment (Weeks 18–19)
* **Objective:** Deepen audit of lottery franchises (Picks 1–5) by evaluating whether front offices should trust the incumbent staff with a top rookie.
* **Why:** Franchises with early lottery picks frequently debate whether to let an existing coach groom a rookie QB or reset the regime.
* **Specific Tasks:**
  1. In `newsroom/config/article_types.json` under `"FRANCHISE_STATE_OF_THE_UNION"`:
     * Add `"Round 1 Draft Order (get_draft_order)"` to `data_queried`.
     * Update `curatorial_autonomy`: Add the **"Coach-Rookie Alignment Test"**:
       `When analyzing early-lottery teams (Picks 1–5), cross-reference their draft slot (get_draft_order) and primary positional need (get_team_draft_needs) against the head coach's tenure, scheme, pass lean, and career win percentage (get_coach_profile). Explicitly interrogate whether the front office should trust the incumbent staff to groom an incoming franchise quarterback or foundational tackle, or if drafting a blue-chip asset necessitates a clean coaching break.`
     * *(New, from Agent 0's tools.)* Add `"Round 1 Mock Draft Simulation (get_mock_draft)"` to `data_queried`. The Coach-Rookie Alignment Test may name the prospect the mock draft gives the team. That pick is only a projection.
* **Verification:** Run `python3 -m pytest -q tests/test_writer_config.py`.

---

### Agent 6: `SUPER_BOWL_MEDIA_INTERVIEW_AUDIT` Head Coach Milestone Records (Week 23, Slot 4)
* **Objective:** Query head coach tenure, career postseason win-loss records, and historical Super Bowl records directly from the database.
* **Why:** Elevates media week psychology from generic press conferences into high-stakes historical pressure.
* **Specific Tasks:**
  1. In `newsroom/config/article_types.json` under `"SUPER_BOWL_MEDIA_INTERVIEW_AUDIT"`:
     * Add `"Career Postseason & Super Bowl Coaching Records (get_coach_profile)"` to `data_queried`.
     * Update `curatorial_autonomy`: Require calling `get_coach_profile` for both head coaches. Ground the psychological audit in their career postseason records, Super Bowl wins/losses, and tenure length.
     * Update `structure_directive` Section 1 and Section 2: Contrast the pressure on a coach seeking dynasty validation versus a first-time participant absorbing the Super Bowl media circus.
* **Verification:** Run `python3 -m unittest tests/test_writer_config.py`.

---

### Agent 7: `SUPER_BOWL_COMPREHENSIVE_PREVIEW` Scheme Exploitation & Play-Calling (Week 23, Hero Slot)
* **Objective:** Ground Section 4 in measured coaching tendencies (`get_matchup_edges` and `get_team_efficiency_profile`).
* **Why:** Grounding the tactical chess match in actual simulation measurements prevents vague or hallucinated coaching descriptions.
* **Specific Tasks:**
  1. In `newsroom/config/article_types.json` under `"SUPER_BOWL_COMPREHENSIVE_PREVIEW"`:
     * In `curatorial_autonomy`: Explicitly mandate pulling the `coaching_matchup` block from `get_matchup_edges` (pass lean, fourth-down aggressiveness, defensive front, and coverage tendency) and team situational rates from `get_team_efficiency_profile`.
     * Update `structure_directive` Section 4 ("The Chess Match"):
       Instruct the writer to deconstruct how the offensive play-caller's measured pass lean and fourth-down aggressiveness collide with the opposing defensive front and coverage philosophy, supported by regular-season red-zone touchdown conversion rates and third-down ranks.
* **Verification:** Run `python3 -m unittest tests/test_writer_config.py`.

---

### Agent 8: Week 22 Slot 8 Legacy Column (`SUPER_BOWL_HISTORICAL_STAKES`)
* **Objective:** Reinstate the joint Head Coach + Quarterback historical legacy breakdown in Week 22 Slot 8.
* **Why:** In Week 22 (Super Bowl Bye), Slot 8 was temporarily given to `PLAYER_DEEP_PROFILE` because coach histories were missing. We can now compare the QB-HC tandems fighting for the Lombardi Trophy.
* **Specific Tasks:**
  1. In `newsroom/config/article_types.json`:
     * Create or update `"SUPER_BOWL_HISTORICAL_STAKES"` (or `"CHAMPIONSHIP_LEGACY_AUDIT"`):
       - Writers: `["CHADIHA", "SIMMONS"]`.
       - Data queried: `Quarterback Playoff Clout (S_playoff)`, `Awards Equity (A_awards)`, `Head-Coach Profile (get_coach_profile)`, `Legacy Floor (S_all-time)`.
       - Structure: 4-part joint audit: `# The Historical Crucible`, `## Tandem A: [Coach] & [QB] (Dynasty vs. Validation)`, `## Tandem B: [Coach] & [QB] (The Breakthrough)`, `### Hall of Fame & Canton Implications`.
  2. In `newsroom/swarm_dispatcher.py`:
     * Update `POSTSEASON_EDITORIAL_SLATES[22]`: Change `("Slot 8", "PLAYER_DEEP_PROFILE")` to `("Slot 8", "SUPER_BOWL_HISTORICAL_STAKES")`.
  3. In `docs/EDITORIAL_MATRIX.md`:
     * Update Week 22 Slot 8 to reflect the joint legacy column.
* **Verification:** Run `python3 -m unittest tests/test_slot_targets.py tests/test_newsroom_dispatch.py`.

---

### Agent 9: Crisis Beat Allocation & Purgatory Draft Capital Context
* **Objective:** Dynamically route regular-season Slot 6 to teams in coaching crisis, and inject draft capital context into midterm progress reports.
* **Why:** Replaces the third generic matchup primer with an accountability beat piece for dysfunctional franchises, and grounds midseason audits in draft positioning.
* **Specific Tasks:**
  1. In `newsroom/slot_targets.py`:
     * In regular-season weeks (Weeks 2–18), convert Slot 6 into a "Crisis Beat" slot: target teams with the worst win-differential vs expectation ($\Delta W = \text{Wins} - \text{Expected Wins} \le -2.0$) or losing streaks with sub-.350 win percentages. Slots 7 and 8 remain `LOCAL_MATCHUP_PRIMER`.
  2. In `newsroom/config/article_types.json`:
     * In `"LOCAL_SEASON_PROGRESS_REPORT"` and `"SEASON_PROGRESS_AUDIT"`, add `"Draft Order (get_draft_order)"` to `data_queried`.
     * In both curatorial autonomy blocks: When diagnosing teams in the Purgatory Penalty, weigh their remaining schedule against their projected draft capital (e.g. escaping the middle tier vs securing a top-5 pick).
  3. In `docs/EDITORIAL_MATRIX.md`:
     * Document Slot 6 as the "Crisis Beat / Hot Seat Accountability" primer in Weeks 2–18.
* **Verification:** Run `python3 -m unittest tests/test_slot_targets.py`.

---

## Conflict Avoidance & Merging Strategy
* **Non-Conflicting Boundaries:**
  * Agents 2, 4, 5, 6, and 7 modify **different, non-overlapping JSON objects** in `newsroom/config/article_types.json`. Agent 0 already owns `OFFSEASON_MOCK_DRAFT`.
  * Agents 1, 8, and 9 touch distinct keys in `newsroom/swarm_dispatcher.py` (`POSTSEASON_EDITORIAL_SLATES`) and distinct functions in `newsroom/slot_targets.py`.
* **Known near-overlaps (expect small conflicts; resolve by keeping both sides):**
  * Agents 1 and 8 edit the Week 19 and Week 22 rows of `docs/EDITORIAL_MATRIX.md`, three lines apart. Agent 9 edits the same file's regular-season section.
  * Every agent updates `PROJECT_SPEC.md` (CLAUDE.md), so adjacent spec lines can collide.
* **Merge Sequence:**
  1. **Agent 0 merges `feat/draft-kiper-mock` first.** That puts the `draft_prospects` / `mock_draft_picks` tables, `get_scouted_draft_board`, `get_mock_draft`, the playoff-exit draft order, `MEL_KIPER`, the 24-pick `OFFSEASON_MOCK_DRAFT`, the automated board scan and the OCR fixes on `main`.
  2. The Agent 0 session then starts Agents 1, 2 and 4–9, each in its own worktree from the merged `main`.
  3. Merge them one at a time on approval. Before each merge, the agent brings in the latest `main`; stop and report on any conflict.
  4. Final verification: run `tools/verify_newsroom.py` and `python3 -m pytest -q tests` on `main`.
