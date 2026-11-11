# Dynamic presentation mod handoff

**2026-11-10 · Codex / GPT-6.1 Sol · Board notice M-0287**

## Where to resume

Start with the **v0.41 afternoon halftime closing transitions**. JR's final test observation, transcript entry **1105**, supersedes the initial FOX-outro report in the resource audit.

| v0.41 fixture | What JR observed | What remains to establish |
|---|---|---|
| Texans at Browns, CBS, full halftime | One CBS intro and the CBS outro | Use this as the full-playback control. |
| Texans at Browns, CBS, skipped halftime | FOX replay wipe at the transition | Trace the skip/return-to-play path and its replay-out selector input. |
| Panthers at Saints, FOX | FOX intro and two closing graphics, described as FOX and CBS outros | Identify the CBS graphic before changing either route. Red-and-blue **Verizon Halftime Report** identifies the CBS halftime outro. A white circle with **CBS \| NFL** identifies the CBS replay wipe. |

If JR remembers the second Panthers–Saints graphic, use that observation. The transcript ends with the question unanswered. Otherwise start offline with the v0.41 project, the v0.37 replay widgets and the native skip/end flows, including `NightMediaTrace/0299_skiphalftime_p.bin`. Trace the replay-out caller used at skip, whether it reaches the normal MH replay-out widget, and what the CBS layout flag and its lifetime are at that point. Trace the outro request and return-to-live transition independently. If static evidence leaves both possibilities open, use a single discriminating probe or a short recorded Panthers–Saints halftime. Test one change against the relevant control before combining fixes.

The next acceptance check should cover full and skipped halftime for both fixtures in the same Madden process. Check one appropriate intro, the appropriate report and outro when played in full, and the correct transition on skip. Keep ordinary replay-in and replay-out, night media and the stock fallback working. Record whether a franchise reload occurred independently of the process staying up.

## Goal and operating boundaries

JR wants one installed Madden 27 `.fbmod` that switches coherent presentation between consecutive **Franchise** games without changing packs or restarting Madden. The research stack has three mods for isolation. The intended release is one package.

- Ordinary afternoon uses the **away team's conference**. AFC selects CBS and NFC selects FOX. Ordinary TNF, SNF and MNF take priority. Tested holiday nights use native pregame weekday and kickoff-time predicates for broadcast while Madden retains its holiday theme.
- The current implementation uses native Madden context. Mu can derive a choice from its schedule, but no verified in-process transport lets Mu supply an arbitrary override between games. A manifest, script hook and save field were hypotheses. Weekly League Office save changes still require returning to the Franchise List and reloading the edited save. This is independent of the presentation mod.
- JR operates Madden and sets up the games. The agent prepares builds and uses read-only verification. This supersedes the original chat's early automation workflow. Ask JR about familiar game behavior before spending a launch on it. Keep tests focused and reuse the established fixtures.
- Use an isolated research pack and disposable `TUNE-*` saves. Stop the save watcher for live research and coordinate Madden PC use through the board before a run. Preserve Mu Base, production routing, active league saves/databases and unrelated worktree edits. The board handoff does not authorize production changes.

## What has been verified in Madden

| Area | Evidence and limits |
|---|---|
| Native selection and independent assets | G0 established afternoon and SNF at the same stadium in one process. G1 traced native theme to visible graphics. G2 v0.2 showed SNF → afternoon → SNF with one mod. The return leg loaded a scratch save. A separately named cloned HUD asset rendered in v0.3. v0.5.1 passed same-stadium AFC → NFC → AFC without a reload. Its predecessor failed because added nodes lacked client event/property flags. |
| Combined afternoon scorebugs | The original Franchise-native wrapper drove CBS → FOX → CBS across DEN at CAR, CAR at NO and BAL at CAR. A separate rebuilt Quick Play wrapper returned team ID `33` for Kansas City instead of `9`, even on fresh games. A fixed-ID control selected CBS. Do not reopen that Quick Play diagnostic to solve a Franchise regression without new evidence. |
| Ordinary five-network graphics | Combined HUD Watermark v0.31 plus the opening add-on passed matching CBS/FOX/TNF/SNF/MNF scorebugs and watermarks, night openings, and no night bumper on the two afternoon controls. AFC-away night precedence also passed. Branded Graphics v0.3 and Quarter Research v0.3 added dedicated ID, badge and quarter-card branding with scoped in-game coverage. Ordinary night replay and halftime were observed, with no complete per-network halftime matrix. |
| Holiday opening and HUD | Halloween CAR at GB and Thanksgiving-week CAR at TB received holiday themes and Standard HUD from Madden. Native pregame weekday and time distinguished night fixtures from afternoon controls. The bumper caller accepted short registered aliases, such as `Bumper_NFL_SN`, while the `_High` filename request produced no video. v0.18 verified event delivery from the observed ordinary SNF lower-third branch. v0.22 verified holiday delivery from the confirmed bumper branch with `93 → 1 → 93` across games. v0.25 passed the holiday night opening, scorebug and watermark checks in the tested fixtures. The watermark has a separate local theme input. Holiday match-channel markers were TNF `93`, SNF `185`, MNF `277`, ordinary `1`. |
| Holiday banners and replay | v0.27 passed holiday quarter cards and an ordinary fallback control. v0.28 passed tested holiday night replay transitions and native afternoon fallback through the shared replay theme chooser. The Thanksgiving MNF/SNF bottom-edge overlap survived the v0.26 local-theme change. JR accepted deferring that cosmetic issue. |
| CBS banners and afternoon movies | v0.29 restored four source banner layouts with separate CBS copies. JR verified the CBS layout flag but the standalone player-entry/quarterback stats panel and actual standalone banner placement remain open. The matchup card read Houston away ID `32` and Carolina away ID `21`. v0.36 played the original CBS and FOX matchup clips in the same session. v0.37 passed the original CBS/FOX replay-in and replay-out wipes in the same two fixtures. Those movie tests cover the high-quality route. |

## Build and investigation checkpoints

- **Current research build.** In `HudFranchiseNative`, the last test used **Mu Halftime One Shot Probe v0.41** at highest priority with **Mu Primetime Branding Quarter Research v0.3** and **Mu Primetime Graphics Branded Research v0.3** below it. Treat this as the last tested stack, and recheck Mod Manager's applied manifest before relying on it. Use v0.37 as the validated afternoon matchup/replay reference. Use v0.28 for the earlier holiday replay baseline.
- **v0.38–v0.41.** `Bumper_HalftimeReport_Widget` in bundle 58170 builds intro, loop and outro movie names from a theme/network token and day/dusk variant. v0.38 used the CBS layout flag, and v0.39 used native away-team ID with stock fallback. Both showed mixed-network playback in the CBS fixture. v0.40 pointed both afternoon branches at CBS and JR saw one playback. v0.41 disables automatic network selection and uses the intro-ready gate to sample the away-team ID once before resolving the intro. The selected token feeds all three movie builders. Only the halftime widget differs from v0.39. Applied CAS checks matched the registry and the four day/dusk CBS/FOX outro payloads, so verify the requesting route before rebuilding media.
- **Movie aliases.** MMC's `BundleRefTable` plugin registered aliases against the **existing** stock bundles. v0.32 had correct payloads but no registry keys. v0.33 played CBS via a stock slot. v0.34 added a raw table and a dedicated bundle and crashed. The exact crash cause is unresolved. v0.35 proved the existing-bundle registration, which v0.36–v0.38 extended. `MovieQuality_Prefab` resolves names through separate high- and low-quality tables.
- **Match channel controls.** Keep the channel in each consuming bundle and keep the writer self-contained in its prefab. Direct writer wiring in the match-life Broadcast layer crashed. A value alone does not prove event delivery. The earlier two-writer `74` interpretation was invalidated by a `93` write with the sender and event input disconnected. Use a single writer, a verified visual branch marker and an event-off or ordinary control for future event probes.
- **Applied asset recovery.** The prior chat verified compiled assets from normally applied ModData against the source mod header and retained projects, receipts and readbacks. It reconstructed the FOX scorebug and visually compared it with the standalone original at kickoff and first down. The broader combined v4.1 attempt failed at startup, and the selective HUD build provided the working base. Saved-project checks and applied bytes establish packaging. JR's observations establish runtime behavior. If MMC Import reports `Stream was too long`, the research opening mod previously worked via direct library copy while Mod Manager was closed. See [Mod install](../MOD_INSTALL.md) and [Applied asset recovery](../reference/MOD_APPLIED_ASSET_RECOVERY.md).

## Remaining work after the current fix

1. **Night halftime.** Recover and compare the standalone TNF `halftime_p` and `halftimeshow_p` changes. Trace holiday halftime independently from opening and replay. Validate normal and holiday night playback by network, preserving the holiday atmosphere and ordinary fallbacks.
2. **Source reconciliation.** Continue from both ledgers in the resource audit. The five-package audit covered 2,116 source rows at v0.28, and the later comparison added 2,175 rows from six separate CBS/FOX add-ons at v0.29. These counts refer to historical stacks. The original audit did not cover the separate CBS/FOX Presentation mods, and later builds only close specific verified paths. Remaining work includes CBS ticker, postseason corners/fonts, alternate shared-night variants, standalone player/coach banner and QB entry panel, player/team ID and touchdown media, shared-logo consumers and timeout/two-minute flows.
3. **Team logos and music.** JR explicitly added the CBS/FOX team-logo and Broadcast Music add-ons to the final scope. Both logo packs write shared global slots. Music packs overlap each other and presentation audio and share the SNF opening identities. Establish scoped per-game selection and decide which source owns each overlapping scene. Same-process audio switching is still unverified.
4. **Coverage and consolidation.** Cover low-quality movie lookups, day/dusk, postseason, unknown IDs, transitions between broadcasts and restarts where relevant. Reconcile resource precedence before combining the three research mods into one `.fbmod`, remove diagnostic overlays and markers, and run the same-session Franchise matrix on the combined export. The wider exploration plan also calls out crowd density and ball/BCA. The broadcast work here does not establish those capabilities or a production replacement for `pipeline/mod_packs.py`.

## Files and evidence

- [Exploration plan](../not_started/DYNAMIC_PRESENTATION_MOD_PLAN.md), [research chronology](DYNAMIC_PRESENTATION_RESEARCH.md) and [resource audit](DYNAMIC_PRESENTATION_RESOURCE_AUDIT.md).
- Local repo evidence at `verify_output/dynamic_presentation/2026-11-01/r5/`, especially `resource_audit/` and `afternoon_media_trace/`. Tooling and recipes are under `tools/mmc_harness/` and `tools/mod_builder/`. Verified historical environment was Madden build `3468304` and MMC Editor `1.1.0.7`. Recheck the build and config before another export.
- Windows build root `C:/Madden27Modding/MuHolidayTrace/EventHub/`, visible from WSL at `/mnt/c/Madden27Modding/MuHolidayTrace/EventHub/`. Start with `HalftimeOneShotProbe/HudHalftimeOneShot.fbproject`, `Mu_Halftime_One_Shot_Probe.fbmod`, `build.sh`, `export_receipt.json` and `applied_outro_receipt.json` in that directory. Use `HalftimeRoutingTrace/` for the selector timing receipt and media frame samples, `AfternoonReplayProbe/` for v0.37 and `AfternoonMediaTrace/capture_cbs/` and `capture_fox/` for the verified original media.
- Transcript and verified raw-log ZIP are in `C:/Users/jrere/Documents/Codex Exports/Review Presentation Mod Plan - 01a19a17/`. The transcript's entries **1079–1108** give the latest priority list and halftime observations. The older working chat is `01a19a17-4749-7771-ab4d-dc08be841806`. The export is local and includes recorded tool output in the ZIP.


## Presentation research cleanup inventory

**Snapshot on 2026-11-10.** Keep these artifacts through final acceptance. Add new probes, packs and scratch saves to this list as they are created. Recheck live pack references and save ownership before removing anything.

### Franchise saves

All five confirmed test franchises and their autosaves are in `C:/Users/jrere/Documents/Madden NFL 27/saves-archive/`. The active `saves/` directory contained none of the presentation saves below.

| Save | Autosave | Purpose |
|---|---|---|
| `CAREER-TUNE-PRESENTATION-G0` | `CAREER-TUNE-PRESENTATI-AUTOSAVE` | Native baseline and early HUD comparisons |
| `CAREER-TUNE-PRES-R2SN` | `CAREER-TUNE-PRES-R2SN-AUTOSAVE` | SNF return and Week 8 control |
| `CAREER-TUNE-PRES-R2CL` | `CAREER-TUNE-PRES-R2CL-AUTOSAVE` | HUD clone probe |
| `CAREER-TUNE-PRES-R2ID` | `CAREER-TUNE-PRES-R2ID-AUTOSAVE` | HUD team ID probe |
| `CAREER-TUNE-PRES-R3CF` | `CAREER-TUNE-PRES-R3CF-AUTOSAVE` | Away-conference and Franchise switching tests |

The research evidence folder also contains four save copies:

- `g0/CAREER-TUNE-PRESENTATION-G0`
- `r2/saves_before/CAREER-TUNE-PRESENTATI-AUTOSAVE`
- `r2/saves_before/CAREER-TUNE-PRESENTATION-G0`
- `r3/away_spike/v051/CAREER-TUNE-PRES-R2SN-WEEK8-control`

**Verify with JR before including these.** The archive also holds `CAREER-DEBUGWEEK8`, `CAREER-DEBUGWEEK10`, `CAREER-DEBUGWEEK12` and their `-AUTOSAVE` files. Their names and dates fit the Week 8, 10 and 12 presentation fixtures. The recovered records did not establish their ownership.

**Keep** `CAREER-WEEK1` and its autosave (source baseline), `CAREER-MU2026`, `CAREER-MUBASE`, unrelated tuning saves and the `CAREER-PRESZW1` through `CAREER-PRESZW3` retirement fixtures.

### Generated test mods

**113 distinct SHA-256 payloads across 274 .fbmod files.** Each row groups byte-identical copies. Distinct builds that reused a filename have separate rows.

- **L** is the Mod Manager library at `C:/Madden27Modding/MMC_Modding_Tools_v1.1.0.7/MMC_ModManager_v1.1.0.7/Mods/Madden27/`
- **W** is the Windows build root at `C:/Madden27Modding/`
- **R** is the repo research root at `/home/jrereyi/Project-Mu/verify_output/dynamic_presentation/2026-11-01/`

| Mod title | Header version | SHA-256 prefix | Locations relative to the roots above |
|---|---|---|---|
| Mu Afternoon Dual Movie Probe | 0.36 | `4bb8878b8b07699b` | L `Mu_Afternoon_Dual_Movie_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/AfternoonDualMovieProbe/Mu_Afternoon_Dual_Movie_Probe.fbmod` |
| Mu Afternoon Halftime Probe | 0.38 | `30e95e2981ef32aa` | L `Mu_Afternoon_Halftime_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/AfternoonHalftimeProbe/Mu_Afternoon_Halftime_Probe.fbmod` |
| Mu Afternoon Matchup Probe | 0.32 | `02c07684fd8841d4` | L `Mu_Afternoon_Matchup_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/AfternoonMatchupBuild/Mu_Afternoon_Matchup_Probe.fbmod` |
| Mu Afternoon Replay Probe | 0.37 | `4c9d2cf2af6dfa77` | L `Mu_Afternoon_Replay_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/AfternoonReplayProbe/Mu_Afternoon_Replay_Probe.fbmod` |
| Mu Bumper Alias Probe | 0.2 | `3dbf58e20ec73078` | L `Mu_BumperAliasProbe.fbmod`<br>R `r5/holiday_routing/Mu_BumperAliasProbe.fbmod`<br>W `MuBumperProbe/Mu_BumperAliasProbe.fbmod` |
| Mu Bumper Suppression Probe | 0.1 | `36d522f414adda7f` | L `Mu_BumperSuppressionProbe.fbmod`<br>R `r5/holiday_routing/Mu_BumperSuppressionProbe.fbmod`<br>W `MuBumperProbe/Mu_BumperSuppressionProbe.fbmod` |
| Mu CBS Banner Layouts Probe | 0.29 | `78ac5d5dab28879e` | L `Mu_CBS_Banner_Layouts_Probe.fbmod`<br>R `r5/cbs_banner_layouts/Mu_CBS_Banner_Layouts_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_CBS_Banner_Layouts_Probe.fbmod` |
| Mu CBS Existing Bundle Probe | 0.35 | `9364e28b0c102bc9` | L `Mu_CBS_Existing_Bundle_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/CbsExistingBundleProbe/Mu_CBS_Existing_Bundle_Probe.fbmod` |
| Mu CBS Fonts Only Diagnostic | 0.6-diagnostic | `0a317c68d29ec0e2` | L `Mu_CbsFontsOnlyDiagnostic.fbmod`<br>R `r5/Mu_CbsFontsOnlyDiagnostic.fbmod` |
| Mu CBS FOX Assets Only Diagnostic | 0.5-diagnostic | `177a4a3477612575` | L `Mu_CbsFoxAssetsOnlyDiagnostic.fbmod`<br>R `r5/Mu_CbsFoxAssetsOnlyDiagnostic.fbmod` |
| Mu CBS FOX Font Registration Diagnostic | 0.3-diagnostic | `7e42f61d9f22d879` | L `Mu_DynamicCbsFoxNoFontRegistrationDiagnostic.fbmod`<br>R `r5/Mu_DynamicCbsFoxNoFontRegistrationDiagnostic.fbmod` |
| Mu CBS FOX HUD Only Diagnostic | 0.4-diagnostic | `6b37819a8841ca29` | L `Mu_DynamicCbsFoxHudOnlyDiagnostic.fbmod`<br>R `r5/Mu_DynamicCbsFoxHudOnlyDiagnostic.fbmod` |
| Mu CBS Movie Registry Probe | 0.34 | `83736cec222b654b` | L `Mu_CBS_Movie_Registry_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/CbsMovieRegistryProbe/Mu_CBS_Movie_Registry_Probe.fbmod` |
| Mu CBS Native Identity Control | 0.21-diagnostic | `55a54289e6a55472` | R `r5/Mu_CbsNativeIdentityControl.fbmod` |
| Mu CBS Native Identity Control | 0.21-diagnostic | `954e11b41a31675f` | R `r5/Mu_CbsNativeIdentityControl2.fbmod` |
| Mu CBS Stock Movie Control | 0.33 | `ffd17132eeb036b7` | L `Mu_CBS_Stock_Movie_Control.fbmod`<br>W `MuHolidayTrace/EventHub/CbsStockMovieControl/Mu_CBS_Stock_Movie_Control.fbmod` |
| Mu CBS Texture Name Hash Fixed Diagnostic | 0.8-diagnostic | `ad98f34c52fda8e0` | L `Mu_CbsTextureNameHashFixedDiagnostic.fbmod`<br>R `r5/Mu_CbsTextureNameHashFixedDiagnostic.fbmod` |
| Mu CBS Texture Subset 0 1 | 0.13-diagnostic | `c95bbef79758ad42` | L `Mu_CbsTextureSubset_0_1.fbmod`<br>R `r5/Mu_CbsTextureSubset_0_1.fbmod` |
| Mu CBS Texture Subset 0 14 | 0.11-diagnostic | `52b0cf0ba6892267` | L `Mu_CbsTextureSubset_0_14.fbmod`<br>R `r5/Mu_CbsTextureSubset_0_14.fbmod` |
| Mu CBS Texture Subset 0 7 | 0.12-diagnostic | `1b00f94ee736840d` | L `Mu_CbsTextureSubset_0_7.fbmod`<br>R `r5/Mu_CbsTextureSubset_0_7.fbmod` |
| Mu CBS Texture Subset 1 1 | 0.15-diagnostic | `29fb140f5d792b21` | L `Mu_CbsTextureSubset_1_1.fbmod`<br>R `r5/Mu_CbsTextureSubset_1_1.fbmod` |
| Mu CBS Texture Subset 14 14 | 0.11-diagnostic | `c777adb0a07c3fab` | L `Mu_CbsTextureSubset_14_14.fbmod`<br>R `r5/Mu_CbsTextureSubset_14_14.fbmod` |
| Mu CBS Texture Subset 3 1 | 0.16-diagnostic | `b02fb8f979ace15f` | L `Mu_CbsTextureSubset_3_1.fbmod`<br>R `r5/Mu_CbsTextureSubset_3_1.fbmod` |
| Mu CBS Texture Subset 3 3 | 0.15-diagnostic | `ccd376db859487ca` | L `Mu_CbsTextureSubset_3_3.fbmod`<br>R `r5/Mu_CbsTextureSubset_3_3.fbmod` |
| Mu CBS Texture Subset 4 1 | 0.16-diagnostic | `76ad3dd65f48bd8b` | L `Mu_CbsTextureSubset_4_1.fbmod`<br>R `r5/Mu_CbsTextureSubset_4_1.fbmod` |
| Mu CBS Texture Subset 5 1 | 0.16-diagnostic | `841538adddea3bad` | L `Mu_CbsTextureSubset_5_1.fbmod`<br>R `r5/Mu_CbsTextureSubset_5_1.fbmod` |
| Mu CBS Texture Subset 6 1 | 0.13-diagnostic | `5db2f05052b7e9ba` | L `Mu_CbsTextureSubset_6_1.fbmod`<br>R `r5/Mu_CbsTextureSubset_6_1.fbmod` |
| Mu CBS Texture Subset 7 7 | 0.12-diagnostic | `90b53393e9f432a0` | R `r5/Mu_CbsTextureSubset_7_7.fbmod` |
| Mu CBS Textures Only Diagnostic | 0.7-diagnostic | `eff02c0d55961f41` | L `Mu_CbsTexturesOnlyDiagnostic.fbmod`<br>R `r5/Mu_CbsTexturesOnlyDiagnostic.fbmod` |
| Mu Combined HUD Away Datasource | 0.27-diagnostic | `e3297dfa62773fa4` | L `Mu_CombinedHudAwayDatasource.fbmod`<br>R `r5/Mu_CombinedHudAwayDatasource.fbmod`<br>W `MuV41Build/Mu_CombinedHudAwayDatasource.fbmod` |
| Mu Combined HUD Case Readout | 0.28-diagnostic | `13150535cfbee8c2` | L `Mu_CombinedHudCaseReadout.fbmod`<br>R `r5/Mu_CombinedHudCaseReadout.fbmod`<br>W `MuV41Build/Mu_CombinedHudCaseReadout.fbmod` |
| Mu Combined HUD Case Updates | 0.29-diagnostic | `27a8143c0994acff` | L `Mu_CombinedHudCaseUpdates.fbmod`<br>R `r5/Mu_CombinedHudCaseUpdates.fbmod`<br>W `MuV41Build/Mu_CombinedHudCaseUpdates.fbmod` |
| Mu Combined HUD Fixed KC ID | 0.26-diagnostic | `85834a3dcfc34482` | L `Mu_CombinedHudMatchupFixedId.fbmod`<br>R `r5/Mu_CombinedHudMatchupFixedId.fbmod`<br>W `MuV41Build/Mu_CombinedHudMatchupFixedId.fbmod` |
| Mu Combined HUD Franchise Native | 0.30-diagnostic | `be1d109271fb78fd` | L `Mu_CombinedHudFranchiseNative.fbmod`<br>R `r5/Mu_CombinedHudFranchiseNative.fbmod`<br>W `MuV41Build/Mu_CombinedHudFranchiseNative.fbmod` |
| Mu Combined HUD Matchup Away | 0.24-diagnostic | `d3e4dd3f64527832` | L `Mu_CombinedHudMatchupAway.fbmod`<br>R `r5/Mu_CombinedHudMatchupAway.fbmod`<br>W `MuV41Build/Mu_CombinedHudMatchupAway.fbmod` |
| Mu Combined HUD Matchup Ready | 0.25-diagnostic | `ba8d7e06e281e669` | L `Mu_CombinedHudMatchupReady.fbmod`<br>R `r5/Mu_CombinedHudMatchupReady.fbmod`<br>W `MuV41Build/Mu_CombinedHudMatchupReady.fbmod` |
| Mu Combined HUD Team IDs | 0.23-diagnostic | `4ba5af8ffe73503a` | L `Mu_CombinedHudTeamId.fbmod`<br>R `r5/Mu_CombinedHudTeamId.fbmod`<br>W `MuV41Build/Mu_CombinedHudTeamId.fbmod` |
| Mu Combined HUD Watermark | 0.31 | `6adb6a5c7dec54b7` | L `Mu_CombinedHudWatermark.fbmod`<br>R `r5/Mu_CombinedHudWatermark.fbmod`<br>W `MuWatermarkBuild/Mu_CombinedHudWatermark.fbmod` |
| Mu Dynamic CBS FOX Original Fonts Research | 0.2 | `bcffb2dd37e9d8f1` | L `Mu_DynamicCbsFoxOriginalFontsResearch.fbmod`<br>R `r5/Mu_DynamicCbsFoxOriginalFontsResearch.fbmod` |
| Mu Dynamic CBS FOX Scorebugs Research | 0.1 | `f31a46b02bb6afeb` | L `Mu_DynamicCbsFoxScorebugsResearch.fbmod`<br>R `r5/Mu_DynamicCbsFoxScorebugsResearch.fbmod` |
| Mu Dynamic CBS FOX Selective HUD | 0.19-research | `8f6524126064c91a` | L `Mu_DynamicCbsFoxHudSelective.fbmod`<br>R `r5/Mu_DynamicCbsFoxHudSelective.fbmod` |
| Mu Dynamic CBS FOX Texture Hashes Research | 0.9-research | `1d0020e8ae12272e` | R `r5/Mu_DynamicCbsFoxTextureHashesResearch.fbmod` |
| Mu Explicit Bumper Probe | 0.1 | `11e071a5439f40f8` | L `Mu_ExplicitBumperProbe.fbmod`<br>R `r5/holiday_routing/Mu_ExplicitBumperProbe.fbmod`<br>W `MuBumperProbe/Mu_ExplicitBumperProbe.fbmod` |
| Mu Force CBS Control | 0.20-diagnostic | `02fa6c6490c3e0a6` | L `Mu_ForceCbsControl.fbmod`<br>R `r5/Mu_ForceCbsControl.fbmod` |
| Mu Force CBS Through Selector | 0.22-diagnostic | `6532cd1d75d9bed9` | L `Mu_ForceCbsThroughSelector.fbmod`<br>R `r5/Mu_ForceCbsThroughSelector.fbmod` |
| Mu Halftime Away Team Probe | 0.39 | `83d0ecb2b79fb97a` | L `Mu_Halftime_Away_Team_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/HalftimeAwayTeamProbe/Mu_Halftime_Away_Team_Probe.fbmod` |
| Mu Halftime CBS Only Control | 0.40 | `f992856cb00a4374` | L `Mu_Halftime_CBS_Only_Control.fbmod`<br>W `MuHolidayTrace/EventHub/HalftimeCbsOnlyControl/Mu_Halftime_CBS_Only_Control.fbmod` |
| Mu Halftime One Shot Probe | 0.41 | `ae58465b5f9dc15d` | L `Mu_Halftime_One_Shot_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/HalftimeOneShotProbe/Mu_Halftime_One_Shot_Probe.fbmod` |
| Mu Holiday Banner Routing Probe | 0.27 | `1736806c31fc5834` | L `Mu_Holiday_Banner_Routing_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_Holiday_Banner_Routing_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_Holiday_Banner_Routing_Probe.fbmod` |
| Mu Holiday Branch Marker | 0.4 | `73d0beb8789857df` | L `Mu_HolidayBranchMarker.fbmod`<br>R `r5/holiday_routing/Mu_HolidayBranchMarker.fbmod`<br>W `MuHolidayBranchMarker/Mu_HolidayBranchMarker.fbmod` |
| Mu Holiday Broadcast Openings | 0.1 | `16a394b0470af16d` | L `Mu_HolidayBroadcastOpenings.fbmod`<br>R `r5/holiday_routing/Mu_HolidayBroadcastOpenings.fbmod`<br>W `MuHolidayBumperRouting/Mu_HolidayBroadcastOpenings.fbmod` |
| Mu Holiday Forced NBC Probe | 0.2 | `46809dc392e4cd2d` | L `Mu_HolidayForcedNbcProbe.fbmod`<br>R `r5/holiday_routing/Mu_HolidayForcedNbcProbe.fbmod`<br>W `MuHolidayOpeningProbe/Mu_HolidayForcedNbcProbe.fbmod` |
| Mu Holiday Matchup Marker | 0.3 | `7b40600eb470324e` | L `Mu_HolidayMatchupMarker.fbmod`<br>R `r5/holiday_routing/Mu_HolidayMatchupMarker.fbmod`<br>W `MuMatchupMarker/Mu_HolidayMatchupMarker.fbmod` |
| Mu Holiday Opening Probe | 0.1 | `2cd2dfbe8f4d6484` | L `Mu_HolidayOpeningProbe.fbmod`<br>R `r5/holiday_routing/Mu_HolidayOpeningProbe.fbmod`<br>W `MuHolidayOpeningProbe/Mu_HolidayOpeningProbe.fbmod` |
| Mu Holiday Replay Routing Probe | 0.28 | `c589ecf14e3bff31` | L `Mu_Holiday_Replay_Routing_Probe.fbmod`<br>R `r5/holiday_routing/night_media_trace/Mu_Holiday_Replay_Routing_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_Holiday_Replay_Routing_Probe.fbmod` |
| Mu Holiday Routing Diagnostic | 0.1 | `40fa146018c74676` | L `Mu_HolidayRoutingDiagnostic.fbmod`<br>R `r5/holiday_routing/Mu_HolidayRoutingDiagnostic.fbmod`<br>W `MuHolidayDiagnostic/Mu_HolidayRoutingDiagnostic.fbmod` |
| Mu HUD Broadcast Stock Probe | 0.4 | `d4ec7a4804894039` | L `Mu_HUD_Broadcast_Stock_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Broadcast_Stock_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Broadcast_Stock_Probe.fbmod` |
| Mu HUD Channel Bundle Probe | 0.2 | `8d33df6fccf8d223` | L `Mu_HUD_Channel_Bundle_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Channel_Bundle_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Channel_Bundle_Probe.fbmod` |
| Mu HUD Channel Prefab Writer Probe | 0.5 | `536e5cc686d52edc` | L `Mu_HUD_Channel_Prefab_Writer_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Channel_Prefab_Writer_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Channel_Prefab_Writer_Probe.fbmod` |
| Mu HUD Channel Startup Probe | 0.1 | `17918a8310e1a8e6` | L `Mu_HUD_Channel_Startup_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Channel_Startup_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Channel_Startup_Probe.fbmod` |
| Mu HUD Channel Writer Probe | 0.3 | `5e2beab7dbd196a9` | L `Mu_HUD_Channel_Writer_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Channel_Writer_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Channel_Writer_Probe.fbmod` |
| Mu HUD Counter Control | 0.1 | `72db2729f9aaaaf4` | L `Mu_HUD_Counter_Control.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Counter_Control.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Counter_Control.fbmod` |
| Mu HUD Holiday All Nights Probe | 0.25 | `7f68ed9e78dbfe1a` | L `Mu_HUD_Holiday_All_Nights_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Holiday_All_Nights_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Holiday_All_Nights_Probe.fbmod` |
| Mu HUD Holiday Broadcast Route Probe | 0.19 | `561c62f89621e339` | L `Mu_HUD_Holiday_Broadcast_Route_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Holiday_Broadcast_Route_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Holiday_Broadcast_Route_Probe.fbmod` |
| Mu HUD Holiday Bumper Sender Probe | 0.22 | `ecb61ba7239b4df0` | L `Mu_HUD_Holiday_Bumper_Sender_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Holiday_Bumper_Sender_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Holiday_Bumper_Sender_Probe.fbmod` |
| Mu HUD Holiday Local Theme Probe | 0.26 | `f2b681aa9efc54c6` | L `Mu_HUD_Holiday_Local_Theme_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Holiday_Local_Theme_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Holiday_Local_Theme_Probe.fbmod` |
| Mu HUD Holiday Single Writer Off | 0.12 | `a9ae1314ea5976b8` | L `Mu_HUD_Holiday_Single_Writer_Off.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Holiday_Single_Writer_Off.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Holiday_Single_Writer_Off.fbmod` |
| Mu HUD Holiday Single Writer On | 0.12 | `55128fc44a0ae0ac` | L `Mu_HUD_Holiday_Single_Writer_On.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Holiday_Single_Writer_On.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Holiday_Single_Writer_On.fbmod` |
| Mu HUD Holiday Timer Control | 0.13 | `b0fde929bf4ca140` | L `Mu_HUD_Holiday_Timer_Control.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Holiday_Timer_Control.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Holiday_Timer_Control.fbmod` |
| Mu HUD Holiday TNF Event-Off Probe | 0.11 | `edd664260a42b035` | L `Mu_HUD_Holiday_TNF_Event_Off_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Holiday_TNF_Event_Off_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Holiday_TNF_Event_Off_Probe.fbmod` |
| Mu HUD Holiday TNF Marker Probe | 0.8 | `4fc97e55e20b7ae3` | L `Mu_HUD_Holiday_TNF_Marker_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Holiday_TNF_Marker_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Holiday_TNF_Marker_Probe.fbmod` |
| Mu HUD Holiday TNF Marker Probe | 0.9 | `9c22550555275974` | L `Mu_HUD_Holiday_TNF_Marker_Probe_v0.9.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Holiday_TNF_Marker_Probe_v0.9.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Holiday_TNF_Marker_Probe_v0.9.fbmod` |
| Mu HUD Holiday TNF Routing Probe | 0.23 | `e5b72eef14e7125a` | L `Mu_HUD_Holiday_TNF_Routing_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Holiday_TNF_Routing_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Holiday_TNF_Routing_Probe.fbmod` |
| Mu HUD Holiday TNF Sender-Off Probe | 0.10 | `6bf86f4e082ddd3e` | L `Mu_HUD_Holiday_TNF_Sender_Off_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Holiday_TNF_Sender_Off_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Holiday_TNF_Sender_Off_Probe.fbmod` |
| Mu HUD Holiday TNF Watermark Probe | 0.24 | `7a3a7a8ee92b6e14` | L `Mu_HUD_Holiday_TNF_Watermark_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Holiday_TNF_Watermark_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Holiday_TNF_Watermark_Probe.fbmod` |
| Mu HUD Lower Third Marker Probe | 0.17 | `6472922aa61a2ddb` | L `Mu_HUD_Lower_Third_Marker_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Lower_Third_Marker_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Lower_Third_Marker_Probe.fbmod` |
| Mu HUD Lower Third Sender Probe | 0.18 | `9c052dc280453f7f` | L `Mu_HUD_Lower_Third_Sender_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Lower_Third_Sender_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Lower_Third_Sender_Probe.fbmod` |
| Mu HUD Match Channel Probe | 0.1 | `afe810c44d1baabe` | L `Mu_HUD_Match_Channel_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Match_Channel_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Match_Channel_Probe.fbmod` |
| Mu HUD Native Event Control | 0.14 | `bbbe00a07d38ba78` | L `Mu_HUD_Native_Event_Control.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Native_Event_Control.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Native_Event_Control.fbmod` |
| Mu HUD Native Halloween Marker Probe | 0.20 | `f6363c586318b916` | L `Mu_HUD_Native_Halloween_Marker_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Native_Halloween_Marker_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Native_Halloween_Marker_Probe.fbmod` |
| Mu HUD Pregame Event Probe | 0.1 | `692088d80fa2f80f` | L `Mu_HUD_Pregame_Event_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Pregame_Event_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Pregame_Event_Probe.fbmod` |
| Mu HUD Pregame Update Probe | 0.7 | `6a00cb52a0a3eb44` | L `Mu_HUD_Pregame_Update_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Pregame_Update_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Pregame_Update_Probe.fbmod` |
| Mu HUD Self-Contained Writer Probe | 0.6 | `7f84c5d1a0e0094d` | L `Mu_HUD_Self_Contained_Writer_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Self_Contained_Writer_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Self_Contained_Writer_Probe.fbmod` |
| Mu HUD Sizzle Branch Marker Probe | 0.16 | `f418b3ba30e34f2b` | L `Mu_HUD_Sizzle_Branch_Marker_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Sizzle_Branch_Marker_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Sizzle_Branch_Marker_Probe.fbmod` |
| Mu HUD Sizzle Sender Control | 0.15 | `6f7ab8909e37986a` | L `Mu_HUD_Sizzle_Sender_Control.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Sizzle_Sender_Control.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Sizzle_Sender_Control.fbmod` |
| Mu HUD Team ID Marker Probe | 0.21 | `a2e2eeb3d80f3aa2` | L `Mu_HUD_Team_ID_Marker_Probe.fbmod`<br>R `r5/holiday_routing/hud_event_trace/Mu_HUD_Team_ID_Marker_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_HUD_Team_ID_Marker_Probe.fbmod` |
| Mu MNF Branding Research | 0.1 | `b7321191c16bf4b6` | L `Mu_MnfBrandingResearch.fbmod`<br>R `r5/primetime_pregame/Mu_MnfBrandingResearch.fbmod`<br>W `MuMnfBrandingBuild/Mu_MnfBrandingResearch.fbmod` |
| Mu Pregame Away ID Probe | 0.30 | `2575d1a1faf2992f` | L `Mu_Pregame_Away_ID_Probe.fbmod`<br>R `r5/afternoon_media_trace/Mu_Pregame_Away_ID_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_Pregame_Away_ID_Probe.fbmod` |
| Mu Pregame Movie Timing Probe | 0.31 | `ec4a1babca749570` | L `Mu_Pregame_Movie_Timing_Probe.fbmod`<br>R `r5/afternoon_media_trace/Mu_Pregame_Movie_Timing_Probe.fbmod`<br>W `MuHolidayTrace/EventHub/Mu_Pregame_Movie_Timing_Probe.fbmod` |
| Mu Primetime Branding Quarter Research | 0.3 | `35c74d0aca0fa08f` | L `Mu_PrimetimeBrandingQuarterResearch.fbmod`<br>R `r5/primetime_pregame/Mu_PrimetimeBrandingQuarterResearch.fbmod`<br>W `MuQuarterBranding/Mu_PrimetimeBrandingQuarterResearch.fbmod` |
| Mu Primetime Branding Research | 0.2 | `c744e8b6870a921a` | L `Mu_PrimetimeBrandingResearch.fbmod`<br>R `r5/primetime_pregame/Mu_PrimetimeBrandingResearch.fbmod`<br>W `MuCombinedBrandingBuild/Mu_PrimetimeBrandingResearch.fbmod` |
| Mu Primetime Graphics Branded Research | 0.3 | `42c441d94fc32d3d` | L `Mu_PrimetimeGraphicsBrandedResearch.fbmod`<br>R `r5/primetime_pregame/Mu_PrimetimeGraphicsBrandedResearch.fbmod`<br>W `MuBrandedNightLogoBuild/Mu_PrimetimeGraphicsBrandedResearch.fbmod` |
| Mu Primetime Graphics Logos Research | 0.3 | `2e88b107ed369430` | W `MuNightLogoBuild/Mu_PrimetimeGraphicsLogosResearch.fbmod` |
| Mu Primetime Graphics Research | 0.2 | `83c7265ea0d29a03` | L `Mu_PrimetimeGraphicsResearch.fbmod`<br>R `r5/primetime_pregame/Mu_PrimetimeGraphicsResearch.fbmod`<br>W `MuNightMediaBuild/Mu_PrimetimeGraphicsResearch.fbmod` |
| Mu Primetime Openings Research | 0.1 | `04434cc138225ff4` | L `Mu_PrimetimeOpeningsResearch.fbmod`<br>R `r5/primetime_pregame/Mu_PrimetimeOpeningsResearch.fbmod`<br>W `MuOpeningBuild/Recovered/Mu_PrimetimeOpeningsResearch.fbmod`<br>W `MuV41Metadata/PrimetimePregame/Mu_PrimetimeOpeningsResearch.fbmod` |
| Mu Reconstructed FOX Scorebug Research | 0.1 | `04fc1ff82729c159` | L `Mu_ReconstructedFoxScorebugResearch.fbmod`<br>R `r4/fox_reconstruction/Mu_ReconstructedFoxScorebugResearch.fbmod` |
| Mu Reconstructed FOX v41 Control | 0.1 | `977e7295126b69ed` | L `Mu_ReconstructedFoxV41Control.fbmod`<br>R `r5/Mu_ReconstructedFoxV41Control.fbmod` |
| Mu Recovered FOX HUD Research | 0.1 | `77e8ef32fd73a96a` | R `r4/applied_assets/hud_reimport/Mu_RecoveredFoxHudResearch.fbmod` |
| Mu Recovered FOX Watermark Research | 0.1 | `48242accf4fadeeb` | R `r4/applied_assets/texture_reimport/Mu_RecoveredFoxWatermarkResearch.fbmod` |
| Mu Research Away Conference Probe | 0.5 | `eef94e79f3bc3911` | R `r3/away_spike/Mu_AwayConferenceProbe.fbmod`<br>R `r3/away_spike/v051/installed_before_v051.fbmod` |
| Mu Research Away Conference Probe | 0.5.1 | `2f752c79a36cf328` | L `Mu_AwayConferenceProbe.fbmod`<br>R `r3/away_spike/v051/Mu_AwayConferenceProbe.fbmod` |
| Mu Research Clone Spike | 0.3 | `bd1a347f6f58451d` | R `r2/clone_spike/Mu_CloneSpike.fbmod` |
| Mu Research HUD Clone Probe | 0.3 | `851f4f2dc2b4ce91` | L `Mu_DynamicPresentationCloneProbe.fbmod`<br>R `r2/clone_spike/Mu_HudCloneProbe.fbmod` |
| Mu Research HUD Team ID Probe | 0.4 | `80587219b9333611` | L `Mu_DynamicPresentationTeamIdProbe.fbmod` |
| Mu Research Native Theme Probe | 0.1 | `07284438a0276854` | L `Mu_DynamicPresentationNativeProbe.fbmod` |
| Mu Research Theme HUD Probe | 0.2 | `d20e2d47ae57e390` | L `Mu_DynamicPresentationHudProbe.fbmod` |
| Mu Single CBS Corner RFC Chunk | 0.14-diagnostic | `7c76d5f2e82159e8` | R `r5/Mu_SingleCbsCornerRfc.fbmod` |
| Mu Single CBS MNF RFC Chunk | 0.17-diagnostic | `43e18efa3982e83f` | L `Mu_SingleCbsMnfRfc.fbmod`<br>R `r5/Mu_SingleCbsMnfRfc.fbmod` |
| Mu Single CBS MNF Short Identity | 0.18-diagnostic | `5f95e74992801731` | L `Mu_SingleCbsMnfShort.fbmod`<br>R `r5/Mu_SingleCbsMnfShort.fbmod` |
| Mu Single CBS Texture cloned | 0.10-diagnostic | `e676e04c9b36867e` | L `Mu_SingleCbsTexture_cloned.fbmod`<br>R `r5/Mu_SingleCbsTexture_cloned.fbmod` |
| Mu Single CBS Texture original | 0.10-diagnostic | `0528b988a51fadfb` | L `Mu_SingleCbsTexture_original.fbmod`<br>R `r5/Mu_SingleCbsTexture_original.fbmod` |
| Mu Single CBS Texture short | 0.10-diagnostic | `743b52ea68309a94` | R `r5/Mu_SingleCbsTexture_short.fbmod` |
| Mu Studio FMV Probe | 0.1 | `734875ae22cee29e` | L `Mu_StudioFmvProbe.fbmod`<br>R `r5/holiday_routing/Mu_StudioFmvProbe.fbmod`<br>W `MuStudioFmvProbe/Mu_StudioFmvProbe.fbmod` |

Keep the original CBS/FOX v3 and v4.1 scorebugs, the five original presentation mods, CBS/FOX team-logo and music source mods, and unrelated Mu jersey and first-down mods. The library files `Mu_Source_CBS_Scorebugs_v41.fbmod` and `Mu_Source_FOX_Scorebugs_v41.fbmod` are research copies of original author mods. Keep them as source inputs or confirm an independent copy before retiring the aliases.

### Mod Manager research packs

46 research pack entries in the live `manager_config.json` and 40 matching applied cache directories under `C:/Program Files/EA Games/Madden NFL 27/ModData/`. The table records enabled mods only. A disabled reference can still appear in a pack.

| Pack | Enabled at inventory | Applied directory present |
|---|---|---|
| `HudFranchiseNative` | `Mu_PrimetimeGraphicsBrandedResearch.fbmod`, `Mu_PrimetimeBrandingQuarterResearch.fbmod`, `Mu_Halftime_One_Shot_Probe.fbmod` | Yes |
| `Mu CBS Presentation Source` | `m27_CBS_presentation_mod_2.3.1.fbmod` | Yes |
| `Mu Fox Presentation Source` | `m27_FOX_Presentation_mod_2.3.fbmod` | Yes |
| `Mu Research Away Conference Probe` | `m27_SNF_presentation_mod_2.3.fbmod`, `M27_NFLScorebugs_FOX_v3.fbmod` | — |
| `Mu Research Away Datasource` | `Mu_CombinedHudAwayDatasource.fbmod` | Yes |
| `Mu Research Case Readout` | `Mu_CombinedHudCaseReadout.fbmod` | Yes |
| `Mu Research Case Updates` | `Mu_CombinedHudCaseUpdates.fbmod` | Yes |
| `Mu Research CBS Fonts Only` | `Mu_CbsFontsOnlyDiagnostic.fbmod` | Yes |
| `Mu Research CBS FOX Assets Only` | `Mu_CbsFoxAssetsOnlyDiagnostic.fbmod` | Yes |
| `Mu Research CBS FOX HUD Only` | `Mu_DynamicCbsFoxHudOnlyDiagnostic.fbmod` | Yes |
| `Mu Research CBS FOX No Font Registration` | `Mu_DynamicCbsFoxNoFontRegistrationDiagnostic.fbmod` | Yes |
| `Mu Research CBS MNF RFC Chunk` | `Mu_SingleCbsMnfRfc.fbmod` | Yes |
| `Mu Research CBS MNF Short Identity` | `Mu_SingleCbsMnfShort.fbmod` | Yes |
| `Mu Research CBS Single Diamond` | `Mu_CbsTextureSubset_5_1.fbmod` | Yes |
| `Mu Research CBS Single Flag Glow` | `Mu_CbsTextureSubset_4_1.fbmod` | Yes |
| `Mu Research CBS Single Kickoff Eye` | `Mu_CbsTextureSubset_3_1.fbmod` | Yes |
| `Mu Research CBS Single MNF Corner` | `Mu_CbsTextureSubset_1_1.fbmod` | Yes |
| `Mu Research CBS Single TNF Corner` | `Mu_CbsTextureSubset_0_1.fbmod` | Yes |
| `Mu Research CBS Single Wildcard Corner` | `Mu_CbsTextureSubset_6_1.fbmod` | Yes |
| `Mu Research CBS Texture Eye Glow Diamond` | `Mu_CbsTextureSubset_3_3.fbmod` | Yes |
| `Mu Research CBS Texture Half A` | `Mu_CbsTextureSubset_0_14.fbmod` | Yes |
| `Mu Research CBS Texture Half B` | `Mu_CbsTextureSubset_14_14.fbmod` | Yes |
| `Mu Research CBS Texture Hash Fixed` | `Mu_CbsTextureNameHashFixedDiagnostic.fbmod` | Yes |
| `Mu Research CBS Texture Quarter A` | `Mu_CbsTextureSubset_0_7.fbmod` | Yes |
| `Mu Research CBS Textures Only` | `Mu_CbsTexturesOnlyDiagnostic.fbmod` | Yes |
| `Mu Research CBS Through Selector` | `Mu_ForceCbsThroughSelector.fbmod` | Yes |
| `Mu Research CBS v41 Source` | `Mu_Source_CBS_Scorebugs_v41.fbmod` | Yes |
| `Mu Research Combined HUD Team IDs` | `Mu_CombinedHudTeamId.fbmod` | Yes |
| `Mu Research Dynamic CBS FOX Selective HUD` | `Mu_DynamicCbsFoxHudSelective.fbmod` | Yes |
| `Mu Research Dynamic CBS FOX v02` | `Mu_DynamicCbsFoxOriginalFontsResearch.fbmod` | Yes |
| `Mu Research Dynamic CBS FOX v41` | `Mu_DynamicCbsFoxScorebugsResearch.fbmod` | Yes |
| `Mu Research Force CBS Control` | `Mu_ForceCbsControl.fbmod` | Yes |
| `Mu Research FOX Original Baseline` | `M27_NFLScorebugs_FOX_v3.fbmod` | Yes |
| `Mu Research FOX Reconstructed` | `Mu_ReconstructedFoxScorebugResearch.fbmod` | Yes |
| `Mu Research FOX v41 Rebuilt Control` | `Mu_ReconstructedFoxV41Control.fbmod` | Yes |
| `Mu Research FOX v41 Source` | `Mu_Source_FOX_Scorebugs_v41.fbmod` | Yes |
| `Mu Research HUD Clone Probe` | `Mu_DynamicPresentationCloneProbe.fbmod` | — |
| `Mu Research HUD Team ID Probe` | `Mu_DynamicPresentationTeamIdProbe.fbmod` | — |
| `Mu Research Matchup Away` | `Mu_CombinedHudMatchupAway.fbmod` | Yes |
| `Mu Research Matchup Fixed ID` | `Mu_CombinedHudMatchupFixedId.fbmod` | Yes |
| `Mu Research Matchup Ready` | `Mu_CombinedHudMatchupReady.fbmod` | Yes |
| `Mu Research Native Baseline` | None | — |
| `Mu Research Native Theme Probe` | `Mu_DynamicPresentationNativeProbe.fbmod` | — |
| `Mu Research Single CBS Cloned` | `Mu_SingleCbsTexture_cloned.fbmod` | Yes |
| `Mu Research Single CBS Original` | `Mu_SingleCbsTexture_original.fbmod` | Yes |
| `Mu Research Theme HUD Probe` | `Mu_DynamicPresentationHudProbe.fbmod` | — |

Keep `Default`, `Mu Base` and `Mu Test` and their shared source mods. `Default` was selected in the saved configuration at inventory time. Preserve the three `HudFranchiseNative` mods and pack through the remaining v0.41 investigation and the consolidated release test.

When the work is complete, review the exact pack names first, then check remaining pack references before retiring library copies or matching applied caches. Keep the receipts, source captures and editable projects needed to reproduce the final release.
