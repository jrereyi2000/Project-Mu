# Madden 27 Offseason Walkthrough (PC)

JR stepped one franchise through a whole offseason week by week on 2026-10-31 and saved at every stage. This is what the pilot (gameplay agent, `tools/madden_pilot`) has to handle to advance an offseason unattended, and what each stage's save holds for Project Mu, which owns re-signings, free agency, UDFA signings and progression in V2 (`docs/in_progress/V2_MASTER_PLAN.md`, `V2_CORE_BUILD_PLAN.md` U13).

## JR's notes (2026-10-31)

- **Free agency week** has a live event that has to be skipped before that week can be advanced.
- **Draft week** replaces the Game Day and Weekly hub tabs with a draft screen. When advancing week by week, the only way forward is to sim the draft itself.
- **After the draft** there is a second live event (sign undrafted free agents), also skipped.
- **Breaking News** reports some re-signings that are really 5th-year options; they don't show in salaries or transactions (a Madden bug). Don't treat Breaking News as a source of contract facts.

## The saves

All in `C:\Users\jrere\Documents\Madden NFL 27\saves\`, one franchise (`Franchise.LeagueID` -1851690351, the `CAREER-WEEK1` lineage) with **all 32 teams user-controlled and set to manual** (the ALLUSER setup, confirmed by JR), read with `pipeline/save_reader/pc_save_reader.mjs`. `week` is `SeasonInfo` week index, `stage` the `CurrentOffseasonStage` value.

| Save | Calendar year | Week type | Week | Offseason stage | Signed | Free agents | Expiring | Draft prospects |
| :--- | ---: | :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| `CAREER-STAFF` | 2026 | OffSeason | 0 | 1 | 1,501 | 673 | 620 | 389 |
| `CAREER-RESIGN` | 2026 | OffSeason | 1 | 1 | 1,501 | 673 | 620 | 389 |
| `CAREER-FAPREP` | 2026 | OffSeason | 2 | 1 | 1,501 | 1,293 | 0 | 389 |
| `CAREER-FREEAGENCYPRELIVE` | 2026 | OffSeason | 3 | 2 | 1,501 | 1,293 | 0 | 389 |
| `CAREER-FREEAGENCY` | 2026 | OffSeason | 3 | 2 | 1,501 | 1,293 | 0 | 389 |
| `CAREER-COMBINE` | 2026 | OffSeason | 5 | 4 | 1,501 | 1,293 | 0 | 389 |
| `CAREER-PRODAY` | 2026 | OffSeason | 7 | 6 | 1,501 | 1,293 | 0 | 389 |
| `CAREER-DRAFT` | 2026 | OffSeason | 11 | 9 | 1,501 | 1,293 | 0 | 389 |
| `CAREER-RECAPPRELIVE` | 2026 | OffSeason | 15 | 10 | 1,725 | 1,457 | 0 | 0 |
| `CAREER-RECAPPOSTLIVE` | 2026 | OffSeason | 15 | 10 | 1,725 | 1,457 | 0 | 0 |
| `CAREER-2027PRESEASONWEEK1` | 2027 | PreSeason | 0 | 10 | 1,725 | 1,457 | 0 | 0 |

## What the saves show

- **The offseason is 8 stages, one Advance each.** Madden's UI shows only the stages JR saved (staff, re-sign, FA prep, free agency, combine, pro day, draft, recap/UDFA), and every one was captured; then the next advance starts the preseason. Under the hood the save's `week_index` jumps 0, 1, 2, 3, 5, 7, 11, 15 (and `CurrentOffseasonStage` 1, 1, 1, 2, 4, 6, 9, 10), so the skipped numbers are internal, not hidden weeks. U13's `detect_stage` should key on these values. `engines/contracts_engine.OFFSEASON_STAGE_WEEKS` (7 stages at league_state weeks 24–32, an estimate) needs re-mapping to the 8 real stages.
- **With every team user-controlled and manual, Madden makes no contract moves.** None of the 620 expiring players was re-signed: all 620 became free agents at FA prep. No free agent and no undrafted prospect was signed. The only additions were the 224 draft picks (7 rounds × 32), signed at the draft; the other 165 prospects became free agents. Teams start the 2027 preseason at about 54 players each. This is the behaviour V2 wants (Mu makes these moves), and it means an unattended run must have Mu do every re-signing, free-agent and UDFA signing each offseason, or rosters stay short.
- **No progression:** manual teams only progress when a user spends upgrades, so no OVR changed from draft to preseason (one changed between staff and draft), which suits Mu owning progression (V2.14). No player's stored age went up by the 2027 preseason either; whether Madden ages players at a later point (week 1, or a birthdate it computes) still needs checking.
- **Retired players are purged at the new season:** 59–60 retired in the offseason saves, 4 in the preseason save, 3,244 → 3,188 players.

## For the pilot

One unattended offseason, by stage: advance staff, re-sign and FA prep; at free agency skip the live event, then advance; advance through combine and pro day to the draft; sim the draft; at the recap skip the UDFA live event; advance into the preseason. Mu reads and writes the save at the stages where it acts (re-signings before FA prep, free agency, UDFA after the draft, cutdown before the preseason). Exact screen names belong in the pilot's screen catalog.
