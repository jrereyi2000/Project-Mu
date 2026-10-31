---
name: tune
description: The tuning agent. Makes simulated leagues look like the real NFL. "progression" fits Project Mu's own progression engine offline (no Madden) from a snapshot of CAREER-WEEK1 and presents recommended constants without applying them; "supersim" tunes Madden's supersim data with Mu Tune mods, the pilot's simulated seasons on CAREER-TUNE-* clones, and scores against real 2015-2025 seasons. Use when JR runs /tune progression|supersim [...], or asks to tune progression, fit the progression constants, or tune Madden's sim stats.
argument-hint: progression [--seasons 5] [--budget 200] [--seed 0] | supersim <run name> [--space PATH] [--max-iters N] [--repeats N]
---

# /tune

Request: $ARGUMENTS

Plan: `docs/in_progress/MADDEN_PILOT_AND_TUNING_PLAN.md` §2.4, §2.5 (and R4, R6). Code: `engines/tuning/` (`targets.py`, `compare.py`, `progression_fit.py`, `supersim_loop.py`; each module's docstring explains its method). Run every command from the main checkout `/home/jrereyi/Project-Mu` with `.venv/bin/python3`. Outputs go to `data/tuning/` (git-ignored).

## Hard rules

- **Never apply what a fit recommends.** Never edit `engines/progression.py` (V2 core U7 owns it) or any engine constant. Present the numbers and stop. Applying them is a follow-up that JR approves for the progression owner.
- Read saves only through `node pipeline/save_reader/pc_save_reader.mjs <save> --out <scratch>`. It reads a temp copy. Never write into Madden's saves folder (`/mnt/c/Users/jrere/Documents/Madden NFL 27/saves`) yourself, and never touch `CAREER-WEEK1` (the baseline league, D1).
- Supersim only: follow `/madden`'s rules (`.claude/skills/madden/SKILL.md`). **Ask JR before taking the PC, every time**, and say how long it will take. Only `CAREER-TUNE-*` saves are simmed, never while `pipeline.watch_save` runs. Never play online, buy anything or delete saves.
- **Never search for, download, or ask JR to paste the Madden 27 key.** JR enters it himself in MMC Editor's prompt.
- Tuning mods are JR's own `Mu_*` builds, installed only into the `Mu Tune` pack (never `Mu Base`).

## /tune progression

1. **Targets.** If `data/tuning/targets_2015_2025.json` is missing, build it: `.venv/bin/python3 -m engines.tuning.targets` (downloads nflverse once through the history cache). The progression fit doesn't score against it. It scores against the snapshot's own league shape and `data/history/nfl_careers_through_2025.csv`. But `/tune supersim` and `compare` need it, so keep it current.
2. **Snapshot** CAREER-WEEK1, read-only, into the scratchpad:
   `node pipeline/save_reader/pc_save_reader.mjs "/mnt/c/Users/jrere/Documents/Madden NFL 27/saves/CAREER-WEEK1" --out <scratchpad>/week1.json`
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

1. **The key.** Run `/mnt/c/Madden27Modding/MuHarness/MmcHarness.exe info` (read-only, about 30 s). If it says `"ebx_readable": false`, stop. Tell JR: "Supersim tuning edits ebx, which needs the Madden 27 encryption key. Enter it once in MMC Editor's prompt (it saves `madden27.key`), then tell me." `supersim_loop.run` refuses by itself too. As of 2026-10-30 the key isn't entered, so this stop is expected.
2. **The search space.** You need `data/tuning/runs/<run>/space.json` (or JR's `--space`): `{"fields": [{asset, path, start, step, min, max}]}`, one entry per supersim field to tune.
   - If it's missing, build it with JR. Dump the `football/attribsys/data/supersim_*` assets (`tools.mod_builder.harness.run(['ebx-dump', <asset>, <out.json>])`), and pick the few fields that move the worst stats in a baseline `compare` report (R4).
   - `start` is the game's current value.
   - Show JR the list before running.
3. **The clone and the pilot.** The run sims `CAREER-TUNE-<run>`, which is a fresh copy of `CAREER-WEEK1` made by `python3 -m tools.madden_pilot clone-save` before every season. Before starting, check three things:
   - The pilot CLI exists (`tools/madden_pilot/jobs.py`).
   - The save watcher isn't running: `ps -eo args | grep "[p]ipeline[.]watch_save"` should print nothing.
   - Madden's Mod Manager is closed (install needs it closed).
4. **Ask JR** for the PC: "Supersim tuning run `<run>` takes the PC's screen and keyboard. Each iteration builds a mod, relaunches Madden with Mu Tune and sims a season: about 20–30 min each, up to N iterations. Minimize Claude or move it to another monitor. OK to start?" Wait for a yes in this conversation.
5. **Run**: `.venv/bin/python3 -m engines.tuning.supersim_loop --run <run> --space <space.json> --max-iters N [--repeats 2]`.
   - Use `--repeats 2` or `3` when one season is too noisy (R6). Measure the noise first: run two baselines and compare their scores.
   - `--dry-run` prints the next change only.
   - Run it in the background.
6. **Report after every iteration.** Each line of `data/tuning/runs/<run>/log.jsonl` is one iteration. Tell JR:
   - the change (field, old value to new value);
   - the score and the gain;
   - whether the change was kept or undone;
   - the three worst stats;
   - the minutes it took.
7. **Stop** when any of these happens:
   - The loop's stop rule fires (no 2% gain over 3 tries).
   - `--max-iters` is reached.
   - JR says stop. Then create `data/tuning/runs/<run>/STOP`, and the loop stops before the next iteration.

   At the end, present `state.json` (the best values) and the recipe in `data/mod_builds/mu_tune_<run>/`. Leaving the `Mu Tune` pack installed or switching back to `Mu Base` is JR's call.
8. On a failed step (`stopped: ... failed`), read the step's output in the error. For pilot stops, read `data/pilot_runs/<run>/stop.json` and its screenshot, and handle them as `/madden` does. Don't retry blindly.
