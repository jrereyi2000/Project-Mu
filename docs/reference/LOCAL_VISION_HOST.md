# Local vision host: Ollama on the M1 MacBook Pro

**Status: working and verified 2026-11-12; not adopted.** JR's call the same day: the Mac's per-frame cost (6.2 s, §5) does not beat the PC's own model (2.2 s with Madden running, §5.1), so the PC stays the default vision host and nothing in the repo points at the Mac. This doc is kept because it records the measurements, the one-time setup and the traps, so the question is answered rather than reopened. `qwen2.5vl:7b` answers single Madden frames from this PC over the Tailscale tailnet, through the pilot's own vision path. Nothing is wired into a pilot run; §6 is how to point at it if it is ever wanted.

Unconfirmed: whether the LaunchAgent in §3.4 or a hand-exported `OLLAMA_KEEP_ALIVE=-1` is what keeps the model resident. Keep-alive is observably active either way (`/api/ps` reports `expires_at` in the year 2319), but which mechanism is in place was never established with JR.

Companion to [LOCAL_VISION_LATENCY.md](LOCAL_VISION_LATENCY.md), which measured the models on the PC's GPU and concluded that a 7B VLM is fast enough in principle but too weak to authorise a key. That conclusion stands and this doc does not change it. What this adds is a *host*: a machine that can serve a vision model without contending with Madden for the PC's GPU.

Measured and written by `deepseek-v4.1-flash (mac-vision-setup)`. JR typed every command on the Mac; this session ran the PC side and the measurements.

## 1. Why a separate host

The PC's RTX 5070 Ti has to carry Madden and the newsroom. A 6.5 GB model loading into it costs 8–75 s of contention on the first call and holds VRAM for as long as it is warm. The 2021 M1 MacBook Pro is always on, has 16 GB of unified memory, and reaches the PC over Tailscale, so it can hold the model resident indefinitely and answer in seconds without touching the PC's GPU.

**This is a latency and contention fix, not an accuracy fix.** See §9.

## 2. The two machines

| | Mac (`jrs-macbook-pro`) | PC (`mu-box`) |
|---|---|---|
| Role | Ollama server | client |
| Tailscale address | **100.87.79.110** | 100.82.39.8 |
| MagicDNS name | `jrs-macbook-pro.tailb0d9fb.ts.net` | `mu-box.tailb0d9fb.ts.net` |
| LAN address | 192.168.1.211 | 192.168.1.213 |
| Memory | 16 GB unified | — |
| OS | macOS | Linux (WSL2) |

The Mac is bound to its **Tailscale** address, not the LAN one and not `0.0.0.0`. A `100.x` tailnet address is tied to the node's identity, so it survives reboots, sleep and every network change; the LAN address does not. See §8 for why this matters more than the addressing convenience.

## 3. Mac side, one-time setup

Every command here runs in Terminal **on the Mac**. JR is the only one who types there.

### 3.1 Check whether it is already running on localhost

```bash
curl -s -m 3 http://127.0.0.1:11434/api/tags
```

JSON means the Ollama menu-bar app already has a server on `127.0.0.1:11434`. That matters in §3.4: two servers cannot share the port.

### 3.2 Bind to the tailnet address

```bash
tailscale ip -4                                        # expect 100.87.79.110
OLLAMA_HOST=100.87.79.110:11434 ollama serve           # foreground; leave the window open
```

On macOS `OLLAMA_HOST` is a **bind address**. The server then answers on the tailnet interface only — not on the LAN, not on loopback.

If macOS asks *"Do you want the application ollama to accept incoming network connections?"*, allow it.

### 3.3 Pull the measured model

```bash
OLLAMA_HOST=100.87.79.110:11434 ollama pull qwen2.5vl:7b
```

- **5.97 GB**, 5 layers, registry manifest `application/vnd.docker.distribution.manifest.v2+json`.
- The `OLLAMA_HOST` prefix is required once the server is no longer on loopback, because the CLI reaches the daemon over the same address.

> **The `v1` / `vl` trap.** The tag is `qwen2.5vl` — vee, then a **lowercase ell** for *vision-language*. Typing `qwen2.5v1` (vee-**one**) fails with `Error: pull model manifest: file does not exist`, which reads like a broken or unreachable server but is simply an unknown tag. The model's own family string is `qwen25vl`, which is the quickest way to check the spelling you have. The failed pull writes no partial data.

### 3.4 Keep it running: a LaunchAgent

A hand-typed `ollama serve` dies with its Terminal window and does not come back after a reboot. This makes it permanent, and sets keep-alive so the 8 s weight load is paid once at login rather than per idle timeout.

```bash
which ollama        # substitute the real path below; /usr/local/bin/ollama on Apple Silicon
mkdir -p ~/Library/Logs
cat > ~/Library/LaunchAgents/com.mu.ollama-vision.plist <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>              <string>com.mu.ollama-vision</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/ollama</string>
        <string>serve</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>OLLAMA_HOST</key>       <string>100.87.79.110:11434</string>
        <key>OLLAMA_KEEP_ALIVE</key> <string>-1</string>
    </dict>
    <key>RunAtLoad</key>          <true/>
    <key>KeepAlive</key>          <true/>
    <key>ThrottleInterval</key>   <integer>20</integer>
    <key>StandardOutPath</key>    <string>/Users/jrereyi/Library/Logs/ollama-vision.log</string>
    <key>StandardErrorPath</key>  <string>/Users/jrereyi/Library/Logs/ollama-vision.err.log</string>
</dict>
</plist>
PLIST
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.mu.ollama-vision.plist
launchctl print gui/$(id -u)/com.mu.ollama-vision | head -12
```

Why each setting earns its place:

- **`OLLAMA_KEEP_ALIVE=-1`** keeps the model resident forever. Without it the daemon unloads after its idle timeout and the next frame pays the load again. Confirmed in effect: `/api/ps` reports `expires_at` in the year **2319**, and `size_vram` 5.35 GB fully resident.
- **`KeepAlive` + `ThrottleInterval 20`** covers the one real race: at login launchd may start Ollama before Tailscale has assigned `100.87.79.110`, so the bind fails with *cannot assign requested address*. `KeepAlive` retries and the throttle stops it spinning. It is up within a minute of login, every time.
- **Quit the menu-bar app and remove it from System Settings → General → Login Items.** The `.app` starts its own server on `127.0.0.1:11434`; if it wins the port, the LaunchAgent dies with *address already in use* on every boot.
- **Logs to `~/Library/Logs/`**, not `/tmp`, which macOS periodically empties.

Restart after an env change: `launchctl kickstart -k gui/$(id -u)/com.mu.ollama-vision`. Remove: `launchctl bootout gui/$(id -u)/com.mu.ollama-vision`.

## 4. Verify from the PC

### 4.1 Reach it

```bash
curl -s -m 10 http://100.87.79.110:11434/api/version     # {"version":"0.40.6"}
curl -s -m 10 http://100.87.79.110:11434/api/tags        # the model list
curl -s -m 10 http://100.87.79.110:11434/api/ps          # what is resident
```

Measured: `/api/tags` **HTTP 200 in 0.094 s**. The network is not the cost; the model is.

### 4.2 One frame, raw curl

Builds the same payload the pilot builds — a pilot frame downscaled to 1280 px wide, base64, the pilot's own prompt.

```bash
cd ~/Project-Mu
python3 - <<'PY'
import base64, json
from PIL import Image
src = "data/pilot_runs/2026-11-10_015728_sim-season/steps/0009_HUB_WEEKLY.png"
with Image.open(src) as o:
    img = o.convert("RGB")
if img.width > 1280:
    img = img.resize((1280, round(img.height * 1280 / img.width)), Image.LANCZOS)
img.save("/tmp/frame_1280.png")
b64 = base64.b64encode(open("/tmp/frame_1280.png", "rb").read()).decode()
prompt = ('Describe this Madden NFL screen. Reply with one compact JSON object and no markdown:\n'
 '{"kind":"menu|dialog|hub|gameplay|replay_or_cutscene|loading|pause_menu|postgame|other",'
 '"title":string|null,"options":[string],"selected":string|null,'
 '"overlays":[string],"warning":string|null,"summary":string}')
json.dump({"model": "qwen2.5vl:7b", "prompt": prompt, "images": [b64], "stream": False,
           "options": {"temperature": 0}}, open("/tmp/payload.json", "w"))
print("payload ready:", img.width, "x", img.height)
PY
curl -s -m 300 -X POST http://100.87.79.110:11434/api/generate \
     -H 'Content-Type: application/json' --data-binary @/tmp/payload.json \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(round(d["total_duration"]/1e9,2), "s", d["eval_count"], "tokens"); print(d["response"])'
```

Expected (this exact frame and model produce a stable answer):

```
6.4 s 148 tokens
{"kind": "hub", "title": "Weekly Hub",
 "options": ["Manage Roster", "Coach Central", "League", "Options"],
 "selected": "Weekly Hub",
 "overlays": ["2026: Week 1 VS Bears (0-0-0)", "NFL Draft: Which rookies will we see in April's draft?", ...],
 "warning": null, ...}
```

The `overlays`/`options` split is the model's, and it is the right one here: the four rail items are options, the news ticker lines are overlays.

### 4.3 One frame, the pilot's own path

Proves the wiring in [vision.py](../../tools/madden_pilot/vision.py) reaches the Mac through `OLLAMA_HOST` — `_ollama_text()` (vision.py:198) calls `engines.llm_provider.get_ollama_base_url()`, which reads the env var and falls back to the project `.env` via `pipeline.notify.env`.

```bash
cd ~/Project-Mu
OLLAMA_HOST=http://100.87.79.110:11434 python3 - <<'PY'
from tools.madden_pilot import vision
d = vision.describe("data/pilot_runs/2026-11-10_015728_sim-season/steps/0009_HUB_WEEKLY.png",
                    backend="ollama:qwen2.5vl:7b", timeout=300)
print(d.model, "%.2fs" % d.seconds, d.kind, repr(d.selected))
print(d.title, d.options)
PY
```

Expected:

```
ollama:qwen2.5vl:7b 6.49s hub 'Weekly Hub'
Weekly Hub ('Manage Roster', 'Coach Central', 'League', 'Options')
```

A second frame, `2026-11-10_124258_sim-season/steps/0003_FRANCHISE_LIST.png`, reads `menu` / `CURRENT FRANCHISES` / selected `TUNE-P2C-AUTOSAVE` with the four `TUNE-*` tiles as options — correct, and the screen that mod-checking actually needs.

## 5. Measured

2026-11-12, 16 GB M1 MacBook Pro, `qwen2.5vl:7b` Q4_K_M (8.3B), `context_length` 4096, fully resident in unified memory. Times are the frame round trip from this PC, `temperature 0`, 1280×720 input.

| | seconds |
|---|---|
| Cold (includes weight load) | **27.4** (8.3 s of it loading) |
| Warm, Weekly Hub frame ×3 | 6.83 / 6.41 / 6.49 — **median 6.49** |
| Warm, Franchise List frame ×3 | 6.20 / 5.68 / 5.98 — **median 5.98** |
| Through `vision.describe()` ×3 | 6.83 / 6.41 / 6.49 — **median 6.49** |
| Network, `/api/tags` | 0.094 |
| Output size | 131–148 tokens a frame; **24.1 tok/s** decode |

Two things to read off that:

- **~6.2 s a frame at steady state, and it is all model time.** The tailnet adds about a tenth of a second, so a faster Mac is the only lever on the rest. That is seconds-scale, which is what the visual mod-check use case needs.
- **One outlier at 16.52 s** on the Franchise List frame, then 5.68–6.20 s on three repeats of the same frame. Not reproducible, so not a token-count effect (131 tokens both times). Most likely contention on the Mac — both `qwen2.5vl:7b` (5.4 GB) and `gemma4:12b` (7.7 GB) have been resident in 16 GB at different times. **Budget p95 above the median, not at it**, and the pilot's default `describe` timeout is 30 s, which this comfortably clears.

For comparison, the model that was on the Mac first, `gemma4:12b`, is a *thinking* model and is disqualified for this use: **38.2 / 61.3 / 80.3 s** warm, 524–1179 tokens a frame, because its budget goes to reasoning before the answer. This matches LOCAL_VISION_LATENCY.md §2. Deleting it from the Mac would remove the memory contention above.

### 5.1 Against the PC, on the same frame, with Madden running

The Mac is **not** the faster host. Measured on the identical frame and model while a game was paused mid-play (`Madden27.exe`, 1.6 GB RAM, GPU at 43% utilisation, 7.4 GB of 16.3 GB already used):

| | same frame, `qwen2.5vl:7b` |
|---|---|
| PC, first call (model had been evicted) | **13.39 s**, of which **9.94 s loading** |
| PC, warm ×2 | **2.26 / 2.23 s** |
| Mac, warm ×3 (§5) | 6.83 / 6.41 / 6.49 — median **6.49 s** |
| PC GPU after load | **14,064 MiB of 16,303 — 86%** |

So the PC answers in about a third of the Mac's time even with Madden live, and it costs 86% of the GPU to do it. LOCAL_VISION_LATENCY.md §1's **0.4 s** is the wrong figure to compare against: that was one lucky frame, and the same doc's 202-frame median is **1.48 s**. Comparing 0.4 s to the Mac's median overstated the gap by about 5×; the real ratio is roughly 3×.

The reason to host on the Mac is therefore **isolation, not speed**: on the PC the model and Madden share one 16 GB pool, the model alone takes it to 86%, and every time Madden needs the memory back the next frame pays a ~10 s reload. The Mac pays its 8 s load once in the morning and then keeps the model resident forever, and Madden never sees it. What is still unmeasured, and is the real argument either way: **whether a resident VLM causes in-game stutter on the shared GPU**. A paused game is not a game in play, so 2.23 s is a floor, not the number during a snap. If JR would rather have the 3× and accepts the contention, `OLLAMA_HOST=localhost` goes straight back to the PC's model and this Mac host becomes optional.

## 6. Pointing Project Mu at it

In the project `.env` on the PC (not committed):

```
OLLAMA_HOST=http://100.87.79.110:11434
```

`get_ollama_base_url()` accepts it with or without the scheme and appends `:11434` when no port is given, so `OLLAMA_HOST=100.87.79.110` also works. The same value can be exported for one command, as in §4.3, without touching `.env`.

**This is a switch, not an addition.** This PC also has `qwen2.5vl:7b` on its own Ollama at `localhost:11434`, and `OLLAMA_HOST` is read by every Ollama caller in the repo, not just vision. Setting it routes *all* of them to the Mac and bypasses the local model. To revert, remove the line (the default is `http://localhost:11434`, `engines/llm_provider.py:88`).

## 7. Gotchas worth knowing before they bite

1. **`OLLAMA_HOST` means opposite things on the two machines.** On the Mac it is a **bind address** (`100.87.79.110:11434`, no scheme). On the PC it is a **client URL** (`http://100.87.79.110:11434`, scheme optional). The same variable name, two different jobs.
2. **`qwen2.5vl`, not `qwen2.5v1`** — see §3.3.
3. **Port 11434 is single-tenant.** The menu-bar app and the LaunchAgent will fight over it; only one can own it (§3.4).
4. **Thinking models are unusable here** regardless of host — `gemma4:12b` and the `qwen3.5` family spend 5–80 s a frame on reasoning tokens.
5. **`vision.py` never sends `format: "json"`** (vision.py:201) because it broke `qwen3.5:4b`'s answers; the model returns a fenced JSON block and `parse_answer()` tolerates the fence. A raw `curl` therefore shows fences that `describe()` strips.
6. **Binding to a tailnet address means the Mac itself must use that address too.** `ollama pull` from the Mac needs the same `OLLAMA_HOST` prefix (§3.3), because loopback is no longer served.

## 8. Security

- **Tailnet only.** The server binds `100.87.79.110`, so it is reachable from the devices on JR's tailnet and nowhere else. Not the LAN, not the internet.
- **No port forwarding, no router changes, no inbound firewall rules.** Tailscale is outbound-only NAT traversal; nothing is exposed to the public internet.
- **`0.0.0.0` was rejected.** It also listens on whatever Wi-Fi the Mac joins — campus, café, hotel, airport — and on every device on the home network, including anything compromised on it.
- **Ollama has no authentication.** Anyone who can reach the port can run any model on the Mac and, more to the point, can send it images and read the answers back. The tailnet address is the entire access control: treat it like a credential, and post `CLAIM · Madden PC` before a run that sends real frames, because frames of a franchise screen name saves and players.
- **What a frame can disclose:** these are screenshots of Madden and, if a run is careless, of the desktop. Nothing else in the repo's vision path sends files, and `image_bytes()` caps frames at 1280 px wide. Binding to the tailnet keeps even that inside JR's own devices.
- Should the Mac ever need to serve a non-tailnet client, prefer an SSH tunnel (`ssh -L 11434:100.87.79.110:11434 jrs-macbook-pro`) over widening the bind.

## 9. What this does not establish

- **Accuracy on 202 frames is not re-measured here.** Two frames were read correctly and consistently, which proves the host works, not that the model is accurate. The measured rates remain LOCAL_VISION_LATENCY.md §2: screen kind **0.490**, options recall **0.593**, highlighted item **0.169**, over 202 real frames. Two easy frames agreeing with the catalog is not evidence against that.
- **Nothing is wired in.** No default was flipped; `default_backend()` (vision.py:103) still resolves to `MU_SPIKE_VISION`, then `data/spikes/vision_choice.json`, then `ollama:qwen3.5:4b`. Setting `MU_SPIKE_VISION=ollama:qwen2.5vl:7b` (with `OLLAMA_HOST` pointed at the Mac) is the deliberate way to try it in the spike agent.
- **Vision still never authorises a key.** The guard's `border_highlight` reader stays the deciding reader (vision.py:26–27). A model that will not press `confirm` on *PRESS ANY BUTTON* is unaffected by being served from a faster machine.
- **The fine-tuning route is untouched.** LOCAL_VISION_LATENCY.md §4.4 remains the recommendation for accuracy; this host makes a fine-tuned small model cheap to serve.
