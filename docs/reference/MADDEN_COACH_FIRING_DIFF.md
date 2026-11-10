# Madden Coach Firing Diff (Spike B)

What Madden 27 itself does to its save when it fires coaches, measured by diffing two real saves. This is the evidence `COACH_SAVE_WRITES_PLAN.md` §2.5 (`"staff"` block) and risk R2/R3 build against.

Measured 2026-11-10 by JR (in game) and the `coach-save-writes` session (the diff).

## The two saves

| | Save | Season frame | SHA-256 |
|---|---|---|---|
| Before | `CAREER-TUNECOACHBEFORE` | 2026, week index 17 (Week 18), `RegularSeason` | `3f1f1827e5334558979c3503b03fd09ea9edde666d723f827bfcc03bb5b99e38` |
| After | `CAREER-TUNECOACHAFTERHC` | 2026, week index 18, `WildcardPlayoff` | `342f89d8ca0a95ccf299d7e94e52784db399f8c95215c690a73dfd402bb91987` |

JR set Madden's coach firings **on** on a `CAREER-TUNE-*` clone of `CAREER-TUNE-P2A` and advanced from Week 18 into the Wild Card week. Madden fired **three head coaches** — Aaron Glenn (NYJ), Dan Quinn (WAS), Robert Saleh (TEN) — and their coordinators, plus a further set of coordinators whose deals expired.

Both saves have **32 `FranchiseUser` rows**, so every team is a user team, which is the wiring the league office creates. This is therefore the real R2 case: a firing on a user-controlled team.

Read with `pipeline/save_reader/node_modules/madden-franchise` (`Franchise.create(copy, {autoParse: true, saveOnChange: false})` on a temp copy; the originals were never opened by the library).

## 1. What a firing writes

**On the Coach record** (example: Aaron Glenn, table 4168 row 37):

| Field | Before | After |
|---|---|---|
| `TeamIndex` | 17 | **32** (the free-agent index) |
| `PrevTeamIndex` | 0 | **17** |
| `ContractStatus` | `Signed` | **`FreeAgent`** |
| `ContractYearsRemaining` | 4 | **0** |
| `Position`, `OriginalPosition` | `HeadCoach` | **unchanged** |
| `ContractLength`, `ContractSalary` | 4, … | **unchanged** |
| `PresentationId`, `FirstName`, `LastName`, `Archetype` | … | **unchanged** |
| `IsUserControlled` | `True` | **`True` (Madden does not clear it)** |

Dan Quinn and Robert Saleh show the same pattern. The **Coach record is not deleted and not duplicated**: 127 live Coach records before and after, and no row is added or removed.

**On the team** (NYJ, WAS, TEN): `HeadCoach`, `OffensiveCoordinator` and `DefensiveCoordinator` all go to **null refs**. `SpecialTeamsCoach` is untouched.

**On the user wiring** — this is the part R2 was about:

| Field | Before | After |
|---|---|---|
| `Team.UserCharacter` | the fired coach | **null** |
| `FranchiseUser.Team` | this team | **null** |
| `FranchiseUser.UserEntity` | the fired coach | **the fired coach, unchanged** |
| `FranchiseUser` count | 32 | **32** (the row is orphaned, not deleted) |

So Madden leaves a null `UserCharacter`, nulls the user's `Team`, and leaves the orphaned user row pointing at a man who is now a free agent. Nothing in the save is repaired.

## 2. Answers to the plan's open risks

**R3 — is `CoachesRoot.CoachesArrays` the free-agent pool? No: there is no `CoachesRoot` table in this save at all.** Enumerating all 1,596 distinct table names finds `Coach`, `CoachAudibles`, `CoachManager`, `CoachAward`, `CoachTalentEffects` and friends, but no `CoachesRoot`. Nothing that could be a free-agent array changes membership across the firing either.

**The free-agent pool is simply `Coach.TeamIndex = 32`.** A released coach is a Coach record whose `TeamIndex` is 32. `release` therefore needs **no array membership at all** — it is field writes only. This removes the array handling the plan's §2.5 described.

**R2 — what a head-coach change must move.** Madden's own firing clears `Team.UserCharacter` and `FranchiseUser.Team` and leaves everything else stale. Mu wants the opposite: every team must keep a live user, because that is what stops Madden firing coaches at all. So a Mu head-coach change is a *release followed by an assign in the same write*, and `assign` must set, for a head-coach seat: the team's `HeadCoach` ref, `Team.UserCharacter`, `FranchiseUser.UserEntity` and `FranchiseUser.Team`, and `Coach.IsUserControlled = true`. `league_setup.mjs state` is the right post-check.

## 3. Facts that change the writer's design

- **Firing a head coach also fires his coordinators.** Madden cleared all three seats on NYJ, WAS and TEN. If Mu fires an HC but wants to keep his coordinators, the writer must re-assign them in the same write. `plan_staff_edits` already self-heals here (a cleared seat that Mu has a coach for becomes an `assign`), so this is behaviour to rely on, not extra code — but it must be tested.
- **`Position` is not changed by a release.** A fired head coach still reads `Position = HeadCoach`. An `assign` that changes role (a retread HC hired as an OC) must write `Position` and `OriginalPosition` itself.
- **A second contract status exists: `Expiring`.** Several coordinators went `Signed → Expiring` without leaving their team (CLE's OC/DC, KC's DC, MIA's OC/DC, NO's OC/DC, TB's OC/DC). That is Madden's own "deal is running out" state, distinct from `FreeAgent`, and it keeps `TeamIndex` unchanged. Mu's release must write `FreeAgent`, not `Expiring`.
- **Empty Coach rows exist for `create` to fill.** Table 4168 holds **470 rows, 127 live**, so 343 empty rows are available. A `create` can `autoUnempty` one, and a release in the same write frees one more. There is also a second, one-row Coach table (id 5866) which was not identified; it is not the pool.
- **`COACH_RATING` is not a firing field.** It changed on many coaches across the transition (and the three fired HCs all landed on exactly 50), so it is a season-scoped recomputation, not a release write. The writer should not carry it.
- **Madden does not hire at the firing.** No Coach record was created at the transition; the seats stay empty. That matches the plan's timing: Mu's hiring cycle and the `OFFSEASON_START` write both land before the game fills anything.

## 4. Still to measure

JR is firing a coordinator at the start of the offseason and saving `CAREER-TUNECOACHBEFOREOC` / `CAREER-TUNECOACHAFTEROC`. When those land, this doc gains a coordinator section: whether a coordinator firing touches the user wiring (it should not) and whether the HC's seat is affected.
