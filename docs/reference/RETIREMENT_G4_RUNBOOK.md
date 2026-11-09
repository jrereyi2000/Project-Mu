# Retirement restore authority (G4) — PC runbook

Working document for **CLOSE_OUT_CHECKLIST P1-07**, following
[`RETIREMENT_AUTHORITY.md`](RETIREMENT_AUTHORITY.md) §5.1 exactly. Status 2026-11-09: **not started
on a CAREER-TUNE clone** — the earlier census (2026-11-07) used JR's all-user offseason saves; CPU teams
are untested and there is no restore proof. `CAREER-MUBASE` is never simulated or edited.

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

## Session 3 — restore proof (spike 1, extended)

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

## Nothing remains on the code side

`pipeline/retirement_edits.py` (default deny), `pipeline/retirement_window.py` (`load_proof`,
`authority_for_run`), `tools/retirement_stage_census.py` and their tests are built and green; the proof is
the only missing input.
