# Project Mu: Madden Pilot and Tuning Plan

**Status:** approved by JR (2026-10-30); building, wave 0 of 2 (spike P-a/P-b, needs JR's yes for the PC).
**Builds on:** [`V2_MASTER_PLAN.md`](../in_progress/V2_MASTER_PLAN.md) §8.1 route 2 (UI automation from Windows); [`MOD_BUILDER_PLAN.md`](../done/MOD_BUILDER_PLAN.md) (tuning mods are built with `tools/mod_builder`); [`PROGRESSION_PLAN.md`](../in_progress/PROGRESSION_PLAN.md) (Project Mu's own progression engine); PROJECT_SPEC §1.1 (`madden_capture.py`, `pipeline/save_reader/`, `pipeline/load_save.py`).

**Goal:** two agents that let Madden run experiments while JR is away.
- **Gameplay agent ("pilot"):** drives Madden 27 on the PC. It reads the screen, knows the menus and presses keys. It can launch the game with a mod pack, check that mods loaded, change a setting, and sim games or a whole season on a franchise save, then quit and leave the save for us to read.
- **Tuning agent:** makes simulated leagues look like the real NFL. It works in two places:
  - **Progression** is Project Mu's own engine (`engines/progression.py`), so it is tuned offline in Python with no Madden at all.
  - **Supersim stat output** is Madden's own tuning data, so it is tuned with mods built by `tools/mod_builder`. The pilot sims a season, we read the save and compare against real NFL seasons, then adjust and repeat.

**Delivers:**
- `tools/madden_pilot/`: the Windows-side screen and keyboard driver, a screen catalog and classifier, guarded navigation, jobs (`launch`, `verify-mods`, `set-setting`, `sim-season`), and a CLI `python3 -m tools.madden_pilot ...`
- `/madden` skill: the gameplay agent, which uses the pilot, looks at screenshots itself, and grows the screen catalog
- `engines/tuning/`: real-NFL targets from nflverse, a comparison of a simulated season against them, an offline progression fit, and the supersim tuning loop
- `/tune` skill: the tuning agent
- `docs/reference/MADDEN_PILOT.md`: the screen catalog and routes in plain words, and how to extend them

**JR's answers (2026-10-30):**
- D1 (Q3): the baseline league is the fresh created league at week one, `CAREER-WEEK1` (read 2026-10-30: calendar year 2026, `RegularSeason` week index 0, LeagueID −1851690351). Each tuning run clones it to `CAREER-TUNE-<name>` (Madden only lists `CAREER-*` saves). `sim-season` never sims `CAREER-WEEK1` itself.
- D2: Q1, Q2, Q4 and Q5 approved as recommended (progression first; runs only when JR starts them; 2015–2025 targets; keyboard first).

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `madden_capture.py` (4,541 lines) drives the PS5 through chiaki-ng with Apple Vision OCR: `classify(f)` maps OCR text to about 40 screen names (`:512`), `wait_for(screens, timeout)` (`:633`), `press_into(key, screen, expected)` with retries (`:2459`), `recover_stream` (`:157`), and per-menu allow-lists for Cross (`LEAGUE_CROSS_OK` `:473`, `ROSTER_CROSS_OK` `:480`). | The pattern carries over: a classifier on OCR words, guarded presses, allow-lists for confirm presses, recovery. The code doesn't, because it is macOS + Remote Play specific. The pilot is new code with the same shape, and many screen tests (hub, schedule, box score) can start from these rules. |
| S2 | Windows has Python 3.14 at `C:\Program Files\Python314\`. The Windows session is where Madden's window, screen capture and keyboard input live. | The driver is a small Windows-Python program called from WSL. Everything else (catalog, navigation, jobs) runs in WSL Python. |
| S3 | `pipeline/save_reader/pc_save_reader.mjs:250-251` reads every player's `season_stats` and `game_stats`; `:125-126` reads each game's team stats. `pipeline/load_save.py:135-220` turns them into offense, defense, kicking and return columns. | A simulated season is read straight from the save file, with no OCR of stat screens. |
| S4 | `engines/history_backfill/sources.py:41` already downloads nflverse releases, and `:52` maps nflverse `stats_player` columns to ours. `data/history/nfl_careers_through_2025.csv` has career totals. | Real per-season targets come from nflverse `stats_player` (regular season, 2015–2025) through the same download and cache. |
| S5 | Supersim tuning in Madden is ebx: FranchiseMod's 157 assets under `football/attribsys/data/supersim_*` (compound abilities, tackle chances, injury, fatigue, play time, run/pass/punt/FG play) (`docs/reference/MOD_REVERSE_ENGINEERING.md:140`). The harness reads and writes ebx only once the Madden 27 key is in MMC Editor (`docs/done/MOD_BUILDER_PLAN.md`, status bullets). | Supersim tuning waits on JR entering the key. Progression tuning doesn't. |
| S6 | `engines/progression.py` is pure functions (`compute_player_delta` `:155`, `apply_ratings_progression` `:685`, the terms `:312-674`). PROGRESSION_PLAN turns Madden's progression sliders to 0 and has Project Mu decide rating changes. `docs/in_progress/V2_CORE_BUILD_PLAN.md` U7 is editing progression right now (another session). | Progression tuning replays the engine offline over a league snapshot, and outputs recommended constants plus a report. It doesn't edit `progression.py`; applying the numbers is a short follow-up once V2 core's progression units are merged. |
| S7 | `pipeline/watch_save.py:25,103` watches `CAREER-*` saves and routes each one by its `Franchise.LeagueID` (`:322`, `:334`) into that franchise's database. Madden's franchise menu only lists saves named `CAREER-*` (JR, 2026-10-30). `docs/in_progress/V2_CORE_BUILD_PLAN.md` U16 owns `watch_save.py` and isn't built yet. | Test saves must be named `CAREER-TUNE-<name>` to show up in Madden. A copied save keeps the original's LeagueID, so the watcher would ingest it into the real franchise database. `sim-season` therefore refuses to run while the watcher is running, and only sims `CAREER-TUNE-*` saves. Skipping `CAREER-TUNE-*` in the watcher is a follow-up after V2 core U16 (this plan doesn't own `watch_save.py`). |
| S8 | `docs/in_progress/V2_CORE_BUILD_PLAN.md:364` plans `pipeline/game_launch.py` (`MMCModManager.exe -launch <pack>` from WSL). V2_MASTER_PLAN §8.1 lists UI automation as an unexplored route. | The pilot's `launch` reuses `game_launch` if it's merged, and otherwise runs the same command itself. Once the pilot works, §8.1's "boot straight into the game" is one job on top of it. |
| S9 | Computer-use can't send input to elevated windows. Both MMC tools run as normal user (`asInvoker`). | If Madden itself runs as normal user, the driver's `SendInput` reaches it. Spike P-a checks this. |

## 1. Scope

**In:**
- **Driver (Windows):** capture Madden's window to PNG, OCR it with Windows' built-in OCR (`Windows.Media.Ocr`, on-device, the PC's counterpart to Apple Vision), and send keys with scan codes (tap or hold), from a CLI the WSL side calls.
- **Catalog and navigation:** a screen catalog (`catalog.json`) with each screen's OCR test and its exits (key → screen). Plus `look`, `press`, `wait_for`, `goto(screen)` (shortest path over the catalog), allow-lists for confirm keys, a stuck/unknown-screen stop, screenshots kept for every step, and an ntfy alert on stop.
- **Jobs:** `launch --pack P`, `verify-mods` (reach the main menu and run a mod's own check, e.g. a screenshot of a known screen compared against what the mod should change), `set-setting <path> <value>`, `sim-season --save CAREER-TUNE-x [--to regular-season-end|playoffs-end]`, `quit`.
- **`/madden` skill:** Claude is the gameplay agent. It runs the jobs, views screenshots when the classifier says `UNKNOWN`, adds new screens to the catalog, and asks JR before taking the PC.
- **Tuning targets and comparison:** real per-season distributions by position and stat (league leaders, percentiles, league totals and per-team totals), and a score per stat for a simulated season read from a save.
- **Progression fit (offline):** replay `engines/progression.py` over N seasons from a snapshot league. Compare OVR spread by age, peak age, career length and 90+ count against targets (Madden's league shape plus nflverse careers), and search the engine's constants. The output is a report and `data/tuning/progression_fit.json`.
- **Supersim loop:** the recipe for a tuning mod (ebx-set ops on `supersim_*` assets) → `mod_builder build` → `install` → `madden_pilot sim-season` → `pc_save_reader` → `compare` → next change. Each iteration is logged in `data/tuning/runs/<run>/`.
- **`/tune` skill:** the tuning agent, which runs both loops and reports to JR.

**Out (non-goals):**
- Playing or slow-simming games with game-time input (playcalling, coach mode). `sim-season` uses Madden's own sim menus. Slow sim ("watch CPU vs CPU") is a later job once the pilot is stable.
- Gameplay sliders for played games (they don't affect supersim). They can be a later `set-setting` use.
- Editing `engines/progression.py` (S6). The fit recommends; applying it is a follow-up.
- Online modes of any kind. Driving anything except Madden and the MMC Mod Manager.
- Searching for or downloading the Madden 27 key.

## 2. Design

### 2.1 Windows driver (`tools/madden_pilot/win_agent.py`, run by Windows Python)

`"/mnt/c/Program Files/Python314/python.exe" <repo>\tools\madden_pilot\win_agent.py <command> ...` (the repo path is given as `\\wsl.localhost\Ubuntu\home\jrereyi\Project-Mu\...`). Each command prints one JSON line. Dependencies are in `tools/madden_pilot/requirements-win.txt`: `mss`, `Pillow`, `winrt-Windows.Media.Ocr`, `winrt-Windows.Graphics.Imaging`, `winrt-Windows.Storage.Streams`, `pywin32`.

| Command | Does | Output |
|---|---|---|
| `windows` | Lists top-level windows (title, pid, rect, foreground, elevated) | `{"ok":true,"windows":[...]}` |
| `focus --title RE` | Brings a matching window to the front | `{"ok":true,"hwnd":...}` |
| `look --title RE --out PNG [--ocr]` | Captures the window's client area (mss on its rect; borderless windowed assumed) and optionally OCRs it | `{"ok":true,"png":...,"size":[w,h],"words":[{"text","box":[x,y,w,h],"line"}]}` |
| `key --name NAME [--hold S] [--times N] [--gap S]` | `SendInput` with scan codes (`KEYS` map: up/down/left/right/enter/esc/space/tab/backspace/a–z/0–9/f1–f12/pgup/pgdn/lshift/lctrl) to the foreground window; refuses if Madden isn't foreground | `{"ok":true}` |
| `run --exe PATH [--args ...]` | Starts a process without elevation | `{"ok":true,"pid":...}` |
| `kill --name EXE` | Ends Madden only (allow-list: `Madden27.exe`, `MMCModManager.exe`) | `{"ok":true}` |

WSL wrapper (`tools/madden_pilot/win.py`): `call(cmd: list[str], timeout=60) -> dict`, `WinError`; `MU_WIN_PYTHON` overrides the interpreter path. It converts path arguments with `wslpath -w`.

### 2.2 Catalog and navigation (`tools/madden_pilot/screens.py`, `navigate.py`, `catalog.json`)

`catalog.json`:

```json
{
  "version": 1,
  "keys": {"confirm": "enter", "back": "esc", "tab_next": "e", "tab_prev": "q"},
  "screens": {
    "MAIN_MENU": {"all": ["FRANCHISE"], "any": ["PLAY NOW", "EXHIBITION"], "none": ["CONTINUE"],
                  "exits": {"FRANCHISE_SELECT": [["down", 2], ["confirm", 1]]},
                  "confirm_ok": ["FRANCHISE"]},
    "HUB": {"all": ["GAMEDAY"], "any": ["COACH CENTRAL", "OPTIONS", "MANAGE ROSTER"], "exits": {}}
  }
}
```

- `screens.Frame(png, words)`: `text` (upper-cased, joined), `find(word)`, `near(word, other)`.
- `classify(frame, catalog) -> str`: the first screen whose `all` words are all present, at least one `any` word is present (when listed), and no `none` word is present. Otherwise `"UNKNOWN"`. Screens are checked in catalog order (most specific first).
- `navigate.Pilot(catalog, run_dir)`:
  - `look() -> (Frame, screen)`: saves `run_dir/steps/NNNN_<screen>.png` plus the OCR JSON.
  - `press(key, hold=0.08, wait=0.6, times=1)`.
  - `wait_for(screens, timeout=10) -> (Frame, screen)`: raises `Stuck`.
  - `goto(target, timeout=120)`: BFS over `exits`, then press and confirm each hop with `wait_for`, re-planning from wherever it lands.
  - `confirm_on(screen, label)`: presses confirm only if `label` is in that screen's `confirm_ok` list. This is the counterpart of madden_capture's Cross allow-lists, so the pilot never confirms Delete, Quit-without-saving, Online, or Store screens.
- Stops: `Stuck` (same screen after N presses), `Unknown` (`UNKNOWN` for more than `--unknown-limit` looks), and `Forbidden` (a screen tagged `"forbidden": true`: online, store, delete save, sign-in). Each one saves a screenshot, writes `run_dir/stop.json`, and sends ntfy through `pipeline.notify.notify` (as `tools/commit_audit.py` does).

### 2.3 Jobs and CLI (`tools/madden_pilot/jobs.py`, `__main__.py`)

`python3 -m tools.madden_pilot <job> [--run-dir DIR] [--dry-run]`. Every run gets `data/pilot_runs/<YYYY-MM-DD_HHMMSS>_<job>/` (git-ignored: `data/*`).
- `launch [--pack P]`: `pipeline.game_launch.launch` if it exists, else `MMCModManager.exe -launch <pack>` through `cmd.exe`. Then waits for Madden's window and `goto("MAIN_MENU")`, passing intro screens (`"skip": ["confirm"]` screens in the catalog).
- `verify-mods --check CHECK`: runs one check from `checks/` (`checks/first_down_line.py` ships as the example: it opens a practice or exhibition screen where the line shows, captures, and measures the line's colour). It prints pass, fail or unknown with the screenshot. Checks are small Python files with `run(pilot) -> dict`.
- `set-setting --path "Settings>Gameplay>Accel Clamp" --value Off`: walks a menu path by OCR labels, changes the value with left/right until the label reads `value`, then backs out and saves.
- `clone-save --from CAREER-WEEK1 --to CAREER-TUNE-<name>`: copies the save file in Madden's saves folder (`MADDEN_SAVES_DIR`) and refuses to overwrite anything that isn't `CAREER-TUNE-*` (D1).
- `sim-season --save CAREER-TUNE-<name> [--to regular-season-end|playoffs-end|week N]`: refuses unless the save name starts with `CAREER-TUNE-` and no `pipeline.watch_save` process is running (`pgrep -f pipeline.watch_save` in WSL, S7). Then loads that franchise save, uses the hub's sim option, watches progress until the target week, saves, quits to the main menu, and writes `result.json` (save path, weeks simmed, minutes).
- `quit`: back out to the main menu and `kill --name Madden27.exe` only if it doesn't respond.

### 2.4 Tuning (`engines/tuning/`)

- `targets.py`:
  - `build_targets(seasons=range(2015, 2026)) -> dict` downloads nflverse `stats_player` (regular season) through `engines.history_backfill.sources._get` (same cache). For each stat and position group it keeps the league-leader values (ranks 1, 5, 10, 25), the percentiles (p50, p75, p90) of qualifying players, the league totals per team-game, and the per-team season totals (points, yards, sacks, turnovers). Per-season values are averaged, with the min and max across seasons as the band.
  - The result is written to `data/tuning/targets_2015_2025.json`.
- `compare.py`: `season_from_snapshot(snapshot, season) -> dict` (the same shapes, from `season_stats` and the games' team stats) and `compare(sim, targets) -> Report`. A stat's score is how far it sits outside the real band in band widths (0 inside). The report gives the overall score and the worst 10 stats. `python3 -m engines.tuning.compare <snapshot.json> [--season N] [--out report.md]`.
- `progression_fit.py`: `replay(snapshot, seasons=5, params=None, seed=0) -> league history`, using only the pure functions of `engines/progression.py`. Inputs that come from games (performance, games missed) come from the snapshot's last season, with seeded noise. `fit(snapshot, targets, budget=200) -> {params, score, report}` does a coordinate search over the engine's module-level constants (read from the module, never written). It writes `data/tuning/progression_fit.json` and a markdown report. `python3 -m engines.tuning.progression_fit <snapshot.json> [--seasons 5] [--budget 200]`.
- `supersim_loop.py`: `iteration(run, change) -> dict` writes the tuning recipe (`ebx-set` ops on `football/attribsys/data/supersim_*`), runs `tools.mod_builder build` + `install` (pack `Mu Tune`), runs `tools.madden_pilot sim-season`, reads the save with `pc_save_reader.mjs`, runs `compare`, and appends to `data/tuning/runs/<run>/log.jsonl`. `next_change(log) -> change` picks the next change: one asset field at a time, a step size that halves on overshoot, and a stop when nothing improves the score by 2% over 3 tries. Every step that needs Madden is a lazy import, so the module and its tests work without it. It refuses to start if `MmcHarness.exe info` says `ebx_readable: false`.

### 2.5 Skills

- `.claude/skills/madden/SKILL.md` (`/madden <task>`):
  - Ask JR before taking the PC's screen and keyboard, every time (Q2), and say how long the run should take.
  - Run jobs through the CLI. On `UNKNOWN`, view the step PNG, add the screen to `catalog.json` (OCR test + exits) and `docs/reference/MADDEN_PILOT.md`, then retry.
  - Never press confirm on a screen that isn't in the catalog.
  - Never play online, buy anything, or delete saves.
  - Only sim `CAREER-TUNE-*` saves, and never while the save watcher runs.
- `.claude/skills/tune/SKILL.md` (`/tune progression|supersim [...]`):
  - Progression: build the targets, run `progression_fit`, and present the recommended constants. Don't apply them.
  - Supersim: check the key is in place, make the `CAREER-TUNE-` save with JR (a copy of a franchise save, renamed), then run iterations with `/madden`'s rules. Report after each iteration and stop at the stop rule or when JR says.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | Madden 27 on PC can't be captured (fullscreen DX12 gives black frames) or ignores `SendInput`. | Spike P-a. | Run Madden borderless windowed (captures fine). For input, try DirectInput scan codes; else install a virtual gamepad (ViGEm, a one-time admin install by JR) and send pad buttons instead. That changes only `win_agent.py`. |
| R2 | Madden runs elevated (anti-cheat or launcher), so input from a normal process is blocked (S9). | Spike P-a: `windows` reports `elevated`. | Run `win_agent.py` from a Task Scheduler task with highest privileges (D1's fallback in the mod builder plan). |
| R3 | Windows OCR misreads Madden's stylised fonts. | Spike P-a OCRs 5 screens. | Upscale 2× and threshold before OCR (madden_capture did similar). Fall back to a local vision model through Ollama for classification only. |
| R4 | Supersim tuning fields aren't understood, or one change shifts many stats. | After the key: `MmcHarness ebx-dump` on the `supersim_*` assets, then a 1-season baseline and a one-field test. | Narrow to the few fields that move the worst stats. Use FranchiseMod's values as a known-good comparison point (reading its asset list only, never its content). |
| R5 | A season sim takes a long time, or Madden stops for events (trade offers, injuries, re-signings, owner prompts) that need answers. | Spike P-b sims a season and records every interrupting screen. | Each one becomes a catalog screen with a fixed safe answer (decline, auto, continue). Unknown ones stop the run (§2.2). |
| R6 | One simulated season is noisy. | Baseline: sim the same `CAREER-TUNE-` save twice and compare the two runs' scores. | Average 2–3 seasons per iteration, or compare league-level stats only. |
| R7 | The watcher ingests a pilot save into a real franchise database (S7). | `sim-season` refuses non-`CAREER-TUNE-*` saves and refuses while `pipeline.watch_save` runs; tests check both. | Follow-up after V2 core U16 merges: `watch_save.py` skips `CAREER-TUNE-*`. Until then JR stops the watcher before a pilot run (the job says so). |
| R8 | The pilot takes over the PC while JR is using it. | Known. | Runs start only when JR asks. `Esc`-hold or moving the mouse to a screen corner aborts (the driver checks before every key). |

**Spike first** (inline, before wave 1, with JR's yes for the PC; about 1 hour):
- **P-a:** start Madden (through the Mod Manager with `Mu Base`). With a 30-line Windows-Python script, capture the window, OCR the main menu, the franchise hub and one settings screen, and press down and confirm. This answers R1–R3.
- **P-b:** on a copy of a franchise save renamed `CAREER-TUNE-SPIKE` (watcher stopped), find the hub's sim option and sim a few weeks, recording every screen. This answers R5 and gives the first catalog entries.

## 4. Tests (synthetic, no live DB)

- `tests/test_madden_pilot_win.py`:
  - `win.call` against a fake interpreter script: path conversion, JSON parsing, timeout, errors.
  - The `KEYS` map, and the kill allow-list refusing other exes.
- `tests/test_madden_pilot_nav.py`:
  - `classify` on synthetic word lists (`all`/`any`/`none`, order).
  - `goto` planning and re-planning with a fake driver that moves between screens.
  - `confirm_on` refusing unlisted labels.
  - `Stuck`, `Unknown` and `Forbidden` stops writing `stop.json`.
- `tests/test_madden_pilot_jobs.py`:
  - `sim-season` refusing saves not named `CAREER-TUNE-*`, and refusing while a `pipeline.watch_save` process runs (process check mocked).
  - The job flows against a scripted fake pilot (launch → main menu, sim to week N, the interrupt screens answered).
  - `set-setting` walking a path.
- `tests/test_tuning.py`:
  - `targets` from a tiny synthetic nflverse frame.
  - `season_from_snapshot` on a synthetic snapshot.
  - `compare` scores (inside the band = 0, outside it scaled by band width).
  - `progression_fit.replay` determinism under a seed.
  - `fit` improving a planted bad constant.
  - `supersim_loop.next_change` (halving on overshoot, the stop rule), with mod_builder and pilot mocked, and its refusal when ebx isn't readable.

Run with `pytest -m "not integration" tests/test_madden_pilot_win.py tests/test_madden_pilot_nav.py tests/test_madden_pilot_jobs.py tests/test_tuning.py`.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`.

### U1: Windows driver

- **Owns:** `tools/madden_pilot/win_agent.py` (new), `tools/madden_pilot/win.py` (new), `tools/madden_pilot/requirements-win.txt` (new), `tests/test_madden_pilot_win.py` (new)
- **Reads:** this plan §2.1 and the spike P-a notes; `tools/mod_builder/harness.py` (the same WSL→Windows wrapper pattern)
- **Depends on:** spike P-a
- **Done when:** with Madden open, `win.call(["look", "--title", "Madden", "--out", ..., "--ocr"])` returns readable words for the main menu, and `key --name down` moves the selection.
- **Verify:** `pytest -m "not integration" tests/test_madden_pilot_win.py` passes. `"/mnt/c/Program Files/Python314/python.exe" ... win_agent.py windows` prints `"ok": true`.

### U2: Catalog and navigation

- **Owns:** `tools/madden_pilot/__init__.py` (new), `tools/madden_pilot/screens.py` (new), `tools/madden_pilot/navigate.py` (new), `tools/madden_pilot/catalog.json` (new), `tests/test_madden_pilot_nav.py` (new)
- **Reads:** this plan §2.2 and the spike P-a/P-b notes (first screens); `madden_capture.py:473-640` and `:2459` for the pattern; `pipeline/notify.py`
- **Depends on:** none (it builds against `win.call`'s interface in §2.1, mocked in tests)
- **Done when:** `classify`, `goto` and the stops behave as in §2.2 on synthetic frames, and the catalog holds the screens recorded in the spike.
- **Verify:** `pytest -m "not integration" tests/test_madden_pilot_nav.py` passes.

### U3: Jobs, CLI, `/madden` skill

- **Owns:** `tools/madden_pilot/jobs.py` (new), `tools/madden_pilot/__main__.py` (new), `tools/madden_pilot/checks/__init__.py` (new), `tools/madden_pilot/checks/first_down_line.py` (new), `.claude/skills/madden/SKILL.md` (new), `docs/reference/MADDEN_PILOT.md` (new), `tests/test_madden_pilot_jobs.py` (new)
- **Reads:** this plan §2.3, §2.5; U1's and U2's code; `pipeline/mod_packs.py:231` (`launch`); `pipeline/game_launch.py` if it exists
- **Depends on:** U1, U2
- **Done when:** with JR's yes, `python3 -m tools.madden_pilot launch --pack "Mu Base"` reaches the main menu, and `sim-season --save CAREER-TUNE-SPIKE --to week 2` sims and saves. The skill and the doc cover §2.5.
- **Verify:** `pytest -m "not integration" tests/test_madden_pilot_jobs.py` passes. `python3 -m tools.madden_pilot sim-season --save CAREER-TUNE-SPIKE --to "week 2" --dry-run` prints the planned route.

### U4: Tuning targets and comparison

- **Owns:** `engines/tuning/__init__.py` (new), `engines/tuning/targets.py` (new), `engines/tuning/compare.py` (new), `tests/test_tuning.py` (new)
- **Reads:** this plan §2.4; `engines/history_backfill/sources.py:41-100`; `pipeline/load_save.py:135-250`; `pipeline/save_reader/pc_save_reader.mjs:100-260`
- **Depends on:** none
- **Done when:** `data/tuning/targets_2015_2025.json` is built from nflverse, and `python3 -m engines.tuning.compare` on a snapshot of `CAREER-2029SB` prints a report.
- **Verify:** `pytest -m "not integration" tests/test_tuning.py` passes. `node pipeline/save_reader/pc_save_reader.mjs CAREER-2029SB --out /tmp/s.json && python3 -m engines.tuning.compare /tmp/s.json` prints a score and the worst 10 stats.

### U5: Progression fit, supersim loop, `/tune` skill

- **Owns:** `engines/tuning/progression_fit.py` (new), `engines/tuning/supersim_loop.py` (new), `.claude/skills/tune/SKILL.md` (new), `tests/test_tuning_loop.py` (new)
- **Reads:** this plan §2.4, §2.5; U4's code; `engines/progression.py`; `docs/in_progress/PROGRESSION_PLAN.md` targets; `tools/mod_builder/` CLI; `docs/reference/MOD_BUILDING.md`
- **Depends on:** U4 (and U3's CLI through lazy imports only)
- **Done when:** `progression_fit` on a snapshot writes `data/tuning/progression_fit.json` and a report. `supersim_loop` runs one mocked iteration end to end and refuses without ebx.
- **Verify:** `pytest -m "not integration" tests/test_tuning_loop.py` passes. `python3 -m engines.tuning.progression_fit /tmp/s.json --seasons 3 --budget 20` writes the fit.

(`tests/test_tuning.py` belongs to U4 and `tests/test_tuning_loop.py` to U5, so no test file is shared.)

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 0 | spike P-a, P-b (inline, Claude + JR's yes for the PC) | — | Decides the driver's capture and input (R1–R3) and gives the first screens |
| 1 | U1, U2, U4 | — | No shared files. U2 mocks the driver interface. U4 is independent |
| 2 | U3, U5 | U1, U2, U4 | U3 uses U1 and U2. U5 uses U4 and calls U3 only by lazy import |

Progression tuning (U4 + U5's `progression_fit`) needs no Madden and no key, so it gives results first. Supersim tuning needs the pilot (U1–U3) and the key.

**File ownership check:**

| File | Unit |
|---|---|
| `.claude/skills/madden/SKILL.md` | U3 |
| `.claude/skills/tune/SKILL.md` | U5 |
| `docs/reference/MADDEN_PILOT.md` | U3 |
| `engines/tuning/__init__.py` | U4 |
| `engines/tuning/compare.py` | U4 |
| `engines/tuning/progression_fit.py` | U5 |
| `engines/tuning/supersim_loop.py` | U5 |
| `engines/tuning/targets.py` | U4 |
| `tests/test_madden_pilot_jobs.py` | U3 |
| `tests/test_madden_pilot_nav.py` | U2 |
| `tests/test_madden_pilot_win.py` | U1 |
| `tests/test_tuning.py` | U4 |
| `tests/test_tuning_loop.py` | U5 |
| `tools/madden_pilot/__init__.py` | U2 |
| `tools/madden_pilot/__main__.py` | U3 |
| `tools/madden_pilot/catalog.json` | U2 |
| `tools/madden_pilot/checks/__init__.py` | U3 |
| `tools/madden_pilot/checks/first_down_line.py` | U3 |
| `tools/madden_pilot/jobs.py` | U3 |
| `tools/madden_pilot/navigate.py` | U2 |
| `tools/madden_pilot/requirements-win.txt` | U1 |
| `tools/madden_pilot/screens.py` | U2 |
| `tools/madden_pilot/win.py` | U1 |
| `tools/madden_pilot/win_agent.py` | U1 |

No path appears twice. No `.gitignore` change: run output goes under `data/` (already ignored).

## 7. Spec changes when built

- §1.1 Repository Layout: `tools/madden_pilot/` (10 files incl. `checks/`), `engines/tuning/` (5 files), `.claude/skills/madden/` and `tune/`, `docs/reference/MADDEN_PILOT.md`, 5 test files; `data/pilot_runs/` and `data/tuning/` in the `data/` tree.
- §1.2 Running the System: rows for `python3 -m tools.madden_pilot <job>`, `python3 -m engines.tuning.compare`, `python3 -m engines.tuning.progression_fit`.
- The prose after the layout tree: a "Madden pilot and tuning" paragraph (the driver split between Windows and WSL, the catalog, `CAREER-TUNE-*` saves only and never while the watcher runs, the two tuning loops, and the key requirement for supersim).
- Env: `MU_WIN_PYTHON`.

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | Which tuning first? | Progression. It's offline (no Madden, no key) and can run tonight once U4 and U5 are built. Supersim follows once you've entered the key and the pilot can sim a season. |
| Q2 | Can the pilot take the PC while you're away? | Yes, but only for runs you start (or approve in that conversation). It sends ntfy when it stops or finishes, you can watch over Parsec, and moving the mouse to a screen corner aborts it. |
| Q3 | The test franchise for sims. | A copy of a franchise save renamed `CAREER-TUNE-<name>` (Madden only lists `CAREER-*` saves), with the watcher stopped during runs. Which save should be the baseline league? |
| Q4 | Real-NFL target years. | 2015–2025 regular seasons (the modern passing era), averaged, with the min–max as the acceptable band. |
| Q5 | Keyboard or virtual gamepad. | Keyboard first (nothing to install). A ViGEm virtual pad only if P-a shows menus that keyboard can't reach. That's a one-time admin install you'd do. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
