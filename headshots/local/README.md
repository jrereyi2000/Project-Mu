# Project Mu — Local Headshot Generation Pipeline

Standalone local portrait generation engine for Project Mu player and rookie headshots, designed to transition Project Mu from cloud image generation (`gemini-nano-banana-2.1`) to a dedicated local GPU (RTX 5070 Ti 16GB VRAM).

References:
- `docs/in_progress/PC_MIGRATION_PLAN.md` §7.7
- `docs/not_started/PRE_PC_WORK_2.md` Item A1

---

## 1. Architecture & Overview

In the cloud pipeline, player headshots are rendered via Gemini API calls in `headshots/make_headshot.py` and `headshots/rookie_headshots.py`. The local pipeline replaces only the image generation call with zero changes to downstream steps:
- Prompts, appearance traits (`headshots/headshot_appearance_generator.py`), team jersey colors (`team_colors()`), `rembg` background cutout, and WebP compression (`static/players/{player_id}.webp`) remain identical.
- Free re-renders: No per-image API costs or rate-limiting batch queues.
- Execution speed: ~10–30s per 1024px portrait on an RTX 5070 Ti (a full rookie class of 250–300 players in 1–2 hours; an entire league of 1,829 players in one overnight run).

### Package Layout

```
headshots/local/
├── __init__.py      # Public exports
├── generator.py     # Core function interface `generate_headshot(...)`
├── backends.py      # HeadshotBackend, MockBackend, DiffusersBackend, ComfyUIBackend
├── adapter.py       # Integration hooks for make_headshot.py and rookie_headshots.py
├── cli.py           # Standalone CLI tool
└── README.md        # Documentation and specification
```

---

## 2. Core Python Interface

```python
from headshots.local import generate_headshot

png_bytes: bytes = generate_headshot(
    prompt="photorealistic NFL media day headshot of a quarterback...",
    ref_image=None,      # Optional face reference bytes for IP-Adapter
    seed=42,             # Reproducible generation
    size=1024,           # 1024x1024 resolution
    backend="mock",      # "mock" | "diffusers" | "comfyui" (or None for auto)
    config=None,         # Optional backend-specific settings
)
```

---

## 3. Backends

### `MockBackend`
- **Purpose**: Fast, deterministic unit tests, CI pipelines, and offline development without GPU dependencies.
- **Engine**: Pillow (`PIL`).
- **Behavior**: Generates valid PNG images with deterministic studio backdrops, player silhouette, jersey polygon, and metadata banner. If `ref_image` is supplied, composites an IP-Adapter reference badge.

### `DiffusersBackend`
- **Purpose**: Direct Hugging Face `diffusers` pipeline execution on local GPU.
- **Target Hardware**:
  - **PC (Target)**: NVIDIA RTX 5070 Ti (16GB VRAM, CUDA, fp16/bfloat16).
  - **Mac (Development)**: Apple Silicon MPS (fp16).
  - **Fallback**: CPU (fp32).
- **VRAM Optimizations**:
  - `pipe.enable_model_cpu_offload()`: keeps models under 16GB VRAM.
  - `pipe.enable_vae_slicing()` & `pipe.enable_vae_tiling()`.
- **Supported Models**: SDXL 1.0 base, Stable Diffusion 3.5 Medium, Flux-schnell.

### `ComfyUIBackend`
- **Purpose**: Client for an external ComfyUI server instance running locally at `http://localhost:8188` (or configured via `COMFYUI_HOST`).
- **Workflow**:
  1. Uploads `ref_image` to `/upload/image` multipart endpoint if provided.
  2. Constructs JSON API prompt graph with KSampler, CLIP text encode, and empty latent.
  3. Submits prompt via `POST /prompt`.
  4. Polls `GET /history/{prompt_id}` until execution completes.
  5. Downloads generated portrait from `GET /view`.

---

## 4. Conditioning & Consistency Hooks (§7.7)

### Pose & Depth ControlNet Hook
Guarantees consistent head framing, eye level, angle, and crop across all players:
- In-game portrait conversion (§9.4) requires exact positioning to prevent awkward framing in game UI screens.
- Enabled via `config={"controlnet": True, "controlnet_model": "..."}` or CLI `--controlnet`.

### IP-Adapter & Face Identity Hook
Maintains player facial likeness across seasons and career milestones:
- Prevents player faces from drastically changing when re-rendered after trades, contract extensions, or veteran aging.
- Enabled automatically whenever `ref_image` bytes are provided.
- Diffusers loads IP-Adapter face weights (`ip-adapter-plus-face_sdxl`) and passes `ip_adapter_image`.
- ComfyUI wires `IPAdapterModelLoader` and `IPAdapterApply` nodes into the workflow graph.

---

## 5. Integration Adapter (`adapter.py`)

The pipeline seamlessly hooks into existing headshot scripts when activated via environment variables:

```bash
# Enable local headshot generation
export USE_LOCAL_HEADSHOTS=1
# Optional: choose backend (defaults to mock if not configured)
export LOCAL_HEADSHOT_BACKEND=diffusers
```

### Programmatic Usage

```python
from headshots.local.adapter import get_local_generator_if_enabled, patch_headshot_generator

# Option A: Check and call local generator
local_gen = get_local_generator_if_enabled()
if local_gen:
    png_bytes = local_gen(prompt, seed=42)

# Option B: Patch existing modules in-place
patch_headshot_generator()
# Now make_headshot() and rookie_headshots automatically use the local pipeline
```

---

## 6. Standalone CLI (`cli.py`)

```bash
# Test with deterministic mock backend
python -m headshots.local.cli --prompt "Quarterback for the Rams" --out test_qb.png --seed 42 --backend mock

# Generate using local ComfyUI instance
python -m headshots.local.cli --prompt "Wide receiver for the Chiefs" --out chiefs_wr.png --backend comfyui

# Face identity preservation with reference image
python -m headshots.local.cli --prompt "Veteran safety in Cardinals jersey" --ref-image rookie_photo.png --out veteran_safety.png

# ControlNet pose/depth conditioning
python -m headshots.local.cli --prompt "Linebacker for the Steelers" --controlnet --out steelers_lb.png
```

---

## 7. Running Tests

Unit tests require no external dependencies or database connection:

```bash
pytest tests/test_local_headshot.py -v
```
