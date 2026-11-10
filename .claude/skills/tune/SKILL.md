---
name: tune
description: The tuning agent. Makes simulated leagues look like the real NFL. "progression" fits Project Mu's own progression engine offline (no Madden) from a snapshot of the tuning baseline (CAREER-MU2026) and presents recommended constants without applying them; "supersim" measures the installed Sabos Franchise Mod against real 2015-2025 seasons on CAREER-TUNE-* clones first, and only tunes Madden's supersim data if that baseline fails. Use when JR runs /tune progression|supersim [...], or asks to tune progression, fit the progression constants, or tune Madden's sim stats.
argument-hint: progression [--seasons 5] [--budget 200] [--seed 0] | supersim <run name> [--space PATH] [--max-iters N] [--repeats N]
---

# /tune

Request: $ARGUMENTS

Plan: `docs/done/MADDEN_PILOT_AND_TUNING_PLAN.md` §2.4, §2.5 (and R4, R6). Code: `engines/tuning/` (`targets.py`, `compare.py`, `progression_fit.py`, `supersim_loop.py`; each module's docstring explains its method). Run every command from the main checkout `/home/jrereyi/Project-Mu` with `.venv/bin/python3`. Outputs go to `data/tuning/` (git-ignored).

## Hard rules

- **Never apply what a fit recommends.** Never edit `engines/progression.py` (V2 core U7 owns it) or any engine constant. Present the numbers and stop. Applying them is a follow-up that JR approves for the progression owner.
- Read saves only through `node pipeline/save_reader/pc_save_reader.mjs <save> --out <scratch>`. It reads a temp copy. Never write into Madden's saves folder (`/mnt/c/Users/jrere/Documents/Madden NFL 27/saves`) yourself, and never touch `CAREER-MU2026` (the tuning baseline, D1) or `CAREER-MUBASE` (its archived creation input, hash-pinned in `data/baselines/`).
- Supersim only: follow `/madden`'s rules (`.claude/skills/madden/SKILL.md`). **Ask JR before taking the PC, every time**, and say how long it will take. Only `CAREER-TUNE-*` saves are simmed, never while `pipeline.watch_save` runs. Never play online, buy anything or delete saves.
- **Never search for, download, or ask JR to paste the Madden 27 key.** JR enters it himself in MMC Editor's prompt.
- Tuning mods are JR's own `Mu_*` builds, installed only into the `Mu Tune` pack (never `Mu Base`).

## /tune progression

1. **Targets.** If `data/tuning/targets_2015_2025.json` is missing, build it: `.venv/bin/python3 -m engines.tuning.targets` (downloads nflverse once through the history cache). The progression fit doesn't score against it. It scores against the snapshot's own league shape and `data/history/nfl_careers_through_2025.csv`. But `/tune supersim` and `compare` need it, so keep it current.
2. **Snapshot** the tuning baseline (`CAREER-MU2026`), read-only, into the scratchpad:
   `node pipeline/save_reader/pc_save_reader.mjs "/mnt/c/Users/jrere/Documents/Madden NFL 27/saves/CAREER-MU2026" --out <scratchpad>/mu2026.json`
   (about 2 s, 2,279 rostered players). If JR names another save, use that one the same way.
3. **Fit**: `.venv/bin/python3 -m engines.tuning.progression_fit <scratchpad>/week1.json --seasons 5 --budget 200 --seed 0`.
   - A replay takes about 0.8 s per season on a full league, so the default takes about 15 minutes. Run it in the background and wait for it to finish.
   - For a quick look, use `--seasons 3 --budget 20`, about a minute.
   - Pass JR's flags through.
4. **Present** `data/tuning/progression_fit.md` (the JSON next to it has every detail):
   - The score before and after (lower is better; 0 means every target is met).
   - The recommended constants, in order of score gain: name, the engine's value now, the recommendation.
   - The targets that moved most, and the worst ones left.
   - Caveats from the report:
     - The career targets come from a survivor-biased cross-section (retention isn't scored).
     - Exits and rookies are the fit's own simple rules (the module docstring).
     - The replay is OVR-level only, and season-1 inputs are thin when the save has few last-season lines.
   - Say plainly that nothing was applied. Offer a second seed (`--seed 1`) to check that the recommendation holds.

## /tune supersim <run>

Stop at the first check that fails, and tell JR what's missing.

**Do not start from vanilla.** `Mu Base` already carries Sabos FranchiseMod 1.3, which ships 157 `supersim_*`
tuning assets (tackle chances, injury, fatigue, play time, run/pass/punt/FG play) tuned to the last three real
seasons. Step 1 is therefore a **measurement, not a tune**: if the installed mod already passes the gates in
`docs/reference/TUNING_ACCEPTANCE_THRESHOLDS.md` §3, record "Sabos used as-is; supersim not tuned" and stop.
That is a legitimate result (JR, 2026-11-09).

1. **Baseline the installed game.** Clone the canonical base, sim one full season with `Mu Base` active, read it
   with `pc_save_reader.mjs`, and score it with `engines/tuning/compare` against `targets_2015_2025.json`.
   Record the overall score and the worst-10 stats. **If it passes, stop here.**
2. **ebx access (only if the baseline fails).** Run `/mnt/c/Madden27Modding/MuHarness/MmcHarness.exe info` (read-only, about 30 s). It should show `"ebx_readable": true` and `"key_source": "editor_builtin"` (the harness loads MMC's built-in game key the way the editor does, since 2026-10-31). If it says `false`, stop and tell JR the MMC build may have moved its key; `supersim_loop.run` refuses by itself too. Never search for or download keys.
3. **The search space (only if the baseline fails).** You need `data/tuning/runs/<run>/space.json` (or JR's `--space`): `{"fields": [{asset, path, start, step, min, max}]}`, one entry per supersim field to tune.
   - Build it from the worst stats in the baseline `compare` report (R4), and set **`start` to the running value in the installed game** (Sabos's), never a vanilla value. First establish whether `ebx-dump` returns the installed or the vanilla values — that decides how the space is built.
   - Dump the `football/attribsys/data/supersim_*` assets with `tools.mod_builder.harness.run(['ebx-dump', <asset>, <out.json>])`.
   - Show JR the list before running.
4. **The clone and the pilot.** The run sims `CAREER-TUNE-<run>`, made by `python3 -m tools.madden_pilot clone-save` before every season from the **tuning baseline** `CAREER-MU2026` (CLOSE_OUT_CHECKLIST P2-09). Never `CAREER-WEEK1` — it lacks the roster-size mod. Before starting, check three things:
   - The pilot CLI exists (`tools/madden_pilot/jobs.py`).
   - The save watcher isn't running: `ps -eo args | grep "[p]ipeline[.]watch_save"` should print nothing.
   - Madden's Mod Manager is closed (install needs it closed).
5. **Ask JR** for the PC: "Supersim tuning run `<run>` takes the PC's screen and keyboard. Each iteration builds a mod, relaunches Madden with Mu Tune and sims a season: about 20–30 min each, up to N iterations. Minimize Claude or move it to another monitor. OK to start?" Wait for a yes in this conversation.
6. **Run**: `.venv/bin/python3 -m engines.tuning.supersim_loop --run <run> --space <space.json> --max-iters N [--repeats 2]`.
   - Use `--repeats 2` or `3` when one season is too noisy (R6). Measure the noise first: run two baselines and compare their scores.
   - `--dry-run` prints the next change only.
   - Run it in the background.
7. **Report after every iteration.** Each line of `data/tuning/runs/<run>/log.jsonl` is one iteration. Tell JR:
   - the change (field, old value to new value);
   - the score and the gain;
   - whether the change was kept or undone;
   - the three worst stats;
   - the minutes it took.
8. **Stop** when any of these happens:
   - The loop's stop rule fires (no 2% gain over 3 tries).
   - `--max-iters` is reached.
   - JR says stop. Then create `data/tuning/runs/<run>/STOP`, and the loop stops before the next iteration.

   At the end, present `state.json` (the best values) and the recipe in `data/mod_builds/mu_tune_<run>/`. Leaving the `Mu Tune` pack installed or switching back to `Mu Base` is JR's call.
9. On a failed step (`stopped: ... failed`), read the step's output in the error. For pilot stops, read `data/pilot_runs/<run>/stop.json` and its screenshot, and handle them as `/madden` does. Don't retry blindly.
