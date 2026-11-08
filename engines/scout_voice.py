"""Scout voice hook (docs/in_progress/SCOUTING_REVAMP_PLAN.md §2.4; PROJECT_SPEC §2.18).

Reports and interviews are written in one neutral "Mu Scouting Department" voice until personality phase 6 gives
scouts names, lenses and regional biases. `ScoutVoice` is the frozen slot phase 6 fills in; `department_voice()` is
the only voice until then, `regional_bias()` is always 0.0, and `REGIONS` (conference -> region) is empty. The
consensus and the department report never change; a phase-6 scout gets his own report row whose grade is the consensus
grade plus his `regional_bias(...)` and whose prose uses `voice_prompt`.
"""
from __future__ import annotations

from dataclasses import dataclass

REGIONS: dict[str, str] = {}          # conference -> region, filled in phase 6


@dataclass(frozen=True)
class ScoutVoice:
    scout_id: int | None = None       # None = department
    name: str | None = None
    region: str | None = None         # a REGIONS key, phase 6
    lens: str | None = None           # "TRAITS" | "TAPE" | "CHARACTER" | "FLOOR" | "CEILING", phase 6


def department_voice() -> ScoutVoice:
    """The one neutral voice every report uses until phase 6 creates scouts."""
    return ScoutVoice()


def voice_prompt(voice: ScoutVoice) -> str:
    """The prose voice for a scout's own report. The department voice is a neutral national scouting service."""
    if voice.scout_id is None:
        return "a neutral national scouting service"
    region = f" focused on the {voice.region}" if voice.region else ""
    lens = {"TRAITS": "who prizes physical traits", "TAPE": "who grinds the tape",
            "CHARACTER": "who weighs character heavily", "FLOOR": "who scouts for a safe floor",
            "CEILING": "who gambles on ceiling"}.get(voice.lens, "")
    name = voice.name or "an area scout"
    return f"{name}, a scout{region}{' ' + lens if lens else ''}"


def regional_bias(voice: ScoutVoice, prospect: dict) -> float:
    """A grade shift for that scout's own report only. 0.0 now; phase 6 gives area scouts a regional lean."""
    return 0.0
