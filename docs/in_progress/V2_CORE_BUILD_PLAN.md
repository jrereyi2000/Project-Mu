# Project Mu: V2 Core Build Plan

**Status:** approved by JR (2026-10-30); building, wave 2 of 6 done.
- *As built (wave 1):* U2's `sign` to a Roster needs a PlayerContract row that no schema field references, and the sample save has none (its one unreferenced row is held by a PersonaNegotiationPackage). Today a Roster signing works only when a release in the same write frees a row. Free agency signs more players than it releases, so creating contract rows (the library can un-empty a table row) is a follow-up to settle with spike R1 before U11/U13 rely on it.
- *As built (wave 1):* practice-squad players have no contract row and stay listed in `League.FreeAgents` (sample save), so a PracticeSquad signing links no contract and leaves the free-agent list alone.
- *As built (wave 1):* U3 maps offseason stages to league_state weeks in `contracts_engine.OFFSEASON_STAGE_WEEKS` (OFFSEASON_START 24, RESIGN 25, FREE_AGENCY 26, DRAFT 29, UDFA 30, CUTDOWN 31, PRESEASON 32; estimates). U13's `detect_stage` must use the same mapping.
- *Spikes R1, R5, R6 (2026-10-30, `CAREER-V2TEST` from `CAREER-WEEK1`: release Simpkins, sign Tyreek Hill to CAR on a reused contract row, trade Jalen Coker CAR→ATL, CAR depth chart King/Young/Pickett at QB and Hill WR1; JR loaded it, advanced a week, saved):* **R1 passed:** the signing, the reused contract row (2152) and the trade all survived load and Advance Week unchanged. **R5 passed:** Madden kept the written depth chart through the advance (no re-sort). **R6:** Madden did not recompute stored cap figures: CAR's `TEAM_SALARY`, `SalCapCapRoom`, `SalCapNextYearCapRoom`, reserves and Hill's `PLYR_CAPSALARY` (5189, from his old deal) were unchanged after the advance. So U13 writes them itself in every run: each moved player's `PLYR_CAPSALARY` (players edit) and each affected team's cap fields (records edit on the Team row), computed with `contracts_engine`. No writer change is needed.
- *As built (wave 2):* the save's 35 DepthChart field names (U6 `POSITION_SLOTS`) differ from V2_MASTER_PLAN §2.2's labels: slot WR is `SLWR`, rush ends `RLE`/`RRE`, rush DT `RDT`, slot CB `SLCB`, sub LB `SUBLB`, plus `NT`, `PWHB`, `GAD`, `KOS`. Every unit uses the save's names.
- *As built (wave 2):* U4's snapshot adds `players[].confidence` and `players[].traits` ({PT_*: bool}, nested) and a `field` key on each slider entry; LeagueSetting references decode as plain 32-bit ints under schema 620 (`lib.mjs refFromValue`).
- *As built (wave 2):* U5's `run_wave` doesn't check cap or roster limits (offers carry `team` and `market` context); U11 `cpu_offers` must. `acceptance` takes `best = {offer, team, market, score?, bar_shift?}`; street/UDFA markets take `needs = {team_id: {needs, starter_ovr?, roster_spots?, …}}`.
- *As built (wave 1):* U1's agent had no database (Docker was down); the orchestrator ran its checks (migration applied twice to `madden_test`, `init_db.sql` built on an empty database).
**Builds on:** [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §2–§5, §7; [`PROGRESSION_PLAN.md`](../in_progress/PROGRESSION_PLAN.md) phases C2, D, E; [`LEAGUE_OFFICE_PLAN.md`](../done/LEAGUE_OFFICE_PLAN.md) (absorbed, D2); PROJECT_SPEC §2.17, §2.21, §3.3d, §3.3n, §6.0b.

**Goal:** Build the franchise operating system that has to exist before the canonical 2034 league starts. Project Mu decides progression, form, depth charts, contracts, free agency, cuts, waivers, the draft and retirements for all 32 teams, and writes them into the save. JR runs the Panthers from a `#franchise` page in `Mu_Network`. Madden only plays the games. The newsroom features and stretch goals are laid out as a roadmap (§9) and become their own plans later.

**Delivers:**
- Save writer moves `sign`, `trade` and `depthCharts` (`pipeline/save_reader/sign.mjs`, `trade.mjs`, `depth_chart.mjs`) next to `release`.
- Save reader blocks `depth_charts` and `league_settings` in the snapshot (reader_version 4).
- `engines/contracts_engine.py` (CBA rules, cap maths), `engines/free_agency_engine.py` (offer utility, seeded market waves), `engines/depth_chart_engine.py` ("Let Staff Set"), `engines/transactions_engine.py` (32-team re-signs, offers, cuts, waivers, practice squad, rookie deals).
- Progression phase C2 finished in `engines/progression.py`; phase D as `engines/progression_save.py`; phase E as `engines/form_engine.py`.
- `pipeline/league_office.py`: the stage loop that turns each save into Project Mu's decisions, writes them and reads them back.
- `pipeline/drift_check.py`: reports anything Madden changed that Project Mu didn't.
- `server/franchise_api.py` (`/api/franchise/*`) and `pipeline/game_launch.py` (the "Play Game" trigger).
- `pipeline/watch_save.py` running the league office on the post-advance save and writing next week's mod pack.
- `#franchise` page with the eight V2 modules (`Mu_Network/src/components/franchise/`).
- Migration `db/migrations/2027_01_14_v2_franchise_os.sql`.

**JR's answers (2026-10-30):**
- D1 Scope: the core gets units and waves; the newsroom features (V2.1–V2.12, V2.16) and the §8 stretch goal are a roadmap (§9).
- D2 `LEAGUE_OFFICE_PLAN.md` is absorbed: its areas are built here, and it moves to `docs/done/` as superseded once this plan is approved.
- D3 At most 3 subagents per wave.
- D4 Player and coach traits ship at 0: engines take trait inputs with neutral defaults; the trait systems (personality phases 4–6, progression F–G) stay in their own plans.
- D5 Active roster limit is 65 (the roster-size mod is installed); cut-down and roster checks use it.
- Earlier the same day: D13 (morale pinned at 50 every week; form is separate and carried by a ratings overlay); ingest runs on the save made after Advance Week (V2 §5).

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `writeSave` plans every move, refuses all if any edit is bad, applies, saves to a temp file, then reads the output back and fails on any mismatch (`pipeline/save_reader/save_writer.mjs:66-131`). Moves follow a plan/apply/verify triple (`release.mjs:107,197,208`). | New moves copy that triple and plug into `writeSave`. Only one unit may edit `save_writer.mjs` (U2). |
| S2 | `release.mjs:5-14` already maps the team lists: `Team.Roster` (100 slots), `Team.PracticeSquad` (12), and `Team.DepthChart` → 35 position fields, each a `Player[]` of 6 slots, checked on all 1,120 rows of the sample save. | V2 §7.0 P6 ("layout not yet read from a save") is already answered. The spec and V2 plan get corrected. `depth_chart.mjs` can rely on this layout. |
| S3 | `record_edits.mjs:63-113` writes plain fields of any table by table id and row, with schema checks and read-back. Today it wrote the slider records (`ProgressionXPSlider` row 1, `int[]` row 1) on `CAREER-WEEK1-AUTOSAVE`. | Sliders, `ConfidenceRating` and the dev-trait flag need no new writer code, only the right ids. The reader must report them (U4). |
| S4 | The snapshot has `meta`, `franchise`, `season`, `teams`, `games`, `players` and no depth charts or league settings (`pc_save_reader.mjs:260-275`). | U4 adds `depth_charts` and `league_settings`. |
| S5 | `LeagueSetting` decodes with generic `Field_N` names under schema 620. `Field_165`–`Field_168` point at progression by age, progression by position, regression by age, regression by position (checked on two saves, `PROGRESSION_PLAN.md` §2). `IsDevTraitRegressionEnabled` exists in the schema but its `Field_N` isn't identified. | Spike R3 finds the dev-trait field before U9. |
| S6 | Players' ratings, `PT_*` flags and `ConfidenceRating` are plain `Player` fields (written today through `save_writer.mjs` `players` edits; PT flags persisted in-game, 2026-10-30). Abilities aren't stored per player (`SignatureAbilitiesEval.PlayersGrantedSignatures` empty in four saves). | Progression writes ratings, OVR, dev trait and PT flags; abilities follow. |
| S7 | At the offseason start 47 players had `ContractStatus` `Retired`; by the 2027 preseason 59 were `Deleted`, and the Player table kept the same 3,246 rows (`CAREER-NOPROGRESSION` saves, 2026-10-30). | Retirement is a status change on a kept row, so un-retiring is a field edit made before Madden's `Deleted` step. Spike R4 pins which stage that is. |
| S8 | `engines/progression.py:714` `run_progression` and `:1271` CLI (`--season`, `--commit`, `--backtest`, `--simulate`, `--with-performance`, `--follow-madden`). Phase C2 built the rating groups and performance term; durability, mileage, ceiling bands, breakthroughs, upkeep, continuity, mentors and the PT model aren't built (`PROGRESSION_PLAN.md` phase table). | U7 finishes C2 inside `progression.py`. U9 writes its output to the save without editing `progression.py`. |
| S9 | `pipeline/load_save.py:743` `load_snapshot` loads players, attributes, games, lines, contracts (`:659`, whole dollars, `:270`), injuries; `:723` `current_week`. | U8 adds depth charts and the league-settings check. Money in the DB stays whole dollars; the writer converts to Madden's $10,000 units. |
| S10 | `pipeline/watch_save.py:282` `prepare_next_week_mod_pack` only logs the chosen pack; `mod_packs.write_pack` exists (`pipeline/mod_packs.py:206`). `:474` `process_save`, `:403` `ingest_snapshot`. | U16 writes the pack and calls the league office after ingest. |
| S11 | `pipeline/contracts.py` shapes cap rows and moves (`:40` `shape_cap`, `:57` `shape_contract`); `engines/trade_value.py:89` `worth(ovr, group, market)`, `:273` `player_value`; `engines/gm_negotiation.py:61` `judge`. | The contract and FA engines reuse market worth and player value instead of new money models. |
| S12 | Routers are included one per line in `server/api.py:144-148`; Draft Central already has a war room (`server/draft_api.py:575-669`). | U14 owns `server/api.py` for the one new include. The Draft War Room module reuses `/api/draft/war-room`. |
| S13 | `Mu_Network` routes on the hash in `App.tsx:56-190`, and nav links live in `App.tsx`. Components are grouped by area under `src/components/`. | One unit (U17) owns `App.tsx`; modules are standalone components under `components/franchise/`. |
| S14 | New tables are mirrored into `db/init_db.sql` (e.g. `player_progression_history` at `init_db.sql:1476`). Latest migration: `2027_01_13_franchise_identity.sql`. | One schema unit (U1) owns `init_db.sql` and the one new migration; every other unit uses its DDL. |
| S15 | `tests/conftest.py:21-96` blocks every database except `madden_test`. Node tests skip without a sample save (`test/release.test.mjs:15-16`, `MU_SAMPLE_SAVE`). | Python tests use fake cursors or `madden_test`; JS tests use the sample save and skip without it. |

## 1. Scope

**In:**
- Progression: phase C2's remaining terms, phase D (offseason pass written to the save, sliders and dev-trait flag set, drift check), phase E (weekly form overlay, morale pinned at 50).
- Writer moves: sign (free agent, draft prospect or practice squad), trade (players and picks between two teams), depth charts (all 35 positions, 6 slots).
- Reader and loader: depth charts and league settings from the save; drift report against Project Mu's last write.
- CBA contract engine: extension windows, 5th-year options, tags, RFA/ERFA tenders, restructures, cap hit, dead money, cut savings, rookie scale, minimums, yearly cap growth.
- Free agency engine: offer utility (money, role, contention, scheme, persona at 0), seeded offseason waves, in-season street free agents, undrafted rookies.
- League office for all 32 teams: re-sign and extend, CPU free-agent offers, cut-down, waivers, practice squad, rookie contracts, the whole draft from team boards, coach-set depth charts, retirements (snapshot, then un-retire whoever Project Mu keeps), injury durations, offseason progression, weekly form. Every write read back and logged.
- JR's front office: `/api/franchise/*` endpoints and the `#franchise` page (Game Center, Depth Chart, Roster & Cap, Free Agency, Contract Room, Trade Machine, Draft War Room, Training Room). JR's moves are queued and go through the same rules and writer as the CPU's.
- Lifecycle: the watcher runs ingest and the league office on the post-advance save, writes the next mod pack; "Play Game" writes pending moves, activates the pack and launches `MMCModManager.exe -launch <pack>`.

**Out (non-goals):**
- Newsroom and narrative features V2.1–V2.12 and V2.16 (roadmap §9).
- Player and coach trait systems (D4): engines get the inputs, set to neutral.
- League office phases G and H: media award and Hall of Fame ballots, owner personas, holdouts and trade requests (roadmap §9; they need the inbox and new persona types).
- Writing coaching-carousel changes into the save's coach tables (roadmap §9; creating coaches in the save needs its own spike).
- Player agents as newsroom personas (the engine uses a neutral agent; roadmap §9).
- O1 (linking this week's unlinked player lines ourselves), O2 (kickoff-window reveals), §8.1 boot straight into the game, P4 in-game portraits (roadmap §9).
- P5, re-running the save checks on a modded save: a manual check JR runs before the 2034 start, not code.
- Starting the canonical 2034 franchise.

## 2. Design

### 2.1 Schema (`db/migrations/2027_01_14_v2_franchise_os.sql`, mirrored in `db/init_db.sql`)

```sql
-- Depth charts as Madden holds them (MADDEN), as Project Mu's coaches set them (MU_COACH) or as JR set them (JR).
CREATE TABLE IF NOT EXISTS depth_charts (
    team_id INT NOT NULL REFERENCES teams(id),
    season INT NOT NULL,
    week INT NOT NULL,
    position_slot VARCHAR(8) NOT NULL,      -- Madden's DepthChart field name: QB, HB, WR, ..., KR, PR, 3DRB, ...
    depth SMALLINT NOT NULL CHECK (depth BETWEEN 1 AND 6),
    player_id INT REFERENCES players(id),
    source VARCHAR(8) NOT NULL CHECK (source IN ('MADDEN','MU_COACH','JR')),
    PRIMARY KEY (team_id, season, week, source, position_slot, depth)
);

-- Contract instruments beyond Madden's plain deal.
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS contract_type VARCHAR(16) NOT NULL DEFAULT 'STANDARD'
    CHECK (contract_type IN ('STANDARD','ROOKIE','UDFA','FIFTH_YEAR_OPTION','FRANCHISE_EXCL','FRANCHISE_NONEXCL',
                             'TRANSITION','RFA_TENDER','ERFA_TENDER','MINIMUM'));
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS guaranteed BIGINT NOT NULL DEFAULT 0;
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS signed_season INT;
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS signed_week INT;
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS last_restructure_season INT;
ALTER TABLE player_contracts ADD COLUMN IF NOT EXISTS drafted_round SMALLINT;   -- NULL = undrafted

-- Every offer or proposal: free agency, re-signing, extension, tender, JR's or a CPU team's.
CREATE TABLE IF NOT EXISTS contract_offers (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL,
    stage VARCHAR(16) NOT NULL,             -- league_office stage name (2.6)
    wave SMALLINT,                          -- free-agency wave 1..3, NULL otherwise
    kind VARCHAR(12) NOT NULL CHECK (kind IN ('FREE_AGENT','RESIGN','EXTENSION','TENDER','UDFA')),
    player_id INT NOT NULL REFERENCES players(id),
    team_id INT NOT NULL REFERENCES teams(id),
    from_user BOOLEAN NOT NULL DEFAULT false,
    years SMALLINT NOT NULL,
    total_value BIGINT NOT NULL,
    bonus BIGINT NOT NULL DEFAULT 0,
    guaranteed BIGINT NOT NULL DEFAULT 0,
    utility NUMERIC(6,4),                   -- free_agency_engine.offer_utility, NULL until scored
    status VARCHAR(10) NOT NULL DEFAULT 'OPEN'
        CHECK (status IN ('OPEN','ACCEPTED','REJECTED','COUNTERED','WITHDRAWN','EXPIRED')),
    counter JSONB,                          -- {years, total_value, bonus, guaranteed} when COUNTERED
    seed TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS contract_offers_player ON contract_offers (player_id, season, status);

CREATE TABLE IF NOT EXISTS waiver_claims (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL, week INT NOT NULL,
    player_id INT NOT NULL REFERENCES players(id),
    team_id INT NOT NULL REFERENCES teams(id),
    priority SMALLINT NOT NULL,             -- 1 = worst record
    awarded BOOLEAN NOT NULL DEFAULT false
);

-- JR's queued moves from Mu_Network, applied at the next league office write.
CREATE TABLE IF NOT EXISTS franchise_pending_moves (
    id SERIAL PRIMARY KEY,
    team_id INT NOT NULL REFERENCES teams(id),
    kind VARCHAR(16) NOT NULL CHECK (kind IN ('DEPTH_CHART','RELEASE','SIGN','TRADE','PS_PROMOTE','PS_DEMOTE',
                                              'IR_PLACE','IR_ACTIVATE','RESTRUCTURE','EXTENSION','TAG','OPTION')),
    payload JSONB NOT NULL,
    status VARCHAR(10) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','WRITTEN','REFUSED','CANCELLED')),
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    written_run INT
);

-- One row per league office run, and per save write inside it.
CREATE TABLE IF NOT EXISTS league_office_runs (
    id SERIAL PRIMARY KEY,
    season INT NOT NULL, week INT, stage VARCHAR(16) NOT NULL,
    save_name TEXT NOT NULL,
    status VARCHAR(10) NOT NULL CHECK (status IN ('PLANNED','WRITTEN','FAILED','DRY_RUN')),
    edits JSONB NOT NULL,                   -- the exact save_writer edits document
    summary JSONB,                          -- counts per move kind
    drift JSONB,                            -- drift_check report before the write
    error TEXT,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS player_form (
    player_id INT NOT NULL REFERENCES players(id),
    season INT NOT NULL, week INT NOT NULL,
    form NUMERIC(4,2) NOT NULL CHECK (form BETWEEN -3 AND 3),   -- OVR points
    overlay JSONB NOT NULL,                 -- {RatingName: delta} written on top of the base ratings
    PRIMARY KEY (player_id, season, week)
);

CREATE TABLE IF NOT EXISTS retirement_decisions (
    season INT NOT NULL,
    player_id INT NOT NULL REFERENCES players(id),
    madden_retired BOOLEAN NOT NULL,
    mu_keep BOOLEAN NOT NULL,               -- Project Mu un-retires him
    reason TEXT,
    PRIMARY KEY (season, player_id)
);

ALTER TABLE injuries ADD COLUMN IF NOT EXISTS mu_weeks_out SMALLINT;   -- Project Mu's duration, written back

-- Progression C2 storage (PROGRESSION_PLAN §5.6).
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS checkpoint VARCHAR(12) NOT NULL DEFAULT 'OFFSEASON';
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS dev_before VARCHAR(16);
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS dev_after VARCHAR(16);
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS group_changes JSONB;
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS wear_term NUMERIC(6,3);
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS upkeep_term NUMERIC(6,3);
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS continuity_term NUMERIC(6,3);
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS mentor_term NUMERIC(6,3);
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS persona_term NUMERIC(6,3) NOT NULL DEFAULT 0;
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS coach_term NUMERIC(6,3) NOT NULL DEFAULT 0;
ALTER TABLE player_progression_history ADD COLUMN IF NOT EXISTS behaviour_changes JSONB;
ALTER TABLE player_ceilings ADD COLUMN IF NOT EXISTS floor NUMERIC(5,2);
ALTER TABLE player_ceilings ADD COLUMN IF NOT EXISTS spread NUMERIC(5,2);
ALTER TABLE player_ceilings ADD COLUMN IF NOT EXISTS group_ceilings JSONB;   -- {physical, technical, mental}
CREATE TABLE IF NOT EXISTS player_durability (
    player_id INT PRIMARY KEY REFERENCES players(id) ON DELETE CASCADE,
    durability NUMERIC(4,3) NOT NULL CHECK (durability BETWEEN 0 AND 1),
    source VARCHAR(16) NOT NULL, updated_season INT NOT NULL
);
CREATE TABLE IF NOT EXISTS player_mileage (
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    season INT NOT NULL,
    touches INT NOT NULL DEFAULT 0, hits INT NOT NULL DEFAULT 0, snaps INT NOT NULL DEFAULT 0,
    wear NUMERIC(6,2) NOT NULL DEFAULT 0,
    PRIMARY KEY (player_id, season)
);
```

### 2.2 Snapshot additions (`pc_save_reader.mjs`, `READER_VERSION = 4`)

```json
"depth_charts": [{"team_index": 3, "slots": {"QB": [22686, 9123, null, null, null, null], "HB": [...], ...}}],
"league_settings": {
  "sliders": {
    "progression_by_age":      {"table_id": 5949, "row": 0, "values": {"int0": 0, "...": 0}},
    "progression_by_position": {"table_id": 4317, "row": 0, "values": {"QB": 0, "LS": 100, "...": 0}},
    "regression_by_age":       {"table_id": 5949, "row": 1, "values": {...}},
    "regression_by_position":  {"table_id": 4317, "row": 1, "values": {...}}
  },
  "league_setting": {"table_id": 4290, "row": 0},
  "dev_trait_regression_field": "Field_NNN",          // from spike R3; null until known
  "dev_trait_regression_enabled": true                 // null while the field is unknown
}
```
Slots hold `pgid` (Player.PresentationId) or `null`, best first. `players[]` keeps its current fields; it must include every `PT_*` flag and `ConfidenceRating` (add them if `readPlayers` doesn't already).

### 2.3 Writer edits (`save_writer.mjs`)

New keys in the edits document, next to `players`, `records` and `release`:

```json
{
  "sign":  [{"pgid": 123, "team_index": 3, "list": "Roster",
             "contract": {"Length": 3, "SalaryYear0": 105, "BonusYear0": 50, "...": 0}}],
  "trade": [{"from_team_index": 3, "to_team_index": 12,
             "pgids": [123], "picks": [{"tableId": 4540, "row": 17}]}],
  "depthCharts": [{"team_index": 3, "slots": {"QB": [22686, 9123], "...": []}}]
}
```
- `sign`: from `FreeAgent` or `Draft` to `Signed` (`list` `Roster`) or `PracticeSquad`. Sets `TeamIndex`, `ContractStatus`, links a `PlayerContract` row (reuses an unreferenced row left by `release`, else fails with a clear error), appends to the team list and removes from `League.FreeAgents` (and from the draft lists for a prospect), raises roster counts. It doesn't touch the depth chart; the league office sends `depthCharts` in the same write.
- `trade`: moves each player with his contract record between the two teams' lists, removes him from the old depth chart, re-points each `DraftPick.CurrentTeam` (same rule as `record_edits.mjs`).
- `depthCharts`: replaces the listed positions' 6 slots (missing slots become empty); every pgid must be on that team's roster.
- Each move is a plan/apply/verify triple; `writeSave` refuses the whole document if any plan fails, and read-back covers every move. A player may appear in only one of `release`, `sign`, `trade`.
- The CLI report gains `sign`, `trade`, `depthCharts` counts.

### 2.4 Engines (pure functions first; DB readers are thin wrappers)

`engines/contracts_engine.py`:
```python
CBA = {...}  # constants: rookie scale by pick, minimums by accrued seasons, tag percentiles, 90% restructure cap
def cap_hit(contract: dict, year: int) -> int
def dead_money(contract: dict, year: int, post_june1: bool = False) -> int
def cut_savings(contract: dict, year: int, post_june1: bool = False) -> int
def extension_eligible(contract: dict, player: dict, season: int, week: int) -> tuple[bool, str]
def fifth_year_option(player: dict, salaries_by_position: dict[str, list[int]]) -> dict   # {tier, salary}
def tag_value(kind: str, position: str, salaries_by_position: dict, prior_salary: int, cap_history: list[int]) -> int
def tender_value(kind: str, accrued_seasons: int, cap: int) -> int
def restructure(contract: dict, year: int, amount: int) -> dict   # new contract dict; refuses > 90% of base
def rookie_contract(overall_pick: int, season: int, cap: int) -> dict
def next_cap(prev_cap: int, season: int, seed: str) -> int
def to_madden(contract: dict) -> dict      # {Length, CurrentYear, SalaryYearN, BonusYearN} in $10,000 units
def from_snapshot(c: dict) -> dict
```
Contract dicts: `{"length", "current_year", "salary_by_year": [...], "bonus_by_year": [...], "guaranteed", "contract_type", "signed_season", "last_restructure_season", "drafted_round"}`, whole dollars.

`engines/free_agency_engine.py`:
```python
W_DEFAULT = {"money": 0.45, "role": 0.25, "team": 0.20, "scheme": 0.10}
def offer_utility(offer: dict, player: dict, team: dict, market: dict, persona: dict | None = None) -> dict
    # {"utility", "financial", "opportunity", "contention", "scheme", "persona": 0.0}
def role_score(projected_depth: int, player_ovr: int) -> float   # 1.0 starter, 0.65 situational, 0.3 backup
def acceptance(best: dict, player: dict, in_season: bool) -> tuple[str, dict | None]   # ACCEPT | REJECT | COUNTER
def run_wave(players: list, offers: list, wave: int, seed: str) -> list[dict]   # decisions, seeded
def street_market(players: list, needs: dict, seed: str) -> list[dict]
def udfa_market(prospects: list, needs: dict, seed: str) -> list[dict]
```
`persona` is `None` everywhere in this plan (D4); the term is 0.

`engines/depth_chart_engine.py`:
```python
POSITION_SLOTS = [...]   # the 35 DepthChart field names, in Madden's order (release.mjs:9)
def staff_depth_chart(roster: list[dict], scheme: dict, injuries: dict, coach: dict | None = None) -> dict[str, list[int]]
    # slot -> up to 6 player_ids, best first; coach trust terms 0 (D4)
def store(cur, team_id: int, season: int, week: int, chart: dict, source: str) -> None
def load(cur, team_id: int, season: int, week: int, source: str) -> dict
```

`engines/transactions_engine.py` (team side, all 32 teams):
```python
def resign_decisions(cur, season: int, seed: str) -> list[dict]      # keep / extend / tag / option / let go
def cpu_offers(cur, season: int, stage: str, wave: int | None, seed: str) -> list[dict]
ROSTER_LIMIT = 65   # roster-size mod (D5)
def cutdown(cur, season: int, seed: str, limit: int = ROSTER_LIMIT) -> list[dict]  # releases to reach the limit
def waivers(cur, season: int, week: int, released: list[int], seed: str) -> list[dict]
def practice_squad(cur, season: int, seed: str) -> list[dict]
def rookie_signings(cur, season: int) -> list[dict]                 # drafted -> rookie_contract
```
Each returns move dicts `{"kind": "SIGN"|"RELEASE"|"TRADE"|"CONTRACT"|"PS_PROMOTE"|..., "player_id", "team_id", ...}` that `league_office.py` turns into writer edits.

Progression:
- `engines/progression.py` (U7) keeps `run_progression`'s signature and result rows, adding the C2 terms and their columns (§2.1). Trait and coach terms are computed as 0.
- `engines/progression_save.py` (U9):
  `slider_edits(snapshot) -> list[dict]` (all four slider records to 0, LS included), `dev_flag_edit(snapshot) -> dict | None`, `rating_edits(rows, snapshot) -> list[dict]` (ratings, `OverallRating`, `OverallGrade0-4`, `PlayerType` from `engines/ovr_weights.py`, `TraitDevelopment`, `PT_*`), `morale_edits(snapshot, value=50) -> list[dict]`, `pass_edits(cur, snapshot, season) -> dict` (the whole edits document).
- `engines/form_engine.py` (U10): `weekly_form(cur, season, week) -> dict[int, float]` (±3 OVR from the last few games, neutral every offseason), `overlay(base_ratings, position, form) -> dict` (rating deltas whose OVR effect equals `form`), `form_edits(cur, snapshot, season, week) -> dict` (base + overlay ratings and `ConfidenceRating` 50 for every player); stores `player_form`.

### 2.5 Drift check (`pipeline/drift_check.py`)

`diff(snapshot, cur, season, week) -> dict`: compares the save against Project Mu's last written state (base ratings plus `player_form` overlay, `player_contracts`, team rosters, `depth_charts` source `MU_COACH`/`JR`, slider values). Returns `{"ratings": [...], "contracts": [...], "rosters": [...], "depth_charts": [...], "settings": [...]}`. The league office stores it in `league_office_runs.drift` and writes Project Mu's version back.

### 2.6 League office (`pipeline/league_office.py`)

```
python3 -m pipeline.league_office --snapshot captures/<run>/save.json --save <CAREER-file> [--stage auto|<name>] [--dry-run]
```
- `detect_stage(snapshot) -> str` from `season.stage`, `week_type`, `week_index`: `REGULAR_WEEK`, `PLAYOFF_WEEK`, `OFFSEASON_START`, `RESIGN`, `FREE_AGENCY` (waves 1–3), `DRAFT`, `UDFA`, `CUTDOWN`, `PRESEASON`.
- `plan_stage(cur, snapshot, stage, seed) -> dict` returns the edits document:
  - every stage: JR's `franchise_pending_moves`, drift write-back;
  - `REGULAR_WEEK`/`PLAYOFF_WEEK`: coach depth charts, waivers, injury durations (`mu_weeks_out`), form overlay and morale 50;
  - `OFFSEASON_START`: progression pass (U9), sliders and dev flag, retirement snapshot (`retirement_decisions`) and un-retire edits for `mu_keep`, form removed;
  - `RESIGN`, `FREE_AGENCY`, `DRAFT` (all 224 picks from team boards, JR's picks from the war room), `UDFA`, `CUTDOWN`: transactions engine moves.
- `run(cur, snapshot, save_path, stage, dry_run) -> int` writes through `save_writer.mjs` (copy, `--out`, then replace the save only after read-back passes and Madden isn't running, `is_madden_running`), logs `league_office_runs`, marks pending moves `WRITTEN` or `REFUSED`.
- Madden-must-be-closed rule: refuses to write while `Madden27.exe` runs.

### 2.7 Franchise API (`server/franchise_api.py`, prefix `/api/franchise`)

| Method, path | Returns / does |
|---|---|
| `GET /{abbr}/overview` | record, cap room, next game, pending moves |
| `GET /{abbr}/depth-chart` · `PUT /{abbr}/depth-chart` · `POST /{abbr}/depth-chart/staff` | current chart by slot; queue JR's chart; staff suggestion from `depth_chart_engine` |
| `GET /{abbr}/roster` | active, practice squad, IR, each with contract and cap hit |
| `GET /{abbr}/cap?years=5` | cap sheet with dead money, rookie reserve |
| `POST /{abbr}/moves/preview` · `POST /{abbr}/moves` | `{kind, payload}` → cap savings, dead money, legality; queue the move |
| `GET /free-agents?position&min_ovr&max_age` · `POST /{abbr}/offers` | market list; JR's offer scored with `offer_utility` and stored in `contract_offers` |
| `GET /{abbr}/contracts/expiring` · `POST /{abbr}/contracts/{player_id}/propose` · `POST /{abbr}/contracts/{player_id}/restructure-preview` | eligibility; agent's ACCEPT/REJECT/COUNTER; restructure effect |
| `POST /{abbr}/trades/evaluate` | wraps `gm_negotiation.judge` and `trade_value` with cap checks from `contracts_engine` |
| `GET /{abbr}/medical` | injuries, weeks out, status |
| `GET /{abbr}/game-center` · `POST /{abbr}/game/launch` | opponent dossier and matchups; launch (`pipeline/game_launch.py`) |

JSON shapes are fixed in the module docstring of `server/franchise_api.py` and mirrored by the frontend types (U12, U15, U17 build against this table and those shapes).

`pipeline/game_launch.py`: `launch(team, season, week, dry_run=False) -> dict`: refuses if moves are pending and unwritten, writes the mod pack (`mod_packs.write_pack`), runs `MMCModManager.exe -launch <pack>` through `cmd.exe` from WSL, returns `{pack, command, started}`.

### 2.8 Lifecycle (`pipeline/watch_save.py`)

After a successful `ingest_snapshot` for a new week or stage: `league_office.run(...)` (unless `--no-league-office`), then `prepare_next_week_mod_pack` writes the pack with `write_pack` instead of only logging it. New flags: `--no-league-office`, `--league-office-dry-run`.

### 2.9 `#franchise` page

`#franchise` (user team from the franchise registry) and `#franchise-<module>`. `FranchisePage.tsx` holds the tab bar and renders eight standalone modules, each `({ teamAbbr }: { teamAbbr: string }) => JSX.Element`: `GameCenterModule`, `DepthChartModule`, `RosterCapModule`, `FreeAgencyModule`, `ContractRoomModule`, `TradeMachineModule`, `DraftWarRoomModule` (reuses `/api/draft/war-room`), `TrainingRoomModule`. Types live in `types/franchiseRoster.ts`, `franchiseMarket.ts`, `franchiseGame.ts`.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Madden rejects or "repairs" a signed or traded player (new contract link, roster counts, cap totals). | After wave 1, JR loads a save written by U2's tests (one sign, one trade, one depth chart) and advances a week; orchestrator diffs it. | U2 follows up before wave 5; the league office keeps Madden's CPU for that move until fixed (LEAGUE_OFFICE §6 fallback). |
| R2 | All-32-user-teams vs CPU automation off (LEAGUE_OFFICE L1): Madden may block advancing until user teams act, or keep making CPU moves. | PC spike before wave 5: JR sets all 32 teams to user and advances through re-sign, FA, draft and cut-down; notes every prompt. | `league_office.py` satisfies each prompt's requirement, or uses route B and the drift check undoes CPU moves. |
| R3 | `IsDevTraitRegressionEnabled`'s `Field_N` in `LeagueSetting` is unknown. | Before wave 3: JR toggles the setting on a copy of a save; orchestrator diffs the two `LeagueSetting` rows. | U9 ships with `dev_flag_edit` returning None and a warning; JR sets it once in the menu per franchise (dev-trait regression is not a slider the menu clamps). |
| R4 | The stage at which Madden turns `Retired` into `Deleted` (S7), and whether a status reset before it keeps the player. | Before wave 5: JR saves at each offseason stage of a test franchise; orchestrator reads `ContractStatus` counts per stage. | Un-retire becomes a `Deleted` → `FreeAgent` reset if that holds, else the retirement step runs in `OFFSEASON_START` before Madden's stage. |
| R5 | Madden re-sorts written depth charts (auto-reorder) or ignores them in game. | Same session as R1: write a visibly wrong chart, advance, read back; JR checks the in-game chart. | Turn auto-reorder off in franchise settings; the weekly write re-applies charts either way. |
| R6 | Madden recomputes or ignores cap totals (`TEAM_SALARY`, `SalCapCapRoom`) after writes. | R1 session: compare cap fields before and after a sign. | `contracts_engine` totals are written too (U2 follow-up) or read back from Madden. |
| R7 | Writing 32 teams' moves in one `writeSave` is slow on the 5.6 MB save. | U2 times a 200-move write in its test. | Split per stage into batches; read-back stays per batch. |
| R8 | Frontend units build against API shapes before the API exists (U12, U15). | U14's tests assert the documented shapes; U17 runs the page against the API. | U17 fixes type mismatches; shapes in §2.7 win. |

**Spike first:** none blocks wave 1. Gates: R1, R5, R6 after wave 1 (JR on the PC, about 30 minutes); R3 before wave 3 (5 minutes); R2 and R4 before wave 5 (JR, about an hour on a test franchise).

## 4. Tests (synthetic, no live DB)

- `pipeline/save_reader/test/sign.test.mjs` → a free agent and a draft prospect signed: status, team, contract link, list membership, free-agent list, roster counts; refusal writes nothing.
- `pipeline/save_reader/test/trade.test.mjs` → players and picks change teams with contracts kept; old depth chart cleared; refusal writes nothing.
- `pipeline/save_reader/test/depth_chart.test.mjs` → slots replaced and read back; a pgid not on the roster refused.
- `pipeline/save_reader/test/write_save.test.mjs` → mixed document (players, records, release, sign, trade, depthCharts) in one write; a player in two moves refused; 200-move timing logged.
- `pipeline/save_reader/test/read_save.test.mjs`, `lib.test.mjs` → `depth_charts` has 32 teams × 35 slots × 6; `league_settings` slider ids match `Field_165-168`; `reader_version` 4.
- `tests/test_contracts_engine.py` → cap hit, proration over min(length, 5), pre/post June 1 dead money, cut savings sign, extension windows (drafted year 3, UDFA year 2, veteran 12 months or final year), option tiers, tag and tender values, restructure 90% cap, rookie scale, `to_madden` units, `next_cap` seeded.
- `tests/test_free_agency_engine.py` → utility parts and weights, starter vs backup rule (80+ OVR rejects a backup role without a premium), contention discount, persona term 0, seeded waves reproducible, street and UDFA markets.
- `tests/test_depth_chart_engine.py` → injured players skipped, best-first by fit and OVR, 35 slots filled from eligible positions, specialists, store/load with a fake cursor.
- `tests/test_progression.py` (extended) → durability seeding, mileage wear, ceiling bands and breakthroughs, upkeep above 90, continuity and mentors, PT gains and losses stored; backtest MAE and `--simulate 5` 90+ count still within today's bounds; trait and coach terms 0.
- `tests/test_progression_save.py` → slider edits set all four records to 0 with LS; dev flag edit only when the field is known; rating edits include OVR, grades and PlayerType; morale edits 50; one document.
- `tests/test_form_engine.py` → form bounded ±3, resets at the offseason, overlay's OVR effect equals form, morale 50 for all.
- `tests/test_transactions_engine.py` → re-sign keeps a valuable cheap player and lets an overpaid one go; offers respect cap room; cut-down reaches 65 (`ROSTER_LIMIT`) and prefers higher cut savings at equal value; waiver order worst-record-first; PS eligibility; rookie deals from the scale.
- `tests/test_load_save.py` (extended), `tests/test_drift_check.py` → depth charts loaded as `MADDEN`; settings check; drift finds a rating, contract, roster, chart and slider change.
- `tests/test_league_office.py` → `detect_stage` for each stage; `plan_stage` builds the right move kinds per stage with fakes; refuses to write while Madden runs; failed read-back leaves the save untouched and logs `FAILED`; pending moves marked.
- `tests/test_franchise_api.py`, `tests/test_game_launch.py` → each endpoint's shape with fake data; move preview math; launch refuses with pending moves and builds the right command in dry run.
- `tests/test_watch_save.py` (extended) → league office called after ingest, skipped with `--no-league-office`; mod pack written.

Run with `pytest -m "not integration" <files>` and `cd pipeline/save_reader && npm test`. Integration tests, if any, run against `madden_test` only. Frontend: `cd Mu_Network && npm run build`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`, `docs/in_progress/V2_MASTER_PLAN.md`, `docs/in_progress/PROGRESSION_PLAN.md`, `docs/done/LEAGUE_OFFICE_PLAN.md`.

### U1: Schema

- **Owns:** `db/migrations/2027_01_14_v2_franchise_os.sql` (new), `db/init_db.sql`
- **Reads:** §2.1; `db/migrations/2026_12_07_contracts.sql`, `2027_01_08_player_progression.sql`, `2027_01_11_progression_rating_groups.sql`, `2026_11_08_injuries.sql`
- **Depends on:** none
- **Done when:** the migration applies twice cleanly to `madden_test`, and `init_db.sql` creates the same objects on an empty database.
- **Verify:** `psql -v ON_ERROR_STOP=1 -h localhost -U postgres -d madden_test -f db/migrations/2027_01_14_v2_franchise_os.sql`, run twice → no errors; `psql -h localhost -U postgres -d madden_test -c '\d contract_offers'` shows the columns. (Not `db/run_sql.py`: it's a one-off script hard-wired to the live `madden` database.)

### U2: Writer moves (sign, trade, depth charts)

- **Owns:** `pipeline/save_reader/sign.mjs` (new), `pipeline/save_reader/trade.mjs` (new), `pipeline/save_reader/depth_chart.mjs` (new), `pipeline/save_reader/save_writer.mjs`, `pipeline/save_reader/test/sign.test.mjs` (new), `pipeline/save_reader/test/trade.test.mjs` (new), `pipeline/save_reader/test/depth_chart.test.mjs` (new), `pipeline/save_reader/test/write_save.test.mjs`
- **Reads:** §2.3; `release.mjs`, `contract_edits.mjs`, `record_edits.mjs`, `lib.mjs`, `test/release.test.mjs`
- **Depends on:** none
- **Done when:** the three moves write and read back on the sample save inside one `writeSave`; a bad move writes nothing; the report counts them. The largest unit (8 files); it stays one unit because all three moves plug into `save_writer.mjs`.
- **Verify:** `cd pipeline/save_reader && npm test` → all pass (skips only without `MU_SAMPLE_SAVE`).

### U3: Contracts engine

- **Owns:** `engines/contracts_engine.py` (new), `tests/test_contracts_engine.py` (new)
- **Reads:** §2.4; V2 plan §3; `pipeline/contracts.py`, `pipeline/load_save.py:270-292`, `engines/trade_value.py`
- **Depends on:** none
- **Done when:** every function in §2.4 exists with tests for each CBA rule in V2 §3.
- **Verify:** `pytest -m "not integration" tests/test_contracts_engine.py` → pass.

### U4: Reader extensions

- **Owns:** `pipeline/save_reader/pc_save_reader.mjs`, `pipeline/save_reader/lib.mjs`, `pipeline/save_reader/test/read_save.test.mjs`, `pipeline/save_reader/test/lib.test.mjs`
- **Reads:** §2.2; `release.mjs:5-14,74-100`; `PROGRESSION_PLAN.md` §2
- **Depends on:** none
- **Done when:** the snapshot has `depth_charts` and `league_settings`, players carry every `PT_*` and `ConfidenceRating`, `READER_VERSION` is 4.
- **Verify:** `cd pipeline/save_reader && npm test` → pass; `node pc_save_reader.mjs ../../CAREER-GIANTS-AUTOSAVE --out /tmp/s.json` → JSON with both blocks.

### U5: Free agency engine

- **Owns:** `engines/free_agency_engine.py` (new), `tests/test_free_agency_engine.py` (new)
- **Reads:** §2.4; V2 plan §4; `engines/trade_value.py:89,273`; `engines/gm_direction.py`
- **Depends on:** none
- **Done when:** utility, acceptance, waves, street and UDFA markets work on synthetic players, seeded and reproducible; persona term 0.
- **Verify:** `pytest -m "not integration" tests/test_free_agency_engine.py` → pass.

### U6: Depth chart engine

- **Owns:** `engines/depth_chart_engine.py` (new), `tests/test_depth_chart_engine.py` (new)
- **Reads:** §2.4; `release.mjs:9-11`; `engines/coach_*` scheme fields; V2 plan §2.2
- **Depends on:** none
- **Done when:** a full 35-slot chart comes out of a synthetic roster with injuries and scheme; store/load round-trip with a fake cursor.
- **Verify:** `pytest -m "not integration" tests/test_depth_chart_engine.py` → pass.

### U7: Progression C2 (rest)

- **Owns:** `engines/progression.py`, `tests/test_progression.py`
- **Reads:** `PROGRESSION_PLAN.md` §5.4–§5.15; §2.1 progression columns
- **Depends on:** U1
- **Done when:** durability and mileage, ceiling bands and breakthroughs, upkeep, continuity and mentors and the PT model are in `run_progression`, stored in U1's columns; trait and coach terms 0; backtest MAE within 0.05 of today's 1.134 and `--simulate 5` 90+ count within today's 143–146 band ±5.
- **Verify:** `pytest -m "not integration" tests/test_progression.py` → pass.

### U8: Loader and drift check

- **Owns:** `pipeline/load_save.py`, `tests/test_load_save.py`, `pipeline/drift_check.py` (new), `tests/test_drift_check.py` (new)
- **Reads:** §2.2, §2.5; U4's snapshot
- **Depends on:** U1, U4
- **Done when:** depth charts load as `MADDEN`; the load report includes slider values and the dev flag; `drift_check.diff` reports each kind of change.
- **Verify:** `pytest -m "not integration" tests/test_load_save.py tests/test_drift_check.py` → pass.

### U9: Progression save pass (phase D)

- **Owns:** `engines/progression_save.py` (new), `tests/test_progression_save.py` (new)
- **Reads:** §2.4; `engines/ovr_weights.py`; `engines/progression.py` (`run_progression` result rows); U4's `league_settings`
- **Depends on:** U4 (and spike R3 for the dev flag)
- **Done when:** `pass_edits` produces one edits document (ratings with OVR dual-write, dev trait, PT flags, sliders to 0 with LS, dev flag, morale 50) that `save_writer.mjs` accepts on the sample save.
- **Verify:** `pytest -m "not integration" tests/test_progression_save.py` → pass.

### U10: Form and morale (phase E)

- **Owns:** `engines/form_engine.py` (new), `tests/test_form_engine.py` (new)
- **Reads:** `PROGRESSION_PLAN.md` §5.11; §2.4; `engines/progression_save.py`
- **Depends on:** U1, U9
- **Done when:** weekly form is stored in `player_form` and `form_edits` writes base + overlay ratings and morale 50 for every player.
- **Verify:** `pytest -m "not integration" tests/test_form_engine.py` → pass.

### U11: Transactions engine

- **Owns:** `engines/transactions_engine.py` (new), `tests/test_transactions_engine.py` (new)
- **Reads:** §2.4; `LEAGUE_OFFICE_PLAN.md` §4.4–§4.6, §4.11; `engines/gm_direction.py`, `engines/trade_value.py`, `pipeline/contracts.py`
- **Depends on:** U1, U3, U5
- **Done when:** each function in §2.4 returns move dicts for all 32 teams from fake data and stores offers and claims in U1's tables.
- **Verify:** `pytest -m "not integration" tests/test_transactions_engine.py` → pass.

### U12: Frontend: Depth Chart, Roster & Cap, Training Room

- **Owns:** `Mu_Network/src/components/franchise/DepthChartModule.tsx` (new), `Mu_Network/src/components/franchise/RosterCapModule.tsx` (new), `Mu_Network/src/components/franchise/TrainingRoomModule.tsx` (new), `Mu_Network/src/types/franchiseRoster.ts` (new)
- **Reads:** §2.7, §2.9; V2 plan §2.2, §2.3, §2.8; an existing team component for styling
- **Depends on:** none (builds against §2.7 shapes)
- **Done when:** the three modules render from mocked fetches and call the §2.7 endpoints; click-to-swap depth chart; cut preview shows savings and dead money.
- **Verify:** `cd Mu_Network && npm run build` → succeeds.

### U13: League office stage loop

- **Owns:** `pipeline/league_office.py` (new), `tests/test_league_office.py` (new)
- **Reads:** §2.5, §2.6; `LEAGUE_OFFICE_PLAN.md` §2–§4; `pipeline/save_reader/save_writer.mjs` CLI; `pipeline/gpu_queue/process_check.py:139`
- **Depends on:** U2, U6, U8, U9, U10, U11 (and spikes R2, R4)
- **Done when:** `--dry-run` on a sample snapshot prints the planned document per stage; a real run writes, reads back, logs and only then replaces the save.
- **Verify:** `pytest -m "not integration" tests/test_league_office.py` → pass; `python3 -m pipeline.league_office --help` → usage.

### U14: Franchise API and game launch

- **Owns:** `server/franchise_api.py` (new), `server/api.py`, `tests/test_franchise_api.py` (new), `pipeline/game_launch.py` (new), `tests/test_game_launch.py` (new)
- **Reads:** §2.7; `server/front_office_api.py`, `server/draft_api.py`, `pipeline/mod_packs.py`
- **Depends on:** U1, U3, U5, U6, U11
- **Done when:** every §2.7 endpoint answers with the documented shape on fake data; moves are queued in `franchise_pending_moves`; launch builds the command in dry run.
- **Verify:** `pytest -m "not integration" tests/test_franchise_api.py tests/test_game_launch.py` → pass.

### U15: Frontend: Free Agency, Contract Room, Trade Machine

- **Owns:** `Mu_Network/src/components/franchise/FreeAgencyModule.tsx` (new), `Mu_Network/src/components/franchise/ContractRoomModule.tsx` (new), `Mu_Network/src/components/franchise/TradeMachineModule.tsx` (new), `Mu_Network/src/types/franchiseMarket.ts` (new)
- **Reads:** §2.7, §2.9; V2 plan §2.4–§2.6
- **Depends on:** none (builds against §2.7 shapes)
- **Done when:** market filters, offer form with utility feedback, extension and restructure forms, two-way trade builder with the surplus bar render from mocked fetches.
- **Verify:** `cd Mu_Network && npm run build` → succeeds.

### U16: Lifecycle

- **Owns:** `pipeline/watch_save.py`, `tests/test_watch_save.py`
- **Reads:** §2.8; `pipeline/league_office.py`
- **Depends on:** U13
- **Done when:** a new week's save runs ingest then the league office; the mod pack is written; both new flags work.
- **Verify:** `pytest -m "not integration" tests/test_watch_save.py` → pass.

### U17: Frontend: shell, Game Center, Draft War Room, route

- **Owns:** `Mu_Network/src/components/franchise/FranchisePage.tsx` (new), `Mu_Network/src/components/franchise/GameCenterModule.tsx` (new), `Mu_Network/src/components/franchise/DraftWarRoomModule.tsx` (new), `Mu_Network/src/types/franchiseGame.ts` (new), `Mu_Network/src/App.tsx`
- **Reads:** §2.7, §2.9; U12 and U15 modules; `DraftPage.tsx`
- **Depends on:** U12, U15 (U14 for a live check)
- **Done when:** `#franchise` shows all eight tabs with a nav link; Game Center's "Play Game" calls the launch endpoint; the page works against the running API.
- **Verify:** `cd Mu_Network && npm run build` → succeeds; with the API up, `#franchise` loads each tab without console errors.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2, U3 | — | Schema, JS writer and a pure engine share no files. Gate after: spikes R1, R5, R6 (JR loads U2's test save). |
| 2 | U4, U5, U6 | — | Reader (JS) and two pure engines; no shared files. Gate after: spike R3. |
| 3 | U7, U8, U9 | U1, U4 | Progression C2, loader and the progression pass touch different files; U8/U9 read U4's snapshot. |
| 4 | U10, U11, U12 | U1, U3, U5, U9 | Form uses U9's edit builders; transactions use U3/U5; frontend part 1 needs nothing merged. Gate after: spikes R2, R4. |
| 5 | U13, U14, U15 | U2, U6, U8–U11 | The stage loop, the API and frontend part 2 share no files. |
| 6 | U16, U17 | U13, U12, U15 | Watcher wiring and the page shell close the loop. |

**File ownership check:**

| File | Unit |
|---|---|
| `Mu_Network/src/App.tsx` | U17 |
| `Mu_Network/src/components/franchise/ContractRoomModule.tsx` | U15 |
| `Mu_Network/src/components/franchise/DepthChartModule.tsx` | U12 |
| `Mu_Network/src/components/franchise/DraftWarRoomModule.tsx` | U17 |
| `Mu_Network/src/components/franchise/FranchisePage.tsx` | U17 |
| `Mu_Network/src/components/franchise/FreeAgencyModule.tsx` | U15 |
| `Mu_Network/src/components/franchise/GameCenterModule.tsx` | U17 |
| `Mu_Network/src/components/franchise/RosterCapModule.tsx` | U12 |
| `Mu_Network/src/components/franchise/TradeMachineModule.tsx` | U15 |
| `Mu_Network/src/components/franchise/TrainingRoomModule.tsx` | U12 |
| `Mu_Network/src/types/franchiseGame.ts` | U17 |
| `Mu_Network/src/types/franchiseMarket.ts` | U15 |
| `Mu_Network/src/types/franchiseRoster.ts` | U12 |
| `db/init_db.sql` | U1 |
| `db/migrations/2027_01_14_v2_franchise_os.sql` | U1 |
| `engines/contracts_engine.py` | U3 |
| `engines/depth_chart_engine.py` | U6 |
| `engines/form_engine.py` | U10 |
| `engines/free_agency_engine.py` | U5 |
| `engines/progression.py` | U7 |
| `engines/progression_save.py` | U9 |
| `engines/transactions_engine.py` | U11 |
| `pipeline/drift_check.py` | U8 |
| `pipeline/game_launch.py` | U14 |
| `pipeline/league_office.py` | U13 |
| `pipeline/load_save.py` | U8 |
| `pipeline/save_reader/depth_chart.mjs` | U2 |
| `pipeline/save_reader/lib.mjs` | U4 |
| `pipeline/save_reader/pc_save_reader.mjs` | U4 |
| `pipeline/save_reader/save_writer.mjs` | U2 |
| `pipeline/save_reader/sign.mjs` | U2 |
| `pipeline/save_reader/test/depth_chart.test.mjs` | U2 |
| `pipeline/save_reader/test/lib.test.mjs` | U4 |
| `pipeline/save_reader/test/read_save.test.mjs` | U4 |
| `pipeline/save_reader/test/sign.test.mjs` | U2 |
| `pipeline/save_reader/test/trade.test.mjs` | U2 |
| `pipeline/save_reader/test/write_save.test.mjs` | U2 |
| `pipeline/save_reader/trade.mjs` | U2 |
| `pipeline/watch_save.py` | U16 |
| `server/api.py` | U14 |
| `server/franchise_api.py` | U14 |
| `tests/test_contracts_engine.py` | U3 |
| `tests/test_depth_chart_engine.py` | U6 |
| `tests/test_drift_check.py` | U8 |
| `tests/test_form_engine.py` | U10 |
| `tests/test_franchise_api.py` | U14 |
| `tests/test_free_agency_engine.py` | U5 |
| `tests/test_game_launch.py` | U14 |
| `tests/test_league_office.py` | U13 |
| `tests/test_load_save.py` | U8 |
| `tests/test_progression.py` | U7 |
| `tests/test_progression_save.py` | U9 |
| `tests/test_transactions_engine.py` | U11 |
| `tests/test_watch_save.py` | U16 |

No path appears twice.

## 7. Spec changes when built

- §1.1 Repository Layout: every new file above; `docs/done/LEAGUE_OFFICE_PLAN.md` → `docs/done/` (superseded); this plan through `in_progress/` to `done/`.
- §1.2 Running the System: `pipeline.league_office` CLI, new `watch_save` flags, `#franchise`.
- §2.21 Player Progression Engine: C2 terms, phase D pass, phase E form and morale 50.
- New §2 sections: Contracts engine (CBA), Free agency engine, Depth chart engine, Transactions engine.
- §3.3d Contracts & Cap: new `player_contracts` columns, `contract_offers`, `waiver_claims`; §3.3b `injuries.mu_weeks_out`; §3.3n progression columns, `player_durability`, `player_mileage`, `player_form`; new subsections for `depth_charts`, `franchise_pending_moves`, `league_office_runs`, `retirement_decisions`.
- §4: new "Franchise Endpoints (`server/franchise_api.py`, `/api/franchise/*`)".
- §5.1 routing: `#franchise`; §5.2 the eight modules.
- §6.0b watcher: league office after ingest, mod pack written; save reader snapshot reader_version 4 with `depth_charts`, `league_settings`; writer moves `sign`, `trade`, `depthCharts`.
- `V2_MASTER_PLAN.md` §7.0: P6 resolved (S2), P2 superseded by this plan, P3 delivered by U16.

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | LEAGUE_OFFICE L1: all 32 teams user-controlled, or CPU teams with automation off? | All 32 user-controlled, decided after spike R2 (before wave 5); `league_office.py` is written to support both. |
| Q2 | LEAGUE_OFFICE L4: JR makes his team's moves only in `Mu_Network`? | Yes; Madden's menus would bypass the rules and the drift check would undo them. |
| Q3 | Media award ballots, owners, holdouts and trade requests (league office phases G, H) and coach writes to the save: roadmap, not this plan? | Roadmap (§9): they need the inbox (V2.9), new persona types and a coach-creation spike. |
| ~~Q4~~ | Roster limit for cut-down | **Decided (JR, 2026-10-30): 65**, the roster-size mod's limit (installed). `ROSTER_LIMIT = 65` in `engines/transactions_engine.py`; `cutdown(..., limit=ROSTER_LIMIT)`. |

## 9. Roadmap after the core (not broken into units)

Each line becomes its own plan. "Needs" is what must be built first; "Alongside" means it can be built in parallel with the core waves.

| Item | Needs | Alongside |
|---|---|---|
| V2.16 / §5.1 dual-track media (Marquee vs background queue) | GPU queue (built) | Yes, from wave 1 |
| V2.7 full-league recaps, V2.3 player voices (postgame quotes) | Post-advance ingest (U16) for timing | Yes; switch to post-advance timing after U16 |
| V2.1 Mu news inside Madden (`NewsCenterArticle`) | A writer move for news rows (new), U2's patterns | After wave 1 |
| V2.2 press conferences, V2.9 front-office inbox | Franchise API (U14) for the UI hook | After wave 5 |
| V2.11 live multi-turn trade talks, transcripts and leaks | U14 trade evaluate, U13 trades | After wave 5 |
| V2.10 scouting revamp and combine interviews | Scouting (built), U14 draft hooks | Yes |
| V2.12 season-long narrative arcs, V2.5 fans and sentiment, V2.6 radio and podcasts | League memory (built), GPU queue | Yes |
| V2.4 consistent-face photography, P4 in-game portraits | Headshot pipeline (built); one-portrait spike | Yes |
| League office G: media award and Hall of Fame ballots | Newsroom personas (built) | After wave 5 |
| League office H: owners, holdouts, trade requests; agents as personas | V2.9 inbox, persona types; traits (D4) | After the inbox |
| Coaching carousel written into the save's coach tables | Coach-creation spike (M27 Coach Creator as prior art) | After wave 1 |
| Player and coach traits (personality 4–6, progression F–G) switching on the zero terms | U5, U6, U7 interfaces | Yes |
| O1 self-linking this week's player lines; O2 kickoff-window reveals | U4 reader; Game Center (U17) for O2 | After wave 2 / 6 |
| §8.1 boot straight into the game | U14 launch | After wave 5 |

## 10. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | worktree-agent-a1ef8be571108931d | .claude/worktrees/agent-a1ef8be571108931d | merged (migration applied twice to madden_test; init_db.sql builds on an empty db; checked by orchestrator, agent had no DB) | 2fae64b |
| U2 | 1 | worktree-agent-aa9d82094eb8c2454 | .claude/worktrees/agent-aa9d82094eb8c2454 | merged (save reader tests 39 pass, 1 old skip; 200-move write 0.8 s) | 28b4ba8 |
| U3 | 1 | worktree-agent-ae037027b0698f284 | .claude/worktrees/agent-ae037027b0698f284 | merged (30 tests pass) | 9afbdbd |
| U4 | 2 | worktree-agent-a874aed2cc404d905 | .claude/worktrees/agent-a874aed2cc404d905 | merged (reader tests 43 pass, 1 old skip; load_save/watch_save tests pass) | 6e8955a |
| U5 | 2 | worktree-agent-aa088807b9934f0b4 | .claude/worktrees/agent-aa088807b9934f0b4 | merged (17 tests pass) | b2916f0 |
| U6 | 2 | worktree-agent-af28754863db29059 | .claude/worktrees/agent-af28754863db29059 | merged (23 tests pass) | 7f8f901 |
