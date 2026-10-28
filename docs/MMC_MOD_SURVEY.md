# Madden Modding Community (MMC) Survey for the PC Migration

Scraped 2026-10-28 from the MMC Discord (server 483324610968944651) through JR's signed-in Chrome. Companion to [`PC_MIGRATION_PLAN.md`](in_progress/PC_MIGRATION_PLAN.md). Nothing here is installed or tested; versions and dates are as posted. Thread links are `https://discord.com/channels/483324610968944651/<id>`.

Starting point was the thread JR linked, *TBRs MyFranchise Tool* (`1221157058447933530`). It lives in `mmc-mod-details`, an archived forum with three Madden 24-era posts, so the survey widened to the live Madden 27 channels: `tools` (~90 posts), `m27-community-mods` (149 posts), `mod-tools-release`, `sabos-mods`, `sinthros-mods` and the MyFranchise channels.

## 1. Must-haves (base install)

| Tool | What it is | Why for Project Mu | Thread |
|---|---|---|---|
| **MMC Modding Tools v1.1.0.7** (WiiMaster, 2026-10-21) | MMC's Frosty-based editor and mod manager for Madden 27 and CFB 27: textures, mesh and face duplication, uniform, stadium and sound editors, Teambuilder import. Ships the SDK/cache for each title update (10/21 patch). Unpack with 7-Zip or WinRAR | Applies every `.fbmod` below; the community's M27 texture path, so it is the route for §9.4 portrait replacement (the plan currently names `FMT.Madden26Plugin`) | `mod-tools-release` (`1536392792870223912`) |
| **MyFranchise Tool (MFT) v2.0.1** (Sinthros, 2026-09-16; M27-only from 2.0.1) | Windows app that opens an offline franchise file at the main menu: edit players (ratings, traits, injuries, appearance, abilities, contracts), coaches (playbooks, schemes, talents), draft class, weather, depth charts (auto-reorder); trade centre; user/CPU control per team; league history; reloads when the file is saved elsewhere | A GUI to inspect and hand-fix what `pipeline/teleport.py` and `save_writer` write; custom portrait import per player (§4) | `myfranchise-download` (`935275895642521640`) |
| **Madden Franchise Editor** (bep713) + **M27 schema** `M27_620_0.gz` (WiiMaster, 2026-08-08) | Raw table editor for franchise files (same author as `madden-franchise`). Until it supports M27 officially: Open Table Editor → Schema viewer → Change → Ctrl+R → add schema from file | Low-level checks of the tables the save reader/writer touches; the schema matches what `madden-franchise` needs for M27 | `tools` pinned (`1142636321131741264`) |
| **M27 Unlocked** (orckestra) | Unlocks vanity gear, jersey styles, tattoos, no-helmet option, etc. | Required by RFM and many cyberface/appearance mods | `1537452638277210162` |
| **Sabos Franchise Mod 1.2** (2026-09-23) | Sim stats tuned to the last 3 seasons of real data, more severe real-life injuries, lower retirement chance, regression reworked, XP disabled (needed for MFT's progression tool), no mini-game skill points | Makes the save's sim lines (which the direct loader reads, §5) closer to the real NFL. Sabos Gameplay Mod 1.0 (2026-10-10) is separate | `sabos-mods` → M27 Download Section (`1540151544744247356`) |

## 2. Migration-plan matches

| Plan section | Community tool | Notes |
|---|---|---|
| §4.2 Option 1: set the year to 2037 | **seasonYearSetup.exe** in *Prime's Retro Franchise Tools (M27)* (Primetime02454, 2026-08-23) | Sets the season year and Super Bowl number: the exact script the plan cites, now built for M27. Same thread: `salaryCapGenerator` (random cap growth over 30 years, run once in Year 1 preseason), `handleSpecialStadiums`, `retroSchedule`, `handleTeamNames` (`1541092270927904900`) |
| §4.3 commentary IDs | **Presentation ID (Commentary) Fix Tool v3.1** (WiiMaster) | Sets every player's presentation ID to match their name so commentary says the right name. Run in preseason week 1 *before* MFT progression (a later ID change re-rolls the progression path). A ready-made alternative to building the surname list ourselves; check whether it covers M27 (`1169366188162293851`) |
| §4.3 appearance | **Player Appearance Transfer v3.1** (orckestra) | Copies CharacterVisuals between two franchise files by player asset name (`1322054873197445120`). **Portrait ID + AssetName** are the two fields that tie a player to his portrait and cyberface (§4) |
| §4.3 release / roster room | **Various Franchise Utilities (M27)** (WiiMaster): `trimFreeAgents` (deletes the lowest free agents so a custom draft class has empty player rows), `draftPickReset`, `clearTeamStaffAbilities` | `trimFreeAgents` matters if teleporter-added players plus a draft class run out of player-table rows (`1538443773787242516`) |
| §4.3 roster size | **M27 Roster size increase** (düstere Legende, GitHub) | 80 in preseason, 65 at cut day, practice squad eligibility 3 years, starting cap +$40M. **New franchise only**; the roster overview shows 60 (hard-coded); practice squad *size* cannot change (`1556167422304722984`). Decide before the 2037 franchise is created |
| §5 save loader / cross-check | **Madden Franchise Exporter** (LilScotty) | Dumps a franchise file to plain-English text: career and season stats, team stats, game logs, rosters. Useful as an independent check of `pipeline/load_save.py` output (`1539472709912756324`) |
| §6 CFB 27 bridge | **Pipeline: CFB 27 → Madden 26/27 Draft Class Tool** (ChanceDaWrapper) | Windows app: reads the dynasty's leaving players (or predicts them on earlier saves), projects round and pick, converts ratings to Madden's scale (deterministic "Power Curve", tunable), assigns dev traits by target shares, writes a Madden-importable draft class with faces. Most of the plan's §6 already exists; compare before building (`1539040565482758245`) |
| §6 alternative draft classes | **Eli2Manningham M27 Draft Classes** (E2M2027–2029 now, 2030–2033 coming; matched to M27's higher rookie scale), **Evy's 2027 Draft Class**, **Madden 27 Draft Class Generator** (Bluechummy) | Fallback when no CFB class is ready (`1559347442774966283`, `1548448083732340748`, `1545503678834937977`) |
| §5 schedule | **Schedule Regenerator v3.1 (M24–27)** (WiiMaster) | Rebuilds a Year 2+ schedule to NFL rules (divisional week 18, no back-to-back primetime); preseason only (`1240015640358682766`) |
| §9.4 portraits | See §4 below | |

## 3. Sim and franchise realism

- **RFM, Realistic Franchise Mod** (Evan, updated 2026-10-25, 3.27 GB): week-7 real-life franchise file (scores, stats, standings, injuries, rosters, staffs), 300+ cyberfaces, player and coach **portrait PNGs for MFT**, real head trainers, revamped Eli2Manningham 2027–2029 classes (with c4), works with MFT and Sabos. Needs M27 Unlocked. Not a fit for the teleported universe itself, but its portrait/asset pack and the Portrait ID / AssetName list are the model to copy (`1544458834733568150`).
- **UltraSim Gameplay Mod v1.1.0** (Buhbooh): CPU-vs-CPU tuning for SlowSim. Settings: All-Pro, 15-sec accelerated clock, 15-min quarters, Superstar abilities off, Wear and Tear on, timing catching off. Not compatible with other gameplay mods (`1536489217524367460`).
- **Jolly Popper's Sim Stat Improvement Mod** (beta, 19 KB `.fbmod`): small sim-stat tweak; the author says Sabos's franchise mod is better (`1545589383653228554`).
- **Madden Trainer (M27) v3.0.0** (Buhbooh): weekly-training progression in place of XP (zero XP sliders) (`1540538723392032899`). MFT's Progression Tool is the other option. Project Mu's own engines decide progression, so pick at most one, or none.
- **Retirement Arbitrator** (M27 only): snapshot at Super Bowl week; anyone who retires with 2+ contract years left goes back to his team (`1559088719158579210`).
- **Slow Sim Automation Tool** (RedDobe, M26): AutoHotkey plus image recognition that slow-sims a whole week for a 32-team user franchise (`1524524174889062580`). Relevant if the universe is ever slow-simmed instead of super-simmed.
- **Gridiron Franchise Editor** (NYG 4 LIFE): sims, a historical database of 32,000 players, league leaders, weekly news (`1560631304377081907`).
- **M26 → M27 Franchise Transfer Tool** (beta, AI-built) with WiiMaster's **M26 → M27 Roster Transfer Tool**: carries a franchise into a new Madden year. Worth watching for the M27 → M28 move (`1549998306728611850`, `1537331814845255680`).
- **Sinthros M27 Coach Creator Tool** and Evan's **Create a Free Agent Coach Tool**: add free-agent coaches. Relevant to the coaching carousel if a Project Mu coach has no Madden counterpart (`1559602977352847522`, `1548135052616142869`).
- Quality-of-life: No Injury Timeouts, Halftime Highlights OFF, No VRAM Warning, No Offline Warning, No Bootflow, MUT Remover, Disable Keyboard/Mouse Mod.
- Presentation for a broadcast feel (§9): icicle22's CBS / FOX / ESPN MNF / NBC SNF / Prime TNF presentation mods, NFL Broadcast Scorebugs, Real Broadcast Music, Madden 27 Stadium Experience Mod (aholbert32).

## 4. Portraits (for §9.4)

How the community does it in M27:

- A player's portrait and cyberface are chosen by two franchise fields, **Portrait ID** and **AssetName** (example from the RFM discussion: Jeremiah Smith, Portrait ID 12313, AssetName `smithjeremiah_17096`). A CFB export gives the same player different values, so mods ship ID lists and patchers to sync them.
- MFT can **import a custom portrait** on a player's edit screen; the portrait file is keyed by the Portrait ID (RFM ships its portrait PNGs for this).
- **Bulk Draft Class Portrait and Cyberface Assignment** (Kevin, M26) sets Portrait ID / cyberface from a default list or your own CSV (`1477387008015073360`); a community *RFM Custom Draft Class Sync Tool* (single HTML page) does the same for CFB classes.
- **Old Style Portraits v1.2** (WiiMaster) and **Updated Roster/Portraits/Dev. Traits** (Keanu Steez) show portrait sets can be replaced wholesale with texture mods.

So the §9.4 spike could take either of two routes: (a) the MFT custom-portrait import (portraits stay in MFT's folder; test whether Madden's own screens show them), or (b) MMC Modding Tools texture replacement keyed by Portrait ID, with `save_writer` writing each player's Portrait ID. Open question: does the MFT import reach Madden's in-game cards, or only MFT?

## 5. Cautions

- Every franchise tool says: back up the file, save and quit to the main menu first, offline franchises only.
- MFT progression is keyed by the franchise file **name**; Presentation ID changes after progression re-roll paths.
- Gameplay mods (Sabos Gameplay, UltraSim, EJ) don't combine with each other.
- Most downloads are unsigned `.exe` files from Discord, MediaFire, Smash or GitHub. Get them from the posts above, not from mirrors.
