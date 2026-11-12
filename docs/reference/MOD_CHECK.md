# Visual mod check: did the mod's assets appear?

One command loads a season game on a throwaway clone of a tuning save, records named stretches of it, and answers *"did this mod's assets appear?"* with a pass, fail or unknown **per asset**, the numbers behind it, and one short written description of each stretch. The verdicts are deterministic pixel checks (`tools.madden_pilot.assets.check_frame`); a vision model writes the description and nothing else.

Built by [`VISUAL_MOD_CHECK_PLAN.md`](../in_progress/VISUAL_MOD_CHECK_PLAN.md) (U1 `tools/mod_check/`, U2 the `watch-game` pilot job). The pilot underneath is [`MADDEN_PILOT.md`](MADDEN_PILOT.md) §5–§6. The per-frame check specs are the spike agent's ([`MADDEN_SPIKE_AGENT.md`](MADDEN_SPIKE_AGENT.md) §7), now standing rather than inside a spike. The model and its measured cost: [`LOCAL_VISION_LATENCY.md`](LOCAL_VISION_LATENCY.md) and [`LOCAL_VISION_HOST.md`](LOCAL_VISION_HOST.md). Building the mod itself: [`MOD_BUILDING.md`](MOD_BUILDING.md).

```
python3 -m tools.mod_check run --save CAREER-TUNE-<name> --mod <slug> [--segments a,b] [--dry-run] [--pack P]   # launches the plan's pack itself
python3 -m tools.mod_check capture --mod <slug> --segment <name> --pass-at M:SS --fail-at M:SS [--asset NAME] [--yes]
python3 -m tools.mod_check run --save CAREER-TUNE-<name> --mod <slug> --data-dir DIR      # or MU_MOD_CHECKS_DIR
python3 -m tools.madden_pilot watch-game --save CAREER-TUNE-<name> --to kickoff|halftime [--hold-seconds N]
```

**What runs without the PC.** `run --dry-run` (validates the plan, prints it, touches nothing), and `capture` over a run's existing frames. Everything else — `run` for real, `capture --save`, `watch-game` live — needs Madden on the PC and JR's yes. Run from the main checkout, not a worktree, so the check folder and the pilot's paths are the real ones.

| Command | Needs | Touches |
|---|---|---|
| `run … --dry-run` | nothing | reads the plan and the reference files, prints it |
| `capture` (no `--save`) | nothing | reads a previous run's frames, writes a preview under `data/mod_checks/captures/` |
| `capture … --yes` | nothing | writes the reference pair (`--yes` is the human gate) |
| `run` (no `--dry-run`) | Madden PC closed, a `CAREER-TUNE-*` save | launches the plan's pack through MMC, clones the save, starts a game, records, quits, writes a run |
| `watch-game` | Madden PC, a `CAREER-TUNE-*` save | starts a game on that clone, writes `ready.json` before its hold, and quits it |

## 1. What it is, and what it is not

- **It is one question per asset.** For every name in the mod's `checks.json`, `pass` (the assets look like the mod), `fail` (they look native) or `unknown` (the frame could not decide), with a measure and the exact frame the verdict came from.
- **It never reads the `.fbmod`.** An `.fbmod`'s payloads are MMC-encrypted ([`MOD_BUILDING.md`](MOD_BUILDING.md)), so the check compares what the screen *shows* against reference crops JR confirmed. A pass means "the screen matches the mod's picture"; it does not prove which pack was loaded (§12).
- **The vision model never decides anything.** It writes the segment's `summary` text (one call per segment, on the contact sheet). It never sets a status, never picks a frame and never presses a key. Every press goes through the pilot's catalog.
- **It does not play football and does not judge the mod.** `watch-game` starts the game, holds, and leaves; whether the result looks *good* is JR's call.
- **It is not a spike.** A spike explores an unknown screen under a written brief; this is a standing check on a screen the catalog already knows.

## 2. Where everything lives

Everything is under `data/mod_checks/`, which is git-ignored (`data/*` in `.gitignore`), like `data/spikes/` and `data/pilot_runs/`. `--data-dir DIR` or `MU_MOD_CHECKS_DIR` moves the whole root; every path the tools write must stay inside it, and a `--run-dir` outside it is refused.

```
data/mod_checks/
  <slug>/                        # one mod: the plan, its specs, its references (JR keeps these)
    segments.json                #   what to record
    checks.json                  #   what to score, one spec per asset
    refs/
      <asset>_pass.png           #   the mod's crop
      <asset>_native.png         #   the native crop
  runs/
    2026-11-12_152415_<slug>/     # one run: <YYYY-MM-DD_HHMMSS>_<slug>
      report.json                #   the machine-readable report (§8)
      report.md                  #   the same, printed and readable
      segments/<name>/
        frames/frame_NNNN.jpg    #   what `win_agent record` wrote, in order
        sheet.png                #   the contact sheet the model described
        clip.gif                 #   the same frames as a clip
  captures/
    2026-11-12_152145_<slug>/     # one capture: previews, and frames if it recorded live
      previews/<asset>_pass.png
      previews/<asset>_native.png
```

A slug is one directory name (`pink-cfb`), never a path. Two runs in the same second get `-2`, `-3`… on the default directory, and an explicit `--run-dir` that already holds anything is refused, so two invocations never share frames.

## 3. `segments.json`: what to record

```json
{"mod": "pink-cfb",
 "pack": "Mu Test",
 "segments": [
  {"name": "game_start", "after": "kickoff", "record_s": 8, "fps": 5, "width": 1280,
   "checks": ["midfield_logo", "pink_ball"]},
  {"name": "halftime", "after": "halftime_start", "record_s": 12, "fps": 5, "width": 1280,
   "checks": ["halftime_wipe"]}
 ]}
```

`pack` is the plan's expected **test pack**. It is required for a run and for a live capture: the run launches that pack itself through the pilot's `launch` job and refuses when Madden is already running, because an attached game's loaded pack cannot be verified (plan R6). The report records both the required and the launched pack.

| Key | Required | Rule | Default |
|---|---|---|---|
| `name` | yes | one plain name; it becomes the segment's folder under the run | — |
| `after` | yes (one of `after`/`cue`) | a cue the runner can see: `kickoff`, `halftime_start`, `supersim_menu` | — |
| `cue` | only the older form for `supersim_menu` | without `after`; the segment is still cued and verified | — |
| `record_s` | yes | 1–30 s, `win_agent record`'s `MAX_RECORD_S` | — |
| `fps` | no | 1–10, `MAX_RECORD_FPS` | 5 |
| `width` | no | at least 320 px; frames are resized to it | 960 (`SMALL_WIDTH`) |
| `checks` | no | names that exist in `checks.json` | `[]` |
| `preroll_s` | no | 0–10 s slept after the cue, before recording starts | 0 |

An unknown key is a refusal, so a typo cannot be silently ignored. The three cue names are the whole vocabulary (`segments.CUES`); anything else is refused rather than guessed:

| Cue | Means | Driven by |
|---|---|---|
| `kickoff` | the kickoff has been taken and the game is holding (a receiving-side start stops: there is no kicking view) | `watch-game --to kickoff` |
| `halftime_start` | the half's last snap has been taken; the job holds for the show | `watch-game --to halftime` |
| `supersim_menu` | the super-sim screen (static) is up | `watch-game --to supersim`, ready screen verified |

**No blind fixed wait.** Every segment is cued and positioned by a `watch-game` route. The job writes `ready.json` immediately before its hold; the driver accepts that marker only from the process it started, only while it is alive, only when the anchor matches the cue, and only when the remaining hold still covers the recording. For `supersim_menu` the marker's screen must also read `SUPERSIM_MENU`. The older `"cue": "supersim_menu"` form (no `after`) is still accepted, but it is cued and verified the same way. Every way of getting it wrong is refused with a sentence naming the segment:

```
segment game_start: after must be one of kickoff, halftime_start, supersim_menu, not 'after_halftime'
segment game_start: cue must be one of kickoff, halftime_start, supersim_menu, not 'supersim'
segment game_start: no after cue. Every segment must name one of kickoff, halftime_start, supersim_menu so it cannot score the wrong moment; the static super-sim screen is "after": "supersim_menu" (or the older "cue": "supersim_menu")
```

`preroll_s` is the plan's answer to a cue landing slightly early (R2): the run sleeps it after the ready marker and before recording. The report now carries the segment's `preroll_s` beside its `cue`, so a reader sees both the cue and the extra wait.

**Two facts to design around.** First, the driver starts `watch-game` in the background and records *during its hold*; the job quits the franchise when the hold ends, and `finish` collects that quit (§12). Second, an asset passes on its **latest decisive frame** in the segment, so a segment must show the asset for its whole length — a logo that is only in frame for the first second of an 8 s recording will be scored on the last frame, which no longer has it. Keep `record_s` short and the `region` on something that stays put (a scorebug, a field logo under a static camera), or cut the segment to the moment.

## 4. `checks.json`: what to score

One spec per asset, in `tools.madden_pilot.assets.check_frame`'s own shape:

```json
{"fox_scorebug": {"region": [0.28, 0.06, 0.72, 0.16],
                  "reference": {"pass": "refs/fox_scorebug_pass.png",
                                "fail": "refs/fox_scorebug_native.png", "margin": 0.04}},
 "pink_ball":    {"region": [0.40, 0.55, 0.20, 0.25],
                  "colour": {"hue": [300, 350], "pass_at": 0.02, "min_sat": 0.5}}}
```

| Part | Shape | Passes when |
|---|---|---|
| `region` | `[x, y, w, h]` as fractions of the frame, not pixels | — |
| `colour` | `{"hue": [lo, hi], "pass_at": x, "min_sat": s, "min_val": v}` | at least a share `x` of the region's pixels sit in the hue window (degrees) and are saturated ≥ `s` (0.5) and bright ≥ `v` (0.4) |
| `reference` | `{"pass": P, "fail": F, "margin": m, "region": [...]}` | the region scores at least `m` (0.05) higher against `P` than against `F`, grey SSIM after resize |
| `reference.region` | optional crop of *both* reference images | default: the reference file *is* the crop |

A spec may give both measures: it passes only when every measure passes, fails only when every measure says native, and is `unknown` otherwise — never a guessed pass. A spec with neither `colour` nor `reference` is refused.

`pass` and `fail` paths are **relative to the mod's directory** (`data/mod_checks/<slug>/`); the loader resolves them to absolute paths before `check_frame` sees them, so `refs/…` is right and `data/mod_checks/…` is wrong. A missing reference file is refused before anything touches the PC:

```
mod_check: checks.json: fox_scorebug: reference pass file is missing: refs/fox_scorebug_pass.png (looked for …/refs/fox_scorebug_pass.png)
```

`min_sat` on a colour check is the one dial for "is this really the mod's colour": a pale pink ball against grass may need 0.3, a solid repaint 0.5.

## 5. `capture`: making the reference pair

The pair is the whole basis of the verdict, so it is a human step on purpose. `capture` cuts two named moments out of one segment's frames, shows both crops with their numbers, and writes them only with `--yes`.

```
python3 -m tools.mod_check capture --mod pink-cfb --segment game_start --pass-at 0:00 --fail-at 0:06
```

| Flag | Means |
|---|---|
| `--mod`, `--segment` | which mod, which segment of its plan |
| `--pass-at`, `--fail-at` | the two moments as `M:SS` (or plain seconds); frame `N` is at `N/fps` of the segment's own `fps` |
| `--asset` | which check to build the pair for (default: the segment's first check) |
| `--yes` | write the pair; without it nothing is written |
| `--save CAREER-TUNE-<name>` | record the segment live (on a fresh clone) instead of reading a previous run |
| `--run-dir DIR` | read that run's frames; default is the newest `runs/*_<slug>/` for the mod |

It takes the frames from:

1. `--run-dir`'s `segments/<name>/frames`, else
2. the newest run for the mod (`runs/*_<slug>/`, by name), else
3. `--save`, which clones the save and records one segment — needs the PC.

Preview, from the demo clip in §11 (a run whose first 1.6 s show the mod and the rest do not):

```
mod_check capture: pink-cfb / game_start / midfield_logo
  pass frame (0:00): similarity_pass=1.000 similarity_fail=0.878 -> pass
  fail frame (0:06): similarity_pass=0.878 similarity_fail=1.000 -> fail
  margin: 0.04
  preview pass:   /tmp/opencode/vmc-doc/captures/2026-11-12_152145_pink-cfb/previews/midfield_logo_pass.png
  preview native: /tmp/opencode/vmc-doc/captures/2026-11-12_152145_pink-cfb/previews/midfield_logo_native.png
  preview only: re-run with --yes to write the reference pair
  would write pass:   /tmp/opencode/vmc-doc/pink-cfb/refs/midfield_logo_pass.png
  would write native: /tmp/opencode/vmc-doc/pink-cfb/refs/midfield_logo_native.png
  nothing written: re-run with --yes once the crops look right
```

(The paths are the temporary data root the examples in this guide were run against, `/tmp/opencode/vmc-doc`, standing in for `data/mod_checks` — see §11.)

Open both preview PNGs and ask the one question: **is the pass crop the mod, and the native crop not?** Then re-run with `--yes`. It writes where `checks.json` says (`reference.pass` / `reference.fail`) or, if the spec had no pair yet, `refs/<asset>_pass.png` and `refs/<asset>_native.png`.

**The margin refusal.** A pair that cannot separate the two frames is refused, with the numbers, and nothing is written (exit 2):

```
mod_check capture: refused: the pair is inside the margin (0.04) and would decide nothing
[pass frame: similarity_pass=1.000 similarity_fail=1.000; fail frame: similarity_pass=1.000 similarity_fail=1.000]
```

That happens when the two moments are really the same picture (the wrong timestamps, or an asset that was never on screen). Pick moments further apart, or a tighter `region`, or a bigger `margin` — but a bigger margin buys separation at the cost of noise.

**Why a wrong pair is the one way this can lie.** Every later verdict is "closer to `pass` than to `fail`"; if the pair is swapped, or both crops are the same picture, the check stays perfectly deterministic and quietly answers the wrong question. Nothing downstream can detect it. That is why the preview exists, why `--yes` is a separate command, and why the report always prints both similarity numbers — a pass of 0.51 against 0.49 in a `.04` margin would have been refused, but a pass of 0.51 against 0.44 is suspicious at a glance.

## 6. The run

```
python3 -m tools.mod_check run --save CAREER-TUNE-WEEK1 --mod pink-cfb --pack "Mu Test"
python3 -m tools.mod_check run --save CAREER-TUNE-WEEK1 --mod pink-cfb --segments game_start --dry-run
```

| Flag | Means |
|---|---|
| `--save` | a `CAREER-TUNE-*` save to clone; anything else is refused |
| `--mod` | the slug: `data/mod_checks/<slug>/` |
| `--segments a,b` | run only these segments, in plan order (default: all) |
| `--dry-run` | print the plan and score nothing: no clone, no driver, no frame, no notice |
| `--pack` | a cross-check of the plan's own `pack`; it is never treated as proof. The run launches the plan's pack itself. |
| `--reuse-launch` | attach to the **same verified Madden** a previous cold run launched (D6/F6): same Windows PID and start time, same plan pack, unchanged applied-pack fingerprint. It never relaunches and refuses unknown/stale/wrong-pack/changed state before cloning, recording or scoring. Needs one earlier run on this mod without the flag (its receipt is `<mod>/launch.json`), and exists for consecutive fixtures in one process (the dynamic-presentation handoff). |
| `--run-dir DIR` | write the run there instead of `runs/<stamp>_<slug>/`; must be inside the data root and must not already hold anything |
| `--data-dir DIR` | the data root (else `MU_MOD_CHECKS_DIR`, else `data/mod_checks`) |

**What it refuses, before touching anything** (exit 2, nothing written):

| Refusal | Message |
|---|---|
| a save that is not `CAREER-TUNE-*` | `mod_check: refused: only CAREER-TUNE-* saves are checked, not 'CAREER-WEEK1' (clone one with \`python3 -m tools.madden_pilot clone-save\`)` |
| a running `pipeline.watch_save` | `mod_check: refused: pipeline.watch_save is running: stop it first (a clone keeps the real franchise's LeagueID, so the watcher would ingest it)` |
| a save that is not on disk | `mod_check: refused: could not clone CAREER-TUNE-NOPE: no save CAREER-TUNE-NOPE in /mnt/c/Users/jrere/Documents/Madden NFL 27/saves` |
| a broken plan | the `PlanError` text, naming the file, segment or check and what was expected |
| a run directory outside the data root | `mod_check: the run directory must be inside <data root> (the git-ignored mod-check folder), not /tmp/elsewhere` |
| a run directory that already holds a run | `mod_check: refused: the run directory … is not fresh (it already holds N item(s) …); every run needs its own directory so old frames cannot satisfy a segment` |
| a plan with no expected pack | `mod_check: refused: segments.json must name the plan's expected test pack … (add "pack": "Mu Test" at the top level; plan R6)` |
| a `--pack` that disagrees with the plan | `mod_check: refused: --pack 'Mu Base' disagrees with the plan's expected pack 'Mu Test'; the launch is bound to the plan (plan R6)` |
| `--reuse-launch` with no stored receipt | `mod_check: stopped: no successful launch receipt to reuse: run once without --reuse-launch on this mod so the launch's provenance is recorded (plan R6/F6)` |
| `--reuse-launch` on a replaced/stale process | `mod_check: stopped: the running Madden is not the launched instance (saved pid/start …, current …); relaunch instead of attaching (F6)` |
| `--reuse-launch` after the pack changed | `mod_check: stopped: the applied pack 'Mu Test' changed since the launch (fingerprint … -> …); relaunch instead of attaching (F6)` |

**It launches the plan's pack, and clones its own save.** The run requires the plan's `pack`, launches it through the pilot's approved `launch` job and refuses when Madden is already running with **unknown provenance** (plan R6). The launch receipt is checked: `status: ok`, the same pack as the plan, `already_running: false`, and it now carries the launch's **process identity** (Windows PID + start time) and **applied-pack fingerprint** (the pack's ordered entries plus size/mtime of every enabled `.fbmod`). The receipt is stored at `<mod>/launch.json`; a caller can then pass `--reuse-launch` for the next fixture, which re-reads the running process and pack and refuses unless both still match. A caller's pack string or the Manager's `SelectedPack` alone is never accepted as proof. On a machine that cannot read identity/fingerprint the receipt records `process_verified`/`applied_verified: false`; a cold run still works and the report says so, but that receipt cannot be reused. Only after the launch (or verified attach) does it clone. Loading and quitting a franchise writes `<save>-AUTOSAVE` (JR, 2026-11-12; plan R1/D1), so the run never scores the save it was given: it copies `--save` to the first free `CAREER-TUNE-CHK<letter>` — A, B, … Z, AA — and scores that clone. There is no `--no-clone`, and a clone that comes back as the source save is a refusal. The names carry **letters, never digits**: the pilot finds a franchise tile by reading its name, and Windows OCR reads `1` as `I`, so `CAREER-TUNE-CHK1` was unfindable in spike S-1. The clone is left on disk (the saves folder's 20 MiB window is not pruned for a short check), so a folder of `CAREER-TUNE-CHK*` files accumulates; JR deletes the ones no report names.

**Every run gets a fresh directory.** The default `runs/<stamp>_<slug>/` gets `-2`, `-3`… when a second run starts in the same second, and an explicit `--run-dir` that already holds anything is refused. Old frames can therefore never satisfy a new segment, and `_frame_paths` only accepts files written after that recording began. `capture`'s default "newest run for this mod" compares the allocation identity (timestamp, then the same-second number as an integer, so `-10` is newer than `-9`), ignores another mod's names and a directory whose `report.json` names another mod, and picks the newest even when that run is incomplete: it then refuses for missing frames instead of silently cutting an older run's frames.

**Before the recorder takes over, the hold is checked again.** Readiness is accepted when the job's `ready.json` arrives, but up to 10 s of preroll can follow. Immediately before `win_agent record` (and after the preroll, at the run/capture seam) the driver re-checks that the job process is still alive and that its own hold deadline still covers the recording plus the margin; a dead job or a short hold stops the segment without calling Windows. `record()` requires that accepted job — a raw call with no job is refused with an explicit contract rather than bypassing the check.

**A previous segment must prove it left the PC before the next one starts.** Every earlier job is collected exactly once, including one that had already exited; a missing `result.json`, a non-zero exit, a stopped/timed-out job or an unconfirmed quit stops the next segment *before* a new driver process or Windows action. The already-scored pixel rows are kept in the stopped result (`segments`), so a failure at the second segment still reports the first one's evidence.

**Exit codes** (the pilot's convention):

| Exit | Means |
|---|---|
| 0 | the run's verdict is `pass`, or it was a `--dry-run` |
| 1 | the verdict is `fail` |
| 2 | `unknown`, refused, stopped, or `incomplete` (a missed cue, a screen nobody has catalogued, a driver that could not run, or a run whose quit/exit could not be confirmed) |

**One ntfy line** at the end, in the pilot's style, naming each segment that did not fully pass:

```
mod_check pink-cfb: 3/3 pass
mod_check pink-cfb: 0/3 pass (game_start fail, supersim fail)
mod_check pink-cfb: 1/3 pass (game_start unknown, supersim unknown)
```

The CLI also prints `mod_check: scored clone <clone> (cloned from <save>, pack <pack>)` and then `report.md` itself, so the terminal shows the same table the run wrote.

**Before a live run, close Madden — unless you are reusing a verified launch.** The run launches the plan's pack itself through `python3 -m tools.madden_pilot launch --pack <plan pack>`, which also presses through the cold boot to a menu. An already-running Madden with no matching receipt is refused because its loaded pack cannot be verified; `--reuse-launch` is the only attach path and it verifies the process and pack first. The driver shells out to `python3 -m tools.madden_pilot`; if that `python3` is not the project interpreter, set `MU_PILOT_PYTHON` to the one the repo uses.

**Pixel verdict and run status are separate.** `report.verdict` is the pixels alone. `report.operational.status` is `complete` only when the job exited 0, reported `ok`, and confirmed `quit: true`; a failed quit, a finish exception, a missing `result.json`, a non-zero exit or a finish timeout reads `cleanup_failed`, keeps the recorded rows for diagnosis, and the CLI exits 2 with the recovery sentence. A passing picture with an open game is therefore not a successful run.

## 7. What `watch-game` does underneath

So a stop can be read, and so the plan's cues make sense. `watch-game` is the one pilot job allowed to start a season game; the catalog's `HUB_GAMEDAY` still confirms `ADVANCE WEEK` alone, and `PLAY SEASON GAME` goes in through the pilot's own policy for that run.

**The pregame, in order** (spike S-1's route): load the `TUNE-<name>` tile on CURRENT FRANCHISES → `PROGRESSION_WARNING` (Proceed) → the hub → GAMEDAY → `PLAY SEASON GAME` (policy-only) → `GAME_PREGAME_PIVOT`, a full-screen card whose only control is a SELECT hint that does not OCR, so Space is pressed directly → `GAME_PREGAME_READY` (READY, confirmed through the catalog) → ~40 s of broadcast intro that runs unattended → `GAME_KICKOFF`. The kick is a **1.5 s Space hold**, not a tap; the catalog's `GAME_KICKOFF` has an empty `confirm_ok` so no tap can start the game by accident. If the game starts from the **receiving** side it shows `GAME_KICKOFF_RECEIVE` instead: no key is pressed at all, the job checks the scorebug clock and waits out the CPU's automatic kick to `GAME_PLAY_CALL` (F8). A `--to kickoff` run that meets that branch stops and says so, because there is no kicking view left to record.

**Then it holds.** `--hold-seconds` (default 60, 1–600) passes with **no key pressed and no look taken** — a look refocuses Madden — and one look at the end reports the screen and, on a game screen, the scorebug clock.

**The ready marker.** Immediately before every hold the job writes `ready.json` into its own run directory: `anchor` (`kickoff`, `halftime_start` or `supersim_menu`), `route`, the last screen it looked at, the clock, `hold_seconds`, a wall-clock hold deadline, the job's pid and the `--ready-token` the driver passed. The driver accepts only a marker carrying its token, from that process's pid, while the process is alive, with the cue's anchor and (for `supersim_menu`) the `SUPERSIM_MENU` screen; the remaining hold must still cover the recording plus a margin. A historical `steps/*.json` classification is never readiness.

**`--to halftime` walks JR's supersim recipe** (D2/D3: a fully simmed game never shows the halftime show, so the half must be resumed for its last play):

1. Escape → `PAUSE_MENU` → SUPER → `SUPERSIM_MENU`;
2. JUMP FORWARD → **NEXT QUARTER** (stops at the start of Q2);
3. JUMP FORWARD → **CHANGE OF POSSESSION** twice (one drive each);
4. **JUMP TO NEXT PLAY** until the scorebug clock reads **1–19 s in Q2** (each press moves about 4 s);
5. Backspace → the **measured landing**: `GAME_PLAY_CALL` (the usual one), or `PAUSE_MENU` in the alternate variant, where only a verified RESUME is confirmed; then **the Q2 clock is re-read immediately before** the half's last snap (Space for the default play, ~1 s, Space again). 0:00, an unreadable clock, or a clock that left the 1–19 s Q2 window during the transition stops without the snap;
6. write `ready.json`, then hold through the halftime show.

The **clock, not a timer, is the resume cue**: if the clock never reads inside the window — 10 unreadable clocks in a row, 320 presses, or a quarter other than 1 or 2 — the job stops as Stuck rather than resume the half at the wrong moment. A clock reading `0:00` in Q2 also stops: the half is already over, so there is no last play to resume. That stop is the recipe failing, not the mod.

**`--to supersim`** kicks off, Escape → `PAUSE_MENU` → SUPER → `SUPERSIM_MENU`, writes its ready marker with `screen: SUPERSIM_MENU`, and holds there. It is the route behind a `supersim_menu` segment, so a static segment is positioned and verified rather than timed on whatever is on screen.

**Control rule.** It calls no play of its own. Every control between the kickoff and the half's last snap is a supersim control; if a sim hands real play back, the job reopens the pause menu and sims on. The half's last snap is the only football key press in the whole job, and an offense drive is simmed, never played. `JUMP TO NEXT PLAY` moves the clock; `JUMP FORWARD → END OF GAME` is never confirmed.

**Leaving.** Outside a game it quits through the same route `advance` uses; inside a live game it first leaves super-sim through the measured Back landing (play call or pause), presses Escape from a play call until `PAUSE_MENU`, pins **QUIT GAME**, lifts that one label through the pilot's policy for the run (the catalog does not allow it), and leaves through the **measured `EXIT GAME` modal**, confirming only **YES - EXIT TO MENU**. `YES - EXIT TO DESKTOP` and `NO` are refused by the job even if a catalog lists them, and a tooltip is not a modal (the pause menu's "Any unsaved progress will be lost." stays `PAUSE_MENU`). The landing after EXIT TO MENU is still unmeasured, so the run reports it and, if it is still a loaded franchise hub, finishes with the normal TUNE quit. A landing that is neither the measured modal nor a menu stops the run rather than press a second blind time. `result.json` names `route` (`kickoff` or `supersim`), `clock`, `quit_route` (`route` or `quit-game`), `end_screen`, and `written` / `autosave_written`.

A dry run of that recipe is free and needs no PC — it proves the route, not the screen:

```
python3 -m tools.madden_pilot watch-game --save CAREER-TUNE-CHKA --to halftime --hold-seconds 1 --dry-run
# result.json: "route": "supersim", clock {"quarter": 2, "seconds": 10}, "quit_route": "quit-game", "end_screen": "FRANCHISE_LIST"
python3 -m tools.madden_pilot watch-game --save CAREER-TUNE-CHKA --to supersim --hold-seconds 1 --dry-run
# result.json: "route": "supersim", screen SUPERSIM_MENU at the ready marker
```

## 8. Reading `report.md`

One table per segment, then the model's description:

```
# Mod check: pink-cfb

- save: `CAREER-TUNE-CHKA`
- pack: `Mu Test` (required `Mu Test`, verified: yes)
- autosave written: yes
- game quit: yes via quit-game, end screen FRANCHISE_LIST
- run status: **complete**
- verdict (pixels): **pass**
- minutes: 0.05

## game_start

cue `kickoff`; 8 s requested; preroll 0 s; 40 frame(s).

| asset | status | measures | frame | why |
|---|---|---|---|---|
| midfield_logo | pass | similarity_pass=0.998; similarity_fail=0.878; reference_margin=0.040 | segments/game_start/frames/frame_0039.jpg | similarity pass 0.998 beats fail 0.878 by 0.04 |
| pink_ball | pass | colour_share=0.467; colour_pass_at=0.020 | segments/game_start/frames/frame_0039.jpg | colour share 0.467 >= 0.02 (hue [300, 350]) |

A broadcast kickoff view of a Madden season game, scorebug across the top and the
midfield logo at centre. The replay banner is not on screen.
```

| Field | Read it as |
|---|---|
| `save` | the **clone** that was scored, not the save that was passed in (`CAREER-TUNE-CHKA`); the CLI prints the source |
| `pack` | the launch receipt's pack, with the plan's required pack and whether the receipt was verified; never the caller's bare string |
| `autosave written` | whether `<clone>-AUTOSAVE` appeared while the run quit — the R1 fact, `yes`/`no`/`unknown` |
| `game quit` | the job's own `quit`/`quit_route`/`end_screen`; `unknown` when finish did not report one |
| `run status` | `complete` only when the job exited 0, reported `ok` and confirmed the quit; otherwise `cleanup_failed` with the reason and recovery |
| `verdict` | the pixels alone: `fail` if any asset failed; `pass` only if every checked asset passed; otherwise `unknown` |
| `minutes` | wall clock of the whole run, clone to report |
| per segment | `cue`, `recorded_s` as requested, and how many frames were actually written |
| `asset` | the check name from `checks.json` |
| `status` | `pass`, `fail` or `unknown` |
| `measures` | every number the check produced: `similarity_pass`, `similarity_fail`, `reference_margin`; or `colour_share`, `colour_pass_at` |
| `frame` | the frame the verdict came from, relative to the run directory — **look at it** when a verdict surprises you |
| `why` | the sentence `check_frame` produced, with the comparison spelled out |
| last line | the model's summary, or exactly `model unavailable` |

**`fail` versus `unknown`.**

- **`fail`**: a measure decided, and it decided native. `similarity fail 1.000 beats pass 0.879 by 0.04`; `colour share 0.000 < 0.02 (hue [300, 350])`. This is a real answer about the screen: the mod's picture was not there.
- **`unknown`**: no decisive answer. The two similarities sit inside the margin (`reference inconclusive (pass 0.970, fail 0.951, margin 0.04)`), or no frames were recorded (`no frames were recorded for this segment`), or a frame could not be read. It is not a quiet pass and not a quiet fail — the run says "I could not tell"; the first thing to try is a tighter `region`, then a better reference pair.
- A single `unknown` asset makes the whole run `unknown`: the verdict is `pass` only when every checked asset passed, so a run with two passes and one inconclusive reads `unknown`. That is deliberate — a run that cannot see one of its assets has not passed.

`report.json` is the same content in the exact shape:

```json
{"save": "CAREER-TUNE-CHKA", "mod": "pink-cfb",
 "segments": [{"name": "game_start", "cue": "kickoff", "recorded_s": 8, "preroll_s": 0.0,
               "frames": ["segments/game_start/frames/frame_0000.jpg", "…"],
               "checks": {"midfield_logo": {"status": "pass",
                                            "measures": {"similarity_pass": 0.998, "…": 0},
                                            "frame": "segments/game_start/frames/frame_0039.jpg",
                                            "why": "similarity pass 0.998 beats fail 0.878 by 0.04"}},
               "summary": "…"}],
 "verdict": "pass", "minutes": 0.05,
 "pack": "Mu Test", "pack_required": "Mu Test", "pack_actual": "Mu Test",
 "pack_verified": true, "launch": {"status": "ok", "pack": "Mu Test", "already_running": false},
 "quit": true, "quit_route": "quit-game", "end_screen": "FRANCHISE_LIST",
 "operational": {"status": "complete", "why": "", "recovery": null,
                 "job_status": "ok", "exit_code": 0},
 "autosave_written": true}
```

## 9. The vision host

The summary is written by the vision backend `tools/madden_pilot/vision.py` already uses, so pointing it at another host is one environment variable:

```
OLLAMA_HOST=http://100.87.79.110:11434 python3 -m tools.mod_check run --save CAREER-TUNE-WEEK1 --mod pink-cfb
```

Set it for one command, or in the project `.env` (not committed) to make it the default. It is read by `engines.llm_provider.get_ollama_base_url()`, so it moves **every** Ollama caller in the repo, not just this one; without it the backend is `http://localhost:11434` on the PC. The scheme is optional and `:11434` is appended if no port is given. Setup, the bind address and the traps are [`LOCAL_VISION_HOST.md`](LOCAL_VISION_HOST.md) — that doc is the guide for the Mac; this one only points at it.

**The summary is optional.** A model that is down, slow or not pulled cannot change a verdict: the describe call is wrapped, the report says `model unavailable`, and the table stands alone. The run is marked complete either way.

**What it costs.** One call per segment, on the segment's contact sheet, so 2 segments is 2 calls. Measured on the PC's GPU over 202 real frames: **median 1.48 s, p95 2.29 s** a frame (`LOCAL_VISION_LATENCY.md` §2). Measured on the Mac over the tailnet: **6.2–6.5 s** a frame warm, first call 27 s (`LOCAL_VISION_HOST.md` §5) — the Mac buys isolation from Madden's GPU, not speed, and as of 2026-11-12 it is not the default. Either way it is seconds inside a run that is minutes.

The model is the same backend everywhere else in the pilot, so a stop on an unknown screen still arrives with the screenshot and OCR lines the pilot writes (`stop.json`) — that read is the spike guard's, not this run's.

## 10. Safety

- **TUNE only.** The run refuses a save that is not `CAREER-TUNE-*`, and `watch-game` refuses one too. It never touches a real `CAREER-*` franchise.
- **Stop the watcher first.** The run refuses while `pipeline.watch_save` runs: a clone keeps the real franchise's LeagueID, so the watcher would ingest it into the real database. Stop it, run the check, start it again.
- **`CLAIM · Madden PC` on the board first**, and say how long you need. A run starts a game, quits it, and gives the PC back; nothing else may drive Madden meanwhile.
- **The clone is throwaway.** The report names it (`CAREER-TUNE-CHK<letter>`), so a `-AUTOSAVE` written by quitting lands on the clone and never on the save JR plays from. The run does not delete clones; delete the ones no report names yourself.
- **The game is quit for you.** There is no "leave it paused" option: a segment that ends in a live game is left through the pause menu's QUIT GAME and its confirmation; a segment that ends on the super-sim screen backs out with that screen's own Backspace first. If the confirmation cannot be read safely, the job stops with the game still up and a screenshot — JR then leaves it by hand, and the run's `operational.status` says `cleanup_failed`.
- **Screens measured on 2026-11-12.** `QUIT_GAME_CONFIRM` is now the real `EXIT GAME / Are you sure you want to exit the game? / YES - EXIT TO DESKTOP / YES - EXIT TO MENU / NO(default, absent from OCR)` modal (`live-F11.md`), not a guess; only `YES - EXIT TO MENU` is ever confirmed. `SUPERSIM_MENU`'s selected row is a white outline whose OCR can truncate `JUMP TO NEXT PLAY` to `JUMP TO NEXT` (named through the screen's measured alias), and Back from it returns to `GAME_PLAY_CALL`, not Pause (`live-F9.md`, `live-F10.md`). `GAME_KICKOFF_RECEIVE` is the receiving kickoff, which needs no key and advances on its own (`live-F8.md`). `SUPERSIM_JUMP_FORWARD`'s arrival selection is still `todo`. **The pause menu's rows vary by game state:** the measured live screen had 9 rows while the catalog lists 11, and its selected `QUIT GAME` tooltip is not a confirmation. Navigation reads the rows actually on the frame and the row actually inside the selected region; it never trusts a catalog shadow index, a fixed row count or a lone bright line. When the selected row cannot be read, the job stops before Space instead of guessing. Some states remain live-unmeasured (the landing after EXIT TO MENU, the real halftime show), so a run may still stop there and teach the catalog — that is the design, not a failure. Read `stop.json`, open the screenshot it names, and extend `catalog.json` as [`MADDEN_PILOT.md` §7](MADDEN_PILOT.md) describes. The committed real-frame regressions live in `tests/fixtures/visual_mod_check/` and `tests/test_visual_mod_check_round2.py`.
- **Never:** online play, purchases, deleting anything, roster decisions, a real franchise, or a pack that is not the throwaway test pack.

## 11. Worked example: `pink-cfb`, end to end

The mod: a pink football and a cyan midfield logo, built with [`MOD_BUILDING.md`](MOD_BUILDING.md) and installed into the `Mu Test` pack. It should be visible at kickoff and on the super-sim screen.

**1. The plan.** `data/mod_checks/pink-cfb/segments.json` (the `pack` is required):

```json
{"mod": "pink-cfb",
 "pack": "Mu Test",
 "segments": [
  {"name": "game_start", "after": "kickoff", "record_s": 8, "fps": 5, "width": 1280,
   "checks": ["midfield_logo", "pink_ball"]},
  {"name": "supersim", "after": "supersim_menu", "record_s": 3, "fps": 2, "width": 960,
   "preroll_s": 1.0, "checks": ["midfield_logo"]}
 ]}
```

**2. The checks.** `data/mod_checks/pink-cfb/checks.json` — a reference pair for the logo, a hue window for the ball:

```json
{"midfield_logo": {"region": [0.35, 0.40, 0.30, 0.20],
                   "reference": {"pass": "refs/midfield_logo_pass.png",
                                 "fail": "refs/midfield_logo_native.png", "margin": 0.04}},
 "pink_ball":    {"region": [0.42, 0.52, 0.14, 0.18],
                  "colour": {"hue": [300, 350], "pass_at": 0.02, "min_sat": 0.5}}}
```

The reference files do not exist yet, so the run would refuse. Dry run once the plan is written is the cheap way to find a typo; a missing reference is named:

```
mod_check: checks.json: midfield_logo: reference pass file is missing: refs/midfield_logo_pass.png (looked for /tmp/opencode/vmc-doc/pink-cfb/refs/midfield_logo_pass.png)
```

**3. Capture the pair.** Record the segment first: on a live chain a `run` records the kickoff segment itself, while `watch-game` holds the game, so its frames are the ones to cut from. `capture` then reads the newest run for the mod, or the folder `--run-dir` names:

```
python3 -m tools.mod_check capture --mod pink-cfb --segment game_start --pass-at 0:00 --fail-at 0:06
```

That prints the two similarity pairs and writes nothing (§5). With the crop at 0:00 showing the cyan logo and the crop at 0:06 showing the native grey one, re-run it with `--yes` and the pair lands in `data/mod_checks/pink-cfb/refs/`.

**4. Run it.**

```
python3 -m tools.mod_check run --save CAREER-TUNE-WEEK1 --mod pink-cfb --pack "Mu Test"
```

The plan, as `--dry-run` prints it before anything is touched (paths shown from a temporary data root, hence `/tmp/opencode/vmc-doc`; with the default root they read `data/mod_checks/pink-cfb`):

```
mod_check plan: pink-cfb (/tmp/opencode/vmc-doc/pink-cfb)
pack: Mu Test (the run launches this pack itself and verifies the launch receipt)
save: CAREER-TUNE-WEEK1 (dry run: would clone it to CAREER-TUNE-CHK<first free n> and score that clone)

segments:
  game_start: cue kickoff, record_s 8, fps 5, width 1280, preroll 0.0s
    check midfield_logo
    check pink_ball
  supersim: cue supersim_menu, record_s 3, fps 2, width 960, preroll 1.0s
    check midfield_logo

checks:
  midfield_logo: region [0.35, 0.4, 0.3, 0.2]; reference pass /tmp/opencode/vmc-doc/pink-cfb/refs/midfield_logo_pass.png fail /tmp/opencode/vmc-doc/pink-cfb/refs/midfield_logo_native.png margin 0.04
  pink_ball: region [0.42, 0.52, 0.14, 0.18]; colour hue [300, 350] pass_at 0.02 min_sat 0.5

dry run: nothing recorded, nothing cloned, nothing scored, no notice sent.
```

The run launches `Mu Test`, clones the save, starts a game on the clone, records 8 s from the kickoff hold and 3 s on the positioned super-sim screen (a second `watch-game` job), scores all recorded frames, quits the game, and writes `data/mod_checks/runs/<stamp>_pink-cfb/`. It prints:

```
mod_check: scored clone CAREER-TUNE-CHKA (cloned from CAREER-TUNE-WEEK1, pack Mu Test)
```

followed by `report.md` (§8), whose table is the one quoted there: both assets `pass` at `segments/game_start/frames/frame_0039.jpg`, the pixel verdict `pass`, `run status: complete`, `autosave written: yes`, and one ntfy line `mod_check pink-cfb: 3/3 pass`.

**5. When it reads `fail`, the mod is not on screen.** The same plan and references on a run with the native assets scored `0/3 pass`:

| asset | status | measures | frame | why |
|---|---|---|---|---|
| midfield_logo | fail | similarity_pass=0.879; similarity_fail=1.000; reference_margin=0.040 | segments/game_start/frames/frame_0039.jpg | similarity fail 1.000 beats pass 0.879 by 0.04 |
| pink_ball | fail | colour_share=0.000; colour_pass_at=0.020 | segments/game_start/frames/frame_0039.jpg | colour share 0.000 < 0.02 (hue [300, 350]) |

Before blaming the mod, check the pack (`- pack:` in the report, which now names both the required and the launched pack) and open the `frame` — a franchise-list frame means the segment was recorded after the game was left, which the driver no longer does for its cued segments (§12).

**6. When it reads `unknown`, nothing decided.** A frame the checks could neither match nor reject:

| asset | status | measures | frame | why |
|---|---|---|---|---|
| midfield_logo | unknown | similarity_pass=0.970; similarity_fail=0.951; reference_margin=0.040 | segments/game_start/frames/frame_0039.jpg | reference inconclusive (pass 0.970, fail 0.951, margin 0.04) |
| pink_ball | pass | colour_share=0.475; colour_pass_at=0.020 | segments/game_start/frames/frame_0039.jpg | colour share 0.475 >= 0.02 (hue [300, 350]) |

Tighten the `region` onto the logo itself, or raise the margin to `0.08` if the field behind it changes with the camera.

The report rows in steps 4–6 come from `tools/mod_check`'s own scorer run over fixture frames in the worktree that wrote this guide (no PC, `describe` stubbed), so `minutes` is seconds rather than a live run's few. The layout, numbers and wording are exactly what a live run prints. Every quoted block in this guide was captured against the temporary data root `/tmp/opencode/vmc-doc`; on JR's checkout the same paths read under `/home/jrereyi/Project-Mu/data/mod_checks/`.

## 12. Limits and the known gap

- **A segment is recorded during `watch-game`'s hold.** `PilotDriver.wait_for` starts `watch-game --save <clone> --to <route> --hold-seconds <record_s + preroll + 20> --run-dir <run>/watch-game/NN_<route> --ready-token <T>` as a background process, then waits for that process's own `ready.json`. It returns while the job is still holding, so `win_agent record` captures the game; when the hold ends the job quits on its own and `finish` collects its `result.json` (`quit`, `quit_route`, `autosave_written`, `end_screen`, `exit_code`) instead of quitting a second time. A process that exits before its marker, one whose marker names another anchor or screen, a stale marker (wrong token/pid), a marker whose hold is too short, and one still short at its cue timeout all stop as a driver `stopped` before a frame is recorded, rather than score whatever is on screen. The cue timeout is per cue: 180 s for `kickoff`, 300 s for `supersim_menu`, and **1800 s for `halftime_start`**, whose bounded 320 JUMP TO NEXT PLAY presses and two possessions need far more than a kickoff's pregame. A by-hand recording is still useful only to debug the pilot itself (and `watch-game --no-quit` is the tool for that):

  ```
  python3 -m tools.madden_pilot watch-game --save CAREER-TUNE-CHKA --to kickoff --hold-seconds 60 --no-quit
  # in another terminal, while it holds (this is exactly the call PilotDriver.record makes):
  python3 - <<'PY'
  from tools.madden_pilot import win
  win.call(["record", "--title", "Madden NFL 27", "--dir",
            "/home/jrereyi/Project-Mu/data/mod_checks/manual/segments/game_start/frames",
            "--seconds", "8", "--fps", "5", "--width", "1280"])
  PY
  ```

  then leave the game by hand and cut the references with `capture --run-dir <a folder with segments/game_start/frames>`. A `report.md` whose `frame` is a franchise list on this code means the job never reached its ready marker (read the stop's `why`), not that the mod failed.
- **Halftime is now recordable, but the show itself is still unmeasured.** `halftime_start` drives the job's `--to halftime` route and records from the job's own ready marker after the half's last snap; the route is no longer an unconditional stop. No live run has yet produced footage that visibly contains the halftime show (plan §2.5a/R4), so the first live run is the measurement: if the hold after the snap does not show the show, the recipe/anchor must be re-measured rather than the result called a pass. A stop during the recipe is the recipe, not the mod.
- **`supersim_menu` has its own route.** `watch-game --to supersim` kicks off, opens the pause menu's SUPER SIM screen, writes a ready marker whose screen must read `SUPERSIM_MENU`, and holds there. The driver refuses any other ready screen, so a supersim segment can no longer score an arbitrary current frame. The screen is still `todo` in the catalog for its selection border, but its words and route are known.
- **Pause-menu navigation is evidence-based, not positional.** The live audit's 9-row pause menu silently activated STATS while a shadow cursor claimed SUPER; `_pin_highlight` and the generic pause exit now use the visible rows and read the selected row from the frame after every Down press, visiting each row at most once (clamped, wrapped or disabled-skipping menus included). A target that is not visible, or a selection that never becomes readable, stops before Space. That is why a live run can stop at a pause-menu step even when the menu is on screen: the fix refuses to press an unverified item, and the acceptance run records the selected-row evidence.
- **Pack provenance is launch-level, not file-level.** The run launches the plan's pack itself and refuses an already-running Madden; the report names the required pack, the launch receipt's pack and `pack_verified`. That proves what the launch workflow started, not that Frosty applied every `.fbmod` file, and it still cannot tell a mod's asset from a hand-painted lookalike (§ last bullet).
- **A clone keeps the original's LeagueID**, so never run a check while the watcher is up, and never leave a `CAREER-TUNE-CHK*` clone where the league office can mistake it for a franchise to advance.
- **The check compares pictures, not files.** It cannot tell a mod's asset from a hand-painted lookalike, and it cannot tell a mod from a pack that shipped the same colour. It answers "the screen looks like the mod's reference", one asset at a time.
