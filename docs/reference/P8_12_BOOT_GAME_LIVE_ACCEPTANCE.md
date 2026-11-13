# P8-12 — `boot-game` live acceptance

**Check:** `docs/CLOSE_OUT_CHECKLIST.md` P8-12 — the live PC acceptance owed since `p8-boot-game` built the
`boot-game` job at **`7a34c8aa`** (`tools/madden_pilot/jobs.py`, CLI `__main__.py`). Unit tests only existed
before this run; nothing had been driven against Madden.
**Run:** 2026-11-12 21:00–21:22 EST, `check-d`, holding `CLAIM · Madden PC`, `Mu Base` pack.
**Verdict: two of three variants pass live, `--to kickoff` fails reproducibly, and the stock CLI cannot start
Madden at all** (a `TypeError` before the first key). P8-12 stays **◐**.

| Acceptance item | Result | Evidence |
|---|---|---|
| `boot-game` on a `CAREER-TUNE-*` clone, `--to pregame` | **PASS** | `data/pilot_runs/2026-11-12_210421_boot-game` |
| the same with `--to kickoff` | **FAIL** (twice, same place) | `…_210534_boot-game` step 0042, `…_210824_boot-game` step 0054 |
| `--managed` on `CAREER-MU2026` | **PASS** | `…_212105_boot-game` |
| one forced hand-back, read as a usable `handback.json` | **PASS** | `…_211300_boot-game` (postseason clone), `…_211121_boot-game` (interop flake) |
| stock CLI front door cold-boots Madden | **FAIL — blocking `TypeError`** | `…_210028_boot-game` |

## 1. What passed

**`--to pregame` on `CAREER-TUNE-BOOT26`** (a fresh clone of `CAREER-MU2026`, week 1): `screen_checks` =
cold boot → menu, tile `TUNE-BOOT26` loaded, hub **2026 WEEK 1 matching the save's own week**, GAMEDAY →
PLAY SEASON GAME, landing on **`GAME_PREGAME_READY`**. `handback: false`,
`football_key: "none: boot-game stops before the kickoff"`. The rear camera shows the real pregame card
(CHI at CAR, the readiness rows READY / VIEW SUPERSTAR X-FACTORS / ADVANCED SETTINGS / EDIT UNIFORMS), i.e. the
job left JR exactly where it says it will.

**`--managed` on `CAREER-MU2026`**: cold boot → the save's own tile `MU2026` → hub **2026 WEEK 1 = the
save's week** → GAMEDAY → PLAY SEASON GAME → `GAME_PREGAME_READY`, `handback: false`. The real franchise was
**loaded and never written** — the job does not sim, save or quit, and `CAREER-MU2026`'s mtime is still 20:37,
more than 40 minutes before this run started at 21:16. Madden is **left sitting at that pregame**, which is
P8-12's designed hand-over.

Both runs also blew past the week-1 `HUB_WEEKLY_DRAFT_CARD` (the draft-class card that otherwise refuses an
advance), because `boot-game` never advances — it opens the game.

## 2. Finding 1 (HIGH, blocking): the CLI cannot cold-boot Madden

`python3 -m tools.madden_pilot boot-game --save …` — the documented front door — raises before Madden starts:

```
TypeError: 'NoneType' object is not callable
  tools/madden_pilot/jobs.py:2042  boot_game → _start_madden(pilot, pack, run, …)
  tools/madden_pilot/jobs.py:703   run(argv)
```

`boot_game(..., run=None, …)` passes its `run` straight into `_start_madden`, which calls `run(argv)` whenever
Madden has to be started. The `launch` job fills that default itself
(`if run is None: run = lambda argv: subprocess.run(argv, check=True, cwd=launch_cwd())`, `jobs.py:646-647`);
`boot_game` does not. `__main__._run` only supplies `run` for a `--dry-run` (line ~604), so **every live
`boot-game` that has to start Madden crashes**, and only a run with Madden already open survives.
Reproduced at HEAD (`git show HEAD:tools/madden_pilot/jobs.py:1980-1983` has `run=None`).
Repro: `data/pilot_runs/2026-11-12_210028_boot-game` (exit 1, traceback, no window started).
Suggested fix: the same two-line default in `boot_game`, and a unit test that calls `boot_game(run=None,
launch=True)` with a fake `madden_running` of `False` and asserts the command was run.

## 3. Finding 2 (MEDIUM): `--to kickoff` cannot press READY, and presses a cursor key instead

Twice, on a fresh cold boot, the job reached `GAME_PREGAME_READY` and then stopped:

```
kind: Stuck · reason: "moving the highlight to READY left GAME_PREGAME_READY for UNKNOWN"
hand-back: screen null-ish / UNKNOWN · next: "the pilot pressed nothing more; take over …"
```

The screenshot from the failure (`…_210534_boot-game/steps/0042_UNKNOWN.png`) is **still the readiness
screen**, but with the **selection moved off READY onto ADVANCED SETTINGS**. What happens: on the frames right
after the pivot, Windows OCR returns only `X C. WILLIAMS / J. THUNEY / J. JOHNSON / OVR / 83 / BACK` — none of
`READY`, `VIEW SUPERSTAR X-FACTORS`, `ADVANCED SETTINGS`, `EDIT UNIFORMS` — so the selected row cannot be
placed (`highlight: None`), and `pilot.highlight("READY")` walks the cursor blindly: two Down presses, landing
on ADVANCED SETTINGS, and the next look (which now reads `ADVANCED SETTINGS`/`OHIO` but not `READY`) classifies
`UNKNOWN`, so the run hands back.

So the kickoff variant is **not usable on the live screen today**, and worse, it presses a game-setup key
(ADVANCED SETTINGS is one Space from opening the settings menu) on a screen the catalog calls safe.
Two things are worth fixing, independently:
- `pilot.highlight()` should stop rather than press blind arrows when the target label has no box and the
  highlight is unreadable (the catalog already requires proof before Space; the cursor movement has no such rule);
- `GAME_PREGAME_READY` should match on its own secondary labels (`ADVANCED SETTINGS`, `EDIT UNIFORMS`,
  `VIEW SUPERSTAR`) so a frame that drops READY is still recognised, and/or the job should re-look until the
  readiness rows are read before moving the selection.

## 4. Hand-back: usable

Two natural hand-backs, both read as working:

| Trigger | `handback.json` |
|---|---|
| clone at the postseason (`CAREER-TUNE-BASE26`, Wild Card) | `status: handback`, `screen: HUB_WEEKLY`, `reason: "the franchise is at WILDCARD, not a regular-season game week; there is no game to boot"`, screenshot, and the run **never pressed PLAY SEASON GAME** — the safe refusal works |
| a WSL interop failure mid cold boot | `kind: Stuck`, `screen: BOOT_SPLASH`, screenshot of the splash, `next: "the pilot pressed nothing more; take over from the screen in the screenshot"` |

Two cosmetic flaws:
- a hand-back with **no screen read at all** (the flake hit the very first look) writes `screenshot: null` while
  `next` still says "the screen in the screenshot" (`…_212006_boot-game`); the text should branch on a null
  screen.
- a **driver failure** is handed back like a game-state unknown, so the reason names WSL plumbing rather than
  anything JR can act on. It is safe (nothing pressed), just not the kind of "unknown screen" the guidance is
  written for.

## 5. Environment, and what was wrapped

- **WSL interop flakes dominated this hour:** `UtilAcceptVsock:281: accept4 failed 110` on a look or a key, with
  the machine at load 17–18 on 16 cores from the other agent sessions. Five of the fifteen driver attempts died
  that way; every one is recorded in its run dir. A retry picks up from the menu, so nothing was lost.
- **Two wrappers, both stated rather than hidden:**
  1. `jobs.watcher_running()` false-positives on any command line containing the literal string
     `pipeline.watch_save` — including this session's own prompt — so every job refused; the driver replaces it
     with an honest check (a python interpreter actually running the module, verified empty).
  2. `boot_game(run=None)` is the HIGH finding above; the driver supplies the same default the `launch` job
     uses, so the rest of the job (cold boot, tile walk, week check, GAMEDAY, pivot, hand-back) could be judged
     on the real screen instead of stopping at the bug.
  Everything else is the job's own code: same catalog, same refusals, same hand-back. The stock CLI's crash is
  reported, not hidden.
- `CAREER-TUNE-BOOT26` was cloned at 20:59 and **pruned cleanly** (596,736 B freed); the P4-01 clone made earlier
  could not prune. Two extra `CAREER-TUNE-*` clones exist from this session (`P4A`, `BASE26`, `BOOT26`); none of
  them is a real save and none was deleted.

## 6. Not tested

- The ~40 s intro and the kickoff view: `--to kickoff` never got past READY, so the post-READY half of the route
  (intro, `GAME_KICKOFF` / `GAME_KICKOFF_RECEIVE`) is unverified live in this build.
- Playing from the hand-over: not part of the acceptance and not attempted; the job's last key is a menu one and
  no football key was pressed in any run (`football_key: none` in both passes).
- The managed run was driven in place (`--franchise MU2026`, `--no-launch`) after the first attempt flaked during
  the tile walk; the load path itself is the clone's, verified in the same hour.
