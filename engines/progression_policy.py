"""Pure candidate policy for lasting development and revisable potential bands.

The calendar/accounting adapter decides which evidence belongs to a checkpoint and
subtracts previously acknowledged entitlements. This module never stores state,
writes Madden dev fields, consumes form, or interprets an existing dev label.
All numerical defaults below are explicit calibration candidates, not NFL fits.
Band group ceilings and underlying_ovr are OVR-like scalars; raw attribute values
are used only to bound attribute entitlements, never as substitutes for OVR.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from typing import Any, Mapping, Sequence

MENTAL_SKILLS = frozenset(("Awareness", "PlayRecognition"))
PHYSICAL_SKILLS = frozenset(("Speed", "Acceleration", "Agility", "ChangeOfDirection",
                             "Strength", "Jumping", "Stamina", "ThrowPower", "KickPower", "Toughness"))
FIXED_SKILLS = frozenset(("Injury",))


@dataclass(frozen=True)
class PolicyConfig:
    """Versioned initial tuning candidates; callers must persist the chosen config."""
    mental_points_per_game: float = 0.12
    technical_points_per_game: float = 0.08
    review_points_per_rating: float = 0.75
    approach_width: float = 5.0
    resistance_floor: float = 0.10
    age_taper_start: float = 26.0
    age_taper_rate: float = 0.08
    minimum_review_signal: float = 0.50
    minimum_review_reliability: float = 0.15
    maximum_review_probability: float = 0.50
    maximum_band_step: float = 3.0
    band_review_distance: float = 3.0
    label_thresholds: tuple[float, float, float] = (75.0, 85.0, 95.0)
    label_technical_weight: float = 0.70

    def __post_init__(self):
        for key, value in asdict(self).items():
            if key == "label_thresholds":
                if len(value) != 3 or any(not math.isfinite(float(v)) for v in value):
                    raise ValueError("label_thresholds must contain three finite candidates")
                if list(value) != sorted(set(value)):
                    raise ValueError("label_thresholds must be strictly increasing")
            elif not math.isfinite(float(value)) or value < 0:
                raise ValueError(f"{key} must be finite and nonnegative")
        for key in ("resistance_floor", "minimum_review_reliability",
                    "maximum_review_probability", "label_technical_weight", "minimum_review_signal"):
            if getattr(self, key) > 1:
                raise ValueError(f"{key} must be between zero and one")
        if self.approach_width == 0:
            raise ValueError("approach_width must be positive")


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be numeric, not boolean")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _unit_draw(identity: Mapping[str, Any], purpose: str) -> float:
    payload = json.dumps({**identity, "purpose": purpose}, sort_keys=True, separators=(",", ":"))
    return int.from_bytes(hashlib.sha256(payload.encode()).digest()[:8], "big") / 2**64


def _validated_band(band: Mapping[str, Any]) -> dict[str, Any]:
    groups = band.get("group_ceilings") or {}
    if not all(key in groups for key in ("physical", "technical", "mental")):
        raise ValueError("band requires physical, technical and mental OVR-like group ceilings")
    out = dict(band)
    out["group_ceilings"] = {key: _finite(value, f"group_ceilings.{key}") for key, value in groups.items()}
    if any(not 0 <= v <= 99 for v in out["group_ceilings"].values()):
        raise ValueError("group ceilings must be between zero and 99")
    return out


def summarize_band(band: Mapping[str, Any], config: PolicyConfig | None = None) -> str:
    """Desired display label only; does not authorize a Madden TraitDevelopment write.

    75/85/95 and the weighting are provisional configurable candidates. Physical
    athleticism alone cannot create a higher development label.
    """
    config = config or PolicyConfig()
    groups = _validated_band(band)["group_ceilings"]
    value = (config.label_technical_weight * groups["technical"]
             + (1 - config.label_technical_weight) * groups["mental"])
    return ("Normal", "Star", "Superstar", "X-Factor")[sum(value >= t for t in config.label_thresholds)]


def growth_resistance(underlying_ovr: float, group_ceiling: float,
                      config: PolicyConfig | None = None) -> float:
    """Smooth soft resistance near/above a band's edge; never an absolute wall."""
    config = config or PolicyConfig()
    distance = max(0.0, (_finite(underlying_ovr, "underlying_ovr")
                         - _finite(group_ceiling, "group_ceiling") + config.approach_width)
                        / config.approach_width)
    return max(config.resistance_floor, 1 / (1 + distance * distance))


def evaluate_development(*, player_id: int, earned_season: int, event_id: str,
                         rules_version: str, ratings: Mapping[str, float],
                         underlying_ovr: float, band: Mapping[str, Any],
                         evidence: Mapping[str, Any], checkpoint_kind: str = "MENTAL",
                         age: float | None = None, supported_skills: Sequence[str] | None = None,
                         config: PolicyConfig | None = None) -> dict[str, Any]:
    """Return fractional incremental entitlements, never applied/rounded save edits.

    evidence consumes Unit B's window_id/evidence_ids/eligible/reliability/
    performance_signal/meaningful_appearances/opportunities/supported_skills.
    MENTAL and TECHNICAL checkpoints can learn from meaningful reps before the
    longer breakout-review window qualifies. REVIEW requires eligibility plus
    positive reliable performance and a reproducible confidence draw; eligibility
    alone never earns improvement. Root's ledger quantizes and deduplicates these
    increments and reconciles the offseason remainder. No input form is consumed.
    """
    config = config or PolicyConfig()
    if checkpoint_kind not in ("MENTAL", "TECHNICAL", "REVIEW"):
        raise ValueError("checkpoint_kind must be MENTAL, TECHNICAL or REVIEW")
    if not event_id or not rules_version:
        raise ValueError("event_id and rules_version are required")
    ovr = _finite(underlying_ovr, "underlying_ovr")
    if not 0 <= ovr <= 99:
        raise ValueError("underlying_ovr must be between zero and 99")
    target_band = _validated_band(band)
    original_groups = dict(target_band["group_ceilings"])
    current = {key: _finite(value, f"rating.{key}") for key, value in ratings.items()}
    if any(not 0 <= value <= 99 for value in current.values()):
        raise ValueError("ratings must be between zero and 99")
    reliability = _finite(evidence.get("reliability", 0), "reliability")
    if not 0 <= reliability <= 1:
        raise ValueError("reliability must be between zero and one")
    signal = evidence.get("performance_signal")
    if signal is not None:
        signal = _finite(signal, "performance_signal")
        if not -1 <= signal <= 1:
            raise ValueError("performance_signal must be between minus one and one")
    appearances = _finite(evidence.get("meaningful_appearances", 0), "meaningful_appearances")
    opportunities = _finite(evidence.get("opportunities", 0), "opportunities")
    if appearances < 0 or opportunities < 0:
        raise ValueError("evidence counts cannot be negative")
    evidence_ids = list(evidence.get("evidence_ids") or [])
    # Display-mapping candidates cannot influence a player's learning/confidence draw.
    learning_config = {key: value for key, value in asdict(config).items()
                       if key not in ("label_thresholds", "label_technical_weight")}
    identity = {"player_id": player_id, "earned_season": earned_season, "event_id": event_id,
                "rules_version": rules_version, "window_id": evidence.get("window_id"),
                "config": learning_config}
    reasons: list[str] = []
    skill_deltas: dict[str, float] = {}
    band_changes: dict[str, float] = {}
    age_factor = 1.0
    if age is not None:
        age = _finite(age, "age")
        if age < 0:
            raise ValueError("age cannot be negative")
        age_factor = 1 / (1 + max(0.0, age - config.age_taper_start) * config.age_taper_rate)
    observed = set(supported_skills if supported_skills is not None
                   else evidence.get("supported_skills") or [])
    if checkpoint_kind == "MENTAL":
        chosen = sorted(MENTAL_SKILLS & current.keys())
    elif checkpoint_kind == "TECHNICAL":
        chosen = sorted(observed & current.keys() - MENTAL_SKILLS - PHYSICAL_SKILLS - FIXED_SKILLS)
    else:
        chosen = sorted(observed & current.keys() - PHYSICAL_SKILLS - FIXED_SKILLS)
    has_reps = bool(evidence_ids and appearances > 0 and opportunities > 0 and reliability > 0)
    can_award = has_reps
    if not has_reps:
        reasons.append("INSUFFICIENT_MEANINGFUL_EVIDENCE")
    if checkpoint_kind == "REVIEW":
        probability = min(config.maximum_review_probability,
                          max(0.0, (signal or 0.0)) * reliability * config.maximum_review_probability)
        can_award = (has_reps and bool(evidence.get("eligible")) and signal is not None
                     and signal >= config.minimum_review_signal
                     and reliability >= config.minimum_review_reliability
                     and _unit_draw(identity, "review-confidence") < probability)
        if not can_award:
            reasons.append("REVIEW_NO_CONFIRMED_LASTING_CHANGE")
        else:
            reasons.append("SUSTAINED_SUPPORTED_PERFORMANCE")
            # A review can revise potential without inventing a direct skill mapping.
            for group in ("technical", "mental"):
                old = original_groups[group]
                if ovr >= old - config.band_review_distance:
                    step = min(99 - old, config.maximum_band_step * reliability * signal)
                    if step > 0:
                        target_band["group_ceilings"][group] = round(old + step, 6)
                        band_changes[group] = round(step, 6)
    if can_award:
        variation = 0.85 + 0.30 * _unit_draw(identity, "learning-variation")
        if checkpoint_kind == "REVIEW":
            points = config.review_points_per_rating * reliability * signal * age_factor * variation
        else:
            rate = (config.mental_points_per_game if checkpoint_kind == "MENTAL"
                    else config.technical_points_per_game)
            # Performance modifies learning modestly; opportunity is not a proxy for wins.
            points = rate * appearances * reliability * age_factor * variation * (0.75 + 0.25 * max(0.0, signal or 0.0))
        for skill in chosen:
            group = "mental" if skill in MENTAL_SKILLS else "technical"
            change = min(99 - current[skill], points * growth_resistance(
                ovr, target_band["group_ceilings"][group], config))
            if round(change, 6) > 0:
                skill_deltas[skill] = round(change, 6)
        if skill_deltas:
            reasons.append("EXPERIENTIAL_LEARNING" if checkpoint_kind != "REVIEW" else "SUPPORTED_SKILL_IMPROVEMENT")
        elif not chosen:
            reasons.append("NO_SUPPORTED_SKILL_MAPPING")
    outcome = ("BOTH" if skill_deltas and band_changes else "SKILL" if skill_deltas
               else "BAND" if band_changes else "NO_CHANGE")
    public = {"outcome": outcome, "reasons": list(reasons), "checkpoint_kind": checkpoint_kind}
    return {"outcome": outcome, "skill_deltas": skill_deltas, "band_changes": band_changes,
            "target_band": target_band, "desired_label": summarize_band(target_band, config),
            "actual_dev_write": None, "reasons": reasons,
            "provenance": {"player_id": player_id, "earned_season": earned_season,
                           "event_id": event_id, "rules_version": rules_version,
                           "window_id": evidence.get("window_id"), "evidence_ids": evidence_ids,
                           "trigger": evidence.get("trigger"), "config": asdict(config)},
            "public_summary": public}
