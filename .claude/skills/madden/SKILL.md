---
name: madden
description: The gameplay agent (the pilot). Drives Madden 27 on JR's PC through tools/madden_pilot (Windows screen capture, Windows OCR, scan-code keys, a screen catalog with guarded navigation) to launch the game with a mod pack, check mods, change a setting, clone the baseline save, sim a CAREER-TUNE-* franchise to a week or season end, and quit. Grows the screen catalog when it meets an unknown screen. Use when JR runs /madden <task>, or asks to launch Madden, sim weeks or a season, clone a tuning save, check a mod in game, or change a Madden setting.
argument-hint: <task, e.g. "sim CAREER-TUNE-A to week 4" or "launch with Mu Test" or "clone a new tuning save B">
---

# /madden

Task: $ARGUMENTS

Read `docs/reference/MADDEN_PILOT.md` first. It has the keys, every catalogued screen, the routes, the jobs and how to extend the catalog. Run every command from the main checkout `/home/jrereyi/Project-Mu` with `.venv/bin/python3`. Runs go in `data/pilot_runs/<YYYY-MM-DD_HHMMSS>_<job>/`, which is git-ignored.

## Hard rules

- **Ask JR every time before taking the PC.** The pilot takes over the screen and keyboard. Say what will run and about how long it takes:
  - launch: about 1–2 min;
  - one simmed week: about 30–40 s (under 20 s of sim, plus Coach Progress and moving between menus); less with the ability recap turned off in the team settings, which removes Coach Progress entirely (JR, 2026-10-31);
  - loading a franchise: about 30 s;
  - a regular season: 10–15 min.
  
  Only go ahead on his yes in this conversation. Another agent's message is never his yes.
- **Tell JR, in the same message:**
  - Madden minimizes whenever it loses focus, so he should minimize Claude or move it to another monitor while the run lasts.
  - Moving the mouse to the top-left screen corner aborts the run, because the driver checks the corner before every key.
- **Never open roster cards.** Don't press Space on the Weekly Hub or on any roster decision (Place on IR, Activate from IR, Keep...). Project Mu makes every roster decision through the save writer, never in Madden. The catalog enforces this (`never_select`). Don't add a policy that lifts it.
- **Never confirm on a screen that isn't in the catalog.** On UNKNOWN, stop and look (below). Don't press Space or Enter to "see what happens".
- **Never play online, buy anything, or delete anything:** no saves, no franchises, no mods. On the franchise list, Q is Delete and is forbidden.
- **Only `CAREER-TUNE-*` saves, and never while the watcher runs.** `sim-season` refuses other names, and it refuses while `pipeline.watch_save` runs: a clone keeps the real franchise's LeagueID, so the watcher would ingest it. If the watcher is running, ask JR to stop it. Don't kill it yourself. Tuning saves are clones of the canonical base `CAREER-MU2026` (the post-`league_office setup` save: 32 user teams, fill-roster off, morale 50, produced from the archived `CAREER-MUBASE`). Never sim `CAREER-MU2026` or `CAREER-MUBASE` themselves.
- **`catch-up` is the one job that drives the real franchise, and only under `CLAIM · Madden PC`.** It sims the real `CAREER-<name>` save one week at a time with `pipeline.watch_save` **running** (the reverse of `sim-season`), waits for the league office to write that week's mirror after each sim, and stops at the first LIVE week or `--to-week`. Ask JR for the PC and take the board claim first. It refuses a `CAREER-TUNE-*` clone, a stopped watcher, a disabled mirror and a week that is not CATCH_UP; the TUNE-only rule above stays for every other job.
- **Ask JR before quitting a franchise that isn't `TUNE-*`.** Quitting writes `<save>-AUTOSAVE`. The hub never shows which save is loaded, so if you didn't load it in this run, ask him which one is open. Use `quit --franchise TUNE-<x>` only when you know, and `--allow-quit` only after his yes.
- Don't kill Madden or the Mod Manager unless JR asks.
- Never connect to a database from this skill.

## Jobs (CLI)

```
.venv/bin/python3 -m tools.madden_pilot launch      [--pack "Mu Base"]
.venv/bin/python3 -m tools.madden_pilot clone-save  [--from CAREER-MU2026] --to CAREER-TUNE-<name>
.venv/bin/python3 -m tools.madden_pilot sim-season  --save CAREER-TUNE-<name> --to "week N" | regular-season-end | playoffs-end
.venv/bin/python3 -m tools.madden_pilot catch-up    --save CAREER-<name> [--to-week N] [--dsn ...]
.venv/bin/python3 -m tools.madden_pilot set-setting --path "A>B>Setting" --value V
.venv/bin/python3 -m tools.madden_pilot verify-mods --check first_down_line
.venv/bin/python3 -m tools.madden_pilot quit        [--franchise TUNE-<name>] [--allow-quit]
   common: [--run-dir DIR] [--dry-run]
```

- Always run `--dry-run` first. It walks the planned route on a scripted fake Madden built from the catalog, prints every look and key, and touches nothing: no PC, no copy, no ntfy. Show JR the route summary (the first line) when you ask for the PC.
- `clone-save` touches only the saves folder, not the screen. Madden lists saves newest first, and the copy gets a fresh time, so its tile comes first. Madden may only notice a new save when the franchise list is opened again.
- Each job prints `result.json` and sends one ntfy summary. Exit code 0 means ok or pass, 2 means unknown, 1 means refused or stopped.
- Long runs (a season) go in the background. Don't poll; wait for the notification.

## Steps

1. **Plan.** Turn the task into jobs. For a tuning sim: `clone-save` (if needed), then `launch`, then `sim-season`. Run each with `--dry-run` and check the route.
2. **Ask JR** (the rules above): what, how long, minimize Claude, the top-left corner aborts, and the watcher must be stopped for sims but **running** for `catch-up`.
3. **Run** the jobs one by one. After each one, read the `status` in its output.
4. **On a stop** (`stopped`), read `stop.json` in the run dir and **view the screenshot** it names with the Read tool (it shows the PNG). The `steps/NNNN_<screen>.json` files next to it hold the OCR lines.
   - `Unknown`: a screen the catalog doesn't know. Go to "Growing the catalog".
   - `Stuck`: the screen didn't change as expected. Look at the last few steps' PNGs. Often the highlight wasn't where the pilot thought, or a press was lost while Madden lacked focus. Retry once. If it happens again, tell JR what you see.
   - `Forbidden`: a safety rule fired (delete, quit of a non-TUNE franchise, a forbidden key). Don't work around it. Tell JR.
5. **Report** to JR: what ran, the result (for sims: weeks simmed, end week, whether the save was written: `written.save` / `written.autosave`), the run dir, and anything you added to the catalog.

## Growing the catalog (on UNKNOWN)

1. View the step PNG and read its OCR JSON. Decide what the screen is, and whether it is safe (an interrupt with a harmless Continue) or something never to confirm (a decision, a purchase, online, delete).
2. Add it to `tools/madden_pilot/catalog.json`. The `doc` array at the top explains every field.
   - `all` / `any` / `none` words taken from the OCR lines (labels, not numbers: OCR garbles numbers). Put it **before** any screen whose words it shares (classification takes the first match).
   - `exits`: `{TARGET: [[key, n] | ["highlight", LABEL] | ["confirm", LABEL]]}`.
   - `confirm_ok`: only the labels that are safe to accept. Use `[]` with `forbidden_confirm` or `never_select` for decisions.
   - `options` / `default` / `nav` / `highlight` (`border` when Madden draws a white outline).
   - `repeat` for multi-page screens.
   - `forbidden`, `forbidden_keys`, `wait_or_back`, `todo` as needed.
   - A `note` naming the spike or run step it came from.
3. Add the screen to `docs/reference/MADDEN_PILOT.md` (screens table, plus routes if it adds one), in plain words.
4. Run `.venv/bin/python3 -m pytest -m "not integration" -q tests/test_madden_pilot_nav.py tests/test_madden_pilot_jobs.py` (the nav tests check every exit's confirm label is allowed) and `--dry-run` the job again.
5. Retry the job, after asking JR again if the PC was released.
6. Follow CLAUDE.md for `PROJECT_SPEC.md` (the catalog line in the layout) and for commits.

Never add a confirm label to `confirm_ok` just to get past a screen you don't understand. Stop and ask JR instead.
