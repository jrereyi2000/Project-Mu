"""Project Mu — Local Headshot Generation Pipeline.

Standalone local portrait generation engine for Project Mu player and rookie headshots
(PC_MIGRATION_PLAN §7.7, PRE_PC_WORK_2 Item A1).
"""

from headshots.local.adapter import (
    get_local_generator_if_enabled,
    is_local_headshots_enabled,
    patch_headshot_generator,
    unpatch_headshot_generator,
)
from headshots.local.backends import (
    ComfyUIBackend,
    DiffusersBackend,
    HeadshotBackend,
    MockBackend,
    get_backend,
    register_backend,
)
from headshots.local.generator import generate_headshot

__all__ = [
    "generate_headshot",
    "HeadshotBackend",
    "MockBackend",
    "DiffusersBackend",
    "ComfyUIBackend",
    "get_backend",
    "register_backend",
    "is_local_headshots_enabled",
    "get_local_generator_if_enabled",
    "patch_headshot_generator",
    "unpatch_headshot_generator",
]
