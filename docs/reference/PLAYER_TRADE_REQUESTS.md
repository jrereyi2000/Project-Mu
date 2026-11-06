# Player trade requests: reference and handoff guide

What is built (plan: [`PERSONALITY_TRADE_REQUESTS_PLAN.md`](../in_progress/PERSONALITY_TRADE_REQUESTS_PLAN.md)), what it does, and what a later integration has to wire. **Nothing here is live.** No production caller, no applied migration, no production policy. Personality effects are inactive.

## What a request is (and is not)

A request records that a player wants to be moved after verified, sustained dissatisfaction. It is **not** trade execution, a price discount, a waived no-trade clause or a player veto. The GM engine keeps valuation, legality, acceptance and execution. The contract policy keeps willingness to extend or re-sign.

## Pieces

| File | Role |
|---|---|
| `engines/player_trade_requests.py` | Pure policy: types, qualification, the seeded annual draw, the lifecycle, projections, consumer handoffs and the adapter from the relationships plan's shared outputs. |
| `engines/player_trade_request_store.py` | Persistence for ENABLED mode only, inside the caller's transaction. |
| `db/migrations/2027_01_25_player_trade_requests.sql` | `player_trade_request_decisions`, `player_trade_requests`, `player_trade_request_actions`. **Not applied anywhere.** |
| `tools/player_trade_request_preview.py` | Offline synthetic report (disabled/shadow), personality versus neutral matched arms. |

## Inputs

- **Incidents** come from the relationships plan (`personality/reactions.py`, `personality/relationships.py`) through `incidents_from_shared(events, relationship_state, reaction_result, team_id=, team_key=, prior_incident_ids=)`:
  - a sustained-role `frustrated` reaction gives `LOST_ROLE`, with its unsatisfied `role_window` run as the observations;
  - an unresolved material breach (a `dismissal`/`disrespect` treatment or a `broken` promise) gives `TREATMENT`/`BROKEN_PROMISE`, with the contiguous known role windows since the breach as the observations;
  - a prior cause that has stopped being open becomes `REPAIRED`, with the accepted repairs (for a breach) or the trailing satisfied windows (for a role run) as repair IDs.
  A reaction result in `off` mode gives no incidents. Unknown windows break runs and never become grievances.
- **Identity**: the core `persona_id` through a verified `db-player` alias. The view is `personality.core.feature_view(profile, 'trade_requests')`. A missing profile is the neutral baseline. This feature never initializes a profile.
- **Clock**: `clock_ordinal(season, tick)` = `season * 1000 + tick` on the relationships calendar (32 ticks a season). Window kind and `trades_open` come from the GM engine's own window facts, never recomputed here.
- **Authority**: `control_context(team_id, user_team_ids=, suggestions_on=)`. JR's teams come from the franchise registry's `user_teams`, never from Madden's ALLUSER flag (all 32 teams are user teams in Madden to stop CPU moves). `user_team_ids=None` gives `UNKNOWN`, and unknown authority blocks automated responses.

## Decisions

- Qualifying: four eligible observations of lost role, or two after a broken promise or material treatment, all certified and open on the current team. Disputed, unknown, repaired or other-team causes never qualify, and a severe incident has no immediate exception. An incomplete assessment or a closed window defers, without using the season's draw.
- **One draw per (universe, persona, season)**: SHA-256 of `["trade-request-v1", universe_id, persona_id, season]`, first 8 bytes big-endian / 2^64. `NO_REQUEST` uses the season's draw. Retries, new incidents, a team change, renamed checkpoints and policy changes cannot redraw. An active request carries over seasons without another draw.
- Trait rule (`trait-v1`, an offline proposal): DIVA or GRUDGE_HOLDER selects the higher odds and PEACEMAKER the lower. Both together give the baseline. The rule never stacks. No production odds exist. `offline-illustration-v1` (.05/.10/.15) is for fixtures and is refused in ENABLED mode.

## Lifecycle

Statuses: `ACTIVE`, `WITHDRAWN`, `RESOLVED_TRADE`, `CLOSED_EMPLOYMENT`, `VOIDED_CORRECTION`. Terminal episodes never reopen. The team's response (`NONE`/`ACKNOWLEDGED`/`DECLINED`/`EXPLORING`) is tracked separately.

- A response from JR (on JR's teams) or an explicit GM response event (on simulated teams) changes the response only. A refusal is not a withdrawal.
- `REVIEW_REPAIR` withdraws the request (`RECONCILED`) only when every cause has been repaired for two eligible observations, with repair IDs, and no other cause is open. One restored game or a new promise is not enough.
- Only an **executed** trade off the requesting team resolves a request (`RECORD_TRADE`). A proposed, approved, rejected or expired deal never does.
- A verified departure (`RELEASED`/`EXPIRED`/`RETIRED`/`OTHER_DEPARTURE`) closes the request, and an unknown one defers. An inbox dismissal is not a player withdrawal.
- `CORRECT` either voids the request (the initial evidence was invalid) or links to it, keeping history and never redrawing.

## Privacy

Requests start private. `request_projection(..., audience='FRONT_OFFICE')` gives JR the factual status, response, `actionable` (status active and trade window open) and cause codes, never traits, odds, draws or relationship scores. `'PUBLIC'` gives nothing until a verified `DISCLOSE`, and then only the disclosed status with its date. `current` turns false once the real status moves on without a new disclosure. There is no automatic leak.

## Consumer handoffs (built as functions, not wired)

- **Contract policy**: `contract_request_context(episode)` gives `{schema_version, request_id, status, team_id, unresolved_incident_ids, source_event_ids, as_of_ordinal, policy_version}`, or `{status: 'ABSENT'}`. It carries no refusal flag and no utility delta. Incident and source IDs are the contribution keys, so relationship deterioration, the request and a GRUDGE_HOLDER read charge one incident once. Request status is never a new incident.
- **GM engine**: `gm_request_signal(episode, clock=)` gives `{request_id, persona_id, team_id, status, cause_codes, actionable}` for an active request. It is a signal only: no destination, deadline or price change.

## Store

`record_decision(cur, context, clock, policy, mode='ENABLED')` and `apply_action(cur, request_id, action, context, clock, policy)` each run in a savepoint under a per-persona advisory lock. Each one checks the core binding, then looks up an exact replay by event ID (a review's event ID is `review:<season>:<window_id>:<checkpoint_id>`) before checking revisions. Each one rejects mixed rewinds and appends a receipt for every review, no-op and deferred ones included. SHADOW persists nothing, and the caller commits. Scratch tests: `TRADE_REQUESTS_TEST_DSN=<empty mu_scratch_* database> .venv/bin/python -m pytest -m 'not integration' tests/test_player_trade_request_store.py -q`.

## Preview

```text
python -m tools.player_trade_request_preview --example /tmp/tr_example.json
python -m tools.player_trade_request_preview --input /tmp/tr_example.json --out /tmp/tr_report [--mode disabled|shadow]
```

The report shows matched personality and neutral arms, eligibility versus draws, annual use, reconciliation, front-office and public projections, the contract and GM handoffs, an incident contribution trace and a sensitivity grid over the supplied policies. These are synthetic demonstrations, not calibration.

## Deferred (not built here)

- Scheduling the reviews in the watcher, building `RequestContext`/`RequestClock` from live data, adding the GM search signal (`gm_trade_engine.run_week(request_signals=)`) and the switchboard: the [activation plan](../not_started/PERSONALITY_ACTIVATION_PLAN.md)'s U3.
- Contract willingness that reads the request context: the contract policy, with incident deduplication and its own activation review.
- Inbox cards, newsroom coverage and API/frontend display, which belong to their owners. Holdouts, fines and effort or playing-time consequences need a separate plan.
- Gates still open: G3 (JR reviews sensitivity and picks a production policy), G4 (integration) and G5 (applying the migration and activating for a chosen franchise and checkpoint).
