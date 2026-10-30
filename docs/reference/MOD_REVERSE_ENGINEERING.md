# Reverse-Engineering Madden 27 Mods (.fbmod)

How a Frosty `.fbmod` built by MMC Editor 1.1.0.x is laid out, what the mods in `PC_MODS/` actually change, and what that means for building our own. Written 2026-10-30 from all 74 `.fbmod` files in `PC_MODS/` (read-only) with `tools/fbmod_inspect.py`. Install and load order: [`../MOD_INSTALL.md`](../MOD_INSTALL.md). Where each mod came from: [`../MMC_MOD_SURVEY.md`](../MMC_MOD_SURVEY.md).

```
python3 -m tools.fbmod_inspect <file.fbmod> [--all] [--json] [--extract OUT]
python3 -m tools.fbmod_conflicts <dir-or-fbmod>...      # which mods touch the same assets
/reverse-mod <mod path or name>                          # the Claude skill that walks through all of this
```

**Tags.** **[verified]** = checked against the bytes of every mod in `PC_MODS/` (the parser walks every record of all 74 files and lands exactly on the data section). **[inferred]** = consistent with the data but not proven. **[guess]** = from Frostbite/Frosty convention or asset names only.

## 1. The headline: every payload is encrypted

Every one of the 19,579 payloads in the 74 mods starts with the 8 bytes `FMENC001` and is high-entropy (7.999 bits/byte) after it **[verified]**. That includes the mod icons and screenshots. MMC Editor encrypts what it writes, and only the MMC Mod Manager decrypts it when it applies a pack. So:

- **What we can read:** the whole header: title, author, version, description, game build, and every resource record, i.e. *which* assets a mod replaces, their types, original sizes, compressed sizes, texture/chunk links and legacy file paths. That's the recipe.
- **What we can't read:** the new content itself (the pink pixels, the new jersey colour values, the new Lua). No texture can be turned into a PNG from these mods.

We don't try to break this. It protects authors' work, and the community norm is to ask the author for their project or rebuild the change yourself in MMC Editor. The inspector writes encrypted payloads out as `.fmenc` files and says so. Plain (unencrypted) Frosty mods from other tools, or our own future ones, decode fully (§2.5).

What the bytes do show about the encryption **[verified unless marked]**:

- stored size = 8 (`FMENC001`) + 48 + the plaintext padded up to the next multiple of 16 with at least one padding byte, i.e. PKCS#7. It holds exactly for every payload whose plaintext size is known (icons, `fsfile`, texture res, chunks whose compressed size is in `range_end`).
- The 48 bytes are probably a 16-byte IV plus a 32-byte HMAC-SHA256 tag, with AES-CBC as the cipher **[guess]**.
- The IV is random: 319 logos with identical SHA1s in the CBS and FOX logo mods have completely different ciphertext, even in the first 24 bytes.
- The plaintext is the normal Frosty payload, *compressed* first: every ebx is stored smaller than its original size (14,972 of 14,972), and a chunk's `range_end` is its compressed size.

## 2. The .fbmod format (version 9)

All integers little-endian unless noted.

### 2.1 File header **[verified]**

| Offset | Type | Field |
|---|---|---|
| 0 | 8 bytes | magic `FROSTY\0\x01` |
| 8 | u32 | format version (9 in every mod) |
| 12 | i64 | data offset: where the data table starts |
| 20 | i32 | data count: entries in the data table |
| 24 | u8 + bytes | profile name, length-prefixed (`Madden27`) |
| … | u32 | game build the mod was made against (e.g. 3468304 = 10/21 patch) |
| … | 20 bytes | game-layout fingerprint. Identical for all mods of the same build, and equal to the 20 bytes after the build number in MMC Editor's `Caches/madden27.cache` |
| … | 6 C strings | title, author, category, version, description, link |
| … | i32 | resource count, then the resource records |

Builds seen: 3468304 (45 mods), 3415994 (12), 3412169 (8), 3235055 (6), 3288067 (2), 3235066 (1). A mod made for an older build can still load. Hair Fix (3235055) is the one that crashed (MOD_INSTALL §4).

### 2.2 Resource records **[verified]**

Every record starts the same way:

```
u8   type          0 embedded, 1 ebx, 2 res, 3 chunk, 4 bundle, 5 fsfile
i32  index         data-table entry, or -1 = no data
cstr name          asset path (ebx/res), chunk GUID, "Icon"/"Screenshot0..3"
-- only if index != -1:
20   sha1          not a hash of the stored bytes (same sha1, different ciphertext). Probably the plaintext or the asset
i64  size          original (uncompressed) size; 0 for most chunks
u8   flags         0 or 0x08 (0x08 on 1,099 ebx, 635 res, 1,883 chunks; meaning unknown [guess: "added/duplicated asset"])
u32  handler       non-zero = the Mod Manager merges this resource instead of replacing it
cstr user_data     "" or "legacy;<path>" or "legacy;Collector (<bundle>)"
-- always:
i32  n, n × u32    bundles to add the asset to (always 0 in these mods)
```

Then, by type:

- **res (2):** `u32 res_type`, `u64 res_rid`, `i32 meta_len` + `meta` (16 bytes; textures start `0c000000 01/03000000`).
- **chunk (3):** 7 × u32 = `range_start, range_end (compressed size), logical_offset, logical_size (decompressed size), h32, h32 again, unknown hash`. Then, **only when `handler == 0`**: `i32 first_mip` (0 for texture chunks, -1 otherwise) and `i32 n` + n × i32. The n values are small numbers (9, 13, 26, …), probably catalog or superbundle ids **[guess]**.
- **bundle (4):** a C string (the bundle name again) + `u32` super-bundle hash. Seen only in Hair Fix and Faces (12 `*_playerhead_brt` bundles).
- **fsfile (5):** nothing extra. Seen once: `Scripts/LicenseeGame.cfg` in Halftime Off.
- **embedded (0):** the icon and four screenshot slots. Unused slots have index -1.

**Chunk ownership [verified]:** a chunk's `h32` is Frosty's name hash (`h = 5381; h = (h*33) ^ byte`, called "Fnv1" in Frosty) of its owner: the lower-case texture res name, or the legacy file path. So the inspector can say "this chunk holds the pixels of `football_color`" even though the chunk is only named by a GUID. Example: the yellow-first-down chunk has h32 `0xF4527E5D` = hash of `contentshared/football/playart/textures/linecolors/playartlinecolor_firstdownline`. Chunks whose owner isn't in the mod are new data for an asset the mod doesn't otherwise touch (e.g. Jumbotron movie chunks).

**Legacy files [verified]:** Frostbite's "legacy" file system (plain files such as Lua, XML, JSON, `.ftc`, `.ast`) shows up as a *chunk* whose `user_data` is `legacy;<path>` and whose h32 is the hash of that path. Each such mod also carries one or more `legacy;Collector (<RtamStorage>)` chunks (handler `0xBD9BFB65`, 52 bytes): the list of changed legacy files for that storage. The Mod Manager merges these collectors across mods.

**Merged resources [verified from handler hashes; behaviour from MOD_INSTALL]:** `handler != 0` appears on the legacy collectors and on bundle ref tables (`*_brt`, res types `0x428EC9D4` / `0xE2E6955B`). Everything else is replaced wholesale, and the mod higher in the pack wins.

### 2.3 Res types seen

| res_type | Name | Basis |
|---|---|---|
| `0x6BDE20BA` | Texture (184-byte header; pixels in a chunk) | verified: chunks hash-link to these names |
| `0x49B156D4` | MeshSet | `*_mesh` names |
| `0xB2C465F6` | SoundWave | `sound/…_nwa`, `mx_broadcast_*` |
| `0x428EC9D4`, `0xE2E6955B` | BundleRefTable (merged) | `*_brt` names + handler |
| `0x73312045` | ShaderBlockDepot | `*_shaderstate/shaderblock*` |
| `0x063994C5` | Font? | only on `content/ui/fonts/*` |
| `0xE113D4B1` | Presentation flow? | `muse/broadcastpresentation/…/preplay`, `postplay` |
| `0x63471787`, `0x85EA8656`, `0x85AC783D`, `0x2D254A89` | part loader?, cloth wrapping?, cloth sim?, shader state? | one folder each |

### 2.4 Data section **[verified]**

At `data_offset`: `data_count` × (`i64 offset`, `i64 size`), offsets relative to the end of this table, then the payloads back to back. Several records can point at the same entry (identical payloads stored once): CBS Logos has 447 records over 369 entries. The old regex reader in `fbmod_conflicts.py` skipped these repeats. It now uses this parser, which raised some overlap counts in MOD_INSTALL §3 (e.g. CBS/FOX presentation 34 → 78, logos 361 → 446). The one-at-a-time groups didn't change.

### 2.5 Unencrypted payloads (what `--extract` decodes)

For a mod whose payloads don't start with `FMENC001` (plain Frosty, or ours), `--extract` writes each payload out and decodes what it can:

- **Frostbite block compression:** a run of 8-byte big-endian block headers (`u32 decompressed size`, `u16 codec`, `u16 compressed size`). Codec byte `0x00` stored, `0x02` zlib, `0x0F` zstd, `0x09` LZ4 (needs `pip install lz4`), `0x11/0x15/0x19` Oodle. Oodle can't be decoded here: it needs `oo2core_*_win64.dll` from the game folder on Windows or Wine. **[guess]**: Frostbite convention, unit-tested on synthetic data only, since no real payload here is plain.
- **Bare streams:** zstd and zlib.
- **By magic bytes:** DDS (converted to PNG with Pillow), PNG, JPEG, EBX (`RIFF…EBX` or `CE D1 B2 0F`).

A Frostbite texture is *not* a DDS. It is a 184-byte Texture res (format, size, mips) plus a pixel chunk. Turning it into a PNG means reading that header (Frosty's TexturePlugin does this), which isn't possible while it is encrypted.

## 3. Teardown: BCA Pink Oct (LarrySznn, v1.01, build 3468304)

*"October Breast Cancer Awareness Pink for Primary accessory colors on all NFL team uniforms."* 597 KB, 72 resources + icon, all encrypted.

**What it replaces:**

1. **62 jersey assets (ebx)**, `content/footballcharacter/items/uniform/jersey/<team>/u_jersey_<team>_<year>_<variant>`. These are two per team (home + away) for 30 teams. **Jaguars and Vikings have only one each** (`u_jersey_jaguars_2024_2whi`, `u_jersey_vikings_2024_2whi`: their white/road sets). The game cache lists their home jerseys (e.g. `u_jersey_jaguars_2024_1gre_*`, `u_jersey_vikings_2024_purple` / `_1pur_white`), so those two teams probably play home games without pink accessories **[inferred]**. The jerseys picked are the current default sets (e.g. `chiefs_2023_red/whi`, `ravens_2026_pur/whi`); alternates, throwbacks, Color Rush and rivalry jerseys aren't touched. Each jersey ebx is 2–4.5 KB. The description says they set the jersey's **primary accessory colour** to pink. The accessory colour is the team colour used for gloves, socks, wristbands, sleeves and towels on that uniform **[inferred from the description plus the asset type; the values themselves are encrypted]**.
2. **Football textures**, `content/objects/common/football/texture/football_color`, `_normal`, `_rsm`. Each is a Texture res + pixel chunk (color 512 KB, normal 512 KB, rsm 341 KB decompressed): a pink ball. `football_color` is the same asset, and the same chunk GUID `2fdfc2a6-…`, that **Mudded Wilson NFL BALL** replaces. That's why the two are one-at-a-time.
3. **Towel texture**, `contentshared/characters/player/parts/torsogear/towel/towel_col` (64 KB): a pink towel for every player.
4. **NFL shield helmet decal**, `content/characters/player/parts/uniforms/helmets/decals/league/helmetnflshield_col` (256 KB): a pink shield on every helmet.

So the mod is all-or-nothing across the league. Per team, you can only switch it off by editing the mod, not the pack (MOD_INSTALL §4, *Not built*).

**How to make the same kind of mod (e.g. a different colour theme, or a Salute to Service set)** in MMC Editor on Windows:

1. Open Madden 27 in MMC Editor (as Administrator). The inspector's list is your shopping list: `python3 -m tools.fbmod_inspect "PC_MODS/BCA Pink Oct.fbmod" --all`.
2. **Accessory colour:** in the Data Explorer, go to `content/footballcharacter/items/uniform/jersey/<team>/` and open each team's `u_jersey_…` asset (or use the Uniform Editor plugin, `FBUniformEditorPlugin`). Find the accessory/primary-accessory colour field and set your colour. Repeat for every jersey you want; add the Jaguars and Vikings home sets that BCA leaves out. Changing one team only is just editing fewer jerseys, so it can be done per team.
3. **Textures** (ball, towel, shield): open the texture asset, *Export* to DDS/PNG, recolour it (any image editor; for us Python + Pillow, §6), then *Import* it back. MMC Editor re-encodes the texture and writes the new res + chunk.
4. *File → Export Mod* with title/author/version and a category, and pick a build. Add the `.fbmod` to the Mod Manager, then put it in a pack above anything that touches the same assets (check with `tools.fbmod_conflicts`).

For a mod made from scratch, the same three asset kinds cover most "theme" mods: uniform ebx colours, gear textures, and the ball.

## 4. Short teardowns

| Mod | What it replaces | Kind |
|---|---|---|
| **M27 yellow first down marker** (LarrySznn) | One texture: `contentshared/football/playart/textures/linecolors/playartlinecolor_firstdownline` (2 KB decompressed, a tiny colour swatch) + its chunk. The smallest possible texture mod: one res + one chunk | texture |
| **Mudded Wilson NFL BALL** | `football_color` texture only (699 KB, full mip chain) | texture |
| **M27_NFLScorebugs_CBS_v3** (SimLeagueTV) | 145 ebx + 70 textures under `ui/screen/ingame/scorehud/eatheme/customhud/<network>/…` (maddenhero, nbcsunday, primevideo, playoffwm2: the in-game score bug for every broadcast skin), 32 font styles (`content/ui/artstyleguide/fontstyles/broadcastpackages/sltvcbs*`), new fonts (`content/ui/fonts/sltvcbs-*`, `customtnfneue-*`), banner widgets (`presentation/banners/screens/…`), the bottom-line ticker, the two kick-meter widgets (why it fights Shifted Kick Meter), 2 presentation-flow res. Shares 325 assets with the FOX version | UI ebx + textures + fonts |
| **m27 CBS/FOX/MNF/SNF/TNF Presentation** (Icicle22) | Mostly audio (~90 sound assets plus their data chunks per mod), network logos and bumpers (textures), intro/replay movie ebx. CBS/FOX share 78 assets | audio + textures + video |
| **Real Broadcast Music CBS/FOX** (WiiMaster) | `sound/coreaudio/samples/music/broadcast/…` SoundWave ebx/res + data chunks (210–386 chunks), 8 music-asset ebx, one bumper movie | audio |
| **CBS/FOX NFL Team Logos** | 223–225 textures in `content/ui/imageassetlibraries/global/teamlogos/teamlogos<network>/assets/` | textures |
| **FranchiseMod1.3** (Sabos) | 157 supersim tuning ebx (`football/attribsys/data/supersim_*`: compound abilities, tackle chances, injury, fatigue, play time, run/pass/punt/FG play), 264 legacy offensive playbook XMLs (`common/franchise/playbooks/playbook_off-*.xml`), `franchise-tuning-binary.ftc`, 7 AI Lua scripts (`common/luascripts/offense*.lua`, `defense*.lua`, `libfile_common.lua`), 3 collectors. The 7 Lua scripts are byte-identical to Sabos Gameplay 1.2.1's | sim ebx + legacy files |
| **M27-Pre80Cut65SalaryCapADD40Pr3yVer1-1** (duestereLegende, "Roster Size Limit increase 1.1") | One legacy file: `common/franchise/frantkdata/0/franchise-franchise-binary.ftc` (2.76 MB uncompressed, the franchise rules binary: roster 80/65, practice-squad years, starting cap +$40M) + its collector. That's why it needs a new franchise | legacy binary |
| **Sabos Madden Gameplay Mod 1.2.1** (folder) | 3,329 payloads: 2,920 play-library ebx (`football/gameplay/playbooks/playlibrary`), 256 gamesheets, 69 behaviour trees (`global/logic/ingame/btree`), 48 attribsys tuning, locomotion curves (`antifreeze/shared/locomotion/runcurves/speedratingcurve`: the 40-time change), traits dsub, the 7 AI Lua scripts. The folder also has `FAQ.txt` (changelog, recommended settings), `SabosMaddenAutoSubSliderImportRevert.zip` (unopened) and `Old/` 1.0–1.2 | gameplay ebx + Lua |
| **autoplaycall** (EJ) | One legacy JSON: `common/database/json/ingameconfig_defaults.json` (16.6 KB) | legacy JSON |
| **MUT Remover 1.6**, **No Offline Warning 1.0.2** | One legacy UI scene each: `common/ui/node_fe/mainmenu/corenode.ast` (53 KB) / `common/ui/node_com/scriptpod.ast` (642 KB; the description says it is a one-byte action change) | legacy .ast |
| **Halftime Off 1.4** | One initfs file: `Scripts/LicenseeGame.cfg` (16.8 KB): a game config switch | fsfile |
| **No VRAM Warning** | One ebx: `content/ui/widgets/perfoverlay/perfoverlayvramwarning` | UI ebx |
| **120 FPS Menu** | One ebx: `contentshared/global/rendersettings/frontendrendersettings` | settings ebx |
| **Shifted Kick Meter** (WiiMaster) | The two kick-meter widget ebx in `football/gameplay/matchlife/widgets/` | UI ebx |
| **Special Teams Formations** | 10 formation ebx in `football/gameplay/playbooks/playlibrary/formations/…/special/` | playbook ebx |
| **Dynamic Crowd Density Tier 1–4** (Judah) | The same 67 stadium ebx in `content/worlds/components/stadiums` with different crowd values; the folder has a README and in-game example JPGs | world ebx |
| **lighting overhual**, **BetterGrass**, **Noblurry**, **No Film Grain+**, **DOF Enabler** | Character material presets, grass colour recipes, visual-environment (`lighting/ve/core`) and post-process ebx; No Film Grain+ adds one texture | settings ebx |
| **smallpads27_2** | One ebx: `contentshared/characters/common/fbmorph/datamapping/fbmorphbodyblendvisualdatamapping` (body-blend morph mapping) | ebx |
| **M27 Unlocked 0.84B** | 1,793 records: player parts, meshes, vanity uniforms, gear, MaddenID images, 19 bundle ref tables | everything |
| **Hair Fix and Faces** | Player-head meshes and textures + the only `bundle` records (12 `*_playerhead_brt`) | mesh + bundles |
| **Jumbotron 2.7**, **Stadium Experience 2.3**, stadium attachments | Ribbon-board/jumbotron movie ebx with new movie chunks (Jumbotron: 385 MB), stadium textures and meshes | video + textures |

## 5. MMC Modding Tools v1.1.0.7 (`PC_MODS/MMC_Modding_Tools_v1.1.0.7/`)

- **`MMC_Editor_v1.1.0.7/MMCEditor.exe`**: a Frosty Editor fork (.NET Framework 4.8, WPF). It is the only place a mod's content can be made or changed. **Plugins:** Texture, AtlasTexture, MeshSet (mesh import/export), **FBUniformEditorPlugin** (uniforms), StadiumViewer, SoundEditor, MovieTexture2, Font, SvgImage, LocalizedString / FsLocalization (text), PlaybookJson, PlayBundleAdd, BundleRefTable, ChunkRes editor, Duplication (clone an asset as a new one), DiffChecker, EbxToXml (export an asset's fields as XML: the readable view of what a mod changes), EbxOriginalValue, EbxPointerGraph, References, TypeExplorer, TeamBuilderImport, PortraitManager, Lua, Animation, LCU explorer; `Plugins/Fifa/` adds legacy-file plugins (BigFile, Text, Database, DDS, LocalizedStrings, KitPreview).
- **`Profiles/`**: SDK DLLs per game (MADDEN19–27, COLLEGEFOOTBALL27, PGATour). **`Caches/`**: `madden27.cache` (138 MB: `FROSTY\0\x02`, version 6, the build number and the same 20-byte fingerprint as the mods, then a **plain zstd stream of ~575 MB**: the full asset index of the game, with superbundles, bundles, ebx/res/chunk names), `madden27_typeinfo.cache`, `madden27_legacysha1.cache`, `madden27_mvdb.cache`. Readable from Python with `zstandard`: this is how we found the Jaguars/Vikings home jersey names. A future mod builder can use it to list vanilla asset names without the game files.
- **`ThirdParty/`**: `CryptBase.dll` (the DLL the Mod Manager drops into the game folder, which loads the mods at runtime), zstd/LZ4/zlib DLLs, `dxtex.dll` (DDS), `ealayer3.dll` (EA audio), `libfbxsdk.dll` (FBX meshes), SharpDX renderer.
- **`MMC_ModManager_v1.1.0.7/MMCModManager.exe`**: applies packs (MOD_INSTALL §4); ships the BundleRefTable, MeshSet and FsLocalization plugins it needs to merge handler resources.
- **`AC_(PUT IN CFB27 or M27 FOLDER)/EAAntiCheat.GameServiceLauncher.exe`**: the replacement launcher (MOD_INSTALL §2). **`ReadMe.txt`**: install steps.
- The Editor's strings mention `*.fbproject (Frosty Project)`: projects are the editable form. Asking an author for the `.fbproject` is the clean way to tweak someone's mod.

Other `PC_MODS/` items: `M27_641_1` is a JSON franchise schema (`{"meta":{"major":641,"minor":1,…`) for Madden Franchise Editor (MMC_MOD_SURVEY §1). `MFE/` is the Franchise Editor install. The `.exe` files are franchise utilities.

## 6. What we can build from WSL/Python, and what needs Windows

**From WSL/Python today:**
- Reading any mod's recipe, conflicts and packs (`tools/fbmod_inspect.py`, `tools/fbmod_conflicts.py`, `pipeline/mod_packs.py`).
- Browsing the vanilla asset index from `madden27.cache` (zstd).
- Making the *inputs* for a texture mod: recolouring a DDS/PNG exported from MMC Editor, generating portraits and DDS (`headshots/portrait_converter/`, BC1/BC3 encoder).
- Writing the *text* of legacy files (Lua, JSON such as `ingameconfig_defaults.json`, playbook XML) once exported from the editor.

**Plausible from Python later (needs work, not proven):**
- Writing an **unencrypted** `.fbmod` directly. The record layout is known, and the parser round-trips synthetic mods. Open questions: whether MMC Mod Manager accepts payloads without `FMENC001`; how to fill in each asset's sha1, flags and the unknown chunk hash; and the codec (zstd vs Oodle) the game expects. The best target is legacy-file mods (one JSON/Lua/XML + a collector, like autoplaycall or MUT Remover), then single-texture mods (yellow marker). Test with a throwaway file first.

**Needs MMC Editor on Windows:**
- Any ebx edit (uniform colours, tuning values, crowd, presentation flow, UI widgets): ebx is a binary object format with a per-game type database (`madden27_typeinfo.cache`), and the editor does the reading and writing.
- Texture import (re-encoding the Frostbite texture header and mips), mesh import, sound import, asset duplication.
- Exporting an encrypted `.fbmod` that the Mod Manager is known to accept.

## 7. Open unknowns

- The cipher, key and the 48 bytes of overhead per payload (§1). Not pursued on purpose.
- Which codec is under the encryption (zstd or Oodle Kraken).
- What the `0x08` flag, the third chunk hash (`unknown_hash`), the chunk extra-int list and the texture `res_meta` bytes mean.
- What the per-resource sha1 hashes.
- Whether the Mod Manager loads unencrypted MMC-format mods (decides whether §6's Python mod writer is worth building).
- The exact field and colour values inside BCA's jersey ebx. In MMC Editor, open `u_jersey_bears_2023_whi` with the mod applied and use EbxToXml or DiffChecker against vanilla.
