# Project Mu: NFL Weekly Mirror Plan

**Status:** approved by JR (2026-11-11); building, wave 1 of 4 done. Waves 2–4 are handed to a DeepSeek session (JR's /execute-plan rule, 2026-11-11). Spikes A–C (Madden PC, JR) run before wave 2.
- As built (wave 1): `store.mark_written` takes `(cur, season, week, run_id)` and also flips the week's PLANNED moves to WRITTEN; `record_moves` has `replace=False`; `build_week` takes an optional `sources=`. The migration is `2027_02_27_nfl_mirror.sql`. It was renamed from `2027_02_26_…` after the merge at audit-2's request (M-0444), because builder-8's `2027_02_26_league_shape_season.sql` was already on main. Neither migration is applied to any franchise DB; only `mu_scratch_*` DBs were used.
- Open from wave 1: (a) `coaching_carousel.staff_in()` and `head_coaches()` order tenures by `start_season DESC` with no `id DESC` tie-break, so after a midseason change in which the outgoing tenure also started that season, they may pick the old man. Add `, ct.id DESC` to both ORDER BYs, with an `offseason_check` run (U6 or a follow-up fix). (b) `staff.py` writes no `staff_moves` or `staff_contracts` rows for mirrored coaching changes, and a coordinator promoted to interim HC keeps his coordinator tenure unless the CSV names his replacement.
- Orchestrator fix: `tests/test_nfl_history_store.py` now leaves `nfl_mirror_*` out of its nfl_* table check (`346e063`).
**Builds on:** PROJECT_SPEC §2.21a (League Office), §3.3x (Real NFL history and `nfl_player_links`), §6.0b (save watcher), §2.19 (coaching tenures), [`REAL_NFL_HISTORY_PLAN.md`](../done/REAL_NFL_HISTORY_PLAN.md), [`COACH_SAVE_WRITES_PLAN.md`](../done/COACH_SAVE_WRITES_PLAN.md).

**Goal:** Before each simulated week of the 2026 season, make the save's rosters match the real NFL's for that week. That covers signings, releases, trades, practice squads, IR, injuries, depth charts and head-coach and coordinator changes. Madden still sims the games. First we catch up on the real weeks already played, one simulated week at a time, with the Madden pilot doing the simming. After that we go live, one simulated week per real week, through the Super Bowl. During catch-up Madden's own injuries are off and the real ones are copied in with their real return dates. Madden's injuries come back on at the first live week. Mirrored weeks get no articles, but their simulated games count in Mu's 2026 history and record book. Real players missing from the save are created with their EA Madden 27 ratings when EA has them, and with position averages by role when it doesn't. From the 2027 offseason on, Mu's engines run the league as they do now.

**Delivers:**
- `engines/nfl_mirror/`: `fetch.py`, `real_state.py`, `trades.py`, `store.py`, `staff.py`, `diff.py`, `contracts.py`, `injuries.py`, `depth.py`, `ea_ratings.py`, `create.py`, `__main__.py` (CLI `python3 -m engines.nfl_mirror status|preview|trades|enable|disable`)
- A pilot job, `catch-up` (`tools/madden_pilot/jobs.py`): it sims the real franchise one week at a time while the mirror is in catch-up, waiting for each week's league office write between weeks
- Migration `db/migrations/2027_02_27_nfl_mirror.sql`: `nfl_mirror_settings`, `nfl_mirror_weeks`, `nfl_mirror_moves`; `nfl_player_links.method` gains `CREATED`
- Hand-kept inputs: `data/history/nfl_trades_2026.csv` and `data/history/nfl_staff_changes_2026.csv`
- A league office step, `step_nfl_mirror` (`pipeline/league_office.py`), plus its adapter `pipeline/league_office_mirror.py`
- Watcher gating: in a mirrored week, no GM trade proposals, no articles and no press conferences

**JR's answers (2026-11-11):**
- D1. Catch up on the real weeks already played, then go live one week at a time for the rest of 2026. 2027 onward is Mu's engines as planned.
- D2. Injury lengths are estimates; in live weeks we can't know the future.
- D3. Madden's own injuries are turned off completely in the save for the mirrored weeks, not just set to slider 0.
- D4. Real players with no Madden record: create active and IR players by reusing spare records. Skip practice-squad-only players and fill the slot from Madden's own free agents.
- D5. Depth charts: real nflverse order for linked players, with Mu's depth-chart engine filling the rest of Madden's 35 slots.
- D6. Real 2026 coaching changes (midseason firings, interim head coaches, coordinator changes) are mirrored too.

**JR's answers (2026-11-11, second round):**
- D7. Catch-up weeks use the real return date (hindsight), not an estimate.
- D8. The Madden pilot sims the catch-up weeks on the real franchise.
- D9. A created player takes his EA Madden 27 ratings when EA's ratings database has him, and position averages by role otherwise.
- D10. Madden's injuries come back on as soon as the catch-up reaches the current real week, at the first LIVE week.
- D11. The simulated games of mirrored weeks count in Mu's 2026 history and record book.
- D12. In LIVE weeks (Madden's injuries on), the mirror copies only real IR, PUP and NFI placements and activations. It doesn't copy the weekly Out/Doubtful reports, and it never heals a player Madden hurt.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | nflverse publishes `roster_weekly_2026`, `injuries_2026` and `depth_charts_2026`. All three covered weeks 1–10 and were updated 2026-11-11 07:00 UTC (checked this session). Contracts (`historical_contracts.parquet`, OTC) were updated 2026-11-10. `trades.csv` was last updated **2026-05-19**. | Rosters, injuries, depth charts and contracts can be fetched. Trades can't: they come from a hand-kept CSV (§2.3). |
| S2 | Weekly roster statuses at week 10: `ACT` (A01 1,335; I01/I02 = inactive 144), `DEV` (practice squad, P01–P07 471), `RES` (R01 IR 227, R48 designated for return 28, R04 PUP 18, R05 4), `CUT`, `RET`, `EXE`. Only 28 teams have week-10 rows. | Teams on a bye have no rows that week, so `real_state` carries the last week forward. `RES` codes map to IR; `DEV` maps to Madden's `PracticeSquad` list. |
| S3 | Injury reports carry `report_status` (Out 588, Doubtful 43, Questionable 590, blank 1,620) and body-part text, but no length. | Lengths are estimated (§2.6). |
| S4 | `engines/nfl_history/link.py:321` `match()` links Madden pgids to gsis ids, stored in `nfl_player_links` (§3.3x). It linked 97.3% of veterans on CAREER-WEEK1. Its `method` CHECK allows only `EXACT, ESPN, ALIAS, FUZZY, OVERRIDE` (`db/migrations/2027_02_04_nfl_history.sql:152`). | The mirror keys every player on these links. Created players need a new method, `CREATED`. |
| S5 | On `CAREER-MUBASE` (this session, crude name match): 148 of 1,439 real week-1 active players sit on a different team, or in free agency, in Madden. There are 332 team joins after week 1 (58 active, ~227 team to team). | Week 1 alone is ~150 moves. |
| S5a | `link.match` run offline (snapshot positions, roster_weekly people) on `CAREER-MUBASE` against the 2,621 real players rostered at week 10: 2,201 linked, 420 unlinked. 16 of the 420 have a same-name Madden player at another position, e.g. Travis Hunter (CB in Madden, WR in nflverse) and Connor Heyward (TE vs RB). The other 404 are absent from the save. By role on the newest nflverse depth chart: 10 starters, 46 backups, 50 deeper reserves, 45 IR or reserve, 253 practice squad. 360 of the 404 went undrafted, and 174 are 2026 rookies. | Creation covers about 150 players (D4: everyone but the practice squad). `diff.py` also tries a name-only match across position groups and reports it for review, so a position change isn't treated as a missing player. |
| S5b | EA's public ratings page (`https://www.ea.com/games/madden-nfl/ratings?page=N`, 100 per page in its `__NEXT_DATA__`) serves Madden 27's newest weekly iteration (`madden-ratings-week-8` on 2026-11-11): 1,958 players, each with position, archetype, team, birthdate, height, weight, college, jersey and 55 ratings. The `drop-api.ea.com/rating/madden-nfl` JSON endpoint still serves Madden 26, and asking it for Madden 27 iteration ids returns nothing. Of the 404 absent players, EA has **119**: all 10 starters (e.g. Aaron Donald DT 95, kickers Dominic Zvada 75 and Drew Stevens 74), 44 of 46 backups, 48 of 50 deeper reserves and 17 of 45 IR. It has none of the 253 practice-squad players. | Our save is the launch roster, so EA's weekly roster updates added these players after it. `ea_ratings.py` reads the newest iteration and the creation path uses it first (D9); position averages cover the other ~32 non-practice-squad players. |
| S6 | `save_writer.mjs` accepts `players` (any Player field), `release`, `sign`, `trade`, `depthCharts`, `records` and `staff` (`pipeline/save_reader/save_writer.mjs:8-35`). Injuries are Player fields: `InjuryStatus, InjuryType, InjurySeverity, InjurySide, Min/MaxInjuryDuration, IsInjuredReserve` (`pc_save_reader.mjs:464-466`). | Every mirror move fits the existing writer, and no new `.mjs` is needed. Giving a healthy player a real injury is a `players` edit. Spike B checks that Madden honours it. |
| S7 | There is no player-create block. `pipeline/madden_player_edit.py:151` `player_edit(donor_row, target, new_name=, age_add=, commentary_id=)` builds a full rewrite of an existing record (name, position, archetype, ratings, commentary ID). | D4's creation reuses a spare record through `player_edit` (§2.8). |
| S8 | `plan_stage` (`pipeline/league_office.py:2184`) runs: JR's pending moves, engine trades (`step_engine_trades`, `:1345`, PRIORITY 1), accepted offers, drift write-back, then IN_SEASON `step_injuries` (`:1502`) and waivers, then coach depth charts (`step_coach_charts`, `:1554`) and `step_legality` (`:1528`, at most `MAX_ACTIVE` 63, `:222`). The first claim on a player stands (`:245-249`). | The mirror step takes the engine-trades slot, so drift and stage moves can't undo it. `step_injuries`, waivers and engine trades are skipped in a mirrored week. Depth charts come from the mirror for linked starters. |
| S9 | `step_staff` runs only in `STAFF_STAGES` (offseason, `pipeline/league_office.py:210`). `engines/coach_save_sync.py:104` `_wanted()` reads `coach_tenures` / `coordinator_tenures` covering the target season, latest start first, and its `plan_staff_edits` emits release / assign / create. Spike C (2026-11-10) showed a created head coach works in game (`pipeline/save_reader/staff.mjs:42`). | Coaching mirror = write the real changes into the tenure tables, then run `step_staff` in a mirrored REGULAR_WEEK too. |
| S10 | Tenures are season-granular: `coach_tenures(start_season, end_season)` (`db/init_db.sql:679-687`). | A firing during 2026 ends the old tenure at 2026, and the interim's tenure starts in 2026 with a higher id, so `_wanted` picks the interim. No schema change is needed for the tenure itself; `nfl_mirror_moves` records the week. |
| S11 | Each weekly ingest runs `run_weekly_engines` (GM proposals `step_gm`, `pipeline/weekly_capture.py:442`, `:700`), then the league office, then `step_articles`, then press conferences (`pipeline/watch_save.py:1418-1440`, §6.0b). | In a mirrored week the watcher skips `step_gm`, the articles and press conferences. The stats engines still run, because the games are still Madden's and they count. |
| S12 | `bootstrap_untracked_players` (`pipeline/league_office.py:2082`) seeds progression state for players Mu has never tracked, before progression plans. | Created players are picked up by progression with no new work. |
| S13 | `league_setup.mjs` already writes LeagueSetting fields (`Field_44` CoachFiring, `Field_82` career clock; `pipeline/save_reader/league_setup.mjs:29`). The Injuries on/off field is not known. | Spike A finds the field. It is then written with a `records` edit. |
| S14 | `contracts_engine.minimum_salary(accrued_seasons, cap)` (`engines/contracts_engine.py:126`); pyarrow 26 and pandas 3.1 are installed. | A midseason signing takes the real OTC terms when found, else the minimum for his years of service. |
| S15 | The latest migration is `2027_02_25_madden_stat_counters.sql`; no plan names `2027_02_26`. | Migration `2027_02_27_nfl_mirror.sql`; re-check at build time. |
| S16 | The pilot's `sim_season` job sims only `CAREER-TUNE-*` saves and refuses while the watcher runs (`tools/madden_pilot/jobs.py:18-21`, `:767-771`). It has `_sim_one_week` (`:501`), a save-header week reader (`_save_week`, `:416`), and `quit_franchise` with `allow_quit` (`navigate.py:254`). The league office replaces the save only while the franchise is closed (§2.21a). | D8 needs a separate job, `catch-up`, that does the opposite: the real franchise, with the watcher running. Each week it loads the franchise, sims one week, saves, quits to the main menu, waits for the league office's WRITTEN run for the next week, and reloads. The TUNE-only rule on `sim_season` stays as it is. |

## 1. Scope

**In:**
- Fetching the real week's rosters, injury report, depth chart, per-game head coach (`games.csv`) and OTC contracts, cached with sha256 receipts.
- Planning one week's moves from the save snapshot and the real week: release, sign (roster or practice squad), trade (from the hand CSV), IR on/off, injury fields, depth charts, and creation of missing active and IR players.
- Copying real injuries into catch-up weeks with their real return dates (D7). Live weeks follow Q1.
- Turning Madden's injuries off at the first mirrored write and back on at the first LIVE week's write (D10).
- Creating missing active and IR players from EA's Madden 27 ratings, falling back to position averages by role (D9).
- The pilot's `catch-up` job (D8).
- Mirroring head-coach changes from `games.csv` and coordinator changes from the hand CSV into the tenure tables and the save's staff block.
- Gating in the league office and the watcher: no engine trades, waivers, GM proposals, articles or press conferences in a mirrored week.
- CLI `status / preview / trades / enable / disable`, plus a markdown report for each week under `verify_output/nfl_mirror/`.

**Out (non-goals):**
- Real game results or real stats. Madden sims every game.
- Real ratings. Progression still owns them.
- Real draft-pick ownership in the save. Pick legs of real trades go into Mu's books only, as engine trades do today (§2.21a).
- Any season after 2026, including the 2027 real offseason (D1).
- Creating practice-squad-only players (D4).
- Articles or coverage of mirrored weeks (a V3 batch job, V3_STUDIO_PLAN §3).
- Letting the pilot sim the real franchise outside catch-up. Live weeks are simmed as JR does now.
- Updating the ratings of players already in the save from EA's weekly ratings. Progression owns them.

## 2. Design

### 2.1 When a week is mirrored

`nfl_mirror_settings` holds one row: `enabled BOOLEAN`, `season INT` (2026), `last_week INT NULL` (NULL = through the Super Bowl), `madden_injuries_field JSONB NULL` (Spike A's `{"table": "LeagueSetting", "tableId": …, "row": 0, "field": "Field_N", "off": v, "on": v}`), `injuries_state VARCHAR(8)` (`ON` / `OFF`, which the league office last wrote).

`store.mirror_active(cur, season, stage, week) -> bool` is true when `enabled`, `season` matches, the stage is REGULAR_WEEK or PLAYOFF_WEEK, and `week <= last_week` (if set). Mu's week numbers are used (`league_week`, playoffs 19/20/21/23 as in `PRO_BOWL_WEEK`, `pipeline/league_office.py:217`). `real_week_for(mu_week)` maps 19→WC, 20→DIV, 21→CON, 23→SB, which in nflverse are weeks 19–22.

The save at Madden's next unplayed week `w` gets the real state of week `w`. Mode: `CATCH_UP` when every real game of week `w` is final in `games.csv`; otherwise `LIVE`, using the newest data available. The first LIVE week is when Madden's injuries come back on (D10). Which real week that is depends on the day the franchise starts. On 2026-11-11 nflverse's newest week is 10, with games on 2026-11-12 to 11-16, so a start today would catch up on weeks 1–9 and go live at week 10.

### 2.2 Real week (`engines/nfl_mirror/real_state.py`, pure)

```python
@dataclass(frozen=True)
class RealPlayer:  gsis_id: str; name: str; position: str; team: str; status: str   # ACTIVE|INACTIVE|PS|IR|PUP|NFI|SUSP
                   designation: str; birth_date: str|None; college: str|None; height_in: int|None; weight_lbs: int|None
                   entry_year: int|None; draft_number: int|None; jersey: int|None
@dataclass(frozen=True)
class RealInjury:  gsis_id: str; team: str; report_status: str; body_part: str|None; practice_status: str|None
@dataclass(frozen=True)
class RealWeek:    season: int; week: int; mode: str; rosters: dict[str, list[RealPlayer]]   # team -> players
                   injuries: dict[str, RealInjury]; depth: dict[str, dict[str, list[str]]]  # team -> pos -> gsis ids
                   head_coach: dict[str, str]; carried_forward: tuple[str, ...]; sources: dict[str, str]  # name -> sha256
def build_week(rows_rosters, rows_injuries, rows_depth, rows_games, season, week) -> RealWeek
```

`STATUS_MAP` maps `(status, status_description_abbr)` → status: `A01`→ACTIVE, `I0x`→INACTIVE, `P0x`→PS, `R01`/`R48`→IR, `R04`→PUP, `R05`→NFI, anything else under RES→IR (logged). `CUT`, `RET` and `EXE` players are left off the team. A team with no rows for `week` reuses its last week's rows and is listed in `carried_forward`. Team codes go through `engines/nfl_history/teams.py` (LA→LAR; nflverse `ARI` vs the save's `AZ` is handled in the map).

`fetch.py` reuses `engines.nfl_history.fetch.get(url, cache_name, refresh=True)` for `weekly_rosters/roster_weekly_2026.csv.gz`, `injuries/injuries_2026.csv.gz`, `depth_charts/depth_charts_2026.csv.gz`, `games.csv` and `contracts/historical_contracts.parquet`. `fetch_week(season, refresh) -> dict[name, FetchedFile]`.

### 2.3 Trades (`engines/nfl_mirror/trades.py`)

`data/history/nfl_trades_2026.csv` columns: `trade_id, date, week, from_team, to_team, gsis_id, player_name, note`. There is one row per player leg; pick legs go in `note`. JR or a session adds rows from news. `read_trades(path) -> list[TradeLeg]`. `infer_switches(prev_week, week) -> list[Switch]` lists every team-to-team move between consecutive weeks. A switch whose `(gsis_id, to_team, week)` has no CSV row is planned as a release plus a sign and listed in the weekly report as "unconfirmed: trade or claim?". CLI `trades --week N` prints them.

### 2.4 Store (`engines/nfl_mirror/store.py`) and migration

`2027_02_27_nfl_mirror.sql`, mirrored at the end of `db/init_db.sql`:
- `nfl_mirror_settings` as in §2.1 (one row, `id SMALLINT PRIMARY KEY CHECK (id = 1)`).
- `nfl_mirror_weeks` (`season, week` PK; `real_week INT`, `mode VARCHAR(8)` CATCH_UP/LIVE, `sources JSONB`, `counts JSONB`, `run_id INT REFERENCES league_office_runs(id)`, `status VARCHAR(10)` PLANNED/WRITTEN/FAILED, `report TEXT`, `created_at`).
- `nfl_mirror_moves` (`id SERIAL`, `season, week`, `kind VARCHAR(10)` CHECK IN `SIGN, RELEASE, TRADE, PS, IR, ACTIVATE, INJURY, HEAL, CREATE, CHART, STAFF, SETTING`, `gsis_id`, `player_id INT REFERENCES players(id) ON DELETE SET NULL`, `pgid INT`, `from_team_id`, `to_team_id`, `detail JSONB`, `status VARCHAR(10)` PLANNED/WRITTEN/SKIPPED/REFUSED, `reason TEXT`).
- `ALTER TABLE nfl_player_links` drops and re-adds the `method` CHECK with `CREATED` added.

Functions: `read_settings(cur)`, `set_enabled(cur, enabled, season=2026, last_week=None)`, `mirror_active(cur, season, stage, week)`, `record_week(cur, season, week, **fields)`, `record_moves(cur, season, week, moves)`, `mark_written(cur, run_id)`, `links(cur) -> {gsis_id: pgid}`, `add_created_link(cur, player_id, gsis_id, evidence)`.

### 2.5 Roster diff (`engines/nfl_mirror/diff.py`, `contracts.py`, pure)

```python
def plan_rosters(snapshot, real: RealWeek, links: dict[str, int], trades: list[TradeLeg], team_index: dict[str, int],
                 contracts: ContractBook, cap: int) -> MirrorRosterPlan
# MirrorRosterPlan: ops: list[dict]   # {"kind": "release"|"sign"|"trade"|"ir"|"activate", "pgid", "gsis_id",
#                                       "to_index", "list": "Roster"|"PracticeSquad", "contract": {...}|None, "why"}
#                   creates: list[RealPlayer]    # active/IR real players with no link and no free Madden match
#                   ps_fill: list[dict]          # {"team_index", "position"}: PS-only real players skipped (D4)
#                   unlinked_new: list[tuple[str, int]]   # (gsis_id, pgid) new links found this week
```

Order of operations: first new links, by running `link.match` over unlinked real players against unlinked Madden players (free agents included). Then trades from the CSV. Then releases (a Madden player on a team the real week doesn't place him on). Then signings, practice-squad moves, IR on and IR off. `ps_fill` signs the best free agent at the position by OVR (`Deps.free_agent_pool` order). `contracts.py`: `ContractBook.from_otc(parquet_path)`, then `contract_for(gsis_id, signed_date, cap) -> dict` (real APY and years when OTC has a deal signed on or after 2026-08-01 for that team, else `minimum_salary(years_exp, cap)` for one year), returned in `league_office.offer_contract` shape.

### 2.6 Injuries and depth charts (`engines/nfl_mirror/injuries.py`, `depth.py`, pure)

`weeks_until_back(gsis_id, week, later_weeks) -> int` is used in CATCH_UP mode (D7). It counts the weeks until the player is next ACTIVE with no Out or Doubtful report, read from the later weeks' rosters and injury reports, and is capped at the weeks left in the regular season. `estimate_weeks(injury: RealInjury, status: str, history: list[str]) -> int` is used in LIVE mode, for whatever Q1 copies: IR and R48 give 4 (the real IR minimum), PUP and NFI give 4, Out gives 1 (2 when he was Out on the previous report too), Doubtful gives 1, Questionable gives 0. `injury_fields(weeks, body_part, enums) -> dict` sets `InjuryStatus`, `InjuryType` (`BODY_PART_TO_TYPE`, Spike B's enum list, else the generic type), `InjurySeverity`, `Min/MaxInjuryDuration = weeks`. A player hurt in Madden but healthy in reality gets `HEAL_FIELDS` (`HEALTHY` values from `pipeline/league_office.py:224`).

`depth.real_chart(real.depth[team], links, madden_roster) -> {slot: [pgid…]}` maps nflverse positions to Madden's 35 depth slots (`DEPTH_SLOTS` table: QB, HB, FB, WR, TE, LT/LG/C/RG/RT, LE/RE/DT, LOLB/MLB/ROLB, CB/FS/SS, K, P; return and special slots left to the engine). An injured, IR or unlinked player is dropped. The league office gives the engine's `staff_depth_chart` result for every slot, or tail of a slot, that `real_chart` leaves empty (`depth.merge(real, engine)`).

### 2.7 Coaching (`engines/nfl_mirror/staff.py`)

`real_staff_changes(real_week, prev_week, staff_csv) -> list[StaffChange]`. A head coach comes from the `games.csv` per-game coach, which changes on a firing. OC/DC changes come from `data/history/nfl_staff_changes_2026.csv` (`date, week, team, role, out_name, in_name, interim, note`). `apply_staff_changes(cur, season, changes) -> list[dict]` sets `end_season = season` on the outgoing tenure. It inserts the incoming person, matched by name to `coaches` / `coordinators` or created with a name-only row (`coach_madden_profile.build_profile` supplies the Madden profile, as the carousel does), and a tenure with `start_season = season`. It records STAFF moves. It never touches a team in the registry's `user_teams`, and there are none in 2026.

### 2.8 Creating players (`engines/nfl_mirror/create.py`)

`ea_ratings.py`:
- `fetch_iteration(refresh=False) -> FetchedFile` downloads every page of `https://www.ea.com/games/madden-nfl/ratings?page=N` until a page adds no new ids. It sends a generic User-Agent, waits 1.5 s between pages, and caches `data/history/.cache/ea_m27_<iteration>.json` with its sha256. The iteration id is read from the page.
- `index(items) -> dict[key, item]` keys each item by `normalize_name` plus birthdate.
- `find(real: RealPlayer, idx) -> item | None` matches on name and birthdate, or on name alone when EA has no birthdate and the position group agrees.
- `madden_fields(item) -> {"position", "archetype", "ratings": {SaveField: value}}` maps EA's 55 camelCase stat keys to the save's `<Name>Rating` fields (`EA_TO_SAVE`, e.g. `bCVision` → `BCVisionRating`, `speed` → `SpeedRating`), and EA's position codes (`LEDG`, `MIKE`, …) through `pipeline/load_save.POSITIONS` reversed. An EA key with no save field is listed in the report, never guessed.

`create.py`:
- `pick_donors(snapshot, links, n, position) -> list[pgid]` takes free agents with no real link (or linked to a person with no 2025–2026 roster row), lowest OVR first, never `Draft` status.
- `created_edit(donor, real: RealPlayer, ea_item | None, template) -> dict` calls `player_edit(donor_row, target, new_name=real.name, commentary_id=commentary_names lookup)` with real height, weight, age (from birth date), college and jersey. Ratings, position and archetype come from `ea_ratings.madden_fields` when EA has the player (D9; source `EA`). Otherwise they come from `ROLE_TEMPLATE` (source `TEMPLATE`): the median Madden player at the position for a real starter by depth chart, the 25th percentile for a backup, and the 10th for IR.
- The edit rides the same write, and the pgid stays the donor's. `add_created_link` stores `CREATED` with the ratings source in `evidence`.

EA's newest iteration (week 8 on 2026-11-11) lags the real week, so a player signed after it falls back to the template. The mirror never re-rates a player once he is created.

### 2.9 League office wiring (`pipeline/league_office.py`, `pipeline/league_office_mirror.py`)

`PRIORITY_MIRROR = PRIORITY_ENGINE_TRADE`. In `plan_stage`, when `deps.mirror_week(cur, season, stage, week)` returns a `MirrorWeek` (not None):
- The "engine trades" step is replaced by `step_nfl_mirror(plan, mirror)`. It applies `creates` (players edits), then `ops` through `plan.release_player / sign_player / trade_player / set_fields` at `PRIORITY_MIRROR` with tag `mirror:<kind>:<gsis_id>`. Next come the injury and heal fields and IR flags, then `ps_fill`, and finally the setting record (injuries OFF on the first mirrored write).
- Waivers are skipped in every mirrored week, and `step_injuries` in CATCH_UP weeks. Drift still runs, and players the mirror has claimed are protected by first-claim-stands.
- `step_coach_charts` gets `deps.chart_override(team_index)` = `depth.merge(...)`. Source `MU_COACH` stays.
- `step_staff(plan, cur, snapshot)` also runs in a mirrored REGULAR_WEEK when `mirror.staff_changed`.
- `step_legality` still runs. Mirror releases normally leave each team at 53 + IR, under 63.
- `record_written` calls `store.mark_written(cur, run_id)` and writes one `transactions` row per SIGN / RELEASE / TRADE move (`raw_text = 'NFL mirror'`, `parse_ok = true`, `row_key = 'mirror:<season>:<week>:<gsis_id>:<kind>'`).

At the first run whose mirror week is LIVE, or whose `mirror_active` is false, while `injuries_state = 'OFF'`, a records edit writes the field's `on` value and sets `injuries_state = 'ON'` (D10). In LIVE weeks `step_injuries` runs again as normal for Madden's own injuries. The mirror then copies only what Q1 decides.

`pipeline/league_office_mirror.py`: `mirror_week(cur, snapshot, season, stage, week) -> MirrorWeek | None` does the fetch, build, diff, estimate and staff work, and records PLANNED rows. `Deps.mirror_week` returns None by default; `default_deps` wires the real adapter. A fetch failure in LIVE mode is a plan error, so nothing is written. JR re-runs once nflverse answers.

### 2.10 Watcher gating (`pipeline/watch_save.py`, `pipeline/weekly_capture.py`)

`ingest_snapshot` reads `store.mirror_active` for the save's (season, stage, week). When true, `run_weekly_engines(..., gm=False)` skips `step_gm`, and the articles and press conferences are skipped with the log line "NFL mirror week: no articles". The notification adds `mirror: N moves (M unconfirmed switches)`.

### 2.11 CLI (`engines/nfl_mirror/__main__.py`)

- `status` prints the settings, the last mirrored week and the next real week's mode.
- `preview --week N [--dsn]` runs fetch, build and diff against the newest snapshot and prints the plan, writing nothing.
- `trades --week N` prints the unconfirmed switches.
- `enable [--last-week N]` and `disable` each need `--confirm <db>`.

### 2.12 Pilot catch-up job (`tools/madden_pilot/jobs.py`, `tools/madden_pilot/__main__.py`)

`python3 -m tools.madden_pilot catch-up --save CAREER-<name> [--to-week N] [--dsn ...]` runs the job `catch_up(pilot, save, to_week=None, dsn=None, progress=None)`. Before touching the PC it refuses in these cases:
- the save is a `CAREER-TUNE-*` clone;
- the watcher isn't running (the reverse of `sim_season`), since the league office write between weeks comes from it;
- `nfl_mirror_settings.enabled` is false;
- the save's next week (`_save_week`) is not CATCH_UP for `real_state`.

Each loop:
1. Load the franchise.
2. `_sim_one_week`.
3. Save, and `quit_franchise(allow_quit=True)` to the main menu so the franchise is closed.
4. Wait, up to `CATCH_UP_WRITE_TIMEOUT` (15 min), for `nfl_mirror_weeks` to show WRITTEN for the next week.
5. Repeat.

It stops at the first LIVE week (or `--to-week`), on a FAILED mirror week, or on any refusal. On stopping it sends one ntfy line per week and a summary. The `/madden` skill text gains the job and its rule: real franchise only during catch-up, under `CLAIM · Madden PC`.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Where the Injuries on/off switch lives in the save | Spike A | If no save field exists: the injury slider at 0 (it is in the settings Madden shows) plus R2's fallback |
| R2 | Madden may ignore or clear a written injury while its injuries are off | Spike B | Fallback: keep the IR flag, and leave Out players off the depth chart (`depth.merge` already drops them), so they don't play |
| R3 | `InjuryType` / `InjurySeverity` enum names | Spike B reads them from the schema | Generic type for every injury |
| R4 | A reused record may show the donor's face or name in game, or crash | Spike C | Treat creation as off: those slots get the best Madden free agent at the position (`ps_fill` logic) |
| R4a | EA's ratings page changes shape or stops serving Madden 27 | `fetch_iteration` checks `totalItems` and the stat keys and fails loudly | Template ratings for every created player; already-created players keep theirs |
| R4b | Name collisions in EA's data (e.g. Zach Bako-Bewele came back as OVR 87, which looks wrong) | `find` requires birthdate agreement when both sides have one; the weekly report lists every EA match with OVR and position | Fall back to the template for doubtful matches |
| R4c | The pilot's catch-up loop sims a week twice or loads the wrong save | It reads the save-header week before and after each sim and refuses when it didn't advance by exactly one; the watcher's `same_week` check never re-ingests | The weekly freeze (`pipeline/weekly_freeze.py restore`) rolls the save and DB back together |
| R5 | A team change is wrongly read as a trade, or the reverse | The hand CSV is the authority; the report lists unconfirmed switches | Release plus sign is safe; only the `transactions` type differs |
| R6 | nflverse lags on Wednesday moves in a LIVE week | `sources` sha256 in `nfl_mirror_weeks`; the next week catches up | A move lands a week late |
| R7 | Real contracts against Madden's cap fields | `CAP_ROOM_RANGE` allows negatives (`pipeline/league_office.py:233`); `CAPSALARY_RANGE` clamps | A few teams show over the cap in Madden until the offseason |
| R8 | Sim standings differ from reality, so real trades look odd | Accepted (D1) | — |
| R9 | Catch-up length depends on when the franchise starts (still held until V2 and tuning) | Known at start | All weeks are CATCH_UP if it starts after the season ends; the design is the same |

**Spike first (before wave 2, JR at the Madden PC under `CLAIM · Madden PC`, about 1 hour, on a `CAREER-TUNE-*` clone):**
- **A.** Toggle franchise Injuries off in League Settings, save, and diff the two snapshots' LeagueSetting rows to name the field and its values.
- **B.** With injuries off, write an Out injury (3 weeks) on a healthy starter and a heal on an injured player. Sim one week and check he sits and the other plays. Dump the `InjuryType` enum.
- **C.** Rewrite one free-agent donor with `player_edit` as a made-up player, sign him and play-sim a week. Check the roster card name, commentary and no crash.

The results go into §0 and `madden_injuries_field`. Wave 1 doesn't depend on them.

## 4. Tests (synthetic, no live DB)

- `tests/test_nfl_mirror_real_state.py`: status map, bye-week carry forward, team code map, playoff week mapping, CATCH_UP vs LIVE from games.
- `tests/test_nfl_mirror_trades.py`: CSV read, switch inference, unconfirmed list.
- `tests/test_nfl_mirror_store.py`: `mirror_active` cases (off, wrong season, offseason stage, past `last_week`); move recording; `CREATED` link; an integration test applies the migration twice on a `mu_scratch_*` database.
- `tests/test_nfl_mirror_staff.py`: HC change from per-game coaches, OC/DC from the CSV, tenure end/start, JR team skipped, a name-only person created once.
- `tests/test_nfl_mirror_diff.py`: release / sign / trade / PS / IR ops on a toy snapshot; CSV trade beats inference; PS-only missing player goes to `ps_fill`; active missing player goes to `creates`; incremental link; OTC contract vs minimum.
- `tests/test_nfl_mirror_injuries.py`: `weeks_until_back` in CATCH_UP (back next week, back after IR, never back, capped), the LIVE estimate table, heal fields, `real_chart` slot mapping, injured players dropped, `merge` fills tails.
- `tests/test_nfl_mirror_create.py`: EA page parsing from a fixture `__NEXT_DATA__`, pagination stop, `find` with and without a birthdate, `EA_TO_SAVE` covering every fixture key, EA position to Madden position; donor choice (never Draft, never a linked active person); EA ratings used when found, the template otherwise; `CREATED` link with its source.
- `tests/test_league_office_mirror.py`: a CATCH_UP REGULAR_WEEK skips engine trades, waivers and `step_injuries`; mirror claims beat drift; injuries OFF on the first write and ON at the first LIVE week; `step_injuries` back in LIVE weeks; staff runs in season only when changed; transactions rows; a non-mirrored week is byte-for-byte unchanged against today's plan.
- `tests/test_madden_pilot_catch_up.py`: refusals (TUNE save, no watcher, mirror off, LIVE week), one loop with a fake pilot and a fake store, stop at the first LIVE week, stop on FAILED, the week-advanced-by-one check.
- `tests/test_watch_save_mirror.py`: a mirrored week skips GM proposals, articles and press; a normal week doesn't.

Run with `pytest -m "not integration" tests/test_nfl_mirror_*.py tests/test_league_office_mirror.py tests/test_league_office.py tests/test_watch_save_mirror.py tests/test_madden_pilot_catch_up.py`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Real week source

- **Owns:** `engines/nfl_mirror/__init__.py` (new), `engines/nfl_mirror/fetch.py` (new), `engines/nfl_mirror/real_state.py` (new), `engines/nfl_mirror/trades.py` (new), `data/history/nfl_trades_2026.csv` (new, header plus the trades JR has named), `tests/test_nfl_mirror_real_state.py` (new), `tests/test_nfl_mirror_trades.py` (new)
- **Reads:** `engines/nfl_history/fetch.py`, `engines/nfl_history/teams.py`, §2.1–2.3
- **Depends on:** none
- **Done when:** `build_week` returns a `RealWeek` from fixture rows with carry-forward and modes; `infer_switches` lists unconfirmed moves.
- **Verify:** `pytest -m "not integration" tests/test_nfl_mirror_real_state.py tests/test_nfl_mirror_trades.py` passes.

### U2: Store, migration and coaching

- **Owns:** `db/migrations/2027_02_27_nfl_mirror.sql` (new), `db/init_db.sql`, `engines/nfl_mirror/store.py` (new), `engines/nfl_mirror/staff.py` (new), `data/history/nfl_staff_changes_2026.csv` (new, header only), `tests/test_nfl_mirror_store.py` (new), `tests/test_nfl_mirror_staff.py` (new)
- **Reads:** `db/migrations/2027_02_04_nfl_history.sql`, `engines/coach_save_sync.py`, `engines/coach_madden_profile.py`, `engines/coaching_carousel.py`, §2.4, §2.7
- **Depends on:** none. `staff.py` takes plain `{team: head_coach}` dicts per week, the shape of `RealWeek.head_coach`.
- **Done when:** the migration applies twice cleanly on a scratch DB; `mirror_active` and the tenure changes behave as §4 says.
- **Verify:** `pytest -m "not integration" tests/test_nfl_mirror_store.py tests/test_nfl_mirror_staff.py` passes.

### U3: Roster diff and contracts

- **Owns:** `engines/nfl_mirror/diff.py` (new), `engines/nfl_mirror/contracts.py` (new), `tests/test_nfl_mirror_diff.py` (new)
- **Reads:** `engines/nfl_history/link.py`, `pipeline/league_office.py` (`offer_contract`, `Plan`), `engines/contracts_engine.py`, §2.5
- **Depends on:** U1
- **Done when:** `plan_rosters` gives the expected ops, creates and `ps_fill` on the toy snapshot.
- **Verify:** `pytest -m "not integration" tests/test_nfl_mirror_diff.py` passes.

### U4: Injuries and depth charts

- **Owns:** `engines/nfl_mirror/injuries.py` (new), `engines/nfl_mirror/depth.py` (new), `tests/test_nfl_mirror_injuries.py` (new)
- **Reads:** `pipeline/league_office.py:388-395, 1502-1526`, `engines/depth_chart_engine.py`, Spike B results, §2.6
- **Depends on:** U1
- **Done when:** estimates, heal fields and merged charts match §4.
- **Verify:** `pytest -m "not integration" tests/test_nfl_mirror_injuries.py` passes.

### U5: Creating players (EA ratings first)

- **Owns:** `engines/nfl_mirror/ea_ratings.py` (new), `engines/nfl_mirror/create.py` (new), `tests/test_nfl_mirror_create.py` (new), `tests/fixtures/ea_ratings_page.json` (new)
- **Reads:** `pipeline/madden_player_edit.py`, `pipeline/commentary_names.py`, `pipeline/load_save.py` (`POSITIONS`), a snapshot's `ratings` keys, Spike C results, §2.8
- **Depends on:** U1, U2
- **Done when:** `created_edit` builds a valid players edit from an EA item or a template; `pick_donors` follows its rules; `EA_TO_SAVE` maps every EA stat key in the fixture.
- **Verify:** `pytest -m "not integration" tests/test_nfl_mirror_create.py` passes.

### U6: League office wiring

- **Owns:** `pipeline/league_office.py`, `pipeline/league_office_mirror.py` (new), `tests/test_league_office_mirror.py` (new)
- **Reads:** §2.9, the U1–U5 modules
- **Depends on:** U1, U2, U3, U4, U5
- **Done when:** a mirrored week plans as §2.9 says, and a non-mirrored week's plan is unchanged.
- **Verify:** `pytest -m "not integration" tests/test_league_office_mirror.py tests/test_league_office.py tests/test_league_office_personality.py tests/test_league_office_staff.py` passes.

### U7: Watcher gating and CLI

- **Owns:** `pipeline/watch_save.py`, `pipeline/weekly_capture.py`, `engines/nfl_mirror/__main__.py` (new), `tests/test_watch_save_mirror.py` (new)
- **Reads:** §2.10–2.11, `engines/nfl_mirror/store.py`, `pipeline/league_office_mirror.py`
- **Depends on:** U2, U6
- **Done when:** gating works as §4 says; `python3 -m engines.nfl_mirror --help` lists the five commands.
- **Verify:** `pytest -m "not integration" tests/test_watch_save_mirror.py` passes, and `python3 -m engines.nfl_mirror --help` exits 0.

### U8: Pilot catch-up job

- **Owns:** `tools/madden_pilot/jobs.py`, `tools/madden_pilot/__main__.py`, `.claude/skills/madden/SKILL.md`, `docs/reference/MADDEN_PILOT.md`, `tests/test_madden_pilot_catch_up.py` (new)
- **Reads:** `tools/madden_pilot/navigate.py`, `engines/nfl_mirror/store.py`, `engines/nfl_mirror/real_state.py`, §2.12
- **Depends on:** U1, U2
- **Done when:** `catch-up` refuses as §2.12 says and loops correctly with fakes; the skill and pilot doc describe it.
- **Verify:** `pytest -m "not integration" tests/test_madden_pilot_catch_up.py` passes; `python3 -m tools.madden_pilot catch-up --help` exits 0.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2 | — | No shared files; U2's staff code takes plain dicts |
| 2 | U3, U4, U5 | U1 (U5 also U2); Spikes A–C done | All pure modules on `RealWeek`; no shared files |
| 3 | U6 | U1–U5 | Wires everything into `plan_stage` |
| 4 | U7, U8 | U2, U6 (U8: U1, U2) | No shared files; U8 is ready after wave 1 but waits so the first real catch-up runs on the finished mirror |

**File ownership check:**

| File | Unit |
|---|---|
| `.claude/skills/madden/SKILL.md` | U8 |
| `data/history/nfl_staff_changes_2026.csv` | U2 |
| `data/history/nfl_trades_2026.csv` | U1 |
| `db/init_db.sql` | U2 |
| `db/migrations/2027_02_27_nfl_mirror.sql` | U2 |
| `docs/reference/MADDEN_PILOT.md` | U8 |
| `engines/nfl_mirror/__init__.py` | U1 |
| `engines/nfl_mirror/__main__.py` | U7 |
| `engines/nfl_mirror/contracts.py` | U3 |
| `engines/nfl_mirror/create.py` | U5 |
| `engines/nfl_mirror/depth.py` | U4 |
| `engines/nfl_mirror/diff.py` | U3 |
| `engines/nfl_mirror/ea_ratings.py` | U5 |
| `engines/nfl_mirror/fetch.py` | U1 |
| `engines/nfl_mirror/injuries.py` | U4 |
| `engines/nfl_mirror/real_state.py` | U1 |
| `engines/nfl_mirror/staff.py` | U2 |
| `engines/nfl_mirror/store.py` | U2 |
| `engines/nfl_mirror/trades.py` | U1 |
| `pipeline/league_office.py` | U6 |
| `pipeline/league_office_mirror.py` | U6 |
| `pipeline/watch_save.py` | U7 |
| `pipeline/weekly_capture.py` | U7 |
| `tools/madden_pilot/__main__.py` | U8 |
| `tools/madden_pilot/jobs.py` | U8 |
| `tests/fixtures/ea_ratings_page.json` | U5 |
| `tests/test_league_office_mirror.py` | U6 |
| `tests/test_madden_pilot_catch_up.py` | U8 |
| `tests/test_nfl_mirror_create.py` | U5 |
| `tests/test_nfl_mirror_diff.py` | U3 |
| `tests/test_nfl_mirror_injuries.py` | U4 |
| `tests/test_nfl_mirror_real_state.py` | U1 |
| `tests/test_nfl_mirror_staff.py` | U2 |
| `tests/test_nfl_mirror_store.py` | U2 |
| `tests/test_nfl_mirror_trades.py` | U1 |
| `tests/test_watch_save_mirror.py` | U7 |

No path appears twice.

## 7. Spec changes when built

- §1 Current direction: the 2026 season mirrors the real NFL week by week (catch-up then live), with Mu's engines from the 2027 offseason.
- New §2.21c NFL Weekly Mirror: sources, modes, diff order, injury estimates, creation, coaching, settings and CLI.
- §2.21a League Office: the mirrored-week branch of `plan_stage`, `PRIORITY_MIRROR`, the injuries setting record, `step_staff` in season, transactions rows.
- §3.3x: `nfl_player_links.method` gains `CREATED`. New §3.3 subsection for the three `nfl_mirror_*` tables.
- §6.0b: mirrored-week gating (no GM proposals, articles or press) and the notification line.
- §1.2 Running the System: `python3 -m engines.nfl_mirror` commands and `python3 -m tools.madden_pilot catch-up`.
- Repository Layout: `engines/nfl_mirror/` and its files (with `ea_ratings.py`), `pipeline/league_office_mirror.py`, the migration, both CSVs, the EA cache file pattern and the new tests; the pilot line gains the `catch-up` job.

## 8. Decisions for JR

Settled on 2026-11-11 (D7–D12): real return dates in catch-up; the pilot sims catch-up; EA ratings first, then position averages; Madden injuries back on at the first live week; mirrored weeks' stats count.

| # | Question | Recommendation |
|---|---|---|
| Q1 | Once Madden's injuries are back on (live weeks), what real injury news do we still copy? | **Decided (D12):** only IR, PUP and NFI placements and activations; no weekly reports; no healing of Madden's injuries. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | worktree-agent-a4c4046ed309775c0 | .claude/worktrees/agent-a4c4046ed309775c0 | merged (42 tests) | 534fb66 |
| U2 | 1 | worktree-agent-a113b2696c3f27a19 | .claude/worktrees/agent-a113b2696c3f27a19 | merged (38 tests + integration on mu_scratch) | 6953da6 |
