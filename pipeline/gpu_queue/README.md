# Nightly GPU Queue Scheduler (`pipeline/gpu_queue`)

A standalone offline queue and scheduler for orchestrating GPU workloads on the Project Mu PC's NVIDIA RTX 5070 Ti (16GB VRAM).

References:
- `docs/in_progress/PC_MIGRATION_PLAN.md` §7.5, §7.8
- `docs/not_started/PRE_PC_WORK_2.md` Item A7

---

## 1. Problem & Architecture

The PC's RTX 5070 Ti has 16GB VRAM. It cannot simultaneously run Madden NFL 27, a 14B–27B LLM (e.g., Gemma 4 / DeepSeek / GLM), and an image generation pipeline (e.g., SDXL / diffusers) without out-of-memory (OOM) crashes or dropped frames.

To guarantee stability:
1. **Sequential Execution:** Exactly one model occupies GPU VRAM at a time. Each model is explicitly unloaded before the next model is loaded.
2. **Game Awareness:** Batch GPU tasks pause immediately whenever Madden NFL 27 is running (`Madden27.exe`). When Madden is detected, any loaded model is evicted from VRAM to preserve full graphical performance for gameplay. Work resumes once Madden exits or after a configured idle timeout.
3. **Tier Prioritization (§7.8):**
   - **Tier 1 (Text):** Newsroom text jobs (Agent Wire batches, agent memory takes, scouting notes, GM text).
   - **Tier 2 (Image):** Headshots for new players / re-renders (§7.7), followed by portrait conversion for mod packaging (§9.4).
   - **Tier 3 (Audio):** TTS production for broadcast layers (§9.1, §9.2).
4. **Model Batching:** Within an active priority tier, jobs requiring the currently loaded model are dispatched consecutively without unloading and reloading the model.
5. **Zero PostgreSQL Dependency:** Operates fully offline using pluggable stores (`SQLiteJobStore` by default in `data/gpu_queue.db`, or `JsonJobStore` in `data/gpu_queue.json`).

---

## 2. Package Structure

```
pipeline/gpu_queue/
├── __init__.py        # Public package exports
├── models.py          # GPUJob dataclass, priority tiers, statuses, serializers
├── queue.py           # GPUQueue manager, BaseJobStore, SQLiteJobStore, JsonJobStore
├── runner.py          # GPUQueueRunner, run_queue helper, model loading & eviction
├── process_check.py   # Madden27 detection via tasklist.exe (WSL2/Win) / pgrep (Mac/Linux)
├── cli.py             # CLI interface: enqueue, list, run, clear, check-madden
└── README.md          # System documentation and usage
```

---

## 3. CLI Usage

Run directly with Python:

```bash
# Check if Madden is currently holding the GPU
python -m pipeline.gpu_queue.cli check-madden

# Enqueue jobs
python -m pipeline.gpu_queue.cli enqueue \
    --kind text \
    --model gemma4:12b \
    --payload '{"prompt": "Generate agent wire reactions for Week 4"}'

python -m pipeline.gpu_queue.cli enqueue \
    --kind headshot \
    --model sdxl-turbo \
    --payload '{"player_id": 1042, "skin_tone": 3}'

python -m pipeline.gpu_queue.cli enqueue \
    --kind audio_tts \
    --model chatter-v1 \
    --payload '{"script": "Welcome back to the pregame show."}'

# List jobs
python -m pipeline.gpu_queue.cli list
python -m pipeline.gpu_queue.cli list --status pending
python -m pipeline.gpu_queue.cli list --json

# Run queue processor
python -m pipeline.gpu_queue.cli run
python -m pipeline.gpu_queue.cli run --once
python -m pipeline.gpu_queue.cli run --poll-interval 1.0

# Clear completed or failed jobs
python -m pipeline.gpu_queue.cli clear --status completed
python -m pipeline.gpu_queue.cli clear
```

---

## 4. Python API

### Enqueueing Jobs

```python
from pipeline.gpu_queue import GPUJob, enqueue, GPUQueue, SQLiteJobStore

# Enqueue with automatic tier inference (kind="text" -> Tier 1)
job_id = enqueue(GPUJob(
    kind="text",
    model_id="gemma4:12b",
    payload={"agent": "wire_writer", "season": 2036, "week": 3},
))

# Explicit priority override
job_id = enqueue(GPUJob(
    kind="custom_urgent_recap",
    model_id="gemma4:12b",
    payload={"urgent": True},
    priority=1,
))
```

### Running the Queue

```python
from pipeline.gpu_queue import GPUQueueRunner, GPUQueue, SQLiteJobStore

def my_load_model(model_id: str):
    print(f"Loading {model_id} into VRAM...")
    return f"handle_{model_id}"

def my_unload_model(model_id: str):
    print(f"Unloading {model_id} from VRAM...")

runner = GPUQueueRunner(
    queue=GPUQueue(SQLiteJobStore("data/gpu_queue.db")),
    load_model=my_load_model,
    unload_model=my_unload_model,
)

runner.register_handler("text", lambda job: {"output": "Generated text..."})
runner.register_handler("headshot", lambda job: {"output": "Generated image bytes..."})

# Run one job or loop until empty
runner.run_once()
runner.run_loop(poll_interval=2.0, stop_on_empty=True)
```

### Process Checking

```python
from pipeline.gpu_queue import is_madden_running, mock_process_running

if is_madden_running():
    print("Madden is active - deferring heavy local jobs")

# In unit tests:
with mock_process_running(True):
    assert is_madden_running() is True
```

---

## 5. Verification & Tests

Run the test suite with pytest:

```bash
pytest tests/test_gpu_queue.py -v
```
