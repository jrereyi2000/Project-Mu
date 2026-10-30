"""
pipeline/gpu_queue/models.py
Data models and constants for the Nightly GPU Queue.

Reference:
- docs/in_progress/PC_MIGRATION_PLAN.md §7.5, §7.8
- docs/not_started/PRE_PC_WORK_2.md Item A7
"""
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
import json
from typing import Any, Dict, Optional
import uuid

# Priority tiers as defined in docs/in_progress/PC_MIGRATION_PLAN.md §7.8:
# Tier 1: Newsroom text jobs (Agent Wire batches, agent memory takes, scouting notes, GM text).
# Tier 2: Headshot generation for new players / re-renders (§7.7) -> portrait conversion (§9.4).
# Tier 3: Audio TTS production (§9).
TIER_1_TEXT = 1
TIER_2_IMAGE = 2
TIER_3_AUDIO = 3

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

VALID_STATUSES = {
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELLED,
}


def default_priority_for_kind(kind: str) -> int:
    """Infer the §7.8 priority tier from the job kind string."""
    k = (kind or "").strip().lower()
    if any(term in k for term in ["text", "wire", "memory", "scout", "gm", "newsroom"]):
        return TIER_1_TEXT
    if any(term in k for term in ["headshot", "image", "portrait"]):
        return TIER_2_IMAGE
    if any(term in k for term in ["audio", "tts", "voice", "sound", "broadcast"]):
        return TIER_3_AUDIO
    return TIER_1_TEXT


def now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class GPUJob:
    """Represents a discrete unit of work scheduled for the PC's GPU."""
    id: str = field(default_factory=lambda: f"job_{uuid.uuid4().hex[:12]}")
    kind: str = "text"
    model_id: str = "gemma4:12b"
    payload: Dict[str, Any] = field(default_factory=dict)
    priority: Optional[int] = None
    status: str = STATUS_PENDING
    created_at: str = field(default_factory=now_iso)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error: Optional[str] = None
    result: Optional[Any] = None

    def __post_init__(self):
        if not self.id:
            self.id = f"job_{uuid.uuid4().hex[:12]}"
        if not self.created_at:
            self.created_at = now_iso()
        if self.priority is None:
            self.priority = default_priority_for_kind(self.kind)
        if self.status not in VALID_STATUSES:
            self.status = STATUS_PENDING

    def to_dict(self) -> Dict[str, Any]:
        """Convert job to a JSON-serializable dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GPUJob":
        """Instantiate a GPUJob from a dictionary."""
        payload = data.get("payload", {})
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                payload = {"raw": payload}
        elif not isinstance(payload, dict):
            payload = {"data": payload}

        result = data.get("result")
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except Exception:
                pass

        priority_raw = data.get("priority")
        priority = int(priority_raw) if priority_raw is not None else None

        return cls(
            id=str(data["id"]),
            kind=str(data.get("kind", "text")),
            model_id=str(data.get("model_id", "")),
            payload=payload,
            priority=priority,
            status=str(data.get("status", STATUS_PENDING)),
            created_at=str(data.get("created_at") or now_iso()),
            started_at=data.get("started_at"),
            completed_at=data.get("completed_at"),
            error=data.get("error"),
            result=result,
        )
