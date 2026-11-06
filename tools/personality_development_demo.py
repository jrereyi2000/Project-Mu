"""Synthetic paired-control experiments for personality development and coaching (offline only).

PERSONALITY_DEVELOPMENT_COACHING_PLAN U2. No database, save or profile store: synthetic players,
synthetic scoped views and the real pure policies (engines.progression_policy,
engines.progression_state quantization, engines.progression_offseason reconciliation,
engines.personality_development, engines.personality_coaching).

    python -m tools.personality_development_demo --scenario all --seed 2026 --seasons 8 \\
        [--config PATH] [--sweep PATH] [--out DIR]

--config is JSON {"development": {...DevelopmentConfig}, "coaching": {...CoachingConfig}} (unknown
fields refused); missing config means off. --sweep is a JSON list of such configs. Writes
DIR/report.json (canonical, byte-stable for identical inputs) and DIR/README.md. Every
coefficient in a config is a candidate for JR's review (gate G3), never an approved preset.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path
from typing import Any

from engines import ovr_weights as ow
from engines import personality_coaching as pc
from engines import personality_development as pd
from engines import progression_offseason as offseason
from engines import progression_policy as policy
from engines import progression_state as state
from engines.progression import mentor_term
from engines.progression_report import RATINGS

SCENARIOS = ("learning", "teaching", "mentoring", "opportunity", "scheme", "reconciliation")
ARMS = ("neutral", "preparation", "teaching", "mentor", "opportunity", "combined")
RULES_VERSION = "personality-development-demo-v1"
GAMES = 17
MENTAL_AT = (4, 8, 12, 16)
TECHNICAL_AT = (9, 17)
DEFAULT_OUT = "verify_output/personality_development_demo"


class DemoError(ValueError):
    pass


# ------------------------------------------------------------------ inputs

def load_config(data: Any) -> dict[str, Any]:
    if data is None:
        return {"development": pd.OFF, "coaching": pc.OFF}
    if not isinstance(data, dict):
        raise DemoError("config must be a JSON object")
    unknown = sorted(set(data) - {"development", "coaching", "label"})
    if unknown:
        raise DemoError(f"unknown config sections: {unknown}")
    try:
        dev = pd.DevelopmentConfig.from_dict(data.get("development") or {})
        coach = pc.CoachingConfig.from_dict(data.get("coaching") or {})
    except (pd.DevelopmentPolicyError, pc.CoachingPolicyError, TypeError) as exc:
        raise DemoError(str(exc)) from None
    label = data.get("label", "")
    if not isinstance(label, str):
        raise DemoError("label must be text")
    return {"development": dev, "coaching": coach, "label": label}


def _read_json(path: str) -> Any:
    def refuse(value):
        raise DemoError(f"non-finite number {value} in {path}")
    try:
        return json.loads(Path(path).read_text(), parse_constant=refuse)
    except json.JSONDecodeError as exc:
        raise DemoError(f"{path}: {exc}") from None


def _view(traits=(), drive=5, persona_id=None):
    sliders = dict(temperament=5, sociability=5, confidence=5, openness=5, warmth=5, drive=drive)
    return {"status": "stored", "origin": "generated", "sliders": sliders, "traits": sorted(traits),
            "revision": 1, "persona_id": persona_id, "catalogue_version": "synthetic", "view_version": "views-v2"}


MISSING = {"status": "missing", "origin": None, "sliders": {}, "traits": [], "revision": None, "persona_id": None}


def cohort(seed: int) -> list[dict[str, Any]]:
    """Twelve synthetic players: young QBs, WRs and linebackers across ages and bases."""
    rng = random.Random(f"pdc-demo:{seed}")
    out = []
    specs = [("QB", 21), ("QB", 23), ("QB", 31), ("WR", 21), ("WR", 22), ("WR", 25),
             ("WR", 31), ("MLB", 21), ("MLB", 23), ("MLB", 26), ("WR", 23), ("QB", 25)]
    for i, (pos, age) in enumerate(specs, start=1):
        level = rng.randint(58, 76)
        ratings = {k: max(25, min(90, level + rng.randint(-12, 8))) for k in sorted(RATINGS)}
        traits = (["FILM_ROOM_RAT"] if i % 3 == 1 else ["PERFECTIONIST"] if i % 3 == 2 else ["COASTING"])
        out.append({"player_id": i, "position": pos, "age": age, "ratings": ratings,
                    "ceiling": min(95, level + rng.randint(6, 18)), "drive": rng.choice((3, 5, 7, 9)),
                    "traits": traits})
    # One deliberate close call: a veteran WR (7) leads the room and a rookie WR (4) trails him by a point
    # per rating, so the opportunity arm has something to decide
    # (two WR starters; WR 6 equals the veteran, the rookie trails both by a point per rating).
    vet, kid = out[6], out[3]
    vet["ratings"] = {k: min(95, v + 12) for k, v in vet["ratings"].items()}
    kid["ratings"] = {k: max(25, v - 1) for k, v in vet["ratings"].items()}
    out[5]["ratings"] = dict(vet["ratings"])  # WR 6 matches the veteran; two starter spots
    return out


def ovr_at(doc, position):
    arches = [a for a in doc["slots"][position] if a and a in doc["archetypes"]]

    def fn(ratings):
        return max(ow.predict_grade(doc, a, ratings, exact=True) for a in arches)
    return fn


# ------------------------------------------------------------------ one season

def _evidence(season, pid, games, appearances, kind, n):
    ids = [f"{season}:{pid}:{g}" for g in games]
    return {"window_id": f"{kind}:{season}:{pid}:{n}", "evidence_ids": ids, "eligible": False,
            "reliability": 0.8, "performance_signal": 0.4, "meaningful_appearances": appearances,
            "opportunities": appearances * 30, "supported_skills": sorted(SUPPORTED)}


SUPPORTED = ("Catching", "ShortRouteRunning", "MediumRouteRunning", "ThrowAccuracyShort",
             "ThrowAccuracyMid", "Tackle", "Pursuit", "ZoneCoverage")


def season_for(player, arm, season, config, *, starter=True, doc=None):
    """One synthetic season under arm; returns new integer ratings and an audit row."""
    dev = config["development"]
    fn = ovr_at(doc, player["position"])
    base = dict(player["ratings"])
    start = dict(base)
    band = {"group_ceilings": {"physical": 90, "technical": player["ceiling"], "mental": player["ceiling"]}}
    prep = arm in ("preparation", "combined")
    teach = arm in ("teaching", "combined")
    pview = _view(player["traits"], player["drive"], f"player-{player['player_id']}") if prep else MISSING
    coaches = ([{"view": _view(["TEACHER", "QB_WHISPERER"], persona_id="coach"), "exposure": 1.0,
                 "persona_id": "coach"}] if teach else [])
    applied = {"by_rating": {}, "abs_spend": 0.0}
    spent: dict[str, float] = {}
    carry: dict[str, float] = {}
    neutral_total: dict[str, float] = {}
    effect_total: dict[str, float] = {}
    capped = 0
    max_ovr_gap = 0.0
    played = list(range(1, GAMES + 1)) if starter else list(range(1, GAMES + 1, 4))
    checkpoints = [("MENTAL", g, n) for n, g in enumerate(MENTAL_AT)] + [("TECHNICAL", g, n) for n, g in enumerate(TECHNICAL_AT)]
    checkpoints.sort(key=lambda c: (c[1], c[0]))
    last = {"MENTAL": 0, "TECHNICAL": 0}
    for kind, upto, n in checkpoints:
        window = [g for g in played if last[kind] < g <= upto]
        last[kind] = upto
        if not window:
            continue
        evidence = _evidence(season, player["player_id"], window, len(window), kind, n)
        underlying = max(0.0, min(99.0, fn(base)))
        result = policy.evaluate_development(
            player_id=player["player_id"], earned_season=season, event_id=evidence["window_id"],
            rules_version=RULES_VERSION, ratings=base, underlying_ovr=underlying, band=band,
            evidence=evidence, checkpoint_kind=kind, age=player["age"], config=None)
        neutral = result["skill_deltas"]
        eff = pd.evaluate_learning_effect(neutral_result=result, player_view=pview, coach_views=coaches,
                                          mentor_view=None, context={"is_qb": player["position"] == "QB",
                                                                     "ratings": base}, config=dev)
        neutral_t = {k: base[k] + neutral.get(k, 0.0) for k in base}
        proposed_t = {k: neutral_t[k] + eff["skill_deltas"].get(k, 0.0) for k in base}
        limited = pd.limit_annual_effect(annual_start=start, neutral_target=neutral_t, proposed_target=proposed_t,
                                         applied_effects=applied, config=dev, ovr_at=fn)
        capped += bool(limited["capped"] and eff["skill_deltas"])
        for k, v in neutral.items():
            neutral_total[k] = neutral_total.get(k, 0.0) + v
        for k, v in limited["annual_effects"].items():
            effect_total[k] = effect_total.get(k, 0.0) + v
            applied["by_rating"][k] = applied["by_rating"].get(k, 0.0) + v
            applied["abs_spend"] += abs(v)
        requested = {k: neutral.get(k, 0.0) + limited["annual_effects"].get(k, 0.0)
                     for k in set(neutral) | set(limited["annual_effects"])}
        entitlement = {k: spent.get(k, 0) + carry.get(k, 0) + requested.get(k, 0)
                       for k in set(spent) | set(carry) | set(requested)}
        whole, carry = state.quantize_entitlement(entitlement, spent)
        for k, d in whole.items():
            if d:
                new = max(0, min(99, base[k] + d))
                spent[k] = spent.get(k, 0) + (new - base[k])
                base[k] = new
        paired = {k: start[k] + neutral_total.get(k, 0.0) for k in start}
        effect_path = {k: paired[k] + effect_total.get(k, 0.0) for k in start}
        max_ovr_gap = max(max_ovr_gap, abs(fn(effect_path) - fn(paired)))
    return base, {
        "neutral_learning": _round(sum(neutral_total.values())),
        "attributed_effect": _round(sum(effect_total.values())),
        "abs_spend": _round(applied["abs_spend"]),
        "max_per_rating_effect": _round(max((abs(v) for v in applied["by_rating"].values()), default=0.0)),
        "max_paired_ovr_gap": _round(max_ovr_gap),
        "capped_checkpoints": capped,
        "carry": _round(sum(carry.values())),
        "appearances": len(played)}


def _round(value, places=6):
    value = round(float(value), places)
    return 0.0 if value == 0 else value


# ------------------------------------------------------------------ arms and scenarios

def _starters(players, arm, config, coach_view):
    """Every arm ranks each position group by neutral OVR and starts the top half; only the opportunity
    arms add the real policy's close-call adjustments (so off/zero gives the neutral depth chart)."""
    starters = {}
    for pos in sorted({p["position"] for p in players}):
        group = [p for p in players if p["position"] == pos]
        fn = ovr_at(DOC_CACHE["doc"], pos)
        cands = [{"player_id": p["player_id"], "slot": pos, "neutral_score": fn(p["ratings"]),
                  "eligible": True, "out": False,
                  "cohort": "youth" if p["age"] <= 24 else "veteran" if p["age"] >= 30 else "other",
                  "cohort_weight": 1.0} for p in group]
        adj = {}
        if arm in ("opportunity", "combined"):
            adj = pc.opportunity_adjustments(candidates=cands, coach_view=coach_view,
                                             config=config["coaching"])["adjustments"].get(pos, {})
        ranked = sorted(cands, key=lambda c: (-(c["neutral_score"] + adj.get(c["player_id"], 0.0)), c["player_id"]))
        count = max(1, len(group) // 2)
        for i, c in enumerate(ranked):
            starters[c["player_id"]] = i < count
    return starters


DOC_CACHE: dict[str, Any] = {}


def run_arms(seed, seasons, start_season, config):
    doc = DOC_CACHE.setdefault("doc", ow.load_weights())
    youth_coach = _view(["YOUTH_DEVELOPER"], persona_id="coach")
    arms = {}
    for arm in ARMS:
        players = cohort(seed)
        years = []
        for year in range(seasons):
            season = start_season + year
            starters = _starters(players, arm, config, youth_coach)
            rows = []
            for p in players:
                # The mentor arm runs as neutral here: mentor effects are offline only (mentoring scenario).
                new, audit = season_for(p, arm if arm != "mentor" else "neutral", season, config,
                                        starter=starters[p["player_id"]], doc=doc)
                fn = ovr_at(doc, p["position"])
                audit.update(player_id=p["player_id"], ovr_before=_round(fn(p["ratings"])), ovr_after=_round(fn(new)))
                p["ratings"] = new
                p["age"] += 1
                rows.append(audit)
            years.append({"season": season, "mean_ovr": _round(sum(r["ovr_after"] for r in rows) / len(rows)),
                          "players": rows})
        arms[arm] = {"years": years, "final_ratings": {str(p["player_id"]): p["ratings"] for p in players}}
    return arms


def summarize(arms):
    neutral = arms["neutral"]["years"]
    out = {}
    for arm, data in arms.items():
        diffs = [_round(y["mean_ovr"] - n["mean_ovr"]) for y, n in zip(data["years"], neutral)]
        vec = 0
        for pid, ratings in data["final_ratings"].items():
            base = arms["neutral"]["final_ratings"][pid]
            vec += sum(abs(ratings[k] - base[k]) for k in ratings)
        flat = [r for y in data["years"] for r in y["players"]]
        out[arm] = {"mean_ovr_difference_by_season": diffs,
                    "final_rating_vector_abs_difference": vec,
                    "attributed_effect_total": _round(sum(r["attributed_effect"] for r in flat)),
                    "max_annual_abs_spend": _round(max(r["abs_spend"] for r in flat)),
                    "max_per_rating_effect": _round(max(r["max_per_rating_effect"] for r in flat)),
                    "max_paired_ovr_gap": _round(max(r["max_paired_ovr_gap"] for r in flat)),
                    "capped_checkpoints": sum(r["capped_checkpoints"] for r in flat),
                    "appearances": sum(r["appearances"] for r in flat)}
    final = {a: out[a]["mean_ovr_difference_by_season"][-1] if out[a]["mean_ovr_difference_by_season"] else 0.0
             for a in out}
    residual = _round(final["combined"] - final["preparation"] - final["teaching"] - final["opportunity"])
    return out, {"final_mean_ovr_difference": final, "combined_interaction_residual": residual,
                 "direct_arms": ["preparation", "teaching"], "indirect_arms": ["opportunity"],
                 "note": "Opportunity changes appearances, which changes neutral learning (indirect feedback). "
                         "The direct-learning caps do not bound it; activation of opportunities is reviewed separately."}


def mentoring_scenario(config):
    """Offline only: the integration keeps mentor effects disabled (component not isolated)."""
    rows = []
    for age in (21, 23, 24, 25, 26):
        component = mentor_term(age, True)
        neutral = {"Awareness": component / 2, "PlayRecognition": component / 2}
        cases = {}
        for label, view, exposure in (("mentor_trait", _view(["MENTOR"]), True),
                                      ("loner_trait", _view(["LONER"]), True),
                                      ("no_exposure", _view(["MENTOR"]), False),
                                      ("missing", None, True)):
            res = pd.mentor_adjustment(neutral_component=neutral, mentor_view=view,
                                       verified_exposure=exposure, config=config["development"])
            diffs = res.get("differences") or res.get("shadow_differences") or {}
            cases[label] = {"difference": _round(sum(diffs.values())), "reasons": res["reasons"]}
        rows.append({"age": age, "neutral_component_ovr_term": component, "cases": cases})
    return {"status": "OFFLINE_ONLY_MENTOR_DISABLED_IN_INTEGRATION", "rows": rows}


def scheme_scenario(config):
    choices = [{"choice_id": "west_coast", "legal": True, "neutral_score": 6.0, "continuity_cost": 0.0,
                "supported_tendency": False},
               {"choice_id": "spread", "legal": True, "neutral_score": 5.4, "continuity_cost": 1.0,
                "supported_tendency": True},
               {"choice_id": "air_raid", "legal": False, "neutral_score": 9.0, "continuity_cost": 0.0,
                "supported_tendency": True}]
    out = pc.rank_scheme_choices(choices=choices, coach_view=_view(["SCHEME_NERD"]), config=config["coaching"])
    neutral = pc.rank_scheme_choices(choices=choices, coach_view=None, config=pc.OFF)
    return {"neutral_ranked": neutral["ranked"], "ranked": out["ranked"],
            "shadow_ranked": out.get("shadow_ranked"), "attribution": out["attribution"], "advice_only": True}


def reconciliation_scenario():
    """Annual target includes applied checkpoint effects once; carry is not paid twice."""
    start = {"Awareness": 60, "PlayRecognition": 58, "Speed": 80}
    current = {"Awareness": 63, "PlayRecognition": 59, "Speed": 80}  # +2 neutral, +1 effect on Awareness
    applied = {"Awareness": 3, "PlayRecognition": 1}
    effect = {"Awareness": 1.0}
    neutral_target = {"Awareness": 63, "PlayRecognition": 60, "Speed": 79}
    with_effect = {k: min(99.0, v + effect.get(k, 0.0)) for k, v in neutral_target.items()}
    carry = {"Awareness": 0.4}
    a = offseason.reconcile_annual(start, current, neutral_target, applied, carry)
    b = offseason.reconcile_annual(start, current, with_effect, applied, carry)
    return {"start": start, "applied": applied, "applied_effect": effect,
            "neutral_target_only": {"final": a["candidate_base"], "effect_survives": a["candidate_base"]["Awareness"] - start["Awareness"]},
            "target_includes_effect_once": {"final": b["candidate_base"],
                                            "effect_survives": b["candidate_base"]["Awareness"] - start["Awareness"]},
            "carry_paid_again": False,
            "note": "With the neutral annual target alone, the applied checkpoint effect is absorbed (Awareness "
                    "ends at the neutral +3). With the applied effect added to the target once, the year ends at "
                    "neutral + effect (+4). Fractional carry is never paid a second time."}


def run(scenario, seed, seasons, start_season, config):
    if seasons < 1 or seasons > 40:
        raise DemoError("--seasons must be 1..40")
    report: dict[str, Any] = {
        "schema": "personality-development-demo-v1", "seed": seed, "seasons": seasons,
        "start_season": start_season, "handoff_season": start_season + seasons,
        "config": {"label": config.get("label", ""), "development": config["development"].as_dict(),
                   "coaching": config["coaching"].as_dict()},
        "labels": {"coefficients": "CANDIDATE (not approved; JR gate G3)",
                   "mentor": "offline only; disabled in progression integration",
                   "opportunity": "indirect feedback measured separately; separate activation"},
        "scenarios": {}}
    wanted = SCENARIOS if scenario == "all" else (scenario,)
    if any(s in wanted for s in ("learning", "teaching", "opportunity")):
        arms = run_arms(seed, seasons, start_season, config)
        summary, causal = summarize(arms)
        if "learning" in wanted:
            report["scenarios"]["learning"] = {"arms": {a: summary[a] for a in ("neutral", "preparation")}}
        if "teaching" in wanted:
            report["scenarios"]["teaching"] = {"arms": {a: summary[a] for a in ("neutral", "teaching")}}
        if "opportunity" in wanted:
            report["scenarios"]["opportunity"] = {"arms": {a: summary[a] for a in ("neutral", "opportunity")}}
        if scenario == "all":
            report["scenarios"]["paired_arms"] = {"arms": summary, "causal": causal,
                                                  "trajectories": {a: [{"season": y["season"], "mean_ovr": y["mean_ovr"]}
                                                                       for y in arms[a]["years"]] for a in ARMS}}
    if "mentoring" in wanted:
        report["scenarios"]["mentoring"] = mentoring_scenario(config)
    if "scheme" in wanted:
        report["scenarios"]["scheme"] = scheme_scenario(config)
    if "reconciliation" in wanted:
        report["scenarios"]["reconciliation"] = reconciliation_scenario()
    caps = config["development"]
    checks = []
    for name, sc in report["scenarios"].items():
        for arm, row in (sc.get("arms") or {}).items():
            checks.append({"scenario": name, "arm": arm,
                           "within_caps": (caps.mode != "enabled" and row["attributed_effect_total"] == 0)
                           or (row["max_annual_abs_spend"] <= caps.max_annual_abs_points + 1e-6
                               and row["max_per_rating_effect"] <= caps.max_per_rating_annual_difference + 1e-6
                               and row["max_paired_ovr_gap"] <= caps.max_annual_ovr_difference + 1e-6)})
    report["cap_checks"] = checks
    return report


def render_readme(report):
    lines = ["# Personality development demo (synthetic, offline)", "",
             f"Seed {report['seed']}, {report['seasons']} seasons from {report['start_season']} "
             f"(handoff state at {report['handoff_season']}). Config label: `{report['config']['label'] or 'none'}`; "
             f"development mode `{report['config']['development']['mode']}`, coaching mode "
             f"`{report['config']['coaching']['mode']}`.", "",
             "All coefficients are **candidates** for review, not an approved preset. Mentor effects are offline "
             "only (disabled in integration). Opportunity effects are indirect (more appearances, more neutral "
             "learning) and are not bounded by the direct-learning caps.", ""]
    paired = report["scenarios"].get("paired_arms")
    if paired:
        lines += ["| Arm | Final mean OVR diff | Rating-vector diff | Attributed effect | Max abs spend | Max paired OVR gap | Capped checkpoints |",
                  "|---|---|---|---|---|---|---|"]
        for arm, row in paired["arms"].items():
            lines.append(f"| {arm} | {row['mean_ovr_difference_by_season'][-1]} | {row['final_rating_vector_abs_difference']} | "
                         f"{row['attributed_effect_total']} | {row['max_annual_abs_spend']} | {row['max_paired_ovr_gap']} | "
                         f"{row['capped_checkpoints']} |")
        lines += ["", f"Combined interaction residual (unallocated): {paired['causal']['combined_interaction_residual']}", ""]
    for check in report["cap_checks"]:
        if not check["within_caps"]:
            lines.append(f"- CAP VIOLATION: {check['scenario']} / {check['arm']}")
    lines.append("")
    return "\n".join(lines)


def canonical_json(report) -> str:
    return json.dumps(report, sort_keys=True, indent=2, allow_nan=False) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m tools.personality_development_demo",
                                     description=__doc__.split("\n\n")[0])
    parser.add_argument("--scenario", choices=SCENARIOS + ("all",), default="all")
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--seasons", type=int, default=8)
    parser.add_argument("--start-season", type=int, default=2026)
    parser.add_argument("--config", help="JSON {development, coaching} candidate config; missing means off")
    parser.add_argument("--sweep", help="JSON list of candidate configs")
    parser.add_argument("--out", default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    try:
        config = load_config(_read_json(args.config) if args.config else None)
        report = run(args.scenario, args.seed, args.seasons, args.start_season, config)
        if args.sweep:
            candidates = _read_json(args.sweep)
            if not isinstance(candidates, list):
                raise DemoError("--sweep must be a JSON list")
            report["sweep"] = []
            for i, data in enumerate(candidates):
                cand = load_config(data)
                sub = run("all", args.seed, args.seasons, args.start_season, cand)
                report["sweep"].append({"index": i, "config": sub["config"],
                                        "causal": sub["scenarios"]["paired_arms"]["causal"],
                                        "cap_checks_ok": all(c["within_caps"] for c in sub["cap_checks"])})
    except DemoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(canonical_json(report))
    (out / "README.md").write_text(render_readme(report))
    print(f"wrote {out / 'report.json'} and {out / 'README.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
