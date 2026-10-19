# Step 2: League Transactions weekly scan (sub-plan)

Written 2026-10-19 for [`TEAM_MANAGEMENT_PLAN.md`](../TEAM_MANAGEMENT_PLAN.md) step 2. This plan owns master §2.2 (the `transactions` schema). It uses step 1's §2.1 contract tables, `pipeline/money.py` and `--mode contracts --teams` only through the interfaces the master plan fixes, and it never designs them.

**Delivers:** `madden_capture.py --only transactions`, `transactions.csv`, migration `2026_12_08_transactions.sql`, a loader that moves players between teams and keeps their contracts and the team cap rows current from the transactions (no automatic Team Salary re-scans, §6 answer 1), a transactions phase in the weekly run, and the changes the engines and Napper need once a player's team can change mid-season.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence for step 2 |
|---|---|---|
| S1 | `LEAGUE_MENU` already lists `"LEAGUE TRANSACTIONS"` (`madden_capture.py:466`). `to_league_tab()` (`:756`) and `move_to()` (`:743`) are how `open_player_stats` (`:765`) and `open_league_schedule` (`:812`) open League items. | `open_transactions()` follows the same pattern. No new navigation code. |
| S2 | `cross()` (`:641`) lets Cross through on `HUB_LEAGUE` **for any highlighted item** (`:680`, `elif screen not in ("HUB_LEAGUE", "SCHED_TILES", "STATS_TILES")`). Today, RELOCATE OR REBUILD isn't guarded. | Add a `HUB_LEAGUE` branch that allows Cross only on `STATS & AWARDS`, `SCHEDULE` and `LEAGUE TRANSACTIONS`, by name (D7). The new `LEAGUE_TRANSACTIONS` screen stays off the allowed list, so Cross on it is refused by the final `else`. |
| S3 | The Edit Draft Class prompt "posts a notice to the league's transaction log" (`:657`). | The list holds rows that aren't roster moves. The parser has to turn them into `OTHER` rather than fail on them. |
| S4 | `classify()` (`:503`) is a text-rule chain, and the hub check (`GAMEDAY` + `COACH CENTRAL`…, `:571`) comes near the end. Classifier tests build a fake `_Frame` from labels (`tests/test_madden_capture.py:95`). | Add `LEAGUE_TRANSACTIONS` before the hub rule, and test it the same way. |
| S5 | `capture_list()` (`:1111`) pages with `press(direction, times=rows_per_page)`. It stops on a repeated page (`same_table`/`text_end`, `confirm_end`) or on all-zero rows. It has no "stop when old enough" hook. | Add an optional `stop(path) -> bool` callback (default `None`, so behaviour is unchanged) that the transactions run uses for its week cutoff. |
| S6 | `clean_week()` (`:878`) fixes OCR digits (`T/I/L→1`, `O→0`), and `week_index()` (`:889`) orders preseason < season < postseason. Both live in `madden_capture.py`, which imports Quartz and Vision (its tests skip off macOS, `tests/test_madden_capture.py:9`). | The heading parser goes into a **pure** module (`pipeline/transactions_parse.py`) so the loader and tests don't need Vision. It reuses the same digit rules. |
| S7 | Apple Vision "drops or misreads the digit 1 in this font" (`:2832`). Roster cards with suspect values go to Gemini (`extract.py:440`, `read_card_values`). | `1 YEAR`, `WEEK 1`/`WEEK 11` and `$1.xxM` are exactly the risky digits. Use Vision first and send flagged rows to Gemini, like the roster and prospects modes do (`extract.py:587`, `extract_prospects`). |
| S8 | Resume convention: `<mode>/done.txt` (`run_injuries`, `:2718–2829`). Main dispatch: `if a.only in (None, "injuries")` (`:3625`). `--only` choices are at `:3539`, and `--teams` is already an **int** for roster (`:3542`). | Add `transactions` to the `--only` choices and to the default (`None`) and `playoff` runs, and add a new `--since` flag. Don't reuse `--teams`. |
| S9 | `detect_mode()` (`pipeline/load_capture.py:46`) picks the mode from CSVs: weekly (`stats_passing.csv`) wins before `injuries`. `LOADERS` is at `:1333`. `load()` (`:1338`) runs one DB transaction and records `captures`. | A weekly folder that also has `transactions.csv` stays `weekly`. A folder with only `transactions.csv` becomes the new `transactions` mode. |
| S10 | `load_weekly()` (`:587`) matches Season Stats rows through `roster()` (`:366`), which reads `player_seasons.team_id`. Its upsert into `season_stats` sets `team_id = EXCLUDED.team_id` (`:744–749`). | **Transactions have to load before Season Stats in the same run**, or a traded or signed player's week is put on his old team or doesn't match. |
| S11 | `player_seasons` is `UNIQUE (player_id, season)` with one `team_id` per season (`db/init_db.sql:90–112`). `load_roster` upserts it (`load_capture.py:182–192`). | A move rewrites the season's team, and there is no per-week team history. The `transactions` table becomes that history. |
| S12 | `load_roster` deletes players that aren't on any roster and have no stats, awards, tags or memories (`:207–226`). Its comment says "clearing the team broke the engines, which assume every player_seasons row has one" (JR, Oct 14). | (a) A **release must not set `team_id` NULL**. Add `player_seasons.released_week` instead. (b) `transactions.player_id` must be `ON DELETE SET NULL`, because a signed-then-unseen depth player can be deleted by the next roster scan. |
| S13 | `capture_matching`: `name_key` (`:65`), `full_key` (`:79`), `name_case` (`:91`), `db_player_name` (`:108`), `match_roster` (`:153`). A full-name test counts only where `player_name` and `full_name` agree (`_consistent`). | Players created from a signing get `player_name = db_player_name(first, last)` and `full_name = "First Last"`, so they agree and the next roster scan matches them (master §4 risk). |
| S14 | `game_stat_generator.generate_team_and_player_stats` subtracts the player's stored lines **from any team** (`engines/game_stat_generator.py:541–553`), and the K/DEF/ST backfill does the same by `player_id` (`:91–96`). It loops over `season_stats.team_id` groups (`:503–516`). | Trades already work if `season_stats.team_id` is moved (S10). Gap: a released player's season row stays under his old team, so he gets zero lines in that team's later games (`:586–597` append a row for every team player). |
| S15 | `fantasy_projection_engine.load_season` counts a player's points only `if roster[pid]["team_id"] == team_id` (`engines/fantasy_projection_engine.py:188`). Baselines use the **current** team's completed weeks (`:403–405`). | After a trade, his old-team points disappear from his history and baseline. This needs a fix. |
| S16 | Napper decides who has a game this week from `ps.team_id` against `games` (`engines/napper_waiver_engine.py:261`, `engines/napper_lineup_engine.py:139–148`). Many API queries filter `ps.team_id IS NOT NULL` (`server/napper_api.py:1044`, `:1112`, `:1373`, `:1562`, `:1690`). | Trades work as they are (the new team's schedule). Released players would still show "has game", so add `released_week IS NULL` there. A NULL `team_id` would drop rostered players from Napper views, which is another reason for S12(a). |
| S17 | `weekly_capture.step_capture` (`pipeline/weekly_capture.py:206`) runs one `madden_capture.py --out/--resume` for the weekly run, then `run()` sends "Turn your controller back on" (`:479`) **before** extract and load. `extract_done` (`:124`) gates on mode CSVs. | The D5 re-scan has to run while Remote Play still has the console, before that notice. So flagged teams are worked out from the extracted CSV, not from a finished load (Phase 6). |
| S18 | `server/debug_api.py:34` `JOB_KINDS` maps buttons to `weekly_capture` args. The page is `Mu_Network/src/DebugPage.tsx`. | Add a `transactions_scan` kind and button, plus a Transactions panel (unparsed rows, pending re-scans). |
| S19 | `tests/test_napper_api.py` / `test_napper_engines` write to the live `madden` DB (memory). `tests/test_load_capture.py` holds DB-free helper tests. | Every new test is pure: parser, merge, effect planning on in-memory state, and classifier fakes. DB writes are tried only with `--dry-run` against a restored copy (`DATABASE_URL`). |

---

## 1. Console facts to check (JR at the console; don't build on them unverified)

Confirmed (master §3.3): the screen is under League > LEAGUE TRANSACTIONS. It's a newest-first list, with the L2 chip on NFL and the R2 type filter on ALL. There is no week filter. Each row has a heading `2036 SEASON - WEEK 4`, and about 4 rows show at a time. Example lines are `… SIGNED FREE AGENT (WR) … TO 1 YEAR, $3.93M CONTRACT` and `… RELEASE (WR) …`. CPU free-agent deals are 1-year only, and re-signings appear in the list.

Still to check (Phase 2 begins with a `--test` / `--step` session):

1. **Screen anchors for the classifier:** the exact title, the chip texts (`NFL`, `ALL`) and the button-bar labels. Is there a Cross action on a row (a player card)? Whatever it is, the capture never presses it.
2. **Opening state:** does the screen open on the NFL chip and the ALL filter, or does it remember the user team or the last filter? If not NFL/ALL, find the L2/R2 press counts to get back there and how the chip reads.
3. **Scrolling:** does Down move a highlight one row (like the Roster list) or scroll the list? Where does the highlight stick before the list scrolls? Does R1/L1 or a held Down page faster (this matters for the first-run backfill and for resume)? How long does a page take to settle after a burst?
4. **Row layout:** is the body line ever wrapped onto two lines (long names, trades)? Where are the heading and body relative to the logo and headshot (needed for crop boxes)?
5. **Headings outside the regular season:** preseason, postseason, offseason stages (re-sign window, free agency, draft) and the year shown in each. For example, is an offseason row headed `2036 …` or `2037 …`? This sets the ordinal and the `season` that offseason rows get.
6. **Week semantics:** does a `WEEK N` heading mean the move happened while Madden's current week was N (that is, before week N's game)? And are JR's own trades, made at current week N, headed `WEEK N`? Phase 4 assumes yes: the player plays week N for his new team.
7. **Trade wording (unverified):** is it one row per player or one row per trade? Which team is named first? How are picks written ("2037 1ST ROUND PICK")? Is there a "FOR …" clause? Make one test trade between two CPU teams through User Teams (D3) and read the rows.
8. **Re-sign / extension wording:** for example `RE-SIGNED (QB) … TO 4 YEARS, $120M CONTRACT`. Is "EXTENSION" a separate verb? Does the amount show for re-signings?
9. **Other row kinds:** release to IR or practice squad, retirement, the draft-class notice (S3), waiver claims, and rookie signings after the draft (possibly 224 rows a season).
10. **How deep the list goes:** does it keep the whole league history, or only this season (or a cap)? This sets how long the first-run backfill is and whether last season's rows exist.
11. **Amount format:** `$3.93M`, `$850K`, `$1.2M`. Is the comma after YEAR always there?

---

## 2. Design

### 2.1 Natural key and idempotency
The list gives no date finer than the heading, and identical rows are legitimate. A team can release the same depth player twice in a week. So the key is:

`UNIQUE (season, txn_date, row_key, seq)`

- `txn_date`: the heading after OCR clean-up (`2036 SEASON - WEEK 4`), as master §2.2's "as Madden shows it".
- `row_key`: a normalized key. For a parsed row it is built **from the parsed fields** (`type|team|to_team|pos|full_key(name)|len|amount`), so two OCR readings of the same row that differ by a stray character still collide. An unparsed row uses `raw:` + the text upper-cased, with whitespace collapsed and homoglyphs folded.
- `seq`: the 1-based count of rows with the same `(txn_date, row_key)`, **counted from the oldest row in that heading group upward**. New rows appear at the top (newest first), so older rows keep their `seq` when the list grows. The scan always covers whole heading groups (§2.2), so `seq` is stable across re-scans.

The loader inserts with `ON CONFLICT DO NOTHING RETURNING id`. **Effects (team moves, contracts, cap deltas) run only for rows inserted now**, so loading the same week again changes nothing.

### 2.2 Scan cutoff
`--since "<heading>"` names the newest heading already loaded. The capture stops after the first page whose **every** visible heading orders strictly before `since`. Rows from the `since` week are therefore read again in full, which is what makes `seq` stable, and rows that are already loaded fall to the unique key. With no `--since`, the scan runs to the end of the list (first-run backfill). Headings are ordered by `heading_ordinal()` = `(year, phase_rank, week_or_stage)`. The ranks for preseason, regular season, postseason and offseason come from console fact 5.

### 2.3 Effects (per inserted row, oldest first, inside the load transaction)
Matching uses `name_case` → `full_key`/`name_key` (S13). The "this season" roster comes from `player_seasons` for `season`.

| Type | Player resolution | `player_seasons` | Contracts (only if `to_regclass('player_contracts')` exists; step 1 may land later) | `team_cap_seasons` (this season; skipped when the team has no row) |
|---|---|---|---|---|
| `SIGNED` | 1) `full_key` among this season's rows that have `released_week` set, 2) `full_key` + position anywhere this season, 3) `full_key` + position in `players` with no row this season. Several hits are **ambiguous**: store `player_id` NULL, report it, and apply no move. No hit: **create** `players(player_name=db_player_name, full_name)` and a `player_seasons` row (`team_id = to_team`, `position`, the other card fields NULL), and report it under `created_players`. | Upsert `team_id = to_team`, `released_week = NULL`. | Any `ACTIVE` row for `(player, season)` → `SUPERSEDED`. Insert `ACTIVE`, `source = 'TRANSACTION'`, `length = years_remaining = N`, `total_value = parse_money(amount)`, bonus/savings/penalty NULL, plus `player_contract_years` for `season … season+4` (`cap_hit` = `total_value / N` for the years in the contract, 0 after). | `to_team`: `salaries` + this season's hit. |
| `RELEASED` | `full_key` / `name_key` + position among `from_team`'s rows this season. If it isn't found there, look league-wide. If there's still no hit, create the player with `team_id = from_team`. | `released_week = week` (**`team_id` kept**, S12). | The `ACTIVE` row → `RELEASED`. | `from_team`: `salaries` − his hit, `cap_penalty` + the stored `cut_penalty` (dead money; NULL counts as 0). |
| `TRADED` (one leg per player) | Same as RELEASED, on `from_team`. | `team_id = to_team`, `released_week = NULL`. | `ACTIVE` → `TRADED`, then insert a copy on `to_team` (`ACTIVE`, `source = 'TRANSACTION'`, same `length`/`years_remaining`/money and copied `player_contract_years`, `projected` flags kept), per master §3.5. | `from_team` − hit, `to_team` + hit. |
| `RE_SIGNED` / `EXTENDED` | Same as RELEASED, on `team` (no player is created). | None. | The `ACTIVE` row keeps this season; it gets `extension_years`, `extension_value`, `extended_transaction_id`, and **projected** `player_contract_years` rows (`projected = true`) for the new deal's years, starting the season after the current deal ends (`season + years_remaining`). Hits = amount × `year_shares(N)` (below). | None. |
| `OTHER` | None. | None. | None. | None. |

Every cap delta changes `salaries` and/or `cap_penalty`, moves `total_spend` by their sum and `cap_space` by minus it, and stamps `derived_at` and `derived_transaction_id`. `derived_at` later than `scanned_at` means the row is derived, not read off the screen. The next Season scan overwrites all of it.

**Re-signing shares** (`average_shares` / `year_shares` / `projected_hits`, pure). From the latest Season scan, take the `ACTIVE`, `SCAN` contracts whose every year is on screen (`years_remaining = length ≤ 5`). For each length, average each year's share of the total. A length backed by fewer than 10 contracts, or no scan at all, splits the amount evenly. The rounding remainder goes to the last year, so the hits sum to the amount.

Pick legs and unparsed trade fragments are stored as `TRADED` with `player_id` NULL and the asset in `raw_text`. They have no effect. `trade_group` is set to `"{season}:{txn_date}:{teamA}-{teamB}:{n}"` (teams sorted) for consecutive TRADED legs between the same pair of teams in one heading group.

**Effects cutoff (first run):** a backfill would otherwise replay preseason and offseason moves onto rosters that the Season scan already captured. So rows get `applied = false` and no effects when they order before `apply_from`. On the first load of a season, `apply_from` defaults to `"<season> SEASON - WEEK 1"`, and `--apply-from <heading>` overrides it. After that, every new row is applied. The roster scan stays authoritative and reconciles anything missed (master §4).

**Optional step-3 hook:** after the TRADED legs of a `trade_group` are applied, if `to_regclass('gm_trade_proposals')` is set and `pipeline.gm_proposals.match_executed_trade` can be imported, call `match_executed_trade(cur, legs) -> proposal_id | None` and write `gm_proposal_id` on the legs. Step 3 owns the matching rules, the `EXECUTED` transition, and the "APPROVED but mismatched" flag (master §4). Step 2 owns only the call site and the column.

### 2.4 Extraction choice
**Apple Vision first, Gemini only for flagged rows.** The rows are short, upper-case text with no table. Vision is free, runs on the device, and the injuries and prospects modes already rely on it (`extract.py:957`, `:594`). A first-run backfill can be hundreds of pages, which would cost real Gemini tokens. Vision's known weakness is the digit 1 (S7). A row goes to Gemini (one cropped row per request, the `extract.py` client, metered by `METER`) when any of these is true:
- it fails the grammar;
- its heading breaks the newest-first order against its neighbours;
- its length or amount group is missing;
- its team isn't one of the 32 nicknames.

The Gemini reading is parsed again by the same grammar. A row that still fails is written with `PARSE_OK=0` and loaded as `OTHER`. **No row is dropped.**

---

## 3. Phases

### Phase 1: Schema and pure parser (no console, no DB writes)
**Files**
- `db/migrations/2026_12_08_transactions.sql`, folded into `db/init_db.sql` (stage only this hunk; the checkout is shared):
  - `transactions`: `id SERIAL PK`, `season INT NOT NULL`, `week INT NULL` (games.week; NULL for offseason stages), `txn_date TEXT NOT NULL`, `type VARCHAR(12) NOT NULL CHECK (type IN ('SIGNED','RELEASED','TRADED','RE_SIGNED','EXTENDED','OTHER'))`, `player_id INT REFERENCES players(id) ON DELETE SET NULL`, `player_name TEXT`, `position VARCHAR(6)`, `from_team_id`/`to_team_id INT REFERENCES teams(id)`, `contract_years SMALLINT`, `contract_value BIGINT`, `raw_text TEXT NOT NULL`, `row_key TEXT NOT NULL`, `seq SMALLINT NOT NULL DEFAULT 1`, `parse_ok BOOLEAN NOT NULL`, `flags TEXT`, `applied BOOLEAN NOT NULL DEFAULT TRUE`, `trade_group TEXT`, `gm_proposal_id INT NULL` (no FK; step 3 adds it), `capture_id INT REFERENCES captures(id) ON DELETE SET NULL`, `created_at TIMESTAMPTZ DEFAULT now()`, `UNIQUE (season, txn_date, row_key, seq)`. Indexes: `(season, week)`, `(player_id)`, `(trade_group)`.
  - On step 1's tables (revised 2026-10-19, after JR dropped the re-scan queue): `player_contracts.extension_years SMALLINT`, `extension_value BIGINT`, `extended_transaction_id INT REFERENCES transactions(id) ON DELETE SET NULL`; `player_contract_years.projected BOOLEAN NOT NULL DEFAULT false`; `team_cap_seasons.derived_at TIMESTAMPTZ`, `derived_transaction_id INT REFERENCES transactions(id) ON DELETE SET NULL`.
  - `ALTER TABLE player_seasons ADD COLUMN IF NOT EXISTS released_week INT` (NULL = on the team's roster; set = free agent since that week).
- `pipeline/transactions_parse.py` (pure, no imports from `madden_capture`):
  - `clean_heading(s)`: the `clean_week` digit fixes plus `SEASON - WEEK` spacing.
  - `heading_ordinal(h)`.
  - `heading_week(h) -> int | None`: reuses `capture_matching.db_week` for postseason names.
  - `parse_row(text, teams) -> dict`: grammar below; `type`, `team`, `to_team`, `pos`, `name`, `years`, `amount_text`, `assets`, `ok`, `flags`.
  - `row_key(parsed, text)`.
  - `assign_seq(rows)`: oldest-first within each heading.
  - `fix_heading_order(rows)`: flags non-monotone headings.
  - Grammar, upper-cased, tolerant of a missing comma and `YEAR/YEARS`:
    - `^(TEAM) SIGN(?:ED|S)? (?:FREE AGENT )?\((POS)\) (NAME) TO (\d+) YEARS?,? \$([\d.]+[MK]) CONTRACT$`
    - `^(TEAM) RELEASE[DS]? \((POS)\) (NAME)$`
    - `^(TEAM) RE-?SIGN(?:ED|S)? \((POS)\) (NAME)…`
    - `…EXTEN(?:D|DED|SION)…`
    - TRADE: any row with `\bTRADE` → team = first nickname, every `\((POS)\) NAME` group a player leg, `TO (TEAM)` the receiving side, and `\d{4} \w+ ROUND PICK` an asset leg. Anything left over goes to `flags`.
    - Other rows → `OTHER`.
  - `TEAM` is one of the 32 nicknames (`load_capture.team_index` names). `POS` is `[A-Z]{1,5}`.

**Tests:** `tests/test_transactions_parse.py`:
- both confirmed example rows;
- missing comma or `YEARS`;
- OCR `WEEKT` / `WEEK 1T` headings;
- ordinal order across preseason, regular season and postseason;
- `seq` stays stable when newer rows are prepended;
- `row_key` is the same for two OCR variants of one parsed row;
- the draft-class notice → `OTHER`;
- three made-up trade shapes (single leg, two legs with "FOR", with a pick), all parse or flag without raising.

**Exit check:** the tests pass. `python3 db/run_sql.py db/migrations/2026_12_08_transactions.sql` applies to a restored test copy; it is applied to live `madden` only with JR's OK.

### Phase 2: Capture (`--only transactions`)
**Files:** `madden_capture.py`
- `classify()`: `LEAGUE_TRANSACTIONS` when the title text (console fact 1) and a `\d{4} (PRE|POST )?SEASON ?- ?` heading are present. Place it before the hub rule (S4).
- `cross()`: the new `HUB_LEAGUE` branch: `cur = frame.highlighted(LEAGUE_MENU)`; allowed are `("STATS & AWARDS", "SCHEDULE", "LEAGUE TRANSACTIONS")`, and anything else raises `Abort`. `LEAGUE_TRANSACTIONS` isn't added anywhere, so Cross on it is refused (D7).
- `go_hub()`: on `LEAGUE_TRANSACTIONS`, press Back.
- `open_transactions()`: `to_league_tab` → `move_to(LEAGUE_MENU, "LEAGUE TRANSACTIONS")` → `cross_into("HUB_LEAGUE", ["LEAGUE_TRANSACTIONS"])`. Then make sure the chips read NFL / ALL; if they don't, press L2/R2 per console fact 2 and re-read.
- `transaction_headings(f)`: the headings OCR'd on the frame, via `transactions_parse.clean_heading`.
- `capture_list(..., stop=None)`: a new optional callback, checked after each saved page (S5).
- `run_transactions(out, since=None)`:
  - Writes `transactions/page_NNN.png` with `rows_per_page = 3` (one row of overlap with ~4 visible; adjust per fact 3), `text_end=True`, `confirm_end=True`, `stop_at_zeros=False`. `stop` is "every visible heading < since".
  - Writes `transactions/meta.json` (`since`, the first and last heading seen, pages, `reached_end`) and `done.txt`.
  - **Resume:** if `done.txt` exists, skip. If only pages exist and `since` is set, delete them and start again (a weekly scan is short). For a backfill without `since`, keep the pages, scroll in bursts without saving until the visible headings reach the last saved page's oldest heading, line up on that page's text, then carry on numbering.
- `main()`: `--only transactions` and `--since "<heading>"`. Add `transactions` to the default `None` run (after injuries) and to `--only playoff`, both passing `--since`.

**Tests:** `tests/test_madden_capture.py` (macOS-only like the file):
- `_Frame` classify cases for the transactions screen versus `HUB_LEAGUE`;
- `cross("HUB_LEAGUE")` refuses `RELOCATE OR REBUILD` and `LEAGUE HISTORY` and allows the three items (`press` mocked);
- `cross("LEAGUE_TRANSACTIONS")` raises;
- the `stop` predicate stops on a page whose headings are older than `since` but not on a mixed page.

**Exit check (JR supervising):** `--test` on the screen prints `LEAGUE_TRANSACTIONS`. Then `--only transactions --out tx_probe` (full backfill) reaches the end of the list. A rerun with `--since` set to a mid-season heading stops within one page past that week. Answers to console facts 1–5 and 10 get written into §1 of this plan.

### Phase 3: Extraction (`transactions.csv`)
**Files:**
- `pipeline/extract_transactions_ocr.py`:
  - Vision items per page → rows. Group items by y-band, with the heading line above the body line and continuation lines joined (fact 4).
  - `merge_pages(pages)`: line up each page on the longest suffix/prefix match of `(heading, text)` sequences with the page before. This is not de-duplication: identical rows survive.
  - Then `fix_heading_order` and the Gemini re-read of flagged rows (§2.4).
- `extract.py` `main()`: if `transactions/` exists, call `extract_transactions_ocr(root)` (the same try/except as injuries at `:957`).

CSV columns: `ORDER` (0 = newest), `PAGE`, `HEADING`, `SEASON`, `WEEK`, `TEXT`, `TYPE`, `TEAM`, `TO_TEAM`, `POS`, `NAME`, `YEARS`, `AMOUNT`, `PARSE_OK`, `FLAGS` (`from_gemini`, `heading_fixed`, `unparsed`, …). The loader **re-parses from `HEADING`/`TEXT`**. The parsed columns are there for review, so a parser fix needs no re-extract.

**Tests:** `tests/test_extract_transactions.py`:
- row assembly from synthetic `(text, bbox)` items, including a wrapped body;
- `merge_pages` with 1-row overlap, 0-row overlap (dropped press → flag `gap?`), and a page made of two identical rows;
- the Gemini path stubbed by monkeypatching the client (no network).

**Exit check:** extract the Phase 2 `tx_probe` folder. JR spot-checks `transactions.csv` against the screen. The share of unparsed rows is reported, and each unparsed row's text goes into the plan's console facts (7–9) and grammar.

### Phase 4: Loader
**Files:**
- `pipeline/load_transactions.py`:
  - `plan_effects(rows, state) -> [op]`: pure. `state` is the in-memory roster, contracts and existing keys; `op` is `move`, `release`, `create_player`, `contract_status`, `contract_insert`, `contract_extend` or `cap_delta`, grouped under the transactions row it belongs to.
  - `load_transactions(cur, folder, season, report, apply_from=None)`: reads the DB state, runs `plan_effects`, executes the ops, then calls the optional step-3 hook.
  - Report keys: `transactions_new`, `transactions_seen`, `unparsed` (list of text), `ambiguous`, `created_players`, `not_applied`, `contract_ops`, `cap_deltas`, `cap_rows_missing`.
  - Checks that raise `LoadError` (nothing written):
    - a heading's year isn't the current season (or the previous one, for offseason stages per fact 5);
    - an unknown team nickname on a *parsed* row;
    - a heading group that is in the DB but has **fewer** rows in this scan than already stored (the scan missed rows: re-capture).
- `pipeline/load_capture.py`:
  - `detect_mode` returns `transactions` for a folder with only `transactions.csv`, and `LOADERS["transactions"]` is `load_transactions_standalone` (week from `league_state`, like `load_injuries_standalone` at `:882`).
  - `load_weekly` calls `load_transactions` **before** step 3, the Season Stats part (S10). `load_playoff` gets it through `load_weekly`.
  - `load_roster`'s upsert sets `released_week = NULL` for every player on a roster.
- `pipeline/load_capture.py main` / `load()`: pass through `--apply-from`.

**Tests:** `tests/test_load_transactions.py` (pure, on `plan_effects`):
- sign known FA (released elsewhere), sign unknown → create, sign ambiguous → no move;
- release keeps `team_id` and sets `released_week`;
- trade moves the contract copy with the same years, and both teams' cap rows;
- release charges the cut penalty as dead money;
- re-sign projects the new deal's years after the current one (even split, and scanned shares);
- sign then release the same week (applied oldest first);
- the same rows loaded twice produce no ops;
- rows before `apply_from` → `applied=false` with no ops;
- contract ops are left out when the state has no `player_contracts`;
- the step-3 hook is called only when it exists (both stubbed).

**Exit check:** `python3 -m pipeline.load_capture captures/tx_probe --dry-run` against a **restored test copy** (`DATABASE_URL`) prints a sensible report. JR approves before the first live load.

### Phase 5: Engines and Napper react to team changes
**Files:**
- `engines/fantasy_projection_engine.py`:
  - `load_season` adds `ps.released_week` to the roster query (`:156`).
  - It records `data.points[pid][week]` **regardless of team** (`:188`) and keeps `allowed` per the line's team.
  - Per-player `played` = the current team's completed weeks ∪ the weeks the player has a line (`:403`).
  - A player with `released_week` set projects 0, with `opp` treated as None.
- `engines/napper_waiver_engine.py:261`, `engines/napper_lineup_engine.py:139–148`, and the `has_game` expressions in `server/napper_api.py`: add `AND ps.released_week IS NULL` to *has game*. The `team_id IS NOT NULL` filters stay, so rostered free agents remain visible.
- `engines/game_stat_generator.py`: skip a `season_p_df` (and K/DEF/ST) player when `released_week` is set and ≤ the game's week, so no zero lines appear after a release (S14). Trades need no change, because the per-player deduction already spans teams (`:541`).
- Newsroom tools that show a player's team read `player_seasons.team_id` and are correct after a move. Showing transactions to writers is step 5.

**Tests:** extend the pure parts:
- `tests/test_fantasy_projections.py`: a traded player keeps his old-team points in the baseline, and a released player projects 0;
- `tests/test_game_stat_generator.py`: if it can run on DataFrames without the DB; otherwise add a DB-free helper for the "skip released" rule and test that;
- Napper SQL changes get **no** live-DB test (S19). They are checked by `--dry-run` reading of the SQL in a restored copy.

**Exit check:** on the restored copy with Phase 4 loaded, `python3 -m engines.game_stat_generator --season <s>` runs clean. A traded player's projection row is on his new team with his history intact.

### Phase 6: Weekly integration, `#debug`
**Files:**
- `pipeline/weekly_capture.py`:
  - `since_heading()` reads `max(txn_date by ordinal)` from `transactions` for the season and passes `--since` to the weekly/playoff `madden_capture` command (S17). With nothing loaded yet, the weekly run uses `<season> SEASON - WEEK 1`, and `--mode transactions` reads the whole list.
  - `extract_done` for weekly/playoff also requires `transactions.csv` when transactions pages exist.
  - The single load (one DB transaction) runs `load_transactions` → Season Stats → …
  - Add `--mode transactions` (connect → `--only transactions` → extract → load → shrink, no engines or articles) to `MODES`.
  - No contracts re-scan (§6 answer 1). Step 1's `--mode contracts --teams` stays a by-hand tool.
- `server/debug_api.py`:
  - `JOB_KINDS["transactions_scan"] = ("League transactions scan", ["--mode", "transactions", "--no-articles"])`;
  - `GET /api/debug/transactions?season&week` returns the rows, unparsed rows and ambiguous rows.
- `Mu_Network/src/DebugPage.tsx`: a button for the new job and a Transactions card that lists unparsed and ambiguous rows and the latest rows.

**Tests:**
- `tests/test_debug_api.py`: the new job kind starts with the right args (the existing job tests pattern; check it doesn't need the live DB, or else mock the connection);
- `tests/test_weekly_capture*`: command building with `--since` and `run_cmd` mocked, folder states, and the transactions run's step order.

**Exit check:** one supervised Scan week. The log shows the transactions phase stopping at the cutoff and one notice. The load's report includes `transactions_new`, `contract_ops` and `cap_deltas`, and the moved teams' `team_cap_seasons` rows carry `derived_at`.

---

## 4. `PROJECT_SPEC.md` edits (CLAUDE.md rule, made in the same phase)

| Phase | Sections |
|---|---|
| 1 | §1.1 layout (migration, `pipeline/transactions_parse.py`); §3: a new **§3.3d Transactions** (`transactions`, the derived/projected columns on step 1's tables, `player_seasons.released_week` in §3.1's `player_seasons` entry) |
| 2 | §1.1 the `madden_capture.py` entry (`--only transactions`, `--since`, `LEAGUE_TRANSACTIONS` screen, the Cross guard now per League item); §1.2 commands |
| 3 | §1.1 (`pipeline/extract_transactions_ocr.py`, `extract.py`'s `transactions.csv`); §6.0 extract step |
| 4 | §1.1 (`pipeline/load_transactions.py`); §1.2 (`load_capture --apply-from`); §3.3d effects and key; §6.0 load order (transactions before Season Stats) |
| 5 | §3.4 fantasy projections (traded-player history, released = 0); §3.4b / §4.4b Napper (has-game rule); §6.0 backfill note |
| 6 | §1.2 (`--mode transactions`); §4.5 (job kind, `/api/debug/transactions`); §5 Debug page (and `Mu_Network/PROJECT_SPEC.md` if it describes the page); §6.0 weekly run (transactions phase) |

---

## 5. Out of scope
- Contract tables, `parse_money`, the Team Salary capture and `--mode contracts` (step 1). Step 2 calls them as they are.
- `gm_trade_proposals`, the proposal matching rules and the `EXECUTED` state (step 3). Step 2 only calls the optional hook and stores `gm_proposal_id`.
- Turning transactions into articles, Agent Wire posts or writer tools (step 5).
- Draft-pick ownership after trades (step 3/4). Pick legs are stored only as raw `TRADED` rows.
- Per-week team history for stats splits. `transactions` holds it, but splits by team-at-the-time aren't built here.
- Re-checking teams with no transactions (master §4: acceptable drift until the next Season scan).

## 6. Answered questions (JR, 2026-10-19)
1. **No automatic contracts re-scans** (revised 2026-10-19; this replaces the earlier answer of re-scans capped at 8 teams a week). Re-signings don't change this season's cap, and every move's effect on the books can be worked out from the row and the stored contract. So contracts and `team_cap_seasons` are kept current from the transactions (§2.3), and a re-signing's future years are projected from the scanned per-length shares. The next Season scan corrects any drift. `--mode contracts --teams` stays a by-hand tool, e.g. before a trade that only just fits under the cap. The `contract_rescan_requests` table and the weekly re-scan step were removed before the migration was ever applied.
2. **First-run backfill cutoff: yes.** Effects apply only from `SEASON - WEEK 1` of the current season. Older rows are stored with `applied = false`, as history only.
