"""Authoritative permanent progression state; proposal preparation never awards growth.

The caller owns transactions and real-save verification. acknowledge_batch accepts an
explicit verified receipt and uses a savepoint so one stale player cannot partially
acknowledge a batch. Native observed ratings must not be bootstrapped unless their
form layer is explicitly known neutral. No function commits a connection.
"""
from __future__ import annotations

import hashlib
import json
import math
import uuid
from contextlib import contextmanager
from decimal import Decimal
from typing import Any, Mapping


class ProgressionStateError(ValueError):
    """A proposal or receipt cannot safely change authoritative state."""


@contextmanager
def _savepoint(cur):
    # Explicit SQL starts an implicit outer transaction when idle; releasing the
    # savepoint never commits that outer transaction (unlike connection.transaction).
    name = "progression_" + uuid.uuid4().hex
    cur.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        cur.execute(f"ROLLBACK TO SAVEPOINT {name}")
        cur.execute(f"RELEASE SAVEPOINT {name}")
        raise
    else:
        cur.execute(f"RELEASE SAVEPOINT {name}")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _text(value, name):
    if value is None or not str(value).strip():
        raise ProgressionStateError(f"{name} is required")
    return str(value)


def _numbers(values, name):
    if not isinstance(values, Mapping):
        raise ProgressionStateError(f"{name} must be a mapping")
    out = {}
    for key, value in values.items():
        if not isinstance(key, str) or not key or isinstance(value, bool):
            raise ProgressionStateError(f"invalid {name} value")
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ProgressionStateError(f"invalid {name} value") from None
        if not math.isfinite(number):
            raise ProgressionStateError(f"non-finite {name} value")
        out[key] = number
    return out


def _ratings(values):
    out = _numbers(values, "ratings")
    if not out or any(v != int(v) or not 0 <= v <= 99 for v in out.values()):
        raise ProgressionStateError("ratings must be nonempty integers in 0..99")
    return {k: int(v) for k, v in out.items()}


def event_id(player_id, earned_season, kind, ordinal, rules_version):
    """Stable identity excludes mutable evidence and source hashes; changed retry bodies refuse."""
    identity = [int(player_id), int(earned_season), _text(kind, "kind"),
                _text(ordinal, "ordinal"), _text(rules_version, "rules_version")]
    return hashlib.sha256(_canonical(identity).encode()).hexdigest()


def annual_remainder(entitlement, applied):
    """Per-rating cumulative entitlement less verified applied totals (signed)."""
    entitlement, applied = _numbers(entitlement, "entitlement"), _numbers(applied, "applied")
    return {k: float(Decimal(str(entitlement.get(k, 0))) - Decimal(str(applied.get(k, 0))))
            for k in sorted(entitlement.keys() | applied.keys())}


def quantize_entitlement(entitlement, applied):
    """Whole rating changes plus fractional carry; truncation never spends an unearned point."""
    remaining = annual_remainder(entitlement, applied)
    deltas = {k: math.trunc(v) for k, v in remaining.items()}
    fractions = {k: float(Decimal(str(v)) - Decimal(deltas[k])) for k, v in remaining.items()}
    return deltas, fractions


def effective_ratings(base, overlay):
    """Compose form onto permanent base without mutating either layer."""
    base, overlay = _ratings(base), _numbers(overlay, "overlay")
    if any(k not in base or v != int(v) for k, v in overlay.items()):
        raise ProgressionStateError("overlay must use existing ratings and integer deltas")
    return {k: max(0, min(99, v + int(overlay.get(k, 0)))) for k, v in base.items()}


def _one(cur):
    row = cur.fetchone()
    if row is None:
        return None
    if isinstance(row, Mapping):
        return dict(row)
    return dict(zip([getattr(d, "name", d[0]) for d in cur.description], row))


def load_state(cur, player_id, for_update=False):
    cur.execute("SELECT player_id, application_season, revision, base_ratings, band, source_fingerprint "
                "FROM progression_player_state WHERE player_id = %s" + (" FOR UPDATE" if for_update else ""),
                (player_id,))
    return _one(cur)


def load_annual_applied(cur, player_id, earned_season):
    cur.execute("SELECT applied FROM progression_annual_accounting WHERE player_id=%s AND earned_season=%s",
                (player_id, earned_season))
    row = _one(cur)
    return dict(row["applied"]) if row else {}


def load_consumed_evidence(cur, player_id, earned_season, purpose):
    cur.execute("SELECT evidence_id FROM progression_evidence_consumption "
                "WHERE player_id=%s AND earned_season=%s AND purpose=%s",
                (player_id, earned_season, purpose))
    return {str(row["evidence_id"] if isinstance(row, Mapping) else row[0]) for row in cur.fetchall()}



def load_bases(cur, application_season):
    """Current authoritative states visible through the application year."""
    cur.execute("SELECT player_id,base_ratings FROM progression_player_state WHERE application_season<=%s",
                (application_season,))
    return {row["player_id"] if isinstance(row, Mapping) else row[0]:
            row["base_ratings"] if isinstance(row, Mapping) else row[1] for row in cur.fetchall()}


def load_applied_overlays(cur, application_season):
    """Overlay paired with each current base revision; bootstrap states are neutral."""
    cur.execute("SELECT s.player_id,COALESCE(e.overlay,'{}'::jsonb) AS overlay FROM progression_player_state s "
                "LEFT JOIN progression_events e ON e.player_id=s.player_id AND e.base_revision=s.revision-1 AND e.status='APPLIED' "
                "WHERE s.application_season<=%s",
                (application_season,))
    return {row["player_id"] if isinstance(row, Mapping) else row[0]:
            row["overlay"] if isinstance(row, Mapping) else row[1] for row in cur.fetchall()}


def load_events(cur, player_id, earned_season, status="APPLIED"):
    if status not in ("PREPARED", "APPLIED", None):
        raise ProgressionStateError("unknown event status")
    sql = "SELECT * FROM progression_events WHERE player_id=%s AND earned_season=%s"
    params = [player_id, earned_season]
    if status is not None:
        sql += " AND status=%s"
        params.append(status)
    cur.execute(sql + " ORDER BY base_revision,event_id", params)
    columns = [getattr(d, "name", d[0]) for d in cur.description]
    return [dict(row) if isinstance(row, Mapping) else dict(zip(columns, row)) for row in cur.fetchall()]

def bootstrap_state(cur, player_id, application_season, base_ratings, band, source_fingerprint,
                    *, confidence=50, form_neutral=False, newcomer=False):
    """Explicit neutral-save initialization, never automatic adoption of an arbitrary ingest.

    `newcomer=True` is the one exception to the confidence-50 gate: a player this load created has no Mu
    state to adopt and no neutrality proof to demand, so his save ConfidenceRating (Madden's own) is
    neither checked nor stored. The gate exists so an arbitrary played save is never adopted as neutral.
    """
    if (confidence != 50 and not newcomer) or not form_neutral:
        raise ProgressionStateError("bootstrap requires confidence 50 and explicitly neutral form")
    ratings = _ratings(base_ratings)
    if not isinstance(band, Mapping) or not band:
        raise ProgressionStateError("a known development band is required")
    source = _text(source_fingerprint, "source_fingerprint")
    with _savepoint(cur):
        cur.execute("INSERT INTO progression_player_state "
                    "(player_id,application_season,base_ratings,band,source_fingerprint) "
                    "VALUES (%s,%s,%s::jsonb,%s::jsonb,%s) ON CONFLICT (player_id) DO NOTHING",
                    (player_id, application_season, _canonical(ratings), _canonical(dict(band)), source))
        state = load_state(cur, player_id, for_update=True)
        if (state["base_ratings"] != ratings or state["band"] != dict(band)
                or state["application_season"] != application_season or state["source_fingerprint"] != source):
            raise ProgressionStateError("player already has a different authoritative state")
        return state


def _load_event(cur, identity, for_update=False):
    cur.execute("SELECT * FROM progression_events WHERE event_id=%s" + (" FOR UPDATE" if for_update else ""),
                (identity,))
    return _one(cur)


def _proposal_metadata(metadata):
    """Receipt-only reporting enrichment is not a change to immutable proposal input."""
    out = dict(metadata)
    out.pop("report_public", None)
    if isinstance(out.get("report_context"), Mapping):
        out["report_context"] = dict(out["report_context"])
        out["report_context"].pop("actual_dev_after", None)
    return out


def prepare_event(cur, *, player_id, earned_season, application_season, kind, ordinal, rules_version,
                  source_fingerprint, base_revision, candidate_base, candidate_band, overlay=None,
                  evidence_ids=(), evidence_purpose="development", annual_deltas=None, metadata=None):
    """Store a reproducible proposal. No base state, evidence or annual spend changes here.

    Source is the current observed save, which can differ from the prior applied
    output after Advance Week. The base revision protects the permanent layer.
    """
    identity = event_id(player_id, earned_season, kind, ordinal, rules_version)
    candidate = _ratings(candidate_base)
    if not isinstance(candidate_band, Mapping) or not candidate_band:
        raise ProgressionStateError("candidate_band is required")
    form = {k: int(v) for k, v in _numbers(overlay or {}, "overlay").items()
            if v == int(v)}
    if len(form) != len(overlay or {}):
        raise ProgressionStateError("overlay deltas must be integers")
    effective_ratings(candidate, form)
    proposal = dict(event_id=identity, player_id=int(player_id), earned_season=int(earned_season),
                    application_season=int(application_season), kind=_text(kind, "kind"),
                    ordinal=_text(ordinal, "ordinal"), rules_version=_text(rules_version, "rules_version"),
                    source_fingerprint=_text(source_fingerprint, "source_fingerprint"),
                    base_revision=int(base_revision), candidate_base=candidate, candidate_band=dict(candidate_band),
                    overlay=form, annual_deltas=_numbers(annual_deltas or {}, "annual_deltas"),
                    evidence_ids=sorted({_text(v, "evidence_id") for v in evidence_ids}),
                    evidence_purpose=_text(evidence_purpose, "evidence_purpose"), metadata=dict(metadata or {}))
    # Serialization also rejects non-finite band/metadata values.
    _canonical(proposal)
    with _savepoint(cur):
        state = load_state(cur, player_id, for_update=True)
        if state is None:
            raise ProgressionStateError("authoritative state must be bootstrapped explicitly")
        old = _load_event(cur, identity)
        if old:
            if any((_proposal_metadata(old[k]) != _proposal_metadata(v)) if k == "metadata"
                   else old[k] != v for k, v in proposal.items()):
                raise ProgressionStateError("event identity already has a different proposal")
            return old
        if state["revision"] != base_revision:
            raise ProgressionStateError("stale authoritative base revision")
        if application_season < state["application_season"]:
            raise ProgressionStateError("application season cannot move backwards")
        if candidate.keys() != state["base_ratings"].keys():
            raise ProgressionStateError("candidate must preserve the authoritative rating fields")
        consumed = load_consumed_evidence(cur, player_id, earned_season, evidence_purpose)
        if consumed.intersection(proposal["evidence_ids"]):
            raise ProgressionStateError("evidence already awarded for this purpose")
        columns = list(proposal)
        json_columns = {"candidate_base", "candidate_band", "overlay", "annual_deltas", "evidence_ids", "metadata"}
        placeholders = ["%s::jsonb" if k in json_columns else "%s" for k in columns]
        values = [_canonical(proposal[k]) if k in json_columns else proposal[k] for k in columns]
        cur.execute(f"INSERT INTO progression_events ({','.join(columns)}) VALUES ({','.join(placeholders)})", values)
        return _load_event(cur, identity)


def acknowledge_batch(cur, event_ids, *, source_fingerprint, output_fingerprint, edits_hash, verified=False):
    """Accept an explicitly verified whole-save receipt; all events succeed or none do.

    The caller must verify actual saved output and bind edits_hash to the document
    used. This API is not a verifier and never starts the live league.
    """
    if verified is not True:
        raise ProgressionStateError("an explicit verified save receipt is required")
    source = _text(source_fingerprint, "source_fingerprint")
    output = _text(output_fingerprint, "output_fingerprint")
    edits = _text(edits_hash, "edits_hash")
    identities = list(dict.fromkeys(event_ids))
    if not identities:
        return []
    with _savepoint(cur):
        events = [_load_event(cur, identity, for_update=True) for identity in sorted(identities)]
        if any(e is None for e in events):
            raise ProgressionStateError("unknown event in receipt")
        players = [e["player_id"] for e in events]
        if len(players) != len(set(players)):
            raise ProgressionStateError("a batch must contain at most one event per player")
        states = {pid: load_state(cur, pid, for_update=True) for pid in sorted(players)}
        for event in events:
            if event["source_fingerprint"] != source:
                raise ProgressionStateError("receipt source differs from prepared source")
            if event["status"] == "APPLIED":
                if event["output_fingerprint"] != output or event["edits_hash"] != edits:
                    raise ProgressionStateError("applied event has a different receipt")
                continue
            state = states[event["player_id"]]
            if state is None or state["revision"] != event["base_revision"]:
                raise ProgressionStateError("stale authoritative base revision")
            consumed = load_consumed_evidence(cur, event["player_id"], event["earned_season"],
                                              event["evidence_purpose"])
            if consumed.intersection(event["evidence_ids"]):
                raise ProgressionStateError("evidence already awarded for this purpose")
        for event in events:
            if event["status"] == "APPLIED":
                continue
            pid, season = event["player_id"], event["earned_season"]
            cur.execute("UPDATE progression_player_state SET application_season=%s, revision=revision+1, "
                        "base_ratings=%s::jsonb, band=%s::jsonb, source_fingerprint=%s, updated_at=NOW() "
                        "WHERE player_id=%s AND revision=%s",
                        (event["application_season"], _canonical(event["candidate_base"]),
                         _canonical(event["candidate_band"]), output, pid, event["base_revision"]))
            if cur.rowcount != 1:
                raise ProgressionStateError("authoritative base compare-and-set failed")
            totals = load_annual_applied(cur, pid, season)
            for rating, value in event["annual_deltas"].items():
                totals[rating] = float(Decimal(str(totals.get(rating, 0))) + Decimal(str(value)))
            cur.execute("INSERT INTO progression_annual_accounting (player_id,earned_season,applied) "
                        "VALUES (%s,%s,%s::jsonb) ON CONFLICT (player_id,earned_season) "
                        "DO UPDATE SET applied=EXCLUDED.applied", (pid, season, _canonical(totals)))
            cur.executemany("INSERT INTO progression_evidence_consumption "
                            "(player_id,earned_season,purpose,evidence_id,event_id) VALUES (%s,%s,%s,%s,%s)",
                            [(pid, season, event["evidence_purpose"], evidence, event["event_id"])
                             for evidence in event["evidence_ids"]])
            cur.execute("UPDATE progression_events SET status='APPLIED',output_fingerprint=%s,edits_hash=%s, "
                        "applied_at=NOW() WHERE event_id=%s", (output, edits, event["event_id"]))
        return [_load_event(cur, identity) for identity in identities]


def capture_report_context(cur, current, *, position=None, actual_dev_before=None):
    """Immutable pre-event audit facts; absence is unknown, never reconstructed from outcomes."""
    cur.execute("SELECT overlay, metadata->'form' AS form FROM progression_events "
                "WHERE player_id=%s AND base_revision=%s AND status='APPLIED'",
                (current["player_id"], current["revision"] - 1))
    previous = _one(cur)
    return {"before_base": dict(current["base_ratings"]),
            "before_band": json.loads(_canonical(current["band"])),
            "before_overlay": dict(previous["overlay"]) if previous else {},
            "before_form": previous["form"] if previous else 0.0,
            "position": position, "actual_dev_before": actual_dev_before}
