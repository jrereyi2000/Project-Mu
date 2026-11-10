# Acceptance checks for staff contracts and coach save-writes

On 2026-11-10 the fixes to staff contracts and coach save-writes took five or more rounds between a builder and the auditor. Each time, the builder's tests passed against fakes and synthetic fixtures, and the auditor's throwaway scripts then found the real behaviour on a real Postgres database and a copy of a real save. This page is the auditor's checks, packaged so that a builder runs them before posting and gets the fix right in one pass.

The tool is `tools/offseason_check/` (`python3 -m tools.offseason_check --help`).

## The rules a change must meet

1. **Staff-contract invariant.** Every season of every staff deal is paid exactly once:
   - his salary if he held that job that season;
   - a buyout instalment if he was fired before it;
   - nothing if he had moved to another staff post (poached, or a lateral move).

   It must never be both, never neither, and never a season outside the deal. Every season a person holds a job is paid by exactly one row.

   One exception is by design. A GM or full-control hot-seat firing books the firing season itself as the first buyout instalment (`buyout_fired_season` = that season). That season counts as paid.
2. **Proven on a copy of a real save.** A save-writer change is proven on a copy of a real save, not just on synthetic fixtures. Use CAREER-MUBASE, the pinned league-start save in `data/baselines/CAREER-MUBASE.sha256`. The original is never written, and its sha256 must be unchanged at the end.
3. **User wiring.** After every write, all 32 teams have a live user. On each team, `Team.HeadCoach`, `Team.UserCharacter`, `FranchiseUser.UserEntity` and `FranchiseUser.Team` agree, the head coach has `Coach.IsUserControlled`, and no staff seat is empty. The check is `league_setup.mjs`'s `verifySetup`.
4. **Re-plan empty.** After a write, `coach_save_sync.plan_staff_edits` on the written copy returns no actions. If it returns any, the write didn't make the save match Mu.
5. **One live contract row.** Each `(team, kind, person)` has at most one row with `remaining_years > 0`, and no row is filed twice.
6. **Hire contracts are non-zero at the first offseason write.** Every seated coach's `ContractLength`, `ContractSalary` and `ContractYearsRemaining` are non-zero and equal his live `staff_contracts` row, from the OFFSEASON_START write on. That covers the carousel's hires, the coaches Mu creates over Madden's made-up ones, and the linked real coaches.

## Which check to run

| You changed | Run |
|---|---|
| `engines/staff_contracts_run.py`, `staff_contracts_engine.py`, the hot seats' settle paths (`coach_hot_seat`, `gm_rollover`), report-card payroll | `timelines`, then `e2e` |
| `engines/coaching_carousel.py`, `fire_coordinator.py`, `staff_names.py` | `e2e`, then `timelines` if hires or fires file contracts |
| `engines/coach_save_sync.py`, `pipeline/coach_link.py`, `pipeline/load_save.py`'s coach part | `e2e` |
| `pipeline/save_reader/staff.mjs`, `save_writer.mjs`, `pc_save_reader.mjs`, `league_setup.mjs`, `contract_edits.mjs` | `e2e` (plus the node tests) |
| `pipeline/league_office.py` | `e2e` |
| You need to check a scratch DB you already built | `invariant --dsn <its DSN>` |

```bash
python3 -m tools.offseason_check timelines                       # ~1 min, empty scratch DB made and dropped
python3 -m tools.offseason_check e2e --save "/mnt/c/Users/jrere/Documents/Madden NFL 27/saves/CAREER-MUBASE" \
    --json /tmp/offseason_check.json                             # several minutes
python3 -m tools.offseason_check invariant --dsn "host=localhost dbname=mu_scratch_<you>_test user=postgres password=postgres"
```

`e2e` runs:
- a fresh `mu_scratch_offseason_<random>_test` database (`init_db.sql`, then `seed_clean_db --season 2026`);
- `load_save` of the copy, then `league_office setup`;
- a scripted Black Monday and the real `hiring_cycle`;
- `league_office` at OFFSEASON_START, RESIGN, FREE_AGENCY wave 1 and COMBINE, with the real save writer writing the copy.

After every write it checks the rules above. After the load it reports the starting-seat link counts (linked / created / released / skipped) and checks F1. The output is a PASS/FAIL table, plus a JSON report with `--json`.
- `--known-open start_years_from_save` shows a known open item as KNOWN-OPEN instead of failing the run.
- `--expect FILE` adds your own expected league-start rows, e.g. `[{"team": "HOU", "kind": "HC", "remaining_years": 2}]`.
- `--keep` leaves the DB and the copies for digging.

**Safety, enforced in code:**
- Only `mu_scratch_*` databases are opened. The DSN is checked before connecting and `current_database()` after. `madden`, `madden_test`, `madden_mu2026`, `madden_2036` and every other `madden_*` are refused by name.
- The pristine copy under `data/baselines` is refused as a source.
- Nothing is ever written in the Madden saves folder.
- Madden is never launched.
- Child processes get `MU_NO_LLM=1` and no API keys or ntfy topic.

In a worktree, `pipeline/save_reader/node_modules` is missing. Symlink the main checkout's copy (`ln -s /home/jrereyi/Project-Mu/pipeline/save_reader/node_modules pipeline/save_reader/node_modules`; it is git-ignored).

## The one-pass process

1. **The handoff states everything up front.** It gives the acceptance rules above and the full case list (the timelines by name, the e2e checks, the expected values), not one finding at a time.
2. **The builder takes the whole open list and fixes all of it.** Per round, run only the targeted tests: the touched files' tests and `tools.offseason_check` itself. Then post once, with the result table in the NOTICE.
3. **The full offline suite runs once, at the end**, not every round.
4. **The auditor reruns the same tool on the same save** and reviews the diff. A disagreement is then about the code, not about how it was tested.

## Known traps (from AGENT_CONVERSATION.md, 2026-11-09/10)

- **Fakes hiding real behaviour.**
  - M-0175: four save-writer defects passed U3's 43 synthetic tests and were found only on copies of real saves.
  - M-0196: the B4 seats were skipped, not created and released, on the real MUBASE.
  - M-0107: a fake cursor ignored an `IS NOT NULL` clause.
  - M-0125, M-0150: a signature test bound the fake's own signature.

  Test on Postgres and a real save copy.
- **Whole-file rewrites deleting tests** (M-0178). A test file rewritten by slicing from a class anchor lost `DecayTests`, `BlockTests`, `PersonKeyTests` and the `__main__` footer. Diff any file you rewrite before committing, and rerun the whole file.
- **Stale main-checkout copies reverting spec lines** (M-0193, M-0194, M-0195). `main` moved from another worktree without the main checkout's files following, so a later edit wrote the old lines back. Before editing a shared file, run `git diff HEAD -- <file>`. Deletions of other sessions' committed lines mean the copy is stale.
- **Whole-index commits sweeping others' staged work** (M-0134, M-0136, M-0137). `git add <doc> && git commit` in the shared checkout took another session's 16 staged files. Commit by path: `git commit --only -m "..." -- <paths>`.
- **Committing before a function exists** (M-0203). 854fd4d called `file_hires` one commit before 44db561 added it, so that commit raises `AttributeError` and breaks bisecting. Each commit must pass on its own; commit the callee first or both together.

## Status at 2026-11-10

`timelines`: all 39 pass at main `25226c8`. Every deal they produce matches the auditor's verify_last reference run.

`e2e` on CAREER-MUBASE before F1 (`1d9dd0b`) reproduced verify_last exactly:
- the link: 71 linked, 25 created, 128 filed;
- the carousel: OUTSIDE, RETREAD and three POACHED head coaches, 20 hires for 2027, each with exactly one live row;
- OFFSEASON_START: 31 release, 65 assign and 31 create, 96 of them with a contract;
- FA's annual: files 0, payroll $554.7M;
- every seat matches its row, and every re-plan is empty.

Its only FAIL was `start_years_from_save`, with 84 of 128 rows on fresh full terms.

`e2e` at `25226c8` (F1 landed, M-0227/M-0228): 40 PASS, 4 INFO, 1 FAIL.
- `start_years_from_save` still fails, now for 32 rows, all on the fallback branch:
  - **7 GMs** hired before 2026 get a fresh 4/4. `file_hires` falls back to "the role's term from this season", not to the tenure-start rule M-0225 asks for.
  - **The 25 coaches Mu creates** over Madden's made-up ones inherit the made-up coach's save deal (e.g. Evero 7/5, Raheem Morris 6/6, Payton 3/2), because `coach_link` builds `years_from` from whoever holds the seat in the save. They have no Madden record of their own, so by M-0225 they should take the tenure rule.
- Everything else passes. The projected annual(2027) shows 7 re-signs and 8 buyouts from 2027, including DeMeco Ryans $22M over 2 years.

Use `--known-open start_years_from_save` only while that item is open, and name its message number.
