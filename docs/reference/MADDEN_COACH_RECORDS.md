# Madden 27 Coach Records

**Status:** working reference produced by Spike A of `docs/in_progress/COACH_SAVE_WRITES_PLAN.md`. This is measured data, not design; constants here are meant to be copied into U1/U2/U3.

## 0. How this was measured

| Item | Value |
|---|---|
| Save | `CAREER-MUBASE` (copy at `/tmp/coach_spike/CAREER-MUBASE`; original never opened for writing) |
| Save size / md5 | 5,662,392 bytes / `f016d8bd984d7f05e2804963487e4bfa` |
| Save mtime | 2026-11-09 09:28 (from `/mnt/c/Users/jrere/Documents/Madden NFL 27/saves/`) |
| Schema | `pipeline/save_reader/node_modules/madden-franchise/data/schemas/27/M27_620_0.gz` (meta `major 620, minor 0, gameYear 27`) |
| Reader | `madden-franchise` 4.4.1, Node 22.22.1 |
| Reference tool | `/mnt/c/Madden27Modding/Tools/CreateCoachM27.exe` (read only; never executed) |

The save was copied to `/tmp/coach_spike/` and every script opened only that copy with `saveOnChange:false` (except the two throwaway write tests, which wrote `/tmp/coach_spike/auto_unempty_test` and `/tmp/coach_spike/enum_test`). Scratch scripts live under `/tmp/coach_spike/` and are not in the repo.

**Cross-check with Spike B.** This doc was measured on `CAREER-MUBASE`. Where it touches what a release does, it defers to Spike B's direct in-game before/after diff: `docs/reference/MADDEN_COACH_FIRING_DIFF.md` (2026-11-10). That diff settled R3: there is **no free-agent array**, and a release is field writes only. Spike A's static observation of a `DataSource` coach array is kept below but is explicitly **not** a write target.

Commands used (all from inside `pipeline/save_reader/` or with the absolute `madden-franchise` import):

```
cp "/mnt/c/Users/jrere/Documents/Madden NFL 27/saves/CAREER-MUBASE" /tmp/coach_spike/
gzip -dk /tmp/coach_spike/M27_620_0.gz
node /tmp/coach_spike/dump_coaches2.mjs       # every live Coach record + the DataSource coach array
node /tmp/coach_spike/dump_refs.mjs           # sub-record references (talents, visuals, playbooks)
node /tmp/coach_spike/counts2.mjs             # enum usage counts
node /tmp/coach_spike/id_ranges.mjs           # PresentationId / Portrait ranges and gaps
node /tmp/coach_spike/parse_coach_visuals.mjs # CharacterVisuals.RawData format
node /tmp/coach_spike/dump_talents.mjs        # CoachingTalents / Talent[] / tier layout
node /tmp/coach_spike/extract_exe_json2.mjs   # CreateCoachM27 embedded JSON arrays
node /tmp/coach_spike/test_autoUnempty.mjs    # autoUnempty on an empty Coach row
node /tmp/coach_spike/test_enums.mjs          # enum write/read-back semantics
```

---

## 1. The Coach table and its fields

### 1.1 Location and size

| Table | Table id | Records | Live | Empty |
|---|---|---|---|---|
| `Coach` | **4168** | 470 | **127** | 343 (rows 127-469) |
| `Coach` (practice template) | 5866 | 1 | 1 | 0 |

- The main table holds every head coach, offensive coordinator and defensive coordinator.
- Table **5866** is a one-row create-a-coach practice template: `Tomas Practice`, Level 27, Portrait 7999, FaceShape `coachhead_6_M_N_21`, GenericHeadAssetName `7_M_BLK_D_001`. It is a useful field template, but it has **no** `StaffTalents`, no `CharacterVisuals`, no playbooks and no schemes.
- Row 100 of table 4168 is a second placeholder (empty names, Portrait 7999, all references null). It is not a usable coach.
- No special-teams coach is a live Coach record. `Team.SpecialTeamsCoach` points into the static table 16384 (row 116393), not into this table.

### 1.2 Field reference (122 fields, example = Todd Bowles, row 10)

The schema lists 123 attributes including the inherited base. The reader exposes **122 fields** per row: `HasTrait` is in the schema but is not exposed by `madden-franchise` 4.4.1 (the table below lists all 123 for completeness and it reads back as null). Values are the stored/decoded values for Todd Bowles.

| Field | Type | Meaning | Example |
|---|---|---|---|
| `Age` | int | Age in years (32-72 for created coaches, per plan D9). | 62 |
| `Archetype` | StaffArchetypeEnum <br>(StaffArchetypeEnum) | Coach archetype; value 0 reads back as `First` but means **OffensiveGuru** (see §3). | `DefensiveGenius` |
| `AssetName` | string | Real-coach headshot/asset key (`<Name>_C_PRO`); empty string for invented coaches. | `BowlesTodd_C_PRO` |
| `AwardPoints` | int | Madden-owned career / season counter. | 0 |
| `COACH_ADAPTIVE_AI` | CoachAdaptiveAI <br>(CoachAdaptiveAI) | `CoachAdaptiveAI` enum (aggression profile). | `Aggressive_2` |
| `COACH_CONSECTEAMCONTRACTS` | int | Madden coach rating/tendency field. | 0 |
| `COACH_DB` | int | Madden coach rating/tendency field. | 94 |
| `COACH_DEFENSETYPE` | int | Madden coach rating/tendency field. | 5 |
| `COACH_DEFENSE` | int | Madden coach rating/tendency field. | 99 |
| `COACH_DEFTENDENCYAGGRESSCONSERV` | int | Madden coach rating/tendency field. | 51 |
| `COACH_DEFTENDENCYRUNPASS` | int | Madden coach rating/tendency field. | 53 |
| `COACH_DEMEANOR` | CoachDemeanor <br>(CoachDemeanor) | `CoachDemeanor` enum (Classic / Calm / Intense). | `Calm` |
| `COACH_DL` | int | Madden coach rating/tendency field. | 95 |
| `COACH_FIREREPORTED` | bool | Madden coach rating/tendency field. | true |
| `COACH_K` | int | Madden coach rating/tendency field. | 35 |
| `COACH_LASTCONTRACTTEAM` | int | Madden coach rating/tendency field. | 0 |
| `COACH_LASTTEAMFIRED` | int | Madden coach rating/tendency field. | 1023 |
| `COACH_LASTTEAMRESIGNED` | int | Madden coach rating/tendency field. | 1023 |
| `COACH_LB` | int | Madden coach rating/tendency field. | 95 |
| `COACH_OFFENSE` | int | Madden coach rating/tendency field. | 45 |
| `COACH_OFFTENDENCYAGGRESSCONSERV` | int | Madden coach rating/tendency field. | 35 |
| `COACH_OFFTENDENCYRUNPASS` | int | Madden coach rating/tendency field. | 50 |
| `COACH_OL` | int | Madden coach rating/tendency field. | 40 |
| `COACH_PERFORMANCELEVEL` | int | Madden coach rating/tendency field. | 0 |
| `COACH_P` | int | Madden coach rating/tendency field. | 41 |
| `COACH_QB` | int | Madden coach rating/tendency field. | 36 |
| `COACH_RATING` | int | Overall coach rating; Madden writes **50** on a release (Spike B measured 54 -> 50). | 40 |
| `COACH_RBTENDENCY` | int | Madden coach rating/tendency field. | 55 |
| `COACH_RB` | int | Madden coach rating/tendency field. | 41 |
| `COACH_RESIGNREPORTED` | bool | Madden coach rating/tendency field. | true |
| `COACH_RETIREYRSLEFT` | int | Madden coach rating/tendency field. | 0 |
| `COACH_SPECIALTY` | CoachSpecialty <br>(CoachSpecialty) | `CoachSpecialty` enum (the position group the coach teaches). | `Linebackers` |
| `COACH_STANCE` | CoachStance <br>(CoachStance) | `CoachStance` enum (Classic / PlaySheet / HandsOnKnees). | `PlaySheet` |
| `COACH_S` | int | Madden coach rating/tendency field. | 93 |
| `COACH_WASPLAYER` | bool | Madden coach rating/tendency field. | false |
| `COACH_WR` | int | Madden coach rating/tendency field. | 40 |
| `CareerAssistant` | bool | Madden-owned career / season counter. | false |
| `CareerBigLossMargin` | int | Madden-owned career / season counter. | 0 |
| `CareerBigWinMargin` | int | Madden-owned career / season counter. | 0 |
| `CareerLongWinStreak` | int | Madden-owned career / season counter. | 1 |
| `CareerLosses` | int | Madden-owned career / season counter. | 74 |
| `CareerPlayoffLosses` | int | Madden-owned career / season counter. | 3 |
| `CareerPlayoffWins` | int | Madden-owned career / season counter. | 1 |
| `CareerPlayoffsMade` | int | Madden-owned career / season counter. | 3 |
| `CareerPointsAgainst` | int | Madden-owned career / season counter. | 0 |
| `CareerPointsFor` | int | Madden-owned career / season counter. | 0 |
| `CareerProBowlPlayers` | int | Madden-owned career / season counter. | 21 |
| `CareerSuperbowlLosses` | int | Madden-owned career / season counter. | 0 |
| `CareerSuperbowlWins` | int | Madden-owned career / season counter. | 0 |
| `CareerTies` | int | Madden-owned career / season counter. | 0 |
| `CareerWinSeasons` | int | Madden-owned career / season counter. | 0 |
| `CareerWins` | int | Madden-owned career / season counter. | 61 |
| `CharacterBodyType` | CharacterBodyType <br>(CharacterBodyType) | Body build, drives the 3D model and the visual JSON `bodyType`. | `Heavy` |
| `CharacterVisuals` | CharacterVisuals | Reference to a `CharacterVisuals` row (plain JSON in M27, see §6). | ref 4215:292 |
| `CoachBackstory` | CoachBackstory <br>(CoachBackstory) | `CoachBackstory` enum (Motivator / Strategist / TeamBuilder / StaffBuilder). | `Motivator` |
| `ConfPlayoffWinStreak` | int | Madden-owned career / season counter. | 0 |
| `ContractLength` | int | Contract length in years. | 4 |
| `ContractSalary` | int | Contract salary, Madden $10k units (Todd Bowles: 300 = $3.0M). | 300 |
| `ContractStatus` | StaffPersonContractStatus <br>(StaffPersonContractStatus) | `StaffPersonContractStatus`: Signed / FreeAgent / Retired / Deleted / Expiring / None. | `Signed` |
| `ContractYearsRemaining` | int | Years left on the contract; a release zeroes it (Spike B). | 2 |
| `CurrentPurchasedTalentCosts` | int | See schema; Madden-owned. | 0 |
| `DefaultTeamPhilosophy` | TeamPhilosophy | Same shape as `TeamPhilosophy`; normally equal to it. | ref 16384:5216 |
| `DefenseAudibles` | CoachAudibles | `CoachAudibles` reference; **null for every coach in this save**. | `00000000000000000000000000000000` |
| `DefensivePlaybook` | DefensivePlaybookDataType | Static `DefensivePlaybookDataType` row; see `DEF_PLAYBOOK_ROWS` (§7.3). | ref 16384:104496 |
| `DefensiveScheme` | Scheme | Static `Scheme` row. Team head coaches point at the team-specific table 16485 row instead; see §3.10. | ref 16485:57914 |
| `DivPlayoffWinStreak` | int | Madden-owned career / season counter. | 0 |
| `ExperiencePoints` | int | Coaching XP; level-progression currency (Madden-owned). | 34885 |
| `FaceShape` | CoachFace <br>(CoachFace) | `CoachFace` face preset. Real coaches use their name; invented coaches use `Invalid_` or a `coachhead_N_*` preset. | `MustBeUnique` |
| `FirstName` | string | Coach first name. | `Todd` |
| `GenericHeadAssetName` | string | Head-model asset key. Real coaches use their `<Name>` or `MustBeUnique`; invented coaches use `coachhead_M_####_HS`. | `MustBeUnique` |
| `HasTrait` | CoachingStaffPerson_HasTrait | `CoachingStaffPerson_HasTrait` reference; null for every coach in this save. | null |
| `Height` | int | Height in inches. | 74 |
| `InBooth` | bool | See schema; Madden-owned. | false |
| `IndexInUnlockList` | int | See schema; Madden-owned. | 14 |
| `IsCreated` | bool | See schema; Madden-owned. | false |
| `IsImported` | bool | See schema; Madden-owned. | false |
| `IsLegend` | bool | See schema; Madden-owned. | false |
| `IsMaxLevel` | bool | See schema; Madden-owned. | false |
| `IsUserControlled` | bool | True only for a team's user coach (linked to `Team.UserCharacter` / `FranchiseUser.UserEntity`). | false |
| `LastName` | string | Coach last name. | `Bowles` |
| `LegacyScore` | int | Madden-owned career / season counter. | 675 |
| `Level` | int | Coaching level (0-41 observed). Drives talent counts loosely (see §5.2). | 17 |
| `Name` | string | Display name, usually `F. Last` (`T. Bowles`). | `T. Bowles` |
| `OWNER_COMMENTID` | int | See schema; Madden-owned. | 0 |
| `OWNER_COMMENTTYPE` | int | See schema; Madden-owned. | 0 |
| `OffenseAudibles` | CoachAudibles | `CoachAudibles` reference; **null for every coach in this save**. | `00000000000000000000000000000000` |
| `OffensivePlaybook` | OffensivePlaybookDataType | Static `OffensivePlaybookDataType` row; see `OFF_PLAYBOOK_ROWS` (§7.3). | ref 16384:104463 |
| `OffensiveScheme` | Scheme | Static `Scheme` row. Team head coaches point at the team-specific table 16485 row instead; see §3.10. | ref 16485:57913 |
| `OriginalPosition` | StaffPosition <br>(StaffPosition) | Position the coach was hired at; identical to `Position` for all 127 records. | `HeadCoach` |
| `Personality` | Personality <br>(Personality) | `Personality` enum (Leader / Intense / Entertainer / TeamPlayer / Unpredictable). | `Leader` |
| `Portrait_Force_Silhouette` | bool | Always `false` in this save. | false |
| `Portrait_Swappable_Library_Path` | SwappableLibraryPath <br>(SwappableLibraryPath) | Always `library_icons_brt` in this save. | `library_icons_brt` |
| `Portrait` | int | Portrait texture ID in the shared `library_icons_brt` bank (see §4.2). | 5 |
| `Position` | StaffPosition <br>(StaffPosition) | `StaffPosition`: HeadCoach / OffensiveCoordinator / DefensiveCoordinator (only these three occur). | `HeadCoach` |
| `PresentationId` | int | Save-wide person id; unique among coaches. Max+1 = 752 (see §4.1). | 86 |
| `PrevTeamIndex` | int | Previous team index; 0 for coaches who have not moved. | 0 |
| `Probation` | bool | See schema; Madden-owned. | false |
| `RegularWinStreak` | int | Madden-owned career / season counter. | 0 |
| `SeasBigLossMargin` | int | Madden-owned career / season counter. | 0 |
| `SeasBigWinMargin` | int | Madden-owned career / season counter. | 0 |
| `SeasLongWinStreak` | int | Madden-owned career / season counter. | 0 |
| `SeasLosses` | int | Madden-owned career / season counter. | 0 |
| `SeasPointsAgainst` | int | Madden-owned career / season counter. | 0 |
| `SeasPointsFor` | int | Madden-owned career / season counter. | 0 |
| `SeasTies` | int | Madden-owned career / season counter. | 0 |
| `SeasWinStreak` | int | Madden-owned career / season counter. | 0 |
| `SeasWins` | int | Madden-owned career / season counter. | 0 |
| `SeasonsWithTeam` | int | Madden-owned career / season counter. | 4 |
| `SimDefensivePlaybook` | SimDefensivePlaybook | Static sim-playbook row (table 16483); often null. | ref 16483:10315 |
| `SimOffensivePlaybook` | SimOffensivePlaybook | Static sim-playbook row (table 16483); often null. | ref 16483:10353 |
| `SpeechId` | int | See schema; Madden-owned. | 31 |
| `StaffTalents` | AbstractTalentList | Reference to a `CoachingTalents` row (GamedayTalents + PlaysheetTalents); see §5. | ref 4304:8 |
| `SuperbowlWinStreak` | int | Madden-owned career / season counter. | 0 |
| `TeamBuilding` | TeamBuilding <br>(TeamBuilding) | `TeamBuilding` enum (Balanced / ThroughDraft / ThroughFreeAgency). | `Balanced` |
| `TeamIndex` | int | Team 0-31, or 32 = free agent. | 5 |
| `TeamPhilosophy` | TeamPhilosophy | Static `TeamPhilosophy` row (16384); team staff normally share the team's `Team.Philosophy`. | ref 16384:5216 |
| `TradingTendency` | TradingTendency <br>(TradingTendency) | `TradingTendency` enum (DoesNotTrade / TradesUp / TradesDown). | `DoesNotTrade` |
| `TraitExpertScout` | bool | See schema; Madden-owned. | false |
| `WCPlayoffWinStreak` | int | Madden-owned career / season counter. | 0 |
| `Weight` | int | Raw weight; **10 for all 127 coaches** (constant default, unlike players). | 10 |
| `WinSeasStreak` | int | Madden-owned career / season counter. | 0 |
| `YearlyAwardCount` | int | Madden-owned career / season counter. | 0 |
| `YearsCoaching` | int | Years of coaching experience. | 9 |

Uniform values across all 127 live coaches:

| Field | Value |
|---|---|
| `Portrait_Force_Silhouette` | `false` |
| `Portrait_Swappable_Library_Path` | `library_icons_brt` |
| `Weight` | `10` (constant; the player offset would read 170 lb, but every coach has 10) |
| `IsCreated`, `IsImported`, `IsLegend`, `IsMaxLevel`, `Probation`, `TraitExpertScout` | `false` |
| `OffenseAudibles`, `DefenseAudibles`, `HasTrait` | null (all-zero reference) |
| `OWNER_COMMENTID`, `OWNER_COMMENTTYPE`, `CurrentPurchasedTalentCosts` | `0` |

### 1.3 Sub-records a created coach needs

| Field | Points at | Shape |
|---|---|---|
| `StaffTalents` | `CoachingTalents` (table 4304) | Two references: `GamedayTalents` and `PlaysheetTalents`, each to a `Talent[]` record (table 5713, 200 slots). See §5. |
| `CharacterVisuals` | `CharacterVisuals` (table 4215) | One `RawData` string of **plain JSON** (540-1,482 bytes for coaches). See §6. |
| `OffensivePlaybook` / `DefensivePlaybook` | static table 16384 | `OffensivePlaybookDataType` / `DefensivePlaybookDataType` rows. |
| `OffensiveScheme` / `DefensiveScheme` | static 16384 (coordinators, free agents) or **16485** (team head coaches) | `Scheme` rows. See §3.10. |
| `SimOffensivePlaybook` / `SimDefensivePlaybook` | static table 16483 | Optional; 65/66 coaches have none. Clone from a template. |
| `TeamPhilosophy` / `DefaultTeamPhilosophy` | static table 16384 | Normally equal to the team's `Team.Philosophy` (90 of 96 team staff match; 6 coordinators carry a different philosophy). |
| `OffenseAudibles` / `DefenseAudibles` | `CoachAudibles` (table 4136) | **Null for every coach in this save**; the writer can leave them null. |

---

## 2. `CoachesRoot` and the free-agent pool

### 2.1 Answer to R3: where the free-agent pool actually is

The schema defines `CoachesRoot` (schema 1624) with `CoachesArrays: Coach[][]`, but **the save contains no table named `CoachesRoot`, `GeneratorRootData` or `Coach[][]`**. **The answer to R3 is that there is no free-agent array to write: the free-agent pool is simply `Coach.TeamIndex = 32`.** This is confirmed by Spike B's measured in-game firing diff (`docs/reference/MADDEN_COACH_FIRING_DIFF.md`, 2026-11-10): firing a coach sets fields on his Coach record and nulls the team's seat ref; **no array changes membership**.

Spike A did find a `Coach[]` array at `DataSource[1].Data` (table 5868) whose 29 members are exactly the 29 `TeamIndex = 32` coaches in CAREER-MUBASE:

| Path | Value in CAREER-MUBASE |
|---|---|
| `DataSource` table | id **4140**, 512 records |
| Row | **`DataSource[1]`** (row index 1) |
| `DataSource[1].Data` | ref `5868:0` -> table **5868** `Coach[]`, record 0 |
| `DataSource[1].DataTemplate` | ref `16455:42680` (static template record, not readable from the save) |
| Array size | 100 slots; **29 filled**, packed from slot 0, slots 29-99 empty |

But Spike B's before/after diff shows Madden does **not** add a fired coach to this array and changes no array membership. Treat it as a **static generator/seed pool** (it sits under `DataSource`, alongside other generator roots), not the free-agent registry. **A release or retire must not touch it.** Changing it is at best a no-op and at worst corrupts a generator pool. If a later spike proves the game reads it, the locator is: scan `DataSource` records for the one whose `.Data` decodes to a `Coach[]` table; in this save that is row 1.

Other coach arrays exist but none is the free-agent pool:

| Table | Referenced by | Meaning | Contents here |
|---|---|---|---|
| 5590 (`Coach[]`, 4 records x 5 slots) | `CoachRetirementEval` fields `CoachesReturningThisYear`, `...AfterOneYear`, `...AfterTwoYears`, `...AfterThreeYears` | Retirement/return queues | all empty |
| 5869 (`Coach[]`, 50 slots) | `Franchise.RetiredCoaches`, `InductHallOfFameCoachesDataFlow.Subjects` | Retired-coach list | empty |
| 5867 (`Coach[]`, 100 slots) | `League.HallOfFameCoaches` | Hall of Fame coaches | 1 entry: John Madden (Coach row 56) |

### 2.2 What a free agent looks like vs a signed coach

| Field | Signed team coach | Free agent |
|---|---|---|
| `TeamIndex` | 0-31 | **32** |
| `ContractStatus` | `Signed` | **`FreeAgent`** |
| `PrevTeamIndex` | 0 in this save | 0 in this save |
| Team ref | present in `Team.HeadCoach` / `OffensiveCoordinator` / `DefensiveCoordinator` | no team ref |
| Free-agent identification | not applicable | **`TeamIndex = 32`** (no array membership) |
| `ContractYearsRemaining` | 1+ | `0` (Madden's release writes this) |
| `COACH_RATING` | varies | `50` (Madden's release lands here; Spike B measured 54 -> 50) |
| Playbook refs | the team's playbook rows | arbitrary team/generic rows |
| Scheme refs | 16384 (OC/DC) or 16485 (HC) | 16384 |

Counts in CAREER-MUBASE: 96 `Signed`, 31 `FreeAgent`. 29 of the free agents sit at `TeamIndex 32`; the two exceptions are **John Madden** (row 56, `TeamIndex 0`, in the Hall of Fame array) and the **blank placeholder** (row 100, `TeamIndex 0`). No coach has `ContractStatus = Retired`, `Deleted`, `Expiring` or `None` in this save. Spike B later observed `Expiring` (Madden's own deal-running-out state, distinct from `FreeAgent`; `TeamIndex` unchanged) in a `CAREER-TUNE-*` save, so Mu should always write `FreeAgent` explicitly.

**A retired coach cannot be observed in this save**, and `Franchise.RetiredCoaches` is empty. Spike B did not fire a coach into retirement. A proposed write is `ContractStatus = Retired`, keep `TeamIndex = 32`, no array write; Spike C must confirm whether `Franchise.RetiredCoaches` also expects a membership.

`IsUserControlled` is true for exactly one coach in this save: **Dave Canales, CAR (Coach row 17)**; his `Team.HeadCoach` and `Team.UserCharacter` both point at row 17. A head-coach change on a user team must move `Team.HeadCoach`, `Team.UserCharacter`, `FranchiseUser.UserEntity` and `IsUserControlled` together (the `league_setup.mjs` rule).

### 2.3 Release / retire target paths (copy-pasteable)

**A released coach is added to no array and removed from no array.** The measured release (Spike B) is five field writes plus a null seat ref:

```
release(coach):
  Team.HeadCoach / OffensiveCoordinator / DefensiveCoordinator that points at coach -> NO_REF
  coach.ContractStatus        = "FreeAgent"
  coach.PrevTeamIndex         = coach.TeamIndex
  coach.TeamIndex             = 32
  coach.ContractYearsRemaining = 0
  coach.COACH_RATING          = 50
  coach.IsUserControlled      = false      # Mu sets it; Madden leaves it true
  # no array membership, no deletion, no new/split record

retire(coach):
  coach.ContractStatus = "Retired"
  keep TeamIndex = 32                      # unconfirmed (no retired coach in this save)
  # no array membership; confirm whether Franchise.RetiredCoaches expects one (Spike C)
```

Spike B also measured, and the writer must account for:

- **A release does not change `Position` or `OriginalPosition`.** An `assign` that changes role writes both.
- **Firing a head coach clears his OC and DC seats as well.** A Mu HC change must release-and-reassign (or re-fill) the coordinators it wants to keep; `plan_staff_edits` self-heals a cleared seat, but this needs a test.
- **On a user team, Madden's firing nulls `Team.UserCharacter` and `FranchiseUser.Team`, leaves `FranchiseUser.UserEntity` and `IsUserControlled` stale.** Mu's HC `assign` must restore `Team.HeadCoach`, `Team.UserCharacter`, `FranchiseUser.UserEntity`, `FranchiseUser.Team` and `Coach.IsUserControlled = true`.
- **Madden does not hire at the firing**; the seats stay empty until Mu fills them.

### 2.4 A freed (empty) Coach row, and `autoUnempty`

- Rows 127-469 (343 rows) are empty. An empty row decodes with 122 fields, `isEmpty = true`, `Portrait = 0`, `TeamIndex = 0`, and non-zero defaults for `TeamBuilding` (`Balanced`), `TradingTendency` (`DoesNotTrade`) and some references.
- **Verified:** opening the save with `autoUnempty: true`, setting `FirstName`, `LastName` and `Age` on row 127, saving and re-opening turns the row live (`isEmpty = false`) with the set values. Defaults that appear: `PresentationId = 0`, `TeamIndex = 0`, `ContractStatus = Signed`. So the writer must set `PresentationId`, `TeamIndex`, `ContractStatus`, `Position`, the name fields and every profile field explicitly; do not rely on row defaults.

---

## 3. Enums: real members and what the save uses

The schema's enum lists include structural sentinels (`First_`, `Last_`, `Max_`, `Invalid_`, `Count_`, aliases at the same value). Below, **real members only**. "Save" counts are across the 127 live coaches.

### 3.1 StaffArchetypeEnum

Real members: `OffensiveGuru=0`, `DefensiveGenius=1`, `PersonnelCzar=2`, `DevelopmentWizard=3`, `HeadScout=4`, `HeadTrainer=5`, `MasterMotivator=6`, `MasterMotivator_JohnMadden=7`, `SharedCoach=8`.

Save: `First` **68** (= value 0 = **OffensiveGuru**), `DefensiveGenius` 54, `DevelopmentWizard` 5. Position cross-tab: all 45 OCs are OffensiveGuru; all 41 DCs are DefensiveGenius; HCs split 23 OffensiveGuru / 13 DefensiveGenius / 5 DevelopmentWizard.

**Important read-back quirk:** value 0 has two names in the schema (`First` and `OffensiveGuru`). The library returns **`First`**. Writing `Archetype = "OffensiveGuru"` is accepted and reads back as **`"First"`**; a verifier must compare stored values, not the requested string (as `sign.mjs`'s `fieldMismatches` already does).

### 3.2 StaffPosition

Real members: `HeadCoach=0`, `OffensiveCoordinator=1`, `DefensiveCoordinator=2`, `SpecialTeams=3`, `Owner=4`, `Scout=5`, `Trainer=6`, `GeneralManager=7`, `PlayerPersonnel=8`.

Save (both `Position` and `OriginalPosition`): `OffensiveCoordinator` 45, `DefensiveCoordinator` 41, `HeadCoach` 41. No `SpecialTeams`.

### 3.3 CoachSpecialty

Real members: `Quarterbacks=0`, `RunningBacks=1`, `Receivers=2`, `OffensiveLine=3`, `DefensiveLine=4`, `Linebackers=5`, `Secondary=6`, `SpecialTeams=7`.

Save: Quarterbacks 41, Secondary 23, Linebackers 22, DefensiveLine 16, Receivers 11, OffensiveLine 9, RunningBacks 5. `SpecialTeams` is unused.

### 3.4 StaffPersonContractStatus (the `ContractStatus` field)

Real members: `Signed=0`, `FreeAgent=1`, `Retired=2`, `Deleted=3`, `Expiring=4`, `None=5`.

Save: Signed 96, FreeAgent 31. No Retired/Deleted/Expiring/None.

### 3.5 CharacterBodyType and the visual `bodyType`

Real members: `Standard=0`, `Thin=1`, `Muscular=2`, `Heavy=3`, `Freshman=4`, `ReservedMain_1=5`, `ReservedMain_2=6`, `ReservedMain_3=7`, `Standard_Alternate=8`, `Thin_Alternate=9`.

Save: Standard 102, Muscular 11, Thin 9, Heavy 5. In the visuals JSON: `Standard` coaches omit `bodyType`; Thin writes `1`, Muscular `2`, Heavy `3` (see §6).

### 3.6 Personality / demeanor / stance / backstory / AI

| Enum | Real members | Save usage |
|---|---|---|
| `Personality` | Leader=0, Intense=1, Entertainer=2, TeamPlayer=3, Unpredictable=4 | Unpredictable 126, Leader 1 |
| `CoachDemeanor` (`COACH_DEMEANOR`) | Classic=0, Calm=1, Intense=2 | Classic 111, Intense 11, Calm 5 |
| `CoachStance` (`COACH_STANCE`) | Classic=0, PlaySheet=1, HandsOnKnees=2 | Classic 100, PlaySheet 22, HandsOnKnees 5 |
| `CoachBackstory` | Motivator=0, Strategist=1, TeamBuilder=2, StaffBuilder=3 | Strategist 60, Motivator 38, TeamBuilder 29 |
| `CoachAdaptiveAI` | Conservative=0, Balanced=1, Aggressive=2, Conservative_1=3, Balanced_1=4, Aggressive_1=5, Conservative_2=6, Balanced_2=7, Aggressive_2=8, Conservative_3=9, Balanced_3=10, Aggressive_3=11 | Conservative 62, Balanced_2 14, Balanced_1 10, Aggressive_2 8, Conservative_1 8, Aggressive 7, Conservative_2 7, Balanced 6, Aggressive_1 5 |
| `TeamBuilding` | Balanced=0, ThroughDraft=1, ThroughFreeAgency=2 | Balanced 98, ThroughDraft 25, ThroughFreeAgency 4 |
| `TradingTendency` | DoesNotTrade=0, TradesUp=1, TradesDown=2 | DoesNotTrade 75, TradesDown 35, TradesUp 17 |

### 3.7 Offensive playbooks

Real members (50 + legacy `M26JohnMadden=195`):

```
Bears=0, Bengals=1, Bills=2, Broncos=3, Browns=4, Buccaneers=5, Cardinals=6, Chargers=7, Chiefs=8, Colts=9, Cowboys=10, Dolphins=11, Eagles=12, Falcons=13, FortyNiners=14, Giants=15, Jaguars=16, Jets=17, Lions=18, Packers=19, Panthers=20, Patriots=21, Raiders=22, Rams=23, Ravens=24, Commanders=25, Saints=26, Seahawks=27, Steelers=28, Titans=29, Vikings=30, Texans=31, Balanced=32, BalancedPass=33, BalancedRun=34, PowerRun=35, WestCoast=36, RunnGun=37, BillWalsh=38, BrianBillick=39, DickVermeil=40, JoeGibbs=41, JohnMadden=42, TomLandry=43, TomFlores=44, VinceLombardi=45, JimmyJohnson=46, MikeDitka=47, Pistol=48, RunandShoot=49, M26JohnMadden=195
```

Save: all **32 team playbooks** appear; the 18 generic/legend playbooks do not. Counts: Cardinals 6, Buccaneers 6, Panthers 6, FortyNiners 6, Bills 5, Titans 5, Raiders 5, Rams 5; Bengals, Bears, Colts, Browns, Eagles, Falcons, Vikings, Seahawks, Saints 4; Cowboys, Jaguars, Broncos, Ravens, Chargers, Texans, Lions, Chiefs, Packers, Jets, Dolphins, Giants, Steelers, Patriots, Commanders 3. Two coaches (John Madden and the placeholder) have no playbook.

### 3.8 Defensive playbooks

Real members (37 + legacy `M26JohnMadden=194`):

```
Bears=0, Bengals=1, Bills=2, Broncos=3, Browns=4, Buccaneers=5, Cardinals=6, Chargers=7, Chiefs=8, Colts=9, Cowboys=10, Dolphins=11, Eagles=12, Falcons=13, FortyNiners=14, Giants=15, Jaguars=16, Jets=17, Lions=18, Packers=19, Panthers=20, Patriots=21, Raiders=22, Rams=23, Ravens=24, Commanders=25, Saints=26, Seahawks=27, Steelers=28, Titans=29, Vikings=30, Texans=31, FourThree=32, ThreeFour=33, FourSix=34, Cover2=35, MultipleD=36, M26JohnMadden=194
```

Save: all **32 team playbooks** appear; `FourSix` appears once; the other generics do not. Counts: Rams 7, Bills 6, Cardinals/Buccaneers/Titans/Seahawks/FortyNiners 5, Cowboys/Bears/Colts/Jaguars/Browns/Panthers/Eagles/Dolphins/Steelers/Vikings/Saints 4, Bengals/Broncos/Ravens/Chargers/Texans/Lions/Chiefs/Falcons/Packers/Jets/Giants/Raiders/Patriots/Commanders 3, FourSix 1.

### 3.9 Offensive / defensive schemes (BaseScheme)

Real members (`BaseScheme`; 0-10 offense, 11-20 defense, `Locked=21`):

```
WestCoastZoneRun=0, WestCoastPowerRun=1, VerticalZoneRun=2, MultipleZoneRun=3, MultiplePowerRun=4, VerticalPowerRun=5, Spread=6, RunAndShoot=7, AirRaid=8, Pistol=9, WestCoastSpread=10, Base4_3=11, Under4_3=12, Base3_4=13, Under3_4=14, Tampa2=15, Quarters4_3=16, Disguise3_4=17, Storm3_4=18, Cover3_4_3=19, Defense_46=20, Locked=21
```

Save usage (scheme refs are static `Scheme` rows, mapped via the table in §7):

| Scheme | Count | Scheme | Count |
|---|---|---|---|
| WestCoastZoneRun | 36 | Base4_3 | 29 |
| MultipleZoneRun | 22 | Base3_4 | 27 |
| MultiplePowerRun | 17 | Under3_4 | 16 |
| WestCoastPowerRun | 13 | Under4_3 | 11 |
| WestCoastSpread | 11 | Tampa2 | 10 |
| VerticalPowerRun | 10 | Quarters4_3 | 9 |
| VerticalZoneRun | 8 | Cover3_4_3 | 9 |
| Spread | 3 | Disguise3_4 | 8 |
| AirRaid | 3 | Storm3_4 | 5 |
| RunAndShoot | 2 | Defense_46 | 1 |
| Pistol | 0 | | |

(The two null-scheme coaches are John Madden and the placeholder.)

### 3.10 The 16485 head-coach scheme table

Every **team head coach** (32 of 41 HCs) stores `OffensiveScheme` and `DefensiveScheme` as references into static table **16485**, not 16384. Free-agent HCs and all coordinators use 16384.

The 16485 rows are **team-specific scheme instances**, not enum-indexed. The team's enum values are available directly as strings on the Team row: `Team.DefaultOffensiveScheme` / `Team.DefaultDefensiveScheme` (e.g. TB: `WestCoastZoneRun` / `Base3_4`). Cross-checked across all 32 teams: each 16485 row always maps to the same enum, and every HC's pair matches his team's defaults.

| TeamIndex | Team | Team default offense | Team default defense | HC OffensiveScheme ref | HC DefensiveScheme ref |
|---|---|---|---|---|---|
| 0 | CHI | MultiplePowerRun | Base4_3 | 16485:57861 | 16485:57862 |
| 1 | CIN | WestCoastPowerRun | Base4_3 | 16485:57873 | 16485:57874 |
| 2 | BUF | WestCoastZoneRun | Tampa2 | 16485:57865 | 16485:57866 |
| 3 | DEN | WestCoastPowerRun | Base3_4 | 16485:57887 | 16485:57888 |
| 4 | CLE | VerticalPowerRun | Base4_3 | 16485:57875 | 16485:57876 |
| 5 | TB | WestCoastZoneRun | Base3_4 | 16485:57913 | 16485:57914 |
| 6 | AZ | WestCoastZoneRun | Tampa2 | 16485:57915 | 16485:57916 |
| 7 | LAC | MultipleZoneRun | Base3_4 | 16485:57893 | 16485:57872 |
| 8 | KC | WestCoastZoneRun | Base4_3 | 16485:57889 | 16485:57890 |
| 9 | IND | WestCoastPowerRun | Tampa2 | 16485:57881 | 16485:57882 |
| 10 | DAL | WestCoastPowerRun | Base4_3 | 16485:57895 | 16485:57896 |
| 11 | MIA | WestCoastPowerRun | Tampa2 | 16485:57887 | 16485:57868 |
| 12 | PHI | MultiplePowerRun | Base3_4 | 16485:57897 | 16485:57898 |
| 13 | ATL | MultiplePowerRun | Base4_3 | 16485:57909 | 16485:57910 |
| 14 | SF | MultipleZoneRun | Base4_3 | 16485:57921 | 16485:57922 |
| 15 | NYG | MultiplePowerRun | Base4_3 | 16485:57899 | 16485:57900 |
| 16 | JAX | WestCoastZoneRun | Base4_3 | 16485:57883 | 16485:57884 |
| 17 | NYJ | MultiplePowerRun | Base4_3 | 16485:57860 | 16485:57858 |
| 18 | DET | MultiplePowerRun | Base4_3 | 16485:57857 | 16485:57858 |
| 19 | GB | WestCoastZoneRun | Tampa2 | 16485:57903 | 16485:57904 |
| 20 | CAR | WestCoastZoneRun | Tampa2 | 16485:57907 | 16485:57908 |
| 21 | NE | MultiplePowerRun | Base3_4 | 16485:57869 | 16485:57870 |
| 22 | LV | MultipleZoneRun | Base4_3 | 16485:57891 | 16485:57892 |
| 23 | LAR | WestCoastZoneRun | Base3_4 | 16485:57917 | 16485:57920 |
| 24 | BAL | MultiplePowerRun | Base3_4 | 16485:57871 | 16485:57920 |
| 25 | WAS | WestCoastZoneRun | Base4_3 | 16485:57901 | 16485:57902 |
| 26 | NO | WestCoastZoneRun | Base3_4 | 16485:57911 | 16485:57912 |
| 27 | SEA | MultipleZoneRun | Base3_4 | 16485:57863 | 16485:57864 |
| 28 | PIT | WestCoastPowerRun | Base3_4 | 16485:57877 | 16485:57878 |
| 29 | TEN | WestCoastPowerRun | Base4_3 | 16485:57885 | 16485:57886 |
| 30 | MIN | WestCoastZoneRun | Base3_4 | 16485:57905 | 16485:57906 |
| 31 | HOU | WestCoastZoneRun | Base4_3 | 16485:57879 | 16485:57880 |

**Writer rule:** for a team head coach, reuse the outgoing HC's 16485 pair (or the table above); for coordinators use the 16384 `SCHEME_ROWS` in §7. Do not point a team HC at a 16384 row without Spike C — the game may rebuild or reject it.

### 3.11 Sim playbooks

`SimOffensivePlaybookEnum`: `SIMFocusedTargeting_Ph=339`, `SIMFocusedTargeting_Ba=340`, `SIMFocusedTargeting_Rn=341`, `SIMExplosiveStress_Ph=342`, `SIMExplosiveStress_Ba=343`, `SIMExplosiveStress_Rn=344`, `SIMVeritcalTeSpine_Ph=345`, `SIMMultiRolePersonnel_Ba=346`, `SIMMultiRolePersonnel_Rn=347`, `SIMVolumeDistribution_Ph=348`, `SIMVolumeDistribution_Ba=349`, `SIMVolumeDistribution_Rn=350`, `SIMScreenRunBlend_Ph=351`, `SIMScreenRunBlend_Ba=352`, `SIMScreenRunBlend_Rn=353`.

`SimDefensivePlaybookEnum`: `SIM34Defense=336`, `SIM43Defense=337`, `SIM46Defense=338`, `SIMCover2Defense=343`, `SIMMultipleDDefense=344`.

Coach fields are references to static table **16483** rows, not the enum. Save: `SimOffensivePlaybook` is mostly `16483:10353` (30) with a handful of 10341-10352; 65 coaches are null. `SimDefensivePlaybook` is `16483:10315` (27) or `16483:10327` (34); 66 are null. Clone the template coach's values; they need not match `Team.TEAM_SIMPLAYBOOK_*`.

### 3.12 FaceShape (CoachFace) and GenericHeadAssetName

`FaceShape` is the `CoachFace` enum (231 members). Real coaches use their named preset (`ReidAndy`, `ShanahanKyle`, ...); invented coaches use `Invalid_` (value 255) or one of the generic `coachhead_N_*` presets.

Save: `Invalid_` 83, `MustBeUnique` 35, and the named presets `patriotsgeneric`, `harbaughJohn`, `MaddenJohn`, `mcCarthyMike`, `McVaySean`, `QuinnDan`, `ReidAndy`, `ShanahanKyle`, `BelichickBill` (1 each).

Generic `CoachFace` presets, grouped by skin-tone digit (the first digit, 1-7, is the skin-tone group used by the headshot generator):

```js
export const COACH_FACE_GENERIC = {
  1: ["coachhead_1_B_N_01", "coachhead_1_B_N_20", "coachhead_1_B_S_20", "coachhead_1_H_B_009", "coachhead_1_H_BD_01", "coachhead_1_H_N_01", "coachhead_1_H_N_03", "coachhead_1_H_N_20", "coachhead_1_M_BD_01", "coachhead_1_M_N_30", "coachhead_1_M_S_011"],
  2: ["coachhead_2_B_M_20", "coachhead_2_B_N_20", "coachhead_2_B_N_21", "coachhead_2_H_M_11", "coachhead_2_H_M_20", "coachhead_2_H_N_03", "coachhead_2_M_N_20", "coachhead_2_M_N_21", "coachhead_2_M_N_22", "coachhead_2_M_N_30"],
  3: ["coachhead_3_B_N_21", "coachhead_3_B_S_20", "coachhead_3_H_B_20", "coachhead_3_H_BD_01", "coachhead_3_H_G_20", "coachhead_3_H_N_20", "coachhead_3_H_N_21", "coachhead_3_H_S_001", "coachhead_3_M_N_20", "coachhead_3_M_N_21", "coachhead_3_M_N_22"],
  4: ["coachhead_4_B_G_01", "coachhead_4_B_MS_01", "coachhead_4_H_BD_01", "coachhead_4_H_G_01", "coachhead_4_H_MS_01", "coachhead_4_H_N_30", "coachhead_4_M_B_30", "coachhead_4_M_BD_01", "coachhead_4_M_N_30", "coachhead_4_T_N_006"],
  5: ["coachhead_5_B_G_20", "coachhead_5_B_N_20", "coachhead_5_B_N_21", "coachhead_5_H_B_20", "coachhead_5_H_N_20", "coachhead_5_H_N_21", "coachhead_5_M_M_30", "coachhead_5_M_N_02", "coachhead_5_M_N_20"],
  6: ["coachhead_6_B_M_20", "coachhead_6_B_N_03", "coachhead_6_B_N_20", "coachhead_6_H_B_30", "coachhead_6_H_G_20", "coachhead_6_H_N_20", "coachhead_6_H_N_21", "coachhead_6_M_N_20", "coachhead_6_M_N_21", "coachhead_6_M_N_22"],
  7: ["coachhead_7_B_B_20", "coachhead_7_B_BD_01", "coachhead_7_B_G_20", "coachhead_7_B_M_20", "coachhead_7_B_N_01", "coachhead_7_B_N_06", "coachhead_7_B_N_20", "coachhead_7_H_BD_01", "coachhead_7_H_M_20", "coachhead_7_H_M_21", "coachhead_7_H_N_01", "coachhead_7_H_N_30", "coachhead_7_M_B_20", "coachhead_7_M_BD_02", "coachhead_7_M_M_30", "coachhead_7_M_N_30"],
};
```

`GenericHeadAssetName` is a **string**, not an enum reference. Real coaches use their own asset key or `MustBeUnique`; invented/free-agent coaches use one of these 53 head assets:

```js
export const GENERIC_HEAD_ASSETS = ["coachhead_M_0001_HS", "coachhead_M_0002_HS", "coachhead_M_0003_HS", "coachhead_M_0008_HS", "coachhead_M_0009_HS", "coachhead_M_0011_HS", "coachhead_M_0013_HS", "coachhead_M_0014_HS", "coachhead_M_0015_HS", "coachhead_M_0016_HS", "coachhead_M_0017_HS", "coachhead_M_0018_HS", "coachhead_M_0019_HS", "coachhead_M_0020_HS", "coachhead_M_0046_HS", "coachhead_M_0055_HS", "coachhead_M_0056_HS", "coachhead_M_0057_HS", "coachhead_M_0058_HS", "coachhead_M_0059_HS", "coachhead_M_0060_HS", "coachhead_M_0061_HS", "coachhead_M_0062_HS", "coachhead_M_0063_HS", "coachhead_M_0064_HS", "coachhead_M_0067_HS", "coachhead_M_0068_HS", "coachhead_M_0069_HS", "coachhead_M_0070_HS", "coachhead_M_0072_HS", "coachhead_M_0074_HS", "coachhead_M_0075_HS", "coachhead_M_0077_HS", "coachhead_M_0080_HS", "coachhead_M_0083_HS", "coachhead_M_0084_HS", "coachhead_M_0085_HS", "coachhead_M_0086_HS", "coachhead_M_0087_HS", "coachhead_M_0089_HS", "coachhead_M_0091_HS", "coachhead_M_0092_HS", "coachhead_M_0093_HS", "coachhead_M_0094_HS", "coachhead_M_0095_HS", "coachhead_M_0096_HS", "coachhead_M_0122_HS", "coachhead_M_0125_HS", "coachhead_M_0131_HS", "coachhead_M_0146_HS", "coachhead_M_0161_HS", "coachhead_M_0182_HS", "coachhead_M_0188_HS"];
```

The schema also has a `GenericHead` enum (313 members, names such as `1_B_N_01`); the Coach field does **not** store enum members, so do not write those strings here. `AssetName` is the real-coach headshot/asset key (`<Name>_C_PRO`); invented coaches have `""`.

### 3.13 Team philosophy

`TeamPhilosophy` is a **struct type**, not an enum: it holds `BuildingPhilosophy`, the four depth-chart philosophies, `DraftPhilosophy`, `DraftTradePhilosophy`, `SpendingPhilosophy`, `StaffHiringPhilosophyRef` and `TeamTradePhilosophy`. The Coach field is a reference to a static team-philosophy row in table 16384. In this save there are 32 such rows (one per team, e.g. 16384:5195-5226). Team staff normally share the team's `Team.Philosophy` (90/96); 6 coordinators differ. The only enum directly involved is `TeamBuilding` (Balanced / ThroughDraft / ThroughFreeAgency), already listed above. **Write the team-appropriate 16384 reference (copy from the template coach or `Team.Philosophy`), not an invented value.**

---

## 4. ID ranges

### 4.1 PresentationId

- Live coaches: **127 unique** values, min **0**, max **751**.
- Gaps: 625 of 0-751 are unused by coaches.
- **`max + 1 = 752` is free** among coaches and among players; 752-759 are all unused. The current rule is safe.
- Coach and player PresentationIds are mostly separate spaces but overlap at **0** and **183**. Checking both tables (as done for 752) is still the safe habit.
- The practice-template table 5866 has its own PresentationId 180; it is a different table and does not affect the main tally.

### 4.2 Portrait (coach and player share one library)

- Coach `Portrait`: **118 unique IDs**, min **1**, max **7999** (7999 is the blank placeholder's value). Used values fall in the blocks 1-52 (with gaps), 321-328, 363-404, 510-533, 543-569, plus 7999.
- Player `PLYR_PORTRAIT`: 3,325 live players, 3,038 unique IDs, min **0**, max **30000**. Real texture IDs cluster up to **11484**; only two player IDs are above 12000 (`14374`, `30000`).
- Both tables write `library_icons_brt` as the swappable library path. **Coach portraits and player portraits are one numeric space over one texture bank**; 29 IDs are currently used by both (e.g. coach Rikki Anderson and player Darnay Holmes both 365; coach Patrick Bailey and player Justin Jefferson both 385). A coach must therefore be given an ID used by **neither** table if the game is not to reuse an existing face.
- Free runs of at least 20 IDs in 1-11484 (the library's observed real range), with no Coach or Player reference:

```
5324-5484 (161)   5486-5581 (96)    5655-5789 (135)   5921-6029 (109)
6150-6298 (149)   6729-6868 (140)   6870-6978 (109)   7564-7711 (148)
3978-4057 (80)    5039-5103 (65)    7423-7491 (69)    7493-7562 (70)
```

`COACH_PORTRAIT_RANGE` = **5324-5484** (161 IDs) is the recommended primary block: it is the largest run inside the texture range and is free of every Coach and Player reference in CAREER-MUBASE. Use 5486-5581 next. Whether the texture bank *contains* art at these IDs is not provable from the save; the portrait pack supplies the DDS anyway, so any free ID is usable if `/build-mod` can replace/add that library entry (Spike D / R4).

- `Portrait_Force_Silhouette = false` everywhere and `Portrait_Swappable_Library_Path = library_icons_brt` everywhere. For the "silhouette until the pack is installed" path (plan D5), set `Portrait = 0` and `Portrait_Force_Silhouette = true`; no coach in this save exercises that combination, so Spike C must confirm it renders a silhouette.
- `Portrait = 7999` is used only by the two placeholder rows (table 5866 and Coach row 100); it is a "no portrait" placeholder, not a usable face.

---

## 5. Talents and wardrobe

### 5.1 Talent record layout

```
Coach.StaffTalents -> CoachingTalents (table 4304, cap 1000, 125 live)
    .GamedayTalents  -> Talent[] (table 5713, 1000 records x 200 slots)
    .PlaysheetTalents-> Talent[] (same table)
Talent[] slot -> ref to one of
    GamedayTalent (table 4115, cap 6720, 985 live)
    SeasonTalent  (table 4112, cap 7680, 242 live)   # PlayerDevelopment / Scouting / Staff talents
    PlaysheetTalent (table 4251, cap 3840, 414 live)
Each talent row:
    .TalentInfo -> static info row (tables 16469, 16470, 16458, 16474, 16476)
    .CurrentTier int 0-3
    .Status      TalentStatus (None / Unlocked / Upgraded / Mastered / Downgraded / Lost) - None for most
    .IsTalentActive bool
    .OwnerPosition StaffPosition
    .Tiers -> TalentTier[] (table 5342, 20160 records, 1750 live), exactly 4 slots
Each TalentTier[] slot -> TalentTier (table 4097, 65535 records, 7060 live)
    .TierStatus TalentTierStatus: NotOwned=0 / Purchasable=1 / Owned=2 / Mastered=3
```

Filled-slot example (Todd Bowles, Gameday slot 0): `GamedayTalent 4115:797`, `TalentInfo 16469:53034` ("No Fly Zone"), `CurrentTier 2`, `Status None`, `IsTalentActive false`, `Tiers 5342:1377` -> four `TalentTier` rows with statuses `Owned, Owned, Owned, Purchasable`.

Rules observed in all 1,639 talent entries:

- Every talent has **exactly 4 tiers**.
- `CurrentTier = n` => tiers 0..n are `Owned`, tiers n+1..3 are `Purchasable` (unowned talents use `NotOwned`).
- Every `Talent[]` list is packed from slot 0 (0 unpacked in the save).
- `Status` is `None` for playsheet talents and for most gameday talents; only 122 gameday rows carry a non-None status.
- The gameday list may hold `SeasonTalent` rows (PlayerDevelopment/Scouting/Staff) as well as `GamedayTalent` rows. The playsheet list holds only `PlaysheetTalent`.

### 5.2 How talent counts scale with Level

There is **no fixed gameday/playsheet count by Level**; the game accumulates talents over a coach's career. For each level, observed gameday / playsheet counts (min / median / max):

| Level | Coaches | Gameday min / med / max | Playsheet min / med / max |
|---|---|---|---|
| 0 | 21 | 1 / 7 / 14 | 0 / 0 / 10 |
| 1 | 4 | 5 / 6 / 7 | 0 / 2 / 3 |
| 2 | 16 | 5 / 7 / 10 | 1 / 3 / 6 |
| 3 | 29 | 5 / 8 / 13 | 0 / 3 / 7 |
| 4 | 11 | 5 / 7 / 17 | 0 / 3 / 6 |
| 5 | 8 | 7 / 9 / 13 | 1 / 2 / 9 |
| 6 | 5 | 6 / 13 / 16 | 0 / 4 / 5 |
| 8 | 2 | 9 / 9 / 11 | 4 / 4 / 5 |
| 9 | 2 | 7 / 7 / 15 | 3 / 3 / 6 |
| 10 | 2 | 6 / 6 / 16 | 3 / 3 / 4 |
| 11 | 1 | 18 / 18 / 18 | 4 / 4 / 4 |
| 12 | 3 | 7 / 10 / 17 | 3 / 4 / 5 |
| 13 | 1 | 9 / 9 / 9 | 3 / 3 / 3 |
| 14 | 2 | 14 / 14 / 18 | 4 / 4 / 6 |
| 17 | 2 | 11 / 11 / 21 | 3 / 3 / 4 |
| 18 | 1 | 16 / 16 / 16 | 5 / 5 / 5 |
| 19 | 2 | 17 / 17 / 21 | 6 / 6 / 7 |
| 21 | 2 | 12 / 12 / 20 | 4 / 4 / 10 |
| 23 | 2 | 16 / 16 / 18 | 6 / 6 / 6 |
| 24 | 1 | 13 / 13 / 13 | 5 / 5 / 5 |
| 25 | 2 | 12 / 12 / 23 | 6 / 6 / 6 |
| 26 | 1 | 24 / 24 / 24 | 10 / 10 / 10 |
| 28 | 1 | 14 / 14 / 14 | 3 / 3 / 3 |
| 29 | 2 | 15 / 15 / 22 | 4 / 4 / 9 |
| 36 | 1 | 19 / 19 / 19 | 4 / 4 / 4 |
| 41 | 1 | 23 / 23 / 23 | 7 / 7 / 7 |

The medians rise with level but the spread is wide (e.g. level 3: gameday 5-13). **Recommended approach for U1/U3:** clone the talent list of a real coach of the same archetype whose level is closest to the target, rather than synthesizing a count from a formula. If a formula is required, use `gameday = clamp(7 + round(0.45 * level), 5, 24)` and `playsheet = clamp(1 + round(0.16 * level), 1, 10)` and treat it as a fallback.

### 5.3 `TALENT_TEMPLATES` shape

Names are the candidates for a new coach's lists, ordered by how often real coaches of that archetype use them (top 20 gameday / top 12 playsheet). The reader returns `First` for `OffensiveGuru`; the key below uses the readable name. `DevelopmentWizard` is thin (only 3 real coaches carry talent lists), so prefer cloning a DevelopmentWizard template coach for that archetype.

```js
export const TALENT_TEMPLATES = {
  OffensiveGuru: {
    gameday: ["Field General", "Dr. Dots", "Practician (QB)", "Cool Under Pressure", "Play Fake Magician", "Outside the Club", "Sure Hands", "Angry Runner", "Insider Trading", "Move the Chains", "Riot Shield", "Dashing Dots", "Trimmed Edges", "Happier Feet", "Interior Dominating", "Blitz Buster", "Stamina Stealer", "Trench Barons", "Ahead of the Chains", "People Movers"],
    playsheet: ["West Coast Playsheet", "Air Raid Gun Playsheet", "Screen Game Playsheet", "Shot Plays Playsheet", "Heavy Sets Playsheet", "Red Zone Passing Playsheet", "Shotgun 2-Back Playsheet", "Air Raid Pistol Playsheet", "Wide Slot Playsheet", "Wide Trips Playsheet", "Run N Shoot Gun Playsheet", "Wide Empty Playsheet"],
  },
  DefensiveGenius: {
    gameday: ["Sideline 2 Sideline", "Behind the Chains", "Inside Stuffed", "Practician (LB)", "Interior Havoc", "Sheddables", "Aerial Suppression", "Fool Me Never", "Mano a Mano", "No Fly Zone", "Practician (CB)", "Trench Goblins", "Mr. Dream", "Island Kings", "Hitamus Stickumus", "Cover 2 Guru", "Ground Guardians", "Bookend Busters", "Cover 4 Guru", "Linebacker Lens"],
    playsheet: ["4-3 Playsheet", "4-2-5 Playsheet", "Batch O' Blitzes Playsheet", "Run Stop Playsheet", "Nickel Mug Playsheet", "3-4 Playsheet", "Dime Playsheet", "Nickel Playsheet", "Dollar Playsheet", "Master of Disguise Playsheet", "3-3-5 Playsheet", "QB Contain Playsheet"],
  },
  DevelopmentWizard: {
    gameday: ["Practician (OL)", "Stamina Stealer", "Ahead of the Chains", "After School Tutor", "Hitamus Stickumus", "Insider Trading", "Outside the Club", "Big Back", "Fit Finder", "People Movers", "Cool Under Pressure", "Dr. Dots", "Trench Barons", "Shield Finder", "Dashing Dots", "Talent Evaluator", "Angry Runner", "Stim Shot", "Riot Shield", "Sure Hands"],
    playsheet: ["Heavy Sets Playsheet", "Gadget Playsheet", "Heavy I Playsheet", "Heavy Strong Playsheet", "West Coast Playsheet", "Run N Shoot Playsheet", "Wide Empty Playsheet", "Red Zone Passing Playsheet", "Screen Game Playsheet", "Triple Option Playsheet", "Pitch Perfect Playsheet", "QB Contain Playsheet"],
  },
};
```

Use the first N names for the target level (N from §5.2 or the template clone). The full name -> static-info mapping is in Appendix B; the writer resolves a name to a `TalentInfo` ref, creates/clones a `GamedayTalent`/`SeasonTalent`/`PlaysheetTalent` row, creates a 4-slot `TalentTier[]` row with the statuses for the chosen tier, and appends the ref to a packed `Talent[]` list.

### 5.4 Wardrobe loadout names

A coach's `CharacterVisuals.RawData` JSON holds `bodyType` plus 1-3 loadouts (2 in 121 of 125 records). Loadout types: `CoachOnField` (183) and `Head` (67); categories `CoachApparel`, `Head`, `GearOnly`; outfit types `FieldInclement` (56), `Invalid` (30), or omitted. Items observed by slot:

| Slot | Item assets observed |
|---|---|
| `LeftShoe` / `RightShoe` | `CoachWardrobe_AirJordanRetro11`, `...Retro11CapNGown`, `...Retro11CoolGray`, `...Retro11Diffused`, `...Retro12Playoffs`, `...Retro1LowSE`, `...Retro1LowSEBlack`, `...Retro1LowSECourt`, `...Retro1LowSEWhite`, `...Retro1MidWhite`, `CoachWardrobe_LeatherSneakerBlack`, `CoachWardrobe_NikeAirMax270`, `...NikeAirMax90Black`, `...NikeAirMax90White`, `...NikeAirZoomPegasus28`, `...NikeFreeMetcon`, `...NikeVomero18`, `...NikeVomero18Black`, `...NikeVomero18White`, `...NikeZoomInvincible3`, `...NikeZoomInvincible3White` |
| `OuterShirt` | `CoachWardrobe_CottonHoodie`, `...HoodedWindBreakerJacket`, `...Hoodie`, `...Hoodie_gray`, `...LongSleeve`, `...LongSleeveRolledUp`, `...LongSleeveRolledUp_black`, `...LongSleeveRolledUp_white`, `...LongSleeve_black`, `...NylonHoodieClosed`, `...Polo`, `...PuffJacket`, `...PufferJacketHoodie`, `...PullOverShortSleeveCotton`, `...PullOverWindBreakerJacket`, `...PulloverLongSleeve`, `...PulloverLongSleeveCotton`, `...PulloverSleeveless`, `...RainJacketHoodieOpen`, `...ShortSleeveTshirt`, `...TshirtHoodie`, `...WindBreakerShortSleeve`, `...Y27_CutOffHoodie`, `...Y27_ThinHoodie`, `...Y27_ThinHoodieRolledUp` |
| `OuterPants` | `CoachWardrobe_Grey_Pants`, `...JoggersY27`, `...JoggersY27RolledUp`, `...Joggers_black`, `...Khaki_Pants`, `...Pants`, `coachwardrobe_black_pants` |
| `HeadWear` | `CoachWardrobe_Hat`, `CoachWardrobe_Hat_None`, `Hat_None`, and per-coach `<Name>_hat_Item` |
| `EarWear` | `CoachWardrobe_Headset_2` and per-coach `<Name>_headset_Item` |
| `PlayCard` | `Coach_ClipBoard` |
| `PlusHead` | one of the 53 `coachhead_M_####_HS` assets (same set as `GenericHeadAssetName`) |

The CreateCoachM27 reference tool also lists `CoachWardrobe_Staff1_shoe`, `...Staff1_pants`, `...Staff1_shirt`, `...Staff2/4_*`, `CoachWardrobe_PuffJacket`, `...Hoodie`, `...ZipUp`, `...QuarterZip`, `...Sweater`, `...Joggers`, `...Polo`, `...Pants`, `Coach_ClipBoard` and the `CoachOnField` loadout type; these are usable alternatives. A created coach can clone a template coach's whole visual JSON and swap `PlusHead`/team colours.

Capacity for new sub-records (all empty slots are usable with `autoUnempty`): `CoachingTalents` 1000 (125 live), `Talent[]` 1000x200 (282 live lists), `GamedayTalent` 6720 (985), `SeasonTalent` 7680 (242), `PlaysheetTalent` 3840 (414), `TalentTier[]` 20160 (1750), `TalentTier` 65535 (7060), `CharacterVisuals` 5056 (3431).

---

## 6. Visuals: `CharacterVisuals.RawData`

| Question | Finding |
|---|---|
| Coach blob size | 540-1,482 bytes (125 coaches; 2 coaches have no visual: John Madden and the placeholder) |
| Encoding | **Plain JSON text**, e.g. `{"bodyType":3,"loadouts":[...]}`; first bytes are `7b 22 62 6f 64 79...` |
| Fields | `bodyType` (absent = Standard, 1 = Thin, 2 = Muscular, 3 = Heavy) and `loadouts[]` (`loadoutType`, `loadoutCategory`, `outfitType`, `loadoutElements[{slotType,itemAssetName}]`) |
| `Overflow` | null for every coach |
| Players | Player `CharacterVisuals.RawData` is **also plain JSON** in this save (3,235 of 3,237; the other 2 are non-string/null). Example: `loadoutType: PlayerOnField`, `loadoutCategory: GearOnly`. |
| Player vs coach shape | Same JSON schema, different `loadoutType`/`loadoutCategory` values. |

### Verdict on `pipeline/character_visuals.py`

**Not reusable for coaches (or this save's players).** The module decodes/encodes a zstd-compressed EA ISON document (dictionary `0x1823777C`) and reads a `skinTone` byte at a fixed offset; the M27 620 save stores `CharacterVisuals.RawData` as plain JSON with no zstd header, no ISON opcodes and no `skinTone` field. Parsing/encoding a coach blob is `JSON.parse`/`JSON.stringify`; the coach writer should not import `pipeline/character_visuals.py`. The file may be removed from the HOLD list for this plan (that is JR's/the orchestrator's call), but it is not a dependency of `staff.mjs`.

---

## 7. Copy-pasteable constants

### 7.1 Playbook maps (Mu name -> Madden enum member)

Mu's playbook vocabulary is exactly `<City> Offense` / `<City> Defense` for these 32 cities:

```js
export const CITY_TO_TEAM = {
  Arizona: "Cardinals", Atlanta: "Falcons", Baltimore: "Ravens", Buffalo: "Bills",
  Carolina: "Panthers", Chicago: "Bears", Cincinnati: "Bengals", Cleveland: "Browns",
  Dallas: "Cowboys", Denver: "Broncos", Detroit: "Lions", "Green Bay": "Packers",
  Houston: "Texans", Indianapolis: "Colts", Jacksonville: "Jaguars", "Kansas City": "Chiefs",
  "LA Chargers": "Chargers", "LA Rams": "Rams", "Las Vegas": "Raiders", Miami: "Dolphins",
  Minnesota: "Vikings", "NY Giants": "Giants", "NY Jets": "Jets", "New England": "Patriots",
  "New Orleans": "Saints", Philadelphia: "Eagles", Pittsburgh: "Steelers", "San Francisco": "FortyNiners",
  Seattle: "Seahawks", "Tampa Bay": "Buccaneers", Tennessee: "Titans", Washington: "Commanders",
};

export const PLAYBOOK_MAP = Object.fromEntries(
  Object.entries(CITY_TO_TEAM).flatMap(([city, team]) => [
    [`${city} Offense`, team],
    [`${city} Defense`, team],
  ]),
);
```

Generic fallbacks (if Mu ever emits them): `West Coast -> WestCoast`, `Power Run -> PowerRun`, `Balanced -> Balanced`, `Balanced Pass -> BalancedPass`, `Balanced Run -> BalancedRun`, `Run and Shoot -> RunandShoot`, `Run n Gun -> RunnGun`, `Pistol -> Pistol`.

### 7.2 Scheme map (Mu name -> Madden enum member)

```js
export const SCHEME_MAP = {
  "Wide Zone West Coast": "WestCoastZoneRun",
  "West Coast": "WestCoastPowerRun",   // closest of the three West Coast variants; JR to confirm
  Spread: "Spread",
  "3-4": "Base3_4",
  "4-3": "Base4_3",
};

// Mu names with no exact Madden scheme member. U1 should raise/report these, not guess.
export const SCHEME_UNMAPPED = ["Erhardt-Perkins"];
```

### 7.3 Enum -> static row maps (write `encodeRef(16384, row)`)

```js
export const OFF_PLAYBOOK_ROWS = {
  FortyNiners: 104461,
  Bears: 102775,
  Bengals: 104440,
  Bills: 104438,
  Broncos: 104443,
  Browns: 104441,
  Buccaneers: 104463,
  Cardinals: 104435,
  Chargers: 104450,
  Chiefs: 104449,
  Colts: 104447,
  Commanders: 104434,
  Cowboys: 104442,
  Dolphins: 104452,
  Eagles: 104459,
  Falcons: 104436,
  Giants: 104456,
  Jaguars: 104448,
  Jets: 104457,
  Lions: 104444,
  Packers: 104445,
  Panthers: 104439,
  Patriots: 104454,
  Raiders: 104458,
  Rams: 104451,
  Ravens: 104437,
  Saints: 104455,
  Seahawks: 104462,
  Steelers: 104460,
  Texans: 104446,
  Titans: 104464,
  Vikings: 104453,
  BalancedPass: 104466,
  BalancedRun: 104467,
  Balanced: 104465,
  Pistol: 104471,
  PowerRun: 104468,
  RunandShoot: 104472,
  RunnGun: 104470,
  WestCoast: 104469,
};

export const DEF_PLAYBOOK_ROWS = {
  FortyNiners: 104494,
  Bears: 102773,
  Bengals: 104473,
  Bills: 104504,
  Broncos: 104476,
  Browns: 104474,
  Buccaneers: 104496,
  Cardinals: 104433,
  Chargers: 104483,
  Chiefs: 104482,
  Colts: 104480,
  Commanders: 104498,
  Cowboys: 104475,
  Dolphins: 104485,
  Eagles: 104492,
  Falcons: 104506,
  Giants: 104489,
  Jaguars: 104481,
  Jets: 104490,
  Lions: 104477,
  Packers: 104478,
  Panthers: 104505,
  Patriots: 104487,
  Raiders: 104491,
  Rams: 104484,
  Ravens: 104507,
  Saints: 104488,
  Seahawks: 104495,
  Steelers: 104493,
  Texans: 104479,
  Titans: 104497,
  Vikings: 104486,
  ThreeFour: 104500,
  FourThree: 104499,
  FourSix: 104501,
  Cover2: 104502,
  MultipleD: 104503,
};

// Scheme rows are type 16384; the head-coach 16485 rows are team-specific and are listed in §3.10.
export const SCHEME_ROWS = {
  WestCoastZoneRun: 104574,
  WestCoastPowerRun: 104579,
  VerticalZoneRun: 104575,
  MultipleZoneRun: 104577,
  MultiplePowerRun: 104576,
  VerticalPowerRun: 104578,
  Spread: 104572,
  RunAndShoot: 104580,
  AirRaid: 122541,
  Pistol: 122509,
  WestCoastSpread: 122479,
  Base4_3: 104581,
  Under4_3: 104582,
  Base3_4: 104584,
  Under3_4: 104583,
  Tampa2: 104586,
  Quarters4_3: 122543,
  Disguise3_4: 122544,
  Storm3_4: 122542,
  Cover3_4_3: 122649,
  Defense_46: 104585,
};
```

The tables these rows index are static (table 16384/16485), so the writer can write the reference without opening the table. Cross-check: every team's `Team.TEAM_OFFPLAYBOOK` and `Team.TEAM_DEFPLAYBOOK` equals its `TeamIndex`. **93 of the 96 team staff carry their own team's playbook rows**; three coordinators are exceptions (Declan Doyle, BAL -> Bears; Drew Petzing, DET -> Cardinals; Owen Scott, ATL -> Browns). A created coach should get the target team's rows, not the seat holder's old rows. The `M26JohnMadden` members (OffensivePlaybook 195, DefensivePlaybook 194) are intentionally omitted: the tool's `AssetId` for them is not in the `0x80000000 + static_row` space, and Mu does not need them.

### 7.4 Archetype / body / face / head constants

```js
export const ARCHETYPES = ["OffensiveGuru","DefensiveGenius","PersonnelCzar","DevelopmentWizard",
  "HeadScout","HeadTrainer","MasterMotivator","MasterMotivator_JohnMadden","SharedCoach"];
// value 0 reads back as "First"; treat "First" as OffensiveGuru.

export const BODY_TYPES = ["Standard","Thin","Muscular","Heavy","Freshman",
  "ReservedMain_1","ReservedMain_2","ReservedMain_3","Standard_Alternate","Thin_Alternate"];
// used by real coaches: Standard, Muscular, Thin, Heavy only

export const FACE_SHAPES_NAMED = ["BelichickBill","harbaughJohn","MaddenJohn","mcCarthyMike",
  "McVaySean","QuinnDan","ReidAndy","ShanahanKyle","patriotsgeneric"];
export const FACE_SHAPE_FALLBACKS = ["Invalid_", "MustBeUnique"];

export const GENERIC_HEAD_ASSETS = ["coachhead_M_0001_HS", "coachhead_M_0002_HS", "coachhead_M_0003_HS", "coachhead_M_0008_HS", "coachhead_M_0009_HS", "coachhead_M_0011_HS", "coachhead_M_0013_HS", "coachhead_M_0014_HS", "coachhead_M_0015_HS", "coachhead_M_0016_HS", "coachhead_M_0017_HS", "coachhead_M_0018_HS", "coachhead_M_0019_HS", "coachhead_M_0020_HS", "coachhead_M_0046_HS", "coachhead_M_0055_HS", "coachhead_M_0056_HS", "coachhead_M_0057_HS", "coachhead_M_0058_HS", "coachhead_M_0059_HS", "coachhead_M_0060_HS", "coachhead_M_0061_HS", "coachhead_M_0062_HS", "coachhead_M_0063_HS", "coachhead_M_0064_HS", "coachhead_M_0067_HS", "coachhead_M_0068_HS", "coachhead_M_0069_HS", "coachhead_M_0070_HS", "coachhead_M_0072_HS", "coachhead_M_0074_HS", "coachhead_M_0075_HS", "coachhead_M_0077_HS", "coachhead_M_0080_HS", "coachhead_M_0083_HS", "coachhead_M_0084_HS", "coachhead_M_0085_HS", "coachhead_M_0086_HS", "coachhead_M_0087_HS", "coachhead_M_0089_HS", "coachhead_M_0091_HS", "coachhead_M_0092_HS", "coachhead_M_0093_HS", "coachhead_M_0094_HS", "coachhead_M_0095_HS", "coachhead_M_0096_HS", "coachhead_M_0122_HS", "coachhead_M_0125_HS", "coachhead_M_0131_HS", "coachhead_M_0146_HS", "coachhead_M_0161_HS", "coachhead_M_0182_HS", "coachhead_M_0188_HS"];

export const COACH_PORTRAIT_RANGE = { start: 5324, end: 5484 };   // 161 free IDs, see §4.2
export const COACH_PORTRAIT_FALLBACKS = [[5486,5581],[5655,5789],[5921,6029],[6150,6298],[6729,6868],[6870,6978],[7564,7711]];
```

### 7.5 Uniform write defaults

```js
export const COACH_DEFAULTS = {
  Portrait_Force_Silhouette: false,
  Portrait_Swappable_Library_Path: "library_icons_brt",
  Weight: 10,
  IsCreated: false, IsImported: false, IsLegend: false, IsMaxLevel: false,
  Probation: false, TraitExpertScout: false,
  OffenseAudibles: null, DefenseAudibles: null, HasTrait: null,
  OWNER_COMMENTID: 0, OWNER_COMMENTTYPE: 0, CurrentPurchasedTalentCosts: 0,
};
```

---

## 8. Answers to the plan's risks

| Risk | Answer |
|---|---|
| **R3: is `CoachesRoot.CoachesArrays` the free-agent pool?** | **No.** The save has no `CoachesRoot` table, and Spike B's measured in-game firing diff shows **no array changes membership**. The free-agent pool is simply `Coach.TeamIndex = 32`; a release is five field writes plus a null seat ref, **no array membership at all**. Spike A did find a `Coach[]` array at `DataSource[1].Data` (table 5868, 100 slots, 29 entries) that mirrors the current free agents, but it is a static generator/seed pool — do not write it. |
| **R4: do coach portraits use the player library, are there free IDs?** | **Yes, one shared space and one bank** (`library_icons_brt`); 29 IDs are used by both coaches and players. There are large free runs inside the texture range; `5324-5484` (161 IDs) has no coach or player reference. Whether the bank contains art at those IDs is not provable from the save; the mod pack supplies the DDS. |
| **R6: how well do Mu's scheme and playbook names map?** | Playbooks map perfectly: Mu's 64 `<City> Offense/Defense` strings map 1:1 to the 32 team enum members. Schemes: 5 of Mu's 6 names map cleanly (Wide Zone West Coast -> WestCoastZoneRun, West Coast -> WestCoastPowerRun, Spread -> Spread, 3-4 -> Base3_4, 4-3 -> Base4_3); **Erhardt-Perkins has no Madden scheme member** and needs a JR decision. |
| **R8: is CreateCoachM27 readable?** | **Yes**, statically. It is a pkg-bundled Node app; the embedded table data is plain JSON (`AssetId`, `Value`, `Binary`, `Source` records) and was extracted without executing it. It yielded the playbook/scheme static-row ids, the 103 talent names and their static refs, the 4-tier cost/effect tables, and the wardrobe asset names. It does **not** contain the 10 legend playbooks (BillWalsh ... MikeDitka). It carries one legacy `M26JohnMadden` row per side, but its `AssetId` is not in the `0x80000000 + static_row` space, so no static row is mapped for it (and Mu does not need it). |

---

## 9. Open questions / what an in-game spike must confirm

1. **Retired coach shape** — no `ContractStatus = Retired` exists in CAREER-MUBASE, and `Franchise.RetiredCoaches` is empty. Spike B did not fire a coach into retirement. Spike C must show whether retire writes `TeamIndex`/`ContractYearsRemaining`/`COACH_RATING` and whether `Franchise.RetiredCoaches` expects a membership.
2. **The `DataSource[1].Data` generator pool** — Spike B proved Madden does not maintain it as a free-agent registry (no membership change on a firing). Its actual role is still unidentified (it sits under `GeneratorRootData`). Leave it untouched; Spike C can confirm that a created coach with `TeamIndex=32` shows up in the free-agent list without touching it.
3. **Head-coach scheme refs (16485)** — confirm a team HC may be re-pointed to the outgoing HC's 16485 pair (or that a 16384 ref also works). Also confirm a released HC's 16485 refs do not break when he sits in the free-agent array.
4. **Silhouette path** — no coach exercises `Portrait = 0, Portrait_Force_Silhouette = true`. Spike C should load one and confirm the silhouette (plan D5).
5. **Portrait bank contents** — `COACH_PORTRAIT_RANGE` is free of record references, not proven to be present in `library_icons_brt`. Spike D / `/reverse-mod` should check the bank's ID range or let the portrait pack add the DDS.
6. **Talent cloning** — the writer must clone `CoachingTalents`, `Talent[]`, `GamedayTalent`/`SeasonTalent`/`PlaysheetTalent` and `TalentTier[]`/`TalentTier` rows. Spike C should confirm a created coach with a cloned talent list loads and that the tier statuses render; this is the biggest sub-record risk (R1).
7. **16485 / 16384 scheme rows for coordinators vs head coaches** — confirm the game does not rebuild a coordinator's 16384 scheme or reject a head coach's 16384 ref.
8. **`GenericHeadAssetName` vs `FaceShape` pairing** — no invented coach in this save pairs a `coachhead_M_*_HS` head with a `coachhead_N_*` face preset; the writer should probably pair a head asset with `FaceShape = Invalid_` (as the 83 generic coaches do) or clone a template coach's pair.

---

## Appendix A: every live Coach record (127 rows)

Table `Coach` (id 4168). Columns: row, FirstName, LastName, Name, Age, TeamIndex, PrevTeamIndex, Position, OriginalPosition, ContractStatus, Archetype (reader value; `First` = OffensiveGuru), Level, ExperiencePoints, YearsCoaching, PresentationId, Portrait, IsUserControlled, CharacterBodyType, Height, Weight, FaceShape, GenericHeadAssetName, AssetName, OffPlaybook, DefPlaybook, OffScheme, DefScheme, SimOff, SimDef, Personality, Demeanor, Specialty, Stance, Backstory. The constant columns `Portrait_Force_Silhouette = false` and `Portrait_Swappable_Library_Path = library_icons_brt` are omitted (uniform for all 127).

```tsv
row	FirstName	LastName	Name	Age	TeamIndex	PrevTeamIndex	Position	OriginalPosition	ContractStatus	Archetype	Level	ExperiencePoints	YearsCoaching	PresentationId	Portrait	IsUserControlled	CharacterBodyType	Height	Weight	FaceShape	GenericHeadAssetName	AssetName	OffPlaybook	DefPlaybook	OffScheme	DefScheme	SimOff	SimDef	Personality	Demeanor	Specialty	Stance	Backstory
0	Klayton	Adams	K. Adams	49	10	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	3	7119	23	150	521	false	Muscular	72	10	Invalid_	AdamsKlayton_C_PRO		Cowboys	Cowboys	VerticalZoneRun	Under4_3	16483:10349	-	Unpredictable	Classic	Quarterbacks	Classic	Strategist
1	Ian	Alexander	I. Alexander	34	1	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	4	9158	1	14	397	false	Standard	74	10	Invalid_	coachhead_M_0089_HS		Bengals	Bengals	MultipleZoneRun	Under4_3	-	16483:10327	Unpredictable	Classic	Secondary	Classic	Motivator
2	Dennis	Allen	D. Allen	53	0	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	17	34194	6	102	522	false	Standard	69	10	MustBeUnique	AllenDennis_C_PRO		Bears	Bears	WestCoastSpread	Base3_4	-	16483:10315	Unpredictable	Classic	Secondary	Classic	TeamBuilder
3	Lou	Anarumo	L. Anarumo	59	9	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	8	17142	7	133	523	false	Standard	74	10	Invalid_	AnarumoLou_C_PRO		Colts	Colts	WestCoastSpread	Tampa2	-	-	Unpredictable	Classic	Linebackers	Classic	Motivator
4	Anthony	Campanile	A. Campanile	50	16	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	2	4697	19	267	569	false	Standard	70	10	Invalid_	coachhead_M_0094_HS		Jaguars	Jaguars	WestCoastZoneRun	Disguise3_4	-	16483:10315	Unpredictable	Classic	DefensiveLine	Classic	TeamBuilder
5	Rikki	Anderson	R. Anderson	41	3	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	3	7160	4	563	365	false	Standard	72	10	Invalid_	coachhead_M_0057_HS		Broncos	Broncos	WestCoastZoneRun	Under3_4	16483:10353	-	Unpredictable	Classic	OffensiveLine	Classic	Strategist
6	Mike	Rutenberg	M. Rutenberg	45	4	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	4	9048	4	263	558	false	Standard	70	10	Invalid_	coachhead_M_0122_HS		Browns	Browns	WestCoastZoneRun	Base4_3	-	16483:10327	Unpredictable	Classic	Secondary	Classic	Strategist
7	Patrick	Bailey	P. Bailey	42	24	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	4	9080	11	59	385	false	Standard	76	10	Invalid_	coachhead_M_0077_HS		Ravens	Ravens	WestCoastPowerRun	Disguise3_4	-	16483:10315	Unpredictable	Classic	Linebackers	Classic	Strategist
8	Pete	Carmichael Jr	P. Carmichael Jr	38	2	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	5	11121	7	262	568	false	Standard	69	10	Invalid_	coachhead_M_0188_HS		Bills	Bills	VerticalPowerRun	Quarters4_3	16483:10346	-	Unpredictable	Classic	Receivers	Classic	Strategist
9	Red	Beakman	R. Beakman	52	6	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	0	1094	21	109	394	false	Standard	72	10	Invalid_	coachhead_M_0086_HS		Cardinals	Cardinals	WestCoastSpread	Base3_4	16483:10353	-	Unpredictable	Classic	Quarterbacks	Classic	TeamBuilder
10	Todd	Bowles	T. Bowles	62	5	0	HeadCoach	HeadCoach	Signed	DefensiveGenius	17	34885	9	86	5	false	Heavy	74	10	MustBeUnique	MustBeUnique	BowlesTodd_C_PRO	Buccaneers	Buccaneers	WestCoastZoneRun	Base3_4	16483:10353	16483:10315	Leader	Calm	Linebackers	PlaySheet	Motivator
11	Gus	Bradley	G. Bradley	59	29	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	2	4658	2	242	524	false	Standard	73	10	Invalid_	BradleyGus_C_PRO		Titans	Titans	WestCoastZoneRun	Under3_4	-	16483:10315	Unpredictable	Classic	Linebackers	Classic	Motivator
12	Joe	Brady	J. Brady	36	2	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	3	6594	0	97	52	false	Thin	75	10	MustBeUnique	MustBeUnique	BradyJoe_C_PRO	Bills	Bills	WestCoastZoneRun	Tampa2	16483:10349	16483:10327	Unpredictable	Calm	Quarterbacks	PlaySheet	Strategist
13	Chris	O'Leary	C. O'Leary	36	7	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	5	11116	4	259	565	false	Standard	71	10	Invalid_	coachhead_M_0146_HS		Chargers	Chargers	VerticalPowerRun	Under3_4	-	16483:10315	Unpredictable	Classic	Linebackers	Classic	Strategist
14	Matt	Burke	M. Burke	50	31	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	3	7170	10	203	525	false	Standard	73	10	MustBeUnique	BurkeMatt_C_PRO		Texans	Texans	MultipleZoneRun	Disguise3_4	-	16483:10315	Unpredictable	Classic	DefensiveLine	Classic	Motivator
15	Nick	Caley	N. Caley	43	31	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	12	25067	9	220	526	false	Standard	72	10	MustBeUnique	CaleyNick_C_PRO		Texans	Texans	MultipleZoneRun	Disguise3_4	16483:10349	-	Unpredictable	Classic	RunningBacks	Classic	Strategist
16	Dan	Campbell	D. Campbell	50	18	0	HeadCoach	HeadCoach	Signed	DevelopmentWizard	21	42686	6	374	18	false	Muscular	71	10	MustBeUnique	MustBeUnique	CampbellDan_C_PRO	Lions	Lions	MultiplePowerRun	Base4_3	16483:10352	16483:10315	Unpredictable	Intense	OffensiveLine	HandsOnKnees	Strategist
17	Dave	Canales	D. Canales	45	20	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	3	7375	2	397	20	true	Thin	73	10	MustBeUnique	MustBeUnique	CanalesDave_C_PRO	Panthers	Panthers	WestCoastZoneRun	Tampa2	16483:10353	16483:10327	Unpredictable	Calm	Quarterbacks	PlaySheet	Motivator
18	Liam	Coen	L. Coen	40	16	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	3	6338	1	751	33	false	Standard	75	10	MustBeUnique	MustBeUnique	CoenLiam_C_PRO	Jaguars	Jaguars	WestCoastZoneRun	Base4_3	16483:10353	16483:10327	Unpredictable	Classic	Quarterbacks	PlaySheet	Strategist
19	Jim Bob	Cooter	J. Cooter	41	9	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	4	9026	3	141	527	false	Muscular	75	10	Invalid_	CooterBob_C_PRO		Colts	Colts	WestCoastSpread	Tampa2	16483:10353	-	Unpredictable	Classic	Receivers	Classic	TeamBuilder
20	Brian	Daboll	B. Daboll	51	29	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	14	29067	1	222	15	false	Heavy	71	10	MustBeUnique	DabollBrian_C_PRO		Titans	Titans	WestCoastZoneRun	Under3_4	16483:10344	-	Unpredictable	Classic	OffensiveLine	Classic	Strategist
21	Deuce	Douglas	D. Douglas	47	8	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	5	11156	16	569	375	false	Standard	70	10	Invalid_	coachhead_M_0067_HS		Chiefs	Chiefs	WestCoastSpread	Base4_3	16483:10353	-	Unpredictable	Classic	RunningBacks	Classic	Motivator
22	Declan	Doyle 	D. Doyle 	29	24	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	0	1087	21	190	528	false	Standard	72	10	MustBeUnique	DoyleDeclan_C_PRO		Bears	Bears	WestCoastSpread	Base3_4	16483:10353	-	Unpredictable	Classic	Quarterbacks	Classic	TeamBuilder
23	Sean	Mannion	S. Mannion	40	12	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	4	8951	11	266	562	false	Standard	72	10	Invalid_	coachhead_M_0094_HS		Eagles	Eagles	WestCoastSpread	Base4_3	16483:10353	-	Unpredictable	Classic	OffensiveLine	Classic	Motivator
24	Sammy	Rios	S. Rios	51	32	0	DefensiveCoordinator	DefensiveCoordinator	FreeAgent	DefensiveGenius	3	6120	0	182	403	false	Standard	70	10	Invalid_	coachhead_M_0095_HS		FortyNiners	Bills	WestCoastZoneRun	Quarters4_3	-	-	Unpredictable	Classic	DefensiveLine	Classic	Strategist
25	Scott	Anderson	S. Anderson	56	32	0	DefensiveCoordinator	DefensiveCoordinator	FreeAgent	DefensiveGenius	5	10750	0	183	399	false	Standard	72	10	Invalid_	coachhead_M_0091_HS		Buccaneers	Rams	Spread	Under3_4	-	-	Unpredictable	Classic	Linebackers	Classic	Motivator
26	Jackson	Pierce	J. Pierce	43	32	0	DefensiveCoordinator	DefensiveCoordinator	FreeAgent	DefensiveGenius	3	6780	0	184	404	false	Standard	72	10	Invalid_	coachhead_M_0096_HS		Raiders	Dolphins	WestCoastPowerRun	Storm3_4	-	-	Unpredictable	Classic	Secondary	Classic	Strategist
27	Brandon	Starks	B. Starks	42	32	0	HeadCoach	HeadCoach	FreeAgent	OffensiveGuru	5	10280	0	244	544	false	Standard	72	10	Invalid_	coachhead_M_0002_HS		Buccaneers	Buccaneers	Spread	Tampa2	-	-	Unpredictable	Classic	Quarterbacks	Classic	Motivator
28	Thomas	Peters	T. Peters	45	32	0	HeadCoach	HeadCoach	FreeAgent	OffensiveGuru	2	5200	0	243	543	false	Standard	72	10	Invalid_	coachhead_M_0001_HS		Falcons	Falcons	VerticalPowerRun	Base3_4	-	-	Unpredictable	Classic	Linebackers	Classic	Strategist
29	Emanuel	Ortega	E. Ortega	48	32	0	HeadCoach	HeadCoach	FreeAgent	DefensiveGenius	10	21450	0	181	545	false	Standard	70	10	Invalid_	coachhead_M_0003_HS		Eagles	Eagles	VerticalZoneRun	Under4_3	-	-	Unpredictable	Classic	OffensiveLine	Classic	Strategist
30	Jamie	Rodriguez	J. Rodriguez	38	32	0	OffensiveCoordinator	OffensiveCoordinator	FreeAgent	OffensiveGuru	4	8172	0	185	400	false	Standard	74	10	Invalid_	coachhead_M_0092_HS		Raiders	Seahawks	WestCoastPowerRun	Cover3_4_3	-	-	Unpredictable	Classic	Quarterbacks	Classic	Motivator
31	Bryan	Warner	B. Warner	49	32	0	OffensiveCoordinator	OffensiveCoordinator	FreeAgent	OffensiveGuru	2	4250	0	186	402	false	Standard	72	10	Invalid_	coachhead_M_0094_HS		Panthers	Steelers	MultipleZoneRun	Disguise3_4	-	-	Unpredictable	Classic	Quarterbacks	Classic	Strategist
32	Nick	Gregory	N. Gregory	39	32	0	OffensiveCoordinator	OffensiveCoordinator	FreeAgent	OffensiveGuru	5	11240	0	187	401	false	Standard	70	10	Invalid_	coachhead_M_0093_HS		Lions	Colts	VerticalZoneRun	Tampa2	-	-	Unpredictable	Classic	Receivers	Classic	TeamBuilder
33	Brian	Flores	B. Flores	45	30	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	29	58167	9	227	529	false	Standard	75	10	Invalid_	FloresBrian_C_PRO		Vikings	Vikings	WestCoastZoneRun	Quarters4_3	-	16483:10327	Unpredictable	Classic	DefensiveLine	Classic	TeamBuilder
34	Anthony	Frazee	A. Frazee	56	5	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	0	1175	5	19	380	false	Standard	74	10	Invalid_	coachhead_M_0072_HS		Buccaneers	Buccaneers	VerticalZoneRun	Under3_4	-	16483:10315	Unpredictable	Classic	Secondary	Classic	Motivator
35	Peter	Gaffney	P. Gaffney	38	3	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	23	46960	1	21	3	false	Heavy	71	10	patriotsgeneric	patriotsGeneric	WrightJonathan_C_PRO	Broncos	Broncos	WestCoastPowerRun	Base3_4	16483:10346	16483:10315	Unpredictable	Classic	Secondary	HandsOnKnees	Strategist
36	Jonthan	Gannon	J. Gannon	43	19	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	13	27132	2	237	6	false	Standard	72	10	Invalid_	GannonJonathan_C_PRO		Packers	Packers	WestCoastZoneRun	Base3_4	-	16483:10315	Unpredictable	Classic	DefensiveLine	Classic	Strategist
37	Aaron	Glenn	A. Glenn	53	17	0	HeadCoach	HeadCoach	Signed	DefensiveGenius	6	12544	1	750	34	false	Standard	70	10	MustBeUnique	MustBeUnique	GlennAaron_C_PRO	Jets	Jets	MultiplePowerRun	Base4_3	16483:10353	16483:10327	Unpredictable	Classic	Secondary	Classic	Strategist
38	Orson	Grizzly	O. Grizzly	52	0	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	0	1117	21	107	393	false	Standard	72	10	Invalid_	coachhead_M_0085_HS		Bears	Bears	WestCoastSpread	Base3_4	16483:10353	-	Unpredictable	Classic	Quarterbacks	Classic	TeamBuilder
39	Jeff	Hafley	J. Hafley	47	11	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	3	6380	0	101	45	false	Standard	75	10	MustBeUnique	MustBeUnique	HafleyJeff_C_PRO	Dolphins	Dolphins	WestCoastPowerRun	Tampa2	16483:10341	16483:10327	Unpredictable	Classic	Quarterbacks	Classic	Strategist
40	Dan	Hampton	D. Hampton	44	11	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	2	4596	5	572	366	false	Standard	70	10	Invalid_	coachhead_M_0058_HS		Dolphins	Dolphins	MultipleZoneRun	Storm3_4	16483:10348	-	Unpredictable	Classic	Receivers	Classic	TeamBuilder
41	Jim	Harbaugh	J. Harbaugh	62	7	0	HeadCoach	HeadCoach	Signed	DevelopmentWizard	25	50389	6	20	7	false	Muscular	72	10	MustBeUnique	MustBeUnique	HarbaughJim_C_PRO	Chargers	Chargers	MultipleZoneRun	Base3_4	16483:10346	16483:10315	Unpredictable	Intense	Quarterbacks	HandsOnKnees	Motivator
42	John	Harbaugh	J. Harbaugh	63	15	0	HeadCoach	HeadCoach	Signed	DevelopmentWizard	36	72875	18	92	24	false	Muscular	72	10	harbaughJohn	harbaughJohn	HarbaughJohn_C_PRO	Giants	Giants	MultiplePowerRun	Base4_3	16483:10341	16483:10315	Unpredictable	Classic	Secondary	HandsOnKnees	TeamBuilder
43	Seth	Henry	S. Henry	35	7	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	4	8997	5	567	370	false	Standard	71	10	Invalid_	coachhead_M_0062_HS		Chargers	Chargers	VerticalPowerRun	Cover3_4_3	16483:10346	-	Unpredictable	Classic	RunningBacks	Classic	Strategist
44	Dash	Hightower	D. Hightower	52	4	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	0	1075	21	108	392	false	Standard	72	10	Invalid_	coachhead_M_0084_HS		Browns	Browns	WestCoastSpread	Base3_4	16483:10353	-	Unpredictable	Classic	Quarterbacks	Classic	TeamBuilder
45	Bradley	Idzik	B. Idzik	34	20	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	3	7123	24	154	530	false	Standard	75	10	MustBeUnique	IdzikBrad_C_PRO		Panthers	Panthers	MultipleZoneRun	Under4_3	16483:10344	-	Unpredictable	Classic	Quarterbacks	Classic	Strategist
46	Patrick	Graham	P. Graham	36	28	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	5	11191	0	275	557	false	Standard	77	10	Invalid_	coachhead_M_0060_HS		Steelers	Steelers	MultiplePowerRun	Base3_4	-	16483:10315	Unpredictable	Classic	Linebackers	Classic	TeamBuilder
47	Aden	Durde	A. Durde	37	27	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	3	7242	6	274	564	false	Standard	74	10	Invalid_	coachhead_M_0067_HS		Seahawks	Seahawks	WestCoastZoneRun	Cover3_4_3	-	16483:10327	Unpredictable	Classic	Linebackers	Classic	Motivator
48	Ben	Johnson	B. Johnson	40	0	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	9	18333	1	749	35	false	Standard	73	10	MustBeUnique	MustBeUnique	JohnsonBen_C_PRO	Bears	Bears	MultiplePowerRun	Base4_3	16483:10353	16483:10327	Unpredictable	Classic	Receivers	PlaySheet	TeamBuilder
49	Vance	Joseph	V. Joseph	53	3	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	4	9114	11	239	531	false	Standard	74	10	Invalid_	josephVance_C_PRO		Broncos	Broncos	WestCoastZoneRun	Under3_4	-	16483:10315	Unpredictable	Classic	Secondary	Classic	Motivator
50	Brian	Angelicho	B. Angelicho	43	28	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	3	7071	9	276	556	false	Standard	74	10	Invalid_	coachhead_M_0011_HS		Steelers	Steelers	MultiplePowerRun	Base3_4	16483:10346	-	Unpredictable	Classic	Receivers	Classic	Motivator
51	Klint 	Kubiak	K. Kubiak	39	22	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	3	6377	0	100	46	false	Thin	75	10	MustBeUnique	MustBeUnique	KubiakKlint_C_PRO	Raiders	Raiders	MultipleZoneRun	Base4_3	16483:10341	16483:10327	Unpredictable	Classic	Quarterbacks	PlaySheet	Strategist
52	Matt	LaFleur	M. LaFleur	46	19	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	23	46695	7	132	19	false	Thin	72	10	MustBeUnique	MustBeUnique	LaFleurMatt_C_PRO	Packers	Packers	WestCoastZoneRun	Tampa2	16483:10353	16483:10327	Unpredictable	Intense	Quarterbacks	PlaySheet	Strategist
53	Mike	LaFleur	M. LaFleur	39	6	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	3	6396	0	61	47	false	Standard	75	10	MustBeUnique	MustBeUnique	LafleurMike_C_PRO	Cardinals	Cardinals	WestCoastZoneRun	Tampa2	16483:10341	16483:10327	Unpredictable	Classic	Quarterbacks	PlaySheet	Strategist
54	Kris	Leisman	K. Leisman	41	19	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	3	7063	6	579	369	false	Standard	71	10	Invalid_	coachhead_M_0061_HS		Packers	Packers	WestCoastZoneRun	Base3_4	16483:10353	-	Unpredictable	Classic	Receivers	Classic	TeamBuilder
55	Mike	Macdonald	M. Macdonald	39	27	0	HeadCoach	HeadCoach	Signed	DefensiveGenius	6	13383	2	743	27	false	Muscular	72	10	MustBeUnique	MustBeUnique	MacdonaldMike_C_PRO	Seahawks	Seahawks	MultipleZoneRun	Base3_4	16483:10344	16483:10315	Unpredictable	Classic	Secondary	PlaySheet	Motivator
56	John	Madden	J. Madden	45	0	22	HeadCoach	HeadCoach	FreeAgent	DevelopmentWizard	0	0	0	362	44	false	Standard	72	10	MaddenJohn		MaddenJohn_C_PRO	-	-	-	-	-	-	Unpredictable	Classic	Linebackers	Classic	Motivator
57	Mike	McCarthy	M. McCarthy	62	28	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	28	57154	17	5	48	false	Heavy	72	10	mcCarthyMike	mcCarthyMike	McCarthyMike_C_PRO	Steelers	Steelers	WestCoastPowerRun	Base3_4	-	-	Unpredictable	Classic	Quarterbacks	PlaySheet	Strategist
58	Josh	McDaniels	J. McDaniels	49	21	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	21	43096	10	179	510	false	Standard	72	10	Invalid_	McDanielsJosh_C_PRO		Patriots	Patriots	MultiplePowerRun	Under3_4	16483:10346	-	Unpredictable	Classic	Quarterbacks	Classic	Strategist
59	Kevin	McPherron	K. McPherron	47	14	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	1	2622	15	559	377	false	Standard	72	10	Invalid_	coachhead_M_0069_HS		FortyNiners	FortyNiners	WestCoastZoneRun	Cover3_4_3	16483:10353	-	Unpredictable	Classic	Quarterbacks	Classic	Strategist
60	Sean	McVay	S. McVay	40	23	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	29	59087	9	378	23	false	Thin	70	10	McVaySean	McVaySean	McVaySean_C_PRO	Rams	Rams	WestCoastZoneRun	Base3_4	16483:10353	16483:10327	Unpredictable	Intense	Quarterbacks	PlaySheet	Strategist
61	Dallas	Menard	D. Menard	36	14	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	3	7146	8	11	395	false	Standard	74	10	Invalid_	coachhead_M_0087_HS		FortyNiners	FortyNiners	WestCoastZoneRun	Cover3_4_3	-	16483:10327	Unpredictable	Classic	Linebackers	Classic	Motivator
62	Steven	Meyer	S. Meyer	47	17	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	0	1182	16	40	383	false	Standard	70	10	Invalid_	coachhead_M_0075_HS		Jets	Jets	MultipleZoneRun	Under3_4	-	16483:10315	Unpredictable	Classic	DefensiveLine	Classic	Strategist
63	Jesse	Minter	J. Minter	43	24	0	HeadCoach	HeadCoach	Signed	DefensiveGenius	3	6368	0	78	49	false	Standard	75	10	MustBeUnique	MustBeUnique	MinterJesse_C_PRO	Ravens	Ravens	MultiplePowerRun	Base3_4	16483:10349	16483:10327	Unpredictable	Classic	Quarterbacks	PlaySheet	Strategist
64	Todd	Monken	T. Monken	60	4	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	3	6343	0	99	50	false	Thin	75	10	MustBeUnique	MustBeUnique	MonkenTodd_C_PRO	Browns	Browns	VerticalPowerRun	Base4_3	16483:10341	16483:10327	Unpredictable	Calm	Quarterbacks	PlaySheet	Strategist
65	Kellen	Moore	K. Moore	37	26	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	4	8320	1	748	26	false	Standard	69	10	MustBeUnique	MustBeUnique	MooreKellen_C_PRO	Saints	Saints	WestCoastZoneRun	Base3_4	16483:10353	16483:10315	Unpredictable	Calm	Quarterbacks	PlaySheet	Strategist
66	Zak	Kuhr	Z. Kuhr	47	21	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	1	2751	18	269	563	false	Standard	71	10	Invalid_	coachhead_M_0161_HS		Patriots	Patriots	MultiplePowerRun	Storm3_4	-	16483:10315	Unpredictable	Classic	Secondary	Classic	Strategist
67	Rodravian	Murphy	R. Murphy	49	25	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	5	11078	13	591	376	false	Standard	76	10	Invalid_	coachhead_M_0068_HS		Commanders	Commanders	VerticalPowerRun	Base4_3	16483:10350	-	Unpredictable	Classic	Receivers	Classic	TeamBuilder
68	Adam	Murray	A. Murray	52	25	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	2	4713	12	131	394	false	Standard	74	10	Invalid_	coachhead_M_0086_HS		Commanders	Commanders	VerticalPowerRun	Base4_3	-	16483:10327	Unpredictable	Classic	Linebackers	Classic	Motivator
69	Matt	Nagy	M. Nagy	48	15	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	3	7078	10	252	532	false	Standard	74	10	Invalid_	nagyMatt_C_PRO		Giants	Giants	VerticalZoneRun	Under3_4	16483:10349	-	Unpredictable	Classic	RunningBacks	Classic	Motivator
70	Jim	Nichols	J. Nichols	50	13	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	3	7146	23	35	392	false	Standard	72	10	Invalid_	coachhead_M_0084_HS		Falcons	Falcons	WestCoastZoneRun	Cover3_4_3	-	16483:10327	Unpredictable	Classic	Linebackers	Classic	Motivator
71	Doug	Nussmeier	D. Nussmeier	55	26	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	2	4560	15	199	511	false	Standard	73	10	MustBeUnique	NussmeierDoug_C_PRO		Saints	Saints	MultiplePowerRun	Base4_3	16483:10346	-	Unpredictable	Classic	Receivers	Classic	Strategist
72	Kevin	O'Connell	K. O'Connell	41	30	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	19	38888	4	95	31	false	Thin	70	10	MustBeUnique	MustBeUnique	OConnellKevin_C_PRO	Vikings	Vikings	WestCoastZoneRun	Base3_4	16483:10353	16483:10315	Unpredictable	Classic	Quarterbacks	PlaySheet	Strategist
73	Nate	Scheelhaase	N. Scheelhaase	34	23	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	2	4651	2	272	559	false	Standard	77	10	Invalid_	coachhead_M_0046_HS		Rams	Rams	WestCoastZoneRun	Base3_4	16483:10353	-	Unpredictable	Classic	Quarterbacks	Classic	Motivator
74	Jim	Leonhard	J. Leonhard	41	2	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	2	4749	6	261	566	false	Standard	72	10	Invalid_	coachhead_M_0009_HS		Bills	Bills	VerticalPowerRun	Quarters4_3	-	16483:10327	Unpredictable	Classic	Secondary	Classic	Strategist
75	Zach	Parker	Z. Parker	45	17	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	3	7122	7	577	367	false	Standard	68	10	Invalid_	coachhead_M_0059_HS		Jets	Jets	MultipleZoneRun	Under3_4	16483:10344	-	Unpredictable	Classic	Receivers	Classic	Strategist
76	Grant	Udinski	G. Udinski	33	16	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	3	7097	6	268	561	false	Standard	74	10	Invalid_	coachhead_M_0125_HS		Jaguars	Jaguars	WestCoastZoneRun	Quarters4_3	16483:10353	-	Unpredictable	Classic	Quarterbacks	Classic	TeamBuilder
77	Drew	Petzing	D. Petzing	39	18	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	3	7144	16	249	512	false	Standard	72	10	Invalid_	PetzingDrew_C_PRO		Cardinals	Cardinals	AirRaid	Base3_4	16483:10341	-	Unpredictable	Classic	Quarterbacks	Classic	Strategist
78	Wes	Phillips	W. Phillips	47	30	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	2	4576	10	229	513	false	Standard	72	10	MustBeUnique	PhillipsWes_C_PRO		Vikings	Vikings	WestCoastZoneRun	Quarters4_3	16483:10353	-	Unpredictable	Classic	Quarterbacks	Classic	Motivator
79	Dan	Pitcher	D. Pitcher	39	1	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	2	4591	11	104	514	false	Standard	75	10	MustBeUnique	PitcherDan_C_PRO		Bengals	Bengals	MultipleZoneRun	Under4_3	16483:10344	-	Unpredictable	Classic	Quarterbacks	Classic	Strategist
80	Dan	Quinn	D. Quinn	55	25	0	HeadCoach	HeadCoach	Signed	DefensiveGenius	14	28791	8	304	25	false	Muscular	72	10	QuinnDan	QuinnDan	QuinnDan_C_PRO	Commanders	Commanders	WestCoastZoneRun	Base4_3	16483:10341	16483:10327	Unpredictable	Intense	DefensiveLine	HandsOnKnees	Strategist
81	Nick	Rallis	N. Rallis	32	6	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	2	4715	12	111	515	false	Standard	72	10	Invalid_	RallisNick_C_PRO		Cardinals	Cardinals	AirRaid	Base3_4	-	16483:10315	Unpredictable	Classic	Secondary	Classic	Strategist
82	Andy	Reid	A. Reid	68	8	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	41	82629	27	12	8	false	Heavy	76	10	ReidAndy	ReidAndy	ReidAndy	Chiefs	Chiefs	WestCoastZoneRun	Base4_3	16483:10353	16483:10327	Unpredictable	Classic	Quarterbacks	PlaySheet	TeamBuilder
83	Zac	Robinson	Z. Robinson	39	5	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	3	7129	18	246	516	false	Standard	74	10	Invalid_	RobinsonZac_C_PRO		Buccaneers	Buccaneers	WestCoastZoneRun	Cover3_4_3	16483:10353	-	Unpredictable	Classic	OffensiveLine	Classic	Strategist
84	DeMeco	Ryans	D. Ryans	42	31	0	HeadCoach	HeadCoach	Signed	DefensiveGenius	12	25226	3	394	29	false	Muscular	72	10	MustBeUnique	MustBeUnique	RyansDeMeco_C_PRO	Texans	Texans	WestCoastZoneRun	Base4_3	16483:10347	16483:10327	Unpredictable	Intense	Linebackers	PlaySheet	TeamBuilder
85	Robert	Saleh	R. Saleh	45	29	0	HeadCoach	HeadCoach	Signed	DefensiveGenius	2	4322	3	368	51	false	Muscular	70	10	MustBeUnique	MustBeUnique	SalehRobert_C_PRO	Titans	Titans	WestCoastPowerRun	Base4_3	-	-	Unpredictable	Intense	DefensiveLine	PlaySheet	Strategist
86	Brian	Schottenheimer	B. Schottenheimer	52	10	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	3	7125	1	747	36	false	Standard	72	10	MustBeUnique	MustBeUnique	SchottenheimerBrian_C_PRO	Cowboys	Cowboys	WestCoastPowerRun	Base4_3	16483:10349	16483:10327	Unpredictable	Classic	Quarterbacks	PlaySheet	Strategist
87	Owen	Scott	O. Scott	42	13	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	3	7107	8	564	378	false	Standard	73	10	Invalid_	coachhead_M_0070_HS		Browns	Browns	WestCoastZoneRun	Base4_3	16483:10353	-	Unpredictable	Classic	Quarterbacks	Classic	Motivator
88	Kyle	Shanahan	K. Shanahan	46	14	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	26	53024	9	558	14	false	Standard	72	10	ShanahanKyle	ShanahanKyle	ShanahanKyle_C_PRO	FortyNiners	FortyNiners	MultipleZoneRun	Base4_3	16483:10353	16483:10327	Unpredictable	Classic	RunningBacks	PlaySheet	Strategist
89	Kelvin	Sheppard	K. Sheppard	38	18	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	10	21207	7	152	517	false	Muscular	71	10	Invalid_	SheppardKelvin_C_PRO		Lions	Lions	MultipleZoneRun	Under3_4	-	16483:10315	Unpredictable	Classic	Secondary	Classic	Motivator
90	Chris	Shula	C. Shula	40	23	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	9	19199	20	188	518	false	Standard	71	10	Invalid_	ShulaChris_C_PRO		Rams	Rams	WestCoastZoneRun	Base3_4	-	16483:10315	Unpredictable	Classic	DefensiveLine	Classic	Strategist
91	Nick	Sirianni	N. Sirianni	45	12	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	19	38927	5	361	12	false	Standard	75	10	MustBeUnique	MustBeUnique	SirianniNick_C_PRO	Eagles	Eagles	MultiplePowerRun	Base3_4	16483:10353	16483:10315	Unpredictable	Intense	Quarterbacks	Classic	Strategist
92	Steve	Spagnuolo	S. Spagnuolo	66	8	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	25	50226	13	129	519	false	Thin	73	10	Invalid_	SpagnuoloSteve_C_PRO		Chiefs	Chiefs	WestCoastSpread	Base4_3	-	16483:10327	Unpredictable	Classic	DefensiveLine	Classic	Strategist
93	Brandon	Staley	B. Staley	43	26	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	12	25157	9	192	520	false	Standard	70	10	MustBeUnique	StaleyBrandon_C_PRO		Saints	Saints	MultiplePowerRun	Base4_3	-	16483:10327	Unpredictable	Classic	Secondary	Classic	Motivator
94	Andrew	Janocko	A. Janocko	40	22	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	0	1078	6	271	567	false	Standard	71	10	Invalid_	coachhead_M_0182_HS		Raiders	Raiders	WestCoastPowerRun	Under4_3	16483:10346	-	Unpredictable	Classic	Receivers	Classic	TeamBuilder
95	Kevin	Stefanski	K. Stefanski	44	13	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	18	37034	6	7	4	false	Standard	75	10	MustBeUnique	MustBeUnique	StefanskiKevin_C_PRO	Falcons	Falcons	MultiplePowerRun	Base4_3	16483:10349	16483:10327	Unpredictable	Intense	Quarterbacks	Classic	Strategist
96	Shane	Steichen	S. Steichen	41	9	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	8	17238	3	393	9	false	Thin	77	10	MustBeUnique	MustBeUnique	SteichenShane_C_PRO	Colts	Colts	WestCoastPowerRun	Tampa2	16483:10353	16483:10327	Unpredictable	Intense	Quarterbacks	PlaySheet	Motivator
97	Bill	Summers	B. Summers	45	12	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	6	13214	5	28	391	false	Standard	76	10	Invalid_	coachhead_M_0083_HS		Eagles	Eagles	WestCoastSpread	Base4_3	-	16483:10327	Unpredictable	Classic	Linebackers	Classic	Strategist
98	Zac	Taylor	Z. Taylor	42	1	0	HeadCoach	HeadCoach	Signed	OffensiveGuru	24	49256	7	3	1	false	Standard	74	10	MustBeUnique	MustBeUnique	TaylorZac_C_PRO	Bengals	Bengals	WestCoastPowerRun	Base4_3	16483:10353	16483:10327	Unpredictable	Classic	Quarterbacks	PlaySheet	Strategist
99	Mario	Uria	M. Uria	42	11	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	3	7167	7	27	382	false	Standard	71	10	Invalid_	coachhead_M_0074_HS		Dolphins	Dolphins	MultipleZoneRun	Storm3_4	-	16483:10315	Unpredictable	Classic	Secondary	Classic	TeamBuilder
100				0	0	0	HeadCoach	HeadCoach	FreeAgent	DevelopmentWizard	0	0	0	0	7999	false	Standard	0	10	BelichickBill			-	-	-	-	-	-	Unpredictable	Classic	Quarterbacks	Classic	Motivator
101	Mike	Vrabel	M. Vrabel	50	21	0	HeadCoach	HeadCoach	Signed	DefensiveGenius	11	23810	7	9	38	false	Muscular	76	10	MustBeUnique	MustBeUnique	VrabelMike_C_PRO	Patriots	Patriots	MultiplePowerRun	Base3_4	16483:10353	16483:10315	Unpredictable	Intense	Linebackers	Classic	Motivator
102	Rob	Leonard	R. Leonard	44	22	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	2	4690	10	270	560	false	Standard	74	10	Invalid_	coachhead_M_0131_HS		Raiders	Raiders	WestCoastPowerRun	Under4_3	-	16483:10327	Unpredictable	Classic	Linebackers	Classic	Motivator
103	Brendan	Washington	B. Washington	35	10	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	4	9129	5	26	388	false	Standard	72	10	Invalid_	coachhead_M_0080_HS		Cowboys	Cowboys	VerticalZoneRun	Under4_3	-	16483:10327	Unpredictable	Classic	Linebackers	Classic	TeamBuilder
104	Mack	Williams	M. Williams	37	27	0	OffensiveCoordinator	OffensiveCoordinator	Signed	OffensiveGuru	2	4606	3	586	364	false	Standard	72	10	Invalid_	coachhead_M_0056_HS		Seahawks	Seahawks	WestCoastZoneRun	Cover3_4_3	16483:10350	-	Unpredictable	Classic	Quarterbacks	Classic	Motivator
105	Dennard	Wilson	D Wilson	44	15	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	2	4705	2	257	533	false	Standard	75	10	Invalid_	wilsonDennard_C_PRO		Giants	Giants	VerticalZoneRun	Under3_4	-	16483:10315	Unpredictable	Classic	DefensiveLine	Classic	Strategist
106	Darren	Young	D. Young	37	20	0	DefensiveCoordinator	DefensiveCoordinator	Signed	DefensiveGenius	1	2714	9	55	393	false	Standard	71	10	Invalid_	coachhead_M_0085_HS		Panthers	Panthers	MultipleZoneRun	Under4_3	-	16483:10327	Unpredictable	Classic	Secondary	Classic	Strategist
107	Russel	Cornwell	R. Cornwell	43	32	0	DefensiveCoordinator	DefensiveCoordinator	FreeAgent	DefensiveGenius	0	0	0	465	363	false	Standard	72	10	Invalid_	coachhead_M_0055_HS		Buccaneers	Buccaneers	MultiplePowerRun	Disguise3_4	-	-	Unpredictable	Classic	Secondary	Classic	TeamBuilder
108	Edward	Mclaughlin	E. Mclaughlin	45	32	0	HeadCoach	HeadCoach	FreeAgent	DefensiveGenius	4	0	0	468	364	false	Standard	72	10	Invalid_	coachhead_M_0056_HS		Panthers	Panthers	MultipleZoneRun	Quarters4_3	-	-	Unpredictable	Classic	DefensiveLine	Classic	TeamBuilder
109	Willie	Ogletree	W. Ogletree	42	32	0	OffensiveCoordinator	OffensiveCoordinator	FreeAgent	OffensiveGuru	0	0	0	466	365	false	Standard	72	10	Invalid_	coachhead_M_0057_HS		Rams	Rams	MultipleZoneRun	Base3_4	-	-	Unpredictable	Classic	Linebackers	Classic	Strategist
110	Norman	Staton	N. Staton	46	32	0	HeadCoach	HeadCoach	FreeAgent	DefensiveGenius	6	0	0	469	366	false	Standard	72	10	Invalid_	coachhead_M_0058_HS		Titans	Titans	MultipleZoneRun	Under3_4	-	-	Unpredictable	Classic	Secondary	Classic	TeamBuilder
111	Jonathan	Vergara	J. Vergara	36	32	0	OffensiveCoordinator	OffensiveCoordinator	FreeAgent	OffensiveGuru	3	0	0	467	367	false	Standard	72	10	Invalid_	coachhead_M_0059_HS		FortyNiners	FortyNiners	VerticalZoneRun	Base4_3	-	-	Unpredictable	Classic	Linebackers	Classic	Strategist
112	Jason	Collins	J. Collins	34	32	0	DefensiveCoordinator	DefensiveCoordinator	FreeAgent	DefensiveGenius	0	0	0	661	323	false	Standard	72	10	Invalid_	coachhead_M_0015_HS		Cardinals	FortyNiners	AirRaid	Under4_3	-	-	Unpredictable	Classic	Secondary	Classic	Strategist
113	Charles	Phillips	C. Phillips	43	32	0	OffensiveCoordinator	OffensiveCoordinator	FreeAgent	OffensiveGuru	0	0	0	660	322	false	Standard	72	10	Invalid_	coachhead_M_0014_HS		Titans	FourSix	MultipleZoneRun	Defense_46	-	-	Unpredictable	Classic	Linebackers	Classic	TeamBuilder
114	Robert	Guzman	R. Guzman	43	32	0	OffensiveCoordinator	OffensiveCoordinator	FreeAgent	OffensiveGuru	0	0	0	657	550	false	Standard	72	10	Invalid_	coachhead_M_0008_HS		Bills	Vikings	VerticalPowerRun	Quarters4_3	-	-	Unpredictable	Classic	OffensiveLine	Classic	TeamBuilder
115	Duane	Morris	D. Morris	41	32	0	DefensiveCoordinator	DefensiveCoordinator	FreeAgent	DefensiveGenius	0	0	0	659	321	false	Standard	72	10	Invalid_	coachhead_M_0013_HS		Bengals	Rams	MultipleZoneRun	Under3_4	-	-	Unpredictable	Classic	OffensiveLine	Classic	Strategist
116	Kelly	Stephens	K. Stephens	37	32	0	OffensiveCoordinator	OffensiveCoordinator	FreeAgent	OffensiveGuru	0	0	0	658	551	false	Standard	72	10	Invalid_	coachhead_M_0009_HS		Panthers	Cowboys	Spread	Under4_3	-	-	Unpredictable	Classic	Quarterbacks	Classic	TeamBuilder
117	William	Bruno	W. Bruno	48	32	0	DefensiveCoordinator	DefensiveCoordinator	FreeAgent	DefensiveGenius	6	0	0	473	368	false	Standard	72	10	Invalid_	coachhead_M_0060_HS		Bills	Bills	WestCoastZoneRun	Base4_3	-	-	Unpredictable	Classic	DefensiveLine	Classic	Motivator
118	Arthur	Cowans	A. Cowans	38	32	0	HeadCoach	HeadCoach	FreeAgent	DefensiveGenius	0	0	0	471	369	false	Standard	72	10	Invalid_	coachhead_M_0061_HS		Cardinals	Cardinals	MultiplePowerRun	Base3_4	-	-	Unpredictable	Classic	Secondary	Classic	Strategist
119	Ronald	Orozco	R. Orozco	36	32	0	OffensiveCoordinator	OffensiveCoordinator	FreeAgent	OffensiveGuru	1	0	0	472	370	false	Standard	72	10	Invalid_	coachhead_M_0062_HS		Rams	Rams	MultipleZoneRun	Disguise3_4	-	-	Unpredictable	Classic	Secondary	Classic	Motivator
120	Craig	Patterson	C. Patterson	47	32	0	HeadCoach	HeadCoach	FreeAgent	DefensiveGenius	0	0	0	474	371	false	Standard	72	10	Invalid_	coachhead_M_0063_HS		Ravens	Ravens	RunAndShoot	Under3_4	-	-	Unpredictable	Classic	DefensiveLine	Classic	Motivator
121	Louis	Rainer	L. Rainer	39	32	0	OffensiveCoordinator	OffensiveCoordinator	FreeAgent	OffensiveGuru	3	0	0	470	372	false	Standard	72	10	Invalid_	coachhead_M_0064_HS		Saints	Saints	RunAndShoot	Base4_3	-	-	Unpredictable	Classic	DefensiveLine	Classic	TeamBuilder
122	Christian	Hall	C. Hall	40	32	0	DefensiveCoordinator	DefensiveCoordinator	FreeAgent	DefensiveGenius	0	0	0	664	326	false	Standard	72	10	Invalid_	coachhead_M_0018_HS		Falcons	Jaguars	WestCoastPowerRun	Base4_3	-	-	Unpredictable	Classic	Linebackers	Classic	Motivator
123	Justin	Henderson	J. Henderson	36	32	0	OffensiveCoordinator	OffensiveCoordinator	FreeAgent	OffensiveGuru	0	0	0	666	328	false	Standard	72	10	Invalid_	coachhead_M_0020_HS		Vikings	Bills	WestCoastZoneRun	Quarters4_3	-	-	Unpredictable	Classic	Secondary	Classic	TeamBuilder
124	Bobby	Moore	B. Moore	49	32	0	DefensiveCoordinator	DefensiveCoordinator	FreeAgent	DefensiveGenius	0	0	0	665	327	false	Standard	72	10	Invalid_	coachhead_M_0019_HS		Colts	Lions	MultiplePowerRun	Disguise3_4	-	-	Unpredictable	Classic	DefensiveLine	Classic	Motivator
125	Louis	Ramirez	L. Ramirez	35	32	0	OffensiveCoordinator	OffensiveCoordinator	FreeAgent	OffensiveGuru	0	0	0	663	325	false	Standard	72	10	Invalid_	coachhead_M_0017_HS		Seahawks	Titans	VerticalPowerRun	Storm3_4	-	-	Unpredictable	Classic	OffensiveLine	Classic	Strategist
126	Kevin	Thompson	K. Thompson	52	32	0	OffensiveCoordinator	OffensiveCoordinator	FreeAgent	OffensiveGuru	0	0	0	662	324	false	Standard	72	10	Invalid_	coachhead_M_0016_HS		FortyNiners	Seahawks	WestCoastZoneRun	Cover3_4_3	-	-	Unpredictable	Classic	Quarterbacks	Classic	Motivator
```

## Appendix B: talent catalog (103 names used in CAREER-MUBASE)

`ref` is the static `TalentInfo` row (write it with `encodeRef(table, row)`). `source` is the CreateCoachM27 record type; `G`/`P` mark which list the talent appears in.

```tsv
name	ref	source	category	logic	lists
3-3-5 Playsheet	16474:47117	PlaysheetTalentInfo	Defensive	PlaysheetPassDefense	P
3-4 Playsheet	16474:47111	PlaysheetTalentInfo	Defensive	PlaysheetRunDefense	P
4-2-5 Playsheet	16474:47123	PlaysheetTalentInfo	Defensive	PlaysheetPassDefense	P
4-3 Playsheet	16474:47105	PlaysheetTalentInfo	Defensive	PlaysheetRunDefense	P
Aerial Suppression	16469:53046	GamedayTalentInfo	Defensive	GamedayPassDefense	G
After School Tutor	16474:66053	SeasonTalentInfo	PlayerDevelopment	PlayerDevelopment	G
Ahead of the Chains	16469:52968	GamedayTalentInfo	Offensive	GamedayRunOffense	G
Air Raid Gun Playsheet	16474:47069	PlaysheetTalentInfo	Offensive	PlaysheetPassOffense	P
Air Raid Pistol Playsheet	16474:47063	PlaysheetTalentInfo	Offensive	PlaysheetPassOffense	P
Always Watching	16469:53064	GamedayTalentInfo	Defensive	GamedayPassDefense	G
Angry Runner	16469:52920	GamedayTalentInfo	Offensive	GamedayPassOffense	G
Batch O' Blitzes Playsheet	16474:47022	PlaysheetTalentInfo	Defensive	PlaysheetPassDefense	P
Behind the Chains	16469:53094	GamedayTalentInfo	Defensive	GamedayRunDefense	G
Big Back	16469:52986	GamedayTalentInfo	Offensive	GamedayRunOffense	G
Blitz Buster	16469:52932	GamedayTalentInfo	Offensive	GamedayPassOffense	G
Bookend Busters	16469:53010	GamedayTalentInfo	Defensive	GamedayPassDefense	G
Cool Under Pressure	16469:52902	GamedayTalentInfo	Offensive	GamedayPassOffense	G
Corner Czar	16474:66102	SeasonTalentInfo	Scouting	PlayerDevelopment	G
Cover 2 Guru	16469:53052	GamedayTalentInfo	Defensive	GamedayPassDefense	G
Cover 4 Guru	16469:53058	GamedayTalentInfo	Defensive	GamedayPassDefense	G
Dashing Dots	16469:52938	GamedayTalentInfo	Offensive	GamedayPassOffense	G
Dime Playsheet	16474:47158	PlaysheetTalentInfo	Defensive	PlaysheetPassDefense	P
Dollar Playsheet	16474:47163	PlaysheetTalentInfo	Defensive	PlaysheetPassDefense	P
Dr. Dots	16469:52914	GamedayTalentInfo	Offensive	GamedayPassOffense	G
Eagle Eye	16474:66087	SeasonTalentInfo	Scouting	PlayerDevelopment	G
Edge Focus	16474:66094	SeasonTalentInfo	Scouting	PlayerDevelopment	G
Edge Wizards	16470:56945	GamedayTalentInfo	Defensive	GamedayPassDefense	G
Field General	16458:35154	GamedayTalentInfo	Offensive	GamedayPassOffense	G
Fit Finder	16474:66085	SeasonTalentInfo	Scouting	PlayerDevelopment	G
Fool Me Never	16469:53028	GamedayTalentInfo	Defensive	GamedayPassDefense	G
Gadget Playsheet	16476:54203	PlaysheetTalentInfo	Offensive	PlaysheetPassOffense	P
Ground Guardians	16470:57028	GamedayTalentInfo	Defensive	GamedayRunDefense	G
Happier Feet	16469:52926	GamedayTalentInfo	Offensive	GamedayPassOffense	G
Head of the Class	16474:66055	SeasonTalentInfo	Staff	Staff	G
Heavy I Playsheet	16476:54215	PlaysheetTalentInfo	Offensive	PlaysheetRunOffense	P
Heavy Sets Playsheet	16474:47151	PlaysheetTalentInfo	Offensive	PlaysheetRunOffense	P
Heavy Strong Playsheet	16476:54221	PlaysheetTalentInfo	Offensive	PlaysheetRunOffense	P
Hitamus Stickumus	16469:53112	GamedayTalentInfo	Defensive	GamedayRunDefense	G
Inside Scoop	16474:66093	SeasonTalentInfo	Scouting	PlayerDevelopment	G
Inside Stuffed	16469:53070	GamedayTalentInfo	Defensive	GamedayRunDefense	G
Insider Trading	16469:52944	GamedayTalentInfo	Offensive	GamedayRunOffense	G
Interior Dominating	16469:52896	GamedayTalentInfo	Offensive	GamedayPassOffense	G
Interior Havoc	16469:53016	GamedayTalentInfo	Defensive	GamedayPassDefense	G
Island Kings	16470:57065	GamedayTalentInfo	Defensive	GamedayPassDefense	G
Linebacker Lens	16474:66096	SeasonTalentInfo	Scouting	PlayerDevelopment	G
Load the Box	16470:57089	GamedayTalentInfo	Defensive	GamedayRunDefense	G
Mano a Mano	16469:53040	GamedayTalentInfo	Defensive	GamedayPassDefense	G
Master of Disguise Playsheet	16474:47087	PlaysheetTalentInfo	Defensive	PlaysheetPassDefense	P
Move the Chains	16458:35196	GamedayTalentInfo	Offensive	GamedayPassOffense	G
Mr. Dream	16469:53004	GamedayTalentInfo	Defensive	GamedayPassDefense	G
Nickel Mug Playsheet	16474:47135	PlaysheetTalentInfo	Defensive	PlaysheetPassDefense	P
Nickel Playsheet	16474:47129	PlaysheetTalentInfo	Defensive	PlaysheetPassDefense	P
No Fly Zone	16469:53034	GamedayTalentInfo	Defensive	GamedayPassDefense	G
Outside the Club	16469:52950	GamedayTalentInfo	Offensive	GamedayRunOffense	G
Passing Paranoia	16469:53022	GamedayTalentInfo	Defensive	GamedayPassDefense	G
People Movers	16458:35258	GamedayTalentInfo	Offensive	GamedayRunOffense	G
Pitch Perfect Playsheet	16474:47057	PlaysheetTalentInfo	Offensive	PlaysheetRunOffense	P
Play Fake Magician	16469:52908	GamedayTalentInfo	Offensive	GamedayPassOffense	G
Practician (CB)	16458:35341	SeasonTalentInfo	PlayerDevelopment	PlayerDevelopment	G
Practician (DL)	16458:35317	SeasonTalentInfo	PlayerDevelopment	PlayerDevelopment	G
Practician (HB)	16458:35280	SeasonTalentInfo	PlayerDevelopment	PlayerDevelopment	G
Practician (LB)	16458:35329	SeasonTalentInfo	PlayerDevelopment	PlayerDevelopment	G
Practician (OL)	16458:35299	SeasonTalentInfo	PlayerDevelopment	PlayerDevelopment	G
Practician (QB)	16458:35274	SeasonTalentInfo	PlayerDevelopment	PlayerDevelopment	G
Practician (S)	16458:35347	SeasonTalentInfo	PlayerDevelopment	PlayerDevelopment	G
Practician (WR/TE)	16458:35286	SeasonTalentInfo	PlayerDevelopment	PlayerDevelopment	G
Pressure Posse	16470:56981	GamedayTalentInfo	Defensive	GamedayPassDefense	G
QB Contain Playsheet	16476:63825	PlaysheetTalentInfo	Defensive	PlaysheetPassDefense	P
QB Runs Playsheet	16476:54233	PlaysheetTalentInfo	Offensive	PlaysheetRunOffense	P
RB Reconnaissance	16474:66089	SeasonTalentInfo	Scouting	PlayerDevelopment	G
Read Blocktion	16469:52956	GamedayTalentInfo	Offensive	GamedayRunOffense	G
Receiver Radar	16474:66090	SeasonTalentInfo	Scouting	PlayerDevelopment	G
Red Zone Passing Playsheet	16476:54209	PlaysheetTalentInfo	Offensive	PlaysheetPassOffense	P
Riot Shield	16458:35246	GamedayTalentInfo	Offensive	GamedayPassOffense	G
RPOh No	16469:53082	GamedayTalentInfo	Defensive	GamedayRunDefense	G
Run N Shoot Gun Playsheet	16474:47051	PlaysheetTalentInfo	Offensive	PlaysheetPassOffense	P
Run N Shoot Playsheet	16474:47039	PlaysheetTalentInfo	Offensive	PlaysheetPassOffense	P
Run Stop Playsheet	16474:47099	PlaysheetTalentInfo	Defensive	PlaysheetRunDefense	P
Safety First	16474:66103	SeasonTalentInfo	Scouting	PlayerDevelopment	G
Screen Game Playsheet	16476:54227	PlaysheetTalentInfo	Offensive	PlaysheetPassOffense	P
Sheddables	16469:53100	GamedayTalentInfo	Defensive	GamedayRunDefense	G
Shield Finder	16474:66092	SeasonTalentInfo	Scouting	PlayerDevelopment	G
Shot Plays Playsheet	16476:54239	PlaysheetTalentInfo	Offensive	PlaysheetPassOffense	P
Shotgun 2-Back Playsheet	16476:54245	PlaysheetTalentInfo	Offensive	PlaysheetPassOffense	P
Sideline 2 Sideline	16469:53076	GamedayTalentInfo	Defensive	GamedayRunDefense	G
Stamina Stealer	16469:52962	GamedayTalentInfo	Offensive	GamedayRunOffense	G
Stim Shot	16469:53088	GamedayTalentInfo	Defensive	GamedayRunDefense	G
Stud Finder	16474:66088	SeasonTalentInfo	Scouting	PlayerDevelopment	G
Sure Hands	16469:52884	GamedayTalentInfo	Offensive	GamedayPassOffense	G
Talent Evaluator	16474:66086	SeasonTalentInfo	Scouting	PlayerDevelopment	G
Tight End Tracker	16474:66091	SeasonTalentInfo	Scouting	PlayerDevelopment	G
Trench Barons	16469:52974	GamedayTalentInfo	Offensive	GamedayRunOffense	G
Trench Goblins	16470:56993	GamedayTalentInfo	Defensive	GamedayRunDefense	G
Trimmed Edges	16469:52890	GamedayTalentInfo	Offensive	GamedayPassOffense	G
Triple Option Playsheet	16474:47033	PlaysheetTalentInfo	Offensive	PlaysheetRunOffense	P
Two Minute Playsheet	16476:120992	PlaysheetTalentInfo	Offensive	PlaysheetPassOffense	P
West Coast Playsheet	16474:47010	PlaysheetTalentInfo	Offensive	PlaysheetPassOffense	P
Where's the Beef?	16474:66095	SeasonTalentInfo	Scouting	PlayerDevelopment	G
Wide Empty Playsheet	16474:47075	PlaysheetTalentInfo	Offensive	PlaysheetPassOffense	P
Wide Slot Playsheet	16474:47081	PlaysheetTalentInfo	Offensive	PlaysheetPassOffense	P
Wide Trips Playsheet	16474:47145	PlaysheetTalentInfo	Offensive	PlaysheetPassOffense	P
Wildcat Playsheet	16474:47045	PlaysheetTalentInfo	Offensive	PlaysheetRunOffense	P
Work Horse	16458:35166	GamedayTalentInfo	Offensive	GamedayRunOffense	G
```

Name -> ref can also be recovered at runtime by indexing every live `GamedayTalent` / `SeasonTalent` / `PlaysheetTalent` row's `TalentInfo`; the exe-derived table above is the static ground truth for names.
