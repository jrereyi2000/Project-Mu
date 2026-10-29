# Madden 27 Mod Install (PC)

Set up 2026-10-29 from `PC_MODS/` (untracked downloads; see [`MMC_MOD_SURVEY.md`](MMC_MOD_SURVEY.md) for what each mod is and where it came from). Install steps follow the MMC Modding Tools v1.1.0.7 ReadMe. The MMC Discord thread JR linked (`1221157058447933530`, *TBRs MyFranchise Tool*) could not be re-read during setup because Discord was logged out; the survey's notes on it still apply. Conflicts were checked by reading every mod's asset list with `python3 -m tools.fbmod_conflicts <folder>`.

## 1. Where things are

| What | Windows path |
|---|---|
| MMC Modding Tools (Mod Manager + Editor) | `C:\Madden27Modding\MMC_Modding_Tools_v1.1.0.7\` |
| All 71 mods, pre-imported | `C:\Madden27Modding\MMC_Modding_Tools_v1.1.0.7\MMC_ModManager_v1.1.0.7\Mods\Madden27\` |
| Franchise tools and installers (not run) | `C:\Madden27Modding\Tools\` |
| Madden 27 | `C:\Program Files\EA Games\Madden NFL 27\` |
| Mod Manager config (packs) | `C:\Users\jrere\AppData\Local\MMC\manager_config.json` |

The tools have to be on the same drive as the game. Sabos Gameplay Mod 1.0–1.2 (`Old/`) were left out; 1.2.1 replaces them. Fox Scorebug 3.0 was deleted by JR in favour of SimLeagueTV's NFLScorebugs FOX v3.

## 2. Steps JR does by hand

These replace an EA anti-cheat file and run unsigned downloads as Administrator, so they are not automated. Steps 1–2 done 2026-10-29: the MMC launcher is in place (EA's original kept as `_EAAntiCheat.GameServiceLauncher.exe`), and the first modded launch ran with a `Default` pack holding only No VRAM Warning; the Mod Manager then wrote `CryptBase.dll` and `ModData\` into the game folder.

1. **Swap the anti-cheat launcher** (ReadMe step 2–3). In `C:\Program Files\EA Games\Madden NFL 27\`, rename `EAAntiCheat.GameServiceLauncher.exe` to `_EAAntiCheat.GameServiceLauncher.exe`, then copy in the one from `C:\Madden27Modding\MMC_Modding_Tools_v1.1.0.7\AC_(PUT IN CFB27 or M27 FOLDER)\`. The game folder's existing `*_b` files are EA's own backups; leave them.
2. **Mod Manager as Administrator:** right-click `MMCModManager.exe` → Properties → Compatibility → *Run this program as administrator*. On first launch pick Madden 27; the 71 mods should already be listed (if not, *Add Mod* and select them from the `Mods\Madden27` folder).
3. **Enable the mods for a pack** in the order of §3, *Apply*, then *Launch*. Offline only: mods are not allowed in online modes. To play online again, put the original launcher back and delete `CryptBase.dll` if the game folder has one (ReadMe §4).
4. **Installers** in `Tools\` (MyFranchise Tool 2.0.1, Madden Franchise Editor 4.6.0, RHI, Pipeline 0.5.0) and the franchise utilities (`seasonYearSetup`, `trimFreeAgents`, `draftPickReset`, `clearTeamStaffAbilities`, `CreateCoachM27`, Contract Retirement Arbitrator, `SabosAutoSubSliderImport`) are run when needed, per the migration plan section that uses them.

## 3. Load order and what can't be on together

In the Mod Manager's applied list, a mod with higher priority wins any asset it shares with a lower one. The table below runs from highest priority to lowest. Groups marked **one at a time** replace the same assets wholesale; JR toggles between them per game.

| # | Group | Mods | Notes |
|---|---|---|---|
| 1 | Stadium attachments | the 21 `<Team> EA Sign Removal / Stadium Attachment` mods, Jets HOF Ad Attachment | Built to sit above the base Stadium Experience mod. They share generic ad textures (mostly identical files), so their order among themselves doesn't matter |
| 2 | Stadium | Madden 27 Stadium Experience Mod 2.3, Madden 27 Jumbotron Mod 2.7 | Share one Cardinals ribbon board; same author |
| 3 | Crowd | Dynamic Crowd Density Tier 1–4 | **One at a time** (README: overlapping crowd assets) |
| 4 | Kick meter | Shifted Kick Meter 1.0 | Must sit above the scorebug: both scorebug packs also edit the kick meter widgets |
| 5 | Scorebug | M27_NFLScorebugs_CBS_v3 / _FOX_v3 | **One at a time** (319 shared assets) |
| 6 | Broadcast music | Real Broadcast Music CBS / FOX Primetime 1.4 | **One at a time** (153 shared). Sits above the presentation mods so its primetime cues win (60+ shared music assets) |
| 7 | Presentation | m27 MNF, SNF, TNF 2.3; m27 CBS 2.3.1 / FOX 2.3 | CBS and FOX are **one at a time** (34 shared intro/replay videos). MNF/SNF/TNF can stay on with either; they only share end-of-game and Super Bowl music |
| 8 | Logos | CBS / FOX NFL Team Logos 1.4 | **One at a time** (361 shared) |
| 9 | Cutscenes / camera | DOF Enabler, More Gameday Cutscenes | Share 10 camera presets; DOF Enabler above keeps its depth-of-field settings. Same author; check in game |
| 10 | Visuals | BetterGrass Medium, No Film Grain+, lighting overhual, Noblurry, M27 yellow first down marker | Noblurry shares the field grass/turf settings with BetterGrass and the global visual environment with No Film Grain+; keep it at the bottom of this group so the others win |
| 11 | Ball | Mudded Wilson NFL Ball / BCA Pink Oct | **One at a time** (football texture). BCA Pink is October only |
| 12 | Players | M27 Unlocked 0.84B, Hair Fix and Faces, Cyberface Arch Manning, Cyberface Dante Moore, UA-LV Justin Jefferson, JJefferson UA Blur Smoke, smallpads27_2 | Share only the player-head bundle table, which the Mod Manager merges. M27 Unlocked is required by several of these |
| 13 | Audio | Immersive Audio Overhaul, Realistic Stadium Sounds 0.4, Realistic Entrance Music 2.0, Normalized Holding Audio 1.0 | No overlaps |
| 14 | Sim / franchise | Sabos Franchise Mod 1.3, Sabos Gameplay Mod 1.2.1, Roster Size Limit increase 1.1, Special Teams Formations | Franchise and Gameplay share 8 identical tuning chunks and one merged database chunk with the roster mod: no conflict. Roster size only takes effect in a new franchise (PC_MIGRATION_PLAN §4.2) |
| 15 | Gameplay alt | autoplaycall (EJ) | Shares one chunk with Sabos Gameplay, and Sabos says not to combine gameplay mods: **one at a time** with Sabos Gameplay |
| 16 | Menus / UI | Madden120FPSMenus / 60FPSMenu 1.1, MUT Remover 1.6, No Offline Warning 1.0.2, No VRAM Warning, Halftime Off 1.4 | The two FPS menu mods are **one at a time**; 120 matches the NVIDIA 120 FPS cap (plan §3). MUT Remover's one shared chunk is merged |

## 4. Switching mods per game (idea, not built)

JR wants the scorebug/network, crowd tier and ball (BCA Pink in October, maybe on chosen players only) picked automatically per game from team popularity and the matchup. What the tools allow:

- `MMCModManager.exe -launch <pack name>` launches the game with a saved pack (strings in the binary; error text "Unable to find pack with name {0}").
- Packs are kept in `C:\Users\jrere\AppData\Local\MMC\manager_config.json` under `Games.Madden27.Packs`, one string per pack: `"<file>.fbmod:True|<file>.fbmod:False|..."` (`True` = enabled), listed from **lowest priority to highest**, so the last mod in the string wins a shared asset (checked 2026-10-29: a pack with MUT Remover lowest and More Gameday Cutscenes highest was saved as `MUTRemover1.6.fbmod:True|No VRAM Warning.fbmod:True|More Gameday Cutscenes.fbmod:True`). `Games.Madden27.Options.SelectedPack` naming the active one. Edit it only while the Mod Manager is closed.
- So one approach: save a pack per combination (e.g. `FOX-Tier1-Mudded`), or rewrite one pack's enabled list in `manager_config.json`, then launch with `-launch`. Project Mu would choose the pack from the week's schedule (network by kickoff window, crowd tier by team popularity/record, October → BCA).
- Mods are applied at launch, so a change needs a game restart; it fits between games, not mid-game.
- Pink gear on only some players would mean editing the BCA mod in MMC Editor (it replaces shared textures, so it is all-or-nothing as shipped).

To write a pack from the table in §3, reverse it: the table's last row goes first in the string.
