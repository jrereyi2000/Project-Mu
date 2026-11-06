"""Pure personality development policy: preparation, teaching, mentoring and annual limits.

docs/in_progress/PERSONALITY_DEVELOPMENT_COACHING_PLAN.md §§2.1-2.4. Nothing here reads a
database, a profile store or a save. Callers pass scoped views (`personality.core.feature_view`
for 'development' on players and mentors, 'coaching' on coaches) and the neutral output of
`engines.progression_policy.evaluate_development`. Effects scale positive eligible
mental/technical learning only: they never create learning where the neutral result has none,
never touch physical/fixed skills, negative deltas, eligibility, evidence, the band, REVIEW odds,
labels or dev writes. Defaults are off/zero; no numerical preset is selected here.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
import hashlib
import json
import math
from typing import Any, Callable, Mapping, Sequence

from engines.progression_policy import FIXED_SKILLS, MENTAL_SKILLS, PHYSICAL_SKILLS

POLICY_SCHEMA = "personality_development_v1"
MODES = ("off", "shadow", "enabled")
WEIGHT_KEYS = frozenset(("drive", "film", "technical_preparation", "coasting",
                         "teaching", "qb_teaching", "mentor"))
NONNEGATIVE_WEIGHTS = frozenset(("drive", "film", "technical_preparation", "teaching",
                                 "qb_teaching", "mentor"))
NONPOSITIVE_WEIGHTS = frozenset(("coasting",))
CAP_FIELDS = ("max_relative_change", "max_annual_abs_points",
              "max_annual_ovr_difference", "max_per_rating_annual_difference")

# Trait -> channel. Only these traits have a development consequence in release one
# (§2.2). YOUTH_DEVELOPER is an opportunity preference (engines/personality_coaching.py),
# MENTOR only scales the existing mentor component (mentor_adjustment). LONER, money,
# quiet, confidence, sociability, warmth, hometown and every other trait/slider are ignored.
FILM_TRAIT = "FILM_ROOM_RAT"
TECHNICAL_TRAIT = "PERFECTIONIST"
COASTING_TRAIT = "COASTING"
TEACHER_TRAIT = "TEACHER"
QB_TEACHER_TRAIT = "QB_WHISPERER"
MENTOR_TRAIT = "MENTOR"
DEFERRED_TRAITS = {"WORKOUT_FANATIC": "PHYSICAL_TRAINING_DEFERRED"}
PLAYER_CHANNELS = ("drive", "film", "technical_preparation", "coasting")
COACH_CHANNELS = ("teaching", "qb_teaching")
LEARNING_KINDS = ("MENTAL", "TECHNICAL")


class DevelopmentPolicyError(ValueError):
    """Invalid configuration or input; an enabled batch must not silently neutralize it."""


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise DevelopmentPolicyError(f"{name} must be numeric, not boolean")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise DevelopmentPolicyError(f"{name} must be finite") from None
    if not math.isfinite(number):
        raise DevelopmentPolicyError(f"{name} must be finite")
    return number


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class DevelopmentConfig:
    """Reviewed, versioned policy input. Off (the default) preserves output exactly."""
    version: str = ""
    mode: str = "off"
    weights: Mapping[str, float] = field(default_factory=dict)
    max_relative_change: float = 0.0
    max_annual_abs_points: float = 0.0
    max_annual_ovr_difference: float = 0.0
    max_per_rating_annual_difference: float = 0.0

    def __post_init__(self):
        if self.mode not in MODES:
            raise DevelopmentPolicyError("mode must be off, shadow or enabled")
        if not isinstance(self.version, str):
            raise DevelopmentPolicyError("version must be text")
        if not isinstance(self.weights, Mapping):
            raise DevelopmentPolicyError("weights must be a mapping")
        clean = {}
        for key, value in self.weights.items():
            if key not in WEIGHT_KEYS:
                raise DevelopmentPolicyError(f"unknown development weight {key!r}")
            number = _finite(value, f"weights.{key}")
            if key in NONNEGATIVE_WEIGHTS and number < 0:
                raise DevelopmentPolicyError(f"weights.{key} must be nonnegative")
            if key in NONPOSITIVE_WEIGHTS and number > 0:
                raise DevelopmentPolicyError(f"weights.{key} must be nonpositive")
            clean[key] = number
        object.__setattr__(self, "weights", dict(sorted(clean.items())))
        for name in CAP_FIELDS:
            number = _finite(getattr(self, name), name)
            if number < 0:
                raise DevelopmentPolicyError(f"{name} must be nonnegative")
            object.__setattr__(self, name, number)
        if self.max_relative_change > 1:
            raise DevelopmentPolicyError("max_relative_change must be between zero and one")
        if self.mode != "off" and not self.version.strip():
            raise DevelopmentPolicyError("shadow/enabled configs need an explicit reviewed version")
        if self.mode == "enabled" and any(getattr(self, name) <= 0 for name in CAP_FIELDS):
            raise DevelopmentPolicyError("an enabled config needs every cap above zero")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DevelopmentConfig":
        if not isinstance(data, Mapping):
            raise DevelopmentPolicyError("config must be an object")
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(data) - known)
        if unknown:
            raise DevelopmentPolicyError(f"unknown config fields: {unknown}")
        return cls(**dict(data))

    def as_dict(self) -> dict[str, Any]:
        return {"version": self.version, "mode": self.mode, "weights": dict(self.weights),
                **{name: getattr(self, name) for name in CAP_FIELDS}}

    def config_hash(self) -> str:
        return digest(self.as_dict())


OFF = DevelopmentConfig()


def _traits(view: Mapping[str, Any] | None) -> frozenset[str]:
    if not view or view.get("status") != "stored":
        return frozenset()
    return frozenset(str(t) for t in view.get("traits") or [])


def _slider(view: Mapping[str, Any] | None, name: str) -> float:
    if not view or view.get("status") != "stored":
        return 5.0
    value = _finite((view.get("sliders") or {}).get(name, 5), f"sliders.{name}")
    if not 0 <= value <= 10:
        raise DevelopmentPolicyError(f"sliders.{name} must be 0..10")
    return value


def profile_ref(view: Mapping[str, Any] | None, scope: str) -> dict[str, Any]:
    view = view or {}
    return {"persona_id": view.get("persona_id"), "revision": view.get("revision"),
            "catalogue_version": view.get("catalogue_version"), "scope": scope,
            "status": view.get("status", "missing")}


def eligible_skill(skill: str, kind: str) -> bool:
    if skill in PHYSICAL_SKILLS or skill in FIXED_SKILLS:
        return False
    if kind == "MENTAL":
        return skill in MENTAL_SKILLS
    return kind == "TECHNICAL" and skill not in MENTAL_SKILLS


def _coach_entries(coach_views: Sequence[Any] | None) -> list[dict[str, Any]]:
    """[{view, exposure 0..1, persona_id?}]. A bare view has no verified exposure (0)."""
    out = []
    for i, entry in enumerate(coach_views or []):
        if not isinstance(entry, Mapping):
            raise DevelopmentPolicyError("coach views must be mappings")
        if "view" in entry:
            view, exposure = entry.get("view"), entry.get("exposure", 0)
        else:
            view, exposure = entry, 0
        exposure = _finite(exposure, f"coach_views[{i}].exposure")
        if not 0 <= exposure <= 1:
            raise DevelopmentPolicyError("coach exposure must be between zero and one")
        out.append({"view": view, "exposure": exposure,
                    "persona_id": entry.get("persona_id") or (view or {}).get("persona_id")})
    return out


def _teaching(entries, *, skill_is_qb: bool, config: DevelopmentConfig):
    """Strongest single eligible teaching contribution (no HC+OC+assistant summing)."""
    best, source = 0.0, None
    for entry in entries:
        traits = _traits(entry["view"])
        generic = config.weights.get("teaching", 0.0) if TEACHER_TRAIT in traits else 0.0
        special = (config.weights.get("qb_teaching", 0.0)
                   if skill_is_qb and QB_TEACHER_TRAIT in traits else 0.0)
        # Strongest of generic/specialty, not both.
        value = max(generic, special) * entry["exposure"]
        if value > best:
            best, source = value, {"persona_id": entry["persona_id"],
                                   "channel": "qb_teaching" if special > generic else "teaching"}
    return best, source


def evaluate_learning_effect(*, neutral_result: Mapping[str, Any], player_view: Mapping[str, Any] | None,
                             coach_views: Sequence[Any] | None, mentor_view: Mapping[str, Any] | None,
                             context: Mapping[str, Any] | None, config: DevelopmentConfig | None) -> dict:
    """Scale each positive eligible neutral entitlement b to b*(1+s), s clamped once.

    context: {checkpoint_kind?: MENTAL|TECHNICAL|REVIEW (default: the neutral result's),
    is_qb?: bool, ratings?: {skill: current} for 0..99 headroom}. Returns
    {skill_deltas: the additional fractional effect per skill (empty unless enabled),
    attribution: private snapshot}. The neutral result is never modified.
    """
    config = config or OFF
    if not isinstance(config, DevelopmentConfig):
        raise DevelopmentPolicyError("config must be a DevelopmentConfig")
    context = dict(context or {})
    base = {"mode": config.mode, "policy_version": config.version, "config_hash": config.config_hash()}
    if config.mode == "off":
        return {"skill_deltas": {}, "attribution": {**base, "reasons": ["OFF"]}}
    neutral = neutral_result.get("skill_deltas") or {}
    kind = context.get("checkpoint_kind") or (neutral_result.get("public_summary") or {}).get("checkpoint_kind")
    is_qb = bool(context.get("is_qb"))
    ratings = context.get("ratings") or {}
    entries = _coach_entries(coach_views)
    reasons: list[str] = []
    traits = _traits(player_view)
    for trait, reason in sorted(DEFERRED_TRAITS.items()):
        if trait in traits:
            reasons.append(reason)
    if mentor_view is not None:
        reasons.append("MENTOR_VIA_EXISTING_COMPONENT_ONLY")
    if (player_view or {}).get("status") != "stored":
        reasons.append("PLAYER_PROFILE_MISSING")
    if not any(e["exposure"] > 0 and _traits(e["view"]) for e in entries):
        reasons.append("NO_VERIFIED_TEACHING_EXPOSURE")
    raw, capped, requested, sources = {}, {}, {}, {}
    if kind not in LEARNING_KINDS:
        reasons.append("REVIEW_UNCHANGED" if kind == "REVIEW" else "UNSUPPORTED_CHECKPOINT")
    else:
        w = config.weights
        drive = (_slider(player_view, "drive") - 5.0) / 5.0 * w.get("drive", 0.0)
        coasting = w.get("coasting", 0.0) if COASTING_TRAIT in traits else 0.0
        for skill, amount in sorted(neutral.items()):
            b = _finite(amount, f"skill_deltas.{skill}")
            if b <= 0 or not eligible_skill(skill, kind):
                continue
            channels = {"drive": drive, "coasting": coasting,
                        "film": (w.get("film", 0.0) if FILM_TRAIT in traits and skill in MENTAL_SKILLS else 0.0),
                        "technical_preparation": (w.get("technical_preparation", 0.0)
                                                  if TECHNICAL_TRAIT in traits and skill not in MENTAL_SKILLS else 0.0)}
            teach, source = _teaching(entries, skill_is_qb=is_qb, config=config)
            channels["teaching"] = teach
            if source:
                sources[skill] = source
            total = sum(channels.values())
            s = max(-config.max_relative_change, min(config.max_relative_change, total))
            effect = b * s
            if skill in ratings:
                headroom = 99.0 - _finite(ratings[skill], f"ratings.{skill}") - b
                effect = min(effect, max(0.0, headroom))
            raw[skill] = {k: round(v, 10) for k, v in channels.items() if v}
            capped[skill] = round(s, 10)
            if round(effect, 10):
                requested[skill] = round(effect, 10)
        if any(capped[k] != round(sum(raw[k].values()), 10) for k in capped):
            reasons.append("RELATIVE_CAP")
    attribution = {**base, "checkpoint_kind": kind, "neutral_skill_deltas": dict(sorted(neutral.items())),
                   "raw_channels": raw, "capped_channels": capped, "requested_effect": requested,
                   "teaching_sources": sources,
                   "profile_refs": [profile_ref(player_view, "development")]
                   + [profile_ref(e["view"], "coaching") for e in entries],
                   "reasons": sorted(set(reasons))}
    if config.mode == "shadow":
        return {"skill_deltas": {}, "attribution": {**attribution, "shadow_skill_deltas": requested}}
    return {"skill_deltas": dict(requested), "attribution": attribution}


def mentor_adjustment(*, neutral_component: Mapping[str, float], mentor_view: Mapping[str, Any] | None,
                      verified_exposure: bool, config: DevelopmentConfig | None) -> dict:
    """Bounded difference from the EXISTING mentor rating component (§2.3), never a new award.

    MENTOR scales positive component entries by min(weights.mentor, max_relative_change).
    Missing profile, no verified exposure, off mode or zero component -> zero difference.
    LONER alone is neutral: it neither erases mentoring nor proves a refusal.
    """
    config = config or OFF
    out = {"differences": {}, "reasons": [], "mode": config.mode}
    if config.mode == "off":
        out["reasons"].append("OFF")
        return out
    if not verified_exposure:
        out["reasons"].append("NO_VERIFIED_MENTOR_EXPOSURE")
        return out
    traits = _traits(mentor_view)
    if (mentor_view or {}).get("status") != "stored":
        out["reasons"].append("MENTOR_PROFILE_MISSING")
        return out
    if MENTOR_TRAIT not in traits:
        out["reasons"].append("NO_MENTOR_TRAIT")
        return out
    scale = min(config.weights.get("mentor", 0.0), config.max_relative_change)
    diffs = {}
    for skill, amount in sorted((neutral_component or {}).items()):
        value = _finite(amount, f"neutral_component.{skill}")
        if value > 0 and skill in MENTAL_SKILLS and round(value * scale, 10):
            diffs[skill] = round(value * scale, 10)
    if config.mode == "shadow":
        out["shadow_differences"] = diffs
        return out
    out["differences"] = diffs
    return out


def _applied(applied_effects: Mapping[str, Any] | None) -> tuple[dict[str, float], float]:
    """Accept {by_rating: {...}, abs_spend: x} or a plain {rating: signed sum} map."""
    applied_effects = applied_effects or {}
    if "by_rating" in applied_effects or "abs_spend" in applied_effects:
        by = {k: _finite(v, f"applied.{k}") for k, v in (applied_effects.get("by_rating") or {}).items()}
        spend = _finite(applied_effects.get("abs_spend", sum(abs(v) for v in by.values())), "abs_spend")
    else:
        by = {k: _finite(v, f"applied.{k}") for k, v in applied_effects.items()}
        spend = sum(abs(v) for v in by.values())
    if spend < 0:
        raise DevelopmentPolicyError("abs_spend cannot be negative")
    return by, spend


def limit_annual_effect(*, annual_start: Mapping[str, float], neutral_target: Mapping[str, float],
                        proposed_target: Mapping[str, float], applied_effects: Mapping[str, Any] | None,
                        config: DevelopmentConfig | None,
                        ovr_at: Callable[[Mapping[str, float]], float]) -> dict:
    """Apply the three combined player+teacher+mentor annual limits to one new increment.

    Targets are fractional absolute ratings. neutral_target already contains earlier APPLIED
    effects (they are in the base); the paired neutral counterfactual is neutral_target minus
    applied_effects. Limits: per-rating |applied + new| <= max_per_rating_annual_difference;
    applied abs spend + sum|new| <= max_annual_abs_points; |OVR(effect) - OVR(paired neutral)|
    <= max_annual_ovr_difference. Unused allowance never carries; a candidate whose caps cannot
    all be proven is refused (zero). Returns {target, annual_effects, remaining_budget, capped, reasons}.
    """
    config = config or OFF
    start = {k: _finite(v, f"annual_start.{k}") for k, v in annual_start.items()}
    neutral = {k: _finite(v, f"neutral_target.{k}") for k, v in neutral_target.items()}
    proposed = {k: _finite(v, f"proposed_target.{k}") for k, v in proposed_target.items()}
    if neutral.keys() != proposed.keys() or not set(start) <= set(neutral):
        raise DevelopmentPolicyError("annual start/neutral/proposed rating fields must match")
    by, spend = _applied(applied_effects)
    if any(k not in neutral for k in by):
        raise DevelopmentPolicyError("applied effects contain an unknown rating")
    reasons: list[str] = []
    request = {k: proposed[k] - neutral[k] for k in neutral if round(proposed[k] - neutral[k], 10)}
    if config.mode == "off" or not request:
        if config.mode == "off" and request:
            reasons.append("OFF")
        return {"target": dict(neutral), "annual_effects": {}, "capped": bool(request),
                "remaining_budget": _remaining(config, spend), "reasons": reasons}
    cap = config.max_per_rating_annual_difference
    new = {}
    for k, v in request.items():
        prior = by.get(k, 0.0)
        hi, lo = cap - prior, -cap - prior
        limited = min(v, max(0.0, hi)) if v > 0 else max(v, min(0.0, lo))
        if limited != v:
            reasons.append("PER_RATING_CAP")
        new[k] = limited
    room = max(0.0, config.max_annual_abs_points - spend)
    used = sum(abs(v) for v in new.values())
    if used > room:
        reasons.append("ANNUAL_POINTS_CAP")
        scale = room / used if used else 0.0
        new = {k: v * scale for k, v in new.items()}
    paired = {k: neutral[k] - by.get(k, 0.0) for k in neutral}
    base_ovr = _finite(ovr_at(paired), "ovr")

    def ovr_gap(scale: float) -> float:
        target = {k: neutral[k] + scale * new.get(k, 0.0) for k in neutral}
        return abs(_finite(ovr_at(target), "ovr") - base_ovr)

    limit = config.max_annual_ovr_difference
    if ovr_gap(1.0) > limit + 1e-9:
        reasons.append("ANNUAL_OVR_CAP")
        if ovr_gap(0.0) > limit + 1e-9:
            scale = 0.0
        else:
            lo, hi = 0.0, 1.0
            for _ in range(40):
                mid = (lo + hi) / 2
                lo, hi = (mid, hi) if ovr_gap(mid) <= limit else (lo, mid)
            scale = lo
        new = {k: v * scale for k, v in new.items()}
        if ovr_gap(1.0 if scale else 0.0) > limit + 1e-9:
            new = {}
    # Rating bounds: an effect never takes a target outside 0..99.
    effects = {}
    for k, v in new.items():
        bounded = max(0.0, min(99.0, neutral[k] + v)) - neutral[k]
        if round(bounded, 10):
            effects[k] = round(bounded, 10)
    target = {k: neutral[k] + effects.get(k, 0.0) for k in neutral}
    spent_now = sum(abs(v) for v in effects.values())
    capped = bool(reasons) or effects != {k: round(v, 10) for k, v in request.items()}
    return {"target": target, "annual_effects": effects, "capped": capped,
            "remaining_budget": _remaining(config, spend + spent_now),
            "reasons": sorted(set(reasons))}


def _remaining(config: DevelopmentConfig, spend: float) -> dict[str, float]:
    return {"abs_points": round(max(0.0, config.max_annual_abs_points - spend), 10)}
