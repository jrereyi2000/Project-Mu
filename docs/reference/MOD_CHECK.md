# Visual mod check: did the mod's assets appear?

One command loads a season game on a throwaway clone of a tuning save, records named stretches of it, and answers *"did this mod's assets appear?"* with a pass, fail or unknown **per asset**, the numbers behind it, and one short written description of each stretch. The verdicts are deterministic pixel checks (`tools.madden_pilot.assets.check_frame`); a vision model writes the description and nothing else.

Built by [`VISUAL_MOD_CHECK_PLAN.md`](../in_progress/VISUAL_MOD_CHECK_PLAN.md) (U1 `tools/mod_check/`, U2 the `watch-game` pilot job). The pilot underneath is [`MADDEN_PILOT.md`](MADDEN_PILOT.md) §5–§6. The per-frame check specs are the spike agent's ([`MADDEN_SPIKE_AGENT.md`](MADDEN_SPIKE_AGENT.md) §7), now standing rather than inside a spike. The model and its measured cost: [`LOCAL_VISION_LATENCY.md`](LOCAL_VISION_LATENCY.md) and [`LOCAL_VISION_HOST.md`](LOCAL_VISION_HOST.md). Building the mod itself: [`MOD_BUILDING.md`](MOD_BUILDING.md).

```
python3 -m tools.mod_check run --save CAREER-TUNE-<name> --mod <slug> [--segments a,b] [--dry-run] [--pack P]
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
| `run` (no `--dry-run`) | Madden PC, a `CAREER-TUNE-*` save | clones the save, starts a game, records, quits, writes a run |
| `watch-game` | Madden PC, a `CAREER-TUNE-*` save | starts a game on that clone and quits it |

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

A slug is one directory name (`pink-cfb`), never a path. Two runs in the same second would collide in principle; the stamp is to the second, so leave a second between runs.

## 3. `segments.json`: what to record

```json
{"mod": "pink-cfb",
 "segments": [
  {"name": "game_start", "after": "kickoff", "record_s": 8, "fps": 5, "width": 1280,
   "checks": ["midfield_logo", "pink_ball"]},
  {"name": "halftime", "after": "halftime_start", "record_s": 12, "fps": 5, "width": 1280,
   "checks": ["halftime_wipe"]}
 ]}
```

| Key | Required | Rule | Default |
|---|---|---|---|
| `name` | yes | one plain name; it becomes the segment's folder under the run | — |
| `after` | for a cued segment | a cue the runner can see: `kickoff`, `halftime_start` | — |
| `cue` | only for a fixed wait | must be `supersim_menu`, and then `after` must be left out | — |
| `record_s` | yes | 1–30 s, `win_agent record`'s `MAX_RECORD_S` | — |
| `fps` | no | 1–10, `MAX_RECORD_FPS` | 5 |
| `width` | no | at least 320 px; frames are resized to it | 960 (`SMALL_WIDTH`) |
| `checks` | no | names that exist in `checks.json` | `[]` |
| `preroll_s` | no | 0–10 s slept after the cue, before recording starts | 0 |

An unknown key is a refusal, so a typo cannot be silently ignored. The three cue names are the whole vocabulary (`segments.CUES`); anything else is refused rather than guessed:

| Cue | Means | Driven by |
|---|---|---|
| `kickoff` | the pre-snap kickoff screen is on | `watch-game --to kickoff` |
| `halftime_start` | the half is over; the halftime show is on | `watch-game --to halftime` |
| `supersim_menu` | the super-sim screen (static) | a fixed wait — never a stop |

**The fixed-wait rule.** A segment with no `after` must say `"cue": "supersim_menu"`. That is the only screen a timing-based segment is allowed on, because it is static: timing any other moment would let a check score the wrong play without saying so. Every way of getting it wrong is refused with a sentence naming the segment:

```
segment game_start: after must be one of kickoff, halftime_start, supersim_menu, not 'after_halftime'
segment game_start: cue must be one of kickoff, halftime_start, supersim_menu, not 'supersim'
segment game_start: no after cue. Only the static supersim_menu screen may be a fixed wait (write "cue": "supersim_menu"); every other segment must name one of kickoff, halftime_start, supersim_menu so it cannot score the wrong moment
```

`preroll_s` is the plan's answer to a cue landing slightly early (R2): the run sleeps it after the cue and before recording, and the report carries the segment's `cue`, not the preroll, so anything timed rather than cued is visible in the plan file.

**Two facts to design around.** First, the driver records *after* `watch-game` returns, and `watch-game` quits the franchise when it returns (§12). Second, an asset passes on its **latest decisive frame** in the segment, so a segment must show the asset for its whole length — a logo that is only in frame for the first second of an 8 s recording will be scored on the last frame, which no longer has it. Keep `record_s` short and the `region` on something that stays put (a scorebug, a field logo under a static camera), or cut the segment to the moment.

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
| `--pack` | the pack the game is running, recorded in the report (it does not launch anything) |
| `--run-dir DIR` | write the run there instead of `runs/<stamp>_<slug>/`; must be inside the data root |
| `--data-dir DIR` | the data root (else `MU_MOD_CHECKS_DIR`, else `data/mod_checks`) |

**What it refuses, before touching anything** (exit 2, nothing written):

| Refusal | Message |
|---|---|
| a save that is not `CAREER-TUNE-*` | `mod_check: refused: only CAREER-TUNE-* saves are checked, not 'CAREER-WEEK1' (clone one with \`python3 -m tools.madden_pilot clone-save\`)` |
| a running `pipeline.watch_save` | `mod_check: refused: pipeline.watch_save is running: stop it first (a clone keeps the real franchise's LeagueID, so the watcher would ingest it)` |
| a save that is not on disk | `mod_check: refused: could not clone CAREER-TUNE-NOPE: no save CAREER-TUNE-NOPE in /mnt/c/Users/jrere/Documents/Madden NFL 27/saves` |
| a broken plan | the `PlanError` text, naming the file, segment or check and what was expected |
| a run directory outside the data root | `mod_check: the run directory must be inside <data root> (the git-ignored mod-check folder), not /tmp/elsewhere` |

**It clones its own save, mandatorily.** Loading and quitting a franchise writes `<save>-AUTOSAVE` (JR, 2026-11-12; plan R1/D1), so the run never scores the save it was given: it copies `--save` to the first free `CAREER-TUNE-CHK<letter>` — A, B, … Z, AA — and scores that clone. There is no `--no-clone`, and a clone that comes back as the source save is a refusal. The names carry **letters, never digits**: the pilot finds a franchise tile by reading its name, and Windows OCR reads `1` as `I`, so `CAREER-TUNE-CHK1` was unfindable in spike S-1. The clone is left on disk (the saves folder's 20 MiB window is not pruned for a short check), so a folder of `CAREER-TUNE-CHK*` files accumulates; JR deletes the ones no report names.

**Exit codes** (the pilot's convention):

| Exit | Means |
|---|---|
| 0 | the run's verdict is `pass`, or it was a `--dry-run` |
| 1 | the verdict is `fail` |
| 2 | `unknown`, refused, or stopped (a missed cue, a screen nobody has catalogued, a driver that could not run) |

**One ntfy line** at the end, in the pilot's style, naming each segment that did not fully pass:

```
mod_check pink-cfb: 3/3 pass
mod_check pink-cfb: 0/3 pass (game_start fail, supersim fail)
mod_check pink-cfb: 1/3 pass (game_start unknown, supersim unknown)
```

The CLI also prints `mod_check: scored clone <clone> (cloned from <save>, pack <pack>)` and then `report.md` itself, so the terminal shows the same table the run wrote.

**Before a live run,** Madden must already be running with the pack loaded — `run` does not launch it (`--pack` is only recorded). Launch with `python3 -m tools.madden_pilot launch --pack "Mu Test"`, which also presses through the cold boot to a menu. The driver shells out to `python3 -m tools.madden_pilot`; if that `python3` is not the project interpreter, set `MU_PILOT_PYTHON` to the one the repo uses.

## 7. What `watch-game` does underneath

So a stop can be read, and so the plan's cues make sense. `watch-game` is the one pilot job allowed to start a season game; the catalog's `HUB_GAMEDAY` still confirms `ADVANCE WEEK` alone, and `PLAY SEASON GAME` goes in through the pilot's own policy for that run.

**The pregame, in order** (spike S-1's route): load the `TUNE-<name>` tile on CURRENT FRANCHISES → `PROGRESSION_WARNING` (Proceed) → the hub → GAMEDAY → `PLAY SEASON GAME` (policy-only) → `GAME_PREGAME_PIVOT`, a full-screen card whose only control is a SELECT hint that does not OCR, so Space is pressed directly → `GAME_PREGAME_READY` (READY, confirmed through the catalog) → ~40 s of broadcast intro that runs unattended → `GAME_KICKOFF`. The kick is a **1.5 s Space hold**, not a tap; the catalog's `GAME_KICKOFF` has an empty `confirm_ok` so no tap can start the game by accident.

**Then it holds.** `--hold-seconds` (default 60, 1–600) passes with **no key pressed and no look taken** — a look refocuses Madden — and one look at the end reports the screen and, on a game screen, the scorebug clock.

**`--to halftime` walks JR's supersim recipe** (D2/D3: a fully simmed game never shows the halftime show, so the half must be resumed for its last play):

1. Escape → `PAUSE_MENU` → SUPER → `SUPERSIM_MENU`;
2. JUMP FORWARD → **NEXT QUARTER** (stops at the start of Q2);
3. JUMP FORWARD → **CHANGE OF POSSESSION** twice (one drive each);
4. **JUMP TO NEXT PLAY** until the scorebug clock reads **under 20 s in Q2** (each press moves about 4 s);
5. Backspace → `PAUSE_MENU` → RESUME → **the half's last snap** (Space for the default play, ~1 s, Space again);
6. hold through the halftime show.

The **clock, not a timer, is the resume cue**: if the clock never reads inside the window — 10 unreadable clocks in a row, 320 presses, or a quarter other than 1 or 2 — the job stops as Stuck rather than resume the half at the wrong moment. That stop is the recipe failing, not the mod.

**Control rule.** It calls no play of its own. Every control between the kickoff and the half's last snap is a supersim control; if a sim hands real play back, the job reopens the pause menu and sims on. The half's last snap is the only football key press in the whole job, and an offense drive is simmed, never played. `JUMP TO NEXT PLAY` moves the clock; `JUMP FORWARD → END OF GAME` is never confirmed.

**Leaving.** Outside a game it quits through the same route `advance` uses; inside a live game it presses Escape until `PAUSE_MENU`, pins **QUIT GAME**, lifts that one label through the pilot's policy for the run (the catalog does not allow it), and leaves through `QUIT_GAME_CONFIRM` → CURRENT FRANCHISES. A label that looks like cancel/no/back/delete/trade is never pressed, and a landing that is neither the confirmation nor the franchise list stops the run rather than press a second blind time. `result.json` names `route` (`kickoff` or `supersim`), `clock`, `quit_route` (`route` or `quit-game`), `end_screen`, and `written` / `autosave_written`.

A dry run of that recipe is free and needs no PC — it proves the route, not the screen:

```
python3 -m tools.madden_pilot watch-game --save CAREER-TUNE-CHKA --to halftime --hold-seconds 1 --dry-run
# result.json: "route": "supersim", clock {"quarter": 2, "seconds": 10}, "quit_route": "quit-game", "end_screen": "FRANCHISE_LIST"
```

## 8. Reading `report.md`

One table per segment, then the model's description:

```
# Mod check: pink-cfb

- save: `CAREER-TUNE-CHKA`
- pack: `Mu Test`
- autosave written: yes
- verdict: **pass**
- minutes: 0.05

## game_start

cue `kickoff`; 8 s requested; 40 frame(s).

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
| `pack` | the `--pack` string, or `unknown (the launch did not report one)` |
| `autosave written` | whether `<clone>-AUTOSAVE` appeared while the run quit — the R1 fact, `yes`/`no`/`unknown` |
| `verdict` | `fail` if any asset failed; `pass` only if every checked asset passed; otherwise `unknown` |
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
- **`unknown`**: no decisive answer. The two similarities sit inside the margin (`reference inconclusive (pass 0.970, fail 0.951, margin 0.04)`), or no frames were recorded (`no frames were recorded for this segment`), or a frame could not be read. It is not a quiet pass and not a quiet fail — the run says "I could not tell", and the fixed wait to try first is the `region`, then the reference pair.
- A single `unknown` asset makes the whole run `unknown`: the verdict is `pass` only when every checked asset passed, so a run with two passes and one inconclusive reads `unknown`. That is deliberate — a run that cannot see one of its assets has not passed.

`report.json` is the same content in the exact shape:

```json
{"save": "CAREER-TUNE-CHKA", "mod": "pink-cfb",
 "segments": [{"name": "game_start", "cue": "kickoff", "recorded_s": 8,
               "frames": ["segments/game_start/frames/frame_0000.jpg", "…"],
               "checks": {"midfield_logo": {"status": "pass",
                                            "measures": {"similarity_pass": 0.998, "…": 0},
                                            "frame": "segments/game_start/frames/frame_0039.jpg",
                                            "why": "similarity pass 0.998 beats fail 0.878 by 0.04"}},
               "summary": "…"}],
 "verdict": "pass", "minutes": 0.05, "pack": "Mu Test", "autosave_written": true}
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
- **The game is quit for you.** There is no "leave it paused" option: a segment that ends in a live game is left through the pause menu's QUIT GAME and its confirmation. If that confirmation cannot be read safely, the job stops with the game still up and a screenshot — JR then leaves it by hand.
- **Screens whose words are still guesses.** `QUIT_GAME_CONFIRM` was never seen in spike S-1: its words (`ARE YOU SURE`, `PROGRESS`, `QUIT GAME`, `CANCEL`) are a reading of what a quit dialog usually says, and the catalog marks it `todo`. `SUPERSIM_MENU` and `SUPERSIM_JUMP_FORWARD` are also `todo`: S-1 saw both, but not which item is selected on arrival, so they are driven by position from the top item. **A first live run is expected to stop somewhere and teach the catalog** — that is the design, not a failure. Read `stop.json`, open the screenshot it names, and extend `catalog.json` as [`MADDEN_PILOT.md` §7](MADDEN_PILOT.md) describes.
- **Never:** online play, purchases, deleting anything, roster decisions, a real franchise, or a pack that is not the throwaway test pack.

## 11. Worked example: `pink-cfb`, end to end

The mod: a pink football and a cyan midfield logo, built with [`MOD_BUILDING.md`](MOD_BUILDING.md) and installed into the `Mu Test` pack. It should be visible at kickoff and on the super-sim screen.

**1. The plan.** `data/mod_checks/pink-cfb/segments.json`:

```json
{"mod": "pink-cfb",
 "segments": [
  {"name": "game_start", "after": "kickoff", "record_s": 8, "fps": 5, "width": 1280,
   "checks": ["midfield_logo", "pink_ball"]},
  {"name": "supersim", "cue": "supersim_menu", "record_s": 3, "fps": 2, "width": 960,
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

**3. Capture the pair.** Record the segment first: on a live chain that means a run whose frames do show the game, and until the driver gap in §12 is fixed, a recording made by hand. `capture` then reads the newest run for the mod, or the folder `--run-dir` names:

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

The run clones the save, starts a game on the clone, records 8 s at kickoff and 3 s on the super-sim screen, scores all 46 frames, quits the game, and writes `data/mod_checks/runs/<stamp>_pink-cfb/`. It prints:

```
mod_check: scored clone CAREER-TUNE-CHKA (cloned from CAREER-TUNE-WEEK1, pack Mu Test)
```

followed by `report.md` (§8), whose table is the one quoted there: both assets `pass` at `segments/game_start/frames/frame_0039.jpg`, verdict `pass`, `autosave written: yes`, and one ntfy line `mod_check pink-cfb: 3/3 pass`.

**5. When it reads `fail`, the mod is not on screen.** The same plan and references on a run with the native assets scored `0/3 pass`:

| asset | status | measures | frame | why |
|---|---|---|---|---|
| midfield_logo | fail | similarity_pass=0.879; similarity_fail=1.000; reference_margin=0.040 | segments/game_start/frames/frame_0039.jpg | similarity fail 1.000 beats pass 0.879 by 0.04 |
| pink_ball | fail | colour_share=0.000; colour_pass_at=0.020 | segments/game_start/frames/frame_0039.jpg | colour share 0.000 < 0.02 (hue [300, 350]) |

Before blaming the mod, check the pack (`- pack:` in the report) and open the `frame` — a franchise-list frame means the recording happened after the game was left (§12).

**6. When it reads `unknown`, nothing decided.** A frame the checks could neither match nor reject:

| asset | status | measures | frame | why |
|---|---|---|---|---|
| midfield_logo | unknown | similarity_pass=0.970; similarity_fail=0.951; reference_margin=0.040 | segments/game_start/frames/frame_0039.jpg | reference inconclusive (pass 0.970, fail 0.951, margin 0.04) |
| pink_ball | pass | colour_share=0.475; colour_pass_at=0.020 | segments/game_start/frames/frame_0039.jpg | colour share 0.475 >= 0.02 (hue [300, 350]) |

Tighten the `region` onto the logo itself, or raise the margin to `0.08` if the field behind it changes with the camera.

The report rows in steps 4–6 come from `tools/mod_check`'s own scorer run over fixture frames in the worktree that wrote this guide (no PC, `describe` stubbed), so `minutes` is seconds rather than a live run's few. The layout, numbers and wording are exactly what a live run prints. Every quoted block in this guide was captured against the temporary data root `/tmp/opencode/vmc-doc`; on JR's checkout the same paths read under `/home/jrereyi/Project-Mu/data/mod_checks/`.

## 12. Limits and the known gap

- **A run records after the game has been left.** As merged, `PilotDriver.wait_for` runs `watch-game` to completion (`runner.py`, `_call` is a blocking `subprocess.run`) and `watch-game` quits the franchise on its way out (`quit=not args.no_quit`, and the driver never passes `--no-quit`); only then does `PilotDriver.record` start `win_agent record`. So a live run today captures what the pilot left on screen — the franchise list — not the kickoff. Until `runner.py` is fixed (run the job with `--no-quit`, or record during its hold in a second process), do the recording by hand:

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

  then leave the game by hand and cut the references with `capture --run-dir <a folder with segments/game_start/frames>`. A `report.md` whose `frame` is a franchise list is this gap, not a mod problem.
- **A fixed-wait `supersim_menu` segment has nothing to park on.** The cue is allowed by the plan and accepted by the plan loader, but no driver route leaves the game standing on the super-sim screen, and a cued segment's `watch-game` has quit by the time recording starts. A `supersim` segment therefore scores whatever is on screen; treat its checks as `unknown` until the driver is fixed.
- **`--pack` is recorded, not verified.** It does not launch Madden and it cannot check what the game actually loaded (plan R6), so a `pass` with the wrong pack looks exactly like a `pass`. Launch with `python3 -m tools.madden_pilot launch --pack "Mu Test"` and read the `- pack:` line.
- **The halftime show itself is unverified.** The recipe in §7 reproduces down to the clock in a dry run, but no live run has yet seen the show (plan §2.5a, R4). A stop during the recipe is the recipe, not the mod.
- **A clone keeps the original's LeagueID**, so never run a check while the watcher is up, and never leave a `CAREER-TUNE-CHK*` clone where the league office can mistake it for a franchise to advance.
- **The check compares pictures, not files.** It cannot tell a mod's asset from a hand-painted lookalike, and it cannot tell a mod from a pack that shipped the same colour. It answers "the screen looks like the mod's reference", one asset at a time.
