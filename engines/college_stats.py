"""
College season stats aggregation, game logs, combine percentile cards, and awards compilation for Draft Central
(docs/DRAFT_CENTRAL_PLAN.md; PROJECT_SPEC §2.18, §4.6).

Pure helpers (`aggregate_games`, `combine_profile`, `format_summary_line`) operate on public event dicts without DB
access. Database readers (`prospect_dossier`, `board_stat_summaries`, `compile_awards_and_honors`) take an open tuple
cursor and expose only public fields — never `true_value`, `true_rank`, `err`, `impact`, `_production_pct`, or Madden
`ovr`.
"""
import json
import math
from typing import Any, Dict, List, Optional

from engines import scouting_events as sev
from engines import scouting_read as sr
from engines import scouting_value as sv
from pipeline.ingest_prospects import inches_height

STEP_PHASE_LABELS = {
    "REGULAR": "College Regular Season",
    "CONF_CHAMP": "Conference Championships",
    "BOWLS": "Bowl Season & CFP",
    "ALL_STAR": "All-Star Bowl Practices",
    "COMBINE": "NFL Scouting Combine",
    "PRO_DAY": "Pro Days & Top-30 Visits",
    "DRAFT_WEEK": "NFL Draft Week",
}


def step_label(step: int) -> str:
    """Human-readable calendar label for a scouting step (1-27)."""
    ph = sev.phase(step)
    if ph == "REGULAR":
        return f"College Week {sev.college_week(step)} (NFL Wk {step})"
    if ph == "CONF_CHAMP":
        return "Conference Championship Week"
    if ph == "BOWLS":
        return f"Bowl & CFP Window (Wk {step})"
    if ph == "ALL_STAR":
        return "Senior Bowl" if step == 23 else "East-West Shrine Bowl"
    if ph == "COMBINE":
        return "NFL Scouting Combine"
    if ph == "PRO_DAY":
        return f"Pro Days & Visits ({'Early' if step == 25 else 'Late'})"
    return "NFL Draft Week"


def game_verdict(headline: Optional[str]) -> str:
    """Derive scout game evaluation verdict from the event headline."""
    h = (headline or "").lower()
    if "shines" in h:
        return "SHINES"
    if "struggles" in h:
        return "STRUGGLES"
    return "STEADY"


def _nfl_passer_rating(comp: int, att: int, yds: int, td: int, ints: int) -> float:
    if att <= 0:
        return 0.0
    a = max(0.0, min(2.375, ((comp / att) - 0.3) * 5.0))
    b = max(0.0, min(2.375, ((yds / att) - 3.0) * 0.25))
    c = max(0.0, min(2.375, (td / att) * 20.0))
    d = max(0.0, min(2.375, 2.375 - ((ints / att) * 25.0)))
    return round(((a + b + c + d) / 6.0) * 100.0, 1)


def format_summary_line(group: str, totals: Dict[str, Any]) -> str:
    """Compact one-line season stat summary for Big Board rows and dossier headers."""
    g = totals.get("games", 0)
    if not g:
        return "No charted games"
    prefix = f"{g} G · "
    if group == "QB":
        return (f"{prefix}{totals.get('comp', 0)}/{totals.get('att', 0)} ({totals.get('comp_pct', 0.0)}%) · "
                f"{totals.get('pass_yds', 0):,} YDS · {totals.get('pass_td', 0)} TD · {totals.get('int', 0)} INT")
    if group == "RB":
        return (f"{prefix}{totals.get('carries', 0)} CAR · {totals.get('rush_yds', 0):,} YDS "
                f"({totals.get('ypc', 0.0)} YPC) · {totals.get('rush_td', 0)} TD · {totals.get('rec', 0)} REC")
    if group in ("WR", "TE"):
        return (f"{prefix}{totals.get('rec', 0)} REC ({totals.get('targets', 0)} TGT) · "
                f"{totals.get('rec_yds', 0):,} YDS ({totals.get('ypr', 0.0)} YPR) · {totals.get('rec_td', 0)} TD")
    if group in ("OT", "IOL"):
        return (f"{prefix}{totals.get('snaps', 0)} SNAPS · {totals.get('sacks_allowed', 0)} SACK ALLOWED · "
                f"{totals.get('pressures_allowed', 0)} PRESS ALLOWED")
    if group in ("EDGE", "DT"):
        return (f"{prefix}{totals.get('tackles', 0)} TKL · {totals.get('tfl', 0)} TFL · "
                f"{totals.get('sacks', 0)} SACK · {totals.get('pressures', 0)} PRESS")
    if group == "LB":
        return (f"{prefix}{totals.get('tackles', 0)} TKL · {totals.get('tfl', 0)} TFL · "
                f"{totals.get('sacks', 0)} SACK · {totals.get('pbu', 0)} PBU")
    if group == "CB":
        return (f"{prefix}{totals.get('catches_allowed', 0)}/{totals.get('targets', 0)} ALLOWED "
                f"({totals.get('comp_pct_allowed', 0.0)}%) · {totals.get('pbu', 0)} PBU · {totals.get('int', 0)} INT")
    if group == "S":
        return f"{prefix}{totals.get('tackles', 0)} TKL · {totals.get('pbu', 0)} PBU · {totals.get('int', 0)} INT"
    if group == "K":
        return (f"{prefix}{totals.get('fgm', 0)}/{totals.get('fga', 0)} FG ({totals.get('fg_pct', 0.0)}%) · "
                f"LNG {totals.get('fg_long', 0)} · {totals.get('xpm', 0)} XP")
    if group == "P":
        return f"{prefix}{totals.get('punts', 0)} PUNTS · {totals.get('punt_avg', 0.0)} AVG · {totals.get('inside_20', 0)} IN20"
    return f"{prefix}{totals.get('snaps', 0)} CLEAN SNAPS"


def aggregate_games(events: List[Dict[str, Any]], position: Optional[str]) -> Dict[str, Any]:
    """Aggregate public GAME events for one prospect into cumulative college season stats and a sorted game log."""
    group = sev.stat_group(position)
    keys = sev.STAT_KEYS.get(group, ("snaps", "bad_snaps"))
    game_events = [e for e in events if e.get("kind") == "GAME" and isinstance((e.get("payload") or {}).get("stats"), dict)]
    game_events.sort(key=lambda e: int(e.get("step") or 0))

    game_log: List[Dict[str, Any]] = []
    sums: Dict[str, float] = {k: 0.0 for k in keys}
    fg_long = 0
    punt_weighted_yds = 0.0

    for ev in game_events:
        pay = sr.public_payload(ev.get("payload") or {})
        raw_stats = pay.get("stats") or {}
        clean_stats: Dict[str, Any] = {}
        for k in keys:
            val = raw_stats.get(k, 0)
            clean_stats[k] = val
            if k == "fg_long":
                fg_long = max(fg_long, int(val or 0))
            elif k == "punt_avg":
                punts_this_game = int(raw_stats.get("punts", 0) or 0)
                punt_weighted_yds += float(val or 0.0) * punts_this_game
            else:
                sums[k] = sums.get(k, 0.0) + float(val or 0)

        line_fn = sev.GAME_LINE.get(group, sev.GAME_LINE["LS"])
        try:
            line_text = line_fn(clean_stats)
        except Exception:
            line_text = ""

        game_log.append({
            "step": ev.get("step"),
            "college_week": pay.get("college_week"),
            "game_type": pay.get("game_type") or "REGULAR",
            "label": pay.get("label") or step_label(int(ev.get("step") or 1)),
            "opponent": pay.get("opponent") or "Opponent",
            "verdict": game_verdict(ev.get("headline")),
            "headline": ev.get("headline"),
            "blurb": ev.get("blurb"),
            "stats": clean_stats,
            "stat_line": line_text,
        })

    n_games = len(game_log)
    totals: Dict[str, Any] = {"games": n_games}
    if n_games > 0:
        for k in keys:
            if k == "fg_long":
                totals[k] = fg_long
            elif k == "punt_avg":
                total_punts = int(sums.get("punts", 0))
                totals[k] = round(punt_weighted_yds / total_punts, 1) if total_punts > 0 else 0.0
            else:
                totals[k] = int(round(sums[k]))

        if group == "QB":
            comp, att = totals.get("comp", 0), totals.get("att", 0)
            yds, td, ints = totals.get("pass_yds", 0), totals.get("pass_td", 0), totals.get("int", 0)
            totals["comp_pct"] = round((comp / att) * 100.0, 1) if att > 0 else 0.0
            totals["ypa"] = round(yds / att, 1) if att > 0 else 0.0
            totals["passer_rating"] = _nfl_passer_rating(comp, att, yds, td, ints)
        elif group == "RB":
            car, r_yds = totals.get("carries", 0), totals.get("rush_yds", 0)
            totals["ypc"] = round(r_yds / car, 1) if car > 0 else 0.0
            totals["total_yds"] = r_yds + totals.get("rec_yds", 0)
        elif group in ("WR", "TE"):
            rec, tgt, yds = totals.get("rec", 0), totals.get("targets", 0), totals.get("rec_yds", 0)
            totals["catch_pct"] = round((rec / tgt) * 100.0, 1) if tgt > 0 else 0.0
            totals["ypr"] = round(yds / rec, 1) if rec > 0 else 0.0
        elif group in ("OT", "IOL"):
            snaps = totals.get("snaps", 0)
            sacks_a = totals.get("sacks_allowed", 0)
            press_a = totals.get("pressures_allowed", 0)
            pass_snaps = max(1.0, snaps * 0.58)
            eff = max(0.0, min(100.0, (1.0 - (sacks_a + 0.5 * press_a) / pass_snaps) * 100.0))
            totals["pass_block_efficiency"] = round(eff, 1)
        elif group == "CB":
            tgt, cat = totals.get("targets", 0), totals.get("catches_allowed", 0)
            totals["comp_pct_allowed"] = round((cat / tgt) * 100.0, 1) if tgt > 0 else 0.0
        elif group == "K":
            fgm, fga = totals.get("fgm", 0), totals.get("fga", 0)
            totals["fg_pct"] = round((fgm / fga) * 100.0, 1) if fga > 0 else 0.0

    return {
        "stat_group": group,
        "stat_keys": list(keys),
        "games": n_games,
        "totals": totals,
        "summary_line": format_summary_line(group, totals),
        "game_log": game_log,
    }


def _norm_cdf_pct(z: float) -> int:
    """Convert a z-score into an integer percentile clamped to [5, 99]."""
    p = 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    return max(5, min(99, int(round(p * 100))))


def combine_profile(events: List[Dict[str, Any]], position: Optional[str],
                    height_in: Optional[int] = None, weight: Optional[int] = None) -> Dict[str, Any]:
    """Extract Step 24 Combine drill results and compute positional athletic percentiles & composite score."""
    group = sev.stat_group(position)
    comb_ev = next((e for e in events if e.get("kind") == "COMBINE"), None)
    pro_ev = next((e for e in events if e.get("kind") == "PRO_DAY"), None)

    base_wt = weight or sev.TYPICAL_WT.get(group, 230)
    heavy = min(max((base_wt - 190.0) / 140.0, 0.0), 1.0)
    exp_forty = sev.FORTY.get(group, 4.70) + (base_wt - sev.TYPICAL_WT.get(group, base_wt)) * 0.004
    exp_vert = 36.5 - 9.0 * heavy
    exp_broad = 124.0 - 18.0 * heavy
    exp_cone = 6.9 + 0.8 * heavy
    exp_bench = 12.0 + 20.0 * heavy

    out: Dict[str, Any] = {
        "participated": comb_ev is not None,
        "height": inches_height(height_in) if height_in else None,
        "height_in": height_in,
        "weight": weight,
        "pro_day_result": (pro_ev.get("payload") or {}).get("result") if pro_ev else None,
        "pro_day_blurb": pro_ev.get("blurb") if pro_ev else None,
    }
    if not comb_ev:
        return out

    pay = sr.public_payload(comb_ev.get("payload") or {})
    m = pay.get("measurables") or {}
    forty = m.get("forty")
    vert = m.get("vertical")
    broad = m.get("broad_jump")
    cone = m.get("three_cone")
    bench = m.get("bench")

    drills = []
    pcts = []
    if forty is not None:
        p = _norm_cdf_pct((exp_forty - float(forty)) / 0.06)
        drills.append({"key": "forty", "label": "40-Yard Dash", "value": f"{forty:.2f}s", "raw": forty, "percentile": p})
        pcts.append(p)
    if vert is not None:
        p = _norm_cdf_pct((float(vert) - exp_vert) / 2.2)
        drills.append({"key": "vertical", "label": "Vertical Jump", "value": f"{vert}\"", "raw": vert, "percentile": p})
        pcts.append(p)
    if broad is not None:
        p = _norm_cdf_pct((float(broad) - exp_broad) / 4.5)
        ft, inc = int(broad) // 12, int(broad) % 12
        drills.append({"key": "broad_jump", "label": "Broad Jump", "value": f"{ft}'{inc}\" ({broad}\")", "raw": broad, "percentile": p})
        pcts.append(p)
    if cone is not None:
        p = _norm_cdf_pct((exp_cone - float(cone)) / 0.10)
        drills.append({"key": "three_cone", "label": "3-Cone Drill", "value": f"{cone:.2f}s", "raw": cone, "percentile": p})
        pcts.append(p)
    if bench is not None:
        p = _norm_cdf_pct((float(bench) - exp_bench) / 3.5)
        drills.append({"key": "bench", "label": "Bench Press (225 lb)", "value": f"{bench} reps", "raw": bench, "percentile": p})
        pcts.append(p)

    athletic_score = round(sum(pcts) / len(pcts) / 10.0, 2) if pcts else None
    out.update({
        "measurables": m,
        "drills": drills,
        "athletic_score": athletic_score,
        "interviews": pay.get("interviews"),
        "medical": pay.get("medical"),
        "headline": comb_ev.get("headline"),
        "blurb": comb_ev.get("blurb"),
    })
    return out


def board_stat_summaries(cur, draft_season: int, keys: List[str], through_step: Optional[int] = None) -> Dict[str, Dict[str, Any]]:
    """Batch-compute season stat summaries (`{games, summary_line, totals, stat_group}`) for a list of prospect keys."""
    if not keys:
        return {}
    cur.execute("SELECT prospect_key, d.position FROM scout_prospects sp LEFT JOIN draft_prospects d ON d.id = sp.prospect_id WHERE sp.draft_season = %s AND sp.prospect_key = ANY(%s)",
                (draft_season, list(keys)))
    pos_by_key = {k: pos for k, pos in cur.fetchall()}

    sql = """SELECT prospect_key, step, headline, blurb, payload
             FROM scout_events
             WHERE draft_season = %s AND kind = 'GAME' AND prospect_key = ANY(%s)"""
    args: List[Any] = [draft_season, list(keys)]
    if through_step is not None:
        sql += " AND step <= %s"
        args.append(through_step)
    sql += " ORDER BY step"
    cur.execute(sql, args)

    evs_by_key: Dict[str, List[Dict[str, Any]]] = {k: [] for k in keys}
    for k, st, head, blurb, payload in cur.fetchall():
        evs_by_key.setdefault(k, []).append({
            "step": st, "kind": "GAME", "headline": head, "blurb": blurb, "payload": sr.public_payload(payload)
        })

    out: Dict[str, Dict[str, Any]] = {}
    for k in keys:
        pos = pos_by_key.get(k) or k.split(":")[-1]
        agg = aggregate_games(evs_by_key.get(k, []), pos)
        out[k] = {
            "stat_group": agg["stat_group"],
            "games": agg["games"],
            "totals": agg["totals"],
            "summary_line": agg["summary_line"],
        }
    return out


def prospect_dossier(cur, prospect_key: str, through_step: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Build the complete public Scouting Dossier for a prospect."""
    if not sr.installed(cur):
        return None
    story = sr.prospect_story(cur, prospect_key, through_step=through_step)
    board_rows = story.get("board") or []
    if not board_rows:
        return None

    latest = board_rows[-1]
    first = board_rows[0]
    peak_rank = min((r["rank"] for r in board_rows if r.get("rank") is not None), default=latest["rank"])
    peak_grade = max((r["grade"] for r in board_rows if r.get("grade") is not None), default=latest["grade"])

    sch = sv.school(latest.get("college"))
    pos = latest.get("position")
    pos_group = sv.group_of(pos)
    pos_words = sev.pos_words(pos)

    events = story.get("events") or []
    stats_block = aggregate_games(events, pos)
    combine_block = combine_profile(events, pos, latest.get("height_in"), latest.get("weight"))

    timeline = []
    for e in reversed(events):
        st = int(e.get("step") or 1)
        timeline.append({
            "step": st,
            "step_label": step_label(st),
            "phase": sev.phase(st),
            "kind": e.get("kind"),
            "team": e.get("team"),
            "headline": e.get("headline"),
            "blurb": e.get("blurb"),
            "payload": e.get("payload"),
        })

    grade_history = [
        {
            "step": r["step"],
            "step_label": step_label(int(r["step"])),
            "week": r["week"],
            "stage": r["stage"],
            "rank": r["rank"],
            "grade": r["grade"],
            "round_label": r["round_label"],
            "delta_rank": r["delta_rank"],
            "delta_grade": r["delta_grade"],
        }
        for r in board_rows
    ]

    return {
        "prospect_key": prospect_key,
        "draft_season": latest["draft_season"],
        "profile": {
            "full_name": latest["full_name"],
            "position": pos,
            "position_group": pos_group,
            "position_words": pos_words,
            "college": latest["college"],
            "school_display": sch["display"] if sch else (latest["college"] or "").title(),
            "nickname": sch["nickname"] if sch else None,
            "conference": latest["conference"] or (sch["conference"] if sch else None),
            "level": sch["level"] if sch else None,
            "height": inches_height(latest["height_in"]) if latest.get("height_in") else None,
            "height_in": latest["height_in"],
            "weight": latest["weight"],
            "age": latest["age"],
            "class_year": latest["class_year"],
            "status": latest["status"],
            "rank": latest["rank"],
            "grade": latest["grade"],
            "round_label": latest["round_label"],
            "delta_rank": latest["delta_rank"],
            "delta_grade": latest["delta_grade"],
            "start_rank": first["rank"],
            "start_grade": first["grade"],
            "net_rank_change": (first["rank"] - latest["rank"]) if (first.get("rank") and latest.get("rank")) else 0,
            "peak_rank": peak_rank,
            "peak_grade": peak_grade,
            "top_headline": latest.get("top_headline"),
        },
        "season_stats": {
            "stat_group": stats_block["stat_group"],
            "stat_keys": stats_block["stat_keys"],
            "games": stats_block["games"],
            "totals": stats_block["totals"],
            "summary_line": stats_block["summary_line"],
        },
        "game_log": stats_block["game_log"],
        "combine": combine_block,
        "grade_history": grade_history,
        "timeline": timeline,
    }


def compile_awards_and_honors(cur, draft_season: int, step: Optional[int] = None) -> Dict[str, Any]:
    """Compile National Player of the Year (Heisman Watch) and positional college awards finalists & watchlists."""
    if not sr.installed(cur):
        return {"draft_season": draft_season, "step": None, "heisman_watch": [], "awards": []}

    steps = sr._steps(cur, draft_season)
    if not steps:
        return {"draft_season": draft_season, "step": None, "heisman_watch": [], "awards": []}
    active_step = step if step is not None else steps[-1][0]

    board = sr.consensus_board(cur, draft_season, step=active_step, limit=250)
    by_key = {r["prospect_key"]: r for r in board}

    cur.execute("""SELECT prospect_key, step, kind, headline, blurb, payload
                   FROM scout_events
                   WHERE draft_season = %s AND step <= %s
                     AND (kind = 'AWARD' OR (kind = 'STORYLINE' AND payload->>'subtype' = 'award_watch'))
                   ORDER BY step DESC, id""", (draft_season, active_step))
    award_rows = cur.fetchall()

    all_keys = list(set([r["prospect_key"] for r in board[:60]] + [r[0] for r in award_rows]))
    summaries = board_stat_summaries(cur, draft_season, all_keys, through_step=active_step)

    awards_map: Dict[str, Dict[str, Any]] = {}
    for pos_group, aw_name in sev.AWARDS.items():
        if aw_name not in awards_map:
            awards_map[aw_name] = {
                "award": aw_name,
                "positions": [],
                "finalists": [],
                "watchlist": [],
            }
        if pos_group not in awards_map[aw_name]["positions"]:
            awards_map[aw_name]["positions"].append(pos_group)

    seen_finalists: set = set()
    seen_watch: set = set()

    for pkey, st, kind, head, blurb, payload in award_rows:
        pay = sr.public_payload(payload)
        p_info = by_key.get(pkey)
        if not p_info:
            continue
        aw_name = pay.get("award") or sev.AWARDS.get(sev.stat_group(p_info.get("position")), "National Award")
        entry = {
            **p_info,
            "step": st,
            "headline": head,
            "blurb": blurb,
            "stat_summary": summaries.get(pkey, {}).get("summary_line", ""),
            "season_totals": summaries.get(pkey, {}).get("totals", {}),
        }
        bucket = awards_map.setdefault(aw_name, {"award": aw_name, "positions": [], "finalists": [], "watchlist": []})
        if kind == "AWARD" and (aw_name, pkey) not in seen_finalists:
            seen_finalists.add((aw_name, pkey))
            bucket["finalists"].append(entry)
        elif kind == "STORYLINE" and (aw_name, pkey) not in seen_watch and (aw_name, pkey) not in seen_finalists:
            seen_watch.add((aw_name, pkey))
            bucket["watchlist"].append(entry)

    # Populate top positional contenders for any award whose finalists haven't been announced yet (steps < 15)
    for aw_name, bucket in awards_map.items():
        pos_set = set(bucket["positions"])
        if not bucket["finalists"] and not bucket["watchlist"]:
            candidates = [r for r in board if sev.stat_group(r.get("position")) in pos_set][:4]
            for c in candidates:
                pkey = c["prospect_key"]
                bucket["watchlist"].append({
                    **c,
                    "step": active_step,
                    "headline": f"{c['full_name'].title()} projecting as a {aw_name} contender",
                    "blurb": "",
                    "stat_summary": summaries.get(pkey, {}).get("summary_line", ""),
                    "season_totals": summaries.get(pkey, {}).get("totals", {}),
                })

    # National Player of the Year / Heisman Watch: top QBs, RBs, WRs, EDGEs, CBs weighted by grade + finalist boost
    heisman_groups = {"QB", "RB", "WR", "EDGE", "CB"}
    heisman_pool = [r for r in board[:50] if sev.stat_group(r.get("position")) in heisman_groups]
    finalist_keys = {k for _, k in seen_finalists}
    watch_keys = {k for _, k in seen_watch}

    def _heisman_score(r: Dict[str, Any]) -> float:
        pkey = r["prospect_key"]
        grp = sev.stat_group(r.get("position"))
        pos_w = {"QB": 3.5, "RB": 2.0, "WR": 2.0, "EDGE": 1.2, "CB": 1.0}.get(grp, 0.0)
        fin_w = 2.5 if pkey in finalist_keys else (1.0 if pkey in watch_keys else 0.0)
        return float(r.get("grade") or 60.0) + pos_w + fin_w

    heisman_sorted = sorted(heisman_pool, key=_heisman_score, reverse=True)[:8]
    heisman_watch = []
    for idx, r in enumerate(heisman_sorted, 1):
        pkey = r["prospect_key"]
        heisman_watch.append({
            "heisman_rank": idx,
            **r,
            "is_award_finalist": pkey in finalist_keys,
            "stat_summary": summaries.get(pkey, {}).get("summary_line", ""),
            "season_totals": summaries.get(pkey, {}).get("totals", {}),
        })

    return {
        "draft_season": draft_season,
        "step": active_step,
        "step_label": step_label(active_step),
        "heisman_watch": heisman_watch,
        "awards": list(awards_map.values()),
    }
