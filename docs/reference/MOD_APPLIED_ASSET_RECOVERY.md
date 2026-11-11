# Recovering and reconstructing applied Madden mod assets

This runbook records the method demonstrated with FOX Scorebugs v3 on Madden27 build3468304 and MMC1.1.0.7. Use it to inspect how an asset mod works, compare author updates, investigate compatibility failures, and reconstruct editable compiled assets. The successful FOX example is evidence for this workflow, not proof that every mod type is recoverable.

Related references: [mod format/header inspection](MOD_REVERSE_ENGINEERING.md), [normal project building](MOD_BUILDING.md), [pack installation](../MOD_INSTALL.md), and [experiment history](../in_progress/DYNAMIC_PRESENTATION_RESEARCH.md). Research evidence is local and ignored under `verify_output/dynamic_presentation/2026-11-01/`; the implementation helpers referenced below are experimental snapshots, not a general supported CLI.

## 1. What the method does

The flow is:

```text
Original .fbmod header → expected resource inventory
Original .fbmod → normal MMC Mod Manager application → isolated ModData pack
ModData metadata + CAS records → verified compiled assets → normal asset decoding
Decoded EBX/RES/chunks → fresh editable .fbproject → normal MMC export
Original-only game capture ↔ reconstructed-only game capture
```

MMC Mod Manager handles its protected input through its normal application process. Our recovery reads the resulting game asset storage. It does **not** directly decrypt protected `.fbmod` payloads, recover the author's original `.fbproject`, or recover layered artwork, source histories, comments and design intent. CAS decompression is distinct from mod payload decryption.

Reconstructed EBX structures can expose widgets, logic, connections and references. Texture/font/binary records can expose compiled pixels, fonts and other data. This can support local repair or adaptation. It cannot by itself explain why an author made a change or establish that a derived modification fixes a particular bug.

## 2. Preserve provenance before applying anything

1. Record each original filename, size, SHA256, header title/version, profile and target game build. Keep different downloads as separate immutable inputs, even when they share a filename. Do not replace the tested baseline.
2. Inventory the readable header by `(resource type, normalized name)`: expected SHA1, original size, added/replaced flags, handler/user data, bundles, RES RID/type/meta, chunk GUID and chunk metadata. Keep all records, including merge handlers and legacy UI chunks.
3. Back up manager config while Mod Manager is closed. Create a uniquely named research pack containing **only one source mod enabled**. Preserve existing packs and selection; use disposable Quick Play or authorized scratch saves with the save watcher stopped.
4. Apply with normal Mod Manager. Capture the actual generated `ModData/<pack>/Data/mods.json`; do not assume the output is `ModData/Default`. Record tool/game versions and observed output path. A different folder or stale cache can silently produce vanilla or another pack's assets.
5. Hash generated source files before capture, and recheck after recovery. Keep reads separate from the reconstructed project's output.

For a comparison of author versions, use separate pack names and output/evidence roots. Header-only inventory does not require installing the download. A filename, download date or version label is not a substitute for a content hash.

## 3. Locate and capture the applied assets

Load the selected applied file system and its metadata using the installed profile/SDK. In the demonstrated environment, full-game EBX indexing crashed in the SDK Oodle wrapper. The research helper instead used the profile's normal metadata loader without decoding every EBX.

For every expected direct record:

- Resolve the applied EBX/RES name or chunk GUID, and record the CAS path, offset, stored size, metadata SHA1, decoded/original size and bundle names.
- Map `native_data/...` against the selected applied root's `Data` directory, while respecting links back to base-game storage.
- Capture exactly the stored record range and hash the captured bytes. For the415 direct FOX records, that hash matched the expected original-header SHA1. Do not infer ownership solely from a matching name.
- If the applied entry has a zero SHA1, hash actual captured bytes and investigate its role. Some global records are merged results rather than the original patch payload.
- Preserve an explicit result per expected record: exact direct match, handler result, missing, unsupported or mismatch. Do not count a merged output as an exact patch recovery.

Mixed packs are weaker evidence. The initial mixed MNF+FOX snapshot was usable only because each direct FOX record matched its expected header hash. A later clean FOX-only application independently verified the collector result. Prefer a single-mod source from the start.

## 4. Decode the normal game records

Validate every CAS block boundary, compressed length, declared output length and total decoded length. Support only formats verified for the specific record; report unsupported compression rather than producing partial data. The demonstrated decoder handled raw, zlib, Zstd and Oodle blocks using already installed libraries.

The normal SDK Oodle wrapper crashed on the target EBX. A Windows Python `ctypes` call to the installed game's `oo2core_9_win64.dll` successfully decompressed the captured records with the normal14-argument ABI,64-bit return type, and64-byte input/output padding. The exact cause of the SDK wrapper crash remains unresolved. `Oodle.Bind` expects a base directory ending in a separator, not a full DLL filename; successful binding alone did not fix the wrapper crash. Do not generalize this ABI/workaround to another library version without checking it.

Parse decoded EBX through the normal MMC `EbxReader` using the matching vanilla SDK/type information. Record file GUID, exported object identities, object count/type and dependencies. Resolve custom references against the entire recovered inventory, not just the vanilla asset index. Keep both direct dependencies and transitive closure, with unresolved boundary references classified explicitly.

For binary assets, retain RES RID/type/meta, texture dimensions/format/mips, texture→chunk links, font data, and legacy file identities. A PNG preview is useful for inspection; it is not a faithful replacement for the original compressed texture chain.

## 5. Distinguish direct replacements from merge instructions

The FOX v3 header has416 records:234 EBX,114 RES and68 chunks. Only415 are direct compiled asset records. The remaining record is a52-byte legacy collector patch:

- Collector GUID: `12723b33-68df-97d8-a361-9f810e933e61`.
- Handler: `3181116261` (`0xbd9bfb65`).
- User data: `legacy;Collector (ContentShared/global/RtamStorages/Presentation_Common)`.
- Clean applied result:243580 decoded bytes; captured compressed SHA1 `4f36f77c373c13f5b728f2f1f984402691ee1c1c`.

Its applied bytes represent the merged collector, **not** the original52-byte instructions. The first live reconstruction used a full collector snapshot as a regular chunk replacement. That works for the isolated FOX A/B; it is not a merge-compatible production solution. Combining networks requires understanding/rebuilding the relevant collector updates, or a deliberately composed and validated shared collector. Do not independently stack full collector snapshots and assume they merge.

The direct legacy chunk `common/ui/node_ig/scriptpod.ast` is also part of the package. Do not classify every chunk as texture pixels or omit non-image chunks from a graphics mod.

## 6. Build a fresh project with identities intact

Start from the matching vanilla build and a new project. Restore added/replaced assets according to their header roles, including bundle/superbundle placement. Preserve:

| Asset | Required identity and relationships |
|---|---|
| EBX | File GUID, exported class GUIDs, object graph, external references and dependencies |
| RES | Original64-bit RID, type, metadata and associated EBX/resource links |
| Chunk | GUID, logical range/size, first mip, H32/H64 and bundle/superbundle membership |
| Texture | EBX→RES→chunk chain, embedded AssetNameHash, dimensions, format, mips and pixel bytes |
| Font | Font EBX/style/config references and compiled font resources |
| Legacy | File chunk, file identity and collector relationship |

The installed SDK's convenience `AddRes(name,...)` generates a new RID. The FOX helper constructed `ResAssetEntry` explicitly and registered it with the original RID. Chunk creation supplied the original GUID. Changing one identity without remapping all references can leave a project that saves but fails in-game.

For faithful texture reuse, use raw DDS or the decoded RES/chunk data. Importing the recovered800×140 FOX PNG into the vanilla52×52 slot resized it to52×52. Raw DDS import/readback preserved the800×140 BC7 texture byte-for-byte. Recomputing chunk compression ranges is necessary when compression changes; do not blindly reuse old compressed ranges. Conversely, the texture update helper cleared logical sizes on66 chunks, so the reconstruction restored the expected logical metadata afterward and verified the exported header.

When namespacing a texture, update its embedded `Texture.AssetNameHash` as well as EBX/RES/chunk identities and chunk ownership hashes. For build3468304 the existing importer uses `Utils.HashString64(res.Name,true)` for the embedded field; the chunk H32/H64 functions are separate. The R5 constructor originally missed this field. Saved-project byte comparisons must explicitly include approved identity edits rather than treating source-identical stale metadata as correct. The corrected texture-only control still exited before title, so this metadata correction alone has not resolved startup.

The current helpers were built for one recorded FOX inventory and game build. They are not a general importer. Their hardcoded roots, record counts and commands must be reviewed before adapting them to v4.1 or CBS.

## 7. Verify the saved project and exported mod

Reopen the saved project in a fresh process before claiming reconstruction success.

- Compare every recovered EBX graph, file GUID, exported identities, object counts and dependencies. Serialization may renumber local objects. Normalize only demonstrated non-semantic changes: local non-exported `$guid` values and `AssetClassGuid.InternalId`. Preserve exported GUIDs, pointer targets/paths, ordering and all actual field values. A broader normalization can hide a broken reference.
- Read every RES/chunk back and compare decoded bytes. Check texture format/dimensions/mips and font/legacy data explicitly.
- Export through normal MMC project serialization. Inspect the resulting header: record set, identities, flags, bundles, resource metadata, chunk hashes and logical metadata. Record intentional differences separately from failures.
- Do not compare encrypted ciphertext as evidence of content equality. Encryption can produce different stored bytes for the same content. Compare verified source hashes, decoded payloads and semantic graphs instead.

FOX v3 result:234/234 canonical EBX graphs and182/182 binary records matched. The normal416-record export is2409395 bytes, SHA256 `04fc1ff82729c1593f42656e64120ddc899e33304082eed7f70dc129cacd761c`. Only the deliberate collector representation differs in the checked identity/bundle metadata. Evidence: `r4/fox_reconstruction/{restore_receipt,readback_comparison,header_comparison,export_receipt}.json`.

## 8. Validate in Madden with the original disabled

Capture the original-only pack first, then launch a reconstructed-only pack after restarting Madden. Verify each applied `mods.json`; a surviving original can mask missing reconstructed dependencies. Match matchup, venue, explicit broadcast context, resolution and comparable UI states. `Calendar` can select a night package, so it is not an explicit afternoon control. **JR’s Quick Play rule (2026-11-03): before starting each test, open Advanced Settings and set First Choice to Receive.** Record the observed setting. This avoids beginning with a user-controlled kickoff, which has blocked automated pause/navigation in earlier tests; it is a practical workaround, not proof that every pause failure has the same cause.

Initial FOX v3 result: original PID34536 and rebuilt PID64320 both rendered NO–CAR afternoon kickoff and first-down graphics. Watermark, team logos, fonts, clock/quarter, timeout bars and down-and-distance styling visually matched. Full frames are not pixel-identical because the scene and timing differ. Nonzero score changes, timeout decrement, possession reversal, penalties/scoring animations and endgame have not yet been compared.

A rendering pass is distinct from a functional/lifecycle pass. For a suspected bug fix, reproduce the concrete failure first and test that exact trigger afterward. For a game update, recheck build/type compatibility, changed vanilla dependencies, saved-project readback and live rendering; do not assume a previous cache remains valid.

## 9. Compare author updates and carry local changes forward

Keep three layers separate: original author version, recovered author snapshot, and our local adaptation. For each update:

1. Inventory headers before installation. Compare resource sets and expected hashes, then metadata/identity changes separately. Name changes can look like removal/addition; they do not establish new behavior.
2. Apply old/new versions in separate single-mod packs on the same game build. Capture and decode both with provenance. Compare EBX graphs, compiled binary hashes and dependency/bundle changes.
3. Establish whether IDs/names changed, whether assets were split/shared, and whether global font/collector/wrapper behavior changed. Do not transplant old routing code over an updated wrapper without inspecting it.
4. Reapply our smallest local changes onto the new recovered snapshot, keeping an explicit old→new identity map. Avoid copying the entire old reconstructed project over the new author version.
5. Repeat offline checks and the relevant live regression cases. Classify failures as source-mod behavior, game-build compatibility, recovery error, reconstruction error, or our integration error.

A matching header hash suggests an unchanged expected direct record; it does not prove the whole package behaves identically. A changed hash identifies a candidate to inspect; it does not establish a bug fix. Read author release notes separately when available.

## 10. Scope of generalization

This workflow is promising for asset mods whose applied changes exist in readable game archives: UI, textures, fonts, gameplay data and some legacy files. Recovery may require different parsers for different resource types. Handler deltas, generated/runtime-only changes, executable patches, other game builds and unsupported compression/storage are separate problems. A functioning mod is not necessarily stored as a complete self-contained author project after application.

For a dynamic CBS/FOX scorebug, first recover each chosen version separately. Then classify shared identical assets, shared conflicting assets and network-specific assets. Keep nonconflicting custom identities where safe and share dependencies only after checking both their graphs and downstream references. Remap conflicting dependencies as a complete chain, compose shared fonts/collectors deliberately, and connect the afternoon selector while preserving night routing. The R5 selective HUD diagnostic retains original CBS font/texture chains and namespaces its widget/style graphs; its startup passes while broader texture-renaming diagnostics remain unresolved. A successful identity plan need not rename every asset. Test NFC→AFC→NFC and AFC→NFC→AFC without restarting, plus night bypass and UI state changes. The old v3 FOX recovery and selector proof remain useful controls; they do not prove the updated v4.1 graphs or a combined package yet.

## 11. Local implementation and evidence map

| Location under `verify_output/dynamic_presentation/2026-11-01/` | Purpose |
|---|---|
| `r4/applied_assets/AppliedAssetHarness.cs`, `build_applied_research.sh` | Applied metadata/capture location research |
| `r4/applied_assets/decode_hud_record.py`, `decode_fox_ebx_batch.py`, `decode_fox_binary_batch.py` | Captured CAS decompression experiments |
| `r4/applied_assets/DecodedEbxHarness.cs` | Normal EBX parsing and fresh-project experiments |
| `r4/applied_assets/fox_*_manifest.json`, `fox_*_results.json` | Source locations, hashes, decode/parse coverage |
| `r4/fox_reconstruction/FoxRebuildHarness.cs`, `build_reconstruction.sh` | Complete fixed FOX v3 project construction/readback |
| `r4/fox_reconstruction/compare_readback.py`, `check_export.py` | Saved-project and header comparison gates |
| `r4/fox_reconstruction/RebuiltFox.fbproject`, `live_comparison.json` | Editable reconstruction and bounded live result |
| `r5/scorebug_version_inventory.json`, `scorebug_version_diff.json` | Read-only v3 versus newly downloaded v4.1 inventory |

Stable repo tools: `tools/fbmod_inspect.py` reads headers; `tools/mod_builder/harness.py` wraps ordinary project operations; `pipeline/mod_packs.py` handles pack config. The ignored research snapshots remain necessary to reproduce the current recovery. Promoting them into a configurable, version-checked recovery tool is future engineering work, not completed by this documentation.
