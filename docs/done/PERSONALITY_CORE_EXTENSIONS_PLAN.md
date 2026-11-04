# Project Mu: Personality Core Extensions Plan

**Status:** approved by JR (2026-11-03), Q1–Q3 settled as recommended (§8); built and on main (2026-11-04). Migration `2027_01_29` not applied to any database; `adopt` not run (JR's call, each after a `--dry-run`).
- As built (U1): the PostgreSQL store tests (`tests/test_personality_core_store.py`, extended) could not be run: no scratch database was reachable from the build machine (no Docker/PostgreSQL in WSL). They skip without `PERSONALITY_CORE_TEST_DSN`; run them against `madden_test` before applying `2027_01_29` anywhere.
- As built (U1): `Identity` derives `legacy_persona_id` from a `legacy:<role>:<id>` birth key, so `resolve_alias` and stored metadata rebuild adopted identities without a new column. Adoption is a pure `core.adopt` plus `store.adopt_legacy` (a generated GM keeps its seeded values; `initialize` would redraw them). `read_as_of` uses the profile's own receipts; `history` reads across links (ancestors' receipts plus the `role_link` receipts of profiles it was carried to). An override that changes a slider which already had evidence replaces that evidence with the override's provenance. `NotFoundError` is new in `core.py`.
- As built (U2): `save_persona` raises `CoreManagedError` only when sliders or traits are passed, so views-only edits of a core-managed persona still work. The `#debug` POST skips the override when the body changes nothing, uses `league_state`'s current week (clamped 1–32) as the default tick, and makes a fresh `debug-<uuid>` override ID per call. The CLI adds `--override-id` (replay key) to `override` and `--origin` to `adopt` (needed for roles other than writer/GM). The writer odds on `SUPERSTITIOUS`/`WORKOUT_FANATIC` also change `seed_traits` draws for any newly generated writer profile (none are stored).
- Follow-up outside this plan's files: `engines/inbox/voice.py` `_core_view` still marks a `real` profile `reviewed` only when its evidence lists the `inbox` feature; under D1 it should treat a stored real profile as reviewed. The other plans' wording was updated at approval (§7).
**Builds on:** [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) §5 step 3, §5.2, D4, D13; [`personality/README.md`](../../personality/README.md); [`PERSONALITY_DRAFT_INTEGRATION_PLAN.md`](../in_progress/PERSONALITY_DRAFT_INTEGRATION_PLAN.md) §2.1, §2.3, Q4 (the dependencies it leaves to the core owner); PROJECT_SPEC §3.3p, §4.5, §7.11.

**Goal:** Close the core gaps the sub-plans depend on.
- JR can change any profile after its first use, audited and outside the one-change-a-season limit.
- Any decision can be replayed with a profile as it was at that point in the season.
- The 40 writer personas and 32 GM personas that already exist come under the core unchanged.
- A player who becomes a coach (or a coach who becomes a GM) keeps his personality.
- Features read reviewed real profiles (real coaches, writers) in full, since real-inspired personas are fictional characters (D13).

**Delivers:**
- `personality/core.py`:
  - `Identity.adopted(...)`;
  - full reads of `real` profiles in `feature_view`;
  - pure `apply_override`, `carry_over` and `profile_as_of`;
  - the simulation-evidence gate removed from `apply_change`.
- `personality/store.py`: `override`, `adopt_legacy`, `link_role_change`, `history`, `read_as_of`.
- Migration `db/migrations/2027_01_29_personality_core_extensions.sql`:
  - `persona_core_events` gains `kind`, `effective_tick`, `actor` and `seq`;
  - the one-per-season rule applies only to evolution;
  - new table `persona_core_links`.
- CLI subcommands `python -m personality preview|override|adopt|link|history|as-of`.
- `#debug` persona endpoints that send core-managed profiles through the override.
- Legacy seeders that leave core-managed personas alone.
- Two catalogue odds fixes so every writer persona validates.

**JR's answers (2026-11-03):**
- D1: Reviewed real profiles are read in full by every feature, like generated ones. Evidence stays as provenance (who reviewed it, from what), not as a per-feature gate, and real-person evolution no longer needs special evidence.
- D2: The override after first use (PERSONALITY_PLAN D4) is a CLI command plus the existing `#debug` persona endpoints. A proper site editor comes from the [personality display plan](../not_started/PERSONALITY_DISPLAY_PLAN.md) (approved 2026-11-03).
- D3: A role change carries the personality over. The new role gets a new profile linked to the old one: sliders copied, traits valid for the new role kept, the rest dropped, nothing redrawn. History reads across the link.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `personality/core.py:155–160` `feature_view` exposes a `generated` profile in full, but for `real` it keeps only fields whose evidence names that exact feature and puts 5s and no traits everywhere else. | D1: `real` reads like `generated`. Evidence stays in the profile as provenance. |
| S2 | `personality/core.py:213–220` `apply_change` refuses a change to a `real` profile unless its evidence has `source == 'simulation:' + source_event_id`. `:113–114` limits low-confidence evidence to `voice`. | D1: the evolution gate goes. Evidence validation stays (it's provenance), but nothing reads its `features` list as a permission. |
| S3 | `personality/core.py:178–181` refuses a change in a season at or before `last_changed_season`. `db/migrations/2027_01_16_personality_core.sql:40` `UNIQUE (persona_type, persona_id, season)` on `persona_core_events`. | An override can't go through `change` or that table as it stands. The migration narrows the season rule to evolution only (a partial unique index), and overrides never touch `last_changed_season`. |
| S4 | `personality/core.py:52–54` `persona_id` is `uuid5` of universe, role and birth key; `validate_profile` (`:65`) insists on it. `db/migrations/2027_01_16_personality_core.sql:19` FKs core profiles to `persona_sliders (persona_type, persona_id)`. | Writers and GMs live in `persona_sliders` under legacy IDs (writer keys like `ESPN`, GM `gm_id`), and memories, views and relationships key on those IDs. Adoption keeps the legacy ID as the core `persona_id` (`Identity.adopted`) instead of copying under a UUID, which would duplicate writer rows that `newsroom/personas.py:526` loads by `persona_type = 'writer'`. |
| S5 | `personality/store.py:99–102` `ensure` refuses an identity whose `(type, id)` already has `persona_sliders` (an unmanaged legacy collision). `:55–57` `read` refuses a profile whose sliders or traits changed outside the core. | Adoption is its own path, not `ensure`. Afterwards the legacy writers (`save_persona` `newsroom/personas.py:365`, `seed_writers_to_db` `:431`, `seed_gms_to_db` `:474`) must skip or refuse core-managed personas, or the next read fails. |
| S6 | `server/debug_api.py:1192` `POST /api/debug/personas/{type}/{id}` edits through `save_persona`; `:1095` `PersonaUpdateIn` has `sliders`, `traits`, `views`, `notes`. `tests/test_personas_db.py:269–285` cover its token and validation. | D2: the endpoint detects a core-managed persona and calls `store.override` (a `reason` is required). Views and notes stay legacy fields. |
| S7 | Offline check on 2026-11-03: `initialize(..., 'real', sliders=..., traits=...)` over the 40 writers in `newsroom/config/agent_personas.json` rejects 3. `DIE_HARD_FAN` has `SUPERSTITIOUS`; `JJ_WATT` and `FORMER_PLAYER` have `WORKOUT_FANATIC`, which the catalogue gives no `writer` odds. GMs are seeded by `seed_persona` for role `gm`, so they validate. | Adoption needs those two traits eligible for writers: a catalogue odds change in `newsroom/config/persona_traits.json`, not a validation bypass (§8 Q1). |
| S8 | `personality/core.py:14` `ROLES` has no `coordinator`. The lifecycle plan (§2.1) puts HC, OC and DC under role `coach`, with `db-coach` / `db-coordinator` namespaces on one identity. | Role changes are between core roles (`player` → `coach`, `coach` → `gm`). A coordinator promoted to head coach is the same `coach` identity, so no link is needed. Note: the producers plan's built `rules.coordinator_alias` uses role `coordinator`; that's recorded there as a fix for its wave 2. |
| S9 | `persona_core_events` (`2027_01_16_personality_core.sql:30–43`) holds the request plus full `before_state` / `after_state` per change, keyed by `season` only. `persona_core_profiles.metadata` holds the current state. | `profile_as_of` can rebuild any past state from these receipts. It needs a within-season position (`effective_tick`) and a write order (`seq`) to place overrides. |
| S10 | `personality/__main__.py:1–20` is a single-command preview (`--universe --role --birth-key --origin --season --out`). | It becomes `preview`, and a call without a subcommand still runs the preview. |
| S11 | `grep -c persona_core db/init_db.sql` is 0: the core migration isn't folded into `init_db.sql` and is applied deliberately (README). Latest reserved migration date is `2027_01_28` (producers). | Same here: `2027_01_29`, not folded into `init_db.sql`. |
| S12 | `tests/test_personality_core.py` and `tests/test_personality_core_store.py` (opt-in scratch DSN `PERSONALITY_CORE_TEST_DSN`, isolated schemas rolled back). | New cases extend those files. |

## 1. Scope

**In:**
- D1 full reads of `real` profiles; the real-person evolution gate removed.
- Audited JR overrides at any time after initialization, validated against the same rules (band 24–36, trait counts and conflicts, role eligibility), outside the annual slot.
- Pure as-of reconstruction plus a store read, by `(season, tick)`.
- Adoption of the legacy writer and GM personas in place, with their current sliders and traits.
- Role-change links that carry the personality over (D3).
- CLI subcommands, the `#debug` override path, legacy seeder guards, and the catalogue odds fix.
- README and tests.

**Out (non-goals):**
- Before-first-use edits in the lifecycle manifest: the draft lifecycle plan.
- Event-to-trait rules and the arbiter that picks each person's one change per season: the evolution plan.
- A site editor: the personality-display plan.
- Researching or seeding real coaches: the real-coach plan, which consumes D1.
- Adopting views (`persona_views`), wants/fears or signature pools: they stay legacy writer fields, read by `newsroom/personas.py`.
- Changing any feature's formulas.
- Applying the migration to a franchise database.

## 2. Design

### 2.1 Pure core (`personality/core.py`)

```python
FEATURE_VIEW_VERSION = 'views-v2'          # D1; CORE_VERSION stays 'personality-core-v1' (the generator is unchanged)
CHANGE_KINDS = ('evolution', 'override', 'adoption', 'role_link')

@dataclass(frozen=True)
class Identity:
    universe_id: str; role: str; birth_key: str
    legacy_persona_id: str | None = None    # set only by Identity.adopted
    @property
    def persona_id(self): ...               # legacy_persona_id when set, else the uuid5 as today
    @classmethod
    def adopted(cls, universe_id, role, legacy_persona_id): ...   # birth_key 'legacy:<role>:<legacy_persona_id>'
```

- **`validate_profile`:**
  - accepts a stored `legacy_persona_id` and checks `persona_id` against `Identity(...).persona_id`;
  - adds an optional `links` list (`[{direction: 'from'|'to', role, persona_id}]`);
  - evidence keeps its current format checks, but `features` is no longer required to gate anything.
- **`feature_view(profile, feature)`:**
  - `generated` and `real` both return the stored sliders and traits;
  - `unknown` stays neutral and `missing` stays explicit;
  - the result gains `view_version: FEATURE_VIEW_VERSION` and `origin`.
- **`apply_change`:** the `real` simulation-evidence block (`:213–220`) is removed. Evidence passed with a change is still recorded under the target.
- **`apply_override(profile, request) -> (after, audit)`.** The request is `{override_id, actor, reason, season, tick, expected_revision, sliders?: {name: 0..10} (full or partial), add_traits?: [..], remove_traits?: [..]}`.
  - `actor` must be `'JR'`; `reason` is nonempty.
  - `unknown` profiles are refused: classify through the lifecycle plan first.
  - The result passes `validate_profile` under the active band and catalogue, so trait counts, conflicts and role eligibility still hold.
  - `revision` goes up by 1. `last_changed_season` is untouched (outside the annual slot, D4).
  - Removed traits lose their evidence entry. Added traits get `{source: 'override:<override_id>', date, confidence: 'high', reviewer: 'JR', reason, features: ['all']}`.
- **`carry_over(profile, new_identity, season, *, catalogue) -> dict` (D3):**
  - copies the sliders exactly;
  - keeps the traits whose catalogue `roles[new_role] > 0`, and drops the rest;
  - keeps `origin`, and keeps the evidence of the traits it kept;
  - `revision` 0, `initialized_season` = season;
  - adds `links: [{direction: 'from', role, persona_id}]`.

  For a `generated` profile that would fall below 3 traits, the kept traits stand and `validate_profile` takes a carried profile's `links` as allowing 0–5 traits. Nothing is redrawn, so the profile may hold fewer traits than a fresh one.
- **`profile_as_of(current, receipts, season, tick) -> dict | None`:**
  - `receipts` are `persona_core_events` rows sorted by `(season, effective_tick NULLS FIRST, seq)`;
  - it returns the `after_state` of the last receipt at or before `(season, tick)`;
  - with no such receipt it returns the first receipt's `before_state`, or `current` when there are no receipts;
  - it returns `None` before `initialized_season`.

  Evolution receipts without a tick count as tick 1 of their season.

### 2.2 Migration (`2027_01_29_personality_core_extensions.sql`; requires `2027_01_16`)

```sql
ALTER TABLE persona_core_events
  ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'evolution'
      CHECK (kind IN ('evolution','override','adoption','role_link')),
  ADD COLUMN IF NOT EXISTS effective_tick SMALLINT CHECK (effective_tick BETWEEN 1 AND 32),
  ADD COLUMN IF NOT EXISTS actor TEXT,
  ADD COLUMN IF NOT EXISTS seq BIGINT GENERATED ALWAYS AS IDENTITY,
  ADD COLUMN IF NOT EXISTS recorded_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE persona_core_events DROP CONSTRAINT IF EXISTS persona_core_events_persona_type_persona_id_season_key;
CREATE UNIQUE INDEX IF NOT EXISTS persona_core_events_one_evolution_per_season
  ON persona_core_events (persona_type, persona_id, season) WHERE kind = 'evolution';
CREATE TABLE IF NOT EXISTS persona_core_links (
  universe_id TEXT NOT NULL,
  from_type VARCHAR(32) NOT NULL, from_id VARCHAR(64) NOT NULL,
  to_type VARCHAR(32) NOT NULL, to_id VARCHAR(64) NOT NULL,
  link_kind TEXT NOT NULL CHECK (link_kind = 'role_change'),
  effective_season INT NOT NULL, effective_tick SMALLINT,
  receipt JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (to_type, to_id),
  UNIQUE (from_type, from_id, to_type),
  FOREIGN KEY (from_type, from_id) REFERENCES persona_core_profiles(persona_type, persona_id),
  FOREIGN KEY (to_type, to_id) REFERENCES persona_core_profiles(persona_type, persona_id));
```

- **Transactional and checked:** the migration is one transaction. It verifies the unique constraint it drops actually exists under that name (or finds it by its columns) and fails otherwise. Existing rows become `kind = 'evolution'`.
- **Not folded:** like the core migration, it is not folded into `init_db.sql`.
- **Adoption and links:** adoption writes one `adoption` receipt (before = the legacy state read, after = the adopted profile). Links write one `role_link` receipt on the new profile.

### 2.3 Store (`personality/store.py`; caller owns the transaction, savepoint plus identity lock as today)

```python
def override(cur, identity, request) -> dict                       # replay of the same override_id returns the original after_state
def adopt_legacy(cur, universe_id, role, legacy_persona_id, origin, season, *, reviewer, reason) -> dict
def link_role_change(cur, from_identity, to_identity, season, *, tick=None, reason) -> dict
def history(cur, identity) -> list[dict]                            # receipts in (season, effective_tick, seq) order, links on both sides
def read_as_of(cur, identity, season, tick) -> dict | None          # core.profile_as_of over history()
```

- **`change`:** also writes `kind = 'evolution'`, and `effective_tick` when the event carries `tick`.
- **`adopt_legacy`:**
  - reads `persona_sliders` and active `persona_traits` for `(role, legacy_persona_id)`, refusing if there are none or if a core profile already exists;
  - builds the profile with `Identity.adopted` and the given origin: `real` for writers (hand-authored and reviewed), `generated` for GMs (seeded by `seed_persona`);
  - inserts the catalogue snapshot, the `persona_core_profiles` row and the `adoption` receipt. The existing `persona_sliders` and `persona_traits` rows are left as they are.

  Running it again on an adopted persona returns the stored profile.
- **`link_role_change`:**
  - requires a stored `from` profile, refuses an existing `to` profile, and refuses the same role or `unknown`;
  - writes `carry_over`'s profile through the same insert path as `ensure` (canonical rows, trait history `reason = 'role change from <role>'`);
  - writes the link row and receipt.

  The old profile stays as it is, for history.

### 2.4 Surfaces

**CLI (`personality/__main__.py`):** no subcommand behaves as `preview` with today's flags.

```text
python -m personality preview --universe U --role R --birth-key K [--origin O] --season S [--out PATH]
python -m personality override (--birth-key K | --alias NS:ID | --legacy-id ID) --universe U --role R --season S --tick T
                       [--set SLIDER=N ...] [--add-trait T ...] [--remove-trait T ...] --reason TEXT [--dry-run] [--dsn DSN]
python -m personality adopt --universe U (--writers | --gms | --role R --legacy-id ID) --season S --reason TEXT [--dry-run] [--dsn DSN]
python -m personality link --universe U --from ROLE:KEY --to ROLE:KEY --season S [--tick T] --reason TEXT [--dry-run] [--dsn DSN]
python -m personality history|as-of --universe U --role R (--birth-key K | --alias NS:ID | --legacy-id ID) [--season S --tick T] [--dsn DSN]
```

- **Database commands:** they read `DATABASE_URL` or `--dsn`, refuse `madden_2036` and `postgres`, and print the receipt as JSON. `--dry-run` rolls back.
- **Exit codes:** 0 ok, 2 invalid request, 3 not found, 4 conflict (stale revision, existing profile).

**`#debug` (`server/debug_api.py`):**
- **GET `/api/debug/personas`:** adds `core: {origin, revision, persona_id, view_version, links}` for core-managed personas.
- **POST `/api/debug/personas/{type}/{id}`:** for a core-managed persona, the sliders and traits in the body go through `store.override` with `actor 'JR'`. `PersonaUpdateIn` gains `reason: str | None` (required for core-managed personas, else 422) and `season` / `tick`, which default to `league_state`. Views and notes stay on the legacy path. Unmanaged personas keep the legacy path unchanged.

**Legacy guards (`newsroom/personas.py`):**
- `save_persona` raises `CoreManagedError` for a `(type, id)` with a `persona_core_profiles` row, when that table exists.
- `seed_writers_to_db` and `seed_gms_to_db` skip core-managed IDs and report them.

**Catalogue (`newsroom/config/persona_traits.json`):** `SUPERSTITIOUS` and `WORKOUT_FANATIC` gain `writer` odds (0.05, an estimate in line with other quirk and work traits), so all 40 writers validate (S7, §8 Q1).

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | The auto-generated name of the season unique constraint differs on some database. | The migration looks the constraint up by its columns in `pg_constraint` before dropping it; a scratch-schema test covers both names. | It fails loudly with no partial change; fix the lookup. |
| R2 | Other code reads `persona_core_events` assuming one row per season. | `grep -rn persona_core_events` over the repo (today only `personality/store.py`). | Readers filter `kind = 'evolution'`. |
| R3 | Adopted writers' `real` origin with 0–5 traits, or a carried profile with fewer than 3, breaks a consumer that assumes 3–5. | `grep` feature code for trait-count assumptions; the legacy writer loader reads `persona_traits` directly and doesn't care. | Consumers treat trait count as data, not a rule. |
| R4 | A legacy debug edit lands between adoption and the guard shipping. | U2 lands the guards and adoption CLI together; adoption is only run after U2 merges. | `read` already refuses a profile changed outside the core (S5), so the failure is loud; fix with an override. |
| R5 | The other approved plans describe the D1 evidence gate (relationships §2.5, development §2.6, the lifecycle plan's "Evidence/scoped permissions remain core-owned"). | Grep the plan docs at approval. | The orchestrator updates their wording when this plan is approved (§7). |

**Spike first:** none needed. The writer validation check (S7) was run during planning.

## 4. Tests (synthetic, no live DB)

- `tests/test_personality_core.py` (existing; add cases):
  - `real` reads in full;
  - unknown stays neutral, and `view_version` is set;
  - the real evolution gate is gone, and evidence is still recorded;
  - `apply_override`:
    - band 24–36 and trait-rule validation, role eligibility;
    - `last_changed_season` untouched, revision +1;
    - refused for `unknown` and for an actor other than JR;
    - the evidence entry on added traits;
  - `Identity.adopted` keeps the legacy ID, and the uuid path is unchanged;
  - `carry_over`:
    - sliders copied, ineligible traits dropped, nothing redrawn;
    - a generated profile carried down to 2 traits still validates;
  - `profile_as_of`:
    - before initialization;
    - between receipts, with an evolution and an override in the same season ordered by tick and seq;
    - a carried-over profile's start;
  - all 40 writers from `agent_personas.json` validate as adopted `real` profiles with the updated catalogue.
- `tests/test_personality_core_store.py` (existing; opt-in scratch DSN):
  - the migration applied twice, existing rows becoming `evolution`;
  - a second evolution in one season still refused, while an override in the same season is allowed;
  - override replay and a stale revision;
  - adopt in place: legacy rows untouched, the second run returns the stored profile, a missing legacy persona refused;
  - the link writes the new profile and receipt and refuses a duplicate;
  - `history` and `read_as_of` across adoption → override → evolution;
  - rollback on forced failure, and no commit.
- `tests/test_personality_cli.py` (new):
  - subcommand parsing; no subcommand = preview, byte-identical to today;
  - `--dry-run` rolls back, using a fake cursor injected through `--dsn` handling;
  - exit codes; DSN guard.
- `tests/test_personas_db.py` (existing; add cases):
  - `save_persona` raises `CoreManagedError` for a core-managed persona;
  - the seeders skip core-managed IDs;
  - debug POST sends core-managed sliders and traits through the override and requires `reason` (422 without it);
  - the unmanaged legacy path is unchanged;
  - GET shows the `core` block.

Run with `pytest -m "not integration" tests/test_personality_core.py tests/test_personality_cli.py tests/test_personas_db.py`. Store cases: `PERSONALITY_CORE_TEST_DSN=<madden_test scratch DSN> pytest tests/test_personality_core_store.py` (isolated schemas, all rolled back).

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Core and store

- **Owns:** `personality/core.py`, `personality/store.py`, `db/migrations/2027_01_29_personality_core_extensions.sql` (new), `tests/test_personality_core.py`, `tests/test_personality_core_store.py`
- **Reads:** this plan §2.1–§2.3; `personality/README.md`; `db/migrations/2027_01_16_personality_core.sql`; `newsroom/personas.py:159–280` (validator, seeders, catalogue loader); `newsroom/config/agent_personas.json`
- **Depends on:** none
- **Done when:** the §2.1 functions and §2.3 store functions exist with the exact signatures. Pure tests pass, including all 40 writers validating against a catalogue fixture that carries the two odds fixes (the real catalogue file belongs to U2). The migration text checks the constraint and refuses a mismatch.
- **Verify:** `pytest -m "not integration" tests/test_personality_core.py -q` passes. With an explicit scratch DSN, `PERSONALITY_CORE_TEST_DSN=... pytest tests/test_personality_core_store.py -q` passes, all rolled back (the orchestrator runs this one if the agent can't reach `madden_test`).

### U2: CLI, #debug, legacy guards, catalogue and README

- **Owns:** `personality/__main__.py`, `personality/README.md`, `server/debug_api.py`, `newsroom/personas.py`, `newsroom/config/persona_traits.json`, `tests/test_personality_cli.py` (new), `tests/test_personas_db.py`
- **Reads:** U1's merged `personality/core.py` and `store.py`; this plan §2.4; `server/debug_api.py:1095–1230`; `tests/test_personas_db.py:250–300`
- **Depends on:** U1
- **Done when:** the subcommands work (no subcommand gives a byte-identical preview); the debug endpoints route core-managed personas through the override and keep the legacy path for the rest; the seeders and `save_persona` respect core management; the catalogue lets all 40 writers validate; the README documents D1–D3, the override, adoption, links and as-of.
- **Verify:** `pytest -m "not integration" tests/test_personality_cli.py tests/test_personas_db.py tests/test_personas.py -q` passes; `python -m personality --help` and `python -m personality preview --universe u --role player --birth-key k --season 2026` exit 0.

## 6. Waves

| Wave | Units | Needs merged first | Why |
|---|---|---|---|
| 1 | U1 | — | Core and store change together: the store calls the new pure functions, and both tests exercise them. |
| 2 | U2 | U1 | Every surface calls the new store functions. |

Small enough that `/execute-plan` could run it as two sequential agents. Building waits until every personality sub-plan is approved (JR, 2026-11-03).

After the build, a gate: running `adopt --writers` / `--gms` on a franchise database, and applying `2027_01_29` there, are JR's call, each after a `--dry-run`.

**File ownership check:**

| File | Unit |
|---|---|
| `db/migrations/2027_01_29_personality_core_extensions.sql` | U1 |
| `newsroom/config/persona_traits.json` | U2 |
| `newsroom/personas.py` | U2 |
| `personality/README.md` | U2 |
| `personality/__main__.py` | U2 |
| `personality/core.py` | U1 |
| `personality/store.py` | U1 |
| `server/debug_api.py` | U2 |
| `tests/test_personality_cli.py` | U2 |
| `tests/test_personality_core.py` | U1 |
| `tests/test_personality_core_store.py` | U1 |
| `tests/test_personas_db.py` | U2 |

No path appears twice. `server/debug_api.py` and `newsroom/personas.py` may also be touched by the V2.9 inbox plan; whichever builds second re-reads them.

## 7. Spec changes when built

- §3.3p: `persona_core_events` new columns and the evolution-only season rule; `persona_core_links`; adopted legacy personas keep their IDs.
- §4.5: the `#debug` persona endpoints' core-managed behaviour, the `reason` field and the `core` block.
- §7.11, the personality core paragraph:
  - D1 full reads of `real` profiles (`views-v2`);
  - no real-person evolution gate;
  - overrides outside the annual slot;
  - adoption, role-change carry-over, and as-of reads;
  - the CLI subcommands.
- §1.1 Repository Layout: the migration, `tests/test_personality_cli.py`, and the `personality/` line (`__main__.py` now has subcommands); this plan's entry.
- §1.2: the `python -m personality` subcommands.
- Other plans, at approval rather than at build:
  - `PERSONALITY_PLAN.md`: §5.2 and the §5.3 tracker row "Two core changes are not in any plan yet" now point here; D4 points here.
  - The relationships plan (§2.5 real-coach evidence wording), the development plan (S1, §2.6) and the lifecycle plan (§2.1 "Evidence/scoped permissions remain core-owned", R3, Q4): replace the evidence-gate wording with a pointer to this plan.

## 8. Decisions for JR

JR approved all three recommendations on 2026-11-03.

| # | Question | Decision (JR, 2026-11-03) |
|---|---|---|
| Q1 | Three writers carry traits the catalogue doesn't give writers (`SUPERSTITIOUS` for DIE_HARD_FAN; `WORKOUT_FANATIC` for JJ_WATT and FORMER_PLAYER). Fix the catalogue or the writers? | Fix the catalogue: give both traits small `writer` odds (0.05). They suit a superstitious fan and two former players, and the writers stay as you wrote them. |
| Q2 | What origin should adopted personas carry? | Writers `real` (hand-written, reviewed); GMs `generated` (seeded by the engine). Under D1 both read in full; origin only records where the profile came from. |
| Q3 | Should JR's overrides be allowed to break the profile rules (for example 6 traits, or a slider total outside 24–36)? | No. An override must pass the same validation as any profile, so no feature has to handle a malformed one. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 core and store | 1 | build/pcext-u1 | .claude/worktrees/pcext-u1 | merged | b5f090a |
| U2 CLI, #debug, guards, catalogue, README | 2 | build/pcext-u2 | .claude/worktrees/pcext-u2 | merged | 94cee31 |
