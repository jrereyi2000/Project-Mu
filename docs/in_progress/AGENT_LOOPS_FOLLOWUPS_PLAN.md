# Project Mu: Agent Loops Follow-ups Plan

**Status:** approved by JR (2026-11-09); building, wave 1 of 1.

**Goal:** finish the two pieces `COACH_PLAYER_AGENT_LOOPS_PLAN` left unwired: the CPU coach's promises (create and review them in the watcher), and the CPU contract marquee model budget.

**Builds on:** [`COACH_PLAYER_AGENT_LOOPS_PLAN.md`](../done/COACH_PLAYER_AGENT_LOOPS_PLAN.md) (§2.2 promises, §2.3 marquee, §2.5 watcher and league-office wiring); [`RELATIONSHIP_EVENT_PRODUCERS_PLAN.md`](../done/RELATIONSHIP_EVENT_PRODUCERS_PLAN.md) (§2.4 watcher step and role windows); [`PERSONALITY_RELATIONSHIPS_PLAN.md`](../done/PERSONALITY_RELATIONSHIPS_PLAN.md) (`promise_made` / `promise_resolved`).

## 1. Scope

**In:**
- A watcher step that runs the coach loop's promise decisions and due-promise reviews after the relationship producers, for CPU teams only.
- A season marquee floor (the 50th-highest average salary) passed to the CPU contract counter chooser, so a marquee CONTRACT can use the week's model budget.

**Out:**
- Changing `p_promise`, `PROMISE_REVIEW_WEEKS`, promise odds or the decision table.
- Contract acceptance, valuation or legality; chart choices; inbox replies (already wired).
- Activation, tuning or any live-database work.

## 2. Design

### 2.1 Promise watcher (`engines/agent_loops/watcher.py` new, `pipeline/watch_save.py`)

```python
def promise_tick(cur, *, league_id, season, tick, universe_id, mode, user_team_ids, snapshot=None,
                 store=None, llm=None) -> dict
    # mode 'off' -> {"status": "off"}; shadow records decisions, active also appends envelopes.
    # 1. Reviews: coach.review_promises(cur, season, tick, league_id=..., chart_depth=..., injured=...)
    # 2. Creation: for each CPU team, each player with a trailing unsatisfied role-window run
    #    >= PROMISE_MIN_UNHAPPY_WEEKS and a lost base slot: coach.promise_options + coach.decide_promise.
    # Returns {"status", "made", "reviewed", "kept", "broken", "unknown", "deferred", "errors"}.
```

- **Role history** comes from the relationships ledger's `role_window` events for the player on the team, oldest first, mapped to `{tick, assessment}` for `coach.unhappy_weeks`. Read them through the relationship store (`read_events` / `relationship_events.select_active`) the way `player_trade_requests` does, never by re-deriving role.
- **Lost slot:** the base slot where he was a depth-1 starter in the MADDEN chart at `tick - 1` and is not at `tick` (`depth_chart_engine.load` + `starter_counts` / `starters_by_base_slot`, the same rule `kinds/role_talk._starter_slots` uses). No lost slot means no promise option.
- **Player and coach views:** the player is `{player_id, name}` from `players`; the coach view is the `coaching` feature view for the team's head coach (`store.resolve_alias('coach', 'db-coach', id)` + `core.feature_view`), or None (neutral).
- **Envelopes:** `coach.decide_promise` gets resolved PersonRefs (`coach.resolve_person`: coach `db-coach`, player `pgid_namespace(universe)`/`players.madden_pgid`). Active appends `promise_made` and every final `promise_resolved` through `coach.append_events(..., team_registry=sources.team_registry(cur))`; shadow appends nothing (the decision table still records).
- **Tick and teams:** one tick per ingest (`relationship_producers.calendar.tick_for`), CPU teams are every team not in the registry's `user_teams`; JR's coach never runs. `chart_depth` reads the MADDEN chart for the season/tick; `injured` reads the snapshot/`injuries` for that week. A missing MADDEN chart defers a review (`unknown` after its one re-review) as the loop already defines.
- `pipeline/watch_save.py`: `step_coach_promises(snapshot_path, save_season, dsn=None, log_fn=log, connect=None, modes=None)` opens its own connection and transaction, gates on `coach_loop` (shadow or active; `MU_COACH_PROMISES=off` kill switch, `=on` forces on), skips with a logged reason when the decisions/lifecycle tables or the universe are missing, and never fails the ingest. It runs right after `step_relationship_producers` (role events exist) and before the trade-request reviews; the ingest result carries `coach_promises`.

### 2.2 CPU contract marquee floor (`pipeline/league_office_personality.py`)

- `_marquee_floor(cur, season)`: one query over the season's ACTIVE `player_contracts` rows, AAV = `total_value / length` for `length > 0`, then `player_loop.marquee_floor(aavs)` (the 50th-highest). Cached per deps; `None` when the table is missing, the query fails or there are no rows.
- The per-player `counter_chooser` passes that floor as `ctx['marquee_floor']`, so `respond_contract` marks a top-50 ask marquee and `base.decide` may spend the week's `MARQUEE_PER_WEEK` budget on it. The floor being `None` keeps today's policy-only behavior.

## 3. Risks

| # | Risk | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | The watcher reads the ledger before the producers wrote this tick's role windows. | The step runs after `step_relationship_producers`; a failed read is recorded, never fatal. | Skip the tick and log it. |
| R2 | Promises fire twice for one player/week. | `agent_loop_decisions`' unique key (`player:<id>:promise`) and `coach.open_promises`. | The stored decision replays; no second envelope. |
| R3 | Marquee model cost rises. | The existing `MARQUEE_PER_WEEK` budget; a shadow count of model calls. | Lower the budget or leave the floor unset. |

## 4. Tests

- `tests/test_agent_loops_promises_wiring.py`:
  - a promise option appears only after two unsatisfied role weeks and only with a lost base slot;
  - shadow records the PROMISE decision and appends no envelope; active appends `promise_made`;
  - a due promise resolves kept/broken from the MADDEN chart, and injured/unread defers once then `unknown`;
  - JR's teams are skipped; the step is off/skip/failure-safe;
  - replaying a tick appends nothing twice.
- `tests/test_agent_loops_wiring.py` (extend):
  - the chooser receives the 50th-highest AAV as `marquee_floor`; a top-50 ask is marquee;
  - a missing table or empty set gives `None` and today's policy-only behavior.
- Verify:
  - U1: `pytest -m "not integration" tests/test_agent_loops_promises_wiring.py tests/test_watch_save.py tests/test_agent_loops_coach.py -q`
  - U2: `pytest -m "not integration" tests/test_agent_loops_wiring.py tests/test_league_office_personality.py tests/test_free_agency_engine.py -q`

## 5. Work breakdown

### U1: Coach-promise watcher

- **Owns:** `engines/agent_loops/watcher.py` (new), `pipeline/watch_save.py`, `tests/test_agent_loops_promises_wiring.py` (new)
- **Reads:** `COACH_PLAYER_AGENT_LOOPS_PLAN.md` §2.2, §2.5; `engines/agent_loops/coach.py`; `pipeline/watch_save.py` (`step_relationship_producers`, `step_trade_requests`, the ingest sequence); `engines/relationship_producers/{sources,run,calendar}.py`; `personality/relationship_store.py`; `engines/player_trade_requests.py` role-window helpers; `engines/inbox/kinds/role_talk.py` (`_starter_slots`); `engines/depth_chart_engine.py`.
- **Depends on:** none.
- **Done when:** §2.1 works in shadow and active, JR's teams never run it, and the ingest carries `coach_promises`.
- **Verify:** `pytest -m "not integration" tests/test_agent_loops_promises_wiring.py tests/test_watch_save.py tests/test_agent_loops_coach.py -q`

### U2: CPU contract marquee floor

- **Owns:** `pipeline/league_office_personality.py`, `tests/test_agent_loops_wiring.py`
- **Reads:** `COACH_PLAYER_AGENT_LOOPS_PLAN.md` §2.3, §2.5; `engines/agent_loops/player.py` (`marquee_floor`); `engines/agent_loops/base.py` (the budget); `db/migrations/2026_12_07_contracts.sql` (`player_contracts`).
- **Depends on:** none.
- **Done when:** §2.2 supplies the floor and the model budget applies to a marquee CPU CONTRACT; missing data keeps today's behavior.
- **Verify:** `pytest -m "not integration" tests/test_agent_loops_wiring.py tests/test_league_office_personality.py tests/test_free_agency_engine.py -q`

## 6. Waves

| Wave | Units | Needs merged first | Why |
|---|---|---|---|
| 1 | U1, U2 | — | Disjoint files; U2 is small and independent of the watcher. |

**File ownership check:**

| File | Unit |
|---|---|
| `engines/agent_loops/watcher.py` | U1 |
| `pipeline/watch_save.py` | U1 |
| `tests/test_agent_loops_promises_wiring.py` | U1 |
| `pipeline/league_office_personality.py` | U2 |
| `tests/test_agent_loops_wiring.py` | U2 |

No path appears twice.

## 7. Spec changes when built

- §1.1 Repository Layout: `engines/agent_loops/watcher.py`, the new test, and this plan.
- §2.21a: the marquee floor and the promise watcher step.
- §6.0b: the `step_coach_promises` bullet in the watcher-step list.
- §7.11: promises now have a caller; update the "not wired yet" note.

## 8. Decisions for JR

None open. Defaults: reviews run before creation each tick; shadow records decisions only; the marquee floor is the season's 50th-highest AAV.

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
