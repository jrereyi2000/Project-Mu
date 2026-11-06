"""Read-only, provenance-scoped personality context for development checkpoints.

PERSONALITY_DEVELOPMENT_COACHING_PLAN §2.1 (U3). Resolves caller-verified aliases through
`personality.store.resolve_alias` / `store.read` and scopes them with `core.feature_view`
('development' for the player and mentor, 'coaching' for staff). Reads never seed, ensure,
name-match or fall back to `newsroom.personas.get_persona`. Missing means neutral; schema,
connection and corruption errors propagate so an enabled batch aborts instead of silently
neutralizing. Exposure intervals come from the caller's verified staff/roster records (no
table records per-week staff exposure yet, plan spike C); without them teaching is neutral.
The caller only invokes this when a development config is shadow/enabled: off never queries.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from engines.personality_development import digest
from personality import core, store

CONTEXT_VERSION = "personality-development-context-v1"


class DevelopmentContextError(ValueError):
    """Malformed caller input (not a missing profile)."""


def _alias(value, label):
    if (not isinstance(value, (tuple, list)) or len(value) != 2
            or not all(isinstance(v, str) and v for v in value)):
        raise DevelopmentContextError(f"{label} must be (namespace, external_id)")
    return str(value[0]), str(value[1])


def _scoped(cur, universe_id, role, alias, feature):
    namespace, external_id = alias
    identity = store.resolve_alias(cur, universe_id, role, namespace, external_id)
    profile = None if identity is None else store.read(cur, identity)
    return core.feature_view(profile, feature)


def _interval(raw, *, coach_keys, mentor_key, as_of_week):
    if not isinstance(raw, Mapping):
        raise DevelopmentContextError("intervals must be mappings")
    start, end = raw.get("start_week"), raw.get("end_week")
    if any(isinstance(v, bool) or not isinstance(v, int) for v in (start, end)) or start > end or start < 0:
        raise DevelopmentContextError("interval weeks must be ordered integers")
    coach_ids = sorted({str(c) for c in raw.get("coach_ids") or []})
    unknown = [c for c in coach_ids if c not in coach_keys]
    if unknown:
        raise DevelopmentContextError(f"interval names unverified staff {unknown}")
    mentor_id = raw.get("mentor_id")
    if mentor_id is not None and str(mentor_id) != mentor_key:
        raise DevelopmentContextError("interval mentor is not the resolved mentor")
    # Cutoff: nothing after the checkpoint week, never retroactive destination staff.
    if start > as_of_week:
        return None
    return {"start_week": start, "end_week": min(end, as_of_week), "coach_ids": coach_ids,
            "mentor_id": None if mentor_id is None else str(mentor_id)}


def load_development_context(cur, *, universe_id: str, player_id: int, earned_season: int, as_of_week: int,
                             player_alias: tuple[str, str], staff_aliases: Sequence[tuple[str, str]],
                             mentor_alias: tuple[str, str] | None, source_event_ids: Sequence[str],
                             intervals: Sequence[Mapping[str, Any]] | None = None) -> dict:
    """{player_view, coach_views, mentor_view, intervals, source_event_ids, cutoff, context_hash, reasons}.

    coach_views is keyed '<namespace>:<external_id>'; intervals name those keys. Overlapping or
    split exposure (trades, coordinator changes) is expressed by the caller's intervals.
    """
    if not isinstance(universe_id, str) or not universe_id:
        raise DevelopmentContextError("universe_id is required")
    for name, value in (("player_id", player_id), ("earned_season", earned_season), ("as_of_week", as_of_week)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise DevelopmentContextError(f"{name} must be an integer")
    player_alias = _alias(player_alias, "player_alias")
    staff = [_alias(a, "staff_alias") for a in staff_aliases or []]
    mentor = None if mentor_alias is None else _alias(mentor_alias, "mentor_alias")
    reasons = []
    player_view = _scoped(cur, universe_id, "player", player_alias, "development")
    coach_views = {f"{ns}:{ext}": _scoped(cur, universe_id, "coach", (ns, ext), "coaching") for ns, ext in staff}
    mentor_view = None if mentor is None else _scoped(cur, universe_id, "player", mentor, "development")
    mentor_key = None if mentor is None else f"{mentor[0]}:{mentor[1]}"
    clean = []
    for raw in intervals or []:
        row = _interval(raw, coach_keys=set(coach_views), mentor_key=mentor_key, as_of_week=as_of_week)
        if row is not None:
            clean.append(row)
    clean.sort(key=lambda r: (r["start_week"], r["end_week"], r["coach_ids"], r["mentor_id"] or ""))
    if not clean:
        reasons.append("NO_VERIFIED_EXPOSURE_INTERVALS")
    if player_view["status"] == "missing":
        reasons.append("PLAYER_PROFILE_MISSING")
    events = sorted({str(e) for e in source_event_ids or []})
    refs = {"player": _ref(player_view), "coaches": {k: _ref(v) for k, v in sorted(coach_views.items())},
            "mentor": None if mentor_view is None else _ref(mentor_view)}
    context = {"version": CONTEXT_VERSION, "universe_id": universe_id, "player_id": player_id,
               "earned_season": earned_season, "cutoff": {"season": earned_season, "week": as_of_week},
               "player_view": player_view, "coach_views": coach_views, "mentor_view": mentor_view,
               "mentor_key": mentor_key, "intervals": clean, "source_event_ids": events,
               "reasons": sorted(reasons)}
    context["context_hash"] = digest({"refs": refs, "intervals": clean, "events": events,
                                      "cutoff": context["cutoff"], "player_id": player_id,
                                      "universe_id": universe_id, "version": CONTEXT_VERSION,
                                      "views": [player_view, coach_views, mentor_view]})
    return context


def _ref(view):
    return {"persona_id": view.get("persona_id"), "revision": view.get("revision"), "status": view.get("status"),
            "catalogue_version": view.get("catalogue_version")}


def exposure_by_coach(context: Mapping[str, Any], game_weeks: Sequence[float]) -> dict[str, float]:
    """Share of this checkpoint's evidence games (by week) covered by each coach's verified intervals."""
    weeks = [float(w) for w in game_weeks]
    if not weeks:
        return {}
    out: dict[str, float] = {}
    for key in (context.get("coach_views") or {}):
        covered = sum(1 for w in weeks if any(key in iv["coach_ids"] and iv["start_week"] <= w <= iv["end_week"]
                                              for iv in context.get("intervals") or []))
        if covered:
            out[key] = covered / len(weeks)
    return out
