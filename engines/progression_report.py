"""Pure, versioned progression explanations with an explicit public boundary.

Projection does not apply progression or certify a receipt. PREPARED events are
PROPOSED; only explicitly APPLIED events carry APPLIED status. Annual calculation
rows remain CALCULATED. Private provenance is opt-in and never part of persisted
public explanations. Missing context stays unknown instead of being reconstructed
from a possibly planned or incomplete delta.
"""
from __future__ import annotations
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
import math
import re
from typing import Any

from engines import ovr_weights as ow

RATINGS = frozenset("Acceleration Agility Awareness BCVision BlockShedding BreakSack BreakTackle Carrying CatchInTraffic Catching ChangeOfDirection DeepRouteRunning FinesseMoves HitPower ImpactBlocking Injury JukeMove Jumping KickAccuracy KickPower KickReturn LeadBlock LongSnap ManCoverage MediumRouteRunning PassBlock PassBlockFinesse PassBlockPower PlayAction PlayRecognition PowerMoves Press Pursuit Release RunBlock RunBlockFinesse RunBlockPower ShortRouteRunning SpectacularCatch Speed SpinMove Stamina StiffArm Strength Tackle ThrowAccuracy ThrowAccuracyDeep ThrowAccuracyMid ThrowAccuracyShort ThrowOnTheRun ThrowPower ThrowUnderPressure Toughness Trucking ZoneCoverage".split())
REASON_CODES = frozenset("INSUFFICIENT_MEANINGFUL_EVIDENCE REVIEW_NO_CONFIRMED_LASTING_CHANGE SUSTAINED_SUPPORTED_PERFORMANCE EXPERIENTIAL_LEARNING SUPPORTED_SKILL_IMPROVEMENT NO_SUPPORTED_SKILL_MAPPING AGE INJURY WEAR UPKEEP PLAYING_TIME COACHING PERSONALITY DEVELOPMENT_RESISTANCE POTENTIAL_HEADROOM BREAKTHROUGH BREAKOUT STANDOUT_SEASON POOR_SEASON DEVELOPED REGRESSED ON_TRACK STALLED ANNUAL_RECONCILIATION".split())
FAILURE_CODES = frozenset("MISSING_BEFORE_RATINGS MISSING_AFTER_RATINGS MISSING_TARGET_RATINGS INVALID_RATINGS OVR_UNAVAILABLE UNKNOWN_EVENT_STATUS INCOMPLETE_RATING_CONTEXT RATINGS_UNAVAILABLE RATING_DISTRIBUTION_FAILED".split())
LABELS = frozenset(("Normal", "Star", "Superstar", "X-Factor", "XFactor", "Superstar X-Factor", "Hidden", "College_Impact", "College_Star", "College_Elite"))
STATUSES = frozenset(("PROPOSED", "APPLIED", "CALCULATED"))
KINDS = frozenset(("CHECKPOINT", "OFFSEASON", "ANNUAL", "MENTAL", "TECHNICAL", "REVIEW"))
FORM_FIELDS = ("before_requested", "after_requested", "before_effective_ovr", "after_effective_ovr",
               "before_ovr_delta", "after_ovr_delta")
PERSONALITY_SCHEMA = "personality_development_v1"
PERSONALITY_REASONS = frozenset(("PERSONALITY", "COACHING"))
CAUSE_MAP = {"CEILING": "POTENTIAL_HEADROOM", "DEV": "DEVELOPED"}
POSITION_MAP = {"LEDG": "LE", "REDG": "RE", "MIKE": "MLB", "WILL": "ROLB", "SAM": "LOLB", "RB": "HB"}


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    return int(value) if value.is_integer() else value


def _safe_codes(values, allowed):
    if not isinstance(values, (list, tuple)):
        return []
    return sorted({value for value in values if isinstance(value, str) and value in allowed})


def _json(value):
    if isinstance(value, Mapping):
        return {str(key): _json(item) for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))}
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, (float, Decimal)):
        return _num(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return f"UNSUPPORTED_TYPE:{type(value).__name__}"


def _rating_values(value):
    if not isinstance(value, Mapping) or not value:
        return None
    out = {}
    for key, number in value.items():
        n = _num(number)
        if key not in RATINGS or n is None or not 0 <= n <= 99:
            return None
        out[key] = n
    return out


def _deltas(value):
    if not isinstance(value, Mapping):
        return None
    return {key: _num(number) for key, number in sorted(value.items())
            if key in RATINGS and _num(number) is not None and -99 <= _num(number) <= 99 and _num(number) != 0}


def _ovr(ratings, position, doc):
    if ratings is None or not isinstance(position, str):
        return None
    position = POSITION_MAP.get(position, position)
    try:
        doc = doc if doc is not None else ow.load_weights()
        arches = [a for a in doc["slots"][position] if a in doc["archetypes"]]
        needed = {key for a in arches for key in doc["archetypes"][a]["terms"]}
        # The fitted legacy models contain an old ThrowAccuracy term for some
        # non-QB slots. The actual 54-field reader omits that obsolete field;
        # ow's established model convention treats it as zero. Do not apply that
        # convention to genuinely missing current inputs such as Catching.
        if not arches or not (needed - {"ThrowAccuracy"}).issubset(ratings):
            return None
        modeled = dict(ratings)
        if "ThrowAccuracy" in needed:
            modeled.setdefault("ThrowAccuracy", 0)
        return max(0, min(99, int(ow.predict_ovr(doc, position, modeled)[0])))
    except (KeyError, TypeError, ValueError, OSError):
        return None


def _effective(base, overlay):
    if base is None or not isinstance(overlay, Mapping):
        return None
    result = dict(base)
    for key, value in overlay.items():
        delta = _num(value)
        if delta is None:
            return None
        if key in result:
            result[key] = max(0, min(99, result[key] + delta))
    return result


def _diff(before, after, stored=None):
    if before is None or after is None:
        return None
    return {key: after[key] - before[key] for key in sorted(before.keys() & after.keys())
            if after[key] != before[key]}


def _evidence(event, decisions, metadata):
    explicit = metadata.get("report_evidence")
    explicit = explicit if isinstance(explicit, Mapping) else {}
    stored = explicit.get("ids", event.get("evidence_ids"))
    if stored is None:
        pieces = [d.get("evidence_ids") for d in decisions if isinstance(d, Mapping) and d.get("evidence_ids") is not None]
        stored = [gid for piece in pieces for gid in piece] if pieces else None
    ids = None if stored is None else sorted({gid for gid in stored if isinstance(gid, (str, int)) and not isinstance(gid, bool)}, key=str)
    reliability = _num(explicit.get("reliability"))
    if reliability is None:
        recorded = [d["evidence"] for d in decisions if isinstance(d, Mapping) and isinstance(d.get("evidence"), Mapping)]
        if len(recorded) == 1:
            reliability = _num(recorded[0].get("reliability"))
    return {"ids": ids, "count": _num(explicit.get("count")) if "count" in explicit else (len(ids) if ids is not None else None),
            "reliability": reliability if reliability is not None and 0 <= reliability <= 1 else None}


def _terms(row):
    if not isinstance(row, Mapping):
        return {"additive": {}, "included": {}}
    additive = {key: _num(value) for key, value in sorted(row.items())
                if key.endswith("_term") and key != "upkeep_term" and _num(value) is not None}
    included = {}
    if _num(row.get("upkeep_term")) is not None:
        included["upkeep_term"] = {"value": _num(row["upkeep_term"]), "included_in": "league_term"}
    return {"additive": additive, "included": included}


def _band_diff(before, after):
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return None
    return {"before": _json(before), "after": _json(after),
            "group_deltas": {key: _num(after.get("group_ceilings", {}).get(key)) - _num(value)
                for key, value in (before.get("group_ceilings") or {}).items()
                if _num(value) is not None and _num((after.get("group_ceilings") or {}).get(key)) is not None}}


def _shape(status, identity, calendar, before, after, planned_ovr, position, before_overlay,
           after_overlay, before_form, after_form, desired, actual_before, actual_after, reasons,
           evidence, failures, doc, stored_deltas=None):
    before_ovr, after_ovr = _ovr(before, position, doc), _ovr(after, position, doc)
    before_effective = _ovr(_effective(before, before_overlay), position, doc)
    after_effective = _ovr(_effective(after, after_overlay), position, doc)
    return {"schema_version": 1, "status": status, "identity": identity, "calendar": calendar,
            "permanent": {"before_ovr": before_ovr, "after_ovr": after_ovr,
                          "planned_ovr": _num(planned_ovr), "rating_changes": _diff(before, after, stored_deltas),
                          "before_ovr_source": "RATINGS" if before_ovr is not None else None,
                          "after_ovr_source": "RATINGS" if after_ovr is not None else None},
            "form": {"before_requested": _num(before_form), "after_requested": _num(after_form),
                     "before_effective_ovr": before_effective, "after_effective_ovr": after_effective,
                     "before_ovr_delta": before_effective - before_ovr if before_effective is not None and before_ovr is not None else None,
                     "after_ovr_delta": after_effective - after_ovr if after_effective is not None and after_ovr is not None else None},
            "reasons": _safe_codes(reasons, REASON_CODES), "evidence": evidence,
            "development_label": {"desired": desired if isinstance(desired, str) and desired in LABELS else None,
                                  "actual_before": actual_before if isinstance(actual_before, str) and actual_before in LABELS else None,
                                  "actual_after": actual_after if isinstance(actual_after, str) and actual_after in LABELS else None},
            "failures": _safe_codes(failures, FAILURE_CODES)}


def _audience(audience):
    if audience not in ("public", "private"):
        raise ValueError("audience must be public or private")


def project_event(event, *, audience="public", doc=None):
    """Project one persisted proposal/receipt; never infer APPLIED from this call."""
    _audience(audience)
    metadata = event.get("metadata") or {}
    context = metadata.get("report_context") or {}
    decisions = metadata.get("decisions") or []
    before = _rating_values(context.get("before_base"))
    after = _rating_values(event.get("candidate_base"))
    failures = []
    if before is None:
        failures.append("MISSING_BEFORE_RATINGS" if context.get("before_base") is None else "INVALID_RATINGS")
    if after is None:
        failures.append("MISSING_AFTER_RATINGS" if event.get("candidate_base") is None else "INVALID_RATINGS")
    if before is not None and after is not None and before.keys() != after.keys():
        failures.append("INCOMPLETE_RATING_CONTEXT")
    source_status = event.get("status")
    status = "APPLIED" if source_status == "APPLIED" else "PROPOSED"
    if source_status not in ("PREPARED", "APPLIED"):
        failures.append("UNKNOWN_EVENT_STATUS")
    reasons = [reason for d in decisions if isinstance(d, Mapping)
               for reason in ((d.get("result") or {}).get("public_summary") or {}).get("reasons", [])]
    annual = metadata.get("annual_result") or {}
    cause = annual.get("cause")
    reasons.append(CAUSE_MAP.get(cause, cause))
    # Personality development (PERSONALITY_DEVELOPMENT_COACHING_PLAN U3): only supported effects of
    # APPLIED enabled receipts name PERSONALITY/COACHING; shadow and proposals never claim one.
    personality = metadata.get(PERSONALITY_SCHEMA) if isinstance(metadata.get(PERSONALITY_SCHEMA), Mapping) else {}
    if source_status == "APPLIED" and personality.get("mode") == "enabled" and personality.get("attributed_effect"):
        reasons.extend(r for r in personality.get("public_reasons") or [] if r in PERSONALITY_REASONS)
    application_week = metadata.get("application_week")
    if application_week is None:
        if (event.get("kind") == "OFFSEASON" and isinstance(event.get("application_season"), int)
                and isinstance(event.get("earned_season"), int)
                and event["application_season"] > event["earned_season"]):
            application_week = 0
        else:
            application_week = metadata.get("week")
    result = _shape(status,
        {"player_id": event.get("player_id"), "event_id": event.get("event_id"), "kind": event.get("kind")},
        {"earned_season": event.get("earned_season"), "application_season": event.get("application_season"), "week": application_week},
        before, after, annual.get("new_ovr"), context.get("position", metadata.get("position")),
        context.get("before_overlay"), event.get("overlay"), context.get("before_form"), metadata.get("form"),
        metadata.get("desired_label"), context.get("actual_dev_before"),
        context.get("actual_dev_after") if status == "APPLIED" else None,
        reasons, _evidence(event, decisions, metadata), failures, doc, event.get("annual_deltas"))
    if result["permanent"]["before_ovr"] is None or result["permanent"]["after_ovr"] is None:
        result["failures"] = sorted(set(result["failures"]) | {"OVR_UNAVAILABLE"})
    if audience == "private":
        requested = {key: _num(value) for key, value in (metadata.get("annual_entitlement") or {}).items()
                     if _num(value) is not None}
        for decision in decisions:
            for key, value in ((decision.get("result") or {}).get("skill_deltas") or {}).items():
                if _num(value) is not None:
                    requested[key] = requested.get(key, 0) + _num(value)
        result["audit"] = {"before_ratings": before, "after_ratings": after,
            "requested_fractional": requested, "carry": _json(metadata.get("fractions")),
            "band_changes": _band_diff(context.get("before_band"), event.get("candidate_band")),
            "term_breakdown": _terms(annual), "calculation": _json(annual.get("calculation")),
            "provenance": _json(event)}
        if personality:
            result["audit"]["personality_development"] = _json(personality)
    return result


def project_annual(row, *, audience="public", doc=None):
    """Project an annual engine calculation, distinguishing its OVR plan from its ratings result."""
    _audience(audience)
    before = _rating_values(row.get("ratings_before"))
    after = _rating_values(row.get("target_ratings"))
    failures = []
    if before is None:
        failures.append("MISSING_BEFORE_RATINGS" if row.get("ratings_before") is None else "INVALID_RATINGS")
    if after is None:
        failures.append("MISSING_TARGET_RATINGS" if row.get("target_ratings") is None else "INVALID_RATINGS")
    if row.get("failure_code") in FAILURE_CODES:
        failures.append(row["failure_code"])
    cause = row.get("cause")
    result = _shape("CALCULATED", {"player_id": row.get("player_id"), "event_id": None, "kind": "ANNUAL"},
        {"earned_season": row.get("earned_season", row.get("season") - 1 if isinstance(row.get("season"), int) else None),
         "application_season": row.get("season"), "week": row.get("week")},
        before, after, row.get("new_ovr"), row.get("position"), None, None, None, None,
        row.get("desired_label"), row.get("dev_before"), None,
        [CAUSE_MAP.get(cause, cause)], {"ids": None, "count": None, "reliability": None}, failures, doc, row.get("ratings_diff"))
    if result["permanent"]["before_ovr"] is None:
        result["permanent"]["before_ovr"] = _num(row.get("old_ovr"))
        if result["permanent"]["before_ovr"] is not None:
            result["permanent"]["before_ovr_source"] = "RECORDED_OVR"
    if after is not None and result["permanent"]["after_ovr"] is None:
        result["failures"] = sorted(set(result["failures"]) | {"OVR_UNAVAILABLE"})
    if audience == "private":
        result["audit"] = {"before_ratings": before, "after_ratings": after,
            "requested_fractional": _json(row.get("annual_entitlement")), "carry": _json(row.get("fractions")),
            "band_changes": _band_diff(row.get("band_before"), row.get("target_band")),
            "term_breakdown": _terms(row), "calculation": _json(row.get("calculation")),
            "provenance": _json(row)}
    return result


def sanitize_public(report):
    """Revalidate stored JSON through a nested whitelist; malformed known types return None.

    Unknown keys are dropped at every level, including audit/raw metadata. Free
    text reason/error strings are never forwarded; only recognized codes survive.
    """
    if not isinstance(report, Mapping) or type(report.get("schema_version")) is not int or report.get("schema_version") != 1:
        return None
    if report.get("status") not in STATUSES:
        return None
    identity, calendar = report.get("identity"), report.get("calendar")
    if not isinstance(identity, Mapping) or not isinstance(calendar, Mapping):
        return None
    def numeric(value, lo=None, hi=None):
        if value is None:
            return None
        n = _num(value)
        if n is None or lo is not None and n < lo or hi is not None and n > hi:
            raise ValueError
        return n
    def integer(value, lo=None):
        n = numeric(value, lo)
        if n is not None and not isinstance(n, int):
            raise ValueError
        return n
    def section(key):
        value = report.get(key, {})
        if not isinstance(value, Mapping):
            raise ValueError
        return value
    try:
        pid = integer(identity.get("player_id"), 1)
        eid, kind = identity.get("event_id"), identity.get("kind")
        if eid is not None and (not isinstance(eid, str) or not re.fullmatch(r"[A-Za-z0-9:_./-]{1,256}", eid)):
            return None
        if kind is not None and kind not in KINDS:
            return None
        p, f, e, d = (section(key) for key in ("permanent", "form", "evidence", "development_label"))
        changes = p.get("rating_changes")
        if changes is not None:
            if not isinstance(changes, Mapping):
                return None
            changes = {key: numeric(value, -99, 99) for key, value in changes.items() if key in RATINGS}
            if any(value is None for value in changes.values()):
                return None
        sources = {key: p.get(key) for key in ("before_ovr_source", "after_ovr_source")}
        if any(value not in (None, "RATINGS", "RECORDED_OVR") for value in sources.values()):
            return None
        ids = e.get("ids")
        if ids is not None:
            if not isinstance(ids, (list, tuple)) or any(isinstance(gid, bool) or not isinstance(gid, (str, int)) for gid in ids):
                return None
            if any(isinstance(gid, str) and not re.fullmatch(r"[A-Za-z0-9:_./-]{1,256}", gid) for gid in ids):
                return None
            ids = sorted(set(ids), key=str)
        labels = {key: d.get(key) for key in ("desired", "actual_before", "actual_after")}
        if report["status"] != "APPLIED":
            labels["actual_after"] = None
        if any(value is not None and value not in LABELS for value in labels.values()):
            return None
        reasons, failures = report.get("reasons", []), report.get("failures", [])
        if not isinstance(reasons, (list, tuple)) or not isinstance(failures, (list, tuple)):
            return None
        clean = {"schema_version": 1, "status": report["status"],
            "identity": {"player_id": pid, "event_id": eid, "kind": kind},
            "calendar": {key: integer(calendar.get(key), 0) for key in ("earned_season", "application_season", "week")},
            "permanent": {"before_ovr": numeric(p.get("before_ovr"), 0, 99),
                          "after_ovr": numeric(p.get("after_ovr"), 0, 99),
                          "planned_ovr": numeric(p.get("planned_ovr"), 0, 99), "rating_changes": changes, **sources},
            "form": {key: numeric(f.get(key), -99 if key.endswith("delta") or key.endswith("requested") else 0, 99)
                     for key in FORM_FIELDS},
            "reasons": _safe_codes(reasons, REASON_CODES),
            "evidence": {"ids": ids, "count": integer(e.get("count"), 0),
                         "reliability": numeric(e.get("reliability"), 0, 1)},
            "development_label": labels, "failures": _safe_codes(failures, FAILURE_CODES)}
        return clean
    except (ValueError, TypeError):
        return None


sanitize_public_report = sanitize_public
