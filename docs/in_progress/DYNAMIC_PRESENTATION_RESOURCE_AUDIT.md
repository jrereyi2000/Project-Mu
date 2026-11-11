# Dynamic Presentation Resource Audit

Audit date: 2026-11-05. Baseline: the validated v0.28 three-mod research stack.

## Result

The inventory audit is complete; source-mod parity is not complete. Every one of the **2,116 source records** has a classification and evidence entry. Those are source rows, including shared resources repeated across mods; they are not 2,116 independent features. The effective stack contains **956 unique resources**.

FOX v4.1 is fully accounted for at this offline classification level. CBS has confirmed missing banner layout overrides plus additional unresolved records. The night packages retain shared-logo and timeout/halftime-flow gaps. Therefore the earlier broad claim that all afternoon source changes were incorporated would be too strong.

## Inputs and priority

Highest priority first:

1. Mu Holiday Replay Routing Probe v0.28.
2. Mu Primetime Branding Quarter Research v0.3.
3. Mu Primetime Graphics Branded Research v0.3.

Source scope: retained CBS/FOX **v4.1** headers and TNF/SNF/MNF **v2.3** headers. Older v3 afternoon packages are historical and are not the completion target. This audit does not cover later upstream releases. SHA-256 fingerprints of input manifests and all three current packs are retained in the ledger.

## Resource accounting

| Source | Direct payload match | Combined/preserved implementation | Intentionally excluded | Missing or unresolved | Total |
|---|---:|---:|---:|---:|---:|
| CBS | 417 | 210 | 0 | 134 | 761 |
| FOX | 539 | 219 | 0 | 0 | 758 |
| TNF | 32 | 1 | 133 | 16 | 182 |
| SNF | 30 | 1 | 143 | 22 | 196 |
| MNF | 40 | 1 | 155 | 23 | 219 |

Totals: **1,058 direct**, **432 combined/preserved**, **431 deliberately excluded**, and **195 unresolved source rows**. The unresolved rows represent **160 unique resource identities**, not 195 established bugs.

Classification rules:

- **Direct** means the same key and source-declared payload SHA-1 occur in the effective stack. Metadata differences are separately listed; activation is not implied.
- **Combined/preserved** requires either documented combined routing or an unchanged current payload from a previously structurally checked export. CBS-to-FOX transfers require explicit source agreement. These classifications do not imply every original source branch was behaviorally reproduced.
- **Excluded** covers 430 audio-related source rows outside the visual reconstruction and the TNF shared generic replay movie record. These exclusions are not parity. The generic movie was deliberately left untouched to protect afternoon games; v0.28 routes the network-specific replay variants instead.
- **Unresolved** includes absent overrides, differing existing payloads without a demonstrated equivalent, and identical payloads found at other identities without verified bindings. No equivalence is inferred from a similar name.

No mod or live pack was changed by this audit.

## Confirmed omission: CBS banner positioning

The current stack has no overrides for these four source CBS widgets. Fresh vanilla readback confirms the source changes real layout fields:

| CBS widget basename | Source OffsetStart / OffsetEnd | Vanilla OffsetStart / OffsetEnd |
|---|---:|---:|
| GenericNFLNameplateWidget_MH | 220 / -220 | 324 / -324 |
| MVPBannerWidget_MH | 220 / -220 | 324 / -324 |
| PlayerBannerWidget_MH | 220 / -220 | 324 / -324 |
| PlayerRecordBrokenBannerWidget_MH | -225 / 225 | -111 / 111 |

These are fields on the first container's VerticalLayout, not guessed screen pixels. They establish omitted source positioning changes, not necessarily a newly observed runtime defect. Restoring them must preserve FOX and night behavior; do not apply CBS-specific layout changes globally without checking consumers. This is separate from the accepted holiday scorebug/ticker overlap investigation, which remains deferred.

## CBS work still to reconcile

Of CBS's 134 unresolved rows, **98 exist under the same identities with different payloads**, while **36 source override records are absent** (including the four confirmed layout omissions). Much of the differing set is shared SNF/MNF/TNF HUD content: the CBS and FOX source packages are not identical there. Treat this as variant reconciliation, not 98 missing assets.

The absent set includes:

- The four banner positioning overrides above.
- CBS bottom-line ticker wrapper/widgets and ticker scorebox, plus the supersim observer support record.
- CBS wildcard/divisional/championship corner assets and related pixel chunks.
- Two CBS down/distance and quarter font styles.

Many source-only names do not exist in vanilla because the original mod adds them. The retained source GUID dependency graph now confirms that the CBS ticker is referenced by its original wrapper and that its game/team/time/alert widgets form a dependency chain. The CBS postseason corner assets are referenced by the source watermark widget. These are not merely unreferenced leftovers in the parsed source scope. Their dynamic selection and the remaining font-style uses still need reconciliation before integration.

The separately named CBS core scorebug assets are already mapped through the saved namespace/structural evidence. Those were not falsely counted as missing merely because their original names changed.

## Night-package work still to reconcile

All **24 network-specific media EBX slots** (eight per network) and their packaged media content were already source-verified; the replay and opening runtime observations remain valid.

The remaining non-audio issues cluster into:

1. **Shared logos.** The originals replace common EA/NFL/general logo slots, including global icon-library assets. We deliberately used targeted consumer replacements to avoid branding afternoon games as a night network. Those replacements are not yet an exhaustive audit of every original shared-logo consumer. Uncovered halftime, postgame or UI uses remain open.
2. **Timeout/two-minute flows.** `two_minute_p`, `two_minute_offline_p` and, where present, `2min_music_p` source overrides are not incorporated. Their visual versus audio effects still require classification; a filename is not enough to call them redundant or audio-only.
3. **TNF halftime flow.** The original TNF `halftime_p` and `halftimeshow_p` overrides remain absent. We have not recovered their source-applied bytes for comparison. Included halftime clips do not establish routing or equivalent sequencing.
4. **Alternate identities.** Three TNF records have matching payloads elsewhere in the stack but lack demonstrated bindings for their source roles. They remain unresolved.

The source Sizzle Flow is recorded as replaced by combined opening routing, rather than copied wholesale. Tested opening outcomes do not prove equivalence to every original flow edit.

## Effective-stack conflict

One overlapping key has different payloads across the enabled packs: `prg_sizzle_flow_p`. The high-priority v0.28 flow wins over Graphics Branded v0.3, as intended for combined opening routing. No other differing-payload overlap was found among these three inputs. This does not scan unrelated enabled mods.

## Runtime coverage is separate

User observations support these families in the tested franchise fixtures:

- AFC/NFC afternoon scorebug and watermark changes across games in one Madden process.
- Ordinary night scorebugs, watermarks, openings and inspected branding scenes.
- Holiday TNF/MNF/SNF openings, scorebugs and watermarks.
- v0.27 holiday quarter-card and ordinary fallback checks.
- v0.28 night replay transitions and ordinary afternoon fallback.

These observations do **not** individually validate every inventory record, all player/stat banners, all teams, postseason variants or every halftime/postgame scene. Ordinary night halftime content was observed in an earlier test without separate per-network coverage. Holiday halftime remains open. The accepted Thanksgiving MNF/SNF lower-edge overlap remains a known cosmetic limitation.

## Recommended remaining work

1. Restore or explicitly replace the confirmed CBS banner layout changes, then reconcile CBS ticker/support and postseason records through source reference closure.
2. Resolve the differing CBS/FOX shared night variants without duplicating global assets unnecessarily.
3. Complete shared-logo consumer coverage and classify the timeout flow edits.
4. Recover/compare TNF halftime flows and finish holiday halftime routing.
5. Run focused regression checks, consolidate the package and remove diagnostic overlays/markers.

This audit does not authorize or perform those implementation changes.

## Afternoon presentation, team logos and music add-ons

The original five-source ledger omitted these six separate packages. The expanded comparison covers all 2,175 non-embedded source rows against the v0.29 probe and the two v0.3 branding mods, in priority order. It compares resource identity, declared payload SHA-1 and relevant metadata. It also looks for identical payload hashes under other identities. These figures are separate from the v0.28 baseline ledger.

| Source package | Source rows | Absent at source key | Same key, different payload |
|---|---:|---:|---:|
| CBS Presentation v2.3.1 | 202 | 200 | 2 |
| FOX Presentation v2.3 | 199 | 197 | 2 |
| CBS NFL Team Logos v1.4 | 446 | 446 | 0 |
| FOX NFL Team Logos v1.4 | 450 | 450 | 0 |
| Real Broadcast Music CBS/Primetime v1.4 | 430 | 428 | 2 |
| Real Broadcast Music FOX/Primetime v1.4 | 698 | 696 | 2 |
| **Total** | **2,175** | **2,167** | **8** |

No source payload hash from these six packages matches the stack at the same or another resource identity. A reconstructed visual may have a different hash, so the overlap records remain distinct from a claim about every visual in game.

- **Presentation.** Both packages target the same 14 movie and video-background identities, including the matchup background, replay wipes, player/team ID backgrounds, touchdown media and day/dusk halftime clips. All 14 identities are absent from the comparison stack. The pair shares 194 keys and differs in payload at 179. The two rows per package present at a different hash are the common watermark EBX and RES, where the stack already implements network watermark selection. Other named additions include global branding textures, two-minute flows and broadcast audio. The installed CBS source is titled *Default Halftime animations*.
- **Team logos.** CBS has 223 texture resources and 223 chunks. FOX has 225 of each. They use the global team-logo library and share 446 identities, with 127 different payloads. Network branding needs scoped logo consumers or equivalent per-game selection. Applying both global libraries in a fixed order would choose a winner for each collision.
- **Broadcast music.** These are CBS/Primetime and FOX/Primetime packages. They share 239 keys and differ at 202. Both touch the SNF opening movie EBX and its linked chunk. The stack uses different payloads from the existing night graphics mod at those two identities. The music pair also overlaps each afternoon presentation package at 72 audio identities with different payloads. The intended track and opening version must be reconciled per scene before integration. Audio selection across games in one Madden session remains unverified.

JR explicitly added the two music packages to the integration scope. The original audit's audio exclusions describe the existing build and no longer define the desired final scope. Preserve those historical counts until the replacement implementation is measured.

The integration work separates matchup and replay media, team-logo consumers, and audio routing. Pregame media needs a network decision available before the matchup card resolves. The v0.29 flag was observed on the HUD, so its availability to that earlier caller needs verification. JR confirmed the expected flag across the tested conditions. The quarterback entry panel uses a separate visual surface from the scorebug stats card, and its exact widget and absence in v0.29 remain open.

The row-by-row comparisons, package fingerprints, alternate-hash lookups and pairwise collisions are in the r5 artifact `resource_audit/afternoon_addon_comparison.json`. No in-game behavior was tested for this comparison.

## Afternoon matchup and replay trace

The native `mograph_matchup_lowerthird_p` graph calls `L3_PreGame_MatchUp_MH`. Its Default_Schematic renders the matchup widget in bundle 58196. The widget's movie-name input feeds its existing holiday selector and MovieQuality prefab. The ordinary branch requests `L3_PreGame_Matchup_MH_BG`. The native replay-in and replay-out MH widgets pass `WipeReplay_MH_In` and `WipeReplay_MH_Out` directly to the MovieQuality prefab in bundles 58174 and 58203. CBS and FOX source payload hashes differ at all three corresponding movie slots.

The matchup schematic already instantiates `MatchupDatasourceInfo`. That prefab consumes the native match-life team reader's AwayTeam field (0x5fe1e2b6), splits FootballTeamData and exposes the ID on pin 0x05a70184. This supplies a concrete candidate for early conference routing. The ID convention and its readiness at movie selection still need a runtime check. Replay can use the validated v0.29 flag once its channel is made available in both wipe bundles. Keep the existing holiday chooser branches and route only the ordinary afternoon media on the first pass.

The player ID widget independently requests `ID_Player_MH_BG`. This confirms a player ID media route. The quarterback entry panel in JR's screenshot still needs its own caller and widget identification. The shared generic replay movie also has separate callers, outside the two MoGraph wipe paths.

Evidence is in the r5 artifact `afternoon_media_trace/trace_receipt.json` and its vanilla readback. The v0.30 pregame diagnostic adds one matchup widget override to v0.29. It reads AwayTeam from the native match-life team reader and uses the matchup prefab's FootballTeamData splitter field to display the raw ID. Saved readback verifies the three new property bindings. Export comparison verifies 854 records and preservation of the 853 prior records. In-game checks should capture the ID at first appearance of the matchup card for one AFC-away and one NFC-away afternoon game in one Madden session. JR observed ID 32 on the Browns–Texans card and ID 21 on the Panthers–Saints card. The existing HUD table maps 32 to CBS and 21 to FOX. This verifies that the IDs are available during the two observed cards. The next media build must verify that selection happens before playback begins. See `afternoon_media_trace/pregame_away_export_receipt.json` for the export fingerprint.

The v0.31 timing probe routes the ordinary matchup movie through the confirmed pregame team reader and the existing HUD conference case table. AFC selects the installed SNF matchup background and NFC selects the installed TNF matchup background as test signals. ID 0 and unknown IDs use the native background. The saved graph retains the holiday selector and existing playback path. Export and readback checks verify all 34 switch events and one changed widget relative to v0.30. JR confirmed that both expected test backgrounds played immediately on the matchup cards. This validates timing for the tested CBS and FOX afternoon fixtures. Evidence is in `afternoon_media_trace/pregame_movie_export_receipt.json`.

### Original afternoon media capture

The retained applied packs contain the v0.31 timing stack and no standalone CBS or FOX Presentation capture. The r5 `afternoon_media_trace/capture/` script reads named applied ModData packs and checks each stored movie EBX and its linked chunk against the original mod header SHA-1. The control recovered one SNF matchup EBX and its linked chunk from `HudFranchiseNative`, with both hashes verified. Its receipt is at `C:/Madden27Modding/MuHolidayTrace/EventHub/AfternoonMediaTrace/capture_control/verified_media.json`.

JR applied the original CBS and FOX Presentation mods in separate source packs. Both captures passed source hash verification for all 14 movie EBX records and all 14 linked chunks per network. Receipts and captured records are under `C:/Madden27Modding/MuHolidayTrace/EventHub/AfternoonMediaTrace/` in `capture_cbs` and `capture_fox`. The next build can give the two matchup movies distinct asset names and select them through the validated v0.31 route.

The v0.32 Afternoon Matchup Probe replaces the two test movie names with independently named copies of the original CBS and FOX matchup movies. Saved-project readback matched both video chunks byte for byte. The export contains 858 gameplay resource records, with the matchup widget changed and two movie EBX records plus two chunks added. The other 853 records match v0.31. The build and receipt are in `C:/Madden27Modding/MuHolidayTrace/EventHub/AfternoonMatchupBuild/`. Runtime validation is pending.

JR's v0.32 Texans at Browns screenshot shows the stadium background during the matchup card, with away ID 32 and CBS layout flag 1. The applied `HudFranchiseNative` manifest confirms v0.32. Reading its CAS records verifies the v0.32 widget contains both alias names, both movie descriptors point to their respective added chunks, and both applied video payloads match the source captures byte for byte. This narrows the failure to runtime selection or lookup/playback of the aliases. The v0.31 timing pass used stock movie names. Use an existing movie slot as the next control to distinguish runtime lookup from a problem with the source clip. The FOX runtime result is open.

The v0.33 CBS Stock Movie Control uses the v0.31 selector and replaces the existing high-quality SNF matchup movie and its chunk with the recovered CBS media. The saved-project readback verifies the original CBS video bytes and stream metadata. The export retains all 854 v0.31 records and adds exactly two stock resource overrides. The SNF matchup background can show CBS under this diagnostic. Build and receipt are in `C:/Madden27Modding/MuHolidayTrace/EventHub/CbsStockMovieControl/`. JR confirmed the CBS clip played on Texans at Browns. This validates the clip through a stock movie name. In v0.32, the new aliases were present in the applied pack but were inserted into the original MH movie bundle. Stock high-resolution movies have a movie-specific bundle name. The offline trace located the registry. `global/Logic/InGame/UI/MovieQuality_Prefab` sends the movie name and quality suffix to an `AssetResolveEntityData` node. Its `{0}_{1}` pattern resolves `MovieTexture2Asset` through `content/FMV/high_movies_brt` and `content/FMV/low_movies_brt`. Those EBX assets reference compiled RES records `5c7e0e7b8cf26f2d` and `a61c609867c777f9`.

Each stock MH, SN and TN matchup appears in both compiled tables under two 64-bit FNV-1 keys, one for the lowercase leaf name and one for the lowercase full asset path. For each movie the two keys share the same trailing eight bytes in their 16-byte lookup records. Neither custom alias has either key in the stock tables, and v0.32 did not override the tables. This explains why adding movie data to a stock bundle alone was insufficient. Dumps are retained in `C:/Madden27Modding/MuHolidayTrace/EventHub/CbsStockMovieControl/`. The v0.34 CBS Movie Registry Probe adds one high-quality CBS path and two lookup keys, preserves all 710 stock keys, and exports a dedicated movie bundle. Saved-project readback matches the CBS clip and patched table. Export verification confirms the bundle link and the updated RES relocation metadata. The v0.31 FOX timing clip is still in the selector as a control. The applied manifest confirms v0.34. JR reported a crash, and the corresponding Madden dump records access violation 0xc0000005 at executable RVA 0x1fbe8d7. The address alone does not identify the failing resource. The build replaced the raw registry and added a bundle in one test, so those changes are not isolated. The installed MMC BundleRefTable plugin exposes a V2 parser/writer, a duplicate-entry API and a Mod Manager merge handler. MMC parses and semantically round-trips both the stock and v0.34 tables. The applied v0.34 table, relocation metadata, CBS video and dedicated bundle also match the exported intent. The plugin-generated duplicate delta preserves all 710 stock lookups and adds the same two CBS keys, but points them to the existing MH bundle reference 21 rather than adding reference 357. The plugin-merged table round-trips with 357 bundle references and 712 lookups. The v0.35 CBS Existing Bundle Probe pairs that lookup with the v0.32 alias asset placement in the existing MH bundle. Readback verifies the registry and clip bytes. Export comparison against v0.31 confirms one updated widget and three additional records for the table, movie EBX and chunk. The probe adds no bundle or stock movie override. The table is the compiled output of MMC’s duplicate merge. Build and receipt are in `C:/Madden27Modding/MuHolidayTrace/EventHub/CbsExistingBundleProbe/`. JR confirmed the CBS video played on the Texans at Browns matchup card. This validates the high-quality CBS alias through the existing afternoon movie bundle. FOX and the low-quality route are outside this runtime check. The v0.36 Afternoon Dual Movie Probe registers the original FOX clip beside CBS in the existing high-quality afternoon bundle. The table preserves all 710 stock keys and adds four alias keys. Relative to v0.35, the export changes the matchup widget and high-quality registry and adds the FOX movie EBX and video chunk. Readback checks passed. JR confirmed the original CBS clip on Texans at Browns and the original FOX clip on Panthers at Saints in one Madden session. This validates the high-quality afternoon matchup cards. The low-quality route and replay wipes remain separate. Build and receipt are in `C:/Madden27Modding/MuHolidayTrace/EventHub/AfternoonDualMovieProbe/`. Evidence is in `plugin_verification_receipt.json`. The exact v0.34 crash cause remains unresolved. Build and receipts are in `C:/Madden27Modding/MuHolidayTrace/EventHub/CbsMovieRegistryProbe/`.

The v0.37 Afternoon Replay Probe registers four original CBS and FOX replay-in and replay-out movie aliases in the two existing high-quality replay bundles. It adds eight lookup keys and retains the 714 v0.36 keys. The two MH replay widgets use the existing CBS layout channel to select CBS for 1 and FOX for 0, with a native default. Saved-project readback verifies both switches, channel bundle coverage, four captured video payloads and the registry. Export comparison confirms 869 records. JR confirmed the CBS and FOX replay wipes in the same Madden session on Texans at Browns and Panthers at Saints. This validates the high-quality afternoon replay-in and replay-out routes for those matchups. Build and receipt are in `C:/Madden27Modding/MuHolidayTrace/EventHub/AfternoonReplayProbe/`.

The halftime trace found the caller in `Bumper_HalftimeReport_Widget` (bundle 58170). Components 30–32 build the outro, loop and intro names from a common prefix, a theme token, a day/dusk token, and the segment suffix. Their outputs feed the existing MovieQuality prefab instances before the default movie selectors. The theme token is also consumed by the independent still-image path. The v0.38 Afternoon Halftime Probe clones the movie theme selector and substitutes CBS/FOX tokens only for its MH input, using the existing CBS layout channel. The still-image route, day/dusk decision, playback sequence and stock night-theme choices retain their original connections. The build registers twelve source-verified high-quality movie aliases in their stock bundles. Saved-project readback verifies the widget topology, movie bytes, bundle coverage and registry. Export comparison confirms 894 records and preservation of all 722 prior lookup keys. This build covers afternoon halftime movies. Holiday night routing and the original TNF flow overrides remain separate. JR observed alternating CBS and FOX halftime intro and outro playback on Texans at Browns. A plausible cause is the v0.38 binary layout flag treating initialization value 0 as FOX. The v0.39 Halftime Away Team Probe replaces the halftime flag reader with the native away-team reader and the same 34 conference routes used for the validated matchup cards. Zero and unknown IDs select the stock MH token. Its export differs from v0.38 in the widget and restoration of the v0.37 channel record. All movie payloads and the lookup table compare equal. Saved-project verification passed. Runtime validation is pending. Trace and build evidence are in `C:/Madden27Modding/MuHolidayTrace/EventHub/HalftimeRoutingTrace/` and `C:/Madden27Modding/MuHolidayTrace/EventHub/AfternoonHalftimeProbe/`.

JR still observed both networks in Texans at Browns under v0.39. Offline frame samples distinguish the captured CBS red/blue Verizon report from the FOX robot and gold FOX NFL sequence. The CBS samples do not show FOX. Stock intro, loop and outro elements all have Loop and AutoStart disabled. The v0.40 CBS Only Control changes one token in the v0.39 widget so both custom afternoon branches request the CBS videos, preserving the two branch events. The resource comparison verifies only the halftime widget changed. JR reported that the CBS-only control played once on Texans at Browns. Since v0.40 changes only the FOX token to CBS, this implicates the widget’s changing movie request in the v0.39 mixed playback. The source of the second selection event is still unknown. Saved v0.39 graph trace confirms all 88 stock event edges remain. The away-team switch has TriggerOnStart and TriggerOnPropertyChange enabled and supplies a live token to all three movie builders. Intro resolve starts through component 22 after the stock theme and day/dusk OnSelected events. That gate still listens to the stock theme selector, has a self-reset, and has no dependency on the new network decision. Intro resolve callbacks can feed playback through a splitter with RunOnce disabled. This allows selection and playback readiness to diverge, but the static trace does not establish which update occurred in-game. The next control should disable automatic network switch firing, sample the away-team ID once at the existing intro readiness event, and start intro resolution after the network selector acknowledges the choice. Keep the token fixed for loop and outro. The repository already contains a native HUD integer switch with both automatic triggers disabled and an explicit In event. Evidence is in HalftimeRoutingTrace/selection_timing_receipt.json and HalftimeAwayTeamProbe/widget_readback.json. Build and receipt are in `C:/Madden27Modding/MuHolidayTrace/EventHub/HalftimeCbsOnlyControl/`.

The v0.41 Halftime One Shot Probe disables automatic evaluation of the native away-team switch in the v0.39 halftime widget. The existing intro readiness event now enters an auto-closing gate, triggers the switch once, and resolves the intro on the network selector's OnSelected event. The chosen network token is shared by the three movie builders for the report. Saved readback confirms the new gate and event path. Export comparison verifies only the halftime widget changed. Build and receipt are in `C:/Madden27Modding/MuHolidayTrace/EventHub/HalftimeOneShotProbe/`. JR observed one CBS intro on Texans at Browns, followed by a FOX-branded closing graphic. Applied CAS verification matches the registry and all four source outro videos, and each EBX points to its expected chunk. The FOX halftime outro begins with a gold FOX NFL HALFTIME REPORT title. The independently routed FOX replay-out wipe begins with a blue oval FOX SPORTS logo and then shows the robot. Identify the observed graphic before changing either selector. See `applied_outro_receipt.json`.

## Implementation follow-up: v0.29 candidate

The first CBS omission candidate is built from v0.28, with offline verification complete and runtime validation pending. It adds separate copies of the four banner widgets with the exact source layout offsets. The original widget definitions remain unchanged.

A separate integer channel publishes a CBS-layout flag from the existing HUD away-conference decision, enabled only for `ScoreHud_Standard`. The banner chooser reads that flag and chooses the corresponding CBS copy for the four banner families (six native enum cases, because record banners have multiple cases). Other banners, FOX games and night packages retain the resolved original widget. The diagnostic HUD now displays **CBS layout flag** in place of HUD event count: expected 1 for a CBS afternoon game and 0 for FOX/night games.

This candidate does not close the audit rows yet: new channel delivery and selection need a runtime check. Ticker, postseason, font-style and shared-night-variant work remains. The v0.28 accounting table above remains the validated-baseline audit; it has not been silently relabeled as v0.29 parity.

Build: `C:/Madden27Modding/MuHolidayTrace/EventHub/Mu_CBS_Banner_Layouts_Probe.fbmod`. Evidence: `verify_output/dynamic_presentation/2026-11-01/r5/cbs_banner_layouts/export_receipt.json` and saved readback. Only ScoreHudWrapper and RimeBannerWidgetChooser are modified from v0.28, alongside five new assets (four widgets and one channel). All other original v0.28 resource payloads remain unchanged.

## Evidence and reproduction

Full row-by-row ledger, checksums, providers, metadata deltas and evidence links:

- `verify_output/dynamic_presentation/2026-11-01/r5/resource_audit/resource_ledger.json`
- `verify_output/dynamic_presentation/2026-11-01/r5/resource_audit/summary.json`
- `verify_output/dynamic_presentation/2026-11-01/r5/resource_audit/vanilla_ebx_comparison.json`
- `verify_output/dynamic_presentation/2026-11-01/r5/resource_audit/vanilla_ebx_field_differences.json`

The ignored artifact directory also retains the fresh vanilla readback, source-root comparison and reproducible `audit_resources.py` script. Run from the canonical repository root:

```sh
python3 -c "import runpy; runpy.run_path('verify_output/dynamic_presentation/2026-11-01/r5/resource_audit/audit_resources.py', run_name='__main__')"
```

The script uses the pinned Windows v0.28 artifact through `/mnt/c` plus repository-retained source/verification artifacts. It checks all source row totals, unique keys, current pack priority and input fingerprints. It is an offline artifact audit, not a game test or an encrypted payload extraction.
