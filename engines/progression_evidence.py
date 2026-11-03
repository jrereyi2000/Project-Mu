"""Pure, cutoff-bounded progression calendars and review evidence.

Thresholds are configurable initial policy, not fitted football constants. This
module assesses evidence only: it never awards ratings or writes state/saves.
Callers supply a player-aligned team schedule to due_checkpoints, and one player's
history to build_evidence. Retain prior game identities across trades: a transfer
does not reset either calendar or evidence. Byes never become completed games.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable, Mapping

POSTSEASON = {"PLAYOFF", "PLAYOFFS", "POSTSEASON", "POST_SEASON", "WILD_CARD", "DIVISIONAL",
              "CONFERENCE", "CONFERENCE_CHAMPIONSHIP", "SUPER_BOWL"}
DEFAULT_THRESHOLDS = {"QB": 10, "RB": 5, "REC": 2, "OL": 15, "FRONT": 15,
                      "DB": 15, "K": 1, "P": 2, "DEFAULT": 15}


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _identity(kind, season, ids):
    payload = json.dumps([kind, season, list(ids)], separators=(",", ":"), sort_keys=True)
    return f"{kind}:" + hashlib.sha256(payload.encode()).hexdigest()[:24]


def _completed(games: Iterable[Mapping[str, Any]], season: int, as_of_week: int,
               as_of_game_id=None):
    """Canonical one-row-per-game sequence. Conflicting duplicates are refused."""
    seen = {}
    for original in games:
        row = dict(original)
        if row.get("season") != season or row.get("game_id") is None:
            continue
        week = _number(row.get("week"))
        if week is None or week > as_of_week:
            continue
        if str(row.get("status", "")).upper() != "COMPLETED":
            continue
        kind = str(row.get("game_type", "REGULAR_SEASON")).upper()
        if kind in {"BYE", "PRESEASON", "PRO_BOWL"}:
            continue
        if as_of_game_id is not None and week == as_of_week:
            # Optional within-week cutoff; IDs are opaque unless comparable.
            try:
                if row["game_id"] > as_of_game_id:
                    continue
            except TypeError:
                raise ValueError("as_of_game_id and game IDs must be comparable") from None
        gid = row["game_id"]
        if gid in seen and seen[gid] != row:
            raise ValueError(f"conflicting duplicate game {gid}")
        seen[gid] = row
    return sorted(seen.values(), key=lambda row: (float(row["week"]), str(row["game_id"])))


def _postseason(row):
    return str(row.get("game_type", "")).upper() in POSTSEASON


def due_checkpoints(team_games, *, season, as_of_week, accounted_game_ids=(),
                    mental_interval=4, technical_midpoint=9, season_closed=False,
                    as_of_game_id=None):
    """Return due events from the completed team-game sequence while rostered.

    Preserve prior-team game IDs across a trade, rather than resetting the count
    to the destination team's latest schedule. The caller supplies that sequence.

    Accounted IDs can be a flat iterable (both groups), or a mapping from MENTAL
    and TECHNICAL to independent iterables. Events contain only newly unaccounted
    evidence. A season closure flushes residual mental/technical games once.
    """
    if mental_interval < 1 or technical_midpoint < 1:
        raise ValueError("checkpoint intervals must be positive")
    games = _completed(team_games, season, as_of_week, as_of_game_id)
    def accounted(group):
        source = accounted_game_ids.get(group, ()) if isinstance(accounted_game_ids, Mapping) else accounted_game_ids
        return set(source)
    out = []
    mental_boundaries = list(range(mental_interval, len(games) + 1, mental_interval))
    if season_closed and games and len(games) not in mental_boundaries:
        mental_boundaries.append(len(games))
    for group, boundaries in (("MENTAL", mental_boundaries),
                              ("TECHNICAL", sorted({n for n in (technical_midpoint,
                                len(games) if season_closed else 0) if 0 < n <= len(games)}))):
        used = accounted(group)
        for boundary in boundaries:
            ids = [g["game_id"] for g in games[:boundary] if g["game_id"] not in used]
            if not ids:
                continue
            key = f"{group}:{boundary}"
            out.append({"checkpoint": key, "event_id": _identity(key, season, ids),
                        "evidence_ids": ids, "completed_games": boundary, "group": group})
            used.update(ids)
    return out


def _opportunities(row, group):
    explicit = _number(row.get("opportunities"))
    if explicit is not None:
        return max(0, explicit), 1.0
    fields = {"QB": "pass_attempts", "RB": "rush_attempts", "REC": "receptions",
              "K": "kick_fga", "P": "punt_punts"}
    value = _number(row.get(fields.get(group, "downs_played")))
    if value is not None:
        # Receptions are a weaker opportunity proxy than targets/routes.
        return max(0, value), 0.5 if group == "REC" else 1.0
    downs = _number(row.get("downs_played"))
    return (max(0, downs), 0.6) if downs is not None else (None, 0.0)


def _signal(row):
    signal = _number(row.get("performance_signal"))
    if signal is not None:
        if not -1 <= signal <= 1:
            raise ValueError("performance_signal must be normalized to -1..1")
        return signal
    # Adapter-calibrated baseline/scale, not an invented population percentile.
    baseline, scale = _number(row.get("baseline_efficiency")), _number(row.get("efficiency_scale"))
    efficiency = _number(row.get("efficiency"))
    if efficiency is not None and baseline is not None and scale is not None and scale > 0:
        return max(-1.0, min(1.0, (efficiency - baseline) / scale))
    return None


def build_evidence(player_games, *, season, as_of_week, team_games=(),
                   playoff_run_closed=False, accounted_evidence_ids=(), min_appearances=6,
                   min_playoff_appearances=3, opportunity_thresholds=None,
                   min_reliability=0.15, as_of_game_id=None):
    """Assess one player's meaningful appearances without granting development.

    A closed sustained postseason run is an independent review path. Callers
    establish closure from elimination/championship, never from absence of rows.
    Missing performance remains None. Reviewed evidence can contextualize a new
    window, but each route requires its minimum NEW meaningful appearances.
    accounted_evidence_ids are review-specific, separate from development awards.
    """
    if min_appearances < 1 or min_playoff_appearances < 2:
        raise ValueError("invalid appearance thresholds")
    if not 0 <= min_reliability <= 1:
        raise ValueError("min_reliability must be in 0..1")
    thresholds = {**DEFAULT_THRESHOLDS, **(opportunity_thresholds or {})}
    if any(_number(v) is None or float(v) <= 0 for v in thresholds.values()):
        raise ValueError("opportunity thresholds must be positive finite numbers")
    games = _completed(player_games, season, as_of_week, as_of_game_id)
    notes, meaningful = [], []
    for row in games:
        group = str(row.get("position_group", row.get("group", "DEFAULT"))).upper()
        opportunity, quality = _opportunities(row, group)
        threshold = float(thresholds.get(group, thresholds["DEFAULT"]))
        if opportunity is None:
            notes.append(f"game {row['game_id']}: opportunities unavailable")
            continue
        if opportunity < threshold:
            continue
        confidence = _number(row.get("signal_reliability"))
        if confidence is not None:
            quality *= max(0.0, min(1.0, confidence))
        meaningful.append((row, opportunity, quality, _signal(row)))
    # Keep a bounded recent window while retaining a whole shorter playoff run.
    meaningful = meaningful[-max(8, min_appearances):]
    playoff = [item for item in meaningful if _postseason(item[0])]
    trigger = ("PLAYOFF_RUN" if playoff_run_closed and len(playoff) >= min_playoff_appearances
               else "PARTIAL_SEASON" if len(meaningful) >= min_appearances else None)
    selected = playoff if trigger == "PLAYOFF_RUN" else meaningful
    ids = [item[0]["game_id"] for item in selected]
    reliability = (min(1.0, len(selected) / 8) * sum(item[2] for item in selected) / len(selected)) if selected else 0.0
    measured = [(opps, signal) for _, opps, _, signal in selected if signal is not None]
    total_weight = sum(weight for weight, _ in measured)
    signal = sum(weight * value for weight, value in measured) / total_weight if total_weight else None
    if signal is None:
        notes.append("comparative performance unavailable; eligibility is not evidence of improvement")
    used = set(accounted_evidence_ids)
    fresh_ids = [gid for gid in ids if gid not in used]
    needed = min_playoff_appearances if trigger == "PLAYOFF_RUN" else min_appearances
    enough_fresh = len(fresh_ids) >= needed
    if ids and not enough_fresh:
        notes.append("insufficient fresh review evidence; previously reviewed games are context only")
    # A caller may pass the filtered team schedule to corroborate postseason IDs.
    schedule = _completed(team_games, season, as_of_week, as_of_game_id)
    if schedule:
        known = {g["game_id"] for g in schedule}
        if any(gid not in known for gid in ids):
            notes.append("player evidence not present in supplied team schedule")
            trigger = None
    skills = sorted({skill for row, _, _, _ in selected for skill in row.get("supported_skills", ())
                     if isinstance(skill, str) and skill})
    return {"window_id": _identity(trigger or "INSUFFICIENT", season, ids), "evidence_ids": ids,
            "trigger": trigger, "eligible": bool(trigger and enough_fresh and reliability >= min_reliability),
            "fresh_evidence_ids": fresh_ids,
            "reliability": round(reliability, 6), "performance_signal": signal,
            "meaningful_appearances": len(selected), "playoff_appearances": len(playoff),
            "opportunities": sum(item[1] for item in selected), "supported_skills": skills,
            "notes": sorted(set(notes))}
