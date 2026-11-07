# Project Mu: Retirement Window Plan

**Status:** approved by JR (2026-11-07, his own message: "Approve both.", with the INFERRED_SLIDERS_AND_FOCUS_PLAN; Q1–Q7 as recommended except Q3, see §8); building, wave 2 of 2 done.
- **As built (U1):** `classify` adds a `STILL_RETIRED` case (list `carried`): a restore candidate already `Retired` in the previous run's image was reported at an earlier stop (blocked, e.g. no proof), so it is carried quietly instead of being re-raised as `NO_BASELINE` every stop. `stop_name(stage, wave)` and `proof_from_env()` are small extra helpers; the `user_team` gate is `USER_TEAM_FROM_SEASON = 2026` (JR's Q3) instead of `JR_TAKEOVER_SEASON = 2034`; `notice` returns `(line, priority)` and keeps restores blocked only by `authority_missing`/`stage_mismatch` at default priority.
- **As built (U2):** the census also prints purged retirees and `max_row` (highest Player row seen, for spike 2's named-vs-rows count); a snapshot whose stage can't be told shows stop `?`.
- **As built (U3):** `Deps.retirement_window_inputs` also returns `decided` = the decision set plus every player a window run restored (keep True), so a late native restored once and retired again is `RE_RETIRED_AGAIN`, not restored a second time. A third Deps method, `player_names(cur, player_ids)`, names vanished players for the notification. With a proof, a decision-stage keep the planner refuses stays retired (row `window:blocked:<STAGE>:<reason>`); a proof that doesn't list the decision stage falls back to the legacy free-agent writer. `NO_BASELINE` rows use `window:conflict:<STAGE>:no_baseline`. The roster image stores what the write puts in the save (kept and restored players at their written status/team, `Plan.image_overrides`); the printed run report leaves the image out (`report_summary`). The watcher also adds the window line to the "week done" notification for PRESEASON/CUTDOWN saves (they take the weekly path), with warning tags when high.
**Builds on:** [`PERSONALITY_RETIREMENT_PLAN.md`](../done/PERSONALITY_RETIREMENT_PLAN.md) §2.4–2.6 (gates G4–G6), [`RETIREMENT_AUTHORITY.md`](../reference/RETIREMENT_AUTHORITY.md) (A1–A12, spikes 1–4), [`PERSONALITY_ACTIVATION_PLAN.md`](../done/PERSONALITY_ACTIVATION_PLAN.md) (`PersonalityDeps.retirement_decision`, switchboard), PROJECT_SPEC §2.17f, §2.21a, §3.3q, §3.3w, §6.0b; [`MADDEN_OFFSEASON_WALKTHROUGH.md`](../reference/MADDEN_OFFSEASON_WALKTHROUGH.md).

**Goal:** Madden can retire players at more than one offseason stop. JR's idea (2026-11-07): Project Mu makes every retirement call for a season once, at the first offseason stop it handles, and any native retirement that shows up after that is undone automatically at the next ingest, before Madden purges the row. This plan adds that "one decision per offseason" rule to the league office. It reuses the retirement plan's default-deny planner for every restore, so late restores stay off until the restore proof (G4) passes on JR's PC. Until then the window still detects, records and reports late retirements and any player who vanished without a trace.

**Delivers:**
- `pipeline/retirement_window.py` (new, pure): `classify`, `restore_requests`, `planner_snapshot`, `authority_for_run`, `load_proof`, `roster_image`, `notice`.
- League office: a **decision stage** (the first offseason stop with a WRITTEN run for the season, normally `OFFSEASON_START`) and a `step_retirement_window` at every later offseason stop through `CUTDOWN`; `summary["retirement_window"]` on every run (decision set, restores, conflicts, vanished players, `roster_image`).
- Two `Deps` methods: `retirement_window_inputs(cur, season)` and `retirement_authority()`; env var `MU_RETIREMENT_AUTHORITY` (path to a JR-approved proof file; unset = default deny).
- Watcher: one notification line per offseason ingest (`retirement_window_notice`), high priority when a rostered player vanished or a restore hit a conflict.
- `tools/retirement_stage_census.py` (new, offline): per-stage counts of new retirees, restores and vanished players from a series of reader snapshots, for JR's PC check.
- `docs/reference/RETIREMENT_AUTHORITY.md`: a §5 PC checklist and the proof-file format.

No migration: the decision record lives in `league_office_runs.summary` (JSONB, written with the run) and the existing `retirement_decisions` projection.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `plan_stage` runs `step_retirements` only when `stage == OFFSEASON_START` (`pipeline/league_office.py:1734–1736`); every other offseason stop runs only `step_restore_retirees` for already-kept players (`:1737–1741`). | A player Madden retires at RESIGN or later is never recorded or restored today. He is purged at the rollover. This is the gap. |
| S2 | `step_retirements` records each `Retired` player in `retirement_decisions` and, when kept, sets `ContractStatus = FreeAgent, TeamIndex = 32` directly (`pipeline/league_office.py:1419–1436`). This writer is live and ungated (A4). | The decision stage already exists. This plan generalises "the first stop" and adds the late window; it does not remove the legacy writer before G4 (Q1). |
| S3 | `Deps.retirement_decision` returns `keep_retired` (`pipeline/league_office.py:974–977`); `PersonalityDeps.retirement_decision` swaps in the retirement policy by switchboard mode and never retires an active player (`pipeline/league_office_personality.py:370–389`; `personality/features.py:52`, `:76`). | The window does not decide anything itself. It reads whatever the decision stage decided, so it works the same whether `retirement` is off, shadow or active. |
| S4 | `plan_retirement_edits` denies by default: it needs an authority whose `source_save_hash`, `stage` and `schema_version` match the snapshot, `exclusive_owner true`, and a proven `KEEP_NATIVE_RETIREE` (`pipeline/retirement_edits.py:35–50`, `:77–145`). A restore copies the baseline's `ContractStatus` and `TeamIndex` (`:32`, `:129–133`); a `Deleted` row, a missing baseline or a pending move for the person is a conflict. | The planner binds authority to one save hash, which never matches a live save. The window needs `authority_for_run` to stamp a proven proof onto the current run (§2.4). The proof must list every stage the window restores at. |
| S5 | `KEEP_STATES` is `AVAILABLE`, `SIGN_PENDING`, `RETURNING`, `UNSIGNED_RETURN_ELIGIBLE` (`pipeline/retirement_edits.py:29`). `NATIVE_RETIREMENT_OBSERVED` moves `AVAILABLE`/`SIGN_PENDING`/`UNSIGNED_RETURN_ELIGIBLE` to `CONFLICT` (`engines/career_decision_state.py:170–174`); `RETURNING` is terminal and rejects every event (`:164`). | Emitting `NATIVE_RETIREMENT_OBSERVED` for a late retiree would block his own restore. v1 of the window does not emit it; it passes the career state (or `AVAILABLE` when there is none) to the planner (§2.6). The retirement plan's "native retirement after a Mu return is a conflict that pauses advance" (§2.6 there) is replaced by a bounded auto-restore (Q2). |
| S6 | `retirement_decisions(season, player_id, madden_retired, mu_keep, reason)`, PK `(season, player_id)` (`db/init_db.sql:1541–1548`); the league office upserts it after a successful write (`pipeline/league_office.py:1921–1925`); `kept_retirees` reads `madden_retired AND mu_keep` (`:1031–1034`). | One row per player per season already exists and is idempotent. It cannot tell a decision-stage row from a later one, so the authoritative decision set goes in the decision-stage run's summary (§2.2). |
| S7 | `league_office_runs` has `summary JSONB` (`db/init_db.sql`, §3.3q), inserted with the PLANNED run (`pipeline/league_office.py:1879`) and kept on WRITTEN (`:1890`, `:2013`). `Plan.finalize` builds the summary dict (`:706–776`); `Plan.view` holds every player's `(status, team_index)` after this run's moves (`:485`). | The decision set and a per-run roster image fit in `summary` with no migration. Only WRITTEN runs count. |
| S8 | The watcher ingests each offseason stop once: it skips a stop that already has a WRITTEN or PLANNED run (`pipeline/watch_save.py:954–967`, `:1276–1290`), and sends one "offseason stage done" notification (`:1112–1116`). | The window runs at most once per stop. If JR doesn't save at a stop, the next ingest sees two stops' worth of changes (`stage_gap`). |
| S9 | The reader drops `Deleted` and unnamed rows (`pipeline/save_reader/pc_save_reader.mjs:300`); `load_save` loads only `Signed`, `PracticeSquad`, `FreeAgent` (`pipeline/load_save.py:91`, `:421`). | A player retired and purged inside one advance simply disappears from the snapshot. Detection must compare with the previous run's roster image (§2.5). |
| S10 | R4 (all-user manual franchise, 2026-10-31): 59 `Retired` from the first offseason save, 60 by the draft recap; rows turn `Deleted` only at the rollover into the preseason (`docs/done/V2_CORE_BUILD_PLAN.md:20`). The walkthrough also counts 4 `Retired` in the preseason save (`docs/reference/MADDEN_OFFSEASON_WALKTHROUGH.md:35`). | Late retirements do happen (one in that test), and every stop before the rollover is catchable. The rollover advance is the one that can retire and purge at once, so `PRESEASON` and `CUTDOWN` are in the window and the census must check them. CPU-controlled teams were not tested. |
| S11 | Offseason stops and their league office stages: `OFFSEASON_STOPS` (`pipeline/league_office.py:169–178`); `PRESEASON`/`CUTDOWN` use `season = calendar year − 1` (`:163`, `:305–308`). | The window's season is `league_season`, so a retirement at the rollover still belongs to the offseason it ends. |
| S12 | `RETIREMENT_AUTHORITY.md` §4 lists four open PC spikes (restore proof G4, purge stage, retire-active proof, Arbitrator decision). | The PC checklist (§2.9) extends spikes 1 and 2 to cover late stops; spikes 3 and 4 are unchanged. |
| S13 | Tests for the area: `tests/test_league_office.py` (fake `Deps`), `tests/test_retirement_edits.py`, `tests/test_watch_save.py`. | New tests stay synthetic and offline. |
| S14 | Migrations through `2027_02_02` are taken or reserved (`db/migrations/`, `docs/in_progress/PERSONALITY_PLAN.md:190`). | This plan needs none. |

## 1. Scope

**In:**
- A decision stage per offseason: the first offseason stop the league office handles for that season (normally `OFFSEASON_START`). It runs today's `step_retirements` through `deps.retirement_decision` and stores the full decision set in its run summary.
- At every later stop (`RESIGN` … `UDFA`, `PRESEASON`, `CUTDOWN`): find native retirements Mu didn't decide, and players Mu kept who were retired again; restore them through `plan_retirement_edits` when a proven authority covers that stop; otherwise record and report them only.
- A bounded re-restore rule (`MAX_WINDOW_RESTORES = 1` per player per season), then a conflict.
- Detection of players who vanished between two runs (retired and purged in one advance), logged, recorded and reported as unrecoverable.
- A roster image on every league office run (the baseline the planner needs).
- Watcher notification; offline stage census tool; PC checklist and proof-file format in the reference doc.
- After G4 only: decision-stage keeps also go through the planner (baseline status and team instead of FreeAgent).

**Out (non-goals):**
- Retiring a player Madden kept (`RETIRE_ACTIVE`). No write path exists (A7); it needs spike 3 and its own plan (phase 2, §2.8).
- Running the PC checks, writing the proof file, or applying anything to a live save. JR does those.
- Wiring the career state machine (`career_decision_states`) into the window. That waits for the retirement feature's activation (G5/G6); §2.6 says what changes then.
- Recovering a `Deleted` player. Never promised (A2/A3).
- Changing `keep_retired`'s rule or the retirement policy's numbers.
- Fixing `players.retired_season` or ledger scan completeness (A8; continuity owner).
- The Contract Retirement Arbitrator. JR's keep/remove decision stays open in the authority doc (Q5).

## 2. Design

### 2.1 Run order per offseason

| Stop | What runs |
|---|---|
| Decision stage (first WRITTEN offseason run of season S; normally `OFFSEASON_START`) | `step_retirements` (unchanged rule, by switchboard mode). Its decisions go to `retirement_decisions` as today and to `summary["retirement_window"]["decided"]`. |
| Every later stop through `CUTDOWN` | `step_retirement_window` runs right after the drift steps and before re-signings, free agency, the draft and cut-down, so a restored player is never also signed or cut in the same write. `step_restore_retirees` runs after it as today. |
| In season | No window step; only `roster_image` is stored (the next `OFFSEASON_START` uses the last playoff run's image as its baseline). |

`OFFSEASON_START`'s salary cap and promoted practice squads stay tied to `OFFSEASON_START`, not to the decision stage.

If the Staff save was missed (no WRITTEN `OFFSEASON_START` run for S), the first offseason stop Mu does handle becomes the decision stage (Q4). Retirements Madden made at the skipped stop are then decided by `keep_retired`/the policy, not auto-restored.

### 2.2 The decision record (no migration)

The decision-stage run's summary carries:

```json
"retirement_window": {
  "version": "retirement-window-v1",
  "role": "decision",
  "decided": {"<pgid>": {"player_id": 123, "keep": true, "reason": "kept: age 30 < 33 with OVR 81 >= 75"}},
  "roster_image": {"<pgid>": ["Signed", 7]},
  ...
}
```

- **One per player per season.** `decided` has one entry per pgid. The decision stage is the first WRITTEN offseason run with `role = "decision"` for season S. There is only one: the watcher never re-runs a stop that has a WRITTEN or PLANNED run (S8), and `step_retirement_window` runs instead whenever a decision run exists.
- **Idempotent.** Replanning the same snapshot gives the same `decided`, the same edits and the same `edits_hash`. Window rows are upserts on `retirement_decisions (season, player_id)`.
- **Recover-safe.** The summary is written with the PLANNED run and kept by `--recover` (S7). `retirement_window_inputs` reads WRITTEN runs only. A PLANNED decision run blocks the watcher until it is recovered, so no later stop can run against a half-written decision.
- `retirement_decisions` stays the compatibility projection. Late rows use these `reason` prefixes: `window:restored:<STAGE>`, `window:blocked:<STAGE>:<first conflict reason>`, `window:conflict:<STAGE>:re_retired_again`, `window:vanished:<STAGE>`.

Later runs carry `role = "window"` and the lists from §2.3 (`restored`, `blocked`, `conflicts`, `vanished`, `purged`, `stage_gap`) plus their own `roster_image`.

### 2.3 Classifying a later stop (pure)

`pipeline/retirement_window.py`:

```python
WINDOW_VERSION = 'retirement-window-v1'
WINDOW_STAGES = ('RESIGN', 'FREE_AGENCY', 'COMBINE', 'PRO_DAY', 'DRAFT', 'UDFA', 'PRESEASON', 'CUTDOWN')
STOP_ORDER = ('OFFSEASON_START', 'RESIGN', 'FREE_AGENCY:1', 'FREE_AGENCY:2', 'COMBINE', 'PRO_DAY', 'DRAFT', 'UDFA',
              'PRESEASON', 'CUTDOWN')
MAX_WINDOW_RESTORES = 1
ACTIVE_STATUSES = ('Signed', 'FreeAgent', 'PracticeSquad')
FREE_AGENT_TEAM_INDEX = 32
JR_TAKEOVER_SEASON = 2034
AUTHORITY_ENV = 'MU_RETIREMENT_AUTHORITY'
PROOF_KIND = 'retirement-authority-proof-v1'

class WindowError(ValueError): ...

def roster_image(view: dict[int, tuple[str | None, int | None]]) -> dict[str, list]
def classify(players: list[dict], *, stage: str, wave: int | None, season: int,
             decided: dict[str, dict], prior_image: dict[str, list] | None, prior_stop: str | None,
             window_restores: dict[str, int], user_team_indexes: frozenset[int] = frozenset()) -> dict
```

`players` is the reader snapshot's `players` list. Per pgid:

| Case | Rule | Output |
|---|---|---|
| `DECIDED_RETIRED` | `Retired` now and `decided[pgid].keep is False` | `decided_retired` (left alone) |
| `LATE_NATIVE` | `Retired` now, pgid not in `decided` | `restore` entry |
| `RE_RETIRED` | `Retired` now, `decided[pgid].keep is True`, `window_restores[pgid] < MAX_WINDOW_RESTORES` | `restore` entry |
| `RE_RETIRED_AGAIN` | as above with `window_restores[pgid] >= MAX_WINDOW_RESTORES` | `conflicts` (not restored) |
| `NO_BASELINE` | a restore candidate whose `prior_image` entry is missing or not in `ACTIVE_STATUSES` | `conflicts` |
| `VANISHED_ROSTERED` | in `prior_image` with an active status and a team index ≠ 32, absent from `players` | `vanished` |
| `VANISHED_FREE_AGENT` | same, with team index 32 | `vanished` (could be a free-agent purge) |
| `PURGED` | `Retired` in `prior_image`, absent now | `purged` (expected) |

Return shape:

```json
{"version": "retirement-window-v1", "season": 2026, "stage": "RESIGN", "stop": "RESIGN", "prior_stop": "OFFSEASON_START",
 "stage_gap": false,
 "decided_retired": [111],
 "restore": [{"pgid": 222, "kind": "LATE_NATIVE", "baseline": {"status": "Signed", "team_index": 7}, "user_team": false}],
 "conflicts": [{"pgid": 333, "kind": "RE_RETIRED_AGAIN", "reason": "re_retired_after_window_restore"}],
 "vanished": [{"pgid": 444, "kind": "VANISHED_ROSTERED", "last": {"status": "Signed", "team_index": 3}}],
 "purged": [555]}
```

`stage_gap` is true when `prior_stop` is not the stop right before this one in `STOP_ORDER` (JR skipped a save). Lists are sorted by pgid. `user_team` is true when `season >= JR_TAKEOVER_SEASON` and the baseline team is in `user_team_indexes` (§2.7).

### 2.4 Authority for a live run

The proof file JR approves after G4 (`MU_RETIREMENT_AUTHORITY=/path/proof.json`):

```json
{"kind": "retirement-authority-proof-v1", "version": "retirement-authority-v1", "fixture_id": "g4-2026-11-xx",
 "proof_save_hash": "<sha256 of the proof save>", "schema_version": 5,
 "stages": ["OFFSEASON_START", "RESIGN", "FREE_AGENCY", "..."], "exclusive_owner": true,
 "actions": {"KEEP_NATIVE_RETIREE": {"proven": true, "proof_id": "...", "fields": ["ContractStatus", "TeamIndex"]}},
 "approved_by": "JR", "approved_on": "2026-11-xx"}
```

```python
def load_proof(path: str | None) -> dict | None
def authority_for_run(proof: dict | None, *, source_save_hash: str, schema_version: int) -> dict | None
```

- `load_proof(None)` → None. A file that exists but is malformed, has another `kind`, lacks `approved_by`/`approved_on`, or lists `RETIRE_ACTIVE` as proven raises `WindowError` (a broken proof should be loud, not silently deny). An unreadable path also raises.
- `authority_for_run` returns None for no proof. Otherwise it returns the planner's authority shape: `{version, fixture_id, source_save_hash: <this run's save>, schema_version: proof.schema_version, stages, exclusive_owner, actions}`. It stamps the current hash only because the proof covers the class of save (schema version + stages), not one file. The planner still checks stage and schema, so a stop the proof didn't cover, or a reader schema change, still denies.
- `schema_version` is the snapshot's `meta.reader_version` (`pipeline/save_reader/pc_save_reader.mjs:482–483`).

### 2.5 The restore path

```python
def restore_requests(report: dict, career_states: dict[str, str] | None = None) -> list[dict]
def planner_snapshot(players: list[dict], *, source_save_hash: str, stage: str, schema_version: int,
                     prior_image: dict[str, list], pending: dict) -> dict
```

- `restore_requests` turns each `restore` entry into `{decision_id: "window:<season>:<pgid>", pgid, action: "KEEP_NATIVE_RETIREE", state}`, with `state = career_states.get(pgid, "AVAILABLE")` (§2.6).
- `planner_snapshot` builds `plan_retirement_edits`' snapshot: `baseline` from `prior_image` (`{pgid: {status, team_index}}`), `pending` from the league office's own moves already claimed in this plan (so the planner's overlap check also covers Mu's moves).
- `step_retirement_window(plan, deps, cur, source_fingerprint)` in `pipeline/league_office.py`:
  1. `inputs = deps.retirement_window_inputs(cur, plan.season)`; `report = rw.classify(...)`.
  2. `authority = rw.authority_for_run(deps.retirement_authority(), source_save_hash=source_fingerprint, schema_version=snapshot["meta"]["reader_version"])`, where `source_fingerprint` is the input-save SHA-256 `plan_stage` already receives (`pipeline/league_office.py:1688`); `step_retirement_window` takes it as an argument.
  3. `out = plan_retirement_edits(rw.planner_snapshot(...), rw.restore_requests(report), authority)`.
  4. Each edit → `plan.set_fields(pgid, set)` and `plan.view[pgid] = ("Retired", None)` (as `step_retirements` does: signable only in a later write). `plan.counts["window_restored"] += 1`.
  5. Each planner conflict → `blocked` with its reasons; `plan.warn` one line per player.
  6. `db["retirements"]` gets one row per restore (`mu_keep true`), block or conflict (`mu_keep false`) and `VANISHED_ROSTERED` (`madden_retired true, mu_keep false`), with the §2.2 reason prefixes.
  7. `summary["retirement_window"]` gets `role "window"`, the report lists, `restored`, `blocked` and `roster_image`.
- Without a proof (today, until G4) every late retiree lands in `blocked` with `authority_missing`. Nothing is written for him and he is purged at the rollover. The report and notification say so each stop (observe-only, Q1).
- With a proof, the decision stage also routes its keeps through the planner: baseline status and team from the previous run's image (no FreeAgent default; the retirement plan §2.6), falling back to the legacy writer only when the authority does not cover `OFFSEASON_START`. `step_restore_retirees` then finds those players already on a team and skips them.
- **Retired and deleted within one advance.** `VANISHED_ROSTERED` is unrecoverable. The window logs it (`plan.warn`), records it, and the watcher sends a high-priority notification naming the player, his last team and the stop. The only fallback is manual: JR reloads the previous stop's save (if he kept it) before advancing again. The census (§2.9) shows whether any stop does this. If one does, that stop goes on JR's list of "save before and after" stops; Mu cannot prevent it, because nothing marks a row as "do not retire".

### 2.6 Switchboard and the career state machine

- `retirement` off, shadow or active changes only how the decision stage decides (S3). The window treats every decision the same way.
- v1 never calls `retirement_store` and never emits `NATIVE_RETIREMENT_OBSERVED` (S5). `Deps.career_states` is not added; `restore_requests` receives `None` and uses `AVAILABLE`, meaning "Mu decided he continues this season".
- When the retirement feature is activated (G5/G6, separate go-ahead), its owner adds a window event kind to `engines/career_decision_state.py` that records a late native retirement without moving `AVAILABLE`/`RETURNING` to `CONFLICT`, and passes real career states. A player whose state is `RETIRE_INTENT`/`RETIRE_PENDING`/`CONFLICT` is then refused by the planner's `KEEP_STATES` check, which is the right outcome. This plan's §7 records the handoff; it does not edit that file.

### 2.7 The user team

- Through 2033 Mu runs every team the same way (retirement plan Q2), so the window treats the user team like any CPU team.
- From 2034, a late retirement on JR's team is still restored (a restore can be undone by releasing the player; a purge can't) and flagged `user_team` in the notification so JR can cut him if he agrees with Madden (Q3). The decision stage keeps the retirement plan's review hold.

### 2.8 Phase 2: Mu-chosen extra retirements (not built here)

At the decision stage, the retirement policy could also pick players Madden kept. That needs spike 3 (retire-active proof: cap, dead money, history, next advance), a `RETIRE_ACTIVE` entry in the proof file, and the career state machine (`PREPARE_RETIREMENT` → `RETIRE_PENDING` with an operation id). The window rule then extends naturally: a player Mu retired who is later found active again is a conflict, never re-retired automatically. A separate plan after spike 3.

### 2.9 PC verification checklist for JR

Runs on a disposable `CAREER-TUNE-*` clone with the watcher paused (`/madden` can drive the sims). Nothing touches the live franchise.

1. **Stage census (spike 2, extended).** Use CPU-controlled teams as in a Mu lineage, not only all-user. Save at all 8 offseason stops, preseason week 1 and the cut-down week. Read each with `pc_save_reader.mjs`, then run `python3 -m tools.retirement_stage_census <snapshots in order> --out census.json`. It answers: at which stops new `Retired` players appear; whether any rostered player vanishes without first showing as `Retired` (the unrecoverable case); and at which advance `Retired` rows disappear. Estimated one session.
2. **Restore proof G4 (spike 1, extended).** Restore three players (expired contract, 2+ years left, practice squad) at `OFFSEASON_START`, and three at one stop after the contract rollover (`FREE_AGENCY` wave 1) and one late stop (`DRAFT` or `UDFA`). Advance and read back status, team, contract row, roster/depth/PS lists, cap and transactions at the next stop and after the rollover into the preseason.
3. **Re-retirement check.** In the same run: does Madden retire a restored player again at a later stop? (Sets whether `MAX_WINDOW_RESTORES = 1` is enough.)
4. **Write the proof file** in §2.4's format, listing only the stops that passed, and set `MU_RETIREMENT_AUTHORITY`. JR approves it by filling `approved_by`/`approved_on`.
5. **Arbitrator (spike 4).** Keep it uninstalled for Mu lineages, or the proof sets `exclusive_owner false` and every restore is blocked.
6. **Later: retire-active proof (spike 3)**, only for phase 2.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | A stop retires and purges in one advance (most likely the rollover: 4 `Retired` in the preseason save, S10). | Census (§2.9 step 1). | `VANISHED_ROSTERED`: logged, recorded, notified; manual reload is the only fallback. |
| R2 | A restore doesn't stick, or Madden re-retires restored players every stop. | §2.9 steps 2–3. | `MAX_WINDOW_RESTORES` stops the loop and raises a conflict; drop the stop from the proof. |
| R3 | Restoring after the contract rollover (FA prep) behaves differently from `OFFSEASON_START` (contract row, cap). | G4 covers at least one post-rollover stop. | The proof's `stages` lists only proven stops; others stay observe-only. |
| R4 | G4 is not run soon, so late retirees keep being purged. | Status quo. | Observe-only reports every case; JR can choose the legacy writer instead (Q1). |
| R5 | `VANISHED_FREE_AGENT` mixes free-agent purges with retirements. | Census. | Reported at default priority only; never recorded as a retirement. |
| R6 | `roster_image` grows `league_office_runs`. Estimate: about 3,300 entries, about 60 KB a run, 25–30 runs a season. | Measure the first stored run. | Store only the image of players who are not `Retired`, or keep images for offseason and playoff runs only. |
| R7 | Two restore owners (the Arbitrator). | Spike 4. | The proof's `exclusive_owner false` blocks every restore. |
| R8 | JR skips a save, so two stops' changes arrive together. | `stage_gap` in the report. | Still caught while rows stay `Retired` (purge at the rollover per S10); the notification says a stop was skipped. |
| R9 | After G4 the decision stage restores baseline status instead of FreeAgent, which changes what `step_restore_retirees` sees. | `tests/test_league_office.py` case. | `step_restore_retirees` already skips players not in free agency. |

**Spike first:** none blocks the build: the window is observe-only until the proof exists. The census (§2.9 step 1) is worth running early because R1 decides how much of the window can ever work. JR on the PC, one session, any time.

## 4. Tests (synthetic, no live DB)

- `tests/test_retirement_window.py`:
  - `classify`: decided-retired left alone; late native → restore with baseline from `prior_image`; re-retired once → restore; again → `RE_RETIRED_AGAIN`; missing or inactive baseline → `NO_BASELINE`; vanished rostered vs free agent; purged; `stage_gap`; `user_team` only from 2034; sorted, deterministic output.
  - `roster_image` round trip; `restore_requests` shape and `AVAILABLE` default.
  - `load_proof`: None path; malformed, wrong kind, unapproved, or proven `RETIRE_ACTIVE` → `WindowError`.
  - `authority_for_run` + the real `plan_retirement_edits`: no proof → every request blocked `authority_missing`; proven synthetic proof → edits restore the baseline status and team; uncovered stage → `stage_mismatch`; schema change → `schema_mismatch`; pending move → overlap conflict.
- `tests/test_league_office.py` (fake `Deps`): decision stage is `OFFSEASON_START` normally and `RESIGN` when no `OFFSEASON_START` run exists; the decision summary holds `decided`; a later stop restores a late native with a proof and only reports it without one; the restored player is not signed by free agency in the same plan; `retirement_decisions` rows carry the reason prefixes; every run's summary has `roster_image`; the same snapshot replans to the same `edits_hash`; after G4 the decision-stage keep uses the baseline team.
- `tests/test_watch_save.py`: `retirement_window_notice` line text; high priority with `VANISHED_ROSTERED` or a conflict; no line when the summary lacks `retirement_window`.
- `tests/test_retirement_stage_census.py`: per-snapshot counts, new retirees, un-retired players, vanished pgids with last status, JSON output; no DB import, no subprocess.

Run with `.venv/bin/python -m pytest -m "not integration" tests/test_retirement_window.py tests/test_league_office.py tests/test_watch_save.py tests/test_retirement_stage_census.py -q`. No integration tests.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Pure window logic

- **Owns:** `pipeline/retirement_window.py` (new), `tests/test_retirement_window.py` (new)
- **Reads:** this plan §2.2–2.6, `pipeline/retirement_edits.py`, `engines/career_decision_state.py`, `docs/reference/RETIREMENT_AUTHORITY.md`
- **Depends on:** none
- **Done when:** every function in §2.3–2.5 exists with the exact signatures and shapes; the module imports no database, subprocess or league office code (it may import `pipeline.retirement_edits` in tests only).
- **Verify:** `.venv/bin/python -m pytest -m "not integration" tests/test_retirement_window.py tests/test_retirement_edits.py -q` passes.

### U2: Stage census tool and PC checklist

- **Owns:** `tools/retirement_stage_census.py` (new), `tests/test_retirement_stage_census.py` (new), `docs/reference/RETIREMENT_AUTHORITY.md`
- **Reads:** this plan §2.4, §2.9; `pipeline/save_reader/pc_save_reader.mjs` (snapshot shape); `pipeline/league_office.py` `stage_and_wave`
- **Depends on:** none
- **Done when:** `python3 -m tools.retirement_stage_census A.json B.json … [--out PATH]` prints a per-snapshot table (stop, named players, `Retired`, new retirees, un-retired, vanished rostered / free agent) and writes JSON; it has no DSN flag, opens no save and launches nothing. The reference doc gains §5 (PC checklist from §2.9 and the proof-file format from §2.4) and spikes 1–2 point at it.
- **Verify:** `.venv/bin/python -m pytest -m "not integration" tests/test_retirement_stage_census.py -q` passes; `python3 -m tools.retirement_stage_census --help` exits 0.

### U3: League office and watcher wiring

- **Owns:** `pipeline/league_office.py`, `tests/test_league_office.py`, `pipeline/watch_save.py`, `tests/test_watch_save.py`
- **Reads:** this plan §2.1–2.7, `pipeline/retirement_window.py` (U1), `pipeline/league_office_personality.py`, `pipeline/retirement_edits.py`
- **Depends on:** U1
- **Done when:** `plan_stage` picks the decision stage per §2.1; `step_retirement_window` runs at every later offseason stop before signings; `Deps.retirement_window_inputs` and `Deps.retirement_authority` exist (WRITTEN runs only; env `MU_RETIREMENT_AUTHORITY`); every run's summary carries `retirement_window` with `roster_image`; the watcher adds `retirement_window_notice` to the offseason notification. Re-read `pipeline/league_office.py` and `pipeline/watch_save.py` on main before editing (other plans touch them).
- **Verify:** `.venv/bin/python -m pytest -m "not integration" tests/test_league_office.py tests/test_watch_save.py tests/test_league_office_personality.py -q` passes.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2 | — | No shared files; U2 does not import U1. |
| 2 | U3 | U1 | Calls U1's functions. |

**File ownership check:**

| File | Unit |
|---|---|
| `docs/reference/RETIREMENT_AUTHORITY.md` | U2 |
| `pipeline/league_office.py` | U3 |
| `pipeline/retirement_window.py` | U1 |
| `pipeline/watch_save.py` | U3 |
| `tests/test_league_office.py` | U3 |
| `tests/test_retirement_stage_census.py` | U2 |
| `tests/test_retirement_window.py` | U1 |
| `tests/test_watch_save.py` | U3 |
| `tools/retirement_stage_census.py` | U2 |

No path appears twice.

## 7. Spec changes when built

- §2.21a League Office: the decision stage (first WRITTEN offseason run of the season), `step_retirement_window` at `RESIGN`…`CUTDOWN`, the run order, `summary["retirement_window"]` (roles, lists, `roster_image`), `MU_RETIREMENT_AUTHORITY`, observe-only until G4, decision-stage keeps through the planner after G4.
- §2.17f Retirement Policy: the window is the planner's first caller; its gates; the handoff for the career state machine (§2.6 here).
- §3.3q League Office & Franchise State: `league_office_runs.summary.retirement_window` shape; `retirement_decisions` late-row reason prefixes.
- §3.3w Retirement Policy: no schema change; note the window does not write the sidecars in v1.
- §6.0b PC Save Watcher: the retirement-window line and its priority rule.
- Repository Layout: `pipeline/retirement_window.py`, `tools/retirement_stage_census.py`, `tests/test_retirement_window.py`, `tests/test_retirement_stage_census.py`.
- `docs/in_progress/PERSONALITY_PLAN.md` tracker: link this plan from the Retirement decisions row (orchestrator).

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | Until the restore proof (G4) passes, should late retirements only be detected and reported, or restored now with the legacy writer `OFFSEASON_START` already uses (FreeAgent, team 32, unproven)? | Report only. The legacy writer's FreeAgent default is what the retirement plan said not to rely on, and G4 is one PC session that also covers late stops. |
| Q2 | When Madden retires a player Mu already kept or restored, restore him again? | Yes, once per player per season (`MAX_WINDOW_RESTORES = 1`); a second re-retirement is a conflict that is reported and left retired. This replaces the retirement plan's "pause the advance" rule for this case, because a paused advance can't stop the purge. |
| Q3 | From 2034, should a late retirement on JR's own team be restored automatically or held for him? | Restore and flag it. A restore can be undone by a release; a purge can't. **JR (2026-11-07):** restore and flag, but not "from 2034": JR's real franchise starts in 2026, so the `user_team` flag applies from the franchise's first season (no season gate). |
| Q4 | If the Staff save is missed, which stop decides the season? | The first offseason stop Mu handles. The alternative (refuse until a Staff run exists) would leave the whole offseason undecided. |
| Q5 | Keep the Contract Retirement Arbitrator? | Leave it uninstalled for Mu lineages (already open in the authority doc); with it installed every restore stays blocked. |
| Q6 | Mu-chosen extra retirements: in this plan or later? | Later, in its own plan after the retire-active proof (spike 3). |
| Q7 | Record a rostered player who vanished in one advance as a retirement (`retirement_decisions`, `madden_retired true`)? | Yes for rostered players only, with reason `window:vanished:<STAGE>`; free agents who vanish are reported but not recorded. |

Assumptions (no clarifying round was possible; JR approved writing this up): Q1–Q7 recommendations are used in §2 as written.

**JR's decisions (2026-11-07):** Q1, Q2, Q4–Q7 as recommended. Q3 as recommended (restore and flag) but applied from the franchise's first season (2026), not from 2034; §2.3's `user_team` and §2.7 are read that way when building.

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | build/rw-u1 | .claude/worktrees/rw-u1 | merged | 5e41e39 |
| U2 | 1 | build/rw-u2 | .claude/worktrees/rw-u2 | merged | 6021ffd |
| U3 | 2 | build/rw-u3 | .claude/worktrees/rw-u3 | merged | 42734a9 |

