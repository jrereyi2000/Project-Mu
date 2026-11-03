# Project Mu: Salary Cap Growth Plan

**Status:** approved by JR (2026-11-02); building, wave 1 of 2.
**Builds on:** PROJECT_SPEC §2.17 (trade value, cap history), §2.17a (contracts engine), §2.17d (transactions engine), §2.21a (league office), §3.3d (`team_cap_seasons`), §4.6b (`/api/franchise`), §6.0b (PC save loader); [`V2_CORE_BUILD_PLAN.md`](../done/V2_CORE_BUILD_PLAN.md) (U3, U11, U13); [`CAP_RELATIVE_CONTRACTS_PLAN.md`](../done/CAP_RELATIVE_CONTRACTS_PLAN.md); [`LEAGUE_OFFICE_PLAN.md`](../done/LEAGUE_OFFICE_PLAN.md) §4.11.

**Goal:** Project Mu sets the league salary cap each offseason and keeps the PC save and PostgreSQL in sync. Today Madden sets `SalaryInfo.TeamSalaryCap` with a hardcoded 4-year schedule that stops growing from franchise year 5 onward, `pc_save_reader.mjs` and `load_save.py` never load `team_cap_seasons` (leaving database-backed engines on the 2024 `$255.4M` fallback), and `league_office.py` reads `SalaryInfo.TeamSalaryCap` without its `$120.0M` binary offset (`$181.2M` instead of `$301.2M`). This plan decodes Madden 27's cap fields, loads `team_cap_seasons` from every PC save, zeroes Madden's built-in cap schedule during league setup, and advances `SalaryInfo.TeamSalaryCap` and all 32 teams' cap rooms at `OFFSEASON_START` with `contracts_engine.next_cap`.

**Delivers:**
- `pipeline/save_reader/lib.mjs`: `SALARY_CAP_OFFSET`, `SALARY_CAP_RAW_RANGE`, `SAL_CAP_ROOM_BIAS`, `salaryCapUnits`, `rawSalaryCap`, `capRoomUnits`, `rawCapRoom`, and widened 19-bit range checks in `fieldValueError`.
- `pipeline/save_reader/pc_save_reader.mjs` (`READER_VERSION = 6`): top-level `salary_cap` block and per-team `teams[].cap` block in true `$10,000` units.
- `pipeline/save_reader/league_setup.mjs`: decoded `salary_cap` plus `salary_info_table_id` and `raw_salary_cap` in `leagueState()`; `setupLeague()` zeroes `SalaryInfo.SalaryCapIncreasePerYear` (`int0..int29 = 0`).
- `pipeline/save_reader/record_edits.mjs`: shared `fieldMeta` from `lib.mjs` so 19-bit `Team` cap-room edits above `200,000` (`+$400.0M` actual room) pass validation.
- `engines/contracts_engine.py`: `SALARY_CAP_OFFSET_UNITS`, `SALARY_CAP_RAW_RANGE`, `SAL_CAP_ROOM_BIAS_UNITS`, `CBA["MIN_SAVE_CAP"]`, `CBA["MAX_SAVE_CAP"]`, `cap_seed(league_id)`, and save-range clamping in `next_cap`.
- `pipeline/load_save.py`: `cap_season(snapshot, season)` and `load_team_caps(cur, snapshot, season, teams, report, capture_id=None)` populating `team_cap_seasons`.
- `pipeline/drift_check.py`: `"salary_cap"` drift check comparing the save's cap against `team_cap_seasons`.
- `server/franchise_api.py`: future `estimated` years in `cap_years()` projected deterministically with `cap_history.cap_in` instead of leaking or diverging via `next_cap(..., "franchise_api")`.
- `pipeline/league_office.py`: `step_salary_cap` at `OFFSEASON_START`, cap drift write-back on other stages, `verify_read_back` check for `SalaryInfo.TeamSalaryCap`, and verified post-write `team_cap_seasons` sync.

**JR's answers (2026-11-02):**
- **D1 (Q1):** `CBA["CAP_GROWTH"]` uses option B, the gentler long-horizon curve `{"mean": 0.045, "sd": 0.020, "lo": -0.01, "hi": 0.09}` (~27 seasons of unconstrained headroom from `$301.2M` in 2026 through ~2053 before touching Madden's `$973.71M` / `$163.83M` binary save ceilings).
- **D2 (Q2):** Future unrecorded columns in `GET /{abbr}/cap` use option A, smooth expected growth (`+4.5%/yr` via `cap_history.cap_in`, `estimated: true`) so offseason cap jumps and squeezes are not revealed 4 years early.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | Read-only check on `CAREER-WEEK1.20261030_033907.bak` (2026-11-02): `SalaryInfo` row 0 (`tableId 5186`) stores `TeamSalaryCap = 18120` as a 32-bit `int` with schema `minValue = 12000, maxValue = 85371`. `madden-franchise` does not add `minValue` on 32-bit `int` fields, so `18120 + 12000 = 30120` `$10k` units = `$301.20M` (the real 2026 NFL cap). Round-trip write `TeamSalaryCap = 20000` reads back `20000` (`$320.00M`). | `league_setup.mjs:382` (`salary_cap: 18120`) and `league_office.py:1423` (`int(v) * 10_000 = $181.20M`) are currently `$120.0M` low on every rookie and promoted-PS contract. Add `SALARY_CAP_OFFSET = 12000` when reading and subtract `12000` when writing `SalaryInfo.TeamSalaryCap`. |
| S2 | Same check on `SalaryInfo.SalaryCapIncreasePerYear` (`tableId 4781`, `int[]` row 0): `int0 = 2500, int1 = 2000, int2 = 1000, int3 = 500, int4..int29 = 0`. Across `CAREER-NOPROGRESSION` backups (`pre_offseason` Super Bowl 2026 → `offseason_start` OffSeason week 0 → `next_season` PreSeason 2027), `TeamSalaryCap` moved once at Super Bowl → OffSeason week 0 (`18120 → 20321`, `$301.20M → $323.21M`) and stayed `20321` through PreSeason. | Madden stops scheduled cap increases after 4 seasons (`int4..int29 = 0`, explaining `madden_2036` sitting at `$367.0M`), and rolls the cap into `season + 1` at `OFFSEASON_START`. Zeroing `SalaryCapIncreasePerYear` in `league_setup.mjs` and writing `TeamSalaryCap` at `OFFSEASON_START` puts Project Mu after Madden's rollover step. |
| S3 | Read-only check across all 128 team-save rows (`32` teams × `4` saves: `CAREER-WEEK1`, `pre_offseason`, `offseason_start`, `next_season`, 2026-11-02): `Team.SalCapCapRoom`, `SalCapSpendingMoney` and `SalCapNextYearCapRoom` are 19-bit fields whose bundled M25 schema says `minValue = -40000, maxValue = 200000`, whereas Madden 27 stores `actual_10k + 200000`. Thus `madden-franchise` reports `actual_10k + 160000` (Panthers Week 1: `161169 - 160000 = 1169` = `$11.69M`, matching JR's `$11.7M` screen in spike R6). On all 128 rows, `(SalCapCapRoom - 160000) == (TeamSalaryCap + 12000 + RolloverCap + ThisYearCapCredits) - (SalCapRosterReserve + ThisYearCapPenalties + SalCapOffersReserve)` and `(SalCapSpendingMoney - 160000) == (SalCapCapRoom - 160000) - SalCapRookieReserve - SalCapRosterFillReserve - SalCapThisYearOfferReserve` with zero error. | Resolves the open unit question in `pipeline/load_save.py:46` and `pipeline/league_office.py:197`. Delta writes (`CAP_ROOM_UNIT = 1`) already use the right `$10k` step, and absolute reads subtract `160000`. |
| S4 | `lib.mjs:152` rejects `value > meta.maxValue`, and `league_office.py:199` clamps `CAP_ROOM_RANGE = (-40_000, 200_000)`. Because of the `+160,000` bias, `200,000` corresponds to only `+$400.0M` actual cap room, whereas 19 bits hold `0..524,287` raw (`-40,000..484,287` formatted = `-$2.0B..+$3.24B` actual; round-trip write `205,000` verified on `/tmp`). `record_edits.mjs:24` has a local copy of `fieldMeta` without `length`. | Share `fieldMeta` (including `length: o.length`) from `lib.mjs` and allow up to `484,287` (`(2 ** 19 - 1) + minValue`) on 19-bit `minValue = -40000, maxValue = 200000` fields, and widen `CAP_ROOM_RANGE` in `league_office.py`. |
| S5 | Save schema ceilings (`CAREER-WEEK1`, 2026-11-02): `SalaryInfo.TeamSalaryCap` max is `85371` (`(85371 + 12000) * $10k = $973.71M`); `PlayerContract.SalaryYear0..N` and `BonusYear0..N` max is `16383` (`$163.83M` each); `Player.PLYR_CAPSALARY` max is `32766` (`$327.66M`). From `$301.2M` in 2026, `6.5%/yr` mean growth reaches `~$499M` in 2034 and hits `$973.71M` around 2045 (~19 seasons); `4.5%/yr` mean growth reaches `~$428M` in 2034 and `$973.71M` around 2053 (~27 seasons). | `contracts_engine.next_cap` (`:347`) must clamp to `[MIN_SAVE_CAP ($240.0M), MAX_SAVE_CAP ($973.71M)]` so `record_edits.mjs` never refuses an overflow. Which mean growth rate to run for the 2026–2050+ franchise is JR's call (§8 Q1). |
| S6 | At `offseason_start` (`CAREER-NOPROGRESSION`), `SeasonInfo.CurrentSeasonYear` is still `2026` (`week_type = 'OffSeason'`), while `TeamSalaryCap`, `RolloverCap`, `ThisYearCapPenalties` and `SalCapRookieReserve` have already rolled to `2027` (`season + 1`). `transactions_engine.fetch_caps(cur, season, target)` (`:743`), `coaching_carousel.py:249`, and `franchise_api.py:345` query `team_cap_seasons` for `target = season + 1` during the offseason. | `load_save.py` must write `team_cap_seasons` under `cap_season = season + 1` when `week_type == 'OffSeason'` and `season` otherwise, so `2026`'s finished cap row is preserved and offseason engines read `2027`'s cap. |
| S7 | Spike R6 (`V2_CORE_BUILD_PLAN.md:7`): Madden does not recompute `Team.SalCapRosterReserve` after `save_writer` signings on Advance Week, whereas `league_office.py` updates `SalCapCapRoom` on every move. | In `load_save.load_team_caps`, derive `cap_space = cap_room * 10_000`, `total_spend = adjusted_cap - cap_space`, and `salaries = max(0, total_spend - cap_penalty - offers_reserve)`. On untouched Madden saves this equals `SalCapRosterReserve * 10_000` on all 128 checked rows, and after league-office moves it keeps `adjusted_cap - total_spend == cap_space` exact. |
| S8 | `server/franchise_api.py:136,651` projects future cap-sheet columns with `ce.next_cap(prev, y, "franchise_api")`. Using the franchise's real cap seed there would reveal future random cap jumps 4 years early; keeping `"franchise_api"` makes projections noisy and inconsistent with `cap_history.cap_in` (`engines/cap_history.py:24`). | `franchise_api.cap_years` should project unrecorded future years deterministically with `cap_history.cap_in(y, {y - 1: prev})` (`estimated: True`), while `league_office.py` draws the real cap at `OFFSEASON_START` with `next_cap(prev_cap, target, cap_seed(league_id))` (§8 Q2). |
| S9 | `team_cap_seasons` (`db/init_db.sql:419-433`) and `pipeline.contracts.upsert_team_cap` (`pipeline/contracts.py:35`) already hold all needed columns (`cap`, `rollover`, `adjusted_cap`, `cap_space`, `salaries`, `cap_penalty`, `total_spend`, `next_rookie_reserve`), read by `cap_history`, `trade_value`, `career_earnings`, `transactions_engine`, `gm_trade_engine`, `gm_direction`, `coaching_carousel`, `franchise_api`, and `newsroom/db_tools.py:3130`. | No database migration is needed. Populating `team_cap_seasons` in `load_save.py` and updating it on `league_office` writes feeds every existing engine and newsroom tool automatically. |

## 1. Scope

**In:**
- Binary offset helpers and 19-bit cap-room validation in `pipeline/save_reader/` (§2.1).
- `pc_save_reader.mjs` (`READER_VERSION = 6`) exporting `salary_cap` and `teams[].cap` (§2.1).
- `league_setup.mjs` fixing `leagueState()`'s `salary_cap` units and zeroing `SalaryCapIncreasePerYear` in `setupLeague()` (§2.1).
- Save-ceiling clamping and `cap_seed` in `engines/contracts_engine.py` (§2.2).
- `load_save.py` loading `team_cap_seasons` for `cap_season(snapshot, season)` (§2.3).
- `drift_check.py` reporting `"salary_cap"` drift against `team_cap_seasons` (§2.4).
- `franchise_api.py` projecting future `estimated` cap columns deterministically via `cap_history.cap_in` (§2.2).
- `league_office.py` computing and writing the new league cap and 32-team room deltas at `OFFSEASON_START`, restoring cap drift on other stages, verifying `TeamSalaryCap` on read-back, and syncing `team_cap_seasons` after verified writes (§2.5).

**Out (non-goals):**
- Changing how `trade_value.py` or `career_earnings.py` read `cap_history` (already built in `CAP_RELATIVE_CONTRACTS_PLAN.md`).
- Creating new `PlayerContract` rows in `sign.mjs` (tracked separately in `V2_MASTER_PLAN.md` §7.3 item 6).
- Dedicated offseason newsroom articles solely for the cap announcement (writers already read `team_cap_seasons` via `newsroom/db_tools.py:3130`).
- Changing Madden's 12% team rollover cap calculation (Madden computes `RolloverCap` cleanly at Super Bowl → `OFFSEASON_START`).

## 2. Design

### 2.1 Save reader, setup, and record validation (`pipeline/save_reader/`)

`pipeline/save_reader/lib.mjs`:
```js
export const SALARY_CAP_OFFSET = 12_000;          // SalaryInfo.TeamSalaryCap raw + 12,000 = $10k units
export const SALARY_CAP_RAW_RANGE = [12_000, 85_371];
export const SAL_CAP_ROOM_BIAS = 160_000;         // Team.SalCap*Room formatted - 160,000 = $10k units
export const salaryCapUnits = raw => (Number.isFinite(raw) ? raw + SALARY_CAP_OFFSET : null);
export const rawSalaryCap = units => units - SALARY_CAP_OFFSET;
export const capRoomUnits = raw => (Number.isFinite(raw) ? raw - SAL_CAP_ROOM_BIAS : null);
export const rawCapRoom = units => units + SAL_CAP_ROOM_BIAS;
```
- `fieldMeta(record, key)` adds `length: o.length`. `record_edits.mjs` imports `fieldMeta` from `./lib.mjs` instead of keeping its own copy.
- `fieldValueError(meta, value)`: when `meta.minValue === -40_000 && meta.maxValue === 200_000 && meta.length === 19`, the upper bound is `(2 ** 19 - 1) + meta.minValue` (`484_287`, i.e., `+$3.24287B` actual room) instead of `200_000`.

`pipeline/save_reader/pc_save_reader.mjs` (`READER_VERSION = 6`):
- Top-level `salary_cap` (or `null` if `SalaryInfo` is missing):
  ```json
  {
    "table_id": 5186,
    "row": 0,
    "raw_team_salary_cap": 18120,
    "team_salary_cap": 30120,
    "initial_salary_cap": 30120,
    "increase_table": {"table_id": 4781, "row": 0, "values": {"int0": 2500, "...": 0}}
  }
  ```
- Each real team (`team_index < 32`) in `teams[]` gains `cap` (all values in true `$10,000` units):
  ```json
  {
    "rollover": 1380,
    "cap_credits": 0,
    "roster_reserve": 27868,
    "cap_penalties": 2463,
    "offers_reserve": 0,
    "rookie_reserve": 0,
    "fill_reserve": 0,
    "this_year_offer_reserve": 0,
    "cap_room": 1169,
    "spending_money": 1169,
    "next_year_cap_room": 1433,
    "team_salary": 10294
  }
  ```

`pipeline/save_reader/league_setup.mjs`:
- `leagueState(savePath)` returns:
  - `salary_info_table_id`: `salary?.header.tableId ?? null`
  - `raw_salary_cap`: `raw` (`SalaryInfo.records[0].TeamSalaryCap ?? null`)
  - `salary_cap`: `salaryCapUnits(raw)` (true `$10,000` units, e.g., `30120` for `$301.20M`)
  - `team_table_id`, `teams` (raw Team fields unchanged so `_cap_records` adds deltas directly to stored Team values), and `spare_contract_rows`.
- `setupLeague(savePath, outPath)` follows `SalaryInfo.records[0].SalaryCapIncreasePerYear` to its `int[]` row and sets every `int0..intN` slot to `0`, verifies all `0` on read-back, and includes `capIncreaseZeroed: N` in the returned report.

### 2.2 Cap growth bounds and projections (`engines/contracts_engine.py`, `server/franchise_api.py`)

`engines/contracts_engine.py`:
```python
SALARY_CAP_OFFSET_UNITS = 12_000
SALARY_CAP_RAW_RANGE = (12_000, 85_371)
SAL_CAP_ROOM_BIAS_UNITS = 160_000

# Inside CBA:
"CAP_GROWTH": {"mean": 0.045, "sd": 0.020, "lo": -0.01, "hi": 0.09},   # D1: ~27 seasons headroom to $973.71M
"MIN_SAVE_CAP": (12_000 + 12_000) * 10_000,     # $240,000,000 (raw TeamSalaryCap = 12,000)
"MAX_SAVE_CAP": (85_371 + 12_000) * 10_000,     # $973,710,000 (raw TeamSalaryCap = 85,371)

def cap_seed(league_id) -> str:
    """Canonical seed for annual league salary-cap draws."""
    return f"salary_cap:{league_id if league_id is not None else 'default'}"
```
- `next_cap(prev_cap: int, season: int, seed: str) -> int`: draws from `CBA["CAP_GROWTH"]` (`mean = 0.045, sd = 0.020, lo = -0.01, hi = 0.09`, D1), rounds to `CBA["CAP_ROUND"]`, and clamps the result to `[CBA["MIN_SAVE_CAP"], CBA["MAX_SAVE_CAP"]]`.

`server/franchise_api.py`:
- In `cap_years(players, caps, season, T, years)`: when `row.get("cap")` is missing, project from `prev` (or empty `{}` when `prev is None`) via `cap_history.cap_in(y, {y - 1: prev} if prev is not None else {})`, clamped to `CBA["MAX_SAVE_CAP"]`. Future `estimated: True` years thus reflect the expected CBA growth mean rather than leaking or diverging from random offseason draws.

### 2.3 Loading `team_cap_seasons` (`pipeline/load_save.py`)

```python
def cap_season(snapshot: dict, season: int) -> int:
    """The league year the save's cap sheet belongs to: season + 1 during OffSeason, else season."""

def team_cap_dollars(league_cap_units: int, team_cap: dict) -> dict:
    """Whole-dollar team_cap_seasons columns from decoded $10,000-unit snapshot fields (pure)."""

def load_team_caps(cur, snapshot: dict, season: int, teams: dict, report: dict, capture_id=None) -> None:
    """Upserts 32 team_cap_seasons rows for cap_season(snapshot, season) on reader_version >= 6 snapshots."""
```
- `team_cap_dollars(league_cap_units, tc)` computes:
  - `cap = league_cap_units * 10_000`
  - `rollover = int(tc["rollover"]) * 10_000`
  - `adjusted_cap = (league_cap_units + int(tc["rollover"]) + int(tc.get("cap_credits") or 0)) * 10_000`
  - `cap_space = int(tc["cap_room"]) * 10_000`
  - `cap_penalty = int(tc["cap_penalties"]) * 10_000`
  - `offers = int(tc.get("offers_reserve") or 0) * 10_000`
  - `total_spend = adjusted_cap - cap_space`
  - `salaries = max(0, total_spend - cap_penalty - offers)`
  - If `int(tc.get("rookie_reserve") or 0) > 0`, sets `rookie_reserve_dollars = int(tc["rookie_reserve"]) * 10_000` (written onto `(team_id, season)`'s `next_rookie_reserve` when `week_type == "OffSeason"` and a `season` row exists, so `franchise_api.py:662` sees Madden's exact pick-slotted reserve before the draft).
- Calls `pipeline.contracts.upsert_team_cap(cur, tid, target_cap_season, values, capture_id)` for each mapped real team.
- Snapshots without `salary_cap` (`reader_version < 6`) are a no-op; `report["team_caps"] = n` records the count when loaded.

### 2.4 Salary cap drift check (`pipeline/drift_check.py`)

- `KINDS` becomes `("ratings", "contracts", "rosters", "depth_charts", "settings", "salary_cap")`.
- `fetch_state` adds `"league_caps": cap_history.league_caps(cur)`.
- `compare_salary_cap(snapshot, state, season) -> list[dict]`:
  - Returns `[]` if `snapshot.get("salary_cap")` is missing or `team_salary_cap` is `None`.
  - Looks up `expected = (state.get("league_caps") or {}).get(ls.cap_season(snapshot, season))`. If `expected is None`, returns `[]`.
  - Computes `found = int(snapshot["salary_cap"]["team_salary_cap"]) * ce.MONEY_UNIT`.
  - If `expected != found`, returns `[{"season": ls.cap_season(snapshot, season), "field": "team_salary_cap", "expected": expected, "found": found}]`, else `[]`.

### 2.5 League Office annual rollover and sync (`pipeline/league_office.py`)

- `CAP_ROOM_RANGE = (-40_000, 484_287)`.
- `league_cap_units(state, snapshot=None) -> int | None`: returns `(state or {}).get("salary_cap")` (already decoded by `league_setup.mjs`), else `(snapshot.get("salary_cap") or {}).get("team_salary_cap")`. `league_cap(state, snapshot=None)` returns `units * ce.MONEY_UNIT` when known, else `ce.CBA["REFERENCE_CAP"]`. `Plan` stores `self.league_cap_dollars = league_cap(state, snapshot)`.
- `Deps` adds `league_caps(self, cur)` (`cap_history.league_caps(cur)`) and `sync_team_caps(self, cur, season, cap_dollars)` (updates `team_cap_seasons` for `season`: `cap = cap_dollars`, `adjusted_cap = cap_dollars + COALESCE(rollover, 0) + (COALESCE(adjusted_cap, cap) - cap - COALESCE(rollover, 0))`, `cap_space = adjusted_cap - COALESCE(total_spend, 0)`).
- **`step_salary_cap(plan, deps, cur)` at `OFFSEASON_START`** (runs before `step_promoted_ps` so promoted minimums use the new cap):
  1. `caps = deps.league_caps(cur)`.
  2. `prev_cap = caps.get(plan.season) or plan.league_cap_dollars`.
  3. `league_id = (plan.snapshot.get("franchise") or {}).get("league_id")`.
  4. `seed_str = ce.cap_seed(league_id)` and `target_cap = ce.next_cap(prev_cap, plan.target, seed_str)`.
  5. Sets `plan.league_cap_dollars = target_cap`.
  6. Resolves `salary_table_id = (plan.state or {}).get("salary_info_table_id") or (plan.snapshot.get("salary_cap") or {}).get("table_id")`.
  7. Computes `target_units = target_cap // ce.MONEY_UNIT` and `save_units = league_cap_units(plan.state, plan.snapshot)`. If `salary_table_id` is known and `save_units is not None` and `target_units != save_units`:
     - `plan.add_records([{"table": "SalaryInfo", "tableId": salary_table_id, "row": 0, "set": {"TeamSalaryCap": target_units - ce.SALARY_CAP_OFFSET_UNITS}}])`
     - `delta = target_units - save_units`
     - For every `team_index` in `plan.teams`: `plan._cap(team_index, delta, delta, 0)`.
     - `plan.counts["salary_cap_updated"] = 1`.
  8. Records `plan.db["salary_cap"] = {"season": plan.target, "prev_cap": prev_cap, "cap": target_cap, "madden_cap": save_units * ce.MONEY_UNIT if save_units is not None else None, "growth_pct": round((target_cap - prev_cap) / prev_cap * 100, 2) if prev_cap else 0.0, "seed": seed_str}`, copied into `summary["salary_cap"]`.
- **Cap drift write-back (stages other than `OFFSEASON_START`):**
  - In `step_drift`, if `plan.stage != OFFSEASON_START` and `drift.get("salary_cap")`: takes `entry = drift["salary_cap"][0]`, sets `plan.league_cap_dollars = entry["expected"]`, writes `SalaryInfo.TeamSalaryCap = entry["expected"] // ce.MONEY_UNIT - ce.SALARY_CAP_OFFSET_UNITS`, shifts all 32 teams' cap rooms by `(entry["expected"] - entry["found"]) // ce.MONEY_UNIT`, and increments `plan.counts["drift_salary_cap"]`.
- **`verify_read_back(edits, snapshot)`:**
  - When `edits.get("records")` sets `TeamSalaryCap` on table `"SalaryInfo"` row `0` and `snapshot.get("salary_cap")` is present, checks `snapshot["salary_cap"]["raw_team_salary_cap"] == written_raw` (and `team_salary_cap == written_raw + ce.SALARY_CAP_OFFSET_UNITS`).
- **Post-write DB sync:**
  - Inside `_apply_db` (called only after the save is written, verified, and replaced — never on `--dry-run`), when `db.get("salary_cap")` is set, calls `deps.sync_team_caps(cur, info["season"], info["cap"])` (and also applies `plan.cap` room deltas to `team_cap_seasons.cap_space` / `total_spend` when rows exist).

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Madden might recompute `SalaryInfo.TeamSalaryCap` from `InitialSalaryCap + sum(SalaryCapIncreasePerYear)` at a later stage (`FA prep` or `PreSeason`) and overwrite Project Mu's `TeamSalaryCap`. | Spike S1 below, plus `drift_check` checking `salary_cap` on every stage save. | `drift_check` + `step_drift` already restores `TeamSalaryCap` automatically; if Madden recomputes from `SalaryCapIncreasePerYear`, write `target_units - prev_units` into `SalaryCapIncreasePerYear[franchise_year - 1]` as well. |
| R2 | At `6.5%/yr` mean growth from `$301.2M` (2026), the cap reaches Madden's `$973.71M` schema ceiling (`TeamSalaryCap = 85371`) around 2045 (~19 seasons), and star QB base salaries approach `PlayerContract.SalaryYearN`'s `$163.83M` ceiling (`16383`). | Exact arithmetic from S5; `next_cap` clamps at `MAX_SAVE_CAP` and `to_madden` clamps deal years. | JR chooses a `4.5%/yr` mean in §8 Q1 (~27 seasons of headroom, to ~2053), or accepts clamping once the cap nears `$973.71M`. |
| R3 | Existing tests pass hand-built snapshots (`reader_version` 4 or 5) without `salary_cap` or `teams[].cap`, or `state={"salary_cap": 30120}`. | Unit test suite (`test_load_save.py`, `test_drift_check.py`, `test_league_office.py`). | `load_team_caps` and `compare_salary_cap` are no-ops when `salary_cap` is absent. Note: any test fixture in `test_league_office.py` that passed raw `25540` (`$255.4M / $10k`) into `state["salary_cap"]` already passed decoded `$10k` units, which matches the fixed `leagueState()` contract. |

**Spike first:** optional 5-minute in-game check before starting the canonical 2026 run (does not block Wave 1 code, which is offline-verified against the real backup saves): on a `CAREER-TUNE-*` clone of `CAREER-STAFF`, write `TeamSalaryCap = 23000` (`$350.0M`) + `+2679` room delta and zero `SalaryCapIncreasePerYear`, advance in Madden past `FA prep` into `FREE_AGENCY`, and confirm with `node pipeline/save_reader/league_setup.mjs state` that `salary_cap` stays `35000` (`$350.0M`).

## 4. Tests (synthetic, no live DB)

- `pipeline/save_reader/test/lib.test.mjs`: `salaryCapUnits` / `rawSalaryCap` round-trip (`18120 <-> 30120`); `capRoomUnits` / `rawCapRoom` round-trip (`161169 <-> 1169`, `-38831` negative room); `fieldValueError` accepts `205000` and `484287` on a 19-bit `minValue = -40000, maxValue = 200000` field and rejects `484288` and `-40001`.
- `pipeline/save_reader/test/league_setup.test.mjs`: `setupLeague` zeroes every `SalaryCapIncreasePerYear` `int[]` slot (`capIncreaseZeroed` > 0 on the first run, 0 on the second) and `leagueState` reports `salary_info_table_id`, `raw_salary_cap`, and `salary_cap === raw_salary_cap + 12000` (skips if sample save absent).
- `pipeline/save_reader/test/record_edits.test.mjs`: `record_edits` accepts a `Team` `SalCapCapRoom` edit above `200000` (`205000`) and reads it back on the sample save (skips if sample save absent).
- `pipeline/save_reader/test/read_save.test.mjs`: `READER_VERSION === 6`; `salary_cap.team_salary_cap === salary_cap.raw_team_salary_cap + 12000`; every real team has `cap` and `cap.cap_room === (salary_cap.team_salary_cap + cap.rollover + cap.cap_credits) - (cap.roster_reserve + cap.cap_penalties + cap.offers_reserve)` on the sample save.
- `tests/test_contracts_engine.py`: `CBA["CAP_GROWTH"]` is `{"mean": 0.045, "sd": 0.020, "lo": -0.01, "hi": 0.09}`; `next_cap` clamps at `CBA["MAX_SAVE_CAP"]` (`$973.71M`) from `$950M` and at `CBA["MIN_SAVE_CAP"]` (`$240.0M`) from `$241M`; normal mid-range draws stay inside `[lo, hi]`; `cap_seed(league_id)` is deterministic per league id.
- `tests/test_trade_value.py`: `test_basis_line` reflects `+4.5%/yr` (`CBA["CAP_GROWTH"]["mean"]`).
- `tests/test_franchise_api.py`: `cap_years` projects unrecorded future seasons deterministically via `cap_history.cap_in` (`estimated: True`) without calling random `next_cap(..., "franchise_api")`, and uses recorded `team_cap_seasons` rows (`estimated: False`) when present.
- `tests/test_load_save.py`: `cap_season` returns `season` in regular season/playoffs/preseason and `season + 1` in `OffSeason`; `team_cap_dollars` preserves `adjusted_cap - total_spend == cap_space` both on untouched Madden numbers and after a `SalCapCapRoom` move delta; `load_team_caps` upserts 32 rows on `reader_version >= 6` snapshots, sets `season`'s `next_rookie_reserve` in `OffSeason`, and is a no-op on v5 snapshots.
- `tests/test_drift_check.py`: `compare_salary_cap` returns `[]` when `salary_cap` is absent, when `team_cap_seasons` has no row for `cap_season`, or when caps match; returns one mismatch dict on `OffSeason` (`season + 1`) or `RegularSeason` (`season`) drift.
- `tests/test_league_office.py`: `step_salary_cap` at `OFFSEASON_START` draws `next_cap(prev_cap, target, cap_seed(league_id))` from `season`'s `team_cap_seasons` cap, writes `SalaryInfo.TeamSalaryCap` with `-12000` offset, shifts all 32 teams' cap rooms by `target_units - save_units`, prices `step_promoted_ps` against the new cap, is idempotent on re-run, and syncs `team_cap_seasons` only on a verified non-dry-run write; `step_drift` restores `SalaryInfo.TeamSalaryCap` and team rooms on non-`OFFSEASON_START` drift; `verify_read_back` catches a mismatched `raw_team_salary_cap`.

Run with:
- `npm --prefix pipeline/save_reader test`
- `pytest -m "not integration" tests/test_contracts_engine.py tests/test_cap_history.py tests/test_franchise_api.py tests/test_load_save.py tests/test_drift_check.py tests/test_league_office.py`

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Save reader v6, setup cap-schedule zeroing, and 19-bit cap-room validation

- **Owns:** `pipeline/save_reader/lib.mjs`, `pipeline/save_reader/pc_save_reader.mjs`, `pipeline/save_reader/league_setup.mjs`, `pipeline/save_reader/record_edits.mjs`, `pipeline/save_reader/test/league_setup.test.mjs`, `pipeline/save_reader/test/lib.test.mjs`, `pipeline/save_reader/test/read_save.test.mjs`, `pipeline/save_reader/test/record_edits.test.mjs`
- **Reads:** `pipeline/league_office.py:792-815` (`_cap_records`), `pipeline/league_office.py:1420-1424` (`league_cap`), this plan §0 S1–S4 and §2.1
- **Depends on:** none
- **Done when:** `READER_VERSION` is `6` with `salary_cap` and `teams[].cap` decoded in `$10k` units; `leagueState()` returns decoded `salary_cap` plus `salary_info_table_id` and `raw_salary_cap`; `setupLeague()` zeroes `SalaryCapIncreasePerYear` and verifies it on read-back; `record_edits` accepts 19-bit `SalCap*Room` values above `200,000`.
- **Verify:** `npm --prefix pipeline/save_reader test` passes; `MU_SAMPLE_SAVE=verify_output/save_backups/CAREER-WEEK1.20261030_033907.bak npm --prefix pipeline/save_reader test` passes if the backup save is present.

### U2: Engine cap bounds, deterministic cap-sheet outlook, PC cap loader, and cap drift check

- **Owns:** `engines/contracts_engine.py`, `server/franchise_api.py`, `pipeline/load_save.py`, `pipeline/drift_check.py`, `tests/test_contracts_engine.py`, `tests/test_trade_value.py`, `tests/test_franchise_api.py`, `tests/test_load_save.py`, `tests/test_drift_check.py`
- **Reads:** `engines/cap_history.py`, `engines/trade_value.py:465-485` (`basis_line`), `pipeline/contracts.py:35-43` (`upsert_team_cap`), `engines/transactions_engine.py:743-752` (`fetch_caps`), this plan §0 S5–S9 and §2.2–§2.4
- **Depends on:** none
- **Done when:** `CBA["CAP_GROWTH"]` is D1 (`4.5%` mean), `next_cap` clamps to `[MIN_SAVE_CAP, MAX_SAVE_CAP]`, and `cap_seed(league_id)` exists; `franchise_api.cap_years` projects unrecorded future seasons via `cap_history.cap_in` (D2); `load_save` populates `team_cap_seasons` under `cap_season(snapshot, season)` for `reader_version >= 6`; `drift_check.diff` includes `"salary_cap"`.
- **Verify:** `pytest -m "not integration" tests/test_contracts_engine.py tests/test_cap_history.py tests/test_trade_value.py tests/test_franchise_api.py tests/test_load_save.py tests/test_drift_check.py` all pass.

### U3: League Office annual cap rollover, drift write-back, and DB sync

- **Owns:** `pipeline/league_office.py`, `tests/test_league_office.py`
- **Reads:** `pipeline/save_reader/league_setup.mjs` (from U1), `engines/contracts_engine.py` and `pipeline/drift_check.py` (from U2), this plan §2.5
- **Depends on:** U1, U2
- **Done when:** `OFFSEASON_START` computes `next_cap`, writes `SalaryInfo.TeamSalaryCap` (`-12000` raw offset) and 32-team room deltas, prices `step_promoted_ps` on the new cap, verifies `TeamSalaryCap` on read-back, and updates `team_cap_seasons` upon verified write; non-`OFFSEASON_START` stages restore `"salary_cap"` drift; `CAP_ROOM_RANGE` is `(-40_000, 484_287)`.
- **Verify:** `pytest -m "not integration" tests/test_league_office.py tests/test_watch_save.py tests/test_transactions_engine.py` all pass.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2 | — | JS save-reader/setup work (U1) and Python engine/loader/drift/API work (U2) share no files and build against §2.1's snapshot JSON shape |
| 2 | U3 | U1, U2 | `league_office.py` consumes U1's `leagueState()` / `salary_cap` fields and U2's `cap_seed`, `SALARY_CAP_OFFSET_UNITS`, and `drift["salary_cap"]` |

**File ownership check:**

| File | Unit |
|---|---|
| `engines/contracts_engine.py` | U2 |
| `pipeline/drift_check.py` | U2 |
| `pipeline/league_office.py` | U3 |
| `pipeline/load_save.py` | U2 |
| `pipeline/save_reader/league_setup.mjs` | U1 |
| `pipeline/save_reader/lib.mjs` | U1 |
| `pipeline/save_reader/pc_save_reader.mjs` | U1 |
| `pipeline/save_reader/record_edits.mjs` | U1 |
| `pipeline/save_reader/test/league_setup.test.mjs` | U1 |
| `pipeline/save_reader/test/lib.test.mjs` | U1 |
| `pipeline/save_reader/test/read_save.test.mjs` | U1 |
| `pipeline/save_reader/test/record_edits.test.mjs` | U1 |
| `server/franchise_api.py` | U2 |
| `tests/test_contracts_engine.py` | U2 |
| `tests/test_drift_check.py` | U2 |
| `tests/test_franchise_api.py` | U2 |
| `tests/test_league_office.py` | U3 |
| `tests/test_load_save.py` | U2 |
| `tests/test_trade_value.py` | U2 |

No path appears twice.

## 7. Spec changes when built

- §1.1 Repository Layout:
  - `pc_save_reader.mjs`: `reader_version 6` (`salary_cap` block and `teams[].cap` block in decoded `$10,000` units).
  - `league_setup.mjs`: `setup` zeroes `SalaryInfo.SalaryCapIncreasePerYear`; `state` returns decoded `salary_cap`, `raw_salary_cap`, and `salary_info_table_id`.
  - `lib.mjs` / `record_edits.mjs`: `SALARY_CAP_OFFSET` (`12,000`), `SAL_CAP_ROOM_BIAS` (`160,000`), shared `fieldMeta`, and 19-bit `SalCap*Room` upper bound (`484,287`).
  - `docs/done/SALARY_CAP_GROWTH_PLAN.md` (moved from `docs/not_started/`).
- §2.17a Contracts Engine: `MIN_SAVE_CAP` (`$240.0M`), `MAX_SAVE_CAP` (`$973.71M`), `SALARY_CAP_OFFSET_UNITS`, `cap_seed(league_id)`, clamping in `next_cap`, and the chosen `CAP_GROWTH` parameters (D1).
- §2.21a League Office & Drift Check: `OFFSEASON_START` `step_salary_cap` (`next_cap` written to `SalaryInfo.TeamSalaryCap` + 32-team cap-room deltas, verified on read-back, synced to `team_cap_seasons`), `"salary_cap"` drift check and write-back, widened `CAP_ROOM_RANGE`.
- §3.3d Contracts & Cap / §6.0b PC Save Loader: `load_save.py` now populates `team_cap_seasons` from PC saves (`reader_version >= 6`) under `cap_season` (`season + 1` in `OffSeason`, else `season`), with the exact binary offset formulas from S1/S3/S7.
- §4.6b Franchise API: `GET /{abbr}/cap` projects unrecorded future years deterministically with `cap_history.cap_in` (`estimated: true`) rather than `next_cap(..., "franchise_api")`.

## 8. Decisions for JR

Q1–Q2 are answered (D1–D2 above). None open.

| # | Question | Recommendation |
|---|---|---|
| Q1 | **Annual cap growth curve (`CBA["CAP_GROWTH"]`):** Madden 27's binary save caps `SalaryInfo.TeamSalaryCap` at `$973.71M` (`85371` raw) and `PlayerContract.SalaryYearN` at `$163.83M` (`16383`). Starting from `$301.2M` in 2026: **(A)** Keep real-NFL `mean = 6.5%, sd = 2.5%, lo = -2%, hi = +12%` (`~$499M` at 2034 takeover; reaches the `$973.71M` save ceiling around **2045**, ~11 seasons after takeover), or **(B)** Use a slightly gentler long-franchise curve `mean = 4.5%, sd = 2.0%, lo = -1%, hi = +9%` (`~$428M` in 2034; **27 seasons** of unconstrained growth through **~2053** before touching Madden's `$973.71M` / `$163.83M` binary ceilings)? | **Decided: B** (`mean = 4.5%, sd = 2.0%, lo = -1%, hi = +9%`). |
| Q2 | **Future columns on `#franchise` Roster & Cap (`GET /{abbr}/cap?years=5`):** Should unrecorded future years show **(A)** smooth expected growth (`+mean%/yr` via `cap_history.cap_in`, `estimated: true`) so offseason cap jumps/squeezes are genuine announcements nobody sees 4 years early, or **(B)** exact pre-rolled `next_cap` numbers from the franchise's `cap_seed(league_id)`? | **Decided: A** (smooth expected growth). |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|

