# Madden Pilot

The pilot drives Madden 27 on JR's PC so Madden can run experiments while JR is away. It launches the game with a mod pack, checks mods, changes a setting, sims a tuning franchise forward and quits, leaving the save for Project Mu to read. Plan: [`MADDEN_PILOT_AND_TUNING_PLAN.md`](../done/MADDEN_PILOT_AND_TUNING_PLAN.md). Skill: `/madden <task>` (`.claude/skills/madden/SKILL.md`).

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
- **Abort.** Moving the mouse to the top-left screen corner aborts, and so does **holding the right mouse button** (useful when a run keeps taking the focus): the driver checks both before every key and every capture, including inside a focus wait.

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
| `ADVANCE_MENU` | Opened by ADVANCE WEEK: `READY TO ADVANCE`, `FORCE ADVANCE`, then under `JUMP AHEAD`, `SIM TO MIDSEASON`. In the all-user league READY TO ADVANCE cannot run the week: one Down reaches FORCE ADVANCE (JR, 2026-11-10). | `b9.png` | Highlight FORCE ADVANCE — one verified Down at a time, waiting for the highlight to settle — and Space. NEXT WEEK (older leagues) is also allowed; jump-ahead is not. |
| `FORCE_ADVANCE_CONFIRM` | `ATTENTION / You have selected to skip ahead multiple weeks. Are you sure you wish to continue?` with `SIM AHEAD` and `CANCEL` (CANCEL selected). | 2026-11-10 (P2-01) | Up then Space on SIM AHEAD. |
| `CHOOSE_TEAM` | `OFFLINE > NFL PRESEASON ROSTER > CHOOSE TEAM > CUSTOMIZE`: the in-franchise create-new-character team grid (4×8). | `look1.png` | Nothing is chosen here. E (Random Team) is forbidden. |
| `FRANCHISE_LIST` | `CURRENT FRANCHISES`: save tiles without the `CAREER-` prefix (`TUNE-SPIKE`, `ALLUSER`, …), newest first, then `CREATE NEW LEAGUE`. Left/Right move. The highlight is a white border. | `b5.png` | Highlight a `TUNE-*` tile and Space to load it. No other tile is ever confirmed. Q (Delete) is forbidden. |
| `HUB_OTHER` | The MANAGE ROSTER, COACH CENTRAL, LEAGUE and OPTIONS hub tabs, matched on `GAMEDAY` plus the open page's own words (`ADJUST LINEUP`, `DEPTH CHART`, `TEAM SALARY`, `STATS & AWARDS`, `LEAGUE SETTINGS`, `USER SETTINGS`). Anchoring on the tab row's `COACH CENTRAL` failed whenever OCR garbled it (`COACHCENTUL`), and the page then matched HUB_GAMEDAY and the pilot walked onto another tab (2026-11-10). | — | Z back one tab toward GAMEDAY. |
| `HUB_GAMEDAY` | The GAMEDAY tab. Buttons from left to right: `WEEKLY RECAP` (after a played week), `ADVANCE WEEK`, `PLAY SEASON GAME`. The order changes between weeks. It is matched on `ADVANCE WEEK` or `PLAY SEASON GAME`, never on `MATCHUP`: the News Center ticker says "matchup" on every hub tab, and a blank Weekly Hub frame with the ticker on it classified here (2026-11-10), which sent the pilot one tab too far. Its header carries the week (`2026. WEEK N`) or the year-less postseason phase (`WILDCARD WEEK`). | `b8.png`, `e1.png` | Space on ADVANCE WEEK only, with the white border read on it first. Backspace opens the quit dialog. |
| `HUB_WEEKLY` | The WEEKLY HUB tab: the header `2026. WEEK 1 / VS BEARS (0-0-0)` and optional cards (matchup, upgrades, staff, roster decisions). In the postseason and offseason it keeps the tab row but usually draws no cards, so it is matched on the phase word instead — `WILDCARD`, `DIVISIONAL`, `CONFERENCE`, `PRO BOWL`, `SUPER BOWL`, `STAFF`, `RE-SIGN`, `FREE AGENCY`, `COMBINE`, `PRO DAY`, `DRAFT`, `UDFA`, `CUTDOWN`, `PRESEASON` (the highlighted `WEEKLY HUB` tab itself often OCRs as nothing). It never shows the save name. | `b1.png`, `d1.png` | No Space ever (cards are never opened). Z to GAMEDAY. Tab for a savepoint. Q is forbidden here; it is allowed only on `HUB_WEEKLY_DRAFT_CARD`. The pilot reads the week from the header here. |
| `ADVANCE_LOCKED` | The GAMEDAY tab with `ADVANCE WEEK LOCKED / The Free Agent Frenzy event has 10 hours remaining` over the standings, one `OK` button, and no ADVANCE WEEK to press. | 2026-11-10 (P2-02) | Space on `OK`, then Z to the LIVE EVENT tab: the pilot's route to ADVANCE WEEK goes through the live event. |
| `FREE_AGENCY_ENDED` | `FREE AGENT FRENZY ENDED / … You can no longer negotiate with players` with one `OK`, drawn over the live-event page after the event is simulated. | 2026-11-10 (P2-02) | Space on `OK`, then C back to GAMEDAY. |
| `LIVE_EVENT_FRENZY_MODAL` | Holding **Escape** on the LIVE EVENT tab opens `ADVANCE FREE AGENT FRENZY / Currently on hour 1…` with `ADVANCE TO NEXT HOUR` (selected), `SIMULATE TO END OF FRENZY` and `CANCEL`. The same modal opens for the post-draft UDFA event. | 2026-11-10 (P2-02, JR) | Down onto SIMULATE TO END OF FRENZY and Space — never ADVANCE TO NEXT HOUR. Windows OCR drops the `END` (`SIMULATE TO OF FRENZY`), so the label is matched on `SIMULATE TO`. |
| `LIVE_EVENT_FRENZY` | The LIVE EVENT tab, one tab left of GAMEDAY: a free-agent contract board (`SALARY OVERVIEW`, `DESIRED YEARS`, `NEGOTIATE CONTRACT`). A live event takes the focus on load, so a franchise opened during one lands here rather than on GAMEDAY. | 2026-11-10 (P2-02) | No Space ever (every contract is Mu's). Hold Escape to open `LIVE_EVENT_FRENZY_MODAL`. |
| `DRAFT_HUB` | Draft week: the GAMEDAY and WEEKLY HUB tabs are replaced by a `DRAFT` tab next to MANAGE ROSTER, holding the NFL DRAFT logo and one `START THE NFL DRAFT` button (JR). | 2026-11-10 (P2-02) | Space on START THE NFL DRAFT. |
| `DRAFT_LAUNCH` | After START THE NFL DRAFT: `START DRAFTING` (selected) and `SIMULATE DRAFT`. Simming the draft is the only way past the stage. | 2026-11-10 (P2-02, JR) | One Down, prove the border left START DRAFTING, then Space on SIMULATE DRAFT — the whole draft runs and the game lands on the recap stop by itself (no ADVANCE WEEK). OCR reads the button as `SIMULATEDRAFT`, so it is found by position, not by name. |
| `HUB_WEEKLY_DRAFT_CARD` | Week 1's Weekly Hub with the draft-class card selected: `WHICH ROOKIES WILL WE SEE IN APRIL'S DRAFT?`, footer HOLD TO DELEGATE. Madden refuses to advance the week until it is delegated. | 2026-11-10 (P2-01) | Highlight the card with Left/Right (it is not always selected; the keys wrap) and **hold Q** about 1.2 s. Space is never pressed here. |
| `TITLE_SCREEN` | EA SPORTS / MADDEN NFL title with PRESS ANY BUTTON. The button sits below the captured client area, so the screen is matched on the MADDEN logo with every menu word excluded. | 2026-11-10 (P2-01) | One Space on PRESS ANY BUTTON, through `confirm_on`. |
| `FRANCHISE_LANDING` | The Franchise mode page: `CONTINUE` (selected; resumes the last-played save), `CREATE / LOAD`, `SEARCH`. | 2026-11-10 (P2-01) | Right to `CREATE / LOAD` then Space, which opens CURRENT FRANCHISES. CONTINUE is never confirmed. |
| `MAIN_MENU` | Madden's PC main menu mode rail. Its mode page (FRANCHISE) is `FRANCHISE_LANDING`. | G0 2026-11-01 | Only Franchise may be confirmed; other modes are not authorized. |

The spike folder also holds frames that aren't separate catalog screens: `b2`, `b4`, `b7`, `c2`, `c3`, `c5`, `c6`, `look2`–`look4` and `nav`.

## 4. Routes

Arrows are the catalog's exits. `goto(target)` takes the shortest route and plans again after every hop, so an interrupt (Coach Progress) or a recap is handled on the way.

- **Load a tuning franchise:** FRANCHISE_LIST → the `TUNE-<x>` tile → Space → PROGRESSION_WARNING → Proceed → (loading, about 25 s) → HUB_WEEKLY. The list is ordered by **the save's own date** (not the file's mtime — a copy changes only that) and autosaves are tiles of their own. The pilot reads which tile carries the white border, picks the direction from the two labels' x positions when both are on screen, and presses one arrow at a time, looking after each, until the border is on the target; a press that does not move the border is the list's edge, and it then goes the other way. Jumping by the saves folder's order is wrong (2026-11-10). **It then checks the load:** the hub's own header must name the same year (and week, in a regular season) as the save file it meant to open, else it stops rather than sim the wrong season (2026-11-10: a lost arrow press loaded the previous save, 2028 week 1, under the name of a 2026 clone).
- **Sim one week:** HUB_WEEKLY → Z → HUB_GAMEDAY → highlight ADVANCE WEEK (border checked) → Space → ADVANCE_MENU → NEXT WEEK → Space → (sim, under 20 s) → COACH_PROGRESS ×3 (Space each) → HUB_WEEKLY (read `2026. WEEK N`).
- **Savepoint:** HUB_WEEKLY → Tab → (back to the hub).
- **Quit a franchise:** hub → Backspace → QUIT_CONFIRM → Up → Space on YES → FRANCHISE_LIST. This writes `<save>-AUTOSAVE`, so it is `TUNE-*` only unless JR allows it.
- **Out of a recap:** WEEKLY_RECAP → Backspace (repeated) → HUB_GAMEDAY.
- **Out of a roster prompt:** ROSTER_DECISION_PROMPT → Backspace → HUB_WEEKLY.
- **Skip a live event** (free agency, and the UDFA event after the draft): the advance is locked, so ADVANCE_LOCKED →
  Space on OK → Z to LIVE_EVENT_FRENZY → hold Escape → LIVE_EVENT_FRENZY_MODAL → Down onto SIMULATE TO END OF FRENZY →
  Space → FREE_AGENCY_ENDED → Space on OK → C to GAMEDAY. The route to ADVANCE_MENU goes through the event, so the
  pilot takes it whenever the week is locked.
- **Draft week:** DRAFT_HUB → Space on START THE NFL DRAFT → DRAFT_LAUNCH → Down → Space on SIMULATE DRAFT. The
  draft sim is long and lands on the recap stop; Mu has already written its picks into the save.

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
| `clone-save [--from CAREER-WEEK1] --to CAREER-TUNE-<name>` | Copies the save file inside `MADDEN_SAVES_DIR` (from the environment or `.env`; default `/mnt/c/Users/jrere/Documents/Madden NFL 27/saves/`). The copy goes through a temporary file that isn't named `CAREER-*` and gets a fresh mtime, so it lists first; it is then capacity-pruned (`pipeline/save_reader/prune_capacity.mjs`, `Highlight` 3,200 rows → 512, freeing 596,736 bytes of the 20 MiB unpacked window — the result carries `pruned` and `prune`, and a refused prune keeps the plain copy with a printed warning), so it is listed first. An existing `CAREER-TUNE-*` file is replaced. | Refuses a destination that isn't `CAREER-TUNE-<name>`, names with path characters, a missing source, and source = destination. It never writes any other file. |
| `sim-season --save CAREER-TUNE-<name> [--franchise TUNE-<loaded>] [--to "week N" \| regular-season-end \| playoffs-end] [--no-quit]` | Loads the tile, reads the week, sims one week at a time until the target, creates a savepoint (Tab), and quits to the franchise list. `result.json` holds the start and end week, the weeks simmed, the minutes, and whether the save and `-AUTOSAVE` were written (by mtime). `week N` means the header reads WEEK N (1–18). `regular-season-end` runs until the header leaves `WEEK N`. `playoffs-end` runs until an offseason header. Post-season screens aren't catalogued yet, so it stops as Unknown there. A 30-week cap stops runaway loops. | Refuses a save that isn't `CAREER-TUNE-*`, a running `pipeline.watch_save` (checked with `ps -eo args \| grep "[p]ipeline[.]watch_save"`; `pgrep -f` matches itself), and a save file missing from the saves folder. Stops as Forbidden if a franchise is already open and isn't known to be `TUNE-*` (it won't quit it). Stops as Stuck if a week doesn't advance or the header can't be read. |
| `advance --save CAREER-TUNE-<name> [--franchise TUNE-<loaded>] [--no-quit]` | Advances **exactly one stop** (one regular-season week, one playoff round or one offseason stage), reads the stop before and after, copies Madden's autosave over the named save, and quits to the franchise list so the league office can write the file (P2-03's stage loop; `engines/tuning/season_loop.py` drives it). `result.json` holds `from`/`to` stops, `from_screen`/`to_screen`, and whether the save was rewritten. In the offseason the hub often carries no phase word, so the stop is read from the save's own `week_index` and named from Madden's eight stops (`STAFF`, `RE-SIGN`, `FA_PREP`, `FREE_AGENCY`, `COMBINE`, `PRO_DAY`, `DRAFT`, `RECAP_UDFA`); an advance that leaves that stop unchanged is a Stuck, not a success. | Same refusals as `sim-season`. Stops Unknown with a screenshot on a screen it has no advance route for. |
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
- `MAIN_MENU` Franchise selection observed during G0 (2026-11-01); catalog now permits only Franchise confirmation. Other main-menu modes remain uncatalogued;
- what Tab (savepoint) shows;
- the preseason/cutdown stops (PreSeason week 0 and week 3+) and whether the cutdown has its own advance;
- a pre-snap screen for `first_down_line`;
- the settings screens for `set-setting`.

G0 presentation research uses a separate local g0/research_catalog.json permitting Play Season Game only after loaded CAREER-TUNE-* identity is verified. The normal pilot catalog retains its Advance Week-only guard. JR confirmed TUNE-PRESENTATION-G0 during the live run. This does not authorize playing production franchises.
