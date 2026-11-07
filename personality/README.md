# Personality core

This package owns identity, one-time initialization, provenance-scoped profile reads and audited profile changes. Feature policies own all numeric consequences. No contract, development, trade, mood, relationship or retirement formulas live here.

## Public interface

```python
from personality.core import Identity, feature_view
from personality import store

identity = Identity('verified-universe-id', 'player', 'verified-immutable-prospect-key')
# Explicit initialization at prospect entry, inside the caller's transaction:
profile = store.ensure(cur, identity, 'generated', 2027)
store.bind_alias(cur, identity, 'draft:2027', 'verified-prospect-id')
# On a verified draft-to-player handoff, attach additional aliases to the SAME identity:
store.bind_alias(cur, identity, 'save-pgid', 'verified-pgid')

# Reads never seed. Missing means neutral; storage/schema/corruption errors propagate.
identity = store.resolve_alias(cur, 'verified-universe-id', 'player', 'save-pgid', 'verified-pgid')
profile = None if identity is None else store.read(cur, identity)
view = feature_view(profile, 'contracts')
```

The code above is an API illustration, not a production ingestion hook. Names, team, ratings, age, OVR, draft rank and the loader's `origin='SAVE'` are not proof of a generated identity. A caller must establish real/generated classification and stable universe/prospect identity first. Do not initialize an unverified person as generated. Unknown profiles are neutral and cannot evolve; classifying an unknown profile is the lifecycle plan's reviewed workflow, and legacy writer/GM personas are adopted in place through `store.adopt_legacy` (below), never a silent replacement through `ensure`.

`Identity` derives a deterministic UUID from universe + role + immutable birth key. The same external pgid can exist in separate universes. Aliases are unique within universe/role/namespace; ambiguous reassignment fails. A rename, trade or promotion never changes identity. `ensure` returns stored state on repeats even after a catalogue change; a different origin or collision with an unmanaged legacy persona fails.

Generated initialization reuses the existing six-slider generator and role-weighted trait catalogue (four traits, conflicts and max two per category). The core records generation version and a content-addressed catalogue snapshot. Existing stored profiles are never regenerated. Any future generator algorithm change must bump `CORE_VERSION`; legacy seeding helpers are reused at initialization, so changes to them must be included in that version review.

Real profiles may have zero to five reviewed traits, unlike the legacy 3–5 writer/generator validator. Real/unknown defaults are six 5s and no traits. **D1 (views `views-v2`, `FEATURE_VIEW_VERSION`):** `feature_view` returns a reviewed `real` profile in full to every feature, exactly like a generated one, because real-inspired personas are fictional characters (PERSONALITY_PLAN D13). Evidence (source, publication date, confidence, reviewer, reason, features) stays in the profile as provenance, who reviewed what and from where; nothing reads its `features` list as a permission. Low-confidence evidence still records only `voice`. Unknown profiles stay neutral and `missing` stays explicit; every view carries `origin` and `view_version`. The consuming feature must still gate its own activation and context (e.g. financial security for strong ring chasing).

## Persistence and transaction ownership

Migration `db/migrations/2027_01_16_personality_core.sql` adds:

- `persona_core_catalogues`: immutable content-addressed catalogue snapshots.
- `persona_core_profiles`: universe identity, origin, revision and lifecycle/provenance metadata. Sliders and active traits remain in existing `persona_sliders` / `persona_traits`.
- `persona_core_aliases`: verified prospect/player/save identity bindings.
- `persona_core_events`: original requests and full before/after receipts, unique by event, canonical source event and profile-season.

The migration requires `2027_01_09_personas.sql`; it is not automatically applied. No franchise DB has been migrated or seeded by this build. Read and mutation helpers take an explicit cursor. Mutations use savepoints plus per-identity transaction advisory locks and never commit. The caller must own a non-autocommit transaction. Any failure rolls back the whole helper, including canonical traits/sliders, metadata and audit. Errors remain visible to the caller. Legacy debug changes to a managed profile are detected and rejected on core reads, not quietly adopted.

Initialization records each starting trait in existing `persona_trait_history`; later audited add/drop operations close/open those entries. The new event log supplies full revision history (the old table cannot represent sliders or exact event replay).

## Evolution boundary

`store.change(cur, identity, event)` accepts an explicit candidate with `event_id`, `source_event_id`, `season`, `expected_revision`, `rule_version`, `reason` and either:

- `kind='trait'`, one optional `remove` and one optional `add` (a replacement counts as one change); or
- `kind='slider'`, a slider name and `step` of -1 or +1.

The originating policy owns whether the simulated event justifies the candidate. The core checks validity, revision, origin, chronology and at most one change per season. It does not verify another engine's source-event ledger; callers must supply verified canonical events. Real-person evolution needs no special evidence (D1); evidence passed with a change (`event['evidence']`) is recorded under the changed field as provenance. An optional `tick` (1–32) places the change within its season for as-of reads. Replayed identical events return the original receipt, never revert a newer state. Reusing either event ID with a different payload or canonical source under a new ID fails.

**Slider band:** since 2026-11-02 (`docs/done/CAP_RELATIVE_CONTRACTS_PLAN.md`) the legacy validator (`newsroom/personas.py` `SLIDER_BUDGET_MIN`/`MAX`) and `chk_persona_sliders_budget` (`2027_01_17_persona_slider_band.sql`, applied to `madden_test`; `db/init_db.sql`) accept totals 24–36; seeding still deals 30. Core initialization records the active validator's bounds (now 24/36), and evolution adopts them, so a single-slider move works on a migrated database. A database still on the exact-30 constraint rejects it. Failure on an old DB is atomic. A band change does not reseed existing profiles. Trait evolution already works under the current constraint.

No automatic event rules, probabilistic trait changes, retrospective migration, prose generation, production ingestion scheduling or feature effects are activated. No hired player-agent role is added. Mood and relationships will be separate consumers/producers of verified events, not fields inferred from these sliders.

## Core extensions (docs/done/PERSONALITY_CORE_EXTENSIONS_PLAN.md)

Migration `db/migrations/2027_01_29_personality_core_extensions.sql` (requires `2027_01_16`; not folded into `init_db.sql`, applied deliberately) gives `persona_core_events` a `kind` (`evolution`, `override`, `adoption`, `role_link`), `effective_tick`, `actor`, `seq` and `recorded_at`, narrows the one-change-a-season rule to `kind = 'evolution'`, and adds `persona_core_links`. `store.change` writes `kind='evolution'`, so it needs this migration.

- **JR's override (D2, PERSONALITY_PLAN D4).** `store.override(cur, identity, request)` with `{override_id, actor: 'JR', reason, season, tick, expected_revision, sliders?: {name: 0..10}, add_traits?, remove_traits?}`. It must pass the same validation as any profile (band 24–36, trait counts, categories, conflicts, role eligibility; plan Q3), sits outside the annual slot (`last_changed_season` untouched), bumps `revision`, and refuses `unknown` profiles. Added traits get evidence `{source: 'override:<id>', confidence: 'high', reviewer: 'JR', reason, features: ['all']}`; removed traits lose theirs. Replaying an `override_id` returns the original result. Surfaces: `python -m personality override` and the `#debug` persona endpoint, which sends a core-managed persona's sliders and traits here (a `reason` is required).
- **Adoption in place.** `store.adopt_legacy(cur, universe_id, role, legacy_persona_id, origin, season, *, reviewer, reason)` brings a legacy writer or GM under the core with its current `persona_sliders` / active `persona_traits`, which stay where they are. `Identity.adopted(universe, role, legacy_id)` keeps the legacy ID as `persona_id` (birth key `legacy:<role>:<id>`), so memories, views and relationships keyed on it keep working. Writers are adopted `real` (hand-written, reviewed), GMs `generated` (seeded); under D1 both read in full. Afterwards `newsroom/personas.py` refuses legacy slider/trait writes (`save_persona` raises `CoreManagedError`) and its seeders skip core-managed IDs; views, wants/fears and signatures stay legacy writer fields.
- **Role changes (D3).** `store.link_role_change(cur, from_identity, to_identity, season, *, tick=None, reason)` creates the new role's profile with `core.carry_over`: sliders copied, traits the catalogue allows the new role kept, the rest dropped, nothing redrawn (a carried generated profile may hold 0–5 traits). It records a `persona_core_links` row and a `role_link` receipt; the old profile stays for history. A coordinator promoted to head coach is the same `coach` identity and needs no link.
- **History and as-of.** `store.history(cur, identity)` lists receipts in `(season, effective_tick NULLS FIRST, seq)` order, reading across role links (the profiles it was carried from, and the `role_link` receipts of profiles it was carried to). `store.read_as_of(cur, identity, season, tick)` and the pure `core.profile_as_of(current, receipts, season, tick)` return the profile as it was at that point (untimed receipts count as tick 1; `tick=None` means the season's end), or `None` before `initialized_season`.

CLI (database commands read `DATABASE_URL` or `--dsn`, refuse `madden_2036` and `postgres`, print JSON, commit unless `--dry-run`; exit 0 ok, 2 invalid, 3 not found, 4 conflict):

```
python -m personality [preview] --universe U --role R --birth-key K [--origin O] --season S [--out PATH]
python -m personality override (--birth-key K | --alias NS:ID | --legacy-id ID) --universe U --role R --season S --tick T \
    [--set SLIDER=N ...] [--add-trait T ...] [--remove-trait T ...] --reason TEXT [--override-id ID] [--dry-run] [--dsn DSN]
python -m personality adopt --universe U (--writers | --gms | --role R --legacy-id ID) [--origin O] --season S --reason TEXT [--dry-run] [--dsn DSN]
python -m personality link --universe U --from ROLE:KEY --to ROLE:KEY --season S [--tick T] --reason TEXT [--dry-run] [--dsn DSN]
python -m personality history|as-of --universe U --role R (--birth-key K | --alias NS:ID | --legacy-id ID) [--season S --tick T] [--dsn DSN]
```

Applying `2027_01_29` to a franchise database and running `adopt --writers` / `--gms` there are JR's decisions, each after a `--dry-run`.

## Inferred sliders and refreshing focus (docs/in_progress/INFERRED_SLIDERS_AND_FOCUS_PLAN.md)

Real coach and owner profiles (`coach_profiles.py`, `owner_profiles.py`) have a v2 schema (`coach-profile-v2`, `owner-profile-v2`; v1 still read) with a `slider_inference` block, a durable `motivation` {want, fear} and a per-slot current `focus`.

- **`slider_inference.py`** (pure): a slider at 5 with no evidence gets `clamp(5 + capped trait leans + seeded step, 2, 8)` from `newsroom/config/slider_leans.json`, balanced into the 24–36 band by moving inferred values toward 5. `infer_sliders`, `check_inference`, `effective_sliders`, `inferred_evidence` (a low-confidence, voice-only `inferred:slider-inference-v1` provenance entry, or `jr-set:<date>` for a slider JR locked), `is_inferred`. The values are fixed in the file at review time (`python -m tools.coach_profile_review infer --write`) and reach the core only through the reviewed lifecycle apply (`core_initial_values` overlays them).
- **`focus.py`** (pure): `validate_file_focus` (motivation and focus word caps, subjects, no names in the motivation), `stale_causes` (SUBJECT_RETIRED, SUBJECT_LEFT_TEAM, SUBJECT_LEFT_ROLE, SUBJECT_LEFT_LEAGUE, SUBJECT_UNRESOLVED, HOLDER_MOVED, EXPIRED; unknown is never stale), `load_templates` (`newsroom/config/focus_templates.json`), `draft_focus` (seeded template pick), `change_note`.
- **`focus_store.py`**: `persona_focus` (append-only focus history, one active row per person and slot) and `persona_focus_runs` from `db/migrations/2027_02_03_persona_focus.sql` (requires `2027_01_16` and `2027_01_24`; not in `init_db.sql`; applying it is JR's call). Explicit cursor, savepoints, never commits.
- **`focus_facts.py`** (read-only): `facts(cur, universe_id, season, *, bindings=None)`: each core coach's seat and team (db-coach / db-coordinator alias and tenures) and each owner's team (birth key `curated-owner:<TEAM>:2026`), team direction, playoffs, Super Bowl, hot seat, the team's people (qb1, young_star, top_defender, top_weapon, head_coach, gm) and the status of the subjects of his active focus rows. A missing table leaves its facts None.
- **`focus_run.py`**: `seed` (seq-0 rows from reviewed v2 files), `run_season` (keep, replace a stale profile/template row by a template draft with a change note, report a stale JR row as `jr_stale`; noop on the same input hash; `dry_run` writes nothing), `render_report`, `set_focus`, `revert`, and the CLI below. The switchboard feature is `focus_refresh` (after `evolution`); the watcher step `step_focus_refresh` runs it on the OFFSEASON_START ingest (shadow = dry run, active = applied; kill switch `MU_FOCUS_REFRESH`).

```
python -m personality.focus_run season --season S [--dry-run] [--universe U] [--dsn DSN]
python -m personality.focus_run report --season S [--out PATH] [--universe U] [--dsn DSN]
python -m personality.focus_run show --persona ID [--as-of S:T] [--universe U] [--dsn DSN]
python -m personality.focus_run set --persona ID --slot want|fear --text TEXT [--subject kind:namespace:id ...] --reason TEXT [--season S --tick T]
python -m personality.focus_run revert --focus-id ID --reason TEXT [--dsn DSN]
```

`DATABASE_URL` or `--dsn`, refusing `madden_2036` and `postgres`; JSON out; commits unless `--dry-run` (report and show never commit); exit 0 ok, 2 invalid, 3 not found or not migrated, 4 conflict.

## Validation and parallel work

Pure tests cover deterministic generated initialization, scopes, sparse real-person evidence, full real reads, mutation limits, active slider policy, overrides, adoption of all 40 writers, carry-over and as-of reconstruction; `tests/test_personality_cli.py` covers the CLI offline. PostgreSQL tests use an explicit scratch DSN and a fresh schema per test; all DDL/data roll back and never touch public profiles. They exercise alias conflicts, insert-once replay, event deduplication, legacy-mutation detection, forced failure after canonical writes and preservation of caller transaction ownership.

```
python -m personality --universe offline-demo --role player --birth-key prospect:2027:example --season 2027 --out verify_output/personality_core/profile.json
PERSONALITY_CORE_TEST_DSN='host=localhost dbname=madden_test user=postgres password=postgres' python -m pytest tests/test_personality_core.py tests/test_personality_core_store.py -q
```

Parallel ownership:

- This core: `personality/`, core migration, core tests and core API documentation.
- Cap-relative contracts/earnings (built 2026-11-02): `engines/trade_value.py`, `engines/cap_history.py`, `engines/career_earnings.py`, the legacy slider-band change and its SQL constraint migration. Consume core snapshots; do not move contract math into core.
- Relationships/reactions: design and a separate module against canonical identity/revision and event IDs; no core schema edits without coordination.
- Progression/coaching: existing progression/depth-chart owners can implement pure policy consumers once the profile view is fixed; integration waits for their neutral-control validation.
- Trade requests/retirement: baseline/policy design can proceed independently, with execution integration later.

Pipeline identity binding is the person lifecycle (docs/done/PERSONALITY_DRAFT_INTEGRATION_PLAN.md; PROJECT_SPEC §7.11): `personality/lifecycle.py` (pure inventory and `personality-lifecycle-v1` manifests), `personality/lifecycle_store.py` (ledgers from `2027_01_24_personality_lifecycle.sql`, apply/revert, `resolve_person`, `require_ready`, creator hooks), `tools/personality_inventory.py` (offline preview/validate/compare; database export/apply/revert with an explicit `--dsn`), and observe-by-default hooks in `pipeline/load_save.py`, `pipeline/load_capture.py`, `engines/scouting_engine.py`, `engines/coaching_carousel.py` and `engines/fire_coordinator.py`. Applying the migration and an inventory to a franchise database remain JR's decisions. Keep feature enablement separate from profile availability.
