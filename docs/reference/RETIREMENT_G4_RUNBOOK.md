# Retirement restore authority (G4) — PC runbook

Working document for **CLOSE_OUT_CHECKLIST P1-07**, following
[`RETIREMENT_AUTHORITY.md`](RETIREMENT_AUTHORITY.md) §5.1 exactly. Status 2026-11-09: **sessions 1–2
done on a `CAREER-TUNE-RETIRE` clone with CPU-controlled teams** (§Session 2 results below); session 3
(the restore proof) is next and still needs JR's PC. The earlier census (2026-11-07) used JR's all-user
offseason saves. `CAREER-MUBASE` is never simulated or edited.

The proof file (`verify_output/retirement_g4/proof_draft.json`) is a **draft**: `proven` is false, the
hash/stages/fixture are TODOs, and `approved_by` / `approved_on` are empty for JR. `retirement_window.load_proof`
refuses it until JR fills and approves it, which is the intended state.

## What only JR can do

The pilot sims regular-season weeks but cannot drive the offseason (post-season screens are not in
`catalog.json`), so every offseason stop below is a manual Madden session. Mu's side — reading the
snapshots, the census, the restore planner, the read-backs and the proof — is automated.

## Session 1 — clone and reach the offseason

1. Confirm the watcher is stopped: `ps -eo args | grep "[p]ipeline[.]watch_save"` prints nothing.
2. Clone (a file copy; no Madden UI):
   ```bash
   python3 -m tools.madden_pilot clone-save --from CAREER-MUBASE --to CAREER-TUNE-RETIRE
   ```
3. Sim the regular season on the clone (`/madden`; the pilot loads only `CAREER-TUNE-*`):
   ```bash
   python3 -m tools.madden_pilot sim-season --save CAREER-TUNE-RETIRE --to regular-season-end
   ```
4. Playoffs and the transition to `OFFSEASON_START` are manual until the post-season screens are
   catalogued. Save at `OFFSEASON_START`.

## Session 2 — the stage census (spike 2, extended)

Save the franchise at each of the 10 stops, with CPU-controlled teams as in a Mu lineage:

| # | League-office stop | Madden stop (from `OFFSEASON_STOPS`) |
|---|---|---|
| 1 | `OFFSEASON_START` | Staff (week 0 / offseason stage 1) |
| 2 | `RESIGN` | Re-sign |
| 3 | `FREE_AGENCY` wave 1 | FA prep |
| 4 | `FREE_AGENCY` wave 2 | Free agency |
| 5 | `COMBINE` | Combine |
| 6 | `PRO_DAY` | Pro day |
| 7 | `DRAFT` | Draft |
| 8 | `UDFA` | Recap / UDFA |
| 9 | `PRESEASON` | preseason week 1 |
| 10 | `CUTDOWN` | cut-down week (`PRESEASON_CUTDOWN_WEEK`, an estimate) |

For each save:

```bash
node pipeline/save_reader/pc_save_reader.mjs "<save file>" > verify_output/retirement_g4/snap_<n>_<stop>.json
```

Then, in save order:

```bash
python3 -m tools.retirement_stage_census verify_output/retirement_g4/snap_*.json \
    --out verify_output/retirement_g4/census.json
```

The census answers: at which stops new `Retired` players appear; whether any rostered player vanishes
without first showing as `Retired` (the unrecoverable case); and at which advance `Retired` rows disappear.

### Session 2 — results (2026-11-09)

JR advanced the `CAREER-TUNE-RETIRE` lineage with CPU-controlled teams and saved every stop, plus the
remaining preseason weeks and 2027 week 1 (13 saves, hashes below). Snapshots and census:
`verify_output/retirement_g4/snap_01…13*.json`, `census.json` (git-ignored, as usual for `verify_output/`).

| # | snapshot | Madden week index | stop | named | Retired | new | un-ret | purged | vanished |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `snap_01_OFFSEASON_START` | 0 (stage 1) | `OFFSEASON_START` | 3,238 | 78 | — | — | — | — |
| 2 | `snap_02_RESIGN` | 1 | `RESIGN` | 3,238 | 78 | 0 | 0 | 0 | 0 |
| 3 | `snap_03_FREE_AGENCY_W1` | 2 | `FREE_AGENCY:1` | 3,238 | 90 | **12** | 0 | 0 | 0 |
| 4 | `snap_04_FREE_AGENCY_W2` | 3 | `FREE_AGENCY:2` | 3,238 | 90 | 0 | 0 | 0 | 0 |
| 5 | `snap_05_COMBINE` | 5 | `COMBINE` | 3,238 | 90 | 0 | 0 | 0 | 0 |
| 6 | `snap_06_PRO_DAY` | 7 | `PRO_DAY` | 3,238 | 90 | 0 | 0 | 0 | 0 |
| 7 | `snap_07_DRAFT` | 11 | `DRAFT` | 3,238 | 90 | 0 | 0 | 0 | 0 |
| 8 | `snap_08_UDFA` | 15 | `UDFA` | 3,238 | 90 | 0 | 0 | 0 | 0 |
| 9 | `snap_09_PRESEASON_W1` | 0 (2027 PS) | `PRESEASON` | 3,150 | 2 | 0 | 0 | **88** | 0 |
| 10 | `snap_10_PRESEASON_W2` | 1 | `PRESEASON` | 3,150 | 2 | 0 | 0 | 0 | 0 |
| 11 | `snap_11_PRESEASON_W3` | 2 | `PRESEASON` | 3,150 | 2 | 0 | 0 | 0 | 0 |
| 12 | `snap_12_CUTDOWN` | 3 | `CUTDOWN` | 3,150 | 2 | 0 | 0 | 0 | 0 |
| 13 | `snap_13_WEEK1_2027` | 0 (2027 reg.) | `REGULAR_WEEK` | 3,200 | 2 | 0 | 0 | 0 | 337 FA |

Answers:

- **New `Retired` appears at `FREE_AGENCY` wave 1** (the week-1 → week-2 advance): 12 players, all
  `Signed` before and all with exactly **1 contract year left** (2027; `years_remaining = length − current_year`). Madden also sets `TeamIndex 32` and
  **removes the `PlayerContract` row** in the same advance. 7 of the 12 (age ≤ 32, OVR ≥ 75) meet
  `keep_retired`'s always-keep rule, so they are exactly the class the retirement window restores.
- **No rostered player vanished without first showing `Retired`** at any saved stop: the unrecoverable
  case did not occur.
- **`Retired` rows are purged entering the 2027 preseason** (UDFA → preseason week 1): 88 of 90 gone.
- **The purge is not exhaustive:** Travis Kelce (pgid 890) and Aaron Rodgers (pgid 26668) stay `Retired`
  through 2027 week 1. Both were among the 78 already retired at `OFFSEASON_START`; no trait, age or OVR
  rule distinguishes them. Cause unknown; note it before relying on "every Retired row is purged".
- No un-retirements at any stop.
- The 78 already retired at `OFFSEASON_START` all had `TeamIndex 32` and no contract row, and none meet
  `keep_retired`'s always-keep rule — the decision stage is not where the restore class comes from.
- At 2027 week 1, 337 free agents vanish and a new 389-prospect class appears (normal league-year churn).

**Implication for session 3:** restoring a rollover retiree must recreate the `PlayerContract` row as well
as set `ContractStatus`/`TeamIndex`, because Madden deletes the contract in the same advance. That is the
sign path P1-08 already supports (`pipeline/save_reader/sign.mjs` fills an empty `PlayerContract` row);
the proof must show the recreated row surviving the next advance.

Stop-save hashes (sha256) at save time:

```
c95abf713db4019ad3d2df92ee2f98027273a6dfce3e3c4364af6e544ed88777  CAREER-OFFSEASONSTART
dbcc9dc18fc0a727677326f56a7ba0392a6305b442a703eafd44042b8a633a2e  CAREER-RESIGNWK
56b07eef5d681ca02ddfecc7cdf075b5cb8605a269dec5a5af884316e0714f74  CAREER-FAW1
38c5c9485254b0f81a9955139391ee4669d709274b2fef114529c6a94b64afe0  CAREER-FAW2
a38aa77a79771b5bb12f610e85eedd0b82923cc9f1fd2e1c8224659682d8c932  CAREER-COMBINEWK
1c80cdf03520b919255df3dd2f9e8a5b6901af6047479da876392a1336bffe86  CAREER-PRODAYWK
9cb7c6a302389e66895b90c7974c0099e63b64491d474c6a00323b24c5dd88f0  CAREER-DRAFTWK
f47b30149e36c14fee6e85a8f7d4d5674b06cc0766dfc1b2a955297b1b5cab6c  CAREER-UDFAWK
3edff38dc43099c570e8cbd69fd3f8e5280bdc0889466a81657b99997a857c9a  CAREER-PRESZW1
d2ce24f4073235d8da6f38432fa864a278368486affc25ec03397e1ed79a6ad8  CAREER-PRESZW2
bb40c7b1101dd672a313a485217c9f2e638de4df27a847e6d6b4aa0208accff8  CAREER-PRESZW3
4a90782f1e21986cd00dc632a9e1d2e2faf92a689a7da1315e812b86ceaf4c6d  CAREER-CUTDOWN
eef4e803ed637c20d631894515bde416f6aa561ad681eb776d78a2bdd17828ba  CAREER-2027W1
```

## Session 3 — restore proof (spike 1, extended)

### Readiness check against the session-2 snapshots (2026-11-09)

Before the run, the census raises four things to settle:

1. **Fixtures among the 78** (the only players retired at the decision stage), from their contract at
   2026 week 0 (`dev_pre1`): 36 had contracts expiring after 2026 (`expired contract`), **1** had 2+ years
   left (Lane Johnson, 4 years, 2 left), 7 had one year left, and 34 were already free agents. **No retired
   player was on the practice squad**, so that fixture class has no native example — replace it with a
   free-agent-at-retirement player, or make the PS case a separate question. The 12 rollover retirees all
   had exactly 1 year left (2027).
2. **No end-of-season contract snapshot exists.** The last snapshot with contract rows for the 78 is the
   clone at 2026 week 0 (`CAREER-TUNE-RETIRE`), 18 weeks before they retired; 1,964 of 3,238 contract rows
   differ from the `RESIGN` snapshot (most by `current_year` only, some by real extensions). If the proof
   must read back the exact final terms, JR should save once at regular-season end (or Super Bowl week)
   before advancing to the offseason. The expired/2+-years classification itself is safe either way.
3. **No ungated restore path for the late natives.** `step_retirement_window` runs
   `plan_retirement_edits`, which blocks every edit without a JR-approved proof, and `load_proof` refuses
   the draft. The only ungated writer is the decision-stage legacy keep, and **`keep_retired` keeps none of
   the 78** (all are 33+ or below its OVR bar), so a fixture run also needs a way to force the three keeps.
   Options: (a) JR approves the proof file up front for the fixture run on the disposable clone;
   (b) add a fixture/`--keeps` seam to the league office; (c) call the planner directly with an in-memory
   fixture authority (the planner does not require JR's approval — `load_proof` does) and apply the edits
   with `save_writer.mjs`. **JR's call.**
4. **The planner does not recreate the contract Madden deleted.** It restores `ContractStatus` and
   `TeamIndex` only; the read-back will show a `Signed` player with no `PlayerContract` row. The proof must
   establish whether Madden keeps that state through the next advance; if it does not, the window needs a
   stored-contract sign after the restore (`step_restore_retirees` is the existing model).

### Fixtures written (2026-11-09)

JR chose the planner + save-writer route and week-0 contracts (2026-11-09). `tools/retirement_g4_fixture.py`
builds each branch's `edits.json` with an in-memory fixture authority (the planner checks the authority's
shape; only `load_proof` requires JR's approval) and `save_writer.mjs` applied it. Config:
`verify_output/retirement_g4/restore/fixtures.json`; edit sets and plans alongside it. Three new saves in
the saves folder, each a copy of an existing stop save, 9 players restored to `Signed` on their 2026 team:

| save | source | stop | players (pgid, baseline team) |
|---|---|---|---|
| `CAREER-TUNE-G4-START` | `CAREER-OFFSEASONSTART` | `OFFSEASON_START` | Cameron Jordan 10697 → 26 (NO, expired, 4 void years); Lane Johnson 927 → 12 (PHI, 2+ years); Chris Brooks 23350 → 19 (GB, one year left) |
| `CAREER-TUNE-G4-FAW1` | `CAREER-FAW1` | `FREE_AGENCY` wave 1 | Jonathan Allen 12521 → 1 (CIN); David Montgomery 20489 → 31 (HOU); E.J. Speed 20730 → 31 (HOU) |
| `CAREER-TUNE-G4-UDFA` | `CAREER-UDFAWK` | `UDFA` | DeMarcus Lawrence 11079 → 27 (SEA); Jourdan Lewis 12555 → 16 (JAX); Xavier Woods 12846 → 11 (MIA) |

Baseline teams come from `YEARBYYEARTEAMINDEX` in the 2026 season stats (the team each player finished the
season with), because the week-0 roster had already moved Campbell and others. All nine had the same team
at week 0 and in their stats, and all were `Signed` in the last image we hold. The practice-squad class had
no native example; Chris Brooks (one year left) stands in, and the proof should say so.

**The writer's read-back already shows the gap in (4):** all nine are `Signed` on the right team with an
**empty `PlayerContract` row**, and none appears in the team's depth chart (the roster and practice-squad
lists are not in the reader's snapshot). What Madden does on load is the open question.

JR's advance steps (each save loads without Madden having seen it before):

1. `CAREER-TUNE-G4-START`: advance to `RESIGN`, save; advance to `FREE_AGENCY` wave 1, save; quit. That is
   the next stop and the contract rollover for the three.
2. `CAREER-TUNE-G4-FAW1`: advance to `FREE_AGENCY` wave 2, save; advance to `COMBINE`, save; quit.
3. `CAREER-TUNE-G4-UDFA`: advance to the 2027 preseason week 1, save (the purge advance); advance to
   cut-down, save; advance to 2027 week 1, save; quit.

Mu then reads each save with `pc_save_reader.mjs` and runs `tools.retirement_stage_census` on the pre/post
pair (each restored player must show as `un-retired`, must not vanish, and must not appear again as
`Retired` — the re-retirement check). Record for each player: status, team, `PlayerContract` row, depth
chart and list membership, cap/dead money, and the transaction. The reader has no roster or practice-squad
list; if the advance needs those, extend the reader first and redo the proof, because a reader change
invalidates the proof's `schema_version`.

### Session 3 — results (2026-11-09)

JR advanced the three saves and saved 7 stops: `CAREER-TUNEG4STARTTORESIGN` (week 1),
`CAREER-TUNEG4STARTTOFAW1` (week 2), `CAREER-TUNEG4FAW1TOW2` (week 3), `CAREER-TUNEG4FAW1TOCOMBINE`
(week 5), `CAREER-TUNEG4UDFATO2027PW1` (2027 preseason week 0), `CAREER-TUNEG4UDFATOCUTDOWN` (preseason
week 3) and `CAREER-TUNEG4UDFATO2027W1` (2027 regular week 0). Read-backs:
`verify_output/retirement_g4/restore/after_*.json`.

| restored at | player | RESIGN | FA W1 | FA W2 | COMBINE | 2027 PS W1 | CUTDOWN | 2027 W1 |
|---|---|---|---|---|---|---|---|---|
| `OFFSEASON_START` | Cameron Jordan | Signed/26 | Signed/26 | — | — | — | — | — |
| `OFFSEASON_START` | Lane Johnson | Signed/12 | Signed/12 | — | — | — | — | — |
| `OFFSEASON_START` | Chris Brooks | Signed/19 | Signed/19 | — | — | — | — | — |
| `FREE_AGENCY` | Jonathan Allen | — | — | Signed/1 | Signed/1 | — | — | — |
| `FREE_AGENCY` | David Montgomery | — | — | Signed/31 | Signed/31 | — | — | — |
| `FREE_AGENCY` | E.J. Speed | — | — | Signed/31 | Signed/31 | — | — | — |
| `UDFA` | DeMarcus Lawrence | — | — | — | — | Signed/27 | **gone** | — |
| `UDFA` | Jourdan Lewis | — | — | — | — | Signed/16 | **gone** | — |
| `UDFA` | Xavier Woods | — | — | — | — | Signed/11 | Signed/11 | Signed/11 |

No player has a `PlayerContract` row or a depth-chart slot at any read-back; each keeps his old
`cap_salary`. The census classifies all nine as `un-retired` at the first stop after the write, with no
`Retired` appearance anywhere.

Findings:

1. **The write holds.** Madden loads and advances a `Signed` player with no contract row on the right
   team. Nothing is re-retired within the window: the `OFFSEASON_START` three are still `Signed` after the
   contract rollover in the next advance (that branch retired 15 *other* players), and the `FREE_AGENCY`
   three are still `Signed` at FA wave 2 and `COMBINE`. `MAX_WINDOW_RESTORES = 1` is not contradicted.
2. **Madden never repairs the gap.** No advance created a `PlayerContract` row, a roster entry or a
   depth-chart slot for any of the nine; `cap_salary` is unchanged.
3. **The late restores survive the purge.** The three restored at `UDFA` are present at the 2027 preseason
   week 1, where the base lineage purged 88 of 90 `Retired` rows (this branch purged 85). A restore does
   keep a native retiree's row alive through the purge.
4. **But cutdown deletes 2 of the 3.** DeMarcus Lawrence and Jourdan Lewis are absent at the cutdown
   save; the census reports 2 rostered players vanished, and their rows (1578, 1608) are reused by the
   2027 draft class at week 1. Xavier Woods survives into 2027 week 1 (`Signed`/11, no contract), and
   Miami then carries 66 signed players against the 65 limit. The proof's durability through cutdown
   **fails** for the contractless restore.
5. **A small edit moves the rollover retirement set.** The `OFFSEASON_START` branch retired 15 players at
   the rollover where the base retired 12, and 7 names differ. Madden's rollover retirements are not
   stable across a three-player roster-state change; each branch's retiree set is its own, so compare a
   branch only with its own baseline.
6. **Not read back:** the transaction record needs a league office run (the fixture had no database), and
   the reader exports no roster or practice-squad lists.

**Conclusion:** the field-only restore is accepted but incomplete and not durable through cutdown. Do not
approve the proof on `ContractStatus`/`TeamIndex` alone. The window needs a signing step after the field
edit — the stored contract through `plan.sign_player`/`sign.mjs`, which also writes the roster list — and
the proof must be rerun with a contract row present. The signed branch below validates that shape.

### Signed-restore branch (2026-11-09)

To test that conclusion before changing code, `udfa_sign` restores the **same three players at the same
stop** as the failed branch, but in two writes to one copy:

1. `udfa_sign.edits.json` — `Retired → FreeAgent/32` (the planner's legacy keep shape), then
2. `udfa_sign.sign.edits.json` — `save_writer`'s `sign` to the old team with the **remaining contract**
   from `snap_02_RESIGN`: `Length 1`, the 2027 salary/bonus (`current_year` row), and `VoidYears` kept
   (Lewis has 2). These mirror `step_restore_retirees`'s `rest` contract.

Output: `CAREER-TUNE-G4SIGN-UDFA`. The writer reports `signed: 3 (3 roster)`, contracts created in three
empty `PlayerContract` rows; the reader shows all three `Signed` on their old team (27, 16, 11) with a
1-year contract (`length 1`, `current_year 0`), `cap_salary` unchanged, and still no depth-chart slot.

JR advances it exactly like the field-only branch: 2027 preseason week 1, cut-down, 2027 week 1. Success is
all three still present at cut-down and week 1 with the contract row intact; the direct comparison is the
field-only branch, where two of the three were deleted at cut-down.

**Result (2026-11-09): the signed restore passes.**

| stop | Lawrence | Lewis | Woods |
|---|---|---|---|
| 2027 PS W1 | Signed/27, 1-yr contract, depth LE | Signed/16, 1-yr contract, depth CB | Signed/11, 1-yr contract, depth FS |
| CUTDOWN | unchanged | unchanged | unchanged |
| 2027 W1 | unchanged | unchanged | unchanged |

All three survive the preseason purge (85 purged, the same as the field-only branch), **cut-down deletes
none**, and they are still `Signed` with their contracts at 2027 week 1. The census reports no vanished
rostered player, no re-retirement, and no new retirees. Madden also put each on his team's depth chart at
the next advance — the field-only restores never appeared there. The A/B is decisive: the contract is what
makes the restore durable.


### Session 3 rerun — the production sequence (2026-11-09)

The proof was rerun on the built code with the reader v7 and the production sequence: write `FreeAgent`/32,
advance the clone once, sign the player back to his old team with his stored remaining contract, advance
again. Config `verify_output/retirement_g4/restore/fixtures_v2.json`.

| branch (restore stop) | players | after the free-agent advance | after the sign-back advance |
|---|---|---|---|
| `OFFSEASON_START` | Lane Johnson (2 yrs), Cam Heyward (1), Chris Brooks (1) | still free agents at `RESIGN` | `Signed` on PHI/PIT/GB with contracts through the FA-W1 rollover |
| `FREE_AGENCY` wave 1 | Jonathan Allen, David Montgomery, E.J. Speed | still free agents at FA wave 2 | `Signed` on CIN/HOU/HOU with contracts through `COMBINE` |
| `UDFA` | DeMarcus Lawrence, Jourdan Lewis, Xavier Woods | still free agents after the 2027 preseason purge | Lawrence/Lewis signed at preseason W1; Woods refused there (Miami 76/75) and signed at cut-down; all three `Signed`/contract/roster/depth at 2027 week 1 |

- The census reports no rostered player vanished in any lineage and no restore re-retired; the `UDFA`
  purge removed the remaining `Retired` rows as usual and left the three flips alone.
- **None of the flipped players is in `League.FreeAgents`** (a field flip does not touch the list), and every
  one of them survived the 2027 week-1 free-agent purge — the purge works off that list. The last-stop
  controls (Kelce and Rodgers flipped to free agents at `CUTDOWN`) survived the same purge.
- The `UDFA` sign needed two writes by roster count: Miami was at 76 against the writer's 75 cap at the
  preseason, then at the limit again at cut-down after its own trim. The production league office signs at
  the next run, which is the same retry.
- **Stages that passed: `OFFSEASON_START`, `FREE_AGENCY`, `UDFA`.** `CUTDOWN` is not claimed: the two
  survivors there have no restorable contract, so only the free-agent flip was tested, not the sign.
- The proof file (`verify_output/retirement_g4/proof_draft.json`) now carries `schema_version` 7, the three
  stages, `KEEP_NATIVE_RETIREE` proven with `ContractStatus`/`TeamIndex`, and the A3 save's sha256. It still
  lacks `approved_by`/`approved_on`; `load_proof` refuses it until JR fills those.

**Conclusion:** the window restore must be a signing, not a field edit. `step_restore_retirees`
(`plan.sign_player` → `sign.mjs`) is the proven shape. Two ways to wire it: sign the player back inside the
window's own write (extend `sign.mjs` to accept a `Retired` player, so `Retired → Signed` with a contract
and roster entry is one move), or keep the legacy two-step (the window sets him `FreeAgent`, the next run
signs him, as `step_restore_retirees` already does for decision-stage keeps). The proof must be rerun on
whichever path the window actually takes.




Restore at `OFFSEASON_START` three players (expired contract, 2+ years left, practice squad), at the
stop after the contract rollover (`FREE_AGENCY` wave 1) three more, and at a late stop (`DRAFT` or `UDFA`)
three more. The existing ungated writer is the league office's (`keep_retired` /
`step_restore_retirees`, `pipeline/league_office.py`); the new planner in `pipeline/retirement_edits.py`
stays blocked until the proof is approved.

At the next stop and after the rollover into the preseason, read back and record for each restored player:

- `ContractStatus`, `TeamIndex`, the `PlayerContract` row;
- roster, depth chart and practice-squad membership;
- cap / dead money;
- the transactions record.

Then the **re-retirement check** in the same run: does Madden retire a restored player again at a later
stop? That sets whether `MAX_WINDOW_RESTORES = 1` is enough.

## Session 4 — the proof file

Fill `verify_output/retirement_g4/proof_draft.json` in the
[`RETIREMENT_AUTHORITY.md`](RETIREMENT_AUTHORITY.md) §5.2 format:

- `fixture_id`: `g4-<date>`;
- `proof_save_hash`: sha256 of the proof save;
- `schema_version`: the reader's `meta.reader_version` at proof time;
- `stages`: only the stops that passed;
- `actions.KEEP_NATIVE_RETIREE`: `proven: true`, a `proof_id`, the fields proven (at least
  `ContractStatus`, `TeamIndex`);
- `approved_by`: `JR`; `approved_on`: the date JR approves.

Point the watcher at it with `MU_RETIREMENT_AUTHORITY=/path/proof.json`. **Do not install the Contract
Retirement Arbitrator** for Mu lineages. P1-07 is ticked only when JR approves the proof.

## What the session-3 run leaves on the code side

`pipeline/retirement_edits.py` (default deny), `pipeline/retirement_window.py` (`load_proof`,
`authority_for_run`), `tools/retirement_stage_census.py`, `tools/retirement_g4_fixture.py` and their tests
are built and green. The missing input is no longer only the proof:

- **A contract step for a window restore.** The planner edits `ContractStatus`/`TeamIndex` only. Session 3
  showed Madden accepts that, never creates a contract or roster entry, and deletes two of three such
  players at the cutdown advance. The signed branch survives all three advances with the contract row,
  roster entry and a Madden-populated depth slot, so a restore must sign the player back with his stored
  contract (the `step_restore_retirees` model, `sign.mjs`), and the proof must be rerun on that path.
- **Roster and practice-squad lists in the reader.** The proof cannot record roster/PS membership today;
  adding them is a reader change, which resets the proof's `schema_version`, so do it before the rerun.
- The transaction record still needs a league office run on the fixture lineage (session 3 used no database).
