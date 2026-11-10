# Project Mu: Coach Save Writes Plan

**Status:** built and on main (2026-11-10).

**As built:**
- **The auditor's second pass (M-0196) was right that the plan was not finished.** JR's B4 decision was not implemented: `plan_staff_edits` *skipped* an unlinked starting seat (the wanted coach had no profile row) instead of creating Mu's man and releasing Madden's. The link now builds a CREATED profile for any Mu coach holding a starting seat he does not occupy, and on CAREER-MUBASE against a fresh 2026 seed that is exactly 25 releases and 25 creates with nothing skipped (measured count: 71 of 96 seats link). **B1 was also only half fixed:** a coach Mu invented was created without contract fields and the planner treated a correctly seated coach as nothing to write, so his deal never reached the save. The reader now exports the Coach contract fields and the planner emits a contract-only assign when a CREATED coach's save deal differs from his live `staff_contracts` row, and nothing once it matches. Also from that pass: a lone head-coach release (the B8 class), `names_match` tightened to at most one edit, and the save's free-agent coaches linked by exact name so a poached coordinator is a move rather than a second person.
- **The auditor's review (M-0185, 2026-11-10) found three blockers my own testing could not see, all fixed afterwards.** **B1 (critical):** the writer refused any coach action without a `contract` block, and a hire has no `staff_contracts` row until `staff_contracts_run.annual` runs after a written FREE_AGENCY write — so every OFFSEASON_START / RESIGN / FREE_AGENCY write was refused, player moves included, and the step that files the contracts never ran. The contract block is now optional and a later catch-up write fills it (verified with a 16-edit write on a real-save copy carrying no contracts at all). Every one of my dry runs had supplied a contract by hand, which is exactly why this survived. **B2:** the playbook fallback read `Team.NickName`, which is slang for nine teams and not a map key, so a create on any of them refused the whole write; it reads `DisplayName` with one alias for San Francisco. **B3:** a poached coordinator promoted to head coach was created as a second person, so his real record was released and later retired (D2); his profile now moves onto the coach identity and keeps his `PresentationId`. Also fixed from the same review: B5 (`--mark-installed` shadowed the function), B6 (start level capped at 12 per D9), B7 (the face preset now carries the skin-tone digit the headshot reads), B8 (a head coach moved away with no replacement in the same write is refused), B9 (an assign writes the target team's 16485 scheme pair), B10 (only an INSTALLED portrait is pushed to the save), B11 (retire keeps `PrevTeamIndex`; the fired head-coach lookup takes the most recently freed free agent). **B4 is open and is JR's call:** 30 of the 96 starting seats don't link — some to spelling, the rest because Madden carries fictional coordinators with no Mu person.
- **U6 wires it in.** `pipeline/league_office.py` plans the `"staff"` block at `OFFSEASON_START` and at every later offseason stop (RESIGN, FREE_AGENCY, COMBINE, PRO_DAY, DRAFT, UDFA; CUTDOWN and PRESEASON are excluded because they are the new league year and the catch-up would write the wrong season), stores the write's `presentation_ids` back on `coach_madden_profiles` (and from the stored operation on `--recover`), and reports skipped seats as warnings. The `Mu: coaches fired` / `Mu: coaches hired` notices now say what the write will apply, and that text lives only in `pipeline/weekly_capture.step_coach_hot_seat` (the carousel's module docstring was still promising hand-mirroring; fixed here). `tools/coach_portrait_pack.py` allocates `COACH_PORTRAIT_RANGE` IDs, writes the manifest and moves GENERATED → PACKED, pushing `Portrait` through a new optional `set` on the writer's `assign`. The migration is applied to `madden_test` and `madden_mu2026` (137 applied, 0 drifted).
- **Spike C, first round (JR in game, 2026-11-10).** The created head coach is accepted: Tennessee's staff screen lists him, he is selectable, and nothing crashes — so the created record, the cloned talent list and the visuals clone are valid data. Three things were wrong or missing and are fixed in `8cf6d4e`: (1) `Portrait = 0` with `Portrait_Force_Silhouette = true` renders **no portrait at all**, so a coach without a face now carries the game's own placeholder (7999, the value both placeholder coach rows hold) with the flag off; (2) a created head coach had a blank `AssetName`, where all 32 real head coaches carry `<First><Last>_C_PRO` (the 94 without one are coordinators, whose model comes from `GenericHeadAssetName`) — a created head coach now gets that key; (3) the record is now marked `IsCreated = true`, which is the flag JR asked about for making a coach editable in game, and is how the 3D model can be inspected at all. The visuals read-back also compared blobs byte for byte and the library lowercases asset names on write, so a correct clone was reported as differing. **Spike C is passed** (JR, 2026-11-10): with every seat in the league filled, Whitaker shows his **3D model** (openable in game), his portrait is the **default silhouette** (so the `7999` placeholder draws exactly the pre-pack state D5 describes), and he and Glenn both survive advancing the clone to the resign week with their staffs. Still unmeasured, both low risk: whether a team head coach accepts a 16384 scheme reference (we write the 16485 pair for head coaches, so nothing depends on it today) and whether a `ContractStatus = Retired` coach stays out of the pool (D8's three-season rule only fires in a live multi-season run). JR also saw a "You've been fired" card on a clone that had **empty staff seats** (Madden's own firing had cleared them); the second clone fills every seat in the league so that can be told apart from a real regression.
- **U3 was validated on copies of real saves before Spike C, and that found four defects its own tests could not.** Its suite passed 43/43 synthetic tests while the write was broken in four ways: the playbook fallback keyed on `Team.ShortName` (`CAR`) instead of the nickname (`Panthers`), so any coach without an explicit playbook was refused; the read-back compared cleared references strictly against the library's `null`, so every `create` failed its own verification; the `FranchiseUser` fallback compared a decoded reference to an encoded string, so the orphaned-user path could never match and a head-coach assign onto a fired team was refused; and a created coach got Spike A's placeholder `Weight = 10`, which is outside the field's own schema range (int 150–512). All four are fixed (`ab5f33c`). Afterwards, on a copy of `CAREER-TUNECOACHAFTEROC` a `create` wrote a new OC on CAR (Coach row 127, `PresentationId 752` = `max+1`) and on `CAREER-TUNECOACHAFTERHC` an `assign` put Aaron Glenn back at NYJ with `IsUserControlled` set; `pc_save_reader` reads both back correctly and `league_setup.mjs state` reports no problems. Also noted: Madden's `Coach.Archetype` enum holds 0 twice (`First` and `OffensiveGuru`), and 68 of the 127 real coaches read back as `First`, so `coach_link` canonicalises that one alias.
- **U5 findings and follow-ups.** A linked `SAVE` row leaves playbook/scheme NULL because the snapshot carries raw static-table references, not enum names. A `CREATED` row is never relabelled `SAVE` by the link (that would make the headshot generator and portrait batch skip it) — only its `presentation_id` is recorded. Two gaps closed after U5 merged: `engines/fire_coordinator.py`'s `--replacement` path created a coordinator without a profile row, so the seat was skipped (fixed, `5ee7f40`), and `PLAYBOOK_MAP` carried only six hand-added nickname aliases, so carousel playbooks (`Panthers Offense`) mapped to nothing and an invented coach would have been created with no playbook (now every team's nickname form maps, `8398fd0`). The 13 `test_coaching_carousel_db.py` tests stay deselected (`-m "not integration"`): they connect through `pipeline.load_capture.DSN`, so running them means the live database.
**U1 (mapping settled by JR, 2026-11-10):** every Mu playbook maps (six were nickname aliases of the 64 `<City> Offense/Defense` strings). **Erhardt-Perkins** now maps to `MultiplePowerRun` — Madden has no EP member, and the save itself gives two of its four real EP coaches that scheme (Vrabel, McDaniels; Daboll is `WestCoastZoneRun`, Saleh `WestCoastPowerRun`). **West Coast** stays `WestCoastPowerRun` as built. U1 also found `staff_names.background` returns no age/years, so `build_profile` accepts a `{age, years}` mapping and otherwise seeds them; Madden's raw coach `Weight` is the constant 10 on all 127 records, so the profile stores 10 and a headshot build must come from `body_type`/height.
- **U2 exports the playbook and scheme fields as the save's stored 32-bit references**, not enum names: schema 620 types them as references into static tables 16384/16485, which are not in the save. That suits the writer (which sets references anyway) but means `coach_madden_profiles.offensive_playbook`'s VARCHAR names get no reverse map from the snapshot; U5 compares seats, not playbooks, so nothing needs one yet.
- **Spike A is in `docs/reference/MADDEN_COACH_RECORDS.md`** (`081670e`), measured on `CAREER-MUBASE`. It supplies every constant U1/U3 need: the 122-field Coach layout, the enum members actually in use, `PLAYBOOK_MAP`/`SCHEME_MAP` plus the enum→static-row tables, `TALENT_TEMPLATES` with the level scaling, the face/head/body lists, and `COACH_PORTRAIT_RANGE`. Three findings changed things: (1) **D11 is answered — `pipeline/character_visuals.py` is not reusable and leaves HOLD**, because `CharacterVisuals.RawData` is plain JSON (`bodyType` + `loadouts[]`) for coaches *and* players in schema 620, so the coach writer does JSON, not zstd/ISON; (2) **coach portraits share one numeric space and one texture bank with `PLYR_PORTRAIT`** (29 IDs are used by both), so `COACH_PORTRAIT_RANGE` is the free block 5324-5484 (161 IDs); (3) **team head coaches' scheme fields reference the static 16485 table, while coordinators and free agents use 16384** — whether a team HC accepts a 16384 ref is unconfirmed and joins Spike C's list.
- **Spike B changed two design assumptions** (`docs/reference/MADDEN_COACH_FIRING_DIFF.md`). JR turned Madden's firings on and advanced Week 18 → Wild Card on a clone; Madden fired three head coaches (NYJ, WAS, TEN) and their coordinators. Measured: (1) **there is no `CoachesRoot` free-agent array** — a released coach is just `TeamIndex = 32` + `ContractStatus = FreeAgent` + `ContractYearsRemaining = 0` + `PrevTeamIndex = old team`, so `release` has no array handling at all (S2 and §2.5 amended); (2) **a firing on a user team nulls the user wiring** — `Team.UserCharacter` → null and `FranchiseUser.Team` → null, while `FranchiseUser.UserEntity` still points at the fired man and `IsUserControlled` stays true. Mu wants a live user on every team, so a head-coach change is a release plus an assign in one write and `FranchiseUser.Team` must be restored. Also measured: firing an HC clears all three of his seats; `Position` is not changed by a release; Madden has a second status, `Expiring`; table 4168 has 470 rows with 127 live, so 343 empty rows are available for `create`; `COACH_RATING` is a season-scoped recomputation, not a release field; and Madden creates no coach record at the firing, which matches the OFFSEASON_START timing. A second pair, `CAREER-TUNECOACHBEFOREOC` → `CAREER-TUNECOACHAFTEROC` (JR firing CAR's OC Bradley Idzik in a fixed offseason frame), isolates the coordinator case: one seat ref nulled, one Coach record released, **`COACH_RATING` → 50** on release, `Position` untouched, and **nothing else at all** — no head-coach change, no user change, and 0 of 32 `FranchiseUser` rows touched. So the user-wiring repair is needed only when the seat being filled is a head-coach seat.
- Spike D (`df76bec`) found the source and the licence shape in `docs/reference/COACH_DONOR_PHOTOS.md`. Club coach photos on `static.clubs.nfl.com` are the only good plates (every club has them; ESPN's coach headshots are 404, Pro-Football-Reference is Cloudflare-403), but their terms are personal, non-commercial, no redistribution: **the donor library stays local and git-ignored**, and Wikimedia Commons is the clean-licence fallback. `static/coaches/` is served by the existing `/static` mount with nothing new (`server/api.py:188-190`).
- **"Painted into a plate" is compositing, not diffusion.** Spike D read only `headshots/local/backends.py` (no backend takes a base image or a mask) and concluded the plate path was missing; that was wrong (JR, 2026-11-10: the player pipeline works). The painting is `headshots/local/jersey_plate.py` — `extract_clean_jersey_plate` cuts a transparent neck socket out of a real photo and `composite_headshot_to_jersey` segments the generated portrait and pastes it into that socket (skin-mask chin detection, scale, collar bounds; `test_local_headshot.py:611-631`). Coach mode is therefore a *coach-plate* variant of the same two calls, not a new backend. U4 owns `headshots/local/jersey_plate.py` so it can generalise the socket geometry and fabric colours from a jersey collar to a coaching polo or quarter-zip.
- The migration is `db/migrations/2027_02_21_coach_madden_profiles.sql`, not the plan's original `2027_02_19`: builder-2 took `2027_02_19` for `2027_02_19_owner_cash_buyouts.sql` (`6e9fbcf`) and then `2027_02_20` for `2027_02_20_buyout_schedule.sql` (M-0128) before U1 started.
- The migration is `db/migrations/2027_02_21_coach_madden_profiles.sql`, not the plan's original `2027_02_19`: builder-2 took `2027_02_19` for `2027_02_19_owner_cash_buyouts.sql` (`6e9fbcf`) and then `2027_02_20` for `2027_02_20_buyout_schedule.sql` (M-0128) before U1 started.

**Builds on:** PROJECT_SPEC §2.19 (coaching carousel), §2.20 (headshots), §2.21a (league office), [`LEAGUE_OFFICE_PLAN.md`](../done/LEAGUE_OFFICE_PLAN.md) §4.3, [`V2_CORE_BUILD_PLAN.md`](../done/V2_CORE_BUILD_PLAN.md) (the "coach-creation spike" row), [`REAL_COACH_PROFILES_PLAN.md`](../done/REAL_COACH_PROFILES_PLAN.md), [`STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN.md`](../done/STAFF_CONTRACTS_AND_FULL_CONTROL_PLAN.md).

**Goal:** Project Mu decides every coaching change: Black Monday firings, coordinator reviews, the hiring cycle, poaching, Godfather coach-GMs and JR's own coordinator firings. Today none of it reaches the game. The league office makes all 32 teams user teams and turns Madden's coach firing off, so Madden never changes a coach on its own. This plan makes the league office write Mu's head coaches and coordinators into the save. A person who is already in the save keeps his own coach record and is moved. A coach Mu invents gets a new Madden coach record with a full set of fields: name, age, body, head, wardrobe, archetype, talents, playbooks, schemes, personality, contract and portrait. Each new coach also gets a generated coaching headshot in team-colour coaching clothes, not a jersey. The headshot is served on the web, and each offseason's new portraits are packed into the Project Mu portrait mod.

**Delivers:**
- Migration `db/migrations/2027_02_21_coach_madden_profiles.sql` (also in `db/init_db.sql`): table `coach_madden_profiles`, one row per Mu coach or coordinator, holding every Madden coach field Mu writes plus the link to the save record.
- `engines/coach_madden_profile.py`: builds a full Madden profile for an invented coach from Mu's data (pure, seeded).
- `pc_save_reader.mjs`: the snapshot gains `coaches` (every Coach record) and `staff` (each team's HC, OC and DC).
- `pipeline/coach_link.py`: links Mu's coaches and coordinators to save records at load and backfills `coach_madden_profiles` rows with `source = 'SAVE'`.
- `pipeline/save_reader/staff.mjs`: a new `"staff"` block in `save_writer.mjs` with three actions, `create`, `assign` and `release`. A head-coach change re-points the team's user (the `league_setup.mjs` wiring).
- `engines/coach_save_sync.py`: diffs Mu's staff for the target season against the save's staff and turns the difference into the `"staff"` block. It is idempotent, so a rerun writes nothing.
- The league office's `OFFSEASON_START` write includes the staff block (and, for catch-up, any later offseason stage while the save still differs).
- `headshots/coach_headshots.py` and a coach mode in `headshots/headshot_appearance_generator.py`: coaching headshots painted into real NFL coach photos (team polo or quarter-zip, sometimes a headset), output to `static/coaches/{coach_key}.webp` plus a 512×512 BC3 DDS.
- `tools/build_coach_donor_library.py`: collects and registers the coach photos (`data/headshots/coach_donors/`).
- `tools/coach_portrait_pack.py`: once per offseason, assigns portrait IDs to the new coaches and writes the DDS manifest that `/build-mod` packs into the Project Mu portrait mod.

**JR's answers (2026-11-10):**
- D1. New coach records with a full set of information, taking inspiration from the community's create-a-coach tool (`CreateCoachM27.exe`).
- D2. A person who already exists in the save is moved, not re-created. This covers a poached coordinator, a retread head coach and an internal promotion. Only coaches Mu invents get a new record. Fired coaches stay in the save as free agents.
- D3. Head coaches and both coordinators (HC, OC, DC).
- D4. Coach headshots come from the headshot appearance generator, extended so a coach isn't drawn in a jersey.
- D5. In-game portraits arrive in one batch per offseason, through the existing portrait mod pack. A coach shows a silhouette until JR installs that pack.
- D6. Coach headshots are painted into real NFL coach photos (like the player donor plates), not generated from a prompt alone.
- D7. The writer applies coaching changes to JR's own teams (CAR, IND) as well. Mu's tables are the truth for all 32 teams (was Q1).
- D8. A fired coach no team hires stays in the free-agent pool for 3 seasons, then is set `ContractStatus = Retired` (was Q2). This is built in U5 (`coach_save_sync`: a `release` with `"retire": true` once the third unhired season ends) and U3 (`release` takes `retire`).
- D9. Created coaches start at a modest Madden level based on years coaching, and grow later through coach progression, which is out of scope (was Q3).
- D10. Real starting coaches keep their real headshots. Only invented coaches are generated (was Q4).
- D11. `pipeline/character_visuals.py` is off the deletion list until Spike A decides whether the coach writer needs it (was Q5). Done 2026-11-10 in `AUDIT_LEDGER.md` §3b (`HOLD`) and the spec's layout line.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `league_setup.mjs:171-199` gives each team a `FranchiseUser` whose `UserEntity` is `Team.HeadCoach`, sets `Team.UserCharacter` to the same coach, and sets `Coach.IsUserControlled`. `league_setup.mjs:27,60` writes `CoachFiring = 2` (OFF). `league_setup.mjs:285-299` checks that HeadCoach, UserCharacter and UserEntity are the same record. | Madden never fires or hires a coach, so only Mu can change one. A head-coach change must move all four links together, or `league_setup.mjs state` reports the team as broken. |
| S2 | The Madden 27 schema (`madden-franchise/data/schemas/27/M27_620_0.gz`): `Team` has `HeadCoach`, `OffensiveCoordinator`, `DefensiveCoordinator` and `SpecialTeamsCoach` (refs to `Coach`), plus `StaffPersonBlacklist`. `CoachesRoot.CoachesArrays` is `Coach[][]`. | Coordinators are Team refs too, so HC, OC and DC changes use the same mechanism. **Spike B: the schema's `CoachesRoot` does not exist as a table in a real save** (none of the 1,596 table names matches it), and nothing array-shaped changes on a real firing. The free-agent coach pool is `Coach.TeamIndex = 32` — see `docs/reference/MADDEN_COACH_FIRING_DIFF.md`. |
| S3 | `Coach` has 123 fields. Identity: `FirstName`, `LastName`, `Name`, `Age`, `PresentationId`, `TeamIndex`, `PrevTeamIndex`, `Position`/`OriginalPosition` (`StaffPosition`). Career: `Archetype` (`StaffArchetypeEnum`: OffensiveGuru, DefensiveGenius, PersonnelCzar, DevelopmentWizard, MasterMotivator, …), `Level`, `ExperiencePoints`, `StaffTalents` (`AbstractTalentList` → `CoachingTalents` Gameday and Playsheet `Talent[]`), `COACH_SPECIALTY`, the `COACH_QB` … `COACH_P` position ratings, `COACH_RATING`, `YearsCoaching`. Contract: `ContractLength`, `ContractSalary`, `ContractStatus` (Signed, FreeAgent, Retired, …), `ContractYearsRemaining`. Football: `OffensivePlaybook`, `DefensivePlaybook`, `OffensiveScheme`, `DefensiveScheme`, `SimOffensivePlaybook`, `SimDefensivePlaybook`, `OffenseAudibles`, `DefenseAudibles`, `TeamPhilosophy`, `DefaultTeamPhilosophy`, the `COACH_*TENDENCY*` fields. Character: `Personality`, `COACH_DEMEANOR`, `COACH_STANCE`, `CoachBackstory`. Look: `CharacterBodyType`, `Height`, `Weight`, `FaceShape`, `GenericHeadAssetName`, `AssetName`, `CharacterVisuals` (a `RawData` binary blob). Portrait: `Portrait`, `Portrait_Force_Silhouette`, `Portrait_Swappable_Library_Path`. Career counters: `CareerWins` … `CareerSuperbowlWins`, plus the season counters. | A created coach needs all of these filled in coherently. Several sub-records (talent lists, audibles, visuals) are references, so making a coach is more than setting fields on one row. |
| S4 | `C:\Madden27Modding\Tools\CreateCoachM27.exe` (95 MB, a `pkg`-bundled Node app) embeds Madden's coach talent tables (`TalentTierInfo`, `PlaysheetTalentInfo`, `GamedayTalentInfo`, `CoachExperienceAward`) and coach wardrobe loadouts (`CoachWardrobe_Polo`, `_Pants`, `_Shoe`, `Coach_ClipBoard`, loadout type `CoachOnField`). `docs/MMC_MOD_SURVEY.md:42` lists it (Sinthros), along with Evan's "Create a Free Agent Coach Tool". | This is prior art for exactly the sub-records S3 says are hard: talents and visuals. Spike A reads how it builds them. We take ideas and data layouts from it; we don't run it inside the pipeline. |
| S5 | `save_writer.mjs:112-200`: one `writeSave` call plans every block, refuses everything if any edit is wrong, applies the blocks in order (release, sign, trade, depthCharts), saves, then re-opens the file and reads every edit back. Its blocks are `players`, `records`, `release`, `sign`, `trade` and `depthCharts`. There is no coach block. `Franchise.create(..., autoUnempty: true)` turns an empty record into a live one when a field is set (`save_writer.mjs:121-123`). | The new `"staff"` block follows the same plan → apply → verify pattern in its own module. `autoUnempty` is how `sign.mjs` fills an empty row (P1-08), and it is how a new Coach row gets made. |
| S6 | `pc_save_reader.mjs` has no coach output: `grep -i coach pipeline/save_reader/pc_save_reader.mjs` finds nothing. `pipeline/load_save.py` has no coach code. | Mu can't see the save's coaches today. The reader has to come first. |
| S7 | Mu's `coaches` table (`db/init_db.sql:646-657`) holds `coach_name`, `slant`, two schemes and four tendencies. `coordinators` (`:671-682`) holds `coordinator_name`, `side`, `scheme`, `playbook` and three tendencies. Tenures are in `coach_tenures` and `coordinator_tenures`, career counters in `coach_seasons` (`:698-712`). There is no Madden link column anywhere. | Mu has a fraction of what Madden needs. New fields go into one side table keyed by person, so the two old tables stay as they are. |
| S8 | `coaching_carousel.py:1047-1056` inserts an invented head coach into `coaches` and opens his `coach_tenures` row. Candidate kinds are `POACHED`, `RETREAD` and `OUTSIDE` (`:1057-1066`), and the person-lifecycle events record which is which. | Only `OUTSIDE` (and any invented coordinator) needs a new Madden record. POACHED and RETREAD are moves (D2). |
| S9 | `league_office.py:179` lists the stages; `OFFSEASON_START` is Madden's Staff stage (`league_office.py:18`). The hiring cycle runs in the post-Super Bowl weekly run (`weekly_capture.py:628-640`, Step 7d), before the save reaches the offseason. | The league office writes the staff changes in its `OFFSEASON_START` write, once the hiring cycle is done. A head-coach firing at Black Monday is written in the same offseason write (one write a year). |
| S10 | `PROJECT_SPEC.md` §2.19 (`:1135`) still says "JR mirrors head-coach changes in Madden … Mu's hire stands for that coach", and the carousel's notices ask JR to fire and hire by hand (`weekly_capture.py:632-634`). | The spec and the notices change: JR no longer mirrors coaching changes. The notice becomes a report of what was written. |
| S11 | Player portraits are `Player.PLYR_PORTRAIT` IDs (`headshots/audit_roster_portraits.py:4,99`). New ones are DDS textures replacing portrait IDs in `library_icons_brt` through MMC/Frosty (`PROJECT_SPEC.md` §2.20, portrait converter). `rookie_headshots.py:228` already writes a 512×512 BC3 DDS for each rookie. | Coach portraits go the same way: a `Portrait` ID plus a texture in the mod pack. Spike A confirms that coach portraits live in the same library and which IDs are free. |
| S12 | The headshot generator paints the face into real media-day **jersey** photos: 5 weight tiers × 32 teams of donor plates (`headshots/local/donors.py:171,259`, spec §2.20). The appearance traits are weighted by position (`headshot_appearance_generator.py:245`), and the age cues run up to player ages (`:190`). | Coaches need their own donor photos (D6), traits without positions, ages up to about 70 (more grey hair, glasses), and no jersey anywhere in the prompt or mask. |
| S13 | The 96 starting staff are real coaches matched to Madden 27's starting staffs (`REAL_COACH_PROFILES_PLAN.md` status line, JR 2026-11-06). Their profiles carry reviewed sliders: drive, confidence, temperament and the rest (`personality/`, `engines/coaching_carousel.candidate_sliders`). | Every starting coach already has a Madden record, which gets linked at first load and never created. Sliders exist to set `Personality` and `COACH_DEMEANOR` for invented coaches. |
| S14 | `pipeline/character_visuals.py` parses and re-encodes the `CharacterVisuals.RawData` of players (marked obsolete for the fresh save, `PROJECT_SPEC.md:115`). | It is a candidate reader for coach visuals, so it shouldn't be deleted before Spike A decides whether `staff.mjs` needs it. |

## 1. Scope

**In:**
- Reading every Coach record and each team's HC, OC and DC from the save.
- Linking the starting staff to their save records, and backfilling their Madden fields into `coach_madden_profiles`.
- Building a full Madden profile for every coach or coordinator Mu invents.
- Writing HC, OC and DC changes into the save, for all 32 teams, at the offseason write:
  - creating new records;
  - moving existing ones (poached, retread, promoted);
  - releasing fired coaches to the free-agent pool;
  - re-pointing the user for head-coach changes.
- Godfather coach-GMs are written like any head coach. The GM role has no Madden counterpart.
- Coach headshots in coaching clothes: web WebP and DDS.
- One portrait batch per offseason for the mod pack.
- Updated carousel notices.

**Out (non-goals):**
- Special-teams coaches, scouts and trainers. Mu doesn't model them; `Team.SpecialTeamsCoach` stays as Madden has it.
- Changing a coach's ratings, talents or level after he's hired. Coach progression belongs to `PROGRESSION_PLAN.md` §7.
- Mid-season firings written during the season. Mu's firings are all at season end, so one offseason write covers them.
- Showing coach headshots on the Mu website's pages. This plan serves the files at `/static/coaches/`. Adding them to the coach pages and cards is a small frontend follow-up.
- Building the mod pack inside the pipeline. `/build-mod` packs the DDS files, and JR installs them, as with player portraits.
- Running `CreateCoachM27.exe` itself. It is a reference only.

## 2. Design

### 2.1 Data: `coach_madden_profiles` (migration `2027_02_21_coach_madden_profiles.sql`)

```sql
CREATE TABLE IF NOT EXISTS coach_madden_profiles (
    id SERIAL PRIMARY KEY,
    person_kind VARCHAR(5) NOT NULL CHECK (person_kind IN ('COACH', 'COORD')),   -- coaches.id or coordinators.id
    person_id INT NOT NULL,
    source VARCHAR(8) NOT NULL CHECK (source IN ('SAVE', 'CREATED')),            -- read from the save, or built by Mu
    presentation_id INT,          -- Coach.PresentationId of the linked save record (NULL until written or linked)
    first_name VARCHAR(64) NOT NULL,
    last_name VARCHAR(64) NOT NULL,
    age INT NOT NULL,
    position VARCHAR(24) NOT NULL,            -- StaffPosition: HeadCoach | OffensiveCoordinator | DefensiveCoordinator
    archetype VARCHAR(32) NOT NULL,           -- StaffArchetypeEnum
    specialty VARCHAR(16),                    -- CoachSpecialty
    level INT NOT NULL DEFAULT 1,
    years_coaching INT,
    offensive_playbook VARCHAR(64), defensive_playbook VARCHAR(64),
    offensive_scheme VARCHAR(64), defensive_scheme VARCHAR(64),
    team_philosophy VARCHAR(32),
    personality VARCHAR(32), demeanor VARCHAR(32),
    body_type VARCHAR(24), height INT, weight INT, face_shape VARCHAR(32), generic_head VARCHAR(64),
    talents JSONB NOT NULL DEFAULT '[]',      -- [{"slot": "Gameday"|"Playsheet", "talent": <name>, "tier": n}]
    ratings JSONB NOT NULL DEFAULT '{}',      -- {"COACH_QB": n, ..., "COACH_RATING": n}
    tendencies JSONB NOT NULL DEFAULT '{}',   -- the COACH_*TENDENCY* fields
    visuals_template INT,                     -- PresentationId of the save coach whose CharacterVisuals/loadouts were cloned
    portrait_id INT,                          -- Coach.Portrait (0 = silhouette until the pack is installed)
    portrait_status VARCHAR(10) NOT NULL DEFAULT 'NONE' CHECK (portrait_status IN ('NONE', 'GENERATED', 'PACKED', 'INSTALLED')),
    headshot_path TEXT,                       -- static/coaches/<coach_key>.webp
    seed TEXT NOT NULL,                       -- the draw's seed (same seed, same profile)
    written_at TIMESTAMP,                     -- last time staff.mjs wrote or verified this record
    UNIQUE (person_kind, person_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS coach_madden_profiles_pid ON coach_madden_profiles (presentation_id)
    WHERE presentation_id IS NOT NULL;
```

The coach's contract stays in `staff_contracts`, which is the truth. The writer copies the active row's salary and years into `ContractSalary`, `ContractLength` and `ContractYearsRemaining`. A `coach_key` is `coach-<id>` or `coord-<id>`.

### 2.2 Reading the save: `coaches` and `staff` in the snapshot

`pc_save_reader.mjs` adds two keys:
- `coaches: [{pgid (PresentationId), table, row, first_name, last_name, age, position, team_index, contract_status, archetype, level, portrait, offensive_playbook, defensive_playbook, offensive_scheme, defensive_scheme, is_user_controlled}]`. It includes every live Coach record, free agents too.
- `staff: [{team_index, hc, oc, dc}]`, each value a coach `pgid`. It is read from the `Team` refs.

No `Coach` field is written by the reader.

### 2.3 Linking at load: `pipeline/coach_link.py`

`link_staff(cur, snapshot, season) -> {"linked": n, "unmatched": [...]}`:
1. For each team's HC, OC and DC in `snapshot["staff"]`, it finds Mu's person for that season and role (`coach_tenures`, `coordinator_tenures`). It matches on a normalised full name (`staff_names` normalisation), with team and role as the tie-breaker.
2. For each match, it upserts `coach_madden_profiles` with `source = 'SAVE'`, the `presentation_id`, and the fields read from the save.
3. A seat whose names don't match is reported, never guessed. The starting staffs match by design (S13), so any miss is a data problem for JR.

`pipeline/load_save.py` calls it after the roster load. It is idempotent and writes nothing else.

### 2.4 An invented coach's profile: `engines/coach_madden_profile.py` (pure)

`build_profile(person, role, *, slant, scheme, playbook, tendencies, sliders, background, contract, seed) -> dict`

It returns the `coach_madden_profiles` row's fields. The rules are below. Every random draw uses `random.Random(seed)`, with the seed `madden-coach:<coach_key>`.
- **Name:** `first_name` and `last_name` are split from Mu's name.
- **Age and years coaching:** from `staff_names.background(seed, kind, role)`, the invented background's age and years, clamped to 32–72.
- **Position:** from the role.
- **Archetype:** a head coach with OFFENSIVE slant → `OffensiveGuru`, DEFENSIVE → `DefensiveGenius`, CEO → `MasterMotivator`. An OC → `OffensiveGuru`, a DC → `DefensiveGenius`. `DevelopmentWizard` applies when the progression trait `teacher` is set and the switch for that trait is on.
- **Playbooks and schemes:** from Mu's `scheme`/`playbook` through `PLAYBOOK_MAP` and `SCHEME_MAP` (Mu name → Madden enum). Spike A builds both maps from the enums in the schema. An unmapped name raises.
- **Level, talents and ratings:** level comes from years coaching (`LEVEL_BY_YEARS`). Talents come from `TALENT_TEMPLATES[archetype]`, scaled to that level, and their layout copies what Spike A finds in the save and in CreateCoachM27. Position ratings centre on the coach's specialty.
- **Personality and demeanor:** from the sliders. Confidence and temperament map to `Personality`/`COACH_DEMEANOR` through `DEMEANOR_RULES`; a missing slider uses the neutral value.
- **Look:** `body_type`, `height`, `weight`, `face_shape` and `generic_head` are drawn by the seed from lists of the values used by real coach records in the save (Spike A). The same lists feed the headshot (§2.7), so the 3D model and the portrait roughly agree on skin tone, hair and build.
- **Visuals template:** `visuals_template` names a save coach of the same position and body type whose `CharacterVisuals` and wardrobe the writer clones (§2.5).

`coaching_carousel.hiring_cycle` calls `build_profile` for each OUTSIDE head coach and each invented coordinator it creates, and stores the row with `source = 'CREATED'` and `presentation_id` NULL. These are the only carousel changes: one call and one insert per invented person, inside the existing savepoint.

### 2.5 The `"staff"` block: `pipeline/save_reader/staff.mjs`

edits.json gains:

```json
"staff": [
  {"action": "release", "pgid": 9001},
  {"action": "assign",  "pgid": 9002, "team_index": 7, "position": "DefensiveCoordinator", "contract": {"ContractLength": 3, "ContractSalary": 450, "ContractYearsRemaining": 3}},
  {"action": "create",  "key": "coach-311", "team_index": 7, "position": "HeadCoach", "profile": { …coach_madden_profiles fields… }, "visuals_template": 9010, "contract": {…}}
]
```

`planStaff(franchise, staff, {freed})`, `applyStaff(plan)`, `verifyStaff(check, plan)` and `staffSummary(plan)` match the `sign.mjs`/`trade.mjs` interface. `save_writer.mjs` runs `staff` **before** `release` (coaches and players don't share records), and refuses the whole write on any error.

- **release:** sets `PrevTeamIndex = TeamIndex`, `TeamIndex = 32`, `ContractStatus = FreeAgent`, `ContractYearsRemaining = 0`, `COACH_RATING = 50` and `IsUserControlled = false`, and clears the Team ref that pointed at him. **There is no free-agent array to add him to or remove him from** (Spike B: this save has no `CoachesRoot` table at all, and no array membership changes on a real firing — the pool is simply `Coach.TeamIndex = 32`). `Position` is left as it is, because Madden leaves it: an `assign` into a different role writes it. With `"retire": true` (a free agent unhired for three seasons, D8), it sets `ContractStatus = Retired` instead.
- **assign:** moves an existing record to a team's HC, OC or DC ref, sets `Position` (and `OriginalPosition` when the role changes), `TeamIndex`, `ContractStatus = Signed` and the contract fields. If the seat is held by someone not released in this write, it refuses.
- **create:** fills an empty Coach row (`autoUnempty`), or one freed by a release in the same write. It:
  1. copies the template coach's sub-records (`CharacterVisuals`, the talent list, audibles) into new or empty rows. The cloning helper is `cloneInto`, factored out of `league_setup.mjs` into `lib.mjs`;
  2. sets every profile field;
  3. writes the talents into the cloned talent list;
  4. gives the coach a new `PresentationId` (`max(existing) + 1`, recorded back to Mu from the write's report);
  5. sets `Portrait` to the profile's `portrait_id`, or 0 with `Portrait_Force_Silhouette = true`;
  6. assigns him like **assign**.
- **Head-coach seats** additionally re-point `Team.UserCharacter`, the team's `FranchiseUser.UserEntity` and `FranchiseUser.Team`, and set `IsUserControlled`, which are the same links `league_setup.mjs` sets (S1). Spike B measured what Madden's own firing does instead: `Team.UserCharacter` → null, `FranchiseUser.Team` → null, `FranchiseUser.UserEntity` still pointing at the fired man, `IsUserControlled` still true, and the user row orphaned rather than deleted. Mu wants the opposite — every team keeps a live user — so a Mu head-coach change is a release and an assign **in the same write**, and `FranchiseUser.Team` is part of what `assign` must restore. Afterwards `verifyStaff` re-reads all four links itself and reports a mismatch (`league_setup.mjs state` checks the user rows and their entities, but it does not compare `Team.UserCharacter` or `FranchiseUser.Team`, so it is not the acceptance check the plan first claimed).
- **Firing a head coach clears his coordinators too in Madden** (Spike B: NYJ, WAS and TEN lost all three seats). `plan_staff_edits` must self-heal this: a coordinator seat Madden cleared while Mu still has a coach for it becomes an `assign`. Test it.

Coach-GMs: the GM seat isn't in the save, so a coach-GM is written as a plain head coach.

### 2.6 What to write: `engines/coach_save_sync.py`

`plan_staff_edits(cur, snapshot, target_season) -> {"staff": [...], "skipped": [...], "report": [...]}`:

- **Wanted seats:** for every team, the HC, OC and DC whose tenure covers `target_season` in Mu, each with his `coach_madden_profiles` row.
- **Actual seats:** `snapshot["staff"]`.
- **Per seat that differs:**
  - the man in the save leaves → `release`, unless he is someone else's wanted coach, in which case it's an `assign`;
  - the wanted man has a `presentation_id` → `assign`;
  - the wanted man has none → `create`.
- **Order:** releases first, then assigns, then creates, so freed rows can be reused.
- **Skips:** a seat whose wanted coach has no profile row, or a team whose hiring cycle isn't finished, is skipped and reported. It never guesses.
- **Rerun:** once the save matches, it returns an empty list.

The league office adds `plan_staff_edits` to `plan_stage` for `OFFSEASON_START`, and for any later offseason stage while the save still differs (catch-up). It also records each new `presentation_id` from the write's report back into `coach_madden_profiles.presentation_id` and `written_at`. A dry run plans and prints without writing.

The carousel's notices change. "Mu: coaches fired / hired" stops asking JR to mirror the changes and instead lists what the next offseason write will apply, then what it applied.

### 2.7 Coach headshots: `headshots/coach_headshots.py`

**Appearance:** `headshot_appearance_generator.appearance(..., role="coach")` is a new keyword, with the player default unchanged. In coach mode:
- the position weights are off;
- the age cues run 32–72, with more grey and recession at 55+;
- glasses appear in about 25% of coaches and facial hair in about 40% (estimates, tunable constants);
- the build comes from the profile's height and weight.

The same seed gives the same face. The profile's `body_type` and `generic_head` skin-tone group feed the draw, so the portrait agrees with the 3D model.

**Donor photos:** `tools/build_coach_donor_library.py` collects real NFL coach media-day or sideline headshots for each team: team polo, quarter-zip or jacket, sometimes a headset. They go in `data/headshots/coach_donors/{TEAM}_{n}.png` and are registered in `data/headshots/coach_donors/registry.json` with an aperture mask. The mask traces the collar of the coaching top (the same 2-px feather rule as `donors.generate_tier_mask`). Spike D checks where these photos can be fetched (ESPN's coach headshots, team sites). Weight tiers don't apply; 2–3 plates per team cover build and clothing.

**Generation:** `generate_coach_headshot(profile, team) -> (webp_path, dds_path)` routes through `headshots.local` inpainting when `USE_LOCAL_HEADSHOTS=1`. The prompt says "NFL coach, team-colour coaching polo/quarter-zip", and its negative prompt carries "jersey, shoulder pads, helmet, number". The outputs are `static/coaches/{coach_key}.webp` (700×508 cutout, as for players) and `data/headshots/dds/coaches/{coach_key}.dds` (512×512 BC3). Each sets `portrait_status = 'GENERATED'`.

**CLI:** `python3 -m headshots.coach_headshots [--season N] [--coach-key K] [--dry-run]` makes every CREATED coach without a headshot. Starting real coaches use their real photos (fetched like veterans' photos) and are not generated.

`server/api.py` already serves `/static/`, so `static/coaches/` needs nothing new (Spike D confirms).

### 2.8 The offseason portrait batch: `tools/coach_portrait_pack.py`

`python3 -m tools.coach_portrait_pack --season N [--dry-run]`, run after the OFFSEASON_START write:
- It assigns each GENERATED coach a free portrait ID from `COACH_PORTRAIT_RANGE` (the range comes from Spike A: IDs the library holds but no record uses).
- It writes `data/headshots/dds/coaches/pack_<N>/portrait_manifest.csv` (portrait_id, dds file, coach_key) for `/build-mod`.
- It sets `portrait_status = 'PACKED'` and queues `Portrait = <id>`, `Portrait_Force_Silhouette = false` as `"staff"` field updates for the next league-office write. `assign` takes an optional `set` of profile fields for exactly this.

JR runs `/build-mod` with the manifest and installs the pack. Once the pack is confirmed installed, `--mark-installed` sets `INSTALLED`. Until then the coach shows a silhouette (D5).

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | A cloned Coach record with new talents and visuals crashes Madden, or shows a broken model. | Spike C: write one created coach into a `CAREER-TUNE-*` clone, load it with the `/madden` pilot, open the staff screen, then sim a game and a week. | Fall back to cloning the template's talents unchanged and only renaming. If even that fails, assign from a pool of the save's free-agent coach records, renamed. That pool is the "move an existing record" path, already planned. |
| R2 | Re-pointing a team's user to a new head coach breaks Madden's user wiring (the coach-firing flow may also touch arcs, goals or the user registry). | Spike C does a head-coach swap on a CPU user team, then checks the `league_setup.mjs state` check and a sim through the next offseason stage. | `staff.mjs` also copies whatever the spike shows Madden's own firing changes (diff a save before and after a manual in-game firing, as the CoachFiring spike did). |
| R3 | `CoachesRoot.CoachesArrays` isn't the free-agent pool, or released coaches need another list. | Spike A diffs a save before and after JR fires a coach in the menu. | Use whatever list the diff shows. **release** stays the same action with different targets. |
| R4 | Coach portraits don't use the player portrait library, or there are no free IDs. | **Answered by Spike A:** they share one numeric space and one texture bank (`library_icons_brt`); 29 IDs are used by both tables, and the free block 5324-5484 is `COACH_PORTRAIT_RANGE`. Still open: whether the DDS pack can add a library entry at a free ID (Spike D / `/build-mod`). | Use 5486-5581 (96 IDs) next; last resort is replacing a retired coach's textures. |
| R5 | No good source of real coach photos per team. | Spike D. | Use generic plates per team colour, from licensed or stock coaching-polo photos, with logos added by the plate step. Last resort: the fully generated outfit JR passed on (D6). |
| R6 | Mu's scheme and playbook names don't map onto Madden's enums. | Spike A listed the enums and U1 wrote `PLAYBOOK_MAP`/`SCHEME_MAP`, which raise on a miss. **Resolved:** every Mu playbook maps (six were nickname aliases), and JR decided the one miss on 2026-11-10. | Nothing left. An unmapped name still raises rather than guessing. |
| R7 | The OFFSEASON_START write runs before the hiring cycle finished (a missed weekly run). | `plan_staff_edits` skips unfinished teams, and the catch-up stages write them later. | No wrong coach is written; it only arrives late. |
| R8 | CreateCoachM27's code isn't readable (bytecode only). | Spike A tries to unpack the `pkg` snapshot. | Read the talent and wardrobe JSON it embeds, which `strings` already shows, and learn the record layout from the save instead. |

**Spike first** (before wave 1; one agent; offline except Spike C):
- **A. Save and tool read (about 2 hours).** On a copy of `CAREER-MUBASE`:
  - dump every Coach record with its talent lists, visuals size, Portrait fields and `CoachesRoot` arrays;
  - list the values the playbook, scheme, archetype, body type and head asset enums actually use;
  - find the PresentationId and portrait ID ranges;
  - unpack CreateCoachM27, or read its embedded data, for its talent and wardrobe layout.

  Output: `docs/reference/MADDEN_COACH_RECORDS.md`. This sets `PLAYBOOK_MAP`, `SCHEME_MAP`, `TALENT_TEMPLATES`, `COACH_PORTRAIT_RANGE` and the free-agent target.
- **B. Manual firing diff (JR, 10 minutes, plus the agent).** JR fires one CPU coach in the Madden menu on a `CAREER-TUNE-*` clone and saves. The agent diffs the save before and after (as in the CoachFiring spike, `V2_CORE_BUILD_PLAN.md:21`).
- **C. In-game check (after U3 merges, before wave 3):** `/madden` loads a clone with one created coach and one HC swap: staff screen, a simmed game, then advance one stage. It also has to confirm four things Spike A could not settle statically: that a **team head coach accepts a 16384 scheme ref** (team HCs use the static 16485 table today); that `Portrait = 0` with `Portrait_Force_Silhouette = true` actually renders a silhouette (no coach in the save exercises it); that a **cloned talent list** works on a created coach; and that a `ContractStatus = Retired` coach stays out of the pool.
- **D. Coach photo source (30 minutes):** where per-team real coach headshots can be fetched, and confirming that `/static/coaches/` is served.

## 4. Tests (synthetic, no live DB)

- `tests/test_coach_madden_profile.py`:
  - the same seed gives the same profile;
  - archetype from slant and role;
  - an unmapped playbook raises;
  - talents scale with level;
  - sliders → demeanor;
  - age clamped to 32–72;
  - every enum value is in the allowed list.
- `tests/test_coach_link.py` (fake cursor and synthetic snapshot):
  - the starting staff all link;
  - a name mismatch is reported, not guessed;
  - a rerun changes nothing;
  - a free-agent coach in the snapshot isn't linked to a seat.
- `tests/test_coach_save_sync.py`:
  - no change → empty list;
  - a Black Monday firing plus an outside hire → release + create;
  - a poached coordinator → assign from his old team plus a promotion at the old team;
  - a retread → assign from free agency;
  - a Godfather coach-GM → plain HC assign;
  - an unfinished team is skipped;
  - the order is releases, assigns, creates;
  - JR's own coordinator firing is written (D7);
  - a coach unhired for three seasons gets `release` with `retire: true`, and not before (D8).
- `pipeline/save_reader/test/staff.test.mjs` (a synthetic franchise fixture like `sign.test.mjs`):
  - create fills an empty row and clones the visuals and talents;
  - assign moves a record and its Team ref;
  - release clears the ref and adds him to the free-agent array;
  - the HC re-point keeps HeadCoach = UserCharacter = UserEntity;
  - an occupied seat refuses;
  - one bad edit writes nothing;
  - the read-back catches a mismatch.
- `pipeline/save_reader/test/write_save.test.mjs`: the `staff` block in a combined write runs before `release`, and the report carries the new PresentationIds.
- `pipeline/save_reader/test/read_save.test.mjs`: the snapshot's `coaches` and `staff` from the fixture.
- `tests/test_coach_headshots.py` (MockBackend):
  - coach mode never puts "jersey" in the prompt and does put it in the negative prompt;
  - the same seed gives the same appearance;
  - the player default is unchanged (a golden player appearance line);
  - outputs both files;
  - skips coaches that already have headshots.
- `tests/test_coach_portrait_pack.py`:
  - IDs come from the range and never repeat;
  - the manifest's columns;
  - status moves GENERATED → PACKED;
  - a rerun assigns nothing new.
- `tests/test_league_office_staff.py`:
  - OFFSEASON_START includes the staff block;
  - a dry run writes nothing;
  - written PresentationIds are recorded back;
  - a later offseason stage writes only what's still missing.

Run with `pytest -p no:cacheprovider -m "not integration" <files>` and `node --test "pipeline/save_reader/test/*.test.mjs"` (the directory form fails on Node 22: it treats the path as an entry module). Nothing touches `madden_test`, `madden_mu2026` or `madden_2036`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Profile schema and builder

- **Owns:** `db/migrations/2027_02_21_coach_madden_profiles.sql` (new), `db/init_db.sql`, `engines/coach_madden_profile.py` (new), `tests/test_coach_madden_profile.py` (new)
- **Reads:** `docs/reference/MADDEN_COACH_RECORDS.md` (Spike A), `engines/staff_names.py`, `engines/coaching_carousel.py:1000-1100`, `db/migrate.py` (ledger and checksum rules)
- **Depends on:** Spike A
- **Done when:** the migration applies twice cleanly to a scratch DB and is mirrored in `init_db.sql` with its ledger row, and `build_profile` returns a complete row for each role and slant.
- **Verify:** `pytest -p no:cacheprovider -m "not integration" tests/test_coach_madden_profile.py` passes, and `python3 -m db.migrate status --dsn <scratch>` shows 0 drift.

### U2: Read coaches from the save

- **Owns:** `pipeline/save_reader/pc_save_reader.mjs`, `pipeline/save_reader/test/read_save.test.mjs`
- **Reads:** `pipeline/save_reader/league_setup.mjs:160-210`, `pipeline/save_reader/lib.mjs`, Spike A
- **Depends on:** Spike A
- **Done when:** the snapshot carries `coaches` and `staff` in the §2.2 shape, and nothing is written to the save.
- **Verify:** `node --test pipeline/save_reader/test/read_save.test.mjs` passes.

### U3: The `"staff"` save-writer block

- **Owns:** `pipeline/save_reader/staff.mjs` (new), `pipeline/save_reader/save_writer.mjs`, `pipeline/save_reader/lib.mjs` (the `cloneInto` move), `pipeline/save_reader/league_setup.mjs` (imports `cloneInto` from lib), `pipeline/save_reader/test/staff.test.mjs` (new), `pipeline/save_reader/test/write_save.test.mjs`, `pipeline/save_reader/test/league_setup.test.mjs`
- **Reads:** `sign.mjs`, `trade.mjs`, `release.mjs` (the plan/apply/verify pattern), Spike A and B
- **Depends on:** Spike A, Spike B
- **Done when:** the three actions plan, apply and read back on the fixture; the HC re-point passes the `league_setup` user check; and `league_setup.mjs` still passes its own tests after the helper move.
- **Verify:** `node --test "pipeline/save_reader/test/*.test.mjs"` all pass (quotable glob form; the bare directory form fails on Node 22).

### U4: Coach headshots

- **Owns:** `headshots/coach_headshots.py` (new), `headshots/headshot_appearance_generator.py`, `headshots/local/jersey_plate.py`, `tools/build_coach_donor_library.py` (new), `data/headshots/coach_donors/registry.json` (new), `tests/test_coach_headshots.py` (new)
- **Reads:** `headshots/local/donors.py`, `headshots/local/adapter.py`, `headshots/local/jersey_plate.py` (the player plate compositor to generalise for coach tops), `headshots/local/build_donor_library.py` (the player donor tool to mirror), `headshots/rookie_headshots.py`, `headshots/portrait_converter/converter.py`, Spike D
- **Depends on:** Spike D
- **Done when:** MockBackend generates a coach WebP and DDS with no jersey in the prompt, the player appearance output is byte-identical to before, and the donor tool registers plates with masks.
- **Verify:** `pytest -p no:cacheprovider -m "not integration" tests/test_coach_headshots.py tests/test_local_headshot.py` passes.

### U5: Link and sync

- **Owns:** `pipeline/coach_link.py` (new), `pipeline/load_save.py`, `engines/coach_save_sync.py` (new), `engines/coaching_carousel.py`, `tests/test_coach_link.py` (new), `tests/test_coach_save_sync.py` (new)
- **Reads:** `engines/coach_madden_profile.py` (U1), the snapshot shape (U2), `engines/coach_hot_seat.py`, `engines/fire_coordinator.py`, `engines/staff_contracts_run.py`
- **Depends on:** U1, U2
- **Done when:**
  - load links the starting staff;
  - the carousel stores a CREATED profile for each invented coach;
  - `plan_staff_edits` turns every §4 scenario into the right actions;
  - the existing carousel tests still pass.
- **Verify:** `pytest -p no:cacheprovider -m "not integration" tests/test_coach_link.py tests/test_coach_save_sync.py tests/test_coaching_carousel_db.py tests/test_coach_hot_seat.py tests/test_godfather_gates.py` passes.

### U6: League office wiring, notices and the portrait batch

- **Owns:** `pipeline/league_office.py`, `pipeline/weekly_capture.py` (the carousel notice text only), `pipeline/save_reader/staff.mjs` (only the optional `set` on `assign` that §2.8 needs), `pipeline/save_reader/test/staff.test.mjs` (only the test for it), `tools/coach_portrait_pack.py` (new), `tests/test_league_office_staff.py` (new), `tests/test_coach_portrait_pack.py` (new)
- **Reads:** U3's block interface, U5's `plan_staff_edits`, U4's outputs, `pipeline/league_office.py:1985-2225` (`plan_stage`), `.claude/skills/build-mod/`
- **Depends on:** U3, U4, U5, plus Spike C passed
- **Done when:**
  - a dry-run OFFSEASON_START prints the staff block;
  - a write on a scratch fixture records the new PresentationIds;
  - the notices list what was written;
  - the pack tool writes a manifest and moves the statuses.
- **Verify:** `pytest -p no:cacheprovider -m "not integration" tests/test_league_office_staff.py tests/test_coach_portrait_pack.py tests/test_league_office.py tests/test_league_office_annual.py` passes.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 0 | Spikes A, B, D | — | They set the maps, ranges and targets every unit builds against. Spike B needs JR for 10 minutes. |
| 1 | U1, U2, U4 | — | No shared files. Each builds against §2 and the spike notes. |
| 2 | U3, U5 | U1, U2 | U5 needs U1's table and U2's snapshot. U3 shares no file with U5. |
| 2.5 | Spike C (in game) | U3 | It proves the created record and the HC swap in Madden before anything is wired to run on its own. |
| 3 | U6 | U3, U4, U5, Spike C | It wires everything into the league office. |

**File ownership check:**

| File | Unit |
|---|---|
| `data/headshots/coach_donors/registry.json` | U4 |
| `db/init_db.sql` | U1 |
| `db/migrations/2027_02_21_coach_madden_profiles.sql` | U1 |
| `engines/coach_madden_profile.py` | U1 |
| `engines/coach_save_sync.py` | U5 |
| `engines/coaching_carousel.py` | U5 |
| `headshots/coach_headshots.py` | U4 |
| `headshots/headshot_appearance_generator.py` | U4 |
| `headshots/local/jersey_plate.py` | U4 |
| `pipeline/coach_link.py` | U5 |
| `pipeline/league_office.py` | U6 |
| `pipeline/save_reader/staff.mjs` | U6 (only the `assign` `set`) |
| `pipeline/save_reader/test/staff.test.mjs` | U6 (only that test) |
| `pipeline/load_save.py` | U5 |
| `pipeline/save_reader/league_setup.mjs` | U3 |
| `pipeline/save_reader/lib.mjs` | U3 |
| `pipeline/save_reader/pc_save_reader.mjs` | U2 |
| `pipeline/save_reader/save_writer.mjs` | U3 |
| `pipeline/save_reader/staff.mjs` | U3 |
| `pipeline/save_reader/test/league_setup.test.mjs` | U3 |
| `pipeline/save_reader/test/read_save.test.mjs` | U2 |
| `pipeline/save_reader/test/staff.test.mjs` | U3 |
| `pipeline/save_reader/test/write_save.test.mjs` | U3 |
| `pipeline/weekly_capture.py` | U6 |
| `tests/test_coach_headshots.py` | U4 |
| `tests/test_coach_link.py` | U5 |
| `tests/test_coach_madden_profile.py` | U1 |
| `tests/test_coach_portrait_pack.py` | U6 |
| `tests/test_coach_save_sync.py` | U5 |
| `tests/test_league_office_staff.py` | U6 |
| `tools/build_coach_donor_library.py` | U4 |
| `tools/coach_portrait_pack.py` | U6 |

No path appears twice. `docs/reference/MADDEN_COACH_RECORDS.md` is written by the spike, not by a unit.

## 7. Spec changes when built

- **§2.19 Coaching Carousel:**
  - replace "JR mirrors head-coach changes in Madden … Mu's hire stands for that coach" with the save write (§2.6);
  - add the CREATED profile step to the hiring cycle;
  - rewrite the notices bullet.
- **§2.20 Headshots:** a "Coach headshots" bullet covering coach mode, the coach donor plates, the outputs and the offseason portrait batch.
- **§2.21a League Office:** OFFSEASON_START (and catch-up) writes the `"staff"` block, and new PresentationIds are recorded back.
- **§3.3 (data model):** a new `coach_madden_profiles` entry and the `2027_02_21` migration.
- **Save tooling (the `pc_save_reader.mjs` and `save_writer.mjs` layout lines and their section):** the snapshot's `coaches`/`staff`, and the `"staff"` block's three actions in the edits.json description.
- **Repository Layout:** `engines/coach_madden_profile.py`, `engines/coach_save_sync.py`, `pipeline/coach_link.py`, `pipeline/save_reader/staff.mjs`, `headshots/coach_headshots.py`, `tools/build_coach_donor_library.py`, `tools/coach_portrait_pack.py`, `static/coaches/`, `data/headshots/coach_donors/`, `docs/reference/MADDEN_COACH_RECORDS.md`, plus the new test files.

## 8. Decisions for JR

All five questions were answered by JR on 2026-11-10; see D7–D11 at the top. None are open.

---

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| Spike D | 0 | `worktree-agent-a1465b19dc48a9bc0` | `.claude/worktrees/agent-a1465b19dc48a9bc0` | merged — `docs/reference/COACH_DONOR_PHOTOS.md` | `df76bec` |
| Spike B | 0 | — (orchestrator, two live saves) | — | merged — `docs/reference/MADDEN_COACH_FIRING_DIFF.md` | `b5a0f55`, `a4975b5` |
| Spike A | 0 | `worktree-agent-af6501f426eed3a8c` | `.claude/worktrees/agent-af6501f426eed3a8c` | merged — `docs/reference/MADDEN_COACH_RECORDS.md` | `081670e` |
| U2 | 1 | `worktree-agent-a02f2a7f775862c72` | `.claude/worktrees/agent-a02f2a7f775862c72` | merged — READER_VERSION 8, `coaches` + `staff` | `b936bdb` |
| U1 | 1 | `worktree-agent-a6fe6cf111d8fd3ad` | `.claude/worktrees/agent-a6fe6cf111d8fd3ad` | merged — `coach_madden_profiles` + `build_profile` | `38ed8e3` |
| U4 | 1 | `worktree-agent-a60662e3ea9a826b7` | `.claude/worktrees/agent-a60662e3ea9a826b7` | merged — coach headshots, coach-plate compositing, donor tool | `d6e650b` |
| U5 | 2 | `worktree-agent-a9dc2221c00352964` | `.claude/worktrees/agent-a9dc2221c00352964` | merged — `coach_link`, `plan_staff_edits`, carousel CREATED profiles | `8cb53dd` |
| U3 | 2 | `worktree-agent-ae83f1723e377833e` | `.claude/worktrees/agent-ae83f1723e377833e` | merged — the `"staff"` writer block | `57fb5d9` |
| U6 | 3 | `worktree-agent-a6abdd73af0e472f2` | `.claude/worktrees/agent-a6abdd73af0e472f2` | merged — league-office staff write, notices, portrait pack | `b1fa89e` |
