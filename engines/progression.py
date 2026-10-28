"""
Player Progression Engine (docs/PROGRESSION_PLAN.md §5; PROJECT_SPEC §2.20; PRE_PC_WORK.md §11).

Decides each player's yearly OVR change and spreads it over individual ratings offline, backtested against
what Madden 27 did on the 2035->2036 offseason.

Two Layers (§5.1):
1. OVR Change Layer (§5.2):
   ΔOVR_i = age(group, age)               # aging curve by position group (trade_value.py fit)
          + dev(dev_trait, age)           # young players with Star/Superstar/X-Factor grow faster
          + pull(ceiling_i - OVR_i)       # room to grow toward hidden ceiling (scout_prospects or seeded)
          + play(starts / games played)   # performance and playing time proxy (snaps/starts)
          + injury(games missed, IR)      # missed time and physical setbacks
          + ε_i                           # deterministic seeded noise: Random(f"prog:{season}:{player_id}").gauss(0, 1.0)
   Clamped per age band and OVR band. Each term stored in breakdown dict for newsroom citations.

2. Ratings Layer (§5.4):
   - Physical ratings (SPD, ACC, STR, AGI, COD, JMP, STA) follow age decline curves and injury setbacks.
   - Skill and mental ratings (AWR, PRC, accuracy, coverage, blocking) develop later and improve with experience.
   - Adjusts archetype 'skills' via engines.ovr_weights.adjust_ratings to reach target OVR, verifying
     the archetype remains on top with settle_archetype / predict_ovr.

CLI & Modes:
  python3 -m engines.progression --season 2037 [--commit] [--dry-run]
  python3 -m engines.progression --backtest [--season 2036]
  python3 -m engines.progression --simulate 5 [--season 2036]
"""

import argparse
import json
import math
import os
import random
import sys
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

import psycopg

from engines import ovr_weights as ow
from engines.dev_quality import (
    dev_rank, fetch_window, fit_expected, expected_delta, starter_flags, ovr_band, OVR_BANDS
)
from engines.gm_direction import position_group
from engines.trade_value import read_fit_rows, fit_aging, age_band, AGE_BANDS
from pipeline.load_save import POSITIONS
from pipeline.teleport import TO_MADDEN_POSITION, madden_archetype, settle_archetype

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")

# Physical ratings tracked for age and injury adjustments
PHYSICAL_RATINGS = ("Speed", "Acceleration", "Agility", "Strength", "Jumping", "Stamina", "ChangeOfDirection")

# Position groups that decline physically earlier (CB, RB, WR peak and fade earlier in Madden)
EARLY_DECLINE_GROUPS = {"CB", "RB", "WR"}

# Clamping bounds: (min_delta, max_delta) by age band and OVR band
OVR_MAX = 99
OVR_MIN = 40
MAX_SEASON_CHANGE = 8


# ------------------------------------------------------------------ ceilings ----
def seed_player_ceiling(
    player_id: int,
    ovr: int,
    age: int,
    dev_trait: Optional[str] = None,
    prospect_true_value: Optional[float] = None
) -> Tuple[float, str]:
    """Determines hidden ceiling for player. If draftee with scout_prospects.true_value, uses that.
    Otherwise seeds deterministically from player_id, age, OVR, and dev trait.
    Ceilings are strictly hidden and never exposed in views, writer tools or #debug."""
    if prospect_true_value is not None and prospect_true_value > 0:
        return float(prospect_true_value), "SCOUT_PROSPECT"

    dr = dev_rank(dev_trait) or 0
    c_rng = random.Random(f"ceiling:{player_id}")

    # Base headroom above current OVR depending on dev trait and age
    headroom_base = {0: 5.0, 1: 9.0, 2: 13.0, 3: 16.0}[dr]
    if age <= 24:
        hr = headroom_base + c_rng.uniform(-1.0, 2.0)
    elif age <= 28:
        hr = max(1.0, headroom_base * 0.7 + c_rng.uniform(-1.0, 1.0))
    else:
        hr = max(0.0, 1.5 + c_rng.uniform(-0.5, 0.5))

    ceiling = min(float(OVR_MAX), float(ovr) + hr)
    return round(ceiling, 2), "SEEDED"


# ------------------------------------------------------------------ OVR math ----
def compute_player_delta(
    player_id: int,
    position: str,
    age: int,
    ovr: int,
    dev_trait: Optional[str],
    ceiling: float,
    games_played: Optional[int],
    downs_played: Optional[int],
    is_starter: bool,
    is_ir: bool,
    injury_weeks: Optional[int],
    season: int,
    aging_curves: Dict[str, Any]
) -> Tuple[int, int, Dict[str, float], str]:
    """Calculates yearly OVR delta and breakdown of individual causal terms (§5.2).
    Returns (delta_ovr, new_ovr, terms_breakdown, cause_tag)."""
    group = position_group(position)
    band = age_band(age)
    curve = aging_curves.get(group, aging_curves.get("generic", [0.0] * len(AGE_BANDS)))
    age_t = curve[band] if band < len(curve) else curve[-1]

    dr = dev_rank(dev_trait) or 0

    # Dev trait term: young players with high dev grow faster; elite vets resist decline
    if age <= 24:
        dev_t = [0.0, 0.50, 1.00, 1.50][dr]
    elif age <= 26:
        dev_t = [0.0, 0.30, 0.65, 1.00][dr]
    elif age <= 28:
        dev_t = [0.0, 0.15, 0.35, 0.60][dr]
    else:
        dev_t = [0.0, 0.20, 0.40, 0.70][dr]

    # Ceiling pull: room to grow toward ceiling, shrinking toward 0 near it
    gap = ceiling - ovr
    ceil_t = 0.09 * gap if gap > 0 else 0.0

    # Playing time proxy: full-time starters gain form, benched players stall
    if games_played is not None:
        if games_played >= 15 or (downs_played and downs_played >= 700):
            play_t = 0.35
        elif games_played >= 11 or (downs_played and downs_played >= 400):
            play_t = 0.15
        elif games_played >= 6:
            play_t = -0.05
        else:
            play_t = -0.25
    elif is_starter:
        play_t = 0.30
    else:
        play_t = -0.10

    # Injury term: missed time and setbacks
    inj_t = 0.0
    if is_ir:
        inj_t = -0.80
    elif injury_weeks and injury_weeks >= 4:
        inj_t = -0.45
    elif injury_weeks and injury_weeks >= 2:
        inj_t = -0.20

    # Seeded noise: strictly deterministic per player and season
    noise_rng = random.Random(f"prog:{season}:{player_id}")
    noise_t = noise_rng.gauss(0.0, 1.0)

    # Offset to balance baseline against empirical Madden 2035->2036 mean (-0.15 OVR)
    baseline_offset = -0.65
    raw_delta = age_t + dev_t + ceil_t + play_t + inj_t + noise_t + baseline_offset

    # Clamping per age band and OVR band
    if ovr >= 99:
        raw_delta = min(0.0, raw_delta)
    elif ovr >= 95:
        raw_delta = min(1.0, max(-6.0, raw_delta))
    elif age >= 32:
        raw_delta = min(1.0, max(-8.0, raw_delta))
    elif age >= 29:
        raw_delta = min(2.0, max(-7.0, raw_delta))
    elif age <= 24:
        raw_delta = min(8.0, max(-4.0, raw_delta))
    else:
        raw_delta = min(6.0, max(-6.0, raw_delta))

    # Overall max change clamp
    raw_delta = min(float(MAX_SEASON_CHANGE), max(float(-MAX_SEASON_CHANGE), raw_delta))

    new_ovr = max(OVR_MIN, min(OVR_MAX, int(round(ovr + raw_delta))))
    final_delta = new_ovr - ovr

    # Assign narrative cause tag (§5.6)
    if is_ir or (inj_t <= -0.4 and final_delta < 0):
        cause = "INJURY"
    elif final_delta >= 3 and (dev_t + ceil_t >= 1.5):
        cause = "BREAKOUT"
    elif ceil_t >= 0.75 and final_delta > 0:
        cause = "CEILING"
    elif abs(play_t) >= 0.3 and abs(play_t) >= abs(age_t):
        cause = "PLAYING_TIME"
    elif age >= 30 and final_delta < 0:
        cause = "AGE"
    elif final_delta > 0:
        cause = "DEVELOPED"
    elif final_delta < 0:
        cause = "REGRESSED"
    else:
        cause = "ON_TRACK" if age < 30 else "STALLED"

    breakdown = {
        "age": round(age_t, 2),
        "dev": round(dev_t, 2),
        "ceiling": round(ceil_t, 2),
        "play": round(play_t, 2),
        "injury": round(inj_t, 2),
        "noise": round(noise_t, 2),
    }

    return final_delta, new_ovr, breakdown, cause


# ---------------------------------------------------------------- ratings layer ----
def apply_ratings_progression(
    doc: Dict[str, Any],
    position: str,
    archetype: Optional[str],
    ratings: Dict[str, int],
    age: int,
    target_ovr: int,
    injury_severity: int = 0
) -> Tuple[Dict[str, int], Dict[str, int], str, bool]:
    """Applies age and injury adjustments to physical ratings, then uses ovr_weights.adjust_ratings
    to move archetype skill ratings to hit target_ovr, verifying archetype stays on top (§5.4).
    Returns (new_ratings, ratings_diff, archetype_name, is_settled)."""
    madden_pos = TO_MADDEN_POSITION.get(position, position)
    arch = madden_archetype(doc, madden_pos, archetype) if archetype else None
    if not arch or arch not in doc["archetypes"]:
        # Fall back to current top archetype from ratings
        _, arch = ow.predict_ovr(doc, madden_pos, ratings)

    updated = dict(ratings)
    original = dict(ratings)
    group = position_group(position)

    # 1. Physical ratings aging & injury adjustments
    if age >= 29:
        drop = 1 if age in (29, 30) else 2
        for pr in ("Speed", "Agility"):
            if pr in updated:
                updated[pr] = max(25, updated[pr] - (drop if group in EARLY_DECLINE_GROUPS else max(1, drop - 1)))
        if age >= 31:
            for pr in ("Acceleration", "Jumping", "ChangeOfDirection"):
                if pr in updated:
                    updated[pr] = max(25, updated[pr] - 1)
    elif age <= 24:
        # Modest physical growth for young players
        for pr in ("Speed", "Strength"):
            if pr in updated and updated[pr] < 88:
                updated[pr] = min(99, updated[pr] + 1)

    if injury_severity > 0:
        for pr in ("Speed", "Acceleration"):
            if pr in updated:
                updated[pr] = max(25, updated[pr] - injury_severity)

    # 2. Adjust archetype skills to reach target_ovr
    try:
        updated, _ = ow.adjust_ratings(doc, arch, updated, target_ovr)
    except ValueError:
        # If target cannot be fully reached, keep best effort
        pass

    # 3. Settle archetype so player's primary archetype remains on top
    settled_ratings, settled_ok = settle_archetype(doc, madden_pos, arch, updated, target_ovr)

    ratings_diff = {
        k: settled_ratings[k] - original.get(k, settled_ratings[k])
        for k in settled_ratings
        if settled_ratings[k] != original.get(k, settled_ratings[k])
    }

    return settled_ratings, ratings_diff, arch, settled_ok


# ---------------------------------------------------------------- DB helpers ----
def fetch_progression_roster(cur, season: int) -> List[Dict[str, Any]]:
    """Loads player roster state from season-1 (or current season if preseason scan)
    along with stats, injury, and ceiling inputs."""
    cur.execute("""
        SELECT
            ps.player_id,
            COALESCE(p.full_name, p.player_name) as name,
            ps.position,
            ps.team_id,
            ps.age,
            ps.overall_rating,
            ps.dev_trait,
            ps.archetype,
            sg.games_played,
            sg.downs_played,
            COALESCE(inj.is_ir, false) as is_ir,
            COALESCE(inj.weeks_remaining, 0) as weeks_remaining,
            sp.true_value as prospect_true_val,
            pc.ceiling as stored_ceiling
        FROM player_seasons ps
        JOIN players p ON p.id = ps.player_id
        LEFT JOIN season_stat_general sg ON sg.player_id = ps.player_id AND sg.season = ps.season
        LEFT JOIN (
            SELECT player_id, bool_or(is_ir) as is_ir, max(weeks_remaining) as weeks_remaining
            FROM injuries WHERE season = %s GROUP BY player_id
        ) inj ON inj.player_id = ps.player_id
        LEFT JOIN scout_prospects sp ON sp.prospect_id = (
            SELECT id FROM draft_prospects dp WHERE dp.player_id = ps.player_id LIMIT 1
        )
        LEFT JOIN player_ceilings pc ON pc.player_id = ps.player_id
        WHERE ps.season = %s
          AND ps.overall_rating IS NOT NULL
          AND ps.position != 'DEF'
    """, (season - 1, season - 1))

    cols = (
        "player_id", "name", "position", "team_id", "age", "overall_rating",
        "dev_trait", "archetype", "games_played", "downs_played",
        "is_ir", "weeks_remaining", "prospect_true_val", "stored_ceiling"
    )
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    # Starter flags based on top 22 start OVRs per team
    s_input = [{"player_id": r["player_id"], "team_id": r["team_id"], "ovr_start": r["overall_rating"]} for r in rows]
    starters = starter_flags(s_input)
    for r in rows:
        r["is_starter"] = starters.get(r["player_id"], False)

    return rows


def fetch_player_attributes_map(cur, season: int) -> Dict[int, Dict[str, int]]:
    """Loads player ratings from player_attributes for the season."""
    cur.execute("""
        SELECT DISTINCT ON (player_id) player_id, ratings
        FROM player_attributes
        WHERE season <= %s
        ORDER BY player_id, season DESC, id DESC
    """, (season,))
    return {r[0]: r[1] for r in cur.fetchall()}


# ---------------------------------------------------------------- execution ----
def run_progression(
    conn,
    season: int,
    commit: bool = False,
    dry_run: bool = False,
    log=print
) -> Dict[str, Any]:
    """Runs progression for all players into target season, storing results in
    player_progression_history and updating player_ceilings."""
    with conn.cursor() as cur:
        _, aging_pairs, _ = read_fit_rows(cur, season - 1 if season > 2036 else 2036)
        aging_curves = fit_aging(aging_pairs)
        roster = fetch_progression_roster(cur, season)
        weights_doc = ow.load_weights()
        attr_map = fetch_player_attributes_map(cur, season)

        log(f"Running progression for Season {season}: {len(roster)} eligible players found")
        results = []
        counts_by_cause = defaultdict(int)
        deltas = []

        for p in roster:
            p_id = p["player_id"]
            ovr = p["overall_rating"]
            age = p["age"] + 1  # Player advances 1 year in offseason

            # Seed or fetch ceiling
            if p["stored_ceiling"] is not None:
                ceiling = float(p["stored_ceiling"])
                c_source = "STORED"
            else:
                ceiling, c_source = seed_player_ceiling(
                    p_id, ovr, age, p["dev_trait"], p["prospect_true_val"]
                )

            delta, new_ovr, breakdown, cause = compute_player_delta(
                player_id=p_id,
                position=p["position"],
                age=age,
                ovr=ovr,
                dev_trait=p["dev_trait"],
                ceiling=ceiling,
                games_played=p["games_played"],
                downs_played=p["downs_played"],
                is_starter=p["is_starter"],
                is_ir=p["is_ir"],
                injury_weeks=p["weeks_remaining"],
                season=season,
                aging_curves=aging_curves,
            )

            ratings_diff = {}
            if p_id in attr_map:
                try:
                    _, ratings_diff, _, _ = apply_ratings_progression(
                        doc=weights_doc,
                        position=p["position"],
                        archetype=p["archetype"],
                        ratings=attr_map[p_id],
                        age=age,
                        target_ovr=new_ovr,
                        injury_severity=1 if p["is_ir"] else 0
                    )
                except Exception:
                    ratings_diff = {}

            results.append({
                "season": season,
                "player_id": p_id,
                "old_ovr": ovr,
                "new_ovr": new_ovr,
                "delta_ovr": delta,
                "age_term": breakdown["age"],
                "dev_term": breakdown["dev"],
                "ceiling_term": breakdown["ceiling"],
                "play_term": breakdown["play"],
                "injury_term": breakdown["injury"],
                "noise_term": breakdown["noise"],
                "ratings_diff": ratings_diff,
                "ceiling": ceiling,
                "ceiling_source": c_source,
                "cause": cause,
            })
            counts_by_cause[cause] += 1
            deltas.append(delta)

        avg_delta = sum(deltas) / len(deltas) if deltas else 0.0
        log(f"Progression calculated: mean ΔOVR={avg_delta:+.2f}, min={min(deltas, default=0)}, max={max(deltas, default=0)}")
        log("Causes breakdown: " + ", ".join(f"{k}: {v}" for k, v in sorted(counts_by_cause.items())))

        if commit and not dry_run:
            log(f"Writing {len(results)} rows to player_progression_history and updating player_ceilings...")
            for r in results:
                cur.execute("""
                    INSERT INTO player_progression_history (
                        season, player_id, old_ovr, new_ovr, delta_ovr,
                        age_term, dev_term, ceiling_term, play_term, injury_term, noise_term,
                        ratings_diff, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    ON CONFLICT (season, player_id) DO UPDATE SET
                        old_ovr = EXCLUDED.old_ovr,
                        new_ovr = EXCLUDED.new_ovr,
                        delta_ovr = EXCLUDED.delta_ovr,
                        age_term = EXCLUDED.age_term,
                        dev_term = EXCLUDED.dev_term,
                        ceiling_term = EXCLUDED.ceiling_term,
                        play_term = EXCLUDED.play_term,
                        injury_term = EXCLUDED.injury_term,
                        noise_term = EXCLUDED.noise_term,
                        ratings_diff = EXCLUDED.ratings_diff,
                        created_at = NOW();
                """, (
                    r["season"], r["player_id"], r["old_ovr"], r["new_ovr"], r["delta_ovr"],
                    r["age_term"], r["dev_term"], r["ceiling_term"], r["play_term"], r["injury_term"], r["noise_term"],
                    json.dumps(r["ratings_diff"])
                ))
                cur.execute("""
                    INSERT INTO player_ceilings (player_id, ceiling, source, updated_season, created_at)
                    VALUES (%s, %s, %s, %s, NOW())
                    ON CONFLICT (player_id) DO UPDATE SET
                        ceiling = EXCLUDED.ceiling,
                        source = EXCLUDED.source,
                        updated_season = EXCLUDED.updated_season,
                        created_at = NOW();
                """, (r["player_id"], r["ceiling"], r["ceiling_source"], season))
            conn.commit()
            log("Commit successful.")
        else:
            log("Dry run complete (no database modifications).")

        return {
            "season": season,
            "players_count": len(results),
            "mean_delta": round(avg_delta, 3),
            "causes": dict(counts_by_cause),
            "results": results
        }


# ---------------------------------------------------------------- backtest ----
def run_backtest(conn, season: int = 2036, log=print) -> Dict[str, Any]:
    """Evaluates predicted vs actual ΔOVR on the 2035->2036 offseason.
    Reports error by age band, OVR band, and position group, and compares against dev_quality."""
    with conn.cursor() as cur:
        _, aging_pairs, _ = read_fit_rows(cur, season)
        aging_curves = fit_aging(aging_pairs)

        # Query 2035 -> 2036 actual pairs
        cur.execute("""
            SELECT
                ps35.player_id,
                ps35.position,
                ps35.age,
                ps35.overall_rating as ovr35,
                ps36.overall_rating as ovr36,
                ps35.dev_trait,
                sg.games_played,
                sg.downs_played,
                COALESCE(inj.is_ir, false) as is_ir,
                COALESCE(inj.weeks_remaining, 0) as weeks_remaining
            FROM player_seasons ps35
            JOIN player_seasons ps36 ON ps36.player_id = ps35.player_id AND ps36.season = %s
            LEFT JOIN season_stat_general sg ON sg.player_id = ps35.player_id AND sg.season = %s
            LEFT JOIN (
                SELECT player_id, bool_or(is_ir) as is_ir, max(weeks_remaining) as weeks_remaining
                FROM injuries WHERE season = %s GROUP BY player_id
            ) inj ON inj.player_id = ps35.player_id
            WHERE ps35.season = %s
              AND ps35.overall_rating IS NOT NULL
              AND ps36.overall_rating IS NOT NULL
              AND ps35.position != 'DEF'
        """, (season, season - 1, season - 1, season - 1))

        rows = cur.fetchall()
        # dev_quality comparison fit
        dev_windows = fetch_window(cur, season)

    log(f"--- Progression Backtest ({season - 1} -> {season}) ---")
    log(f"Sample size: {len(rows)} players")

    beta, sds = fit_expected(dev_windows)

    preds, actuals, dev_exps = [], [], []
    by_age_band = defaultdict(list)
    by_ovr_band = defaultdict(list)
    by_pos_group = defaultdict(list)

    for r in rows:
        p_id, pos, age, o35, o36, dev, gp, downs, is_ir, inj_w = r
        ceiling, _ = seed_player_ceiling(p_id, o35, age, dev)
        d_pred, _, _, _ = compute_player_delta(
            player_id=p_id,
            position=pos,
            age=age,
            ovr=o35,
            dev_trait=dev,
            ceiling=ceiling,
            games_played=gp,
            downs_played=downs,
            is_starter=(gp and gp >= 12),
            is_ir=is_ir,
            injury_weeks=inj_w,
            season=season,
            aging_curves=aging_curves
        )
        d_act = o36 - o35
        preds.append(d_pred)
        actuals.append(d_act)

        # Match dev_quality expected delta
        w_sample = {
            "age": age, "ovr_start": o35, "dev_start": dev, "rookie": False
        }
        dev_exp = expected_delta(beta, w_sample)
        dev_exps.append(dev_exp)

        err = d_pred - d_act
        band_a = age_band(age)
        band_o = ovr_band(o35)
        grp = position_group(pos) or "OTHER"

        by_age_band[band_a].append((d_pred, d_act, err))
        by_ovr_band[band_o].append((d_pred, d_act, err))
        by_pos_group[grp].append((d_pred, d_act, err))

    mean_pred = sum(preds) / len(preds)
    mean_act = sum(actuals) / len(actuals)
    mean_dev_exp = sum(dev_exps) / len(dev_exps)
    mae = sum(abs(p - a) for p, a in zip(preds, actuals)) / len(preds)
    rmse = math.sqrt(sum((p - a) ** 2 for p, a in zip(preds, actuals)) / len(preds))
    exact = sum(p == a for p, a in zip(preds, actuals)) / len(preds)
    within_1 = sum(abs(p - a) <= 1 for p, a in zip(preds, actuals)) / len(preds)

    log(f"League Mean ΔOVR: predicted={mean_pred:+.3f}, actual={mean_act:+.3f}, dev_quality fit={mean_dev_exp:+.3f}")
    log(f"Accuracy: MAE={mae:.3f}, RMSE={rmse:.3f}, Exact={exact:.1%}, Within ±1={within_1:.1%}")

    log("\nBreakdown by Age Band:")
    age_report = {}
    for b in sorted(by_age_band):
        vals = by_age_band[b]
        mp = sum(x[0] for x in vals) / len(vals)
        ma = sum(x[1] for x in vals) / len(vals)
        me = sum(x[2] for x in vals) / len(vals)
        lbl = f"Band {b} ({AGE_BANDS[b][0]}-{AGE_BANDS[b][1]})"
        log(f"  {lbl:16}: n={len(vals):3} pred={mp:+.2f} act={ma:+.2f} err={me:+.2f}")
        age_report[lbl] = {"n": len(vals), "pred": round(mp, 2), "act": round(ma, 2), "err": round(me, 2)}

    log("\nBreakdown by OVR Band:")
    band_names = ["<70", "70-79", "80-84", "85-89", "90-94", "95+"]
    ovr_report = {}
    for b in sorted(by_ovr_band):
        vals = by_ovr_band[b]
        mp = sum(x[0] for x in vals) / len(vals)
        ma = sum(x[1] for x in vals) / len(vals)
        me = sum(x[2] for x in vals) / len(vals)
        lbl = band_names[b]
        log(f"  {lbl:16}: n={len(vals):3} pred={mp:+.2f} act={ma:+.2f} err={me:+.2f}")
        ovr_report[lbl] = {"n": len(vals), "pred": round(mp, 2), "act": round(ma, 2), "err": round(me, 2)}

    log("\nBreakdown by Position Group:")
    group_report = {}
    for g in sorted(by_pos_group):
        vals = by_pos_group[g]
        mp = sum(x[0] for x in vals) / len(vals)
        ma = sum(x[1] for x in vals) / len(vals)
        me = sum(x[2] for x in vals) / len(vals)
        log(f"  {g:16}: n={len(vals):3} pred={mp:+.2f} act={ma:+.2f} err={me:+.2f}")
        group_report[g] = {"n": len(vals), "pred": round(mp, 2), "act": round(ma, 2), "err": round(me, 2)}

    return {
        "sample_size": len(rows),
        "mean_predicted": round(mean_pred, 3),
        "mean_actual": round(mean_act, 3),
        "dev_quality_mean": round(mean_dev_exp, 3),
        "mae": round(mae, 3),
        "rmse": round(rmse, 3),
        "exact_pct": round(exact, 3),
        "within_1_pct": round(within_1, 3),
        "age_bands": age_report,
        "ovr_bands": ovr_report,
        "position_groups": group_report,
    }


# ---------------------------------------------------------------- simulation ----
def run_simulation(
    conn,
    start_season: int = 2036,
    num_seasons: int = 5,
    log=print
) -> Dict[str, Any]:
    """Simulates N seasons ahead on a copy of league rosters to verify league stability bounds (§5.5).
    Tracks count of 90+ players, average starter OVR by position, average OVR, and age mix."""
    with conn.cursor() as cur:
        _, aging_pairs, _ = read_fit_rows(cur, start_season)
        aging_curves = fit_aging(aging_pairs)

        cur.execute("""
            SELECT player_id, team_id, position, age, overall_rating, dev_trait
            FROM player_seasons
            WHERE season = %s
              AND overall_rating IS NOT NULL
              AND position != 'DEF'
        """, (start_season,))
        current_roster = [
            {
                "player_id": r[0], "team_id": r[1], "position": r[2], "age": r[3],
                "ovr": r[4], "dev": r[5] or "Normal", "gp": 16
            }
            for r in cur.fetchall()
        ]

    log(f"--- League Stability Simulation ({num_seasons} seasons from {start_season}) ---")
    baseline_n = len(current_roster)
    baseline_90 = sum(1 for p in current_roster if p["ovr"] >= 90)
    baseline_ovr = sum(p["ovr"] for p in current_roster) / baseline_n
    baseline_age = sum(p["age"] for p in current_roster) / baseline_n

    s_rows = [{"player_id": p["player_id"], "team_id": p["team_id"], "ovr_start": p["ovr"]} for p in current_roster]
    st = starter_flags(s_rows)
    starters = [p for p in current_roster if st.get(p["player_id"])]
    baseline_starter = sum(p["ovr"] for p in starters) / len(starters) if starters else 0.0

    log(f"Baseline {start_season}: Players={baseline_n}, 90+={baseline_90}, Avg OVR={baseline_ovr:.2f}, Avg Age={baseline_age:.2f}, Avg Starter OVR={baseline_starter:.2f}")

    seasons_history = []
    positions_pool = [p["position"] for p in current_roster]
    teams_pool = list(set(p["team_id"] for p in current_roster if p["team_id"]))

    annual_adjustment = 0.0
    for yr in range(1, num_seasons + 1):
        sim_s = start_season + yr
        survivors = []
        retire_rng = random.Random(f"retire:{sim_s}")

        for p in current_roster:
            age = p["age"]
            ovr = p["ovr"]

            # NFL retirement odds
            if age >= 36:
                ret_prob = 0.90
            elif age >= 34:
                ret_prob = 0.35 if ovr >= 85 else 0.70
            elif age >= 32:
                ret_prob = 0.12 if ovr >= 85 else 0.40
            elif age >= 30:
                ret_prob = 0.04 if ovr >= 80 else 0.20
            elif age >= 27:
                ret_prob = 0.01 if ovr >= 72 else 0.10
            else:
                ret_prob = 0.005 if ovr >= 68 else 0.05

            if retire_rng.random() < ret_prob:
                continue

            new_age = age + 1
            p_id = p["player_id"]
            pos = p["position"]
            dev = p["dev"]

            ceiling, _ = seed_player_ceiling(p_id, ovr, new_age, dev)
            delta, new_ovr, _, _ = compute_player_delta(
                player_id=p_id,
                position=pos,
                age=new_age,
                ovr=ovr,
                dev_trait=dev,
                ceiling=ceiling,
                games_played=p.get("gp", 16),
                downs_played=None,
                is_starter=True,
                is_ir=False,
                injury_weeks=0,
                season=sim_s,
                aging_curves=aging_curves
            )
            # Apply dynamic drift adjustment (§5.5)
            if annual_adjustment != 0.0:
                new_ovr = max(OVR_MIN, min(OVR_MAX, int(round(new_ovr + annual_adjustment))))

            survivors.append({
                "player_id": p_id, "team_id": p["team_id"], "position": pos,
                "age": new_age, "ovr": new_ovr, "dev": dev, "gp": 16
            })

        # Replenish league with rookie class
        needed = baseline_n - len(survivors)
        rookie_rng = random.Random(f"draft:{sim_s}")
        for i in range(needed):
            pos = rookie_rng.choice(positions_pool)
            team = rookie_rng.choice(teams_pool)
            pick = i + 1
            if pick <= 10:
                r_ovr = rookie_rng.randint(79, 84)
                r_dev = rookie_rng.choices(["Star", "Superstar", "X-Factor", "Normal"], weights=[0.35, 0.40, 0.20, 0.05])[0]
            elif pick <= 32:
                r_ovr = rookie_rng.randint(76, 80)
                r_dev = rookie_rng.choices(["Star", "Superstar", "Normal"], weights=[0.45, 0.30, 0.25])[0]
            elif pick <= 100:
                r_ovr = rookie_rng.randint(72, 77)
                r_dev = rookie_rng.choices(["Star", "Superstar", "Normal"], weights=[0.40, 0.10, 0.50])[0]
            else:
                r_ovr = rookie_rng.randint(66, 73)
                r_dev = rookie_rng.choices(["Star", "Normal"], weights=[0.20, 0.80])[0]

            r_age = rookie_rng.choice([21, 22, 23])
            survivors.append({
                "player_id": 100000 + sim_s * 1000 + i,
                "team_id": team, "position": pos, "age": r_age,
                "ovr": r_ovr, "dev": r_dev, "gp": 15
            })

        current_roster = survivors
        n_90 = sum(1 for p in current_roster if p["ovr"] >= 90)
        avg_o = sum(p["ovr"] for p in current_roster) / len(current_roster)
        avg_a = sum(p["age"] for p in current_roster) / len(current_roster)

        s_rows = [{"player_id": p["player_id"], "team_id": p["team_id"], "ovr_start": p["ovr"]} for p in current_roster]
        st = starter_flags(s_rows)
        starters = [p for p in current_roster if st.get(p["player_id"])]
        avg_starter = sum(p["ovr"] for p in starters) / len(starters) if starters else 0.0

        # Starter avg by position
        pos_starter_ovr = defaultdict(list)
        for p in starters:
            pos_starter_ovr[p["position"]].append(p["ovr"])
        pos_starter_means = {pos: round(sum(vals) / len(vals), 1) for pos, vals in sorted(pos_starter_ovr.items())}

        log(f"Season {sim_s}: 90+={n_90:3} (Δ={n_90 - baseline_90:+3}), Avg OVR={avg_o:.2f}, Avg Age={avg_a:.2f}, Avg Starter={avg_starter:.2f}")

        seasons_history.append({
            "season": sim_s,
            "players_count": len(current_roster),
            "count_90_plus": n_90,
            "avg_ovr": round(avg_o, 2),
            "avg_age": round(avg_a, 2),
            "avg_starter_ovr": round(avg_starter, 2),
            "starter_ovr_by_pos": pos_starter_means
        })

        # §5.5 feedback: adjust age/baseline term for next season if drifting
        if n_90 < 130:
            annual_adjustment = min(0.40, annual_adjustment + 0.10)
        elif n_90 > 155:
            annual_adjustment = max(-0.40, annual_adjustment - 0.10)

    # Stability checks (§5.5): verify no drift past bounds
    final_state = seasons_history[-1]
    is_stable = (
        (45 <= final_state["count_90_plus"] <= 180) and
        (80.5 <= final_state["avg_starter_ovr"] <= 88.0) and
        (74.0 <= final_state["avg_ovr"] <= 80.5) and
        (25.5 <= final_state["avg_age"] <= 28.5)
    )

    log(f"\nStability Check Result: {'PASSED (No drift detected)' if is_stable else 'WARNING (Drift detected)'}")
    return {
        "start_season": start_season,
        "num_seasons": num_seasons,
        "baseline": {
            "players": baseline_n, "count_90_plus": baseline_90,
            "avg_ovr": round(baseline_ovr, 2), "avg_age": round(baseline_age, 2),
            "avg_starter_ovr": round(baseline_starter, 2)
        },
        "history": seasons_history,
        "is_stable": is_stable
    }


# ---------------------------------------------------------------- CLI ----
def main():
    parser = argparse.ArgumentParser(description="Player Progression Engine (offline)")
    parser.add_argument("--season", type=int, default=2037, help="Target season (default 2037)")
    parser.add_argument("--commit", action="store_true", help="Commit results to player_progression_history")
    parser.add_argument("--dry-run", action="store_true", help="Dry run without database commit")
    parser.add_argument("--backtest", action="store_true", help="Evaluate 2035->2036 actual vs predicted progression")
    parser.add_argument("--simulate", type=int, metavar="N", help="Simulate N seasons ahead to verify stability")
    parser.add_argument("--dsn", default=DSN, help="Database DSN")

    args = parser.parse_args()

    with psycopg.connect(args.dsn) as conn:
        if args.backtest:
            run_backtest(conn, season=args.season if args.season <= 2036 else 2036)
        elif args.simulate:
            run_simulation(conn, start_season=args.season if args.season <= 2036 else 2036, num_seasons=args.simulate)
        else:
            run_progression(conn, season=args.season, commit=args.commit, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
