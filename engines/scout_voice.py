"""Scout voice hook (docs/done/SCOUTING_REVAMP_PLAN.md §2.4; PROJECT_SPEC §2.18).

Reports and interviews are written in one neutral "Mu Scouting Department" voice until a team's scouting director
is known (the owners plan's U6). `ScoutVoice` carries a director's scout_id, name, region (a REGIONS key, from the
team's conference) and lens (TRAITS | TAPE | CHARACTER | FLOOR | CEILING, from his scouting traits). `department_voice()`
is the neutral voice; `voice_prompt()` writes the prose voice; `regional_bias()` is a director's grade shift for his
own report row only (0.0 for the department voice), delegated to engines.scout_directors.board_bias. The consensus
and the department report never change.
"""
from __future__ import annotations

from dataclasses import dataclass

REGIONS: dict[str, str] = {"AFC": "AFC", "NFC": "NFC"}          # conference -> region


@dataclass(frozen=True)
class ScoutVoice:
    scout_id: int | None = None       # None = department
    name: str | None = None
    region: str | None = None         # a REGIONS key
    lens: str | None = None           # "TRAITS" | "TAPE" | "CHARACTER" | "FLOOR" | "CEILING"


def department_voice() -> ScoutVoice:
    """The one neutral voice every report uses until a team's scouting director is created."""
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
    """A grade shift for that scout's own report only. 0.0 for the department voice; a director voice delegates to
    engines.scout_directors.board_bias (his lens trait's lean)."""
    if voice.scout_id is None:
        return 0.0
    from engines.scout_directors import board_bias, director_view_from_voice
    return board_bias(director_view_from_voice(voice), prospect)
