"""Pure coaching preferences: close-call youth/veteran opportunities and scheme-choice advice.

docs/in_progress/PERSONALITY_DEVELOPMENT_COACHING_PLAN.md §2.5. Nothing here reads a database
or writes a chart, scheme, playbook or installation. Callers pass a scoped
`feature_view(profile, 'coaching')` and already-normalized candidates whose neutral score
includes ability, eligibility penalty and scheme fit (so fit is never added twice).
Adjustments are bounded per-slot score inputs for `depth_chart_engine.staff_depth_chart`
(`score_adjustments=`); off/zero produces none. No numerical preset is selected here.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
import math
from typing import Any, Mapping, Sequence

from engines.personality_development import canonical, digest

MODES = ("off", "shadow", "enabled")
WEIGHT_KEYS = frozenset(("youth_developer", "team_vision", "scheme_tendency"))
CAP_FIELDS = ("max_depth_score_shift", "close_score_window", "max_scheme_score_shift")
COHORTS = ("youth", "veteran", "other")
PLAYING_TIME = ("YOUTH", "VETERANS", "MERIT")
YOUTH_TRAIT = "YOUTH_DEVELOPER"
# Context only: these never change a neutral ordering by themselves (§2.5).
CONTEXT_TRAITS = ("SCHEME_NERD", "OLD_SCHOOL")


class CoachingPolicyError(ValueError):
    """Invalid configuration or input."""


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise CoachingPolicyError(f"{name} must be numeric, not boolean")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise CoachingPolicyError(f"{name} must be finite") from None
    if not math.isfinite(number):
        raise CoachingPolicyError(f"{name} must be finite")
    return number


@dataclass(frozen=True)
class CoachingConfig:
    version: str = ""
    mode: str = "off"
    weights: Mapping[str, float] = field(default_factory=dict)
    max_depth_score_shift: float = 0.0
    close_score_window: float = 0.0
    max_scheme_score_shift: float = 0.0

    def __post_init__(self):
        if self.mode not in MODES:
            raise CoachingPolicyError("mode must be off, shadow or enabled")
        if not isinstance(self.version, str):
            raise CoachingPolicyError("version must be text")
        if not isinstance(self.weights, Mapping):
            raise CoachingPolicyError("weights must be a mapping")
        clean = {}
        for key, value in self.weights.items():
            if key not in WEIGHT_KEYS:
                raise CoachingPolicyError(f"unknown coaching weight {key!r}")
            number = _finite(value, f"weights.{key}")
            if number < 0:
                raise CoachingPolicyError(f"weights.{key} must be nonnegative")
            clean[key] = number
        object.__setattr__(self, "weights", dict(sorted(clean.items())))
        for name in CAP_FIELDS:
            number = _finite(getattr(self, name), name)
            if number < 0:
                raise CoachingPolicyError(f"{name} must be nonnegative")
            object.__setattr__(self, name, number)
        if self.mode != "off" and not self.version.strip():
            raise CoachingPolicyError("shadow/enabled configs need an explicit reviewed version")
        if self.mode == "enabled" and not (self.max_depth_score_shift > 0 or self.max_scheme_score_shift > 0):
            raise CoachingPolicyError("an enabled config needs a nonzero cap")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CoachingConfig":
        if not isinstance(data, Mapping):
            raise CoachingPolicyError("config must be an object")
        unknown = sorted(set(data) - {f.name for f in fields(cls)})
        if unknown:
            raise CoachingPolicyError(f"unknown config fields: {unknown}")
        return cls(**dict(data))

    def as_dict(self) -> dict[str, Any]:
        return {"version": self.version, "mode": self.mode, "weights": dict(self.weights),
                **{name: getattr(self, name) for name in CAP_FIELDS}}

    def config_hash(self) -> str:
        return digest(self.as_dict())


OFF = CoachingConfig()


def _traits(view: Mapping[str, Any] | None) -> frozenset[str]:
    if not view or view.get("status") != "stored":
        return frozenset()
    return frozenset(str(t) for t in view.get("traits") or [])


def _candidate(raw: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise CoachingPolicyError("candidates must be mappings")
    pid = raw.get("player_id")
    if isinstance(pid, bool) or not isinstance(pid, int):
        raise CoachingPolicyError("player_id must be an integer")
    slot = raw.get("slot")
    if not isinstance(slot, str) or not slot:
        raise CoachingPolicyError("slot is required")
    cohort = raw.get("cohort", "other")
    if cohort not in COHORTS:
        raise CoachingPolicyError("cohort must be youth, veteran or other")
    weight = _finite(raw.get("cohort_weight", 0), "cohort_weight")
    return {"player_id": pid, "slot": slot, "neutral_score": _finite(raw.get("neutral_score"), "neutral_score"),
            "eligible": bool(raw.get("eligible")), "out": bool(raw.get("out")), "cohort": cohort,
            # Caller-normalized, clipped once: extreme ages cannot amplify a preference.
            "cohort_weight": max(0.0, min(1.0, weight))}


def opportunity_adjustments(*, candidates: Sequence[Mapping[str, Any]], coach_view: Mapping[str, Any] | None,
                            config: CoachingConfig | None, playing_time: str | None = None) -> dict:
    """Bounded per-slot score adjustments for close youth/veteran calls.

    YOUTH_DEVELOPER (weights.youth_developer) favors youth; the inbox team-vision
    playing_time (YOUTH|VETERANS|MERIT, weights.team_vision) is one more input inside the
    same window and cap. Veteran preference needs that explicit input: OLD_SCHOOL alone is
    not evidence. Only eligible, healthy candidates within close_score_window of the slot's
    best eligible neutral score move; the sum is clipped once to max_depth_score_shift.
    Returns {adjustments: {slot: {player_id: float}}, reasons: [...]} (empty unless enabled).
    """
    config = config or OFF
    if playing_time is not None and playing_time not in PLAYING_TIME:
        raise CoachingPolicyError("playing_time must be YOUTH, VETERANS or MERIT")
    rows = [_candidate(c) for c in candidates]
    seen = set()
    for row in rows:
        key = (row["slot"], row["player_id"])
        if key in seen:
            raise CoachingPolicyError(f"duplicate candidate {key}")
        seen.add(key)
    if config.mode == "off":
        return {"adjustments": {}, "reasons": [{"code": "OFF"}]}
    traits = _traits(coach_view)
    w = config.weights
    youth_pref = w.get("youth_developer", 0.0) if YOUTH_TRAIT in traits else 0.0
    vision = w.get("team_vision", 0.0)
    prefs = {"youth": youth_pref + (vision if playing_time == "YOUTH" else 0.0),
             "veteran": vision if playing_time == "VETERANS" else 0.0, "other": 0.0}
    adjustments: dict[str, dict[int, float]] = {}
    reasons: list[dict[str, Any]] = []
    for slot in sorted({r["slot"] for r in rows}):
        live = [r for r in rows if r["slot"] == slot and r["eligible"] and not r["out"]]
        if not live:
            continue
        best = max(r["neutral_score"] for r in live)
        for r in sorted(live, key=lambda r: r["player_id"]):
            pref = prefs[r["cohort"]] * r["cohort_weight"]
            if pref <= 0 or best - r["neutral_score"] > config.close_score_window:
                continue
            amount = round(min(config.max_depth_score_shift, pref), 10)
            if not amount:
                continue
            adjustments.setdefault(slot, {})[r["player_id"]] = amount
            codes = []
            if r["cohort"] == "youth" and youth_pref:
                codes.append("YOUTH_DEVELOPER")
            if (r["cohort"] == "youth" and playing_time == "YOUTH") or (r["cohort"] == "veteran" and playing_time == "VETERANS"):
                codes.append("TEAM_VISION_" + playing_time)
            reasons.append({"slot": slot, "player_id": r["player_id"], "codes": codes, "amount": amount,
                            "gap": round(best - r["neutral_score"], 10)})
    if config.mode == "shadow":
        return {"adjustments": {}, "reasons": [{"code": "SHADOW"}],
                "shadow_adjustments": adjustments, "shadow_reasons": reasons}
    return {"adjustments": adjustments, "reasons": reasons}


def rank_scheme_choices(*, choices: Sequence[Mapping[str, Any]], coach_view: Mapping[str, Any] | None,
                        config: CoachingConfig | None) -> dict:
    """Advice only: rank caller-supplied LEGAL choices; stable ties by choice_id.

    Only a choice flagged supported_tendency (the coach's reviewed, scoped football tendency
    matches it) can move, by min(weights.scheme_tendency, max_scheme_score_shift).
    continuity_cost stays with its owning engine (not subtracted again here); SCHEME_NERD or
    high openness add context only. Nothing is installed or executed.
    """
    config = config or OFF
    legal, attribution = [], []
    ids = set()
    for raw in choices:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("choice_id"), str) or not raw["choice_id"]:
            raise CoachingPolicyError("choices need a text choice_id")
        if raw["choice_id"] in ids:
            raise CoachingPolicyError("duplicate choice_id")
        ids.add(raw["choice_id"])
        score = _finite(raw.get("neutral_score"), "neutral_score")
        _finite(raw.get("continuity_cost", 0), "continuity_cost")
        if not raw.get("legal"):
            attribution.append({"choice_id": raw["choice_id"], "code": "ILLEGAL_EXCLUDED"})
            continue
        shift = 0.0
        if config.mode == "enabled" and raw.get("supported_tendency"):
            shift = min(config.weights.get("scheme_tendency", 0.0), config.max_scheme_score_shift)
        legal.append((raw["choice_id"], score, shift))
        if shift:
            attribution.append({"choice_id": raw["choice_id"], "code": "SUPPORTED_TENDENCY",
                                "shift": round(shift, 10)})
    context = sorted(t for t in _traits(coach_view) if t in CONTEXT_TRAITS)
    if context:
        attribution.append({"code": "CONTEXT_ONLY", "traits": context})
    ranked = [cid for cid, s, d in sorted(legal, key=lambda t: (-(t[1] + t[2]), t[0]))]
    out = {"ranked": ranked, "advice_only": True, "attribution": attribution, "mode": config.mode}
    if config.mode == "shadow":
        shadow_shift = min(config.weights.get("scheme_tendency", 0.0), config.max_scheme_score_shift)
        out["shadow_ranked"] = [cid for cid, s, d in sorted(
            ((c, s, shadow_shift if next(r for r in choices if r["choice_id"] == c).get("supported_tendency") else 0.0)
             for c, s, _ in legal), key=lambda t: (-(t[1] + t[2]), t[0]))]
    return out


__all__ = ["CoachingConfig", "CoachingPolicyError", "OFF", "opportunity_adjustments",
           "rank_scheme_choices", "canonical"]
