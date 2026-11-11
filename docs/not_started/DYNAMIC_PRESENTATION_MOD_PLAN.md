# Dynamic Presentation Mod — Exploration Plan

**Status:** proposed research; no implementation or feasibility claim.
**Date:** 2026-11-01.
**Objective:** determine whether one permanently installed presentation `.fbmod` can select each game's broadcast and atmosphere from Mu's decisions without changing packs or restarting Madden. Determine separately whether this can work without reloading the franchise.

## 1. Product intent and boundaries

Mu retains the decision logic, database, franchise engines and newsroom. Madden renders the chosen presentation. This project investigates the presentation boundary only; it does not port progression, contracts or transactions into the game or establish that loaded save files can be edited safely.

Success should let JR play successive matchups with different presentation configurations during one Madden process. Returning to the franchise hub between games is acceptable. Changing already loaded game assets mid-match is unnecessary. Choices freeze before the matchup loads and remain fixed until it ends.

There are three distinct outcomes to measure:

1. **No pack change:** one installed mod contains all required variants.
2. **No Madden restart:** a later matchup selects different variants in the same process.
3. **No franchise reload for presentation:** Mu can supply a new choice while the franchise remains loaded.

A save-field selector could achieve the first two while still requiring a franchise reload. Native schedule-based selection may achieve all three for some components, but is not arbitrary control by Mu. Presentation success does not remove the existing reload boundary for Mu's other save edits.

**Confirmed weekly boundary split (2026-11-02 live save-edit check):** Writing an edited save via the weekly-flow pipeline (`progression_save.rating_edits` → `save_writer.mjs` → `league_office.replace_save`) while `CAREER-WEEK1-AUTOSAVE` was loaded in Madden succeeded with no OS file lock, no crash and no save corruption (`pc_save_reader.mjs` parsed all 3,244 players and 320 games cleanly afterward). However, Madden keeps an open franchise strictly in RAM, never re-reads the save file while loaded, and overwrites `<save>-AUTOSAVE` from RAM on exit to the Franchise List — silently clobbering external edits made while loaded. Between weeks, JR must therefore always **back out to the Franchise List** (~30 s, keeping `Madden27.exe` open) so `league_office.py` can write weekly progression, depth charts, contracts and transactions before reloading the tile. Whether JR also has to **close and restart Madden** (~2 min via `MMCModManager.exe`) depends solely on whether one static mod setup can dynamically cover the per-game groups currently toggled by `pipeline/mod_packs.py` (**broadcast**, **ball/BCA** and **crowd density**) without changing `ModData`.

This plan authorizes a research sequence when taken up, not changes to Mu Base, production launch behavior, the active franchise or archive databases. Use disposable franchises and an isolated test pack for experiments. Do not push changes as part of research. Preserve unrelated working-tree edits.

## 2. Evidence already available

Treat these as a starting inventory; record the exact game build and tools again when experiments begin.

| Evidence | Finding | Limit |
|---|---|---|
| `pipeline/mod_packs.py` | `choose(GameInfo)` produces broadcast window, crowd tier and BCA choice. `build_pack` converts these into enable/disable states. | Decision code; no runtime selector. |
| `pipeline/game_launch.py` | Launch currently refuses while Madden is running and launches a selected MMC pack. | Existing operational path; no hot switching. |
| Read-only inspection of our presentation `.fbmod` resource lists | CBS and FOX presentation mods share 148 resource names in the inspected copies, including generic broadcast logos. Other packages also share resources. | Counts include resource records, not necessarily 148 distinct visual elements. Shared names alone do not establish differing contents. |
| `docs/reference/MOD_REVERSE_ENGINEERING.md` | Mods replace EBX, textures, audio, legacy files and other assets; installed payloads are encrypted. | Metadata exposes affected assets, not all replacement values or selector logic. |
| Printable strings in local `madden27_typeinfo.cache` | Candidate names include `BroadcastPresentationIdAsset`, `BroadcastGameContext`, `BroadcastGameContextData`, `BroadcastNetwork_DBE`, `BroadcastPresentationDataProvider`, `FootballBroadcastSetupPresentationMessage` and `FootballBroadcastStartPresentationMessage`. | Names are leads only. They do not prove writable fields, call sites, an external configuration API or script access. |
| Existing mod builder | `tools/mod_builder/` and `tools/mmc_harness/` support asset search, EBX dump/set, texture export/import, project/mod export and isolated installation. | Asset duplication, graph rewiring and runtime access must be assessed separately. Current environment/key readiness is unverified for this task. |
| Loaded-save edit check (2026-11-02) | Atomic replacement of loaded `CAREER-WEEK1-AUTOSAVE` (`pgid 22686` `ThrowPowerRating` 87 → 99 + OVR dual-write) succeeded with no Windows lock, crash or corruption, but was ignored in RAM and overwritten on exit to the Franchise List. | Live save edits cannot carry changes into an open franchise; any save-backed input or weekly League Office write requires backing out to the Franchise List first (no game restart needed). |

Primary external reference: [EA's M27 presentation deep dive](https://careers.ea.com/games/madden-nfl/news/madden-27-sights-and-sounds), especially seasonal packages and franchise virtual-calendar selection. This supports the existence of native conditional presentation; it does not document a Mu-controlled mod interface. Check the article and current build during research rather than treating marketing behavior as a field specification.

Local reference material:
- `docs/done/MOD_BUILDER_PLAN.md`
- `docs/reference/MOD_REVERSE_ENGINEERING.md`
- `docs/MOD_INSTALL.md`
- `tools/mod_builder/harness.py`, `asset_index.py`, `recipe.py`, `install.py`
- `tools/fbmod_inspect.py`, `tools/fbmod_conflicts.py`

## 3. Hypotheses to test

**H1 — Native broadcast routing.** Existing per-matchup presentation context selects distinct asset graphs. We can map contexts to separate graphics/music variants in one mod.

**H2 — Mu control through existing state.** An existing legitimate game/franchise setting can carry Mu's selection without falsifying kickoff date, teams, commentary assignment or other football state. Reload requirements depend on when the field is read.

**H3 — Mu control through a script.** An executed game script has both a pre-matchup hook and access to a supported input channel plus presentation selection. An editor Lua plugin or presence of Lua assets alone is not evidence for this.

**H4 — Mu control through a runtime bridge.** If native/script routes are insufficient, a small runtime component can read a local manifest and invoke or redirect the native selector before assets resolve. This would be a hybrid deployment, not a standalone `.fbmod`.

**H5 — Independent atmosphere routing.** Crowd and ball assets can be chosen per matchup independently of the broadcast package, without duplicating full stadiums or all combinations of the configuration.

Investigate H1 first. H2 and H3 are alternatives for the control channel. Do not commit to a bridge before identifying the selector and its lifecycle. Failure of one component does not establish failure of every other component.

## 4. Proposed architecture and input contract

```text
Mu's existing presentation rules
             |
             v
Versioned per-game configuration
             |
             v
Verified control channel: native state OR game script OR runtime bridge
             |
             v
Pre-matchup selection and asset references
             |
             v
One static presentation asset package
```

Mu computes the choice; Madden receives resolved options. Avoid reimplementing crowd/popularity rules inside the game. Keep the asset package unchanged between games.

### 4.1 Candidate configuration

This JSON is an interface proposal, not a file Madden currently knows how to read:

```json
{
  "schema_version": 1,
  "revision": 12,
  "franchise_id": "mu-test-2034",
  "game": {
    "mu_game_id": 123,
    "season": 2034,
    "week": 7,
    "home": "CAR",
    "away": "ATL"
  },
  "assets_version": "prototype-1",
  "presentation": {
    "broadcast": "FOX",
    "crowd": "TIER2",
    "ball": "MUDDED",
    "accessories": "STANDARD"
  }
}
```

Do not assume a Mu database game ID exists inside Madden. Establish a mapping to an actual game reference, or a validated composite identity including stage and schedule semantics. Neutral-site games and playoffs must not collide with regular-season identities. A new franchise must not reuse an old manifest accidentally.

Initially keep a single selected-game manifest rather than a whole-week table. Add multi-game support only if needed. Use a Windows-local directory readable by the game-side consumer; Mu in WSL can write through `/mnt/c/...`. The final path depends on actual consumer permissions and must not assume arbitrary script filesystem access.

For a file-reading bridge, Mu writes a complete temporary document then replaces it; the consumer parses and validates a complete revision. It must verify schema, asset version and matchup identity, then latch the selection before asset resolution. Invalid, absent or mismatched input uses a known default and records why. It must never apply a stale profile to another game or mutate assets mid-match.

Where possible emit an acknowledgement with game identity, revision, resolved values and fallback reasons. Keep requested, resolved and visually observed results separate: a log saying FOX was requested is not proof that FOX rendered.

### 4.2 Asset organization

Build separately addressable variants, conceptually `mu/presentation/cbs/...` and `mu/presentation/fox/...`; exact asset paths depend on supported duplication and bundle rules. Unique path names alone may not be sufficient: asset GUIDs, texture chunks, internal references, bundle membership and dependencies may also need distinct identities.

Map dependencies for scorebug, logos, wipes, intros and fonts. Audio is outside this presentation-mod effort, as confirmed by JR; it is not a verification gate. Shared neutral assets may remain shared. Network-specific assets must not silently replace global resources used by another variant. Prefer independent selectors for broadcast, crowd and ball over one asset graph per full combination.

Use original test graphics for the prototype. Our encrypted installed mods provide resource inventories, not editable source projects. For later integration, obtain editable author projects/exports where available, or rebuild changes from supported vanilla exports. Record provenance and any distribution limitations; do not plan to decrypt protected payloads.

## 5. Research phases and gates

### Phase 0 — Reproducible baseline

**Work**
- Record game build, MMC version, cache fingerprint, current Git revision and relevant local edits.
- Confirm the harness and asset index are usable. Read the existing harness commands before invoking exports or edits; never print key contents.
- Inventory exact mod filenames, versions, resource hashes and relevant overlaps.
- Create an isolated evidence directory and disposable franchise/pack. Keep the normal launch path available as a fallback.
- Record baseline native afternoon and primetime presentation using controlled games. Include a repeated venue where practical.

**Deliverables:** environment inventory, baseline captures and asset-overlap report.

**Gate G0:** baseline games and the build/inspection route work. If editor access or game exports are unavailable, record the specific missing capability; metadata-only research may continue, but mark graph conclusions unverified.

### Phase 1 — Find the selector and loading boundary

**Work**
- Search asset names/types for the candidate broadcast types and related presentation/package/context assets. Use the existing index rather than scanning every asset manually.
- Dump promising EBX objects and inspect references in the harness or MMC Editor. Trace actual reference chains from selector/context to a visible graphic.
- Identify enumerated native contexts, where context comes from, and whether it is a native enum with fixed engine behavior or an extensible data-driven list.
- Inspect available save schema/game settings for legitimate presentation fields. Record actual table, field, type and sampled values; do not infer availability from the type cache alone.
- Establish when values and dependencies are read: process startup, franchise load, matchup setup, stadium load or UI creation.
- Determine whether scripts participate in that path. Record an observed call or reference, not only script filenames.

**Deliverable:** selector/reference diagram with asset paths, types, field names, callers where known, load timing and evidence confidence.

**Gate G1:** identify a testable route from a per-matchup context to at least one visible presentation asset. If not found, stop after the scoped investigation and report the missing link rather than building a full package speculatively.

### Phase 2 — Two variants in one static mod

**Work**
- Create two obvious original test graphics, such as differently colored labeled broadcast watermarks.
- Duplicate the necessary asset and dependencies through the supported editor route. Preserve distinct identity and bundle reachability.
- Route two existing native contexts to different variants in one exported `.fbmod`.
- Verify the exported resource list and use only the dedicated test pack. Check that other presentation mods cannot mask the test.
- Load context A, return to the hub, then load context B and A again without restarting Madden, reapplying mods or editing the package.
- Repeat at the same stadium to expose cached resources. Repeat the sequence in reverse order.

**Deliverables:** build recipe/project, resource verification, A/B/A captures and process/load-boundary log.

**Gate G2:** both variants render correctly in one process from the unchanged mod. Record whether a franchise reload was needed. Native automatic switching passes coexistence, not arbitrary Mu control.

### Phase 3 — Establish Mu's control channel

Investigate these routes in order, retaining evidence for rejected ones:

**A. Existing state/native rules**
- Identify a legitimate selector field or native mapping from actual matchup context.
- Test if Mu's CBS/FOX policy can be represented without changing football facts or disrupting commentary/holiday context.
- If save writes are required, use the established unloaded-franchise workflow (back out to the Franchise List, write, reload; no process restart needed). Label this route as requiring reload; an external write to a loaded file is ignored in RAM and clobbered by exit autosave (verified 2026-11-02).

**B. Game script**
- Verify that an editable script actually executes before relevant assets load.
- Verify presentation access and the available input API separately. Do not assume `io`, networking, arbitrary DLL loading or unrestricted Lua libraries exist.
- Test a minimal explicit switch before any full configuration parser.

**C. Runtime bridge, only if needed**
- Write a feasibility note identifying a real function/message/selector and a supported or researched way to reach it.
- Assess build-specific discovery, execution thread, initialization order, object lifetime, cache behavior and clean failure.
- Prefer selecting existing asset graphs or invoking native functions over rewriting loaded textures or general memory fields.
- Keep communications local and minimize the interface to presentation selection. A bridge is not a franchise save editor.
- Estimate maintenance and deployment burden before building this route; present it as a separate architecture decision if it exceeds a small presentation integration.

**Gate G3:** Mu can cause A/B/A selection for the intended game. Classify the result as native-only, reload-required state control, script-assisted or hybrid bridge. Report separately whether arbitrary CBS/FOX selection is possible for an otherwise identical afternoon matchup.

### Phase 4 — Coherent broadcast packages

Expand only after G2 and G3 have a viable result.

- Add CBS and FOX scorebugs, relevant fonts/logos, intro, replay/quarter wipes and music.
- Trace every remaining global reference and test for mixed branding.
- Then add SNF, MNF and TNF using actual native context mapping; do not assume five independently controllable slots exist.
- Preserve legitimate playoff, Super Bowl and holiday behavior unless a documented Mu choice replaces it.
- Check scoreboard fields, clock, possession, penalties, overtime and kick-meter compatibility, not only a still image of the opening screen.
- Verify visual transitions across intro, quarter breaks, replay and endgame. Audio is outside scope.

**Gate G4:** supported broadcasts render coherently across successive games with no installed-pack change. Publish a coverage matrix for components that remain global, fixed or unsupported.

### Phase 5 — Crowd, ball and accessories as independent spikes

**Crowd**
- Identify which stadium fields the current tiers change and whether attendance is a live parameter, preset reference or baked asset difference.
- Test two densities in successive games at the same venue, with identical broadcast choice. Verify several camera views and venue-specific sections.
- If full venue duplication is required, measure dependencies, memory and loading cost before proceeding. Do not assume atmosphere audio follows visual attendance.

**Ball**
- Find the reference selecting the ball/material; create two test variants with distinct identities.
- Test the change across games, replays and closeups. Check color, normal and material dependencies and whether the choice is globally cached.

**BCA accessories**
- Treat uniforms/accessories separately from the ball. Inspect jersey variants, towels, helmet decals and team-specific dependencies.
- Test home/away/alternate uniforms, including teams with incomplete coverage in the current BCA mod. Preserve player identity and gameplay state.

**Gate G5:** independent per-component results. A broadcast-only mod is a legitimate partial result; no requirement to force all dimensions into a fragile combined solution.

### Phase 6 — Operational decision and integration proposal

Compare:

| Outcome | Consequence |
|---|---|
| Native selection covers the requirements | Build one static mod and a small Mu exporter only if an external input channel is actually needed. |
| Native selection covers only schedule-based broadcasts | Keep those variants together; retain pack switching for unsupported choices. |
| Selection requires save changes | Eliminate mod restarts where proven, while retaining franchise reloads. |
| A runtime bridge is necessary and maintainable | Propose a separately versioned bridge plus one asset mod. |
| Critical assets are startup-global or hooks are too fragile | Keep current packs; document partial simplifications worth retaining. |

Only after this decision propose changes to `mod_packs.py`, `game_launch.py` and any user-facing launch flow. Preserve an explicit fallback to the current pack route. Do not mark production readiness from prototype success alone.

## 6. Verification matrix

Record game build, pack/mod hash, manifest revision, requested choice, resolved choice, observed result, process ID, restart/reload actions and evidence paths for every run.

| Case | Required observation |
|---|---|
| A/B/A, same process and venue | Correct variants every time; no stale cached branding. |
| Identical afternoon matchup, changed Mu choice | Establishes arbitrary Mu control if supported. |
| Successive different games | Previous game's choice cannot leak into the next. |
| Wrong franchise/game identity | Known fallback with a visible/logged reason. |
| Missing, malformed or partially written manifest | No crash; no half-applied configuration. |
| Unsupported schema or asset version | Explicit fallback; no guessed interpretation. |
| Configuration changed during a game | Current selection stays latched; later game reads the appropriate revision. |
| CBS/FOX then each primetime context | Graphics coherent; supported components identified. |
| Holiday, playoffs, Super Bowl, neutral site | Defined precedence and no accidental routing collisions. |
| Same venue, changed crowd; same broadcast, changed ball | Independent selection demonstrated. |
| Save/reload, return from game, replay/rematch | Chosen lifecycle behavior documented. |
| Mod disabled or bridge unavailable | Existing operational fallback remains usable. |

Measure startup/load time, memory and stability relative to baseline, especially if assets are duplicated. A single successful game is insufficient evidence for cache/lifecycle claims. If code is implemented, add meaningful tests for configuration validation, matchup identity, fallback and atomic publication; unit tests cannot prove Madden rendered the correct assets.

## 7. Evidence layout and research units

Proposed evidence root: `verify_output/dynamic_presentation/`, with build-specific subfolders containing:

- `environment.json`, `inventory.json`, `asset_overlaps.json`
- `selector_map.md` with exported-object evidence and unresolved links
- `experiments.csv` with the run fields from §6
- `captures/`, `exports/`, `logs/` and `builds/`
- `capability_matrix.md` and `decision.md`

These are proposed outputs, not files already created by this plan. Keep large binaries out of Git unless repository policy calls for them; retain reproducible recipes and hashes. Do not include keys or unrelated private data in exports or logs.

Sequential work units (no delegation required):

| Unit | Scope | Proposed owned files |
|---|---|---|
| R0 | Baseline and resource inventory | Evidence inventory and experiment log |
| R1 | Selector and lifecycle mapping | Selector map and relevant object exports |
| R2 | Two-context asset prototype | Isolated mod-build recipe/project and captures |
| R3 | Mu control-channel proof | Prototype input adapter/consumer if justified, contract tests and route decision |
| R4 | Coherent broadcast expansion | Broadcast assets, dependency map and captures |
| R5 | Independent atmosphere spikes | Crowd/ball/accessory recipes and capability results |
| R6 | Architecture recommendation | Final capability matrix, costs, limitations and integration proposal |

R0 → R1 → R2 → R3 is the critical path. R4 and R5 follow a viable selection/control result. The plan and `PROJECT_SPEC.md` stay centrally maintained; do not invent production modules before research identifies the necessary interface.

## 8. Stop conditions and completion criteria

Timebox initial baseline/selector research to one focused session and the minimal two-variant prototype to a second, adjusting only when a concrete new lead warrants it. These are investigation checkpoints, not promises of delivery in two sessions. At each gate, record what was proven, disproven and remains unknown.

Stop a route when it requires unavailable author source, an unobservable selector, unsupported runtime capabilities, or broad engine reverse engineering disproportionate to avoiding restarts. State the exact missing capability and preserve useful partial findings. Do not infer impossibility merely from a failed asset-name search.

Exploration is complete when it provides:

1. A per-component feasibility matrix with evidence and load boundaries.
2. A demonstrated minimal route, or a documented technical blocker after scoped experiments.
3. A clear distinction between one `.fbmod`, one `.fbmod` plus a bridge, and remaining pack-based features.
4. A recommendation covering restart/reload savings, maintenance, asset provenance and build compatibility.
5. A concrete next implementation scope, or a reason to retain the current design.

## 9. Open decisions to resolve through evidence

- Can Mu select CBS versus FOX independently of the native afternoon context?
- Are presentation IDs extensible, or tied to fixed engine enum cases?
- Which components are per-matchup and which are cached for the process?
- Can a valid input arrive while the franchise is loaded, or only through a save reload?
- Can broadcast, crowd and ball vary independently without multiplying asset graphs?
- Is a bridge sufficiently small and stable to justify its deployment cost?
- Which editable source assets are available for the final branded package?

No production routing, save ownership or active pack changes are implied by this exploration plan.