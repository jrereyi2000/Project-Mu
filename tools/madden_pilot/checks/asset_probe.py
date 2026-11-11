"""verify-mods asset check (MADDEN_SPIKE_AGENT_PLAN section 2.8).

``run(pilot)`` is the entry point ``python3 -m tools.madden_pilot verify-mods --check
asset_probe`` uses: it reads the check spec from ``MU_ASSET_PROBE`` (a JSON file), captures a
frame if the pilot has none yet, and runs ``assets.check_frame`` on it. ``run_frame(png, spec)``
is the same measurement for a saved frame, which ``spike-check`` and ``spike-analyze`` (U2)
call directly.

With no ``MU_ASSET_PROBE`` the check returns unknown and presses nothing. The spec file holds
one spec object, or a list of them; a list passes only when every spec passes, fails when any
spec fails, and is unknown otherwise.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from tools.madden_pilot import assets

ENV_ASSET_PROBE = "MU_ASSET_PROBE"


def run_frame(png: str | Path, spec: dict) -> dict:
    """Measure one saved frame against one check spec."""
    return assets.check_frame(png, spec)


def _read_spec(raw: str) -> dict | list:
    return json.loads(Path(raw).read_text(encoding="utf-8"))


def _frame_png(pilot) -> str | None:
    """The pilot's current frame path, capturing one if it has not looked yet."""
    png = getattr(pilot, "last_png", None)
    if not png:
        frame = getattr(pilot, "frame", None)
        png = getattr(frame, "png", None)
    if not png and callable(getattr(pilot, "look", None)):
        pilot.look()
        png = getattr(pilot, "last_png", None)
        if not png:
            png = getattr(getattr(pilot, "frame", None), "png", None)
    return png


def _combine(results: list[dict]) -> dict:
    statuses = [item.get("status", "unknown") for item in results]
    if statuses and all(status == "pass" for status in statuses):
        status = "pass"
    elif any(status == "fail" for status in statuses):
        status = "fail"
    else:
        status = "unknown"
    measures = {str(index): item.get("measures", {}) for index, item in enumerate(results)}
    why = " | ".join(str(item.get("why", "")) for item in results if item.get("why"))
    return {"status": status, "measures": measures, "why": why}


def run(pilot) -> dict:
    """Read ``MU_ASSET_PROBE`` and check the pilot's current frame (or return unknown)."""
    raw = os.environ.get(ENV_ASSET_PROBE, "").strip()
    if not raw:
        return {"status": "unknown", "measures": {},
                "why": f"{ENV_ASSET_PROBE} is not set; no asset spec was given"}
    try:
        spec = _read_spec(raw)
    except (OSError, ValueError) as exc:
        return {"status": "unknown", "measures": {},
                "why": f"cannot read {ENV_ASSET_PROBE} {raw!r}: {exc}"}
    try:
        png = _frame_png(pilot)
    except Exception as exc:  # a pilot stop is information, not a check failure
        return {"status": "unknown", "measures": {},
                "why": f"could not capture a frame: {exc}"}
    if not png:
        return {"status": "unknown", "measures": {},
                "why": "the pilot has no frame to check"}
    if isinstance(spec, list):
        return _combine([run_frame(png, item) for item in spec])
    if not isinstance(spec, dict):
        return {"status": "unknown", "measures": {},
                "why": f"{ENV_ASSET_PROBE} must hold one check object or a list of them"}
    return run_frame(png, spec)
