# Project Mu: Relationship Event Producers Plan

**Status:** approved by JR (2026-11-03), Q1–Q3 settled as recommended (§8); built and on main (2026-11-04), off by default: nothing applied to or run on a franchise database (migration `2027_01_28` not applied; the hook waits for `MU_RELATIONSHIP_PRODUCERS=on` after JR reviews a preview and a `catch-up --dry-run`, §6 gate).
- **Spike (2026-11-03):** WRITTEN `league_office_runs.edits` hold `release` (pgids), `sign` (`{pgid, team_index, list, contract}`) and `trade` (`{from_team_index, to_team_index, pgids}`); re-signs and extensions are only `players` contract-field edits, so they come from `player_contracts` as planned. pgids map to `players.madden_pgid` and team indexes through the run's `summary.operation` `team_ids`. `stage_and_wave` (`pipeline/league_office.py:268`) maps every offseason stop, PRESEASON and CUTDOWN included. A bye is a `games` row with `game_type` or `status` `BYE`. No change to §2.
- **As built (wave 1):** `store.py` keeps `event_hashes` values as `{"hash", "ref"}`, not a bare hash, because a null retraction needs the vanished event's subject, target, time and visibility. Correction IDs are `<orig>#correction:<hash16>` / `<orig>#retract:<hash16>`, and a later correction supersedes the one in effect. `rules.py` exposes `facet_key(env)` (`<role>:<namespace>:<external_id>|<team_key>|<kind>`), but `store.py` derives its own facet when an envelope has no `facet_key` field. U3 must set `env["facet_key"] = rules.facet_key(env)` on every envelope so both use one key. `rules.py` adds payload fields beyond §2.2 (`eligible`, `ineligible_reason`, injury, award and result details); `established_role` counts a higher tier as holding the lower ones; a streak fires on the game that reaches 3; healing uses source ID `mu:injury:<id>:resolved`. The migration's rerun check compares columns, types, nullability, primary key and the index; it has not yet run on PostgreSQL (its integration test is opt-in). **Known bug (found 2026-11-03):** `rules.coordinator_alias` used role `coordinator`, which the core lacks; fixed in wave 2 (below).
- **As built (wave 2, U3; built by the orchestrator in its own worktree, no subagents):**
  - Fixes outside U3's Owns, placed in U3 because no wave-2 unit owned them (orchestrator's instruction): `rules.coordinator_alias` is role `coach`, namespace `db-coordinator`. `rules.envelope` puts only rows an occurrence owns outright into `source_refs` (an injuries, awards, staff_moves or hot-seat row, a re-sign's new contract; new `own_refs` argument); shared evidence (roster, depth chart, game, league-office run, cause row, superseded contract) is in `attribution.evidence_refs` only, because the relationships store refuses a batch where one external ref names two occurrences, which wave 1's refs did on every real batch. `store.append_or_hold` and `flush_pending` read `receipt['rejected']`: `unknown_identity` rejections are held PENDING (retried by every flush), other contract rejections stored DROPPED with the reason; neither is counted as appended, and duplicates are reported separately. Corrections own no source ref (a correction chain would reuse one), the original goes in evidence refs. Tests for these are in the rules and store test files.
  - Watcher gate: the activation plan's switchboard isn't built, so the step is gated on `MU_RELATIONSHIP_PRODUCERS=on` alone (the switchboard replaces it later). The SAVE roster comes from the snapshot (`sources.roster_from_snapshot`, as the loader places players), because `player_seasons` keeps stale teams for players who left the save; `run` from the CLI reads `player_seasons` (minus retired players).
  - Timing rules so a rerun reproduces every event: tenure, role, moves and recognitions at the run's tick; injury starts and heals, streaks, contract years and season outcomes when their tick is in the run's window (the ticks after the previous producer run); awards at (season, 23) and hot-seat pressure at 19 (Black Monday) / 23 (playoff exit) on every later run (repeats are store duplicates); a coach's LEFT at his hot-seat phase tick and HIRED at 24. Recognitions come from ACTIVE contracts above the previous run's `player_contracts` id watermark (in `counts.watermarks`); the catch-up has no watermark and produces none.
  - Catch-up replays only in-season ticks that have MADDEN charts (offseason stops have no stored per-stop data); membership is chart membership, then the week's injuries rows; a player missing from a catch-up tick is not a move. `run_tick` refuses a CATCHUP run over a SAVE run, and `catch_up` refuses any range with a SAVE run at or before `through`. `record_failure` never overwrites a good run's row (its `event_hashes` drive later corrections). The run needs both the producer and relationships tables (exit 3 / the watcher skips otherwise).
**Builds on:** [`PERSONALITY_RELATIONSHIPS_PLAN.md`](../done/PERSONALITY_RELATIONSHIPS_PLAN.md) §2.1–§2.2 (the event envelope and store this plan feeds), [`PERSONALITY_DRAFT_INTEGRATION_PLAN.md`](../done/PERSONALITY_DRAFT_INTEGRATION_PLAN.md) §2.1–§2.2 (universe and alias identity), [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) §4.6–§4.7 and §5.3; PROJECT_SPEC §2.17c (depth charts), §2.19 (coaching carousel), §2.21a (league office), §3.3b (injuries), §3.3e (transactions), §3.3m (season ledger), §6.0b (save watcher).

**Goal:** Give the relationships plan its main inputs. Today only the V2.9 inbox and V2.2 press conferences will record relationship events, so a player's trust, attachment and mood would come only from conversations. This plan turns what actually happens in the simulated league (who is on which team, who starts, who gets cut, re-signed, hurt, honoured, fired or hired) into the relationships plan's canonical events, every week and every offseason stop. Every outcome comes from fixed, versioned rules, with no model calls. It feeds trade requests (lost role, treatment), contract loyalty and retirement (tenure, treatment), and mood (situations).

**Delivers:**
- Package `engines/relationship_producers/`: `calendar.py`, `rules.py`, `sources.py`, `store.py`, `run.py`, `__main__.py`.
- Migration `db/migrations/2027_01_28_relationship_producers.sql` (folded into `db/init_db.sql`): tables `relationship_producer_runs`, `relationship_roster_observations`, `relationship_pending_events`.
- A watcher step `step_relationship_producers` in `pipeline/watch_save.py`, after the load, on in-season weeks and offseason stops alike. Off unless `MU_RELATIONSHIP_PRODUCERS=on`.
- CLI: `python -m engines.relationship_producers run|catch-up|flush|preview`.

**JR's answers (2026-11-03):**
- D1: Fixed rules decide whether a fact felt good or bad (outcome and significance), and each event records the rule as its reason. There are no model calls, so every event can be replayed exactly. The inbox and press conferences keep their own model appraisals.
- D2: A lost role is a demotion only: a player held a role for several healthy weeks and was then moved down while healthy. A player rated above the starter who still doesn't start is recorded as a fact, not as unhappiness, in v1.
- D3: When the producers come online mid-league, a one-time catch-up rebuilds events from the franchise's stored weekly data, back to its first save. It uses only real simulated facts, never the HISTORY backfill rows.
- D4: Events for a coach (fired, hired, promoted) who has no identity yet are held in a pending queue and appended once the lifecycle plan registers him. Players without a registered identity wait in the same queue.

---

## 0. Survey (code facts that set the scope)

Line numbers in `pipeline/watch_save.py` are from the working tree on 2026-11-03, which has uncommitted edits by another session.

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | The relationships plan defines the envelope (§2.2: `source_event_id`, `kind`, `occurred_at {season,tick}`, `subject`, `target`, `actor`, `basis`, `confidence`, `visibility`, `attribution`, `payload`) and the store `append_events(cur, events, *, expected_head, team_registry)` (§2.1). Its §1 Out lists "production ingestion/scheduling". | This plan owns that ingestion. It builds envelopes and appends through that store; it does not define trust. |
| S2 | `pipeline/watch_save.py:640` runs `load_save.load`. `:667` returns early on an offseason stop (`stage_only`: load and league office only). `:684` runs `run_weekly_engines` only on in-season weeks. | The hook goes right after the load, before the `stage_only` branch, so offseason moves (re-signs, free agency, cuts) produce events too. |
| S3 | `db/init_db.sql:93–117` `player_seasons` is unique on `(player_id, season)`, and the loader overwrites `team_id` on every load (`pipeline/load_save.py:421–423`). `released_week` marks an in-season release. | Nothing stores which team a player was on each week. This plan keeps its own weekly observation (`relationship_roster_observations`). |
| S4 | `pipeline/load_save.py:931–960` stores the save's depth chart for every team each in-season week as `depth_charts` source `MADDEN`, keyed by week (`db/init_db.sql:566–575`). Outside the regular season and playoffs it skips the chart (`:939`). | Weekly role history exists for in-season weeks, both going forward and for the catch-up. Offseason ticks have no role facts. |
| S5 | `engines/depth_chart_engine.py:309` `starter_counts(scheme)` (WR 3, CB 2, DT 2/1, MLB 1/2) and `:380` `starters_by_base_slot(chart, scheme)`; `BASE_SLOTS` / `SITUATIONAL_SLOTS` at `:48–53`. | Role tiers reuse the engine's own idea of a starter. No second starter rule. |
| S6 | `db/init_db.sql:318–334` `injuries` has weekly rows (`weeks_remaining`, `is_ir`, `mu_weeks_out`). PROJECT_SPEC §3.3b documents Madden's IR bug: a healed player stays on IR with `injury_type` NONE, `weeks_remaining` 0 and `is_ir` true, and never plays again. | Injured weeks and bug-stuck-IR weeks don't count as eligible role observations (so they never count as a demotion). Only a real injury produces an `injury` situation. |
| S7 | `pipeline/league_office.py:1784` `record_written` stores pending-move statuses, charts, injury durations, `retirement_decisions` and offer statuses after a write. It writes no `transactions` rows. CPU releases and signings live only in `league_office_runs.edits` (WRITTEN) and `franchise_pending_moves`. `pipeline/trade_moves.py:160` does insert trades into `transactions`. | A move counts once the next save's roster shows it (the relationships rule: only verified executed movement). The cause comes from the last WRITTEN run's edits, from JR's WRITTEN pending moves, or from a `transactions` row. A roster change with no known cause is still a tenure fact but never a treatment. |
| S8 | `db/init_db.sql:510–530` `transactions.type` includes `RELEASED`, `TRADED`, `RE_SIGNED`, `EXTENDED`, `SIGNED`, `RETIRED`; `player_contracts.status` / `source` (`:438–456`) mark superseded and transaction-made contracts. | Re-signs and extensions show up as a new ACTIVE contract replacing a SUPERSEDED one on the same team, which also covers league-office re-signs that have no `transactions` row. |
| S9 | `db/init_db.sql:1551–1567` `player_season_transitions` holds each player's status between seasons (`RETAINED`, `MOVED`, `RETIRED`, `FREE_AGENT`, `DRAFTED`, `NEW`) and its source. | The catch-up reads season boundaries here instead of re-deriving them. |
| S10 | `db/init_db.sql:1852–1898` `coach_hot_seat` and `coordinator_hot_seat` (score, `fired`, phase) and `staff_moves` (`HIRED`, `LEFT`, `REPLACED`; source `POACHED`, `PROMOTED`, `RETREAD`, …); `coach_tenures` at `:651`. `engines/coaching_carousel.py:38` `FIRST_SEASON = 2026`. | These are the coach facts. They are held until coaches have identities (D4). |
| S11 | `db/init_db.sql:1387` `awards` (player or coach, `source`); `team_seasons` and `games` hold results. | Award and team-result situations. |
| S12 | `pipeline/league_office.py:181` `STAGE_WEEKS` (OFFSEASON_START 24 … PRESEASON 32, plus COMBINE 27 and PRO_DAY 28) and `:264` `detect_stage(snapshot)`; `engines/contracts_engine.py:38` says a season's offseason comes after its week 23. | Tick = the league-state week. Weeks 1–23 are games and 24–32 are offseason stops, both counted in the season just played: 32 ticks per season. |
| S13 | `personality/store.py:136` `resolve_alias(cur, universe_id, role, namespace, external_id)`. The lifecycle plan binds `db-player` / `db-coach` / `db-coordinator` aliases and registers `persona_lifecycle_universes` by `league_id` (§2.1–§2.2). | Subjects resolve through those aliases. An unresolved subject is held (D4), never guessed. |
| S14 | `grep -i 'home(town\|state\|city)\|birth'` finds nothing in `pipeline/save_reader/pc_save_reader.mjs` or `pipeline/load_save.py`. | No source for `home_familiarity`. Out of scope. |
| S15 | `tests/test_relationships.py` tests the newsroom's writer biases, and the relationships plan's own tests are `tests/test_personality_relationships.py`. No `engines/relationship_producers*` or `tests/test_relationship_producer*` exists. Latest reserved migration date is `2027_01_27` (PERSONALITY_PLAN §5.3). | New names are free. Migration `2027_01_28`. |
| S16 | The inbox plan (D16) resolves role-talk promises "from the actual depth chart" itself. | This plan doesn't emit `promise_resolved`. It publishes the role fact's `source_event_id` so the inbox can cite it in `outcome_refs`. |

## 1. Scope

**In:**
- Weekly roster observation for every player in the save, and `tenure_window` events per player and team.
- Weekly role tier from the MADDEN depth chart, and `role_window` events with a rule-made assessment (D2).
- `treatment` events for releases (dismissal) and re-signs or extensions (recognition), with the cause attributed only when it is known.
- `situation` events: arrival and departure (signing, trade, draft, release, expiry), injury, award, team result (season outcome and 3-game streaks), and job pressure (a player's contract year).
- Coach facts (fired, hired, promoted, hot-seat survival) as held events (D4).
- A pending queue for unresolved subjects, flushed when identities appear.
- Idempotent runs per tick, corrections when a tick's inputs change, and the one-time catch-up (D3).
- An offline preview over a fixture.
- The watcher hook, off by default.

**Out (non-goals):**
- Trust, attachment, mood and their numbers: the relationships plan.
- `promise_made` / `promise_resolved`: the inbox (S16).
- `meaningful_interaction`: conversations and pressers produce those, and nothing in the simulation shows mentoring actually happened (development plan S11).
- `home_familiarity`: no source (S14).
- Players reacting to a new head coach: the relationships contract has no situation kind for "my team changed coaches", so it needs a schema-version change there first.
- Lowball offers, cap casualties after a discount, and positional-rank demands: deferred in PERSONALITY_PLAN §12.
- GM and owner subjects (no core identities, phase 6).
- Writing the save, or changing any engine's decisions.
- HISTORY-origin seasons and players (D3).

## 2. Design

### 2.1 Calendar and identity (`calendar.py`, `rules.py`)

```python
TICKS_PER_SEASON = 32
def tick_for(snapshot: dict, league_state_week: int | None) -> int   # in-season: league_state week after the load; offseason: league_office.STAGE_WEEKS[detect_stage(snapshot)]
def calendar(seasons: list[int]) -> dict                             # {season: 32}, the relationships plan's calendar input
def time_point(season: int, tick: int) -> dict                       # {"season": .., "tick": ..}
```

Subjects and targets:
- **Player:** `{"role": "player", "namespace": "db-player", "external_id": str(players.id)}`.
- **Coach:** `db-coach` / `coaches.id`.
- **Coordinator:** `db-coordinator` / `coordinators.id`.
- **Team:** `TeamRef` with `team_key = "team:<teams.id>"`. `teams.id` is stable inside a franchise database and survives relocation.
- **Universe:** `persona_lifecycle_universes.universe_id` for `league_state.madden_league_id`.

`team_registry(cur, universe_id) -> dict` returns `{team_key: {"team_id", "abbreviation"}}` from `teams`. Its hash goes into each run, and it is the trusted registry `append_events` needs.

Every source event ID is deterministic, so a rerun reproduces it and other producers can cite it:

| Event | `source_event_id` |
|---|---|
| tenure | `mu:tenure:<season>:<tick>:<player_id>` |
| role | `mu:role:<season>:<tick>:<player_id>` (published as `role_source_id(season, tick, player_id)` for the inbox) |
| release | `mu:release:<season>:<tick>:<player_id>` |
| re-sign / extension | `mu:contract:<player_contracts.id>` |
| arrival | `mu:arrive:<season>:<tick>:<player_id>:<team_id>` |
| departure | `mu:depart:<season>:<tick>:<player_id>:<team_id>` |
| injury | `mu:injury:<injuries.id>` |
| award | `mu:award:<awards.id>` |
| team result | `mu:result:<season>:<team_id>:<kind>:<tick>` |
| contract year | `mu:contract-year:<season>:<player_id>` |
| coach | `mu:staff:<staff_moves.id>`, `mu:hotseat:<season>:<team_id>:<role>` |

Producer-owned source refs use namespace `mu-producer` (`{namespace: "mu-producer", key: <table>:<id>}`).

### 2.2 Rules (`rules.py`, pure, `PRODUCER_RULES_VERSION = "producers-v1"`)

All rules take plain dicts and return envelopes or `None`. Constants are review-only estimates (§8).

**Role tier for one player-week** (`role_tier(chart, scheme, player_id) -> 'STARTER'|'ROTATION'|'RESERVE'`):
- STARTER: in `starters_by_base_slot(chart, scheme)` for any base slot.
- ROTATION: depth 1 in a situational slot (`SLWR`, `3DRB`, `PWHB`, `SUBLB`, `SLCB`, `RLE`, `RRE`, `RDT`, `NT`), or the first player after the starters in a base slot. `KR`, `PR`, `KOS` and `GAD` are ignored.
- RESERVE: on the roster but neither of those.
- The scheme is the team's head coach's `offensive_scheme` / `defensive_scheme` that season.

**Eligibility** (`eligible(obs) -> (bool, reason)`). A week is not eligible when:
- the player has an `injuries` row that season and week with `weeks_remaining > 0` or `is_ir` (the Madden stuck-IR case included, S6);
- the team had a bye;
- his team has no MADDEN chart that week;
- it is his first week on this team;
- it is an offseason tick.

**Established role** (`established_role(history) -> tier | None`): the highest tier he held for `ROLE_ESTABLISHED_WEEKS = 4` consecutive eligible weeks in his current tenure on this team. Ineligible weeks pause the count without breaking it. At a season boundary it resets to the tier he held for the most eligible weeks of the season just ended. A new team starts with none.

**`role_window`**, one per player per in-season tick on a team:
- Payload: `{team_key, start: {season,tick}, end: {season,tick+1}, agreed_role: established or null, observed_role: tier, assessment, assessment_basis_refs: [chart and injury refs]}`.
- `assessment`: `satisfied` when eligible and the tier is at or above the established role; `unsatisfied` when eligible and below it (D2); `unknown` when ineligible or nothing is established.
- The payload also records `rating_rank_at_position` (his OVR rank among the team's players at his position) as a fact. It never sets the assessment (D2).
- Visibility `private`: the chart is public, but whether it felt like a demotion is the player's own.

**`tenure_window`**, one per player per tick while he is on a team: `{team_key, start, end, coverage}`. Coverage is `complete` from the save, and `partial` in the catch-up when membership is inferred (§2.5). Visibility `public`.

**Moves**, from consecutive roster observations, with the cause looked up in order: `transactions` row, the last WRITTEN `league_office_runs.edits` entry, then JR's WRITTEN `franchise_pending_moves`.

| Observed change | Events | Outcome / significance (D1) |
|---|---|---|
| On team T, then a free agent, with a known release | `treatment` dismissal toward T; `situation` departure (negative) | negative / meaningful |
| On T, then a free agent at contract expiry (no release) | `situation` departure (uncertain) | no treatment: an expiring deal isn't mistreatment |
| On T, then on U, cause trade | `situation` departure from T (uncertain), arrival at U (uncertain) | no treatment (relationships §2.2: a trade doesn't prove misconduct) |
| Free agent or draft, then on U | `situation` arrival (positive for a draft pick or an above-minimum signing, else uncertain) | — |
| New ACTIVE contract on the same team replacing a SUPERSEDED one | `treatment` recognition toward T | positive / meaningful |
| Roster change with no known cause | tenure facts only, flag `cause_unknown` | none |

`attribution.responsible` is the team (`TeamRef`). For a JR-written move it also records `reason_code: 'JR_MOVE'`. GMs have no core identities, so no person is blamed.

**Situations:**
- `injury`: a new `injuries` row with `weeks_remaining >= INJURY_SITUATION_WEEKS = 2`, or IR with a real `injury_type`. State `negative`, `valid_until` the expected return tick. Healing emits `resolved`.
- `award`: an `awards` row (positive, public).
- `team_result`:
  - a streak of `STREAK_GAMES = 3` wins or losses, `valid_until` the next game;
  - the season outcome (missed playoffs, made playoffs, lost in round N, won the Super Bowl), `valid_until` the start of next season;
  - subjects are every player on the team that tick.
- `job_pressure`: an ACTIVE contract with `years_remaining = 1` at the season's first tick. State `uncertain`, valid until RESIGN (tick 25).

**Coach facts** (held until identity, D4):
- `staff_moves` LEFT after a hot-seat `fired` row: `treatment` dismissal toward the team.
- HIRED: `situation` arrival.
- PROMOTED: `treatment` recognition.
- A `coach_hot_seat` row kept with score ≥ `HOT_SEAT_PRESSURE = 0.5` of the firing line: `situation` job_pressure.

Every envelope: `basis = 'verified_simulation'`; `confidence` `high` (save-observed) or `medium` (catch-up inference); `schema_version` from the relationships plan; `payload.rule = '<rule name>@producers-v1'` as the stated reason (D1).

### 2.3 Tables (`2027_01_28_relationship_producers.sql`)

```sql
CREATE TABLE relationship_producer_runs (
    league_id BIGINT NOT NULL, season INT NOT NULL, tick SMALLINT NOT NULL CHECK (tick BETWEEN 1 AND 32),
    rules_version TEXT NOT NULL, source VARCHAR(8) NOT NULL CHECK (source IN ('SAVE','CATCHUP')),
    input_hash TEXT NOT NULL,          -- sha256 of the canonical inputs read for this tick
    registry_hash TEXT NOT NULL,
    event_hashes JSONB NOT NULL,       -- {source_event_id|facet: payload_hash} appended or held
    counts JSONB NOT NULL, status VARCHAR(10) NOT NULL CHECK (status IN ('APPENDED','HELD','NOOP','FAILED')),
    error TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (league_id, season, tick, rules_version));
CREATE TABLE relationship_roster_observations (
    league_id BIGINT NOT NULL, season INT NOT NULL, tick SMALLINT NOT NULL,
    player_id INT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    team_id INT REFERENCES teams(id),  -- NULL: free agent
    source VARCHAR(8) NOT NULL CHECK (source IN ('SAVE','CATCHUP')),
    PRIMARY KEY (league_id, season, tick, player_id));
CREATE TABLE relationship_pending_events (
    league_id BIGINT NOT NULL, source_event_id TEXT NOT NULL, facet_key TEXT NOT NULL,
    subject_alias JSONB NOT NULL,      -- {role, namespace, external_id}
    envelope JSONB NOT NULL,           -- complete except subject.persona_id
    status VARCHAR(10) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','APPENDED','DROPPED')),
    reason TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), appended_at TIMESTAMPTZ,
    PRIMARY KEY (league_id, source_event_id, facet_key));
CREATE INDEX relationship_pending_open ON relationship_pending_events (league_id, status);
```

Tables are keyed by `league_id`, not `universe_id`, so held events can exist before the lifecycle plan registers the universe. The migration is transactional and checks the definition when rerun. It seeds nothing.

### 2.4 Store and run (`store.py`, `run.py`)

```python
# store.py (caller owns the transaction; never commits or connects)
def read_run(cur, league_id, season, tick, rules_version) -> dict | None
def record_run(cur, run: dict) -> None
def save_observations(cur, league_id, season, tick, rows: list[dict], source: str) -> int
def append_or_hold(cur, envelopes: list[dict], *, league_id, universe_id: str | None, team_registry: dict) -> dict
    # resolves each subject alias; resolved → personality.relationship_store.append_events; unresolved → pending
def flush_pending(cur, league_id, universe_id, team_registry) -> dict   # appends held events whose subject now resolves, oldest first
# run.py
def run_tick(cur, season: int, tick: int, *, source='SAVE', rules=None) -> dict
def catch_up(cur, *, through: tuple[int,int], dry_run=False) -> dict
```

`run_tick` steps:
1. Read the inputs for the tick (`sources.py`) and hash them.
2. If a run with the same key and `input_hash` exists, return it (`NOOP`).
3. If the hash changed (a re-ingest after a correction), build the new envelopes and diff them against `event_hashes`. Changed or vanished events get relationships-plan `correction` events (`supersedes_source_event_id`); unchanged ones are skipped.
4. Save observations, run the rules, call `append_or_hold`, flush pending, record the run.

Everything sits inside one savepoint in the caller's transaction. A weekly-freeze restore rolls this plan's tables back together with the relationship ledger, because both are in the same dump.

**Watcher hook (`pipeline/watch_save.py`).** `step_relationship_producers(save_season, tick)` runs right after the load, before the `stage_only` branch. It opens its own connection and transaction, does nothing unless the switchboard's `producers` mode is on ([activation plan](../in_progress/PERSONALITY_ACTIVATION_PLAN.md); `MU_RELATIONSHIP_PRODUCERS=off` stays as a kill switch), and never fails the ingest: errors are logged, notified, and recorded as a FAILED run. It is skipped with a logged reason when the relationships store isn't migrated.

### 2.5 Catch-up (D3)

`catch_up` replays every tick from the franchise's first save (`min(season)` over `captures` with mode `save`) up to `through`, in order, with `source='CATCHUP'`.
- **Membership per week:** inferred from MADDEN `depth_charts` rows, then `injuries` rows (team that week), then `player_seasons` and `player_season_transitions` at season edges. Coverage is `partial` and confidence `medium`.
- **Players:** `players.origin` `HISTORY*` rows are skipped.
- **Order:** it refuses a tick that already has a SAVE run, so it never rewrites forward data. It has to run before the watcher hook is switched on, or over a range that ends before the first SAVE run.
- **Dry run:** `--dry-run` reports counts and writes nothing.

### 2.6 CLI (`__main__.py`)

```text
python -m engines.relationship_producers run --season S --tick T [--dsn DSN]
python -m engines.relationship_producers catch-up --through S:T [--dry-run] [--dsn DSN]
python -m engines.relationship_producers flush [--dsn DSN]
python -m engines.relationship_producers preview --fixture PATH --out PATH
```

- `run`, `catch-up` and `flush` read `DATABASE_URL` or `--dsn` and refuse `madden`, `madden_2036` and `postgres`, the same guard the watcher uses.
- `preview` is fully offline: fixture JSON in (charts, rosters, injuries, moves, contracts, awards, results), deterministic envelopes and per-rule counts out, no database import. Exit codes: 0 ok, 2 bad input, 3 store not migrated, 4 conflict.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Volume. One `tenure_window` and one `role_window` per rostered player per in-season tick is about 1,700 × 2 × 23 ≈ 78,000 events a season (estimate), about 700,000 by 2034. The relationships plan's full replay (its R5) may be slow. | The preview CLI on a synthetic 32-team season, timed through the relationships plan's projection. | Coalesce: emit a window when its tier or team changes and at each 4-week checkpoint, instead of every week. Same rules, fewer rows. |
| R2 | Release cause lookup misses league-office moves when a run's `edits` shape differs from what `sources.py` expects. | Spike: read `league_office.plan_record` and a synthetic WRITTEN run's `edits` for release, sign and re-sign entries. | The change stays a tenure fact with `cause_unknown`, never a guessed treatment. |
| R3 | Bye weeks and Madden's stuck-IR rows misread as demotions. | Fixtures for byes, real injuries, stuck IR and JR's manual activation. | Those weeks are ineligible, so they can only delay a demotion signal, never create one. |
| R4 | The catch-up's inferred membership wrongly puts a player on a team. | Compare catch-up and SAVE observations on a synthetic league with known moves. | `partial` coverage and `medium` confidence; consumers already treat partial coverage as unknown. |
| R5 | The relationships plan's U1 envelope or U4 store change shape before this plan builds. | Build after both merge; U3 imports them and its tests run against them. | Revise `rules.py` envelopes only; the rules themselves don't change. |
| R6 | `pipeline/watch_save.py` and `tests/test_watch_save.py` have uncommitted edits by another session, and other plans touch the watcher too. | `git status` before wave 2. | U3 re-reads the file after those land; the hook is a few lines in one place. |

**Spike first** (orchestrator, about an hour, before wave 1; source and synthetic only, no database): answer R2 (league-office `edits` entries for release, sign and re-sign), confirm `detect_stage` gives a tick for every offseason stop, and confirm how a bye shows in `games` (status `BYE`). R2's answer can change `sources.py` but not the interfaces in §2.

## 4. Tests (synthetic, no live DB)

- `tests/test_relationship_producer_rules.py`:
  - role tiers per scheme (4-3 and 3-4), and situational slots counting as rotation;
  - established role (4 weeks, pausing on injury, resetting at the season boundary and on a new team);
  - assessments: demotion only while healthy; a rated-above-starter backup stays `unknown`/`satisfied` (D2);
  - stuck-IR and bye weeks are ineligible;
  - every move row in §2.2 (release vs expiry vs trade, re-sign as recognition, unknown cause gives no treatment);
  - injury thresholds and healing, streaks, season outcomes, contract year, coach facts;
  - deterministic source IDs (`role_source_id` included); every envelope passes the relationships plan's `validate_event`; HISTORY players skipped.
- `tests/test_relationship_producer_store.py`: fake-cursor sequencing, plus opt-in scratch-schema cases on `madden_test`:
  - repeat migration and an incompatible existing table;
  - append versus hold by alias resolution, and flush once an alias appears, oldest first;
  - NOOP on an unchanged hash, and corrections for a changed tick;
  - rollback mid-batch, and no commit.
- `tests/test_relationship_producer_run.py`:
  - `run_tick` over a fake league for 2026 week 1 → offseason → 2027;
  - the catch-up refuses ticks with SAVE runs and matches SAVE output on overlapping synthetic data except for coverage and confidence;
  - preview output is byte-identical across runs, the CLI exit codes, and the DSN guard.
- `tests/test_watch_save.py` (existing; add cases):
  - the hook runs on a weekly ingest and on an offseason stop;
  - it is off without `MU_RELATIONSHIP_PRODUCERS=on`;
  - a producer exception never stops the ingest.

Run with `pytest -m "not integration" tests/test_relationship_producer_rules.py tests/test_relationship_producer_store.py tests/test_relationship_producer_run.py tests/test_watch_save.py`. Store integration cases need an explicit scratch DSN for `madden_test` and roll everything back.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

Cross-plan prerequisites (merged on main before wave 2):
- relationships plan U1 (`personality/relationship_events.py`) and U4 (`personality/relationship_store.py`, its migration);
- the lifecycle plan's U2 for real identities.

Without the lifecycle plan everything is held, which is valid.

### U1: Rules and calendar

- **Owns:** `engines/relationship_producers/__init__.py` (new), `engines/relationship_producers/calendar.py` (new), `engines/relationship_producers/rules.py` (new), `tests/test_relationship_producer_rules.py` (new)
- **Reads:** this plan §2.1–§2.2; `PERSONALITY_RELATIONSHIPS_PLAN.md` §2.1–§2.2; `engines/depth_chart_engine.py:39–53`, `:309–383`; `pipeline/league_office.py:181`, `:264`; `engines/contracts_engine.py:38`
- **Depends on:** none. It builds envelopes against the relationships plan's documented shape; validation against `validate_event` is skipped when that module isn't present.
- **Done when:** every rule in §2.2 maps synthetic inputs to the documented envelopes, deterministically, with no database import.
- **Verify:** `pytest -m "not integration" tests/test_relationship_producer_rules.py -q` passes.

### U2: Tables and store

- **Owns:** `db/migrations/2027_01_28_relationship_producers.sql` (new), `db/init_db.sql`, `engines/relationship_producers/store.py` (new), `tests/test_relationship_producer_store.py` (new)
- **Reads:** §2.3–§2.4; `personality/store.py:136`; the relationships plan §2.6 (append semantics, corrections)
- **Depends on:** none in this plan; the relationships plan's U4 for the append path. Store tests fake `append_events` when the module is absent.
- **Done when:** the migration applies twice cleanly on a scratch schema; append, hold, flush, NOOP and correction paths pass; no commits.
- **Verify:** `pytest -m "not integration" tests/test_relationship_producer_store.py -q`, then again with an explicit scratch DSN for `madden_test`; all pass, and nothing is left behind.

### U3: Sources, runner, catch-up, CLI and watcher hook

- **Owns:** `engines/relationship_producers/sources.py` (new), `engines/relationship_producers/run.py` (new), `engines/relationship_producers/__main__.py` (new), `tests/test_relationship_producer_run.py` (new), `pipeline/watch_save.py`, `tests/test_watch_save.py`
- **Reads:** U1 and U2; `pipeline/load_save.py:401–430`, `:931–960`; `pipeline/league_office.py:1784–1840`; `pipeline/watch_save.py:632–712`; `db/init_db.sql` (tables in §0)
- **Depends on:** U1, U2, plus the cross-plan prerequisites above
- **Done when:** `run_tick` and `catch_up` produce, hold and flush events over a fake league; the CLI works; the watcher runs the step after the load on weekly and offseason ingests, stays off by default, and never fails the ingest.
- **Verify:** `pytest -m "not integration" tests/test_relationship_producer_run.py tests/test_watch_save.py -q` passes; `python -m engines.relationship_producers --help` exits 0; `python -m engines.relationship_producers preview --fixture <test fixture> --out /tmp/x.json` produces the same file twice.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 0 | Spike (§3) | JR approval | Settles league-office edits, offseason ticks and byes. |
| 1 | U1, U2 | — | Pure rules and persistence share no files and build against §2. |
| 2 | U3 | U1, U2, relationships U1+U4 | Wires real sources and the watcher; one owner for `watch_save.py`. |

Gate after the build: JR turns the hook on (`MU_RELATIONSHIP_PRODUCERS=on`) and runs `catch-up` on a franchise only after reviewing a preview and a `catch-up --dry-run` report. Building this plan writes nothing to a franchise database.

**File ownership check:**

| File | Unit |
|---|---|
| `db/init_db.sql` | U2 |
| `db/migrations/2027_01_28_relationship_producers.sql` | U2 |
| `engines/relationship_producers/__init__.py` | U1 |
| `engines/relationship_producers/__main__.py` | U3 |
| `engines/relationship_producers/calendar.py` | U1 |
| `engines/relationship_producers/rules.py` | U1 |
| `engines/relationship_producers/run.py` | U3 |
| `engines/relationship_producers/sources.py` | U3 |
| `engines/relationship_producers/store.py` | U2 |
| `pipeline/watch_save.py` | U3 |
| `tests/test_relationship_producer_rules.py` | U1 |
| `tests/test_relationship_producer_run.py` | U3 |
| `tests/test_relationship_producer_store.py` | U2 |
| `tests/test_watch_save.py` | U3 |

No path appears twice. `db/init_db.sql` is also edited by the inbox, press conference, scouting, trade-talks, lifecycle, retirement and salary-cap-growth plans, so U2 re-reads it and merges after whichever lands first.

## 7. Spec changes when built

- §1.1 Repository Layout: `engines/relationship_producers/` and its modules, the migration, the three new tests, and this plan's entry.
- §1.2: the four CLI commands and `MU_RELATIONSHIP_PRODUCERS`.
- §3: a new subsection after §3.3q for the three producer tables (keys, sources, catch-up coverage). §3.3p gains a pointer to it.
- §6.0b step 5: the producer step right after the load, on weekly and offseason ingests; off by default; never fatal.
- §7.11: relationship events now come from the simulation as well as the inbox and pressers; fixed rules (D1); demotion-only lost role (D2); catch-up (D3); held coach events (D4); no `home_familiarity` source.
- PERSONALITY_PLAN §5.3: a row for this plan in the tracker. PERSONALITY_RELATIONSHIPS_PLAN §1: point "production ingestion" at this plan.

## 8. Decisions for JR

JR approved all three recommendations on 2026-11-03.

| # | Question | Decision (JR, 2026-11-03) |
|---|---|---|
| Q1 | When should the producers start running on the live franchise? | Off after the build. Turn them on with a catch-up once the relationships plan's store is merged and you've looked at a preview and a dry-run report. Turning them on changes no decisions, because consumers stay neutral until their own gates. |
| Q2 | Are the starting thresholds right: established role after 4 healthy weeks, injury situation at 2+ weeks out, streaks of 3, coach pressure at half the firing line? | Yes as review-only starting points in `rules.py`. Each event records the rule version, so a later change replays cleanly. |
| Q3 | Should role windows be private (the player's view of his demotion) while moves, injuries, awards and results are public? | Yes. Writers see the public facts. Whether a benching stung stays private unless the relationships plan's visibility rule exposes it. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | rep-u1 | .claude/worktrees/rep-u1 | merged; 53 tests (first agents' worktrees came from stale origin/main and were auto-removed; rebuilt from main 5b5115d) | 1d7ff4a |
| U2 | 1 | rep-u2 | .claude/worktrees/rep-u2 | merged; 20 tests (+1 opt-in integration, not run) | 0c43a12 |
| U3 | 2 | build/rep-u3 | .claude/worktrees/rep-u3 | merged; 15 run tests + 7 watcher tests, rules 54, store 24 (+ the two fixes above) | 16f2e57 |
