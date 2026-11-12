# Project Mu: Visual Mod Check Plan

**Status:** approved by JR (2026-11-12), with his answers folded in below. Built and on main (2026-11-12). Wave 0 spike S-1 ran the same day, status partial — see §2.5a; the halftime show itself was never seen, so that segment is written for and not yet measured. **M-0689/F7 audit fixes (2026-11-12):** the halftime and static-super-sim routes are now reachable and recording is synchronized on the job's own ready marker, fresh run directories and launch-verified pack provenance; the show itself is still unmeasured live and the plan stays partial until a cloned-save run produces footage of it. F7's measured pause-menu mismatch is fixed: navigation reads the visible rows and the actual selected row, and stops before Space when it cannot; the other `ocr_order` screens' selection evidence remains live-unmeasured.
**Builds on:** PROJECT_SPEC §1.1 (repository layout), [`MUSE_EMBER_ROTATION_PLAN.md`](../done/MUSE_EMBER_ROTATION_PLAN.md) (the Muse route and the computer-use rules), [`MADDEN_PILOT.md`](../reference/MADDEN_PILOT.md) §5 (jobs) and §6 (safety), [`MADDEN_SPIKE_AGENT.md`](../reference/MADDEN_SPIKE_AGENT.md) §7 (asset checks, checkpoints, clips), [`LOCAL_VISION_LATENCY.md`](../reference/LOCAL_VISION_LATENCY.md) (what a local model measured at), `docs/reference/LOCAL_VISION_HOST.md` (the Mac host, being set up by `mac-vision-setup`).

**Goal:** replace the manual Codex loop JR uses to test visual mods. One command loads a `CAREER-TUNE-*` save, records the game-start and halftime segments, and answers *"did this mod's assets appear?"* with a number per asset plus a short written summary — deterministic pixel checks for the verdict, a vision model (hosted on JR's always-on M1 Mac, never on Madden's GPU) for the description and for unknown screens. JR stops describing frames by hand and starts reading pass/fail lines.

**JR's answers (2026-11-12):**
- **D1 — quitting a game does write `-AUTOSAVE`** (his word; R1 is therefore a fact, not a risk). The run must test a clone of its own, never JR's working tuning save.
- **D2 — the halftime route is a supersim recipe, not a played game:** supersim to the end of the first quarter, then simulate the second quarter **by play**, then resume the real game for the last play of the half with under 20 s on the clock, and the halftime show plays.
- **D3 — the halftime show exists only in a real game** (a fully supersimmed game never shows it), so D2's recipe is the only way to see it.
- **D4 — Q1–Q4 approved as recommended**, with Q2 answered by D2: `watch-game` drives the recipe itself, because a hand-started game cannot be counted on to stop at the right moment.

**Delivers:**
- `tools/mod_check/` — a new package: segment plans, the check runner, the report, a CLI (`python3 -m tools.mod_check run|capture`)
- `tools.madden_pilot` — one new guarded job, `watch-game`, that starts a game on a TUNE save, holds still while a segment is recorded, and leaves the save as it found it
- `docs/reference/MOD_CHECK.md` — how to define a mod's check, capture its references, run it and read the report
- Reference check specs under `data/mod_checks/<slug>/` (git-ignored), one `pass`/`fail` pair and region per asset JR wants to verify

**As built (deviations from the text above):**
- **U1** reads `win_agent`'s record caps from that file's source with `ast`, because `win_agent.py` imports `win32api` and cannot be imported under WSL; the caps still have one source of truth.
- A fixed-wait segment is written `"cue": "supersim_menu"` with no `after`, so a plan cannot silently time any other moment. `preroll_s` is in the plan and the report.
- `report.json`'s `save` names the **clone** the run scored (the source save is in the runner's return dict and the CLI prints it), and a run with no checked assets is `unknown`, not a vacuous `pass`.
- The vision description is one call per segment on its contact sheet, stored as `summary`; the current vision prompt asks for one sentence, so the "two sentences" wording above is as close as the API allows.
- **Clone names carry a letter, not a digit** (`CAREER-TUNE-CHKA`, `CHKB`, …): spike S-1 measured that the pilot's tile OCR reads `1` as `I`, so `CAREER-TUNE-CHK1` was unfindable.
- **`tools/madden_pilot/__main__.py` belongs to U2**, not to no unit: `watch-game` needs a CLI entry.
- **`tools/madden_pilot/spike.py` gained `allow_space`** (JR, 2026-11-12), outside every unit's files, because an in-game frame has no OCR label to confirm Space against. It is in the spec's spike paragraph.

- **U2** keeps `HUB_GAMEDAY.confirm_ok` at `["ADVANCE WEEK"]`: `PLAY SEASON GAME` goes in through the pilot's own policy for the run, as the spike did. `PAUSE_MENU` confirms RESUME and SUPER only, so `QUIT GAME` is policy-only too.
- **Leaving a live game (JR, 2026-11-12):** a `CAREER-TUNE-*` clone may have its game quit, so `watch-game` presses the pause menu's `QUIT GAME` and its confirmation dialog instead of leaving Madden mid-game. The dialog (`QUIT_GAME_CONFIRM`) was never seen in S-1: its words are a guess until a live run reads them, a cancel-shaped label is never pressed, and any other landing stops the run.
- **The cue names and the job's stops differ:** a segment cue says *kickoff*, *halftime_start* or *supersim_menu*; `watch-game --to` says `kickoff`, `halftime` or `supersim`. `tools/mod_check`'s driver maps between them. No cue is a blind fixed wait: every route writes `ready.json` immediately before its hold, and the driver accepts only its own process's marker while that process is alive and the hold still covers the recording.
- **Run directories are unique per invocation** (`-2`, `-3` on a same-second collision; an explicit `--run-dir` that already holds anything is refused), because the pilot numbers step PNGs per process (S-1 lost most of its frames that way). Old frames are never a fallback for an empty recording.

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | `tools/madden_pilot/assets.py:169` `check_frame(png, spec)` returns `{"status", "measures", "why"}` and takes `region` plus either `colour` (`hue`, `pass_at`, `min_sat`, `min_val`) or `reference` (`pass`, `fail`, `margin`, `region`), or both. | The verdict layer already exists, is deterministic, and needs no model. The plan supplies *frames* and *specs*, not new image math. |
| S2 | `tools/madden_pilot/checks/asset_probe.py:1-16` runs one spec via `verify-mods --check asset_probe`, spec path in `MU_ASSET_PROBE`, and exposes `run_frame(png, spec)` for saved frames. | A per-mod check is already a solved call on a saved frame; what is missing is the run that produces the frames and aggregates them. |
| S3 | `tools/madden_pilot/spike.py:183-204` validates checkpoints (`name`, `checks`, `record_s` 1..`MAX_RECORD_S`); `spike.py:685-713` records a clip through `win_agent record` (`--seconds`, `--fps`, `--width`) and builds a GIF and contact sheet. | Recording is built, but only inside a spike session with a brief. JR's loop needs it outside a spike, repeatedly, on his own saves. |
| S4 | `tools/madden_pilot/vision.py:198-202` posts frames to `engines.llm_provider.get_ollama_base_url()`, which reads `OLLAMA_HOST` (`engines/llm_provider.py:78-86`). | The Mac host is a drop-in: `OLLAMA_HOST=http://<mac>:11434`. No code change needed to use it. |
| S5 | `tools/madden_pilot/vision_bench.py` scores kind/options/selected/layout accuracy, median and p95 seconds, and writes `bench.md` + `vision_choice.json`. Measured 2026-11-12: `qwen2.5vl:7b` scored 0.49 kind / 0.17 highlighted at 1.48 s median (`LOCAL_VISION_LATENCY.md` §2). | The model is a *describer*, not a selector. Nothing in this plan may gate a pass/fail on a model answer. |
| S6 | `tools/madden_pilot/jobs.py:413` `launch`, `sim_season` at ~:700, `quit_franchise` — all TUNE-only; the catalog's `HUB_GAMEDAY` permits **Space on ADVANCE WEEK only** and not on `PLAY SEASON GAME` (`MADDEN_PILOT.md` §5). | Starting a game is deliberately forbidden today. A visual mod check needs it, so it needs its own guarded job and JR's explicit rule, not a widened `HUB_GAMEDAY`. |
| S7 | `docs/reference/MADDEN_SPIKE_AGENT.md` §7: checks are named specs run by `spike-check`; checkpoints capture a frame and record a clip into `state.json["checkpoints"]`. | The report shape and the "one spec per asset" idea can follow the spike's, so a spike and a mod check read the same way. |
| S8 | `grep -ril "asset check\|mod check\|visual mod" docs/not_started docs/in_progress` → nothing. | No existing plan covers this; nothing to extend. |
| S9 | `.gitignore:13` ignores `data/*`; `data/mod_checks/` will be git-ignored like `data/spikes/`. | Reference crops and runs stay out of git, as the spike frames do. |

## 1. Scope

**In:**
- A repeatable run that takes a TUNE save, records named segments (game start, halftime), and returns pass/fail per checked asset with the numbers behind it.
- A one-time `capture` step that turns one known-good run into a mod's `pass`/`fail` reference pairs and regions.
- A guarded `watch-game` pilot job, because starting a game is the one thing the pilot refuses today.
- The model's role: describe the segment and name unknown screens; never decide pass/fail.
- The Mac host as the vision backend through `OLLAMA_HOST`, with the frame size and clock budget written down.

**Out (non-goals):**
- **Playing football.** No snap is taken, no play is called, no result is recorded. `watch-game` starts the game, holds, and leaves.
- **Deciding whether a mod is *good*.** The run reports what appeared; taste is JR's.
- **Live gameplay control or a vision-driven controller** — measured and ruled out in `LOCAL_VISION_LATENCY.md` §4; gameplay control is a policy, not a VLM.
- **Widening `HUB_GAMEDAY` to allow Play Season Game.** A separate job keeps that guard intact.
- **Reading a mod's encrypted payloads.** An `.fbmod` is opaque (`MOD_BUILDING.md`); the check compares what the screen *shows*, never the mod file.
- **The Mac host setup itself** — that is `docs/reference/LOCAL_VISION_HOST.md` and the `mac-vision-setup` session, not this plan.

## 2. Design

### 2.1 The run

```
python3 -m tools.mod_check run --save CAREER-TUNE-<name> --mod <slug> \
    --segments game_start,halftime [--dry-run] [--run-dir DIR]
```

1. Refuse anything but `CAREER-TUNE-*` (same rule as `sim-season`), and refuse while `pipeline.watch_save` runs.
2. Read `data/mod_checks/<slug>/segments.json` — the segment plan (§2.2) — and `checks.json` — the asset specs (§2.3).
3. Drive the pilot: `launch` (or attach when Madden runs), load the tile, then the new `watch-game` job to reach the first segment; turn the clip on; sample frames; move to the next segment.
4. Run `assets.check_frame` for every spec against every sampled frame of its segment; a segment passes when its spec's latest decisive frame passes, and the report names the frame.
5. Ask the vision model for one short description of the segment's contact sheet (§2.4). Never used for the verdict.
6. Write `data/mod_checks/runs/<stamp>_<slug>/report.json` and `report.md`, print the markdown, and send one ntfy summary like the pilot's jobs.

`--dry-run` walks the segment plan and the check specs and prints what it would record and score, touching no PC and no save (the same contract as `tools.madden_pilot --dry-run`).

### 2.2 Segment plans

`data/mod_checks/<slug>/segments.json`:

```json
{"segments": [
  {"name": "game_start", "after": "kickoff", "record_s": 8, "fps": 10, "width": 1280,
   "checks": ["fox_scorebug", "custom_midfield_logo"]},
  {"name": "halftime",  "after": "halftime_start", "record_s": 12, "fps": 10, "width": 1280,
   "checks": ["halftime_wipe"]}
]}
```

`after` names a **cue** the run waits for, drawn from a small fixed vocabulary the runner knows how to detect on screen (`kickoff`, `halftime_start`, `supersim_menu`); an unrecognised cue is a refusal, never a guess. `record_s`, `fps` and `width` mirror `win_agent record`'s own limits (`MAX_RECORD_S`, `MAX_RECORD_FPS`, `SMALL_WIDTH`).

A segment with no cue is a fixed wait, which is only allowed for `supersim_menu` (a static screen), so a timing-based check cannot silently score the wrong moment.

### 2.3 Checks per asset

`data/mod_checks/<slug>/checks.json` holds one spec per asset, in `assets.check_frame`'s own shape:

```json
{"fox_scorebug": {"region": [0.28, 0.06, 0.72, 0.16],
                  "reference": {"pass": "refs/fox_scorebug_pass.png",
                                "fail": "refs/fox_scorebug_native.png", "margin": 0.04}},
 "pink_ball":    {"region": [0.40, 0.55, 0.60, 0.80],
                  "colour": {"hue": [300, 350], "pass_at": 0.02, "min_sat": 0.5}}}
```

`capture` builds the references from JR's own run:

```
python3 -m tools.mod_check capture --mod <slug> --segment game_start --pass-at 0:03 --fail-at 0:31
```

It records the segment once, cuts the two named moments into `refs/*_pass.png` and `refs/*_native.png`, previews both crops, and only writes them after JR confirms the pair looks like "the mod" and "not the mod" — a wrong reference pair is the one way this whole thing can lie, so it is a human step on purpose.

### 2.4 The model's role

- **Description only:** `vision.describe()` on the segment's contact sheet, prompt "describe what is on screen in two sentences", answer stored as `summary` in the report.
- **Unknown screens:** if the run meets a screen the catalog does not know, it stops (as the pilot does) and the model's read of the frame is attached to the stop, so JR sees what it thinks before he looks.
- **Budget:** the Mac at ~1–2 s a frame, four to six frames a segment, so ~10 s of model time per segment — inside the run's minutes, not its seconds.
- **Never** a verdict, never a key press. Every press goes through the catalog, exactly as `sim-season` does today.

### 2.5 `watch-game`, the one new pilot job

```
python3 -m tools.madden_pilot watch-game --save CAREER-TUNE-<name> --franchise TUNE-<loaded>     [--to kickoff|halftime] [--hold-seconds N]
```

- Loads the TUNE tile and navigates GAMEDAY → the game's Play Season Game **only** from a catalogued, guarded route, presses through the pregame to kickoff, then **holds**: it presses nothing while the caller records, and returns the screen it ended on. `--hold-seconds` defaults to 60.
- **It never calls a play of its own** on the kickoff path. The supersim path below is the one exception, and it is a *simulation control* call, not gameplay: JR's D2 recipe.
- **The halftime path (D2, D3)** — the only way to reach the halftime show:
  1. supersim to the end of the first quarter;
  2. simulate the second quarter **by play** (not "sim to end");
  3. resume the real game for the last play of the half, with **1–19 s on the clock**;
  4. write the ready marker, then hold while the caller records the halftime show.
  Each step is a catalogued screen with a bounded label set, and the result records `route: supersim` and the clock so a missed halftime cannot be mistaken for a mod problem.
- It stops as `Stuck` if the clock does not read inside the window (or reads 0:00, when the half is already over) when it expects to resume, rather than resuming a play at the wrong time.
- **The static path:** `--to supersim` kicks off, opens SUPER SIM and holds there; its ready marker's screen must be `SUPERSIM_MENU`, so a fixed-wait segment is positioned and verified rather than timed on an arbitrary frame.
- Leaving: it quits to the franchise list through the same path `quit-franchise` uses, and the report states whether `-AUTOSAVE` was written. **That write is confirmed (D1, R1).**
- The catalog gains the screens this route needs, each with `confirm_ok` limited to the labels that start a game and the supersim controls it must press; roster cards and delete paths stay `never_select`/forbidden.

### 2.5a What spike S-1 verified (2026-11-12, `data/spikes/20261112-1410-game-start-halftime/report.md`)

- **Starting a game:** `HUB_GAMEDAY` (PLAY SEASON GAME bordered on arrival) → Space → a full-screen **pregame pivot** card whose only control is a SELECT hint that does not OCR (Space) → a **ready** screen with READY highlighted (Space) → a ~40 s broadcast intro that runs unattended → the **kickoff** screen (aim arc, 1 DEEP KICK / 2 PLACEMENT / 3 POWER SQUIB). The kick itself is a **~1.5 s Space hold**, not a tap.
- **Supersim:** Escape pauses; the pause menu's sixth item opens the supersim screen (CUSTOM PLAY OPTIONS / JUMP TO NEXT PLAY / JUMP FORWARD; SPEED: FAST; Backspace returns to the pause menu). JUMP FORWARD offers **CHANGE OF POSSESSION** (one drive, ~2 min of clock), **NEXT QUARTER** (stops at the next quarter start) and END OF GAME. JUMP TO NEXT PLAY moves only ~4 s of clock a press, so a whole quarter by play is not a bounded number of steps.
- **Real play does not run itself:** left alone it stops on a delay-of-game PENALTY dialog (Accept / Decline) whose border does not read. JR's rules: never control offense except the last play of the quarter (or the last play before the half or game ends, whichever is sooner); if on offense, reopen supersim and sim until on defense; if a snap must be taken, Space for the default play, ~1 s for the line, Space again.
- **Clone names:** the franchise tile OCR reads a digit `1` as `I`, so `CAREER-TUNE-CHK1` is unfindable; use digit-free clone names (the spike ran `CAREER-TUNE-CHECK`).
- **Evidence:** the pilot numbers step PNGs per process, so a multi-call run overwrites `steps/0001_*.png`. `watch-game` must write one run directory per invocation with unique step names.
- **Not verified:** the halftime show itself; whether NEXT QUARTER from Q2 plays or skips it; the exact last-play/resume window (the code now refuses 0:00 and requires 1–19 s). The first live cloned-save run is the measurement.

### 2.6 Output

`report.json`: `{save, mod, segments: [{name, cue, recorded_s, frames, checks: {asset: {status, measures, frame, why}}, summary}], verdict: pass|fail|unknown, minutes}`. `report.md` is the same in one table per segment plus the model's two sentences. One ntfy line: `mod_check <slug>: 2/3 pass (halftime fail)`.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | **Quitting a game writes `-AUTOSAVE` — confirmed by JR (D1).** A check could disturb the save it tested. | Not a question any more: S-1 measures *what* changes (which file, hashes, mtimes) so the report can state it. | Mandatory: `run` clones `--from` into a fresh `CAREER-TUNE-CHK<n>` and checks that; it refuses to run against a save it did not make. |
| R2 | The game-start segment is short and the cue may be missed (intro timing, pregame skip). | Run the capture step twice on the same save and compare which frames the cue picks. | Allow a fixed pre-roll (`preroll_s`) before the cue; report it so the reader knows the segment was timed, not cued. |
| R3 | A wrong `pass`/`fail` reference pair silently inverts a verdict. | `capture` previews both crops and requires JR's yes; the report always prints both similarity numbers. | Reject a pair whose pass/fail similarity is within the margin (unknown, with the numbers shown), rather than guessing. |
| R4 | **Halftime needs the real game resumed at the right moment (D2, D3).** A supersimmed game never shows the halftime show, so the recipe is: supersim to the end of Q1 → sim Q2 by play → resume for the last play of the half with 1–19 s on the clock. | Spike S-1 walks that recipe once and records the screens and the cue that says "resume now". | The route is now implemented and recording is synchronized with the job's ready marker; the show itself remains unmeasured until a live cloned-save run, and the result names the route/clock. 0:00 and an unreadable/out-of-window clock stop as Stuck. |
| R5 | The Mac is not up yet, or the LAN round trip is slower than measured. | `python3 -m tools.madden_pilot.vision_bench --models ollama:qwen2.5vl:7b` with `OLLAMA_HOST` pointed at the Mac. | The model's summary is optional: the run still produces every pass/fail without it, with `summary: "model unavailable"`. |
| R6 | Someone treats a `pass` as "the mod is installed" when the pack was not applied. | The plan names its expected `pack`; the run launches that pack itself through the pilot's `launch` job and checks the receipt (`status: ok`, same pack, `already_running: false`). | Refuse an already-running Madden (unknown provenance), a receipt naming another pack and a launch with no verified pack, before recording. The report records `pack_required`, `pack_actual`, `pack_verified` and the receipt. |

**Spike first (S-1, ~20 minutes, needs JR's PC and his yes):** on a throwaway `CAREER-TUNE-CHK1` clone, answer three things — (1) which catalogued route starts a game, (2) whether the pregame reaches kickoff without a press, (3) what quitting writes (save and autosave hashes before/after). It decides R1 and R4, which change U2's shape. Run it before wave 1.

## 4. Tests (synthetic, no live DB, no PC)

- `tests/test_mod_check.py` — segment plan validation: an unknown cue, a `record_s` outside `win_agent record`'s limits, a check name missing from `checks.json`, and a missing reference file each refuse with a readable reason; `--dry-run` prints the plan and scores nothing; the report shape is exactly §2.6; a passing run, a failing run and an inconclusive pair produce `pass`, `fail` and `unknown`.
- `tests/test_mod_check_capture.py` — cutting a clip into `pass`/`fail` crops by timestamp uses the right frames (a two-frame fixture clip), and a pair whose similarity is inside the margin is refused rather than saved.
- `tests/test_madden_pilot_jobs.py` (extend) — `watch_game` refuses a non-TUNE save, refuses while the watcher runs, presses nothing during its hold, writes its ready marker immediately before the hold for kickoff/halftime/supersim, reports the autosave it caused, and stops at 0:00 instead of resuming.
- `tests/test_madden_pilot_jobs.py` (F7) — a real Pilot and a menu driver separate from the catalog cover 9-row and 11-row pause menus, clamped/wrapped navigation, disabled-row skipping, a CELEBRATIONS start and targets SUPER SIM/RESUME/QUIT GAME; a missing selection stops before Space, and the generic pause exit reaches SUPERSIM_MENU on the measured 9-row menu.
- `tests/test_mod_check.py` / `test_mod_check_capture.py` (M-0689) — the M-0689 regressions: halftime is drivable and records on its ready marker; a missed supersim cue records nothing and scores nothing; historical/stale/too-short ready markers and an exited process stop; a failed quit, finish exception, missing result and non-zero exit are `incomplete`; live capture finishes exactly once on success, empty recording, cue error and bad timestamp; and launch/pack mismatches stop before recording.
- `tests/test_madden_pilot_nav.py` (extend) — every new catalog exit's confirm label is allowed, and the roster/delete guards are unchanged.

## 5. Work breakdown

### U1: `tools/mod_check` — segments, checks, report, CLI

- **Owns:** `tools/mod_check/__init__.py`, `tools/mod_check/segments.py`, `tools/mod_check/runner.py`, `tools/mod_check/report.py`, `tools/mod_check/__main__.py`, `tests/test_mod_check.py`, `tests/test_mod_check_capture.py`
- **Reads:** `tools/madden_pilot/assets.py` (`check_frame`), `checks/asset_probe.py` (`run_frame`), `vision.py` (`describe`), `MADDEN_SPIKE_AGENT.md` §7
- **Depends on:** none for the core; uses `watch-game` if present, else an injected driver the tests supply
- **Done when:** `python3 -m tools.mod_check run --save CAREER-TUNE-X --mod demo --dry-run` prints the segment and check plan; a synthetic run over fixture frames yields the §2.6 report with a pass, a fail and an unknown; `pytest -m "not integration" tests/test_mod_check.py tests/test_mod_check_capture.py` passes
- **Verify:** that pytest command → all pass

### U2: `watch-game` and its catalog entries

- **Owns:** `tools/madden_pilot/jobs.py`, `tools/madden_pilot/catalog.json`, `tools/madden_pilot/__main__.py`, `tests/test_madden_pilot_jobs.py`, `tests/test_madden_pilot_nav.py`
- **Reads:** `MADDEN_PILOT.md` §5–§6, the spike's findings (S-1), `jobs.py`'s `quit_franchise`
- **Depends on:** none (S-1's findings are written into the plan before the wave)
- **Done when:** `watch-game` refuses a non-TUNE save and a running watcher; it presses nothing during a kickoff hold; it walks the D2 halftime recipe (supersim to end of Q1 → Q2 by play → resume under 20 s) and refuses to resume outside that window; it reports the autosave write; and the nav tests still prove roster cards and delete paths are never confirmed
- **Verify:** `pytest -m "not integration" tests/test_madden_pilot_jobs.py tests/test_madden_pilot_nav.py` → all pass

### U3: the guide

- **Owns:** `docs/reference/MOD_CHECK.md`
- **Reads:** U1–U2's merged code, `LOCAL_VISION_HOST.md`
- **Depends on:** U1, U2
- **Done when:** the doc shows a worked example end to end for one mod — capture the references, run the check, read the table — with the Mac host line and the safety notes
- **Verify:** `grep -c "mod_check" docs/reference/MOD_CHECK.md` → 1 or more, and the commands in it run as written

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 0 | spike S-1 (no unit) | JR's PC and yes | Decides R1 (autosave) and R4 (halftime route) before U2 is shaped |
| 1 | U1, U2 | S-1 | Different files, no dependency: U1 tests against a fake driver, U2 guards a new job |
| 2 | U3 | U1, U2 | Writes the guide against merged code |

**File ownership check:**

| File | Unit |
|---|---|
| `docs/reference/MOD_CHECK.md` | U3 |
| `tests/test_madden_pilot_jobs.py` | U2 |
| `tests/test_madden_pilot_nav.py` | U2 |
| `tests/test_mod_check.py` | U1 |
| `tests/test_mod_check_capture.py` | U1 |
| `tools/madden_pilot/__main__.py` | U2 |
| `tools/madden_pilot/catalog.json` | U2 |
| `tools/madden_pilot/jobs.py` | U2 |
| `tools/mod_check/__init__.py` | U1 |
| `tools/mod_check/__main__.py` | U1 |
| `tools/mod_check/report.py` | U1 |
| `tools/mod_check/runner.py` | U1 |
| `tools/mod_check/segments.py` | U1 |

No path appears twice.

## 7. Spec changes when built

- **§1.1 Repository Layout:** a `tools/mod_check/` block (`__main__.py`, `segments.py`, `runner.py`, `report.py`) and `docs/reference/MOD_CHECK.md` in the reference list; `jobs.py`'s line gains `watch_game`.
- **§3 (Madden pilot):** the `watch-game` job and its TUNE-only rule; `HUB_GAMEDAY` explicitly **not** widened, with the reason.
- **`docs/reference/MADDEN_PILOT.md`:** a §5 row for `watch-game`; §6 gains "starting a game is `watch-game`'s job and it never calls a play".
- **`docs/reference/MADDEN_SPIKE_AGENT.md` §7:** one line pointing at `MOD_CHECK.md` for standing (non-spike) asset checks.
- **No schema, database, API endpoint or engine change.**

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | **What is the first segment?** Game start only, or game start + halftime? | **Answered:** game start and the supersim menu first; halftime is built on D2's recipe and lands in the same unit once S-1 has walked it. |
| Q2 | **Who starts the game — `watch-game` or JR by hand?** | **Answered:** `watch-game` drives it, including the supersim recipe — a hand-started game cannot be trusted to stop under 20 s for the last play of the half. |
| Q3 | **May the run make its own throwaway clone?** | **Answered yes and now mandatory** (D1): quitting writes an autosave, so `run` refuses any save it did not clone itself. |
| Q4 | **Does the model write anything JR reads first?** | **Answered as recommended:** two sentences of description per segment, after the pass/fail table. The verdict is the numbers; the model is commentary. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 | 1 | `worktree-vmc-u1` | `.claude/worktrees/vmc-u1` | merged | `32f554b` |
| U2 | 1 | `worktree-vmc-u2` | `.claude/worktrees/vmc-u2` | merged | `9b1006f`, `a7a5620` |
| U3 | 2 | `worktree-vmc-u3` | `.claude/worktrees/vmc-u3` | merged | `855696b` |
| U1 fix (orchestrator follow-up) | 2 | `worktree-vmc-u1b` | `.claude/worktrees/vmc-u1b` | merged | `e1d36ca` |
