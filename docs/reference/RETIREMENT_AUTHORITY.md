# Retirement authority and interface inventory

Source-only inventory for [PERSONALITY_RETIREMENT_PLAN.md](../done/PERSONALITY_RETIREMENT_PLAN.md) U0 (2026-11-06). It separates what the saves have **shown** (observed timing) from what Project Mu has **proven** it may write (authority). Nothing here launched an executable, opened a save, read a database or ran a migration. Line numbers were rechecked on main at `7bbfb62`.

**Bottom line:** Mu has observed native retirement timing and already has one restore path in the league office (`keep_retired`), but no disposable-save proof that a restore keeps contracts, references and survives the next advance, and no write path at all for retiring an active player. Both actions stay **unsupported** in `pipeline/retirement_edits.py` (default deny) until gate G4. The offline policy, state machine, store and preview do not need that proof and are built without it.

## 1. Evidence matrix

| # | Fact | Source | Kind | What it establishes | What it does not |
|---|---|---|---|---|---|
| A1 | Madden sets `ContractStatus = Retired` on kept Player rows at the offseason start (47 in `CAREER-NOPROGRESSION`; 59–60 in the stage saves) and later `Deleted` (59 by the 2027 preseason). | `docs/done/V2_CORE_BUILD_PLAN.md:65` (S7), `:20` (R4 notes); `docs/reference/MADDEN_OFFSEASON_WALKTHROUGH.md:35` | Observed | Retirement is first a status change on a kept row, so a restore is a field edit made before the purge. | That a status reset keeps the player through the next advance, or keeps his contract, roster/depth/PS references and history. |
| A2 | The walkthrough reports the Player count falling 3,244 → 3,188 by the preseason; S7 reports the table "kept the same 3,246 rows". | `MADDEN_OFFSEASON_WALKTHROUGH.md:35`; `V2_CORE_BUILD_PLAN.md:65` | Observed, **inconsistent** | Purge timing is "before the 2027 preseason". | Whether `Deleted` rows are reused or removed (the two notes count different things: named players vs table rows). Treat a `Deleted` row as unrecoverable. |
| A3 | The reader skips empty, unnamed and `Deleted` Player rows; it exports `ContractStatus` for the rest and `CurrentStage` / `CurrentOffseasonStage`. | `pipeline/save_reader/pc_save_reader.mjs:300`, `:308`, `:90–91` | Code | Snapshots carry `status: 'Retired'` and the stage, so a pre-purge snapshot is the baseline. | Recovery of anyone already `Deleted`. |
| A4 | The league office already restores native retirees: `keep_retired(player, season, seed)` (age ≤ 32 with OVR ≥ 75 always; 33–34 with OVR ≥ 85 on a seeded 50%) and `step_retirements` record every `Retired` player in `retirement_decisions` and, when kept, set `ContractStatus = FreeAgent, TeamIndex = 32`; `step_restore_retirees` later signs a kept retiree back to his old team when his stored contract runs into the league year. | `pipeline/league_office.py:193–195`, `:343–351`, `:1398–1413`, `:1416–1430`, `:1709`, `:1712–1714`, `:1891–1895` | Code | A restore writer exists and goes through the verified save write (`save_writer.mjs` read-back). | That this writer has been run on a disposable save and checked at the next stage and rollover; it also defaults every kept retiree to FreeAgent first, which plan §2.6 says not to rely on for unexpired contracts. |
| A5 | `retirement_decisions(season, player_id, madden_retired, mu_keep, reason)` is the league office's projection. | `db/init_db.sql:1540–1547`; `db/migrations/2027_01_14_v2_franchise_os.sql:100–107`; `PROJECT_SPEC.md` §3.3q | Schema | Observed native retirement plus the keep override. | Policy intent, replayable draws or execution receipts (the new sidecars hold those). |
| A6 | The writer refuses a pgid in more than one of release/sign/trade and a `players` edit that sets a field a move sets (`TeamIndex`, `ContractStatus`, `Contract`, ...); every edit is planned, schema-checked, written to a copy and read back. | `pipeline/save_reader/save_writer.mjs:25–35`, `:50–75`, `:184–187` | Code | The conflict boundary a retirement edit must respect: a status edit for a moved player is refused by the writer itself. | Any retirement-specific semantics; "release" is free agency, not retirement. |
| A7 | No writer sets `ContractStatus = Retired` for an active player. | grep of `pipeline/`, `engines/` (no match) | Absence | Retire-active has **no** write path. | — |
| A8 | Captured ledger: `classify` returns RETIRED only with a `player_retirements` row, otherwise FREE_AGENT with `retirement_scan_missing`; `build` treats a nonempty retirement set as "the scan ran". | `engines/season_ledger.py:67–91`, `:152–154` | Code | Disappearance is not retirement. | Completeness of an empty scan (`bool(retired)` cannot tell "none retired" from "not scanned"). |
| A9 | Transaction capture has no retirement branch. | `pipeline/load_transactions.py`, `pipeline/transactions_parse.py` (no `retire` match) | Absence | Captured retirements come from the save status, not League Transactions parsing. | — |
| A10 | Contract acceptance and execution live in `engines/free_agency_engine.py` (`acceptance` `:283`, `run_wave` `:416`) and `engines/transactions_engine.py` (`plan_resign` `:313`, `_extend_or_option` `:488`, `plan_offers` `:553`, `signings_from_decisions` `:636`, `resign_decisions` `:983`, `cpu_offers` `:989`). | those files | Code | Every committing re-sign / extension / FA wave / street / user move is a path that must reserve through the career state machine before activation. | Any of them calling it today (none does). |
| A11 | The Contract Retirement Arbitrator ships two Windows executables, `snapshot_franchise.exe` (sha256 `2d4d2b4b…decad1`, 106,781,695 bytes) and `restore_retired_players.exe` (sha256 `3461835f…2f7ace`, 106,789,840 bytes), identical in `PC_MODS/Contract Retirement Arbitrator/` (git-ignored) and `C:\Madden27Modding\Tools\Contract Retirement Arbitrator\`. The mod survey describes it as: snapshot at Super Bowl week; anyone who retires with 2+ contract years left goes back to his team. | file listing and hashes only; `docs/MMC_MOD_SURVEY.md:38`; `docs/MOD_INSTALL.md:24`; `docs/done/LEAGUE_OFFICE_PLAN.md:141` | Inventory | A second restore owner exists on disk. | Its actual fields, stage handling, or compatibility. Not launched or decompiled here. |
| A12 | Stored age did not increase by the 2027 preseason. | `MADDEN_OFFSEASON_WALKTHROUGH.md:34` | Observed | Age needs an explicit source/version (plan R7). | When or whether Madden ages players. |

## 2. Actions and their status

| Action | Status | Why | Needed to support (gate G4) |
|---|---|---|---|
| `OBSERVE_NATIVE` (record a native retirement in Mu's audit) | Supported offline | Reader exports `Retired` before the purge (A3). | — |
| `KEEP_NATIVE_RETIREE` (restore a native retiree) | **Unsupported** in the new planner; an older ungated writer exists (A4) | No disposable-save proof of contract, references, cap/dead money, history and next-advance survival. | JR-authorized disposable saves: pre-native, first-retirement, restored, next-stage and rollover snapshots; separate expired/active/FA/PS/injured fixtures; an authority fixture naming schema version, stage and fields. |
| `RETIRE_ACTIVE` (retire a player Madden kept) | **Unsupported** | No writer (A7); restoration proof does not cover it (plan §2.6, Q1). | Its own contract/cap/history test after restoration is proven. |
| `RELEASE` as a stand-in for retirement | Never | Release is free agency (A6). | — |
| Recover a `Deleted` player | Never promised | A2/A3. | — |

## 3. Interface decisions

| Interface | Decision | Status |
|---|---|---|
| Personality input | `personality.core.feature_view(profile, 'retirement')`; missing → neutral; validation/storage errors propagate and block effects-enabled finalization. Never `newsroom.personas.get_persona`. | Reconciled (core built; views-v2 reads full profiles) |
| Earnings input | `engines.career_earnings.earned_shares` + three-state `is_secure` (True / False only with a complete history / None). Context carries `{known_cap_share, coverage, secure, source_version, source_ids}`; None disables the ring term. | Reconciled (cap-relative plan built) |
| Attachment input | `personality.relationships.relationship_context(state, target=TeamRef)`; status `neutral` (off/observe) means no attachment term. | Reconciled (relationships plan built, observe-only) |
| Origin | Every player is generated (lifecycle plan); origin is never read from a name or SAVE provenance. | Reconciled |
| League office hook | Activation plan adds `PersonalityDeps.retirement_decision(cur, player, season, seed) -> (bool, str)` that `step_retirements` asks instead of `keep_retired`. This plan's decision feeds it once active. | **Unresolved until the activation plan builds** (G0 with U13 owner) |
| Contract reservation | Re-sign, extension, FA wave, street and user-move paths call `reserve_contract` before committing (A10). | **Unresolved**; no caller wired by this plan |
| Arbitrator ownership | Mu and the Arbitrator must not both restore. Recommendation: leave the Arbitrator uninstalled for Mu-managed lineages. | **Needs JR** (keep or retire the tool) |
| `retirement_decisions` | Stays the league office's compatibility projection; never policy authority. | Reconciled |
| Scan completeness | Ledger should take explicit completeness evidence instead of `bool(retired)` (A8). | **Unresolved**; continuity owner, not this plan |

## 4. Spikes that need JR's PC (not done, not faked)

1. **Restore proof (G4, Q1 first half).** On a disposable `CAREER-TUNE-*` clone with the watcher paused: save at the offseason start (Retired present), apply a restore of three players (expired contract, 2+ years left, practice squad), then save at the next stage and after the rollover. Read back status, TeamIndex, contract row, roster/depth/PS lists, cap and transactions.
2. **Purge stage and row reuse (A2).** Count named vs total Player rows per offseason stage to settle which note is right.
3. **Retire-active proof (Q1 second half).** Only after (1): set an active contracted player to Retired at the offseason start and check cap/dead money and the next advance.
4. **Arbitrator workflow (R2).** JR decides whether the tool stays; if it does, document its stage, inputs and edited fields from its own docs before any isolated test.
