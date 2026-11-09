"""Scouting reports (docs/done/SCOUTING_REVAMP_PLAN.md §2.3; PROJECT_SPEC §2.18).

Every prospect in a draft class gets a written scouting report in three editions a year (FALL at the class's first
stored step, BOWLS at step 21, FINAL at step 26). The engine decides every grade, trait score, role, flag and body
section; the LLM only rewrites the prose (the consensus top LLM_TOP per edition, plus a CLI for more).

Traits are the prospect's hidden Madden ratings seen through noise:
    true_t = Σ w·rating / Σ w
    obs_t  = true_t + σ_T(s) · (RHO_T · ẑ_C + √(1 − RHO_T²) · z_t)
σ_T falls linearly from SIGMA_T0 at the class's first step to SIGMA_T_DRAFT at step 27 (never below); ẑ_C is the
prospect's consensus error over its class sd at the step (the same input `scouting_boards.board` uses, so a prospect
the consensus overrates also looks better trait by trait); z_t is a unit normal seeded by (prospect_uid, trait) and
fixed across editions, so reports sharpen rather than flip. `score` is the prospect's percentile of obs_t among the
class's same-group prospects mapped to 1.0-9.0 (one decimal); the `word` comes from SCORE_WORDS. Class-relative
scores never expose a rating.

CLI:
    python3 -m engines.scouting_reports [--class 2037] [--edition FINAL] [--rewrite] [--limit N] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import math
import random
import sys

from engines import college_stats
from engines import gm_llm
from engines import scouting_events as sev
from engines import scouting_narrative as sn
from engines import scouting_value as sv

EDITIONS = {"FALL": None, "BOWLS": 21, "FINAL": 26}   # FALL = the class's first stored step
SIGMA_T0, SIGMA_T_DRAFT, RHO_T = 8.0, 4.0, 0.5        # rating points; estimates, checked by spike X3
LLM_TOP = 64                                           # consensus top N rewritten by the LLM per edition
_SIGMA_STEP = 27                                       # the step σ_T reaches SIGMA_T_DRAFT (scouting_engine.STEP_DRAFT)

ROLE_BANDS = ((15, "IMPACT_STARTER"), (48, "DAY1_STARTER"), (100, "EARLY_CONTRIBUTOR"),
              (180, "DEVELOPMENTAL"), (260, "DEPTH"), (None, "PRIORITY_FA"))
SCORE_WORDS = ((8.5, "elite"), (7, "plus"), (5, "solid"), (3.5, "below average"), (0, "a concern"))

ROLE_WORDS = {
    "IMPACT_STARTER": "an immediate impact starter",
    "DAY1_STARTER": "a Day 1 starter",
    "EARLY_CONTRIBUTOR": "an early contributor",
    "DEVELOPMENTAL": "a developmental prospect with starter upside",
    "DEPTH": "a depth piece",
    "PRIORITY_FA": "a priority free agent",
}

FLAG_WORDS = {
    "INJURY_HISTORY": "an injury history",
    "CONSISTENCY": "consistency questions",
    "INTERVIEWS_MIXED": "mixed combine interviews",
    "SMALL_SCHOOL": "small-school competition",
    "AGE": "an older prospect",
    "VOLATILE": "a volatile evaluation",
}

# Each trait: (trait, label, [rating names], [weights]). The Madden keys are those spike X1 confirmed; BCVision,
# Carrying and LongSnap replace the §2.3 draft's BallCarrierVision, CarryingAbility and (for LS) Awareness. Weights
# are uniform (the plan gives none); a rating missing from the stored dict is dropped from the average.
_WR = (
    ("speed", "speed", ["Speed"], [1.0]),
    ("routes", "route running", ["ShortRouteRunning", "MediumRouteRunning", "DeepRouteRunning"], [1.0, 1.0, 1.0]),
    ("hands", "hands", ["Catching", "CatchInTraffic"], [1.0, 1.0]),
    ("release", "release", ["Release"], [1.0]),
    ("contested", "contested-catch ability", ["SpectacularCatch", "Jumping"], [1.0, 1.0]),
)
_OL = (
    ("pass_pro", "pass protection", ["PassBlock", "PassBlockPower", "PassBlockFinesse"], [1.0, 1.0, 1.0]),
    ("run_block", "run blocking", ["RunBlock", "RunBlockPower", "RunBlockFinesse", "ImpactBlocking"],
     [1.0, 1.0, 1.0, 1.0]),
    ("strength", "strength", ["Strength"], [1.0]),
    ("feet", "footwork", ["Agility", "Awareness"], [1.0, 1.0]),
)
_DL = (
    ("get_off", "get-off", ["Acceleration", "Speed"], [1.0, 1.0]),
    ("power", "power", ["PowerMoves", "Strength"], [1.0, 1.0]),
    ("finesse", "finesse", ["FinesseMoves"], [1.0]),
    ("shed", "block-shedding", ["BlockShedding"], [1.0]),
    ("instincts", "instincts", ["PlayRecognition", "Pursuit"], [1.0, 1.0]),
)
_DB = (
    ("speed", "speed", ["Speed"], [1.0]),
    ("man", "man coverage", ["ManCoverage", "Press"], [1.0, 1.0]),
    ("zone", "zone coverage", ["ZoneCoverage", "PlayRecognition"], [1.0, 1.0]),
    ("ball_skills", "ball skills", ["Catching", "Jumping"], [1.0, 1.0]),
    ("tackling", "tackling", ["Tackle", "Pursuit"], [1.0, 1.0]),
)
_KICK = (
    ("power", "leg strength", ["KickPower"], [1.0]),
    ("accuracy", "accuracy", ["KickAccuracy"], [1.0]),
)

TRAITS = {
    "QB": (
        ("arm", "arm strength", ["ThrowPower"], [1.0]),
        ("accuracy", "accuracy", ["ThrowAccuracyShort", "ThrowAccuracyMid", "ThrowAccuracyDeep"], [1.0, 1.0, 1.0]),
        ("processing", "processing", ["Awareness", "PlayAction"], [1.0, 1.0]),
        ("pocket", "pocket presence", ["ThrowUnderPressure", "BreakSack"], [1.0, 1.0]),
        ("mobility", "mobility", ["Speed", "ThrowOnTheRun"], [1.0, 1.0]),
    ),
    "RB": (
        ("burst", "burst", ["Acceleration", "Speed"], [1.0, 1.0]),
        ("elusiveness", "elusiveness", ["JukeMove", "SpinMove", "ChangeOfDirection"], [1.0, 1.0, 1.0]),
        ("power", "power", ["Trucking", "BreakTackle"], [1.0, 1.0]),
        ("hands", "hands", ["Catching", "Carrying"], [1.0, 1.0]),
        ("vision", "vision", ["BCVision"], [1.0]),
    ),
    "WR": _WR,
    "TE": _WR + (("blocking", "blocking", ["RunBlock", "PassBlock"], [1.0, 1.0]),),
    "OT": _OL,
    "IOL": _OL,
    "EDGE": _DL,
    "DT": _DL,
    "LB": (
        ("range", "range", ["Speed", "Pursuit"], [1.0, 1.0]),
        ("instincts", "instincts", ["PlayRecognition", "Awareness"], [1.0, 1.0]),
        ("tackling", "tackling", ["Tackle", "HitPower"], [1.0, 1.0]),
        ("coverage", "coverage", ["ZoneCoverage", "ManCoverage"], [1.0, 1.0]),
        ("shed", "block-shedding", ["BlockShedding"], [1.0]),
    ),
    "CB": _DB,
    "S": _DB,
    "K": _KICK,
    "P": _KICK,
    "LS": (("snapping", "snapping", ["LongSnap"], [1.0]),),
}


# ------------------------------------------------------------------ editions ----
def edition_for(step, first_step):
    """The edition a step stores: FALL at the class's first step, BOWLS at 21, FINAL at 26, else None.
    A class first initialized after step 21 gets no FALL/BOWLS (only a FINAL edition at 26)."""
    if step == EDITIONS["FINAL"]:
        return "FINAL"
    if first_step > EDITIONS["BOWLS"]:
        return None
    if step == first_step:
        return "FALL"
    if step == EDITIONS["BOWLS"]:
        return "BOWLS"
    return None


def word_for(score):
    for threshold, word in SCORE_WORDS:
        if score >= threshold:
            return word
    return SCORE_WORDS[-1][1]


def role_for(rank):
    for limit, role in ROLE_BANDS:
        if limit is None or rank <= limit:
            return role
    return ROLE_BANDS[-1][1]


def sigma_t(step, first_step):
    """σ_T(s): falls linearly from SIGMA_T0 at the class's first step to SIGMA_T_DRAFT at step 27, never below."""
    span = max(_SIGMA_STEP - first_step, 1)
    f = min(max((step - first_step) / span, 0.0), 1.0)
    return SIGMA_T0 + (SIGMA_T_DRAFT - SIGMA_T0) * f


def volatile_keys(vols):
    """{key} whose consensus-move sd is in the class's top quartile (0.0 excluded, so a step with no move history
    flags nobody)."""
    pos = sorted(v for v in vols.values() if v and v > 0)
    if not pos:
        return set()
    k = max(1, (len(pos) + 3) // 4)          # the highest quarter (ceil), at least one
    threshold = pos[-k]
    return {key for key, v in vols.items() if v and v > 0 and v >= threshold}


# ---------------------------------------------------------------- observation ----
def _observe(cls, cons, ratings, uids, step, first_step):
    """{key: [{"trait", "label", "score", "word"}]} sorted by score desc ({} for a traits-free prospect)."""
    errs = [r["err"] for r in cons.values() if isinstance(r.get("err"), (int, float))]
    if not errs:
        return {}
    mean = sum(errs) / len(errs)
    sd = math.sqrt(sum((e - mean) ** 2 for e in errs) / max(len(errs) - 1, 1)) or 1.0
    sig = sigma_t(step, first_step)
    cross = math.sqrt(1.0 - RHO_T * RHO_T)

    group_obs = {}                                     # group -> {key: {trait: (obs, label)}}
    for p in cls:
        key = p["key"]
        if key not in uids or key not in cons:
            continue
        entry = ratings.get(key)
        if entry is None:
            continue
        ratings_dict = entry[1] if isinstance(entry, tuple) and len(entry) == 2 else entry
        if not isinstance(ratings_dict, dict):
            continue
        group = sev.stat_group(p.get("position"))
        traits = TRAITS.get(group, ())
        if not traits:
            continue
        zc = (cons[key]["err"] - mean) / sd
        uid = uids[key]
        for trait, label, names, weights in traits:
            vals, wsum = [], 0.0
            for n, w in zip(names, weights):
                v = ratings_dict.get(n)
                if isinstance(v, (int, float)):
                    vals.append(v * w)
                    wsum += w
            if not vals or wsum <= 0:
                continue
            true_t = sum(vals) / wsum
            z_t = random.Random(f"trait:{uid}:{trait}").gauss(0, 1)
            group_obs.setdefault(group, {}).setdefault(key, {})[trait] = (
                true_t + sig * (RHO_T * zc + cross * z_t), label)

    out = {}
    for group, key_obs in group_obs.items():
        for trait in sorted({t for tdict in key_obs.values() for t in tdict}):
            vals = [(k, tdict[trait][0]) for k, tdict in key_obs.items() if trait in tdict]
            n = len(vals)
            for k, obs in vals:
                below = sum(o < obs for _, o in vals) + 0.5 * (sum(o == obs for _, o in vals) - 1)
                pct = below / max(n - 1, 1) if n > 1 else 0.5
                score = round(1.0 + 8.0 * pct, 1)
                out.setdefault(k, []).append({"trait": trait, "label": key_obs[k][trait][1],
                                              "score": score, "word": word_for(score)})
    for k in out:
        out[k].sort(key=lambda t: -t["score"])
    return out


# ------------------------------------------------------------------- reports ----
def _school_display(p):
    sch = p.get("school") or {}
    return sch.get("display") or (p.get("college") or "").title() or "his school"


def _position_words(p):
    return sv.POSITION_WORDS.get((p.get("position") or "").upper(), "prospect")


def flags_for(p, events, volatile):
    """Public-fact flags from stored events/payloads, the board's school and age, and the class's volatility.
    INTERVIEWS_MIXED is a property of the combine profile, so it flags on every edition, not only FINAL."""
    flags = []
    for e in events:
        kind = e.get("kind")
        payload = e.get("payload") or {}
        if kind == "STORYLINE" and payload.get("subtype") == "injury":
            flags.append("INJURY_HISTORY")
        elif kind == "MEDICAL":
            flags.append("INJURY_HISTORY")
        elif kind == "STORYLINE" and payload.get("subtype") in ("character", "rough_outing"):
            flags.append("CONSISTENCY")
        elif kind == "COMBINE" and payload.get("interviews") == "mixed":
            flags.append("INTERVIEWS_MIXED")
    if (p.get("school") or {}).get("level") not in ("FBS", "P4"):
        flags.append("SMALL_SCHOOL")
    if (p.get("age") or 0) >= 24:
        flags.append("AGE")
    if volatile:
        flags.append("VOLATILE")
    return list(dict.fromkeys(flags))


def _list_sentence(prefix, items, empty_note):
    return f"{prefix}: {', '.join(items)}." if items else empty_note


def _athletic_text(combine):
    if not combine or not combine.get("participated"):
        return ""
    forty = next((d for d in combine.get("drills", []) if d.get("key") == "forty"), None)
    if forty and forty.get("value"):
        return f"At the combine he ran the 40 in {forty['value']}."
    return "He worked out at the combine."


def _body(p, cons_row, role, strengths, concerns, flags, production, combine):
    name = (p.get("full_name") or "").title() or p["key"].split(":")[1].title()
    pos = _position_words(p)
    school = _school_display(p)
    role_words = ROLE_WORDS.get(role, "a prospect")
    return {
        "summary": f"{name} is a {pos} out of {school} who projects as {role_words}.",
        "strengths": _list_sentence("Best traits", strengths, "No standout traits on this view."),
        "concerns": _list_sentence("Concerns", concerns, "No major concerns on tape."),
        "production": production or "Limited college production on the board.",
        "athletic": _athletic_text(combine),
        "projection": f"{name} projects as {role_words} and carries a {cons_row['round_label']} grade.",
        "bottom_line": (f"{name} grades out as {role_words}. He brings "
                        f"{', '.join(strengths) if strengths else 'a well-rounded skill set'}, with "
                        f"{', '.join(concerns) if concerns else 'no major concerns'} to weigh against the tape."),
    }


def build_report(p, cons_row, events, obs, dossier_bits, edition, step):
    """One prospect's report (pure). grade/rank/round_label from the consensus row; role from ROLE_BANDS;
    strengths = up to 3 traits scored >= 7 (highest first); concerns = up to 2 traits scored <= 3.5 (lowest first)
    then flag words; flags from stored events/payloads and public facts only. The body never mentions a number the
    fact sheet doesn't hold."""
    rank = cons_row["rank"]
    role = role_for(rank)
    flags = flags_for(p, events, bool(dossier_bits.get("volatile")))
    strengths = [t["label"] for t in obs if t["score"] >= 7][:3]
    concerns = [t["label"] for t in sorted(obs, key=lambda t: t["score"]) if t["score"] <= 3.5][:2]
    concerns += [FLAG_WORDS[f] for f in flags]
    body = _body(p, cons_row, role, strengths, concerns, flags, dossier_bits.get("production"),
                 dossier_bits.get("combine"))
    return {
        "grade": float(cons_row["grade"]),
        "rank": rank,
        "round_label": cons_row.get("round_label") or sv.round_label(rank),
        "role": role,
        "traits": list(obs),
        "strengths": strengths,
        "concerns": concerns,
        "flags": flags,
        "body": body,
    }


def build_edition(cls, cons, events_by_key, ratings, uids, step, first_step, draft_season, vols,
                  directors=None, team_keys=None, signals=None):
    """One report row per non-WITHDRAWN prospect that has a prospect_uid in uids (OK status only), pure.

    `directors` (a list of scout_directors director views) adds each team's private row: the consensus row is
    copied, its grade shifts by the director's bounded `regional_bias` and it carries his `scout_id`/`voice`.
    `team_keys` ({team_id: keys}) limits a team's rows to the prospects on its board (default: every report).
    `signals` ({key: {athleticism, production, flags, volatility}}) feeds the bias; without it a director's
    bias is 0. The department row (team_id/scout_id/voice None) never changes."""
    edition = edition_for(step, first_step)
    if edition is None:
        return []
    obs_by_key = _observe(cls, cons, ratings, uids, step, first_step)
    volatile = volatile_keys(vols)
    out = []
    for p in cls:
        key = p["key"]
        if p.get("status", "ACTIVE") == "WITHDRAWN" or key not in uids or key not in cons:
            continue
        events = events_by_key.get(key, [])
        position = p.get("position")
        production = college_stats.aggregate_games(events, position)["summary_line"]
        combine = None
        if edition == "FINAL":
            combine = college_stats.combine_profile(events, position, p.get("height_in"), p.get("weight"))
        report = build_report(p, cons[key], events, obs_by_key.get(key, []),
                              {"production": production, "combine": combine, "volatile": key in volatile},
                              edition, step)
        report.update({"draft_season": draft_season, "step": step, "prospect_uid": uids[key],
                       "prospect_key": key, "edition": edition, "text_source": "TEMPLATE",
                       "team_id": None, "scout_id": None, "voice": None})
        out.append(report)
    if directors:
        from engines import scout_directors as sd
        base = {r["prospect_key"]: r for r in out}
        for director in directors:
            tid = director.get("team_id")
            keys = team_keys.get(tid) if team_keys else None
            pool = [base[k] for k in keys if k in base] if keys is not None else list(base.values())
            view = sd.voice(director)
            for row in pool:
                sig = (signals or {}).get(row["prospect_key"]) or {}
                bias = sd.board_bias(director, sig)
                team_row = dict(row, team_id=tid, scout_id=view.get("scout_id"), voice=view,
                                grade=round(max(0.0, row["grade"] + bias), 1))
                out.append(team_row)
    return out


def report_rows(reports):
    """[(...)] in scout_reports column order (for _write_step): the stored columns, JSONB fields serialized."""
    return [(r["draft_season"], r["step"], r["prospect_uid"], r["prospect_key"], r["edition"], r["grade"],
             r["rank"], r["round_label"], r["role"], json.dumps(r["traits"]), json.dumps(r["strengths"]),
             json.dumps(r["concerns"]), json.dumps(r["flags"]), json.dumps(r["body"]), r["text_source"],
             r.get("team_id"), r.get("scout_id"),
             json.dumps(r["voice"]) if r.get("voice") is not None else None)
            for r in reports]


# ----------------------------------------------------------------------- db ----
def _table(cur, name):
    cur.execute(f"SELECT to_regclass('{name}') IS NOT NULL")
    row = cur.fetchone()
    return bool(row and row[0])


def _json(v):
    return json.loads(v) if isinstance(v, str) else v


def load_ratings(cur, draft_season):
    """{prospect_key: (prospect_uid, ratings)} for the class's current aliases (joined through scout_prospect_ids;
    KEY_COLLISION people left out). {} without the tables."""
    if not _table(cur, "scout_prospect_ratings") or not _table(cur, "scout_prospect_ids"):
        return {}
    cur.execute("""SELECT i.prospect_key, r.prospect_uid, r.ratings
                   FROM scout_prospect_ratings r
                   JOIN scout_prospect_ids i ON i.prospect_uid = r.prospect_uid
                   WHERE r.draft_season = %s AND i.status = 'OK'""", (draft_season,))
    return {k: (str(uid), _json(ratings)) for k, uid, ratings in cur.fetchall()}


def fact_sheet(report, prospect):
    """scouting_narrative.fact_sheet extended with the report's public fields, so validate_scout_text can allow the
    trait words/scores, production line, combine marks and role words the prose may cite."""
    base = sn.fact_sheet({"kind": "REPORT", "payload": {}, "blurb": (report.get("body") or {}).get("bottom_line", "")},
                         prospect)
    body = report.get("body") or {}
    base.update({
        "role_words": ROLE_WORDS.get(report.get("role")),
        "round_label": report.get("round_label"),
        "traits": report.get("traits", []),
        "strengths": report.get("strengths", []),
        "concerns": report.get("concerns", []),
        "flags": report.get("flags", []),
        "flag_words": [FLAG_WORDS.get(f, f) for f in report.get("flags", [])],
        "production": body.get("production", ""),
        "athletic": body.get("athletic", ""),
        "projection": body.get("projection", ""),
        "template_summary": body.get("summary", ""),
        "template_bottom_line": body.get("bottom_line", ""),
    })
    return base


def report_prompt(sheet, voice, section):
    """The LLM prompt for one body section (label scout_report). voice: a scout_voice.ScoutVoice."""
    from engines.scout_voice import voice_prompt
    return ("You are a college football scout writing one section of a scouting report for an NFL draft board, in "
            f"this voice: {voice_prompt(voice)}. Rewrite the {section} template below as 2-3 sentences. Use only the "
            "facts in the JSON: no other numbers, schools, NFL teams or heights. Never mention ratings, OVR or "
            "Madden. An injury is day-to-day at most, and never say a player is out or will miss time. You may add "
            "scout's colour (tape study, body language, scheme detail) without new facts. Answer with the section "
            "text only.\n"
            f"FACTS: {json.dumps(sheet, default=str)}\nTEMPLATE: {sheet.get('template_' + section)}")


def _edition_rows(cur, draft_season, edition):
    cur.execute("""SELECT r.prospect_uid, r.prospect_key, r.rank, r.grade, r.round_label, r.role, r.traits,
                          r.strengths, r.concerns, r.flags, r.body, r.text_source,
                          d.full_name, d.position, d.college, d.age, d.height_in, d.weight, sp.class_year
                   FROM scout_reports r
                   JOIN scout_prospects sp ON sp.draft_season = r.draft_season AND sp.prospect_key = r.prospect_key
                   LEFT JOIN draft_prospects d ON d.id = sp.prospect_id
                   WHERE r.draft_season = %s AND r.edition = %s AND r.text_source = 'TEMPLATE'
                     AND r.team_id IS NULL
                   ORDER BY r.rank""", (draft_season, edition))
    cols = ("prospect_uid", "prospect_key", "rank", "grade", "round_label", "role", "traits", "strengths",
            "concerns", "flags", "body", "text_source", "full_name", "position", "college", "age", "height_in",
            "weight", "class_year")
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    for r in rows:
        r["prospect_uid"] = str(r["prospect_uid"])
        r["grade"] = float(r["grade"])
        for k in ("traits", "strengths", "concerns", "flags", "body"):
            r[k] = _json(r[k])
    return rows


# The primary college stat whose total is a prospect's "production" for a director's lean (review-only proxy).
PRIMARY_STAT = {"QB": "pass_yds", "RB": "rush_yds", "WR": "rec_yds", "TE": "rec_yds", "OT": "snaps",
                "IOL": "snaps", "EDGE": "sacks", "DT": "sacks", "LB": "tackles", "CB": "pbu", "S": "tackles",
                "K": "fgm", "P": "punts", "LS": "snaps"}


def _percentile(value, values):
    """0-100 mid-rank percentile of value among values (50.0 when there is only itself)."""
    n = len(values)
    if n <= 1:
        return 50.0
    below = sum(v < value for v in values) + 0.5 * (sum(v == value for v in values) - 1)
    return round(100.0 * below / (n - 1), 1)


def director_signals(cur, cls, step, draft_season, vols, edition=None):
    """{key: {athleticism, production, flags, volatility}} for scout_directors.board_bias, from stored events
    through `step`. Review-only proxies: athleticism is the combine composite (0-100, absent when he didn't work
    out); production is the prospect's percentile of his group's primary college stat (PRIMARY_STAT, 0-100);
    flags are the public report flags (STORYLINE and MEDICAL events included, so CHARACTER_HAWK can see
    CONSISTENCY); volatility is the step's consensus-move sd, flagged in the class's top quartile as the report
    rows flag it."""
    keys = [p["key"] for p in cls]
    if not keys:
        return {}
    marks = ", ".join(["%s"] * len(keys))
    cur.execute(f"""SELECT prospect_key, kind, payload, headline, blurb FROM scout_events
                    WHERE draft_season = %s AND step <= %s AND prospect_key IN ({marks})
                      AND kind IN ('GAME', 'COMBINE', 'STORYLINE', 'MEDICAL') ORDER BY step""",
                (draft_season, step, *keys))
    by_key = {}
    for key, kind, payload, headline, blurb in cur.fetchall():
        payload = _json(payload) or {}
        by_key.setdefault(key, []).append({"kind": kind, "payload": payload, "headline": headline, "blurb": blurb})
    group_of = {p["key"]: sev.stat_group(p.get("position")) for p in cls}
    totals = {}
    for p in cls:
        group = group_of[p["key"]]
        stat = PRIMARY_STAT.get(group)
        by_key_evs = by_key.get(p["key"], [])
        production = 0.0
        for e in by_key_evs:
            if e["kind"] != "GAME":
                continue
            stats = (e["payload"] or {}).get("stats") or {}
            v = stats.get(stat) if stat else None
            if isinstance(v, (int, float)):
                production += float(v)
        totals.setdefault(group, {})[p["key"]] = production
    volatile = volatile_keys(vols)
    out = {}
    for p in cls:
        key, group = p["key"], group_of[p["key"]]
        evs = by_key.get(key, [])
        combine = next((e for e in evs if e["kind"] == "COMBINE"), None)
        profile = college_stats.combine_profile([combine] if combine else [], p.get("position"),
                                                p.get("height_in"), p.get("weight")) if combine else {}
        athletic = profile.get("athletic_score")
        flags = flags_for(p, evs, key in volatile)
        out[key] = {
            "athleticism": None if athletic is None else float(athletic) * 10.0,
            "production": _percentile(totals.get(group, {}).get(key, 0.0), list(totals.get(group, {}).values())),
            "flags": flags,
            "volatility": (vols or {}).get(key) or 0.0,
        }
    return out


def rewrite(cur, draft_season, edition, llm, limit=LLM_TOP, keys=None):
    """Rewrite body.summary and body.bottom_line for the edition's consensus top `limit` (or `keys`) still TEMPLATE,
    via the llm (label scout_report). Each section is validated with validate_scout_text; a rejection, an error or no
    llm keeps the template. Never touches any other field. Returns the count rewritten."""
    if llm is None or not _table(cur, "scout_reports"):
        return 0
    rows = _edition_rows(cur, draft_season, edition)
    if keys is not None:
        keyset = set(keys)
        rows = [r for r in rows if r["prospect_key"] in keyset]
    else:
        rows = rows[:limit]
    from engines import scout_voice
    voice = scout_voice.department_voice()
    n = 0
    for row in rows:
        report = {k: row[k] for k in ("prospect_uid", "prospect_key", "grade", "rank", "round_label", "role",
                                      "traits", "strengths", "concerns", "flags", "body")}
        prospect = {"full_name": row.get("full_name") or row["prospect_key"].split(":")[1],
                    "position": row.get("position") or row["prospect_key"].split(":")[-1],
                    "college": row.get("college"), "age": row.get("age"), "height_in": row.get("height_in"),
                    "weight": row.get("weight"), "class_year": row.get("class_year")}
        prospect["school"] = sv.school(row.get("college"))
        sheet = fact_sheet(report, prospect)
        new_body = dict(report["body"])
        changed = False
        for section in ("summary", "bottom_line"):
            text = gm_llm.call(llm, report_prompt(sheet, voice, section))
            if not text:
                continue
            text = text.strip().strip('"')
            ok, _ = sn.validate_scout_text(text, sheet)
            if ok:
                new_body[section] = text
                changed = True
        if changed:
            cur.execute("UPDATE scout_reports SET body = %s, text_source = 'LLM' "
                        "WHERE prospect_uid = %s AND edition = %s AND team_id IS NULL",
                        (json.dumps(new_body), row["prospect_uid"], edition))
            n += 1
    return n


def report_for(cur, prospect_uid, edition=None, team_id=None):
    """The latest (or named) edition for a prospect_uid: public fields only. None when there is none.

    team_id None reads the department (public consensus) row; a team_id reads that team's private row when its
    director has written one, falling back to the department row. A team's rows cover only its stored top 100, so
    both are read and the later step wins (the team row only on a tie): a prospect who fell off the team's newer
    board must not be served the team's older edition while the department has the newer one."""
    if not _table(cur, "scout_reports"):
        return None
    cols = ("draft_season", "step", "prospect_key", "edition", "grade", "rank", "round_label", "role", "traits",
            "strengths", "concerns", "flags", "body", "text_source", "scout_id", "voice", "team_id")

    def _read(tid):
        sql = f"""SELECT {', '.join(cols)}
                  FROM scout_reports WHERE prospect_uid = %s AND team_id {'IS NULL' if tid is None else '= %s'}"""
        args = [str(prospect_uid)] + ([] if tid is None else [tid])
        if edition:
            sql += " AND edition = %s"
            args.append(edition)
        sql += " ORDER BY step DESC LIMIT 1"
        cur.execute(sql, args)
        return cur.fetchone()

    if team_id is None:
        row = _read(None)
    else:
        row, dept = _read(team_id), _read(None)
        if row is None or (dept is not None and int(dept[1]) > int(row[1])):     # index 1 is `step`
            row = dept
    if not row:
        return None
    d = dict(zip(cols, row))
    d["prospect_uid"] = str(prospect_uid)
    d["grade"] = float(d["grade"])
    for k in ("traits", "strengths", "concerns", "flags", "body", "voice"):
        d[k] = _json(d[k])
    d["role_words"] = ROLE_WORDS.get(d["role"])
    return d


def editions_for(cur, prospect_uid):
    """The editions actually stored for a prospect, in chronological (step) order. [] without the table."""
    if not _table(cur, "scout_reports"):
        return []
    cur.execute("SELECT edition FROM scout_reports WHERE prospect_uid = %s AND team_id IS NULL ORDER BY step",
                (str(prospect_uid),))
    return [r[0] for r in cur.fetchall()]


# ---------------------------------------------------------------------- cli ----
def _connect():
    import psycopg
    from pipeline.load_capture import DSN
    return psycopg.connect(DSN)


def _print_counts(cur, draft_season, edition=None):
    where, args = "WHERE draft_season = %s AND team_id IS NULL", [draft_season]
    if edition:
        where += " AND edition = %s"
        args.append(edition)
    print(f"scouting reports {draft_season}{' (' + edition + ')' if edition else ''}:")
    cur.execute(f"SELECT edition, count(*) FROM scout_reports {where} GROUP BY edition ORDER BY edition", args)
    print("  by edition: " + _counts(cur.fetchall()))
    cur.execute(f"SELECT role, count(*) FROM scout_reports {where} GROUP BY role ORDER BY role", args)
    print("  by role: " + _counts(cur.fetchall()))
    cur.execute(f"""SELECT flag, count(*) FROM scout_reports, jsonb_array_elements_text(flags) AS flag
                    {where} GROUP BY flag ORDER BY flag""", args)
    print("  by flag: " + _counts(cur.fetchall()))


def _counts(rows):
    """`"FALL:3, FINAL:4"`, or "(none)" when there are no rows (the old expression concatenated first, so the
    fallback could never print)."""
    return ", ".join(f"{k}:{n}" for k, n in rows) or "(none)"


def _template_keys(cur, draft_season, edition, limit):
    editions = (edition,) if edition else ("FALL", "BOWLS", "FINAL")
    out = []
    for ed in editions:
        cur.execute("""SELECT prospect_key FROM scout_reports WHERE draft_season = %s AND edition = %s
                       AND text_source = 'TEMPLATE' AND team_id IS NULL ORDER BY rank LIMIT %s""",
                    (draft_season, ed, limit))
        out += [r[0] for r in cur.fetchall()]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Scouting reports (V2.10): editions, roles, flags and the LLM rewrite")
    ap.add_argument("--class", dest="draft_season", type=int, default=None,
                    help="draft class (default: the league's season + 1)")
    ap.add_argument("--edition", choices=("FALL", "BOWLS", "FINAL"), default=None)
    ap.add_argument("--rewrite", action="store_true", help="rewrite the edition's top reports with the LLM")
    ap.add_argument("--limit", type=int, default=LLM_TOP)
    ap.add_argument("--dry-run", action="store_true", help="print what would happen, write nothing")
    a = ap.parse_args(argv)
    with _connect() as conn:
        cur = conn.cursor()
        if a.draft_season is None:
            from engines import scouting_engine as se
            a.draft_season = se.current_step(cur)[0] + 1
        if a.rewrite:
            if a.dry_run:
                keys = _template_keys(cur, a.draft_season, a.edition, a.limit)
                print(f"rewrite {a.draft_season}: {len(keys)} reports would be rewritten")
            else:
                llm = gm_llm.make_llm("scout_report")
                if llm is None:
                    print("no LLM configured: nothing rewritten")
                else:
                    editions = (a.edition,) if a.edition else ("FALL", "BOWLS", "FINAL")
                    total = sum(rewrite(cur, a.draft_season, ed, llm, a.limit) for ed in editions)
                    print(f"rewrite {a.draft_season}: {total} reports rewritten")
        else:
            _print_counts(cur, a.draft_season, a.edition)
        if a.dry_run or not a.rewrite:
            conn.rollback()                       # reading, or a rehearsal: nothing to keep
        else:
            conn.commit()                         # --rewrite really saves; it used to roll back every time
    return 0


if __name__ == "__main__":
    sys.exit(main())
