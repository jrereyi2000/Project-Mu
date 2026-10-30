---
name: reverse-mod
description: Take apart a Madden 27 Frosty .fbmod (MMC Mod Manager) and explain in plain language what it changes and how to make or tweak a mod like it, then add the findings to docs/reference/MOD_REVERSE_ENGINEERING.md. Use when JR runs /reverse-mod <mod path or name>, or asks what a mod does, which assets it replaces, why two mods clash, or how to build a similar mod.
argument-hint: <mod path, file name, or part of a mod's name>
---

# /reverse-mod

Mod: $ARGUMENTS

Read `docs/reference/MOD_REVERSE_ENGINEERING.md` first. It holds the .fbmod format, what's verified vs guessed, and the teardowns done so far. If this mod already has a teardown there, start from it.

## Rules

- `PC_MODS/` (main checkout: `/home/jrereyi/Project-Mu/PC_MODS`) is JR's untracked download folder. **Read only.** Never write, move or rename anything in it.
- Extract only into the session scratchpad or the system temp dir, never into tracked paths. Never commit mod content.
- Every MMC-built payload is encrypted (`FMENC001`). Don't try to decrypt it, find keys, or patch the tools. Say what can't be seen and how to see it legitimately: MMC Editor, or the author's `.fbproject`.

## Steps

1. **Find the file.** If `$ARGUMENTS` isn't a path, search `PC_MODS/` (including subfolders: `Sabos Madden Gameplay Mod/`, `Dynamic Crowd Density/Mods/`, `Stadium Experience/`, `CYBERFACES/`) for a `.fbmod` whose name matches, case-insensitively. Several matches: list them and ask. A folder: list its `.fbmod`s and read any README/FAQ in it.
2. **Inspect:** `python3 -m tools.fbmod_inspect "<file>"` (add `--all` if it has more than 40 resources). Use `--json` when you need the fields (`res_type_name`, `logical_size`, `owner`, `user_data`, `extra`).
3. **Extract:** `python3 -m tools.fbmod_inspect "<file>" --extract "<scratchpad>/reverse-mod/<mod name>"`. Report how many payloads were encrypted vs decoded. For any decoded `.png` (from a DDS), view it with the Read tool. For decoded text (JSON/Lua/XML/cfg), read it.
4. **Check clashes:** `python3 -m tools.fbmod_conflicts "<file>" /home/jrereyi/Project-Mu/PC_MODS` shows the other mods that touch the same assets. Note which win where (docs/MOD_INSTALL.md §3 has the load order).
5. **Look up vanilla context if it helps** (e.g. which jerseys a uniform mod skipped): stream-decompress `PC_MODS/MMC_Modding_Tools_v1.1.0.7/MMC_Editor_v1.1.0.7/Caches/madden27.cache` (skip the first 0x28 bytes, then `zstandard` stream) and regex-search asset names. Don't write the decompressed cache to disk (575 MB).
6. **Explain it to JR in plain language:**
   - what the mod changes in the game (one or two sentences),
   - the asset groups, with counts and folders (uniform ebx, textures + pixel chunks, legacy files, audio, UI widgets, tuning, ...), and what each probably does,
   - what is verified (names, sizes, links) vs inferred (what the encrypted values are),
   - which mods it clashes with,
   - **how to make or tweak one like it:** the exact assets to open in MMC Editor, the plugin to use (Texture, FBUniformEditor, EbxToXml/DiffChecker to see changed fields, legacy text editors), what can be prepared in Python/WSL vs what needs the Windows editor (doc §6).
7. **Record it:** add or update a row in §4 of `docs/reference/MOD_REVERSE_ENGINEERING.md` (a full section like §3 for a big or interesting mod). Add anything new about the format to §2 with a verified/inferred/guess tag, and resolved or new unknowns to §7. If the inspector got something wrong (bad category, unknown res type now identified, a record it can't parse), fix `tools/fbmod_inspect.py`, add a synthetic test in `tests/test_fbmod_inspect.py`, and run `python3 -m pytest tests/test_fbmod_inspect.py -q`. Follow CLAUDE.md for PROJECT_SPEC.md and commits.
