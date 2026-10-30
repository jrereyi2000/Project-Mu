"""
Player Progression Engine (docs/in_progress/PROGRESSION_PLAN.md §5; PROJECT_SPEC §2.21; docs/done/PRE_PC_WORK.md §11).

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
          + offset(age band)              # AGE_BAND_OFFSET, calibration to Madden's 2035->2036 offseason
          + league                        # league-shape term holding the 2036 shape (calibrate_league_term)
          + performance                   # last season's production rank + awards (performance_scores)
          + wear + continuity + mentor    # phase C2 (§5.8, §5.13); upkeep above 90 sits inside league (§5.10)
          + persona + coach               # 0 until personalities (§6, §7; V2 core D4)
   `dev` is growth toward the ceiling band (§5.9: band_growth from the spread and room to the technical ceiling);
   breakthroughs raise technical and mental ceilings; lasting injury damage lands in `injury`.
   Clamped per age band and OVR band. Each term stored in breakdown dict for newsroom citations.

2. Ratings Layer (§5.4):
   - Speed, Agility, Acceleration, Jumping and ChangeOfDirection decline with age; Speed and Strength grow a little
     at 24 and under; injuries lower Speed and Acceleration.
   - Adjusts archetype 'skills' via engines.ovr_weights.adjust_ratings to reach target OVR, verifying
     the archetype remains on top with settle_archetype / predict_ovr. The skills include Awareness and often
     Speed, so the OVR change lands on them too; the plan's rating groups (phase C2) replace this.

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
from pipeline.madden_player_edit import TO_MADDEN_POSITION, madden_archetype, settle_archetype

DSN = os.environ.get("DATABASE_URL", "host=localhost dbname=madden user=postgres password=postgres")

# Rating groups (PROGRESSION_PLAN §5.1, §5.4): physical ratings are set on draft day and worn down by age, mental
# ratings decline last and slowest, everything else is technical. Injury is left alone (durability, §5.8).
PHYSICAL_RATINGS = ("Speed", "Acceleration", "Agility", "ChangeOfDirection", "Strength", "Jumping", "Stamina",
                    "ThrowPower", "KickPower", "Toughness")
MENTAL_RATINGS = ("Awareness", "PlayRecognition")
FIXED_RATINGS = ("Injury",)

# Madden's mean mental gain since draft day by years pro (Awareness and PlayRecognition, current minus
# Original*Rating in CAREER-GIANTS-AUTOSAVE): ((max years pro, gain), ...). Stands in for draft-day ratings until
# the PC save's Original*Rating is loaded; 10+ years (+19.6 in the sample) mixes in pre-franchise entry ratings, so
# 7+ uses the 7-9 value.
MADDEN_MENTAL_GAIN_BY_YEARS = ((1, 0.2), (3, 15.3), (6, 32.6), (99, 37.0))
MENTAL_RATING_FLOOR = 20
# Mental decline (JR 2026-10-28, D10): last and slowest, at a rate set by the peak. From MENTAL_DECLINE_AGE on (once
# physical decline is under way) a player loses a share each season of what he gained between draft day and his
# career peak, never falling below his draft-day rating: ((max age, share), ...).
MENTAL_DECLINE_SHARE = ((29, 0.0), (31, 0.04), (33, 0.07), (99, 0.10))
# Share of an OVR gain put into mental skills first (§5.4). Madden's young players gain mostly mental ratings:
# +15.3 Awareness/PlayRecognition against +4.6 on technical ratings (draft-day 55+) at 2-3 years pro in
# CAREER-GIANTS-AUTOSAVE (3.3 to 1 in points per rating). At 0.3 the engine's 1-3-year players gaining 3 OVR grow
# 3.7 to 1 (0 gives 1.4; 0.4 gives 15). Refit on the PC from the save's own Original*Rating.
MENTAL_GROWTH_SHARE = 0.3
# Strength program (§5.4): +1 Strength and Stamina after each of a player's first two seasons, below this rating
STRENGTH_PROGRAM_YEARS = 1          # years_of_experience 0 and 1 (rookie = 0)
STRENGTH_PROGRAM_CAP = 90

# Position groups that decline physically earlier (CB, RB, WR peak and fade earlier in Madden)
EARLY_DECLINE_GROUPS = {"CB", "RB", "WR"}

# Clamping bounds: (min_delta, max_delta) by age band and OVR band
OVR_MAX = 99
OVR_MIN = 40
MAX_SEASON_CHANGE = 8

# Calibration to Madden (backtest on 2035->2036, 1,385 players): a per-age-band offset in place of one -0.65 for
# everyone, which grew young players ~0.1-0.2 a year too fast and aged 30-33s ~0.4 too fast. Band order as
# trade_value.AGE_BANDS: <=23, 24-26, 27-29, 30-31, 32-33, 34+ (34+ had 5 players; it takes 32-33's value).
AGE_BAND_OFFSET = (-0.86, -0.76, -0.65, -0.28, -0.19, -0.19)

# League shape (JR, 2026-10-28): year over year the league keeps roughly its 2036 shape, the same number of 90+
# players and the same mean OVR. Madden's own turnover doesn't (its rookie classes come in ~10 OVR below the veterans
# they replace), so each offseason a league term adds development to young players, sized by projecting the league
# with the coming rookie class (calibrate_league_term). Weight of the term by age; 0 from 30 on.
LEAGUE_SHAPE_SEASON = 2036
LEAGUE_TERM_WEIGHT = ((23, 1.0), (26, 0.75), (29, 0.35))
# ... and by OVR: the term develops the young players below star level (who replace departing starters), not the
# stars, so the 90+ count is left to ceilings and aging instead of being manufactured
LEAGUE_TERM_OVR_WEIGHT = ((79, 1.0), (85, 0.5), (87, 0.2))
# A second, star term holds the 90+ count: OVR a year for players at STAR_OVR+ and at most STAR_MAX_AGE (slower
# decline or a push past 90). Both terms are fitted together each offseason.
STAR_OVR, STAR_MAX_AGE = 86, 32
LEAGUE_TERM_STEPS = [i / 2 for i in range(0, 13)]        # development: 0 .. 6 OVR a year
STAR_TERM_STEPS = [i / 4 for i in range(-4, 9)]          # stars: -1 .. +2 OVR a year

# Stability check bounds relative to 2036 baseline (PROGRESSION_PLAN §5.5; PRE_PC_WORK.md R6)
# Evaluated for each season simulated, not just the final season.
STABILITY_BOUNDS_2036 = {
    "count_90_plus_pct": 0.25,        # 90+ count within ±25% of baseline (108 to 180 for 144 baseline)
    "avg_ovr_delta": 1.0,             # Mean OVR within ±1.0 of baseline (77.13 to 79.13 for 78.13 baseline)
    "avg_starter_ovr_delta": 2.0,     # Mean starter OVR within ±2.0 of baseline (83.51 to 87.51 for 85.51 baseline)
    "avg_age_min": 25.5,
    "avg_age_max": 28.8,
}


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
    aging_curves: Dict[str, Any],
    league_adj: float = 0.0,
    star_adj: float = 0.0,
    performance: float = 0.0,
    spread: Optional[float] = None,
    technical_ceiling: Optional[float] = None,
    wear: float = 0.0,
    lasting_damage: int = 0,
    upkeep: float = 0.0,
    continuity: float = 0.0,
    mentor: float = 0.0,
    breakthrough: bool = False,
) -> Tuple[int, int, Dict[str, float], str]:
    """Calculates yearly OVR delta and breakdown of individual causal terms (§5.2).
    Phase C2 (§5.8-§5.13): `dev` is growth toward the ceiling band (band_growth; spread defaults to the dev rank's
    BAND_SPREAD_BY_DEV, technical_ceiling to ceiling); `wear` (wear_term) and `lasting_damage` (rating points lost,
    LASTING_DAMAGE_OVR each, in the injury term) are physical; `upkeep` (upkeep_term) sits inside the league term
    and is applied only with it; `continuity` and `mentor` are small growth terms. Persona and coach terms are 0.
    Returns (delta_ovr, new_ovr, terms_breakdown, cause_tag)."""
    group = position_group(position)
    band = age_band(age)
    curve = aging_curves.get(group, aging_curves.get("generic", [0.0] * len(AGE_BANDS)))
    age_t = curve[band] if band < len(curve) else curve[-1]

    dr = dev_rank(dev_trait) or 0

    # Growth toward the ceiling band (§5.9; replaces the dev table): the spread (from the dev rank unless stored)
    # sets the rate, room to the technical ceiling scales it; from 29 the band resists decline
    sp = BAND_SPREAD_BY_DEV[dr] if spread is None else float(spread)
    dev_t = band_growth(player_id, season, age, ovr, sp, ceiling if technical_ceiling is None else technical_ceiling)

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
    # Lasting damage from a major structural injury (§5.8)
    inj_t -= LASTING_DAMAGE_OVR * max(0, lasting_damage)

    # Seeded noise: strictly deterministic per player and season
    noise_rng = random.Random(f"prog:{season}:{player_id}")
    noise_t = noise_rng.gauss(0.0, 1.0)

    # Calibration to Madden's 2035->2036 offseason by age band (AGE_BAND_OFFSET)
    baseline_offset = AGE_BAND_OFFSET[min(band, len(AGE_BAND_OFFSET) - 1)]
    # League shape term (calibrate_league_term): extra development for young players, 0 from 30 on
    league_t = league_adj * league_term_weight(age, ovr) + (star_adj if ovr >= STAR_OVR and age <= STAR_MAX_AGE else 0.0)
    # Upkeep above 90 (§5.10) is part of the league term
    league_t += upkeep
    # Performance term (performance_term): how well he played last season, ranked within his position group
    perf_t = performance
    c2_t = wear + continuity + mentor + PERSONA_TERM + COACH_TERM
    raw_delta = age_t + dev_t + ceil_t + play_t + inj_t + noise_t + baseline_offset + league_t + perf_t + c2_t

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
    elif breakthrough:
        cause = "BREAKTHROUGH"
    elif perf_t >= 1.0 and final_delta > 0:
        cause = "STANDOUT_SEASON"
    elif perf_t <= -0.75 and final_delta < 0:
        cause = "POOR_SEASON"
    elif upkeep <= -1.0 and final_delta < 0:
        cause = "UPKEEP"
    elif wear <= -0.6 and final_delta < 0:
        cause = "WEAR"
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
        "league": round(league_t, 2),
        "performance": round(perf_t, 2),
        "wear": round(wear, 3),
        "upkeep": round(upkeep, 3),
        "continuity": round(continuity, 3),
        "mentor": round(mentor, 3),
        "persona": PERSONA_TERM,
        "coach": COACH_TERM,
    }

    return final_delta, new_ovr, breakdown, cause


def league_term_weight(age: int, ovr: int = 0) -> float:
    by_age = next((w for hi, w in LEAGUE_TERM_WEIGHT if (age or 0) <= hi), 0.0)
    by_ovr = next((w for hi, w in LEAGUE_TERM_OVR_WEIGHT if (ovr or 0) <= hi), 0.0)
    return by_age * by_ovr


# ------------------------------------------------------- phase C2 terms (§5.8-§5.13) ----
# Every C2 term is small, bounded and pure; run_progression reads its inputs from the DB (fetch_c2_inputs).
# Personality (§6) and coach (§7) terms ship at 0 (V2 core plan D4): persona_term and coach_term are always 0.
PERSONA_TERM = 0.0
COACH_TERM = 0.0

# Durability (§5.8, hidden, 0-1): a start from InjuryRating and ToughnessRating (their mean 80 -> 0.5, each 4 points
# -> 0.1, kept inside DURABILITY_START_RANGE), moved by a seeded draw sha256("durability:" + player_id) of at most
# ±DURABILITY_SPREAD / 2. No ratings: start 0.5 (source DEFAULT).
DURABILITY_RATING_MID, DURABILITY_RATING_SCALE = 80.0, 40.0
DURABILITY_START_RANGE = (0.15, 0.85)
DURABILITY_SPREAD = 0.3


def _sha_unit(key: str) -> float:
    """A stable number in [0, 1] from sha256(key) (unlike hash(), the same in every process)."""
    import hashlib
    return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF


def seed_durability(player_id: int, injury_rating: Optional[float] = None,
                    toughness_rating: Optional[float] = None) -> Tuple[float, str]:
    """(durability 0..1, source 'RATINGS' | 'DEFAULT'), deterministic per player."""
    vals = [float(v) for v in (injury_rating, toughness_rating) if v is not None]
    if vals:
        lo, hi = DURABILITY_START_RANGE
        start = max(lo, min(hi, 0.5 + (sum(vals) / len(vals) - DURABILITY_RATING_MID) / DURABILITY_RATING_SCALE))
        source = "RATINGS"
    else:
        start, source = 0.5, "DEFAULT"
    d = start + (_sha_unit(f"durability:{player_id}") - 0.5) * DURABILITY_SPREAD
    return round(max(0.0, min(1.0, d)), 3), source


# Mileage (§5.8): one season's wear points from touches, hits and snaps, weighted by position group so a starter at
# any position carries roughly 10 a season: (per touch, per hit, per snap). Touches: carries, catches, returns. Hits:
# QB sacks taken and runs; RB carries and catches; receivers' catches; defenders' tackles. Snaps: downs played, or
# games x MILEAGE_SNAPS_PER_GAME where downs aren't recorded (before 2035).
MILEAGE_WEIGHTS = {
    "QB": (0.0, 0.05, 0.005), "RB": (0.03, 0.01, 0.004), "REC": (0.03, 0.01, 0.006), "OL": (0.0, 0.0, 0.0095),
    "FRONT": (0.0, 0.03, 0.01), "DB": (0.0, 0.02, 0.0095), "K": (0.0, 0.0, 0.002), "P": (0.0, 0.0, 0.002),
}
MILEAGE_SNAPS_PER_GAME = {"QB": 55, "RB": 30, "REC": 40, "OL": 60, "FRONT": 45, "DB": 50, "K": 8, "P": 8}
DEFAULT_SEASON_WEAR = 6.0       # normal season wear when a group has no stat lines to average


def season_mileage(group: Optional[str], r: Dict[str, Any]) -> Dict[str, Any]:
    """{touches, hits, snaps, wear} for one season stat line (season_stat_lines column names)."""
    g = lambda k: int(r.get(k) or 0)
    touches = g("rush_attempts") + g("receptions") + g("kr_returns") + g("pr_returns")
    if group == "QB":
        hits = g("pass_sacks") + g("rush_attempts")
    elif group == "RB":
        hits = g("rush_attempts") + g("receptions")
    elif group == "REC":
        hits = g("receptions")
    elif group in ("FRONT", "DB"):
        hits = g("def_tackles")
    else:
        hits = 0
    snaps = g("downs_played") or g("games_played") * MILEAGE_SNAPS_PER_GAME.get(group, 30)
    wt, wh, ws = MILEAGE_WEIGHTS.get(group, (0.0, 0.0, 0.005))
    return {"touches": touches, "hits": hits, "snaps": snaps, "wear": round(wt * touches + wh * hits + ws * snaps, 2)}


def mileage_excess(career_wear: float, seasons: int, normal_season_wear: float) -> float:
    """Career wear above (+) or below (-) normal for the seasons he has played at his position group, as a share of
    normal: 0 = normal, 1 = twice normal. 0 without seasons."""
    if seasons <= 0 or normal_season_wear <= 0:
        return 0.0
    normal = normal_season_wear * seasons
    return (career_wear - normal) / normal


# Wear (§5.8) in OVR a year, part of the physical decline: -WEAR_K x excess (clamped to WEAR_EXCESS_RANGE) x
# (WEAR_DURABILITY_PIVOT - durability) x the age ramp (0 before the physical decline starts, full WEAR_RAMP_YEARS
# later; a year earlier for CB, RB, WR). High durability halves it, low durability raises it by half. Low mileage
# slows the decline a little. Bounded by WEAR_TERM_RANGE.
WEAR_K = 0.5
WEAR_EXCESS_RANGE = (-0.5, 1.5)
WEAR_DURABILITY_PIVOT = 1.5
WEAR_START_AGE, WEAR_RAMP_YEARS = 27, 4
WEAR_TERM_RANGE = (-1.2, 0.4)


def wear_term(age: int, position: Optional[str], excess: float, durability: float) -> float:
    start = WEAR_START_AGE - (1 if position_group(position) in EARLY_DECLINE_GROUPS else 0)
    ramp = max(0.0, min(1.0, ((age or 0) - start + 1) / WEAR_RAMP_YEARS))
    if not ramp:
        return 0.0
    e = max(WEAR_EXCESS_RANGE[0], min(WEAR_EXCESS_RANGE[1], excess))
    w = -WEAR_K * e * (WEAR_DURABILITY_PIVOT - max(0.0, min(1.0, durability))) * ramp
    return round(max(WEAR_TERM_RANGE[0], min(WEAR_TERM_RANGE[1], w)), 3)


# Lasting injury damage (§5.8): a major structural injury last season (injury_type contains one of these, and not
# PARTIAL) rolls, seeded per season and player, for a permanent -1..-3 to Speed, Acceleration or Agility and the
# same off his physical ceiling. Chance LASTING_DAMAGE_BASE x (1.5 - durability), within LASTING_DAMAGE_CHANCE_RANGE.
# OVR effect: LASTING_DAMAGE_OVR per point, in the injury term.
MAJOR_STRUCTURAL_INJURIES = ("ACL TEAR", "TORN ACL", "ACHILLES", "LISFRANC", "PATELLA", "DISLOCATED KNEE")
LASTING_DAMAGE_BASE = 0.5
LASTING_DAMAGE_CHANCE_RANGE = (0.15, 0.85)
LASTING_DAMAGE_OVR = 0.35
LASTING_DAMAGE_RATINGS = ("Speed", "Acceleration", "Agility")


def is_major_structural(injury_type: Optional[str]) -> bool:
    t = (injury_type or "").upper()
    return "PARTIAL" not in t and any(k in t for k in MAJOR_STRUCTURAL_INJURIES)


def lasting_injury_damage(player_id: int, season: int, injury_types: List[Optional[str]],
                          durability: float) -> Dict[str, int]:
    """{rating: -points} for at most one physical rating, or {} (no major injury, or the roll missed)."""
    if not any(is_major_structural(t) for t in injury_types or []):
        return {}
    rng = random.Random(f"injury_damage:{season}:{player_id}")
    lo, hi = LASTING_DAMAGE_CHANCE_RANGE
    if rng.random() >= max(lo, min(hi, LASTING_DAMAGE_BASE * (1.5 - durability))):
        return {}
    return {rng.choice(LASTING_DAMAGE_RATINGS): -rng.randint(1, 3)}


# Ceiling bands (§5.9): floor, spread and a ceiling per group. The spread replaces the dev multiplier: growth a year
# is BAND_GROWTH_PER_SPREAD (by age) x spread x room (technical ceiling - OVR, full at BAND_ROOM_FULL points), plus a
# seeded draw of at most ±BAND_DRAW_SCALE x spread while he is still growing (28 and under). From 29 the band
# resists decline at the same rate, room or not. Spreads by dev rank (BAND_SPREAD_BY_DEV) reproduce the old dev table
# within 0.05 (Normal 0; Star +0.5 / X-Factor +1.5 at 24 and under), so the backtest holds.
BAND_SPREAD_BY_DEV = (0.0, 1.0, 2.0, 3.0)
BAND_GROWTH_PER_SPREAD = ((24, 0.5), (26, 0.33), (28, 0.2), (99, 0.23))
BAND_ROOM_FULL = 3.0
BAND_DRAW_SCALE = 0.1
BAND_FLOOR_DROP = 4.0           # floor: OVR - this + dev rank, ± 1 seeded
MENTAL_CEILING_BONUS = 1.0      # mental ceiling above the technical one (mental grows longest, §5.4)
STRENGTH_PROGRAM_OVR = 1.0      # physical ceiling above current OVR in the first two seasons


def seed_ceiling_band(player_id: int, ovr: int, dev_trait: Optional[str], ceiling: float,
                      years_pro: Optional[int] = None) -> Dict[str, Any]:
    """{floor, spread, group_ceilings: {physical, technical, mental}} for a player without a stored band."""
    dr = dev_rank(dev_trait) or 0
    rng = random.Random(f"band:{player_id}")
    floor = max(float(OVR_MIN), min(float(ovr), ceiling) - BAND_FLOOR_DROP + dr + rng.uniform(-1.0, 1.0))
    phys = min(float(OVR_MAX), ovr + (STRENGTH_PROGRAM_OVR if years_pro is not None and years_pro <= 1 else 0.0))
    return {"floor": round(floor, 2), "spread": BAND_SPREAD_BY_DEV[dr],
            "group_ceilings": {"physical": round(phys, 2), "technical": round(float(ceiling), 2),
                               "mental": round(min(float(OVR_MAX), ceiling + MENTAL_CEILING_BONUS), 2)}}


def band_growth(player_id: int, season: int, age: int, ovr: int, spread: float, technical_ceiling: float) -> float:
    """Growth toward the band (OVR a year), the dev term of §5.2 as §5.9 revises it."""
    rate = next(r for hi, r in BAND_GROWTH_PER_SPREAD if (age or 0) <= hi)
    if (age or 0) > 28:
        return rate * spread
    room = max(0.0, min(1.0, (technical_ceiling - ovr) / BAND_ROOM_FULL))
    draw = random.Random(f"band:{season}:{player_id}").uniform(-1.0, 1.0) * BAND_DRAW_SCALE * spread
    return rate * spread * room + draw * room


# Breakthroughs (§5.9): 28 or younger with two straight seasons in his position group's top quarter of
# performance_scores: with BREAKTHROUGH_CHANCE (personality and coaching odds are 0 until §6, §7), his technical and
# mental ceilings rise by a seeded 1..4. Cause BREAKTHROUGH.
BREAKTHROUGH_MAX_AGE = 28
BREAKTHROUGH_CHANCE = 0.5
BREAKTHROUGH_RANGE = (1, 4)
TOP_QUARTER = 0.75


def top_quarter(perf: Dict[int, Dict[str, Any]]) -> set:
    """Players whose score is in the top quarter of their position group (ties share the higher rank)."""
    by_group = defaultdict(list)
    for pid, p in (perf or {}).items():
        if p.get("group"):
            by_group[p["group"]].append((p.get("score", 0.0), pid))
    out = set()
    for vals in by_group.values():
        if len(vals) < 4:
            continue
        scores = sorted((s for s, _ in vals), reverse=True)
        cut = scores[int(math.ceil((1 - TOP_QUARTER) * len(scores))) - 1]
        if cut > scores[-1]:
            out.update(pid for s, pid in vals if s >= cut)
    return out


def breakthrough(player_id: int, season: int, age: int, top_last: bool, top_prev: bool) -> int:
    """Ceiling points added to technical and mental (0: no breakthrough)."""
    if (age or 99) > BREAKTHROUGH_MAX_AGE or not (top_last and top_prev):
        return 0
    rng = random.Random(f"breakthrough:{season}:{player_id}")
    return rng.randint(*BREAKTHROUGH_RANGE) if rng.random() < BREAKTHROUGH_CHANCE else 0


def apply_breakthrough(band: Dict[str, Any], ceiling: float, points: int) -> Tuple[Dict[str, Any], float]:
    if not points:
        return band, ceiling
    gc = dict(band["group_ceilings"])
    gc["technical"] = min(float(OVR_MAX), gc["technical"] + points)
    gc["mental"] = min(float(OVR_MAX), gc["mental"] + points)
    return {**band, "group_ceilings": gc}, min(float(OVR_MAX), ceiling + points)


# Upkeep above 90 (§5.10), part of the league term (off with --follow-madden and in the Madden backtest, which has no
# league term): -UPKEEP_K x (OVR - 90) x (1 - production), production = (reliability-weighted score + 1) / 2 in 0..1,
# raised by missed games (up to +UPKEEP_MISSED_MAX at UPKEEP_MISSED_FULL games) and wear above normal (up to
# +UPKEEP_WEAR_MAX). A 98 with a middling season loses ~2, ~2.75 if he also missed 6 games; a top producer ~0. The
# star term (calibrate_league_term) keeps the 90+ count, so upkeep decides who holds the spots. Bounded by
# UPKEEP_TERM_MIN.
UPKEEP_OVR = 90
UPKEEP_K = 0.5
UPKEEP_MISSED_FULL, UPKEEP_MISSED_MAX = 8, 0.5
UPKEEP_WEAR_MAX = 0.25
UPKEEP_TERM_MIN = -4.0


def upkeep_term(ovr: int, perf_score: Optional[float], games_missed: int = 0, wear_excess: float = 0.0) -> float:
    if (ovr or 0) <= UPKEEP_OVR:
        return 0.0
    production = max(0.0, min(1.0, ((perf_score or 0.0) + 1.0) / 2.0))
    mult = (1.0 + UPKEEP_MISSED_MAX * min(max(games_missed, 0), UPKEEP_MISSED_FULL) / UPKEEP_MISSED_FULL
            + UPKEEP_WEAR_MAX * max(0.0, min(1.0, wear_excess)))
    return round(max(UPKEEP_TERM_MIN, -UPKEEP_K * (ovr - UPKEEP_OVR) * (1.0 - production) * mult), 3)


# Scheme continuity (§5.13): the first season under a new coordinator for his side of the ball, or on a new team,
# costs technical and mental growth CONTINUITY_PENALTY; a second straight season with the same team and staff earns
# CONTINUITY_BONUS. Weighted by how much he still grows (CONTINUITY_AGE_WEIGHT). Openness (§7) is 0 for now.
CONTINUITY_PENALTY = 0.25
CONTINUITY_BONUS = 0.05
CONTINUITY_AGE_WEIGHT = ((26, 1.0), (29, 0.5), (99, 0.25))
OFFENSE_GROUPS = ("QB", "RB", "REC", "OL")
DEFENSE_GROUPS = ("FRONT", "DB")


def continuity_term(age: int, new_coordinator: bool, changed_team: bool, known: bool = True) -> float:
    if not known:
        return 0.0
    w = next(x for hi, x in CONTINUITY_AGE_WEIGHT if (age or 0) <= hi)
    return round(w * (-CONTINUITY_PENALTY if (new_coordinator or changed_team) else CONTINUITY_BONUS), 3)


# Mentors (§5.13): a teammate in the same position group, MENTOR_MIN_AGE or older with Awareness MENTOR_MIN_AWARENESS+,
# speeds a young player's mental growth by MENTOR_BONUS (half from MENTOR_FULL_AGE + 1 to MENTOR_MAX_AGE). The
# Mentor / Loner traits (§6) multiply it later.
MENTOR_BONUS = 0.15
MENTOR_FULL_AGE, MENTOR_MAX_AGE = 23, 25
MENTOR_MIN_AGE, MENTOR_MIN_AWARENESS = 29, 85


def mentor_term(age: int, has_mentor: bool) -> float:
    if not has_mentor or (age or 99) > MENTOR_MAX_AGE:
        return 0.0
    return MENTOR_BONUS if age <= MENTOR_FULL_AGE else MENTOR_BONUS / 2


def find_mentor(player: Dict[str, Any], teammates: List[Dict[str, Any]],
                awareness: Dict[int, int]) -> Optional[int]:
    """The best-aware eligible veteran teammate in his position group (player_id), or None."""
    group = _group_of(player.get("position"))
    best = None
    for t in teammates:
        if t["player_id"] == player["player_id"] or _group_of(t.get("position")) != group:
            continue
        aw = awareness.get(t["player_id"]) or 0
        if (t.get("age") or 0) >= MENTOR_MIN_AGE and aw >= MENTOR_MIN_AWARENESS and (best is None or aw > best[0]):
            best = (aw, t["player_id"])
    return best[1] if best else None


# Behaviour traits (§5.12): PT_* flags gained or lost in the offseason pass, stored in behaviour_changes (written to
# the save in phase H, not here). At most one gained and one lost per player and season, seeded
# Random(f"pt:{season}:{player_id}"). A flag is gained only if not known to be set and lost only if known to be set
# (traits from the save snapshot; none known -> no losses). Penalty-driven flags (PT_UNDISCIPLINED) need penalty
# stats Project Mu doesn't have yet, so they aren't modelled.
PT_BAD_QB = ("PT_HAPPYFEET", "PT_SEEINGGHOSTS")
PT_SACK_RATE_BAD, PT_SACK_RATE_GOOD = 0.08, 0.055
PT_MIN_ATTEMPTS = 150
PT_YOUNG_QB_YEARS = 3
PT_GAIN_CHANCE = 0.25            # + PT_GAIN_PER_RATE x (sack rate - bad rate), at most PT_GAIN_MAX
PT_GAIN_PER_RATE, PT_GAIN_MAX = 3.0, 0.5
PT_LOSE_CHANCE = 0.35
PT_MENTOR_SHIFT = 0.1            # a mentor lowers gain odds and raises loss odds by this
PT_COVERBALL = "PT_COVERBALL"
PT_FUMBLE_RATE_BAD, PT_FUMBLE_RATE_GOOD = 0.015, 0.004
PT_MIN_TOUCHES_LOSE, PT_MIN_TOUCHES_GAIN = 100, 150
PT_COVERBALL_GAIN_CHANCE = 0.2


def behaviour_changes(player_id: int, season: int, position: Optional[str], years_pro: Optional[int],
                      stats: Optional[Dict[str, Any]], traits: Optional[Dict[str, bool]] = None,
                      has_mentor: bool = False) -> Optional[Dict[str, List[Dict[str, str]]]]:
    """{"gained": [{"flag", "cause"}], "lost": [{"flag", "cause"}]} or None when nothing changes."""
    traits = traits or {}
    s = stats or {}
    g = lambda k: float(s.get(k) or 0)
    group = _group_of(position)
    rng = random.Random(f"pt:{season}:{player_id}")
    gain_opts, lose_opts = [], []      # (chance, flag, cause)
    shift = PT_MENTOR_SHIFT if has_mentor else 0.0
    if group == "QB" and g("pass_attempts") >= PT_MIN_ATTEMPTS:
        rate = g("pass_sacks") / (g("pass_attempts") + g("pass_sacks"))
        young = years_pro is not None and years_pro <= PT_YOUNG_QB_YEARS
        if young and rate >= PT_SACK_RATE_BAD:
            chance = max(0.0, min(PT_GAIN_MAX, PT_GAIN_CHANCE + PT_GAIN_PER_RATE * (rate - PT_SACK_RATE_BAD)) - shift)
            flag = PT_BAD_QB[rng.randrange(len(PT_BAD_QB))]
            if not traits.get(flag):
                gain_opts.append((chance, flag, "SACKS"))
        for flag in PT_BAD_QB:
            if traits.get(flag) and (rate <= PT_SACK_RATE_GOOD or (years_pro or 0) > PT_YOUNG_QB_YEARS):
                lose_opts.append((PT_LOSE_CHANCE + shift, flag,
                                  "BETTER_SITUATION" if rate <= PT_SACK_RATE_GOOD else "EXPERIENCE"))
    touches = g("rush_attempts") + g("receptions")
    if group in ("RB", "REC", "QB") and touches > 0:
        frate = g("fumb") / touches
        if traits.get(PT_COVERBALL) and touches >= PT_MIN_TOUCHES_LOSE and frate >= PT_FUMBLE_RATE_BAD:
            lose_opts.append((PT_LOSE_CHANCE, PT_COVERBALL, "FUMBLES"))
        if (group == "RB" and not traits.get(PT_COVERBALL) and touches >= PT_MIN_TOUCHES_GAIN
                and frate <= PT_FUMBLE_RATE_GOOD):
            gain_opts.append((PT_COVERBALL_GAIN_CHANCE, PT_COVERBALL, "BALL_SECURITY"))
    gained = next(({"flag": f, "cause": c} for ch, f, c in gain_opts if rng.random() < ch), None)
    lost = next(({"flag": f, "cause": c} for ch, f, c in lose_opts
                 if rng.random() < ch and (gained is None or gained["flag"] != f)), None)
    if not gained and not lost:
        return None
    return {"gained": [gained] if gained else [], "lost": [lost] if lost else []}


def group_changes(ratings_diff: Dict[str, int]) -> Dict[str, int]:
    """Rating points changed per group (physical, technical, mental) from a ratings diff."""
    out = {"physical": 0, "technical": 0, "mental": 0}
    for k, v in (ratings_diff or {}).items():
        grp = rating_group(k)
        if grp in out:
            out[grp] += v
    return out


# ---------------------------------------------------------------- ratings layer ----
def rating_group(name: str) -> str:
    """'physical', 'mental', 'fixed' or 'technical' (§5.1)."""
    if name in PHYSICAL_RATINGS:
        return "physical"
    if name in MENTAL_RATINGS:
        return "mental"
    return "fixed" if name in FIXED_RATINGS else "technical"


def estimate_entry_mental(ratings: Dict[str, int], years_pro: Optional[int]) -> Dict[str, int]:
    """Draft-day mental ratings when the save's Original*Rating isn't loaded: current minus Madden's mean gain for
    his years pro (MADDEN_MENTAL_GAIN_BY_YEARS), never above current."""
    years = years_pro if years_pro is not None else 99
    gain = next(g for hi, g in MADDEN_MENTAL_GAIN_BY_YEARS if years <= hi)
    return {k: min(ratings[k], max(MENTAL_RATING_FLOOR, round(ratings[k] - gain)))
            for k in MENTAL_RATINGS if k in ratings}


def mental_decline(current: Dict[str, int], peak: Dict[str, int], entry: Dict[str, int], age: int) -> Dict[str, int]:
    """New mental ratings after this season's decline (D10): a share by age of the gain from draft day to the peak,
    never below draft day."""
    share = next(s for hi, s in MENTAL_DECLINE_SHARE if age <= hi)
    out = {}
    for k, x in current.items():
        p, e = max(peak.get(k, x), x), min(entry.get(k, x), x)
        out[k] = max(e, x - round(share * max(0, p - e)))
    return out


def apply_ratings_progression(
    doc: Dict[str, Any],
    position: str,
    archetype: Optional[str],
    ratings: Dict[str, int],
    age: int,
    target_ovr: int,
    injury_severity: int = 0,
    years_pro: Optional[int] = None,
    peak_mental: Optional[Dict[str, int]] = None,
    entry_mental: Optional[Dict[str, int]] = None,
    delta_ovr: Optional[int] = None,
    wear: float = 0.0,
    lasting_damage: Optional[Dict[str, int]] = None,
) -> Tuple[Dict[str, int], Dict[str, int], str, bool]:
    """Spreads the OVR change over the rating groups (§5.1, §5.4), then checks the archetype stays on top:
    1. physical: age decline (earlier for CB, RB, WR), extra Speed and Agility loss from wear (round(-wear) points,
       wear_term), the strength program in the first two seasons, injuries and lasting injury damage;
    2. mental: from 30, the peak-based decline (mental_decline);
    3. the rest of the way to target_ovr with ovr_weights.adjust_ratings on the archetype's technical skills, plus
       mental skills when he is growing; all skills only if that can't reach the target.
    With delta_ovr, the target is the ratings' own grade plus delta_ovr: the ratings (the PS5 scan) carry their own
    OVR, which wins over an older player_seasons read (PC_MIGRATION_PLAN §4.3).
    Returns (new_ratings, ratings_diff, archetype_name, is_settled)."""
    madden_pos = TO_MADDEN_POSITION.get(position, position)
    arch = madden_archetype(doc, madden_pos, archetype) if archetype else None
    if not arch or arch not in doc["archetypes"]:
        # Fall back to current top archetype from ratings
        _, arch = ow.predict_ovr(doc, madden_pos, ratings)
    if delta_ovr is not None:
        target_ovr = max(OVR_MIN, min(OVR_MAX, ow.predict_grade(doc, arch, ratings) + delta_ovr))

    updated = dict(ratings)
    original = dict(ratings)
    group = position_group(position)

    # 1. Physical: age decline, strength program, injuries
    if age >= 29:
        drop = 1 if age in (29, 30) else 2
        for pr in ("Speed", "Agility"):
            if pr in updated:
                updated[pr] = max(25, updated[pr] - (drop if group in EARLY_DECLINE_GROUPS else max(1, drop - 1)))
        if age >= 31:
            for pr in ("Acceleration", "Jumping", "ChangeOfDirection"):
                if pr in updated:
                    updated[pr] = max(25, updated[pr] - 1)
        if age >= 33:
            for pr in ("ThrowPower", "KickPower"):
                if pr in updated:
                    updated[pr] = max(25, updated[pr] - 1)
    if years_pro is not None and years_pro <= STRENGTH_PROGRAM_YEARS:
        for pr in ("Strength", "Stamina"):
            if pr in updated and updated[pr] < STRENGTH_PROGRAM_CAP:
                updated[pr] += 1

    if injury_severity > 0:
        for pr in ("Speed", "Acceleration"):
            if pr in updated:
                updated[pr] = max(25, updated[pr] - injury_severity)
    extra = int(round(max(0.0, -wear)))
    if extra:
        for pr in ("Speed", "Agility"):
            if pr in updated:
                updated[pr] = max(25, updated[pr] - extra)
    for pr, d in (lasting_damage or {}).items():
        if pr in updated:
            updated[pr] = max(25, updated[pr] + d)

    # 2. Mental: declines last, by the peak
    mental = {k: updated[k] for k in MENTAL_RATINGS if k in updated}
    entry = entry_mental or estimate_entry_mental(mental, years_pro)
    updated.update(mental_decline(mental, peak_mental or mental, entry, age))

    # 3. The rest of the way to target_ovr: growth goes first to mental skills (MENTAL_GROWTH_SHARE of it), then
    # technical and mental; decline beyond the physical and mental steps comes out of technical skills
    skills = doc["archetypes"][arch].get("skills", list(doc["archetypes"][arch]["terms"]))
    grade = ow.predict_grade(doc, arch, updated)
    growing = grade < target_ovr
    if growing:
        mental_skills = [k for k in skills if rating_group(k) == "mental"]
        step = grade + math.ceil(MENTAL_GROWTH_SHARE * (target_ovr - grade))
        if mental_skills and step > grade:
            try:
                updated, _ = ow.adjust_ratings(doc, arch, updated, step, skills=mental_skills)
            except ValueError:
                pass
    allowed = [k for k in skills if rating_group(k) == "technical" or (growing and rating_group(k) == "mental")]
    for choice in (allowed, skills):
        try:
            updated, _ = ow.adjust_ratings(doc, arch, updated, target_ovr, skills=choice)
            break
        except ValueError:
            continue

    # 4. Settle archetype so player's primary archetype remains on top
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
    cur.execute("SELECT to_regclass('public.player_ceilings') IS NOT NULL")
    has_ceilings = bool((cur.fetchone() or [False])[0])
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'player_ceilings' AND column_name = 'peak_mental'")
    has_mental = has_ceilings and cur.fetchone() is not None
    ceiling_col = "pc.ceiling as stored_ceiling" if has_ceilings else "NULL as stored_ceiling"
    mental_cols = "pc.peak_mental, pc.entry_mental" if has_mental else "NULL, NULL"
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = 'player_ceilings' AND column_name = 'group_ceilings'")
    has_band = has_ceilings and cur.fetchone() is not None
    band_cols = "pc.floor, pc.spread, pc.group_ceilings" if has_band else "NULL, NULL, NULL"
    ceiling_join = "LEFT JOIN player_ceilings pc ON pc.player_id = ps.player_id" if has_ceilings else ""

    cur.execute(f"""
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
            {ceiling_col},
            ps.years_of_experience,
            {mental_cols},
            {band_cols}
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
        {ceiling_join}
        WHERE ps.season = %s
          AND ps.overall_rating IS NOT NULL
          AND ps.position != 'DEF'
    """, (season - 1, season - 1))

    cols = (
        "player_id", "name", "position", "team_id", "age", "overall_rating",
        "dev_trait", "archetype", "games_played", "downs_played",
        "is_ir", "weeks_remaining", "prospect_true_val", "stored_ceiling", "years_pro", "peak_mental", "entry_mental",
        "stored_floor", "stored_spread", "stored_group_ceilings"
    )
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    # Starter flags based on top 22 start OVRs per team
    s_input = [{"player_id": r["player_id"], "team_id": r["team_id"], "ovr_start": r["overall_rating"]} for r in rows]
    starters = starter_flags(s_input)
    for r in rows:
        r["is_starter"] = starters.get(r["player_id"], False)

    return rows


# ---------------------------------------------------------------- performance ----
# How well a player played (JR, 2026-10-28), from Project Mu's season stats until the PC save's per-game
# GAMERATING exists. Production per game is ranked against the same position group (players with at least
# PERF_MIN_GAMES games), mapped to a score from -1 (worst) to +1 (best), plus award bonuses.
PERF_MIN_GAMES = 6
PERF_GROUPS = {
    "QB": ("QB",), "RB": ("HB", "FB"), "REC": ("WR", "TE"), "OL": ("LT", "LG", "C", "RG", "RT"),
    "FRONT": ("DT", "LEDG", "REDG", "LE", "RE", "MIKE", "WILL", "SAM", "MLB", "LOLB", "ROLB"),
    "DB": ("CB", "FS", "SS"), "K": ("K",), "P": ("P",),
}
AWARD_BONUS = {"First Team All-Pro": 1.0, "Second Team All-Pro": 0.5, "MVP": 0.5, "OPOY": 0.5, "DPOY": 0.5,
               "OROY": 0.5, "DROY": 0.5}
AWARD_BONUS_CAP = 1.5
PERF_WEIGHT = 1.0                # OVR a year for the best (+1) or worst (-1) production in the group
PERF_TERM_RANGE = (-1.0, 2.0)    # rank plus awards, clamped
# Not fitted to Madden: its 2035->2036 changes barely follow production (correlation 0.03 with the score), so
# this term is Project Mu's choice; the backtest against Madden gets slightly worse with it.


def performance_term(perf: Optional[Dict[str, Any]]) -> float:
    if not perf:
        return 0.0
    lo, hi = PERF_TERM_RANGE
    return max(lo, min(hi, PERF_WEIGHT * perf.get("score", 0.0) + perf.get("awards", 0.0)))


# Positions whose box scores say little (PROGRESSION_PLAN §5.15, JR 2026-10-28): linemen and defensive backs are
# ranked on per-down rates where downs are known, with part of the score from their unit (UNIT_SHARE), and every
# group's score is scaled by how well it repeats year to year (performance_reliability).
DOWN_RATE_GROUPS = ("OL", "FRONT", "DB")
UNIT_SHARE = {"OL": 0.5, "DB": 0.4, "FRONT": 0.2}
PERF_SHRINK_GAMES = 4            # a player's score is scaled by games / (games + this)
RELIABILITY_DOWNS_SHARE = 0.3    # a season counts for reliability when this share of its stat lines has downs


def production(group: str, r: Dict[str, Any], downs_per_game: Optional[float] = None) -> Optional[float]:
    """One season's production for a position group, per game (None: nothing to rank on). For linemen and
    defenders with downs played, counts are per down scaled to `downs_per_game` (the group's median), so players
    with and without downs rank on the same scale. Tackles by defensive backs don't count (§5.15)."""
    g = lambda k: float(r.get(k) or 0)
    games = g("games_played")
    if group == "K":
        return g("kick_fg_pct") + g("kick_fgm_50") * 2 if g("kick_fga") >= 10 else None
    if group == "P":
        return g("punt_net_average") if g("punt_punts") >= 20 else None
    if games < PERF_MIN_GAMES:
        return None
    if group == "QB":
        v = g("pass_yards") + 20 * g("pass_tds") - 45 * g("interceptions") + g("rush_yards") + 20 * g("rush_tds")
    elif group == "RB":
        v = g("rush_yards") + g("rec_yards") + 20 * (g("rush_tds") + g("rec_tds")) - 25 * g("fumb")
    elif group == "REC":
        v = g("rec_yards") + 20 * g("rec_tds") + 2 * g("receptions")
    elif group == "OL":
        v = -g("blk_sacks_allowed")
    elif group == "FRONT":
        v = (g("def_tackles") + 2 * g("def_tfl") + 5 * g("def_sacks") + 6 * g("def_ints") + 2 * g("def_deflections")
             + 4 * g("def_forced_fumbles") + 15 * g("def_tds"))
    else:  # DB
        v = 8 * g("def_ints") + 3 * g("def_deflections") + 4 * g("def_forced_fumbles") + 15 * g("def_tds")
    if group in DOWN_RATE_GROUPS and downs_per_game and g("downs_played") > 0:
        return v / g("downs_played") * downs_per_game
    return v / games


def _z(values: Dict[Any, float]) -> Dict[Any, float]:
    if len(values) < 2:
        return {k: 0.0 for k in values}
    m = sum(values.values()) / len(values)
    sd = math.sqrt(sum((v - m) ** 2 for v in values.values()) / (len(values) - 1)) or 1.0
    return {k: (v - m) / sd for k, v in values.items()}


def unit_scores(cur, season: int) -> Dict[int, Dict[str, float]]:
    """{team_id: {"OL": z, "FRONT": z, "DB": z}} for `season`, each a z-score across teams (higher is better).
    OL: sack rate allowed and yards per carry, from the team's own season stat lines (every season). FRONT:
    opponents' yards per carry; DB: opponents' completion rate, yards per attempt and passing TDs per attempt, from
    the regular-season team box scores (team_stats, 2035 on); teams without them get no defensive unit score."""
    cur.execute("""
        SELECT team_id, sum(pass_sacks), sum(pass_attempts), sum(rush_yards), sum(rush_attempts)
        FROM season_stat_lines WHERE season = %s AND team_id IS NOT NULL GROUP BY team_id
    """, (season,))
    sack, ypc = {}, {}
    for team, sacks, att, ry, ra in cur.fetchall():
        if att and ra:
            sack[team] = -float(sacks or 0) / (float(att) + float(sacks or 0))
            ypc[team] = float(ry or 0) / float(ra)
    out = defaultdict(dict)
    zs, zy = _z(sack), _z(ypc)
    for team in zs:
        out[team]["OL"] = (zs[team] + zy[team]) / 2
    cur.execute("""
        SELECT opp.id, sum(t.passing_completions), sum(t.passing_attempts), sum(t.passing_yards),
               sum(t.passing_tds), sum(t.rushing_yards), sum(t.rushing_attempts)
        FROM team_stats t
        JOIN games g ON g.id = t.game_id AND g.season = %s AND g.game_type = 'REGULAR_SEASON'
        JOIN teams tm ON tm.name = t.team_name
        JOIN teams opp ON opp.id = CASE WHEN g.home_team_id = tm.id THEN g.away_team_id ELSE g.home_team_id END
        GROUP BY opp.id
    """, (season,))
    comp, ypa, tdr, rypc = {}, {}, {}, {}
    for team, c, a, y, td, ry, ra in cur.fetchall():
        if a and ra:
            comp[team], ypa[team], tdr[team] = -float(c) / a, -float(y) / a, -float(td) / a
            rypc[team] = -float(ry) / ra
    zc, za, zt, zr = _z(comp), _z(ypa), _z(tdr), _z(rypc)
    for team in zc:
        out[team]["DB"] = (zc[team] + za[team] + zt[team]) / 3
        out[team]["FRONT"] = zr[team]
    return dict(out)


def _group_of(position: Optional[str]) -> Optional[str]:
    return next((k for k, v in PERF_GROUPS.items() if position in v), None)


def raw_performance(cur, season: int) -> Dict[int, Dict[str, Any]]:
    """{player_id: {"group", "raw" (-1..+1 rank of own production blended with the unit's score), "games"}} for
    `season`, before the reliability weight and awards."""
    cur.execute("SELECT * FROM season_stat_lines WHERE season = %s", (season,))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    dpg = defaultdict(list)
    for r in rows:
        group = _group_of(r.get("position"))
        if group in DOWN_RATE_GROUPS and (r.get("downs_played") or 0) > 0 and (r.get("games_played") or 0) >= PERF_MIN_GAMES:
            dpg[group].append(r["downs_played"] / r["games_played"])
    median = {g: sorted(v)[len(v) // 2] for g, v in dpg.items()}
    units = unit_scores(cur, season)
    by_group = defaultdict(dict)
    meta = {}
    for r in rows:
        group = _group_of(r.get("position"))
        if group:
            v = production(group, r, median.get(group))
            if v is not None:
                by_group[group][r["player_id"]] = v
                meta[r["player_id"]] = (group, r.get("team_id"), r.get("games_played") or 0)
    out = {}
    for group, vals in by_group.items():
        z = _z(vals)
        share = UNIT_SHARE.get(group, 0.0)
        blended = {}
        for pid, zv in z.items():
            unit = units.get(meta[pid][1], {}).get(group)
            blended[pid] = zv if unit is None or not share else (1 - share) * zv + share * unit
        ranked = sorted(blended.items(), key=lambda kv: kv[1])
        n = len(ranked)
        for i, (pid, b) in enumerate(ranked):
            # average rank of ties, as a percentile, to -1..+1
            lo = next(j for j in range(n) if ranked[j][1] == b)
            hi = max(j for j in range(n) if ranked[j][1] == b)
            pct = ((lo + hi) / 2) / (n - 1) if n > 1 else 0.5
            out[pid] = {"group": group, "raw": 2 * pct - 1, "games": meta[pid][2]}
    return out


_RELIABILITY_CACHE: Dict[Tuple[str, int], Dict[str, Any]] = {}


def performance_reliability(cur, upto_season: int) -> Dict[str, Any]:
    """{"weights": {group: 0..1}, "r": {group: correlation}, "n": {group: pairs}, "seasons": [...]}: how well each
    group's score predicts itself the next season, pooled over pairs of consecutive stat seasons up to
    `upto_season`, scaled so the most repeatable group gets 1 (§5.15). Only pairs where both seasons have downs
    played (RELIABILITY_DOWNS_SHARE of stat lines; 2035 on) count, since earlier seasons rank per game and without
    unit scores, which makes volume look repeatable; with no such pair, every pair counts. A group that doesn't
    repeat (r <= 0) gets 0."""
    key = (cur.connection.info.dbname, upto_season)
    if key in _RELIABILITY_CACHE:
        return _RELIABILITY_CACHE[key]
    cur.execute("""
        SELECT season, avg(CASE WHEN downs_played > 0 THEN 1.0 ELSE 0.0 END)
        FROM season_stat_lines WHERE season <= %s GROUP BY 1 ORDER BY 1
    """, (upto_season,))
    coverage = dict(cur.fetchall())
    seasons = list(coverage)
    with_downs = [s for s in seasons if coverage[s] >= RELIABILITY_DOWNS_SHARE]
    if not any(s + 1 in with_downs for s in with_downs):
        with_downs = seasons
    raw = {s: raw_performance(cur, s) for s in with_downs}
    pairs = defaultdict(list)
    for s in with_downs:
        if s + 1 in raw:
            for pid, a in raw[s].items():
                b = raw[s + 1].get(pid)
                if b and b["group"] == a["group"]:
                    pairs[a["group"]].append((a["raw"], b["raw"]))
    r = {}
    for group, xy in pairs.items():
        if len(xy) < 20:
            continue
        mx, my = sum(x for x, _ in xy) / len(xy), sum(y for _, y in xy) / len(xy)
        sxy = sum((x - mx) * (y - my) for x, y in xy)
        sx = math.sqrt(sum((x - mx) ** 2 for x, _ in xy))
        sy = math.sqrt(sum((y - my) ** 2 for _, y in xy))
        r[group] = sxy / (sx * sy) if sx and sy else 0.0
    top = max([v for v in r.values() if v > 0], default=1.0)
    out = {"weights": {g: round(max(0.0, v) / top, 3) for g, v in r.items()},
           "r": {g: round(v, 3) for g, v in r.items()}, "n": {g: len(v) for g, v in pairs.items()},
           "seasons": sorted(raw)}
    _RELIABILITY_CACHE[key] = out
    return out


def performance_scores(cur, season: int) -> Dict[int, Dict[str, Any]]:
    """{player_id: {"score": -1..+1, "awards": bonus, "notes": [...], "group"}} for `season`: the position-group
    rank of production (raw_performance), times the group's reliability weight and the player's games / (games +
    PERF_SHRINK_GAMES). Players without enough games or attempts get no score (their playing-time term still
    applies)."""
    weights = performance_reliability(cur, season)["weights"]
    out = {}
    for pid, p in raw_performance(cur, season).items():
        shrink = p["games"] / (p["games"] + PERF_SHRINK_GAMES) if p["games"] else 1.0
        out[pid] = {"score": round(p["raw"] * weights.get(p["group"], 1.0) * shrink, 3), "awards": 0.0,
                    "notes": [], "group": p["group"]}
    cur.execute("SELECT player_id, award_type FROM awards WHERE season = %s AND player_id IS NOT NULL", (season,))
    for pid, award in cur.fetchall():
        bonus = AWARD_BONUS.get(award)
        if bonus:
            e = out.setdefault(pid, {"score": 0.0, "awards": 0.0, "notes": [], "group": None})
            e["awards"] = min(AWARD_BONUS_CAP, e["awards"] + bonus)
            e["notes"].append(award)
    return out


SEASON_GAMES = 17               # regular-season games; games missed = SEASON_GAMES - games played (upkeep)


def _has_table(cur, name: str) -> bool:
    cur.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{name}",))
    return bool((cur.fetchone() or [False])[0])


def _has_column(cur, table: str, column: str) -> bool:
    cur.execute("SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = %s", (table, column))
    return cur.fetchone() is not None


def load_c2_context(cur, season: int) -> Dict[str, Any]:
    """What the C2 terms read from the DB for the offseason into `season` (last season = season - 1): career stat
    lines (mileage), the league's normal season wear per position group, last season's injuries, the coordinators
    in charge last season, each player's team last season and the one before, last season's rosters (mentors) and
    stored durability. Tables that don't exist give empty parts, so the terms fall back to 0."""
    last = season - 1
    ctx: Dict[str, Any] = {"season": season, "stats": defaultdict(dict), "normal_wear": {}, "injuries": defaultdict(list),
                           "coord_start": {}, "team_last": {}, "team_prev": {}, "roster_by_team": defaultdict(list),
                           "durability": {}}
    cur.execute("SELECT * FROM season_stat_lines WHERE season <= %s AND player_id IS NOT NULL", (last,))
    cols = [d[0] for d in cur.description]
    for row in cur.fetchall():
        r = dict(zip(cols, row))
        prev = ctx["stats"][r["player_id"]].get(r["season"])
        if prev is None:
            ctx["stats"][r["player_id"]][r["season"]] = r
        else:   # several lines for one season (team split): add counts, keep the larger games and downs
            for k, v in r.items():
                if isinstance(v, (int, float)) and not isinstance(v, bool) and k not in ("season", "player_id", "team_id"):
                    prev[k] = (max if k in ("games_played", "downs_played") else (lambda a, b: a + b))(prev.get(k) or 0, v)
    wear_by_group = defaultdict(list)
    for seasons in ctx["stats"].values():
        for line in seasons.values():
            group = _group_of(line.get("position"))
            if group and (line.get("games_played") or 0) > 0:
                wear_by_group[group].append(season_mileage(group, line)["wear"])
    ctx["normal_wear"] = {g: sum(v) / len(v) for g, v in wear_by_group.items() if v}
    if _has_table(cur, "injuries"):
        cur.execute("SELECT player_id, injury_type FROM injuries WHERE season = %s AND player_id IS NOT NULL", (last,))
        for pid, t in cur.fetchall():
            ctx["injuries"][pid].append(t)
    if _has_table(cur, "coordinator_tenures"):
        cur.execute("""
            SELECT team_id, role, max(start_season) FROM coordinator_tenures
            WHERE start_season <= %s AND COALESCE(end_season, 9999) >= %s GROUP BY 1, 2
        """, (last, last))
        ctx["coord_start"] = {(t, role): start for t, role, start in cur.fetchall()}
    cur.execute("""
        SELECT season, player_id, team_id, position, age FROM player_seasons
        WHERE season IN (%s, %s) AND position != 'DEF'
    """, (last, last - 1))
    for s, pid, team, pos, age in cur.fetchall():
        if s == last:
            ctx["team_last"][pid] = team
            if team:
                ctx["roster_by_team"][team].append({"player_id": pid, "position": pos, "age": age})
        else:
            ctx["team_prev"][pid] = team
    if _has_table(cur, "player_durability"):
        cur.execute("SELECT player_id, durability, source FROM player_durability")
        ctx["durability"] = {pid: (float(d), src) for pid, d, src in cur.fetchall()}
    return ctx


def c2_player_inputs(p: Dict[str, Any], ctx: Dict[str, Any], attrs: Optional[Dict[str, int]],
                     awareness: Dict[int, int], age: int) -> Dict[str, Any]:
    """The C2 terms for one player from load_c2_context's ctx (pure). `p` needs player_id and position; `age` is
    the age the terms are judged at."""
    pid, pos = p["player_id"], p.get("position")
    season = ctx["season"]
    group = _group_of(pos)
    # Durability: stored, else seeded from InjuryRating / ToughnessRating
    if pid in ctx["durability"]:
        durability, d_source = ctx["durability"][pid]
    else:
        attrs = attrs or {}
        durability, d_source = seed_durability(pid, attrs.get("Injury"), attrs.get("Toughness"))
    # Mileage and wear
    seasons = ctx["stats"].get(pid, {})
    mileage = {s: season_mileage(_group_of(line.get("position")) or group, line) for s, line in sorted(seasons.items())}
    played = [m for s, m in mileage.items() if (seasons[s].get("games_played") or 0) > 0]
    career = sum(m["wear"] for m in played)
    excess = mileage_excess(career, len(played), ctx["normal_wear"].get(group, DEFAULT_SEASON_WEAR))
    wear = wear_term(age, pos, excess, durability)
    # Lasting injury damage
    damage = lasting_injury_damage(pid, season, ctx["injuries"].get(pid, []), durability)
    # Continuity: last season under a new coordinator for his side, or on a new team
    team_last, team_prev = ctx["team_last"].get(pid), ctx["team_prev"].get(pid)
    role = "OC" if group in OFFENSE_GROUPS else "DC" if group in DEFENSE_GROUPS else None
    start = ctx["coord_start"].get((team_last, role)) if role else None
    new_coord = start is not None and start == season - 1
    changed = team_last is not None and team_prev is not None and team_last != team_prev
    known = start is not None or (team_last is not None and team_prev is not None)
    continuity = continuity_term(age, new_coord, changed, known)
    # Mentor: a veteran teammate in his position group
    mentor_id = None
    if team_last is not None and (age or 99) <= MENTOR_MAX_AGE:
        mentor_id = find_mentor(p, ctx["roster_by_team"].get(team_last, []), awareness)
    return {"durability": durability, "durability_source": d_source, "mileage": mileage, "career_wear": round(career, 2),
            "mileage_excess": round(excess, 3), "wear": wear, "lasting_damage": damage,
            "lasting_points": -sum(damage.values()), "continuity": continuity,
            "mentor": mentor_term(age, mentor_id is not None), "mentor_id": mentor_id,
            "last_stats": seasons.get(season - 1)}


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
DEV_LABEL_MAX = 16      # player_progression_history.dev_before / dev_after are VARCHAR(16)


def dev_label(dev_trait: Optional[str]) -> Optional[str]:
    """The dev trait as stored in dev_before / dev_after: 'Superstar X-Factor' (18 characters) as 'X-Factor'."""
    if dev_trait is None:
        return None
    if len(dev_trait) <= DEV_LABEL_MAX:
        return dev_trait
    return "X-Factor" if "X-Factor" in dev_trait else dev_trait[:DEV_LABEL_MAX]


def stored_band(p: Dict[str, Any], ceiling: float) -> Optional[Dict[str, Any]]:
    """The band stored in player_ceilings (floor, spread, group_ceilings), or None when it isn't complete."""
    gc = p.get("stored_group_ceilings")
    if isinstance(gc, str):
        gc = json.loads(gc)
    if not gc or p.get("stored_spread") is None:
        return None
    return {"floor": float(p["stored_floor"]) if p.get("stored_floor") is not None else None,
            "spread": float(p["stored_spread"]),
            "group_ceilings": {k: float(gc.get(k, ceiling)) for k in ("physical", "technical", "mental")}}


def run_progression(
    conn,
    season: int,
    commit: bool = False,
    dry_run: bool = False,
    hold_shape: bool = True,
    log=print,
    traits: Optional[Dict[int, Dict[str, bool]]] = None,
) -> Dict[str, Any]:
    """Runs progression for all players into target season, storing results in
    player_progression_history and updating player_ceilings. With hold_shape (default) the development and star
    terms are calibrated first so the league after the coming draft keeps the LEAGUE_SHAPE_SEASON shape: the draft
    class is projected from that season's real class, sized to refill the league (calibrate_league_term).
    Phase C2 (§5.8-§5.13): durability (player_durability), mileage and wear (player_mileage), lasting injury damage,
    ceiling bands and breakthroughs (player_ceilings floor / spread / group_ceilings), upkeep above 90 inside the
    league term, scheme continuity, mentors and PT_* behaviour changes (stored, not written to the save) are part
    of each row. `traits` ({player_id: {PT_*: bool}}, e.g. from the save snapshot) lets the behaviour model drop
    flags a player has; without it only gains are possible. Persona and coach terms are 0 (D4)."""
    with conn.cursor() as cur:
        _, aging_pairs, _ = read_fit_rows(cur, season - 1 if season > 2036 else 2036)
        aging_curves = fit_aging(aging_pairs)
        roster = fetch_progression_roster(cur, season)
        weights_doc = ow.load_weights()
        attr_map = fetch_player_attributes_map(cur, season)
        awareness = {pid: r.get("Awareness") for pid, r in attr_map.items() if r}
        ctx = load_c2_context(cur, season)

        log(f"Running progression for Season {season}: {len(roster)} eligible players found")
        results = []
        counts_by_cause = defaultdict(int)
        deltas = []

        perf = performance_scores(cur, season - 1)
        try:
            perf_prev = performance_scores(cur, season - 2)
        except Exception:
            perf_prev = {}
        top_last, top_prev = top_quarter(perf), top_quarter(perf_prev)

        def delta_inputs(p):
            age = p["age"] + 1  # Player advances 1 year in offseason
            # Seed or fetch ceiling
            if p["stored_ceiling"] is not None:
                ceiling, c_source = float(p["stored_ceiling"]), "STORED"
            else:
                ceiling, c_source = seed_player_ceiling(
                    p["player_id"], p["overall_rating"], age, p["dev_trait"], p["prospect_true_val"])
            band = stored_band(p, ceiling) or seed_ceiling_band(
                p["player_id"], p["overall_rating"], p["dev_trait"], ceiling, p["years_pro"])
            c2 = c2_player_inputs(p, ctx, attr_map.get(p["player_id"]), awareness, age)
            bt = breakthrough(p["player_id"], season, age, p["player_id"] in top_last, p["player_id"] in top_prev)
            band, ceiling = apply_breakthrough(band, ceiling, bt)
            if c2["lasting_points"]:
                gc = dict(band["group_ceilings"])
                gc["physical"] = max(float(OVR_MIN), gc["physical"] - c2["lasting_points"])
                band = {**band, "group_ceilings": gc}
            pf = perf.get(p["player_id"]) or {}
            missed = max(0, SEASON_GAMES - p["games_played"]) if p["games_played"] is not None else 0
            upkeep = upkeep_term(p["overall_rating"], pf.get("score"), missed, c2["mileage_excess"]) if hold_shape else 0.0
            c2.update(band=band, breakthrough_points=bt, upkeep=upkeep)
            return ceiling, c_source, c2, dict(
                player_id=p["player_id"], position=p["position"], age=age, ovr=p["overall_rating"],
                dev_trait=p["dev_trait"], ceiling=ceiling, games_played=p["games_played"],
                downs_played=p["downs_played"], is_starter=p["is_starter"], is_ir=p["is_ir"],
                injury_weeks=p["weeks_remaining"], season=season, aging_curves=aging_curves,
                performance=performance_term(pf or None), spread=band["spread"],
                technical_ceiling=band["group_ceilings"]["technical"], wear=c2["wear"],
                lasting_damage=c2["lasting_points"], upkeep=upkeep, continuity=c2["continuity"],
                mentor=c2["mentor"], breakthrough=bool(bt))

        prepared = [(p, *delta_inputs(p)) for p in roster]
        terms = (0.0, 0.0)
        if hold_shape and prepared:
            target = league_shape_target(cur)
            class_size = max(0, target["players"] - len(prepared))
            rookies = rookie_class(league_turnover(cur, LEAGUE_SHAPE_SEASON), class_size,
                                   random.Random(f"draft:{season}"))
            terms, projected = calibrate_league_term(
                prepared, [r["ovr"] for r in rookies], target,
                lambda item, dev, star: compute_player_delta(**item[4], league_adj=dev, star_adj=star)[1])
            log(f"League shape: holding {target['season']} (90+={target['count_90_plus']}, "
                f"mean {target['avg_ovr']:.2f}) with development term {terms[0]:+.1f} and star term "
                f"{terms[1]:+.2f}; projected after a {class_size}-player draft: 90+={projected['count_90_plus']}, "
                f"mean {projected['avg_ovr']:.2f}")

        for p, ceiling, c_source, c2, kwargs in prepared:
            p_id = p["player_id"]
            ovr = p["overall_rating"]
            age = kwargs["age"]
            delta, new_ovr, breakdown, cause = compute_player_delta(
                **kwargs, league_adj=terms[0], star_adj=terms[1])

            ratings_diff, peak, entry, entry_source = {}, None, None, None
            if p_id in attr_map:
                current = attr_map[p_id]
                mental = {k: current[k] for k in MENTAL_RATINGS if k in current}
                if p["entry_mental"]:
                    entry, entry_source = p["entry_mental"], "STORED"
                else:
                    entry, entry_source = estimate_entry_mental(mental, p["years_pro"]), "ESTIMATED"
                peak = {k: max(v, (p["peak_mental"] or {}).get(k, v)) for k, v in mental.items()}
                try:
                    new_ratings, ratings_diff, _, _ = apply_ratings_progression(
                        doc=weights_doc,
                        position=p["position"],
                        archetype=p["archetype"],
                        ratings=current,
                        age=age,
                        target_ovr=new_ovr,
                        delta_ovr=delta,
                        injury_severity=1 if p["is_ir"] else 0,
                        years_pro=p["years_pro"],
                        peak_mental=peak,
                        entry_mental=entry,
                        wear=c2["wear"],
                        lasting_damage=c2["lasting_damage"],
                    )
                    peak = {k: max(v, new_ratings.get(k, v)) for k, v in peak.items()}
                except Exception:
                    ratings_diff = {}

            behaviour = behaviour_changes(p_id, season, p["position"], p["years_pro"], c2["last_stats"],
                                          (traits or {}).get(p_id), c2["mentor_id"] is not None)
            band = c2["band"]
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
                "league_term": breakdown["league"],
                "performance_term": breakdown["performance"],
                "performance_notes": (perf.get(p_id) or {}).get("notes", []),
                "ratings_diff": ratings_diff,
                "ceiling": ceiling,
                "ceiling_source": c_source,
                "cause": cause,
                "peak_mental": peak,
                "entry_mental": entry,
                "entry_source": entry_source,
                # phase C2 (§5.6)
                "checkpoint": "OFFSEASON",
                "dev_before": dev_label(p["dev_trait"]),
                "dev_after": dev_label(p["dev_trait"]),   # dev changes are phase H (§5.7)
                "group_changes": group_changes(ratings_diff),
                "wear_term": breakdown["wear"],
                "upkeep_term": breakdown["upkeep"],
                "continuity_term": breakdown["continuity"],
                "mentor_term": breakdown["mentor"],
                "persona_term": PERSONA_TERM,
                "coach_term": COACH_TERM,
                "behaviour_changes": behaviour,
                "floor": band["floor"],
                "spread": band["spread"],
                "group_ceilings": band["group_ceilings"],
                "breakthrough": c2["breakthrough_points"],
                "durability": c2["durability"],
                "durability_source": c2["durability_source"],
                "mileage": c2["mileage"],
                "career_wear": c2["career_wear"],
                "lasting_damage": c2["lasting_damage"],
                "mentor_id": c2["mentor_id"],
            })
            counts_by_cause[cause] += 1
            deltas.append(delta)

        avg_delta = sum(deltas) / len(deltas) if deltas else 0.0
        log(f"Progression calculated: mean ΔOVR={avg_delta:+.2f}, min={min(deltas, default=0)}, max={max(deltas, default=0)}")
        log("Causes breakdown: " + ", ".join(f"{k}: {v}" for k, v in sorted(counts_by_cause.items())))

        if commit and not dry_run:
            log(f"Writing {len(results)} rows to player_progression_history and updating player_ceilings...")
            store_progression(cur, results, season)
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


C2_HISTORY_COLUMNS = ("checkpoint", "dev_before", "dev_after", "group_changes", "wear_term", "upkeep_term",
                      "continuity_term", "mentor_term", "persona_term", "coach_term", "behaviour_changes")
_JSONB_COLUMNS = {"ratings_diff", "group_changes", "behaviour_changes"}


def history_rows(results: List[Dict[str, Any]], with_c2: bool = True) -> Tuple[Tuple[str, ...], List[tuple]]:
    """(columns, value tuples) for player_progression_history, JSONB columns as JSON text."""
    base = ("season", "player_id", "old_ovr", "new_ovr", "delta_ovr", "age_term", "dev_term", "ceiling_term",
            "play_term", "injury_term", "noise_term", "league_term", "performance_term", "ratings_diff", "cause")
    cols = base + (C2_HISTORY_COLUMNS if with_c2 else ())
    return cols, [tuple(json.dumps(r[c]) if c in _JSONB_COLUMNS and r[c] is not None else r[c] for c in cols)
                  for r in results]


def store_progression(cur, results: List[Dict[str, Any]], season: int) -> None:
    """Writes run_progression's rows: player_progression_history (with the C2 columns when the 2027_01_14
    migration is applied), player_ceilings (with the band), player_durability and player_mileage."""
    has_c2 = _has_column(cur, "player_progression_history", "wear_term")
    has_band = _has_column(cur, "player_ceilings", "group_ceilings")
    has_dur, has_mil = _has_table(cur, "player_durability"), _has_table(cur, "player_mileage")
    cols, rows = history_rows(results, has_c2)
    sql = (f"INSERT INTO player_progression_history ({', '.join(cols)}, created_at) "
           f"VALUES ({', '.join(['%s'] * len(cols))}, NOW()) ON CONFLICT (season, player_id) DO UPDATE SET "
           + ", ".join(f"{c} = EXCLUDED.{c}" for c in cols[2:]) + ", created_at = NOW()")
    cur.executemany(sql, rows)
    band_cols = ", floor, spread, group_ceilings" if has_band else ""
    band_ph = ", %s, %s, %s" if has_band else ""
    band_set = (", floor = EXCLUDED.floor, spread = EXCLUDED.spread, group_ceilings = EXCLUDED.group_ceilings"
                if has_band else "")
    for r in results:
        band_vals = (r["floor"], r["spread"], json.dumps(r["group_ceilings"])) if has_band else ()
        cur.execute(f"""
            INSERT INTO player_ceilings (player_id, ceiling, source, updated_season, peak_mental, entry_mental,
                                         entry_source{band_cols}, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s{band_ph}, NOW())
            ON CONFLICT (player_id) DO UPDATE SET
                ceiling = EXCLUDED.ceiling,
                source = EXCLUDED.source,
                updated_season = EXCLUDED.updated_season,
                peak_mental = COALESCE(EXCLUDED.peak_mental, player_ceilings.peak_mental),
                entry_mental = COALESCE(player_ceilings.entry_mental, EXCLUDED.entry_mental),
                entry_source = COALESCE(player_ceilings.entry_source, EXCLUDED.entry_source){band_set},
                created_at = NOW();
        """, (r["player_id"], r["ceiling"], r["ceiling_source"], season,
              json.dumps(r["peak_mental"]) if r["peak_mental"] else None,
              json.dumps(r["entry_mental"]) if r["entry_mental"] else None, r["entry_source"], *band_vals))
    if has_dur:
        cur.executemany("""
            INSERT INTO player_durability (player_id, durability, source, updated_season) VALUES (%s, %s, %s, %s)
            ON CONFLICT (player_id) DO UPDATE SET durability = EXCLUDED.durability, source = EXCLUDED.source,
                updated_season = EXCLUDED.updated_season
        """, [(r["player_id"], r["durability"], r["durability_source"], season) for r in results])
    if has_mil:
        cur.executemany("""
            INSERT INTO player_mileage (player_id, season, touches, hits, snaps, wear) VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (player_id, season) DO UPDATE SET touches = EXCLUDED.touches, hits = EXCLUDED.hits,
                snaps = EXCLUDED.snaps, wear = EXCLUDED.wear
        """, [(r["player_id"], s, m["touches"], m["hits"], m["snaps"], m["wear"])
              for r in results for s, m in (r["mileage"] or {}).items()])


# ---------------------------------------------------------------- backtest ----
def run_backtest(conn, season: int = 2036, with_performance: bool = False, log=print) -> Dict[str, Any]:
    """Evaluates predicted vs actual ΔOVR on the 2035->2036 offseason.
    Reports error by age band, OVR band, and position group, and compares against dev_quality.
    The Madden calibration (AGE_BAND_OFFSET) is fitted without the performance term, which Madden doesn't follow;
    with_performance shows what that term costs against Madden."""
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
                COALESCE(inj.weeks_remaining, 0) as weeks_remaining,
                ps35.team_id,
                ps35.years_of_experience
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
        perf = performance_scores(cur, season - 1) if with_performance else {}
        # C2 terms with data behind them (wear, lasting damage, continuity, mentors); breakthroughs follow
        # production, so only with_performance; upkeep is part of the league term, which the backtest leaves out
        ctx = load_c2_context(cur, season)
        attr_map = fetch_player_attributes_map(cur, season)
        perf_prev = performance_scores(cur, season - 2) if with_performance else {}
        # dev_quality comparison fit
        dev_windows = fetch_window(cur, season)

    log(f"--- Progression Backtest ({season - 1} -> {season}) ---")
    log(f"Sample size: {len(rows)} players")

    beta, sds = fit_expected(dev_windows)

    preds, actuals, dev_exps = [], [], []
    by_age_band = defaultdict(list)
    by_ovr_band = defaultdict(list)
    by_pos_group = defaultdict(list)

    awareness = {pid: a.get("Awareness") for pid, a in attr_map.items() if a}
    top_last, top_prev = top_quarter(perf), top_quarter(perf_prev)
    for r in rows:
        p_id, pos, age, o35, o36, dev, gp, downs, is_ir, inj_w, _team, yrs = r
        ceiling, _ = seed_player_ceiling(p_id, o35, age, dev)
        c2 = c2_player_inputs({"player_id": p_id, "position": pos}, ctx, attr_map.get(p_id), awareness, age)
        bt = breakthrough(p_id, season, age, p_id in top_last, p_id in top_prev)
        ceiling = min(float(OVR_MAX), ceiling + bt)
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
            aging_curves=aging_curves,
            performance=performance_term(perf.get(p_id)),
            wear=c2["wear"],
            lasting_damage=c2["lasting_points"],
            continuity=c2["continuity"],
            mentor=c2["mentor"],
            breakthrough=bool(bt),
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
def league_shape_target(cur, season: int = LEAGUE_SHAPE_SEASON) -> Dict[str, Any]:
    """The shape the league keeps (JR, 2026-10-28): size, 90+ count and mean OVR of `season`'s rosters."""
    cur.execute("""
        SELECT count(*), count(*) FILTER (WHERE overall_rating >= 90), avg(overall_rating)
        FROM player_seasons WHERE season = %s AND overall_rating IS NOT NULL AND position != 'DEF'
    """, (season,))
    n, n90, mean = cur.fetchone()
    return {"season": season, "players": n, "count_90_plus": n90, "avg_ovr": float(mean or 0)}


def calibrate_league_term(players: List[Dict[str, Any]], rookie_ovrs: List[int], target: Dict[str, Any],
                          progress) -> Tuple[Tuple[float, float], Dict[str, Any]]:
    """The development and star terms (OVR a year) that bring the projected league (every player in `players`
    progressed by progress(p, dev, star) -> new OVR, plus the coming rookie class) closest to `target`'s 90+ count
    and mean OVR. Errors are scaled by what counts as close (5% of the 90+ count, a quarter point of mean); ties go
    to the smaller terms. Returns ((dev, star), projected shape)."""
    best = None
    for dev in LEAGUE_TERM_STEPS:
        for star in STAR_TERM_STEPS:
            ovrs = [progress(p, dev, star) for p in players] + list(rookie_ovrs)
            n90 = sum(1 for o in ovrs if o >= 90)
            mean = sum(ovrs) / len(ovrs)
            err = (abs(n90 - target["count_90_plus"]) / max(target["count_90_plus"] * 0.05, 1)
                   + abs(mean - target["avg_ovr"]) / 0.25)
            key = (round(err, 9), abs(dev) + abs(star))
            if best is None or key < best[0]:
                best = (key, (dev, star), {"count_90_plus": n90, "avg_ovr": round(mean, 2)})
    return best[1], best[2]


def league_turnover(cur, season: int) -> Dict[str, Any]:
    """What Madden itself did between season - 1 and season (PRE_PC_WORK.md R6): the rookie class (OVR, position,
    age), the dev traits of second-year players (rookies show 'Hidden'), and the share of last season's players
    gone by age band and OVR band. 'Gone' is everyone not on a roster this season (retired, cut, unsigned), which is
    what the simulation replaces with rookies."""
    cur.execute("""
        SELECT overall_rating, position, age FROM player_seasons
        WHERE season = %s AND years_of_experience = 0 AND overall_rating IS NOT NULL AND position != 'DEF'
        ORDER BY overall_rating DESC
    """, (season,))
    rookies = [{"ovr": r[0], "position": r[1], "age": r[2] or 22} for r in cur.fetchall()]
    cur.execute("""
        SELECT COALESCE(dev_trait, 'Normal'), count(*) FROM player_seasons
        WHERE season = %s AND years_of_experience = 1 AND position != 'DEF' AND dev_trait IS DISTINCT FROM 'Hidden'
        GROUP BY 1
    """, (season,))
    devs = dict(cur.fetchall()) or {"Normal": 1}
    cur.execute("""
        SELECT a.age, a.overall_rating,
               NOT EXISTS (SELECT 1 FROM player_seasons b WHERE b.season = %s AND b.player_id = a.player_id)
        FROM player_seasons a
        WHERE a.season = %s AND a.position != 'DEF' AND a.overall_rating IS NOT NULL
    """, (season, season - 1))
    counts = defaultdict(lambda: [0, 0])
    for age, ovr, gone in cur.fetchall():
        k = (turnover_band(age), ovr >= 85)
        counts[k][0] += int(gone)
        counts[k][1] += 1
    return {"season": season, "rookies": rookies, "devs": devs,
            "gone": {k: (g, n) for k, (g, n) in counts.items()}}


TURNOVER_BANDS = ((36, "36+"), (33, "33-35"), (30, "30-32"), (27, "27-29"), (0, "<27"))
MIN_TURNOVER_SAMPLE = 20      # a band with fewer players borrows the rate of the whole age band
OLDEST_GONE = 0.9             # 36+ has almost no players left in the data; assume most leave


def turnover_band(age: int) -> str:
    return next(name for lo, name in TURNOVER_BANDS if (age or 0) >= lo)


def gone_rate(turnover: Dict[str, Any], age: int, ovr: int) -> float:
    band, top = turnover_band(age), ovr >= 85
    g, n = turnover["gone"].get((band, top), (0, 0))
    if n >= MIN_TURNOVER_SAMPLE:
        return g / n
    g2 = sum(v[0] for (b, _), v in turnover["gone"].items() if b == band)
    n2 = sum(v[1] for (b, _), v in turnover["gone"].items() if b == band)
    if n2 >= MIN_TURNOVER_SAMPLE:
        return g2 / n2
    return OLDEST_GONE if band == "36+" else 0.5


def rookie_class(turnover: Dict[str, Any], size: int, rng: random.Random) -> List[Dict[str, Any]]:
    """`size` rookies shaped like the real class: pick i takes the real class's OVR at the same quantile (±1),
    with the real class's positions and ages and second-year dev traits."""
    real = turnover["rookies"]
    devs, weights = zip(*sorted(turnover["devs"].items()))
    out = []
    for i in range(size):
        src = real[min(len(real) - 1, int(i * len(real) / max(size, 1)))]
        out.append({"ovr": max(OVR_MIN, min(OVR_MAX, src["ovr"] + rng.choice((-1, 0, 0, 1)))),
                    "position": rng.choice(real)["position"], "age": rng.choice(real)["age"],
                    "dev": rng.choices(devs, weights=weights)[0]})
    return out


SIM_ALL_PRO_PER_SEASON = 62      # First + Second Team, as in the real awards table


def simulated_performance(roster: List[Dict[str, Any]], season: int,
                          weights: Optional[Dict[str, float]] = None) -> Dict[int, Dict[str, Any]]:
    """A season's performance for the simulation, which has no stats: production ranked within the position group
    on OVR plus seeded noise (better players usually, not always, produce more), scaled by the group's reliability
    weight as real scores are (performance_reliability), and the 62 best scores, spread over the groups by size,
    get All-Pro bonuses (the first half First Team)."""
    weights = weights or {}
    rng = random.Random(f"perf:{season}")
    by_group = defaultdict(list)
    for p in roster:
        group = next((k for k, v in PERF_GROUPS.items() if p["position"] in v), None)
        if group:
            by_group[group].append((p["ovr"] + rng.gauss(0.0, 4.0), p["player_id"]))
    out = {}
    for group, vals in by_group.items():
        vals.sort()
        n = len(vals)
        for i, (_, pid) in enumerate(vals):
            out[pid] = {"score": round((2 * (i / (n - 1) if n > 1 else 0.5) - 1) * weights.get(group, 1.0), 3),
                        "awards": 0.0, "notes": []}
        k = round(SIM_ALL_PRO_PER_SEASON * n / max(len(roster), 1))
        for j, (_, pid) in enumerate(reversed(vals[-k:] if k else [])):
            out[pid]["awards"] = AWARD_BONUS["First Team All-Pro"] if j < (k + 1) // 2 else AWARD_BONUS["Second Team All-Pro"]
    return out


def league_shape(roster: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(roster)
    rows = [{"player_id": p["player_id"], "team_id": p["team_id"], "ovr_start": p["ovr"]} for p in roster]
    st = starter_flags(rows)
    starters = [p for p in roster if st.get(p["player_id"])]
    by_pos = defaultdict(list)
    for p in starters:
        by_pos[p["position"]].append(p["ovr"])
    return {"players_count": n,
            "count_90_plus": sum(1 for p in roster if p["ovr"] >= 90),
            "avg_ovr": round(sum(p["ovr"] for p in roster) / n, 2),
            "avg_age": round(sum(p["age"] for p in roster) / n, 2),
            "avg_starter_ovr": round(sum(p["ovr"] for p in starters) / len(starters), 2) if starters else 0.0,
            "starter_ovr_by_pos": {k: round(sum(v) / len(v), 1) for k, v in sorted(by_pos.items())}}


def run_simulation(
    conn,
    start_season: int = 2036,
    num_seasons: int = 5,
    hold_shape: bool = True,
    log=print
) -> Dict[str, Any]:
    """Simulates N seasons ahead on a copy of the league to check its shape stays stable (§5.5).

    Each season players leave at the rates Madden's own league showed between start_season - 1 and start_season
    (league_turnover), everyone else ages a year and gets compute_player_delta, and the league is refilled to its
    size with a rookie class shaped like Madden's start_season class (rookie_class). With hold_shape (the default,
    JR 2026-10-28) the league term is calibrated each season so the league after the draft keeps start_season's
    90+ count and mean OVR (calibrate_league_term); without it the league follows Madden's own turnover.

    is_stable: every simulated season within STABILITY_BOUNDS_2036 of the start season."""
    with conn.cursor() as cur:
        _, aging_pairs, _ = read_fit_rows(cur, start_season)
        aging_curves = fit_aging(aging_pairs)
        turnover = league_turnover(cur, start_season)
        cur.execute("""
            SELECT player_id, team_id, position, age, overall_rating, dev_trait
            FROM player_seasons
            WHERE season = %s AND overall_rating IS NOT NULL AND position != 'DEF'
        """, (start_season,))
        roster = [{"player_id": r[0], "team_id": r[1], "position": r[2], "age": r[3], "ovr": r[4],
                   "dev": r[5] or "Normal"} for r in cur.fetchall()]
        weights = performance_reliability(cur, start_season)["weights"]

    log(f"--- League Stability Simulation ({num_seasons} seasons from {start_season}, "
        f"{'holding the ' + str(start_season) + ' shape' if hold_shape else 'following Madden'}) ---")
    base = league_shape(roster)
    size = base["players_count"]
    log(f"Baseline {start_season}: Players={size}, 90+={base['count_90_plus']}, Avg OVR={base['avg_ovr']:.2f}, "
        f"Avg Age={base['avg_age']:.2f}, Avg Starter OVR={base['avg_starter_ovr']:.2f}")
    teams = sorted({p["team_id"] for p in roster if p["team_id"]})

    target = {"count_90_plus": base["count_90_plus"], "avg_ovr": base["avg_ovr"]}
    history = []
    for yr in range(1, num_seasons + 1):
        sim_s = start_season + yr
        leave_rng = random.Random(f"retire:{sim_s}")
        staying = [p for p in roster if leave_rng.random() >= gone_rate(turnover, p["age"], p["ovr"])]
        rng = random.Random(f"draft:{sim_s}")
        rookies = rookie_class(turnover, size - len(staying), rng)
        perf = simulated_performance(roster, sim_s - 1, weights)   # last season, played by this roster

        def progress(p, dev, star=0.0, sim_s=sim_s):
            ceiling, _ = seed_player_ceiling(p["player_id"], p["ovr"], p["age"] + 1, p["dev"])
            upkeep = upkeep_term(p["ovr"], (perf.get(p["player_id"]) or {}).get("score")) if hold_shape else 0.0
            return compute_player_delta(
                player_id=p["player_id"], position=p["position"], age=p["age"] + 1, ovr=p["ovr"], dev_trait=p["dev"],
                ceiling=ceiling, games_played=16, downs_played=None, is_starter=True, is_ir=False,
                injury_weeks=0, season=sim_s, aging_curves=aging_curves, league_adj=dev, star_adj=star,
                performance=performance_term(perf.get(p["player_id"])), upkeep=upkeep)[1]

        terms = (calibrate_league_term(staying, [r["ovr"] for r in rookies], target, progress)[0]
                 if hold_shape else (0.0, 0.0))
        roster = [{**p, "age": p["age"] + 1, "ovr": progress(p, *terms)} for p in staying]
        roster += [{"player_id": 100000 + sim_s * 1000 + i, "team_id": rng.choice(teams), **r}
                   for i, r in enumerate(rookies)]

        shape = {"season": sim_s, **league_shape(roster), "league_term": terms[0], "star_term": terms[1]}
        history.append(shape)
        log(f"Season {sim_s}: 90+={shape['count_90_plus']:3} (Δ={shape['count_90_plus'] - base['count_90_plus']:+3}), "
            f"Avg OVR={shape['avg_ovr']:.2f}, Avg Age={shape['avg_age']:.2f}, "
            f"Avg Starter={shape['avg_starter_ovr']:.2f}, terms dev {terms[0]:+.1f} star {terms[1]:+.2f}")

    b = STABILITY_BOUNDS_2036
    lo90 = round(base["count_90_plus"] * (1 - b["count_90_plus_pct"]))
    hi90 = round(base["count_90_plus"] * (1 + b["count_90_plus_pct"]))
    is_stable = all(
        lo90 <= s["count_90_plus"] <= hi90
        and abs(s["avg_ovr"] - base["avg_ovr"]) <= b["avg_ovr_delta"]
        and abs(s["avg_starter_ovr"] - base["avg_starter_ovr"]) <= b["avg_starter_ovr_delta"]
        and b["avg_age_min"] <= s["avg_age"] <= b["avg_age_max"]
        for s in history)
    log(f"\nStability check (each season within {b} of {start_season}): "
        f"{'PASSED' if is_stable else 'DRIFT'}")
    return {"start_season": start_season, "num_seasons": num_seasons, "hold_shape": hold_shape,
            "baseline": {"players": size, **{k: base[k] for k in ("count_90_plus", "avg_ovr", "avg_age",
                                                                   "avg_starter_ovr")}},
            "turnover": {"rookies": len(turnover["rookies"]),
                         "gone": {f"{k[0]}{' 85+' if k[1] else ''}": round(g / n, 3)
                                  for k, (g, n) in sorted(turnover["gone"].items()) if n}},
            "history": history, "is_stable": is_stable}


# ---------------------------------------------------------------- CLI ----
def main():
    parser = argparse.ArgumentParser(description="Player Progression Engine (offline)")
    parser.add_argument("--season", type=int, default=2037, help="Target season (default 2037)")
    parser.add_argument("--commit", action="store_true", help="Commit results to player_progression_history")
    parser.add_argument("--dry-run", action="store_true", help="Dry run without database commit")
    parser.add_argument("--backtest", action="store_true", help="Evaluate 2035->2036 actual vs predicted progression")
    parser.add_argument("--simulate", type=int, metavar="N", help="Simulate N seasons ahead to verify stability")
    parser.add_argument("--with-performance", action="store_true",
                        help="With --backtest: include the performance term (Madden doesn't follow it)")
    parser.add_argument("--follow-madden", action="store_true",
                        help="No league term: let the league follow Madden's turnover instead of holding its 2036 shape")
    parser.add_argument("--dsn", default=DSN, help="Database DSN")

    args = parser.parse_args()

    with psycopg.connect(args.dsn) as conn:
        if args.backtest:
            run_backtest(conn, season=args.season if args.season <= 2036 else 2036,
                         with_performance=args.with_performance)
        elif args.simulate:
            run_simulation(conn, start_season=args.season if args.season <= 2036 else 2036, num_seasons=args.simulate,
                           hold_shape=not args.follow_madden)
        else:
            run_progression(conn, season=args.season, commit=args.commit, dry_run=args.dry_run,
                            hold_shape=not args.follow_madden)


if __name__ == "__main__":
    main()
