"""Reconcile full-year calibrated targets with verified in-season permanent awards.

No old progression fit is run here. The caller computes annual targets using
annual_start_ratings overrides and disables legacy dev/breakthrough lotteries.
Form is reset, never converted into permanent growth. This module prepares only.
"""
from __future__ import annotations
import copy
import math
from engines import progression_state as state
from engines import progression_policy as policy


def reconcile_annual(base_start, current_base, annual_target, applied, carry):
    """Full-year absolute targets less earned permanent awards, per rating.

    Fractional in-season carry is part of the full-year entitlement, not an extra
    award on top. A reduced positive entitlement never claws back prior growth.
    An explicitly negative annual target can still reconcile aging/injury loss.
    """
    start, current = state._ratings(base_start), state._ratings(current_base)
    target = state._numbers(annual_target, "annual_target")
    spent = state._numbers(applied, "applied")
    state._numbers(carry, "carry")  # validate, but do not pay it a second time
    if start.keys() != current.keys() or start.keys() != target.keys():
        raise state.ProgressionStateError("annual start/current/target rating fields must match")
    if any(not 0 <= v <= 99 for v in target.values()):
        raise state.ProgressionStateError("annual targets must be in 0..99")
    if any(k not in start for k in spent):
        raise state.ProgressionStateError("applied annual accounting contains unknown rating")
    entitlement = {k: target[k] - start[k] for k in start}
    remaining = state.annual_remainder(entitlement, spent)
    candidate, deltas, fractions = {}, {}, {}
    for rating, current_value in current.items():
        residual = remaining[rating]
        # Avoid revoking already recognized development merely because a later
        # estimate shrank. Actual decline has an explicitly negative annual target.
        if entitlement[rating] >= 0:
            residual = max(0.0, residual)
        else:
            residual = min(0.0, residual)
        whole = math.trunc(residual)
        candidate[rating] = max(0, min(99, current_value + whole))
        actual = candidate[rating] - current_value
        if actual:
            deltas[rating] = actual
        fractions[rating] = round(residual - whole, 10) if actual == whole else 0.0
    return {"candidate_base": candidate, "annual_deltas": deltas,
            "fractions": fractions, "annual_entitlement": entitlement}


def annual_start_ratings(cur, player_id, earned_season):
    """Recover immutable start from earliest applied checkpoint, else explicit bootstrap state."""
    events = state.load_events(cur, player_id, earned_season)
    for event in events:
        start = event["metadata"].get("annual_start")
        if start is not None:
            return state._ratings(start)
    current = state.load_state(cur, player_id)
    if current is None:
        raise state.ProgressionStateError("explicit neutral bootstrap required")
    if current["application_season"] > earned_season:
        raise state.ProgressionStateError("year-start ratings unavailable after rollover")
    return dict(current["base_ratings"])


def prepare_offseason(cur, rows, *, earned_season, application_season, source_fingerprint,
                      rules_version, pgid_by_player=None, week=24, config=None):
    """Prepare full-year remainder events; caller supplies targets calculated from year start.

    Each row needs player_id and absolute target_ratings; optional band changes are
    accepted only as explicit target_band, never reconstructed from legacy dev labels.
    Existing applied final events are returned unchanged for safe replay handling.
    """
    if application_season <= earned_season:
        raise state.ProgressionStateError("offseason must apply after the earned season")
    events = []
    with state._savepoint(cur):
        for row in rows:
            pid = row["player_id"]
            existing = next((e for e in state.load_events(cur, pid, earned_season, status=None)
                             if e["kind"] == "OFFSEASON" and e["ordinal"] == "final"
                             and e["rules_version"] == rules_version), None)
            if existing:
                if existing["status"] == "APPLIED":
                    replay_state = state.load_state(cur, pid)
                    if (replay_state is None or replay_state["revision"] != existing["base_revision"] + 1
                            or replay_state["application_season"] != existing["application_season"]):
                        raise state.ProgressionStateError("historical offseason replay cannot overwrite newer state")
                if existing["status"] != "APPLIED":
                    replay_state = state.load_state(cur, pid)
                    if replay_state is None or replay_state["revision"] != existing["base_revision"]:
                        raise state.ProgressionStateError("pending offseason has a stale authoritative revision")
                    if existing["source_fingerprint"] != source_fingerprint:
                        raise state.ProgressionStateError("pending offseason has a different source")
                events.append(existing)
                continue
            current = state.load_state(cur, pid)
            if current is None:
                raise state.ProgressionStateError("explicit neutral bootstrap required")
            if current["application_season"] != earned_season:
                raise state.ProgressionStateError("offseason earned season must match authoritative state")
            target = row.get("target_ratings")
            if not target:
                raise state.ProgressionStateError(f"player {pid}: authoritative annual target missing")
            prior = state.load_events(cur, pid, earned_season)
            latest = max(prior, key=lambda e: e["base_revision"], default=None)
            carry = latest["metadata"].get("fractions", {}) if latest else {}
            start = annual_start_ratings(cur, pid, earned_season)
            reconciled = reconcile_annual(start, current["base_ratings"], target,
                                         state.load_annual_applied(cur, pid, earned_season), carry)
            band = copy.deepcopy(row.get("target_band", current["band"]))
            desired = policy.summarize_band(band, config)
            pgid = (pgid_by_player or {}).get(pid, row.get("pgid"))
            if pgid is None:
                raise state.ProgressionStateError(f"player {pid}: save pgid missing")
            metadata = {"week": week, "application_week": 0, "pgid": pgid, "position": row.get("position"),
                        "form": 0.0, "fractions": reconciled["fractions"], "annual_start": start,
                        "annual_entitlement": reconciled["annual_entitlement"],
                        "annual_target": target, "decisions": [], "desired_label": desired,
                        "actual_dev_write": None}
            metadata["report_context"] = state.capture_report_context(
                cur, current, position=row.get("position"), actual_dev_before=row.get("actual_dev_before"))
            # Full calibrated results remain private receipt metadata. The root
            # coordinator persists legacy annual/C2 state only after verified ack.
            # Pure reconciliation callers may provide minimal rows instead.
            if "checkpoint" in row or "durability" in row or "mileage" in row:
                metadata["annual_result"] = copy.deepcopy(row)
            events.append(state.prepare_event(
                cur, player_id=pid, earned_season=earned_season, application_season=application_season,
                kind="OFFSEASON", ordinal="final", rules_version=rules_version,
                source_fingerprint=source_fingerprint, base_revision=current["revision"],
                candidate_base=reconciled["candidate_base"], candidate_band=band, overlay={},
                annual_deltas=reconciled["annual_deltas"], evidence_ids=(),
                evidence_purpose="offseason-reconciliation", metadata=metadata))
    return events
