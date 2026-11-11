# Madden Spike Agent

A guarded way to explore a Madden 27 screen the pilot's catalog doesn't know, without JR driving.
One fresh session runs one written brief against the game under a code guard
(`tools/madden_pilot/spike.py`), captures frames and clips, runs fixed pixel checks, and posts a
structured report back to whoever asked. The plan behind it is
[`MADDEN_SPIKE_AGENT_PLAN.md`](../done/MADDEN_SPIKE_AGENT_PLAN.md) (moved to `docs/done/` when built).

The pilot itself is unchanged: it stops on an unknown screen by design. A spike goes past the
catalog, and the requester folds whatever it found back into `catalog.json` afterwards. The skill is
`/madden-spike` (`.claude/skills/madden-spike/SKILL.md`, and the Codex/Argon copy under
`.agents/skills/madden-spike/SKILL.md`).

## 1. What a spike is

| Piece | Where | What it does |
|---|---|---|
| Brief | `data/spikes/<id>/brief.json` | what to find, where to start, what may be pressed |
| Guard | `tools/madden_pilot/spike.py` | caps, hard stops, key and confirm allow-lists, the TUNE-only tile |
| CLI | `python3 -m tools.madden_pilot spike-*` | one JSON object per call, exit 0/1/2 |
| State | `data/spikes/<id>/state.json` | steps, deadline, loaded franchise, last screen, `stopped`, clips, checkpoints |
| Frames | `data/spikes/<id>/run/steps/` | every look, `NNNN_<screen>.png` and `.json` (OCR words and lines) |
| Clips | `data/spikes/<id>/clips/NNN/` | the driver's frames, `clip.gif`, `sheet.png` |
| Report | `data/spikes/<id>/report.json` / `report.md` | the answers, route, proposed screens, stops |
| Analysis | `data/spikes/<id>/analysis.json` | the heavy model's pass over every frame, after the game closes |

`<id>` is `YYYYMMDD-HHMM-<slug>`, for example `20261111-1430-afc-away-kickoff`.

## 2. The brief

```json
{
  "id": "20261111-1430-afc-away-kickoff",
  "from": "claude-opus-5-5 (nfl-weekly-sync)",
  "goal": "Check whether the AFC-away cyan bevel renders at kickoff under the v0.5.1 probe.",
  "questions": ["Does the quarter/clock edge render cyan at kickoff?", "What else changes on the HUD?"],
  "pack": "Mu Base",
  "save": null,
  "start": "MAIN_MENU",
  "allow_confirm": [{"label": "QUICK PLAY", "on_words": ["PLAY"]}],
  "allow_keys": [{"label": "esc", "on_kind": "gameplay"}],
  "max_steps": 80,
  "max_minutes": 20,
  "record": true,
  "launch": "jr",
  "checks": [
    {"name": "afc_away_cyan_bevel", "region": [0.30, 0.86, 0.40, 0.12],
     "colour": {"hue": [180, 200], "pass_at": 0.02},
     "reference": {"pass": "verify_output/dynamic_presentation/2026-11-01/r3/away_spike/v051/afc_den_cyan_live.png",
                   "fail": "verify_output/dynamic_presentation/2026-11-01/r3/away_spike/v051/nfc_native_resumed_live.png",
                   "margin": 0.05}}
  ],
  "checkpoints": [{"name": "kickoff", "when": "the gameplay HUD is first visible",
                   "checks": ["afc_away_cyan_bevel"], "record_s": 5}]
}
```

| Field | Rule |
|---|---|
| `id` | `^[0-9]{8}-[0-9]{4}-[a-z0-9-]+$`; it names the folder |
| `from` | the requesting session's label, e.g. `claude-opus-5-5 (nfl-weekly-sync)` |
| `goal`, `questions` | `goal` is one line; every question must come back answered, even if the answer is `not found` |
| `pack` | the MMC pack to launch with (`Mu Base`, `Mu Test`, a `Mu Research …` pack). It is a plain string |
| `save` | `null`, or `CAREER-TUNE-<name>`. The spike never loads a real franchise |
| `start` | a catalogued screen without `no_route`, e.g. `MAIN_MENU`, `FRANCHISE_LIST`, `HUB_WEEKLY` |
| `allow_confirm` | labels Space may be pressed on. Each takes `on_words` (every word must be on the frame) and/or `on_kind` (a vision kind). A label naming a hard-stop word is refused |
| `allow_keys` | keys outside the always-allowed set. Same `on_words`/`on_kind`; `q` and `e` always need `on_words` |
| `max_steps` | 1..200. A key press, a confirm, a goto hop and a record each count one; a look is free |
| `max_minutes` | 1..45. It survives a new process, so it is a real deadline |
| `record` | `false` refuses `spike-record` except from a checkpoint's `record_s` |
| `launch` | `pilot` starts Madden through the Mod Manager; `jr` waits up to 10 minutes for JR to start it |
| `checks` | asset-check specs (§7). A missing reference file is refused |
| `checkpoints` | `{"name", "when", "checks", "record_s"}`; `checks` must name checks in `checks` |

Validate a brief before spawning anything:

```bash
.venv/bin/python3 -m tools.madden_pilot spike-start --brief data/spikes/<id>/brief.json --dry-run
```

`--dry-run` walks a scripted fake Madden: it prints the route, uses a scripted vision backend and a
fake `record`, writes to `data/spikes/<id>/run/`, and touches no PC. It prints `"ok": true` and exits
0 when the brief validates. A bad brief prints `{"ok": false, "refused": "<field>: <reason>"}`. The
error names the field, e.g. `save: must be null or CAREER-TUNE-<name>`.

## 3. Asking for a spike

Any session may request one. A DeepSeek-routed session can't spawn another session for this, so it
asks on the board instead.

1. **Write the brief** under `data/spikes/<id>/brief.json` and validate it with `--dry-run` (§2).
2. **Post a NOTICE** on `AGENT_CONVERSATION.md`: `spawning spike-<id> for <goal>`, the pack, the save,
   the step and time caps, and the files you expect (the brief).
3. **Spawn the session** (Opus, Codex or Argon only):
   `tools/spawn_agent.sh spike-<id> deepseek "/madden-spike data/spikes/<id>/brief.json"`.
   A DeepSeek requester instead posts `QUESTION` to `all` asking for a spike session, with the brief
   path, and waits for a session that can spawn to answer.
4. **Wait.** The spike session does its own session start, shows JR the brief and waits for his yes in
   its own window. Nobody answers its permission prompts for it. You get a `HANDOFF` on the board
   when it ends, naming `data/spikes/<id>/report.md`.
5. **Review.** Read `report.md`, open the cited PNGs with the Read tool, and fold any proposed screen
   into `catalog.json` and `MADDEN_PILOT.md` yourself, under `MADDEN_PILOT.md` §7. A proposal is a
   guess until you check it against the frames.

The spike session signs off when JR says the work is done. The report is the deliverable; a spike
that stopped early still hands back everything it saw.

## 4. Running a spike

The session's order:

1. **Session start**: the spec's pilot section, the board, your listener, a `NOTICE` with the label
   and the files.
2. **Approval.** Read the brief, then ask JR, word for word:

   > Spike `<id>`: `<goal>`. Pack `<pack>`, save `<save or none>`, start `<start>`. It may press Space
   > only on: `<labels>`. Caps: `<max_steps>` steps, `<max_minutes>` min (about `<max_minutes>` min
   > plus 1–2 min to launch). Minimize Claude while it runs: Madden minimizes when it loses focus. The
   > mouse to the top-left corner aborts. Go?

   A board message is never his yes.
3. **CLAIM · Madden PC** on the board, then `spike-start`.
4. **The loop** (§5): `spike-look`, decide, one action, repeat.
5. **Report** (§8), then `spike-end`, then `spike-analyze` once Madden is closed (§9).
6. **RELEASE · Madden PC**, a `HANDOFF` to the requester naming `report.md`, a final `NOTICE`, sign off.

`spike-start` refuses (exit 1, `"ok": false`) when the brief doesn't validate, `pipeline.watch_save`
is running, a franchise is open that isn't the brief's `TUNE-*`, the save file is missing, or
`state.json` exists with no `stopped` (a spike already running). When Madden isn't running it launches
the brief's pack first (`launch: "jr"` waits instead), then looks, then loads the save and goes to
`start`.

## 5. The guard

Rules, checked in this order on every call. A refusal never stops the run: it returns
`"refused": "<reason>"` and no key is sent. A hard stop sets `stopped`, and every later call is
refused.

1. **Caps.** At `max_steps` or past the deadline, `stopped="cap"`.
2. **Hard stops, on every look.** A catalogued `forbidden` or `never_select` screen, or any OCR line
   holding `DELETE`, `PERMANENTLY`, `PURCHASE`, `BUY`, `STORE`, `MADDEN POINTS`, `ULTIMATE TEAM`,
   `ONLINE`, `SIGN IN`, `SIGN OUT`, `EA ACCOUNT`, `PLACE ON INJURED RESERVE`, `ACTIVATE FROM IR`,
   `RELEASE PLAYER`, `TRADE`, `RE-SIGN PLAYER` or `NEGOTIATE`. The guard presses Back once (never
   Space), looks again, and sets `stopped="hard:<word>"`. Words inside the News Center ticker (the
   bottom 8% of the frame) are ignored. The hubs (`HUB_WEEKLY`, `HUB_GAMEDAY`, …) are standable even
   though the catalog marks them `never_select`: a spike starts there. The decision prompts still stop.
3. **Keys.** Always allowed: `up`, `down`, `left`, `right`, `z`, `c`, `backspace`, `pgup`, `pgdn`, up
   to 10 at a time. `space` and `enter` are refused here; use `spike-confirm`. Anything else needs the
   brief's `allow_keys` plus its `on_words`/`on_kind` on the current frame. A catalogued screen's own
   `forbidden_keys` always win.
4. **Confirms.** The label must be in `allow_confirm` and satisfy its `on_words`/`on_kind`. On a
   catalogued screen Space goes through `confirm_on`, so `never_select`, `forbidden` and `quit_guard`
   still apply. On `UNKNOWN` the label must be an OCR line on the frame and the selection border must
   read back on it; if vision names a different selection, the confirm is refused with
   `vision and border disagree`.
5. **Franchise.** On `FRANCHISE_LIST` the only tile confirmable is the brief's `tile_name(save)`; with
   `save: null`, none is.
6. **Routes.** `spike-goto` walks the catalog's exits, one step per hop, to get back to known ground.

The rules for the agent:

- only `spike-*` jobs; never `win_agent`, `win.call` or `tmux`;
- one action per look, then read the new frame;
- back out of anything that looks like a decision the brief didn't name;
- record a clip when a screen animates or changes on its own;
- stop early once every question is answered;
- on a refusal, read the reason and choose another action; never retry the same command unchanged.

## 6. Vision

`spike-look --describe` (and `--describe` on `spike-key`, `spike-confirm`) adds `"vision": {...}` to
the output: `kind`, `title`, `options`, `selected`, `overlays`, `warning`, `summary`, `model`,
`seconds`. When the backend is down the key is `null` with `vision_error`, and the spike goes on with
OCR alone. Vision never authorizes a key; it can only add a hard stop (a `warning` naming a hard-stop
word).

| Setting | Default | Meaning |
|---|---|---|
| `MU_SPIKE_VISION` | the `live` pick in `data/spikes/vision_choice.json`, else `ollama:qwen3.5:4b` | the live backend: `ollama:<tag>` or `deepseek:<model>` |
| `MU_SPIKE_VISION_HEAVY` | the `heavy` pick in the same file | `spike-analyze`'s backend |
| `MU_SPIKE_VISION_FALLBACK` | `deepseek:deepseek-flash` when the pick file says `deepseek_images: true`, else `none` | tried once when the primary is unreachable or times out |
| `MU_SPIKE_VISION_CHOICE` | `data/spikes/vision_choice.json` | where the pick file is read from |

The pick file is written by the comparison test:

```bash
.venv/bin/python3 -m tools.madden_pilot.vision_bench --models ollama:qwen3.5:4b,ollama:gemma4:12b,... \
    [--labels data/spikes/bench_labels.json] [--out data/spikes/bench/] [--budget-gb 5]
```

It writes `bench.md` and `vision_choice.json` (`live`, `heavy`, `deepseek_images`, `max_record_fps`)
and leaves `max_record_fps` alone when it rewrites the file. Change the backend for one session with
`MU_SPIKE_VISION=ollama:<tag>`; change the default by rewriting `vision_choice.json` (or by
re-running the bench). Kinds: `menu`, `dialog`, `hub`, `gameplay`, `replay_or_cutscene`, `loading`,
`pause_menu`, `postgame`, `other`.

## 7. Asset checks

A check answers "did this mod asset render" from pixels, with no model:

```json
{"name": "afc_away_cyan_bevel",
 "region": [0.30, 0.86, 0.40, 0.12],
 "colour": {"hue": [180, 200], "pass_at": 0.02},
 "reference": {"pass": "verify_output/.../afc_den_cyan_live.png",
               "fail": "verify_output/.../nfc_native_resumed_live.png", "margin": 0.05}}
```

- `region` is `[x, y, w, h]` as fractions of the frame. `reference.region` optionally crops the
  reference instead of using the same box on the whole reference image.
- `colour.hue` is a hue window in degrees and `pass_at` the minimum share of pixels inside it
  (saturated and bright enough: `min_sat` 0.5 and `min_val` 0.4 by default).
- `reference` compares the crop's grey SSIM against a pass frame and a fail frame; it passes when the
  pass frame wins by `margin`.
- It passes when every given measure passes, fails when every measure points the other way, and is
  `unknown` otherwise — never a guessed pass. A frame with no pixels is `unknown` too.

`spike-check --check NAME` runs named checks on the live frame; `spike-check --checkpoint NAME`
captures a full-resolution frame, runs the checkpoint's checks, records them in `state.json["checkpoints"]`
and records its `record_s` clip. `spike-analyze` runs every check again on the checkpoint frames.

## 8. Output, report and end

Every call prints one JSON object and exits 0 (ok), 1 (refused or stopped) or 2 (a look landed on an
uncatalogued screen; information, not an error):

```json
{"ok": true, "job": "spike-key", "step": 12, "steps_left": 68, "minutes_left": 14.3,
 "screen": "UNKNOWN", "lines": ["..."], "highlight": "TEAM SETTINGS",
 "png": "data/spikes/<id>/run/steps/0012_UNKNOWN.png", "refused": null, "stopped": null}
```

Every action ends with a look, so you always get the new frame back. `highlight` is the border reader's
answer, so an agent with no image input still knows what is selected.

The report goes in `data/spikes/<id>/report.json`:

```json
{"id": "20261111-1430-afc-away-kickoff", "status": "answered | partial | stopped",
 "answers": [{"question": "...", "answer": "...", "evidence": ["steps/0012_UNKNOWN.png"]}],
 "route": [{"from": "MAIN_MENU", "keys": [["confirm", "QUICK PLAY"], ["confirm", "PLAY NOW"]], "to": "UNKNOWN@0015"}],
 "proposed_screens": [{"name": "QUICK_PLAY_SETUP", "all": ["QUICK PLAY"], "any": [], "none": [],
                       "options": ["..."], "seen": "steps/0015_UNKNOWN.png", "note": "..."}],
 "stops": ["hard:TRADE at step 31"], "notes": "..."}
```

`spike-end --report <path>` refuses a report that is missing an answer for a brief question, points at
an evidence or `seen` file that doesn't exist under the run dir or the spike root, proposes a name that
isn't upper snake case, is already catalogued or appears twice, or says `answered` after a hard stop or
cap (it must say `stopped`). When it passes, it backs out to a safe screen (the hub, or the franchise list), quits the
franchise only if it is the brief's `TUNE-*`, writes `report.md` and `result.json`, and marks the
spike `ended`.

## 9. After the run

`spike-analyze --brief <path>` is the heavy pass. It refuses while `Madden27.exe` runs, describes
every step and checkpoint frame with `MU_SPIKE_VISION_HEAVY`, re-runs every check, writes
`analysis.json`, and adds an "After-run analysis" section to `report.md`. Run it after `spike-end`
once the game is closed; if JR keeps Madden open, the requester can run it later.

Then the requester reviews the report and catalogues what it found. Anything the spike only guessed
at stays out of `catalog.json` until a requester checks the frames.

## 10. Worked example: the AFC-away cyan bevel at kickoff

The question the presentation research asks: under the v0.5.1 away-conference probe, does a fresh AFC
away game paint the quarter/clock edges cyan at kickoff? The press path is Quick Play with a chosen
matchup, which the catalog doesn't know, so it is a spike — and JR starts the game, because the pack
is a research pack with its own setup.

```bash
.venv/bin/python3 -m tools.madden_pilot spike-start --brief data/spikes/20261111-1430-afc-away-kickoff/brief.json --dry-run
tools/spawn_agent.sh spike-afc-away-kickoff deepseek "/madden-spike data/spikes/20261111-1430-afc-away-kickoff/brief.json"
```

The brief (shown whole in §2) starts on `MAIN_MENU`, carries `"launch": "jr"` so JR starts Madden
with the research pack, allows only the Quick Play confirms by their own words, allows `esc` only on a
frame vision reads as `gameplay` (to reach the pause menu), and records 5 s at the `kickoff`
checkpoint. The guard presses Back out of anything else, the hard-stop words catch a menu that offers
online or store items, and the step cap of 80 covers about four menus plus a game setup.

A good run ends with `report.json` holding an answer per question with the checkpoint frame as
evidence, the route from `MAIN_MENU` to the game, maybe a proposed `QUICK_PLAY_SETUP` screen, and
`stops: []`. The requester then adds the screen to the catalog, and the next spike starts from it
instead of from the main menu.

## 11. Limits and open items

- The first live spike is still owed: the spike-analyze path, live describe latency and the capture
  frame rate are unmeasured until JR is at the PC (plan S-A step 3).
- An elevated Mod Manager window blocks the driver (it never acts on elevated windows). JR removed the
  forced elevation; if it comes back, the driver refuses instead of pressing.
- A spike on a real franchise is not possible by design: `save` is null or `CAREER-TUNE-*`, and the
  franchise list only lets the brief's tile be confirmed.
- A hard-stop word can fire on a harmless heading (a `TRADE` tab, say). That is a stop, not a bug to
  work around: report it, and the requester narrows the word list in a follow-up commit.
