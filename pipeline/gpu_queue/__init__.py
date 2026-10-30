"""
pipeline/gpu_queue: Standalone Nightly GPU Queue Scheduler for Project Mu.

Reference:
- docs/in_progress/PC_MIGRATION_PLAN.md §7.5, §7.8
- docs/not_started/PRE_PC_WORK_2.md Item A7
"""

from .models import (
    GPUJob,
    TIER_1_TEXT,
    TIER_2_IMAGE,
    TIER_3_AUDIO,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_CANCELLED,
    default_priority_for_kind,
)
from .queue import (
    GPUQueue,
    BaseJobStore,
    SQLiteJobStore,
    JsonJobStore,
    enqueue,
)
from .runner import (
    GPUQueueRunner,
    run_queue,
)
from .process_check import (
    is_madden_running,
    is_process_running,
    set_process_check_mock,
    mock_process_running,
)

__all__ = [
    "GPUJob",
    "TIER_1_TEXT",
    "TIER_2_IMAGE",
    "TIER_3_AUDIO",
    "STATUS_PENDING",
    "STATUS_RUNNING",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_CANCELLED",
    "default_priority_for_kind",
    "GPUQueue",
    "BaseJobStore",
    "SQLiteJobStore",
    "JsonJobStore",
    "enqueue",
    "GPUQueueRunner",
    "run_queue",
    "is_madden_running",
    "is_process_running",
    "set_process_check_mock",
    "mock_process_running",
]
