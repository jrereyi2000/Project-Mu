# Building Madden 27 Mods

How Project Mu builds JR's own `.fbmod` files: the recipe, the console harness, install into a `Mu Test` pack, and the in-game check. `/build-mod <idea>` (`.claude/skills/build-mod/SKILL.md`) follows this doc. Design and history: [`../done/MOD_BUILDER_PLAN.md`](../done/MOD_BUILDER_PLAN.md). Format of a built mod, and the teardowns of other authors' mods: [`MOD_REVERSE_ENGINEERING.md`](MOD_REVERSE_ENGINEERING.md). Packs and load order: [`../MOD_INSTALL.md`](../MOD_INSTALL.md).

```
python3 -m tools.mod_builder find <pattern> [--kind ebx|res|bundle|legacy|unknown] [--limit N]
python3 -m tools.mod_builder new <slug>
python3 -m tools.mod_builder build <slug> [--route harness|gui] [--dry-run]
python3 -m tools.mod_builder check <slug>
python3 -m tools.mod_builder install <slug> [--pack NAME]          # default pack "Mu Test"; Mod Manager closed
bash tools/mmc_harness/build.sh                                     # once: builds C:\Madden27Modding\MuHarness\MmcHarness.exe
/mnt/c/Madden27Modding/MuHarness/MmcHarness.exe info                # is the harness working, and can it read ebx?
```

The standard recipe examples build JR's own changes. The separately authorized [applied-asset reconstruction workflow](MOD_APPLIED_ASSET_RECOVERY.md) can also produce a fresh project from compiled game assets after normal Mod Manager application. Neither route directly decrypts or edits another author's protected `.fbmod` payloads or copies installed MMC DLLs.

## 1. How the pieces fit

| Piece | Runs on | Does |
|---|---|---|
| `tools/mod_builder/` (Python) | WSL | Recipes (`recipe.py`), asset search over MMC Editor's `madden27.cache` (`asset_index.py`), recolouring and PNG→DDS (`recolor.py`), the build plan and CLI (`__main__.py`), the read-back check and install (`install.py`), the harness wrapper (`harness.py`) |
| `tools/mmc_harness/MmcHarness.exe` (C# 5, .NET 4.8) | Windows, started from WSL | MMC Editor's own Frosty libraries with no window: export/import textures, dump/set ebx fields, save `.fbproject`, export `.fbmod` through `FrostyProject.WriteToMod`. Every MMC DLL is loaded from the editor folder at run time |
| MMC Editor window (`MMCEditor.exe`) | Windows, computer-use | The fallback route (§6) for anything the harness can't do |
| `pipeline/mod_packs.py` | WSL | Pack read/write in `manager_config.json`, used by `install` |
| MMC Mod Manager | Windows, JR | Applies the `Mu Test` pack and launches the game |

**Why the export has to be MMC's own.** Every payload in every MMC-built mod is `FMENC001`-encrypted (MOD_REVERSE_ENGINEERING §1), and the Mod Manager only decrypts that format. We don't write `.fbmod` files ourselves and we don't touch the encryption: both routes end in MMC's own export code (`WriteToMod`, the same call as the editor's *Export Mod* menu), so our mods come out encrypted exactly like editor-made ones. Our tools only read a built mod's **header** back (`tools/fbmod_inspect.py`), never its payloads.

**Data flow (harness route):** recipe.json → `build` plans the steps → harness `export-texture` writes a PNG into `work/` → Python recolours it and writes an RGBA8 DDS → harness `import-texture` re-encodes it to the texture's own format and saves `<slug>.fbproject` → harness `export-mod` writes `<slug>.fbmod` → `check` reads the header back into `check.json` → `install` copies it into the Mods folder as `Mu_<Name>.fbmod` and writes the `Mu Test` pack.

**Build folder:** `data/mod_builds/<slug>/` (git-ignored; `MU_MOD_BUILDS` overrides the root): `recipe.json`, `work/`, `<slug>.fbproject`, `<slug>.fbmod`, `build.log` (every harness call and local step with time and result), `check.json`. Run the CLI from the main checkout (`/home/jrereyi/Project-Mu`): the build root and the asset index (`data/mod_builds/_index/madden27_assets.sqlite`) are relative to the checkout, so a git worktree gets its own empty ones unless `MU_MOD_BUILDS` points at the main one. The harness reads and writes `\\wsl.localhost\...` paths directly (spike S-c), so nothing is copied to `C:`.

**Env.** WSL side: `MU_MOD_BUILDS`, `MU_MMC_EDITOR_DIR` (WSL path of the editor folder, for the asset index), `MU_MMC_HARNESS` (exe path for `harness.py`), `MMC_MANAGER_DIR`, `MMC_CONFIG`. Harness (read by the Windows process, so set from WSL only through `WSLENV`): `MU_MMC_EDITOR_DIR` (Windows path), `MU_MADDEN_GAME_DIR`, `MU_MMC_KEY_FILE`. `build.sh`: `MU_MMC_EDITOR_DIR`, `MU_MMC_HARNESS_OUT` (both Windows paths). The defaults fit JR's PC; none needs setting. Note that `MU_MMC_EDITOR_DIR` is a WSL path for the asset index but a Windows path for `build.sh` and the harness, so set it per command, not globally.

## 2. The harness route end to end: the yellow first-down line

The first real mod, built headless on 2026-10-30 (U2). One texture, `contentshared/football/playart/textures/linecolors/playartlinecolor_firstdownline`: a 4×512 `BC7_UNORM` swatch, 10 mips, vanilla orange `(252,106,41)`.

**1. Find the asset.**

```
$ python3 -m tools.mod_builder find firstdownline
ebx     TextureAsset                 ContentShared/football/PlayArt/textures/lineColors/PlayArtLineColor_FirstDownLine
res     Texture                      contentshared/football/playart/textures/linecolors/playartlinecolor_firstdownline
```

The first run in a checkout builds the index (2,252,240 names). A plain pattern is a substring; a pattern with `*`, `?` or `[` is a glob on the **whole** name (`u_jersey_jaguars_2024*` finds nothing, `*u_jersey_jaguars_2024*` works). Matching is case-blind; the harness accepts either spelling.

**2. Start the recipe and fill it in.** `python3 -m tools.mod_builder new yellow_first_down` writes a template with no ops; edit it to:

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
     "recolor": {"mode": "tint", "color": "#FFD400", "keep_alpha": true}}
  ]
}
```

**3. Check the harness.** `MmcHarness.exe info` (28 s on 2026-10-30):

```json
{"ok":true,"editor_version":"1.1.0.7","profile":"Madden27","data_version":20260612,"game_build":3468304,
 "catalogs":20,"ebx_count":1451484,"must_add_chunks":false,"requires_key":true,
 "key_file":"C:\\Madden27Modding\\MMC_Modding_Tools_v1.1.0.7\\MMC_Editor_v1.1.0.7\\madden27.key",
 "key_file_present":false,"initfs_files":0,"ebx_readable":false,"cache_build":3468304,
 "fingerprint":"f7fd7b7b82ebb659d33809e907f61fd38a3df855","harness_version":"1.0", ...}
```

That run was before 2026-10-31; `ebx_readable` is now true (§3). A texture mod never needs it.

**4. Dry run.** `python3 -m tools.mod_builder build yellow_first_down --dry-run` (paths shortened):

```
yellow_first_down: 6 steps, route harness (dry run)
  harness: MmcHarness.exe info
  harness: MmcHarness.exe export-texture contentshared/.../playartlinecolor_firstdownline .../work/playartlinecolor_firstdownline.png
  local:   recolor {"mode": "tint", "color": "#FFD400", "keep_alpha": true} .../work/playartlinecolor_firstdownline.png -> .../work/playartlinecolor_firstdownline_recolor.png
  local:   png_to_dds .../work/playartlinecolor_firstdownline_recolor.png -> .../work/playartlinecolor_firstdownline_recolor.dds
  harness: MmcHarness.exe import-texture .../yellow_first_down.fbproject contentshared/.../playartlinecolor_firstdownline .../work/playartlinecolor_firstdownline_recolor.dds
  harness: MmcHarness.exe export-mod .../yellow_first_down.fbproject .../yellow_first_down.fbmod --title "Mu Yellow First Down Line" --author "JR (Project Mu)" --version 1.0 --category Cosmetic --description "First-down line yellow, like the broadcasts"
```

Four harness calls, so about two minutes.

**5. Build.** `python3 -m tools.mod_builder build yellow_first_down` runs those steps, logs each to `build.log`, and ends with the `check`. The pixels at each stage (read with Pillow, and looked at with the Read tool):

| File | Main colour |
|---|---|
| exported vanilla PNG | `(252,106,41)` orange |
| recoloured PNG (tint to hue 50°, S and V kept) | `(252,217,41)` yellow; the lighter edge rows `(253,180,147)` became `(253,235,147)` |
| read back from the project after import (`export-texture <asset> <out.png> --project <slug>.fbproject`) | `(253,217,41)`: BC7 kept, one step of rounding |

The output: `Mu_YellowFirstDown.fbmod`, 2,132 bytes. Its header, from `python3 -m tools.fbmod_inspect <file>`:

```
Mu Yellow First Down Line 1.0  by JR (Project Mu)  [Cosmetic]
  profile Madden27, game build 3468304, format v9, 2,132 bytes, 2 payloads (2 encrypted)
  [   0] res      texture (field overlay)   184 /   264 Texture  contentshared/football/playart/textures/linecolors/playartlinecolor_firstdownline
  [   1] chunk    texture pixels              - / 1,304 logical 4,112 -> .../playartlinecolor_firstdownline  405c35fb-...
```

One res and its chunk, both `FMENC001`: the same shape as LarrySznn's *M27 yellow first down marker* (MOD_REVERSE_ENGINEERING §4).

**6. Check.** `python3 -m tools.mod_builder check yellow_first_down` prints `OK: Mu Yellow First Down Line v1.0 by JR (Project Mu), build 3468304, 7 resources` (the count includes the icon/screenshot records) and writes `check.json` with empty `missing`, `extra`, `details_mismatch` and `unencrypted`, and `"ok": true`.

**7. Install** (§5), with JR's OK and the Mod Manager closed: `python3 -m tools.mod_builder install yellow_first_down`.

*As built:* U2 ran the harness calls by hand before `build` existed, so the real folder `data/mod_builds/yellow_first_down/` holds `Mu_YellowFirstDown.fbmod`, `yellow_first_down.fbproject` and `work/` (`firstdown_original.png`, `firstdown_yellow.png`, `firstdown_imported_check.png`, a spike DDS with red and blue swapped, and the fixed one) but no `recipe.json`, and `build` names its output `<slug>.fbmod`. To bring it under the CLI, write the recipe above into that folder and run `build` once. The `check` result above was run on a copy of that mod in a scratch build root.

## 3. Harness commands (`MmcHarness.exe`)

Each call prints one JSON object on stdout (`{"ok": true, ...}` or `{"ok": false, "error": "..."}`), logs on stderr, exit 0 when ok. Each call loads the whole asset index first: **13–47 s a call**. From WSL, `tools/mod_builder/harness.py` `run(cmd, timeout=600)` converts the path arguments with `wslpath -w` and raises `HarnessError` on failure:

```
python3 -c "from tools.mod_builder.harness import run; print(run(['ebx-dump', '<asset>', 'data/mod_builds/<slug>/work/<stem>.json']))"
```

| Command | Does | Returns |
|---|---|---|
| `info` | Loads the profile, file system and cache | `editor_version`, `profile`, `data_version`, `game_build`, `catalogs`, `ebx_count`, `must_add_chunks`, `requires_key`, `key_file`, `key_file_present`, `initfs_files`, `ebx_readable`, `cache_build`, `fingerprint`, `harness_version` |
| `find <pattern> [--kind ebx\|res\|chunk\|legacy] [--limit N]` | Asset names from the loaded asset manager (default limit 200). `*`/`?` makes it a case-blind glob on the whole name, else a substring. `--kind legacy` lists legacy file paths through MMC's legacy file manager | `count`, `truncated`, `matches[{name, kind, type}]` |
| `export-legacy <legacy/file/path> <out.bin>` | A vanilla legacy file's bytes (Lua, XML, JSON, `.ast`, ...), read only | `path`, `type`, `size`, `legacy_manager` |
| `export-texture <asset> <out.png\|.dds\|.tga\|.hdr> [--project P]` | Through TexturePlugin's `TextureExporter`. With `--project`, the texture as that project has it (the read-back after an import) | `out`, `bytes`, `texture{width, height, depth, mips, first_mip, format, type, flags, chunk}` |
| `import-texture <project> <asset> <in.png\|.tga\|.hdr\|.dds> [--raw]` | Opens the `.fbproject` (or starts one), imports, saves. The texture keeps its own pixel format: PNG/TGA/HDR are encoded with the editor's `dxtex.dll`, mips generated; a DDS in the same format goes in as is, another format is decoded and re-encoded. `--raw` keeps a DDS's own format. 2D textures only | `original`, `imported` (texture info), `note`, `modified_assets` |
| `ebx-dump <asset> <out.json> [--project P]` | The ebx root object as JSON (`$type`, `$guid`; internal pointers inline, `{"$ref": path}` on repeats; external pointers `{"$external": name, file_guid, class_guid}`). **Needs the initfs key** | `type`, `out`, `fields`, `color_fields` (first 50 paths of every Vec3/Vec4 and colour/tint field), `color_field_count` |
| `ebx-set <project> <asset> <field.path> <value>` | Sets one field (path like `Materials[0].Color.x`, through internal pointers only), `ModifyEbx`, saves the project. `#RRGGBB`/`#RRGGBBAA` is written in the field's own type (Vec3/Vec4 floats 0–1, or packed ints); else number, bool, enum name, string, GUID. **Needs the initfs key** | `field_type`, `old`, `new`, `modified_assets` |
| `export-mod <project> <out.fbmod> --title T --author A --version V [--category C] [--description D]` | Loads the project, sets `ModSettings`, calls `FrostyProject.WriteToMod`. Refuses a project with no modified assets | `out`, `bytes`, `title`, `author`, `version`, `category`, `modified_assets` |

`MmcHarness.exe --help` (or no command) prints the usage line for each.

**Op kind → harness commands** (`build` plans these; `HARNESS_OPS` in `__main__.py`):

| Op | Steps |
|---|---|
| `texture-recolor` | `export-texture` → local recolour → local `png_to_dds` → `import-texture` |
| `texture-replace` | (a PNG is turned into a DDS locally) → `import-texture` |
| `ebx-set` | `ebx-set` (needs `ebx_readable`, §3) |
| `legacy-replace` | no harness command: always the window route (§6) |

Every harness build starts with `info` and ends with `export-mod`, then `check`.

**The game key (needed for ebx only).** Madden 27's `Data/initfs_Win32` is encrypted (`ProfilesLibrary.RequiresKey`); without the key the initfs isn't loaded and ebx can't be read (textures are unaffected). MMC ships the key inside its own `FrostyCore.dll`: the editor's profile window (`FrostyProfileTaskWindow.Window_Loaded`) adds it as `Key1` with `KeyManager.Instance.AddKey` when the profile needs one, which is why MMC Editor never asks for a key and no `madden27.key` exists (found 2026-10-31). The harness does the same, as JR decided ("mods should be created the way the editor does", 2026-10-31): `EditorBuiltInKey()` finds that method's 16-byte array by its `ldtoken` in the IL and adds it as `Key1`, in memory only (never written, logged or printed). A `madden27.key` in the editor folder (or `MU_MMC_KEY_FILE`) still overrides it, as in the editor. `info` reports `key_source` (`editor_builtin`, `key_file` or `none`) and `ebx_readable`; on 2026-10-31 it showed `editor_builtin`, 159 initfs files, `ebx_readable: true`. This is the game's content key; MMC's separate mod-encryption code (`FrostyModCryptor`, `FrostyModKeyProvider`) stays on the never-call list.

**Never called** (README "Never call", checked by `tests/test_mmc_harness.py`): `FrostyModCryptor`, `FrostyModKeyProvider`, `FrostyModReader.GetResourceData`, or anything that reads or decrypts a `.fbmod`.

## 4. The recipe (`data/mod_builds/<slug>/recipe.json`)

```json
{
  "slug": "pink_home_jerseys",
  "title": "Mu Pink Home Jerseys",
  "author": "JR (Project Mu)",
  "version": "1.0",
  "category": "Cosmetic",
  "description": "Jaguars and Vikings home jerseys in pink",
  "route": "harness",
  "ops": [
    {"op": "texture-recolor",
     "asset": "<the jersey's JerseyColorMap texture, from ebx-dump>",
     "recolor": {"mode": "map", "map": [["#006778", "#FF69B4", 40]], "keep_alpha": true}},
    {"op": "texture-recolor",
     "asset": "<another texture>",
     "recolor": {"mode": "hue-shift", "degrees": 30}},
    {"op": "texture-replace",
     "asset": "<texture>",
     "file": "work/new_texture.png"},
    {"op": "ebx-set",
     "asset": "content/FootballCharacter/Items/Uniform/Jersey/Jaguars/U_Jersey_Jaguars_2024_tea",
     "path": "<field path, from ebx-dump>",
     "value": "#FF69B4"},
    {"op": "legacy-replace",
     "path": "common/database/json/<file>.json",
     "file": "work/<file>.json",
     "route": "gui"}
  ]
}
```

(An illustration of every op kind; the pink jerseys plan is in §7.)

| Key | Rule (`recipe.validate`) |
|---|---|
| `slug` | lower-case letters, digits, underscores (`^[a-z0-9][a-z0-9_]*$`); also the folder name. Installs as `Mu_<CamelSlug>.fbmod` (`yellow_first_down` → `Mu_YellowFirstDown.fbmod`) |
| `title`, `author`, `version` | required, non-empty. Title starts `Mu `, author `JR (Project Mu)` (plan Q4) |
| `category`, `description` | strings (default `Cosmetic`, `""`). The description ends up in `install`'s closing line, so write what JR should see in game |
| `route` | `harness` (default) or `gui`. An op may carry its own `route`; `build --route` overrides all of them. Ops without a harness command always go through the window |
| `ops` | at least one |
| `texture-recolor` | `asset`, `recolor`: `{"mode": "tint", "color": "#RRGGBB"}` (every pixel takes the colour's hue and keeps its own saturation and value, so grey or white pixels stay grey or white), `{"mode": "hue-shift", "degrees": N}`, or `{"mode": "map", "map": [["#from", "#to", tolerance], ...]}` (pixels within `tolerance`, the largest channel difference, of `from` move by `to − from`). `keep_alpha` true/false (default true) |
| `texture-replace` | `asset`, `file` (`.png` or `.dds`, relative to the recipe folder) |
| `ebx-set` | `asset`, `path` (field path from `ebx-dump`), `value` (`#RRGGBB` colours, or a number, bool, enum name, string, GUID) |
| `legacy-replace` | `path` (the legacy file path, from `find --kind legacy`), `file` (local file) |

## 5. Install, in-game check, removal

**Install** (ask JR first; the Mod Manager must be closed):

```
python3 -m tools.mod_builder install <slug>
```

It runs `check` first and stops if it fails. It refuses while `MMCModManager.exe` is open (the Manager keeps its own copy of the packs and would save over the file), refuses `--pack "Mu Base"`, and only ever writes a file named `Mu_*.fbmod`. It copies the mod to `C:\Madden27Modding\MMC_Modding_Tools_v1.1.0.7\MMC_ModManager_v1.1.0.7\Mods\Madden27\Mu_<Name>.fbmod`, backs up `manager_config.json` as `manager_config.backup-<time>-before-mu-test.json`, and writes **`Mu Test` = JR's `Mu Base` pack, in order, plus this mod enabled at the end** (highest priority, so it wins any asset it shares), and selects it. Each install rebuilds `Mu Test` from `Mu Base`, so `Mu Test` holds only the latest built mod.

**Before telling JR to look:** check for mods in `Mu Base` that touch the same assets, because they would hide whether ours works:

```
python3 -m tools.fbmod_conflicts data/mod_builds/<slug>/<slug>.fbmod "/mnt/c/Madden27Modding/MMC_Modding_Tools_v1.1.0.7/MMC_ModManager_v1.1.0.7/Mods/Madden27"
```

Ours is on top and wins, but if the other mod makes the same change the check proves nothing. Example: `Mu Base` has LarrySznn's *M27 yellow first down marker* on, which replaces the same texture with the same colour.

**In-game check list for JR:**

1. Open **MMC Mod Manager** (Start menu). If Windows asks for admin, that's JR's call; Claude never clicks a UAC prompt.
2. Select the **`Mu Test`** pack (install already selected it). The new `Mu_<Name>` mod is the last one and ticked. Untick any mod that `fbmod_conflicts` showed making the same change (for the yellow line: *M27 yellow first down marker*).
3. **Launch.** Wait for the game to load the mods.
4. Go where the change shows, and compare with a game on `Mu Base`. For each mod, the check list names the exact screen, e.g.:
   - yellow first-down line: *Play Now* (exhibition), any teams, any stadium. Before the first snap, the first-down line across the field should be yellow (`#FFD400`-ish), not orange, and stay yellow after each first down.
   - a jersey: *Play Now* with that team at home; the home jersey on the field and in the pre-game walkout. *Not* the menus' jersey pictures, which are separate `imageassetlibraries/.../vnty_*` textures.
5. Report: works / wrong colour / not changed / the game crashed or wouldn't load (and at what point).

If it doesn't show: check that `Mu Test` was the pack applied, that the mod sits last and is ticked, and that the game build in `check.json` (`game_version`) matches `info`'s `game_build`.

**Remove from `Mu Test`:** in the Mod Manager, untick the mod in `Mu Test`, or just pick `Mu Base` (or `Default`); nothing else references a test build. Every `install` rebuilds `Mu Test` anyway. To delete the file itself, with the Mod Manager closed, remove only `Mods\Madden27\Mu_<Name>.fbmod`, and only when JR asks. To undo a pack write, restore the `manager_config.backup-...` copy next to `manager_config.json` (Mod Manager closed). `Mu Base` is JR's: when he likes a mod, he adds it to `Mu Base` himself.

## 6. The window route (fallback outline)

For an op the harness can't do (`legacy-replace`, a cube/array/3D texture, an edit through an external pointer), or to look at a texture in the editor's preview. No click-by-click runbook exists: spike S-b was dropped because the harness exports through the same `WriteToMod` code as the editor's menu. If a recipe ever needs this route, record the screens into this section as you go.

- **Ask JR every time** before taking the screen, in that conversation (JR declined screen access once on 2026-10-30). Only work on MMC Editor's window.
- `python3 -m tools.mod_builder build <slug> --route gui` clicks nothing: it prints the steps (start *MMC Editor* from the Start menu, no admin, open the Madden27 profile; open or create the project; per op: Data Explorer → find the asset → Export/Import, the property grid for an ebx field, the Legacy Explorer for a legacy file; *File → Save Project*; *File → Export Mod* with the recipe's title, author, version, category and description, saved as `<slug>.fbmod`). Run it again after exporting a texture into `work/` and it does the recolour locally.
- computer-use: `request_access` for "MMC Editor" (the Start-menu shortcuts were added for this). The editor doesn't need admin, so input reaches its window.
- **Stop and tell JR** on any UAC prompt, a key prompt (there shouldn't be one: MMC's key is built in, §3), or any screen you can't explain. No clicking around to recover.
- Then `check`, and the rest as in §5.

## 7. Uniforms

What `ebx-dump` showed (2026-10-31, `data/mod_builds/_probe/jaguars_jersey.json`): a jersey asset (`CharacterUniformJerseyItem`, e.g. `content/FootballCharacter/Items/Uniform/Jersey/Jaguars/U_Jersey_Jaguars_2024_tea`, 184 fields) carries its own palette. `ItemPalette` is a `PaletteVisuals` object **inside the jersey's own asset** (internal ids 2, 3, 6), so editing it changes only that jersey. It has `PrimaryColor` and `SecondaryColor` (`ColorVisuals`, each `ColorChannels` a linear-RGB `Vec4` plus `ColorLookup`); `TertiaryColor` and `QuaternaryColor` are empty. Jaguars teal home: primary `(0.0212, 0.0212, 0.0252, 1)` ≈ `#262628` (near-black), secondary `(0, 0.1329, 0.1812, 1)` ≈ `#00666E` (teal). `JerseyColorMap` is an external texture pointer. So a per-jersey colour change is an **`ebx-set` on `ItemPalette.<Primary|Secondary>Color.ColorChannels`** (linear values: convert sRGB to linear first); a texture recolour of `JerseyColorMap` is the fallback if the palette doesn't drive the part JR wants. What BCA Pink Oct changed in its 62 jersey ebx (it calls it the "primary accessory colour") is still unknown; the palette pointer is the likely place (MOD_REVERSE_ENGINEERING §3, §7). The BCA teardown in MOD_REVERSE_ENGINEERING §3 is the map of uniform assets: the jersey ebx folders, and which Jaguars/Vikings sets BCA skips.

Home jerseys (from `find "*u_jersey_jaguars_2024*"` and `find "*jersey_vikings_2024*"`; there is also a `_footballcharacteritems_brt` bundle-ref table per jersey, and the `vnty_*` menu pictures):

- Jaguars: `.../Jersey/Jaguars/U_Jersey_Jaguars_2024_tea` (teal home; not `_teal`), plus `_1gre_teal`, `_1gre_black`, `_1gre_white`, `_2whi*`, `_3bla`.
- Vikings: `.../Jersey/Vikings/U_Jersey_Vikings_2024_purple` (purple home), plus `_1pur_white`, `_2whi`, `_3pur`, `_4pur`, `_silpur`.

**Palettes of the two home jerseys** (2026-10-31; linear `ColorChannels` → sRGB):

| Jersey | Primary | Secondary | Tertiary |
|---|---|---|---|
| `Jaguars/U_Jersey_Jaguars_2024_tea` (teal home) | `#262628` near-black | `#00666E` teal | — |
| `Vikings/U_Jersey_Vikings_2024_purple` (purple home) | `#422D67` purple | `#EBAD33` gold | `#232323` near-black |

The teal jersey's primary is black, so the palette very likely drives the trim, numbers and accents, while the cloth colour comes from `JerseyColorMap`. This is to be confirmed in game.

**Built: `pink_home_jerseys` (2026-10-31, JR chose "pink trim first").** Two `ebx-set` ops set `ItemPalette.SecondaryColor.ColorChannels` on both home jerseys to BCA hot pink `#FF69B4`. **`ebx-set` writes `#RRGGBB` as plain /255 floats, and palettes are linear**, so the recipe passes the linear value `#FF2474` (sRGB `#FF69B4` → linear `(1.000, 0.141, 0.456)`). Passing `#FF69B4` directly would come out as a pale pink. Build: 4 harness calls (info, 2 × ebx-set, export-mod), check OK (7 resources, all FMENC001). Read-back with `ebx-dump --project` shows `(1, 0.1412, 0.4549, 1)` on both. No installed mod touches either jersey (BCA Pink Oct skips both home sets). Installed 2026-10-31 as `Mu_PinkHomeJerseys.fbmod` in `Mu Test` (49 of 68 mods on, this one on top; config backup `manager_config.backup-20261031-114157-before-mu-test.json`). The install worked with Madden running; only renaming the Mod Manager folder is blocked then. In game: not yet checked.

**Next steps for the pink Jaguars/Vikings home jerseys** (ebx is readable now, §3):

1. `info` shows `ebx_readable: true`.
2. `ebx-dump` both home jerseys into `work/` (the Jaguars one is already in `data/mod_builds/_probe/`). Decide with JR which palette slot to make pink (the teal secondary is the likely accent), and try that `ebx-set` first. Read `JerseyColorMap` (its `$external` name is the texture) only if the palette isn't enough. Note whether the two teams' colour maps are shared with other jerseys (the same `$external` name in another jersey's dump means the recolour changes that jersey too).
3. `export-texture` each colour map into `work/` and look at it. If it's a painted texture, recolour with `map` from the team colour (teal / purple) to pink with a tolerance that catches the shading; if it's a mask (grey or channel-coded), `tint` won't work (it keeps saturation) and the recolour has to target the channels instead. Decide with JR.
4. Recipe with two `texture-recolor` ops, `build`, read back with `export-texture --project`, `check`, ask, `install`, check in game (home game for each team).
5. Write what the dump showed (field names, palette layout, what `ItemPalette` holds) back into this section.

## 8. Known limits and open items

- **Game key**: resolved 2026-10-31. The harness loads MMC's built-in key the way the editor does (§3), so ebx works (`key_source: editor_builtin`). If a future MMC build moves the key, `info` will show `key_source: none` and `ebx_readable: false`.
- **2D textures only** through the harness. Cube, array and 3D textures go through the window (§6). Meshes, audio, animation and face scans are out of scope.
- **13–47 s per harness call**, almost all loading the asset index. Each step of a build is its own call, so a one-texture mod is about four calls (two minutes). Batch ops into one recipe (one project) rather than many builds.
- `ebx-set` edits fields of internal objects only; it can't repoint a `PointerRef` or edit through an external pointer.
- `legacy-replace` has no harness command; it always goes through the window route.
- Legacy-format `.fbproject` files, and projects from another editor format version, are refused; open and re-save them in MMC Editor.
- **Mu Test holds one mod at a time** (each install rebuilds it from `Mu Base`).
- The harness-made yellow mod's in-game check by JR is the proof that the Mod Manager accepts harness exports (plan R3). Until JR reports it, treat harness exports as unproven in game.
- The game build matters: a mod built on one build may break on another (Hair Fix, MOD_INSTALL §4). `info` shows the build the harness sees; `check.json` records the build the mod was made for. Rebuild after a game patch.
- The yellow first-down build folder predates the CLI (no `recipe.json`; output named `Mu_YellowFirstDown.fbmod`, §2).
