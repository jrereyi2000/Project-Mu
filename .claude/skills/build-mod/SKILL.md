---
name: build-mod
description: Build one of JR's own Madden 27 mods (.fbmod) from an idea, using a recipe, MMC's own libraries through the console harness (or MMC Editor's window as a fallback), a read-back check, install into the Mu Test pack, and an in-game check list for JR. Use when JR runs /build-mod <idea>, or asks to make, recolour or change a Madden mod (a texture, a jersey colour, a field line, a ball).
argument-hint: <mod idea, e.g. "yellow first-down line" or "pink Jaguars and Vikings home jerseys">
---

# /build-mod

Idea: $ARGUMENTS

Read `docs/reference/MOD_BUILDING.md` first. It has the routes, the harness commands, the recipe format, the worked yellow first-down example and the known limits. For uniforms, also read its §7 and the BCA Pink Oct teardown in `docs/reference/MOD_REVERSE_ENGINEERING.md` §3. Run every command from the main checkout `/home/jrereyi/Project-Mu`. The build folders and the asset index live in its `data/mod_builds/`.

## Hard rules

- Only JR's own mods. Never decrypt, extract or edit another author's `.fbmod`. Never call MMC's decrypt or key code (`FrostyModCryptor`, `FrostyModKeyProvider`, `FrostyModReader.GetResourceData`). Never patch MMC's tools. To change someone else's mod, rebuild the change ourselves, or ask the author for the `.fbproject`.
- Never copy MMC DLLs anywhere. The harness loads them from the editor folder.
- **Never search for, download, or ask JR to paste key material** into chat (the Madden 27 initfs key, or any other key). JR enters the key himself, in MMC Editor's prompt.
- Install only `Mu_*.fbmod` files, and only into `Mu Test` or another test pack JR names. Never pass `--pack "Mu Base"`, and never edit `Mu Base` or another author's file in the Mods folder.
- **Ask JR before `install`.** The Mod Manager must be closed when you install.
- The window route (computer-use on MMC Editor) needs JR's yes in this conversation, every time. JR declined screen access once on 2026-10-30. Stop on any UAC prompt, key prompt, or screen you can't explain. Never click through it.

## Steps

1. **Recipe with JR.** Turn the idea into concrete changes: which asset, which colour (`#RRGGBB`), which teams or uniforms. Ask only what changes the build. Typical questions: the exact colour, home or away, or all variants. Don't ask about title, author or version. Use `Mu <Name>`, `JR (Project Mu)`, `1.0`, category `Cosmetic`. Write `description` as what JR will see in game.

2. **Find the assets.** Use `python3 -m tools.mod_builder find <pattern> [--kind ebx|res|bundle|legacy] [--limit N]`. A plain pattern is a substring match. A pattern with `*`, `?` or `[` is a glob on the whole name, so wrap it in `*...*`. A texture shows up as an ebx `TextureAsset` plus a res `Texture`. Use the res name in the recipe. For uniforms, start from MOD_BUILDING §7 and MOD_REVERSE_ENGINEERING §3. Jersey colour comes from the jersey's `JerseyColorMap` texture, not a colour field, and finding that texture needs `ebx-dump` (step 4). The UI pictures (`imageassetlibraries/.../vnty_*`) are not the on-field jersey. If several assets fit, show JR the candidates.

3. **Write the recipe.** Run `python3 -m tools.mod_builder new <slug>`, with a slug of lower-case letters, digits and underscores. Then write `data/mod_builds/<slug>/recipe.json` in the format of MOD_BUILDING §4. Pick the recolour mode with care. `tint` keeps each pixel's saturation and value, so white or grey pixels stay white or grey. `map` moves one colour to another within a tolerance. `hue-shift` rotates hues.

4. **Pick the route.** Run `/mnt/c/Madden27Modding/MuHarness/MmcHarness.exe info`. It takes about 30 s and prints one JSON line.
   - If it is missing or `ok` is false, the harness isn't built. Run `bash tools/mmc_harness/build.sh` once, then `info` again.
   - **Harness route**, the default: use it when every op has a harness command (`texture-recolor`, `texture-replace`, `ebx-set`) and `info` is ok.
   - **ebx ops** (`ebx-set`, or the `ebx-dump` you need to find a jersey's `JerseyColorMap`): if `info` says `"ebx_readable": false`, stop. Tell JR: "ebx needs the Madden 27 encryption key. It isn't on your PC. It comes from the MMC modding community. Enter it once in MMC Editor's prompt: when `madden27.key` is missing from the editor folder, MMC Editor asks for the key at start-up, and then saves `madden27.key` itself. Tell me when it's done." Texture-only recipes don't need the key.
   - To find field paths or texture pointers, dump the asset: `python3 -c "from tools.mod_builder.harness import run; print(run(['ebx-dump', '<asset>', 'data/mod_builds/<slug>/work/<stem>.json']))"`. Then read the JSON (`color_fields`, `$external` names).
   - **Window route**: use it only for ops with no harness command (`legacy-replace`), non-2D textures, or a failed harness. Follow MOD_BUILDING §6, and only after JR says yes in this conversation.

5. **Look at the texture before you build** a texture op you haven't seen yet. Export it with `python3 -c "from tools.mod_builder.harness import run; print(run(['export-texture', '<asset>', 'data/mod_builds/<slug>/work/<stem>.png']))"`. Preview the recolour into the scratchpad with `python3 -c "from tools.mod_builder.recolor import recolor_png; recolor_png('<in.png>', '<scratch>/preview.png', <recolor spec>)"`. View both PNGs with the Read tool. If the change looks wrong (wrong regions, a mask instead of a painted texture, lost shading), fix the recipe first.

6. **Build.** Run `python3 -m tools.mod_builder build <slug> --dry-run`, check the planned steps, then run `python3 -m tools.mod_builder build <slug>`. Each harness call takes 13–47 s, so a one-texture mod takes about two minutes. Run the build in the background if it has many ops. On a harness error, read `data/mod_builds/<slug>/build.log` and the error text. Don't retry blindly. When you're done, view `work/<stem>.png` and `work/<stem>_recolor.png` again. Read the imported result back with `run(['export-texture', '<asset>', '<scratch>/readback.png', '--project', 'data/mod_builds/<slug>/<slug>.fbproject'])` and view it.

7. **Check.** `build` ends with the check. Run `python3 -m tools.mod_builder check <slug>` again after any rebuild. It must print `OK`. `check.json` must have no `missing`, no `extra`, no `details_mismatch` and no `unencrypted` entries. If anything fails, fix it before going on.

8. **Clashes.** Run `python3 -m tools.fbmod_conflicts data/mod_builds/<slug>/<slug>.fbmod "/mnt/c/Madden27Modding/MMC_Modding_Tools_v1.1.0.7/MMC_ModManager_v1.1.0.7/Mods/Madden27"` to find mods in `Mu Base` that touch the same assets. Ours goes on top and wins. Still, if another mod makes the same change (for example *M27 yellow first down marker*), JR must untick it in `Mu Test` for the check to mean anything.

9. **Ask JR, then install.** Say what will happen: the mod is copied in as `Mu_<Name>.fbmod`, and `Mu Test` is rewritten as `Mu Base` plus this mod on top, which replaces whatever test mod was in it. Ask him to close MMC Mod Manager. Then, only after his yes, run `python3 -m tools.mod_builder install <slug>`. If it refuses because the Mod Manager is open, ask him to close it. Don't kill it.

10. **Check list for JR.** End with a short list:
    1. Open MMC Mod Manager.
    2. Select the `Mu Test` pack. The new `Mu_<Name>` mod is last and ticked. Untick these clashing mods: … (from step 8).
    3. Launch.
    4. Where to look, exactly. Name the mode, the teams, home or away, the moment, and what it should look like compared with before. For example: "Play Now, any teams: before the first snap, the first-down line is yellow, not orange." For jerseys: "Play Now with the Jaguars at home: their teal home jersey is pink on the field."
    5. What to tell me back: works, wrong colour, no change, or crash (and when).

    Also say how to take it out again: pick `Mu Base` in the Mod Manager (MOD_BUILDING §5).

11. **Write down what you learned.** Add new facts to `docs/reference/MOD_BUILDING.md`: asset names and pointers, field paths, which recolour mode worked, harness quirks, and JR's in-game result. Edit the right section; don't append a log. Follow CLAUDE.md for `PROJECT_SPEC.md` and commits. Build output in `data/` is git-ignored and never committed.
