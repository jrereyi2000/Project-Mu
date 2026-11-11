"""Receipt-driven development checkpoints. U13 owns actual save replacement.

Preparation writes proposals only. acknowledge_pass verifies a reader snapshot against
all player/record edits before accepting state; the caller owns the DB transaction.
No function starts Madden, replaces a live save or commits the connection.
"""
from __future__ import annotations
import copy
import hashlib
import json
import math
from collections import defaultdict
from engines import progression_state as state
from engines import progression_evidence as evidence
from engines import progression_policy as policy
from engines import progression_save as psave
from engines import form_engine as form
from engines import ovr_weights as ow
from engines import personality_development as pdev

RULES_VERSION = "progression-2026-10-31-v1"


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def normalize_save_ratings(ratings):
    """Madden's saved ratings brought into the progression model's range.

    Madden stores 100 for a handful of legacy ratings (CAREER-MUBASE: Jerry Rice's catching and three route-running
    ratings are 100), while the engine's model is 0..99 and `progression_state._ratings` refuses anything outside
    it. Normalise at ingestion so the engine invariant stays strict; non-integers are left for the guard to reject.
    """
    return {k: (min(v, 99) if isinstance(v, int) and v > 99 else v) for k, v in (ratings or {}).items()}


def _prior(cur, player_id, season):
    events = state.load_events(cur, player_id, season, status=None)
    applied = [e for e in events if e["status"] == "APPLIED"]
    return events, applied


def _used(applied, kind):
    return {gid for event in applied for decision in event["metadata"].get("decisions", [])
            if decision["kind"] == kind for gid in decision["evidence_ids"]}


def applied_development_effects(applied):
    """Attributed personality effects of APPLIED receipts only (PREPARED proposals never spend)."""
    by_rating, spend = defaultdict(float), 0.0
    for event in applied:
        block = (event.get("metadata") or {}).get(pdev.POLICY_SCHEMA)
        if not block or block.get("mode") != "enabled":
            continue
        for rating, value in (block.get("attributed_effect") or {}).items():
            by_rating[rating] += float(value)
        spend += float(block.get("annual_abs_spend") or 0.0)
    return {"by_rating": {k: round(v, 10) for k, v in sorted(by_rating.items())}, "abs_spend": round(spend, 10)}


def _exact_ovr(doc, save_position):
    arches = [a for a in doc["slots"][save_position] if a and a in doc["archetypes"]]
    return lambda ratings: max(ow.predict_grade(doc, a, ratings, exact=True) for a in arches)


def _development_effect(*, player_id, base, decisions, player_games, applied, annual_start, position,
                        save_position, doc, context, config):
    """Personality effect on this event's eligible fractional learning, capped before quantization.

    Returns (metadata block, effect to add to requested). Shadow adds nothing. Mentor effects stay
    disabled: progression's mentor term is an annual OVR scalar with no isolated rating component.
    """
    reasons = ["MENTOR_COMPONENT_NOT_ISOLATED"]
    if context is None:
        reasons.append("NO_CONTEXT")
        context = {}
    elif context.get("player_id") not in (None, player_id):
        raise state.ProgressionStateError("development context belongs to another player")
    weeks = {g["game_id"]: g.get("week") for g in player_games}
    from engines.personality_development_inputs import exposure_by_coach
    neutral, requested, raw, capped, refs = defaultdict(float), defaultdict(float), {}, {}, {}
    teaching = personality = False
    for decision in decisions:
        result = decision["result"]
        for rating, amount in result["skill_deltas"].items():
            neutral[rating] += amount
        if decision["kind"] not in pdev.LEARNING_KINDS:
            continue
        exposure = exposure_by_coach(context, [weeks[g] for g in decision["evidence_ids"]
                                               if weeks.get(g) is not None])
        coaches = [{"view": view, "exposure": exposure.get(key, 0.0), "persona_id": key}
                   for key, view in sorted((context.get("coach_views") or {}).items())]
        out = pdev.evaluate_learning_effect(
            neutral_result=result, player_view=context.get("player_view"), coach_views=coaches,
            mentor_view=None, context={"checkpoint_kind": decision["kind"], "is_qb": position == "QB",
                                       "ratings": base}, config=config)
        attribution = out["attribution"]
        effect = out["skill_deltas"] or attribution.get("shadow_skill_deltas") or {}
        for rating, amount in effect.items():
            requested[rating] += amount
        for rating, channels in attribution.get("raw_channels", {}).items():
            raw.setdefault(decision["kind"], {})[rating] = channels
            teaching |= bool(channels.get("teaching"))
            personality |= any(channels.get(c) for c in pdev.PLAYER_CHANNELS)
        capped[decision["kind"]] = attribution.get("capped_channels", {})
        for ref in attribution.get("profile_refs", []):
            refs[(ref["scope"], str(ref["persona_id"]))] = ref
        reasons += attribution.get("reasons", [])
    neutral_target = {k: v + neutral.get(k, 0.0) for k, v in base.items()}
    proposed = {k: neutral_target[k] + requested.get(k, 0.0) for k in base}
    prior = applied_development_effects(applied)
    # Shadow compares against the same caps when they are configured; shadow never changes a target.
    caps_set = all(getattr(config, f) > 0 for f in pdev.CAP_FIELDS)
    if config.mode == "enabled" or caps_set:
        limit_config = pdev.DevelopmentConfig(**{**config.as_dict(), "mode": "enabled"})
        limited = pdev.limit_annual_effect(annual_start=annual_start, neutral_target=neutral_target,
                                           proposed_target=proposed, applied_effects=prior,
                                           config=limit_config, ovr_at=_exact_ovr(doc, save_position))
    else:
        uncapped = {k: round(v, 10) for k, v in requested.items() if round(v, 10)}
        limited = {"target": proposed, "annual_effects": uncapped, "reasons": ["SHADOW_CAPS_NOT_CONFIGURED"]}
    effects = limited["annual_effects"]
    enabled = config.mode == "enabled"
    public = []
    if enabled and effects:
        public = sorted(({"PERSONALITY"} if personality else set()) | ({"COACHING"} if teaching else set()))
    block = {"mode": config.mode, "policy_version": config.version, "config_hash": config.config_hash(),
             "context_hash": context.get("context_hash"),
             "profile_refs": [refs[k] for k in sorted(refs)],
             "evidence_ids": sorted({str(g) for d in decisions if d["kind"] in pdev.LEARNING_KINDS
                                     for g in d["evidence_ids"]}),
             "neutral_skill_deltas": {k: round(v, 10) for k, v in sorted(neutral.items())},
             "raw_channels": raw, "capped_channels": capped,
             "requested_effect": {k: round(v, 10) for k, v in sorted(requested.items())},
             "attributed_effect": effects if enabled else {},
             "shadow_effect": {} if enabled else effects,
             "annual_abs_spend": round(sum(abs(v) for v in effects.values()), 10) if enabled else 0.0,
             "prior_applied": prior,
             "neutral_target": {k: round(v, 10) for k, v in sorted(neutral_target.items())},
             "effect_target": {k: round(v, 10) for k, v in sorted(limited["target"].items())},
             "cap_reasons": limited["reasons"], "public_reasons": public,
             "mentor": {"status": "disabled", "reason": "MENTOR_COMPONENT_NOT_ISOLATED"},
             "reasons": sorted(set(reasons))}
    return block, (effects if enabled else {})


def prepare_player(cur, *, player_id, pgid, earned_season, application_season,
                   as_of_week, source_fingerprint, player_games, team_games,
                   position, age=None, season_closed=False, playoff_run_closed=False,
                   form_value=0.0, supported_skills=(), notes=(), actual_dev_before=None, config=None, doc=None,
                   development_context=None, development_config=None):
    """Prepare one composite event/player. Frozen evidence cutoffs make retries stable.

    Ordinary checkpoint evidence and breakout review evidence are accounted separately.
    A finished postseason run is an independent review route. No actual dev-field write.
    development_config (personality_development.DevelopmentConfig, default None = off) and a
    frozen development_context (personality_development_inputs.load_development_context) add a
    capped personality effect to eligible learning before quantization; off changes nothing.
    """
    if development_config is not None and not isinstance(development_config, pdev.DevelopmentConfig):
        raise ValueError("development_config must be a DevelopmentConfig")
    if not math.isfinite(float(form_value)) or abs(float(form_value)) > form.FORM_MAX:
        raise ValueError("form must be finite and within the configured +/-3 OVR limit")
    current = state.load_state(cur, player_id)
    if current is None:
        raise state.ProgressionStateError(f"player {player_id}: explicit neutral bootstrap required")
    if application_season != current["application_season"]:
        raise state.ProgressionStateError("weekly progression requires the authoritative season; reconcile offseason first")
    events, applied = _prior(cur, player_id, earned_season)
    ordinal = f"week:{as_of_week}:closed:{int(season_closed)}:playoffs:{int(playoff_run_closed)}"
    identity = state.event_id(player_id, earned_season, "CHECKPOINT", ordinal, RULES_VERSION)
    previous = next((e for e in events if e["event_id"] == identity), None)
    if previous is not None:
        expected_revision = previous["base_revision"] + (previous["status"] == "APPLIED")
        if current["revision"] != expected_revision:
            raise state.ProgressionStateError("historical checkpoint cannot overwrite a newer authoritative revision")
        if previous["status"] != "APPLIED" and previous["source_fingerprint"] != source_fingerprint:
            raise state.ProgressionStateError("pending checkpoint has a different input save")
        return previous
    base = current["base_ratings"]
    band = copy.deepcopy(current["band"])
    doc = doc or ow.load_weights()
    from pipeline.madden_player_edit import TO_MADDEN_POSITION
    save_position = TO_MADDEN_POSITION.get(position, position)
    # The fitted grades can overshoot the game's 99 ceiling at the very top (Jerry Rice 102,
    # Elway 101 on the 2026 roster), and the policy rejects an out-of-range OVR — which failed a
    # whole PLAYOFF_WEEK plan (2026-11-10, madden_tune_p2g). Clamp as progression_report does.
    underlying_ovr = min(99, max(0, ow.predict_ovr(doc, save_position, base)[0]))
    checkpoints = evidence.due_checkpoints(
        team_games, season=earned_season, as_of_week=as_of_week,
        accounted_game_ids={kind: _used(applied, kind) for kind in ("MENTAL", "TECHNICAL")},
        season_closed=season_closed)
    decisions, requested = [], defaultdict(float)
    for checkpoint in checkpoints:
        ids = set(checkpoint["evidence_ids"])
        lines = [g for g in player_games if g["game_id"] in ids]
        observed = evidence.build_evidence(lines, season=earned_season, as_of_week=as_of_week)
        result = policy.evaluate_development(
            player_id=player_id, earned_season=earned_season, event_id=checkpoint["event_id"],
            rules_version=RULES_VERSION, ratings=base, band=band, evidence=observed,
            underlying_ovr=underlying_ovr, checkpoint_kind=checkpoint["group"], age=age,
            supported_skills=observed.get("supported_skills", ()), config=config)
        for rating, amount in result["skill_deltas"].items():
            requested[rating] += amount
        decisions.append({"kind": checkpoint["group"], "evidence_ids": sorted(ids),
                          "evidence": observed, "result": result})
    review = evidence.build_evidence(
        player_games, season=earned_season, as_of_week=as_of_week, team_games=team_games,
        playoff_run_closed=playoff_run_closed, accounted_evidence_ids=_used(applied, "REVIEW"))
    if review["eligible"]:
        result = policy.evaluate_development(
            player_id=player_id, earned_season=earned_season,
            event_id=state.event_id(player_id, earned_season, "REVIEW", review["window_id"], RULES_VERSION),
            rules_version=RULES_VERSION, ratings=base, band=band, evidence=review,
            underlying_ovr=underlying_ovr, checkpoint_kind="REVIEW", age=age,
            supported_skills=review.get("supported_skills", ()), config=config)
        band = result["target_band"]
        for rating, amount in result["skill_deltas"].items():
            requested[rating] += amount
        decisions.append({"kind": "REVIEW", "evidence_ids": review.get("fresh_evidence_ids", review["evidence_ids"]),
                          "evidence": review, "result": result})
    annual_start = applied[0]["metadata"].get("annual_start", base) if applied else base
    personality = None
    if development_config is not None and development_config.mode != "off":
        personality, effect = _development_effect(
            player_id=player_id, base=base, decisions=decisions, player_games=player_games, applied=applied,
            annual_start=annual_start, position=position, save_position=save_position, doc=doc,
            context=development_context, config=development_config)
        for rating, amount in effect.items():
            requested[rating] += amount
    # Fractional learning is retained only by applied events, never by prepared plans.
    latest = max(applied, key=lambda e: e["base_revision"], default=None)
    fractions = latest["metadata"].get("fractions", {}) if latest else {}
    spent = state.load_annual_applied(cur, player_id, earned_season)
    entitlement = {k: spent.get(k, 0) + fractions.get(k, 0) + requested.get(k, 0)
                   for k in set(spent) | set(fractions) | set(requested)}
    integer, carry = state.quantize_entitlement(entitlement, spent)
    candidate = {k: max(0, min(99, v + integer.get(k, 0))) for k, v in base.items()}
    actual = {k: candidate[k] - v for k, v in base.items() if candidate[k] != v}
    for rating, delta in integer.items():
        if actual.get(rating, 0) != delta:
            carry[rating] = 0.0
    overlay = form.overlay(candidate, save_position, None, form_value, doc)
    metadata = {"week": as_of_week, "application_week": as_of_week, "pgid": pgid, "position": position,
                "decisions": decisions, "fractions": carry, "form": float(form_value),
                "input_notes": list(notes),
                "annual_start": annual_start,
                "desired_label": policy.summarize_band(band, config), "actual_dev_write": None}
    if personality is not None:
        metadata[pdev.POLICY_SCHEMA] = personality
    metadata["report_context"] = state.capture_report_context(
        cur, current, position=position, actual_dev_before=actual_dev_before)
    consumed = sorted({str(gid) for d in decisions for gid in d["evidence_ids"]})
    return state.prepare_event(
        cur, player_id=player_id, earned_season=earned_season, application_season=application_season,
        kind="CHECKPOINT", ordinal=ordinal, rules_version=RULES_VERSION,
        source_fingerprint=source_fingerprint, base_revision=current["revision"],
        candidate_base=candidate, candidate_band=band, overlay=overlay,
        evidence_ids=consumed, evidence_purpose=identity, annual_deltas=actual, metadata=metadata)


def document_for_events(events, snapshot, doc=None):
    """Use absolute new permanent base + temporary form, retaining the ability gate."""
    rows = [{"player_id": e["player_id"], "pgid": e["metadata"]["pgid"],
             "target_ratings": state.effective_ratings(e["candidate_base"], e["overlay"]),
             "behaviour_changes": e["metadata"].get("annual_result", {}).get("behaviour_changes")} for e in events]
    return psave.build_document(rows, snapshot, morale=50, doc=doc)


def prepare_pass(cur, snapshot, season, week, *, source_fingerprint,
                 player_inputs, forms=None, config=None, development_config=None):
    """player_inputs is cutoff-normalized evidence per internal player ID.

    Missing authoritative state is a hard failure; bootstrap is an explicit setup step.
    No partial batch is retained if one player cannot be prepared.
    """
    events = []
    with state._savepoint(cur):
        players = {p["pgid"]: p for p in snapshot.get("players", []) if p.get("pgid") is not None}
        for pid, inputs in sorted(player_inputs.items()):
            inputs = dict(inputs)
            inputs.setdefault("actual_dev_before", players.get(inputs.get("pgid"), {}).get("dev_trait"))
            events.append(prepare_player(cur, player_id=pid, earned_season=season,
                application_season=season, as_of_week=week, source_fingerprint=source_fingerprint,
                form_value=(forms or {}).get(pid, 0.0), config=config,
                development_config=development_config, **inputs))
        document = document_for_events(events, snapshot)
        pending = [event for event in events if event["status"] != "APPLIED"]
    return {"document": document, "event_ids": [e["event_id"] for e in pending],
            "source_fingerprint": source_fingerprint, "edits_hash": fingerprint(document)}


def verify_document(document, observed):
    """Read-back verification of exactly the progression-owned edit fields."""
    players = {p["pgid"]: p for p in observed.get("players", []) if p.get("pgid", 0) > 0}
    names = {"OverallRating": "overall", "PlayerType": "archetype", "ConfidenceRating": "confidence",
             "TraitDevelopment": "dev_trait"}
    for edit in document.get("players", []):
        p = players.get(edit["pgid"])
        if p is None:
            raise state.ProgressionStateError("verified snapshot lacks edited player")
        for field, value in edit["set"].items():
            if field in ("ExperiencePoints", "SkillPoints"):
                # Reader v4 omits these; save_writer's mandatory schema readback verifies them.
                continue
            if field.startswith("OverallGrade"):
                grades = p.get("archetype_grades") or []
                i = int(field[len("OverallGrade"):])
                found = grades[i] if i < len(grades) else None
            elif field in names:
                found = p.get(names[field])
            elif field.startswith("PT_"):
                found = (p.get("traits") or {}).get(field)
            elif field.endswith("Rating"):
                found = (p.get("ratings") or {}).get(field[:-6])
            else:
                raise state.ProgressionStateError(f"unsupported verification field {field}")
            if found != value:
                raise state.ProgressionStateError(f"readback mismatch pgid {edit['pgid']} field {field}")
    # Sliders/settings are verified by the save_writer record verifier; also check reader projection.
    ls = observed.get("league_settings") or {}
    for edit in document.get("records", []):
        if edit["table"] == "LeagueSetting":
            for field, value in edit["set"].items():
                keys = (("dev_trait_regression_field", "dev_trait_regression_enabled"),
                        ("breakout_scenarios_field", "breakout_scenarios_enabled"))
                enabled = next((ls.get(key) for fkey, key in keys if ls.get(fkey) == field), None)
                if enabled is None or bool(enabled) != bool(value):
                    raise state.ProgressionStateError("league setting readback mismatch")
        else:
            slider = next((v for v in (ls.get("sliders") or {}).values() if v
                           and v.get("table_id") == edit["tableId"] and v.get("row") == edit["row"]), None)
            if slider is None or any(slider.get("values", {}).get(k) != v for k, v in edit["set"].items()):
                raise state.ProgressionStateError("slider readback mismatch")


def acknowledge_pass(cur, prepared, observed, *, output_fingerprint, writer_verified=False):
    """U13 calls only after writer readback and durable output/replacement verification.

    Raw XP balances are absent from reader v4, so the writer verification receipt is
    mandatory in addition to our snapshot verification. Caller commits/reconciles crashes.
    """
    if not writer_verified or fingerprint(prepared["document"]) != prepared["edits_hash"]:
        raise state.ProgressionStateError("verified writer receipt and unchanged edits required")
    verify_document(prepared["document"], observed)
    with state._savepoint(cur):
        observed_players = {p["pgid"]: p for p in observed.get("players", [])}
        for identity in prepared["event_ids"]:
            event = state._load_event(cur, identity)
            if event is None:
                raise state.ProgressionStateError("unknown progression event")
            player = observed_players.get(event["metadata"]["pgid"])
            expected = state.effective_ratings(event["candidate_base"], event["overlay"])
            if player is None or any(player.get("ratings", {}).get(k) != v for k, v in expected.items()):
                raise state.ProgressionStateError("readback does not match prepared authoritative targets")
        events = state.acknowledge_batch(cur, prepared["event_ids"],
            source_fingerprint=prepared["source_fingerprint"], output_fingerprint=output_fingerprint,
            edits_hash=prepared["edits_hash"], verified=True)
        by_week = defaultdict(list)
        for event in events:
            md = event["metadata"]
            by_week[(event["application_season"], md["week"])].append(
                {"player_id": event["player_id"], "form": md["form"], "overlay": event["overlay"]})
        # Upsert affected players only, preserving other players in partial batches.
        for (season, week), rows in by_week.items():
            cur.executemany("INSERT INTO player_form (player_id,season,week,form,overlay) VALUES (%s,%s,%s,%s,%s::jsonb) "
                "ON CONFLICT (player_id,season,week) DO UPDATE SET form=EXCLUDED.form,overlay=EXCLUDED.overlay",
                [(r["player_id"], season, week, r["form"], json.dumps(r["overlay"])) for r in rows])
        # Preserve the existing annual writer summary and C2 career state only
        # after the corresponding save is verified. Checkpoint events remain separate.
        annual = [event for event in events if event["metadata"].get("annual_result")]
        if annual:
            from engines import progression
            for event in annual:
                row = dict(event["metadata"]["annual_result"])
                if "age_term" not in row:
                    continue
                base_start = event["metadata"]["annual_start"]
                candidate = event["candidate_base"]
                position = event["metadata"]["position"]
                doc = ow.load_weights()
                from pipeline.madden_player_edit import TO_MADDEN_POSITION
                position = TO_MADDEN_POSITION.get(position, position)
                row["old_ovr"] = ow.predict_ovr(doc, position, base_start)[0]
                row["new_ovr"] = ow.predict_ovr(doc, position, candidate)[0]
                row["delta_ovr"] = row["new_ovr"] - row["old_ovr"]
                row["ratings_diff"] = {k: candidate[k]-v for k,v in base_start.items() if candidate[k] != v}
                row["peak_mental"] = {k: max((row.get("peak_mental") or {}).get(k, 0), candidate.get(k, 0))
                                      for k in ("Awareness", "PlayRecognition") if k in candidate}
                row.update(event["candidate_band"])
                row["ceiling"] = max(event["candidate_band"]["group_ceilings"][k] for k in ("technical", "mental"))
                progression.store_progression(cur, [row], event["application_season"])
        # Publish a versioned whitelist projection only after verified application.
        # It lives in the same acknowledgement savepoint as the permanent award.
        from engines.progression_report import project_event
        report_weights = ow.load_weights()
        for event in events:
            md = event["metadata"]
            context = dict(md.get("report_context") or {})
            context["actual_dev_after"] = observed_players[md["pgid"]].get("dev_trait")
            md["report_context"] = context
            # The first receipt projection is immutable on retry, including unknown
            # facts from legacy events that did not capture a before context.
            if "report_public" not in md:
                md["report_public"] = project_event(event, audience="public", doc=report_weights)
                cur.execute("UPDATE progression_events SET metadata=%s::jsonb WHERE event_id=%s AND status='APPLIED'",
                            (json.dumps(md, sort_keys=True, allow_nan=False), event["event_id"]))
        return events


def explain_prepared(cur, prepared, *, audience="public"):
    """Read-only report projection; retain the original preparation envelope for application."""
    from engines.progression_report import project_event
    reports = []
    weights = ow.load_weights()
    for identity in prepared["event_ids"]:
        event = state._load_event(cur, identity)
        if event is None:
            raise state.ProgressionStateError("unknown progression event")
        reports.append(project_event(event, audience=audience, doc=weights))
    return reports


def _bootstrap_player(cur, pid, p, season, source_fingerprint, *, form_neutral, newcomer=False):
    from engines import progression
    ceiling, _ = progression.seed_player_ceiling(pid, p["overall"], p.get("age") or 25, p.get("dev_trait"))
    band = progression.seed_ceiling_band(pid, p["overall"], p.get("dev_trait"), ceiling, p.get("years_pro"))
    return state.bootstrap_state(cur, pid, season, normalize_save_ratings(p["ratings"]), band,
                                 source_fingerprint, confidence=p.get("confidence"), form_neutral=form_neutral,
                                 newcomer=newcomer)


def bootstrap_pass(cur, snapshot, season, *, source_fingerprint, form_neutral=False,
                   pgid_by_player=None):
    """Explicit setup from a known-neutral save. Raw ingests never call this."""
    ids = pgid_by_player if pgid_by_player is not None else psave.fetch_pgid_map(cur)
    players = {p["pgid"]: p for p in snapshot.get("players", []) if p.get("pgid", 0) > 0}
    out = []
    with state._savepoint(cur):
        for pid, pgid in sorted(ids.items()):
            p = players.get(pgid)
            if p is None or not p.get("ratings"):
                continue
            if state.load_state(cur, pid) is not None:
                continue
            out.append(_bootstrap_player(cur, pid, p, season, source_fingerprint, form_neutral=form_neutral))
    return out


def bootstrap_newcomers(cur, snapshot, season, *, source_fingerprint, pgid_by_player):
    """Seed state for explicit player ids that have none (PROGRESSION_PLAN §5.3a).

    A newcomer has no Mu history to adopt, so the setup pass's confidence-50 neutrality proof does not
    apply and his form starts neutral. `bootstrap_untracked` is the eligibility rule callers should use;
    this is the low-level id-driven form.
    """
    players = {p["pgid"]: p for p in snapshot.get("players", []) if p.get("pgid", 0) > 0}
    out = []
    with state._savepoint(cur):
        for pid, pgid in sorted((pgid_by_player or {}).items()):
            p = players.get(pgid)
            if p is None or not p.get("ratings"):
                continue
            if state.load_state(cur, pid) is not None:
                continue
            out.append(_bootstrap_player(cur, pid, p, season, source_fingerprint,
                                         form_neutral=True, newcomer=True))
    return out


def bootstrap_untracked(cur, snapshot, season, *, source_fingerprint):
    """Seed every player in `snapshot` that Mu has never tracked (JR, 2026-11-10).

    "Never tracked" means no `progression_player_state` row and no `progression_events` row. The rule is
    durable: a newcomer is healed at any later ingest too, not only on the load that created him, so a
    player stranded by an older load (or by an unrecognised stage, a missing rating or an exception) is
    picked up next time instead of failing every progression plan for the whole league. A player with
    event history but no state is deliberately left alone — `prepare_pass` keeps refusing him.
    Returns (seeded rows, pgids the snapshot has no ratings for).
    """
    players = {p["pgid"]: p for p in snapshot.get("players", []) if p.get("pgid", 0) > 0}
    if not players:
        return [], []
    cur.execute("""SELECT p.id, p.madden_pgid FROM players p
                   WHERE p.madden_pgid = ANY(%s)
                     AND NOT EXISTS (SELECT 1 FROM progression_player_state s WHERE s.player_id = p.id)
                     AND NOT EXISTS (SELECT 1 FROM progression_events e WHERE e.player_id = p.id)""",
                (list(players),))
    untracked = dict(cur.fetchall())
    no_ratings = sorted(pgid for pid, pgid in untracked.items() if not (players.get(pgid) or {}).get("ratings"))
    return bootstrap_newcomers(cur, snapshot, season, source_fingerprint=source_fingerprint,
                               pgid_by_player=untracked), no_ratings


def prepare_weekly_pass(cur, snapshot, season, week, *, source_fingerprint, config=None):
    """Public U13 adapter: explicit observation/fingerprint in, receipt-ready proposal out."""
    from engines.progression_inputs import fetch_inputs
    inputs = fetch_inputs(cur, season, week)
    saved_pgids = {p["pgid"] for p in snapshot.get("players", []) if p.get("pgid", 0) > 0}
    inputs = {pid: data for pid, data in inputs.items() if data.get("pgid") in saved_pgids}
    forms = form.weekly_form(cur, season, week)
    return prepare_pass(cur, snapshot, season, week, source_fingerprint=source_fingerprint,
                        player_inputs=inputs, forms=forms, config=config)


def prepare_offseason_pass(cur, snapshot, season, *, source_fingerprint, config=None):
    """Reconcile earned season S-1 against annual-start targets; form resets to zero."""
    from engines import progression
    from engines import progression_offseason as offseason
    saved_pgids = {p["pgid"] for p in snapshot.get("players", []) if p.get("pgid", 0) > 0}
    ids = {pid: pgid for pid, pgid in psave.fetch_pgid_map(cur).items() if pgid in saved_pgids}
    bases, bands = {}, {}
    for pid in ids:
        current = state.load_state(cur, pid)
        if current is not None:
            bases[pid] = offseason.annual_start_ratings(cur, pid, season - 1)
            bands[pid] = current["band"]
    if not bases:
        raise state.ProgressionStateError("explicit neutral bootstrap is required before offseason progression")
    result = progression.run_progression(cur.connection, season, commit=False, dry_run=True,
        traits=psave.snapshot_traits(snapshot, ids), baseline_ratings=bases, band_overrides=bands,
        log=lambda message: None)
    rows = [row for row in result["results"] if row["player_id"] in bases]
    observed_players = {p["pgid"]: p for p in snapshot.get("players", [])}
    for row in rows:
        row["actual_dev_before"] = observed_players.get(ids.get(row["player_id"]), {}).get("dev_trait")
        # Preserve lasting physical injury-band changes while keeping the reviewed
        # stored technical/mental bands supplied to annual calculation.
        if row.get("group_ceilings"):
            row["target_band"] = {"floor": row["floor"], "spread": row["spread"],
                                  "group_ceilings": row["group_ceilings"]}
    with state._savepoint(cur):
        events = offseason.prepare_offseason(cur, rows, earned_season=season-1,
            application_season=season, source_fingerprint=source_fingerprint,
            rules_version=RULES_VERSION, pgid_by_player=ids, config=config)
        document = document_for_events(events, snapshot)
    return {"document": document, "event_ids": [e["event_id"] for e in events if e["status"] != "APPLIED"],
            "source_fingerprint": source_fingerprint, "edits_hash": fingerprint(document)}
