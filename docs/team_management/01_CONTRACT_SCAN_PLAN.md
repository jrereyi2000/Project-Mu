# Step 1: Contract scan (sub-plan)

Written 2026-10-19 for [`TEAM_MANAGEMENT_PLAN.md`](../TEAM_MANAGEMENT_PLAN.md), step 1. This plan builds within the master's fixed interfaces: §2.1 schema, §2.3 capture conventions, D5/D7, and the §3 console facts. Where it extends them, it says so.

**Delivers:** `madden_capture.py --only contracts`, `contracts.csv` + `team_cap.csv` from `extract.py`, `pipeline/money.py`, migration `2026_12_07_contracts.sql` (folded into `init_db.sql`), a `contracts` load mode, `weekly_capture.py --mode contracts [--teams CAR,NO]`, a contracts phase in the Season scan, and a `#debug` button.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence for this step |
|---|---|---|
| S1 | `ROSTER_MENU` already lists `"TEAM SALARY"` (`madden_capture.py:470`). `cross()` (`:641`) allows Cross on `HUB_ROSTER` only when ROSTER, INJURY REPORT or SCOUTING is highlighted (`:646-650`). | Add `"TEAM SALARY"` to that tuple **by name**. TRADE, FREE AGENCY and CONTRACTS stay refused (D7). Add a test for each refused item. |
| S2 | `classify()` (`:503`) returns `ROSTER_LIST` on `SAVINGS` + `PENALTY` + `MOR` (`:516-518`). The right edge of PLAYER_STATS is recognised by `VIEW PLAYER CARD` + `SORT` + `R2` (`:554-555`). | TEAM SALARY's right edge also shows SAVINGS and PENALTY, and may show the same button bar. Its rule must come **before** both of these rules and must use the header strip, not the table headers (C3). |
| S3 | `LIST_SCREENS` (`:117`) decides which key `stream_frozen()` (`:123`) presses to probe a frozen stream. | Add `TEAM_SALARY` there, so Down/Up is the probe. |
| S4 | `capture_wide_list(folder, base, rows_per_page, ..., traverse=traverse, ...)` (`:1175`) zig-zags left↔right per page. It writes `{base}_NNN.png` + `{base}_right_NNN.png` at the same row position and ends when a page repeats. `traverse()` (`:1159`) calls the module-level `at_edge()` (`:1151`), whose right edge is `"DOWNS PLAYED"`, so it only fits Season Stats. | Reuse `capture_wide_list` as is, and pass a `salary_traverse` built on a `salary_at_edge()`: left = `NAME` header found, right = `NO TRADE CLAUSE` (C4). `rows_per_page=11`. Pass `confirm_end=True`, because dropped Down presses after a restart are documented at `:1113-1115`. |
| S5 | `run_injuries(out, target_teams=None)` (`:2718`) opens its screen with `open_injury_report()` (`:2691`: go_hub → MANAGE ROSTER → move_to → `cross_into`), reads the team with `injury_team()` (`:2704`), and cycles with L2 (3 tries, then `stream_frozen()` → `recover_stream()` → reopen). It names files `NN_team.png`, writes `done.txt`, and raises if L2 wraps to the first team early. | This is the model for `open_team_salary()`, `salary_team()` and `run_contracts()`. A team needs a **folder**, not one png, so the done/partial handling follows `run_roster()` (`:3347`): `done.txt` lines `NN TEAM`, and `clear_partial()` removes folders of unfinished teams. |
| S6 | `main()` `--teams` is already `type=int` ("--only roster: stop after this many teams", `:3542`). | `madden_capture.py` needs a **new** flag for a team subset: `--team-names PANTHERS,SAINTS` (nicknames, the same strings as `TEAMS` at `:497`). `weekly_capture.py --teams CAR,NO` (abbreviations, master §2.3) maps them to nicknames through `teams.abbreviation`/`teams.name`. |
| S7 | The `--only season` block (`:3631-3687`) runs schedule → rosters → team overalls → injuries → prospects. Each phase is skipped on `--resume` when its `done.txt` exists, and is followed by `go_hub()`. | Insert contracts **right after rosters**, in the same skip-on-done pattern. Both screens are on the Manage Roster tab. |
| S8 | `extract.py` reads every table with Gemini. Wide tables go through `merge_wide(left, right, label)` (`:272`, paired by page number and row position, with a disagreement warning). Misreads are settled by `vote_misreads(..., key_cols=["NAME","POS"])` (`:376`). Roster list pages get their own crop and note (`ROSTER_LIST_BOX` `:128`, `_is_roster` `:144`, re-read when short `:179-190`). Only fixed single-screen layouts use Apple Vision (injuries, team overalls, draft recap, prospects: `:957-976`). | Contracts use **Gemini through `merge_wide`** (decision in Phase 3). |
| S9 | `load_capture.detect_mode()` (`:46`) picks the mode from the CSVs present. `LOADERS` (`:1333`) maps a mode to `fn(cur, folder, season, report)`. `load()` (`:1338`) writes the `captures` row through `capture_row()` (`:1250`), then runs the loader in one transaction. In season mode it first rolls `league_state` to season+1, week 1 (`:1348-1351`). Loaders are **not** given the capture id. | Add a `contracts` mode. `load()` puts `report["capture_id"] = cid` before calling the loader (a one-line change, `None` on dry runs). `load_season()` (`:1311`) calls `load_contracts` right after `load_roster`. |
| S10 | `load_roster()` stops on ambiguity: `match_roster()` returns `ambiguous` → `LoadError("players that can't be told apart (nothing written)")` (`:146-150`). `roster(cur, season)` (`:366`) returns `(player_id, name_key, team_id, position, player_name)` for the season. `load_injuries()` (`:791`), by contrast, **guesses** (falls back to `candidates[0][0]`, `:860`). | The contracts matcher follows the roster load: 0 or >1 candidates is a `LoadError` naming every bad row. It never guesses. It uses `capture_matching.name_key()` (`:65`) and `full_key()` (`:79`). |
| S11 | `captures.mode` is `VARCHAR(32)` with no CHECK (`init_db.sql:357-368`). `league_state` is a single row (`:17`). `teams.name` is the nickname (`load_roster` keys `upper(name)` against `PANTHERS`, `load_capture.py:79`). | `mode='contracts'` needs no schema change. The team folder name maps to `teams.id` through `upper(name)`. |
| S12 | `weekly_capture.py`: `MODES` (`:53`), `folder_mode()` (`:92`), `capture_done()` (`:111`), `extract_done()` (`:124`), `step_capture()` (`:206`, season args `:229-231`, timeout `CAPTURE_TIMEOUT * 2` = 150 min), `run()` (`:415`, lock `captures/.weekly_run.lock`), and the early return for roster/prospects/draft-results (`:514`). | Each one gets a `contracts` branch (Phase 5). The season timeout has to grow by about 40 min. |
| S13 | `server/debug_api.py` `JOB_KINDS` (`:34-44`): kind → (label, weekly_capture args). `job_command()` (`:529`). The page lists the kinds in `Mu_Network/src/DebugPage.tsx:51-53`. `tests/test_debug_api.py` has a test per scan kind (`:55-75`). | Add `contract_scan` → `["--mode","contracts","--no-articles"]`, plus a DebugPage entry and a test. |
| S14 | Offline test patterns: `tests/test_madden_capture.py` builds a fake `_Frame` (`:93`) for `classify()` and is skipped off macOS. `tests/test_extract.py` seeds `extract._cache` with table dicts (`:24-99`), so no Gemini call is made. `tests/test_load_capture.py` uses a fake cursor (`:37+`). Memory: `test_napper_api`/`test_napper_engines` write to the live DB. | New tests use only these patterns. **No new test opens a psycopg connection.** |
| S15 | `pipeline/money.py` doesn't exist. `capture_matching.num()` (`:113`) handles plain integers only. | New module (Phase 1). |
| S16 | Checked 2026-10-19: the live `league_state` is season 2036, week 4, which matches TEAM SALARY's `36 CAP`. The spec's "2035, week 18" was stale and has been fixed. | The year check (Phase 4) passes for an in-season scan now. Q2 is left with only the offseason window (C11). |

Unrelated bug noticed and not fixed here: `load_team_overalls()` calls `cur.fetchall()` twice (`load_capture.py:909-910`), so `by_abbr` is always empty.

---

## 1. Console facts to check (JR at the console; don't build on these until checked)

Phase 0 checks them with `madden_capture.py --test` and `--step none|down|right|l2` (read-only, plus the presses JR approves). Each answer goes back into this table.

| # | Question | Why it matters | Default if unverified |
|---|---|---|---|
| C1 | Does Cross on Manage Roster > TEAM SALARY open the table directly, with no prompt or sub-menu? Does Back return to `HUB_ROSTER`? | `open_team_salary()` and `go_hub()` | none: stop on anything else |
| C2 | Where is the team nickname on screen (a chip or header, and the box), and does L2 switch it in place the way the Injury Report does? After VIKINGS, does it wrap to the first team or go to an NFL entry? | `salary_team()` and the wrap check | the `injury_team()` geometry |
| C3 | The full OCR text at both edges, and the Manage Roster hub with TEAM SALARY highlighted: does the hub show CAP SPACE or ROLLOVER anywhere (a cap tile)? Does the table show MOR, VIEW PLAYER CARD or SORT? | Classifier rule order (S2). Proposed rule: `"ROLLOVER" in t and "CAP SPACE" in t and ("ROOKIE RES" in t or "TOTAL SPEND" in t)` and not hub tabs (`GAMEDAY` + `COACH CENTRAL`) | none |
| C4 | Do D-pad Left and Right scroll the columns without wrapping, and is `NO TRADE CLAUSE` (or `NO TRADE`) readable at the right edge and `NAME` at the left? | `salary_at_edge()` | none |
| C5 | Down moves the highlight. Where does the list start scrolling under it (the roster scrolls at row 8)? Is the last page short or overlapping? Does L2 reset the list to the top row and the left edge? | Page overlap is harmless (merge dedupes). A list that doesn't reset needs `list_to_top()`-style Up presses before each team. | press Up to the top and traverse left before each team |
| C6 | Which rows are listed: the 53-man roster only, or IR and practice squad too? Are released players with dead money listed? | Row-count bounds in the loader | `ROSTER_TEAMS_MIN/MAX` 45–56 (`load_capture.py:72`) |
| C7 | How is NAME shown: `G.Crawford` (like the roster list) or a full name? | Matching uses `name_key()` either way, but the fixture must match | `G.Crawford` |
| C8 | **Answered (JR, 2026-10-19): the left and right `BONUS` are the same number.** | `merge_wide` treats same-named columns as one and counts a disagreement as a misaligned page. §2.1 has a single `bonus`. | treat them as different: rename the right-hand one `BONUS_R` before merging, keep it in the CSV, and store only the left one (Q1) |
| C9 | What does a NO TRADE CLAUSE cell show (YES/NO, a check icon, blank)? INCENTIVE values besides `NONE`? CONV: an icon (as on the roster)? | Gemini note and parsing | NTC: YES → true, anything else false. CONV ignored. |
| C10 | Do the header identities hold on screen: ADJUSTED CAP = CAP + ROLLOVER, TOTAL SPEND = SALARIES + CAP PENALTY, CAP SPACE = ADJUSTED CAP − TOTAL SPEND? | Used as a read check on the cap strip (within display rounding) | warnings only |
| C11 | In the offseason (weeks 23–24, before the Season scan rolls `league_state`), does TEAM SALARY already read `37 CAP`? | The year check (Q2) | the loader stops on a mismatch |

---

## 2. Phases

Each phase is mergeable on its own. Nothing in Phases 1–4 changes what a weekly run does.

### Phase 0: Console mapping and fixtures (JR at the console, ~20 min)
- JR answers C1–C11 with `--test` / `--step` on TEAM SALARY at both edges, after an L2, and after Down ×11. The `--step` mapping tool saves a screenshot + OCR to `explore/` (`madden_capture.py:26`). Cross on the hub needs `--step cross --allow-cross`, with JR's OK.
- Save as fixtures: `tests/fixtures/team_salary/` holds the OCR item lists of 4 frames as JSON (hub with TEAM SALARY highlighted, left edge, right edge, PANTHERS after L2). It also holds **one team's pages downscaled** (left/right `_000`, `_001`) for extraction fixtures, plus a hand-written `expected_contracts.csv` and `expected_team_cap.csv` for that team.
- Update §1 of this plan with the answers.
- **Exit:** every C-row has an answer or an explicit default JR accepts.

### Phase 1: Money parser and schema
**Files**
- `pipeline/money.py`: `parse_money(s: str) -> int | None`. It strips spaces and `,`, maps Unicode minus and en/em dash to `-`, and accepts `-$29.5M`, `$-29.5M` and `($29.5M)`. Suffixes: `K` ×1e3, `M` ×1e6, `B` ×1e9, none = dollars. It uses `decimal.Decimal`, so `16.8 × 1e6` is exactly `16800000`. `""`, `-`, `--`, `N/A` → `None`. Anything else raises `ValueError(f"not money: {s!r}")`, so OCR junk can't become a number. A leading `S` is **not** read as `$`: the loader reports it.
- `db/migrations/2026_12_07_contracts.sql` (BEGIN/COMMIT, idempotent, with the usual header: back up first, then `docker exec ... psql`), exactly as master §2.1:
  - `team_cap_seasons (team_id INT NOT NULL REFERENCES teams(id), season INT NOT NULL, cap BIGINT, rollover BIGINT, adjusted_cap BIGINT, cap_space BIGINT, salaries BIGINT, cap_penalty BIGINT, total_spend BIGINT, next_rookie_reserve BIGINT, scanned_at TIMESTAMPTZ NOT NULL DEFAULT now(), capture_id INT REFERENCES captures(id) ON DELETE SET NULL, PRIMARY KEY (team_id, season))`.
  - `player_contracts (id SERIAL PRIMARY KEY, player_id INT NOT NULL REFERENCES players(id), team_id INT NOT NULL REFERENCES teams(id), season INT NOT NULL, length INT, years_remaining INT, total_value BIGINT, bonus BIGINT, cut_savings BIGINT, cut_penalty BIGINT, no_trade_clause BOOLEAN NOT NULL DEFAULT false, incentive TEXT, status VARCHAR(12) NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','SUPERSEDED','RELEASED','TRADED')), source VARCHAR(12) NOT NULL DEFAULT 'SCAN' CHECK (source IN ('SCAN','TRANSACTION')), scanned_at TIMESTAMPTZ NOT NULL DEFAULT now(), capture_id INT REFERENCES captures(id) ON DELETE SET NULL)`, plus `CREATE UNIQUE INDEX player_contracts_one_active ON player_contracts (player_id, season) WHERE status = 'ACTIVE'` and `CREATE INDEX player_contracts_team_season ON player_contracts (team_id, season, status)`.
  - `player_contract_years (contract_id INT NOT NULL REFERENCES player_contracts(id) ON DELETE CASCADE, year INT NOT NULL, cap_hit BIGINT NOT NULL, PRIMARY KEY (contract_id, year))`.
  - `vw_team_cap_sheet`: one row per `ACTIVE` contract. Columns: `team_id`, `abbreviation`, `season`, `contract_id`, `player_id`, `player_name`, `position` (from `player_seasons` for that season), `length`, `years_remaining`, `contract_end_season` (= `season + years_remaining - 1`, per §2.1), `total_value`, `bonus`, `cut_savings`, `cut_penalty`, `no_trade_clause`, `incentive`, `cap_hit_y0` … `cap_hit_y4` (years `season` … `season+4`, pivoted from `player_contract_years`), and the team row `cap`, `adjusted_cap`, `cap_space`, `total_spend`, `next_rookie_reserve` (LEFT JOIN `team_cap_seasons` on team and season). Relative year columns keep the view the same from season to season. Steps 3 and 5 read only this view and the three tables.
- `db/init_db.sql`: the same DDL folded in, after `captures` (it references `captures`). Stage only this hunk: other sessions edit this file (memory: shared checkout).

**Tests:** `tests/test_money.py` (pure): `$16.8M`, `$200K`, `$0`, `-$29.5M`, `$3.93M`, `$1,250,000`, `$1.25M`, blanks → None, junk → ValueError, no float drift (`$0.7M` → 700000).
**Exit:** tests pass. JR approves applying the migration (after `db/backup_db.sh`); it's applied once on the live DB, then re-run to prove it's idempotent. `\d player_contracts` matches §2.1.

### Phase 2: `madden_capture.py --only contracts`
**Files:** `madden_capture.py` only.
- `classify()`: a `TEAM_SALARY` rule (C3) placed before the `ROSTER_LIST` rule (`:516`). Add `"TEAM_SALARY"` to `LIST_SCREENS`. `go_hub()` needs nothing: it already presses Back on any non-hub screen (`:738-739`).
- `cross()`: the HUB_ROSTER tuple becomes `("ROSTER", "INJURY REPORT", "SCOUTING", "TEAM SALARY")`, and the error text names them. `TEAM_SALARY` itself is **not** added to the allowed screens, so Cross on the table is refused (it would open a contract action).
- `open_team_salary()`, modelled on `open_injury_report()` (`:2691`): `cross_into("HUB_ROSTER", ["TEAM_SALARY"], timeout=8)`.
- `salary_team(f)`: from C2 geometry, returns a member of `TEAMS`.
- `salary_at_edge(f, direction)` and `salary_traverse(direction, burst=6, max_rounds=12)`: the same loop as `traverse()` (`:1159`), with its own edge test. The shared `traverse` is not touched.
- `read_cap_strip(f)` is **not** done on the Mac. The strip is in every page screenshot, and extraction reads it (Phase 3).
- `capture_team_salary(out, index, team)`: folder `contracts/<NN>_<team>/`. Puts the list at the top and the left edge (C5), then `capture_wide_list(folder, "salary", rows_per_page=11, traverse=salary_traverse, confirm_end=True)`. If it returns fewer than 3 pages, log a WARNING (53 rows need ≥5 pages).
- `run_contracts(out, target_teams=None, resume=False)`, modelled on `run_injuries` (L2 loop, 3 tries, `stream_frozen()` → `recover_stream()` → `open_team_salary()`, wrap check). Done handling follows `run_roster`: `contracts/done.txt` lines `NN TEAM`, written only after a team's pages are complete. `clear_partial()` removes folders not in `done.txt`, and runs on start and after every Abort. The target list is written to `contracts/targets.txt` on the first run. A resume without `--team-names` reads it back, so a resumed partial re-scan can't widen to 32 teams. The run ends when every target is in `done.txt`. It stops (Abort) if L2 wraps to the first team before then, naming the missing teams.
- `main()`: add `"contracts"` to `--only` choices, add `--team-names` (comma-separated nicknames, validated against `TEAMS`, an unknown name exits), and add the `--only contracts` branch: `run_mode("team salaries", lambda: run_contracts(out, targets, resume=bool(a.resume) or tries>1))`, with the `tries` counter pattern from rosters (`:3641-3655`). In the `--only season` block, add a contracts phase after rosters, skipped on `--resume` when `contracts/done.txt` lists 32 teams, followed by `go_hub()`. Add `contracts` to the `--resume` help text.

**Tests** (`tests/test_madden_capture.py`, fake frames from the Phase 0 OCR JSON):
- Both TEAM SALARY edges classify as `TEAM_SALARY`, and the hub with TEAM SALARY highlighted stays `HUB_ROSTER`.
- The existing ROSTER_LIST and PLAYER_STATS-right-edge tests still pass.
- `cross("HUB_ROSTER", frame)` is allowed on TEAM SALARY and raises on TRADE, FREE AGENCY, CONTRACTS and UPGRADE PLAYERS. Mock `press`, and assert it isn't called on the refused items.
- `cross("TEAM_SALARY")` raises.
- `salary_at_edge` on both fixtures.
- `run_contracts` resume bookkeeping with `open_team_salary`, `snap`, `press` and `capture_team_salary` mocked: the done/targets round trip, `clear_partial` removing an unfinished folder, a subset run stopping when its targets are done, and a wrap before completion raising.

**Exit:** JR runs `python3 madden_capture.py --only contracts --team-names PANTHERS` (~1.5 min). The folder holds `salary_NNN.png`/`salary_right_NNN.png` pairs covering every row. Then a full 32-team run, interrupted once (Ctrl-C) and `--resume`d, finishes with 32 lines in `done.txt` in ≤45 min.

### Phase 3: Extraction → `contracts.csv`, `team_cap.csv`
**Decision: Gemini, through the existing `merge_wide` path, not Apple Vision.** Why:
1. Every table captured with `capture_wide_list` is already read this way (`merge_wide` + `vote_misreads`), including the left/right pairing by row position. Vision would need a new column-geometry reader for 19 columns at two edges.
2. Apple Vision drops or misreads the digit 1 in Madden's font (`madden_capture.py:2832-2834`, the reason roster card values are re-read by Gemini). Money cells (`$1.1M`, `$11.1M`) are full of 1s, and one wrong digit is a wrong cap figure.
3. Vision is used today only for single-screen fixed layouts (injuries, team overalls, draft recap, prospects).

Cost: about 32 teams × ~6 pages × 2 halves ≈ 400 half-size crops, about the roster scan's list-page load. It's recorded by `UsageMeter` as today.

**Files:** `extract.py` only.
- `SALARY_LIST_BOX` (from Phase 0 frames: the table rows and their headers, **excluding** the player card on the right, as `ROSTER_LIST_BOX` does). `_is_salary(path)` matches `/contracts/` in the path. In `_read_table_remote`, salary pages get `PROMPT + SALARY_NOTE` and the re-read-when-short rule (11 rows, the roster's `:179-190` loop). `SALARY_NOTE`: money exactly as shown (`$16.8M`, `$200K`, `$0`, `-$1.2M`); CONV is an icon, return `""`; the C9 NTC rule; year headers as 4-digit years.
- `CapStrip` pydantic model (cap_label, cap, rollover_label, rollover, adjusted_cap, cap_space, salaries, cap_penalty, total_spend, rookie_res_label, rookie_res: all `str`, values as shown, labels like `36 CAP`, `35 ROLLOVER`, `37 ROOKIE RES`) and `SALARY_HEADER_BOX`. `read_cap_strip(team_dir)` reads the strip from `salary_000.png` and `salary_right_000.png`. If the two readings disagree, or a C10 identity fails by more than $0.2M, it reads `salary_001.png` and keeps the majority. With no majority it raises, and extract fails, so nothing half-read loads.
- `extract_contracts(root)`, modelled on `extract_roster()` (`:509`):
  1. `prefetch` every page under `contracts/*/`.
  2. Per team, `vote_misreads(team, lambda: merge_wide(left, right, team), both, ["NAME","POS"])`. C8 is answered (same figure), so the shared `BONUS` header merges as one column, and a disagreement flags a misaligned row. `merge_wide` needs no change.
  3. Write `contracts.csv`: `TEAM` + the merged columns (`NAME, POS, AGE, OVR, LEN, REM, TOTAL, BONUS, SAVINGS, PENALTY, 2036…2040, INCENTIVE, NO TRADE CLAUSE`). Values stay raw strings. The loader parses them (the roster CSVs work the same way).
  4. Write `team_cap.csv`: `TEAM, CAP_LABEL, CAP, ROLLOVER_LABEL, ROLLOVER, ADJUSTED_CAP, CAP_SPACE, SALARIES, CAP_PENALTY, TOTAL_SPEND, ROOKIE_RES_LABEL, ROOKIE_RES`, raw strings.
  5. Warnings (printed, not fatal): a team with < 40 rows; a year header list that isn't 5 consecutive years.
- `main()`: call `extract_contracts(root)` after `extract_roster(root)`. Update the module docstring's "Writes" list.

**Tests** (`tests/test_extract.py`, offline; seed `extract._cache` from the Phase 0 expected tables):
- A Panthers left/right pair merges to the expected rows.
- An overlapping last page doesn't duplicate rows.
- The cap strip: disagreement triggers a third read, majority wins, and no majority raises (mock `_generate`).
- `_is_salary` / crop selection.

**Exit:** `python3 extract.py captures/<phase-2 run>`. JR spot-checks two teams' CSV rows against the screenshots (all 19 columns, one cap strip each).

### Phase 4: Loader, `contracts` mode
**Files**
- `pipeline/capture_matching.py`: pure `match_contract_rows(rows, candidates) -> (matches: {i: player_id}, problems: [str])`. Candidates are `(player_id, team_id, name_key, full_key, position, age, ovr)` for the season. The steps for a row:
  1. Same team, `name_key(NAME)` equal (or `full_key` if C7 shows full names).
  2. If more than one, the same `norm_pos`-style position.
  3. If still more than one, AGE and then OVR equal.
  4. Exactly one → match. 0 → `"{team} {NAME} {POS}: not on the team's {season} roster"`. More than one → `"... can't be told apart"`.
  5. Two rows matching the same player is a problem too.
- `pipeline/contracts.py` (DB writes, reusable by step 2):
  - `supersede_active(cur, season, team_ids, player_ids) -> int`: `UPDATE player_contracts SET status='SUPERSEDED' WHERE season=%s AND status='ACTIVE' AND (team_id = ANY(%s) OR player_id = ANY(%s))`. The `player_id` arm clears a player's ACTIVE row on another team, such as a move step 2 hasn't recorded yet. Otherwise the partial unique index would reject the insert.
  - `insert_contract(cur, *, player_id, team_id, season, length, years_remaining, total_value, bonus, cut_savings, cut_penalty, no_trade_clause, incentive, years: dict[int,int], source='SCAN', capture_id=None) -> int`: one `player_contracts` row plus one `player_contract_years` row per year (`$0` → 0).
  - `upsert_team_cap(cur, team_id, season, values, capture_id)`: `ON CONFLICT (team_id, season) DO UPDATE` (latest wins).
- `pipeline/load_capture.py`:
  - `detect_mode()`: `contracts.csv` alone → `"contracts"`. A folder that also has the season CSVs stays `"season"`.
  - `load()`: `report["capture_id"] = cid`. Store `week` for contracts runs too (the current week), so the captures row says when the scan was taken.
  - `load_contracts(cur, folder, season, report)`. Every check collects into `problems`, and any problem → `LoadError(...)` with nothing written. The checks:
    1. **Year check (master §3.2):** each team's `CAP_LABEL` (`36 CAP`) → `2000 + 36`. The `contracts.csv` year headers must be 5 consecutive years starting at that year. `ROOKIE_RES_LABEL` must be year + 1. All teams must agree, and the year must equal `season` (`league_state.current_season`, or the rolled-over season in season mode). On a mismatch: `"TEAM SALARY shows {year}, league_state is {season}"`.
    2. **Teams:** the teams in `team_cap.csv` equal the teams in `contracts.csv`. If `contracts/targets.txt` exists, they equal that set; otherwise all 32.
    3. **Rows:** per-team row count within the C6 bounds. Every money cell passes `parse_money`. `LEN ≥ 1`, `0 ≤ REM ≤ LEN`. AGE and OVR are integers (`num()`).
    4. **Matching:** `match_contract_rows` against `player_seasons ⨝ players` for `season`. Any problem stops the load, the same way the roster load does. The message lists each row and suggests running the roster scan, or the step-2 transactions scan for a CPU signing.
    5. **Writes:** `supersede_active(season, scanned team ids, matched player ids)`, then `insert_contract(...)` per row and `upsert_team_cap(...)` per team, all with `capture_id`.
    6. **Report:** `contracts`, `superseded`, `teams`, and `cap_warnings`. Those are C10 identity misses and teams whose summed year-0 cap hits differ from SALARIES by more than rows × $0.05M (display rounding). They're reported, not fatal.
  - `load_season()`: call `load_contracts` after `load_roster` when `contracts.csv` exists. `LOADERS["contracts"] = load_contracts`.

**Tests** (offline only):
- `tests/test_capture_matching.py`: a unique match, a position tie-break, an age/OVR tie-break, not on roster, ambiguous, two rows on one player.
- `tests/test_load_capture.py`: `load_contracts` against a fake cursor. It returns the Panthers fixture roster for the `player_seasons` query and records every `execute`. Check:
  - the year check passes on `36 CAP` with season 2036, and stops on 2035 and on a `2037` first column;
  - a partial run (`targets.txt` = PANTHERS) supersedes only Panthers rows and matched ids;
  - an ambiguous row means no INSERT is recorded;
  - `NONE` incentive → NULL;
  - `$0` years are stored as 0;
  - `detect_mode` on temp folders.
- `tests/test_contracts.py`: the SQL and params `supersede_active`/`insert_contract` send, using the fake cursor.

**Exit:** JR OKs a backup, then `python3 -m pipeline.load_capture captures/<run> --dry-run` (checks only, rolled back). Then the real load. Spot-check: `SELECT count(*) FROM player_contracts WHERE status='ACTIVE' AND season=<s>` ≈ the sum of rows, 32 rows in `team_cap_seasons`, and `vw_team_cap_sheet` for CAR matches the screenshots. A second Panthers-only run marks the first Panthers rows `SUPERSEDED` and leaves the other 31 teams untouched.

### Phase 5: Weekly run, Season scan, `#debug`
**Files**
- `pipeline/weekly_capture.py`:
  - `MODES` gets `"contracts"`.
  - `folder_mode()`: a `contracts/` dir without season dirs → `"contracts"`. This check goes after the season check.
  - `capture_done()`: `.capture_done`.
  - `extract_done()`: for `"contracts"`, `contracts.csv` + `team_cap.csv`. For `"season"`, also require them when `contracts/` exists.
  - `step_capture()`: a contracts branch. `--only contracts`, then `--resume` if `contracts/` exists, else `--out`, then `--team-names …` when teams are given. Timeout: `CAPTURE_TIMEOUT`, which is 75 min against ~40 min of work. The season branch adds `contracts` to its resume-dir test, and its timeout goes from `CAPTURE_TIMEOUT * 2` to `* 3`, because the scan adds ~40 min.
  - `run(..., teams=None)`: `teams` is a list of abbreviations, resolved to nicknames with one `SELECT abbreviation, name FROM teams`. An unknown abbreviation → `WeekStopped` before any console press.
  - `what` label `"Contract scan"`.
  - The early-return block (`:514`) handles `"contracts"` with what_done `"Contracts"`. There are no engines or articles.
  - `--teams CAR,NO` CLI flag, valid only with `--mode contracts`.
  - Extract the capture → extract → load sequence for contracts into `contracts_phase(folder, log_path, teams=None)`. `run(mode="contracts")` calls it under the lock, and step 2's weekly phase calls it **without** taking the lock again (see §4).
- Season scan: no new step in `weekly_capture`. The contracts phase rides inside `madden_capture --only season` (Phase 2) and `load_season` (Phase 4), so it runs after rosters in both capture and load.
- `server/debug_api.py`: `JOB_KINDS["contract_scan"] = ("Contract scan (Team Salary, all 32 teams)", ["--mode", "contracts", "--no-articles"])`. A team subset from `#debug` is out of scope: re-scans come from step 2's weekly phase, or by hand with `--teams`. The `season_scan` label adds "contracts".
- `Mu_Network/src/DebugPage.tsx`: a `contract_scan` entry beside `roster_scan` (`:52`). The note says it takes ~40 min, is normally part of the Season scan, and runs with no articles. The `season_scan` note adds contracts after rosters.

**Tests**
- `tests/test_debug_api.py`: `test_contract_scan_job`, in the pattern of `test_roster_scan_job` (`:55`; it runs a stub command, not madden_capture).
- A new `tests/test_weekly_capture_contracts.py` (temp dirs only, `run_cmd` and `league_state` mocked):
  - `folder_mode`/`extract_done`/`capture_done` for contracts and for season-with-contracts;
  - `step_capture` builds `--only contracts --resume <run> --team-names PANTHERS,SAINTS`;
  - an unknown `--teams` abbreviation stops before `run_cmd` is called.

**Exit:** a `#debug` Contract scan job finishes end to end (connect → capture → extract → load) with a phone notice. A by-hand `python3 -m pipeline.weekly_capture --mode contracts --teams CAR` supersedes only Carolina. The next Season scan (when one is due) loads contracts after rosters without intervention.

---

## 3. `PROJECT_SPEC.md` edits (made in the same phase as the change)

| Phase | Section | Edit |
|---|---|---|
| 1 | §1.1 Repository Layout | `pipeline/money.py`; `db/migrations/` list += `2026_12_07_contracts.sql`; `docs/team_management/` folder (this plan) and `docs/TEAM_MANAGEMENT_PLAN.md` under docs |
| 1 | §3 Schema | New §3.3d **Contracts & Cap** (`2026_12_07_contracts.sql`): the three tables, the partial unique index, status/source values, money-as-bigint rule, `vw_team_cap_sheet` columns, SUPERSEDED semantics |
| 2 | §1.1 (`madden_capture.py` and `captures/` lines) | `--only contracts`, `--team-names`, the `contracts/<NN>_<team>/salary[_right]_NNN.png` layout, `done.txt`/`targets.txt`; the Cross guard now includes TEAM SALARY |
| 2 | §1.2 Running the System | Capture-row command: `madden_capture.py --only contracts [--team-names …]` |
| 3 | §1.1 (`extract.py` line), §1.2 | `contracts.csv`, `team_cap.csv` (Gemini, `merge_wide`, cap strip read twice) |
| 4 | §1.1 (`load_capture.py`, `capture_matching.py`, new `pipeline/contracts.py`), §1.2 | The `contracts` mode, year check, stop-on-ambiguity, re-scan semantics; `load_season` order (rosters → contracts) |
| 5 | §1.1 (`weekly_capture.py` line), §1.2, §4.5 (jobs `kind` list += `contract_scan`), §5 (#debug buttons, if listed), §6.0 step 1 (buttons) and step 2 (Season scan phases now include contracts after rosters; `--mode contracts --teams`) | as built |

---

## 4. Interfaces with other steps

**Step 1 provides (step 2 may rely on these):**
- `pipeline.money.parse_money(str) -> int | None` (raises on junk).
- The §2.1 tables plus `pipeline/contracts.py`: `supersede_active()`, `insert_contract(..., source='TRANSACTION')` and `upsert_team_cap()`. Step 2's TRADED → new ACTIVE row can call `insert_contract` with the old row's years.
- `pipeline.weekly_capture.contracts_phase(folder, log_path, teams)`. It captures, extracts and loads a contracts re-scan of the given abbreviations into its own run folder, and doesn't take the run lock. CLI equivalent: `--mode contracts --teams CAR,NO`.

**Step 1 needs from step 2 (the step-2 sub-plan should confirm):**
1. The D5 re-scan list as **team abbreviations**, available after the transactions load commits.
2. The weekly transactions phase calls `contracts_phase` **after** its own load, so CPU signings already have `players`/`player_seasons` rows. Otherwise the contracts load stops with "not on the team's roster", by design.
3. The re-scan uses a **separate run folder** (e.g. `captures/<weekly run>_contracts`), because `detect_mode()`/`folder_mode()` pick one mode per folder and `capture_row()` refuses an already-loaded folder.
4. A transaction that moves a player (TRADED/RELEASED) marks the old row itself. Step 1's load only ever writes `SUPERSEDED` and `ACTIVE`.

---

## 5. Out of scope
- The transactions scan, the `transactions` table, and moving `player_seasons.team_id`: step 2.
- Deciding *which* teams to re-scan each week: step 2. Step 1 only accepts the list.
- Cap reasoning, trade legality, GM use of the view: step 3. Newsroom cap tools: step 5.
- Reading the player card on TEAM SALARY (it repeats the row, master §3.1).
- A `#debug` team picker for partial scans.
- Back-filling contracts for past seasons.
- Fixing `load_team_overalls`' double `fetchall` (noted under the Survey table). That's a separate fix.

## 6. Open questions for JR
- **Q1 (C8), answered:** the two BONUS columns are the same figure. `merge_wide` treats them as one column and uses them as the left/right row-alignment check. No `BONUS_R` rename is needed.
- **Q2 (C11/S16), answered (JR, 2026-10-19):** accepted. Contracts load only in-season and in the Season scan. A by-hand scan in the offseason window stops on the year check by design.
