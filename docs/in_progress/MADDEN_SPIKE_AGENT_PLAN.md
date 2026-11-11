# Project Mu: Madden Spike Agent Plan

**Status:** approved by JR (2026-11-11); building, wave 1 of 3 (U1 merged; U4 next). Q1–Q4 accepted as recommended.
**As built:** U1 (`7e51183`) also repaired two `tests/test_madden_pilot_win.py` cases that had been failing since `1383874` made `ensure_foreground` retry for 20 s: they expected a single focus attempt; they now use a fake clock and assert the retry contract. No production behaviour changed for this.
**Builds on:** [`MADDEN_PILOT_AND_TUNING_PLAN.md`](../done/MADDEN_PILOT_AND_TUNING_PLAN.md), [`docs/reference/MADDEN_PILOT.md`](../reference/MADDEN_PILOT.md), CLAUDE.md § "Starting other sessions".

**Goal:** Today any Madden spike that hits a screen the pilot doesn't know comes back to JR, because the pilot stops on UNKNOWN by design. With this plan, a main agent writes a short spike brief. A fresh session, one per spike, then drives Madden past the catalog under guards that live in code. The session is usually DeepSeek, but any model family can run it. A local vision model turns each frame into a text description, so the agent doesn't need image input of its own. The agent takes screenshots and short clips, runs fixed pixel checks that answer whether a mod asset is rendering, and posts a structured report back to the requesting agent on the board. The first real user is the dynamic presentation work: getting through a game's flow (Quick Play or a franchise game, kickoff, pause, Super Sim, postgame) and checking which scorebug, banner or marker renders. JR still says yes before each spike takes the PC. He no longer has to drive.

**Delivers:**
- `tools/madden_pilot/win_agent.py` `record`: a burst of window frames captured with mss, with the abort check on every frame.
- `tools/madden_pilot/spike.py`: the brief, the guard (`SpikeGuard`), the per-spike state file, clip assembly and the report check.
- CLI jobs `spike-start`, `spike-look`, `spike-key`, `spike-confirm`, `spike-record`, `spike-goto`, `spike-check`, `spike-end` in `python3 -m tools.madden_pilot`, each with `--dry-run` on `ScriptedMadden`.
- `tools/madden_pilot/vision.py`: `describe(png) -> ScreenDescription`, served by local Ollama with DeepSeek as the fallback (`MU_SPIKE_VISION`, `MU_SPIKE_VISION_FALLBACK`), with a fixed prompt and a JSON check on the answer.
- `tools/madden_pilot/assets.py` and `tools/madden_pilot/checks/asset_probe.py`: fixed asset checks (a marker-colour share in a named region, and a similarity score for a crop against reference crops) that run on live frames or on saved ones.
- `python3 -m tools.madden_pilot.vision_bench`: the vision model comparison test over labelled pilot and presentation captures.
- `python3 -m tools.madden_pilot spike-analyze`: the heavy pass after Madden closes (a bigger model, every asset check, over the saved frames).
- Skill `/madden-spike <brief.json>` for every model family: `.claude/skills/madden-spike/SKILL.md` (Claude Code, including DeepSeek-routed sessions) and `.agents/skills/madden-spike/SKILL.md` (Codex, Argon).
- Guide `docs/reference/MADDEN_SPIKE_AGENT.md`: the brief format, the report format, how to request a spike, and the guard rules.

**JR's answers (2026-11-11):**
- D1 **Explore limit:** on any screen the agent may move the highlight (arrows, tabs) and Back out. It may press Space or Enter only on labels the requesting agent listed in the brief. Delete, online, store, purchase, sign-in and roster-decision screens always stop the run.
- D2 **One session per spike.** The requesting agent spawns a fresh DeepSeek session for each spike, and that session signs off once it has reported. This keeps each session's context small.
- D3 **JR says yes per brief,** in the spike session's own tmux window, after it shows him the brief. This is the same rule as `/madden` today.
- D4 **A local vision model describes the screen,** so OCR alone isn't carrying the agent's view and every model family can run a spike. OCR stays as the exact source of labels for the guard.
- D5 **The use case is in-game presentation checks** (the dynamic presentation research): getting through a game's flow and seeing which mod assets are on screen. It doesn't need to be responsive.
- D6 **DeepSeek, not Gemini, is the fallback vision backend.** If S-A shows the DeepSeek API refuses images, the fallback is OCR only, and Gemini comes back to JR as a separate question.
- D7 **The orchestrator labels the presentation frames** for the comparison test.
- D8 **The first real spike is Codex's AFC-away cyan bevel check.**

---

## 0. Survey (code facts that set the scope)

| # | Fact (file:line) | Consequence |
|---|---|---|
| S1 | The driver has `windows`, `focus`, `look --ocr`, `key` and `run`/`kill`, and nothing that records video (`tools/madden_pilot/win_agent.py:421-450`). `capture()` grabs one frame with mss and checks the abort gestures first (`win_agent.py:282-296`). | `record` is new. It can reuse `capture()`'s mss path and its abort check, so the Windows venv needs no new package (mss 10.2.0, Pillow 13.0.0, `requirements-win.txt`). |
| S2 | `cmd_key` sends any key in `KEYS` up to 100 times, refocusing and checking for abort before each press (`win_agent.py:354-374`). The driver does not refuse Space. Space is refused one layer up, in `Pilot.press` (`navigate.py:160-182`). | The spike guard must sit above the driver as well: in `spike.py`, wrapping `Pilot`, never in the agent's own judgement. The skill tells the agent to call only the `spike-*` jobs, never `win_agent` or `win.call` directly. |
| S3 | `Pilot.look` stops after more than `unknown_limit` (20) UNKNOWN looks in a row, and stops on any `forbidden` screen (`navigate.py:118-158`). | Exploring means living on UNKNOWN. The guard sets `unknown_limit` to the brief's step cap and keeps the forbidden-screen stop. |
| S4 | `confirm_on` presses Space only on a catalogued screen, only on a `confirm_ok` label or a `policy` label, and only with the border highlight read on that label (`navigate.py:259-283`). `border_highlight(frame, labels)` reads the white outline for any label list (`screens.py:144`). | On an UNKNOWN screen, `spike-confirm` builds its own check: the label must be in the brief's `allow_confirm` and `border_highlight` must put the border on it. A border it can't read means no press. |
| S5 | Every Pilot run writes `steps/NNNN_<screen>.png|.json` with OCR lines (`navigate.py:126-148`). | The spike's evidence is these step files plus clips. The report cites step numbers. |
| S6 | `sim-season` refuses while `pipeline.watch_save` runs, because a `CAREER-TUNE-*` clone keeps the real LeagueID (`MADDEN_PILOT.md` §6; `jobs.watcher_running`, `jobs.py:115`). `quit_guard` refuses to quit any franchise not named `TUNE-*` (`navigate.py:250-257`). | `spike-start` makes the same watcher check. A spike may load only the brief's own `CAREER-TUNE-*` save, or no save at all. |
| S7 | `tools/spawn_agent.sh` refuses to start a DeepSeek session from a DeepSeek-routed one (`tools/spawn_agent.sh`, the `MU_ALLOW_DEEPSEEK_SPAWN` check). | An Opus, Codex or Argon session spawns the spike session. A DeepSeek session that needs a spike asks `opus`, `argon` or `codex` on the board, as for builders. |
| S8 | The DeepSeek route is Claude Code pointed at DeepSeek's Anthropic-compatible endpoint (`.claude/skills/deepseek/SKILL.md`). Nothing in the repo says whether that route accepts image input. | Unknown R1. The design must work on OCR text plus the border reader alone. Screenshots are evidence for the requester, who can view them. |
| S9 | `data/*` is git-ignored (`.gitignore:13`). Pilot runs live in `data/pilot_runs/` (`MADDEN_PILOT.md` §5). | Briefs, state, clips and reports go in `data/spikes/<id>/`, which is never committed. |
| S10 | `docs/reference/AGENTS.md` carries another session's uncommitted edits (`git diff --stat`, 2026-11-11). | No unit owns it. The orchestrator adds the skill row at the end, after checking `git diff HEAD`. |
| S11 | Local Ollama is running with `gemma4:e4b`, `gemma4:12b`, `qwen3.5:4b`, `qwen3.8:27b` and `lfm3:1.7b` (`/api/tags`, 2026-11-11). Nobody has yet checked which of them take images. `newsroom/llm_provider.py` calls Ollama. `tools/dossier_pilot.py:37,447-461` calls DeepSeek (`deepseek-flash`, `DEEPSEEK_API_KEY`) through its OpenAI-compatible API. Nothing in the repo shows a DeepSeek call with an image. | The vision connector supports `ollama:` and `deepseek:` (D6). S-A step 1b checks that DeepSeek takes images before the fallback is relied on. The comparison test (S-A) first finds which installed models take images, then adds one Qwen-VL 7–8B 4-bit model as a candidate. |
| S12 | The GPU is an RTX 5070 Ti with 16 GB (`docs/reference/PC_HARDWARE.md:5`), and 12.8 GB was in use when I checked on 2026-11-11 (`nvidia-smi`). Madden at 1440p takes a large share, and more during gameplay than in menus. | During a live spike only a small model (about ≤ 5 GB) runs beside Madden. Heavy description and checks wait for `spike-analyze`, after Madden closes, or go to DeepSeek. |
| S13 | The presentation research already uses deliberate marker colours (a cyan `BeveledEdge_MH`, a magenta `Bg_InnerFrame_Sliced_SN_96`) and keeps 103 captures of native and modded renders under `verify_output/dynamic_presentation/` (`DYNAMIC_PRESENTATION_RESEARCH.md:134-158`). It records that an automated pause "failed to register" and that the elevated MMC Manager couldn't be controlled (`:134`, `:223`). | Asset checks are fixed pixel tests, not vision-model judgements. The 103 captures are reference crops and labelled test data. A spike can't drive the elevated launcher, so a brief whose pack needs a launch says JR launches it (`launch: "jr"`). |
| S14 | `tools/madden_pilot/checks/first_down_line.py` is a stub that already describes a pixel measurement in a field band. `verify-mods --check <name>` runs `checks/<name>.py`'s `run(pilot)` (`MADDEN_PILOT.md` §5). | Asset checks live in `checks/` and use the same `run(pilot)` entry point, plus `run_frame(png, spec)` for saved frames. |
| S15 | `.agents/skills/` holds `argon`, `codex` and `opus`. Codex and Argon read skills from there, not from `.claude/skills/`. | The spike skill ships in both places, with the same body. |

## 1. Scope

**In:**
- Video: `win_agent record` frame bursts, and a GIF and contact sheet built from them in WSL.
- A brief → guarded explore → report loop for one spike, run by one fresh DeepSeek session.
- Guards in code: hard-stop words and screens, the brief's confirm allow-list, the step and time caps, saves limited to the clone, the watcher check, and quitting only `TUNE-*` franchises.
- New screens found on a spike come back as *proposed* catalog entries in the report.
- The `/madden-spike` skill and the reference guide, for Claude Code and for `.agents` models.
- A vision description on every look (local small model live, a bigger local model afterwards, DeepSeek as the fallback), plus a comparison test that picks the model from labelled captures.
- Asset checks (marker colour and reference-crop similarity) usable live, after a run, and from `verify-mods`.
- Briefs for in-game flow: Quick Play or a franchise game through kickoff, pause, Super Sim and postgame, with checkpoints where frames are captured and checked.

**Out (non-goals):**
- No vision-model judgement ever authorizes a key. It informs the agent; the guard still decides on OCR labels and the outline reader.
- No play-by-play control of a live game (moving players, calling plays in real time). Simming and menus only.
- The spike agent never edits `catalog.json` or `MADDEN_PILOT.md`. The requester, or a later `/madden` session, folds proposed entries in after reviewing the screenshots. This avoids two agents editing the catalog.
- No real `CAREER-*` franchise, no `catch-up`, no mod builds and no pack installs. Those stay with `/madden` and `/build-mod`.
- No in-game play (snaps, play calling). It covers menus and screens only.
- No audio capture, and no mp4 (there is no ffmpeg on either side). GIF and frames only.
- No standing yes and no long-lived spike session (D2, D3).

## 2. Design

### 2.1 Flow

1. **Request.** The requesting session (Opus, Codex or Argon) writes `data/spikes/<id>/brief.json` (§2.2) and runs `python3 -m tools.madden_pilot spike-start --brief <path> --dry-run` to check that the brief validates. It posts `NOTICE` "spawning spike-<id> for <goal>", then runs `tools/spawn_agent.sh spike-<id> deepseek "/madden-spike data/spikes/<id>/brief.json"`. `<id>` is `YYYYMMDD-HHMM-<slug>`.
2. **Approve.** The spike session does the normal session start (spec skim limited to the pilot section, board inbox, listener, `NOTICE`). It prints the brief summary (goal, save, allowed confirms, step cap, time cap, about how long it will take, plus the two `/madden` warnings: minimize Claude, and the abort gestures) and waits for JR's yes in its window (D3). A board message is never a yes.
3. **Claim.** On the yes it posts `CLAIM · Madden PC` and runs `spike-start`.
4. **Explore.** It loops: `spike-look` (read the OCR lines and the classified screen) → choose the next action → `spike-key`, `spike-confirm`, `spike-goto` or `spike-record`. The guard refuses anything outside the rules and says why, in JSON. A refusal never stops the run; a hard stop does.
5. **End.** `spike-end --report <path>` checks the report (§2.4), backs out to a safe screen (the hub or the franchise list), and quits the franchise only if it is the brief's `TUNE-*`. It writes `result.json`. The session posts `RELEASE · Madden PC`, a `HANDOFF` to the requester naming `data/spikes/<id>/report.md`, then a final `NOTICE` and signs off (D2).
6. **Review.** The requester reads the report, views the cited screenshots, and folds the proposed catalog entries into `catalog.json` and `MADDEN_PILOT.md` itself, under the normal rules (`MADDEN_PILOT.md` §7).

### 2.2 Brief (`brief.json`)

```json
{
  "id": "20261111-1430-settings-ability-recap",
  "from": "claude-opus-5-5 (nfl-weekly-sync)",
  "goal": "Find the team setting that turns off the ability recap, and turn it off.",
  "questions": ["Where is the setting?", "What does the screen look like after it is off?"],
  "pack": "Mu Base",
  "save": "CAREER-TUNE-SPIKE",
  "start": "HUB_WEEKLY",
  "allow_confirm": [
    {"label": "TEAM SETTINGS", "on_words": ["OPTIONS"]},
    {"label": "ABILITY RECAP", "on_words": ["SETTINGS"]}
  ],
  "allow_keys": [],
  "max_steps": 80,
  "max_minutes": 15,
  "record": true,
  "launch": "pilot",
  "checks": [],
  "checkpoints": []
}
```

`checks` holds the §2.8 specs, and `checkpoints` the §2.9 entries. `allow_keys` entries may carry `on_kind` (a vision `kind`) in place of, or as well as, `on_words`. `launch` is `"pilot"` or `"jr"`.

`load_brief(path) -> Brief` checks the brief and refuses it (`BriefError`, naming the field) when:
- `save` is neither `null` (no franchise is loaded) nor `CAREER-TUNE-<name>`;
- `start` is not a catalogued screen without `no_route`;
- `allow_confirm` names a label in `HARD_STOP_WORDS`;
- `allow_keys` holds anything other than names in `win_agent.KEYS`, or holds `q`/`e` without `"on_words"`;
- `max_steps` is outside 1..`MAX_STEPS_CAP` (200);
- `max_minutes` is outside 1..`MAX_MINUTES_CAP` (45);
- `id` is not `^[0-9]{8}-[0-9]{4}-[a-z0-9-]+$`.

`on_words`: the label is allowed only on a frame whose OCR holds every listed word. That ties an allowed confirm to the screen it was meant for.

```python
@dataclass(frozen=True)
class AllowConfirm: label: str; on_words: tuple[str, ...] = ()
@dataclass(frozen=True)
class Brief:
    id: str; from_: str; goal: str; questions: tuple[str, ...]; pack: str
    save: str | None; start: str; allow_confirm: tuple[AllowConfirm, ...]
    allow_keys: tuple[AllowConfirm, ...]   # label = key name
    max_steps: int; max_minutes: int; record: bool
    launch: str; checks: tuple[dict, ...]; checkpoints: tuple[dict, ...]
```

`AllowConfirm` also carries `on_kind: str | None = None`. `load_brief` additionally refuses a check that names a missing reference file, a checkpoint that names an unknown check, and an `on_kind` outside `vision.SCREEN_KINDS`.

### 2.3 The guard (`tools/madden_pilot/spike.py`)

`SpikeGuard(brief, pilot, state_path, clock=time.monotonic)` wraps a `Pilot` built with `unknown_limit=brief.max_steps` and `policy={}`. After every command, the state is saved to `data/spikes/<id>/state.json` (`{"steps": int, "started": iso, "deadline": iso, "loaded_franchise": str|null, "last_screen": str, "stopped": str|null}`). Each CLI call is a new process, so every call reloads the state and refuses once `stopped` is set.

Rules, checked in this order on every call:
1. **Caps.** `steps >= max_steps` or past the `deadline` → a hard stop, `stopped="cap"`. Each key press, confirm, goto hop or record counts as one step. A look doesn't.
2. **Hard stops, on every look.** A catalogued `forbidden` or `never_select` screen, or any OCR line that holds a `HARD_STOP_WORDS` entry: `DELETE`, `PERMANENTLY`, `PURCHASE`, `BUY`, `STORE`, `MADDEN POINTS`, `ULTIMATE TEAM`, `ONLINE`, `SIGN IN`, `SIGN OUT`, `EA ACCOUNT`, `PLACE ON INJURED RESERVE`, `ACTIVATE FROM IR`, `RELEASE PLAYER`, `TRADE`, `RE-SIGN PLAYER`, `NEGOTIATE`. After a hard stop the guard presses Back once (never Space), looks again, and sets `stopped="hard:<word>"`. There is one exception: a word inside the News Center ticker (the bottom `TICKER_BAND` = 8% of the frame height) is ignored, because the ticker carries free text.
3. **Keys (`spike-key --name K [--times n] [--hold s]`).** These are always allowed: arrows, `z`, `c`, `backspace`, `pgup`, `pgdn`, with `times <= 10`. `space` and `enter` are refused (use `spike-confirm`). `q`, `e`, `tab`, `esc`, `t` and every other key are refused unless the brief's `allow_keys` lists them with `on_words` that the current frame matches. A catalogued screen's own `forbidden_keys` always win.
4. **Confirms (`spike-confirm --label L`).** On a catalogued screen this goes to `Pilot.confirm_on` with `policy={screen: [L]}` when L is in `allow_confirm`, so `never_select`, `forbidden` and `quit_guard` still apply. On UNKNOWN: L must be in `allow_confirm`, the frame must hold its `on_words`, L must be an OCR line on the frame, and `border_highlight(frame, [L, *other OCR lines])` must return L. Then Space is pressed through `Pilot.press(..., _confirm=True)`. Any miss is refused with the reason, and no key is sent.
5. **Franchise.** On `FRANCHISE_LIST` the only tile that may be confirmed is `tile_name(brief.save)`. With `save: null`, no tile may be. `spike-end` quits only when `loaded_franchise` starts with `TUNE-`.
6. **Routes (`spike-goto --to SCREEN`).** This is `Pilot.goto` over the catalog, counting each hop as a step. It is for getting back to known ground.

`spike-start` refuses (exit 1, `{"ok": false, "refused": ...}`) when:
- the brief doesn't validate;
- `pipeline.watch_save` is running;
- a franchise is open and isn't the brief's `TUNE-*`;
- the save file is missing from the saves folder;
- `state.json` already exists with `stopped` unset (a spike already running).

If Madden is not running, it runs `jobs.launch(pack)`. With a `save`, it loads it through `jobs._load_franchise`, then `goto(brief.start)`.

### 2.4 Record and report

**Driver** (`win_agent.py`): `record --title T --dir D --seconds S --fps F [--width 960]`, with `S` 1..`MAX_RECORD_S` (30) and `F` 1..`MAX_RECORD_FPS` (10). It refocuses once and then grabs frames with mss to `D/frame_NNNN.jpg` (JPEG at quality 80, resized to `--width`). It checks `abort_requested()` before every frame. It returns `{"ok": true, "dir": D, "frames": n, "fps_actual": x, "size": [w, h]}`. `win.py` adds `--dir` to `PATH_OPTIONS`.

**WSL** (`spike.py`): `build_clip(dir, out_gif, out_sheet, fps)` uses Pillow to write an animated GIF (every frame, 480 px wide) and a contact sheet PNG (up to 24 frames evenly spaced, in a 6-column grid, each labelled with its timestamp). `spike-record --seconds S --fps F [--note TEXT]` writes `data/spikes/<id>/clips/NNN/` and records the clip in `state.json["clips"]`.

**Report:** the agent writes `data/spikes/<id>/report.json`. `check_report(path, brief, run_dir) -> list[str]` returns the problems, and an empty list means it passes. `spike-end` refuses a report that has problems and says which. Shape:

```json
{
  "id": "...", "status": "answered | partial | stopped",
  "answers": [{"question": "...", "answer": "...", "evidence": ["steps/0012_UNKNOWN.png", "clips/001/sheet.png"]}],
  "route": [{"from": "HUB_WEEKLY", "keys": [["c", 3], ["confirm", "TEAM SETTINGS"]], "to": "UNKNOWN@0015"}],
  "proposed_screens": [{"name": "TEAM_SETTINGS", "all": ["TEAM SETTINGS"], "any": [], "none": [],
                        "options": ["..."], "seen": "steps/0015_UNKNOWN.png", "note": "..."}],
  "stops": ["hard:TRADE at step 31"],
  "notes": "..."
}
```

`check_report` requires:
- one answer per brief question (the answer may be `"not found"`);
- every `evidence` and `seen` path exists under the run dir;
- `proposed_screens` names that are upper snake case and not already in the catalog;
- `status: "stopped"` whenever `state.stopped` is a hard stop or a cap.

`spike-end` then writes `report.md`: a plain rendering for the requester, with the answers, the route, the proposed screens and image links.

### 2.5 CLI output contract

Every `spike-*` job prints one JSON object on stdout and returns exit 0 (ok), 1 (refused or stopped) or 2 (unknown screen after a look; this is information, not an error). The keys are always present:

```json
{"ok": bool, "job": "spike-key", "step": int, "steps_left": int, "minutes_left": float,
 "screen": "UNKNOWN", "lines": ["..."], "highlight": "TEAM SETTINGS" | null,
 "png": "data/spikes/<id>/run/steps/0015_UNKNOWN.png", "refused": "reason" | null, "stopped": "reason" | null}
```

`highlight` is `border_highlight` over the frame's OCR lines, so an agent with no image input can still tell what is selected. Every action job ends with a look, so the agent always gets the new frame back.

### 2.6 Skill (`/madden-spike`)

`SKILL.md` covers:
- session start;
- the D3 approval message (word for word, with the time estimate: max_minutes plus a launch of 1–2 min when Madden isn't running);
- the CLAIM;
- the loop, with rules for the agent:
  - only `spike-*` jobs, never `win_agent`, `win.call` or `tmux`;
  - one action per look;
  - Back out of anything that looks like a decision the brief didn't name;
  - record a clip when a screen animates or changes on its own;
  - stop early once every question is answered;
- writing the report;
- `spike-end`;
- RELEASE, HANDOFF and sign-off.

It repeats the `/madden` hard rules: never roster cards, never online, buy or delete, never confirm a label the brief didn't name. It also says what to do on a refusal: read the reason and choose another action. Never retry the same command unchanged.

The `.agents/skills/madden-spike/SKILL.md` copy has the same body. Only the frontmatter and the line on how a Codex or Argon session starts are different.

### 2.7 Vision description (`tools/madden_pilot/vision.py`)

```python
@dataclass(frozen=True)
class ScreenDescription:
    kind: str            # one of SCREEN_KINDS: "menu", "dialog", "hub", "gameplay", "replay_or_cutscene",
                         # "loading", "pause_menu", "postgame", "other"
    title: str | None    # the heading as the model reads it
    options: tuple[str, ...]   # selectable items, in on-screen order
    selected: str | None       # the item the model sees as highlighted
    overlays: tuple[str, ...]  # e.g. "scorebug bottom-center", "network watermark top-right", "banner"
    warning: str | None        # anything about delete/online/purchase/sign-in the model sees
    summary: str               # one sentence
    model: str; seconds: float

def describe(png: str | Path, *, backend: str | None = None, model: str | None = None,
             timeout: float = 30.0, client=None) -> ScreenDescription
```

- **Backend:** `MU_SPIKE_VISION` = `ollama:<tag>` (the default is the comparison test's winner, written to `data/spikes/vision_choice.json`) or `deepseek:<model>` (an OpenAI-compatible chat call with an `image_url` data-URI part, sent the same way as `dossier_pilot`'s DeepSeek arm, with `DEEPSEEK_API_KEY`). The live default is the small local model. `spike-analyze` uses `MU_SPIKE_VISION_HEAVY`.
- **Fallback:** `MU_SPIKE_VISION_FALLBACK` (default `deepseek:deepseek-flash` when `vision_choice.json` records `deepseek_images: true`, else `none`) is tried once when the primary raises `VisionUnavailable` or runs past its timeout. Each describe's `model` field says which backend answered, and the report counts DeepSeek calls.
- **Input:** the frame is downscaled to 1280 px wide before sending, which is enough for menu text and keeps it fast.
- **Answer:** the prompt asks for exactly the JSON fields above. `describe` checks the JSON, puts anything malformed into `summary` with `kind="other"`, and never raises on a bad answer (it raises `VisionUnavailable` only when the backend can't be reached).
- **Where it's called:** `spike-look --describe` (and `--describe` on every action job) adds `"vision": {...}` to the §2.5 output. When the backend is unavailable the key is `null` with a reason, and the spike goes on with OCR alone.
- **Guard rule 4b:** on UNKNOWN, if `vision.selected` is set and the outline reader names a different label, `spike-confirm` is refused (`"refused": "vision and border disagree"`).
- **Hard stops:** rule 2 also stops on `vision.warning` when it names one of `HARD_STOP_WORDS`. Vision can add stops; it can never remove one.

### 2.8 Asset checks (`tools/madden_pilot/assets.py`, `checks/asset_probe.py`)

```python
Region = tuple[float, float, float, float]       # x, y, w, h as fractions of the frame
def colour_share(png, region: Region, hue: tuple[float, float], min_sat=0.5, min_val=0.4) -> float
def crop_similarity(png, region: Region, reference_png, ref_region: Region | None = None) -> float  # 0..1, SSIM on grey after resize
def check_frame(png, spec: dict) -> dict      # {"status": "pass|fail|unknown", "measures": {...}, "why": str}
```

The `spec` lives in the brief's `checks` list:

```json
{"name": "afc_away_cyan_bevel", "region": [0.30, 0.86, 0.40, 0.12],
 "colour": {"hue": [180, 200], "pass_at": 0.02},
 "reference": {"pass": "verify_output/dynamic_presentation/.../afc_cyan.png",
               "fail": "verify_output/dynamic_presentation/.../native_white.png", "margin": 0.05}}
```

- **Pass and fail:** a check passes when every given measure passes: the colour share ≥ `pass_at`, and the similarity to `pass` beats the similarity to `fail` by `margin`. It fails when every measure points the other way, and is unknown otherwise.
- **Pure functions:** these are Pillow plus numpy, with no model. `numpy` is already in `.venv`; U4 confirms that and uses only Pillow if it isn't.
- **`checks/asset_probe.py`:**
  - `run(pilot)` reads the spec from `MU_ASSET_PROBE` (a path) for `verify-mods`;
  - `run_frame(png, spec)` is what `spike-check` and `spike-analyze` call.

### 2.9 In-game flow and the later pass

- **Checkpoints.** A brief may list `checkpoints`: `[{"name": "kickoff", "when": "the gameplay HUD is first visible", "checks": ["afc_away_cyan_bevel"], "record_s": 5}]`. When the agent believes it has reached a checkpoint (vision `kind == "gameplay"` with a scorebug overlay, say), it runs `spike-check --checkpoint kickoff`. That captures a full-resolution frame and an optional clip, runs the named checks, and records the result in `state.json["checkpoints"]`.
- **Game flow keys.** A brief for game flow names its extra keys in `allow_keys` with `on_words` or `on_kind` (a vision `kind`). An example: `esc` to pause only `on_kind: "gameplay"`. `on_kind` alone never allows Space or Enter. Confirms still need an OCR label.
- **`spike-analyze --id <id>`.** It refuses while `Madden27.exe` runs (the full GPU is wanted). It runs `MU_SPIKE_VISION_HEAVY` over every step frame and checkpoint frame and runs every check again. It writes `analysis.json` and adds an "After-run analysis" section to `report.md`. The spike session runs it after `spike-end` and after JR (or `quit`) closes Madden. If Madden stays open, the requester can run it later.
- **Launch.** `"launch": "pilot" | "jr"`. With `jr`, `spike-start` waits up to 10 minutes for the Madden window instead of launching, and the approval message tells JR to start the pack (S13).

### 2.10 Vision comparison test (`tools/madden_pilot/vision_bench.py`)

`python3 -m tools.madden_pilot.vision_bench --models ollama:gemma4:e4b,ollama:qwen3.5:4b,... [--labels data/spikes/bench_labels.json] [--out data/spikes/bench/]`

- **Labels:** `build_labels()` makes the label set from what we already know:
  - every pilot `steps/NNNN_<SCREEN>.png` with a catalogued screen gives the expected `kind`, `options` (from the catalog) and `selected` (from `border_highlight` where the screen uses `border`);
  - JR's named presentation captures give the expected overlay (`scorebug_layout`: `standard`, `sn`, `mn`, `tn`) from a hand-written `bench_presentation_labels.json` of about 20 frames.
- **Scoring:** each model gets a `kind` accuracy, `options` recall, a `selected` exact match, a scorebug-layout accuracy, a median and 95th-percentile time, and video memory used (`nvidia-smi` before and during).
- **Output:** `bench.md` and `vision_choice.json`, which name the live pick (the best score within a memory budget of 5 GB) and the heavy pick.
- **Images:** a model that refuses an image is reported as "no vision" and skipped.

## 3. Risks and unknowns

| # | Risk or unknown | How we find out | If it goes wrong |
|---|---|---|---|
| R1 | No installed small model reads Madden's menus well enough. | S-A's comparison test. | Pull a Qwen-VL 7–8B 4-bit model. If none fits beside Madden, use DeepSeek live (if it takes images) and keep local models for `spike-analyze`. |
| R1e | The DeepSeek API is text-only, or it drops images through its OpenAI-compatible endpoint. | S-A step 1b. | The fallback is `none` (OCR plus the outline reader). JR decides separately on Gemini. |
| R1b | The vision model invents a highlight or an overlay. | Comparison test `selected` accuracy. Rule 4b's disagreement refusals in real spikes. | It never authorizes a key (§1 Out). A wrong description costs a refused confirm, not a bad press. |
| R1c | The live model and Madden fight over video memory (stutter, or Ollama falling back to CPU). | S-A measures memory with Madden in gameplay. | Live describes only on request (`--describe`), not on every look. Otherwise DeepSeek live and heavy work afterwards. |
| R1d | The asset-check regions differ between camera angles, the scorebug's animation states and resolutions. | Checks are written from the 103 existing captures. The first live spike records a clip at each checkpoint. | `unknown` rather than a wrong pass, from the both-ways rule in §2.8. Regions are tuned in the brief, not in code. |
| R2 | `border_highlight` misreads the selection on new screens (some menus use a fill, not an outline). | The unit tests use catalogued spike frames. Live: the first real spike. | The border can't be read → no confirm (refused). The agent reports it, and the requester catalogues the screen with `highlight: ocr_order`. |
| R3 | A hard-stop word lists too much and stops harmless screens (for example `TRADE` on a League tab heading). | The first spikes' stops. | Each stop is in the report. The requester narrows `HARD_STOP_WORDS` in a follow-up commit, never the spike agent. |
| R4 | mss at 10 fps on a 2560×1440 window may not keep up while Madden renders. | `record` returns `fps_actual`. Spike S-A records 5 s. | Lower `MAX_RECORD_FPS` to the rate measured live. |
| R5 | Something destructive the word list doesn't name. | — | Only `TUNE-*` clones are loaded, so the worst case is a spoiled clone, which `clone-save` rebuilds. The abort gestures always work. |
| R6 | A spawned DeepSeek session's own permission prompts slow the loop. | The first spike. | JR may allow `python3 -m tools.madden_pilot spike-*` for that window. That is a choice for JR, and the plan doesn't change settings. |

**Spike first:** S-A, before wave 1, about 30–45 minutes, done by whoever orchestrates (offline apart from step 3).
1. **Which installed models take images.** Send one catalogued frame to each Ollama tag through `/api/generate` with `images` set, using a throwaway script in scratch.
   1b. **Does DeepSeek take images.** Send the same frame to `deepseek-flash` once, with an `image_url` part, under JR's existing `DEEPSEEK_API_KEY`. Record `deepseek_images: true|false` (and its describe quality on the 30 frames if true). It costs about one cent.
2. **A small offline comparison.** About 30 labelled frames (catalogued pilot steps plus 10 presentation captures). Score each image-capable model and one Qwen-VL 7–8B 4-bit pull by hand. This answers R1 and chooses the U4 default. The full `vision_bench` comes in U4.
3. **With JR at the PC and under `CLAIM · Madden PC`:**
   - run Madden into a Quick Play game and leave it running;
   - read `nvidia-smi`, and time one describe on the live model (R1c);
   - time a 5-second mss burst from the Windows venv (R4).

The results set `vision_choice.json`'s first values, the live/heavy split and `MAX_RECORD_FPS`. They don't change the units.

## 4. Tests (synthetic, no live DB)

- `tests/test_madden_pilot_win.py` (extended): `record` refuses bad `--seconds`/`--fps`, stops on the abort gesture partway (the fake `win32api`), and writes N frames with a fake mss. `win.convert_args` converts `--dir`.
- `tests/test_madden_pilot_spike.py` (new):
  - `load_brief` refusals, one per rule in §2.2;
  - hard-stop words stop the run, but not inside `TICKER_BAND`;
  - key rules: an arrow is allowed, Space and Enter are refused, Q is refused without `on_words` and allowed with them, and catalog `forbidden_keys` win;
  - `spike-confirm` on UNKNOWN: allowed with the border on the label; refused with the border elsewhere, an unreadable border, the label not on the frame, or `on_words` missing;
  - on a catalogued screen it goes to `confirm_on` (a `ROSTER_DECISION_PROMPT` confirm is refused);
  - on the franchise list only the brief's tile may be confirmed;
  - caps (steps, and minutes with a fake clock);
  - state persists across two guard instances, and a stopped spike refuses everything;
  - `spike-start` refusals (watcher running, a non-TUNE franchise open, a spike already running);
  - `build_clip` writes a GIF and a contact sheet from synthetic frames;
  - `check_report` finds a missing question, missing evidence, a duplicate screen name, and a wrong `status` after a hard stop;
  - the CLI JSON contract has every key.
- `tests/test_madden_pilot_spike.py` also covers:
  - rule 4b (vision and border disagree → refused);
  - a vision warning adds a hard stop, and vision can't remove one;
  - `on_kind` keys (`esc` allowed on gameplay, refused elsewhere, never Space);
  - `spike-check` records checkpoint results;
  - `spike-analyze` refuses while Madden runs (a fake process list);
  - `vision: null` when the backend is down;
  - `launch: "jr"` waits for the window.
- `tests/test_madden_pilot_vision.py` (new):
  - `describe` with a fake client: good JSON, malformed JSON goes to `kind="other"`, an unknown `kind` is coerced, and an unreachable backend raises `VisionUnavailable`;
  - the fallback chain: the primary is down → DeepSeek answers and `model` names it; the fallback is `none` → `VisionUnavailable`; `deepseek_images: false` → no DeepSeek call;
  - the DeepSeek request carries the image as a data-URI part (a fake OpenAI client);
  - the downscale width;
  - the backend parsing of `MU_SPIKE_VISION`;
  - `vision_bench` scoring on three fake models and five labelled frames, with the memory-budget pick.
- `tests/test_madden_pilot_assets.py` (new):
  - `colour_share` on synthetic images (a pure cyan block → 1.0, grey → 0);
  - `crop_similarity` (identical → 1, noise → low);
  - `check_frame` pass, fail and unknown (mixed measures);
  - `asset_probe.run` reads `MU_ASSET_PROBE` and returns unknown with no spec.
- `tests/test_madden_pilot_jobs.py`: the existing dry-run test runs unchanged (it guards `__main__.py`).

Run with `pytest -m "not integration" tests/test_madden_pilot_win.py tests/test_madden_pilot_spike.py tests/test_madden_pilot_vision.py tests/test_madden_pilot_assets.py tests/test_madden_pilot_nav.py tests/test_madden_pilot_jobs.py tests/test_madden_pilot_catch_up.py`. No database or live model is touched; vision tests use a fake client.

## 5. Work breakdown

Shared files (orchestrator only, never owned by a unit): `PROJECT_SPEC.md`, this plan, `CLAUDE.md`, `docs/reference/AGENTS.md` (S10).

### U1: driver `record`

- **Owns:** `tools/madden_pilot/win_agent.py`, `tools/madden_pilot/win.py`, `tests/test_madden_pilot_win.py`
- **Reads:** `docs/reference/MADDEN_PILOT.md` §1, this plan §2.4
- **Depends on:** none
- **Done when:** `win_agent record` exists with the §2.4 flags, caps, abort check and output, and `win.py` converts `--dir`.
- **Verify:** `pytest -m "not integration" tests/test_madden_pilot_win.py` passes.

### U4: vision connector, comparison test and asset checks

- **Owns:** `tools/madden_pilot/vision.py` (new), `tools/madden_pilot/vision_bench.py` (new), `tools/madden_pilot/assets.py` (new), `tools/madden_pilot/checks/asset_probe.py` (new), `tests/test_madden_pilot_vision.py` (new), `tests/test_madden_pilot_assets.py` (new)
- **Reads:** `engines/llm_provider.py` (`call_ollama`; the first draft said `newsroom/`, which does not exist), `tools/dossier_pilot.py` (the DeepSeek client set-up), `newsroom/tool_loop/openai_loop.py`, `tools/madden_pilot/screens.py` (`border_highlight`), `tools/madden_pilot/checks/first_down_line.py`, `catalog.json`, the S-A notes, this plan §2.7, §2.8 and §2.10
- **Depends on:** none
- **Done when:** `describe`, `ScreenDescription`, `SCREEN_KINDS` and `VisionUnavailable` exist exactly as in §2.7. `colour_share`, `crop_similarity` and `check_frame` exist as in §2.8, and `vision_bench --help` runs. Every U4 test passes with fake clients and synthetic images.
- **Verify:**
  - `pytest -m "not integration" tests/test_madden_pilot_vision.py tests/test_madden_pilot_assets.py` passes;
  - `.venv/bin/python3 -m tools.madden_pilot.vision_bench --help` exits 0.

### U2: guard, clips, report and CLI jobs

- **Owns:** `tools/madden_pilot/spike.py` (new), `tools/madden_pilot/__main__.py`, `tests/test_madden_pilot_spike.py` (new)
- **Reads:** `tools/madden_pilot/navigate.py`, `screens.py`, `jobs.py` (`launch`, `_load_franchise`, `watcher_running`, `tile_name`, `_leave_to_main_menu`), `__main__.py` (`ScriptedMadden`, `build_parser`), merged `vision.py` and `assets.py`, this plan §2.2–2.9
- **Depends on:** U4 (it imports `describe`, `SCREEN_KINDS` and `check_frame`). It builds against U1's `record` contract through a fake driver.
- **Done when:** the eight `spike-*` jobs and `spike-analyze` run with `--dry-run` on `ScriptedMadden`, and every §4 spike test passes. `ScriptedMadden` grows a `record` answer that writes three blank frames, and the dry run uses a fake vision backend.
- **Verify:**
  - `pytest -m "not integration" tests/test_madden_pilot_spike.py tests/test_madden_pilot_jobs.py tests/test_madden_pilot_nav.py tests/test_madden_pilot_catch_up.py` passes;
  - `.venv/bin/python3 -m tools.madden_pilot spike-start --brief <a synthetic brief in tmp> --dry-run` prints `"ok": true`.

### U3: skills and guide

- **Owns:** `.claude/skills/madden-spike/SKILL.md` (new), `.agents/skills/madden-spike/SKILL.md` (new), `docs/reference/MADDEN_SPIKE_AGENT.md` (new), `docs/reference/MADDEN_PILOT.md`, `.claude/skills/madden/SKILL.md`
- **Reads:** this plan, `.claude/skills/madden/SKILL.md`, `.claude/skills/deepseek/SKILL.md`, `.agents/skills/codex/SKILL.md`, `tools/spawn_agent.sh`, U2's merged `spike.py` and `--help`, `DYNAMIC_PRESENTATION_RESEARCH.md` (for the worked example)
- **Depends on:** U2 (it documents the exact commands and refusal texts)
- **Done when:**
  - both skills cover §2.6;
  - the guide holds:
    - the brief and report formats;
    - how to request a spike (the §2.1 steps 1 and 6, including the DeepSeek requester who asks on the board);
    - the guard rules;
    - the vision backends and how to change them;
    - asset-check specs;
    - one worked presentation brief (the AFC-away cyan bevel at kickoff, `launch: "jr"`);
  - `MADDEN_PILOT.md` gets a §8 "Spikes" pointer and the `record` row in §1;
  - `/madden`'s skill says "for an unknown screen you need explored, request a spike".
- **Verify:** `grep -n "spike-start\|spike-confirm\|spike-check\|spike-analyze\|spike-end" .claude/skills/madden-spike/SKILL.md .agents/skills/madden-spike/SKILL.md docs/reference/MADDEN_SPIKE_AGENT.md` shows each command, and every command quoted matches `python3 -m tools.madden_pilot --help`.

## 6. Waves

| Wave | Units | Needs merged first | Why together |
|---|---|---|---|
| 0 | Spike S-A | — | It chooses the vision models and the frame-rate cap. |
| 1 | U1, U4 | — | No shared files, and no dependency between them. |
| 2 | U2 | U4 | It imports vision and the asset checks, and uses U1's `record` through a fake. |
| 3 | U3 | U2 | The docs quote U2's real commands and refusals. |

U3 is small enough for the orchestrator to write inline after wave 2, which saves a wave.

**File ownership check:**

| File | Unit |
|---|---|
| `.agents/skills/madden-spike/SKILL.md` | U3 |
| `.claude/skills/madden-spike/SKILL.md` | U3 |
| `.claude/skills/madden/SKILL.md` | U3 |
| `docs/reference/MADDEN_PILOT.md` | U3 |
| `docs/reference/MADDEN_SPIKE_AGENT.md` | U3 |
| `tests/test_madden_pilot_assets.py` | U4 |
| `tests/test_madden_pilot_spike.py` | U2 |
| `tests/test_madden_pilot_vision.py` | U4 |
| `tests/test_madden_pilot_win.py` | U1 |
| `tools/madden_pilot/__main__.py` | U2 |
| `tools/madden_pilot/assets.py` | U4 |
| `tools/madden_pilot/checks/asset_probe.py` | U4 |
| `tools/madden_pilot/spike.py` | U2 |
| `tools/madden_pilot/vision.py` | U4 |
| `tools/madden_pilot/vision_bench.py` | U4 |
| `tools/madden_pilot/win.py` | U1 |
| `tools/madden_pilot/win_agent.py` | U1 |

No path appears twice.

## 7. Spec changes when built

- The pilot section of PROJECT_SPEC (`tools/madden_pilot`):
  - add `record` to the driver's commands;
  - add `spike.py`, the eight `spike-*` jobs and `spike-analyze`;
  - add `vision.py` with `MU_SPIKE_VISION` / `MU_SPIKE_VISION_HEAVY` / `MU_SPIKE_VISION_FALLBACK` (Ollama, with DeepSeek as the fallback), `vision_bench.py`, `assets.py` and `checks/asset_probe.py` (`MU_ASSET_PROBE`);
  - spike runs live in `data/spikes/<id>/`, and the model pick in `data/spikes/vision_choice.json`.
- Repository Layout:
  - `tools/madden_pilot/spike.py`, `vision.py`, `vision_bench.py`, `assets.py`, `checks/asset_probe.py`;
  - the `__main__.py` CLI line gains the `spike-*` jobs;
  - `.claude/skills/madden-spike/` (the skills block, next to `madden/`) and `.agents/skills/madden-spike/`;
  - `tests/test_madden_pilot_spike.py`, `test_madden_pilot_vision.py`, `test_madden_pilot_assets.py`;
  - `docs/reference/MADDEN_SPIKE_AGENT.md` in the reference list.
- `docs/reference/AGENTS.md` (orchestrator): a `/madden-spike` row under Built.

## 8. Decisions for JR

| # | Question | Recommendation |
|---|---|---|
| Q1 | Is `HARD_STOP_WORDS` (§2.3 rule 2) the right starting list? `TRADE` and `NEGOTIATE` will stop some harmless League screens. | Keep it strict to start, and narrow it from real stops (R3). |
| Q2 | Caps: 200 steps and 45 minutes at most per spike, with briefs defaulting to 80 steps and 15 minutes. | As written. A longer job is a `/madden` job, not a spike. |
| Q3 | Should the requester fold proposed screens into the catalog itself, or hand them to a `/madden` session? | The requester does it, since it asked the question and has the context. |
| Q4 | Who builds it? Under M-0446's rule, `/execute-plan` builds go to a DeepSeek session. | Follow the rule: a DeepSeek builder, with `audit` reviewing. |

## 9. Build log

| Unit | Wave | Branch | Worktree | Result | Merge |
|---|---|---|---|---|---|
| U1 driver record | 1 | `worktree-agent-a5156ed21b7c1e65f` | `.claude/worktrees/agent-a5156ed21b7c1e65f` | merged; `pytest -m "not integration" tests/test_madden_pilot_win.py` 47 passed in the worktree and on `main` | `7e51183` |
