# Local vision models for a live Madden pilot

**Question (JR, 2026-11-12):** is there a model fast enough to take live input — navigating a Madden game screen where latency matters, basic control — rather than the catalog driver?
**Answer, short:** not from the local models on the PC today. A 7B vision model is fast enough in principle and far too weak in practice; the catalog driver stays the live layer. The realistic path to a fast, accurate screen reader is fine-tuning a small VLM on the pilot's own labelled frames, which the repo already has 2,467 of.

Measured on JR's PC (RTX 5070 Ti, 16 GB), 2026-11-12, by `deepseek-v4.1-flash (muse-ember-plan-2)`. Nothing here is a production change.

## 1. Speed, single frame

One saved 1080p pilot frame, "name the screen, list the menu options, say what's highlighted":

| Model | Cold (loads weights) | Warm | Answer |
|---|---|---|---|
| `qwen2.5vl:7b` (6.0 GB) | 75.5 s | **0.4 s** | correct: *Weekly Hub*, options in order, *Weekly Hub* highlighted |
| `qwen3.5:4b` (3.3 GB, thinking) | 6.9 s | 0.7 s | empty — the output budget went to reasoning |

A warm 0.4 s read is genuinely live-pilot speed for menus: capture 0.1 s + model 0.4 s + key 0.05 s + settle 0.5–1 s ≈ **1–1.5 s an action**. Keep the model warm; the first call pays the load.

## 2. Quality, `vision_bench` over 202 real frames

`python3 -m tools.madden_pilot.vision_bench --models ollama:qwen2.5vl:7b --labels data/spikes/bench_labels_sample.json`
Sample: 202 frames, up to 10 per screen, across 24 catalogued screens of `data/pilot_runs`; 108 frames carry expected options and 71 a known highlighted item.

| | value |
|---|---|
| screen kind correct | **0.490** |
| options recall | **0.593** |
| highlighted item exact | **0.169** |
| median | **1.48 s** |
| p95 | 2.29 s |
| VRAM | 6.51 GB |

The one-frame success above was a lucky frame. Half the screens are named as the wrong *kind* and only one in six gets the highlight right — and the highlight is what any press depends on. It also exceeds the bench's 5 GB live budget, so the tool's own verdict was **live pick: none**, heavy pick: `qwen2.5vl:7b`.

`gemma4:e4b` and `qwen3.5:4b` were started on an 88-frame subset and stopped: both are thinking models, 5–10 s a frame, which disqualifies them for live use before quality matters.

## 3. Stress test: can a local model load a franchise into a game?

Harness: `/tmp/opencode/stress_load.py` (one-off, not production). The model picks the action from the frame and OCR text; the **catalog guards every press** — `confirm` only where `confirm_ok` allows it, `confirm` on the franchise list only with a `TUNE-` tile on screen, no letters, no mouse, no delete keys. Keys were mapped semantically (`confirm` → Space, `back` → Backspace) because a model cannot guess Madden's key legend, and it was told when its last answer was refused.

| Model | Result |
|---|---|
| `qwen2.5vl:7b` | **failed.** 30 actions, never once proposed `confirm` on a screen reading *PRESS ANY BUTTON*; cycled left/up/back/right and kept cycling even with the refusal fed back. 2.0–2.2 s a step (1.48 s model + capture). |
| `gemma4:e4b` | **failed.** 0.6–0.7 s a frame, but no parseable action at all on this prompt. |

Neither reached a loaded franchise. The guard did its job: zero unsafe presses, which is exactly why the failure is safe and visible.

**Byproduct worth fixing in the pilot:** the game's title screen after a profile error reads `OMADDEN±27` / `PRESS ANY BUTTON` / `PROFILE UNAVAILABLE`. The catalog's `TITLE_SCREEN` needs the word `MADDEN`, which that OCR never produces, so the frame classifies `UNKNOWN` — a cold start on that screen would stop or wait out its timeout. The stress harness special-cased `PRESS ANY BUTTON`.

## 4. What this means

1. **The catalog driver is the right live layer.** It is deterministic, ~1 s a look, no GPU, and it cannot hallucinate a confirm. A 7B VLM is slower than it, less accurate, and unsafe to trust with a press.
2. **Latency is not the blocker; judgement is.** 0.4–1.5 s is fine for menus. 49% screen-kind and 17% highlight accuracy is not, and neither is a model that will not press `confirm` on a screen that says *PRESS ANY BUTTON*.
3. **Menu flight, yes; gameplay, no.** Pre-snap reads and stick inputs need well under 100 ms. That is a scripted policy or RL over the save/memory, never a vision-language model.
4. **The promising route is fine-tuning, not a bigger download.** The pilot has **2,467 labelled frames** (24 screens, with options and highlighted item) — exactly the training set for a small screen-reader VLM or a fast classifier head. A fine-tuned 2–4B model would keep the 0.4 s and fix the accuracy, and could then be offered to a model only when the catalog is unsure.
5. **Cloud alternatives** (Gemini Flash class, DeepSeek V4.1 Flash on Go) sit at ~1 s plus network and are cheaper to try than a local fine-tune, but they add latency variance, a dependency and — for the Go Muse models — training terms. Worth one A/B on the same 202 frames before either is chosen.

## 5. Reproduce

```
python3 -m tools.madden_pilot.vision_bench --models ollama:qwen2.5vl:7b \
    --labels data/spikes/bench_labels_sample.json --out data/spikes/bench_sample
```
`data/spikes/bench_sample/bench.md` holds the table; `bench_labels_sample.json` and `bench_labels_small.json` are the sampled manifests. The full 2,467-frame manifest can be rebuilt with `vision_bench.build_labels()`.
