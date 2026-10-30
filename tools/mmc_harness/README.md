# MmcHarness: MMC Editor without the window

`MmcHarness.exe` is a small console program that runs MMC Editor's own Frosty libraries headless.
It is the "console route" of `docs/in_progress/MOD_BUILDER_PLAN.md` (§2.4): export and import
textures, read and set ebx fields, save `.fbproject` files and export `.fbmod` files through MMC's
own `FrostyProject.WriteToMod` (the same code as the editor's *Export Mod* menu, so payloads come out
`FMENC001`-encrypted the way the Mod Manager wants).

It is for building our own mods only. It never decrypts anyone's mod, and no MMC file is ever copied:
every DLL is loaded at run time from the editor folder.

## Build

```bash
bash tools/mmc_harness/build.sh
```

- Compiler: Windows' own `C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe` (C# 5, `/platform:x64`), run through WSL interop. Nothing to install.
- References, in place: the editor's `FrostySdk.dll`, `FrostyCore.dll`, `FrostyHash.dll`, `Plugins\TexturePlugin.dll`, `ThirdParty\SharpDX*.dll`, plus WPF from the framework folder.
- Output: `C:\Madden27Modding\MuHarness\MmcHarness.exe` and `MmcHarness.exe.config` (outside the repo). Nothing is written into the repo.
- Env: `MU_MMC_EDITOR_DIR` (Windows path, default `C:\Madden27Modding\MMC_Modding_Tools_v1.1.0.7\MMC_Editor_v1.1.0.7`), `MU_MMC_HARNESS_OUT` (output folder).

Run-time env: `MU_MMC_EDITOR_DIR` (same default), `MU_MADDEN_GAME_DIR` (default `C:\Program Files\EA Games\Madden NFL 27`), `MU_MMC_KEY_FILE` (the game key file, default `<editor>\madden27.key`; see Known limits).

## Commands

Each command prints exactly one JSON object on stdout (`{"ok": true, ...}` or `{"ok": false, "error": "..."}`). Logs go to stderr. Exit code 0 means ok. One run takes about 15-45 s, almost all of it loading the asset index.

| Command | Does |
|---|---|
| `info` | Loads the Madden27 profile and the cache. Prints `editor_version`, `game_build` (FileSystem head), `cache_build`, the 20-byte `fingerprint` from the cache header (MOD_REVERSE_ENGINEERING §2.1), `ebx_count`, and `ebx_readable` (whether the game initfs loaded). |
| `find <pattern> [--kind ebx\|res\|chunk] [--limit N]` | Asset names from the loaded asset manager. A pattern with `*`/`?` is a case-insensitive glob over the whole name; otherwise a substring. Returns `count`, `truncated`, `matches[{name, kind, type}]`. |
| `export-texture <asset> <out.png\|.dds\|.tga\|.hdr> [--project P]` | Through `TexturePlugin.TextureExporter`. With `--project`, exports the texture as the project has it. |
| `import-texture <project> <asset> <in.png\|.tga\|.hdr\|.dds> [--raw]` | Loads the `.fbproject` if it exists (a new one otherwise), imports, saves. The texture keeps its own pixel format: PNG/TGA/HDR are encoded with the editor's `dxtex.dll` (`FrostyTextureEditor.ConvertImageToDDS` with `ResolveImportOptions`, mips generated); a DDS in the same format goes in as is; a DDS in another format is decoded to PNG and re-encoded. `--raw` keeps a DDS's own format instead. 2D textures only. |
| `ebx-dump <asset> <out.json> [--project P]` | The ebx root object as JSON: each object gets `$type` and `$guid`, internal pointers are followed inline (`{"$ref": path}` on repeats), external pointers are `{"$external": name, file_guid, class_guid}`. Also lists `color_fields` (every Vec3/Vec4, and fields named colour/tint) with their paths. |
| `ebx-set <project> <asset> <field.path> <value>` | Sets one field (path like `Materials[0].Color.x`, through internal pointers), then `AssetManager.ModifyEbx`, saves the project. `#RRGGBB`/`#RRGGBBAA` is written to the field's own type: Vec3/Vec4 floats 0-1 (Vec4 `w` from `AA`, else kept), or packed ints (alpha byte kept unless given). Other values: number, bool, enum name, string, GUID. |
| `export-mod <project> <out.fbmod> --title T --author A --version V [--category C] [--description D]` | Loads the project, sets `ModSettings` Title/Author/Version/Category/Description by property name and calls `FrostyProject.WriteToMod(out, settings)`. |

Python: `tools/mod_builder/harness.py` `run(cmd, timeout=600) -> dict` converts path arguments with `wslpath -w` (WSL paths become `\\wsl.localhost\...`, which works), runs the exe (`MU_MMC_HARNESS` overrides `/mnt/c/Madden27Modding/MuHarness/MmcHarness.exe`) and raises `HarnessError` on `ok: false`, a nonzero exit, a timeout, no JSON, or a missing exe.

```python
from tools.mod_builder.harness import run
run(["import-texture", "data/mod_builds/x/x.fbproject", "contentshared/.../playartlinecolor_firstdownline", "data/mod_builds/x/work/new.png"])
```

## Start-up sequence

The order proven by spike S-c (MOD_BUILDER_PLAN §3), in `Boot.Start`:

1. `[STAThread]`; an `AppDomain.AssemblyResolve` handler loads MMC DLLs from the editor folder (`.`, `Plugins`, `ThirdParty`, `Profiles`); `EbxClasses` resolves to `Profiles\MADDEN27SDK.dll` with `Assembly.LoadFile`, cached and guarded against re-entry. A WPF `Application` with a hidden main window exists (editor code reads `Application.Current`). `Environment.CurrentDirectory` = the editor folder. `Config.Load` on a temp file holding `{}`.
2. `ProfilesLibrary.Initialize(new Profile[0])` (embedded profile list), then `ProfilesLibrary.Initialize("Madden27")`.
3. `TypeLibrary.Initialize(true)`.
4. `new FileSystemManager(gameDir)`, `AddSource` for each `ProfilesLibrary.Sources`, `Initialize(key)`. `key` is the first 16 bytes of the editor's game key file when it exists (the editor does the same, `SplashWindow`), else null.
5. `ResourceManager` and `AssetManager`.
6. `Frosty.Core.App` globals: `FileSystemManager`, `ResourceManager`, `AssetManager`, `Logger`, `IsEditor`, `SelectedProfile`, `NotificationManager`, `PluginManager` (`PluginManagerType.Editor`, `Initialize()`).
7. `AssetManager.Initialize(true, new AssetManagerImportResult())` (uses the editor's existing cache).

Native DLLs print to stdout (dxtex: `[Using DirectCompute ...]`), so the harness points the Win32 stdout handle at stderr and keeps its own stream on the real stdout for the JSON.

## Headless replacements for window-bound editor code

- **Texture import.** `TextureImporter.Import`/`ImportDdsBytes` run inside `FrostyTaskWindow.Show` (WPF, fails headless). `TextureImport.Import` repeats its callback (read from MMC's IL; Frosty Toolsuite's importer is the same shape): make a DDS in the texture's format, read the header, `new Texture(type, format, w, h, 1, orig.Version, orig.ResourceMeta)`, copy `FirstMip`/`TextureGroup`, `CalculateMipData`, flags (`SrgbGamma` from the source, other bits from the original), `AddChunk` when `ProfilesLibrary.MustAddChunks` and the chunk has no bundles (else `ModifyChunk`), copy `Unknown3`, `SetData`, `AssetNameHash` (`Utils.HashString64(name, true)` on profile versions 20260605/20260612, else FNV-1), `ModifyRes`, `LinkAsset` res→chunk and ebx→res.
- **Project load.** `FrostyProject.Load` also runs inside `FrostyTaskWindow.Show`. `Projects.Open` repeats `Load`/`InternalLoad`'s header checks (magic, format version, profile) and invokes the private `LoadTaskBody(reader, task)` with an uninitialized `FrostyTaskWindow` (its `Update` only sets two fields). `FrostyProject.Save` and `WriteToMod` work headless as they are.

## Never call

The editor DLLs also hold code that decrypts other authors' mods. The harness must never call:

- `FrostyModCryptor` (any member, including `Decrypt`)
- `FrostyModKeyProvider` (any member)
- `FrostyModReader.GetResourceData`
- anything else that reads or decrypts a `.fbmod`

`tests/test_mmc_harness.py` checks the source names none of them outside comments. To check a built mod, read it back with `python3 -m tools.fbmod_inspect` (headers only, never payloads).

## Known limits

- **ebx needs the game key.** Madden 27's `Data/initfs_Win32` is AES-encrypted (`ProfilesLibrary.RequiresKey`). MMC Editor asks for the key on first start and keeps it as `madden27.key` in its working folder. Without that file the initfs isn't loaded: textures (res + chunk) work, but `ebx-dump`/`ebx-set` (and loading a project with ebx edits) fail with a message saying so, because RIFF ebx needs the initfs's shared type descriptors. Put the editor's `madden27.key` in the editor folder or point `MU_MMC_KEY_FILE` at it. This is the game's content key, the one the editor itself uses to read the game; it has nothing to do with mod encryption.
- Only 2D textures import headless (cube/array/3D: use the window route).
- Legacy (pre-magic) `.fbproject` files and projects from another editor format version are refused; open and re-save them in MMC Editor.
- `ebx-set` edits fields of internal objects only; it can't repoint a `PointerRef` or edit through an external one.
- Colour import of an uncompressed DDS made by `headshots/portrait_converter/dds.py` works (BGRA masks are read, then re-encoded to the texture's format). The spike's `data/mod_builds/yellow_first_down/work/firstdown_yellow.dds` has its red and blue swapped (its bytes are RGBA under BGRA masks), so it decodes light blue; use the PNG or a fresh `encode_dds(..., "RGBA")`.
- Each call reloads the whole asset index (15-45 s). Batch work into one project and as few calls as you can.
