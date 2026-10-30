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

## 3a. The `Default` pack (matches Shady Mike's list)

Written 2026-10-29 into `manager_config.json` to match the applied list in Shady Mike Gaming's *My COMPLETE Madden 27 Mod List!* (YouTube `pG2SaNC1r30`, the scroll at ~0:56–1:08; MMC Edition 1.1.0.6). His list numbers mods 64 (lowest priority, top) to 1 (highest, bottom) in folders: Miscellaneous, Presentation / Broadcast, Field, Player Likeness, Accessories & Equipment, Jerseys, High Priority. The pack keeps his order and his on/off state for every mod we have; the frame shows his setup for a Prime TNF game. 21 of the 71 mods are on:

- **On:** MUT Remover, Mudded ball, No VRAM Warning, yellow first down marker, lighting overhual, Noblurry, Shifted Kick Meter, Immersive Audio Overhaul, Prime TNF presentation, NFLScorebugs FOX v3 and CBS v3, More Gameday Cutscenes, DOF Enabler, BetterGrass, Stadium Experience, Jumbotron, Hair Fix and Faces, M27 Unlocked, smallpads27_2, Special Teams Formations, autoplaycall.
- **Off, as in his list:** Halftime Off, SNF/MNF/FOX/CBS presentations, both Real Broadcast Music mods, Sabos Franchise Mod and Sabos Gameplay Mod. He uses autoplaycall instead of Sabos Gameplay and keeps it above any gameplay mod.
- **Off, not in his list** (placed in the matching folder's spot so turning one on needs no reordering): BCA Pink Oct, both FPS menu mods, No Offline Warning, No Film Grain+ (above Noblurry), Realistic Stadium Sounds, Realistic Entrance Music, Normalized Holding Audio, CBS/FOX team logos (next to their scorebug), the 22 stadium attachments (above Stadium Experience), the 4 crowd tiers, both cyberfaces, both Justin Jefferson cleats, Roster Size Limit increase.
- **His mods we don't have:** Old Style Portraits, Brandon Gaudin/Charles Davis Only, Modern UI, No Music in Practice, NO Bootflow Video, CM Clean Screen Base and its three CMCS add-ons (player indicator, passing meter, ball landing), CalebWilliamsMustache, RFM Tattoos, NoWeatherVisorRemoval, Modern Practice Attire, Speedflexchinstrap, Turtlenecks and Turtleneck_Replacement, 16 jersey/rebrand mods (all but Updated Mascots Jerseys off), 2017-18 Companion (off), HurtsSplashScreen.
- **Version gaps:** he runs Stadium Experience 2.0, Jumbotron 2.5, Halftime Off 1.3, Sabos Gameplay 1.1 and **autoplaycall v4**; ours are 2.3, 2.7, 1.4, 1.2.1 and an autoplaycall whose header says version 1, so ours is probably older.

How he switches networks (transcript ~15:30–16:45): for a FOX game turn on FOX presentation, FOX Real Broadcast Music and the FOX scorebug and turn off everything CBS; the reverse for CBS. The two scorebugs only replace the main bug, so one can stay on for SNF/MNF/TNF games, but the network music and presentation go off and the primetime presentation goes on. The pack has **both** scorebugs on because that's what the frame shows; CBS (higher) wins where they differ, so untick one.

Where his order differs from §3: the scorebugs sit above Shifted Kick Meter, so the scorebugs' copies of the two kick meter widgets win. Move Shifted Kick Meter above the scorebugs if the shifted meter doesn't show.

His UI folders aren't part of the pack string; they would have to be recreated in the UI.

## 4. Per-game packs (`pipeline/mod_packs.py`)

**Pack format.** Packs live in `C:\Users\jrere\AppData\Local\MMC\manager_config.json` under `Games.Madden27.Packs`, one string per pack: `"<file>.fbmod:True|<file>.fbmod:False|..."` (`True` = enabled), listed from **lowest priority to highest**, so the last mod in the string wins a shared asset (checked 2026-10-29: a pack with MUT Remover lowest and More Gameday Cutscenes highest was saved as `MUTRemover1.6.fbmod:True|No VRAM Warning.fbmod:True|More Gameday Cutscenes.fbmod:True`). `Games.Madden27.Options.SelectedPack` names the active pack. The UI reads the file fresh on every start (checked 2026-10-29: a scripted `MuTest` pack with one mod disabled opened selected, with the same mods, order and on/off states), but while it is open it keeps its own copy and can save it over the file, so the file is only edited while `MMCModManager.exe` is closed. `MMCModManager.exe -launch <pack name>` starts the game with a saved pack. Mods apply at launch, so a switch happens between games, not mid-game. To write a pack from the table in §3, reverse it: the table's last row goes first in the string.

**What a game's pack is.** The hand-kept **`Mu Base`** pack (JR plays from `Default`, so it can't be the base) with three groups switched for the game:

| Group | Rule |
|---|---|
| Broadcast window (`games.game_time`) | Thu from 6 PM → TNF; Mon → MNF; Sat/Sun from 6 PM → SNF (also playoff evening games and the Super Bowl); anything else → **CBS** when the road team is AFC, **FOX** when it's NFC (the NFL's afternoon rule). CBS/FOX: that network's presentation, Real Broadcast Music, scorebug and team logos. Primetime: its own presentation, the **FOX** scorebug and logos (the FOX pack has primetime bugs; JR, 2026-10-29), no network music |
| Ball | BCA Pink when the game's date (`newsroom.social_publish.kickoff`: week 1 opens the Thursday after Labor Day) is in October, Mudded ball otherwise |
| Crowd | Playoff games: **Full** (no crowd mod, Madden's sold-out stands). Regular season: score = 100 × (0.50 home popularity + 0.30 home form + 0.20 opponent popularity) + 8 division game + 6 primetime + 6 week 15+ with the home team at .500 or better. Popularity is the team's rank among the 32 by `team_seasons.popularity_index` (0–1; last season's until this one has values); form is the home team's win share in completed regular-season games before the week, blended with last season's until 4 games are played. Full ≥ 80, Tier 1 (80%) ≥ 54, Tier 2 (70%) ≥ 40, Tier 3 (60%) ≥ 25, Tier 4 (40%) below. Calibrated on 2036 (regular season: Full 12%, Tier 1 38%, Tier 2 24%, Tier 3 18%, Tier 4 8%); 2035 comes out 11 / 42 / 21 / 17 / 9% |

Every other mod keeps the state and position it has in `Mu Base`. Mods are matched by file-name pattern (e.g. `^M27_NFLScorebugs_(CBS|FOX)`), so a newer version of a switched mod keeps working. Each combination gets its own named pack, e.g. `Mu FOX Tier2 Mudded`, `Mu MNF Tier1 BCA`, `Mu SNF Full Mudded`, created only when a game needs it, so the dropdown shows which combination ran.

```
python3 -m pipeline.mod_packs seed-base --from Default          # once: copy a pack into Mu Base (never overwrites)
python3 -m pipeline.mod_packs plan [--season S] [--week W] [--team ABBR]
python3 -m pipeline.mod_packs write --team NYG [--week W] [--dry-run] [--launch]   # or --game <games.id>
```

`plan` lists each game's window, crowd score and pack. `write` builds the game's pack, refuses while the Mod Manager is open (`tasklist.exe`), writes it and selects it; `--launch` then runs `MMCModManager.exe -launch "<pack>"` through `cmd.exe start` (the exe runs as Administrator, so Windows asks for consent; not yet tried). Reads `DATABASE_URL` (default: the active franchise, `db.franchises`), `MMC_CONFIG` (default: the one `manager_config.json` under `C:\Users\*\AppData\Local\MMC`) and `MMC_MANAGER_DIR`. Tests: `tests/test_mod_packs.py`.

Example, 2036 Giants: week 6 MIN @ NYG Mon 8:30 PM → `Mu MNF Tier1 BCA` (score 77.0); week 7 CLE @ NYG Sun 1:00 PM → `Mu CBS Tier1 BCA`; week 17 NYG @ ATL → `Mu FOX Tier3 Mudded` (28.6); wild card NYG @ AZ → `Mu FOX Full Mudded`.

Not built: pink gear on only some players (the BCA mod replaces shared textures, so it is all-or-nothing as shipped; per-player would mean editing it in MMC Editor), and wiring `write` into the save watcher so the next game's pack is ready after each week.
