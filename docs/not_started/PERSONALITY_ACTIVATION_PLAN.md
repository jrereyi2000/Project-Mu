# Project Mu: Personality Live Wiring and Activation Plan

**Status:** approved by JR (2026-11-03), Q1–Q3 settled as recommended (§8); not built. Queued: JR wants every personality sub-plan written and approved before more building (2026-11-03). The other plans were updated at approval (§7).
**Builds on:** [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) §5.3, §6; the approved feature plans: [contracts](../done/PERSONALITY_CONTRACT_POLICY_PLAN.md) §2.4, §2.6; [development/coaching](../done/PERSONALITY_DEVELOPMENT_COACHING_PLAN.md) §2.4–§2.5, §2.7; [trade requests](../done/PERSONALITY_TRADE_REQUESTS_PLAN.md) §2.5, gates G4–G5; [retirement](PERSONALITY_RETIREMENT_PLAN.md) §2.4–§2.6, gates G4–G6; [relationships](../done/PERSONALITY_RELATIONSHIPS_PLAN.md) §2.1 modes; [event producers](../done/RELATIONSHIP_EVENT_PRODUCERS_PLAN.md); [evolution](../done/PERSONALITY_EVOLUTION_PLAN.md); [lifecycle](../done/PERSONALITY_DRAFT_INTEGRATION_PLAN.md) §6 gates; PROJECT_SPEC §2.21a, §6.0b.

**Goal:** Connect the personality features to the live league, live from the real franchise's first season (2026 week 1). Each feature plan builds its policy, storage and offline reports and stops before the live hookup. This plan:
- adds one switchboard per franchise, holding each feature's mode (off, shadow, active);
- feeds the league office's existing engine calls each feature's inputs;
- schedules trade-request reviews and hands their signal to the GM engine.

Each feature still earns its way to active: a full simulated season in shadow, in the go-live order, with JR's review. That shadow season happens on tuning franchises before the real league starts, and its clearance is imported, so the real 2026–2033 seasons aren't spent in shadow.

**Delivers:**
- `personality/features.py` and migration `db/migrations/2027_01_31_personality_feature_modes.sql`: tables `personality_feature_modes` and `personality_feature_mode_changes`.
- CLI `python -m personality.features show|set|history`.
- `pipeline/league_office_personality.py`: `PersonalityDeps`, a subclass of the league office's `Deps` that passes contract, development, depth-chart and retirement inputs according to each feature's mode.
- Small seams in `pipeline/league_office.py`:
  - the deps factory;
  - `step_retirements` asks `deps.retirement_decision`;
  - `step_coach_charts` passes `deps.chart_adjustments`.
- A watcher step for trade-request reviews, and the switchboard replacing the producers' and evolution's environment flags.
- An optional request signal in the GM trade engine's search.

**JR's answers (2026-11-03):**
- D1: One shared switchboard. A single table and CLI hold every feature's mode, version and JR's review note per franchise, and the contract plan's own activation table folds into it.
- D2: Features go live one at a time, in a fixed order, each in shadow first and active only when JR says so.
- D3: A feature stays in shadow for one full simulated season, with its report, before it can go active. The switchboard refuses an earlier switch unless JR forces it.
- D4 (JR, 2026-11-03, after asking whether features would be off in the first season): features are live from the real franchise's first season. The shadow season and tuning happen on tuning franchises, a `CAREER-TUNE-*` clone of the starting save loaded into a scratch database, before the real league starts. A tuning franchise's shadow season, imported as a clearance, meets D3 on the real franchise, and the D2 order is followed on the tuning franchises. Everything JR has cleared is active from 2026 week 1. A feature added later, or a new version mid-league, goes through a tuning-franchise season first, or JR forces it with a reason.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `pipeline/league_office.py:822` `class Deps` holds every engine call the stage loop makes: `:890` `resign_decisions`, `:893` `cpu_offers`, `:899` `run_wave`, `:909` `udfa_market`, `:912` `cutdown`, `:924` the progression checkpoint. `:1665` `plan_stage(..., deps=None)` and `:2000` `run(..., deps=None)` default to `Deps()`. | `PersonalityDeps(Deps)` is the seam: the stage loop stays as it is, and each method passes its feature's inputs. |
| S2 | `pipeline/league_office.py:1549` `step_free_agency` calls `deps.run_wave(pool, offers, wave, seed)`; `:1524` `step_udfa` calls `deps.udfa_market`; `:1646` the RESIGN stage calls `deps.resign_decisions`. | **The contract plan's survey note "no implemented league-office runner invoking run_wave/street_market/udfa_market" is out of date.** The runner exists; the contract plan's `contract_inputs` keyword goes through these deps methods. |
| S3 | `pipeline/league_office.py:343` `keep_retired(player, season, seed)` and `:1398` `step_retirements` already record each Madden retirement in `retirement_decisions` and un-retire a player (as a free agent) by a seeded age/OVR rule; `:1416` `step_restore_retirees` signs him back. These writes go through the league office's verified save write (§2.21a). | **The retirement plan's survey notes (S3 "No retirement writer was found", "Prove save authority before replacing Madden's choices") are partly out of date.** Restoring a native retiree already has a writer. Retiring an active player still has none. In active mode the retirement policy replaces `keep_retired`'s decision; it never adds a new kind of write. |
| S4 | `pipeline/league_office.py:1330` `step_coach_charts` builds each CPU team's chart from the staff algorithm. The development plan adds `score_adjustments=None` to `staff_depth_chart` (its §2.5). | `deps.chart_adjustments(team_id, ...)` supplies those adjustments when depth-chart opportunities are active, and nothing otherwise. |
| S5 | Progression runs inside the league office (`:80–82`; `:924` required). The development plan adds `development_context=None, development_config=None` to its checkpoint functions (its U3). | `PersonalityDeps` passes them when development learning is shadow or active. |
| S6 | `pipeline/weekly_capture.py:381` `step_gm` runs `engines.gm_trade_engine.run_week` in the weekly engines. The trade-requests plan stops at a signal: `{request_id, persona_id, team_id, status, cause_codes, actionable}` (its §2.5), "no `gm_trade_engine.py` edits", G4 for later. | This plan schedules request reviews and adds the signal to the GM search, as an extra candidate to shop at no price change. |
| S7 | The contract plan's migration creates `contract_policy_activation`, keyed by `universe_id` (its §2.5). The producers and evolution plans gate their watcher steps on `MU_RELATIONSHIP_PRODUCERS` and `MU_PERSONALITY_EVOLUTION`. The relationships plan has `mode='off'|'observe'|'enabled'` in its policy. | D1: one switchboard replaces all of these. Each plan's text is updated at approval to read `personality.features.mode(...)`. |
| S8 | `pipeline/watch_save.py` runs the producers step after the load (on weekly and offseason ingests) and the evolution step at tick 24 (those plans' U3). | The trade-request review step goes after them. The switchboard is read once per ingest. |
| S9 | Latest reserved migration date is `2027_01_30` (evolution). | This plan takes `2027_01_31`. |
| S10 | The tuning setup (`docs/in_progress/MADDEN_PILOT_AND_TUNING_PLAN.md` D1, `:108–109`, `:128`): each run sims `CAREER-TUNE-<run>`, a fresh copy of `CAREER-WEEK1` that keeps its LeagueID, and refuses to sim while `pipeline.watch_save` runs. | A tuning franchise can't be told apart from the real one by league ID. It lives in its own scratch database (loaded with `--dsn`), and its shadow results reach the real franchise only as an imported, hashed clearance (D4). |

## 1. Scope

**In:**
- The switchboard (modes, versions, configs, review notes, history), the go-live order (D2), the shadow-season rule (D3) and clearances imported from tuning franchises (D4), with a CLI.
- `activate-cleared`: on the real franchise, switch on every cleared feature in order at 2026 week 1.
- `PersonalityDeps`: contract inputs on re-sign, free agency and UDFA; development context on progression; chart adjustments on CPU staff charts; the retirement decision.
- The deps factory in `league_office.run`.
- Trade-request review scheduling, and the GM engine signal.
- Replacing the producers' and evolution's environment flags with the switchboard.
- Shadow reports gathered in one place.

**Out (non-goals):**
- Every feature's own policy and formulas, and its shadow comparisons: the feature plans.
- The contract HTTP and UI path: the contract plan's U4, which reads the switchboard instead of its own table.
- Retiring an active player in the save: still unsupported until the retirement plan's own proof passes (its Q1).
- New GM valuation or price discounts.
- Applying migrations or switching modes on a franchise: JR's call, after the build.

## 2. Design

### 2.1 Switchboard (`personality/features.py`)

```python
FEATURES = ('producers', 'relationships', 'profiles', 'evolution', 'contracts_ambition',
            'contracts_relationships', 'development_learning', 'trade_requests', 'retirement',
            'development_opportunity', 'coach_loop', 'player_loop', 'owner_patience', 'owner_hiring',
            'owner_spending', 'owner_meddling', 'scout_directors', 'media_voters')   # D2 go-live order; coach_loop/player_loop from the agent loops plan, the last six from the owners, scouts and voters plan
MODES = ('off', 'shadow', 'active')
def mode(cur, league_id, feature) -> str                       # missing row -> 'off'
def config(cur, league_id, feature) -> dict                    # {mode, version, config, review_ref, shadow_since}
def set_mode(cur, league_id, feature, mode, *, version, config, review_ref, now: dict, force_reason=None) -> dict
def snapshot(cur, league_id) -> dict                           # every feature's config, frozen per ingest
def export_clearance(cur, league_id, feature, *, source_label) -> dict   # on a tuning franchise: needs a full shadow season; returns the clearance document
def import_clearance(cur, league_id, document, *, cleared_by='JR') -> dict  # on the real franchise: checks feature, version and report hash
def activate_cleared(cur, league_id, *, now, review_ref) -> list[dict]     # sets every cleared feature active, in FEATURES order; stops at the first gap
```

`set_mode` rules (each refusal says why):
- **D2, going active:** a feature can be active only when every earlier feature is active. `profiles` counts as active once the lifecycle plan's inventory batch is applied for the universe.
- **D3, the shadow season:** `active` needs either `shadow_since` at or before tick 24 of the previous season on this franchise (measured from `now`, `{season, tick}` from `league_state`), or a `personality_feature_clearances` row for this feature and version (D4). Either is one full simulated season in shadow.
- **Shadow:** `shadow` needs the feature's code installed, meaning the feature module imports and its migration is present.
- **Forcing:** `force_reason` skips the order and shadow checks, and is recorded.
- **Off:** going `off` is always allowed.
- **History:** every change writes a `personality_feature_mode_changes` row.

`producers` and `relationships` only ever reach `shadow` meaning "observe": consumers stay neutral. `active` for them means consumers may read the projected context.

### 2.2 Tables (`2027_01_31_personality_feature_modes.sql`)

```sql
CREATE TABLE personality_feature_modes (
  league_id BIGINT NOT NULL, feature TEXT NOT NULL, mode TEXT NOT NULL CHECK (mode IN ('off','shadow','active')),
  version TEXT NOT NULL, config JSONB NOT NULL DEFAULT '{}', review_ref TEXT,
  shadow_since_season INT, shadow_since_tick SMALLINT, set_by TEXT NOT NULL, set_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (league_id, feature));
CREATE TABLE personality_feature_mode_changes (
  id BIGSERIAL PRIMARY KEY, league_id BIGINT NOT NULL, feature TEXT NOT NULL,
  from_mode TEXT, to_mode TEXT NOT NULL, version TEXT NOT NULL, review_ref TEXT, force_reason TEXT,
  at_season INT NOT NULL, at_tick SMALLINT NOT NULL, set_by TEXT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
```

```sql
CREATE TABLE personality_feature_clearances (
  league_id BIGINT NOT NULL, feature TEXT NOT NULL, version TEXT NOT NULL,
  source_db TEXT NOT NULL, source_label TEXT NOT NULL,          -- e.g. madden_tune_2026a, CAREER-TUNE-2026a
  shadow_from_season INT NOT NULL, shadow_from_tick SMALLINT NOT NULL, shadow_to_season INT NOT NULL, shadow_to_tick SMALLINT NOT NULL,
  report_path TEXT NOT NULL, report_sha256 TEXT NOT NULL, config JSONB NOT NULL,
  cleared_by TEXT NOT NULL, cleared_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (league_id, feature, version));
```

Keyed by `league_id`, like the producers' tables. The migration inserts no rows, so every feature starts `off`. It is transactional and refuses an incompatible table. A clearance carries the config tuned on the tuning franchise, so the real franchise goes active with the same settings.

### 2.3 League office (`pipeline/league_office_personality.py`, `pipeline/league_office.py`)

```python
class PersonalityDeps(Deps):
    def __init__(self, cur, league_id, snapshot_modes): ...
    def resign_decisions(self, cur, season, seed): ...       # + contract_inputs when contracts_* != off
    def run_wave(self, players, offers, wave, seed): ...      # + contract_inputs
    def udfa_market(self, prospects, needs, seed): ...        # + contract_inputs
    def progression_checkpoint(self, *args, **kw): ...       # + development_context/config when development_learning != off
    def chart_adjustments(self, cur, team_id, season, week) -> dict | None   # development_opportunity active only
    def retirement_decision(self, cur, player, season, seed) -> tuple[bool, str]
def deps_for(cur, snapshot) -> Deps                           # PersonalityDeps if any feature beyond producers is non-off, else Deps()
```

- **Shadow:** each feature runs its baseline and adjusted paths. The baseline's result goes to the league office unchanged, and the adjusted result is recorded in that feature's receipts. **Active:** the adjusted result is used. That way the contract plan's "off/shadow executes original arithmetic exactly" holds through the live path.
- **`resign_decisions`, `run_wave`, `udfa_market`:** build `contract_inputs` with the contract plan's `personality_contract_context.load_contract_context` per player, from one frozen batch ID per stage write (`'<run_id>:<stage>:<wave>'`). The contract plan's policy mode is the switchboard's `contracts_ambition` / `contracts_relationships` modes.
- **`retirement_decision`:**
  - `off`: returns `keep_retired(...)` unchanged.
  - `shadow`: returns `keep_retired(...)` and records the retirement policy's decision for the same player.
  - `active`: returns the retirement policy's decision for Madden-retired players, mapped to keep or retire.

  Retiring a player Madden kept active is never produced here (S3).
- **`chart_adjustments`:** uses the development plan's `opportunity_adjustments` with the team's coach view. It returns `None` unless `development_opportunity` is active, so a shadow chart is computed, recorded and not used.
- **Seams in `league_office.py`:**
  - `run()` and `plan_stage()` use `deps_for(cur, snapshot)` when no `deps` is passed;
  - `step_retirements` calls `deps.retirement_decision` instead of `keep_retired` directly, and `Deps.retirement_decision` keeps today's behaviour;
  - `step_coach_charts` passes `deps.chart_adjustments(...)` into the chart call;
  - `Deps.chart_adjustments` returns `None`.
- **Recovery:** the frozen mode snapshot goes into `summary.operation`, so `--recover` replays with the same modes.

### 2.4 Trade requests and the GM engine

**Watcher step.** `step_trade_requests(season, tick)` runs after the producers and evolution steps on every ingest where `trade_requests` isn't off. For each player it:
1. builds the trade-requests plan's `RequestContext` from the relationships plan's incidents and the core view;
2. builds its `RequestClock` from the tick and the GM window (`gm_trade_engine`'s window functions);
3. calls `record_decision` and `apply_action` through its store.

In shadow, decisions go to the plan's offline report path (its shadow mode persists nothing). In active mode they persist.

**GM handoff.** `engines/gm_trade_engine.py` `run_week` gains an optional `request_signals=None`. When `trade_requests` is active:
- the requesting team's GM adds the player to the players it is willing to shop, with its normal valuation, legality and acceptance;
- no price change, no deadline, and no forced trade;
- a user team (from the registry's `user_teams`) gets no automatic shopping, only the inbox card the inbox plan shows.

`step_gm` passes `request_signals` from the trade-requests store.

**Flags.** The producers step and the evolution step read `features.mode(...)` (`producers`, `evolution`) instead of their environment variables. The environment variables stay as a kill switch: when set to `off`, the step is off whatever the switchboard says.

### 2.5 CLI

```text
python -m personality.features show [--dsn DSN]                     # each feature: mode, version, shadow since, next allowed step and why
python -m personality.features set FEATURE MODE --version V --review TEXT [--config PATH] [--force REASON] [--dsn DSN]
python -m personality.features history [--feature F] [--dsn DSN]
python -m personality.features export-clearance FEATURE --label CAREER-TUNE-<run> --out PATH --dsn <tuning DB>
python -m personality.features import-clearance PATH [--dsn DSN]
python -m personality.features activate-cleared --review TEXT [--dsn DSN]   # real franchise, before 2026 week 1
```

These read `DATABASE_URL` or `--dsn` and refuse `madden_2036` and `postgres`. `show` lists each feature's latest shadow report path, which each feature plan writes. Exit codes: 0 ok, 2 bad request, 4 refused by a rule.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Shadow doubles the work in a league-office write (baseline plus adjusted paths). | Time a synthetic stage plan with every feature in shadow. | Shadow computes adjusted results only for the batch's players with stored profiles; others are skipped. |
| R2 | `--recover` replays a run with different modes. | The mode snapshot in `summary.operation`; a recovery test. | Recovery uses the stored snapshot, never the current switchboard. |
| R3 | The feature plans' interfaces shift before this plan builds. | This plan builds last, after every feature unit it wires. | `PersonalityDeps` adapts to the merged signatures; no feature logic moves here. |
| R4 | The GM engine's search changes in the trade-talks plan (V2.11), which also edits `gm_trade_engine.py`. | Build order: trade talks first. | U3 re-reads the engine and adds only the optional signal. |
| R5 | An active feature misbehaves mid-season. | Each feature's receipts and reports. | `set ... off` takes effect at the next write. Already-written moves stay; undoing them means a weekly-freeze restore (§6.0b), as the feature plans say. |

**Spike first:** none. The league-office seams were checked (S1–S5).

## 4. Tests (synthetic, no live DB)

- `tests/test_personality_features.py`:
  - a missing row means `off`;
  - the order rule (D2) refuses and allows correctly, and `profiles` is active once a lifecycle batch is applied;
  - the shadow-season rule (D3) at the boundary (tick 24 of the previous season);
  - an imported clearance meets D3 for its feature and version only, and a tampered report hash is refused;
  - export refuses a tuning franchise without a full shadow season;
  - `activate-cleared` follows the order and stops at the first uncleared feature;
  - `force_reason` is recorded, and going `off` is always allowed;
  - every change writes history; the snapshot is stable;
  - CLI exit codes and the DSN guard;
  - opt-in scratch DSN: the migration twice, and an incompatible table refused.
- `tests/test_league_office_personality.py`:
  - `deps_for` picks `Deps` when everything is off;
  - with fake feature modules:
    - shadow returns exactly the baseline for re-sign, free agency, UDFA, charts and retirement, and records the adjusted result;
    - active returns the adjusted result;
  - `retirement_decision` never retires an active player;
  - the frozen mode snapshot is stored and reused by recovery;
  - the existing `tests/test_league_office.py` still passes unchanged.
- `tests/test_personality_trade_request_wiring.py`:
  - the watcher step runs only when the switchboard allows it, and never fails the ingest;
  - in active mode the GM engine shops a requesting player at an unchanged valuation, and never for a user team;
  - in shadow mode `run_week` sees no signals;
  - the environment kill switch wins.

Run with `pytest -m "not integration" tests/test_personality_features.py tests/test_league_office_personality.py tests/test_league_office.py tests/test_personality_trade_request_wiring.py`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Switchboard

- **Owns:** `personality/features.py` (new), `db/migrations/2027_01_31_personality_feature_modes.sql` (new), `tests/test_personality_features.py` (new)
- **Reads:** this plan §2.1–§2.2, §2.5; the lifecycle plan §2.2 (`persona_lifecycle_batches`)
- **Depends on:** none
- **Done when:** the §2.1 functions and the CLI work with the order and shadow rules; history is written.
- **Verify:** `pytest -m "not integration" tests/test_personality_features.py -q` passes; `python -m personality.features --help` exits 0.

### U2: League office wiring

- **Owns:** `pipeline/league_office_personality.py` (new), `pipeline/league_office.py`, `tests/test_league_office_personality.py` (new)
- **Reads:** U1; `pipeline/league_office.py:822–930`, `:1330–1420`, `:1524–1580`, `:1646`, `:1665`, `:2000`; the merged contract, development and retirement modules
- **Depends on:** U1. Cross-plan: the contract plan's U3, the development plan's U3–U4 and the retirement plan's U1–U4 are merged.
- **Done when:** `PersonalityDeps` and `deps_for` work as §2.3; shadow equals the baseline exactly; the existing league office tests pass.
- **Verify:** `pytest -m "not integration" tests/test_league_office_personality.py tests/test_league_office.py -q` passes.

### U3: Trade-request scheduling, GM handoff and flags

- **Owns:** `pipeline/watch_save.py`, `pipeline/weekly_capture.py`, `engines/gm_trade_engine.py`, `tests/test_personality_trade_request_wiring.py` (new)
- **Reads:** U1; the trade-requests plan §2.1–§2.6; `pipeline/weekly_capture.py:381–420`; `engines/gm_trade_engine.py` (`run_week`, the windows); the producers and evolution watcher steps
- **Depends on:** U1. Cross-plan: the trade-requests plan's U1–U2, the producers plan's U3, the evolution plan's U3 and the trade-talks plan are merged.
- **Done when:** the watcher step, the GM signal and the switchboard-driven flags work as §2.4.
- **Verify:** `pytest -m "not integration" tests/test_personality_trade_request_wiring.py tests/test_watch_save.py -q` passes.

## 6. Waves

| Wave | Units | Needs merged first | Why |
|---|---|---|---|
| 1 | U1 | — | Everything reads the switchboard. |
| 2 | U2, U3 | U1 plus their cross-plan units | Disjoint files; both read U1. |

This plan builds last among the personality plans, because it wires their merged code. Building waits until every personality sub-plan is approved (JR, 2026-11-03).

After the build, before the real league's 2026 week 1:
1. On a tuning franchise, move each feature through `off → shadow` in the D2 order and sim at least one full season (`/tune` and `/madden` already drive `CAREER-TUNE-*` sims).
2. JR reviews each feature's shadow report and sets its config.
3. Export the clearances from the tuning database and import them into the real franchise.
4. Run `activate-cleared` there.

**File ownership check:**

| File | Unit |
|---|---|
| `db/migrations/2027_01_31_personality_feature_modes.sql` | U1 |
| `engines/gm_trade_engine.py` | U3 |
| `personality/features.py` | U1 |
| `pipeline/league_office.py` | U2 |
| `pipeline/league_office_personality.py` | U2 |
| `pipeline/watch_save.py` | U3 |
| `pipeline/weekly_capture.py` | U3 |
| `tests/test_league_office_personality.py` | U2 |
| `tests/test_personality_features.py` | U1 |
| `tests/test_personality_trade_request_wiring.py` | U3 |

No path appears twice. `pipeline/watch_save.py` is also owned, earlier, by the producers and evolution plans' U3s. `engines/gm_trade_engine.py` is also owned by the trade-talks and inbox plans. Each is built in sequence, never in parallel.

## 7. Spec changes when built

- §1.1 Repository Layout: `personality/features.py`, `pipeline/league_office_personality.py`, the migration, the three tests and this plan's entry.
- §1.2: `python -m personality.features` commands.
- §2.21a:
  - the deps factory, and `PersonalityDeps` passing contract, development, chart and retirement inputs by mode;
  - shadow keeps the baseline;
  - the mode snapshot in `summary.operation`.
- §2.17 (trade value / GM engine): the optional request signal.
- §3.3p: the three switchboard tables.
- §6.0b step 5: the trade-request step; the switchboard replacing the environment flags (kept as kill switches).
- §7.11: the go-live order, the shadow-season rule, tuning-franchise clearances and the switchboard.
- Other plans, at approval:
  - the contract plan: drop `contract_policy_activation` for the switchboard (its U2 and §2.5), and correct S-note "no implemented league-office runner" (S2 here);
  - the retirement plan: correct S3, and note the existing `keep_retired` path;
  - the producers and evolution plans: modes from the switchboard;
  - the relationships plan: its `mode` comes from the switchboard;
  - the trade-requests plan: G4 points here;
  - PERSONALITY_PLAN §5.3: a tracker row.

## 8. Decisions for JR

JR approved all three recommendations on 2026-11-03.

| # | Question | Decision (JR, 2026-11-03) |
|---|---|---|
| Q1 | Is the go-live order right: producers, relationships, profiles, evolution, contracts (ambition, then relationships), development learning, trade requests, retirement, depth-chart opportunities? | Yes, on the tuning franchises. Facts and profiles first, then money decisions (easiest to inspect), then development, then features that move players (requests, retirement, opportunities) last. On the real franchise, everything cleared switches on together at 2026 week 1. |
| Q2 | When a request is active, may the CPU GM shop the player? | Yes, at his normal value, through the usual legality and acceptance checks, and never on your teams. A request is a reason to listen to offers, not a discount. |
| Q3 | In active retirement mode, may the policy only decide about players Madden retired (keep or let go), not retire active players? | Yes, until the retirement plan's own proof for retiring an active player passes (its Q1). Then it adds that action through its own plan. |
