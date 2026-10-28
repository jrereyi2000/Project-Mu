"""
pipeline/tombstone.py: Lightweight deprecation tripwire and runtime audit helper.

Logs a loud warning or raises RuntimeError when superseded/deprecated functions or branches
are executed at runtime.
"""
import functools
import logging
import os
import sys
from typing import Callable, Any

logger = logging.getLogger("tombstone")


def tombstone(version: str = "v0", replaced_by: str = "NONE", raise_error: bool = False) -> Callable:
    """
    Decorator marking a function or method as a superseded tombstone.

    Emits a loud warning by default to stderr and the logger.
    Raises RuntimeError if TOMBSTONE_RAISE=1 or raise_error=True.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            msg = (
                f"[TOMBSTONE] Call to superseded {version} symbol: '{func.__qualname__}'. "
                f"Replaced by: {replaced_by}"
            )
            logger.warning(msg)
            sys.stderr.write(f"\n⚠️  {msg}\n")
            if raise_error or os.environ.get("TOMBSTONE_RAISE", "").lower() in ("1", "true", "yes"):
                raise RuntimeError(msg)
            return func(*args, **kwargs)
        return wrapper
    return decorator


def tombstone_branch(version: str = "v0", reason: str = "Unreachable/obsolete fallback", raise_error: bool = False) -> None:
    """
    Tripwire function to place inside dead conditional branches or exception handlers.
    """
    msg = f"[TOMBSTONE_BRANCH] Executing dead {version} branch: {reason}"
    logger.warning(msg)
    sys.stderr.write(f"\n⚠️  {msg}\n")
    if raise_error or os.environ.get("TOMBSTONE_RAISE", "").lower() in ("1", "true", "yes"):
        raise RuntimeError(msg)
