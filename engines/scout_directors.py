"""Scouting directors (docs/in_progress/OWNERS_SCOUTS_VOTERS_PLAN.md §2.4; PROJECT_SPEC §2.18).

One generated scouting director per team, kept across seasons (a GM firing never costs him his director, plan Q4).
A director is a generated role-'scout' person created through the personality lifecycle: `ensure_directors` calls
the `lifecycle` creator hook and stores the durable row in `scout_directors`. His five scouting traits
(TRAITS_CHASER, TAPE_GRINDER, CHARACTER_HAWK, FLOOR_SEEKER / CEILING_GAMBLER) lean that team's draft board and
voice its reports through the scouting plan's `scout_voice` hooks (`regional_bias` delegates here). The bias is a
grade shift inside the scouting plan's noise model; it never changes a prospect's true ratings.

This module is the pure hook layer; engines/scouting_engine.py and engines/scouting_boards.py consume it when the
`scout_directors` switchboard feature is active (P1-11): `ensure_directors` is called from the scouting run with the
reviewed `staff:scout` lifecycle context (`scouting_engine.director_lifecycle`), the director's `board_bias` leans
his team's board (`scouting_boards.board(extra_bias=)`), and his `voice` and bias fill that team's private report
rows (`scouting_reports.build_edition`).

    ensure_directors(cur, season, *, lifecycle) -> [director_view]
    board_bias(director_view, prospect) -> float          |x| <= MAX_BIAS (0.5) grade points
    voice(director_view) -> {"scout_id", "name", "region", "lens"}
    lens_for(traits) -> "TRAITS" | "TAPE" | "CHARACTER" | "FLOOR" | "CEILING" | None
    director_view_from_voice(voice) -> director_view

`director_view` (what ensure_directors returns, and what board_bias/voice read):
    {scout_id, team_id, name, persona_id, start_season, region, lens, traits}.

`lifecycle` is the creator hook `lifecycle(cur, *, team_id, season, name) -> {"persona_id", "traits"}`: it
initializes (once; replaying the same team/season/name returns the same person) a generated role-'scout' person
through the personality lifecycle and returns its persona_id and scouting traits. In production this is the
lifecycle store's initialize_person path (a later wiring step).

`prospect` keys read by board_bias (all optional; a missing signal contributes nothing):
    athleticism   float 0-100, higher = more athletic (a combine composite)
    production    float 0-100, higher = more college production
    flags         list[str] public report flags; CHARACTER_HAWK reacts to INTERVIEWS_MIXED / CONSISTENCY
    volatility    float >= 0, higher = a more volatile evaluation (a truthy `volatile` bool counts as 1.0)
"""
from __future__ import annotations

from engines import staff_names
from engines.scout_voice import REGIONS

MAX_BIAS = 0.5

_LENS_BY_TRAIT = {"TRAITS_CHASER": "TRAITS", "TAPE_GRINDER": "TAPE", "CHARACTER_HAWK": "CHARACTER",
                  "FLOOR_SEEKER": "FLOOR", "CEILING_GAMBLER": "CEILING"}
_LENS_ORDER = ("TRAITS_CHASER", "TAPE_GRINDER", "CHARACTER_HAWK", "FLOOR_SEEKER", "CEILING_GAMBLER")
_NEGATIVE_INTERVIEW_FLAGS = ("INTERVIEWS_MIXED", "CONSISTENCY")

# Review-only estimates (the plan gives none): the signal window over which a trait ramps its full |MAX_BIAS|.
ATHLETIC_LO, ATHLETIC_HI = 75.0, 95.0
PRODUCTION_LO, PRODUCTION_HI = 60.0, 90.0
VOLATILITY_LO, VOLATILITY_HI = 0.25, 0.75


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _ramp(x, lo, hi):
    """0..1 as x goes lo..hi, clamped; a missing signal contributes 0."""
    if x is None:
        return 0.0
    if hi <= lo:
        return 1.0 if x >= hi else 0.0
    return _clamp((x - lo) / (hi - lo), 0.0, 1.0)


def _num(prospect, key):
    v = prospect.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _volatility(prospect):
    """0..1 share of a prospect's evaluation variance (a bool `volatile` counts as 1.0)."""
    v = _num(prospect, "volatility")
    if v is None and prospect.get("volatile") is True:
        v = 1.0
    return _ramp(v, VOLATILITY_LO, VOLATILITY_HI)


def lens_for(traits):
    """The single lens for a director's voice, from his lens traits (a fixed priority order); None without one."""
    traits = traits or []
    for tid in _LENS_ORDER:
        if tid in traits:
            return _LENS_BY_TRAIT[tid]
    return None


def board_bias(director_view, prospect):
    """A grade shift in [-MAX_BIAS, MAX_BIAS] from the director's traits. Pure: never mutates its inputs and never
    reads or changes a prospect's ratings."""
    traits = set(director_view.get("traits") or [])
    bias = 0.0
    if "TRAITS_CHASER" in traits:
        bias += MAX_BIAS * _ramp(_num(prospect, "athleticism"), ATHLETIC_LO, ATHLETIC_HI)
    if "TAPE_GRINDER" in traits:
        bias += MAX_BIAS * _ramp(_num(prospect, "production"), PRODUCTION_LO, PRODUCTION_HI)
    if "CHARACTER_HAWK" in traits and (set(prospect.get("flags") or []) & set(_NEGATIVE_INTERVIEW_FLAGS)):
        bias -= MAX_BIAS
    if "FLOOR_SEEKER" in traits:
        bias -= MAX_BIAS * _volatility(prospect)
    if "CEILING_GAMBLER" in traits:
        bias += MAX_BIAS * _volatility(prospect)
    return _clamp(bias, -MAX_BIAS, MAX_BIAS)


def voice(director_view):
    """The ScoutVoice fields for the scouting plan's reports (scout_id, name, region, lens)."""
    return {
        "scout_id": director_view.get("scout_id"),
        "name": director_view.get("name"),
        "region": director_view.get("region"),
        "lens": director_view.get("lens") or lens_for(director_view.get("traits")),
    }


def director_view_from_voice(voice):
    """A minimal director_view from a scout_voice.ScoutVoice (its single lens maps back to its trait)."""
    lens = getattr(voice, "lens", None)
    trait = next((t for t, l in _LENS_BY_TRAIT.items() if l == lens), None)
    return {"scout_id": getattr(voice, "scout_id", None), "name": getattr(voice, "name", None),
            "region": getattr(voice, "region", None), "lens": lens, "traits": [trait] if trait else []}


def ensure_directors(cur, season, *, lifecycle):
    """One director view per team, created through `lifecycle` and kept across seasons in `scout_directors`.

    Returns the director views sorted by team_id, or [] when the scout_directors table isn't migrated yet (the
    feature is off until then). Idempotent: a team with an active director (no end_season) is reused, so a later
    season returns the same scout_id / name / persona_id and never inserts a second row."""
    cur.execute("/* q:directors_table */ SELECT to_regclass('scout_directors') IS NOT NULL")
    if not cur.fetchone()[0]:
        return []
    cur.execute("/* q:teams */ SELECT id, conference FROM teams ORDER BY id")
    teams = {tid: conf for tid, conf in cur.fetchall()}
    cur.execute("/* q:active_directors */ SELECT id, team_id, name, persona_id, start_season "
                "FROM scout_directors WHERE end_season IS NULL ORDER BY team_id")
    existing = {team_id: {"scout_id": sid, "team_id": team_id, "name": name, "persona_id": pid,
                          "start_season": start_season}
                for sid, team_id, name, pid, start_season in cur.fetchall()}
    taken = {d["name"].lower() for d in existing.values()}
    out = []
    for team_id, conference in sorted(teams.items()):
        director = existing.get(team_id)
        if director is None:
            name = staff_names.new_name(f"scout-director:{team_id}:{season}", taken)
            view = lifecycle(cur, team_id=team_id, season=season, name=name)
            cur.execute("/* q:insert_director */ INSERT INTO scout_directors "
                        "(team_id, name, persona_id, start_season) VALUES (%s, %s, %s, %s) RETURNING id",
                        (team_id, name, view["persona_id"], season))
            director = {"scout_id": cur.fetchone()[0], "team_id": team_id, "name": name,
                        "persona_id": view["persona_id"], "start_season": season,
                        "traits": list(view["traits"])}
        else:
            view = lifecycle(cur, team_id=team_id, season=director["start_season"], name=director["name"])
            director = dict(director, traits=list(view["traits"]))
        director["region"] = REGIONS.get(conference)
        director["lens"] = lens_for(director["traits"])
        out.append(director)
    return out
