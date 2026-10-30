# Project Mu: Mod Builder Plan

**Status:** built and on main (2026-10-30). Approved by JR 2026-10-30; wave 0 spike (S-c passed, S-a answered the admin question, S-b dropped), wave 1 U1 + U2, wave 2 U3.
- As built (U1): the asset index parses `madden27.cache` record by record (1,451,484 ebx and 473,182 res for build 3468304; layout in `asset_index.py`) and keys on `(name, kind)`. `tint` keeps each pixel's saturation and value (not "hue and saturation from color"). `install` runs `check` first, refuses the `Mu Base` pack, and installs as `Mu_<CamelSlug>.fbmod`. Added env `MU_MOD_BUILDS`. The Jaguars teal jersey is `.../Jaguars/U_Jersey_Jaguars_2024_tea`, not `_teal`.
- As built (U3): `/build-mod` (`.claude/skills/build-mod/SKILL.md`) and `docs/reference/MOD_BUILDING.md`. The window route is a fallback outline only (S-b was dropped), not the click-by-click runbook §2.5 describes. The skill previews textures with `harness.run` + `recolor_png` before `build`, because `build` imports with no pause. It also runs `tools.fbmod_conflicts` before the check list: **Mu Base already enables LarrySznn's "M27 yellow first down marker"**, which replaces the same texture, so JR has to untick it in Mu Test to see ours. `find` globs match the whole name (`*...*`). Run the tools from the main checkout (the index and build root are relative to it).
- The initfs key (2026-10-30): no `madden27.key` exists on JR's PC. When it is missing, MMC Editor's SplashWindow opens a KeyPromptWindow, then writes `<editor>\madden27.key` and calls `KeyManager.AddKey`. JR enters the key once (from the MMC community). Until then, ebx reads fail; textures are fine.
- The yellow build folder was finished by hand after U2's manual run: `recipe.json` was added and the mod was copied to `yellow_first_down.fbmod`, so `python3 -m tools.mod_builder check yellow_first_down` passes (7 resources) and `install yellow_first_down` will work. Not installed yet.
- As built (U2): texture import is our own code and keeps BC7 through the editor's `dxtex.dll` with no window. `.fbproject` load calls FrostyProject's private `LoadTaskBody` by reflection. The yellow first-down mod was built headless: `data/mod_builds/yellow_first_down/Mu_YellowFirstDown.fbmod` (2,132 bytes, 1 res + 1 chunk, both FMENC001). The spike's DDS had red and blue swapped (RGBA bytes with BGRA masks), so U2 used a fixed DDS. **`ebx-dump` / `ebx-set` need the game's initfs key** (`<editor>\madden27.key` or `MU_MMC_KEY_FILE`; the profile has `RequiresKey`). Without it, ebx can't be read; textures are unaffected. R5 at type level: `CharacterUniformJerseyItem` has no colour field, only texture pointers (`JerseyColorMap`, ...) and an `ItemPalette` pointer to shared palette assets, so per-team pink is most likely a `JerseyColorMap` texture recolour. Added `--project`, `--limit`, env `MU_MADDEN_GAME_DIR`, `MU_MMC_KEY_FILE`, `MU_MMC_HARNESS_OUT`.
**Builds on:** [`MOD_REVERSE_ENGINEERING.md`](../reference/MOD_REVERSE_ENGINEERING.md) §1, §5, §6; [`MOD_INSTALL.md`](../MOD_INSTALL.md) §1, §4; PROJECT_SPEC §1.1 (`tools/fbmod_inspect.py`, `pipeline/mod_packs.py`).

**Goal:** JR describes a mod idea ("make the first-down line yellow", "give the Jaguars and Vikings pink home jerseys") and Claude builds a working `.fbmod` with MMC Editor, puts it in a test pack, and tells JR how to check it in game. There are two ways to drive the editor: Claude works the editor's window with computer-use, or a small console program runs MMC's own libraries with no window. The window route works for anything the editor can do. The console route is for the steps that repeat (texture import, colour edits, export). Both end at the editor's own export, the only thing that makes mods the Mod Manager accepts (payloads are `FMENC001`-encrypted, MOD_REVERSE_ENGINEERING §1). This is only for JR's own mods: it never decrypts other authors' mods and never copies MMC's DLLs anywhere.

**Delivers:**
- `/build-mod <idea>` skill: a recipe from the idea, a choice of route, the build, install into a `Mu Test` pack, and a check list for JR
- `tools/mod_builder/` (Python, WSL): the recipe format, a search over the game's asset index, texture recolouring, build folders, install into the Mod Manager
- `tools/mmc_harness/` (C#, .NET Framework 4.8, built with Windows' own `csc.exe`): `MmcHarness.exe` with `info`, `find`, `export-texture`, `import-texture`, `ebx-dump`, `ebx-set`, `export-mod`
- `docs/reference/MOD_BUILDING.md`: the window runbook, recorded click by click during the spike, plus how the routes fit together

**JR's answers (2026-10-30):**
- D1 (with the approval): anything that needs admin is flagged to JR, who sets it up once so it doesn't prompt every time. The same goes for the MMC Mod Manager. Preferred order (S-a): (1) check whether the app really needs admin, or only asks for it: run it with `__COMPAT_LAYER=RunAsInvoker`, and if it fails only on writes to `C:\Program Files\EA Games\Madden NFL 27\`, grant JR's account write access to that folder (`icacls`) instead of elevating; (2) only if it truly needs admin, a Task Scheduler task with "Run with highest privileges", started with `schtasks.exe /run /tn <name>` (no prompt). Turning UAC off globally is not an option. Note: computer-use can't send clicks to an elevated window, because Windows blocks input from a normal process to an admin one. So for the window route the editor has to run *without* admin, which makes (1) the target.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | Every payload in all 74 mods is `FMENC001`-encrypted, and only MMC Editor's export writes valid mods (`docs/reference/MOD_REVERSE_ENGINEERING.md:15`, `:179`). | Both routes must end in MMC's own export code. No Python `.fbmod` writer in this plan. |
| S2 | `MMCEditor.exe` is a .NET Framework 4.8 WPF app with no command-line export. Export is the menu command `ExportModMenuItemCommand`, and its strings include `LoadProject`, `SaveProject`, `EncryptionKey` (seen 2026-10-30 with `strings`). | The window route needs no new code. The console route has to call the same classes the menu command calls. |
| S3 | `FrostyCore.dll` has `FrostyModWriter`, `FrostyProject`, `WriteToMod`; `FrostySdk.dll` has `ProfilesLibrary`, `FileSystemManager`, `EbxAsset`; `Plugins/TexturePlugin.dll` has `TextureImporter`, `TextureExporter`; `FrostyCore.xml` documents 373 members (all in `C:\Madden27Modding\MMC_Modding_Tools_v1.1.0.7\MMC_Editor_v1.1.0.7\`). | The console route looks doable. MMC Editor is a Frosty Toolsuite fork, whose open-source code shows how these classes are called. Whether MMC's changes keep that working is spike S-c. |
| S4 | Windows has `C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe` and `MSBuild.exe`. `dotnet.exe` is installed but `--list-sdks` printed nothing. | The harness is built with the framework's own `csc.exe` (C# 5) with nothing to install. No SDK-style project. |
| S5 | The editor's `Caches/madden27.cache` is a plain zstd stream (~575 MB unpacked) holding the full asset index (`MOD_REVERSE_ENGINEERING.md:160`). | Asset lookup can run in Python under WSL without the editor open. |
| S6 | `tools/fbmod_inspect.py:122` `parse_fbmod` reads any built mod's resource list. | Each build is checked by reading its output back: it should list the assets the recipe meant to change, and nothing else. |
| S7 | `pipeline/mod_packs.py:206` `write_pack(path, name, entries, select=True)`, `:224` `base_pack`, `:197` `manager_running` (checks `tasklist.exe`, `:39`), `:231` `launch`. Packs live in `C:\Users\jrere\AppData\Local\MMC\manager_config.json` (`docs/MOD_INSTALL.md:13`), mods in `...\MMC_ModManager_v1.1.0.7\Mods\Madden27\` (`:10`). | Installing a build means copying it to that Mods folder and writing a `Mu Test` pack (Mu Base plus the new mod) with existing functions. No change to `mod_packs.py`. |
| S8 | `headshots/portrait_converter/dds.py` has a Pillow-based DDS encoder; `fbmod_inspect.py:326` `dds_to_png`. | Recolouring works on PNG and DDS from this code. The editor does the final Frostbite texture encoding on import. |
| S9 | MOD_REVERSE_ENGINEERING §3 says to open the editor "as Administrator". | A UAC prompt is on the secure desktop, where computer-use can't click. JR may have to start the editor, or the spike has to show it runs without admin. |
| S10 | MOD_INSTALL.md:89: pink gear on only some players is "not built" because BCA replaces shared textures. | Per-player gear is out of scope here (see §1 Out). |

## 1. Scope

**In:**
- A recipe file per mod: title, author, version, a list of operations (`texture-recolor`, `texture-replace`, `ebx-set`, `legacy-replace`), and the route each step uses.
- Asset search over the game's index (`python3 -m tools.mod_builder find <pattern>`).
- Texture recolouring in Python (tint, hue shift, colour map) on PNG exported from the editor.
- The window route: computer-use drives MMC Editor through a runbook recorded during the spike.
- The console route: `MmcHarness.exe` opens the game profile, exports and imports textures, reads and sets ebx fields, saves `.fbproject` files, and exports `.fbmod` through MMC's own writer.
- Install and check: copy the mod into the Mod Manager's Mods folder, write a `Mu Test` pack, read the built mod back with `fbmod_inspect`, give JR a check list for the game.
- First two real mods: the yellow first-down line (one texture), then pink home jerseys for the Jaguars and Vikings as a small mod of our own (two uniform ebx edits).

**Out (non-goals):**
- Decrypting or editing other authors' `.fbmod` files. Tweaking someone's mod means asking for their `.fbproject`, or rebuilding the change ourselves.
- Copying or shipping MMC's DLLs. The harness loads them from the editor's folder at run time.
- A Python `.fbmod` writer (MOD_REVERSE_ENGINEERING §6, "plausible later").
- Meshes, animation, audio, face scans (face scans are their own plan, after this one).
- Per-player gear, and wiring built mods into the weekly per-game packs (`mod_packs plan/write`). Built mods only go into `Mu Test` until JR adds them to Mu Base himself.
- Running the game automatically to check a mod. JR checks in game.

## 2. Design

### 2.1 Recipe (`tools/mod_builder/recipe.py`)

One JSON file per mod in `data/mod_builds/<slug>/recipe.json` (`data/*` is already git-ignored):

```json
{
  "slug": "yellow_first_down",
  "title": "Mu Yellow First Down Line",
  "author": "JR (Project Mu)",
  "version": "1.0",
  "category": "Cosmetic",
  "description": "First-down line yellow, like the broadcasts",
  "route": "harness",
  "ops": [
    {"op": "texture-recolor",
     "asset": "contentshared/football/playart/textures/linecolors/playartlinecolor_firstdownline",
     "recolor": {"mode": "tint", "color": "#FFD400", "keep_alpha": true}},
    {"op": "ebx-set",
     "asset": "content/footballcharacter/items/uniform/jersey/jaguars/u_jersey_jaguars_2024_teal",
     "path": "<field path, found with ebx-dump>", "value": "#FF69B4"}
  ]
}
```

- `load_recipe(path) -> Recipe`, `validate(recipe) -> list[str]` (empty = valid). `route` is `"harness"` or `"gui"`. An op may set its own `route` to override.
- Op kinds: `texture-recolor` (export, recolour, import), `texture-replace` (import a given PNG/DDS), `ebx-set` (field path + value), `legacy-replace` (path + local file).
- `recolor.mode`: `tint` (keep luminance, set hue and saturation from `color`), `hue-shift` (`degrees`), `map` (list of `[from, to, tolerance]`).

### 2.2 Build folder

`data/mod_builds/<slug>/`: `recipe.json`, `work/` (exported and recoloured textures, `ebx-dump` output), `<slug>.fbproject`, `<slug>.fbmod`, `build.log` (every harness call or window step, with time and result), `check.json` (the read-back from 2.6). The Windows side gets the same folder as `\\wsl.localhost\Ubuntu\home\jrereyi\Project-Mu\data\mod_builds\<slug>\`, or a copy under `C:\Madden27Modding\MuBuilds\<slug>\` if the spike shows the editor or harness can't use `\\wsl` paths (R4).

### 2.3 Asset index (`tools/mod_builder/asset_index.py`)

- `build_index(cache_path, out_path)` streams `madden27.cache` through zstd and pulls out asset names with their kind (ebx, res, chunk, legacy). It writes `data/mod_builds/_index/madden27_assets.sqlite` (table `assets(name TEXT PRIMARY KEY, kind TEXT, type TEXT)`; `type` is filled when it can be read and is NULL otherwise). It rebuilds when the cache's size or mtime changes.
- `find(pattern, kind=None, limit=50) -> list[dict]`: glob or substring match.
- The default cache path is `/mnt/c/Madden27Modding/MMC_Modding_Tools_v1.1.0.7/MMC_Editor_v1.1.0.7/Caches/madden27.cache` (env `MU_MMC_EDITOR_DIR` overrides the editor folder).

### 2.4 Console route (`tools/mmc_harness/`)

`MmcHarness.cs` (C# 5, one file), compiled by `tools/mmc_harness/build.sh`, which calls `csc.exe` through WSL interop and writes `C:\Madden27Modding\MuHarness\MmcHarness.exe`. That folder holds only our exe: an `AppDomain.AssemblyResolve` handler loads every MMC DLL from `MU_MMC_EDITOR_DIR` (default the editor folder above), so no MMC file is copied.

Commands. Each one prints one JSON object on stdout (`{"ok": true, ...}` or `{"ok": false, "error": "..."}`), with logs on stderr. Exit 0 means ok.

| Command | Does |
|---|---|
| `info` | Loads the Madden27 profile and the cache. Prints the editor version, game build, and fingerprint. Must match the mods (MOD_REVERSE_ENGINEERING §2.1). |
| `find <pattern> [--kind ebx\|res\|chunk]` | Asset names from the loaded asset manager (the authoritative check for 2.3). |
| `export-texture <asset> <out.png\|out.dds>` | Through `TextureExporter`. |
| `import-texture <project> <asset> <in.png\|in.dds>` | Opens or creates the `.fbproject`, imports through `TextureImporter`, saves the project. |
| `ebx-dump <asset> <out.json>` | The ebx object as JSON (field paths and values), the same shape EbxToXml shows. |
| `ebx-set <project> <asset> <field path> <value>` | Sets one field. Colours are given as `#RRGGBB` and written in the field's own type. Saves the project. |
| `export-mod <project> <out.fbmod> --title T --author A --version V [--category C] [--description D]` | Writes the mod the way `ExportModMenuItemCommand` does, through `FrostyModWriter` with MMC's encryption. |

The Python wrapper `tools/mod_builder/harness.py` has `run(cmd: list[str], timeout=600) -> dict`. It converts paths to Windows form (`wslpath -w`), runs `/mnt/c/Madden27Modding/MuHarness/MmcHarness.exe`, parses the JSON, and raises `HarnessError` when `ok` is false. Env `MU_MMC_HARNESS` overrides the exe path.

### 2.5 Window route

Computer-use on the Windows desktop drives `MMCEditor.exe`, following the runbook in `docs/reference/MOD_BUILDING.md` §2 (open the profile, find an asset in the Data Explorer, export and import a texture, edit an ebx field in the property grid, save the project, *Export Mod* with title, author and version). The runbook is written during spike S-b, one step per screen, with what the screen should show at each step, so a run can tell when it is lost. Rules:
- `/build-mod` asks JR before it takes the screen, and only works on the editor's window.
- If a UAC prompt or a screen the runbook doesn't know appears, stop and tell JR. No clicking around to recover.
- The window route is used for anything the harness can't do yet (S-c fails, or an op kind has no harness command), and to check a harness-made mod visually in the editor's preview.

### 2.6 Install and check (`tools/mod_builder/install.py`)

- `install(fbmod_path, pack="Mu Test")`: refuses while `manager_running()` is true (`mod_packs.py:197`). Copies the mod into the Mods folder (it must not overwrite a file that isn't ours: our mods start with `Mu_`). Writes the pack as `base_pack()` plus the new mod enabled at the top, through `write_pack` (`mod_packs.py:206`, `:224`). Backs up `manager_config.json` first, the same way MOD_INSTALL does.
- `check(fbmod_path, recipe) -> dict`: `parse_fbmod` on the output. Every recipe asset should be present, nothing extra except the chunks and collectors that belong to them, the title, author and version should match, and every payload should be `FMENC001`. The result goes to `check.json`.

### 2.7 CLI and skill

- `python3 -m tools.mod_builder find <pattern> | new <slug> | build <slug> [--route harness|gui] [--dry-run] | install <slug> [--pack NAME] | check <slug>`.
- `build --route gui` doesn't click anything. It prints the runbook steps for the recipe, which the skill then follows with computer-use.
- `.claude/skills/build-mod/SKILL.md`: `/build-mod <idea>`. It writes the recipe with JR, finds the assets, picks the route (harness when every op has a command and S-c passed, otherwise the window), builds, checks, installs into `Mu Test` (asking first), and ends with a check list for JR ("start the Mod Manager, pick Mu Test, launch, look at ..."). Findings go into MOD_BUILDING.md.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Computer-use can't see or drive MMC Editor (a WPF window that may run as admin). | Spike S-a. | JR starts the editor himself and Claude takes over from there. If computer-use can't drive it at all, the window route drops and the harness becomes required. |
| R2 | MMC's fork changed the Frosty classes, so the harness can't load the profile or can't export (for example, the encryption key is only set up by the editor's UI). | Spike S-c, then `export-mod` in U2 with a mod read back by `check`. | Drop U2. The window route does everything, and the skill's speed is the window's speed. |
| R3 | The Mod Manager refuses, or the game crashes on, a mod made by the harness. | JR loads the first harness mod in game (§4 manual check). | Compare against the window-made copy of the same recipe with `fbmod_inspect` and fix the harness's export fields. Until then, export through the window route. |
| R4 | The editor or harness can't read or write `\\wsl.localhost` paths (.NET Framework is strict about UNC paths). | Spike S-c writes a file to a `\\wsl` path. | Build folders get copied to `C:\Madden27Modding\MuBuilds\<slug>\` and back (2.2). |
| R5 | Jersey colours aren't one simple field (they might be texture-driven or spread over several ebx). | `ebx-dump` (or EbxToXml in the editor) on `u_jersey_jaguars_*` against vanilla, in U2's verification or during the window route. | The second mod becomes a texture recolour, or waits until the uniform plugin's fields are understood. |
| R6 | The window route takes over JR's mouse and keyboard while it runs. | Known. | The skill always asks first. Long builds go through the harness. |
| R7 | MMC's licence or community rules on using its libraries from our own program. | JR reads the MMC rules or asks in the Discord. | Window route only. |

**Spike first** (run inline by Claude with JR at the PC before wave 1, about 1–2 hours; the results go into this plan's §2 and into notes that U3 turns into the runbook):
- **S-a:** computer-use `request_access` for MMC Editor. Can it read the screen, click the Data Explorer, type in the search box? Does the editor need admin?
- **S-b:** build the yellow first-down mod by hand through the window: export the texture, recolour it with Pillow in WSL, import it, export the mod. Write down every screen. JR loads it in game. This proves the whole chain and gives us a known-good mod for R3.
- **S-c:** a 30-line `csc.exe` program that loads FrostySdk from the editor folder, initializes the Madden27 profile, prints the game build, and lists one asset name, then writes a file to a `\\wsl` path. Pass means U2 goes ahead. Fail means U2 is dropped and U3 is written for the window route only.

**Spike results (2026-10-30):**
- **Admin (S-a, D1):** neither `MMCEditor.exe` nor `MMCModManager.exe` asks for admin (both manifests are `requestedExecutionLevel level="asInvoker"`). MMC Editor started from WSL with `Start-Process` runs normally (window "Frosty Editor: MMC Edition - 1.1.0.7"). `C:\Program Files\EA Games\Madden NFL 27\` gives `BUILTIN\Users` read/execute only, so anything that writes there (the Mod Manager launching the game with `CryptBase.dll`, the anti-cheat launcher swap) fails without admin. The fix is a one-time grant of Modify on that folder to JR's account, not elevation. Nothing here needs a Task Scheduler task.
- **Screen control (S-a):** computer-use only accepts apps in its Start-menu list, which it reads when the session starts. Start-menu shortcuts "MMC Editor" and "MMC Mod Manager" were added to `%APPDATA%\Microsoft\Windows\Start Menu\Programs\` (2026-10-30). Screen control works from a session started after that.
- **Console route (S-c): passed.** `csc.exe` (v4.0.30319, C# 5, x64) builds against the editor's DLLs, which are loaded from the editor folder by an `AssemblyResolve` handler (none are copied). Spike programs are in `C:\Madden27Modding\MuHarness\spike\` (`Probe.cs`, `Types.cs`, `Fields.cs`, `Calls.cs`, `Init.cs`). The working start-up sequence, about 10 s per run:
  1. `[STAThread]`, then `Environment.CurrentDirectory` = the editor folder.
  2. `ProfilesLibrary.Initialize(new Profile[0])` (loads the embedded `FrostySdk.Profiles.bin`), then `ProfilesLibrary.Initialize("Madden27")`.
  3. Resolve the assembly name `EbxClasses` to `Profiles\MADDEN27SDK.dll` with `Assembly.LoadFile`, cached and guarded against re-entry (without the guard it overflows the stack). Then `TypeLibrary.Initialize(true)`.
  4. `new FileSystemManager(gamePath)`, `AddSource` for each `ProfilesLibrary.Sources`, then `Initialize(null)`. That gives head 3468304 and 20 catalogs; the profile says `RequiresKey`, but no key was needed.
  5. `ResourceManager` and `AssetManager` (`Initialize(true, new AssetManagerImportResult())`): 1,451,484 ebx, using the existing cache.
  6. Set the `Frosty.Core.App` globals (`AssetManager`, `ResourceManager`, `FileSystemManager`, `Logger`, `IsEditor`, `SelectedProfile`, `NotificationManager`, and `PluginManager` with `PluginManagerType.Editor` and `Initialize()`), plus `Config.Load` on a file holding `{}`.
- **What works headlessly:**
  - Looking up the first-down texture (`TextureAsset`, 4×512 `BC7_UNORM`, 10 mips; orange `(252,106,41)`).
  - `TextureExporter.Export(tex, png, "*.png")`.
  - `FrostyProject.Save` and `FrostyProject.WriteToMod(path, GetModSettings())`, with `ModSettings` Title, Author, Version, Category and Description set by property name.
  - Writing to `\\wsl.localhost\Ubuntu\...` paths (R4 retired).
- **What doesn't yet:** `TextureImporter.Import` and `ImportDdsBytes` both run their work inside `FrostyTaskWindow.Show`, a WPF window whose BAML needs the editor's theme resources. It throws without them, so the mod written (262 bytes) is empty. The callback's steps (read from its IL with `Calls.cs`) are all public SDK calls: `FrostyTextureEditor.ConvertImageToDDS`, read the `DDSHeader`, build a `new Texture(...)`, `CalculateMipData`, `AssetManager.ModifyChunk` (or `AddChunk` when `ProfilesLibrary.MustAddChunks`), `Texture.SetData`, set `AssetNameHash`, `AssetManager.ModifyRes`, `EbxAssetEntry.LinkAsset`. **U2 does texture import by making these calls itself, with no window** (Frosty Toolsuite's `TextureImporter.cs` is the reference). Inputs are made in WSL: `data/mod_builds/yellow_first_down/work/firstdown_yellow.png` (hue set to 50°, saturation and value kept) and `firstdown_yellow.dds` (RGBA8, 10 mips, `headshots/portrait_converter/dds.py`).
- **Never call:** the editor DLLs also hold `FrostyModCryptor.Decrypt`, `FrostyModKeyProvider` and `FrostyModReader.GetResourceData`. The harness must not call these, so other authors' mods are never decrypted (§1 Out). U2's code review checks for them.
- **S-b dropped (JR, 2026-10-30).** The harness exports through MMC's own `FrostyProject.WriteToMod`, the same code as the editor's export menu, so JR's in-game check of the harness-made yellow mod covers R3, and no window-made reference mod is needed. The window route stays in the design as a fallback (§2.5), but its click-by-click runbook is written only if a recipe ever needs an op the harness can't do. Screen control still works from a new session (the Start-menu shortcuts are in place).

## 4. Tests (synthetic, no live DB)

- `tests/test_mod_builder.py`:
  - recipe validation (unknown op, missing asset, bad colour, bad route)
  - the three recolour modes on a small synthetic PNG (luminance and alpha kept)
  - asset index on a small synthetic zstd cache built in the test
  - `check` on a synthetic `.fbmod` made with the helpers in `tests/test_fbmod_inspect.py`: missing asset, extra asset, wrong title
  - `install` against a temp `manager_config.json` and Mods folder, with `manager_running` mocked: refuses while it's running, never overwrites a mod that isn't `Mu_`, makes a backup, puts the pack in the right order
- `tests/test_mmc_harness.py`: `harness.run` against a fake exe (a Python script printing JSON): path conversion, the ok/error JSON, the timeout, and the missing exe case.
- The harness itself isn't unit-tested. Its check is U2's Verify on the real PC, plus JR's in-game check (R3).

Run with `pytest -m "not integration" tests/test_mod_builder.py tests/test_mmc_harness.py`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Python mod builder

- **Owns:** `tools/mod_builder/__init__.py` (new), `tools/mod_builder/__main__.py` (new), `tools/mod_builder/recipe.py` (new), `tools/mod_builder/recolor.py` (new), `tools/mod_builder/asset_index.py` (new), `tools/mod_builder/install.py` (new), `tests/test_mod_builder.py` (new)
- **Reads:** this plan §2.1–2.3, §2.6, §2.7; `tools/fbmod_inspect.py`; `pipeline/mod_packs.py`; `headshots/portrait_converter/dds.py`; `docs/reference/MOD_REVERSE_ENGINEERING.md`; `docs/MOD_INSTALL.md` §4
- **Depends on:** none (`build --route harness` calls `tools.mod_builder.harness.run` from §2.4; until U2 is merged that import is lazy and `build` says the harness isn't installed)
- **Done when:** `find`, `new`, `build --dry-run`, `install`, `check` work as in §2. `find firstdownline` against the real cache returns the first-down texture.
- **Verify:** `pytest -m "not integration" tests/test_mod_builder.py` passes, and `python3 -m tools.mod_builder find firstdownline` lists `contentshared/football/playart/textures/linecolors/playartlinecolor_firstdownline`.

### U2: Console harness

- **Owns:** `tools/mmc_harness/MmcHarness.cs` (new), `tools/mmc_harness/build.sh` (new), `tools/mmc_harness/README.md` (new), `tools/mod_builder/harness.py` (new), `tests/test_mmc_harness.py` (new)
- **Reads:** this plan §2.4 and the spike S-c notes; `FrostyCore.xml` in the editor folder; the Frosty Toolsuite source on GitHub for how `ExportModMenuItemCommand`, `FrostyProject` and `TextureImporter` are called
- **Depends on:** spike S-c passing
- **Done when:** on the PC, `info`, `find`, `export-texture`, `import-texture`, `ebx-dump`, and `export-mod` work for the first-down texture, and the exported mod passes `fbmod_inspect`'s read-back (one texture + its chunk, encrypted, right title).
- **Verify:** `bash tools/mmc_harness/build.sh && pytest -m "not integration" tests/test_mmc_harness.py` passes. `/mnt/c/Madden27Modding/MuHarness/MmcHarness.exe info` prints `"ok": true` with game build 3468304 or the current one.

### U3: Skill and runbook

- **Owns:** `.claude/skills/build-mod/SKILL.md` (new), `docs/reference/MOD_BUILDING.md` (new)
- **Reads:** this plan, including the spike results in §3; U1's CLI (`python3 -m tools.mod_builder --help`) and U2's README (merged by then); `.claude/skills/reverse-mod/SKILL.md` for skill style
- **Depends on:** U1, U2 (or U2 dropped after S-c)
- **Done when:** the skill covers §2.7 end to end. MOD_BUILDING.md has the harness route end to end (§2), the harness command table (§3), the window route as a short fallback outline with no click-by-click steps (S-b was dropped), the recipe format (§4), and the in-game check list (§5).
- **Verify:** the skill frontmatter parses (`python3 -c "import yaml,sys; yaml.safe_load(open('.claude/skills/build-mod/SKILL.md').read().split('---')[1])"`), and every CLI command and harness command named in the skill and doc exists (`python3 -m tools.mod_builder --help`, the README table).

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 0 | spike S-a, S-b, S-c (inline, Claude + JR) | — | Decides whether U2 is built, and gives U3 its runbook |
| 1 | U1, U2 | — | No shared files. U1 calls U2 only through `harness.run` (§2.4) |
| 2 | U3 | U1, U2 | Documents what U1 and U2 actually shipped |

After wave 2, Claude builds the two first mods with `/build-mod` (one through each route if both work), and JR checks them in game.

**File ownership check:**

| File | Unit |
|---|---|
| `.claude/skills/build-mod/SKILL.md` | U3 |
| `docs/reference/MOD_BUILDING.md` | U3 |
| `tests/test_mmc_harness.py` | U2 |
| `tests/test_mod_builder.py` | U1 |
| `tools/mmc_harness/MmcHarness.cs` | U2 |
| `tools/mmc_harness/README.md` | U2 |
| `tools/mmc_harness/build.sh` | U2 |
| `tools/mod_builder/__init__.py` | U1 |
| `tools/mod_builder/__main__.py` | U1 |
| `tools/mod_builder/asset_index.py` | U1 |
| `tools/mod_builder/harness.py` | U2 |
| `tools/mod_builder/install.py` | U1 |
| `tools/mod_builder/recipe.py` | U1 |
| `tools/mod_builder/recolor.py` | U1 |

No path appears twice. No `.gitignore` change: build output goes to `data/mod_builds/` (already ignored) and the exe goes to `C:\Madden27Modding\MuHarness\` (outside the repo).

## 7. Spec changes when built

- §1.1 Repository Layout: `tools/mod_builder/` (6 files), `tools/mmc_harness/` (3 files), `.claude/skills/build-mod/`, `docs/reference/MOD_BUILDING.md`, two test files. The `data/` tree gets `data/mod_builds/` (git-ignored build folders and the asset index).
- The "Mod inspection" paragraph after the layout tree becomes "Mod inspection and building", which adds the two routes, the recipe, `Mu Test`, and the rule that only MMC's own export writes mods.
- §1.2 Running the System: rows for `python3 -m tools.mod_builder ...` and `bash tools/mmc_harness/build.sh`.
- Env vars: `MU_MMC_EDITOR_DIR`, `MU_MMC_HARNESS`.

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | Which mod first? | The yellow first-down line: one texture, so a failure points at the tooling and not at the asset. Pink Jaguars/Vikings home jerseys second, since it needs ebx edits (R5). |
| Q2 | Can the window route take your screen when you're not there? | Only after you say yes in that conversation. Never on its own schedule. |
| Q3 | Who checks a mod in game? | You, from the check list. Automating launch-and-look is out of scope until the slider tuner shows whether the game can run unattended. |
| Q4 | Built mods' names and author. | File `Mu_<Slug>.fbmod`, author `JR (Project Mu)`, so `install` can tell our files from downloads and never overwrite someone else's. |
| Q5 | Where built mods go. | A separate `Mu Test` pack (Mu Base + the new mod). You move a mod into Mu Base when you're happy with it. |
| Q6 | MMC's rules on using its libraries from our own tool (R7). | Check the MMC rules or ask in the Discord before U2. Personal, local use with nothing redistributed is the likely answer, but it's their call. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | worktree-agent-aa2d3a27a51197080 | .claude/worktrees/agent-aa2d3a27a51197080 | merged | 4ca5b5e |
| U2 | 1 | worktree-agent-a52c3eaf6fa66de40 | .claude/worktrees/agent-a52c3eaf6fa66de40 | merged | 5ef3a4e |
| U3 | 2 | worktree-agent-a0bb44be154793700 | .claude/worktrees/agent-a0bb44be154793700 | merged | 5608bab |
