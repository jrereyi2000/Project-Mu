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

The code above is an API illustration, not a production ingestion hook. Names, team, ratings, age, OVR, draft rank and the loader's `origin='SAVE'` are not proof of a generated identity. A caller must establish real/generated classification and stable universe/prospect identity first. Do not initialize an unverified person as generated. Unknown profiles are neutral and cannot evolve; classification/adoption of existing unknown or legacy profiles needs a future explicitly reviewed workflow, not a silent replacement through `ensure`.

`Identity` derives a deterministic UUID from universe + role + immutable birth key. The same external pgid can exist in separate universes. Aliases are unique within universe/role/namespace; ambiguous reassignment fails. A rename, trade or promotion never changes identity. `ensure` returns stored state on repeats even after a catalogue change; a different origin or collision with an unmanaged legacy persona fails.

Generated initialization reuses the existing six-slider generator and role-weighted trait catalogue (four traits, conflicts and max two per category). The core records generation version and a content-addressed catalogue snapshot. Existing stored profiles are never regenerated. Any future generator algorithm change must bump `CORE_VERSION`; legacy seeding helpers are reused at initialization, so changes to them must be included in that version review.

Real profiles may have zero to five reviewed traits, unlike the legacy 3–5 writer/generator validator. Real/unknown defaults are six 5s and no traits. A real field is exposed to a feature only when its evidence contains source, publication date, confidence, reviewer, reason and that exact feature permission. Low-confidence evidence is `voice` only. Feature names are policy namespaces, not formulas. High confidence does not automatically enable a field. Returned feature views are filtered contexts, not profiles to persist or revalidate against the slider budget. A generated view exposes the stored profile; the consuming feature must still gate its own activation and context (e.g. financial security for strong ring chasing).

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

The originating policy owns whether the simulated event justifies the candidate. The core checks validity, revision, origin, chronology and at most one change per season. It does not verify another engine's source-event ledger; callers must supply verified canonical events. A real person's newly changed field requires scoped evidence with `source='simulation:' + source_event_id`; an old interview cannot grant new permissions. Replayed identical events return the original receipt, never revert a newer state. Reusing either event ID with a different payload or canonical source under a new ID fails.

**Slider band:** since 2026-11-02 (`docs/done/CAP_RELATIVE_CONTRACTS_PLAN.md`) the legacy validator (`newsroom/personas.py` `SLIDER_BUDGET_MIN`/`MAX`) and `chk_persona_sliders_budget` (`2027_01_17_persona_slider_band.sql`, applied to `madden_test`; `db/init_db.sql`) accept totals 24–36; seeding still deals 30. Core initialization records the active validator's bounds (now 24/36), and evolution adopts them, so a single-slider move works on a migrated database. A database still on the exact-30 constraint rejects it. Failure on an old DB is atomic. A band change does not reseed existing profiles. Trait evolution already works under the current constraint.

No automatic event rules, probabilistic trait changes, retrospective migration, prose generation, production ingestion scheduling or feature effects are activated. No hired player-agent role is added. Mood and relationships will be separate consumers/producers of verified events, not fields inferred from these sliders.

## Validation and parallel work

Pure tests cover deterministic generated initialization, scopes, sparse real-person evidence, fail-closed views, mutation limits, active slider policy and simulation-evidence permissions. PostgreSQL tests use an explicit scratch DSN and a fresh schema per test; all DDL/data roll back and never touch public profiles. They exercise alias conflicts, insert-once replay, event deduplication, legacy-mutation detection, forced failure after canonical writes and preservation of caller transaction ownership.

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

Actual pipeline identity binding and production migration remain deliberate integration work. Keep feature enablement separate from profile availability.
