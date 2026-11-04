# Project Mu: Personality Evolution Plan

**Status:** approved by JR (2026-11-03), Q1–Q3 settled as recommended (§8); not built. Queued: JR wants every personality sub-plan written and approved before more building (2026-11-03). The other plans' pointers were updated at approval (§7).
**Builds on:** [`PERSONALITY_PLAN.md`](../in_progress/PERSONALITY_PLAN.md) §5 ("Change over time", steps 4–5), D4, D6; [`PERSONALITY_GENERATED_EFFECTS_DESIGN.md`](../in_progress/PERSONALITY_GENERATED_EFFECTS_DESIGN.md) §4 step 6; [`personality/README.md`](../../personality/README.md) (evolution boundary); [`PERSONALITY_CORE_EXTENSIONS_PLAN.md`](../done/PERSONALITY_CORE_EXTENSIONS_PLAN.md) (`kind 'evolution'`, overrides, `history`); [`PERSONALITY_RELATIONSHIPS_PLAN.md`](PERSONALITY_RELATIONSHIPS_PLAN.md) §2.5 (external candidates); [`RELATIONSHIP_EVENT_PRODUCERS_PLAN.md`](../in_progress/RELATIONSHIP_EVENT_PRODUCERS_PLAN.md) (the event inputs); [`REAL_COACH_PROFILES_PLAN.md`](REAL_COACH_PROFILES_PLAN.md) (`persona_for`).

**Goal:** Let big simulated events slowly change who players and coaches are, the way Sims aspirations do. A title can turn a Ring Chaser into a Legacy Builder, a firing can raise a coach's temper, a mentor's retirement can turn a veteran into a Mentor. Each change has its cause logged, so writers can tell the story. At most one change per person per season (D6), decided by fixed rules and a seeded draw, and you can undo any of them.

**Delivers:**
- `personality/evolution_rules.py`: the versioned rule set (`EVOLUTION_RULES_VERSION = "evolution-v1"`), pure.
- `personality/evolution.py`: candidates, the seeded draw, the one-a-season arbiter and change notes, pure.
- `personality/evolution_facts.py`: each person's season facts, read from the database.
- `personality/evolution_store.py` and migration `db/migrations/2027_01_30_personality_evolution.sql`: tables `persona_evolution_runs` and `persona_evolution_candidates`.
- `personality/evolution_run.py`: `python -m personality.evolution_run season|report|revert`.
- Change notes in prompts (`render_persona`, `coach_profiles.persona_for`), and a watcher step at the OFFSEASON_START stop.

**JR's answers (2026-11-03):**
- D1: Seeded rules plus a report. Each rule gives a chance, a seeded draw decides, and the arbiter applies at most one change per person per season automatically. JR gets a season report of every change with its cause, and can undo or change any of them through his override.
- D2: Players and coaches change over time. Writers and GMs keep their authored personalities; JR can still edit them by override.
- D3: The authored voice stays as written. Each change adds a short note built from its receipt ("since the 2029 title he talks more about legacy"), and prompts show the recent notes. No model calls, and every note can be replayed.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `personality/core.py:166–225` `apply_change` takes one slider step or one trait add/drop/replace per season, with `expected_revision`, `rule_version` and `source_event_id`. `personality/store.py:145` `change` persists it with replay protection. The core extensions plan narrows the season rule to `kind = 'evolution'` and removes the real-person evidence gate. | The arbiter submits through `store.change`; nothing here writes profiles directly. Real coaches change like generated players. |
| S2 | The core README's evolution boundary: "The originating policy owns whether the simulated event justifies the candidate". The generated-effects design (§4 step 6) says events "create candidates, not automatic stereotype transformations (a title need not make every winner a Legacy Builder)". | Rules produce candidates with a chance. The seeded draw is what keeps most winners from changing. |
| S3 | The relationships plan §2.5 `validate_evolution_candidate` returns `review_required | ready | blocked` for candidates other features send. The development plan §2.6 says "a cross-feature review coordinator must arbitrate its single annual change slot. That coordinator is not built here". | The arbiter here is that coordinator. It takes this plan's rule candidates plus `ready` external candidates, one slot per person. |
| S4 | The producers plan emits `treatment` (dismissal, recognition), `situation` (award, injury, team_result, job_pressure) and `role_window` events per player and coach, with fixed source IDs. | These, plus `awards`, `games` (SUPER_BOWL), `staff_moves`, `coach_hot_seat`, `player_retirements` and `player_seasons`, are the rule inputs. |
| S5 | `engines/progression.py:579` `find_mentor(player, teammates, awareness)` picks a mentor (same group, age 29+, Awareness 85+); `:1187–1193` uses it per player. `player_progression_history` (`db/init_db.sql:1570–1601`) stores `mentor_term` but not `mentor_id`. | The "mentor retires" rule recomputes last season's mentor with `find_mentor` over last season's rosters instead of reading a stored ID. |
| S6 | PERSONALITY_PLAN §6 rejected shortcuts: Money First does not cause coasting or a post-payday penalty; a contract extension does not prove financial security. | No rule keys on a payday, contract size or Money First. |
| S7 | `newsroom/personas.py:746` `render_persona` builds the prompt block from `voice`, sliders, traits, views and signatures. The coach plan's `persona_for` returns that shape. | Change notes are an optional `change_notes` list in the same dict. `render_persona` shows up to `MAX_CHANGE_NOTES = 2` of them under the voice. |
| S8 | `pipeline/league_office.py:181` `STAGE_WEEKS["OFFSEASON_START"] = 24`. The producers plan's watcher step runs right after each load, on offseason stops too. | The season's evolution runs once per season at tick 24, after the producers step, so all of the season's events are in. |
| S9 | Latest reserved migration date is `2027_01_29` (core extensions). | This plan takes `2027_01_30`. |

## 1. Scope

**In:**
- The rule set for players and coaches (D2), with chances, gates and priorities.
- Season facts per person.
- Rule candidates plus external `ready` candidates.
- The seeded draw, and the arbiter: one change per person per season, applied through `store.change`.
- Candidate and run records, the season report, and revert through the JR override.
- Change notes in prompts (D3).
- The watcher step at OFFSEASON_START, off by default.

**Out (non-goals):**
- Rules for writers and GMs (D2).
- Model-written voice (D3).
- Feature effects of the new traits: each owning plan.
- Other features' own candidate logic: the relationships and development plans send theirs in.
- Mood: the relationships plan, separate from identity.
- Showing changes on the site: the display plan.
- Any rule keyed on money or contracts (S6).

## 2. Design

### 2.1 Facts (`evolution_facts.py`)

```python
def season_facts(cur, universe_id, season) -> dict[str, dict]   # persona_id -> facts
```

Each person's facts dict:
- `{persona_id, role, profile_view, age, tenure_seasons_on_team, team_id, position_group}`;
- `won_super_bowl, made_playoffs, losing_seasons_in_row`;
- `awards: [award_type]`;
- `unsatisfied_role_weeks, released_by_team, signed_elsewhere_after_release`;
- `injury_weeks_out`;
- `mentor_retired` (`find_mentor` on last season's roster, cross-checked with `player_retirements`);
- `coach_fired, coach_promoted_to_hc, coach_kept_on_hot_seat`;
- `source_event_ids: {fact: [ids]}`.

Every fact cites its source rows or relationship events.

### 2.2 Rules (`evolution_rules.py`, `evolution-v1`)

A rule is `{rule_id, roles, when(facts) -> bool, change(facts) -> {kind, slider/step | remove/add}, chance, priority, note}`. `note` is a template filled from facts, for example `"Since the {season} title he talks more about legacy than rings."` The chances are review-only estimates (§8 Q1).

| rule_id | Roles | When | Change | Chance | Priority |
|---|---|---|---|---|---|
| `TITLE_LEGACY` | player, coach | won the Super Bowl; has RING_CHASER; tenure ≥ 4 seasons in the league | replace RING_CHASER with LEGACY_BUILDER | 0.35 | 1 |
| `FIRED_TEMPER` | coach | fired this season | temperament +1 | 0.50 | 1 |
| `MENTOR_RETIRES` | player | his mentor retired; age ≥ 27; no MENTOR or LONER | add MENTOR | 0.25 | 2 |
| `VETERAN_LEADER` | player | age ≥ 31; ≥ 5 seasons on his team; no MENTOR or LONER | add MENTOR | 0.10 | 4 |
| `BENCHED_TEMPER` | player | ≥ 6 eligible unsatisfied role weeks this season | temperament +1 | 0.30 | 2 |
| `CUT_GRUDGE` | player | released by his team this season, then signed elsewhere; no PEACEMAKER | add GRUDGE_HOLDER | 0.15 | 3 |
| `MVP_CONFIDENCE` | player | MVP, OPOY, DPOY or All-Pro first team | confidence +1 | 0.40 | 2 |
| `LONG_INJURY_DRIVE` | player | ≥ 8 weeks out injured | drive +1 | 0.20 | 3 |
| `PROMOTED_CONFIDENCE` | coach | coordinator promoted to head coach | confidence +1 | 0.40 | 2 |
| `LOSING_OPENNESS` | coach | two losing seasons in a row and kept his job | openness +1 | 0.25 | 3 |
| `COY_CONFIDENCE` | coach | Coach of the Year | confidence +1 | 0.40 | 2 |

**Validity:** a candidate whose change would fail core validation is `blocked` with the reason (for example a sixth trait, a conflict, a slider at 10, or a total outside 24–36), and is never forced.

### 2.3 Draw and arbiter (`evolution.py`)

```python
def rule_candidates(facts, rules) -> list[dict]
def draw(universe_id, persona_id, season, rule_id) -> float      # sha256(json ['evolution-v1', universe, persona, season, rule]) first 8 bytes / 2**64
def arbitrate(candidates, external, *, season) -> dict           # {persona_id: {chosen | None, others: [...]}}
def change_event(chosen, profile, season) -> dict                # the store.change event
def change_notes(history, *, as_of, limit=MAX_CHANGE_NOTES) -> list[str]
```

**Arbitration** (D1), per person:
1. Keep the rule candidates whose draw is below their chance (`status 'drawn_out'` otherwise), plus external candidates with status `ready`.
2. Drop those core validation would refuse (`blocked`).
3. Choose the lowest `priority`; ties go to the lowest draw, then `rule_id`.
4. Mark the rest `superseded`.

**The applied change:** `change_event` builds `{event_id: 'evo:<universe>:<persona>:<season>', source_event_id: <first cited fact id>, season, tick: 24, expected_revision, rule_version, reason: <filled note>, kind, ...}`. A person whose `last_changed_season` is already this season (a change applied earlier) is skipped.

**Change notes** (`change_notes`) come from `history()` evolution receipts whose request carries `reason`. They are newest first, and each is prefixed with the season when the template doesn't name it. JR overrides add no note unless his override reason starts with `note:`.

### 2.4 Tables (`2027_01_30_personality_evolution.sql`; requires the core and core-extensions migrations)

```sql
CREATE TABLE persona_evolution_runs (
  universe_id TEXT NOT NULL, season INT NOT NULL, rules_version TEXT NOT NULL,
  input_hash TEXT NOT NULL, counts JSONB NOT NULL,
  status VARCHAR(10) NOT NULL CHECK (status IN ('APPLIED','PREVIEW','FAILED')),
  error TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (universe_id, season, rules_version));
CREATE TABLE persona_evolution_candidates (
  candidate_id TEXT PRIMARY KEY,                -- '<universe>:<persona>:<season>:<rule_id|external id>'
  universe_id TEXT NOT NULL, persona_type VARCHAR(32) NOT NULL, persona_id VARCHAR(64) NOT NULL,
  season INT NOT NULL, source VARCHAR(16) NOT NULL CHECK (source IN ('RULE','RELATIONSHIPS','DEVELOPMENT','OTHER')),
  rule_id TEXT NOT NULL, rules_version TEXT NOT NULL, change JSONB NOT NULL,
  chance NUMERIC(5,4), draw DOUBLE PRECISION, source_event_ids TEXT[] NOT NULL, note TEXT,
  status VARCHAR(12) NOT NULL CHECK (status IN ('drawn_out','blocked','superseded','applied','reverted')),
  reason TEXT, core_event_id TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (universe_id, persona_type, persona_id, season, rule_id));
CREATE INDEX persona_evolution_candidates_season ON persona_evolution_candidates (universe_id, season, status);
```

The migration is transactional, refuses an incompatible existing table, and seeds nothing.

### 2.5 Store, run and report (`evolution_store.py`, `evolution_run.py`)

```python
# evolution_store.py (caller transaction, savepoint, never commits)
def record_candidates(cur, rows) -> int
def record_run(cur, run) -> None
def read_run(cur, universe_id, season, rules_version) -> dict | None
def mark_reverted(cur, candidate_id, override_receipt) -> None
# evolution_run.py
def run_season(cur, universe_id, season, *, dry_run=False, external=()) -> dict
```

**`run_season`:**
1. Read the facts and hash them. If a run with the same key and hash exists, return it.
2. Build candidates, arbitrate, and call `store.change` for each chosen one.
3. Record all candidates and the run.

`dry_run` writes nothing and returns the report.

**CLI** (`DATABASE_URL` or `--dsn`, refusing `madden_2036` and `postgres`):

```text
python -m personality.evolution_run season --season S [--dry-run] [--dsn DSN]
python -m personality.evolution_run report --season S [--out PATH]      # markdown: every applied change with cause, note and draw; counts drawn_out / blocked / superseded
python -m personality.evolution_run revert --candidate ID --reason TEXT [--dsn DSN]
```

**Revert** is a JR override back to the before-state (core extensions `store.override`, actor JR), and marks the candidate `reverted`. It never deletes the evolution receipt.

**Watcher step:** on the OFFSEASON_START ingest (tick 24), `step_personality_evolution(season)` runs after the producers step and before the league office, when the switchboard's `evolution` mode allows it ([activation plan](PERSONALITY_ACTIVATION_PLAN.md); `MU_PERSONALITY_EVOLUTION=off` stays as a kill switch). It never fails the ingest, and it notifies "Mu: N personality changes in <season>", with the report path.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Too many or too few changes a season. | `season --dry-run` on a synthetic 32-team league; report counts by rule. | Tune chances in `evolution-v2`; an old run's receipts keep their version. |
| R2 | Rules pile the same trait onto a population (every veteran a Mentor) over 8 seasons. | A multi-season synthetic run reporting trait shares by age and role. | Lower chances or add gates; no hard caps per trait. |
| R3 | Inputs from the producers or relationships plan aren't built yet. | Facts list a reason per missing source. | Rules whose facts are missing don't fire; others run. |
| R4 | A revert races a later season's change. | Revert checks `expected_revision`. | It refuses on a stale revision; JR uses an override instead. |
| R5 | Shared files with other plans: `newsroom/personas.py` (core extensions U2), `personality/coach_profiles.py` (coach plan U1), `pipeline/watch_save.py` (producers U3). | Build order. | This plan's U3 builds after those land and re-reads them. |

**Spike first:** none. The mentor source is settled (S5).

## 4. Tests (synthetic, no live DB)

- `tests/test_personality_evolution.py`:
  - each rule's `when` and change on fixtures;
  - the draw is deterministic, and changes with season and rule;
  - arbitration: priority, then draw, then rule; external `ready` candidates join and other statuses are ignored;
  - blocked candidates for invalid changes;
  - one change per person and a skip when already changed;
  - `change_event` shape accepted by `core.apply_change`;
  - `change_notes` order, limit and season prefix;
  - no rule reads money or contracts.
- `tests/test_personality_evolution_store.py`:
  - fake cursor: run NOOP on the same hash, candidates written once, revert marks and calls the override, no commit;
  - opt-in scratch DSN: the migration twice, and an incompatible table refused.
- `tests/test_personality_evolution_run.py`:
  - `season_facts` over a fake cursor (Super Bowl, firing, mentor retirement through `find_mentor`, release then signing, role weeks from relationship events);
  - `run_season` end to end against a fake store;
  - `report` output deterministic; CLI exit codes and DSN guard.
- `tests/test_personality_evolution_wiring.py`:
  - `render_persona` shows up to two change notes and nothing when there are none;
  - `persona_for` passes notes through;
  - the watcher step runs only at tick 24 with the flag on and never fails the ingest.

Run with `pytest -m "not integration" tests/test_personality_evolution.py tests/test_personality_evolution_store.py tests/test_personality_evolution_run.py tests/test_personality_evolution_wiring.py`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Rules, draw, arbiter, notes

- **Owns:** `personality/evolution_rules.py` (new), `personality/evolution.py` (new), `tests/test_personality_evolution.py` (new)
- **Reads:** this plan §2.2–§2.3; `personality/core.py` (`apply_change`, `validate_profile`); the core extensions plan §2.1; the relationships plan §2.5
- **Depends on:** none
- **Done when:** the §2.2 rule table and §2.3 functions exist exactly; every case in §4's first bullet passes.
- **Verify:** `pytest -m "not integration" tests/test_personality_evolution.py -q` passes.

### U2: Facts, tables, store, run and CLI

- **Owns:** `personality/evolution_facts.py` (new), `personality/evolution_store.py` (new), `personality/evolution_run.py` (new), `db/migrations/2027_01_30_personality_evolution.sql` (new), `tests/test_personality_evolution_store.py` (new), `tests/test_personality_evolution_run.py` (new)
- **Reads:** U1; this plan §2.1, §2.4–§2.5; `engines/progression.py:573–600`; the producers plan §2.1–§2.2 (event kinds and source IDs); `db/init_db.sql` (awards, games, staff_moves, coach_hot_seat, player_retirements, player_seasons)
- **Depends on:** U1
- **Done when:** facts, store, `run_season`, the report and revert work against fakes; the migration text checks the definition.
- **Verify:** `pytest -m "not integration" tests/test_personality_evolution_store.py tests/test_personality_evolution_run.py -q` passes; `python -m personality.evolution_run --help` exits 0.

### U3: Prompts and watcher

- **Owns:** `newsroom/personas.py`, `personality/coach_profiles.py`, `pipeline/watch_save.py`, `tests/test_personality_evolution_wiring.py` (new)
- **Reads:** U1, U2; `newsroom/personas.py:740–800`; the coach plan §2.2; `pipeline/watch_save.py` (the ingest chain and the producers step)
- **Depends on:** U2. Cross-plan: the core extensions U2, coach U1 and producers U3 are merged first (R5).
- **Done when:** `render_persona` and `persona_for` carry change notes; the watcher step runs at tick 24 behind the flag and never fails the ingest.
- **Verify:** `pytest -m "not integration" tests/test_personality_evolution_wiring.py tests/test_personas.py -q` passes.

## 6. Waves

| Wave | Units | Needs merged first | Why |
|---|---|---|---|
| 1 | U1 | — | Pure rules and arbiter. |
| 2 | U2 | U1 | Uses U1's functions. |
| 3 | U3 | U2 and the cross-plan files (R5) | Touches files other plans own first. |

Building waits until every personality sub-plan is approved (JR, 2026-11-03). After the build, a gate: JR switches `MU_PERSONALITY_EVOLUTION=on` after a `season --dry-run` report.

**File ownership check:**

| File | Unit |
|---|---|
| `db/migrations/2027_01_30_personality_evolution.sql` | U2 |
| `newsroom/personas.py` | U3 |
| `personality/coach_profiles.py` | U3 |
| `personality/evolution.py` | U1 |
| `personality/evolution_facts.py` | U2 |
| `personality/evolution_rules.py` | U1 |
| `personality/evolution_run.py` | U2 |
| `personality/evolution_store.py` | U2 |
| `pipeline/watch_save.py` | U3 |
| `tests/test_personality_evolution.py` | U1 |
| `tests/test_personality_evolution_run.py` | U2 |
| `tests/test_personality_evolution_store.py` | U2 |
| `tests/test_personality_evolution_wiring.py` | U3 |

No path appears twice.

## 7. Spec changes when built

- §1.1 Repository Layout: the five `personality/evolution*` modules, the migration, the four tests, and this plan's entry.
- §1.2: the three `personality.evolution_run` commands and `MU_PERSONALITY_EVOLUTION`.
- §3.3p: `persona_evolution_runs` and `persona_evolution_candidates`.
- §6.0b step 5: the evolution step at the OFFSEASON_START ingest, after the producers step; off by default.
- §7.11: the rule set (`evolution-v1`), the seeded draw, the arbiter (one change per person per season, rule and external candidates), revert by override, and change notes in prompts.
- Other plans, at approval: PERSONALITY_PLAN §5 "Change over time" and the §5.3 tracker point here; the development plan §2.6 and the relationships plan §2.5 name this arbiter as the coordinator.

## 8. Decisions for JR

JR approved all three recommendations on 2026-11-03.

| # | Question | Decision (JR, 2026-11-03) |
|---|---|---|
| Q1 | Are the starting chances right (for example 35% that a Ring Chaser who wins it all becomes a Legacy Builder, 50% that a fired coach's temper rises)? | Use them as review-only starting points. The first dry-run report shows how many changes a season they produce, and you tune from there. |
| Q2 | When a person qualifies for two changes in one season, which wins? | The rule's priority first (a title or a firing beats a slow drift like a veteran becoming a Mentor), then the lower draw. |
| Q3 | Should a reverted change come back as a candidate the next season? | No. A revert marks that rule's candidate for that season only; next season's facts decide afresh, without remembering the revert. |
