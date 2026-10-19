# Player Headshot Generation Architecture & Pipeline

This document details the end-to-end design, deterministic synthesis, batch generation, and post-processing pipeline used to produce photorealistic AI player headshots for all players in the league database.

---

## 1. Overview & Objectives

In Madden simulation and fictionalized league modes, players lack real-world photography. Rather than using generic silhouettes, 2D avatar placeholders, or inconsistent one-off generations, Project Mu employs a deterministic pipeline to produce broadcast-quality media-day headshots.

Key system constraints:
1. **Deterministic Reproducibility:** Every player's appearance is derived pseudo-randomly via SHA-256 hashes of their unique attributes (`name + team`), guaranteeing that re-running generation produces the exact same facial structure, hairstyle, and skin tone.
2. **Positional Demographics & Realism:** Appearance traits (skin tone, hair texture, facial hair, physical build) match realistic NFL demographic and position distributions.
3. **Official Team Branding:** Players wear their authentic franchise home jerseys, rendered with exact team primary and secondary hex colors and shoulder stripe detailing.
4. **Broadcast Media Day Framing:** Uniform studio lighting (key, fill, rim), 85mm full-frame portrait optics, and pure white seamless backdrops.
5. **High-Throughput Batch Inference:** Requests are compiled into JSONL batches for Google's Gemini Batch API (`gemini-nano-banana-2.1`), keeping costs low and avoiding rate limits.
6. **Alpha Matte Post-Processing & WebP Compression:** Output images have studio backgrounds removed via `rembg` (ONNX `silueta` model) and are compressed to transparent WebP files saved as `static/players/{player_id}.webp`.

---

## 2. Directory & File Structure

```text
headshots/
├── headshot_appearance_generator.py # Deterministic physical & facial feature generator
├── make_headshot.py                 # Single-image generation, team color cache, batch request builder
├── rookie_headshots.py              # Automated rookie headshot generation for seasonal pipeline
├── gen.py                           # Gemini Batch API submission and artifact downloader
└── process_headshots.py             # Multiprocess rembg cutout pipeline and WebP conversion
data/headshots/
├── batch_headshot_requests.jsonl    # Batch input payload (1,499 JSON requests)
├── batch_results.jsonl              # Batch output payload from Gemini (raw model responses)
└── players_backup/                  # Uncropped alpha cutout WebPs ({player_id}.webp)
static/players/
└── {player_id}.webp                 # Final transparent headshots served by the API (700x508 WebP)
```

---

## 3. Step-by-Step Generation Pipeline

### Step 1: Appearance Profile Synthesis (`headshot_appearance_generator.py`)

A player's appearance is synthesized using independent pseudo-random number generator (RNG) streams seeded by deterministic hashes:

```python
rng = random.Random(int(hashlib.sha256(f"{name}|{team}|{version}".encode()).hexdigest(), 16))
```

* **Skin Tone Distributions (`TONE_W`):** Five tones (`light`, `light-olive`, `medium tan`, `medium-deep brown`, `deep brown`) weighted according to NFL positional benchmarks (e.g., QBs and Kickers vs. CBs and RBs).
* **Positional Normalization (`POS_ALIASES`):** Normalizes Madden sub-positions (`HB` $\to$ `RB`, `LEDG`/`REDG` $\to$ `DL`, `MIKE`/`WILL`/`SAM` $\to$ `LB`, `LT`/`RT`/`LG`/`RG`/`C` $\to$ `OL`).
* **Hair & Grooming:** Position-weighted textures and styles (fades, dreadlocks, curls, buzz cuts) with age-dependent receding hairline probabilities for older veterans ($\ge 27$).
* **Physical Build:** Computes BMI from database height (`h_in`) and weight (`w_lb`):
  * BMI $< 26$: Lean build, slim neck, narrow shoulders.
  * BMI $26–29$: Athletic build, moderately thick neck, broad shoulders.
  * BMI $30–34$: Muscular stocky build, thick neck, very broad shoulders, fuller face.
  * BMI $\ge 35$: Very large build, very thick neck, extremely wide shoulders, round face.
* **Micro-Aging & Facial Imperfections:** Injects fine lines around eyes for older players and applies per-feature Gaussian jitter across jawline, nasal profile, eye shape, and skin texture (visible pores and minor blemishes) to ensure players do not look airbrushed.

### Step 2: Team Palettes & Studio Prompting (`make_headshot.py`)

Each player prompt combines the appearance string with official franchise identity:
* **Palette Dictionary (`_color_cache`):** Hardcoded primary and secondary hex values for all 32 NFL clubs (e.g., Dolphins: Aqua `#008E97` & Orange `#FC4C02`; Vikings: Purple `#4F2683` & Gold `#FFC62F`).
* **Prompt Template (`IMAGE_PROMPT`):** Enforces a photorealistic sports-league media-day headshot, full-frame camera with an 85mm lens at $f/5.6$, softbox key/fill lighting, subtle rim light for hair separation, ribbed v-neck jersey with mesh texturing, and seamless pure white backdrop (`#FFFFFF`).

### Step 3: Compiling Batch Requests (`build_batch_request`)

Requests are dumped to `data/headshots/batch_headshot_requests.jsonl` matching the Gemini Batch API schema:
* **Unique Request Keys:** Uses `{Player_Name}_{Team}` as the key. For rare duplicate names on the same roster (e.g. `S. Lewis` on the Seahawks or `T. Davis` on the Panthers), the builder disambiguates keys with positional or ID suffixes (`{Player_Name}_{Team}_{Position}`).
* **Model Configuration:** Targets `gemini-nano-banana-2.1` with `responseModalities: ["IMAGE"]` and `aspectRatio: "3:4"`.

### Step 4: Batch Job Execution (`headshots/gen.py`)

Batch submission and retrieval:
1. `client.files.upload("data/headshots/batch_headshot_requests.jsonl")` uploads the compiled batch input.
2. `client.batches.create(model="gemini-nano-banana-2.1", src=file.name)` schedules the batch job.
3. `gen.py` polls `client.batches.get(name=JOB_NAME)` until status reaches `JOB_STATE_SUCCEEDED`.
4. The destination file is downloaded and written to `data/headshots/batch_results.jsonl` (5.1 GB across 1,499 responses).

### Step 5: Background Removal & WebP Processing (`process_headshots.py`)

1. **Multiprocessing Worker Pool:** Uses `multiprocessing.Queue` and 6 concurrent workers matching host CPU capabilities.
2. **AI Background Cutout:** Evaluated multiple ONNX segmentation models (`u2net`, `u2netp`, `isnet`, `silueta`). The **`silueta`** model was selected for optimal edge fidelity around jersey shoulders and hair while running at $\approx 0.35\text{s}$ per inference.
3. **Encoding:** Converted raw decoded JPEGs to RGBA, applied alpha mattes, and saved as WebP (`quality=90`, `method=4`).
4. **Primary Key Mapping:** Parsed batch keys back against the Postgres `players` table and wrote files directly to:
   ```text
   static/players/{player_id}.webp
   ```

---

## 4. Frontend & API Integration

* **Serving Endpoint:** Mounted via FastAPI in `server/api.py`:
  ```python
  app.mount("/static", StaticFiles(directory="static"), name="static")
  ```
* **Frontend Cache Busting:** `Mu_Network/src/App.tsx` defines:
  ```typescript
  export const HEADSHOT_VERSION = 1;
  ```
  Player cards, modals, roster tables, and social feeds resolve URLs via:
  ```typescript
  `${API_BASE}/static/players/${player.id}.webp?v=${HEADSHOT_VERSION}`
  ```
  If a player image fails to load, the frontend falls back seamlessly to an initialed team-colored avatar placeholder.

---

## 5. Seasonal Rookie Headshot Pipeline (`headshots/rookie_headshots.py`)

While veteran players are seeded from prior seasons, every new draft class brings new rookies into the league without preexisting headshots. The seasonal rookie headshot pipeline bridges this gap automatically as part of the seasonal cadence:

1. **Trigger Points:**
   - **Seasonal Ingestion (`pipeline/weekly_capture.py --mode season`):** Runs immediately after `step_load` once all 32 team rosters are loaded and rookies are linked to `draft_prospects` via `link_rookies`. Runs before preseason engines and Week 1 articles (`step_rookie_headshots`).
   - **Roster Ingestion (`pipeline/weekly_capture.py --mode roster`):** Also executes `step_rookie_headshots` when running an explicit 32-team roster cadence scan.
   - **Standalone CLI:**
     ```bash
     python3 -m headshots.rookie_headshots [--season 2036] [--workers 5] [--drafted-only] [--dry-run]
     ```

2. **Querying Candidates:**
   - Queries `player_seasons` for `years_of_experience = 0` in the target season, joining `players`, `teams`, and `draft_prospects`.
   - Checks `static/players/{player_id}.webp`. If a headshot already exists, generation is skipped idempotently, allowing interrupted runs to resume seamlessly.
   - `--drafted-only` optionally restricts generation to prospects with an `actual_pick` recorded in `draft_prospects`.

3. **Concurrent Generation & Segmentation:**
   - Uses `ThreadPoolExecutor` (default 5 workers) with thread-local `genai.Client` and thread-local `rembg` ONNX `silueta` sessions.
   - Generates the media-day portrait via `gemini-nano-banana-2.1` using the player's deterministic appearance profile and team home jersey palette.
   - Removes the white background using `rembg` (silueta).
   - Saves uncropped alpha matte cutouts to `data/headshots/players_backup/{player_id}.webp`.
   - Resizes cleanly via Lanczos filter to standard broadcast dimensions `(700, 508)` matching all existing player headshots, saving as transparent WebP (`quality=90`, `method=4`) directly to `static/players/{player_id}.webp`.
