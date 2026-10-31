# Madden Pilot

The pilot drives Madden 27 on JR's PC so Madden can run experiments while JR is away. It launches the game with a mod pack, checks mods, changes a setting, sims a tuning franchise forward and quits, leaving the save for Project Mu to read. Plan: [`MADDEN_PILOT_AND_TUNING_PLAN.md`](../in_progress/MADDEN_PILOT_AND_TUNING_PLAN.md). Skill: `/madden <task>` (`.claude/skills/madden/SKILL.md`).

## 1. How it works

| Layer | File | Runs in | Does |
|---|---|---|---|
| Driver | `tools/madden_pilot/win_agent.py` | Windows Python (`C:\Madden27Modding\MuPilot\venv`, `MU_WIN_PYTHON`) | `windows`, `focus`, `look --ocr` (captures Madden's client area with mss and reads it with Windows OCR), `key` (hardware scan codes through SendInput), `run`, `kill` (Madden27.exe and MMCModManager.exe only). One JSON line per command. |
| Wrapper | `tools/madden_pilot/win.py` | WSL | `call(cmd)` runs the driver and converts paths with `wslpath -w`. |
| Catalog | `tools/madden_pilot/catalog.json` | WSL | Every screen seen so far: its OCR test, its exits, which labels Space may accept, and its safety flags. The `doc` array at the top explains each field. |
| Classifier | `tools/madden_pilot/screens.py` | WSL | `classify(frame)` returns the first screen in catalog order whose words match, else `UNKNOWN`. `border_highlight` finds Madden's white selection outline in the screenshot. |
| Navigation | `tools/madden_pilot/navigate.py` | WSL | `Pilot`: `look`, `press`, `wait_for`, `goto` (shortest route over the exits, re-planned after every hop), `confirm_on` (Space only on allowed labels, with the highlight checked), and the stops. |
| Jobs and CLI | `tools/madden_pilot/jobs.py`, `__main__.py`, `checks/` | WSL | The jobs below, `python3 -m tools.madden_pilot <job>`, and the `--dry-run` fake Madden. |

Facts from the spikes (2026-10-30):
- **Not elevated.** Madden runs as the normal user, so SendInput reaches it.
- **The window.** It is called `Madden NFL 27`: a 2560×1440 borderless popup at (0,0).
- **It minimizes whenever it loses focus.** The driver refocuses it before every look and key, and refuses to press a key when it can't. During a run, Claude must be minimized or on another monitor.
- **OCR.** Windows OCR reads labels and headings exactly. It garbles stylised numbers (`OVR` reads as `WR`, `$10.9M` as `SIO.9M`), so screens are classified on labels. Stats always come from the save file, never from OCR.
- **Abort.** Moving the mouse to the top-left screen corner aborts: the driver checks it before every key.

## 2. Keys (PC, from the on-screen hints)

| Catalog name | Key | Meaning |
|---|---|---|
| `select` | Space | Select, accept, continue. Only ever pressed through `confirm_on`. |
| `back` | Backspace | Back. On the hub it opens the quit dialog. |
| `tab_prev` / `tab_next` | Z / C | Previous / next top tab of the franchise hub. |
| `savepoint` | Tab | Create Savepoint (hint on GAMEDAY and the Weekly Hub). |
| — | arrows | Move the highlight. One press is one step while Madden has focus. |
| — | Q | **Delete** on the franchise list, Hold to Delegate on the Weekly Hub. Forbidden on both. |
| — | E | Random Team on the team choice. Forbidden. |
| — | T | Profile. Not used. |

Esc backs out of nothing seen so far.

## 3. Screens in the catalog

Screenshots are in `C:\Madden27Modding\MuPilot\spike\` (WSL: `/mnt/c/Madden27Modding/MuPilot/spike/`). Each has a `_small.png` copy. The table is in catalog (classification) order.

| Screen | What it is | Spike screenshots | What the pilot may do |
|---|---|---|---|
| `DELETE_CONFIRM` | Any dialog about deleting a franchise. Not seen yet; the words are guesses. | — | Nothing. **Forbidden**: the run stops on sight. |
| `QUIT_CONFIRM` | `WARNING / Are you sure you want to quit? / YES / NO`, opened by Backspace on the hub. NO is highlighted on arrival. YES writes `<save>-AUTOSAVE` and returns to the franchise list. | `b3.png` | Up then Space on YES, **only** if the loaded franchise is `TUNE-*` or JR allowed it (`quit_guard`). NO is always allowed. |
| `PROGRESSION_WARNING` | `PROGRESSION WARNING / Unable to connect to EA servers… / PROCEED WITHOUT PROGRESSION / CANCEL`, drawn over the franchise list when a franchise loads offline. Proceed is highlighted. The franchise then loads in about 25 s. | `b6.png`, `b6_crop.png` | Space on Proceed (or Cancel). |
| `ROSTER_DECISION_PROMPT` | The prompt behind a Weekly Hub roster card (`PLACE ON INJURED RESERVE / KEEP ON ACTIVE ROSTER`, `ACTIVATE FROM IR / KEEP ON IR`). In the spike one blind Space put R. Walker on IR on the clone. | `c4.png` | Never Space, and no policy can lift that (`never_select`). Never a route target. If the pilot lands here, it backs out. |
| `COACH_PROGRESS` | After a simmed week: three tabs, `COACH PROGRESS`, `LOADOUT PROGRESS` and `UNLOCKS`, with CONTINUE. The only interrupt after NEXT WEEK. | `b10.png`, `c1.png` | Space (CONTINUE) through each tab (`repeat`), then the Weekly Hub. **It can be turned off:** a team setting disables the ability recap, and then Next Week goes straight to the next week's Weekly Hub (JR, 2026-10-31). Turn it off in the baseline save. |
| `WEEKLY_RECAP` | The week recap video. It never plays on its own. In the spike a Space on GAMEDAY's recap tile started it. | `e2.png`–`e5.png`, `f1.png`–`f6.png`, `g1.png`–`g6.png`, `h1.png` | Recovery only: Backspace steps out one segment at a time, up to 15 (`wait_or_back`). The frames between are UNKNOWN. |
| `ADVANCE_MENU` | Opened by ADVANCE WEEK on GAMEDAY: `NEXT WEEK` and, under `JUMP AHEAD`, `SIM TO MIDSEASON`. NEXT WEEK sims the whole week, including the user's game, in under 20 s. | `b9.png` | Space on NEXT WEEK only. Jump-ahead options aren't allowed yet. |
| `CHOOSE_TEAM` | `OFFLINE > NFL PRESEASON ROSTER > CHOOSE TEAM > CUSTOMIZE`: the in-franchise create-new-character team grid (4×8). | `look1.png` | Nothing is chosen here. E (Random Team) is forbidden. |
| `FRANCHISE_LIST` | `CURRENT FRANCHISES`: save tiles without the `CAREER-` prefix (`TUNE-SPIKE`, `ALLUSER`, …), newest first, then `CREATE NEW LEAGUE`. Left/Right move. The highlight is a white border. | `b5.png` | Highlight a `TUNE-*` tile and Space to load it. No other tile is ever confirmed. Q (Delete) is forbidden. |
| `HUB_OTHER` | The MANAGE ROSTER, COACH CENTRAL, LEAGUE and OPTIONS hub tabs. Words come from madden_capture's PS5 mapping, not the spike (`todo`). | — | Z back toward GAMEDAY. |
| `HUB_GAMEDAY` | The GAMEDAY tab (`MATCHUP`). Buttons from left to right: `WEEKLY RECAP` (after a played week), `ADVANCE WEEK`, `PLAY SEASON GAME`. The order changes between weeks. It shows no week header. | `b8.png`, `e1.png` | Space on ADVANCE WEEK only, with the white border read on it first. Backspace opens the quit dialog. |
| `HUB_WEEKLY` | The WEEKLY HUB tab: the header `2026. WEEK 1 / VS BEARS (0-0-0)` and optional cards (matchup, upgrades, staff, roster decisions). It never shows the save name. | `b1.png`, `d1.png` | No Space ever (cards are never opened). Z to GAMEDAY. Tab for a savepoint. Q (Delegate) forbidden. The pilot reads the week from the header here. |
| `MAIN_MENU` | Madden's PC main menu. **Not seen yet**: the words are guesses (`todo`), and it has no exits. | — | Nothing yet. `launch` waits for it, and stops as Unknown with the screenshot if the real menu doesn't match. |

The spike folder also holds frames that aren't separate catalog screens: `b2`, `b4`, `b7`, `c2`, `c3`, `c5`, `c6`, `look2`–`look4` and `nav`.

## 4. Routes

Arrows are the catalog's exits. `goto(target)` takes the shortest route and plans again after every hop, so an interrupt (Coach Progress) or a recap is handled on the way.

- **Load a tuning franchise:** FRANCHISE_LIST → highlight `TUNE-<x>` → Space → PROGRESSION_WARNING → Proceed → (loading, about 25 s) → HUB_WEEKLY.
- **Sim one week:** HUB_WEEKLY → Z → HUB_GAMEDAY → highlight ADVANCE WEEK (border checked) → Space → ADVANCE_MENU → NEXT WEEK → Space → (sim, under 20 s) → COACH_PROGRESS ×3 (Space each) → HUB_WEEKLY (read `2026. WEEK N`).
- **Savepoint:** HUB_WEEKLY → Tab → (back to the hub).
- **Quit a franchise:** hub → Backspace → QUIT_CONFIRM → Up → Space on YES → FRANCHISE_LIST. This writes `<save>-AUTOSAVE`, so it is `TUNE-*` only unless JR allows it.
- **Out of a recap:** WEEKLY_RECAP → Backspace (repeated) → HUB_GAMEDAY.
- **Out of a roster prompt:** ROSTER_DECISION_PROMPT → Backspace → HUB_WEEKLY.

Not mapped yet: the main menu, the way from the main menu to the franchise list, settings screens, the post-season, and what Tab shows.

## 5. Jobs

`python3 -m tools.madden_pilot <job> [--run-dir DIR] [--dry-run]`. Each run gets `data/pilot_runs/<YYYY-MM-DD_HHMMSS>_<job>/` with:
- `steps/NNNN_<screen>.png` and `.json`: every look, with its OCR lines;
- `result.json`;
- `stop.json` on a stop.

Every job ends with one ntfy summary through `pipeline.notify`, on finish, refusal or stop. A stop also sends its own alert. Exit code 0 means ok or pass, 2 means unknown, 1 means refused or stopped.

| Job | Does | Refuses / stops |
|---|---|---|
| `launch [--pack "Mu Base"]` | Starts Madden with `cmd.exe /c start "" "<MMCModManager.exe>" -launch <pack>`, using `pipeline.game_launch`'s `command` and `manager_exe` helpers (its own `launch()` picks a per-game pack from the database, so it isn't used). It skips the start if Madden already runs. Then it waits for the `Madden NFL 27` window, and for MAIN_MENU, FRANCHISE_LIST or a hub. | Stops as Unknown with the screenshot if the screen stays unrecognised for 300 s. MAIN_MENU is still todo, so expect this on the first live run. |
| `clone-save [--from CAREER-WEEK1] --to CAREER-TUNE-<name>` | Copies the save file inside `MADDEN_SAVES_DIR` (from the environment or `.env`; default `/mnt/c/Users/jrere/Documents/Madden NFL 27/saves/`). The copy goes through a temporary file that isn't named `CAREER-*` and gets a fresh mtime, so it is listed first. An existing `CAREER-TUNE-*` file is replaced. | Refuses a destination that isn't `CAREER-TUNE-<name>`, names with path characters, a missing source, and source = destination. It never writes any other file. |
| `sim-season --save CAREER-TUNE-<name> [--to "week N" \| regular-season-end \| playoffs-end] [--no-quit]` | Loads the tile, reads the week, sims one week at a time until the target, creates a savepoint (Tab), and quits to the franchise list. `result.json` holds the start and end week, the weeks simmed, the minutes, and whether the save and `-AUTOSAVE` were written (by mtime). `week N` means the header reads WEEK N (1–18). `regular-season-end` runs until the header leaves `WEEK N`. `playoffs-end` runs until an offseason header. Post-season screens aren't catalogued yet, so it stops as Unknown there. A 30-week cap stops runaway loops. | Refuses a save that isn't `CAREER-TUNE-*`, a running `pipeline.watch_save` (checked with `ps -eo args \| grep "[p]ipeline[.]watch_save"`; `pgrep -f` matches itself), and a save file missing from the saves folder. Stops as Forbidden if a franchise is already open and isn't known to be `TUNE-*` (it won't quit it). Stops as Stuck if a week doesn't advance or the header can't be read. |
| `set-setting --path "A>B>Setting" --value V` | Walks the path by OCR labels. Each hop needs a catalogued screen with the label in its `confirm_ok`, and the highlight moved onto the label. Then it presses Right on the last label until its row reads V, and goes back to where it started. | Stops as Unknown on any screen or label that isn't catalogued. No settings screen is catalogued yet. |
| `verify-mods --check <name>` | Runs `tools/madden_pilot/checks/<name>.py`'s `run(pilot)` and returns pass, fail or unknown. `first_down_line` is a stub that returns unknown until a pre-snap screen and its route are catalogued (its docstring says what it needs). | Refuses names that aren't plain module names. |
| `quit [--franchise TUNE-<x>] [--allow-quit]` | Hub → QUIT_CONFIRM → YES → franchise list. Does nothing on the franchise list. | Stops as Forbidden unless the franchise is `TUNE-*` (the hub doesn't show it, so pass `--franchise`) or `--allow-quit` (after JR says yes). |

`--dry-run` replaces the Windows driver with `ScriptedMadden` (in `__main__.py`), a fake Madden built from the catalog:
- each screen shows its `all` words, its first `any` word and its options;
- each exit's last step leads to its target;
- loading a tile, Proceed, NEXT WEEK (three Coach Progress tabs) and Tab are scripted;
- after week 18 the header reads WILD CARD, and the next NEXT WEEK opens an uncatalogued screen.

It prints the route summary and every look and key. It launches, copies, presses and sends nothing, and it warns if the watcher is running.

Not done automatically: killing Madden. `kill` exists in the driver, but no job calls it. A save half written by a killed Madden is worse than a stuck run.

## 6. Safety rules

- **Space** is only pressed through `confirm_on`, only on labels in the screen's `confirm_ok`, and, where the highlight can be read, only with the highlight on that label.
- **Roster cards and decisions** are never selected (`never_select`): Project Mu makes roster decisions through the save writer.
- **Forbidden screens** (delete, and later online, store, sign-in) stop the run on sight. `forbidden_keys` (Q, E) are never pressed.
- **Saves.** Only `CAREER-TUNE-*` saves are loaded, simmed or quit without JR, and never while `pipeline.watch_save` runs. A clone keeps the original's LeagueID, so the watcher would ingest it into the real franchise database.
- **Asking first.** Every run asks JR before taking the PC, and says how long it takes. Quitting a non-TUNE franchise asks JR first.
- **Never:** online play, purchases, deleting anything.

## 7. Extending the catalog

When a job stops as Unknown:

1. Open `stop.json` and view the screenshot it names (the Read tool shows PNGs). The `steps/NNNN_UNKNOWN.json` next to it holds the OCR lines and words.
2. Add the screen to `tools/madden_pilot/catalog.json`:
   - **Name and words:** an upper-case name; `all`, `any` and `none` taken from OCR **labels** (never numbers). Place it before any screen it shares words with, because the first match wins.
   - **`exits`:** `{TARGET: [[key, n] | ["highlight", LABEL] | ["confirm", LABEL]]}`. Every confirm label must be in `confirm_ok` (the nav tests check this).
   - **`confirm_ok`:** only labels that are safe to accept. A decision gets `[]` with `forbidden_confirm` or `never_select`.
   - **Highlight:** `options`, `default`, `nav` (vertical or horizontal) and `highlight` (`border` when Madden draws a white outline, `ocr_order` when it can't be read).
   - **Flags:**
     - `repeat`: a multi-page screen that looks the same page to page;
     - `wait_or_back`: a video;
     - `forbidden` or `forbidden_keys`;
     - `no_route`: never a target;
     - `todo`: a guess.
   - **`note`:** where it was seen (run dir and step).
3. Add a row to §3 here, and a route to §4 if it adds one.
4. Run `pytest -m "not integration" tests/test_madden_pilot_nav.py tests/test_madden_pilot_jobs.py`, then `--dry-run` the job again.
5. Retry the job, asking JR again for the PC.

Next screens to catalogue:
- `MAIN_MENU`, and the way from it to `FRANCHISE_LIST`;
- what Tab (savepoint) shows;
- the post-season headers and screens (for `playoffs-end`);
- a pre-snap screen for `first_down_line`;
- the settings screens for `set-setting`.
