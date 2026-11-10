# Project Mu: Retirement Window Signing Restore Plan

**Status:** approved by JR (2026-11-09); building, wave 1 of 1 done.

**As built (2026-11-09):**
- U2: `_apply_restores` became `_apply_free_agent_flip`, shared by the window and the proven decision-stage keep (both `FreeAgent`/32, view Retired, image `FreeAgent`/32, summary `restore: free_agent`); `restored_retirees` counts the sign-back, with `no_restorable_contract` and `restore_at_last_stop` warnings (the last only on a CUTDOWN restore, in the window). The sign-back test pins the existing stage-offset writer block (a `Length`-2 block with an empty offset year at `RESIGN`). Edge note: if the decision stage itself ever falls at CUTDOWN (a missed Staff save), the proven keep has the same next-run signing risk and does not warn.
- U1: `free_agents` is the raw `League.FreeAgents` list, which also holds every practice-squad player (Madden's list semantics; `sign.mjs` header); the real free agents in it are status `FreeAgent` on team 32. The roster-length check uses `cap.SalCapRosterSize` (the snapshot has no `ActiveRosterSize`). The two existing node tests (`sign.test.mjs`, `release.test.mjs`) exclude the new team keys from their whole-object comparison.
**Builds on:** PROJECT_SPEC §2.17f and §2.21a, [`RETIREMENT_WINDOW_PLAN.md`](../done/RETIREMENT_WINDOW_PLAN.md), and [`RETIREMENT_G4_RUNBOOK.md`](../reference/RETIREMENT_G4_RUNBOOK.md) session 3.

**Goal:** The retirement window restores a native retiree by editing two fields. The G4 proof showed that
state is not durable: Madden creates no contract or roster entry, and two of three such players were deleted
at the cut-down advance. The sequence that did survive is the legacy one — set the player `FreeAgent`/32,
then sign him back to his old team with his stored remaining contract. Make the window use that sequence,
export roster, practice-squad and free-agent membership from the reader so the proof can record them, and
rerun the proof.

**Delivers:**
- `pc_save_reader.mjs` reader_version 7: `teams[].roster`, `teams[].practice_squad`, top-level `free_agents`.
- `step_retirement_window` and the proven `step_retirements` branch restore by the legacy free-agent flip;
  the existing `step_restore_retirees` signs the player at the next run with the stored contract.
- `summary["retirement_window"]` per-player `restore` kind and `window_free_agent`; a `restored_retirees`
  count for the signing run.
- Tests and the proof rerun procedure (three branches, reader v7).

**JR's answers (2026-11-09):**
- D1 The single-write "sign a `Retired` player" idea is dropped. Restore with the proven legacy sequence:
  free agent first, then the existing sign-back step at the next run.
- D2 "If we can't make him a free agent, let's leave him as retired." Read as: always flip the native
  retiree to `FreeAgent`/32; if a stored contract runs into the league year, `step_restore_retirees` signs
  him back; otherwise he stays a free agent. Mu does not invent contract terms. Confirmed with the approval
  (2026-11-09).
- D3 Reader v7 exports roster, practice squad and free agents.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | Session 3: field-only restores kept `ContractStatus`/`TeamIndex` but got no contract or roster entry, and 2 of 3 were deleted at cut-down; the free-agent-then-sign sequence survived purge, cut-down and 2027 week 1 with the contract, roster entry and a Madden depth slot (`docs/reference/RETIREMENT_G4_RUNBOOK.md`). | Restore must use the free-agent-then-sign sequence. |
| S2 | `league_office.py:1718 step_restore_retirees` already signs a kept retiree back with his stored contract: `y = target - start`, `rest` at `:1730`, `sign_player` at `:1732`. It needs the player to be `FreeAgent` (`:1724`). | The sign half already exists; the window only has to produce the free agent. |
| S3 | `league_office.py:1083 kept_retirees` selects `retirement_decisions` rows with `madden_retired AND mu_keep`; the window records every restore with `mu_keep=True` (`:1689`). | The next run already picks up a window restore for the sign-back. No new bookkeeping. |
| S4 | `league_office.py:1659 step_retirement_window` applies the planner's edits through `_apply_restores` (`:1598`), which sets the baseline status/team only. | This is the function to change: apply the free-agent flip instead. |
| S5 | `league_office.py:1611 step_retirements`, with a proven authority and the stage listed, also restores through `_apply_restores` (baseline team) instead of its legacy free-agent branch. | Align it to the same flip, or an approved proof would reintroduce the non-durable state at the decision stage. |
| S6 | `league_office.py:2039` calls `step_restore_retirees` for `kept` retirees at every non-`OFFSEASON_START` stage, including in-season saves. | A restore is signed at the next run, whatever its stop. |
| S7 | `pc_save_reader.mjs:43 READER_VERSION = 6`; `:160 readTeams` is synchronous and exports no lists; `:346 readDepthCharts` shows the async Player[] pattern the lists need. | Add an async `readLists`, export the three lists and bump the version. |
| S8 | `load_save.py:26-34` gates optional loads on `reader_version` and ignores unknown keys. | A v7 snapshot with new keys does not break the loader. |
| S9 | `retirement_window.py:190 authority_for_run` stamps the snapshot's `schema_version`; the planner refuses a mismatch. | A v7 reader makes v6 proofs unusable until the proof is rerun. Expected. |
| S10 | `tests/test_league_office.py:1594`, `:1614` and `:1647` assert the current baseline-team restores. | They state the old design and must change. |
| S11 | The node save tests skip without a real save (`read_save.test.mjs:10-13`). | Their verification runs on JR's PC with `MU_SAMPLE_SAVE`. |
| S12 | `tools/retirement_g4_fixture.py` already builds free-agent-then-sign branches (`contract_from`). | The proof rerun reuses it; no new tool. |

## 1. Scope

**In:**
- Reader v7 with `teams[].roster`, `teams[].practice_squad`, `free_agents`.
- The window's restore (and the proven decision-stage keep) writes the free-agent flip; the next run signs
  back through the existing `step_restore_retirees`.
- Summary additions: per-player `restore: free_agent`, `window_free_agent`, `restored_retirees`.
- Tests for all of the above.
- The proof rerun procedure and a reader-v7 proof (`schema_version` 7).

**Out (non-goals):**
- A one-write `Retired → Signed` sign and any change to `sign.mjs` (dropped by JR, D1).
- `RETIRE_ACTIVE` (the window plan's phase 2) and the retire-active proof.
- Inventing contract terms (minimums) when no stored contract exists.
- Any migration, API endpoint or UI change.
- Teaching `load_save` to consume the new lists (it does not need them).

## 2. Design

### 2.1 Reader v7 lists

- `pc_save_reader.mjs`: `READER_VERSION = 7`.
- New `readLists(tables, teamTable, playerTableId, pgidByRow)` next to `readDepthCharts`:
  - per real team (`0 <= TeamIndex < REAL_TEAMS`): `roster` and `practice_squad` as pgid arrays in list
    order, from the team's `Roster` and `PracticeSquad` Player[] records; a reference to a player the
    snapshot does not export is dropped (same rule as depth charts).
  - the league free-agent list from the one-row League table (`League.FreeAgents`, the `sign.mjs:290`
    pattern) as a top-level `free_agents` array.
- Attach the two lists to the matching `teams[]` entry by `team_index`; no other key changes.

### 2.2 The restore is a free-agent flip plus the existing sign-back

**Window (`step_retirement_window`).** For each allowed edit in `out["edits"]["players"]` (the planner has
authorized the restore), do what the legacy decision branch does (`:1631-1636`):

- `plan.set_fields(g, {"ContractStatus": "FreeAgent", "TeamIndex": 32})`
- `plan.view[g] = ("Retired", None)` and `plan.image_overrides[g] = ("FreeAgent", 32)`
- record the player with `mu_keep=True` (as today, `:1689`), `restore: "free_agent"`, and
  `plan.counts["window_free_agent"] += 1` (plus `window_restored`).

The `edit`'s baseline status/team are no longer written; the planner still decides *whether* the restore is
allowed (authority, stage, pending moves, baseline). The proof's fields stay `ContractStatus`/`TeamIndex`.

**Decision stage (`step_retirements`).** With a proven authority the keep now takes the same free-agent
branch instead of `_apply_restores`. The planner call stays (it is the authorization and conflict list); only
the applied target changes.

**The sign-back.** At the next non-`OFFSEASON_START` run, `step_restore_retirees` finds the player because
`retirement_decisions.mu_keep` is true and his roster image is `FreeAgent`, and signs him to his old team
with his stored remaining contract (`:1718-1732`). Add `plan.counts["restored_retirees"] += 1` per signing
so the summary names it. No change to `step_restore_retirees`'s eligibility math.

**Timing.** `step_restore_retirees` runs in the same run, after the window, but `kept_retirees` reads the
database, so this run's restore is signed by the *next* run. Consequences:
- A restore at `OFFSEASON_START` is signed at `RESIGN`; one at `FREE_AGENCY` wave 1 at wave 2; one at `UDFA`
  at the 2027 preseason week 1.
- A restore at `CUTDOWN` is signed at 2027 week 1, after the free-agent purge in that advance. It may not
  survive. The window should record such a restore and the run should warn (`restore_at_last_stop`); do not
  claim the `CUTDOWN` stage in the proof until it is tested.

**No contract.** If `step_restore_retirees` finds no stored contract running into the league year, it does
nothing; the player stays a free agent (D2). Record it in that run's warnings as `no_restorable_contract`.

`_apply_restores` becomes unused and is removed.

### 2.3 Interfaces units build against

- Snapshot: `teams[i].roster: [int]`, `teams[i].practice_squad: [int]`, top-level `free_agents: [int]`,
  `meta.reader_version = 7`.
- Summary: `retirement_window.restored[]` entries carry `restore: "free_agent"`; counts `window_restored`,
  `window_free_agent`; a later run's `restored_retirees` for the sign-back.

### 2.4 Proof and rerun

- `schema_version` for the proof becomes 7 (the reader version at proof time). The draft proof is
  regenerated; old v6 snapshots no longer satisfy a v7 proof.
- Rerun: rebuild the three branches with `tools/retirement_g4_fixture.py` using `contract_from` (free-agent
  flip + sign) at `OFFSEASON_START`, `FREE_AGENCY` wave 1 and `UDFA`; JR advances each as in the runbook;
  read back with the v7 reader.
- Record per player: status, team, `PlayerContract` row, `teams[].roster` / `practice_squad` membership,
  `free_agents` absence, depth chart, cap. Approve only if every restored player is signed at the next stop
  and, for `UDFA`, survives cut-down and 2027 week 1.
- Add one extra branch at `CUTDOWN` to test the last-stop timing before any stage claims it.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | A restored player is a free agent for one advance; he must survive it and be signed at the next run. | The proof rerun (UNFAd → preseason week 1 is the tightest). | If he is purged, revisit the one-write sign (writer change) as a separate plan. |
| R2 | A `CUTDOWN` restore is signed only at 2027 week 1, after the free-agent purge. | An extra `CUTDOWN` branch in the rerun. | Do not claim `CUTDOWN` in the proof; note the limitation. |
| R3 | The free-agent flip leaves the player in the FA list; another engine could sign him before the next run. | The league office's FA wave could claim him. | Accept: he was a free agent by Mu's own choice; record it. |
| R4 | Reader v7 invalidates every v6 proof/snapshot. | Expected: `authority_for_run` reports `schema_mismatch`. | Regenerate the proof; the rerun uses fresh saves. |
| R5 | A stale `sign`-time contract (`y = target - start`) gives the wrong years. | Unit test with a known contract; the PC read-back. | The player stays a free agent and the run warns. |

**Spike first:** none. The session-3 run already proved the free-agent-then-sign sequence on a real save;
the rerun tests the production run cadence.

## 4. Tests (synthetic, no live DB)

- `pipeline/save_reader/test/read_save.test.mjs` → v7: every team has `roster`/`practice_squad` arrays,
  every pgid in them exists in `players`, no duplicates, `free_agents` players are `FreeAgent` on team 32,
  a roster's length matches `ActiveRosterSize`. Skips without a save.
- `tests/test_league_office.py` →
  - a window restore with a proof writes `{ContractStatus: FreeAgent, TeamIndex: 32}`, `restore:
    free_agent`, `window_free_agent` 1, and no baseline-team edit;
  - the proven decision-stage keep also writes the free-agent flip (update
    `test_decision_stage_keep_uses_the_baseline_team_with_a_proof`);
  - a following run with the same retiree marked `mu_keep` and a stored contract queues a `sign` move to
    his old team with the remaining contract and counts `restored_retirees`;
  - no stored contract → no sign, warning `no_restorable_contract`;
  - no proof → still blocked `authority_missing`, no edit;
  - a `CUTDOWN` restore records the last-stop warning.
- Existing planner tests (`tests/test_retirement_edits.py`) unchanged; `tests/test_retirement_window.py`
  unchanged.

Run with `pytest -m "not integration" tests/test_league_office.py` and, on the PC,
`cd pipeline/save_reader && MU_SAMPLE_SAVE=<a G4 save> npm test`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`,
`docs/reference/RETIREMENT_AUTHORITY.md` (§5.2 schema note) and `docs/reference/RETIREMENT_G4_RUNBOOK.md`.

### U1: Reader v7 lists

- **Owns:** `pipeline/save_reader/pc_save_reader.mjs`, `pipeline/save_reader/test/read_save.test.mjs`, `pipeline/save_reader/test/sign.test.mjs` (the two-line `teamsApartFromCap` update only), `pipeline/save_reader/test/release.test.mjs` (same)
- **Reads:** `pipeline/save_reader/lib.mjs` (`arrayRefs`, `arraySlots`), `pipeline/save_reader/sign.mjs`
  (the `League.FreeAgents` pattern), `pipeline/load_save.py` (version gating)
- **Depends on:** none
- **Done when:** a v7 snapshot carries `teams[].roster`, `teams[].practice_squad` and `free_agents`; the
  loader accepts it unchanged.
- **Verify:** `cd pipeline/save_reader && MU_SAMPLE_SAVE=<save> node --test test/read_save.test.mjs` green
  (with a real save); without one it skips.

### U2: Restore by the free-agent flip and sign at the next run

- **Owns:** `pipeline/league_office.py`, `tests/test_league_office.py`
- **Reads:** `pipeline/retirement_window.py` (`classify`, `restore_requests`, `authority_for_run`),
  `pipeline/retirement_edits.py`
- **Depends on:** none
- **Done when:** an authorized window restore and a proven decision-stage keep both write `FreeAgent`/32;
  the next run's `step_restore_retirees` signs the player back with his stored contract and counts
  `restored_retirees`; no proof still blocks; a last-stop restore warns.
- **Verify:** `python3 -m pytest -m "not integration" tests/test_league_office.py -q` green.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 1 | U1, U2 | — | Different files, no dependency: the reader lists and the restore mechanism are independent. |

**File ownership check:**

| File | Unit |
|---|---|
| `pipeline/save_reader/pc_save_reader.mjs` | U1 |
| `pipeline/save_reader/test/read_save.test.mjs` | U1 |
| `pipeline/save_reader/test/sign.test.mjs` | U1 (two-line helper update) |
| `pipeline/save_reader/test/release.test.mjs` | U1 (two-line helper update) |
| `pipeline/league_office.py` | U2 |
| `tests/test_league_office.py` | U2 |

**After the wave merges:** the proof rerun (§2.4) is JR's PC step — rebuild the signed branches on a
`CAREER-TUNE-*` lineage, advance each, read back with the v7 reader, then fill the proof. It is not a unit
and changes no code.

## 7. Spec changes when built

- §2.17f: the window and the proven decision-stage keep restore as free agents; the next run signs them
  with the stored contract; the no-contract outcome and the last-stop warning; reader v7.
- §2.21a: the run order's restore step and the `restored_retirees` count.
- §1.2 / save-reader docs: snapshot `reader_version` 7 and the new lists.
- `docs/reference/RETIREMENT_AUTHORITY.md` §5.2: `schema_version` 7 and the roster/PS/FA read-back.
- `docs/reference/RETIREMENT_G4_RUNBOOK.md`: the rerun procedure and result table.
- Repository Layout: no new files; the reader version note changes.

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | Confirm D2's reading: always flip to free agent, sign back only when a stored contract applies, otherwise he stays a free agent. | Yes, as written. Your sentence could also mean "leave him retired when no contract exists" — say which. |
| Q2 | Keep the proof's `fields` at `ContractStatus`/`TeamIndex`? | Keep the two fields; the free-agent flip sets both, and the sign-back is the existing verified mechanism. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 Reader v7 lists | 1 | `retire-reader-v7` | `.claude/worktrees/retire-reader-v7` | merged | `6e1120a` |
| U2 Free-agent flip restore | 1 | `retire-window-flip` | `.claude/worktrees/retire-window-flip` | merged | `802e4cd` |
